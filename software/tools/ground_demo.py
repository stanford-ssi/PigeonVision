#!/usr/bin/env python3
"""Remux the saved synthetic fisheyes with production-shaped demo telemetry.

Run from any directory: python software/tools/ground_demo.py
Requires PyAV, NumPy and SciPy. Outputs stay under build/ground-station/demo.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from contextlib import ExitStack
from fractions import Fraction
import hashlib
import heapq
import json
import math
import os
from pathlib import Path
import sys
import tempfile

import av
import numpy as np
from scipy import __version__ as SCIPY_VERSION
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "software/python"))
from pigeonvision.ground.projection import validate_calibration
from pigeonvision.ground.transport import pts_microseconds

CLOCK_ORIGIN_NS = 1_000_000_000
TRANSPORT_OFFSET_US = 1_000_000
TIME_BASE = Fraction(1, 90000)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_path(path: Path) -> str:
    return Path(os.path.relpath(path, ROOT)).as_posix()


def clip_info(path: Path) -> dict:
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        if len(container.streams.video) != 1 or stream.codec_context.name != "h264":
            raise ValueError(f"Expected one H.264 stream: {path}")
        frames = []
        for packet in container.demux(stream):
            if not packet.size:
                continue
            if packet.pts is None or packet.dts is None or packet.pts != packet.dts:
                raise ValueError("Demo inputs require timestamped H.264 without B frames")
            frames.append({"pts_us": pts_microseconds(packet.pts, packet.time_base),
                           "duration_us": pts_microseconds(packet.duration, packet.time_base)})
        if not frames or frames[0]["pts_us"] != 0:
            raise ValueError("The saved demo clips must share the trajectory's zero origin")
        return {"width": stream.width, "height": stream.height, "frames": frames,
                "duration_s": (frames[-1]["pts_us"] + frames[-1]["duration_us"]) / 1e6}


def fit_calibration(source_manifest: dict) -> dict:
    camera = source_manifest["parameters"]["capture"]["camera"]
    focal, b, pitch = (camera[key] for key in ("efl_mm", "distortion_b", "pixel_pitch_mm"))
    half_field = math.radians(camera["field_deg"] / 2)
    theta = np.linspace(0, half_field, 1001)
    target = focal * np.sin(b * theta) / b / pitch

    def radius(parameters, angles):
        scale, xi, k1, k2 = parameters
        u = np.sin(angles) / (np.cos(angles) + xi)
        return scale * u * (1 + k1 * u**2 + k2 * u**4)

    fit = least_squares(lambda parameters: radius(parameters, theta) - target,
                        [1000, 1.2, .01, .001],
                        bounds=([1, .8, -10, -10], [10000, 2.5, 10, 10]),
                        xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=10000)
    if not fit.success:
        raise ValueError(f"Synthetic Mei fit failed: {fit.message}")
    scale, xi, k1, k2 = map(float, fit.x)
    held_out = np.linspace(0, half_field, 10002)[1::2]
    errors = radius(fit.x, held_out) - focal * np.sin(b * held_out) / b / pitch
    if float(np.max(np.abs(errors))) > 1:
        raise ValueError("Synthetic radial fit exceeds the one-pixel fixture tolerance")
    width, height, crop = camera["sensor_width"], camera["sensor_height"], camera["crop_px"]
    models = {}
    for name, rotation in (("A", np.eye(3)), ("B", np.diag([-1, 1, -1]))):
        models[name] = {"image_size": [width, height],
            "K": [[scale, 0, (width - 1) / 2], [0, scale, (height - 1) / 2], [0, 0, 1]],
            "D": [k1, k2, 0, 0], "xi": xi,
            "R_camera_from_rig": rotation.astype(int).tolist(),
            "crop": [(width - crop) // 2, (height - crop) // 2, crop, crop],
            "output_size": [crop, crop], "flip_x": False, "flip_y": False,
            "valid_radius_px": camera["image_circle_mm"] / (2 * pitch),
            "max_theta_deg": camera["field_deg"] / 2,
            "provenance": {"device_id": f"synthetic-camera-{name}", "simulated": True,
                           "method": "numerical_fit_to_simulator_radial_function"}}
    bundle = {"schema_version": 1, "model": "mei", "simulated": True,
        "calibration_kind": "synthetic_model_approximation_not_measured",
        "rig_axes": "+Z through A; +X image-right for A; +Y image-down",
        "orientation_basis": "CAPTURE v.y is body-up. Canvas PNG export is top-left; encoded image-down is -body-Z. B reverses its optical and image-horizontal axes.",
        "fit": {"source_model": "r_px = f_mm * sin(b * theta_rad) / b / pixel_pitch_mm",
                "f_mm": focal, "b": b, "pixel_pitch_mm": pitch,
                "fit_samples": len(theta), "validation_samples": len(held_out),
                "theta_range_deg": [0, camera["field_deg"] / 2],
                "radial_rms_error_px": float(np.sqrt(np.mean(errors**2))),
                "radial_max_error_px": float(np.max(np.abs(errors))),
                "scope": "Radial model discrepancy only; excludes physical optics, codec loss, parallax and image resampling."},
        "cameras": models}
    return validate_calibration(bundle)


def trajectory_height(trajectory: list[dict], times: list[float], seconds: float) -> float:
    if not times[0] <= seconds <= times[-1]:
        raise ValueError("Cannot extrapolate the saved OpenRocket trajectory")
    index = min(len(times) - 2, max(0, bisect_right(times, seconds) - 1))
    a, b = trajectory[index], trajectory[index + 1]
    weight = (seconds - a["t_s"]) / (b["t_s"] - a["t_s"])
    return a["altitude_m"] + weight * (b["altitude_m"] - a["altitude_m"])


def telemetry_records(source_manifest: dict, flight: dict, clip: dict, session_id: str,
                      duration_s: float, attitudes: list[dict] | None = None) -> list[dict]:
    end_us = round(duration_s * 1e6)
    origin_us = CLOCK_ORIGIN_NS // 1000
    trajectory = flight["trajectory"]
    times = [row["t_s"] for row in trajectory]
    summary = flight["summary"]
    changes = sorted([(round(summary[key] * 1e6), event) for key, event in (
        ("ignition_time_s", "LAUNCH"), ("apogee_time_s", "APOGEE"),
        ("deployment_time_s", "DEPLOYMENT"), ("landing_time_s", "LANDED"))
        if summary.get(key) is not None and summary[key] <= duration_s], key=lambda change: change[0])
    descriptions = [{"id": name, "device": f"synthetic-camera-{name}",
        "width": clip["width"], "height": clip["height"], "sensor_size": [2064, 1552],
        "flip_x": False, "flip_y": False, "sensor_crop": [256, 0, 1552, 1552],
        "simulated": True} for name in ("A", "B")]
    records = []

    def add(kind, pts, **fields):
        record = {"schema_version": 1, "type": kind, "session_id": session_id,
                  "pts_us": pts, "simulated": True, "backend": "simulation", **fields}
        records.append(record)
        return record

    for pts in range(0, end_us + 1, 1_000_000):
        add("session", pts, clock_origin_ns=CLOCK_ORIGIN_NS, clock_domain="CLOCK_BOOTTIME",
            clock_origin_source="synthetic_virtual_clock", cameras=descriptions,
            transport_pts_offset_us=TRANSPORT_OFFSET_US,
            metadata_pes_clock="synthetic_monotonic_queue_admission",
            simulation_events={key: summary[key] for key in ("deployment_time_s", "main_deployment_time_s", "landing_time_s") if summary.get(key) is not None})
    for sequence, frame in enumerate(clip["frames"]):
        pts = frame["pts_us"]
        if pts >= end_us:
            break
        for name in ("A", "B"):
            row = add("frame", pts, camera_id=name, sequence=sequence,
                sensor_timestamp_ns=CLOCK_ORIGIN_NS + pts * 1000,
                sensor_crop=[256, 0, 1552, 1552], exposure_us=500,
                status="encoded", drop_reason=None)
            if attitudes is not None:
                row["simulation_attitude"] = {"R_world_from_rig": attitudes[sequence]["R_world_from_rig"],
                    "frame": "ENU", "source": "scene"}

    samples = []
    for sequence, pts in enumerate(range(0, end_us + 1, 40000)):
        altitude = trajectory_height(trajectory, times, pts / 1e6)
        pressure = 101325 * (1 - altitude / 44330)**(1 / .190263)
        temperature = 15 - .0065 * altitude
        samples.append((sequence, pts, float(f"{pressure:.6g}"), float(f"{temperature:.6g}")))
    cursor = 0
    for publication in [*range(0, end_us + 1, 100000), end_us]:
        batch = []
        while cursor < len(samples) and samples[cursor][1] <= publication:
            batch.append(samples[cursor]); cursor += 1
        if not batch:
            continue
        base = batch[0][1]
        rows = [[sequence, pts - base, pts - base, pts - base, pressure, temperature]
                for sequence, pts, pressure, temperature in batch]
        add("sensors", publication, clock_domain="CLOCK_BOOTTIME",
            acquisition_clock_domain="CLOCK_BOOTTIME", timestamp_basis="host_read",
            sample_count=len(rows), dropped_samples=0, value_significant_digits=6,
            sources=[{"source": "bmp581", "valid": True,
                      "base_monotonic_us": origin_us + base, "rows": rows,
                      "shared_values": {"in_operating_pressure_range": True}}])

    launch_us = round(summary["ignition_time_s"] * 1e6)
    apogee_us = round(summary["apogee_time_s"] * 1e6)
    landing_us = round(summary["landing_time_s"] * 1e6) if summary.get("landing_time_s") is not None else math.inf
    fc_times = sorted(set(range(0, end_us + 1, 500000)) | {pts for pts, _ in changes})
    status_rows = []
    for seq, pts in enumerate(fc_times):
        previous_events = [(time, event) for time, event in changes if time <= pts]
        event = previous_events[-1][1] if previous_events else "NONE"
        fields = {"valid": True, "stale": False,
            "phase": "PAD" if pts < launch_us else "ASCENT" if pts < apogee_us else "DESCENT" if pts < landing_us else "LANDED",
            "event": event, "inferred_epoch": 0, "epoch_reacquisition_pending": False,
            "seq": seq, "event_seq": len(previous_events), "fc_uptime_ms": pts // 1000,
            "receive_monotonic_us": origin_us + pts, "monotonic_us": origin_us + pts,
            "publication_timestamp_ns": CLOCK_ORIGIN_NS + pts * 1000,
            "host_clock_domain": "CLOCK_BOOTTIME",
            "counters": {"accepted": seq + 1, "bad_crc": 0, "malformed": 0,
                         "duplicate": 0, "out_of_order": 0, "dropped_events": 0}}
        status_rows.append(add("flight_status", pts, **fields))
        for event_seq, (time, event) in enumerate(previous_events, start=1):
            if time == pts:
                add("flight_event", pts, **{**fields, "event": event, "event_seq": event_seq})
    for pts in range(0, end_us + 1, 1_000_000):
        status = next(row for row in reversed(status_rows) if row["pts_us"] <= pts)
        add("health", pts, phase=status["phase"], phase_stale=False, flight_uart=status,
            lifecycle="running", degraded=False,
            components={"A": "synthetic_video", "B": "synthetic_video"})
    return sorted(records, key=lambda record: record["pts_us"])


def remux(paths: dict[str, Path], output: Path, records: list[dict], duration_s: float) -> dict:
    counts = Counter()
    adjustment_max_us = 0
    with ExitStack() as stack:
        inputs = {name: stack.enter_context(av.open(str(path))) for name, path in paths.items()}
        target = stack.enter_context(av.open(str(output), "w", format="mpegts", options={
            "mpegts_start_pid": "256", "mpegts_copyts": "1", "muxrate": "9000000",
            "pcr_period": "20", "pat_period": "0.1"}))
        streams = {name: target.add_stream_from_template(source.streams.video[0])
                   for name, source in inputs.items()}
        data = target.add_data_stream("bin_data")
        data.time_base = TIME_BASE

        def video_packets(name):
            for packet in inputs[name].demux(inputs[name].streams.video[0]):
                if not packet.size:
                    continue
                timestamp = pts_microseconds(packet.pts, packet.time_base)
                if timestamp >= round(duration_s * 1e6):
                    break
                packet.pts += round(Fraction(TRANSPORT_OFFSET_US, 1_000_000) / packet.time_base)
                packet.dts += round(Fraction(TRANSPORT_OFFSET_US, 1_000_000) / packet.time_base)
                packet.stream = streams[name]
                yield timestamp, 1 if name == "A" else 2, packet, name

        def metadata_packets():
            nonlocal adjustment_max_us
            last_tick = -1
            for record in records:
                packet = av.Packet(json.dumps(record, separators=(",", ":"), allow_nan=False).encode())
                tick = round(Fraction(record["pts_us"] + TRANSPORT_OFFSET_US, 1_000_000) / TIME_BASE)
                adjusted = max(tick, last_tick + 1)
                adjustment_max_us = max(adjustment_max_us, pts_microseconds(adjusted - tick, TIME_BASE))
                packet.pts = packet.dts = adjusted
                packet.time_base = TIME_BASE
                packet.stream = data
                last_tick = adjusted
                yield record["pts_us"], 0, packet, "metadata"

        for _, _, packet, name in heapq.merge(metadata_packets(), video_packets("A"), video_packets("B"),
                                            key=lambda item: item[:2]):
            target.mux(packet)
            counts[name] += 1
    with av.open(str(output)) as source:
        pids = {stream.type + (f"_{stream.index}" if stream.type == "video" else ""): stream.id
                for stream in source.streams}
    if list(pids.values()) != [256, 257, 258]:
        raise ValueError(f"Unexpected output PIDs: {pids}")
    return {"pids": pids, "packet_counts": dict(counts),
            "metadata_serialization_max_adjustment_us": adjustment_max_us}


def generate(output: Path, *, duration_s: float | None = None, force: bool = False,
             source_manifest: Path | None = None, trajectory: Path | None = None,
             camera_attitudes: Path | None = None) -> dict:
    source_path = source_manifest or ROOT / "docs/assets/video/manifest-v3.json"
    flight_path = trajectory or ROOT / "docs/assets/launch.json"
    source = json.loads(source_path.read_text())
    flight = json.loads(flight_path.read_text())
    expected_flight = source.get("fingerprint", {}).get("flight_sha256")
    if expected_flight and digest(flight_path) != expected_flight:
        raise ValueError("Trajectory differs from the capture fingerprint; supply its matching --trajectory")
    if source.get("start_s", 0) != 0:
        raise ValueError("Demo remux requires a capture beginning at trajectory time zero")
    paths = {name: source_path.parent / source["cameras"][name.lower()]["mp4"] for name in ("A", "B")}
    clips = {name: clip_info(path) for name, path in paths.items()}
    if clips["A"] != clips["B"]:
        raise ValueError("Camera clips do not share dimensions and packet timestamps")
    available = min(source["duration_s"], clips["A"]["duration_s"], flight["trajectory"][-1]["t_s"])
    duration_s = available if duration_s is None else duration_s
    if not math.isfinite(duration_s) or not 0 < duration_s <= available:
        raise ValueError(f"Duration must be positive and no longer than the {available:g}s saved clip")
    if any((output / name).exists() for name in ("launch.ts", "calibration.json", "manifest.json")) and not force:
        raise ValueError("Demo files already exist; use --force to regenerate them")
    output.mkdir(parents=True, exist_ok=True)
    fingerprints = {"source_manifest": digest(source_path), "trajectory": digest(flight_path),
                    **{name: digest(path) for name, path in paths.items()}}
    attitude_path = camera_attitudes
    if attitude_path is None and source.get("camera_attitudes"):
        attitude_path = source_path.parent / source["camera_attitudes"]
    attitudes = None
    if attitude_path is not None:
        document = json.loads(attitude_path.read_text())
        if document.get("simulated") is not True or document.get("frame") != "ENU" or document.get("source") != "scene":
            raise ValueError("Attitude fixture must declare simulated ENU scene provenance")
        if source.get("camera_attitudes_sha256") and digest(attitude_path) != source["camera_attitudes_sha256"]:
            raise ValueError("Attitude fixture differs from the render manifest")
        attitudes = document["frames"]
        if len(attitudes) != len(clips["A"]["frames"]):
            raise ValueError("Attitude fixture must contain one exact pose per encoded pair")
        for sequence, row in enumerate(attitudes):
            matrix = np.asarray(row["R_world_from_rig"], dtype=float)
            if row.get("sequence") != sequence or matrix.shape != (3, 3) or not np.isfinite(matrix).all() \
                    or not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-8) \
                    or not math.isclose(float(np.linalg.det(matrix)), -1, abs_tol=1e-8):
                raise ValueError("Attitude rows require the exact orthogonal reflected image basis in sequence")
        fingerprints["camera_attitudes"] = digest(attitude_path)
    session_id = "ground-demo-" + hashlib.sha256(json.dumps(fingerprints, sort_keys=True).encode()).hexdigest()[:16]
    calibration = fit_calibration(source)
    records = telemetry_records(source, flight, clips["A"], session_id, duration_s, attitudes)
    with tempfile.NamedTemporaryFile(prefix=".launch-", suffix=".ts", dir=output, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        transport = remux(paths, temporary, records, duration_s)
        temporary.replace(output / "launch.ts")
    finally:
        temporary.unlink(missing_ok=True)
    manifest = {"schema_version": 1, "kind": "synthetic_ground_station_fixture", "simulated": True,
        "backend": "simulation", "session_id": session_id, "duration_s": duration_s,
        "clock_origin_ns": CLOCK_ORIGIN_NS, "transport_pts_offset_us": TRANSPORT_OFFSET_US,
        "source_manifest": manifest_path(source_path),
        "trajectory": manifest_path(flight_path), "input_sha256": fingerprints,
        "videos": {name: {"path": manifest_path(paths[name]),
                          "width": clips[name]["width"], "height": clips[name]["height"]} for name in paths},
        "outputs": {"transport": "launch.ts", "calibration": "calibration.json"},
        "camera_attitudes": manifest_path(attitude_path) if attitude_path else None,
        "flight_summary": flight["summary"], "simulation_model": flight.get("model"),
        "generator": Path(__file__).relative_to(ROOT).as_posix(), "generator_sha256": digest(Path(__file__)),
        "transport": transport, "metadata_counts": dict(Counter(row["type"] for row in records)),
        "calibration_fit": calibration["fit"],
        "orientation_provenance": {
            "shader_sha256": digest(ROOT / "docs/engine.js"),
            "clip_manifest_shader_sha256": source["fingerprint"]["inputs"]["engine.js"],
            "basis": "Shader +Y pixel offset is body-up; Canvas PNG export places it at image-top. Recheck decoded orientation if replacing the source clips."},
        "telemetry_model": {"pressure_pa": "101325*(1-altitude_m/44330)^(1/0.190263)",
            "temperature_c": "15-0.0065*altitude_m", "sensor_rate_hz": 25,
            "publication_rate_hz": 10, "fc_heartbeat_hz": 2,
            "fc_event_basis": "OpenRocket summary events; zero simulated UART delay; not measured ignition"},
        "limitations": ["Encoded optical scene and all telemetry are simulated, not hardware measurements.",
            "The approximate Mei model is fitted to the simulator function, not a measured lens calibration.",
            "Saved packet timestamp quantization is preserved; camera PTS equality is synthetic, not shutter-sync evidence.",
            "No RF loss, sensor noise, UART delay or hardware performance is modeled.",
            "Recovery attitude, effective-canopy geometry and post-contact motion are assumed, not measured."
                if flight["summary"].get("landing_time_s") is not None else "The clip ends during early descent; no landing telemetry is generated.",
            "Scene attitude uses a reflected image basis (determinant -1); it is not a measured physical orientation."],
        "runtime": {"pyav": av.__version__, "numpy": np.__version__, "scipy": SCIPY_VERSION}}
    for name, document in (("calibration.json", calibration), ("manifest.json", manifest)):
        (output / name).write_text(json.dumps(document, indent=2, allow_nan=False) + "\n")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/ground-station/demo")
    parser.add_argument("--duration", type=float, help="Optional shorter prefix; never extrapolates the saved clip")
    parser.add_argument("--source-manifest", type=Path, help="Render manifest; defaults to the saved 33 s docs clips")
    parser.add_argument("--trajectory", type=Path, help="Matching launch.json; defaults to docs/assets/launch.json")
    parser.add_argument("--camera-attitudes", type=Path, help="Exact simulated render poses; inferred from manifest when present")
    parser.add_argument("--force", action="store_true", help="Regenerate existing demo outputs")
    args = parser.parse_args(argv)
    try:
        manifest = generate(args.output.resolve(), duration_s=args.duration, force=args.force,
            source_manifest=args.source_manifest.resolve() if args.source_manifest else None,
            trajectory=args.trajectory.resolve() if args.trajectory else None,
            camera_attitudes=args.camera_attitudes.resolve() if args.camera_attitudes else None)
    except (OSError, ValueError, av.FFmpegError) as exc:
        parser.exit(2, f"ground demo: {exc}\n")
    print(json.dumps({"output": str(args.output.resolve()), "duration_s": manifest["duration_s"],
                      "packets": manifest["transport"]["packet_counts"],
                      "calibration_fit": manifest["calibration_fit"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

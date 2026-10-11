"""Check the real saved clips retain their compressed pictures and shared timeline."""
import hashlib
import importlib.util
import json
import math
from pathlib import Path

import av
import numpy as np
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools/ground_demo.py"
spec = importlib.util.spec_from_file_location("ground_demo", SCRIPT)
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
from pigeonvision.ground.projection import project_ray, validate_calibration
from pigeonvision.ground.transport import H264Normalizer, annexb_nals, pts_microseconds, read_metadata


@pytest.fixture(scope="module")
def prefix(tmp_path_factory):
    output = tmp_path_factory.mktemp("ground-demo")
    manifest = demo.generate(output, duration_s=3.2)
    return output, manifest


def video_fingerprints(path, pid=None, end_us=3_200_000, offset_us=0):
    with av.open(str(path)) as container:
        stream = next(s for s in container.streams.video if pid is None or s.id == pid)
        normalizer = H264Normalizer(stream.codec_context.extradata or b"")
        frames = []
        for packet in container.demux(stream):
            if not packet.size:
                continue
            pts = pts_microseconds(packet.pts, packet.time_base) - offset_us
            if pts >= end_us:
                break
            unit = normalizer.normalize(bytes(packet), "A", pts)
            assert unit is not None
            slices = [nal for nal in annexb_nals(unit.data) if nal[0] & 31 in (1, 5)]
            assert slices
            payload = b"".join(len(nal).to_bytes(4, "big") + nal for nal in slices)
            frames.append((pts, hashlib.sha256(payload).hexdigest()))
        return frames


def test_real_h264_slices_pids_and_common_pts_are_preserved(prefix):
    output, manifest = prefix
    with av.open(str(output / "launch.ts")) as container:
        assert [(s.id, s.type) for s in container.streams] == [(256, "video"), (257, "video"), (258, "data")]
        packets = (packet for packet in container.demux() if packet.size)
        first = next(packets)
        assert first.stream.id == 258
        session = read_metadata(bytes(first))["record"]
        assert session["type"] == "session"
        assert session["transport_pts_offset_us"] == 1_000_000
    times = []
    for name, pid in (("A", 256), ("B", 257)):
        source = demo.ROOT / manifest["videos"][name]["path"]
        original = video_fingerprints(source)
        remuxed = video_fingerprints(output / "launch.ts", pid=pid, offset_us=1_000_000)
        assert len(original) == len(remuxed) == 96
        assert original == remuxed, "Remuxing must not alter any compressed H.264 picture slice"
        times.append([pts for pts, _ in remuxed])
    assert times[0] == times[1]
    assert times[0][0] == 0


def test_demo_metadata_uses_acquisition_clock_pressure_model_and_truthful_scope(prefix):
    output, manifest = prefix
    source_flight = json.loads((demo.ROOT / manifest["trajectory"]).read_text())
    trajectory = source_flight["trajectory"]
    times = [row["t_s"] for row in trajectory]
    records, wire_pts = [], []
    with av.open(str(output / "launch.ts")) as container:
        stream = next(s for s in container.streams if s.id == 258)
        for packet in container.demux(stream):
            if packet.size:
                records.append(read_metadata(bytes(packet))["record"])
                wire_pts.append(pts_microseconds(packet.pts, packet.time_base))
    assert all(b > a for a, b in zip(wire_pts, wire_pts[1:]))
    assert all(row["schema_version"] == 1 and row["simulated"] is True and row["backend"] == "simulation" for row in records)
    assert all(0 <= row["pts_us"] <= 3_200_000 for row in records)
    assert {row["session_id"] for row in records} == {manifest["session_id"]}
    samples = []
    origin = manifest["clock_origin_ns"] // 1000
    for record in records:
        if record["type"] != "sensors":
            continue
        assert record["sample_count"] == sum(len(source["rows"]) for source in record["sources"])
        for source in record["sources"]:
            assert source["source"] == "bmp581" and source["valid"] is True
            for sequence, acquisition, read_start, last_good, pressure, temperature in source["rows"]:
                pts = source["base_monotonic_us"] + acquisition - origin
                assert 0 <= pts <= record["pts_us"]
                altitude = demo.trajectory_height(trajectory, times, pts / 1e6)
                expected = 101325 * (1 - altitude / 44330)**(1 / .190263)
                assert abs(pressure - expected) < .51  # Six significant digits on the wire.
                assert abs(temperature - (15 - .0065 * altitude)) < .001
                samples.append((sequence, pts))
    assert samples == [(index, index * 40000) for index in range(81)]
    events = [row for row in records if row["type"] == "flight_event"]
    assert [row["event"] for row in events] == ["LAUNCH"]
    assert events[0]["receive_monotonic_us"] - origin == 3_000_000
    assert not any(row.get("phase") == "LANDED" for row in records)
    assert all(row["sensor_crop"] == [256, 0, 1552, 1552] for row in records if row["type"] == "frame")


def test_synthetic_mei_fit_has_correct_pixel_convention_orientation_and_error_bound(prefix):
    output, manifest = prefix
    calibration = validate_calibration(json.loads((output / "calibration.json").read_text()))
    assert calibration["simulated"] is True
    assert calibration["calibration_kind"] == "synthetic_model_approximation_not_measured"
    assert calibration["fit"]["radial_max_error_px"] < .65
    assert calibration["fit"]["radial_rms_error_px"] < .14
    for name, sign in (("A", 1), ("B", -1)):
        camera = calibration["cameras"][name]
        assert project_ray(camera, (0, 0, sign)) == (775.5, 775.5)
        for degrees in (10, 70, 90, 112.9):
            angle = math.radians(degrees)
            ray = (sign * math.sin(angle) / math.sqrt(2), math.sin(angle) / math.sqrt(2), sign * math.cos(angle))
            radius = 1.1 * math.sin(.4 * angle) / .4 / .00225
            expected = np.array([775.5, 775.5]) + radius / math.sqrt(2)
            actual = project_ray(camera, ray)
            assert actual is not None
            assert np.linalg.norm(np.array(actual) - expected) < .65
        outside = math.radians(113)
        assert project_ray(camera, (sign * math.sin(outside), 0, sign * math.cos(outside))) is None
    assert manifest["calibration_fit"] == calibration["fit"]


def test_generation_is_reproducible_and_refuses_overwrite_or_trajectory_extrapolation(prefix, tmp_path):
    output, _ = prefix
    before = {name: demo.digest(output / name) for name in ("launch.ts", "calibration.json", "manifest.json")}
    with pytest.raises(ValueError, match="already exist"):
        demo.generate(output, duration_s=3.2)
    demo.generate(output, duration_s=3.2, force=True)
    assert {name: demo.digest(output / name) for name in before} == before
    with pytest.raises(ValueError, match="no longer"):
        demo.generate(tmp_path / "beyond-clip", duration_s=33.1)
    with pytest.raises(ValueError, match="extrapolate"):
        demo.trajectory_height([{ "t_s": 0, "altitude_m": 0 }, { "t_s": 1, "altitude_m": 10 }], [0, 1], 1.01)


def test_exact_scene_attitude_is_bound_to_encoded_sequence_not_rounded_nominal_pts():
    flight = json.loads((demo.ROOT / "docs/assets/launch.json").read_text())
    clip = {"width":1552,"height":1552,"frames":[{"pts_us":0},{"pts_us":33000}]}
    basis = [[0,0,1],[1,0,0],[0,-1,0]]
    poses = [{"sequence":0,"pts_us":0,"R_world_from_rig":basis},
             {"sequence":1,"pts_us":33333,"R_world_from_rig":basis}]
    records = demo.telemetry_records({},flight,clip,"test",.1,poses)
    frames = [record for record in records if record["type"] == "frame"]
    assert [row["pts_us"] for row in frames] == [0,0,33000,33000]
    assert all(row["simulation_attitude"] == {"R_world_from_rig":basis,"frame":"ENU","source":"scene"} for row in frames)


def test_equal_time_flight_events_keep_their_sequence_and_survive_mux(prefix, tmp_path):
    _, manifest = prefix
    flight = json.loads((demo.ROOT / manifest["trajectory"]).read_text())
    flight["summary"].update(apogee_time_s=3.1, deployment_time_s=3.1)
    paths = {name: demo.ROOT / manifest["videos"][name]["path"] for name in ("A", "B")}
    records = demo.telemetry_records({}, flight, demo.clip_info(paths["A"]), "equal-time-events", 3.2)
    status = next(row for row in records if row["type"] == "flight_status" and row["pts_us"] == 3_100_000)
    assert (status["event"], status["event_seq"]) == ("DEPLOYMENT", 3)

    transport = tmp_path / "equal-time.ts"
    demo.remux(paths, transport, records, 3.2)
    events, wire_pts = [], []
    with av.open(str(transport)) as container:
        metadata = next(stream for stream in container.streams if stream.id == 258)
        for packet in container.demux(metadata):
            if not packet.size:
                continue
            record = read_metadata(bytes(packet))["record"]
            if record["type"] == "flight_event":
                events.append((record["event"], record["pts_us"], record["event_seq"]))
                wire_pts.append(pts_microseconds(packet.pts, packet.time_base))
    assert events == [("LAUNCH", 3_000_000, 1), ("APOGEE", 3_100_000, 2), ("DEPLOYMENT", 3_100_000, 3)]
    assert all(b > a for a, b in zip(wire_pts, wire_pts[1:]))


def test_capture_and_trajectory_fingerprints_cannot_be_mixed(tmp_path):
    trajectory = tmp_path / "wrong-flight.json"
    flight = json.loads((demo.ROOT / "docs/assets/launch.json").read_text())
    flight["summary"]["duration_s"] += 1
    trajectory.write_text(json.dumps(flight))
    with pytest.raises(ValueError, match="capture fingerprint"):
        demo.generate(tmp_path / "demo", trajectory=trajectory)

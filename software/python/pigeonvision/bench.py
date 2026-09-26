"""Capture orchestration and repeatable matrix benchmarks with host telemetry."""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from .doctor import command, read_text

DEFAULTS = {"schema_version": 1, "width": 1552, "height": 1552, "fps": 30,
            "bitrate": 4_000_000, "vbv_bits": 2_000_000, "preset": "ultrafast",
            "segment_seconds": 60, "min_free_bytes": 2_147_483_648,
            "encode": True, "record": True, "udp_destination": None, "mux_bitrate": 9_000_000}


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the same scalar types/ranges accepted by the native executable.

    Relative session_dir is deliberately preserved here: the process executing
    capture resolves it against its CWD, including on the remote host over SSH.
    A configuration file's own directory does not change that base directory.
    """
    if not isinstance(config, dict):
        raise ValueError("Capture configuration must be a JSON object.")
    out = DEFAULTS | config
    if type(out["schema_version"]) is not int or out["schema_version"] != 1:
        raise ValueError("Only integer capture schema_version 1 is supported.")
    cameras = out.get("cameras", [])
    if not isinstance(cameras, list) or not 1 <= len(cameras) <= 2 or any(not isinstance(c, dict) for c in cameras):
        raise ValueError("Map one or two unique physical cameras to logical IDs A/B.")
    names = [c.get("id") for c in cameras]
    if any(name not in ("A", "B") for name in names) or len(set(names)) != len(names):
        raise ValueError("Map one or two unique physical cameras to logical IDs A/B.")
    if any(not isinstance(c.get("device"), str) or not c["device"] for c in cameras):
        raise ValueError("Each camera requires its actual libcamera device ID; run pv doctor.")
    if len({c["device"] for c in cameras}) != len(cameras):
        raise ValueError("A and B must name different physical devices.")
    for camera in cameras:
        for name in ("flip_x", "flip_y"):
            if name in camera and not isinstance(camera[name], bool):
                raise ValueError(f"Camera {name} must be true or false.")
    if not isinstance(out.get("session_dir"), str) or not out["session_dir"]:
        raise ValueError("session_dir must name a new session directory.")
    ranges = {"width": (16, 2064), "height": (16, 1552), "fps": (1, 120),
              "bitrate": (10_000, 100_000_000), "vbv_bits": (10_000, 100_000_000),
              "segment_seconds": (1, 86400), "mux_bitrate": (1, 100_000_000),
              "min_free_bytes": (0, 2**64 - 1)}
    for name, (minimum, maximum) in ranges.items():
        if type(out[name]) is not int:
            raise ValueError(f"{name} must be an integer.")
        if not minimum <= out[name] <= maximum:
            raise ValueError(f"{name} must lie between {minimum} and {maximum}.")
    if out["width"] % 2 or out["height"] % 2:
        raise ValueError("YUV420 output width and height must be even.")
    for name in ("encode", "record"):
        if not isinstance(out[name], bool):
            raise ValueError(f"{name} must be true or false.")
    if "duration_seconds" in out:
        duration = out["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration < 0:
            raise ValueError("duration_seconds must be finite and nonnegative (0 means run until stopped).")
    if out["udp_destination"] is not None and not isinstance(out["udp_destination"], str):
        raise ValueError("udp_destination must be a host:port string or null.")
    if out["udp_destination"] == "":
        out["udp_destination"] = None
    if not out["encode"] and (out["record"] or out["udp_destination"]):
        raise ValueError("Capture-only mode requires record=false and no UDP destination.")
    if out["preset"] not in {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}:
        raise ValueError("Unsupported x264 preset.")
    if out["udp_destination"] and out["mux_bitrate"] < out["bitrate"] * len(cameras) + 500_000:
        raise ValueError("Transport needs at least 500 kbit/s headroom above combined video bitrate.")
    out["cameras"] = [{"flip_x": False, "flip_y": True} | c for c in cameras]
    return out


def timestamp_us(sensor_timestamp_ns: int, clock_origin_ns: int) -> int:
    """Convert both cameras using the same CLOCK_BOOTTIME origin, retaining offset."""
    if sensor_timestamp_ns < clock_origin_ns:
        raise ValueError("Sensor timestamp precedes session origin; check clock domain.")
    return (sensor_timestamp_ns - clock_origin_ns) // 1000


class HostSampler:
    def __init__(self):
        self.previous_cpu: tuple[int, int] | None = None

    def sample(self) -> dict[str, Any]:
        cpu = None
        stat = read_text("/proc/stat")
        if stat:
            values = [int(v) for v in stat.splitlines()[0].split()[1:9]]
            total, idle = sum(values), values[3] + values[4]
            if self.previous_cpu:
                old_total, old_idle = self.previous_cpu
                if total > old_total:
                    cpu = 100 * (1 - (idle - old_idle) / (total - old_total))
            self.previous_cpu = total, idle
        mem = read_text("/proc/meminfo")
        mem_values = {}
        if mem:
            mem_values = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in mem.splitlines()}
        temperature = read_text("/sys/class/thermal/thermal_zone0/temp")
        clock_khz = read_text("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
        throttle = command(["vcgencmd", "get_throttled"], timeout=2)
        throttle_bits = None
        if throttle.get("returncode") == 0:
            try:
                throttle_bits = int(throttle["output"].split("=", 1)[1], 16)
            except (ValueError, IndexError):
                pass
        return {"schema_version": 1, "type": "host_health", "monotonic_ns": time.monotonic_ns(),
                "cpu_percent": cpu, "temperature_c": int(temperature) / 1000 if temperature else None,
                "cpu_clock_hz": int(clock_khz) * 1000 if clock_khz else None,
                "memory_available_bytes": mem_values.get("MemAvailable"),
                "memory_total_bytes": mem_values.get("MemTotal"),
                "throttled_bits": throttle_bits}


def _append(path: Path, data: dict[str, Any]) -> None:
    # A separate host file avoids competing with the native buffered health writer.
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(data, allow_nan=False) + "\n").encode())
    finally:
        os.close(fd)


def run_capture(config: dict[str, Any], binary: str = "pv-capture", sample_interval: float = 1) -> int:
    config = validate_config(config)
    executable = shutil.which(binary)
    if executable is None:
        raise RuntimeError(f"Cannot find {binary!r}. Build software/flight on the provisioned CM5, "
                           "then pass --binary /absolute/path/pv-capture or use --ssh with pv installed there.")
    # Resolve only on the execution host; SSH must not resolve paths on the Mac.
    session = Path(config["session_dir"]).resolve()
    if session.exists() and any(session.iterdir()):
        raise ValueError(f"Session directory is not empty: {session}. Choose a new directory.")
    session.mkdir(parents=True, exist_ok=True)
    config["session_dir"] = str(session)
    config_file = session / "capture-config.json"
    config_file.write_text(json.dumps(config, indent=2) + "\n")
    stop = threading.Event()
    telemetry_errors = []

    def collect():
        sampler = HostSampler()
        while not stop.is_set():
            try:
                data = sampler.sample()
                data["disk_free_bytes"] = shutil.disk_usage(session).free
                _append(session / "host-health.jsonl", data)
            except (OSError, ValueError) as exc:
                telemetry_errors.append(str(exc))
            stop.wait(sample_interval)

    started = time.monotonic_ns()
    with (session / "capture.stdout.log").open("w") as stdout, (session / "capture.stderr.log").open("w") as stderr:
        process = subprocess.Popen([executable, "--config", str(config_file)], stdout=stdout, stderr=stderr)
        thread = threading.Thread(target=collect, daemon=True)
        thread.start()
        try:
            code = process.wait()
        except KeyboardInterrupt:
            process.send_signal(signal.SIGINT)
            try:
                code = process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    code = process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait()
        finally:
            stop.set()
            thread.join(timeout=5)
    _append(session / "host-health.jsonl", {"schema_version": 1, "type": "capture_exit", "returncode": code,
             "monotonic_ns": time.monotonic_ns(), "duration_seconds": (time.monotonic_ns() - started) / 1e9,
             "telemetry_errors": telemetry_errors})
    if code:
        print(f"Capture exited {code}; inspect {session / 'capture.stderr.log'}")
    return code


def matrix(base: dict[str, Any], output: Path, duration: float, destination: str | None,
           presets: list[str] | None = None) -> list[dict[str, Any]]:
    if duration <= 0:
        raise ValueError("Benchmark duration must be positive.")
    base = validate_config(base)
    cameras = base["cameras"]
    if len(cameras) != 2:
        raise ValueError("The benchmark matrix needs both mapped cameras.")
    profiles = [("capture", False, False, None, cameras),
                ("one-encode", True, False, None, cameras[:1]),
                ("two-encode", True, False, None, cameras),
                ("record", True, True, None, cameras)]
    if destination:
        profiles += [("stream", True, False, destination, cameras),
                     ("combined", True, True, destination, cameras)]
    result = []
    for width, height in ((1080, 1080), (1552, 1552), (2064, 1552)):
        for preset in presets or ["ultrafast", "superfast", "veryfast"]:
            for name, encode, record, udp, selected in profiles:
                if not encode and preset != (presets or ["ultrafast"])[0]:
                    continue
                tag = f"{width}x{height}-{preset}-{name}"
                cfg = copy.deepcopy(base)
                cfg.update(width=width, height=height, preset=preset, cameras=selected, encode=encode,
                           record=record, udp_destination=udp, duration_seconds=duration,
                           session_dir=str(output / tag))
                result.append(validate_config(cfg))
    return result


def run_matrix(base: dict[str, Any], output: Path, duration: float, destination: str | None,
               binary: str, presets: list[str] | None = None) -> int:
    from .report import summarize
    configurations = matrix(base, output, duration, destination, presets)
    output.mkdir(parents=True, exist_ok=True)
    (output / "matrix.json").write_text(json.dumps({"schema_version": 1, "created_utc": dt.datetime.now(dt.UTC).isoformat(),
        "configurations": configurations, "skipped": [] if destination else ["stream", "combined"]}, indent=2) + "\n")
    outcomes = []
    for index, cfg in enumerate(configurations, 1):
        print(f"[{index}/{len(configurations)}] {cfg['session_dir']}", flush=True)
        code = run_capture(cfg, binary)
        summary = summarize(Path(cfg["session_dir"]))
        outcomes.append({"session_dir": cfg["session_dir"], "returncode": code, "report": summary})
        (output / "results.json").write_text(json.dumps(outcomes, indent=2) + "\n")
        if code:
            return code  # Stop and investigate instead of hiding failures in later profiles.
    return 0

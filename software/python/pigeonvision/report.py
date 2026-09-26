"""Summarize measured session evidence without turning missing data into a pass."""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.exists():
        return [], [f"Missing {path.name}"]
    records, problems = [], []
    with path.open() as source:
        for number, line in enumerate(source, 1):
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("expected an object")
                records.append(record)
            except (ValueError, json.JSONDecodeError) as exc:
                problems.append(f"{path.name}:{number}: {exc}")
    return records, problems


def _assessment(value, condition, unit=None):
    return {"value": value, "unit": unit,
            "status": "unknown" if value is None else "met" if condition(value) else "not_met"}


def summarize(session: Path) -> dict[str, Any]:
    frames, problems = read_jsonl(session / "frames.jsonl")
    health, health_problems = read_jsonl(session / "health.jsonl")
    problems += health_problems
    if (session / "host-health.jsonl").exists():
        host_health, host_problems = read_jsonl(session / "host-health.jsonl")
        health += host_health
        problems += host_problems
    manifest_path = session / "session.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    config = manifest.get("configuration", {})
    if not config and (session / "capture-config.json").exists():
        config = json.loads((session / "capture-config.json").read_text())
    native_samples = [r for r in health if r.get("type") in ("health", "sample") and "cpu_busy_percent" in r]
    samples = [r for r in health if r.get("type") == "host_health"]
    if not samples:
        samples = [dict(r, cpu_percent=r.get("cpu_busy_percent"), cpu_clock_hz=r.get("cpu_clock_khz") * 1000 if r.get("cpu_clock_khz") is not None else None) for r in native_samples]
    cpus = [r["cpu_percent"] for r in samples if r.get("cpu_percent") is not None]
    temperatures = [r["temperature_c"] for r in samples if r.get("temperature_c") is not None]
    throttle = [r["throttled_bits"] for r in samples if r.get("throttled_bits") is not None]
    clock_rates = [r["cpu_clock_hz"] for r in samples if r.get("cpu_clock_hz") is not None]
    free_memory = [r["memory_available_bytes"] for r in samples if r.get("memory_available_bytes") is not None]
    native_ends = [r for r in health if r.get("type") == "session_end"]
    exits = [r for r in health if r.get("type") == "capture_exit"]
    duration = exits[-1].get("duration_seconds") if exits else None
    camera_reports = {}
    expected_ids = [c["id"] for c in config.get("cameras", [])] or ["A", "B"]
    for camera_id in expected_ids:
        rows = [f for f in frames if f.get("camera_id") == camera_id]
        sequences = sorted({f["sequence"] for f in rows if isinstance(f.get("sequence"), int)})
        missing = sequences[-1] - sequences[0] + 1 - len(sequences) if sequences else None
        dropped = [f for f in rows if f.get("drop_reason") is not None]
        encoded = [f for f in rows if f.get("drop_reason") is None and f.get("status") != "captured"]
        reported_times = sorted(f["sensor_timestamp_ns"] for f in rows if isinstance(f.get("sensor_timestamp_ns"), int))
        interval = (reported_times[-1] - reported_times[0]) / 1e9 if len(reported_times) > 1 else None
        fps = (len(reported_times) - 1) / interval if interval and interval > 0 else None
        camera_reports[camera_id] = {"records": len(rows), "encoded_records": len(encoded), "dropped_records": len(dropped),
                "unexplained_sequence_gaps": missing, "observed_capture_fps": fps,
                "timestamp_span_seconds": interval, "drop_reasons": sorted({str(f["drop_reason"]) for f in dropped}),
                "queue_high_water": max((c.get("queue_high_water", 0) for sample in native_samples for c in sample.get("cameras", []) if c.get("camera_id") == camera_id), default=None)}
    measurements = {}
    path = session / "measurements.json"
    if path.exists():
        measurements = json.loads(path.read_text())
    gaps = sum(v["unexplained_sequence_gaps"] for v in camera_reports.values()) if all(v["unexplained_sequence_gaps"] is not None for v in camera_reports.values()) else None
    target_fps = config.get("fps", 30)
    fps_met = all(v["observed_capture_fps"] is not None and abs(v["observed_capture_fps"] - target_fps) <= target_fps * .01 for v in camera_reports.values()) if frames else None
    minimum_span = min((v["timestamp_span_seconds"] for v in camera_reports.values()), default=None) if all(v["timestamp_span_seconds"] is not None for v in camera_reports.values()) else None
    log_losses = [r["log_records_lost"] for r in native_samples if r.get("log_records_lost") is not None]
    checks = {
        "no_log_records_lost": _assessment(max(log_losses) if log_losses else None, lambda x: x == 0, "records"),
        "dual_1552_record_and_stream_configuration": _assessment(
            ({c["id"] for c in config.get("cameras", [])} == {"A", "B"} and config.get("width") == 1552 and config.get("height") == 1552 and config.get("fps") == 30 and config.get("encode", True) and config.get("record") is True and bool(config.get("udp_destination"))) if config else None, bool),
        "one_hour_camera_timestamps": _assessment(minimum_span, lambda x: x >= 3600, "seconds"),
        "no_reported_frame_drops": _assessment(sum(v["dropped_records"] for v in camera_reports.values()) if frames else None, lambda x: x == 0, "frames"),
        "one_hour_duration": _assessment(duration, lambda x: x >= 3600, "seconds"),
        "mean_cpu_below_80_percent": _assessment(statistics.mean(cpus) if cpus else None, lambda x: x < 80, "percent"),
        "no_current_throttle_or_undervoltage": _assessment(any(x & 0xf for x in throttle) if throttle else None, lambda x: not x),
        "no_unexplained_sequence_gaps": _assessment(gaps, lambda x: x == 0, "frames"),
        "nominal_capture_rate": _assessment(fps_met, bool),
        "glass_to_glass_latency": _assessment(measurements.get("glass_to_glass_latency_p95_ms"), lambda x: x <= 1000, "milliseconds"),
        "exposure_skew": _assessment(measurements.get("exposure_skew_max_us"), lambda x: x <= 100, "microseconds"),
        "network_failure_preserves_recording": _assessment(measurements.get("network_failure_preserves_recording"), lambda x: x is True),
        "finalized_segments_readable": _assessment(measurements.get("finalized_segments_readable"), lambda x: x is True),
        "bounded_queues": _assessment(measurements.get("bounded_queues"), lambda x: x is True),
        "clean_capture_exit": _assessment(exits[-1].get("returncode") if exits else None, lambda x: x == 0)}
    # External measurements must name the method/evidence; booleans alone cannot qualify hardware.
    external = ("glass_to_glass_latency", "exposure_skew", "network_failure_preserves_recording", "finalized_segments_readable", "bounded_queues")
    for name in external:
        if checks[name]["value"] is not None and not measurements.get("evidence", {}).get(name):
            checks[name]["status"] = "unverified"
            checks[name]["note"] = "Supply the measurement method and evidence reference."
    return {"schema_version": 1, "session": str(session), "session_id": manifest.get("session_id"),
            "configuration": config, "duration_seconds": duration, "cameras": camera_reports,
            "health": {"samples": len(samples), "mean_cpu_percent": statistics.mean(cpus) if cpus else None,
                       "max_temperature_c": max(temperatures) if temperatures else None,
                       "min_cpu_clock_hz": min(clock_rates) if clock_rates else None,
                       "max_cpu_clock_hz": max(clock_rates) if clock_rates else None,
                       "min_memory_available_bytes": min(free_memory) if free_memory else None,
                       "throttled_bits_observed": sorted(set(throttle)),
                       "historical_throttle_or_undervoltage": any(x & 0xf0000 for x in throttle) if throttle else None},
            "final_outputs": native_ends[-1].get("outputs") if native_ends else native_samples[-1].get("outputs") if native_samples else None,
            "events": [r for r in health if r.get("type") == "event"],
            "checks": checks, "qualified": not problems and all(c["status"] == "met" for c in checks.values()),
            "problems": problems,
            "limitations": ["Capture timestamps are not independently measured exposure synchronization.",
                            "Board temperature and CPU samples describe only this run.",
                            "Calibration and browser interaction require their own measured evidence."]}


def markdown(report: dict[str, Any]) -> str:
    lines = [f"# Session {report.get('session_id') or Path(report['session']).name}", "",
             "Qualification evidence is complete." if report["qualified"] else "Qualification is incomplete or a measured target was missed.", "",
             "| Measurement | Value | Assessment |", "|---|---|---|"]
    for name, check in report["checks"].items():
        value = "unknown" if check["value"] is None else str(check["value"])
        if check["unit"]:
            value += " " + check["unit"]
        lines.append(f"| {name.replace('_', ' ')} | {value} | {check['status']} |")
    if report["problems"]:
        lines.extend(["", "Data issues:", ""] + [f"- {p}" for p in report["problems"]])
    lines.extend(["", *report["limitations"], ""])
    return "\n".join(lines)

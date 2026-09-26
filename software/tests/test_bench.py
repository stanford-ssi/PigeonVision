import json
from pathlib import Path

import pytest

from pigeonvision.bench import matrix, timestamp_us, validate_config
from pigeonvision.cli import main
from pigeonvision.report import summarize


def config(tmp_path):
    return {"session_dir": str(tmp_path / "session"), "cameras": [
        {"id": "A", "device": "/soc/camera0"}, {"id": "B", "device": "/soc/camera1"}]}


def test_shared_clock_retains_phase_offset():
    origin = 90_000_000_000
    a = timestamp_us(origin + 16_000_000, origin)
    b = timestamp_us(origin + 21_000_000, origin)
    assert b - a == 5000
    assert a == 16000
    with pytest.raises(ValueError, match="clock domain"):
        timestamp_us(origin - 1, origin)


def test_camera_identity_and_capture_only_validation(tmp_path):
    cfg = config(tmp_path)
    cfg["cameras"][1]["device"] = cfg["cameras"][0]["device"]
    with pytest.raises(ValueError, match="different physical"):
        validate_config(cfg)
    cfg = config(tmp_path) | {"encode": False}
    with pytest.raises(ValueError, match="Capture-only"):
        validate_config(cfg)


def test_matrix_is_distinct_and_streaming_explicit(tmp_path):
    configs = matrix(config(tmp_path), tmp_path, 1, "192.0.2.1:1234", ["ultrafast"])
    assert len(configs) == 18
    assert len({c["session_dir"] for c in configs}) == 18
    captures = [c for c in configs if not c["encode"]]
    assert len(captures) == 3
    assert all(not c["record"] and c["udp_destination"] is None for c in captures)
    local = matrix(config(tmp_path), tmp_path, 1, None, ["ultrafast"])
    assert len(local) == 12
    assert all(c["udp_destination"] is None for c in local)


def test_missing_measurements_never_qualify(tmp_path):
    report = summarize(tmp_path)
    assert report["qualified"] is False
    assert all(c["status"] == "unknown" for c in report["checks"].values())
    assert report["health"]["mean_cpu_percent"] is None


def test_report_gaps_drops_and_unknown_sync(tmp_path):
    cfg = config(tmp_path)
    (tmp_path / "capture-config.json").write_text(json.dumps(cfg))
    frames = [{"camera_id": camera, "sequence": seq,
               "sensor_timestamp_ns": seq * 33_333_333,
               "drop_reason": "queue_full" if seq == 1 else None}
              for camera in "AB" for seq in [0, 1, 3]]
    (tmp_path / "frames.jsonl").write_text("".join(json.dumps(f) + "\n" for f in frames))
    health = [{"type": "host_health", "cpu_percent": 70, "temperature_c": 60, "throttled_bits": 0},
              {"type": "capture_exit", "returncode": 0, "duration_seconds": 3601}]
    (tmp_path / "health.jsonl").write_text("".join(json.dumps(h) + "\n" for h in health))
    report = summarize(tmp_path)
    assert report["cameras"]["A"]["unexplained_sequence_gaps"] == 1
    assert report["cameras"]["A"]["dropped_records"] == 1
    assert report["checks"]["exposure_skew"]["status"] == "unknown"
    assert not report["qualified"]


def test_external_measurement_requires_evidence(tmp_path):
    (tmp_path / "measurements.json").write_text(json.dumps({"exposure_skew_max_us": 25}))
    report = summarize(tmp_path)
    assert report["checks"]["exposure_skew"]["status"] == "unverified"


def test_cli_config_smoke_and_missing_native_actionable(tmp_path, capsys):
    output = tmp_path / "config.json"
    assert main(["capture", "--camera-a", "actual0", "--write-config", str(output)]) == 0
    assert json.loads(output.read_text())["cameras"][0]["flip_y"] is True
    assert main(["capture", "--camera-a", "actual0", "--binary", "pv-nonexistent-binary"]) == 2
    assert "Build software/flight" in capsys.readouterr().err


def test_report_reads_native_health_without_wrapper(tmp_path):
    samples = [{"type": "health", "cpu_busy_percent": 40., "temperature_c": 53.,
                "cpu_clock_khz": 2400000, "memory_available_bytes": 2000000000,
                "throttled_bits": 0, "log_records_lost": 0,
                "cameras": [{"camera_id": "A", "queue_high_water": 2}],
                "outputs": {"transport": {"datagram_errors": 3}}}]
    (tmp_path / "health.jsonl").write_text(json.dumps(samples[0])+"\n")
    result = summarize(tmp_path)
    assert result["health"]["mean_cpu_percent"] == 40.
    assert result["health"]["min_cpu_clock_hz"] == 2400000000
    assert result["cameras"]["A"]["queue_high_water"] == 2
    assert result["final_outputs"]["transport"]["datagram_errors"] == 3
    assert not result["qualified"]


@pytest.mark.parametrize("name,value", [
    ("fps", 29.97), ("fps", True), ("fps", 0), ("fps", 121),
    ("segment_seconds", 60.5), ("segment_seconds", 0), ("segment_seconds", 86401),
    ("width", 14), ("width", 2066), ("height", 1554), ("height", 1551),
    ("bitrate", 9999), ("bitrate", 100000001), ("vbv_bits", 100000001),
    ("min_free_bytes", -1), ("min_free_bytes", True), ("min_free_bytes", 2**64),
    ("duration_seconds", None), ("duration_seconds", True), ("duration_seconds", float("inf")),
])
def test_capture_config_matches_native_scalar_limits(tmp_path, name, value):
    with pytest.raises(ValueError):
        validate_config(config(tmp_path) | {name: value})


def test_native_duration_zero_and_transport_headroom(tmp_path):
    assert validate_config(config(tmp_path) | {"duration_seconds": 0})["duration_seconds"] == 0
    assert validate_config(config(tmp_path) | {"duration_seconds": .5})["duration_seconds"] == .5
    with pytest.raises(ValueError, match="headroom"):
        validate_config(config(tmp_path) | {"udp_destination": "192.0.2.1:1234", "mux_bitrate": 8_499_999})
    assert validate_config(config(tmp_path) | {"udp_destination": "192.0.2.1:1234", "mux_bitrate": 8_500_000})


def test_capture_session_path_uses_execution_cwd_not_config_parent(tmp_path, monkeypatch):
    import sys
    execution = tmp_path / "execution"
    source = tmp_path / "configuration"
    execution.mkdir()
    source.mkdir()
    binary = tmp_path / "capture-test-binary"
    binary.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n")
    binary.chmod(0o755)
    path = source / "input.json"
    path.write_text(json.dumps(config(tmp_path) | {"session_dir": "relative-session"}))
    monkeypatch.chdir(execution)
    assert main(["capture", "--config", str(path), "--binary", str(binary)]) == 0
    written = execution / "relative-session" / "capture-config.json"
    assert json.loads(written.read_text())["session_dir"] == str((execution / "relative-session").resolve())
    assert not (source / "relative-session").exists()


def test_ssh_preserves_relative_path_for_remote_execution(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import pigeonvision.cli
    path = tmp_path / "remote.json"
    path.write_text(json.dumps(config(tmp_path) | {"session_dir": "output/remote-session"}))
    calls = []
    def remote_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(pigeonvision.cli.subprocess, "run", remote_run)
    assert main(["capture", "--config", str(path), "--ssh", "pi@pigeonvision.local"]) == 0
    assert calls[0][0][:3] == ["ssh", "--", "pi@pigeonvision.local"]
    assert json.loads(calls[0][1]["input"])["session_dir"] == "output/remote-session"


def test_exact_hour_includes_final_frame_period(tmp_path):
    cfg = validate_config(config(tmp_path))
    (tmp_path / "capture-config.json").write_text(json.dumps(cfg))
    rows = [{"camera_id": camera, "sequence": seq, "sensor_timestamp_ns": int(seq * 1_000_000_000 / 30), "drop_reason": None}
            for camera in "AB" for seq in (0, 107999)]
    (tmp_path / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "host-health.jsonl").write_text(json.dumps({"type": "capture_exit", "returncode": 0, "duration_seconds": 3600}) + "\n")
    report = summarize(tmp_path)
    assert report["checks"]["one_hour_camera_timestamps"]["status"] == "met"
    assert report["checks"]["one_hour_duration"]["status"] == "met"
    assert not report["qualified"]  # Sparse fixture is not continuous hardware evidence.
    rows[-1]["sensor_timestamp_ns"] -= 33_333_333
    (tmp_path / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert summarize(tmp_path)["checks"]["one_hour_camera_timestamps"]["status"] == "not_met"

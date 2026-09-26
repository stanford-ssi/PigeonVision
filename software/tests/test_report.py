"""Encoding evidence must not be inferred from sensor cadence or missing status."""
import json

import pytest

from pigeonvision.report import markdown, summarize


def session(tmp_path, rows, *, encode=True, health=None):
    (tmp_path / "session.json").write_text(json.dumps({"configuration": {
        "cameras": [{"id": "A"}, {"id": "B"}], "fps": 30, "encode": encode,
        "width": 1552, "height": 1552, "record": True, "udp_destination": "192.0.2.1:1234"}}))
    (tmp_path / "frames.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (tmp_path / "health.jsonl").write_text("".join(json.dumps(row) + "\n" for row in health or []))
    return summarize(tmp_path)


def timed_rows(status="encoded"):
    return [{"camera_id": camera, "sequence": sequence, "sensor_timestamp_ns": round(sequence * 1e9 / 30),
             "status": status, "drop_reason": None}
            for camera in "AB" for sequence in range(91)]


def test_capture_thirty_fps_does_not_claim_encoding_thirty_fps(tmp_path):
    rows = timed_rows()
    for row in rows:
        if row["sequence"] % 3 == 2:
            row.update(status="dropped", drop_reason="capture_queue_full" if row["sequence"] < 45 else "encoder_no_output")
    report = session(tmp_path, rows)
    for camera in report["cameras"].values():
        assert camera["observed_capture_fps"] == pytest.approx(30)
        assert camera["observed_encoded_fps"] == pytest.approx(20)
        assert camera["encoded_records"] == camera["encoded_timestamped_records"] == 61
        assert camera["drop_reason_counts"] == {"capture_queue_full": 15, "encoder_no_output": 15}
    assert report["checks"]["nominal_capture_rate"]["status"] == "met"
    assert report["checks"]["nominal_encode_rate"]["status"] == "not_met"
    assert report["checks"]["no_reported_frame_drops"] == {"value": 60, "unit": "frames", "status": "not_met"}
    assert not report["qualified"]
    text = markdown(report)
    assert "30.000 | 20.000 | 61" in text
    assert "capture_queue_full: 15" in text


@pytest.mark.parametrize("status", [None, "captured", "dropped"])
def test_nonencoded_or_absent_status_never_counts_as_encoded(tmp_path, status):
    rows = timed_rows(status)
    if status is None:
        for row in rows:
            del row["status"]
    report = session(tmp_path, rows)
    assert all(c["encoded_records"] == 0 and c["observed_encoded_fps"] is None for c in report["cameras"].values())
    assert report["checks"]["nominal_encode_rate"]["status"] == "unknown"
    assert not report["qualified"]


def test_encoding_requires_timestamps_and_both_configured_cameras(tmp_path):
    rows = timed_rows()
    for row in rows:
        if row["camera_id"] == "B":
            row["sensor_timestamp_ns"] = None
    report = session(tmp_path, rows)
    assert report["cameras"]["A"]["observed_encoded_fps"] == pytest.approx(30)
    assert report["cameras"]["B"]["encoded_records"] == 91
    assert report["cameras"]["B"]["observed_encoded_fps"] is None
    assert report["checks"]["nominal_encode_rate"]["status"] == "unknown"


def test_capture_only_does_not_assess_encoding_and_native_end_is_not_process_exit(tmp_path):
    report = session(tmp_path, timed_rows("captured"), encode=False,
                     health=[{"type": "session_end", "failed": False, "signal": 0, "outputs": {}}])
    assert report["checks"]["nominal_encode_rate"]["status"] == "unknown"
    assert report["checks"]["clean_capture_exit"]["status"] == "unknown"
    assert report["checks"]["one_hour_duration"]["status"] == "unknown"
    assert report["duration_seconds"] is None
    assert not report["qualified"]


def test_partially_missing_encoded_timestamps_cannot_qualify_rate(tmp_path):
    rows = timed_rows()
    rows[-1]["sensor_timestamp_ns"] = None
    report = session(tmp_path, rows)
    assert report["cameras"]["B"]["observed_encoded_fps"] == pytest.approx(30)
    assert report["cameras"]["B"]["encoded_timestamped_records"] == 90
    assert report["cameras"]["B"]["encoded_records"] == 91
    assert report["checks"]["nominal_encode_rate"]["status"] == "unknown"
    assert not report["qualified"]


def test_explicit_encoding_rate_and_wrapper_exit_are_separate_evidence(tmp_path):
    report = session(tmp_path, timed_rows(), health=[
        {"type": "capture_exit", "returncode": 0, "duration_seconds": 3.1}])
    assert report["checks"]["nominal_encode_rate"]["status"] == "met"
    assert report["checks"]["clean_capture_exit"]["status"] == "met"
    assert report["checks"]["one_hour_duration"]["status"] == "not_met"
    assert not report["qualified"]

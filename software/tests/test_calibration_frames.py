"""Offline collector checks use synthetic, explicitly labelled PyAV archives."""
import importlib.util
from fractions import Fraction
import json
from pathlib import Path

import av
import cv2
import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "calibration_frames.py"
spec = importlib.util.spec_from_file_location("calibration_frames", SCRIPT)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
ORIGIN = 900_000_000_000
BOARD = {"type": "checkerboard", "squares_x": 18, "squares_y": 18, "square_length_m": .02}


def write_json(path, value):
    path.write_text(json.dumps(value))


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def make_session(tmp_path, *, matrix=1):
    session = tmp_path / "synthetic-session"
    session.mkdir()
    board = tmp_path / "board.json"
    write_json(board, BOARD)
    cameras = [{"id": camera, "device": f"synthetic-{camera}", "flip_x": False, "flip_y": True} for camera in "AB"]
    manifest = {"schema_version": 1, "session_id": "synthetic-fixture", "clock_domain": "CLOCK_BOOTTIME", "clock_origin_ns": ORIGIN,
                "configuration": {"width": 2064, "height": 1552, "cameras": cameras},
                "hardware": {"cameras": [c | {"width": 2064, "height": 1552, "sensor_size": [2064, 1552],
                                             "requested_sensor_crop": [0, 0, 2064, 1552]} for c in cameras]}}
    rows, segments = [], []
    # Planar Y'CbCr bars are deliberately not a calibration target. Lossless
    # FFV1 preserves their code values; native production files contain H.264.
    codes = [(100, 140, 160), (180, 90, 150)]
    def expected_bgr(y, cb, cr):
        luma, blue, red = (y - 16) / 219, (cb - 128) / 224, (cr - 128) / 224
        return np.rint(np.clip(255 * np.array([
            luma + 1.8556 * blue,
            luma - .1873242729306488 * blue - .4681242729306488 * red,
            luma + 1.5748 * red]), 0, 255)).astype(np.uint8)
    pixels = np.tile(expected_bgr(*codes[0]), (1552, 2064, 1))
    pixels[:200] = expected_bgr(*codes[1])
    for camera, phase in (("A", 0), ("B", 10_000)):
        pts_values = [1_234_567 + phase, 1_267_900 + phase, 2_234_567 + phase]
        name = f"{camera}-000001.mkv"
        with av.open(str(session / name), "w", format="matroska") as container:
            stream = container.add_stream("ffv1", rate=30)
            stream.width, stream.height, stream.pix_fmt = 2064, 1552, "yuv420p"
            stream.codec_context.colorspace = matrix
            stream.codec_context.color_range = 1
            stream.codec_context.color_primaries = stream.codec_context.color_trc = 1
            stream.time_base = stream.codec_context.time_base = Fraction(1, 1_000_000)
            stream.metadata["title"] = camera
            for sequence, pts in enumerate(pts_values):
                frame = av.VideoFrame(2064, 1552, "yuv420p")
                frame.colorspace = matrix
                frame.color_range = 1
                for index, plane in enumerate(frame.planes):
                    data = np.full((plane.height, plane.line_size), codes[0][index], np.uint8)
                    data[:200 if index == 0 else 100] = codes[1][index]
                    plane.update(data.tobytes())
                frame.pts, frame.time_base = pts, Fraction(1, 1_000_000)
                for packet in stream.encode(frame):
                    container.mux(packet)
                rows.append({"schema_version": 1, "camera_id": camera, "sequence": sequence, "pts_us": pts,
                             "sensor_timestamp_ns": ORIGIN + pts * 1000 + 321, "status": "encoded", "drop_reason": None,
                             "sensor_crop": [0., 0., 2064., 1552.], "exposure_us": 1000})
            for packet in stream.encode():
                container.mux(packet)
        segments.append({"schema_version": 1, "camera_id": camera, "path": name, "index": 1,
                         "complete": True, "first_pts_us": pts_values[0], "last_pts_us": pts_values[-1]})
    write_json(session / "session.json", manifest)
    write_rows(session / "frames.jsonl", rows)
    write_rows(session / "segments.jsonl", segments)
    return session, board, manifest, rows, segments, pixels


def test_real_mkv_decode_retains_common_phase_metadata_orientation_and_pixels(tmp_path):
    source, board, _, rows, _, pixels = make_session(tmp_path)
    output = tmp_path / "candidates"
    report = collector.collect(source, board, output, interval_seconds=1, split="validation")
    dataset = json.loads((output / "dataset.json").read_text())
    assert report["accepted"] == {"A": 2, "B": 2}
    assert report["interval_skips"] == 2
    assert report["source_session_completion"] == "unknown"
    assert dataset["image_orientation"]["A"] == {"flip_x": False, "flip_y": True}
    assert dataset["physical_cameras"]["A"]["device"] == "synthetic-A"
    a, b = dataset["cameras"]["A"][0], dataset["cameras"]["B"][0]
    assert a["pts_us"] == 1_234_567
    assert b["pts_us"] - a["pts_us"] == 10_000
    assert a["capture_metadata"] == rows[0]
    assert abs(a["timestamp_quantization_error_us"]) <= 501
    assert a["split"] == "validation" and a["source_time_base"] == [1, 1000]
    actual = cv2.imread(str(output / a["path"]))
    # Chroma interpolation blends the two rows next to the colour-bar edge;
    # interiors retain the independently calculated colour and orientation.
    assert np.max(np.abs(actual[:198].astype(int) - pixels[:198])) <= 2
    assert np.max(np.abs(actual[202:].astype(int) - pixels[202:])) <= 2
    assert a["colour_conversion"]["source_matrix"] == "bt709"
    assert a["colour_conversion"]["source_range"] == "limited"
    assert a["colour_conversion"]["output_pixel_format"] == "bgr24"
    assert not (output / "calibration.json").exists()
    with pytest.raises(FileExistsError):
        collector.collect(source, board, output, interval_seconds=1, split="fit")


def test_partial_archive_reports_missing_incomplete_and_unindexed_segments(tmp_path):
    source, board, _, _, segments, _ = make_session(tmp_path)
    segments += [segments[0] | {"path": "A-000002.mkv", "index": 2}]
    segments[1]["complete"] = False
    (source / "A-000003.mkv").write_bytes(b"synthetic unindexed file, never decoded")
    write_rows(source / "segments.jsonl", segments)
    report = collector.collect(source, board, tmp_path / "candidates", interval_seconds=1, split="fit")
    assert report["missing_indexed_segments"] == ["A-000002.mkv"]
    assert report["incomplete_indexed_segments"] == ["B-000001.mkv"]
    assert report["unindexed_mkv_files"] == ["A-000003.mkv"]
    assert report["all_indexed_segments_present"] is False
    assert report["accepted"] == {"A": 2, "B": 0}


def test_camera_selection_never_decodes_unselected_archive(tmp_path, monkeypatch):
    source, board, _, _, _, _ = make_session(tmp_path)
    opened = []
    open_archive = collector.av.open
    def inspect_open(path, *args, **kwargs):
        opened.append(Path(path).name)
        return open_archive(path, *args, **kwargs)
    monkeypatch.setattr(collector.av, "open", inspect_open)
    output = tmp_path / "camera-b"
    report = collector.collect(source, board, output, interval_seconds=1, split="fit", camera="B")
    assert opened == ["B-000001.mkv"]
    assert report["selected_cameras"] == ["B"]
    assert report["accepted"] == {"A": 0, "B": 2}
    assert report["decoded_frames"] == 3
    assert report["camera_exclusions"] == {"A": "not_selected"}
    assert report["segments"][0]["excluded"] == "camera_not_selected"
    dataset = json.loads((output / "dataset.json").read_text())
    assert dataset["cameras"]["A"] == []
    assert dataset["cameras"]["B"][0]["pts_us"] == 1_244_567


def test_unknown_frame_crop_and_ambiguous_metadata_are_rejected(tmp_path):
    source, board, _, rows, _, _ = make_session(tmp_path)
    del rows[0]["sensor_crop"]
    duplicate = rows[-1] | {"sequence": 50}
    rows.append(duplicate)
    write_rows(source / "frames.jsonl", rows)
    report = collector.collect(source, board, tmp_path / "candidates", interval_seconds=1, split="fit")
    assert report["accepted"] == {"A": 1, "B": 1}
    assert report["rejection_counts"] == {"metadata_crop_unknown_or_mismatched": 1, "metadata_ambiguous": 1}


def test_non_native_colour_matrix_is_reported_and_no_png_is_saved(tmp_path):
    source, board, _, _, _, _ = make_session(tmp_path, matrix=5)
    output = tmp_path / "candidates"
    report = collector.collect(source, board, output, interval_seconds=1, split="fit")
    assert report["accepted"] == {"A": 0, "B": 0}
    assert report["rejection_counts"] == {"native_colour_metadata_frame_colorspace_expected_1_got_5": 4}
    assert not list(output.rglob("*.png"))


@pytest.mark.parametrize("change", ["dimensions", "flip", "device", "crop"])
def test_unknown_or_mismatched_manifest_geometry_excludes_camera(tmp_path, change):
    source, board, manifest, _, _, _ = make_session(tmp_path)
    camera = manifest["hardware"]["cameras"][0]
    if change == "dimensions":
        camera["width"] = 1552
    elif change == "flip":
        del camera["flip_y"]
    elif change == "device":
        camera["device"] = "synthetic-wrong-device"
    else:
        camera["requested_sensor_crop"] = [256, 0, 1552, 1552]
    write_json(source / "session.json", manifest)
    report = collector.collect(source, board, tmp_path / "candidates", interval_seconds=1, split="fit")
    assert "A" in report["camera_exclusions"]
    assert report["accepted"] == {"A": 0, "B": 2}


def test_real_detector_rejects_negative_board_without_saving_false_candidates(tmp_path, monkeypatch):
    source, board, _, _, _, pixels = make_session(tmp_path)
    observed = []
    detect = collector.BoardDetector.detect
    def inspect_detection(self, gray):
        observed.append(gray.copy())
        return detect(self, gray)
    monkeypatch.setattr(collector.BoardDetector, "detect", inspect_detection)
    output = tmp_path / "candidates"
    report = collector.collect(source, board, output, interval_seconds=1, split="fit", require_board=True)
    assert report["accepted"] == {"A": 0, "B": 0}
    assert report["rejection_counts"] == {"complete_board_not_detected": 4}
    assert not list(output.rglob("*.png"))
    assert len(observed) == 4
    expected = cv2.flip(cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY), 0)
    assert np.max(np.abs(observed[0].astype(int) - expected)) <= 2


def test_timestamp_match_is_quantization_limited_and_clock_checked():
    row = {"schema_version": 1, "camera_id": "A", "sequence": 1, "pts_us": 1_234_567, "sensor_timestamp_ns": ORIGIN + 1_234_567_000,
           "status": "encoded", "drop_reason": None, "sensor_crop": [0, 0, 2064, 1552]}
    index = collector.FrameMetadata([row], "A")
    assert index.match(Fraction(1_235_000), Fraction(1000), ORIGIN) == row
    with pytest.raises(ValueError, match="metadata_missing"):
        index.match(Fraction(1_236_000), Fraction(1000), ORIGIN)
    with pytest.raises(ValueError, match="clock_mismatch"):
        index.match(Fraction(1_235_000), Fraction(1000), ORIGIN + 2000)

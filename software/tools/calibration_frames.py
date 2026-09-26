#!/usr/bin/env python3
"""Extract original full-sensor calibration candidates from finalized native MKVs.

Run with software/.venv/bin/python. This is offline collection, never a lens fit.
PNG preserves decoded pixels and capture flips; H.264 compression is not undone.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

import av
import cv2

from pigeonvision.calibration import BoardDetector


SIZE = [2064, 1552]
FULL_CROP = [0, 0, *SIZE]


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_rows(path: Path, problems: list[str], camera: str | None = None) -> list[dict]:
    rows = []
    with path.open() as source:
        for number, line in enumerate(source, 1):
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("expected an object")
                if camera is None or row.get("camera_id") == camera:
                    rows.append(row)
            except ValueError as exc:
                problems.append(f"{path.name}:{number}: {exc}")
    return rows


def full_crop(value) -> bool:
    return isinstance(value, list) and len(value) == 4 and all(
        type(actual) in (int, float) and math.isfinite(actual) and abs(actual - expected) <= 1e-6
        for actual, expected in zip(value, FULL_CROP))


def camera_geometry(manifest: dict, camera_id: str) -> dict:
    configured = [c for c in manifest.get("configuration", {}).get("cameras", []) if c.get("id") == camera_id]
    actual = [c for c in manifest.get("hardware", {}).get("cameras", []) if c.get("id") == camera_id]
    if len(configured) != 1 or len(actual) != 1:
        raise ValueError("missing or ambiguous physical camera description")
    config, camera = configured[0], actual[0]
    if not isinstance(camera.get("device"), str) or not camera["device"] or config.get("device") != camera["device"]:
        raise ValueError("missing or mismatched physical device ID")
    if camera.get("sensor_size") != SIZE or [camera.get("width"), camera.get("height")] != SIZE:
        raise ValueError("negotiated geometry is not full 2064x1552")
    if [manifest["configuration"].get("width"), manifest["configuration"].get("height")] != SIZE:
        raise ValueError("configured output is not full 2064x1552")
    for key in ("flip_x", "flip_y"):
        if type(camera.get(key)) is not bool or config.get(key) is not camera[key]:
            raise ValueError("unknown or mismatched capture flips")
    if not full_crop(camera.get("requested_sensor_crop")):
        raise ValueError("requested full-sensor crop is unknown or mismatched")
    return camera


class FrameMetadata:
    def __init__(self, rows: list[dict], camera_id: str):
        selected = [r for r in rows if r.get("camera_id") == camera_id and type(r.get("pts_us")) is int]
        self.rows = sorted(selected, key=lambda r: r["pts_us"])
        self.times = [r["pts_us"] for r in self.rows]

    def match(self, pts: Fraction, tick: Fraction, origin: int) -> dict:
        # Native av_packet_rescale_ts rounds to the MKV timebase. Allow half a
        # tick plus one microsecond for the sensor-to-microsecond truncation.
        tolerance = tick / 2 + 1
        first, last = bisect_left(self.times, pts - tolerance), bisect_right(self.times, pts + tolerance)
        if last - first != 1:
            raise ValueError("metadata_missing" if last == first else "metadata_ambiguous")
        row = self.rows[first]
        if row.get("schema_version") != 1:
            raise ValueError("metadata_schema_unknown")
        if row.get("status") != "encoded" or row.get("drop_reason") is not None:
            raise ValueError("metadata_not_encoded")
        timestamp = row.get("sensor_timestamp_ns")
        if type(timestamp) is not int or timestamp < origin or (timestamp - origin) // 1000 != row["pts_us"]:
            raise ValueError("metadata_clock_mismatch")
        if type(row.get("sequence")) is not int or row["sequence"] < 0:
            raise ValueError("metadata_sequence_unknown")
        if not full_crop(row.get("sensor_crop")):
            raise ValueError("metadata_crop_unknown_or_mismatched")
        return row


def collect(session: Path, board_path: Path, output: Path, *, interval_seconds: float,
            split: str, require_board: bool = False, region: str = "unlabelled", camera: str | None = None) -> dict:
    if not math.isfinite(interval_seconds) or interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive and finite")
    if split not in ("fit", "validation"):
        raise ValueError("split must explicitly be fit or validation")
    if camera not in (None, "A", "B"):
        raise ValueError("camera must be A or B when specified")
    selected = [camera] if camera else ["A", "B"]
    session, output = session.resolve(), output.resolve()
    manifest = json.loads((session / "session.json").read_text())
    origin = manifest.get("clock_origin_ns")
    if manifest.get("schema_version") != 1 or manifest.get("clock_domain") != "CLOCK_BOOTTIME" or type(origin) is not int or origin < 0:
        raise ValueError("A version-1 session with its original CLOCK_BOOTTIME origin is required")
    board_document = json.loads(board_path.read_text())
    board = board_document.get("board", board_document)
    detector = BoardDetector(board)
    if detector.type != "checkerboard" or detector.pattern_size != (17, 17):
        raise ValueError("This collector requires the complete 17x17 inner-corner checkerboard (18x18 squares)")
    problems: list[str] = []
    rows = read_rows(session / "frames.jsonl", problems, camera)
    segments = read_rows(session / "segments.jsonl", problems)
    report = {"schema_version": 1, "kind": "calibration_candidates_not_a_fit", "source_session": str(session),
              "source_session_id": manifest.get("session_id"), "clock_domain": manifest["clock_domain"],
              "clock_origin_ns": origin, "interval_seconds": interval_seconds, "split": split,
              "selected_cameras": selected,
              "board_filter": "complete_17x17" if require_board else "not_requested", "problems": problems,
              "segments": [], "missing_indexed_segments": [], "incomplete_indexed_segments": [],
              "camera_exclusions": {}, "decoded_frames": 0, "interval_skips": 0, "accepted": {"A": 0, "B": 0},
              "rejections": [], "source_session_completion": "unknown",
              "limitations": ["Candidates require manual pose-diversity and held-out selection; no calibration was fitted.",
                              "Decoded PNGs retain H.264 compression history and recorded orientation.",
                              "Matched common timestamps do not establish simultaneous exposures.",
                              *detector.warnings]}
    dataset = {"schema_version": 1, "board": board, "image_orientation": {}, "cameras": {"A": [], "B": []},
               "physical_cameras": {}, "collection_status": "candidates_require_manual_review",
               "provenance": {"session_json_sha256": digest(session / "session.json"),
                              "frames_jsonl_sha256": digest(session / "frames.jsonl"),
                              "segments_jsonl_sha256": digest(session / "segments.jsonl"),
                              "board_json_sha256": digest(board_path), "source_session": str(session)}}
    geometry = {}
    for camera_id in ("A", "B"):
        if camera_id not in selected:
            report["camera_exclusions"][camera_id] = "not_selected"
            dataset["image_orientation"][camera_id] = {"flip_x": None, "flip_y": None}
            continue
        try:
            geometry[camera_id] = camera_geometry(manifest, camera_id)
            dataset["image_orientation"][camera_id] = {key: geometry[camera_id][key] for key in ("flip_x", "flip_y")}
            dataset["physical_cameras"][camera_id] = geometry[camera_id]
        except ValueError as exc:
            report["camera_exclusions"][camera_id] = str(exc)
            dataset["image_orientation"][camera_id] = {"flip_x": None, "flip_y": None}
    metadata = {camera_id: FrameMetadata(rows, camera_id) for camera_id in geometry}
    indexed_names = [s.get("path") for s in segments]
    duplicates = {name for name, count in Counter(name for name in indexed_names if isinstance(name, str)).items() if count > 1}
    report["unindexed_mkv_files"] = sorted(p.name for p in session.glob("*.mkv") if p.name not in indexed_names)
    output.mkdir(parents=True, exist_ok=False)
    interval_us = Fraction(str(interval_seconds)) * 1_000_000
    last_bin = {camera_id: None for camera_id in geometry}
    seen = set()
    try:
        for segment in sorted(segments, key=lambda s: (str(s.get("camera_id")), s.get("first_pts_us") if type(s.get("first_pts_us")) is int else -1)):
            name, camera_id = segment.get("path"), segment.get("camera_id")
            info = {"index_record": segment, "accepted": 0}
            report["segments"].append(info)
            if segment.get("schema_version") != 1 or not isinstance(name, str) or Path(name).name != name or not name.endswith(".mkv"):
                info["excluded"] = "invalid_segment_path"
                continue
            source = session / name
            if source.resolve().parent != session:
                info["excluded"] = "segment_path_outside_session"
                continue
            if not source.is_file():
                report["missing_indexed_segments"].append(name)
                info["excluded"] = "missing_indexed_segment"
                continue
            if segment.get("complete") is not True:
                report["incomplete_indexed_segments"].append(name)
                info["excluded"] = "segment_not_finalized"
                continue
            if camera_id not in selected:
                info["excluded"] = "camera_not_selected"
                continue
            if name in duplicates or camera_id not in geometry:
                info["excluded"] = "duplicate_index" if name in duplicates else "camera_geometry_excluded"
                continue
            start, end = segment.get("first_pts_us"), segment.get("last_pts_us")
            if type(start) is not int or type(end) is not int or not 0 <= start <= end:
                info["excluded"] = "invalid_segment_time_range"
                continue
            try:
                with av.open(str(source)) as container:
                    if len(container.streams.video) != 1:
                        raise ValueError("segment must contain exactly one video stream")
                    stream = container.streams.video[0]
                    if stream.metadata.get("title") != camera_id:
                        raise ValueError("video stream title does not match indexed camera ID")
                    info["source_sha256"] = digest(source)
                    for frame in container.decode(stream):
                        report["decoded_frames"] += 1
                        rejection = {"segment": name, "camera_id": camera_id, "frame_pts": frame.pts}
                        try:
                            if frame.pts is None or frame.time_base is None or frame.time_base <= 0:
                                raise ValueError("frame_timestamp_unknown")
                            pts = frame.pts * frame.time_base * 1_000_000
                            tick = frame.time_base * 1_000_000
                            if not start - tick / 2 - 1 <= pts <= end + tick / 2 + 1:
                                raise ValueError("frame_outside_indexed_time_range")
                            if [frame.width, frame.height] != SIZE:
                                raise ValueError("decoded_dimensions_not_full_sensor")
                            sample_bin = pts // interval_us
                            if last_bin[camera_id] is not None and sample_bin <= last_bin[camera_id]:
                                report["interval_skips"] += 1
                                continue
                            last_bin[camera_id] = sample_bin
                            row = metadata[camera_id].match(pts, tick, origin)
                            identity = (camera_id, row["sequence"], row["pts_us"])
                            if identity in seen:
                                raise ValueError("duplicate_source_frame")
                            seen.add(identity)
                            pixels = frame.to_ndarray(format="bgr24")
                            detection = None
                            if require_board:
                                gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
                                for key, axis in (("flip_x", 1), ("flip_y", 0)):
                                    if geometry[camera_id][key]:
                                        gray = cv2.flip(gray, axis)
                                detection = detector.detect(gray)
                                if detection is None:
                                    raise ValueError("complete_board_not_detected")
                            relative = Path("images") / camera_id / f"{split}-{row['sequence']:010d}-{row['pts_us']}.png"
                            destination = output / relative
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            success, png = cv2.imencode(".png", pixels)
                            if not success:
                                raise OSError("PNG encoding failed")
                            with destination.open("xb") as target:
                                target.write(png.tobytes())
                            record = {"path": relative.as_posix(), "split": split, "region": region,
                                      "sha256": digest(destination), "device_id": geometry[camera_id]["device"],
                                      "source_segment": name, "source_segment_sha256": info["source_sha256"],
                                      "source_frame_pts": frame.pts,
                                      "source_time_base": [frame.time_base.numerator, frame.time_base.denominator],
                                      "decoded_pts_us": float(pts), "pts_us": row["pts_us"],
                                      "timestamp_quantization_error_us": float(pts - row["pts_us"]),
                                      "capture_metadata": row, "board_corner_count": len(detection[2]) if detection is not None else None}
                            dataset["cameras"][camera_id].append(record)
                            report["accepted"][camera_id] += 1
                            info["accepted"] += 1
                        except ValueError as exc:
                            rejection["reason"] = str(exc)
                            report["rejections"].append(rejection)
            except (av.FFmpegError, ValueError, OSError) as exc:
                info["error"] = str(exc)
                problems.append(f"{name}: {exc}")
    finally:
        report["all_indexed_segments_present"] = not report["missing_indexed_segments"]
        report["rejection_counts"] = dict(Counter(r["reason"] for r in report["rejections"]))
        for name, value in (("dataset.json", dataset), ("collection-report.json", report)):
            with (output / name).open("x") as target:
                target.write(json.dumps(value, indent=2, allow_nan=False) + "\n")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--board", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=float, required=True, help="Sample one candidate per common-timeline interval")
    parser.add_argument("--split", choices=("fit", "validation"), required=True)
    parser.add_argument("--camera", choices=("A", "B"), help="Process only this camera; default processes both")
    parser.add_argument("--region", default="unlabelled", help="Operator-supplied region; inspect pose diversity afterward")
    parser.add_argument("--require-board", action="store_true", help="Keep only a complete 17x17 inner-corner detection")
    args = parser.parse_args(argv)
    try:
        report = collect(args.session, args.board, args.output, interval_seconds=args.interval_seconds,
                         split=args.split, require_board=args.require_board, region=args.region, camera=args.camera)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"calibration collection: {exc}\n")
    print(json.dumps({"output": str(args.output), "accepted": report["accepted"],
                      "selected_cameras": report["selected_cameras"],
                      "missing_indexed_segments": report["missing_indexed_segments"],
                      "camera_exclusions": report["camera_exclusions"], "problems": report["problems"]}, indent=2))
    return 0 if sum(report["accepted"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

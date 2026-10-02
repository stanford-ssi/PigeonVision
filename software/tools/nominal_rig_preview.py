#!/usr/bin/env python3
"""Combine real Mei lens fits with explicitly nominal opposed rig geometry.

This creates a viewer preview, never measured rig alignment or qualification.
Run with software/.venv/bin/python; existing output directories are refused.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date
import hashlib
import json
import math
from pathlib import Path

from pigeonvision.calibration import _intrinsics_camera, _validate_intrinsics_source
from pigeonvision.ground.projection import validate_calibration


SIZE = [2064, 1552]
ROTATIONS = {"A": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "B": [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]}


def fingerprint(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def read_lens(path: Path, camera_id: str, dataset_paths: list[Path]) -> tuple[dict, dict, dict]:
    source = json.loads(path.read_text())
    if source.get("schema_version") != 1 or source.get("model") != "mei" or source.get("intrinsic_coordinate_convention") != "unflipped_full_sensor_pixel_centres":
        raise ValueError(f"{camera_id}: expected canonical version-1 Mei lens measurements")
    flat = "cameras" not in source
    if flat:
        if source.get("camera_id") != camera_id or source.get("kind") != "training_only_intrinsics_diagnostic":
            raise ValueError(f"{camera_id}: unsupported flat diagnostic or mismatched camera ID")
        lens = copy.deepcopy(source)
        validation = {"status": "unvalidated_missing_or_insufficient_held_out",
                      "held_out_views": 0, "held_out_status": source.get("held_out_status"),
                      "training": copy.deepcopy(source.get("training")), "fit_views": source.get("fit_accepted_views"),
                      "held_out": {"rms_px": None, "p95_px": None}, "thresholds_met": None,
                      "seam_thresholds_met": None, "seam_coverage_measured": False}
    else:
        if camera_id not in source["cameras"]:
            raise ValueError(f"{camera_id}: lens is missing from diagnostic")
        lens = copy.deepcopy(source["cameras"][camera_id])
        validation = copy.deepcopy(source.get("validation", {}).get("cameras", {}).get(camera_id, {}))
        if not validation:
            raise ValueError(f"{camera_id}: lens validation state is missing")
    if lens.get("image_size") != SIZE:
        raise ValueError(f"{camera_id}: lens fit must use full 2064x1552 sensor coordinates")
    if not dataset_paths:
        if not source.get("source_dataset"):
            raise ValueError(f"{camera_id}: supply source datasets to establish physical identity and orientation")
        dataset_paths = [Path(source["source_dataset"])]
    datasets = {p.resolve(): json.loads(p.read_text()) for p in dataset_paths}
    verified_fit_dataset = None
    if source.get("source_dataset"):
        original = Path(source["source_dataset"]).resolve()
        if original not in datasets or source.get("provenance", {}).get("dataset_sha256") != fingerprint(original)["sha256"]:
            raise ValueError(f"{camera_id}: fitted source dataset/hash does not match supplied dataset")
        verified_fit_dataset = str(original)
    physical, orientation = None, None
    for dataset_path, dataset in datasets.items():
        camera = _intrinsics_camera(dataset, camera_id)
        current_orientation = dataset["image_orientation"][camera_id]
        if camera["image_size"] != SIZE or current_orientation != {"flip_x": False, "flip_y": True}:
            raise ValueError(f"{camera_id}: source geometry differs from the full-sensor native FlipY profile")
        if physical and camera["provenance"]["device_id"] != physical["provenance"]["device_id"]:
            raise ValueError(f"{camera_id}: datasets identify different physical cameras")
        physical, orientation = camera, current_orientation
        for record in dataset.get("cameras", {}).get(camera_id, []):
            _validate_intrinsics_source(record, camera_id, camera, current_orientation)
    if flat:
        views = source.get("views", [])
        if not views:
            raise ValueError(f"{camera_id}: flat diagnostic has no source views to bind its identity")
        for view in views:
            dataset_path = Path(view["source_dataset"]).resolve()
            dataset = datasets.get(dataset_path)
            if dataset is None or not any(record.get("path") == view.get("path") for record in dataset.get("cameras", {}).get(camera_id, [])):
                raise ValueError(f"{camera_id}: diagnostic view is not present in its supplied source dataset")
    existing_id = lens.get("provenance", {}).get("device_id")
    if existing_id is not None and existing_id != physical["provenance"]["device_id"]:
        raise ValueError(f"{camera_id}: fitted lens physical ID differs from source dataset")
    result = {key: copy.deepcopy(lens[key]) for key in ("K", "D", "xi", "image_size")}
    result.update(crop=[0, 0, *SIZE], output_size=SIZE.copy(), **orientation,
                  R_camera_from_rig=ROTATIONS[camera_id], valid_radius_px=lens.get("valid_radius_px"),
                  max_theta_deg=lens.get("max_theta_deg") if validation.get("held_out_views", 0) >= 3 else None,
                  provenance=copy.deepcopy(lens.get("provenance", physical["provenance"])))
    result["provenance"]["device_id"] = physical["provenance"]["device_id"]
    provenance = {"intrinsics": fingerprint(path), "datasets": [fingerprint(p) for p in datasets],
                  "source_kind": source.get("kind", source.get("scope")),
                  "source_validation_status": source.get("validation", {}).get("status", source.get("held_out_status")),
                  "source_limitations": source.get("limitations", [])}
    provenance["source_binding"] = {
        "method": "fit_time_dataset_sha256_verified" if verified_fit_dataset else
                  "source_view_membership_only" if flat else "physical_identity_only",
        "fit_time_dataset_hash_verified": verified_fit_dataset is not None,
        "verified_fit_dataset": verified_fit_dataset,
        "fingerprint_scope": "files_at_preview_generation",
        "note": "The source dataset matches the hash recorded when the lens was fitted." if verified_fit_dataset else
                "Current source membership/identity is checked; these fingerprints do not prove the files are unchanged since fitting."
    }
    return result, validation, provenance


def generate(intrinsics_a: Path, intrinsics_b: Path, output: Path, *, baseline_m: float,
             geometry_source_date: str, opposed: bool, common_roll_assumed: bool,
             datasets_a: list[Path] | None = None, datasets_b: list[Path] | None = None) -> dict:
    if opposed is not True or common_roll_assumed is not True:
        raise ValueError("Explicit opposed-axis and common-roll assumptions are required for this nominal preview")
    if type(baseline_m) not in (int, float) or not math.isfinite(baseline_m) or baseline_m <= 0:
        raise ValueError("baseline_m must be a positive finite nominal separation")
    date.fromisoformat(geometry_source_date)
    bundle = {"schema_version": 1, "model": "mei", "scope": "nominal_preview",
              "rig_alignment_status": "nominal_operator_geometry", "qualified": False,
              "rig_axes": "+Z forward through A, +X right, +Y down",
              "intrinsic_coordinate_convention": "unflipped_full_sensor_pixel_centres",
              "nominal_geometry": {"source": "operator supplied approximate mounting geometry",
                  "source_date": geometry_source_date, "opposed_angle_deg": 180, "baseline_m": baseline_m,
                  "baseline_status": "nominal_approximate", "translation_used_in_projection": False,
                  "common_roll_assumption": "Camera +Y axes are parallel; relative roll is assumed zero, not measured."},
              "validation": {"status": "nominal_preview_unqualified", "cameras": {}}, "cameras": {},
              "provenance": {}, "limitations": [
                  "Opposed alignment and common roll are operator assumptions, not a precision rig solve.",
                  "The nominal baseline is recorded only; infinity projection does not correct near-field parallax.",
                  "Lens validation states remain those of the supplied fits; training errors are not held-out validation.",
                  "Missing angular bounds mean edge and seam coverage remain unvalidated."]}
    for camera_id, path, datasets in (("A", intrinsics_a, datasets_a), ("B", intrinsics_b, datasets_b)):
        lens, validation, provenance = read_lens(path, camera_id, datasets or [])
        bundle["cameras"][camera_id] = lens
        bundle["validation"]["cameras"][camera_id] = validation
        bundle["provenance"][camera_id] = provenance
    if bundle["cameras"]["A"]["provenance"]["device_id"] == bundle["cameras"]["B"]["provenance"]["device_id"]:
        raise ValueError("A and B must identify distinct physical cameras")
    validate_calibration(bundle)
    output.mkdir(parents=True, exist_ok=False)
    with (output / "calibration.json").open("x") as destination:
        destination.write(json.dumps(bundle, indent=2, allow_nan=False) + "\n")
    return bundle


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intrinsics-a", type=Path, required=True)
    parser.add_argument("--intrinsics-b", type=Path, required=True)
    parser.add_argument("--dataset-a", type=Path, action="append", default=[])
    parser.add_argument("--dataset-b", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-m", type=float, required=True)
    parser.add_argument("--geometry-source-date", required=True)
    parser.add_argument("--opposed", action="store_true", required=True)
    parser.add_argument("--common-roll-assumed", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        generate(args.intrinsics_a, args.intrinsics_b, args.output, baseline_m=args.baseline_m,
                 geometry_source_date=args.geometry_source_date, opposed=args.opposed,
                 common_roll_assumed=args.common_roll_assumed, datasets_a=args.dataset_a, datasets_b=args.dataset_b)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"nominal preview: {exc}\n")
    print(args.output / "calibration.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

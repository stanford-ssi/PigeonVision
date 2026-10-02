"""Offline, fixed-lens alignment from explicitly identified shared board corners.

This writes an evidence report, never a viewer calibration. See
software/calibration/rig-alignment.md for the intentionally explicit input schema.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix
from scipy.spatial.transform import Rotation

from .calibration import BoardDetector, _fit_board_pose, _intrinsics_camera, _validate_intrinsics_source, error_stats


CONVENTION = "unflipped_full_sensor_pixel_centres"
MIN_FIT = 8
MIN_VALIDATION_PER_SEAM = 3


def _hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def _lens(document: dict, camera_id: str, geometry: dict) -> dict:
    if document.get("schema_version") != 1 or document.get("model") != "mei" or document.get("intrinsic_coordinate_convention") != CONVENTION:
        raise ValueError("Intrinsics must be a version-1 Mei bundle in canonical full-sensor coordinates.")
    camera = document.get("cameras", {}).get(camera_id, {})
    K, D = np.asarray(camera.get("K"), dtype=float), np.asarray(camera.get("D"), dtype=float)
    xi = camera.get("xi")
    if K.shape != (3, 3) or D.shape != (4,) or not np.isfinite(K).all() or not np.isfinite(D).all():
        raise ValueError("Each lens needs finite K[3,3] and D[4].")
    if type(xi) not in (float, int) or not np.isfinite(xi) or K[0, 0] <= 0 or K[1, 1] <= 0 or not np.allclose(K[2], [0, 0, 1]):
        raise ValueError("Invalid Mei lens parameters.")
    if camera.get("image_size") != geometry["image_size"] or camera.get("provenance", {}).get("device_id") != geometry["provenance"]["device_id"]:
        raise ValueError("Lens calibration dimensions/device do not match the selected physical camera.")
    return {"K": K, "D": D, "xi": float(xi)}


def _project(points: np.ndarray, lens: dict) -> tuple[np.ndarray, bool]:
    lengths = np.linalg.norm(points, axis=1)
    unit = points / np.maximum(lengths[:, None], 1e-15)
    valid = (lengths > 1e-9) & (unit[:, 2] + lens["xi"] > 1e-9)
    if lens["xi"] > 1:
        valid &= unit[:, 2] > -1 / lens["xi"]
    projected, _ = cv2.omnidir.projectPoints(np.ascontiguousarray(points.reshape(-1, 1, 3)),
                                           np.zeros(3), np.zeros(3), lens["K"], lens["xi"], lens["D"])
    projected = projected.reshape(-1, 2)
    valid &= np.isfinite(projected).all(axis=1)
    # Keep numerical trial steps finite; an invalid final pose cannot qualify.
    projected[~valid] = 1e6
    return projected, bool(valid.all())


def _errors(obj: np.ndarray, pair: dict, lenses: dict, transform: np.ndarray, pose: np.ndarray):
    board_rotation, relative_rotation = cv2.Rodrigues(pose[:3])[0], cv2.Rodrigues(transform[:3])[0]
    xyz_a = obj @ board_rotation.T + pose[3:]
    xyz_b = xyz_a @ relative_rotation.T + transform[3:]
    pa, va = _project(xyz_a, lenses["A"])
    pb, vb = _project(xyz_b, lenses["B"])
    center_b = -relative_rotation.T @ transform[3:]
    normal = board_rotation[:, 2]
    same_face = (normal @ pose[3:]) * (normal @ (pose[3:] - center_b)) > 1e-12
    return {"A": pa - pair["points"]["A"], "B": pb - pair["points"]["B"]}, va and vb and bool(same_face)


def _residual(obj, pair, lenses, transform, pose):
    errors, _ = _errors(obj, pair, lenses, transform, pose)
    return np.r_[errors["A"].ravel(), errors["B"].ravel()]


def _pose(obj, image, lens):
    rvec, tvec, _, _ = _fit_board_pose(cv2, obj.reshape(-1, 1, 3), image, lens["K"], lens["D"], lens["xi"])
    return np.r_[rvec, tvec]


def _relative(a, b):
    rotation = cv2.Rodrigues(b[:3])[0] @ cv2.Rodrigues(a[:3])[0].T
    return np.r_[cv2.Rodrigues(rotation)[0].ravel(), b[3:] - rotation @ a[3:]]


def _load_pair(record, base, geometry, orientations, count, anchors, max_skew_us):
    for key in ("pair_id", "pose_group", "seam"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f"Pair needs a nonempty {key}.")
    if record.get("split") not in ("fit", "validation") or record.get("stationary_hold_confirmed") is not True:
        raise ValueError("Declare fit/validation split and confirm a stationary shared-board hold.")
    confirmation = record.get("correspondence", {})
    if not isinstance(confirmation, dict) or confirmation.get("operator_confirmed") is not True or confirmation.get("same_printed_face") is not True or not isinstance(confirmation.get("note"), str) or not confirmation["note"].strip():
        raise ValueError("Explicit operator confirmation of O/X/Y identities and the same printed face is required.")
    if not isinstance(record.get("frames"), dict):
        raise ValueError("Pair needs frames.A and frames.B records.")
    pair = {"record": record, "points": {}, "frames": {}}
    for name in ("A", "B"):
        frame = record.get("frames", {}).get(name, {})
        if not isinstance(frame, dict) or not isinstance(frame.get("path"), str) or not frame["path"]:
            raise ValueError(f"{name}: an original image path and frame record are required.")
        if frame.get("image_orientation") != orientations[name]:
            raise ValueError(f"{name}: frame capture orientation must be explicit and match the physical camera.")
        _validate_intrinsics_source(frame, name, geometry[name], orientations[name])
        if "source_provenance" in frame:
            raise ValueError("Rig pairing requires native original timestamps; manual still provenance is insufficient.")
        path = (base / frame["path"]).resolve()
        digest = _hash(path)
        if frame.get("sha256") != digest:
            raise ValueError(f"{name}: original image SHA-256 is missing or mismatched.")
        gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if gray is None or list(gray.shape[::-1]) != geometry[name]["image_size"]:
            raise ValueError(f"{name}: original image is unreadable or not full sensor size.")
        metadata = frame["capture_metadata"]
        if metadata.get("schema_version") != 1:
            raise ValueError(f"{name}: native capture metadata must declare schema_version 1.")
        origin, timestamp, pts = frame.get("clock_origin_ns"), metadata.get("sensor_timestamp_ns"), metadata.get("pts_us")
        if frame.get("clock_domain") != "CLOCK_BOOTTIME" or type(origin) is not int or origin < 0 or type(timestamp) is not int or timestamp < origin or type(pts) is not int or (timestamp - origin) // 1000 != pts:
            raise ValueError(f"{name}: original BOOTTIME sensor timestamp/PTS is missing or inconsistent.")
        if "pts_us" in frame and frame["pts_us"] != pts:
            raise ValueError(f"{name}: frame PTS contradicts original capture metadata.")
        if not isinstance(frame.get("session_id"), str) or not frame["session_id"] or type(metadata.get("sequence")) is not int or metadata["sequence"] < 0:
            raise ValueError(f"{name}: session and original frame sequence are required.")
        if metadata.get("status") != "encoded" or metadata.get("drop_reason") is not None:
            raise ValueError(f"{name}: source metadata does not identify an encoded frame.")
        ids, points = frame.get("corner_ids"), np.asarray(frame.get("image_points"), dtype=float)
        if not isinstance(ids, list) or len(ids) != count or any(type(i) is not int for i in ids) or sorted(ids) != list(range(count)):
            raise ValueError(f"{name}: provide every physical inner-corner ID exactly once; detector-local IDs are not correspondence.")
        if points.shape != (count, 2) or not np.isfinite(points).all():
            raise ValueError(f"{name}: provide finite canonical image_points[N,2].")
        points = points[np.argsort(ids)]
        width, height = geometry[name]["image_size"]
        if np.any(points < 0) or np.any(points[:, 0] >= width) or np.any(points[:, 1] >= height):
            raise ValueError(f"{name}: canonical points lie outside the full sensor.")
        for label, ident in anchors.items():
            clicked = np.asarray(frame.get("anchor_pixels", {}).get(label), dtype=float)
            if clicked.shape != (2,) or not np.isfinite(clicked).all() or np.linalg.norm(clicked - points[ident]) > 3:
                raise ValueError(f"{name}: physical {label} anchor conflicts with corner IDs; resolve checkerboard symmetry.")
        pair["points"][name] = points
        pair["frames"][name] = {"path": str(path), "sha256": digest, "device_id": frame["device_id"],
                                "session_id": frame["session_id"], "clock_origin_ns": origin,
                                "sensor_timestamp_ns": timestamp, "pts_us": pts, "sequence": metadata["sequence"]}
    a, b = pair["frames"]["A"], pair["frames"]["B"]
    if a["session_id"] != b["session_id"] or a["clock_origin_ns"] != b["clock_origin_ns"]:
        raise ValueError("A/B must retain one session and common clock origin; independently rebased streams cannot be paired.")
    pair["skew_us"] = (b["sensor_timestamp_ns"] - a["sensor_timestamp_ns"]) / 1000
    if abs(pair["skew_us"]) > max_skew_us:
        raise ValueError("Pair exceeds the declared original sensor-timestamp skew tolerance.")
    return pair


def calibrate_rig(dataset_path: Path, output: Path) -> dict:
    """Fit a rigid A-to-B transform with frozen lenses; preserve every rejection."""
    dataset_path, output = Path(dataset_path), Path(output)
    dataset = json.loads(dataset_path.read_text())
    if dataset.get("schema_version") != 1 or dataset.get("kind") != "rig_alignment_observations" or dataset.get("coordinate_convention") != CONVENTION:
        raise ValueError("Expected version-1 rig_alignment_observations in canonical full-sensor coordinates.")
    if dataset.get("evidence_kind") not in ("camera_capture", "synthetic_fixture"):
        raise ValueError("Declare camera_capture or synthetic_fixture evidence_kind.")
    if dataset.get("assembly_unchanged_confirmed") is not True:
        raise ValueError("Confirm lens focus and rigid camera mounting have not changed since intrinsic collection.")
    if not isinstance(dataset.get("pairs"), list) or any(not isinstance(pair, dict) for pair in dataset["pairs"]):
        raise ValueError("pairs must be a list of explicit paired observation records.")
    seams = dataset.get("seams")
    if not isinstance(seams, list) or not seams or any(not isinstance(s, str) or not s.strip() for s in seams) or len(set(seams)) != len(seams):
        raise ValueError("Declare distinct seam labels to validate.")
    skew_limit = dataset.get("max_pair_skew_us", 20_000)
    if type(skew_limit) not in (int, float) or not np.isfinite(skew_limit) or not 0 < skew_limit <= 100_000:
        raise ValueError("max_pair_skew_us must be positive and at most 100000.")
    detector = BoardDetector(dataset["board"])
    if detector.type != "checkerboard":
        raise ValueError("This bounded alignment tool requires the explicitly marked checkerboard convention.")
    obj = detector.object_points.reshape(-1, 3)
    columns, rows = detector.pattern_size
    anchors = {"O": 0, "X": columns - 1, "Y": columns * (rows - 1)}
    geometry = {name: _intrinsics_camera(dataset, name) for name in ("A", "B")}
    if geometry["A"]["provenance"]["device_id"] == geometry["B"]["provenance"]["device_id"]:
        raise ValueError("A and B must identify different physical cameras.")
    lenses, sources, synthetic_sources = {}, {}, []
    for name in ("A", "B"):
        path = (dataset_path.parent / dataset["intrinsics"][name]).resolve()
        document = json.loads(path.read_text())
        lenses[name] = _lens(document, name, geometry[name])
        sources[name] = {"path": str(path), "sha256": _hash(path), "source_validation": document.get("validation"),
                         "parameters": document["cameras"][name]}
        validation = document.get("validation")
        validation_status = validation.get("status", "") if isinstance(validation, dict) else ""
        if document.get("evidence_kind") == "synthetic_fixture" or str(validation_status).startswith("synthetic"):
            synthetic_sources.append(name)
    report = {"schema_version": 1, "kind": "rig_alignment_result", "status": "insufficient_evidence",
              "rig_alignment_status": "unmeasured", "panorama_ready": False,
              "evidence_kind": dataset["evidence_kind"], "source_dataset": str(dataset_path.resolve()),
              "synthetic_intrinsic_sources": synthetic_sources,
              "dataset_sha256": _hash(dataset_path), "intrinsics": sources, "lens_parameters_frozen": True,
              "held_out_protocol": "Shared rig transform and both lenses frozen; one board pose jointly optimized per held-out A/B pair",
              "held_out_scope": "relative_alignment_only_conditional_on_supplied_intrinsics",
              "lens_training_overlap_status": "not_audited",
              "units": {"translation": "metres", "image_residual": "pixels", "timing_skew": "microseconds"},
              "transform_convention": "X_B = R_B_from_A @ X_A + t_B_from_A_m",
              "rig_frame": "A optical frame: +X image right, +Y image down, +Z optical; no rocket-body alignment inferred",
              "board": dataset["board"], "physical_anchor_ids": anchors, "declared_seams": seams,
              "requirements": {"distinct_fit_pairs": MIN_FIT, "held_out_pairs_per_seam": MIN_VALIDATION_PER_SEAM,
                               "maximum_rms_px": 1, "maximum_p95_px": 2, "max_pair_skew_us": skew_limit,
                               "near_duplicate_grid_rms_px": 2},
              "pairs": [], "rejected_pairs": [],
              "limitations": ["Reported sensor timestamps do not prove simultaneous exposures; stationary holds are operator-confirmed.",
                              "Physical corner identity and unchanged focus/mounting rely on explicit operator declarations.",
                              "Alignment-held-out observations may have trained the supplied lens models; lens-training overlap is not audited, so this is not independent whole-stack validation.",
                              "Held-out checks cover the declared board/seam observations, not every scene ray.",
                              "A nonzero baseline causes depth-dependent parallax; an infinity panorama cannot remove it.",
                              "This artifact is not a viewer calibration and never enables a panorama automatically."]}
    output.mkdir(parents=True, exist_ok=False)
    try:
        usable, seen_ids, seen_groups, seen_frames = [], set(), set(), set()
        for record in dataset.get("pairs", []):
            try:
                pair = _load_pair(record, dataset_path.parent, geometry, dataset["image_orientation"], len(obj), anchors, skew_limit)
                if record["seam"] not in seams:
                    raise ValueError("Pair seam is not declared in the dataset.")
                if record["pair_id"] in seen_ids or record["pose_group"] in seen_groups:
                    raise ValueError("Pair IDs and pose groups must be unique; never split the same hold between fit and validation.")
                identities = {(name, f["session_id"], f["sequence"]) for name, f in pair["frames"].items()}
                hashes = {(name, f["sha256"]) for name, f in pair["frames"].items()}
                if (identities | hashes) & seen_frames:
                    raise ValueError("A source frame/image is reused in another pair.")
                if any(max(float(np.sqrt(np.mean(np.sum((pair["points"][name] - other["points"][name]) ** 2, axis=1)))) for name in ("A", "B")) < 2 for other in usable):
                    raise ValueError("Near-duplicate corner grids are not a distinct board pose.")
                pair["initial_poses"] = {name: _pose(obj, pair["points"][name], lenses[name]) for name in ("A", "B")}
                seen_ids.add(record["pair_id"]); seen_groups.add(record["pose_group"]); seen_frames.update(identities | hashes)
                pair["evidence"] = {key: record[key] for key in ("pair_id", "pose_group", "split", "seam", "correspondence")}
                pair["evidence"].update(frames=pair["frames"], timing_skew_us=pair["skew_us"])
                report["pairs"].append(pair["evidence"])
                usable.append(pair)
            except (KeyError, ValueError, OSError, cv2.error) as exc:
                report["rejected_pairs"].append({"pair_id": record.get("pair_id"), "reason": str(exc)})
        fit = [p for p in usable if p["record"]["split"] == "fit"]
        validation = [p for p in usable if p["record"]["split"] == "validation"]
        report["fit_pairs"] = len(fit)
        report["held_out_pairs_per_seam"] = {s: sum(p["record"]["seam"] == s for p in validation) for s in seams}
        report["timing"] = {"max_abs_skew_us": max((abs(p["skew_us"]) for p in usable), default=None), "hardware_synchronization_verified": False}
        if len(fit) < MIN_FIT:
            report["status"] = "insufficient_distinct_fit_pairs"
            return report
        relative = [_relative(p["initial_poses"]["A"], p["initial_poses"]["B"]) for p in fit]
        mean_r = Rotation.from_rotvec(np.array(relative)[:, :3]).mean().as_rotvec()
        initial = np.r_[mean_r, np.median(np.array(relative)[:, 3:], axis=0), *[p["initial_poses"]["A"] for p in fit]]
        nres = len(obj) * 4
        sparsity = lil_matrix((nres * len(fit), len(initial)), dtype=int)
        for i in range(len(fit)):
            sparsity[i * nres:(i + 1) * nres, :6] = 1
            sparsity[i * nres:(i + 1) * nres, 6 + 6 * i:12 + 6 * i] = 1
        def residual(parameters):
            return np.concatenate([_residual(obj, pair, lenses, parameters[:6], parameters[6 + 6*i:12 + 6*i]) for i, pair in enumerate(fit)])
        result = least_squares(residual, initial, jac_sparsity=sparsity.tocsr(), x_scale="jac", max_nfev=300,
                               ftol=1e-10, xtol=1e-10, gtol=1e-10)
        if not result.success or not np.isfinite(result.x).all() or not np.isfinite(result.fun).all():
            report["status"] = "optimization_failed"
            report["optimizer_message"] = result.message
            return report
        transform = result.x[:6]
        rotation = cv2.Rodrigues(transform[:3])[0]
        report["candidate_transform"] = {"R_B_from_A": rotation.tolist(), "t_B_from_A_m": transform[3:].tolist(),
                                           "camera_B_center_in_A_m": (-rotation.T @ transform[3:]).tolist(),
                                           "baseline_m": float(np.linalg.norm(transform[3:]))}
        singular = np.linalg.svd(result.jac.toarray() if hasattr(result.jac, "toarray") else result.jac, compute_uv=False)
        rank = int(np.sum(singular > singular[0] * 1e-10))
        report["observability"] = {"jacobian_rank": rank, "parameter_count": len(initial),
                                   "condition_number": float(singular[0] / singular[-1]) if singular[-1] > 0 else None,
                                   "note": "Local numerical rank uses metre/radian parameter units; it is not a global uniqueness proof."}
        groups = {phase: {name: [] for name in ("A", "B")} for phase in ("fit", "validation")}
        by_seam = {s: {name: [] for name in ("A", "B")} for s in seams}
        failures = False
        evaluated = {s: 0 for s in seams}
        for phase, pairs in (("fit", fit), ("validation", validation)):
            for i, pair in enumerate(pairs):
                if phase == "fit":
                    pose = result.x[6 + 6*i:12 + 6*i]
                else:
                    # Shared extrinsics and both lens models stay frozen; only
                    # this held-out board's pose is adjusted jointly for A/B.
                    b = pair["initial_poses"]["B"]
                    from_b = np.r_[cv2.Rodrigues(rotation.T @ cv2.Rodrigues(b[:3])[0])[0].ravel(), rotation.T @ (b[3:] - transform[3:])]
                    candidates = [least_squares(lambda p: _residual(obj, pair, lenses, transform, p), guess, x_scale="jac", max_nfev=300)
                                  for guess in (pair["initial_poses"]["A"], from_b)]
                    candidates = [c for c in candidates if c.success and np.isfinite(c.fun).all()]
                    if not candidates:
                        report["rejected_pairs"].append({"pair_id": pair["record"]["pair_id"], "reason": "held-out shared-board pose optimization failed"})
                        failures = True
                        continue
                    pose = min(candidates, key=lambda c: np.sum(c.fun ** 2)).x
                errors, domain_valid = _errors(obj, pair, lenses, transform, pose)
                pair["evidence"][phase] = {}
                for name in ("A", "B"):
                    values = np.linalg.norm(errors[name], axis=1)
                    stats = error_stats(values)
                    pair["evidence"][phase][name] = stats
                    groups[phase][name].extend(values.tolist())
                    if phase == "validation":
                        by_seam[pair["record"]["seam"]][name].extend(values.tolist())
                    if not domain_valid or stats["rms_px"] > 1 or stats["p95_px"] > 2:
                        failures = True
                        pair["evidence"]["check_failure"] = "Mei domain, same-board-face geometry or per-camera residual target failed; inspect correspondence and lens coverage"
                if phase == "validation" and domain_valid:
                    evaluated[pair["record"]["seam"]] += 1
        report["errors"] = {phase: {name: error_stats(values) for name, values in cameras.items()} for phase, cameras in groups.items()}
        report["held_out_by_seam"] = {seam: {name: error_stats(values) for name, values in cameras.items()} for seam, cameras in by_seam.items()}
        report["evaluated_held_out_pairs_per_seam"] = evaluated
        if any(count < MIN_VALIDATION_PER_SEAM for count in evaluated.values()):
            report["status"] = "insufficient_held_out_pairs"
        elif failures:
            report["status"] = "residual_or_domain_checks_failed"
        elif rank != len(initial):
            report["status"] = "degenerate_local_geometry"
        elif dataset["evidence_kind"] == "synthetic_fixture" or synthetic_sources:
            report["status"] = "synthetic_test_only"
        else:
            report["status"] = "measured_for_declared_seams"
            report["rig_alignment_status"] = "measured_with_held_out_shared_board_pairs"
        return report
    except (ValueError, KeyError, RuntimeError, cv2.error) as exc:
        report["status"] = "processing_failed"
        report["error"] = str(exc)
        raise
    finally:
        (output / "rig-alignment.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = calibrate_rig(args.dataset, args.output)
    except (ValueError, KeyError, OSError, cv2.error) as exc:
        parser.exit(2, f"rig alignment: {exc}\n")
    print(json.dumps({"status": report["status"], "output": str(args.output / "rig-alignment.json")}, indent=2))
    return 0 if report["status"] in ("measured_for_declared_seams", "synthetic_test_only") else 2


if __name__ == "__main__":
    raise SystemExit(main())

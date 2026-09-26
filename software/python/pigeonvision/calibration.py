"""Measured full-sensor board/Mei calibration with explicit held-out evidence.

Input dataset JSON: board {type:'charuco'|'checkerboard',squares_x,squares_y,
square_length_m}; ChArUco additionally needs marker_length_m and dictionary and
is the default when type is omitted. cameras {A: [{path,split:'fit'|'validation',
region}], B: [...]}. Paths
are relative to the dataset file. Rig JSON provides cameras A/B with image_size,
R_camera_from_rig, crop, output_size, flip_x, flip_y and optional valid_radius_px.
Rig axes are +Z forward through A, +X right, +Y down. Camera axes use +Z optical,
+X image right, +Y image down. Rig rotation must be supplied and its measurement
status is preserved; a fit cannot establish rig alignment from unrelated views.
"""
from __future__ import annotations

import json
import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np


class BoardDetector:
    """Detect measured planar targets in an already canonical, unflipped image.

    Checkerboard IDs describe detector-local row/column order. They do not
    identify the same physical corner across images of a symmetric board.
    """

    def __init__(self, board_info: dict[str, Any]):
        import cv2

        self.type = board_info.get("type", "charuco")
        if self.type not in ("charuco", "checkerboard"):
            raise ValueError("board.type must be charuco or checkerboard.")
        dimensions = [board_info.get(key) for key in ("squares_x", "squares_y")]
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 3 for value in dimensions):
            raise ValueError("Board squares_x/squares_y must be integers of at least three; count squares, not inner corners.")
        square = board_info.get("square_length_m")
        if isinstance(square, bool) or not isinstance(square, (int, float)) or not math.isfinite(square) or square <= 0:
            raise ValueError("Use a positive, finite measured square_length_m in metres.")
        self.pattern_size = (dimensions[0] - 1, dimensions[1] - 1)
        self.warnings = []
        if self.type == "checkerboard":
            columns, rows = self.pattern_size
            self.object_points = np.asarray(
                [[x * square, y * square, 0.] for y in range(rows) for x in range(columns)],
                dtype=np.float64).reshape(-1, 1, 3)
            self.rejection_reason = f"complete {columns}x{rows} checkerboard inner-corner grid was not detected"
            self.corner_id_scope = "detector_local_per_view"
            ambiguity = "90/180/270-degree" if columns == rows else "180-degree"
            self.warnings.append(
                f"Checkerboard {ambiguity} corner-order ambiguity is unresolved. Local corner IDs are suitable for independent intrinsics; "
                "paired extrinsic estimation requires a verified common physical origin and axis direction in both images.")
        else:
            if not hasattr(cv2, "aruco"):
                raise RuntimeError("Install opencv-contrib-python-headless for ChArUco detection.")
            dictionary_name = board_info.get("dictionary", "DICT_4X4_100")
            if not isinstance(dictionary_name, str) or not dictionary_name.startswith("DICT_") or not hasattr(cv2.aruco, dictionary_name):
                raise ValueError(f"Unknown ChArUco dictionary {dictionary_name}")
            marker = board_info.get("marker_length_m")
            if isinstance(marker, bool) or not isinstance(marker, (int, float)) or not math.isfinite(marker) or not 0 < marker < square:
                raise ValueError("Use measured board dimensions in metres with 0 < marker < square.")
            dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
            self.board = cv2.aruco.CharucoBoard(tuple(dimensions), square, marker, dictionary)
            self.detector = cv2.aruco.CharucoDetector(self.board)
            self.rejection_reason = "fewer than eight detected ChArUco corners"
            self.corner_id_scope = "board_corner_id"

    def detect(self, gray: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """Return object points, image points and IDs, or None for a rejected view."""
        import cv2

        if self.type == "checkerboard":
            # SB refines to subpixel coordinates internally. No LARGER flag:
            # only a complete grid with the requested size is accepted.
            flags = cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY
            found, corners = cv2.findChessboardCornersSB(gray, self.pattern_size, flags=flags)
            if not found or corners is None or len(corners) != len(self.object_points):
                return None
            ids = np.arange(len(self.object_points), dtype=np.int32)
            obj = self.object_points.copy()
        else:
            corners, ids, _, _ = self.detector.detectBoard(gray)
            if ids is None or len(ids) < 8:
                return None
            ids = ids.ravel()
            obj = self.board.getChessboardCorners()[ids].reshape(-1, 1, 3)
        image = np.ascontiguousarray(corners, dtype=np.float64).reshape(-1, 1, 2)
        if not np.isfinite(image).all():
            return None
        return np.ascontiguousarray(obj, dtype=np.float64), image, ids


def project_mei(rays: np.ndarray, camera: dict[str, Any], *, output: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """Project rig rays through Mei, distortion, full-sensor crop, resize and flips."""
    rays = np.asarray(rays, dtype=float)
    rotation = np.asarray(camera["R_camera_from_rig"], dtype=float)
    xyz = rays @ rotation.T
    lengths = np.linalg.norm(xyz, axis=-1)
    unit = xyz / np.maximum(lengths[..., None], 1e-15)
    denominator = unit[..., 2] + float(camera["xi"])
    xi = float(camera["xi"])
    valid = (lengths > 0) & (denominator > 1e-9)
    if xi > 1:
        valid &= unit[..., 2] > -1 / xi
    x, y = unit[..., 0] / np.maximum(denominator, 1e-9), unit[..., 1] / np.maximum(denominator, 1e-9)
    k1, k2, p1, p2 = np.asarray(camera["D"]).reshape(4)
    r2 = x * x + y * y
    radial = 1 + k1 * r2 + k2 * r2 * r2
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    K = np.asarray(camera["K"])
    points = np.stack([K[0, 0] * xd + K[0, 1] * yd + K[0, 2], K[1, 1] * yd + K[1, 2]], axis=-1)
    width, height = camera["image_size"]
    valid &= (points[..., 0] >= 0) & (points[..., 0] < width) & (points[..., 1] >= 0) & (points[..., 1] < height)
    if camera.get("valid_radius_px") is not None:
        valid &= np.linalg.norm(points - K[:2, 2], axis=-1) <= camera["valid_radius_px"]
    if camera.get("max_theta_deg") is not None:
        valid &= np.arccos(np.clip(unit[..., 2], -1, 1)) <= math.radians(camera["max_theta_deg"])
    if output:
        cx, cy, cw, ch = camera["crop"]
        valid &= (points[..., 0] >= cx) & (points[..., 0] < cx + cw) & (points[..., 1] >= cy) & (points[..., 1] < cy + ch)
        ow, oh = camera["output_size"]
        points = (points - [cx, cy] + 0.5) * [ow / cw, oh / ch] - 0.5
        if camera.get("flip_x", False):
            points[..., 0] = ow - 1 - points[..., 0]
        if camera.get("flip_y", False):
            points[..., 1] = oh - 1 - points[..., 1]
    return points, valid


def error_stats(errors: list[float] | np.ndarray) -> dict[str, Any]:
    values = np.asarray(errors, dtype=float)
    return {"point_count": int(values.size),
            "rms_px": float(np.sqrt(np.mean(values ** 2))) if values.size else None,
            "p95_px": float(np.percentile(values, 95)) if values.size else None,
            "max_px": float(values.max()) if values.size else None}


def validate_rig(camera: dict[str, Any]) -> None:
    for key in ("image_size", "R_camera_from_rig", "crop", "output_size", "flip_x", "flip_y"):
        if key not in camera:
            raise ValueError(f"Rig camera is missing {key}; supply the measured geometry and image transform.")
    rotation = np.asarray(camera["R_camera_from_rig"], dtype=float)
    if rotation.shape != (3, 3) or not np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-5) or not np.isclose(np.linalg.det(rotation), 1, atol=1e-5):
        raise ValueError("R_camera_from_rig must be a proper orthonormal rotation.")
    width, height = camera["image_size"]
    x, y, w, h = camera["crop"]
    if min(width, height, w, h, *camera["output_size"]) <= 0 or min(x, y) < 0 or x + w > width or y + h > height:
        raise ValueError("Crop must lie within the measured full-sensor image.")


def _held_out(cv2, obj, image, K, D, xi):
    """Fit only board pose for held-out views; keep all lens parameters fixed."""
    from scipy.optimize import least_squares
    obj = np.ascontiguousarray(obj, dtype=np.float64).reshape(-1, 1, 3)
    image = np.asarray(image, dtype=float).reshape(-1, 2)
    # Undistorted central rays provide an initial board pose. Then minimize the
    # real omnidirectional projection (including >90 degree rays when present).
    success, rvec, tvec = cv2.solvePnP(obj, image.reshape(-1, 1, 2), K, None, flags=cv2.SOLVEPNP_ITERATIVE)
    if not success:
        raise ValueError("Held-out board pose initialization failed.")

    def residual(parameters):
        predicted, _ = cv2.omnidir.projectPoints(obj, parameters[:3].reshape(3, 1), parameters[3:].reshape(3, 1), K, float(xi), D)
        return (predicted.reshape(-1, 2) - image).ravel()

    result = least_squares(residual, np.r_[rvec.ravel(), tvec.ravel()], max_nfev=1000)
    if not result.success or not np.isfinite(result.fun).all():
        raise ValueError("Held-out board pose optimization failed.")
    transformed = obj.reshape(-1, 3) @ cv2.Rodrigues(result.x[:3])[0].T + result.x[3:]
    unit = transformed / np.linalg.norm(transformed, axis=1)[:, None]
    if np.any(unit[:, 2] + float(xi) <= 1e-9) or (xi > 1 and np.any(unit[:, 2] <= -1 / xi)):
        raise ValueError("Held-out fitted pose is outside the Mei projection domain.")
    theta = np.rad2deg(np.arccos(np.clip(unit[:, 2], -1, 1)))
    return np.linalg.norm(result.fun.reshape(-1, 2), axis=1), theta


def calibrate(dataset_path: Path, rig_path: Path, output: Path) -> dict[str, Any]:
    import cv2
    if not hasattr(cv2, "omnidir"):
        raise RuntimeError("Install the locked opencv-contrib-python-headless package; omnidir is required.")
    dataset = json.loads(dataset_path.read_text())
    rig = json.loads(rig_path.read_text())
    board_info = dataset["board"]
    detector = BoardDetector(board_info)
    bundle = {"schema_version": 1, "model": "mei", "rig_axes": "+Z forward through A, +X right, +Y down",
              "rig_alignment_status": rig.get("rig_alignment_status", "unverified"), "cameras": {},
              "validation": {"status": "measured_intrinsics_only", "targets": {"rms_px": 1, "p95_px": 2}, "cameras": {}},
              "source_dataset": str(dataset_path.resolve()), "board": board_info,
              "board_detection": {"type": detector.type, "corner_id_scope": detector.corner_id_scope, "warnings": detector.warnings},
              "intrinsic_coordinate_convention": "unflipped_full_sensor_pixel_centres",
              "provenance": {"dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                             "rig_sha256": hashlib.sha256(rig_path.read_bytes()).hexdigest()}}
    evidence = {"schema_version": 1, "board_detection": bundle["board_detection"], "observations": []}
    if output.exists() and any(output.iterdir()):
        raise ValueError("Calibration output is not empty; choose a new output directory to preserve earlier evidence.")
    output.mkdir(parents=True, exist_ok=True)
    try:
        for camera_id in ("A", "B"):
            camera = dict(rig["cameras"][camera_id])
            validate_rig(camera)
            size = tuple(camera["image_size"])
            orientation = dataset.get("image_orientation", {}).get(camera_id, {})
            if any(not isinstance(orientation.get(key), bool) for key in ("flip_x", "flip_y")):
                raise ValueError(f"Explicit image_orientation.{camera_id}.flip_x/flip_y booleans are required; vendor stills may already be flipped.")
            camera["provenance"] = {key: camera.get("provenance", {}).get(key) for key in ("device_id", "lens_id", "focus_position", "focus_reference_sha256", "crop_source")}
            fit, validation, seen, image_hashes = [], [], set(), set()
            for record in dataset["cameras"][camera_id]:
                path = (dataset_path.parent / record["path"]).resolve()
                observation = {"camera_id": camera_id, "path": str(path), "split": record.get("split"), "region": record.get("region", "unlabelled")}
                evidence["observations"].append(observation)
                if path in seen:
                    raise ValueError(f"Duplicate image in dataset: {path}; fit and validation must be disjoint.")
                seen.add(path)
                if record.get("split") not in ("fit", "validation"):
                    raise ValueError("Each image needs an explicit fit or validation split.")
                gray = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
                if gray is None or gray.shape[::-1] != size:
                    observation["rejected"] = "unreadable or different full-sensor image size"
                    continue
                observation["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                if observation["sha256"] in image_hashes:
                    raise ValueError(f"Duplicate image content in camera {camera_id}: {path}; held-out images must be independent.")
                image_hashes.add(observation["sha256"])
                observation["image_unflip_applied"] = orientation
                if orientation["flip_x"]:
                    gray = cv2.flip(gray, 1)
                if orientation["flip_y"]:
                    gray = cv2.flip(gray, 0)
                detected = detector.detect(gray)
                observation.update(board_type=detector.type, corner_id_scope=detector.corner_id_scope)
                if detected is None:
                    observation["rejected"] = detector.rejection_reason
                    continue
                obj, img, ids = detected
                observation.update(corner_count=len(ids), corner_ids=ids.ravel().tolist(), canonical_image_points=img.reshape(-1, 2).tolist())
                (fit if record["split"] == "fit" else validation).append((obj, img, observation))
            if len(fit) < 8 or len(validation) < 3:
                raise ValueError(f"Camera {camera_id} needs at least eight accepted fit views and three held-out views.")
            flags = cv2.omnidir.CALIB_FIX_SKEW
            rms, K, xi, D, _, _, accepted = cv2.omnidir.calibrate(
                [x[0] for x in fit], [x[1] for x in fit], size, None, None, None, flags,
                (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 200, 1e-8))
            accepted_indices = set(np.asarray(accepted).ravel().tolist())
            for index, (_, _, observation) in enumerate(fit):
                observation["fit_used"] = index in accepted_indices
                if index not in accepted_indices:
                    observation["rejected"] = "omnidir initialization rejected this fit view"
            if len(accepted_indices) < 8:
                raise ValueError(f"Camera {camera_id}: omnidir retained fewer than eight fit views.")
            if not np.isfinite(K).all() or not np.isfinite(D).all() or not np.isfinite(xi).all():
                raise ValueError(f"Camera {camera_id}: non-finite fitted parameters.")
            all_errors, all_theta, region_errors = [], [], {}
            for obj, img, observation in validation:
                try:
                    errors, theta = _held_out(cv2, obj, img, K, D, float(xi.item()))
                except ValueError as exc:
                    observation["rejected"] = str(exc)
                    continue
                observation["validation"] = error_stats(errors)
                observation["theta_range_deg"] = [float(theta.min()), float(theta.max())]
                all_errors.extend(errors.tolist())
                all_theta.extend(theta.tolist())
                region_errors.setdefault(observation["region"], []).extend(errors.tolist())
                radius = np.linalg.norm(img.reshape(-1, 2) - K[:2, 2], axis=1) / (min(size) / 2)
                for label, mask in (("central", radius < .5), ("mid", (radius >= .5) & (radius < .8)), ("edge", radius >= .8)):
                    region_errors.setdefault(label, []).extend(errors[mask].tolist())
            validation_count = sum("validation" in v[2] for v in validation)
            if validation_count < 3:
                raise ValueError(f"Camera {camera_id}: fewer than three held-out poses could be evaluated.")
            camera.update(K=K.tolist(), D=D.ravel().tolist(), xi=float(xi.item()),
                          max_theta_deg=max(all_theta), valid_radius_px=camera.get("valid_radius_px"))
            bundle["cameras"][camera_id] = camera
            stats = error_stats(all_errors)
            region_stats = {name: error_stats(errors) for name, errors in region_errors.items()}
            seam_stats = [value for name, value in region_stats.items() if name.startswith("seam") and value["point_count"]]
            bundle["validation"]["cameras"][camera_id] = {
                "fit_rms_px": float(rms), "fit_views": len(accepted_indices), "held_out_views": validation_count,
                "held_out": stats, "regions": region_stats,
                "thresholds_met": stats["rms_px"] <= 1 and stats["p95_px"] <= 2,
                "seam_thresholds_met": all(value["rms_px"] <= 1 and value["p95_px"] <= 2 for value in seam_stats) if seam_stats else None,
                "seam_coverage_measured": bool(seam_stats),
                "domain_note": "max_theta_deg is the largest observed held-out board ray, not proof of complete angular coverage."}
        (output / "calibration.json").write_text(json.dumps(bundle, indent=2, allow_nan=False) + "\n")
        return bundle
    except cv2.error as exc:
        raise ValueError(f"OpenCV calibration failed; inspect retained observations and board coverage: {exc}") from exc
    finally:
        (output / "observations.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")

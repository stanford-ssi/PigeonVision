"""Synthetic shared-board observations, never hardware alignment evidence."""
import hashlib
import json

import cv2
import numpy as np
import pytest

from pigeonvision.calibration import BoardDetector, _fit_board_pose, _held_out
from pigeonvision.rig_calibration import calibrate_rig


@pytest.fixture
def shared_board(tmp_path):
    board = {"type": "checkerboard", "squares_x": 7, "squares_y": 7, "square_length_m": .03}
    obj = BoardDetector(board).object_points.reshape(-1, 3)
    camera_size = [640, 480]
    K = np.array([[180., 0, 320.], [0, 183., 240.], [0, 0, 1.]])
    D = np.array([-.03, .004, .0002, -.0001])
    rotation = cv2.Rodrigues(np.array([.015, np.pi - .025, .01]))[0]
    center_b = np.array([.005, .002, -.17])
    translation = -rotation @ center_b
    physical = {name: {"id": name, "device": f"synthetic-{name}", "sensor_size": camera_size,
                       "width": 640, "height": 480, "requested_sensor_crop": [0, 0, 640, 480],
                       "flip_x": False, "flip_y": True} for name in "AB"}
    intrinsic_paths = {}
    for name in "AB":
        path = tmp_path / f"{name}-intrinsics.json"
        path.write_text(json.dumps({"schema_version": 1, "model": "mei", "scope": "intrinsics_only",
            "intrinsic_coordinate_convention": "unflipped_full_sensor_pixel_centres",
            "validation": {"status": "synthetic_test_only"},
            "cameras": {name: {"image_size": camera_size, "K": K.tolist(), "D": D.tolist(), "xi": 1.,
                               "provenance": {"device_id": f"synthetic-{name}"}}}}))
        intrinsic_paths[name] = path.name
    dataset = {"schema_version": 1, "kind": "rig_alignment_observations", "evidence_kind": "synthetic_fixture",
               "coordinate_convention": "unflipped_full_sensor_pixel_centres", "assembly_unchanged_confirmed": True,
               "seams": ["seam-left", "seam-right"], "board": board,
               "physical_cameras": physical, "image_orientation": {name: {"flip_x": False, "flip_y": True} for name in "AB"},
               "intrinsics": intrinsic_paths, "pairs": []}
    origin = 9_000_000_000
    for i in range(14):
        side = -1 if i % 2 else 1
        rb = cv2.Rodrigues(np.array([0., -side * np.pi / 2, 0.]))[0] @ cv2.Rodrigues(np.array([.16*np.sin(i+.2), .12*np.cos(i), .2*np.sin(i*.7)]))[0]
        center = np.array([side*(.6 + .055*(i % 3)), .07*np.sin(i*.9), -.085 + .035*np.cos(i*.6)])
        ta = center - rb @ obj.mean(axis=0)
        xyz_a = obj @ rb.T + ta
        xyz_b = xyz_a @ rotation.T + translation
        pair = {"pair_id": f"synthetic-{i}", "pose_group": f"distinct-hold-{i}",
                "split": "fit" if i < 8 else "validation", "seam": "seam-left" if side < 0 else "seam-right",
                "stationary_hold_confirmed": True,
                "correspondence": {"operator_confirmed": True, "same_printed_face": True,
                                   "note": "Synthetic fixture has known physical O/X/Y; no real operator confirmation."}, "frames": {}}
        for name, xyz in (("A", xyz_a), ("B", xyz_b)):
            points, _ = cv2.omnidir.projectPoints(xyz.reshape(-1, 1, 3), np.zeros(3), np.zeros(3), K, 1., D)
            points = points.reshape(-1, 2)
            path = tmp_path / f"{name}-{i}.png"
            assert cv2.imwrite(str(path), np.full((480, 640), 20+i+(100 if name == "B" else 0), np.uint8))
            pts = 1_000_000 + i * 500_000 + (600 if name == "B" else 0)
            # Intentionally shuffle record order: shared identity comes from IDs.
            order = np.roll(np.arange(len(obj)), i + (3 if name == "B" else 0))
            pair["frames"][name] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "device_id": f"synthetic-{name}", "session_id": "synthetic-shared-clock",
                "clock_domain": "CLOCK_BOOTTIME", "clock_origin_ns": origin,
                "image_orientation": {"flip_x": False, "flip_y": True},
                "capture_metadata": {"schema_version": 1, "camera_id": name, "sequence": i,
                    "sensor_timestamp_ns": origin + pts * 1000, "pts_us": pts, "status": "encoded", "drop_reason": None,
                    "sensor_crop": [0, 0, 640, 480]},
                "corner_ids": order.tolist(), "image_points": points[order].tolist(),
                "anchor_pixels": {"O": points[0].tolist(), "X": points[5].tolist(), "Y": points[30].tolist()}}
        dataset["pairs"].append(pair)
    path = tmp_path / "rig-observations.json"
    def write():
        path.write_text(json.dumps(dataset))
    write()
    return path, dataset, write, rotation, translation


def test_frozen_mei_joint_alignment_recovers_nonzero_baseline_and_held_out(shared_board, tmp_path):
    path, dataset, _, expected_r, expected_t = shared_board
    lens_bytes = {n: (tmp_path / dataset["intrinsics"][n]).read_bytes() for n in "AB"}
    result = calibrate_rig(path, tmp_path / "result")
    assert result["status"] == "synthetic_test_only"
    assert result["rig_alignment_status"] == "unmeasured" and result["panorama_ready"] is False
    fitted = result["candidate_transform"]
    assert np.allclose(fitted["R_B_from_A"], expected_r, atol=1e-6)
    assert np.allclose(fitted["t_B_from_A_m"], expected_t, atol=1e-6)
    assert fitted["baseline_m"] == pytest.approx(np.linalg.norm(expected_t), abs=1e-6)
    assert result["timing"]["max_abs_skew_us"] == 600
    assert result["lens_parameters_frozen"] is True
    assert result["held_out_scope"] == "relative_alignment_only_conditional_on_supplied_intrinsics"
    assert result["lens_training_overlap_status"] == "not_audited"
    assert result["evaluated_held_out_pairs_per_seam"] == {"seam-left": 3, "seam-right": 3}
    for phase in ("fit", "validation"):
        for name in "AB":
            assert result["errors"][phase][name]["rms_px"] < 1e-5
            assert (tmp_path / dataset["intrinsics"][name]).read_bytes() == lens_bytes[name]
    assert not (tmp_path / "result" / "calibration.json").exists()
    with pytest.raises(FileExistsError):
        calibrate_rig(path, tmp_path / "result")


@pytest.mark.parametrize("change,reason", [
    ("symmetry", "anchor conflicts"), ("unconfirmed", "operator confirmation"),
    ("clock", "common clock origin"), ("skew", "skew tolerance"),
    ("crop", "sensor_crop"), ("hash", "SHA-256"), ("device", "device/logical"),
    ("orientation", "capture orientation"),
])
def test_bad_pair_provenance_timing_or_identifiable_symmetry_is_rejected(shared_board, tmp_path, change, reason):
    path, dataset, write, _, _ = shared_board
    pair = dataset["pairs"][0]
    frame = pair["frames"]["B"]
    if change == "symmetry":
        ids = np.array(frame["corner_ids"])
        ordered = np.array(frame["image_points"])[np.argsort(ids)].reshape(6, 6, 2)
        frame["image_points"] = np.rot90(ordered).reshape(-1, 2)[ids].tolist()
    elif change == "unconfirmed":
        pair["correspondence"]["operator_confirmed"] = False
    elif change == "clock":
        frame["clock_origin_ns"] += 1000
        frame["capture_metadata"]["sensor_timestamp_ns"] += 1000
    elif change == "skew":
        frame["capture_metadata"]["sensor_timestamp_ns"] += 30_000_000
        frame["capture_metadata"]["pts_us"] += 30_000
    elif change == "crop":
        frame["capture_metadata"]["sensor_crop"] = [10, 0, 620, 480]
    elif change == "hash":
        frame["sha256"] = "0" * 64
    elif change == "device":
        frame["device_id"] = "synthetic-wrong-camera"
    else:
        frame["image_orientation"]["flip_y"] = False
    write()
    result = calibrate_rig(path, tmp_path / "result")
    assert result["status"] == "insufficient_distinct_fit_pairs"
    assert result["fit_pairs"] == 7
    assert any(reason in r["reason"] for r in result["rejected_pairs"])
    assert "candidate_transform" not in result


def test_held_out_group_cannot_reuse_a_fit_hold(shared_board, tmp_path):
    path, dataset, write, _, _ = shared_board
    dataset["pairs"][8]["pose_group"] = dataset["pairs"][0]["pose_group"]
    write()
    result = calibrate_rig(path, tmp_path / "result")
    assert result["status"] == "insufficient_held_out_pairs"
    assert result["rig_alignment_status"] == "unmeasured"
    assert any("pose groups" in r["reason"] for r in result["rejected_pairs"])


def test_false_correspondence_that_passes_anchor_declaration_fails_fixed_rig_held_out(shared_board, tmp_path):
    path, dataset, write, _, _ = shared_board
    frame = dataset["pairs"][8]["frames"]["B"]
    ids = np.array(frame["corner_ids"])
    grid = np.array(frame["image_points"])[np.argsort(ids)].reshape(6, 6, 2)
    wrong = np.rot90(grid).reshape(-1, 2)
    frame["image_points"] = wrong[ids].tolist()
    # Deliberately lying about the anchors is not detectable from their labels
    # alone. The held-out joint reprojection must still expose inconsistency.
    frame["anchor_pixels"] = {"O": wrong[0].tolist(), "X": wrong[5].tolist(), "Y": wrong[30].tolist()}
    write()
    result = calibrate_rig(path, tmp_path / "result")
    assert result["status"] == "residual_or_domain_checks_failed"
    assert result["rig_alignment_status"] == "unmeasured"
    assert max(result["errors"]["validation"][name]["rms_px"] for name in "AB") > 1
    assert max(result["errors"]["fit"][name]["rms_px"] for name in "AB") < 1e-5


def test_synthetic_intrinsic_source_cannot_be_promoted_to_real_measurement(shared_board, tmp_path):
    path, dataset, write, _, _ = shared_board
    dataset["evidence_kind"] = "camera_capture"
    write()
    result = calibrate_rig(path, tmp_path / "result")
    assert result["status"] == "synthetic_test_only"
    assert result["synthetic_intrinsic_sources"] == ["A", "B"]
    assert result["rig_alignment_status"] == "unmeasured"


def test_exposed_board_pose_helper_preserves_held_out_results():
    obj = np.array([[x*.02, y*.02, 0] for x in range(5) for y in range(5)], dtype=np.float64).reshape(-1, 1, 3)
    K, D, xi = np.array([[500., 0, 320.], [0, 505., 240.], [0, 0, 1.]]), np.zeros(4), .9
    expected_r, expected_t = np.array([.1, -.2, .04]), np.array([-.03, -.05, .5])
    image, _ = cv2.omnidir.projectPoints(obj, expected_r, expected_t, K, xi, D)
    rvec, tvec, errors, theta = _fit_board_pose(cv2, obj, image, K, D, xi)
    old_errors, old_theta = _held_out(cv2, obj, image, K, D, xi)
    assert np.allclose(cv2.Rodrigues(rvec)[0], cv2.Rodrigues(expected_r)[0], atol=1e-7)
    assert np.allclose(tvec, expected_t, atol=1e-7)
    assert np.allclose(errors, old_errors) and np.allclose(theta, old_theta)

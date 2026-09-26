"""Real Mei fits on synthetic corners; these fixtures are not camera evidence."""
import hashlib
import json

import cv2
import numpy as np
import pytest

from pigeonvision.calibration import BoardDetector, calibrate_intrinsics
from pigeonvision.ground.projection import validate_calibration


@pytest.fixture
def synthetic_dataset(tmp_path, monkeypatch):
    board = {"type": "checkerboard", "squares_x": 8, "squares_y": 7, "square_length_m": .03}
    detector = BoardDetector(board)
    K = np.array([[800., 0., 1032.], [0., 810., 776.], [0., 0., 1.]])
    D = np.array([.03, -.006, .0002, -.0001])
    observations = {}
    records = []
    for index in range(12):
        obj = detector.object_points.copy()
        rvec = np.array([.18 * (index % 4) - .25, .15 * (index % 5) - .3, .09 * (index % 3) - .08])
        tvec = np.array([.06 * (index % 4) - .18, .05 * (index % 3) - .1, .35 + .04 * (index % 5)])
        image, _ = cv2.omnidir.projectPoints(obj, rvec, tvec, K, .7, D)
        observations[index + 1] = (obj, image, np.arange(len(obj), dtype=np.int32))
        # Encode only a fixture ID into each image. The detector is separately
        # tested; real imread/unflip, provenance checks and Mei fit run here.
        gray = np.full((1552, 2064), 255, np.uint8)
        gray[:2, :2] = index + 1
        path = tmp_path / f"synthetic-{index:02d}.png"
        assert cv2.imwrite(str(path), cv2.flip(gray, 0))
        records.append({"path": path.name, "split": "fit", "device_id": "synthetic-B",
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "capture_metadata": {"camera_id": "B", "sensor_crop": [0, 0, 2064, 1552]}})
    def detect(self, gray):
        return observations[int(gray[0, 0])]
    monkeypatch.setattr(BoardDetector, "detect", detect)
    dataset = {"schema_version": 1, "board": board,
               "image_orientation": {"B": {"flip_x": False, "flip_y": True}},
               "physical_cameras": {"B": {"id": "B", "device": "synthetic-B", "sensor_size": [2064, 1552],
                  "width": 2064, "height": 1552, "requested_sensor_crop": [0, 0, 2064, 1552],
                  "flip_x": False, "flip_y": True}},
               "cameras": {"A": [], "B": records}}
    path = tmp_path / "dataset.json"
    def write():
        path.write_text(json.dumps(dataset))
    write()
    return path, dataset, write


@pytest.mark.parametrize("selection", [("B",), None])
def test_real_fit_explicit_training_only_has_no_rig_or_validation_claim(synthetic_dataset, tmp_path, selection):
    path, _, _ = synthetic_dataset
    output = tmp_path / "fit"
    bundle = calibrate_intrinsics(path, output, camera_ids=selection, allow_unvalidated=True)
    assert set(bundle["cameras"]) == {"B"}
    assert bundle["scope"] == "intrinsics_only" and bundle["rig_alignment_status"] == "unmeasured"
    assert "rig_axes" not in bundle and "R_camera_from_rig" not in bundle["cameras"]["B"]
    assert bundle["provenance"]["rig_sha256"] is None
    assert bundle["cameras"]["B"]["provenance"]["device_id"] == "synthetic-B"
    assert bundle["cameras"]["B"]["max_theta_deg"] is None
    assert bundle["validation"]["status"] == "unvalidated_intrinsics"
    evidence = bundle["validation"]["cameras"]["B"]
    assert evidence["fit_views"] >= 8
    assert evidence["training"]["rms_px"] < .01
    assert evidence["held_out_views"] == 0 and evidence["held_out"]["rms_px"] is None
    assert evidence["thresholds_met"] is None and evidence["seam_thresholds_met"] is None
    assert (output / "intrinsics.json").exists() and not (output / "calibration.json").exists()
    assert json.loads((output / "intrinsics.json").read_text()) == bundle
    with pytest.raises((ValueError, KeyError)):
        validate_calibration(bundle)


def test_held_out_is_required_by_default_and_failure_keeps_observations(synthetic_dataset, tmp_path):
    path, _, _ = synthetic_dataset
    output = tmp_path / "rejected"
    with pytest.raises(ValueError, match="three held-out"):
        calibrate_intrinsics(path, output)
    assert (output / "observations.json").exists()
    assert not (output / "intrinsics.json").exists() and not (output / "calibration.json").exists()


@pytest.mark.parametrize("contradiction", ["physical_id", "device", "logical_id", "crop", "missing_source", "hash", "orientation"])
def test_source_provenance_mismatches_are_rejected_before_fit(synthetic_dataset, tmp_path, contradiction):
    path, dataset, write = synthetic_dataset
    record = dataset["cameras"]["B"][0]
    if contradiction == "physical_id":
        dataset["physical_cameras"]["B"]["id"] = "A"
    elif contradiction == "device":
        record["device_id"] = "synthetic-A"
    elif contradiction == "logical_id":
        record["capture_metadata"]["camera_id"] = "A"
    elif contradiction == "crop":
        record["capture_metadata"]["sensor_crop"] = [256, 0, 1552, 1552]
    elif contradiction == "missing_source":
        del record["capture_metadata"]
    elif contradiction == "hash":
        record["sha256"] = "0" * 64
    else:
        record["image_orientation"] = {"flip_x": False, "flip_y": False}
    write()
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        calibrate_intrinsics(path, output, allow_unvalidated=True)
    assert not (output / "intrinsics.json").exists()
    assert (output / "observations.json").exists()


def test_unvalidated_opt_in_does_not_relax_fit_view_minimum(synthetic_dataset, tmp_path):
    path, dataset, write = synthetic_dataset
    dataset["cameras"]["B"] = dataset["cameras"]["B"][:7]
    write()
    with pytest.raises(ValueError, match="eight accepted fit"):
        calibrate_intrinsics(path, tmp_path / "fit", allow_unvalidated=True)


@pytest.mark.parametrize("held_out", [2, 3])
def test_real_fit_distinguishes_insufficient_and_sufficient_held_out_evidence(synthetic_dataset, tmp_path, held_out):
    path, dataset, write = synthetic_dataset
    for record in dataset["cameras"]["B"][-held_out:]:
        record["split"] = "validation"
    write()
    bundle = calibrate_intrinsics(path, tmp_path / "fit", allow_unvalidated=held_out < 3)
    evidence = bundle["validation"]["cameras"]["B"]
    assert evidence["held_out_views"] == held_out
    assert evidence["held_out"]["rms_px"] < .01
    if held_out < 3:
        assert bundle["validation"]["status"] == "unvalidated_intrinsics"
        assert evidence["thresholds_met"] is None
        assert bundle["cameras"]["B"]["max_theta_deg"] is None
    else:
        assert evidence["status"] == "held_out_evaluated" and evidence["thresholds_met"] is True
        assert bundle["cameras"]["B"]["max_theta_deg"] > 0
    assert bundle["rig_alignment_status"] == "unmeasured"
    assert "R_camera_from_rig" not in bundle["cameras"]["B"]

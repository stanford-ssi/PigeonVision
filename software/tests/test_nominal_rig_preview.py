"""Nominal rig previews preserve measurement limits and physical identity."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from pigeonvision.ground.projection import project_ray, validate_calibration


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "nominal_rig_preview.py"
spec = importlib.util.spec_from_file_location("nominal_rig_preview", SCRIPT)
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


def write(path, data):
    path.write_text(json.dumps(data))


@pytest.fixture
def inputs(tmp_path):
    paths = {}
    for camera_id in "AB":
        path = tmp_path / f"synthetic-{camera_id}-dataset.json"
        physical = {"id": camera_id, "device": f"synthetic-{camera_id}", "sensor_size": [2064, 1552],
                    "width": 2064, "height": 1552, "requested_sensor_crop": [0, 0, 2064, 1552],
                    "flip_x": False, "flip_y": True}
        write(path, {"physical_cameras": {camera_id: physical},
                     "image_orientation": {camera_id: {"flip_x": False, "flip_y": True}},
                     "cameras": {camera_id: [{"path": "synthetic.png", "device_id": f"synthetic-{camera_id}",
                        "capture_metadata": {"camera_id": camera_id, "sensor_crop": [0, 0, 2064, 1552]}}]}})
        paths[camera_id] = path
    lens = {"image_size": [2064, 1552], "K": [[500., 0., 1032.], [0., 500., 776.], [0., 0., 1.]],
            "D": [0., 0., 0., 0.], "xi": 1., "max_theta_deg": 170}
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    common = {"schema_version": 1, "model": "mei", "intrinsic_coordinate_convention": "unflipped_full_sensor_pixel_centres"}
    write(a, common | {"cameras": {"A": lens | {"provenance": {"device_id": "synthetic-A"}}},
        "validation": {"status": "unvalidated_intrinsics", "cameras": {"A": {
            "status": "unvalidated_missing_or_insufficient_held_out", "held_out_views": 0, "thresholds_met": None}}},
        "source_dataset": str(paths["A"]), "provenance": {"dataset_sha256": hashlib.sha256(paths["A"].read_bytes()).hexdigest()}})
    write(b, common | lens | {"camera_id": "B", "kind": "training_only_intrinsics_diagnostic",
        "training": {"rms_px": .2}, "fit_accepted_views": 12, "held_out_status": "missing",
        "views": [{"path": "synthetic.png", "source_dataset": str(paths["B"])}]})
    return a, b, paths


def generate(inputs, output, **overrides):
    a, b, datasets = inputs
    kwargs = {"baseline_m": .1524, "geometry_source_date": "2026-09-25", "opposed": True,
              "common_roll_assumed": True, "datasets_b": [datasets["B"]]} | overrides
    return preview.generate(a, b, output, **kwargs)


def test_viewer_bundle_uses_nominal_opposed_axes_without_promoting_training(inputs, tmp_path):
    output = tmp_path / "preview"
    bundle = generate(inputs, output)
    assert validate_calibration(bundle) == bundle
    assert bundle["rig_alignment_status"] == "nominal_operator_geometry" and bundle["qualified"] is False
    assert bundle["nominal_geometry"]["baseline_m"] == .1524
    assert bundle["nominal_geometry"]["translation_used_in_projection"] is False
    assert "assumed zero" in bundle["nominal_geometry"]["common_roll_assumption"]
    for camera_id in "AB":
        camera = bundle["cameras"][camera_id]
        assert camera["max_theta_deg"] is None  # Training-only source limits are not validated bounds.
        assert camera["crop"] == [0, 0, 2064, 1552] and camera["flip_y"] is True
        assert camera["provenance"]["device_id"] == f"synthetic-{camera_id}"
        assert bundle["validation"]["cameras"][camera_id]["thresholds_met"] is None
    assert project_ray(bundle["cameras"]["A"], (0, 0, 1)) == (1032., 775.)
    assert project_ray(bundle["cameras"]["B"], (0, 0, -1)) == (1032., 775.)
    assert project_ray(bundle["cameras"]["B"], (0, 0, 1)) is None
    binding_a = bundle["provenance"]["A"]["source_binding"]
    binding_b = bundle["provenance"]["B"]["source_binding"]
    assert binding_a["method"] == "fit_time_dataset_sha256_verified"
    assert binding_a["fit_time_dataset_hash_verified"] is True
    assert binding_a["verified_fit_dataset"] == str(inputs[2]["A"].resolve())
    assert binding_b["method"] == "source_view_membership_only"
    assert binding_b["fit_time_dataset_hash_verified"] is False
    assert binding_b["verified_fit_dataset"] is None
    assert binding_b["fingerprint_scope"] == "files_at_preview_generation"
    assert json.loads((output / "calibration.json").read_text()) == bundle
    with pytest.raises(FileExistsError):
        generate(inputs, output)


@pytest.mark.parametrize("override", [{"opposed": False}, {"common_roll_assumed": False}, {"baseline_m": 0},
                                     {"baseline_m": float("nan")}, {"geometry_source_date": "unknown"}])
def test_nominal_geometry_requires_explicit_finite_assumptions(inputs, tmp_path, override):
    with pytest.raises(ValueError):
        generate(inputs, tmp_path / "preview", **override)


@pytest.mark.parametrize("mismatch", ["device", "crop", "orientation", "source_view", "dataset_hash"])
def test_source_identity_geometry_and_fit_provenance_are_checked(inputs, tmp_path, mismatch):
    a, b, datasets = inputs
    if mismatch == "dataset_hash":
        value = json.loads(a.read_text())
        value["provenance"]["dataset_sha256"] = "wrong"
        write(a, value)
    elif mismatch == "source_view":
        value = json.loads(b.read_text())
        value["views"][0]["path"] = "not-in-source.png"
        write(b, value)
    else:
        value = json.loads(datasets["B"].read_text())
        if mismatch == "device":
            value["cameras"]["B"][0]["device_id"] = "synthetic-A"
        elif mismatch == "crop":
            value["physical_cameras"]["B"]["requested_sensor_crop"] = [256, 0, 1552, 1552]
        else:
            value["image_orientation"]["B"]["flip_y"] = False
        write(datasets["B"], value)
    with pytest.raises(ValueError):
        generate(inputs, tmp_path / "preview")
    assert not (tmp_path / "preview").exists()


def test_current_dataset_fingerprints_are_not_promoted_to_fit_time_verification(inputs, tmp_path):
    dataset_b = inputs[2]["B"]
    value = json.loads(dataset_b.read_text())
    value["operator_note"] = "Added after fitting; source view membership is unchanged."
    write(dataset_b, value)
    bundle = generate(inputs, tmp_path / "preview")
    provenance = bundle["provenance"]["B"]
    assert provenance["datasets"][0]["sha256"] == hashlib.sha256(dataset_b.read_bytes()).hexdigest()
    assert provenance["source_binding"]["fit_time_dataset_hash_verified"] is False
    assert provenance["source_binding"]["verified_fit_dataset"] is None

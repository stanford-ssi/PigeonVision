from copy import deepcopy
import math

import cv2
import numpy as np
import pytest

from pigeonvision.ground.projection import project_ray, validate_calibration


def camera():
    return {"image_size": [2064, 1552], "K": [[610, 0, 1031.5], [0, 610, 775.5], [0, 0, 1]],
            "D": [.01, -.001, .0005, -.0004], "xi": 1.0,
            "R_camera_from_rig": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "crop": [256, 0, 1552, 1552], "output_size": [1552, 1552],
            "flip_x": False, "flip_y": False, "valid_radius_px": None,
            "max_theta_deg": 110}


def test_mei_matches_opencv_including_negative_z():
    c = camera()
    for angle in (0, 30, 80, 90, 100):
        ray = (math.sin(math.radians(angle)), 0, math.cos(math.radians(angle)))
        pixels, _ = cv2.omnidir.projectPoints(np.array([[ray]], np.float64),
                                              np.zeros(3), np.zeros(3), np.array(c["K"], np.float64),
                                              c["xi"], np.array(c["D"], np.float64))
        actual = project_ray(c, ray)
        assert actual is not None
        assert np.allclose(actual, pixels[0, 0] - [256, 0], atol=1e-8)
    assert project_ray(c, (math.sin(math.radians(120)), 0, math.cos(math.radians(120)))) is None


def test_crop_resize_flip_rotation_and_mask():
    c = camera()
    centre = project_ray(c, (0, 0, 1))
    assert centre == (775.5, 775.5)
    c["output_size"] = [776, 776]
    point = project_ray(c, (.2, -.1, 1))
    c["flip_x"] = c["flip_y"] = True
    assert np.allclose(project_ray(c, (.2, -.1, 1)), [775 - point[0], 775 - point[1]])
    c["valid_radius_px"] = 5
    assert project_ray(c, (.2, -.1, 1)) is None
    c["R_camera_from_rig"] = [[-1, 0, 0], [0, 1, 0], [0, 0, -1]]
    assert project_ray(c, (0, 0, -1)) is not None
    assert project_ray(c, (0, 0, 1)) is None


def test_calibration_validation():
    bundle = {"schema_version": 1, "model": "mei", "cameras": {"A": camera(), "B": camera()}}
    assert validate_calibration(bundle) is bundle
    invalid = deepcopy(bundle)
    invalid["cameras"]["A"]["crop"] = [1000, 0, 1552, 1552]
    with pytest.raises(ValueError, match="crop"):
        validate_calibration(invalid)
    invalid = deepcopy(bundle)
    invalid["cameras"]["B"]["K"][0][0] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        validate_calibration(invalid)


def test_mei_second_branch_is_rejected():
    c = camera()
    c["xi"] = 2.0
    c["max_theta_deg"] = 170
    assert project_ray(c, (1, 0, -.5)) is not None
    assert project_ray(c, (.2, 0, -1)) is None


def colour_bundle():
    return {"schema_version": 1, "model": "mei", "cameras": {
        name: {**camera(), "provenance": {"device_id": f"device-{name}"}} for name in ("A", "B")},
        "display_colour": {"schema_version": 1, "method": "display_rgb_gain", "reference_camera": "A",
                           "gains": {"A": [1, 1, 1], "B": [1.05, .98, 1.08]},
                           "devices": {"A": "device-A", "B": "device-B"}}}


def test_display_colour_is_bound_to_physical_cameras():
    bundle = colour_bundle()
    assert validate_calibration(bundle) is bundle
    bundle["display_colour"]["devices"]["B"] = "device-A"
    with pytest.raises(ValueError, match="physical cameras"):
        validate_calibration(bundle)


@pytest.mark.parametrize("gains", [[float("nan"), 1, 1], [True, 1, 1], [.49, 1, 1], [1, 2.01, 1], [1, 1], None])
def test_display_colour_rejects_invalid_gains(gains):
    bundle = colour_bundle()
    bundle["display_colour"]["gains"]["B"] = gains
    with pytest.raises(ValueError, match="gains"):
        validate_calibration(bundle)


def test_display_colour_preserves_reference_camera():
    bundle = colour_bundle()
    bundle["display_colour"]["gains"]["A"] = [.9, 1, 1]
    with pytest.raises(ValueError, match="reference camera"):
        validate_calibration(bundle)


def test_neutral_target_can_balance_both_cameras():
    bundle = colour_bundle()
    colour = bundle["display_colour"]
    colour.update(reference_camera=None, reference_target="colorchecker_neutrals")
    colour["gains"]["A"] = [1.1, 1, .99]
    assert validate_calibration(bundle) is bundle
    colour["reference_target"] = "unidentified_surface"
    with pytest.raises(ValueError, match="reference"):
        validate_calibration(bundle)
    colour.update(reference_camera="A", reference_target="colorchecker_neutrals")
    with pytest.raises(ValueError, match="one colour reference"):
        validate_calibration(bundle)


@pytest.mark.parametrize("field,value", [("method", "unknown"), ("reference_camera", "C"), ("gains", None), ("devices", None)])
def test_display_colour_rejects_invalid_profile(field, value):
    bundle = colour_bundle()
    bundle["display_colour"][field] = value
    with pytest.raises(ValueError):
        validate_calibration(bundle)


def test_neutral_preview_accepts_headroom_and_independent_strengths():
    bundle = colour_bundle()
    bundle["display_colour"].update(reference_camera=None, reference_target="colorchecker_neutrals",
                                    common_headroom_scale=.88, camera_strengths={"A": 1, "B": .5})
    assert validate_calibration(bundle) is bundle
    bundle["display_colour"].update(reference_camera="A", reference_target=None)
    with pytest.raises(ValueError, match="identity headroom"):
        validate_calibration(bundle)


@pytest.mark.parametrize("field,value", [
    ("common_headroom_scale", True), ("common_headroom_scale", None),
    ("common_headroom_scale", float("nan")), ("common_headroom_scale", .49),
    ("common_headroom_scale", 1.01), ("camera_strengths", None),
    ("camera_strengths", {"A": .5}), ("camera_strengths", {"A": 0, "B": True}),
    ("camera_strengths", {"A": -1, "B": 0}), ("camera_strengths", {"A": 0, "B": 1.01}),
    ("camera_strengths", {"A": float("inf"), "B": 1}),
])
def test_colour_strength_controls_reject_invalid_values(field, value):
    bundle = colour_bundle()
    bundle["display_colour"][field] = value
    with pytest.raises(ValueError):
        validate_calibration(bundle)

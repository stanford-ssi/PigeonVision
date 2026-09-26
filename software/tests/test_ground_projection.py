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

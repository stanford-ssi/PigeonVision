import math

import numpy as np
import pytest

from pigeonvision.calibration import _held_out, error_stats, project_mei, validate_rig


def camera():
    return {"image_size": [2064, 1552], "K": [[500., 0., 1032.], [0., 500., 776.], [0., 0., 1.]],
            "D": [0., 0., 0., 0.], "xi": 1., "R_camera_from_rig": np.eye(3).tolist(),
            "crop": [256, 0, 1552, 1552], "output_size": [1552, 1552], "flip_x": False, "flip_y": False,
            "max_theta_deg": 110, "valid_radius_px": None}


def test_mei_preserves_more_than_hemisphere_and_masks_domain():
    c = camera()
    angles = np.deg2rad([0, 90, 100, 120])
    rays = np.c_[np.sin(angles), np.zeros(4), np.cos(angles)]
    points, valid = project_mei(rays, c)
    assert np.allclose(points[0], [776, 776])
    assert valid.tolist() == [True, True, True, False]
    assert points[2, 0] > points[1, 0]


def test_projection_matches_opencv_omnidir_full_sensor():
    import cv2
    c = camera()
    c["D"] = [.03, -.002, .001, -.002]
    rays = np.asarray([[.1, -.2, 1.], [1, .2, -.1], [-.4, .5, 1.]], dtype=float)
    expected, _ = cv2.omnidir.projectPoints(rays.reshape(-1, 1, 3), np.zeros((3, 1)), np.zeros((3, 1)),
                                          np.array(c["K"]), c["xi"], np.array(c["D"]))
    actual, _ = project_mei(rays, c, output=False)
    assert np.allclose(actual, expected.reshape(-1, 2), atol=1e-8)


def test_crop_flip_order_and_rotation():
    c = camera()
    c["flip_y"] = True
    c["output_size"] = [776, 776]
    points, valid = project_mei(np.array([[0., 0., 1.]]), c)
    assert np.allclose(points[0], [387.75, 387.25])
    assert valid[0]
    c["R_camera_from_rig"] = np.diag([-1, 1, -1]).tolist()
    _, valid = project_mei(np.array([[0., 0., 1.], [0., 0., -1.]]), c)
    assert valid.tolist() == [False, True]


def test_held_out_pose_recovers_known_projection_with_frozen_lens():
    import cv2
    c = camera()
    points = np.asarray([[x * .03, y * .03, 0] for y in range(6) for x in range(7)], dtype=float).reshape(-1, 1, 3)
    K, D = np.array(c["K"]), np.array(c["D"])
    image, _ = cv2.omnidir.projectPoints(points, np.array([.1, -.3, .05]), np.array([-.1, -.08, .4]), K, c["xi"], D)
    errors, theta = _held_out(cv2, points, image, K, D, c["xi"])
    assert max(errors) < 1e-5
    assert theta.max() > 0


def test_bad_rotation_and_unknown_errors():
    c = camera()
    c["R_camera_from_rig"][0][0] = -1
    with pytest.raises(ValueError, match="proper orthonormal"):
        validate_rig(c)
    assert error_stats([])["rms_px"] is None


def test_xi_above_one_rejects_noninjective_back_branch():
    c = camera()
    c["xi"] = 2.0
    c["max_theta_deg"] = 179
    c["K"][0][0] = c["K"][1][1] = 100
    theta = np.deg2rad([115, 125])
    rays = np.c_[np.sin(theta), np.zeros(2), np.cos(theta)]
    _, valid = project_mei(rays, c)
    assert valid.tolist() == [True, False]

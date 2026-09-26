import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from pigeonvision.calibration import BoardDetector, calibrate


PRESET = Path(__file__).parents[1] / "calibration" / "boards" / "dfvision-q18-400-20.json"


def checker_image(square_px=30, border=50):
    image = np.full((18 * square_px + 2 * border,) * 2, 255, np.uint8)
    for y in range(18):
        for x in range(18):
            if (x + y) % 2 == 0:
                image[border + y * square_px:border + (y + 1) * square_px,
                      border + x * square_px:border + (x + 1) * square_px] = 0
    return image


def test_q18_full_grid_metric_coordinates_and_subpixel_detection():
    detector = BoardDetector(json.loads(PRESET.read_text()))
    obj, image, ids = detector.detect(checker_image())
    assert obj.shape == (289, 1, 3)
    assert image.shape == (289, 1, 2)
    assert ids.tolist() == list(range(289))
    assert np.allclose(obj[0, 0], [0, 0, 0])
    assert np.allclose(obj[-1, 0], [.32, .32, 0])
    # Grid orientation is deliberately unspecified; compare corner locations.
    expected = np.array([[50 + x * 30 - .5, 50 + y * 30 - .5]
                         for y in range(1, 18) for x in range(1, 18)])
    distances = np.linalg.norm(image[:, 0, None, :] - expected[None, :, :], axis=2)
    assert distances.min(axis=1).max() < .2
    assert detector.corner_id_scope == "detector_local_per_view"
    assert "90/180/270" in detector.warnings[0]


def test_q18_detects_perspective_and_rejects_clipped_grid():
    image = checker_image()
    source = np.float32([[0, 0], [639, 0], [639, 639], [0, 639]])
    destination = np.float32([[85, 25], [605, 70], [625, 615], [35, 575]])
    projected = cv2.warpPerspective(image, cv2.getPerspectiveTransform(source, destination),
                                    (660, 660), borderValue=255)
    detector = BoardDetector(json.loads(PRESET.read_text()))
    assert detector.detect(projected) is not None
    clipped = image.copy()
    clipped[:, :150] = 255
    assert detector.detect(clipped) is None


def test_charuco_default_keeps_board_ids():
    board = {"squares_x": 7, "squares_y": 9, "square_length_m": .025, "marker_length_m": .0175}
    detector = BoardDetector(board)
    rendered = detector.board.generateImage((700, 900), marginSize=30)
    obj, image, ids = detector.detect(rendered)
    assert detector.type == "charuco"
    assert len(ids) == 48
    assert detector.corner_id_scope == "board_corner_id"
    assert np.allclose(obj[:, 0], detector.board.getChessboardCorners()[ids])
    assert image.shape == (48, 1, 2)
    assert detector.warnings == []


@pytest.mark.parametrize("change", [
    {"type": "circles"}, {"squares_x": 17.5}, {"squares_y": True},
    {"square_length_m": 0}, {"square_length_m": float("nan")},
])
def test_invalid_board_geometry_rejected(change):
    board = json.loads(PRESET.read_text()) | change
    with pytest.raises(ValueError):
        BoardDetector(board)


def test_calibrate_checkerboard_keeps_evidence_and_requires_real_view_count(tmp_path):
    canonical = checker_image()
    assert cv2.imwrite(str(tmp_path / "A.png"), cv2.flip(canonical, 0))
    board = json.loads(PRESET.read_text())
    dataset = {"board": board, "image_orientation": {"A": {"flip_x": False, "flip_y": True}},
               "cameras": {"A": [{"path": "A.png", "split": "fit", "region": "centre"}], "B": []}}
    rig = {"cameras": {"A": {"image_size": [640, 640], "crop": [0, 0, 640, 640],
                               "output_size": [640, 640], "flip_x": False, "flip_y": True,
                               "R_camera_from_rig": np.eye(3).tolist()}}}
    (tmp_path / "dataset.json").write_text(json.dumps(dataset))
    (tmp_path / "rig.json").write_text(json.dumps(rig))
    with pytest.raises(ValueError, match="eight accepted fit views"):
        calibrate(tmp_path / "dataset.json", tmp_path / "rig.json", tmp_path / "result")
    evidence = json.loads((tmp_path / "result" / "observations.json").read_text())
    observation = evidence["observations"][0]
    assert observation["corner_count"] == 289
    assert observation["board_type"] == "checkerboard"
    assert observation["corner_id_scope"] == "detector_local_per_view"
    expected = BoardDetector(board).detect(canonical)[1].reshape(-1, 2)
    assert np.allclose(observation["canonical_image_points"], expected)
    assert not (tmp_path / "result" / "calibration.json").exists()

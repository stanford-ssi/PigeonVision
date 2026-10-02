"""Verify target dimensions, genuine OpenCV markers and empty-data semantics."""
import importlib.util
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "calibration_target.py"
spec = importlib.util.spec_from_file_location("calibration_target", SCRIPT)
target = importlib.util.module_from_spec(spec)
spec.loader.exec_module(target)
NS = {"svg": "http://www.w3.org/2000/svg"}


def render_board(svg: str, manifest: dict):
    """Rasterize only emitted vector rectangles, independent of OpenCV drawBoard."""
    root = ET.fromstring(svg)
    scale = 12  # 300 pixels/square; 35 pixels/marker module at default sizes.
    width, height = manifest["board_size_mm"]
    margin = 50
    image = np.full((round(height * scale) + margin * 2, round(width * scale) + margin * 2), 255, np.uint8)
    group = root.find("svg:g[@id='charuco-board']", NS)
    for rect in group.findall("svg:rect", NS):
        x, y, w, h = (float(rect.attrib[k]) for k in ("x", "y", "width", "height"))
        left, top = round(x * scale) + margin, round(y * scale) + margin
        right, bottom = round((x + w) * scale) + margin, round((y + h) * scale) + margin
        image[top:bottom, left:right] = 0
    return image


@pytest.mark.parametrize("paper,dimensions", [("letter", (215.9, 279.4)), ("a4", (210, 297))])
def test_print_dimensions_and_opencv_detection(paper, dimensions):
    svg, manifest = target.target_svg(paper=paper)
    root = ET.fromstring(svg)
    assert root.attrib["width"] == target.number(dimensions[0]) + "mm"
    assert root.attrib["height"] == target.number(dimensions[1]) + "mm"
    assert root.attrib["viewBox"] == f"0 0 {target.number(dimensions[0])} {target.number(dimensions[1])}"
    assert manifest["board_size_mm"] == [175, 225]
    assert manifest["board"]["square_length_m"] == .025
    assert manifest["board"]["marker_length_m"] == .0175
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_100)
    board = cv2.aruco.CharucoBoard((7, 9), .025, .0175, dictionary)
    corners, ids, markers, marker_ids = cv2.aruco.CharucoDetector(board).detectBoard(render_board(svg, manifest))
    assert len(ids) == 48
    assert len(marker_ids) == 31
    assert sorted(marker_ids.ravel().tolist()) == manifest["marker_ids"]
    assert sorted(ids.ravel().tolist()) == list(range(48))


def test_empty_template_compatible_with_fitter_and_no_overwrite(tmp_path):
    output = tmp_path / "target"
    target.generate(output)
    dataset = json.loads((output / "dataset.template.json").read_text())
    assert dataset["cameras"] == {"A": [], "B": []}
    assert dataset["image_orientation"] == {"A": {"flip_x": None, "flip_y": None}, "B": {"flip_x": None, "flip_y": None}}
    assert dataset["minimum_accepted_views_per_camera"] == {"fit": 8, "validation": 3}
    assert set(dataset["board"]) == {"squares_x", "squares_y", "square_length_m", "marker_length_m", "dictionary"}
    assert not (output / "calibration.json").exists()
    assert not (output / "rig.json").exists()
    with pytest.raises(ValueError, match="not empty"):
        target.generate(output)


@pytest.mark.parametrize("kwargs", [{"square_mm": 40}, {"marker_mm": 26},
                                    {"dictionary_name": "DICT_NOT_REAL"}, {"squares_x": 2}])
def test_invalid_targets_are_rejected(kwargs):
    with pytest.raises(ValueError):
        target.target_svg(**kwargs)

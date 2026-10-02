#!/usr/bin/env python3
"""Generate a printable, dimensioned OpenCV ChArUco target and empty dataset.

Run with software/.venv/bin/python. All marker bits, IDs and placements come
from the same OpenCV ChArUco API used by pigeonvision.calibration.calibrate.
This generates a target, never calibration measurements or fitted parameters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape

import cv2


PAPERS_MM = {"letter": (215.9, 279.4), "a4": (210.0, 297.0)}


def number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def black_runs(image):
    """Convert actual OpenCV marker modules into compact horizontal SVG runs."""
    for y, row in enumerate(image):
        start = None
        for x, value in enumerate(row):
            if value == 0 and start is None:
                start = x
            if start is not None and (value != 0 or x == len(row) - 1):
                end = x if value != 0 else x + 1
                yield start, y, end - start
                start = None


def target_svg(*, paper: str = "letter", squares_x: int = 7, squares_y: int = 9,
               square_mm: float = 25, marker_mm: float = 17.5,
               dictionary_name: str = "DICT_4X4_100") -> tuple[str, dict]:
    if paper not in PAPERS_MM:
        raise ValueError("Paper must be letter or a4")
    if squares_x < 3 or squares_y < 3 or not 0 < marker_mm < square_mm:
        raise ValueError("Use at least 3×3 squares and 0 < marker size < square size")
    if not dictionary_name.startswith("DICT_") or not hasattr(cv2.aruco, dictionary_name):
        raise ValueError(f"Unknown OpenCV ArUco dictionary: {dictionary_name}")
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
    if squares_x * squares_y // 2 > len(dictionary.bytesList):
        raise ValueError("The selected dictionary has too few distinct marker IDs")
    page_w, page_h = PAPERS_MM[paper]
    board_w, board_h = squares_x * square_mm, squares_y * square_mm
    origin_x, origin_y = (page_w - board_w) / 2, (page_h - board_h) / 2
    if origin_x < 12 or origin_y < 23:
        raise ValueError("Board does not fit with printable margins and measurement gauges; reduce square size or square count")
    board = cv2.aruco.CharucoBoard((squares_x, squares_y), square_mm / 1000,
                                  marker_mm / 1000, dictionary)
    # This coarse reference supplies the actual checker-square convention. No
    # hand-assumed legacy parity or marker ordering is mixed into the target.
    reference = board.generateImage((squares_x * 100, squares_y * 100), marginSize=0, borderBits=1)
    board_info = {"squares_x": squares_x, "squares_y": squares_y,
                  "square_length_m": square_mm / 1000,
                  "marker_length_m": marker_mm / 1000,
                  "dictionary": dictionary_name}
    manifest = {"schema_version": 1, "kind": "printable_target_not_measurements",
                "paper": paper, "page_size_mm": [page_w, page_h],
                "board": board_info, "board_origin_mm": [origin_x, origin_y],
                "board_size_mm": [board_w, board_h], "marker_border_bits": 1,
                "marker_ids": board.getIds().ravel().tolist(),
                "legacy_pattern": bool(board.getLegacyPattern()),
                "opencv_version": cv2.__version__, "measurement_gauges_mm": [100, 100]}
    rects = []
    def rectangle(x, y, width, height, **attrs):
        extra = " ".join(f'{key.replace("_", "-")}="{escape(str(value))}"' for key, value in attrs.items())
        rects.append(f'<rect x="{number(x)}" y="{number(y)}" width="{number(width)}" height="{number(height)}" {extra}/>')

    for y in range(squares_y):
        for x in range(squares_x):
            if reference[y * 100 + 2, x * 100 + 2] == 0:
                rectangle(x * square_mm, y * square_mm, square_mm, square_mm, data_kind="square")
    modules = dictionary.markerSize + 2
    for ident, corners in zip(board.getIds().ravel(), board.getObjPoints()):
        # OpenCV board coordinates are metres. Rounding removes only float32
        # representation noise (<0.00001 mm), not printed geometric detail.
        x, y = (round(float(v) * 1000, 5) for v in corners[0, :2])
        marker = cv2.aruco.generateImageMarker(dictionary, int(ident), modules, borderBits=1)
        module_mm = marker_mm / modules
        for rx, ry, length in black_runs(marker):
            rectangle(x + rx * module_mm, y + ry * module_mm,
                      length * module_mm, module_mm, data_kind="marker", data_marker_id=int(ident))

    scale_x, scale_y = (page_w - 100) / 2, page_h - 13
    vertical_x, vertical_y = 8, (page_h - 100) / 2
    title = f"PigeonVision / ChArUco {squares_x} × {squares_y}"
    subtitle = f"{dictionary_name} · square {number(square_mm)} mm · marker {number(marker_mm)} mm"
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{number(page_w)}mm" height="{number(page_h)}mm" viewBox="0 0 {number(page_w)} {number(page_h)}">
  <title>{escape(title)}</title>
  <desc>Print at 100 percent actual size, with no fit-to-page scaling. Measure both 100 mm gauges and several squares after printing. Target generation is not a calibration.</desc>
  <metadata>{escape(json.dumps(manifest, separators=(",", ":")))}</metadata>
  <rect width="{number(page_w)}" height="{number(page_h)}" fill="white"/>
  <g fill="black" font-family="Arial,Helvetica,sans-serif" text-anchor="middle">
    <text x="{number(page_w / 2)}" y="9" font-size="4">{escape(title)}</text>
    <text x="{number(page_w / 2)}" y="15" font-size="2.5">{escape(subtitle)}</text>
    <text x="{number(page_w / 2)}" y="20" font-size="2.3">ACTUAL SIZE / 100% · NO FIT TO PAGE · KEEP FLAT</text>
  </g>
  <g id="charuco-board" fill="black" shape-rendering="crispEdges" transform="translate({number(origin_x)} {number(origin_y)})">
    {chr(10).join(rects)}
  </g>
  <g stroke="black" stroke-width="0.25" fill="none">
    <path id="horizontal-gauge" d="M {number(scale_x)} {number(scale_y)} h 100 M {number(scale_x)} {number(scale_y - 2)} v 4 M {number(scale_x + 100)} {number(scale_y - 2)} v 4"/>
    <path id="vertical-gauge" d="M {number(vertical_x)} {number(vertical_y)} v 100 M {number(vertical_x - 2)} {number(vertical_y)} h 4 M {number(vertical_x - 2)} {number(vertical_y + 100)} h 4"/>
  </g>
  <g fill="black" font-family="Arial,Helvetica,sans-serif" font-size="2.5" text-anchor="middle">
    <text x="{number(page_w / 2)}" y="{number(scale_y - 3)}">100 mm — verify with a ruler</text>
    <text transform="translate(4 {number(page_h / 2)}) rotate(-90)">100 mm — verify with a ruler</text>
    <text x="{number(page_w / 2)}" y="{number(page_h - 5)}">Measure several squares in both directions before collecting images.</text>
  </g>
</svg>
'''
    return svg, manifest


def collection_guide(manifest: dict) -> str:
    board = manifest["board"]
    return f'''# Real camera calibration collection

This directory contains a printable target and an **empty** dataset template. It does not contain measured observations, fitted lenses, or rig alignment.

## Print and measure

Print `target.svg` on {manifest["paper"].upper()} at **100% / actual size**, with browser headers and footers disabled. Do not use “fit to page”. If your print path rescales SVG, correct that before collecting images. The board is {board["squares_x"]} × {board["squares_y"]} squares; each square should measure {board["square_length_m"] * 1000:g} mm and each marker's outer black border {board["marker_length_m"] * 1000:g} mm.

Measure both 100 mm gauges and several square spacings horizontally and vertically with a ruler. Mount the sheet flat on a rigid backing; do not stretch it or leave it curved. If the scale differs uniformly, use the actual measured square and marker dimensions in metres in the dataset. If the two directions scale differently, reprint. Keep the target manifest with the photographs.

## Save original sensor images

Use full 2064 × 1552 camera frames for the current IMX900 bench. Preserve A/B physical identities, focus position, capture orientation, actual sensor crop and output dimensions from capture metadata. Do not change focus between collection and use.

Prefer sharp full-sensor stills; decoded frames from a saved original transport or native MKV can be saved as PNG for the initial fit. Confirm their dimensions and check marker edges for blur or compression artifacts. A PNG extracted from H.264 retains that compression history. **Do not use viewer screenshots**: focus zoom, drag, display rotation, resizing and panorama rendering are not the original sensor image.

Copy `dataset.template.json` to `dataset.json`. Set `image_orientation.A` and `.B` to the actual recorded flip_x/flip_y booleans from the capture session. The viewer's optional 180° display rotation does not change recorded camera orientation. The fitter unflips images into canonical sensor coordinates before detecting corners.

Add an entry only after that real file exists, with its path relative to `dataset.json`. Example format:

```json
{{"path":"images/A/fit-001.png","split":"fit","region":"centre"}}
```

## Capture varied views of each fixed camera

Hold the board still for every selected frame. Fill a useful part of the image while keeping individual marker cells readable. With these very wide lenses the board must often be close; choose distance by visible marker detail, not a guessed focal length. Capture different distances and tilts as well as image positions.

- Fit set: aim for 20–30 distinct poses per camera spanning centre, upper/lower/left/right edges and both overlap strips. At least **eight accepted** fit views per camera are required by the current fitter.
- Held-out set: choose 6–10 additional poses, including edges and both seams. At least **three accepted** held-out views per camera are required. Reserve these before fitting; do not duplicate a fit image or select adjacent frames of the same unmoved pose as supposedly independent evidence.
- Use region labels such as `centre`, `top`, `bottom`, `left`, `right`, `seam-left` and `seam-right`. Labels beginning with `seam` are included in the fitter's seam report.
- Include board rays beyond 90° from the lens axis where the installed camera actually retains coverage. A low central error alone cannot validate the overlap. Partial boards are usable only when at least eight ChArUco corners remain detectable.

Keep rejected/blurry captures separately with their reasons. The fitter retains detection and initialization rejections, so do not replace failed evidence with invented observations.

## Fit and assess

The current `pv calibrate` command also needs a `rig.json` containing each camera's physical ID, full sensor size, crop/output transform, recorded flips and a supplied proper `R_camera_from_rig` rotation. Rig axes are +Z forward through A, +X right, +Y down. The target generator intentionally does not fabricate these rotations or a usable rig calibration.

From the repository root, once the measured dataset and rig file exist:

```sh
software/.venv/bin/pv calibrate --dataset PATH/dataset.json --rig PATH/rig.json --output output/calibration/bench-001
```

Inspect `observations.json` and `calibration.json`: retained fit views, held-out centre/edge/seam residuals, actual angular coverage, and rig-alignment status. The current report targets ≤1 pixel held-out RMS and ≤2 pixels at the 95th percentile; meeting these numerical thresholds does not establish mechanical retention, shutter synchronization or complete overlap coverage.

For the mounted pair, independently capture shared board/landmarks in both overlap strips to establish relative orientation. Validate the stitch on distant stationary features first, then nearby objects. The cameras' separated viewpoints cause depth-dependent parallax that an infinity panorama cannot eliminate.
'''


def generate(output: Path, **options) -> dict:
    svg, manifest = target_svg(**options)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory is not empty; choose a new directory to preserve existing targets and observations")
    output.mkdir(parents=True, exist_ok=True)
    template = {"schema_version": 1, "board": manifest["board"],
                "image_orientation": {name: {"flip_x": None, "flip_y": None} for name in "AB"},
                "cameras": {"A": [], "B": []},
                "collection_status": "not_collected", "minimum_accepted_views_per_camera": {"fit": 8, "validation": 3}}
    (output / "target.svg").write_text(svg, encoding="utf-8")
    manifest["target_sha256"] = hashlib.sha256(svg.encode()).hexdigest()
    (output / "target.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "dataset.template.json").write_text(json.dumps(template, indent=2) + "\n")
    (output / "COLLECTION.md").write_text(collection_guide(manifest), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--paper", choices=PAPERS_MM, default="letter")
    parser.add_argument("--squares-x", type=int, default=7)
    parser.add_argument("--squares-y", type=int, default=9)
    parser.add_argument("--square-mm", type=float, default=25)
    parser.add_argument("--marker-mm", type=float, default=17.5)
    parser.add_argument("--dictionary", dest="dictionary_name", default="DICT_4X4_100")
    args = parser.parse_args(argv)
    try:
        result = generate(**vars(args))
    except (ValueError, OSError, cv2.error) as exc:
        parser.exit(2, f"calibration_target: {exc}\n")
    print(f"Generated {args.output / 'target.svg'} ({args.paper}, {result['board_size_mm'][0]:g} × {result['board_size_mm'][1]:g} mm board)")
    print(f"Empty dataset: {args.output / 'dataset.template.json'}; instructions: {args.output / 'COLLECTION.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

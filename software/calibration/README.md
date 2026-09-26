# Real lens calibration with the DFVision board

The supplied Q18-400-20 is a checkerboard with **18 × 18 squares at 20 mm pitch**,
a 360 × 360 mm pattern, and 400 × 400 mm overall dimensions. That produces
**17 × 17 = 289 inner corners**; OpenCV takes inner-corner counts, while our
dataset takes square counts. These are nominal manufacturer dimensions; confirm
the model label and physical pitch on the actual board.
[Manufacturer specification, Q18 row](https://www.dfoptic.com/in-stock-standards-product/152.html).

Use [boards/dfvision-q18-400-20.json](boards/dfvision-q18-400-20.json) as the dataset's
`board` object. No ArUco dictionary or marker dimensions apply to this board.
Existing datasets without `board.type` continue to mean ChArUco.

## Collect original images

1. Finish focusing both lenses, then keep their focus, aperture, mounting and
   camera identity fixed. Record the session and lens settings. Changing focus
   after collection invalidates those intrinsic measurements.
2. Record the original full-sensor **2064 × 1552** streams. Extract PNG frames from
   the saved transport or per-camera recording. Do not calibrate from browser
   screenshots, viewer zoom/rotation, resized images, or a cropped preview. H.264
   decoded images retain compression artifacts, even when saved as PNG.
3. Keep the whole checkerboard and its surrounding border visible, sharp and
   unobstructed. Hold each pose still before choosing a frame. Either move the board
   or keep the heavy board fixed and move the entire rigid camera/rocket assembly.
   Vary their relative distances, tilts and positions across each camera's image: centre, top, bottom,
   left, right and the two seam directions. Avoid reflections and motion blur.
   Change pose rather than collecting adjacent frames of the same stationary board.
   Keeping the board stationary and moving the entire rocket rig is equally
   valid: translate and rotate the mounted assembly without adjusting either lens
   or camera mount. Hold each new pose for 2–3 seconds when sampling once per
   second; the collector tests one frame in each interval, which can otherwise
   land during motion.
4. Aim for **20–30 distinct fit poses and 6–10 separate held-out poses per camera**.
   The fitter refuses fewer than **eight accepted fit views and three accepted
   held-out views** per camera. These counts alone do not establish angular coverage.
   Keep held-out poses independent; do not put near-identical frames from a static
   hold in both splits. A/B may require separate board positions for lens fitting.
5. Include edge/seam poses only while the entire grid remains visible and detected.
   This checkerboard detector requires all 289 inner corners and does not support
   partial-board inference. A central-board fit does not validate >90° rays or a
   360° seam. Preserve missed detections and report the remaining coverage gap.

The detector uses OpenCV's subpixel `findChessboardCornersSB`, with exhaustive
search and accuracy refinement. The SB detector needs a complete ordered grid;
it does not attach persistent physical identities to checkerboard corners.
[OpenCV detector documentation](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).

## Dataset and orientation

Extract candidates offline from a copy of a native session containing `session.json`,
`frames.jsonl`, `segments.jsonl`, and finalized per-camera MKV files:

```sh
software/.venv/bin/python software/tools/calibration_frames.py \
  --session output/sessions/calibration-fit-001 \
  --board software/calibration/boards/dfvision-q18-400-20.json \
  --output output/calibration/fit-candidates-001 \
  --interval-seconds 1 --split fit --require-board
```

Use a separate recording, output directory, and explicit `--split validation`
for held-out poses. The collector samples at most one frame per camera in each
interval on the original common timeline. `--require-board` retains only a
complete 289-corner detection; omitting it saves unchecked candidates for manual
inspection. Optional `--region` labels the collected poses; its default is
`unlabelled`. Inspect diversity and correct region labels before fitting.

The output contains original-orientation PNGs, `dataset.json`, and
`collection-report.json`. Each image retains the source segment/hash, container
PTS/timebase, original capture metadata/common PTS, device ID and PNG hash.
Timestamp matching allows only the MKV timebase's rounding uncertainty; ambiguous
metadata and unknown/cropped geometry are rejected. Missing indexed segments,
unfinalized segments, and unindexed MKVs are reported explicitly, so a partial
copy is not described as a complete archive. Existing output directories are
never overwritten. This command does not access the Pi or generate a rig or fit.

Each camera needs a list of real image paths relative to the dataset JSON, an
explicit `fit` or `validation` split, and a useful region label. Labels beginning
with `seam` are reported separately. For example, after those image files exist:

```json
{
  "board": {"type": "checkerboard", "squares_x": 18, "squares_y": 18, "square_length_m": 0.02},
  "image_orientation": {
    "A": {"flip_x": false, "flip_y": true},
    "B": {"flip_x": false, "flip_y": true}
  },
  "cameras": {
    "A": [{"path": "images/A/fit-001.png", "split": "fit", "region": "centre"}],
    "B": [{"path": "images/B/validation-001.png", "split": "validation", "region": "seam-left"}]
  }
}
```

The example is incomplete and its flip values are illustrative: use the actual
capture session's booleans, never the browser's independent 180° view setting.
The fitter reverses capture flips before detection so `K` and `D` describe the
unflipped full sensor. Keep original PTS, pair offset, capture dimensions, actual
sensor crop and physical device IDs alongside extracted frames.

## Fit and inspect evidence

The current fitter requires an explicit rig JSON with each camera's full-sensor
size, stream crop/output size/flips and `R_camera_from_rig`. Unrelated board poses
can determine independent lens intrinsics but cannot measure relative camera
alignment. Do not invent a measured rotation or set `rig_alignment_status` to a
verified value merely to enable the panorama.

From the repository root, once the real dataset and rig description exist:

```sh
software/.venv/bin/pv calibrate --dataset output/calibration/dataset.json \
  --rig output/calibration/rig.json --output output/calibration/fit-01
```

Read `observations.json` for rejected detections and retained views. Inspect
held-out RMS/p95 errors, per-region/seam errors and angular coverage in
`calibration.json`. Current error targets are ≤1 px RMS and ≤2 px p95; satisfying
them on central poses does not qualify a seam. `max_theta_deg` is only the largest
observed held-out ray, not evidence that every ray inside that angle was tested.

## Relative orientation remains a separate measurement

The 18 × 18 square pattern has unresolved 90°/180°/270° corner-order ambiguity.
The detector's first corner and axes can change between views. Per-view pose
fitting absorbs this for independent intrinsic calibration. **Do not equate
the same local corner index in A and B for extrinsic calibration.**

For paired extrinsics, mark a physical board origin and two axis directions on
the outer margin without covering the pattern, record enough context to identify
them in both images, and explicitly verify/reorder corner correspondences. The
current detector does not automatically read those marks. Capture simultaneous
common-board views in actual overlap where both cameras see the same printed
face and full grid. Keep the A/B timestamp difference and move slowly or hold the
board still; nearest timestamps do not prove hardware synchronization.

If the mounted geometry cannot present a complete grid to both cameras, record
that limitation and use a separately designed alignment measurement. Nominal
back-to-back rotations are an initial geometric assumption, not a measured stitch.
Lens intrinsics plus relative rotations support distant-scene alignment; the
physical separation of the cameras still produces depth-dependent parallax.

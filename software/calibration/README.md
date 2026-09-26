# Lens calibration and the initial panorama

The bench has preliminary Mei lens fits and a nominal opposed-camera panorama;
held-out lens/seam accuracy and measured rig alignment remain incomplete. See the
[bench evidence](../bench-notes/2026-09-25.md) for results. This guide covers
collection, independent lens fitting and the initial preview; detailed paired
alignment is in [rig-alignment.md](rig-alignment.md).

## Board and collection

The DFVision **Q18-400-20** has 18×18 squares at nominal 20 mm pitch: a 360 mm
pattern on a 400 mm board, giving **17×17 = 289 inner corners**. Confirm the label
and physical pitch. Use [the board preset](boards/dfvision-q18-400-20.json) as the
dataset's `board` object; it takes square counts, while OpenCV takes inner-corner
counts. No ArUco dimensions apply. Datasets without `board.type` retain ChArUco
behavior. [Manufacturer specification](https://www.dfoptic.com/in-stock-standards-product/152.html).

1. Finish focus, then preserve focus/aperture, mounts and camera identity. Record
   settings; changing focus invalidates those intrinsic measurements.
2. Collect original **2064×1552** images from transport or native recordings,
   never resized/cropped previews or browser screenshots. Decoded H.264 retains
   compression artifacts even when saved as PNG.
3. Show the complete sharp, unobstructed grid and border. Vary distance, tilt and
   position across centre, edges and both seams; avoid reflections and blur.
   Move either the board or the entire rigid rocket without adjusting its mounts.
   Hold each pose 2–3 seconds for one-second sampling; only one frame per interval
   is tested. Adjacent frames from one hold are not distinct poses.
4. Aim for 20–30 fit poses and 6–10 independent held-out poses per camera. Normally
   the fitter requires eight accepted fit views and three held-out views. Do not
   split near-identical holds across fitting/validation; counts do not prove coverage.
5. Edge poses must still contain all 289 corners. The SB detector uses exhaustive,
   subpixel refinement and has no partial-board inference or persistent physical
   corner IDs. Preserve missed detections and coverage gaps. A central fit does
   not validate >90° rays or seams.
   [OpenCV detector documentation](https://docs.opencv.org/4.x/d9/d0c/group__calib3d.html).

## Extract and curate

From the repository root, use a local copy of `session.json`, `frames.jsonl`,
`segments.jsonl` and finalized A/B MKVs:

```sh
software/.venv/bin/python software/tools/calibration_frames.py \
  --session output/sessions/calibration-fit-001 \
  --board software/calibration/boards/dfvision-q18-400-20.json \
  --output output/calibration/fit-candidates-001 \
  --interval-seconds 1 --split fit --require-board
```

Use another recording/output with `--split validation` for held-out poses.
`--camera A` or `--camera B` avoids searching the opposite camera when collecting
one lens; default is both. `--require-board` keeps complete detections; omit it
for unchecked candidates. Set useful `--region` labels; default is `unlabelled`,
and labels beginning `seam` are reported separately. Curate diversity before fitting.

Output includes original-orientation PNGs, `dataset.json` and
`collection-report.json`. Records retain segment/PNG hashes, container PTS/timebase,
original capture metadata/common PTS, physical IDs and geometry. Matching allows
only MKV rounding uncertainty; ambiguous metadata and unknown/cropped geometry
are rejected. Missing indexed, unfinalized and unindexed segments are reported,
so a partial copy is not called complete. Existing outputs are refused. The
collector neither accesses the Pi nor fits lenses/alignment.

Each camera's image list needs paths relative to its dataset, explicit
`fit`/`validation` splits and region labels. Preserve `physical_cameras`,
`image_orientation`, per-image `device_id` and `capture_metadata` when merging
collections. Use actual capture `flip_x`/`flip_y`, not browser rotation; the
fitter reverses them before detection so intrinsics describe canonical unflipped
full-sensor pixels. Keep common timestamps/offsets and actual sensor crop.
Padding a cropped image back to full size is invalid.

For manual originals, each record instead needs `source_provenance`:
`kind:"manual_full_sensor"`, `camera_id`, `device_id`, `sensor_crop`, `flip_x`,
`flip_y` and a nonempty acquisition `note`. It must match the dataset's physical
camera description and cannot override contradictory extractor metadata.

## Fit and check lenses

Fit a lens independently; omit `--camera` to process every nonempty camera list:

```sh
software/.venv/bin/pv calibrate --intrinsics-only --camera B \
  --dataset output/calibration/dataset.json --output output/calibration/B-fit-01
```

`intrinsics.json` has `scope:"intrinsics_only"`, `rig_alignment_status:"unmeasured"`
and no camera-to-rig rotation; it cannot enable a panorama. IDs, dimensions,
crop/orientation and supplied image hashes are checked before fitting.

For a collecting-stage diagnostic, explicitly add `--allow-unvalidated`.
Eight accepted fit views are still required. Insufficient held-out evidence gives
`unvalidated_intrinsics`, null threshold results and no measured angular limit.
Training RMS is not validation. Once new-session observations enter a refit,
their earlier test against a frozen model no longer validates the new model.

Read `observations.json` for rejected/retained views and residuals; inspect
held-out RMS/p95, region/seam errors and coverage in the output bundle. Targets
are ≤1 px RMS and ≤2 px p95. Central accuracy does not qualify seams;
`max_theta_deg` is the largest observed held-out ray, not proof that all enclosed
rays were tested.

## Initial nominal opposed-camera preview

An initial demo can combine real lens fits with approximate operator geometry
before a measured rig solve. A is the reference; B rotates 180° about rig Y,
assuming parallel canonical +Y axes (zero relative roll). The example records an
approximate six-inch baseline; infinity projection **does not correct parallax**.

```sh
software/.venv/bin/python software/tools/nominal_rig_preview.py \
  --intrinsics-a PATH/A-intrinsics.json --intrinsics-b PATH/B-intrinsics.json \
  --dataset-b PATH/B-source-dataset.json --output output/nominal-preview \
  --baseline-m 0.1524 --geometry-source-date 2026-09-25 \
  --opposed --common-roll-assumed
```

Use the actual source date/separation. Normal intrinsics bind their source dataset
and hash; flat diagnostics need explicit datasets (repeat `--dataset-b` for
combined fits). The generator verifies physical IDs and full-sensor FlipY geometry,
refuses existing output, and writes `calibration.json` with
`rig_alignment_status:nominal_operator_geometry`, `qualified:false`. Lens validation
states and missing angular limits remain unchanged; alignment and seam coverage
are still unmeasured.

## Measured alignment is separate

A symmetric checkerboard's detector-local corner order can rotate between views.
**Do not equate A/B corner indices.** Paired alignment needs a marked physical
origin/axes, verified correspondence, the same printed face/full grid in both
cameras, original timestamps and stationary holds. Nearest timestamps do not
prove exposure synchronization. Follow the [paired alignment workflow](rig-alignment.md);
if full-board overlap is unavailable, record that limit and design another
measurement. Camera separation still causes depth-dependent parallax.

With a separately measured rig JSON specifying sensor size, crop/output/flips
and `R_camera_from_rig`, fit the combined bundle:

```sh
software/.venv/bin/pv calibrate --dataset output/calibration/dataset.json \
  --rig output/calibration/rig.json --output output/calibration/fit-01
```

Unrelated A/B board poses do not determine rig alignment; never label nominal
rotations as measured to enable a viewer.

# Measured alignment of the two mounted cameras

`pigeonvision.rig_calibration` is an offline solver for a shared rigid transform
between two cameras with **frozen Mei lens parameters**. It writes a separate
evidence report. It does not modify the live viewer or produce a panorama-ready
`calibration.json`. No real paired alignment observations have been collected yet.

## Operator collection

Keep both cameras in their final rigid mount and preserve lens focus. Start by
finding **one complete board visible simultaneously in both original images**.
Place the board to a side of the rocket with its printed face toward both lenses.
Move it farther if needed to fit in the shared field, while keeping individual
squares large and sharp enough to detect. Sensor cropping, lens edge quality and
body occlusion can make the complete grid unavailable in the nominal overlap.
A supplier FOV prediction does not establish usable installed overlap.

Both cameras must see the same printed face and physical corners. Moving the
assembled rocket while keeping the board stationary is valid; hold both still
for each chosen pair. Nearest timestamps alone do not establish synchronization.
Keep original full-sensor images and metadata, not rotated viewer screenshots.

For the symmetric DFVision board, mark **O**, **X** and **Y** on the outer margin
without covering the checker pattern. Define O beside one chosen physical inner
corner. X identifies the far end of that corner's row; Y identifies the far end
of its column. On the 17 × 17 inner grid:

- O is physical ID 0, at `(0, 0, 0)` metres.
- X is physical ID 16, at `(0.320, 0, 0)` metres.
- Y is physical ID 272, at `(0, 0.320, 0)` metres.
- Every corner ID is `row * 17 + column`; coordinates are
  `(column * 0.020, row * 0.020, 0)` metres.

The meaning is physical: an image's top-left detector corner need not be O.
For **every pair and each camera**, inspect the marked board, reorder detected
corners to these physical IDs, and independently click the three corresponding
inner corners. Store those clicks as `anchor_pixels`. The solver checks each
click against its declared ID within 3 pixels. An incorrectly rotated ID grid
that contradicts the clicks is rejected. Consistently false operator labels
cannot be proven correct by a numerical fit; the confirmation must be real.

Coordinates are canonical, unflipped full-sensor pixel centres. Undo the capture
session's `flip_x`/`flip_y` for annotation only; retain the original image bytes
and their hash. Ignore the browser's independent viewing rotation.

Collect at least **eight distinct fitting pairs** with different board positions,
distances and tilts. Reserve **three distinct held-out pairs per declared seam**.
For the two seam directions this means at least six held-out pairs. Assign one
`pose_group` per physical stationary hold and choose one pair from it. Do not
split frames from one hold between fitting and validation. Identical or nearly
identical grids are rejected even if their group names differ.

If a complete shared grid is not visible, stop this workflow. A separately
designed ID-bearing partial target or surveyed multi-target arrangement is needed.
Alternating unrelated board views does not establish the mounted relative pose.

## Input contract

Call the module independently from the existing CLI:

```sh
software/.venv/bin/python -m pigeonvision.rig_calibration \
  --dataset output/alignment/observations.json --output output/alignment/fit-01
```

Or call `calibrate_rig(dataset_path: Path, output: Path)`. The output directory
must not already exist. Paths inside the dataset are relative to its JSON file.

The top-level JSON fields are:

| Field | Required meaning |
| --- | --- |
| `schema_version` | `1` |
| `kind` | `"rig_alignment_observations"` |
| `evidence_kind` | `"camera_capture"` for actual observations; `"synthetic_fixture"` for tests |
| `coordinate_convention` | `"unflipped_full_sensor_pixel_centres"` |
| `assembly_unchanged_confirmed` | Explicit `true` after checking mount and focus |
| `board` | Checkerboard type, square counts and measured square pitch, as in the existing board preset |
| `physical_cameras`, `image_orientation` | The full-sensor physical descriptions and capture flips from the collector |
| `intrinsics` | `{"A": "A-intrinsics.json", "B": "B-intrinsics.json"}`; each file identifies that physical device and image size |
| `seams` | Distinct labels such as `["seam-left", "seam-right"]` |
| `max_pair_skew_us` | Optional, defaults to 20000; absolute original sensor-timestamp difference |
| `pairs` | Actual paired observations described below |

Every pair has nonempty `pair_id`, `pose_group`, and `seam`, an explicit `split`
(`"fit"` or `"validation"`), and `stationary_hold_confirmed: true`. Its
`correspondence` object requires `operator_confirmed: true`,
`same_printed_face: true`, and a nonempty `note` documenting the physical marks.

`frames.A` and `frames.B` each retain the collector's `path`, `sha256`, `device_id`
and `capture_metadata`. Add these fields without rewriting original metadata:

| Frame field | Meaning |
| --- | --- |
| `session_id` | Original native session ID, identical within the A/B pair |
| `clock_domain` | `"CLOCK_BOOTTIME"` |
| `clock_origin_ns` | The original common session origin, identical for A/B |
| `image_orientation` | Explicit original capture `flip_x`/`flip_y`, matching the physical description |
| `corner_ids` | Every physical inner-corner ID exactly once; array order may differ between cameras |
| `image_points` | Corresponding canonical `[u,v]` points, in the same order as this frame's `corner_ids` |
| `anchor_pixels` | `{"O": [u,v], "X": [u,v], "Y": [u,v]}` from independent operator identification |

Native `capture_metadata` must identify the logical camera, encoded frame status,
original sequence, `sensor_timestamp_ns`, `pts_us`, and actual full `sensor_crop`.
The fitter verifies `pts_us == (sensor_timestamp_ns - clock_origin_ns) // 1000`.
It checks image hashes/dimensions, physical IDs, crop, orientation, shared origin,
timing tolerance and the anchor correspondences. It does not independently rebase
the streams or silently equate matching detector-array positions.

## Solve and evidence

The board pose initializer uses inverse Mei bearings and a local virtual forward
view, including boards beyond the camera's pinhole forward hemisphere. This is
only a pose calculation. No rig alignment is assumed by the initializer.

For each fitting pair, independent initial board poses provide:

```text
R_B_from_A = R_B_from_board @ R_A_from_board.T
t_B_from_A = t_B_from_board - R_B_from_A @ t_A_from_board
X_B = R_B_from_A @ X_A + t_B_from_A
```

The joint solver refines one shared rotation and **translation in metres** plus
one board pose per pair. Both cameras' K, D and xi remain fixed. Translation is
necessary for the finite-distance board, even if a future infinity panorama uses
only rotation. B's optical centre in A coordinates is `-R_B_from_A.T @ t_B_from_A`.
The reported frame is A's optical frame; alignment to rocket-body axes is separate.

For a held-out pair, the fitted relative transform and both lens models remain
frozen. Only a single board pose is fitted jointly to A and B. The report includes
per-camera training and held-out residuals, per-seam held-out residuals, pair-level
errors, rejected inputs, source hashes, skew and local numerical observability.
Targets are ≤1 pixel RMS and ≤2 pixels p95 for every evaluated camera/pair.
This holdout is **alignment-only, conditional on the supplied lens models**.
Overlap with their original training images or physical holds is not audited;
the report explicitly marks that unknown. It does not claim independent validation
of the entire lens-plus-rig stack. Reserve additional observations independent of
both lens fitting and alignment fitting when assessing the full stack.

`rig-alignment.json` can contain a `candidate_transform` even when checks fail.
Its `status` is authoritative. A measured status requires enough distinct fit and
held-out pairs, valid Mei projections, residual targets and local full-rank geometry.
Synthetic datasets always remain `synthetic_test_only`, never real measurements.
Insufficient observations or failed checks leave `rig_alignment_status: "unmeasured"`.
Source intrinsic-validation status is retained; this report only checks observed
seam/board rays, not the lens's entire FOV. A measured result still has
`panorama_ready: false` until separately reviewed and integrated.

No translation estimate can remove arbitrary near-scene parallax from two
spatially separated cameras using one infinity projection.

OpenCV's omnidirectional stereo workflow likewise requires shared observations
of the same pattern and reports the relative rotation and translation.
[Official OpenCV tutorial](https://docs.opencv.org/4.9.0/dd/d12/tutorial_omnidir_calib_main.html).

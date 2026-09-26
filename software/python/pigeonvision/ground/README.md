# Ground receiver

The Mac ground app receives both camera streams, serves A/B and panoramic views,
and records/replays received transport. It uses PyAV, WebSockets, WebCodecs and
WebGL2 with the [version 1 contracts](../../../shared/README.md). Controls affect
ground playback only; there is no flight-control endpoint.

From the repository root after installing the Python package:

```sh
pv view udp://0.0.0.0:5000 --record-transport output/session/transport.ts
pv replay output/session/transport.ts --calibration output/calibration/calibration.json
python -m pigeonvision.ground output/session/transport.ts --calibration output/calibration/calibration.json
```

Open `http://127.0.0.1:8768/` in desktop Chrome. Python API:
`pigeonvision.ground.run(source, host="127.0.0.1", port=8768, calibration=None, open_browser=True, replay=None, record_transport=None)`.
`replay=None` distinguishes files from UDP URLs; the async app factory is
`pigeonvision.ground.server.create_app`.

## Received-transport recording

`--record-transport` saves each UDP payload exactly once before demux, including
video, private JSON and received damaged packets. Upstream loss remains present.
Both the output and `.recording.json` evidence path must be new; the option
requires live UDP and persists through demux reconnects.

A bounded 512-datagram writer queue isolates reception from disk writes. Overflow
or disk errors disable recording while live viewing continues. Browser status
and `/health` expose `transport_recording` state, bytes and errors. Normal shutdown
drains accepted bytes, fsyncs/closes, then records size, SHA-256 and completion.
Hard termination leaves the evidence marked `recording`, not complete.
Direct replay of paired native MKVs still needs a future remux/import command.

## Views and calibration

Saved TS opens paused with one pair loaded. Play/Pause/Step pair/Restart share one
server timeline; another replay viewer restarts it. Raw A/B views work without
calibration; panorama, perspective and coverage views require a Mei bundle.
Missing cameras never become simulator imagery.

In raw views, use the 1–8× slider or wheel to zoom and drag to pan. At 1× the full
transmitted rectangle fits. `Reset view` resets that camera's zoom/centre but
preserves its independent `Rotate 180°` toggle. These page-lifetime settings do
not change recordings or calibrated geometry; zoom cannot recover cropped pixels.

Projection uses canonical full-sensor pixel centres, then crop/scale
`(u-crop_x+0.5)*scale-0.5` and output flips. Rig axes are +Z through A, +X right,
+Y down; the Mei `xi>1` second branch is rejected. Optional `max_theta_deg` bounds
observed coverage; absent limits stay unvalidated. See the shared contract for
masks and rotation fields.

Runtime device IDs, flips, sensor mode, output dimensions and canonical
`sensor_crop` must match the bundle. Mismatches disable panoramas; missing
evidence is shown as unverified. Raw `scaler_crop` needs the native origin/scale
conversion. Importing a bundle does not certify alignment, lens retention,
edge quality or synchronized exposures.

## Preview colour balance

Optional `display_colour` uses these fields:

| Field | Requirement |
| --- | --- |
| `schema_version`, `method` | `1`, `"display_rgb_gain"` |
| `gains`, `devices` | A/B RGB triples, finite 0.5–2; IDs match lens provenance |
| `reference_camera` | A or B, retaining identity gains; or `null` with `reference_target: "colorchecker_neutrals"` |
| `camera_strengths` | Optional initial A/B strengths, each 0–1 |
| `common_headroom_scale` | Optional 0.5–1 for a neutral target; camera reference requires 1 |

The shader applies gains to decoded display RGB before blending. This is not a
linear-light colour calibration: [WebCodecs may convert colour space](https://www.w3.org/TR/webcodecs/#video-frame-rendering).
Retain source frames, lighting, fit/check evidence and limitations with the profile.

Independent A/B strength sliders use `scale + strength*(gain-scale)`: 0% removes
balance while retaining common dimming. The original-colour toggle bypasses
both. Choices survive WebSocket reconnects within the page. Missing or mismatched
identity/orientation/crop gates correction. Raw and panoramic views change;
recorded pixels, camera controls, timestamps and coverage masks do not.

Measure unclipped neutral patches, check other frames and remeasure after
lighting or ISP changes. Neutral balance does not establish absolute colour or
exposure matching; see [ColorChecker guidance](https://calibrite.com/us/product/colorchecker-classic/?noredirect=en-US).

## Timing, recovery and APIs

PIDs 256/257 carry A/B; 258 carries JSON. The declared common
`transport_pts_offset_us` is subtracted once from both videos, preserving their
relative timing; undeclared transports use zero. A late declaration resets both
decoders. Native session metadata repeats for late joins.

TS continuity gaps, corruption, backward timestamps and queue overflow trigger
explicit IDR recovery. Bounds include 48 receiver messages, browser ingress and
decoder queues, and six pairing frames per camera. Live source failures reconnect;
stopped cameras display held/stale states.

`/ws` carries Annex-B access-unit envelopes and typed status/metadata/calibration/
error messages. Saved-source controls are JSON
`{"type":"control","action":"play"}`, also accepting `pause`, `step`, `restart`.
`/health` exposes counters and first-access-unit delay, which is neither
exposure skew nor glass-to-glass latency. Shutdown closes WebSockets before
waiting for handlers, then finalizes the receiver and transport recording.

## Planned flight telemetry

Future ground views will show altitude/altimeter readings, attitude and other
flight telemetry alongside video. Inputs are planned from BMI088/BMP581 and a
receive-only flight-controller UART. Records need timestamps for video alignment
and explicit stale/missing states. Sensor acquisition, telemetry transport and
these display panels are **not implemented**; video remains independent of
recovery functions.

## Verification

Python tests cover real mux/demux, timestamps, UDP/HTTP/WebSockets, shutdown,
continuity recovery, byte-exact recording and failure isolation. Projection tests
compare against OpenCV. Run them from `software/` with `uv run pytest -q`.

Browser checks use Chrome/Playwright. `test_ground_browser.cjs` and
`test_ground_shader.cjs` need a local test viewer. The focus, layout and colour
browser tests route local assets with fake WebSockets; `test_ground_focus.cjs`
and `test_ground_errors.cjs` run offline in Node. Fixtures are synthetic;
these checks do not qualify real optics or hardware.

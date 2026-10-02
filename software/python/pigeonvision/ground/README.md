# Ground receiver

The ground station receives both camera streams, presents a full-bleed video
view with mission telemetry, and records/replays received transport. It uses PyAV,
WebSockets, WebCodecs and WebGL2 with the
[version 1 contracts](../../../shared/README.md). Controls change the ground
presentation and playback; there is no flight-control endpoint.

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

## Radio input

The primary field computer is the M4 Pro MacBook: radio decoding, viewing,
recording and broadcast. Keep Linux compatible without requiring a desktop or
RTX 3070. GNU Radio runs separately in its own environment; radio reception and
full-rate decoding remain untested. Upscaling and frame interpolation are
optional later work, subject to measured laptop headroom.

```text
Ground antenna → E200 RX1 → wired Gigabit Ethernet → ground computer
  gr-dvbs2rx → TSDuck → UDP :5000 → PigeonVision viewer / recording
```

Use the E200's Pluto/IIO firmware. USB-C provides serial maintenance; Ethernet
carries I/Q. Its documented default address is `192.168.1.10`; a direct-connected
computer can use an otherwise unused `192.168.1.100/24` Ethernet interface. A
MacBook needs a USB-C/Thunderbolt Gigabit Ethernet adapter.

| Setting | Baseline |
| --- | --- |
| Waveform | DVB-S2 CCM, single stream, QPSK 2/3 |
| Frames | Normal, pilots on, Gold code 0 |
| Symbol rate / roll-off | 8 Msymbol/s / 0.20 |
| Nominal occupied bandwidth | 9.6 MHz |
| E200 receive sampling | 16 Mcomplex samples/s |
| Transport | 9 Mb/s TS; A/B video PIDs 256/257, JSON PID 258 |

I/Q alone uses `16e6 × 2 × 16 = 512 Mb/s` before network overhead. The nominal
9.6 MHz waveform fits the AD9363 model's 20 MHz analog bandwidth. Sustained IIO
transfer and CPU decoding still need measurement; neither proves RF link margin.

The launcher targets [gr-dvbs2rx](https://github.com/igorauad/gr-dvbs2rx/tree/130c31576cfeebb1a5842b24ccaf4a561b6a9990)
at `130c31576cfeebb1a5842b24ccaf4a561b6a9990`, GNU Radio 3.10 with
`gnuradio.iio.fmcomms2_source_fc32`, compatible libiio/libad9361, and
[TSDuck](https://tsduck.io/). The receiver also imports PyQt5 and GNU Radio UHD.
Keep its Python installation separate from the viewer's virtual environment.
Use [conda-forge GNU Radio](https://github.com/conda-forge/gnuradio-feedstock/blob/main/recipe/meta.yaml)
as the common dependency route: its IIO packages cover Linux and native Apple
Silicon. Stock Homebrew GNU Radio omits IIO. Apply
[`gr-dvbs2rx-arm64.patch`](../../../platform/gr-dvbs2rx-arm64.patch) to the pinned
receiver source before building; it removes a 32-bit-only compiler flag on
Apple's `arm64` target while retaining NEON. Patch application was checked;
the complete environments and receiver builds remain unverified.

From the repository root, start the viewer, then receive in a second terminal:

```sh
pv view udp://127.0.0.1:5000 --record-transport output/radio-001/transport.ts \
  --calibration software/calibration/bench-2026-10-02/calibration.json
RX_FREQ_HZ=REPLACE_WITH_BENCH_FREQUENCY_IN_HZ
bash software/tools/e200_receive.sh "$RX_FREQ_HZ"
```

Use a new recording path each run. `E200_URI`, `RX_GAIN_DB` and `TS_DEST` override
the launcher defaults; append `--dry-run` to inspect commands. Gain starts at
0 dB for bench setup and must be adjusted to the received signal. TSDuck forwards
seven original TS packets per UDP datagram without transcoding or removing
telemetry. Initial preload and input batches are seven packets to avoid the
default multi-megabyte startup buffer. Receiver diagnostics stay on stderr.

Next: decode a bounded RP2350 reference waveform offline, then measure real-time
decoding on the MacBook. When the E200 arrives, check sustained 16 MS/s IIO
capture before adding decoding, viewing and recording. RF lock/SNR/FEC status
still needs integration into the viewer; the current HUD only knows TS health.

References: [E200 setup](https://antsdr-docs.microphase.cn/en/latest/device_and_usage_manual/ANTSDR_E_Series_Module/ANTSDR_E200_Reference_Manual/AntsdrE200_Unpacking_examination.html),
[receiver installation](https://igorauad.github.io/gr-dvbs2rx/docs/installation.html),
[receiver interfaces](https://igorauad.github.io/gr-dvbs2rx/docs/usage.html).

## Operator and audience

`/` is the operator view; `/?mode=audience` follows the shared presentation with
the drawer hidden. Both show source failures, held video and unavailable telemetry.
The first operator receives control. Other operators follow until selecting
**Take control** in the bottom toolbar; audience viewers cannot claim control. Ownership coordinates
trusted local viewers and is not authentication.

Open **Controls** to select raw A/B, panorama or look-around views, manage replay,
set a pad reference and inspect diagnostics. Focus the image for shortcuts:

| Input | Action |
| --- | --- |
| WASD / arrow keys | Pan continuously in look-around mode |
| Q / E | Roll the look-around view left / right |
| Shift while panning | Fine movement |
| Drag / wheel | Pan / zoom the current view |
| 1–9 / preset button | Recall a view |
| Shift+1–9 / Shift-click | Save direction, roll, FOV and reference frame |
| Space / period | Replay play-pause / step pair |

Presets and **Use as default** FOV are stored per browser; unavailable storage
keeps changes within the page. The direction ribbon is body-relative: A is 0°,
B is 180°, with positive pitch looking up. It is not a geographic heading.
The view follows the camera rig by default. **Level horizon** uses the demo's
exact scene orientation at the displayed pair's timestamp. It is optional and
unavailable for real footage until timed attitude estimation is implemented.
Missing orientation stops compensation and shows an unavailable state. In level
mode the direction ribbon uses the scene reference, not camera A/B bearings.
Q/E adds manual roll; neither option changes the recorded images.

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
server timeline. Opening or reconnecting another viewer loads cached decode
history and the held frame without restarting, resuming or advancing established
playback. Replay pauses when the final viewer disconnects. Raw A/B views work
without calibration; panorama, perspective and coverage views require a Mei
bundle. Missing cameras never become simulator imagery.

In raw views, use the 1–8× slider or wheel to zoom and drag to pan. At 1× the full
transmitted rectangle fits. `Reset view` resets that camera's zoom/centre but
preserves its independent `Rotate 180°` toggle. These display settings are shared
with following viewers and do not change recordings or calibrated geometry;
zoom cannot recover cropped pixels.

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
| `reference_camera` | A or B, retaining identity gains; or `null` with `reference_target: "colorchecker_neutrals"` or `"measured_neutral_surfaces"` |
| `camera_strengths` | Optional initial A/B strengths, each 0–1 |
| `common_headroom_scale` | Optional 0.5–1 for a neutral target; camera reference requires 1 |

The shader applies gains to decoded display RGB before blending. This is not a
linear-light colour calibration: [WebCodecs may convert colour space](https://www.w3.org/TR/webcodecs/#video-frame-rendering).
Retain source frames, lighting, fit/check evidence and limitations with the profile.

Independent A/B strength sliders use `scale + strength*(gain-scale)`: 0% removes
balance while retaining common dimming. The original-colour toggle bypasses
both. Choices survive WebSocket reconnects within the page. Missing or mismatched
identity/orientation/crop gates correction. During metadata recovery, the last
verified corrected image is held until new frames can be verified. Held video is
labeled and cannot advance telemetry. Explicit camera or crop mismatches still
disable correction. Recorded pixels and camera controls do not change.

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

`/ws?role=operator` or `/ws?role=audience` carries Annex-B access-unit envelopes and
typed status/metadata/calibration/error messages, plus ground view, ownership and
reference updates. The active operator's saved-source controls are JSON
`{"type":"control","action":"play"}`, also accepting `pause`, `step`, `restart`.
`/health` exposes counters and first-access-unit delay, which is neither
exposure skew nor glass-to-glass latency. Shutdown closes WebSockets before
waiting for handlers, then finalizes the receiver and transport recording.

## Mission telemetry

The HUD consumes transported BMP581 pressure and flight status/events. Sensor
measurements use acquisition timestamps aligned to the displayed video PTS;
future measurements and events are excluded. Legacy flight records or individual
sensor samples without acquisition fields use their declared publication PTS.
Raw sensor and flight diagnostics remain in the drawer.

While phase is `PAD` and pressure is fresh, the operator can **Set barometric
zero**. `BARO AGL` estimates height above that pad pressure using the standard
atmosphere, `44330 * (1 - (pressure / pad_pressure)^0.190263)` metres. It does not
measure terrain clearance. `BARO VERTICAL SPEED` is a least-squares altitude
slope over the last second, requiring four samples spanning at least 0.5 seconds;
invalid readings or gaps above 250 ms suppress the slope.

Pressure expires after 0.5 seconds at the displayed PTS, phase after 3 seconds;
live pressure and phase also expire after 3 seconds without receipt. Missing, invalid or
stale values show `—`, and unavailable phase shows `UNKNOWN`. The altitude plot
breaks acquisition gaps and invalid samples. `T+ (FC RX)` and event times use the
CM5 receipt time of the flight-controller event, not physical ignition time.
`LIVE`/`REPLAY` identify the source mode; simulated inputs retain `SIMULATED`.

## Broadcast and demo

Use a fullscreen Chrome audience window and an OBS **Window Capture** of that
window, with the operator window outside the capture. HDMI/SRT handoff remains
TBD. A standalone native GPU renderer is an optional later enhancement and is
not implemented. Projection, HUD and preview colour operate on the ground
display; the original TS stays unmodified.

Generate the synthetic demo from the repository root, then open its saved replay:

```sh
python software/tools/ground_demo.py
pv replay build/ground-station/demo/launch.ts --calibration build/ground-station/demo/calibration.json
```

The generator writes both files under `build/ground-station/demo/`. Its synthetic
video and telemetry are labeled `SIMULATED`.

For the full saved OpenRocket flight, prepare a separate capture site and serve it:

```sh
python software/tools/full_flight.py path/to/rocket.ork --prepare-site
python -m http.server 8774 --bind 127.0.0.1 --directory build/ground-station/full-flight/site
```

In another terminal, with Chrome, Playwright and FFmpeg available:

```sh
SIM_SITE_DIR=build/ground-station/full-flight/site \
SIM_OUTPUT_DIR=build/ground-station/full-flight/video \
SIM_URL='http://127.0.0.1:8774/?capture=1' node software/simulator/render_sequence.cjs
python software/tools/ground_demo.py \
  --source-manifest build/ground-station/full-flight/video/manifest-v3.json \
  --trajectory build/ground-station/full-flight/site/assets/launch.json \
  --output build/ground-station/full-flight/demo
pv replay build/ground-station/full-flight/demo/launch.ts \
  --calibration build/ground-station/full-flight/demo/calibration.json
```

This uses the file's saved trajectory through landing. Recovery swing, impact
pose and terrain remain illustrative. One effective canopy changes size for
main deployment; it does not reproduce the real two-canopy rigging.

## Verification

Python tests cover real mux/demux, timestamps, UDP/HTTP/WebSockets, shutdown,
continuity recovery, byte-exact recording and failure isolation. Projection tests
compare against OpenCV. Run them from `software/` with `uv run pytest -q`.

Browser checks use Chrome/Playwright. `test_ground_browser.cjs` and
`test_ground_shader.cjs` need a local test viewer. The focus, layout, colour-app
and telemetry browser tests route local assets with mocked WebSockets.
`test_ground_focus.cjs`, `test_ground_errors.cjs`, `test_ground_mission.cjs` and
`test_ground_navigation.cjs` run offline in Node. Fixtures are synthetic;
these checks do not qualify real optics or hardware.

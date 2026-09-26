# PigeonVision bench software

Capture and encode two IMX900 colour cameras on a CM5, retain their original video locally, and stitch calibrated views on the ground. The initial bench uses the official CM5 IO board, one FRAMOS camera on each camera port, Ethernet, and a Mac ground station. The existing simulator remains an explicit synthetic reference.

The operating target is two 1552-square crops at 30 fps, each encoded at 4 Mb/s, inside a 9 Mb/s MPEG transport stream. This is a qualification target, not a measured capability. CM5 encoding uses its CPUs; there is no hardware H.264 encoder.

## Components

| Directory | Responsibility |
|---|---|
| `flight/` | C++20 libcamera capture, software x264, independent segmented recording and MPEG-TS/UDP output |
| `python/pigeonvision/` | `pv` commands, read-only diagnostics, benchmarks, calibration and reports |
| `python/pigeonvision/ground/` | PyAV demux, local WebSocket server, WebCodecs/WebGL viewer |
| `platform/` | Explicit OS customization, guarded macOS eMMC writing, pinned FRAMOS target build and camera smoke test |
| `shared/` | Session, frame, transport and calibration contracts |
| `tests/` | Python tests; portable native tests live under `flight/tests/` |

Agent collaboration: Astra leads integration; Astra and Opus 5.5 can both contribute implementation, ideas and independent reviews. Neither is a preferred subagent model. Assign separate file ownership, review a worker's changes independently, and keep physical provisioning under one operator.

## Provision the official CM5 IO board

The version lock is `platform/versions.json`: Raspberry Pi OS Lite 64-bit 2025-11-24, kernel **6.12.47+rpt-rpi-2712**, pinned FRAMOS driver/libcamera commits and rpicam-apps v1.10.0. Lite shares the vendor's dated kernel baseline; the target installer verifies the running kernel. Do not run a distribution upgrade during bench qualification.

1. With `nRPIBOOT` fitted, use the pinned official Raspberry Pi `usbboot` Linux mass-storage gadget. Initial `BCM2712D0 Boot` enumeration is a ROM interface, not a disk. Normal imaging does not require EEPROM recovery or OTP provisioning.
2. Verify the vendor image's SHA-256, decompress it, then run `platform/customize_image.py` against the regular image file with the intended **public** SSH key. It writes cloud-init configuration, keeps password SSH disabled, and reports the customized image hash. Dependencies for this separate provisioning environment are `pyfatfs==1.1.0` and `setuptools<81`.
3. Use `platform/flash_cm5_macos.py` with an explicit disk identifier, exact Raspberry Pi USB serial, capacity, image hash and new evidence path. It refuses internal/non-USB/non-eMMC targets, writes the image, verifies every written byte with SHA-256 and ejects the disk. Native macOS authentication stays on the Mac.
4. Only after verification and ejection, disconnect power, remove `nRPIBOOT`, connect Ethernet, and power normally. The default customized account is `pigeon@pigeonvision.local`. Record `cloud-init status --long`, `uname -a`, board model, RAM and storage inventory before proceeding.
5. Copy this repository to the Pi, then run `bash software/platform/install_framos.sh` as the bench user. It holds kernel/firmware packages, installs build dependencies, builds the pinned sources on the CM5, backs up and updates the camera boot configuration. It does not reboot automatically.
6. Verify the official IO-board camera-1 jumpers against its actual revision, reboot, then run `bash software/platform/camera_smoke.sh output/first-cameras`. Inspect colour, orientation, focus and both simultaneous images before performance testing.

The camera configuration disables automatic detection and uses `fr_imx900,cam0` plus `fr_imx900,cam1-compute`. The vendor colour baseline uses vertical flip. These IO-board GPIO assumptions must be revisited for the custom carrier.

Primary references: [FRAMOS drivers](https://github.com/framosimaging/framos-rpi-drivers), [FRAMOS libcamera](https://github.com/framosimaging/framos-libcamera), [Raspberry Pi usbboot](https://github.com/raspberrypi/usbboot), and [Imager customization formats](https://github.com/raspberrypi/rpi-imager/blob/main/doc/os_customisation_formats.md).

## Build and run

From `software/`, install the locked Python environment on the Mac and Pi:

```sh
uv sync --locked --extra test
uv run pv doctor
```

After the FRAMOS target build, build native capture **on the Pi**:

```sh
cmake -S flight -B build/flight -DCMAKE_BUILD_TYPE=Release
cmake --build build/flight -j2
ctest --test-dir build/flight --output-on-failure
sudo cmake --install build/flight
sudo ln -sf "$PWD/.venv/bin/pv" /usr/local/bin/pv
pv-capture --list-cameras
```

Map physical camera IDs deliberately to A/B. Camera indices and connector paths alone do not establish that lenses or ribbons have not been swapped. A starter capture configuration follows; replace both device values with enumerated IDs and use a new session directory each run:

```json
{
  "schema_version": 1,
  "session_dir": "output/sessions/bench-001",
  "cameras": [
    {"id": "A", "device": "REPLACE_WITH_CAMERA_A_ID", "flip_y": true},
    {"id": "B", "device": "REPLACE_WITH_CAMERA_B_ID", "flip_y": true}
  ],
  "width": 1552,
  "height": 1552,
  "fps": 30,
  "bitrate": 4000000,
  "vbv_bits": 2000000,
  "preset": "ultrafast",
  "segment_seconds": 60,
  "min_free_bytes": 2147483648,
  "record": true,
  "udp_destination": null,
  "mux_bitrate": 9000000,
  "duration_seconds": 30
}
```

Start with recording alone: `pv capture --config capture.json`. Set `udp_destination` to the Mac's reachable `IP:1234` for streaming. On the Mac, run `pv view udp://0.0.0.0:1234`; the viewer serves locally at `http://127.0.0.1:8768`. Use Chrome with WebCodecs support. `pv capture --config capture.json --ssh pigeon@pigeonvision.local` runs the installed remote command; paths inside that configuration refer to the Pi.

`pv replay path/to/session.ts` exercises the same receiver using a saved transport. Raw A/B views work without calibration; calibrated panorama/perspective modes require a real bundle supplied with `--calibration`. Source failures never silently switch to simulator content. Viewer playback controls affect ground replay, not flight capture.

Recording segments share a common clock and begin at keyframes. JSON frame records are authoritative for exact timestamps; container timebases may quantize them. A low-space event stops recording while capture/transport continue. No automatic deletion of earlier evidence is performed.

## Calibrate and qualify

Use full-sensor ChArUco observations with measured board dimensions and diverse poses, including both seam regions. Explicitly identify the capture orientation so observations can be transformed back to canonical unflipped full-sensor coordinates. Keep held-out images separate from fitting images. The Mei model supports rays beyond 180 degrees; it does not remove near-field parallax.

```sh
pv calibrate --dataset dataset.json --rig rig.json --output output/calibration/bench-001
pv view udp://0.0.0.0:1234 --calibration output/calibration/bench-001/calibration.json
pv bench --config capture.json --output output/bench/matrix-001 --duration 60 --udp MAC_IP:1234 --preset ultrafast --dry-run
pv report output/sessions/bench-001
```

The dataset records board geometry, per-camera image orientation and `fit`/`validation` image splits. Rig input supplies measured rotations, crop, output dimensions and masks; the tool does not invent lens alignment. Calibration results preserve rejected views and provenance, and report held-out central/edge/seam errors separately. See `python/pigeonvision/calibration.py` for the input contract and validation requirements.

Run capture-only, single encode, dual encode, recording, streaming and combined cases. Compare 1080-square, 1552-square and full-sensor output; then compare encoder presets. For the one-hour qualification, use sufficient duration to cover a full hour of camera timestamps (for example 3610 seconds), not just process startup time.

Qualification targets: nominal dual 30 fps with no unexplained gaps; mean total CPU below 80%; no throttling/undervoltage or growing queues; glass-to-glass latency at most one second; independent recording during network loss; readable finalized segments; held-out calibration RMS at most one pixel and 95th percentile at most two pixels, including overlaps. Missing measurements remain unknown in reports.

Free-running cameras remain labelled unsynchronized. Hardware synchronization requires verifying the actual adapter's XVS/XHS connectors and signal levels, then measuring exposure skew optically/electrically against the 100 microsecond target. Reported sensor timestamps alone cannot qualify synchronization.

## Validation and later stages

```sh
uv run pytest -q
cmake -S flight -B build/core -DPV_BUILD_CAPTURE=OFF
cmake --build build/core
ctest --test-dir build/core --output-on-failure
```

Portable tests and synthetic fixture playback verify software behavior; they do not establish camera operation, calibration quality or CM5 throughput. Store captures, logs, images and reports under ignored `output/` directories.

After the wired stack is qualified: add BMI088/BMP581 and receive-only flight-controller UART; validate E200 transport through a cable/attenuator; port shared camera power/reset and GPIO to the custom carrier; then qualify systemd startup/recovery and shutdown/storage behavior. Keep video independent of recovery functions. The later radio target remains DVB-S2 QPSK 2/3, normal frames, pilots, 8 MSymbol/s and a 9 Mb/s transport; the existing Tezuka E200 firmware requires actual hardware qualification. PA control remains off until its own bench procedure is authorized.

# PigeonVision bench software

The CM5 bench captures both colour IMX900 cameras, records their original video,
and streams to the Mac's interactive A/B and panorama viewer. It uses the official
CM5 IO board, FRAMOS drivers, Ethernet from the Pi, and software x264 encoding.
The Mac currently reaches the LAN over Wi-Fi.

Full 2064×1552 output approached dual 30 fps in short cached-buffer trials; a
later 180-second run still logged five queue drops. The panorama uses measured
lens fits with **nominal** opposed-camera alignment. One-hour performance,
held-out seam accuracy, colour accuracy and exposure synchronization remain
unqualified. See the [bench evidence](bench-notes/2026-09-25.md).

Next: verify colour and optical coverage, measure alignment and sustained
performance, then add altitude/attitude telemetry and flight interfaces. The
[ground roadmap](python/pigeonvision/ground/README.md#planned-flight-telemetry)
describes the planned telemetry display.

## Start here

| Guide | Covers |
| --- | --- |
| [Native capture](flight/README.md) | C++20/libcamera/x264 build, buffers, recording and transport |
| [Ground app](python/pigeonvision/ground/README.md) | Viewing, replay, received-TS recording and colour controls |
| [Calibration](calibration/README.md) | Board collection, lens fitting and nominal preview |
| [Shared interfaces](shared/README.md) | Configuration, session/frame records, timestamps and calibration schema |
| [Platform pins](platform/versions.json) | Matched OS, kernel and vendor revisions |

Astra leads integration; Astra and Opus 5.5 may both implement, discuss and review.
Neither is preferred. Give workers separate ownership and independent review;
keep provisioning under one operator.

## Provision the CM5

Use Raspberry Pi OS Lite 64-bit **2025-11-24**, kernel
**6.12.47+rpt-rpi-2712**, and the pinned FRAMOS/rpicam-apps stack. Do not run a
distribution upgrade during qualification.
Run provisioning commands from the repository root.

1. Fit `nRPIBOOT` and use the pinned official `usbboot` Linux mass-storage gadget.
   `BCM2712D0 Boot` is the ROM interface, not a disk. Normal imaging needs no
   EEPROM recovery or OTP provisioning.
2. Verify the vendor image SHA-256 and decompress it. Run
   `software/platform/customize_image.py` on the image with the intended **public** SSH key;
   it writes cloud-init, disables password SSH and reports the customized hash.
   Its separate provisioning environment needs `pyfatfs==1.1.0`, `setuptools<81`.
3. Run `software/platform/flash_cm5_macos.py` with explicit disk, USB serial, capacity,
   image hash and new evidence path. It rejects internal/non-USB/non-eMMC targets,
   verifies the write by SHA-256 readback, then ejects. Authenticate on the Mac.
4. After verification/ejection, disconnect power, remove `nRPIBOOT`, connect
   Ethernet and boot normally. Default account: `pigeon@pigeonvision.local`.
   Record `cloud-init status --long`, `uname -a`, board, RAM and storage inventory.
5. Copy the repository to the Pi and run
   `bash software/platform/install_framos.sh`. It holds kernel/firmware packages,
   builds pinned dependencies and backs up/updates boot configuration; it does
   not reboot automatically.
6. Check camera-1 jumpers against the actual IO-board revision, reboot, and run
   `bash software/platform/camera_smoke.sh output/first-cameras`. Inspect both
   cameras' colour, orientation and focus before benchmarking.

Camera auto-detection is disabled; overlays are `fr_imx900,cam0` and
`fr_imx900,cam1-compute`, with vendor FlipY. Recheck GPIO assumptions for a custom
carrier. References: [FRAMOS](https://github.com/framosimaging/framos-rpi-drivers),
[usbboot](https://github.com/raspberrypi/usbboot),
[image customization](https://github.com/raspberrypi/rpi-imager/blob/main/doc/os_customisation_formats.md).

## Build and run

From `software/`, on the Mac and Pi:

```sh
uv sync --locked --extra test
. .venv/bin/activate
pv doctor
```

After installing FRAMOS, build capture **on the Pi**:

```sh
cmake -S flight -B build/flight -DCMAKE_BUILD_TYPE=Release
cmake --build build/flight -j2
ctest --test-dir build/flight --output-on-failure
sudo cmake --install build/flight
sudo ln -sf "$PWD/.venv/bin/pv" /usr/local/bin/pv
pv-capture --list-cameras
```

Replace device placeholders with enumerated IDs and verify their physical A/B
mapping. Use a new session directory for each run. This is the baseline square
profile; set width/height to 2064/1552 for full-sensor output.

```json
{
  "schema_version": 1,
  "session_dir": "output/sessions/bench-001",
  "cameras": [
    {"id": "A", "device": "REPLACE_WITH_CAMERA_A_ID", "flip_y": true},
    {"id": "B", "device": "REPLACE_WITH_CAMERA_B_ID", "flip_y": true}
  ],
  "width": 1552, "height": 1552, "fps": 30,
  "bitrate": 4000000, "vbv_bits": 2000000, "preset": "ultrafast",
  "segment_seconds": 60, "min_free_bytes": 2147483648,
  "record": true, "udp_destination": null, "mux_bitrate": 9000000,
  "duration_seconds": 30
}
```

```sh
pv capture --config capture.json
# Or run the installed remote capture command; config paths then refer to the Pi:
pv capture --config capture.json --ssh pigeon@pigeonvision.local
# Set udp_destination to the Mac's reachable IP:1234, then on the Mac:
pv view udp://0.0.0.0:1234
pv replay path/to/session.ts
```

Use Chrome at `http://127.0.0.1:8768`. Raw views need no calibration; add
`--calibration PATH/calibration.json` for a panorama. The simulator is an explicit
source, never an automatic fallback. See the linked guides for capture options,
received-TS recording and calibration commands.

## Benchmark and qualify

```sh
pv bench --config capture.json --output output/bench/matrix-001 --duration 60 --udp MAC_IP:1234 --preset ultrafast --dry-run
pv report output/sessions/bench-001
uv run pytest -q
cmake -S flight -B build/core -DPV_BUILD_CAPTURE=OFF
cmake --build build/core
ctest --test-dir build/core --output-on-failure
```

Compare capture-only, one/two encoders, recording, streaming and combined loads;
then resolutions and presets. Allow a full hour of camera timestamps, not just
process runtime (for example, run 3610 seconds).

Targets remain dual nominal 30 fps with accounted gaps, mean total CPU <80%, no
throttling/undervoltage or growing queues, ≤1 s glass-to-glass latency, recording
through network loss, and readable finalized segments. Calibration targets are
≤1 px held-out RMS and ≤2 px p95, including overlap regions. Free-running cameras
remain unsynchronized until adapter XVS/XHS wiring and levels are verified and
≤100 µs exposure skew is measured optically/electrically. Missing evidence stays
unknown; portable/synthetic tests do not qualify hardware.

After wired qualification: BMI088/BMP581 and receive-only flight-controller UART;
E200 cable/attenuator transport; custom-carrier power/reset/GPIO; then systemd
startup/recovery and storage interruption tests. Keep video independent of
recovery functions. The radio target remains DVB-S2 QPSK 2/3, normal frames,
pilots, 8 MSymbol/s and 9 Mb/s TS; E200 firmware needs hardware qualification.
PA control stays off pending its own authorized procedure. Keep generated
evidence under ignored `output/` directories.

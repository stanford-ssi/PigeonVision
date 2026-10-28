# PigeonVision software

The CM5 bench captures both colour IMX900 cameras, records their original video,
and streams to the Mac's interactive A/B and panorama viewer. It uses the official
CM5 IO board, FRAMOS drivers, Ethernet from the Pi, and software x264 encoding.
The Mac currently reaches the LAN over Wi-Fi.

Full 2064×1552 output approached dual 30 fps in short cached-buffer trials; a
later 180-second run still logged five queue drops. The panorama uses measured
lens fits with **nominal** opposed-camera alignment. One-hour performance,
held-out seam accuracy, colour accuracy and exposure synchronization remain
unqualified. See the [bench evidence](bench-notes/README.md#cameras).

The existing native SPI sender was benchmarked with the CM5 and Pico. With a fan, the two-minute test
reached **29.99 fps per camera and 9 Mb/s TS**, with matching Pico counts and
CRC. CPU averaged 61.4%; maximum temperature was 56.75°C. Each camera had one
66.7 ms interval after warm-up. Keep `pv spi` for patterns and file tests.
See [SPI results and setup](flight/spi.md). Those are Pico transport results;
they do not qualify the new transmitter board.

**Flight plan:** one CM5 records both cameras and sends MPEG-TS over SPI to
[Yapogee S3](../hardware/yapogee-s3/README.md). Its ESP32-S3-WROOM-1U-N16R8
generates DVB-S2 at 2.42 GHz. A short U.FL jumper feeds the RFFC5072A converter;
a 3.70 GHz LO produces 1.28 GHz, followed by the SPF5043Z driver, SMA cable and
separate GRF5613 PA/final filter. High-side conversion needs IQ conjugation or
equivalent receiver handling. Ethernet remains the E200 fallback. The ground
receiver remains to be selected and qualified; the Mac decodes and stitches.

The [S3 reference firmware](https://github.com/Rivercraft911/esp-dvbs2) already
implements PV-SPI v1: one 1332-byte message per CS, CRC-32 and READY backpressure.
Single-line SPI is the baseline; quad data pins are reserved on the board.
The ESP32 path has conducted RF/video evidence. Carrier GPIO configuration,
converter/PA control, autonomous flight startup and the complete new RF chain
still need integration and hardware testing. The diagrams distinguish that plan
from the existing CM5 capture, sensor and recording software.

The reference firmware also has an [S3 DVB-S2 receiver](https://github.com/Rivercraft911/esp-dvbs2/blob/main/docs/receiver.md) using raw-IQ capture, with conducted tests at 2.42 GHz. Receiving at 1.28 GHz would require its own suitable RF conversion path; the present Yapogee amplifier/converter chain is arranged for transmission.

[Editable architecture](architecture.drawio) · [Data flow](diagrams/data-flow.svg) ·
[CM5 lifecycle](diagrams/cm5-lifecycle.svg) ·
[Sensors and telemetry](diagrams/sensors-telemetry.svg) ·
[Yapogee S3 lifecycle](diagrams/transmitter-lifecycle.svg) ·
[Startup/shutdown](diagrams/startup-shutdown.svg)

## Start here

| Guide | Covers |
| --- | --- |
| [Native capture](flight/README.md) | C++20/libcamera/x264 build, buffers, recording and transport |
| [CM5 SPI bench](flight/spi.md) | Existing sender, Pico wiring/evidence, backpressure and viewer copy |
| [Ground app](python/pigeonvision/ground/README.md) | Viewing, replay, received-TS recording and colour controls |
| [Calibration](calibration/README.md) | Board collection, lens fitting and nominal preview |
| [Shared interfaces](shared/README.md) | Configuration, session/frame records, timestamps and calibration schema |
| [Platform pins](platform/versions.json) | Matched OS, kernel and vendor revisions |

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
recovery functions. The camera mux planning rate remains 9 Mb/s TS. QPSK and
8PSK are waveform candidates; symbol rate, coding, occupied bandwidth and
delivered PA power need qualification on the selected S3/converter/PA/receiver
chain. The earlier Pico profile and measurements remain bench evidence, not a
frozen S3 flight profile. PA control stays off pending its own authorized
procedure. Keep generated evidence under ignored `output/` directories.

<img src="assets/pigeonvision.png" alt="Pigeon wearing two fisheye camera lenses" width="160">

# PigeonVision

Video downlink for Stanford SSI's IREC rocket. Two fisheye cameras record the flight and send video to the ground for stitching and viewing.

Try the [flight demo](https://stanford-ssi.github.io/PigeonVision/) to see the camera views and stitched panorama.

**Current plan:** two IMX900 cameras at 30 fps, one CM5 and [Yapogee S3](hardware/yapogee-s3/README.md), an ESP32-S3-WROOM-1U DVB-S2 transmitter with RFFC5072A conversion from 2.42 GHz to 1.28 GHz. Keep Ethernet for an E200 fallback and a separate PA/filter board. The ESP32 path has conducted RF/video results; the new converter and PA chain still need schematic capture and hardware testing. Flight channel, bandwidth, power and ground receiver remain open.

- [Hardware](hardware/README.md): system diagram, carrier and RF frontend.
- [Yapogee S3](hardware/yapogee-s3/README.md): transmitter architecture, 5 V interface and waveform candidates.
- [Software](software/README.md): flight and ground tasks.
- [Link budget](calculations/link-budget.xlsx): RF and video bitrate calculations.
- [Simulator](docs/README.md): camera views, flight playback and local setup.

**Next:** capture and review the Yapogee S3 schematic, integrate the carrier interface and PA control, then complete layout and hardware qualification. The system, carrier, RF and software diagrams describe this plan; existing Pico transport measurements remain historical evidence.

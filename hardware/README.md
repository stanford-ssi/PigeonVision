# Hardware

One CM5 captures two fisheye cameras. SPI feeds [Yapogee S3](yapogee-s3/README.md): an ESP32-S3 makes DVB-S2 at 2.42 GHz, then an integrated mixer/LO converts it to the flight band near 1.28 GHz. A separate PA/filter board feeds the antenna. Ethernet supports an E200 fallback. The flight ground receiver remains to be selected and qualified.

![System block diagram](system.svg)

[Edit diagram](system.drawio)

This diagram retains the earlier RP2350/AFE7071 architecture. The [Yapogee S3 guide](yapogee-s3/README.md) contains the new signal and power diagram; carrier pin assignments and legacy diagrams still need reconciliation with its final interface.

- [Carrier](carrier/README.md): compute, cameras, power and sensors.
- [Yapogee S3](yapogee-s3/README.md): ESP32 modem, integrated RF converter and 5 V input. Architecture draft.
- [RF frontend](rf-frontend/README.md): driver, PA and output filter.
- [Project library](libraries/README.md): KiCad parts and CM5 placement.

Use one flight transmitter at a time, connected by SMA coax to the separate PA/filter board. Swap cables/configuration for the E200 fallback. The diagrams show the planned interfaces, not a completed schematic.

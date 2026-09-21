# Hardware

One CM5 captures two fisheye cameras. An E200 sends video to the ground for stitching and viewing.

![System block diagram](system.svg)

[Edit diagram](system.drawio)

- [Carrier](carrier/README.md): compute, cameras, power and sensors.
- [RF frontend](rf-frontend/README.md): driver, PA and output filter.
- [Project library](libraries/README.md): KiCad parts and CM5 placement.

The flight E200 connects by Ethernet and SMA coax. A second E200 connects to the ground PC.

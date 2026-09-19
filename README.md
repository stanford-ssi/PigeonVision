<img src="assets/pigeonvision.png" alt="Pigeon wearing two fisheye camera lenses" width="160">

# PigeonVision

Video downlink for Stanford SSI's IREC rocket. Two fisheye cameras record the flight and send video to the ground for stitching and viewing.

Try the [flight demo](https://stanford-ssi.github.io/PigeonVision/) to see the camera views and stitched panorama.

**Cur Plan:** two IMX900 cameras at 30 fps, one CM5 and an E200 DVB-S2 transmitter. A second E200 and Linux PC receive and stitch the video. Target: 10,000 ft AGL, 6-inch airframe, 0.5 W average PA output.

- [Hardware](hardware/README.md): system diagram, carrier and RF frontend.
- [Software](software/README.md): flight and ground tasks.
- [Link budget](calculations/link-budget.xlsx): RF and video bitrate calculations.
- [Simulator](docs/README.md): camera views, flight playback and local setup.

**Next:** KiCad schematics, dual-camera test and E200 bench link. Test before PCB fabrication.

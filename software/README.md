# Software

**Cur Plan:** record both cameras onboard; transmit both views; stitch on the ground.

**Flight:** acquire both cameras at 30 fps, encode, record locally and multiplex one MPEG transport stream. Native `pv-capture` sends PV-SPI to the RP2350; UDP/Ethernet remains the E200 fallback. Handle storage limits, shutdown and RF inhibit.

**Radio:** RP2350 DVB-S2 QPSK 2/3, normal frames, pilots and 8 MSymbol/s, feeding an AFE7071 RF board. The E200 fallback uses the [Tezuka transmitter image](https://github.com/F5OEO/tezuka_fw/releases/tag/v0.3.21). Neither RF path is qualified. PV-SPI v1 carries TS only; the Pico currently needs USB configuration/startup.

**Ground:** an E200 sends I/Q over Ethernet to the Linux PC. Use [gr-dvbs2rx](https://github.com/igorauad/gr-dvbs2rx) to recover the transport, decode both cameras, apply lens calibration and stitch a movable, stabilized view. Retain original streams and telemetry.

Measured with a fan: two 2064 × 1552 cameras, 4 Mb/s each, local recording and 9 Mb/s TS over 20 MHz SPI reached 29.99 fps each for two minutes, with matching host/Pico counts and CRC. This does not establish RF performance or one-hour endurance.

This carrier branch predates the bench code. Use the [current software on main](https://github.com/stanford-ssi/PigeonVision/tree/main/software) and [SPI setup/results](https://github.com/stanford-ssi/PigeonVision/blob/main/software/flight/spi.md). Next: AFE7071 RF prototype, attenuated E200 receive test, then PA and endurance tests. No camera-command uplink is planned.

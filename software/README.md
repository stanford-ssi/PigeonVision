# Software

**Cur Plan:** record both cameras onboard; transmit both views; stitch on the ground.

**Flight:** acquire both cameras at 30 fps, timestamp frames and sensors, encode two fisheyes, record locally and multiplex one MPEG transport stream over UDP/Ethernet to the E200. Handle storage limits, shutdown and PA enable.

**Radio:** DVB-S2 QPSK 2/3, normal frames, pilots, 8 MSymbol/s target. Start with the existing [Tezuka E200 transmitter image](https://github.com/F5OEO/tezuka_fw/releases/tag/v0.3.21). That release includes an E200 build but does not report hardware testing on E200.

**Ground:** a second E200 sends I/Q over Ethernet to the Linux PC. Use [gr-dvbs2rx](https://github.com/igorauad/gr-dvbs2rx) to recover the transport, decode both cameras, apply lens calibration and stitch a movable, stabilized view. Retain original streams and telemetry.

Camera test target: two calibrated 1552 x 1552 crops from the IMX900/CIL212 pair at 30 fps and an initial 4 Mb/s each, with recording and sensors on one CM5. Check dropped frames, latency, temperature and power.

Radio bench test: E200 TX through attenuation to E200 RX, without the PA. Start at 1 MSymbol/s, then reach 8 MSymbol/s with a 9 Mb/s test transport. Run for one hour and check continuity errors, underruns and latency before PCB fabrication. No camera-command uplink is planned.

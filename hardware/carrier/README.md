# Carrier

**Cur Plan:** one CM5, two cameras and an Ethernet connection to the E200. Record to eMMC. 3S battery, one-hour runtime target. No separate MCU.

![Carrier block diagram](block-diagram.svg)

[Edit diagram](block-diagram.drawio) · [KiCad project](PigeonCarrier/PigeonCarrier.kicad_pro) · [Design master](../../calculations/design-master.xlsx)

| Block | Parts / connection |
| --- | --- |
| Compute | SC1596 / CM5104064: 4 GB RAM, 64 GB eMMC |
| CM5 connectors | 2 × Amphenol 10164227-1001A1RLF, 1.5 mm stack |
| Cameras | 2 × FRAMOS IMX900, CIL212 lenses, FFA-A/MC50 adapters; I-PEX 20525-050E-02 on carrier |
| Input protection | Fuse, LM74502DDFR; proposed 2 × CSD18540Q5B and SMBJ18CA |
| Main 5 V | LM61495RPHR; CM5, E200 and camera regulators |
| RF 5 V | TPS62933FDRLR; separate supply, default off |
| Camera rails | 2 × TLV75801PDRVR: shared 4.0 V and 1.8 V |
| Battery monitor | INA226AIDGSR; shunt value TBD |
| Sensors | BMI088 and BMP581, matching Aerolotl; optional ADXL375 |
| Camera control | TCA9406DCUR for I²C; SN74AXC4T245PWR for reset/sync |
| Ethernet | EDAC A70-112-331N126 with integrated magnetics |
| Service | USB recovery, boot selector, debug UART, record/shutdown control |
| Flight UART | Receive-only; logic voltage and protocol TBD |

Both cameras power-cycle together. Hold resets low, establish 1.8 V before 4.0 V, then wait at least 200 ms after both rails are valid before releasing reset. The driver needs updating for this sequence. MIPI connects directly to CM5.

Sensors use filtered CM5 3.3 V. The E200 mounts above the carrier with a short Ethernet cable. Battery input and flight UART go on the top sheet; power circuits go under Regulators.

Still to size: passives, fuse, shunt, startup ramp and UV/OV thresholds. Confirm bay dimensions and mounting holes before layout.

[Library and sourcing](../libraries/README.md) · [CM5 datasheet](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf) · [Camera datasheet](https://www.mouser.com/catalog/specsheets/FRAMOS_2-12-2026_FSM.GO-IMX900_Datasheet_v1.2a.pdf)

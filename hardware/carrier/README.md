# Carrier

**Cur Plan:** one CM5, two cameras, SPI to the RP2350 transmitter and Ethernet for service or the E200 fallback. Record to eMMC. 3S battery, one-hour runtime target. The RP2350 lives on the transmitter board.

![Carrier block diagram](block-diagram.svg)

[Edit diagram](block-diagram.drawio) · [KiCad project](PigeonCarrier/PigeonCarrier.kicad_pro) · [Design master](../../calculations/design-master.xlsx)

| Block | Parts / connection |
| --- | --- |
| Compute | SC1596 / CM5104064: 4 GB RAM, 64 GB eMMC |
| CM5 connectors | 2 × Amphenol 10164227-1001A1RLF, 1.5 mm stack |
| Cameras | 2 × FRAMOS IMX900 + CIL212; short FRAMOS ribbon adapters preferred. Carrier connector/pinout still to select; MC50 is an alternative |
| Input protection | LM74502DDFR, 2 × CSD18540Q5B, SMBJ18CA; fuse still to select |
| Main 5 V | LM61495RPHR; CM5, 4 V camera LDO and transmitter core or E200 |
| RF 5 V | TPS62933FDRLR; separate supply, default off |
| Camera rails | 2 × TLV75801PDRVR: 4.0 V from 5V_MAIN, 1.8 V from CM5 3.3 V; shared by both cameras |
| Battery monitor | INA226AIDGSR + WSL25125L000FEA18, 5 mΩ |
| Sensors | BMI088 and BMP581, matching Aerolotl; optional ADXL375 |
| Camera control | TCA9406DCUR for I²C; SN74AXC4T245PWR for reset/sync |
| Ethernet | EDAC A70-112-331N126 with integrated magnetics |
| Service | Proposed USB-C recovery + nRPIBOOT on carrier; separate USB-C, BOOTSEL, reset and SWD on transmitter |
| Flight UART | Receive required; return TX optional. Logic voltage, connector and protocol TBD |

Both cameras power-cycle together. Hold resets low, establish 1.8 V before 4.0 V, then wait at least 200 ms after both rails are valid before releasing reset. The driver needs updating for this sequence. MIPI connects directly to CM5.

Sensors use filtered CM5 3.3 V. The transmitter core gets 5V_MAIN and makes its own local rails; the PA uses switched 5V_RF. Keep space, 5 V power and Ethernet for the E200 fallback. Battery input and flight UART go on the top sheet; power circuits go under Regulators.

Reserve these CM5 pins for PV-SPI. J1 numbers are the module connector pads, not IO-board header positions.

| Signal | CM5 GPIO | J1 pad | Pico Plus 2 GPIO |
| --- | --- | --- | --- |
| SCK | 11 | 38 | 17 |
| MOSI | 10 | 44 | 18 |
| CS_N | 8 | 39 | 19 |
| READY | 25 | 41 | 22 |
| MISO, reserved | 9 | 40 | 20 |

3.3 V logic with CM5 GPIO_VREF tied to CM5 3.3 V, common GND, 20 MHz SPI mode 0. On the transmitter: 4.7 kΩ CS_N pull-up and 4.7–6.8 kΩ READY pull-down. MISO is unused in v1. Keep sensors off this dedicated SPI bus. Reset, RF inhibit and connector pin numbering still need assignment.

PV-SPI v1 carries video only; USB currently starts/configures the Pico. Autonomous startup and status/control still need firmware work. USB-C service ports must not backfeed the battery rails. [Protocol](https://github.com/Rivercraft911/RP2350_IQ_Benchmark/blob/main/docs/pv-spi-spec.md) · [Measured SPI result](https://github.com/stanford-ssi/PigeonVision/blob/main/software/flight/spi.md)

Power control: J4 on the Regulators sheet connects an external pull-pin switch. Pin 1 is U2's `EN/UVLO` node (`PWR_EN`); pin 2 is GND. The switch must be **closed with the pin inserted (off), open with the pin removed (on)**. Closing it disables the LM74502 and both input MOSFETs; opening it restores the existing UVLO divider. It carries control current, about 1.68 mA at 12.6 V, rather than load current. This is not a latch: shut down the CM5 before reinserting the pin. An unplugged switch cable also permits startup.

With the pin inserted, the present dividers draw approximately `12.6 V / 7.5 kohm + 12.6 V / (51 + 4.7) kohm = 1.91 mA`, plus controller shutdown current and leakage. Off means battery power to downstream loads is disabled, not zero battery drain or isolation from external power. J4's connector/footprint and the mechanical switch remain to be selected; J4 has not been added to the PCB. Contact bounce and power transitions need bench verification. [LM74502 datasheet](https://www.ti.com/lit/ds/symlink/lm74502.pdf), sections 5, 6.5 and 8.3.

**To finish:** wire CM5 power/grounds and interfaces; select camera, SPI, USB-C and pull-pin connectors; assign camera enables/reset, RF inhibit, sensors and flight UART without GPIO conflicts. The saved camera, sensor and Ethernet sheets are still empty. CM5 connector pins are unwired.

The design master still budgets E200 power. Add a separate RP2350 + AFE7071 core load once estimated, keeping the E200 fallback as an alternative rather than adding both together. PA choice is also open: this repo previously used GRF5115; the transmitter draft proposes GRF5613. Confirm board dimensions, mounting and connector access before final layout.

[Library and sourcing](../libraries/README.md) · [CM5 datasheet](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf) · [Camera datasheet](https://www.mouser.com/catalog/specsheets/FRAMOS_2-12-2026_FSM.GO-IMX900_Datasheet_v1.2a.pdf)

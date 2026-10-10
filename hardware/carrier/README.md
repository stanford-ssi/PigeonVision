# Carrier

**Current plan:** one CM5, two cameras, SPI to the RP2350 transmitter and Ethernet for service or the E200 fallback. Record to eMMC. Use a 3S 5 Ah battery; one hour including pad time is a planning target. The RP2350 lives on the transmitter board. Carrier diameter is 5.7 in (144.78 mm).

![Carrier block diagram](block-diagram.svg)

[Edit diagram](block-diagram.drawio) · [KiCad project](PigeonCarrier/PigeonCarrier.kicad_pro) · [Design master](../../calculations/design-master.xlsx)

| Block | Parts / connection |
| --- | --- |
| Compute | SC1596 / CM5104064: 4 GB RAM, 64 GB eMMC |
| CM5 connectors | 2 × Amphenol 10164227-1001A1RLF, 1.5 mm stack |
| Cameras | 2 × FRAMOS IMX900 + CIL212; retain the working FRAMOS adapters. Two Hirose FH12-22S-0.5SH(55) FFC connectors with CM5IO-style pinout |
| Input protection | LM74502DDFR, 2 × CSD18540Q5B, SMBJ18CA; unfused input |
| Main 5 V | LM61495RPHR; CM5 and transmitter core or E200 |
| RF 5 V | TPS62933FDRLR; separate supply, default off |
| Camera rail | TPS62933FDRLR: shared 3V3_CAM from VBAT, enabled by 3V3_CM5; adapter boards provide camera-specific rails |
| Battery monitor | INA226AIDGSR + WSL25125L000FEA18, 5 mΩ |
| Sensors | BMI088, BMP581 and INA226 on CM5 GPIO2/3 I²C; polling without interrupt wiring |
| Camera control | Dedicated CM5 camera I²C lines; CAM0_RST from J1.97 and CAM1_RST from J1.100. Synchronization access reserved, synchronized exposures not required |
| Ethernet | EDAC A70-112-331N126 with integrated magnetics |
| Service | USB-C data/recovery, nRPIBOOT shunt, PWR_BUT button, J12 debug UART and CM5 activity LED on carrier; separate USB-C, BOOTSEL, reset and SWD on transmitter |
| Flight UART | Receive required; return TX optional. Logic voltage, connector and protocol TBD |

Both camera connectors receive the shared 3V3_CAM rail; MIPI connects directly to CM5. Keep the working adapter boards and their camera-specific power sequencing. The carrier does not replace their internal regulator circuitry.

Sensors use CM5 3.3 V. The transmitter core gets 5V_MAIN and makes its own local rails; the selected GRF5613 PA uses switched 5V_RF. Keep space, 5 V power and Ethernet for the E200 fallback. The bench input is a PJ-102AH barrel jack beside the battery connector, used with the battery unplugged.

Reserve these CM5 pins for PV-SPI. J1 numbers are the module connector pads, not IO-board header positions.

| Signal | CM5 GPIO | J1 pad | Pico Plus 2 GPIO |
| --- | --- | --- | --- |
| SCK | 11 | 38 | 17 |
| MOSI | 10 | 44 | 18 |
| CS_N | 8 | 39 | 19 |
| READY | 25 | 41 | 22 |
| MISO, reserved | 9 | 40 | 20 |

3.3 V logic with CM5 GPIO_VREF tied to CM5 3.3 V, common GND, 20 MHz SPI mode 0. On the transmitter: 4.7 kΩ CS_N pull-up and 4.7–6.8 kΩ READY pull-down. MISO is unused in v1. Keep sensors off this dedicated SPI bus. Transmitter reset and its connector pin numbering remain to be assigned.

The RF buck enable is GPIO17 / J1.50, with its existing 10 kΩ pull-down. Reserve GPIO22 / J1.46 for a separate 3.3 V PA_EN request. The proposed four-pin Molex 0430450418 PA connector carries 1: 5V_RF, 2: GND, 3: PA_EN, 4: GND. Local PA circuitry must default off and handle GRF5613 shutdown polarity and bias. This interface is a pin plan; it has not been wired into the carrier schematic. [PA plan](../rf-frontend/README.md).

PV-SPI v1 carries video only; USB currently starts/configures the Pico. Autonomous startup and status/control still need firmware work. USB-C service ports must not backfeed the battery rails. [Protocol](https://github.com/Rivercraft911/RP2350_IQ_Benchmark/blob/main/docs/pv-spi-spec.md) · [Measured SPI result](https://github.com/stanford-ssi/PigeonVision/blob/main/software/flight/spi.md)

J12 is a JST SH BM03B-SRSS-TB debug UART on CM5 GPIO14/15 using the Raspberry Pi debug-connector pinout: 1 RX into the CM5 (GPIO15), 2 GND, 3 TX from the CM5 (GPIO14). A Raspberry Pi Debug Probe plugs in with a straight-through cable. Both lines have 1 kΩ series resistors. The specification calls for 100 Ω, but CM5 GPIO pads are not failsafe, so the larger value limits back-feed from a powered probe into an unpowered CM5. LED4 (via R41, 220 Ω from 3V3_CM5) shows CM5 activity on LED_nACT.

CM5 configuration implied by this carrier: set `PSU_MAX_CURRENT=5000` in the bootloader EEPROM, because CC1/CC2 are unconnected and no USB-PD negotiation occurs. Set `enable_uart=1` for a Linux console on J12. Set `force_eeprom_read=0`, because ID_SD/ID_SC carry the CAM1 I²C bus.

Power control: J6 on Regulators uses the existing AMASS XT30PW-M connector. Pin 1 connects directly to U1 pin 1 (EN/UVLO / PWR_EN); pin 2 is GND. Use the microswitch COM and NO terminals: with the pin inserted and the lever pressed, closed contacts ground EN/UVLO and turn power off. Removing the pin releases the lever and opens the contact, allowing the existing UVLO divider to enable power. Confirm that the actual mechanism gives these contact states. An unplugged switch also allows power. This is not a latch: shut down the CM5 before reinserting the pin.

Q4, R39 and R40 were removed from the schematic and unrouted PCB. At 12.6 V, the closed contact carries 12.6 V / 7.5 kΩ = 1.68 mA through R2; R2 dissipates 21.2 mW. The existing UVLO and OV dividers remain unchanged. Total off-state divider draw is about 1.89 mA, plus controller current and leakage. Off disables downstream battery power; it does not isolate the battery or eliminate standby current. Switch contact quality, bounce and startup remain bench checks. [LM74502 datasheet](https://www.ti.com/lit/ds/symlink/lm74502.pdf).

Camera, sensor, Ethernet and USB/service hierarchy is connected, along with CM5 power/grounds and both regulator enables. The carrier USB-C port uses host VBUS only for data-switch control, with no connection to the battery or 5 V power rail. Its ESD footprint uses explicit external copper connections from each protected pad to the opposite NC pad. USB enumeration, recovery and power transitions remain hardware checks. The cooler fan header uses the native CM5 PWM/tach interface with 5V_MAIN supply; transmitter/Yapogee and PA interfaces still need completion.

The 2026-10-06 direct-switch check reports no ERC errors or warnings. All 200 CM5 contacts are connected or intentionally marked unused; all 147 components and 670 numbered PCB pads matched the exported schematic at that time (152 components after the 2026-10-07 additions; schematic parity remains clean). The switch change affects only J6.1 among the retained component connections. [Switch verification](../../build/pull-pin-direct-2026-10-06/verification.json). Unwired pins reserved for SPI and PA_EN currently have no-connect marks; replace those marks with the RF interface wiring when it is finalized. Power-source flags identify feeds through passive parts, including U7's internally charged bootstrap node. The review does not establish hardware performance: switch contact states, camera adapter current/sequencing, XT60 revision/fit, power transitions and final layout remain to be checked. [Connection review](../../build/carrier-closeout-2026-10-05/connection-review.json) · [Footprint review](../../build/carrier-closeout-2026-10-05/all-footprints-audit.json).

The design master still budgets E200 power. Add a separate RP2350 + AFE7071 core load once estimated, keeping the E200 fallback as an alternative. GRF5613 is selected for PA planning; its loaded current and thermal performance remain unmeasured. Confirm mounting and connector access before final layout.

[Library and sourcing](../libraries/README.md) · [CM5 datasheet](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf) · [Camera datasheet](https://www.mouser.com/catalog/specsheets/FRAMOS_2-12-2026_FSM.GO-IMX900_Datasheet_v1.2a.pdf)

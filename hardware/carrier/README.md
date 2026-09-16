# Carrier

**Cur Plan:** one CM5, two cameras, E200, power and sensors. Record to eMMC; retrieve over Ethernet. No separate MCU.

![Carrier block diagram](block-diagram.svg)

[Edit in draw.io](block-diagram.drawio)

| Block | Part / connection |
| --- | --- |
| Compute | SC1596 / CM5104064, 4 GB RAM, 64 GB eMMC. |
| CM5 connectors | Two Amphenol 10164227-1001A1RLF, 1.5 mm stack height, matching CM5IO. No components beneath the CM5. |
| Battery input | 3S preferred; 4S compatibility proposed. Fuse, reverse protection, TVS and input filter. |
| Power | Switched 5 V branches for CM5, E200 and RF frontend; camera and sensor rails. |
| Cameras | Two FRAMOS IMX900 modules, CIL212 lenses and FRAMOS FFA-A/MC50 adapters. I-PEX 20525-050E-02 on the carrier. |
| Sensors | BMI088 IMU and BMP581 barometer, shared with Aerolotl. ADXL375 high-g accelerometer optional. Digital power and temperature sensing. |
| Flight UART | Receive-only telemetry; voltage and protocol TBD. |
| Service | USB recovery, boot selector, debug UART, Ethernet and record/shutdown control. No native microSD with the eMMC CM5. |

Include camera level translation, power sequencing and hardware exposure sync.

Top sheet: battery connector and flight UART. Route battery input into Regulators, which contains input protection and the supply circuits. Add regulator subsheets as those circuits are selected.

The CM5 sheet contains two connector symbols and a mounting pattern from the [project library](../libraries/README.md). J1 and J2 each contribute one connector to the assembly BOM. The module itself is installed separately.

Use Aerolotl's sensor parts for simpler sourcing and shared sensor code where practical. Read them directly from CM5. BMI088 gyro range is ±2000°/s (5.56 turns/s); check expected spin before finalizing it. BMP581 covers 300–1250 hPa; revisit its range for a future 30,000 ft flight.

Camera path: PixelMate → purchased FFA-A/MC50 board → MC50 cable → carrier. The [FRAMOS kit](https://www.mouser.com/en/ProductDetail/FRAMOS/FFA-MC50-Kit-0.3m?qs=%252BHhoWzUJg4K3LtaE207mhw%3D%3D) includes both adapters and a 300 mm cable. The carrier replaces the processor-side adapter. Confirm camera screw spacing and cable length before layout.

Mount the bare E200 on standoffs above the carrier. Connect with a short Ethernet cable and separate regulated 5 V. The carrier jack also serves bench access; simultaneous connections need a switch.

Provide rail/enable/reset test points and status LEDs. PA enable defaults off.

Before layout: confirm board outline, mounting holes, camera pinout, rail currents and startup order.

## Power plan

**Cur Plan:** one hour on battery. 3S baseline, 4S-compatible input. Battery → fuse / TVS / reverse protection → three independent buck supplies. External charging; no USB power sharing in this revision.

These are preliminary parts, not completed circuits. IC current ratings do not establish board-level continuous current capability.

| Circuit | Candidate | Purpose |
| --- | --- | --- |
| Input protection | [LM74502DDFR](https://www.ti.com/product/LM74502), two external N-MOSFETs | Reverse polarity, disconnect, UV/OV thresholds and controlled startup. Fuse remains required; this is not a current-limiting eFuse or reverse-current blocker. |
| CM5 / camera input supply | [TPS56837RPAR](https://www.ti.com/product/TPS56837), 5.1 V | 8 A IC, 28 V input limit. Start with a 5 A branch allocation including camera supplies and peripherals. |
| E200 supply | [TPS62933FDRLR](https://www.ti.com/product/TPS62933F), 5.0 V | 3 A IC, 30 V input limit. Separate enable; allocate 2 A initially. |
| RF supply | TPS62933FDRLR, 5.0 V | Separate converter, hardware default-off. Allocate 1.5 A initially; measure driver and PA DC current. |
| Camera supplies | Two [TLV75801PDRVR](https://www.ti.com/product/TLV758P) LDOs | Shared 3.8 V from 5.1 V; shared 1.8 V from CM5_3.3V. Each IC rated 500 mA. |
| Sensors / control | CM5_3.3V | Budget all external loads within its 600 mA limit, including the camera 1.8 V LDO input. |
| Battery measurement | [INA226AIDGSR](https://www.ti.com/product/INA226) and Kelvin shunt | Measure protected battery-bus voltage and total current. |

Sourcing references: [LM74502 C3236215](https://jlcpcb.com/partdetail/TexasInstruments-LM74502DDFR/C3236215), [TPS56837 C22428366](https://jlcpcb.com/partdetail/TexasInstruments-TPS56837RPAR/C22428366), [TPS62933F C5219272](https://jlcpcb.com/partdetail/TexasInstruments-TPS62933FDRLR/C5219272), [TLV758 C2876308](https://www.lcsc.com/product-detail/C2876308.html), [INA226 C49851](https://jlcpcb.com/partdetail/TexasInstruments-INA226AIDGSR/C49851). Listings located; current assembly inventory and prices are not confirmed.

FRAMOS lists 245 mW at 3.8 V and 175 mW at 1.8 V per [IMX900 module](https://docs.framos.com/en/latest/FSMEcosystem/ProductDocumentation/FSMGO/IMX900.html). For two: `I3V8 = 2 × 0.245 / 3.8 = 0.129 A`; `I1V8 = 2 × 0.175 / 1.8 = 0.194 A`. Estimated LDO losses are 0.168 W and 0.292 W respectively. Verify the purchased modules and capture mode. Bring 1.8 V up before or with 3.8 V; hold camera reset low for more than 180 ms after the rails rise. Check power-down and signal backfeed too.

Battery sizing uses measured average load, not summed converter ratings. Provisional 30–40 W load, 90% overall conversion efficiency and 80% usable battery energy give `Epack = Pload × 1 h / (0.90 × 0.80) = 41.7–55.6 Wh`. At 3.6 V nominal per cell, a 3S 5 Ah pack is 54 Wh: about 58–78 minutes under those assumptions. Keep 3S 6 Ah or 4S 5 Ah as options if measured draw is near 40 W. Final capacity depends on cells, temperature and discharge cutoff.

At the provisional branch allocation, `5.1 V × 5 A + 5 V × 2 A + 5 V × 1.5 A = 43 W`. At 9 V input and 90% efficiency, `Ibat = 43 / (9 × 0.90) = 5.31 A`. The connector, fuse, protection FETs and copper need additional startup/fault allowance. 9 V is a calculation point, not a selected cell cutoff. A full 4S pack reaches 16.8 V; select TVS clamp and component ratings together.

Design master, once the Google Sheet exists: **Loads & Runtime**, **Converters**, **Input & Sequencing**. Next calculations are inductor ripple / saturation, capacitor effective capacitance / ripple / inrush, rail voltage tolerances, and losses. For now, keep regulator circuits undrawn until those values are selected.

CM5 supply starting point: a 75.0 kΩ / 10.0 kΩ divider gives 5.10 V. With 0.1% resistors and the TPS56837's full-temperature reference limits, the calculated static range is 5.015–5.186 V, excluding dynamic error and distribution loss. Only 64 mV remains to the CM5's 5.25 V upper limit, so overshoot matters. A preliminary 3.3 µH / 500 kHz combination gives 2.15 A peak-to-peak ripple at 16.8 V input and a 6.08 A peak at 5 A load. Include inductance/frequency tolerance and fault current before selecting the inductor. [TI design guidance](https://www.ti.com/lit/ds/symlink/tps56837.pdf).

References: [CM5 datasheet](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf), [FRAMOS MC50 mapping](https://docs.framos.com/en/latest/FSMEcosystem/ProductDocumentation/FFA/FFA-MC.html), [BMI088](https://www.bosch-sensortec.com/en/products/motion-sensors/imus/bmi088), [BMP581](https://www.bosch-sensortec.com/en/products/environmental-sensors/pressure-sensors/bmp581).

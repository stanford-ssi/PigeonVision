# Carrier

**Cur Plan:** one CM5, two cameras, E200, power and sensors. Record to eMMC; retrieve over Ethernet. No separate MCU.

![Carrier block diagram](block-diagram.svg)

[Edit in draw.io](block-diagram.drawio)

| Block | Part / connection |
| --- | --- |
| Compute | SC1596 / CM5104064, 4 GB RAM, 64 GB eMMC. |
| CM5 connectors | Two Amphenol 10164227-1001A1RLF, 1.5 mm stack height, matching CM5IO. No components beneath the CM5. |
| Battery input | 3S only, 12.6 V fully charged. Fuse, reverse protection, TVS and input filter. |
| Power | Main 5 V for CM5, E200 and camera regulators; separate RF supply. Filtered sensor branch from CM5_3.3V. |
| Cameras | Two FRAMOS IMX900 modules, CIL212 lenses and FRAMOS FFA-A/MC50 adapters. I-PEX 20525-050E-02 on the carrier. |
| Sensors | BMI088 IMU and BMP581 barometer, shared with Aerolotl. ADXL375 high-g accelerometer optional. Digital power and temperature sensing. |
| Flight UART | Receive-only telemetry; voltage and protocol TBD. |
| Service | USB recovery, boot selector, debug UART, Ethernet and record/shutdown control. No native microSD with the eMMC CM5. |

Include camera level translation, power sequencing and hardware exposure sync.

Top sheet: battery connector and flight UART. Route battery input into Regulators, which contains input protection and the supply circuits. Add regulator subsheets as those circuits are selected.

The CM5 sheet contains two connector symbols and a mounting pattern from the [project library](../libraries/README.md). J1 and J2 each contribute one connector to the assembly BOM. The module itself is installed separately.

Use Aerolotl's sensor parts for simpler sourcing and shared sensor code where practical. Read them directly from CM5. BMI088 gyro range is ±2000°/s (5.56 turns/s); check expected spin before finalizing it. BMP581 covers 300–1250 hPa; revisit its range for a future 30,000 ft flight.

Camera path: PixelMate → purchased FFA-A/MC50 board → MC50 cable → carrier. The [FRAMOS kit](https://www.mouser.com/en/ProductDetail/FRAMOS/FFA-MC50-Kit-0.3m?qs=%252BHhoWzUJg4K3LtaE207mhw%3D%3D) includes both adapters and a 300 mm cable. The carrier replaces the processor-side adapter. Confirm camera screw spacing and cable length before layout.

Mount the bare E200 on standoffs above the carrier. Connect with a short Ethernet cable and a branch from the main regulated 5 V supply. The carrier jack also serves bench access; simultaneous connections need a switch.

Provide rail/enable/reset test points and status LEDs. PA enable defaults off.

Before layout: confirm board outline, mounting holes, camera pinout, rail currents and startup order.

Still to size: power inductors and capacitors, protection FETs, fuse and current shunt. Interface candidates are below; connector choices and the camera GPIO map remain open. The PA and RF filtering remain on the separate RF board.

## Interfaces and protection

Preliminary circuit selection. [Project library and sourcing](../libraries/README.md#camera-interfaces-and-protection). The six semiconductor parts are available for JLC assembly; the EDAC jack is planned for external purchase and hand installation.

| Function | Candidate | Implementation |
| --- | --- | --- |
| Camera I²C | 2 × [TCA9406DCUR](https://www.ti.com/lit/ds/symlink/tca9406.pdf), C840107 | One per camera on separate CM5 buses. Camera side 1.8 V, CM5 side 3.3 V. |
| Camera reset / sync | [SN74AXC4T245PWR](https://www.ti.com/lit/ds/symlink/sn74axc4t245.pdf), C2867798 | Four channels in two direction-controlled pairs. Quantity follows the final GPIO map. |
| Gigabit Ethernet | [EDAC A70-112-331N126](https://www.mouser.com/catalog/specsheets/Edac_12-03-2025_A70_series.pdf), C43709820 | Integrated magnetic RJ45. JLC has no stock; purchase from Mouser and hand-install. |
| Ethernet ESD | 2 × [TPD4EUSB30DQAR](https://www.ti.com/lit/ds/symlink/tpd4eusb30.pdf), C90627 | Eight protected conductors; follow the CM5IO reference circuit. |
| USB recovery ESD | [USBLC6-2SC6](https://www.st.com/resource/en/datasheet/usblc6-2.pdf), C7519 | USB 2.0 D+/D− protection. Check VBUS sensing and avoid powering the carrier from USB. |
| Camera / UART ESD | [TPD4E05U06DQAR](https://www.ti.com/lit/ds/symlink/tpd4e05u06.pdf), C138714 | Low-capacitance candidate for exposed signal connections. Final population follows connector access and signal-integrity review. |
| Battery transient clamp | Littelfuse [SMBJ15CA](https://www.mouser.com/datasheet/2/240/Littelfuse_TVS-Diode_SMBJ-48653.pdf), C78809 | Candidate after the fuse, before reverse protection. Coordinate with input ratings and expected transients. |

MIPI CSI-2 pairs connect directly to the CM5; they do not pass through a GPIO level translator. A four-lane camera plus clock has ten conductors, so protecting all of them takes three four-channel ESD arrays per camera. Keep protection close to the connector with short ground returns; do not add generic high-capacitance TVS parts to these pairs.

TCA9406 has internal 10 kΩ pullups; account for existing board pullups and cable capacitance before adding more. Pull OE low at boot. Its OE input tolerates 5.5 V, so a 3.3 V CM5 GPIO can enable it after the camera rails are valid. Both ports isolate when either supply is at ground.

For SN74AXC4T245, use A = CM5 3.3 V and B = camera 1.8 V. Pull active-low output enables high and camera resets low for safe boot defaults. Fix each channel pair's direction for the chosen camera mode. One IC can carry two resets and two triggers; camera timing outputs need additional channels if used. Its partial-power-down support prevents normal GPIO drive from powering an unpowered camera through this translator.

The CM5 already contains the Ethernet PHY. Use the integrated jack magnetics and the [CM5IO reference wiring](https://pip.raspberrypi.com/categories/1098-design-files); no extra PHY or separate transformer bank. The local EDAC footprint corrects the reference geometry to the manufacturer's drawing. EDAC specifies 0–70 °C operation. No PoE.

Flight UART needs a confirmed logic voltage before selecting translation. If both ends are 3.3 V, use direct logic with connector ESD protection and series-resistor provisions. Keep local sensor buses on the carrier without unnecessary translators.

SMBJ15CA has 15 V standoff and specifies 24.4 V clamping at 24.6 A for the rated pulse. It is not a 15 V clamp or a sustained-overvoltage disconnect. Final input component ratings, fuse and FET selection must account for that clamp and wiring overshoot. Power inductors, capacitors and ferrite values remain for sizing.

## Power plan

**Cur Plan:** one hour on battery. 3S only (12.6 V maximum battery voltage). Battery → fuse / TVS / reverse protection → a main 5 V buck and a separate RF buck. External charging; no USB power sharing in this revision.

These are preliminary parts, not completed circuits. IC current ratings do not establish board-level continuous current capability.

| Circuit | Candidate | Purpose |
| --- | --- | --- |
| Input protection | [LM74502DDFR](https://www.ti.com/product/LM74502), two external N-MOSFETs | Reverse polarity, disconnect, UV/OV thresholds and controlled startup. Fuse remains required; this is not a current-limiting eFuse or reverse-current blocker. |
| Main 5 V | [LM61495RPHR](https://www.ti.com/lit/ds/symlink/lm61495.pdf) | 10 A buck, 3–36 V input. CM5, E200 and camera regulators share a provisional 7 A allocation. Start with 400 kHz; final setpoint and thermal design remain open. |
| E200 branch | Load switch, if independent reset is needed | Powered from main 5 V; no separate E200 converter. |
| RF supply | [TPS62933FDRLR](https://www.ti.com/product/TPS62933F), 5.0 V | 3 A buck, 3.8–30 V input, forced continuous switching. Separate converter, hardware default-off. Allocate 1.5 A initially; measure driver and PA DC current. |
| Camera supplies | Two [TLV75801PDRVR](https://www.ti.com/product/TLV758P) LDOs | Shared 4.0 V from main 5 V; shared 1.8 V from CM5_3.3V. Each LDO is rated 500 mA and has enable and output discharge. Power-cycle both cameras together. |
| Camera reset | CM5 driver and default-low reset circuitry | Software waits at least 200 ms after both rails are valid before releasing reset. No dedicated reset timer in the baseline. Rail ordering and safe boot states remain hardware requirements. |
| Sensors / control | CM5_3.3V; ferrite + local capacitors → SENSOR_3V3 | Budget all external loads within 600 mA, including the camera 1.8 V LDO. Select bead impedance under DC bias, DCR and damping; this is a filtered branch, not another regulator. |
| Battery measurement | [INA226AIDGSR](https://www.ti.com/product/INA226) and Kelvin shunt | Measure protected battery-bus voltage and total current. |

JLC assembly listings: [LM61495 C2943584](https://jlcpcb.com/partdetail/TexasInstruments-LM61495RPHR/C2943584), [LM74502 C3236215](https://jlcpcb.com/partdetail/TexasInstruments-LM74502DDFR/C3236215), [TPS62933F C5219272](https://jlcpcb.com/partdetail/TexasInstruments-TPS62933FDRLR/C5219272), [TLV758 C2876308](https://jlcpcb.com/partdetail/TexasInstruments-TLV75801PDRVR/C2876308), [INA226 C49851](https://jlcpcb.com/partdetail/TexasInstruments-INA226AIDGSR/C49851). All five listed parts were available for assembly when checked; all are Extended parts. Recheck stock and assembly fees with the final BOM. LCSC distributor inventory is separate.

Main-buck sizing: the guide's 70% current rule gives `10 A × 0.70 = 7 A`. The earlier 8 A TPS56837 allows 5.6 A under that rule. At 5 V / 7 A, output power is 35 W; assuming 90–95% efficiency gives 1.8–3.9 W total converter loss. These are design estimates, not measured efficiency or guaranteed board current. Check the inductor against peak current and the converter's current-limit range.

Camera requirements: [FRAMOS sections 5.2–5.4](https://www.mouser.com/catalog/specsheets/FRAMOS_2-12-2026_FSM.GO-IMX900_Datasheet_v1.2a.pdf) specify 3.7–5.1 V on `3V8_VDD`, 1.7–1.9 V on `1V8_VDD`, and 1.8 V control logic. Target 4.0 V for cable/switch drop allowance; verify tolerances at the camera connector. Earlier rail-power figures give preliminary combined loads of 129 mA and 194 mA for two cameras. These are sizing estimates, not measured startup peaks or a guarantee at 4.0 V.

Both cameras share one power sequence and power-cycle together. No per-camera power switches. Keep separate reset signals and power-off-safe control interfaces. Startup: hold both resets low, establish 1.8 V, then 4.0 V, then keep reset low for more than 180 ms after both rails are valid. Use regulator enables where practical; verify output discharge and signal backfeed so a power cycle actually removes camera power. Define shutdown and brownout behavior, with safe hardware defaults during CM5 boot.

Gate camera 4.0 V until 1.8 V is valid. With the preliminary currents above, LDO losses are `(5 − 4) × 0.129 = 0.129 W` and `(3.3 − 1.8) × 0.194 = 0.291 W`. Drawing the 1.8 V rail from main 5 V instead would dissipate 0.621 W in that LDO. Both estimates exclude quiescent current.

Software controls camera reset. Hardware must hold reset low before the driver runs, preserve rail ordering, and prevent control-signal backfeed. The driver must establish rail validity and then wait at least 200 ms before releasing reset, including every power cycle. Brownout handling remains to be defined. A reset supervisor is optional, not part of the baseline.

The [FRAMOS Pi driver inspected](https://github.com/framosimaging/framos-rpi-drivers/blob/c56eef0d232061fa953bccdf514737eeb92e706c/drivers/fr_imx900.c#L2162) toggles its reset GPIO and waits 25–30 ms in `imx900_power_on`; that routine alone does not establish the required 180 ms hold. Its overlay assumes the FRAMOS adapter and must be adapted to our GPIOs and shared supplies.

Selection check: keep LM61495 over [TPS56C215](https://www.ti.com/product/TPS56C215) for its 36 V operating input ceiling versus 17 V, accepting the lower 10 A versus 12 A current rating. Keep TPS62933F for forced continuous switching and adjustable soft start. TLV758 remains suitable if rail timing is established without a PG pin; a PG-equipped LDO is optional. LM74502 remains provisional: retain it for whole-board UV/OV disconnect, or use a PMOS if those functions are provided elsewhere. Neither topology alone replaces the fuse or pack protection.

Battery sizing uses measured average load, not summed converter ratings. Provisional 30–40 W load, 90% overall conversion efficiency and 80% usable battery energy give `Epack = Pload × 1 h / (0.90 × 0.80) = 41.7–55.6 Wh`. At 3.6 V nominal per cell, a 3S 5 Ah pack is 54 Wh: about 58–78 minutes under those assumptions. Use a larger 3S pack if measured draw is near 40 W; 3S 6 Ah is a candidate. Final capacity depends on cells, temperature and discharge cutoff.

At the previous allocations, the main rail needs up to 7 A and RF 1.5 A. At nominal 5 V this is `5 × (7 + 1.5) = 42.5 W`. At 9 V input and 90% efficiency, `Ibat = 42.5 / (9 × 0.90) = 5.25 A`. These are allocations, not measured consumption. Add startup/fault allowance for connector, fuse, FETs and copper. 9 V is a calculation point, not a selected cutoff. A full 3S pack reaches 12.6 V; input transients can exceed it.

Design master, once the Google Sheet exists: **Loads & Runtime**, **Converters**, **Input & Sequencing**. Next calculations are inductor ripple / saturation, capacitor effective capacitance / ripple / inrush, rail voltage tolerances, and losses. For now, keep regulator circuits undrawn until those values are selected.

Next: size the LM61495 divider, inductor and capacitor bank, then the protection FETs and camera sequencing circuit. Keep the imported TPS56837 as an earlier library candidate.

References: [CM5 datasheet](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf), [FRAMOS MC50 mapping](https://docs.framos.com/en/latest/FSMEcosystem/ProductDocumentation/FFA/FFA-MC.html), [BMI088](https://www.bosch-sensortec.com/en/products/motion-sensors/imus/bmi088), [BMP581](https://www.bosch-sensortec.com/en/products/environmental-sensors/pressure-sensors/bmp581).

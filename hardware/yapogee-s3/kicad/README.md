# Yapogee S3 KiCad

Open [YapogeeS3.kicad_pro](YapogeeS3.kicad_pro) in KiCad 10. Placement, routing and ground stitching are captured. USB breakout and silkscreen DRC follow-up remains.

| Sheet | Main parts |
| --- | --- |
| Root | USB-C, TPD2EUSB30, CC resistors, JP1 and the castellated edge |
| MCU | WROOM-1U-N16R8, direct host bus, RF control translator, local BOOT/RESET and native USB |
| POWER | TPS62933FDRLR, SRP5030TA-2R2M, TPS7A9001DSKR, TPS22919DCKR |
| RF_FRONT_END | RFFC5072A, TCXO/reference buffer, baluns, filter, SPF5043Z and SMA |

[Design Master](../design-master.xlsx) · [Datasheet PDFs](../datasheets.md) · [BOM](../BOM.csv) · [Interfaces](../interfaces.md)

The project tables include local IO and Power libraries. The IO library includes the module-edge strip symbol, assigned to the paired header/castellation footprint with 12 extra GND anchors. Power rails use global symbols; hierarchy ports carry signals. The BOM covers the captured circuit. Numbering is retained during the direct-host change. GPIO2 is free and castellation 14 is reserved.

Power, direct host GPIO, RF control translation and MCU EN/reset/boot support are captured. RF capture includes matching, PLL loop, reference, supplies and driver. The RF sheet uses A3 for spacing. Design Master contains capacitor screens and loop-model estimates; hardware stability is unmeasured.

J1 carries the closed 55 × 43.18 mm Edge.Cuts outline, R1 outer corners and the Samtec drop-in notch. The 19 mm U.FL harness remains one arc on Dwgs.User. Headers sit 1.60 mm inboard with continuous oval copper lands; retain manual edge soldering and no carrier paste. The JLCPCB JLC04161H-7628 standard 1.6 mm four-layer stackup is entered: 1 oz outer copper, 0.5 oz inner copper.

JP2 BOOT and JP3 RESET are bare, normally-open PCB jumper pads. For manual download, close BOOT during reset. Leave both open for normal operation; briefly short RESET to pull EN to GND. Native USB provides the usual ESP flashing path, without a UART bridge or Pi BOOT control.

## RF and routing

| Network | Populated values |
| --- | --- |
| Input pad | 3 dB nominal; 300 / 18 / 300 |
| Balanced DC blocks | C32/C33 and C59/C60: 100 pF each |
| Mixer output feeds | L3/L4: 7.5 nH each, LQW15AN7N5G80D |
| Driver feed | L2: 100 nH |
| Frequency plan | 3700 MHz LO − 2420 MHz input = 1280 MHz output; spectrum inverted |

Matching uses nominal manufacturer port and passive models. Bias 6 input impedance is unknown; assembled matching, output power and EVM remain unmeasured. Keep balanced connections short and symmetric. L3/L4 may need tuning after assembly.

MODE uses a 10k pullup. EXT_LO and EXT_LO_DEC stay open with the internal LO. The reference divider is nominally 1.2 Vpp before REF_IN loading. The driver uses switched 3.328 V minus switch, resistor and choke drops. Its 3 V characterization is typical; gain, stability and EVM need measurement. [RF plots](../plots/README.md).

| Netclass | Width (mm) | Pair gap (mm) | Coplanar GND gap (mm) |
| --- | ---: | ---: | ---: |
| RF50 | 0.32 | — | 0.25 |
| USB90 | 0.235 | 0.15 | 0.25 |
| RF_MATCH | 0.20 | — | — |
| REF40 | 0.20 | — | — |

RF50 and USB90 use masked L1 traces over continuous L2 GND. Hold the listed GND gaps with the actual copper pours; netclass clearance is only a minimum. RF_MATCH marks short lumped-network connections, not controlled 100/200 Ω lines. Vendor geometry still requires fabrication/CAM confirmation. [Stackup](../stackup.json) · [JLC calculator](https://jlcpcb.com/pcb-impedance-calculator)

Continuous RF wires use automatic net names with explicit netclass patterns. Recheck assignments after reannotation. USB_P/N and MCU UD_P/N are recognized differential pairs on both sides of R3/R4. REF40 is a short clock interconnect default, without an impedance target. Bypass captions assign each capacitor pair to its physical supply pin.

[JLC fabrication and routing rules](YapogeeS3.kicad_dru) are loaded with RF50, RF_MATCH, REF40, USB90 and power netclasses. RF50 and USB90 have explicit 0.25 mm clearance to GND. USB retains 0.15 mm pair spacing. Standard through vias only; USB breakout spacing and small existing silk still need DRC review.

Use netclass widths. Supply defaults are 0.75 mm for 5 V, 0.50 mm for 3V3_MAIN and 0.30 mm for RF/driver supplies; use pours where practical. These widths are starting values, not current or thermal qualifications. Default vias are 0.60/0.30 mm. New zones use netclass clearances; maintain the calculated 0.25 mm GND gaps along RF50 and USB90.

Ground stitching uses 0.50/0.20 mm vias across the board, with closer placement along RF paths and the shield. Minimum via diameter is 0.45 mm with a 0.125 mm annulus; via-to-via hole spacing is 0.20 mm. Component-hole spacing remains 0.45 mm. Refill and rerun DRC after routing changes. Stitch placement is not an EM validation.

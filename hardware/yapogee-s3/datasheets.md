# Manufacturer PDFs

Quick references for schematic capture and layout, checked 2026-10-28. Page numbers below are PDF pages, counted from 1. Family datasheets contain several packages and pinouts; use the exact order code and package listed here.

## MCU

| Selected part | Manufacturer PDF | Start here |
| --- | --- | --- |
| ESP32-S3-WROOM-1U-N16R8 | [WROOM-1 / WROOM-1U, v1.8](https://documentation.espressif.com/esp32-s3-wroom-1_wroom-1u_datasheet_en.pdf) | Table 1-2 for N16R8; pp. 10–12 for pins/reserved memory GPIOs; pp. 40–43 for module/peripheral schematics and dimensions; p. 46 for the **1U** land pattern. |
| SN74AXC4T774PWR | [SN74AXC4T774, SCES898C](https://www.ti.com/lit/ds/symlink/sn74axc4t774.pdf) | Rev. C, May 2022. Pinout p. 3; operating limits pp. 4–5; partial-power behavior and OE/DIR control in the detailed description; **PW0016A** package/lands/stencil pp. 36–38. |
| TLV803EA30DBZR, reset supervisor | [TLV803E family, SLVSES2J](https://www.ti.com/lit/ds/symlink/tlv803e.pdf) | Rev. J, May 2021. Order-code naming p. 3; choose the default **DBZ** pinout on pp. 4–5; reset behavior pp. 16–19; DBZ package/lands pp. 39–41. |
| TPD2EUSB30DRTR, USB data ESD | [TPDxEUSB30, SLVSAC2G](https://www.ti.com/lit/ds/symlink/tpd2eusb30.pdf) | Rev. G, June 2021. **DRT, 3 pins:** pinout p. 3; placement/routing pp. 11–12; DRT package drawing p. 26. The drawing is an image and may not appear in text searches. |
| USB4105-GF-A-060 | [GCT USB4105 drawing](https://gct.co/files/drawings/usb4105.pdf) | Rev. B4, December 18, 2023. Sheet 1: connector pin map, board edge, lands, slots and holes. |

## POWER

| Selected part | Manufacturer PDF | Start here |
| --- | --- | --- |
| TPS62933FDRLR, main buck | [TPS6293x, SLUSEA4D](https://www.ti.com/lit/ds/symlink/tps62933.pdf) | Rev. D, August 2022. Pinout p. 3; component selection pp. 28–34; layout pp. 40–41; **DRL0008A** package/lands/stencil pp. 48–50. Use the **F** variant's FCCM behavior. |
| TPS7A9001DSKR, quiet RF LDO | [TPS7A90, SBVS324B](https://www.ti.com/lit/ds/symlink/tps7a90.pdf) | Rev. B, September 2026. Pins p. 3; feedback, NR/SS, capacitors and thermal design pp. 18–24; **DSK0010A** package/lands/stencil pp. 32–34. |
| TPS22919DCKR, driver supply switch | [TPS22919, SLVSEN5B](https://www.ti.com/lit/ds/symlink/tps22919.pdf) | Rev. B, May 2019. Pins p. 3; application and inrush pp. 15–17; **DCK0006A** package/lands/stencil pp. 24–26. |
| SRP5030TA-2R2M, main inductor | [Bourns SRP5030TA](https://www.bourns.com/docs/product-datasheets/srp5030ta.pdf) | Electrical ratings and recommended lands p. 1; current/inductance curves p. 2. Lands are 2.0 × 1.8 mm at x = ±2.25 mm. |
| GCM31CR71A226KE02L, bulk capacitor | [Murata reference sheet](https://pim.murata.com/asset/pim4/ceramicCapacitorSMD/GCM31CR71A226KE02-01A-EN_PDF_CERAMICCAPACITORSMD) | 22 µF, 10 V, X7R, 1206. Use exact-part DC/AC curves in Design Master. |
| GCM155R71H104KE02D, bypass | [Murata reference sheet](https://pim.murata.com/asset/pim4/ceramicCapacitorSMD/GCM155R71H104KE02-01A-EN_PDF_CERAMICCAPACITORSMD) | 100 nF, 50 V, X7R, 0402. |
| GCM1885C1H103JA16D, SS/CFF | [Murata reference sheet](https://search.murata.co.jp/Ceramy/image/img/A01X/G101/ENG/GCM1885C1H103JA16-01A.pdf) | 10 nF, 50 V, C0G, 0603, ±5%. |
| KAM15AR71A225KT, switch input | [Kyocera reference sheet](https://spec.ele.kyocera.com/specifications/KAM15AR71A225KT.pdf) | 2.2 µF, 10 V, X7R, 0603. |

## RF_FRONT_END

TCXO, PLL, mixer and RF signal conditioning stay together on this sheet.

| Selected part | Manufacturer PDF | Start here |
| --- | --- | --- |
| RFFC5072ATR13 | [RFFC5071A/2A, Rev. E](https://www.qorvo.com/products/r/ra000741) | April 8, 2022. Supply/specifications p. 2; loop filter pp. 5–6; wideband circuit p. 11; Mixer 2 curves p. 18; pins p. 23; package/lands p. 24. |
| SPF5043Z, driver | [SPF5043Z](https://www.qorvo.com/products/r/ra001884) | June 12, 2018. Supply/specifications p. 2; **3 V** typical characterization p. 3; pin functions p. 12; package p. 13. |
| ATXAIG-H13-F-40.000MHZ-F25-T3 | [ATXAIG-H13 CMOS TCXO](https://abracon.com/datasheets/ATXAIG-H13.pdf) | Revised February 3, 2023. Electrical/options pp. 1–2; pin functions, dimensions and recommended lands p. 3. Pin 1 is **INH**. |
| 74LVC1G34GV,125 | [Nexperia 74LVC1G34](https://assets.nexperia.com/documents/data-sheet/74LVC1G34.pdf) | Rev. 11, November 13, 2024. Pins and limits pp. 2–5; timing p. 7; **GV / SOT753** outline p. 10. |
| 2450BL15B0100001E, input balun | [Johanson 2450BL15B0100001E](https://www.johansontechnology.com/docs/4499/Balun-2450BL15B0100001E.pdf) | Rev. 4.0, document 36S0022A. Electrical limits p. 1; terminal orientation/dimensions p. 2; PCB layout p. 3. |
| BD1222J50200AHF, output balun | [TTM BD1222J50200AHF](https://cdn.ttm.com/repository/products/wireless-xinger/balun-transformers/BD1222J50200AHF/BD1222J50200AHF.pdf) | Rev. E. Pin assignment, package and port impedances p. 1; layout/characterization on the following pages. Read the drawing's view convention before assigning terminals. |
| LQW15AN7N5G80D, mixer output feeds | [Murata JELF243A_0100K-01](https://pim.murata.com/asset/pim4/inductor/JELF243A-0100_PDF_INDUCTOR) | 7.5 nH ±2%. Electrical table p. 5; 1.0 × 0.6 mm body p. 8; reference lands p. 13. Use `PigeonVision:Murata_LQW15AN_80_1006`; the older `_00` series land pattern differs. |
| LQW18ANR10J00D, driver choke | [Murata JELF243A_0024AC-01](https://pim.murata.com/asset/pim4/inductor/JELF243A-0024_PDF_INDUCTOR) | Exact part p. 3; body p. 4; reference lands p. 9. `PigeonVision:Murata_LQW18AN_00_1608` uses 0.70 mm inner gap, 1.95 mm outer span and 0.85 mm land width. |
| LFCN-1450+, converter output LPF | [Mini-Circuits LFCN-1450+](https://www.minicircuits.com/pdfs/LFCN-1450%2B.pdf) | Rev. M. Electrical response p. 1; **FV1206** outline and PCB land pattern p. 2. |
| SMA-J-P-H-ST-EM3 | [Samtec SMA-J-P-X-ST-EM3 drawing](https://suddendocs.samtec.com/prints/sma-j-p-x-st-em3-mkt.pdf) | Rev. D, sheet 1. Drop-in notch, R0.40 reliefs, signal/ground lands and body clearance. |
| U.FL-R-SMT-1(80) | [Hirose U.FL catalog](https://www.hirose.com/product/download/?distributor=all&lang=en&series=U.FL&type=catalogue) | Catalog cover dated September 2024. Receptacle outline, recommended copper, under-body keepout and stencil p. 12. Match **U.FL-R-SMT-1**, rather than the different SMT-K version. |
| BMI-S-206-F frame / BMI-S-206-C lid | [Laird BMI-S-206 drawing](https://www.laird.com/sites/default/files/bmi-s-206-sales.pdf) | July 14, 2020, single sheet. Frame lands, lid dimensions and assembled height. Frame and lid are separate parts. |
### RFFC programming and matching

These are manufacturer downloads linked from the [RFFC5072A product page](https://www.qorvo.com/products/p/RFFC5072A). Qorvo may serve the PDFs as generic binary downloads.

- [Integrated Synthesizer/Mixer Programming Guide](https://www.qorvo.com/products/r/ra000757): version 2.0, June 25, 2012; section 2.3.1 on p. 9 derives divider values. Also covers calibration and A-series temperature compensation.
- [Integrated Synthesizer/Mixer Register Map](https://www.qorvo.com/products/r/ra000758): register definitions used alongside the programming guide. Retrieve the current manufacturer document before writing the control driver.
- [Integrated Synthesizer/Mixer Matching Circuits and Baluns](https://www.qorvo.com/products/r/ra000753): port matching and balun arrangements; read alongside the selected balun drawings and the Rev. E mixer pin notes.

The quiet regulator uses two fitted 22 µF capacitors per side. TI's 50% derating wording and the adopted capacitance interpretation are recorded in [Design Master](design-master.xlsx). Solder the exposed thermal pad. Nominal capacitance alone does not establish effective capacitance or hardware stability.

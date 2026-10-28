# Yapogee S3 KiCad project

Open [YapogeeS3.kicad_pro](YapogeeS3.kicad_pro) in KiCad 10.

- [Schematic](YapogeeS3.kicad_sch): capture in progress; selected parts are in the [BOM](../BOM.csv).
- [PCB](YapogeeS3.kicad_pcb): 53 × 40 mm mechanical starting envelope with six reference footprints: edge interface, shield frame, SMA, USB, WROOM module and RF input socket. The 25 mm jumper path is a drawing envelope. No circuit nets or routing are present.
- Library tables resolve through ${KIPRJMOD}/../../libraries.

| Sheet | Main components |
| --- | --- |
| MCU | ESP32-S3-WROOM-1U-N16R8; TLV803EA30DBZR reset supervisor; two SN74AXC4T774PWR host translators; USB data routing/protection, BOOT/RESET, UART and carrier signals |
| POWER | STUSB4500LQTR and SN74LVC1G97DBVR USB qualification; TPS2116DRLR source mux; TPS62933FDRLR main buck at 3.328 V; TPS7A9001DSKR quiet LDO at 3.000 V; TPS22919DCKR driver supply switch |
| RF_FRONT_END | U.FL/input pad; 2450BL15B0100001E balun; RFFC5072ATR13 and PLL loop filter; H13 TCXO/74LVC1G34 buffer; third SN74AXC4T774PWR; BD1222J50200AHF balun, LFCN-1450+, SPF5043Z, output pad and SMA |

Keep bypass, feedback, protection and bias parts with their circuits. The module includes flash, PSRAM, crystal and chip RF matching. GRF5613 and its final output filter belong to the separate PA board. The CSV BOM is the selected-part plan; reconcile its designators with the captured schematic before exporting a manufacturing BOM.

[RF signal path](../block-diagram.svg) · [Power domains](../power.svg) · [Editable diagrams](../block-diagram.drawio)

Board-only J100 contains the real Edge.Cuts, SMA notch and 19 physical edge pads. It is excluded from purchased BOM and placement files. Use PigeonVision:YapogeeS3_ModuleEdge_Interface16 for electrical capture and reconcile the existing footprint. A matching carrier symbol/landing is available; its carrier cutout remains a planning contour.

U101 includes its flash, crystal, PSRAM and chip RF match. Its antenna socket connects to J502 through offboard W501, not a fabricated electrical pad on the module footprint. Place through the exact WROOM-1U-N16R8 symbol; module pads 28–30 are reserved for PSRAM. The nine center ground lands and thermal via implementation require assembly review.

[Power/interface contract](../interfaces.md) · [Mechanical dimensions](../mechanical-interface.json) · [Stackup basis](../stackup.json) · [Offboard cable BOM](../BOM_offboard.csv)

Keep generated exports in ignored output/. Working research, prices and vendor downloads stay outside the public repository. Complete capture, placement, fabrication stackup/impedance setup and hardware qualification remain.

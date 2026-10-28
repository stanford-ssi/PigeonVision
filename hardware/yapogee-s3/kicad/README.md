# Yapogee S3 KiCad project

Open [YapogeeS3.kicad_pro](YapogeeS3.kicad_pro) in KiCad 10.

- [Schematic](YapogeeS3.kicad_sch): unwired A4 starter; selected parts are in the [BOM](../BOM.csv).
- [PCB](YapogeeS3.kicad_pcb): 53 × 40 mm mechanical starting envelope with six reference footprints: edge interface, shield frame, SMA, USB, WROOM module and RF input socket. The 25 mm jumper path is a drawing envelope. No circuit nets or routing are present.
- Library tables resolve through ${KIPRJMOD}/../../libraries.

Board-only J100 contains the real Edge.Cuts, SMA notch and 19 physical edge pads. It is excluded from purchased BOM and placement files. Use PigeonVision:YapogeeS3_ModuleEdge_Interface16 for electrical capture and reconcile the existing footprint. A matching carrier symbol/landing is available; its carrier cutout remains a planning contour.

U101 includes its flash, crystal, PSRAM and chip RF match. Its antenna socket connects to J502 through offboard W501, not a fabricated electrical pad on the module footprint. Place through the exact WROOM-1U-N16R8 symbol; module pads 28–30 are reserved for PSRAM. The nine center ground lands and thermal via implementation require assembly review.

[Power/interface contract](../interfaces.md) · [Mechanical dimensions](../mechanical-interface.json) · [Stackup basis](../stackup.json) · [Offboard cable BOM](../BOM_offboard.csv)

Keep generated exports in ignored output/. Working research, prices and vendor downloads stay outside the public repository. Capture, complete placement, fabrication stackup/impedance setup and hardware qualification remain.

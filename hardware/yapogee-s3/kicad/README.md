# Yapogee S3 KiCad project

Open [YapogeeS3.kicad_pro](YapogeeS3.kicad_pro) in KiCad 10.

- [Schematic](YapogeeS3.kicad_sch): unwired A4 starter; exact parts are in the [BOM](../BOM.csv).
- [PCB](YapogeeS3.kicad_pcb): 53 × 40 mm mechanical starting envelope, four copper layers, castellations, shield frame, SMA and USB. These are mechanical reference placements with no circuit nets or routing; reconcile them during schematic capture.
- Library tables resolve through ${KIPRJMOD}/../../libraries.

Board-only J100 contains the real Edge.Cuts, including the SMA notch, and 19 physical edge pads. It is excluded from the purchased BOM and placement files. Use PigeonVision:YapogeeS3_ModuleEdge_Interface16 for its electrical capture and reconcile the existing footprint. The matching carrier symbol/landing is also available; its cutout is a planning contour requiring separate carrier integration.

[Power and interface contract](../interfaces.md) · [Mechanical dimensions](../mechanical-interface.json) · [Selected stackup basis](../stackup.json)

Keep generated exports in the ignored output/ directory. Research and vendor downloads stay outside the public repository. The original carrier is unchanged. Circuit capture, placement, actual fabrication stackup/impedance setup and hardware qualification remain.

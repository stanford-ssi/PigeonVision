# Yapogee S3 KiCad project

Open [YapogeeS3.kicad_pro](YapogeeS3.kicad_pro) in KiCad 10 to begin the transmitter schematic and PCB.

- [Schematic](YapogeeS3.kicad_sch): an A4 starter with the selected signal path noted; no components or electrical connections yet.
- [PCB](YapogeeS3.kicad_pcb): empty native board; the design basis is four copper layers and 1.6 mm FR4. Outline, fabricator dielectric stackup, impedance geometry and routing remain to be designed.
- `sym-lib-table` and `fp-lib-table`: reference the shared PigeonVision library through `${KIPRJMOD}/../../libraries`, so the checkout can move.

Keep transmitter project files here, component libraries under the shared hardware library, and generated exports and checks in this folder's ignored `output/` directory. The carrier is a separate KiCad project under `hardware/carrier/`.

The [architecture and interfaces](../README.md) describe the nominal 5 V input and selected bare-chip converter chain. The [BOM](../BOM.csv) assigns exact MPNs, library symbols and local footprints. Place parts through the `PigeonVision` library; DNP is an instance assembly choice. Vendor drawings and working reviews stay outside the public repository. Circuit capture and hardware qualification remain to be completed.

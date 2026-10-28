# Yapogee S3 KiCad project

Open [YapogeeS3.kicad_pro](YapogeeS3.kicad_pro) in KiCad 10 to begin the transmitter schematic and PCB.

- [Schematic](YapogeeS3.kicad_sch): an A4 starter with the signal path noted; no components or electrical connections yet.
- [PCB](YapogeeS3.kicad_pcb): empty native board; outline, stackup, layers, impedance geometry and routing remain to be designed. The default two copper layers are not an RF stackup decision.
- `sym-lib-table` and `fp-lib-table`: reference the shared PigeonVision library through `${KIPRJMOD}/../../libraries`, so the checkout can move.

Keep transmitter project files here, component libraries under the shared hardware library, and generated exports and checks in this folder's ignored `output/` directory. The carrier is a separate KiCad project under `hardware/carrier/`.

The [architecture and interfaces](../README.md) describe the nominal 5 V input, converter candidates and waveform requirements. Pin assignments, purchased parts and PA drive remain open; this starter does not select an IC or establish a working circuit.

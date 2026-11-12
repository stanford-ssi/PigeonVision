# PigeonCarrier Rev 1.1

- [PCB fabrication ZIP](PigeonCarrier-Rev1.1.zip) with six copper layers, paste, mask, legend, outline, plated and unplated drills, and the two via process files.
- [PCB BOM](PigeonCarrier-Rev1.1_bom.csv) with 148 fitted references. J1, J2, J6 and J9 are through-hole and soldered by hand. J14 uses Molex 0430450418, DigiKey WM1802CT-ND.
- [Placement CSV](PigeonCarrier-Rev1.1_positions.csv) with 144 top-side parts in mm. X and Y use the Gerber origin, with Y increasing upward. Rotation follows the KiCad footprint. Set the feeder orientation in LumenPnP.
- [Electrical test netlist](netlist.ipc).

The CM5, cooler, recovery shunt and Yapogee S3 are installed separately. Hand solder the Yapogee S3 to J13.

Use the [carrier fabrication notes](../../fabrication-notes.md) for the order. Confirm JLC06161H-3313, 1.6 mm, ENIG and epoxy fill and copper cap on all 645 vias in the CAM preview.

Fabrication Toolkit generated the copper, outline, paste, BOM, placement and test netlist. The archive uses KiCad mask and silkscreen plots to retain the board mask settings, alternate-mode Excellon for the plated slots, and KiCad fill and cap Gerbers. ERC found zero violations. DRC found zero errors, zero unconnected items and five footprint library warnings with matching pad and drill geometry.

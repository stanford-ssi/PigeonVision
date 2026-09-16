# Project library

CM5 symbols and connector geometry are derived from Raspberry Pi's [CM5IO revision 2 design files](https://pip.raspberrypi.com/categories/1098-design-files), archive `RP-008099-DD-1`. The supplied connector STEP model remains subject to its manufacturer's terms. See the [CM5 datasheet](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf) and [Amphenol drawing](https://cdn.amphenol-cs.com/media/wysiwyg/files/drawing/10164227.pdf).

| Symbol | PCB item | Assembly BOM |
| --- | --- | --- |
| `CM5_J1` | GPIO/power connector, 100 pads | One 10164227-1001A1RLF |
| `CM5_J2` | High-speed connector, 100 pads | One 10164227-1001A1RLF |
| `CM5_Mount` | Module outline and four mounting holes | Excluded |

J1 uses CM5 pins 1–100. J2 uses connector-local pins 1–100, corresponding to CM5 pins 101–200. [Pin map](cm5-pin-map.csv). Signal names and electrical types retain the reference symbol's module-side meaning. J2 has its own footprint to preserve the reference design's module trace-length offsets. Both footprints purchase the same connector; group the assembly BOM by MPN and Value for quantity two. The SC1596 module is purchased separately and installed after PCB assembly.

For placement at 0° on the front of the carrier, relative to the centre of `CM5_Mount`:

| Item | X (mm) | Y (mm) | Rotation |
| --- | ---: | ---: | ---: |
| J1 | -17 | +2.5 | 0° |
| J2 | +17 | +2.5 | 0° |
| Mounting holes | ±16.5 | ±24 | |

Position these numerically, then group and lock them. Updating from the schematic creates separate footprints; it does not automatically position the connectors relative to each other. The mounting footprint's origin is the module centre, not a screw hole. Its outline is a drawing, not an enforced component keepout. No other components belong underneath the module with the 1.5 mm stack.

Checked independently: all 200 signal-to-pad mappings and copper positions against the source design, 35 high-speed trace-length offsets, cached schematic symbols, netlist export and two-connector BOM. Both connector courtyards have one outline, no holes, and contain all pad centres. Scratch-board placement exports contain only J1 and J2. PCB placement, connector orientation against the purchased parts, antenna clearance and final assembly exports still need review before fabrication.

## Library workflow

1. **Source:** record the exact MPN, package, datasheet revision and manufacturer land pattern. Check the installed KiCad library before creating a part.
2. **Import:** one agent owns the symbol/footprint files. Record every pin mapping, exposed pad, mechanical pad and numbering translation. Keep source downloads and previews in `build/`.
3. **Independent check:** another agent compares the result with the source, including top/bottom view, pin 1, pitch, paste, mask, courtyard, component height and mating clearance. Reusing an import script is not an independent check.
4. **KiCad check:** load and render the symbol/footprint, check courtyard containment and clearances, then export a sample netlist, BOM and placement file. Check MPN, quantity, origin and rotation. Resolve findings before placing the part in the board design.
5. **Board review:** review the real placement, pin-to-net mapping, 3D fit and final assembly exports. Library checks alone do not release a PCB for fabrication.

Keep checks and limitations beside each part in this file. New imports remain provisional until independently checked. Preserve user edits; agents work on assigned files, and schematic placement follows review.

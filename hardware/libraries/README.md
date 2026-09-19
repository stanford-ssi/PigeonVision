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

## Power parts

Start with these candidates from the carrier power plan. New parts need **Manufacturer**, **MPN**, **Datasheet** and **Footprint** fields, per SD-100001 section 3.4. Add a hidden **LCSC Part #** symbol field for assembly sourcing. Reuse existing KiCad geometry after checking the exact package.

| Function | Exact MPN | Library source |
| --- | --- | --- |
| Main buck | LM61495RPHR | Project symbol and TI RPH-16 footprint |
| Earlier buck candidate | TPS56837RPAR | Project entry; TI RPA-10 HotRod. Retained, not selected for the shared main supply. |
| Battery protection candidate | LM74502DDFR | Project symbol and local TI DDF-8 footprint |
| RF buck | TPS62933FDRLR | Project copy of KiCad symbol and SOT-583-8 footprint |
| Camera LDOs | TLV75801PDRVR | Project symbol and TI DRV-6 footprint, including ground pad 7 |
| Battery monitor | INA226AIDGSR | Project symbol and TI DGS-10 footprint |
| Reset timer | None in baseline | CM5 driver provides delay; hardware supplies safe reset defaults and rail ordering. |

For imports, use exact-MPN symbols in `PigeonVision.kicad_sym`. Copy and check existing KiCad symbols before drawing new ones. Reuse verified stock footprints, or keep custom/revised footprints in `PigeonVision.pretty`; never edit the installed global libraries. Both project library tables already use relative paths.

| MPN | LCSC Part # |
| --- | --- |
| LM61495RPHR | C2943584 |
| TPS62933FDRLR | C5219272 |
| TLV75801PDRVR | C2876308 |
| LM74502DDFR, provisional | C3236215 |
| INA226AIDGSR | C49851 |

The sourcing field belongs on the symbol and is exported in the BOM. A package footprint can serve many purchasable parts. Group assembly quantities by exact MPN, footprint and sourcing code. During library updates, preserve intentional per-instance fields and review the diff. [JLC export guide](https://jlcpcb.com/help/article/how-to-generate-the-bom-and-centroid-file-from-kicad).

All five exact-MPN symbols and assigned footprints are in the project libraries, with hidden sourcing fields. JLCImport supplied EasyEDA source data; TI drawings determined the final geometry. Existing KiCad symbols and footprints were reused where suitable. RPH compound pads, DRV pads/paste and DGS row spacing were corrected to the manufacturer examples.

KiCad 10 sources: `Regulator_Switching:TPS62933F`, `Regulator_Linear:TLV75801PDRV`, `Sensor_Energy:INA226`, and the corresponding DRL, DRV, DGS and DDF package footprints. Derived library content retains the [KiCad library license](https://www.kicad.org/libraries/license/).

Independently checked: all 49 pin-to-pad mappings, exact MPN/LCSC fields, footprint origins, copper geometry, paste and courtyards. LM61495 center pad 16 is SW; TLV758 exposed pad 7 is GND. The retained DDF stock footprint has different corner rounding from TI's illustration, with matching nominal pad extents. No verified 3D models were added. Thermal copper, stencil thickness and final assembly rotations still depend on the board design. A library entry is not a finished regulator circuit.

Parts pages: [LM61495 / C2943584](https://www.lcsc.com/product-detail/C2943584.html), [TPS56837 / C22428366](https://www.lcsc.com/product-detail/C22428366.html), [LM74502 / C3236215](https://www.lcsc.com/product-detail/C3236215.html), [TPS62933F / C5219272](https://jlcpcb.com/partdetail/TexasInstruments-TPS62933FDRLR/C5219272), [TLV758 / C2876308](https://www.lcsc.com/product-detail/C2876308.html), [TPS3808 / C19653](https://www.lcsc.com/product-detail/C19653.html), [INA226 / C49851](https://jlcpcb.com/partdetail/TexasInstruments-INA226AIDGSR/C49851). Distributor stock does not establish JLC assembly stock; check the assembly BOM before ordering.

Select inductors and capacitors after ripple, current-limit, bias and inrush calculations. Under the guide's 60% ceramic-voltage rule, the full 3S voltage of 12.6 V needs at least 21 V rating before transients. A 25 V capacitor is the nominal minimum standard rating; check TVS clamp and effective capacitance before choosing 25 V or higher. The guide's 70% analog-IC current limit gives preliminary ceilings of 7 A for a 10 A buck, 5.6 A for an 8 A buck, 2.1 A for a 3 A buck and 350 mA for a 500 mA LDO. Thermal limits can be lower. Record junction-temperature ratings separately from ambient ratings.

## Camera interfaces and protection

Exact-MPN symbols use local footprints and hidden Manufacturer, MPN, Datasheet and LCSC Part # fields. Electrical limits are recorded in the symbol descriptions. These are library parts; circuit placement and component counts remain open.

| Part | JLC code | Available to order | USD each, 1+ |
| --- | --- | ---: | ---: |
| [TCA9406DCUR](https://jlcpcb.com/partdetail/TexasInstruments-TCA9406DCUR/C840107) | C840107 | 16,997 | 0.8495 |
| [SN74AXC4T245PWR](https://jlcpcb.com/partdetail/TexasInstruments-SN74AXC4T245PWR/C2867798) | C2867798 | 653 | 0.4165 |
| [TPD4EUSB30DQAR](https://jlcpcb.com/partdetail/TexasInstruments-TPD4EUSB30DQAR/C90627) | C90627 | 8,733 | 0.3726 |
| [USBLC6-2SC6](https://jlcpcb.com/partdetail/STMicroelectronics-USBLC6_2SC6/C7519) | C7519 | 34,067 | 0.1746 |
| [TPD4E05U06DQAR](https://jlcpcb.com/partdetail/TexasInstruments-TPD4E05U06DQAR/C138714) | C138714 | 53,775 | 0.0865 |
| [SMBJ15CA, Littelfuse](https://jlcpcb.com/partdetail/Littelfuse-SMBJ15CA/C78809) | C78809 | 500 | 0.1485 |
| A70-112-331N126, EDAC | C43709820 | 0 | External purchase |

All six semiconductor parts were available in JLC's live assembly inventory when checked; all are Extended parts. Prices exclude assembly/setup fees and inventory is not reserved. The EDAC jack is available from [Mouser](https://www.mouser.co.uk/en/ProductDetail/EDAC/A70-112-331N126?qs=sGAEpiMZZMvlX3nhDDO4ADo1TeYw%2F5hzDU2wUQ1RI9w%3D), part 587-A70-112-331N126. Plan to hand-install it. Its symbol retains the JLC listing code but also carries an explicit external-assembly field. Exclude it from the JLC placement order unless sourcing changes.

TPD4EUSB30 and TPD4E05U06 share the checked TI DQA footprint. Pins 6, 7, 9 and 10 are not internal feedthrough connections; route the actual protected pins as the datasheet shows. SMBJ15CA is bidirectional and has no cathode orientation requirement. The local EDAC footprint follows its R11 drawing, with corrected locating/shield holes and LED positions, plus top-copper shield-contact keepouts. Pins 15/16 are the green LED, 17/18 yellow; this differs from the CM5IO reference symbol's color labels. Shield pads 19/20 use project numbering.

Independently checked: all 72 electrical pin-to-pad mappings, two mechanical holes, manufacturer land patterns, symbol renders and seven-part netlist/BOM/placement exports. The nine existing library symbols and actual design files were preserved.

Sources and checks are in `build/library-interfaces/`: JLCImport data, manufacturer drawings, stock snapshot and KiCad previews. No verified 3D models are included. Footprint origins and pad numbering do not establish JLC's final placement rotation; inspect the assembly preview before ordering.

## Library workflow

1. **Source:** record the exact MPN, package, datasheet revision and manufacturer land pattern. Check the installed KiCad library before creating a part.
2. **Import:** one agent owns the symbol/footprint files. Record every pin mapping, exposed pad, mechanical pad and numbering translation. Keep source downloads and previews in `build/`.
3. **Independent check:** another agent compares the result with the source, including top/bottom view, pin 1, pitch, paste, mask, courtyard, component height and mating clearance. Reusing an import script is not an independent check.
4. **KiCad check:** load and render the symbol/footprint, check courtyard containment and clearances, then export a sample netlist, BOM and placement file. Check MPN, quantity, origin and rotation. Resolve findings before placing the part in the board design.
5. **Board review:** review the real placement, pin-to-net mapping, 3D fit and final assembly exports. Library checks alone do not release a PCB for fabrication.

Keep checks and limitations beside each part in this file. New imports remain provisional until independently checked. Preserve user edits; agents work on assigned files, and schematic placement follows review.

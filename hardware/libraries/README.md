# Project library

Local KiCad symbols, footprints and 3D models. Sourcing codes belong on symbols; keep library paths relative to the project.

## CM5

Based on the [CM5IO revision 2 files](https://pip.raspberrypi.com/categories/1098-design-files), archive `RP-008099-DD-1`. [Pin map](cm5-pin-map.csv) · [Connector drawing](https://cdn.amphenol-cs.com/media/wysiwyg/files/drawing/10164227.pdf)

| Symbol | Assembly BOM |
| --- | --- |
| `CM5_J1` | 1 × Amphenol 10164227-1001A1RLF |
| `CM5_J2` | 1 × Amphenol 10164227-1001A1RLF |
| `CM5_Mount` | Excluded; module outline and plated mounting holes |

J2 pads 1–100 map to module pins 101–200. Its footprint retains the reference trace-length offsets. Buy the CM5 separately.

Place on the carrier front at 0°, relative to the mounting footprint centre:

| Item | X (mm) | Y (mm) |
| --- | ---: | ---: |
| J1 | -17 | +2.5 |
| J2 | +17 | +2.5 |
| Mounting holes | ±16.5 | ±24 |

Position numerically, then group and lock. KiCad does not position the connectors together automatically. Keep components out from under the module at the 1.5 mm stack height.

Mounting holes: 2.7 mm plated bore, 6 mm copper, 0.05 mm mask expansion, 0.25 mm clearance. Pads 1/2 are upper/lower left; 3/4 upper/lower right. Wire their symbol pins to the intended net. Check metal hardware contact and grounding.

The mounting footprint includes the [official CM5 STEP model](https://pip.raspberrypi.com/categories/1096-design-files), archive `RP-007222-DD-2`, without a heatsink.

## Parts

| Function | MPN | LCSC code |
| --- | --- | --- |
| Main buck | LM61495RPHR | C2943584 |
| RF buck | TPS62933FDRLR | C5219272 |
| Camera LDO | TLV75801PDRVR | C2876308 |
| Input controller | LM74502DDFR | C3236215 |
| Battery monitor | INA226AIDGSR | C49851 |
| Camera I²C | TCA9406DCUR | C840107 |
| Camera reset/sync | SN74AXC4T245PWR | C2867798 |
| Ethernet ESD | TPD4EUSB30DQAR | C90627 |
| USB ESD | USBLC6-2SC6 | C7519 |
| Camera/UART ESD | TPD4E05U06DQAR | C138714 |
| Ethernet jack | EDAC A70-112-331N126 | C43709820; external assembly |

Proposed additions: CSD18540Q5B (C86513) and Hongjiacheng SMBJ18CA (C19077574). The older SMBJ15CA and TPS56837 entries remain in the library. Check assembly stock when ordering.

Package details: LM61495 pad 16 is **SW**; TLV758 pad 7 is **GND**. TPD4EUSB30/TPD4E05U06 pins 6, 7, 9 and 10 are not internal feedthroughs. EDAC pins 15/16 are the green LED, 17/18 yellow, and 19/20 shield.

## Adding parts

1. Record Manufacturer, MPN, Datasheet, Footprint and hidden `LCSC Part #` fields.
2. Check every pin and pad against the manufacturer drawing, including exposed pads and pin 1.
3. Independently review geometry, then inspect KiCad renders and sample BOM/placement exports.
4. Check actual board orientation and mating clearance before fabrication.

Use `PigeonVision.kicad_sym` and `PigeonVision.pretty`; leave installed libraries alone. Source files and previews stay in `build/`. Existing mapping checks cover 200 CM5, 49 power and 72 interface pins; final board placement still needs review.

Derived KiCad parts retain the [KiCad library license](https://www.kicad.org/libraries/license/). Manufacturer models retain their original terms.

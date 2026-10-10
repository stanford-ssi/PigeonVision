# Project library

Local KiCad symbols, footprints and 3D models for River’s carrier schematic work. Library corrections and the reviewed camera/CM5 hierarchy changes are active. Sourcing codes belong on symbols; keep library paths relative to the project.

[Library verification and model limitations](review/library-qualification.md) · [Portable dependency manifest](review/portable-library-manifest.json) · [Current model-path verification](review/model-refresh-manifest.json)

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

Position numerically, then group and lock. KiCad does not position the connectors together automatically. The1.5mm mating stack provides **zero component clearance under the module**, per CM5 mechanical specifications; reserve that area. The connector height is not available component clearance.

Mounting holes: 2.7 mm plated bore, 6 mm copper, 0.05 mm mask expansion, 0.25 mm clearance. Pads 1/2 are upper/lower left; 3/4 upper/lower right. Wire their symbol pins to the intended net. Check metal hardware contact and grounding.

The mounting footprint includes the [official CM5 STEP model](https://pip.raspberrypi.com/categories/1096-design-files), archive `RP-007222-DD-2`, without a heatsink.

The proposed active cooler uses the same four CM5 mounting positions: **EDATEC ED-CM5ACOOLER**, M2.5 threads on 48 × 33 mm centres. No additional cooler holes are required. Its screws install from below the carrier; reserve screw-head/tool access, the correct module spacers and thermal-pad compression. Keep the cooler's fan intake and exhaust unobstructed and retain its cable. Verify the mounted sample before releasing the PCB. [Manufacturer drawing/installation](https://edatec.cn/storage/file/ED-CM5ACOOLER%20Datasheet-EN-2024.11.14.pdf).

## E200 fallback mounting

Use **`PigeonVision:MicroPhase_ANTSDR_E200_Mounting_80x50mm_Planning`** from PCB Editor's Add Footprint dialog. The four mounting centres follow [MicroPhase's E200 mechanical DXF](https://github.com/MicroPhase/antsdr_doc_en/blob/ea2cd8d9a0c83157a3ecfbc60ee6bee2f3e73d8f/mechanical/E200_Mechanical.dxf): nominal 80 × 50 mm PCB, 53 × 45 mm hole pitch, with the hole pattern offset 10.201 mm toward the SMA end from the board centre. The drawing-derived STEP includes the board and representative connectors/components. It is a planning model, not manufacturer CAD.

The **2.7 mm carrier NPTH drill, 10 mm standoff gap and all component heights are provisional**; measure the actual board before fabrication. Change the model's Z offset for the chosen standoffs. F.Fab shows the module perimeter; Dwgs.User shows connectors and the overall envelope; F.CrtYd reserves only local fastener space. Check under-module height clearance separately, including the omitted bottom microSD assembly, plugs and cable bends. This mechanical footprint is excluded from the BOM and placement files. [Dimensions, provenance and limitations](review/e200-mounting-manifest.json). Nothing has been placed on the carrier.

## Parts and procurement

The proposed choices and sourcing BOM are preserved in the separate local `PCB_Carrier_Review_2026-10-02` project. Its schematic is a reference for manual transfer; the restored repository schematic is River’s working drawing. The library also retains unused older parts; their presence is not a BOM selection. Exact MPN, manufacturer, Digi-Key code, JLC/LCSC code and package are recorded. In-house pick-and-place is preferred; JLC catalog listing does not guarantee assembly availability at order time.

| Function | Selected MPN / package |
| --- | --- |
| Main converter | LM61495RPHR,16-pin RPH; official TI RPH STEP replaces the incorrect28-pin model |
| RF/camera converters | TPS62933FDRLR,SOT583-8 |
| Common input control | LM74502DDFR,SOT23-8; CSD18540Q5B,Q5B |
| Source/output reverse blocking | LM74700QDBVRQ1 + CSD18540Q5B |
| USB PD | STUSB4500QTR,QFN24 withpad25; DMP4025LSS-13,SO8; C0G-bounded filter capacitance |
| Input clamp / battery TVS | TVS2200DRVR,DRV6EP; battery-only LittelfuseSMBJ18CA,SMB |
| Monitor | INA226AIDGSR,VSSOP10; WSL25125L000FEA18,5mΩ2512 |
| Sensors | BMI088,LGA16; BMP581,LGA10 |
| Camera FFCs | HiroseFH12-22S-0.5SH(55),22pins0.5mm |
| Fan | JSTBM04B-SRSS-TB(LF)(SN),SH4; TPS22919DCKR,SC70-6 |
| Pull-pin connector | AMASS XT30PW-M, horizontal through-hole; external mechanical switch separate |
| Bench DC input | Same Sky/CUI PJ-102AH, right-angle through-hole; Digi-Key CP-102AH-ND; LCSC C3096093 |
| Service | GCTUSB4105-GF-A; TS3USB221DRCR; TPD4E05U06DQAR; BSS138BK,215; TPD1E05U06DYAR; SamtecTSM-102-01-L-SV-P-TR recovery header |
| Service button | C&K PTS636 SK25 SMTR LFS, two-terminal gull-wing SMT; Digi-Key CKN12322-1-ND; LCSC/JLC C2689642 |
| TX/Yapogee | JSTGH12/GH4; SN74AXC4T245PWR,TSSOP16; SN74AXC1T45DCKR,SC70-6; SN74LVC1G07DBVR,SOT23-5 |
| TX/PA power | Molex0430450218,Micro-Fit3.0; branch fuses and reverse-blocking stages |
| Ethernet | EDACA70-112-331N126; TPD4EUSB30DQAR ×2 |
| Indicators / bulk | Hubei KENTO KT-0805G green, 0805 (LCSC C2297; used for LED1–LED4); PanasonicEEE-FK1V221P,caseF. The audited LiteOn LTST-C170KGKT footprint remains in the library as an unused alternative. |

Footprints and symbols were compared against manufacturer pin tables/lands, including exposed pads, connector polarity and unmated views. See block-specific manifests in `hardware/libraries/review/` for sources and geometry. Passives use standard KiCad SMD footprints; installed KiCad10 stock symbols remain a dependency. The optional legacy PCM imported symbols are cached in existing sheets; the proposed reference schematic’s assigned footprints and model references resolve inside the project. Existing placed symbols retain their cached properties until explicitly updated from their library.

Models have distinct evidence levels: manufacturer originals (CM5, TI main buck, Panasonic, Hirose, JST SH, EDAC RJ45 and Same Sky jack), representative licensed KiCad package models, and clearly named derived maximum envelopes. The XT60 uses a detailed public 2019 model matching the older drawing; the current supplier revision specifies larger PCB holes than the preserved footprint. Confirm the purchased XT60 revision before fabrication. BMP581, Micro-Fit and several power packages retain model limitations in their reviews. Do not infer seating/mating tolerances or machine pickup geometry from an envelope model.

The populated PCB received 50 model-only repairs, including cached IC models and portable passive model paths. Pad geometry, placement, nets and routing were preserved. The PCB still contains older schematic content; this model refresh was not a full schematic-to-PCB update.

Two underside PA heatsink mounting candidates are available in `PigeonVision.pretty`: `ATS_CPX050050010_121_C1_R0_Mechanical_Planning` (50 × 50 × 10 mm) and `Wakefield_623A_M4_NPTH_Mechanical_Planning` (120.65 × 76.2 × 11.7 mm). Their STEP files are plain **clearance envelopes**, omitting fins, holes and hardware. A nominal 0.5 mm TIM offset gives body projections of 10.5 and 12.2 mm below the board; the target is 12.7 mm including hardware. Mounting stack, contact area, thermal vias and final heat rejection remain to be designed and tested. The wide part nearly fills the 144.78 mm carrier circle. [Drawings, sourcing and mounting limits](review/pa-heatsink-mounting-manifest.json). Neither candidate has been placed on the carrier.

The carrier's selected pull-pin header is `Connector_AMASS:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal`, copied into the enabled local AMASS library. Its two contacts are actually 5 mm apart despite the stock filename. In the selected footprint frame, pad 1 is at (0,0) mm and pad 2 at (−5,0) mm; polarity marks identify pad 1 as negative and pad 2 as positive. Here they carry gate control and GND, not battery load current. The detailed public model comes from [mjbots/moteus](https://github.com/mjbots/moteus/blob/main/hw/c1/3d/XT30PW-M.step); its source license is retained beside the model. Rotation (−90,0,0)° and offset (−2.5,+10,0) mm reproduce the reference board's model-to-pad mapping in the stock footprint frame. KiCad renders were checked; purchased-part fit and revision remain physical checks. The two camera and fan hold-down pads are unnumbered mechanical lands; CM5's four plated mounting lands are connected to GND.

The bench jack is `PigeonVision:PJ-102AH`, assigned to `PigeonVision:SameSky_PJ-102AH_Horizontal`. Its manufacturer rating is 5 A / 24 V. Pin 1 is the center contact, pin 2 is the sleeve, and pin 3 is the sleeve shunt that opens when a plug is inserted. For center-positive bench power, connect pin 1 upstream of the shared input protection/pull-pin circuit and pin 2 to GND. The schematic also ties pin 3 to GND; it only touches the sleeve (pin 2) with no plug inserted, so the connection is harmless. Use battery or adapter one at a time. This jack remains a 5 A connection when used with a 10 A adapter. It is a hand/selective solder operation; an LCSC listing does not establish JLC assembly availability. [Geometry, model provenance and sourcing](review/barrel-jack-library-manifest.json).

The user selected LumenPnP and approved0402 parts. Assembly requires0.4mm-pitch vision/placement,0.4mm-pitch CM5 connector placement,QFN/USON exposed-pad stencil control, and pickup capacity for the large inductors/connectors. Confirm reel width/pitch,lead/trailer requirements,nozzle height and MSL/floor life against the actual machine/parts before ordering. The RJ45 and battery XT60 are through-hole operations; pin-in-paste or manual/wave assembly needs a separate process decision. Placement files and production stencil are deferred with PCB placement.

The service button is `PigeonVision:PTS636_SK25_SMTR_LFS`, with a matching two-pad footprint and detailed licensed KiCad model. Its nominal 2.5 mm height has ±0.2 mm tolerance. `PigeonVision:BSS138BK,215` and `PigeonVision:TPD1E05U06DYAR` provide the data-port detector and VBUS protection. Their models are representative SOT-23 and a clearly marked DYA envelope. [Part geometry, sourcing and model limits](review/service-controls-library-manifest.json). The circuit is wired in [USB & Service](../carrier/PigeonCarrier/Service_USB.kicad_sch), using the existing JLC 0402 passive symbols and CL05B104KB54PNC 100 nF decoupler. TPD4E05U06 pairs 1–10, 2–9, 4–7 and 5–6 require external PCB copper for straight-through routing. Parts have not been placed on the PCB.

## Adding or substituting parts

1. Keep exact manufacturer identity and supplier codes, including packaging suffixes.
2. Verify every symbol pin and pad against the latest manufacturer drawing.
3. Check land/stencil geometry and model orientation; record model provenance.
4. Recompute biased capacitance,thermal/current limits or logic timing when substituting. Equal nominal values do not establish equivalence.
5. Verify final board orientation,mating clearance and machine setup before fabrication.

Use `PigeonVision.kicad_sym` and `PigeonVision.pretty`; leave installed libraries alone. Generated evidence,source copies and previews remain in `build/`. Derived KiCad parts retain the [KiCad library license](https://www.kicad.org/libraries/license/); manufacturer CAD retains its source terms.

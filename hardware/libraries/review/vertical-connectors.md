# Vertical carrier connector options

Added to the enabled **PigeonVision** library on 2026-10-14. The vertical XT60 J1, XT30 J6 and both camera connectors J7/J8 are now installed in the carrier schematic and PCB. The two JST GH parts remain selectable alternatives. USB-C and the PJ-102AH bench jack remain horizontal. Existing Micro-Fit power, JST SH fan/debug, and recovery headers are already vertical.

| Use | Exact selected order code | Symbol after `PigeonVision:` | Sourcing checked 2026-10-14 |
| --- | --- | --- | --- |
| Battery J1 | AMASS XT60PB-M30.G.Y | `AMASS_XT60PB_M30_G_Y` | [LCSC C19268037](https://www.lcsc.com/product-detail/C19268037.html) lists the abbreviated XT60PB-M.G.Y; 25,643 shown. JLC assembly inventory unverified. |
| Pull-pin J6 | AMASS XT30UPB-M.G.Y | `AMASS_XT30UPB_M_G_Y` | [JLC C428721](https://jlcpcb.com/partdetail/Changzhou_AmassElec-AMASS_XT30UPB_M/C428721), Extended, wave soldering. [TME XT30UPB-M](https://www.tme.eu/en/details/xt30upb-m/dc-power-connectors/amass/) showed 3,352; incoming revision needs checking. |
| Camera J7/J8 | Hirose FH12-22S-0.5SVA(54) | `CM5_Camera_FH12_22S_0_5SVA` | [JLC C6250833](https://jlcpcb.com/partdetail/HRS_Hirose-FH12_22S_0_5SVA_54/C6250833), Extended, SMT; 45 shown. |
| Four-wire GH option | JST BM04B-GHS-TBT(LF)(SN) | `JST_GH_BM04B_GHS_TBT` | [JLC C161692](https://jlcpcb.com/partdetail/JST-BM04B_GHS_TBT_LF_SN/C161692), Extended, SMT. LCSC showed 290; JLC quantity unverified. |
| Twelve-wire GH option | JST BM12B-GHS-TBT(LF)(SN)(N) | `JST_GH_BM12B_GHS_TBT` | [JLC C2983891](https://jlcpcb.com/partdetail/BM12B-GHS-TBT%28LF%29%28SN%29%28N%29/C2983891), Extended, SMT. LCSC showed 12,818; JLC quantity unverified. |

Supplier inventory is a dated observation, not a reservation. The GH4 suffix deliberately matches its C161692 listing; the alternative `(N)` code C2983876 was unavailable. JLC **C9900017179** is an XT60 assembly-service listing, not verified stock of the AMASS connector.

## Space and access

Measurements below are the bounding rectangles of the saved F.CrtYd polygons, in each footprint's own axes. They exclude vertical plug insertion, wire bends, cable strain relief and finger/tool access. The percentage is `100 × (new width × new depth / old width / old depth − 1)`.

| Connector | Existing horizontal courtyard, mm | New vertical courtyard, mm | Rectangle area change |
| --- | ---: | ---: | ---: |
| XT60 | 16.5 × 19.4 | 16.8 × 9.5 | −50.1% |
| XT30 | 14.3 × 15.85 | 11.5 × 6.9 | −65.0% |
| Camera | 17.1 × 7.9 | 15.4 × 7.3 | −16.8% |
| GH4 | 9.46 × 6.4 | 9.46 × 6.6 | +3.1% |
| GH12 | 19.46 × 6.4 | 19.46 × 6.6 | +3.1% |

The GH options primarily move the cable exit upward; their PCB courtyards are slightly larger. JST specifies approximately 7.3 mm mated height before cable bending. For the camera connector, Hirose's drawing gives **3.6 ± 0.2 mm closed** and **5.5 ± 0.2 mm fully open**. Reserve access above it for ribbon insertion and actuator operation; ease of use in the enclosure remains untested.

## Pin and footprint checks

The camera option is the **same exact connector that FRAMOS specifies for FPA-A/P22-V2 J2**. All 22 numbered symbol pins, their signal names, types and schematic positions are retained from the existing camera symbol. The FRAMOS pinout matches the carrier: seven ground contacts, four differential CSI data lanes, differential clock, reset on 17, reserved MCLK on 18, SCL/SDA on 20/21, and 3.3 V on 22. [FRAMOS connector and pinout](https://docs.framos.com/en/latest/FSMEcosystem/ProductDocumentation/FPA/FPA-A-P22V2.html).

The vertical camera footprint derives from [Hirose ED C3-159266-03](https://www.hirose.com/en/product/p/CL0586-0813-6-54), sheets 2/5. It uses staggered tails: 0.5 mm contact pitch, 1 mm pitch within each PCB row. Pin 1 is (+5.25, −1.5) mm; pin 2 is (+4.75, +1.5) mm. Copper is 0.6 × 1.5 mm, with separate 0.5 × 1.5 mm paste openings. Four mechanical lands are unnumbered. All 22 actual manufacturer STEP contact soles were checked inside their corresponding pads.

FRAMOS specifies same-side-contact ribbon, for example Molex 0151660241. Hirose sheet 6 puts the exposed conductors opposite the actuator operation side. Confirm pin-1 continuity and contact face on the actual cable when installing this option; the CAD check does not prove cable wiring.

AMASS footprints follow current revision-labelled drawings: [XT60 2025V0](https://www.china-amass.net/uploads/62.XT60PB-M-SPEC-2025V0.pdf) gives **7.20 ± 0.05 mm pitch / 4.60 +0.10/−0.00 mm finished holes**; [XT30 2025V1 archive](https://www.china-amass.net/uploads/XT30UPB-FM.rar) gives **5.00 ± 0.05 mm pitch / 1.80 +0.20/−0.00 mm finished holes**. Copper diameters of 5.6/3.0 mm are project choices, giving 0.5/0.6 mm nominal radial annuli. The latest XT30 body is 5.6 mm deep, wider than the installed stock footprint's older 5.2 mm body.

Both XT symbols retain the carrier's numbered contact convention: **1 is the keyed/negative-marked side; 2 is the flat/positive-marked side**. Battery replacement must retain J1.1 = GND and J1.2 = VBAT_RAW. Pull-pin replacement must retain J6.1 = PWR_EN and J6.2 = GND; its moulded polarity marks do not describe the voltage polarity of that signal-switch circuit. The EasyEDA XT60 footprint uses a different numbering convention and was not copied. The earlier claim that the manufacturer PCB layout was an underside view was unsupported: its viewing face is not labeled. For this symmetric two-contact pattern, a 180-degree rotation reconciles the drawing and footprint; the official moulded key/polarity marks establish the mapping.

GH land patterns and pin-1 orientation were checked against [JST's GH drawing](https://www.jst-mfg.com/product/pdf/eng/eGH.pdf). Both use 0.6 × 1.7 mm signal lands at 1.25 mm pitch and two unnumbered 1.0 × 2.8 mm hold-down lands. Existing GH symbol pin numbering and drawing positions are retained.

## Real STEP files and limits

Every added option has real detailed STEP geometry in `hardware/libraries/3dmodels/`. The current XT60 is a documented local derivative of native CAD; the camera has a white presentation copy with identical geometry. Original source files are preserved. No substitute envelope or mesh-to-STEP conversion was generated.

| Option | STEP provenance | Status |
| --- | --- | --- |
| Camera | Exact Hirose product download; archive internally calls it `FH12-22S-0.5SV.stp` | Detailed manufacturer geometry, actuator open. Active white appearance copy changes only the single `COLOUR_RGB` entity to RGB (0.94, 0.94, 0.94); all geometry/topology entity bytes and the original file are unchanged. Display color is not a physical-part color specification. |
| GH4/GH12 | KiCad detailed community CAD, Frank Severinsen, 2018 | Nominal models under CC-BY-SA 4.0 with KiCad design-file exception; source headers retained. Not manufacturer-issued CAD. |
| XT30 | JLCEDA/EasyEDA Official Library, native model UUID `c57778ddcf0a475097683aa679abcd99` | Detailed older model has correct 5 mm pitch, but 5.2 mm body depth and 2 mm tails, versus current 5.6/3 mm. Enabled for visual planning; current drawing governs clearance. |
| XT60 | Detailed native [CalSTAR CAD](https://github.com/calstar/hardware-sch-blocks/blob/c9ae07583d4098d1f878f01dcc28a294dfc86612/XT60-M/XT60-M.step), BSD-3-Clause; current file is an explicitly local PCB planning derivative | **Enabled:** measured 7.2 mm contact pitch; 15.5 × 8.1 × 16 mm housing seated at Z=0, with 3 mm tails. Native housing and mating features are preserved; only the excess tail length was cropped, followed by rigid alignment. The source's cable-cup termination details remain an unverified approximation of factory PCB tails. |

XT source attribution: [JLCEDA](https://lceda.cn/) / [EasyEDA](https://easyeda.com/). Manufacturer authorship of these XT models is unverified. The original incorrect 7.0 mm-pitch XT60 model remains preserved and unassigned. No exact manufacturer-issued XT60PB-M30.G.Y CAD was obtained. The enabled replacement uses correctly spaced native detailed geometry without stretching its pitch. Its local derivation and remaining termination assumptions are recorded explicitly.

Hirose restricts redistribution of its STEP; broader redistribution permission for the downloaded XT assets was not established. The original three models plus the white camera appearance copy are installed locally and explicitly ignored by Git. The CalSTAR-derived XT60 planning model carries its source repository BSD-3-Clause license beside it. Do not upload or commit them without the required rights. GH license and source records are retained alongside the models. All footprint links use `${KIPRJMOD}/../../libraries/3dmodels/`.

## Verification and remaining work

During the initial library addition, KiCad 10 loaded all five symbols and footprints and exported their SVGs. A build-only coupon had zero reported DRC violations and zero unconnected items; its STEP models were rendered for inspection. The follow-up separately rechecked XT60 hole geometry and moulded polarity, and directly measured the new STEP's contact axes, seating plane, keyed end and below-board tail projection. Existing library symbols were preserved byte-for-byte, and hashes confirmed the carrier files unchanged at that stage. The subsequent carrier installation is recorded below. These checks do not validate hardware fit, current capability, vibration retention, cable wiring, camera signal integrity or enclosure access.

The EDAC Ethernet connector remains horizontal. Bel V891-1XX1-A1 is a possible vertical Gigabit candidate, but its actual CAD, pin mapping and practical sourcing were not qualified; no replacement symbol was added.

[Installed geometry, source hashes and model transforms](vertical-connectors-manifest.json) · [Build checks and source drawings](../../../build/vertical-connectors-2026-10-14/) · [Current 3D coupon preview](../../../build/vertical-connectors-2026-10-14/followup/connector-coupon.png)

## Follow-up recheck and appearance update

The XT60 footprint pads are unchanged: centers (0,0)/(7.2,0) mm, 4.6 mm nominal drills, 5.6 mm copper. Nominal radial annulus is (5.6−4.6)/2 = 0.50 mm; at the drawing's maximum finished 4.7 mm hole it is 0.45 mm. Copper edge gap is 7.2−5.6 = 1.60 mm. Manufacturer hole tolerance is **finished 4.60 +0.10/−0.00 mm**; KiCad stores the nominal drill only, so communicate the finished-hole tolerance during fabrication. Courtyard margins are 0.65/0.70 mm per side at nominal body size, or 0.50/0.55 mm at maximum size if centered. Physical fit remains untested.

The enabled detailed XT60 planning model has three valid solids and 134 faces. Its terminal axes are (0,0)/(7.2,0) mm; housing Z=0…16 mm and tails Z=−3…0 mm. The source's 4.35 mm OD terminal geometry would leave (4.6−4.35)/2 = 0.125 mm nominal radial clearance in the saved holes. This is a model calculation, not evidence that the purchased PCB variant uses those terminals. Source body geometry and all contact geometry above the PCB are unchanged by the tail crop. The source's cable-cup openings/internal details remain; treat this as detailed planning CAD, not manufacturer fit qualification.

[Independent footprint/polarity review](../../../build/vertical-connectors-2026-10-14/followup/xt-footprint/README.md) · [Detailed model derivation](../../../build/vertical-connectors-2026-10-14/followup/xt60-planning-model-proof.json) · [Independent STEP measurements](../../../build/vertical-connectors-2026-10-14/followup/xt-source/parent-model-independent-proof.json) · [Camera color-only proof](../../../build/vertical-connectors-2026-10-14/followup/camera-color-proof.json)

## Carrier installation and polarity check

Installed J1/J6/J7/J8 in the active carrier schematic and PCB on 2026-10-14. Exact MPN, manufacturer, footprint, datasheet and supplier fields now match the selected vertical parts; horizontal-part supplier codes were replaced or cleared. J1/J6 retain the user's functional values `BATT CONN` / `PULL_PIN`, with exact ordering codes in their MPN fields. The new J1 schematic symbol is mirrored to retain both original wire endpoints. All reference and pin UUIDs are retained.

The manufacturer's moulded marks were checked independently for both AMASS series. J1.1 is the keyed/− end on GND; J1.2 is the flat/+ end on VBAT_RAW. J6.1 remains PWR_EN and J6.2 GND: it connects a dry contact, so its −/+ marks do not describe the circuit's voltage polarity. Closing J6 grounds LM74502 U1.1 EN/UVLO and inhibits downstream power; opening it permits the UVLO divider to enable power, subject to controller conditions. Actual battery harness polarity remains unmeasured. Camera contacts retain pin 1 = GND and pin 22 = 3V3_CAM, with the existing CSI and control pin assignments.

During the 2026-10-14 installation, J6 was rotated 180° to preserve its two existing hole positions, (37.3,85.075)/(32.3,85.075) mm. Three new courtyard overlaps required connector-only adjustments: J1 moved 1.55 mm left to clear Q1, J7 moved 0.8 mm upward to clear C7, and J8 moved 0.8 mm downward to clear C8. The installation anchors were J1 (71.75,59.453724) at 90°, J6 (37.3,85.075) at 180°, J7 (90,36.2) at 180°, and J8 (90,163.8) at 0°. All unrelated PCB objects, placements, groups, board outline and schematic objects were preserved by that swap. River subsequently updated placements on 2026-10-15; the active PCB file is authoritative for current positions. USB-C J10 and bench jack J2 remain horizontal.

KiCad 10 exported the installed netlist: all 216 nets are unchanged, all 152 component footprints/values agree, and all 681 numbered board pads match schematic nets. The four installed land patterns match their library masters, and every enabled STEP path resolves. ERC reports zero errors/warnings; schematic-to-PCB parity reports zero differences. DRC reports no new violations: the two replaced XT footprint-library mismatches disappeared, leaving the existing 38 solder-mask bridge errors and five warnings. The carrier remains unrouted with 457 unconnected items; this connector swap does not establish a fabrication-ready layout.

[Installed swap verification](../../../build/vertical-connectors-2026-10-14/swap/verification.json) · [Independent polarity and circuit trace](../../../build/vertical-connectors-2026-10-14/swap/polarity-audit/audit.json) · [Independent 44-pin camera audit](../../../build/vertical-connectors-2026-10-14/swap/camera-audit/camera-audit.json) · [Carrier 3D preview](../../../build/vertical-connectors-2026-10-14/swap/carrier-vertical-connectors.png)

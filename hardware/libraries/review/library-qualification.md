# Active carrier parts library

The prepared symbols, footprints and 3D model files are active in `hardware/libraries/`. The carrier's existing symbol and footprint tables already point to these folders through `${KIPRJMOD}`. Restoring these files did not change the repository schematic, project or PCB.

The proposed reference schematic uses 52 distinct footprints. Symbol pin numbers were compared with those footprint pads and the manufacturer pin tables. The active file audit checks every local footprint model link and records hashes in [active-library-verification.json](active-library-verification.json). The library also contains older unused parts; inclusion alone does not mean a part has been selected or newly qualified.

3D models have different purposes:

- Manufacturer CAD: CM5, the TI RPH main-converter package, Panasonic case F capacitor, Hirose camera connectors, JST SH fan connector, Same Sky barrel jack and EDAC A70-112-331N126 Ethernet connector.
- Representative package geometry: licensed KiCad passives and several standard packages/connectors. These are not exact MPN mechanical models.
- Detailed public CAD: the XT60PW-M connector now uses an older 2019 model checked against the AMASS V1.2 drawing. It includes the keyed mating face and contacts; manufacturer authorship is unverified.
- Derived drawing or clearance envelopes: Micro-Fit connector and several power packages. These preserve the recorded geometry and limitations, rather than complete mating detail.

The XT60 model fits the existing footprint's four contact positions. Current LCSC C98732 documentation instead names the M30 variant and recommends 2.90 mm power holes and 0.80 × 1.90 mm retention slots; the preserved footprint uses the older 2.70 mm and 0.60 × 1.70 mm recommendations. Resolve that incoming-part revision before fabrication. The detailed model represents the older form and does not establish current-stock fit. Evidence is in `build/xt60-model-2026-10-02/XT60_MODEL_REVIEW.md`.

The EDAC STEP is the manufacturer's nominal surface assembly, with no solid volumes or colour entities. Its terminals and posts align with the manufacturer drawing and footprint; the largest nominal terminal-centre difference is 0.04 mm. Source and rigid-transform checks are in `build/rj45-model-2026-10-02/`.

Nominal CAD is not a guaranteed maximum envelope. The JST GH side-entry models, for example, stop near 4.245 mm above the board, while the drawing gives 4.35 mm nominal including standoff. Generic passive models also omit some maximum height/width tolerances. Use the [CAD review](cad-final-review.md) and [passive model audit](passive-model-height-audit.md) when setting physical clearances.

The [CSD18540Q5B land/stencil review](csd18540q5b-footprint.md), [XT60 model record](amass-xt60pw-model.md), and block-specific JSON manifests record the drawing sources, rotations and limitations. Package-model orientation and pad mapping were inspected on a coupon. Final board mating, assembly process, stencil behavior and physical sample fit remain unverified.

Existing placed symbols retain their cached data until updated from their libraries. These library files support selecting parts and manually transferring circuit blocks from the separate local review project; they do not automatically revise River's restored drawing.

The model refresh repaired the saved PCB's empty IC model lists and old KiCad 8 package paths. Only model blocks changed in the PCB; its pads, nets and placements were preserved. Selected PCM packages and the stock XT60 identity now have project-local footprint copies with portable model links. These model repairs do not synchronize the older populated PCB with the current schematic. Verification is in `build/model-refresh-2026-10-02/`.

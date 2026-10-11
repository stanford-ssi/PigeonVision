# CSD18540Q5B copper and stencil audit

Reviewed 2026-10-02 against [TI SLPS488B April2017, pp8–9](https://www.ti.com/lit/ds/symlink/csd18540q5b.pdf). Shared footprint is`PigeonVision:TI_CSD18540Q5B_Q5B`, used by the power input and transmitter core/PA stages. Only this footprint was edited; no schematic/symbol/model/PCB files were changed.

The **copper was dimensionally correct**. The **Fab outline and paste aperture locations needed correction**. The original5×5mm Fab outline omitted1mm of package length; TI nominal body is6×5mm and maximum is6.10×5.10×1.05mm. Fab is now6×5mm, courtyard7.42×5.60mm covers copper/max body plus clearance. Pin1 remains lower-right, corresponding to180° rotation of the manufacturer's top view; the top-view copper diagram onp9 has pin4 upper-right and pin1 lower-right. Source pins1/2/3, gate4 and the internally common drain5–8 remain correctly mapped. The custom unified drain pad is numbered5 to match the project's single-drain symbol.

| Copper feature | Saved geometry; matches TI p9 |
|---|---|
| Unified drain | x−3.456…+0.984mm, y±2.260mm; nominal4.440×4.520mm envelope. Three0.590×0.560mm left-edge notches leave four0.710mm-high tabs. |
| Source/gate pads | Four1.372×0.710mm lands atx2.770mm, y±0.635/±1.905mm, pitch1.270mm. Drain-right-to-signal-left gap1.100mm. |
| Pad shape | Copper dimensions/numbering preserved; source/gate pads now rectangular to reproduce the example drawing. |

The original paste span was6.762mm against TI's6.586mm, left tab-to-interior gap0.256mm against0.286mm, and central row gap0.240mm against0.300mm. Paste apertures are now rectangular and satisfy the drawing's dimensions. TI does not dimension the stencil's absolute registration to the package origin; **the implementation explicitly centres its6.586mm total span on the package**, consistent with the drawing's centre lines. This is a documented registration choice, not an exact manufacturer CAD claim.

| Final aperture group | Count / size(mm) | Centre coordinates(mm) |
|---|---|---|
| Drain interior | Eight1.294×0.746 | x−1.594/+0.050; y±0.523/±1.569 |
| Drain tabs | Four0.766×0.508 | x−2.910; y±0.635/±1.905 |
| Source/gate | Four1.072×0.562 | x2.757; y±0.635/±1.905 |

Interior-column gap0.350mm; each row gap0.300mm; tab-to-interior gap0.286mm. Outer vertical tab span4.318mm; source/gate span4.372mm (half2.186mm). TI's printed horizontal dimension chain sums6.587mm while its printed overall width is6.586mm. The saved right-interior-to-signal gap is therefore1.524mm against the printed1.525mm, a **1µm rounding difference**. My first proposed registration incorrectly assumed the stencil's left edge coincided with the copper edge; that assumption was withdrawn after checking the PDF vector centre lines.

Drain paste area is9.279104mm² over19.0776mm² drain copper, about48.64%, arising from TI's aperture sizes. This is not an independently chosen generic paste percentage. Stencil thickness, paste process, reflow profile, via treatment and actual wetting/voiding remain assembly qualification.

The existing local`TI_CSD18540Q5B_max_envelope.step` is a drawing-derived maximum rectangular envelope, not official manufacturer CAD. Bounds6.10×5.10×1.05mm matchp8's maxima. It omits leads, body moulding and a physical pin1 mark; footprint pin1 marking controls orientation. The project-relative path and zero model transform remain unchanged. KiCad coupon render was inspected upright.

Proof: `build/carrier-completion/power-cad/csd-independent-copper-paste-proof.json` contains actual saved coordinates, source and hashes. Prior footprint snapshot is preserved in the same build directory. Actual parsed-copper/paste overlay is`build/carrier-completion/csd-independent-copper-paste.png`; KiCad loaded the footprint and exported`csd-paste-svg`; model preview is`csd-independent-model.png`. The carrier board was not placed/routed by this audit.

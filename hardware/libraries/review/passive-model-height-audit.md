# Passive STEP height audit

The installed KiCad1210 capacitor STEP is **2.5mm high**, not1.6mm. Samsung CL32B106KBJZW6E has nominal3.2×2.5×2.5mm and maxima3.5×2.7×2.7mm. The previous CL32B106KBJNNNE has the same dimensions. The exact selected soft-termination part is listed on the manufacturer's base-part page. [Samsung selected part](https://product.samsungsem.com/mlcc/CL32B106KBJZW6.do), [previous part](https://product.samsungsem.com/mlcc/CL32B106KBJNNN.do).

Created `PigeonVision:Samsung_CL32B106KBJZW6E_C1210_MaxEnvelope` with local drawing-derived STEP bounding3.5×2.7×2.7mm. It is a solid clearance envelope, not native manufacturer CAD. Stock1210 copper is preserved exactly:1.15×2.7mm pads atx±1.475mm. Stock courtyard4.6×3.2mm contains maximum body3.5×2.7mm with at least0.25mm width margin. Fab shows nominal body. Copper is KiCad IPC-7351 nominal land, not a new manufacturer-prescribed solder pattern. No schematic instance or PCB was changed by this audit.

The envelope starts atboardz=0 and does not include solder standoff, PCB warp or assembly tolerance. Add those to mechanical clearance separately. See `bulk-capacitor-model-manifest.json` for exact source, hashes, pad preservation and re-read STEP bounds. Measurement script/log: `../../../build/carrier-completion/external-interfaces/audit_passive_models.py` and `../../../build/carrier-completion/passive-models/stock-models.json`.

Other generic passive STEP files represent nominal size, not maximum clearance:

| Actual carrier part/family | Stock top above model origin(mm) | Manufacturer maximum component thickness(mm) | Gap(mm) / evidence |
|---|---:|---:|---|
| CL31B475KBHNNNE1206 |1.60 |1.80 |0.20; [Samsung](https://product.samsungsem.com/mlcc/CL31B475KBHNNN.do) |
| GRM31C5C1H104JA01L1206C0G |1.60 |1.80 |0.20; [Murata](https://www.murata.com/en-us/products/productdetail?partno=GRM31C5C1H104JA01%23); exact reference sheet local`power-cad/sources/GRM31C5C1H104JA01.pdf` |
| CL10A105KB8NNNC0603 |0.80 |0.90 |0.10; [Samsung](https://product.samsungsem.com/mlcc/CL10A105KB8NNN.do) |
| CL21B224KBFNNNE0805 |1.25 |1.35 |0.10; [Samsung](https://product.samsungsem.com/mlcc/CL21B224KBFNNN.do) |
| CL05A105KA5NQNC0402 |0.50 |0.60 |0.10; [Samsung](https://product.samsungsem.com/mlcc/CL05A105KA5NQN.do) |
| CL05B104KO5NNNC0402 |0.50 |0.55 |0.05; [Samsung](https://product.samsungsem.com/mlcc/CL05B104KO5NNN.do) |
| CL05B104KB54PNC0402 |0.50 |0.55 |0.05; [Samsung](https://product.samsungsem.com/mlcc/CL05B104KB54PN.do) |
| YAGEORC0402 |0.35 |0.40 |0.05; [exact1k manufacturer sheet](https://www.yageogroup.com/component-documentation/download/specsheet/RC0402FR-071KL); individual series/value limits still apply |
| YAGEORT0402 |0.35 |0.35 |0; height0.30±0.05 in [manufacturer V17](https://www.yageogroup.com/content/datasheet/asset/file/PYU-RT_1-TO-0-01_ROHS_L) |
| YAGEORC1206FR-077K5L |0.55 |0.65 |0.10; maximum width1.7mm versus model1.6mm; [exact manufacturer sheet](https://www.yageogroup.com/component-documentation/download/specsheet/RC1206FR-077K5L) |
| Panasonic ERJ-P08F1206 |0.55 |0.70 |0.15; height0.60±0.10 in [manufacturer catalog](https://industrial.panasonic.com/cdbs/www-data/pdf/RDM0000/DMM0000COL9.pdf); local`power-cad/sources/Panasonic_ERJP.pdf` |

No checked generic passive has a hidden1mm height error. The0.05–0.20mm differences still matter under module/shield/enclosure clearances. Retaining these generic nominal models does not certify maximum assembly height. No unverified individual component dimensions were inferred from package name alone.

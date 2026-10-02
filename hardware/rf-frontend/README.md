# RF frontend

**Cur Plan:** AFE7071 transmitter or E200 → attenuation/driver → PA → filter → antenna. Target 0.5 W average DVB-S2 output at the PA, initially at 1.28 GHz. RF remains untested; frequency and power still need confirmation.

![RF frontend block diagram](block-diagram.svg)

[Edit diagram](block-diagram.drawio) · [Link budget](../../calculations/link-budget.xlsx)

| Stage | Part / starting value |
| --- | --- |
| Input pad | 50 Ω; fit for the selected source. Do not carry the E200's old 20 dB assumption into the AFE7071 path |
| Driver | GRF2011; optional assembly bypass |
| Interstage pad | Value after gain/peak-level calculation |
| PA | GRF5115 was the earlier choice; GRF5613 is proposed in the transmitter draft. Selection open |
| Output filter | LFCN-1500+ candidate; verify attenuation at required frequencies |

SMA input/output. Carrier supplies switched 5 V; local filtering stays on this board. Coordinate default-off PA control with the transmitter and carrier RF inhibit. Include current test points and heatsink mounting.

Size attenuation from measured gain and waveform peaks. A 1 dB filter loss leaves about 0.4 W. Check modulation quality, spectrum and temperature; filtering cannot fix clipping.

[GRF2011](https://www.guerrilla-rf.com/products/detail/sku/GRF2011) · [GRF5115 reference circuit](https://www.guerrilla-rf.com/includes/prodFiles/5115/GRF5115%201200-1400%20MHz.pdf) · [LFCN-1500+](https://www.minicircuits.com/pdfs/LFCN-1500%2B.pdf)

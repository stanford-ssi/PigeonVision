# RF frontend

**Cur Plan:** 5 V RF board between the E200 and antenna. Target 0.5 W clean average DVB-S2 output at the PA, initially at 1.28 GHz. A 1 dB filter loss leaves about 0.40 W before antenna-cable loss. Frequency TBD.

![RF frontend block diagram](block-diagram.svg)

[Edit in draw.io](block-diagram.drawio)

| Stage | Part / value |
| --- | --- |
| Input attenuation | Fixed 50-ohm pad, initially 20 dB. Final value from measured gain and waveform peaks. |
| Driver | GRF2011. Reference gain 15.2 dB at 900 MHz, 5 V / 90 mA. Verify at 1.28 GHz. |
| Interstage pad | 3 dB initial. |
| Final PA | GRF5115, 1200-1400 MHz reference tune. At 1300 MHz: 18.6 dB gain and 33.4 dBm P1dB, typical at 5 V. |
| Output filter | LFCN-1500+ low-pass after the final matching and DC block. Add band-pass filtering only if measured spurs require it. |

Gain estimate: `15.2 − 3 + 18.6 = 30.8 dB` before the input pad and other losses. A 20 dB input pad with +10 dBm drive gives about 0.12 W before filtering. Adjust after measurement to reach 0.5 W.

Set usable output from modulation quality, spectrum and receiver performance. P1dB alone does not establish clean DVB-S2 power; filtering cannot repair clipping.

SMA input/output; carrier power, enable and temperature connection. Include default-off enable, separate bias feeds and current test points. Provide heatsink mounting. Driver bypass is an assembly option.

Power filtering: the carrier supplies regulated RF 5 V with its required buck output capacitors. Put the final supply filter, local bulk capacitance and each amplifier's reference bias/bypass network on this RF board. Select series impedance for DC current and voltage drop; check filter damping and PA load transients. Supply filtering is separate from the RF output filter.

Start the schematic and PCB from the manufacturers' band-specific reference circuits, with replaceable input/interstage resistor pads and accessible RF test connections. Check matching, bias, stackup and thermal construction before fabrication. Bench testing sets the final pad values and usable DVB-S2 output power; it is not a prerequisite for starting the design.

A pad is a matched resistor attenuator. The input pad reduces drive; the interstage pad reduces drive and reflections between amplifiers. The shown 20 dB and 3 dB values are starting options. Use the E200's transmit attenuation for routine power adjustment; keep a fixed pad sized for the maximum permitted drive. A separate digital step attenuator is optional if SDR control is insufficient. Its setting, like the SDR setting, does not measure output watts. Verify output power, modulation quality, spectrum and temperature during bring-up.

References: [GRF2011](https://www.guerrilla-rf.com/products/detail/sku/GRF2011), [GRF5115 tune](https://www.guerrilla-rf.com/includes/prodFiles/5115/GRF5115%201200-1400%20MHz.pdf), [GRF5115 datasheet](https://www.guerrilla-rf.com/includes/prodFiles/5115/GRF5115DS.pdf), [LFCN-1500+](https://www.minicircuits.com/pdfs/LFCN-1500%2B.pdf).

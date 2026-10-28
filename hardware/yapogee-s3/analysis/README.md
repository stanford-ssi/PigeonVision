# Mixer linearity estimate

The [firmware crest-factor calculation and updated parts comparison](crest-factor.md) replace the earlier assumed 4-5 dB waveform PAPR with about 3.8 dB for the current 40 MS/s modes, using 4 dB as a preliminary envelope allowance. This is calculated, not measured RF.

The RF2052 is still a credible converter candidate. Its lower headline IIP3 does not establish a material DVB-S2 penalty at the actual mixer drive level. The decision needs ESP32 power and spectrum, required PA drive, and converter distortion at the proposed frequency pair.

## What the datasheets provide

[RF2052 DS140110](https://www.qorvo.com/products/r/ra000361), pp. 4-5, lists typical low/high-linearity IIP3 of +10/+18 dBm and input P1dB of +2/+12 dBm. [RFFC2072A Rev F](https://www.qorvo.com/products/r/ra000701), p. 2, lists +10/+23 dBm typical IIP3 for low/high-linearity settings. These are input intercepts, not allowable average or peak waveform powers.

The actual curves contain more detail than those headline numbers. RF2052 p. 9 plots IIP3 versus bias for 500/900 MHz inputs converted to 100 MHz, and 2000 MHz converted to 500 MHz. RFFC2072A p. 15 plots bias-dependent IIP3 and input compression for a 1000 MHz LO and 100 MHz output. Its plotted compression point is around +12 dBm at several higher bias settings, rather than automatically 5 dB better because its headline IIP3 is 5 dB higher. None of these plots directly characterizes our 2420 MHz input, 1140 MHz LO and 1280 MHz output. Plotting a typical curve at another conversion is not a guarantee at this one.

RF2052 p. 18 explicitly identifies setting 1 as lowest, 4 as default and 5 (`101`) as highest. At setting 5, p. 9's IIP3 curves reach approximately +25 to +26 dBm in their stated test conversions, while plotted input P1dB remains around +12 to +13 dBm. The +18 dBm table value used below must not be interpreted as a measured ceiling at the highest bias setting. Raising bias is worth evaluating, but does not give the same improvement in output compression headroom. The p. 22 register table gives a conflicting broader current-code description; use the characterized 1-5 guidance rather than assuming `111` is an appropriate maximum setting.

The public manufacturer product pages list datasheets, matching/programming notes and CAD resources; no downloadable nonlinear circuit or behavioral model was identified in those listed resources. A CAD symbol/footprint/3D model is not an electrical simulation model. Small-signal S-parameters and port-impedance models can help design matching, but do not describe compression, AM/PM or intermodulation. A sufficiently characterized nonlinear mixer model could support a stronger simulation; it would still need the right bias, LO, reference planes and frequency range.

## Reproduce the estimate

Install NumPy, then run from the repository root:

```sh
python3 hardware/yapogee-s3/analysis/mixer_linearity.py
```

The script writes its parameters, version, metrics and limits into `output/mixer-linearity.json` beside itself. Paths are independent of the current working directory. Output is generated, not a hardware measurement.

It generates 32,768 independent random QPSK or 8PSK symbols with an ideal RRC pulse: roll-off 0.35, 8 samples/symbol and 12-symbol span. It removes edge transients and normalizes mean sample power to one. This is representative pulse-shaped PSK, not the ESP32's exact DVB framing, finite pulse tables, DAC quantization or analog output.

For complex envelope `x` normalized to unit average power, the fitted model is:

```text
k = 10^((P_average_dBm - IIP3_dBm)/10)
y = x * (1 - k*|x|^2)
```

In dimensional terms, the cubic coefficient is `1/P_IIP3`, with powers in watts. For two equal low-level tones it gives `IM3_dBc ~= 2*(P_tone_dBm - IIP3_dBm)`. This is the standard two-tone intercept convention, with each tone's power distinct from total average power. [Mini-Circuits AN00-010](https://www.minicircuits.com/appdoc/AN00-010.html).

The script independently checks that convention using FFT bins of a two-tone signal: -30 dBm per tone and +18 dBm IIP3 predict -96 dBc; the implemented cubic gives -95.9996 dBc.

After fitting common complex gain `a = sum(conj(x)*y)/sum(|x|^2)`, waveform MER is `10*log10(sum(|a*x|^2)/sum(|y-a*x|^2))`. This describes added waveform distortion only. It is not a receiver-symbol MER, decoding result or sensitivity measurement.

## Example results

At an **assumed 0 dBm average mixer input**, the model gives:

| Typical IIP3 used | QPSK waveform MER | 8PSK waveform MER |
| --- | ---: | ---: |
| +10 dBm | 27.33 dB | 27.35 dB |
| +18 dBm | 44.28 dB | 44.29 dB |
| +23 dBm | 54.39 dB | 54.41 dB |

At -10 dBm average input, the +18 dBm case gives about 64.43 dB waveform MER. These high numbers describe a model with no noise, PLL phase noise, source impairments or spurious floor. They are not attainable hardware MER predictions. The weak-distortion approximation becomes less reliable as waveform peaks approach compression; `peak_cubic_term_fraction` exposes the largest cubic correction in each case.

QPSK and 8PSK show almost the same amplitude-distortion sensitivity in this model. Both acquire envelope variation after filtering. QPSK's decoding threshold can be lower, but the four-point constellation does not make its pulse-shaped RF signal immune to mixer nonlinearity. The script holds the pulse shape and normalized drive constant so it does not silently assign either mode a PA advantage.

## Why this does not identify the final device

IIP3 alone cannot determine the nonlinear transfer function. For this particular compressive cubic, the predicted CW compression point is:

`P1dB = IIP3 + 10*log10(1 - 10^(-1/20)) = IIP3 - 9.636 dB`.

Using +18 dBm IIP3 therefore predicts +8.36 dBm P1dB, whereas the RF2052 table gives +12 dBm in its high-linearity case. Both numbers cannot be reproduced by this one-coefficient model. Higher-order terms, phase behavior, frequency/bias conditions or a different compression law matter. The result is a sensitivity estimate, not a substitute for the missing device model.

For a practical comparison, first measure ESP32 output versus drive and determine the input needed by the PA after converter/filter losses. Then compare measured added MER/EVM, spectral regrowth, LO/source leakage and phase noise at that operating point. Lowering mixer drive reduces cubic distortion, but may demand extra gain downstream. Account for that circuit's noise, distortion, power and cost before selecting the converter.

## PA drive and output power

The proposed engineering target is 1-2 W average modulated output. The [GRF5613 EVB184 1240-1420 MHz tune](https://www.guerrilla-rf.com/includes/prodFiles/5613/GRF5613%20EVB184%201240-1420%20MHz.pdf), dated November 19, 2025, gives the nearest tabulated point at 1270 MHz: 23.7 dB gain, 33.6 dBm CW OP1dB and 34.13 dBm saturated output at 5 V. Its gain is measured at 22 dBm tone output. The generic datasheet's 24.5 dB / 35.4 dBm headline uses a different 1400 MHz reference condition.

Ignoring gain compression, `P_PA_input_dBm = 30 + 10*log10(P_PA_output_W) - 23.7`. This gives:

| Average PA output target | PA output | PA input from gain extrapolation | Mixer input with 2 dB conversion loss and 2 dB loss after mixer |
| --- | ---: | ---: | ---: |
| 1 W | 30.00 dBm | +6.30 dBm | +10.30 dBm |
| 2 W | 33.01 dBm | +9.31 dBm | +13.31 dBm |

The mixer and passive losses are planning assumptions, not measurements. Add input balun/pad loss to obtain the ESP32 source requirement. The output target in this table is at the PA output, before any final filter loss; an antenna-port target needs that loss added too.

The proposed +7 to +8 dBm average PA drive corresponds to 1.17-1.48 W by linear extrapolation. With the same loss assumptions, the mixer input would be +11 to +12 dBm average, around the RF2052's published typical CW compression point before allowing for waveform peaks. Therefore the existing loss-only mixer cannot both accept very low ESP32 power and deliver that PA drive. A linear driver after the mixer/filter would let the mixer operate at a lower level, at the expense of another gain stage.

The input reference plane matters for RFFC2072A too. A +7 dBm mixer input, -2 dB nominal conversion gain and 2 dB assumed following passive loss yield +3 dBm at the PA, then 26.7 dBm (0.47 W) at its output by gain extrapolation. This is not the same operating point as +7 dBm directly at the PA input. The -2 dB figure excludes balun losses; the exact assembled loss remains to be measured.

RFFC2072A contains mixer 2; its mixer 1 input/output pins are NC (Rev F p. 19). Use the mixer 2 curves on p. 15. Both sets of curves have their own stated 1000 MHz LO / 100 MHz IF test conditions. At high temperature the curves also show that the highest bias code does not maximize compression headroom. A typical 8.5 dBm CW input P1dB with 7 dBm average drive leaves only 1.5 dB before that CW point; 4 dB waveform peaks reach 11 dBm. That does not prove reception fails, but it cannot establish low distortion from the average level alone. Select bias using measured MER/spectrum and loss at the actual conversion.

There is a separate PA limit. For an assumed waveform PAPR of 4-5 dB, 2 W average asks for peaks of 37.0-38.0 dBm, above this tune's measured CW compression and saturation levels. As a reference calculation, placing peaks at its 33.6 dBm CW OP1dB gives average powers of 0.91 W at 4 dB PAPR and 0.72 W at 5 dB PAPR. This is not a universal clean-output ceiling: crest-factor statistics, AM/PM and the permitted distortion/regrowth decide the actual average operating point. Measure the real waveform's PAPR and MER/spectrum versus drive. A reliable 2 W average goal may require a PA with more peak headroom, regardless of the mixer bias setting.

## Range with the owned antenna pair

The flight antenna is [TrueRC Singularity 1280 V2 BLACK RHCP](https://truerc.com/37938-singularity-1280-v2-black), nominally 1.9 dBic over 1250-1290 MHz. The ground antenna is [TrueRC X2-AIR 1.3 RHCP](https://truerc.com/35572-xc2b2-air-1-3), nominally 13 dBic over 1200-1360 MHz. Use these co-polar gains directly in the link equation. The workbook's older 3 dB linear-to-circular penalty does not apply to an ideal RHCP pair; a separate 0.5 dB residual polarization allowance is assumed here, not measured.

Run `python3 hardware/yapogee-s3/analysis/link_budget.py`. It writes full tables and inputs to `output/link-budget.md` and `output/link-budget.json`. Defaults use the owned antennas and retain the workbook's 1 dB PA output filter, 0.3 dB flight cable, 1 dB receive cable/filter and 2 dB ground-pointing loss. With 0.5 dB polarization allowance, total fixed loss is 4.8 dB. Receiver NF is assumed 8 dB at its input connector, excluding the separately counted receive feeder/filter. The calculation adds a new 2 dB implementation allowance and retains the 10 dB reserve. Neither receiver NF nor implementation loss is measured.

From [ETSI Table 13](https://www.etsi.org/deliver/etsi_en/302300_302399/30230701/01.04.01_60/en_30230701v010401p.pdf), normal-frame ideal Es/N0 is 1.00 dB for QPSK 1/2 and 6.62 dB for 8PSK 2/3. With 290 K noise temperature and the above allowances, the estimated receiver-input thresholds are -92.98 dBm at 10 Msym/s QPSK 1/2 and -90.37 dBm at 5 Msym/s 8PSK 2/3. QPSK requires a ground receiver supporting 10 Msym/s; the current S3 receiver implements only 1 and 5 Msym/s.

| Assumed clean average PA output before final filter | QPSK 1/2 free-space slant range with 10 dB reserve | 8PSK 2/3 free-space slant range with 10 dB reserve |
| --- | ---: | ---: |
| 0.10 W | 8.40 km | 6.22 km |
| 0.25 W | 13.28 km | 9.83 km |
| 0.50 W | 18.78 km | 13.90 km |
| 1.00 W | 26.56 km | 19.66 km |
| 2.00 W | 37.56 km | 27.81 km |

At 10,000 ft AGL and 100 m station offset (3.050 km slant), 0.5 W QPSK has 15.79 dB remaining after the separate 10 dB reserve. At 30,000 ft and the same offset (9.145 km), the remaining margin is 6.25 dB. These are conditional planning results; the table does not establish that the mixer/PA can produce each clean output power.

The equations are `FSPL = 20*log10(4*pi*d*f/c)` and `P_RX = P_PA + G_flight + G_ground - losses - FSPL`, with power in dBm and gains/losses in dB. At 290 K, `P_threshold = 10*log10(k*T*1000) + NF + 10*log10(Rs) + Es/N0 + implementation_allowance`. This symbol-rate form accounts for the bandwidth trade without adding roll-off noise twice.

Nominal antenna gain is not the gain in every direction. The X2-AIR's -3 dB beamwidth is 74 degrees horizontal by 34 degrees vertical; its broader advertised coverage is not constant 13 dBic gain. Aim it at the flight volume. Mounted flight pattern, axial ratio, airframe blockage and actual receiver sensitivity remain to be measured. Each additional 10 dB loss divides the calculated free-space range by 3.162. For example, use `--extra-loss-db 10` for that sensitivity case.

The owned flight antenna also constrains bandwidth. A 13.5 MHz waveform at 1280 MHz lies nominally in 1273.25-1286.75 MHz and fits its stated band. A 40 MHz waveform at 1280 MHz extends to 1300 MHz, outside that band. Moving a 40 MHz waveform to 1270 MHz only touches both stated band edges, leaving no additional guard.

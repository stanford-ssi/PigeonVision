# Mixer linearity estimate

The RF2052 is still a credible converter candidate. Its lower headline IIP3 does not establish a material DVB-S2 penalty at the actual mixer drive level. The decision needs ESP32 power and spectrum, required PA drive, and converter distortion at the proposed frequency pair.

## What the datasheets provide

[RF2052 DS140110](https://www.qorvo.com/products/r/ra000361), pp. 4-5, lists typical low/high-linearity IIP3 of +10/+18 dBm and input P1dB of +2/+12 dBm. [RFFC2072A Rev F](https://www.qorvo.com/products/r/ra000701), p. 2, lists +10/+23 dBm typical IIP3 for low/high-linearity settings. These are input intercepts, not allowable average or peak waveform powers.

The actual curves contain more detail than those headline numbers. RF2052 p. 9 plots IIP3 versus bias for 500/900 MHz inputs converted to 100 MHz, and 2000 MHz converted to 500 MHz. RFFC2072A p. 15 plots bias-dependent IIP3 and input compression for a 1000 MHz LO and 100 MHz output. Its plotted compression point is around +12 dBm at several higher bias settings, rather than automatically 5 dB better because its headline IIP3 is 5 dB higher. None of these plots directly characterizes our 2420 MHz input, 1140 MHz LO and 1280 MHz output. Plotting a typical curve at another conversion is not a guarantee at this one.

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

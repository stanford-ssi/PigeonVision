# Crest factor and converter options

The current 40 MS/s ESP32 waveform calculates to about **3.8 dB peak-to-average envelope power (PAPR)** for both proposed QPSK and 5 Msym/s 8PSK. Use **4 dB as a preliminary waveform allowance**, replacing the earlier assumed 4-5 dB range. This is a firmware calculation with an assumed linear reconstruction, not measured RF or a guaranteed worst-case bound after the radio, mixer and filters.

## Definition and calculation

For complex envelope `x = I + jQ`, `PAPR_dB = 10*log10(max(|x|^2)/mean(|x|^2))`. Envelope amplitude crest factor is `sqrt(PAPR)`: about **1.55**, with peak envelope power **2.40 times average** at 3.8 dB. Real RF voltage peak/RMS includes the sinusoidal carrier's extra factor of `sqrt(2)`, giving 2.19 (6.8 dB). **Use 3.8 dB for PA envelope power headroom; do not add the carrier's 3 dB again.**

Source is esp-dvbs2 commit `f6149796044796f83fe10a27dc381554df21a2d8`: `firmware/tx/main/dvbtx.c`, `firmware/components/shaper/shaper.c`, `include/shaper_taps.h`, and the Python DVB-S2 encoder. These modes use the ordinary nine-symbol inverse-hold RRC pulse, roll-off 0.35, DAC 40 MS/s, default level 128. QPSK 10 Msym/s has four DAC samples/symbol; 8PSK 5 Msym/s has eight. Separate higher-rate pulse implementations are not covered.

The script builds the actual portable C shaper, including tap scaling and separately rounded partial sums. Every stream's first 4096 symbols matches an independent fixed-point convolution exactly. Encoded frames include headers, pilots and scrambling, continuously across frame boundaries; populated initial history excludes zero-padded startup.

Linear reconstruction uses the zero-order DAC hold's `sinc(f/F_DAC)` amplitude response followed by a flat brick-wall low-pass at `|f| <= 0.8*Rs`, eight-times interpolation and 256 DAC samples of guard per FFT block. Eight-versus-sixteen-times interpolation on a common subset differs by less than 0.001 dB. Actual RF filtering, compression, AM/PM, IQ imbalance, carrier leakage, gain settings and keying transients remain unmeasured.

| Mode / input | Symbols | Digital DAC PAPR | Linear reconstruction PAPR |
| --- | ---: | ---: | ---: |
| QPSK 1/2, 10 Msym/s, random | 1,000,000 | 3.66 dB | 3.82 dB |
| QPSK 1/2, 10 Msym/s, 16 normal frames | 532,512 | 3.66 dB | 3.83 dB |
| 8PSK 2/3, 5 Msym/s, random | 1,000,000 | 3.85 dB | 3.77 dB |
| 8PSK 2/3, 5 Msym/s, 16 normal frames | 355,104 | 3.78 dB | 3.74 dB |
| 8PSK 3/4, 5 Msym/s, random | 1,000,000 | 3.85 dB | 3.80 dB |
| 8PSK 3/4, 5 Msym/s, 16 short frames | 89,568 | 3.82 dB | 3.78 dB |

Random-stream reconstructed thresholds above mean power:

| Fraction of sampled time above threshold | QPSK 10 Msym/s | 8PSK 5 Msym/s, 2/3 run |
| --- | ---: | ---: |
| 0.1% | 3.55 dB | 3.29 dB |
| 0.01% | 3.70 dB | 3.52 dB |
| 0.001% | 3.77 dB | 3.66 dB |

Samples are correlated: these fractions are not independent peak-event probabilities. A finite observed maximum is not an absolute bound. Independently, the unrounded finite pulse's largest amplitude is `max_p sum_k |h[k,p]|`, attainable with antipodal symbols; independent-symbol mean power is `mean_p sum_k h[k,p]^2`. Their ratio gives 3.63 dB at four samples/symbol and 3.77 dB at eight. Quantization and reconstruction change these figures.

Reproduce with `python3 hardware/yapogee-s3/analysis/crest_factor.py --esp-repo /path/to/esp-dvbs2`. Requires NumPy, SciPy and a C compiler. Source hashes, inputs, native build and full statistics are written under ignored `output/crest-factor/`. No radio is contacted or keyed.

## Power consequences

+7 dBm average mixer input with 3.8 dB PAPR means **+10.8 dBm peaks**. Against an 8.5 dBm CW P1dB example, that exceeds the point by 2.3 dB. Putting peaks at that point requires average `8.5 - 3.8 = 4.7 dBm`, before any additional distortion margin. Neither crossing P1dB nor placing peaks at it establishes actual decoding, MER or spectrum performance.

The [GRF5613 EVB184](https://www.guerrilla-rf.com/includes/prodFiles/5613/GRF5613%20EVB184%201240-1420%20MHz.pdf) measures 33.6 dBm CW OP1dB at 1270 MHz. A 1 W average waveform asks for 33.8 dBm peaks; 2 W asks for 36.8 dBm. A stronger converter/driver cannot fix the final PA's headroom. At 0.5 W, estimated peaks are 30.8 dBm, 2.8 dB below that CW point. Measure spectrum and MER versus drive.

## Parts search, 2026-10-27

For 2420 MHz input, 1140 MHz LO and 1280 MHz output, this search did not establish a clearly superior inexpensive single-chip PLL/VCO/mixer. RF2052 remains a cheaper candidate; RFFC2072A's highest bias code is not necessarily its best hot compression setting. Neither is characterized at our exact conversion by the cited compression curves.

| Option | Evidence / limitation | Single-unit IC price |
| --- | --- | ---: |
| RF2052TR13 | PLL/VCO/mixer, nominal -2 dB conversion gain. Datasheet high-linearity input P1dB is +12 dBm typical in its stated test. Its headline +18 dBm IIP3 is not a maximum-bias ceiling. | $12.88 cut tape |
| RFFC2072ATR13 | PLL/VCO/mixer, nominal -2 dB gain. Evaluate mixer 2's compression versus bias/temperature; +23 dBm IIP3 is not a compression rating. | $23.72 cut tape |
| RF2052 + SPF5043Z driver | Reduces mixer drive needed for a given PA input. Adds a gain stage, bias feed and support parts. Does not increase the mixer's intrinsic P1dB. | $17.74 for both ICs |

Prices from [DigiKey RF2052](https://www.digikey.com/en/products/detail/qorvo/RF2052TR13/4501995), [RFFC2072A](https://www.digikey.com/en/products/detail/qorvo/RFFC2072ATR13/24719422) and [SPF5043Z](https://www.digikey.com/en/products/detail/qorvo/SPF5043Z/2708620); listings showed 16, 2243 and 11924 respectively. Support parts/shipping excluded. This is a stock snapshot; RF2052 availability is tighter. RF2052 compression source: [DS140110 pp. 4-5](https://www.qorvo.com/products/r/ra000361).

The [SPF5043Z datasheet](https://mm.digikey.com/Volume0/opasdata/d220001/medias/docus/414/SPF5043Z.pdf), June 12, 2018 pp. 2-4, gives typical 5 V/46 mA bias, 18.2 dB gain at 900 MHz and 14.8 dB at 1500 MHz, with typical output P1dB 22.6/22.7 dBm at those points. **16 dB gain at 1280 MHz is an interpolation estimate**, not a specification. At +8 dBm average output and 3.8 dB PAPR, estimated 11.8 dBm peaks are below those neighboring compression figures. Matching, stability, temperature and distortion remain to be verified. Pin 1 is RF output/drain feed, pin 3 RF input, pins 2/4 ground; use DC blocking and an output bias choke per the reference circuit. Nominal bias power is 0.23 W.

Example: with 16 dB assumed driver gain and 4 dB combined mixer/passive loss, +8 dBm PA input needs `8 - 16 + 4 = -4 dBm` average mixer input. Gain/attenuation sets the final drive; usable clean PA output is a separate question.

Other screened parts: [RFFC5072A](https://www.qorvo.com/products/p/RFFC5072A) offers wider frequency coverage, with no established compression advantage here; [ADRF6655](https://www.analog.com/en/products/adrf6655.html) is obsolete; [RF2053](https://www.qorvo.com/products/p/RF2053) is discontinued and requires an external VCO. [LTC5567](https://www.analog.com/en/products/ltc5567.html) and [LTC5548](https://www.analog.com/en/products/ltc5548.html) require external LO sources. This is not an exhaustive market survey; the schematic/BOM remains open.

#!/usr/bin/env python3
"""Weak-cubic mixer sensitivity estimate, not a manufacturer device simulation.

Independent random QPSK/8PSK symbols use an ideal finite RRC pulse. This does not
reproduce the ESP32's DAC quantization, pulse tables, carrier leak or analog RF.
The cubic is fitted only to typical input IP3. It excludes AM/PM, memory, PLL
noise, noise figure, mismatch and higher-order products. See README.md.
"""

import json
import math
from pathlib import Path

import numpy as np


def rrc(alpha=0.35, sps=8, span=12):
    t = np.arange(-span * sps // 2, span * sps // 2 + 1) / sps
    h = np.empty_like(t)
    for i, u in enumerate(t):
        if abs(u) < 1e-12:
            h[i] = 1 + alpha * (4 / np.pi - 1)
        elif abs(abs(4 * alpha * u) - 1) < 1e-12:
            h[i] = alpha / np.sqrt(2) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * alpha))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * alpha))
            )
        else:
            h[i] = (
                np.sin(np.pi * u * (1 - alpha))
                + 4 * alpha * u * np.cos(np.pi * u * (1 + alpha))
            ) / (np.pi * u * (1 - (4 * alpha * u) ** 2))
    return h / np.linalg.norm(h)


def waveform(order, seed=2026, symbols=32768, sps=8, span=12):
    rng = np.random.default_rng(seed)
    phases = 2 * np.pi * rng.integers(order, size=symbols) / order
    if order == 4:
        phases += np.pi / 4
    impulses = np.zeros(symbols * sps, dtype=complex)
    impulses[::sps] = np.exp(1j * phases)
    x = np.convolve(impulses, rrc(sps=sps, span=span), mode="same")
    x = x[span * sps : -span * sps]
    return x / np.sqrt(np.mean(abs(x) ** 2))


def cubic(x, input_dbm, iip3_dbm):
    # x has unit mean power. In watts, the complex-envelope coefficient is
    # 1 / P_IIP3, giving the standard weak two-tone IM3 intercept convention.
    coefficient = 10 ** ((input_dbm - iip3_dbm) / 10)
    return x * (1 - coefficient * abs(x) ** 2)


def mer(x, y):
    # Fit common complex gain so a uniform gain change is not counted as error.
    gain = np.vdot(x, y) / np.vdot(x, x)
    error = y - gain * x
    return float(10 * np.log10(np.sum(abs(gain * x) ** 2) / np.sum(abs(error) ** 2)))


def two_tone_check():
    # Independent spectral check of the IP3 convention, well below compression.
    n = np.arange(4096)
    x = (np.exp(2j * np.pi * 11 * n / len(n))
         + np.exp(2j * np.pi * 17 * n / len(n))) / np.sqrt(2)
    per_tone_dbm, ip3 = -30.0, 18.0
    y = cubic(x, per_tone_dbm + 10 * np.log10(2), ip3)
    spectrum = np.fft.fft(y)
    observed = 20 * np.log10(abs(spectrum[5] / spectrum[11]))
    expected = 2 * (per_tone_dbm - ip3)
    if abs(observed - expected) > 0.01:
        raise ValueError(f"Two-tone convention mismatch: {observed} vs {expected}")
    return float(observed)


def main():
    check = two_tone_check()
    cases = {
        "RF2052_low_bias": {"iip3_dbm": 10, "published_p1db_dbm": 2},
        "RF2052_high_bias": {"iip3_dbm": 18, "published_p1db_dbm": 12},
        "RFFC2072A_high_bias": {"iip3_dbm": 23, "published_p1db_dbm": None},
    }
    result = {
        "model": "ideal RRC waveform and memoryless real compressive cubic fitted to typical IIP3",
        "limits": "Not measured hardware MER or receiver-symbol MER; no AM/PM, memory, PLL noise, noise floor, mismatch, higher orders or ESP32 impairments. Weak-distortion estimate only.",
        "numpy_version": np.__version__,
        "seed": 2026, "symbols": 32768, "sps": 8, "rolloff": 0.35, "span_symbols": 12,
        "two_tone_im3_dbc": check,
        "sources": {
            "RF2052": "https://www.qorvo.com/products/r/ra000361 (DS140110 pp. 4-5)",
            "RFFC2072A": "https://www.qorvo.com/products/r/ra000701 (Rev F p. 2)",
        },
        "cases": cases, "waveforms": {}, "sweep": [],
    }
    for name, case in cases.items():
        case["cubic_predicted_cw_p1db_dbm"] = (
            case["iip3_dbm"] + 10 * math.log10(1 - 10 ** (-1 / 20))
        )
    for name, order in [("QPSK", 4), ("8PSK", 8)]:
        x = waveform(order)
        power = abs(x) ** 2
        peak_db = float(10 * np.log10(max(power)))
        result["waveforms"][name] = {"sampled_peak_to_average_db": peak_db}
        for case_name, case in cases.items():
            ip3 = case["iip3_dbm"]
            for input_dbm in [-20, -10, -5, 0]:
                result["sweep"].append({
                    "waveform": name, "case": case_name,
                    "input_average_dbm": input_dbm,
                    "input_sampled_peak_dbm": input_dbm + peak_db,
                    "waveform_mer_db": mer(x, cubic(x, input_dbm, ip3)),
                    "peak_cubic_term_fraction": 10 ** ((input_dbm + peak_db - ip3) / 10),
                })
    out = Path(__file__).resolve().parent / "output"
    out.mkdir(exist_ok=True)
    path = out / "mixer-linearity.json"
    path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(path)
    print(f"Two-tone check: {check:.4f} dBc; weak-IP3 expectation -96 dBc")
    print("Ideal waveform MER from cubic only, at 0 dBm average input:")
    for row in result["sweep"]:
        if row["input_average_dbm"] == 0:
            print(f"{row['waveform']:5} {row['case']:24} {row['waveform_mer_db']:6.2f} dB")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Crest factor of the current 40 MS/s ESP32 pulse shaper; no board access.

Requires numpy, scipy and a C compiler. --esp-repo points to esp-dvbs2.
Builds the actual portable C shaper, checks it against independent convolution,
then analyzes random symbols and encoded DVB-S2 frames, including headers/pilots.
The optional linear reconstruction is a stated model, not the ESP32 RF response.
"""

import argparse
import ctypes as ct
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import numpy as np
from scipy.signal import resample


def array(text, name):
    body = re.search(r"\b" + name + r"\[\d+\] = \{([^}]+)", text).group(1)
    return np.array([int(v) for v in body.split(",") if v.strip()], np.int64)


def statistics(powers):
    # Histogram thresholds have 0.002 dB resolution. Normalize only after all
    # chunks: reconstruction changes average power as well as the peak.
    edges = np.arange(-100, 20.002, 0.002)
    hist = np.zeros(len(edges) - 1, np.int64)
    count, total, peak = 0, 0.0, 0.0
    for p in powers:
        count += p.size
        total += float(p.sum())
        peak = max(peak, float(p.max()))
        logarithm = 10 * np.log10(np.maximum(p, 1e-12))
        hist += np.histogram(np.clip(logarithm, edges[0], edges[-1]), edges)[0]
    mean = total / count
    assert hist.sum() == count
    cumulative = np.cumsum(hist)
    quantiles = {}
    for fraction in (1e-3, 1e-4, 1e-5):
        index = min(np.searchsorted(cumulative, (1 - fraction) * count), len(hist) - 1)
        quantiles[str(fraction)] = float(edges[index + 1] - 10 * np.log10(mean))
    return {"samples": count, "mean_power_relative_to_digital": mean,
            "observed_papr_db": float(10 * np.log10(peak / mean)),
            "observed_envelope_peak_over_rms": float(np.sqrt(peak / mean)),
            "power_db_exceeded_for_time_fraction": quantiles}


def reconstructed_power(x, sps, up, normalization):
    # Model: zero-order DAC hold, then a flat brick-wall filter at +/-0.8 Rs.
    # In the baseband image a hold contributes sinc(f/Fdac), apart from delay.
    # 256 native samples on each side isolate circular-FFT boundary effects.
    block, guard = 32768, 256
    for start in range(guard, len(x) - guard, block):
        stop = min(start + block, len(x) - guard)
        part = x[start - guard:stop + guard]
        f = np.fft.fftfreq(len(part))  # cycles / native DAC sample
        response = np.sinc(f) * (np.abs(f) <= 0.8 / sps)
        filtered = np.fft.ifft(np.fft.fft(part) * response)
        y = resample(filtered, len(filtered) * up)[guard * up:-guard * up]
        yield np.abs(y) ** 2 / normalization


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--esp-repo", required=True, type=Path)
    parser.add_argument("--random-symbols", type=int, default=1_000_000)
    parser.add_argument("--frames", type=int, default=16)
    parser.add_argument("--level", type=int, default=128)
    args = parser.parse_args()
    if args.random_symbols < 4096 or args.frames < 1 or not 16 <= args.level <= 508:
        parser.error("need >=4096 random symbols, >=1 frame, and level 16..508")
    repo = args.esp_repo.resolve()
    out = Path(__file__).resolve().parent / "output" / "crest-factor"
    out.mkdir(parents=True, exist_ok=True)
    component = repo / "firmware/components/shaper"
    source = component / "include/shaper_taps.h"
    text = source.read_text()
    (out / "esp_attr.h").write_text("#pragma once\n#define IRAM_ATTR\n")
    bridge = out / "bridge.c"
    bridge.write_text('''#include "shaper.h"
#include "shaper_taps.h"
void make_samples(uint32_t *out, const uint8_t *in, int count,
                  unsigned sps, unsigned bits, unsigned level) {
    int32_t taps[72];
    const int32_t *source = sps == 8 ? shaper_taps_8psk_n8_a35 : shaper_taps_8psk_n4_a35;
    for (unsigned j=0;j<9*sps;++j) taps[j]=(int32_t)((double)source[j]*level/718.4);
    shaper_table_t table;
    shaper_build(&table,shaper_store,taps,sps,bits);
    shaper_fill_c(out,in+8,count,&table);
}
''')
    library = out / "shaper.so"
    subprocess.run(["cc", "-std=c11", "-O2", "-fPIC", "-shared", "-I", str(out),
                    "-I", str(component / "include"), str(component / "shaper.c"),
                    str(bridge), "-o", str(library)], check=True)
    lib = ct.CDLL(str(library))
    lib.make_samples.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_int,
                                ct.c_uint, ct.c_uint, ct.c_uint]
    lib.make_samples.restype = None
    sys.path.insert(0, str(repo / "host"))
    import dvbs2

    levels = [array(text, "shaper_level_i"), array(text, "shaper_level_q")]
    rng = np.random.default_rng(20261027)
    cases = [("QPSK 1/2, 10 Msym/s", "qpsk", "1/2", 4, False),
             ("8PSK 2/3, 5 Msym/s", "8psk", "2/3", 8, False),
             ("8PSK 3/4 short, 5 Msym/s", "8psk", "3/4", 8, True)]
    results = []
    for name, modulation, rate, sps, short in cases:
        bits = dvbs2.BITS_PER_SYMBOL[modulation]
        taps = np.trunc(array(text, f"shaper_taps_8psk_n{sps}_a35") *
                        args.level / 718.4).astype(np.int64).reshape(9, sps)
        h = taps.astype(float) / 256
        nominal_mean = float(np.mean(np.sum(h * h, axis=0)))
        unrounded_peak = float(np.max(np.sum(abs(h), axis=0)))
        analytic_bound = float(10 * np.log10(unrounded_peak ** 2 / nominal_mean))

        streams = {}
        phases = rng.integers(0, 1 << bits, args.random_symbols + 8, dtype=np.uint8)
        if bits == 2:
            phases = 2 * phases + 1
        streams["iid_random_symbols"] = phases
        frames = []
        for _ in range(args.frames):
            payload = rng.integers(0, 2, dvbs2.code(rate, short).kbch - 80, dtype=np.uint8)
            fec = dvbs2.fecframe(dvbs2.bbframe(payload, rate, short=short), rate, short)
            frames.append(dvbs2.plframe_phases(fec, rate, short=short, modulation=modulation))
        phase_frames = np.concatenate(frames)
        streams["encoded_frames_with_pilots"] = np.r_[phase_frames[-8:], phase_frames]
        row = {"mode": name, "sps": sps, "rolloff": 0.35, "span_symbols": 9,
               "unrounded_finite_pulse_peak_over_iid_mean_db": analytic_bound,
               "streams": {}}
        for label, phases in streams.items():
            encoded = (phases << 4).astype(np.uint8)
            words = np.empty((len(phases) - 8) * sps, np.uint32)
            lib.make_samples(words.ctypes.data, encoded.ctypes.data, len(phases) - 8,
                             sps, bits, args.level)
            i = ((words.astype(np.int64) & 1023) ^ 512) - 512
            q = (((words.astype(np.int64) >> 10) & 1023) ^ 512) - 512
            x = i + 1j * q
            # Independent fixed-point convolution, including three rounding steps.
            count = min(4096, len(phases) - 8)
            for axis, samples in zip(levels, (i, q)):
                check = np.zeros((count, sps), np.int64)
                for part in range(3):
                    acc = np.zeros_like(check)
                    for age in range(3 * part, 3 * part + 3):
                        acc += axis[phases[8-age:8-age+count], None] * taps[age]
                    check += (acc + (1 << 37)) >> 38
                np.testing.assert_array_equal(samples[:count * sps], check.ravel())
            mean = float(np.mean(abs(x) ** 2))
            stats = {"symbols": len(phases) - 8,
                     "digital": statistics([abs(x) ** 2 / mean]),
                     "linear_reconstruction_8x": statistics(reconstructed_power(x, sps, 8, mean))}
            # Check peak convergence on the same first 32768 DAC samples.
            subset = x[:32768 + 512]
            p8 = statistics(reconstructed_power(subset, sps, 8, mean))["observed_papr_db"]
            p16 = statistics(reconstructed_power(subset, sps, 16, mean))["observed_papr_db"]
            stats["8x_to_16x_papr_difference_db_on_subset"] = p16 - p8
            if abs(p16 - p8) > 0.02:
                raise ValueError("reconstruction peak has not converged to 0.02 dB")
            row["streams"][label] = stats
        results.append(row)
        print(name, {k: {v: round(s[v]["observed_papr_db"], 3)
                        for v in ("digital", "linear_reconstruction_8x")}
                     for k, s in row["streams"].items()}, flush=True)

    files = [source, component / "shaper.c", repo / "firmware/tx/main/dvbtx.c",
             repo / "host/dvbs2.py", repo / "host/dvbs2_tables.py"]
    result = {"status": "Digital waveform calculation and assumed linear reconstruction; not measured RF",
              "esp_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
              "source_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
              "seed": 20261027, "level": args.level, "frames": args.frames,
              "definition": "PAPR dB = 10 log10(max |I+jQ|^2 / mean |I+jQ|^2); envelope crest factor = sqrt(PAPR ratio). No extra 3 dB RF-carrier-cycle factor for PA envelope headroom.",
              "reconstruction": "Zero-order hold sinc amplitude, flat brick-wall low-pass at +/-0.8 Rs, 8x DAC interpolation, 256-sample edge guard. Actual ESP32 RF filter, PA compression, AM/PM, IQ imbalance, mixer/filter response and turn-on transients are not known.",
              "checks": "First 4096 symbols per stream match independent fixed-point convolution exactly. 8x/16x interpolation subset convergence within 0.02 dB. Pulse peak bound is unrounded, relative to iid mean; it is not a measured RF worst-case bound.",
              "results": results}
    (out / "crest-factor.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print("Wrote", out / "crest-factor.json")


if __name__ == "__main__":
    main()

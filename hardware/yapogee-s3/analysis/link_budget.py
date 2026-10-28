#!/usr/bin/env python3
"""Free-space planning budget with explicit, unmeasured hardware assumptions.

Power is wanted-channel average power at the PA output BEFORE the final filter.
Defaults use the user's RHCP antenna pair and retain the existing workbook's
remaining losses and NF. Its old C/N threshold is replaced with ETSI Es/N0 plus
an implementation allowance. Polarization loss is an explicit planning allowance.
No circuit, waveform quality, antenna pattern or receiver sensitivity is measured.
"""

import argparse
import json
import math
from pathlib import Path

C = 299792458.0
K = 1.380649e-23
FREQUENCY_HZ = 1.28e9
TEMPERATURE_K = 290.0
LOSSES_DB = {
    "pa_output_filter": 1.0,
    "flight_feeder": 0.3,
    "receive_feeder_filter": 1.0,
    "ground_pointing": 2.0,
}
MODES = {
    "QPSK 1/2": {"symbol_rate_hz": 10e6, "esn0_ideal_db": 1.0,
                 "kbch": 32208, "frame_symbols": 33282},
    "8PSK 2/3": {"symbol_rate_hz": 5e6, "esn0_ideal_db": 6.62,
                 "kbch": 43040, "frame_symbols": 22194},
}


def db(x):
    return 10 * math.log10(x)


def fspl(distance_km):
    return 20 * math.log10(4 * math.pi * distance_km * 1000 * FREQUENCY_HZ / C)


def threshold(mode, nf_db, implementation_db):
    # At 290 K, receiver input noise density is k*T*F in W/Hz.
    n0_dbm_hz = db(K * TEMPERATURE_K * 1000) + nf_db
    return n0_dbm_hz + db(mode["symbol_rate_hz"]) + mode["esn0_ideal_db"] + implementation_db


def result(power_w, mode, args):
    tx_dbm = 30 + db(power_w)
    path_constant = (tx_dbm + args.flight_gain_dbi + args.ground_gain_dbi
                     - sum(LOSSES_DB.values()) - args.polarization_loss_db - args.extra_loss_db)
    rx_threshold = threshold(mode, args.receiver_nf_db, args.implementation_db)
    max_path_loss = path_constant - rx_threshold - args.reserve_db
    max_slant_km = 10 ** ((max_path_loss - fspl(1.0)) / 20)
    samples = []
    for label, distance in [
        ("10000 ft AGL, 100 m horizontal", math.hypot(10000 * 0.3048, 100) / 1000),
        ("30000 ft AGL, 100 m horizontal", math.hypot(30000 * 0.3048, 100) / 1000),
        ("10 km slant", 10.0), ("30 km slant", 30.0),
    ]:
        path_loss = fspl(distance)
        received = path_constant - path_loss
        samples.append({"case": label, "slant_km": distance, "fspl_db": path_loss,
                        "received_at_receiver_dbm": received,
                        "gross_margin_db": received - rx_threshold,
                        "margin_after_reserve_db": received - rx_threshold - args.reserve_db})
    return {"assumed_pa_average_output_w": power_w,
            "receiver_threshold_dbm": rx_threshold,
            "maximum_free_space_slant_km_with_reserve": max_slant_km,
            "cases": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flight-gain-dbi", type=float, default=1.9,
                        help="Nominal co-polar gain: Singularity 1280 V2 RHCP, 1.9 dBic")
    parser.add_argument("--ground-gain-dbi", type=float, default=13.0,
                        help="Nominal co-polar gain: X2-AIR 1.3 RHCP, 13 dBic")
    parser.add_argument("--polarization-loss-db", type=float, default=0.5,
                        help="Assumed residual loss for matched RHCP antennas; not measured")
    parser.add_argument("--receiver-nf-db", type=float, default=8.0)
    parser.add_argument("--implementation-db", type=float, default=2.0)
    parser.add_argument("--reserve-db", type=float, default=10.0)
    parser.add_argument("--extra-loss-db", type=float, default=0.0)
    args = parser.parse_args()
    if any(v < 0 for v in [args.receiver_nf_db, args.implementation_db,
                           args.reserve_db, args.polarization_loss_db, args.extra_loss_db]):
        parser.error("NF, implementation allowance, reserve and loss allowances must be nonnegative")

    powers = [0.1, 0.25, 0.5, 1.0, 2.0]
    calculations = {name: [result(p, mode, args) for p in powers]
                    for name, mode in MODES.items()}
    # Physics and inverse checks: range doubling adds 6.0206 dB loss, while
    # doubling transmitter power adds 3.0103 dB and extends free-space range sqrt(2).
    assert abs(fspl(2) - fspl(1) - 20 * math.log10(2)) < 1e-10
    base = result(1, MODES["QPSK 1/2"], args)
    doubled = result(2, MODES["QPSK 1/2"], args)
    assert abs(doubled["maximum_free_space_slant_km_with_reserve"] /
               base["maximum_free_space_slant_km_with_reserve"] - math.sqrt(2)) < 1e-10
    path_constant = (30 + args.flight_gain_dbi + args.ground_gain_dbi
                     - sum(LOSSES_DB.values()) - args.polarization_loss_db - args.extra_loss_db)
    at_range = path_constant - fspl(base["maximum_free_space_slant_km_with_reserve"])
    assert abs(at_range - base["receiver_threshold_dbm"] - args.reserve_db) < 1e-9

    payloads = {name: {**mode,
                      "rolloff": 0.35,
                      "occupied_bandwidth_hz": 1.35 * mode["symbol_rate_hz"],
                      "payload_mbps": mode["symbol_rate_hz"] *
                                      (mode["kbch"] - 80) / mode["frame_symbols"] / 1e6}
                for name, mode in MODES.items()}
    assumptions = {"frequency_hz": FREQUENCY_HZ, "noise_temperature_k": TEMPERATURE_K,
                   **vars(args), "loss_allocations_db": {**LOSSES_DB, "polarization": args.polarization_loss_db},
                   "total_fixed_loss_db": sum(LOSSES_DB.values()) + args.polarization_loss_db,
                   "power_reference_plane": "average wanted-channel RF at PA output, before final filter",
                   "antenna_gain_reference": "Nominal co-polar RHCP gains (dBic), used directly in Friis; mounted/off-axis gain remains unknown",
                   "receiver_nf_reference": "receiver input connector, excluding receive feeder/filter loss above"}
    source = {
        "existing_assumptions": "calculations/link-budget.xlsx, Link budget C14, C18:C19, C22:C23, C25:C26 and C28",
        "antenna_source": "User identified the RHCP pair; manufacturer nominal gains replace workbook C20:C21",
        "ground_antenna": {"model": "TrueRC X2-AIR 1.3 RHCP", "gain_dbic": 13.0,
                           "specified_band_mhz": [1200, 1360],
                           "half_power_beamwidth_horizontal_deg": 74,
                           "half_power_beamwidth_vertical_deg": 34,
                           "url": "https://truerc.com/35572-xc2b2-air-1-3"},
        "flight_antenna": {"model": "TrueRC Singularity 1280 V2 BLACK RHCP", "gain_dbic": 1.9,
                           "specified_band_mhz": [1250, 1290],
                           "url": "https://truerc.com/37938-singularity-1280-v2-black"},
        "new_assumptions": "0.5 dB residual RHCP polarization loss and 2 dB implementation allowance; neither is measured. The old 3 dB linear/circular penalty does not apply to an ideal RHCP pair.",
        "decoder_thresholds": "ETSI EN 302 307-1 V1.4.1 section 6 Table 13, ideal AWGN, normal frames",
        "standard_url": "https://www.etsi.org/deliver/etsi_en/302300_302399/30230701/01.04.01_60/en_30230701v010401p.pdf",
    }
    folder = Path(__file__).resolve().parent / "output"
    folder.mkdir(exist_ok=True)
    (folder / "link-budget.json").write_text(json.dumps({
        "status": "Planning calculation only; no measured range or clean PA power is established",
        "assumptions": assumptions, "sources": source, "modes": payloads,
        "calculations": calculations,
    }, indent=2, allow_nan=False) + "\n")

    lines = ["# Yapogee S3 link budget", "",
             "Calculated clear-line-of-sight ranges, not measured flight performance. Power is assumed clean average RF at the PA output before its final filter. Antenna nulls and obstruction are not modeled.", "",
             f"1280 MHz; nominal RHCP flight gain {args.flight_gain_dbi:g} dBic; ground gain {args.ground_gain_dbi:g} dBic; receiver NF {args.receiver_nf_db:g} dB; total fixed losses {sum(LOSSES_DB.values()) + args.polarization_loss_db:g} dB including assumed polarization loss {args.polarization_loss_db:g} dB; extra loss {args.extra_loss_db:g} dB; implementation allowance {args.implementation_db:g} dB; reserve {args.reserve_db:g} dB.", "",
             "## Range with reserve", "",
             "| Average PA power W | QPSK 1/2 at 10 Msym/s km | 8PSK 2/3 at 5 Msym/s km |",
             "| ---: | ---: | ---: |"]
    for i, p in enumerate(powers):
        vals = [calculations[name][i]["maximum_free_space_slant_km_with_reserve"] for name in MODES]
        lines.append(f"| {p:g} | {vals[0]:.2f} | {vals[1]:.2f} |")
    for name, cases in calculations.items():
        lines += ["", f"## {name}", "",
                  f"Receiver threshold including implementation allowance: {cases[0]['receiver_threshold_dbm']:.2f} dBm.", "",
                  "| Average PA power W | Case | Slant km | Received dBm | Margin after reserve dB |",
                  "| ---: | --- | ---: | ---: | ---: |"]
        for item in cases:
            for case in item["cases"]:
                lines.append(f"| {item['assumed_pa_average_output_w']:g} | {case['case']} | {case['slant_km']:.3f} | {case['received_at_receiver_dbm']:.2f} | {case['margin_after_reserve_db']:+.2f} |")
    lines += ["", "## Equations", "",
              "FSPL = 20 log10(4 pi d f / c). Received dBm = PA dBm + flight gain + ground gain - fixed losses - extra loss - FSPL.", "",
              "Threshold dBm = 10 log10(k T * 1000) + NF + 10 log10(symbol rate) + required Es/N0 + implementation allowance. Margin after reserve = received power - threshold - reserve.", "",
              "The symbol-rate form includes the bandwidth/noise trade without counting roll-off twice. Equivalent occupied-bandwidth calculation uses B=Rs(1+alpha) and C/N=Es/N0-10log10(1+alpha).", "",
              "The user's antennas are the TrueRC Singularity 1280 V2 BLACK RHCP (nominal 1.9 dBic, 1250-1290 MHz) and X2-AIR 1.3 RHCP (nominal 13 dBic, 1200-1360 MHz). Gains are manufacturer figures, not mounted-pattern measurements. The ground antenna's -3 dB beamwidth is 74 degrees horizontal by 34 degrees vertical; broad coverage wording does not imply constant peak gain.", "",
              "At 1280 MHz, a 13.5 MHz waveform lies nominally within 1273.25-1286.75 MHz and fits both stated antenna bands. A 40 MHz waveform centered at 1280 MHz extends to 1300 MHz and exceeds the flight antenna's stated band. A 40 MHz waveform at 1270 MHz only touches both flight-band edges before guard.", "",
              "Receiver NF, implementation allowance, losses and reserve remain planning assumptions. Every extra 10 dB loss divides these free-space ranges by 3.162. A nameplate antenna gain cannot represent a mounted flight antenna null."]
    report = folder / "link-budget.md"
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:15]))
    print("Receiver thresholds:", {name: round(cases[0]["receiver_threshold_dbm"], 2)
                                   for name, cases in calculations.items()})
    print("Wrote", report)


if __name__ == "__main__":
    main()

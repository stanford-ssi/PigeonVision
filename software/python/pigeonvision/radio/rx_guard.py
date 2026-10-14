#!/usr/bin/env python3
"""Prepare the identified E200 Pluto v0.34 driver for RX-only use.

No RF measurement or interprocess lock: another client, reboot, initialization,
firmware change, or direct register write invalidates this check. The IIO API
cannot generally report another client's active TX buffer. Stop other clients.
Historical driver semantics: MicroPhase/linux commit 9c04de11ae53, ad9361.c.
"""
import argparse
from decimal import Decimal, DecimalException
import sys

from .profile import URI


class GuardError(RuntimeError):
    pass


def required(attrs, name):
    if name not in attrs:
        raise GuardError(f"Missing critical attribute: {name}")
    return attrs[name]


def write_check(attrs, name, value, *, numeric=False, prefix=False):
    attr = required(attrs, name)
    attr.value = str(value)
    actual = attr.value.strip()
    if not actual:
        raise GuardError(f"Empty readback for {name}")
    if numeric:
        try:
            matches = Decimal(actual.split()[0]) == Decimal(str(value))
        except DecimalException as exc:
            raise GuardError(f"Invalid numeric readback for {name}: {actual!r}") from exc
    elif prefix:
        matches = actual.split()[0] == str(value).split()[0]
    else:
        matches = actual == str(value)
    if not matches:
        raise GuardError(f"Readback mismatch for {name}: expected {value}, got {actual!r}")


def prepare(ctx):
    """Mutate only explicit shutdown/calibration controls; fail on missing controls."""
    phy = ctx.find_device("ad9361-phy")
    dac = ctx.find_device("cf-ad9361-dds-core-lpc")
    if phy is None or dac is None:
        raise GuardError("Requires Pluto/IIO ad9361-phy and cf-ad9361-dds-core-lpc")
    txlo = phy.find_channel("altvoltage1", True)
    if txlo is None:
        raise GuardError("Missing TX LO output channel altvoltage1")

    # Shut LO down before inspecting or changing any other setting. On failure,
    # never restore earlier values: leaving shutdown in place is intentional.
    write_check(txlo.attrs, "powerdown", 1, numeric=True)
    for name in ("buffer_enabled", "buffer/enable"):
        if name in dac.attrs and int(dac.attrs[name].value.strip()):
            raise GuardError("Active TX DMA buffer detected; stop its owner first")

    debug = phy.debug_attrs
    write_check(debug, "adi,tx-lo-powerdown-managed-enable", 0, numeric=True)
    # 'manual' alone still runs TX quad calibration on bandwidth/rate updates.
    available = required(phy.attrs, "calib_mode_available").value.split()
    if "manual_tx_quad" not in available:
        raise GuardError("Firmware lacks manual_tx_quad calibration mode")
    write_check(phy.attrs, "calib_mode", "manual_tx_quad", prefix=True)

    for name in ("loopback", "bist_prbs"):
        write_check(debug, name, 0, numeric=True)
    write_check(debug, "bist_tone", "0 0 0 0", prefix=True)

    tones = [c for c in dac.channels if c.output and c.id.startswith("altvoltage")]
    if not tones:
        raise GuardError("Missing DDS tone output channels")
    for tone in tones:
        write_check(tone.attrs, "raw", 0, numeric=True)
        write_check(tone.attrs, "scale", 0, numeric=True)
    txchannels = [c for c in phy.channels if c.output and c.id in ("voltage0", "voltage1")]
    if not any(c.id == "voltage0" for c in txchannels):
        raise GuardError("Missing TX voltage0 output channel")
    for channel in txchannels:
        # Exact identified AD936x range, rather than silently trusting an
        # unrelated device's gain units or range.
        write_check(channel.attrs, "hardwaregain", "-89.75", numeric=True)
    write_check(txlo.attrs, "powerdown", 1, numeric=True)
    return len(tones), len(txchannels)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=URI)
    args = parser.parse_args(argv)
    try:
        import iio  # lazy: --help and mocked tests need no radio bindings
        ctx = iio.Context(args.uri)
        tones, channels = prepare(ctx)
    except (ImportError, OSError, RuntimeError, ValueError, AttributeError, DecimalException) as exc:
        print(f"RX guard refused: {exc}", file=sys.stderr)
        return 1
    print(f"TX LO powerdown=1; {tones} DDS tones zeroed; {channels} TX gains=-89.75 dB.")
    print("Managed TX LO restart disabled; manual_tx_quad mode selected; BIST/loopback off.")
    print("RF silence was not measured. Other-client TX DMA ownership is not generally observable via IIO.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

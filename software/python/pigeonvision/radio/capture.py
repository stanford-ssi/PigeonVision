#!/usr/bin/env python3
"""Bounded E200 RX transport check. Discards IQ; does not prove sample continuity."""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from .rx_guard import prepare
from .profile import URI, SAMPLE_RATE, BUFFER_SAMPLES, KERNEL_BUFFERS


# MicroPhase/linux 9c04de11ae53 cf_axi_adc_core.c routes the magic-bit
# register address to the ADC FPGA core (not the AD936x SPI registers).
DMA_STATUS = 0x80000088
DMA_OVERFLOW = 4


def measure_refills(iio, adc, seconds, buffer_samples, kernel_buffers=KERNEL_BUFFERS, clock=time.monotonic):
    """Count native refill return bytes without copying IQ into Python.

    libiio 0.26's public Buffer.refill discards the ssize_t return value;
    its ctypes wrapper preserves it. Require that exact interface explicitly.
    """
    refill = getattr(iio, "_buffer_refill", None)
    if refill is None:
        raise RuntimeError("Requires libiio Python 0.26 _buffer_refill byte-count interface")
    for channel in adc.channels:
        if not channel.output:
            channel.enabled = channel.id in ("voltage0", "voltage1")
    if adc.sample_size != 4:
        raise RuntimeError("Expected exactly one 16-bit I/Q pair (4 bytes per sample)")
    adc.set_kernel_buffers_count(kernel_buffers)
    buffer = iio.Buffer(adc, buffer_samples, False)
    try:
        # Warm up the already-running stream; discard these bytes and setup time.
        deadline = clock() + 1.0
        while clock() < deadline:
            if refill(buffer._buffer) <= 0:
                raise RuntimeError("Empty RX refill during warmup")
        warmup_status = adc.reg_read(DMA_STATUS)
        adc.reg_write(DMA_STATUS, DMA_OVERFLOW)
        started = clock()
        next_poll = started + 0.25
        total_bytes = refills = overflow_observations = 0
        status = 0
        poll_seconds = 0.0
        poll_count = 0
        error = None
        while clock() - started < seconds:
            try:
                count = refill(buffer._buffer)
                if count <= 0 or count % 4:
                    raise RuntimeError(f"Invalid RX refill byte count: {count}")
                total_bytes += count
                refills += 1
                now = clock()
                if now >= next_poll:
                    poll_started = clock()
                    status = adc.reg_read(DMA_STATUS)
                    if status & DMA_OVERFLOW:
                        overflow_observations += 1
                        adc.reg_write(DMA_STATUS, DMA_OVERFLOW)
                    poll_seconds += clock() - poll_started
                    poll_count += 1
                    next_poll = clock() + 0.25
            except (OSError, RuntimeError) as exc:
                error = str(exc)
                break
        elapsed = clock() - started
        status = adc.reg_read(DMA_STATUS)
        if status & DMA_OVERFLOW:
            overflow_observations += 1
        return {
            "warmup_seconds": 1.0,
            "warmup_dma_status": warmup_status,
            "measurement_seconds": elapsed,
            "kernel_buffers": kernel_buffers,
            "status_poll_interval_seconds": 0.25,
            "status_polls_during_measurement": poll_count,
            "status_query_seconds_during_measurement": poll_seconds,
            "delivered_bytes": total_bytes,
            "delivered_complex_samples": total_bytes // 4,
            "refills": refills,
            "delivered_complex_samples_per_second": total_bytes / 4 / elapsed,
            "overflow_observations": overflow_observations,
            "dma_status_after": status,
            "capture_completed": error is None,
            "receiver_error": error,
        }
    finally:
        # libiio Buffer owns the native RX buffer; destruction closes streaming.
        del buffer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", default=URI)
    parser.add_argument("--sample-rate", type=int, default=SAMPLE_RATE)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--buffer-samples", type=int, default=BUFFER_SAMPLES)
    parser.add_argument("--kernel-buffers", type=int, choices=(4, 8, 16), default=KERNEL_BUFFERS)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 60:
        parser.error("--seconds must be between 1 and 60")
    if not 3_000_000 <= args.sample_rate <= 16_000_000:
        parser.error("this bench check supports 3–16 MS/s")
    if args.buffer_samples not in (65536, 262144, 1048576):
        parser.error("--buffer-samples must be 65536, 262144 or 1048576")
    if args.output.exists():
        parser.error("output already exists; choose a new result path")
    import iio

    ctx = iio.Context(args.uri)
    ctx.set_timeout(3000)
    prepare(ctx)
    phy = ctx.find_device("ad9361-phy")
    rx = phy.find_channel("voltage0", False)
    adc = ctx.find_device("cf-ad9361-lpc")
    if rx is None or adc is None:
        raise RuntimeError("RX1 control or capture device missing")
    # RX-only guard suppresses TX calibration. Leave the existing RX LO intact.
    for key, value in (("gain_control_mode", "manual"), ("hardwaregain", "0"),
                       ("sampling_frequency", str(args.sample_rate)),
                       ("rf_bandwidth", str(args.sample_rate))):
        rx.attrs[key].value = value
    actual_rate = int(rx.attrs["sampling_frequency"].value)
    if actual_rate != args.sample_rate:
        raise RuntimeError(f"RX rate readback {actual_rate} != {args.sample_rate}")
    for name in ("voltage0", "voltage1"):
        channel = adc.find_channel(name, False)
        if channel is None or int(channel.attrs["sampling_frequency"].value) != actual_rate:
            raise RuntimeError("ADC channel rate does not match the RX rate")
        if channel.data_format.length != 16:
            raise RuntimeError("Expected 16-bit I and Q storage")
    lo = phy.find_channel("altvoltage1", True)
    if lo.attrs["powerdown"].value.strip() != "1":
        raise RuntimeError("TX LO shutdown changed during RX configuration")

    try:
        measurement = measure_refills(iio, adc, args.seconds, args.buffer_samples, args.kernel_buffers)
    except (OSError, RuntimeError) as exc:
        measurement = {"capture_completed": False, "receiver_error": str(exc),
                       "delivered_complex_samples_per_second": None}
    state = {
        "tx_lo_powerdown": lo.attrs["powerdown"].value.strip(),
        "managed_tx_lo_restart": phy.debug_attrs["adi,tx-lo-powerdown-managed-enable"].value.strip(),
        "calib_mode": phy.attrs["calib_mode"].value.strip(),
        "ensm_mode": phy.attrs["ensm_mode"].value.strip(),
    }
    shutdown_valid = (state["tx_lo_powerdown"] == "1"
                      and state["managed_tx_lo_restart"] == "0"
                      and state["calib_mode"].split()[:1] == ["manual_tx_quad"])
    if not shutdown_valid:
        # Restore shutdown before filesystem/reporting operations can fail.
        prepare(ctx)
    capture_completed = measurement["capture_completed"] and shutdown_valid
    report = {
        "utc": datetime.now(timezone.utc).isoformat(),
        "uri": args.uri,
        "firmware": dict(ctx.attrs),
        "sample_rate_hz": actual_rate,
        "rx_lo_hz": int(phy.find_channel("altvoltage0", True).attrs["frequency"].value),
        "rf_bandwidth_hz": int(rx.attrs["rf_bandwidth"].value),
        "rx_gain": rx.attrs["hardwaregain"].value,
        "buffer_samples": args.buffer_samples,
        **measurement,
        "capture_completed": capture_completed,
        "shutdown_state_valid_after_capture": shutdown_valid,
        "state_after": state,
        "contiguous_samples_proven": False,
        "note": "IQ discarded; actual native refill byte counts measured after warmup. "
                "Historical kernel clears DMA status on each block submission: zero observed "
                "overflows does not prove continuity. No sequence counter or RF input.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(json.dumps(report, indent=2))
    if not shutdown_valid:
        raise RuntimeError("TX shutdown state changed during capture; guard reapplied")
    return 0 if capture_completed else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Check video decode deadlines in a captured constant-rate MPEG transport stream.

Optional arrivals are [cumulative_bytes, CLOCK_BOOTTIME_ns] pairs captured on the
sender host. Supply the capture session.json to compare those times with DTS.
This checks transport timing, not decoder quality, RF or SPI acceptance.
"""
import argparse
import bisect
import json
from pathlib import Path


def timestamp(data):
    return (((data[0] >> 1) & 7) << 30 | data[1] << 22 |
            (data[2] >> 1) << 15 | data[3] << 7 | data[4] >> 1) / 90000


def check(path, bitrate, arrivals=None, origin_ns=None):
    data = path.read_bytes()
    if len(data) % 188:
        raise ValueError("incomplete TS packet")
    offsets = [row[0] for row in arrivals] if arrivals else []
    previous_cc, deadlines, frames = {}, {}, {}
    margins, arrival_margins = [], []
    continuity_errors = late_packets = pcr_count = 0
    pcr_error = 0.0
    anchor = None
    for offset in range(0, len(data), 188):
        packet = data[offset:offset + 188]
        if packet[0] != 0x47 or packet[1] & 0x80:
            raise ValueError(f"invalid TS packet at byte {offset}")
        pid = (packet[1] & 31) << 8 | packet[2]
        control = packet[3] >> 4 & 3
        payload = 4
        if control & 2:
            length = packet[4]
            payload = 5 + length
            if payload > 188:
                raise ValueError("invalid adaptation length")
            if length >= 7 and packet[5] & 16:
                base = (packet[6] << 25 | packet[7] << 17 | packet[8] << 9 |
                        packet[9] << 1 | packet[10] >> 7)
                pcr = base / 90000 + ((packet[10] & 1) << 8 | packet[11]) / 27000000
                pcr_count += 1
                if anchor is None:
                    anchor = (offset + 12, pcr)
                predicted = anchor[1] + (offset + 12 - anchor[0]) * 8 / bitrate
                pcr_error = max(pcr_error, abs(pcr - predicted))
        if not control & 1 or payload >= 188 or pid == 8191:
            continue
        cc = packet[3] & 15
        if pid in previous_cc and cc != (previous_cc[pid] + 1) % 16:
            continuity_errors += 1
        previous_cc[pid] = cc
        if pid not in (256, 257):
            continue
        if packet[1] & 64:
            pes = packet[payload:]
            if len(pes) < 14 or pes[:3] != b"\x00\x00\x01" or not pes[7] & 0x80:
                raise ValueError("video PES lacks timestamp")
            start = 14 if pes[7] & 0x40 else 9
            deadlines[pid] = timestamp(pes[start:start + 5])
            frames[pid] = frames.get(pid, 0) + 1
        if pid not in deadlines or anchor is None:
            continue
        end_clock = anchor[1] + (offset + 188 - anchor[0]) * 8 / bitrate
        margin = deadlines[pid] - end_clock
        margins.append(margin)
        late_packets += margin < 0
        if arrivals:
            index = bisect.bisect_left(offsets, offset + 188)
            if index == len(arrivals):
                raise ValueError("arrival log ends before TS capture")
            received = (arrivals[index][1] - origin_ns) / 1e9
            arrival_margins.append(deadlines[pid] - received)
    if not margins:
        raise ValueError("no video with PCR timing found")
    return {
        "bytes": len(data), "mux_bitrate": bitrate, "video_pes": frames,
        "continuity_errors": continuity_errors, "pcr_samples": pcr_count,
        "max_pcr_error_us": pcr_error * 1e6,
        "late_video_ts_packets": late_packets,
        "min_decode_margin_ms": min(margins) * 1000,
        "min_host_delivery_margin_ms": min(arrival_margins) * 1000 if arrivals else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("transport", type=Path)
    parser.add_argument("--bitrate", type=int, default=9000000)
    parser.add_argument("--arrivals", type=Path)
    parser.add_argument("--session", type=Path)
    args = parser.parse_args()
    if args.bitrate <= 0 or bool(args.arrivals) != bool(args.session):
        parser.error("positive bitrate required; arrivals and session must be supplied together")
    arrivals = json.loads(args.arrivals.read_text()) if args.arrivals else None
    origin = json.loads(args.session.read_text())["clock_origin_ns"] if args.session else None
    result = check(args.transport, args.bitrate, arrivals, origin)
    print(json.dumps(result, indent=2))
    raise SystemExit(bool(result["continuity_errors"] or result["late_video_ts_packets"] or
                          result["max_pcr_error_us"] > 1000 or
                          (result["min_host_delivery_margin_ms"] is not None and
                           result["min_host_delivery_margin_ms"] < 0)))

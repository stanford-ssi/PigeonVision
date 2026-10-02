"""MPEG-TS/H.264 to the shared browser access-unit contract.

Timestamp conversion uses each packet's rational time base. Stream offsets are
never removed. This is capture-timestamp transport, not proof of shutter sync.
"""
from dataclasses import dataclass
from fractions import Fraction
import json
import re
import struct


VIDEO_PIDS = {256: "A", 257: "B"}
METADATA_PID = 258
START_CODE = b"\x00\x00\x00\x01"


def unsigned_exp_golomb(data: bytes, count: int) -> list[int]:
    """Read the leading unsigned Exp-Golomb fields of an H.264 RBSP."""
    rbsp = data.replace(b"\x00\x00\x03", b"\x00\x00")
    offset, result = 0, []
    def bit():
        nonlocal offset
        if offset >= len(rbsp) * 8:
            raise ValueError("Truncated H.264 parameter/slice header")
        value = rbsp[offset // 8] >> (7 - offset % 8) & 1
        offset += 1
        return value
    for _ in range(count):
        zeros = 0
        while bit() == 0:
            zeros += 1
            if zeros > 31:
                raise ValueError("Invalid H.264 Exp-Golomb field")
        value = 1
        for _ in range(zeros):
            value = value * 2 + bit()
        result.append(value - 1)
    return result


def annexb_nals(data: bytes) -> list[bytes]:
    return [part for part in re.split(b"\x00\x00\x00?\x01", data) if part]


def pts_microseconds(pts: int, time_base: Fraction) -> int:
    return round(Fraction(pts) * time_base * 1_000_000)


@dataclass(frozen=True)
class AccessUnit:
    camera_id: str
    timestamp_us: int
    keyframe: bool
    codec: str
    data: bytes

    def encode(self) -> bytes:
        header = json.dumps({"type": "frame", "camera_id": self.camera_id,
                             "timestamp_us": self.timestamp_us,
                             "keyframe": self.keyframe, "codec": self.codec},
                            separators=(",", ":")).encode()
        return struct.pack(">I", len(header)) + header + self.data


def unpack_message(message: bytes) -> tuple[dict, bytes]:
    if len(message) < 4:
        raise ValueError("Truncated access-unit header")
    size = struct.unpack(">I", message[:4])[0]
    if size > 16384 or len(message) < size + 4:
        raise ValueError("Invalid access-unit header length")
    return json.loads(message[4:4 + size]), message[4 + size:]


class H264Normalizer:
    """Retain SPS/PPS and emit complete Annex-B IDRs for browser recovery."""

    def __init__(self, extradata: bytes = b""):
        self.sps: dict[int, bytes] = {}
        self.pps: dict[int, bytes] = {}
        self.pps_sps: dict[int, int] = {}
        self.length_size = 4
        self.waiting_for_keyframe = True
        if extradata:
            if extradata[0] == 1:
                self._read_avcc(extradata)
            else:
                self._remember(annexb_nals(extradata))

    def _read_avcc(self, data: bytes) -> None:
        if len(data) < 7:
            raise ValueError("Truncated AVC decoder configuration")
        self.length_size = (data[4] & 3) + 1
        pos = 6
        nals = []
        for count in [data[5] & 31, None]:
            if count is None:
                if pos >= len(data):
                    raise ValueError("Missing AVC PPS count")
                count, pos = data[pos], pos + 1
            for _ in range(count):
                if pos + 2 > len(data):
                    raise ValueError("Truncated AVC parameter set")
                size = int.from_bytes(data[pos:pos + 2], "big")
                pos += 2
                if pos + size > len(data):
                    raise ValueError("Truncated AVC parameter set payload")
                nals.append(data[pos:pos + size])
                pos += size
        self._remember(nals)

    def _remember(self, nals: list[bytes]) -> None:
        # IDs are bounded by H.264; replace changed configurations instead of
        # retaining conflicting historical SPS/PPS indefinitely.
        for nal in nals:
            kind = nal[0] & 31
            if kind == 7:
                if len(nal) < 5:
                    raise ValueError("Truncated H.264 SPS")
                ident = unsigned_exp_golomb(nal[4:], 1)[0]
                if ident > 31:
                    raise ValueError("Invalid H.264 SPS ID")
                target = self.sps
            elif kind == 8:
                ident, sps_id = unsigned_exp_golomb(nal[1:], 2)
                if ident > 255 or sps_id > 31:
                    raise ValueError("Invalid H.264 PPS ID")
                self.pps_sps[ident] = sps_id
                target = self.pps
            else:
                continue
            if ident in target and target[ident] != nal:
                self.waiting_for_keyframe = True
            target[ident] = nal

    def _nals(self, data: bytes) -> list[bytes]:
        if data.startswith((b"\x00\x00\x01", START_CODE)):
            return annexb_nals(data)
        nals, pos = [], 0
        while pos < len(data):
            if pos + self.length_size > len(data):
                raise ValueError("Truncated length-prefixed H.264 NAL")
            size = int.from_bytes(data[pos:pos + self.length_size], "big")
            pos += self.length_size
            if size == 0 or pos + size > len(data):
                raise ValueError("Invalid length-prefixed H.264 NAL")
            nals.append(data[pos:pos + size])
            pos += size
        return nals

    def normalize(self, data: bytes, camera_id: str, timestamp_us: int,
                  *, corrupt: bool = False) -> AccessUnit | None:
        if corrupt:
            self.waiting_for_keyframe = True
            return None
        nals = self._nals(data)
        self._remember(nals)
        keyframe = any(nal[0] & 31 == 5 for nal in nals)
        if not any(nal[0] & 31 in (1, 5) for nal in nals):
            return None
        if not self.sps or not self.pps or (self.waiting_for_keyframe and not keyframe):
            return None
        slice_nal = next(nal for nal in nals if nal[0] & 31 in (1, 5))
        pps_id = unsigned_exp_golomb(slice_nal[1:], 3)[2]
        if pps_id not in self.pps_sps or self.pps_sps[pps_id] not in self.sps:
            self.waiting_for_keyframe = True
            return None
        sps = self.sps[self.pps_sps[pps_id]]
        if len(sps) < 4:
            raise ValueError("Truncated H.264 SPS")
        if keyframe:
            nals = list(self.sps.values()) + list(self.pps.values()) + [
                nal for nal in nals if nal[0] & 31 not in (7, 8)]
            self.waiting_for_keyframe = False
        return AccessUnit(camera_id, timestamp_us, keyframe,
                          "avc1." + sps[1:4].hex(),
                          b"".join(START_CODE + nal for nal in nals))


def read_metadata(data: bytes) -> dict:
    value = json.loads(data.rstrip(b"\0").decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Metadata must be a JSON object")
    return {"type": "metadata", "record": value}

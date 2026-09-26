"""Read MPEG-TS while auditing 188-byte packet continuity before demux.

Demux corruption flags alone do not detect a whole lost access unit. Events are
retained with byte offsets so read-ahead does not reset the decoder too early.
"""
from collections import deque
import io
from pathlib import Path
import socket
from urllib.parse import urlparse


class ContinuityAudit:
    def __init__(self):
        self.pending = bytearray()
        self.offset = 0
        self.previous = {}
        self.events = deque()
        self.errors = {}

    def _event(self, pid, reason, offset):
        self.errors[pid] = self.errors.get(pid, 0) + 1
        # Probe/read-ahead is bounded, but never allow malformed input to grow
        # an unlimited event log. A coalesced global event forces both IDRs.
        if len(self.events) >= 4096:
            self.events.clear()
            self.events.append({"pid": None, "reason": "Many transport discontinuities", "offset": offset})
        else:
            self.events.append({"pid": pid, "reason": reason, "offset": offset})

    def feed(self, data: bytes):
        self.pending.extend(data)
        while len(self.pending) >= 188:
            if self.pending[0] != 0x47:
                next_sync = self.pending.find(0x47, 1)
                skip = next_sync if next_sync >= 0 else len(self.pending) - 187
                self._event(None, "MPEG-TS sync byte lost", self.offset)
                self.previous.clear()
                del self.pending[:skip]
                self.offset += skip
                continue
            packet = bytes(self.pending[:188])
            del self.pending[:188]
            offset, self.offset = self.offset, self.offset + 188
            pid = ((packet[1] & 31) << 8) | packet[2]
            if pid == 8191:
                continue
            control, counter = (packet[3] >> 4) & 3, packet[3] & 15
            if packet[1] & 128 or control == 0:
                self._event(pid, "MPEG-TS transport error", offset)
                self.previous.pop(pid, None)
                continue
            if control & 2 and packet[4] > 0 and packet[5] & 128:
                self._event(pid, "Declared MPEG-TS discontinuity", offset)
                self.previous.pop(pid, None)
            if not control & 1:
                continue
            prior = self.previous.get(pid)
            if prior is not None:
                old_counter, old_packet = prior
                if counter == old_counter and packet == old_packet:
                    continue  # MPEG-TS allows an identical duplicated packet.
                if counter != (old_counter + 1) % 16:
                    self._event(pid, "MPEG-TS continuity counter gap", offset)
            self.previous[pid] = (counter, packet)

    def pop_through(self, position: int | None):
        while self.events and (position is None or position < 0 or self.events[0]["offset"] <= position):
            yield self.events.popleft()


class TsInput(io.RawIOBase):
    def __init__(self, source: str, stop_event):
        self.audit = ContinuityAudit()
        self.stop_event = stop_event
        self.file = None
        self.socket = None
        parsed = urlparse(source)
        if parsed.scheme == "udp":
            if parsed.port is None:
                raise ValueError("UDP source requires a listening port")
            family = socket.AF_INET6 if ":" in (parsed.hostname or "") else socket.AF_INET
            self.socket = socket.socket(family, socket.SOCK_DGRAM)
            try:
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
                self.socket.settimeout(2)
                self.socket.bind((parsed.hostname or "0.0.0.0", parsed.port))
            except Exception:
                self.socket.close()
                raise
        elif parsed.scheme:
            raise ValueError("Ground source must be a saved TS path or udp:// listening address")
        else:
            self.file = Path(source).open("rb")
        self.buffer = bytearray()

    def readable(self):
        return True

    def read(self, size=-1):
        if self.stop_event.is_set():
            return b""
        if self.file:
            data = self.file.read(size)
        elif self.socket:
            if not self.buffer:
                try:
                    self.buffer.extend(self.socket.recv(65535))
                except TimeoutError as exc:
                    raise TimeoutError("No UDP transport received for two seconds") from exc
            count = len(self.buffer) if size < 0 else min(size, len(self.buffer))
            data = bytes(self.buffer[:count])
            del self.buffer[:count]
        else:
            return b""
        self.audit.feed(data)
        return data

    def close(self):
        if self.file:
            self.file.close()
        if self.socket:
            self.socket.close()
        super().close()

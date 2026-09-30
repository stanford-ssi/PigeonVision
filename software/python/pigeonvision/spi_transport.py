"""CM5 PV-SPI v1 sender. Hardware imports are optional until a run starts.

Each xfer2 call is one 1332-byte SPI_IOC_MESSAGE(1), with hardware CS. The
summary's crc_chain covers completed TS_DATA writes, not receiver acknowledgments
(PV-SPI v1 has none). Compare it with the Pico's accepted-message chain.
Framing and pattern follow RP2350_IQ_Benchmark/host/cm5/pv_spi_tx.py.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from functools import lru_cache
import ipaddress
import json
import math
from pathlib import Path
import queue
import signal
import socket
import struct
import sys
import threading
import time
from typing import Callable, Iterator
import zlib

MAGIC, VERSION, NOP, TS_DATA = 0x5650, 1, 0, 1
TS_BYTES, PAYLOAD_BYTES, MESSAGE_BYTES = 188, 1316, 1332
CS_HIGH_SECONDS = 10e-6
READY_WARNING_SECONDS = .020


def validate_payload(payload: bytes) -> None:
    if not payload or len(payload) > PAYLOAD_BYTES or len(payload) % TS_BYTES:
        raise ValueError("TS payload must contain 1..7 whole 188-byte packets")
    if any(payload[offset] != 0x47 for offset in range(0, len(payload), TS_BYTES)):
        raise ValueError("TS packet does not start with sync byte 0x47")


def message(seq: int, payload: bytes, mtype: int = TS_DATA) -> bytes:
    """Build one frame; NOP has no payload. Sequence wraps at 16 bits."""
    if mtype == TS_DATA:
        validate_payload(payload)
    elif mtype != NOP or payload:
        raise ValueError("message type must be TS_DATA, or NOP with empty payload")
    body = struct.pack("<HBBHHI", MAGIC, VERSION, mtype, seq & 0xffff, len(payload), 0)
    body += payload + bytes(PAYLOAD_BYTES - len(payload))
    return body + struct.pack("<I", zlib.crc32(body))


def pattern_packet(k: int) -> bytes:
    """Match Pico self-test: PID 0x100, CC k%16, period 112 packets."""
    return bytes((0x47, 0x01, 0x00, 0x10 | (k & 15))) + bytes(
        ((k % 112) + j) & 255 for j in range(184))


@lru_cache(maxsize=16)
def _pattern_payload(index: int) -> bytes:
    return b"".join(pattern_packet(7 * index + k) for k in range(7))


def pattern_payload(index: int) -> bytes:
    return _pattern_payload(index % 16)


def vector() -> bytes:
    return message(1, pattern_packet(0))


class Stopped(Exception):
    def __init__(self, reason: str, exit_code: int = 0):
        super().__init__(reason)
        self.reason, self.exit_code = reason, exit_code


class Control:
    """Cancellation/deadline shared by source and sender; no unbounded wait."""
    def __init__(self, duration: float | None = None, *, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.deadline = None if duration is None else self.started + duration
        self.event = threading.Event()
        self.reason, self.exit_code = "stopped", 0

    def stop(self, reason="stopped", exit_code=0):
        self.reason, self.exit_code = reason, exit_code
        self.event.set()

    def check(self):
        if self.event.is_set():
            raise Stopped(self.reason, self.exit_code)
        if self.deadline is not None and self.clock() >= self.deadline:
            raise Stopped("duration")


@dataclass
class SendStats:
    messages: int = 0
    attempted_messages: int = 0
    ts_packets: int = 0
    payload_bytes: int = 0
    next_seq: int = 0
    crc_chain: int = 0
    ready_waits: int = 0
    ready_timeouts: int = 0
    ready_waits_over_20ms: int = 0
    max_ready_wait_ms: float = 0
    pending_payload_bytes: int = 0
    transfer_uncertain: bool = False
    mirror_datagrams: int = 0
    mirror_errors: int = 0


class Sender:
    """Testable SPI/READY state machine. An SPI failure is never retried."""
    def __init__(self, spi, ready, *, hz=1_000_000, ready_timeout=1., poll_us=20.,
                 control: Control | None = None, clock=time.monotonic,
                 sleep=time.sleep, warning: Callable[[str], None] | None = None,
                 source_check: Callable[[], None] | None = None, mirror=None):
        self.spi, self.ready = spi, ready
        self.hz, self.ready_timeout = hz, ready_timeout
        self.poll_seconds = poll_us * 1e-6
        self.clock, self.sleep = clock, sleep
        self.control = control or Control(clock=clock)
        self.warning = warning or (lambda text: print(text, file=sys.stderr))
        self.source_check = source_check or (lambda: None)
        self.mirror = mirror
        self.stats = SendStats()
        # ioctl return is after CS deassertion. Timing from here is conservative.
        self.cs_released = clock()

    def _check(self):
        self.control.check()
        self.source_check()

    def _pause(self, seconds):
        if self.control.deadline is not None:
            seconds = min(seconds, max(0., self.control.deadline - self.clock()))
        self.sleep(min(seconds, .05))

    def _ready(self):
        self._check()
        while (remaining := self.cs_released + CS_HIGH_SECONDS - self.clock()) > 0:
            self._pause(remaining)
            self._check()
        started, waited, warned = self.clock(), False, False
        try:
            while True:
                self._check()
                elapsed = self.clock() - started
                if waited and elapsed >= self.ready_timeout:
                    self.stats.ready_timeouts += 1
                    raise TimeoutError(f"READY stayed low for {elapsed:.3f} s")
                if self.ready.get():
                    return
                if not waited:
                    waited = True
                    self.stats.ready_waits += 1
                if elapsed >= READY_WARNING_SECONDS and not warned:
                    warned = True
                    self.stats.ready_waits_over_20ms += 1
                    self.warning(f"pv spi: READY wait exceeded 20 ms ({elapsed * 1000:.1f} ms)")
                self._pause(min(self.poll_seconds, max(0., self.ready_timeout - elapsed)))
        finally:
            if waited:
                self.stats.max_ready_wait_ms = max(
                    self.stats.max_ready_wait_ms, (self.clock() - started) * 1000)

    def send(self, payload: bytes):
        validate_payload(payload)
        self.stats.pending_payload_bytes = len(payload)
        frame = message(self.stats.next_seq, payload)
        # Convert before READY is sampled, leaving only the ioctl after it.
        tx = list(frame)
        self._ready()
        self.stats.attempted_messages += 1
        try:
            rx = self.spi.xfer2(tx, self.hz, 0, 8)
            if len(rx) != MESSAGE_BYTES:
                raise RuntimeError(f"SPI returned {len(rx)} bytes, expected {MESSAGE_BYTES}")
        except BaseException:
            # The Pico may have accepted some/all bytes. Retrying could duplicate TS.
            self.stats.transfer_uncertain = True
            raise
        finally:
            self.cs_released = self.clock()
        self.stats.messages += 1
        self.stats.ts_packets += len(payload) // TS_BYTES
        self.stats.payload_bytes += len(payload)
        self.stats.crc_chain = zlib.crc32(frame[-4:], self.stats.crc_chain)
        self.stats.next_seq = (self.stats.next_seq + 1) & 0xffff
        self.stats.pending_payload_bytes = 0
        if self.mirror is not None:
            try:
                if self.mirror.send(payload) != len(payload):
                    raise OSError("short UDP mirror send")
                self.stats.mirror_datagrams += 1
            except OSError:
                self.stats.mirror_errors += 1
                raise

    def run(self, payloads, count: int | None = None) -> str:
        iterator = iter(payloads)
        while count is None or self.stats.messages < count:
            self._check()
            try:
                payload = next(iterator)
            except StopIteration:
                self._check()
                return "eof"
            self.send(payload)
        return "count"


def file_payloads(path: Path) -> Iterator[bytes]:
    """Fail on any incomplete TS tail, rather than trimming it."""
    with path.open("rb") as source:
        while payload := source.read(PAYLOAD_BYTES):
            validate_payload(payload)
            yield payload


def pattern_payloads() -> Iterator[bytes]:
    index = 0
    while True:
        yield pattern_payload(index)
        index += 1


def endpoint(text: str) -> tuple[str, int]:
    try:
        host, separator, port_text = text.rpartition(":")
        if not separator:
            raise ValueError
        address = ipaddress.IPv4Address(host)
        port = int(port_text)
        if not 1 <= port <= 65535:
            raise ValueError
        return str(address), port
    except ValueError as exc:
        raise ValueError("UDP endpoint must be a numeric IPv4 address:port (1..65535)") from exc


@dataclass
class UdpStats:
    datagrams_received: int = 0
    bytes_received: int = 0
    invalid_datagrams: int = 0
    queue_overflows: int = 0
    kernel_drops: int = 0
    queue_high_water: int = 0
    buffered_unsent: int = 0
    receive_buffer_requested_bytes: int = 0
    receive_buffer_bytes: int = 0
    receive_buffer_below_spec: bool = False
    kernel_drop_monitor: bool = False


class UdpSource:
    """Bounded receive worker; overflow is a fatal, reported source error.

SO_RXQ_OVFL reports Linux receive-queue drops on subsequent datagrams. It cannot
detect loss upstream of this socket, or a final drop with no subsequent arrival.
"""
    def __init__(self, address: str, control: Control, *, interface="0.0.0.0",
                 queue_messages=256, rcvbuf=4 << 20, idle_timeout=2.,
                 socket_factory=socket.socket, overflow_option: int | None = None):
        host, port = endpoint(address)
        interface = str(ipaddress.IPv4Address(interface))
        if overflow_option is None:
            if not sys.platform.startswith("linux"):
                raise RuntimeError("UDP SPI input requires Linux SO_RXQ_OVFL drop accounting")
            overflow_option = getattr(socket, "SO_RXQ_OVFL", 40)
        self.control, self.idle_timeout = control, idle_timeout
        self.queue = queue.Queue(maxsize=queue_messages)
        self.stats, self.error = UdpStats(), None
        self.closed = threading.Event()
        self.sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        self.overflow_option = overflow_option
        self.last_drop_count = 0
        self.thread = None
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
            self.sock.setsockopt(socket.SOL_SOCKET, overflow_option, 1)
            self.stats.kernel_drop_monitor = True
            self.stats.receive_buffer_requested_bytes = rcvbuf
            self.stats.receive_buffer_bytes = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
            # Linux returns twice the configured SO_RCVBUF for bookkeeping.
            self.stats.receive_buffer_below_spec = self.stats.receive_buffer_bytes < 2 << 20
            if self.stats.receive_buffer_below_spec:
                print(f"pv spi: UDP SO_RCVBUF is {self.stats.receive_buffer_bytes} bytes "
                      f"including Linux bookkeeping (requested {rcvbuf}); configure "
                      "net.core.rmem_max >= 1048576 for the spec's >= 1 MiB receive buffer",
                      file=sys.stderr)
            self.sock.settimeout(.05)
            multicast = ipaddress.IPv4Address(host).is_multicast
            # Unicast uses the specified local address. No implicit wildcard bind.
            self.sock.bind(("0.0.0.0" if multicast else host, port))
            if multicast:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                     socket.inet_aton(host) + socket.inet_aton(interface))
            self.thread = threading.Thread(target=self._receive, name="pv-spi-udp", daemon=True)
            self.thread.start()
        except BaseException:
            self.sock.close()
            raise

    def _receive(self):
        last_data = self.control.clock()
        try:
            while not self.closed.is_set():
                self.control.check()
                try:
                    payload, ancillary, flags, _ = self.sock.recvmsg(65535, socket.CMSG_SPACE(4))
                except socket.timeout:
                    if self.control.clock() - last_data >= self.idle_timeout:
                        raise TimeoutError(f"No UDP TS datagram for {self.idle_timeout:g} s")
                    continue
                last_data = self.control.clock()
                self.stats.datagrams_received += 1
                self.stats.bytes_received += len(payload)
                for level, kind, data in ancillary:
                    if level == socket.SOL_SOCKET and kind == self.overflow_option and len(data) >= 4:
                        total = struct.unpack("=I", data[:4])[0]
                        self.stats.kernel_drops += (total - self.last_drop_count) & 0xffffffff
                        self.last_drop_count = total
                if self.stats.kernel_drops:
                    raise RuntimeError(f"UDP kernel receive queue dropped {self.stats.kernel_drops} datagrams")
                try:
                    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                        raise ValueError("UDP datagram or overflow metadata was truncated")
                    validate_payload(payload)
                except ValueError:
                    self.stats.invalid_datagrams += 1
                    raise
                try:
                    self.queue.put_nowait(payload)
                except queue.Full as exc:
                    self.stats.queue_overflows += 1
                    raise RuntimeError("UDP software queue overflow; stopping instead of dropping TS silently") from exc
                self.stats.queue_high_water = max(self.stats.queue_high_water, self.queue.qsize())
        except Stopped:
            pass
        except (OSError, ValueError, RuntimeError) as exc:
            if not self.closed.is_set():
                self.error = exc

    def check(self):
        if self.error is not None:
            raise self.error

    def __iter__(self):
        while True:
            self.control.check()
            self.check()
            try:
                yield self.queue.get(timeout=.05)
            except queue.Empty:
                continue

    def close(self):
        self.closed.set()
        self.sock.close()
        if self.thread is not None:
            self.thread.join(timeout=.2)
            if self.thread.is_alive():
                raise RuntimeError("UDP receiver did not stop within 200 ms")
        self.stats.buffered_unsent = self.queue.qsize()


class Ready:
    def __init__(self, gpiod, pin: int, chip: str | None):
        from gpiod.line import Direction, Value
        if chip is None:
            matches = set()
            for candidate in sorted(Path("/dev").glob("gpiochip*")):
                with gpiod.Chip(str(candidate)) as device:
                    if "rp1" in device.get_info().label.lower():
                        matches.add(str(candidate.resolve()))
            if len(matches) != 1:
                raise RuntimeError(f"Expected one RP1 GPIO chip, found {len(matches)}; specify --gpiochip")
            chip = matches.pop()
        self.pin, self.active = pin, Value.ACTIVE
        with gpiod.Chip(chip) as device:
            info = device.get_info()
            if "rp1" not in info.label.lower():
                raise RuntimeError(f"READY chip {chip} is not RP1 ({info.label})")
            if not 0 <= pin < info.num_lines or device.get_line_info(pin).name != f"GPIO{pin}":
                raise RuntimeError(f"READY offset {pin} does not map to RP1 GPIO{pin} on {chip}")
        self.request = gpiod.request_lines(chip, consumer="pigeonvision-spi",
                                          config={pin: gpiod.LineSettings(direction=Direction.INPUT)})

    def get(self) -> bool:
        return self.request.get_value(self.pin) == self.active

    def close(self):
        self.request.release()


@contextmanager
def hardware(args):
    if not sys.platform.startswith("linux"):
        raise RuntimeError("PV-SPI hardware requires Linux on the CM5")
    try:
        import spidev
        import gpiod
    except ImportError as exc:
        raise RuntimeError("Install pigeonvision[spi] on the CM5 for spidev and libgpiod v2") from exc
    if not hasattr(gpiod, "request_lines"):
        raise RuntimeError("PV-SPI requires libgpiod Python v2; no GPIO chip fallback is used")
    try:
        buffer_size = int(Path("/sys/module/spidev/parameters/bufsiz").read_text().strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Cannot verify spidev bufsiz; require at least 1332 bytes for one transfer") from exc
    if buffer_size < MESSAGE_BYTES:
        raise RuntimeError(f"spidev bufsiz is {buffer_size}; require at least {MESSAGE_BYTES} bytes")
    with ExitStack() as cleanup:
        spi = spidev.SpiDev()
        cleanup.callback(spi.close)
        spi.open(args.bus, args.cs)
        spi.mode, spi.bits_per_word, spi.max_speed_hz = 0, 8, int(args.hz)
        spi.lsbfirst, spi.cshigh, spi.no_cs = False, False, False
        ready = Ready(gpiod, args.ready, args.gpiochip)
        cleanup.callback(ready.close)
        yield spi, ready


def add_arguments(parser: argparse.ArgumentParser) -> None:
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--vector", action="store_true", help="Print the protocol vector without opening hardware")
    sources.add_argument("--pattern", action="store_true", help="Send deterministic Pico self-test packets")
    sources.add_argument("--file", type=Path, help="Send a complete MPEG-TS file")
    sources.add_argument("--udp", metavar="IPv4:PORT", help="Listen on an explicit local IPv4 address, or join a multicast group")
    parser.add_argument("--mirror-udp", metavar="IPv4:PORT", help="Also send each completed TS payload to this UDP destination")
    parser.add_argument("--interface", default="0.0.0.0", help="Local IPv4 interface for multicast membership")
    parser.add_argument("--count", type=int, help="Stop after this many messages (pattern default: 10000)")
    parser.add_argument("--duration", type=float, help="Stop after this many seconds, including READY/source waits")
    parser.add_argument("--hz", type=float, default=1e6, help="SPI SCK in Hz; bring-up 1e6, service 20e6")
    parser.add_argument("--bus", type=int, default=0)
    parser.add_argument("--cs", type=int, default=0)
    parser.add_argument("--ready", type=int, default=25, help="READY GPIO line offset on RP1 (default 25)")
    parser.add_argument("--gpiochip", help="Explicit GPIO chip path; default auto-discovers one RP1 chip")
    parser.add_argument("--ready-timeout", type=float, default=1., help="Fail if READY stays low this many seconds")
    parser.add_argument("--idle-timeout", type=float, default=2., help="Fail after this many seconds without UDP input")
    parser.add_argument("--poll-us", type=float, default=20., help="READY polling sleep, in microseconds")
    parser.add_argument("--queue-messages", type=int, default=256, help="Bounded UDP receive queue capacity")
    parser.add_argument("--rcvbuf", type=int, default=4 << 20, help="Requested UDP socket receive buffer (at least 1 MiB)")
    parser.add_argument("--summary", type=Path, help="Also write JSON to a new file; existing files are never overwritten")


def _validate_args(args):
    if not math.isfinite(args.hz) or not 1 <= args.hz <= 20_000_000 or args.hz != int(args.hz):
        raise ValueError("--hz must be an integer frequency from 1 through 20000000 Hz")
    for field in ("ready_timeout", "idle_timeout", "poll_us"):
        if not math.isfinite(getattr(args, field)) or getattr(args, field) <= 0:
            raise ValueError(f"--{field.replace('_', '-')} must be finite and positive")
    if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
        raise ValueError("--duration must be finite and positive")
    if args.count is not None and args.count <= 0:
        raise ValueError("--count must be positive")
    if min(args.bus, args.cs, args.ready) < 0:
        raise ValueError("SPI bus, CS and READY line must be nonnegative")
    if not 1 <= args.queue_messages <= 65536:
        raise ValueError("--queue-messages must be from 1 through 65536")
    if args.rcvbuf < 1 << 20:
        raise ValueError("--rcvbuf must be at least 1048576 bytes")
    if args.udp:
        endpoint(args.udp)
    ipaddress.IPv4Address(args.interface)
    if args.mirror_udp:
        endpoint(args.mirror_udp)
    if args.pattern and args.count is None and args.duration is None:
        args.count = 10000


@contextmanager
def _signals(control):
    originals = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            originals[signum] = signal.getsignal(signum)
            signal.signal(signum, lambda number, frame: control.stop(signal.Signals(number).name.lower(), 128 + number))
    try:
        yield
    finally:
        for signum, handler in originals.items():
            signal.signal(signum, handler)


def run_from_args(args) -> int:
    """CLI dispatch shared by `pv spi` and `python -m ...spi_transport`."""
    sender, source, control = None, None, Control()
    code, reason, error, summary_file = 0, "eof", None, None
    try:
        _validate_args(args)
        if args.vector:
            frame = vector()
            print(frame.hex(" "))
            print(f"crc32 = 0x{struct.unpack('<I', frame[-4:])[0]:08x}", file=sys.stderr)
            return 0
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            summary_file = args.summary.open("x", encoding="utf-8")
        control = Control(args.duration)
        with _signals(control), ExitStack() as cleanup:
            spi, ready = cleanup.enter_context(hardware(args))
            mirror = None
            if args.mirror_udp:
                mirror = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                cleanup.callback(mirror.close)
                mirror.settimeout(.2)
                mirror.connect(endpoint(args.mirror_udp))
            if args.udp:
                source = UdpSource(args.udp, control, interface=args.interface,
                                   queue_messages=args.queue_messages, rcvbuf=args.rcvbuf,
                                   idle_timeout=args.idle_timeout)
                cleanup.callback(source.close)
                payloads = source
            elif args.file:
                payloads = file_payloads(args.file)
                cleanup.callback(payloads.close)
            else:
                payloads = pattern_payloads()
            sender = Sender(spi, ready, hz=int(args.hz), ready_timeout=args.ready_timeout,
                            poll_us=args.poll_us, control=control,
                            source_check=source.check if source else None, mirror=mirror)
            reason = sender.run(payloads, args.count)
            if source:
                # Stop/join before checking the terminal worker error and counts.
                source.close()
                source.check()
    except Stopped as exc:
        reason, code = exc.reason, exc.exit_code
    except KeyboardInterrupt:
        reason, code = "sigint", 130
    except (OSError, ValueError, RuntimeError) as exc:
        reason, code, error = "error", 2, str(exc)
        print(f"pv spi: {exc}", file=sys.stderr)
    finally:
        if not args.vector:
            if source is not None and source.error is not None and error is None:
                reason, code, error = "error", 2, str(source.error)
                print(f"pv spi: {source.error}", file=sys.stderr)
            elapsed = max(0., control.clock() - control.started)
            stats = asdict(sender.stats if sender else SendStats())
            stats["crc_chain"] = f"0x{stats['crc_chain']:08x}"
            result = {"protocol": "PV-SPI v1", "status": "error" if error else "stopped" if code else "complete",
                      "stop_reason": reason, "error": error, "seconds": round(elapsed, 6),
                      "sck_hz": args.hz, **stats,
                      "msg_per_s": round(stats["messages"] / elapsed, 3) if elapsed else 0.,
                      "ts_mbps": round(stats["payload_bytes"] * 8 / elapsed / 1e6, 6) if elapsed else 0.,
                      "receiver_acceptance": "unverified: compare Pico counters and crc_chain",
                      "udp": asdict(source.stats) if source else None}
            rendered = json.dumps(result, sort_keys=True) + "\n"
            print(rendered, end="")
            if summary_file is not None:
                try:
                    summary_file.write(rendered)
                except OSError as exc:
                    code = 2
                    print(f"pv spi: cannot write summary: {exc}", file=sys.stderr)
                finally:
                    summary_file.close()
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run_from_args(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())

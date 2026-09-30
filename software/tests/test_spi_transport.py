"""Protocol and lifecycle checks without touching SPI/GPIO devices."""
from contextlib import contextmanager
import json
from pathlib import Path
import queue
import signal
import socket
import struct
import sys
from types import SimpleNamespace
import zlib

import pytest

from pigeonvision import spi_transport as tx


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        assert 0 < seconds <= .05
        self.now += seconds


class Spi:
    def __init__(self, clock=None, fail=None):
        self.clock, self.fail, self.calls = clock, fail, []

    def xfer2(self, data, hz, delay, bits):
        self.calls.append((bytes(data), hz, delay, bits, self.clock() if self.clock else None))
        if self.fail:
            raise self.fail
        if self.clock:
            self.clock.now += len(data) * 8 / hz
        return [0] * len(data)


class Ready:
    def __init__(self, clock=None, high_at=0.):
        self.clock, self.high_at, self.reads = clock, high_at, []

    def get(self):
        now = self.clock() if self.clock else 0.
        self.reads.append(now)
        return now >= self.high_at


def sender(**kwargs):
    clock = Clock()
    spi, ready = Spi(clock), Ready(clock, kwargs.pop("high_at", 0.))
    result = tx.Sender(spi, ready, clock=clock, sleep=clock.sleep, **kwargs)
    return result, spi, ready, clock


def test_spec_vector_exact_header_padding_crc_and_ieee_check():
    frame = tx.vector()
    assert len(frame) == 1332
    assert frame[:16] == bytes.fromhex("50 56 01 01 01 00 bc 00 00 00 00 00 47 01 00 10")
    assert frame[16:200] == bytes(range(184))
    assert frame[200:1328] == bytes(1128)
    assert frame[-4:] == bytes.fromhex("8d 32 be 51")
    assert zlib.crc32(b"123456789") == 0xcbf43926
    assert struct.unpack("<I", frame[-4:])[0] == zlib.crc32(frame[:-4])


@pytest.mark.parametrize("n", range(1, 8))
def test_all_legal_payload_lengths_and_zero_reserved(n):
    payload = b"".join(tx.pattern_packet(k) for k in range(n))
    frame = tx.message(65537, payload)
    assert struct.unpack("<HBBHHI", frame[:12]) == (0x5650, 1, 1, 1, n * 188, 0)
    assert frame[12:12 + len(payload)] == payload
    assert frame[12 + len(payload):-4] == bytes(1316 - len(payload))
    assert len(frame) == 1332


@pytest.mark.parametrize("payload", [b"", b"x", bytes(188), tx.pattern_payload(0) + tx.pattern_packet(0), tx.pattern_packet(0) + b"x"])
def test_invalid_input_never_forms_a_ts_message(payload):
    with pytest.raises(ValueError):
        tx.message(0, payload)


def test_nop_empty_and_types_strict():
    frame = tx.message(65535, b"", tx.NOP)
    assert frame[3] == 0 and frame[6:8] == b"\0\0" and len(frame) == 1332
    for payload, kind in ((tx.pattern_packet(0), tx.NOP), (b"", 2)):
        with pytest.raises(ValueError):
            tx.message(0, payload, kind)


def test_pattern_matches_firmware_period_cc_and_packet_payload():
    assert tx.pattern_payload(0) == tx.pattern_payload(16)
    assert tx.pattern_payload(15)[0:4] == bytes((0x47, 1, 0, 0x19))
    for k in (0, 15, 111, 112, 1000):
        packet = tx.pattern_packet(k)
        assert packet[:4] == bytes((0x47, 1, 0, 0x10 | (k % 16)))
        assert packet[4:] == bytes(((k % 112) + j) % 256 for j in range(184))


def test_single_transfer_cs_hold_before_ready_seq_wrap_and_chain():
    send, spi, ready, clock = sender(hz=20_000_000)
    send.stats.next_seq = 65535
    send.send(tx.pattern_packet(0))
    first_return = clock()
    send.send(tx.pattern_payload(1))
    assert len(spi.calls) == 2
    assert all(len(call[0]) == 1332 and call[1:4] == (20_000_000, 0, 8) for call in spi.calls)
    assert ready.reads[0] >= tx.CS_HIGH_SECONDS
    assert ready.reads[1] >= first_return + tx.CS_HIGH_SECONDS
    assert [struct.unpack_from("<H", call[0], 4)[0] for call in spi.calls] == [65535, 0]
    chain = 0
    for frame, *_ in spi.calls:
        chain = zlib.crc32(frame[-4:], chain)
    assert send.stats.crc_chain == chain
    assert send.stats.messages == 2 and send.stats.ts_packets == 8
    assert send.stats.payload_bytes == 1504 and send.stats.next_seq == 1


def test_ready_wait_warning_and_timeout_count_without_any_spi_write():
    warnings = []
    send, spi, ready, clock = sender(high_at=10., ready_timeout=.025, poll_us=1000, warning=warnings.append)
    with pytest.raises(TimeoutError, match="READY stayed low"):
        send.send(tx.pattern_packet(0))
    assert not spi.calls
    assert send.stats.ready_waits == 1 and send.stats.ready_timeouts == 1
    assert send.stats.ready_waits_over_20ms == 1 and len(warnings) == 1
    assert send.stats.max_ready_wait_ms == pytest.approx(25.)
    assert send.stats.pending_payload_bytes == 188


def test_ready_recovers_then_data_is_written_once():
    send, spi, ready, clock = sender(high_at=.001)
    send.send(tx.pattern_payload(0))
    assert len(spi.calls) == 1 and send.stats.ready_waits == 1
    assert send.stats.max_ready_wait_ms >= .99


def test_duration_cancels_ready_wait_even_with_large_poll_interval():
    clock = Clock()
    control = tx.Control(.005, clock=clock)
    send = tx.Sender(Spi(clock), Ready(clock, 10.), control=control, clock=clock,
                     sleep=clock.sleep, poll_us=1e9, ready_timeout=10.)
    with pytest.raises(tx.Stopped, match="duration"):
        send.send(tx.pattern_packet(0))
    assert clock() == pytest.approx(.005)
    assert not send.spi.calls and send.stats.ready_timeouts == 0


def test_spi_failure_marks_uncertain_and_never_retries_or_updates_chain():
    send, spi, *_ = sender()
    spi.fail = OSError("mock ioctl failed")
    with pytest.raises(OSError, match="mock ioctl"):
        send.run([tx.pattern_packet(0), tx.pattern_packet(1)])
    assert len(spi.calls) == 1
    assert send.stats.attempted_messages == 1 and send.stats.messages == 0
    assert send.stats.transfer_uncertain and send.stats.crc_chain == 0


def test_source_error_during_ready_prevents_write():
    def fail():
        raise RuntimeError("UDP overflow")
    send, spi, *_ = sender(source_check=fail)
    with pytest.raises(RuntimeError, match="UDP overflow"):
        send.send(tx.pattern_packet(0))
    assert not spi.calls


def test_file_tail_is_not_silently_truncated(tmp_path):
    file = tmp_path / "input.ts"
    file.write_bytes(tx.pattern_payload(0) + tx.pattern_packet(7))
    assert list(tx.file_payloads(file)) == [tx.pattern_payload(0), tx.pattern_packet(7)]
    file.write_bytes(tx.pattern_payload(0) + b"incomplete")
    source = tx.file_payloads(file)
    assert next(source) == tx.pattern_payload(0)
    with pytest.raises(ValueError, match="whole 188-byte"):
        next(source)


class ThreadStub:
    def __init__(self, **kwargs):
        pass
    def start(self):
        pass
    def join(self, timeout):
        pass
    def is_alive(self):
        return False


class UdpSocket:
    def __init__(self, incoming):
        self.incoming = iter(incoming)
        self.options, self.bound, self.closed = [], None, False
    def setsockopt(self, *args):
        self.options.append(args)
    def getsockopt(self, *args):
        return 8 << 20
    def settimeout(self, seconds):
        self.timeout = seconds
    def bind(self, address):
        self.bound = address
    def recvmsg(self, size, ancillary_size):
        assert size == 65535 and ancillary_size >= 4
        item = next(self.incoming)
        if isinstance(item, Exception):
            raise item
        return item
    def close(self):
        self.closed = True


def udp_source(monkeypatch, incoming, **kwargs):
    monkeypatch.setattr(tx.threading, "Thread", ThreadStub)
    sock = UdpSocket(incoming)
    source = tx.UdpSource("127.0.0.1:1234", tx.Control(), socket_factory=lambda *args: sock,
                          overflow_option=40, **kwargs)
    return source, sock


def datagram(payload, ancillary=(), flags=0):
    return payload, ancillary, flags, ("127.0.0.1", 9999)


def test_udp_unicast_bind_bounded_queue_and_overflow_fails(monkeypatch):
    source, sock = udp_source(monkeypatch, [datagram(tx.pattern_packet(k)) for k in range(2)], queue_messages=1)
    source._receive()
    with pytest.raises(RuntimeError, match="software queue overflow"):
        source.check()
    assert sock.bound == ("127.0.0.1", 1234)
    assert source.queue.qsize() == 1
    assert source.stats.datagrams_received == 2 and source.stats.queue_overflows == 1
    source.close()
    assert sock.closed and source.stats.buffered_unsent == 1
    assert source.stats.kernel_drop_monitor


def test_udp_kernel_drop_is_reported_before_writing(monkeypatch):
    ancillary = [(socket.SOL_SOCKET, 40, struct.pack("=I", 3))]
    source, _ = udp_source(monkeypatch, [datagram(tx.pattern_packet(0), ancillary)])
    source._receive()
    with pytest.raises(RuntimeError, match="dropped 3"):
        source.check()
    assert source.stats.kernel_drops == 3 and source.queue.empty()
    source.close()


def test_udp_effective_buffer_cap_is_explicit(monkeypatch, capsys):
    monkeypatch.setattr(UdpSocket, "getsockopt", lambda self, *args: 425984)
    source, _ = udp_source(monkeypatch, [])
    assert source.stats.receive_buffer_requested_bytes == 4 << 20
    assert source.stats.receive_buffer_bytes == 425984 and source.stats.receive_buffer_below_spec
    assert "net.core.rmem_max >= 1048576" in capsys.readouterr().err
    source.close()


@pytest.mark.parametrize("payload,flags", [(b"", 0), (tx.pattern_packet(0) + b"x", 0),
                                            (tx.pattern_payload(0) * 2, 0),
                                            (tx.pattern_packet(0), socket.MSG_TRUNC),
                                            (tx.pattern_packet(0), socket.MSG_CTRUNC)])
def test_udp_invalid_or_truncated_datagram_stops_without_trimming(monkeypatch, payload, flags):
    source, _ = udp_source(monkeypatch, [datagram(payload, flags=flags)])
    source._receive()
    with pytest.raises(ValueError):
        source.check()
    assert source.stats.invalid_datagrams == 1 and source.queue.empty()
    source.close()


def test_udp_idle_timeout_is_reported(monkeypatch):
    clock = Clock()
    source, sock = udp_source(monkeypatch, [], idle_timeout=.01)
    source.control = tx.Control(clock=clock)
    def timeout(*args):
        clock.now += .05
        raise socket.timeout()
    sock.recvmsg = timeout
    source._receive()
    with pytest.raises(TimeoutError, match="No UDP"):
        source.check()
    source.close()


def test_mirror_failure_retains_spi_completed_chain():
    mirror = SimpleNamespace(send=lambda data: 0)
    send, spi, *_ = sender(mirror=mirror)
    with pytest.raises(OSError, match="short UDP mirror"):
        send.send(tx.pattern_packet(0))
    assert send.stats.messages == 1 and send.stats.mirror_errors == 1
    assert send.stats.mirror_datagrams == 0
    assert send.stats.crc_chain == zlib.crc32(spi.calls[0][0][-4:])


def test_cli_vector_has_no_hardware_imports(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "spidev", None)
    monkeypatch.setitem(sys.modules, "gpiod", None)
    assert tx.main(["--vector"]) == 0
    output = capsys.readouterr()
    assert bytes.fromhex(output.out) == tx.vector()
    assert "0x51be328d" in output.err


@pytest.mark.parametrize("args", [["--hz", "nan"], ["--hz", "20000001"], ["--duration", "0"],
                                  ["--ready-timeout", "inf"], ["--count", "0"],
                                  ["--poll-us", "-1"], ["--rcvbuf", "1024"],
                                  ["--queue-messages", "0"], ["--mirror-udp", "bad:1"]])
def test_cli_rejects_invalid_configuration_before_hardware(args, monkeypatch, capsys):
    def no_hardware(*args):
        pytest.fail("invalid settings opened hardware")
    monkeypatch.setattr(tx, "hardware", no_hardware)
    assert tx.main(["--pattern", *args]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_pattern_duration_has_no_implicit_count():
    parser = argparse_parser()
    args = parser.parse_args(["--pattern", "--duration", "60"])
    tx._validate_args(args)
    assert args.count is None
    args = parser.parse_args(["--pattern"])
    tx._validate_args(args)
    assert args.count == 10000


def argparse_parser():
    import argparse
    parser = argparse.ArgumentParser()
    tx.add_arguments(parser)
    return parser


@contextmanager
def mock_hardware(args):
    yield Spi(), Ready()


def test_cli_summary_counts_short_file_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tx, "hardware", mock_hardware)
    file, summary = tmp_path / "in.ts", tmp_path / "out" / "summary.json"
    file.write_bytes(tx.pattern_payload(0) + tx.pattern_packet(7))
    assert tx.main(["--file", str(file), "--summary", str(summary)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["messages"] == 2 and result["ts_packets"] == 8
    assert result["payload_bytes"] == 1504 and result["stop_reason"] == "eof"
    assert json.loads(summary.read_text()) == result
    assert tx.main(["--pattern", "--count", "1", "--summary", str(summary)]) == 2
    capsys.readouterr()
    assert json.loads(summary.read_text()) == result


def test_cli_signal_stops_and_restores_handlers(monkeypatch, capsys):
    monkeypatch.setattr(tx, "hardware", mock_hardware)
    original = signal.getsignal(signal.SIGTERM)
    def interrupted(self, payloads, count):
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        self.control.check()
    monkeypatch.setattr(tx.Sender, "run", interrupted)
    assert tx.main(["--pattern", "--count", "1"]) == 143
    assert signal.getsignal(signal.SIGTERM) is original
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "stopped" and result["stop_reason"] == "sigterm"


def test_terminal_worker_error_overrides_normal_duration_stop(monkeypatch, capsys):
    monkeypatch.setattr(tx, "hardware", mock_hardware)
    class Source:
        def __init__(self, address, control, **kwargs):
            self.error, self.stats = RuntimeError("late UDP overflow"), tx.UdpStats(queue_overflows=1)
        def check(self):
            pass
        def __iter__(self):
            raise tx.Stopped("duration")
        def close(self):
            pass
    monkeypatch.setattr(tx, "UdpSource", Source)
    assert tx.main(["--udp", "127.0.0.1:1234", "--duration", "1"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "error" and result["error"] == "late UDP overflow"


def gpiod_mock(monkeypatch, tmp_path, label="pinctrl-rp1", name="GPIO25"):
    active, direction = object(), object()
    monkeypatch.setitem(sys.modules, "gpiod.line", SimpleNamespace(Direction=SimpleNamespace(INPUT=direction), Value=SimpleNamespace(ACTIVE=active)))
    class Chip:
        def __init__(self, path):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get_info(self):
            return SimpleNamespace(label=label, num_lines=54)
        def get_line_info(self, offset):
            return SimpleNamespace(name=name)
    requests = []
    def request_lines(path, **kwargs):
        requests.append((path, kwargs))
        return SimpleNamespace(get_value=lambda pin: active, release=lambda: None)
    return SimpleNamespace(Chip=Chip, request_lines=request_lines, LineSettings=lambda **kwargs: kwargs), requests


def test_gpio_auto_discovery_deduplicates_cm5_symlink(monkeypatch, tmp_path):
    device = tmp_path / "gpiochip0"
    device.touch()
    alias = tmp_path / "gpiochip4"
    alias.symlink_to(device)
    monkeypatch.setattr(Path, "glob", lambda self, pattern: iter((device, alias)))
    gpiod, requests = gpiod_mock(monkeypatch, tmp_path)
    ready = tx.Ready(gpiod, 25, None)
    assert requests[0][0] == str(device.resolve()) and ready.get()
    ready.close()


@pytest.mark.parametrize("label,name", [("pinctrl-bcm2712", "GPIO25"), ("pinctrl-rp1", "OTHER")])
def test_explicit_gpiochip_must_map_to_rp1_gpio(monkeypatch, tmp_path, label, name):
    gpiod, requests = gpiod_mock(monkeypatch, tmp_path, label, name)
    with pytest.raises(RuntimeError):
        tx.Ready(gpiod, 25, "/dev/gpiochip4")
    assert not requests


def test_hardware_preflight_buffer_and_explicit_spi_settings(monkeypatch):
    monkeypatch.setattr(tx.sys, "platform", "linux")
    spi = Spi()
    spi.closed = False
    spi.open = lambda bus, cs: setattr(spi, "opened", (bus, cs))
    spi.close = lambda: setattr(spi, "closed", True)
    monkeypatch.setitem(sys.modules, "spidev", SimpleNamespace(SpiDev=lambda: spi))
    monkeypatch.setitem(sys.modules, "gpiod", SimpleNamespace(request_lines=True))
    monkeypatch.setattr(tx, "Ready", lambda *args: SimpleNamespace(close=lambda: None))
    args = argparse_parser().parse_args(["--pattern"])
    monkeypatch.setattr(Path, "read_text", lambda self: "1024\n")
    with pytest.raises(RuntimeError, match="require at least 1332"):
        with tx.hardware(args):
            pytest.fail("undersized buffer opened hardware")
    monkeypatch.setattr(Path, "read_text", lambda self: "4096\n")
    with tx.hardware(args) as (opened, ready):
        assert opened is spi and spi.opened == (0, 0)
        assert (spi.mode, spi.bits_per_word, spi.max_speed_hz) == (0, 8, 1_000_000)
        assert (spi.lsbfirst, spi.cshigh, spi.no_cs) == (False, False, False)
    assert spi.closed

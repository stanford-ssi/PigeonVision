"""Receive-side recording preserves bytes while keeping live reads independent."""
from collections import deque
from fractions import Fraction
import hashlib
import json
import threading
import time

import av
import pytest

from pigeonvision.ground.recording import TransportRecorder
from pigeonvision.ground.server import Receiver
from pigeonvision.ground.transport import unpack_message
from pigeonvision.ground.ts_input import TsInput
from test_ground_transport import transport  # Shared generated dual-camera fixture.


class FakeSocket:
    def __init__(self, datagrams):
        self.datagrams = deque(datagrams)
        self.closed = False

    def setsockopt(self, *args):
        pass

    def settimeout(self, timeout):
        pass

    def bind(self, address):
        pass

    def recv(self, size):
        if not self.datagrams:
            raise TimeoutError("end of scripted datagrams")
        return self.datagrams.popleft()

    def close(self):
        self.closed = True


def test_udp_tap_is_byte_exact_across_short_reads_and_reconnects(tmp_path, monkeypatch):
    datagrams = [b"\x47" + bytes(range(187)), b"second-datagram", b"third-datagram"]
    sockets = deque([FakeSocket(datagrams[:2]), FakeSocket(datagrams[2:])])
    monkeypatch.setattr("pigeonvision.ground.ts_input.socket.socket", lambda *args: sockets.popleft())
    target = tmp_path / "received.ts"
    recorder = TransportRecorder(target)
    try:
        with TsInput("udp://127.0.0.1:5000", threading.Event(), recorder) as source:
            assert source.read(0) == b""
            assert source.read(1) + source.read(7) + source.read(180) == datagrams[0]
            assert source.read(1000) == datagrams[1]
        with TsInput("udp://127.0.0.1:5000", threading.Event(), recorder) as source:
            assert source.read(2) + source.read(1000) == datagrams[2]
    finally:
        recorder.close()
    expected = b"".join(datagrams)
    assert target.read_bytes() == expected
    evidence = json.loads(recorder.evidence_path.read_text())
    assert evidence["complete"] is True
    assert evidence["received_datagrams"] == 3
    assert evidence["written_bytes"] == len(expected)
    assert evidence["sha256"] == hashlib.sha256(expected).hexdigest()


@pytest.mark.parametrize("existing", ["transport", "evidence"])
def test_recording_never_overwrites_existing_artifacts(tmp_path, existing):
    target = tmp_path / "received.ts"
    occupied = target if existing == "transport" else target.with_name(target.name + ".recording.json")
    occupied.write_bytes(b"existing evidence")
    with pytest.raises(FileExistsError):
        TransportRecorder(target)
    assert occupied.read_bytes() == b"existing evidence"
    if existing == "evidence":
        assert not target.exists()


def test_queue_overflow_disables_tap_but_live_reads_continue(tmp_path, monkeypatch):
    first_write = threading.Event()
    release = threading.Event()
    target = tmp_path / "overflow.ts"
    errors = []
    recorder = TransportRecorder(target, queue_size=1, on_error=errors.append)
    original_write = recorder._write
    def blocked_write(data):
        first_write.set()
        assert release.wait(3)
        original_write(data)
    recorder._write = blocked_write
    datagrams = [b"first", b"second", b"lost", b"still-live"]
    monkeypatch.setattr("pigeonvision.ground.ts_input.socket.socket", lambda *args: FakeSocket(datagrams))
    try:
        with TsInput("udp://127.0.0.1:5000", threading.Event(), recorder) as source:
            assert source.read(65535) == datagrams[0]
            assert first_write.wait(3)
            assert source.read(65535) == datagrams[1]
            assert source.read(65535) == datagrams[2]
            assert source.read(65535) == datagrams[3]
        assert recorder.status()["state"] == "failed"
        assert len(errors) == 1
    finally:
        release.set()
        recorder.close()
    assert target.read_bytes() == b"firstsecond"
    evidence = json.loads(recorder.evidence_path.read_text())
    assert evidence["complete"] is False
    assert evidence["dropped_datagrams"] == 2
    assert "queue full" in evidence["error"]


def test_disk_error_is_reported_without_interrupting_live_input(tmp_path, monkeypatch):
    target = tmp_path / "disk-error.ts"
    receiver = Receiver("udp://127.0.0.1:5000", record_transport=str(target))
    failed = threading.Event()
    def fail_write(data):
        failed.set()
        raise OSError("simulated disk full")
    receiver.recorder._write = fail_write
    monkeypatch.setattr("pigeonvision.ground.ts_input.socket.socket", lambda *args: FakeSocket([b"before", b"after"]))
    try:
        with TsInput(receiver.source, receiver.stop_event, receiver.recorder) as source:
            assert source.read(65535) == b"before"
            assert failed.wait(3)
            deadline = time.monotonic() + 3
            while receiver.recorder.status()["state"] != "failed" and time.monotonic() < deadline:
                time.sleep(.005)
            assert source.read(65535) == b"after"
        assert "simulated disk full" in receiver.status()["transport_recording"]["error"]
        error = receiver.messages.get(timeout=3)
        assert error["component"] == "transport_recording"
        assert "Live reception continues" in error["message"]
    finally:
        receiver.stop()
    assert json.loads(receiver.recorder.evidence_path.read_text())["complete"] is False


def test_short_writes_and_fsync_failure_are_not_reported_complete(tmp_path, monkeypatch):
    recorder = TransportRecorder(tmp_path / "fsync-error.ts")
    real_file = recorder._file
    class ShortFile:
        def write(self, data):
            return real_file.write(data[:3])
        def flush(self):
            real_file.flush()
        def fileno(self):
            return real_file.fileno()
        def close(self):
            real_file.close()
    recorder._file = ShortFile()
    import pigeonvision.ground.recording as recording
    real_fsync = recording.os.fsync
    transport_fd = real_file.fileno()
    def fail_transport_fsync(fd):
        if fd == transport_fd:
            raise OSError("simulated finalization failure")
        real_fsync(fd)
    monkeypatch.setattr(recording.os, "fsync", fail_transport_fsync)
    assert recorder.submit(b"complete payload despite short writes")
    recorder.close()
    assert recorder.path.read_bytes() == b"complete payload despite short writes"
    evidence = json.loads(recorder.evidence_path.read_text())
    assert evidence["complete"] is False
    assert "finalization failure" in evidence["error"]


def test_recorded_transport_replays_both_cameras_with_private_timing_metadata(transport, tmp_path, monkeypatch):
    source_path = tmp_path / "with-private-metadata.ts"
    with av.open(str(transport)) as source, av.open(str(source_path), "w", format="mpegts",
            options={"mpegts_start_pid": "256", "mpegts_copyts": "1"}) as output:
        streams = {stream.index: output.add_stream_from_template(stream) for stream in source.streams.video}
        metadata = output.add_data_stream("bin_data")
        metadata.time_base = Fraction(1, 90000)
        packet = av.Packet(json.dumps({"schema_version": 1, "type": "session", "transport_pts_offset_us": 1_000_000}).encode())
        packet.stream = metadata
        packet.pts = packet.dts = 360000
        packet.time_base = Fraction(1, 90000)
        output.mux(packet)
        for packet in source.demux():
            if packet.size and packet.stream.index in streams:
                packet.stream = streams[packet.stream.index]
                output.mux(packet)
    data = source_path.read_bytes()
    datagrams = [data[i:i+1316] for i in range(0, len(data), 1316)]
    monkeypatch.setattr("pigeonvision.ground.ts_input.socket.socket", lambda *args: FakeSocket(datagrams))
    recorder = TransportRecorder(tmp_path / "saved.ts")
    with TsInput("udp://127.0.0.1:5000", threading.Event(), recorder) as source:
        for datagram in datagrams:
            assert source.read(65535) == datagram
    recorder.close()
    assert recorder.path.read_bytes() == data
    receiver = Receiver(str(recorder.path))
    receiver.start()
    receiver.control("play")
    units = {"A": [], "B": []}
    metadata_seen = False
    try:
        while True:
            message = receiver.messages.get(timeout=3)
            if isinstance(message, bytes):
                header, _ = unpack_message(message)
                units[header["camera_id"]].append(header["timestamp_us"])
            elif message.get("type") == "metadata":
                metadata_seen = True
            elif message.get("state") == "ended":
                break
            elif message.get("type") == "error":
                pytest.fail(message["message"])
        assert metadata_seen
        assert [len(units[name]) for name in ("A", "B")] == [12, 12]
        assert units["A"][0] == 4_000_000
        assert units["B"][0] - units["A"][0] == 10_000
    finally:
        receiver.stop()


def test_recording_rejects_replay_sources_before_creating_output(transport, tmp_path):
    target = tmp_path / "must-not-exist.ts"
    with pytest.raises(ValueError, match="live UDP"):
        Receiver(str(transport), record_transport=str(target))
    assert not target.exists()


def test_evidence_fsync_failure_marks_recording_failed(tmp_path, monkeypatch):
    recorder = TransportRecorder(tmp_path / "evidence-error.ts")
    import pigeonvision.ground.recording as recording
    original_fsync = recording.os.fsync
    evidence_fd = recorder._evidence.fileno()
    def fail_evidence_sync(fd):
        if fd == evidence_fd:
            raise OSError("simulated evidence fsync failure")
        original_fsync(fd)
    monkeypatch.setattr(recording.os, "fsync", fail_evidence_sync)
    recorder.submit(b"received bytes")
    recorder.close()
    assert recorder.status()["state"] == "failed"
    assert "evidence fsync failure" in recorder.status()["error"]
    assert json.loads(recorder.evidence_path.read_text())["complete"] is False

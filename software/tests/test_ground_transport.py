"""Generated media verifies real demux and browser payloads without a camera."""
import asyncio
from fractions import Fraction
import json
import queue
import socket
import threading
import time

import av
from aiohttp import ClientSession, WSMsgType, web
import numpy as np
import pytest

from pigeonvision.ground.server import CLIENTS, RECEIVER, Receiver, create_app
from pigeonvision.ground.transport import (
    H264Normalizer, START_CODE, annexb_nals, pts_microseconds, unpack_message,
)
from pigeonvision.ground.ts_input import ContinuityAudit


@pytest.fixture
def transport(tmp_path):
    path = tmp_path / "two-cameras.ts"
    with av.open(str(path), "w", format="mpegts", options={"mpegts_start_pid": "256", "mpegts_copyts": "1"}) as output:
        streams = []
        for _ in range(2):
            stream = output.add_stream("libx264", rate=30)
            stream.width = stream.height = 64
            stream.pix_fmt = "yuv420p"
            stream.codec_context.time_base = Fraction(1, 90000)
            stream.codec_context.options = {"preset": "ultrafast", "tune": "zerolatency",
                                             "x264-params": "keyint=5:min-keyint=5:scenecut=0:bframes=0:repeat-headers=1"}
            streams.append(stream)
        for index in range(12):
            for camera, stream in enumerate(streams):
                pixels = np.zeros((64, 64, 3), dtype=np.uint8)
                pixels[:, :, camera] = 100 + index * 10
                frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
                frame.pts = 450000 + index * 3000 + camera * 900
                frame.time_base = Fraction(1, 90000)
                for packet in stream.encode(frame):
                    output.mux(packet)
        for stream in streams:
            for packet in stream.encode():
                output.mux(packet)
    return path


def test_h264_recovery_and_envelope():
    sps, pps, idr, delta = b"\x67\x64\x00\x32\x80", b"\x68\xc0", b"\x65\xb8", b"\x41\xb8"
    normalizer = H264Normalizer(START_CODE + sps + START_CODE + pps)
    assert normalizer.normalize(START_CODE + delta, "A", 99) is None
    unit = normalizer.normalize(START_CODE + idr, "A", 1_234_567)
    header, data = unpack_message(unit.encode())
    assert header == {"type": "frame", "camera_id": "A", "timestamp_us": 1_234_567,
                      "keyframe": True, "codec": "avc1.640032"}
    assert annexb_nals(data) == [sps, pps, idr]
    assert normalizer.normalize(START_CODE + delta, "A", 1_267_900).keyframe is False
    assert normalizer.normalize(START_CODE + delta, "A", 1_301_233, corrupt=True) is None
    assert normalizer.normalize(START_CODE + delta, "A", 1_334_566) is None
    assert normalizer.normalize(START_CODE + idr, "A", 1_367_899).keyframe is True


def test_avcc_and_integer_timestamps():
    sps, pps, idr = b"\x67\x64\x00\x32\x80", b"\x68\xc0", b"\x65\xb8"
    extradata = b"\x01\x64\x00\x32\xff\xe1" + len(sps).to_bytes(2, "big") + sps + b"\x01" + len(pps).to_bytes(2, "big") + pps
    unit = H264Normalizer(extradata).normalize(len(idr).to_bytes(4, "big") + idr, "B", -123)
    assert annexb_nals(unit.data) == [sps, pps, idr]
    assert pts_microseconds(450900, Fraction(1, 90000)) == 5_010_000
    assert pts_microseconds(-900, Fraction(1, 90000)) == -10_000
    with pytest.raises(ValueError):
        unpack_message(b"\0\0\0\x20tiny")


def test_sps_reconfiguration_replaces_same_id():
    sps, pps, idr = b"\x67\x64\x00\x32\x80", b"\x68\xc0", b"\x65\xb8"
    newer_sps = b"\x67\x42\x00\x1f\x80"
    normalizer = H264Normalizer(START_CODE + sps + START_CODE + pps)
    assert normalizer.normalize(START_CODE + idr, "A", 0).codec == "avc1.640032"
    unit = normalizer.normalize(START_CODE + newer_sps + START_CODE + idr, "A", 33333)
    assert unit.codec == "avc1.42001f"
    assert len(normalizer.sps) == 1
    assert annexb_nals(unit.data) == [newer_sps, pps, idr]


def test_real_mux_demux_preserves_shared_pts_and_decodes(transport):
    receiver = Receiver(str(transport))
    receiver.start()
    receiver.control("play")
    units = {"A": [], "B": []}
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            message = receiver.messages.get(timeout=3)
            if isinstance(message, bytes):
                header, payload = unpack_message(message)
                units[header["camera_id"]].append((header, payload))
            elif message.get("state") == "ended":
                break
            elif message.get("type") == "error":
                pytest.fail(message["message"])
        assert [len(units[name]) for name in ("A", "B")] == [12, 12]
        assert units["A"][0][0]["timestamp_us"] == 5_000_000
        assert units["B"][0][0]["timestamp_us"] - units["A"][0][0]["timestamp_us"] == 10_000
        for camera_units in units.values():
            decoder = av.CodecContext.create("h264", "r")
            decoded = []
            for header, payload in camera_units:
                if header["keyframe"]:
                    assert {nal[0] & 31 for nal in annexb_nals(payload)} >= {7, 8, 5}
                packet = av.Packet(payload)
                packet.pts = header["timestamp_us"]
                packet.time_base = Fraction(1, 1_000_000)
                decoded.extend(decoder.decode(packet))
            decoded.extend(decoder.decode(None))
            assert len(decoded) == 12
            assert decoded[0].width == 64
    finally:
        receiver.stop()


def test_replay_step_and_missing_camera_are_explicit(transport):
    receiver = Receiver(str(transport))
    receiver.start()
    receiver.control("step")
    try:
        cameras = []
        while len(cameras) < 2:
            message = receiver.messages.get(timeout=3)
            if isinstance(message, bytes):
                cameras.append(unpack_message(message)[0]["camera_id"])
        assert set(cameras) == {"A", "B"}
        time.sleep(.08)
        remaining = []
        while not receiver.messages.empty():
            remaining.append(receiver.messages.get_nowait())
        assert not any(isinstance(message, bytes) for message in remaining)
        assert receiver.playing is False
        assert receiver.steps == 0
        assert receiver.status()["synchronization"] == "Exposure synchronization is unverified"
    finally:
        receiver.stop()


def test_replay_steps_remain_bounded_when_one_camera_stops(transport, tmp_path):
    path = tmp_path / "camera-a-stops.ts"
    with av.open(str(transport)) as source, av.open(str(path), "w", format="mpegts",
            options={"mpegts_start_pid": "256", "mpegts_copyts": "1"}) as output:
        streams = {stream.index: output.add_stream_from_template(stream)
                   for stream in source.streams.video}
        a_packets = 0
        for packet in source.demux():
            if not packet.size or packet.stream.index not in streams:
                continue
            if packet.stream.id == 256:
                a_packets += 1
                if a_packets > 5:
                    continue
            packet.stream = streams[packet.stream.index]
            output.mux(packet)

    receiver = Receiver(str(path))
    receiver.start()
    units = {"A": [], "B": []}
    partial_steps = []
    try:
        for _ in range(17):
            receiver.control("step")
            seen = []
            while True:
                message = receiver.messages.get(timeout=3)
                if isinstance(message, bytes):
                    header, payload = unpack_message(message)
                    seen.append(header["camera_id"])
                    units[header["camera_id"]].append((header, payload))
                elif message.get("type") == "error":
                    pytest.fail(message["message"])
                elif message.get("step_complete"):
                    assert message["step_cameras"] == sorted(seen)
                    assert message["step_missing_cameras"] == sorted({"A", "B"} - set(seen))
                    if message["step_missing_cameras"]:
                        partial_steps.append(message["step_missing_cameras"])
                    break
            assert 1 <= len(seen) <= 2
            assert len(seen) == len(set(seen)), "A step must not drain an unpaired camera"
            assert receiver.steps == 0
            if [len(units[name]) for name in ("A", "B")] == [5, 12]:
                break
        assert [len(units[name]) for name in ("A", "B")] == [5, 12]
        assert ["A"] in partial_steps
        # Holding the next AU at the step boundary must retain all compressed
        # references, including deltas between the surviving camera's IDRs.
        for name, count in (("A", 5), ("B", 12)):
            decoder = av.CodecContext.create("h264", "r")
            decoded = []
            for header, payload in units[name]:
                packet = av.Packet(payload)
                packet.pts = header["timestamp_us"]
                packet.time_base = Fraction(1, 1_000_000)
                decoded.extend(decoder.decode(packet))
            decoded.extend(decoder.decode(None))
            assert len(decoded) == count
    finally:
        receiver.stop()


def test_http_websocket_replay(transport):
    async def scenario():
        app = create_app(str(transport))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/") as response:
                    assert response.status == 200
                    assert "PigeonVision" in await response.text()
                async with client.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                    seen, calibrated = set(), True
                    for _ in range(20):
                        message = await ws.receive(timeout=3)
                        if message.type == WSMsgType.BINARY:
                            seen.add(unpack_message(message.data)[0]["camera_id"])
                        elif message.type == WSMsgType.TEXT:
                            payload = json.loads(message.data)
                            if payload["type"] == "calibration":
                                calibrated = payload["calibration"] is not None
                        if len(seen) == 2:
                            break
                    assert seen == {"A", "B"}
                    assert not calibrated
                    await ws.send_json({"type": "control", "action": "pause"})
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_shutdown_closes_connected_websocket_before_waiting_for_handlers(transport, monkeypatch):
    async def scenario():
        app = create_app(str(transport))
        receiver = app[RECEIVER]
        original_stop = receiver.stop
        stopped = []

        def stop():
            stopped.append(not app[CLIENTS])
            original_stop()

        monkeypatch.setattr(receiver, "stop", stop)
        # Keep aiohttp's ordinary shutdown timeout: shortening it would hide
        # the regression where cleanup_ctx closes sockets only after that wait.
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        try:
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            async with ClientSession() as client:
                async with client.ws_connect(f"http://127.0.0.1:{port}/ws") as ws:
                    assert (await ws.receive_json(timeout=2))["type"] == "status"
                    assert not ws.closed and len(app[CLIENTS]) == 1

                    async def receive_close():
                        while True:
                            message = await ws.receive(timeout=2)
                            if message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                                return message

                    _, closed = await asyncio.wait_for(
                        asyncio.gather(runner.cleanup(), receive_close()), timeout=2)
                    assert closed.type == WSMsgType.CLOSE
                    assert closed.data == 1001
                    assert stopped == [True], "Receiver cleanup must run after the client handler exits"
                    assert receiver.stop_event.is_set()
                    assert not receiver.thread.is_alive()
        finally:
            # Also release fixture resources if the bounded regression fails.
            await runner.cleanup()
            if not stopped:
                original_stop()
    asyncio.run(scenario())


def test_continuity_counts_duplicate_adaptation_and_gaps():
    def packet(pid, counter, marker=42, control=1):
        return bytes([0x47, pid >> 8, pid & 255, control << 4 | counter]) + bytes([marker]) * 184
    audit = ContinuityAudit()
    sequence = packet(256, 14) + packet(257, 3) + packet(256, 15)
    sequence += packet(256, 15)  # Identical duplicate is allowed.
    sequence += packet(256, 15, 0, control=2)  # No payload: CC need not increment.
    sequence += packet(256, 0) + packet(256, 2) + packet(257, 4)
    # Feed boundaries deliberately differ from transport packet boundaries.
    for start in range(0, len(sequence), 137):
        audit.feed(sequence[start:start + 137])
    events = list(audit.pop_through(None))
    assert len(events) == 1
    assert events[0]["pid"] == 256
    assert events[0]["reason"] == "MPEG-TS continuity counter gap"


def test_lost_transport_packet_requires_camera_idr(transport, tmp_path):
    data = transport.read_bytes()
    packets = [data[i:i + 188] for i in range(0, len(data), 188)]
    starts = [i for i, p in enumerate(packets) if (((p[1] & 31) << 8) | p[2]) == 256 and p[1] & 64]
    del packets[starts[2]]
    damaged = tmp_path / "lost-packet.ts"
    damaged.write_bytes(b"".join(packets))
    receiver = Receiver(str(damaged))
    receiver.start()
    receiver.control("play")
    reset_seen = False
    waiting = False
    try:
        while True:
            message = receiver.messages.get(timeout=3)
            if isinstance(message, bytes):
                header, _ = unpack_message(message)
                if waiting and header["camera_id"] == "A":
                    assert header["keyframe"]
                    waiting = False
            elif message.get("reset_camera") == "A":
                waiting = reset_seen = True
            elif message.get("state") == "ended":
                break
            elif message.get("type") == "error":
                pytest.fail(message["message"])
        assert reset_seen
        assert receiver.metrics["transport_discontinuities"]["A"] >= 1
    finally:
        receiver.stop()


def test_live_udp_demux(transport):
    reserve = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    reserve.bind(("127.0.0.1", 0))
    port = reserve.getsockname()[1]
    reserve.close()
    receiver = Receiver(f"udp://127.0.0.1:{port}")
    receiver.start()
    done = threading.Event()
    data = transport.read_bytes()
    def sender():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            time.sleep(.05)
            while not done.is_set():
                for start in range(0, len(data), 1316):
                    sock.sendto(data[start:start + 1316], ("127.0.0.1", port))
                    if done.wait(.002):
                        return
    thread = threading.Thread(target=sender)
    thread.start()
    try:
        seen = set()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and len(seen) < 2:
            message = receiver.messages.get(timeout=4)
            if isinstance(message, bytes):
                seen.add(unpack_message(message)[0]["camera_id"])
        assert seen == {"A", "B"}
        assert receiver.replay is False
    finally:
        done.set()
        thread.join(timeout=2)
        receiver.stop()


def test_private_session_common_mux_offset(transport, tmp_path):
    native = tmp_path / "native-offset.ts"
    with av.open(str(transport)) as source, av.open(str(native), "w", format="mpegts",
                                                   options={"mpegts_start_pid": "256", "mpegts_copyts": "1"}) as output:
        streams = {stream.index: output.add_stream_from_template(stream) for stream in source.streams.video}
        metadata = output.add_data_stream("bin_data")
        metadata.time_base = Fraction(1, 90000)
        packet = av.Packet(json.dumps({"schema_version": 1, "type": "session",
                                       "transport_pts_offset_us": 1_000_000}).encode())
        packet.stream = metadata
        packet.pts = packet.dts = 360000
        packet.time_base = Fraction(1, 90000)
        output.mux(packet)
        for packet in source.demux():
            if packet.size and packet.stream.index in streams:
                packet.stream = streams[packet.stream.index]
                output.mux(packet)
    receiver = Receiver(str(native))
    receiver.start()
    receiver.control("play")
    first = {}
    metadata_seen = False
    try:
        while True:
            message = receiver.messages.get(timeout=3)
            if isinstance(message, bytes):
                header, _ = unpack_message(message)
                first.setdefault(header["camera_id"], header["timestamp_us"])
            elif message.get("type") == "metadata":
                metadata_seen = True
            elif message.get("state") == "ended":
                break
            elif message.get("type") == "error":
                pytest.fail(message["message"])
        assert metadata_seen
        assert first == {"A": 4_000_000, "B": 4_010_000}
        assert receiver.transport_pts_offset_us == 1_000_000
    finally:
        receiver.stop()


def test_telemetry_only_private_pid(tmp_path):
    path = tmp_path / "telemetry-only.ts"
    record = {"schema_version": 1, "type": "health", "phase": "COAST", "phase_stale": False}
    with av.open(str(path), "w", format="mpegts", options={"mpegts_start_pid": "258", "mpegts_copyts": "1"}) as output:
        metadata = output.add_data_stream("bin_data")
        metadata.time_base = Fraction(1, 90000)
        for index in range(5):
            packet = av.Packet(json.dumps(record).encode())
            packet.stream = metadata
            packet.pts = packet.dts = 90000 + index * 9000
            packet.time_base = Fraction(1, 90000)
            output.mux(packet)
    with av.open(str(path)) as source:
        assert [(stream.id, stream.type) for stream in source.streams] == [(258, "data")]
    receiver = Receiver(str(path))
    receiver.start()
    receiver.control("play")
    seen = []
    try:
        while True:
            message = receiver.messages.get(timeout=3)
            assert not isinstance(message, bytes)
            if message.get("type") == "error":
                pytest.fail(message["message"])
            if message.get("type") == "metadata":
                seen.append(message["record"])
            if message.get("state") == "ended":
                break
        assert seen == [record] * 5
    finally:
        receiver.stop()


def test_late_audience_joins_paused_replay_without_rewind_and_control_is_owned(transport):
    """Decode the cached complete GOP while the shared replay stays at its frame."""
    async def scenario():
        from pigeonvision.ground.server import PRESENTATION
        app = create_app(str(transport))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"

        async def receive_until(ws, predicate, frames=None, events=None):
            for _ in range(1000):
                message = await ws.receive(timeout=3)
                assert message.type in (WSMsgType.TEXT, WSMsgType.BINARY), message
                if message.type == WSMsgType.BINARY:
                    header, payload = unpack_message(message.data)
                    if frames is not None:
                        frames.append((header, payload))
                    continue
                value = json.loads(message.data)
                if events is not None:
                    events.append(value)
                if value["type"] == "bootstrap_checkpoint":
                    await ws.send_json({"type": "bootstrap_ack", "id": value["id"], "sequence": value["sequence"]})
                if predicate(value):
                    return value
            pytest.fail("Expected presentation response was not received")

        try:
            async with ClientSession() as client:
                async with client.ws_connect(url) as operator:
                    initial = []
                    await receive_until(operator, lambda value: value.get("step_complete"), events=initial)
                    assert any(value.get("can_control") is True for value in initial)
                    for _ in range(4):
                        await operator.send_json({"type": "control", "action": "step"})
                        await receive_until(operator, lambda value: value.get("step_complete"))
                    receiver = app[RECEIVER]
                    generation = receiver.generation
                    position = dict(receiver.metrics["last_pts_us"])
                    assert not receiver.playing and receiver.steps == 0
                    async with client.ws_connect(url + "?role=audience") as audience:
                        frames, events = [], []
                        await receive_until(audience, lambda value: value["type"] == "bootstrap_end", frames, events)
                        assert any(value.get("can_control") is False for value in events)
                        assert receiver.generation == generation and receiver.metrics["last_pts_us"] == position
                        assert not receiver.playing and receiver.steps == 0
                        assert len([value for value in events if value["type"] == "bootstrap_checkpoint"]) >= 2
                        for name in ("A", "B"):
                            units = [(header, payload) for header, payload in frames if header["camera_id"] == name]
                            assert len(units) == 5 and units[0][0]["keyframe"]
                            assert units[-1][0]["timestamp_us"] == position[name]
                            decoder = av.CodecContext.create("h264", "r")
                            decoded = []
                            for header, payload in units:
                                packet = av.Packet(payload)
                                packet.pts = header["timestamp_us"]
                                packet.time_base = Fraction(1, 1_000_000)
                                decoded.extend(decoder.decode(packet))
                            decoded.extend(decoder.decode(None))
                            assert len(decoded) == 5 and decoded[-1].pts == position[name]
                        for command in ({"type": "control", "action": "play"}, {"type": "claim_control"},
                                        {"type": "view", "view": {"mode": "b", "yaw": 0, "pitch": 0, "fov": 1}},
                                        {"type": "reference", "reference": None}):
                            await audience.send_json(command)
                            await receive_until(audience, lambda value: value["type"] == "error")
                        assert receiver.generation == generation and not receiver.playing
                        async with client.ws_connect(url) as other:
                            other_events = []
                            await receive_until(other, lambda value: value["type"] == "bootstrap_end", events=other_events)
                            assert any(value.get("can_control") is False for value in other_events)
                            await other.send_json({"type": "claim_control"})
                            assert (await receive_until(other, lambda value: value["type"] == "control_owner"))["can_control"]
                            assert not (await receive_until(operator, lambda value: value["type"] == "control_owner"))["can_control"]
                            await operator.send_json({"type": "control", "action": "step"})
                            await receive_until(operator, lambda value: value["type"] == "error")
                            assert receiver.metrics["last_pts_us"] == position
                            view = {"mode": "perspective", "yaw": .4, "pitch": -.1, "roll": .25, "fov": 1.2, "horizon": False}
                            await other.send_json({"type": "view", "view": view})
                            assert (await receive_until(other, lambda value: value["type"] == "view"))["view"] == view
                            assert (await receive_until(audience, lambda value: value["type"] == "view"))["view"] == view
                            await other.send_json({"type": "view", "view": {**view, "pitch": 2}})
                            await receive_until(other, lambda value: value["type"] == "error")
                            assert app[PRESENTATION].view == view
                            receiver.messages.put_nowait({"type": "metadata", "record": {"type": "session", "session_id": "test-session"}})
                            await receive_until(other, lambda value: value["type"] == "metadata")
                            reference = {"pressure": 100123, "pts": position["A"], "sessionId": "test-session"}
                            await other.send_json({"type": "reference", "reference": reference})
                            assert (await receive_until(other, lambda value: value["type"] == "reference"))["reference"] == reference
                            await other.send_json({"type": "reference"})
                            await receive_until(other, lambda value: value["type"] == "error")
                            assert app[PRESENTATION].reference == reference
                            # A third audience gets the transported session before
                            # its datum, and receives the accepted view too.
                            async with client.ws_connect(url + "?role=audience") as late:
                                cached = []
                                await receive_until(late, lambda value: value["type"] == "bootstrap_end", events=cached)
                                start = next(i for i, value in enumerate(cached) if value["type"] == "bootstrap_start")
                                datum = next(i for i, value in enumerate(cached) if value["type"] == "reference")
                                assert start < datum and cached[datum]["reference"] == reference
                                assert cached[start]["metadata"][0]["record"]["session_id"] == "test-session"
                                cached_view = next(value for value in cached if value["type"] == "view")
                                assert cached_view["view"] == view and cached_view["initial"] is True
                        # Ownership returns to the next operator when its owner leaves.
                        assert (await receive_until(operator, lambda value: value["type"] == "control_owner"))["can_control"]
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


def test_ongoing_replay_follows_bootstrap_snapshot_without_missing_or_reordered_units(transport):
    async def scenario():
        app = create_app(str(transport))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"

        async def receive_until(ws, predicate, units=None, acknowledge=True):
            for _ in range(1000):
                message = await ws.receive(timeout=3)
                assert message.type in (WSMsgType.TEXT, WSMsgType.BINARY), message
                if message.type == WSMsgType.BINARY:
                    if units is not None:
                        units.append(unpack_message(message.data))
                    continue
                value = json.loads(message.data)
                if acknowledge and value["type"] == "bootstrap_checkpoint":
                    await ws.send_json({"type": "bootstrap_ack", "id": value["id"], "sequence": value["sequence"]})
                if predicate(value):
                    return value
            pytest.fail("Expected bootstrap continuation was not received")

        try:
            async with ClientSession() as client, client.ws_connect(url) as operator:
                await receive_until(operator, lambda value: value.get("step_complete"))
                for _ in range(4):
                    await operator.send_json({"type": "control", "action": "step"})
                    await receive_until(operator, lambda value: value.get("step_complete"))
                async with client.ws_connect(url + "?role=audience") as audience:
                    units = []
                    held = await receive_until(audience, lambda value: value["type"] == "bootstrap_checkpoint", units, acknowledge=False)
                    # Let the entire remainder reach the shared pump while this
                    # viewer is still waiting at its first cached GOP batch.
                    await operator.send_json({"type": "control", "action": "play"})
                    await receive_until(operator, lambda value: value.get("state") == "ended")
                    generation = app[RECEIVER].generation
                    position = dict(app[RECEIVER].metrics["last_pts_us"])
                    await audience.send_json({"type": "bootstrap_ack", "id": held["id"], "sequence": held["sequence"]})
                    await receive_until(audience, lambda value: value["type"] == "bootstrap_end", units)
                    assert app[RECEIVER].generation == generation
                    for name in ("A", "B"):
                        camera_units = [(header, payload) for header, payload in units if header["camera_id"] == name]
                        stamps = [header["timestamp_us"] for header, _ in camera_units]
                        assert len(stamps) == 12 and stamps == sorted(set(stamps))
                        assert stamps[-1] == position[name]
                        decoder = av.CodecContext.create("h264", "r")
                        decoded = []
                        for _, payload in camera_units:
                            decoded.extend(decoder.decode(av.Packet(payload)))
                        decoded.extend(decoder.decode(None))
                        assert len(decoded) == 12
        finally:
            await runner.cleanup()
    asyncio.run(scenario())

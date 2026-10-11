"""Bounded MPEG-TS receiver and localhost WebSocket/static HTTP service."""
import asyncio
from collections import deque
import json
import logging
from pathlib import Path
import queue
import threading
import time
from urllib.parse import urlparse
import webbrowser

import av
from aiohttp import web, WSMsgType

from .projection import validate_calibration
from .presentation import GroundPresentation, message_size
from .transport import H264Normalizer, METADATA_PID, VIDEO_PIDS, pts_microseconds, read_metadata, unpack_message
from .ts_input import TsInput
from .recording import TransportRecorder

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Receiver:
    """A single demux thread. Queue limits also apply when a browser is slow."""

    def __init__(self, source: str, replay: bool | None = None, queue_size: int = 48,
                 record_transport: str | None = None):
        self.source = source
        self.replay = urlparse(source).scheme not in {"udp", "tcp", "srt"} if replay is None else replay
        if self.replay and not Path(source).is_file():
            raise ValueError(f"Transport file does not exist: {source}")
        self.messages: queue.Queue = queue.Queue(maxsize=queue_size)
        self.stop_event = threading.Event()
        self.condition = threading.Condition()
        self.playing = not self.replay
        self.steps = 0
        self.restart_requested = False
        self.ended = False
        self.thread: threading.Thread | None = None
        self.generation = 0
        self.pacing_epoch = 0
        self.transport_pts_offset_us = 0
        self.source_opened_at = None
        self.first_unit_at = None
        self.metrics = {"access_units": {"A": 0, "B": 0}, "last_pts_us": {"A": None, "B": None},
                        "corrupt_packets": 0, "queue_resets": 0, "metadata_errors": 0,
                        "timestamp_discontinuities": 0,
                        "transport_discontinuities": {"A": 0, "B": 0, "metadata": 0, "other": 0}}
        self.state = "waiting"
        if record_transport is not None and (self.replay or urlparse(source).scheme != "udp"):
            raise ValueError("--record-transport requires a live UDP source")
        self.recorder = TransportRecorder(record_transport, on_error=self._recording_error) if record_transport is not None else None

    def _recording_error(self, message):
        self._put({"type": "error", "component": "transport_recording",
                   "message": f"Transport recording disabled: {message}. Live reception continues.",
                   "recoverable": False})
        self._put(self.status())

    def status(self, **extra) -> dict:
        return {"type": "status", "state": self.state, "source": self.source,
                "replay": self.replay, "playing": self.playing,
                "generation": self.generation, "queue_depth": self.messages.qsize(),
                "transport_pts_offset_us": self.transport_pts_offset_us,
                "transport_recording": self.recorder.status() if self.recorder else None,
                "first_unit_delay_s": None if self.first_unit_at is None else self.first_unit_at - self.source_opened_at,
                "metrics": self.metrics, "synchronization": "Exposure synchronization is unverified",
                **extra}

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, name="pigeonvision-demux", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.condition:
            self.pacing_epoch += 1
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=4)
        if self.recorder:
            self.recorder.close()

    def control(self, action: str) -> None:
        if not self.replay:
            raise ValueError("Replay controls are available only for saved files")
        with self.condition:
            self.pacing_epoch += 1
            if action == "play":
                self.playing = True
                if self.ended:
                    self.restart_requested = True
            elif action == "pause":
                self.playing = False
                self.steps = 0
            elif action == "step":
                self.playing = False
                self.steps = min(self.steps + 1, 3)
                if self.ended:
                    self.restart_requested = True
            elif action == "restart":
                self.playing = False
                self.steps = 1
                self.restart_requested = True
            else:
                raise ValueError(f"Unknown replay control: {action}")
            self.condition.notify_all()

    def _put(self, message) -> bool:
        if self.replay:
            while not self.stop_event.is_set():
                try:
                    self.messages.put(message, timeout=.1)
                    return True
                except queue.Full:
                    if self.restart_requested:
                        return False
            return False
        try:
            self.messages.put_nowait(message)
            return True
        except queue.Full:
            return False

    def _clear(self) -> None:
        while True:
            try:
                self.messages.get_nowait()
            except queue.Empty:
                return

    def _gate(self) -> bool:
        with self.condition:
            while self.replay and not self.playing and not self.steps and not self.stop_event.is_set() and not self.restart_requested:
                self.condition.wait(timeout=.25)
            return not (self.stop_event.is_set() or self.restart_requested)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            with self.condition:
                self.restart_requested = False
                self.ended = False
            self.generation += 1
            self._clear()
            self._put(self.status(reset=True))
            try:
                self._read()
                if self.restart_requested:
                    continue
                self.ended = True
                self.playing = False if self.replay else self.playing
                self.state = "ended" if self.replay else "disconnected"
                self._put(self.status())
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                self.state = "error"
                LOG.warning("Ground source: %s", exc)
                self._put({"type": "error", "component": "source", "message": str(exc), "recoverable": not self.replay})
                self._put(self.status())
                self.ended = True
            if self.replay:
                with self.condition:
                    while not self.restart_requested and not self.stop_event.is_set():
                        self.condition.wait(timeout=.25)
            else:
                self.stop_event.wait(.5)

    def _read(self) -> None:
        self.source_opened_at = time.monotonic()
        self.first_unit_at = None
        self.transport_pts_offset_us = 0
        with TsInput(self.source, self.stop_event, self.recorder) as transport, av.open(
                transport, mode="r", format="mpegts", options={"probesize": "262144", "analyzeduration": "1000000"}) as container:
            streams = {stream.index: VIDEO_PIDS[stream.id] for stream in container.streams
                       if stream.id in VIDEO_PIDS and stream.type == "video"}
            if not streams and not any(stream.id == METADATA_PID for stream in container.streams):
                raise ValueError("Expected camera or telemetry streams on MPEG-TS PIDs 256, 257, or 258")
            for stream in container.streams:
                if stream.index in streams and stream.codec_context.name != "h264":
                    raise ValueError(f"Camera {streams[stream.index]} is not H.264")
            normalizers = {stream.index: H264Normalizer(stream.codec_context.extradata or b"")
                           for stream in container.streams if stream.index in streams}
            self.state = "ready"
            self._put(self.status(cameras=sorted(streams.values()),
                                  missing_cameras=sorted(set(VIDEO_PIDS.values()) - set(streams.values()))))
            last_pts, step_seen = {}, set()

            def finish_step():
                if not step_seen:
                    return
                with self.condition:
                    self.steps = max(0, self.steps - 1)
                self._put(self.status(step_complete=True,
                                      step_cameras=sorted(step_seen),
                                      step_missing_cameras=sorted(set(streams.values()) - step_seen)))
                step_seen.clear()

            anchor_pts = anchor_wall = None
            previous_playing = False
            pacing_epoch = self.pacing_epoch
            last_status = 0.0
            for packet in container.demux():
                if not self._gate():
                    return
                if packet.size == 0:
                    continue
                for event in transport.audit.pop_through(packet.pos):
                    pid = event["pid"]
                    affected = VIDEO_PIDS.get(pid)
                    category = affected or ("metadata" if pid == METADATA_PID else "other")
                    self.metrics["transport_discontinuities"][category] += 1
                    if affected:
                        for stream_index, camera_id in streams.items():
                            if camera_id == affected:
                                normalizers[stream_index].waiting_for_keyframe = True
                        self._put(self.status(reset_camera=affected, reason=event["reason"]))
                    elif pid is None:
                        for normalizer in normalizers.values():
                            normalizer.waiting_for_keyframe = True
                        self._put(self.status(reset=True, reason=event["reason"]))
                    else:
                        self._put(self.status(reason=event["reason"]))
                if packet.stream.id == METADATA_PID:
                    try:
                        message = read_metadata(bytes(packet))
                        record = message["record"]
                        offset = record.get("transport_pts_offset_us") if record.get("type") == "session" else None
                        if offset is not None:
                            if not isinstance(offset, int):
                                raise ValueError("Transport PTS offset must be integer microseconds")
                            if offset != self.transport_pts_offset_us:
                                self.transport_pts_offset_us = offset
                                for normalizer in normalizers.values():
                                    normalizer.waiting_for_keyframe = True
                                last_pts.clear()
                                step_seen.clear()
                                anchor_pts = anchor_wall = None
                                self._put(self.status(reset=True, reason="Applying declared common transport PTS offset"))
                        self._put(message)
                    except (ValueError, UnicodeError):
                        self.metrics["metadata_errors"] += 1
                    continue
                if packet.stream.index not in streams or packet.pts is None:
                    continue
                index = packet.stream.index
                name = streams[index]
                timestamp = pts_microseconds(packet.pts, packet.time_base) - self.transport_pts_offset_us
                if name in last_pts and timestamp <= last_pts[name]:
                    # A backwards clock invalidates the decoder/pairing timeline.
                    self.metrics["timestamp_discontinuities"] += 1
                    for normalizer in normalizers.values():
                        normalizer.waiting_for_keyframe = True
                    self._put(self.status(reset=True, reason="Timestamp discontinuity"))
                    anchor_pts = anchor_wall = None
                    last_pts.clear()
                last_pts[name] = timestamp
                corrupt = bool(packet.is_corrupt)
                if corrupt:
                    self.metrics["corrupt_packets"] += 1
                    self._put(self.status(reset_camera=name, reason="Corrupt packet; waiting for IDR"))
                unit = normalizers[index].normalize(bytes(packet), name, timestamp, corrupt=corrupt)
                if unit is None:
                    continue
                if self.replay and not self.playing and name in step_seen:
                    # A camera can disappear while its stream remains in the
                    # PMT. Do not drain the surviving camera in search of its
                    # partner. Keep this AU for the next step so no compressed
                    # reference frame is discarded.
                    finish_step()
                    if not self._gate():
                        return
                if self.replay and self.playing:
                    if anchor_pts is None or not previous_playing or pacing_epoch != self.pacing_epoch:
                        anchor_pts, anchor_wall = timestamp, time.monotonic()
                        pacing_epoch = self.pacing_epoch
                    target = anchor_wall + (timestamp - anchor_pts) / 1_000_000
                    while target > time.monotonic() and not self.stop_event.is_set():
                        with self.condition:
                            self.condition.wait(timeout=min(.05, target - time.monotonic()))
                            if not self.playing or self.restart_requested:
                                break
                    if not self._gate():
                        return
                previous_playing = self.playing
                if not self._put(unit.encode()):
                    if self.replay:
                        return
                    # Compressed deltas cannot be discarded individually. Flush
                    # the backlog and explicitly wait for fresh keyframes.
                    self.metrics["queue_resets"] += 1
                    self._clear()
                    for normalizer in normalizers.values():
                        normalizer.waiting_for_keyframe = True
                    self._put(self.status(reset=True, preserve_geometry=True,
                                          reason="Receive queue full; waiting for IDRs"))
                    continue
                if self.first_unit_at is None:
                    self.first_unit_at = time.monotonic()
                self.metrics["access_units"][name] += 1
                self.metrics["last_pts_us"][name] = timestamp
                if self.replay and not self.playing:
                    step_seen.add(name)
                    if step_seen >= set(streams.values()):
                        finish_step()
                else:
                    step_seen.clear()
                if time.monotonic() - last_status > 1:
                    self._put(self.status())
                    last_status = time.monotonic()
            if self.replay and not self.playing:
                finish_step()


RECEIVER = web.AppKey("receiver", Receiver)
CLIENTS = web.AppKey("clients", set)
CALIBRATION = web.AppKey("calibration", dict)
PRESENTATION = web.AppKey("presentation", GroundPresentation)


class ViewerConnection:
    """A joining viewer cannot block the shared pump or drop its own deltas."""
    MAX_MESSAGES = 256
    MAX_BYTES = 16 * 1024 * 1024

    def __init__(self, ws, bootstrap_id):
        self.ws = ws
        self.bootstrap_id = bootstrap_id
        self.pending = deque()
        self.pending_bytes = 0
        self.wake = asyncio.Event()
        self.overflowed = False
        self.checkpoint = None
        self.ack = None
        self.task = None

    def enqueue(self, message):
        if self.overflowed:
            return False
        size = message_size(message)
        if len(self.pending) >= self.MAX_MESSAGES or self.pending_bytes + size > self.MAX_BYTES:
            self.pending.clear()
            self.pending_bytes = 0
            self.overflowed = True
            return False
        self.pending.append((message, size))
        self.pending_bytes += size
        self.wake.set()
        return True

    def pop(self):
        message, size = self.pending.popleft()
        self.pending_bytes -= size
        if not self.pending:
            self.wake.clear()
        return message

    def batch(self):
        """At most eight messages and four decode submissions per camera."""
        counts, result = {"A": 0, "B": 0}, []
        while self.pending and len(result) < 8:
            message = self.pending[0][0]
            name = unpack_message(message)[0]["camera_id"] if isinstance(message, bytes) else None
            if name in counts:
                if counts[name] == 4:
                    break
                counts[name] += 1
            result.append(self.pop())
        return result


def create_app(source: str, *, calibration: str | None = None,
               replay: bool | None = None, record_transport: str | None = None) -> web.Application:
    app = web.Application(client_max_size=16384)
    if calibration is not None:
        with open(calibration, encoding="utf-8") as handle:
            app[CALIBRATION] = validate_calibration(json.load(handle))
    app[RECEIVER] = Receiver(source, replay, record_transport=record_transport)
    app[CLIENTS] = set()

    async def index(request):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def health(request):
        return web.json_response(app[RECEIVER].status())

    connections = {}
    app[PRESENTATION] = GroundPresentation()
    bootstrap_counter = 0

    def enqueue(connection, message):
        if not connection.enqueue(message) and not connection.ws.closed:
            # Preserve other viewers and the receiver's wait-for-IDR recovery.
            # Only the viewer whose complete compressed chain cannot fit closes.
            asyncio.create_task(connection.ws.close(code=1013, message=b"Viewer too slow; reconnect for keyframes"))

    def broadcast(message):
        for connection in tuple(connections.values()):
            enqueue(connection, message)

    def notify_ownership():
        for ws, connection in tuple(connections.items()):
            enqueue(connection, {"type": "control_owner", "can_control": app[PRESENTATION].can_control(ws)})

    async def send(ws, message):
        task = ws.send_bytes(message) if isinstance(message, bytes) else ws.send_json(message)
        await asyncio.wait_for(task, timeout=.5)

    async def checkpoint(connection, sequence):
        connection.checkpoint = sequence
        connection.ack = asyncio.get_running_loop().create_future()
        try:
            await send(connection.ws, {"type": "bootstrap_checkpoint", "id": connection.bootstrap_id, "sequence": sequence})
            await asyncio.wait_for(connection.ack, timeout=3)
        finally:
            connection.ack = None
            connection.checkpoint = None

    async def deliver(connection, initial, records, video, reference):
        try:
            for message in initial:
                await send(connection.ws, message)
            await send(connection.ws, {"type": "bootstrap_start", "id": connection.bootstrap_id, "metadata": records})
            # Apply the transported session before the pressure datum that uses it.
            await send(connection.ws, {"type": "reference", "reference": reference})
            sequence = 0
            snapshot = ViewerConnection(connection.ws, connection.bootstrap_id)
            for message in video:
                if not snapshot.enqueue(message):
                    raise RuntimeError("Late-join snapshot exceeded its bounded queue")
            while snapshot.pending:
                for message in snapshot.batch():
                    await send(connection.ws, message)
                sequence += 1
                await checkpoint(connection, sequence)
            # The pump continued while the snapshot decoded. Drain that strictly
            # ordered continuation with the same limits before normal streaming.
            while connection.pending:
                for message in connection.batch():
                    await send(connection.ws, message)
                sequence += 1
                await checkpoint(connection, sequence)
            await send(connection.ws, {"type": "bootstrap_end", "id": connection.bootstrap_id})
            while True:
                await connection.wake.wait()
                while connection.pending:
                    await send(connection.ws, connection.pop())
        except (ConnectionError, asyncio.TimeoutError, RuntimeError):
            await connection.ws.close(code=1013, message=b"Viewer too slow; reconnect for keyframes")

    async def websocket(request):
        nonlocal bootstrap_counter
        role = request.query.get("role", "operator")
        if role not in {"operator", "audience"}:
            raise web.HTTPBadRequest(text="Unknown ground viewer role")
        ws = web.WebSocketResponse(heartbeat=10, max_msg_size=16384)
        await ws.prepare(request)
        receiver, presentation = app[RECEIVER], app[PRESENTATION]
        bootstrap_counter += 1
        connection = ViewerConnection(ws, bootstrap_counter)
        app[CLIENTS].add(ws)
        connections[ws] = connection
        presentation.join(ws, role)
        # Snapshot and registration are synchronous on this loop. Messages the
        # pump accepts after this point enter the viewer's continuation queue.
        records, video = presentation.cache.bootstrap_parts()
        initial = [receiver.status(reset=True),
                   {"type": "calibration", "calibration": app.get(CALIBRATION)},
                   {"type": "control_owner", "can_control": presentation.can_control(ws)}]
        if presentation.view is not None:
            initial.append({"type": "view", "view": presentation.view, "initial": True})
        connection.task = asyncio.create_task(deliver(connection, initial, records, video, presentation.reference))
        if receiver.replay and not presentation.viewer_started:
            presentation.viewer_started = True
            # Load only the first pair. Reconnecting and late joining use the
            # cache and never restart, resume, or advance an established replay.
            if not receiver.playing and not any(presentation.cache.video.values()):
                receiver.control("step")
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        value = json.loads(message.data)
                        if not isinstance(value, dict):
                            raise ValueError("Expected ground presentation control")
                        kind = value.get("type")
                        if kind == "bootstrap_ack":
                            if (type(value.get("id")) is int and value["id"] == connection.bootstrap_id and
                                    type(value.get("sequence")) is int and value["sequence"] == connection.checkpoint and
                                    connection.ack is not None and not connection.ack.done()):
                                connection.ack.set_result(None)
                        elif kind == "claim_control":
                            presentation.claim_control(ws)
                            notify_ownership()
                        elif kind == "control":
                            presentation.require_control(ws)
                            receiver.control(value.get("action", ""))
                            broadcast(receiver.status())
                        elif kind == "view":
                            broadcast(presentation.set_view(ws, value.get("view")))
                        elif kind == "reference":
                            if "reference" not in value:
                                raise ValueError("Expected pad pressure reference")
                            broadcast(presentation.set_reference(ws, value["reference"]))
                        else:
                            raise ValueError("Unknown ground presentation control")
                    except (ValueError, AttributeError, TypeError) as exc:
                        enqueue(connection, {"type": "error", "message": str(exc)})
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            app[CLIENTS].discard(ws)
            connections.pop(ws, None)
            if presentation.leave(ws):
                notify_ownership()
            connection.task.cancel()
            try:
                await connection.task
            except asyncio.CancelledError:
                pass
            if receiver.replay and not app[CLIENTS]:
                receiver.control("pause")
        return ws

    async def pump():
        while True:
            try:
                message = await asyncio.to_thread(app[RECEIVER].messages.get, True, .25)
            except queue.Empty:
                continue
            reference_change = app[PRESENTATION].accept(message)
            if app[PRESENTATION].should_relay(message):
                broadcast(message)
            if reference_change is not None:
                broadcast(reference_change)

    async def close_clients(application):
        # Shutdown precedes aiohttp's wait for active request handlers. Closing
        # here releases each WebSocket handler before that wait begins.
        await asyncio.gather(*(ws.close(code=1001, message=b"Ground server stopped")
                               for ws in tuple(application[CLIENTS])))

    async def lifecycle(application):
        application[RECEIVER].start()
        task = asyncio.create_task(pump())
        yield
        await asyncio.to_thread(application[RECEIVER].stop)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def revalidate_assets(request, response):
        if request.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"

    app.on_response_prepare.append(revalidate_assets)
    app.on_shutdown.append(close_clients)
    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/static/", STATIC)
    return app


def run(source: str, *, host: str = "127.0.0.1", port: int = 8768,
        calibration: str | None = None, open_browser: bool = True,
        replay: bool | None = None, record_transport: str | None = None) -> None:
    app = create_app(source, calibration=calibration, replay=replay, record_transport=record_transport)
    if open_browser:
        async def launch(application):
            asyncio.get_running_loop().call_later(.5, webbrowser.open, f"http://{host}:{port}/")
        app.on_startup.append(launch)
    web.run_app(app, host=host, port=port)

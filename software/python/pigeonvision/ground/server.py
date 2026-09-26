"""Bounded MPEG-TS receiver and localhost WebSocket/static HTTP service."""
import asyncio
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
from .transport import H264Normalizer, METADATA_PID, VIDEO_PIDS, pts_microseconds, read_metadata
from .ts_input import TsInput

LOG = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"


class Receiver:
    """A single demux thread. Queue limits also apply when a browser is slow."""

    def __init__(self, source: str, replay: bool | None = None, queue_size: int = 48):
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

    def status(self, **extra) -> dict:
        return {"type": "status", "state": self.state, "source": self.source,
                "replay": self.replay, "playing": self.playing,
                "generation": self.generation, "queue_depth": self.messages.qsize(),
                "transport_pts_offset_us": self.transport_pts_offset_us,
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
                self._put({"type": "error", "message": str(exc), "recoverable": not self.replay})
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
        with TsInput(self.source, self.stop_event) as transport, av.open(
                transport, mode="r", format="mpegts", options={"probesize": "262144", "analyzeduration": "1000000"}) as container:
            streams = {stream.index: VIDEO_PIDS[stream.id] for stream in container.streams
                       if stream.id in VIDEO_PIDS and stream.type == "video"}
            if not streams:
                raise ValueError("Expected H.264 camera streams on MPEG-TS PIDs 256 and 257")
            for stream in container.streams:
                if stream.index in streams and stream.codec_context.name != "h264":
                    raise ValueError(f"Camera {streams[stream.index]} is not H.264")
            normalizers = {stream.index: H264Normalizer(stream.codec_context.extradata or b"")
                           for stream in container.streams if stream.index in streams}
            self.state = "ready"
            self._put(self.status(cameras=sorted(streams.values()),
                                  missing_cameras=sorted(set(VIDEO_PIDS.values()) - set(streams.values()))))
            last_pts, step_seen = {}, set()
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
                    self._put(self.status(reset=True, reason="Receive queue full; waiting for IDRs"))
                    continue
                if self.first_unit_at is None:
                    self.first_unit_at = time.monotonic()
                self.metrics["access_units"][name] += 1
                self.metrics["last_pts_us"][name] = timestamp
                if self.replay and not self.playing:
                    step_seen.add(name)
                    if step_seen >= set(streams.values()):
                        with self.condition:
                            self.steps = max(0, self.steps - 1)
                        step_seen.clear()
                        self._put(self.status())
                else:
                    step_seen.clear()
                if time.monotonic() - last_status > 1:
                    self._put(self.status())
                    last_status = time.monotonic()


RECEIVER = web.AppKey("receiver", Receiver)
CLIENTS = web.AppKey("clients", set)
CALIBRATION = web.AppKey("calibration", dict)


def create_app(source: str, *, calibration: str | None = None,
               replay: bool | None = None) -> web.Application:
    app = web.Application(client_max_size=16384)
    app[RECEIVER] = Receiver(source, replay)
    app[CLIENTS] = set()
    if calibration is not None:
        with open(calibration, encoding="utf-8") as handle:
            app[CALIBRATION] = validate_calibration(json.load(handle))

    async def index(request):
        return web.FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def health(request):
        return web.json_response(app[RECEIVER].status())

    async def websocket(request):
        ws = web.WebSocketResponse(heartbeat=10, max_msg_size=16384)
        await ws.prepare(request)
        app[CLIENTS].add(ws)
        receiver = app[RECEIVER]
        await ws.send_json(receiver.status(reset=True))
        await ws.send_json({"type": "calibration", "calibration": app.get(CALIBRATION)})
        if receiver.replay:
            receiver.control("restart")
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        value = json.loads(message.data)
                        if value.get("type") != "control":
                            raise ValueError("Expected ground playback control")
                        receiver.control(value.get("action", ""))
                        await ws.send_json(receiver.status())
                    except (ValueError, AttributeError) as exc:
                        await ws.send_json({"type": "error", "message": str(exc)})
                elif message.type == WSMsgType.ERROR:
                    break
        finally:
            app[CLIENTS].discard(ws)
            if receiver.replay and not app[CLIENTS]:
                receiver.control("pause")
        return ws

    async def send(ws, message):
        try:
            task = ws.send_bytes(message) if isinstance(message, bytes) else ws.send_json(message)
            await asyncio.wait_for(task, timeout=.5)
        except (ConnectionError, asyncio.TimeoutError, RuntimeError):
            app[CLIENTS].discard(ws)
            await ws.close(code=1013, message=b"Viewer too slow; reconnect for keyframes")

    async def pump():
        while True:
            try:
                message = await asyncio.to_thread(app[RECEIVER].messages.get, True, .25)
            except queue.Empty:
                continue
            await asyncio.gather(*(send(ws, message) for ws in tuple(app[CLIENTS])))

    async def lifecycle(application):
        application[RECEIVER].start()
        task = asyncio.create_task(pump())
        yield
        await asyncio.gather(*(ws.close(code=1001, message=b"Ground server stopped")
                               for ws in tuple(application[CLIENTS])))
        await asyncio.to_thread(application[RECEIVER].stop)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app.cleanup_ctx.append(lifecycle)
    app.router.add_get("/", index)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", websocket)
    app.router.add_static("/static/", STATIC)
    return app


def run(source: str, *, host: str = "127.0.0.1", port: int = 8768,
        calibration: str | None = None, open_browser: bool = True,
        replay: bool | None = None) -> None:
    app = create_app(source, calibration=calibration, replay=replay)
    if open_browser:
        async def launch(application):
            asyncio.get_running_loop().call_later(.5, webbrowser.open, f"http://{host}:{port}/")
        app.on_startup.append(launch)
    web.run_app(app, host=host, port=port)

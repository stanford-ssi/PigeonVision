"""Ground-only operator ownership and bounded late-join presentation cache."""
from collections import OrderedDict, deque
import json
import math

from .transport import unpack_message


def valid_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_view(value):
    if not isinstance(value, dict):
        raise ValueError("Expected a viewing position")
    if not isinstance(value.get("mode"), str) or value["mode"] not in {"a", "b", "sphere", "perspective", "mask"}:
        raise ValueError("Unknown camera view")
    for key, lower, upper in (("yaw", -math.pi, math.pi), ("pitch", -1.56, 1.56), ("fov", .25, 2.6)):
        if not valid_number(value.get(key)) or not lower <= value[key] <= upper:
            raise ValueError(f"Invalid view {key}")
    result = {key: value[key] for key in ("mode", "yaw", "pitch", "fov")}
    roll = value.get("roll", 0)
    if not valid_number(roll) or not -math.pi <= roll <= math.pi:
        raise ValueError("Invalid view roll")
    result["roll"] = roll
    horizon = value.get("horizon", False)
    if type(horizon) is not bool:
        raise ValueError("Invalid horizon selection")
    result["horizon"] = horizon
    if "display" in value:
        result["display"] = validate_display(value["display"])
    return result


def validate_display(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid view display settings")
    result = {"focus": {}, "rotation": {}, "colour": {"strength": {}}}
    focus, rotation, colour = value.get("focus"), value.get("rotation"), value.get("colour")
    if not all(isinstance(item, dict) for item in (focus, rotation, colour)) or not isinstance(colour.get("strength"), dict):
        raise ValueError("Incomplete view display settings")
    for name in ("A", "B"):
        camera = focus.get(name)
        if not isinstance(camera, dict) or not valid_number(camera.get("zoom")) or not 1 <= camera["zoom"] <= 8:
            raise ValueError(f"Invalid camera {name} focus zoom")
        center = camera.get("center")
        if not isinstance(center, list) or len(center) != 2 or any(not valid_number(item) or not 0 <= item <= 1 for item in center):
            raise ValueError(f"Invalid camera {name} focus center")
        angle = rotation.get(name)
        if not valid_number(angle) or angle not in (0, 180):
            raise ValueError(f"Invalid camera {name} display rotation")
        strength = colour["strength"].get(name)
        if not valid_number(strength) or not 0 <= strength <= 1:
            raise ValueError(f"Invalid camera {name} display colour strength")
        result["focus"][name] = {"zoom": camera["zoom"], "center": list(center)}
        result["rotation"][name] = int(angle)
        result["colour"]["strength"][name] = strength
    # These are the four actual seam modes in the shader and HTML selector.
    if not valid_number(value.get("seam")) or value["seam"] not in (0, 1, 2, 3):
        raise ValueError("Invalid overlap seam")
    if type(colour.get("requested")) is not bool:
        raise ValueError("Invalid display colour request")
    if not valid_number(value.get("maxSkew")) or not .05 <= value["maxSkew"] <= 100:
        raise ValueError("Invalid maximum pairing gap")
    result["seam"] = int(value["seam"])
    result["colour"]["requested"] = colour["requested"]
    result["maxSkew"] = value["maxSkew"]  # milliseconds, matching the pairing control
    return result


def validate_reference(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or not valid_number(value.get("pressure")) or
            not 1000 < value["pressure"] < 125000 or not valid_number(value.get("pts")) or
            not isinstance(value.get("sessionId"), str) or not 1 <= len(value["sessionId"]) <= 200):
        raise ValueError("Invalid pad pressure reference")
    return {key: value[key] for key in ("pressure", "pts", "sessionId")}


def message_size(message):
    return len(message) if isinstance(message, bytes) else len(json.dumps(message, separators=(",", ":")).encode())


class PresentationCache:
    """Keep complete GOPs; an overflow waits for the next IDR, never a delta.

    Limits apply separately to each camera. Metadata has both a record and byte
    limit so a long run or oversized telemetry does not grow the cache.
    """
    VIDEO_BYTES = 8 * 1024 * 1024
    VIDEO_UNITS = 120
    METADATA_BYTES = 1024 * 1024
    METADATA_RECORDS = 512
    RECORD_BYTES = 65536
    EVENT_RECORDS = 128
    EVENT_BYTES = 256 * 1024
    FRAME_RECORDS = 256
    FRAME_BYTES = 8 * 1024 * 1024

    def __init__(self):
        self.video = {"A": [], "B": []}
        self.sizes = {"A": 0, "B": 0}
        self.video_pts = {"A": set(), "B": set()}
        self.metadata = deque()
        self.metadata_bytes = 0
        self.session = None
        self.frames = {}
        self.frame_history = {"A": OrderedDict(), "B": OrderedDict()}
        self.frame_bytes = {"A": 0, "B": 0}
        self.generation = None
        self.confirmed_session_id = None
        self.retired_sessions = deque(maxlen=32)
        self.events = deque()
        self.event_bytes = 0
        self.event_ids = set()

    @property
    def session_id(self):
        return self.session["record"].get("session_id") if self.session else None

    def _clear_camera(self, name):
        self.video[name] = []
        self.sizes[name] = 0
        self.video_pts[name].clear()

    def _clear_frames(self, name):
        self.frames.pop(name, None)
        self.frame_history[name].clear()
        self.frame_bytes[name] = 0

    def _remember_frame(self, name, message, size):
        pts = message["record"].get("pts_us")
        if not valid_number(pts) or pts < 0:
            return
        previous = self.frame_history[name].pop(pts, None)
        if previous:
            self.frame_bytes[name] -= previous[1]
        self.frame_history[name][pts] = (message, size)
        self.frame_bytes[name] += size
        while len(self.frame_history[name]) > self.FRAME_RECORDS or self.frame_bytes[name] > self.FRAME_BYTES:
            # Read-ahead metadata is dispensable; a held frame's exact record
            # is required by timestamped presentation such as demo attitude.
            retired = next((key for key in self.frame_history[name] if key not in self.video_pts[name]),
                           next(iter(self.frame_history[name])))
            _, removed = self.frame_history[name].pop(retired)
            self.frame_bytes[name] -= removed

    def _clear_timeline(self, *, keep_session=False):
        for name in self.video:
            self._clear_camera(name)
            self._clear_frames(name)
        self.metadata.clear()
        self.metadata_bytes = 0
        if not keep_session:
            self.session = None

    def _clear_events(self):
        self.events.clear()
        self.event_bytes = 0
        self.event_ids.clear()

    def accept(self, message):
        if isinstance(message, bytes):
            header, _ = unpack_message(message)
            name = header["camera_id"]
            if name not in self.video:
                return
            if header["keyframe"]:
                self._clear_camera(name)
            elif not self.video[name]:
                return
            if self.sizes[name] + len(message) > self.VIDEO_BYTES or len(self.video[name]) >= self.VIDEO_UNITS:
                self._clear_camera(name)
                return
            self.video[name].append(message)
            self.sizes[name] += len(message)
            self.video_pts[name].add(header["timestamp_us"])
        elif message.get("type") == "status":
            if "generation" in message and message["generation"] != self.generation:
                self._clear_timeline()
                self.generation = message["generation"]
                if message.get("replay"):
                    # A saved file may contain multiple sessions. Restarting it
                    # legitimately returns to a previously encountered session.
                    self.retired_sessions.clear()
            elif message.get("reset"):
                # Same-session video recovery retains its descriptive session,
                # but timestamps and frame metadata before the gap are stale.
                self._clear_timeline(keep_session=True)
            name = message.get("reset_camera")
            if isinstance(name, str) and name in self.video:
                self._clear_camera(name)
                self._clear_frames(name)
        elif message.get("type") == "metadata":
            record = message.get("record")
            if not isinstance(record, dict):
                return False
            size = message_size(message)
            if size > self.RECORD_BYTES:
                return False
            if record.get("type") == "session":
                session_id = record.get("session_id")
                if session_id is not None and not isinstance(session_id, str):
                    return False
                if session_id in self.retired_sessions:
                    return False
                if self.confirmed_session_id and session_id and session_id != self.confirmed_session_id:
                    self.retired_sessions.append(self.confirmed_session_id)
                    self._clear_timeline()
                    self._clear_events()
                if session_id:
                    self.confirmed_session_id = session_id
                self.session = message
            elif self.confirmed_session_id and record.get("session_id", self.confirmed_session_id) != self.confirmed_session_id:
                # A delayed old-session record cannot populate the new history.
                return False
            elif record.get("type") == "flight_event":
                ident = json.dumps([record.get(key) for key in ("inferred_epoch", "event_seq", "event", "valid", "stale")])
                if ident not in self.event_ids:
                    self.events.append((message, size, ident))
                    self.event_ids.add(ident)
                    self.event_bytes += size
                    while len(self.events) > self.EVENT_RECORDS or self.event_bytes > self.EVENT_BYTES:
                        _, removed, retired = self.events.popleft()
                        self.event_bytes -= removed
                        self.event_ids.remove(retired)
            elif isinstance(record.get("camera_id"), str) and record["camera_id"] in self.video:
                name = record["camera_id"]
                self.frames[name] = message
                if record.get("type") == "frame":
                    self._remember_frame(name, message, size)
            elif record.get("camera_id"):
                return False
            else:
                self.metadata.append((message, size))
                self.metadata_bytes += size
                while len(self.metadata) > self.METADATA_RECORDS or self.metadata_bytes > self.METADATA_BYTES:
                    _, removed = self.metadata.popleft()
                    self.metadata_bytes -= removed
            return True

    def bootstrap_parts(self):
        # Session clock origin must precede events; hide retained events while
        # a restarted receiver has not yet confirmed its transported session.
        events = [message for message, _, _ in self.events] if self.session else []
        matched = [history[pts][0] for name, history in self.frame_history.items()
                   for pts in sorted(self.video_pts[name]) if pts in history]
        matched.sort(key=lambda message: message["record"]["pts_us"])
        # Keep the latest camera geometry last even when the receiver has read
        # ahead of a paused image. Matched history supplies the held image PTS.
        latest = [message for message in self.frames.values() if message not in matched]
        records = ([self.session] if self.session else []) + events + [message for message, _ in self.metadata] + matched + latest
        # Stable ordering preserves each camera's compressed reference chain,
        # including when the two cameras have equal capture timestamps.
        video = sorted(self.video["A"] + self.video["B"], key=lambda m: unpack_message(m)[0]["timestamp_us"])
        return records, video

    def bootstrap(self):
        records, video = self.bootstrap_parts()
        return records + video


class GroundPresentation:
    """Ownership coordinates local viewers; roles are not authentication."""
    def __init__(self):
        self.cache = PresentationCache()
        self.roles = {}
        self.owner = None
        self.view = None
        self.reference = None
        self.viewer_started = False

    def join(self, client, role):
        if role not in {"operator", "audience"}:
            raise ValueError("Unknown ground viewer role")
        self.roles[client] = role
        if self.owner is None and role == "operator":
            self.owner = client

    def leave(self, client):
        self.roles.pop(client, None)
        if self.owner != client:
            return False
        self.owner = next((other for other, role in self.roles.items() if role == "operator"), None)
        return True

    def can_control(self, client):
        return self.roles.get(client) == "operator" and self.owner == client

    def require_control(self, client):
        if not self.can_control(client):
            raise ValueError("Only the active ground operator can change the presentation")

    def claim_control(self, client):
        if self.roles.get(client) != "operator":
            raise ValueError("Audience viewers cannot claim ground controls")
        self.owner = client

    def set_view(self, client, value):
        self.require_control(client)
        self.view = validate_view(value)
        return {"type": "view", "view": self.view}

    def set_reference(self, client, value):
        self.require_control(client)
        reference = validate_reference(value)
        if reference is not None and reference["sessionId"] != self.cache.session_id:
            raise ValueError("Pad pressure reference must match the current transported session")
        self.reference = reference
        return {"type": "reference", "reference": reference}

    def accept(self, message):
        accepted = self.cache.accept(message)
        record = message.get("record") if isinstance(message, dict) else None
        if accepted is not False and isinstance(record, dict) and record.get("type") == "session":
            session_id = record.get("session_id")
            if self.reference is not None and session_id and session_id != self.reference["sessionId"]:
                self.reference = None
                return {"type": "reference", "reference": None}
        return None

    def should_relay(self, message):
        record = message.get("record") if isinstance(message, dict) else None
        session_id = record.get("session_id") if isinstance(record, dict) else None
        # A new viewer has not seen the retired session itself, so its browser
        # cannot independently know to reject a delayed old-session declaration.
        return not isinstance(session_id, str) or session_id not in self.cache.retired_sessions

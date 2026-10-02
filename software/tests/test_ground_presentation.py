"""Ground-only policy, complete compressed chains and bounded bootstrap work."""
import math

import pytest

from pigeonvision.ground.presentation import GroundPresentation, PresentationCache, validate_reference, validate_view
from pigeonvision.ground.server import ViewerConnection
from pigeonvision.ground.transport import AccessUnit, unpack_message


def unit(name="A", pts=0, keyframe=False, size=1):
    return AccessUnit(name, pts, keyframe, "avc1.640032", b"x" * size).encode()


def metadata(kind="health", session="one", **extra):
    return {"type": "metadata", "record": {"type": kind, "session_id": session, **extra}}


def test_operator_ownership_never_grants_audience_control():
    ground = GroundPresentation()
    ground.join("audience", "audience")
    assert ground.owner is None
    ground.join("first", "operator")
    ground.join("second", "operator")
    assert ground.can_control("first") and not ground.can_control("second")
    for client in ("audience", "second", "unknown"):
        with pytest.raises(ValueError):
            ground.require_control(client)
    with pytest.raises(ValueError):
        ground.claim_control("audience")
    ground.claim_control("second")
    assert ground.can_control("second") and not ground.can_control("first")
    assert ground.leave("second")
    assert ground.can_control("first")
    assert ground.leave("first")
    assert ground.owner is None and not ground.can_control("audience")


def test_view_and_reference_require_current_operator_and_session():
    ground = GroundPresentation()
    ground.join("operator", "operator")
    ground.join("audience", "audience")
    view = {"mode": "perspective", "yaw": .3, "pitch": -.2, "fov": 1.1, "extra": "ignored"}
    assert ground.set_view("operator", view)["view"] == {**{key: view[key] for key in ("mode", "yaw", "pitch", "fov")}, "roll": 0, "horizon": False}
    with pytest.raises(ValueError):
        ground.set_view("audience", view)
    reference = {"pressure": 101325, "pts": 42, "sessionId": "one"}
    with pytest.raises(ValueError):
        ground.set_reference("operator", reference)
    ground.accept(metadata("session"))
    assert ground.set_reference("operator", reference)["reference"] == reference
    with pytest.raises(ValueError):
        ground.set_reference("audience", None)
    with pytest.raises(ValueError):
        ground.set_reference("operator", {**reference, "sessionId": "old"})
    # Decoder recovery and replaying this session retain the same pressure datum.
    ground.accept({"type": "status", "generation": 1, "reset": True})
    assert ground.reference == reference
    ground.accept(metadata("session"))
    assert ground.reference == reference
    assert ground.accept(metadata("session", "two")) == {"type": "reference", "reference": None}
    assert ground.reference is None


@pytest.mark.parametrize("field,value", [
    ("mode", "unknown"), ("mode", []), ("yaw", math.pi + .001),
    ("yaw", math.nan), ("pitch", 1.561), ("pitch", True),
    ("fov", .249), ("fov", math.inf), ("fov", 10 ** 1000),
    ("roll", math.pi + .001), ("roll", -math.pi - .001),
    ("roll", math.nan), ("roll", math.inf), ("roll", None), ("roll", True), ("roll", "0"),
    ("horizon", None), ("horizon", 1), ("horizon", "true"),
])
def test_invalid_view_values(field, value):
    view = {"mode": "sphere", "yaw": 0, "pitch": 0, "fov": 1.2}
    with pytest.raises(ValueError):
        validate_view({**view, field: value})


def test_view_boundaries_and_reference_validation():
    assert validate_view({"mode": "mask", "yaw": -math.pi, "pitch": -1.56, "fov": .25})["fov"] == .25
    assert validate_view({"mode": "a", "yaw": math.pi, "pitch": 1.56, "fov": 2.6})["mode"] == "a"
    assert validate_reference(None) is None
    for reference in ({}, {"pressure": True, "pts": 0, "sessionId": "one"},
                      {"pressure": 101325, "pts": math.nan, "sessionId": "one"},
                      {"pressure": 101325, "pts": 0, "sessionId": ""}):
        with pytest.raises(ValueError):
            validate_reference(reference)


def test_roll_is_canonical_in_ground_views_and_legacy_pose_defaults_to_level():
    ground = GroundPresentation()
    ground.join("operator", "operator")
    pose = {"mode": "perspective", "yaw": .4, "pitch": -.2, "fov": 1.1}
    assert ground.set_view("operator", pose)["view"]["roll"] == 0
    for roll in (-math.pi, .7, math.pi):
        shared = ground.set_view("operator", {**pose, "roll": roll})
        assert shared["view"]["roll"] == roll and ground.view["roll"] == roll
    assert ground.view["horizon"] is False
    assert ground.set_view("operator", {**pose, "horizon": True})["view"]["horizon"] is True


def test_video_cache_keeps_complete_gop_and_waits_for_idr_after_overflow():
    cache = PresentationCache()
    cache.VIDEO_UNITS = 3
    cache.accept(unit(pts=-1))
    assert not cache.video["A"]
    for pts in range(3):
        cache.accept(unit(pts=pts, keyframe=pts == 0))
    assert len(cache.video["A"]) == 3
    cache.accept(unit(pts=3))
    assert cache.video["A"] == [] and cache.sizes["A"] == 0
    cache.accept(unit(pts=4))
    assert not cache.video["A"]
    cache.accept(unit(pts=5, keyframe=True))
    cache.accept(unit("B", pts=4, keyframe=True))
    cache.accept(unit("B", pts=6))
    records, video = cache.bootstrap_parts()
    assert not records
    assert [unpack_message(message)[0]["timestamp_us"] for message in video] == [4, 5, 6]
    for name in ("A", "B"):
        assert unpack_message(cache.video[name][0])[0]["keyframe"]
    cache.VIDEO_BYTES = len(unit(pts=0, keyframe=True))
    cache.accept(unit(pts=10, keyframe=True, size=500))
    assert not cache.video["A"] and cache.sizes["A"] == 0


def test_metadata_cache_has_byte_and_record_limits_and_invalidates_timelines():
    cache = PresentationCache()
    cache.METADATA_RECORDS = 3
    cache.METADATA_BYTES = 400
    cache.accept({"type": "status", "generation": 1})
    cache.accept(metadata("session"))
    cache.accept(unit(keyframe=True))
    cache.accept(metadata("frame", camera_id="A"))
    for index in range(20):
        cache.accept(metadata(pts_us=index, detail="x" * 40))
    assert len(cache.metadata) <= 3 and cache.metadata_bytes <= 400
    cache.accept(metadata(camera_id="C"))
    cache.accept(metadata(camera_id=["bad"]))
    assert set(cache.frames) == {"A"}
    cache.accept(metadata("session", "two"))
    assert cache.session_id == "two"
    assert not cache.metadata and not cache.frames and not any(cache.video.values())
    cache.accept(metadata(session="one"))
    assert not cache.metadata
    cache.accept(unit(keyframe=True))
    cache.accept(metadata("frame", "two", camera_id="A"))
    cache.accept({"type": "status", "generation": 1, "reset_camera": "A"})
    assert not cache.video["A"] and "A" not in cache.frames
    cache.accept(metadata(session="two"))
    cache.accept({"type": "status", "generation": 2})
    assert cache.bootstrap() == [] and cache.metadata_bytes == 0
    cache.RECORD_BYTES = 100
    cache.accept(metadata("session", "large", detail="x" * 200))
    assert cache.session is None


def test_paused_bootstrap_keeps_exact_frame_metadata_despite_receiver_read_ahead():
    cache = PresentationCache()
    cache.accept(metadata("session", clock_origin_ns=1000000))
    held, future = [], []
    for name in ("A", "B"):
        current = metadata("frame", camera_id=name, pts_us=0, simulation_attitude={"source": "scene"})
        ahead = metadata("frame", camera_id=name, pts_us=33000, sensor_crop=[0, 0, 1920, 1080])
        cache.accept(current)
        cache.accept(unit(name, pts=0, keyframe=True))
        cache.accept(ahead)
        held.append(current)
        future.append(ahead)
    records, video = cache.bootstrap_parts()
    assert [unpack_message(message)[0]["timestamp_us"] for message in video] == [0, 0]
    assert all(message in records for message in held + future)
    assert records[-2:] == future  # latest camera geometry remains available
    assert [message["record"]["pts_us"] for message in records if message["record"]["type"] == "frame"] == [0, 0, 33000, 33000]


def test_frame_history_bounds_prioritize_metadata_matching_retained_gop():
    cache = PresentationCache()
    cache.FRAME_RECORDS = 3
    cache.FRAME_BYTES = 700
    retained = []
    for name in ("A", "B"):
        for pts in (0, 1):
            message = metadata("frame", camera_id=name, pts_us=pts, detail="retained")
            cache.accept(message)
            cache.accept(unit(name, pts=pts, keyframe=pts == 0))
            retained.append(message)
        for pts in range(2, 40):
            cache.accept(metadata("frame", camera_id=name, pts_us=pts, detail="x" * 100))
        assert len(cache.frame_history[name]) <= 3
        assert cache.frame_bytes[name] <= 700
        assert set(cache.frame_history[name]) >= {0, 1}
    records, _ = cache.bootstrap_parts()
    assert all(message in records for message in retained)
    assert not any(message["record"].get("pts_us") == 2 for message in records)
    replacement = metadata("frame", camera_id="A", pts_us=1, detail="replacement")
    before = cache.frame_bytes["A"]
    removed = cache.frame_history["A"][1][1]
    cache.accept(replacement)
    assert cache.frame_history["A"][1][0] == replacement
    assert cache.frame_bytes["A"] == before - removed + cache.frame_history["A"][1][1]
    # A byte bound is enforced even if overridden below one metadata record.
    cache.FRAME_BYTES = 1
    cache.accept(metadata("frame", camera_id="A", pts_us=1))
    assert cache.frame_bytes["A"] == 0 and not cache.frame_history["A"]


def test_frame_history_matches_complete_current_gop_and_clears_on_all_resets():
    cache = PresentationCache()
    cache.accept({"type": "status", "generation": 1})
    cache.accept(metadata("session"))
    for name in ("A", "B"):
        for pts in range(4):
            cache.accept(metadata("frame", camera_id=name, pts_us=pts))
            cache.accept(unit(name, pts=pts, keyframe=pts in (0, 2)))
    records, _ = cache.bootstrap_parts()
    assert [(message["record"]["camera_id"], message["record"]["pts_us"]) for message in records if message["record"]["type"] == "frame"] == [
        ("A", 2), ("B", 2), ("A", 3), ("B", 3)]
    cache.accept({"type": "status", "generation": 1, "reset_camera": "A"})
    assert not cache.frame_history["A"] and cache.frame_bytes["A"] == 0
    assert cache.frame_history["B"] and not cache.video_pts["A"]
    cache.accept({"type": "status", "generation": 1, "reset": True})
    assert cache.session_id == "one"
    assert not any(cache.frame_history.values()) and not any(cache.frame_bytes.values())
    cache.accept(metadata("frame", camera_id="A", pts_us=0))
    cache.accept({"type": "status", "generation": 2})
    assert not any(cache.frame_history.values()) and not any(cache.frame_bytes.values())
    cache.accept(metadata("session"))
    cache.accept(metadata("frame", camera_id="A", pts_us=0))
    cache.accept(metadata("session", "two"))
    assert not any(cache.frame_history.values()) and not any(cache.frame_bytes.values())
    assert cache.accept(metadata("frame", "one", camera_id="A", pts_us=0)) is False
    assert not any(cache.frame_history.values())


def test_bootstrap_batches_preserve_order_and_decoder_limits():
    connection = ViewerConnection(None, 1)
    expected = [unit("A", pts=i, keyframe=i == 0) for i in range(10)]
    expected += [metadata()]
    expected += [unit("B", pts=i, keyframe=i == 0) for i in range(10)]
    for message in expected:
        assert connection.enqueue(message)
    actual = []
    while connection.pending:
        batch = connection.batch()
        assert len(batch) <= 8
        cameras = [unpack_message(message)[0]["camera_id"] for message in batch if isinstance(message, bytes)]
        assert cameras.count("A") <= 4 and cameras.count("B") <= 4
        actual.extend(batch)
    assert actual == expected and connection.pending_bytes == 0 and not connection.wake.is_set()
    connection.MAX_MESSAGES = 2
    assert connection.enqueue(expected[0])
    assert connection.enqueue(expected[1])
    assert not connection.enqueue(expected[2])
    assert connection.overflowed and not connection.pending and connection.pending_bytes == 0
    assert not connection.enqueue(expected[0])
    another = ViewerConnection(None, 2)
    another.MAX_BYTES = 1
    assert not another.enqueue(expected[0])


def display():
    return {"focus": {"A": {"zoom": 1, "center": [.5, .5]}, "B": {"zoom": 8, "center": [0, 1]}},
            "rotation": {"A": 0, "B": 180}, "seam": 3,
            "colour": {"requested": True, "strength": {"A": 0, "B": 1}}, "maxSkew": 16.7}


def test_optional_display_state_is_complete_normalized_and_copied():
    settings = display()
    view = {"mode": "b", "yaw": 0, "pitch": 0, "fov": 1.2, "display": settings}
    normalized = validate_view(view)
    assert normalized["display"] == settings
    settings["focus"]["A"]["center"][0] = .7
    assert normalized["display"]["focus"]["A"]["center"] == [.5, .5]
    with pytest.raises(ValueError):
        validate_view({**view, "display": {}})
    for seam in (0, 1, 2, 3):
        assert validate_view({**view, "display": {**display(), "seam": seam}})["display"]["seam"] == seam


@pytest.mark.parametrize("path,value", [
    (("focus", "A", "zoom"), .99), (("focus", "B", "zoom"), 8.01),
    (("focus", "A", "center"), [0]), (("focus", "A", "center"), [-.01, .5]),
    (("focus", "B", "center"), [.5, math.nan]), (("rotation", "A"), True),
    (("rotation", "B"), 90), (("seam",), 4), (("seam",), .5),
    (("colour", "requested"), 1), (("colour", "strength", "A"), -1),
    (("colour", "strength", "B"), math.inf), (("maxSkew",), 0), (("maxSkew",), 100.01),
])
def test_invalid_shared_display_settings(path, value):
    settings = display()
    target = settings
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate_view({"mode": "a", "yaw": 0, "pitch": 0, "fov": 1.2, "display": settings})


def test_mission_events_survive_recent_metadata_eviction_and_same_session_restart():
    cache = PresentationCache()
    cache.METADATA_RECORDS = 2
    cache.EVENT_RECORDS = 3
    cache.accept({"type": "status", "generation": 1, "replay": True})
    cache.accept(metadata("session", clock_origin_ns=1000000))
    launch = metadata("flight_event", event="LAUNCH", event_seq=1, inferred_epoch=0, valid=True, pts_us=10)
    cache.accept(launch)
    cache.accept(launch)
    for pts in range(20):
        cache.accept(metadata(pts_us=pts))
    records, _ = cache.bootstrap_parts()
    assert records[0]["record"]["type"] == "session" and records[1] == launch
    assert len(cache.events) == 1 and len(cache.metadata) == 2
    cache.accept({"type": "status", "generation": 2, "replay": True})
    assert len(cache.events) == 1 and cache.bootstrap() == []  # unknown origin until session arrives
    cache.accept(metadata("session", clock_origin_ns=1000000))
    assert cache.bootstrap()[1] == launch
    for sequence in range(2, 7):
        cache.accept(metadata("flight_event", event="DEPLOYMENT", event_seq=sequence, inferred_epoch=0, valid=True))
    assert len(cache.events) == 3 and len(cache.event_ids) == 3
    cache.EVENT_BYTES = 100
    cache.accept(metadata("flight_event", event="LARGE", event_seq=99, detail="x" * 200))
    assert cache.event_bytes <= 100 and len(cache.events) == len(cache.event_ids)
    cache.accept(metadata("session", "two"))
    assert not cache.events and cache.event_bytes == 0


def test_retired_session_cannot_resurrect_cache_or_clear_current_pad_reference():
    ground = GroundPresentation()
    ground.join("operator", "operator")
    ground.accept(metadata("session"))
    ground.accept(metadata("session", "two"))
    datum = {"pressure": 101325, "pts": 42, "sessionId": "two"}
    ground.set_reference("operator", datum)
    assert ground.accept(metadata("session", "one")) is None
    assert ground.cache.session_id == "two" and ground.reference == datum
    assert not ground.should_relay(metadata("session", "one"))
    assert not ground.should_relay(metadata(session="one"))
    # A live decoder/source reconnect does not forget which session was retired.
    ground.accept({"type": "status", "generation": 2, "replay": False})
    ground.accept(metadata("session", "one"))
    assert ground.cache.session is None and ground.reference == datum
    ground.accept(metadata("session", "two"))
    assert ground.cache.session_id == "two"
    for index in range(40):
        ground.accept(metadata("session", f"session-{index}"))
    assert len(ground.cache.retired_sessions) <= 32
    # Explicit replay generation allows a multi-session file to begin again.
    ground.accept({"type": "status", "generation": 3, "replay": True})
    ground.accept(metadata("session", "one"))
    assert ground.cache.session_id == "one" and ground.should_relay(metadata("session", "one"))

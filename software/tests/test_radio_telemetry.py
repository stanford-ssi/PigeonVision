"""Private-PID telemetry remains intact alongside both H.264 camera streams."""
import asyncio
from fractions import Fraction
import json

import av
from aiohttp import ClientSession, WSMsgType, web

from pigeonvision.ground.server import create_app
from pigeonvision.ground.transport import unpack_message
from test_ground_transport import transport


def telemetry_fixture_records(session_id, pts_us=1_200_000):
    """Synthetic native-axis values; host FIFO-read times are not conversion times."""
    common = {"schema_version": 1, "session_id": session_id, "backend": "simulation",
              "simulated": True, "clock_domain": "CLOCK_BOOTTIME",
              "acquisition_clock_domain": "CLOCK_BOOTTIME"}
    base = 1_000_000 + pts_us - 90_000
    batch = {**common, "type": "sensors", "proof_id": "complete-imu-batch",
             "pts_us": pts_us, "timestamp_basis": "host_read", "sample_count": 9,
             "value_significant_digits": 6, "dropped_samples": 0, "sources": [
        {"source": "bmi088_accel", "valid": True, "base_monotonic_us": base,
         "shared_values": {"saturated": False}, "units": {"accel_m_s2": "m/s^2"},
         "rows": [[101, 0, -120, 0, [1.25, -2.5, 9.125]],
                  [102, 10000, 9880, 10000, [1.5, -2.75, 9.25]],
                  [103, 20000, 19880, 20000, [1.75, -3.0, 9.375]]]},
        {"source": "bmi088_gyro", "valid": True, "base_monotonic_us": base + 25000,
         "shared_values": {"fifo_overrun": True, "saturated": False},
         "units": {"gyro_rad_s": "rad/s"},
         "rows": [[201, 0, -700, 0, [-0.125, 0.25, -0.5]],
                  [202, 0, -700, 0, [-0.25, 0.375, -0.625]],
                  [203, 0, -700, 0, [-0.375, 0.5, -0.75]]]},
        {"source": "bmp581", "valid": True, "base_monotonic_us": base + 1000,
         "shared_values": {"in_operating_pressure_range": True},
         "units": {"pressure_pa": "Pa", "temperature_c": "degC"},
         "rows": [[301, 0, -150, 0, 100123.0, 20.125],
                  [302, 40000, 39850, 40000, 100111.0, 20.25]]},
        {"source": "ina226", "valid": True, "base_monotonic_us": base + 2000,
         "shared_values": {"power_basis": "bus_times_current", "saturated": False},
         "units": {"bus_voltage_v": "V", "current_a": "A", "power_w": "W", "shunt_voltage_v": "V"},
         "rows": [[401, 0, -200, 0, 12.25, -0.375, -4.59375, -0.001875]]}]}
    invalid = {**common, "type": "sensors", "proof_id": "invalid-imu-status",
               "pts_us": pts_us + 100000, "timestamp_basis": "host_read", "sample_count": 1,
               "value_significant_digits": 6, "dropped_samples": 2, "sources": [
        {"source": "bmi088_gyro", "valid": False, "status": "io_error", "errors": 2,
         "missed_polls": 3, "base_monotonic_us": base + 125000,
         "rows": [[204, 0, -700, -100000]]}]}
    status = {**common, "type": "flight_status", "proof_id": "complete-fc-status",
              "pts_us": pts_us + 20000, "valid": True, "stale": False, "seq": 42,
              "phase": "ASCENT", "event": "LAUNCH", "event_seq": 1, "fc_uptime_ms": 12345,
              "receive_monotonic_us": base + 100000, "monotonic_us": base + 100000,
              "publication_timestamp_ns": (1_000_000 + pts_us + 20000) * 1000,
              "host_clock_domain": "CLOCK_BOOTTIME", "inferred_epoch": 0,
              "epoch_reacquisition_pending": False,
              "counters": {"accepted": 43, "bad_crc": 1, "malformed": 2,
                           "duplicate": 3, "out_of_order": 4, "dropped_events": 0}}
    event = {**status, "type": "flight_event", "proof_id": "complete-fc-event", "pts_us": pts_us + 30000,
             "publication_timestamp_ns": (1_000_000 + pts_us + 30000) * 1000}
    return [batch, status, event, invalid]


def test_all_sensor_rows_and_fc_fields_reach_websocket_unchanged(transport, tmp_path):
    expected = telemetry_fixture_records("radio-telemetry-test", pts_us=4_100_000)
    session = {"schema_version": 1, "type": "session", "session_id": "radio-telemetry-test",
               "pts_us": 4_000_000, "clock_origin_ns": 1_000_000_000,
               "clock_domain": "CLOCK_BOOTTIME", "transport_pts_offset_us": 1_000_000,
               "cameras": []}
    path = tmp_path / "radio-telemetry.ts"
    with av.open(str(transport)) as source, av.open(str(path), "w", format="mpegts", options={
            "mpegts_start_pid": "256", "mpegts_copyts": "1"}) as target:
        streams = {stream.index: target.add_stream_from_template(stream) for stream in source.streams.video}
        metadata = target.add_data_stream("bin_data")
        metadata.time_base = Fraction(1, 90000)
        packets = []
        for packet in source.demux():
            if packet.size and packet.stream.index in streams:
                packet.stream = streams[packet.stream.index]
                packets.append(packet)
        for record in [session, *expected]:
            packet = av.Packet(json.dumps(record, separators=(",", ":")).encode())
            packet.pts = packet.dts = (record["pts_us"] + 1_000_000) * 90000 // 1_000_000
            packet.time_base, packet.stream = metadata.time_base, metadata
            packets.append(packet)
        for packet in sorted(packets, key=lambda value: value.dts * value.time_base):
            target.mux(packet)

    async def scenario():
        app = create_app(str(path))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/ws"
        received, decoders, decoded = {}, {}, set()
        try:
            async with ClientSession() as client, client.ws_connect(url) as ws:
                await ws.send_json({"type": "control", "action": "play"})
                while len(received) < len(expected) or decoded != {"A", "B"}:
                    message = await ws.receive(timeout=3)
                    if message.type == WSMsgType.BINARY:
                        header, payload = unpack_message(message.data)
                        name = header["camera_id"]
                        decoder = decoders.setdefault(name, av.CodecContext.create("h264", "r"))
                        if decoder.decode(av.Packet(payload)):
                            decoded.add(name)
                    else:
                        assert message.type == WSMsgType.TEXT
                        value = json.loads(message.data)
                        assert value.get("type") != "error", value
                        if value["type"] == "bootstrap_checkpoint":
                            await ws.send_json({"type": "bootstrap_ack", "id": value["id"], "sequence": value["sequence"]})
                        if value["type"] == "metadata" and "proof_id" in value["record"]:
                            record = value["record"]
                            received[record["proof_id"]] = record
                # This compares every row, unit, signed axis, host read time,
                # validity/overflow/error flag, and FC counter without expansion.
                assert received == {record["proof_id"]: record for record in expected}
        finally:
            await runner.cleanup()
    asyncio.run(scenario())

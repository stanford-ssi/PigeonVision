"""Saved-flight import retains descent; recovery visuals remain explicit assumptions."""
import importlib.util
import json
import math
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1] / "tools"
spec = importlib.util.spec_from_file_location("full_flight", TOOLS / "full_flight.py")
full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full)


@pytest.fixture
def saved(tmp_path):
    columns = ["Time", "Altitude", "Vertical velocity", "Total velocity", "Vertical acceleration",
        "Position East of launch", "Position North of launch", "Roll rate", "Vertical orientation (zenith)",
        "Lateral orientation (azimuth)", "Mass", "Thrust"]
    rows = [[0,0,0,0,0,0,0,.2,math.pi/2,0,10,100],
        [2,100,0,0,-9.8,1,0,.2,1,0,10,0], [3,90,-10,10,0,2,0,float("nan"),1,0,10,0],
        [5,0,-6,6,0,4,0,float("nan"),1,0,10,0]]
    source = tmp_path / "saved.xml"
    source.write_text('<openrocket><simulations><simulation status="uptodate"><name>Saved test</name>'
        '<flightdata maxaltitude="100"><databranch types="' + ','.join(columns) + '">'
        + ''.join(f'<datapoint>{",".join(map(str,row))}</datapoint>' for row in rows)
        + ''.join(f'<event type="{kind}" time="{time}"/>' for kind,time in [
            ("launch",0),("burnout",1),("apogee",2),("recoverydevicedeployment",2.1),
            ("recoverydevicedeployment",3),("groundhit",5)])
        + '</databranch></flightdata></simulation></simulations></openrocket>')
    return source


def test_saved_descent_is_resampled_through_contact_then_truthfully_held(saved):
    flight = full.import_flight(saved)
    summary, rows = flight["summary"], flight["trajectory"]
    assert summary["main_deployment_time_s"] == 6
    assert summary["landing_time_s"] == 8
    assert summary["duration_s"] == 11
    assert len(rows) == 331
    assert rows[90]["altitude_m"] == 0
    assert rows[150]["altitude_m"] == 100
    assert rows[180]["altitude_m"] == 90
    assert rows[210]["altitude_m"] == 45
    assert rows[239]["altitude_m"] > 0 and rows[239]["velocity_mps"] < 0
    assert all(row["stage"] == "landed" and row["altitude_m"] == row["velocity_mps"] == row["total_velocity_mps"] == 0 for row in rows[240:])
    assert all(row["east_m"] == 4 for row in rows[240:])
    assert "assumed" in flight["model"]["attitude"]
    assert "second physical canopy" in flight["model"]["recovery_geometry"]
    json.dumps(flight, allow_nan=False)


def test_saved_status_and_complete_ground_event_are_required(saved):
    saved.write_text(saved.read_text().replace('status="uptodate"','status="outdated"'))
    with pytest.raises(ValueError, match="up-to-date"):
        full.import_flight(saved)


def test_actual_supplied_source_keeps_known_openrocket_events():
    source = full.ROOT / "build/ground-station/full-flight/source.ork"
    if not source.exists():
        pytest.skip("Actual supplied .ork is a local ignored input")
    flight = full.import_flight(source)
    assert flight["source"]["sha256"] == "7cd22013359340f2e9ae044466c07e8728f033079e22b516593a42072fe91a27"
    summary = flight["summary"]
    assert summary["deployment_time_s"] == 26.967
    assert summary["main_deployment_time_s"] == 111.335
    assert summary["landing_time_s"] == 144.432
    assert math.isclose(summary["duration_s"], 4423/30)
    row = min(flight["trajectory"], key=lambda row: abs(row["t_s"]-111.335))
    assert 194 < row["altitude_m"] < 195  # Saved event occurs after crossing the nominal 200 m trigger.
    assert flight["trajectory"][-1]["altitude_m"] == 0


def test_full_demo_telemetry_reaches_landing_and_preserves_main_provenance():
    source = full.ROOT / "build/ground-station/full-flight/source.ork"
    if not source.exists():
        pytest.skip("Actual supplied .ork is a local ignored input")
    spec = importlib.util.spec_from_file_location("demo", TOOLS / "ground_demo.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    flight = full.import_flight(source)
    duration = flight["summary"]["duration_s"]
    clip = {"width":1552,"height":1552,"frames":[]}
    records = demo.telemetry_records({}, flight, clip, "test-session", duration)
    events = [record for record in records if record["type"] == "flight_event"]
    assert [(event["event"],event["pts_us"]) for event in events] == [
        ("LAUNCH",3_000_000),("APOGEE",26_966_000),("DEPLOYMENT",26_967_000),("LANDED",144_432_000)]
    assert records[0]["simulation_events"]["main_deployment_time_s"] == 111.335
    landed = [record for record in records if record["type"] == "flight_status" and record["phase"] == "LANDED"]
    assert landed[0]["pts_us"] == 144_432_000
    assert all(record["receive_monotonic_us"]-demo.CLOCK_ORIGIN_NS//1000 == record["pts_us"] for record in landed)
    pressure_rows = [row for record in records if record["type"] == "sensors"
                    for source in record["sources"] for row in source["rows"]]
    assert pressure_rows[-1][4] == 101325
    assert "effective canopy" in flight["model"]["recovery_geometry"]
    with pytest.raises(ValueError, match="extrapolate"):
        demo.trajectory_height(flight["trajectory"], [row["t_s"] for row in flight["trajectory"]], duration+.01)

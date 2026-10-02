#!/usr/bin/env python3
"""Import a saved OpenRocket flight through landing; no flight solver.

python software/tools/full_flight.py rocket.ork --prepare-site
Stages a separate capture site under build/ground-station/full-flight/site.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
FPS = 30


def import_flight(source: Path, *, pad_s: float = 3, grounded_s: float = 3) -> dict:
    if not all(math.isfinite(value) and value >= 0 for value in (pad_s, grounded_s)):
        raise ValueError("Pad and grounded-tail durations must be finite and nonnegative")
    payload = source.read_bytes()
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            xml = archive.read("rocket.ork")
    else:
        xml = payload
    document = ET.fromstring(xml)
    simulations = [s for s in document.findall("./simulations/simulation") if s.get("status") == "uptodate"]
    if len(simulations) != 1:
        raise ValueError("Expected exactly one saved up-to-date simulation")
    simulation = simulations[0]
    config_id = simulation.findtext("conditions/configid")
    motors = [motor.findtext("designation") for motor in document.findall(".//rocket//motor")
              if config_id is not None and motor.get("configid") == config_id]
    branch = simulation.find("flightdata/databranch")
    keys = branch.get("types").split(",")
    rows = [dict(zip(keys, map(float, point.text.split(",")))) for point in branch.findall("datapoint")]
    rows = sorted({row["Time"]: row for row in rows}.values(), key=lambda row: row["Time"])
    times = [row["Time"] for row in rows]
    events = [{**event.attrib, "time": float(event.get("time"))} for event in branch.findall("event")]
    def event_time(kind):
        return next(event["time"] for event in events if event["type"] == kind)
    apogee, burnout, contact = map(event_time, ("apogee", "burnout", "groundhit"))
    deployments = sorted(event["time"] for event in events if event["type"] == "recoverydevicedeployment")
    drogue = deployments[0]
    main = deployments[1] if len(deployments) > 1 else None
    if rows[-1]["Time"] < contact or not rows[0]["Time"] <= 0:
        raise ValueError("Saved flight does not contain the complete launch-to-ground trajectory")
    roll = 0
    for index, row in enumerate(rows):
        if index:
            previous = rows[index - 1]
            a, b = previous["Roll rate"], row["Roll rate"]
            if math.isfinite(a) and math.isfinite(b):
                roll += (a + b) * .5 * (row["Time"] - previous["Time"])
        row["Integrated roll"] = roll
    def sample(seconds):
        seconds = min(contact, max(0, seconds))
        index = min(len(rows) - 1, max(1, bisect.bisect_right(times, seconds)))
        a, b = rows[index - 1:index + 1]
        weight = (seconds - a["Time"]) / (b["Time"] - a["Time"])
        return {key: a[key] + (b[key] - a[key]) * weight
                if math.isfinite(a[key]) and math.isfinite(b[key]) else a[key] for key in a}
    duration = math.ceil((pad_s + contact + grounded_s) * FPS) / FPS
    deployment_value = sample(drogue)
    trajectory = []
    for index in range(round(duration * FPS) + 1):
        time = index / FPS
        value = sample(time - pad_s)
        grounded = time >= pad_s + contact
        east, north, altitude = (value[key] for key in ("Position East of launch", "Position North of launch", "Altitude"))
        altitude = 0 if grounded else altitude
        distance = math.sqrt((east - 100)**2 + north**2 + altitude**2)
        fspl = 92.45 + 20 * math.log10(1.28) + 20 * math.log10(max(distance, 1) / 1000)
        received = 10 * math.log10(500) + 15 - 7 - fspl
        noise = -174 + 10 * math.log10(9.6e6) + 3
        row = dict(t_s=time, altitude_m=altitude, east_m=east, north_m=north,
            velocity_mps=0 if grounded or time < pad_s else value["Vertical velocity"],
            total_velocity_mps=0 if grounded or time < pad_s else value["Total velocity"],
            acceleration_mps2=0 if grounded else value["Vertical acceleration"],
            mass_kg=value["Mass"], thrust_n=0 if grounded or time < pad_s else value["Thrust"],
            roll_deg=math.degrees(value["Integrated roll"]),
            roll_rate_rps=value["Roll rate"] / (2 * math.pi) if math.isfinite(value["Roll rate"]) else 0,
            tilt_deg=90 - math.degrees(value["Vertical orientation (zenith)"]),
            heading_deg=90 - math.degrees(value["Lateral orientation (azimuth)"]),
            slant_range_m=distance, fspl_db=fspl, received_dbm=received,
            link_margin_after_10db_reserve_db=received - noise - 8 - 10,
            stage="landed" if grounded else "pad" if time < pad_s else "boost" if time < pad_s + burnout
                else "coast" if time < pad_s + apogee else "recovery")
        trajectory.append({key: round(value, 7) if isinstance(value, float) else value for key, value in row.items()})
    return {"purpose": "Saved OpenRocket flight resampled at 30 Hz through contact, with a 3 s pad hold and assumed grounded tail.",
        "source": {"file": source.name, "sha256": hashlib.sha256(payload).hexdigest(),
            "xml_sha256": hashlib.sha256(xml).hexdigest(), "simulation": simulation.findtext("name"),
            "saved_status": simulation.get("status"), "motor": motors[0] if len(motors) == 1 else None},
        "inputs": {"target_apogee_m": 3048, "pad_duration_s": pad_s, "grounded_tail_s": grounded_s,
            "ground_horizontal_offset_m": 100, "body_diameter_m": .156718},
        "model": {"translation": "Saved OpenRocket simulation through groundhit, without altitude scaling. Position held after contact; post-contact zero velocity is assumed.",
            "attitude": "Saved zenith/azimuth and integrated finite roll rate before deployment. Recovery swing, swivel spin, main-deployment response and impact pose are assumed animation; not measured.",
            "recovery_geometry": "Illustrative shared-cord assembly. One effective canopy changes from drogue to main size; this does not model a second physical canopy or actual rigging.",
            "terrain": "Procedural desert, not a surveyed launch site."},
        "events": [{"type": event["type"], "t_s": pad_s + event["time"], "source": event.get("source")}
            for event in events if event["type"] in {"launch", "burnout", "apogee", "recoverydevicedeployment", "groundhit"}],
        "summary": {"sample_rate_hz": FPS, "duration_s": duration, "ignition_time_s": pad_s,
            "burnout_time_s": pad_s + burnout, "apogee_time_s": pad_s + apogee,
            "apogee_altitude_m": float(simulation.find("flightdata").get("maxaltitude")),
            "deployment_time_s": pad_s + drogue, "main_deployment_time_s": pad_s + main if main is not None else None,
            "deployment_attitude": {"tilt_deg": 90 - math.degrees(deployment_value["Vertical orientation (zenith)"]),
                                    "roll_deg": math.degrees(deployment_value["Integrated roll"])},
            "landing_time_s": pad_s + contact, "frames": len(trajectory)}, "trajectory": trajectory}


def prepare_site(source: Path, destination: Path, flight: dict):
    def replace_once(text, before, after):
        if text.count(before) != 1:
            raise ValueError(f"Source snapshot changed: expected exactly one {before!r}")
        return text.replace(before, after, 1)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "docs", destination, dirs_exist_ok=True)
    snapshot_source = destination.parent / ("source.ork" if zipfile.is_zipfile(source) else "source.xml")
    if source.resolve() != snapshot_source.resolve():
        shutil.copy2(source, snapshot_source)
    shutil.copy2(Path(__file__).with_name("recovery_attitude.js"), destination / "recovery_attitude.js")
    scene_path = destination / "scene.js"
    scene = scene_path.read_text()
    if scene.count("export function recoveryAttitude(") != 1 or scene.count("\n// World-frame recovery geometry") != 1:
        raise ValueError("Source snapshot changed: recovery function markers are ambiguous or missing")
    start = scene.index("export function recoveryAttitude(")
    end = scene.index("\n// World-frame recovery geometry", start)
    scene = 'import { assumedAttitude, groundRecoveryPose } from "./recovery_attitude.js";\n' + scene[:start] + "export const recoveryAttitude = assumedAttitude;\n" + scene[end:]
    scene = replace_once(scene, "const tau = s.tau ?? -1, R = RECOVERY;", "const tau = s.tau ?? -1, R = RECOVERY;\n  if (s.landed) return groundRecoveryPose(s, AIRFRAME, R, bodyToWorld);")
    scene = replace_once(scene, "const radius = R.drogueRadius * R.projectedFraction", "const radius = (R.drogueRadius + (ORK.main.diameter / 2 - R.drogueRadius) * (s.mainInflation || 0)) * R.projectedFraction")
    scene = replace_once(scene, "const boosterTop = add(swivel", "let boosterTop = add(swivel")
    scene = replace_once(scene, "const boosterAxis = mul(w, -1);", "const boosterAxis = mul(w, -1);\n  boosterTop[2] = Math.max(boosterTop[2], -s.altitude + (AIRFRAME.joint - AIRFRAME.bottom) * Math.max(0, boosterAxis[2]) + .08);")
    scene = replace_once(scene, "altitude: row.altitude_m - AIRFRAME.bottom,", "altitude: row.altitude_m + (attitude.tau <= 0 ? -AIRFRAME.bottom : attitude.cameraClearance),\n    landed: attitude.landed,\n    landingTau: attitude.landingTau,\n    mainInflation: attitude.mainInflation,")
    scene_path.write_text(scene)
    # OpenRocket's saved groundhit uses a flat AGL plane. Keep that small area
    # flat in the illustrative terrain too, including shader-scale roughness.
    terrain_path = destination / "terrain.js"
    terrain = replace_once(terrain_path.read_text(), "return h+${DETAIL_FINE.base", "return smoothstep(55.,85.,length(p))*(h+${DETAIL_FINE.base")
    terrain = replace_once(terrain, "190.,footprint);", "190.,footprint));")
    terrain_path.write_text(terrain)
    (destination / "assets/launch.json").write_text(json.dumps(flight, indent=2, allow_nan=False) + "\n")
    return destination / "assets/launch.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help=".ork archive or extracted rocket XML with saved flight")
    parser.add_argument("--output", type=Path, default=ROOT / "build/ground-station/full-flight/site/assets/launch.json")
    parser.add_argument("--prepare-site", action="store_true", help="Copy docs and apply the explicitly assumed recovery animation in the isolated output site")
    args = parser.parse_args(argv)
    flight = import_flight(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.prepare_site:
        prepare_site(args.source, args.output.parent.parent, flight)
    else:
        args.output.write_text(json.dumps(flight, indent=2, allow_nan=False) + "\n")
    print(json.dumps(flight["summary"], indent=2))


if __name__ == "__main__":
    main()

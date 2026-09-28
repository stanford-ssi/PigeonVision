"""Command line entry point for the Pi and the Mac ground station."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="pv", description="PigeonVision bench controls; measured hardware evidence stays in session directories.")
    root.add_argument("--version", action="version", version="pigeonvision 0.1.0")
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="Read-only host and camera inventory")
    doctor.add_argument("--binary", default="pv-capture")
    doctor.add_argument("--ssh", metavar="HOST")
    capture = commands.add_parser("capture", help="Run native capture and sample host health")
    capture.add_argument("--config", help="Configuration JSON file, or - for stdin; relative session_dir uses execution CWD")
    capture.add_argument("--camera-a", metavar="LIBCAMERA_ID")
    capture.add_argument("--camera-b", metavar="LIBCAMERA_ID")
    capture.add_argument("--session", help="New session directory, relative to execution CWD (remote CWD for --ssh)")
    capture.add_argument("--duration", type=float)
    capture.add_argument("--udp", metavar="HOST:PORT")
    capture.add_argument("--no-record", action="store_true")
    capture.add_argument("--binary", default="pv-capture")
    capture.add_argument("--ssh", metavar="HOST", help="Run installed pv on a reachable Pi over SSH")
    capture.add_argument("--write-config", metavar="PATH", help="Write resolved configuration without running capture")
    bench = commands.add_parser("bench", help="Capture/encode/record/stream matrix; failures stop the run")
    bench.add_argument("--config", required=True)
    bench.add_argument("--output", required=True, type=Path)
    bench.add_argument("--duration", type=float, default=60)
    bench.add_argument("--udp", metavar="HOST:PORT", help="Required to include stream and combined cases")
    bench.add_argument("--preset", choices=["ultrafast", "superfast", "veryfast"], action="append")
    bench.add_argument("--binary", default="pv-capture")
    bench.add_argument("--dry-run", action="store_true")
    calibration = commands.add_parser("calibrate", help="Fit checkerboard or ChArUco Mei intrinsics and evaluate held-out observations")
    calibration.add_argument("--dataset", required=True, type=Path)
    calibration_mode = calibration.add_mutually_exclusive_group(required=True)
    calibration_mode.add_argument("--rig", type=Path, help="Supplied rig geometry for the existing calibration bundle")
    calibration_mode.add_argument("--intrinsics-only", action="store_true", help="Per-lens diagnostics without rig alignment; absent held-out evidence remains unvalidated")
    calibration.add_argument("--camera", choices=("A", "B"), help="Select one camera for --intrinsics-only; default uses nonempty camera datasets")
    calibration.add_argument("--allow-unvalidated", action="store_true", help="Allow --intrinsics-only diagnostics without sufficient held-out views; output stays unvalidated")
    calibration.add_argument("--output", required=True, type=Path)
    for name in ("view", "replay"):
        viewer = commands.add_parser(name, help="View live UDP or replay a saved MPEG-TS in the browser")
        viewer.add_argument("source", help="MPEG-TS path or udp://0.0.0.0:PORT")
        viewer.add_argument("--host", default="127.0.0.1")
        viewer.add_argument("--port", type=int, default=8768)
        viewer.add_argument("--calibration")
        viewer.add_argument("--record-transport", metavar="PATH", help="Save live received UDP transport to a new .ts file")
        viewer.add_argument("--no-browser", action="store_true")
    report = commands.add_parser("report", help="Summarize measured session evidence and remaining unknowns")
    report.add_argument("session", type=Path)
    report.add_argument("--format", choices=["json", "markdown"], default="markdown")
    report.add_argument("--output", type=Path)
    return root


def _config(args):
    from .bench import validate_config
    if args.config:
        result = json.load(sys.stdin) if args.config == "-" else json.loads(Path(args.config).read_text())
        if args.camera_a or args.camera_b or args.duration is not None or args.udp or args.no_record:
            raise ValueError("With --config, set capture options in that file to keep the session configuration explicit.")
    else:
        stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S.%fZ")
        result = {"session_dir": args.session or f"output/sessions/{stamp}",
                  "cameras": [{"id": identity, "device": device} for identity, device in (("A", args.camera_a), ("B", args.camera_b)) if device],
                  "record": not args.no_record, "udp_destination": args.udp}
        if args.duration is not None:
            result["duration_seconds"] = args.duration
    if args.session:
        result["session_dir"] = args.session
    return validate_config(result)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "doctor":
            if args.ssh:
                return subprocess.run(["ssh", "--", args.ssh, shlex.join(["pv", "doctor", "--binary", args.binary])]).returncode
            from .doctor import inventory
            print(json.dumps(inventory(args.binary), indent=2))
        elif args.command == "capture":
            config = _config(args)
            if args.write_config:
                destination = Path(args.write_config)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("x") as target:
                    json.dump(config, target, indent=2)
                    target.write("\n")
                print(destination)
            elif args.ssh:
                remote_command = shlex.join(["pv", "capture", "--config", "-", "--binary", args.binary])
                return subprocess.run(["ssh", "--", args.ssh, remote_command], input=json.dumps(config), text=True).returncode
            else:
                from .bench import run_capture
                return run_capture(config, args.binary)
        elif args.command == "bench":
            from .bench import matrix, run_matrix
            config = json.loads(Path(args.config).read_text())
            if args.dry_run:
                print(json.dumps(matrix(config, args.output, args.duration, args.udp, args.preset), indent=2))
            else:
                return run_matrix(config, args.output, args.duration, args.udp, args.binary, args.preset)
        elif args.command == "calibrate":
            if (args.camera or args.allow_unvalidated) and not args.intrinsics_only:
                raise ValueError("--camera and --allow-unvalidated are only supported with --intrinsics-only.")
            if args.intrinsics_only:
                from .calibration import calibrate_intrinsics
                result = calibrate_intrinsics(args.dataset, args.output,
                                              camera_ids=(args.camera,) if args.camera else None,
                                              allow_unvalidated=args.allow_unvalidated)
                filename = "intrinsics.json"
            else:
                from .calibration import calibrate
                result = calibrate(args.dataset, args.rig, args.output)
                filename = "calibration.json"
            print(json.dumps(result["validation"], indent=2))
            print(f"Saved {args.output / filename}")
        elif args.command in ("view", "replay"):
            from .ground import run
            run(args.source, host=args.host, port=args.port, calibration=args.calibration,
                open_browser=not args.no_browser, replay=True if args.command == "replay" else None, record_transport=args.record_transport)
        elif args.command == "report":
            from .report import markdown, summarize
            result = summarize(args.session)
            rendered = json.dumps(result, indent=2) + "\n" if args.format == "json" else markdown(result)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered)
            else:
                print(rendered, end="")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"pv: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Black-box Linux acquisition checks. Uses simulation and localhost UDP only.

Usage: supervisor_integration.py --binary /path/pv-capture --output /path/build
Artifacts stay in a new directory under --output. No camera, SPI, or RF is used.
"""
import argparse
import json
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time


class Metadata:
    """Extract complete JSON PES records from the actual private PID 258."""
    def __init__(self):
        self.pending = bytearray()
        self.pes = bytearray()
        self.records = []
        self.bytes = 0

    def feed(self, data):
        self.bytes += len(data)
        self.pending.extend(data)
        while len(self.pending) >= 188:
            packet = self.pending[:188]
            del self.pending[:188]
            assert packet[0] == 0x47, "transport lost TS framing"
            if ((packet[1] & 31) << 8 | packet[2]) != 258:
                continue
            control = (packet[3] >> 4) & 3
            if not control & 1:
                continue
            offset = 5 + packet[4] if control & 2 else 4
            if packet[1] & 64:
                self.pes.clear()
            self.pes.extend(packet[offset:])
            if len(self.pes) < 9 or self.pes[:3] != b"\x00\x00\x01":
                continue
            length = 6 + int.from_bytes(self.pes[4:6], "big")
            if length == 6 or len(self.pes) < length:
                continue
            payload = bytes(self.pes[9 + self.pes[8]:length])
            self.records.append(json.loads(payload))
            self.pes.clear()


class Capture:
    def __init__(self, binary, directory, overrides=None):
        self.directory = directory
        directory.mkdir()
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.settimeout(.05)
        self.metadata = Metadata()
        self.status = directory / "status.json"
        self.config = directory / "config.json"
        settings = {
            "schema_version": 1, "profile": "bench", "cameras": [],
            "session_root": str(directory / "sessions"),
            "lock_path": str(directory / "capture.lock"),
            "status_path": str(self.status), "encode": True, "record": True,
            "udp_destination": f"127.0.0.1:{self.socket.getsockname()[1]}",
            "mux_bitrate": 9000000, "sensors": {"backend": "simulation"},
            "flight_uart": {"backend": "simulation"},
        }
        settings.update(overrides or {})
        self.config.write_text(json.dumps(settings))
        self.stdout = (directory / "stdout.jsonl").open("w")
        self.stderr = (directory / "stderr.log").open("w")
        self.process = subprocess.Popen([str(binary), "--config", str(self.config)],
                                        stdout=self.stdout, stderr=self.stderr)

    def collect(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            assert self.process.poll() is None, f"capture exited early: {self.directory}"
            try:
                self.metadata.feed(self.socket.recv(65535))
            except TimeoutError:
                pass

    def stop(self, expected_code=None, expected_failed=None):
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        try:
            code = self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
            raise AssertionError("SIGTERM did not finish within three seconds")
        finally:
            self.stdout.close()
            self.stderr.close()
            self.socket.close()
        assert code in (0, 1), f"unexpected shutdown status {code}"
        status = json.loads(self.status.read_text())
        assert status["lifecycle"] == "stopped"
        if expected_code is not None:
            assert code == expected_code, f"shutdown status {code}, expected {expected_code}"
        if expected_failed is not None:
            assert status["failed"] is expected_failed

    def health(self):
        records = [r for r in self.metadata.records if r.get("type") == "health"]
        assert records, "no health JSON received over PID 258"
        sessions = {r["session_id"] for r in self.metadata.records if r.get("type") == "session"}
        assert len(sessions) == 1, "missing or inconsistent session metadata"
        assert all(record.get("session_id") in sessions for record in records), \
            "health records must identify their acquisition session"
        return records[-1]

    def types(self):
        return {r.get("type") for r in self.metadata.records}


def run(binary, output):
    output.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="supervisor-integration-", dir=output))
    active = None
    try:
        active = Capture(binary, root / "nominal")
        active.collect(2.2)
        assert {"session", "health", "sensors", "flight_status"} <= active.types()
        assert active.health()["cameras"] == []
        assert active.health()["flight_uart"]["valid"] is True
        assert all(record["backend"] == "simulation"
                   for record in active.health()["sensors"]["devices"].values())
        duplicate = subprocess.run([str(binary), "--config", str(active.config)],
                                   capture_output=True, text=True, timeout=2)
        assert duplicate.returncode != 0 and "lock" in duplicate.stderr.lower()
        status = subprocess.run([str(binary), "--status", str(active.status)],
                                capture_output=True, text=True, timeout=2)
        assert status.returncode == 0 and json.loads(status.stdout)["lifecycle"] == "running"
        active.stop(expected_code=0, expected_failed=False)
        active = None
        sessions = list((root / "nominal" / "sessions").iterdir())
        assert len(sessions) == 1
        samples = [json.loads(line) for line in (sessions[0] / "sensors.jsonl").read_text().splitlines()]
        assert any(r.get("type") == "sensor_sample" and r["valid"] for r in samples)
        print("nominal simulation, telemetry, exclusive lock, SIGTERM: verified", flush=True)

        active = Capture(binary, root / "missing-camera", {
            "cameras": [{"id": "A", "device": "nonexistent-pv-camera"}]})
        active.collect(2.2)
        assert active.health()["degraded"] is True
        assert active.health()["cameras"] == []
        assert {"sensors", "flight_status"} <= active.types()
        active.stop(expected_code=1, expected_failed=True)
        active = None
        print("camera initialization failure retains telemetry and unsuccessful session result: verified",
              flush=True)

        missing = root / "absent-device"
        mappings = {name: {"device": str(missing), "address": address} for name, address in
                    (("bmi088_accel", 24), ("bmi088_gyro", 104), ("bmp581", 70), ("ina226", 64))}
        active = Capture(binary, root / "unavailable", {
            "sensors": {"backend": "i2c", **mappings},
            "flight_uart": {"backend": "serial", "device": str(missing)}})
        active.collect(2.2)
        health = active.health()
        assert health["degraded"] is True and health["flight_uart"]["stale"] is True
        assert all(not sample["valid"] for sample in health["sensors"]["devices"].values())
        assert "sensors" in active.types()
        active.stop()
        active = None
        print("unavailable sensor and UART devices leave telemetry running: verified", flush=True)

        active = Capture(binary, root / "fault", {
            "sensors": {"backend": "simulation", "simulation_fail_after_ms": 300}})
        active.collect(2.2)
        health = active.health()
        assert health["degraded"] is True
        assert any(sample["status"] == "io_error" for sample in health["sensors"]["devices"].values())
        assert health["flight_uart"]["valid"] is True
        assert "sensors" in active.types()
        active.stop()
        active = None
        print("simulated sensor fault leaves UART and telemetry running: verified", flush=True)

        obstruction = root / "regular-file"
        obstruction.write_text("blocks directory creation")
        active = Capture(binary, root / "storage", {
            "session_root": str(obstruction / "sessions")})
        active.collect(2.2)
        assert active.health()["log_failed"] is True
        assert active.health()["degraded"] is True
        assert "sensors" in active.types() and active.metadata.bytes > 0
        active.stop(expected_code=1, expected_failed=True)
        active = None
        print("unwritable session path leaves actual UDP telemetry running: verified", flush=True)

        bad = root / "invalid.json"
        bad.write_text(json.dumps({"session_dir": str(root / "never-created"), "cameras": [],
                                   "sensors": {"backend": "unknown"}}))
        rejected = subprocess.run([str(binary), "--config", str(bad)],
                                  capture_output=True, text=True, timeout=2)
        assert rejected.returncode != 0 and "backend" in rejected.stderr
        assert not (root / "never-created").exists()
        print(f"invalid config rejected before acquisition; artifacts: {root}", flush=True)
    finally:
        if active is not None:
            active.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run(args.binary.resolve(), args.output.resolve())

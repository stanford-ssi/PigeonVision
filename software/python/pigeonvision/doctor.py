"""Read-only host and camera inventory. Never installs or configures a device."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text().strip().replace("\x00", "")
    except (OSError, UnicodeError):
        return None


def command(args: list[str], timeout: float = 10) -> dict[str, Any]:
    if not shutil.which(args[0]):
        return {"available": False, "output": None}
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return {"available": True, "returncode": result.returncode,
                "output": result.stdout.strip(), "error": result.stderr.strip() or None}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "error": str(exc), "output": None}


def inventory(binary: str = "pv-capture") -> dict[str, Any]:
    from importlib.metadata import PackageNotFoundError, version
    packages = {}
    for name in ("pigeonvision", "av", "aiohttp", "numpy", "opencv-contrib-python-headless", "scipy"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    return {
        "schema_version": 1, "type": "inventory", "read_only": True,
        "host": {"system": platform.system(), "machine": platform.machine(),
                 "release": platform.release(), "python": platform.python_version(),
                 "cpu_count": os.cpu_count(), "model": read_text("/proc/device-tree/model"),
                 "os_release": read_text("/etc/os-release")},
        "packages": packages,
        "cameras": command([binary, "--list-cameras"]),
        "media_devices": sorted(str(p) for p in Path("/dev").glob("media*")),
        "v4l2_subdevices": sorted(str(p) for p in Path("/dev").glob("v4l-subdev*")),
        "throttled": command(["vcgencmd", "get_throttled"]),
        "notes": ["Camera IDs must be mapped to physical A/B before capture.",
                  "Reported timestamps do not establish synchronized exposure."]}

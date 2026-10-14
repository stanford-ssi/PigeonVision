#!/usr/bin/env python3
"""Prepare E200 RX-only operation; implementation lives in pigeonvision.radio."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from pigeonvision.radio.rx_guard import main

if __name__ == "__main__":
    raise SystemExit(main())

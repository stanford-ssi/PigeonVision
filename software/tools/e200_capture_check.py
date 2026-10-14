#!/usr/bin/env python3
"""Bounded E200 transport check; discards IQ and does not prove continuity."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from pigeonvision.radio.capture import main

if __name__ == "__main__":
    raise SystemExit(main())

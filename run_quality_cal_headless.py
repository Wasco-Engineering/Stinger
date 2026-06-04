#!/usr/bin/env python3
"""Headless quality calibration sweep on real hardware (no UI)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from quality_cal.headless import main

if __name__ == '__main__':
    raise SystemExit(main())

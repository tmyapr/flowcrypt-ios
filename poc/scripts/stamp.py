#!/usr/bin/env python3
"""Prefix every stdin line with a wall-clock timestamp and flush immediately.

Used to stamp `xcrun simctl launch --console-pty` output so the leaked
credential lines can be aligned with the screen recording.
"""
import sys
from datetime import datetime

for line in sys.stdin:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    sys.stdout.write(f"[{ts}] {line}")
    sys.stdout.flush()

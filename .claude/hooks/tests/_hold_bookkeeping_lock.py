#!/usr/bin/env python3
"""Test helper for test_publish_scope_e2e.sh (C1): hold a repo's bookkeeping
lock for N seconds, printing HELD once it is acquired.

Usage: _hold_bookkeeping_lock.py <repo-file-path> <seconds>
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bookkeeping_lock  # noqa: E402

target, secs = sys.argv[1], float(sys.argv[2])
with bookkeeping_lock.bookkeeping_lock(target, timeout=5):
    print("HELD", flush=True)
    time.sleep(secs)

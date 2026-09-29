#!/usr/bin/env python3
"""Slice J-1: write-ahead intent journal for `/work-done`'s four-surface write.

The journal is the LOAD-BEARING mechanism for OQ-J-16 crash-safety: it records
the intended payload for each of the four ship-event surfaces (TODO line, spine
slice-register row, spine `## Sessions` log bullet, conditional retire-marker)
BEFORE any surface is touched, then flips per-surface `applied: true` flags as
each surface is written + verified. On crash mid-write, re-running `/work-done`
re-reads the journal, calls L's `verify_write` to confirm any `applied: true`
claims still hold against the live surface, and re-applies any surface still
showing `applied: false`. When all four surfaces are confirmed applied, the
journal is deleted (the `.completed.jsonl` ledger written by /work-done is the
durable record).

Schema (`schema_version: 1`):

    {
      "schema_version": 1,
      "sid": "<session-id>",
      "topic_slug": "<topic-slug>",
      "created_at": "<ISO8601 UTC>",
      "surfaces": {
        "todo":           {"payload": {...}, "applied": false, "applied_at": null},
        "slice_register": {"payload": {...}, "applied": false, "applied_at": null},
        "sessions_log":   {"payload": {...}, "applied": false, "applied_at": null},
        "retire_marker":  {"payload": {...}, "applied": false, "applied_at": null}
      }
    }

`payload` per surface is opaque to this module — orchestrator writes it,
re-reads it on resume. `applied` is monotonic (false → true; never reverts in
normal flow). Atomic writes via tmp + rename mirror `pre_plan_gates._write_json`
(line 308). Concurrent writers serialize through an `fcntl.flock(LOCK_EX)`
sidecar `.lock` file (mirrors `taskmanagement.py:168` precedent).

Layer: pure data — no orchestration, no surface-write calls, no knowledge of L.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Module-level state dir constants — monkeypatchable from tests (mirrors
# taskmanagement.py LOCKS_DIR fixture pattern in tests/test_taskmanagement.py).
STATE_DIR = Path.home() / ".claude" / "state" / "work_done"

SCHEMA_VERSION = 1
SURFACES = ("todo", "slice_register", "sessions_log", "retire_marker")


def _intent_path(sid: str, topic_slug: str) -> Path:
    return STATE_DIR / f"{sid}__{topic_slug}.intent.json"


def _lock_path(sid: str, topic_slug: str) -> Path:
    return STATE_DIR / f"{sid}__{topic_slug}.intent.lock"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def _exclusive_lock(sid: str, topic_slug: str):
    """fcntl.LOCK_EX guard on a sidecar `.lock` file — concurrent writers serialize."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _lock_path(sid, topic_slug)
    # Open in append mode so the file is created if needed; we never read/write
    # payload through it — fcntl uses the file descriptor as the lock token.
    with open(lock_path, "a", encoding="utf-8") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    """Atomic tmp + rename — mirrors pre_plan_gates._write_json (line 308)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def write_intent(sid: str, topic_slug: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create the intent journal with all four surface payloads, applied=False.

    `payload` is `{surface_name: surface_payload, ...}` for any subset of the
    four surfaces. Missing surfaces get `payload: None` (the orchestrator can
    skip applying them — e.g., `retire_marker` is conditional on
    `all_slices_done`).

    Raises FileExistsError if the journal already exists — caller must
    explicitly `delete_intent` before overwriting (crash-recovery path re-reads
    the existing journal via `read_intent`, never re-writes it).
    """
    if not sid or not topic_slug:
        raise ValueError("sid and topic_slug must be non-empty.")
    unknown = set(payload.keys()) - set(SURFACES)
    if unknown:
        raise ValueError(
            f"Unknown surface(s) in payload: {sorted(unknown)}. "
            f"Allowed: {SURFACES}."
        )
    path = _intent_path(sid, topic_slug)
    with _exclusive_lock(sid, topic_slug):
        if path.exists():
            raise FileExistsError(
                f"Intent journal already exists: {path}. "
                "Re-running /work-done? Use read_intent + mark_applied to resume; "
                "use delete_intent to discard."
            )
        record = {
            "schema_version": SCHEMA_VERSION,
            "sid": sid,
            "topic_slug": topic_slug,
            "created_at": _utcnow_iso(),
            "surfaces": {
                surface: {
                    "payload": payload.get(surface),
                    "applied": False,
                    "applied_at": None,
                }
                for surface in SURFACES
            },
        }
        _atomic_write_json(path, record)
        return record


def read_intent(sid: str, topic_slug: str) -> Optional[Dict[str, Any]]:
    """Return the journal record dict, or None if no journal file exists."""
    path = _intent_path(sid, topic_slug)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def mark_applied(sid: str, topic_slug: str, surface: str) -> Dict[str, Any]:
    """Flip surfaces[surface].applied true (monotonic). Returns the updated record.

    Raises ValueError if the journal doesn't exist or `surface` is unknown.
    Idempotent: re-marking an already-applied surface is a no-op (returns the
    existing record unchanged).
    """
    if surface not in SURFACES:
        raise ValueError(f"Unknown surface: {surface}. Allowed: {SURFACES}.")
    path = _intent_path(sid, topic_slug)
    with _exclusive_lock(sid, topic_slug):
        if not path.exists():
            raise ValueError(
                f"No intent journal for sid={sid} topic={topic_slug}: {path}"
            )
        record = json.loads(path.read_text(encoding="utf-8"))
        surf = record["surfaces"][surface]
        if surf["applied"]:
            return record
        surf["applied"] = True
        surf["applied_at"] = _utcnow_iso()
        _atomic_write_json(path, record)
        return record


def is_applied(sid: str, topic_slug: str, surface: str) -> bool:
    """Return True iff surfaces[surface].applied is True in the on-disk journal.

    Returns False if the journal doesn't exist (no in-flight ship event). Raises
    ValueError on unknown surface.
    """
    if surface not in SURFACES:
        raise ValueError(f"Unknown surface: {surface}. Allowed: {SURFACES}.")
    record = read_intent(sid, topic_slug)
    if record is None:
        return False
    return bool(record["surfaces"][surface]["applied"])


def delete_intent(sid: str, topic_slug: str) -> bool:
    """Remove the journal file and its lock sidecar. Returns True if anything deleted."""
    path = _intent_path(sid, topic_slug)
    lock_path = _lock_path(sid, topic_slug)
    removed_any = False
    with _exclusive_lock(sid, topic_slug):
        if path.exists():
            path.unlink()
            removed_any = True
    # Clean up the sidecar AFTER releasing the lock to avoid the rare race
    # where another process holds the lock fd. Missing sidecar is fine.
    try:
        if lock_path.exists():
            lock_path.unlink()
            removed_any = True
    except FileNotFoundError:
        pass
    return removed_any


if __name__ == "__main__":  # pragma: no cover — module is library-only
    import sys

    print(
        "work_done_journal is a library module (Slice J-1). "
        "No CLI — call from work_done.py orchestrator.",
        file=sys.stderr,
    )
    sys.exit(2)

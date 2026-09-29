#!/usr/bin/env python3
"""skill_marker.py — session skill-invocation marker (orchestrator-pattern S3 seed).

A canonical skill writes a per-session marker when it starts running. A later
PreToolUse Agent guardrail hook (Slice S4, `check-skill-marker.sh`) reads the
same marker to block a look-alike dispatch that shadows the skill when its
marker is absent (fail-closed). This module is the standalone, vendor-neutral
seed both sides share — no Claude-specific imports, unit-testable without the
harness (`python3 skill_marker.py --self-test`).

Marker path (locked A15):
    <config>/state/skill-markers/<session-id>/<skill-name>.marker
where <config> = $CLAUDE_CONFIG_DIR or ~/.claude.

Commands:
    write <skill> <sid>   Create the marker (idempotent). Exit 0.
    check <skill> <sid>   Exit 0 if present (prints the marker JSON);
                          exit 2 if absent/unreadable (fail-closed).
    path  <skill> <sid>   Print the resolved marker path. Exit 0.
    --self-test           Run an in-memory sentinel test against a temp dir.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def _config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def marker_path(skill: str, sid: str, root: Path = None) -> Path:
    base = root if root is not None else _config_dir()
    return base / "state" / "skill-markers" / sid / f"{skill}.marker"


def write_marker(skill: str, sid: str, root: Path = None) -> dict:
    p = marker_path(skill, sid, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "skill": skill,
        "sid": sid,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    # Idempotent: preserve the original started_at if the marker already exists.
    if p.exists():
        try:
            prior = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(prior, dict) and prior.get("started_at"):
                payload["started_at"] = prior["started_at"]
        except (ValueError, OSError):
            pass
    p.write_text(json.dumps(payload), encoding="utf-8")
    return {"status": "wrote", "marker": str(p), **payload}


def check_marker(skill: str, sid: str, root: Path = None):
    """Return (present: bool, info: dict). Absent/unreadable → (False, ...)."""
    p = marker_path(skill, sid, root)
    if not p.exists():
        return False, {"status": "absent", "marker": str(p)}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return True, {"status": "present", "marker": str(p), "payload": data}
    except (ValueError, OSError) as e:
        # Unreadable = absent (fail-closed).
        return False, {"status": "unreadable", "marker": str(p), "error": str(e)}


def _self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        skill, sid = "close", "sid-test-123"
        # Absent before write → check fails closed.
        present, _ = check_marker(skill, sid, root)
        assert present is False, "expected absent before write"
        # Write → present.
        w = write_marker(skill, sid, root)
        assert Path(w["marker"]).is_file(), "marker not created"
        present, info = check_marker(skill, sid, root)
        assert present is True and info["payload"]["skill"] == skill, info
        # Idempotent write preserves started_at.
        started = info["payload"]["started_at"]
        write_marker(skill, sid, root)
        _, info2 = check_marker(skill, sid, root)
        assert info2["payload"]["started_at"] == started, "started_at not preserved"
        # A different skill's marker is independent (no shadow).
        present_other, _ = check_marker("research", sid, root)
        assert present_other is False, "unrelated skill should be absent"
    print("skill_marker.py self-test: PASS")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return _self_test()
    if len(argv) < 3:
        print("usage: skill_marker.py {write|check|path} <skill> <sid>", file=sys.stderr)
        return 2
    cmd, skill, sid = argv[0], argv[1], argv[2]
    if cmd == "write":
        print(json.dumps(write_marker(skill, sid)))
        return 0
    if cmd == "check":
        present, info = check_marker(skill, sid)
        print(json.dumps(info))
        return 0 if present else 2
    if cmd == "path":
        print(str(marker_path(skill, sid)))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

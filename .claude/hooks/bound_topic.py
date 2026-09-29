#!/usr/bin/env python3
"""bound_topic — resolve the active session's bound topic, and the path where a
slug-scoped advisory draft (double-check / recommend / challenge) should land.

Closes the bookkeeping-model audit-trail rule (GP#6 / B1+B2 S2): fact-check
ephemera live UNDER the owning slug in `Thoughts/<slug>-<ts>_<TYPE>_<sid>.md`
(advisory bucket — slug-grep returns them, they co-retire) when a topic is bound,
falling back to the legacy `~/.claude/state/plan_validation/adhoc/` location only
for cold sessions with no bound topic.

Shared helper (#8.0) for the three skills (#8/#9/#10). Resolution mirrors
_factcheck_engine._resolve_topic_default but stays standalone (no engine import —
Cockburn: don't couple three skills to the FC engine for a 15-line state read).

CLI:
    bound_topic.py resolve <session_id>                      # JSON or 'null'
    bound_topic.py draft-path <session_id> <TYPE> [--ts T]   # prints the target path
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bookkeeping_invariant as bi

PPG_DIR = os.path.join(os.path.expanduser("~"), ".claude", "state", "pre_plan_gates")
ADHOC_DIR = os.path.join(os.path.expanduser("~"), ".claude", "state", "plan_validation", "adhoc")

# Advisory ephemera TYPEs this helper places under the slug.
EPHEMERA_TYPES = ("DOUBLECHECK", "RECOMMEND", "CHALLENGE")


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def resolve(session_id: str) -> dict | None:
    """active session -> {slug, thoughts_dir, project_root} or None (cold)."""
    active = _load_json(os.path.join(PPG_DIR, "_active.json")) or {}
    sd = active.get(session_id)
    if not isinstance(sd, dict):
        return None
    proj = sd.get("topic_slug")
    topic = sd.get("active_project")
    if not proj or not topic:
        return None
    state = _load_json(os.path.join(PPG_DIR, f"{proj}__{topic}.json"))
    if not isinstance(state, dict):
        return None
    slug = state.get("topic_slug") or proj
    project_root = state.get("project_root")
    tfp = state.get("thought_file_path")
    if tfp:
        thoughts_dir = os.path.dirname(tfp)
        slug = bi.classify(os.path.basename(tfp)).slug or slug
    elif project_root:
        thoughts_dir = os.path.join(project_root, "Thoughts")
    else:
        return None
    return {"slug": slug, "thoughts_dir": thoughts_dir, "project_root": project_root}


def resolve_by_sid8(sid8: str) -> dict | None:
    """Resolve an 8-char session-id prefix (as found in adhoc filenames) to its
    bound topic, by prefix-matching the full session ids in _active.json."""
    active = _load_json(os.path.join(PPG_DIR, "_active.json")) or {}
    for full_sid in active:
        if full_sid.startswith(sid8):
            info = resolve(full_sid)
            if info:
                return info
    return None


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def draft_path(session_id: str, type_: str, ts: str | None = None) -> tuple[str, bool]:
    """Return (path, bound). Bound -> Thoughts/<slug>-<ts>_<TYPE>_<sid8>.md;
    cold -> the legacy adhoc fallback. Never raises."""
    type_ = type_.upper()
    sid8 = (session_id or "nosession")[:8]
    ts = ts or _now_ts()
    info = resolve(session_id)
    if info and info.get("thoughts_dir"):
        fn = f"{info['slug']}-{ts}_{type_}_{sid8}.md"
        return os.path.join(info["thoughts_dir"], fn), True
    fn = f"{type_.lower()}_{sid8}_{ts}.md"
    return os.path.join(ADHOC_DIR, fn), False


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(__doc__ + "\n")
        return 2
    cmd = argv[0]
    if cmd == "resolve" and len(argv) >= 2:
        print(json.dumps(resolve(argv[1])))
        return 0
    if cmd == "draft-path" and len(argv) >= 3:
        ts = None
        if "--ts" in argv:
            i = argv.index("--ts")
            if i + 1 < len(argv):
                ts = argv[i + 1]
        path, bound = draft_path(argv[1], argv[2], ts)
        print(path)
        sys.stderr.write(f"bound={bound}\n")
        return 0
    sys.stderr.write("usage: bound_topic.py resolve <sid> | draft-path <sid> <TYPE> [--ts T]\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

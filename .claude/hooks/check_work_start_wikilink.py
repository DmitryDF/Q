#!/usr/bin/env python3
"""check_work_start_wikilink — block orphan plain-* topic-state mints (DS8 #11 / Row 1).

The 2026-06-16 incident: `/work-start` falls back to Plain mode and mints durable
`plain-<sid8>` state even when the TODO line's `Master plan: [[...]]` wikilink is
unresolved — an orphan that never traces to a spine. This PreToolUse hook
intercepts the exact mint command
(`pre_plan_gates.py create-topic ... --intake-source plain --todo-line-ref P:N`),
reads TODO line N, and BLOCKS (exit 2) only when a `Master plan` wikilink is
present AND resolves to no file in the project tree. Conservative: a genuinely
plain TODO (no Master-plan wikilink) or an uncertain resolution is ALLOWED, so
legitimate /work-start is never false-blocked. The reaper
(reap_orphan_plain_topics.py) cleans pre-existing orphans; this prevents new ones.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

_CREATE_RE = re.compile(r"create-topic\b")
_PLAIN_RE = re.compile(r"--intake-source\s+plain\b")
_REF_RE = re.compile(r"--todo-line-ref\s+(\S+)")
_MASTER_RE = re.compile(r"Master plan:.*?\[\[(?P<stem>[^\]|#]+?)(?:\.md)?(?:[|#][^\]]*)?\]\]")


def _resolves(stem: str, project_root: str) -> bool:
    """True if a `<stem>.md` file exists anywhere under project_root (vault-wide,
    bounded to this project subtree). Uncertain -> treat as resolved (don't block)."""
    direct = [os.path.join(project_root, f"{stem}.md"),
              os.path.join(project_root, "Thoughts", f"{stem}.md")]
    if any(os.path.isfile(p) for p in direct):
        return True
    try:
        hits = glob.glob(os.path.join(project_root, "**", f"{stem}.md"), recursive=True)
        return bool(hits)
    except OSError:
        return True  # can't tell -> don't block


def evaluate(command: str) -> str | None:
    """Return a block message if this is an orphan-minting create-topic, else None."""
    if not (_CREATE_RE.search(command) and _PLAIN_RE.search(command)):
        return None
    ref = _REF_RE.search(command)
    if not ref or ":" not in ref.group(1):
        return None
    todo_path, _, line_s = ref.group(1).rpartition(":")
    try:
        line_no = int(line_s)
    except ValueError:
        return None
    try:
        with open(todo_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
    except OSError:
        return None
    if not (1 <= line_no <= len(lines)):
        return None
    mm = _MASTER_RE.search(lines[line_no - 1])
    if not mm:
        return None  # genuinely plain (no Master-plan wikilink) -> allow
    stem = mm.group("stem").strip()
    project_root = os.path.dirname(os.path.abspath(todo_path))
    if _resolves(stem, project_root):
        return None
    return (f"BLOCKED: /work-start would mint orphan `plain-*` state — the "
            f"`Master plan: [[{stem}]]` wikilink in {todo_path}:{line_no} resolves to no "
            f"file under {project_root}. Fix or remove the wikilink before /work-start "
            f"(bookkeeping-model §15 Row 1).")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    cmd = (payload.get("tool_input", {}) or {}).get("command", "") or ""
    msg = evaluate(cmd)
    if msg:
        sys.stderr.write(msg + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

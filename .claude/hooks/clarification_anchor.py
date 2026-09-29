#!/usr/bin/env python3
"""clarification_anchor — anchor-by-discovery nudge on a `_THOUGHT.md` write (DS8 #16 / Row 7).

bookkeeping-model §12: the first write to a tracked surface should prompt the
operator to anchor the work (`/work-frame-and-create-todo`). A cold
`/clarification`'s new `_THOUGHT.md` is its own anchor seed — the nudge fires
AFTER the file exists. This hook is purely ADVISORY (exit 0, never blocks): a
blocking gate would disrupt `/clarification`'s multi-write gated flow. It stays
silent once the Thought is anchored (its slug appears on TODO.md).
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bookkeeping_invariant as bi


def _nearest_todo(start_dir: str, levels: int = 6) -> str | None:
    d = start_dir
    for _ in range(levels):
        cand = os.path.join(d, "TODO.md")
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return None


def check(file_path: str) -> str | None:
    """Return an advisory message if the _THOUGHT is unanchored, else None."""
    if not file_path.endswith("_THOUGHT.md"):
        return None
    m = bi.classify(os.path.basename(file_path))
    if m.type != "THOUGHT" or not m.slug:
        return None
    todo = _nearest_todo(os.path.dirname(os.path.abspath(file_path)))
    if todo is None:
        return None
    try:
        with open(todo, "r", encoding="utf-8", errors="replace") as fh:
            if m.slug in fh.read():
                return None  # already anchored
    except OSError:
        return None
    return (f"anchor-by-discovery: `{os.path.basename(file_path)}` has no TODO.md line "
            f"(slug `{m.slug}` not found in {todo}). Consider `/work-frame-and-create-todo` "
            f"to anchor this Thought (bookkeeping-model §12). Advisory only.")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    fp = (payload.get("tool_input", {}) or {}).get("file_path", "") or ""
    msg = check(fp)
    if msg:
        sys.stderr.write(msg + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

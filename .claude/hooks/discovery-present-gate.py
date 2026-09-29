#!/usr/bin/env python3
"""Stop hook: enforce canonical display via Read tool during discovery DECISION.

Scoped via session state file presence — exits 0 immediately when no
discovery-present session is active. Two checks when active:

1. Current assistant turn must include a Read tool call whose input
   file_path matches the canonical_path from the session state file.
   Without this, the canonical would not be displayed inline in chat.

2. Current assistant turn's text content must not duplicate the canonical
   (>30% of non-trivial canonical lines appearing as substrings in AI
   text). Prevents double-display where AI pastes canonical in chat text
   in addition to calling Read.

Exit codes:
- 0: pass (no state, stale state, malformed transcript, or checks pass)
- 2: block with stderr retry instruction fed back to Claude

Cloned from watchlist-present-gate.py. Parameterized for discovery:
    STATE_PATH = session-state/discovery-present-active.json
    Message prefix: "discovery-present-gate"
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta

STATE_PATH = os.path.expanduser(
    '~/.claude/session-state/discovery-present-active.json')
STATE_TTL_HOURS = 2
OVERLAP_THRESHOLD = 0.3
MIN_LINE_LENGTH = 20


def _normalize(text):
    """Lowercase + collapse whitespace."""
    return re.sub(r'\s+', ' ', text.lower()).strip()


def _load_state():
    """Read session state JSON. Returns None if absent/stale/malformed."""
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    started_at_str = (state.get('started_at') or '').rstrip('Z')
    try:
        started_at = datetime.fromisoformat(started_at_str)
    except ValueError:
        return None
    if datetime.utcnow() - started_at > timedelta(hours=STATE_TTL_HOURS):
        return None
    if not state.get('canonical_path'):
        return None
    return state


def _parse_transcript(transcript_path):
    """Parse JSONL transcript, return list of entry dicts."""
    entries = []
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def _current_turn_assistants(entries):
    """Collect assistant entries from the last user turn to end of transcript."""
    current = []
    for entry in reversed(entries):
        if entry.get('type') == 'user':
            break
        if entry.get('type') == 'assistant':
            current.append(entry)
    return list(reversed(current))


def _collect_tool_and_text(assistant_entries):
    """Return (read_file_paths, text_blocks) from assistant turn content."""
    read_paths = []
    text_blocks = []
    for entry in assistant_entries:
        message = entry.get('message') or {}
        content = message.get('content')
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get('type')
            if btype == 'tool_use' and block.get('name') == 'Read':
                file_path = (block.get('input') or {}).get('file_path')
                if file_path:
                    read_paths.append(file_path)
            elif btype == 'text':
                text = block.get('text')
                if text:
                    text_blocks.append(text)
    return read_paths, text_blocks


def main():
    try:
        stdin_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    state = _load_state()
    if state is None:
        sys.exit(0)

    canonical_path = state['canonical_path']
    transcript_path = stdin_data.get('transcript_path')
    if not transcript_path or not os.path.exists(transcript_path):
        sys.exit(0)

    try:
        with open(canonical_path) as f:
            canonical_content = f.read()
    except OSError:
        sys.exit(0)

    entries = _parse_transcript(transcript_path)
    if not entries:
        sys.exit(0)
    current = _current_turn_assistants(entries)
    if not current:
        sys.exit(0)

    read_paths, text_blocks = _collect_tool_and_text(current)

    abs_canonical = os.path.realpath(canonical_path)
    matched_read = any(
        os.path.realpath(p) == abs_canonical for p in read_paths
    )
    if not matched_read:
        sys.stderr.write(
            f"BLOCKED by discovery-present-gate: the canonical discovery "
            f"assessment has not been displayed inline. Call Read({canonical_path!r}) "
            f"so the user sees the full canonical via the tool result. Do not "
            f"paste canonical content in your chat text.\n"
        )
        sys.exit(2)

    canonical_lines = [
        ln.strip() for ln in canonical_content.splitlines()
        if len(ln.strip()) > MIN_LINE_LENGTH
    ]
    if canonical_lines:
        normalized_text = _normalize(' '.join(text_blocks))
        if normalized_text:
            matches = sum(
                1 for ln in canonical_lines
                if _normalize(ln) in normalized_text
            )
            ratio = matches / len(canonical_lines)
            if ratio > OVERLAP_THRESHOLD:
                sys.stderr.write(
                    f"BLOCKED by discovery-present-gate: your chat text "
                    f"duplicates {matches}/{len(canonical_lines)} canonical "
                    f"lines ({ratio:.0%}, threshold {OVERLAP_THRESHOLD:.0%}). "
                    f"Do not paste canonical content in your text response — "
                    f"the Read tool result already displays it. Keep chat "
                    f"text to per-ticker observations and the approval prompt.\n"
                )
                sys.exit(2)

    sys.exit(0)


if __name__ == '__main__':
    main()

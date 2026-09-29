#!/bin/bash
# UserPromptSubmit hook — per-turn liveness refresh for this session's topic locks.
#
# streamed-dancing-goose S4 (A4). Bumps `last_heartbeat` on every lock the session
# holds so a reader sees "this session was here a moment ago". FAIL-OPEN AND SILENT
# BY CONTRACT: this event injects hook stdout into the session's context and a
# non-zero exit blocks the prompt, so this script prints nothing and exits 0
# unconditionally — a refresh that cannot run costs one stale heartbeat, never a
# blocked turn.
#
# What it does NOT deliver: UserPromptSubmit fires only when a person sends a
# prompt. During an autonomous run the heartbeat goes stale; the process liveness
# check in `taskmanagement.acquire_lock` (pid + start time) is what actually keeps
# a working session's lock (C2). This refresh is a convenience signal.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
[ -z "$SESSION_ID" ] && exit 0

HOOK_DIR="$(dirname "$0")"
python3 "$HOOK_DIR/taskmanagement.py" heartbeat --session-id "$SESSION_ID" >/dev/null 2>&1 || true
exit 0

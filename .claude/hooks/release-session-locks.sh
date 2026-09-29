#!/bin/bash
# SessionEnd hook — release every topic lock this session holds.
#
# streamed-dancing-goose S4 (A4). A NEW registration on SessionEnd: the hook
# already registered there (`cleanup-plan-files.sh`) deletes stale plan files and
# never touches a lock, so there was nothing to extend. Each lock is MOVED ASIDE
# to a timestamped sibling (`<key>.lock.bak-<stamp>-<pid>`), never deleted —
# `taskmanagement._move_aside` is exclusive-create, so a backup never overwrites
# an existing one. Audit row: `session-end-release` in `_releases.jsonl`.
#
# Reports what it did on stdout (SessionEnd output is not injected into any
# context), exits 0 unconditionally: a release that fails leaves a lock the
# liveness probe reads as DEAD once this process is gone, so the lazy reclaim
# and the SessionStart sweep still end it (C3/C9).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"
[ -z "$SESSION_ID" ] && exit 0

HOOK_DIR="$(dirname "$0")"
python3 "$HOOK_DIR/taskmanagement.py" release-session --session-id "$SESSION_ID" 2>/dev/null || true
exit 0

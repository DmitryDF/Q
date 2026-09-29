#!/bin/bash
# SessionStart hook — sweep topic locks whose holder is PROVABLY dead.
#
# streamed-dancing-goose S4 (A4). This is the named event that runs the sweep;
# without a registered trigger a batch sweep is dead code that silently fails to
# deliver C9. The predicate is `taskmanagement.sweep_dead_locks` and it is
# conjunctive, never blanket — a payload moves ONLY when it carries the S4
# identity AND is not this session's AND its process probes DEAD (confirmed, not
# UNKNOWN) AND its heartbeat is older than STALE_T. A pre-S4 (legacy) payload is
# never touched: its recorded pid is a helper that always reads dead, so a
# dead-pid predicate is true of a LIVE session's legacy lock too.
#
# Prints one line to stdout only when something was swept (SessionStart stdout
# reaches the session's context; silence is the common case). Exits 0 always.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat 2>/dev/null)
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID="${CLAUDE_CODE_SESSION_ID:-}"

HOOK_DIR="$(dirname "$0")"
if [ -n "$SESSION_ID" ]; then
  OUT=$(python3 "$HOOK_DIR/taskmanagement.py" sweep --session-id "$SESSION_ID" 2>/dev/null)
else
  OUT=$(python3 "$HOOK_DIR/taskmanagement.py" sweep 2>/dev/null)
fi
COUNT=$(printf '%s' "$OUT" | jq -r '.swept | length' 2>/dev/null)
if [ -n "$COUNT" ] && [ "$COUNT" != "0" ] && [ "$COUNT" != "null" ]; then
  KEYS=$(printf '%s' "$OUT" | jq -r '[.swept[].topic_slug] | join(", ")' 2>/dev/null)
  echo "sweep-dead-locks: moved aside $COUNT lock(s) whose session process is gone: $KEYS (backups beside them in ~/.claude/state/locks/, audit in _releases.jsonl)"
fi
exit 0

#!/bin/bash
# S4 — Stop-event hook: catch an omitted /work-done (project-tracking-staleness Phase 2).
#
# Thin adapter (skill-location.md standalone-check-logic): all judgment lives in the
# vendor-neutral module `check_work_done_omission.py`; this wrapper only reads the
# Stop stdin, applies the standard guards, calls the module, and translates its
# decision into an exit code.
#
# Fail-open contract (plan A3 guard rails): treat ONLY an explicit exit 2 from the
# module as a BLOCK. ANY other non-zero (missing interpreter, syntax error, uncaught
# error) → exit 0 — a wrapper/interpreter failure must never hard-block a session
# close (the E1 "block only on confidence" rule). The module also fails open
# internally; this is the belt-and-suspenders adapter layer.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

# Empty stdin → nothing to decide (fail-open).
[ -z "$INPUT" ] && exit 0

# Loop guard — do not re-fire on our own block.
STOP_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$STOP_ACTIVE" = "true" ] && exit 0

SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

MODULE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/check_work_done_omission.py"
[ -f "$MODULE" ] || exit 0   # module missing → fail-open

python3 "$MODULE" "$SESSION_ID"
RC=$?

# ONLY an explicit exit 2 blocks; every other code (0, or any error) fails open.
if [ "$RC" -eq 2 ]; then
  exit 2
fi
exit 0

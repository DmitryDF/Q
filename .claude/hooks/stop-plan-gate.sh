#!/bin/bash
# Stop hook: plan gate enforcement for plan-mode sessions.
# Session-scoped via .manifest.json so concurrent sessions don't
# interfere with each other's gate verdicts.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

# Only enforce on plan-mode session stops
PERM_MODE=$(echo "$INPUT" | jq -r '.permission_mode // empty')
[ "$PERM_MODE" != "plan" ] && exit 0

# Prevent infinite loop
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$STOP_ACTIVE" = "true" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAN_FILE=$("$SCRIPT_DIR/find-session-plan.sh" "$SESSION_ID") || exit 0

"$SCRIPT_DIR/check-plan-gates.sh" "$PLAN_FILE"
GATE_RESULT=$?
if [ "$GATE_RESULT" -ne 0 ]; then
  # Gate check failed — mark file so it doesn't block future sessions
  if ! grep -q '<!-- STALE -->' "$PLAN_FILE"; then
    echo "" >> "$PLAN_FILE"
    echo "<!-- STALE -->" >> "$PLAN_FILE"
  fi
fi
exit "$GATE_RESULT"

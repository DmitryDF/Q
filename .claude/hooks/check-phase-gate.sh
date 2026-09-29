#!/bin/bash
# Stop hook: phase-gate enforcement for sessions with an active phase unit.
# Checks for open decision checkpoints that must be resolved before stopping.
# Mirrors stop-plan-gate.sh / check-plan-gates.sh armature (Slice A).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

# Prevent infinite loop
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$STOP_ACTIVE" = "true" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

STATUS=$(python3 "$SCRIPT_DIR/pre_plan_gates.py" phase-status "$SESSION_ID" 2>/dev/null)
[ $? -ne 0 ] && exit 0  # Error resolving state — pass through

PHASE=$(echo "$STATUS" | jq -r '.phase // empty' 2>/dev/null)
[ -z "$PHASE" ] || [ "$PHASE" = "null" ] && exit 0  # phase=None → authority not engaged

DC=$(echo "$STATUS" | jq -c '.open_decision_checkpoint' 2>/dev/null)
if [ "$DC" != "null" ] && [ -n "$DC" ]; then
  DESC=$(echo "$DC" | jq -r '.description // ""' 2>/dev/null)
  echo "✗ Session has an open decision checkpoint: \"$DESC\"" >&2
  echo "  Resolve before stopping:" >&2
  echo "  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py decision-checkpoint $SESSION_ID resolve --auth '<!-- DC_AUTH:$SESSION_ID -->'" >&2
  exit 2
fi

exit 0

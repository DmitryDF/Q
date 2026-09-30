#!/bin/bash
# PreToolUse: EnterPlanMode — block if clarification completed this session.
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "EnterPlanMode" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

CLARIFICATION_PHASE_SID=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" get-clarification-phase-session "$SESSION_ID" 2>/dev/null)

if [ "$CLARIFICATION_PHASE_SID" = "$SESSION_ID" ]; then
    echo "✗ Session boundary: clarification completed this session." >&2
    echo "Each stage requires a separate fresh session." >&2
    echo "Run /close → /clear → start plan mode in a new session." >&2
    exit 2
fi
exit 0

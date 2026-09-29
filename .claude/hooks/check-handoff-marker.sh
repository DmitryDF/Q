#!/bin/bash
# Stop hook: warn if clarification completed this session but handoff marker is missing.
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

CLARIFICATION_PHASE_SID=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" get-clarification-phase-session "$SESSION_ID" 2>/dev/null)

# Only act if clarification was completed THIS session
[ "$CLARIFICATION_PHASE_SID" != "$SESSION_ID" ] && exit 0

# Get Thought file path
THOUGHT_FILE=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" get-thought-file "$SESSION_ID" 2>/dev/null)
[ -z "$THOUGHT_FILE" ] && exit 0

# Resolve relative path; fall back to $PWD if CLAUDE_PROJECT_DIR unset
if [[ "$THOUGHT_FILE" != /* ]]; then
    THOUGHT_FILE="${CLAUDE_PROJECT_DIR:-$PWD}/${THOUGHT_FILE}"
fi

if [ -f "$THOUGHT_FILE" ]; then
    if ! grep -qi "next session prompt" "$THOUGHT_FILE"; then
        echo "⚠️  Handoff marker missing in $(basename "$THOUGHT_FILE")." >&2
        echo "Step 10 of /clarification requires a '## Next Session Prompt' section." >&2
        echo "Add it before closing." >&2
    fi
else
    echo "⚠️  Thought file not found: $THOUGHT_FILE" >&2
fi

# Soft warning — never block session end
exit 0

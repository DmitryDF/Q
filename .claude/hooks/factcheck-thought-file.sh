#!/bin/bash
# PostToolUse hook — triggers factcheck-thought on Write/Edit to *_THOUGHT.md
# ONLY when all 5 pre-plan gates are complete for the active topic.
# Intermediate gate writes (gates 0-4 not yet all complete) do not trigger.
# Debounce is handled by the engine (factcheck_run debounce_seconds=30).
# PostToolUse CANNOT block (tool already ran). Exit 0 always.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Only process *_THOUGHT.md files
case "$FILE_PATH" in
  *_THOUGHT.md) ;;
  *) exit 0 ;;
esac

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

# Gates-complete guard: dispatch only when all 5 gates are done.
# `pre_plan_gates.py read SESSION_ID` returns session state JSON with top-level
# "gates" dict. If absent or < 5 entries, gates are not complete — skip dispatch.
STATE=$(python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" read "$SESSION_ID" 2>/dev/null)
if [ $? -ne 0 ] || [ -z "$STATE" ]; then
  exit 0
fi

GATE_COUNT=$(echo "$STATE" | jq '.gates | length // 0' 2>/dev/null)
[ "${GATE_COUNT:-0}" -lt 5 ] && exit 0

# Background dispatch — engine handles 30s debounce and flock concurrency
nohup python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" factcheck-thought \
  "$SESSION_ID" "$FILE_PATH" \
  > "/tmp/factcheck-thought-${SESSION_ID}.log" 2>&1 &

exit 0

#!/bin/bash
# PostToolUse hook — triggers factcheck-plan on Write/Edit to ~/.claude/plans/*.md.
# Debounce is handled by the engine (factcheck_run debounce_seconds=30).
# PostToolUse CANNOT block (tool already ran). Exit 0 always.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Only process plan files — legacy harness path OR project-side
# <project_root>/Thoughts/<project_slug>_PLAN.md (Phase 1 migration).
case "$FILE_PATH" in
  "$HOME/.claude/plans/"*.md) ;;
  */Thoughts/*_PLAN.md) ;;
  *) exit 0 ;;
esac

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

# Background dispatch — engine handles 30s debounce and flock concurrency
nohup python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" factcheck-plan \
  "$SESSION_ID" "$FILE_PATH" \
  > "/tmp/factcheck-plan-${SESSION_ID}.log" 2>&1 &

exit 0

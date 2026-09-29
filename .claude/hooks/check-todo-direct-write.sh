#!/usr/bin/env bash
# PostToolUse hook — direct TODO.md write validation (bookkeeping-model drift Row 3).
# Matchers (settings.json): Write | Edit  (path-filtered to **/TODO.md inside).
# Read-only: surfaces a diary-link gap, never patches. Edit's net-new Done line
# missing a `[[date]]` blocks (exit 2); a full Write WARNs only (exit 0) to avoid
# flagging pre-existing debt. All parsing happens in todo.py (no jq juggling).
# Canon: ~/.claude/rules/bookkeeping-model.md §11 (diary links) + §15 Row 3.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
case "$TOOL_NAME" in
  Write|Edit) ;;
  *) exit 0 ;;
esac

printf '%s' "$INPUT" | python3 "${KIT_HOOKS_DIR}/todo.py" validate-todo-write
exit $?

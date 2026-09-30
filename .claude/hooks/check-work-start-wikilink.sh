#!/usr/bin/env bash
# PreToolUse hook — block orphan plain-* topic-state mints (DS8 #11 / Row 1).
# Matcher (settings.json): Bash. Exits 2 ONLY for a create-topic --intake-source
# plain whose TODO Master-plan wikilink is unresolved; exit 0 otherwise.
# Canon: ~/.claude/rules/bookkeeping-model.md §15 Row 1.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "Bash" ] && exit 0

printf '%s' "$INPUT" | python3 "${KIT_HOOKS_DIR}/check_work_start_wikilink.py"
exit $?

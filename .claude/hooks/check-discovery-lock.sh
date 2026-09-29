#!/usr/bin/env bash
# PreToolUse on Edit, Write — blocks edits to DISCOVERY_LOCKED_FIELDS in _THOUGHT.md files
# unless `clarification_active_session` for that file matches the current SESSION_ID.
# Exit 0 = allow. Exit 2 = block (message on stderr).
INPUT=$(cat)
echo "$INPUT" | python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/_discovery_lock_check.py"
exit $?

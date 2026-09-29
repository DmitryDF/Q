#!/bin/bash
# SessionStart hook: snapshots git status at the beginning of a session.
# Used by session-scope.sh to compute git delta (files changed during session).
#
# Trigger: SessionStart

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
PROJECT=$(echo "$INPUT" | jq -r '.cwd // "unknown"')

[ -z "$SESSION_ID" ] && exit 0
[ "$PROJECT" = "unknown" ] || [ -z "$PROJECT" ] && exit 0

LOG_DIR="$PROJECT/.claude/logs"
mkdir -p "$LOG_DIR"

# Save filenames only (strip status codes) for reliable comparison
git -C "$PROJECT" status --porcelain -uno 2>/dev/null | awk '{print $NF}' | sort > "$LOG_DIR/_git_snapshot-$SESSION_ID"
exit 0

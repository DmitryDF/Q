#!/bin/bash
# Safety net before context compaction
# Creates marker in project's log directory

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
PROJECT=$(echo "$INPUT" | jq -r '.cwd // "unknown"')

# Log into project directory; fall back to global if cwd is unknown
if [ "$PROJECT" = "unknown" ] || [ -z "$PROJECT" ]; then
  LOG_DIR="$HOME/.claude/logs/_unknown"
else
  LOG_DIR="$PROJECT/.claude/logs"
fi
mkdir -p "$LOG_DIR"

# Check if there are any log files to protect
HAS_PROMPTS=$(ls "$LOG_DIR"/prompts-*.log 2>/dev/null | head -1)
HAS_OUTPUTS=$(ls "$LOG_DIR"/outputs/*.md 2>/dev/null | head -1)

if [ -n "$HAS_PROMPTS" ] || [ -n "$HAS_OUTPUTS" ]; then
  echo "$(date -Iseconds)" > "$LOG_DIR/_compaction_marker"
fi

exit 0

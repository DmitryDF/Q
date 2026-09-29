#!/bin/bash
# Log user prompts into the project directory
# Triggered on UserPromptSubmit hook
# Logs to <project>/.claude/logs/
# Echoes SESSION_ID to stdout — visible to AI in conversation

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
PROMPT=$(echo "$INPUT" | jq -r '.prompt')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
PROJECT=$(echo "$INPUT" | jq -r '.cwd // "unknown"')

[ -z "$SESSION_ID" ] && exit 0

# Log into project directory; fall back to global if cwd is unknown
if [ "$PROJECT" = "unknown" ] || [ -z "$PROJECT" ]; then
  LOG_DIR="$HOME/.claude/logs/_unknown"
else
  LOG_DIR="$PROJECT/.claude/logs"
fi
mkdir -p "$LOG_DIR"

LOGFILE="$LOG_DIR/prompts-$(date +%Y%m%d)-$SESSION_ID.log"
echo "$(date +%H:%M:%S)|$PROMPT" >> "$LOGFILE"

# UserPromptSubmit stdout is shown to AI in conversation
echo "SESSION_ID=$SESSION_ID"
exit 0

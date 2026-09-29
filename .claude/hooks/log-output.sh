#!/bin/bash
# Log substantial Claude outputs for accurate diary timestamps
# Triggered on Stop hook (Claude finishes responding)
# Writes to <project>/.claude/logs/outputs/
# Filter: responses over 500 chars, excluding tool confirmations

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TRANSCRIPT_PATH=$(echo "$INPUT" | jq -r '.transcript_path')
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

# Exit if no transcript or session
[ -z "$TRANSCRIPT_PATH" ] || [ "$TRANSCRIPT_PATH" = "null" ] && exit 0
[ -z "$SESSION_ID" ] && exit 0

# Determine project path from cwd
PROJECT=$(echo "$INPUT" | jq -r '.cwd // "unknown"')

# Log into project directory; fall back to global if cwd is unknown
if [ "$PROJECT" = "unknown" ] || [ -z "$PROJECT" ]; then
  LOG_DIR="$HOME/.claude/logs/_unknown"
else
  LOG_DIR="$PROJECT/.claude/logs"
fi
OUTPUT_DIR="$LOG_DIR/outputs"
mkdir -p "$OUTPUT_DIR"

# Get last assistant message from transcript (JSONL format)
LAST_RESPONSE=$(jq -s -r '
  [.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | last // empty
' "$TRANSCRIPT_PATH" 2>/dev/null)

[ -z "$LAST_RESPONSE" ] && exit 0

# Skip short responses (confirmations, one-liners)
[ ${#LAST_RESPONSE} -lt 500 ] && exit 0

# Skip tool confirmations
if printf '%s\n' "$LAST_RESPONSE" | grep -qE '^(File created|File updated|The file|Updated\.)'; then
  exit 0
fi

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
LOGFILE="$OUTPUT_DIR/$TIMESTAMP-$SESSION_ID.md"

printf '%s\n' "---" > "$LOGFILE"
printf '%s\n' "timestamp: $(date -Iseconds)" >> "$LOGFILE"
printf '%s\n' "session_id: $SESSION_ID" >> "$LOGFILE"
printf '%s\n' "project: $PROJECT" >> "$LOGFILE"
printf '%s\n' "---" >> "$LOGFILE"
printf '\n' >> "$LOGFILE"
printf '%s\n' "$LAST_RESPONSE" >> "$LOGFILE"

exit 0

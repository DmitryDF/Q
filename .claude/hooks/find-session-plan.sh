#!/bin/bash
# Finds the most recently modified plan file owned by a given session.
# Used by permission-plan-gate.sh and stop-plan-gate.sh to ensure each
# session's gate check inspects its own plan file, not whichever file
# happens to be newest on disk globally.
#
# Usage: find-session-plan.sh <session_id>
# Exit codes:
#   0 = plan file path printed on stdout
#   1 = no eligible plan file for this session (or bad inputs)

SESSION_ID="$1"
[ -z "$SESSION_ID" ] && exit 1

PLANS_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plans"
MANIFEST="$PLANS_DIR/.manifest.json"

[ ! -d "$PLANS_DIR" ] && exit 1
[ ! -f "$MANIFEST" ] && exit 1

BEST=""
BEST_MTIME=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  [ ! -f "$f" ] && continue
  MTIME=$(stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null)
  [ -z "$MTIME" ] && continue
  if [ "$MTIME" -gt "$BEST_MTIME" ]; then
    BEST="$f"
    BEST_MTIME="$MTIME"
  fi
done < <(jq -r --arg sid "$SESSION_ID" '.[$sid] // [] | .[]' "$MANIFEST" 2>/dev/null)

[ -z "$BEST" ] && exit 1
echo "$BEST"
exit 0

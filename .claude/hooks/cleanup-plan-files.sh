#!/bin/bash
# SessionEnd hook: deletes plan files created by this session.
# Reads manifest written by track-plan-file.sh, removes this
# session's files, cleans up the manifest entry.
# Also sweeps orphaned entries older than 7 days as fallback.

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')

PLANS_DIR="$HOME/.claude/plans"
MANIFEST="$PLANS_DIR/.manifest.json"

[ ! -f "$MANIFEST" ] && exit 0
[ -z "$SESSION_ID" ] && exit 0

(
  flock -w 5 200 || exit 0
  CURRENT=$(cat "$MANIFEST" 2>/dev/null || echo '{}')

  # Delete this session's plan files.
  # Path-prefix filter: only auto-delete legacy harness-side plans under
  # ~/.claude/plans/. Project-side plans (<project>/Thoughts/<topic>_PLAN.md)
  # are version-controlled and survive session end.
  # DS5a: Mode-C ninja-plans now also live under <project>/Thoughts/ (with
  # `bookkeeping: mode-c` frontmatter) and are tracked in the manifest by
  # track-plan-file.sh. They fall through the path-prefix filter below and are
  # NEVER auto-deleted — Thoughts/ Mode-C plans are durable, not 7-day-swept.
  # while-read (not `for $FILES`) — paths may contain spaces (iCloud root).
  echo "$CURRENT" | jq -r --arg sid "$SESSION_ID" '.[$sid] // [] | .[]' | while IFS= read -r f; do
    case "$f" in
      "$PLANS_DIR/"*) [ -f "$f" ] && rm -f "$f" ;;
      *) ;;  # project-side plans: never auto-delete
    esac
  done

  # Remove session entry from manifest
  UPDATED=$(echo "$CURRENT" | jq --arg sid "$SESSION_ID" 'del(.[$sid])')

  echo "$UPDATED" > "$MANIFEST"
) 200>"$MANIFEST.lock"

# Fallback: delete orphaned plan files older than 7 days
find "$PLANS_DIR" -maxdepth 1 -name '*.md' -type f -mtime +7 -delete 2>/dev/null

# Clean up todo.py first-prompt markers for this session
rm -f "$HOME/.claude/session-state/_todo-read-$SESSION_ID" 2>/dev/null

# Fallback: sweep orphan todo markers older than 1 day
find "$HOME/.claude/session-state" -maxdepth 1 -name '_todo-read-*' -type f -mtime +1 -delete 2>/dev/null

exit 0

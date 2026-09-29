#!/bin/bash
# UserPromptSubmit advisory nudge — soft layer, never blocks.
# Detects new-topic-shaped prompts and injects a system-reminder pointing to /clarification.
#
# Heuristic (new-topic-shaped = all of the following):
#   - Not a slash command (prompt does not start with /)
#   - Not a short ack (prompt length < 5 words)
#   - Not a continuation (prompt does not start with common ack phrases)
#   - No active topic for this session (post-Slice-F: replaces the legacy
#     classification check; topic-exists signal comes from `pre_plan_gates.py
#     read` returning a status other than `no_state_file`)
#
# Exit code: 0 always. UserPromptSubmit must not block; the previous
# `set -euo pipefail` + 2>/dev/null combination silently translated any
# inner-command failure (e.g. a removed CLI subcommand) into exit 2, which
# Claude Code reads as a block with no diagnostic. Errors are now logged to
# ~/.claude/state/hook-debug/clarification-nudge.log instead of being swallowed.

set -uo pipefail

DEBUG_LOG="${HOME}/.claude/state/hook-debug/clarification-nudge.log"
mkdir -p "$(dirname "$DEBUG_LOG")" 2>/dev/null || true
log() { printf "%s %s\n" "$(date -u +%FT%TZ)" "$*" >> "$DEBUG_LOG" 2>/dev/null || true; }

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('session_id', ''))
" 2>>"$DEBUG_LOG") || { log "session_id parse failed"; exit 0; }

PROMPT=$(echo "$INPUT" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('prompt', ''))
" 2>>"$DEBUG_LOG") || { log "prompt parse failed"; exit 0; }

[ -z "$PROMPT" ] && exit 0
[ -z "$SESSION_ID" ] && exit 0

# Session-boundary fallback: warn if clarification phase completed this session
CLARIFICATION_PHASE_SID=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" get-clarification-phase-session "$SESSION_ID" 2>>"$DEBUG_LOG") || true
if [ "$CLARIFICATION_PHASE_SID" = "$SESSION_ID" ]; then
  cat <<'EOF'
---
[Session boundary] Clarification completed this session. Do NOT enter plan mode now.
Each stage requires a separate fresh session.
Run /close → /clear → start plan mode in a new session and paste the "Next Session Prompt" from the Thought file.
---
EOF
  exit 0
fi

# Skip slash commands
if [[ "$PROMPT" == /* ]]; then
  exit 0
fi

# Skip short acks (< 5 words)
WORD_COUNT=$(echo "$PROMPT" | wc -w | tr -d ' ')
if [ "$WORD_COUNT" -lt 5 ]; then
  exit 0
fi

# Skip common continuation phrases
LOWER_PROMPT=$(echo "$PROMPT" | tr '[:upper:]' '[:lower:]' | head -c 50)
for ACK in "ok" "yes" "no" "sure" "go ahead" "apply" "continue" "proceed" "that" "looks good" "great" "done" "thanks"; do
  if [[ "$LOWER_PROMPT" == "$ACK"* ]]; then
    exit 0
  fi
done

# M10 (project-tracking-staleness S2): a pasted session-handoff prompt — a
# message carrying paired <topic> + <instructions> blocks (per the
# prompt-engineering.md "Handoff Prompts" template) — is a PRE-VERIFIED
# directive to execute, NOT a new topic to frame. Skip the nudge even in an
# unbound session (where topic-orient would return `new`). Recognition is the
# shared E4 anchor handoff_structure.py (single locus; the S4 Stop hook reuses
# it). Use printf '%s' (never echo) so a prompt containing -e/-n/backslashes is
# passed intact — matches the printf '%s' "$PROMPT" idiom below. Fail-open: any
# non-zero exit (not-a-handoff OR a python error, logged) falls through to the
# existing nudge logic; only a definite exit-0 match skips.
if printf '%s' "$PROMPT" | python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/handoff_structure.py" is-handoff 2>>"$DEBUG_LOG"; then
  exit 0
fi

# Slice C (cryptic-mapping-torvalds.md C4): consult topic-orient first.
# topic-orient owns spine + plan + state + prompt-body inference. On
# verdict mid-flight | stalled, suppress the nudge — the prompt references
# (or the session already owns) an active topic and the user is not in
# new-topic territory.
ORIENT_JSON=$(printf '%s' "$PROMPT" | python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" \
  topic-orient "$SESSION_ID" --prompt-body - 2>>"$DEBUG_LOG") || {
  log "topic-orient failed for $SESSION_ID — falling through to legacy read check"
  ORIENT_JSON=""
}

if [ -n "$ORIENT_JSON" ]; then
  ORIENT_VERDICT=$(printf '%s' "$ORIENT_JSON" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('verdict', ''))
except Exception:
    print('')
" 2>>"$DEBUG_LOG") || ORIENT_VERDICT=""
  case "$ORIENT_VERDICT" in
    mid-flight|stalled)
      exit 0
      ;;
  esac
fi

# Skip if a topic exists for this session — work is already framed.
# Post-Slice-F: classification was removed; topic-exists comes from `read`
# returning a status other than `no_state_file`. Retained as a fallback for
# the case where topic-orient errored out and ORIENT_JSON is empty.
TOPIC_RAW=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" read "$SESSION_ID" 2>>"$DEBUG_LOG") || {
  log "read CLI failed for $SESSION_ID — falling through to nudge"
  TOPIC_RAW=""
}

TOPIC_STATUS=$(printf '%s' "$TOPIC_RAW" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(data.get('status', 'has_state'))
except Exception:
    print('parse_error')
" 2>>"$DEBUG_LOG") || TOPIC_STATUS="parse_error"

# Suppress the nudge only on positive evidence of an active topic.
# parse_error / empty / no_state_file fall through to the nudge.
case "$TOPIC_STATUS" in
  ""|no_state_file|parse_error)
    : # fall through to nudge
    ;;
  *)
    exit 0
    ;;
esac

# Inject advisory nudge via stdout (becomes system context)
cat <<'EOF'
---
[Clarification notice] This looks like a new topic. Before diving in, consider running /clarification to frame the problem and create a structured Thoughts file. For trivial upstream-surfaced code errors, use /ninja-fix instead.

This is advice, not a gate — nothing here blocks you, and you can proceed without framing.
---
EOF

exit 0

#!/usr/bin/env bash
# PostToolUse hook on ExitPlanMode — Slice I (S-I-Impl-2 / G9 / C9).
#
# F10 v5 hard-gated post-exit UX. After the user approves `ExitPlanMode`,
# this hook sets a session-scoped marker that the matcher-`.*` PreToolUse
# hook `check-post-plan-pending.sh` reads to block every tool other than
# `AskUserQuestion` until the AI has surfaced the (a) generate-handoff-
# prompt / (b) `/clear` choice and the user has picked.
#
# The marker is consumed + cleared by
# `pre_plan_gates.py clear-post-plan-choice <SESSION_ID>` when the AI
# acts on the user's reply.
#
# Feasibility (S-I-Impl-2 A6e spike): PostToolUse fires on ExitPlanMode
# per Claude Code hooks docs (verified 2026-05-22). Documented Stop-event
# fallback remains available if the harness changes.
#
# Exit codes:
#   0 always (no-op on non-ExitPlanMode; marker write on ExitPlanMode).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "ExitPlanMode" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

MARKER_DIR="$HOME/.claude/state/post_plan_choice"
mkdir -p "$MARKER_DIR"
MARKER="$MARKER_DIR/${SESSION_ID}.marker"

TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
cat > "$MARKER" <<EOF
{
  "session_id": "$SESSION_ID",
  "pending": true,
  "set_at": "$TS"
}
EOF

exit 0

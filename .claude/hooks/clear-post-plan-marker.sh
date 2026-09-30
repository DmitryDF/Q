#!/usr/bin/env bash
# PostToolUse hook on AskUserQuestion — Slice I follow-up (fixes the
# `check-post-plan-pending.sh` self-deadlock surfaced 2026-05-23, SID
# d0d9a868).
#
# Background. The PreToolUse hook `check-post-plan-pending.sh` (matcher
# `.*`) lets only AskUserQuestion through while
# `~/.claude/state/post_plan_choice/<SID>.marker` has `pending: true`.
# Its stderr instructed the AI to call
# `pre_plan_gates.py clear-post-plan-choice <SID>` via Bash — but Bash
# was blocked by the same hook. Deadlock.
#
# Fix. After the user answers the (a)/(b) AskUserQuestion that the
# /plan skill surfaces post-ExitPlanMode, this PostToolUse hook
# auto-clears the marker. Hook subprocesses are not subject to the
# PreToolUse gate, so no deadlock.
#
# Safety. While pending=true, the PreToolUse gate permits exactly one
# tool: AskUserQuestion. Therefore any AskUserQuestion that completes
# during the pending window is, by exclusion, the gate's own question.
# There is no false-positive class.
#
# Exit codes:
#   0 always (no-op on non-AskUserQuestion; marker clear on match).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "AskUserQuestion" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

MARKER="$HOME/.claude/state/post_plan_choice/${SESSION_ID}.marker"
[ ! -f "$MARKER" ] && exit 0

PENDING=$(jq -r '.pending // false' "$MARKER" 2>/dev/null)
[ "$PENDING" != "true" ] && exit 0

python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" clear-post-plan-choice "$SESSION_ID" >/dev/null 2>&1
exit 0

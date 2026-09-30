#!/usr/bin/env bash
# PreToolUse hook (matcher `.*`) — Slice I (S-I-Impl-2 / G9 / C9).
#
# F10 v5 hard-gated post-exit UX gate. After `post-plan-uxgate.sh` sets
# `~/.claude/state/post_plan_choice/<SID>.marker` with `pending=true`,
# this hook blocks every tool other than `AskUserQuestion` until the
# `/plan` skill has surfaced the (a)/(b) choice and the AI has cleared
# the marker via `pre_plan_gates.py clear-post-plan-choice`.
#
# Early-exit pattern modeled on `check-kb-disposition-gate.sh:18` —
# matcher is broad (`.*`); the in-script `TOOL_NAME` allow-list lets the
# permitted tool through while every other call is blocked.
#
# Exit codes:
#   0 = allow (no marker, marker not pending, or TOOL_NAME=AskUserQuestion)
#   2 = block (marker pending and TOOL_NAME is not AskUserQuestion)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

MARKER="$HOME/.claude/state/post_plan_choice/${SESSION_ID}.marker"
[ ! -f "$MARKER" ] && exit 0

PENDING=$(jq -r '.pending // false' "$MARKER" 2>/dev/null)
[ "$PENDING" != "true" ] && exit 0

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)

# Allow-list: only AskUserQuestion can fire while the post-plan UX gate
# is pending. Every other tool is blocked with the same surface message.
if [ "$TOOL_NAME" = "AskUserQuestion" ]; then
  exit 0
fi

cat >&2 <<EOF
BLOCKED: Post-plan UX gate pending.

The plan was just approved. Before any other tool runs, invoke
AskUserQuestion with the three options the /plan skill specifies:

  (a) Generate handoff prompt to proceed in fresh Implementation session
      (calls /prompt-for-handoff with the bound _thought file for Mode
      A/B, or with the plan file for Mode C state-agnostic mode).
  (b) /close + /clear and pick another topic.
  (c) Continue in this session — exit plan mode and implement here.
      Suited to small/single-action plans where carrying plan-mode
      context is fine and a fresh session would be overhead.

The marker is cleared automatically by the PostToolUse hook
\`clear-post-plan-marker.sh\` once AskUserQuestion completes. After
the user picks, proceed with the chosen branch — no CLI call needed.
EOF
exit 2

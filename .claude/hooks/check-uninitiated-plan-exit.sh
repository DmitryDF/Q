#!/bin/bash
# PreToolUse hook (matcher .*): halt-and-surface guard for un-initiated plan-mode exits.
#
# Background (2026-07-06): a successful Write to a non-designated file during plan mode
# makes the harness silently leave plan mode with NO ExitPlanMode and NO approval. The
# primary fix (check-plan-readonly.sh) blocks the known trigger, but this guard is the
# defense-in-depth backstop for ANY residual/novel cause: if a session left plan mode
# without a recorded ExitPlanMode grant, halt the assistant and force it to surface the
# anomaly to the user before continuing (audit f1f82bd9 Deviation 2 — the assistant
# absorbed a silent exit and kept working).
#
# Fire condition (all true):
#   - permission_mode != plan          (we are already out of plan mode)
#   - plan_mode_init/<sid>.json present (this session DID enter plan mode)
#   - post_plan_choice/<sid>.marker ABSENT (no legitimate ExitPlanMode grant recorded;
#       the marker persists after a real grant, and plan-mode-init clears it per episode)
#   - plan_exit_ack/<sid>.marker ABSENT (not yet acknowledged this episode)
#
# One-shot per plan-mode episode: after the assistant surfaces the anomaly it clears the
# guard with `pre_plan_gates.py plan-exit-ack <sid>`; `plan-mode-init` re-arms it (clears
# both the ack and the stale grant) on the next plan-mode entry.
#
# Escape hatches (never blocked): AskUserQuestion (so the assistant can surface to the
# user) and the `plan-exit-ack` command itself (so the assistant can clear the guard).
#
# Exit codes: 0 = allow, 2 = block (silent exit detected, not yet surfaced/acked).

INPUT=$(cat)

PERM_MODE=$(echo "$INPUT" | jq -r '.permission_mode // empty')
# Only relevant once we are OUT of plan mode.
[ "$PERM_MODE" = "plan" ] && exit 0

SID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SID" ] && exit 0

STATE="$HOME/.claude/state"

# Did this session ever enter plan mode? If not, nothing to guard.
[ ! -f "$STATE/plan_mode_init/$SID.json" ] && exit 0

# Legitimate ExitPlanMode grant recorded? (marker persists after a real exit) → allow.
[ -f "$STATE/post_plan_choice/$SID.marker" ] && exit 0

# Already surfaced/acknowledged this episode? → allow.
[ -f "$STATE/plan_exit_ack/$SID.marker" ] && exit 0

# Escape hatches so the assistant can surface + clear the guard.
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" = "AskUserQuestion" ] && exit 0
CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
case "$CMD" in
  *plan-exit-ack*) exit 0 ;;
esac

# Silent exit detected, not yet surfaced → BLOCK and instruct halt-and-surface.
echo "BLOCKED: plan mode ended WITHOUT your ExitPlanMode approval — a silent plan-mode" >&2
echo "exit (the 2026-07-06 bug class). You must NOT continue working on the unapproved plan." >&2
echo "" >&2
echo "STOP now and surface this to the user: tell them plan mode ended without their" >&2
echo "approval and ask how they want to proceed. Only after you have surfaced it, clear" >&2
echo "this guard with:" >&2
echo "  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py plan-exit-ack $SID" >&2
exit 2

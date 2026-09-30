#!/usr/bin/env bash
# PreToolUse hook (matcher "Skill") — resume-scoped /execute-plan checkpoint
# (execplan-resume-gate-fix A1, 2026-07-10). REPLACES the retired SessionStart-arm
# + PreToolUse-`.*` blanket block, which armed at SessionStart and blocked EVERY
# tool — collaterally hard-blocking unrelated and parallel sessions.
#
# This gate fires ONLY on the one concrete resume action: a /execute-plan skill
# invocation. If a per-run pointer exists for a run in THIS session's worktree that
# this session neither owns nor has acked, it blocks that invocation and asks the
# operator to orient (Investigate / Proceed / Not now). An unrelated session never
# invokes /execute-plan, so it is never gated.
#
# All deterministic logic lives in run.py (execplan-gate-check); this hook is a
# thin adapter (code_first_architecture.md — hexagonal). Honors
# EXECPLAN_ACK_STATE_DIR.
#
# Exit codes: 0 = allow; 2 = block (orientation pending).
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "Skill" ] && exit 0

# Only the /execute-plan invocation is the resume action. Match the bare name and
# any plugin-qualified form (`<plugin>:execute-plan`).
SKILL=$(echo "$INPUT" | jq -r '.tool_input.skill // empty' 2>/dev/null)
case "$SKILL" in
  execute-plan|*:execute-plan) : ;;
  *) exit 0 ;;
esac

SID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SID" ] && exit 0        # no session id → cannot scope a run; default-open

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
# Worktree root scopes visibility (git-policy.md §3). Empty when not in a git tree
# — run.py then treats the run as globally visible (legacy/migrated fallback).
WORKTREE=""
if [ -n "$CWD" ]; then
  WORKTREE=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
fi

RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
DECISION=$(jq -nc --arg sid "$SID" --arg wt "$WORKTREE" \
  '{session_id:$sid, worktree_root:$wt}' \
  | python3 "$RUN_PY" execplan-gate-check 2>/dev/null)

BLOCK=$(echo "$DECISION" | jq -r '.block // false' 2>/dev/null)

# A5: a run nobody is walking is LISTED, not acknowledged. It costs no decision and
# must not block — but it is still SHOWN, because the gate exists so the operator
# looks at each parked plan. Dropping the listing here would trade one Guiding Policy
# commitment for another. Advisory: printed to stderr, then exit 0.
if [ "$BLOCK" != "true" ]; then
  ARMED=$(echo "$DECISION" | jq -r '.armed_count // 0' 2>/dev/null)
  if [ -n "${ARMED:-}" ] && [ "$ARMED" -gt 0 ] 2>/dev/null; then
    {
      printf 'execplan: %s parked run(s) in this worktree — approved but nobody is walking them.\n' "$ARMED"
      printf 'No decision needed; listed so they stay visible:\n'
      # Carries last-activity age, exactly as the blocking path's inventory does.
      # Without it this listing gives the operator no basis to judge how long a run
      # has sat — which is the whole point of surfacing it (gap G4).
      echo "$DECISION" | jq -r '
        .listed[]? |
        "  • \(.slug)  next slice \(.slice_id)   last activity: " +
        (if .age_seconds == null then "unknown"
         elif .age_seconds < 60    then "\(.age_seconds)s ago"
         elif .age_seconds < 3600  then "\(.age_seconds / 60 | floor)m ago"
         elif .age_seconds < 86400 then "\(.age_seconds / 3600 | floor)h ago"
         else "\(.age_seconds / 86400 | floor)d ago" end)'
      printf 'Resume one with /execute-plan, or leave them parked.\n'
    } >&2
  fi
  exit 0
fi

# Print the orientation prompt to stderr (fd1 → fd2). NOTE ordering: a trailing
# `2>/dev/null >&2` would point stdout at /dev/null (fd1 follows fd2's *current*
# target), silently swallowing the message — so redirect stdout to stderr only.
echo "$DECISION" | jq -r '.message // empty' >&2
exit 2

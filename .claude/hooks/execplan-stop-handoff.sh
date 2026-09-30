#!/usr/bin/env bash
# Stop hook: /execute-plan GENUINE-STOP handoff + walker reset
# (execplan-contract-hardening Slice S6, 2026-07-20).
#
# Code-enforces design v3.1 section 2c (Thoughts/execplan-contract-hardening-*_PLAN.md
# Coherent Action A6, Gap G3): a walk that stops mid-way for anything OTHER than a
# contract reason (escalation / deadlock / abort / plan-detour -- those already leave
# their own explicit state) must not silently strand a slice as "in flight forever".
# When THIS session is the recorded `walking_session_id` for an in-flight, INCOMPLETE
# run and the stop is GENUINE (not a loop re-fire, not a mid-turn pause waiting on a
# confirm-gate/AskUserQuestion), this hook (a) computes the next-ready-slice handoff
# via the SAME `compute-handoff` verb the cross-session resume path already uses, (b)
# arms it onto the run pointer's `pending_handoff` field while KEEPING
# `execution_pending:true`, AND (c) EXPLICITLY null-resets `walking_session_id` and
# `current_slice_id` -- so the run returns to "pending, no active walker, no checked-
# out slice" and the NEXT session's entry gate (check-execplan-entry-gate.sh, S3)
# re-engages exactly as if no one had ever started walking
# (code_first_architecture.md -- "Defend boundaries with code, not goodwill").
#
# This hook is a THIN ADAPTER. All decision logic that CAN live in run.py already
# does (compute_handoff / set_active_run's preserve-vs-reset semantics [C5]); the one
# thing this hook resolves that no run.py verb resolves itself is which in-flight run
# (if any) THIS session is the walker for -- a READ-ONLY scan of the same
# run-<id>.json pointers run.py owns (mirrors check-execplan-walk-gate.sh's own
# resolution -- no field written here). The genuine-stop discriminator is then
# evaluated RUN-SCOPED against THAT resolved run R.
#
# Genuine-stop discriminator (RUN-SCOPED against the resolved walker run R):
#   this session == R.walking_session_id  (resolved FIRST; scopes everything to R)
#   AND R is incomplete                   (compute-handoff non-null, or completed<total)
#   AND stop_hook_active == false         (not a Stop-hook re-fire / loop-guard)
#
# WHY NO CONFIRM-GATE PROXY (S6 conformance fix, 2026-07-20). Design v3.1 section 2c
# listed "no pending confirm-gate/AskUserQuestion armed" as a third condition, and an
# earlier draft implemented it by reading the session-scoped `pending-<sid>.json`
# marker. That is the WRONG signal and was DROPPED: run.py `gate_check()` writes
# `pending-<sid>.json` for a DIFFERENT run G that gates a FUTURE /execute-plan
# invocation by this session (run.py:2791-2815 explicitly `continue`s past any run
# this session owns and records G's run_id in the marker) -- it has nothing to do with
# R's walk or a confirm-gate pause. Reading it session-scoped, before resolving R,
# MISCLASSIFIED a genuine stop of R as a pause whenever the session also held a
# pending marker for an unrelated run G -- stranding R until GC/TTL (the exact C3
# regression the conformance check flagged). The proxy is unnecessary anyway: a
# confirm-gate AskUserQuestion is a MID-TURN TOOL CALL, so the Stop hook does not fire
# during it at all (Stop fires only when the assistant's turn ends with no pending
# tool call). The run-scoped discriminator above is the complete and correct signal.
#
# Incompleteness discriminator (per the plan's Slice S6 contract): the run is
# INCOMPLETE iff EITHER (a) `compute-handoff` returns a non-null next-ready-slice
# handoff, OR (b) the completed-slice count (read from the run's `state_path`) is
# less than its `total_slices`. (a) is checked first and is the common case; (b) is
# the fallback for a deadlocked register (slices remain but none are ready) -- there
# is no slice to hand off in that case, so the walker/checkout fields are still reset
# (the entry gate must re-engage) but no `pending_handoff` is armed. A run that is
# actually complete (or whose completeness cannot be determined) is left untouched --
# a genuine no-op, matching the normal completion path's own state management.
#
# Hook hygiene (mandatory, non-negotiable -- mirrors check-execplan-entry-gate.sh /
# check-execplan-walk-gate.sh):
#   - fail-OPEN on any infra error (missing python3/run.py, unexpected verb
#     failure/non-JSON output, unresolvable register) -> exit 0, no state change.
#   - fully NON-INTERACTIVE -- never reads from or prompts a TTY.
#   - `BYPASS_EXECPLAN=1` disables this hook entirely, but the bypass is LOGGED
#     (never silent) -- the SAME bypass log the entry/walk gates write to.
#   - Honors EXECPLAN_ACK_STATE_DIR for test isolation (the same env var the S1/S2
#     run.py code and the S3/S4 hooks read for the pointer dir + bypass log root).
#
# This hook NEVER blocks the stop itself -- it only arms/records state as a SIDE
# EFFECT of a genuine stop. Exit code is always 0.

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && exit 0

INPUT=$(cat)

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)

STATE_ROOT="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state}"
BYPASS_LOG="$STATE_ROOT/execplan/bypass.log"

# BYPASS_EXECPLAN=1 -- operator-recovery override. Always logged, never silent.
if [ "${BYPASS_EXECPLAN:-}" = "1" ]; then
  mkdir -p "$(dirname "$BYPASS_LOG")" 2>/dev/null
  printf '%s session=%s hook=Stop BYPASS_EXECPLAN=1 -- stop-handoff disabled for this call\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SESSION_ID:-unknown}" \
    >> "$BYPASS_LOG" 2>/dev/null
  echo "execplan-stop-handoff: BYPASS_EXECPLAN=1 -- stop-handoff disabled for this call (logged: $BYPASS_LOG)" >&2
  exit 0
fi

# Loop-guard: a Stop event re-fired because a PRIOR Stop hook blocked is not a
# genuine end-of-walk stop -- never arm/reset on it (mirrors check-dc-obligation-stop.sh).
[ "$STOP_ACTIVE" = "true" ] && exit 0

# No session id surfaced -> cannot scope a run to a walker; default-open.
[ -z "$SESSION_ID" ] && exit 0

POINTER_DIR="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state/execplan_session_ack}"
[ -d "$POINTER_DIR" ] || exit 0

RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
[ -f "$RUN_PY" ] || exit 0                          # fail-open: infra error
command -v python3 >/dev/null 2>&1 || exit 0        # fail-open: infra error

# --- Resolve which in-flight run (if any) THIS session is the walker for. ---
# READ-ONLY scan of the same run-<id>.json pointers run.py itself owns (mirrors
# check-execplan-walk-gate.sh); CWD/worktree is used as a best-effort extra filter
# when resolvable, never a hard requirement (a Stop event's cwd is not load-bearing
# the way a PreToolUse target path is).
WORKTREE=""
if [ -n "$CWD" ]; then
  WORKTREE=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
fi

POINTER_FILE=""
for f in "$POINTER_DIR"/run-*.json; do
  [ -f "$f" ] || continue
  WALKING=$(jq -r '.walking_session_id // empty' "$f" 2>/dev/null)
  [ "$WALKING" = "$SESSION_ID" ] || continue
  if [ -n "$WORKTREE" ]; then
    PWT=$(jq -r '.worktree_root // empty' "$f" 2>/dev/null)
    if [ -n "$PWT" ] && [ "$PWT" != "$WORKTREE" ]; then
      continue   # worktree-scoped to a DIFFERENT worktree -- not this one
    fi
  fi
  POINTER_FILE="$f"
  break
done
# This session is not the walker for any run -> nothing to arm/reset. NO-OP.
[ -z "$POINTER_FILE" ] && exit 0

SURFACE_PATH=$(jq -r '.surface_path // empty' "$POINTER_FILE" 2>/dev/null)
OWNER=$(jq -r '.owner_session_id // empty' "$POINTER_FILE" 2>/dev/null)
TOTAL_SLICES=$(jq -r '.total_slices // empty' "$POINTER_FILE" 2>/dev/null)
STATE_PATH=$(jq -r '.state_path // empty' "$POINTER_FILE" 2>/dev/null)
# A malformed pointer (no surface_path to resolve the register from) -> fail-open.
[ -z "$SURFACE_PATH" ] && exit 0

# --- Resolve the slice_execution map (mirrors run.py's _pointer_completed_count:
# unwrap .slice_execution if present, else use the whole doc; default {} on any
# read/parse failure). ---
EXECMAP='{}'
if [ -n "$STATE_PATH" ] && [ -f "$STATE_PATH" ]; then
  EXTRACTED=$(jq -c 'if type=="object" and has("slice_execution") then .slice_execution else . end' \
    "$STATE_PATH" 2>/dev/null)
  if [ -n "$EXTRACTED" ] && [ "$EXTRACTED" != "null" ]; then
    EXECMAP="$EXTRACTED"
  fi
fi

# --- (a) compute the handoff, via the SAME verb the cross-session resume reads. ---
DECISION=$(jq -nc --arg sp "$SURFACE_PATH" --argjson exec "$EXECMAP" \
  '{spine_path:$sp, slice_execution:$exec}' \
  | python3 "$RUN_PY" compute-handoff 2>/dev/null)

STATUS=$(echo "$DECISION" | jq -r '.status // empty' 2>/dev/null)
# Any parse/verb failure (empty DECISION, malformed JSON, unreadable spine) ->
# fail-open. Only a genuine OK result is acted on.
[ "$STATUS" != "OK" ] && exit 0

HANDOFF=$(echo "$DECISION" | jq -c '.handoff // null' 2>/dev/null)
[ -z "$HANDOFF" ] && HANDOFF="null"

INCOMPLETE=0
if [ "$HANDOFF" != "null" ]; then
  INCOMPLETE=1
else
  # (b) fallback: completed-count < total_slices (deadlock -- slices remain but none
  # are ready; no handoff record to arm, but the walker still must be released so the
  # entry gate re-engages for the next session to investigate).
  COMPLETED=$(echo "$EXECMAP" | jq '[.[]? | select(type=="object" and .status=="completed")] | length' 2>/dev/null)
  case "$COMPLETED" in ''|*[!0-9]*) COMPLETED=0 ;; esac
  case "$TOTAL_SLICES" in ''|null|*[!0-9]*) TOTAL_SLICES="" ;; esac
  if [ -n "$TOTAL_SLICES" ] && [ "$COMPLETED" -lt "$TOTAL_SLICES" ]; then
    INCOMPLETE=1
  fi
fi

# Run is complete (or completeness cannot be determined) -> genuine NO-OP, matching
# the normal completion path's own state management.
[ "$INCOMPLETE" = "1" ] || exit 0

# --- (b) arm the handoff onto the pointer + (c) EXPLICITLY null-reset the walker /
# checkout fields. Re-supplies EVERY existing pointer field verbatim (owner_session_id,
# surface_path, surface_kind, total_slices, worktree_root, state_path) because
# cmd_set_active_run REBUILDS its output from the payload alone for these fields (only
# execution_pending/walking_session_id/current_slice_id preserve-on-omit) -- omitting
# any of them here would silently WIPE it from the re-armed pointer. ---
NEW_PAYLOAD=$(jq -c --argjson handoff "$HANDOFF" \
  '{owner_session_id, surface_path, surface_kind, total_slices, worktree_root, state_path,
    execution_pending: true, walking_session_id: null, current_slice_id: null}
   + (if $handoff != null then {pending_handoff: $handoff} else {} end)' \
  "$POINTER_FILE" 2>/dev/null)

if [ -n "$NEW_PAYLOAD" ]; then
  echo "$NEW_PAYLOAD" | python3 "$RUN_PY" set-active-run >/dev/null 2>&1
fi

exit 0

#!/usr/bin/env bash
# SessionStart hook -- surfaces a parked /execute-plan handoff before it can be
# silently garbage-collected (execplan-contract-hardening Slice S6, 2026-07-20;
# design v3.1 section 6 residual (iii): "24h TTL park ... closed by optional
# execplan-scan-pending SessionStart re-arm; without it a >24h parked plan stops
# gating").
#
# execplan-gc.sh (registered AFTER this hook in settings.json) reaps any run
# pointer that is provably complete OR older than the TTL -- including a run that
# is genuinely PARKED (execution_pending:true, walking_session_id:null,
# pending_handoff SET by execplan-stop-handoff.sh) awaiting resume. Once reaped,
# the pointer -- and with it the entry-gate enforcement AND the resumable handoff
# -- silently disappears. This hook runs BEFORE the reap and prints an
# informational reminder for any pointer in that exact parked shape, so an
# operator sees it and can resume (or explicitly abandon) it before the TTL
# window closes. Read-only -- it never writes or reaps anything itself; that
# stays execplan-gc.sh's job.
#
# Hook hygiene (mirrors execplan-gc.sh / execplan-stop-handoff.sh):
#   - fail-OPEN / silent on any infra error -- SessionStart output is advisory only.
#   - fully NON-INTERACTIVE.
#   - Honors EXECPLAN_ACK_STATE_DIR for test isolation.
#
# Exit codes: 0 always (SessionStart hooks do not gate; stdout is advisory context).

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && exit 0

POINTER_DIR="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state/execplan_session_ack}"
[ -d "$POINTER_DIR" ] || exit 0

command -v jq >/dev/null 2>&1 || exit 0

# A6 / gap G4 widening: the scan used to require `pending_handoff != null`, so it
# listed ONLY a parked run that had already reached a slice boundary and been handed
# off. A pointer armed at /plan Step 11 — execution_pending true, no walker, no
# handoff yet — was surfaced NOWHERE until it blocked somebody. That is the shape
# every freshly-approved plan starts in, and the one most likely to be forgotten.
# The handoff requirement is dropped; a run with no handoff simply shows "—".
#
# `has("walking_session_id")` is required as well as the null test, so this stays
# consistent with the resume gate: a legacy pointer missing the field entirely is
# treated there as in-flight, not parked, and must not be advertised here as parked.
FOUND=0
for f in "$POINTER_DIR"/run-*.json; do
  [ -f "$f" ] || continue
  PENDING=$(jq -c '. as $d
    | ($d.execution_pending == true)
      and ($d | has("walking_session_id"))
      and ($d.walking_session_id == null)' "$f" 2>/dev/null)
  [ "$PENDING" = "true" ] || continue
  if [ "$FOUND" = "0" ]; then
    echo "execplan-scan-pending: parked /execute-plan run(s) awaiting resume --"
    FOUND=1
  fi
  SURFACE=$(jq -r '.surface_path // "unknown"' "$f" 2>/dev/null)
  SLICE=$(jq -r '.current_slice_id // .pending_handoff.slice_id // "—"' "$f" 2>/dev/null)
  TYPE=$(jq -r '.pending_handoff.type // "not yet handed off"' "$f" 2>/dev/null)
  UPDATED=$(jq -r '.updated_at // "unknown"' "$f" 2>/dev/null)
  echo "  - $SURFACE  (next slice: $SLICE, handoff: $TYPE, last updated: $UPDATED)"
done

[ "$FOUND" = "1" ] && echo "  Run /execute-plan to resume, or abandon via clear-active-run."

exit 0

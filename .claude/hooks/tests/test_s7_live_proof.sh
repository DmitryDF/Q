#!/usr/bin/env bash
# =============================================================================
# S7 — closing implementation_verification: live two-session proof for the
#      /execute-plan CONTRACT-HARDENING change (execplan-contract-hardening,
#      2026-07-20). Plan: Thoughts/execplan-contract-hardening-20260720120500_PLAN.md
#      Coherent Action A7; design v3.1 §7.
#
# WHAT THIS PROVES
#   The ASSEMBLED system (S1 run.py domain + S2 receipts/walk-gate + S3 entry
#   gate hook + S4 walk gate hook + S6 stop-handoff hook) delivers outcome
#   claims C1–C6 END-TO-END. This harness was NOT written by the author of the
#   hooks/verbs (producer-never-verifies): it DRIVES the real components and
#   observes their real exit codes / receipt verdicts / pointer state.
#
# THE "TWO SESSIONS" MODEL (honest scope — read before trusting the result)
#   The three gates discriminate sessions ENTIRELY by the `session_id` field in
#   the PreToolUse / Stop stdin JSON, and by the `walking_session_id` recorded on
#   the on-disk run pointer. Nothing else about "a session" enters the decision.
#   The S0 spike proved PreToolUse does NOT fire inside a spawned subagent, and
#   the clone's settings.json intentionally registers the hooks at LIVE paths (so
#   promotion is correct) — therefore a clone-config `claude` session cannot
#   self-dispatch the NEW hooks, and installing them at live paths would violate
#   never-edit-live. So this proof drives the REAL hooks + REAL run.py verbs with
#   THREE DISTINCT session_id values:
#       SESSION_A     — the elected walker
#       SESSION_B     — a concurrent second session
#       SESSION_FRESH — a fresh entrant (and the handoff-resume session)
#   Because the gate logic reads session_id from stdin and the walker id from the
#   pointer and NOTHING ELSE, driving distinct session_id values FAITHFULLY
#   exercises the entry / walker-exemption / concurrency / handoff-resume logic.
#   This is the honest boundary of the proof: it exercises the code paths a real
#   multi-session run would hit, via the same inputs the harness feeds the hooks.
#
# ISOLATION (mandatory — zero writes outside /tmp + the temp state dir)
#   * All run state lives under a fresh EXECPLAN_ACK_STATE_DIR (mktemp -d).
#   * The worktrees are EPHEMERAL `git init` repos created under /tmp.
#   * NOTHING is ever written to the live ~/.claude/ namespace, to the clone's
#     own dirs, or to the real Projects repo. A verifier-isolation snapshot of
#     live ~/.claude/{settings.json,hooks/*} therefore shows NO drift.
#   * TEARDOWN IS NON-DESTRUCTIVE: at the end the ephemeral base dir is `mv`'d to
#     a timestamped trash dir under /tmp — NEVER `rm -rf`, NEVER `git reset
#     --hard`, NEVER an index.lock removal, on ANY path.
#
# EXIT: 0 iff every assertion passes; non-zero + a summary on any failure.
# =============================================================================

set -u

CLONE_ROOT="~/.claude-staging-execplan-hardening"
RUN_PY="$CLONE_ROOT/skills/execute-plan/run.py"
ENTRY_HOOK="$CLONE_ROOT/hooks/check-execplan-entry-gate.sh"
WALK_HOOK="$CLONE_ROOT/hooks/check-execplan-walk-gate.sh"
STOP_HOOK="$CLONE_ROOT/hooks/execplan-stop-handoff.sh"

# --- Ephemeral, isolated state. Everything under one base dir on /tmp. ---
BASE=$(mktemp -d "/tmp/execplan-s7-XXXXXX") || { echo "mktemp failed"; exit 3; }
export EXECPLAN_ACK_STATE_DIR="$BASE/state"
mkdir -p "$EXECPLAN_ACK_STATE_DIR"
# Ensure the hooks never early-exit on the remote guard.
export CLAUDE_CODE_REMOTE="false"
# Never let a stray operator override leak in.
unset BYPASS_EXECPLAN 2>/dev/null || true

# Distinct session ids — the ONLY thing the gates discriminate sessions by.
SESSION_PLAN="s7-plan-approver"   # the /plan approving session that arms the pointer
SESSION_A="s7-session-A-walker"
SESSION_B="s7-session-B-concurrent"
SESSION_FRESH="s7-session-FRESH"

PASS=0
FAIL=0

# NON-DESTRUCTIVE teardown: mv the whole ephemeral base to a timestamped /tmp
# trash dir. NEVER rm -rf, NEVER git reset, NEVER remove an index.lock.
TRASH=""
teardown() {
  TRASH="/tmp/execplan-s7-trash-$(date +%Y%m%d-%H%M%S)-$$"
  mkdir -p "$TRASH" 2>/dev/null
  if mv "$BASE" "$TRASH/" 2>/dev/null; then
    echo ""
    echo "TEARDOWN (non-destructive): moved ephemeral base"
    echo "  from: $BASE"
    echo "  to:   $TRASH/$(basename "$BASE")   (mv, NOT rm — recoverable)"
  else
    echo "TEARDOWN WARNING: could not mv $BASE (left in place; NOT rm'd)"
  fi
}
trap teardown EXIT

pass() { echo "  PASS  $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL  $1"; FAIL=$((FAIL + 1)); }

# check_exit <label> <expected_exit> <actual_exit> [observed-note]
check_exit() {
  local label="$1" exp="$2" got="$3" note="${4:-}"
  if [ "$got" -eq "$exp" ]; then
    pass "$label  (exit=$got${note:+, $note})"
  else
    fail "$label  (expected exit $exp, got $got${note:+, $note})"
  fi
}

# check_eq <label> <expected> <actual>
check_eq() {
  local label="$1" exp="$2" got="$3"
  if [ "$exp" = "$got" ]; then
    pass "$label  (observed='$got')"
  else
    fail "$label  (expected '$exp', observed '$got')"
  fi
}

# --- PreToolUse stdin JSON builders (exactly the harness envelope shape) ---
mk_write_json() {   # <session_id> <cwd> <file_path>
  jq -nc --arg sid "$1" --arg cwd "$2" --arg fp "$3" \
    '{tool_name:"Write", session_id:$sid, cwd:$cwd, tool_input:{file_path:$fp}}'
}
mk_edit_json() {    # <session_id> <cwd> <file_path>
  jq -nc --arg sid "$1" --arg cwd "$2" --arg fp "$3" \
    '{tool_name:"Edit", session_id:$sid, cwd:$cwd, tool_input:{file_path:$fp}}'
}
mk_agent_json() {   # <session_id> <cwd> <prompt>
  jq -nc --arg sid "$1" --arg cwd "$2" --arg p "$3" \
    '{tool_name:"Agent", session_id:$sid, cwd:$cwd, tool_input:{subagent_type:"claude", prompt:$p}}'
}
mk_stop_json() {    # <session_id> <cwd> <stop_hook_active:true|false>
  jq -nc --arg sid "$1" --arg cwd "$2" --argjson sha "$3" \
    '{session_id:$sid, cwd:$cwd, stop_hook_active:$sha}'
}

# drive_hook <hook_path> <json>  -> sets HOOK_OUT / HOOK_RC
drive_hook() {
  HOOK_OUT=$(printf '%s' "$2" | bash "$1" 2>&1)
  HOOK_RC=$?
}

# run_verb <subcommand> <json>  -> sets VERB_OUT / VERB_RC (real run.py)
run_verb() {
  VERB_OUT=$(printf '%s' "$2" | python3 "$RUN_PY" "$1" 2>/dev/null)
  VERB_RC=$?
}

echo "============================================================"
echo "S7 live two-session proof — /execute-plan contract hardening"
echo "  clone:        $CLONE_ROOT"
echo "  state dir:    $EXECPLAN_ACK_STATE_DIR"
echo "  sessions:     A(walker)=$SESSION_A  B(concurrent)=$SESSION_B  FRESH=$SESSION_FRESH"
echo "============================================================"

# -------------------------------------------------------------------------
# SETUP — one ephemeral git worktree with a durable MULTI-SLICE _PLAN
#         (>=2 non-closing slices), the run pointer armed via the REAL
#         set-active-run exactly as /plan Step 11 does (execution_pending:true,
#         walking_session_id:null, current_slice_id:null).
# -------------------------------------------------------------------------
echo ""
echo "--- SETUP: ephemeral worktree + /plan Step 11 pointer arm ---"

WT_RAW="$BASE/worktree"
mkdir -p "$WT_RAW"
git -C "$WT_RAW" init -q
# Canonical worktree root — the SAME path the hooks derive via
# `git rev-parse --show-toplevel` (on macOS /tmp -> /private/tmp). Using this
# everywhere keeps pointer.worktree_root == the hook-resolved worktree.
WT=$(git -C "$WT_RAW" rev-parse --show-toplevel)

SURFACE="$WT/Thoughts/execplans7proof-20260720120500_PLAN.md"
STATE_PATH="$WT/execplans7proof.run-state.json"
mkdir -p "$(dirname "$SURFACE")"

# Multi-slice register: S1, S2 are non-closing (implementation); S3 is the
# closing implementation_verification. -> 2 non-closing -> register present.
cat > "$SURFACE" <<'REGEOF'
# execplan S7 proof plan (ephemeral)

#### Slices
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | first work slice | implementation | do first thing | routine | — |
| S2 | second work slice | implementation | do second thing | routine | S1 |
| S3 | closing verification | implementation_verification | prove it | routine | S2 |
REGEOF

echo '{}' > "$STATE_PATH"

# Arm the pointer via the REAL set-active-run — the /plan Step 11 shape.
ARM_JSON=$(jq -nc --arg owner "$SESSION_PLAN" --arg surf "$SURFACE" \
                  --arg wt "$WT" --arg sp "$STATE_PATH" \
  '{owner_session_id:$owner, surface_path:$surf, worktree_root:$wt,
    surface_kind:"full", total_slices:3, state_path:$sp,
    execution_pending:true, walking_session_id:null, current_slice_id:null}')
run_verb "set-active-run" "$ARM_JSON"
RUN_ID=$(printf '%s' "$VERB_OUT" | jq -r '.run_id // empty')
POINTER=$(printf '%s' "$VERB_OUT" | jq -r '.pointer // empty')
echo "  armed pointer: run_id=$RUN_ID"
echo "                 pointer=$POINTER"
if [ -n "$RUN_ID" ] && [ -f "$POINTER" ]; then
  pass "setup: real set-active-run armed a pending pointer (walking=null, current=null)"
else
  fail "setup: set-active-run did not arm a pointer"
fi

# Demonstrate the register-presence predicate the arming decision rests on.
run_verb "register-presence" "$(jq -nc --arg s "$SURFACE" '{spine_path:$s}')"
PRESENT=$(printf '%s' "$VERB_OUT" | jq -r '.present')
NONCLOSING=$(printf '%s' "$VERB_OUT" | jq -r '.non_closing_count')
check_eq "setup: register-presence present=true for the multi-slice plan" "true" "$PRESENT"
check_eq "setup: register-presence counts 2 non-closing slices" "2" "$NONCLOSING"

# =========================================================================
# ASSERTION 1 [C1] — a FRESH session's hand-rolled Write inside the worktree
#   is BLOCKED at entry, AND a fresh session's Agent-spawn is BLOCKED at entry.
#   (Before any walker is elected: writing_session_id != walking_session_id==null.)
#   Real component driven: check-execplan-entry-gate.sh (over run.py
#   execplan-entry-check).
# =========================================================================
echo ""
echo "--- ASSERTION 1 [C1]: fresh-session Write AND Agent-spawn blocked at entry ---"

drive_hook "$ENTRY_HOOK" "$(mk_write_json "$SESSION_FRESH" "$WT" "$WT/src/hand_rolled.py")"
check_exit "1a [C1] fresh-session hand-rolled Write BLOCKED at entry gate" 2 "$HOOK_RC"

drive_hook "$ENTRY_HOOK" "$(mk_agent_json "$SESSION_FRESH" "$WT" "hand-build the slices without the orchestrator")"
check_exit "1b [C1] fresh-session Agent-spawn BLOCKED at entry gate" 2 "$HOOK_RC"

# =========================================================================
# ASSERTION 2 [C4] — after SESSION_A becomes the walker (real become-walker),
#   a CONCURRENT SESSION_B's Write in the worktree is BLOCKED during the walk,
#   while SESSION_A's own Write is ALLOWED (walker exemption). A second
#   would-be walker's become-walker CAS is REJECTED (cannot clobber SESSION_A).
#   Real components: run.py become-walker (CAS) + check-execplan-entry-gate.sh.
# =========================================================================
echo ""
echo "--- ASSERTION 2 [C4]: concurrency boundary (walker exemption + CAS) ---"

run_verb "become-walker" "$(jq -nc --arg s "$SURFACE" --arg wt "$WT" --arg sid "$SESSION_A" \
  '{surface_path:$s, worktree_root:$wt, session_id:$sid}')"
BW_OK=$(printf '%s' "$VERB_OUT" | jq -r '.ok')
BW_WALK=$(printf '%s' "$VERB_OUT" | jq -r '.walking_session_id')
check_eq "2a [C4] SESSION_A become-walker (CAS) succeeds" "true" "$BW_OK"
check_eq "2a [C4] pointer walking_session_id == SESSION_A" "$SESSION_A" "$BW_WALK"

drive_hook "$ENTRY_HOOK" "$(mk_write_json "$SESSION_B" "$WT" "$WT/src/concurrent_edit.py")"
check_exit "2b [C4] concurrent SESSION_B Write BLOCKED during the walk" 2 "$HOOK_RC"

drive_hook "$ENTRY_HOOK" "$(mk_write_json "$SESSION_A" "$WT" "$WT/src/walker_edit.py")"
check_exit "2c [C4] walker SESSION_A's own Write ALLOWED (walker exemption)" 0 "$HOOK_RC"

run_verb "become-walker" "$(jq -nc --arg s "$SURFACE" --arg wt "$WT" --arg sid "$SESSION_B" \
  '{surface_path:$s, worktree_root:$wt, session_id:$sid}')"
BW2_REJECTED=$(printf '%s' "$VERB_OUT" | jq -r '.rejected')
BW2_WALK=$(printf '%s' "$VERB_OUT" | jq -r '.walking_session_id')
check_eq "2d [C4] second would-be walker SESSION_B's CAS REJECTED" "true" "$BW2_REJECTED"
check_eq "2d [C4] walker still SESSION_A after clobber attempt" "$SESSION_A" "$BW2_WALK"

# =========================================================================
# ASSERTION 3 [C2] — record-conformance REFUSES an inline verdict payload and
#   REQUIRES captured isolated-checker outputs aggregated to PASS before a
#   conformance receipt exists.
#   Real component: run.py record-conformance (-> aggregate_round_verdict).
# =========================================================================
echo ""
echo "--- ASSERTION 3 [C2]: conformance verdict is code-aggregated, never inline ---"

CONF_SLICE="S1"
RECEIPT="$EXECPLAN_ACK_STATE_DIR/execplan/receipts/$RUN_ID/$CONF_SLICE.conformance.json"

# 3a — an inline {verdict:PASS} payload is REFUSED (exit 3), NO receipt written.
run_verb "record-conformance" "$(jq -nc --arg rid "$RUN_ID" --arg sid "$CONF_SLICE" \
  '{run_id:$rid, slice_id:$sid, verdict:"PASS",
    checkers_json:[{model:"sonnet", verdict:"PASS"}]}')"
INLINE_RC=$VERB_RC
INLINE_STATUS=$(printf '%s' "$VERB_OUT" | jq -r '.status // empty')
if [ "$INLINE_RC" -eq 3 ] && [ "$INLINE_STATUS" = "ERROR" ] && [ ! -f "$RECEIPT" ]; then
  pass "3a [C2] inline verdict REFUSED (exit=3, status=ERROR, NO receipt written)"
else
  fail "3a [C2] inline verdict not refused as expected (exit=$INLINE_RC status=$INLINE_STATUS receipt_exists=$([ -f "$RECEIPT" ] && echo yes || echo no))"
fi

# 3b — captured isolated-checker outputs aggregated to PASS -> receipt with verdict PASS.
run_verb "record-conformance" "$(jq -nc --arg rid "$RUN_ID" --arg sid "$CONF_SLICE" \
  '{run_id:$rid, slice_id:$sid, is_final:true,
    checkers_json:[{model:"sonnet", verdict:"PASS"},
                   {model:"sonnet", verdict:"PASS"},
                   {model:"sonnet", verdict:"PASS"}]}')"
AGG_VERDICT=$(printf '%s' "$VERB_OUT" | jq -r '.verdict // empty')
if [ "$VERB_RC" -eq 0 ] && [ "$AGG_VERDICT" = "PASS" ] && [ -f "$RECEIPT" ]; then
  RECEIPT_VERDICT=$(jq -r '.verdict' "$RECEIPT")
  check_eq "3b [C2] receipt written only after code-aggregated checker PASS" "PASS" "$RECEIPT_VERDICT"
else
  fail "3b [C2] aggregated PASS receipt not produced (exit=$VERB_RC verdict=$AGG_VERDICT receipt_exists=$([ -f "$RECEIPT" ] && echo yes || echo no))"
fi

# =========================================================================
# ASSERTION 4 [C2] — mid-walk, an out-of-order inline edit with NO dep-satisfied
#   slice checked out is BLOCKED by the walk gate; once the correct slice is
#   checked out (real checkout-slice over next_ready_slice) the edit is ALLOWED.
#   Real components: check-execplan-walk-gate.sh + run.py checkout-slice.
# =========================================================================
echo ""
echo "--- ASSERTION 4 [C2]: walk-gate checkout invariant (out-of-order edit) ---"

# No slice checked out yet (current_slice_id is null) -> walker's worktree edit blocked.
drive_hook "$WALK_HOOK" "$(mk_edit_json "$SESSION_A" "$WT" "$WT/src/out_of_order.py")"
OO_RC=$HOOK_RC
if [ "$OO_RC" -eq 2 ] && printf '%s' "$HOOK_OUT" | grep -q "no slice is checked out"; then
  pass "4a [C2] mid-walk edit with NO slice checked out BLOCKED (exit=2, 'no slice is checked out')"
else
  fail "4a [C2] out-of-order edit not blocked as expected (exit=$OO_RC)"
fi

# Real checkout of the next-ready slice (S1) — the exclusive current_slice_id writer.
run_verb "checkout-slice" "$(jq -nc --arg s "$SURFACE" --arg wt "$WT" --arg sp "$STATE_PATH" \
  '{surface_path:$s, worktree_root:$wt, slice_id:"S1", state_path:$sp}')"
CO_OK=$(printf '%s' "$VERB_OUT" | jq -r '.ok')
CO_CUR=$(printf '%s' "$VERB_OUT" | jq -r '.current_slice_id')
check_eq "4b [C2] checkout-slice(S1) over next_ready_slice succeeds" "true" "$CO_OK"
check_eq "4b [C2] current_slice_id now == S1" "S1" "$CO_CUR"

# With S1 checked out -> the walker's worktree edit is now allowed.
drive_hook "$WALK_HOOK" "$(mk_edit_json "$SESSION_A" "$WT" "$WT/src/slice1_impl.py")"
check_exit "4c [C2] edit ALLOWED once the ready slice is checked out" 0 "$HOOK_RC"

# =========================================================================
# ASSERTION 5 [C3] — a GENUINE mid-walk stop by SESSION_A on an incomplete run
#   arms a resumable pending_handoff AND null-resets walking_session_id /
#   current_slice_id (entry gate re-engages); whereas a confirm-gate / mid-turn
#   pause (stop_hook_active=true) does NOT arm/reset (no-op).
#   Real component: execplan-stop-handoff.sh (-> compute-handoff + set-active-run).
# =========================================================================
echo ""
echo "--- ASSERTION 5 [C3]: genuine stop arms handoff+resets vs pause no-op ---"

# 5a — a confirm-gate / mid-turn pause (stop_hook_active=true) is a NO-OP:
#      walker + checked-out slice survive; no pending_handoff armed.
drive_hook "$STOP_HOOK" "$(mk_stop_json "$SESSION_A" "$WT" true)"
PAUSE_WALK=$(jq -r '.walking_session_id // "null"' "$POINTER")
PAUSE_CUR=$(jq -r '.current_slice_id // "null"' "$POINTER")
PAUSE_PH=$(jq -r 'if .pending_handoff == null or (.pending_handoff|not) then "none" else .pending_handoff.slice_id end' "$POINTER" 2>/dev/null)
if [ "$PAUSE_WALK" = "$SESSION_A" ] && [ "$PAUSE_CUR" = "S1" ] && [ "$PAUSE_PH" = "none" ]; then
  pass "5a [C3] confirm-gate pause (stop_hook_active=true) is a NO-OP (walker=$SESSION_A, current=S1, no handoff)"
else
  fail "5a [C3] pause was not a no-op (walking=$PAUSE_WALK current=$PAUSE_CUR handoff=$PAUSE_PH)"
fi

# 5b — a GENUINE stop (stop_hook_active=false) arms a resumable handoff AND
#      null-resets walking_session_id + current_slice_id.
drive_hook "$STOP_HOOK" "$(mk_stop_json "$SESSION_A" "$WT" false)"
STOP_RC=$HOOK_RC
STOP_WALK=$(jq -r '.walking_session_id // "null"' "$POINTER")
STOP_CUR=$(jq -r '.current_slice_id // "null"' "$POINTER")
STOP_PENDING=$(jq -r '.execution_pending' "$POINTER")
STOP_PH=$(jq -r 'if .pending_handoff == null then "none" else .pending_handoff.slice_id end' "$POINTER" 2>/dev/null)
check_exit "5b [C3] stop hook never blocks the stop itself" 0 "$STOP_RC"
check_eq "5b [C3] genuine stop null-resets walking_session_id" "null" "$STOP_WALK"
check_eq "5b [C3] genuine stop null-resets current_slice_id" "null" "$STOP_CUR"
check_eq "5b [C3] run stays execution_pending after the stop" "true" "$STOP_PENDING"
if [ "$STOP_PH" != "none" ] && [ -n "$STOP_PH" ]; then
  pass "5b [C3] resumable pending_handoff armed (slice_id=$STOP_PH)"
else
  fail "5b [C3] no pending_handoff armed after genuine stop (got '$STOP_PH')"
fi

# =========================================================================
# C5 — gate enforcement holds across BOTH the in-session walk AND a handoff-
#   resume; enforcement does not depend on the AI invoking the orchestrator.
#   In-walk enforcement was shown in ASSERTION 2 (concurrent SESSION_B blocked
#   while SESSION_A walked). Here we prove the HANDOFF-RESUME half: after the
#   genuine stop re-armed the pointer (walker null again), a FRESH session STILL
#   hits the entry gate for the same run.
#   Real component: check-execplan-entry-gate.sh over the re-armed pointer.
# =========================================================================
echo ""
echo "--- C5: enforcement across in-walk (A2) + handoff-resume (below) ---"

drive_hook "$ENTRY_HOOK" "$(mk_write_json "$SESSION_FRESH" "$WT" "$WT/src/after_handoff.py")"
check_exit "C5 (handoff-resume) fresh-session Write STILL BLOCKED after stop re-armed the run" 2 "$HOOK_RC"
echo "  note: C5 (in-walk) half proven by ASSERTION 2b — concurrent SESSION_B blocked during SESSION_A's walk."

# =========================================================================
# ASSERTION 6 [C6] — a SINGLE-WORK plan (register-presence present=false: one
#   non-closing slice) arms NO pointer and the entry gate does NOT block a
#   hand-rolled write for it (non-regression).
#   Real components: run.py register-presence + check-execplan-entry-gate.sh.
# =========================================================================
echo ""
echo "--- ASSERTION 6 [C6]: single-work plan is un-gated (non-regression) ---"

WT2_RAW="$BASE/worktree-singlework"
mkdir -p "$WT2_RAW"
git -C "$WT2_RAW" init -q
WT2=$(git -C "$WT2_RAW" rev-parse --show-toplevel)
SURFACE2="$WT2/Thoughts/singlework-20260720120500_PLAN.md"
mkdir -p "$(dirname "$SURFACE2")"
cat > "$SURFACE2" <<'REGEOF'
# single-work plan (ephemeral)

#### Slices
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | the only work | implementation | do the one thing | routine | — |
| S2 | closing verification | implementation_verification | prove it | routine | S1 |
REGEOF

run_verb "register-presence" "$(jq -nc --arg s "$SURFACE2" '{spine_path:$s}')"
SW_PRESENT=$(printf '%s' "$VERB_OUT" | jq -r '.present')
SW_NONCLOSING=$(printf '%s' "$VERB_OUT" | jq -r '.non_closing_count')
check_eq "6a [C6] register-presence present=false for a single-work plan" "false" "$SW_PRESENT"
check_eq "6a [C6] single-work plan has 1 non-closing slice" "1" "$SW_NONCLOSING"

# Per /plan Step 11, a non-present register arms NO pointer -> deliberately none armed
# for WT2. A fresh hand-rolled write there is NOT blocked (no in-flight walk owns it).
drive_hook "$ENTRY_HOOK" "$(mk_write_json "$SESSION_FRESH" "$WT2" "$WT2/src/direct_impl.py")"
check_exit "6b [C6] hand-rolled Write in an un-armed single-work worktree NOT blocked" 0 "$HOOK_RC"

# =========================================================================
# SUMMARY
# =========================================================================
echo ""
echo "============================================================"
echo "S7 RESULTS:  $PASS passed, $FAIL failed."
echo "============================================================"
if [ "$FAIL" -eq 0 ]; then
  echo "OVERALL: PASS — C1–C6 + C5 all proven against the real assembled system."
  exit 0
else
  echo "OVERALL: FAIL — see the FAIL lines above."
  exit 1
fi

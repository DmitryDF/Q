#!/usr/bin/env bash
# Sentinel tests for execplan-stop-handoff.sh (Stop).
#
# execplan-contract-hardening Slice S6 (Thoughts/execplan-contract-hardening-*_PLAN.md
# Coherent Action A6, Gap G3). Everything is isolated under EXECPLAN_ACK_STATE_DIR (a
# fresh temp dir) -- NEVER the live ~/.claude/state/execplan* namespace. Each scenario
# arms real state via the SAME run.py verbs the orchestrator uses (set-active-run),
# then drives the hook exactly as the harness would -- Stop-event JSON stdin in, exit
# code + the resulting pointer JSON out. Hermetic: no network, no git config beyond
# `git init` in throwaway temp worktrees (cleaned up on exit).
#
# Hook contract: ALWAYS exits 0 (a Stop hook that only arms/resets state as a side
# effect must never itself block the stop) -- so every assertion here is about the
# resulting POINTER STATE, not the exit code alone.

set -u

HOOK="~/.claude-staging-execplan-hardening/hooks/execplan-stop-handoff.sh"
RUN_PY="~/.claude-staging-execplan-hardening/skills/execute-plan/run.py"

TMP_ROOT=$(mktemp -d -t execplan-stop-handoff-test-XXXXXX)
export EXECPLAN_ACK_STATE_DIR="$TMP_ROOT/state"
mkdir -p "$EXECPLAN_ACK_STATE_DIR"

PASS=0
FAIL=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

# mk_worktree <name> -> prints the canonical (git rev-parse'd) worktree root.
mk_worktree() {
  local dir="$TMP_ROOT/$1"
  mkdir -p "$dir"
  git -C "$dir" init -q
  git -C "$dir" rev-parse --show-toplevel
}

# compute_run_id <surface> <wt> -- MUST match run.py's compute_run_id() exactly:
# sha256(worktree_root + NUL + surface_path)[:12] (worktree FIRST).
compute_run_id() {
  local surface="$1" wt="$2"
  python3 - "$surface" "$wt" <<'PYEOF'
import hashlib, sys
surface, wt = sys.argv[1], sys.argv[2]
key = (wt + "\x00" + surface) if wt else surface
print(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12])
PYEOF
}

# arm_fresh <walking_sid|null> -- resets the run pointer to a KNOWN baseline via the
# REAL set-active-run verb: owner=OWNER_SID, surface/worktree/state_path/total_slices
# fixed, walking_session_id=<arg>, current_slice_id=null, execution_pending=true, NO
# pending_handoff (omitting it drops any prior one -- a clean slate per scenario).
arm_fresh() {
  local walking="$1" walking_json
  if [ "$walking" = "null" ]; then walking_json='null'; else walking_json="\"$walking\""; fi
  jq -nc --arg owner "$OWNER_SID" --arg surf "$SURFACE1" --arg wt "$WT1" --arg sp "$STATE_PATH" \
         --argjson walking "$walking_json" \
    '{owner_session_id:$owner, surface_path:$surf, worktree_root:$wt, state_path:$sp,
      total_slices:2, execution_pending:true, walking_session_id:$walking, current_slice_id:null}' \
    | python3 "$RUN_PY" set-active-run >/dev/null
}

# mk_stop_payload <session_id> <stop_hook_active: true|false> <cwd>
mk_stop_payload() {
  jq -nc --arg sid "$1" --argjson active "$2" --arg cwd "$3" \
    '{hook_event_name:"Stop", session_id:$sid, stop_hook_active:$active, cwd:$cwd}'
}

pointer_field() {
  local field="$1"
  jq -r ".${field} // \"null\"" "$POINTER_FILE" 2>/dev/null
}

run_hook() {
  local json="$1"
  printf '%s' "$json" | bash "$HOOK" 2>&1
  echo "EXIT:$?"
}

assert_eq() {
  local name="$1" expected="$2" got="$3"
  if [ "$got" = "$expected" ]; then
    echo "PASS  $name  (=$got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected '$expected', got '$got')"
    FAIL=$((FAIL+1))
  fi
}

echo "--- Setup ---"
WT1=$(mk_worktree wt1)
SURFACE1="$WT1/Thoughts/stophandofftest_PLAN.md"
STATE_PATH="$WT1/stophandofftest.run-state.json"
OWNER_SID="owner-sid-001"
WALKER_SID="walker-sid-101"
OTHER_SID="other-sid-202"

mkdir -p "$(dirname "$SURFACE1")"
cat > "$SURFACE1" <<'REGEOF'
# stophandofftest plan

| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|---------------|-------|------------|
| S1 | First slice | impl | do the first thing | routine | — |
| S2 | Second slice | impl | do the second thing | routine | S1 |
REGEOF

RUN_ID=$(compute_run_id "$SURFACE1" "$WT1")
POINTER_FILE="$EXECPLAN_ACK_STATE_DIR/run-$RUN_ID.json"
echo "run_id=$RUN_ID pointer=$POINTER_FILE"

echo ""
echo "--- 1: genuine stop BY THE WALKER on an incomplete run -> arms + resets ---"

echo '{}' > "$STATE_PATH"          # no slices completed -> S1 is next-ready
arm_fresh "$WALKER_SID"

OUT=$(run_hook "$(mk_stop_payload "$WALKER_SID" false "$WT1")")
EXIT_CODE=$(echo "$OUT" | grep -o 'EXIT:[0-9]*' | tail -1 | cut -d: -f2)
assert_eq "1-exit-always-0" "0" "$EXIT_CODE"

assert_eq "1-execution-pending-stays-true" "true" "$(pointer_field execution_pending)"
assert_eq "1-walking-session-id-null-reset" "null" "$(pointer_field walking_session_id)"
assert_eq "1-current-slice-id-null-reset" "null" "$(pointer_field current_slice_id)"
assert_eq "1-pending-handoff-slice-is-S1" "S1" "$(pointer_field pending_handoff.slice_id)"
assert_eq "1-pending-handoff-type" "continue-the-run" "$(pointer_field pending_handoff.type)"
assert_eq "1-state-path-preserved" "$STATE_PATH" "$(pointer_field state_path)"
assert_eq "1-total-slices-preserved" "2" "$(pointer_field total_slices)"
assert_eq "1-owner-preserved" "$OWNER_SID" "$(pointer_field owner_session_id)"

echo ""
echo "--- 2: stop by a NON-WALKER session -> NO-OP ---"

arm_fresh "$WALKER_SID"
run_hook "$(mk_stop_payload "$OTHER_SID" false "$WT1")" >/dev/null

assert_eq "2-walking-session-id-unchanged" "$WALKER_SID" "$(pointer_field walking_session_id)"
assert_eq "2-no-pending-handoff-armed" "null" "$(pointer_field pending_handoff)"

echo ""
echo "--- 3: stop_hook_active=true (loop re-fire, not genuine) -> NO-OP ---"

arm_fresh "$WALKER_SID"
run_hook "$(mk_stop_payload "$WALKER_SID" true "$WT1")" >/dev/null

assert_eq "3-walking-session-id-unchanged" "$WALKER_SID" "$(pointer_field walking_session_id)"
assert_eq "3-no-pending-handoff-armed" "null" "$(pointer_field pending_handoff)"

echo ""
echo "--- 4: stop on a COMPLETE run -> NO-OP ---"

jq -n '{S1:{status:"completed", result:{}}, S2:{status:"completed", result:{}}}' > "$STATE_PATH"
arm_fresh "$WALKER_SID"
run_hook "$(mk_stop_payload "$WALKER_SID" false "$WT1")" >/dev/null

assert_eq "4-walking-session-id-unchanged-complete-run" "$WALKER_SID" "$(pointer_field walking_session_id)"
assert_eq "4-no-pending-handoff-armed-complete-run" "null" "$(pointer_field pending_handoff)"

echo ""
echo "--- 5 (REGRESSION, S6 conformance fix): walker of incomplete R that ALSO holds a"
echo "    pending-<sid> marker for an UNRELATED run G -> R's genuine stop STILL arms + resets ---"
#
# The flagged bug: the pause-check read the session-scoped pending-<sid>.json marker
# BEFORE resolving R. That marker is written by run.py gate_check() for a DIFFERENT
# run G gating a FUTURE /execute-plan invocation (records G's run_id, run.py:2811-2815)
# -- NOT a confirm-gate pause in R's walk. A session done walking R that happens to
# hold a pending marker for G had R's genuine stop MISCLASSIFIED as a pause -> R
# stranded. The fix is run-scoped: the marker no longer participates in the
# discriminator at all. This test reproduces the exact scenario and asserts the
# genuine handoff still fires for R.

echo '{}' > "$STATE_PATH"           # R is incomplete again -> S1 next-ready
arm_fresh "$WALKER_SID"
# Simulate an UNRELATED run G that this session invoked /execute-plan for while R was
# in flight -- gate_check would have written this exact session-scoped marker, keyed
# only by session_id, recording G's (different) run_id.
jq -n --arg sid "$WALKER_SID" \
  '{session_id:$sid, pending:true, run_id:"unrelated-run-G-9999",
    slug:"other-plan", slice_id:"S3", dispatch:"attended", set_at:"2026-07-20T00:00:00Z"}' \
  > "$EXECPLAN_ACK_STATE_DIR/pending-$WALKER_SID.json"

run_hook "$(mk_stop_payload "$WALKER_SID" false "$WT1")" >/dev/null

assert_eq "5-regression-execution-pending-stays-true" "true" "$(pointer_field execution_pending)"
assert_eq "5-regression-walking-session-id-null-reset" "null" "$(pointer_field walking_session_id)"
assert_eq "5-regression-current-slice-id-null-reset" "null" "$(pointer_field current_slice_id)"
assert_eq "5-regression-pending-handoff-armed-for-R" "S1" "$(pointer_field pending_handoff.slice_id)"

rm -f "$EXECPLAN_ACK_STATE_DIR/pending-$WALKER_SID.json"

echo ""
echo "--- 6: BYPASS_EXECPLAN=1 -- disables the hook, logs the bypass ---"

arm_fresh "$WALKER_SID"
BYPASS_LOG="$EXECPLAN_ACK_STATE_DIR/execplan/bypass.log"
rm -f "$BYPASS_LOG"
export BYPASS_EXECPLAN=1
run_hook "$(mk_stop_payload "$WALKER_SID" false "$WT1")" >/dev/null
unset BYPASS_EXECPLAN

assert_eq "6-walking-session-id-unchanged-bypass" "$WALKER_SID" "$(pointer_field walking_session_id)"
if [ -f "$BYPASS_LOG" ] && grep -q "BYPASS_EXECPLAN=1" "$BYPASS_LOG"; then
  echo "PASS  6b-bypass-is-logged"
  PASS=$((PASS+1))
else
  echo "FAIL  6b-bypass-is-logged (log missing or empty: $BYPASS_LOG)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- 7: fail-open on infra error (missing run.py) ---"

arm_fresh "$WALKER_SID"
FAKE_ROOT="$TMP_ROOT/fakeroot"
mkdir -p "$FAKE_ROOT/hooks"
cp "$HOOK" "$FAKE_ROOT/hooks/execplan-stop-handoff.sh"
chmod +x "$FAKE_ROOT/hooks/execplan-stop-handoff.sh"
# Deliberately no $FAKE_ROOT/skills/execute-plan/run.py -- the hook's own
# self-relative RUN_PY resolution will point at a nonexistent file.
FAKE_HOOK="$FAKE_ROOT/hooks/execplan-stop-handoff.sh"
OUT=$(printf '%s' "$(mk_stop_payload "$WALKER_SID" false "$WT1")" | bash "$FAKE_HOOK" 2>&1)
GOT=$?
if [ "$GOT" -eq 0 ]; then
  echo "PASS  7-fail-open-exit-0  (exit $GOT)"
  PASS=$((PASS+1))
else
  echo "FAIL  7-fail-open-exit-0  (expected 0, got $GOT)"
  echo "      stderr: $OUT"
  FAIL=$((FAIL+1))
fi
assert_eq "7-pointer-untouched-on-infra-error" "$WALKER_SID" "$(pointer_field walking_session_id)"

echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

#!/usr/bin/env bash
# Sentinel tests for check-execplan-walk-gate.sh (PreToolUse Edit|Write|Bash|Agent).
#
# execplan-contract-hardening Slice S4 (Thoughts/execplan-contract-hardening-*_PLAN.md
# Coherent Action A4). Everything is isolated under EXECPLAN_ACK_STATE_DIR (a fresh
# temp dir) -- NEVER the live ~/.claude/state/execplan* namespace. Each scenario arms
# real state via the SAME run.py verbs the orchestrator uses (set-active-run,
# checkout-slice, check-code, record-conformance), then drives the hook exactly as
# the harness would -- stdin JSON in, exit code + stderr out. Hermetic: no network,
# no git config beyond `git init` in throwaway temp worktrees (cleaned up on exit).
#
# Hook contract: exit 0 = allow; exit 2 = block.

set -u

HOOK="~/.claude-staging-execplan-hardening/hooks/check-execplan-walk-gate.sh"
RUN_PY="~/.claude-staging-execplan-hardening/skills/execute-plan/run.py"

TMP_ROOT=$(mktemp -d -t execplan-walk-gate-test-XXXXXX)
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

# arm_run <surface> <worktree> <owner_sid> <walking_sid|null>
# Arms a per-run pointer with execution_pending:true + walking_session_id via the
# real set-active-run verb -- the same primitive /plan Step 11 / become-walker use.
arm_run() {
  local surface="$1" wt="$2" owner="$3" walking="$4"
  local walking_json
  if [ "$walking" = "null" ]; then
    walking_json='null'
  else
    walking_json="\"$walking\""
  fi
  jq -nc --arg owner "$owner" --arg surf "$surface" --arg wt "$wt" \
         --argjson walking "$walking_json" \
    '{owner_session_id:$owner, surface_path:$surf, worktree_root:$wt,
      execution_pending:true, walking_session_id:$walking, current_slice_id:null}' \
    | python3 "$RUN_PY" set-active-run >/dev/null
}

# compute_run_id <surface> <wt> -- derives the run_id the SAME way run.py's own
# compute_run_id does: sha256(surface_path[+worktree_root])[:12]. Needed for test
# setup only (record_conformance_pass / write_pass_receipt key on run_id); the hook
# itself never computes this -- it reads run_id straight off the matched pointer.
compute_run_id() {
  local surface="$1" wt="$2"
  python3 - "$surface" "$wt" <<'PYEOF'
import hashlib, sys
surface, wt = sys.argv[1], sys.argv[2]
# MUST match run.py's compute_run_id() exactly: worktree_root + NUL + surface_path
# (worktree FIRST) -- reversing the order silently produces a DIFFERENT run_id.
key = (wt + "\x00" + surface) if wt else surface
print(hashlib.sha256(key.encode("utf-8")).hexdigest()[:12])
PYEOF
}

# checkout <surface> <wt> <slice_id> <state_path>
checkout() {
  local surface="$1" wt="$2" slice_id="$3" state_path="$4"
  jq -nc --arg surf "$surface" --arg wt "$wt" --arg sid "$slice_id" --arg sp "$state_path" \
    '{surface_path:$surf, worktree_root:$wt, slice_id:$sid, state_path:$sp}' \
    | python3 "$RUN_PY" checkout-slice
}

# write_pass_receipt <run_id> <slice_id> -- calls run.py's write_code_receipt
# directly (module import) so the test doesn't depend on a specific verify_specs
# shape passing through _verify_write. This is TEST SETUP ONLY, isolated under
# EXECPLAN_ACK_STATE_DIR -- never the live receipt store.
write_pass_receipt() {
  local run_id="$1" slice_id="$2"
  RUN_PY="$RUN_PY" python3 - "$run_id" "$slice_id" <<'PYEOF'
import importlib.util, os, sys
run_id, slice_id = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("run", os.environ["RUN_PY"])
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)
run.write_code_receipt(run_id, slice_id)
PYEOF
}

# record_conformance_pass <run_id> <slice_id> -- arms a PASS conformance receipt via
# the REAL record-conformance verb (captured-checkers path, never an inline verdict).
record_conformance_pass() {
  local run_id="$1" slice_id="$2"
  jq -nc --arg rid "$run_id" --arg sid "$slice_id" \
    '{run_id:$rid, slice_id:$sid, is_final:true,
      checkers_json:[{model:"sonnet", verdict:"PASS"},
                     {model:"sonnet", verdict:"PASS"},
                     {model:"sonnet", verdict:"PASS"}]}' \
    | python3 "$RUN_PY" record-conformance >/dev/null
}

# mk_payload helpers
mk_edit_payload() {
  jq -nc --arg t "$1" --arg sid "$2" --arg cwd "$3" --arg fp "$4" \
    '{tool_name:$t, session_id:$sid, cwd:$cwd, tool_input:{file_path:$fp}}'
}
mk_bash_payload() {
  jq -nc --arg sid "$1" --arg cwd "$2" --arg cmd "$3" \
    '{tool_name:"Bash", session_id:$sid, cwd:$cwd, tool_input:{command:$cmd}}'
}
mk_agent_payload() {
  jq -nc --arg sid "$1" --arg cwd "$2" --arg prompt "$3" \
    '{tool_name:"Agent", session_id:$sid, cwd:$cwd, tool_input:{subagent_type:"claude", prompt:$prompt}}'
}

run_case() {
  local name="$1" expected="$2" json="$3"
  local out got
  out=$(printf '%s' "$json" | bash "$HOOK" 2>&1)
  got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    echo "      stderr: $out"
    FAIL=$((FAIL+1))
  fi
}

run_case_grep() {
  local name="$1" expected="$2" json="$3" pattern="$4"
  local out got
  out=$(printf '%s' "$json" | bash "$HOOK" 2>&1)
  got=$?
  if [ "$got" -ne "$expected" ]; then
    echo "FAIL  $name  (expected exit $expected, got $got)"
    echo "      stderr: $out"
    FAIL=$((FAIL+1))
    return
  fi
  if ! printf '%s' "$out" | grep -q "$pattern"; then
    echo "FAIL  $name  (exit ok, but stderr missing pattern: $pattern)"
    echo "      stderr: $out"
    FAIL=$((FAIL+1))
    return
  fi
  echo "PASS  $name  (exit $got, pattern matched)"
  PASS=$((PASS+1))
}

echo "--- Setup ---"
WT1=$(mk_worktree wt1)
SURFACE1="$WT1/Thoughts/walkgatetest_PLAN.md"
STATE_PATH="$WT1/walkgatetest.run-state.json"
WALKER_SID="walker-sid-101"
OTHER_SID="other-sid-202"

mkdir -p "$(dirname "$SURFACE1")"
cat > "$SURFACE1" <<'REGEOF'
# walkgatetest plan

| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|---------------|-------|------------|
| S1 | First slice | impl | do the first thing | routine | — |
| S2 | Second slice | impl | do the second thing | routine | S1 |
REGEOF

echo '{}' > "$STATE_PATH"

arm_run "$SURFACE1" "$WT1" "$WALKER_SID" "$WALKER_SID"
RUN_ID=$(compute_run_id "$SURFACE1" "$WT1")
echo "armed run pointer: surface=$SURFACE1 worktree=$WT1 walker=$WALKER_SID run_id=$RUN_ID"

echo ""
echo "--- Edit/Write: no slice checked out -> blocked; walker only ---"

run_case_grep "1-walker-edit-blocked-no-checkout" 2 \
  "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$WT1/src/foo.py")" \
  "no slice is checked out"

run_case "2-non-walker-edit-not-this-gate" 0 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$WT1/src/foo.py")"

echo ""
echo "--- Excluded bookkeeping paths (allowed even for the walker with no checkout) ---"

run_case "3-todo-md-allowed" 0 \
  "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$WT1/TODO.md")"

run_case "3b-run-state-allowed" 0 \
  "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$STATE_PATH")"

run_case "3c-spine-plan-allowed" 0 \
  "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$SURFACE1")"

echo ""
echo "--- Checkout S1 (the ready slice) -> edit now allowed for the walker ---"

CHECKOUT_OUT=$(checkout "$SURFACE1" "$WT1" "S1" "$STATE_PATH")
echo "checkout S1: $CHECKOUT_OUT"

run_case "4-walker-edit-allowed-after-checkout" 0 \
  "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$WT1/src/foo.py")"

run_case "5-walker-edit-allowed-other-file" 0 \
  "$(mk_edit_payload Edit "$WALKER_SID" "$WT1" "$WT1/src/bar.py")"

echo ""
echo "--- Adversarial Bash suite (checkout=S1) ---"

run_case "6-bash-redirect-walker-allowed" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" "echo x > $WT1/src/f1.txt")"

run_case "7-bash-readonly-pipeline-allowed" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" "ls -la | grep foo")"

run_case "8-bash-readonly-cat-allowed" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" "cat $WT1/src/f1.txt")"

run_case "9-bash-stderr-redirect-not-mistaken-for-write" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" "some_cmd 2>/dev/null")"

echo ""
echo "--- Agent spawn: [SLICE:Sn] tag vs untagged (union-fallback) ---"

run_case "10-agent-untagged-union-fallback-allowed" 0 \
  "$(mk_agent_payload "$WALKER_SID" "$WT1" "do some verification work, no slice tag here")"

run_case "11-agent-slice-tag-matches-current-no-deps-allowed" 0 \
  "$(mk_agent_payload "$WALKER_SID" "$WT1" "[SLICE:S1] implement the first slice")"

run_case_grep "12-agent-slice-tag-not-current-blocked" 2 \
  "$(mk_agent_payload "$WALKER_SID" "$WT1" "[SLICE:S2] implement the second slice")" \
  "not the checked-out slice"

echo ""
echo "--- Move checkout to S2 (depends on S1) -- dependency receipt gating ---"

# Mark S1 completed on the (keyed-directly, Minimal-shape) state surface so
# next_ready_slice() advances to S2.
jq -n '{S1:{status:"completed", result:{}}}' > "$STATE_PATH"

CHECKOUT2_OUT=$(checkout "$SURFACE1" "$WT1" "S2" "$STATE_PATH")
echo "checkout S2: $CHECKOUT2_OUT"

run_case_grep "13-agent-s2-blocked-dep-s1-no-conformance-receipt" 2 \
  "$(mk_agent_payload "$WALKER_SID" "$WT1" "[SLICE:S2] implement the second slice")" \
  "dependencies without a conformance PASS receipt"

record_conformance_pass "$RUN_ID" "S1"

run_case "14-agent-s2-allowed-once-s1-conformance-pass-recorded" 0 \
  "$(mk_agent_payload "$WALKER_SID" "$WT1" "[SLICE:S2] implement the second slice")"

echo ""
echo "--- Sn: commit gating (code-layer receipt precondition) ---"

run_case_grep "15-commit-s2-blocked-no-code-receipt" 2 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" 'git commit -m "S2: implement the second slice"')" \
  "no code-layer receipt"

write_pass_receipt "$RUN_ID" "S2"

run_case "16-commit-s2-allowed-with-code-receipt" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" 'git commit -m "S2: implement the second slice"')"

run_case "17-commit-non-slice-message-not-gated" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" 'git commit -m "unrelated docs fix"')"

echo ""
echo "--- BYPASS_EXECPLAN=1 (disables the gate; logs the bypass) ---"

BYPASS_LOG="$EXECPLAN_ACK_STATE_DIR/execplan/bypass.log"
rm -f "$BYPASS_LOG"
export BYPASS_EXECPLAN=1
run_case "18-bypass-disables-gate" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" 'git commit -m "S2: implement the second slice"')"
unset BYPASS_EXECPLAN
if [ -f "$BYPASS_LOG" ] && grep -q "BYPASS_EXECPLAN=1" "$BYPASS_LOG"; then
  echo "PASS  18b-bypass-is-logged"
  PASS=$((PASS+1))
else
  echo "FAIL  18b-bypass-is-logged (log missing or empty: $BYPASS_LOG)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- Fail-open on infra error (missing run.py) ---"

FAKE_ROOT="$TMP_ROOT/fakeroot"
mkdir -p "$FAKE_ROOT/hooks"
cp "$HOOK" "$FAKE_ROOT/hooks/check-execplan-walk-gate.sh"
chmod +x "$FAKE_ROOT/hooks/check-execplan-walk-gate.sh"
# Deliberately no $FAKE_ROOT/skills/execute-plan/run.py -- the hook's own
# self-relative RUN_PY resolution will point at a nonexistent file.
FAKE_HOOK="$FAKE_ROOT/hooks/check-execplan-walk-gate.sh"
out=$(printf '%s' "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$WT1/src/foo.py")" \
  | bash "$FAKE_HOOK" 2>&1)
got=$?
if [ "$got" -eq 0 ]; then
  echo "PASS  19-fail-open-missing-run-py  (exit $got)"
  PASS=$((PASS+1))
else
  echo "FAIL  19-fail-open-missing-run-py  (expected 0, got $got)"
  echo "      stderr: $out"
  FAIL=$((FAIL+1))
fi

echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

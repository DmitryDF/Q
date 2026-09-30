#!/usr/bin/env bash
# Sentinel tests for check-execplan-entry-gate.sh (PreToolUse Edit|Write|Bash|Agent).
#
# execplan-contract-hardening Slice S3 (Thoughts/execplan-contract-hardening-*_PLAN.md
# Coherent Action A3). Everything is isolated under EXECPLAN_ACK_STATE_DIR (a fresh
# temp dir) -- NEVER the live ~/.claude/state/execplan pointer/receipt namespace.
# Each scenario arms a real per-run pointer via the SAME run.py verb the orchestrator
# uses (`set-active-run`), then drives the hook exactly as the harness would --
# stdin JSON in, exit code + stderr out. Hermetic: no network, no git config beyond
# `git init` in throwaway temp worktrees (cleaned up on exit).
#
# Hook contract: exit 0 = allow; exit 2 = block.

set -u

HOOK="~/.claude-staging-execplan-hardening/hooks/check-execplan-entry-gate.sh"
RUN_PY="~/.claude-staging-execplan-hardening/skills/execute-plan/run.py"

TMP_ROOT=$(mktemp -d -t execplan-entry-gate-test-XXXXXX)
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
# Arms a per-run pointer with execution_pending:true via the real set-active-run
# verb -- the same primitive /plan Step 11 uses.
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

# mk_payload <tool_name> <session_id> <cwd> <file_path|command>
mk_edit_payload() {
  jq -nc --arg t "$1" --arg sid "$2" --arg cwd "$3" --arg fp "$4" \
    '{tool_name:$t, session_id:$sid, cwd:$cwd, tool_input:{file_path:$fp}}'
}

mk_bash_payload() {
  jq -nc --arg sid "$1" --arg cwd "$2" --arg cmd "$3" \
    '{tool_name:"Bash", session_id:$sid, cwd:$cwd, tool_input:{command:$cmd}}'
}

mk_agent_payload() {
  jq -nc --arg sid "$1" --arg cwd "$2" \
    '{tool_name:"Agent", session_id:$sid, cwd:$cwd, tool_input:{subagent_type:"claude", prompt:"do work"}}'
}

# run_case <name> <expected_exit> <json>
run_case() {
  local name="$1" expected="$2" json="$3"
  local out
  out=$(printf '%s' "$json" | bash "$HOOK" 2>&1)
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    echo "      stderr: $out"
    FAIL=$((FAIL+1))
  fi
}

# run_case_grep <name> <expected_exit> <json> <grep_pattern>
# Same as run_case, but also asserts stderr matches <grep_pattern> (block cases).
run_case_grep() {
  local name="$1" expected="$2" json="$3" pattern="$4"
  local out
  out=$(printf '%s' "$json" | bash "$HOOK" 2>&1)
  local got=$?
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
SURFACE1="$WT1/Thoughts/entrygatetest_PLAN.md"
WALKER_SID="walker-sid-001"
OTHER_SID="other-sid-002"
arm_run "$SURFACE1" "$WT1" "$WALKER_SID" "$WALKER_SID"
echo "armed run pointer: surface=$SURFACE1 worktree=$WT1 walker=$WALKER_SID"

echo ""
echo "--- Edit/Write: walker vs non-walker ---"

run_case "1-non-walker-write-blocked" 2 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$WT1/src/foo.py")"

run_case "2-walker-write-allowed" 0 \
  "$(mk_edit_payload Write "$WALKER_SID" "$WT1" "$WT1/src/foo.py")"

run_case_grep "1b-non-walker-block-message-names-plan" 2 \
  "$(mk_edit_payload Edit "$OTHER_SID" "$WT1" "$WT1/src/bar.py")" \
  "entrygatetest"

echo ""
echo "--- Excluded bookkeeping paths (allowed even for a non-walker) ---"

run_case "3-todo-md-allowed" 0 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$WT1/TODO.md")"

run_case "3b-minimal-run-state-allowed" 0 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$WT1/myplan.run-state.json")"

run_case "3c-claude-state-dir-allowed" 0 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$HOME/.claude/state/somefile.json")"

echo ""
echo "--- Out-of-worktree path (allowed) ---"

run_case "4-outside-worktree-allowed" 0 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "/tmp/definitely-outside-$$/foo.py")"

echo ""
echo "--- Agent spawn: walker vs non-walker ---"

run_case "5-non-walker-agent-spawn-blocked" 2 \
  "$(mk_agent_payload "$OTHER_SID" "$WT1")"

run_case "6-walker-agent-spawn-allowed" 0 \
  "$(mk_agent_payload "$WALKER_SID" "$WT1")"

echo ""
echo "--- Adversarial Bash suite ---"

run_case "7-bash-redirect-single-blocked" 2 \
  "$(mk_bash_payload "$OTHER_SID" "$WT1" "echo x > $WT1/src/f1.txt")"

run_case "8-bash-redirect-append-blocked" 2 \
  "$(mk_bash_payload "$OTHER_SID" "$WT1" "echo x >> $WT1/src/f2.txt")"

run_case "9-bash-redirect-walker-allowed" 0 \
  "$(mk_bash_payload "$WALKER_SID" "$WT1" "echo x > $WT1/src/f3.txt")"

run_case "10-bash-readonly-pipeline-allowed" 0 \
  "$(mk_bash_payload "$OTHER_SID" "$WT1" "ls -la | grep foo")"

run_case "11-bash-readonly-cat-allowed" 0 \
  "$(mk_bash_payload "$OTHER_SID" "$WT1" "cat $WT1/src/f1.txt")"

run_case "12-bash-readonly-grep-r-allowed" 0 \
  "$(mk_bash_payload "$OTHER_SID" "$WT1" "grep -r x .")"

run_case "13-bash-stderr-redirect-not-mistaken-for-write" 0 \
  "$(mk_bash_payload "$OTHER_SID" "$WT1" "some_cmd 2>/dev/null")"

echo ""
echo "--- BYPASS_EXECPLAN=1 (disables the gate; logs the bypass) ---"

BYPASS_LOG="$EXECPLAN_ACK_STATE_DIR/execplan/bypass.log"
rm -f "$BYPASS_LOG"
export BYPASS_EXECPLAN=1
run_case "14-bypass-disables-gate" 0 \
  "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$WT1/src/foo.py")"
unset BYPASS_EXECPLAN
if [ -f "$BYPASS_LOG" ] && grep -q "BYPASS_EXECPLAN=1" "$BYPASS_LOG"; then
  echo "PASS  14b-bypass-is-logged"
  PASS=$((PASS+1))
else
  echo "FAIL  14b-bypass-is-logged (log missing or empty: $BYPASS_LOG)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "--- Fail-open on infra error (missing run.py) ---"

FAKE_ROOT="$TMP_ROOT/fakeroot"
mkdir -p "$FAKE_ROOT/hooks"
cp "$HOOK" "$FAKE_ROOT/hooks/check-execplan-entry-gate.sh"
chmod +x "$FAKE_ROOT/hooks/check-execplan-entry-gate.sh"
# Deliberately no $FAKE_ROOT/skills/execute-plan/run.py -- the hook's own
# self-relative RUN_PY resolution will point at a nonexistent file.
FAKE_HOOK="$FAKE_ROOT/hooks/check-execplan-entry-gate.sh"
out=$(printf '%s' "$(mk_edit_payload Write "$OTHER_SID" "$WT1" "$WT1/src/foo.py")" \
  | bash "$FAKE_HOOK" 2>&1)
got=$?
if [ "$got" -eq 0 ]; then
  echo "PASS  15-fail-open-missing-run-py  (exit $got)"
  PASS=$((PASS+1))
else
  echo "FAIL  15-fail-open-missing-run-py  (expected 0, got $got)"
  echo "      stderr: $out"
  FAIL=$((FAIL+1))
fi

echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

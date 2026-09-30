#!/usr/bin/env bash
# Integration tests for check-impl-models.sh (runtime model enforcement).
#
# Locks the unconditional contract (2026-06-19): the hook enforces the declared
# implementation model for ANY plan that declares a real family — it no longer
# requires the GATE0SR:SLICES opt-in marker. The Model column is $7 (the 6th
# data column), consistent with check-plan-gates.sh.
#
# Fully hermetic: the hook resolves the plan via find-session-plan.sh
# ($HOME/.claude/plans/.manifest.json) and the subagent transcript via
# $HOME/.claude/projects/<sid>/subagents/agent-<aid>.jsonl. We invoke the REAL
# hook with HOME pointed at a temp dir, so no live config is touched.
#
# Hook contract: reads PostToolUse JSON on stdin; exit 0 = allow, 2 = block.

set -u

HOOK="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/check-impl-models.sh"   # capture before HOME override (respects CLAUDE_CONFIG_DIR for claude-experiment staging)
TMP_ROOT=$(mktemp -d -t impl-models-test-XXXXXX)
FAKE_HOME="$TMP_ROOT/home"
PASS=0
FAIL=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "$FAKE_HOME/.claude/plans"

# A2 skip-list fixture: verification/close subagent_types the model contract skips.
mkdir -p "$FAKE_HOME/.claude/state/impl-models"
printf '{"skip":["Explore","close-diary-drafter","close-commit-composer"]}\n' \
  > "$FAKE_HOME/.claude/state/impl-models/skip-list.json"

# write_plan <path> <model-cell> <with_slices: yes|no>
write_plan() {
  local path="$1" model="$2" slices="$3"
  {
    echo "# Test Plan"
    echo ""
    [ "$slices" = "yes" ] && echo "<!-- GATE0SR:SLICES -->"
    echo "## Coherent Actions"
    echo "| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |"
    echo "|--------|---------------|------|-------------|-----------------|-------|--------|"
    echo "| A1 | G1 | thing | guard | gate | $model | core |"
    echo "<!-- GATE0E:ACTIONS -->"
  } > "$path"
}

# write_plan_multi <path> — two actions with DIFFERENT models: A1=sonnet, A2=opus.
# Used by the A4 per-action tests: the union is {sonnet,opus}, so a union check
# alone cannot catch an A1(sonnet)-declared action that ran on opus.
write_plan_multi() {
  local path="$1"
  {
    echo "# Test Plan Multi"
    echo ""
    echo "## Coherent Actions"
    echo "| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |"
    echo "|--------|---------------|------|-------------|-----------------|-------|--------|"
    echo "| A1 | G1 | thing | guard | gate | sonnet | core |"
    echo "| A2 | G2 | thing | guard | gate | opus | core |"
    echo "<!-- GATE0E:ACTIONS -->"
  } > "$path"
}

# write_transcript <sid> <aid> <model-id>
write_transcript() {
  local d="$FAKE_HOME/.claude/projects/$1/subagents"
  mkdir -p "$d"
  printf '{"message":{"role":"assistant","model":"%s"}}\n' "$3" > "$d/agent-$2.jsonl"
}

# write_manifest <sid> <plan-path>
write_manifest() {
  printf '{"%s":["%s"]}\n' "$1" "$2" > "$FAKE_HOME/.claude/plans/.manifest.json"
}

# run_case <name> <expected> <session_id> <agent_id> [tool_name]
run_case() {
  local name="$1" expected="$2" sid="$3" aid="$4" tool="${5:-Agent}"
  local json
  json=$(printf '{"tool_name":"%s","session_id":"%s","tool_use_id":"%s"}' "$tool" "$sid" "$aid")
  HOME="$FAKE_HOME" CLAUDE_CONFIG_DIR="$FAKE_HOME/.claude" bash "$HOOK" >/dev/null 2>&1 <<< "$json"
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# run_case_st <name> <expected> <session_id> <agent_id> <subagent_type>
# Like run_case but carries tool_input.subagent_type (A2 scoping).
run_case_st() {
  local name="$1" expected="$2" sid="$3" aid="$4" st="$5"
  local json
  json=$(printf '{"tool_name":"Agent","session_id":"%s","tool_use_id":"%s","tool_input":{"subagent_type":"%s"}}' "$sid" "$aid" "$st")
  HOME="$FAKE_HOME" CLAUDE_CONFIG_DIR="$FAKE_HOME/.claude" bash "$HOOK" >/dev/null 2>&1 <<< "$json"
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# run_case_prompt <name> <expected> <session_id> <agent_id> <prompt>
# Carries tool_input.prompt (A4 per-action tag). jq -n builds the JSON so the
# prompt may safely contain brackets like [ACTION:A1].
run_case_prompt() {
  local name="$1" expected="$2" sid="$3" aid="$4" prompt="$5"
  local json
  json=$(jq -nc --arg s "$sid" --arg a "$aid" --arg p "$prompt" \
    '{tool_name:"Agent", session_id:$s, tool_use_id:$a, tool_input:{prompt:$p}}')
  HOME="$FAKE_HOME" CLAUDE_CONFIG_DIR="$FAKE_HOME/.claude" bash "$HOOK" >/dev/null 2>&1 <<< "$json"
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# ----- Case 1: declared opus, subagent ran sonnet, NO SLICES → block -----
PLAN="$FAKE_HOME/plan1.md"
write_plan "$PLAN" "opus" "no"
write_manifest "sid1" "$PLAN"
write_transcript "sid1" "a1" "claude-sonnet-4-6"
run_case "1-mismatch-no-slices-blocks" 2 "sid1" "a1"

# ----- Case 2: declared opus, subagent ran opus, NO SLICES → allow -----
write_manifest "sid2" "$PLAN"          # same plan (declares opus)
write_transcript "sid2" "a2" "claude-opus-4-8"
run_case "2-match-no-slices-allows" 0 "sid2" "a2"

# ----- Case 3: declared opus WITH SLICES, ran sonnet → still block (back-compat) -----
PLAN_SL="$FAKE_HOME/plan3.md"
write_plan "$PLAN_SL" "opus" "yes"
write_manifest "sid3" "$PLAN_SL"
write_transcript "sid3" "a3" "claude-sonnet-4-6"
run_case "3-mismatch-with-slices-blocks" 2 "sid3" "a3"

# ----- Case 4: plan declares no real model (em-dash) → no-op allow -----
PLAN_DASH="$FAKE_HOME/plan4.md"
write_plan "$PLAN_DASH" "—" "no"
write_manifest "sid4" "$PLAN_DASH"
write_transcript "sid4" "a4" "claude-sonnet-4-6"
run_case "4-no-declared-model-noop" 0 "sid4" "a4"

# ----- Case 5: non-Agent tool → allow (early exit) -----
write_manifest "sid5" "$PLAN"
write_transcript "sid5" "a5" "claude-sonnet-4-6"
run_case "5-non-agent-tool-allows" 0 "sid5" "a5" "Bash"

# ===== A2: subagent_type skip-list (spawn-intent scoping) =====
# Plan declares opus; transcript ran sonnet (a MISMATCH). Whether we block
# depends purely on whether the spawn is implementation work.

# ----- Case 6: Explore (verification) → skipped → allow despite mismatch -----
write_manifest "sid6" "$PLAN"
write_transcript "sid6" "a6" "claude-sonnet-4-6"
run_case_st "6-explore-skipped" 0 "sid6" "a6" "Explore"

# ----- Case 7: close-diary-drafter (session-close) → skipped → allow -----
write_manifest "sid7" "$PLAN"
write_transcript "sid7" "a7" "claude-sonnet-4-6"
run_case_st "7-close-drafter-skipped" 0 "sid7" "a7" "close-diary-drafter"

# ----- Case 8: general-purpose (implementation) → NOT skipped → block -----
write_manifest "sid8" "$PLAN"
write_transcript "sid8" "a8" "claude-sonnet-4-6"
run_case_st "8-general-purpose-enforced" 2 "sid8" "a8" "general-purpose"

# ----- Case 9: unknown type → NOT skipped (anti-bypass) → block -----
write_manifest "sid9" "$PLAN"
write_transcript "sid9" "a9" "claude-sonnet-4-6"
run_case_st "9-unknown-type-enforced" 2 "sid9" "a9" "mystery-type"

# ===== A1: timing grace on missing transcript =====

# ----- Case 10: missing transcript → fail-open → allow (was exit 2) -----
write_manifest "sid10" "$PLAN"
# deliberately NO write_transcript for sid10/a10
run_case "10-missing-transcript-failopen" 0 "sid10" "a10"

# ----- Case 11: present transcript with mismatch → still blocks (regression) -----
write_manifest "sid11" "$PLAN"
write_transcript "sid11" "a11" "claude-sonnet-4-6"
run_case "11-present-mismatch-still-blocks" 2 "sid11" "a11"

# ===== A4: per-action model comparison (plan: A1=sonnet, A2=opus) =====
PLAN_MULTI="$FAKE_HOME/plan_multi.md"
write_plan_multi "$PLAN_MULTI"

# ----- Case 12: THE key case — tagged A1 (sonnet) but ran opus → per-action
#        BLOCK, even though opus IS in the union (A2's model). Union alone misses this.
write_manifest "sid12" "$PLAN_MULTI"
write_transcript "sid12" "a12" "claude-opus-4-8"
run_case_prompt "12-peraction-A1-ran-opus-blocks" 2 "sid12" "a12" "do the work [ACTION:A1] please"

# ----- Case 13: tagged A2 (opus), ran opus → per-action match → allow -----
write_manifest "sid13" "$PLAN_MULTI"
write_transcript "sid13" "a13" "claude-opus-4-8"
run_case_prompt "13-peraction-A2-ran-opus-allows" 0 "sid13" "a13" "[ACTION:A2] go"

# ----- Case 14: tagged A1 (sonnet), ran sonnet → per-action match → allow -----
write_manifest "sid14" "$PLAN_MULTI"
write_transcript "sid14" "a14" "claude-sonnet-4-6"
run_case_prompt "14-peraction-A1-ran-sonnet-allows" 0 "sid14" "a14" "[ACTION:A1] go"

# ----- Case 15: untagged, ran sonnet (in union) → union fallback → allow -----
write_manifest "sid15" "$PLAN_MULTI"
write_transcript "sid15" "a15" "claude-sonnet-4-6"
run_case_prompt "15-untagged-in-union-allows" 0 "sid15" "a15" "no tag here"

# ----- Case 16: untagged, ran haiku (NOT in union) → union fallback → block -----
write_manifest "sid16" "$PLAN_MULTI"
write_transcript "sid16" "a16" "claude-haiku-4-5"
run_case_prompt "16-untagged-not-in-union-blocks" 2 "sid16" "a16" "no tag here"

# ----- Case 17: tagged UNKNOWN action A9 (not in plan), ran opus (in union) →
#        union fallback → allow (tag to a missing action degrades to union) -----
write_manifest "sid17" "$PLAN_MULTI"
write_transcript "sid17" "a17" "claude-opus-4-8"
run_case_prompt "17-unknown-action-falls-back-union" 0 "sid17" "a17" "[ACTION:A9] mystery"

# ===== A4: [MODEL:fam] dispatcher-intended tag (execute-plan stamp) =====

# ----- Case 18: [MODEL:sonnet] but ran opus → block, even though opus is in the
#        union (per-spawn intent catches what union misses) -----
write_manifest "sid18" "$PLAN_MULTI"
write_transcript "sid18" "a18" "claude-opus-4-8"
run_case_prompt "18-model-sonnet-ran-opus-blocks" 2 "sid18" "a18" "impl slice [MODEL:sonnet]"

# ----- Case 19: [MODEL:opus] ran opus → match → allow -----
write_manifest "sid19" "$PLAN_MULTI"
write_transcript "sid19" "a19" "claude-opus-4-8"
run_case_prompt "19-model-opus-ran-opus-allows" 0 "sid19" "a19" "impl slice [MODEL:opus]"

# ----- Case 20: [MODEL:sonnet] ran sonnet → match → allow -----
write_manifest "sid20" "$PLAN_MULTI"
write_transcript "sid20" "a20" "claude-sonnet-4-6"
run_case_prompt "20-model-sonnet-ran-sonnet-allows" 0 "sid20" "a20" "impl slice [MODEL:sonnet]"

# ----- Case 21: BOTH tags — [ACTION:A1](sonnet) + [MODEL:opus], ran opus →
#        ACTION path wins (A1 declares sonnet) → block. Confirms ACTION > MODEL. -----
write_manifest "sid21" "$PLAN_MULTI"
write_transcript "sid21" "a21" "claude-opus-4-8"
run_case_prompt "21-action-beats-model-blocks" 2 "sid21" "a21" "[ACTION:A1] [MODEL:opus] go"

# ----- Summary -----
echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

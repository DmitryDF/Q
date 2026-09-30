#!/usr/bin/env bash
# Characterization + Mode-C tests for check-plan-readonly.sh (PreToolUse gate).
#
# This hook is LIVE-REGISTERED (settings.json) and gates Edit/Write during plan
# mode. A wrong edit blocks plan-mode entry for every session immediately, and
# it had NO test before DS5a. These cases lock the current allow/block contract
# AND the new Mode-C branch (DS5a): a `*/Thoughts/*_PLAN.md` write carrying
# `bookkeeping: mode-c` frontmatter (in the payload OR already on disk) is
# allowed even with no bound topic to exact-match against.
#
# Hermetic: HOME is pointed at a temp dir. The hook resolves topic state via
# `${KIT_HOOKS_DIR}/pre_plan_gates.py read <sid>`; we drop a STUB there that
# returns canned topic state for session `bound-sid` and empty otherwise, so
# Modes A/B exact-match and the no-topic (Mode-C) path are both deterministic.
#
# Hook contract: reads PreToolUse JSON on stdin; exit 0 = allow, 2 = block.

set -u

HOOK="${KIT_HOOKS_DIR}/check-plan-readonly.sh"   # capture before HOME override
TMP_ROOT=$(mktemp -d -t plan-readonly-test-XXXXXX)
FAKE_HOME="$TMP_ROOT/home"
PASS=0
FAIL=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "$FAKE_HOME/.claude/plans" "$FAKE_HOME/.claude/hooks"

# Stub pre_plan_gates.py: canned topic state for `bound-sid`, empty otherwise.
cat > "$FAKE_HOME/.claude/hooks/pre_plan_gates.py" <<'PYEOF'
import sys, json
if len(sys.argv) >= 3 and sys.argv[1] == "read" and sys.argv[2] == "bound-sid":
    print(json.dumps({"topic_classification":
                      {"project_root": "/proj", "project_slug": "mytopic"}}))
PYEOF

# run_case <name> <expected> <json>
run_case() {
  local name="$1" expected="$2" json="$3"
  HOME="$FAKE_HOME" bash "$HOOK" >/dev/null 2>&1 <<< "$json"
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# mk <tool> <permission_mode> <file_path> <session_id> [content] [new_string]
mk() {
  jq -nc --arg t "$1" --arg pm "$2" --arg fp "$3" --arg sid "$4" \
         --arg c "${5-}" --arg ns "${6-}" \
    '{tool_name:$t, permission_mode:$pm, session_id:$sid,
      tool_input:({file_path:$fp}
                  + (if $c  != "" then {content:$c}     else {} end)
                  + (if $ns != "" then {new_string:$ns} else {} end))}'
}

PLANS="$FAKE_HOME/.claude/plans"

echo "--- Characterization (current contract) ---"

# 1. Non-Edit/Write tool → allow (early exit).
run_case "1-non-edit-write-allows" 0 \
  "$(mk Bash plan "/proj/src/x.py" bound-sid)"

# 2. Edit but NOT in plan mode → allow.
run_case "2-not-plan-mode-allows" 0 \
  "$(mk Edit default "/proj/src/x.py" bound-sid)"

# 3. Plan mode, target under ~/.claude/plans/ (Arm 1 legacy) → allow.
run_case "3-legacy-plans-dir-allows" 0 \
  "$(mk Write plan "$PLANS/harness-slug.md" bound-sid)"

# 4. Plan mode, Thoughts/ plan, bound topic EXACT match (Arm 2 A/B) → allow.
run_case "4-boundtopic-exact-match-allows" 0 \
  "$(mk Write plan "/proj/Thoughts/mytopic_PLAN.md" bound-sid)"

# 5. Plan mode, Thoughts/ plan, bound topic but WRONG path → block.
run_case "5-boundtopic-mismatch-blocks" 2 \
  "$(mk Write plan "/proj/Thoughts/other_PLAN.md" bound-sid)"

# 6. Plan mode, Thoughts/ plan, NO topic state, NO mode-c frontmatter → block.
#    (Stays block after DS5a — only frontmatter-carrying Mode-C plans get the pass.)
run_case "6-thoughts-no-topic-no-frontmatter-blocks" 2 \
  "$(mk Write plan "/proj/Thoughts/ninja_PLAN.md" unknown-sid)"

# 7. Plan mode, arbitrary source file → block.
run_case "7-source-file-blocks" 2 \
  "$(mk Write plan "/proj/src/x.py" bound-sid)"

echo "--- Mode-C branch (DS5a) ---"

# 8. Plan mode, Thoughts/ plan, NO topic state, content carries mode-c → allow.
run_case "8-modec-content-allows" 0 \
  "$(mk Write plan "/proj/Thoughts/ninja-20260624120000_PLAN.md" unknown-sid \
        "---
bookkeeping: mode-c
---
# Ninja Plan")"

# 9. Plan mode, Edit (no frontmatter in new_string) but file on disk carries
#    mode-c → allow (subsequent edits during plan authoring).
MODEC_ON_DISK="$TMP_ROOT/ondisk_Thoughts"
mkdir -p "$MODEC_ON_DISK/Thoughts"
printf -- '---\nbookkeeping: mode-c\n---\n# Ninja Plan\n' \
  > "$MODEC_ON_DISK/Thoughts/ninja-20260624120000_PLAN.md"
run_case "9-modec-ondisk-edit-allows" 0 \
  "$(mk Edit plan "$MODEC_ON_DISK/Thoughts/ninja-20260624120000_PLAN.md" unknown-sid \
        "" "## Diagnosis\nmore text")"

# 10. Plan mode, Thoughts/ plan, content has UNRELATED frontmatter only → block.
run_case "10-thoughts-unrelated-frontmatter-blocks" 2 \
  "$(mk Write plan "/proj/Thoughts/ninja_PLAN.md" unknown-sid \
        "---
bookkeeping: retired-2026-06-24
---
# Not Mode C")"

echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

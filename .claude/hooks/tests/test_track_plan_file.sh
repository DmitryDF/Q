#!/usr/bin/env bash
# Characterization + Mode-C tests for track-plan-file.sh (PostToolUse tracker).
#
# This hook is LIVE-REGISTERED (settings.json) and records plan files written by
# each session into ~/.claude/plans/.manifest.json. It had NO test before DS5a.
# These cases lock the current append/skip contract AND the new Mode-C branch
# (DS5a): a `*/Thoughts/*_PLAN.md` write carrying `bookkeeping: mode-c`
# frontmatter (on disk OR in the payload) is recorded in the manifest even with
# no bound topic — this is the "manifest coupling" fix (extend the WRITER).
#
# Hermetic: HOME points at a temp dir; a STUB pre_plan_gates.py returns canned
# topic state for `bound-sid`, empty otherwise.
#
# Hook contract: reads PostToolUse JSON on stdin; appends to the manifest as a
# side effect; always exits 0.

set -u

HOOK="${KIT_HOOKS_DIR}/track-plan-file.sh"   # capture before HOME override
TMP_ROOT=$(mktemp -d -t track-plan-test-XXXXXX)
FAKE_HOME="$TMP_ROOT/home"
MANIFEST="$FAKE_HOME/.claude/plans/.manifest.json"
PASS=0
FAIL=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "$FAKE_HOME/.claude/plans" "$FAKE_HOME/.claude/hooks"

# `flock` (the shell command) is absent on macOS, which USED TO make the
# manifest-append a silent no-op (`flock ... || exit 0`). Fixed 2026-07-09:
# track-plan-file.sh now delegates the append to _plan_manifest.py (fcntl-based
# lock, macOS-native). These tests therefore run the REAL macOS path with NO
# `flock` shim — the manifest is written natively. Make the real helper reachable
# under FAKE_HOME so the hook's `${KIT_HOOKS_DIR}/_plan_manifest.py` resolves.
cp "$(dirname "$HOOK")/_plan_manifest.py" "$FAKE_HOME/.claude/hooks/_plan_manifest.py"

cat > "$FAKE_HOME/.claude/hooks/pre_plan_gates.py" <<'PYEOF'
import sys, json
# Stub must match the schema the hook actually reads: `.topic_state.{project_root,
# topic_slug}` (NOT the legacy `.topic_classification.*` / `project_slug`, which
# left case 4 silently failing — the topic_state schema drift).
if len(sys.argv) >= 3 and sys.argv[1] == "read" and sys.argv[2] == "bound-sid":
    print(json.dumps({"topic_state":
                      {"project_root": "/proj", "topic_slug": "mytopic"}}))
PYEOF

reset_manifest() { rm -f "$MANIFEST" "$MANIFEST.lock"; }

# run <json>
run() { HOME="$FAKE_HOME" bash "$HOOK" >/dev/null 2>&1 <<< "$1"; }

# assert_tracked <name> <sid> <path> <yes|no>
assert_tracked() {
  local name="$1" sid="$2" path="$3" want="$4" got="no"
  if [ -f "$MANIFEST" ] && \
     jq -e --arg s "$sid" --arg p "$path" '(.[$s] // []) | index($p)' \
        "$MANIFEST" >/dev/null 2>&1; then
    got="yes"
  fi
  if [ "$got" = "$want" ]; then
    echo "PASS  $name  (tracked=$got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected tracked=$want, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# mk <tool> <file_path> <session_id> <agent_id> [content]
mk() {
  jq -nc --arg t "$1" --arg fp "$2" --arg sid "$3" --arg aid "$4" --arg c "${5-}" \
    '{tool_name:$t, session_id:$sid}
     + (if $aid != "" then {agent_id:$aid} else {} end)
     + {tool_input:({file_path:$fp}
                    + (if $c != "" then {content:$c} else {} end))}'
}

PLANS="$FAKE_HOME/.claude/plans"

echo "--- Characterization (current contract) ---"

# 1. Non-Write/Edit tool → not tracked.
reset_manifest
run "$(mk Bash "$PLANS/x.md" sid1 "")"
assert_tracked "1-non-write-edit-skips" sid1 "$PLANS/x.md" no

# 2. Subagent write (agent_id present) → not tracked.
reset_manifest
run "$(mk Write "$PLANS/x.md" sid2 "agent-7")"
assert_tracked "2-subagent-skips" sid2 "$PLANS/x.md" no

# 3. Legacy ~/.claude/plans/ write → tracked.
reset_manifest
run "$(mk Write "$PLANS/harness-slug.md" sid3 "")"
assert_tracked "3-legacy-plans-dir-tracked" sid3 "$PLANS/harness-slug.md" yes

# 4. Thoughts/ plan, bound topic EXACT match → tracked.
reset_manifest
run "$(mk Write "/proj/Thoughts/mytopic_PLAN.md" bound-sid "")"
assert_tracked "4-boundtopic-exact-tracked" bound-sid "/proj/Thoughts/mytopic_PLAN.md" yes

# 5. Thoughts/ plan, bound topic WRONG path → not tracked (WARN path).
reset_manifest
run "$(mk Write "/proj/Thoughts/other_PLAN.md" bound-sid "")"
assert_tracked "5-boundtopic-mismatch-skips" bound-sid "/proj/Thoughts/other_PLAN.md" no

# 6. Thoughts/ plan, NO topic state, NO mode-c frontmatter → not tracked.
reset_manifest
run "$(mk Write "/proj/Thoughts/ninja_PLAN.md" unknown-sid "")"
assert_tracked "6-thoughts-no-topic-no-frontmatter-skips" unknown-sid "/proj/Thoughts/ninja_PLAN.md" no

# 7. Arbitrary source file → not tracked.
reset_manifest
run "$(mk Write "/proj/src/x.py" sid7 "")"
assert_tracked "7-source-file-skips" sid7 "/proj/src/x.py" no

echo "--- Mode-C branch (DS5a) ---"

# 8. Thoughts/ plan, NO topic state, file on disk carries mode-c → tracked.
reset_manifest
DISK="$TMP_ROOT/proj8"
mkdir -p "$DISK/Thoughts"
P8="$DISK/Thoughts/ninja-20260624120000_PLAN.md"
printf -- '---\nbookkeeping: mode-c\n---\n# Ninja Plan\n' > "$P8"
run "$(mk Write "$P8" modec-sid "")"
assert_tracked "8-modec-ondisk-tracked" modec-sid "$P8" yes

# 9. Thoughts/ plan, NO topic state, mode-c only in payload content → tracked.
reset_manifest
P9="/proj/Thoughts/ninja-20260624130000_PLAN.md"
run "$(mk Write "$P9" modec-sid2 "" "---
bookkeeping: mode-c
---
# Ninja Plan")"
assert_tracked "9-modec-payload-tracked" modec-sid2 "$P9" yes

echo "--- macOS-native regression guard (2026-07-09 flock fix) ---"

# 10. Legacy plans-dir write with NO flock shim present → tracked natively.
#     Regression guard: before the fix, an absent `flock` made this a silent
#     no-op and the manifest stayed empty on macOS.
reset_manifest
run "$(mk Write "$PLANS/native-check.md" sid10 "")"
assert_tracked "10-macos-native-manifest-written" sid10 "$PLANS/native-check.md" yes

echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

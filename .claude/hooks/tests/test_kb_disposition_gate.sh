#!/usr/bin/env bash
# Integration tests for check-kb-disposition-gate.sh
#
# Each test constructs a temp project + temp plan + seeded manifest, invokes the hook
# with synthesized stdin, and asserts the exit code.

set -u

HOOK="${KIT_HOOKS_DIR}/check-kb-disposition-gate.sh"
MANIFEST="$HOME/.claude/plans/.manifest.json"
TMP_ROOT=$(mktemp -d -t kb-disp-test-XXXXXX)
PASS=0
FAIL=0

cleanup() {
  rm -rf "$TMP_ROOT"
  # restore original manifest if we backed it up
  if [ -f "$MANIFEST.bak-tests" ]; then
    mv "$MANIFEST.bak-tests" "$MANIFEST"
  fi
}
trap cleanup EXIT

# Back up real manifest so we don't pollute real sessions
if [ -f "$MANIFEST" ]; then
  cp "$MANIFEST" "$MANIFEST.bak-tests"
fi

run_case() {
  local name="$1"
  local expected="$2"
  local project_dir="$3"
  local plan_path="$4"
  local session_id="kb-disp-test-$name-$$"

  # Seed manifest
  printf '{"%s": ["%s"]}\n' "$session_id" "$plan_path" > "$MANIFEST"

  # Build stdin payload mimicking PermissionRequest for ExitPlanMode
  local payload
  payload=$(printf '{"tool_name": "ExitPlanMode", "session_id": "%s"}' "$session_id")

  CLAUDE_PROJECT_DIR="$project_dir" bash "$HOOK" <<<"$payload" 2>/dev/null
  local got=$?

  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# ----- Fixture builders -----

# A valid Gate 1 header block
gate1_block() {
  cat <<'EOF'
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status | Disposition | Hypothesis ID |
|-------|----------|----------------|-----------------|--------|-------------|---------------|
EOF
}

# ----- Test 1: Project with no declarations → no-op pass -----

P1="$TMP_ROOT/proj1"
mkdir -p "$P1"
cat > "$P1/CLAUDE.md" <<'EOF'
# Some Project
Just a project without plan-gate declarations.
EOF
mkdir -p "$TMP_ROOT/plans1"
PLAN1="$TMP_ROOT/plans1/plan.md"
cat > "$PLAN1" <<EOF
# Plan
$(gate1_block)
| X | Y | ~/file.md | line 1: \`snippet\` | [ok] | editorial-user | H-9999 |
<!-- GATE1:END -->
EOF
run_case "1-no-declarations" 0 "$P1" "$PLAN1"

# ----- Test 2: Declared project + plan missing Disposition column → fail -----

P2="$TMP_ROOT/proj2"
mkdir -p "$P2/Docs"
cat > "$P2/CLAUDE.md" <<'EOF'
knowledge_library_index: docs/INDEX.md
hypotheses_ledger: Docs/ledger.md
EOF
cat > "$P2/Docs/ledger.md" <<'EOF'
| H-0001 | rule | plan | hyp | 2026-01-01 | active |
EOF
PLAN2="$TMP_ROOT/plans1/plan2.md"
cat > "$PLAN2" <<EOF
# Plan 2
$(gate1_block)
| C | Cat | src | ver | stat |
<!-- GATE1:END -->
EOF
run_case "2-missing-disposition" 2 "$P2" "$PLAN2"

# ----- Test 3: editorial-user row with missing H-ID → fail -----

PLAN3="$TMP_ROOT/plans1/plan3.md"
cat > "$PLAN3" <<EOF
# Plan 3
$(gate1_block)
| C | Cat | src | ver | stat | editorial-user |  |
<!-- GATE1:END -->
<!-- GATE1T:CHECKER_PASS -->
EOF
run_case "3-editorial-no-hid" 2 "$P2" "$PLAN3"

# ----- Test 4: editorial-user H-ID not present in ledger → fail -----

PLAN4="$TMP_ROOT/plans1/plan4.md"
cat > "$PLAN4" <<EOF
# Plan 4
$(gate1_block)
| C | Cat | src | ver | stat | editorial-user | H-7777 |
<!-- GATE1:END -->
<!-- GATE1T:USER_APPROVED:H-7777 -->
<!-- GATE1T:CHECKER_PASS -->
EOF
run_case "4-hid-not-in-ledger" 2 "$P2" "$PLAN4"

# ----- Test 5: fully valid plan → pass -----

PLAN5="$TMP_ROOT/plans1/plan5.md"
cat > "$PLAN5" <<EOF
# Plan 5
$(gate1_block)
| C1 | Cat | src | ver | stat | editorial-user | H-0001 |
| C2 | Cat | src | ver | stat | n/a | — |
<!-- GATE1:END -->
<!-- GATE1T:USER_APPROVED:H-0001 -->
<!-- GATE1T:CHECKER_PASS -->
EOF
run_case "5-valid-pass" 0 "$P2" "$PLAN5"

# ----- Test 6: rule rows present but no CHECKER_PASS marker → fail -----

PLAN6="$TMP_ROOT/plans1/plan6.md"
cat > "$PLAN6" <<EOF
# Plan 6
$(gate1_block)
| C1 | Cat | src | ver | stat | expert-sourced | — |
<!-- GATE1:END -->
EOF
run_case "6-missing-checker-pass" 2 "$P2" "$PLAN6"

# ----- Summary -----

echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

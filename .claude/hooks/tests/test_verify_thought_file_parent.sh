#!/usr/bin/env bash
# Self-test for verify-thought-file.sh Parent: back-link emission.
#
# Regression guard for the _THOUGHT_check <-> bookkeeping G3 Parent loop
# (Thoughts/thought-check-parent-loop-*_PLAN.md). The generator regenerates the
# sidecar on every *_THOUGHT.md save; if it omits the `Parent:` line, the
# bookkeeping-invariant G3 case blocks the NEXT save. This test asserts the
# generated sidecar carries `Parent: [[<slug>_THOUGHT]]` on BOTH write paths
# (normal PASS/FAIL and validator-error UNVERIFIED).
#
# Runs against a sentinel CLAUDE_CONFIG_DIR + temp thought files; never touches
# live config or live Thoughts/.
set -u

HOOK="$(cd "$(dirname "$0")/.." && pwd)/verify-thought-file.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

CFG="$TMP/cfg"
mkdir -p "$CFG/hooks"
export CLAUDE_CONFIG_DIR="$CFG"

PASS=0
FAIL=0

# Write a fake structural validator with a chosen exit code into the sentinel
# hooks dir. exit 0 -> PASS, 1 -> FAIL (both take the normal write block);
# >=2 -> validator-error (UNVERIFIED write block).
make_validator() {  # $1 exit_code
  cat > "$CFG/hooks/_validate-thought-file.py" <<PYEOF
import sys
print("## Checks\n- stub")
sys.exit($1)
PYEOF
}

run_case() {  # $1 label  $2 slug  $3 expected_parent_line
  local label="$1" slug="$2" want="$3"
  local dir="$TMP/$slug"
  mkdir -p "$dir"
  local spine="$dir/${slug}_THOUGHT.md"
  printf '# %s\n' "$slug" > "$spine"
  printf '{"tool_name":"Write","tool_input":{"file_path":"%s"}}' "$spine" \
    | bash "$HOOK" >/dev/null 2>&1
  local sidecar="$dir/${slug}_THOUGHT_check.md"
  if [ -f "$sidecar" ] && grep -qF "$want" "$sidecar"; then
    PASS=$((PASS + 1)); printf '  ok   %s (%s present)\n' "$label" "$want"
  else
    FAIL=$((FAIL + 1)); printf '  FAIL %s (missing: %s)\n' "$label" "$want"
  fi
}

# 1. Normal write path (validator exits 1 = FAIL).
make_validator 1
run_case "normal PASS/FAIL path emits Parent" "alpha" "Parent: [[alpha_THOUGHT]]"

# 2. Validator-error UNVERIFIED write path (validator exits 3).
make_validator 3
run_case "validator-error path emits Parent" "beta" "Parent: [[beta_THOUGHT]]"

# 3. Timestamped slug — ts preserved by ${FILENAME%_THOUGHT.md}.
make_validator 1
run_case "timestamped slug resolves" "gamma-20260709120000" \
  "Parent: [[gamma-20260709120000_THOUGHT]]"

printf '\n%s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

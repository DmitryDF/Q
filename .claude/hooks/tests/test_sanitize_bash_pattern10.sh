#!/bin/bash
# Test for sanitize-bash.sh Pattern 10 (VAR= assignment preamble + tilde-in-assignment).
# Block cases are verbatim shapes observed in real permission prompts (2026-08-14).
# Pass-through cases are the legitimate forms that MUST keep working — an env-var
# prefix (VAR=value command) is not a preamble and must never be blocked.
#
# Override the hook under test with SANITIZE_HOOK=/path/to/sanitize-bash.sh
# (used to exercise a config-experiment clone before promotion).

set -u

if ! command -v jq >/dev/null 2>&1; then
  echo "FAIL: jq not installed; this test requires jq for JSON payload construction." >&2
  exit 1
fi

HOOK="${SANITIZE_HOOK:-${KIT_HOOKS_DIR}/sanitize-bash.sh}"
if [ ! -x "$HOOK" ]; then
  echo "FAIL: $HOOK not found or not executable." >&2
  exit 1
fi
echo "hook under test: $HOOK"

PASS=0
FAIL=0

probe() {
  local desc="$1"
  local cmd="$2"
  local expect_exit="$3"
  local expect_stderr="$4"

  local payload
  payload=$(printf '{"tool_name":"Bash","tool_input":{"command":%s}}' "$(printf '%s' "$cmd" | jq -Rs .)")

  local stderr exit_code
  stderr=$(echo "$payload" | "$HOOK" 2>&1 >/dev/null)
  exit_code=$?

  if [ "$exit_code" -eq "$expect_exit" ] && \
     { [ -z "$expect_stderr" ] || printf '%s' "$stderr" | grep -q "$expect_stderr"; }; then
    echo "PASS: $desc"
    PASS=$((PASS+1))
  else
    echo "FAIL: $desc — expected exit=$expect_exit stderr~='$expect_stderr', got exit=$exit_code stderr='$stderr'"
    FAIL=$((FAIL+1))
  fi
}

# ---- MUST BLOCK: assignment preamble (real observed shapes) ----------------
probe "unquoted path preamble + semicolon" \
  'SP=/private/tmp/claude-501/scratchpad; python3 "$SP/checkcode_s2.py"' 2 "assignment preamble"

probe "quoted path preamble + semicolon" \
  'R="~/repos/Projects"; git -C "$R" push origin main' 2 "assignment preamble"

probe "HOME-expanding preamble" \
  'SRC="$HOME/.<config-source-repo>"; git -C "$SRC" log --oneline -3' 2 "assignment preamble"

probe "multi-line assignment preamble (newline-terminated)" \
  'WT="~/repos/config-source/topic"
git -C "$WT" merge --no-edit main' 2 "assignment preamble"

# ---- MUST BLOCK: tilde in assignment value --------------------------------
probe "tilde assignment + semicolon" \
  'E=~/.claude-staging/hooks/_factcheck_engine.py; grep -n debounce "$E"' 2 "tilde in an assignment"

probe "tilde assignment, env-prefix form" \
  'F=${KIT_HOOKS_DIR}/tests/test_sanitize_bash_pattern7.sh grep -c probe "$F"' 2 "tilde in an assignment"

# ---- MUST NOT BLOCK: legitimate env-var prefixes ---------------------------
probe "env prefix: ALLOW_OUT_OF_TREE" \
  'ALLOW_OUT_OF_TREE=1 git -C ~/repos/Projects commit -m "msg"' 0 ""

probe "env prefix with semicolon inside a quoted commit message" \
  'ALLOW_OUT_OF_TREE=1 git -C ~/repos/Projects commit -m "fix: a; then b"' 0 ""

probe "env prefix: CLAUDE_CONFIG_DIR + bash" \
  'CLAUDE_CONFIG_DIR=~/.claude-staging bash /tmp/run.sh' 0 ""

probe "env prefix: verify-then-land override" \
  'VERIFY_THEN_LAND_SKIP=1 config-promote --emergency-skip' 0 ""

# ---- MUST NOT BLOCK: ordinary commands -------------------------------------
probe "plain absolute-path invocation" \
  'python3 ${KIT_HOOKS_DIR}/todo.py read --cwd ~/repos/Projects' 0 ""

probe "export is not a bare assignment" \
  'export NVM_DIR="$HOME/.nvm"' 0 ""

probe "long flag containing =" \
  'python3 /tmp/x.py --mode=fast --out=/tmp/y.json' 0 ""

probe "git -C with equals in a quoted message" \
  'git -C ~/repos/Projects commit -m "set KEY=value in config"' 0 ""

# ---- Regression: P9 must not fire on English prose (the 2026-08-14 FP) -----
probe "prose containing the word 'do' does not trip the loop rule" \
  'python3 ${KIT_HOOKS_DIR}/todo.py add "name the fix the way P1-P9 messages do." --bucket NOW' 0 ""

probe "prose containing the word 'for' does not trip the loop rule" \
  'python3 ${KIT_HOOKS_DIR}/todo.py add "a prompt for review, nothing more" --bucket NOW' 0 ""

echo
echo "pattern10: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] || exit 1

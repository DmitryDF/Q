#!/bin/bash
# Test for sanitize-bash.sh Pattern 7 (sed -n line-range read).
# Probes the hook with eight canonical sed forms; asserts expected
# block (exit 2 + BLOCKED stderr) or pass-through (exit 0).

set -u

if ! command -v jq >/dev/null 2>&1; then
  echo "FAIL: jq not installed; this test requires jq for JSON payload construction." >&2
  exit 1
fi

HOOK="${KIT_HOOKS_DIR}/sanitize-bash.sh"
if [ ! -x "$HOOK" ]; then
  echo "FAIL: $HOOK not found or not executable." >&2
  exit 1
fi

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

probe "block N,Mp"          "sed -n '391,394p' /tmp/x"           2 "BLOCKED"
probe "block Np"            "sed -n '10p' /tmp/x"                 2 "BLOCKED"
probe "block N,\$p"         "sed -n '10,\$p' /tmp/x"              2 "BLOCKED"
probe "block -e N,Mp"       "sed -n -e '10,20p' /tmp/x"          2 "BLOCKED"
probe "block N,M space p"   "sed -n '5,10 p' /tmp/x"             2 "BLOCKED"
probe "pass -i"             "sed -i 's/x/y/' /tmp/x"             0 ""
probe "pass plain filter"   "sed 's/x/y/' /tmp/x"                0 ""
probe "pass regex address"  "sed -n '/start/,/end/p' /tmp/x"     0 ""

echo "Results: $PASS passed, $FAIL failed"
exit "$FAIL"

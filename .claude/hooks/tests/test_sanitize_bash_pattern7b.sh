#!/bin/bash
# Test for sanitize-bash.sh Pattern 7b (sed reformatting read-command output).
# P7 only catches `sed -n '<range>p'`; P7b catches the substitution forms that
# passed P7 and prompted anyway (measured 2026-08-16, both from live screenshots).
# Also asserts P7b does NOT over-reach: -i edits, standalone sed, and non-read
# upstreams must still pass through.

set -u

if ! command -v jq >/dev/null 2>&1; then
  echo "FAIL: jq not installed; this test requires jq for JSON payload construction." >&2
  exit 1
fi

HOOK="${SANITIZE_HOOK:-~/.claude-staging-p7fix/hooks/sanitize-bash.sh}"
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

echo "--- P7b: the two live-observed shapes that P7 missed ---"
probe "screenshot 1: grep -o | sed 's|.*/||' | head" \
  "grep -o '\"[a-z_]*worktree[a-z_]*\"' ~/.claude/state/pre_plan_gates/*.json | sed 's|.*/||' | head -40" \
  2 "sed reformatting command output"

probe "screenshot 2: grep -n | sed 'expr' 'expr'" \
  "grep -n '^[0-9]\\+\\.' /tmp/x.md | sed 's/a/b/' 's/c/d/'" \
  2 "sed reformatting command output"

probe "cat piped into sed" \
  "cat /tmp/x.txt | sed 's/foo/bar/'" \
  2 "sed reformatting command output"

probe "ls piped into sed" \
  "ls /tmp | sed 's/^/PREFIX /'" \
  2 "sed reformatting command output"

echo
echo "--- P7b must NOT over-reach ---"
probe "sed -i in a pipe is an in-place edit, allowed" \
  "grep -l foo /tmp/*.txt | xargs sed -i '' 's/foo/bar/'" \
  0 ""

probe "standalone sed with no read-command upstream" \
  "sed 's/foo/bar/' /tmp/x.txt" \
  0 ""

probe "echo upstream is not a read command" \
  "echo hello | sed 's/h/H/'" \
  0 ""

echo
echo "--- P7 regression: the -n form still blocks with ITS message ---"
probe "sed -n '36,43p' still hits P7, not P7b" \
  "sed -n '36,43p' ${KIT_HOOKS_DIR}/pre_plan_gates.py" \
  2 "sed -n '<range>p' for reading lines"

echo
echo "--- unrelated commands still pass ---"
probe "plain grep pipeline, no sed" \
  "grep -c foo /tmp/x.txt | head -1" \
  0 ""

echo
echo "PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

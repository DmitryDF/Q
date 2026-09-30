#!/bin/bash
# Tests for sanitize-bash.sh Pattern 2 (cd && command), Pattern 8 (cat > write-redirect),
# and Pattern 9 (shell control flow — multi-line and single-line).
# Follows the probe() pattern from test_sanitize_bash_pattern7.sh.

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

echo "=== Pattern 2: cd && command ==="

# Should block: cd && grep (the observed trigger)
probe "block cd && grep"        "cd \"/tmp/proj\" && grep -ril -E \"pattern\" ." 2 "BLOCKED"

# Should block: cd && sqlite3
probe "block cd && sqlite3"     "cd \"/tmp/proj\" && sqlite3 trading.db \".schema\"" 2 "BLOCKED"

# Should block: cd && python (original P2 scope)
probe "block cd && python"      "cd \"/tmp/proj\" && python3 script.py" 2 "BLOCKED"

# Should pass: git -C (the correct alternative)
probe "pass git -C"             "git -C \"/tmp/proj\" status" 0 ""

# Should pass: plain cd (no &&)
probe "pass plain cd"           "cd /tmp" 0 ""

echo ""
echo "=== Pattern 8: cat > write-redirect ==="

# Should block: cat > file heredoc form
probe "block cat > heredoc"     "cat > /tmp/x << 'EOF'
{}
EOF" 2 "BLOCKED"

# Should block: cat > with json content
probe "block cat > macro.json"  "cat > /tmp/macro.json << 'JSONEOF'
{\"key\": \"val\"}
JSONEOF" 2 "BLOCKED"

# Should block: simple cat > redirect
probe "block cat > simple"      "cat > /tmp/out.txt" 2 "BLOCKED"

# Should pass: read-only cat (no >)
probe "pass cat read-only"      "cat /tmp/x" 0 ""

# Should pass: git commit $(cat << 'EOF') — P3 exception; cat > not involved
probe "pass git commit cat heredoc" "git commit -m \"\$(cat <<'EOF'
Msg
EOF
)\"" 0 ""

# Should pass: git commit with 'cat >' in message body — P3/P8 exception
probe "pass git commit body mentions cat>" "git -C /tmp/repo commit -m \"\$(cat <<'EOF'
Fix: cat > write-redirect blocked by P8
EOF
)\"" 0 ""

echo ""
echo "=== Pattern 9: multi-line shell control flow ==="

# Should block: for loop
probe "block for loop"          "for hook in a b c; do
  echo \"\$hook\"
done" 2 "BLOCKED"

# Should block: if/fi block
probe "block if/fi"             "if [ -f ${KIT_HOOKS_DIR}/x.sh ]; then
  echo EXISTS
fi" 2 "BLOCKED"

# Should block: while loop
probe "block while loop"        "while read -r line; do
  echo \"\$line\"
done < /tmp/input.txt" 2 "BLOCKED"

# Should block: case/esac block
probe "block case/esac"         "case \"\$1\" in
  start) echo start ;;
esac" 2 "BLOCKED"

# xfail — VAR=value chain: P9 won't block because no control-flow keywords present.
# This is the known residual gap (Category D sub-variant) documented in Design Review §2.
# Dedicated Pattern 10 deferred to follow-up session.
# probe "xfail VAR=value chain" "DOTCLAUDE=~/.claude
# SLUG=x
# git -C \"\$DOTCLAUDE\" status" 0 ""
echo "SKIP (xfail): VAR=value chain — no control-flow keywords, P9 gap documented in Design Review §2"

# Should pass: git commit with control flow keywords in message body — P9 exception
probe "pass git commit body has for/done" "git -C /tmp/repo commit -m \"\$(cat <<'EOF'
Add P9: multi-line for/while/done/fi/esac blocking
EOF
)\"" 0 ""

# Should pass: single-line git command
probe "pass single-line git"    "git log --oneline -5" 0 ""

# Should pass: single-line with 'for' in commit message (no newline)
probe "pass for in message"     "git commit -m \"fix: update for loop handling\"" 0 ""

# Should pass: multi-line without control flow keywords (no for/while/done/fi/esac)
probe "pass multiline no keywords" "echo line1
echo line2" 0 ""

echo ""
echo "=== Pattern 9 Form B: single-line loops ==="

# Should block: single-line for loop (the observed trigger — Image 3)
probe "block single-line for loop" \
    "for db in \"/tmp/a.db\" \"/tmp/b.db\"; do echo \"\$db\"; sqlite3 \"\$db\" \".tables\"; done" \
    2 "BLOCKED"

# Should block: single-line while loop
probe "block single-line while loop" \
    "while read -r line; do echo \"\$line\"; done < /tmp/input.txt" \
    2 "BLOCKED"

# Should pass: git commit body with 'for ... do' text — P9B exception
probe "pass git commit body for do" \
    "git -C /tmp/repo commit -m \"\$(cat <<'EOF'
Use for db in list; do ... done pattern
EOF
)\"" 0 ""

# Should pass: 'for' without 'do' (e.g. in a string argument)
probe "pass for without do"     "git log --format=\"%s\" | grep \"for release\"" 0 ""

echo ""
echo "Results: $PASS passed, $FAIL failed"
exit "$FAIL"

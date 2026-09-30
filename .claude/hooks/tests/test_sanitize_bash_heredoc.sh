#!/bin/bash
# Tests for sanitize-bash.sh Pattern 6: python heredoc

HOOK="${KIT_HOOKS_DIR}/sanitize-bash.sh"
PASSED=0
FAILED=0

run_hook() {
    local cmd="$1"
    local payload
    payload=$(printf '{"tool_name":"Bash","tool_input":{"command":"%s"}}' "$cmd")
    echo "$payload" | bash "$HOOK"
    return $?
}

assert_exit() {
    local expected="$1"
    local actual="$2"
    local label="$3"
    if [ "$actual" -eq "$expected" ]; then
        echo "  PASS  $label"
        PASSED=$((PASSED + 1))
    else
        echo "  FAIL  $label (expected exit $expected, got $actual)"
        FAILED=$((FAILED + 1))
    fi
}

# python3 - << 'EOF' → exit 2 (Pattern 6)
run_hook "python3 - << 'PYEOF'"
assert_exit 2 $? "python3 - << heredoc → exit 2"

# python - << EOF → exit 2 (Pattern 6)
run_hook "python - << EOF"
assert_exit 2 $? "python - << heredoc → exit 2"

# python3 script.py → exit 0 (not a heredoc, not otherwise blocked)
run_hook "python3 scripts/health.py"
assert_exit 0 $? "python3 script.py → exit 0"

# python3 -u script.py → exit 0
run_hook "python3 -u scripts/watchlist.py"
assert_exit 0 $? "python3 -u script.py → exit 0"

echo ""
echo "$PASSED/$((PASSED + FAILED)) passed"
[ $FAILED -eq 0 ]

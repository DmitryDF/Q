#!/usr/bin/env bash
# Self-test for check-skill-marker.sh (orchestrator-pattern S4 guardrail).
# Runs the hook against a sentinel CLAUDE_CONFIG_DIR + sentinel shadow-map so it
# never touches live config or markers. Verifies the fail-closed contract.
set -u

HOOK="$(cd "$(dirname "$0")/.." && pwd)/check-skill-marker.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Sentinel config dir: put a copy of skill_marker.py where the hook expects it.
mkdir -p "$TMP/hooks" "$TMP/state/skill-markers"
cp "$(dirname "$HOOK")/skill_marker.py" "$TMP/hooks/skill_marker.py"

MAP="$TMP/state/skill-markers/shadow-map.json"
printf '%s\n' '{"shadows": {"sentinel-agent": "sentinel-skill"}}' > "$MAP"

export CLAUDE_CONFIG_DIR="$TMP"
export SKILL_SHADOW_MAP="$MAP"

SID="sid-s4-test"
PASS=0
FAIL=0

run_case() {  # $1 label  $2 expected_exit  $3 stdin_json
  local label="$1" expected="$2" json="$3" got
  printf '%s' "$json" | bash "$HOOK" >/dev/null 2>&1
  got=$?
  if [ "$got" -eq "$expected" ]; then
    PASS=$((PASS+1)); printf '  ok   %s (exit %s)\n' "$label" "$got"
  else
    FAIL=$((FAIL+1)); printf '  FAIL %s (expected %s, got %s)\n' "$label" "$expected" "$got"
  fi
}

# 1. Non-Agent tool → allow.
run_case "non-Agent tool" 0 \
  '{"tool_name":"Bash","tool_input":{"command":"ls"},"session_id":"'"$SID"'"}'

# 2. Agent, subagent_type not in shadow map → allow.
run_case "non-shadow subagent_type" 0 \
  '{"tool_name":"Agent","tool_input":{"subagent_type":"Explore"},"session_id":"'"$SID"'"}'

# 3. Agent, shadowing type, NO marker → block (fail-closed).
run_case "shadow + no marker -> BLOCK" 2 \
  '{"tool_name":"Agent","tool_input":{"subagent_type":"sentinel-agent"},"session_id":"'"$SID"'"}'

# 4. Write the canonical skill marker, then the same dispatch → allow.
python3 "$TMP/hooks/skill_marker.py" write sentinel-skill "$SID" >/dev/null 2>&1
run_case "shadow + marker present -> ALLOW" 0 \
  '{"tool_name":"Agent","tool_input":{"subagent_type":"sentinel-agent"},"session_id":"'"$SID"'"}'

# 5. Agent, shadowing type, but no session_id → allow (cannot resolve marker).
run_case "shadow + no session_id" 0 \
  '{"tool_name":"Agent","tool_input":{"subagent_type":"sentinel-agent"}}'

# 5b. Corrupt/unreadable marker → block (fail-closed). The marker dir was created
#     by case 4's write; overwrite the marker with non-JSON garbage.
printf '%s' 'not-json{' > "$TMP/state/skill-markers/$SID/sentinel-skill.marker"
run_case "shadow + unreadable marker -> BLOCK" 2 \
  '{"tool_name":"Agent","tool_input":{"subagent_type":"sentinel-agent"},"session_id":"'"$SID"'"}'

# 6. Empty shadow map → no-op allow even for a would-be shadow type.
printf '%s\n' '{"shadows": {}}' > "$MAP"
run_case "empty shadow map -> no-op" 0 \
  '{"tool_name":"Agent","tool_input":{"subagent_type":"sentinel-agent"},"session_id":"'"$SID"'"}'

printf '\ncheck-skill-marker self-test: %s passed, %s failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

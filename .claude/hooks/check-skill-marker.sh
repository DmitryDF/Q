#!/usr/bin/env bash
set -eu

# PreToolUse hook: named-shadowing guardrail (orchestrator-pattern Slice S4).
#
# Blocks an `Agent` dispatch that SHADOWS a canonical skill when that skill's
# session marker is absent — i.e. a hand-rolled look-alike that bypasses the
# real skill's gates / methodology / fact-checking. Clone of the proven
# research-scope-gate.sh marker→PreToolUse pattern (locked A9 / A15).
#
# Contract:
#   stdin    JSON PreToolUse payload — .tool_name, .tool_input.subagent_type, .session_id
#   exit 0   allowed — not an Agent dispatch, subagent_type does not shadow a
#            canonical skill, OR the shadowed skill's marker is present
#   exit 2   blocked — shadowing dispatch AND the canonical skill's marker is
#            absent/unreadable (fail-closed)
#
# The mapping (which subagent_type shadows which skill) is DATA, not code:
#   $CLAUDE_CONFIG_DIR/state/skill-markers/shadow-map.json  →  {"shadows": {"<type>": "<skill>"}}
# Entries are added as skills are retrofitted to write their marker (S7+); an
# empty map (or absent file) makes this hook a safe no-op.
#
# Env overrides (tests):
#   SKILL_SHADOW_MAP   alternate shadow-map.json path
#   CLAUDE_CONFIG_DIR  alternate config dir (marker + default map root)

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
# Only Agent dispatches can shadow a skill; everything else is out of scope.
[ "$TOOL_NAME" != "Agent" ] && exit 0

SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty')
# No subagent_type → not a named dispatch we can classify; default-open.
[ -z "$SUBAGENT_TYPE" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
# No session id → cannot resolve a per-session marker; default-open (mirrors
# research-scope-gate.sh:47 — do not gate non-session contexts).
[ -z "$SESSION_ID" ] && exit 0

CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SHADOW_MAP="${SKILL_SHADOW_MAP:-$CONFIG_DIR/state/skill-markers/shadow-map.json}"

# No map on disk → nothing is gated yet.
[ -f "$SHADOW_MAP" ] || exit 0

# Which canonical skill does this subagent_type shadow? (empty → not a shadow)
SKILL=$(jq -r --arg t "$SUBAGENT_TYPE" '.shadows[$t] // empty' "$SHADOW_MAP" 2>/dev/null || echo "")
[ -z "$SKILL" ] && exit 0

# Shadowing dispatch → the canonical skill's marker MUST be present. The skill
# writes it on start; its own internal dispatch therefore passes, while a
# hand-rolled look-alike (skill never ran → no marker) is blocked. Fail-closed:
# skill_marker.py check exits 2 on absent/unreadable.
if python3 "$CONFIG_DIR/hooks/skill_marker.py" check "$SKILL" "$SESSION_ID" >/dev/null 2>&1; then
  exit 0
fi

# Record the blocked look-alike as a metrics signal (S5 A17 — guardrail-hook
# byproduct). Best-effort: a metrics-write failure must never prevent the block.
BLOCK_ROW=$(jq -nc --arg skill "$SKILL" --arg sid "$SESSION_ID" --arg t "$SUBAGENT_TYPE" \
  '{skill:$skill,sid:$sid,run_kind:"blocked",note:("look-alike dispatch blocked: "+$t)}' 2>/dev/null || echo "")
[ -n "$BLOCK_ROW" ] && python3 "$CONFIG_DIR/hooks/skill_runs.py" append "$BLOCK_ROW" >/dev/null 2>&1 || true

cat >&2 <<EOF
✗ check-skill-marker: dispatch of subagent_type '${SUBAGENT_TYPE}' shadows the canonical skill '/${SKILL}', but '/${SKILL}' has not run this session (no skill marker).

Route this work through the real skill — invoke /${SKILL}. Its gates, methodology, and fact-checking only hold when the skill itself runs; a hand-rolled '${SUBAGENT_TYPE}' Agent is not equivalent.

(orchestrator-pattern guardrail — ~/.claude/rules/orchestrator-pattern.md §5. Fail-closed: missing/unreadable marker = blocked.)
EOF
exit 2

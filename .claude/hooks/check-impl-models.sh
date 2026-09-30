#!/usr/bin/env bash
# PostToolUse hook on Agent — Slice I (S-I-Impl-2 / G5 / C5),
# scoped 2026-07-12 (check-impl-models-scope-fix).
#
# Runtime model enforcement: validate that the subagent the harness just
# spawned actually ran on the model family INTENDED for it. Modeled on
# `_factcheck_engine.py:380-393` `provenance: code-verified` pattern — read the
# harness-written subagent transcript at
# `~/.claude/projects/<sessionId>/subagents/agent-<agentId>.jsonl`, normalize
# every assistant turn's `.message.model` to a family slug, and compare.
#
# Scoping (2026-07-12) — the contract fires ONLY on a plan's IMPLEMENTATION
# spawns, restoring the original A5 intent that over-reach had eroded:
#   A2 intent  — a data-driven skip-list (state/impl-models/skip-list.json)
#     skips verification (`Explore`) and session-close (`close-*`) subagents.
#     DENY-LIST: an unknown/absent subagent_type is still enforced (a plan
#     cannot opt out — anti-bypass preserved).
#   A1 timing  — a not-yet-flushed transcript FAILS OPEN with a warning (a
#     write-lag is not a downgrade). A present transcript with a wrong/mixed
#     family still blocks. (Revises A5's original explicit fail-closed default.)
#   A4 precise — when the spawn prompt carries `[ACTION:An]` (→ that action's
#     declared Model) or `[MODEL:fam]` (→ the dispatcher's resolved family,
#     stamped by execute-plan's run.stamp_model), compare against THAT spawn's
#     own intended model, not the whole-plan union. Untagged spawns fall back to
#     the union check WITH a warning (no weaker than the prior status quo).
#
# Enforces for ANY plan that declares a real model family in its 7-column
# Coherent Actions table (Model+Reason, made mandatory by check-plan-gates.sh).
# A plan that declares no real model is a no-op via the empty-DECLARED guard.
# Quiet exit-0 on every non-Agent tool, skip-listed subagent_type, plan with no
# declared model, missing session, or absent plan file.
#
# Exit codes:
#   0 = allow (skip-listed spawn; missing-transcript timing grace; no declared
#       model; or actual family matches the per-spawn / union expectation)
#   2 = block (per-action / per-model / union mismatch, or mixed-family transcript)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "Agent" ] && exit 0

# --- A2: spawn-intent scoping (data-driven deny-list) ---
# Only a plan's IMPLEMENTATION spawns are in scope. Verification (subagent_type
# Explore) and session-close (close-diary-drafter / close-commit-composer)
# subagents are NOT plan work — skip them. The list is DATA (JSON), mirroring
# check-skill-marker.sh's shadow-map.json pattern, so a new verification/close
# type is added without editing this hook. DENY-LIST ONLY: an unknown or absent
# subagent_type falls THROUGH to enforcement, preserving the anti-bypass property
# (a plan cannot opt out by naming a type). Absent/empty list → enforce as before.
SUBAGENT_TYPE=$(echo "$INPUT" | jq -r '.tool_input.subagent_type // empty' 2>/dev/null)
CONFIG_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SKIP_LIST="${IMPL_MODELS_SKIP_LIST:-$CONFIG_DIR/state/impl-models/skip-list.json}"
if [ -n "$SUBAGENT_TYPE" ] && [ -f "$SKIP_LIST" ]; then
  if jq -e --arg t "$SUBAGENT_TYPE" '(.skip // []) | index($t)' "$SKIP_LIST" >/dev/null 2>&1; then
    exit 0
  fi
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAN_FILE=$("$SCRIPT_DIR/find-session-plan.sh" "$SESSION_ID" 2>/dev/null) || exit 0
[ -z "$PLAN_FILE" ] && exit 0
[ ! -f "$PLAN_FILE" ] && exit 0

# Model contract is UNCONDITIONAL: enforce for ANY plan that declares a real
# model family in its Coherent Actions table. The empty-DECLARED guard below
# governs the no-op case (no model declared → nothing to enforce). Previously
# this early-exited unless the plan carried the GATE0SR:SLICES opt-in marker —
# removed so runtime enforcement is not bypassable by omitting the marker.

# Union of declared model families from the 7-column Coherent Actions table.
# Row schema: | Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
# awk -F'|' yields: $1=leading, $2..$8 = columns, $9=trailing → Model is $7.
# Header rows have "Model" in $7; allowlist check filters them out.
DECLARED=$(awk -F'|' '
  NF >= 9 {
    m=$7
    gsub(/^[ \t]+|[ \t]+$/, "", m)
    m=tolower(m)
    if (m=="sonnet" || m=="opus" || m=="haiku") print m
  }
' "$PLAN_FILE" | sort -u)

# No declared models → nothing to enforce (e.g. a plan with em-dash Model
# values only, or before Coherent Actions are filled).
[ -z "$DECLARED" ] && exit 0

# Resolve agent_id from the PostToolUse input. The harness emits the
# subagent transcript at agent-<agentId>.jsonl; the agent id is usually
# carried as tool_use_id (with optional `agent-` prefix that the harness
# also uses in the filename).
AGENT_ID=$(echo "$INPUT" | jq -r '.tool_use_id // empty' 2>/dev/null)
[ -z "$AGENT_ID" ] && AGENT_ID=$(echo "$INPUT" | jq -r '.tool_response.agent_id // .agent_id // empty' 2>/dev/null)
case "$AGENT_ID" in
  agent-*) AGENT_ID="${AGENT_ID#agent-}" ;;
esac

PROJECTS_ROOT="$HOME/.claude/projects"
TRANSCRIPT=""
if [ -n "$AGENT_ID" ]; then
  DIRECT="$PROJECTS_ROOT/$SESSION_ID/subagents/agent-$AGENT_ID.jsonl"
  if [ -f "$DIRECT" ]; then
    TRANSCRIPT="$DIRECT"
  else
    TRANSCRIPT=$(find "$PROJECTS_ROOT" -maxdepth 5 -name "agent-$AGENT_ID.jsonl" -type f 2>/dev/null | head -1)
  fi
fi

if [ -z "$TRANSCRIPT" ] || [ ! -f "$TRANSCRIPT" ]; then
  # A1: timing grace — a not-yet-written transcript is a harness write-lag, NOT a
  # model violation. Fail OPEN with a warning (exit 0), distinct from a block.
  # The load-bearing detection is preserved: a transcript that EXISTS with a
  # wrong/mixed family still exits 2 below. (Revises A5's original explicit
  # fail-closed-on-missing-transcript default — justified because PostToolUse is
  # advisory and the write-lag is not a downgrade.) The word "BLOCKED" is
  # deliberately absent so this is not mistaken for an enforcement block.
  cat >&2 <<EOF
NOTE (check-impl-models): subagent transcript not yet available for agent_id=${AGENT_ID:-?}
(harness may not have flushed ~/.claude/projects/<sessionId>/subagents/agent-<agentId>.jsonl).
Skipping the model check for this spawn (fail-open on a timing lag; a present transcript with a
wrong family still blocks). Plan ${PLAN_FILE}.
EOF
  exit 0
fi

# Normalize every assistant-turn .message.model to a family slug.
FAMILIES=$(jq -r 'select(.message.role=="assistant") | .message.model // empty' "$TRANSCRIPT" 2>/dev/null \
  | grep -E '^claude-[a-z]+-' \
  | sed -E 's/^claude-([a-z]+)-.*/\1/' \
  | sort -u)

# No assistant turns yet → nothing to check (very early read; harmless).
[ -z "$FAMILIES" ] && exit 0

FAMILY_COUNT=$(printf '%s\n' "$FAMILIES" | wc -l | tr -d ' ')
if [ "$FAMILY_COUNT" -gt 1 ]; then
  cat >&2 <<EOF
BLOCKED: subagent transcript $TRANSCRIPT contains mixed model families:
  observed: $(printf '%s' "$FAMILIES" | tr '\n' ' ')
All assistant turns must share one family (canon §1 + _factcheck_engine.py
provenance: code-verified pattern).
EOF
  exit 2
fi

ACTUAL="$FAMILIES"

# A4 — per-spawn precise comparison. Two tag forms, checked in priority order:
#   1. [ACTION:An]  — the spawn's own Coherent Action (A5:159 "current in-flight
#      Action"); look up that action's declared Model in the plan table.
#   2. [MODEL:fam]  — the model the dispatcher (execute-plan) RESOLVED and intends
#      for this spawn; verify the observed family matches it directly. execute-plan
#      works per-slice with a resolved model_family (no Coherent-Action id), so this
#      is the form it stamps — verifying "ran on the intended model" per spawn.
# When neither tag resolves, fall back to the whole-plan UNION check WITH a warning
# — never silently more permissive than the shipped status quo.
PROMPT=$(printf '%s' "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null)
ACTION_ID=$(printf '%s' "$PROMPT" | grep -oE '\[ACTION:[A-Za-z0-9_.-]+\]' | head -1 \
  | sed -E 's/\[ACTION:([A-Za-z0-9_.-]+)\]/\1/')
MODEL_TAG=$(printf '%s' "$PROMPT" | grep -oE '\[MODEL:(sonnet|opus|haiku)\]' | head -1 \
  | sed -E 's/\[MODEL:([a-z]+)\]/\1/')

ACTION_MODEL=""
if [ -n "$ACTION_ID" ]; then
  ACTION_MODEL=$(awk -F'|' -v aid="$ACTION_ID" '
    NF >= 9 {
      a=$2; m=$7
      gsub(/^[ \t]+|[ \t]+$/, "", a); gsub(/^[ \t]+|[ \t]+$/, "", m)
      m=tolower(m)
      if (a==aid && (m=="sonnet"||m=="opus"||m=="haiku")) { print m; exit }
    }
  ' "$PLAN_FILE")
fi

if [ -n "$ACTION_ID" ] && [ -n "$ACTION_MODEL" ]; then
  # Precise path: compare the observed family against THIS action's declared model.
  if [ "$ACTUAL" != "$ACTION_MODEL" ]; then
    cat >&2 <<EOF
BLOCKED: Model mismatch on Agent spawn for Coherent Action ${ACTION_ID}.
  observed family: $ACTUAL
  action ${ACTION_ID} declared: $ACTION_MODEL
  plan: $PLAN_FILE
Re-spawn with the declared model, or update the plan's Model column for ${ACTION_ID}.
EOF
    exit 2
  fi
  exit 0
fi

if [ -n "$MODEL_TAG" ]; then
  # Dispatcher-declared intended model (execute-plan [MODEL:fam]). Verify the
  # observed family matches what was intended for this spawn.
  if [ "$ACTUAL" != "$MODEL_TAG" ]; then
    cat >&2 <<EOF
BLOCKED: Model mismatch on Agent spawn.
  observed family: $ACTUAL
  dispatcher-intended model: $MODEL_TAG
  plan: $PLAN_FILE
Re-spawn with the intended model (the dispatcher resolved $MODEL_TAG for this spawn).
EOF
    exit 2
  fi
  exit 0
fi

# Fallback: whole-plan UNION check WITH a warning (no [ACTION:]/[MODEL:] tag, or the
# tagged action was not found in the plan). No weaker than the shipped status quo.
if [ -n "$ACTION_ID" ]; then
  echo "NOTE (check-impl-models): action ${ACTION_ID} not found in ${PLAN_FILE}; falling back to whole-plan union check." >&2
else
  echo "NOTE (check-impl-models): spawn carries no [ACTION:]/[MODEL:] tag; falling back to whole-plan union check (per-spawn precision unavailable)." >&2
fi
if ! printf '%s\n' "$DECLARED" | grep -qFx "$ACTUAL"; then
  cat >&2 <<EOF
BLOCKED: Model mismatch on Agent spawn.
  observed family: $ACTUAL
  plan-declared union: $(printf '%s' "$DECLARED" | tr '\n' ',' | sed 's/,$//')
  plan: $PLAN_FILE
Re-spawn with a declared model, or update the plan's Model column for this Action.
EOF
  exit 2
fi

exit 0

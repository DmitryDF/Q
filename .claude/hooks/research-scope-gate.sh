#!/usr/bin/env bash
set -eu

# PreToolUse hook: research-scope gate.
#
# Blocks search-shaped tool calls (WebSearch, WebFetch, Agent,
# mcp__claude-in-chrome__.*, and Bash matched by spine-scoped patterns) until the
# research_pipeline manifest carries r1_scope_approved=true for
# (SESSION_ID, CYCLE_ID).
#
# Spec: Thoughts/research-scope-and-focus_THOUGHT.md Architecture Decision A2 +
# ~/.claude/plans/lazy-doodling-wadler.md Coherent Action A2 + A9.
#
# Contract:
#   stdin    JSON PreToolUse payload — .tool_name, .tool_input, .session_id
#   exit 0   approved (fast-exit) OR Bash command not spine-scoped (no gating)
#   exit 2   blocked — manifest absent or cycle's r1_scope_approved unset/false
#
# Environment overrides:
#   RP_STATE_DIR                alternate research_pipeline state dir (mirrors research_pipeline.py)
#   RESEARCH_SCOPE_GATE_NO_ORIENT=1  skip the topic-orient check (for subprocess tests only)
#
# NOT an input: CYCLE_ID. Until D1 this gate resolved its cycle from
# `${CYCLE_ID:-default}` — a variable NO code in the harness ever sets, so the
# expression could only ever take the value `default` (measured: 478 of 478
# recorded side-channel files read `default`). A German- or Russian-only run,
# whose approval sits on cycle `de`/`ru`, was therefore refused seconds after
# the person approved it. The cycle is now resolved from the manifest — the key
# this surface actually holds — via `research_pipeline.py scope-status`.

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ -z "$TOOL_NAME" ] && exit 0

# Bash matcher: only fire on spine-scoped command patterns. Out-of-spine Bash
# (curl/wget/urllib/node-fetch — locked Discovery `## Scope` "In" residual) is
# explicitly not gated by this hook.
if [ "$TOOL_NAME" = "Bash" ]; then
  CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
  # Spine pattern (a): browser-automation drivers — must follow a python invocation.
  # Spine pattern (b): write-redirect (>, >>) targeting *_RESEARCH*.md anywhere
  #                    in the command (matches _RESEARCH.md, _RESEARCH_DE.md, …).
  if ! printf '%s' "$CMD" | grep -qE 'python[^|;&]*(patchright|playwright|chromium|headless)'; then
    if ! printf '%s' "$CMD" | grep -qE '>>?[[:space:]]*[^[:space:]]*_RESEARCH[^[:space:]]*\.md(\b|$)'; then
      exit 0
    fi
  fi
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
# No session id surfaced → cannot resolve manifest; default-open (mirrors
# check-research-pipeline-gate.sh:20 and avoids gating non-session contexts).
[ -z "$SESSION_ID" ] && exit 0

STATE_DIR="${RP_STATE_DIR:-$HOME/.claude/state/research_pipeline}"
STATE_FILE="$STATE_DIR/RP-${SESSION_ID}.json"

# Slice C (cryptic-mapping-torvalds.md C5): consult topic-orient first.
# Exemption rules:
#   - intake_source_class ∈ {plain, thought-bound, worktree-origin} → exit 0
#     (session is not research-scoped; gate has nothing to enforce)
#   - intake_source_class == "none" with any verdict produced by topic-orient
#     → exit 0 (no topic state → not a research session)
#   - intake_source_class == "research" (or missing/unknown) → fall through to
#     the manifest check below (fail-closed)
ORIENT_VERDICT=""
ORIENT_CLASS=""
if [ -z "${RESEARCH_SCOPE_GATE_NO_ORIENT:-}" ]; then
ORIENT_JSON=$(python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" \
  topic-orient "$SESSION_ID" 2>/dev/null) || ORIENT_JSON=""
else
  ORIENT_JSON=""
fi
if [ -n "$ORIENT_JSON" ]; then
  ORIENT_VERDICT=$(printf '%s' "$ORIENT_JSON" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('verdict', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
  ORIENT_CLASS=$(printf '%s' "$ORIENT_JSON" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('intake_source_class', ''))
except Exception:
    print('')
" 2>/dev/null || echo "")
  case "$ORIENT_CLASS" in
    plain|thought-bound|worktree-origin) exit 0 ;;
    none) [ "$ORIENT_VERDICT" != "" ] && exit 0 ;;
    research|"") : ;;
    *) : ;;   # forward-compat: future enum values default to fall-through (fail-closed)
  esac
fi

# D1: resolve the cycle from the manifest — the key this surface actually holds
# — instead of the producerless CYCLE_ID variable. Placed AFTER the topic-orient
# exemption on purpose: a session exempted above never pays for this call, which
# matters because this gate is registered on the Bash matcher and so runs on
# every Bash call in every session.
#
# The resolver returns the decision, the DECIDING cycle id, and that cycle's OWN
# r1_scope_approved / r1_scope_revoked flags. The flags are what let the
# message-selection test below stay exactly as it was: a revoked-but-never-
# approved cycle is blocked and still gets the GENERIC text, because
# `APPROVED = true AND REVOKED = true` is false for it now as it was before.
#
# Any non-zero exit, empty or unparseable output leaves SCOPE_APPROVED empty and
# CYCLE_ID at `default`, so the jq fallback below reproduces today's behaviour —
# mirroring the topic-orient handling at line 66 above, which empties its
# variable on failure rather than propagating an error.
#
# The resolver is addressed RELATIVE TO THIS SCRIPT (the pattern
# research-linkcheck.sh:43 already uses), not through a hardcoded
# $HOME/.claude/hooks. A hardcoded path makes the gate read one tree's schema
# module while running another tree's gate — which is precisely the breakage an
# isolated config clone exists to catch, and it caught it here.
CYCLE_ID="default"
SCOPE_APPROVED=""
SCOPE_REVOKED=""
if [ -f "$STATE_FILE" ]; then
  # Guarded on the manifest existing so a session with no research manifest
  # never pays for the spawn: with no manifest the resolver would answer
  # none/`default` anyway, which is what the initialisers above already say.
  GATE_HOOKS_DIR="$(dirname "$0")"
  SCOPE_JSON=$(python3 "$GATE_HOOKS_DIR/research_pipeline.py" \
    scope-status "$SESSION_ID" 2>/dev/null) || SCOPE_JSON=""
  if [ -n "$SCOPE_JSON" ]; then
    # Parsed with jq, not a second python spawn: jq is already this file's
    # parser (below) and costs ~10ms against ~110ms for a python interpreter
    # start, on a gate registered for the Bash matcher. Booleans first and the
    # cycle id last, so `cut -f3-` keeps an id that itself contains a tab.
    SCOPE_LINE=$(printf '%s' "$SCOPE_JSON" | jq -r '
      [(.r1_scope_approved // false | tostring),
       (.r1_scope_revoked  // false | tostring),
       (.cycle_id // "default")] | @tsv' 2>/dev/null || echo "")
    if [ -n "$SCOPE_LINE" ]; then
      SCOPE_APPROVED=$(printf '%s' "$SCOPE_LINE" | cut -f1)
      SCOPE_REVOKED=$(printf '%s' "$SCOPE_LINE" | cut -f2)
      CYCLE_ID=$(printf '%s' "$SCOPE_LINE" | cut -f3-)
      [ -z "$CYCLE_ID" ] && CYCLE_ID="default"
    fi
  fi
fi

if [ -f "$STATE_FILE" ]; then
  # Honor manifest-level bypass (research_pipeline.py:170 `state.bypass`).
  BYPASS=$(jq -r '.bypass // false' "$STATE_FILE" 2>/dev/null || echo "false")
  [ "$BYPASS" = "true" ] && exit 0
  if [ -n "$SCOPE_APPROVED" ]; then
    APPROVED="$SCOPE_APPROVED"
    REVOKED="$SCOPE_REVOKED"
  else
    # Resolver unavailable — fall back to today's single-cycle read.
    APPROVED=$(jq -r --arg cid "$CYCLE_ID" '.cycles[$cid].r1_scope_approved // false' "$STATE_FILE" 2>/dev/null || echo "false")
    REVOKED=$(jq -r --arg cid "$CYCLE_ID" '.cycles[$cid].r1_scope_revoked // false' "$STATE_FILE" 2>/dev/null || echo "false")
  fi
  # S7: exit 0 iff r1_scope_approved=true AND NOT r1_scope_revoked.
  if [ "$APPROVED" = "true" ] && [ "$REVOKED" = "true" ]; then
    # Cycle was revoked by an autonomous-headless abort. Plain-English remediation
    # per Q11 — no internal identifiers in user-facing text.
    cat >&2 <<EOF
✗ research-scope-gate: this research session was aborted earlier and is blocked from restarting.

Run the following command to recover and start a fresh research session:
  python3 ${KIT_HOOKS_DIR}/research_pipeline.py reset ${SESSION_ID} --cycle-id ${CYCLE_ID}

After the reset, restart your research with /research.
EOF
    exit 2
  fi
  [ "$APPROVED" = "true" ] && exit 0
  # Manifest exists but cycle not approved → fall through to blocking remediation.
fi

cat >&2 <<EOF
✗ research-scope-gate: r1_scope_approved is not set for (session=${SESSION_ID}, cycle_id=${CYCLE_ID}).

Tool '${TOOL_NAME}' is gated by the research scope-approval contract
(Thoughts/research-scope-and-focus_THOUGHT.md, Solution Alternative 1 / A2).

Resolution: complete scope-framing through /research, /clarification Step 6, or
/work-decode S3 — each advances the manifest to r1_scope_approved at r0_intake.
The gate then exits 0 on the next call.

Inspect: python3 ${KIT_HOOKS_DIR}/research_pipeline.py read ${SESSION_ID}
EOF
exit 2

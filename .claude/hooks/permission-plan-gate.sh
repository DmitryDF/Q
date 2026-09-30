#!/bin/bash
# PermissionRequest hook for ExitPlanMode.
# Fires when Claude requests permission to exit plan mode.
# Finds the most recent plan file and checks for gate markers.
#
# Exit codes:
#   0 = allow (permission dialog shown to user as normal)
#   2 = deny (gates incomplete — stderr fed to Claude)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

# ---------------------------------------------------------------------------
# Locating the plan's `thought_file:` — by the plan's own STRUCTURE, never by a
# line count. First inside the `<!-- GATE0:CHAIN_SRC -->` block; when that block
# yields no such line (marker absent, block empty — the `<!--`-on-next-line shape
# — or key absent) fall back to the first column-0 `thought_file:` line anywhere
# in the file. Column-0 match only; `n/a` is returned verbatim for the caller to
# treat as "no spine".
# ---------------------------------------------------------------------------
_chain_src_block() {
  # Copied from check-plan-gates.sh `_block_after_marker` (the 4-line awk), taking
  # the file as an argument. Copied, not extracted: no second caller justifies a
  # shared helper, and check-plan-gates.sh carries a 100+-case suite a refactor
  # would put in play for four lines.
  awk -v marker="<!-- GATE0:CHAIN_SRC -->" '
    $0 == marker { found=1; next }
    found && /^<!--/ { exit }
    found { print }
  ' "$1"
}
_locate_thought_file() {
  local plan="$1" tf
  tf=$(_chain_src_block "$plan" | grep '^thought_file:' | head -1 | sed 's/^thought_file:[[:space:]]*//')
  if [ -z "$tf" ]; then
    tf=$(grep '^thought_file:' "$plan" | head -1 | sed 's/^thought_file:[[:space:]]*//')
  fi
  printf '%s' "$tf"
}
# Test/dry-run seam: `permission-plan-gate.sh --locate-thought-file <plan>` prints the
# located value (empty when none) and exits 0, so the end-to-end gate test and the
# corpus dry-run exercise THIS rule rather than a re-implementation of it.
if [ "${1:-}" = "--locate-thought-file" ]; then
  [ -f "${2:-}" ] || exit 1
  _locate_thought_file "$2"
  echo
  exit 0
fi

# Read stdin (PermissionRequest JSON with tool_name, tool_input)
INPUT=$(cat)

# Only applies to ExitPlanMode
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
if [ "$TOOL_NAME" != "ExitPlanMode" ]; then
  exit 0
fi

# Look up this session's plan file via the manifest (session-scoped —
# avoids cross-session lookup bugs when multiple terminals plan at once)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAN_FILE=$("$SCRIPT_DIR/find-session-plan.sh" "$SESSION_ID") || exit 0

# Delegate to shared gate checker
"$SCRIPT_DIR/check-plan-gates.sh" "$PLAN_FILE"
GATE_RESULT=$?

if [ "$GATE_RESULT" -eq 0 ]; then

  # Kill switch — bypass all pre-plan checks
  if [ -f "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/pre_plan_gates_disabled" ]; then
    if ! grep -q '<!-- APPROVED -->' "$PLAN_FILE"; then
      echo "" >> "$PLAN_FILE"
      echo "<!-- APPROVED -->" >> "$PLAN_FILE"
    fi
    exit 0
  fi

  # Pre-plan check 1: gate state file (no file = vanilla mode, allow)
  python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" check "$SESSION_ID"
  PRE_RESULT=$?
  if [ "$PRE_RESULT" -eq 1 ]; then
    exit 2
  fi

  # Pre-plan check 2: Model|Reason columns in Coherent Actions table
  if ! grep -q '|.*Model.*|.*Reason.*|' "$PLAN_FILE"; then
    echo "✗ Coherent Actions table missing Model|Reason columns." >&2
    echo "Every action must have a Model assignment and Reason." >&2
    exit 2
  fi

  # Pre-plan check 3: Workflow/KL Alignment section present (accepts legacy Process/KL for back-compat)
  if ! grep -qE '## (Workflow|Process)/KL Alignment' "$PLAN_FILE"; then
    echo "✗ Plan missing '## Workflow/KL Alignment' section." >&2
    echo "  (Legacy '## Process/KL Alignment' also accepted for backward-compat with pre-S3 plans.)" >&2
    echo "Every business rule must trace to workflow doc or KL. Add section before Guiding Policy." >&2
    exit 2
  fi

  # Pre-plan check 4: Opus actions must have non-empty Reason
  python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" check-model-reasons "$PLAN_FILE"
  if [ $? -ne 0 ]; then
    exit 2
  fi

  # Pre-plan check 5: the Thought spine the plan names (its `thought_file:` line) is
  # validated LIVE, right here, by running `_validate-thought-file.py` on the resolved
  # spine. The gate used to read a `<slug>_THOUGHT_check.md` sidecar that only a
  # Write|Edit save of the spine produced (verify-thought-file.sh) — a note that was
  # structurally FAIL for every spine since the `classification` field was retired
  # and that a spine written any other way (the /clarification-v2 door's Python
  # write) never got at all. The sidecar is now advisory early feedback only: its
  # presence, absence, or verdict has no effect on approval.
  #
  # Mode-C (Ninja-Plan) plans carry `thought_file: n/a` — the sentinel means "no
  # spine", so there is nothing to validate. Skip this check for the literal `n/a`
  # value (mirrors the `discovery_src_hash: n/a` sentinel).
  #
  # `thought_file:` is located by the plan's own STRUCTURE (`_locate_thought_file`,
  # defined at the top of this script), never by a line count.
  THOUGHT_FILE=$(_locate_thought_file "$PLAN_FILE")
  if [ -n "$THOUGHT_FILE" ] && [ "$THOUGHT_FILE" != "n/a" ]; then
    # Resolve thought_file path: absolute pass-through, OR resolve a relative path
    # existence-based against two roots — the session's project_root (from topic
    # state, `read` emits it under `.topic_state.project_root`), then the canonical
    # PROJECTS_ROOT — taking the first root that actually holds the file. The corpus
    # carries both relative shapes (project-relative `Thoughts/<slug>_THOUGHT.md` and
    # Projects-root-relative `Personal/<proj>/Thoughts/<slug>_THOUGHT.md`), so a
    # single-root rule breaks one of them. When neither root holds the file, the
    # first candidate is kept so the validator's "File not found" names it.
    # PROJECTS_ROOT is read via the `projects-root` subcommand (A9) — single source
    # of truth in pre_plan_gates.py. An unbound session yields an empty state root
    # and falls straight to PROJECTS_ROOT.
    PROJECTS_ROOT="$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" projects-root)"
    if [[ "$THOUGHT_FILE" == /* ]]; then
      THOUGHT_ABS="$THOUGHT_FILE"
    else
      TOPIC_STATE="$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" read "$SESSION_ID" 2>/dev/null)"
      PROJECT_ROOT_FROM_STATE=$(echo "$TOPIC_STATE" | jq -r '.topic_state.project_root // empty' 2>/dev/null)
      THOUGHT_ABS=""
      for CANDIDATE_ROOT in "$PROJECT_ROOT_FROM_STATE" "$PROJECTS_ROOT"; do
        [ -z "$CANDIDATE_ROOT" ] && continue
        if [ -f "${CANDIDATE_ROOT}/${THOUGHT_FILE}" ]; then
          THOUGHT_ABS="${CANDIDATE_ROOT}/${THOUGHT_FILE}"
          break
        fi
        [ -z "$THOUGHT_ABS_FIRST" ] && THOUGHT_ABS_FIRST="${CANDIDATE_ROOT}/${THOUGHT_FILE}"
      done
      [ -z "$THOUGHT_ABS" ] && THOUGHT_ABS="${THOUGHT_ABS_FIRST:-${PROJECTS_ROOT}/${THOUGHT_FILE}}"
    fi

    # Run the validator live on the spine. One positional; stdout+stderr captured so
    # a refusal carries the validator's own findings (which check failed, on which
    # file). Fail-closed on every non-zero exit; an exit ≥2 is a validator error, not
    # a malformed spine, and the refusal says so.
    VALIDATOR_OUT=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/_validate-thought-file.py" "$THOUGHT_ABS" 2>&1)
    VALIDATOR_EXIT=$?
    if [ "$VALIDATOR_EXIT" -eq 1 ]; then
      echo "✗ Thought spine failed structural validation: ${THOUGHT_ABS}" >&2
      echo "  Plan references thought_file: ${THOUGHT_FILE}" >&2
      echo "$VALIDATOR_OUT" >&2
      echo "  Fix the failing checks in that Thought file, then request approval again." >&2
      exit 2
    elif [ "$VALIDATOR_EXIT" -ne 0 ]; then
      echo "✗ Thought spine validator error (exit ${VALIDATOR_EXIT}) — not a verdict on the spine: ${THOUGHT_ABS}" >&2
      echo "  Plan references thought_file: ${THOUGHT_FILE}" >&2
      echo "$VALIDATOR_OUT" >&2
      echo "  The validator itself could not run; fix the validator error before requesting approval again." >&2
      exit 2
    fi
  fi

  # Pre-plan check 6: plan validation gate (CONVERGED marker + state cross-check)
  VALIDATED_EXIT=3
  python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" is-plan-validated "$SESSION_ID" "$PLAN_FILE" \
    > /dev/null 2>&1
  VALIDATED_EXIT=$?

  if [ "$VALIDATED_EXIT" -eq 3 ]; then
    # No active topic — vanilla mode, fall through (allow)
    :
  elif [ "$VALIDATED_EXIT" -eq 0 ]; then
    # Marker present AND state file shows PASS — allow
    :
  elif [ "$VALIDATED_EXIT" -eq 1 ]; then
    # Marker present but state file shows DIRTY/FAIL — anti-tampering deny
    echo "✗ Plan validation state mismatch: <!-- VALIDATION:CONVERGED --> marker present" >&2
    echo "  but state file does NOT show verdict: PASS for this plan." >&2
    echo "  The marker may have been typed without running validation rounds." >&2
    echo "  Re-run validation: python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-plan $SESSION_ID $PLAN_FILE" >&2
    exit 2
  elif [ "$VALIDATED_EXIT" -eq 2 ]; then
    # Marker missing — check for user-typed waiver
    # Resolve MESSAGE_PATH: look for the session's prompt log in project .claude/logs/
    CWD=$(echo "$INPUT" | jq -r '.cwd // empty')
    MESSAGE_PATH=""
    if [ -n "$CWD" ] && [ -d "$CWD/.claude/logs" ]; then
      # Find most recent prompt log for this session (format: prompts-YYYYMMDD-SESSION_ID.log)
      PROMPT_LOG=$(ls "$CWD/.claude/logs/prompts-"*"-$SESSION_ID.log" 2>/dev/null | sort | tail -1)
      if [ -n "$PROMPT_LOG" ] && [ -f "$PROMPT_LOG" ]; then
        # Extract last line (most recent user message) to a temp file
        MESSAGE_PATH="/tmp/last-user-msg-${SESSION_ID}.txt"
        tail -1 "$PROMPT_LOG" | sed 's/^[0-9]*:[0-9]*:[0-9]*|//' > "$MESSAGE_PATH"
      fi
    fi

    if [ -z "$MESSAGE_PATH" ] || [ ! -f "$MESSAGE_PATH" ]; then
      # Could not resolve message — deny with hint (degrade gracefully)
      echo "✗ Plan not validated: <!-- VALIDATION:CONVERGED --> marker is missing." >&2
      echo "  To skip validation, type exactly: <!-- VALIDATION:WAIVED:reason --> in your message." >&2
      echo "  To validate: python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-plan $SESSION_ID $PLAN_FILE" >&2
      exit 2
    fi

    python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py" is-validation-waived \
      "$SESSION_ID" "$PLAN_FILE" "$MESSAGE_PATH" > /dev/null 2>&1
    WAIVED_EXIT=$?

    if [ "$WAIVED_EXIT" -eq 0 ]; then
      # User typed the waiver marker — honor it
      :
    else
      # No valid waiver — deny
      echo "✗ Plan not validated: <!-- VALIDATION:CONVERGED --> marker is missing." >&2
      echo "  To skip validation, type exactly: <!-- VALIDATION:WAIVED:reason --> in your message" >&2
      echo "  (literal substring required — AI typing it without user input is rejected)." >&2
      echo "  To validate: python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-plan $SESSION_ID $PLAN_FILE" >&2
      exit 2
    fi
  fi

  # All checks passed — mark plan as approved for the readonly hook
  if ! grep -q '<!-- APPROVED -->' "$PLAN_FILE"; then
    echo "" >> "$PLAN_FILE"
    echo "<!-- APPROVED -->" >> "$PLAN_FILE"
  fi
fi

exit "$GATE_RESULT"

#!/usr/bin/env bash
# PreToolUse hook on EnterPlanMode — Slice I (S-I-Impl-2 / G7 + G8).
#
# Surfaces the Track-properly vs Ninja-Plan choice when plan mode is entered
# without a session-scoped `plan-mode-init` marker, or when the marker says
# no `_thought` is bound and mode != C. Fires regardless of trigger source:
# `/plan` command OR the plan-mode hotkey both go through `EnterPlanMode`.
#
# The `plan/SKILL.md` orchestrator writes the marker BEFORE invoking plan
# mode (via `pre_plan_gates.py plan-mode-init`). This hook reads it.
#
# Exit codes:
#   0 = allow (marker present + mode set + thought bound, or mode=C)
#   2 = block (no marker, or Modes A/B without thought)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "EnterPlanMode" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

MARKER="$HOME/.claude/state/plan_mode_init/${SESSION_ID}.json"

if [ ! -f "$MARKER" ]; then
  cat >&2 <<EOF
BLOCKED: Plan-mode entry without a \`plan-mode-init\` marker.

Plan mode requires the \`/plan\` orchestrator to surface the Track-properly
vs Ninja-Plan choice before entering. Two options:

  (a) Track properly (recommended) — type \`/plan\` to launch the
      orchestrator. It will check for a bound \`_thought\` file and route
      you through Mode A (full upstream), Mode B (Phase 2 absent → invoke
      \`/solution-design\` first), or Mode C (Ninja-Plan, no spine).

  (b) Ninja-Plan inline — set mode=C explicitly:
        python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py plan-mode-init \\
            $SESSION_ID --mode C
      Then re-enter plan mode.
EOF
  exit 2
fi

MODE=$(jq -r '.mode // empty' "$MARKER" 2>/dev/null)
THOUGHT_PATH=$(jq -r '.thought_path // empty' "$MARKER" 2>/dev/null)

if [ "$MODE" = "C" ]; then
  exit 0
fi

if [ -z "$THOUGHT_PATH" ] || [ "$THOUGHT_PATH" = "null" ]; then
  cat >&2 <<EOF
BLOCKED: Plan-mode entry in Mode ${MODE:-?} without a bound \`_thought\` file.

Modes A and B require an upstream \`_THOUGHT.md\` to consume. Either:

  (a) Bind a thought file:
        python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py plan-mode-init \\
            $SESSION_ID --mode ${MODE:-A} --thought-path /path/to/_THOUGHT.md

  (b) Switch to Ninja-Plan (Mode C):
        python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py plan-mode-init \\
            $SESSION_ID --mode C
EOF
  exit 2
fi

exit 0

#!/bin/bash
# PreToolUse hook: blocks Edit/Write during active plan mode
#
# Allows writes to any .md file in ~/.claude/plans/ (session-scoped
# plan files). Claude Code assigns each session a unique plan filename;
# the system message tells Claude which file to use. This hook ensures
# nothing OTHER than that designated plan file is written during planning.
#
# IMPORTANT (2026-07-06): the harness does NOT reliably block Edit/Write in plan
# mode. A *successful* Write to a file other than the designated plan file causes
# the harness to silently leave plan mode (no ExitPlanMode, no approval). This hook
# is therefore the real guard: it must permit ONLY ~/.claude/plans/*.md so a stray
# project-side plan write cannot trigger that silent exit.
#
# Safety layers:
#   1. This hook blocks writes to anything outside ~/.claude/plans/ (primary guard)
#   2. System message directs Claude to the correct plan file (per session)
#   3. check-uninitiated-plan-exit.sh halts+surfaces if a silent exit happens anyway
#
# Exit codes:
#   0 = allow (not in plan mode, or target is a plan file)
#   2 = block (plan mode active, target is not a plan file)

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only applies to Edit and Write
if [ "$TOOL_NAME" != "Edit" ] && [ "$TOOL_NAME" != "Write" ]; then
  exit 0
fi

# Check if THIS session is in plan mode (session-scoped, not file-scoped)
PERM_MODE=$(echo "$INPUT" | jq -r '.permission_mode // empty')
if [ "$PERM_MODE" != "plan" ]; then
  exit 0
fi

# Session is in plan mode — the ONLY writable surface is the harness-designated
# plan file at ~/.claude/plans/<slug>.md (named in the plan-mode system message).
#
# A project-side <project_root>/Thoughts/<slug>_PLAN.md is deliberately NOT allowed
# during plan mode: a *successful* Write to any file other than the harness-designated
# plan file makes the harness silently transition out of plan mode into acceptEdits
# WITHOUT an ExitPlanMode call — so the approval gate + all plan-gate validation are
# bypassed (reproduced live 2026-07-06 via A/B test; see the plan
# fancy-sauteeing-widget_PLAN.md, action A2). The durable project-side copy is produced
# AFTER approval by the relocation CLI, which runs OUTSIDE plan mode.
PLANS_DIR="$HOME/.claude/plans"
TARGET_FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

# Allow ONLY the harness-designated plan file.
case "$TARGET_FILE" in
  "$PLANS_DIR/"*.md) exit 0 ;;
esac

# Block — anything other than the harness-designated plan file.
echo "BLOCKED: Plan mode is active. During plan mode, Edit/Write is allowed ONLY on the" >&2
echo "harness-designated plan file in $PLANS_DIR (the path named in the plan-mode system message)." >&2
echo "Do NOT write the plan to a project-side Thoughts/..._PLAN.md during plan mode: a successful" >&2
echo "write there silently exits plan mode WITHOUT your approval. The project-side copy is created" >&2
echo "after approval by the relocation step (which runs outside plan mode)." >&2
echo "Target file: $TARGET_FILE" >&2
echo "Call ExitPlanMode to get the plan approved before implementing." >&2
exit 2

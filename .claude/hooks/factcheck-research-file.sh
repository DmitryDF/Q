#!/bin/bash
# PostToolUse hook — triggers factcheck-research on Write/Edit to _RESEARCH.md
# (and your-project Assessments/watchlist_assessment_*.md).
# Mirrors factcheck-thought-file.sh dispatcher pattern.
# PostToolUse CANNOT block — exit 0 always. Reader gate at Stop (check-research-gate.sh).
# Canon: ~/.claude/rules/factcheck-convergence.md

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Path-glob filter — four watched patterns
case "$FILE_PATH" in
  */Thoughts/*_RESEARCH*.md) ;;
  */Personal/your-project/Docs/*_RESEARCH.md) ;;
  */Personal/your-project/Assessments/watchlist_assessment_*.md) ;;
  */CV/[AuthorName]/APPLICATION_*/*.md)
    [[ "$FILE_PATH" == *_RESEARCH_* ]] && exit 0
    [[ "$FILE_PATH" == *_BMC_* ]] && exit 0
    [[ "$FILE_PATH" == *_THOUGHT.md ]] && exit 0
    ;;
  *) exit 0 ;;
esac

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

# A1 — record the OBLIGATION before the engine is entered.
#
# The engine resolves (proj, topic) from _active.json and raises before it builds
# topic_dir, so every refusal path precedes _touch_dispatch_sentinel and leaves
# nothing behind. This dispatcher is the one component in the chain that cannot
# fail for that reason: it always runs when a check is dispatched, and it already
# knows the artifact path without consulting _active.json. So the record of "a
# check was owed" is written HERE, where it cannot be skipped by the component
# that may decline to act.
#
# Deliberately NOT best-effort, following the discipline _touch_dispatch_sentinel
# states for itself (_factcheck_engine.py:4493-4501): a missing signal reads as a
# pass, so the append's failure is NOT swallowed with `2>/dev/null || true`. That
# swallow would recreate the diagnosed fault one layer out.
#
# `printf` with separate %s conversions, never a bare interpolated `echo`: a path
# containing a quote, a backslash or a newline must not be able to split one
# record into two, or forge a second one.
#
# PostToolUse cannot block, so the hook's exit status is untouched (exit 0 below,
# unconditionally). "Loud" here means stderr plus a non-silent state, not a
# refusal — see the plan's Design Review edge case.
OBLIGATION_DIR="$HOME/.claude/state/research_obligations"
mkdir -p "$OBLIGATION_DIR"
printf '%s\t%s\t%s\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  "$SESSION_ID" \
  "$FILE_PATH" \
  >> "$OBLIGATION_DIR/${SESSION_ID}.ledger"

# Background dispatch — engine handles 30s debounce and flock concurrency.
# --auto marks this as the automatic on-save dispatch (S2/A15, Bug 9): only the
# auto path keeps the 30s cooldown; a manual re-run (no --auto) uses debounce=0
# so it is never silently suppressed by this background run's cooldown.
#
# A1/F5: `>>`, not `>`. With truncation, two dispatches in one session wiped the
# first one's traceback before anybody could read it — so the only surviving
# record of a refusal was destroyed by the next save.
nohup python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" factcheck-research \
  "$SESSION_ID" "$FILE_PATH" --auto \
  >> "/tmp/factcheck-research-${SESSION_ID}.log" 2>&1 &

exit 0

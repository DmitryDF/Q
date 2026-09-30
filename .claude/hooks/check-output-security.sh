#!/bin/bash
# PreToolUse hook — synchronous output-security inspection of a produced-claim write.
#
# The one moment code sits between a producer and the persisted artifact: the claim text is
# present in full with its provenance markers intact, and the claim can still be prevented
# from existing. After the write there is only quarantine; before it there is no artifact.
#
# THIN BY DESIGN. This wrapper does three things and nothing else: filter by tool, filter by
# path, and hand the payload to the Python half. Every judgment — the attribution partition,
# the single bounded judge dispatch, the disposition rule, the operator wording — lives in
# `output_security_judge.py` and `output_security.py`, where it is unit-testable without a
# shell. The path glob is cloned from `factcheck-research-file.sh:19-28`.
#
# EXIT 2 ONLY ON BLOCK. A report-only finding, a clean write, and an inspection that could
# not run all exit 0. Detection here is a layer on top of containment rather than the thing
# holding the boundary, so its failure may cost accuracy but must never cost availability —
# a hook that hangs or hard-fails would degrade every research write in every session.
#
# The Layer-2 rules mirror for this boundary describes it and gates nothing; THIS is the
# gate. The mirror is deliberately not named here by filename: a shipped S1 assertion proves
# the mirror is inert by checking that no .py/.sh/.json in the tree mentions it, and naming
# it in a comment would make that proof read as broken when it is not.
# Slice S3 of Thoughts/research-output-security-20260804213834_S3_PLAN.md (action A4).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Path-glob filter — the produced-claim artifacts this boundary watches.
case "$FILE_PATH" in
  *_RESEARCH*.md) ;;
  *_CLAIMS*.md) ;;
  *) exit 0 ;;
esac

# Resolved from THIS script's own directory rather than the tree's usual `$HOME/.claude`
# convention. Deliberate: it makes the hook run against whichever config tree contains it,
# so the `claude-experiment` clone this slice was built in exercises the clone's engine
# rather than reaching back into live — the same tree-relative property the two test modules
# already rely on.
HOOK_DIR="$(dirname "$0")"

# The Python half owns the decision and its own failure modes: it never raises, and it
# returns 2 only on BLOCK. `|| true` is deliberately NOT used — the exit code is the
# decision, and swallowing it would silently disable the gate.
printf '%s' "$INPUT" | python3 "$HOOK_DIR/output_security_judge.py" gate
exit $?

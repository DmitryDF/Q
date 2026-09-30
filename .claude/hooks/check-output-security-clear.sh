#!/bin/bash
# PostToolUse hook — commit the clearing record a landed write earned.
#
# THE SEAM ASYMMETRY, AND WHY THERE ARE TWO WRAPPERS INSTEAD OF ONE.
# The PreToolUse wrapper writes a record that CARRIES findings, because over-reporting a
# finding for a write that was then refused is the safe direction — a later clean inspection
# clears it. A record that CLEARS findings cannot be written there: a file carrying a live
# flag, then a clean write that a sibling hook refuses, would have its flag erased while the
# payload sat untouched on disk. That is a fail-open, and it is the defect three earlier
# drafts of this slice carried.
#
# So the clean result is STAGED before the write and committed here, after it. This wrapper
# runs only when the write actually landed, which is the one fact the pre-write seam cannot
# know.
#
# NO MODEL CALL HAPPENS HERE. The verdict was computed once, before the write; this commits
# the already-computed result. PostToolUse cannot block, and this exits 0 unconditionally.
#
# THIN BY DESIGN, like its sibling: filter by tool, filter by path, hand the payload to the
# Python half. The path glob is identical to check-output-security.sh's — deliberately, so
# the two seams watch exactly the same set of files. A file one seam watches and the other
# does not would be a file whose clean writes never clear its flags.
#
# Slice S4 of Thoughts/research-output-security-20260804213834_S4_PLAN.md (action A1).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

case "$FILE_PATH" in
  *_RESEARCH*.md) ;;
  *_CLAIMS*.md) ;;
  *) exit 0 ;;
esac

# Tree-relative, for the same reason the sibling wrapper is: it makes the hook run against
# whichever config tree contains it, so a claude-experiment clone exercises the clone.
HOOK_DIR="$(dirname "$0")"

printf '%s' "$INPUT" | python3 "$HOOK_DIR/output_security_record.py" commit-staged

# ── The decorrelated meta-check — DETACHED, not merely late. ──────────────────
#
# "Later" is not enough. This hook is itself inside the harness hook budget, and a
# three-round convergence loop with a model call per round cannot fit inside it. So the loop
# is dispatched with nohup and this hook returns immediately — the same pattern
# factcheck-research-file.sh already uses to dispatch this very engine.
#
# It is dispatched from HERE and never from the PreToolUse seam. Design decision A24 holds
# the synchronous path to at most one judge call, so a refused save returns after one
# judgement's wait rather than two. The write seam does not even import the meta-check
# module, which is what makes that conformance structural rather than remembered.
#
# The run is a no-op when the file carries no ungraded flag, so an ordinary clean write costs
# a process that exits immediately and no model call at all.
nohup python3 "$HOOK_DIR/output_security_metacheck.py" run "$FILE_PATH" \
  > /dev/null 2>&1 &

exit 0

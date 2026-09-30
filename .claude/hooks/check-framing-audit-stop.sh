#!/bin/bash
# Stop hook — report UNTRACKED unframed [Thought] TODO lines the session is ending with.
#
# streamed-dancing-goose S5 (A5). C7 is a guarantee about what the operator finds
# out; routing it through the close skill's prose would lose it whenever a session
# ends another way. So the report is delivered by THIS registered hook — modelled on
# `check-output-security-stop.sh`: it reports, it never blocks, and it exits 0
# unconditionally, including on its own failure.
#
# WHAT IT REPORTS. `todo.py audit-coverage --surface` renders the ONE shared block
# for `[Thought]` lines that are unframed AND carry no `[auto-registered]` marker —
# the lines nothing else tracks (a hand-typed line, the /clarification-v2 door's
# line). Marked lines are the obligation surface's job (`framing_obligation`,
# already rendered at Stop by `check-work-done-omission.sh` and at SessionStart);
# reporting them here too would list every legacy unframed line at every close.
#
# WHERE THE REPORT LANDS, stated because the tree already priced it
# (work_done_report.py:11-28): stderr from a Stop hook on exit 0 is NOT
# established to reach the operator. The same block is therefore ALSO rendered
# by the SessionStart TODO scan (`todo.py`), the channel that demonstrably does.
# This hook is the "by any route" half; the scan is the "the operator finds out"
# half. Neither blocks.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat 2>/dev/null)
CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

HOOK_DIR="$(dirname "$0")"
# The audit exits 1 whenever any untracked line fails — that is a REPORT here,
# never a reason to stop the session: swallow the exit code, keep the block.
python3 "$HOOK_DIR/todo.py" audit-coverage --cwd "$CWD" --surface >/dev/null || true
exit 0

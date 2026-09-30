#!/usr/bin/env bash
# PostToolUse hook on AskUserQuestion — resume-gate ack recorder
# (execplan-resume-gate-fix A1, 2026-07-10). After the operator answers the
# orientation question the A1 gate surfaced, record the run as acknowledged so the
# next /execute-plan invocation in this session is not re-blocked. Deterministic
# logic lives in run.py (execplan-ack); this hook is a thin adapter. No-op when
# there is no pending marker for this session. Honors EXECPLAN_ACK_STATE_DIR.
#
# Exit codes: 0 always.
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "AskUserQuestion" ] && exit 0

SID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SID" ] && exit 0

RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
jq -nc --arg sid "$SID" '{session_id:$sid}' \
  | python3 "$RUN_PY" execplan-ack >/dev/null 2>&1 || true
exit 0

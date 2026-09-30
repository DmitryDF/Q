#!/bin/bash
# Stop hook — report produced-claim flags the session is ending with.
#
# AN ADDITIONAL READER, ANSWERED INDEPENDENTLY. The Stop array already holds the factual
# research gate, whose `resolved` status folds in a BYPASSED verdict with a stated reason.
# That is a sanction on the FACTUAL question — is this research checked — and it has no
# bearing on the security question. This reader has its own marker and its own answer, so a
# factual bypass can never suppress it. Two questions, two answers.
#
# IT REPORTS; IT NEVER BLOCKS. Exit 0 unconditionally, including on its own failure. What it
# can surface is by construction content the gate ALLOWED rather than refused — a report-only
# finding is one the disposition rule deliberately let through, and the harvest quarantine is
# what holds it back from promotion. Blocking here would contradict the decision the
# disposition already made, and would stop work over a case the design chose not to stop.
#
# IT COMPARES NO HASH. It reads whatever each file's latest inspection left. That is what
# keeps the engine's own background rewrites of these files — which land outside the Write
# tool and trigger no inspection — from silently retiring a live flag.
#
# Slice S4 of Thoughts/research-output-security-20260804213834_S4_PLAN.md (action A3).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

# Drain stdin so the harness never blocks on an unread pipe. The payload is not needed: the
# report is over the boundary's own records, not over anything this event carries.
cat > /dev/null 2>&1

HOOK_DIR="$(dirname "$0")"

REPORT=$(python3 "$HOOK_DIR/output_security_record.py" stop-report 2>/dev/null)

if [ -n "$REPORT" ]; then
  printf '%s\n' "$REPORT" >&2
fi

exit 0

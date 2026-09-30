#!/usr/bin/env bash
# SessionStart hook — execplan resume-gate garbage collection
# (execplan-resume-gate-fix A5, 2026-07-10). Clone of
# cleanup-stale-verifier-isolation.sh. Two-part reap, both age/completion-bounded
# so a live, in-TTL, incomplete run is never touched:
#   (1) mechanical (A5): delete ack/pending markers + retired `*.marker` files
#       (the pre-fix per-session pending markers) older than the TTL.
#   (2) completion-aware (A3): run.py execplan-reap removes run pointers that are
#       provably complete OR older than the TTL.
# Honors EXECPLAN_ACK_STATE_DIR.
#
# Exit codes: 0 always.
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

STATE_DIR="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state/execplan_session_ack}"
[ -d "$STATE_DIR" ] || exit 0

# 24h default (1440 min) — editorial, scales the 6h verifier-isolation precedent.
TTL_MIN="${EXECPLAN_ACK_TTL_MINUTES:-1440}"
# `entry-suppress-*` joins the reap (execplan-gate-blast-radius A6, gap G7). It was
# the one marker family the GC never touched, so path exemptions accumulated
# permanently — 132 of them across 32 sessions by the time this was diagnosed, the
# walking session's own file holding 60-odd paths. They now age out on the same TTL
# as the rest, so the residue stops growing without bound. Ageing a suppression out
# only ever RE-ARMS the gate for that path, so an over-eager reap costs one extra
# prompt rather than a missed block.
find "$STATE_DIR" -maxdepth 1 -type f \
  \( -name 'pending-*.json' -o -name 'acked-*.json' -o -name '*.marker' \
     -o -name 'entry-suppress-*.json' \) \
  -mmin +"$TTL_MIN" -delete 2>/dev/null

RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
printf '{}' | python3 "$RUN_PY" execplan-reap >/dev/null 2>&1 || true
exit 0

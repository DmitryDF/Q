#!/usr/bin/env bash
# SessionStart hook -- WARN (never silently pass) when a multi-step plan bound to
# the active topic has been taken into implementation OFF the /plan Step-11 arming
# path (hand-authored, resumed fresh, or backfilled), so it was never armed for
# /execute-plan (execplan-multislice A3, 2026-08-02).
#
# Arming -- the `execution_pending` run pointer the /execute-plan entry gate reads
# -- only ever happens at /plan Step 11. A multi-slice plan that reaches
# implementation any OTHER way is never armed, so it runs unsafely AND SILENTLY
# (no gate, no warning). This hook re-detects "looks multi-step" from each bound
# plan's own register (all logic lives in the deterministic `scan-unarmed` verb in
# skills/execute-plan/run.py -- this hook is a THIN ADAPTER), and prints an
# advisory warning for any unarmed-but-multistep or malformed-multistep plan so an
# operator sees it before driving the plan by hand.
#
# Hook hygiene (mirrors execplan-scan-pending.sh / check-execplan-entry-gate.sh):
#   - fail-OPEN / silent on any infra error -- SessionStart output is advisory only.
#   - NO `set -e` (must stay fail-OPEN).
#   - fully NON-INTERACTIVE.
#   - Honors EXECPLAN_ACK_STATE_DIR indirectly (via run.py's _execplan_ack_dir).
#
# Exit codes: 0 ALWAYS (SessionStart hooks do not gate; stdout is advisory context).

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && exit 0

# Prepend the standard system bin dirs so an IDE-stripped-PATH shell still finds
# coreutils (jq/wc/tail/mktemp/mv). Do NOT hardcode per-OS absolute tool paths.
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:${PATH}"

INPUT=$(cat)

command -v jq >/dev/null 2>&1 || exit 0
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)

# Resolve run.py by traversing from THIS hook's own dir into the sibling skill
# (mirrors check-execplan-entry-gate.sh).
RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
[ -f "$RUN_PY" ] || exit 0

# Trace log -- create the dir first (guarded), then size-bound BEFORE appending.
LOG_DIR="$HOME/.claude/logs"
TRACE_LOG="$LOG_DIR/execplan-scan.log"
mkdir -p "$LOG_DIR" 2>/dev/null || true
if [ -f "$TRACE_LOG" ]; then
  SZ=$(wc -c < "$TRACE_LOG" 2>/dev/null || echo 0)
  # `wc -c` is used (NOT stat, whose flags differ across BSD/GNU).
  if [ "${SZ:-0}" -gt 262144 ]; then
    # Full-path mktemp template in the SAME dir (no -t flag, for BSD/GNU parity);
    # atomic same-dir rename -- NEVER a bare `>` truncation of live state.
    TMP=$(mktemp "$LOG_DIR/execplan-scan.log.XXXXXX" 2>/dev/null) || true
    if [ -n "${TMP:-}" ]; then
      tail -n 500 "$TRACE_LOG" > "$TMP" 2>/dev/null || true
      mv "$TMP" "$TRACE_LOG" 2>/dev/null || true
    fi
  fi
fi

# Resolve python3: PATH first, else a bounded fixed list of common locations.
PY=""
if command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
else
  for cand in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3 \
    "$HOME/.pyenv/shims/python3" \
    "$HOME/.asdf/shims/python3"; do
    if [ -x "$cand" ]; then
      PY="$cand"
      break
    fi
  done
fi
if [ -z "$PY" ]; then
  printf '%s no python3 found on PATH or common locations -- scan skipped\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)" >> "$TRACE_LOG" 2>/dev/null || true
  exit 0
fi

# Invoke the verb. Build the payload with jq (safe quoting). stderr -> trace log;
# stdout captured for parsing. Fail-open on any error.
PAYLOAD=$(jq -nc --arg sid "$SESSION_ID" '{session_id:$sid}' 2>/dev/null)
[ -z "${PAYLOAD:-}" ] && exit 0

OUT=$(printf '%s' "$PAYLOAD" | "$PY" "$RUN_PY" scan-unarmed 2>>"$TRACE_LOG")

COUNT=$(printf '%s' "$OUT" | jq -r '.count // 0' 2>/dev/null)
# A jq parse failure yields empty -> treated as 0 -> nothing printed.
if [ -n "${COUNT:-}" ] && [ "$COUNT" -gt 0 ] 2>/dev/null; then
  echo "execplan-scan-unarmed: multi-step plan(s) not armed for /execute-plan --"
  printf '%s' "$OUT" | jq -r '.warnings[]?.message' 2>/dev/null \
    | while IFS= read -r msg; do
        [ -n "$msg" ] && echo "  - $msg"
      done
fi

exit 0

#!/usr/bin/env bash
# PreToolUse hook: /execute-plan ENTRY-boundary gate
# (execplan-contract-hardening Slice S3, 2026-07-20).
#
# Code-enforces design v3.1 §1b/§1c (Thoughts/execplan-contract-hardening-*_PLAN.md
# Coherent Action A3, Gaps G1+G4): while a multi-slice plan's per-run pointer has
# `execution_pending:true`, a hand-rolled write/spawn from a session that is NOT the
# recorded `walking_session_id` is BLOCKED, so "the orchestrator actually ran" stops
# being AI-narratable (code_first_architecture.md — "Defend boundaries with code,
# not goodwill"). This hook is a THIN ADAPTER — all decision logic (the pending +
# not-walker + inside-worktree + not-excluded + not-suppressed predicate) lives in
# the deterministic `execplan_entry_check()` domain function in
# skills/execute-plan/run.py (S1); the hook only resolves the PreToolUse envelope
# into that verb's {writing_session_id, worktree_root, target_path} input.
#
# Matcher: Edit|Write|Bash|Agent.
#   - Edit/Write  -> target_path = tool_input.file_path
#   - Bash        -> the command's write DESTINATIONS are resolved by the shared
#                    `extract-bash-targets` verb (run.py) — BOTH `>`/`>>` redirects
#                    AND the non-redirect write utilities cp / mv / install / sed -i /
#                    tee / dd of= (execplan-nonredirect-write-guard, 2026-07-20). Every
#                    target is handed to the widened `execplan_entry_check` in ONE
#                    invocation (list signature — no per-target Python re-invoke, so the
#                    cost is O(1) in the number of targets). Read-only Bash (ls/cat/grep,
#                    a `sed` without -i, a `dd` with no of=) yields no target and is
#                    NEVER gated; fd-redirects (`2>`/`&>`) and `git commit` messages are
#                    excluded by the verb.
#   - Agent       -> no file target exists for a spawn; probed with a worktree-scoped
#                    sentinel path so the walker-exemption predicate
#                    (writing_session_id == walking_session_id) still decides
#                    correctly — the walker's OWN spawns are exempt exactly like its
#                    writes; a non-walker's spawn is blocked the same way.
#
# On block: fails CLOSED (exit 2) and prints a plan-named remediation with three
# options — (a) run /execute-plan now, (b) path-scoped suppression for this target
# only (resume-ack untouched), (c) abandon (clears execution_pending).
#
# Hook hygiene (mandatory, non-negotiable):
#   - fail-OPEN on any infra error (missing python3/run.py, unexpected verb
#     failure/non-JSON output) -> exit 0. Only a genuine verb `block:true` exits 2.
#   - fully NON-INTERACTIVE — never reads from or prompts a TTY.
#   - `BYPASS_EXECPLAN=1` disables this gate entirely, but the bypass is LOGGED
#     (never silent) — mirrors the existing EXECPLAN_ACK_STATE_DIR /
#     PUSH_TARGET_CHECK_SKIP escape hatches.
#   - Honors EXECPLAN_ACK_STATE_DIR for test isolation (passed through to run.py
#     via the environment; also used for the bypass log location).
#
# Exit codes: 0 = allow; 2 = block.

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)

STATE_ROOT="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state}"
BYPASS_LOG="$STATE_ROOT/execplan/bypass.log"

# BYPASS_EXECPLAN=1 — operator-recovery override. Always logged, never silent.
if [ "${BYPASS_EXECPLAN:-}" = "1" ]; then
  mkdir -p "$(dirname "$BYPASS_LOG")" 2>/dev/null
  printf '%s session=%s tool=%s BYPASS_EXECPLAN=1 -- entry gate disabled for this call\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SESSION_ID:-unknown}" "${TOOL_NAME:-unknown}" \
    >> "$BYPASS_LOG" 2>/dev/null
  echo "check-execplan-entry-gate: BYPASS_EXECPLAN=1 -- entry gate disabled for this call (logged: $BYPASS_LOG)" >&2
  exit 0
fi

case "$TOOL_NAME" in
  Edit|Write|Bash|Agent) : ;;
  *) exit 0 ;;
esac

# No session id surfaced -> cannot scope a run to a walker; default-open (mirrors
# check-execplan-session-ack.sh / research-scope-gate.sh).
[ -z "$SESSION_ID" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
WORKTREE=""
if [ -n "$CWD" ]; then
  WORKTREE=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
fi
# Not in a git worktree -> nothing for this gate to scope to; default-open.
[ -z "$WORKTREE" ] && exit 0

RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
[ -f "$RUN_PY" ] || exit 0                          # fail-open: infra error
command -v python3 >/dev/null 2>&1 || exit 0        # fail-open: infra error

# Collect the write DESTINATION(s) for this tool call. Edit/Write/Agent yield one
# target each; Bash delegates to the shared `extract-bash-targets` verb, which emits
# every write destination NUL-terminated (a Bash var/arg cannot hold a NUL byte, so
# the list is ingested via a `read -d ''` process-substitution loop — NEVER `$()`,
# which would strip the NULs and corrupt paths with spaces). Fail-open + guarded
# expansions throughout (no `set -e` — the gate stays fail-OPEN on infra error).
declare -a TARGETS=()
case "$TOOL_NAME" in
  Edit|Write)
    TP=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
    [ -n "${TP:-}" ] && TARGETS+=("$TP")
    ;;
  Agent)
    # A6 / gap G8: an Agent spawn has no file target, so this gate used to probe
    # EVERY spawn with a synthesized in-worktree sentinel path. That manufactured a
    # gateable target for operations that were never writes to a real file — a
    # read-only checker subagent (grant: Read, Grep, Glob; no shell) cannot write at
    # all, yet was blocked, and clearing it cost a PERMANENT path exemption. That is
    # a share of the accumulated residue, and it made independent verification the
    # most expensive thing a walking session could do.
    #
    # Mirror the walk gate's shipped idiom instead of inventing one: a spawn with no
    # `[SLICE:Sn]` tag is not a slice-implementation spawn, so it is not this gate's
    # concern and yields no target. A TAGGED spawn still probes exactly as before,
    # so the walker-exemption predicate still decides correctly for the spawns that
    # genuinely stand in for slice work.
    PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null)
    if printf '%s' "$PROMPT" | grep -qE '\[SLICE:[[:space:]]*[Ss][0-9]+[[:space:]]*\]'; then
      TARGETS+=("$WORKTREE/.execplan-entry-gate-agent-probe")
    fi
    ;;
  Bash)
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
    if [ -n "${CMD:-}" ]; then
      while IFS= read -r -d '' t; do
        [ -n "$t" ] && TARGETS+=("$t")
      done < <(jq -nc --arg c "$CMD" --arg cwd "$CWD" '{command:$c, cwd:$cwd}' \
                 | python3 "$RUN_PY" extract-bash-targets 2>/dev/null)
    fi
    ;;
esac
# No write destination (read-only Bash, empty Edit/Write path) -> nothing to gate.
[ "${#TARGETS[@]}" -eq 0 ] && exit 0

# Hand the FULL target list to the widened execplan_entry_check in a SINGLE call
# (backward-compatible superset: `target_paths` list; the verb blocks on the first
# gateable target). jq --args carries each path as a distinct argv element, so spaces
# and shell metacharacters survive intact. The scalar `target_path` (first target) is
# ALSO sent so an un-upgraded run.py during a partial the deploy step still checks at
# least the first target instead of fail-opening on the unknown `target_paths` key.
TARGETS_JSON=$(jq -nc '$ARGS.positional' --args "${TARGETS[@]}" 2>/dev/null)
[ -z "${TARGETS_JSON:-}" ] && exit 0                 # fail-open: jq error

DECISION=$(jq -nc --arg ws "$SESSION_ID" --arg wt "$WORKTREE" \
  --arg tp0 "${TARGETS[0]}" --argjson tps "$TARGETS_JSON" \
  '{writing_session_id:$ws, worktree_root:$wt, target_path:$tp0, target_paths:$tps}' \
  | python3 "$RUN_PY" execplan-entry-check 2>/dev/null)

BLOCK=$(echo "$DECISION" | jq -r '.block // false' 2>/dev/null)
# Any parse/verb failure (empty DECISION, malformed JSON) also lands here as
# "false" via jq's `// false` default -> fail-open.
[ "$BLOCK" != "true" ] && exit 0

SLUG=$(echo "$DECISION" | jq -r '.slug // "unknown-plan"' 2>/dev/null)
SLICE_ID=$(echo "$DECISION" | jq -r '.slice_id // "-"' 2>/dev/null)
RUN_ID=$(echo "$DECISION" | jq -r '.run_id // empty' 2>/dev/null)
# The verb reports which target it gated (first gateable); fall back to the first
# collected target for display if absent.
BLOCKED_TARGET=$(echo "$DECISION" | jq -r '.blocked_target // empty' 2>/dev/null)
[ -z "${BLOCKED_TARGET:-}" ] && BLOCKED_TARGET="${TARGETS[0]}"

# A3 liveness columns. Displayed so the operator decides on visible fact instead of
# reconstructing liveness from timestamps by hand -- which is how one session
# declared another abandoned while it was alive and blocked at this same gate.
# DISPLAY ONLY: nothing here disarms a run, and no code path may act on it.
PHASE=$(echo "$DECISION" | jq -r '.phase // "in-flight"' 2>/dev/null)
WALKER=$(echo "$DECISION" | jq -r '.walking_session_id // empty' 2>/dev/null)
AGE=$(echo "$DECISION" | jq -r '.last_activity_age_seconds // empty' 2>/dev/null)
SCOPED=$(echo "$DECISION" | jq -r '.scoped_to_write_targets // false' 2>/dev/null)
REASON=$(echo "$DECISION" | jq -r '.reason // empty' 2>/dev/null)

if [ -n "${AGE:-}" ] && [ "$AGE" -ge 0 ] 2>/dev/null; then
  if   [ "$AGE" -lt 60 ]    ; then AGE_TXT="${AGE}s ago"
  elif [ "$AGE" -lt 3600 ]  ; then AGE_TXT="$((AGE / 60))m ago"
  elif [ "$AGE" -lt 86400 ] ; then AGE_TXT="$((AGE / 3600))h ago"
  else                             AGE_TXT="$((AGE / 86400))d ago"
  fi
else
  AGE_TXT="unknown"
fi

if [ "$PHASE" = "armed" ]; then
  cat >&2 <<EOF
check-execplan-entry-gate: BLOCKED -- YOUR OWN approved plan has not been walked yet.

Plan: ${SLUG}  (next slice: ${SLICE_ID}, run_id: ${RUN_ID:-unknown})
Phase: armed -- no session is walking this run. Last activity: ${AGE_TXT}.
Tool '${TOOL_NAME}' writes '${BLOCKED_TARGET}'.

This gate holds ONLY the session that approved the plan, and only until the walk
starts -- so a multi-slice plan is walked through its gates rather than
hand-implemented. Other sessions are not affected by this run.

Pick one:
EOF
else
  # Say WHY this target is held, and say it truthfully. Two different reasons
  # reach this branch and they call for different responses: a declared-target
  # match ("your file is one the walked slice is editing") is a real collision
  # and waiting is usually right; an UNSCOPED block ("the run declares no
  # anchorable targets, so the whole checkout is held") is a fail-closed
  # fallback that may have nothing to do with your file, and (b) is usually
  # right. Printing "declares it will touch" for both told a session its file
  # was claimed when the run had claimed nothing.
  if [ "$SCOPED" = "true" ]; then
    WHY="which the walked slice declares it will touch"
  else
    WHY="held because the run declares NO anchorable write targets, so the
whole checkout is held (a fail-closed fallback -- this file is not named by
the walked slice; option (b) is usually the right answer)"
  fi
  cat >&2 <<EOF
check-execplan-entry-gate: BLOCKED -- an in-flight /execute-plan walk owns this worktree.

Plan: ${SLUG}  (slice: ${SLICE_ID}, run_id: ${RUN_ID:-unknown})
Walker: ${WALKER:-unknown}   Last activity: ${AGE_TXT}
Scoped to that slice's declared write targets: ${SCOPED}
Tool '${TOOL_NAME}' writes '${BLOCKED_TARGET}', ${WHY}
(execplan-contract-hardening Slice S3 -- code_first_architecture.md:
"Defend boundaries with code, not goodwill").

The walker and its last-activity age are shown so you can judge whether to wait or
proceed. They are informational: nothing disarms a run automatically, because a
wrong "dead" verdict destroys in-flight work while a wrong "alive" costs one pointer
the 24h TTL clears anyway.

Pick one:
EOF
fi

cat >&2 <<EOF
  (a) Run /execute-plan now to become the walker and continue the plan through its gates.
  (b) Suppress the entry gate for ONLY this target (resume-ack untouched), then retry:
      echo '{"session_id":"${SESSION_ID}","target_path":"${BLOCKED_TARGET}"}' | \\
        python3 "${RUN_PY}" execplan-entry-suppress
  (c) Abandon this walk (retires the pointer for run ${RUN_ID:-unknown}).
      This is ANOTHER session's run: it may be mid-walk, and retiring it is how
      in-flight work gets silently discarded. Prefer (a) or (b). If you are the
      owning session, prove it -- the clear is refused without either the owning
      owner_session_id or an explicit override:
      echo '{"run_id":"${RUN_ID}","owner_session_id":"${SESSION_ID}"}' | \\
        python3 "${RUN_PY}" clear-active-run
      Not the owner, and you are sure the run is dead? Override explicitly:
      echo '{"run_id":"${RUN_ID}","confirm_non_owner":true}' | \\
        python3 "${RUN_PY}" clear-active-run
      Either way the pointer is MOVED ASIDE (.bak-<epoch>-<pid>), never deleted.
EOF
exit 2

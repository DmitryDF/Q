#!/usr/bin/env bash
# PreToolUse hook: /execute-plan EXECUTION-boundary gate (the checkout invariant)
# (execplan-contract-hardening Slice S4, 2026-07-20).
#
# Code-enforces design v3.1 section 2b (Thoughts/execplan-contract-hardening-*_PLAN.md
# Coherent Action A4, Gap G2): while a session IS the recorded `walking_session_id`
# for an in-flight /execute-plan run, its own worktree code edits, `[SLICE:Sn]`
# implementation spawns, and `Sn:`-prefixed commits are gated by the CHECKOUT
# INVARIANT -- a dep-satisfied slice must actually be checked out (via the atomic
# `checkout-slice` verb) before code may be edited, a slice's own implementation
# spawn is only allowed once ITS deps carry a conformance PASS receipt, and a
# slice's commit is only allowed once ITS code-layer receipt exists. This is what
# stops "the walk is narrated but the two-layer gate never actually ran"
# (code_first_architecture.md -- "Defend boundaries with code, not goodwill").
#
# This hook is a THIN ADAPTER, mirroring check-execplan-entry-gate.sh's shape --
# ALL decision logic (the checkout-invariant / dependency-receipt / commit-receipt
# predicate) lives in the deterministic `walk_gate_check()` domain function in
# skills/execute-plan/run.py (S2). The hook's own job is exactly two things the
# verb's payload contract needs but does not resolve itself:
#   (1) find which in-flight run (if any) THIS session is the walker for, by
#       scanning the same on-disk run-*.json pointers run.py itself owns (read-only
#       -- no pointer field is written here), so `run_id` can be handed to the verb
#       (`walk_gate_check()` requires {run_id|surface_path}, unlike
#       `execplan_entry_check()` which self-scans);
#   (2) pre-filter to the cases the design scopes this gate to -- worktree-inside
#       code paths only, excluding the SAME bookkeeping surfaces the S3 entry gate
#       excludes (a walker must still be able to touch TODO.md / the spine / state
#       while no slice is checked out yet), and the union-fallback for an Agent
#       spawn that carries no `[SLICE:Sn]` tag (a checker/close-* subagent is not a
#       slice-implementation spawn and must not be spuriously blocked).
#
# Matcher: Edit|Write|Bash|Agent.
#   - Edit/Write  -> action=edit|write; TARGET_PATH = tool_input.file_path.
#   - Bash        -> two shapes, checked in order (the `Sn:` commit branch stays
#       AHEAD of the write detector so a commit MESSAGE containing `>` is classified
#       as a commit, never mis-parsed as a redirect write):
#       (a) a `git commit -m "Sn: ..."` (or 'Sn: ...') command whose message
#           STARTS WITH an `Sn:` slice prefix -> action=commit, target_or_tag=the
#           message (the verb's `_parse_commit_slice` extracts Sn from the start).
#       (b) otherwise, the SAME shared `extract-bash-targets` verb the entry gate uses
#           (`>`/`>>` redirects AND cp/mv/install/sed -i/tee/dd of=, excluding `2>`/
#           `&>` fd-redirects) -> each detected target is run through the bash-local
#           bookkeeping-exclusion + worktree-containment filters, and if ANY in-worktree
#           code target survives -> action=bash (one `walk-gate-check` call). Read-only
#           Bash (ls/cat/grep/pipelines, a `sed` without -i, a `dd` with no of=, and a
#           git-commit whose message is NOT `Sn:`-prefixed) is NEVER gated.
#   - Agent       -> a `[SLICE:Sn]` tag (the NEW bracketed tag [C2], parsed with the
#                    SAME parse-with-union-fallback idiom check-impl-models.sh uses
#                    for `[ACTION:An]`/`[MODEL:fam]`) is looked for in the spawn
#                    prompt. Present -> action=agent, target_or_tag=the tag. ABSENT
#                    -> union-fallback: ALLOW immediately (not this gate's concern --
#                    a spawn with no slice tag is not a slice-implementation spawn).
#
# On block: fails CLOSED (exit 2) and prints a remediation naming the reason (no
# slice checked out / out-of-order slice / a dependency or the slice's own code
# receipt is missing) and how to proceed -- check out the ready slice via the
# orchestrator, or the logged BYPASS_EXECPLAN=1 recovery override.
#
# Hook hygiene (mandatory, non-negotiable -- mirrors check-execplan-entry-gate.sh):
#   - fail-OPEN on any infra error (missing python3/run.py, unexpected verb
#     failure/non-JSON output) -> exit 0. Only a genuine verb `block:true` exits 2.
#   - fully NON-INTERACTIVE -- never reads from or prompts a TTY.
#   - `BYPASS_EXECPLAN=1` disables this gate entirely, but the bypass is LOGGED
#     (never silent) -- the SAME bypass log the entry gate writes to.
#   - Honors EXECPLAN_ACK_STATE_DIR for test isolation (the same env var the S1/S2
#     run.py code reads for both the pointer dir and the receipt store root).
#
# Exit codes: 0 = allow; 2 = block.

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)

STATE_ROOT="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state}"
BYPASS_LOG="$STATE_ROOT/execplan/bypass.log"

# BYPASS_EXECPLAN=1 -- operator-recovery override. Always logged, never silent.
if [ "${BYPASS_EXECPLAN:-}" = "1" ]; then
  mkdir -p "$(dirname "$BYPASS_LOG")" 2>/dev/null
  printf '%s session=%s tool=%s BYPASS_EXECPLAN=1 -- walk gate disabled for this call\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${SESSION_ID:-unknown}" "${TOOL_NAME:-unknown}" \
    >> "$BYPASS_LOG" 2>/dev/null
  echo "check-execplan-walk-gate: BYPASS_EXECPLAN=1 -- walk gate disabled for this call (logged: $BYPASS_LOG)" >&2
  exit 0
fi

case "$TOOL_NAME" in
  Edit|Write|Bash|Agent) : ;;
  *) exit 0 ;;
esac

# No session id surfaced -> cannot scope a run to a walker; default-open (mirrors
# check-execplan-entry-gate.sh / check-execplan-session-ack.sh).
[ -z "$SESSION_ID" ] && exit 0

CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
WORKTREE=""
if [ -n "$CWD" ]; then
  WORKTREE=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null || echo "")
fi
# Not in a git worktree -> nothing for this gate to scope to; default-open.
[ -z "$WORKTREE" ] && exit 0

# --- Same bookkeeping-surface exclusion the S3 entry gate applies (a walker must
# still be able to touch these paths while no slice is checked out yet) ---
is_excluded_bookkeeping_path() {
  local p="$1" name namelc
  case "$p" in
    "$HOME"/.claude/plans|"$HOME"/.claude/plans/*) return 0 ;;
    "$HOME"/.claude/state|"$HOME"/.claude/state/*) return 0 ;;
  esac
  name="${p##*/}"
  [ "$name" = "TODO.md" ] && return 0
  namelc=$(printf '%s' "$name" | tr '[:upper:]' '[:lower:]')
  if [[ "$namelc" =~ _(plan|thought)(_check)?\.md$ ]]; then
    return 0
  fi
  case "$namelc" in
    *.run-state.json) return 0 ;;
  esac
  case "$p" in
    */Diary/*) return 0 ;;
  esac
  return 1
}

# --- worktree containment (mirrors run.py's _path_inside, two-phase) ---
# Phase 1 is the literal prefix match (the fast, common case -- the harness hands
# these paths in already absolute). Phase 2 re-checks on SYMLINK-RESOLVED spellings
# and runs ONLY when phase 1 says "outside": without it a symlink-spelled target
# reads as outside the worktree and this gate silently fails OPEN
# (execplan-gate-blast-radius A1, gap G6). Live, not hypothetical -- ~/Projects is
# a symlink to ~/repos/Projects, so one checkout has two spellings.
# Phase 2 is purely WIDENING (phase 1 returns first), so no containment decision
# this function used to make is reversed. Cost is zero on the common path: python3
# is spawned only for a target the cheap compare already rejected.
# python3 availability is established below, before the first path_inside call.
resolve_path() {
  python3 -c 'import os,sys; sys.stdout.write(os.path.realpath(os.path.expanduser(sys.argv[1])))' "$1" 2>/dev/null
}

path_inside() {
  local target="$1" root="$2" rt rr
  case "$target" in
    "$root"|"$root"/*) return 0 ;;
  esac
  rt=$(resolve_path "$target"); [ -z "${rt:-}" ] && return 1   # fail-open on resolver error
  rr=$(resolve_path "$root");   [ -z "${rr:-}" ] && return 1
  case "$rt" in
    "$rr"|"$rr"/*) return 0 ;;
  esac
  return 1
}

# Resolved early because the Bash branch's write detector calls run.py inline.
# fail-OPEN on infra error (mirrors check-execplan-entry-gate.sh).
RUN_PY="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/skills/execute-plan/run.py"
[ -f "$RUN_PY" ] || exit 0                          # fail-open: infra error
command -v python3 >/dev/null 2>&1 || exit 0        # fail-open: infra error

ACTION=""
TARGET_OR_TAG=""

case "$TOOL_NAME" in
  Edit|Write)
    TARGET_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
    [ -z "$TARGET_PATH" ] && exit 0
    is_excluded_bookkeeping_path "$TARGET_PATH" && exit 0
    path_inside "$TARGET_PATH" "$WORKTREE" || exit 0
    ACTION=$(printf '%s' "$TOOL_NAME" | tr '[:upper:]' '[:lower:]')   # edit | write
    ;;
  Bash)
    CMD=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
    [ -z "$CMD" ] && exit 0
    COMMIT_MSG=""
    if [[ "$CMD" =~ git[[:space:]]+commit ]]; then
      if [[ "$CMD" =~ -m[[:space:]]*\"([^\"]*)\" ]]; then
        COMMIT_MSG="${BASH_REMATCH[1]}"
      elif [[ "$CMD" =~ -m[[:space:]]*\'([^\']*)\' ]]; then
        COMMIT_MSG="${BASH_REMATCH[1]}"
      fi
    fi
    if [ -n "$COMMIT_MSG" ] && [[ "$COMMIT_MSG" =~ ^[[:space:]]*[Ss][0-9]+[[:space:]]*: ]]; then
      ACTION="commit"
      TARGET_OR_TAG="$COMMIT_MSG"
    else
      # Non-commit Bash: the shared `extract-bash-targets` verb resolves every write
      # destination (`>`/`>>` redirects AND cp/mv/install/sed -i/tee/dd of=, excluding
      # `2>`/`&>` fd-redirects and git-commit messages). NUL-terminated output is
      # ingested via a `read -d ''` process-substitution loop (Bash vars cannot hold a
      # NUL, so NEVER `$()`). Each target runs through the SAME bash-local
      # bookkeeping-exclusion + worktree-containment filters; the first in-worktree code
      # target that survives sets action=bash (one walk-gate-check call). O(1) Python
      # calls: the verb once + walk-gate-check once, independent of target count.
      GATED_TARGET=""
      while IFS= read -r -d '' t; do
        [ -z "$t" ] && continue
        is_excluded_bookkeeping_path "$t" && continue
        path_inside "$t" "$WORKTREE" || continue
        GATED_TARGET="$t"
        break
      done < <(jq -nc --arg c "$CMD" --arg cwd "$CWD" '{command:$c, cwd:$cwd}' \
                 | python3 "$RUN_PY" extract-bash-targets 2>/dev/null)
      if [ -n "${GATED_TARGET:-}" ]; then
        ACTION="bash"
        TARGET_OR_TAG="$GATED_TARGET"
      else
        exit 0
      fi
    fi
    ;;
  Agent)
    PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // empty' 2>/dev/null)
    TAG=$(printf '%s' "$PROMPT" | grep -oE '\[SLICE:[[:space:]]*[Ss][0-9]+[[:space:]]*\]' | head -1)
    # Union-fallback (mirrors check-impl-models.sh's [ACTION:]/[MODEL:] idiom): no
    # [SLICE:Sn] tag -> this is not a slice-implementation spawn -- never
    # spuriously block a checker/close-* or other untagged subagent.
    [ -z "$TAG" ] && exit 0
    ACTION="agent"
    TARGET_OR_TAG="$TAG"
    ;;
esac

[ -z "$ACTION" ] && exit 0
# RUN_PY + python3 availability were resolved (fail-open) before the matcher case.

# --- Resolve which in-flight run (if any) THIS session is the walker for. ---
# walk_gate_check() requires {run_id|surface_path} -- unlike execplan_entry_check()
# it does not self-scan the pointer dir. This is a READ-ONLY scan of the SAME
# run-<id>.json pointers run.py itself owns (no field is written here); it mirrors
# run.py's own `_pointer_visible_to` predicate (worktree-scoped pointer must match
# THIS worktree; a pointer with no worktree_root is a global/legacy fallback).
POINTER_DIR="${EXECPLAN_ACK_STATE_DIR:-$HOME/.claude/state/execplan_session_ack}"
RUN_ID=""
if [ -d "$POINTER_DIR" ]; then
  for f in "$POINTER_DIR"/run-*.json; do
    [ -f "$f" ] || continue
    WALKING=$(jq -r '.walking_session_id // empty' "$f" 2>/dev/null)
    [ "$WALKING" = "$SESSION_ID" ] || continue
    PWT=$(jq -r '.worktree_root // empty' "$f" 2>/dev/null)
    if [ -n "$PWT" ] && [ "$PWT" != "$WORKTREE" ]; then
      continue   # worktree-scoped to a DIFFERENT worktree -- not visible here
    fi
    CAND=$(jq -r '.run_id // empty' "$f" 2>/dev/null)
    if [ -n "$CAND" ]; then
      RUN_ID="$CAND"
      break
    fi
  done
fi
# This session is not the walker for any run visible to this worktree -> the
# ENTRY gate (S3) is the authority, not this one. No double-gating.
[ -z "$RUN_ID" ] && exit 0

DECISION=$(jq -nc --arg ws "$SESSION_ID" --arg act "$ACTION" --arg tot "$TARGET_OR_TAG" --arg rid "$RUN_ID" \
  '{writing_session_id:$ws, action:$act, target_or_tag:$tot, run_id:$rid}' \
  | python3 "$RUN_PY" walk-gate-check 2>/dev/null)

BLOCK=$(echo "$DECISION" | jq -r '.block // false' 2>/dev/null)
# Any parse/verb failure (empty DECISION, malformed JSON) also lands here as
# "false" via jq's `// false` default -> fail-open.
[ "$BLOCK" != "true" ] && exit 0

REASON=$(echo "$DECISION" | jq -r '.reason // "the checkout invariant blocked this action"' 2>/dev/null)
CURRENT=$(echo "$DECISION" | jq -r '.current_slice_id // "none"' 2>/dev/null)

cat >&2 <<EOF
check-execplan-walk-gate: BLOCKED -- the /execute-plan checkout invariant rejects
this ${TOOL_NAME} action (action=${ACTION}).

Reason: ${REASON}
Currently checked-out slice: ${CURRENT}
run_id: ${RUN_ID}
(execplan-contract-hardening Slice S4 -- code_first_architecture.md:
"Defend boundaries with code, not goodwill")

Pick one:
  (a) Check out the next ready slice via /execute-plan's own \`checkout-slice\` verb
      (the orchestrator does this automatically as it walks the register), then retry.
  (b) If a dependency's conformance check is missing, run /execute-plan so its
      isolated /double-check round runs and \`record-conformance\` writes the receipt.
  (c) Recovery override (LOGGED, use only if this gate itself is wrong):
      BYPASS_EXECPLAN=1 <retry the same tool call>
EOF
exit 2

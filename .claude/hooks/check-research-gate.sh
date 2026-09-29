#!/bin/bash
# Stop-event reader — enforces research-kind verdict at session close.
# Reads ~/.claude/state/plan_validation/<proj>/<topic>/research/R<N>.md
# for the active topic; requires verdict: PASS.
# Mirrors stop-plan-gate.sh shape + check-extraction-gate.sh:134 verdict-parse.
# Canon: ~/.claude/rules/factcheck-convergence.md (reader; section 7 schema)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

# Loop guard
STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$STOP_ACTIVE" = "true" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

# ---------------------------------------------------------------------------
# A4 — the obligation read. THIS MUST STAY ABOVE THE `_active.json` EXITS.
#
# Everything below line ~90 is reached only by a session with a bound topic and
# an existing research directory. That is exactly the population the diagnosed
# fault does NOT govern: when the engine refuses because no topic is bound, this
# gate exits at `[ -z "$PROJ" ]` before it reaches the per-cycle loop at all, so
# a refusal and a session that legitimately did no research are the same state.
# Placed below those exits this block would be dark for the one case it exists
# for.
#
# `ENGINE`/`PYBIN` are DEFINED HERE rather than at their historical site below,
# because that site is under those same exits — a call from up here would have
# invoked an empty string.
#
# Note what this block is NOT: it does not glob the marker directory. The single
# shell evidence probe in this file (the loop further down) is pinned by
# `EvidenceProbeConsistencyTests`, which asserts EXACTLY ONE across both gates,
# so all of this routes through a CLI verb instead. The probe's own loop keyword
# is deliberately not repeated in this comment — that test scrapes the file for
# it, and a second textual occurrence is a trap for the next reader even where
# the regex happens not to match.
# ---------------------------------------------------------------------------
ENGINE="$(dirname "$0")/_factcheck_engine.py"
PPG="$(dirname "$0")/pre_plan_gates.py"
PYBIN=$(command -v python3 2>/dev/null)
OBLIGATION_LEDGER="$HOME/.claude/state/research_obligations/${SESSION_ID}.ledger"

# "No check was owed" is NOT "the answer could not be read", and only the second
# may block — the same discipline this gate already states for its per-cycle
# probe further down. A session that dispatched no research check has no ledger,
# and must never be blocked by tooling it does not depend on.
if [ -s "$OBLIGATION_LEDGER" ]; then
  # Past this point a check WAS owed, so every undeterminable outcome BLOCKS
  # (Guiding Policy 5). This is the branch the guard below exists for: it is
  # deliberately fail-CLOSED, because down here "cannot tell" would mean passing
  # a file nobody checked.
  if [ -z "$PYBIN" ] || [ ! -f "$ENGINE" ]; then
    echo "BLOCKED: this session owes at least one research fact-check, and this gate cannot determine whether it ran." >&2
    [ -z "$PYBIN" ] && echo "  python3 is not on PATH." >&2
    [ ! -f "$ENGINE" ] && echo "  the fact-check engine is missing at $ENGINE." >&2
    echo "  Obligations recorded at: $OBLIGATION_LEDGER" >&2
    exit 2
  fi

  OBLIG=$("$PYBIN" "$ENGINE" research-obligations "$SESSION_ID" 2>/dev/null)
  ORC=$?
  OSTATUS=$(printf '%s' "$OBLIG" | jq -r '.status // empty' 2>/dev/null)

  if [ "$OSTATUS" != "OK" ]; then
    # Includes an engine with no such verb (exit 2 + no JSON), a traceback, and
    # unparseable output. Distinguished from the exit-2 "unmet" signal by
    # `status`, never by the exit code alone.
    OERR=$(printf '%s' "$OBLIG" | jq -r '.error // empty' 2>/dev/null)
    [ -z "$OERR" ] && OERR="the obligation reader exited $ORC with output this gate cannot parse"
    echo "BLOCKED: this session owes at least one research fact-check, and its status is undetermined — $OERR" >&2
    echo "  re-run: python3 $ENGINE research-obligations $SESSION_ID" >&2
    exit 2
  fi

  UNMET_COUNT=$(printf '%s' "$OBLIG" | jq -r '(.unmet // []) | length' 2>/dev/null)
  [ -z "$UNMET_COUNT" ] && UNMET_COUNT=0

  if [ "$UNMET_COUNT" -gt 0 ]; then
    echo "BLOCKED: ${UNMET_COUNT} research fact-check(s) this session owed are unmet — nothing checked these files:" >&2
    printf '%s' "$OBLIG" | jq -r '
      .unmet[] | "  - " + (.file // .raw // "«unreadable ledger row»") + " — " + (.why // "unmet")' >&2
    echo "" >&2
    # C4 lives HERE. The engine's own refusal text goes to a /tmp log nothing
    # reads, so this message is the one surface the operator reliably sees.
    echo "What to do next — one of:" >&2
    echo "  1. If a fact-check is still running in the background, wait ~30-60s and close again." >&2
    echo "     The engine dispatches its checkers asynchronously, so a block right after a save is expected, not a bug." >&2
    echo "  2. Bind a topic and run the check:" >&2
    echo "       /work-start <topic>        # then:" >&2
    echo "       python3 $PPG factcheck-research $SESSION_ID <RESEARCH_FILE>" >&2
    echo "  3. Accept the file unchecked, on the record (a reason is required and is kept):" >&2
    echo "       python3 $PPG accept-research-incomplete $SESSION_ID --file <RESEARCH_FILE> --reason \"<why>\"" >&2
    echo "" >&2
    echo "Do NOT stand in a hand-rolled checker panel instead: it produces no attested checker_models and no marker any gate can read, so it cannot clear this even when its judgement is sound (~/.claude/rules/orchestrator-pattern.md §5)." >&2
    # Only when there IS one. A remediation for a condition the operator does
    # not have is noise in a message they are reading under a block.
    MALFORMED=$(printf '%s' "$OBLIG" | jq -r '[.unmet[] | select(.malformed)] | length' 2>/dev/null)
    if [ -n "$MALFORMED" ] && [ "$MALFORMED" -gt 0 ] 2>/dev/null; then
      echo "An «unreadable ledger row» cannot be named to the accept verb. Move the ledger aside to clear it — that keeps the record rather than destroying it:" >&2
      echo "  mv \"$OBLIGATION_LEDGER\" \"$OBLIGATION_LEDGER.bak-\$(date +%Y%m%d%H%M%S)\"" >&2
    fi
    exit 2
  fi
fi

ACTIVE_FILE="$HOME/.claude/state/pre_plan_gates/_active.json"
[ -f "$ACTIVE_FILE" ] || exit 0

# _active.json is session-keyed dict; fields are topic_slug + active_project
# (verified against actual file shape + pre_plan_gates.py:_resolve_topic)
PROJ=$(jq -r --arg sid "$SESSION_ID" '.[$sid].topic_slug // empty' "$ACTIVE_FILE")
TOPIC=$(jq -r --arg sid "$SESSION_ID" '.[$sid].active_project // empty' "$ACTIVE_FILE")
[ -z "$PROJ" ] && exit 0
[ -z "$TOPIC" ] && exit 0

RESEARCH_BASE="$HOME/.claude/state/plan_validation/$PROJ/$TOPIC/research"
[ -d "$RESEARCH_BASE" ] || exit 0

# Resolve cycles from the session manifest. Multi-cycle runs (e.g.,
# multi-language fan-out) keep one verdict directory per cycle:
#   default cycle  -> $RESEARCH_BASE/R*.md  (legacy single-cycle layout)
#   non-default    -> $RESEARCH_BASE/<cycle>/R*.md  (forward-compat)
# Session with no manifest yet falls back to the default cycle.
RP_FILE="$HOME/.claude/state/research_pipeline/RP-${SESSION_ID}.json"
[ -n "$RP_STATE_DIR" ] && RP_FILE="$RP_STATE_DIR/RP-${SESSION_ID}.json"

if [ -f "$RP_FILE" ]; then
  CYCLES=$(jq -r '(.cycles // {}) | keys[]' "$RP_FILE" 2>/dev/null)
fi
[ -z "$CYCLES" ] && CYCLES="default"

# Lever B: does this topic's sequence include r4_factcheck? Enforced only when a
# manifest exists (mirrors check-research-pipeline-gate.sh's manifest-path scope,
# so Internal-KB / CV / no-manifest sessions are never false-blocked).
R4_IN_SEQ="false"
if [ -f "$RP_FILE" ]; then
  R4_IN_SEQ=$(python3 "$(dirname "$0")/research_pipeline.py" check "$SESSION_ID" 2>/dev/null | jq -r '.r4_in_sequence // false')
fi

# ---------------------------------------------------------------------------
# Per-file rendering (research-fc-backlog Group I item 2, slice S1).
#
# This reader used to collapse a whole cycle to ONE verdict:
#   ls -1 "$DIR"/R*.md | sort -V | tail -1
# With every marker in a cycle named R{n}.md, that "latest slip in the drawer"
# WAS the cycle's verdict — so one file's PASS masked another file's ESCALATE
# and the operator saw a green gate over work nobody checked.
#
# Markers are now keyed per file ({key}_R{n}.md), and the grouping decision
# lives in ONE shared verb (`_factcheck_engine.py research-rollup`). This script
# is left with a single responsibility: turn a rollup into an exit code and a
# message.
#
# NOW GENUINELY SHARED (S5). `check-research-pipeline-gate.sh` calls the same
# `research-rollup` verb on both of its read paths, so the two gates group the
# same directory identically and cannot drift apart. `BothGatesAgreeTests` in
# tests/test_per_file_fc_gate.py asserts it directly rather than leaving it to
# this comment.
#
# The history is kept because it is the point: between S1 and S5 the two readers
# really did disagree — this file read keyed groups while the pipeline gate still
# did `ls -1 "$DIR"/R*.md | sort -V | tail -1`, blind to every keyed marker. That
# is the state Guiding Policy 6 predicts for "stopping after the coercion audit",
# and one of the reasons the policy forbids promoting this work before S9. The
# mitigation was the single-promotion rule, never anything in this file.
#
# Fail-CLOSED (Guiding Policy 5): if the verb errors, times out, or emits output
# this gate cannot parse, the gate BLOCKS naming the failure. It must NOT clone
# the fail-open default a few lines above (R4_IN_SEQ, which defaults false on
# any failure) — that shape on the VERDICT path would pass a cycle nobody
# checked, which is the whole harm class this slice removes.
#
# The timeout is enforced INSIDE the Python verb, never by a shell `timeout`:
# neither `timeout` nor `gtimeout` exists on this host.
# ---------------------------------------------------------------------------

# ENGINE and PYBIN were defined here until A4. They are now set just after
# SESSION_ID is read, ABOVE the `_active.json` exits, because the obligation
# read up there needs them and this site is below those exits. One definition,
# not two — a second assignment here would be the kind of duplicate that drifts.
#
# Tab is an IFS *whitespace* character, so bash collapses runs of it and
# strips leading/trailing ones. An EMPTY field would therefore shift every
# later column and the gate would report the wrong text against the wrong
# file. The jq program below never emits an empty field (every value has a
# placeholder), which removes the hazard rather than relying on the reader
# to survive it.
TAB=$(printf '\t')
NIL="-"                       # the placeholder the jq program below emits

NON_PASS_COUNT=0
NON_PASS_LINES=""

while IFS= read -r CYCLE; do
  [ -z "$CYCLE" ] && continue
  if [ "$CYCLE" = "default" ]; then
    DIR="$RESEARCH_BASE"
  else
    DIR="$RESEARCH_BASE/$CYCLE"
  fi

  # "No evidence expected" is NOT "evidence could not be read", and only the
  # second may block. Decide which case this is WITHOUT the verb, so a cycle
  # that legitimately holds nothing (CV / Internal-KB) is never blocked by a
  # tooling failure it does not depend on.
  #
  # S4: evidence is a marker OR a `.dispatched-{key}` sentinel. The sentinel is
  # positive proof that a fact-check was ATTEMPTED for a specific file, taken
  # from the PostToolUse path-glob dispatcher rather than the session manifest —
  # so a dispatched-but-unchecked file is now surfaced through the rollup below,
  # independently of `R4_IN_SEQ`. That is the point of UX2: missing-marker
  # feedback is sentinel-driven, NOT Lever-B-driven, because Lever B can only
  # speak about work the manifest already knows about.
  HAS_EVIDENCE="false"
  if [ -d "$DIR" ]; then
    for _M in "$DIR"/*_R*.md "$DIR"/R*.md "$DIR"/.dispatched-*; do
      if [ -e "$_M" ]; then HAS_EVIDENCE="true"; break; fi
    done
  fi

  if [ "$HAS_EVIDENCE" = "false" ]; then
    # Lever B, NARROWED to its original job (S4): neither marker NOR sentinel,
    # so nothing was ever dispatched here. Block only when r4 is in this topic's
    # sequence (a real FC was expected); CV / Internal-KB / no-manifest → skip.
    if [ "$R4_IN_SEQ" = "true" ]; then
      NON_PASS_COUNT=$((NON_PASS_COUNT + 1))
      NON_PASS_LINES="${NON_PASS_LINES}  - cycle '$CYCLE': fact-check not run (no R-marker) — engine produced no verdict
"
    fi
    continue
  fi

  # From here a check WAS owed, so every undeterminable outcome BLOCKS.
  if [ -z "$PYBIN" ]; then
    NON_PASS_COUNT=$((NON_PASS_COUNT + 1))
    NON_PASS_LINES="${NON_PASS_LINES}  - cycle '$CYCLE': cannot determine per-file verdicts — python3 is not on PATH (markers exist in $DIR)
"
    continue
  fi

  ROLLUP=$("$PYBIN" "$ENGINE" research-rollup "$DIR" 2>/dev/null)
  RC=$?
  STATUS=$(printf '%s' "$ROLLUP" | jq -r '.status // empty' 2>/dev/null)

  if [ "$RC" -ne 0 ] || [ "$STATUS" != "OK" ]; then
    RERR=$(printf '%s' "$ROLLUP" | jq -r '.error // empty' 2>/dev/null)
    [ -z "$RERR" ] && RERR="rollup exited $RC with output this gate cannot parse"
    NON_PASS_COUNT=$((NON_PASS_COUNT + 1))
    NON_PASS_LINES="${NON_PASS_LINES}  - cycle '$CYCLE': verdict undetermined — $RERR
      re-run: python3 $ENGINE research-rollup $DIR
"
    continue
  fi

  # One row per research file. `resolved` already folds in the two sanctioned
  # exits (BYPASSED + bypass_reason, INCOMPLETE + accept_reason), decided in the
  # shared verb so both gates sanction identically — and PER ROW, so accepting
  # one file's incomplete never sanctions a sibling.
  ROWS=$(printf '%s' "$ROLLUP" | jq -r '
    def nz(v): if (v // "") == "" then "-" else v end;
    .rows[] | [ (if .legacy then "«unattributed legacy»" else nz(.key) end),
                nz(.verdict),
                ((.resolved // false) | tostring),
                nz(.error),
                nz(.marker) ] | @tsv' 2>/dev/null)

  while IFS="$TAB" read -r RKEY RVERDICT RRESOLVED RERROR RMARKER; do
    [ -z "$RKEY" ] && continue
    [ "$RRESOLVED" = "true" ] && continue
    NON_PASS_COUNT=$((NON_PASS_COUNT + 1))
    if [ -n "$RERROR" ] && [ "$RERROR" != "$NIL" ]; then
      NON_PASS_LINES="${NON_PASS_LINES}  - cycle '$CYCLE' · file '$RKEY': $RERROR (marker $RMARKER)
"
    elif [ "$RVERDICT" = "UNCHECKED" ]; then
      # A distinct class from a failing verdict: this file was dispatched and
      # produced NO verdict, so there is no marker to point at and nothing to
      # "resolve" — it has to be run.
      NON_PASS_LINES="${NON_PASS_LINES}  - cycle '$CYCLE' · file '$RKEY': UNCHECKED — dispatched, but no verdict was ever recorded
"
    else
      NON_PASS_LINES="${NON_PASS_LINES}  - cycle '$CYCLE' · file '$RKEY': verdict $RVERDICT (marker $RMARKER)
"
    fi
  done <<EOF
$ROWS
EOF
done <<<"$CYCLES"

if [ "$NON_PASS_COUNT" -gt 0 ]; then
  # The words "non-PASS" and "unverified" are part of this gate's established
  # operator-facing vocabulary and are asserted by sibling gate tests — the
  # rendering became per-file, the vocabulary did not change.
  echo "BLOCKED: Research-kind verdict non-PASS/unresolved for ${NON_PASS_COUNT} file-row(s) on topic '$PROJ/$TOPIC':" >&2
  printf '%s' "$NON_PASS_LINES" >&2
  echo "Re-run fact-check for each named file until verdict: PASS, or resolve ESCALATE." >&2
  echo "An UNCHECKED row means that file WAS dispatched and produced no verdict — re-run the fact-check for it. If that file no longer exists, re-running is impossible, so set its dispatch record aside instead:" >&2
  echo "  python3 $ENGINE adopt-legacy-markers <cycle-dir> --supersede --sentinel <research-file> --reason \"<why>\" --apply" >&2
  echo "An «unattributed legacy» row predates per-file keying and has no owning file — it cannot be attributed by code, so attribute or set it aside before it can resolve:" >&2
  echo "  python3 $ENGINE adopt-legacy-markers <cycle-dir> --adopt --file <research-file> --apply     # attribute it" >&2
  echo "  python3 $ENGINE adopt-legacy-markers <cycle-dir> --supersede --reason \"<why>\" --apply      # set it aside" >&2
  echo "  (both preview by default — omit --apply to see what would change; both are pure renames, restorable with a single mv)" >&2
  # UX3 — the boundary is stated where the operator reads the verdict, not only
  # in the module docstring. A gate whose green is trusted has to say what its
  # green does NOT cover; otherwise this change replaces one silent gap with a
  # smaller unstated one, which is the same harm at a smaller scale.
  echo "Detection boundary: a file that produced a marker, and a file that was dispatched but produced none, are both covered here regardless of session-manifest registration. A file that was never dispatched AND is not registered is NOT covered by this gate (residual R-1) — for example one written outside the fact-check dispatcher's path globs, created by a shell redirect or a git checkout, or produced in a session with CLAUDE_CODE_REMOTE=true." >&2
  echo "(check-research-pipeline-gate.sh emits the user-facing 3-part report + accepted/another-round prompt.)" >&2
  exit 2
fi

exit 0

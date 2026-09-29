#!/bin/bash
# Stop-event gate — enforces research pipeline checkpoint completeness AND
# surfaces non-PASS FC verdicts to the user (3-part report + 2-option prompt).
# Co-runs with check-research-gate.sh (orthogonal multi-cycle iterator).
#
# Exit codes:
#   0 — no manifest (non-research session) OR bypass set OR
#       all checkpoints complete AND every cycle's verdict is PASS
#   2 — manifest exists but required checkpoints are missing (blocked) OR
#       at least one cycle's verdict is non-PASS (user-prompt path)
#
# Unattended-session policy: env var CLAUDE_RESEARCH_ON_NON_PASS governs
# headless behavior on non-PASS verdicts. Values:
#   accepted     — auto-record non-PASS as accepted; exit 0
#   another-round — block with re-run instruction; exit 2
#   abort | unset — block with abort instruction; exit 2 (default per
#                   Anthropic headless-mode pattern, terminate the process)
#
# Environment overrides (test-friendly):
#   RP_STATE_DIR    — override default manifest directory
#   RP_ACTIVE_FILE  — override default _active.json path
#   RP_PLAN_VAL_DIR — override default plan_validation root
#                     (default: $HOME/.claude/state/plan_validation)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

STOP_ACTIVE=$(echo "$INPUT" | jq -r '.stop_hook_active // false')
[ "$STOP_ACTIVE" = "true" ] && exit 0

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

# ---------------------------------------------------------------------------
# parse_verdict_marker PATH
# Reads verdict: and bypass_reason: from R*.md frontmatter (schema_version: 3).
# Sets globals _PVM_VERDICT and _PVM_BYPASS_REASON (non-empty only on BYPASSED).
# Pattern mirrors _BYPASS_REASON_RE at _factcheck_engine.py:1210.
# Called by both the manifest path and the no-manifest fallback — single locus.
# ---------------------------------------------------------------------------
_PVM_VERDICT=""
_PVM_BYPASS_REASON=""
_PVM_ACCEPT_REASON=""
parse_verdict_marker() {
  _PVM_VERDICT=$(awk '/^---$/{n++; next} n==1 && /^verdict:/{print $2; exit}' "$1")
  _PVM_BYPASS_REASON=""
  _PVM_ACCEPT_REASON=""
  if [ "$_PVM_VERDICT" = "BYPASSED" ]; then
    _PVM_BYPASS_REASON=$(awk '/^---$/{n++; next} n==1 && /^bypass_reason:/{sub(/^bypass_reason:[[:space:]]*/, ""); gsub(/^"|"$/, ""); print; exit}' "$1")
  fi
  # S2/A14 (Bug 8): an accepted-INCOMPLETE marker carries a non-empty accept_reason.
  if [ "$_PVM_VERDICT" = "INCOMPLETE" ]; then
    _PVM_ACCEPT_REASON=$(awk '/^---$/{n++; next} n==1 && /^accept_reason:/{sub(/^accept_reason:[[:space:]]*/, ""); gsub(/^"|"$/, ""); print; exit}' "$1")
  fi
}

# S5: the shared per-file rollup verb both gates read through, plus the two
# rendering placeholders. `TAB`/`NIL` exist because tab is an IFS *whitespace*
# character — bash collapses runs of it and strips leading/trailing ones, so an
# empty field would shift every later column. The jq programs below emit a
# placeholder for every value so an empty field never occurs.
ENGINE="$(dirname "$0")/_factcheck_engine.py"
PYBIN=$(command -v python3 2>/dev/null)
TAB=$(printf '\t')
NIL="-"

# UX3 detection boundary — stated where the operator reads the verdict. The close
# gate carries this in its BLOCK footer, which is only seen when something blocks;
# a GREEN verdict is read HERE, so the boundary belongs on this report too.
DETECTION_BOUNDARY="Detection boundary: a file that produced a marker, and a file that was dispatched but produced none, are both covered regardless of session-manifest registration. A file that was never dispatched AND is not registered is NOT covered by this gate (residual R-1) — for example one written outside the fact-check dispatcher's path globs, created by a shell redirect or a git checkout, or produced in a session with CLAUDE_CODE_REMOTE=true."

# Resolve state directory (test-override-friendly)
if [ -n "$RP_STATE_DIR" ]; then
  STATE_FILE="$RP_STATE_DIR/RP-${SESSION_ID}.json"
else
  STATE_FILE="$HOME/.claude/state/research_pipeline/RP-${SESSION_ID}.json"
fi

# ---------------------------------------------------------------------------
# No manifest → check for Internal KB fallback (path b), then exit 0.
# Internal KB sessions intentionally skip r0_intake (no manifest). When an
# active topic context exists and a recent R*.md is found in the research dir
# (mtime within 6-hour session window), surface its verdict via RESEARCH-VERDICT:.
# Mtime window closes the misattribution risk of R*.md files from prior sessions.
# ---------------------------------------------------------------------------
if [ ! -f "$STATE_FILE" ]; then
  _NM_ACTIVE="${RP_ACTIVE_FILE:-$HOME/.claude/state/pre_plan_gates/_active.json}"
  _NM_PLAN_VAL="${RP_PLAN_VAL_DIR:-$HOME/.claude/state/plan_validation}"
  if [ -f "$_NM_ACTIVE" ]; then
    _NM_PROJ=$(jq -r --arg sid "$SESSION_ID" '.[$sid].topic_slug // empty' "$_NM_ACTIVE")
    _NM_TOPIC=$(jq -r --arg sid "$SESSION_ID" '.[$sid].active_project // empty' "$_NM_ACTIVE")
    if [ -n "$_NM_PROJ" ] && [ -n "$_NM_TOPIC" ]; then
      _NM_BASE="$_NM_PLAN_VAL/$_NM_PROJ/$_NM_TOPIC/research"
      if [ -d "$_NM_BASE" ]; then
        # S5: key-group-aware. The old probe was `-name "R*.md"`, which matches
        # NO keyed marker — so after per-file keying an Internal-KB session's
        # verdict became invisible on this path. The 6-hour mtime window is
        # unchanged and still decides WHETHER to report: it guards against
        # misattributing a prior session's markers. Widening which names count
        # as evidence must not widen that window.
        #
        # `.dispatched-*` is in this probe DELIBERATELY, and its absence was a
        # real defect. A first cut listed only the two marker patterns, which
        # made THIS path — the one that runs when there is no manifest at all —
        # the single evidence probe in the system blind to dispatch sentinels.
        # That is exactly backwards: AD11/UX2 introduce the sentinel *because*
        # it is registration-independent, i.e. precisely to cover work the
        # manifest never knew about. A dispatched-but-unchecked file in an
        # Internal-KB session would have produced no match, so the rollup was
        # never invoked and the operator was told nothing. Found by a
        # cross-slice check reading S4, S5 and S6 together; neither slice's own
        # verification could see it.
        _NM_RECENT=$(find "$_NM_BASE" -maxdepth 1 \
                       \( -name "*_R*.md" -o -name "R*.md" -o -name ".dispatched-*" \) \
                       -mmin -360 2>/dev/null | head -1)
        if [ -n "$_NM_RECENT" ] && [ -n "$PYBIN" ]; then
          _NM_ROLLUP=$("$PYBIN" "$ENGINE" research-rollup "$_NM_BASE" 2>/dev/null)
          _NM_STATUS=$(printf '%s' "$_NM_ROLLUP" | jq -r '.status // empty' 2>/dev/null)
          if [ "$_NM_STATUS" = "OK" ]; then
            # INFORMATIONAL ONLY — this path never blocks, by design, and S5 does
            # not change that. It reports one line per research file instead of
            # one line per cycle.
            _NM_ROWS=$(printf '%s' "$_NM_ROLLUP" | jq -r '
              def nz(v): if (v // "") == "" then "-" else v end;
              .rows[] | [ (if .legacy then "«unattributed legacy»" else nz(.key) end),
                          nz(.verdict),
                          nz(.marker) ] | @tsv' 2>/dev/null)
            while IFS="$TAB" read -r _NMKEY _NMVERDICT _NMMARKER; do
              [ -z "$_NMKEY" ] && continue
              if [ "$_NMMARKER" != "$NIL" ] && [ -f "$_NMMARKER" ]; then
                parse_verdict_marker "$_NMMARKER"
                if [ "$_PVM_VERDICT" = "BYPASSED" ] && [ -n "$_PVM_BYPASS_REASON" ]; then
                  echo "RESEARCH-VERDICT: BYPASSED — $_PVM_BYPASS_REASON (informational; file: $_NMKEY; marker: $_NMMARKER)" >&2
                  continue
                fi
              fi
              [ "$_NMVERDICT" = "$NIL" ] && continue
              echo "RESEARCH-VERDICT: $_NMVERDICT (no-manifest; Internal KB session; file: $_NMKEY; marker: $_NMMARKER)" >&2
            done <<EOF
$_NM_ROWS
EOF
          fi
        fi
      fi
    fi
  fi
  exit 0
fi

# Bypass set → require non-empty bypass_reason (after whitespace strip) before
# accepting the whole-gate sidestep. Closes the pre-existing silent surface
# (A3b — mirrors A2's per-cycle whitespace-strip pattern).
BYPASS=$(jq -r '.bypass // false' "$STATE_FILE")
if [ "$BYPASS" = "true" ]; then
  BYPASS_REASON=$(jq -r '.bypass_reason // ""' "$STATE_FILE")
  TRIMMED=$(printf '%s' "$BYPASS_REASON" | tr -d '[:space:]')
  if [ -z "$TRIMMED" ]; then
    echo "BLOCKED: Pipeline .bypass is true but .bypass_reason is empty/whitespace-only in $STATE_FILE." >&2
    echo "Set .bypass_reason to a non-empty descriptive string (e.g., 'all 3 checkers timed out 2026-06-11')." >&2
    echo "Or invoke: python3 ${KIT_HOOKS_DIR}/research_pipeline.py bypass $SESSION_ID \"<reason>\"" >&2
    exit 2
  fi
  echo "PIPELINE BYPASSED: $BYPASS_REASON" >&2
  exit 0
fi

# Determine expected sequence (PIPELINE_OVERRIDES or full sequence)
TOPIC_SLUG=$(jq -r '.topic_slug // empty' "$STATE_FILE")

# Ask research_pipeline.py for the missing checkpoints
HOOKS_DIR="$(dirname "$0")"
MISSING_JSON=$(python3 "$HOOKS_DIR/research_pipeline.py" check "$SESSION_ID" 2>/dev/null)

# Fallback: if python3 call fails, read state file directly
if [ -z "$MISSING_JSON" ]; then
  echo "BLOCKED: research_pipeline.py check failed for session $SESSION_ID" >&2
  exit 2
fi

COMPLETE=$(echo "$MISSING_JSON" | jq -r '.complete // false')
# Lever B: does this topic's expected sequence include r4_factcheck? (CV excludes
# it.) Used below to block a cycle that is complete but has no fact-check marker.
R4_IN_SEQ=$(echo "$MISSING_JSON" | jq -r '.r4_in_sequence // false')

if [ "$COMPLETE" != "true" ]; then
  # Pipeline incomplete → existing checkpoint-missing path
  MISSING_QUOTED=$(echo "$MISSING_JSON" | jq -r '.missing | join(", ")' 2>/dev/null)
  echo "BLOCKED: Research pipeline incomplete for session '$SESSION_ID'." >&2
  echo "Missing checkpoints: $MISSING_QUOTED" >&2
  echo "Run 'python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID <checkpoint> <json>' to record each step." >&2
  echo "Or bypass: python3 ${KIT_HOOKS_DIR}/research_pipeline.py bypass $SESSION_ID \"reason\"" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Pipeline complete — A4(a): non-PASS verdict surfacing per cycle.
# ---------------------------------------------------------------------------

ACTIVE_FILE="${RP_ACTIVE_FILE:-$HOME/.claude/state/pre_plan_gates/_active.json}"
[ -f "$ACTIVE_FILE" ] || exit 0

# Mirror check-research-gate.sh's variable convention: PROJ holds the
# topic_slug, TOPIC holds the active_project. Names are misleading, but the
# on-disk layout is plan_validation/<topic_slug>/<project_slug>/research/
# (see pre_plan_gates._resolve_topic return order + _factcheck_engine
# topic_dir construction).
PROJ=$(jq -r --arg sid "$SESSION_ID" '.[$sid].topic_slug // empty' "$ACTIVE_FILE")
TOPIC=$(jq -r --arg sid "$SESSION_ID" '.[$sid].active_project // empty' "$ACTIVE_FILE")
[ -z "$PROJ" ] && exit 0
[ -z "$TOPIC" ] && exit 0

PLAN_VAL_DIR="${RP_PLAN_VAL_DIR:-$HOME/.claude/state/plan_validation}"
RESEARCH_BASE="$PLAN_VAL_DIR/$PROJ/$TOPIC/research"
[ -d "$RESEARCH_BASE" ] || exit 0

# Iterate cycles in the manifest. Default cycle reads $RESEARCH_BASE/R*.md;
# non-default cycles read $RESEARCH_BASE/<cycle>/R*.md (forward-compatible
# per-cycle layout; engine writes the flat path today for cycle='default').
CYCLES=$(jq -r '(.cycles // {}) | keys[]' "$STATE_FILE" 2>/dev/null)
[ -z "$CYCLES" ] && CYCLES="default"

NON_PASS_COUNT=0
NON_PASS_REPORT=""
BYPASSED_REPORT=""
# D2: the ids of the cycles that actually failed, newline-separated. The loop
# below already knows which cycle each finding belongs to; before D2 it kept
# only a COUNT and a prose report, so the dispatch after the loop had no way to
# name the failing cycle and fell back to `default` — withdrawing a run that was
# never the one that failed. Newline-separated so an id containing spaces is
# never split; empty means nothing failed, and nothing is then dispatched.
NON_PASS_CYCLES=""

while IFS= read -r CYCLE; do
  [ -z "$CYCLE" ] && continue
  if [ "$CYCLE" = "default" ]; then
    DIR="$RESEARCH_BASE"
  else
    DIR="$RESEARCH_BASE/$CYCLE"
  fi

  CYCLE_NON_PASS=0
  CYCLE_SECTION=""

  # ---- Dimension 1: factcheck verdicts, PER FILE (S5) ----
  #
  # This read used to be `ls -1 "$DIR"/R*.md | sort -V | tail -1` — ONE verdict
  # for the whole cycle, taken from the newest slip in the drawer. It therefore
  # (a) let one file's PASS mask a sibling's ESCALATE, and (b) after per-file
  # keying, matched NO keyed name at all, so a cycle whose markers were all keyed
  # looked like a cycle with no markers.
  #
  # Both shell gates now consume the SAME shared verb
  # (`_factcheck_engine.py research-rollup`), so they cannot report different
  # groupings for the same directory. Ordering INSIDE a key group is by parsed
  # round NUMBER, not a lexical sort — `{key}_R10` is later than `{key}_R2`,
  # which `sort -V` gave us here and an ordinary sort would have got wrong.
  #
  # Fail-CLOSED (Guiding Policy 5): if the verb errors, times out, or emits
  # output this gate cannot parse, the cycle is reported non-PASS naming the
  # failure — never waved through.
  HAS_EVIDENCE="false"
  if [ -d "$DIR" ]; then
    for _M in "$DIR"/*_R*.md "$DIR"/R*.md "$DIR"/.dispatched-*; do
      if [ -e "$_M" ]; then HAS_EVIDENCE="true"; break; fi
    done
  fi

  if [ "$HAS_EVIDENCE" = "true" ]; then
    if [ -z "$PYBIN" ]; then
      CYCLE_NON_PASS=1
      CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE — verdict undetermined ===
python3 is not on PATH, so the per-file rollup could not run — but markers exist
in $DIR, so a check WAS owed and its result is unknown.
"
    else
      ROLLUP=$("$PYBIN" "$ENGINE" research-rollup "$DIR" 2>/dev/null)
      RC=$?
      STATUS=$(printf '%s' "$ROLLUP" | jq -r '.status // empty' 2>/dev/null)

      if [ "$RC" -ne 0 ] || [ "$STATUS" != "OK" ]; then
        RERR=$(printf '%s' "$ROLLUP" | jq -r '.error // empty' 2>/dev/null)
        [ -z "$RERR" ] && RERR="rollup exited $RC with output this gate cannot parse"
        CYCLE_NON_PASS=1
        CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE — verdict undetermined ===
$RERR
Re-run: python3 $ENGINE research-rollup $DIR
"
      else
        # One row per research file. Every value carries a placeholder so no
        # field is ever empty: tab is an IFS *whitespace* character, so bash
        # collapses runs of it and an empty field would shift every later column
        # and attribute the wrong text to the wrong file.
        ROWS=$(printf '%s' "$ROLLUP" | jq -r '
          def nz(v): if (v // "") == "" then "-" else v end;
          .rows[] | [ (if .legacy then "«unattributed legacy»" else nz(.key) end),
                      nz(.verdict),
                      ((.resolved // false) | tostring),
                      ((.sanctioned // false) | tostring),
                      nz(.error),
                      nz(.marker) ] | @tsv' 2>/dev/null)

        while IFS="$TAB" read -r RKEY RVERDICT RRESOLVED RSANCTIONED RERROR RMARKER; do
          [ -z "$RKEY" ] && continue

          # A sanctioned row resolved through a deliberate operator exit
          # (BYPASSED + reason, or accepted INCOMPLETE). It is informational —
          # and it is resolved PER ROW, so accepting one file's incomplete never
          # sanctions a sibling.
          if [ "$RRESOLVED" = "true" ] && [ "$RSANCTIONED" = "true" ]; then
            # Report ALWAYS, even when the marker cannot be re-read here.
            #
            # The reason text needs a second, shell-side read of the marker, and
            # that read can fail — the file may have been moved or removed
            # between the rollup and this loop. An earlier cut appended to the
            # report only INSIDE that file-exists guard while `continue`-ing
            # unconditionally, so such a row vanished entirely: it did not block
            # (correct) and it was not reported either (not correct). A sanctioned
            # exit is a deliberate operator decision; silently dropping it is how
            # a sanctioned row stops being auditable.
            _SREASON=""
            if [ "$RMARKER" != "$NIL" ] && [ -f "$RMARKER" ]; then
              parse_verdict_marker "$RMARKER"
              if [ "$RVERDICT" = "BYPASSED" ]; then
                _SREASON="$_PVM_BYPASS_REASON"
              else
                _SREASON="$_PVM_ACCEPT_REASON"
              fi
            fi
            [ -z "$_SREASON" ] && _SREASON="(reason unavailable — marker not readable at $RMARKER)"
            if [ "$RVERDICT" = "BYPASSED" ]; then
              BYPASSED_REPORT="${BYPASSED_REPORT}
=== Cycle: $CYCLE · file '$RKEY' — verification BYPASSED ===
Reason: $_SREASON
Marker: $RMARKER
"
            else
              BYPASSED_REPORT="${BYPASSED_REPORT}
=== Cycle: $CYCLE · file '$RKEY' — accepted INCOMPLETE ===
Accept reason: $_SREASON
Marker: $RMARKER
"
            fi
            continue
          fi

          [ "$RRESOLVED" = "true" ] && continue      # a genuine PASS

          CYCLE_NON_PASS=1
          if [ -n "$RERROR" ] && [ "$RERROR" != "$NIL" ]; then
            CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE · file '$RKEY' — verdict undetermined ===
$RERROR (marker $RMARKER)
"
          elif [ "$RVERDICT" = "UNCHECKED" ]; then
            CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE · file '$RKEY' — UNCHECKED ===
This file was dispatched for fact-check and produced no verdict at all. Re-run it:
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-research $SESSION_ID <research-file-path>
If that file no longer exists, re-running is impossible — set its dispatch record
aside instead (previews by default; omit --apply to see what would change):
  python3 $ENGINE adopt-legacy-markers $DIR --supersede --sentinel <research-file> --reason \"<why>\" --apply
"
          else
            ROUNDS="?"
            CHECKER_COUNT="?"
            UNRESOLVED="(marker not readable)"
            if [ "$RMARKER" != "$NIL" ] && [ -f "$RMARKER" ]; then
              ROUNDS=$(awk '/^---$/{n++; next} n==1 && /^rounds:/{print $2; exit}' "$RMARKER")
              CHECKER_COUNT=$(awk '/^---$/{n++; next} n==1 && /^checker_count:/{print $2; exit}' "$RMARKER")
              UNRESOLVED=$(grep -E "^DISCREPANCY:" "$RMARKER" 2>/dev/null | head -10)
              [ -z "$UNRESOLVED" ] && UNRESOLVED="(no DISCREPANCY: lines in latest round; see $RMARKER body)"
            fi
            CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE · file '$RKEY' — verdict: $RVERDICT ===
What happened: ${ROUNDS:-?} round(s) with ${CHECKER_COUNT:-?} checker(s); marker $RMARKER.
What was fixed: prior rounds in this file's own sequence in $DIR. PASS-flipped discrepancies are no longer listed below.
What's still unresolved:
$UNRESOLVED
"
            # An unattributed legacy row has NO owning file, so this script's
            # standing remediation at the bottom ("re-dispatch factcheck for the
            # affected cycle's research file") is inapplicable to it — there is
            # no file to name. Without the lines below the operator is told to do
            # something impossible and never hears about the verb that actually
            # resolves the row. The close gate carried this remediation and this
            # gate did not, which is the same asymmetry that made the no-manifest
            # probe sentinel-blind.
            if [ "$RKEY" = "«unattributed legacy»" ]; then
              CYCLE_SECTION="${CYCLE_SECTION}This row predates per-file keying and cannot be attributed to a file by code, so re-running does not clear it. Resolve it with:
  python3 $ENGINE adopt-legacy-markers $DIR --adopt --file <research-file> --apply     # attribute it
  python3 $ENGINE adopt-legacy-markers $DIR --supersede --reason \"<why>\" --apply      # set it aside
  (both preview by default — omit --apply to see what would change; both are pure renames, restorable with a single mv)
"
            fi
          fi
        done <<EOF
$ROWS
EOF
      fi
    fi
  elif [ "$R4_IN_SEQ" = "true" ]; then
    # Lever B — r4_factcheck is in this topic's sequence but NO fact-check
    # marker exists for the cycle: the engine produced no verdict. Don't let it
    # close green (the old no-marker hole). Preserved: no-manifest/Internal-KB
    # (exits earlier), whole-pipeline BYPASS (exits earlier), CV (r4 not in
    # sequence → R4_IN_SEQ=false), accepted/BYPASSED cycles (write a marker →
    # LATEST set → handled in the branch above).
    CYCLE_NON_PASS=1
    CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE — fact-check not run (no R-marker) ===
The fact-check engine has not produced a verdict for this cycle yet. It runs in
the background on _RESEARCH.md save; wait ~30-60s and retry close, or re-dispatch:
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-research $SESSION_ID <research-file-path>
"
  fi

  # ---- Dimension 2: linkcheck broken_count (S6-shipped, A6) ----
  # Read from cycles[CYCLE].linkcheck.broken_count in the manifest. A cycle
  # may surface as non-PASS via verdict, broken links, or both — only one
  # increment of NON_PASS_COUNT per cycle.
  BROKEN_COUNT=$(jq -r --arg c "$CYCLE" '(.cycles[$c].linkcheck.broken_count // 0)' "$STATE_FILE" 2>/dev/null)
  if [ -n "$BROKEN_COUNT" ] && [ "$BROKEN_COUNT" -gt 0 ] 2>/dev/null; then
    CYCLE_NON_PASS=1
    OK_COUNT=$(jq -r --arg c "$CYCLE" '(.cycles[$c].linkcheck.ok_count // 0)' "$STATE_FILE")
    CHECKED_AT=$(jq -r --arg c "$CYCLE" '(.cycles[$c].linkcheck.checked_at // "?")' "$STATE_FILE")
    RESEARCH_PATH=$(jq -r --arg c "$CYCLE" '(.cycles[$c].research_file_path // "?")' "$STATE_FILE")
    CYCLE_SECTION="${CYCLE_SECTION}
=== Cycle: $CYCLE — broken external links ===
Linkcheck: $BROKEN_COUNT broken / $OK_COUNT ok (checked $CHECKED_AT).
Broken URLs are tagged inline in $RESEARCH_PATH as '⚠ BROKEN (HTTP <code> at <ts>)'.
User acknowledgement required before delivery.
"
  fi

  if [ $CYCLE_NON_PASS -eq 1 ]; then
    NON_PASS_COUNT=$((NON_PASS_COUNT + 1))
    NON_PASS_REPORT="${NON_PASS_REPORT}${CYCLE_SECTION}"
    NON_PASS_CYCLES="${NON_PASS_CYCLES}${CYCLE}
"
  fi
done <<<"$CYCLES"

if [ "$NON_PASS_COUNT" -eq 0 ]; then
  # Surface BYPASSED cycles to stderr for visibility even when nothing blocks.
  [ -n "$BYPASSED_REPORT" ] && {
    echo "INFO: research cycle(s) bypassed (verification not run):" >&2
    echo "$BYPASSED_REPORT" >&2
  }
  echo "RESEARCH-VERDICT: PASS" >&2
  echo "$DETECTION_BOUNDARY" >&2
  exit 0
fi

# Policy dispatch: explicit per-invocation flag wins over the interactive
# default. CLAUDE_RESEARCH_ON_NON_PASS unset → interactive prompt path
# (AI invokes AskUserQuestion in response to the stderr below).
ON_NON_PASS="${CLAUDE_RESEARCH_ON_NON_PASS:-}"
case "$ON_NON_PASS" in
  accepted)
    # D2: record the acceptance against EACH cycle that actually failed. This
    # is the audit trail ("which run did the operator let through?"), so filing
    # it all under `default` made the record state something untrue. No gate
    # reads `non_pass_verdict` (write_accept_marker owns closing), so this is
    # attribution, not gating.
    printf '%s' "$NON_PASS_CYCLES" | while IFS= read -r _NPC; do
      [ -z "$_NPC" ] && continue
      python3 "$HOOKS_DIR/research_pipeline.py" dispatch-non-pass "$SESSION_ID" \
        --on-non-pass accepted --cycle-id "$_NPC" \
        >/dev/null 2>&1
    done
    echo "RESEARCH-VERDICT: PASS (auto-accepted; ${NON_PASS_COUNT} non-PASS cycle(s) recorded via CLAUDE_RESEARCH_ON_NON_PASS=accepted)" >&2
    exit 0
    ;;
  another-round)
    echo "RESEARCH-VERDICT: ${NON_PASS_COUNT} non-PASS research cycle(s); CLAUDE_RESEARCH_ON_NON_PASS=another-round." >&2
    echo "$NON_PASS_REPORT" >&2
    [ -n "$BYPASSED_REPORT" ] && {
      echo "(Informational — also bypassed cycle(s):)" >&2
      echo "$BYPASSED_REPORT" >&2
    }
    echo "ACTION: re-dispatch factcheck for each affected cycle's research file" >&2
    echo "  (e.g., python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-research $SESSION_ID <path>)." >&2
    exit 2
    ;;
  abort)
    echo "RESEARCH-VERDICT: ${NON_PASS_COUNT} non-PASS research cycle(s); CLAUDE_RESEARCH_ON_NON_PASS=abort (Anthropic headless-mode default for unattended runs)." >&2
    echo "$NON_PASS_REPORT" >&2
    [ -n "$BYPASSED_REPORT" ] && {
      echo "(Informational — also bypassed cycle(s):)" >&2
      echo "$BYPASSED_REPORT" >&2
    }
    # D2: revoke EVERY cycle that failed — and only those. An abort is a
    # response to the runs that failed verification; a cycle that PASSED was
    # not aborted and keeps its approval. Before D2 this always revoked
    # `default`, so a German-only run that failed left `de` approved and
    # withdrew a run that was never in question.
    printf '%s' "$NON_PASS_CYCLES" | while IFS= read -r _NPC; do
      [ -z "$_NPC" ] && continue
      python3 "$HOOKS_DIR/research_pipeline.py" dispatch-non-pass "$SESSION_ID" \
        --on-non-pass abort --cycle-id "$_NPC" \
        >/dev/null 2>&1
    done
    exit 2
    ;;
  "")
    # Interactive default — AI surfaces AskUserQuestion in response.
    echo "RESEARCH-VERDICT: ${NON_PASS_COUNT} non-PASS research cycle(s)." >&2
    echo "$NON_PASS_REPORT" >&2
    [ -n "$BYPASSED_REPORT" ] && {
      echo "(Informational — also bypassed cycle(s):)" >&2
      echo "$BYPASSED_REPORT" >&2
    }
    echo "ACTION REQUIRED: invoke AskUserQuestion with two options per non-PASS cycle:" >&2
    echo "  1. 'accepted'                    — proceed past gate (records non-PASS as accepted; durable in transcript JSONL)" >&2
    echo "  2. 'one more verification round' — re-run convergence factcheck" >&2
    # D2: name the failing cycle in the command the operator is told to run —
    # one line per failing cycle. Without --cycle-id the command records the
    # acceptance against `default`, which may be none of the runs that failed,
    # so an operator copy-pasting it repeated the mis-target by hand.
    printf '%s' "$NON_PASS_CYCLES" | while IFS= read -r _NPC; do
      [ -z "$_NPC" ] && continue
      echo "On 'accepted':       python3 ${KIT_HOOKS_DIR}/research_pipeline.py dispatch-non-pass $SESSION_ID --on-non-pass accepted --cycle-id $_NPC" >&2
    done
    echo "On 'another-round':  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-research $SESSION_ID <research-file-path>" >&2
    exit 2
    ;;
  *)
    echo "RESEARCH-VERDICT: ${NON_PASS_COUNT} non-PASS research cycle(s); CLAUDE_RESEARCH_ON_NON_PASS='${ON_NON_PASS}' is unknown → abort." >&2
    echo "$NON_PASS_REPORT" >&2
    [ -n "$BYPASSED_REPORT" ] && {
      echo "(Informational — also bypassed cycle(s):)" >&2
      echo "$BYPASSED_REPORT" >&2
    }
    exit 2
    ;;
esac

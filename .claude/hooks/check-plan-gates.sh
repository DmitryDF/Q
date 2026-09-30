#!/bin/bash
# Shared gate-checking logic for plan files.
# Called by permission-plan-gate.sh and stop-plan-gate.sh.
#
# Usage: check-plan-gates.sh <plan-file-path>
#
# Exit codes:
#   0 = all gates present
#   2 = one or more gates missing (details on stderr)

# Order-tolerant arg parse: --dry-run may appear before or after the plan path.
# The production callers (permission-plan-gate.sh, stop-plan-gate.sh) pass ONLY
# the plan path, so they are unaffected by this flag detection.
DRY_RUN=0
PLAN_FILE=""
for _arg in "$@"; do
  case "$_arg" in
    --dry-run) DRY_RUN=1 ;;
    *) [ -z "$PLAN_FILE" ] && PLAN_FILE="$_arg" ;;
  esac
done

if [ -z "$PLAN_FILE" ] || [ ! -f "$PLAN_FILE" ]; then
  echo "BLOCKED: No plan file found at: $PLAN_FILE" >&2
  exit 2
fi

# infer_reason SNIPPET FILE_PATH [LINE_REF]
# Report-only diagnostics: re-test a failed snippet against relaxed copies and
# name the first relaxation that WOULD have matched. This NEVER touches the real
# grep -qF decision or the UNGROUNDED counter — it only explains an already-
# decided failure. Match scope mirrors the real check: a ±5-line window when a
# line reference is present, else the whole file.
infer_reason() {
  local snip="$1" fpath="$2" lref="$3" scope start end
  if [ -n "$lref" ]; then
    start=$((lref - 5)); [ "$start" -lt 1 ] && start=1
    end=$((lref + 5))
    scope=$(sed -n "${start},${end}p" "$fpath")
  else
    scope=$(cat "$fpath")
  fi
  # (1) whitespace mismatch — matches after collapsing all runs of whitespace
  local snip_ws scope_ws
  snip_ws=$(printf '%s' "$snip" | tr -s '[:space:]' ' ')
  scope_ws=$(printf '%s' "$scope" | tr -s '[:space:]' ' ')
  if printf '%s' "$scope_ws" | grep -qF -- "$snip_ws"; then
    echo "whitespace mismatch (matches after collapsing whitespace)"; return
  fi
  # (2) added/removed quotes — matches after deleting \042 (") and \047 (')
  local snip_nq scope_nq
  snip_nq=$(printf '%s' "$snip" | tr -d '\042\047')
  scope_nq=$(printf '%s' "$scope" | tr -d '\042\047')
  if [ -n "$snip_nq" ] && printf '%s' "$scope_nq" | grep -qF -- "$snip_nq"; then
    echo "added/removed quotes (matches after stripping quotes)"; return
  fi
  # (3) interior ellipsis — every fragment (split on ... or …) present in scope
  case "$snip" in
    *...*|*…*)
      local ok=1 frag
      while IFS= read -r frag; do
        [ -z "$frag" ] && continue
        printf '%s' "$scope" | grep -qF -- "$frag" || ok=0
      done < <(printf '%s' "$snip" | sed 's/…/.../g' \
        | awk '{n=split($0,a,/\.\.\./); for(i=1;i<=n;i++){gsub(/^[ \t]+|[ \t]+$/,"",a[i]); if(a[i]!="") print a[i]}}')
      [ "$ok" -eq 1 ] && { echo "interior ellipsis (all fragments present, joined by ...)"; return; }
      ;;
  esac
  echo "no reason inferred (check the line number, the ±5-line window, or the snippet text)"
}

# Collects per-row grounding notes across the whole run (real failures + the
# report-only unresolvable-path notes). Initialized here so it always exists,
# including for the --dry-run report and for plans with no Gate 1 table.
GROUNDING_DETAILS=()

MISSING=()

# Gate 0a: Diagnosis
if ! grep -qE '^<!-- GATE0A:PROBLEM -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0a (Diagnosis): Add <!-- GATE0A:PROBLEM --> after identifying the critical challenge.")
fi

# Gate 0b: Desired Outcome
if ! grep -qE '^<!-- GATE0B:OUTCOME -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0b (Desired Outcome): Add <!-- GATE0B:OUTCOME --> after defining the desired outcome.")
fi

# Gate 0b2: Outcome Claims
if ! grep -qE '^<!-- GATE0B2:CLAIMS -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0b2 (Outcome Claims): Add <!-- GATE0B2:CLAIMS --> after decomposing the outcome into testable claims.")
fi

# Gate 0c: Gap Analysis
if ! grep -qE '^<!-- GATE0C:GAPS -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0c (Gap Analysis): Add <!-- GATE0C:GAPS --> after listing gaps between current and desired state.")
fi

# Gate 0d: Guiding Policy
if ! grep -qE '^<!-- GATE0D:POLICY -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0d (Guiding Policy): Add <!-- GATE0D:POLICY --> after stating the overall approach.")
fi

# Gate 0e: Coherent Actions (accepts legacy GATE0D:ACTIONS)
if ! grep -qE '^<!-- GATE0E:ACTIONS -->$' "$PLAN_FILE" && ! grep -qE '^<!-- GATE0D:ACTIONS -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0e (Coherent Actions): Add <!-- GATE0E:ACTIONS --> after mapping each action to a gap and guiding policy.")
fi

# Gate 0f: Design Review
if ! grep -qE '^<!-- GATE0F:REVIEWED -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 0f (Design Review): Add <!-- GATE0F:REVIEWED --> after reviewing triggers, thresholds, edge cases, and expert alignment against exploration findings.")
fi

# Gate 0 ordering: markers must appear in sequence (0A < 0B < 0C < 0D < 0E)
if grep -qE '^<!-- GATE0A:PROBLEM -->$' "$PLAN_FILE"; then
  LINE_0A=$(grep -nE '^<!-- GATE0A:PROBLEM -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
  LINE_0B=$(grep -nE '^<!-- GATE0B:OUTCOME -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
  LINE_0B2=$(grep -nE '^<!-- GATE0B2:CLAIMS -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
  LINE_0C=$(grep -nE '^<!-- GATE0C:GAPS -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
  LINE_0D=$(grep -nE '^<!-- GATE0D:POLICY -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
  # Accept either GATE0E:ACTIONS or legacy GATE0D:ACTIONS
  LINE_0E=$(grep -nE '^<!-- GATE0E:ACTIONS -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
  [ -z "$LINE_0E" ] && LINE_0E=$(grep -nE '^<!-- GATE0D:ACTIONS -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)

  LINE_0F=$(grep -nE '^<!-- GATE0F:REVIEWED -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)

  if [ -n "$LINE_0A" ] && [ -n "$LINE_0B" ] && [ -n "$LINE_0C" ] && [ -n "$LINE_0D" ] && [ -n "$LINE_0E" ]; then
    if [ "$LINE_0A" -ge "$LINE_0B" ] || [ "$LINE_0B" -ge "$LINE_0C" ] || [ "$LINE_0C" -ge "$LINE_0D" ] || [ "$LINE_0D" -ge "$LINE_0E" ]; then
      MISSING+=("Gate 0 Ordering: Markers must appear in order: PROBLEM → OUTCOME → [CLAIMS →] GAPS → POLICY → ACTIONS → [REVIEWED]. Reorder the plan sections.")
    fi
    # If 0f exists, verify it sits after 0e
    if [ -n "$LINE_0F" ] && [ "$LINE_0E" -ge "$LINE_0F" ]; then
      MISSING+=("Gate 0 Ordering: REVIEWED marker must appear after ACTIONS.")
    fi
  fi

  # Additional: if 0B2 exists, verify it sits between 0B and 0C
  if [ -n "$LINE_0B2" ]; then
    if [ "$LINE_0B" -ge "$LINE_0B2" ] || [ "$LINE_0B2" -ge "$LINE_0C" ]; then
      MISSING+=("Gate 0 Ordering: CLAIMS marker must appear between OUTCOME and GAPS.")
    fi
  fi
fi

# Gate 0 coherence: cross-reference G# between gap table and actions table
if grep -qE '^<!-- GATE0C:GAPS -->$' "$PLAN_FILE" && grep -qE '^<!-- GATE0E:ACTIONS -->$' "$PLAN_FILE"; then
  # Extract G# from gap table column 1 (between OUTCOME and GAPS markers)
  GAP_IDS=$(sed -n '/^<!-- GATE0B:OUTCOME -->$/,/^<!-- GATE0C:GAPS -->$/p' "$PLAN_FILE" \
    | grep '^|' | grep -v '^| #' | grep -v '^|---' \
    | awk -F'|' '{print $2}' \
    | grep -oE 'G[0-9]+' | sort -u)

  # Extract gap refs from actions table "Addresses Gap" column (column 3)
  ACTIONS_END='GATE0E:ACTIONS'
  grep -qE '^<!-- GATE0E:ACTIONS -->$' "$PLAN_FILE" || ACTIONS_END='GATE0D:ACTIONS'
  ACTION_GAP_COL=$(sed -n '/^<!-- GATE0D:POLICY -->$/,/^<!-- '"$ACTIONS_END"' -->$/p' "$PLAN_FILE" \
    | grep '^|' | grep -v '^| Action' | grep -v '^|---' \
    | awk -F'|' '{print $3}')

  # Expand range notation (G1-G5 → G1 G2 G3 G4 G5) and discrete refs
  ACTION_GAP_REFS=$(echo "$ACTION_GAP_COL" | grep -oE 'G[0-9]+-G[0-9]+|G[0-9]+' | while read -r ref; do
    if echo "$ref" | grep -q -- '-'; then
      START_N=$(echo "$ref" | grep -oE '[0-9]+' | head -1)
      END_N=$(echo "$ref" | grep -oE '[0-9]+' | tail -1)
      for i in $(seq "$START_N" "$END_N"); do echo "G$i"; done
    else
      echo "$ref"
    fi
  done | sort -u)

  # Check: every gap has at least one action
  if [ -n "$GAP_IDS" ]; then
    for GID in $GAP_IDS; do
      if ! echo "$ACTION_GAP_REFS" | grep -q "^${GID}$"; then
        MISSING+=("Gate 0 Coherence: Gap $GID has no action addressing it. Every gap must have at least one coherent action.")
      fi
    done
  fi

  # Check: every action gap ref exists in gap table
  if [ -n "$ACTION_GAP_REFS" ]; then
    for AREF in $ACTION_GAP_REFS; do
      if ! echo "$GAP_IDS" | grep -q "^${AREF}$"; then
        MISSING+=("Gate 0 Coherence: Action references $AREF but no such gap exists in the gap table.")
      fi
    done
  fi
fi

# Gate 0 coherence: Claims↔Gaps bidirectional cross-reference (A3) + per-claim
# diagnosis-mapping presence (A6). Since plan-claim-single-source-of-truth
# (2026-07-18) the claim ids come from the ONE structured Outcome-Claims list
# (the fenced json under GATE0B2:CLAIMS, or — on the degraded escape path — the
# hand-authored markdown table alongside a claim_fallback_reason line), NOT from a
# re-worded second table. _plan_claim_gate.py reads the ids and enforces both
# directions (forward: every claim has a gap; reverse: no gap cites a phantom
# claim — the hole the old forward-only shell check missed) plus, on the
# structured path, a non-empty addresses_diagnosis + id on every claim. Standalone
# module (skill-location.md: pure check-logic + thin shell adapter).
if grep -qE '^<!-- GATE0B2:CLAIMS -->$' "$PLAN_FILE" && grep -qE '^<!-- GATE0C:GAPS -->$' "$PLAN_FILE"; then
  CLAIM_GATE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/_plan_claim_gate.py"
  if [ -f "$CLAIM_GATE" ]; then
    # stdout drives MISSING (byte-stable, unchanged). stderr carries the S3 additive
    # advisory attestation channel (_claim_attest.classify_provenance, via
    # _plan_claim_gate.py's `flags`) — captured SEPARATELY and surfaced only as
    # informational output. It is NEVER folded into MISSING and can never block
    # ExitPlanMode (A6/U3 — flag-only, never a gate).
    CG_ERR_FILE=$(mktemp)
    CG_OUT=$(python3 "$CLAIM_GATE" "$PLAN_FILE" 2>"$CG_ERR_FILE")
    CG_RC=$?
    CG_ERR=$(cat "$CG_ERR_FILE" 2>/dev/null)
    rm -f "$CG_ERR_FILE"
    if [ "$CG_RC" -ne 0 ] && [ -n "$CG_OUT" ]; then
      while IFS= read -r _cerr; do
        [ -n "$_cerr" ] && MISSING+=("$_cerr")
      done <<< "$CG_OUT"
    fi
    if [ -n "$CG_ERR" ]; then
      while IFS= read -r _cadv; do
        [ -n "$_cadv" ] && echo "INFO (Gate 0b2 attestation): $_cadv" >&2
      done <<< "$CG_ERR"
    fi
  fi
fi

# Gate 1: Implementation Verification
if ! grep -qE '^<!-- GATE1:VERIFIED -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 1 (Implementation Verification): Add <!-- GATE1:VERIFIED --> after verifying each recommendation against its documentation source.")
fi

# Gate 1 content check: every claim row must have a verification tag
if grep -qE '^<!-- GATE1:START -->$' "$PLAN_FILE" && grep -qE '^<!-- GATE1:END -->$' "$PLAN_FILE"; then
  CLAIM_ROWS=$(sed -n '/^<!-- GATE1:START -->$/,/^<!-- GATE1:END -->$/p' "$PLAN_FILE" \
    | grep '^|' | grep -v '^| Claim' | grep -v '^|---')
  ROW_COUNT=$(echo "$CLAIM_ROWS" | grep -c '|' || true)
  TAGGED_COUNT=$(echo "$CLAIM_ROWS" | grep -cE '\[(verified|unverified|partially verified)' || true)

  if [ "$ROW_COUNT" -gt 0 ] && [ "$TAGGED_COUNT" -lt "$ROW_COUNT" ]; then
    MISSING+=("Gate 1 Content: $((ROW_COUNT - TAGGED_COUNT)) of $ROW_COUNT claims lack a [verified:] or [unverified] or [partially verified] tag.")
  fi

  # Check that [verified:] tags cite real sources (URL domain or evidence keyword)
  # Extract ONLY the tag content to avoid matching keywords in other table cells
  REGISTRY_DOMAINS="code\.claude\.com|docs\.anthropic\.com|platform\.claude\.com"
  EVIDENCE_KEYWORDS="file read|production|tested|existing|grep|confirmed|read .* line|source file|code|corrected|checked|verified agent|fact-check|sonnet|principle|rule exists|grounded|\\.[a-z]{2,4}\\]"

  BAD_COUNT=0
  while IFS= read -r ROW; do
    TAG=$(echo "$ROW" | grep -oE '\[(verified|partially verified):[^]]+\]' || true)
    [ -z "$TAG" ] && continue
    # Special-case the EXACT documented placeholder from plan-gates.md (the rule
    # instructs authors to write it verbatim). Only the exact literal passes —
    # the bare word "artifact" is NOT whitelisted, so an embedded use such as
    # "[verified: an artifact of X]" still fails (no fabrication loophole).
    [ "$TAG" = '[verified: artifact]' ] && continue
    if ! echo "$TAG" | grep -qiE "($REGISTRY_DOMAINS|$EVIDENCE_KEYWORDS)"; then
      BAD_COUNT=$((BAD_COUNT + 1))
    fi
  done <<< "$CLAIM_ROWS"

  if [ "$BAD_COUNT" -gt 0 ]; then
    MISSING+=("Gate 1 Sources: $BAD_COUNT claims cite unrecognized sources. Status tag must contain: a file name (e.g. fundamentals.py), a registry domain (code.claude.com, docs.anthropic.com), or an evidence keyword (file read, production, tested, existing, confirmed, checked, grounded).")
  fi

  # Gate 1 source location: require 6 pipe-delimited columns
  # | Claim | Category | Source Location | Verified Against | Status |
  if [ -n "$CLAIM_ROWS" ] && [ "$ROW_COUNT" -gt 0 ]; then
    SHORT_ROWS=0
    while IFS= read -r ROW; do
      [ -z "$ROW" ] && continue
      COL_COUNT=$(echo "$ROW" | awk -F'|' '{print NF}')
      # 6 data columns + leading/trailing empty = NF >= 7
      if [ "$COL_COUNT" -lt 7 ]; then
        SHORT_ROWS=$((SHORT_ROWS + 1))
      fi
    done <<< "$CLAIM_ROWS"

    if [ "$SHORT_ROWS" -gt 0 ]; then
      MISSING+=("Gate 1 Source Location: $SHORT_ROWS rows missing columns. Required: | Claim | Category | Source Location | Verified Against | Status |")
    fi
  fi

  # Gate 1 source grounding: verify cited evidence against actual files
  # For each row where Source Location contains a file path, check that
  # the quoted snippet in Verified Against actually exists in that file.
  CWD=$(pwd)
  UNGROUNDED=0
  while IFS= read -r ROW; do
    # Skip unverified rows
    echo "$ROW" | grep -qi 'unverified' && continue

    # Extract Source Location (column 4) and Verified Against (column 5)
    SRC_COL=$(echo "$ROW" | awk -F'|' '{print $4}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    VA_COL=$(echo "$ROW" | awk -F'|' '{print $5}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    [ -z "$SRC_COL" ] || [ -z "$VA_COL" ] && continue

    # Extract file path from Source Location (backtick-enclosed paths)
    FILE_PATH=$(echo "$SRC_COL" | grep -oE '`[^`]+\.(sh|md|json|py|yaml|yml|conf|toml)`' | head -1 | tr -d '`')
    [ -z "$FILE_PATH" ] && continue

    # Resolve relative to CWD
    if [ ! -f "$FILE_PATH" ]; then
      RESOLVED="$CWD/$FILE_PATH"
      [ -f "$RESOLVED" ] && FILE_PATH="$RESOLVED"
    fi
    if [ ! -f "$FILE_PATH" ]; then
      # A2: surface the unresolvable cited path instead of skipping silently.
      # Report-only: NOT counted as ungrounded and does NOT block (preserves the
      # prior pass/skip behavior); visible in --dry-run and in an informational note.
      GROUNDING_DETAILS+=("$FILE_PATH :: UNRESOLVED PATH (not found from CWD=$CWD)")
      continue
    fi

    # Check 1: Line reference — "line N" → verify snippet near that line
    LINE_REF=$(echo "$VA_COL" | grep -oE 'line [0-9]+' | head -1 | grep -oE '[0-9]+')
    if [ -n "$LINE_REF" ]; then
      SNIPPET=$(echo "$VA_COL" | grep -oE '`[^`]{8,}`' | head -1 | tr -d '`')
      if [ -n "$SNIPPET" ]; then
        START=$((LINE_REF - 5)); [ "$START" -lt 1 ] && START=1
        END=$((LINE_REF + 5))
        if ! sed -n "${START},${END}p" "$FILE_PATH" | grep -qF -- "$SNIPPET"; then
          UNGROUNDED=$((UNGROUNDED + 1))
          GROUNDING_DETAILS+=("$FILE_PATH :: snippet=\`$SNIPPET\` (near line $LINE_REF) :: $(infer_reason "$SNIPPET" "$FILE_PATH" "$LINE_REF")")
        fi
      fi
      continue
    fi

    # Check 2: Quoted snippet without line ref — verify it exists anywhere in file
    SNIPPET=$(echo "$VA_COL" | grep -oE '`[^`]{8,}`' | head -1 | tr -d '`')
    if [ -n "$SNIPPET" ]; then
      if ! grep -qF -- "$SNIPPET" "$FILE_PATH"; then
        UNGROUNDED=$((UNGROUNDED + 1))
        GROUNDING_DETAILS+=("$FILE_PATH :: snippet=\`$SNIPPET\` :: $(infer_reason "$SNIPPET" "$FILE_PATH" "")")
      fi
      continue
    fi

    # No line ref and no quoted snippet for a file-path source — shallow evidence
    UNGROUNDED=$((UNGROUNDED + 1))
    GROUNDING_DETAILS+=("$SRC_COL :: no backtick snippet (>=8 chars) in 'Verified Against' :: no reason inferred (add a line number + a backtick snippet from the cited file)")
  done <<< "$CLAIM_ROWS"

  if [ "$UNGROUNDED" -gt 0 ]; then
    _GDETAIL=""
    for _d in "${GROUNDING_DETAILS[@]}"; do
      # The report-only UNRESOLVED notes are surfaced separately (below), not here.
      case "$_d" in *"UNRESOLVED PATH"*) continue ;; esac
      _GDETAIL="$_GDETAIL"$'\n'"      - $_d"
    done
    MISSING+=("Gate 1 Source Grounding: $UNGROUNDED claim(s) have unverifiable evidence (each 'Verified Against' citing a file needs a line number + a backtick snippet that EXISTS in the cited file). Failing rows:$_GDETAIL")
  fi

  # Gate 1 numeric coverage: check that body numerics appear in Gate 1 table

  # A1: Extract narrative body text with line numbers, excluding:
  #   - Gate table zones (GATE1/2/3:START to END)
  #   - Fenced code blocks (``` to ```)
  #   - Verification section (## Verification to next ## heading)
  #   - Markdown table rows (^|) — tables contain sourced evidence with inline tags
  BODY_TEXT=$(awk '
    /^```/        { in_code = !in_code; next }
    in_code       { next }
    /<!-- GATE[123]:START -->/ { in_gate = 1; next }
    /<!-- GATE[123]:END -->/   { in_gate = 0; next }
    in_gate       { next }
    /^## Verification/ { in_verif = 1; next }
    in_verif && /^## / { in_verif = 0 }
    in_verif      { next }
    /^\|/         { next }
    { print NR ":" $0 }
  ' "$PLAN_FILE")

  # A2: Extract lines containing numeric tokens with financial context
  # NOTE: no -n flag — BODY_TEXT already has NR: prefix from awk
  NUMERIC_LINES=$(echo "$BODY_TEXT" | grep -E '\$[0-9,.]+[MBKmk]?|\b[0-9,.]+%|[0-9,.]+x\b|[0-9,.]+[MBK] shares' || true)

  # A3: Filter out editorial lines
  EDITORIAL_PATTERN='(^[0-9]+:.*\blines? [0-9]|[0-9]+ tests?\b|[0-9]+ LOC\b|\bSection [0-9]|\bPhase [0-9]|\bGate [0-9]|\bRound [0-9]|\b202[0-9]-[0-9]|\bv[0-9]+\b|[0-9]+ (file|row|quer|module|function|import|call))'
  FILTERED=$(echo "$NUMERIC_LINES" | grep -vE "$EDITORIAL_PATTERN" || true)

  # A4: Find NUMERIC:EDITORIAL markers → exempt next non-empty line
  EXEMPT_LINES=""
  while IFS= read -r MARKER_LINE; do
    [ -z "$MARKER_LINE" ] && continue
    MARKER_NUM=$(echo "$MARKER_LINE" | grep -oE '^[0-9]+')
    # Find next non-empty line number after marker
    NEXT_LINE=$(echo "$BODY_TEXT" | awk -F: -v start="$MARKER_NUM" '
      $1 > start && $2 !~ /^[[:space:]]*$/ { print $1; exit }
    ')
    [ -n "$NEXT_LINE" ] && EXEMPT_LINES="$EXEMPT_LINES $NEXT_LINE"
  done < <(echo "$BODY_TEXT" | grep '<!-- NUMERIC:EDITORIAL -->' || true)

  # A5: Extract Gate 1 table text (all content between markers)
  GATE1_TEXT=$(sed -n '/^<!-- GATE1:START -->$/,/^<!-- GATE1:END -->$/p' "$PLAN_FILE")

  # Check each non-exempt numeric line
  UNMATCHED_NUMERICS=()
  while IFS= read -r LINE; do
    [ -z "$LINE" ] && continue
    LINE_NUM=$(echo "$LINE" | cut -d: -f1)

    # Skip if line is in exempt list (from NUMERIC:EDITORIAL markers)
    if echo "$EXEMPT_LINES" | grep -qw "$LINE_NUM"; then
      continue
    fi

    # Extract the numeric tokens from this line
    TOKENS=$(echo "$LINE" | grep -oE '\$[0-9,.]+[MBKmk]?|[0-9,.]+%|[0-9,.]+x\b|[0-9,.]+[MBK] shares' || true)
    while IFS= read -r TOKEN; do
      [ -z "$TOKEN" ] && continue
      # Check if token appears anywhere in Gate 1 table
      # For "NM shares" tokens, also try without " shares" suffix
      if ! echo "$GATE1_TEXT" | grep -qF -- "$TOKEN"; then
        SHORT_TOKEN="${TOKEN% shares}"
        if [ "$SHORT_TOKEN" = "$TOKEN" ] || ! echo "$GATE1_TEXT" | grep -qF -- "$SHORT_TOKEN"; then
          UNMATCHED_NUMERICS+=("line $LINE_NUM: $TOKEN")
        fi
      fi
    done <<< "$TOKENS"
  done <<< "$FILTERED"

  # A6: Report unmatched numerics
  if [ ${#UNMATCHED_NUMERICS[@]} -gt 0 ]; then
    DETAIL=""
    for item in "${UNMATCHED_NUMERICS[@]}"; do
      DETAIL="$DETAIL\n    $item"
    done
    MISSING+=("Gate 1 Numeric Coverage: ${#UNMATCHED_NUMERICS[@]} numeric claims in plan body have no matching Gate 1 verification row. Either add a Gate 1 row containing the number, or place <!-- NUMERIC:EDITORIAL --> on the line before the number. Unmatched:$DETAIL")
  fi

fi

# Gate 2: Code vs AI Boundary
if ! grep -qE '^<!-- GATE2:BOUNDARIES -->$' "$PLAN_FILE"; then
  MISSING+=("Gate 2 (Code vs AI Boundary): Add <!-- GATE2:BOUNDARIES --> after classifying each step as Code, AI, or FLAGGED.")
fi

# Gate 2 content validation
if grep -qE '^<!-- GATE2:START -->$' "$PLAN_FILE" && grep -qE '^<!-- GATE2:END -->$' "$PLAN_FILE"; then
  G2_ROWS=$(sed -n '/^<!-- GATE2:START -->$/,/^<!-- GATE2:END -->$/p' "$PLAN_FILE" \
    | grep '^|' | grep -v '^| Step' | grep -v '^|---')
  G2_ROW_COUNT=$(echo "$G2_ROWS" | grep -c '|' || true)

  if [ "$G2_ROW_COUNT" -eq 0 ]; then
    MISSING+=("Gate 2 Content: No classification rows between GATE2:START and GATE2:END.")
  fi

  # FLAGGED items must be resolved — count ONLY the Type column (col 3), not the
  # whole row, so the word "FLAGGED" in a Step or Enforcement description does
  # not false-trip (mirrors the Code-row Type-column check below).
  FLAGGED_COUNT=0
  while IFS= read -r ROW; do
    [ -z "$ROW" ] && continue
    echo "$ROW" | awk -F'|' '{print $3}' | grep -qi 'FLAGGED' && FLAGGED_COUNT=$((FLAGGED_COUNT + 1))
  done <<< "$G2_ROWS"
  if [ "$FLAGGED_COUNT" -gt 0 ] && ! grep -qE '^<!-- GATE2:USER_APPROVED -->$' "$PLAN_FILE"; then
    MISSING+=("Gate 2 FLAGGED: $FLAGGED_COUNT steps marked FLAGGED. Convert to Code or add <!-- GATE2:USER_APPROVED -->.")
  fi

  # Code rows must name enforcement mechanism
  CODE_KEYWORDS="function|hook|script|test|assert|DDL|migration|conditional|check|validate|regex|query|pytest|grep|comparison"
  CODE_BAD=0
  while IFS= read -r ROW; do
    # Check Type column (column 3) for "Code"
    TYPE_COL=$(echo "$ROW" | awk -F'|' '{print $3}')
    echo "$TYPE_COL" | grep -qi 'Code' || continue
    # Check Enforcement column (column 4) for mechanism keyword
    ENFORCE_COL=$(echo "$ROW" | awk -F'|' '{print $4}')
    if ! echo "$ENFORCE_COL" | grep -qiE "($CODE_KEYWORDS)"; then
      CODE_BAD=$((CODE_BAD + 1))
    fi
  done <<< "$G2_ROWS"

  if [ "$CODE_BAD" -gt 0 ]; then
    MISSING+=("Gate 2 Enforcement: $CODE_BAD Code rows lack a named mechanism keyword. Must contain one of: function, hook, script, test, assert, DDL, migration, conditional, check, validate, regex, query, pytest, grep, comparison.")
  fi
fi

# Design Review
if ! grep -qE '^<!-- GATE2B:DESIGN_REVIEW -->$' "$PLAN_FILE"; then
  MISSING+=("Design Review: Add <!-- GATE2B:DESIGN_REVIEW --> after reviewing the full design for chain integrity, outcome delivery, and interaction effects.")
fi

# Gate 3: Claim Verification — three-track system
HAS_GATE3_OK=false
HAS_GATE3_NO_CLAIMS=false
HAS_GATE3_INTERNAL=false

grep -qE '^<!-- GATE3:CLAIMS_OK -->$' "$PLAN_FILE" && HAS_GATE3_OK=true
grep -qE '^<!-- GATE3:NO_CLAIMS -->$' "$PLAN_FILE" && HAS_GATE3_NO_CLAIMS=true
grep -qE '^<!-- GATE3:INTERNAL_ONLY -->$' "$PLAN_FILE" && HAS_GATE3_INTERNAL=true

if [ "$HAS_GATE3_OK" = false ] && [ "$HAS_GATE3_NO_CLAIMS" = false ] && [ "$HAS_GATE3_INTERNAL" = false ]; then
  MISSING+=("Gate 3: Add one of: <!-- GATE3:CLAIMS_OK --> (external claims verified), <!-- GATE3:INTERNAL_ONLY --> (internal claims with file evidence), or <!-- GATE3:NO_CLAIMS --> (no verifiable claims).")
fi

# INTERNAL_ONLY track: must have GATE3:START section with at least one row
if [ "$HAS_GATE3_INTERNAL" = true ]; then
  if grep -qE '^<!-- GATE3:START -->$' "$PLAN_FILE"; then
    G3_ROW_COUNT=$(sed -n '/^<!-- GATE3:START -->$/,/^<!-- GATE3:END -->$/p' "$PLAN_FILE" \
      | grep '^|' | grep -v '^| Claim' | grep -v '^|---' | grep -c '|' || true)
    if [ "$G3_ROW_COUNT" -eq 0 ]; then
      MISSING+=("Gate 3 Internal: GATE3:INTERNAL_ONLY requires at least one verification row in the GATE3:START section.")
    fi
  else
    MISSING+=("Gate 3 Internal: GATE3:INTERNAL_ONLY present but no GATE3:START section. Add a verification table with file-backed evidence.")
  fi
fi

# Gate 3 circuit breaker: check round count if active
# Canon: 2-round-then-escalate (factcheck-convergence.md §4; engine default FACTCHECK_MAX_ROUNDS=2; plan kind max_rounds=3)
if grep -qE '^<!-- GATE3:START -->$' "$PLAN_FILE"; then
  # Scope the round count to the Gate-3 zone, and prefer the structural
  # `### Round N` headers (the real convergence-round markers) over any prose
  # "Round N" elsewhere. Fallback to zone-scoped prose only when no headers
  # exist. Prevents a "Round N" in editorial prose — or in a plan ABOUT this
  # checker — from inflating the circuit-breaker.
  GATE3_SECTION=$(sed -n '/^<!-- GATE3:START -->$/,/^<!-- GATE3:END -->$/p' "$PLAN_FILE")
  HIGHEST_ROUND=$(echo "$GATE3_SECTION" | grep -oE '^### Round [0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1)
  [ -z "$HIGHEST_ROUND" ] && HIGHEST_ROUND=$(echo "$GATE3_SECTION" | grep -oE 'Round [0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1)

  if [ -n "$HIGHEST_ROUND" ]; then
    # After round 2: require root cause analysis — but only when the last round did NOT converge.
    # Normal flow: R1 DIRTY → R2 PASS is expected; ROOT_CAUSE not needed.
    # Problematic flow: R2 DIRTY → escalation needed; ROOT_CAUSE required.
    GATE3_CONVERGED=false
    echo "$GATE3_SECTION" | grep -qiE 'Aggregated.*PASS|Convergence.*PASS|verdict.*PASS' && GATE3_CONVERGED=true
    if [ "$HIGHEST_ROUND" -ge 2 ] && [ "$GATE3_CONVERGED" = false ] && ! grep -qE '^<!-- GATE3:ROOT_CAUSE -->$' "$PLAN_FILE"; then
      MISSING+=("Gate 3 Circuit Breaker: Round $HIGHEST_ROUND reached without convergence. Add <!-- GATE3:ROOT_CAUSE --> with root-cause analysis of why claims aren't converging.")
    fi

    # After round 3: hard stop (engine escalates after max_rounds; plan kind uses max_rounds=3)
    if [ "$HIGHEST_ROUND" -ge 4 ] && [ "$HAS_GATE3_OK" = false ]; then
      echo "HARD STOP: Gate 3 reached Round $HIGHEST_ROUND without convergence." >&2
      echo "Plan file: $PLAN_FILE" >&2
      echo "Canon is 2-round-then-escalate (factcheck-convergence.md §4); plan kind override: max_rounds=3." >&2
      echo "Steps:" >&2
      echo "  1. Review the root-cause analysis" >&2
      echo "  2. Remove or rewrite unverifiable claims" >&2
      echo "  3. Use <!-- GATE3:NO_CLAIMS --> if remaining claims are internal/observable" >&2
      exit 2
    fi
  fi
fi

# Gate 3 convergence quality: 2 full rounds with 3 checkers before CLAIMS_OK
# Only applies to CLAIMS_OK track (not INTERNAL_ONLY or NO_CLAIMS)
if [ "$HAS_GATE3_OK" = true ] && [ "$HAS_GATE3_NO_CLAIMS" = false ] && [ "$HAS_GATE3_INTERNAL" = false ]; then
  if grep -qE '^<!-- GATE3:START -->$' "$PLAN_FILE"; then
    ROUND_SECTION=$(sed -n '/^<!-- GATE3:START -->$/,/^<!-- GATE3:END -->$/p' "$PLAN_FILE")
    TOTAL_ROUNDS=$(echo "$ROUND_SECTION" | grep -cE '^### Round [0-9]+' || true)

    if [ "$TOTAL_ROUNDS" -lt 2 ]; then
      MISSING+=("Gate 3 Convergence: Requires at least 2 verification rounds before CLAIMS_OK. Found $TOTAL_ROUNDS.")
    else
      # Rounds 1-2 must not be diff-only
      for RN in 1 2; do
        HEADER=$(echo "$ROUND_SECTION" | grep -E "^### Round ${RN}[^0-9]" || true)
        if echo "$HEADER" | grep -qi 'diff-only'; then
          MISSING+=("Gate 3 Convergence: Round $RN must be a full check, not diff-only. Diff-only allowed from Round 3 onward.")
        fi
      done

      # Rounds 1-2 must show 3 checkers.
      # Primary: parse R<N>.md engine artifact verdict from plan_validation state dir.
      # Fallback: prose-pattern heuristic (Agent A/B/C or Checker #1/#2/#3) for plans
      #           authored without engine (preserves backward compatibility).
      for RN in 1 2; do
        NEXT_RN=$((RN + 1))
        # Extract section from "Round N" to "Round N+1" or GATE3:END using awk
        R_SECTION=$(echo "$ROUND_SECTION" | awk "/^### Round ${RN}[^0-9]/{found=1} found{print} found && /^### Round ${NEXT_RN}[^0-9]|<!-- GATE3:END/{if(NR>1)exit}")
        CHECKER_COUNT=0

        # Primary path: look for engine R<N>.md artifact
        # State dir: ~/.claude/state/plan_validation/<proj>/<topic>/plan/R<N>.md
        # Resolved via session active state; falls through to fallback if unavailable.
        ACTIVE_JSON="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/state/pre_plan_gates/_active.json"
        R_ARTIFACT=""
        if [ -f "$ACTIVE_JSON" ] && command -v python3 >/dev/null 2>&1; then
          SESSION_ID_FROM_ENV="${CLAUDE_SESSION_ID:-}"
          if [ -n "$SESSION_ID_FROM_ENV" ]; then
            R_ARTIFACT=$(python3 - "$ACTIVE_JSON" "$SESSION_ID_FROM_ENV" "$RN" 2>/dev/null <<'PYEOF'
import sys, json, os, pathlib
active_json, session_id, rn = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    active = json.loads(pathlib.Path(active_json).read_text())
    sess = active.get(session_id, {})
    proj = sess.get("topic_slug", "")
    topic = sess.get("active_project", "")
    if proj and topic:
        p = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR")
                 or (pathlib.Path.home() / ".claude")) / "state/plan_validation" / proj / topic / "plan" / f"R{rn}.md"
        print(str(p))
except Exception:
    pass
PYEOF
            )
          fi
        fi

        if [ -n "$R_ARTIFACT" ] && [ -f "$R_ARTIFACT" ]; then
          # Parse verdict and checker_count from frontmatter
          ARTIFACT_VERDICT=$(awk '/^---$/{n++; next} n==1 && /^verdict:/{print $2; exit}' "$R_ARTIFACT")
          ARTIFACT_CHECKERS=$(awk '/^---$/{n++; next} n==1 && /^checker_count:/{print $2; exit}' "$R_ARTIFACT")
          if [ -n "$ARTIFACT_CHECKERS" ]; then
            CHECKER_COUNT="$ARTIFACT_CHECKERS"
          fi
          # For Round 1 or Round 2 that is the final round: require PASS verdict
          if [ "$RN" -eq 2 ] && [ "$ARTIFACT_VERDICT" != "PASS" ] && [ "$ARTIFACT_VERDICT" != "DIRTY" ]; then
            MISSING+=("Gate 3 Convergence: R${RN}.md has verdict='$ARTIFACT_VERDICT'. Expected PASS or DIRTY for an in-progress round.")
          fi
        else
          # Fallback: prose-pattern heuristic (no engine artifact available)
          echo "$R_SECTION" | grep -qiE 'Agent A|Checker.?#?1|Checker [Oo]ne' && CHECKER_COUNT=$((CHECKER_COUNT + 1))
          echo "$R_SECTION" | grep -qiE 'Agent B|Checker.?#?2|Checker [Tt]wo' && CHECKER_COUNT=$((CHECKER_COUNT + 1))
          echo "$R_SECTION" | grep -qiE 'Agent C|Checker.?#?3|Checker [Tt]hree' && CHECKER_COUNT=$((CHECKER_COUNT + 1))
          # Accept "3/3" as legacy fallback evidence
          echo "$R_SECTION" | grep -qE '3/3' && CHECKER_COUNT=3
        fi

        if [ "$CHECKER_COUNT" -lt 3 ] 2>/dev/null; then
          MISSING+=("Gate 3 Convergence: Round $RN must show 3 independent checkers (Checker #1/#2/#3 or Agent A/B/C). Found evidence of $CHECKER_COUNT.")
        fi
      done
    fi
  else
    MISSING+=("Gate 3 Convergence: GATE3:CLAIMS_OK present but no GATE3:START section. Add verification rounds.")
  fi
fi

# ---------------------------------------------------------------------------
# Slice I (S-I-Impl-1): new Gate 0 surface — opt-in via GATE0:CHAIN_SRC
#
# Backward compat: when <!-- GATE0:CHAIN_SRC --> is absent, none of the
# Slice I gates fire. Plans authored before Slice I behave under legacy
# rules unchanged.
#
# Per-mode required-set (when CHAIN_SRC present):
#   Mode A,B: 0B2:VALIDATED, 0C:VALIDATED, GATE0SR:SLICES, GATE0G:COHERENCY
#   Mode C  : 0A:VALIDATED + the same Mode-A/B set
# ---------------------------------------------------------------------------

HAS_CHAIN_SRC=false
grep -qE '^<!-- GATE0:CHAIN_SRC -->$' "$PLAN_FILE" && HAS_CHAIN_SRC=true

_block_after_marker() {
  # Extract the block of lines after a marker, up to the next HTML comment.
  awk -v marker="$1" '
    $0 == marker { found=1; next }
    found && /^<!--/ { exit }
    found { print }
  ' "$PLAN_FILE"
}

_line_of_marker() {
  grep -nE "^$1$" "$PLAN_FILE" | head -1 | cut -d: -f1
}

# ─────────────────────────────────────────────────────────────────────────────
# plan-validation-engine-consumer (S2): read the code-written engine RECEIPT for a
# Slice-I gate instead of trusting an author-typed `verdict:` line. Mirrors the
# Gate-3 R<N>.md reader above (lines ~561-597) — the mechanism that already closes
# this hole for Gate 3, now applied to the 0A/0B2/0C/0G markers.
#
# `_plan_receipt_verdict GATE` echoes exactly one of:
#   PASS | DIRTY | ESCALATE | INCOMPLETE   — the latest round's engine verdict
#   OVERRIDE                                — an explicit, recorded operator override
#   MISSING                                 — no receipt (fail-closed → the gate blocks)
# The receipt DIR is resolved by the same code that writes it (the `plan-receipt-dir`
# CLI verb) so the shell never re-implements the path hash. Fail-closed: any error,
# missing tool, or absent receipt yields MISSING.
# ─────────────────────────────────────────────────────────────────────────────
PPG_PY="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/pre_plan_gates.py"

_plan_receipt_verdict() {
  local gate="$1" rdir latest n=0 f num verdict
  command -v python3 >/dev/null 2>&1 || { echo "MISSING"; return; }
  [ -f "$PPG_PY" ] || { echo "MISSING"; return; }
  rdir=$(python3 "$PPG_PY" plan-receipt-dir "$PLAN_FILE" "$gate" 2>/dev/null)
  [ -n "$rdir" ] && [ -d "$rdir" ] || { echo "MISSING"; return; }
  # An explicit recorded operator override wins over the (possibly non-PASS) receipt.
  [ -f "$rdir/OVERRIDE" ] && { echo "OVERRIDE"; return; }
  # Pick the highest-numbered R<N>.md (the latest round).
  latest=""
  for f in "$rdir"/R*.md; do
    [ -e "$f" ] || continue
    num=$(basename "$f" .md); num=${num#R}
    case "$num" in ''|*[!0-9]*) continue ;; esac
    if [ "$num" -ge "$n" ]; then n="$num"; latest="$f"; fi
  done
  [ -n "$latest" ] || { echo "MISSING"; return; }
  verdict=$(awk '/^---$/{c++; next} c==1 && /^verdict:/{print $2; exit}' "$latest")
  [ -n "$verdict" ] || verdict="MISSING"
  echo "$verdict"
}

# _require_receipt_pass GATE HUMAN_NAME EXTRA_HINT — append a MISSING entry unless
# the gate's engine receipt is a converged PASS (or a recorded operator override).
_require_receipt_pass() {
  local gate="$1" human="$2" hint="$3" v
  v=$(_plan_receipt_verdict "$gate")
  if [ "$v" != "PASS" ] && [ "$v" != "OVERRIDE" ]; then
    MISSING+=("Gate ${gate}: no converged engine PASS receipt for the ${human} — found '${v}'. The verdict must come from the shared validation engine (${hint}), not an author-typed line; a non-PASS does not unlock approval. Re-run the dispatch to PASS, or record an explicit operator override (pre_plan_gates.py plan-override ${gate} <plan> --reason ...).")
  fi
}

# ─────────────────────────────────────────────────────────────────────────────
# Gate 0b2 — single structured Outcome-Claims list (plan-claim-single-source-of-truth,
# 2026-07-18). The `## Outcome Claims` section (GATE0B:OUTCOME → GATE0B2:CLAIMS) carries
# EITHER a fenced ```json claim-set (the Deep engine's output — the ONE source of truth;
# no separate GATE0B2:CLAIM_SET marker, no re-worded second table) OR a reason-logged
# `claim_fallback_reason:` line (the retained degraded escape). Keyed on the
# unconditionally-mandatory GATE0B2:CLAIMS marker (checked ~line 90): every plan that
# reaches Outcome Claims must carry exactly one of the two. The json is schema + DEEP
# validated through the SAME shared seam (_claim_persist.parse_claim_set, --site plan:0b2)
# so "the engine ran at DEEP" is enforced in code (code_first_architecture.md), and the
# success/fallback paths both leave a site-keyed .claim-runs.md receipt (tracking parity
# with clarification / extract-knowledge). Claims↔Gaps bidirectional + per-claim
# addresses_diagnosis presence are enforced by _plan_claim_gate.py (near line 190).
# ─────────────────────────────────────────────────────────────────────────────
if grep -qE '^<!-- GATE0B2:CLAIMS -->$' "$PLAN_FILE"; then
  CLAIM_PERSIST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/_claim_persist.py"
  # The Outcome Claims section = strictly between GATE0B:OUTCOME and GATE0B2:CLAIMS.
  OC_SECTION=$(sed -n '/^<!-- GATE0B:OUTCOME -->$/,/^<!-- GATE0B2:CLAIMS -->$/p' "$PLAN_FILE")
  if printf '%s\n' "$OC_SECTION" | grep -qE '^claim_fallback_reason:[[:space:]]*[^[:space:]]'; then
    # Reason-logged DEFAULT fallback (retained escape). The reason must pass the SHARED
    # fallback-reason grammar (dc_obligation.validate_fallback_reason): a concrete
    # engine-failure category (engine-error:/zero-claims:/timeout:/plan-mode-seam-blocked:)
    # OR the explicit override 'user-acknowledged-skip:'. A bare elective skip is REFUSED.
    # On a VALID reason: accept AND record a fallback run-record (best-effort; never a
    # silent skip — the fact-check BYPASSED pattern).
    DC_OBLIGATION="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/dc_obligation.py"
    FB_REASON=$(printf '%s\n' "$OC_SECTION" | sed -n 's/^claim_fallback_reason:[[:space:]]*//p' | head -1)
    if [ -f "$DC_OBLIGATION" ]; then
      FB_ERR=$(printf '%s' "$FB_REASON" | python3 "$DC_OBLIGATION" validate-fallback-reason 2>&1 1>/dev/null)
      FB_RC=$?
    else
      # Validator module absent — fail-open on grammar (do not block on a missing tool).
      FB_ERR=""
      FB_RC=0
    fi
    if [ "$FB_RC" -ne 0 ]; then
      MISSING+=("Gate 0b2 (fallback): claim_fallback_reason must name a concrete engine-failure category (engine-error:/zero-claims:/timeout:/plan-mode-seam-blocked:) or the explicit override 'user-acknowledged-skip:'. ${FB_ERR}")
    elif [ -n "$FB_REASON" ] && [ -f "$CLAIM_PERSIST" ]; then
      python3 "$CLAIM_PERSIST" --fallback-reason "$FB_REASON" \
        --runs-sidecar "${PLAN_FILE%.md}.claim-runs.md" --site plan:0b2 --dedup-runs \
        >/dev/null 2>&1 || true
    fi
  else
    # Success path: the single structured claim list is the fenced ```json in the section.
    CLAIM_JSON=$(printf '%s\n' "$OC_SECTION" | awk '/^```json$/{f=1;next} /^```$/{f=0} f')
    if [ -z "$(printf '%s' "$CLAIM_JSON" | tr -d '[:space:]')" ]; then
      MISSING+=("Gate 0b2 (single list): GATE0B2:CLAIMS present but the ## Outcome Claims section carries neither a fenced \`\`\`json claim-set nor a non-empty 'claim_fallback_reason: <why>' line. Emit the Deep engine's claim-set as ONE fenced json list (the single source of truth), or record a fallback reason. See ~/.claude/skills/plan/SKILL.md Step 5b.")
    elif [ ! -f "$CLAIM_PERSIST" ]; then
      MISSING+=("Gate 0b2 (single list): _claim_persist.py not found at $CLAIM_PERSIST — cannot schema-validate the claim-set.")
    elif ! CLAIM_ERR=$(printf '%s' "$CLAIM_JSON" | python3 "$CLAIM_PERSIST" - \
            --runs-sidecar "${PLAN_FILE%.md}.claim-runs.md" --site plan:0b2 --dedup-runs \
            2>&1 1>/dev/null); then
      # On success, the same call appends the manageable run-record to the plan's
      # co-located .claim-runs.md (dedup collapses the permission+stop double-fire).
      MISSING+=("Gate 0b2 (single list): claim-set failed schema validation at the seam (_claim_persist.parse_claim_set): ${CLAIM_ERR}")
    fi
    # (Claims↔Gaps bidirectional + per-claim addresses_diagnosis/id: _plan_claim_gate.py.)
  fi
fi

if [ "$HAS_CHAIN_SRC" = true ]; then
  # ----- CHAIN_SRC ordering: must precede GATE0A -----
  LINE_CHAIN=$(_line_of_marker '<!-- GATE0:CHAIN_SRC -->')
  if [ -n "$LINE_CHAIN" ] && [ -n "$LINE_0A" ] && [ "$LINE_CHAIN" -ge "$LINE_0A" ]; then
    MISSING+=("Gate 0 CHAIN_SRC: marker must appear BEFORE GATE0A:PROBLEM.")
  fi

  # ----- CHAIN_SRC payload: mode ∈ {A,B,C} first (needed to mode-branch the hash) -----
  CHAIN_BLOCK=$(_block_after_marker '<!-- GATE0:CHAIN_SRC -->')
  if ! echo "$CHAIN_BLOCK" | grep -qE 'mode:[[:space:]]*[ABC]\b'; then
    MISSING+=("Gate 0 CHAIN_SRC: missing or invalid mode (must be A, B, or C).")
  fi
  CHAIN_MODE=$(echo "$CHAIN_BLOCK" | grep -oE 'mode:[[:space:]]*[ABC]' | head -1 | awk '{print $NF}')

  # ----- discovery_src_hash: strict 12-hex for A/B; Mode C also accepts "n/a" -----
  # Mode C (Ninja-Plan) has no upstream Discovery to fingerprint, so it may declare
  # `discovery_src_hash: n/a` (mirrors `thought_file: n/a`; see plan-gates.md). A/B keep
  # strict 12-hex so the provenance-anchor guarantee is preserved. The Mode-C branch is
  # end-anchored so "n/an/a"/trailing junk cannot slip through. The hash is a shape-check
  # with no downstream consumer for Mode C.
  if [ "$CHAIN_MODE" = "C" ]; then
    CHAIN_HASH_RE='discovery_src_hash:[[:space:]]*([0-9a-f]{12}|n/a)[[:space:]]*$'
  else
    CHAIN_HASH_RE='discovery_src_hash:[[:space:]]*[0-9a-f]{12}\b'
  fi
  if ! echo "$CHAIN_BLOCK" | grep -qE "$CHAIN_HASH_RE"; then
    MISSING+=("Gate 0 CHAIN_SRC: missing or malformed discovery_src_hash (must be exactly 12 hex chars, or \"n/a\" for Mode C).")
  fi

  # ----- 0B2:VALIDATED required (all modes) -----
  if ! grep -qE '^<!-- GATE0B2:VALIDATED -->$' "$PLAN_FILE"; then
    MISSING+=("Gate 0B2 VALIDATED: when CHAIN_SRC present, add <!-- GATE0B2:VALIDATED --> after factcheck-plan-step 0B2 returns PASS.")
  else
    LINE_0B2V=$(_line_of_marker '<!-- GATE0B2:VALIDATED -->')
    if [ -n "$LINE_0B2" ] && [ -n "$LINE_0C" ] && [ -n "$LINE_0B2V" ]; then
      if [ "$LINE_0B2" -ge "$LINE_0B2V" ] || [ "$LINE_0B2V" -ge "$LINE_0C" ]; then
        MISSING+=("Gate 0B2 VALIDATED: marker must appear between GATE0B2:CLAIMS and GATE0C:GAPS.")
      fi
    fi
    # Verdict authority is the ENGINE RECEIPT, not the author-typed line (S2).
    _require_receipt_pass "0B2" "Outcome Claims (0B2) section" "factcheck-plan-step 0B2"
  fi

  # ----- 0C:VALIDATED required (all modes) -----
  if ! grep -qE '^<!-- GATE0C:VALIDATED -->$' "$PLAN_FILE"; then
    MISSING+=("Gate 0C VALIDATED: when CHAIN_SRC present, add <!-- GATE0C:VALIDATED --> after factcheck-plan-step 0C returns PASS.")
  else
    LINE_0CV=$(_line_of_marker '<!-- GATE0C:VALIDATED -->')
    if [ -n "$LINE_0C" ] && [ -n "$LINE_0E" ] && [ -n "$LINE_0CV" ]; then
      if [ "$LINE_0C" -ge "$LINE_0CV" ] || [ "$LINE_0CV" -ge "$LINE_0E" ]; then
        MISSING+=("Gate 0C VALIDATED: marker must appear between GATE0C:GAPS and GATE0E:ACTIONS.")
      fi
    fi
    # Verdict authority is the ENGINE RECEIPT, not the author-typed line (S2).
    _require_receipt_pass "0C" "Gap Analysis (0C) section" "factcheck-plan-step 0C"
  fi

  # ----- 0A:VALIDATED required ONLY in Mode C -----
  if [ "$CHAIN_MODE" = "C" ]; then
    if ! grep -qE '^<!-- GATE0A:VALIDATED -->$' "$PLAN_FILE"; then
      MISSING+=("Gate 0A VALIDATED: Mode C plans require <!-- GATE0A:VALIDATED --> (fresh-Diagnosis verification PASS).")
    else
      LINE_0AV=$(_line_of_marker '<!-- GATE0A:VALIDATED -->')
      if [ -n "$LINE_0A" ] && [ -n "$LINE_0B" ] && [ -n "$LINE_0AV" ]; then
        if [ "$LINE_0A" -ge "$LINE_0AV" ] || [ "$LINE_0AV" -ge "$LINE_0B" ]; then
          MISSING+=("Gate 0A VALIDATED: marker must appear between GATE0A:PROBLEM and GATE0B:OUTCOME.")
        fi
      fi
      # Verdict authority is the ENGINE RECEIPT, not the author-typed line (S2).
      _require_receipt_pass "0A" "fresh Diagnosis (0A) section" "factcheck-plan-step 0A"
    fi
  fi

  # ----- SLICES marker required, between 0D and 0E -----
  if ! grep -qE '^<!-- GATE0SR:SLICES -->$' "$PLAN_FILE"; then
    MISSING+=("Gate 0SR SLICES: when CHAIN_SRC present, add <!-- GATE0SR:SLICES --> with slice_register_ref + slice_id.")
  else
    LINE_SR=$(_line_of_marker '<!-- GATE0SR:SLICES -->')
    if [ -n "$LINE_0D" ] && [ -n "$LINE_0E" ] && [ -n "$LINE_SR" ]; then
      if [ "$LINE_0D" -ge "$LINE_SR" ] || [ "$LINE_SR" -ge "$LINE_0E" ]; then
        MISSING+=("Gate 0SR SLICES: marker must appear between GATE0D:POLICY and GATE0E:ACTIONS.")
      fi
    fi
    SR_BLOCK=$(_block_after_marker '<!-- GATE0SR:SLICES -->')
    if ! echo "$SR_BLOCK" | grep -qE 'slice_register_ref:[[:space:]]*\S'; then
      MISSING+=("Gate 0SR SLICES: missing slice_register_ref (e.g., 'slice_register_ref: <spine-file>#slice-register').")
    fi
    if ! echo "$SR_BLOCK" | grep -qE 'slice_id:[[:space:]]*\S'; then
      MISSING+=("Gate 0SR SLICES: missing slice_id (e.g., 'slice_id: I').")
    fi

    # A4(a) — WARN, never block, when a multi-slice register omits the
    # "Write targets" column. /execute-plan's entry gate narrows an in-flight block
    # to the walked slice's declared write targets; with no column the list resolves
    # empty and the block falls back to the whole checkout. That fallback is SAFE
    # (it is today's behaviour), which is exactly why this must not block: a plan
    # authored before the column existed is still correct, merely un-narrowed.
    #
    # KNOWN LIMIT, stated rather than papered over: this fires on an ABSENT column,
    # not an INCOMPLETE one. A register that declares the column but omits a file the
    # slice actually writes passes here and silently stops gating that file. That is
    # the residual A4 records — an author-written cell is a weaker signal than
    # code-written state — and this warning narrows it without closing it.
    if grep -qE '^\|[[:space:]]*(Slice|ID)[[:space:]]*\|' "$PLAN_FILE" \
       && ! grep -qiE '^\|.*\|[[:space:]]*Write targets[[:space:]]*\|' "$PLAN_FILE"; then
      printf '⚠ Gate 0SR: the slice register has no "Write targets" column.\n' >&2
      printf '  /execute-plan will block the WHOLE checkout while a slice of this plan is\n' >&2
      printf '  walked, instead of just the files that slice touches. Add a 7th column\n' >&2
      printf '  naming each slice'"'"'s repo-relative write targets to narrow it.\n' >&2
      printf '  Advisory only — this does not block ExitPlanMode.\n' >&2
    fi
  fi

  # ----- COHERENCY required AFTER 0F -----
  if ! grep -qE '^<!-- GATE0G:COHERENCY -->$' "$PLAN_FILE"; then
    MISSING+=("Gate 0G COHERENCY: when CHAIN_SRC present, add <!-- GATE0G:COHERENCY --> after final coherency check (verdict + rounds + checker_models).")
  else
    LINE_0G=$(_line_of_marker '<!-- GATE0G:COHERENCY -->')
    if [ -n "$LINE_0F" ] && [ -n "$LINE_0G" ] && [ "$LINE_0F" -ge "$LINE_0G" ]; then
      MISSING+=("Gate 0G COHERENCY: marker must appear AFTER GATE0F:REVIEWED.")
    fi
    COH_BLOCK=$(_block_after_marker '<!-- GATE0G:COHERENCY -->')
    # Verdict authority is the ENGINE RECEIPT (S2). This ALSO fixes the E4/0G
    # threshold bug: the old grep admitted verdict: PASS|DIRTY|ESCALATE, so a
    # non-PASS silently unlocked approval. Now only a converged PASS (or a recorded
    # operator override) admits; DIRTY/ESCALATE/MISSING block.
    _require_receipt_pass "0G" "whole-plan coherency check" "factcheck-plan-coherency"
    if ! echo "$COH_BLOCK" | grep -qE 'rounds:[[:space:]]*[0-9]+'; then
      MISSING+=("Gate 0G COHERENCY: missing rounds field.")
    else
      ROUNDS_VAL=$(echo "$COH_BLOCK" | grep -oE 'rounds:[[:space:]]*[0-9]+' | head -1 | grep -oE '[0-9]+')
      # The ceiling itself is NOT weakened: >3 rounds still blocks by default, and
      # the counter is never reset by a plan edit. The ceiling firing is correct —
      # it catches real process failure. What was missing is the ESCAPE: every other
      # verdict gate in this checker admits a RECORDED operator override, and this
      # one alone did not, so a human-sanctioned continuation past max_rounds was
      # inexpressible rather than merely discouraged.
      #
      # ONE MARKER, DELIBERATELY (settled, not inherited). The rounds check consults
      # the SAME 0G OVERRIDE marker the 0G receipt check uses — no new marker, no new
      # CLI verb. Rationale: overriding a non-PASS 0G receipt is the strictly MORE
      # permissive concession (it admits a plan the engine says never converged);
      # admitting a converged-but-churny plan (PASS receipt, >3 rounds) is strictly
      # weaker and is therefore already subsumed by it. A distinct marker would add a
      # primitive without closing any hole the receipt override does not already open.
      # The real cost of sharing — an operator overriding a DIRTY receipt also clears
      # the ceiling — is answered by making it LOUD rather than by splitting the
      # marker: when the override is what admits the excess rounds, we say so on
      # stderr (see ROUNDS_OVERRIDDEN below) on both the passing and the dry-run path.
      if [ -n "$ROUNDS_VAL" ] && [ "$ROUNDS_VAL" -gt 3 ]; then
        if [ "$(_plan_receipt_verdict "0G")" = "OVERRIDE" ]; then
          ROUNDS_OVERRIDDEN="$ROUNDS_VAL"
        else
          MISSING+=("Gate 0G COHERENCY: rounds=$ROUNDS_VAL exceeds plan-kind max_rounds=3 (factcheck-convergence.md §4). Re-converge within the ceiling, or record an explicit operator override (pre_plan_gates.py plan-override 0G <plan> --reason ...) — the same recorded marker that admits a non-PASS 0G receipt.")
        fi
      fi
    fi
    if ! echo "$COH_BLOCK" | grep -qE 'checker_models:'; then
      MISSING+=("Gate 0G COHERENCY: missing checker_models field.")
    fi
  fi

  # (Per-Action Model contract moved OUT of the CHAIN_SRC block — it is now
  # mandatory for ALL plans; see the unconditional block below.)
fi
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Mandatory per-row implementation-model contract (ALL plans — unconditional).
#
# Every Coherent Action row must use the 7-column schema and declare a Model in
# {sonnet, opus, haiku, —}; at least one row must declare a REAL family
# (sonnet/opus/haiku); an empty Model cell is rejected. Keyed ONLY on the
# presence of a Coherent Actions zone (GATE0E:ACTIONS, or legacy
# GATE0D:ACTIONS) — NOT on the GATE0:CHAIN_SRC / GATE0SR:SLICES opt-in. This is
# the single source of the model-declaration gate; both ExitPlanMode paths
# (permission-plan-gate.sh, stop-plan-gate.sh) call this checker, so the
# contract holds on every path. The em-dash (—) marks a mechanical row that
# spawns no subagent; it is valid but does not satisfy the ≥1-real-family rule.
# ---------------------------------------------------------------------------
# The Coherent Actions table sits BETWEEN the Guiding Policy marker and the
# actions marker (the marker closes the section). Zone = GATE0D:POLICY →
# GATE0E:ACTIONS (or legacy GATE0D:ACTIONS) — same span the gap-coherence
# check uses above.
MODEL_ZONE_START=$(grep -nE '^<!-- GATE0D:POLICY -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
MODEL_ZONE_END=$(grep -nE '^<!-- GATE0E:ACTIONS -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)
[ -z "$MODEL_ZONE_END" ] && MODEL_ZONE_END=$(grep -nE '^<!-- GATE0D:ACTIONS -->$' "$PLAN_FILE" | head -1 | cut -d: -f1)

if [ -n "$MODEL_ZONE_START" ] && [ -n "$MODEL_ZONE_END" ]; then
  # Coherent Action rows between the Guiding Policy marker and the actions marker.
  # Anchor on the Coherent-Actions header and validate ONLY the contiguous run
  # of rows under each such header. A GATE0SR:SLICES register or a per-slice
  # Implementation-Session table living in the same GATE0D→GATE0E span is NOT an
  # actions table, so its rows must not be swept in. Multiple nested actions
  # tables (Slice-I nests one per session) are each validated; a separator row
  # or any non-pipe line ends a run.
  ACTION_ROWS=$(awk -v ln_e="$MODEL_ZONE_START" -v ln_f="$MODEL_ZONE_END" '
    NR>ln_e && NR<ln_f {
      if ($0 ~ /^\|[[:space:]]*Action[[:space:]]*\|/) { in_tbl=1; next }
      if (in_tbl && $0 ~ /^\|/) {
        if ($0 ~ /^[|[:space:]:-]+$/) next
        print
      } else {
        in_tbl=0
      }
    }
  ' "$PLAN_FILE")
  BAD_ROWS=0
  MODEL_BAD=0
  MODEL_EMPTY=0
  REAL_MODEL_COUNT=0
  ROW_COUNT=0
  while IFS= read -r ROW; do
    [ -z "$ROW" ] && continue
    ROW_COUNT=$((ROW_COUNT + 1))
    COL_COUNT=$(echo "$ROW" | awk -F'|' '{print NF}')
    # 7 data cols → NF >= 9 (leading + 7 + trailing pipes).
    if [ "$COL_COUNT" -lt 9 ]; then
      BAD_ROWS=$((BAD_ROWS + 1))
      continue
    fi
    # Schema: | Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
    # awk -F'|' → $1=leading, $2..$8 = the 7 columns, $9=trailing. Model is $7.
    MODEL_CELL=$(echo "$ROW" | awk -F'|' '{print $7}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    MODEL_LC=$(echo "$MODEL_CELL" | tr 'A-Z' 'a-z')
    case "$MODEL_LC" in
      sonnet|opus|haiku) REAL_MODEL_COUNT=$((REAL_MODEL_COUNT + 1)) ;;
      —) ;;
      "") MODEL_EMPTY=$((MODEL_EMPTY + 1)) ;;
      *) MODEL_BAD=$((MODEL_BAD + 1)) ;;
    esac
  done <<< "$ACTION_ROWS"
  if [ "$BAD_ROWS" -gt 0 ]; then
    MISSING+=("Gate 0E Actions Schema: $BAD_ROWS Coherent Action rows have fewer than 7 columns. Required: | Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |")
  fi
  if [ "$MODEL_EMPTY" -gt 0 ]; then
    MISSING+=("Gate 0E Model Required: $MODEL_EMPTY Coherent Action rows have an empty Model cell. Every action must declare a Model (sonnet/opus/haiku, or — for a mechanical row that spawns no subagent).")
  fi
  if [ "$MODEL_BAD" -gt 0 ]; then
    MISSING+=("Gate 0E Model Allowlist: $MODEL_BAD Coherent Action rows have a Model value outside {sonnet, opus, haiku, —}.")
  fi
  if [ "$ROW_COUNT" -gt 0 ] && [ "$REAL_MODEL_COUNT" -eq 0 ]; then
    MISSING+=("Gate 0E Model Required: no Coherent Action declares a real model. At least one action must use sonnet, opus, or haiku (— alone is only for mechanical rows).")
  fi
fi

# A3: --dry-run preview — surface every grounding note + every gate issue, then
# exit 0 without blocking. Lets an author see all failures at once and fix the
# whole batch before a real (blocking) run.
if [ "$DRY_RUN" -eq 1 ]; then
  echo "DRY-RUN: preview for $PLAN_FILE (no block; exit 0)"
  if [ "${#GROUNDING_DETAILS[@]}" -gt 0 ]; then
    echo ""
    echo "Gate 1 source-grounding notes:"
    for _d in "${GROUNDING_DETAILS[@]}"; do
      echo "  - $_d"
    done
  fi
  if [ -n "${ROUNDS_OVERRIDDEN:-}" ]; then
    echo ""
    echo "  · note: Gate 0G rounds=$ROUNDS_OVERRIDDEN exceeds plan-kind max_rounds=3 and is admitted ONLY by the recorded 0G operator override."
  fi
  echo ""
  if [ ${#MISSING[@]} -gt 0 ]; then
    echo "Gate issues that WOULD block a normal run:"
    for msg in "${MISSING[@]}"; do
      echo "  ✗ $msg"
    done
  else
    echo "No blocking gate issues."
  fi
  exit 0
fi

# Informational (non-blocking): surface any unresolvable cited paths — on BOTH a
# passing and a blocking run — so an author is never left guessing why a citation
# "passed" without actually being checked (C3: surfaced, not silently skipped).
for _d in "${GROUNDING_DETAILS[@]}"; do
  case "$_d" in *"UNRESOLVED PATH"*) echo "  · note: $_d" >&2 ;; esac
done

# Informational (non-blocking): the 0G rounds ceiling was exceeded and admitted
# ONLY by the recorded operator override. Surfaced on the passing run (and in
# --dry-run above) so the shared-marker coupling is visible at the moment it acts
# — an operator who recorded the override to clear a non-PASS 0G receipt learns
# here that the same marker also cleared the max_rounds ceiling. Never silent.
if [ -n "${ROUNDS_OVERRIDDEN:-}" ]; then
  echo "  · note: Gate 0G rounds=$ROUNDS_OVERRIDDEN exceeds plan-kind max_rounds=3 (factcheck-convergence.md §4) and was admitted ONLY by the recorded 0G operator override (pre_plan_gates.py plan-override 0G). The ceiling was not weakened; this continuation is sanctioned and logged in the override marker." >&2
fi

# Report results
if [ ${#MISSING[@]} -gt 0 ]; then
  echo "BLOCKED: Plan is missing required gate markers." >&2
  echo "Plan file: $PLAN_FILE" >&2
  echo "" >&2
  for msg in "${MISSING[@]}"; do
    echo "  ✗ $msg" >&2
  done
  echo "" >&2
  echo "Complete all gates before exiting plan mode." >&2
  exit 2
fi

exit 0

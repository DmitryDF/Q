#!/usr/bin/env bash
# Integration tests for check-plan-gates.sh.
#
# Locks the whole-line anchoring contract (2026-04-20): marker regexes must
# treat markers as delimiters, not as substrings. Without anchoring, a plan's
# own prose mentioning marker text can shift sed ranges and flip grep
# existence checks — see Diary/2026-04-19 carry-forward note.
#
# Also locks the unconditional implementation-model contract (2026-06-19):
# EVERY plan with a Coherent Actions zone must use the 7-column schema and
# declare a Model per action (≥1 real family; no empty cell; em-dash only for
# mechanical rows) — independent of the GATE0:CHAIN_SRC / GATE0SR:SLICES
# opt-in. The Model cell is column $7 (NOT $8 = Reason); the garbage-Model /
# valid-Reason case below pins that column fix.
#
# The hook is invoked with the plan path as $1 and returns 0 on pass, 2 on
# block. Tests build temp plan fixtures and assert the exit code.

set -u

# Pin the config dir (where the hooks live) BEFORE isolating HOME, so the hook
# path survives the HOME override on both a live run and a claude-experiment clone.
: "${CLAUDE_CONFIG_DIR:=$HOME/.claude}"
export CLAUDE_CONFIG_DIR
HOOK="$CLAUDE_CONFIG_DIR/hooks/check-plan-gates.sh"
PPG="$CLAUDE_CONFIG_DIR/hooks/pre_plan_gates.py"
TMP_ROOT=$(mktemp -d -t plan-gates-test-XXXXXX)
# Isolate all plan-validation STATE (the S2 engine receipts, which pre_plan_gates.py
# writes under Path.home()/.claude/state) into the temp tree — never touch real
# ~/.claude/state. The config dir is already pinned above, so the hooks still resolve.
export HOME="$TMP_ROOT"
PASS=0
FAIL=0

# write_slice_i_receipts PLAN_PATH [VERDICT] — write an engine receipt for each
# Slice-I gate (0A/0B2/0C/0G) so a CHAIN_SRC plan can reach a converged verdict.
# The verdict is authored by the shared engine from the piped checker output
# (VERDICT: PASS by default) — this is exactly what the plan/SKILL.md orchestrator
# does at authoring time; the tests reproduce it deterministically.
write_slice_i_receipts() {
  local plan="$1" verdict="${2:-PASS}" gate
  for gate in 0A 0B2 0C; do
    printf '[{"model":"sonnet","verdict":"VERDICT: %s"}]' "$verdict" \
      | python3 "$PPG" factcheck-plan-step "$gate" "$plan" --round 1 --max-rounds 3 \
        >/dev/null 2>&1
  done
  printf '[{"model":"sonnet","verdict":"VERDICT: %s"}]' "$verdict" \
    | python3 "$PPG" factcheck-plan-coherency "sid-test" "$plan" --round 1 --max-rounds 3 \
      >/dev/null 2>&1
}

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

run_case() {
  local name="$1" expected="$2" plan_path="$3"
  bash "$HOOK" "$plan_path" >/dev/null 2>&1
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    echo "      plan: $plan_path"
    FAIL=$((FAIL+1))
  fi
}

# ----- Fixture halves: everything up to "## Coherent Actions", and the tail -----
#
# The Coherent Actions TABLE is injected between the two halves so each test can
# vary it. Gate 0a–0f markers in canonical order; Gate 0 coherence links
# C1→G1→A1; Gate 1 table with one [unverified] row; Gate 2 Code row with an
# enforcement keyword; Gate 2B marker; Gate 3 NO_CLAIMS track.
emit_head() {
  cat <<'EOF'
# Test Plan

## Diagnosis
Describes the problem.
<!-- GATE0A:PROBLEM -->

## Desired Outcome
Describes the operational outcome.
<!-- GATE0B:OUTCOME -->

## Outcome Claims
| # | Claim | Addresses Diagnosis |
|---|-------|---------------------|
| C1 | Claim text | the problem |

claim_fallback_reason: user-acknowledged-skip: test fixture baseline (hand-decomposition)

**Coverage:** C1 resolves the diagnosis because it addresses the problem.
<!-- GATE0B2:CLAIMS -->

## Gap Analysis
| # | Gap | Current | Desired | Claim |
|---|-----|---------|---------|-------|
| G1 | Gap text | now | later | C1 |
<!-- GATE0C:GAPS -->

## Guiding Policy
Describes the approach.
<!-- GATE0D:POLICY -->

## Coherent Actions
EOF
}

# Success-path head: the single structured Outcome-Claims list is ONE fenced json
# block inside the ## Outcome Claims section (no re-worded markdown table, no
# separate CLAIM_SET marker). Each claim carries id + addresses_diagnosis; the gap
# table references those ids. $1 = the json claim-set string.
emit_json_head() {
  local js="$1"
  cat <<EOF
# Test Plan

## Diagnosis
Describes the problem.
<!-- GATE0A:PROBLEM -->

## Desired Outcome
Describes the operational outcome.
<!-- GATE0B:OUTCOME -->

## Outcome Claims

\`\`\`json
$js
\`\`\`

**Coverage:** C1 resolves the diagnosis because it addresses the problem.
<!-- GATE0B2:CLAIMS -->

## Gap Analysis
| # | Gap | Current | Desired | Claim |
|---|-----|---------|---------|-------|
| G1 | Gap text | now | later | C1 |
<!-- GATE0C:GAPS -->

## Guiding Policy
Describes the approach.
<!-- GATE0D:POLICY -->

## Coherent Actions
EOF
}

# A valid single-list deep json: one claim id=C1 with addresses_diagnosis, linked
# to gap G1's Claim ref. Reused by the success-path tests below.
VALID_SINGLE_LIST_JSON='{"source_type":"web","source_path":"plan:0b2-test","lang":"en","thoroughness":"deep","claims":[{"text":"A test claim for the fixture.","id":"C1","addresses_diagnosis":"the problem","locator":"T-1","role":"backward","flags":{"atomicity":true,"verifiability":true,"decontextuality":true,"minimality":true,"fluency":true,"faithfulness":true}}],"refused":[]}'

emit_json_plan() { emit_json_head "$1"; printf '%s\n' "$VALID_ACTIONS"; emit_tail; }

emit_tail() {
  cat <<'EOF'

**Coherence:** A1 carries out the guiding policy.
<!-- GATE0E:ACTIONS -->

## Design Review
Reviewed for edge cases.
<!-- GATE0F:REVIEWED -->

## Verification
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status | Disposition | Hypothesis ID |
|-------|----------|-----------------|------------------|--------|-------------|---------------|
| Claim one | Code behavior | `file.sh` | synthetic | [unverified — alternative: test fixture] | n/a | |
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->

## Gate 2
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
| A1 | Code | validate via test harness |
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->

<!-- GATE2B:DESIGN_REVIEW -->

<!-- GATE3:NO_CLAIMS -->
EOF
}

# Valid 7-column Coherent Actions table (one real-model row).
VALID_ACTIONS='| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | sonnet | judgment task |'

emit_plan() { emit_head; printf '%s\n' "$1"; emit_tail; }
emit_valid_plan() { emit_plan "$VALID_ACTIONS"; }

# Full Slice-I plan (GATE0:CHAIN_SRC opt-in) for the Mode-C discovery_src_hash
# sentinel tests (2026-07-09). $1 = mode (A|B|C), $2 = discovery_src_hash value
# (a 12-hex string or the literal "n/a"). Carries the whole per-mode required-set
# incl. GATE0A:VALIDATED; for A/B the extra 0A:VALIDATED marker is harmless (only
# required for Mode C). Backticks in the Gate-1 row are escaped so the unquoted
# heredoc does not command-substitute them.
emit_chain_src_plan() {
  local mode="$1" hash="$2" rounds="${3:-1}"
  cat <<EOF
# Test Plan CHAIN_SRC

<!-- GATE0:CHAIN_SRC -->
mode: $mode
discovery_src_hash: $hash
alternative_n: n/a

## Diagnosis
Describes the problem.
<!-- GATE0A:PROBLEM -->

verdict: PASS
<!-- GATE0A:VALIDATED -->

## Desired Outcome
Describes the operational outcome.
<!-- GATE0B:OUTCOME -->

## Outcome Claims
| # | Claim | Addresses Diagnosis |
|---|-------|---------------------|
| C1 | Claim text | the problem |

claim_fallback_reason: user-acknowledged-skip: test fixture baseline (hand-decomposition)

**Coverage:** C1 resolves the diagnosis because it addresses the problem.
<!-- GATE0B2:CLAIMS -->

verdict: PASS
<!-- GATE0B2:VALIDATED -->

## Gap Analysis
| # | Gap | Current | Desired | Claim |
|---|-----|---------|---------|-------|
| G1 | Gap text | now | later | C1 |
<!-- GATE0C:GAPS -->

verdict: PASS
<!-- GATE0C:VALIDATED -->

## Guiding Policy
Describes the approach.
<!-- GATE0D:POLICY -->

<!-- GATE0SR:SLICES -->
slice_register_ref: n/a (Mode C — no spine)
slice_id: S1
<!-- GATE0SR:SLICES -->

## Coherent Actions
| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | sonnet | judgment task |

**Coherence:** A1 carries out the guiding policy.
<!-- GATE0E:ACTIONS -->

## Design Review
Reviewed for edge cases.
<!-- GATE0F:REVIEWED -->

## Verification
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status |
|-------|----------|-----------------|------------------|--------|
| Claim one | Code behavior | \`file.sh\` | synthetic | [unverified — alternative: test fixture] |
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->

## Gate 2
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
| A1 | Code | validate via test harness |
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->

<!-- GATE2B:DESIGN_REVIEW -->

<!-- GATE3:NO_CLAIMS -->

<!-- GATE0G:COHERENCY -->
verdict: PASS
rounds: $rounds
checker_models: [sonnet, sonnet, sonnet, opus]
<!-- GATE0G:COHERENCY -->
EOF
}

# ----- Test 1: baseline pass (7-column, real model) -----
PLAN1="$TMP_ROOT/baseline.md"
emit_valid_plan > "$PLAN1"
run_case "1-baseline-valid" 0 "$PLAN1"

# ----- Test 2: pseudocode-in-prose regression -----
#
# Same plan, plus an extra paragraph quoting literal marker text in prose and
# in a sed pseudocode example. Anchored regex must keep it invisible. exit 0.
PLAN2="$TMP_ROOT/prose-markers.md"
{
  emit_valid_plan
  cat <<'EOF'

## Notes

In the old hook, `sed -n '/<!-- GATE1:START -->/,/<!-- GATE1:END -->/p'` would
open a phantom range at this prose line. A mention of `<!-- GATE0A:PROBLEM -->`
in a sentence should not be treated as a real marker either.
EOF
} > "$PLAN2"
run_case "2-prose-markers-regression" 0 "$PLAN2"

# ----- Test 3: missing marker sanity -----
PLAN3="$TMP_ROOT/missing-0a.md"
emit_valid_plan | grep -v 'GATE0A:PROBLEM' > "$PLAN3"
run_case "3-missing-0a-marker" 2 "$PLAN3"

# ----- Test 4: legacy 3-column actions table → block (model contract mandatory) -----
PLAN4="$TMP_ROOT/legacy-3col.md"
emit_plan '| Action | Addresses Gap | What it does |
|--------|---------------|--------------|
| A1 | G1 | Does the thing |' > "$PLAN4"
run_case "4-legacy-3col-no-model" 2 "$PLAN4"

# ----- Test 5: all rows em-dash → block (no real model declared) -----
PLAN5="$TMP_ROOT/all-emdash.md"
emit_plan '| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | — | mechanical |' > "$PLAN5"
run_case "5-all-emdash-no-real-model" 2 "$PLAN5"

# ----- Test 6: empty Model cell → block -----
PLAN6="$TMP_ROOT/empty-model.md"
emit_plan '| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes |  | missing |' > "$PLAN6"
run_case "6-empty-model-cell" 2 "$PLAN6"

# ----- Test 7: column-fix regression — garbage Model, valid family in Reason → block -----
#
# If the hook read $8 (Reason) instead of $7 (Model), it would see "opus" and
# PASS. Reading $7 it sees "garbage" → MODEL_BAD + no real model → block.
PLAN7="$TMP_ROOT/garbage-model-valid-reason.md"
emit_plan '| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | garbage | opus |' > "$PLAN7"
run_case "7-garbage-model-valid-reason" 2 "$PLAN7"

# ----- Test 8: em-dash mechanical row alongside a real-model row → pass -----
PLAN8="$TMP_ROOT/mixed-emdash-real.md"
emit_plan '| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | opus | core logic |
| A2 | G1 | Commit + smoke | guard | suite green | — | mechanical |' > "$PLAN8"
run_case "8-mixed-emdash-and-real-model" 0 "$PLAN8"

# ---------------------------------------------------------------------------
# Slice-I GATE0:CHAIN_SRC — Mode-C discovery_src_hash "n/a" sentinel (2026-07-09).
#
# Mode C (Ninja-Plan) has no upstream Discovery to fingerprint, so it may declare
# `discovery_src_hash: n/a`. The relaxation is mode-branched: A/B keep the strict
# 12-hex requirement (their provenance anchor). Locks all four corners.
# ---------------------------------------------------------------------------

# 8a: Mode-C with the n/a sentinel → pass (the fix). Needs engine receipts (S2).
PLAN8A="$TMP_ROOT/chainsrc-modec-na.md"
emit_chain_src_plan C "n/a" > "$PLAN8A"
write_slice_i_receipts "$PLAN8A"
run_case "8a-modeC-hash-na-passes" 0 "$PLAN8A"

# 8b: Mode-C with a real 12-hex → still pass (backward-compatible).
PLAN8B="$TMP_ROOT/chainsrc-modec-hex.md"
emit_chain_src_plan C "24d440b2f697" > "$PLAN8B"
write_slice_i_receipts "$PLAN8B"
run_case "8b-modeC-hash-12hex-passes" 0 "$PLAN8B"

# 8c: Mode-A with the n/a sentinel → block (A/B provenance anchor preserved).
PLAN8C="$TMP_ROOT/chainsrc-modeA-na.md"
emit_chain_src_plan A "n/a" > "$PLAN8C"
write_slice_i_receipts "$PLAN8C"
run_case "8c-modeA-hash-na-blocks" 2 "$PLAN8C"

# 8d: Mode-A with a real 12-hex → pass.
PLAN8D="$TMP_ROOT/chainsrc-modeA-hex.md"
emit_chain_src_plan A "24d440b2f697" > "$PLAN8D"
write_slice_i_receipts "$PLAN8D"
run_case "8d-modeA-hash-12hex-passes" 0 "$PLAN8D"

# ---------------------------------------------------------------------------
# S2: the Slice-I verdict comes from the ENGINE RECEIPT, not the author-typed
# `verdict:` line. These lock the honor-system close + the E4/0G threshold fix.
# ---------------------------------------------------------------------------

# 8e: author typed verdict: PASS everywhere but NO engine receipt exists → block.
# (This is the honor-system hole: a self-attested verdict must not unlock approval.)
PLAN8E="$TMP_ROOT/chainsrc-no-receipt.md"
emit_chain_src_plan C "n/a" > "$PLAN8E"
run_case "8e-no-receipt-blocks-despite-typed-PASS" 2 "$PLAN8E"

# 8f: a non-PASS engine receipt (DIRTY) → block, even with typed verdict: PASS.
# (Fixes E4: the old 0G grep admitted DIRTY/ESCALATE.)
PLAN8F="$TMP_ROOT/chainsrc-dirty-receipt.md"
emit_chain_src_plan C "n/a" > "$PLAN8F"
write_slice_i_receipts "$PLAN8F" DISCREPANCY
run_case "8f-nonpass-receipt-blocks" 2 "$PLAN8F"

# 8g: a DIRTY receipt PLUS an explicit recorded operator override → pass.
PLAN8G="$TMP_ROOT/chainsrc-override.md"
emit_chain_src_plan C "n/a" > "$PLAN8G"
write_slice_i_receipts "$PLAN8G" DISCREPANCY
for _g in 0A 0B2 0C 0G; do
  python3 "$PPG" plan-override "$_g" "$PLAN8G" --reason "operator accepts residual: test" \
    >/dev/null 2>&1
done
run_case "8g-recorded-override-passes" 0 "$PLAN8G"

# ---------------------------------------------------------------------------
# Gate 1 source-grounding diagnostics + --dry-run (2026-07-09).
#
# Locks: (a) a failed grounding row emits a per-row detail naming the file,
# the snippet, AND an inferred reason (quotes / whitespace / ellipsis / none);
# (b) --dry-run surfaces the details and exits 0 without blocking; (c) a
# genuinely-absent snippet still fails (strict grep -qF preserved); (d) an
# unresolvable cited path is surfaced (informational) and does NOT block.
# ---------------------------------------------------------------------------

# assert_case NAME EXPECTED_EXIT NEEDLE STREAM(out|err|both) HOOK_ARG...
assert_case() {
  local name="$1" expected="$2" needle="$3" stream="$4"; shift 4
  local ofile="$TMP_ROOT/o.out" efile="$TMP_ROOT/o.err" rc hay okc okn
  bash "$HOOK" "$@" >"$ofile" 2>"$efile"; rc=$?
  case "$stream" in
    out) hay=$(cat "$ofile") ;;
    err) hay=$(cat "$efile") ;;
    *)   hay=$(cat "$ofile" "$efile") ;;
  esac
  okc=0; okn=0
  [ "$rc" -eq "$expected" ] && okc=1
  if [ -z "$needle" ] || printf '%s' "$hay" | grep -qF "$needle"; then okn=1; fi
  if [ "$okc" -eq 1 ] && [ "$okn" -eq 1 ]; then
    echo "PASS  $name  (exit $rc, matched \"$needle\")"; PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected exit $expected got $rc; needle \"$needle\" found=$okn)"
    FAIL=$((FAIL+1))
  fi
}

# A cited source file with known substrings that fail grep -qF by exactly one
# kind of cosmetic drift.
SRCFILE="$TMP_ROOT/src.sh"
cat > "$SRCFILE" <<'EOF'
#!/bin/bash
greeting=hello world done
spaced=alpha   beta   gamma
joined=start middle finish
EOF

# Build a plan whose Gate 1 table is a single caller-supplied verified row.
emit_ground_plan() {
  emit_head
  printf '%s\n' "$VALID_ACTIONS"
  cat <<EOF

**Coherence:** A1 carries out the guiding policy.
<!-- GATE0E:ACTIONS -->

## Design Review
Reviewed.
<!-- GATE0F:REVIEWED -->

## Verification
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status |
|-------|----------|-----------------|------------------|--------|
$1
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->

## Gate 2
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
| A1 | Code | validate via test harness |
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->

<!-- GATE2B:DESIGN_REVIEW -->

<!-- GATE3:NO_CLAIMS -->
EOF
}

# Rows drifting from SRCFILE by exactly one cosmetic kind (whole-file check).
ROW_QUOTES='| quote drift | Code behavior | `'"$SRCFILE"'` | snippet `"hello world"` | [verified: grounded] |'
ROW_WS='| whitespace drift | Code behavior | `'"$SRCFILE"'` | snippet `alpha beta` | [verified: grounded] |'
ROW_ELLIP='| ellipsis drift | Code behavior | `'"$SRCFILE"'` | snippet `start...finish` | [verified: grounded] |'
ROW_FAB='| fabricated | Code behavior | `'"$SRCFILE"'` | snippet `zzz_absent_snippet_text` | [verified: grounded] |'
ROW_UNRES='| unresolvable | Code behavior | `'"$TMP_ROOT"'/does-not-exist.sh` | snippet `some missing thing` | [verified: grounded] |'

PLAN_Q="$TMP_ROOT/g-quotes.md";  emit_ground_plan "$ROW_QUOTES" > "$PLAN_Q"
PLAN_W="$TMP_ROOT/g-ws.md";      emit_ground_plan "$ROW_WS"     > "$PLAN_W"
PLAN_E="$TMP_ROOT/g-ellip.md";   emit_ground_plan "$ROW_ELLIP"  > "$PLAN_E"
PLAN_F="$TMP_ROOT/g-fab.md";     emit_ground_plan "$ROW_FAB"    > "$PLAN_F"
PLAN_U="$TMP_ROOT/g-unres.md";   emit_ground_plan "$ROW_UNRES"  > "$PLAN_U"

# 9: normal run names file + snippet + "added/removed quotes" reason, still blocks.
assert_case "9-quotes-normal-detail"   2 "added/removed quotes"  err "$PLAN_Q"
# 10: --dry-run surfaces the same reason and exits 0 (order-tolerant: flag first).
assert_case "10-quotes-dryrun-exit0"   0 "added/removed quotes"  out --dry-run "$PLAN_Q"
# 10b: flag after the path also works (order-tolerant).
assert_case "10b-dryrun-flag-after"    0 "DRY-RUN"               out "$PLAN_Q" --dry-run
# 11: whitespace reason.
assert_case "11-whitespace-reason"     2 "whitespace mismatch"   err "$PLAN_W"
# 12: interior-ellipsis reason.
assert_case "12-ellipsis-reason"       2 "interior ellipsis"     err "$PLAN_E"
# 13: fabricated snippet still fails (strict match preserved) with no inferred reason.
assert_case "13-fabricated-still-fails" 2 "no reason inferred"   err "$PLAN_F"
# 14: unresolvable path is surfaced (informational) and does NOT block (exit 0).
assert_case "14-unresolvable-note"     0 "UNRESOLVED PATH"       err "$PLAN_U"
# 15: the baseline valid plan is unaffected under --dry-run (exit 0, no issues).
assert_case "15-dryrun-clean-plan"     0 "No blocking gate"      out --dry-run "$PLAN1"

# ---------------------------------------------------------------------------
# 5 over-broad-matcher hardening (2026-07-09). Each fix has a POSITIVE fixture
# (a legitimate plan no longer false-blocks) and a NEGATIVE fixture (a genuinely
# malformed plan STILL blocks) — narrowing must not weaken enforcement.
# ---------------------------------------------------------------------------

# Fix 1 — Coherent-Actions zone: a slice-register/other pipe table in the
# GATE0D→GATE0E span must not be parsed as short Coherent-Action rows.
F1_POS='| Slice | Status |
|-------|--------|
| S1 | NOW |

| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | sonnet | judgment |'
PLAN_F1P="$TMP_ROOT/f1-slice-table.md"; emit_plan "$F1_POS" > "$PLAN_F1P"
run_case "16-actions-zone-slice-table-passes" 0 "$PLAN_F1P"

F1_NEG='| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | sonnet | judgment |
| A2 | G1 | short row |'
PLAN_F1N="$TMP_ROOT/f1-short-row.md"; emit_plan "$F1_NEG" > "$PLAN_F1N"
run_case "17-actions-zone-short-row-blocks" 2 "$PLAN_F1N"

# Fix 2 — FLAGGED counted only in the Type column (col 3).
emit_valid_plan | sed 's/validate via test harness/validate FLAGGED items here via test harness/' > "$TMP_ROOT/f2-flagged-desc.md"
run_case "18-flagged-in-description-passes" 0 "$TMP_ROOT/f2-flagged-desc.md"
emit_valid_plan | sed 's/| A1 | Code | validate via test harness |/| A1 | FLAGGED | needs a human decision |/' > "$TMP_ROOT/f2-flagged-type.md"
run_case "19-flagged-type-column-blocks" 2 "$TMP_ROOT/f2-flagged-type.md"

# Fix 3 — round count scoped to the Gate-3 zone / `### Round` headers.
{
  emit_head
  printf '%s\n' "$VALID_ACTIONS"
  cat <<'EOF'

**Coherence:** A1 carries out the policy. The earlier effort needed Round 6 to converge.
<!-- GATE0E:ACTIONS -->

## Design Review
Reviewed. A prior project took Round 6 of review.
<!-- GATE0F:REVIEWED -->

## Verification
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status |
|-------|----------|-----------------|------------------|--------|
| c | Code behavior | `file.sh` | synthetic | [unverified — alternative: fixture] |
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->

## Gate 2
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
| A1 | Code | validate via test harness |
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->

<!-- GATE2B:DESIGN_REVIEW -->

<!-- GATE3:START -->
| Claim | Source | Status |
|-------|--------|--------|
| internal claim | `file.sh` line 1 | verified |
<!-- GATE3:END -->
<!-- GATE3:INTERNAL_ONLY -->
EOF
} > "$TMP_ROOT/f3-prose-round.md"
run_case "20-round-prose-outside-zone-passes" 0 "$TMP_ROOT/f3-prose-round.md"

{
  emit_head
  printf '%s\n' "$VALID_ACTIONS"
  cat <<'EOF'

**Coherence:** A1 carries out the policy.
<!-- GATE0E:ACTIONS -->

## Design Review
Reviewed.
<!-- GATE0F:REVIEWED -->

## Verification
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status |
|-------|----------|-----------------|------------------|--------|
| c | Code behavior | `file.sh` | synthetic | [unverified — alternative: fixture] |
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->

## Gate 2
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
| A1 | Code | validate via test harness |
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->

<!-- GATE2B:DESIGN_REVIEW -->

<!-- GATE3:START -->
### Round 1
checkers ran; DIRTY.
### Round 2
DIRTY.
### Round 3
DIRTY.
### Round 4
still not converged.
| Claim | Source | Status |
|-------|--------|--------|
| internal claim | `file.sh` line 1 | verified |
<!-- GATE3:END -->
<!-- GATE3:INTERNAL_ONLY -->
EOF
} > "$TMP_ROOT/f3-unconverged.md"
run_case "21-round-headers-in-zone-block" 2 "$TMP_ROOT/f3-unconverged.md"

# Fix 4 — exact documented placeholder passes; embedded "artifact" still blocks.
ROW_ARTIFACT='| doc placeholder | Docs | plain prose | none | [verified: artifact] |'
emit_ground_plan "$ROW_ARTIFACT" > "$TMP_ROOT/f4-artifact-literal.md"
run_case "22-verified-artifact-literal-passes" 0 "$TMP_ROOT/f4-artifact-literal.md"
ROW_EMBED_ARTIFACT='| embedded | Docs | plain prose | none | [verified: an artifact of X] |'
emit_ground_plan "$ROW_EMBED_ARTIFACT" > "$TMP_ROOT/f4-artifact-embedded.md"
run_case "23-embedded-artifact-still-blocks" 2 "$TMP_ROOT/f4-artifact-embedded.md"

# Fix 5 — dash-leading grounding snippet checked correctly; absent one still blocks.
DASHSRC="$TMP_ROOT/dashsrc.sh"
cat > "$DASHSRC" <<'EOF'
#!/bin/bash
usage: --dry-run is a supported flag
EOF
ROW_DASH_OK='| dash present | Code behavior | `'"$DASHSRC"'` | snippet `--dry-run is a supported flag` | [verified: grounded] |'
emit_ground_plan "$ROW_DASH_OK" > "$TMP_ROOT/f5-dash-present.md"
run_case "24-dash-snippet-present-passes" 0 "$TMP_ROOT/f5-dash-present.md"
ROW_DASH_ABSENT='| dash absent | Code behavior | `'"$DASHSRC"'` | snippet `--no-such-flag-xyz-abc` | [verified: grounded] |'
emit_ground_plan "$ROW_DASH_ABSENT" > "$TMP_ROOT/f5-dash-absent.md"
run_case "25-dash-snippet-absent-blocks" 2 "$TMP_ROOT/f5-dash-absent.md"

# ----- Tests 26-37: single structured Outcome-Claims list (plan-claim-single-source-of-truth, 2026-07-18) -----
# The `## Outcome Claims` section (GATE0B:OUTCOME → GATE0B2:CLAIMS) must carry EITHER a
# fenced ```json claim-set (the single source of truth — no separate GATE0B2:CLAIM_SET
# marker, no re-worded second table) OR a non-empty claim_fallback_reason: line. Schema +
# DEEP validated through the shared _claim_persist seam; Claims↔Gaps bidirectional +
# per-claim addresses_diagnosis enforced by _plan_claim_gate.py.

# 26: CLAIMS present but the section carries neither json nor a fallback reason → block.
PLAN_NOCS="$TMP_ROOT/no-list.md"
emit_valid_plan | grep -v '^claim_fallback_reason:' > "$PLAN_NOCS"
run_case "26-claims-present-no-list-blocks" 2 "$PLAN_NOCS"

# 27: a stray retired GATE0B2:CLAIM_SET marker is inert (the section still has the
# fallback reason) → pass. Proves the retired marker is neither required nor rejected.
PLAN_STRAY="$TMP_ROOT/stray-claimset-marker.md"
emit_valid_plan | sed 's/^## Gap Analysis/<!-- GATE0B2:CLAIM_SET -->\n\n## Gap Analysis/' > "$PLAN_STRAY"
run_case "27-stray-retired-claimset-marker-inert" 0 "$PLAN_STRAY"

# 28: fallback path (non-empty claim_fallback_reason) → pass (retained escape).
PLAN_FB="$TMP_ROOT/fallback-list.md"
emit_valid_plan > "$PLAN_FB"
run_case "28-fallback-reason-passes" 0 "$PLAN_FB"

# 29: success path — one fenced json single-list (id + addresses_diagnosis, linked gap) → pass.
PLAN_JSON="$TMP_ROOT/json-list.md"
emit_json_plan "$VALID_SINGLE_LIST_JSON" > "$PLAN_JSON"
run_case "29-single-list-valid-json-passes" 0 "$PLAN_JSON"

# 30: bare free-text fallback reason (no category prefix) → block (shared grammar).
PLAN_BAREFB="$TMP_ROOT/bare-fallback.md"
emit_valid_plan | sed 's/^claim_fallback_reason:.*/claim_fallback_reason: chose to hand-decompose/' > "$PLAN_BAREFB"
run_case "30-bare-fallback-reason-blocks" 2 "$PLAN_BAREFB"

# 31: a concrete engine-failure category prefix passes the grammar.
PLAN_CATFB="$TMP_ROOT/cat-fallback.md"
emit_valid_plan | sed 's/^claim_fallback_reason:.*/claim_fallback_reason: engine-error: dispatch adapter raised SchemaError/' > "$PLAN_CATFB"
run_case "31-category-fallback-reason-passes" 0 "$PLAN_CATFB"

# 32: NORMAL-thoroughness single list → block (deep_mode_ok refuses a non-DEEP set at plan:0b2).
PLAN_NORMAL="$TMP_ROOT/json-normal.md"
NORMAL_JSON=$(printf '%s' "$VALID_SINGLE_LIST_JSON" | sed 's/"thoroughness":"deep"/"thoroughness":"normal"/')
emit_json_plan "$NORMAL_JSON" > "$PLAN_NORMAL"
run_case "32-normal-thoroughness-single-list-blocks" 2 "$PLAN_NORMAL"

# 33: a claim with no addresses_diagnosis field → block (A6, code-enforced).
PLAN_NODIAG="$TMP_ROOT/json-no-diag.md"
NODIAG_JSON=$(printf '%s' "$VALID_SINGLE_LIST_JSON" | sed 's/,"addresses_diagnosis":"the problem"//')
emit_json_plan "$NODIAG_JSON" > "$PLAN_NODIAG"
run_case "33-claim-missing-addresses-diagnosis-blocks" 2 "$PLAN_NODIAG"

# 34: a claim with an empty addresses_diagnosis → block (A6).
PLAN_EMPTYDIAG="$TMP_ROOT/json-empty-diag.md"
EMPTYDIAG_JSON=$(printf '%s' "$VALID_SINGLE_LIST_JSON" | sed 's/"addresses_diagnosis":"the problem"/"addresses_diagnosis":""/')
emit_json_plan "$EMPTYDIAG_JSON" > "$PLAN_EMPTYDIAG"
run_case "34-claim-empty-addresses-diagnosis-blocks" 2 "$PLAN_EMPTYDIAG"

# 35: a gap that cites a claim id absent from the single list → block (A3 reverse; phantom-gap hole closed).
PLAN_PHANTOM="$TMP_ROOT/json-phantom-gap.md"
emit_json_plan "$VALID_SINGLE_LIST_JSON" | sed 's/| G1 | Gap text | now | later | C1 |/| G1 | Gap text | now | later | C1 |\n| G2 | Other | now | later | C9 |/' > "$PLAN_PHANTOM"
run_case "35-gap-cites-phantom-claim-blocks" 2 "$PLAN_PHANTOM"

# 36: a claim in the single list with no gap addressing it → block (A3 forward preserved).
PLAN_ORPHAN="$TMP_ROOT/json-orphan-claim.md"
TWO_CLAIM_JSON='{"source_type":"web","source_path":"plan:0b2-test","lang":"en","thoroughness":"deep","claims":[{"text":"First claim.","id":"C1","addresses_diagnosis":"the problem","locator":"T-1","role":"backward","flags":{"atomicity":true,"verifiability":true,"decontextuality":true,"minimality":true,"fluency":true,"faithfulness":true}},{"text":"Second claim with no gap.","id":"C2","addresses_diagnosis":"another aspect","locator":"T-2","role":"backward","flags":{"atomicity":true,"verifiability":true,"decontextuality":true,"minimality":true,"fluency":true,"faithfulness":true}}],"refused":[]}'
emit_json_plan "$TWO_CLAIM_JSON" > "$PLAN_ORPHAN"
run_case "36-claim-with-no-gap-blocks" 2 "$PLAN_ORPHAN"

# 37: a claim in the single list with no id → block (needed for cross-ref).
PLAN_NOID="$TMP_ROOT/json-no-id.md"
NOID_JSON=$(printf '%s' "$VALID_SINGLE_LIST_JSON" | sed 's/"id":"C1",//')
emit_json_plan "$NOID_JSON" > "$PLAN_NOID"
run_case "37-claim-missing-id-blocks" 2 "$PLAN_NOID"

# ---------------------------------------------------------------------------
# Tests 38-43: the 0G rounds ceiling has a RECORDED-override escape (2026-08-08).
#
# Every other verdict gate in check-plan-gates.sh admits a recorded operator
# override; the 0G rounds ceiling alone did not, so a human-sanctioned
# continuation past max_rounds was inexpressible rather than merely discouraged.
# The ceiling is NOT weakened — >3 still blocks by default (39) and the boundary
# is unchanged (42); only a RECORDED override admits it (40), it is announced
# rather than silent (41), and the marker is read per-gate so an override on a
# different gate does not reach it (43).
# ---------------------------------------------------------------------------

# assert_stderr NAME PLAN PATTERN — the hook's stderr must contain PATTERN.
assert_stderr() {
  local name="$1" plan="$2" pattern="$3" err
  err=$(bash "$HOOK" "$plan" 2>&1 >/dev/null)
  if printf '%s' "$err" | grep -qF "$pattern"; then
    echo "PASS  $name"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (stderr missing: $pattern)"
    echo "      got: $err"
    FAIL=$((FAIL+1))
  fi
}

# 38: rounds at 1, no override → pass (regression: the common path is untouched).
PLAN_R1="$TMP_ROOT/rounds-1-no-override.md"
emit_chain_src_plan C "n/a" 1 > "$PLAN_R1"
write_slice_i_receipts "$PLAN_R1"
run_case "38-rounds-within-ceiling-passes" 0 "$PLAN_R1"

# 39: rounds=14, NO override → still blocks. The ceiling is not weakened.
PLAN_R14="$TMP_ROOT/rounds-14-no-override.md"
emit_chain_src_plan C "n/a" 14 > "$PLAN_R14"
write_slice_i_receipts "$PLAN_R14"
run_case "39-rounds-over-ceiling-no-override-blocks" 2 "$PLAN_R14"

# 40: rounds=14 + a RECORDED 0G override → admitted (the fix).
PLAN_R14OV="$TMP_ROOT/rounds-14-override.md"
emit_chain_src_plan C "n/a" 14 > "$PLAN_R14OV"
write_slice_i_receipts "$PLAN_R14OV"
python3 "$PPG" plan-override 0G "$PLAN_R14OV" \
  --reason "operator reviewed the 14-round convergence and sanctions the continuation" \
  >/dev/null 2>&1
run_case "40-rounds-over-ceiling-with-override-passes" 0 "$PLAN_R14OV"

# 41: the same admitted run ANNOUNCES the override on stderr — never silent.
assert_stderr "41-override-admission-is-announced" "$PLAN_R14OV" \
  "admitted ONLY by the recorded 0G operator override"

# 42: rounds exactly at the ceiling (3), no override → pass (boundary unchanged).
PLAN_R3="$TMP_ROOT/rounds-3-boundary.md"
emit_chain_src_plan C "n/a" 3 > "$PLAN_R3"
write_slice_i_receipts "$PLAN_R3"
run_case "42-rounds-at-ceiling-boundary-passes" 0 "$PLAN_R3"

# 43: an override recorded on a DIFFERENT gate (0C) does NOT clear the 0G rounds
# ceiling — the check reads the 0G marker specifically, not "any override".
PLAN_R14OTHER="$TMP_ROOT/rounds-14-override-other-gate.md"
emit_chain_src_plan C "n/a" 14 > "$PLAN_R14OTHER"
write_slice_i_receipts "$PLAN_R14OTHER"
python3 "$PPG" plan-override 0C "$PLAN_R14OTHER" --reason "unrelated gap-analysis override" \
  >/dev/null 2>&1
run_case "43-override-on-other-gate-does-not-clear-rounds" 2 "$PLAN_R14OTHER"

# ----- Summary -----
echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

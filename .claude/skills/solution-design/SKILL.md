---
name: solution-design
version: "0.1"
description: AI orchestrator — coordinates Phase 2 (solution design + delivery slicing). Dispatches N parallel `solution-designer` (B1) calls with varied parameters, ranks them via an Opus selector, post-verifies the ranking via /double-check (class `check`), then auto-selects the verified top-ranked candidate and elaborates it to full requirement coverage (coverage-gated via /double-check (class `check`) until 0 gaps), presents the coverage-complete design in the topic's `<base>_DESIGN.md` (status pending-approval) for the operator to approve, invokes `solution-slicer` (B2) on approval, final-verifies via /double-check (class `gate`), and flips the Design to `chosen`. On ESCALATE at any post-verify seam, surfaces a two-option choice — (a) one more round, (b) proceed as is. Stateless skills, orchestrator-owns-persistence (C5).
allowed-tools: Read, Write, Agent, Skill
---

# /solution-design — Phase 2 Orchestrator

Coordinate Phase 2 (solution design + delivery slicing) in a single command. Dispatches `solution-designer` (B1) calls in parallel with varied parameters, ranks and post-verifies them, **auto-selects the verified top-ranked candidate and elaborates it to full requirement coverage** (coverage-gated by `/double-check`), presents the coverage-complete design for the operator to approve, dispatches `solution-slicer` (B2) once on the approved design, final-verifies the assembled output, and writes a durable `### Solution Alternative N` block into the topic's `<base>_DESIGN.md` (spine carries a pointer).

This skill is a **stateless orchestrator** that mirrors B2's pattern (parallel dispatch → consolidator → post-verify → user-on-PASS-only) and follows the AI-adapter hexagonal pattern: deterministic flow in code-style execution steps, AI judgment behind named ports (B1, Opus selector, B2, `/double-check`). Persistence is centralized — only B3 writes `_thought`. Producer-never-verifies is preserved by topology.

Main-session-only. Cannot be invoked from inside a subagent.

---

## Trigger

Activate when user says:
- `/solution-design`
- `/solution-design --help`
- "run solution design"
- "design a solution from my discovery"
- "phase 2"
- "design and slice"
- "I've finished clarification, what's next"

---

## When to Use

Activate in one situation:

**After `/clarification` completes and the `# Discovery` section has all 4 locked fields** — the user wants to design candidate solutions, have the verified-best auto-selected and elaborated to full requirement coverage, approve the finished design, get a delivery-slicing recommendation, and end with a durable record in their topic file.

Do NOT activate if `# Discovery` is absent or its 4 locked fields are missing — hard-fail at Step 1 with "Run `/clarification` first to lock the 4 Discovery fields."

Do NOT activate from inside a subagent — see Edge Case E12.

---

## Invariants

1. **Stateless skills.** B1 and B2 are never modified. B3 wraps them but does not edit their contracts. Only B3 writes `_thought`. *(C5 of S-H-v2-A; `~/.claude/rules/code_first_architecture.md:81–102`.)*

2. **Producer-never-verifies (external).** B3 does not verify its own ranking, coverage elaboration, or assembled output. `/double-check` is the external verifier at three code-defined seams (Step 4 ranking, Step 5.3 coverage, Step 9 final). *(`~/.claude/rules/code_first_architecture.md:112`.)*

3. **User sees only PASS results.** On ESCALATE at Step 4, Step 5.3, or Step 9, B3 surfaces the discrepancies verbatim and gives the user the (a) one-more-round / (b) proceed-as-is choice — it never presents an unverified result as final on its own, and does NOT write a `chosen` Design without resolving the seam.

4. **Two mandatory hard-gate triples.** At the design-approval gate (Step 5.4 — approve / request-changes / re-roll) and after slicing (Step 7), the user MUST make an explicit (a)/(b)/(c) choice. B3 cannot proceed past either gate without one. Step 5 no longer asks the user to *pick* a candidate — selection is automatic (5.1); the user approves the finished, coverage-complete design.

5. **Monotonic Alternative N.** Each B3 run that reaches Step 10 appends `### Solution Alternative N` where N = max(prior K)+1 (or 1 on first run). Prior alternatives are marked "superseded by Alternative N+1". N never decreases.

6. **B1 and B2 contracts unchanged.** B3 produces inputs that conform to B1's Input Contract and B2's Input Artifact. It consumes their outputs as-is. No contract changes to B1 or B2.

---

## Parameters

All optional (defaults shown):

| Flag | Default | Description |
|------|---------|-------------|
| `--sonnet N` | 1 | Number of Sonnet-model B1 candidate dispatches |
| `--opus N` | 1 | Number of Opus-model B1 candidate dispatches |
| `--candidates N` | 2 | Total candidate count override (overrides `--sonnet`+`--opus` if specified; default splits 1+1) |
| `--thought <path>` | (active topic state) | Path to `_thought` file containing locked `# Discovery` |
| `--against TEXT` | (auto-generated from desired outcome) | Additional `--against` text passed to `/double-check` at Steps 4 and 9 |
| `--rounds N` | 1 | Max convergence rounds passed to `/double-check` at both post-verify steps |
| `--help` | — | Print this Parameters table and stop |

`--sonnet + --opus` must be ≥ 1. If `--candidates N` is given, distribute as: floor(N/2) Opus + remainder Sonnet (e.g. N=3 → 1 Opus + 2 Sonnet; N=2 → 1 Opus + 1 Sonnet default).

**The candidate count is capped by the rigor dial** (class `panel` — B1 dispatches *generate* alternatives; they verify nothing). Resolve the ceiling before dispatching, passing the total the flags above produced:

```bash
python3 ${KIT_HOOKS_DIR}/rigor.py cap <total candidates> --count-only
```

At `thorough` and `standard` the default two candidates survive; at `light` and `minimal` the run designs one alternative, so there is nothing to rank — say that in the record rather than presenting a single candidate as a selection. An operator naming `--candidates`/`--sonnet`/`--opus` explicitly overrides the cap.

The three `/double-check` seams (Steps 4, 5.3, 9) are a different dial class and are resolved separately — see *Verification*.

---

## Input Contract

### Schema (primary surface — Surface 1)

```json
{
  "thought_path": "<optional: absolute path to the topic _thought file>",
  "sonnet_agents": 1,
  "opus_agents": 1,
  "rounds": 1,
  "against_supplement": "<optional free-text appended to the auto-generated --against string>"
}
```

The skill resolves the `# Discovery` section by reading `thought_path` (or the active topic state). All B1 dispatches receive the same locked fields; only `design_context_override` and `model` vary per dispatch.

### Field rules

| Field | Required | Semantics |
|-------|----------|-----------|
| `thought_path` | no | Absolute path to `_thought` file. If absent, skill reads from active topic state (`pre_plan_gates.py phase-show`). If neither resolves, hard-fail. |
| `sonnet_agents` | no (default 1) | Number of Sonnet B1 dispatches. |
| `opus_agents` | no (default 1) | Number of Opus B1 dispatches. |
| `rounds` | no (default 1) | Passed to `/double-check --rounds` at Steps 4 and 9. |
| `against_supplement` | no | Free-text appended to the auto-generated `--against` string for both post-verify calls. |

### Deterministic errors

If `# Discovery` is absent or any of the 4 locked fields is missing/placeholder, the skill returns:

```json
{
  "error": "<one of: discovery_section_absent, guiding_policy_missing, desired_outcome_missing, desired_solution_missing, metrics_omtm_missing>",
  "alternative_n": null
}
```

---

## Output Contract

### Assembled JSON (rendered into the `_DESIGN.md` at Step 10)

```json
{
  "alternative_n": 1,
  "date": "YYYY-MM-DD",
  "status": "chosen",
  "discovery_src_hash": "<12-hex SHA-256 prefix>",
  "selector_ranking_summary": "<one-line summary of rank with brief reasoning>",
  "chosen_candidate_id": "c1",
  "chosen_candidate": {
    "agreed_solution_description": "<markdown body, plain business words first>",
    "architecture_decisions": [
      {
        "area": "<noun phrase>",
        "choice": "<chosen approach>",
        "rationale": "<1–3 sentences>",
        "discovery_anchor": "<verbatim excerpt from locked Discovery>"
      }
    ],
    "ux_decisions": [],
    "cockburn_gate_results": {
      "abstraction_test": { "passed": true, "reasoning": "..." },
      "responsibility_alignment_test": { "passed": true, "reasoning": "..." },
      "evolution_test": { "notes": "..." },
      "communications_patterns_test": { "notes": "..." },
      "data_connectedness_test": { "notes": "..." },
      "data_variations_test": { "notes": "..." }
    },
    "confidence": "high",
    "assumptions": [],
    "discovery_anchors": [
      { "field": "guiding_policy", "excerpt": "<verbatim>" }
    ],
    "model_used": "opus"
  },
  "slices": [
    {
      "id": "S1",
      "name": "<noun phrase>",
      "description": "<plain business words>",
      "type": "implementation",
      "cohesion_rationale": "...",
      "single_reason_for_change": "...",
      "slicing_idea_applied": "walking_skeleton",
      "agent_choice": "routine",
      "agent_justification": "",
      "context_fit_assessment": "...",
      "depends_on": []
    },
    {
      "id": "S-final",
      "name": "Implementation verification",
      "description": "Confirm the whole plan shipped against the desired outcome.",
      "type": "implementation_verification",
      "cohesion_rationale": "Validation-V closing step.",
      "single_reason_for_change": "Desired outcome or aggregate scope changes.",
      "slicing_idea_applied": "knowledge_vs_implementation",
      "agent_choice": "routine",
      "agent_justification": "",
      "context_fit_assessment": "Verification work is lightweight; fits routine model.",
      "depends_on": ["S1"]
    }
  ],
  "verification_metadata": {
    "selector_ranking": {
      "double_check_verdict": "PASS",
      "claims_checked": 12,
      "rounds_run": 1,
      "against": "<the --against string used>"
    },
    "coverage": {
      "double_check_verdict": "PASS",
      "claims_checked": 14,
      "rounds_run": 1,
      "gaps_remaining": 0,
      "against": "<the Step-5.3 coverage --against string>"
    },
    "final_output": {
      "double_check_verdict": "PASS",
      "claims_checked": 25,
      "rounds_run": 1,
      "against": "<the --against string used>"
    }
  },
  "aggregation_metadata": {
    "candidates_evaluated": 2,
    "design_rounds": 1,
    "slice_rounds": 1,
    "auto_selected_candidate_id": "c1",
    "coverage_rounds": 1,
    "design_approved": true,
    "reclarification_gate_counts": {
      "category_i": 0,
      "category_ii": 0,
      "category_iii": 0
    }
  },
  "supersession_marker_written": false
}
```

### `### Solution Alternative N` markdown block (written into the `_DESIGN.md`)

```markdown
### Solution Alternative N
**Date:** YYYY-MM-DD
**Status:** pending-approval | chosen | superseded by Alternative N+1
**discovery_src_hash:** <12-hex>
**Selector ranking:** <one-line summary>

#### Chosen Solution Description
<agreed_solution_description from chosen B1 candidate, verbatim>

#### Architecture Decisions
<list from chosen B1 candidate>

#### UX Decisions
<list from chosen B1 candidate>

#### Coverage Map
<coverage_map: each Tier-1/2/3 requirement → covered / pulled-from-candidate-N / added-from-Discovery>

#### Change Log
<change_log: what the coverage step (Step 5.2) added or changed, and from which candidate or Discovery excerpt>

#### Slices (from B2)
<slices[] from B2 output rendered as the CANONICAL slice register — a pipe-table with the exact 7-column header below, one row per slice INCLUDING the closing implementation-verification slice (S-final). This canonical shape is what `/execute-plan`'s register reader parses most cleanly (A4 drift-prevention):>

| ID | Name | Type | Slicing idea | Model | Depends on | Write targets |
|----|------|------|--------------|-------|------------|---------------|
| S1 | <name> | implementation | <slicing_idea_applied> | routine | — | <comma-joined repo-relative paths> |
| ... | ... | ... | ... | ... | ... | ... |
| S-final | Implementation verification | implementation_verification | knowledge_vs_implementation | routine | S1, ... | — |

#### Verification
- Selector ranking: /double-check check (<flags as dispatched>) — PASS (claims_checked: N)
- Coverage (Step 5.3): /double-check check (<flags as dispatched>) — PASS, 0 gaps (claims_checked: K)
- Final Phase-2 output: /double-check gate (<flags as dispatched>) — PASS (claims_checked: M)
- Rigor tier: <tier, and where it came from — `rigor.py get`>

Record the flags the dial actually resolved, not the class alone: a reader months
later needs to know whether that PASS was a three-checker vote or one isolated
opinion, and the class name does not say.

#### Audit Trail
- Selector evaluated N candidates: <list of candidate IDs + brief per-candidate rationale>
- Design rounds (Step 5): <R_design> rounds total — per-round summary: round 1 → approve / request-changes / re-roll with feedback "<text>" or N/A; ...
- Auto-selected: rank-1 candidate <id>; coverage rounds: <R_cov>; design approved at round <R_design>
- Slice rounds (Step 7): <R_slice> rounds total — per-round summary: round 1 → user picked (a)/(b)/(c) with feedback "<text>" or N/A; ...
- Re-clarification gate: 0 (i), 0 (ii), 0 (iii) — or detail
```

### Field rules

| Field | Rules |
|-------|-------|
| `alternative_n` | Positive integer. Must be max(prior_K)+1, or 1 on first run. Never decreases across runs. |
| `status` | Enum: `"pending-approval"`, `"chosen"`, or `"superseded by Alternative N+1"`. Written as `pending-approval` at Step 5.4 (the coverage-complete design draft, before the operator approves); flipped to `chosen` at Step 10 after approval; updated to `superseded` when a future run writes N+1. |
| `discovery_src_hash` | The 12-hex SHA-256 prefix computed by `_discovery_locked_fields_hash` at Step 1. Must match the hash echoed by the chosen B1 candidate's input. |
| `chosen_candidate` | The full B1 output JSON for the auto-selected, coverage-complete design (Step 5). Field names MUST match B1's Output Contract verbatim (`agreed_solution_description`, `architecture_decisions`, `ux_decisions`, `cockburn_gate_results`, `confidence`, `assumptions`, `discovery_anchors`, `model_used`). No renaming. |
| `slices` | The full B2 `slices[]` output for the chosen solution. Field names MUST match B2's Output Contract verbatim. When B2 returns `slicing_needed: false`, `slices: []`. |
| `verification_metadata.selector_ranking` | Records the `/double-check` result from Step 4. `double_check_verdict` enum: `"PASS"` or `"ESCALATE"`. Only PASS results proceed past Step 4. |
| `verification_metadata.final_output` | Records the `/double-check` result from Step 9. Only PASS results proceed to Step 10. |
| `supersession_marker_written` | Boolean. `true` when Step 10 marked a prior alternative as "superseded". `false` on first run (N=1). |

---

## Execution

Twelve internal steps (Steps 0–11 + Step 7b sub-step). Run in order; no step is skipped except the assess-first short-circuit at Step 0.

### Step 0 — Help

If invoked with `--help` or no locked Discovery is available in session context, output the Parameters table and stop. Do not proceed to Step 1.

### Step 1 — Identify and load

1. Resolve the `_thought` file: use `--thought <path>` if provided; otherwise read active topic state from `pre_plan_gates.py phase-show`. If neither resolves, hard-fail with `error: discovery_section_absent`.
2. Read the `# Discovery` section. Locate and verify the 4 locked fields:
   - `## Guiding Policy` (locked 🔒)
   - `## Desired Outcome` (locked 🔒)
   - `## Desired Solution` (locked 🔒)
   - `## Metrics` with OMTM sub-line (locked 🔒)
3. If any field is absent, empty, or a placeholder (`TODO`, empty body), return the deterministic error envelope and stop. The skill MUST NOT proceed without all 4 locked fields.
4. Compute `discovery_src_hash`: call `_discovery_locked_fields_hash` from `pre_plan_gates.py` (12-hex SHA-256 prefix of the concatenated locked field bodies).
5. Scan for existing `### Solution Alternative K` headers in the topic's `<base>_DESIGN.md` if it exists, else (legacy) in the spine's `# Solution Design` section. Record max(K) as `prior_n` (0 if neither holds an Alternative — first run).

**Worktree auto-placement (S7 A14, `--worktree`-opt-in).** When `/solution-design --worktree` is used on a harness topic, place the session into the topic's worktree after the `_thought` resolves (step 1) and before the Step-2 B1 dispatch, so the `<base>_DESIGN.md` is authored inside the topic's isolated workspace. Because `/solution-design` runs downstream of `/clarification`, the worktree usually already exists — `place` is idempotent (a resume returns it, never a duplicate). Same two-step form as `work-start/SKILL.md:255-259` (retrieve the path from `place`, then guarded `cd`):

```bash
REPO="$(the configured source path)"
DEST="$(${KIT_HOOKS_DIR}/worktree-helper.sh place --repo "$REPO" --topic "<topic-slug>")" \
  || { echo "[solution-design] worktree placement did not complete" >&2; exit 1; }
cd "$DEST" || { echo "[solution-design] cannot cd into worktree $DEST" >&2; exit 1; }
```

Without `--worktree`, no placement (default unchanged). Projects-side binding activates with S9.

### Step 2 — Dispatch N parallel B1 calls

1. Determine N = `--sonnet` + `--opus` (default 2: 1 Sonnet + 1 Opus).
2. Build N distinct `design_context_override` strings, one per candidate. Starter library (pick N, no repeats):
   - "favor explicit user gates over implicit automation"
   - "optimize for fewest moving parts — minimize new abstractions"
   - "explore a synchronous step-by-step design with no background agents"
   - "minimize new abstractions — reuse existing code patterns wherever possible"
   If `design_feedback` is set (from a prior Step 5 round-trip (a)), append it to every variation string: `"<variation>; user feedback: <design_feedback>"`. This preserves per-candidate diversity while adding user steering.
3. Invoke B1 (`/solution-designer`) via Agent tool, all N calls in **parallel** (single message, multiple Agent tool calls). Per call:
   - `subagent_type: general-purpose`
   - `model: "sonnet"` or `"opus"` per the allocation
   - Pass B1 Input Contract JSON: `guiding_policy`, `desired_outcome`, `desired_solution`, `metrics`, `discovery_src_hash`, `design_context_override`, `model`.
4. Collect N candidate JSONs. Any error envelope returned by B1 is collected as-is (the selector handles all-error cases at Step 3).

### Step 3 — Synthesize ranking (Opus selector)

1. Write a ranking bundle to `~/.claude/state/solution_design/adhoc/draft_<sid8>_<ts>.md`:
   - The N candidate JSONs (full B1 output)
   - The locked Discovery fields (for grounding verification)
   - The selector rubric (verbatim below)
2. Invoke one **Opus** Agent call with `subagent_type: readonly-checker` that reads the artifact and emits a ranked recommendation per the schema below.

**Selector rubric** (written into the artifact and into the Opus agent prompt):

```
You are an independent Opus selector. Read the candidate solutions and the locked Discovery section at the artifact path provided.

Rank the N candidates using these criteria, in priority order:
1. Cockburn hard gates (DOMINANT): prefer candidates where both abstraction_test.passed and responsibility_alignment_test.passed are true. A failing candidate is ranked last unless ALL candidates fail.
2. Discovery anchor coverage: prefer candidates where all architecture and UX decisions cite a verbatim Discovery anchor (discovery_anchors[] covers all decisions).
3. Assumption parsimony: prefer candidates with fewer assumptions[] entries. Each assumption is a gap between Discovery and the design.
4. Discovery fit: prefer candidates whose agreed_solution_description addresses the desired_outcome and desired_solution without adding unrequested scope.

Produce a ranked list (rank 1 = best). For each candidate:
  - rank (integer, 1 = best)
  - candidate_id
  - strongest_on: one phrase naming the rubric dimension this candidate leads on
  - trade_offs: 1–2 sentences on what this candidate sacrifices vs. rank-1
  - per_criterion_notes: one sentence per criterion (cockburn_gates, anchor_coverage, assumption_parsimony, discovery_fit)

Produce an overall_argument: 2–3 sentences explaining why rank-1 is the best choice given Discovery.

If all candidates have errors (B1 error envelopes), report: error: "all_candidates_failed", no ranking.

Return JSON only. No prose before or after.
```

**Selector output schema:**
```json
{
  "ranked_candidates": [
    {
      "rank": 1,
      "candidate_id": "c1",
      "strongest_on": "cockburn_gates",
      "trade_offs": "...",
      "per_criterion_notes": {
        "cockburn_gates": "...",
        "anchor_coverage": "...",
        "assumption_parsimony": "...",
        "discovery_fit": "..."
      }
    }
  ],
  "overall_argument": "...",
  "error": null
}
```

If `selector.error == "all_candidates_failed"`: surface the error to the user, do NOT proceed to Step 4. Run ends without writing any Alternative.

### Step 4 — Post-verify ranking

Build the `--against` string:
```
<desired_outcome>; ranking rubric: Cockburn hard gates dominant + Discovery anchor coverage + assumption parsimony + Discovery fit
```
Append `against_supplement` if provided.

Invoke:
```
/double-check --class check --rounds 2 --against "[against_string]"
```

- **PASS (0 discrepancies):** Record in `verification_metadata.selector_ranking`. Proceed to Step 5.
- **ESCALATE (`max_rounds` reached with unresolved discrepancies):** Surface the discrepancies verbatim and present the user a two-option choice (do NOT default-proceed):
  - **(a) One more round** — re-invoke `/double-check` with the same `--against` string; round counter increments past `max_rounds`; fresh isolated checkers.
  - **(b) Proceed as is** — accept the unresolved discrepancies. Record `verification_metadata.selector_ranking.double_check_verdict = "ESCALATE"` and the discrepancy list, proceed to Step 5.
  - No `override_reason` capture — the user's pick is recorded.

### Step 5 — Auto-select, ensure coverage, design-approval gate

The selector ranking (Step 3) is post-verified (Step 4). B3 does **not** ask the user to pick a candidate. Instead it auto-takes the verified top-ranked candidate, elaborates it to full requirement coverage, and gates the **finished** design for approval. The user's judgment moves from picking a raw candidate to approving a coverage-complete design.

**5.1 Auto-select.** Take the verified rank-1 candidate as `chosen_candidate`; record `auto_selected_candidate_id`. There is no user override of rank — the operator judges the finished, coverage-complete design at 5.4, not the raw ranking.

**5.2 Ensure coverage (producer step — does NOT self-verify).** Elaborate the chosen candidate so it covers every requirement the locked Discovery imposes:
- Requirement checklist: **Tier 1** = the 4 locked fields (Desired Outcome, Desired Solution, Guiding Policy, OMTM); **Tier 2** = Problem (under `# Idea`), Scope in/out, surfaced rules; **Tier 3** = resolved Q&A.
- For each requirement the chosen candidate does not fully cover, close the gap — **prefer pulling the relevant decision from one of the other candidates** over inventing one; anchor every pulled/added decision to a verbatim Discovery excerpt. If no candidate covers it, fill from Discovery, anchored, and flag it as added.
- Apply the Guiding Policy as a **hard gate** on every change; keep both Cockburn hard gates passing.
- Emit the elaborated design in the **B1 Output Contract verbatim**, plus `coverage_map` (each requirement → `covered` / `pulled-from-candidate-N` / `added-from-Discovery`) and `change_log`.

**5.3 Coverage verify (external evaluator — producer-never-verifies).** Invoke:
```
/double-check --class check --rounds 2 --against "the design covers every Tier 1/2/3 requirement from the _thought, stays within the Guiding Policy, anchors every decision to a verbatim Discovery excerpt, and passes both Cockburn hard gates"
```
- **PASS (0 gaps):** record in `verification_metadata.coverage`. This PASS is the pre-approval gate. Proceed to 5.4.
- **ESCALATE:** surface the gaps verbatim; two-option choice — **(a) one more round** (the coverage producer revises in 5.2, then re-verify) / **(b) proceed as is** (record `verification_metadata.coverage.double_check_verdict = "ESCALATE"`). Never loop 5.2/5.3 without this user gate.

**5.4 Present for approval (single approve gate).** Write the coverage-complete design to the topic's `_DESIGN.md` with `status: pending-approval` using the **Step-10 write mechanics** (Parent line + spine pointer), including `coverage_map` + `change_log`, **no Slices yet**. Show the operator: `agreed_solution_description` (first paragraph), `confidence`, hard-gate booleans, and the `coverage_map`. Present three explicit choices (B3 CANNOT proceed without one):

> **(a) Approve** — accept the coverage-complete design. Proceed to slicing (Step 6); Step 10 flips `status` to `chosen`.
>
> **(b) Request changes** — provide steering text. B3 re-dispatches N B1 agents with your feedback injected into every `design_context_override` (loop to Step 2); the `pending-approval` draft is replaced on the next pass. `design_round` increments.
>
> **(c) Re-roll** — fresh N B1 agents, no feedback, fresh isolated context (loop to Step 2). `design_round` increments.

**On (a):** record approval. Proceed to Step 6.
**On (b):** record `design_feedback`; increment `design_round`; loop to Step 2.
**On (c):** increment `design_round`; loop to Step 2 (clean re-roll).

No cap on design rounds. The user gates approval, not candidate selection.

### Step 6 — Dispatch B2 once

Invoke `/solution-slicer` via the Skill tool with:
- `plan_path_or_content`: the chosen candidate's `agreed_solution_description` (inline markdown)
- `desired_outcome`: the locked `## Desired Outcome` field body

B2 internally post-verifies its own recommendation via its built-in `/double-check --class gate`. B3 consumes B2's Output Contract as-is (`assessment`, `slices[]`, `aggregation_metadata`, `verification_metadata`, `confidence`, `assumptions`).

If B2 returns an error envelope: surface the error, do NOT proceed to Step 7.

If B2 returns `assessment.slicing_needed: false`: note this for the user at Step 7; `slices: []` will be recorded.

### Step 7 — Slice-approval hard gate

Present the slice recommendation to the user:
1. Show `assessment.rationale` (why slicing is or is not recommended).
2. If `slicing_needed: true`: show the `slices[]` table (id, name, description, type, agent_choice).
3. Show `verification_metadata.double_check_verdict` from B2.
4. Present three explicit choices (B3 CANNOT proceed without one):

> **(a) Feedback — revise the slicing**
> Provide steering text for a new slicing round. B3 will re-invoke B2 with your feedback appended to `desired_outcome`. Slicing-round counter increments.
>
> **(b) Approve — accept the slices**
> Proceed to assemble the Phase-2 output (Step 8).
>
> **(c) Try another slicing round — fresh independent B2 agents**
> Fresh re-invocation of B2 with the same inputs, no feedback injected. B2's internal multi-perspective dispatch ensures isolated context. Slicing-round counter increments.

**On (a):** Append feedback to `desired_outcome` for next B2 call (concatenated: `"<original desired_outcome>; slicing feedback: <slice_feedback>"`). Increment `slice_round` counter. Loop back to Step 6.

**On (b):** Proceed to Step 7b (re-clarification gate), then Step 8.

**On (c):** Fresh B2 re-invocation with same inputs. Increment `slice_round` counter. Loop back to Step 6.

No cap on slicing rounds. The user gates each loop.

### Step 7b — Re-clarification gate (orthogonal, triggered by content inspection)

After user approves at Step 7 (b), inspect the chosen B1 candidate output + B2 output for Discovery gaps. This step triggers only if inspection reveals gaps:

1. **Inspect gaps:** For each `assumptions[]` entry in the chosen candidate, and for each B2 assessor note that references a missing Discovery element, classify:
   - **(i) Q&A new info** — new factual content not in Discovery (e.g. an external constraint the user knows): write to `## Q&A` with `[origin: solution-design SID:<sid8>]` tag. No re-clarification. Proceed.
   - **(ii) Scope refinement** — a clarification of scope boundaries already implicit in Discovery: write to `## Q&A` with same origin tag. No re-clarification. Proceed.
   - **(iii) Locked-field conflict** — an assumption that contradicts a locked field (e.g. "assumes Desired Solution allows X" but Desired Solution explicitly forbids X): surface the tension to the user with:
     - The conflicting assumption quoted verbatim
     - The locked field excerpt quoted verbatim
     - Three choices: **Revise** (invoke `/clarification --from <thought-path>`, B3 stops) / **Reject** (discard this assumption, proceed treating the locked field as authoritative) / **Override** (note the override explicitly in `aggregation_metadata`, proceed)

2. **Re-clarification counter:** each category-(iii) surface increments the counter. Hard-stop on 3rd surface (counter==2 + new attempt): "Re-clarification gate hard-stopped after 2 round-trips. Please run `/clarification --from <path>` directly to revise the locked fields, then re-invoke `/solution-design`."

3. If user picks **Revise** at any category-(iii) surface: invoke `/clarification --from <thought-path>`, do NOT write any Alternative, stop.

4. If no gaps detected: proceed directly to Step 8.

### Step 8 — Assemble the slices + verification additions

The design **body** — the `### Solution Alternative N` header (N computed at Step 5.4 / Step-10 Stage 1), `**Status:** pending-approval`, `discovery_src_hash`, Chosen Solution Description, Architecture/UX Decisions, Coverage Map, Change Log — was already written to `<base>_DESIGN.md` at Step 5.4. Step 8 assembles only the **remaining** sections, to be merged in at Step 10 Stage 2:
1. `selector_ranking_summary` = one-line from the selector's `overall_argument`.
2. `Slices (from B2)` = B2's `slices[]` rendered as the **canonical 7-column** `#### Slices` pipe-table (A4 drift-prevention — so `/execute-plan`'s reader parses new registers cleanly). Use the exact header `| ID | Name | Type | Slicing idea | Model | Depends on | Write targets |` with a separator row, mapping each slice's JSON fields verbatim: `id`→ID, `name`→Name, `type`→Type (`implementation` / `implementation_verification`), `slicing_idea_applied`→Slicing idea, `agent_choice`→Model (`routine` / `more_capable`), `depends_on`→Depends on (comma-joined, `—` when empty), `write_targets`→Write targets (comma-joined repo-relative paths, `—` when empty). Always include the closing **S-final** row with `type: implementation_verification` so the reader's non-closing count is correct.

   **Both halves of this hop must stay in step.** `/solution-slicer` emits the per-slice `write_targets` *field*; this step renders the *column*. Extending only one leaves the column unrendered and `write_targets` resolving to `()` in `/execute-plan`'s register reader — a functional no-op that looks like a working feature. The column is what `/execute-plan`'s entry gate narrows an in-flight block to, so an omitted path silently stops being gated rather than failing loudly.
3. `Verification` = all three `/double-check` verdicts (coverage at 5.3, B2 slicing, Step-9 final) with `claims_checked`.
4. `Audit Trail` = full round-by-round summary.
5. If N > 1, prepare the supersession edit: locate `### Solution Alternative N-1` in `<base>_DESIGN.md` and change its `**Status:**` line to `superseded by Alternative N` (applied at Step 10). Set `supersession_marker_written: true` in aggregation_metadata.

### Step 9 — Final post-verify

Build the `--against` string:
```
<desired_outcome>; the assembled Phase-2 output (chosen B1 candidate + B2 slices) should coherently address the Discovery locked fields without contradicting them; verification_metadata must record both /double-check verdicts
```
Append `against_supplement` if provided.

Invoke:
```
/double-check --class gate --rounds 1 --against "[against_string]"
```
(Per S-H-v2-A spec — the heavier budget for the durable record.)

- **PASS:** Record in `verification_metadata.final_output`. Proceed to Step 10.
- **ESCALATE (`max_rounds` reached with unresolved discrepancies):** Surface the discrepancies verbatim and present the user a two-option choice (do NOT default-proceed):
  - **(a) One more round** — re-invoke `/double-check` with the same `--against` string; round counter increments past `max_rounds`; fresh isolated checkers.
  - **(b) Proceed as is** — accept the unresolved discrepancies. Record `verification_metadata.final_output.double_check_verdict = "ESCALATE"` and the discrepancy list, proceed to Step 10. The Alternative is written with the ESCALATE disposition embedded in the Verification audit trail.
  - No `override_reason` capture — the user's pick is recorded.

### Step 10 — Two-stage write of the Agreed Solution to `_DESIGN.md` + spine pointer

Per the bookkeeping model (OQ18 pointer-manifest; `~/.claude/rules/bookkeeping-model.md` §4 / §8), the Agreed Solution lives in a separate `_DESIGN.md` file and the spine's `# Solution Design` is a **pointer manifest** — wikilinks to the Design, no inline body.

Resolve `design_path = <spine_dir>/<base>_DESIGN.md`, where `<base>` is the spine filename minus `_THOUGHT.md` (reuses the spine's slug + any timestamp); its wikilink stem is `<base>_DESIGN`. Let `<spine_stem>` be the spine filename minus `.md`.

**Stage 1 — at Step 5.4 (the `pending-approval` draft; "Step-10 write mechanics" referenced there).** Create `design_path` if absent as a coordinated single Write — read → compose → one Write call:
   - **First line** `Parent: [[<spine_stem>]]` — the bidirectional contract requires it (G3); without it the bookkeeping hook flags the Design.
   - `## Agreed Solution Description` with the chosen candidate's `agreed_solution_description`.
   - Compute `alternative_n` (scan `design_path` for `### Solution Alternative K`; `n = max(K)+1`, or 1 if none) and append the `### Solution Alternative N` block with `**Status:** pending-approval`, Chosen Solution Description, Architecture/UX Decisions, Coverage Map, Change Log — **no Slices, no final Verification yet**.
   - **Spine pointer:** ensure `- [[<base>_DESIGN]]` exists under the spine's `# Solution Design` (create the header before `# Implementation Details`, or at end of file if absent — pointer manifest, no inline body). The spine must wikilink the Design (G4).

**Stage 2 — here, after approval (Step 5.4a) + slicing (Steps 6–7) + Step-9 PASS.** Update the same `design_path` (read → edit):
   - Flip the Alternative N `**Status:**` line `pending-approval → chosen`.
   - Append `#### Slices (from B2)` and the final `#### Verification` (coverage + B2 slicing + Step-9 verdicts) + `#### Audit Trail` assembled at Step 8.
   - **Mark prior alternative superseded** (if N > 1): Edit the `**Status:**` line of `### Solution Alternative N-1` **within `design_path`**.
   - The spine pointer from Stage 1 already exists — ensure it is present (idempotent).

**Backward-compat:** existing spines that carry an inline `### Solution Alternative N` are still consumed by `/plan` Mode A via its dual-read (DESIGN-or-inline). This file-based persistence applies to new `/solution-design` runs; a topic already holding an inline Alternative is left as-is unless re-run.

### Step 11 — Handoff

Present the user-facing summary:
- Alternative N written: `### Solution Alternative N` (date, hash, candidates evaluated, rounds)
- Selector ranking: `/double-check --class check` (<flags as dispatched>) — PASS
- Coverage (Step 5.3): `/double-check --class check` (<flags as dispatched>) — PASS, 0 gaps
- Final output: `/double-check --class gate` (<flags as dispatched>) — PASS
- B2 assessment: `slicing_needed: true/false`, `slices: N`
- **Suggest next step:** Run `/plan` to enter plan mode with the chosen Agreed Solution Description and slices as Coherent Actions input.

If user chose Revise at Step 7b: suggest `/clarification --from <thought-path>` instead.

---

## Invocation Surfaces

The skill is invokable from three surfaces. All three resolve the same locked Discovery, dispatch the same B1/B2 calls, and produce the same JSON output. The surface layer is the only changing locus when adding a new caller (Cockburn Evolution Test — single-locus change).

### Surface 1 — Primary CLI (`/solution-design`)

The user runs `/solution-design` directly after `/clarification`. B3 reads the active topic state or `--thought <path>`. Full 12-step Execution. Writes `### Solution Alternative N` to the `<base>_DESIGN.md` (+ spine pointer under `# Solution Design`).

### Surface 2 — `/plan` Slice I fallback

When the user runs `/plan` directly and Phase 2 was skipped, Slice I (the `/plan` enhancement) invokes `/solution-design --inline` as a fallback to fill the Coherent Actions input. In `--inline` mode:
- B3 runs the full Execution (Steps 1–9)
- At Step 10, B3 writes the `<base>_DESIGN.md` (+ spine pointer) AND returns the assembled output JSON to the caller (Slice I)
- Slice I records time + tokens to `Stats.md` — that instrumentation belongs to Slice I, NOT to B3

### Surface 3 — Standalone CLI

```
/solution-design --thought <path-to-_thought> [--sonnet N] [--opus N] [--rounds N]
```

For one-off iteration outside the normal flow — e.g., debugging a Discovery, testing the selector, iterating on fixtures. In standalone mode:
- B3 runs the full Execution (Steps 1–9)
- At Step 10: outputs the assembled Alternative JSON to stdout ONLY — no `_thought` write (no topic context guaranteed)
- Confirmed editorial: E14 = JSON to stdout, no write.

---

## Optionality Boundary

This skill does NOT decide whether Phase 2 should be run at all. That decision belongs to the user. When invoked, B3 always executes (subject to the deterministic Step 1 hard-fail on missing/unlocked Discovery).

When Phase 2 is optional (e.g., a trivial change where the solution is obvious), the user simply skips `/solution-design` and runs `/plan` directly. Slice I may invoke B3 as a fallback (Surface 2), but Slice I decides whether to invoke it.

---

## Edge Cases

| # | Condition | Handling |
|---|-----------|----------|
| E1 | `# Discovery` absent or unlocked | Hard-fail at Step 1 with deterministic error envelope. Do not proceed. |
| E2 | `discovery_src_hash` from a B1 candidate doesn't match Step 1 hash | Stale candidate. Surface to user: "Candidate <id> hash mismatch — re-dispatching." Re-dispatch that candidate (one retry). If still mismatch: reject and exclude from selector ranking. Note in `aggregation_metadata`. |
| E3 | All N B1 candidates return error envelopes | Selector Step 3 detects `error: "all_candidates_failed"`. Surface error. No Alternative written. Run ends. |
| E4 | User keeps choosing request-changes / re-roll at Step 5.4 | No cap; each loops to Step 2 (request-changes injects feedback; re-roll is clean). No Alternative is written until the user approves at 5.4 (a). |
| E5 | Selector post-verify ESCALATE (Step 4) | Surface discrepancies verbatim; user picks (a) one more round or (b) proceed-as-is (records ESCALATE, continues to Step 5). No `chosen` Design until resolved. |
| E6 | B2 returns error envelope | Surface error to user. Do NOT proceed to Step 7. Run ends. |
| E7 | Final post-verify ESCALATE (Step 9) | Surface discrepancies verbatim; user picks (a) one more round or (b) proceed-as-is (records ESCALATE in `verification_metadata.final_output`, proceeds to Step 10 with the ESCALATE disposition embedded). |
| E8 | User picks Revise at Step 7b category-(iii) | Invoke `/clarification --from <thought-path>`. Do NOT write Alternative. Stop. |
| E9 | First run on a topic (no `_DESIGN.md` yet) | Step 10 creates the `<base>_DESIGN.md` (with `Parent:` line) + the spine `# Solution Design` pointer + writes Alternative 1. `supersession_marker_written: false`. |
| E10 | N-th run (N ≥ 2) | Step 10 appends Alternative N + marks Alternative N-1 superseded. `supersession_marker_written: true`. |
| E11 | Re-clarification counter at 2; user attempts 3rd category-(iii) surface | Hard-stop. Tell user to run `/clarification --from <path>` directly and then re-invoke `/solution-design`. Counter resets on fresh invocation. |
| E12 | Invoked from inside a subagent | Reject: "solution-design is main-session-only — cannot be invoked from inside a subagent." |
| E13 | `/plan` Slice I fallback (Surface 2, `--inline`) | B3 runs end-to-end and both writes `_thought` AND returns the Alternative JSON to Slice I. Slice I records time + tokens. |
| E14 | Standalone CLI (Surface 3) | End-to-end run; output is the Alternative JSON to stdout. No `_thought` write. Confirmed editorial. |
| E15 | Coverage gate (Step 5.3) cannot reach 0 gaps within rounds | ESCALATE: surface the remaining gaps verbatim; user picks (a) one more round or (b) proceed-as-is. Record in `verification_metadata.coverage`. No silent pass. |
| E16 | User picks (b) Request changes at Step 5.4 | `design_feedback` injected into every B1 dispatch's `design_context_override` (appended); loop to Step 2; the `pending-approval` draft is replaced next pass. `design_round` increments. |
| E17 | User picks (c) Re-roll at Step 5.4 | No feedback injection. Same `design_context_override` variation library. Fresh isolated B1 agents via Agent tool. `design_round` increments. |
| E18 | User picks (a) Feedback at Step 7 | Feedback concatenated to `desired_outcome` for B2 re-invocation. B2 sees the augmented outcome. Slicing-round counter increments. |
| E19 | User picks (c) Try another slicing round at Step 7 | Fresh B2 re-invocation with same original inputs. No feedback injection. Round counter increments. |
| E20 | User approves design (Step 5) then rejects slices (Step 7) multiple times | Step 7 hard gate stays in place per round. No special handling. User can use (a) feedback to steer B2 ("regenerate slices for the same approved design with constraint X"). |

---

## Eval Harness

This skill ships a Python eval companion (`eval.py`). The harness is **hybrid by design**, following B1/B2 precedent:

| Layer | Mechanism | Lives in |
|-------|-----------|----------|
| Deterministic checks | Python script — input fixture schema validation, output schema validation, cross-field checks, SHA-256 change-impact diff | `~/.claude/skills/solution-design/eval.py` |
| Judgment-quality eval | `/double-check` invocations against fixtures | Caller's responsibility (not bundled into `eval.py`) |

### Fixtures

`~/.claude/skills/solution-design/fixtures/` holds 3 starter fixtures:

| File | Shape |
|------|-------|
| `sample-pass.json` | 2 candidates (1 Sonnet + 1 Opus), PASS on both post-verifications, first run (prior_alternative_n: null) |
| `sample-escalate-on-verify.json` | 2 candidates, ESCALATE on final post-verify — exercises E7 path |
| `sample-reclarification.json` | 1 candidate with a locked-field conflict assumption — exercises E8 / Step 7b category-(iii) path |

### Running the deterministic eval

```bash
# Validate one fixture + a captured skill output against the schema, and diff against a baseline:
python3 ~/.claude/skills/solution-design/eval.py \
  --fixture ~/.claude/skills/solution-design/fixtures/sample-pass.json \
  --skill-output /tmp/b3-run-2026-05-22.json \
  [--baseline /tmp/b3-run-prior.json]
```

The script emits a JSON report to stdout with:
1. `fixture_schema_valid: true | false`
2. `output_schema_valid: true | false`
3. `cross_field_errors`: list of cross-field check failures (empty = clean)
4. `change_impact`: `{sha256: "<hex>", diff: "<unified-diff>" | null}`

Exits 0 if both schemas valid and no cross-field errors. Exits 1 otherwise.

### Cross-field checks (deterministic)

| Check | Error |
|-------|-------|
| `output.discovery_src_hash` absent or empty | "output.discovery_src_hash is required" |
| `output.discovery_src_hash != fixture.discovery_src_hash` | "output.discovery_src_hash mismatch — chosen candidate was dispatched against a different Discovery state" |
| `fixture.prior_alternative_n > 0` AND `output.alternative_n <= fixture.prior_alternative_n` | "monotonic-N rule violated: output.alternative_n must be greater than prior_alternative_n" |
| `fixture.prior_alternative_n > 0` AND `output.supersession_marker_written != true` | "prior alternative not marked superseded — supersession_marker_written must be true when prior_alternative_n > 0" |
| `output.verification_metadata.selector_ranking` absent | "verification_metadata.selector_ranking is required (Step 4 /double-check result)" |
| `output.verification_metadata.final_output` absent | "verification_metadata.final_output is required (Step 9 /double-check result)" |

---

## Source

- `~/.claude/rules/code_first_architecture.md` lines 81–102 — AI Adapter / judgment-port pattern.
- `~/.claude/rules/code_first_architecture.md:112` — Producer-never-verifies invariant.
- `${KIT_HOOKS_DIR}/pre_plan_gates.py:153-158` — `DISCOVERY_LOCKED_FIELDS` (the four locked field names).
- `${KIT_HOOKS_DIR}/pre_plan_gates.py:178-189` — `_discovery_locked_fields_hash` (12-hex SHA-256 prefix).
- `~/.claude/skills/solution-designer/SKILL.md` — B1 Input Contract + Output Contract (ports B3 consumes).
- `~/.claude/skills/solution-slicer/SKILL.md` — B2 Input Artifact + Output Contract (ports B3 consumes).
- `~/.claude/skills/double-check/SKILL.md` — post-verification pattern (multi-agent dispatch + PASS-only user-facing results).
- `~/.claude/rules/factcheck-convergence.md` §2 — independence / isolated context.
- `~/.claude/rules/plan-gates.md` Gate 0a OQ19 — Plain business words first.
- `Thoughts/workflow-phases-redesign_THOUGHT.md` S-H-v2-A entry — 3-skill split + V1 hybrid selector + C1–C5 constraints.
- `Thoughts/workflow-phases-redesign_H_v2_B3_PLAN.md` — locked plan this skill implements.

---

## Non-goals

- This skill does **not** modify B1 or B2 contracts. Ports are consumed as-is.
- It does **not** decide whether Phase 2 should run (Optionality Boundary).
- It does **not** select among candidates by formula. An Opus selector ranks qualitatively and B3 auto-takes the verified rank-1 (Step 5.1); the user gate owns **approval of the finished, coverage-complete design** (Step 5.4), not candidate selection.
- It does **not** verify its own output. Producer-never-verifies via `/double-check` at Steps 4, 5.3, and 9.
- It does **not** write a `chosen` (final) Design until Step 9 post-verify PASSes. A `pending-approval` draft is written at Step 5.4 only after the coverage gate (Step 5.3) PASSes, and is flipped to `chosen` only at Step 10. ESCALATE at any seam = no advance past it without the user's (a)/(b) choice.
- It does **not** produce more than one `### Solution Alternative N` per invocation.
- It does **not** change the `# Discovery` section. Discovery is the input; `# Solution Design` is the output.
- It does **not** bypass the re-clarification counter cap. Hard-stop at round-trip 3.

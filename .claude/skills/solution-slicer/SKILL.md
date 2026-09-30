---
name: solution-slicer
description: Rule-driven delivery-slicing assessor + multi-perspective recommender for
  plans. Assesses whether a plan needs partitioning into delivery slices; when it does,
  gathers independent assessor perspectives, consolidates one recommendation that always
  ends with a closing implementation-verification slice, and confirms it via /double-check
  1,1,2 against the desired outcome and slicing rules before presenting. On ESCALATE,
  surfaces a two-option choice to the user — (a) one more round, (b) proceed as is.
allowed-tools: Read, Write, Agent, Skill
---

# /solution-slicer — Delivery Slicing Assessor

Assess a draft plan and either confirm it needs no slicing, or produce a verified, rule-driven partition into delivery slices — each ending with a closing implementation-verification slice that confirms the whole plan shipped.

This skill is a **self-orchestrating multi-perspective assessor** (per `~/.claude/rules/code_first_architecture.md` §AI Adapter, lines 81–102). It mirrors the `/double-check` pattern: multi-agent dispatch + consolidator + post-verification. The consolidator is the producer; `/double-check` is the external verifier. Producer-never-verifies is preserved by the topology.

---

## Trigger

Activate when user says:
- `/solution-slicer`
- `/solution-slicer --help`
- "recommend delivery slices"
- "slice this plan"
- "assess this plan for slicing"
- "should this be sliced"
- "partition this plan into sessions"

---

## When to Use

Activate in two situations:

1. **Technical-design step (Phase 2 / `/solution-design`)** — after clarification, when the agreed solution needs to be partitioned into delivery sessions before handing off to `/plan`.
2. **Planning-step dedicated session (`/plan` / Slice I)** — a dedicated session inside plan mode invokes this skill to assess whether the plan's Coherent Actions should be partitioned across implementation sessions.

In both situations the skill consumes the same Input Artifact and emits the same Output Contract. The skill does not decide whether slicing should be attempted at all — when invoked, it always assesses.

---

## Invariants

The skill MUST obey the following invariants. Non-negotiable.

1. **Assess-before-slice.** The skill's first step is always to decide whether slicing is needed. The default outcome is "do not slice"; slicing is recommended only when the assessor finds a clear violation of the four slicing rules in the un-sliced plan.
2. **Slicing is for delivery efficiency + trust — not scope reduction.** The recommended slices cover 100% of the planned scope. No work is dropped during delivery partitioning.
3. **Closing implementation-verification slice always present.** Every recommended slice sequence ends with a slice of `type: "implementation_verification"` whose job is to confirm the whole plan shipped against the desired outcome. If the consolidated list does not already end with one, Step 4 appends it.
4. **Producer-never-verifies (external).** The consolidator (this skill) does not verify its own consolidated recommendation. `/double-check` is the external verifier invoked at Step 5. User sees only PASS results.
5. **Multi-perspective dispatch.** Assessors are `readonly-checker` subagents with isolated context — no shared conversation history, no producer reasoning injected.
6. **Stateless persistence-wise.** The skill MUST NOT write `_thought` files. It MUST NOT call `pre_plan_gates.py` write-side commands. The artifact write at Step 3 is volatile per-invocation (`~/.claude/state/solution_slicer/adhoc/`).

---

## Parameters

All optional (defaults shown):

| Flag | Default | Description |
|------|---------|-------------|
| `--routine N` | 1 | Number of routine-model (Sonnet) assessors |
| `--more-capable N` | 1 | Number of more-capable-model (Opus) assessors |
| `--rounds N` | 1 | Max convergence rounds |
| `--against TEXT` | (none) | Desired outcome text for post-verification. The skill auto-appends the four slicing rules; the user need only pass the desired outcome. |

Minimum 1 assessor total (`--routine + --more-capable ≥ 1`).

Note: defaults here (1,1,1) intentionally diverge from `/double-check`'s defaults (3,0,2) — this skill's role-based model-pair is editorial per user rule #7.

---

## Input Artifact

### Schema

```json
{
  "plan_path_or_content": "<file path (string starting with / or ~/) OR inline plan markdown body>",
  "desired_outcome": "<text describing the operational outcome the plan aims to achieve>",
  "context_budget_per_agent": {
    "routine": "<optional — estimated token budget for this plan content, e.g. '8000 tokens'>",
    "more_capable": "<optional — estimated token budget for the more-capable model>"
  }
}
```

### Field rules

| Field | Required | Semantics |
|-------|----------|-----------|
| `plan_path_or_content` | yes | File path → read with the Read tool. Inline markdown → use directly. If a path is given and the file does not exist, return a deterministic error. |
| `desired_outcome` | yes | Plain-text description of the operational outcome. The skill appends the four slicing rules to this string when calling `/double-check --against`. |
| `context_budget_per_agent` | no | Token-budget hints the caller passes so the assessor can reason about context-window fit. If absent, the skill uses no numeric budget; assessors apply qualitative judgment for context-window fit. |

### Deterministic errors

If either required field is missing, empty, or whitespace-only, return:

```json
{
  "error": "<one of: plan_path_or_content required, desired_outcome required, plan file not found: <path>>",
  "assessment": null,
  "slices": [],
  "aggregation_metadata": null,
  "verification_metadata": null,
  "confidence": null,
  "assumptions": []
}
```

Do not dispatch assessors on a deterministic error.

---

## Output Contract

### Schema

```json
{
  "assessment": {
    "slicing_needed": true,
    "rationale": "<why slicing is or is not recommended>"
  },
  "slices": [
    {
      "id": "S1",
      "name": "<short noun phrase for this slice>",
      "description": "<what this slice implements, in plain business words>",
      "type": "implementation",
      "cohesion_rationale": "<why this set of changes belongs together>",
      "single_reason_for_change": "<the single reason this slice would change — the cohesion axis>",
      "slicing_idea_applied": "walking_skeleton",
      "agent_choice": "routine",
      "agent_justification": "",
      "context_fit_assessment": "<assessment that this slice fits the chosen agent's working capacity for one focused session>",
      "depends_on": [],
      "write_targets": ["<repo-relative path this slice will write>"]
    },
    {
      "id": "S-final",
      "name": "Implementation verification",
      "description": "<what is confirmed — the whole plan shipped against the desired outcome>",
      "type": "implementation_verification",
      "cohesion_rationale": "Validation-V closing step — confirms the aggregate delivery matches the desired outcome.",
      "single_reason_for_change": "Desired outcome or aggregate scope changes.",
      "slicing_idea_applied": "knowledge_vs_implementation",
      "agent_choice": "routine",
      "agent_justification": "",
      "context_fit_assessment": "<assessment for the verification slice>",
      "depends_on": ["S1", "..."],
      "write_targets": []
    }
  ],
  "aggregation_metadata": {
    "assessors_run": ["routine-1", "more_capable-1"],
    "disagreements": []
  },
  "verification_metadata": {
    "double_check_verdict": "PASS",
    "claims_checked": 12,
    "rounds_run": 1,
    "against": "<the --against string used>"
  },
  "confidence": "high",
  "assumptions": []
}
```

### Field rules

| Field | Rules |
|-------|-------|
| `assessment.slicing_needed` | Boolean. When `false`: `slices` MUST be `[]` and `rationale` MUST be non-empty. |
| `slices` | Non-empty list when `slicing_needed == true`; empty list `[]` when `slicing_needed == false`. |
| `slices[-1].type` | MUST be `"implementation_verification"` when `slicing_needed == true`. Code-enforced in `eval.py`. |
| `type` | Enum: `"implementation"` or `"implementation_verification"`. No other values. |
| `slicing_idea_applied` | For `type == "implementation"` slices: MUST be one of `{walking_skeleton, sentence_extension, partial_step, different_ways, data_rules, knowledge_vs_implementation}`. For `type == "implementation_verification"` slices: no constraint (the verification slice is not bounded by the six ideas). |
| `agent_choice` | Enum: `"routine"` or `"more_capable"`. Default is `"routine"`. |
| `agent_justification` | MUST be non-empty when `agent_choice == "more_capable"`. MUST be empty string `""` when `agent_choice == "routine"`. |
| `context_fit_assessment` | MUST be non-empty for every slice. |
| `single_reason_for_change` | MUST be non-empty for every slice. |
| `write_targets` | List of repo-relative paths this slice will WRITE (create or edit). Name the files, not directories, wherever the slice is specific enough to know them. MUST be present on every slice; `[]` is legitimate for a slice that authors nothing in the checkout (a verification or promotion slice that only runs suites and ships). Overlap across slices is expected and correct — a file touched by three slices appears in all three. This declares blast radius, NOT scope: it is what `/execute-plan`'s entry gate narrows an in-flight block to, so an omitted path silently stops being gated rather than failing loudly. Under-declaring is the failure mode to guard against; when unsure whether a slice will touch a file, declare it. |
| `aggregation_metadata.assessors_run` | Non-empty list of assessor descriptions. |
| `verification_metadata.double_check_verdict` | Enum: `"PASS"` or `"ESCALATE"`. |
| `confidence` | Enum: `"low"` / `"medium"` / `"high"`. Low if ESCALATE verdict, or assessors disagreed on slice count. |

---

## Execution

Six internal steps. Run in order; no step is skipped unless the assess-first short-circuit fires at Step 2.

**Step 0 — Help**

If invoked with `--help` or no plan is available in session context, output the Parameters table and stop. Do not proceed to Step 1.

**Step 1 — Identify plan and desired outcome**

1. Read the plan from `plan_path_or_content`:
   - If it starts with `/` or `~/`: treat as a file path; use the Read tool to load it.
   - Otherwise: use the value directly as inline plan markdown.
   - If the file does not exist: return the deterministic error envelope and stop.
2. Confirm the desired-outcome string:
   - If `desired_outcome` is provided in the input: use it directly.
   - If absent or ambiguous: ask the user before proceeding. Default: the last stated desired outcome in this session. If still ambiguous, ask.
3. If multiple plans are open in the session, confirm which one to assess.

**Step 2 — Assess whether slicing is needed**

Apply all four slicing rules to the plan as one single slice:

1. **Meaningful piece** — does the plan as one unit represent one cohesive piece of work? Is there a single over-arching "reason this plan exists"?
2. **Single reason to change** — does everything in the plan change for the same underlying reason? Would a future requirement change affect all parts of the plan uniformly, or would different parts respond to different drivers?
3. **Context-window fit** — does the whole plan fit the routine model's working capacity for one focused implementation session? Consider the plan length, the number of files touched, and the depth of logic involved.
4. **Agent justification** — would implementing the whole plan as one slice require the more-capable model? If yes, is there a concrete reason that justifies the cost?

If **all four rules pass** for the plan as a single unit:
- Emit `assessment.slicing_needed = false`
- Write a non-empty `rationale` explaining why
- Set `slices = []`
- Set `aggregation_metadata.assessors_run = ["assess-first-short-circuit"]`
- Set `verification_metadata` with `double_check_verdict = "PASS"`, `claims_checked = 0`, `rounds_run = 0`, `against = ""`
- Set `confidence = "high"` (no assessors disagreed; no post-verification needed for the trivial no-slicing case)
- **Stop. Skip Steps 3–6.**

If **any rule fails**: proceed to Step 3 to gather multi-perspective slice candidates.

**Step 3 — Write artifact and dispatch assessor agents**

1. Write the assessment bundle to:
   ```
   ~/.claude/state/solution_slicer/adhoc/draft_<session_id_first8>_<timestamp>.md
   ```
   The bundle contains: the plan content + the desired-outcome string + the four slicing rules (verbatim) + the Cockburn six-ideas toolbox (verbatim below).

2. Build the structural-claim addendum by scanning the plan for: file paths (e.g. `path/to/file.py`), line number references, function/method names, configuration keys, named constraints. Format as a numbered list (per `/double-check` Step 2c pattern).

3. Dispatch `--routine N` + `--more-capable N` assessor agents in **parallel** (single message, multiple Agent tool calls). Default: 1 routine Sonnet + 1 more-capable Opus.

For each assessor, use:
- `subagent_type: readonly-checker`
- `model: sonnet` (routine) or `opus` (more-capable)
- `description: "Round [R] assessor [i/total] — solution-slicer"`

Assessor prompt template:

```
You are an independent delivery-slicing assessor. You have no access to the conversation that produced this plan.

Your task:
1. Read the plan + desired outcome + rules bundle at: `[artifact_path]`
2. Apply the four slicing rules to identify candidate slice boundaries:
   - Rule 1 (Meaningful piece): each slice must cover one cohesive unit of implementation work.
   - Rule 2 (Single reason to change): things that change for the same reason stay together; things that change for different reasons stay apart.
   - Rule 3 (Context-window fit): each slice must fit the chosen agent's working capacity for one focused session.
   - Rule 4 (Agent justification): default agent is "routine" (Sonnet); "more_capable" (Opus) requires a stated reason per slice.
3. Use the Cockburn Six Ideas to find candidate slice boundaries:
   - Idea 1 (walking_skeleton): find the thin end-to-end path; make that Slice 1.
   - Idea 2 (sentence_extension): extend the walking skeleton one step at a time.
   - Idea 3 (partial_step): implement part of a step (simple data/flows first, complex later).
   - Idea 4 (different_ways): separate alternative paths or variations into distinct slices.
   - Idea 5 (data_rules): slice on data complexity — simple data/rules first, complex later.
   - Idea 6 (knowledge_vs_implementation): spike/learn first, then implement.
4. Produce a candidate slice list. Each slice must have:
   - id, name, description, type ("implementation" or "implementation_verification")
   - cohesion_rationale (why this set of changes belongs together)
   - single_reason_for_change (the cohesion axis)
   - slicing_idea_applied (which of the six ideas grounded this boundary — for implementation slices)
   - agent_choice ("routine" or "more_capable")
   - agent_justification (required and non-empty only when agent_choice is "more_capable")
   - context_fit_assessment (non-empty; qualitative judgment of fit)
   - depends_on (list of slice ids this depends on, or empty list)
5. The LAST slice in your list MUST have type "implementation_verification" — it confirms the whole plan shipped against the desired outcome.
6. Additionally verify these structural claims from the plan:
   [scope_addendum built in Step 3, item 2 — the structural-claim addendum]

Return your candidate slice list as a JSON array (the "slices" field). Also return:
  assessor_id: [your assessor description]
  slicing_needed: true
  slice_count: [N]
  notes: [any observations or caveats]
```

**For more-capable (Opus) assessors only**, append after the JSON instruction:

```
## Scope-Coverage Assessment (advisory — Opus only)

After proposing your candidate slices, assess:
1. Do the candidate slices together cover 100% of the plan scope? Is any planned work absent?
2. Append to your return:
   scope_coverage: COVERS | PARTIAL | GAPS_FOUND
   scope_notes: [1-3 sentences]
```

Track round number R (starts at 1). Assessors are stateless; each round dispatches fresh agents.

**Step 4 — Consolidate**

1. Collect all assessor responses.
2. Merge candidate slice lists using the four rules:
   - Apply cohesion + meaningful-piece to merge over-fine slices (slices that have the same reason-to-change belong together).
   - Apply single-reason-to-change to split incorrectly-merged slices (slices with different reasons-to-change must be separated).
   - Ensure context-window fit for the chosen agent per slice.
   - Enforce agent justification: if a slice has `agent_choice = "more_capable"`, the justification must be non-empty.
3. Note any assessor disagreements in `aggregation_metadata.disagreements`. If assessors disagree on slice count, the consolidator picks the **smallest count that still satisfies cohesion + meaningful-piece** (cohesion takes precedence over granularity).
4. **Closing-slice rule:** check `slices[-1].type`. If the consolidated list does NOT already end with a slice of type `"implementation_verification"`, **append** a closing implementation-verification slice now:
   ```json
   {
     "id": "S-final",
     "name": "Implementation verification",
     "description": "Confirm the whole plan shipped against the desired outcome: [desired_outcome_text]",
     "type": "implementation_verification",
     "cohesion_rationale": "Validation-V closing step (Cockburn) — every multi-slice delivery ends with an aggregate verification V.",
     "single_reason_for_change": "Desired outcome or aggregate scope changes.",
     "slicing_idea_applied": "knowledge_vs_implementation",
     "agent_choice": "routine",
     "agent_justification": "",
     "context_fit_assessment": "Verification work is lightweight relative to implementation slices; fits routine model.",
     "depends_on": ["<all prior slice ids>"]
   }
   ```

**Step 5 — Post-verify via `/double-check`**

Build the `--against` string:
```
<desired_outcome>; slicing rules: (1) each slice is a meaningful piece, (2) single reason to change per slice, (3) each slice fits the chosen agent's working capacity, (4) routine model is the default — more_capable requires a stated justification; (5) the final slice is always type implementation_verification
```

Invoke:
```
/double-check --class check --rounds 2 --against "[against_string above]"
```

- **PASS (0 discrepancies):** Record in `verification_metadata`. Proceed to Step 6.
- **DISCREPANCY (round 1):** `/double-check` re-dispatches diff-only on round 2.
- **ESCALATE (`max_rounds` reached with unresolved discrepancies):** Surface the discrepancies verbatim and present the user a two-option choice (do NOT default-proceed):
  - **(a) One more round** — re-invoke `/double-check` with the same `--against` string; round counter increments past `max_rounds`; fresh isolated checkers.
  - **(b) Proceed as is** — accept the unresolved discrepancies. Set `verification_metadata.double_check_verdict = "ESCALATE"`, record the discrepancy list in `verification_metadata.escalate_disposition`, set `confidence = "low"`, proceed to Step 6.
  - No `override_reason` capture — the user's pick is recorded.

**Step 6 — Present**

Present the final recommendation only after `/double-check` returns PASS. Include:

1. The `assessment` block (`slicing_needed` + `rationale`).
2. The `slices[]` with all required fields, formatted as a readable table or list.
3. The `aggregation_metadata` (assessors run + any disagreements).
4. The `verification_metadata` (verdict + claims checked + rounds + against-string) so the user can audit the chain.
5. The `confidence` and `assumptions[]`.

---

## Invocation Surfaces

The skill is invokable from two surfaces. Both pass the same Input Artifact and consume the same Output Contract. The surface layer is the only changing locus when adding a new caller (Cockburn Evolution Test — single-locus change).

### Surface 1 — Technical-design step (Phase 2 / `/solution-design`)

Called by either the user directly (after `/clarification` produces a locked `# Discovery`) or by the `/solution-design` orchestrator (B3) as part of the design-to-delivery-plan handoff. The desired outcome is drawn from the locked `## Desired Outcome` field in `# Discovery`. The plan content is the agreed solution description from `## Agreed Solution Description`.

### Surface 2 — Plan-mode dedicated session (`/plan` / Slice I)

Slice I (the `/plan` enhancement) embeds a dedicated session that invokes this skill against the draft Coherent Actions table. The desired outcome is drawn from Gate 0b of the active plan mode session. The plan content is the Coherent Actions table + supporting narrative.

---

## Constraints

- **Assessors are `readonly-checker` agents** — dispatched with isolated context, custom system prompt, no shared conversation history with the producer. Producer-never-verifies is preserved by the topology. (`factcheck-convergence.md` §2 — isolation property preserved.)
- **Main-session-only** — this skill cannot be invoked from inside a subagent (matches `/double-check` constraint at `~/.claude/skills/double-check/SKILL.md:5`).
- **Artifact = file only** — write the plan + outcome + rules bundle to `~/.claude/state/solution_slicer/adhoc/draft_<session_id_first8>_<timestamp>.md` using the Write tool before dispatching assessors. Never pass plan content inline in the agent prompt.
- **Post-verification required** — user sees the recommendation only after `/double-check` returns PASS. An ESCALATE result surfaces unresolved discrepancies to the user; the user decides whether to revise the plan before re-invoking.
- **Trust Hierarchy exception** — convergence orchestration lives in skill text, not Python code, because the engine cannot call the Agent tool. (Same documented exception as `/double-check`; see `Thoughts/double-check-skill-subprocess_THOUGHT.md`.)
- **Scope-coverage rule** — 100% of plan scope must appear in the consolidated slices. The consolidator must reject any merge operation that drops work.

---

## Optionality Boundary

This skill does NOT decide whether the slicing assessment should be run at all. That decision lives in the caller (the `/solution-design` orchestrator or the Slice I plan-mode session).

When this skill is invoked, it **always assesses** (subject to the deterministic input-validation errors in the Input Artifact section). Only the *result* of the assessment may be "no slicing needed." The boundary keeps this skill a clean adapter.

---

## Edge Cases

| # | Condition | Handling |
|---|-----------|----------|
| E1 | Empty plan or missing `plan_path_or_content` | Return deterministic error envelope; no assessors dispatched. |
| E2 | Missing desired outcome | Ask the user for the desired outcome before proceeding. Do not default to a guess. |
| E3 | Plan file path given but file does not exist | Return deterministic error envelope with `error: "plan file not found: <path>"`. |
| E4 | Plan exceeds even more-capable agent's working capacity | `slicing_needed = true`; partition the plan further; note capacity issue in `context_fit_assessment` for each slice; record assumption that the most-capable available model is the ceiling. |
| E5 | All assessors disagree on slice count | Consolidator picks the smallest count that satisfies cohesion + meaningful-piece (cohesion takes precedence over granularity). Record disagreements in `aggregation_metadata.disagreements`. |
| E6 | `/double-check` ESCALATE after one revision | Return ESCALATE to user with unresolved discrepancies verbatim. Do not loop a second revision. User must revise the plan or the desired outcome before re-invoking. |
| E7 | Invalid parameters: `--routine 0 --more-capable 0` | Error: "minimum 1 assessor required (`--routine + --more-capable ≥ 1`)." |
| E8 | `--rounds 0` | Error: "minimum 1 round required." |
| E9 | Invoked inside a subagent | Reject with: "solution-slicer is main-session-only — cannot be invoked from inside a subagent." |
| E10 | Re-invocation after a prior run in the same session | Fresh assessment; no shared state with the prior run. Each invocation is independent. |
| E11 | Plan explicitly contains "do not slice" instruction | Run the assessment anyway. If the assessors agree with the plan's instruction (no violations found), emit `slicing_needed = false` with a note that the plan's instruction was consistent with the four rules. If assessors recommend slicing despite the instruction, surface the tension to the user — present the candidate slices AND note the plan's instruction — and defer to the user to decide. |
| E12 | Closing implementation-verification slice already present at the end of the input plan | Consolidator detects `slices[-1].type == "implementation_verification"` after merging and does NOT append a second one. `eval.py` sees one closing slice and passes. |
| E13 | Single-assessor mode (`--routine 1 --more-capable 0` or `--routine 0 --more-capable 1`) | Valid; `aggregation_metadata.assessors_run` lists one assessor; no disagreements possible. |

---

## Source

- `~/.claude/skills/double-check/SKILL.md` — primary pattern (multi-agent dispatch + aggregator + configurable counts + post-verification + artifact-file pattern + structural-claim addendum).
- `~/.claude/rules/code_first_architecture.md:81-102` — AI Adapter / judgment-port pattern.
- `~/.claude/rules/code_first_architecture.md:112` — Producer-never-verifies invariant.
- `~/.claude/rules/factcheck-convergence.md` §1 — minimum 1 checker total (≥1 assessor).
- `~/.claude/rules/factcheck-convergence.md` §2 — independence / isolated context.
- `<KL>/Development/Sources/Books/slice-the-problem-grow-the-solution-cockburn-v0.9b/slice-the-problem-grow-the-solution-cockburn-v0.9b.md:493-502` — Six Ideas for Slicing a Use Case (Ch 8).
- `slice-the-problem-grow-the-solution-cockburn-v0.9b.md:696-704` — Four Context-Dependent Factors for Slice Size (Ch 12).
- `slice-the-problem-grow-the-solution-cockburn-v0.9b.md:125-129` — Validation-V: closing implementation-verification slice grounding.
- `<KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md:73` — Responsibility Alignment Test (basis for "single reason to change" rule).
- `simplifying-software-design-cockburn.md:69-80` — Six Design Tests.
- `Thoughts/workflow-phases-redesign_H_v2_B2_PLAN.md` — locked plan this skill implements.

---

## Non-goals

- This skill does **not** write `_thought` files. Stateless.
- It does **not** decide whether Phase 2 (`/solution-design`) should run at all. That decision lives in the calling orchestrator.
- It does **not** select among multiple Agreed Solution Descriptions. Selection is the orchestrator's responsibility.
- It does **not** produce more than one consolidated recommendation per invocation.
- It does **not** bypass the post-verification step (Step 5). The user always sees only PASS results.
- It does **not** omit the closing implementation-verification slice when slicing is recommended (the consolidator always appends it at Step 4 if missing).
- It does **not** reduce plan scope. Coverage rule: 100% of planned work appears in the recommended slices.

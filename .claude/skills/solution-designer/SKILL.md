---
name: solution-designer
version: "0.1"
description: AI adapter — designs ONE candidate Agreed Solution Description (architecture + UX folded) from a clarified `# Discovery` section. Called N-parallel by `/solution-design` orchestrator; also invokable by `/plan` Slice I fallback or standalone CLI. Stateless — never writes `_thought`.
allowed-tools: Read
---

# /solution-designer — Solution Design Adapter

Design **one** candidate Agreed Solution Description (architecture + UX, folded) from a clarified `# Discovery` section. The orchestrator (B3) calls N parallel instances of this skill, ranks the candidates with an independent Opus selector, and the user picks one. This skill produces a single candidate — it does not select, compare, or persist.

This skill is an **AI adapter — "Judgment only, behind a port"** (per `~/.claude/rules/code_first_architecture.md` §AI Adapter, lines 81–102). It receives a structured input, returns a structured output, and has no state of its own.

Design self-checks use the Cockburn Six Design Tests (`<KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md` §0.3, lines 69–80). The Abstraction Test and Responsibility Alignment Test are hard-gate booleans in the output schema; the other four are advisory notes (`simplifying-software-design-cockburn.md:79` — "experienced designers invent decently robust designs by paying close attention to the first two tests").

---

## When to Use

Activate when:

- Called by `/solution-design` (Phase 2) orchestrator (B3) as one of N parallel candidate producers — the primary invocation surface.
- Called by `/plan` (Phase 3, Slice I) as the inline fallback when Phase 2 was skipped — same I/O contract, different caller.
- Called directly via the standalone CLI surface `/solution-designer --discovery <path-or-section>` for one-off iteration on a fixture or a single Discovery.

In all three surfaces the skill consumes the same JSON-shaped input and emits the same JSON-shaped output. The skill does not decide whether Phase 2 should run at all (that decision lives in B3) — when invoked, it always executes.

The skill is pure judgment. No tools are required besides Read (for the Cockburn citation source and the optional Discovery file).

---

## Invariants

The skill MUST obey the following invariants. They are non-negotiable and any caller that needs different behavior must wrap this skill, not modify it.

1. **Stateless.** The skill MUST NOT write `_thought` files. It MUST NOT mutate phase state. It MUST NOT call `pre_plan_gates.py` write-side commands (`phase-start`, `phase-stop`, `register-session-todo`, etc.). The orchestrator (B3) owns all persistence. *(C5 of S-H-v2-A; `~/.claude/rules/code_first_architecture.md:81–102` AI-adapter pattern.)*

2. **Producer-never-verifies (external).** This skill does not fact-check its own output. The Cockburn-gate self-check inside Step 4 of Execution is a *self-report*, not a verification — the booleans are emitted in the output, and the orchestrator's independent Opus selector + the user's pick are the actual quality gate. *(`~/.claude/rules/code_first_architecture.md:112` "Producer never verifies its own output.")*

3. **UX + architecture folded for v1.** A single invocation produces one candidate that covers both architecture and UX decisions. Do not split into separate UX-only or architecture-only runs. *(Cockburn `simplifying-software-design-cockburn.md:206` — "start fat, split only as needed.")*

4. **Discovery is the only grounding surface.** Every decision in the candidate MUST cite which locked `# Discovery` field excerpt grounded it. The skill MUST NOT invent decisions that have no anchor in Discovery; it MUST NOT consult the codebase, prior plans, or external sources beyond what is passed in as input.

5. **One candidate per invocation.** Do not emit multiple alternatives in a single run. The orchestrator achieves N alternatives by running N parallel invocations (each with different `model` / `design_context_override`).

---

## Input Contract

### Schema

```json
{
  "guiding_policy": "<markdown body of the locked `## Guiding Policy` field from `# Discovery`>",
  "desired_outcome": "<markdown body of the locked `## Desired Outcome` field from `# Discovery`>",
  "desired_solution": "<markdown body of the locked `## Desired Solution` field from `# Discovery`>",
  "metrics": "<markdown body of the locked `## Metrics` field, including the OMTM line>",
  "discovery_src_hash": "<12-hex SHA-256 prefix of the four locked field bodies — the `_discovery_locked_fields_hash` from pre_plan_gates.py>",
  "design_context_override": "<optional free-text string injected by the orchestrator to vary candidates (e.g. 'explore a synchronous design', 'optimize for fewest moving parts')>",
  "model": "sonnet" | "opus"
}
```

### Field rules

| Field | Required | Source / Semantics |
|-------|----------|--------------------|
| `guiding_policy` | yes | Locked `## Guiding Policy` body verbatim from `# Discovery`. The list of locked fields is `DISCOVERY_LOCKED_FIELDS` in `${KIT_HOOKS_DIR}/pre_plan_gates.py:153-158`. |
| `desired_outcome` | yes | Locked `## Desired Outcome` body verbatim. |
| `desired_solution` | yes | Locked `## Desired Solution` body verbatim. |
| `metrics` | yes | Locked `## Metrics` body verbatim. The OMTM sub-field inside `## Metrics` is what `pre_plan_gates.py` actually locks (see the inline comment at `pre_plan_gates.py:157`). |
| `discovery_src_hash` | yes | The 12-hex SHA-256 prefix of the concatenated bodies of the four locked fields, computed by `_discovery_locked_fields_hash` in `pre_plan_gates.py:178-189`. Used by the caller / orchestrator to detect stale invocations; this skill echoes it back unchanged in the output as provenance. |
| `design_context_override` | no | Free-text string the orchestrator passes to vary one candidate against another. The skill incorporates it as additional steering but it does NOT override locked Discovery — locked fields always take precedence. |
| `model` | no (default `"sonnet"`) | Enum `"sonnet"` \| `"opus"`. Selects which Claude model the skill runs under for this one candidate. The orchestrator's per-run model-mix decision (e.g. `--number-of-sonnet-agents N --number-of-opus-agents N`) passes through to per-candidate model selection. Default is `sonnet`. |

### Deterministic errors

If any required locked field is missing, empty, or whitespace-only, the skill MUST return an error result without producing a candidate. Errors are deterministic — the skill does not try to fill in the missing field.

```json
{
  "error": "<one of: guiding_policy required, desired_outcome required, desired_solution required, metrics required, discovery_src_hash required>",
  "agreed_solution_description": null,
  "architecture_decisions": [],
  "ux_decisions": [],
  "cockburn_gate_results": {},
  "confidence": null,
  "assumptions": [],
  "discovery_anchors": [],
  "model_used": "<echo of input.model or default>"
}
```

If `metrics` is present but contains no OMTM line, return `error: "metrics field has no OMTM line — run /clarification step 8 first"`. The OMTM line is the locked sub-field per `pre_plan_gates.py:157`.

If a locked field is present but malformed in a way the skill can detect (e.g. it is empty markdown, or a placeholder like `TODO`), return `error: "<field> is empty or placeholder; re-run /clarification --from <thought-path>"`.

---

## Output Contract

### Schema

```json
{
  "agreed_solution_description": "<markdown body, plain business words first, technical detail in a follow-up paragraph>",
  "architecture_decisions": [
    {
      "area": "<short noun phrase, e.g. 'persistence', 'concurrency model', 'message format'>",
      "choice": "<the chosen approach, in plain business words first>",
      "rationale": "<one to three sentences: why this choice fits the Discovery>",
      "discovery_anchor": "<verbatim excerpt from one of the 4 locked Discovery fields that grounds this choice>"
    }
  ],
  "ux_decisions": [
    {
      "area": "<short noun phrase, e.g. 'entry surface', 'feedback timing', 'failure-recovery affordance'>",
      "choice": "<the chosen UX approach>",
      "rationale": "<why this choice fits the Discovery>",
      "discovery_anchor": "<verbatim excerpt from a locked Discovery field>"
    }
  ],
  "cockburn_gate_results": {
    "abstraction_test": {
      "passed": true,
      "reasoning": "<one to three sentences explaining the test against this candidate>"
    },
    "responsibility_alignment_test": {
      "passed": true,
      "reasoning": "<one to three sentences>"
    },
    "evolution_test": {
      "notes": "<advisory: what changes are likely, and how many components each would touch>"
    },
    "communications_patterns_test": {
      "notes": "<advisory: do communication paths form cycles or back-edges>"
    },
    "data_connectedness_test": {
      "notes": "<advisory: does each component have the data it needs from input>"
    },
    "data_variations_test": {
      "notes": "<advisory: handling of edge data shapes, first-run, empty input>"
    }
  },
  "confidence": "low" | "medium" | "high",
  "assumptions": [
    "<one assumption made about the Discovery or its context, in plain words>"
  ],
  "discovery_anchors": [
    {
      "field": "guiding_policy" | "desired_outcome" | "desired_solution" | "metrics",
      "excerpt": "<verbatim excerpt that was used as grounding somewhere in the candidate>"
    }
  ],
  "model_used": "sonnet" | "opus"
}
```

### Field rules

- **`agreed_solution_description`**: Markdown body. Plain business words first; technical detail follows in a separate paragraph (`~/.claude/rules/plan-gates.md` Gate 0a OQ19). A reader without codebase context must understand the operational change the candidate proposes from the first paragraph alone.
- **`architecture_decisions` / `ux_decisions`**: Each is a list. Each item has all four sub-fields. If the candidate has no UX-relevant choices (e.g. a backend-only slice), emit `"ux_decisions": []` — do NOT fabricate decisions. Same rule applies to `architecture_decisions` for a UX-only candidate.
- **`cockburn_gate_results.abstraction_test.passed`** and **`cockburn_gate_results.responsibility_alignment_test.passed`** are hard-gate booleans. A candidate with either set to `false` is emitted anyway (the orchestrator's selector may rank it low or surface it as a degraded candidate; an all-FAIL emission is informative because it suggests the Discovery itself is malformed).
- **`confidence`**: `"low"` \| `"medium"` \| `"high"`. Low if either hard gate failed, or if more than 2 assumptions were required, or if more than half of decisions lack a verbatim Discovery anchor.
- **`assumptions`**: Each entry names one judgment call the skill made that was not directly grounded in Discovery. Empty list is acceptable. Do not hide assumptions.
- **`discovery_anchors`**: Aggregate list of the verbatim excerpts the skill leaned on. Each `excerpt` MUST be a verbatim substring of the corresponding input field — no paraphrase, no synthesis.
- **`model_used`**: Echoes back `input.model` (or the default `"sonnet"`). Provenance for the orchestrator's selector when ranking candidates that came from different models.

All required fields must be non-empty on a successful return. On error, the skill returns the error envelope from the Input Contract section.

---

## Execution

Five internal steps. The skill runs them in order; no step is skipped.

### Step 1 — Load and validate

Read the input fields. If any required field is missing, empty, or whitespace-only, return the deterministic error envelope (see Input Contract → Deterministic errors). If `metrics` lacks an OMTM line, return the corresponding error. If a field is a placeholder (`TODO`, empty body), return the corresponding error. The skill MUST NOT attempt to design over a missing or malformed anchor.

If a `design_context_override` is present, retain it as steering. Locked Discovery fields always take precedence over the override — the override may *narrow* the design space but it MUST NOT contradict a locked field.

### Step 2 — Restate the locked fields as anchors

Read the four locked-field bodies verbatim. Keep them in working memory as the only grounding surface for the rest of the run. This is the equivalent of the Cagan-style "quote relevant parts before answering" pattern for long-context tasks — the skill anchors itself before drafting.

### Step 3 — Draft the candidate

Produce a single candidate that covers both architecture and UX (folded per Cockburn "start fat"). For every decision in the candidate:

1. Name the area (short noun phrase).
2. State the choice in plain business words first.
3. Write the rationale in one to three sentences.
4. Pick a verbatim excerpt from one of the four locked Discovery fields that grounds this choice. If no verbatim excerpt fits, the decision is ungrounded — either revise the choice so it traces to Discovery, or record it as an explicit assumption (in `assumptions[]`) instead of as a grounded decision.

Draft `agreed_solution_description` in plain business words first: a reader without codebase context must grasp the operational change from the first paragraph alone (`~/.claude/rules/plan-gates.md` Gate 0a OQ19). Technical detail (file paths, function names, schema details) belongs in a follow-up paragraph or in the per-decision rationale fields, not in the opening paragraph.

If the input represents a backend-only slice, emit `ux_decisions: []`. If it represents a UX-only slice, emit `architecture_decisions: []`. Do NOT fabricate decisions to fill the empty list.

### Step 4 — Self-apply the Cockburn Six Design Tests

Apply each test against the drafted candidate. The first two are hard gates (booleans + reasoning); the other four are advisory notes.

| Test | Apply by asking |
|------|------------------|
| **Abstraction Test** (Cockburn `:72`) | Do the names of the components / areas in the candidate convey what each does? Are they recognizable to experts in the domain? |
| **Responsibility Alignment Test** (Cockburn `:73`) | For each named component / area, do the name, the responsibility, and the data + functions it owns align? |
| **Evolution Test** (Cockburn `:74`) | If a likely future change happens (orchestrator changes, Discovery format changes, UX is split off later), how many components in this candidate have to change? Single-locus is good. |
| **Communications Patterns Test** (Cockburn `:75`) | Do the components talk to each other in cycles or back-edges? One-way flow with a leaf node is good. |
| **Data Connectedness Test** (Cockburn `:76`) | Does each component have all the data it needs from its inputs, or does it have to reach out to fetch state? |
| **Data Variations Test** (Cockburn `:77`) | How does the candidate handle edge data shapes — empty inputs, first run, malformed but valid-looking data? |

**Refine-once-on-FAIL rule.** If either hard gate (`abstraction_test` or `responsibility_alignment_test`) fails, attempt **one** focused refinement of the candidate that targets the failing gate (e.g., rename a misleading component, or split a multi-responsibility node into two). Re-apply the failing test. Then emit with the resulting results regardless of pass/fail. Do not loop. (Editorial — chosen to prevent in-skill iteration loops while still giving the skill a chance to fix obvious self-detected misalignment.)

### Step 5 — Emit structured output

Assemble the output JSON per the Output Contract schema. Populate `discovery_anchors[]` by aggregating the verbatim excerpts used across `architecture_decisions[].discovery_anchor` and `ux_decisions[].discovery_anchor`. Set `confidence`:

- `low` — either hard gate failed (even after the one refinement), OR `assumptions` has more than 2 entries, OR more than half the decisions lack a verbatim `discovery_anchor`.
- `medium` — both hard gates passed AND assumptions ≤ 2 AND at least half the decisions have a verbatim anchor.
- `high` — both hard gates passed AND assumptions ≤ 1 AND every decision has a verbatim anchor.

Echo `model_used` from `input.model` (or the default). Return.

---

## Eval Harness

This skill is the first in the platform to ship a Python eval companion (`eval.py`). The harness is **hybrid by design**:

| Layer | Mechanism | Lives in |
|-------|-----------|----------|
| Deterministic checks | Python script — schema validation, anchor-coverage ratio, SHA-256 change-impact diff | `~/.claude/skills/solution-designer/eval.py` |
| Judgment-quality eval | `/double-check` invocations against fixtures | Caller's responsibility (not bundled into `eval.py`) |

The separation reflects Cockburn responsibility alignment — schema validation and judgment evaluation are distinct responsibilities and live in distinct components. It also preserves producer-never-verifies: AI-based eval inside the skill would be self-verification; AI-based eval lives in a separate context via `/double-check`.

### Fixtures

`~/.claude/skills/solution-designer/fixtures/` holds 3–5 canonical input fixtures drawn from real shipped slices (or hypothetical-but-realistic UX-heavy inputs). Each fixture is a JSON file conforming to the Input Contract schema.

Starter fixtures (shipped with v0.1):

| File | Source slice / shape |
|------|----------------------|
| `sample-slice-A-walking-skeleton.json` | Backend-heavy phase-state authority; UX surface is the CLI. |
| `sample-slice-C-todo-mgmt.json` | Code-enforcement slice; UX surface is the `todo.py` CLI + TODO.md format. |
| `sample-ux-heavy-hypothetical.json` | Hypothetical UX-heavy slice (e.g. a wizard with explicit user choices). Used to exercise the `ux_decisions[]` path. |

### Running the deterministic eval

```bash
# Validate one fixture + a captured skill output against the schema, and diff against a baseline:
python3 ~/.claude/skills/solution-designer/eval.py \
  --fixture ~/.claude/skills/solution-designer/fixtures/sample-slice-A-walking-skeleton.json \
  --skill-output /tmp/run-2026-05-22.json \
  --baseline /tmp/run-2026-05-21.json
```

The script emits a JSON report to stdout with:

1. `schema_valid: true | false` per fixture (deterministic).
2. `anchor_coverage_ratio`: `(decisions with non-empty discovery_anchor) / (total decisions)` — a regex-based metric over the captured output.
3. `change_impact`: `{sha256: "<hex>", diff: "<unified-diff body>" | null}`. On first run with no baseline, emits `baseline_absent: true` and no diff.

Re-running with an unchanged fixture and an unchanged prompt body should yield identical SHA-256 and an empty diff. Non-zero diff on an unchanged run is itself a regression signal (the skill is non-deterministic).

### Running judgment-quality eval

Judgment-quality evaluation runs through `/double-check`, separately from `eval.py`. Suggested form:

```
/double-check --class gate --rounds 1 \
  --against "<solution-designer output for fixture X>; discovery-grounding + Cockburn-gate validity per ~/.claude/skills/solution-designer/SKILL.md §Execution Step 4"
```

The checker receives the fixture + the skill output as the claim package and verifies discovery-grounding (every decision cites a verbatim anchor) and Cockburn-gate validity (the hard-gate booleans match the candidate's actual abstraction / responsibility shape).

---

## Invocation Surfaces

The skill is invokable from three surfaces. All three pass the same Input Contract JSON and consume the same Output Contract JSON. The surface layer is the only changing locus when adding a new caller (Cockburn Evolution Test — single-locus change).

### Surface 1 — `/solution-design` orchestrator (B3, primary)

The Phase 2 orchestrator (B3) invokes this skill via the Agent tool, N times in parallel, with different `model` and/or `design_context_override` per invocation. The locked Discovery is inlined into the prompt by the orchestrator (B3 is the only writer; B1 stays stateless). The choice of `subagent_type` (e.g. `general-purpose` vs. a dedicated type) is decided at B3 Planning — B1 does not depend on it.

### Surface 2 — `/plan` Slice I fallback

When the user runs `/plan` directly and Phase 2 was skipped, Slice I (the `/plan` enhancement) invokes this skill inline as a fallback to fill the Coherent Actions input. Slice I (or Slice E) tracks time + tokens to `Stats.md` — that instrumentation belongs to Slice I / E, NOT to this skill.

### Surface 3 — Standalone CLI

```
/solution-designer --discovery <path-to-_thought-or-section> [--context <text>] [--model sonnet|opus]
```

For one-off iteration outside the orchestrator — e.g., debugging a fixture, exercising the Discovery → candidate pipeline against a hand-written Discovery, or regression-testing prompt changes. Stateless; the standalone surface runs the skill and prints the JSON output to stdout.

---

## Optionality Boundary

This skill does NOT decide whether Phase 2 (`/solution-design`) should be invoked at all. That decision — the "optionality trigger" question — lives in `/solution-design` (B3 orchestrator).

When this skill is called, it always executes (subject to the deterministic input-validation errors in the Input Contract). The boundary keeps this skill a clean adapter: it produces a candidate when asked. The orchestrator owns the upstream "should we even run Phase 2" question.

---

## Edge Cases

| # | Condition | Handling |
|---|-----------|----------|
| E1 | `# Discovery` section absent in the source `_thought` | Caller's responsibility to detect upstream. If the skill is called with empty locked-field inputs, return the deterministic error envelope per Input Contract. |
| E2 | `design_context_override` empty or missing | Run without it (optional field). |
| E3 | All 6 Cockburn tests fail | Emit anyway with `cockburn_gate_results.*.passed=false` for the two hard gates + `confidence: low`. Do NOT refuse to emit — an all-FAIL candidate is informative (the orchestrator's selector may treat it as a signal that the Discovery itself is malformed). |
| E4 | Standalone CLI invocation without orchestrator | Skill runs (stateless); `eval.py` is the verification path for one-off runs. |
| E5 | `/plan` (Slice I) fallback path | Same skill, same I/O contract; orchestrator records time + tokens to `Stats.md`. The time/token instrumentation belongs to Slice I or Slice E, not to this skill. |
| E6 | UX-only or arch-only candidate (one is N/A) | Emit empty array for the N/A type. Do NOT fabricate decisions. Hard rule. |
| E7 | Re-run with identical fixture and unchanged prompt | `change-impact` diff in `eval.py` should yield zero diff and identical SHA-256. Non-zero diff = the skill is non-deterministic and that itself is a regression signal — caller's call. |
| E8 | First-run baseline (no prior output for `eval.py` diff) | `eval.py` reports `baseline_absent: true`, no diff metric (degraded mode, not failure). |
| E9 | Locked Discovery fields present but malformed (e.g. `## Metrics` body has no OMTM line) | Reject at Step 1 with a specific pointer to the malformed field. Do not silently attempt a design over a missing-anchor field. |

---

## Source

- `~/.claude/rules/code_first_architecture.md` lines 81–102 — AI Adapter / judgment-port pattern.
- `~/.claude/rules/code_first_architecture.md:112` — Producer-never-verifies invariant.
- `${KIT_HOOKS_DIR}/pre_plan_gates.py:153-158` — `DISCOVERY_LOCKED_FIELDS` (the four locked field names).
- `${KIT_HOOKS_DIR}/pre_plan_gates.py:178-189` — `_discovery_locked_fields_hash` (12-hex SHA-256 prefix of the four locked field bodies).
- `<KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md` lines 69–80 — Six Design Tests.
- `<KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md:79` — "first two tests" emphasis (Abstraction + Responsibility Alignment).
- `<KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md:206` — "start fat, split only as needed" (UX + architecture folded for v1).
- `~/.claude/rules/plan-gates.md` Gate 0a OQ19 — Plain business words first.
- `Thoughts/workflow-phases-redesign_THOUGHT.md` S-H-v2-A entry — Slice H v2 architecture (3-skill split, C1–C5).
- `Thoughts/workflow-phases-redesign_H_v2_B1_PLAN.md` — Locked plan this skill implements.

---

## Non-goals

- This skill does **not** select among candidates. The orchestrator's independent Opus selector + the user gate own selection.
- It does **not** decide whether Phase 2 runs at all (Optionality Boundary, above).
- It does **not** write or mutate any file. Stateless adapter — only the orchestrator writes.
- It does **not** invoke `/double-check` on its own output. Producer-never-verifies is preserved by the orchestrator's N-parallel + selector + user gate pattern, not by self-fact-check inside the skill.
- It does **not** produce multiple alternatives in a single run. N alternatives come from N parallel invocations.
- It does **not** consult the codebase, prior plans, or external sources. Discovery is the only grounding surface.

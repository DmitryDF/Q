---
name: outcome-framing
version: "1.0"
description: Cagan-grounded outcome framing — turn an own-words solution + diagnosis into a Cagan-framed Outcome statement (output → outcome shift). Called from /clarification step 7 and any other workflow that needs to draft a desired-outcome statement from a problem + a proposed solution.
---

# /outcome-framing

Turn a {diagnosis, own_words_solution} pair into a Cagan-framed **Outcome** — a statement of the operational change the user experiences, with no implementation mechanism in the wording.

This skill is an **AI adapter — "Judgment only, behind a port"** (per `~/.claude/rules/code_first_architecture.md` §AI Adapter). It receives structured input, returns structured output, and has no state of its own.

---

## Trigger

Activate when:

- Called by `/clarification` step 7 (the documented calling site)
- User says `/outcome-framing` or "frame the outcome" with a diagnosis + solution context available
- Any workflow needs to convert an output-shaped statement ("we will build X") into an outcome-shaped one ("user experiences Y")

Main-session use is the default. The skill is pure judgment — no tools required besides Read for the citation source.

---

## Contract

### Input

```json
{
  "diagnosis": "<one-paragraph statement of the critical challenge — Rumelt-kernel style>",
  "own_words_solution": "<the user's plain-language description of what they want to do>"
}
```

Both fields are required strings. If either is missing or empty, return an error result (see Edge Cases below).

### Output

```json
{
  "outcome": "<the Cagan-framed outcome statement>",
  "rationale": "<one-sentence explanation of why this framing fits the diagnosis and avoids implementation mechanism>",
  "source_citation": "<KL>/ProductManagement/Sources/Books/inspired-cagan/inspired-cagan.md:113 (Cagan, Inspired ch.7 — outcome vs. output)"
}
```

All three fields must be non-empty on a successful return.

---

## Execution

### Step 1 — Load the Cagan anchor

Read `<KL>/ProductManagement/Sources/Books/inspired-cagan/inspired-cagan.md` lines 110-115 (or open the file and locate the `## Three Overarching Principles of Strong Product Teams` section, [STEP] 3). The anchor quote is:

> "it's all about solving problems, not implementing features. Conventional product roadmaps are all about output. Strong teams know it's not only about implementing a solution. They must ensure that solution solves the underlying problem. It's about business results." — Cagan, *Inspired* ch.7

The skill's job is to apply this distinction to the input pair.

### Step 2 — Apply the rubric

A Cagan-framed Outcome must:

1. **Describe the operational workflow after implementation** — what the user (or downstream actor) experiences in practice.
2. **Be observable from the workflow, not the code** — "Can I tell this is working by watching someone use it?"
3. **Trace to the diagnosis** — the outcome resolves (or measurably reduces) the critical challenge stated in the diagnosis.
4. **Name no implementation mechanism** — no models, algorithms, data structures, file paths, function names, thresholds, iteration counts, or schema details in the outcome wording.

Anti-pattern table (illustrating Gate 0b's principle from `~/.claude/rules/plan-gates.md`; first row mirrors Gate 0b's example, rows 2–3 are Slice-D-idiomatic extensions of the same principle):

| Output-shaped (reject) | Outcome-shaped (accept) |
|------------------------|-------------------------|
| "3 independent Sonnet checkers verify claims, minimum 2 rounds, hard stop at iteration 4" | "Research claims are independently verified to convergence before delivery; user sees convergence status and unresolved caveats" |
| "Add DISCOVERY_LOCKED_FIELDS to pre_plan_gates.py" | "Every clarified topic enters the next phase with 4 locked commitments in # Discovery" |
| "Run /double-check at step 7 with three Sonnet checkers and an Opus advisory" | "Before locking a Snapshot, the user sees an independent fact-check report and either passes or overrides with a reason" |

### Step 3 — Draft, then anti-pattern check

1. Draft a candidate Outcome from the input pair.
2. Scan the candidate for any of: model names (Sonnet, Opus, GPT, etc.), algorithm names, data-structure names (list, dict, schema, table, frontmatter, JSON, YAML), file/function/flag names, numeric thresholds, iteration counts, or shell/CLI invocations.
3. If any are present: **push back**. Do not return the candidate. Either:
   - Re-draft it without the mechanism, OR
   - If the mechanism is load-bearing for the user's intent (and the user has not yet separated it out), return an error result asking the user to restate the *what changes* without the *how it changes*.
4. If the candidate passes the scan, write a one-sentence rationale explaining how the framing addresses the diagnosis without leaking mechanism.

### Step 4 — Return

Return the JSON object specified in the Output contract. All three fields non-empty.

---

## Edge Cases

| # | Condition | Handling |
|---|-----------|----------|
| E1 | `diagnosis` missing or empty | Return `{"error": "diagnosis required", "outcome": null, ...}`. Do not invent one. |
| E2 | `own_words_solution` missing or empty | Return `{"error": "own_words_solution required", ...}`. Do not invent one. |
| E3 | Solution is already outcome-shaped (no mechanism present) | Refine wording for clarity/observability; return as-is if no improvement available. Rationale notes: "input was already outcome-shaped — minimal reframe." |
| E4 | Solution is entirely output/mechanism with no observable user-side change implied | Return `{"error": "no observable user-side change inferable from solution; user must state what changes in the workflow", ...}`. |
| E5 | Mechanism load-bearing in the user's framing | Return an outcome that strips the mechanism + a rationale flagging the stripped mechanism. Caller (e.g. `/clarification` step 7) decides whether to re-prompt the user. |
| E6 | Source file unreachable | Return the outcome + rationale, but flag `source_citation` as `"<KL>/ProductManagement/Sources/Books/inspired-cagan/inspired-cagan.md:113 (UNVERIFIED — file unreachable at runtime)"`. Do not block. |

---

## Source

- `<KL>/ProductManagement/Sources/Books/inspired-cagan/inspired-cagan.md:113` — Cagan, *Inspired* ch.7, "Solve problems, not implement features"
- `~/.claude/rules/plan-gates.md` — Gate 0b (Desired Outcome) — rubric and anti-pattern table
- `~/.claude/rules/code_first_architecture.md` — AI adapter / judgment port pattern

---

## Sample invocation

**Input:**

```json
{
  "diagnosis": "Plans drift into implementation framing because the outcome is written as 'build X', not 'user experiences Y'. Reviewers can't tell from the outcome whether the change has shipped successfully.",
  "own_words_solution": "Add a step in /clarification that runs a skill to reword the outcome so it doesn't name implementation details."
}
```

**Output:**

```json
{
  "outcome": "Every clarified topic enters planning with an outcome statement whose success can be judged by watching the operational workflow, not by reading the code.",
  "rationale": "Frames the change in terms of what reviewers observe (workflow-level success criterion), trace-able to the diagnosis (drift caused by output-shaped outcomes), with no naming of skills, steps, or mechanisms.",
  "source_citation": "<KL>/ProductManagement/Sources/Books/inspired-cagan/inspired-cagan.md:113 (Cagan, Inspired ch.7 — outcome vs. output)"
}
```

---

## Non-goals

- This skill does **not** decompose the outcome into testable claims (that is Gate 0b2 in plan mode, owned by `/clarification`/`/plan` skills).
- It does **not** validate the outcome against metrics (that is `/lean-analytics-metrics`, A6).
- It does **not** fact-check itself. The calling skill (`/clarification` step 7) wraps the broader coherency chain in `/double-check` independently.
- It does **not** edit any files. It is a pure function: input pair → output struct.

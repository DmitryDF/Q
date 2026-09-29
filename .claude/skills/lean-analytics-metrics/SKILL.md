---
name: lean-analytics-metrics
version: "1.0"
description: Validate candidate OMTM + secondary metrics against Lean Analytics' 4 good-metric properties + actionability. Called from /clarification step 8 and any other workflow that needs to gate metric choice before committing.
---

# /lean-analytics-metrics

Validate a `{omtm, secondary, context}` triple against the five-property rubric from *Lean Analytics* (Croll & Yoskovitz, ch.2): **comparative, understandable, ratio or rate, changes the way you behave**, plus the actionability check. Return per-property booleans + an `AND`-of-five verdict + rationale + source citation.

This skill is an **AI adapter** in the sense of `~/.claude/rules/code_first_architecture.md` § "AI Adapter — Judgment only, behind a port". It receives a structured input, returns a structured output, and has no state of its own. Verification of the assessment is delegated to `/double-check` (producer-never-verifies, per `~/.claude/rules/factcheck-convergence.md` §6).

---

## Trigger

Activate when:

- Called by `/clarification` step 8 (the documented calling site)
- User says `/lean-analytics-metrics` or "validate these metrics" with a candidate OMTM + secondary metric available
- Any workflow needs to gate-check a metric choice against Lean Analytics' rubric before committing it

Main-session use is the default. The skill is judgment + a single `/double-check` dispatch — no other tools required besides Read for the citation source.

---

## Contract

### Input

```json
{
  "omtm": "<the One Metric That Matters candidate, in plain words>",
  "secondary": "<the secondary metric paired with the OMTM>",
  "context": "<one-paragraph statement of what the user wants to improve and the operational setting>"
}
```

All three fields are required strings. If any is missing or empty, return an error result (see Edge Cases below).

### Output

```json
{
  "verdict": "PASS" | "FAIL",
  "per_property": {
    "comparative": true,
    "understandable": true,
    "ratio_or_rate": true,
    "changes_behavior": true,
    "actionable": true
  },
  "rationale": "<one-paragraph explanation, per metric, of how each property was judged>",
  "source_citation": "<KL>/ProductManagement/Sources/Books/lean-analytics/lean-analytics.md:24-25 (Croll & Yoskovitz, Lean Analytics ch.2 — good-metric properties + actionability)"
}
```

**Verdict logic:** `verdict = AND of all five per_property booleans`. If any property is `false` for either metric, that property's boolean is `false`, and the verdict is `FAIL`.

**Scope of each boolean:** each per-property boolean is the AND across both metrics (OMTM and secondary). If the OMTM passes a property but the secondary fails it, the property's boolean is `false` and the rationale must name which metric failed and why.

All four output fields must be non-empty on a successful return.

---

## Execution

### Step 1 — Load the Lean Analytics anchor

Read `<KL>/ProductManagement/Sources/Books/lean-analytics/lean-analytics.md` lines 24-25 (or open the file and locate the `### Key Rules` block under `## Analytics Foundations (Principle)`). The two anchor quotes are:

> "A good metric has four properties: A good metric is comparative... A good metric is understandable... A good metric is a ratio or a rate... A good metric changes the way you behave." — ch.2

> "Whenever you look at a metric, ask yourself, 'What will I do differently based on this information?' If you can't answer that question, you probably shouldn't worry about the metric too much." — ch.2

The skill's job is to apply these five properties to both candidate metrics.

### Step 2 — Apply the rubric (per metric, per property)

For each metric in `{omtm, secondary}`, evaluate each of the five properties:

| Property | Test |
|----------|------|
| **comparative** | Does the metric statement let you say "better/worse than X" — vs. a baseline period, a segment, a benchmark, or a target? A bare count ("5,000 signups") with no reference point fails. A rate or share ("12% of trial users convert, vs. 8% last quarter") passes. |
| **understandable** | Can a non-specialist in the user's organization restate what the metric means in one sentence without help? Compound metrics with hidden weights or domain jargon fail. |
| **ratio_or_rate** | Is the metric a ratio, a rate, a percentage, or a per-unit-time / per-unit-user normalization? Pure absolute counts ("number of page views", "total signups") fail. |
| **changes_behavior** | If this metric moves up or down by a meaningful amount, would the team take a different action than they would otherwise? Metrics that drift without triggering a decision fail. |
| **actionable** | Apply the ch.2 actionability check verbatim: "What will I do differently based on this information?" If the rationale for the metric can't end with a concrete next action the team would take, it fails (and is, in Lean Analytics' terminology, a vanity metric per `lean-analytics.md:36`). |

**Note on `changes_behavior` and `actionable`:** the source treats the `:25` actionability quote as a *check on* the `:24` "changes the way you behave" property, not as a separate concept. The skill exposes both as booleans because the plan §A6 contract requires five booleans, but the underlying judgment is the same — if the team has no action to take on the metric, both fail. How tightly to couple the two is an open framing question (see TODO `[Thought] lean-analytics-metrics — 5-vs-4-property framing`); for now the skill assesses each independently but expects them to move together in practice.

Anti-pattern table (illustrating the principle behind the ch.2 rubric; the OMTM/secondary pairings below are Slice-D-idiomatic extensions of the same principle, not verbatim Lean Analytics examples — the "page views" vanity classification is verbatim per `lean-analytics.md:44`):

| Output-shaped metric (FAIL) | Outcome-shaped metric (PASS) | Property that fails |
|------------------------------|------------------------------|---------------------|
| "page views" | "page views per active user per week, vs. prior 4-week average" | `ratio_or_rate`, `comparative` |
| "number of users in the database" | "share of users active in the last 30 days, vs. prior 30-day cohort" | `ratio_or_rate`, `comparative`, `changes_behavior` |
| "engagement score" (composite, opaque) | "median session length per active user, by cohort week" | `understandable`, `comparative` |

### Step 3 — Compute per-property booleans + draft verdict

1. For each property, set the boolean = `True` iff **both** metrics pass it. Otherwise `False`.
2. Compute `verdict = "PASS"` iff all five booleans are `True`; otherwise `"FAIL"`.
3. Draft a one-paragraph rationale that names, per metric, which properties pass and which fail, and why. If any property fails, the rationale must say which of OMTM or secondary caused the failure.

### Step 4 — Dispatch `/double-check` for independent verification

Producer never verifies its own output (`~/.claude/rules/factcheck-convergence.md` §6). Dispatch `/double-check` at the dial's `check` class — a discrepancy here flips a property boolean and amends the rationale rather than stopping the run:

```
/double-check --class check --rounds 1 --against "<candidate OMTM>; <secondary>; Lean Analytics ch.2 four-properties rubric (comparative / understandable / ratio_or_rate / changes_behavior) + actionability check"
```

The checker receives the per-property booleans + rationale as the claim package and verifies each property assignment against the rubric anchor.

- **Checker PASS:** keep the per_property booleans + verdict as drafted in Step 3.
- **Checker DIRTY / ESCALATE:** flip the disputed property's boolean to `False`, recompute verdict, and append a sentence to the rationale naming the disputed property and the checker's reason.

### Step 5 — Return

Return the JSON object specified in the Output contract. All four fields non-empty.

---

## Edge Cases

| # | Condition | Handling |
|---|-----------|----------|
| E1 | `omtm` missing or empty | Return `{"error": "omtm required", "verdict": null, ...}`. Do not invent one. |
| E2 | `secondary` missing or empty | Return `{"error": "secondary required", ...}`. Do not invent one. |
| E3 | `context` missing or empty | Return `{"error": "context required — cannot judge actionability without operational setting", ...}`. The actionability test ("What will I do differently?") depends on context. |
| E4 | OMTM and secondary are the same metric | Return `verdict: "FAIL"` with `rationale` flagging "OMTM and secondary are identical — secondary must give a different angle on the same outcome". All booleans set per the metric. |
| E5 | One metric matches a Lean Analytics vanity metric from `lean-analytics.md:44` (Number of hits / Number of page views / Number of visits / Number of unique visitors / Number of followers, friends, or likes / Time on site or number of pages / Emails collected / Number of downloads) | Set `actionable: false` and `changes_behavior: false` automatically (the source ties vanity classification to actionability failure — `lean-analytics.md:36`: "If you have a piece of data on which you cannot act, it's a vanity metric"; reinforced at `:45` re: total signups). `verdict: "FAIL"`. Rationale cites `lean-analytics.md:36` + `:44` and names the offending metric. Other properties (`comparative`, `understandable`, `ratio_or_rate`) are still assessed normally — most of these vanity items will independently fail `ratio_or_rate` because they are absolute counts. |
| E6 | `/double-check` returns ESCALATE on the assessment | Return `verdict: "FAIL"` regardless of Step 3 draft, with `rationale` appended: "Independent verification escalated — assessment requires user review." |
| E7 | Source file `lean-analytics.md` unreachable at runtime | Return the verdict + rationale, but flag `source_citation` as `"...lean-analytics.md:24-25 (UNVERIFIED — file unreachable at runtime)"`. Do not block. |

---

## Source

- `<KL>/ProductManagement/Sources/Books/lean-analytics/lean-analytics.md:24-25` — Croll & Yoskovitz, *Lean Analytics* ch.2 (four good-metric properties + actionability)
- `<KL>/ProductManagement/Sources/Books/lean-analytics/lean-analytics.md:44` — ch.2 list of eight vanity metrics
- `~/.claude/rules/plan-gates.md` — `0b: Desired Outcome`, **Testability** — the related observe-the-workflow test applied to outcomes at plan time. Note it is *not* the four-properties rubric: plan-gates.md carries no metrics gate, and the rubric itself lives only in this skill and its Lean Analytics source
- `~/.claude/rules/code_first_architecture.md` § "AI Adapter — Judgment only, behind a port" — judgment-port pattern
- `~/.claude/rules/factcheck-convergence.md` §6 — producer-never-verifies invariant
- `~/.claude/skills/double-check/SKILL.md` — verification pipeline dispatched in Step 4

---

## Sample invocation

**Input:**

```json
{
  "omtm": "page views per week",
  "secondary": "share of users who returned within 7 days of signup, vs. prior cohort",
  "context": "We are deciding whether the new onboarding flow is working. Goal: drive repeat usage of the core feature, not just first-touch."
}
```

**Output (expected — illustrates the contract):**

```json
{
  "verdict": "FAIL",
  "per_property": {
    "comparative": false,
    "understandable": true,
    "ratio_or_rate": false,
    "changes_behavior": false,
    "actionable": false
  },
  "rationale": "OMTM 'page views per week' matches the vanity-metric list at lean-analytics.md:44 — actionable=false (E5 short-circuit per :36) and changes_behavior=false (coupled to actionable per source). It also fails comparative (no baseline) and ratio_or_rate (absolute count over a time window, not a per-user normalization). Secondary 'share of users who returned within 7 days of signup, vs. prior cohort' passes all five properties on its own. Property booleans are AND across both metrics; OMTM drags every property to false except `understandable`.",
  "source_citation": "<KL>/ProductManagement/Sources/Books/lean-analytics/lean-analytics.md:24-25 (Croll & Yoskovitz, Lean Analytics ch.2 — good-metric properties + actionability)"
}
```

---

## Non-goals

- This skill does **not** propose metrics. It only validates a pair supplied by the caller.
- It does **not** set the "line in the sand" (target value) — that is Gate 4 in `/clarification`, owned by user judgment.
- It does **not** validate the *outcome* statement the metrics are measuring — that is `/outcome-framing` (A5).
- It does **not** dispatch its own multi-round convergence. The single `/double-check --class check --rounds 1` dispatch in Step 4 is the entire verification — the calling skill (`/clarification` step 8) decides whether to re-invoke on failure.
- It does **not** edit any files. It is a pure function: input triple → output struct.

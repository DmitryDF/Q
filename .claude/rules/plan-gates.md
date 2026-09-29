# Plan Gates — Mandatory Quality Checks

See `~/.claude/rules/factcheck-convergence.md` for canonical convergence rules.

Every plan MUST complete Gate 0 (problem framing) and Gates 1-3 (verification) before exiting plan mode. Code hooks enforce this — you cannot exit without the gate markers.

## Gate 0 — Strategic Kernel (5 parts, in order)

<!-- canonical-kernel:plan-gates.md is the single locus for Diagnosis/Guiding-Policy/Coherent-Actions definitions; pre-plan-gates.md references, never restates (OQ13 DECIDED user 2026-05-17). Slice I (future) will rewrite pre-plan-gates.md to reference. -->

Before designing any solution, frame the problem using Rumelt's kernel of good strategy. Each part must appear in the plan **in order** — the hook checks line positions. No diagnosis stated → no plan.

### 0a: Diagnosis

Identify the critical challenge. A good diagnosis simplifies complexity by identifying which aspects of the situation are critical — it doesn't just state what's broken, it explains *why* and defines where action can have leverage.

> "A good diagnosis simplifies the often overwhelming complexity of reality by identifying certain aspects of the situation as critical." — Rumelt ch.5

Anti-pattern: characterizing the challenge as "underperformance" without explaining causes. "Underperformance is a result. The true challenges are the reasons for the underperformance." — Rumelt ch.3

<!-- anchor: gate-0a-oq19 -->
**Plain business words first (OQ19).** Write the diagnosis in plain business words first; technical detail follows. A reader without codebase context should grasp *which aspect of the situation is critical and why* from the first paragraph alone. Technical specifics (file paths, function names, schema details) belong in a follow-up paragraph or in the Coherent Actions table — not in the Diagnosis opener. This is a checker-assessable judgment quality, not a code gate.

```
## Diagnosis
[What's the critical challenge? Which aspects are most important? Why?]
[Which aspect of the situation can be addressed by the actions available to you?]
<!-- GATE0A:PROBLEM -->
```

### 0b: Desired Outcome

Describe the future operational workflow — what the user experiences and what changes in practice after this plan is implemented. Focus on the outcome (the difference you make), not the output (what you build).

> "it's all about solving problems, not implementing features. Conventional product roadmaps are all about output. Strong teams know it's not only about implementing a solution. They must ensure that solution solves the underlying problem. It's about business results." — Cagan, Inspired ch.7

The outcome drives the reasoning chain: Gap Analysis (0c) surfaces what's missing to reach this operational state. Guiding Policy (0d) defines the technical approach. Coherent Actions (0e) specify the implementation. If the outcome already names the technical mechanism, Gap Analysis becomes gap-filling for a predetermined solution.

**Anti-pattern:** Technical implementation in the outcome. If you're naming models, algorithms, data structures, or iteration counts — that belongs in Guiding Policy (0d) or Coherent Actions (0e).

| Instead of (output) | Write (outcome) |
|---------------------|-----------------|
| "3 independent Sonnet checkers verify claims, minimum 2 rounds, hard stop at iteration 4" | "Research claims are independently verified to convergence before delivery; user sees convergence status and unresolved caveats" |

**Testability:** The outcome must be testable by observing the operational workflow, not by inspecting code. Ask: "Can I tell this is working by watching someone use it?"

```
## Desired Outcome
[Future operational workflow. What the user experiences. What changes in practice.]
[Observable, testable from the workflow — no technical mechanism.]
<!-- GATE0B:OUTCOME -->
```

### 0b2: Outcome Claims

Decompose the outcome into discrete, testable claims. Each claim is a statement that, if true, means one aspect of the outcome is achieved. Together, the claims must fully resolve the diagnosed problem.

> "An important duty of any leader is to absorb a large part of that complexity and ambiguity, passing on to the organization a simpler problem—one that is solvable." — Rumelt ch.7

This is the Cagan OKR pattern applied to planning: the outcome is the objective, the claims are the key results. "Objectives should be qualitative; key results need to be quantitative/measurable." (Cagan, Inspired ch.28). In planning terms: the outcome describes the qualitative operational change; the claims make it measurable.

> "Key results should be a measure of business results, not output or tasks." — Cagan, Inspired ch.28

Each claim must: (1) be testable from the operational workflow (same criterion as 0b), (2) trace to a specific aspect of the diagnosis, (3) be necessary — removing it would leave the diagnosis partially unresolved.

**Coverage check:** After listing claims, state: "When C1–Cn are all satisfied, [restate diagnosis] is resolved because [reasoning]." If you can't write this sentence convincingly, a claim is missing or the diagnosis needs refinement.

The claims→gaps link is explicit in Cagan's scaled OKR process: "the leadership team looks at the proposed key results from the product teams and identifies gaps and then looks to what might be adjusted to cover those gaps" (ch.30). Same pattern: decompose the outcome into claims, then identify gaps per claim.

**Anti-pattern:** Claims that describe implementation rather than operational state. Same rule as 0b — if you're naming models, data structures, or code patterns, it belongs in Guiding Policy (0d) or Coherent Actions (0e).

Since **plan-claim-single-source-of-truth (2026-07-18)** the Outcome Claims are ONE
structured list — a fenced ` ```json ` claim-set inside the `## Outcome Claims`
section — **not** a re-worded markdown table plus a separate `GATE0B2:CLAIM_SET` json.
There is no second copy to keep in sync, so the `From` column, the
`_plan_claim_linkage.py` reconciliation check, and the `GATE0B2:CLAIM_SET` separate
marker are all **retired**. The single json IS the list the whole plan is built from.

```
## Outcome Claims

```json
{
  "source_type": "web", "source_path": "plan:0b2", "lang": "en",
  "thoroughness": "deep",
  "claims": [
    {"text": "[testable statement about the operational workflow]",
     "id": "C1", "addresses_diagnosis": "[which aspect of the diagnosis this resolves]",
     "role": "backward", "locator": "DO-1",
     "flags": {"atomicity": true, "verifiability": true, "decontextuality": true,
               "minimality": true, "fluency": true, "faithfulness": true}}
  ],
  "refused": []
}
```

**Coverage:** When C1–Cn are satisfied, [diagnosis] is resolved because [reasoning].
<!-- GATE0B2:CLAIMS -->
```

**Two /plan-scoped per-claim fields — `id` + `addresses_diagnosis` (code-required).**
Every claim in the single list MUST carry a non-empty **`id`** (e.g. `"id": "C1"` — the
token a Gap's `Claim` cell references) and a non-empty **`addresses_diagnosis`** (the
aspect of the Diagnosis that claim resolves — the author writes this; the engine does not
know the Diagnosis). `check-plan-gates.sh` enforces both via `_plan_claim_gate.py`
(/plan-scoped — the shared `_claim_persist.parse_claim_set` still treats `id` as optional
and does not know `addresses_diagnosis`, so the other three engine consumers are
unaffected). A claim missing either field blocks ExitPlanMode.

**Claims↔Gaps is bidirectional.** `_plan_claim_gate.py` reads the claim ids from the
single list and checks BOTH directions (mirroring the proven `G#` block): forward — every
claim id is referenced by ≥1 gap `Claim` cell; reverse — every gap `Claim` ref resolves to
a real claim id (a gap citing a phantom id blocks). The reverse direction closes the
forward-only hole the old shell check missed.

**Deep engine, code-enforced.** The single list is produced by the Claim-Identification
engine's two-stage contract over the Desired-Outcome text **at DEEP thoroughness** (the
benchmark-proven higher-coverage setting — `Thoughts/refc-conformance-flip-*_RESEARCH.md`;
NORMAL under-recalls) (Stage 1 identify + ambiguity-refusal gate → Stage 2
decontextualize/structure; six per-criterion flags), then a **moderate consolidation pass**
(`_claim_consolidate`), and is schema-validated through the same shared seam A1 uses
(`check-plan-gates.sh` pipes the fenced json through `_claim_persist.parse_claim_set`,
`--site plan:0b2`). The `--site plan:0b2` makes the seam **enforce `thoroughness: "deep"`**
(CF-4): a NORMAL claim-set is refused (no silent "flip ships NORMAL"), so the producer must
run DEEP or record a `claim_fallback_reason:`. The "engine ran at DEEP" decision therefore
lives in code, not skill text (`code_first_architecture.md` — code owns the flow). This does
**not** change the `GATE0B2:CLAIMS` marker contract or the `GATE0B2:VALIDATED` per-transition
coverage dispatch.

**Retained fallback (reason-logged, never a silent skip).** If the DEEP engine genuinely
can't run — or the author deliberately opts out — hand-decompose the Outcome Claims as a
plain markdown table (the only list on this degraded path) AND emit, inside the
`## Outcome Claims` section, a non-empty `claim_fallback_reason:` line **instead of** the
fenced json. The reason must name a concrete engine-failure category
(`engine-error:`/`zero-claims:`/`timeout:`/`plan-mode-seam-blocked:`) or the explicit
override `user-acknowledged-skip:` (shared grammar `dc_obligation.validate_fallback_reason`;
a bare elective skip is refused). The Claims↔Gaps bidirectional check still runs on this
path (ids from the markdown table); the per-claim `addresses_diagnosis` requirement does
not (no structured claims).

**Producer wiring + tracking parity (4th consumer).** The producer is `/plan` Step 5b
(`~/.claude/skills/plan/SKILL.md`): a skill-dispatched Explore claim-identification adapter
over the finalized Desired Outcome at DEEP → moderate consolidation → one Edit emitting the
single json list — the same subagent-dispatch pattern the sibling consumers `/clarification`
Step 2 and `/extract-knowledge` carry (NOT the `/double-check` `_dc_claim_seam` code seam).
Tracking is symmetric on both paths and site-keyed like the siblings: on a valid claim-set
the seam call writes a `manageable_record`; on the `claim_fallback_reason:` branch
`check-plan-gates.sh` writes a `fallback_record` (via `_claim_persist --fallback-reason`
→ `_claim_metrics.fallback_record`) to the plan's co-located `.claim-runs.md`. (A central
`claim_identify()` dispatcher converging all four consumers is a noted follow-up, not yet
built.)

### 0c: Gap Analysis

List what's missing between current state and desired outcome. Each gap should trace to at least one outcome claim (C#). Gaps without a claim reference are either missing a claim in 0b2 or out of scope.

```
## Gap Analysis
| # | Gap | Current State | Desired State | Claim |
|---|-----|---------------|---------------|-------|
| G1 | ... | ... | ... | C1 |
<!-- GATE0C:GAPS -->
```

> If the gap table has more than 4 gaps, consider whether the diagnosis is specific enough. "Strategy is primarily about deciding what is truly important and focusing resources and action on that objective. It is a hard discipline because focusing on one thing slights another." — Rumelt ch.5

### 0d: Guiding Policy

State the overall approach to closing the gaps. The guiding policy channels action without defining exact steps — like guardrails on a highway. It should tackle the obstacles identified in the diagnosis by creating or drawing upon sources of advantage.

> "The guiding policy outlines an overall approach for overcoming the obstacles highlighted by the diagnosis. It is 'guiding' because it channels action in certain directions without defining exactly what shall be done." — Rumelt ch.5

A guiding policy creates advantage through: (1) anticipating reactions, (2) reducing complexity, (3) concentrating effort on the pivotal aspect, (4) ensuring actions are coherent.

Anti-pattern: staying at the level of intent — "As long as strategy remained at the level of intent and concept, the conflicts among various values remained tolerable. It was the imperative of action that forced a decision." — Rumelt ch.5

Anti-pattern: conflicting guiding policies — a policy that contains two incompatible directions is not a policy, it avoids the hard choice. Ford adopted "protect brand equity" and "share platforms across brands" simultaneously — two policies that directly contradicted each other. — Rumelt ch.5

```
## Guiding Policy
[The approach — how will you tackle this? What direction constrains the actions?]
<!-- GATE0D:POLICY -->
```

### 0e: Coherent Actions

Map each action to a specific gap AND to the guiding policy. Actions must be coordinated — not just individually correct but mutually reinforcing. State explicitly how the policy addresses the diagnosis and how actions reinforce each other.

> "Strategic coordination, or coherence, is not ad hoc mutual adjustment. It is coherence imposed on a system by policy and design." — Rumelt ch.5

Anti-pattern: listing uncoordinated actions that each map to a gap but don't reinforce each other.

```
## Coherent Actions
| Action | Addresses Gap | What it does |
|--------|---------------|--------------|
| A1: ... | G1 | ... |

**Coherence:** [1-2 sentences: How does the guiding policy address the diagnosis? How do the actions reinforce each other to carry out the policy?]
<!-- GATE0E:ACTIONS -->
```

### 0f: Design Review

After writing Coherent Actions and before Implementation Specifics, re-read the exploration findings and critically review the design:

1. **Trigger/threshold audit:** For each proposed trigger, threshold, or boundary — is there an expert source? If editorial, is it calibrated against expert data or arbitrary?
2. **Alignment check:** Do proposed mechanisms align with existing code patterns (expert_router situations, Workflow.md rules)? Flag misalignments.
3. **Edge cases:** New portfolio (0 data), single position, all approaches, first-run (no prior data).
4. **What each expert cluster would disagree with** — approach-specific biases in the design.
**For coding plans — Cockburn design tests:**
5. **Abstraction Test** — do component names convey their role?
6. **Responsibility Alignment** — do name, responsibility, and interface align?
7. **Evolution Test** — if a decision changes, how many components must also change?
8. **Design sources consulted?** If not loaded during Explore, state why.
9. **Record findings and fixes** in the plan body.

```
## Design Review
[Findings from review, fixes applied, remaining biases noted]
<!-- GATE0F:REVIEWED -->
```

### Ordering rule

The hook enforces: `GATE0A` < `GATE0B` < `[GATE0B2]` < `GATE0C` < `GATE0D` < `GATE0E` < `GATE0F`. This prevents writing implementation first and retroactively adding framing. After the design review, continue with **Implementation Specifics** and then Gates 1-3.

When the Slice I markers below are in use, the ordering extends to: `GATE0:CHAIN_SRC` < `GATE0A` < `[GATE0A:VALIDATED — Mode C only]` < `GATE0B` < `GATE0B2` < `GATE0B2:VALIDATED` < `GATE0C` < `GATE0C:VALIDATED` < `GATE0D` < `GATE0SR:SLICES` < `GATE0E` < `GATE0F` < `GATE0G:COHERENCY`.

## Gate 0 — Chain Consumption + Per-Transition Validation + Coherency (Slice I)

Plans authored under Slice I (`/plan` Phase 3 enhance) are downstream **consumers** of the upstream chain produced by `/clarification` (Phase 1) and `/solution-design` (Phase 2). The markers below make that consumption + per-transition verification + final coherency check machine-readable.

**Opt-in semantics (chain + coherency gates only).** Presence of `<!-- GATE0:CHAIN_SRC -->` is the sole opt-in **for the chain-consumption and per-transition/coherency gates** (`GATE0A/0B2/0C:VALIDATED`, `GATE0SR:SLICES`, `GATE0G:COHERENCY`). When the marker is absent, none of *those* gates fire — plans authored before Slice I continue under legacy rules for them. When the marker is present, the per-mode required-set below applies and `check-plan-gates.sh` enforces it.

**The implementation-model contract is NOT opt-in.** Independently of `GATE0:CHAIN_SRC`/`GATE0SR:SLICES`, **every** plan with a Coherent Actions zone must carry the 7-column schema and declare a Model per action, with at least one real family (see "Coherent Actions — required for all plans" below). `check-plan-gates.sh` enforces this on **both** ExitPlanMode paths (it is the shared checker called by `permission-plan-gate.sh` and `stop-plan-gate.sh`), and `check-impl-models.sh` enforces the declared model at runtime for any plan that declares one — regardless of the opt-in markers.

<!-- anchor: engine-consumer-receipt-contract -->
### Engine-consumer model + receipt contract (plan-validation-engine-consumer, 2026-07-18)

Plan validation is a **consumer of the one shared validation engine**, not a fork. Two mechanisms carry this, and together they close the honor-system hole where the gate trusted an author-typed `verdict:` line:

1. **Code authors the verdict (S1).** The `/plan` orchestrator dispatches its per-transition (0A/0B2/0C/0D/0E) and final-coherency (0G) checkers via the Agent tool (the engine's own `claude --print` dispatch fails in-session), captures their raw outputs, and hands them to `pre_plan_gates.py factcheck-plan-step <GATE>` / `factcheck-plan-coherency`. Those CLIs compute the round verdict via the **shared** engine aggregator (`_factcheck_engine.aggregate_round_verdict`, `kind="plan"` — each checker's final `VERDICT:` token decides, CoT-safe, folding the 0G per-axis AND-gate + cross-axis into that one token) and write a code-owned `R<N>.md` engine **receipt** (schema `factcheck-convergence.md` §7) keyed to `(plan-file, gate)`. A producer-supplied verdict is never consulted (`code_first_architecture.md` — code owns the verdict; producer-never-verifies).

2. **The gate reads the receipt (S2).** `check-plan-gates.sh` resolves each Slice-I gate's receipt via the `pre_plan_gates.py plan-receipt-dir` verb (the single shared path locus — the shell never re-implements the hash) and admits the section **only** on a converged `verdict: PASS` (or a recorded operator override); a DIRTY/ESCALATE/missing receipt **blocks** `ExitPlanMode`, naming the section. This extends the same `R<N>.md` reader the gate already used for Gate 3, and it **fixes the E4/0G threshold hole** (the old grep silently admitted `verdict: PASS|DIRTY|ESCALATE`). The `verdict:` line still written into each `*:VALIDATED` / `GATE0G:COHERENCY` block is **informational** (the human record); it no longer unlocks approval.

**Operator override (the recorded escape).** The legitimate "proceed as is" choice on an unresolved ESCALATE is recorded with `pre_plan_gates.py plan-override <GATE> <plan> --reason "…"`, which writes an `OVERRIDE` marker into the gate's receipt dir. The gate admits an overridden section regardless of receipt verdict. The override is **explicit + reason-required + recorded** — never a silently-typed line (Design Review residual #9).

**Receipt location.** `~/.claude/state/plan_validation/sections/<sha256(resolved-plan-path)[:12]>/<gate>/R<N>.md` (+ optional `OVERRIDE`). Keyed on the resolved plan-file path so writer and reader agree with no dependence on mutable session/topic state. State-dir only — not part of the promoted config.

**Define-once-propagate (S5).** Because convergence, axes, and the marker live in the one engine, improving the engine once improves plan validation automatically — there is no plan-specific copy to keep in sync. On-demand section validation and whole-plan validation are the **same** mechanism exposed ad-hoc (a per-transition check is a per-section validation with a fixed `--against`; the final coherency check is whole-plan validation as a strict rollup of per-section receipts — never one omnibus judge).

### Marker: `GATE0:CHAIN_SRC`

Position: BEFORE `GATE0A:PROBLEM`. Carries the chain-provenance payload as `key: value` lines (anywhere in the immediately following block until the next HTML comment):

```
<!-- GATE0:CHAIN_SRC -->
mode: A                              # A = full upstream / B = chain + /solution-design invoked / C = Ninja-Plan
thought_file: <path-or-"n/a">        # spine path for Modes A/B; "n/a" for Mode C
discovery_src_hash: <12-hex or "n/a"> # SHA-256[:12] of locked-Discovery fields (pre_plan_gates._discovery_locked_fields_hash); "n/a" for Mode C
alternative_n: <int-or-"n/a">        # for Mode A/B when /solution-design has run; "n/a" otherwise
```

**Mode-C `discovery_src_hash` sentinel.** Mode C (Ninja-Plan) has no upstream Discovery to fingerprint (`plan-mode-init --mode C` emits null), so it declares `discovery_src_hash: n/a` — the freshly-authored Diagnosis is anchored instead by `GATE0A:VALIDATED`. `check-plan-gates.sh` accepts `n/a` **only** when `mode: C`; A/B keep the strict 12-hex requirement (their provenance anchor). The hash is a shape-check with no downstream consumer for Mode C, so `n/a` and a real hash are behaviorally identical there — the sentinel is the honest form. This mirrors the existing `thought_file: n/a` Mode-C sentinel (line above).

### Marker: `GATE0A:VALIDATED` (Mode C only)

Position: between `GATE0A:PROBLEM` and `GATE0B:OUTCOME`. Mode C plans introduce a fresh Diagnosis (no upstream); this marker carries the per-transition `/double-check 1,1,3` verdict (1 Sonnet + 1 Opus, early-exit on Round-1 PASS, plan-kind `max_rounds=3` per `factcheck-convergence.md` §4). **Verdict authority is the engine receipt** (see "Engine-consumer model + receipt contract" above): `check-plan-gates.sh` admits `GATE0A:VALIDATED` only when the `0A` engine receipt is a converged PASS (or a recorded override), written by `factcheck-plan-step 0A` from the captured checker outputs. The `verdict: PASS` line written near the marker is the human record — it no longer decides the gate.

### Marker: `GATE0B2:VALIDATED` (all modes)

Position: between `GATE0B2:CLAIMS` and `GATE0C:GAPS`. Carries the per-transition verdict for Outcome Claims ↔ Desired Outcome coverage. Same format as `GATE0A:VALIDATED`.

### Marker: `GATE0C:VALIDATED` (all modes)

Position: between `GATE0C:GAPS` and `GATE0E:ACTIONS`. Carries the per-transition verdict for Gap Analysis ↔ Outcome Claims + existing codebase. Same format as `GATE0A:VALIDATED`.

### Marker: `GATE0SR:SLICES`

Position: between `GATE0D:POLICY` and `GATE0E:ACTIONS`. Carries the slice register reference + the slice-id under-plan + the per-slice Implementation Session table schema:

```
<!-- GATE0SR:SLICES -->
slice_register_ref: <spine-file>#slice-register   # e.g., workflow-phases-redesign_THOUGHT.md#slice-register
slice_id: <id>                                    # e.g., I (matches an entry in the spine slice register)
```

Plans nest Implementation Sessions under the slice. Each session row carries a Model + Reason column; each session's Coherent Actions follow the new 7-column schema below.

### Coherent Actions — 7-column schema (required for all plans)

**Every** plan with a Coherent Actions zone uses this schema — independently of `GATE0:CHAIN_SRC` (the legacy "Plan Mode Additions" 3-column form with separate `| Model | Reason |` columns in `pre-plan-gates.md` is **superseded**; the 7-column schema is now mandatory, not Slice-I-only):

```
| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1     | G1            | ...  | ...         | ...             | opus  | ...    |
```

**Model allowlist + required declaration.** The Model column accepts only `sonnet`, `opus`, or `haiku` (family slug, lowercase), or the em-dash (`—`). The em-dash is allowed for mechanical rows that don't spawn a subagent (e.g., `A0 (relocate)`, `V1 (commit + smoke)`). Two mandatory rules enforced by `check-plan-gates.sh` for **all** plans: (1) **no empty Model cell** — every action row must declare a value (a family or `—`); (2) **at least one real family** — `—` alone for every row is rejected, since the work must run on *some* model. This declaration gate is unconditional (not gated on `GATE0:CHAIN_SRC`/`GATE0SR:SLICES`) and runs on both ExitPlanMode paths; the declared model is then enforced at runtime by `check-impl-models.sh` for any plan that declares one (subagent spawns — `Agent` PostToolUse; compared against the union of declared families).

**Plan-detail level.** Each row describes Goal + Guard rails + Validation gate. Do not enumerate micro-steps; that violates `prompt-engineering.md` "prefer general instructions over prescriptive steps" + Cockburn nano-increments.

<!-- anchor: skill-internal-locked-step-contracts -->
**Skill-internal locked step contracts.** This rules file's "prefer general instructions over prescriptive steps" + Cockburn nano-increments rules apply to **plan Coherent Actions tables** in any `_PLAN.md` file — the bound scope where the Coherent Actions schema (7-column with Goal / Guard rails / Validation gate) lives. They do **not** apply to **skill-internal locked step contracts** — a distinct category where a narrow-scope skill (e.g., `/ninja-fix`, `/clarification`) bounds its own outcome through a named multi-step protocol whose steps are not editable per invocation. Such contracts are sanctioned because: (i) the steps are the skill's own design surface, not a plan's execution surface; (ii) the skill's own validation gate (e.g., `/double-check 3,1,1` for `/ninja-fix`) provides the producer-never-verifies discipline at the skill scope; (iii) the steps express the Rumelt kernel (Diagnosis → GP → Action) inline so the AI cannot conflate them. When authoring a skill with a locked step contract: name each step with a single subsection heading, state the step's gate-or-deliverable in plain words, and reference any external validator. Do **not** introduce a locked step contract that duplicates a plan-mode gate — that's plan-mode's surface.

### Marker: `GATE0G:COHERENCY`

Position: after `GATE0F:REVIEWED`. Carries the final coherency check verdict + rounds + checker_models from `pre_plan_gates.py factcheck-plan-coherency` (3 Sonnet + 1 Opus against Diagnosis + Desired Outcome + Guiding Policy with explicit per-spawn model pinning, plan-kind `max_rounds=3`). **Verdict authority is the engine receipt** (see "Engine-consumer model + receipt contract" above): the CLI computes the verdict via the shared engine from the captured checker outputs and writes the `0G` receipt; `check-plan-gates.sh` admits only a converged PASS (or a recorded override) and **blocks on DIRTY/ESCALATE/missing** (fixes the earlier grep that admitted `PASS|DIRTY|ESCALATE`). The block below is the human record:

```
<!-- GATE0G:COHERENCY -->
verdict: PASS                                     # PASS | DIRTY | ESCALATE
rounds: 1                                         # 1..3 (plan-kind max_rounds=3)
checker_models: [sonnet, sonnet, sonnet, opus]    # per-spawn family slugs
```

**The 0G rounds ceiling shares the 0G override marker (2026-08-08).** A declared `rounds:` above the plan-kind `max_rounds=3` **blocks** by default — the ceiling is not weakened, and the counter is never reset by a plan edit; a ceiling breach is a real process signal, not noise. But it is admitted by the **same recorded 0G `OVERRIDE` marker** that admits a non-PASS 0G receipt (`pre_plan_gates.py plan-override 0G <plan> --reason "…"`) — no separate marker, no separate verb. **Why one marker:** overriding a non-PASS 0G receipt is the strictly *more* permissive concession (it admits a plan the engine says never converged), so it already subsumes admitting a converged-but-churny one; a second marker would add a primitive without closing a hole the receipt override does not already open. **The shared-marker cost is made visible, not silent:** when the override is what admits the excess rounds, `check-plan-gates.sh` says so on stderr on both the passing and the `--dry-run` path, so an operator who recorded the override for a DIRTY receipt learns that it also cleared the ceiling. The marker is read **per gate** — an override recorded on 0C does not reach the 0G rounds check. Locked by `test_plan_gates.sh` cases 38–43.

### Per-mode required-set (when CHAIN_SRC is present)

| Mode | Required markers |
|------|------------------|
| A    | `CHAIN_SRC`, `GATE0B2:VALIDATED`, `GATE0C:VALIDATED`, `GATE0SR:SLICES`, `GATE0G:COHERENCY` |
| B    | `CHAIN_SRC`, `GATE0B2:VALIDATED`, `GATE0C:VALIDATED`, `GATE0SR:SLICES`, `GATE0G:COHERENCY` |
| C    | `CHAIN_SRC`, `GATE0A:VALIDATED`, `GATE0B2:VALIDATED`, `GATE0C:VALIDATED`, `GATE0SR:SLICES`, `GATE0G:COHERENCY` |

Mode A and B differ in upstream completeness (A consumes the existing `### Solution Alternative N`; B triggers `/solution-design` whole inside the orchestrator) but share the same required-set because both consume Diagnosis from the spine verbatim.

`check-plan-gates.sh` enforces presence + ordering + verdict + per-mode required-set. Missing markers or non-PASS verdicts block ExitPlanMode.

<!-- anchor: verification-source-registry -->
## Verification Source Registry

When verifying claims, use the correct source for each category:

| Claim Category | Verification Source |
|---------------|---------------------|
| Hooks & Events | code.claude.com/docs/en/hooks, /hooks-guide |
| Tools & Capabilities | code.claude.com/docs/en/ (tools section) |
| Settings & Config | code.claude.com/docs/en/settings |
| Permissions | code.claude.com/docs/en/permissions |
| Model capabilities | docs.anthropic.com/en/docs/about-claude/models |
| API & SDK | docs.anthropic.com/en/api/, platform.claude.com/docs/en/ |
| Platform & Subscription | platform.claude.com/docs/en/ |
| Plan mode | code.claude.com/docs/en/ (plan section) |
| Skills & Subagents | code.claude.com/docs/en/skills, /sub-agents |
| Memory & CLAUDE.md | code.claude.com/docs/en/memory |
| Code vs Skills boundary | Check existing hooks/skills in project |
| Hallucination risk | 2+ independent sources must agree |
| Business logic & strategy | Named framework (Rumelt, Porter, Jobs-to-be-Done, etc.) or user-stated business rule |
| Domain expertise | Named principle, industry standard, or expert source (book ch., article) |
| User requirements | User message in conversation (quote or paraphrase) |
| AI platform docs | `<KL>/AIDevelopment/Themes.md` (cross-platform routing table) |
| Software design | Cockburn (<KL>/Development/Sources/Books/simplifying-software-design-cockburn/) + code-first architecture (~/.claude/rules/code_first_architecture.md, if AI runtime) |
| Prompt engineering | platform.claude.com/docs/en/docs/build-with-claude/prompt-engineering/claude-prompting-best-practices (single living reference for all Claude 4.x models) |

## Gate 1 — Implementation Verification

**What belongs in this table — the litmus test.** A statement belongs in Gate 1 if and only if it carries either a `<file>:<line>` source anchor (with a backtick-quoted snippet) or a `<URL>` + named-claim that an external source attests. Statements that reason about *why* the change matters, *what* the outcome looks like, or *how* the actions reinforce each other belong in the Gate 0 chain sections (Diagnosis / Outcome / Outcome Claims / Gap Analysis / Guiding Policy / Coherent Actions) and are independently verified by the per-transition `GATE0A/0B2/0C:VALIDATED` + `GATE0G:COHERENCY` dispatches defined in the Slice-I section above. The two layers split the verification load on purpose — Gate 1 grounds individual anchors; the chain markers grade the reasoning that uses them.

For each recommendation: (1) classify into a registry category, (2) identify source location (file path + line range, URL, or framework name), (3) fetch and read the source, (4) record findings, (5) tag `[verified: <source>]` citing the real source — e.g. `[verified: fundamentals.py:214]` — or `[unverified — alternative: X]`. (The checker also accepts the bare literal `[verified: artifact]` for backward compatibility, but cite the real source.)

**Source sufficiency:** Technical claims need one authoritative source (file path + line, or docs URL). Business/logic claims need a named framework or KB extraction. After checking 2 sources without resolution, mark `[unverified — needs user input]` and move on.

Write verification between markers:

```
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status |
|-------|----------|----------------|-----------------|--------|
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->
```

Only add `<!-- GATE1:VERIFIED -->` when ALL claims are tagged.

### Source-Grounding Enforcement (Code-Enforced)

The `check-plan-gates.sh` hook reads cited source files and verifies Gate 1 evidence. Every "Verified Against" entry citing a file path must include a line number + backtick-quoted snippet. The hook checks the snippet exists near the cited line (±5 lines). URL claims need descriptive text > 40 chars with a recognized source keyword. Claims without verifiable evidence block ExitPlanMode.

**Required format:** File claims: `line 25: \`$TOOL_NAME != "Agent"\``. URL claims: descriptive text with source keyword (e.g., `code.claude.com`).

## Gate 2 — Code vs AI Boundary

Classify each step: **Code** (hook/script/test enforces it), **AI** (rules file + judgment), or **FLAGGED** (AI self-policing a quality gate — must be converted to code).

```
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->
```

Only add `<!-- GATE2:BOUNDARIES -->` when: all steps classified, no FLAGGED items remain (converted to Code or user-approved via `<!-- GATE2:USER_APPROVED -->`), Code rows name a specific mechanism, AI rows reference a specific rules file or principle.

## Design Review

After verifying claims (Gate 1) and classifying boundaries (Gate 2), step back and assess the whole design. You now have information unavailable during kernel construction: which claims needed correction, where the AI/code boundaries fell, what verification revealed.

> "A good strategy doesn't just draw on existing strength; it creates strength through the coherence of its design." — Rumelt intro

**Review the full chain:**
1. **Chain integrity:** Trace one claim end-to-end: Diagnosis → Claim (C#) → Gap (G#) → Action (A#). Does the chain hold after verification?
2. **Outcome delivery:** Do the actions, taken together, produce the claimed outcome? Or do they close gaps without delivering the operational change?
3. **Interaction effects:** Do any actions conflict, create uncaptured dependencies, or have emergent effects visible only after verification?
4. **Verification impact:** Did Gates 1-2 reveal anything that changes the design? Corrected claims, boundary surprises, unverified assumptions?

If the review surfaces issues, revise the kernel before proceeding to Gate 3.

```
<!-- GATE2B:DESIGN_REVIEW -->
```

## Gate 3 — Claim Verification

### Three tracks

| Track | Marker | When to use |
|-------|--------|-------------|
| **No claims** | `<!-- GATE3:NO_CLAIMS -->` | Purely structural changes. No behavior assertions. |
| **Internal claims** | `<!-- GATE3:INTERNAL_ONLY -->` | Claims verifiable from codebase. Needs `GATE3:START` with ≥1 row. No convergence. |
| **External claims** | `<!-- GATE3:CLAIMS_OK -->` | External APIs, docs, frameworks. Full convergence protocol. |

Track selection: all claims from repo files → INTERNAL_ONLY. Any external claim → CLAIMS_OK. No claims at all → NO_CLAIMS.

### Convergence protocol (CLAIMS_OK track only)

Rounds 1-2: full check with 3 Sonnet (canonical: `~/.claude/rules/factcheck-convergence.md` §1; engine default `_factcheck_engine.py:33` `CHECKER_MODELS`). After Round 2: diff-only. After Round 3: root-cause required (`<!-- GATE3:ROOT_CAUSE -->`). After Round 5: hard stop (existing hook fires at HIGHEST_ROUND ≥ 6, i.e., after Round 5 has run).

**Checker context:** Convergence checkers are subagents and don't inherit skills from the parent conversation. When spawning checkers, include source-grounding rules explicitly in the task prompt (quote-first extraction, source-language constraint, label editorial frames, count-check with enumeration).

Write rounds between `<!-- GATE3:START -->` and `<!-- GATE3:END -->`, then add track marker.

### Cross-session isolation

**Authoring-time location (2026-07-06, DS5 reversed).** ALL plans — every mode, worktree or not — are AUTHORED at the harness-designated `~/.claude/plans/<harness-slug>.md` during plan mode; that is the only path `check-plan-readonly.sh` permits and the only one that does not trigger the harness's silent plan-mode exit (a successful Write to any other file during plan mode drops the session to `acceptEdits` with no `ExitPlanMode`/approval — see `bookkeeping-model.md` §5 + drifter #10). `check-plan-gates.sh` validates the plan there via the `track-plan-file.sh` manifest (`find-session-plan.sh`). The `<project_root>/Thoughts/<project_slug>_PLAN.md` durable copy is created only AFTER approval, by `pre_plan_gates.py relocate-plan-after-approval` (`/plan` Step 11), outside plan mode; project-side plans live next to their `_THOUGHT.md` and are version-controlled with the project. Use `--worktree` for concurrent plan-mode sessions; worktree detection (`pre_plan_gates._in_worktree()`, `git rev-parse --git-common-dir` vs `--git-dir`) still governs whether a spine relocation target exists.

## Scope

**Rejected design — conditional `paths:` scoping.** Making this file load only in
plan-mode sessions via `paths:` frontmatter was investigated and rejected on
mechanism (it fails three independent ways, and fails *silently*). The reasoning is
recorded at `~/.claude/docs/rejected-conditional-rule-scoping.md` — backticked, not
`@`-prefixed, because an `@` would re-import that document into the always-loaded
set and defeat the point. Read it before re-proposing the idea.

- **Gate 0 (a-e, including 0b2):** Always required. Ordering enforced by hook.
- **Gates 1-2 + Design Review:** Always required.
- **Gate 3:** Choose appropriate track.
- **Enforcement:** `check-plan-gates.sh` (shared logic) called by `permission-plan-gate.sh` (ExitPlanMode) + `stop-plan-gate.sh` (Stop fallback).

---

## Gate 1T — Business Rule Disposition (opt-in per project)

Gate 1T is **opt-in per project** and is inert unless a project declares the three keys below in its `CLAUDE.md`. Enforcement is `check-kb-disposition-gate.sh` (global hook, after `permission-plan-gate.sh` / `stop-plan-gate.sh`), which reads the plan — never this file — so the spec's location does not affect the gate.

**Full spec** — disposition enum, the 7-column Gate 1 schema, the INDEX-routed expert-absence scan, the separate-checker protocol, the `GATE1T:USER_APPROVED` marker, ledger discipline, and the hook's behaviour — lives with the project that opted in:
`Personal/your-project/.claude/rules/plan-gates-1t-business-rule-disposition.md` (relocated 2026-08-16; the body was carried verbatim).

The three opt-in keys are kept inline here because they are the only Gate 1T facts a session could need before it has any reason to open the spec:

```
knowledge_library: <repo-relative path to the KL root for this project>
knowledge_library_index: <repo-relative path to the KL INDEX.md>
hypotheses_ledger: <repo-relative path to the project's editorial-hypotheses ledger>
```

Absent declarations → the hook exits 0 silently. Missing ledger but present KL → the hook blocks any `editorial-user` tag (no home for the hypothesis).

<!-- The remainder of the former Gate 1T body was relocated 2026-08-16. Rationale: global rules
     should not carry project-specific opt-in content that exactly one project can execute.
     This was an ownership decision, not a size one — see the plan's Guiding Policy. -->

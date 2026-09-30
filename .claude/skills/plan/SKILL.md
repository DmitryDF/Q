---
name: plan
version: "0.1"
description: AI orchestrator — coordinates Phase 3 (planning). Consumes the upstream chain from a topic `_thought` (Mode A full upstream / Mode B chain present + invoke `/solution-design` first / Mode C Ninja-Plan no spine) BEFORE `EnterPlanMode` fires. Sequences per-transition coherency dispatches (`factcheck-plan-step 0A/0B2/0C`) and a final coherency check (`factcheck-plan-coherency`) at each structural transition with explicit per-spawn model pinning (Opus = primary thinker, Sonnet = support; early-exit on Round-1 PASS). After plan approval (ExitPlanMode), hard-gates the next move via an AskUserQuestion (a) handoff-prompt / (b) /close+/clear / (c) continue-in-session triple driven by the `post-plan-uxgate.sh` + `check-post-plan-pending.sh` marker pair.
allowed-tools: Read, Write, Edit, Agent, Bash, Skill, AskUserQuestion
---

# /plan — Phase 3 Orchestrator

Coordinate Phase 3 (planning) in a single command. The orchestrator runs **strictly BEFORE `EnterPlanMode` fires** — every spine read, every cross-phase fetch, every per-transition verification dispatch, and every spine writeback happens in the orchestrator phase. Plan mode itself is reserved for operational delivery: per-slice Coherent Actions + Design Review + Gate-1/2/3 verification, all written into the plan file (the only path `check-plan-readonly.sh` allows during plan mode).

The skill is a **stateless thin orchestrator** in the hexagonal-AI-adapter pattern (`~/.claude/rules/code_first_architecture.md`, lines 81–128). Code owns the flow (the new CLIs in `pre_plan_gates.py`: `plan-mode-init`, `link-plan-to-thought`, `factcheck-plan-step`, `factcheck-plan-coherency`); the AI sits behind ports and only renders judgment when code cannot (Coherent Actions design, Mode B's `/solution-design` invocation, the per-transition checker prompts). Producer-never-verifies is enforced **at each structural transition**, not just once — Outcome → Outcome Claims (0B2), Outcome Claims → Gap Analysis (0C), full plan → Diagnosis + Desired Outcome (0G), plus Mode-C fresh-Diagnosis (0A).

Main-session-only. Cannot be invoked from inside a subagent.

---

## Trigger

Activate when the user says:
- `/plan`
- `/plan --help`
- "plan this"
- "let's plan"
- "phase 3"
- "ready to plan"
- "enter plan mode" (route through the orchestrator, not directly to `EnterPlanMode`)

The plan-mode hotkey (Shift+Tab → `EnterPlanMode`) is gated by `check-thought-bound.sh` (PreToolUse on `EnterPlanMode`). If the user presses the hotkey without a `plan-mode-init` marker, the hook surfaces the Track-properly vs Ninja-Plan choice and the user is routed back through this skill.

---

## When to Use

Activate in three situations, each mapping to one of the three modes:

| Mode | Precondition | Upstream artifacts consumed |
|------|--------------|-----------------------------|
| **A** Full upstream | `_thought` bound + `# Discovery` locked (4 fields) + an Agreed Solution present — a `<base>_DESIGN.md` in the slug-family **or** (legacy) at least one inline `### Solution Alternative N` in the spine | Verbatim: Diagnosis, Guiding Policy, Desired Outcome, OMTM, Agreed Solution, Architecture/UX decisions, B2 slices — read from the `_DESIGN.md` when present, else from the spine's inline block |
| **B** Chain present, Phase 2 absent | `_thought` bound + `# Discovery` locked + NO Agreed Solution (neither a `_DESIGN.md` nor an inline `### Solution Alternative`) | Invoke `/solution-design` whole BEFORE plan mode opens → writes a fresh `<base>_DESIGN.md` + spine pointer via B3 → resume Mode A flow |
| **C** Ninja-Plan | No `_thought` bound (user explicitly opts out of tracking) | None — plan is single-slice, lands at `<project_root>/Thoughts/<slug>-<ts>_PLAN.md` carrying a `bookkeeping: mode-c` frontmatter line, no spine writeback |

Mode is chosen **before** `EnterPlanMode` fires. The `check-thought-bound.sh` hook reads the `plan-mode-init` marker; absence blocks plan-mode entry with the Track-properly / Ninja-Plan surface.

**Fast-track entry (user-facing).** Mode C is the canonical **fast-track** into Planning — when the topic is already well-framed (Diagnosis + Desired Outcome are clear; there is no real Solution-Design choice) the user invokes `/plan --mode C` (or picks "Ninja-Plan" at the Track-properly gate) and the orchestrator drops straight into single-slice plan authoring without consuming any spine. Full plan-mode gates still cover the work — Gate 0 (a–f), Gate 1, Gate 2, Gate 3, and the per-transition + final coherency dispatches. The sibling **ultra-fast-track** is `/ninja-fix`, which enters at Implementation rather than Planning; see `Workflow.md` "Cross-Cutting → Speed-track entry points" for the side-by-side use cases.

Do NOT activate from inside a subagent (recursion is bounded by topology — see Edge Case E11).

---

## Invariants

1. **Orchestrator runs strictly BEFORE `EnterPlanMode`.** Every read of the spine, every `/solution-design` invocation, every per-transition coherency dispatch happens in the orchestrator phase. Inside plan mode the only writable surface is the plan file itself (`check-plan-readonly.sh` enforces this). *(F9 / `${KIT_HOOKS_DIR}/check-plan-readonly.sh`.)*

2. **Plan mode is a consumer, never a producer.** Modes A and B do not re-derive Diagnosis, Desired Outcome, Guiding Policy, or OMTM — these are read verbatim from the spine's `# Discovery` section. The `GATE0:CHAIN_SRC` marker carries the `discovery_src_hash` anchor proving no re-rendering happened. *(F1, F2.)*

3. **Producer-never-verifies AT EACH structural transition.** Per `code_first_architecture.md:121-124` grading order ("code → model → human" applied per layer), every transition is independently verified by parent-spawned isolated `readonly-checker` checkers — not just the final plan. The four transitions are:
   - **0A:VALIDATED** (Mode C only) — fresh Diagnosis grounded in codebase + concept
   - **0B2:VALIDATED** — Outcome Claims cover the Desired Outcome
   - **0C:VALIDATED** — Gap Analysis maps to Outcome Claims AND exists in the current codebase
   - **0G:COHERENCY** — assembled plan delivers Diagnosis + Desired Outcome
   *(F4, F11.)*

4. **Reuse split locked by topology.**
   - **Bare primitives** (reusable as stateless adapters): `outcome-framing`, `lean-analytics-metrics`, reflect-back, `/double-check`.
   - **Whole skills** (invoked entire, never decomposed inline): `/solution-design` — because B3 owns spine writeback (`~/.claude/skills/solution-design/SKILL.md:45`); decomposing into bare B2 would violate the stateless contract (`~/.claude/skills/solution-slicer/SKILL.md:52,:426`).
   *(F7.)*

5. **Per-spawn explicit model pinning.** Every checker spawned via the Agent tool MUST carry an explicit `model:` parameter — never rely on the subagent default model. Re-fc 2026-05-19 lesson: unpinned Explore subagents silently ran on Haiku for ~1 day, scribe recorded false provenance. Post-spawn enforcement via `check-impl-models.sh` is the runtime backstop (`${KIT_HOOKS_DIR}/_factcheck_engine.py:380-393` precedent). *(F6.)*

6. **Plan-detail level: outcomes + gates, not micro-steps.** Each Coherent Action describes Goal + Guard rails + Validation gate. Do NOT enumerate sub-steps; that violates Anthropic prompt-engineering guidance ("prefer general instructions over prescriptive steps", `~/.claude/rules/prompt-engineering.md:139`) and Cockburn nano-increments. The checker (and the implementer) supplies the steps from the validation gate.

7. **F10 v5 hard-gated post-exit UX.** After `ExitPlanMode` is granted, the next tool the AI invokes MUST be `AskUserQuestion` with the (a) handoff-prompt / (b) `/close` + `/clear` pair. The marker pair `post-plan-uxgate.sh` (PostToolUse on `ExitPlanMode`) + `check-post-plan-pending.sh` (PreToolUse `.*`, in-script `AskUserQuestion` allow-list) enforces this. AI clears the marker via `pre_plan_gates.py clear-post-plan-choice` after the user picks.

---

## Parameters

All optional (defaults shown):

| Flag | Default | Description |
|------|---------|-------------|
| `--mode {A,B,C}` | inferred from spine state | Force a specific mode. Use Mode C explicitly for Ninja-Plan when an `_thought` exists but you want to bypass it. |
| `--thought <path>` | from active topic state | Path to `_thought` file. Required if mode=A/B and no active topic. |
| `--coherency-rounds N` | 2 | Max rounds passed to `factcheck-plan-coherency`. Plan-kind ceiling is 3 (`~/.claude/rules/factcheck-convergence.md` §4). |
| `--step-rounds N` | 1 | Max rounds per `factcheck-plan-step 0A/0B2/0C/0D/0E` dispatch. Same ceiling rules. |
| `--help` | — | Print this table and stop. |

---

## Execution

The orchestrator runs in two phases: **pre-plan-mode** (where all spine I/O and per-transition checks happen) and **inside-plan-mode** (where the plan file is authored and Gate 1/2/3 verification runs). The boundary is `EnterPlanMode`.

### Step 0 — Help

If invoked with `--help`, print Parameters and stop.

### Step 1 — Resolve session + spine state (pre-plan-mode)

1. Read `SESSION_ID` from the most recent `UserPromptSubmit hook success: SESSION_ID=...` line.
2. Resolve the topic spine: use `--thought <path>` if provided; otherwise read active topic state via `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-show`. If `_thought_file_path` resolves, treat as candidate spine.
3. Read the candidate spine. Locate `# Discovery` section:
   - All 4 locked fields present (`## Guiding Policy`, `## Desired Outcome`, `## Desired Solution`, `## Metrics` with OMTM sub-line) → Mode A or B candidate
   - Section absent or any field missing/placeholder → cannot use this spine
4. If a candidate spine resolves with all 4 locked fields, look for an Agreed Solution by **dual-read** (bookkeeping-model OQ18): first check for a `<base>_DESIGN.md` in the slug-family (`<base>` = spine filename minus `_THOUGHT.md`); if absent, scan the spine's `# Solution Design` for inline `### Solution Alternative N` headers (legacy):
   - A `_DESIGN.md` exists **or** ≥1 inline `### Solution Alternative N` present → **Mode A** (consume the latest Alternative by max-N from whichever source holds it — `~/.claude/skills/solution-design/SKILL.md:53` monotonic-N invariant)
   - Neither present → **Mode B** (chain present, Phase 2 absent)
5. If no candidate spine or `# Discovery` incomplete: prompt the user via `AskUserQuestion`:
   - (a) Track properly — pause `/plan`, run `/clarification` to lock `# Discovery`, then return
   - (b) Ninja-Plan — proceed inline as **Mode C** (no spine, single slice; plan lands in `<project_root>/Thoughts/<slug>-<ts>_PLAN.md` with a `bookkeeping: mode-c` frontmatter line)

The `--mode` flag overrides the inference. Honour explicit user opt-out.

### Step 2 — Mode B sub-step (only if Mode B)

Invoke `/solution-design` whole as a Skill call (never invoke bare B1/B2 inline). The skill produces a new `### Solution Alternative N` via B3 and writes it to the topic's `<base>_DESIGN.md` (with a spine pointer under `# Solution Design`). On ESCALATE, surface verbatim and stop — do not proceed to plan mode. On PASS, refresh the spine + Design snapshot from Step 1 and continue as Mode A.

### Step 3 — Initialize plan-mode marker (pre-plan-mode)

Call:
```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py plan-mode-init <SESSION_ID> --mode {A|B|C} [--thought-path <spine path>]
```

For Modes A/B, `--thought-path` is required (provides `discovery_src_hash`). For Mode C, omit `--thought-path`. The CLI writes `~/.claude/state/plan_mode_init/<SESSION_ID>.json` which `check-thought-bound.sh` reads on `EnterPlanMode`.

### Step 4 — Mode C ONLY: fresh-Diagnosis verification (per-transition gate 0A)

In Mode C only, the user authors a fresh Diagnosis (no upstream to consume). After the user states the diagnosis but before drafting Outcome Claims:

1. Draft the candidate Diagnosis in chat (not yet in the plan file — plan mode is not open).
2. **Pre-plan-mode candidate screen (advisory).** Dispatch **1 Sonnet + 1 Opus** `readonly-checker` checkers on the candidate Diagnosis text (isolated context, explicit `model:` pinning) with the rubric: "reason through whether this Diagnosis names a critical aspect grounded in observable behavior of the named codebase paths, then emit as the final line exactly `VERDICT: PASS` or `VERDICT: DISCREPANCY`." This early screen catches a weak Diagnosis before you invest in plan mode; if it looks DISCREPANCY, refine with the user before entering plan mode.
3. **The gate-authoritative 0A receipt is written at Step 6**, once the Diagnosis is authored into the plan file (the engine receipt is keyed to the plan-file path, which the harness assigns only at `EnterPlanMode`). At Step 6, re-run `factcheck-plan-step 0A` with the same dispatch and hand the captured checker outputs to the CLI — that engine receipt is what `check-plan-gates.sh` reads for `GATE0A:VALIDATED` (S2). Carry the candidate outputs forward or re-dispatch; either way the Step-6 CLI call is what authors the verdict.

(Modes A and B skip Step 4 — the Diagnosis is consumed from the spine, already verified upstream by `/clarification` Step 7.)

### Step 5 — Enter plan mode

The orchestrator's pre-plan-mode phase is now complete. Invoke `EnterPlanMode`. The `check-thought-bound.sh` hook reads the marker written at Step 3 and allows entry.

Inside plan mode, author the plan file at the **harness-designated path named in the plan-mode system message** — `~/.claude/plans/<harness-slug>.md`. Author the full Gate 0 (a-f) + Slice-I markers (`CHAIN_SRC`, `SLICES`, per-transition `*:VALIDATED`, `COHERENCY`) into that file.

**Critical (2026-07-06): author ONLY at the harness path during plan mode.** The harness-designated plan file is the ONLY path `check-plan-readonly.sh` permits during plan mode, and the ONLY path that does not trigger the harness's silent plan-mode exit: a *successful* Write to any other file (including a project-side `Thoughts/…_PLAN.md`) during plan mode drops the session to `acceptEdits` with NO `ExitPlanMode` and NO approval — bypassing every plan-gate + the approval prompt (reproduced live via A/B test; see `bookkeeping-model.md` §5). Do NOT write the plan to `Thoughts/` during plan mode.

The project-side bookkeeping home is produced AFTER approval by the relocation CLI (Step 11), NOT during plan mode:
- **Modes A/B:** `<project_root>/Thoughts/<project_slug>_PLAN.md` (or `_<slice-id>_PLAN.md` per slice naming)
- **Mode C:** `<project_root>/Thoughts/<slug>-<ts>_PLAN.md` with a `bookkeeping: mode-c` frontmatter line (the frontmatter is what `bookkeeping_invariant.py`'s G2 case keys on to exempt the singleton plan family).

`check-plan-gates.sh` validates the plan at the harness path (found via the session manifest — `find-session-plan.sh` + `track-plan-file.sh` Arm 1). No project-side file exists until Step 11.

In Modes A/B, the plan opens with a `<!-- GATE0:CHAIN_SRC -->` block carrying `mode`, `thought_file`, `discovery_src_hash`, and `alternative_n` (Mode A only). The Diagnosis / Guiding Policy / Desired Outcome / OMTM sections quote the spine verbatim — no re-derivation.

In **Mode C** the block opens the plan too, but Mode C has no upstream to consume, so every upstream-less field carries the `n/a` sentinel: `mode: C`, `thought_file: n/a`, `discovery_src_hash: n/a`, `alternative_n: n/a`. Do NOT fabricate a 12-hex hash — `check-plan-gates.sh` accepts `discovery_src_hash: n/a` for Mode C, and the fresh Diagnosis is anchored by `GATE0A:VALIDATED` instead (see `plan-gates.md` "Mode-C `discovery_src_hash` sentinel"). The Diagnosis is authored fresh (Step 4), not quoted from a spine.

**Coherent Actions Model column is mandatory in every mode.** Regardless of the `GATE0:CHAIN_SRC`/`GATE0SR:SLICES` opt-in, the Coherent Actions table must use the 7-column schema and declare a Model per action (≥1 real family; em-dash only for mechanical rows). This is enforced unconditionally by `check-plan-gates.sh` on both ExitPlanMode paths, and the declared model is enforced at runtime by `check-impl-models.sh`.

### Step 5b — Gate 0b2 producer: Deep-identify the Outcome Claims (all modes)

`/plan` is the **4th consumer** of the shared claim-identification engine (siblings: `/clarification` Step 2, `/extract-knowledge`, `/double-check` Step 2c). Its site (`/plan Gate 0b2`) resolves to **Deep** (`_claim_harvest.resolve_mode("plan:0b2")` → `MODE_DEEP`), so the Outcome Claims (Gate 0b2) are **produced by the engine**, not hand-written — using the same skill-dispatched read-only-adapter (`readonly-checker`) pattern the two siblings carry, over the **finalized Desired Outcome (Gate 0b)**. This step runs when you reach the `## Outcome Claims` section, in every mode (A/B/C): the Desired Outcome is present in the plan file before 0b2 in all modes (code-enforced ordering `GATE0B < GATE0B2`), so there is one invocation site with no mode branching.

**Deep (primary).**
1. **Identify (engine contract at DEEP, AI-as-adapter).** Dispatch the scoped claim-identification adapter — a parent-spawned `readonly-checker` subagent, isolated context, explicit `model:` pin (the fact-check-engine dispatch pattern, NOT the main AI improvising, NOT a `claude --print` subprocess) — to run the engine's two-stage contract **at DEEP thoroughness** over the Desired-Outcome text: **Stage 1** identify candidate claims + the ambiguity-refusal gate (a candidate advances only if it resolves to a single checkable meaning; ambiguous ones are *refused*, not extracted); **Stage 2** decontextualize/structure each survivor to a standalone atomic claim, scored on the six criteria (atomicity · verifiability · decontextuality · minimality · fluency · faithfulness). The claim set **must carry `thoroughness: "deep"`** — the persist seam refuses a NORMAL set at this Deep site (CF-4/A2; no silent "flip ships NORMAL"). The engine does **not** validate truth (A9).
2. **Consolidate (moderate, producer-never-verifies).** Run `${KIT_HOOKS_DIR}/_claim_consolidate.py` over the DEEP output — `dedup_exact` plus a scoped consolidator dispatch that merges redundant DEEP splits while **preserving completeness** (gated by an independent completeness spot-check — a THIRD actor). Tames DEEP verbosity without dropping a distinct fact.
3. **Emit — one Edit (single list, A2).** In a **single** Edit to the plan file, write the `## Outcome Claims` section carrying **ONE** fenced ` ```json ` claim-set (the consolidated engine output — this IS the single source of truth; there is **no** separate re-worded markdown claims table and **no** `GATE0B2:CLAIM_SET` marker — both retired 2026-07-18). The json uses the `_claim_persist.py` payload schema (`source_type`, `source_path`, six-flag `claims`, `refused`, `thoroughness: "deep"`) **plus two /plan-scoped per-claim fields**: a non-empty **`id`** (e.g. `"id": "C1"` — the token a Gap's `Claim` cell references) and a non-empty **`addresses_diagnosis`** (the aspect of the Diagnosis that claim resolves — **you** author this; the engine does not know the Diagnosis). Both are **code-required** by `check-plan-gates.sh` (via `_plan_claim_gate.py`); a claim missing either blocks ExitPlanMode. Place the json inside the `## Outcome Claims` section (between `GATE0B:OUTCOME` and `GATE0B2:CLAIMS`); the coverage sentence (`**Coverage:** …`) still follows it. Do **not** run a separate in-mode `_claim_persist` validate — `check-plan-gates.sh` schema+DEEP-validates the json at ExitPlanMode (and writes the success run-record for free); a second call would double-run.

**Automatic fallback (retained, reason-logged — never a silent skip).** If the DEEP adapter genuinely can't run (engine error, zero BACKWARD claims, or the ambiguity gate refuses everything) — or you deliberately opt out — hand-decompose the Outcome Claims as a plain markdown table (the only list on this degraded path) AND emit, inside the `## Outcome Claims` section, a non-empty `claim_fallback_reason: <why>` line **instead of** the fenced json. The reason must name a concrete engine-failure category (`engine-error:`/`zero-claims:`/`timeout:`/`plan-mode-seam-blocked:`) or the explicit override `user-acknowledged-skip:`. `check-plan-gates.sh` accepts that branch AND records a site-keyed `fallback_record` receipt to the plan's `.claim-runs.md` (parity with the siblings; the fact-check `BYPASSED` pattern). The Claims↔Gaps bidirectional check still runs on the fallback path (ids read from the markdown table), but the per-claim `addresses_diagnosis` requirement does not apply there (no structured claims).

This step does **not** change the `GATE0B2:CLAIMS` / `GATE0B2:VALIDATED` marker contract or the Step-6 `factcheck-plan-step 0B2` coverage dispatch below — the single json list (schema) and the `GATE0B2:VALIDATED` verdict (coverage) are independent concerns that coexist.

### Step 6 — Per-transition verification dispatches (inside plan mode, between structural steps)

After each structural section is drafted in the plan file, run the appropriate `factcheck-plan-step` dispatch BEFORE drafting downstream sections:

| After section | Dispatch | Required modes | Marker written |
|---------------|----------|----------------|----------------|
| Diagnosis (Mode C only) | `factcheck-plan-step 0A` | C only | `<!-- GATE0A:VALIDATED -->` |
| Outcome Claims (0B2) | `factcheck-plan-step 0B2` | A, B, C | `<!-- GATE0B2:VALIDATED -->` |
| Gap Analysis (0C) | `factcheck-plan-step 0C` | A, B, C | `<!-- GATE0C:VALIDATED -->` |

Each dispatch is a parent-spawned **1 Sonnet + 1 Opus** dispatch (Opus = primary thinker, Sonnet = support — independent cross-check), `subagent_type: readonly-checker`, isolated context, explicit per-spawn `model:` pinning. Early-exit on Round-1 PASS. Plan-kind max rounds = 3.

Each checker receives:
- The plan file path (so it can read the section under verification)
- The `--against` text per gate (0A: codebase + concept; 0B2: Desired Outcome; 0C: Outcome Claims + existing codebase)
- The rubric: "reason through whether the section holds against the `--against` text, then emit as the **final line** exactly `VERDICT: PASS` or `VERDICT: DISCREPANCY` (append a short bulleted discrepancy list if DISCREPANCY). The final `VERDICT:` token is authoritative — reasoning that merely mentions the word 'discrepancy' does not decide the verdict."

**You do NOT decide the verdict — the shared engine does (plan-validation-engine-consumer).** After the checkers return, capture each checker's raw output into a JSON list and hand it to the CLI, which computes the round verdict via the shared engine aggregator (`_factcheck_engine.aggregate_round_verdict`, plan kind) and writes a code-owned `R<N>.md` engine receipt keyed to `(gate, section)`:

```
printf '%s' '[{"model":"sonnet","verdict":"<checker-1 raw output>"},{"model":"opus","verdict":"<checker-2 raw output>"}]' \
  | python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-plan-step <GATE> <PLAN_FILE> --round <N> --max-rounds 3
```

(or pass `--checkers-json <FILE>` instead of stdin). Read the returned `verdict`: **DIRTY** → retry (round N+1, diff-only after round 2); **ESCALATE** → surface the discrepancies verbatim and stop; **PASS** → write the verdict line into the `*:VALIDATED` marker block via Edit. That plan-text verdict line is **informational only** — `check-plan-gates.sh` reads the engine receipt, not the typed line (S2), so a hand-typed verdict cannot unlock approval.

**Same mechanism, on demand (0D/0E and any section).** The dispatch→CLI→receipt spine above is not limited to the three per-transition gates. `factcheck-plan-step` now also accepts **`0D`** (Guiding Policy) and **`0E`** (Coherent Actions) — the former blind-spot sections (S3) — so any kernel section can be validated in isolation with the same 1-Sonnet+1-Opus dispatch, the same `VERDICT:` rubric against that section's `--against`, and the same engine-computed receipt. This is what delivers "validate one dedicated section" from the Desired Outcome.

### Step 6b — On-demand section & whole-plan validation (S3/S4)

The per-transition dispatches (Step 6) and the final coherency dispatch (Step 7) are the two standing uses of one mechanism. That same mechanism is also available on demand — a per-transition check *is* a per-section validation with a fixed `--against`, and the final coherency check *is* whole-plan validation — not a second pathway.

- **Validate one section.** Run `factcheck-plan-step <GATE>` (any of `0A/0B2/0C/0D/0E`) or `factcheck-plan-coherency` (`0G`) with the dispatch→CLI→receipt spine. The CLI returns the engine-computed verdict scoped to that section and writes its receipt.
- **Validate the whole plan (strict rollup — never an omnibus judge).** Validate each kernel section with its own per-section dispatch, then **roll up the per-section engine receipts**: the whole plan PASSes only when every section's latest receipt is a converged PASS (or a recorded override); any non-PASS names the offending section. Do **not** dispatch one checker over the entire plan body — per-section prompts stay under the codebase's measured ~355 KB checker-prompt cap, and Anthropic evals guidance favors decomposed dimension-by-dimension checks over a single compound judge. (The gate at ExitPlanMode *is* this rollup: `check-plan-gates.sh` reads each section's receipt independently.)
- **Codebase anchors for code-touching sections (C6).** When a section makes claims about the codebase (Diagnosis, Gap Analysis, Coherent Actions that cite files), ground its checkers against that section's **enumerated codebase anchors** — the `Source Location` cells of the Gate-1 table for the claims that section relies on — passed in the checker's `--against`. A code-touching section's PASS requires a non-empty anchor set so "reads coherent but doesn't match real code" is caught against the actual files, not just the plan's internal prose.

**Engine-consumer — define-once-propagate (S5, C7).** Plan validation is now a *consumer* of the one shared validation engine, not a fork. The verdict is computed by the engine's own aggregator (`_factcheck_engine.aggregate_round_verdict`) and recorded by the engine's own marker writer (`_write_round_file`, schema `factcheck-convergence.md` §7); when a section needs claim-level rigor, identify its claims through the **same in-session claim-identification port** that Gate 0b2 uses at Step 5b (`_dc_claim_seam.build_scope_addendum` / `_claim_engine.in_session_bootstrap`, DEEP) rather than a plan-specific copy. Because convergence, axes, and the marker live in the one engine, improving the engine once (its convergence rule, an axis, how reviewers are spread) improves plan validation automatically — there is no plan-side tally to keep in sync. Do **not** reintroduce a prose convergence count in the orchestrator: capture checker outputs, hand them to the CLI, read the engine's verdict.

### Step 7 — Final coherency dispatch (Gate 0G)

After the plan is fully assembled (Coherent Actions + Design Review + Gate 1/2/3), dispatch the final coherency check via `factcheck-plan-coherency`:

1. Spawn **3 Sonnet + 1 Opus** checkers in parallel via Agent tool, `subagent_type: readonly-checker`, isolated context, explicit per-spawn `model:` pinning. Default rounds = 2 (`--coherency-rounds`; plan-kind ceiling 3 per `factcheck-convergence.md` §4). Each checker receives the plan file + `--against` = "the Coherent Actions solve the Diagnosis and deliver the Desired Outcome while respecting the Guiding Policy".

2. **Per-axis structured output (mandatory for all checkers).** The checker prompt requires each checker to return three field-shaped axis verdicts plus a per-axis citation — NOT a compound PASS/DISCREPANCY string. The three axes:

   ```
   axis_1 (Coherent Actions solve the Diagnosis):       PASS | DISCREPANCY    citation: <plan-file location + 1-line quote>
   axis_2 (Coherent Actions deliver the Desired Outcome): PASS | DISCREPANCY    citation: <plan-file location + 1-line quote>
   axis_3 (Coherent Actions respect the Guiding Policy):  PASS | DISCREPANCY    citation: <plan-file location + 1-line quote>
   ```

   The Opus checker (and only Opus) additionally returns a **cross-axis coherence assessment** — the system-level judgment that catches inter-axis tensions per-axis decomposition cannot:

   ```
   cross_axis_coherence: COHERENT | TENSION | BROKEN
   cross_axis_notes: [1-3 sentences naming any inter-axis tension or break]
   ```

   Definitions:
   - **COHERENT** — the three axes hold together as one chain; the Coherent Actions deliver the Outcome via the Policy in a way that flows causally from the Diagnosis.
   - **TENSION** — axes pass individually but inter-axis friction exists (e.g., an action that delivers the Outcome but does so via a path the Guiding Policy only loosely supports). Surface for ESCALATE.
   - **BROKEN** — axes pass individually but the chain doesn't actually flow (e.g., the Actions solve the Diagnosis and deliver the Outcome, but the two are causally disconnected — the chain is two parallel checks pretending to be one). Surface for ESCALATE.

   Rationale: Anthropic prompt-engineering guidance favors structured output over compound predicates; this schema turns "averaging" into a code-checkable AND-gate. Opus carries the cross-axis role because the existing `/double-check` Opus scope-coverage pattern (`~/.claude/skills/double-check/SKILL.md:142-162`) already assigns Opus the system-level judgment that Sonnets aren't tasked with. The 3 Sonnets give per-axis breadth (3 independent perspectives per axis); the 1 Opus adds the system-level catch.

   **Fold into a final `VERDICT:` token (mandatory).** After the per-axis (and, for Opus, cross-axis) block, each checker emits as its **final line** exactly `VERDICT: PASS` or `VERDICT: DISCREPANCY`. The rule the checker applies: a **Sonnet** emits `VERDICT: DISCREPANCY` iff any of its three axes is DISCREPANCY; the **Opus** emits `VERDICT: DISCREPANCY` iff any of its three axes is DISCREPANCY **OR** `cross_axis_coherence ∈ {TENSION, BROKEN}`. This is what lets the shared engine reproduce the 0G AND-gate from one token per checker (the plan-kind `_verdict_bucket` reads the final `VERDICT:` line — CoT-safe).

3. **Gate-PASS logic (code-computed by the shared engine, C4).** You do **not** decide the 0G verdict. The AND-gate — all 12 per-axis verdicts PASS **and** Opus `cross_axis_coherence` = COHERENT — is enforced structurally: every failing axis or a cross-axis TENSION/BROKEN makes that checker's `VERDICT:` token DISCREPANCY, and the engine aggregates ANY DISCREPANCY → DIRTY (round < 3) / ESCALATE (final round). 0 discrepancies → PASS.

4. Early-exit on Round-1 PASS.
5. **Persist by handing the captured checker outputs to the CLI (engine computes the verdict).** Capture each checker's raw output into a JSON list and pipe it in; the CLI computes the round verdict via the shared engine aggregator and writes the code-owned `0G` engine receipt (plus an audit row to `_verifications.jsonl`):
   ```
   printf '%s' '[{"model":"sonnet","verdict":"<c1 raw>"},{"model":"sonnet","verdict":"<c2 raw>"},{"model":"sonnet","verdict":"<c3 raw>"},{"model":"opus","verdict":"<c4 raw>"}]' \
     | python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py factcheck-plan-coherency \
         <SESSION_ID> <PLAN_FILE_PATH> --round <N> --max-rounds 3 --claims-checked <N>
   ```
   (or `--checkers-json <FILE>`). Read the returned `verdict`.
6. Write the verdict block into the plan file inside `<!-- GATE0G:COHERENCY -->` via Edit (verdict + rounds + checker_models + cross_axis_coherence). This block is **informational** — `check-plan-gates.sh` reads the engine receipt (S2), and a non-PASS receipt no longer unlocks approval (fixes the old E4/0G threshold hole where DIRTY/ESCALATE were silently admitted).

**On ESCALATE — two-option user choice (do NOT default-stop).** Surface the discrepancies verbatim (including the cross-axis disposition if Opus flagged TENSION/BROKEN) and present the user:
- **(a) One more round** — re-dispatch the same 3 Sonnet + 1 Opus checkers; round counter increments past `--coherency-rounds` (ceiling 3); fresh isolated checkers. After the new round, re-evaluate.
- **(b) Proceed as is** — record an **explicit operator override** so the gate admits the section (a non-PASS receipt alone will NOT unlock approval — S2):
  ```
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py plan-override 0G <PLAN_FILE_PATH> \
      --reason "<the user's stated reason for proceeding with the unresolved discrepancies>" \
      --session <SESSION_ID>
  ```
  Then write the `verdict: ESCALATE` + discrepancy list + cross-axis disposition into `<!-- GATE0G:COHERENCY -->` for the human record. The override is the recorded, explicit escape from the fail-closed gate — never a silently-typed verdict line.

### Step 8 — Spine cross-link (Modes A/B only)

Before requesting `ExitPlanMode`, in Modes A/B only, append a wikilink to the spine's `# Implementation Details` section:

```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py link-plan-to-thought <SESSION_ID> <PLAN_FILE_PATH>
```

The CLI is idempotent (no-op if already linked) and mode-aware (no-op in Mode C). This is the only spine writeback after Step 2's `/solution-design` invocation — `check-plan-readonly.sh` permits it because the orchestrator runs OUTSIDE plan mode for the writeback step (the CLI is invoked from chat, not from plan mode's Edit/Write).

Note: alternatively, this step may run immediately after Step 3 (before entering plan mode) — both placements satisfy the invariant that `check-plan-readonly.sh` does not block the spine write. Pick whichever fits the conversational flow.

### Step 9 — Request ExitPlanMode

The plan is complete and all required markers carry verdict=PASS. Request `ExitPlanMode`. The existing `permission-plan-gate.sh` + `check-plan-gates.sh` validator runs and enforces the new Slice-I per-mode required-set (see `plan-gates.md` "Per-mode required-set (when CHAIN_SRC is present)").

After `ExitPlanMode` is granted, `post-plan-uxgate.sh` (PostToolUse) sets the `post_plan_choice` marker.

### Step 10 — F10 v5 AskUserQuestion (mandatory next call)

Immediately after `ExitPlanMode` is granted — **before any other tool runs** — invoke `AskUserQuestion`. The `check-post-plan-pending.sh` hook blocks every other tool until this happens.

**Verbatim AskUserQuestion contract:**

- **Question:** "Plan approved. What's next?"
- **Header:** "Next step"
- **multiSelect:** false
- **Options:**
  - **(a)** Label: "Generate handoff prompt (Recommended)"
    Description: "Compose a fresh-Implementation-session prompt via /prompt-for-handoff. Modes A/B pass the bound _thought file (state-aware); Mode C passes the plan file (state-agnostic mode per ~/.claude/skills/prompt-for-handoff/SKILL.md:27). Run /close in this session before starting the new one."
  - **(b)** Label: "Run /close and /clear, pick another topic"
    Description: "Wrap this session (diary + TODO + git commit via /close) then /clear to start fresh on a different topic. The current plan stays linked in the spine and is ready for a future Implementation session."
  - **(c)** Label: "Continue in this session"
    Description: "Exit plan mode and implement here. Suited to small/single-action plans where carrying plan-mode context is fine and a fresh session would be overhead. The plan stays linked in the spine."

### Step 11 — Act on user choice + clear marker

After the user picks:

1. **Relocate the plan to its durable `Thoughts/` home (all choices).** The plan was authored at the harness path `~/.claude/plans/<harness-slug>.md`; now that it is approved, relocate it — this is where the project-side `_PLAN.md` first comes to exist (it is NOT written during plan mode):
   ```
   python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py relocate-plan-after-approval <SESSION_ID> <HARNESS_PLAN_PATH> [--dest-slug <slug>]
   ```
   Modes A/B derive the destination slug from `topic_state.topic_slug` automatically and add the `# Implementation Details` spine back-link. Mode C: pass `--dest-slug` if you already have a good bookkeeping slug; otherwise the CLI auto-derives one from the plan's own problem statement (its H1 title). It only errors if that derivation finds nothing usable — then supply `--dest-slug` (optionally via `AskUserQuestion`). The CLI refuses unless a real ExitPlanMode grant was recorded, so it only runs post-approval, outside plan mode (where `check-plan-readonly.sh` no longer applies).
2. **Arm the per-run execution pointer (register-presence-gated — all choices).** This is the on-disk state the code-enforced `/execute-plan` entry gate reads later, so it must be armed here (post-relocation) — the gate then holds whether or not a future session remembers to route through `/execute-plan`. The guarantee is code's (S1+S3); this arming is what feeds it. First test whether the plan is a multi-slice plan worth gating, reading the relocated durable copy:
   ```
   printf '%s' '{"spine_path":"<relocated_plan_file>"}' \
     | python3 ~/.claude/skills/execute-plan/run.py register-presence
   ```
   - **`present: true`** (≥2 non-closing slices — a real walk) → arm the pointer for the approving session:
     ```
     printf '%s' '{"owner_session_id":"<SESSION_ID>","surface_path":"<relocated_plan_file>","total_slices":<plan slice count>,"execution_pending":true,"walking_session_id":null,"current_slice_id":null}' \
       | python3 ~/.claude/skills/execute-plan/run.py set-active-run
     ```
     Add `"worktree_root":"<worktree root>"` to the payload when the plan lives in a git worktree (the run_id keys on surface_path + worktree_root). `execution_pending:true` is what the entry gate keys on; `walking_session_id` and `current_slice_id` stay explicitly `null` — no session is walking yet and no slice is checked out (a walker is elected only when `/execute-plan` actually begins, never here).

     **Worktree auto-placement + land-readiness (S7 A14/A13, `/plan --worktree`-opt-in).** When `/plan --worktree` is used on a harness topic, place the session into the topic's worktree (the `work-start/SKILL.md:255-259` two-step `place` → guarded `cd` form, harness binding the configured source path) so the plan is authored/executed inside its isolated workspace, and set `worktree_root` above from the resulting `place` path. Record the topic's advisory land-readiness for a later resume — `python3 ${KIT_HOOKS_DIR}/land_readiness.py write <topic-slug> in-progress` (a plan just authored is `in-progress`; it becomes `ready-to-verify-then-land` only once the slices are implemented + green, recorded by `/close`/`/prompt-for-handoff`, S7 A13). The record is an advisory routing hint only — the A6 gate re-computes green/red at land time. **Mode C caveat:** a Ninja-Plan has no up-front topic slug (the durable slug is minted at Step-11 relocation), so `--worktree` placement in Mode C uses the relocation slug and places at/after relocation; without `--worktree`, no placement (default unchanged). Projects-side binding activates with S9.
   - **`present: false`** → **arm NOTHING** (non-regression C6). But first check the register-presence output for a *malformed multi-step* register — a plan that LOOKS multi-step yet did not resolve, which must be surfaced LOUDLY rather than silently downgraded to a one-shot (A2, C2):
     - **Fail-loud warning (A2)** — if the register-presence output shows `raw_non_closing_count` ≥ 2 (there are ≥2 non-closing rows under a recognized register, but the tolerant reader could not turn ≥2 of them into valid slices) **OR** `pointer_broken: true` (a `slice_register_ref` that did not resolve), emit a prominent operator warning naming the plan file and pointing at `/execute-plan`, e.g.:

       > ⚠️ This plan **looks multi-step** (`raw_non_closing_count` non-closing rows detected) but its slice register did **not** resolve to ≥2 valid slices — so step-by-step execution was **not** armed. The register is likely malformed (missing a `Type`/`Depends on` column, or a broken `slice_register_ref`). Fix the register in `<relocated_plan_file>` and re-run, or drive execution manually via `/execute-plan`. It is **not** being silently treated as a one-shot.

       This is a **warning, not a block** — after surfacing it, still arm nothing and continue (the operator decides; block-with-override, git-policy §8). The `warning` field in the register-presence output (already printed to stderr for the broken-pointer case) carries the loud text for the pointer case; compose the malformed-register text from `raw_non_closing_count`.
     - **Genuine single-work / trivial plan** — `raw_non_closing_count` < 2 (0 or 1 non-closing row, e.g. a canonical `1 work slice + S-final`) and no broken pointer → arm nothing **silently** (C6: no spurious warning, no gating). Skip to clearing the marker.
3. Clear the marker:
   ```
   python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py clear-post-plan-choice <SESSION_ID>
   ```
4. If (a): invoke `/prompt-for-handoff <thought_file>` (Modes A/B) or `/prompt-for-handoff <relocated_plan_file>` (Mode C — state-agnostic mode).
5. If (b): remind the user to run `/close` then `/clear`. Do not auto-invoke `/close` or `/clear` — they are user-issued commands.
6. If (c): no further action — proceed with implementation in this session. The marker auto-clears when `AskUserQuestion` completes (`clear-post-plan-marker.sh`).

---

## Output Contract

The skill produces no JSON output. Its observable effects are:

- During plan mode: the plan authored at the harness path `~/.claude/plans/<harness-slug>.md`, carrying the Slice-I markers and `verdict: PASS` on every required gate.
- After approval (Step 11 relocation): a `<project_root>/Thoughts/<project_slug>_PLAN.md` (Modes A/B) or `<project_root>/Thoughts/<slug>-<ts>_PLAN.md` with `bookkeeping: mode-c` frontmatter (Mode C) durable copy.
- A wikilink under `# Implementation Details` of the spine (Modes A/B), added by the relocation CLI.
- An `ExitPlanMode` grant.
- An `AskUserQuestion` surface for the post-plan UX gate.
- An append to `~/.claude/state/plan_validation/_verifications.jsonl` per coherency round.

---

## Edge Cases

- **E1 — Marker orphan on session crash.** `~/.claude/state/post_plan_choice/<SID>.marker` may survive a crash. `/session-start` should sweep stale markers (cross-slice carry to Slice J).
- **E2 — Two plans per session.** Plan mode in one session writes one plan file (`check-plan-readonly.sh` enforces the allowed paths). The `plan-mode-init` marker is one-per-session.
- **E3 — Mode C authoring + relocation.** Mode C plans are AUTHORED at the harness path `~/.claude/plans/<harness-slug>.md` during plan mode (DS5 reversed 2026-07-06 — authoring at `Thoughts/` during plan mode triggers the silent plan-mode exit; see `bookkeeping-model.md` §5), then relocated after approval to `<project_root>/Thoughts/<slug>-<ts>_PLAN.md` with `bookkeeping: mode-c` frontmatter by `relocate-plan-after-approval` (Step 11; `--dest-slug` required for Mode C; auto-creates `Thoughts/` if missing). `link-plan-to-thought` is a no-op for Mode C (no spine).
- **E4 — Gate 0G ESCALATE (receipt-based, plan-validation-engine-consumer).** Surface discrepancies verbatim. `check-plan-gates.sh` reads the engine **receipt** for each Slice-I gate (0A/0B2/0C/0G), not an author-typed `verdict:` line, and admits ONLY a converged PASS (or a recorded operator override) — a DIRTY/ESCALATE/missing receipt blocks `ExitPlanMode`, naming the section (this fixes the old threshold hole where the 0G grep silently admitted DIRTY/ESCALATE). To proceed with unresolved discrepancies the user must explicitly accept: record it with `pre_plan_gates.py plan-override 0G <plan> --reason "…"` (Step 7 option (b)). The `verdict: ESCALATE` line in the plan text is the human record; the recorded override is what unlocks approval.
- **E5 — Coherent Action missing validation gate.** The 7-column row schema (`check-plan-gates.sh` A2 validators) blocks `ExitPlanMode` if any row has fewer than 7 columns.
- **E6 — Unsupported / missing Model value.** The Model-column allowlist (`sonnet`, `opus`, `haiku`, or `—` for mechanical rows — `check-plan-gates.sh`) blocks on unknown values. The Model column is **mandatory for every plan in every mode** (not opt-in): an empty Model cell is rejected, and at least one action must declare a real family. Enforced on both ExitPlanMode paths (`permission-plan-gate.sh` + `stop-plan-gate.sh` both call `check-plan-gates.sh`).
- **E7 — Hotkey-entry edge.** `check-thought-bound.sh` PreToolUse matcher fires for `EnterPlanMode` regardless of trigger source — hotkey and `/plan` go through the same gate.
- **E8 — Plan-naming collision.** Slice-id appears in the filename (`_I_PLAN.md`); cross-session isolation uses `--worktree` per `plan-gates.md` cross-session paragraph.
- **E9 — PostToolUse on ExitPlanMode unsupported.** Spike-verified 2026-05-22 per Claude Code hooks docs. Fallback if the harness changes: Stop event hook scanning the transcript for the most-recent `ExitPlanMode` permission grant.
- **E10 — Checker uses default model.** `factcheck-plan-coherency` and `factcheck-plan-step` always spawn with explicit `model:` parameter. `check-impl-models.sh` PostToolUse on `Agent` is the runtime backstop for **all plans that declare a model** — the implementation-model contract is unconditional (not gated on the `GATE0SR:SLICES` opt-in).
- **E11 — `/plan` invoked from inside a subagent.** Refuse. The skill is main-session-only — recursive `/solution-design` invocation is bounded by Phase 2's own main-session-only invariant (`solution-design/SKILL.md:14`).
- **E12 — Stale `discovery_src_hash` after Phase 1 edit.** Recomputed every `plan-mode-init` call from the current spine via `_discovery_locked_fields_hash` (`pre_plan_gates.py:178-202`).
- **E13 — Multiple `### Solution Alternative N` blocks.** Read the LATEST (max N) per `solution-design/SKILL.md:53` monotonic-N invariant.
- **E14 — `link-plan-to-thought` race when two sessions write the same spine.** The CLI is idempotent (`if wikilink_line in text: return already_linked`). The plan file basenames are slice-distinct.

---

## See also

- `~/.claude/rules/plan-gates.md` — canonical Gate 0/1/2/3 + Slice-I marker spec
- `~/.claude/rules/code_first_architecture.md` — hexagonal + producer-never-verifies + grading order
- `~/.claude/rules/factcheck-convergence.md` — §4 stopping condition, §7 R<N>.md schema
- `~/.claude/rules/prompt-engineering.md` — §139 prefer general instructions over prescriptive steps
- `${KIT_HOOKS_DIR}/pre_plan_gates.py` — `plan-mode-init`, `link-plan-to-thought`, `factcheck-plan-step`, `factcheck-plan-coherency`, `clear-post-plan-choice`, `relocate-plan-after-approval`
- `~/.claude/skills/execute-plan/run.py` — `register-presence` (route-gating predicate) + `set-active-run` (Step 11 arms the per-run execution pointer the `/execute-plan` entry gate reads)
- `${KIT_HOOKS_DIR}/check-thought-bound.sh` — PreToolUse `EnterPlanMode`
- `${KIT_HOOKS_DIR}/check-impl-models.sh` — PostToolUse `Agent`, runtime model enforcement
- `${KIT_HOOKS_DIR}/post-plan-uxgate.sh` — PostToolUse `ExitPlanMode`, F10 v5
- `${KIT_HOOKS_DIR}/check-post-plan-pending.sh` — PreToolUse `.*`, F10 v5
- `~/.claude/skills/solution-design/SKILL.md` — Phase 2 (Mode B's `/solution-design` invocation)
- `~/.claude/skills/prompt-for-handoff/SKILL.md:27` — state-agnostic mode for Mode C

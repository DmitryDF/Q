# Orchestrator Pattern — Doctrine for Heavy Skills

**Status:** active. Authored 2026-07-08 (Slice S1 of `Thoughts/subagent-delegation-context-focus_PLAN.md`, Mode A, `discovery_src_hash: 1db01438e1fb`).
> **Note on that hash (2026-08-16).** `1db01438e1fb` predates the
> discovery-field-match-anchoring fix and no longer reproduces: it was computed by
> the unanchored extractor, whose `## Metrics` span had bound to a prose mention
> rather than to the locked heading. Recomputing the same spine now yields
> `09c21d6f4ab0`. The value above is left as written — it is a provenance record of
> what was computed at authoring time, not a checksum to re-verify against.
**Grounding trio:** `~/.claude/rules/code_first_architecture.md` (hexagonal orchestrator/adapter + producer-never-verifies), `~/.claude/rules/bookkeeping-model.md` (artifact allocation), Cockburn — *Simplifying Software Design* (`<KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md` — Fatness Tradeoff + Responsibility Alignment Test).
**Conformance sibling:** `~/.claude/rules/skill-authoring.md` (the single Anthropic-skill-authoring conformance reference every revamped skill is checked against). This file is the *pattern*; that file is the *authoring standard*. They are read together when revamping a skill.

---

## 0. Why this exists (the diagnosis)

A heavy skill produces its result **inside the main conversation**. That single choice — *where* the work runs, not *what* it does — is the root of three harms:

1. **Context bloat.** All the intermediate detail behind a result stays in the main window even though only the final outcome matters going forward. Over a long session this piles up until it crosses into the credits-gated 1M-context tier.
2. **Over-powered model.** Inline work inherits the session model (often Opus), so routine production burns a top-tier model when Sonnet/Haiku would do.
3. **Look-alike bypass.** When delegation does happen, the AI sometimes replaces the canonical skill with a hand-rolled `Agent` dispatch it claims is "equivalent," silently losing the skill's gates, methodology, and fact-checking.

**The fix is one shared discipline** that separates context that must stay to continue the session from context needed only to produce one outcome, and pushes the latter into an isolated, right-sized subagent that returns just the outcome **through the canonical skill**. Fix the *where* and all three harms resolve at their single shared cause.

---

## 1. The four-part orchestrator

A heavy skill is not a producer. It is an **orchestrator** (a conductor) that does exactly four things and delegates everything else:

| Part | Responsibility | Grounding |
|------|----------------|-----------|
| **(a) Interaction** | Holds the live user interaction — the gates, the decisions, the turn-by-turn dialog. Stays inline in the main session. | `code_first_architecture.md` — "Code owns the flow." The orchestrator IS the application layer. |
| **(b) Persistence** | Owns writing each step's state + outputs to **accessible artifacts**, allocated/named/linked/retired per `bookkeeping-model.md`. The main window keeps only a **pointer** (a wikilink / slug-grep), never the produced content. | `bookkeeping-model.md` §4–§8 (slug-family, bidirectional links). |
| **(c) Delegation** | Dispatches every **production act** through one port to an isolated, right-sized subagent that returns **only its outcome**. | `code_first_architecture.md` — "AI is an adapter … receives structured input and returns structured output." |
| **(d) Coherency guard** | Checks that the user's inputs stay consistent across the session — a delegated pairwise check against prior *accepted* fields, blind to full session history (CoVe independence). | Locked Discovery Q2 / A7 / A13 / A14. See `skill-authoring.md` and the coherency-guard module for mechanics. |

**The orchestrator never produces.** If it is drafting prose, running research, extracting knowledge, or composing a commit — that is a production act and it belongs behind the port (Part c), not in the orchestrator body.

> Cockburn Responsibility Alignment Test — check the triple *name / responsibility / signatures*. If a "skill" named as an orchestrator still carries production logic in its own body, the three are out of alignment and the design has drifted (Part 1, §1.2).

---

## 2. The three-way classification — inline / code / delegate

Every step a skill performs sorts into exactly one of three bins. This is the **determinate** rule that removes the "delegate everything vs keep everything" tension:

| Bin | What goes here | Runs where | Test |
|-----|----------------|-----------|------|
| **INLINE** | Session-durable interaction: the gates, the live decisions, the pointers. Needed to *continue the session*. | Main session (the orchestrator body). | "Is this needed to continue the conversation after the outcome exists?" → yes = inline. |
| **CODE** | Deterministic mechanics: schema checks, date math, file moves, roll-ups, git plumbing. No judgment, no AI. | The domain layer — a script/hook, no model call. | "Is the answer deterministic?" → yes = code (no AI). (`code_first_architecture.md` Domain Layer — "Not my job to involve AI.") |
| **DELEGATE** | Job-scoped model production: drafting, assessing, extracting, synthesizing, judging. Needed only to *produce one outcome*. | An isolated subagent behind the port. | "Does this need judgment AND is its intermediate detail disposable once the outcome exists?" → yes = delegate. |

**No "both" bin.** Rule (b) keeps everything the session needs in accessible artifacts; rule (c) delegates the production *act*. The orchestrator keeps only a pointer. There is never a category that is simultaneously inline-and-delegated (locked Q1).

**Deterministic-first.** Before delegating, ask whether the step is deterministic. If it is, it belongs in CODE, not in an AI adapter — do not spend a model call (or a subagent) on arithmetic, a file move, or a schema check.

---

## 3. Orchestrator ↔ adapter tiering + the single production port

There is **exactly one production port**. The orchestrator dispatches every production act through the **`Agent` tool** (or a **named agent** in `~/.claude/agents/`), passing a scoped input and receiving back only a structured outcome.

- **Two tiers by choice, not constraint:** *orchestrators* hold interaction and run in the main session; *adapters/leaves* are pure production and run isolated.
- **Default flat.** The orchestrator pre-decomposes the work and dispatches leaves that have **no** `Agent` tool (they cannot nest).
- **Escape-hatch (single level).** For genuinely hierarchical work, hand a leaf the `Agent` tool so it can nest **one** level, used deliberately. No deeper nesting in v1 (locked A10).

### 3.1 Model is set at dispatch — never in SKILL frontmatter

Right-size each adapter by setting the model **at dispatch**:
- the **`Agent`-tool `model` parameter** at the dispatch site, or
- a **named agent's `model:` field** (`~/.claude/agents/*.md` support a model field).

**SKILL.md files do NOT carry a model field.** The reverted `model:`-frontmatter experiment (commit c2e2476, 2026-06-19; reverted 73e4cf0, 2026-06-21) proved the point: frontmatter switches the model **without isolating context**, so it serves neither driver. Model selection is a property of the *dispatch*, chosen independently of the session model — never of the skill's own frontmatter.

> This is the load-bearing distinction between this pattern and the failed earlier fix. Isolation (Part c) + independent model (this section) are two effects of one mechanism — the isolated subagent dispatch. Frontmatter delivers neither.

---

## 4. Adapter sizing — Cockburn's Fatness Tradeoff

An adapter is sized by **one responsibility at one altitude**, not by token count.

> "start with a fat object, split only as needed, and stop splitting as soon as possible." — Cockburn, Part 0 §0.3

> "Splitting objects can only be defended by appealing to certain predicted futures." — Cockburn, Part 0 §0.3

Practically:
- **Start fat.** A new adapter should do one whole job. Do not pre-split it into micro-adapters.
- **Split only for a named future.** Split an adapter into two only when you can name the specific future that requires it (a second caller, a divergent model tier, an independent verification need). A speculative split is a defect.
- **Stop as soon as possible.** Once the responsibilities are cleanly separated, stop.
- **Cost reinforces this.** Multi-agent delegation runs ~15× the tokens of a single inline pass (locked Q7). That multiplier is the natural brake on over-splitting and speculative nesting — do not delegate what does not need isolation or a different model.

> Responsibility Alignment Test (Cockburn Part 1 §1.2): for each adapter, the *name*, the *responsibility statement*, and the *input/output signatures* must align. If the name says "diary-drafter" but the signature also returns a commit message, the three have drifted — split or rename.

---

## 5. The guardrail — route through the canonical skill, never a look-alike

When work belongs to a skill (research → `/research`), it MUST be invoked **as that skill**, not as a hand-rolled `Agent` dispatch (e.g. `subagent_type: research`) the AI claims is "equivalent." The skill is the contract; its gates, methodology, fact-checking, and output conventions only hold when the skill itself runs. **The AI must not substitute its own "this is the same thing" reasoning to bypass the skill.**

Enforcement splits by how detectable the violation is:

| Violation | Layer | Mechanism |
|-----------|-------|-----------|
| **Named shadowing** — an `Agent` dispatch that shadows a canonical skill (same job, hand-rolled). | **Code** | Marker handshake: the canonical skill writes a session marker on start; a PreToolUse `Agent` hook (`check-skill-marker.sh`, S4) blocks a skill-shadowing dispatch when the marker is absent. Missing/unreadable marker = absent (**fail-closed**). Cloned from the proven `research-scope-gate.sh` pattern. |
| **Creative substitution** — a *different real skill* stood in for the right one (e.g. a deep-research dispatch instead of `/research`). | **Rules + discipline** | No marker distinguishes this; the rule here is the guard. When work maps to a skill, invoke that skill. |
| **Hand-rolled inlining** — the orchestrator does the production itself instead of delegating. | **Rules + discipline** | Part 1: the orchestrator never produces. If you are drafting/assessing/synthesizing in the orchestrator body, you have inlined — stop and delegate. |

**Let the skill decide inline-vs-delegate.** The resolution of "delegate everything" vs "always use the skill" is: **use the skill, and let the skill (as an orchestrator) decide whether it runs inline or delegates.** Some skills are inline by design (`/clarification` — gated, turn-by-turn); others delegate (`/research`). The caller's job is to route through the real skill, not to second-guess its internal shape.

---

## 6. Model rubric + `/double-check` quality gate + one-tier-up fallback

### 6.1 Model rubric (right-size by responsibility)

| Work | Model |
|------|-------|
| Mechanical (clone a known pattern, wire data, format) | **Haiku** |
| Normal production (draft, extract, assess) | **Sonnet** |
| Hard judgment / the orchestrator itself | **Opus** |

### 6.2 Quality gate — orchestrator-run `/double-check` (producer-never-verifies)

The orchestrator runs the quality gate on each delegated outcome. **Adapters never verify themselves** (`code_first_architecture.md` — "Producer never verifies its own output. 'Not my job.'").

- **Where a skill/adapter already determines its own gate** (an existing `/double-check M,N,R` allocation — e.g. `/clarification` `3,1,2`, `/solution-design` `1,1,2`) — **use that**.
- **Where none is determined** — default to `/double-check` with **checker tier ≥ producer tier**. Independence is mandatory; escalate the checker tier by stakes; at the Opus ceiling, verify with **Opus + Sonnet** (the canonical `/double-check` shape).

### 6.3 One-tier-up fallback (capped once)

On a gate **failure**, the orchestrator **re-produces one tier up** (Haiku→Sonnet→Opus), **capped at one escalation**, then re-gates. If it still fails after the single escalation, surface to the user — do not loop. (Operator-designed — Anthropic documents neither tier-matched verification nor auto-escalation.)

---

## 7. Persistence allocation (per `bookkeeping-model.md`)

The orchestrator owns persistence; **adapters never write to durable surfaces**.

| Output kind | Home | Pointer the orchestrator keeps inline |
|-------------|------|----------------------------------------|
| **Durable outcome** (a plan, a research file, a design) | Thoughts **slug-family** (`<slug>-<ts>_<TYPE>.md`) per `bookkeeping-model.md` §4–§5. | A wikilink / slug-grep reference. |
| **Working ephemera** (an intermediate draft, a scratchpad) | **Advisory bucket** (`<slug>-<ts>_<TYPE>_<sid>.md`) per `bookkeeping-model.md` §4 Bucket 3. | Optional wikilink under `## Audit Trail`. |

The main window holds **interaction + pointers only** — never the produced content. This is what keeps the conversation lean: production no longer *enters* the main window; only outcomes + pointers do.

---

## 8. Where the leanness comes from (honest scoping)

- **Primary lever (built into the pattern):** production work no longer *enters* the main window, so the conversation stops accumulating work-in-progress. This is non-accumulation, not eviction.
- **Secondary lever (platform features, not new code):** the orchestrator **applies or prompts** the platform's context-reduction features (`/compact`, `/rewind`, server-side compaction, context-editing) when the live window warrants. A lever, not net-new machinery.
- **Known residual (honest):** the *interaction skeleton* (gates, decisions, pointers) still grows with session length — much slower than today, but not flat. On a very long session the platform reduction features carry the rest.
- **Out of scope (v1):** building *new* mid-session context-eviction machinery beyond the platform's own features; multi-vendor models; cross-topic coherency; deep multi-level nesting.

---

## 9. Metric — is the pattern working?

Ship the pattern **with its meter** (reuse-first):

- **OMTM:** main-chat token delta per heavy-skill run (lower = leaner). The one new raw capture.
- **Secondary:** model-weighted token usage per run (guards the ~15× over-delegation trap).
- **Signals:** session altimeter (accumulated per-run deltas), ceiling alarm (1M-tier incident count), blocked-look-alike count (guardrail-hook byproduct).

Reuse `/double-check`'s CostBlock + the `research_token_parser` pattern + per-subagent usage; roll into `~/.claude/state/metrics/skill-runs.jsonl` + `/close` Stats. (Substrate: Slice S5.)

---

## 10. Retrofit checklist (per skill)

A skill is "revamped" when:

1. Its heavy production steps are **delegated behind the port** (Part 1c) and **route through the canonical skill** (Part 5).
2. Deterministic mechanics are in **CODE**, not delegated or inlined (Part 2).
3. Each adapter is **one responsibility at one altitude** (Part 4), with its model set **at dispatch** (Part 3.1).
4. Each delegated outcome passes an **orchestrator-run `/double-check`** gate with the one-tier-up fallback (Part 6).
5. Outputs are **persisted per `bookkeeping-model.md`**; the main window keeps only pointers (Part 7).
6. The skill **conforms to `skill-authoring.md`** (frontmatter, triggering description, progressive disclosure, `allowed-tools`, no `model` field).
7. The named-shadowing **guardrail marker** is written on skill start (Part 5).

**Minimum acceptable end-state:** heavy production off the main thread AND the guardrail holds. Full four-property conformance is the target; a skill counts as revamped once those two hold.

Prove on **`/close` first** (S7), **`daily-discovery` second** (S8), then the post-v1 seed pool.

---

## Consumed by

- `~/.claude/rules/skill-authoring.md` — the authoring-standard sibling (read together when revamping a skill).
- Every revamped heavy skill — `/close` (S7, first proof), `daily-discovery` (S8, second proof), then the seed pool.
- `check-skill-marker.sh` (S4) — the named-shadowing guardrail this doctrine mandates (Part 5).
- The metrics substrate (S5) — `skill-runs.jsonl` + `/close` Stats (Part 9).

*Provenance: Slice S1 of `Thoughts/subagent-delegation-context-focus_PLAN.md`. Grounded in the locked Discovery (Guiding Policy / Desired Solution A1–A17) + the grounding trio. Validation gate: `/double-check` (checker tier ≥ producer; Opus ceiling → Opus + Sonnet) against the locked Guiding Policy + trio.*

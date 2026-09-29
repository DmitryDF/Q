# /clarification — Step Contracts

Bundled reference for the `/clarification` skill (one level deep — see [`SKILL.md`](SKILL.md) for frontmatter, invariants, orchestrator-pattern conformance, and Setup). This file holds the ten gated step contracts, resume-mode behavior, and the cascade map. Every gate token, `clar-advance` call, and output schema below is authoritative.

---

## Step 1 — Idea, confirmed (Moment 1)

**Purpose.** The person states the idea in their own words; the system captures it verbatim with no editing, then shows back a short numbered statement of **what it understood** — an interpretation in the system's own words, not the person's words played back and not a grammar-corrected transcript. The person corrects it or accepts it. What they accept becomes the idea the rest of the session works from; the words they typed are kept beside it, never discarded. Also writes the 4-section `_thought` file scaffold on first acceptance.

**Input.** User's raw input to `/clarification`.

**Prompt scaffold (AI → user).** Two turns — capture, then the interpretation.

```
What's the idea?

State it in your own words. I will capture it verbatim. There is no
schema yet — talk about what you want to do and why it matters to you.
```

Then, from that text:

```
/clarification — Step 1: Idea (initial)

Here is what I understood:

1. <point>
2. <point>
3. <point>

That is my reading, not your words repeated back. Tell me what is wrong,
or accept it — what you accept becomes the idea everything else is built
from.
```

Keep this moment to the idea itself: **no assumptions list, no gaps, no pitfalls.** Those belong to Step 2, the full reflection — a checkpoint the person is meant to answer quickly should not bury the thing being confirmed. A one-sentence idea yields a one-item list; that is correct behavior, not a defect.

**Output schema.**

```json
{
  "gate_token": "accepted",
  "idea_verbatim": "<user's text>",
  "idea_confirmed": ["..."]
}
```

`idea_verbatim` is the person's own words, unedited. `idea_confirmed` is the numbered interpretation they accepted — the artefact Step 1b frames the Problem FROM and Step 2 reads. Both are kept; neither replaces the other.

**Gate.** User confirms with `accepted` (the literal token). What is being confirmed is `idea_confirmed` — not merely that they typed something. Then call:

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py clar-advance SESSION_ID 1 '{"gate_token":"accepted","idea_verbatim":"...","idea_confirmed":["..."]}'
```

**Correction loop.** If the person says what is wrong, revise the numbered statement and show it again under the iteration-header convention (see Step 2) — `Step 1: Idea (<what has changed>)`. Loop until they accept. Never advance on an interpretation they have not confirmed.

**Fresh mode scaffold write.** On first acceptance, write the 4-section `_thought` file scaffold (creates `Thoughts/<slug>_THOUGHT.md` if absent). The person's raw words come **first**, with the confirmed interpretation beneath them under its own H2 heading — `## Idea as confirmed`, which is also the heading `/clarification-v2` writes, so the two doors' saved files share a shape rather than merely sharing content:

```markdown
# Idea

<verbatim user input>

## Idea as confirmed

1. <accepted point>
2. <accepted point>

## Problem
*(populated by Step 1b — Problem framing)*

# Discovery

*(populated by Steps 3 / 4 / 5 / 6 / 7 / 8 / 9)*

# Solution Design

*(populated by /solution-design — Phase 2)*

# Implementation Details

*(populated by /plan — Phase 3, Obsidian wikilinks)*
```

`## Idea as confirmed` sits **above** `## Problem` deliberately, and the order is not cosmetic: the harness validator bounds `## Problem`'s body by its own sibling list (`IDEA_SECTIONS = ["## Problem"]`, read at `${KIT_HOOKS_DIR}/_validate-thought-file.py:118-122`), so a new heading placed *after* `## Problem` would be swallowed into the Problem body. Do **not** add this heading to `IDEA_SECTIONS`: `tests/test_clarification_schema.py:74-75` pins that list to exactly `["## Problem"]`, and no harness code needs to know about this subsection.

Also call `phase_start("thought", todo_text=<first-step-output>)` per Slice C-ii to register the `[Thought]` TODO entry.

**Resume mode.** If `clar-status` shows step 1 already complete, present **both** things it holds — the stored `idea_verbatim` and the accepted `idea_confirmed` — and ask: "This is what step 1 holds: the words you typed, and the reading you accepted. Accept (no change) or provide feedback?" Accept → no write. Feedback → re-run the correction loop on the interpretation; if the person wants to restate the idea itself, capture the new words verbatim and re-interpret from them (the stored verbatim text is replaced only by the person's own restatement, never edited by the system). Then call `clar-rewind --to 0` followed by `clar-advance 1 ...`.

**Cascade on update.** Per `CLARIFICATION_CASCADE_MAP[1] = [2, "1b", 3, 4, 5, 7, 9]`. Resume-mode revision of step 1 marks steps 2/1b/3/4/5/7/9 stale; user re-confirms each (default) or `--rerun` re-runs them.

---

## Step 1b — Problem, framed from the confirmed idea (Moment 2)

**Purpose.** Gate on the Problem statement before any subsequent step proceeds. The person says what the problem is; the door shows **two** things — what was heard, and a proposed statement of the real problem to be solved, framed from the idea they confirmed at Step 1. The person must accept (or revise) that one-paragraph Problem before anything else is confirmed. This is still the person's reviewed Problem — not an AI construct that slips past unreviewed.

**Input.** `clarification_payloads["1"].idea_confirmed` (the accepted interpretation — this is what the framing is built FROM) plus the person's own statement of the problem in this turn. `idea_verbatim` remains available for reference, but the framing is built from the confirmed idea, not from the raw words: a misread of the idea must not pass silently into the framed Problem.

**Prompt scaffold (AI → user).** Ask first, then show both parts.

```
What is the problem here — what is going wrong, or not happening, today?
```

Then, from their answer plus the confirmed idea:

```
/clarification — Step 1b: Problem (initial)

Here is what I hear:

1. <point>
2. <point>

And here is the problem I think you are actually solving:

<one paragraph, plain business words>

Accept this as your Problem, or tell me what's wrong and I'll revise.
```

The framed paragraph leads with plain business words — see `~/.claude/rules/plan-gates.md` Gate 0a OQ19.

If the person explicitly stated a Problem, the what-I-hear list carries it back in their terms; the framed paragraph is still the system's statement of the problem to be solved, drawn from the confirmed idea, and it is what they accept. As at Step 1, this moment carries **no** assumptions list, gaps, or pitfalls — they belong to Step 2. Correction loop under the iteration-header convention (see Step 2) until the person accepts.

**Output schema.**

```json
{
  "gate_token": "problem-accepted",
  "problem": "<user-accepted problem statement>",
  "heard_numbered": ["..."]
}
```

`problem` stays the accepted **one-paragraph** statement — Step 2 and Step 7 read it. `heard_numbered` records the what-was-heard list shown alongside it, so the moment is auditable after the fact.

**Gate.** User confirms with `problem-accepted`. Then call:

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py clar-advance SESSION_ID 1b '{"gate_token":"problem-accepted","problem":"...","heard_numbered":["..."]}'
```

On acceptance, write the body of `## Problem` under `# Idea` in the `_thought` file (replace the Step-1 placeholder line). `## Idea as confirmed` is left exactly as Step 1 wrote it — the two subsections sit side by side, confirmed idea first.

**Resume mode.** Present existing `## Problem` content as-is, alongside the confirmed idea it was framed from. Same accept-or-revise pattern.

**Cascade on update.** Per `CLARIFICATION_CASCADE_MAP["1b"] = [2, 7, 9]`. Revision marks steps 2/7/9 stale.

---

## Step 2 — Full reflection (Moment 3)

**Purpose.** No new input is asked for. The system shows the full reflection — what I hear, assumptions, and what is not yet clear — built from **both** artefacts the person has already confirmed: the idea accepted at Step 1 and the Problem accepted at Step 1b. The user accepts or revises until the reflection is faithful. This is the only moment that carries assumptions and gaps; Steps 1 and 1b deliberately do not.

**Input.** `clarification_payloads["1"].idea_confirmed` **and** `clarification_payloads["1b"].problem` — the two confirmed artefacts. (`idea_verbatim` stays available as the record of the person's own words.) Reading the accepted Problem here is the dependency the state machine already asserts: `CLARIFICATION_CASCADE_MAP["1b"] = [2, 7, 9]` marks this step stale whenever the Problem changes, so a contract that ignored the Problem would contradict the code.

**Prompt scaffold.**

```
Reflect back in full — drawn from the idea and the problem you confirmed:

1. What I hear (numbered points, verbatim phrases where possible).
2. Assumptions. Default = my inference; add `(user)` to any the user committed to via their statement.
3. What I am NOT yet hearing (gaps that may matter).

Tell me where the reflection is wrong or incomplete.
```

**Output schema.**

```json
{
  "gate_token": "accepted",
  "mirror_numbered": ["..."],
  "tacit_assumptions": ["..."],
  "gaps_flagged": ["..."]
}
```

(JSON field `tacit_assumptions` is a legacy name retained for engine-contract stability; semantically the field holds the "Assumptions" list per prompt-scaffold item 2 — default = system inference, `(user)` tag where the user committed to it. `mirror_numbered` likewise keeps its name and now holds the what-I-hear list of this full reflection.)

**Gate.** `accepted`. Loop until the user accepts.

**Iteration header convention.** When presenting the reflection back to the user in any round (initial or revised), use the header `/clarification — Step 2: Full reflection ([what has changed])`. On the first pass, `[what has changed]` = `initial`. On each subsequent pass, replace it with a short delta tag describing what the user changed in the previous turn (e.g., `validation scope resolved`, `author OQ carried forward`). The same convention applies to every other step with a correction loop — Step 1 (`Step 1: Idea (…)`), Step 1b (`Step 1b: Problem (…)`), and Step 4.

**Resume mode.** Present the existing full reflection as-is, together with the two confirmed artefacts it was drawn from. Same accept-or-revise pattern as step 1.

**Cascade on update.** `[5, 7, 9]` — reflection change → re-run FC at 5, 7, 9.

**Mode — `Deep` (primary) + `Automatic` (fallback) — two-mode, CF-4 (renames the earlier
`manageable`/`default` pair); Deep-primary + code-enforced 2026-07-10.** This site's mode
resolves to **Deep** (`_claim_harvest.resolve_mode("/clarification Step 2")`); Deep = the
engine at **DEEP thoroughness**, the benchmark-proven higher-coverage setting (NORMAL
under-recalls — `Thoughts/refc-conformance-flip-*_RESEARCH.md`). After the reflection is
`accepted`, **dispatch the scoped claim-identification adapter** — a parent-spawned
`readonly-checker` subagent with isolated context (the `/double-check` / fact-check-engine dispatch
pattern, NOT the main AI improvising and NOT a `claude --print` subprocess) — to run the
engine's two-stage contract **at DEEP thoroughness** over the accepted reflection +
`tacit_assumptions` (Stage 1 identify + ambiguity-refusal gate → Stage 2 decontextualize/
structure; six per-criterion flags). Then run the **moderate consolidation pass**
(`_claim_consolidate` — `dedup_exact` + a scoped consolidator dispatch that merges
redundant DEEP splits while **preserving completeness**, verified by the completeness
spot-check; producer-never-verifies) over the DEEP output. The adapter returns a structured
claim set matching the `_claim_persist.py` payload schema (`source_type`, `source_path`,
six-flag `claims`, optional `refused`) and it **must carry `thoroughness: "deep"`** — the
code gate (CF-4/A2) refuses a NORMAL claim-set at this Deep site (no silent "flip ships
NORMAL"). The engine does **not** validate truth (A9); the typed claim set feeds the
downstream Discovery/Q&A synthesis, and ambiguity-refused candidates surface as Step-2
`gaps_flagged`.

**Code gate (why this can't be a silent no-op — A1).** Pass the adapter's output into
the Step-2 advance as a `claim_set` field:
`clar-advance SESSION_ID 2 '{"gate_token":"accepted", ..., "claim_set": {…}}'`.
`clar_advance` (`pre_plan_gates.py` `_validate_claim_gate`) **refuses to advance Step 2**
unless the payload carries either a schema-valid `claim_set` (validated at the seam via
`_claim_persist.parse_claim_set` — a missing criterion flag → refuse, nothing advances)
**or** a non-empty `claim_fallback_reason`. The invoke-and-verify decision now lives in
Layer-1 code (`code_first_architecture.md` — "code owns the flow"), not in this skill
text: the engine cannot silently skip.

**`default` fallback (retained, U3 — never deleted; now reason-logged).** If the adapter
genuinely can't run, or the user opts out, advance Step 2 with
`"claim_fallback_reason": "<why>"` instead of `claim_set` — the reflection above is
byte-unchanged between the two modes and the reason is recorded in state (the fact-check `BYPASSED` pattern;
never a silent skip). Retire `default` only after `manageable` is benchmarked at parity
(`.claim-runs.md`); retirement is user-driven (`[Decommission]` reminder — U4). The
Step-2 output schema, the `accepted` gate, and the `[5,7,9]` cascade are unchanged in
both modes.

---

## Step 3 — Elicit rules

**Purpose.** Surface the rules / constraints that govern this work (tech: design / architecture / security / infra; business: rules / limitations / regulations).

**Input.** `clarification_payloads["1"]` + `["2"]`.

**Prompt scaffold.**

```
What rules or constraints govern this work?

Examples by domain:
- Tech: design rules (architecture, security, infra, compliance)
- Business: limitations, regulations, policies

Tell me what applies. If you'd rather I recommend a starter set, say
"recommend" and I will dispatch an independent rules-recommender against
the inferred domain.
```

**If user defers ("recommend"):** dispatch an Opus `readonly-checker` agent against the inferred domain and present grouped suggestions. The agent reads only domain-relevant rules sources (e.g. `~/.claude/rules/code_first_architecture.md`, `Projects/CLAUDE.md`, KL).

**Output schema.**

```json
{
  "gate_token": "accepted",
  "rules": ["..."],
  "recommended_by_agent": false
}
```

**Gate.** `accepted`.

**Resume mode.** Present existing rules list as-is.

**Cascade on update.** `[7, 9]` — rules constrain Snapshot + Final Solution; re-run FC at 7, 9.

---

## Step 4 — Own-words solution

**Purpose.** User describes the solution in their own words. The system mirrors that solution back and loops until the mirror is faithful — its own mirror of its own input, unchanged by this plan. It follows the same correction-loop and iteration-header convention as Step 2, but it is not Step 2's full reflection: it carries no assumptions or gaps list.

**Input.** `clarification_payloads["1"]` + `["2"]` + `["3"]`.

**Prompt scaffold.**

```
Describe the solution in your own words.

What would you do, and why is it the right shape? I'll mirror it back —
same correction loop as the earlier steps — until the mirror is faithful.
```

**Output schema.**

```json
{
  "gate_token": "accepted",
  "own_words_solution": "...",
  "mirror_numbered": ["..."]
}
```

**Gate.** `accepted`.

**Resume mode.** Present existing own_words_solution + mirror as-is.

**Cascade on update.** `[7, 9]` — re-run FC at 7, 9.

---

## Step 5 — Q&A entries (surface new questions)

**Purpose.** Surface Q&A entries (questions) that must be answered before the framing is locked. Producer-never-verifies — independent critics surface Q&A entries, not the producer. The Q&A list holds both open entries and clarified entries (clarified ones get a `[✓]` marker plus the answer — see Step 7 `## Q&A`).

**Input.** `clarification_payloads["1"]` + `["2"]` + `["4"]` (confirmed idea + full reflection + own-words solution).

**Prompt scaffold (the skill's own copy is short — the work is delegated).**

```
Surface Q&A entries (questions) via the existing /challenge skill in surface_oqs mode.
Independent Opus critics produce 1–5 Q&A entries each, with
`question / why-it-matters / one-trade-off`.
```

**Dispatch (mandatory).**

```bash
/challenge --mode surface_oqs --embed --target <idea+own-words artifact path> --rounds 1
```

No agent count is named here: `surface_oqs` carries its own authored panel size and
the rigor dial caps it at dispatch (`/challenge`, *Per-mode defaults*). Restating the
number at the call site would pin the panel behind the operator's setting.

The artifact path is `~/.claude/state/clarification/<sid>/step5_target.md` (write the confirmed idea + full reflection + own-words solution there before dispatch). `--embed` suppresses the user-facing `(a)/(b)/(c)` handoff and emits a YAML summary the caller parses.

> **Note (D-Impl-1 forward-compat):** `--embed` is added to `/challenge` in D-Impl-4 (Slice D action A9). The calling site in this skill is forward-compatible: when D-Impl-4 ships, this dispatch starts emitting structured summaries automatically. If you run this skill before D-Impl-4 ships, `/challenge` will still produce the `(a)/(b)/(c)` handoff — treat that as a transient and revisit after A9 lands.

**Output schema.**

```json
{
  "gate_token": "oqs-complete",
  "oqs": [
    {"question": "...", "why_it_matters": "...", "trade_off": "..."}
  ],
  "fc_report_path": "<project>/Thoughts/<slug>-<ts>_CHALLENGE_<sid8>.md (bound; cold fallback: ~/.claude/state/plan_validation/adhoc/challenge_<sid8>_<ts>.md)",
  "escalated": false
}
```

**Gate.** `oqs-complete` (literal token — kept for engine-contract stability; semantically "Q&A surface complete"). If `/challenge` reported `escalated: true` under `--embed`, surface the candidate union to the user (per skill E4) and resolve before advancing.

**Resume mode.** Present existing Q&A list as-is.

**Cascade on update.** `[6, 7, 9]` — re-running step 5 invalidates research routing (step 6 may need updating) and re-runs FC at 7, 9.

---

## Step 6 — Research hard-gate

**Purpose.** Resolve every open Q&A entry before locking the framing. User picks a research depth and either resolves open Q&A entries or accepts the skip.

**Input.** `clarification_payloads["5"].oqs`.

**Prompt scaffold.**

```
Pick a research depth:
  (a) Deep   — invoke the harness skill /research (~/.claude/skills/research/SKILL.md), Deep tier
              (writes a _RESEARCH.md in Thoughts/)
  (b) Brief  — same skill, Standard tier
  (c) KL     — local <KL> grep — TEMPORARILY UNAVAILABLE: the skill's
              argument surface has no way to select this route yet (see the KNOWN
              OPEN GAP below, carried on the same S4 intake-site reconciliation).
              Restored once that lands.
  (d) Skip   — proceed without external research (skip is not bypass)

After research, I will show you the revised Q&A list. We may loop back
through step 5 once if research surfaced new questions. The hard gate
before step 7 is [0 unresolved Q&A entries].
```

**Dispatch through the harness `/research` skill — this step no longer registers `r0_intake` itself (research-entry-point-enforcement S2, 2026-09-22).**

When `depth ∈ {deep, brief}` — before invoking any research-shaped tool — call the harness skill with the spine as its **predefined scope**:

```
/research --from "<path to this topic's _THOUGHT.md>" --caller /clarification --cycle-id default --research-file "Thoughts/<topic-slug>_RESEARCH.md" [--scope-record '<ScopeRecord JSON>']
```

The skill writes its start marker, registers `r0_intake` on this step's behalf, and dispatches the `research` adapter. Only the fields that genuinely have a carrier through the skill's arguments (`--from`, `--caller`, `--cycle-id`, `--research-file`, `--scope-record`) reach the manifest this way: `caller_skill: "/clarification"`, `caller_session_id`, `cycle_id: "default"` (single-cycle caller — one research scope per session per the locked Discovery), `research_file_path` (via `--research-file`), the predefined scope read from `--from` (recorded with `explicit-argument` provenance), any `scope_record` (via `--scope-record`), and `downstream_tool: "~/.claude/skills/research/SKILL.md"` — the token `/clarification` is bound to in `CALLER_DOWNSTREAM_WHITELIST` (`research_pipeline.py`), repointed from `Skills/research-en.md` in the same change. **KNOWN OPEN GAP:** `topic_slug`, and the structured `scope` object the retired payload used to build directly (the open `oqs` from `clarification_payloads["5"].oqs` as `focused_questions`, `depth_tier` per the user's pick, `where_to_search`) where it is not carried by `--scope-record`/`--from`, have **no carrier** in the skill's argument surface today — there is no `--topic-slug` and no structured `--scope` flag. **S4 has landed and did NOT close this** — it reconciled the intake SITE and the approval CONDITION, and the missing flag is an argument-surface gap, not a site question. This gap is now **unowned**; do not keep pointing it at S4. Consequence: a project declaring `PIPELINE_OVERRIDES` keyed on `topic_slug` (`research_pipeline.py`) is currently held to the full `RESEARCH_SEQUENCE` from this caller, since no `topic_slug` reaches the cycle to key the override lookup. **Do not register `r0_intake` here as well:** two registrations for one cycle collide in `cmd_advance`.

**Why `--from <spine>` and not a sixth stop.** The spine's step-5 open questions ARE the scope; the spine's "carried open" row records this as the predefined-input case. The spine is uncommitted in the session writing it, so U3's git-`HEAD` branch cannot admit it — U3's *other* branch, a thought file named by an explicit argument, does: this step names it on behalf of the operator who just authored those questions through five gated stops and chose to research at all at the depth gate. The skill DISPLAYS the scope it read before the run and records provenance `explicit-argument` with the argument that named it; no interactive confirmation is added.

**The approval-artifact fields have landed (research-entry-point-enforcement S4, 2026-09-24).** This paragraph used to end "the approval-artifact fields land in S4", which is no longer a forward reference. What it means for this step, concretely: naming `/clarification` as `caller_skill` **no longer approves anything by itself** — before S4 the flip fired on a whitelisted caller name, so this step's registration asserted an approval nobody had given. The cycle is approved only because the payload carries the artifact: `user_approved_scope: true`, the `scope` object read from the spine, `scope_provenance: "explicit-argument"`, and `scope_source_ref` holding the `--from` argument verbatim so the approval stays re-checkable. This step's behaviour is unchanged in effect — the run is still approved with no sixth stop — but it is now approved *for a reason that can be inspected afterwards* rather than by the caller's name. A registration that omits those fields downgrades to interactive confirmation; it is never refused.

**`depth=kl`** targets the Internal-knowledge-base route (`~/.claude/rules/research-scope-framing.md`), which opens no manifest cycle and therefore never calls `r0_intake` — so the placeholder-path instruction that used to stand here (a `Thoughts/<topic-slug>_KL_RESEARCH.md` stand-in for `research_file_path`) is retired rather than corrected, because `research_file_path` is a required `r0_intake` field (`research_pipeline.py` `RESEARCH_SCHEMAS["r0_intake"]["required"]`) and this route never reaches that call at all. **KNOWN OPEN GAP:** the skill's argument surface (`--from`, `--caller`, `--cycle-id`, `--research-file`, `--scope-record`) carries no way for a caller to SELECT the Internal-KB route — there is no `--depth` or route-selecting flag. This step cannot currently dispatch `depth=kl` through the harness skill at all. **S4 has landed and did NOT close this either** — a route-selecting flag is an argument-surface gap, not an intake-site question, so this is likewise **unowned** and must stop pointing at S4. **`depth=skip`** calls nothing — no research fires, no gate to clear.

*(Retired text, for the record: this block used to construct the `r0_intake` payload here — `caller_skill`, `downstream_tool: "Skills/research-en.md"`, `caller_session_id`, `cycle_id`, `research_file_path` (with a `Thoughts/<topic-slug>_KL_RESEARCH.md` placeholder for `depth=kl`), `topic_slug`, `scope` — and dispatched it with `research_pipeline.py advance "$SESSION_ID" r0_intake "$PAYLOAD_JSON" --cycle-id default`. The skill registers now — but the fields are NOT all the same: `topic_slug` and the structured `scope` object have no carrier in the skill's argument surface, as the KNOWN OPEN GAP above records. An earlier version of this parenthetical claimed they were unchanged; corrected 2026-09-22 [sid:692a888e].)*

**Pre-filled, then confirmed — never a sixth stop, never silent consent.**

When the research this step dispatches should read one of the user's own sources — their code, their document folders or knowledge library (S7), and later their tracker — this step assembles the declaration **on the user's behalf** rather than opening a separate source picker. Build it with `~/.claude/skills/research/source_picker.py` (`build_record`, whose JSON form is `ScopeRecord.to_json()`), and pass it to the skill as `--scope-record '<json>'` — the skill's intake hoists it onto the cycle exactly as `r0_intake` always did (`research_pipeline.py`, the `scope_record` hoist). *(Its carrier changed from "the payload above" to "the skill's argument" at S2; the assembly itself, and whether it should be assembled by prose at all, belong to the "declaration assembled by prose" item — nothing here decides that.)*

**Pass the routing path — `build_record(selection, route=<the route this research will run on>)`.** This step is the one caller that assembles a declaration **outside the source list entirely**, so no picker guards it; without the route it would be the door through which a class gets declared on a route that is not willing to read it — today that means a `web` source declared for an Internal-KB run, which opens no manifest cycle and would therefore be read unbounded. `build_record` refuses that combination when it is given the route, and cannot see it when it is not. *(This sentence gave the reason as "a route that has no reader for it" until 2026-09-11. That was false — readers dispatch on kind, not route — and the route set is a source-tier policy instead; `source_picker.is_selectable` carries the canonical statement.)* The routes are named in `source_picker.ROUTES`; the research dispatched from here runs on an open-web route unless this step says otherwise, so `route=source_picker.ROUTE_NINJA` (or the route actually chosen) is the value to pass — never omit it.

Concretely, and **reversed on 2026-09-11 (Q26)**: `knowledge_library` and `document_folder` are now readable on **every** route, so declaring either from here for an open-web run **succeeds** and the source is actually read. This sentence previously said the opposite — that both were readable only on the Internal-knowledge-base route and would be refused here — which was true when written and became false when the two classes were widened. It is corrected rather than deleted because it is the instruction this caller acts on: a session following the old wording would have formed a false belief about what its own `build_record` call does.

What is still refused from here is the other direction: `code`, `web` and `linear` cannot be declared for a run on the Internal-knowledge-base route. Passing the route remains mandatory for exactly the reason above — it is what makes that refusal reachable at all.

**`code` declared from here is now actually read.** From S3 until the code-driver slice it was not: the declaration was assembled, hoisted onto the cycle, and nothing ever opened the repository, so this step could pre-fill a source the run then ignored. `~/.claude/skills/research/declared_read.py` is the reader that closes it, and the research this step dispatches invokes it after intake and before synthesis — see `~/.claude/rules/research-scope-framing.md` Step 4. The same ceiling applies here as everywhere else it is described: the declaration is **followed, not enforced** (design-A18, withdrawn), so never tell a person a read outside it is impossible.

Two rules make this a pre-fill rather than a delegation of consent:

1. **The assembled source list is SHOWN at this step's existing depth gate**, in the same prompt where the user picks a depth — each source and the bound given for it. They see it and confirm it there. No additional approval prompt is added, which is the whole point: the framing phase already has a gate, and reusing it is what keeps the count at five.
2. **A framing-driven run never silently narrows to web-only.** A source that cannot be reached, or that has no bound, degrades and is reported exactly as it would in a direct `/research` run — it is not quietly dropped so the run can proceed.

`depth=skip` calls nothing, so it carries no declaration either. When no internal source is in play, omit `--scope-record` entirely; the skill's intake is still not byte-identical to what this step used to register — it differs in the ways the KNOWN OPEN GAP above (`topic_slug`, the structured `scope` object) already records. `downstream_tool` is not part of that gap — it IS carried, repointed from `Skills/research-en.md` to `~/.claude/skills/research/SKILL.md` in the same change (see above).

A non-zero exit from the skill's own intake indicates the payload it built failed validation (e.g., a mistyped `caller_skill`, a missing `research_file_path`). The skill surfaces the error and does NOT fall through to the search dispatch — silent bypass is the failure mode the redesign exists to close (per `code_first_architecture.md` "Code does, AI thinks, code checks"). This step surfaces it likewise.

**Output schema.**

Note: `kl` remains a valid stored value (a prior payload may still carry it), but it
cannot currently be SELECTED at the depth-gate prompt above — see the KNOWN OPEN GAP
in the dispatch section.

```json
{
  "gate_token": "research-resolved",
  "depth": "deep|brief|kl|skip",
  "research_artifact": "<path or null>",
  "resolved_oqs": ["..."],
  "still_open_after_research": []
}
```

**Hard gate (code-enforced upstream of step 7).** `still_open_after_research` must be `[]` before `clar-advance` to step 7 is accepted by the skill author. Skip (`depth=skip`) is allowed only when the user resolves open Q&A entries another way (e.g. confirming the question is moot in light of the rules / own-words solution); the user must list `resolved_oqs` explaining each.

**Resume mode.** Present existing research disposition + open Q&A status as-is.

**Cascade on update.** `[5, 7, 9]` — research may surface new Q&A entries; step 5 may need to re-run; FC at 7, 9 re-runs.

---

## Step 7 — Discovery draft + diffs + validate

**Purpose.** Draft the `# Discovery` section, show a diff vs the initial idea, and **independently fact-check** the coherency chain before the user sees the draft as locked-able. The 🔒 markers are documentation in the draft — actual lock markers are written at Step 9.

**Input.** All prior payloads (`1` … `6`).

**Prompt scaffold (AI → user).**

```
Here is the Discovery draft. Sections:

  ## Guiding Policy     🔒  (overall approach; see plan-gates.md)
  ## Desired Outcome    🔒  (Cagan-framed via outcome-framing skill)
  ## Desired Solution   🔒  (from Step 4 own-words solution)
  ## Scope                  (in/out boundaries; from steps 2/3/6)
  ## Q&A                    (Q&A entries: open + resolved; [✓] = resolved with answer)
  (## Metrics is written at Step 8)

Below is the diff vs the initial idea. Then the fact-check report.
```

**Drafting cadence (user-selectable — default full-pass; code-enforced).** Before drafting, ask the user which cadence to use for this Step-7 pass. The choice is **persisted in topic state and enforced by the engine** — it is not a prose convention the AI can skip:

  (i) **Full draft now** (default) — synthesize all five sections in one pass, then run the fact-check tail and the single `clar-advance SESSION_ID 7` finalize. Do NOT set a cadence (leaving it unset == full-pass).
  (ii) **Section by section** — first record the cadence, then draft and confirm one section at a time before the next:

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py clar-set-cadence SESSION_ID section_by_section
```

After that, drive the five section sub-steps **in this fixed order**, each gated on its own user-confirm token, calling `clar-advance` per section before drafting the next:

| Sub-step | Section | Drafted via |
|----------|---------|-------------|
| `7a` | Guiding Policy | — |
| `7b` | Desired Outcome | `outcome-framing` |
| `7c` | Desired Solution | verbatim Step 4 own-words |
| `7d` | Scope | steps 2/3/6 |
| `7e` | Q&A | resolved entries |

```bash
# after the user confirms each drafted section:
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py clar-advance SESSION_ID 7a '{"gate_token":"section-accepted", ...}'
# ... then 7b, 7c, 7d, 7e in order ...
```

**Code enforcement (why this can't be bypassed).** Once cadence is `section_by_section`, the engine refuses `clar-advance SESSION_ID 7` until all five sub-steps `7a..7e` have been recorded in order — a one-shot dump followed by a single Step-7 finalize raises a sequence violation (`pre_plan_gates.py` `clar_advance`). The per-section confirms are author/user checkpoints, NOT fact-check seams. **All five sections are drafted before the Step-7 gate fires** in either cadence; the single `/double-check` dispatch below still runs once at the end against the full coherency chain (producer-never-verifies), and the final `clar-advance ... 7` fires once after PASS (or a two-option ESCALATE disposition). The cadence is persisted, so a Step-7 resume mid-cadence (e.g. current step `7c`) holds the choice; a `clar-rewind` to step 6 or earlier resets it to full-pass.

**Origin convention for `## Q&A`.** Entries authored during `/clarification` may omit the origin tag (default = clarification). Entries authored from downstream phases MUST tag origin:

> `Q (origin: solution-design 2026-05-22): …`
> `A (origin: solution-design 2026-05-23): …`

Resolved entries get `[✓]` appended. `_validate-thought-file.py` warns (does not block) on missing origin tags on Q-lines authored after the Step-9 lock marker.

**Draft the Desired Outcome via the `outcome-framing` skill.**

```bash
# Skill input: {diagnosis (from ## Problem), own_words_solution (from Step 4)}
# Skill output: {outcome, rationale, source_citation}
```

If `outcome-framing` returns no output, present a draft Outcome inline labeled `(editorial — outcome-framing skill unavailable)`.

**Sections in scope at this gate** (read this before dispatch — the `--against` string is intentionally scoped):

| Section | In scope? | Reason |
|---|---|---|
| `## Problem` | ✓ included | written at Step 1b — coherency anchor |
| `## Desired Outcome` | ✓ included | drafted at Step 7 via `outcome-framing` — coherency anchor |
| `## Desired Solution` | ✓ included | verbatim Step 4 own-words solution — coherency anchor |
| `## Guiding Policy` | ✓ included | drafted at Step 7 — coherency anchor |
| `## Q&A` | ✓ included | resolved entries from Step 6 — coherency anchor |
| `## Metrics` | ✗ excluded | NOT YET WRITTEN; arrives at Step 8 — Gate 3 (Step 9) covers it |
| `## Scope` | ✗ excluded | descriptive in/out boundaries; not a coherency axis (mutable post-lock) |

**Dispatch independent FC (mandatory).**

```bash
/double-check --class gate --rounds 2 --against "the Problem, Desired Outcome, and Desired Solution while respecting the Guiding Policy and the resolved Q&A"
```

`--class gate` because this verdict blocks the lock: the allocation comes from the
operator's rigor tier, resolved at dispatch (`/double-check`, *Allocation*). At
`light`/`minimal` it is one isolated checker rather than a vote — report the verdict
as such.

The Discovery draft is written to `~/.claude/state/clarification/<sid>/discovery.md` before dispatch.

**Output schema.**

```json
{
  "gate_token": "validated",
  "discovery_draft_path": "~/.claude/state/clarification/<sid>/discovery.md",
  "fc_report_path": "<project>/Thoughts/<slug>-<ts>_DOUBLECHECK_<sid8>.md (bound; cold fallback: ~/.claude/state/plan_validation/adhoc/doublecheck_<sid8>_<ts>.md)",
  "fc_verdict": "PASS|ESCALATE",
  "escalate_disposition": null
}
```

**Gate (two-option ESCALATE pattern).**

- `validated` — FC verdict PASS. Advance.
- On FC ESCALATE (`max_rounds` reached with unresolved discrepancies), surface the discrepancies verbatim and present the user a two-option choice (do NOT default-advance):
  - **(a) One more round** — re-invoke `/double-check` with the same `--against` string; round counter increments past `max_rounds`; fresh isolated checkers. After the new round, re-evaluate.
  - **(b) Proceed as is** — `gate_token: "validated"`, `fc_verdict: "ESCALATE"`, `escalate_disposition: { discrepancies: [...], user_picked: "proceed_as_is" }`. Advance with the unresolved discrepancies recorded.
  - No `override_reason` capture — the user's pick is recorded in `escalate_disposition.user_picked`.

**Resume mode.** Present existing Discovery draft + FC report path as-is. If marked `stale: true` (cascade from earlier step), re-run FC before accepting `validated`.

**Cascade on update.** `[9]` — step 7-tail and step 9 re-run.

---

## Step 7-tail — Optional challenge

**Purpose.** Offer the user one challenge round on the validated Snapshot. Producer-never-verifies — critics surface critiques the producer would miss.

**Gate (precondition).** Step 7 must be at `validated` or `validated-with-override`.

**Prompt scaffold.**

```
Want to challenge the Snapshot one round?  (y / n)

A `y` invokes the existing /challenge skill in challenge_concept mode.
Independent Opus critics produce 1–5 critiques each, with
`claim / weakness / trade-off`. You can amend the Snapshot in response.
```

**If `y`, dispatch.**

```bash
/challenge --mode challenge_concept --embed --target <Snapshot-draft path> --rounds 1
```

No agent count here either — `challenge_concept`'s authored default is one critic, and
the dial caps rather than replaces it.

> **Forward-compat note.** Same as step 5 — `--embed` lands in D-Impl-4 (A9). The calling site is forward-compatible.

**Output schema.**

```json
{
  "gate_token": "challenge-resolved",
  "challenged": true,
  "challenge_report_path": "<project>/Thoughts/<slug>-<ts>_CHALLENGE_<sid8>.md (bound; cold fallback: ~/.claude/state/plan_validation/adhoc/challenge_<sid8>_<ts>.md)",
  "amended_snapshot": false,
  "escalated": false
}
```

If `amended_snapshot: true`, cascade back to step 7 (re-validate the amended Snapshot via `/double-check`).

**Gate.** `challenge-resolved`.

**Resume mode.** Present existing challenge disposition as-is.

**Cascade on update.** `[7, 9]` — amended Snapshot re-runs FC at 7 and propagates to 9.

---

## Step 8 — Metrics

**Purpose.** Define one OMTM + one secondary metric, validate against Lean Analytics' 4 properties + actionability, and write the `## Metrics` block under `# Discovery`.

**Input.** `clarification_payloads["7"].discovery_draft_path` (for context — what we're trying to improve).

**Prompt scaffold.**

```
What do you want to improve? I will propose:
  - one OMTM (one metric that matters most for the current stage)
  - one secondary metric (a guardrail or directional signal)

Then I'll invoke the lean-analytics-metrics skill to validate both
against the 4 good-metric properties + actionability.
```

**Dispatch (mandatory).**

```bash
# Skill input: {omtm, secondary, context}
# Skill output: {verdict: PASS|FAIL, per_property: {...}, rationale, source_citation}
# The skill internally dispatches /double-check against the candidate
# metrics + criteria for the verdict.
```

The `lean-analytics-metrics` skill reads `<KL>/ProductManagement/Sources/Books/lean-analytics/lean-analytics.md:24-25`. If unavailable, apply the 4-properties check by hand and label `(editorial — lean-analytics-metrics skill unavailable)`.

**`## Metrics` write format (Step 8 writes this block; Step 9 locks it).**

```markdown
## Metrics

OMTM: <one metric>
Secondary: <one metric>
(verdict: PASS|FAIL via lean-analytics-metrics)
```

**What counts as the metric line** (`pre_plan_gates.py has_metric_line`, the shared predicate both checking sites call; **blocks** — `_validate-thought-file.py` puts the verdict in `results`, and `permission-plan-gate.sh` runs that validator live on this spine at `ExitPlanMode`, so a failing check refuses the plan exit):

A metric line starts its own line with `OMTM`, optionally bulleted and optionally decorated, may carry a label before the separator, and **must be followed by a value**. A bare label with nothing after it does not count.

| Form | Accepted? | Why |
|---|---|---|
| `OMTM: <value>` | yes | the plain form |
| `- OMTM: <value>` | yes | bulleted |
| `**OMTM:** <value>` | yes | decoration is part of the field, not the name |
| `**OMTM.** <value>` | yes | `.` is accepted as the separator alongside `:` |
| `- OMTM (per Gate 4): <value>` | yes | a label may sit between the name and the separator |
| `**OMTM — main-chat growth:** <value>` | yes | same, with an em-dash label |
| `OMTM:` | **no** | a label with no value says nothing |
| a mid-sentence mention of OMTM | **no** | the name must start the line |

*(Changed 2026-09-18 by discovery-field-predicate-coherence A8. This statement previously read "The `OMTM:` prefix (start of line) is the marker that `validate_discovery_locked_fields()` checks" — which is now false in three ways: the accepted forms are wider, a value is required where presence alone used to pass, and the module that BLOCKS is `_validate-thought-file.py`, not `validate_discovery_locked_fields()`. That function still runs the same rule through the same shared helper, but its callers are non-blocking — `topic_orient` feeds `spine_locked` to `clarification-nudge.sh`, `research-scope-gate.sh` and `/work-start`. Measured over both corpus roots: 0 spines newly blocked, 1 verdict flip, and 15 further spines whose false "Missing OMTM line" now correctly disappears — every one of them carrying a real labelled metric the old prefix test could not see.)*

Secondary metric is mutable after Step 9 lock.

**Output schema.**

```json
{
  "gate_token": "metrics-accepted",
  "omtm": "...",
  "secondary": "...",
  "verdict": "PASS|FAIL",
  "per_property": {
    "comparative": true,
    "understandable": true,
    "ratio_or_rate": true,
    "changes_behavior": true,
    "actionable": true
  },
  "fc_report_path": "<path or null>"
}
```

**Gate.** `metrics-accepted`. FAIL verdict blocks advance; user must redraft.

**Resume mode.** Present existing metrics + verdict as-is.

**Cascade on update.** `[9]` — metrics feed the lock; re-run FC at 9.

---

## Step 9 — Commit — lock 4 fields

**Purpose.** Run the final FC on the coherency chain, then lock the four `DISCOVERY_LOCKED_FIELDS` by writing inline lock markers. The Desired Solution is verbatim Step 4's own-words solution — no new synthesis. After lock, the four fields are defended by the PreToolUse `check-discovery-lock.sh` hook.

**Input.** All prior payloads, especially `1b` (Problem), `7` (Discovery draft), `8` (Metrics).

**Prompt scaffold.**

```
Dispatching /double-check against the Problem + Desired Outcome +
Desired Solution + Guiding Policy + Metrics coherency chain before locking.
```

**Sections in scope at this gate** (read this before dispatch — the `--against` string is intentionally scoped):

| Section | In scope? | Reason |
|---|---|---|
| `## Problem` | ✓ included | written at Step 1b — coherency anchor |
| `## Desired Outcome` | ✓ included | locked Cagan-framed outcome — coherency anchor |
| `## Desired Solution` | ✓ included | verbatim Step 4 own-words — coherency anchor |
| `## Guiding Policy` | ✓ included | locked policy — coherency anchor |
| `## Metrics` | ✓ included **IF present** | OMTM + secondary written at Step 8. If the section is empty, override-flagged, or absent, the checker treats this anchor as N/A rather than DISCREPANCY (see Edge: Metrics-not-provided). |
| `## Q&A` | ✗ excluded | mutable post-lock; not part of the locked-field coherency check |
| `## Scope` | ✗ excluded | descriptive in/out boundaries; not a coherency axis (mutable post-lock) |

**Edge: Metrics-not-provided.** When `## Metrics` is empty, override-flagged at Step 8, or the OMTM line is absent, the checker MUST treat the Metrics anchor as N/A — do not flag DISCREPANCY for the absent section. The skill records `metrics_present: false` in the output schema; downstream consumers can detect it. The other four anchors (Problem / Desired Outcome / Desired Solution / Guiding Policy) are still verified.

**Dispatch (mandatory, BEFORE lock).**

```bash
/double-check --class gate --rounds 2 --against "the Problem, Desired Outcome, and Desired Solution while respecting the Guiding Policy, with Metrics that measure the Desired Outcome"
```

`gate` — this runs BEFORE the lock and its verdict decides whether the lock happens.

**Output schema.**

```json
{
  "gate_token": "lock",
  "fc_report_path": "<project>/Thoughts/<slug>-<ts>_DOUBLECHECK_<sid8>.md (bound; cold fallback: ~/.claude/state/plan_validation/adhoc/doublecheck_<sid8>_<ts>.md)",
  "fc_verdict": "PASS|ESCALATE",
  "escalate_disposition": null,
  "metrics_present": true,
  "locked_thought_path": "<Thoughts/<slug>_THOUGHT.md or wherever the topic file lives>"
}
```

**Gate (two-option ESCALATE pattern).**

- `lock` — FC verdict PASS. Lock the four DISCOVERY_LOCKED_FIELDS.
- On FC ESCALATE (`max_rounds` reached with unresolved discrepancies), surface the discrepancies verbatim and present the user a two-option choice (do NOT default-lock):
  - **(a) One more round** — re-invoke `/double-check` with the same `--against` string; round counter increments past `max_rounds`; fresh isolated checkers. After the new round, re-evaluate.
  - **(b) Proceed as is** — `gate_token: "lock"`, `fc_verdict: "ESCALATE"`, `escalate_disposition: { discrepancies: [...], user_picked: "proceed_as_is" }`. Lock the four fields with the unresolved discrepancies recorded.
  - No `override_reason` capture — the user's pick is recorded in `escalate_disposition.user_picked`.

**Lock write.** On `lock`, write the four lock markers inline immediately after each locked field's header in the topic's `_THOUGHT.md` `# Discovery` section:

```markdown
## Desired Outcome
<!-- locked: <SESSION_ID> <ISO 8601 UTC> -->

<body>
```

All four `DISCOVERY_LOCKED_FIELDS` must be present and non-empty, and `## Metrics` must carry a metric line.

**What counts as content** (`pre_plan_gates.py is_effectively_empty`, the shared predicate every emptiness site calls; **blocks** — an empty locked field fails the validator the plan gate runs live, and the plan exit is refused):

**Chrome does not count as content.** A field body holding only a lock marker, only the `<text>` sentinel, or only HTML comments is **empty**, exactly as a blank body is. Write something a reader can act on, not a marker.

**Which module blocks, and which only reports** — the two are different and the difference is load-bearing:

| Rule | Enforcing function | Blocks or warns |
|---|---|---|
| all four fields present | `_validate-thought-file.py check_sections` | **blocks** (gate runs the validator live → plan exit) |
| a field body is not chrome-only | `pre_plan_gates.py is_effectively_empty` | **blocks** (same path) |
| `## Metrics` carries a metric line | `pre_plan_gates.py has_metric_line` | **blocks** (same path) |
| a locked field's heading is readable | `pre_plan_gates.py heading_re` | **blocks** — an unreadable heading reads as absent |
| a heading is a near miss (`### Metrics`, `## Metrics:`) | `_validate-thought-file.py _near_miss_heading_line` | **warns** under `## Warnings`; the edit proceeds |

*(Changed 2026-09-18 by discovery-field-predicate-coherence A8/A8b. This statement previously named `validate_discovery_locked_fields()` as the enforcer of both rules. That function still runs them, through the same shared predicates, but nothing blocking reads it. Naming the non-blocking module as the gate is the inversion this topic exists to remove, and it was written here too.)*

*(Changed 2026-09-22 by exitplanmode-spine-gate-live-validation. The blocking path named here used to be `_validate-thought-file.py` → the `_THOUGHT_check.md` sidecar → `permission-plan-gate.sh`. It no longer runs through that sidecar: at `ExitPlanMode` the gate runs `_validate-thought-file.py` **itself** on the spine the plan names, and refuses with the validator's own finding. `verify-thought-file.sh` still writes the sidecar on a `Write`/`Edit` of a spine, but it is **advisory early feedback** — its presence, absence, or verdict has no effect on approval. What this means for a spine written outside the harness's edit tools, such as `/clarification-v2`'s Python write: it is validated at approval exactly like any other.)*

**What counts as a locked field's heading** (`pre_plan_gates.py heading_re`, the anchored predicate every locked-field consumer shares; blocks — an unreadable heading means the field reads as absent downstream and `_validate-thought-file.py` reports `Missing <field>` when the plan gate runs it live, refusing the plan exit):

A locked field's heading must **start its own line** as `## <Name>` and be followed by **whitespace or the end of the line**. Decoration after that point *is* part of the field and is accepted.

| Form | Accepted | Why |
|---|---|---|
| `## Metrics` | yes | the canonical form |
| `## Metrics 🔒 (locked 2026-09-17)` | yes | whitespace follows the name; the decoration is the field's |
| `## Metrics:` | **no** | a colon is not whitespace — the predicate stops at the name |
| `### Metrics` | **no** | heading level is part of the form, not decoration |
| `  ## Metrics` | **no** | the heading must start its own line |
| `see ## Metrics below` | **no** | a prose mention is not a heading |

This rule is **ratified from what already runs** rather than newly invented: it fits all 80 measured locked-field slots across both corpus roots, and both alternatives are refuted by real files — a stricter `^<name>$` silently un-defends the 5 decorated slots, and a looser rule gives two lines one field, four times over in a single spine. A mis-levelled or colon-suffixed heading is **reported, not refused**: `_validate-thought-file.py` names the offending line under `## Warnings` and the edit proceeds, so a heading can always be repaired in place.

*(Kept equal to the code by a drift test — see `test_clarification_schema.py`, which derives the accepted and rejected forms from `heading_re` itself and fails if this table and the regex disagree.)*

The `<!-- handoff-src-hash -->` marker is refreshed via `_discovery_locked_fields_hash()` at `pre_plan_gates.py:175` (regex) and the write seam at `pre_plan_gates.py:2332`. The hash covers only the four locked fields — mutable edits to `## Scope` / `## Q&A` do not invalidate it.

After writing the lock markers, call `clar-advance SESSION_ID 9 '{...}'`. The `clar_advance` handler clears `clarification_active_session` from the topic state, enabling the `check-discovery-lock.sh` hook to block subsequent edits to the locked fields.

**Resume mode.** Present existing locked `# Discovery` + FC report as-is. Revision re-runs FC + re-locks (sets `clarification_active_session` via `--from` entry at Step 0b).

**Cascade on update.** `[10]` — lock feeds handoff.

---

## Step 10 — Handoff

**Purpose.** Compose the next-session prompt per `~/.claude/rules/prompt-engineering.md`. Two destinations:

- (a) `/solution-design` (default) — the next session enters Phase 2 (Solution Design) with the locked `# Discovery` as input.
- (b) Fast-track to `/plan` (Phase 3) — skips Solution Design. Useful for small-scope or well-understood topics.

**Prompt scaffold.**

```
Handoff. Choose:
  (a) Compose a next-session prompt for /solution-design (default, Phase 2)
  (b) Fast-track to /plan (Phase 3, skip Solution Design)
```

**Output schema.**

```json
{
  "gate_token": "handoff-emitted",
  "destination": "solution-design|plan",
  "prompt_path": "<written via write-next-session-prompt>"
}
```

**Gate.** `handoff-emitted`. The skill author writes the next-session prompt via `pre_plan_gates.py write-next-session-prompt` (existing seam), then advances.

**Skill-run metrics (orchestrator-pattern — S5 meter; best-effort, never blocks the handoff).** After the `handoff-emitted` gate, record one skill-run row for this `/clarification` run and refresh the managed Stats block (clone of `~/.claude/skills/close/SKILL.md` §D). Each command is best-effort — a metrics failure must never abort the skill (`… || true`):

1. **Secondary (model-weighted usage)** — derived for the session (reuses `research_token_parser`, no new capture):
   ```bash
   python3 ${KIT_HOOKS_DIR}/skill_metrics.py secondary SESSION_ID
   ```
2. **Append the run row** — `run_kind:"full"`; `omtm_main_chat_delta` = the main-chat token delta the run added (`null` until precise per-run capture lands); `secondary_model_weighted` from step 1:
   ```bash
   python3 ${KIT_HOOKS_DIR}/skill_runs.py append \
     '{"skill":"clarification","sid":"SESSION_ID","run_kind":"full","omtm_main_chat_delta":<k|null>,"secondary_model_weighted":<v|null>,"note":"clarification run"}'
   ```
3. **Refresh the managed Stats block** in the topic's project Stats.md (idempotent; rolls up OMTM/Secondary + the three signals):
   ```bash
   python3 ${KIT_HOOKS_DIR}/skill_metrics.py write-stats <project>/Stats.md
   ```

The blocked-look-alike signal is emitted automatically by the guardrail hook (`check-skill-marker.sh`) once a `clarification` shadow-map entry exists; this step only records the run's own OMTM + Secondary.

**Resume mode.** Present existing handoff prompt as-is.

**Cascade on update.** None (step 10 is terminal in this skill).

---

## Resume mode (`--from <path>`) — composite behavior

At each step, `clar-status` is consulted. If a prior payload exists, the AI presents existing content **as-is** at the gate:

> "This is what step N currently holds — accept (no change) or provide feedback?"

- **Accept** → no write; advance to the next step.
- **Feedback** → run that step's correction loop (same template as the fresh-mode step); call `clar-rewind --to <N-1>` then `clar-advance <N> ...` with the new payload. Per `CLARIFICATION_CASCADE_MAP`, downstream-dependent steps are marked `stale: true` and re-confirm in turn (default) or re-run on explicit `--rerun STEP`.

A modified step with an FC seam (5 / 7 / 9 — and 7-tail if amended) **always re-runs** the FC; PASS or explicit override is required to advance.

## Cascade-on-update map (reference)

```
1  → [2, "1b", 3, 4, 5, 7, 9]
1b → [2, 7, 9]
2  → [5, 7, 9]
3  → [7, 9]
4  → [7, 9]
5  → [6, 7, 9]
6  → [5, 7, 9]
7  → [9]
8  → [9]
9  → [10]
10 → []
```

Authoritative copy: `CLARIFICATION_CASCADE_MAP` in `${KIT_HOOKS_DIR}/pre_plan_gates.py`.


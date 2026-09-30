---
name: plan-followups-review
version: "1.0"
description: Walk a set of already-verified observations one at a time into triaged TODOs. For each observation the operator sees a plain-words "why this matters", an optional code-first /recommend with trade-offs, then an explicit accept/edit/discard. A follow-up TODO is created only on explicit accept and only through the framing gate — never silently, never in a batch. Reusable primitive; the caller hands pre-verified observations and reads back a disposition summary. Main-session use only.
allowed-tools: Bash, Read, AskUserQuestion, Skill
---

# /plan-followups-review — One-at-a-Time Observation Triage

`/plan-followups-review` is a reusable sub-skill that walks a set of **already-verified** observations one at a time into triaged dispositions (accept → a framed TODO / edit / discard). It is the observation-triage primitive the plan-execution orchestrator (S9) calls at end-of-plan; it is also invokable standalone.

The walk loop, the per-observation disposition ledger, the complete-coverage gate, the no-silent-creation invariant, and crash-safe resume all live in the code shim `run.py` (Trust Hierarchy: Code > Rules > Skill text). This file owns only the judgment surface: composing the plain-words why-line, running the recommendation, presenting trade-offs, and taking the operator's accept/edit/discard call.

The per-observation step sequence below is a **locked step contract** sanctioned by `~/.claude/rules/plan-gates.md` "Skill-internal locked step contracts" — distinct from plan Coherent Actions tables, which remain bound by `prompt-engineering.md` "prefer general instructions over prescriptive steps".

**Main-session-only. Cannot be invoked from inside a subagent.** (Producer-never-verifies discipline matches `/recommend` and `/double-check`.)

---

## Trigger

Explicit invocation only:

- `/plan-followups-review` (caller passes the observation list — see Input contract)
- `/plan-followups-review --help`
- Invoked by the plan-execution orchestrator (S9) at end-of-plan observation harvest.

**Not auto-triggered.** v1 is callable-only, by the operator or by an upstream skill/orchestrator.

---

## Input contract — the observation record (caller / S9-facing port)

The caller hands a JSON array of observation records. Each record:

| Field | Required | Description |
|-------|----------|-------------|
| `id` | yes | Stable, unique identifier for the observation within this walk. |
| `title` | yes | Short label the operator recognizes at a glance. |
| `body` | yes | The observation content — the raw material the why-line is composed from. |
| `source_ref` | no | Optional pointer (file:line, slice id, …) to where the observation arose. |

**The observations arrive ALREADY VERIFIED.** `/plan-followups-review` runs **no** fact-checking pass of its own over them — verification is the caller's upstream responsibility (e.g., the orchestrator's `/double-check` gate before it hands the list over). This skill never re-verifies its inputs; producer-never-verifies holds by topology.

`run.py` validates this schema at `init` and rejects a malformed list (missing/empty `id`/`title`/`body`, non-object element, duplicate `id`) with exit 3.

---

## Calling the walk-state machine

Drive `run.py` from this skill folder via Bash, one subcommand per call, JSON payload on stdin:

```
echo '<json>' | python3 ~/.claude/skills/plan-followups-review/run.py <subcommand>
```

| Subcommand | Payload | Purpose |
|------------|---------|---------|
| `init` | `{session_id, observations:[…]}` | Seed the walk. Idempotent — resumes (never clobbers dispositions) if a ledger already exists for the session. |
| `next` | `{session_id}` | Hand out the next undisposed observation (`status: OBSERVATION`) or `status: DONE`. Exactly one observation per call. |
| `record` | `{session_id, obs_id, disposition, todo_ref?, note?}` | Record a disposition. `disposition` ∈ `accept` / `edit` / `discard`. `accept` REQUIRES a non-empty `todo_ref` (else REJECTED, exit 1). |
| `summary` | `{session_id}` | Emit the disposition summary (`status: SUMMARY`) — only after every observation is disposed, else `INCOMPLETE` (exit 2). |
| `reset` | `{session_id}` | Clear the session ledger (standalone re-runs / tests). |

Exit codes: `0=OK`, `1=REJECTED` (recoverable — re-ask), `2=INCOMPLETE` (coverage gate), `3=usage/schema error`. Pass the current `SESSION_ID` so resume works across `/clear`. State persists at `~/.claude/state/plan-followups-review/<session_id>.json`.

---

## Locked step contract

**Setup.** Call `run.py init` with the observation list + `SESSION_ID`. Then loop: call `run.py next`. On `status: DONE`, go to **Closeout**. On `status: OBSERVATION`, run the per-observation contract below against that single observation, then loop back to `next`.

The operator sees **exactly one observation at a time**. Never present a batch list or a multi-select over observations — the single-hand-out is enforced in `run.py next`, and this surface must match it.

### Step 1 — Why this matters

Compose a plain-words "why this matters" for the observation from its `body` (and `source_ref` if present), in the operator's terms. Present it inline **before** asking for any decision. This is judgment (AI) — keep it to a short paragraph; do not ask the operator anything yet.

### Step 2 — Triage lean

Ask the operator, via `AskUserQuestion` (single observation, single question), whether they lean toward keeping this observation or dropping it. If they lean drop, skip to **Step 5** with the discard option foregrounded. If they lean keep (or are unsure), continue to Step 3.

### Step 3 — Recommendation (kept items)

For an observation the operator leans toward keeping, invoke `/recommend` whole and name no proposer count:

```
/recommend
```

`/recommend` carries its own authored default and the rigor dial caps it (class `panel`); a count restated here would pin the panel behind the operator's setting. Augment the target/context with a **code-first** framing: the recommendation should respect `~/.claude/rules/code_first_architecture.md` (prefer a code-enforceable follow-up over an AI-judgment one where the choice exists). Do not reinvent `/recommend`'s logic — its proposers are parent-spawned isolated Explore agents; that isolation is inherited, not duplicated.

### Step 4 — Trade-offs

Present the recommendation's trade-offs (the `trade-offs` and `concerns` lines from `/recommend`) to the operator inline, so the keep/accept decision is informed. Render `/recommend`'s output; do not editorialize past it.

### Step 5 — Decision (accept / edit / discard)

Ask the operator, via `AskUserQuestion` (this one observation only), for the explicit call:

- **Accept** → go to **Step 6**.
- **Edit** → the operator revises the observation/framing; record via `run.py record … --disposition edit` (optionally re-run Steps 3–4 first). No TODO is required for an edit.
- **Discard** → record via `run.py record … --disposition discard`.

No batch multi-select. One `AskUserQuestion` per observation.

### Step 6 — Accept ⇒ framed TODO, then record

On accept, create the follow-up TODO through the framing gate by invoking `/work-frame-and-create-todo` whole (it owns the 4-component framing validator + the code-enforced retry cap). Capture the resolved TODO reference it reports (the `TODO.md` path it landed in).

Then record the disposition WITH that reference:

```
echo '{"session_id":"<SID>","obs_id":"<id>","disposition":"accept","todo_ref":"<path>"}' \
  | python3 ~/.claude/skills/plan-followups-review/run.py record
```

`run.py` REJECTS an `accept` with no `todo_ref` — so a recorded accept proves a framed TODO exists. There is no path to a silent TODO. If `/work-frame-and-create-todo` returns `EXHAUSTED` (framing failed the cap), the accept is **not** recordable — surface the validator error and re-ask Step 5 (accept-retry / edit / discard); never silently skip the observation.

### Closeout

When `next` reports `DONE`, call `run.py summary` and present the **Output contract** below to the operator (and return it to the calling orchestrator).

---

## Output contract

The skill's result is the `run.py summary` payload (`status: SUMMARY`):

- `counts` — `{accept, edit, discard}` tallies.
- `dispositions` — one row per observation: `{id, title, disposition, todo_ref, note}`.

Report to the operator a short "Accepted / Edited / Discarded" summary, with the framed TODO path for each accept. The same payload is the value the orchestrator (S9) reads back. Follow the project's Concerns (0–3) convention when reporting.

---

## Constraints

- **No re-verification.** Inputs arrive pre-verified from the caller's upstream gate. This skill spawns no fact-check over them. (`~/.claude/rules/code_first_architecture.md:112` producer-never-verifies — held by topology.)
- **No silent creation.** A TODO appears only on explicit accept, only through `/work-frame-and-create-todo`. `run.py` makes this machine-checkable: `record accept` requires a real `todo_ref`.
- **No batch multi-select.** Exactly one observation is handed out (`run.py next`) and decided (`AskUserQuestion`) at a time.
- **Reuse, never reinvent.** `/recommend` and `/work-frame-and-create-todo` are invoked whole; their validators and isolated-checker discipline are inherited.
- **Code owns the loop.** `run.py` owns one-at-a-time, complete-coverage, the no-silent-creation invariant, and crash-safe resume. Even if this file's text drifts, those invariants hold in code. (Trust Hierarchy.)
- **Crash-safe resume.** State persists per session; resuming continues at the next undisposed observation and never re-asks a disposed one. Pass `SESSION_ID` so resume survives `/clear`.
- **Main-session-only.** Refuse recursive invocation from inside a subagent.
- **Thin elicitation surface.** This file contains no triage/verification logic of its own — it elicits judgment and renders the shim's state.

---

## Edge Cases

| # | Case | Behavior |
|---|------|----------|
| E1 | Empty observation list | `init` OK with total 0; first `next` returns `DONE`; `summary` returns an empty `SUMMARY`. No error. |
| E2 | Single observation | One-at-a-time still holds — one `next`, one decision, one closeout. |
| E3 | `/work-frame-and-create-todo` returns `EXHAUSTED` on accept | The accept is not recordable (no `todo_ref`). Surface the validator error; re-ask Step 5. Never silently skip. |
| E4 | Crash mid-`/recommend` (before record) | Disposition not yet written → resume re-hands the same observation; the recommendation re-runs. |
| E5 | `/clear` between observations | State file persists; resume continues at the next undisposed observation. |
| E6 | Malformed observation record | `run.py init` rejects on schema validation (exit 3) before any walk begins. |
| E7 | Operator picks Edit | Record `edit` (no `todo_ref` required); optionally re-loop Steps 3–4 on the revised observation. |
| E8 | Re-`init` mid-walk (same session) | Idempotent — `run.py` resumes and preserves recorded dispositions; never clobbers. |
| E9 | Invoked from inside a subagent | Refuse. Main-session-only. |

---

## See also

- `~/.claude/skills/plan-followups-review/run.py` — the walk-state machine (loop, ledger, invariants, resume).
- `~/.claude/rules/plan-gates.md` — "Skill-internal locked step contracts" carve-out that sanctions the per-observation contract.
- `~/.claude/rules/code_first_architecture.md` — code-first floor; producer-never-verifies; "automated regression suites defend those boundaries".
- `~/.claude/skills/recommend/SKILL.md` — invoked whole at Step 3 (kept items).
- `~/.claude/skills/work-frame-and-create-todo/SKILL.md` — invoked whole at Step 6 (accept → framed TODO).
- `~/.claude/skills/ninja-fix/SKILL.md` — sibling locked-step-contract skill (the authoring precedent).
- `~/.claude/skills/double-check/SKILL.md` — numeric-arg convention (`sonnet,opus,rounds`).
- `Thoughts/automate-plan-execution_THOUGHT.md` — spine; Solution Alternative 1 (commitment 5, U5, A18) is the locked design this slice ships.

---

*Created: 2026-06-22 — v1 (slice S1 of automate-plan-execution). Code-enforced walk-state machine (`run.py`) + locked per-observation step contract. SKILL.md owns elicitation only.*

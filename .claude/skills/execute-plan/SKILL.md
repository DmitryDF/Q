---
name: execute-plan
description: Plan-execution orchestrator (v2) — walks a locked _PLAN slice register across session boundaries via a code-derived typed cross-session handoff (continue-the-run / plan-this-slice / implement-this-slice) carried on active-run.json's pending_handoff and routed through the live execplan-session-ack gate. The v2 cross-session dispatch loop is the single dispatch entry point; the hands-off arm reuses the in-session spawn + two-layer-verify + commit path. Main-session use only.
allowed-tools: Bash, Read, Agent
---

# /execute-plan — Plan-Execution Orchestrator (v2 — cross-session typed handoff)

Carry a locked master plan's slice register end-to-end — **across session
boundaries** — so the operator stops typing orchestration commands between
slices (`/work-start` → implement → `/work-done` → `/prompt-for-handoff` → copy
→ paste → repeat) *and* between sessions. The **v2 cross-session dispatch loop**
(below) is the **single dispatch entry point**: it computes a code-derived typed
handoff per ready slice, arms it on `active-run.json`, and routes the operator's
next move through the live `execplan-session-ack` gate so a fresh session resumes
the walk from one paste. The in-session machinery (spawn, two-layer verify, git
commit, mode + close-out gates — S2–S10 below) is **reused** as the hands-off
arm; it is not a second, parallel entry point.

Design contract: spine `### Solution Alternative 1` (A1–A21, U1–U8) in
`Thoughts/automate-plan-execution_THOUGHT.md`; code-first hexagonal per
`~/.claude/rules/code_first_architecture.md`.

## What this is (architecture)

The code spine lives in `run.py` — a thin, code-owned dispatch loop behind two
output ports:

- **`BookkeepingPort`** — slice lifecycle persistence (real `Full` / `Minimal`
  adapters, S3).
- **`SpawnPort`** — agent execution. The **real** spawn cannot be a Python
  adapter (Python cannot invoke the harness `Agent` tool), so the production
  spawn is driven from THIS skill — see *Spawn boundary*.

`run.py` owns the deterministic decisions (compute / route / gate / render) and
holds no I/O beyond stdin/stdout, no git, and no `Agent` call. The four
Python-uncrossable surfaces — the `Agent` spawn, `AskUserQuestion`, `git push`,
and `/plan` mode — are driven from this skill. The cross-session handoff itself
(`compute-handoff` → `set-active-run {pending_handoff}` → `resume`) is code in
`run.py`; this skill only invokes those CLIs and surfaces the human gates.

## Trigger

```
/execute-plan                 # enter the v2 cross-session dispatch loop over the active topic's locked slice register
/execute-plan --help
```

`/execute-plan` walks the locked register across session boundaries via the
typed cross-session handoff (below), dispatching each ready slice by its
`dispatch` axis. It requires a locked `_PLAN` carrying a B2 `#### Slices`
register (the engine's input contract). The operator types it once to START a
run; a fresh session resumes an in-flight run by **invoking `/execute-plan`
again** — that invocation is where the resume-scoped `execplan-session-ack` gate
fires (it is NOT armed at SessionStart, so an unrelated or parallel session is
never gated).

## v2 cross-session dispatch loop — the single entry point

Typing `/execute-plan` enters THIS loop. It is the **only** path that dispatches
a slice — there is no parallel v1 within-session path. The loop **reuses** every
in-session seam below (spawn, two-layer verify, git contract, mode + close-out)
as the **hands-off arm**; what v2 adds is the code-derived typed handoff that
carries the walk over session boundaries. The handoff is computed by `run.py`,
never judged by the AI at dispatch (`code does, AI thinks, code checks`).

**Runner init — fail-loud on a malformed multi-step plan (A3, the 4th warning surface).**
Before the first `compute-handoff`, run `register-presence` on the plan surface. If it
returns `present: false` BUT `raw_non_closing_count` ≥ 2 (or `pointer_broken: true`) —
a plan that *looks* multi-step yet whose register did not resolve to ≥2 valid slices —
surface a prominent warning naming the plan file (its register is malformed: a missing
`Type`/`Depends on` column, or a broken `slice_register_ref`), then stop: there is no
walkable register, so a manual `/execute-plan` on it must NOT silently no-op. Fix the
register and re-run. A `present: true` plan proceeds into the loop; a genuinely
single-work plan (`present: false`, `raw_non_closing_count` < 2) is out of scope for
`/execute-plan` and is reported as such (no walk, no spurious warning — C6).

```bash
printf '%s' '{"spine_path":"<PLAN_FILE>"}' \
  | python3 ~/.claude/skills/execute-plan/run.py register-presence
```

Per ready slice (in `next_ready_slice` DAG order):

1. **Compute the typed handoff (code-derived).**
   `run.py compute-handoff {register_markdown|spine_path, slice_execution?}`
   returns a `HandoffRecord` for the next ready slice: a `type`
   (`continue-the-run` / `plan-this-slice` / `implement-this-slice`, derived from
   the slice `type` + on-disk `_PLAN` existence) and a `dispatch`
   (`hands-off` / `attended` / `out-of-session`, read verbatim from the slice).
   A `null` result ⇒ the run is complete (or deadlocked — the loop owns that
   distinction).

2. **Arm it on the per-run pointer.**
   `run.py set-active-run {owner_session_id, surface_path, total_slices,
   worktree_root?, state_path?, pending_handoff: <HandoffRecord>}` writes the
   handoff onto the **per-run** pointer `run-<run_id>.json` (run_id =
   `sha256(surface_path [+ worktree_root])[:12]`), marking the run resumable and
   seeding the fresh-session read. Per-run scoping means concurrent runs never
   clobber each other, and a run in one worktree never gates a session in another
   (git-policy.md §3). `state_path` is the JSON slice_execution surface used by
   the self-heal; `worktree_root` is `git rev-parse --show-toplevel` for the
   session, when in a worktree.

   **Worktree auto-placement (S7 A14, `--worktree`-opt-in).** For a harness
   worktree topic, place the session into the topic's worktree BEFORE this arming
   step, so the walk runs inside the isolated workspace and `worktree_root` resolves
   to it. Same two-step form as `work-start/SKILL.md:255-259` (retrieve the path
   from `${KIT_HOOKS_DIR}/worktree-helper.sh place --repo "$(the configured source path)"
   --topic "<slug>"`, then a guarded `cd`); `place` is idempotent (a resume returns
   the existing worktree). Without `--worktree` the run stays in the current
   checkout, exactly as today. Projects-side binding activates with S9.

3. **Cross the session boundary through the resume-scoped gate.** A fresh session
   resumes by **invoking `/execute-plan`** — the PreToolUse `check-execplan-
   session-ack.sh` gate (matcher `Skill`) fires on exactly that invocation: if a
   per-run pointer exists for a run in this session's worktree that this session
   neither owns nor has acked, it blocks the invocation and asks the operator to
   pick **(a) Investigate / (b) Proceed / (c) Not now** — the prompt names the run
   (slug + slice_id + dispatch, A7). Investigate renders `run.py session-summary`.
   `clear-execplan-session-ack.sh` (PostToolUse `AskUserQuestion`) records the ack
   so the re-invocation on **Proceed** passes; **Not now** leaves the run parked
   and touches nothing. No SessionStart arm, no PreToolUse-`.*` block — an
   unrelated session (which never invokes `/execute-plan`) is never gated.

4. **Read the handoff back + route.**
   `run.py resume {pointer_dir?}` reads `pending_handoff` and returns
   `{action, type, dispatch, slice_id, next}`. Route on `next`:

   | `next` | Arm | Status |
   |--------|-----|--------|
   | `dispatch-hands-off` | run the slice hands-off via the **existing** *Spawn boundary* → *Two-layer grading order* → `commit-slice` machinery (below) | wired |
   | `cross-session-boundary` | arm the handoff (step 2) + hand the operator a one-paste resume for a fresh session | wired |
   | `plan-detour` | attended `/plan` detour, then resume — see *Plan-detour arm* | wired |
   | `dispatch-attended` | operator runs the slice in-session — see *Attended arm* | wired |
   | `dispatch-out-of-session` | render the out-of-session copy-paste block — see *Out-of-session arm* | wired |

   At an **attended** or **out-of-session** cross-session resume — and only there —
   reconcile the register against the shipped code BEFORE emitting the next-step
   handoff (see *Code-face reconcile*); the hands-off path is unaffected.

   The router is gate-aware: a `hands-off` slice with no automatable verification
   gate at dispatch is promoted to `dispatch-attended`
   (`render_resume(has_verification_gate=False)` — AI-promotes-only, monotonic;
   never demotes a flagged slice).

5. **Hands-off dispatch (the reused in-session arm).** For `dispatch-hands-off`,
   run the slice exactly as the *Spawn boundary* section prescribes (resolve the
   model family in code → `Agent` spawn pinned to that family → `verify-model` →
   code-layer `check-code` → `commit-slice` → model-layer `check-conformance`),
   then loop to step 1 for the next ready slice. No reimplementation — this arm
   IS the in-session machinery documented below.

**All arms wired.** The hands-off + cross-session-boundary spine was proven first
by a real two-session run (*Spine validation* below); the remaining arms
(plan-detour, attended, out-of-session, code-face reconcile, P-commits, resume
prose, exception surface, OMTM) then attach at their already-shipped CLI seams —
each documented in *v2 dispatch arms* below. The router is the single hub: every
arm is a `next`-value `render_resume` already emits, so no arm forks a second
dispatch path.

### Spine validation (the gate that proved the spine)

The spine was proven by ONE real two-session `/execute-plan` run watched across a
boundary — NOT a test suite (this driver is un-unit-testable judgment text). On a
small locked all-hands-off register: session A dispatched a hands-off slice, armed
`pending_handoff`, and reached the `execplan-session-ack` boundary; a **fresh**
session B resumed from one paste (`run.py resume`) and finished the walk. This
gated the remaining arms — only after it confirmed the crossing were they wired.
A closing multi-arm run (*Closing verification* near the end) proves the assembled
whole. Rollback if a future change breaks the spine:
`git checkout main -- skills/execute-plan/SKILL.md`.

## v2 dispatch arms

Each arm handles one `next`-value the router emits. All hang off the same router;
none forks a second dispatch path. `run.py` owns the deterministic pieces; the
`Agent` spawn / `AskUserQuestion` / `git push` / `/plan` mode surfaces stay
SKILL-driven (Python cannot invoke them).

### Plan-detour arm (`next = plan-detour`)

A `plan-this-slice` handoff (a plan-needing slice with no `_PLAN` on disk yet)
routes here. Planning stays human-gated; **one plan mode per session**.

1. Enter a fresh attended `/plan` for the slice (its own Diagnosis → ExitPlanMode).
2. On approval the existing `post-plan-uxgate` gate fires its own (a)/(b)/(c) —
   **COMPOSE with it, never bypass**. Consume the operator's **(a) generate
   handoff** choice and follow the ordered descriptor from
   `run.py plan-detour-return {slice_id, plan_basename}`:
   - **file-plan** — SKILL writes both surfaces (`link-plan-to-thought` +
     `write_slice_row`);
   - **verify-plan-filed** — `run.py verify-plan-filed {spine_path, slice_id,
     plan_basename}` re-reads BOTH surfaces (spine wikilink + register row);
     exit 9 if the plan did not land on both;
   - **flip** — the next `compute-handoff` sees the `_PLAN` on disk →
     `implement-this-slice`;
   - **mark-plan-session-exhausted** — `run.py mark-plan-exhausted`;
   - **arm-fresh-session-handoff** — the `implement-this-slice` handoff for the
     next session.
3. This session cannot plan a second slice: a further `plan-this-slice` while
   `plan_session_exhausted` routes to `cross-session-boundary`
   (`render_resume(plan_session_exhausted=True)`) — a fresh session plans it.

### P-commit arm (the plan-filing commit)

The filed plan is committed under the **same** unified git contract with a
planning-commit tag, path-scoped, sequenced before `/close`:
`run.py commit-detour {commit_id: "P<N>", paths: [<plan paths>], commit_summary}`
→ a `P<N>: <summary>` commit over ONLY the plan paths (exit 8 on contract error).
`git push` stays operator-confirmed.

### Attended arm (`next = dispatch-attended`)

The operator runs the slice **in-session** (no `Agent` subagent) — used when the
slice has no automatable verification gate at dispatch (e.g. an untestable
markdown edit) or when the router promoted a declared-hands-off slice
(`has_verification_gate=False`). The two-layer conformance gate still runs before
`mark_completed`: code-layer `check-code`, then model-layer `check-conformance`.
Decided=decided — no mid-plan mode re-prompt.

### Out-of-session arm (`next = dispatch-out-of-session`)

A verifier-isolation slice runs in a SEPARATE terminal OUTSIDE the producer's
process tree. Render the copy-paste block (`render_out_of_session_block`, returned
in the resume context) with the hard **migrate-before-register** ordering, surface
it as the operator's next step, and **WAIT for the paste-back ack** — the
orchestrator never executes it inline. Resume the walk only after the ack.

### Code-face reconcile (attended / out-of-session resume only)

At an attended or out-of-session cross-session resume — and ONLY there
(`RECONCILE_DISPATCH_MODES`) — BEFORE emitting the next-step handoff, reconcile
the register's claimed status against the shipped code:
`run.py reconcile-code-face {claimed: <slice_execution>, specs_by_slice}` surfaces
register-vs-code drift (a register-`completed` slice the code shows is partial; a
status over-claim). A fresh code-face check, NOT a status summary. **Never inside
the dispatch loop**; the hands-off path is unaffected (its synchronous per-slice
conformance already covers it). On drift, surface it to the operator before
continuing.

### Resume prose (the lean re-seed)

When composing the resume prompt for a fresh session, re-seed with durable state +
a lean prompt only — never a finished agent's transcript:

1. `run.py resume-context {handoff, session_summary?, deferred_captures?}` → the
   composer input bundle (handoff + slice-register status + ALL deferred-this-run
   captures).
2. `run.py deferred-captures {dispatched|slice_execution}` → enumerate deferred
   items (origin-slice-id) + the completeness check against the run's actual
   deferred set.
3. `run.py recompose-check {current_type}` reads the previously-recorded type
   off the pointer and FORCES a recompose on a handoff-type change (the type-blind
   `/prompt-for-handoff` fingerprint cannot see a same-slice `plan-this-slice →
   implement-this-slice` transition); after composing, stamp the pointer for the
   next comparison with `run.py record-composed-type {composed_handoff_type}`.
4. Reuse the `/prompt-for-handoff` machinery to compose, freshness-gate,
   independently verify, and write the prompt — do not reimplement it.

### Exception surface

On any exception (escalation / deadlock / conformance-fail / drift / abort /
model-abort): `run.py exception-surface {exception_type, slice_id, last_committed?,
dispatched?}` → a structured block with a PROMINENT default option first + the
non-default options. Surface it as an `AskUserQuestion` and **record** the
operator's choice — captured-not-gated, never inferred for flow. The secondary
metric (`run.py engagement-rate`) consumes the recorded choices at close-out.

### OMTM (close-out)

At close-out, alongside the S9 report / diagnostics / harvest: `run.py omtm {...}`
→ the OMTM (hands-off minutes per implementation slice; plan-needing + attended
`/plan`-detour slices excluded from both numerator and denominator) plus per-slice
timestamps + the `attended` flag. Surface as an informational read-out;
captured-not-gated, never a blocker.

## Dispatch contract

`run.py` (stdin JSON → structured JSON + exit codes; mirrors
`plan-followups-review/run.py`):

| Subcommand | Input | Output | Exit |
|------------|-------|--------|------|
| `translate` | `{"agent_choice": "routine"\|"more_capable"}` | `{"model_family": ...}` | 0 ok / 3 unknown |
| `plan-slices` | `{"register_markdown"}` or `{"spine_path"}` | `{"slices": [...with model_family, depends_on]}` | 0 / 3 |
| `dry-run` | `{"register_markdown"}` or `{"spine_path"}` | `{"dispatched", "summary", "spawn_calls", "bookkeeping"}` | 0 / 2 deadlock / 3 |
| `check-code` | `{"verify_specs": [{surface_kind, path, expected_payload, locator}, ...]}` | `{"status", "results"}` | 0 all-pass / 5 any-fail / 3 usage |
| `check-conformance` | `{"verdict": "..."}` or `{"verdict_path": "..."}` | `{"status", "verdict", "source"}` | 0 PASS / 6 non-PASS / 3 usage |
| `commit-slice` | `{"slice_id","repo_dir"?,"commit_summary"?}` | `{"status","slice_id","subject","branch","sha"}` | 0 committed / 8 contract-error / 3 usage |
| `push-gate` | `{"all_slices_done","work_done_succeeded","git_status_clean","aborted"?}` | `{"status","should_push","reason","partial_prompt"}` | 0 should_push / 7 withheld / 3 usage |
| `mode` | `{"mode": "observer"\|"confirm"\|"auto"?}` | `{"mode","default"}` (blank→observer) | 0 ok / 3 unknown |
| `confirm-gate` | `{"id","name"?,"type"?,"agent_choice"?,"confirm_override"?,"mode","layer1"?}` | `{"needs_confirm","mode","prompt"}` | 0 ok / 3 usage |
| `escalation-surface` | `{"slice_id","attempts","mode","notify_recipient"?,"dispatched"?}` | `{"in_band","notify","retry_count","completed_so_far"}` | 0 ok / 3 usage |
| `session-summary` | `{"slice_execution"}` or `{"state_path"}` | `{"completed","in_flight","escalated","incomplete_remaining","summary_line"}` | 0 ok / 3 usage |
| `set-active-run` | `{"owner_session_id","surface_path","surface_kind"?,"total_slices"?,"worktree_root"?,"state_path"?,"pending_handoff"?}` | `{"pointer","run_id",...}` | 0 ok / 3 usage |
| `clear-active-run` | `{"run_id"?}` or `{"surface_path"[,"worktree_root"]}` `[,"owner_session_id"]` `[,"confirm_non_owner"]` | `{"cleared","run_id","pointer","backup"?,"reason"?,"remediation"?}` | 0 ok |
| `execplan-gate-check` | `{"session_id","worktree_root"?,"now_epoch"?}` | `{"block","run_id","run_ids","slug","slice_id","dispatch","in_flight","listed","armed_count","message"}` | 0 ok / 3 usage |
| `execplan-ack` | `{"session_id"}` | `{"acked": <run_id\|null>, "acked_run_ids": [...]}` | 0 ok / 3 usage |
| `execplan-reap` | `{"now_epoch"?}` | `{"reaped":[...],"count"}` | 0 ok |
| `report` | `{"slice_execution"}` or `{"state_path"}` | `{"rows","markdown","count"}` (Assigned/Aborts/Used Model/Status per slice) | 0 ok / 3 usage |
| `diagnostics` | `{"slice_execution"}` or `{"state_path"}` + `{"restarts"}?` | `{"tokens_per_family","model_abort_rate","conformance_failure_rate","captured","gated":false,...}` | 0 ok (captured-not-gated) / 3 usage |
| `harvest-observations` | `{"dispatched"}` or `{"slice_execution"}` or `{"state_path"}` | `{"observations":[{id,title,body,source_ref}],"count"}` | 0 ok / 3 usage |

Exit codes: 0 OK · 2 DEADLOCK · 3 usage/input error · 4 model-pin mismatch (verify-model) · 5 code-layer verify_write FAIL (check-code) · 6 conformance non-PASS (check-conformance) · 7 push WITHHELD (push-gate) · 8 git-contract error (commit-slice)

The loop (`run_dispatch_loop`) walks the DAG **synchronously, one slice in
flight**: `mark_started` → resolve model family → `spawn` (awaited inline) →
`mark_completed` → next ready slice. `next_ready_slice` returns the first
register-order slice whose `depends_on` are all completed and never a blocked
slice. If slices remain but none are ready (cycle / unsatisfiable depends_on),
the loop raises `DeadlockError` (exit 2) rather than hanging.

The register input is the spine's B2 `#### Slices` pipe-table (the read-only
contract — `depends_on`, `agent_choice`); `run.py` consumes it verbatim and
never re-derives the DAG (A1: plan-as-contract).

## Model-family translation

`agent_choice_to_model_family` is a pure, deterministic function:
`routine → sonnet`, `more_capable → opus`. The target slugs are exactly the keys
of the model-family map pinned at `${KIT_HOOKS_DIR}/_factcheck_engine.py:379-383`
(spine A8) — reused, never reinvented. An unrecognized `agent_choice` raises
`ValueError` (no silent default — a wrong-model run must never start).

## Spawn boundary

The one boundary code cannot exercise from Python is the real `Agent` spawn.
The production spawn is therefore driven from this skill:

1. Resolve the slice's model family in code: `run.py translate` (or read it
   from `run.py plan-slices`). Never guess the model.
2. Invoke the `Agent` tool with `subagent_type: general-purpose` (a **writing**
   agent — the implementer must create/edit the slice's files; `Explore` is
   read-only and cannot) and an **explicit `model:` pin** equal to that resolved
   family. **Pass the prompt produced by `run.py` verbatim** — it carries a
   machine-readable `[MODEL:<family>]` tag (appended by `run.stamp_model`) that
   the runtime backstop reads for a per-spawn check. `${KIT_HOOKS_DIR}/check-impl-models.sh`
   (PostToolUse on `Agent`) now scopes enforcement to implementation spawns only:
   it **skips** verification (`Explore`) and session-close (`close-*`) subagents
   via a data-driven skip-list, **fails open with a warning** when the transcript
   has not flushed yet (a timing lag, not a violation), and — when a
   `[MODEL:<family>]` (or `[ACTION:An]`) tag is present — verifies the spawn's
   transcript family against **that spawn's own intended model** rather than the
   whole-plan union (union-with-warn remains the untagged fallback). It blocks
   (exit 2) only on a genuine family mismatch.
3. The production spawn implements the slice and returns a result dict carrying
   the transcript locator (`session_id` + `agent_id`, or `transcript_path`) for
   the `verify-model` + conformance checks, plus the per-slice `commit_summary`,
   `observations`, and any `deferred` captures the close-out consumes. The
   commit lands only after `verify-model` (S5) + the two-layer verify (S6) pass.

This per-spawn explicit-model discipline mirrors
`~/.claude/skills/re-fc-extraction/SKILL.md:26` ("never rely on subagent
default model").

**Spawn-result locator contract (S5 model-pin).** Because the S5 model-pin
check reads the spawned agent's *actual* model from the harness-written
transcript, the production spawn must surface a transcript locator in its result
dict: either `session_id` + `agent_id` (the orchestrator's session id plus the
`Agent` tool's returned agent id) or an explicit `transcript_path`. The
`pre_commit_verify` callable built by `make_model_pin_verify` extracts that
locator via `_spawn_result_locator` and reads
`~/.claude/projects/<session_id>/subagents/agent-<agent_id>.jsonl`. The check is
**fail-closed**: a result with no locator (or a transcript that is missing,
empty, mixed-family, or unreadable) raises `SliceAttemptError` — no commit lands.

## In-session machinery (S2–S10, the reused hands-off arm)

Each in-session capability attaches at a named seam without touching the port or
the translation (Evolution Test). The v2 hands-off arm reuses all of them:

| Concern | Slice | Seam |
|---------|-------|------|
| Real persistence (Full/Minimal adapters) | S3 ✓ DONE | `BookkeepingPort` impls |
| Crash-safe 3-checkpoint state + idempotent commit guard + persisted retry + resume | S4 ✓ DONE | `BookkeepingPort` lifecycle (`mark_committed`/`record_attempt`/`mark_escalated`) + `next_ready_slice` resume reconciliation + `run_dispatch_loop` retry-2x-then-escalate; `CommitGuardPort`/`GitCommitGuard` (read-only git log/grep) + `CommitPort`/`FakeCommitAdapter` stand-in |
| Post-dispatch transcript model-pin verification (hard-abort before commit) | S5 ✓ DONE | `ModelPinPort`/`TranscriptModelPinAdapter` (reads the harness transcript's `.message.model`, replicates `_factcheck_engine.py:50-173`, never imports it) + `FakeModelPinAdapter`; `make_model_pin_verify(port)` fills the `pre_commit_verify` seam in `run_dispatch_loop` (between `spawn` and the `committed` checkpoint) — a family mismatch OR an unverifiable transcript raises `SliceAttemptError` (fail-closed hard-abort, routed into the 2-budget). CLI: `verify-model` (exit 0 match / 4 mismatch-or-unverifiable / 3 usage) |
| Two-layer conformance (`/double-check --class gate`) + `verify_write` | S6 ✓ DONE | Fills BOTH seams: (1) NEW `pre_commit_codecheck` seam (code-layer: `_verify_write` replica + injectable `code_check`, BEFORE the commit) and (2) the EXISTING `post_commit_conformance` seam (model-layer: `ConformancePort`/`DoubleCheckConformanceAdapter`/`FakeConformanceAdapter` + `make_conformance_check`, between `mark_committed` and `mark_completed`). Both mandatory before `mark_completed`; grading order: code-layer first (deterministic, pre-commit), model-layer second (judgment, post-commit). See *Two-layer grading order* below. |
| Slice_id-prefixed git commit + unified git contract + end-of-plan push gate | S7 ✓ DONE | Adds the `UnifiedGitContract` (pure policy ABOVE the adapter layer: commit format `<slice_id>: <summary>` — the same `<slice_id>:` prefix `GitCommitGuard` greps; protected-branch policy; `evaluate_push_gate`) + a real `GitCommitAdapter(CommitPort)` that OBEYS the contract (mirrors `GitCommitGuard`'s injectable-runner shape; replaces the `FakeCommitAdapter` at the loop's existing `commit=` parameter — `_run_one_slice`/`run_dispatch_loop` unchanged; a protected-branch / git failure raises `GitContractError`, which is NOT a `SliceAttemptError`, so it hard-fails and is never retried) + a `PushPort`/`GitPushAdapter`/`FakePushAdapter` (fail-closed: pushes only when the `PushDecision` allows). CLI: `commit-slice` (exit 0 / 8 `GitContractError` / 3 usage) + `push-gate` (exit 0 should_push / 7 withheld / 3 usage). See *Unified git contract + push gate* below. |
| Session-boundary + ExitPlanMode + Hybrid mode + Confirm timing + escalation | S8 ✓ DONE | Adds the session/mode UX layer as PURE code + a hook gate: `EXECUTION_MODES`/`normalize_mode` (Observer default, U1/U7); `Slice.confirm_override` (additive 7th register column) + `layer1_risky` + AI-promotes-only `slice_needs_confirm` (U3); pure renderers `render_confirm_prompt` (U4) / `render_escalation_surface` + `should_notify_on_escalation` (U8, persisted `attempts`) / `render_session_summary` (U2/U6); 6 CLIs (`mode`/`confirm-gate`/`escalation-surface`/`session-summary`/`set-active-run`/`clear-active-run`); and the `execplan-session-ack` hook triad (originally: SessionStart arm / PreToolUse `.*` blocker / PostToolUse AskUserQuestion clear) reusing the post-plan gate pattern (A9), registered in `settings.json`. **[SUPERSEDED 2026-07-10 by execplan-resume-gate-fix]** — that SessionStart-arm + PreToolUse-`.*` triad collaterally blocked unrelated/parallel sessions and is retired; the checkpoint now fires resume-scoped on the `/execute-plan` invocation (PreToolUse matcher `Skill`) against per-run pointers. See the U2 section + the v2 dispatch-loop step 3 above. `AskUserQuestion`/`PushNotification` stay SKILL-driven (Python cannot invoke them). See *Session/mode UX (S8)* below. |
| Observation harvest via `/plan-followups-review` + per-slice report + diagnostics | S9 ✓ DONE | Adds three PURE end-of-plan consumers of already-persisted state + 3 read-only CLIs: `render_implementation_report` (A16 — Assigned/Aborts/Used Model/Status per slice; `used_model` for a completed/committed slice is the assigned family, PROVABLE by the S5 fail-closed gate) + `report` CLI; `render_diagnostics` (A17 — tokens-per-family / restart-count / model-abort-rate / conformance-failure-rate / routine-intervention engagement; CAPTURED, NOT gated — never raises, always exit 0) + `diagnostics` CLI; `collect_observations` (U5 — per-slice verified observations → the `/plan-followups-review` `{id,title,body,source_ref}` input contract) + `harvest-observations` CLI. The `/double-check` verify + the one-at-a-time `/plan-followups-review` walk + `AskUserQuestion` stay SKILL-driven. No loop/port/seam/lifecycle change (Evolution Test). See *End-of-plan close-out (S9)* below. |
| End-to-end verification against the desired outcome | S10 ✓ DONE | `test_run.py` S10 section: ONE run of the fully-wired REAL-adapter pipeline (`_run_e2e`) over a representative ~7-slice register (`E2E_REGISTER_MD`) against an isolated temp git repo, asserting the locked Desired Outcome claims C1–C6 — dependency-order walk + per-slice real model-pin + two-layer verify + real `<slice_id>:` commit (C1/C2), wrong-model hard-abort before commit (C2), exact confirm-trigger set + resume summary (C3/C5), close-out report/diagnostics/harvest + push gate over the REAL persisted `slice_execution` (C4). Producer-never-verifies: an isolated Opus-pinned checker confirmed the harness fakes ONLY `SpawnPort` + the SKILL-driven surfaces. See *End-to-end verification (S10)* below. |

## Two-layer grading order (S6)

Per `code_first_architecture.md:121-124` — code-layer first (deterministic),
model-layer second (judgment), both mandatory before `mark_completed`.

**Code-layer (before the commit, `pre_commit_codecheck` seam):**
SKILL.md drives the slice's agent; after the agent returns, the skill runs the
slice's tests/lints via Bash and calls `run.py check-code` with the expected
write specs (surface_kind / path / expected_payload / locator per the slice's
design). A failure here raises `SliceAttemptError` BEFORE any commit lands.

**Model-layer (after `mark_committed`, `post_commit_conformance` seam):**
SKILL.md runs `/double-check --class gate --rounds 1` AFTER the
commit and writes the verdict into the spawn result as
`conformance_verdict` (bare string) or `conformance_verdict_path` (path to a
file containing the verdict as a bare token or JSON `{"verdict": "..."}` ).
`make_conformance_check(port)` reads that locator and raises `SliceAttemptError`
on any non-PASS verdict — **fail-closed** (DIRTY / ESCALATE / unparseable →
abort before `mark_completed`). If neither key is present in the result dict,
the adapter returns `ok=False` and the check aborts (fail-closed if absent).

**Verdict vocabulary:**
- `PASS` — all 3 checkers agree; `ok=True`; slice proceeds to `mark_completed`.
- `ESCALATE` — budget exhausted, non-convergent (live `/double-check` final verdict); `ok=False`.
- `DIRTY` — convergence-engine label (`factcheck-convergence.md §4`); used in fakes/tests; `ok=False`.
- `DISCREPANCY` — per-checker intermediate; recognized and fail-closed for defence.
- Anything else → fail-closed (`ok=False`, error set).

**Conformance-verdict locator contract (spawn result keys):**
```
{
  ...
  "conformance_verdict": "PASS",          # inline verdict token (preferred)
  # OR:
  "conformance_verdict_path": "/path/to/verdict.txt"  # path to verdict file
}
```
The file may contain a bare verdict token (`PASS\n`) or a JSON object
(`{"verdict": "PASS", ...}`). Absent → fail-closed.

## Unified git contract + push gate (S7)

Git commit format, branch policy, and push gating live in ONE `UnifiedGitContract`
ABOVE the adapter layer (spine A13/Q6) — the same rules the orchestrator,
`/close`, and every commit-producing skill follow (skills replicate it, never
import — dependency direction). `BookkeepingPort` adapters own internal state
only; they never touch git.

**Commit format (single source).** `UnifiedGitContract.format_commit_subject(slice_id, summary)`
produces `<slice_id>: <summary>`. The `<slice_id>:` prefix is the SAME string
`GitCommitGuard` greps (`^<slice_id>:`), so the format the commit adapter writes
and the prefix the idempotency guard reads derive from ONE place — a resume/retry
never double-commits, by construction. A malformed slice id or empty summary is a
`GitContractError` (no silent default).

**Real commit (`GitCommitAdapter`).** Behind the existing `CommitPort`, it resolves
the current branch, refuses a protected/default branch (`GitContractError` — a HARD
error, NOT a `SliceAttemptError`, so it is never absorbed into the retry budget;
branch *management* stays operator-owned, A20), formats the subject via the contract,
`git add -A` + `git commit -m <subject>`, and returns `{status, slice_id, subject,
branch, sha}`. It plugs into the loop's existing `commit=` parameter (the
`FakeCommitAdapter` stays for tests); `_run_one_slice`/`run_dispatch_loop` are
unchanged. The adapter stages + commits ONLY — it never pushes. SKILL.md drives the
real commit via `run.py commit-slice` (the slice's `commit_summary` flows in through
the spawn result; `run.py` defaults it deterministically if absent).

**End-of-plan push gate (A14).** `UnifiedGitContract.evaluate_push_gate(*,
all_slices_done, work_done_succeeded, git_status_clean, aborted=False)` returns a
`PushDecision` whose `should_push` is True only when ALL three conditions hold; a
mid-plan abort (or any unmet condition) yields `should_push=False` with an
operator-facing push-partial prompt. SKILL.md evaluates the gate via `run.py
push-gate` at end-of-plan.

**Real push is operator-confirmed.** `git push` writes to a REMOTE (outward,
hard-to-reverse), so — like the `Agent` spawn — the actual push is the SKILL.md
operator-confirmed step, never silent: surface the gate's decision (and the
push-partial prompt when withheld), and only on `should_push=True` AND explicit
operator confirmation run the push. `GitPushAdapter` is fail-closed (a
`should_push=False` decision never pushes) and is proven test-to-real (fake runner);
its production invocation is gated by both the `PushDecision` and operator
confirmation.

## Session/mode UX (S8)

S8 adds the human-decision layer. `run.py` owns ONLY the deterministic decisions +
payload rendering; the `AskUserQuestion` / `PushNotification` surfaces themselves are
SKILL.md-driven (Python cannot invoke them — the same boundary as the real `Agent`
spawn and the operator-confirmed `git push`). The loop topology / ports / seams from
S2–S7 are untouched — the mode/confirm/escalation logic is consumed by SKILL.md AROUND
the production per-slice dispatch, not inside `run_dispatch_loop`.

**U1 — ExitPlanMode entry (single-axis 3-option → mode).** The existing post-plan gate
surfaces (a) Handoff / (b) Close / (c) Continue. On **(c) Continue** for an execute-plan
run, surface a SECOND `AskUserQuestion`: *Mode? Observer (default) / Confirm / Auto*.
`run.py mode` validates the pick (`normalize_mode`: blank→`observer`, unknown→error).

**U7 — Observer is the default.** Automation (Confirm/Auto) is opted INTO. In Observer
mode the orchestrator records progress and issues no transitions — the operator drives
`/work-start` / `/work-done` / `/prompt-for-handoff` themselves.

**U3 — Hybrid mode (AI-promotes-only).** The per-slice confirm decision is the OR of the
run-wide mode + the slicer's per-slice `confirm_override` (an additive 7th register
column, Layer-2 Opus judgment, consumed verbatim) + a deterministic Layer-1 risk net
(`layer1_risky`, regex over the slice name+type). `slice_needs_confirm(s, mode=...)` is
monotonic: `auto`→confirm only risky slices, `confirm`→confirm every slice,
`observer`→none. It can only escalate a slice toward MORE confirmation, never demote one.

**U4 — Confirm timing (per risky slice, after gating, before spawn).** Inside the
production dispatch, after gating is resolved and BEFORE the `Agent` spawn, call
`run.py confirm-gate` for the slice. If `needs_confirm` is true, surface the returned
`prompt` payload as an `AskUserQuestion` (Proceed / Skip / Abort). There is NO mid-plan
mode-change re-prompt — decided=decided; ESC always works.

**U8 — Escalation surface (in-band always + Auto-only opt-in notify).** When the loop
raises `EscalationRequired` (S4/A12, retry-2x-then-escalate), call
`run.py escalation-surface {slice_id, attempts, mode, notify_recipient?}` — `attempts` is
the PERSISTED `slice_execution.attempts` (crash-survived). ALWAYS surface the
`in_band` payload as an `AskUserQuestion` (Retry / Skip / Abort). If
`notify.should_notify` is true (Auto mode AND a configured `escalation.notify_recipient`
only — `should_notify_on_escalation`), ALSO fire a `PushNotification` to the recipient.

**U2 — Resume-scoped acknowledgment gate (code-enforced; execplan-resume-gate-fix,
2026-07-10).** When a run starts (or advances), call `run.py set-active-run
{owner_session_id, surface_path, surface_kind, total_slices, worktree_root?,
state_path?, pending_handoff?}` to write/refresh the **per-run** pointer
`run-<run_id>.json` (run_id = `sha256(surface_path [+ worktree_root])[:12]`); call
`run.py clear-active-run {run_id | surface_path[, worktree_root], owner_session_id}`
at end-of-plan (owner-guarded, run-scoped — never a cross-run wipe). The checkpoint
fires ONLY on the resume action: the PreToolUse `check-execplan-session-ack.sh` gate
(matcher `Skill`) runs on a `/execute-plan` invocation, calls `run.py
execplan-gate-check {session_id, worktree_root}`, and blocks that invocation (exit 2)
iff a run in this session's worktree that it neither owns nor has acked is **genuinely
in flight** — see *Phase split* below. The block message names each gating run with its
walker and last-activity age. The operator picks **(a) Investigate / (b) Proceed / (c) Not now** via
`AskUserQuestion`; the Investigate path renders `run.py session-summary
{state_path: <state_path>}`. `clear-execplan-session-ack.sh` (PostToolUse
AskUserQuestion) calls `run.py execplan-ack {session_id}` so the re-invocation on
Proceed passes; Not now leaves the run parked. Self-heal + staleness: the gate (and
the SessionStart `execplan-gc.sh`, via `run.py execplan-reap`) removes a run pointer
that is provably complete (completed-slice count read from the real `state_path`
JSON ≥ `total_slices`) or older than the 24h TTL — a completed/stale pointer never
gates, a live incomplete in-TTL run is untouched. All hooks honor
`EXECPLAN_ACK_STATE_DIR` (and `EXECPLAN_ACK_TTL_SECONDS`/`EXECPLAN_ACK_TTL_MINUTES`)
for test isolation.

## Phase split — armed vs in-flight (execplan-gate-blast-radius, 2026-08-11)

Both gates distinguish two phases of a run pointer, because a pointer armed at
`/plan` Step 11 asserts a walk that has not begun and may never begin. Reading
`walking_session_id` is what tells them apart, and it is written by code at the
moment the event occurs.

**ARMED** (`walking_session_id` null) — no walk has started.
- *Entry gate*: constrains ONLY `owner_session_id`. Every other session works
  untouched. Holding the owner is deliberate: it routes a multi-slice plan through
  the walker instead of being hand-implemented. **Residual, stated not claimed at
  parity:** `owner_session_id` is AI-composed at `/plan` Step 11, so a wrong value
  means the armed phase constrains nobody — a softer guarantee than the
  whole-checkout block it replaced.
- *Resume gate*: the run is **listed, not acknowledged**. It appears in the inventory
  the operator sees and costs no decision. The listing is not optional — dropping it
  would trade one commitment (stop charging for work nobody is doing) for another
  (never remove a surface on which a parked run is shown).
- A pointer with NO `walking_session_id` key at all (pre-dating these fields) is
  treated as IN-FLIGHT, not armed: absence is unknown, not empty, so it fails closed.

**IN-FLIGHT** (`walking_session_id` set) — a real walk owns the worktree.
- The walker itself is exempt. Others are blocked, scoped to the walked slice's
  declared `write_targets`; an empty declaration means whole-checkout (never
  "nothing"). The intersection reuses `_path_inside`, so a symlink-spelled target
  cannot slip past a real-spelled declaration.
- Where more than one run gates, ONE prompt lists them all and ONE acknowledgement
  clears them (`run_ids` on the pending marker; the scalar `run_id` is kept so a
  half-applied upgrade still acks).

**Liveness is displayed, never acted upon.** Both gates surface walker identity and
last-activity age so the operator decides on visible fact. No code path disarms a run
on an inferred-dead verdict: a false "dead" destroys in-flight work, a false "alive"
costs one pointer the 24h TTL clears anyway.

**Write targets** reach the pointer from the slice register's 7th column, emitted by
`/solution-slicer` (the per-slice `write_targets` field) and rendered by
`/solution-design` Step 8 (the `Write targets` column). Both halves must stay in
step — extending only one leaves the column unrendered and the list empty.
`checkout_slice` refreshes them as it advances `current_slice_id`; a declaration whose
`slice_id` disagrees with `current_slice_id` is discarded in favour of whole-checkout.
`check-plan-gates.sh` warns (never blocks) when a register omits the column.

**Disarming is guarded and reversible.** `clear-active-run` proceeds only when the
caller proves ownership or passes `confirm_non_owner: true`; it MOVES the pointer
aside (`.bak-<epoch>-<pid>`) rather than unlinking it, and reports `backup`. Omitting
`owner_session_id` is the unproven case, not a way past the guard.

## End-of-plan close-out (S9)

S9 adds the close-out the run reaches when the last slice completes. It is three
PURE consumers of state the run already persisted (the `slice_execution` map +
the loop's `dispatched` list) — `run.py` owns ONLY the deterministic rendering +
collection; the `/double-check` verify, the `/plan-followups-review` walk, and
`AskUserQuestion` are SKILL-driven (Python cannot invoke them). The S2–S8 loop
topology / ports / seams / `BookkeepingPort` lifecycle are untouched (Evolution
Test). Run the close-out in this order once the DAG walk has completed (all
slices `completed`, or the run was deliberately stopped):

**1 — Per-slice implementation report (A16).** `run.py report
{slice_execution}` (or `{state_path}` to read the active Full topic-state JSON
or Minimal run-state file). Surface the returned `markdown` table — Assigned /
Aborts / Used Model / Status per slice. `used_model` for a `completed`/`committed`
slice is the assigned family, PROVABLE by the S5 fail-closed model-pin gate (a
wrong-model run aborts before the commit); for an `escalated`/`started` slice it
shows `—`. `aborts`/`used_model` render captured values when the dispatch threaded
them into the spawn `result`, else a provable/`—` fallback — never a fabricated
number.

**2 — Diagnostic correlations (A17 — captured, NOT gated).** `run.py diagnostics
{slice_execution}` `+ {"restarts": N}?`. Surface the correlations
(tokens-per-family, restart-count, model-abort-rate, conformance-failure-rate,
routine-intervention engagement) as **informational** read-out only. This CLI is
always exit 0 on a valid map and `gated` is hard-coded `false` — it never blocks
close-out, and a `captured` block reports how many slices backed each metric so an
un-instrumented run reads as `0`/coverage-0, not a fabricated rate.

**3 — Observation harvest (U5 — one at a time, no silent TODO, no batch).**
   a. `run.py harvest-observations {dispatched}` (or `{slice_execution}` /
      `{state_path}`) collects each slice's verified observations (threaded into
      its spawn `result["observations"]` during the run) into the
      `/plan-followups-review` input contract (`{id, title, body, source_ref}`,
      stable ids `<slice_id>-obs<n>`). END-OF-PLAN ONLY — there is no mid-plan
      propagation (Q5).
   b. **Verify before harvest (producer-never-verifies).** Run `/double-check
      3,0,1` over the collected observations so the list handed to the sub-skill
      is already verified — the harvest itself does NO verification (the
      collector and `/plan-followups-review` both trust their inputs by topology).
   c. **Hand the verified list to `/plan-followups-review` whole** — invoke the
      sub-skill, which owns the one-at-a-time walk: `init` the observation list,
      then loop `next` → (why-line → triage → `/recommend 1,1` → trade-offs →
      accept/edit/discard) → `record`, until `summary`. A follow-up TODO is
      created ONLY on explicit accept, ONLY through the framing gate
      (`/work-frame-and-create-todo`); `record accept` is rejected without a real
      `todo_ref`. No batch multi-select. Read back the sub-skill's disposition
      summary as the close-out result.

**End-of-plan push (A14, S7).** After the close-out, evaluate `run.py push-gate`
(all slices done AND `/work-done` succeeded AND git clean) and, on
`should_push` + operator confirmation, push.

## End-to-end verification (S10)

S10 is the FINAL slice — it proves the **assembled** orchestrator (S2–S9) delivers
the locked Desired Outcome end-to-end, and adds NO new orchestrator feature code
(all of S10 lands in `test_run.py` + this section). The 184 prior tests each
exercise one slice's mechanism in isolation; S10 adds the whole-system proof.

**What the Python harness proves (real-to-real on the Python axis).** The
`test_run.py` S10 section (`E2E_REGISTER_MD` + `_run_e2e`) drives ONE
`run_dispatch_loop` over a representative ~7-slice register with **real adapters
at every Python-reachable seam** — `FullBookkeepingAdapter` (persists the real
`slice_execution`), `GitCommitAdapter` + `GitCommitGuard` against an **isolated
temp git repo** (verification isolation — never a live repo),
`TranscriptModelPinAdapter` (fixture transcripts), `make_code_verify`,
`make_conformance_check` — and **fakes ONLY `SpawnPort`** (the Agent tool is not
callable from Python). It asserts: dependency-order unattended walk + one
`<slice_id>:`-prefixed commit per slice (C1/C2); a wrong-model slice hard-aborts
BEFORE any commit and escalates (C2); the confirm-trigger set is exactly the
risky slice + the resume summary explains a mid-run state (C3/C5); and the
end-of-plan report / diagnostics / harvest + push gate run over the REAL
persisted `slice_execution` (C4).

**Producer-never-verifies (A6).** The harness itself was independently graded by
an isolated, Opus-pinned `Explore` checker (read-only) confirming it uses real
adapters at every Python-reachable seam and its assertions map to C1–C6 — the
producer does not grade its own E2E proof.

**The two Python-uncrossable boundaries (operator-driven, real-to-real).** Two
seams the harness fakes are exercised for real OUTSIDE Python, by the operator:

1. **The real `Agent` spawn → model-pin.** After a slice's real `Agent` spawn
   (`subagent_type: general-purpose`, `model:` = the code-resolved family), confirm
   the slice ran its assigned family by reading the harness-written transcript:
   ```
   echo '{"session_id":"<sid>","agent_id":"<agent-id>","agent_choice":"<routine|more_capable>"}' \
     | python3 ~/.claude/skills/execute-plan/run.py verify-model
   ```
   exit 0 = match → proceed to commit; exit 4 = mismatch/unverifiable →
   hard-abort (no commit). This is the live counterpart of the harness's
   `TranscriptModelPinAdapter` over fixture transcripts.
2. **The operator-confirmed `git push`.** Evaluate the end-of-plan gate
   (`run.py push-gate`); only on `should_push: true` AND explicit operator
   confirmation does the real `git push` run (the harness proves the gate
   decision; the push itself is never silent).

## Verification

1. **Engine regression (frozen).**
   `python3 ~/.claude/skills/execute-plan/test_run.py` — the v2 engine's
   282-test suite stays green; this driver wiring does not change `run.py`.
2. **Spine — the operator-facing gate.** One real two-session `/execute-plan`
   run crosses a session boundary (see *Spine validation* above): session A arms
   `pending_handoff` + reaches the `execplan-session-ack` boundary; a **fresh**
   session B resumes from one paste (`run.py resume`) and continues the walk to
   completion. This — not a green suite — is the proof that the driver is wired
   (the driver is un-unit-testable judgment text).
3. **Closing verification — the assembled whole.** One real multi-slice run that
   exercises the plan-detour + at least two dispatch arms across a session
   boundary, asserted against the locked Desired Outcome (cross-session flow,
   plan-detour pause/resume, hands-off implementation, bounded context, close-out
   measurability). Proves the arms compose on the single router, not just in
   isolation.

## See also

- `Thoughts/automate-plan-execution_THOUGHT.md` — spine + `### Solution Alternative 1` (design contract).
- `Thoughts/automate-plan-execution_S2_PLAN.md` — this slice's plan.
- `~/.claude/rules/code_first_architecture.md` — hexagonal Ports & Adapters + 4-step growth (`:281-291`).
- `${KIT_HOOKS_DIR}/_factcheck_engine.py:379-383` — the reused model-family slug map.
- `~/.claude/skills/plan-followups-review/` — sibling primitive (S1); consumed by the orchestrator's S9 observation harvest.
- `${KIT_HOOKS_DIR}/check-impl-models.sh` — runtime per-spawn model enforcement.

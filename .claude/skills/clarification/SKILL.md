---
name: clarification
version: "1.1"
description: Clarification phase — 10 hard-gated steps (+ Step 1b problem framing) that turn a raw idea into a 4-section _thought file with 4 locked fields in # Discovery, ready for /solution-design. Opens with three confirmation moments — the idea reflected back as what was understood (Step 1), the problem framed from that confirmed idea (Step 1b), then the full reflection drawn from both (Step 2) — so a misread is caught where it is cheapest to fix. Supports resume mode against an existing _THOUGHT.md.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# /clarification — Clarification Phase

See `~/.claude/rules/factcheck-convergence.md` for canonical convergence rules.
See `~/.claude/rules/plan-gates.md` for the canonical kernel definitions (Diagnosis, Guiding Policy, Coherent Actions). This skill **references** that kernel — it does not restate it.

Canonical workflow for the Clarification phase. Source: `Thoughts/workflow-phases-redesign_THOUGHT.md` (Slice D spec + D follow-up 2026-05-21).

## When to Use

Run at the START of any session with a new topic that has more than trivial scope. The Clarification phase produces a **`_thought` file with 4 locked fields** that downstream `/solution-design` (Phase 2) or fast-track `/plan` (Phase 3) consumes.

- Fresh mode: `/clarification` — starts at step 1 against a new topic.
- Resume mode: `/clarification --from <path>` — re-enters against an existing `_THOUGHT.md`; each step presents its existing content "as-is" at the gate. If the file has the legacy `## Snapshot` structure (10-section flat), offer the user a one-shot migration to the 4-section spec before resuming.

For trivial upstream-surfaced code errors, use `/ninja-fix` (ultra-fast-track). For topics already past Clarification, go to `/solution-design` (default) or `/plan` (fast-track).

## Invariants

- **Producer-never-verifies** (`Thoughts/workflow-phases-redesign_THOUGHT.md:93`; `~/.claude/rules/code_first_architecture.md:112`). Any AI output that reaches the user inside this skill is independently fact-checked by `/double-check` or `/challenge` before the user sees it.
- **No user decision → no step forward.** Every step gates on an explicit user-confirmation token. The `pre_plan_gates.py clar-advance` handler enforces strict step ordering in code.
- **One canonical kernel.** Diagnosis / Guiding Policy / Coherent Actions vocabulary lives in `~/.claude/rules/plan-gates.md`. This skill reads it — it does not restate it.

## Orchestrator-pattern conformance

`/clarification` is an **orchestrator** in the sense of `~/.claude/rules/orchestrator-pattern.md`: it holds the live gated interaction, delegates genuine production to canonical sub-skills, and keeps its flow in code. It is **inline-by-design** (`orchestrator-pattern.md` §5 names it explicitly) — the gated, turn-by-turn confirm-and-correct dialog IS the deliverable and must stay in the main session. Every act is classified inline / code / delegate per §2:

| Act | Bin | Where |
|-----|-----|-------|
| Step ordering, cascade, rewind, cadence | **CODE** | `pre_plan_gates.py` `clar-advance`/`clar-status`/`clar-rewind`/`clar-set-cadence` |
| Topic + phase registration; Step-9 lock-marker writes; Step-10 next-session prompt | **CODE** | `create-topic`, `phase_start`, lock markers, `write-next-session-prompt` |
| Skill marker (Step 0a) + skill-run metrics (Step 10) | **CODE** | `skill_marker.py`, `skill_metrics.py`, `skill_runs.py` |
| Step 6 research-scope registration + the pre-filled source declaration it carries | **CODE** | `research_pipeline.py advance r0_intake`; the declaration is assembled by `~/.claude/skills/research/source_picker.py` and shown at Step 6's existing depth gate for confirmation — pre-filled, never a sixth stop and never silent consent |
| Step 2 `manageable`-mode claim-set validation (opt-in) | **CODE** | `_claim_persist.py` in-memory (no `--persist`); typed set or `SchemaError`, no truth judgment |
| Step 1 idea capture + the confirmed-idea interpretation (Moment 1); Step 1b what-I-hear + the Problem framed from that confirmed idea (Moment 2); Step 2 full reflection drawn from both (Moment 3); Step 4's own-words mirror; Step 2 `manageable`-mode claim identification (two-stage, in-memory over the accepted reflection); Step 6 depth pick; Step 7 cadence + Guiding-Policy draft + Discovery assembly + Scope/Q&A synthesis; all gate confirmations | **INLINE** | the gated main-session dialog |
| Step 3 rules recommendation (on defer) | **DELEGATE** | Opus `readonly-checker` agent |
| Step 5 Q&A surfacing | **DELEGATE** | `/challenge --mode surface_oqs` |
| Step 6 research | **DELEGATE** | `research-en` / KL |
| Step 7 Desired-Outcome draft | **DELEGATE** | `outcome-framing` |
| Steps 7 & 9 coherency fact-check; Step 7-tail challenge | **DELEGATE** | `/double-check`, `/challenge` |
| Step 8 metrics validation | **DELEGATE** | `lean-analytics-metrics` (internally `/double-check`) |

**Residual-inline disposition (why the inline acts stay inline).** The AI-produced inline acts — the Step 1 confirmed-idea interpretation, the Step 1b Problem draft, the Step 2 full reflection, Step 4's mirror, and the Step 7a Guiding-Policy draft + Discovery assembly — are **kept inline**, not delegated:

- The three confirmation moments and Step 4's mirror are **load-bearing gated interaction**: each one *is* the dialog at that point, and what the person accepts at it becomes what every later step reads — the idea at Step 1, the Problem at Step 1b (Step 1b: "not an AI construct that slips past unreviewed"). Delegating them would break the correction loop that makes the acceptance mean something.
- The Step 7a Guiding-Policy draft + Discovery assembly are **entangled with the gated Step-7 draft-then-validate loop** and are already producer-never-verified by the Step-7 `/double-check` dispatch. There is no cleanly-separable production sub-act here today. Delegating the Guiding-Policy first-draft to a right-sized adapter is a *named future* (`orchestrator-pattern.md` §4 — "split only for a named future"), deliberately **not** done now: the marginal benefit is low and the risk to the gated loop is real.

Net: `/clarification` already delegates all of its genuine production; the retrofit adds only the two missing mechanical seams (marker + metrics) and this classification record. Model selection for every delegated act is **at dispatch**, never in this skill's frontmatter (`orchestrator-pattern.md` §3.1).

## Setup

**Step 0a — Read session state.**

Read the SESSION_ID from the most recent `SESSION_ID=...` line in this conversation (echoed by the UserPromptSubmit hook on every prompt). Then write the `/clarification` skill-invocation marker — the orchestrator-pattern guardrail-handshake seed that records the canonical skill is running, so a look-alike `Agent` dispatch is detectable (`~/.claude/rules/orchestrator-pattern.md` §5; enforced by the PreToolUse `check-skill-marker.sh`). Idempotent — safe to re-run on `--from` resume:

```bash
python3 ${KIT_HOOKS_DIR}/skill_marker.py write clarification SESSION_ID
```

**Step 0b — Detect mode.**

- Fresh: `/clarification` with no argument → proceed to step 1 against a new topic.
- Resume: `/clarification --from <path>` → resolve `<path>` to an existing `_THOUGHT.md`. If the file does not exist, hard error ("did you mean `Thoughts/<slug>_THOUGHT.md`?"); do not silently create. Otherwise call:

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py clar-status SESSION_ID
```

The status reply tells you the current step + any prior payloads + the resume source.

**Step 0c — Register topic (fresh mode only).**

If `clar-status` reports `no_topic`, register the topic first via the engine's `create-topic` CLI:

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py create-topic SESSION_ID PROJECT_SLUG TOPIC_SLUG
```

**`TOPIC_SLUG` is not optional here.** The engine derives the work-name half from the work's own artifact or from an explicit slug, and **refuses when it has neither** — it will not fall back to the working directory (that fallback minted records whose stored identity contradicted their own filename). This line documented the bracketed optional form until 2026-08-22; following it now yields a non-zero exit naming what is missing. If the spine already exists, the equivalent artifact-derived form is:

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py create-topic SESSION_ID PROJECT_SLUG \
    --thought-file-path <path-to>/<slug>_THOUGHT.md
```

This creates a topic-state file under `~/.claude/state/pre_plan_gates/` and points `_active.json` at it. The topic's `phase` should be `thought` (Slice C-ii precedent — `phase_start("thought", todo_text=<first-step-output>)` at step-1 acceptance). Slice F removed the legacy `classify --new-topic` CLI + the `classification` field — topics are just topics; research and exploration workflows live in standalone skills (`/research`, `/extract-knowledge`, Step 6 below).

**Step 0d — Worktree auto-placement (S7 A14, `--worktree`-opt-in).**

When `/clarification --worktree` is used on a harness (config-source) topic, place the session into the topic's own worktree right after Step 0c — before Step 1 mints the `_thought` — so the new topic is authored inside its isolated workspace (the one-choice placement `/work-start` already offers, `work-start/SKILL.md:255`). Retrieve the path from `place` (path-only stdout) FIRST, then `cd` with strict short-circuit (never a bare `cd` — `work-start/SKILL.md:259`):

```bash
REPO="$(the configured source path)"      # harness repo-binding
DEST="$(${KIT_HOOKS_DIR}/worktree-helper.sh place --repo "$REPO" --topic "<the topic slug Step 0c registered>")" \
  || { echo "[clarification] worktree placement did not complete" >&2; exit 1; }
cd "$DEST" || { echo "[clarification] cannot cd into worktree $DEST" >&2; exit 1; }
```

On the artifact-derived form above, no `TOPIC_SLUG` is typed at Step 0c at all — the slug is derived from the spine, so read it from `create-topic`'s JSON result (`topic`) rather than from anything you passed in.

Without `--worktree` the topic is authored in place, exactly as today — no placement (`work-start/SKILL.md:219` precedent). `place` is idempotent (a resume returns the existing worktree, never a duplicate). Projects-side binding activates with S9/`[[storage-decouple]]`.

## Steps — the gated step contracts

The ten hard-gated step contracts (**Step 1 → Step 10**, plus **Step 1b** problem framing and **Step 7-tail**), the **resume-mode** composite behavior, and the **cascade-on-update map** live in [`steps.md`](steps.md) — a one-level bundled reference loaded on demand (`~/.claude/rules/skill-authoring.md` §5 progressive disclosure). **Read `steps.md` in full before executing the phase**; each step there carries its own gate token, `clar-advance` call, and output schema verbatim.

---

## Error Recovery

If a step's correction loop fails (user can't articulate, AI keeps mishearing, sub-deliverable skill errors, `/double-check` or `/challenge` times out): stop. Tell the user what failed and offer:

- Retry the same step with fresh input.
- Fall back to free-text input (skip the AI scaffold, the user writes prose directly into the payload).
- Abort `/clarification` (state persists; resume later via `/clarification --from <path>` after the underlying issue is fixed).

Never silently advance. If `clar-advance` returns a sequence violation, do not retry with a different `STEP_NUM` — read `clar-status` first and surface the discrepancy to the user.

## Important

- This skill produces no AI output that reaches the user without an independent fact-check (steps 5, 7, 8, 9 — and 7-tail when invoked). The `/double-check` and `/challenge` pipelines own the FC mechanics; this skill is the orchestrator.
- The canonical strategy kernel lives in `~/.claude/rules/plan-gates.md`. Read it for the authoritative Diagnosis / Guiding Policy / Coherent Actions definitions. Do not restate them here.
- `phase_start("thought", todo_text=...)` is called once on step-1 acceptance (Slice C-ii); the `[Thought]` TODO entry tracks the Clarification work across sessions. The counter advances in `/close` via the existing `todo.py advance-sessions` seam.
- After Step 9 lock, the four `DISCOVERY_LOCKED_FIELDS` are defended by the `check-discovery-lock.sh` PreToolUse hook. Direct Edit/Write to a locked field returns exit 2 with a message naming the field and the unlock path (`/clarification --from <path>`).
- The `<!-- handoff-src-hash -->` marker hash covers only the four locked fields (see `pre_plan_gates.py:175` regex and `:2332` write seam). Mutable Discovery edits (`## Scope`, `## Q&A`) do not invalidate handoff freshness.

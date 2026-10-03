---
name: prompt-for-handoff
description: Generates a PE-conformant session-continuity prompt on demand, at any point in any session — output to chat, and persisted to the target file's `## Next Session Prompt` section when a file resolves. Use when the operator asks for a handoff prompt, a continuation prompt, or a next-session prompt, or says "hand this off", "write the handoff", or "prep the next session". Also called internally by /clarification Step 10, /close section 1d, and /work-done's M7 last step. Not for writing the diary or closing a session — that is /close.
---

# Skill: /prompt-for-handoff

> **Runtime requirement.** This skill depends on Claude Code machinery:
> `hooks/pre_plan_gates.py`, a `SESSION_ID` published by the `UserPromptSubmit`
> hook, and the `write-next-session-prompt` verified-write gate. None of those
> load under OpenCode (no `hooks` key in its config schema — hooks arrive only
> via plugins). Under OpenCode, Step 1 cannot resolve a target file; stop and say
> so rather than improvising a path.

## Description
Generate a PE-conformant session-continuity prompt on demand, at any point
in any session. Output to chat. Persist to `## Next Session Prompt` in the
target file when a file is resolved.

## Trigger
Invoked explicitly as `/prompt-for-handoff [<file>] [--regen]`.
Also called internally by `/clarification` Step 10, `/close` section 1d, and
`/work-done`'s M7 last step (which always passes `--regen`).

**`--regen` (force regeneration).** Skips Step 3.5's freshness *reuse* decision
entirely and always composes → verifies → writes (the ABSENT-path behaviour),
so a stale `## Next Session Prompt` is refreshed even when `handoff-freshness`
reports `FRESH`. This is the AD-5 operator escape hatch AND the force path
`/work-done`'s M7 step consumes at a ship event (project-tracking-staleness S5):
a ship event, by construction, changes the picture, so the handoff must be
regenerated regardless of what the source-bytes freshness gate says. `--regen`
forces *recomposition* only — it never bypasses the verified write gate
(`write-next-session-prompt` still refuses without a `/double-check` PASS
marker), so producer-never-verifies is preserved.

## Steps

### Step 1 — Resolve target file (code)

Run:
```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py resolve-target-file SESSION_ID [FILE_ARG]
```
Replace SESSION_ID with the value from the `UserPromptSubmit hook success: SESSION_ID=...`
line. Replace `[FILE_ARG]` with the explicit file argument the user passed, if any.

Handle output:
- `<abs_path>` → target file resolved; proceed to Step 2
- `OFFER_CREATE:<path>` → ask user: "File not found. Create [path]?" If yes, create it (Write tool with a minimal header), then proceed.
- `NOT_FOUND:<path>` → report not-found, ask user for correct path, then retry Step 1 with the corrected path.
- `` (empty string) → state-agnostic mode; proceed to Step 2 (no file write in Step 4).

### Step 2 — Read PE spec (mandatory before any Write)

Read `~/.claude/rules/prompt-engineering.md`.
The PE-freshness hook validates on Read; follow its instructions if it blocks.

Focus on lines 195–220 (`## Handoff Prompts — PE Guidance`): the 5 properties
and the XML template are the canonical spec. Do not duplicate or paraphrase —
apply directly.

### Step 3 — Compose candidate prompt (artifact only, NO Advisory)

The composed/persisted artifact is **only** the `<topic>` + `<instructions>`
block. The operator Advisory is chat-only and is emitted separately in Step 4 —
never inside the composed block, in any mode.

**Composer file-input invariant (A2 coupling, updated D-follow-up 2026-05-21; visible-stamp note added parsed-floating-naur Slice S1):** the state-aware composer reads the target file's `# Discovery` section (new-spec files) or `## Snapshot` (legacy files). The `handoff-freshness` code gate hashes the four `DISCOVERY_LOCKED_FIELDS` inside `# Discovery` (via `_discovery_locked_fields_hash` at `pre_plan_gates.py:172`, write seam at `pre_plan_gates.py:3154-3164`) AND a phase-progress fingerprint (`_phase_progress_fingerprint` — count of `### Solution Alternative N`, first not-done slice id, current `Sessions: M/N done.` counter). Legacy files without `# Discovery` return ABSENT — composer rewrites the handoff. The write seam also renders a visible `*Written: <ISO 8601 UTC>*` header as the first body line of the persisted `## Next Session Prompt` section; the header and the marker's `written:` segment share a single timestamp value (code-owned, not composed by the AI). If you ever change which section(s) the composer reads, you MUST update `DISCOVERY_LOCKED_FIELDS` + `_discovery_locked_fields_hash` in `pre_plan_gates.py` in the same change.

**If target file resolved (state-aware):**
1. Read the target file. Extract from `# Discovery` if present (new-spec files), else from `## Snapshot` (legacy):
   topic title, classification, diagnosis, and next step.
2. Compose a thin, file-anchored prompt following the XML template in
   `prompt-engineering.md:205-220` — the `<topic>` + `<instructions>` fence
   **only** (no Advisory line inside it):
   - `<topic>`: title + classification + diagnosis (one sentence)
   - `<instructions>`: numbered steps —
     (1) Run `/work-start [resolved file path]` to bind this session's topic
         before doing anything else (M13 defense-in-depth — a handoff-entered
         session is otherwise unbound at open, and every bookkeeping write that
         needs a bound topic would have no target).
     (2) Read [resolved file path] fully before starting.
     (3) [Next action — what to do, not how — derived from current session state]
     (4) Constraints: [specific constraints from the plan or session context]

**If state-agnostic (no file resolved):**
1. Extract from conversation context: topic title, classification, diagnosis.
   If context is minimal, warn: "Minimal context — this prompt may need manual enrichment."
2. Compose a self-contained `<topic>`+`<instructions>` block, same XML structure,
   no Advisory inside it. → skip Step 3.5 (no file), go to Step 4 (chat-only).

Write the composed candidate to a temp file for the lifecycle, e.g.
`/tmp/handoff_candidate_<SESSION_ID>.txt` (call it `CAND`).

### Step 3.1 — /execute-plan routing (register-presence-gated, AI-layer convenience)

When the topic has a locked `_PLAN` whose slice register is **register-present**
(≥2 non-closing slices), the next session should resume through the
`/execute-plan` orchestrator — not hand-implement the slices — so it inherits the
code-enforced gates. Shape instruction `(2)` of the composed block accordingly.

Resolve the plan and test presence (read-only; skip on any miss):

1. **Find the `_PLAN`.** If the resolved target file is itself a `_PLAN.md` (Mode C
   / state-agnostic), use it directly. Otherwise (Modes A/B, target = `_thought`)
   take the plan wikilinked under the spine's `# Implementation Details`.
2. Run the presence predicate on it:
   ```
   printf '%s' '{"spine_path":"<plan_file>"}' \
     | python3 ~/.claude/skills/execute-plan/run.py register-presence
   ```
3. **`present: true`** → phrase the "next action" step as *resume plan execution by
   invoking `/execute-plan`* (name the plan file), rather than a generic
   hand-implementation instruction.
   **`present: false`** (single-work / trivial plan) or no `_PLAN` resolvable →
   keep the existing generic next-action wording (non-regression C6).

This is a convenience only: it does not gate anything and the `/execute-plan`
code guarantee does not depend on it (the entry gate is armed independently at
`/plan` Step 11). If the predicate can't run, fall back to the generic wording.

### Step 3.2 — Cross-session worktree resume + land-readiness (S7, worktree topics only)

This step fires ONLY when the topic runs under the worktree-per-topic model — i.e.
a harness (config-source) topic, or a Projects topic once `[[storage-decouple]]`/S9
lands. For a non-worktree topic it is a silent no-op (the generic wording from
Step 3.1 stands). It has two jobs — both **advisory routing hints**, never safety
gates (the A6 verify-then-land gate re-computes green/red at land time, so a stale
hint can never cause an unsafe land — DESIGN A13/B3):

1. **Record land-readiness (the write seam).** Decide the topic's state from the
   session's end-state: `ready-to-verify-then-land` iff the work reached a landable
   point (slices done, tests green, nothing left to implement), else `in-progress`
   (the safe default — an unsure call records `in-progress`, never `ready`). Persist
   it with the code writer (survives across sessions; not swept to `_processed`):
   ```
   python3 ${KIT_HOOKS_DIR}/land_readiness.py write <topic-slug> <in-progress|ready-to-verify-then-land>
   ```
   A write failure is an advisory no-op (fail-open) — never block the handoff on it.

2. **Route the resume to the EXISTING worktree (shape instruction (2)).** Phrase the
   composed block's "next action" so the next session resumes **through the isolated
   handoff worker**, which resume-places the topic into its own existing worktree
   (reusing the shipped `create-or-lookup` LOOKUP arm — never a fresh collision) and,
   if the recorded readiness is `ready`, routes the land through the A6 gate:
   ```
   ${KIT_HOOKS_DIR}/handoff-worker.sh --topic <topic-slug>
   ```
   The worker prints its report to stdout and its logs to stderr; it never fires a
   bare merge (the A6 gate stays authoritative). Do NOT instruct the next session to
   `git merge`/land by hand — that bypasses the gate.

If the topic's worktree can't be resolved (not a worktree topic), skip both jobs.

### Step 3.5 — Lifecycle: reuse / verify / escalate (only if target file resolved)

**`--regen` force bypass (project-tracking-staleness S5 / AD-5).** If the
invocation carried `--regen`, do **not** run `handoff-freshness` and do **not**
take the `FRESH` reuse branch below. Treat the source as if it were `ABSENT`:
set `candidate` = the freshly composed Step-3 prompt and run the **Verify
subroutine** (compose → `/double-check` → `handoff-verify` → the sole
`write-next-session-prompt` gate). PASS → the section is rewritten (subroutine
writes it). FAIL/ESCALATE after one regenerate → no write, surface the
discrepancies, stop (the existing prior prompt is left untouched — this is what
lets a caller's fail-loud check see a stale/unchanged `written:` timestamp). Then
go to Step 4. Do NOT read `handoff-freshness` at all on a `--regen` run — the
gate is structurally blind to ship-event drift, which is the whole reason the
force path exists.

Otherwise (no `--regen`), run the deterministic source-change signal:
```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py handoff-freshness <target_file>
```

- **`FRESH`** → **reuse, no write, no verify.** The source `## Snapshot` is
  unchanged since the last verified handoff. Extract the existing
  `## Next Session Prompt` body from the file and use it as the final prompt.
  Tell the user: "Reused existing prompt (source unchanged — FRESH); no rewrite."
  Go to Step 4.

- **`STALE`** → the existing prompt may have drifted from a changed source.
  Set `candidate` = the **existing** `## Next Session Prompt` body. Run the
  **Verify subroutine** on it.
  - PASS → keep the existing prompt, **no write**. Tell the user:
    "Existing prompt re-validated against changed source (STALE → PASS); kept."
    Go to Step 4.
  - FAIL → **regenerate once** (OQ5 bound = 1): recompose from `## Snapshot`
    plus the surfaced discrepancies → new `CAND`. Run the Verify subroutine on
    the regenerated candidate. PASS → write (subroutine does it). FAIL/ESCALATE
    → **no write**, existing prompt untouched, surface discrepancies, stop.

- **`ABSENT`** → first persist (no prior verified prompt, or no `## Snapshot`).
  Set `candidate` = the freshly composed Step-3 prompt. Run the **Verify
  subroutine**. PASS → write (subroutine). FAIL/ESCALATE after 1 regenerate →
  no write, surface discrepancies, stop.

**Verify subroutine (producer never self-verifies — independent check):**
1. Ensure `candidate` text is in `CAND` (temp file).
2. Build a **ground-truth `--against` context** and invoke the checker (S8 / M5):
   a. `python3 ${KIT_HOOKS_DIR}/work_done.py ground-truth-blob <target_file>` →
      write its stdout to `GT_BLOB` (a temp file). This is the topic's
      deterministic tracking state — what slices have shipped, the registered
      ordering + next-unshipped slice, linked-plan slice ids, and the completion
      ledger (assembled by code, no AI — producer-never-verifies preserved). On
      a target with no slice register (e.g. a Mode-C plan) the blob is still
      valid (sections self-flag "(no … rows)").
   b. Assemble the combined context file `AGAINST` = the `GT_BLOB` contents,
      then a separator line, then the target file's contents (blob leads so the
      checker weighs the tracking state; the target content preserves the
      coherence-with-explicit-ask check).
   c. Invoke `/double-check --class gate --against <absolute path of AGAINST>` on
      the candidate prompt. The class is `gate` rather than `check` because the
      verdict BLOCKS — `write-next-session-prompt` refuses to write the section
      without a PASS — and passing a class rather than counts is what lets the
      operator's rigor tier reach this dispatch at all. The ground-truth lets the checker flag a handoff that
      **re-targets an already-SHIPPED slice or violates plan ordering** — not
      just internal consistency. Note the artifact path `/double-check` writes
      (call it `DC_ARTIFACT`) and its chat verdict.
3. If verdict is **PASS**:
   a. `H=` output of `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py content-hash CAND`
      (deterministic — never hand-compute the hash).
   b. `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py handoff-verify <H> PASS <DC_ARTIFACT>`
   c. `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py write-next-session-prompt <target_file> --from-file CAND`
      — this is the **sole write path** (A3). It re-checks the gate and embeds
      the source hash. Do **not** use Read+Edit to write the section: that would
      bypass the verify gate. On `{"status": "written"}`, report success.
4. If verdict is **not PASS** (ESCALATE / discrepancies): return FAIL to the
   caller. Do **not** call `handoff-verify`; any write attempt will be refused
   by A3 (no marker). The existing prompt stays untouched.

### Step 4 — Output

1. **Always** echo the final prompt (composed, reused, or kept) to chat inside a
   fenced code block (```) for easy copy-paste — the `<topic>`+`<instructions>`
   block only.
2. **Always** echo the Advisory to chat **outside / below** that fenced block,
   in every mode (state-aware and state-agnostic). pfh owns the operator-facing
   Advisory copy:
   > Advisory: After this session — run /close, then /clear, then start a fresh
   > session and paste this prompt.
3. Status line:
   - written → "Written to [file] → ## Next Session Prompt (verified; dc: `<DC_ARTIFACT>`)."
   - FRESH reuse → "Reused existing prompt (source unchanged); no rewrite."
   - STALE kept → "Existing prompt re-validated (STALE → PASS); kept, no rewrite."
   - FAIL/ESCALATE → "Existing prompt KEPT. The regenerated prompt did not pass
     independent verification after one retry. Discrepancies: [list]. Operator
     decision needed — nothing was overwritten."

Do NOT write to a fallback location in state-agnostic mode. Chat-only is correct
(the A3 gate is file-scoped; chat-only mode has no persisted artifact).

**Persisted-file shape (code-owned, not composer-owned):** when the write seam
persists the `## Next Session Prompt` section, the first body line under the
heading is a visible `*Written: <ISO 8601 UTC>*` stamp produced by the A3 write
seam at `pre_plan_gates.py:3154-3164`. The chat-echoed prompt does NOT carry
this header — it is a file-only render so operators reading the spine can tell
at a glance when the prompt was authored. Do not duplicate the timestamp in
composer text; code owns it.

### Step 5 — Caller contract (when invoked by /clarification or /close)

This skill generates and persists the prompt only. It does NOT:
- Handle classification routing (caller's responsibility — /clarification Step 10)
- Emit the EnterPlanMode-block advisory (caller's responsibility — /clarification Step 10)
- Determine whether a follow-up is expected (caller's responsibility — /close 1d)

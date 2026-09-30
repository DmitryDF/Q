---
name: ninja-fix
version: "1.0"
description: Ultra-fast-track entry into the 5-phase workflow. Resolves a concrete upstream-surfaced code error end-to-end in one session via a 5-step locked contract (Diagnosis → Guiding Policy → Coherent Action → independent validator → user-confirmation hard-gate). No spine, no plan file, no handoff. Replaces legacy /quickfix.
allowed-tools: Read, Edit, Write, Bash, Grep, Glob, Agent, AskUserQuestion
---

# /ninja-fix — Ultra-Fast-Track

`/ninja-fix` is the **ultra-fast-track** entry into the 5-phase workflow (the sibling fast-track is `/plan` Mode C, see `Workflow.md` "Cross-Cutting"). It runs end-to-end in one session — no spine, no plan file, no handoff — and bounds itself with a 5-step locked contract that re-imposes producer-never-verifies discipline as a narrow named exception.

Typical use: an upstream agent surfaces a concrete code error ("Pipeline failed at MACRO_ANALYZE: `store_macro_snapshot()` got an unexpected keyword argument `check_exposures`. Want me to fix it?") and the user types `/ninja-fix`. The skill walks Diagnosis → Guiding Policy → Coherent Action → independent `/double-check` validator → user-confirmation, then either applies the edit or surfaces a clean DIRTY summary for the user to revise. A typical run takes ~2 minutes.

The 5-step contract is sanctioned by `~/.claude/rules/plan-gates.md` "Skill-internal locked step contracts" — distinct from plan Coherent Actions tables, which remain bound by `prompt-engineering.md` "prefer general instructions over prescriptive steps" + Cockburn nano-increments.

Main-session-only. Cannot be invoked from inside a subagent.

---

## Constants

```
ALLOWLIST = [sonnet, sonnet, sonnet, opus]    # Step 4 validator family pinning
COMMIT_PREFIX = "[ninja-fix]"                  # commit-message prefix on apply
CONTEXT_DEFAULT = 1                            # turns walked back in --context
CONTEXT_CAP = 20                               # hard ceiling on --context
MAX_CORRECTION_CYCLES = 2                      # auto-revise once + user-revise once
```

---

## Trigger

Activate when the user says:
- `/ninja-fix` (no args; reads the prior turn via `--context 1`)
- `/ninja-fix --help`
- `/ninja-fix --context N` or `/ninja-fix --context all`
- `/ninja-fix --acknowledge-pnv-bypass` (override after second DIRTY — see Step 5)

Do NOT activate for: multi-file refactors, architectural decisions, anything requiring plan mode, or anything where the Diagnosis is not already concrete in the upstream turn. Route those to `/clarification` or `/plan` (Mode C if fast-tracking).

---

## When to Use

| Situation | Track |
|-----------|-------|
| Upstream agent surfaced a concrete code error; the fix is one localized edit | **`/ninja-fix`** (this skill) |
| Topic is well-framed; user wants Planning entry without Clarification + Solution Design | `/plan` Mode C (Ninja-Plan, fast-track) |
| Topic needs framing | `/clarification` (full Phase 1) |

---

## Parameters

| Flag | Default | Description |
|------|---------|-------------|
| `--context N` | `1` | Number of prior turns to walk backwards from the JSONL transcript. `N=1` reads the immediately preceding turn (typical: the upstream tool-result that surfaced the error). Includes tool-use + tool-result + orchestrator records. |
| `--context all` | — | Walk the entire session JSONL. Used when the diagnostic context is spread across multiple turns. |
| `--acknowledge-pnv-bypass` | — | Override flag for the second-DIRTY pause (see Step 5). Records `pnv_bypass: true` to the commit message + diary line. Use only when the user has weighed the DIRTY summary and accepts the risk. |
| `--help` | — | Print Parameters + 5-step contract summary and stop. |

`--context` is capped at 20 — values above 20 emit a warning and clamp to 20. The cap exists because the context window for a one-shot ultra-fast-track should never need to walk further than that; if it does, the topic warrants `/clarification` instead.

---

## Invariants

1. **Validation-first ordering.** In the PASS path, the user never sees an unvalidated snapshot. Step 4 (independent `/double-check`) runs BEFORE Step 5 (user-confirmation hard-gate). The snapshot the user confirms always carries a verdict line.
2. **Producer-never-verifies at the validator dispatch.** Each Step 4 checker is parent-spawned isolated `readonly-checker` with explicit per-spawn `model:` parameter. No shared context. The runtime backstop is `${KIT_HOOKS_DIR}/check-impl-models.sh` PostToolUse on `Agent`.
3. **Code-first floor.** The Step 4 validator consumes `~/.claude/rules/code_first_architecture.md` as a baseline source of truth. Its three properties (root-cause coverage / codebase consistency / floor alignment) are direct expressions of "code-based grading before model-based grading."
4. **CLAUDE.md governance at runtime.** Step 0 reads the active project's `CLAUDE.md` and consumes declared overlay keys (`workflow_doc:`, `knowledge_library:`, `knowledge_library_index:`, `hypotheses_ledger:`). Warn-and-proceed semantics on missing declared files — emergency fixes are not blocked by stale CLAUDE.md declarations.
5. **Bounded correction cycles.** Auto-revise once on first DIRTY. Second DIRTY pauses with three options (revise / abort / override). Hard ceiling at `MAX_CORRECTION_CYCLES = 2` total cycles.
6. **Main-session-only.** Recursive invocation from inside a subagent is refused.

---

## Execution

### Step 0 — Read project CLAUDE.md (warn-and-proceed)

Resolve the active project's `CLAUDE.md` from the current working directory (walk up the tree until found, or fall back to `~/.claude/CLAUDE.md`). Read it and extract any declared overlay keys:

- `workflow_doc:` — additional governance source the Step 4 validator consumes
- `knowledge_library:` — KL root for the project
- `knowledge_library_index:` — KL index path
- `hypotheses_ledger:` — editorial-hypotheses ledger path

For each declared key whose target file does not exist, emit a one-line warning to the user (`[ninja-fix] CLAUDE.md declares workflow_doc: <path> — file not found; proceeding without`) and continue. Never block on a missing declared file.

If no `CLAUDE.md` is found at all, proceed with the universal floor only (`code_first_architecture.md`).

**Worktree auto-placement (S7 A14, `--worktree`-opt-in — reconciliation).** `/ninja-fix` resolves a single-surface upstream error in the active project's cwd, so it does NOT auto-place by default (a one-file fix rarely warrants a worktree, and the E5 single-surface constraint bounds it). `/ninja-fix --worktree` (opt-in only) resolves the topic's worktree and `cd`s into it before Step 1 (the `work-start/SKILL.md:255-259` two-step `place` → guarded `cd` form); the binding is the *invocation* repo (not necessarily the configured source path). Projects-side activation is gated on S9/`[[storage-decouple]]`. Without the flag, behavior is exactly as today.

### Step 1 — Diagnosis

Read the upstream context per `--context N` by walking `~/.claude/projects/<project-slug>/<SESSION_ID>.jsonl` backwards N turns. Include tool-use + tool-result + orchestrator records. The project slug is the kebabified absolute cwd path (e.g., `-Users-you-Library-Mobile-Documents-...`).

Draft a one-paragraph Diagnosis: name the critical aspect of the situation, in plain business words first. Identify which aspect of the upstream error is the leverage point — not just "what's broken" but "why and where action has leverage." Cite the file:line surface(s) involved.

Present the Diagnosis to the user inline. The user does NOT yet confirm — confirmation happens at Step 5. The user MAY interrupt to correct the framing; if so, redraft and continue.

### Step 2 — Guiding Policy

State the overall approach to closing the diagnosed gap, in one short paragraph. The Guiding Policy channels the action; it does not enumerate steps. Reference the code-first floor and any `workflow_doc:` overlay from Step 0.

Present inline. Do NOT pause.

### Step 3 — Coherent Action

State the specific edit: file path, line range (or function name), and the nature of the change (what is being added/removed/replaced and why). Keep this to one paragraph. If the edit touches more than one file, stop and route the user to `/plan` Mode C instead — `/ninja-fix` is scoped to single-surface localized edits.

Present inline. Do NOT apply the edit yet. The actual Edit/Write call lands at Step 5 after PASS verdict + user confirmation.

### Step 4 — Validation (`/double-check`, class `gate`)

Dispatch the validator as a parallel parent-spawned isolated `readonly-checker` agent batch. The checker allocation is the rigor dial's `gate` class — this verdict decides whether the edit is offered at all, so it is the dial's strictest class. Resolve it before spawning:

```bash
python3 ${KIT_HOOKS_DIR}/rigor.py for gate
```

- Checkers per the resolved allocation. `ALLOWLIST` is the resolved family list, in order — at `thorough` that is `[sonnet, sonnet, sonnet, opus]`; at `minimal` it is `[sonnet]`, one isolated opinion rather than a vote, and the verdict line must say so.
- `subagent_type: readonly-checker` (read-only — `Read`, `Glob`, `Grep` only)
- Isolated context per checker — no shared conversation history, no producer reasoning injected
- Explicit per-spawn `model:` parameter on each Agent tool call
- Max rounds = 1 (latency budget for the ~2-minute target run)

Each checker receives the validator prompt template below, with the substitutions filled in.

#### Validator prompt template (Step 4 inline)

```text
You are an independent validator for a /ninja-fix proposal. You will receive
a candidate triple (Diagnosis, Guiding Policy, Coherent Action) and must
verify it against three properties, returning a strict JSON verdict.

You are a `readonly-checker` subagent — read-only tools only (Read, Glob, Grep). Do not
attempt to write, edit, or run code. Do not spawn further subagents.

INPUTS:
- Diagnosis: <step-1 output, verbatim>
- Guiding Policy: <step-2 output, verbatim>
- Coherent Action: <step-3 output, verbatim>
- Codebase root: <absolute cwd path>
- Floor source: ~/.claude/rules/code_first_architecture.md
- Workflow overlay (if declared): <workflow_doc path from CLAUDE.md, else "n/a">
- KL overlay (if declared): <knowledge_library path from CLAUDE.md, else "n/a">
- Upstream context: <N-turn excerpt from session JSONL, verbatim>

PROPERTIES TO VERIFY:

1. Root-cause coverage. Does the Coherent Action address the critical aspect
   named in the Diagnosis? Or does it patch a symptom that leaves the root
   cause intact? Read the cited file:line surfaces. Reason about whether the
   edit, once applied, removes the failure mode named in the upstream error
   — not just the immediate stack trace, but the underlying cause.

2. Codebase consistency. Read the cited surfaces + their callers + their
   tests (if any). Does the proposed edit:
   (a) preserve invariants the surrounding code relies on?
   (b) match the project's established conventions for similar edits?
   (c) avoid breaking other callers of the same surface?
   Surface any caller that would break, any convention violated, any
   invariant weakened.

3. Floor alignment. Read ~/.claude/rules/code_first_architecture.md. Does the
   proposed edit respect: deterministic logic in domain layer; AI behind
   ports; producer-never-verifies; code owns the flow? If a workflow_doc
   overlay is declared, read it too and check alignment with any rules it
   states. Surface any floor violation explicitly.

OUTPUT (strict JSON, nothing else):

{
  "verdict": "PASS" | "DIRTY" | "ESCALATE",
  "properties": {
    "root_cause_coverage": "PASS" | "DIRTY",
    "codebase_consistency": "PASS" | "DIRTY",
    "floor_alignment": "PASS" | "DIRTY"
  },
  "discrepancies": [
    {
      "property": "root_cause_coverage" | "codebase_consistency" | "floor_alignment",
      "evidence": "<file:line + quoted snippet, or doc:line + quoted text>",
      "summary": "<one sentence>"
    }
  ],
  "model_attested": "<the model family you ran as: sonnet | opus | haiku>"
}

Rules:
- PASS only if all three properties are PASS and discrepancies is [].
- DIRTY on any single property failure; list every discrepancy you found.
- ESCALATE only if the inputs are incoherent (e.g., Diagnosis names a file
  that does not exist after Read) and you cannot evaluate the proposal.
- Always populate model_attested with the family you ran as. The orchestrator
  cross-checks this against the subagent transcript JSONL.
```

Aggregate the four verdicts deterministically:
- 4× PASS → overall PASS
- ≥1 DIRTY → overall DIRTY (collect all discrepancies)
- ≥1 ESCALATE → overall ESCALATE (surface verbatim)

#### Model attestation (in-skill)

After all four checkers return, read each checker's subagent transcript at:

```
~/.claude/projects/<project-slug>/subagents/agent-<agent-id>.jsonl
```

For each transcript, scan the assistant turns and extract `.message.model`. Normalize to a family slug (e.g., `claude-sonnet-4-6` → `sonnet`). Compare the four resolved slugs to `ALLOWLIST = [sonnet, sonnet, sonnet, opus]` (multiset equality, order-independent). On mismatch — silent Haiku fallback, wrong family, etc. — hard-fail the dispatch: report the drift to the user, do NOT proceed to Step 5, and recommend they re-run `/ninja-fix` after the platform issue is resolved (the runtime backstop `check-impl-models.sh` is the second line of defense).

### Step 5 — Snapshot + user-confirmation hard-gate

Present the snapshot to the user:

```
/ninja-fix — Snapshot

Diagnosis:    <step-1 output>
Guiding Policy: <step-2 output>
Coherent Action: <step-3 output>
Validator verdict: <PASS | DIRTY | ESCALATE>
Models attested: [sonnet, sonnet, sonnet, opus]
<if DIRTY: list of discrepancies>
```

Branch on verdict:

**PASS path.** Ask the user via `AskUserQuestion`:
- "Apply the edit?"
- Options: (a) Apply (Recommended) — runs the Edit/Write call + commits with `[ninja-fix] <one-sentence rationale>`. (b) Abort — discard, end session.

On (a), apply the edit and commit. The commit message starts with `[ninja-fix]` per `COMMIT_PREFIX`. Append a one-line diary entry capturing the Diagnosis + Coherent Action (Step 0 of `/close` will pick it up). End the skill.

**Commit — publish exactly the one file the edit touched.** `/ninja-fix` is single-surface by construction, so its declaration is that one file and nothing else. Do NOT compose `git add` + `git commit` yourself: a scoped `git add` followed by a bare `git commit` still commits everything any concurrent session has staged in the shared index. `publish` scopes BOTH verbs in one process, refuses a directory, and announces before committing if another live session also wrote the file:

```bash
python3 ${KIT_HOOKS_DIR}/commit_scope.py publish --repo "<repo root containing the edited file>" -m "[ninja-fix] <one-sentence rationale>" -- "<the one file the Edit/Write call changed>"
```

- **If the edited file is under `~/.claude/`, do NOT use `publish`.** `~/.claude/.git` is a frozen local snapshot (`git-policy.md` §2) and `publish` refuses it.
  - If the file is in the **managed config scope** (`agents/`, `bin/`, `hooks/`, `rules/`, `skills/`, `CLAUDE.md`, `settings.json`), promote that one file — `claude-promote` scopes both verbs in the config source and runs the PR → merge → apply flow:
    ```bash
    ~/.claude/bin/claude-promote -m "[ninja-fix] <one-sentence rationale>" --paths "<the one ~/.claude file the Edit/Write call changed>"
    ```
  - Anything else under `~/.claude/` (`plans/`, `state/`, `logs/`, `projects/` …) is session state, not publishable config. Do not promote it: `claude-promote` would `config-source add` it into the shared repo. Report the edit as applied and unpublished.
- The declared path is repo-root-relative (or absolute inside that repo). Never declare a directory, a glob, or more than the edited file — the diary line is `/close`'s to publish, not this commit's.
- On the override path (c), put `pnv_bypass: true` in the message body: pass `-m` a quoted value whose subject line is `[ninja-fix] <rationale>`, followed by a blank line, then `pnv_bypass: true`.
- **If the publish is refused with `commit is outside any topic worktree`, that is expected on this skill's default path — re-run it once with `ALLOW_OUT_OF_TREE=1` prefixed, paths still declared.** Step 0 states `/ninja-fix` does NOT auto-place, so a project-side fix runs in the primary checkout, which the commit-chokepoint gate (`check-worktree-commit-gate.sh`) refuses for a domain file. A single-surface fix is the case that override exists for — the gate's own guidance is "if this commit is legitimately out-of-tree, opt in explicitly for THIS commit only, and still name its paths". Do **not** reach for `/work-start --worktree` to get around it: that migrates the whole working tree for a one-line fix. Do **not** set `ALLOW_UNSCOPED_COMMIT=1` — it is a different variable, it is not what the gate asked for, and the declared scope must hold.
  ```bash
  ALLOW_OUT_OF_TREE=1 python3 ${KIT_HOOKS_DIR}/commit_scope.py publish --repo "<repo root>" -m "[ninja-fix] <rationale>" -- "<the one file>"
  ```
  Tell the user you used it and why; the override is a deliberate, stated choice, never a silent retry. **The refusal leaves the declared path STAGED** — `publish` is not atomic across the gate — so if you do not re-run immediately, unstage it with the scoped command the gate prints. Never a bare `git reset`: the index is shared with every other session in that checkout.
- `status: skipped` (no changes) or a `PublishError` → report it verbatim and stop. Never fall back to a bare `git commit`.

**DIRTY path (cycle 1 — auto-revise once).** Read the discrepancies. Auto-revise the Diagnosis / Guiding Policy / Coherent Action triple in response — the AI does the revision unprompted, then re-runs Step 4 with the revised triple. Present the new snapshot. Increment correction-cycle counter to 1.

**DIRTY path (cycle 2 — pause with 3 options).** Ask the user via `AskUserQuestion`:
- "Validator returned DIRTY twice. What's next?"
- Options:
  - (a) Revise myself (Recommended) — user provides a revised framing; skill re-runs Step 4 once. This consumes the second of `MAX_CORRECTION_CYCLES = 2`.
  - (b) Abort — discard, end session. No commit.
  - (c) Override with `--acknowledge-pnv-bypass` — apply the edit despite DIRTY. Records `pnv_bypass: true` in the commit message body + diary line. Use only when the user has weighed the DIRTY summary and accepts the risk.

After either correction cycle, if the validator still returns DIRTY and the user has not picked override, end the skill with a DIRTY summary message — do NOT apply the edit.

**ESCALATE path.** Surface the ESCALATE summary verbatim. End the skill — do not apply.

---

## Output Contract

The skill produces no JSON output. Its observable effects are exactly one of:

- A single git commit with message prefix `[ninja-fix]` containing exactly the one edited file (PASS path or override path) — published via `commit_scope.py publish`, or via `claude-promote --paths` for a managed-scope `~/.claude` file. A `~/.claude` session-state edit is applied but not published.
- A DIRTY summary message + no edit applied (correction-cycle exhausted or user-aborted).
- An ESCALATE summary message + no edit applied.

A one-line diary entry is appended either way (apply or abort) so `/close` can pick it up.

---

## Edge Cases

- **E1 — No upstream context.** `--context 1` walks back, finds no non-tool-result user message → fail loud with a diagnostic telling the user to either provide context inline or use `/clarification`.
- **E2 — `--context all` very large session.** Cap reads at the 20-turn ceiling regardless; emit a warning telling the user the diagnosis may miss earlier context.
- **E3 — No CLAUDE.md anywhere.** Step 0 proceeds with the universal floor only. No warning beyond the standard "no CLAUDE.md found" note.
- **E4 — CLAUDE.md declares a file that does not exist.** Warn-and-proceed per Invariant 4. The Step 4 validator runs without that overlay.
- **E5 — Multi-file edit surfaced at Step 3.** Stop. Route the user to `/plan` Mode C. `/ninja-fix` is scoped to single-surface edits.
- **E6 — Subagent transcript missing or unreadable.** Model attestation hard-fails per Invariant 2. Report drift and refuse to proceed to Step 5.
- **E7 — Mixed-family attestation (e.g., one checker silently ran on Haiku).** Hard-fail per Invariant 2. The runtime backstop `check-impl-models.sh` is the second line of defense.
- **E8 — User aborts at Step 5.** No commit, no edit. Diary line captures the abort with the DIRTY summary (if any) so the topic is searchable later.
- **E9 — Override flag at Step 5 cycle 1.** Allowed only at cycle 2 — the auto-revise must run first. Refuse the override if cycle counter is 0.
- **E10 — Invoked from inside a subagent.** Refuse. Main-session-only.
- **E11 — Validator dispatch fails (Agent tool errors).** Treat as ESCALATE. Surface the error verbatim and end.
- **E12 — Project slug resolution fails.** Walk `~/.claude/projects/` and pick the most-recently-modified slug whose directory contains a JSONL named `<SESSION_ID>.jsonl`. If none match, fail loud.

---

## See also

- `~/.claude/rules/plan-gates.md` — "Skill-internal locked step contracts" carve-out that sanctions the 5-step contract
- `~/.claude/rules/code_first_architecture.md` — code-first floor consumed by Step 4 validator
- `~/.claude/rules/factcheck-convergence.md` — voting pattern + isolation + grading order that Step 4 inherits
- `~/.claude/rules/prompt-engineering.md` — general instructions over prescriptive steps (applies to plan Coherent Actions, NOT to this skill — the carve-out applies)
- `${KIT_HOOKS_DIR}/check-impl-models.sh` — runtime model-pinning backstop (PostToolUse on `Agent`)
- `~/.claude/skills/plan/SKILL.md` — sibling fast-track (Mode C Ninja-Plan)
- `~/.claude/skills/clarification/SKILL.md` — full Phase 1 entry for topics that need framing
- `Workflow.md` "Cross-Cutting" — names both speed tracks (this skill + `/plan` Mode C)

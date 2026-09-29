---
name: work-frame-and-create-todo
description: Frame a piece of work using the 4-component convention (Problem / Context / Guiding policy / Master plan) and add it to the project's TODO.md through the framing validator. Use when a user wants to create a [Thought]-tagged TODO item or any item that must pass the framing gate.
allowed-tools: Bash, AskUserQuestion, Read
---

# /work-frame-and-create-todo

Conversationally elicit the four framing components, then add the framed item to the project's `TODO.md` through the code-side framing validator. Thin AI elicitation surface around `${KIT_HOOKS_DIR}/todo.py add --validate-framing`; the cap on how many times we retry on validation failure lives in `run.py` (code), not in this file.

---

## Trigger

Explicit invocation only:

- `/work-frame-and-create-todo`
- "frame this and add it to TODO"
- "create a [Thought] TODO for …"

**Not auto-triggered.** v1 is callable-only — the user (or another skill) invokes it explicitly.

---

## What this skill does

1. **Elicit** the four framing components from the user (and a bucket):
   - `**Title**` — the bold-wrapped item title
   - `Problem:` — what's broken or unresolved
   - `Context:` — relevant background, prior decisions, constraints
   - `Guiding policy:` — the overall approach (no implementation detail)
   - `Master plan:` — either a wikilink to the plan/thought file or the literal `Master plan:` label followed by the plan reference
2. **Ask for the bucket** — `NOW` / `NEXT` / `NEARBY` / `NASCENT` / `SCHEDULED` (per 5N just-in-time learning).
3. **Compose** a single-line TODO item that matches the convention:
   `**Title** — Problem: …. Context: …. Guiding policy: …. Master plan: [[…]].`
4. **Call** `run.py` (the orchestrator) with a JSON payload on stdin.
5. **Branch** on `run.py`'s structured JSON result:
   - `status: PASS` → tell the user it landed, show the resolved TODO.md path, stop.
   - `status: RETRY` → re-elicit ONLY the components listed in `missing_components`; recompose; call `run.py` again.
   - `status: EXHAUSTED` → surface `stderr_verbatim` and stop. **Do not loop further.** The 3-retry cap is code-enforced — `run.py` will refuse to retry on the next call regardless of what this skill does.

---

## Calling the orchestrator

Run `run.py` from this skill folder via Bash. Pass the JSON on stdin via a heredoc-safe pattern (use `Write` to a temp file then pipe, or pass a single-line JSON):

```
echo '<json>' | python3 ~/.claude/skills/work-frame-and-create-todo/run.py
```

Input JSON contract (keys):

| Key | Required | Description |
|-----|----------|-------------|
| `item_text` | yes | The composed single-line TODO item (4 components + bold title + em-dash). |
| `bucket` | yes | One of `NOW`, `NEXT`, `NEARBY`, `NASCENT`, `SCHEDULED`. |
| `cwd` | no | Working directory to resolve the target `TODO.md` from. Defaults to the orchestrator's `os.getcwd()`. |
| `session_id` | no | Stable identifier so consecutive calls share the retry counter. Defaults to `$CLAUDE_SESSION_ID` or `"default"`. Pass the current session id when available. |

Output JSON contract (keys): `status` (`PASS` / `RETRY` / `EXHAUSTED` / `ERROR`), `attempt`, `max_retries`, `missing_components`, `stderr_verbatim`, `session_id`. Exit codes: `0=PASS`, `1=RETRY`, `2=EXHAUSTED`, `3=usage/input error`.

---

## Elicitation flow

**Step 1 — Title + bucket.** Ask the user for a short, action-oriented title and a bucket. Use `AskUserQuestion` for the bucket so the user can pick from a single-select list.

**Step 2 — Four components.** Ask for `Problem:`, `Context:`, `Guiding policy:`, `Master plan:` — one at a time or in a single prompt depending on what the user prefers. Be explicit that:

- `Problem:` should name the critical challenge, not just describe symptoms (Rumelt diagnosis style).
- `Guiding policy:` should describe the approach, not the specific actions.
- `Master plan:` should be either a wikilink (`[[plan-slug]]`) to the planning artifact OR the literal `Master plan:` label followed by a description; the validator accepts either form.

**Step 3 — Compose.** Build the item text in this exact shape (the validator regexes match against these labels):

```
**<Title>** — Problem: <…>. Context: <…>. Guiding policy: <…>. Master plan: [[<plan-slug>]].
```

`Master plan:` takes **either** form. Both of these are accepted:

```
… Master plan: [[omtm-dashboard-plan]].
… Master plan: none yet — this TODO owns the work directly.
```

Show the second form when there is no planning artifact. Only ever showing the
wikilink is what made the other form look unsupported.

**Evidence.** This skill files through `--require-evidence`, so an item must
point at somewhere to look — a `path:line`, a backticked file, a `[[wikilink]]`,
or a URL. An item that points at nothing is refused with a reason, and nothing
is written.

For build-new work there is often nothing to point at yet. Record that
deliberately with the literal token `[build-new]` in the item text:

```
**Create a strategy-creation skill** — Problem: no reusable tool helps author a
strategy from a diagnosis. Context: the kernel is documented but nothing
operationalises it. Guiding policy: compose existing sub-skills rather than
adding a new framework. Master plan: none yet. [build-new]
```

The token stays on the list on purpose — an escape hatch that vanishes after the
call cannot be audited, and its overuse should be visible. A citation that does
not resolve produces a **warning, never a refusal**: most correct citations in
this corpus name a file in another repo or by an ambiguous basename.

**Step 4 — Call `run.py`.** Use Bash to invoke the orchestrator. Pass `session_id` when available so consecutive failed attempts share the retry counter.

**Step 5 — Branch on result.**

- **PASS** → "Item added to <path>. Bucket: <bucket>." Stop.
- **RETRY** → look at `missing_components`. Re-elicit ONLY those components from the user. Recompose. Call `run.py` again.
- **EXHAUSTED** → "Framing validation failed after 3 attempts. The validator reports: `<stderr_verbatim>`. Re-engage when you have the four components ready, or compose the item directly." Stop. **Do not loop further.**

---

## Concerns (0–3) convention

When you report PASS / RETRY / EXHAUSTED back to the user, follow the project's Concerns convention: include 0–3 specific concerns about your own elicitation (gaps, assumptions, missing data). One sentence each. No abstract scores.

---

## Constraints

- **No re-validation in this skill.** The validator (`todo.py add --validate-framing`) is the single source of truth for framing correctness. This skill does not parse the user's input against the four components on its own.
- **Retry cap is code-enforced.** `run.py` owns the per-session counter and the 3-retry cap via a state file at `~/.claude/state/work-frame-and-create-todo/<session_id>.json`. Even if this SKILL.md tells the AI to "keep trying," the orchestrator will return `EXHAUSTED` on the 4th call. Trust Hierarchy: Code > Rules > Skill text.
- **Producer-never-verifies.** AI elicits; code validates. (`~/.claude/rules/code_first_architecture.md:112`.)
- **Thin shim.** `run.py` contains NO framing logic — it only orchestrates the loop around `todo.py add --validate-framing`.
- **`cwd` resolution.** `run.py` walks up from `cwd` to find the nearest `TODO.md`. If none is found up to filesystem root, it returns an `ERROR` payload and exits with code 3.

---

## Edge Cases

| # | Case | Detection | Behavior |
|---|------|-----------|----------|
| E1 | cwd inside a subdir | Working dir is several levels below the project root | `run.py` walks up looking for `TODO.md`; uses `--project <root>` once found. |
| E2 | Partial framing | Validator emits `  ✗ <err>` lines via stderr | `run.py` returns `status: RETRY` with `missing_components: [...]`; SKILL re-elicits ONLY those fields. |
| E3 | No `TODO.md` anywhere | Walk-up from `cwd` reaches filesystem root with nothing | `run.py` returns `ERROR` exit 3. Tell the user: "No TODO.md found at <cwd> or any parent. Create one with `touch TODO.md`, then retry." Stop. |
| E4 | Cap reached | `run.py` exit code 2, `status: EXHAUSTED` | Surface `stderr_verbatim`. **Do not loop.** Stop. |
| E5 | Bucket not in enum | User picks something else than NOW/NEXT/NEARBY/NASCENT/SCHEDULED | `run.py` returns `ERROR` exit 3. Re-ask the user for a valid bucket. |
| E6 | Empty stdin / bad JSON | Orchestrator can't parse input | `run.py` returns `ERROR` exit 3. Skill recomposes and retries. |
| E7 | User abandons elicitation | No reply within one prompt cycle | Skill exits silently. No blocking state; cap state file is per-session and cleaned up on next PASS / EXHAUSTED. |

---

## Examples

### Example 1 — Happy path

**User:** `/work-frame-and-create-todo`

Skill asks for the title, the bucket, and the four components. User answers each. Skill composes:

```
**Wire up the OMTM dashboard** — Problem: we can't see week-over-week OMTM movement. Context: dashboard wiring exists but the OMTM query was never plumbed. Guiding policy: reuse the existing dashboard scaffolding; do not introduce a new visualization. Master plan: [[omtm-dashboard-plan]].
```

Skill calls `run.py` with `bucket: NEXT`. Validator passes. Skill reports:

> Item added to `Personal/foo-Initiative/TODO.md` under NEXT. Concerns: [].

### Example 2 — Retry path

User provides three of four components on the first pass. Skill composes, calls `run.py`. Validator fails on `Master plan:`. `run.py` returns:

```json
{"status": "RETRY", "attempt": 1, "max_retries": 3,
 "missing_components": ["Missing 'Master plan:' label or [[wikilink]]"],
 "stderr_verbatim": "  ✗ Missing 'Master plan:' label or [[wikilink]]\nFraming validation failed. Fix the item text and retry.\n",
 "session_id": "abc123"}
```

Skill re-elicits ONLY the Master plan field, recomposes, calls `run.py` again. Validator passes; item added; state file cleared.

### Example 3 — Cap reached

User gives malformed framing four times in a row. Calls 1–3 return `RETRY`; call 4 returns `EXHAUSTED` (exit code 2). Skill surfaces the validator's verbatim stderr and stops. The state file at `~/.claude/state/work-frame-and-create-todo/<session_id>.json` is deleted by `run.py` on the EXHAUSTED return, so the next fresh invocation starts a new attempt counter.

---

## Sibling Skills

- `/clarification` (`~/.claude/skills/clarification/SKILL.md`) — full Clarification phase that creates a `_THOUGHT.md` and a framed TODO line in one motion. Use `/clarification` when the work **needs a spine** — it is large enough to carry its own Discovery, solution design and plan. Use this skill when you only want the framed TODO.

  **A pre-existing plan or thought file is NOT a precondition for this skill.** A framed TODO may own a plan directly, with no `_THOUGHT.md` in between — that is a sanctioned entry point (`bookkeeping-model.md` §6, "TODO-owned"), not a workaround. Earlier wording here said to use this skill when "the framing artifact (plan / thought file) already exists", which read as a requirement and steered people into `/clarification` for work that never needed a spine. Nothing in the validator asks for one: the `Master plan:` field accepts a plain reference as readily as a wikilink (see `:73` and the second example below).
- `/work-start` (`~/.claude/skills/work-start/SKILL.md`) — moves an already-framed TODO into in-progress.

---

*Created: 2026-05-24 — v1. Code-enforced retry cap via `run.py` orchestrator (per-session state file). SKILL.md owns elicitation only.*

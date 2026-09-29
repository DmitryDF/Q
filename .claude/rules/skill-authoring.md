# Skill Authoring — Conformance Reference

**Status:** active. Authored 2026-07-08 (Slice S7 of `Thoughts/subagent-delegation-context-focus_PLAN.md`) from the settled `[[subagent-delegation-context-focus_SKILLAUTHORING_RESEARCH]]`.
**Role:** the **single conformance reference** every revamped skill is checked against. Sibling of `~/.claude/rules/orchestrator-pattern.md` (the *pattern*); this file is the *authoring standard*. Read both together when authoring or retrofitting a skill. Location/scope rules live in `~/.claude/rules/skill-location.md` — this file is authoring, not location.

**Provenance note (S7 settling pass).** The source research carried a fact-check verdict of `ESCALATE / no_consensus_after_3_rounds`, but its own confidence was *"High — 0 concerns on official guidance"*; the non-convergence was disjoint-checker churn on two edge-detail open questions (below) + one WebFetch-denied cross-reference, not unsettledness of the core guidance. The core rules are grounded verbatim in the agentskills.io open standard + Anthropic best practices and are safe to author against. The two genuinely-open items are carried as explicit caveats, not silently resolved.

---

## 1. Frontmatter — two required fields, everything else optional

A skill is a directory with `SKILL.md` at its root. Frontmatter is YAML.

**Open-standard fields (agentskills.io spec — portable across 30+ tools):**

| Field | Required | Constraints |
|---|---|---|
| `name` | **Yes** | ≤ 64 chars; lowercase letters/numbers/hyphens only; no leading/trailing/consecutive hyphens; must match the parent directory name. |
| `description` | **Yes** | ≤ 1024 chars; non-empty; states **what** the skill does **and when** to use it. |
| `license` | No | License name or bundled-file reference. |
| `compatibility` | No | ≤ 500 chars; environment requirements. |
| `metadata` | No | Arbitrary key-value map. |
| `allowed-tools` | No | Space-separated pre-approved tools. **Experimental — CLI-only, not supported in the SDK.** |

[stated — https://agentskills.io/specification]

**Author only `name` + `description` for maximum portability.** Everything else is optional or a vendor extension. Claude-specific frontmatter extensions (`disable-model-invocation`, `user-invocable`, `agent`, `context`) are advisory and ignored by other tools — never load-bearing.

---

## 2. The `description` is the ONLY trigger

The AI decides whether to invoke a skill from its `description` alone (the body is not loaded until the skill fires — §5). So the description carries the entire triggering burden:

- **Third person.** "Extracts knowledge from books…" not "I extract…" / "You can extract…".
- **Both WHAT and WHEN.** Name what the skill does *and* the situations that should invoke it, with specific keywords a matching prompt would contain.
- **Concrete over abstract.** Include the file types, task shapes, or trigger phrases that should fire it (e.g. "Use when working with PDF files, extracting text, or filling forms").
- **Distinguish siblings.** When a similar skill exists, say what this one does NOT do.

Cross-vendor parallel: OpenAI's "Use this when…" and Gemini's "be extremely clear and specific" are the same discipline for JSON-schema tools. Anthropic's convention is *third person, what + when* — there is no official "start with 'Use this when'" rule.

---

## 3. No `model` field in SKILL.md

**SKILL.md has no `model` field** in the open standard or any Claude-documented extension. Model selection for a skill's delegated work is an **orchestration concern** — set it at dispatch (the `Agent`-tool `model` parameter, or a **named agent's** `model:` field in `~/.claude/agents/*.md`), never in SKILL frontmatter. This is the same rule the orchestrator pattern enforces (`orchestrator-pattern.md` §3.1): frontmatter model-switching was tried and reverted because it changes the model without isolating context.

*(Open question 2, carried: no `model` field for SKILL.md was found in the agentskills.io spec, Anthropic best practices/overview/SDK, or skills-guide; the sub-agents `AGENT.md` format does carry one. Treat "SKILL.md has no model field" as correct until an official source says otherwise.)*

---

## 4. `allowed-tools` — experimental, least-privilege

- Marked **Experimental** in the open standard; **CLI-only** (not honored by the SDK). Do not rely on it for security.
- When used, scope to **least privilege** — list only the tools the skill needs. A read-only skill lists read tools; a producer that must write lists Write/Edit; an orchestrator that delegates lists `Agent`.
- Because support varies, treat `allowed-tools` as advisory; the real tool boundary for a delegated adapter is the **named agent's** `tools:` field (`~/.claude/agents/*.md`), which is honored.

---

## 5. Progressive disclosure — keep bodies lean, references one level deep

Skills load in three levels; authoring must respect it:

1. **Metadata (~100 tokens/skill, always loaded):** `name` + `description` only. This is why a large skill library is cheap — the trigger surface is tiny.
2. **Body (loaded on trigger):** the `SKILL.md` markdown. Keep it under the **~500-line / ~5,000-token** soft threshold (editorial — official guidance, not code-enforced; a performance/clarity limit, not a hard cap).
3. **Bundled references (loaded on demand):** extra files the body points to.

**One level deep — never nest.** `SKILL.md → reference → content`, never `SKILL.md → A → B → content`. Keep the reference graph flat and grep-able (a code-checkable pattern).

If a skill body is growing past the threshold, split cohesive chunks into bundled references the body links once — do not inline everything.

---

## 6. Body prescriptiveness — "degrees of freedom"

Use Anthropic's own **degrees-of-freedom** framing for how prescriptive a skill body should be:

- **High freedom** (judgment tasks): give general instructions and principles; let the model reason ("think thoroughly", not a rigid step list). Aligns with `prompt-engineering.md` — "prefer general instructions over prescriptive steps".
- **Low freedom** (a bounded, must-not-vary contract): a named multi-step protocol is legitimate — but only as a **skill-internal locked step contract** (`plan-gates.md` sanctions these for narrow-scope skills), never a re-implementation of a plan-mode gate.

Match prescriptiveness to the task: a research skill guides judgment; a `/close`-style checklist can be stepwise because its steps are the contract.

---

## 7. Skills vs. CLAUDE.md vs. sub-agents

- **Skill** = a repeated, multi-step **procedure** (a workflow the AI runs).
- **CLAUDE.md** = **facts + standing instructions** (always-on context), not procedures.
- **Sub-agent (`~/.claude/agents/*.md`)** = a delegated worker with its own `tools:` and `model:` — the production adapter an orchestrator skill dispatches to (§3). A skill that does heavy production should delegate it to a sub-agent, not inline it (`orchestrator-pattern.md`).

---

## 8. Conformance checklist (what a revamped skill is checked against)

A skill conforms to this reference when:

1. Frontmatter carries `name` (dir-matching, valid grammar) + a third-person `description` with **what + when** (§1, §2).
2. **No `model` field** in SKILL.md; delegated model selection is at dispatch / named-agent `model:` (§3).
3. `allowed-tools` (if present) is least-privilege; real tool boundaries live on the named agents (§4).
4. Body is under the ~500-line/~5k-token soft threshold, with references **one level deep** (§5).
5. Body prescriptiveness matches the task's degrees of freedom (§6).
6. Heavy production is delegated to sub-agents, not inlined (§7 + `orchestrator-pattern.md`).
7. Only `name` + `description` are relied on for portability; vendor extensions are advisory (§1).

---

## Open questions (carried, non-blocking)

1. **`disable-model-invocation`** — observed locally (`~/.claude/skills/session-start/SKILL.md`) but not found in any official page. Treat as an undocumented Claude Code extension; do not depend on cross-vendor behavior.
2. **Non-public SKILL.md `model` field** — not found in any official source (see §3). "SKILL.md has no model field" stands until an official source says otherwise.

---

## Consumed by

- `~/.claude/rules/orchestrator-pattern.md` §10 (retrofit checklist item 6 — "conform to skill-authoring.md").
- Every revamped heavy skill — `/close` (S7), `daily-discovery` (S8), then the seed pool — checked against §8.

*Provenance: Slice S7 of `Thoughts/subagent-delegation-context-focus_PLAN.md`, authored from the settled `_SKILLAUTHORING_RESEARCH` (S7 settling pass — no S7a/S7b split needed). Sources: agentskills.io/specification; platform.claude.com Agent Skills best-practices + overview + skills-guide; code.claude.com/docs/en/skills + agent-sdk/skills.*

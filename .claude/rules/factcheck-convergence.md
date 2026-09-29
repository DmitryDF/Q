# Fact-Check Convergence (Canonical Rules)

## Scope

Seven kinds: `workflow`, `plan`, `thought`, `kl_extraction`, `research`, `coverage_check`, `recommendation`.

Five enforcement contexts:
- KL extractions (`check-extraction-gate.sh`, kl_extraction kind)
- KL coverage check (`check-extraction-gate.sh` IS_PASS2 branch, coverage_check kind)
- Plan-mode Gate 3 (`check-plan-gates.sh`, plan kind)
- Pre-planning step-7 + ExitPlanMode runner (thought/plan kind)
- `_RESEARCH.md` files (`factcheck-research-file.sh` dispatcher + `check-research-gate.sh` reader, research kind — Session 1b)
- `/double-check` skill (`~/.claude/skills/double-check/SKILL.md`, recommendation kind, on-demand user-invoked)

## 1. Voting pattern (3 Sonnet checkers, same scope)

Three independent Sonnet checkers each receive the same scope prompt and run the same task independently. All three must agree (0 discrepancies) for a PASS.

Source: Anthropic `building-effective-agents` — "Running the same task multiple times to obtain diverse outputs"; recommended "where multiple perspectives or attempts are needed for higher confidence results."
KL: `~/.claude/rules/code_first_architecture.md:114` — "Code spawns 3 separate AI instances as verification adapters."

Default checker count is 3 Sonnet per canon and `code_first_architecture.md`. The engine accepts
≥1 checker (via the `models` parameter) for on-demand use cases such as the `recommendation` kind;
the 3-checker default is unchanged for all fixed pipelines.

**Research-kind exception — cross-family panel + chain-of-thought (deliberate governance edit, 2026-07-09).**
The `research` kind uses a **cross-family** panel — one **Sonnet** + one **Opus** + one **Haiku**
(`CHECKER_MODELS` in `_factcheck_engine.py`, wired at the `factcheck-research` dispatch) — and its
checker prompt **requires chain-of-thought** before an explicit final `VERDICT:` line. Rationale: a
same-family "3 Sonnet" panel satisfies the *letter* of the voting pattern above but gives ~2 effective
votes because same-family checkers share correlated blind spots; distinct model families decorrelate
the errors (`code_first_architecture.md` independence principle + swappable-adapter / Evolution Test),
and shown reasoning further reduces correlated mistakes. This is a **bounded** exception: it applies to
the `research` kind ONLY. Every other fixed pipeline (`workflow`, `plan`, `thought`, `kl_extraction`,
`coverage_check`) keeps the 3-Sonnet default above, and the `kl_extraction` Sonnet-only provenance rule
(`KL_ALLOWED_CHECKER_MODEL_FAMILY`) is unchanged. True cross-**provider** (non-Anthropic) diversity is a
noted follow-up, not yet built. (Source: research-fc-checker-timeout Discovery `## Q&A` "RESOLVED —
checker-panel governance / topic F", user-approved 2026-07-08; Slice S3 / A5.)

**Plan-kind note — CoT-safe `VERDICT:` token, in-session consumption (plan-validation-engine-consumer, 2026-07-18).**
The `plan` kind also reads the explicit final `VERDICT:` token in `_verdict_bucket` (it is in
`_EXPLICIT_VERDICT_KINDS` alongside `research`) — so a plan checker emits chain-of-thought and then one
authoritative `VERDICT: PASS|DISCREPANCY`, folding the 0G per-axis AND-gate + cross-axis coherence into
that single token. This is NOT the research cross-family panel: plan per-transition checks use 1 Sonnet +
1 Opus and the 0G coherency check uses 3 Sonnet + 1 Opus (unchanged). The distinction from every other
fixed pipeline is *how the plan kind is DRIVEN*: its checkers are dispatched by the `/plan` orchestrator
via the Agent tool in-session (the engine's own `claude --print` dispatch fails in-session), and the
verdict + `R<N>.md` receipt are then computed by code consuming the shared engine primitives
(`aggregate_round_verdict` + `_write_round_file`) through `pre_plan_gates.py factcheck-plan-step` /
`factcheck-plan-coherency` — a *consumer* of the one engine, not a fork. See `plan-gates.md`
"Engine-consumer model + receipt contract".

## 2. Independence (parent-spawned, isolated context)

Each checker is parent-spawned with isolated context: custom system prompt, no shared conversation history, no producer reasoning injected. Subagents cannot spawn other subagents.

Source: Anthropic `code.claude.com/docs/en/sub-agents` — parent-spawned subagents, isolated context, custom system prompts, tool restrictions per subagent, "subagents cannot spawn other subagents."
KL: `~/.claude/rules/code_first_architecture.md:97-102`.

## 3. Subagent type and tools (readonly-checker, read-only)

Checkers use the **`readonly-checker`** named subagent type (`~/.claude/agents/readonly-checker.md`), whose tool grant is exactly `Read`, `Glob`, `Grep` — **no Bash, Write, Edit, or Agent tools**. The framework enforces the named agent's `tools:` grant, so a checker structurally cannot run a shell command or mutate a file.

**Caution — do NOT use the built-in `Explore` type for read-only work.** Despite its name, the built-in `Explore` subagent type's grant **includes Bash** ("all tools except Agent, Artifact, ExitPlanMode, Edit, Write, NotebookEdit"). A prior version of this section wrongly asserted Explore checkers had no Bash; that was false, and on 2026-07-19 an Explore-typed `/double-check` checker used its shell to construct `rm -rf ~/.claude/hooks` (caught only post-hoc by the harness monitor — `Audits/session_issue_audit_20260719_12456fa7.md`). Every in-session read-only checker/verifier now dispatches through `readonly-checker` (which holds no shell) rather than `Explore`. Engine-dispatched checkers (`_factcheck_engine.py`) are already shell-free via `--allowedTools ""` / a populated read-only `--tools` list. See `~/.claude/rules/safe-defaults.md`.

Source: Anthropic `code.claude.com/docs/en/sub-agents` — named-agent `tools:` restrictions (framework-enforced). Built-in `Explore` grant per the harness agent-type listing.

## 4. Stopping condition

- 0 discrepancies → `verdict: PASS`
- ≥1 discrepancy → `verdict: DIRTY`, retry from checker dispatch (round N+1)
- No consensus after `max_rounds` rounds → `verdict: ESCALATE` (human review required)

Engine constant: `FACTCHECK_MAX_ROUNDS = 2`. Default: `FACTCHECK_MAX_ROUNDS = 2` (all kinds). Per-kind overrides (editorial): plan kind uses `max_rounds=3` (user decision 2026-05-14); kl_extraction kind uses `max_rounds=3` (user decision 2026-06-08); research kind uses `max_rounds=3` (user decision 2026-06-15). On ESCALATE, the engine writes `R<N>.md` with `verdict: ESCALATE` and does NOT append a converged marker to the artifact.

### 4a. ESCALATE has TWO origins — no-consensus, and a terminal axis gate (research kind)

The three bullets above describe the **round-driven** origin only. Since the S6/S7 axis
gates shipped (E2c, `26f1a93`, 2026-08-09), the engine emits ESCALATE from a **second,
structurally different** origin, and this section under-described the code until
2026-08-16. Layer-2 documentation must not under-describe Layer-1 behaviour — that is the
rules/code drift this mirror discipline exists to prevent (`code_first_architecture.md`
trust hierarchy).

| Origin | Emitted by | `checker_count` | Meaning |
|---|---|---|---|
| **No consensus** | the round loop, after `max_rounds` | ≥1 (the panel that ran) | Checkers ran and could not converge. |
| **Terminal axis gate** *(research kind only)* | `_run_source_integrity_gate` / `_run_coverage_axis_gate` → `_fold_coverage_result` | **`0`** (`_factcheck_engine.py:1979`) | **Zero checkers ran.** A close-time gate rejected the artifact after the content rounds had already finished. |

**Why the gate is terminal rather than DIRTY.** Both gates run *after* the content rounds
and cannot re-dispatch, so DIRTY "would promise a retry that never comes" (the E2c
rationale, carried verbatim in-comment at both gate sites). ESCALATE is the honest token:
it needs a person, not another round. The source-integrity ESCALATE is additionally **not
operator-OK-able** — there is no accept path for a hallucinated/stale-no-archive source
(`_write_source_integrity_marker` docstring). A gate INCOMPLETE, by contrast, IS
accept-able and is written un-accepted so the close gate blocks until the operator
explicitly accepts.

**Known limitation — the two origins are verdict-indistinguishable.** A gate-origin
ESCALATE marker carries **no `checker_models` key at all**, so a reader comparing only
`verdict:` cannot tell the two apart. The discriminators that DO exist are
`checker_count: 0` plus the origin-specific keys `source_integrity: link_gate` and
`coverage_axis: content_coverage`. **Decision 2026-08-16 — documented, not "fixed":**
emitting `checker_models: []` on a marker where zero checkers ran would be schema noise,
not information (§7 defines `checker_models` as the *resolved family list* — an empty list
asserts nothing), and changing marker schema would be a behaviour change touching every
gate reader, out of proportion to a documentation gap. The durable discriminators already
exist; what is missing is a reader that **asserts** on them, which is carried as a
follow-up rather than closed here.

Source: KL `~/.claude/rules/code_first_architecture.md:116-119` — "0 discrepancies → PASS / ≥1 discrepancy → FAIL, retry from application flow step 2 / No consensus after `max_rounds` rounds (per-kind; see `factcheck-convergence.md` §4) → escalate to human." The axis-gate origin above extends that canon for the research kind; it does not replace it.

## 5. Grading order (code → model → human)

1. Code-based validation (schema, format, value ranges) — before AI checkers
2. Model-based verification (≥1 checkers per §1; default 3 Sonnet for all fixed pipelines)
3. Human calibration — only after ESCALATE or explicit review request

Source: KL `~/.claude/rules/code_first_architecture.md:121-124` — "1. Code-based … / 2. Model-based … (3 checkers) / 3. Human calibration."

## 6. Producer-never-verifies

The producer of an artifact cannot be one of its own checkers. Engine enforces this structurally: parent spawns isolated child processes; no shared context.

Source: KL `~/.claude/rules/code_first_architecture.md:112` — "Producer never verifies its own output. 'Not my job.'"

## 7. Canonical R<N>.md marker schema (schema_version: 3)

Every fact-check round writes an `R<N>.md` artifact with this YAML frontmatter:

```yaml
---
schema_version: 3
kind: workflow | plan | thought | kl_extraction | research | coverage_check | recommendation
verdict: PASS | DIRTY | ESCALATE | BYPASSED
rounds: <N>
checker_count: 3
checker_models: [sonnet, sonnet, sonnet]            # resolved family list — API-attested per checker
checker_models_raw: [claude-sonnet-4-6, ...]        # (kl_extraction only) raw .message.model IDs from each subagent transcript
provenance: code-verified                            # (kl_extraction only) integrity stamp — present when the scribe verified each checker's transcript against the Sonnet allowlist
checked_at: <ISO 8601 UTC>
bypass_reason: "<text>"                              # REQUIRED when verdict is BYPASSED; non-empty after whitespace strip; research kind only
legacy_status_line: "<one-line prose, present only on markers migrated from pre-v2 format>"
---
```

When `verdict: BYPASSED`, the marker MUST carry a non-empty `bypass_reason: <text>` field documenting why the engine could not run (e.g., "all 3 checkers timed out 2026-06-11"). `BYPASSED` is valid for the **research kind only** — plan, workflow, thought, kl_extraction, coverage_check, and recommendation kinds still require `PASS` to converge. Engine writers (`_factcheck_engine.py`) MUST NOT emit `BYPASSED` themselves; `BYPASSED` markers are hand-authored by the operator when the engine genuinely cannot run, and read by gates and by the operator-invoked `write-research-frontmatter` CLI.

Gates that consume this schema must parse the `verdict:` frontmatter field. They must not grep the prose body for convergence evidence.

The `checker_models` value is **API-attested**: for the `kl_extraction` kind, the scribe (`aggregate_kl_extraction_round` in `_factcheck_engine.py`) reads each checker's harness-written subagent transcript (`~/.claude/projects/<sessionId>/subagents/agent-<agentId>.jsonl`), normalizes every assistant turn's `.message.model` to a family slug, hard-fails on any non-Sonnet (or mixed-family) run, and records the resolved family list. Markers without `provenance: code-verified` predate this enforcement (Session 4b checker-model-provenance) and should be treated as unverified. `checker_models_raw` carries the raw IDs for out-of-band audit. For the non-`kl_extraction` kinds, the model list is supplied by the orchestrator (`factcheck_run` `models=` parameter); the `provenance` and `checker_models_raw` fields are not emitted. Per the §1 research-kind exception, a `research` marker's `checker_models` reflects the cross-family panel — e.g. `[sonnet, opus, haiku]` — not the 3-Sonnet default (Slice S3 / A5).

Versioning basis: existing R<N>.md format (engine `_factcheck_engine.py:225-250`) is implicitly v1; `schema_version: 2` is the explicit migration epoch. KL Cockburn evolution test (single change locus) supports a version field over silent drift.

## Consumed by

- `_factcheck_engine.py` — writer (emits R<N>.md per kind)
- `check-extraction-gate.sh` — reader (kl_extraction kind, `verdict: PASS` required)
- `check-extraction-gate.sh` IS_PASS2 branch — reader (coverage_check kind, `verdict: PASS` required before Pass 2)
- `check-plan-gates.sh` Gate 3 — reader (plan kind, `verdict: PASS` required)
- `factcheck-research-file.sh` — dispatcher (PostToolUse Write|Edit; research kind — Session 1b)
- `check-research-gate.sh` — reader (Stop event; research kind — Session 1b)
- Pre-planning runner — reader (thought kind)

## 8. Interior-ellipsis adjacency (N1)

When a quote uses "..." as an interior connector — joining two text fragments —
checkers must verify that both fragments appear adjacent in the original source:
- **Prose sources:** both fragments in the same paragraph
- **Transcript sources:** both fragments in the same speaker turn (transcripts have
  no paragraph structure — speaker turn is the natural adjacency unit)

If fragments are non-adjacent, classify as SPLICE.

Fix Agent contract for SPLICE: split into two separate extraction items, each with
its own source location. Delete if neither fragment has standalone value. Do NOT
re-splice.

Start/end ellipsis (truncation at quote boundary) is NOT a splice and does not
require adjacency verification.

(empirical-grounding: Kamat transcript 2026-05 — non-adjacent fragment splice
survived R2 fact-check; each fragment passed source verification individually but
the combined quote misrepresented the source)
(editorial: "same paragraph / same speaker turn" granularity — no expert source
quantifies adjacency criterion for ellipsis verification)

## 9. Coverage check convergence (N2)

Coverage Check (Phase 1.5 in extraction-pipeline.md) runs through `factcheck_run()`
with `kind="coverage_check"`. Three independent Sonnet checkers each read the Pass 1
evidence file and the original chapter source, reporting any extractable content not
present in the evidence file.
(Uses 3 Sonnet per §1 default; hardcoded in `_write_coverage_check_marker`.)

On PASS: engine writes `_coverage-check-{book_slug}.marker` to `Chapters/` with
`verdict: PASS` in schema_version:2 frontmatter. Gate (`check-extraction-gate.sh`
IS_PASS2 branch) reads this marker. Pass 2 is blocked until marker exists with
`verdict: PASS`.

(empirical-grounding: 9 of 10 books reported `Gap Count: 0` on single-agent
coverage check; independent re-FC found real gaps in all of them)

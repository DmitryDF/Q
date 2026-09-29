---
name: recommend
description: Recommend a single bounded forward move with trade-offs and concerns. Main-session use only.
allowed-tools: Bash, Read, Write, Edit, Agent
---

# /recommend

Surface a single bounded forward move when the user (or an upstream skill) is stuck. Returns one recommendation block with justification, trade-offs, a small test-and-learn experiment, and Concerns (0-3).

---

## Trigger

Explicit invocation only:

- `/recommend`
- `/recommend --help`
- `/recommend --target Thoughts/foo_THOUGHT.md:14-58`
- "what's the best move here"
- "recommend a next step"
- Invoked by `/challenge`'s hard-gate handoff (option a).

**Not auto-triggered.** v1 is callable-only, by user or by another skill/process.

---

## Parameters

All optional (defaults shown):

| Flag | Default | Description |
|------|---------|-------------|
| `--target PATH[:START-END]` | last assistant reply this session | Snapshot context the recommendation operates on |
| `--sonnet N` | 0 | Number of Sonnet proposer agents |
| `--opus N` | 1 | Number of Opus proposer agents |
| `--rounds N` | 1 | Convergence rounds (≥ 1) |

Default: `--sonnet 0 --opus 1 --rounds 1` (single bounded Opus pass, no convergence loop). Minimum 1 proposer total (`--sonnet + --opus ≥ 1`). `--rounds 0` is rejected (floor at 1). **No `--mode` flag at v1.**

---

## Execution

**Step 0 — Help**

If user invoked with `--help` or no target is available (no prior assistant reply and no `--target`), output the Parameters table and stop. Do not proceed.

**Step 1 — Resolve target**

- Default: the last substantial assistant reply in the current session.
- If `--target PATH[:START-END]` supplied: read the file via the Read tool; reject on missing path or out-of-range line span (see Edge Cases E2, E3).
- If target text > 8000 chars and no line range was supplied: abort with the truncation-rule message (see Edge Case E8).

**Step 2 — Write artifact**

Resolve the artifact path (bookkeeping-model audit-trail rule — drafts live under
the owning slug when a topic is bound, else the legacy adhoc location):

```
python3 ${KIT_HOOKS_DIR}/bound_topic.py draft-path <SESSION_ID> RECOMMEND
```

This prints `<project>/Thoughts/<slug>-<ts>_RECOMMEND_<sid8>.md` when a topic is
bound (advisory bucket — slug-grep returns it; it co-retires with the family), or
`~/.claude/state/plan_validation/adhoc/recommend_<sid8>_<ts>.md` for a cold
session. Write the resolved target text to that path using the Write tool. Plain
text only — do not inject producer reasoning.

**Step 3 — Dispatch proposer agents**

Launch `--sonnet + --opus` `readonly-checker` agents in parallel (single message, multiple Agent tool calls).

- `subagent_type: readonly-checker`
- `model: sonnet` or `opus` per allocation
- `description: "Round [R] proposer [i/total] — /recommend"`

**Proposer prompt template:**

```
You are an independent recommender. You have no access to the conversation that produced this artifact.

Your task:
1. Read the artifact at: `[artifact_path]`
2. Produce ONE recommendation block. A recommendation is a single bounded next move — not a list, not a plan, not an analysis.
3. Output structure:
     - recommendation: [one sentence — the move]
     - justification: [why this move, given the artifact]
     - trade-offs: [what is gained vs. lost vs. the most likely alternative]
     - test-and-learn-experiment: [a single-iteration, bounded probe the author can run to validate the move before committing — see Working Identity ch.1–2]
     - concerns: [0–3 specific concerns about your own recommendation — gaps, assumptions, missing data]

Bias:
- Test-and-learn over plan-then-execute (Working Identity ch.1–2). The author is exploring possible selves; recommend a small move, not a binding choice.
- Outside-in / Future-back framing (Hawkins ch.3). Anchor the recommendation in the desired future state and work backward, not in the current frustration.
- Bounded experiments only. No multi-step plans at v1.

Return the structured block only. Do not soften, do not hedge beyond the concerns line.
```

**Step 4 — Synthesize (only when total proposers > 1)**

- **One proposer** (`--sonnet 0 --opus 1`, the default): return that proposer's block verbatim. Skip synthesis.
- **Multiple proposers in one round** (`--sonnet + --opus > 1`, `--rounds 1`): the main session runs a synthesizer pass — read all N recommendation blocks and pick-or-merge into a single canonical recommendation. Record which proposers it drew from inline (e.g. `synthesized from: proposer 1, proposer 3`).
- **`--rounds N>1`:** between rounds, feed the prior-round synthesized recommendation as the seed to the next round's proposer prompts (append: `prior round produced: [synthesized recommendation]`). If a synthesizer can no longer reduce variance after `--rounds`, ESCALATE — surface all candidate recommendations verbatim, grouped by proposer. Do not swallow.

**Step 5 — Emit final block**

Return the single canonical recommendation block (recommendation / justification / trade-offs / test-and-learn-experiment / concerns).

No hard-gate handoff at end of `/recommend` (different from `/challenge`). The user is now unstuck and takes the next action.

---

## Stance

Inline coaching grounding — verbatim quotes:

- **Test-and-learn over plan-then-execute** (Working Identity, ch.2, "The plan-and-implement model encapsulates the conventional wisdom of career counseling: starting by developing a clear picture of what you want."). Ibarra calls plan-then-implement a near-trap (Working Identity, ch.2, "More often than not, it's a recipe for paralysis."). The path forward is action, not introspection (Working Identity, ch.2, "the kind of knowledge we need to make change in our lives is personal and situational; it comes from involvement in a specific context and with specific people, not from solitary introspection or abstract information gleaned from theoretical, general-purpose personality profiles. It can only be acquired by taking action."). Every `/recommend` output therefore includes a single-iteration experiment, not a multi-step plan — the author updates from what happens, then invokes again (Working Identity, ch.1, "the tools at your disposal group into three kinds: experimenting with different possibilities, making new and different connections, and stepping back to make sense of what you are learning along the way.").

- **Possible selves, not the true self** (Working Identity, ch.2, "we are not one true self but many selves and that those identities exist not only in the past and present but also, and most importantly, in the future."). The author is tinkering with a set of possibilities; the recommendation is one bounded probe against one of them (Working Identity, ch.2, "Only by testing do we learn what is really appealing and feasible---and, in the process, create our own opportunities.").

- **Outside-in / Future-back framing** (Leadership Team Coaching, ch.3, **Outside-in** — "starting by asking who the team is there to serve and what their stakeholders need and want from them, rather than starting with the team and only then looking at stakeholders (inside-out)."). Anchor the recommendation in the receiver's need, not the artifact's current frustration. Pair with future-back framing (Leadership Team Coaching, ch.3, **Future-back** — "focusing on what current and 'not-yet' customers and stakeholders will need different in the future, rather than working from 'the past forward' trying to address current problems that arose from the past.").

- **Bounded experiments only.** No iterative agentic loops at v1. One recommendation per invocation; the user (or upstream skill) chooses whether to invoke again.

---

## Constraints

- **Main-session use only.** This skill cannot be invoked from inside an Agent subagent. If detected, fail loudly with: "/recommend is main-session-only — cannot be invoked inside a subagent. Trust Hierarchy exception extends `/double-check`'s pattern; nested orchestration breaks isolation."
- **Trust Hierarchy exception, extending `~/.claude/skills/double-check/SKILL.md:204`.** Skill text orchestrates Agent-tool dispatch because the engine writer is unreachable in-session. Documented exception, not a default pattern.
- **No `R<N>.md` artifact written.** Inherits `/double-check`'s §7 deviation.
- **`--rounds 0` is rejected.** Floor at 1.
- **Concerns (0-3) is the output convention.** No bare 1–10 scores anywhere. Concerns are 0–3 short, specific sentences — each names a gap, assumption, or missing data point. 0 concerns means high confidence; 3 means low. No abstract scores without concerns to back them.
- **Sibling files deferred.** No `-de` / `-ru` sibling at v1.
- **Producer-never-verifies.** Proposers are parent-spawned `readonly-checker` agents with isolated context. (`~/.claude/rules/code_first_architecture.md:112`.)
- **No `--mode` flag at v1.** Single behavior: bounded forward move with test-and-learn bias.
- **No iterative agentic loop at v1.** One recommendation per invocation; no plan-then-execute.
- **No topic-KL routing at v1.** The skill does not look up topic-specific KL sources; the stance KL chapters above are static.
- **Code-enforcement deferred to v2.** Parameter validation, sibling-sync state are skill-text rules at v1. Known Deviation from `code_first_architecture.md:18`; v2 follow-up TODO recorded.

---

## Edge Cases

| # | Case | Detection | Behavior |
|---|------|-----------|----------|
| E1 | Empty target | First message of session, no prior assistant reply, no `--target` | Return error: "No prior assistant reply to recommend against. Provide `--target PATH[:START-END]` or invoke after at least one AI response." Stop. |
| E2 | Invalid target path | Read tool returns missing-file error | Return error naming the failed path. Stop. |
| E3 | Out-of-range line span | Read tool returns line beyond EOF | Return error: "Target `<path>:<lines>` is out of range — file has N lines." Stop. |
| E4 | ESCALATE from internal convergence | Step 4 synthesizer cannot reduce variance after `--rounds` | Surface all candidate recommendations verbatim, grouped by proposer. Do not swallow. |
| E6 | Nested Agent-subagent invocation | Session is itself an Agent-tool subagent | Fail loudly per Constraints. Stop. |
| E7 | Parameter typo | `--rounds 0`; total proposers (`--sonnet + --opus`) is 0; unknown flag | Return error naming the offending flag and the valid floor. Stop. |
| E8 | Long target, no line range | Target text > 8000 chars, no `--target PATH:START-END` | Return error: "Target is N chars (> 8000). Provide `--target PATH:START-END` to scope or re-issue with `--allow-truncate` (v2 — not yet implemented)." Stop. |

(No E5/E9 — `/recommend` has no `RECOMMEND_SKILL` indirection and no hard-gate handoff.)

---

## Examples

### Example 1 — Default single-Opus pass

**User:** `/recommend`

`/recommend` resolves the target to the most recent substantial assistant reply, dispatches one Opus proposer, returns its block verbatim:

> - recommendation: Spend two days instrumenting the slow request path before deciding on caching.
> - justification: The artifact names "slowness" without naming a bottleneck; caching can mask the wrong layer.
> - trade-offs: Adds two days vs. starting caching today; saves potentially rebuilding the cache at the wrong layer.
> - test-and-learn-experiment: Add one tracing decorator to the suspected handler, run it against the last 24 h of production traffic, and read the p95 breakdown before changing any code.
> - concerns: [1] The artifact does not say whether tracing infra exists; the experiment may need a day of prep.

### Example 2 — Multi-proposer with synthesizer pass

**User:** `/recommend --opus 3 --rounds 2`

`/recommend` dispatches three Opus proposers in Round 1 → main-session synthesizer pass picks-or-merges into one block, records which proposers it drew from → Round 2 seeds three fresh Opus proposers with the synthesized block → synthesizer pass again → final block emitted, or ESCALATE with all candidates surfaced if variance did not reduce.

---

## Sibling Skills

- `/challenge` (`~/.claude/skills/challenge/SKILL.md`) — stress-test skill, peer of `/recommend`. Hard-gate handoff option (a) invokes `/recommend`.
- `/double-check` (`~/.claude/skills/double-check/SKILL.md`) — independent factcheck of a recommendation, including outputs of `/recommend`. Run `/double-check` after `/recommend` if the recommendation's stakes warrant verification.
- Future: `-de` / `-ru` sibling skills — deferred until non-English coaching KL exists.

---

*Promoted to global scope: 2026-05-24 — folder-skill rewrite of the legacy flat `recommend-en.md`. Embedded KL grounding inline (Working Identity ch.1, ch.2; Leadership Team Coaching ch.3); removed all vault-relative filesystem references; switched Sibling Skills to global peers. v1 behavior preserved verbatim — parameters, execution steps, edge cases, examples.*

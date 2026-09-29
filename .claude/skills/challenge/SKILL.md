---
name: challenge
description: Stress-test an artifact against AI agree-bias. Returns critiques or open questions plus a hard-gate handoff. Main-session use only.
allowed-tools: Bash, Read, Write, Edit, Agent
---

RECOMMEND_SKILL: /recommend

# /challenge

Stress-test an artifact (last assistant reply, plan, research file, etc.) against AI's tendency to agree. Returns structured critiques or open questions, then offers a hard-gate handoff to a forward move.

---

## Trigger

Explicit invocation only:

- `/challenge`
- `/challenge --help`
- `/challenge --mode surface_oqs`
- `/challenge --mode challenge_concept --target Thoughts/foo_THOUGHT.md:14-58`
- `/challenge --prompt-append "realism, over-commitment, override-soundness, trade-off-honesty"`
- "challenge this"
- "stress-test that artifact"

**Not auto-triggered.** This skill does not activate on idea-proposing language ("let's…", "what if…", "I think we should…"). v1 is callable-only, by user or by another skill/process.

---

## Parameters

All optional (defaults shown):

| Flag | Default | Description |
|------|---------|-------------|
| `--mode {challenge_concept\|surface_oqs}` | `challenge_concept` | What kind of pressure to apply |
| `--target PATH[:START-END]` | last assistant reply this session | Text under review |
| `--sonnet N` | per mode (see below) | Number of Sonnet critique agents |
| `--opus N` | per mode (see below) | Number of Opus critique agents |
| `--rounds N` | 1 | Convergence rounds (≥ 1) |
| `--embed` | off | Suppress the `(a)/(b)/(c)` handoff and append a structured YAML summary block instead. Used when another skill invokes `/challenge` and consumes the output programmatically. Purely additive — default behaviour is unchanged when the flag is absent. |
| `--prompt-append "<focus>"` | unset | Append the focus string to the standard `/challenge` mode-specific prompt so the critique targets the named axis (e.g., `"realism, over-commitment, override-soundness, trade-off-honesty"` in tactical use; `"strategic coherence"` in strategy use). Backwards-compatible — absent flag = today's behaviour (no append, standard mode-specific prompt unchanged). Length-bounded 5–200 characters; values outside the bound are rejected (Edge Case E10). |

**Per-mode defaults:**

- `challenge_concept` — `--sonnet 0 --opus 1 --rounds 1` (one bounded Opus critique)
- `surface_oqs` — `--sonnet 0 --opus 2 --rounds 1` (two Opus agents to widen the open-question set)

Minimum 1 proposer total (`--sonnet + --opus ≥ 1`). `--rounds 0` is rejected (floor at 1).

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
python3 ${KIT_HOOKS_DIR}/bound_topic.py draft-path <SESSION_ID> CHALLENGE
```

This prints `<project>/Thoughts/<slug>-<ts>_CHALLENGE_<sid8>.md` when a topic is
bound (advisory bucket — slug-grep returns it; it co-retires with the family), or
`~/.claude/state/plan_validation/adhoc/challenge_<sid8>_<ts>.md` for a cold
session. Write the resolved target text to that path using the Write tool. Plain
text only — do not inject producer reasoning.

**Step 3 — Dispatch critique agents**

Launch `--sonnet + --opus` `readonly-checker` agents in parallel (single message, multiple Agent tool calls). Each receives a mode-specific prompt.

- `subagent_type: readonly-checker`
- `model: sonnet` or `opus` per allocation
- `description: "Round [R] critic [i/total] — /challenge [mode]"`

**`challenge_concept` prompt template:**

```
You are an independent critic. You have no access to the conversation that produced this artifact.

Your task:
1. Read the artifact at: `[artifact_path]`
2. Produce 1–5 critiques. Each critique must be a flaw in the artifact's reasoning, evidence, or design — not a stylistic note.
3. For each critique, output:
     - claim: [exact phrasing of what the artifact asserts]
     - weakness: [why it is wrong, weak, or unsupported]
     - trade-off: [what is gained vs. lost if the artifact were revised to fix this]
4. End your reply with:
     concerns: [0–3 specific concerns about your own critique — gaps, assumptions, missing data]

Bias: stay curious longer (Advice Trap ch.4). Be real, not nice (Co-Active ch.2). Surface the foggy-fier, do not be one (Advice Trap ch.5).

Do not propose solutions. Do not soften. Return the structured block only.
```

**`surface_oqs` prompt template:**

```
You are an independent critic surfacing open questions. You have no access to the conversation that produced this artifact.

Your task:
1. Read the artifact at: `[artifact_path]`
2. Produce 1–5 open questions the artifact has not answered. Each must be answerable in principle and must change a decision in the artifact if answered.
3. For each question, output:
     - question: [the question, as asked of the author]
     - why-it-matters: [which decision in the artifact pivots on the answer]
     - one-trade-off: [the cost of answering vs. the cost of not answering]
4. End your reply with:
     concerns: [0–3 specific concerns about your own questions]

Bias: forward and deepen (Co-Active ch.6). NCRW + Designed Alliance (Co-Active ch.1) — the artifact's author is whole, treat the gap as the author's to close.

Return the structured block only.
```

**Append focus (`--prompt-append`)**

If `--prompt-append "<focus>"` is set, append the focus string verbatim to the mode-specific prompt template ABOVE, after the existing template body, separated by one blank line and prefixed with:

```
Focus this critique on: <focus>
```

The append is purely additive. Validate the focus string is 5–200 characters (see Edge Case E10); reject outside the bound BEFORE dispatching agents. When the flag is absent, the prompt template is used verbatim — no append, no behavioural change.

Use cases (informational, not exhaustive):

**Step 4 — Aggregate and converge**

- With `--rounds 1` (default): deduplicate identical items across critics and present the union, grouped by critic.
- With `--rounds N>1`: run additional rounds where each round dispatches fresh agents seeded with the prior-round outputs in their prompts (focus on consolidation, not duplication).
- If rounds reach `--rounds` without convergence (critics still disagree): ESCALATE — surface all candidate critiques / OQs verbatim, grouped by proposer. Do not swallow.

**Step 5 — Handoff or embed summary**

Two paths, selected by the `--embed` flag.

**Default path (no `--embed`) — hard-gate handoff.** After presenting critiques / OQs, end the reply with exactly:

> Pick one:
> (a) Invoke **{RECOMMEND_SKILL}** to surface a forward move.
> (b) Share your own next step.
> (c) End.

`{RECOMMEND_SKILL}` is replaced with the literal value from the indirection line at the top of this file (currently `/recommend`). If the indirection target does not exist, see Edge Case E5.

**Embed path (`--embed` set) — structured summary, no handoff.** Suppress the `(a)/(b)/(c)` block entirely. After the critique / OQ blocks, append a YAML summary fenced by literal marker lines `--- challenge-embed-summary ---` and `--- end ---`:

```yaml
--- challenge-embed-summary ---
mode: surface_oqs | challenge_concept
target: <resolved path>
rounds: N
items_total: M
items_per_critic:
  - critic: <model><index>      # e.g. opus1, sonnet2
    count: K
artifact_path: <bound: <project>/Thoughts/<slug>-<ts>_CHALLENGE_<sid8>.md | cold: ~/.claude/state/plan_validation/adhoc/challenge_<sid8>_<ts>.md>
--- end ---
```

Field contract:

- `mode` — resolved `--mode` value.
- `target` — resolved target path, or the literal token `<last-assistant-reply>` if defaulted (no `--target`).
- `rounds` — resolved `--rounds` after floor enforcement.
- `items_total` — count of critique items (or OQs) across all critics after Step 4 deduplication.
- `items_per_critic` — one row per dispatched critic, in dispatch order. `critic` is `<model><index>` (e.g., `opus1`, `sonnet2`); `count` is that critic's contribution to `items_total` after dedup.
- `artifact_path` — the absolute path written in Step 2.

The YAML block is the last content in the reply when `--embed` is set. No `(a)/(b)/(c)` handoff is shown, and the calling skill is responsible for next-step routing.

---

## Stance

Inline coaching grounding — verbatim quotes:

- **Stay curious longer** (Advice Trap, ch.4, "Stay curious a little longer. Rush to action and advice-giving a little more slowly."). Resist the urge to leap to advice or repair after the first critique; one more probing question almost always sharpens the next one. Stanier also names the habit directly (Advice Trap, ch.4, "you can't be more coach-like if you're not being curious. While I want you to be lazy, I also want you to work really hard at staying curious and managing the process of the conversation.").

- **Spot the foggy-fier** (Advice Trap, ch.5, "There are six ways that people trip themselves up and fail to uncover the real challenge. These are the Foggy-fiers: patterns of conversation that stop you getting clear on what matters."). The artifact's haziest sentence is usually the load-bearing one; name the fog rather than fill it in. Stanier frames the reframe (Advice Trap, ch.5, "You can be known as the person who helps articulate the critical issue or as the person who provides hasty answers to solve the wrong problem.").

- **NCRW + Designed Alliance** (Co-Active Coaching, ch.1, "People are Naturally Creative, Resourceful, and Whole"). The author of the artifact is whole; the critic surfaces gaps; closing them is the author's work (Co-Active Coaching, ch.1, "They are capable: capable of finding answers, capable of choosing, capable of taking action, capable of recovering when things don't go as planned, and, especially, capable of learning."). Power belongs to the relationship, not the critic (Co-Active Coaching, ch.1, "In co-active coaching, power is granted to the coaching relationship, not to the coach.").

- **Real, not nice** (Co-Active Coaching, ch.2, "A real relationship is not built on being nice; it's built on being real."). Saying the hard thing kindly beats softening it into noise. The frame allows challenge without harm (Co-Active Coaching, ch.2, "safe does not necessarily mean comfortable").

- **Forward & Deepen** (Co-Active Coaching, ch.6, "a second outcome, which is complementary and just as important as action, is learning."). Every critique or open question should either move the work forward or deepen the author's understanding of the problem — never both at once, never neither (Co-Active Coaching, ch.6, "it's this cycle of action and learning over time that leads to sustained and effective change.").

---

## Constraints

- **Main-session use only.** This skill cannot be invoked from inside an Agent subagent. If detected (no main-session id available, or harness subagent flag set), fail loudly with: "/challenge is main-session-only — cannot be invoked inside a subagent. Trust Hierarchy exception extends `/double-check`'s pattern; nested orchestration breaks isolation."
- **Trust Hierarchy exception, extending `~/.claude/skills/double-check/SKILL.md:204`.** Skill text orchestrates Agent-tool dispatch because the engine writer is unreachable in-session. Documented exception, not a default pattern.
- **No `R<N>.md` artifact written.** Inherits `/double-check`'s §7 deviation — engine writer unreachable in-session.
- **`--rounds 0` is rejected.** Floor at 1. "No convergence" is `--rounds 1` (single pass); 0 would be a no-op and a likely typo.
- **Concerns (0-3) is the output convention.** No bare 1–10 scores anywhere in critique output. Concerns are 0–3 short, specific sentences — each names a gap, assumption, or missing data point. 0 concerns means high confidence; 3 means low. No abstract scores without concerns to back them.
- **Sibling files deferred.** No `-de` / `-ru` sibling at v1 — non-English coaching KL does not exist yet.
- **Producer-never-verifies.** Critics are parent-spawned `readonly-checker` agents with isolated context; the producer of the target does not critique it. (`~/.claude/rules/code_first_architecture.md:112`.)
- **Code-enforcement deferred to v2.** Parameter validation, handoff state, sibling-sync state are skill-text rules at v1. Known Deviation from `code_first_architecture.md:18`; v2 follow-up TODO recorded.

---

## Edge Cases

| # | Case | Detection | Behavior |
|---|------|-----------|----------|
| E1 | Empty target | First message of session, no prior assistant reply, no `--target` | Return error: "No prior assistant reply to challenge. Provide `--target PATH[:START-END]` or invoke after at least one AI response." Stop. |
| E2 | Invalid target path | Read tool returns missing-file error | Return error naming the failed path. Stop. |
| E3 | Out-of-range line span | Read tool returns line beyond EOF | Return error: "Target `<path>:<lines>` is out of range — file has N lines." Stop. |
| E4 | ESCALATE from internal convergence | Step 4 returns no consensus after `--rounds` | Surface all candidate critiques / OQs verbatim, grouped by proposer. Do not swallow. |
| E5 | `RECOMMEND_SKILL` not found | Hard-gate handoff time; sibling skill missing or renamed without indirection update | Show all three handoff options but tag option (a) with `[NOT FOUND — set RECOMMEND_SKILL or pick (b)/(c)]`. **Moot under `--embed`** — no handoff is shown, so the indirection target is not consulted. |
| E6 | Nested Agent-subagent invocation | Session is itself an Agent-tool subagent | Fail loudly per Constraints. Stop. |
| E7 | Parameter typo | `--mode` not in enum; `--rounds 0`; total proposers (`--sonnet + --opus`) is 0 | Return error naming the offending flag and the valid enum / floor. Stop. |
| E8 | Long target, no line range | Target text > 8000 chars, no `--target PATH:START-END` | Return error: "Target is N chars (> 8000). Provide `--target PATH:START-END` to scope or re-issue with `--allow-truncate` (v2 — not yet implemented)." Stop. |
| E9 | Abandoned handoff | User does not pick (a)/(b)/(c) within one prompt cycle | Skill exits silently. No blocking state retained. **Moot under `--embed`** — no handoff is shown, so there is nothing to abandon. |
| E10 | `--prompt-append` value outside 5–200 chars | Detected at parameter validation, BEFORE dispatch | Return error: `"--prompt-append must be 5–200 characters; got N"`. Stop. Do not dispatch agents. Absent flag is not an error. |

---

## Examples

### Example 1 — Default mode on last assistant reply

**User:** `/challenge`

`/challenge` resolves the target to the most recent substantial assistant reply, dispatches one Opus critic (`challenge_concept` default), and returns:

> **Critique 1 (Opus)**
> - claim: "Caching will solve the slowness."
> - weakness: No measurement of which operation is slow; "slowness" is unscoped.
> - trade-off: A scoped measurement adds a day of work but prevents caching the wrong path.
>
> concerns: [1] No baseline latency was offered in the artifact.
>
> Pick one:
> (a) Invoke **/recommend** to surface a forward move.
> (b) Share your own next step.
> (c) End.

### Example 2 — Surface open questions against a specific artifact

**User:** `/challenge --mode surface_oqs --target Thoughts/foo_THOUGHT.md:14-58`

`/challenge` reads lines 14–58 of the Thoughts file, dispatches two Opus critics, deduplicates, and returns 1–5 open questions per critic with a concerns line — then the same three-option handoff.

### Example 3 — Handoff sub-flow

After Example 1:

- User picks **(a)** → control passes to `/recommend` (which reads the same target by default).
- User picks **(b)** → user types their own next step; `/challenge` exits.
- User picks **(c)** → `/challenge` exits silently.

If the user provides no response within one prompt cycle, see Edge Case E9.

### Example 5 — Focus the critique with `--prompt-append`

**User:** `/challenge --prompt-append "realism, over-commitment, override-soundness, trade-off-honesty"`

`/challenge` resolves the target (last assistant reply), dispatches one Opus critic with the mode-specific `challenge_concept` template + the appended focus line, and returns critiques aimed at the four named axes:

> **Critique 1 (Opus) — focused on realism, over-commitment, override-soundness, trade-off-honesty**
> - claim: "All five items can ship in the 2-week window."
> - weakness: Capacity math implies one senior fully on auth-rewrite (1.5w) PLUS checkout (0.5w) = no slack for review/rollout — over-commitment.
> - trade-off: Trimming to 4 items gains realistic delivery; loses the optionality of pulling forward the dashboard work.
>
> concerns: [1] No prior throughput data was offered in the artifact.
>
> Pick one: …

Absent `--prompt-append`, the same invocation produces the unfocused default `challenge_concept` critique.

### Example 4 — Embedded invocation from another skill

**Caller (e.g. `/clarification` step 5):** `/challenge --mode surface_oqs --embed --target ~/.claude/state/clarification/<sid>/idea-and-own-words.md --opus 2 --rounds 1`

`/challenge` dispatches two Opus critics, deduplicates, and returns the OQ blocks per critic followed by the YAML summary — no `(a)/(b)/(c)` handoff. The caller parses the summary, persists `artifact_path` in its own state, and decides the next step itself.

> **OQ block — Opus 1**
> - question: …
> - why-it-matters: …
> - one-trade-off: …
>
> concerns: …
>
> **OQ block — Opus 2**
> - question: …
> - why-it-matters: …
> - one-trade-off: …
>
> concerns: …
>
> ```yaml
> --- challenge-embed-summary ---
> mode: surface_oqs
> target: ~/.claude/state/clarification/<sid>/idea-and-own-words.md
> rounds: 1
> items_total: 7
> items_per_critic:
>   - critic: opus1
>     count: 4
>   - critic: opus2
>     count: 3
> artifact_path: <bound: <project>/Thoughts/<slug>-<ts>_CHALLENGE_<sid8>.md | cold: ~/.claude/state/plan_validation/adhoc/challenge_<sid8>_<ts>.md>
> --- end ---
> ```

---

## Sibling Skills

- `/recommend` (`~/.claude/skills/recommend/SKILL.md`) — forward-move skill, peer of `/challenge`. Hard-gate handoff option (a) invokes `/recommend`.
- `/double-check` (`~/.claude/skills/double-check/SKILL.md`) — independent factcheck of a recommendation; orthogonal use case.
- Future: `-de` / `-ru` sibling skills — deferred until non-English coaching KL exists.

---

*Promoted to global scope: 2026-05-24 — folder-skill rewrite of the legacy flat `challenge-mode-en.md`. Embedded KL grounding inline (Advice Trap ch.4, ch.5; Co-Active Coaching ch.1, ch.2, ch.6); removed all vault-relative filesystem references; switched Sibling Skills to global peers. v1 behavior preserved verbatim — parameters, execution steps, edge cases, examples.*

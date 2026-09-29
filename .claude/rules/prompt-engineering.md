# Prompt Engineering — Anthropic Reference

Synthesized from official Anthropic documentation. Single living reference for prompt engineering across all current Claude models (Fable 5.1 / Mythos 5.1, Fable 5 / Mythos 5, Opus 5, Opus 4.8 / 4.7 / 4.6, Sonnet 5 / 4.6, Haiku 4.5).

## Sources

- S1: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- S2: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/overview (planning/when-to-tune; the techniques live on S1)
- S3: https://platform.claude.com/docs/en/build-with-claude/effort (effort levels + per-model defaults; per-message effort beta)
- S4: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5
- S5: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-sonnet-5
- S6: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5
- S7: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-4-8
- S8: https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5-1

Fetched: 2026-09-22 (prior: 2026-08-13, 2026-07-13, 2026-05-14).

**Note on the URL path:** the canonical prefix is `platform.claude.com/docs/en/build-with-claude/…`. The doubled `/docs/en/docs/build-with-claude/…` form used in earlier revisions of this file still resolves but is not canonical.

**What changed since 2026-08-13:**
- **Claude Fable 5.1 / Mythos 5.1 shipped** and have their own prompting page (S8). The per-model page set is now **five** (Fable 5.1, Fable 5, Sonnet 5, Opus 5, Opus 4.8). Fable 5 prompts carry over unchanged; a dozen behaviours differ and get their own section below. Two are the *opposite* of the prior generation: Fable 5.1 writes **fewer** user-facing updates between tool calls (Opus 5 narrates readily) and **formats less** in chat (earlier models over-formatted) — carried-over "hold all findings" / anti-formatting rules should be removed.
- **Append-only conversation history is now enforced, not advised** — for accounts created on or after 2026-08-31, a Fable 5.1 thinking block replayed after its prefix changed returns a **400** (or is dropped under a beta flag). Per-turn reminders go in **turn-scoped system messages**, instruction/tool changes in **mid-conversation system messages**; never rewrite `system`/`tools` or summarize earlier turns in place. Migration item 7 on S1.
- **Per-message effort (beta)** on Fable 5.1 / Mythos 5.1 / Opus 5 changes effort mid-conversation **without** invalidating the prompt cache (header `mid-conversation-output-config-2026-07-01`); Fable 5 returns 400. The "hold top-level effort constant in a cached session" rule still applies to every other model.
- **Progress updates come back as `thinking` blocks** on Fable 5.1 and are empty under the default `thinking.display: "omitted"` — set `display: "updates"` (beta `thinking-display-updates-2026-08-18`) before prompting for more narration.
- Opus 5 subagent caps are named: `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, SDK `max_budget_usd` (Claude Code ≥ 2.1.217).
- Computer use: Sonnet 5 and Opus 4.8 support the `computer_toolset_20260801` toolset and the `browser_toolset_20260801` browser tool alongside `computer_20251124`.
- Effort page now carries an Opus 4.7 per-level table and links a **task budgets** feature (advisory token budget for a whole agentic loop).

---

## Model-specific pages

The shared techniques below apply to all current models. Where a specific model behaves differently, consult its page before tuning:

- **Prompting Claude Fable 5.1** (and Mythos 5.1) — S8. Effort sweep, progress updates, tool-call batching, append-only history, writing density, formatting in chat, quoting sources, finishing the task, compaction summaries, scoped changes/tests, search at low effort, safeguard false positives, targeted edits, long outputs, lead-agent concurrency, vision.
- **Prompting Claude Fable 5** (and Mythos 5) — S6. Effort, instruction following, long-run progress claims, memory systems, send-to-user tool, the `reasoning_extraction` refusal category.
- **Prompting Claude Sonnet 5** — S5. Response length, effort/thinking calibration, tool-use triggering, literal instruction following, code-review harnesses, design defaults.
- **Prompting Claude Opus 5** — S4. Response verbosity, agentic narration, written-deliverable length, task scope + over-verification, subagent control, self-correction, thinking-disabled artifacts.
- **Prompting Claude Opus 4.8** — S7. Response length, effort calibration, tool-use triggering, literal instruction following, subagent control, design/frontend defaults.

---

## General Principles

### Be clear and direct (S1)

"Claude responds well to clear, explicit instructions. Being specific about your desired output can help enhance results."

If you want "above and beyond" behavior, **explicitly request it** rather than relying on the model to infer it from a vague prompt (e.g. "Include as many relevant features and interactions as possible. Go beyond the basics.").

**Golden rule:** "Show your prompt to a colleague with minimal context on the task and ask them to follow it. If they'd be confused, Claude will be too."

- Be specific about the desired output format and constraints.
- Provide instructions as sequential steps (numbered/bulleted) when order or completeness matters.
- Think of Claude as a brilliant but new employee who lacks context on your norms and workflows.

### Add context to improve performance (S1, S6)

"Providing context or motivation behind your instructions, such as explaining to Claude why such behavior is important, can help Claude better understand your goals and deliver more targeted responses." Claude generalizes from the explanation — e.g. "never use ellipses, since a text-to-speech engine can't pronounce them" beats a bare "NEVER use ellipses."

Positive examples of the desired behavior tend to be more effective than negative "do not" instructions.

Fable 5 makes this explicit for long-running agents — give the reason, not only the request:
```text
I'm working on [the larger task] for [who it's for]. They need [what the output enables]. With that in mind: [request].
```

### Use examples effectively (S1)

"Examples are one of the most reliable ways to steer Claude's output format, tone, and structure."

Make examples:
- **Relevant:** mirror your actual use case closely.
- **Diverse:** cover edge cases and vary enough that Claude doesn't pick up unintended patterns.
- **Structured:** wrap in `<example>` tags (multiple in `<examples>`) so Claude distinguishes them from instructions.

Include 3–5 examples for best results. You can also ask Claude to evaluate your examples for relevance/diversity or generate more.

### Structure prompts with XML tags (S1)

"XML tags help Claude parse complex prompts unambiguously, especially when your prompt mixes instructions, context, examples, and variable inputs." Wrap each content type in its own tag (`<instructions>`, `<context>`, `<input>`).

- Use consistent, descriptive tag names.
- Nest tags when content has a natural hierarchy (documents inside `<documents>`, each inside `<document index="n">`).

### Give Claude a role (S1)

"Setting a role in the system prompt focuses Claude's behavior and tone for your use case. Even a single sentence makes a difference."

### Long context prompting (S1)

For large inputs (20k+ tokens):
- **Put longform data at the top:** place long documents above the query, instructions, and examples. "Queries at the end can improve response quality by up to 30 percent in tests," especially with complex multi-document inputs.
- **Structure document content with XML tags:** wrap each document in `<document>` with `<document_content>` and `<source>` (plus other metadata) subtags.
- **Ground responses in quotes:** for long-document tasks, ask Claude to quote the relevant parts first (in `<quotes>` tags) before carrying out the task — this cuts through the noise of the rest.

### Model self-knowledge (S1)

To make Claude identify itself or emit a correct model string in an app:

```text
The assistant is Claude, created by Anthropic. The current model is Claude Opus 5.
```
```text
When an LLM is needed, please default to Claude Opus 5 unless the user requests
otherwise. The exact model string for Claude Opus 5 is claude-opus-5.
```

---

## Output and Formatting

### Communication style and verbosity (S1)

Current models are "more direct and grounded, more conversational, less verbose" — they may skip verbal summaries after tool calls and jump to the next action. To restore visibility:

```text
After completing a task that involves tool use, provide a quick summary of the work you've done.
```

**Two models pull in opposite directions.** Claude Opus 5's default user-facing responses run *longer* than prior models', and "raising or lowering effort does not reliably change visible response length" — prompt explicitly for conciseness (Opus 5 section below). Claude Fable 5.1 "writes fewer user-facing updates between tool calls" during agentic work — ask for progress text explicitly and remove any instruction telling it to keep that text brief (Fable 5.1 section below).

### Control format of responses (S1, S8)

1. **Tell Claude what to do, not what not to do** — instead of "Do not use markdown," try "Your response should be composed of smoothly flowing prose paragraphs."
2. **Use XML format indicators** — "Write the prose sections of your response in `<smoothly_flowing_prose_paragraphs>` tags."
3. **Match your prompt style to the desired output** — removing markdown from your prompt reduces markdown in the output.
4. **Use detailed prompts for specific formatting preferences.** For heavy markdown suppression, S1 offers an `<avoid_excessive_markdown_and_bullet_points>` block: write flowing prose, reserve markdown for `inline code`, code blocks, and simple `##`/`###` headings, avoid **bold**/*italics* and gratuitous lists, and never output a series of overly short bullet points.

**Not on Fable 5.1.** It "already formats less than earlier models, so on that model a block like this can suppress structure the content needs." Remove it, or replace it with a rule that says when formatting *is* appropriate:
```text
Use lists and bullet points when asked to, or when the content is multifaceted enough that they help with clarity. If the person explicitly requests minimal formatting, always format your responses without bullet points, headers, lists, or bold emphasis, as requested. In conversational, personal, or emotional exchanges, keep to plain prose.
```

### LaTeX output (S1)

Current models default to LaTeX for math/technical expressions. To force plain text, instruct explicitly: "Do not use LaTeX, MathJax, or markup such as `\( \)`, `$`, or `\frac{}{}`. Write math with standard text characters (`/`, `*`, `^`)."

### Document creation (S1)

Current models produce presentations/animations/visual documents with strong instruction following, usually usable on the first try. Ask for design intent explicitly: "Include thoughtful design elements, visual hierarchy, and engaging animations where appropriate."

### Prefilled responses no longer supported (S1)

Starting with Claude 4.6 models (and Claude Mythos Preview), prefilling a partial assistant message on the **last** assistant turn returns a 400 error. Earlier models still support it; assistant messages elsewhere in the conversation are unaffected. Migration paths:
- **Controlling format:** use Structured Outputs, or just ask the model to conform to the schema (newer models match complex schemas reliably, especially with retries). For classification, use a tool with an enum field or structured outputs.
- **Eliminating preambles:** "Respond directly without preamble. Do not start with phrases like 'Here is...', 'Based on...', etc." — or emit within XML tags / via tool calling; strip stray preambles in post-processing.
- **Avoiding bad refusals:** no longer needed — clear prompting in the `user` message suffices.
- **Continuations:** move to the user message: "Your previous response was interrupted and ended with `[previous_response]`. Continue from where you left off." Or just retry.
- **Context hydration / role consistency:** inject former prefilled reminders into the user turn, or hydrate via tools / context compaction for agentic systems.

---

## Tool Use

### Be explicit about actions (S1)

Current models are trained for precise instruction following and benefit from explicit direction. "Can you suggest some changes" may yield only suggestions; to take action be direct — "Change this function to improve its performance" / "Make these edits to the authentication flow."

To make Claude proactive by default:
```text
<default_to_action>
By default, implement changes rather than only suggesting them. If the user's intent is
unclear, infer the most useful likely action and proceed, using tools to discover any
missing details instead of guessing. Try to infer the user's intent about whether a tool
call (e.g., file edit or read) is intended or not, and act accordingly.
</default_to_action>
```

To make Claude conservative by default (info/research over action):
```text
<do_not_act_before_instructions>
Do not jump into implementation or change files unless clearly instructed to make changes.
When the user's intent is ambiguous, default to providing information, doing research, and
providing recommendations rather than taking action. Only proceed with edits, modifications,
or implementations when the user explicitly requests them.
</do_not_act_before_instructions>
```

### Dial back over-trigger language (S1)

Opus 4.5/4.6 are more responsive to the system prompt than earlier models, so prompts written to fix *under*-triggering can now *over*-trigger. Replace "CRITICAL: You MUST use this tool when…" with plain "Use this tool when…".

### Optimize parallel tool calling (S1, S8)

Current models run independent tool calls in parallel (speculative searches, reading several files at once, parallel bash). To push to ~100% parallel efficiency:
```text
<use_parallel_tool_calls>
If you intend to call multiple tools and there are no dependencies between the tool calls,
make all of the independent tool calls in parallel. Prioritize calling tools simultaneously
whenever the actions can be done in parallel rather than sequentially. For example, when
reading 3 files, run 3 tool calls in parallel to read all 3 files into context at the same
time. Maximize use of parallel tool calls where possible to increase speed and efficiency.
However, if some tool calls depend on previous calls to inform dependent values like the
parameters, do NOT call these tools in parallel and instead call them sequentially. Never
use placeholders or guess missing parameters in tool calls.
</use_parallel_tool_calls>
```
To reduce parallelism: "Execute operations sequentially with brief pauses between each step to ensure stability."

**Fable 5.1 in agent loops** issues parallel calls when the request names several things, but in coding / bash-and-editor / computer-use loops — where the next independent calls are *implied* — it may issue them one per turn. Send this one-sentence nudge as a **turn-scoped system message** after each round of tool results (beta header `mid-conversation-system-clear-at-2026-08-21`; without the beta, put it in a text block after the `tool_result` blocks), appending a fresh copy each turn and leaving earlier copies in place byte-for-byte:
```text
First privately list what you need next; then request every item that doesn't depend on another's result in this one response.
```

---

## Thinking and Reasoning

### Adaptive thinking + per-model defaults (S1, S3)

Claude 4.6 and later models use **adaptive thinking** (`thinking: {type: "adaptive"}`), where Claude dynamically decides when and how much to think based on the `effort` parameter and query complexity. "In internal evaluations, adaptive thinking reliably drives better performance than extended thinking."

**What happens when you omit the `thinking` parameter — this differs by model:**

| Model | Thinking when `thinking` omitted | Can it be disabled? |
|-------|----------------------------------|---------------------|
| Fable 5.1 / Mythos 5.1, Fable 5 / Mythos 5 | **always on** (adaptive is the only mode) | no |
| Opus 5 | **on** | only at effort `high` or below (`xhigh`/`max` + disabled → 400) |
| Sonnet 5 | **on** | yes (`thinking: {type: "disabled"}`) |
| Opus 4.6 / 4.7 / 4.8, Sonnet 4.6 | **off** | n/a (set `adaptive` to enable) |

Use adaptive thinking for agentic workloads: multi-step tool use, complex coding, long-horizon loops.

To steer thinking frequency down (helps with large/complex system prompts):
```text
Thinking adds latency and should only be used when it will meaningfully improve
answer quality - typically for problems that require multistep reasoning. When in
doubt, respond directly.
```

**Watch `max_tokens`.** It is a hard limit on *total* output — thinking plus response text. At `high`/`xhigh`/`max`, leave headroom or you may get a response that is almost entirely thinking followed by a truncated answer and `stop_reason: "max_tokens"`.

**Fable 5.1 at `xhigh`/`max` on long deliverables** can draft the whole deliverable in thinking and then write it again as the reply. Prefer `high`; if you must run higher, set `max_tokens` for thinking *and* reply, and append to the user message (replace `[max_tokens]` with the request's real value):
```text
Everything produced in one reply, including any reasoning or drafting done before the reply, counts toward a single limit of about [max_tokens] tokens. If that limit is reached before the reply is finished, the person receives a cut-off response and has to start over. Composing an entire output or deliverable in full as reasoning and then again as a reply would double the length of the turn without improving the result, so don't do that.

Instead, when the person has asked for a long or effort-intensive deliverable such as a multi-section document, a large table or dataset, or a complete code file, spend extra effort on understanding the request, checking the inputs the answer depends on, settling the structure and other difficult decisions, and otherwise using the reasoning space to reason and the output space to write an output. Usually it is not needed to draft an output multiple times.
```

### `budget_tokens` is deprecated / removed (S1, S3)

`budget_tokens` (manual extended thinking) is still functional but **deprecated** on Opus 4.6 and Sonnet 4.6, and **returns a 400 error on Claude 4.7 and later models** (Opus 4.7, Opus 4.8, Opus 5, Sonnet 5, Fable 5/5.1, Mythos 5/5.1). Opus 4.5 still uses manual thinking — it is the only extended-thinking-only model that also supports `effort`. Prefer lowering `effort`, or use `max_tokens` as a hard output ceiling with adaptive thinking.

### Effort parameter (S3)

Passed under `output_config: {effort: "..."}`. Affects **all** response tokens (text, tool calls, thinking) — not just thinking — so it works even without thinking enabled; lower effort also means fewer and terser tool calls. **API default is `high`**; setting `high` is identical to omitting the parameter. It is "a behavioral signal, not a strict token budget."

Do **not** pass `adaptive` as an effort value — `adaptive` is a thinking mode, not an effort level.

| Level | Use case | Availability |
|-------|----------|--------------|
| `max` | Deepest possible reasoning; no token constraint. | Fable 5.1, Mythos 5.1, Fable 5, Mythos 5, Mythos Preview, Opus 5, Opus 4.8/4.7/4.6, Sonnet 5/4.6 |
| `xhigh` | Long-running agentic/coding (30 min+), million-token budgets. | Fable 5.1, Mythos 5.1, Fable 5, Mythos 5, Opus 5, Opus 4.8/4.7, Sonnet 5 |
| `high` (default) | Complex reasoning, difficult coding, agentic tasks. | all |
| `medium` | Balanced speed/cost/performance. | all |
| `low` | Most efficient; simpler tasks, subagents, latency-sensitive. | all |

Per-model defaults and starting points (the per-model guidance overrides the table where they differ):
- **Fable 5.1 / Mythos 5.1:** start `high`; test every level against your own evals, and **re-run the sweep even if you ran one on Fable 5** — level names don't mean the same amount of thinking across models. Gains over Fable 5 are largest at the higher settings; `medium` roughly matches Fable 5 at lower cost; `low` is often competitive with Opus/Sonnet on cost per task while scoring higher, so include it wherever you'd otherwise run a smaller model at a higher effort.
- **Fable 5 / Mythos 5:** effort is the primary intelligence/latency/cost control; start `high`, `xhigh` for the most capability-sensitive work, `medium`/`low` for routine (lower settings often beat prior models' `xhigh`). Reduce effort if a task completes but takes longer than necessary.
- **Opus 5:** start at the default `high`; step up to `xhigh` for demanding coding/agentic work, `max` when the task justifies unconstrained spend. Use `low`/`medium` **liberally** as the primary cost/latency control wherever evals show quality holds. If you carried effort settings over from an earlier model, **re-run a fresh effort sweep**. Effort controls thinking volume, **not** visible response length here.
- **Opus 4.8 / 4.7:** start `xhigh` for coding/agentic, `high` as the minimum for intelligence-sensitive work, step down only when evals show quality holds. Opus 4.7's per-level table: `low` for short scoped tasks (pair with checklists), `medium` the drop-in for the average workflow, `high` often the best quality/token balance, `xhigh` the recommended start for coding/agentic/exploratory work, `max` reserved for frontier problems (can overthink on structured-output tasks). "If you observe shallow reasoning on complex problems, raise effort rather than prompting around it."
- **Sonnet 5:** defaults `high`; `xhigh` for the hardest coding/agentic; `medium` ≈ Sonnet 4.6 at high, and `high` ≈ Sonnet 4.6 at max; `low` for chat/high-volume. When benchmarking across models, match by *observed thinking length*, not effort name.
- **Sonnet 4.6:** defaults `high` but set effort explicitly to avoid latency surprises; `medium` is the recommended everyday default.

**`max_tokens` headroom is per-family — the 64k figure is Opus-specific.** On **Opus 4.7 / 4.8 / 5** at `xhigh` or `max`: "Starting at 64k tokens and tuning from there is a reasonable default." On **Fable 5.1 / Fable 5** set a large `max_tokens` at **`high` and above** (no figure given). On **Sonnet 5** leave headroom at `high`/`xhigh`/`max`. Do not generalize the Opus number across families.

**Effort and prompt caching.** "Because top-level effort shapes the rendered prompt, changing it between requests doesn't preserve cached prefixes from earlier turns." Hold top-level effort constant within a cached conversation; vary it *across* workloads. **Exception (beta): per-message effort** on Fable 5.1, Mythos 5.1 and Opus 5 — add a `role: "system"` message with empty `content` and `output_config.effort` set (header `mid-conversation-output-config-2026-07-01`); the new level takes effect from the next `user` turn and the cached prefix still matches. Models without it (incl. Fable 5) return 400. On Fable 5.1 prefer this form: a top-level change "also steers the model less reliably: its earlier replies were written at the previous level, and it tends to stay consistent with them."

**Claude Code `ultracode` is not an API effort level** — it pairs `xhigh` with standing permission to launch multi-agent workflows.

### Tips for thinking (S1)

- **Prefer general instructions over prescriptive steps.** "A prompt like 'think thoroughly' often produces better reasoning than a hand-written step-by-step plan."
- **Multishot examples work with thinking.** Use `<thinking>` tags inside few-shot examples to show the reasoning pattern.
- **Manual CoT as a fallback.** When thinking is off, use structured `<thinking>` / `<answer>` tags to separate reasoning from output. **Not on Opus 5** — with thinking disabled it can leak internal XML tags into visible output; prefer thinking enabled at `low` effort instead.
- **Ask Claude to self-check.** "Before you finish, verify your answer against [test criteria]." **Opus 5 is the exception** — it self-verifies well without instruction, and carried-over verification instructions cause *over*-verification. Remove them rather than rewriting them.
- **Note:** when extended thinking is disabled, Opus 4.5 is sensitive to the word "think" — prefer "consider," "evaluate," or "reason through."

### Overthinking / commit to an approach (S1, S6)

Higher `effort` makes Opus 4.6 do more upfront exploration, which can inflate thinking tokens. Tune thoroughness prompts (replace "Default to using [tool]" with "Use [tool] when it would enhance your understanding") or lower `effort`. To stop it revisiting decisions:
```text
When you're deciding how to approach a problem, choose an approach and commit to it.
Avoid revisiting decisions unless you encounter new information that directly contradicts
your reasoning. If you're weighing two approaches, pick one and see it through. You can
always course-correct later if the chosen approach fails.
```
Fable 5's variant, to stop over-planning on ambiguous tasks: "When you have enough information to act, act. Do not re-derive facts already established in the conversation, re-litigate a decision the user has already made, or narrate options you will not pursue in user-facing messages. If you are weighing a choice, give a recommendation, not an exhaustive survey. This does not apply to thinking blocks."

---

## Claude Fable 5.1 / Mythos 5.1 — what differs from Fable 5 (S8)

Existing Fable 5 prompts perform well unchanged. Start with the section matching what you observe.

**Progress updates: fewer by default.** The opposite of Opus 5. Users may see the agent go quiet for minutes, or a final message covering only the last step. Three steps, in order: (1) check the client receives them at all — the between-call notes come back as **progress-update `thinking` blocks**, empty under the default `thinking.display: "omitted"`; set `display: "updates"` (beta `thinking-display-updates-2026-08-18`) or `"summarized"`; (2) remove prompt lines that suppress narration ("hold all findings for the final response"); (3) only then add:
```text
Before you start, say in a line what you're about to do; brief updates while you work help the user follow along. Close with a short recap that stands on its own — what you found, what you did, and what's next — so a reader who only sees the last message has the full picture.
```
If your UI collapses tool output, say so in a turn-scoped system message: "Only you see that command's output — the user's terminal shows at most a few lines of it. If the user needs to read any of it, put it in your reply."

**Append-only history — enforced.** Append each assistant turn exactly as returned, thinking blocks included. For accounts created on or after 2026-08-31, replaying a thinking block after its prefix changed (system prompt, tool list, any earlier message) returns **400** — or drops the block under `thinking.block_binding.prefix_mismatch_behavior: "drop_block"` (beta `thinking-binding-controls-2026-08-01`). Future models are expected to enforce it for all accounts. The edits that trip it are the same ones that restart the prompt cache: injected/removed per-turn reminders, in-place summarization, mid-session system changes. Use turn-scoped system messages for reminders, mid-conversation system messages for instruction/tool changes, server-side compaction or context editing for trimming; client-side compaction should replace the whole history with one summary message plus the new user turn and replay nothing else. Cache reads are cheaper now, so compacting early may no longer be the right trade — experiment with later compaction points.

**Tool-call batching in agent loops** — the turn-scoped nudge in *Optimize parallel tool calling* above.

**Writing density.** Prose can run denser than Fable 5's (longer sentences, fewer breaks). Define the anti-pattern, in a user message preferably:
```text
Mannered prose substitutes metaphor and flourish for direct statement. Instead of "a parameter worth varying," the mannered writer produces "a dial worth turning." Instead of "this point still matters," they write "this point earns its keep." The phrases exist to display the writer, not to convey the idea, and readers can tell. That is why mannered prose irritates: it makes the reader work harder so the writer can perform. It is also imprecise. Metaphors drag in connotations the writer did not choose and cannot control. The fix is to say what you mean. When a literal phrase is available, use it.
```
The short form "Please remove all mannered prose." also tends to work.

**Formatting in chat: less by default** — see *Control format of responses* above; remove anti-formatting rules.

**Quoting retrieved sources.** More likely than Fable 5 to reproduce source passages unmarked when summarizing. Add one complete `<example>` to the system prompt (request → response → `<rationale>` explaining why it is correct: organized around agreement/difference, each source in one or two sentences of indirect speech, one short marked phrase, everything else reworded), with your tool's name in place of `[web_search: …]`.

**Finish the whole task.** On complex async work it may describe the next step instead of doing it ("Next, I'll …") or ask permission for a step already requested. Two system-prompt blocks together; the first alone keeps most of the effect, and its opening sentence carries much of it — keep it as written:
```text
You are operating autonomously. The user is not watching in real time and cannot answer questions mid-task, so asking 'Want me to…?' or 'Shall I…?' will block the work. For reversible actions that follow from the original request, proceed without asking. Stop only for destructive actions or genuine scope changes the user must decide. Offering follow-ups after the task is done is fine; asking permission before doing the work is not.

Exception: when the user is describing a problem, asking a question, or thinking out loud rather than requesting a change, the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one.

Before ending your turn, check your last paragraph. If it is a plan, an analysis, a question, a list of next steps, or a promise about work you have not done ('I'll…', 'let me know when…'), do that work now with tool calls. That includes retrying after errors and gathering missing information yourself. Do not stop because the context or session is long. End your turn only when the task is complete or you are blocked on input only the user can provide.

Before running a command that changes system state (such as restarts, deletes, or config edits), check that the evidence actually supports that specific action. A signal that pattern-matches to a known failure may have a different cause.
```
The second defines the request as the scope of the deliverable:
```text
# Delivering work
The user's request — or the plan they approved — sets the scope, and the scope is the deliverable: don't quietly narrow, widen, or swap it. Read ambiguity the way a careful colleague would: make routine judgment calls yourself, and check in only when different readings would lead to materially different work. If you see a real problem with the task as specified, say so in a sentence or two and keep building under stated assumptions; if the user hears the concern and reaffirms, that is their decision, so deliver the full request.

If a question comes up partway, first do everything that doesn't depend on the answer; then state the assumption you made, or — when going ahead on a wrong guess would be unsafe or would make the work useless — put the question at the end of a turn that also delivers that progress. If one part turns out to be blocked, complete every other part in full and say exactly what you left out and why — the whole task is the deliverable, and scaling it down is the user's call, not yours. A step you have decided on is something to run, not to announce: describing the next step and ending the turn leaves it undone until the user replies.

Keep changes to what the request needs. Something else you notice worth doing — cleanup or documentation the task didn't call for, a change to a file the task didn't require — is a suggestion to make at the end, not a change to make; actions clearly beyond what the ask implies, and risky or destructive ones, still need the user's go-ahead.
```
This block can make the model less likely to ask about ambiguous requests — check that trade-off.

**Compaction summaries (client-side).** Tell the model what to preserve: difficulties and how resolved; options raised/tried/set aside and why; anything asked, decided, agreed, ruled out, or established as a constraint — stated exactly; exactly where things stand; anything open or expected next; hard-to-reconstruct details (names, numbers, dates, exact wording, links) kept exactly. Keep the user's words close to verbatim; condense the model's own reasoning. (Full instruction on S8.)

**Keep changes and tests to what the task asks for.** It may fix nearby code, extend unmentioned behaviour, or commit more test files than warranted; with this instruction those drop substantially with no change in task success:
```text
If, while working or testing, you find a pre-existing bug, a performance concern, or behavior the task doesn't mention, don't fix, optimize or extend it in this change unless the requested behavior cannot work without it; report it as a follow-up in your summary. Where the task is ambiguous, implement the reading its wording and the surrounding code most directly support, state that assumption in your summary, and don't build for the other readings as well. Verify your work however you like; scratch scripts and quick checks need not be kept. Commit tests only where the task asks for them or this repository already keeps tests for this kind of change, sized like the neighboring test files — roughly one focused test per stated behavior — and don't turn scratch checks into additional permanent test files. This is about extras only: implement every behavior the task asks for, completely.
```

**Search triggering at low effort.** At `low` it answers from memory more often. Raise effort for the affected turns (per-message effort), or nudge: when a query centres on a name it doesn't confidently recognize — or one from a fast-moving area — search before answering, including the name as the user wrote it; "familiarity is not a reason to skip the search."

**Safeguard false positives** (`stop_reason: "refusal"`; fewer than Fable 5 at launch; finding vulnerabilities in source code is permitted). Ask "Are there any bugs in this program?" rather than "Does this compile without errors?"; give context/docs for lesser-known languages; remove tools that return base64 into context.

**Prefer targeted edits.** More likely than Fable 5 to rewrite whole files for small changes: "The number of tokens used to edit files is best minimized, all else being equal. Therefore, when it will not affect the end result, try to surgically edit a file rather than rewrite the entire thing."

**Let the lead agent keep working while subagents run.** Have the spawn tool return immediately, deliver results in a later `user` message, and give the lead a separate wait tool — lower time-to-completion at similar quality and cost.

**Vision.** Best on dense charts when it can iteratively analyze, crop and verify; a crop tool alone delivers most of the uplift.

---

## Claude Fable 5 / Mythos 5 — the long-run profile (S6)

Relevant even when not the active model, because several patterns generalize to long autonomous runs:

- **Longer turns by default.** Individual requests on hard tasks can run many minutes; autonomous runs extend for hours. Adjust client timeouts, streaming, and progress indicators; consider checking on runs asynchronously rather than blocking.
- **Ground progress claims.** "Before reporting progress, audit each claim against a tool result from this session. Only report work you can point to evidence for; if something is not yet verified, say so explicitly. Report outcomes faithfully: if tests fail, say so with the output; if a step was skipped, say that; when something is done and verified, state it plainly without hedging." In Anthropic's testing this "nearly eliminated fabricated status reports."
- **Strong instruction following** — steer with a brief instruction rather than enumerating behaviors. Skills written for prior models "are often too prescriptive for Claude Fable 5 and can degrade output quality." A brevity instruction ("Lead with the outcome… Being readable and being concise are different things, and readability matters more") replaces a list of verbosity patterns; a checkpoint instruction ("Pause for the user only when the work genuinely requires them: a destructive or irreversible action, a real scope change, or input that only they can provide") replaces an enumerated case list.
- **State the boundaries** — it can take unrequested actions (drafting an email, defensive git-branch backups): "When the user is describing a problem… the deliverable is your assessment. Report your findings and stop. Don't apply a fix until they ask for one."
- **Parallel subagents** — dispatches them more readily; prefer asynchronous orchestrator/subagent communication and long-lived subagents: "Delegate independent subtasks to subagents and keep working while they run. Intervene if a subagent goes off track or is missing relevant context."
- **Memory systems** pay off: "Store one lesson per file with a one-line summary at the top. Record corrections and confirmed approaches alike, including why they mattered. Don't save what the repo or chat history already records; update an existing note rather than creating a duplicate; delete notes that turn out to be wrong."
- **Rare early stopping.** Deep into a long session it can end a turn with a statement of intent without the tool call, or ask permission it doesn't need. A "continue" suffices; for autonomous pipelines use the autonomy block (the Fable 5.1 "Finish the whole task" block above is its successor).
- **Rare context-budget concern.** Most often triggered when the harness shows a remaining-token countdown. Avoid surfacing counts; if the harness must, add "You have ample context remaining. Do not stop, summarize, or suggest a new session on account of context limits. Continue the work."
- **Readability when communicating with the user.** After long agentic work, the final summary is a re-grounding for a reader who saw none of it: outcome first, complete sentences, no arrow chains or invented labels, each identifier in its own plain-language clause.
- **Don't instruct it to reproduce its reasoning.** Prompts that tell the model to echo, transcribe, or explain internal reasoning as response text can trigger the `reasoning_extraction` refusal category and cause elevated fallbacks to Opus 4.8. Audit skills and system prompts for show-your-thinking instructions; read structured `thinking` blocks instead.
- **send-to-user tool** for long async agents — surfaces verbatim content mid-turn without ending it. Requires a system-prompt instruction or the model rarely calls it; never route narration through it.
- **Scaffolding:** start at the top of your difficulty range; make self-verification explicit in long-run prompts with fresh-context verifier subagents at an interval; refactor over-prescriptive skills.

---

## Claude Opus 5 — behavioral profile (S4)

Opus 5 "performs well out of the box on existing Claude Opus 4.8 prompts," but six behaviors most often need tuning. Its context window is **1M tokens as both the default and the maximum**, with instruction following, tool calling, and reasoning consistent throughout.

**Response length and verbosity.** Default user-facing responses run longer than prior Opus models'. Effort controls *thinking*, not *saying* — prompt explicitly:
```text
Keep responses focused, brief, and concise. Keep disclaimers and caveats short, and spend
most of the response on the main answer. When asked to explain something, give a high-level
summary unless an in-depth explanation is specifically requested.
```
In a long system prompt, pair it with a short reminder near the end (`<tone_preference>Keep outputs reasonably concise.</tone_preference>`).

**User-facing progress updates.** Opus 5 narrates readily during agentic work. To tune narration down, describe the cadence you want — "Before your first tool call, say in one sentence what you're about to do. While working, give a brief update only when you find something important or change direction. When you finish, lead with the outcome."

**Written deliverable length.** Files written to disk (reports, Markdown docs) are often longer than on prior models: "Match the length of written documents to what the task needs: cover the substance, but do not pad with filler sections, redundant summaries, or boilerplate."

**Task scope and over-verification.** Opus 5 verifies its own work unprompted. "If your prompt contains explicit verification instructions … remove them: instructions like these cause over-verification on Claude Opus 5, and removing them reduces wasted tokens with no loss in quality. The same applies to legacy harness scaffolding that adds separate verification steps." It can also expand scope; for narrow tasks, constrain explicitly ("Deliver what was asked, at the scope intended… Finish the whole task, and stop short of actions that are clearly beyond what was asked").

**Subagent spawning.** Opus 5 delegates more readily than prior models. "Delegate to a subagent only for large tasks that are genuinely independent and parallelizable… Do not delegate work you can finish yourself in a handful of tool calls, and do not use subagents to verify or double-check your own work." Deterministic caps in Claude Code / the Agent SDK: `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, SDK `max_budget_usd` (Claude Code ≥ 2.1.217). Claude Code adds its own delegation instruction only under the `claude_code` system-prompt preset; with a custom/omitted system prompt, add one yourself.

**Self-correction.** It catches its own mistakes well — avoid "double-check your answer" instructions. It also narrates corrections more than prior models: "Only correct an earlier statement when the error would change the user's code, conclusions, or decisions."

**Code review.** High precision *and* recall, holding at lower effort. But "if your review prompt says 'only report high-severity issues' or 'be conservative,' the model may follow that instruction literally and report less; ask it to report everything and filter in a separate pass instead."

**Thinking disabled → two artifacts.** With thinking off, Opus 5 occasionally (a) writes a tool call as visible text instead of a `tool_use` block — the call never runs and the leaked text pollutes later turns, and (b) emits `<thinking>` or other internal XML tags into visible output (a rule "not to think" increases leakage — remove it). Primary mitigation is to keep thinking **on** at low effort. If it must stay off, one combined instruction mitigates both, and the *general* form works better than naming thinking tags:
```text
When you use a tool, you may say a brief sentence first. If no tool can express what the
user asked for, say so instead of guessing. Do not include internal or system XML tags in
your response.
```

---

## Agentic Systems

### Long-horizon reasoning + context awareness (S1)

Current models track state well across extended sessions, "making steady advances on a few things at a time rather than attempting everything at once," especially across multiple context windows.

Sonnet 5 / 4.6 / 4.5 and Haiku 4.5 have **context awareness** — they track their remaining token budget. In a harness that compacts or saves state (like Claude Code), tell Claude so it doesn't wrap up prematurely:
```text
Your context window will be automatically compacted as it approaches its limit, allowing
you to continue working indefinitely from where you left off. Therefore, do not stop tasks
early due to token budget concerns. As you approach your limit, save your progress and
state to memory before the context window refreshes. Never artificially stop any task early
regardless of the context remaining.
```
The memory tool pairs well with context awareness. (Fable counterpart: avoid surfacing explicit remaining-token counts at all — see S6.)

### Multi-context-window workflows (S1)

- Use a **different prompt for the first context window** (set up framework: write tests, setup scripts), then iterate on a todo-list in later windows.
- Have the model **write tests in a structured format** (`tests.json`) and treat them as inviolable ("It is unacceptable to remove or edit tests").
- Set up **quality-of-life tools** (`init.sh` to start servers, run tests/linters).
- **Starting fresh vs compacting:** current models discover state from the filesystem effectively — sometimes a brand-new context window beats compaction. Be prescriptive: "Call pwd… Review progress.txt, tests.json, and the git logs… run a fundamental integration test before new features."
- **Provide verification tools** (computer use, browser use, a browser-automation MCP server) as autonomous tasks lengthen.
- **Encourage complete usage of context:** "It's encouraged to spend your entire output context working on the task - just make sure you don't run out of context with significant uncommitted work."

### State management (S1)

- **Structured formats (JSON)** for structured state (test results, task status).
- **Freeform text** for progress notes.
- **Git** for state tracking across sessions (log + restorable checkpoints).
- **Emphasize incremental progress** explicitly.

### Balancing autonomy and safety (S1)

"Without guidance, Claude Opus 4.6 may take actions that are difficult to reverse or affect shared systems."
```text
Consider the reversibility and potential impact of your actions. You are encouraged to take
local, reversible actions like editing files or running tests, but for actions that are hard
to reverse, affect shared systems, or could be destructive, ask the user before proceeding.
```
S1 lists warrant-confirmation examples (deleting files/branches, `rm -rf`, `git push --force`, `git reset --hard`, amending published commits, pushing code, commenting on PRs, messaging, modifying shared infra) and adds: "do not use destructive actions as a shortcut" (no `--no-verify`, don't discard unfamiliar in-progress files).

### Research and information gathering (S1)

Provide clear success criteria, encourage cross-source verification, and for complex tasks:
```text
Search for this information in a structured way. As you gather data, develop several
competing hypotheses. Track your confidence levels in your progress notes to improve
calibration. Regularly self-critique your approach and plan. Update a hypothesis tree or
research notes file to persist information and provide transparency.
```

### Subagent orchestration (S1)

Current models orchestrate subagents natively and delegate proactively. Opus 4.6 "has a strong predilection for subagents"; **Opus 5 also delegates more readily than prior models**; **Fable 5 / 5.1 dispatch parallel subagents more readily still** (and are explicitly recommended to use them frequently, asynchronously, with the lead agent continuing while they run). Opus 4.8, by contrast, spawns *fewer* by default and may need encouragement.

To curb overuse:
```text
Use subagents when tasks can run in parallel, require isolated context, or involve
independent workstreams that don't need to share state. For simple tasks, sequential
operations, single-file edits, or tasks where you need to maintain context across steps,
work directly rather than delegating.
```

### Chain complex prompts (S1)

Adaptive thinking + native orchestration handle most multi-step reasoning internally. Explicit prompt chaining still helps when you need to inspect intermediate outputs or enforce a pipeline. The most common pattern is **self-correction:** draft → review against criteria → refine, each as a separate call so you can log/evaluate/branch.

### Reduce file creation (S1)

Current models may create scratch files as a "temporary scratchpad" (often python scripts) — usually helpful for agentic coding. To minimize net-new files: "If you create any temporary new files, scripts, or helper files for iteration, clean up these files by removing them at the end of the task."

### Overeagerness / over-engineering (S1, S6)

"Claude Opus 4.5 and Claude Opus 4.6 have a tendency to overengineer by creating extra files, adding unnecessary abstractions, or building in flexibility that wasn't requested."
```text
Avoid over-engineering. Only make changes that are directly requested or clearly necessary.
Keep solutions simple and focused. Don't add features, refactor, or make "improvements"
beyond what was asked. Don't add docstrings/comments/types to code you didn't change. Don't
add error handling for scenarios that can't happen — only validate at system boundaries.
Don't create helpers or abstractions for one-time operations.
```
Fable 5 at higher effort can tidy or refactor unasked; S6 adds "Don't use feature flags or backwards-compatibility shims when you can just change the code." Fable 5.1's scoped-changes block (above) is the tested successor.

### Avoid passing-tests-at-all-costs / hard-coding (S1)

Claude can over-focus on making tests pass or use helper-script workarounds. To get general solutions:
```text
Please write a high-quality, general-purpose solution using the standard tools available.
Do not create helper scripts or workarounds. Implement a solution that works correctly for
all valid inputs, not just the test cases. Do not hard-code values. Tests verify
correctness, they do not define the solution. If the task is infeasible or a test is
incorrect, inform me rather than working around it.
```

### Minimizing hallucinations (S1)

```text
<investigate_before_answering>
Never speculate about code you have not opened. If the user references a specific file, you
MUST read the file before answering. Investigate and read relevant files BEFORE answering
questions about the codebase. Never make claims about code before investigating unless you
are certain - give grounded and hallucination-free answers.
</investigate_before_answering>
```

---

## Capability-Specific Tips

### Vision (S1, S4, S8)

Opus 4.5/4.6 have improved vision (image processing, data extraction, multi-image context, computer-use screenshots; analyze video by frames). Giving Claude a **crop tool / skill** to "zoom" into relevant regions yields consistent uplift on image evals.

Opus 5 is strong on chart/document/diagram understanding and UI replication — **re-validate any prompt-side vision workarounds tuned for prior models**; tool use ("iteratively analyze, crop, and visually verify") is a more cost-effective lever than thinking alone. Fable 5.1: run it as an agent with a container holding the raw images and PIL/OpenCV; if that is too much, a crop tool alone delivers most of the uplift.

### Code review harnesses (S4, S5, S7)

A harness tuned for an earlier model may show **lower recall** on Sonnet 5 / Opus 4.8 / Opus 5 — a harness effect, not a capability regression. Instructions like "only report high-severity issues" or "don't nitpick" are now followed *faithfully*: the model investigates just as thoroughly but converts fewer investigations into reported findings. Precision rises, measured recall falls.
```text
Report every issue you find, including ones you are uncertain about or consider
low-severity. Do not filter for importance or confidence at this stage - a separate
verification step will do that. Your goal here is coverage: it is better to surface a
finding that later gets filtered out than to silently drop a real bug. For each finding,
include your confidence level and an estimated severity so a downstream filter can rank them.
```
If you want single-pass self-filtering, be concrete about the bar rather than saying "important" — e.g. "report any bugs that could cause incorrect behavior, a test failure, or a misleading result; only omit nits like pure style or naming preferences."

### Frontend design (S1, S5, S7)

Models build strong frontends but default to generic "AI slop" without guidance. S1 offers a full `<frontend_aesthetics>` block: distinctive typography (avoid Inter/Roboto/Arial), a cohesive color/theme via CSS variables, motion for high-impact moments, atmospheric backgrounds — and explicitly avoid overused fonts, purple-gradient-on-white clichés, and predictable layouts. Opus 4.8 and Sonnet 5 need less: a two-sentence `<frontend_aesthetics>` ("NEVER use generic AI-generated aesthetics like overused font families… Use unique fonts, cohesive colors and themes, and animations for effects and micro-interactions.") works alongside the variety approaches below.

**Opus 4.8 has a documented default house style** — warm cream/off-white backgrounds (~`#F4F1EA`), serif display type (Georgia, Fraunces, Playfair), italic word-accents, terracotta/amber accent. Sonnet 5 similarly settles into a consistent default. Both read well for editorial/hospitality/portfolio briefs and feel off for dashboards, dev tools, fintech, healthcare, enterprise.

The default is persistent, and **generic instructions ("don't use cream," "make it clean") shift it to a *different fixed* palette rather than producing variety.** Two approaches work: (1) specify a concrete alternative with explicit hexes/typography/spacing, or (2) have the model propose 4 distinct visual directions before building and let the user pick. Since `temperature` is not accepted on Sonnet 5, (2) is the recommended way to get variety across runs.

### Computer use (S5, S7)

Sonnet 5 and Opus 4.8 support the `computer_toolset_20260801` toolset (Claude API and Google Cloud) and the earlier `computer_20251124` tool, plus the browser use tool (`browser_toolset_20260801`) for tasks inside webpages. Works across resolutions up to **2576px / 3.75MP**; **1080p** balances performance and cost; 720p or 1366×768 are lower-cost options for cost-sensitive workloads.

---

## Sonnet 5 — API constraints worth knowing (S5)

Two changes bite silently when migrating from Sonnet 4.6:

- **Sampling parameters rejected.** Setting `temperature`, `top_p`, or `top_k` to a non-default value returns a **400 error** — new for Sonnet-class models. Remove them; steer tone and variety with system-prompt instructions instead.
- **New tokenizer, ~30% more tokens.** It "produces approximately 30% more tokens for the same text," so `max_tokens` limits tuned for Sonnet 4.6 may truncate equivalent output. The exact increase depends on content and workload shape.

Also: adaptive thinking is **on** by default (a change from 4.6, where the same request ran without thinking), manual `budget_tokens` returns 400, and the model is more agentic — more tool reaching and self-verification loops (with thinking disabled it reaches for tools less; add an explicit nudge). It follows instructions **literally**: "If you need Claude to apply an instruction broadly, state the scope explicitly." It provides regular, higher-quality progress updates — remove scaffolding that forces interim status messages. For interactive coding products, use `xhigh`/`high`, add an auto mode, and specify task/intent/constraints up front to reduce user turns.

---

## Migration considerations (S1)

1. **Be specific** about desired behavior and output.
2. **Frame instructions with modifiers** ("Go beyond the basics to create a fully-featured implementation").
3. **Request features explicitly** (animations, interactivity).
4. **Update thinking config:** adaptive thinking (`{type: "adaptive"}`) + `effort` instead of `budget_tokens`.
5. **Migrate away from prefilled responses** (400 on 4.6+).
6. **Tune anti-laziness prompting down** — current models are proactive and may over-trigger on aggressiveness older models needed.
7. **Pass thinking blocks back unchanged and keep history append-only** — on Fable 5.1, editing earlier messages, rebuilding `system`/`tools`, or summarizing older turns in place invalidates every later thinking block (error, or drop under the beta flag); move those changes to mid-conversation system messages and server-side context management.
8. **Re-run an effort sweep** rather than carrying effort defaults across a model change (explicit for Opus 5 and Fable 5.1).
9. **Strip carried-over verification scaffolding** when moving to Opus 5 — it compounds with native self-verification.
10. **Strip carried-over anti-formatting and "hold findings" rules** when moving to Fable 5.1 — it already formats less and narrates less.

---

## Handoff Prompts — PE Guidance (for /clarification Step 10)

A well-crafted handoff prompt for a new session should:

1. **Be specific and direct** (S1: "Be clear and direct") — state exactly what to read first, what to do, and what constraints apply.
2. **Include context** (S1: "Add context to improve performance") — topic title + one-sentence diagnosis for orientation.
3. **Provide sequential steps** — numbered actions in order.
4. **Use XML tags for structure** — separate instructions from context.
5. **Avoid over-specifying mechanisms** — state outcomes, not implementation paths; the next session reads the plan and decides how.

Example structure:
```xml
<topic>
  <title>[Topic title]</title>
  <diagnosis>[One sentence: what's the critical challenge]</diagnosis>
</topic>

<instructions>
  1. Read [specific file] fully before starting.
  2. [Next action — what to do, not how]
  3. Constraints: [specific constraints]
</instructions>
```

Advisory (operator chat only — NOT part of the artifact): After this session — run /close, then /clear, then start a fresh session and paste this prompt.

The Advisory is operator chat guidance for the *current* session — it is not part of the artifact and must never be persisted into `## Next Session Prompt`. The composed/pasted prompt is everything inside the `<topic>` + `<instructions>` fence and nothing else.

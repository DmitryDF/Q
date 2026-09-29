---
name: research-subagent
description: Subagent orchestration reference for research skill — model strategy, output tracking, report format, constraints.
parent: SKILL.md
---

# Research Subagent Mode

This skill can be executed autonomously by a Task subagent. Loaded on demand by `SKILL.md` (the harness `/research` skill); it describes the `research` adapter at `~/.claude/agents/research.md`.

---

## Model Strategy

| Research Phase | Recommended Execution | Why |
|---------------|----------------------|-----|
| WebSearch + URL collection | Haiku subagent | Mechanical — just run queries and collect URLs |
| WebFetch + content extraction | Sonnet subagent | Needs some comprehension to extract relevant passages |
| Source quote extraction | Sonnet subagent | Comparison task — find passage supporting claim |
| Source evaluation + tier assignment | Opus (main) | Judgment call requiring understanding of source quality |
| Synthesis + competing hypotheses | Opus (main) | Highest intelligence for cross-source reasoning |
| Fact-check pass | Sonnet subagent | Structured comparison — claim vs source text |
| Convergence fact-check | Sonnet subagent | Independent verification — 3 checkers per round |
| Medical/legal/high-stakes | Opus (main) only | Risk — wrong answers have real consequences |
| Recommendation | Opus (main) | Weighing all evidence, nuanced judgment |

For "deep research": Use Opus for everything — reliability over speed.
For "regular research": Delegate search/fetch to Sonnet/Haiku subagents, synthesize in Opus.

---

## Layered Output Tracking

When running as subagent, track progress through these layers:

| Layer | What | File Section | Status Marker |
|-------|------|--------------|---------------|
| **Topics** | Questions extracted from input | ## [Date] — Topics | `[x] Topics extracted` |
| **Findings** | Initial answers per topic | ## [Date] — Findings | `[x] Initial findings` |
| **Deep Research** | Gaps filled from concerns loop | ## [Date] — Deep Research | `[x] Deep research` |
| **Synthesis** | Patterns, rules derived | ## [Date] — Synthesis | `[x] Synthesis` |
| **Recommendation** | Final advice with confidence | ## [Date] — Recommendation | `[x] Recommendation` |

---

## Subagent Report Format

*(Corrected 2026-09-30, S6 FIXER review — this section described a stale, whole-run report with Synthesis/Recommendation/Confidence layers, from before the `research` adapter's contract changed at S6. The adapter now covers ONE angle per dispatch and returns findings for the dispatching skill to record; it does not write the research file or the claims register, and its report carries no Synthesis/Recommendation/Confidence layer — those belong to the dispatching skill, which sees every angle. This section now matches `~/.claude/agents/research.md`'s own "What to Return" contract rather than restating a shape the adapter no longer produces.)*

The adapter ends its report with exactly one fenced `json` block:

```json
{
  "angle": "[the angle covered]",
  "findings": [
    {"claim": "[one self-contained sentence]", "source": "[the URL]", "topic": "[the angle, or a sub-topic of it]", "marker": "stated"}
  ],
  "not_found": ["[what was searched for and could not be sourced]"],
  "open_questions": ["[what remains unanswered]"]
}
```

Before the block, a brief prose report:

```
### Angle complete: [angle]

**Key findings:**
- [bullet 1]
- [bullet 2]

**Not found:** [count]
**Open questions:** [count]
**Shell steps skipped (belong to the dispatching session):** [named list]
```

The dispatching skill records each entry of `findings` through the code-layer recorder (`research_pipeline.record_finding`) as it is returned — see `SKILL.md` §4, "Recording each finding as it lands". Anything outside the `json` block is for the operator to read, not for the skill to parse.

---

## Output Logging

When subagent returns its report, the **main conversation** must write the report to the log directory:
- Write to `~/.claude/logs/[hash]/outputs/YYYYMMDD-HHMMSS.md`
- The orchestrator passes the log path as a parameter — subagent does not need to determine the hash
- Include frontmatter: `type: subagent-research`, `topic: [topic]`
- This preserves the research process that would otherwise be lost on compaction

---

## Constraints

When running autonomously (no user interaction):
- **No clarifying questions** — note uncertainty in report instead
- **Attribution required** — every claim needs source URL + supporting quote
- **Track open questions** — for later redo by orchestrator
- **Follow concerns loop** — max 3 rounds, then report with caveats
- **Regional tagging** — if multilingual, tag findings by language/region

---

## Multilingual Execution

When orchestrator specifies multiple languages:
1. Read source lists from each `research-[lang].md`
2. Execute searches in each language
3. Tag findings: `[EN]`, `[DE]`, `[RU]`
4. Include Regional Differences table if findings vary
5. Cross-language deduplication: same finding from EN and DE sources should merge, not double-count

*Moved to the harness 2026-09-22 (research-entry-point-enforcement S2) from `Projects/Skills/research-subagent.md`, which is now a committed pointer stub. Bundled reference of `~/.claude/skills/research/SKILL.md`.*

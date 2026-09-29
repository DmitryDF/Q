---
name: research-initiative
description: Initiative/BMC-specific research extension — assumption validation, contradiction detection, research loop.
parent: SKILL.md
---

# Initiative Research Mode

**Trigger:** Research for `_INITIATIVE.md` file or Business Model Canvas.

Extends `SKILL.md` with BMC-specific methodology.

---

## Validate Core Assumptions First

Before general search, extract core assumptions from each BMC block:

| Block | Typical Assumption | Priority |
|-------|--------------------|----------|
| **Revenue Streams** | Price per X (EUR/unit, EUR/month) | HIGH — validate first |
| **Cost Structure** | FTE capacity, costs | HIGH |
| **Customer Segments** | Who pays, size | MEDIUM |
| **Value Proposition** | Differentiation claim | MEDIUM |
| **Key Resources** | Availability, cost | MEDIUM |

**Rule:** Revenue/Pricing assumptions are tested FIRST — if wrong, entire model collapses.

---

## Contradiction Detection

After each search result, compare against documented assumptions:

**If research contradicts an initiative assumption:**
1. **STOP** — flag immediately
2. Show: "Research contradicts assumption: [X]"
3. Ask: "Continue with other research, or pause to address this?"

Don't wait until end of research to reveal critical contradictions.

---

## Storage Location

**Naming convention:** `[topic]_RESEARCH.md`
- Primary file (main language): `[topic]_RESEARCH.md`
- Translation: `[topic]_RESEARCH_EN.md`

**Before project creation:** Research goes to `Thoughts/[topic]_RESEARCH.md` or `[Work]/Thoughts/[topic]_RESEARCH.md`.

**After project creation:**
- `_RESEARCH.md` is MOVED to project folder
- New research: append to `[Project]/[topic]_RESEARCH.md`
- Format with date headers: `## YYYY-MM-DD — [Topic]`

---

## Research Loop

**Continue researching when:**
- Any BMC block confidence < 7/10
- Concerns list has > 1 item per block
- Core assumptions (pricing, capacity, market) unvalidated

**Stop researching when:**
- All blocks >= 7/10 confidence
- Remaining questions require interviews (can't desk-research)
- Max 3 research loops completed — ask user for direction

**After each loop:**
1. Append findings to Research.md (with date header)
2. Update _INITIATIVE.md conclusions only
3. Re-calculate confidence per block
4. Report: "X blocks improved, Y still need work"
5. Offer: "Re-assess initiative?"

*Moved to the harness 2026-09-22 (research-entry-point-enforcement S2) from `Projects/Skills/research-initiative.md`, which is now a committed pointer stub. Bundled reference of `~/.claude/skills/research/SKILL.md`.*

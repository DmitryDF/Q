# Q — a spec-driven development system that refuses to let work skip the thinking

Q is a **spec-driven development system**. It turns a raw idea into a written spec —
a locked Discovery contract, then a design, then a plan — and then implements that
spec. Vibe coding is where it pays off: the agent still writes the code fast, but it
writes it against something that was thought through, verified, and can be pointed at
later. When the spec needs information nobody in the room has, `/research` goes and
finds it rather than letting the gap be filled with a plausible guess.

Q came out of experimenting with Claude Code and agentic development.

It doesn't claim to be the most modern tool, and it may not use the newest trends or
approaches. What it does is let anyone working with a coding agent do four things:

- **verify what you found**, independently, by checkers that never see your reasoning
- **find the evidence** for an idea, on the open web or in what you have already written down
- **learn what you don't know yet** but need in order to finish something
- **turn almost any idea into a working system**

**Which agents.** Q is built on Claude Code's harness — its skills, its hooks, its
`settings.json`. The skills themselves are written to the open
[Agent Skills](https://agentskills.io) `SKILL.md` standard, so Codex, Antigravity,
Gemini CLI and the other tools that read it can run the *methodology*. What does not
port automatically is the enforcement: the gates are hook registrations, and hook
config is proprietary per vendor. On Claude Code you get the methodology and the
gates. Elsewhere you get the methodology, and the gates need an adapter.

As of today the set ships **13 skills** for getting a bounded next move, stress-testing an idea, independent verification, framing work before you start it, session orientation, turning a solution into an outcome, choosing a metric that holds up, evidence-based research, turning a raw idea into a contract, carrying work across sessions, designing one candidate solution, cutting work into delivery slices, and designing against a contract. More are
coming for vibecoding. Soon.

**One honest caveat.** Some of this may behave differently on your machine than on
mine — a few skills read settings specific to my environment. You can tailor it to
yours (ask your agent to do it), or open an
[issue](https://github.com/DmitryDF/Q/issues) and I'll make it more flexible.

---

## Why "Q"

In the espionage world, **007** is the force in the field; **Q** supports him from
headquarters with the technology. They are two halves of the same coin — action and
preparation.

**Q** refuses to let work skip the thinking. It turns a raw idea into a locked
Discovery contract, researches what the contract says is unknown, designs against it,
plans against the design, and executes the plan across sessions — with independent
verification at every handover and an auditable artifact trail behind all of it.

The loop it runs is **ClaSPEL** — **Cla**rification, **S**olution-design, **P**lan,
**E**xecution, **L**oop. The **L** is the loop closing: what an execution run learns
becomes the framed input to the next topic, so finishing one piece of work is how the
next one gets set up. It is a metaphor for the cycle, not a sixth skill.

Research is not a sixth letter because it is not a sixth phase: `/research` is
dispatched *from inside* clarification, at the step where a question cannot be closed
from what the operator already knows. That binding is code-enforced, not conventional
— `/clarification` declares the research skill it is about to invoke, and registration
hard-fails if it names a different one.

---

## Included Skills

- `/recommend` — one bounded forward move, with trade-offs and stated concerns.
- `/challenge` — stress-tests an artifact against agree-bias.
- `/double-check` — independent verification by isolated read-only checkers.
- `/session-start` — orientation at the top of a session.
- `/outcome-framing` — turns a solution plus a diagnosis into a Cagan-framed outcome.
- `/lean-analytics-metrics` — gates a candidate OMTM against the four good-metric properties.
- `/research` — scope framing and approval, evidence-based findings, fact-check, claims register.
- `/clarification` — ten gated steps turning a raw idea into a locked Discovery.
- `/prompt-for-handoff` — a verified session-continuity prompt.
- `/solution-designer` — designs one candidate solution from a locked Discovery.
- `/solution-slicer` — assesses whether a plan needs delivery slices, and proposes them.
- `/solution-design` — N parallel candidates, verified ranking, coverage gate, slicing.

**Where the four ClaSPEL phases stand in this release:**

- **Clarification** (`/clarification`) — shipped
- **Solution-design** (`/solution-design`) — shipped
- **Plan** (`/plan`) — not yet
- **Execution** (`/execute-plan`) — not yet

13 of 21 skills are here so far. The phases marked *not yet* land in later pushes — this README is generated from what is actually in the tree, so it will not describe a skill before it exists.

## Layout

- Repo scaffold, licence, install instructions and CI.
- Bash sanitisation and config-path guards.
- The rules the skills ground in, and the bookkeeping invariant that enforces them.
- The shell-free `readonly-checker` every verifier runs as.
- The fact-check engine, the claim seam, and the rigor dial the skills resolve their checker allocation from.
- The TODO surface and the bookkeeping rules it obeys — the two-surface model, the slug grammar, and the git working model.
- `/work-frame-and-create-todo` — the framing gate on that surface: Problem, Context, Guiding policy, Master plan, or no TODO gets created.
- Session logging.
- Topic state, the Discovery lock, bookkeeping and worktree placement.
- The research pipeline manifest, scope gate and source adapters.
- Handoff composition, land-readiness and cross-session resume.

---

## Installing

```bash
git clone https://github.com/DmitryDF/Q.git && cd Q && ./setup.sh --global
```

`./setup.sh` is the installer; `./setup.sh --settings` re-opens the one question it
asks (how thorough verification should be) on an install that is already running.
[INSTALL.md](INSTALL.md) is the long form, written for your agent to follow rather
than for you to execute by hand.

Q installs into `~/.claude/` and asks before it touches
anything you already own — your `CLAUDE.md` and your `settings.json` are yours; the
installer shows you what to add rather than overwriting them.

## Honest limits

- **This is one operator's working system, genericized — not a product.** Personal
  paths, project names and a private knowledge library have been removed, but the
  opinions have not. Some hooks encode preferences you may not share.
- **Knowledge-library citations point at books you must supply.** Several skills
  ground their judgment in specific book extractions. Those extractions are not
  distributed — see [NOTICES.md](NOTICES.md). Where the source is unreachable the
  skills fall back and say so.
- **Gate enforcement is client-side.** Hooks are the practical fast feedback, not the
  authority — real enforcement is server-side/CI.

## License

[MIT](LICENSE) — use it, change it, ship it, sell it. Keep the copyright notice.

One carve-out, because MIT can only grant what the licensor owns: this repo contains
short attributed quotations from published books, and verbatim prompt templates from
Anthropic's public documentation. Those passages belong to their authors and are not
covered by the MIT grant — they are quoted under the right of quotation, for
commentary. Every one is inventoried in [NOTICES.md](NOTICES.md).

Everything else — the skills, the rules prose, the hooks, the engines — is original
work under MIT.

---

Built by **Dmitrii Frikh-Khar** ([@DmitryDF](https://github.com/DmitryDF)).

Questions, bug reports, or "how would I adapt this to my setup?" — open an
[issue](https://github.com/DmitryDF/Q/issues). Contributions are welcome under the
same MIT terms the project ships under.

<!-- Generated by q-release. Do not edit this file directly — edit README.tmpl.md
     and the register in pushes.yaml, then run `q-release readme`. -->

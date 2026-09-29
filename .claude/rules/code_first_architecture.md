# Code-First Enforcement Architecture

Core principle: **Code does, AI thinks, code checks.**

Design pattern: **Hexagonal (Ports & Adapters)**
Design vocabulary: **Cockburn — "Not my job" / "No need to know"**

---

## Trust Hierarchy

Three enforcement layers, most reliable to least:

1. **Code** — DB constraints, validation gates, orchestration. Cannot be bypassed.
2. **Rules files** — Auto-loaded, shared across sessions. AI reads on startup.
3. **Skill text** — Guides AI judgment. No enforcement power.

If a rule can be enforced by code, it must be.

---

## Architecture

```
┌─────────────────────────────────────────┐
│              APPLICATION                │
│    Orchestration, validation, gates     │
│                                         │
│    ┌────────────┐   ┌───────────────┐   │
│    │   Domain   │   │   Use Cases   │   │
│    │ Business   │   │  Code flow,   │   │
│    │ rules only │   │  schema check │   │
│    └────────────┘   └───────────────┘   │
│                                         │
├──────────┬───────────────┬──────────────┤
│   Port   │     Port      │    Port      │
├──────────┼───────────────┼──────────────┤
│    DB    │  AI (Claude)  │   External   │
│  Adapter │   Adapter     │     API      │
└──────────┴───────────────┴──────────────┘
```

---

## Domain Layer — "Not my job" to involve AI

Deterministic logic only. No AI, no external calls, no I/O.

If the answer is deterministic, it belongs here. Business rules, validation, state machines, calculations. Code enforces these — AI has no role.

Before assigning work, ask: is the answer deterministic?
- Yes → domain. No AI.
- No → AI adapter, with code-enforced input and output.

> [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) — workflows for predictable tasks, agents for judgment
> Cockburn, Part 1 §1.1 — "Not my job"

---

## Application Layer — Code owns the flow

Use cases orchestrate: fetch from DB → pass to AI → validate return → store result → trigger verification.

- Code fetches data from DB adapter → scopes it for the task
- Code passes scoped data to AI adapter
- AI returns structured output
- Code validates schema (required fields, types, ranges)
- Code stores to DB adapter
- Code triggers verification

AI never queries DB. AI never writes DB. AI never decides what happens next. "No need to know" — AI sees only what code passes in.

State persists as structured data (DB rows, JSON). No unstructured AI output reaches persistent storage.

> [Tool use docs](https://docs.anthropic.com/en/docs/build-with-claude/tool-use) — structured I/O
> [Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents) — "return only high signal information"
> Cockburn, Part 1 §1.1 — "No need to know"

---

## AI Adapter — Judgment only, behind a port

AI is an adapter. It sits behind a port interface defined by code. It receives structured input and returns structured output.

**Strong implementation** (Cockburn, *Slice the Problem* Ch. 9): "The app cannot know anything about the external technology." Applied here: the AI adapter does not leak prompt strings, model IDs, or vendor-specific structures into the application layer. What the application needs from "the AI" must be expressed purely in domain terms (assessments, scores, classifications) — the adapter translates.

**AI's job:**
- Interpretation of unstructured data
- Assessment against criteria
- Natural language evaluation
- Judgment calls with stated confidence

**Not AI's job:**
- Querying data
- Writing state
- Deciding flow
- Verifying its own output

Each AI task runs in its own context:
- Scoped system prompt (only task-relevant instructions)
- Restricted tool access (only what the task needs)
- Only task-relevant data passed in

No shared state between AI calls. No shared context between assessment and verification.

> [Custom subagents](https://code.claude.com/docs/en/sub-agents) — independent context, scoped tools
> [Writing effective tools](https://www.anthropic.com/engineering/writing-tools-for-agents) — selective tool exposure
> Cockburn, Part 2 §2.3 — "The application refuses to talk to anyone except in its preferred internal language"

---

## Verification — Code orchestrates, separate instances check

Producer never verifies its own output. "Not my job."

Code spawns 3 separate AI instances as verification adapters. Each receives: AI output + original sources. Each returns: PASS or DISCREPANCY with citation.

**Gate logic (code-enforced):**
- 0 discrepancies → PASS
- ≥1 discrepancy → FAIL, retry from application flow step 2
- No consensus after `max_rounds` rounds (per-kind; see `factcheck-convergence.md` §4) → escalate to human

**Grading order (strict):**
1. Code-based — schema, format, value ranges (before AI touches it)
2. Model-based — groundedness, coverage, source quality (3 checkers)
3. Human calibration — periodic review to prevent drift

> [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) — voting pattern, parallelization
> [Safe Agents Framework](https://www.anthropic.com/news/our-framework-for-developing-safe-and-trustworthy-agents) — separate instance screening
> [Demystifying evals](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) — grader hierarchy, groundedness checks

---

## Verification Isolation

Producer-never-verifies (above) stops a producer from grading its own output. It does not stop a *verifier* from corrupting the live system it runs inside. Confirmed trigger (2026-06-09): a verification (V3) smoke test ran a user-facing installer (`setup.sh`, which rewrites `${KIT_HOOKS_DIR}` paths) from inside the active session; `/tmp` paths then appeared in the live `~/.claude/{settings.json, hooks/*}` (exact bridge unconfirmed), and when the temp dir was cleaned up every hook dangled and the session was blocked.

**Primary rule:** A verification step must not run a user-facing installer, or create/mutate a live-config namespace (any `.claude/`), inside the producer's process tree.

**(b) Pre/post live-namespace snapshot — load-bearing guard (always-on rule; enforced in code):** Before any V-step that can write to disk, snapshot the live config namespace (sha of `~/.claude/{settings.json, hooks/*}` + `git -C ~/.claude status --porcelain -- settings.json hooks`); after the step, hard-fail on any drift. **Scope is `settings.json` + `hooks/*` ONLY — drift in `rules/`, `skills/`, `agents/`, `state/`, or any other path under `~/.claude/` is NOT detected by this guard.** Bridge-agnostic within that scope — it catches a leak no matter *how* it reached `~/.claude/{settings.json, hooks/*}`, which is why it is the real protection while the 2026-06-09 bridge stays unpinned. Enforcement: `${KIT_HOOKS_DIR}/check-verifier-isolation.sh` (registered as a PreToolUse/PostToolUse Bash pair in `settings.json` — `pre` arms a snapshot around installer-/namespace-creating commands, `post` recomputes and exits 2 on drift with remediation text; a stale-armed `pre` re-checks if a prior `post` was skipped; a separate SessionStart hook `cleanup-stale-verifier-isolation.sh` removes `.armed`/`.snap` files older than 6h to bound the blast radius of crashed-arm runs). Tested by `${KIT_HOOKS_DIR}/tests/test_verifier_isolation.sh` against a sentinel mirror via `VERIFIER_ISOLATION_HOME`, never touching live config.

**(a) Out-of-session execution — defense-in-depth (AI-judgment, rules layer):** A V-step that invokes a user-facing installer (`setup.sh`, `install.py`, `bootstrap`) or creates a `.claude/`-style namespace is rendered as a copy-paste block the user (or a fresh shell outside the harness) runs after the producer commits — never inline. This removes the confirmed trigger but is not sufficient alone (an installer that writes to `~/.claude` unconditionally would still leak), which is why (b) is the backstop.

> Extends "Producer never verifies its own output" (above) — the verifier is also an actor that must not reach into shared state.

---

## Design Tests (Cockburn — apply at review)

1. **Abstraction Test** — do component names convey their role?
2. **Responsibility Alignment Test** — do name, responsibility, and interface align for each component?
3. **Evolution Test** — if AI model changes, only the adapter changes. If DB changes, only the adapter changes. If verification approach changes, only the verification orchestrator changes. Trajectory of change stays low.

> Cockburn, Part 0 §0.3 — Six Design Tests (rooted concept: Evolution Test)
> Cockburn, Part 1 §1.3 ("Anthropomorphic design with objects") — introduces the phrase "trajectory of change"

---

## Code-Level Rules (Python)

### Dependency Direction

Inner layers never import from outer layers.

```
domain/          → imports nothing external
application/     → imports from domain/, never from infrastructure/
infrastructure/  → implements ports defined in application/
```

Violation = responsibility leakage. Cockburn: "the responsibility
allocation may have been fine to start with, but there is just
no enforcement of it, so it goes wrong."

Cockburn (*Slice the Problem* Ch. 9) names **leakage protection** as a
first-class benefit of Ports & Adapters: the test wall around the application
detects whenever someone leaks UI or technology details into the business
section, or business logic into the UI or external technology sections.
Import-direction enforcement is how that test wall is built in code.

### Domain Objects ≠ ORM Models

Domain objects are plain Python (dataclasses, Pydantic models).
They never inherit from ORM base classes (SQLAlchemy, Django ORM).
The repository adapter converts between DB representation and
domain objects. The domain speaks its own language.

### Ports = Abstract Interfaces

Defined in `application/` layer. Python: `abc.ABC` or `typing.Protocol`.

```python
class AssessmentPort(ABC):
    @abstractmethod
    def assess(self, input: AssessmentInput) -> AssessmentOutput: ...
```

Input port: what the application offers (use case entry points).
Output port: what the application demands (repository, AI adapter).

### Adapters = Concrete Implementations

Defined in `infrastructure/` layer. Implement output ports.

```python
class ClaudeAssessmentAdapter(AssessmentPort):
    def assess(self, input: AssessmentInput) -> AssessmentOutput:
        # Call Claude API, return structured output
        ...
```

Swappable: production adapter, test double, different AI model —
same port interface.

### Dependency Injection

`bootstrap.py` wires adapters to ports at startup. No framework
needed in Python — manual wiring is sufficient for most projects.

```python
def bootstrap() -> ApplicationService:
    repo = SqlAlchemyRepository(session)
    ai = ClaudeAssessmentAdapter(api_key)
    return ApplicationService(repo=repo, ai=ai)
```

### Test Doubles

In-memory adapters substitute real ones in tests. Tests enforce
that no infrastructure details leak into domain.

```python
class FakeRepository(RepositoryPort):
    def __init__(self):
        self.items = []
    def add(self, item): self.items.append(item)
    def get(self, id): return next(i for i in self.items if i.id == id)
```

### Folder Structure

```
project/
├── domain/          # Business rules, entities, value objects. Zero imports.
├── application/     # Use cases, port interfaces, DTOs. Imports domain/.
├── infrastructure/  # Adapters (DB, AI, external). Implements ports.
├── bootstrap.py     # Dependency injection wiring
└── controllers.py   # Input adapters (REST, CLI)
```

### Enforcement

- Import linting: no `infrastructure/` imports in `domain/`
- Tests using in-memory adapters (test doubles)
- Cockburn: "Those tests can enforce the rule that no UI or
  repository details get into the business logic"

---

## AI as a Runtime Actor — Cockburn's Prescriptions (2026)

Cockburn's *Slice the Problem, Grow the Solution* (v0.9b, "AI Hits" epilogue,
April 2026) prescribes the following for AI-in-the-loop work. These extend
the rules above with Cockburn-native language:

- **Treat agents as hostile or aberrant partners.** Do not assume they share
  your intent. Defend boundaries with code, not goodwill.
- **Separate agents for separate roles.** One agent writes the spec, a
  different one writes the tests, a different one writes the code, a different
  one audits check-in history. No single agent owns more than one role.
  (Reinforces "Producer never verifies its own output" — Verification section.)
- **Hexagonal Architecture for boundary protection.** The Ports & Adapters
  pattern is named explicitly as the structural defense against agents
  reaching past their assigned role.
- **Automated regression suites defend those boundaries.** Boundary checks
  cannot live in human review; they must be executable on every commit.
- **Nano-increments control cognitive load.** Agents are fast and produce
  large diffs; small steps keep the human (and the next agent) able to verify
  what changed.

### Growth sequence inside the port (Cockburn, *Slice the Problem* Ch. 9)

Once the Hexagonal frame is in place, grow each capability through four
nano-increments:

1. **test-to-test** — port's test double talks to adapter's test double
2. **real-to-test** — production port talks to adapter's test double
3. **test-to-real** — port's test double talks to production adapter
4. **real-to-real** — production port talks to production adapter

This is the construction order for a new AI adapter: do not skip to step 4.

### Use case as the boundary of one AI task (Cockburn, *Unifying User Stories* Ch. 3, Concept 15)

> "Use cases fit with the Ports & Adapters pattern. The use case specifies the
> primary actors as those that drive the system and the supporting actors as
> those that the system drives. It happens that those exactly match the
> 'driving' and 'driven' actors in the Ports & Adapters pattern."

Applied here: one AI task = one use case at a defined altitude. The primary
actor (driver) is on the input side of the port; supporting actors (driven)
are on the output side. This is the Cockburn-native vocabulary for scoping
what a single AI invocation is responsible for.

### Slicing — pointer to sibling rules

For *how big* an AI task or slice should be, Cockburn provides slicing
vocabulary (vertical slice / layer cake / carpaccio, Ch. 4; "How big should
your slices be?", Ch. 12). That material belongs in a planning/slicing
rules file, not here. Cited for completeness; not load-bearing for this
file's enforcement model.

---

## When to Apply

- Complex business logic with AI in the loop → full hexagonal
- Simple CRUD → no (Cockburn: "start with a fat object, split only as needed, and stop splitting as soon as possible")
- AI only helping write code (not a runtime actor) → Cockburn design principles only, skip code-first rules

---

## Not Covered by Docs (Define Yourself)

- DB schema conventions for AI pipelines
- Checker prompt design
- Retry thresholds per gate type

---

## Sources

### Design Principles

| Source | Status | Location |
|---|---|---|
| Cockburn, *Simplifying Software Design* (2026) | Extracted, fact-checked | <KL>/Development/Sources/Books/simplifying-software-design-cockburn/ |
| Cockburn, *Slice the Problem, Grow the Solution* (v0.9b, 2026) | Extracted, fact-checked | <KL>/Development/Sources/Books/slice-the-problem-grow-the-solution-cockburn-v0.9b/ |
| Cockburn, *Unifying User Stories, Use Cases, Story Maps* (2024) | Extracted, fact-checked | <KL>/Development/Sources/Books/unifying-user-stories-use-cases-story-maps-cockburn/ |
| Cockburn, *Hexagonal Architecture Explained* (2025) | Not extracted | — |
| Cockburn, original 2005 article | Available locally | `[YOUR_DRIVE_PATH]/path/to/file.pdf` |
| Martin, *Clean Architecture* (2017) | Not extracted | Same core idea, different terminology |
| Anthropic docs (5 sources) | Linked per section | See inline references |

### Python Implementation References

| Repo | Stars | Use for |
|---|---|---|
| [cosmicpython/code](https://github.com/cosmicpython/code) | ~2,600 | **Primary reference.** ABC ports, SQLAlchemy adapter, FakeRepository. O'Reilly book, free at cosmicpython.com |
| [cdddg/py-clean-arch](https://github.com/cdddg/py-clean-arch) | 189 | Adapter swapping demo — 5 DB backends behind one port |
| [Enforcer/clean-architecture](https://github.com/Enforcer/clean-architecture) | 563 | Auction platform, explicit Port/Adapter separation |
| [ivan-borovets/fastapi-clean-example](https://github.com/ivan-borovets/fastapi-clean-example) | 528 | FastAPI + DDD + CQRS + Unit of Work |
| [szymon6927/hexagonal-architecture-python](https://github.com/szymon6927/hexagonal-architecture-python) | 147 | Educational example (blog companion) |

### Community Discussion

| Source | Key takeaway |
|---|---|
| [r/Python thread](https://www.reddit.com/r/Python/comments/yusgzs/hexagonal_architecture_in_python/) | Domain objects ≠ ORM models (repo converts). SOLID = class level, hexagonal = architecture level. Bootstrap handles DI. |

*Created: 2026-04-09*
*Revised: 2026-04-09 — reorganized around hexagonal pattern (Cockburn), merged overlapping rules, added Python code-level rules and reference repos*
*Revised: 2026-06-12 — Pass 1 mechanical fixes (verbatim "fat object" quote, "Responsibility Alignment Test" name, §1.3 title correction, Sources table) + Pass 2 additions from newly extracted Cockburn books (Slice the Problem 2026; Unifying User Stories 2024): strong-implementation rule for AI Adapter, leakage-protection benefit on Dependency Direction, new "AI as a Runtime Actor" section with AI Hits prescriptions, 4-step nano-increment sequence, and use-case-as-boundary vocabulary*

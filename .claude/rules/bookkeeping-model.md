# Bookkeeping Model — Canonical

This file **is** the bookkeeping model. It defines how work and its artifacts are
tracked across two surfaces (the `TODO.md` task surface + the flat `Thoughts/`
graph), how those artifacts are named, linked, counted, anchored, audited, and
retired, and which layer enforces each rule. When a downstream skill, plan, or
design decision touches bookkeeping, it grounds itself here — nothing scattered.

**Authored:** 2026-06-23 (DS1 of `Thoughts/bookkeeping-model_PLAN.md`, Mode A,
`discovery_src_hash: b64acdcbbb5e`). Rewrites the prior 2026-06-16 target-state
sketch wholesale (R5 INPUT — fully replaced).
**Status:** active.

**How to read the enforcement labels.** Every rule below is tagged with its
layer from the R1 Trust Hierarchy (`code_first_architecture.md`) and its
grounding per R12 (`grounding.md`):

- **[Code]** — a hook/script/test enforces it; the operator cannot silently
  diverge.
- **[Rules]** — auto-loaded into every session; AI reads and applies judgment.
- **[Skill]** — guides a specific skill's behavior; no standalone enforcement.
- `source-cited` — traces to a named locked Discovery decision or an external
  authority. `(editorial)` — a copilot design choice with no external source.

The on-disk enforcement (slug grammar + the 11 G-cases) is implemented by
`${KIT_HOOKS_DIR}/bookkeeping-invariant.sh` + `bookkeeping_invariant.py`
(PostToolUse, read-only). This file is the human-readable contract; that module
is its executable form. The two must stay in lock-step — a change to the grammar
or the G-cases here is a change there (Cockburn Evolution Test: single locus).

---

## 1. Two surfaces, intertwined  [Rules] (source-cited: GP#1, Step 4 Substance)

The model has **two surfaces** that must stay coherent with each other:

- **`TODO.md` — per-task tracking.** Entries come from three sources: (i) a
  `_THOUGHT.md` file (as a `[Thought]` line), (ii) a TODO framed via
  `/work-frame-and-create-todo`, (iii) a user-typed manual line. Five active
  buckets (Now / Next / Nearby / Nascent / Scheduled) plus one terminal bucket
  **Never / Won't-Do** (a sibling of Done — tasks explicitly decided against stay
  `[ ]` here, visible as declined, never swept to Done). Active buckets are flat —
  no subsections (R8 flat-bucket invariant). Terminal buckets (Never, Done) are
  excluded from all active-work surfaces: counts, overdue/urgent scan, dependency
  scan, and the active-project test.
- **The Thought graph — big-things tracking.** Thoughts are large units of work.
  The operator works *on Thoughts*; while doing so, smaller TODOs surface
  (Plan-slices from a `_PLAN.md`, or manually-created linked TODOs).

**Intertwining:** every active Thought has a `[Thought]` line on `TODO.md`. A
clarified Thought flows `/clarification → /solution-design → /plan → _PLAN.md`
with Plan-slices (`GATE0SR:SLICES`), reflected on `TODO.md` as planned / in
implementation / finished.

---

## 2. Canonical vocabulary  [Rules] (source-cited: F1 closure, GP#8)

The operator's terms are **The Base**. Industry terms are cross-readability
bridges only — not authorities.

| THIS model (canonical) | Role |
|---|---|
| **Thought** | A large unit of work. Deliberately abstract; `/clarification` makes it concrete. One `_THOUGHT.md` spine per Thought. |
| **Scope** | The list of committed items for a Thought, plus its in/out boundary. Lives in the spine's `## Scope`. |
| **Scope Item** | One committed item. Either an **inline work item** (becomes a Plan-slice) or a **Thought-reference** (a wikilink to another `_THOUGHT.md`). |
| **Slice** | A delivery chunk within a `_PLAN.md` (`GATE0SR:SLICES`). |
| **Usage Scenario** | A supporting artifact on a `_THOUGHT.md` — one scenario through the intended solution (Cockburn Concept 12; a subset of a full Use Case). |

Bridges (footnote only, never authoritative): Thought ≈ Atlassian *Initiative* /
Patton *big rock*; Scope Item ≈ Atlassian *Epic*; Slice ≈ Atlassian *Story* /
Cockburn *vertical slice* (RG3 *Slice the Problem, Grow the Solution*). Patton's
caveat (ch.11): *"If you create language in your organization, don't try to be
too precise."* — these columns are bridges, not definitions.

**All Thoughts are on the same level.** There is no "master / Sub" distinction
and no depth (OQ6). A Thought may *reference* other Thoughts in its `## Scope`;
the referenced Thought's work is handled by its own chain. Adding or removing a
scope-reference is the only operation — there is no promotion/demotion, and the
referenced file never moves or renames.

---

## 3. The flat `Thoughts/` graph  [Code] (source-cited: OQ2, OQ10)

- All `_THOUGHT.md` files live as **flat siblings** in the `Thoughts/` folder of
  the working tree (root-level for cross-project topics; project-scoped for
  per-project topics). No hierarchical subfolders — mobility-friendly (a file
  doesn't move when scope-references change).
- **Staging paths are root-relative** (per `topic-workflows.md`):
  `<Projects-root>/Thoughts/`, `<Projects-root>/Personal/Thoughts/`,
  `<Projects-root>/<Work>/Thoughts/`. Never create a nested `Thoughts/` under a
  project CWD.
- **Cross-project lookup** uses state JSON
  (`~/.claude/state/pre_plan_gates/<topic>__<project>.json`) — no global
  topic-register file (OQ14).

---

## 4. Slug grammar — the four buckets  [Code] (source-cited: B1+B2 S1, A12, OQ12)

Every artifact related to one Thought shares that Thought's **slug** (lowercase
kebab, e.g. `bookkeeping-model`). `grep <slug> Thoughts/*` returns the whole
family — slug-grep is the manifest primitive (GP#4, OQ14). The hook owns every
file in `Thoughts/` matching one of these four patterns:

### Bucket 1 — Standard (mandatory members)
`<slug>[-<yyyymmddHHMMSS>]_<TYPE>.md`
TYPE ∈ `{THOUGHT, DESIGN, PLAN, RESEARCH, CLAIMS, THOUGHT_check}`.
The `-<ts>` timestamp is **mandatory for new files** (OQ7) and **optional in the
grammar** (`(?:-\d{14})?`) so existing untimestamped files are grandfathered.

### Bucket 2 — Scope-keyed (per-scope-item members)
`<slug>[-<ts>]_<SCOPE>_<TYPE>.md`
`<SCOPE>` = operator-chosen **UPPERCASE** label (e.g. `_K_PLAN`, `_F_RESEARCH`,
`_H_v2_B1_PLAN`). TYPE ∈ `{PLAN, RESEARCH, DESIGN, THOUGHT_check}`. UPPERCASE
`<SCOPE>` after the (lowercase-only) slug+ts disambiguates from Bucket 1.

### Bucket 3 — Advisory (recognized, opt-in linking)
`<slug>[-<ts>][_<SCOPE>]_<TYPE>[_<sid>].md`, two sub-categories:
- **User-driven on-demand:** TYPE ∈ `{META, CASES, DISCOVERY_WIP, DISCOVERY,
  NEXT_SESSION_PROMPT, ASSESSMENT, ADMISSION}`. (`ASSESSMENT` = an `/assess` engine
  `AssessmentRecord` persisted under the topic slug — assessment-engine A6.
  `ADMISSION` = one research **admission run**'s durable record — the evidence
  entries and degradation records written by `~/.claude/skills/research/`'s
  admission port, as `<slug>[-<ts>][_<SCOPE>]_ADMISSION_<run_id>.md` with the JSON
  in a fenced block — research-source-adapters S3 / design-A16, scope segment
  added by D6. **The scope segment is what keeps same-slug siblings apart**: a
  topic whose folder holds `<slug>-<ts>_RESEARCH.md` alongside
  `<slug>-<ts>_DRIVEAUTH_RESEARCH.md` shares one slug AND one timestamp across
  both (§5's stamped-once-reused-verbatim discipline), so without it the two
  research files would file one record between them.
  **Which research filenames actually reach this form, stated because the
  answer is not "all of them".** The resolver reads a slug out of the research
  file's own name and admits a scope **only alongside a 14-digit timestamp**
  (`declared_read.durable_store_for`) — the requirement that stops it
  manufacturing a topic slug from a name that carries none. So a Bucket-2 name
  with no timestamp is grandfathered onto the shared cold-session store rather
  than filed under its topic, as is a name whose scope begins with a registered
  TYPE (not Bucket 2 at all — Bucket 2 puts `<SCOPE>` before `<TYPE>`). Measured
  over the live corpus on 2026-09-02: of 251 reachable `_RESEARCH`/`_CLAIMS`
  files, 108 file under their own slug and **143 still share one store**, and 21
  of those 143 are genuine Bucket-2 files refused for want of a timestamp. This
  paragraph carries that limit because the grammar above describes a *name*,
  which says nothing about which files are given one.
  *(An eighth advisory TYPE was registered here 2026-08-29 by
  research-source-adapters S10 and **deregistered 2026-09-01 by slice D5**
  (in the live tree; not yet promoted to the config source, which still
  registers it), in the same change as its code half, when the per-claim scoring
  layer it served was descoped. Its whole description is removed rather than struck through, so
  this file names only TYPEs that exist; the full record is the
  research-source-adapters spine `## Q&A` Q22 / Q23 and git history. Nothing was
  stranded — no file of that TYPE existed anywhere on disk at removal, verified.)*
  **The enum in this line is a
  MIRROR**: the code-authoritative list is `ADVISORY_USER_TYPES` in
  `${KIT_HOOKS_DIR}/bookkeeping_invariant.py:39`, which is what `_ADV_USER_RE` is
  built from *(corrected 2026-09-01: this line named `RE_ADVISORY`, a symbol that
  does not exist in that file — a pre-existing rules/code drift found while
  making this edit, and exactly the class of silent divergence the sentence below
  warns about)*. Unlike the citation-marker registry there is **no drift guard
  comparing the two**, so a mirror-only edit fails *silently* — registering a TYPE
  means editing both, in one change.)
- **Fact-check ephemera:** TYPE ∈ `{R<N>, DOUBLECHECK, RECOMMEND, CHALLENGE}`
  (`R<N>` = `R` followed by digits).

Advisory files are slug-recognized (slug-grep returns them; they co-retire with
the family) but are **not** in the bidirectional contract — no `Parent:` line
required, the spine need not auto-list them. The operator MAY add wikilinks under
a `## Audit Trail` heading at choice.

### Bucket 4 — Out-of-scope (skipped)
`_AUDIT[_<date>]` and leading-underscore audit-trail variants are produced by
`/audit-session` and are independent of any Thought. A bare-name `.md` (no
`_<TYPE>` segment) is out-of-surface. Both are skipped entirely.

**TYPE → spine heading (`TYPE_TO_SLOT`)** — pinned in code; an OQ18 rename is a
one-line update:

| TYPE | Spine heading |
|---|---|
| DESIGN | `# Solution Design` |
| PLAN | `# Implementation Details` |
| RESEARCH / CLAIMS / THOUGHT_check | `# Discovery` |

### 4a. Machine-readable shared-path manifest  [Code] (source-cited: git-working-model S2 / A4, design A3 Cycle-7 SCL)

The set of **main-owned shared bookkeeping paths** — the surfaces every session
reads/writes through the one `main`-pinned resolver (S2/A1), that topic
worktrees `sparse-checkout`-exclude (S2/A3), and that the commit-gate allows on
`main` without a worktree (S2/A5) — has ONE machine-readable source of truth:
`${KIT_HOOKS_DIR}/bookkeeping-paths.json`. The table below is **generated** from
that manifest (single-source + code-gen link); a drift-check
(`python3 ${KIT_HOOKS_DIR}/bookkeeping_paths.py check-drift`, run automatically
in `claude-verify --phase pre`) fails on any divergence. **Do NOT hand-edit the
block between the sentinels** — edit the manifest and regenerate:
`python3 ${KIT_HOOKS_DIR}/bookkeeping_paths.py gen-prose --write`.

<!-- BEGIN GENERATED bookkeeping-paths -->
| Path | Match | Kind | merge=union |
|---|---|---|---|
| `TODO.md` | basename | code-writer | yes |
| `Thoughts/` | dir-prefix | spine | no |
| `Diary/` | dir-prefix | append | yes |
| `Stats.md` | exact | append | yes |

Harness (config-source) repo: recognized by `config-source-repo` — every commit in its primary clone is a main-side promotion, allowed structurally.
<!-- END GENERATED bookkeeping-paths -->

---

### 4b. Shared-file coherence — main-ownership, the canonical resolver, and the STABILIZED LOCK PRINCIPLE  [Code] (source-cited: git-working-model S2 / A4, S3 / A6, design A4)

The manifest paths in §4a are **owned by `main`**: they are edited only on `main`; a topic worktree
holds only its own non-bookkeeping artifacts and `sparse-checkout`-**excludes** the `main`-owned
paths, so a raw `cat`/`grep` of `TODO.md` inside a worktree **fails closed** (ENOENT) rather than
silently returning a stale frozen copy. This is what keeps shared bookkeeping coherent under
concurrency.

*(Retracted 2026-09-17, glittery-humming-pine S7: this paragraph previously ended "…and
kills the `/close`-sweep collision". It does not. Worktree topology keeps a topic's DOMAIN
files off `main`, but two sessions closing on `main` share ONE index, and a `/close` whose
commit named no paths published the other session's staged files — four cross-attributed
commits landed in eight days while this sentence said the collision was dead, which is part
of why it went unfixed. What actually bounds it is a declared commit scope — both verbs name
their paths (`git-policy.md` §4) — enforced by the surfaces, the pre-commit scope gate and the
build-time `check-publish-scope.sh`. Worktrees do not bound it either: a worktree's index is
per worktree, not per session.)*

- **Canonical `main`-pinned resolver (all readers).** Every reader of a `main`-owned path — the
  code-layer writers (`taskmanagement.py`/`work_done.py`/spine writers), `todo.py`'s vault-root
  resolution, the SessionStart TODO scan — resolves through the ONE `main`-pinned resolver
  (`bookkeeping_resolver.py`, extending §5 "spine binding outranks CWD" to `TODO.md`/`Diary`/`Stats`),
  never via `git --show-toplevel` on the worktree CWD. The sanctioned human read is `todo.py read`
  / a resolver-backed convenience alias / a read on the `main` checkout — never a stale-returning
  shim (that would reopen the coherence hole). The fail-closed ENOENT is deliberate (EDGE4).
- **STABILIZED LOCK PRINCIPLE — the single authoritative statement.** The shared `main` checkout has
  exactly **two writers — `main`-owned bookkeeping commits and topic-branch *lands* (`git-policy.md`
  §11) — and BOTH serialize under ONE repo-wide `flock`** (keyed on the git-common-dir;
  `bookkeeping_lock.py`). The invariant: **the lock spans only the local critical section and is
  strictly short** — the read-modify-write → `git add -A -- <paths>` → `git commit -m <msg> --
  <paths>`, BOTH verbs naming the session's declared paths (`git-policy.md` §4; a bare commit
  under the lock still publishes every session's staged files, because the lock serializes the
  writers but not the index), with no hook bypass (the scope gate and the bookkeeping-safety
  hooks both run; never `--no-verify`), atomic per session — and **no lock ever spans the network** (`push`/`fetch`/`pull`) **or unbounded human
  input**. A non-fast-forward push reconciles with an **ordinary non-interactive `git merge`**
  (`--no-edit`, `GIT_EDITOR=true`; append-style files favor `merge=union`, a genuine conflict
  auto-aborts and surfaces rather than leaving a `MERGE_HEAD`) — never rebase-under-lock, never a raw
  `update-ref` that desyncs the ref from the index/working tree. Verify-then-act atomicity is
  content-addressed (pin the approved SHA / re-check `main` hasn't advanced before acting), not held
  by a longer lock. This one statement is authoritative; `git-policy.md` §11 cross-references it and
  does not restate it. The exact `flock` wrapper + which commands sit inside it are plan/build-time
  mechanics; the invariant (local-and-short, ordinary-merge, one-lock-two-writers) is the rule.

---

## 5. File location + timestamped-slug discipline  [Code] (source-cited: OQ7)

- All **durable** artifacts live at `<spine_parent>/Thoughts/<slug>-<yyyymmddHHMMSS>_<TYPE>.md`.
  **Plans are the one authoring-time exception (DS5 REVERSED 2026-07-06).** While a session is
  in plan mode, the plan file MUST be authored at the harness-designated
  `~/.claude/plans/<harness-slug>.md` path (named in the plan-mode system message), NOT at the
  `Thoughts/` path. Reason: a *successful* Write to any non-designated file during plan mode makes
  the harness silently transition out of plan mode with no `ExitPlanMode` and no approval
  (reproduced live 2026-07-06; audits `session_issue_audit_20260706_665ca48a` /
  `…_f1f82bd9`) — so `check-plan-readonly.sh` permits ONLY `~/.claude/plans/*.md` during plan mode.
  The durable `Thoughts/<slug>-<ts>_PLAN.md` copy is produced by the **post-approval relocation**
  (`/plan` Step 11 → `pre_plan_gates.py` relocation CLI), which runs OUTSIDE plan mode. So
  `~/.claude/plans/<harness-slug>.md` is the canonical **in-plan-mode** target (NOT retired), and
  `Thoughts/` is the durable home reached only **after** approval.
- **Spine binding outranks CWD.** Resolution uses `topic_state.project_root` for
  active topics, else a walk-up to the nearest `TODO.md`. Code never silently
  uses CWD when there is a real ambiguity — it refuses and asks.
- **Auto-create `Thoughts/`:** if `<resolved-root>/Thoughts/` is missing at write
  time, create it and proceed. The only surviving refusal is when the root
  resolves outside `PROJECTS_ROOT` — then the operator picks the target.
- **Timestamp discipline:** stamped at the first artifact in the chain, reused
  verbatim for related artifacts. Same-second collision → second writer's
  timestamp bumped +1s (`O_EXCL` atomic create-or-fail). Existing untimestamped
  files are grandfathered.

---

## 6. Three Plan entry points  [Rules] (source-cited: OQ7)

1. **Standard:** `_THOUGHT.md → (_DESIGN.md) → _PLAN.md`. Default; topic went
   through `/clarification`. Counter: 3-bucket on the Thought's TODO line over
   its own scope items.
2. **TODO-owned:** `TODO (framed via /work-frame-and-create-todo) → _PLAN.md`. A
   framed TODO directly owns a Plan; no `_THOUGHT.md` intermediary. Bare manual
   TODOs cannot own a Plan — they must upgrade via `/work-frame-and-create-todo`
   or author a Thought via `/clarification` first. Counter: 3-bucket on the
   TODO's own line over the Plan-slices.
3. **Ninja-plan (Mode C):** `_PLAN.md` stands alone. The operator triggers
   `/plan` Mode C cold; that deliberate invocation IS the consent signal for
   off-bookkeeping work. **No TODO line, no per-Thought counter** — the slice
   register lives inside the `_PLAN.md` (`GATE0SR:SLICES`). Anchor-exempt.

`_DESIGN.md` exists only when `/solution-design` ran. A Thought has **1..N
Plans** (§7). Plans coexist independently — there is no "master Plan" rolling up
scope-item Plans.

**Authoring vs. durable location (2026-07-06, DS5 reversed).** In all three entry
points, a `_PLAN.md` is AUTHORED during plan mode at the harness-designated
`~/.claude/plans/<harness-slug>.md` (the only path that does not trigger the harness's
silent plan-mode exit — §5), and its durable `Thoughts/<slug>-<ts>_PLAN.md` copy is
created only AFTER `ExitPlanMode` approval by `pre_plan_gates.py
relocate-plan-after-approval` (`/plan` Step 11). The counters, entry-point semantics, and
slug grammar in this model describe the durable `Thoughts/` copy; the transient harness-path
file lives OUTSIDE `Thoughts/` entirely, so it is not in the §4 slug-grammar surface at all
(`bookkeeping_invariant.py` G0-skips it — "not in `Thoughts/`") until relocation.

---

## 7. Multi-Plan per Thought  [Rules] (source-cited: OQ7, B1+B2)

A Thought may carry more than one Plan, each covering a subset of its scope items:
- `<slug>-<ts>_PLAN.md` (bare-name) — the residual Plan: covers the scope items
  not split into per-scope-item Plans. When ALL items are split, no bare Plan is
  needed.
- `<slug>[-<ts>]_<SCOPE>_PLAN.md` — one per scope item warranting dedicated
  planning. Each has its own slice register.

Same shape for Designs and Research (bare + scope-keyed). **Implementation
sequence lives in `## Scope` via per-scope-item blocking annotations** (§11) —
NOT in the Plan section, NOT in the counter, NOT on the TODO line (OQ4).
Re-running `/plan` on an existing `_PLAN.md` is refinement (preserves
slug+timestamp), not a new file.

---

## 8. Bidirectional cross-link contract  [Code] (source-cited: B1+B2)

Navigation works both ways, code-enforced on every write:
- Open any `_THOUGHT.md` → the spine wikilinks every mandatory child under the
  `TYPE_TO_SLOT` heading.
- Open any child → a `Parent: [[...]]` line at the top points to the right
  parent.

**Parent-target rules** (computable from on-disk state alone):
- Bare-name `_DESIGN`, `_RESEARCH`, `_CLAIMS`, `_THOUGHT_check` → spine.
- Scope-keyed `_<SCOPE>_<TYPE>` for non-PLAN TYPEs → spine.
- Bare-name `_PLAN.md` → `_DESIGN.md` if it exists, else spine.
- Scope-keyed `_<SCOPE>_PLAN.md` → `_<SCOPE>_DESIGN.md` if it exists, else
  `_DESIGN.md` if it exists, else spine.

When **no** parent target exists in-family (a lone `_PLAN.md` with no spine and
no design — a TODO-owned or Mode-C root plan), no `Parent:` line is required:
the contract never demands a link to a nonexistent file. This is what keeps the
hook from false-positiving on root plans.

**Enforcement style — read-only hook, no auto-patch** (GP#10): the hook is a pure
function of `(path, content, on-disk siblings, per-pid pre-snapshot)`. On
violation it exits 2 with the exact fix on stderr. The operator pastes the fix;
the next save converges. No auto-patch eliminates the transactional-patch class
of bugs.

---

## 9. The eleven G-cases  [Code] (source-cited: B1+B2 "Eleven named cases")

The hook walks the touched file's slug-family and reports these cases. A
**retired** family (§13) is exempt from G3–G7.

| Case | Meaning | Verdict |
|---|---|---|
| **G0** | Touched file is in no slug-family (not in `Thoughts/`, or no `_<TYPE>`). | skip (exit 0) |
| **G1** | Family is clean. | pass (exit 0) |
| **G2** | Mode-C ninja-plan: singleton `{_PLAN.md}` bare-name family with `bookkeeping: mode-c` frontmatter. | pass (exit 0) |
| **G3** | A mandatory non-spine member is missing its `Parent:` line (and a parent target exists in-family). | **reject (exit 2)** |
| **G4** | The spine exists but is missing the wikilink to a mandatory non-spine member. | **reject (exit 2)** |
| **G5** | A `Parent:` target does not resolve to a file in the slug-family. | **reject (exit 2)** |
| **G6** | The spine used to link a child; it no longer does; the child still exists (order-dependent drop-link). | **reject (exit 2)** |
| **G7** | A `Parent:` points to a wrong-TYPE file in the family. | **reject (exit 2)** |
| **GA** | An advisory-bucket file is recognized. | info only (exit 0) |
| **GO** | `_AUDIT` or out-of-surface (bare `.md`). | skip (exit 0) |
| **GLC** | iCloud `<name> 2.md` collision in the family. | warn + reconcile guidance (exit 0) |

**Mode-C detection:** YAML frontmatter `bookkeeping: mode-c` on a singleton
bare-name `{_PLAN.md}` slug-set distinguishes intentional Mode C from a slug
typo. **G6** needs the per-pid pre-snapshot from the PreToolUse sidecar (DS4);
absent a snapshot, G6 is skipped (single-process-safe; cross-pid race is the
deferred `plan_locks` forward-design item).

---

## 10. Audit trail belongs to the topic  [Code] (source-cited: GP#6, B1+B2 S2 Q4)

Fact-check rounds (`R<N>.md`) and `/double-check` / `/recommend` / `/challenge`
drafts live under the slug as **advisory** artifacts:
`Thoughts/<slug>-<ts>_<TYPE>_<sid>.md`. `grep <slug> Thoughts/*` returns the
complete chain from `/clarification` through implementation. The topic's history
co-retires with it via slug binding. Fallback to
`~/.claude/state/plan_validation/adhoc/` is preserved **only** for cold-start
sessions with no bound topic. (Writers re-routed in DS6/DS7.)

---

## 11. TODO surface — statuses, counter, tags, blocking  [Code/Rules] (source-cited: OQ1, OQ4)

**Four statuses, no fifth** (OQ4):
- `not in progress`
- `in progress 🚧 since YYYY-MM-DDTHH:MM:SS` (the timestamp is an attribute on
  the existing status, not a new status)
- `DONE` `[x]` + `DONE YYYY-MM-DD`
- `Won't Do` (an out-of-scope item becomes `Won't Do (Out of Scope)` in the
  Thought's `## Scope`)

**Note — two distinct "won't do" surfaces:**
- **TODO-surface `## Never` bucket** — for any task the operator decides to never
  pursue. The item stays `[ ]` (visibly declined, not deleted). `cmd_done` refuses
  to mark it `[x]`; cleanup never sweeps it. Filed via `add --bucket NEVER`.
- **Scope-item `Won't Do (Out of Scope)` status** — for a Thought's scope item
  that was scoped in and later removed. Lives in the Thought's `## Scope`, not in
  `TODO.md`.

These are two different mechanisms on two different surfaces; neither subsumes the
other.

**3-bucket counter** [Rules] — each Thought's TODO line surfaces three buckets
over its **own** scope items, no tree rollup: **In progress** (count + names),
**Remaining** (count only), **Done** (count + names). Inline work items and
Thought-references count uniformly; a referenced Thought's own progress is one
click away on its own TODO line.

**Adhoc Created Items — slug-tag** [Code] (REVISED 2026-06-20) — when a new TODO
relates to a not-finished Thought, tag it with that Thought's slug:
`- [ ] [<slug>-<ts>] todo text`. Multiple relations → multiple tags. Filter via
`grep <slug>-<ts> TODO.md`. **No cross-file mirroring** (the old
mirror-under-Scope mechanism is deprecated — the tag is the single source of
truth).

**Per-scope-item blocking** [Rules] (source-cited: OQ1 residual 2) — any scope
item may declare it is blocked by zero or more other scope items in the same
Scope. Mutable at any time. A Thought cannot reach `Retired` while any scope item
is still `in progress` (OQ1 residual 3) — each item must reach DONE or move
Out-of-Scope. `/solution-design` warns on missing/incomplete scope-item Thoughts
but proceeds on strict user confirmation (residual 4); its rounds may run
concurrently with a referenced Thought's `/clarification` (residual 1).

**Diary links** [Code] — every Done item ends with a `[[YYYY-MM-DD]]` diary
wikilink (DS7 validator + `check-todo-direct-write.sh`).

**One lock per TODO** [Code] (source-cited: OQ3, OQ8) — a session = one
`/work-start → /work-done` cycle = one lock lifetime. A mid-session
`/clarification` runs under the same lock; the new `_THOUGHT.md` is
simultaneously a delivered artifact of that session (linked from the TODO line)
AND an independent Thought with its own `[Thought]` line. The owning TODO is
**not blocked** by the new Thought.

---

## 12. Anchor-by-discovery  [Code] (source-cited: OQ5, GP#7)

- Sessions run **anchorless** — no session-start anchor requirement.
- The **first write** to a tracked surface (`TODO.md`, a `_THOUGHT.md`, or a
  `_PLAN.md`) triggers an inline `/work-frame-and-create-todo` prompt.
- A cold `/clarification`'s new `_THOUGHT.md` is **its own anchor seed** — the
  prompt fires after the Thought is created, not at session start.
- A Mode-C ninja-plan is **anchor-exempt** (the Mode C invocation is the consent
  signal; no refusal tracked).
- If the operator refuses to anchor when prompted, the refusal is tracked.

---

## 13. Retirement  [Code] (source-cited: GP#9, B1+B2)

- Mark the spine `**Status:** Retired YYYY-MM-DD` (normal Thoughts), or
  `bookkeeping: retired-YYYY-MM-DD` frontmatter (Mode-C ninja-plans, no spine).
- The whole family then goes dormant and is **exempt from G3–G7**.
- Co-move with one command: `mv Thoughts/<slug>-* Thoughts/_retired/` — the slug
  binds spine, Designs, Plans, Research, Claims, and the fact-check trail
  together.
- Finished predicate: a `_THOUGHT.md` is "finished" iff its `**Status:**` line
  contains `Retired YYYY-MM-DD` (`work_done._RETIRED_TOKEN_RE`).

---

## 14. Cross-topic ownership  [Rules] (source-cited: OQ11)

- Every artifact carries exactly **one** owning slug. There is no multi-owner
  artifact.
- When Thought B needs an artifact owned by Thought A, B's `## Scope` carries a
  wikilink to A (a Thought-reference scope item) and B's spine wikilinks the
  specific artifact. No duplication, no symlink, no shared-slug file.
- When a piece genuinely sits between two Thoughts and neither owns it more, the
  operator authors a **third** Thought to own it; the other two scope-reference
  it.

---

## 15. Drift-as-code — the ten drifters  [Code] (source-cited: B3 closure; row 10 added 2026-07-06)

Known drifters become enforced contracts; the drift list is the single source of
what to maintain. Nine are code-enforced, one is WARN-only (structural).

| # | Drifter | Enforcement |
|---|---|---|
| 1 | `/work-start` mints durable orphan `plain-*` state on an unresolved wikilink | `check-work-start-wikilink.sh` + `reap_orphan_plain_topics.py` (DS8) |
| 2 | Verifier-isolation snapshot scope too narrow | extend `check-verifier-isolation.sh` to `rules/* + skills/**/SKILL.md + agents/* + state/**/*.json` (DS8) |
| 3 | Manual `TODO.md` write bypasses framing + diary-link validation | `check-todo-direct-write.sh` PostToolUse (DS7) |
| 4 | Multi-Bash chained writes violate the invariant mid-chain | **WARN-only** — per-subcommand visibility is structurally impossible (PostToolUse fires once per tool call) |
| 5 | iCloud `<name> 2.md` collisions accumulate | `reconcile_icloud_collisions.py` — **operator-run** quarantine CLI (DS8); SessionStart auto-reaping deferred (see Row 5 note below) |
| 6 | `_CLAIMS.md` never auto-creates | `claims_registry.ensure_exists()` at engine round 0 (DS7) |
| 7 | Cold `/clarification` `_THOUGHT.md` write doesn't trigger the anchor prompt | PostToolUse on new `_THOUGHT.md` (DS8) |
| 8 | Advisory TYPEs uncovered by the grammar | extend `_ADV_USER_RE` (DS4) *(named `RE_ADVISORY` until 2026-09-01; no such symbol exists — same pre-existing drift corrected in §4 Bucket 3)* |
| 9 | `work_done._STATUS_LINE_RE` matches body, not preamble | constrain to lines above the first H1 (DS8) |
| 10 | Plan authored to a project `Thoughts/…_PLAN.md` **during plan mode** → the harness silently exits plan mode, bypassing `ExitPlanMode` approval + all plan gates (reproduced live 2026-07-06; audits `…_665ca48a` / `…_f1f82bd9`) | `check-plan-readonly.sh` permits ONLY `~/.claude/plans/*.md` in plan mode (A2, primary); `check-uninitiated-plan-exit.sh` halt-and-surface backstop for any residual silent exit (A6); durable `Thoughts/` copy via post-approval `pre_plan_gates.py relocate-plan-after-approval` (A3) |

**Row 6 scope (documented exception, 2026-06-24).** `ensure_exists()` fires at
the first fact-check round for **research and thought** kinds only. The B3 Row 6
spec also listed `kl_extraction`, but that is **deliberately excluded**: KL book
extractions route through a separate scribe (`aggregate_kl_extraction_round`),
have no project-side path to place a registry, and use evidence files + coverage
markers + their own API-attested provenance (`checker_models_raw`,
`provenance: code-verified`) — not the URL-cited research/initiative `_CLAIMS`
registry (`claims-registry.md`). Creating a `_CLAIMS.md` in a book's `Chapters/`
would be a category error and spurious drift. (Rationale per the model's own
"documented exception with rationale" path.)

**Row 5 form (documented exception, 2026-06-24).** Row 5 (and the Desired
Outcome "iCloud collisions reaped deterministically at SessionStart") specified a
**`reconcile-icloud-collisions.sh` SessionStart hook**. What shipped is
`reconcile_icloud_collisions.py` — an **operator-run, quarantine-only, dry-run-default**
CLI (used once as DS9 migration prep to reconcile 97 `Thoughts/` twins). It is
**not** registered as a recurring SessionStart hook, so collisions are surfaced/
reaped **on demand**, not automatically each session. Rationale: a SessionStart
hook that auto-moves files every session is the riskiest automation class;
shipping a conservative operator-run tool first is the safe increment. **Future
enhancement (not yet built):** a SessionStart wrapper that quarantines exact-dups
and warns on divergent twins would deliver the original auto-reaping outcome —
done deliberately, test-net-first, inside the verifier-isolation pair.

---

## 16. Enforcement-layer summary  [Rules]

| Concern | Layer | Mechanism |
|---|---|---|
| Slug grammar + 11 G-cases | Code | `bookkeeping_invariant.py` + `bookkeeping-invariant.sh` (PostToolUse, read-only) |
| G6 drop-link snapshot | Code | PreToolUse sidecar `snapshot_<pid>.json` (DS4) |
| Existing-corpus backfill | Code | `bookkeeping_migrate.py` (idempotent, grandfather-aware) |
| Audit-trail-under-slug | Code | `bound_topic` + slug-scoped writers (DS6/DS7) |
| Ten drifters | Code (9) + WARN (1) | dedicated hooks (DS7/DS8; row 10 = plan-mode silent exit, 2026-07-06) |
| Plan authored at harness path in plan mode; project write blocked | Code | `check-plan-readonly.sh` (PreToolUse, only `~/.claude/plans/*.md` in plan mode) |
| Un-initiated plan-mode exit → halt-and-surface | Code | `check-uninitiated-plan-exit.sh` (PreToolUse `.*`) + `plan-exit-ack` / `plan-mode-init` re-arm |
| Post-approval plan relocation to `Thoughts/` | Code | `pre_plan_gates.py relocate-plan-after-approval` (outside plan mode) |
| Counter / blocking / vocabulary / entry points | Rules | this file + AI judgment |
| Mode-C frontmatter, `_DESIGN.md` separation, `_AUDIT` exclusion | Skill | `/plan`, `/solution-design`, `/audit-session` (DS5) |

---

## 17. Carry-over / supersede vs adjacent rules  [Rules]

| Adjacent rule | Disposition |
|---|---|
| `topic-workflows.md` (R4) | Carries over Thoughts/ staging, templates, promotion. Supersedes the topic-artifact table (extended with `_DESIGN`/`_PLAN`/Slice; Thoughts are flat — no Sub distinction). |
| `todo-management.md` (R7/R8) | Carries over 5N buckets, `[Thought]` tag, framing validator, flat-bucket invariant, three-source origin. Supersedes the single `Sessions: M/N done.` counter (→ 3-bucket per Thought). |
| `plan-gates.md` (R10) | Carries over Gate-0 markers; `GATE0SR:SLICES` is the Plan-slice cardinality seam. |
| `factcheck-convergence.md` (R11), `code_first_architecture.md` (R1/R2/R3) | Carry over verbatim. |
| `claims-registry.md`, `periodic-reviews.md` | Out of scope (orthogonal). |
| State-dir + lock + atomic-write conventions | Code layer (`taskmanagement.py`, `work_done.py`) — referenced, not redefined. |

---

*Provenance: full model locked in `Thoughts/bookkeeping-model_THOUGHT.md`
`# Discovery` (Steps 3–9, `discovery_src_hash: b64acdcbbb5e`) and the
`### Solution Alternative 1` champion. This file is Scope slice #1 / DS1.*

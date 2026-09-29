# Git Policy — Canonical

Single source for git branch / commit / recovery policy across both repos.
Supersedes the `## Branch Policy` block in Projects-root `CLAUDE.md` (which now
references this file).

**Authored:** 2026-07-09. **Status:** active.
**Key change vs. the 2026-06-23 CLAUDE.md policy:** the two repos no longer share
one model. The **Projects** repo uses GitHub flow; the **`~/.claude` harness** repo
is config-source-managed and promoted via `claude-promote` — direct commits to
`~/.claude/.git` are frozen (`claude-infra-overhaul` S2, 2026-07).

---

## The canonical git working model — index (worktree-per-topic)

This file is the **one discoverable source** for how git works in both repos. The model is
**worktree-per-topic**: every unit of work runs in its own branch + folder so concurrent topics
cannot collide; shared bookkeeping stays coherent on `main`; nothing reaches `main` until the
*merged* result verifies; and staging→prod promotion runs as one supervised worker. Built and
shipped across `git-working-model` slices S1–S7 (spine
`Thoughts/git-working-model-20260714130221_THOUGHT.md`; frozen design `…_DESIGN.md`).

**The pillars, and where each is codified** (rules = the human-readable contract; the cited code is
authoritative — `code_first_architecture.md` trust hierarchy):

| Pillar | Codified in | Authoritative code |
|---|---|---|
| Worktree-per-topic topology + push-safety | §3 | `worktree-helper.sh`, `worktree-detect.sh`, `check-worktree-push-target.sh` |
| Commit-chokepoint gate (block-with-override) + work-init auto-placement | §3 | `check-worktree-commit-gate.sh`, `worktree-helper.sh place` |
| Legacy cutover (clean relocate / dirty transactional migration) | §3 | `worktree-helper.sh` cutover, `worktree_cutover.py` |
| Cross-session continuity (resume to existing worktree; handoff worker) | §3 | `handoff-worker.sh`/`handoff_resume.py`, `land_readiness.py` |
| Shared bookkeeping owned by `main` + canonical resolver + **STABILIZED LOCK PRINCIPLE** | `bookkeeping-model.md` §4a (manifest) + §4b (ownership/resolver/lock) | `bookkeeping_resolver.py`, `bookkeeping_lock.py`, `bookkeeping-paths.json` |
| Verify-then-land gate + one land **port** / two adapters | §11 | `verify-then-land.sh`, `land_port.py` |
| Automated promotion worker (staging→prod) — **designed, NOT wired; refuses. Use `claude-promote`** | §2 + §11 | `promote-worker.sh`, `land_port.py` (`PromotionAdapter`), `hooks/land-port-revival-checklist.md` |

The **shared-file coherence half** (main-ownership, the canonical `main`-pinned resolver, the
STABILIZED LOCK PRINCIPLE) lives in `bookkeeping-model.md` §4b (its existing shared-path domain,
alongside the §4a manifest); this file cross-references it rather than restating it, so each rule has
one authoritative statement, not a maintained twin. The **Projects side** of the model is gated on `[[storage-decouple]]` (slice S9);
the harness side ships independently.

**Codification provenance (S8 / A11 · OQ2 one-pass clause measurement).** The material above is
codified **in place** — extending this file and `bookkeeping-model.md`, with no new
`~/.claude/rules/git-working-model.md`. That placement was decided by the OQ2 one-pass clause
measurement: of the 13 net-new clause-clusters, **13/13 extend an existing section** of these two
files (0 genuinely separable), so the net-new material is large but HIGH-overlap; the OQ2 criterion
(split only if *large AND low-overlap*) is not met → extend in place. See
`Thoughts/git-working-model-20260714130221_S8_PLAN.md`.

---

## 1. Projects repo — GitHub flow

- `main` is the source of truth and stays deployable/usable.
- Day-to-day work on a **short-lived feature branch** → open a **PR** → merge back
  to `main` **promptly** → **delete the branch** after merge.
- Do not let a feature branch live for weeks (the drift this policy prevents).

## 2. `~/.claude` harness repo — config-source promotion (supersedes GitHub-flow-on-harness)

The harness is **not** changed by committing to `~/.claude/.git` — that repo is a
**frozen local snapshot** (no writer commits to it post-S2). The source of truth is
the **config source tree at `<config-source-repo>/`** (pushed to the `config-repo`
remote). Live `~/.claude/` is the *deployed* copy, reached only via the deploy step.

- **Experiment first for executable modules (never edit live config for
  experimentation).** A broken edit to a config-source-managed prod module that live skills
  load on demand (`hooks/*`, `*.py`) breaks those skills for *all* sessions. Develop
  + test such edits in a `claude-experiment spawn <label>` clone first — a CoW copy of
  the **managed scope only** (`agents bin CLAUDE.md hooks rules settings.json skills`)
  at `~/.claude-staging-<label>/`, CLAUDE_CONFIG_DIR-redirected; unmanaged dirs
  (`projects/`, `cache/`, `history.jsonl`, `statsig/`, `sessions/`…) never enter it.
  `claude-experiment {promote,discard,list,refresh}` manage the clone; `promote`/
  `discard` are **non-mutating on live**. (A pure docs/rules-markdown add carries no
  runtime-execution risk, so it may be authored live — but it still ships via
  `claude-promote` below.)
- **Promote, don't commit.** `claude-promote` = the capture step (capture live edits)
  → short-lived-branch **PR** → merge (**merge, not rebase**) → the deploy step →
  **`green-<yyyymmdd-HHMM>` recovery tag** → `claude-verify` bookends. Never hand-commit
  the frozen `~/.claude/.git`.
- **`/close` step 4** routes harness changes through `claude-promote` automatically.
- **PR-review mode is per-repo** (`.pr-mode` at the config source root): `quick`
  (solo self-merge, the default) vs. `extra-safety` (blocks self-approval via script
  refusal + GitHub native rejection).
- **Drift-clean gate:** `claude-divergence-check` enforces the staging-≥-prod
  invariant (the drift check destination column; exit 1 on an un-captured direct
  prod edit, naming the drifted path + stage-forward remediation).
  `claude-verify --phase pre` = structural only (valid `settings.json` +
  executable `hooks/*.sh`; deliberately skips the deploy verification so it never
  false-positives mid-promotion); `--phase post`/standalone adds the deploy verification
  (live == managed state).
- **The land primitive backs promotion.** `claude-promote` is the harness **adapter** of the one
  land primitive (`dry-run/diff → apply → promote`). The automated promotion worker and the
  verify-then-land gate that share that primitive — plus the per-adapter rollback — are codified
  together in §11.
- **`claude-promote`'s deploy-time gate is the Tier-2 verify-then-land gate for harness *code*
  deploys** (client-side; `harness-land-model-adoption-miss` S3/S5). Before the promotion branch
  reaches origin, `claude-promote` classifies the just-committed change (via
  `harness_code_paths.py`): docs/rules-only promotions skip the gate (no false friction); a
  promotion carrying any harness **code** must render the candidate and pass the canonical check
  (`claude-verify --phase pre`) against the *rendered* tree, then publish the branch **plus** a
  `refs/notes/verify-then-land` receipt note **atomically** — or nothing lands. Being client-side,
  §8's caveat still applies: **real enforcement is server-side/CI** — the S4 CI mirror is the server
  (Tier-3) tier that reads the receipt note and rejects a receipt-less PR; this client gate is the
  practical fast feedback, not the authority.
- **Sanctioned, logged override.** A genuine emergency may skip the check with
  **`VERIFY_THEN_LAND_SKIP=1` AND `--emergency-skip`** (both required for the one invocation — a
  stray exported env var alone, or the flag alone, does NOT bypass; never a blanket `--no-verify`).
  A non-empty reason is required and is recorded as a `refs/notes/verify-then-land-skip` note
  (pushed atomically with the branch so CI sees the sanctioned skip rather than a silent
  receipt-less PR). **Human-judgment residual (rules-layer, not code):** whether a newer model is
  now governing the change, and whether the situation is a genuine emergency, are not
  machine-decidable — they stay operator judgment at override time (per the plan's Guiding Policy).

## 3. Worktrees & wrong-target push-safety (both repos)

For parallel topics, use **one git worktree per topic** (`claude-infra-overhaul` S5 —
worktree = topic, orthogonal to the `~/.claude` config clone). Live repos live in the
shared out-of-iCloud object store `~/repos/`; see
`~/.claude/docs/worktree-per-topic-runbook.md`.

- **Push-safety hook.** `${KIT_HOOKS_DIR}/check-worktree-push-target.sh` is a **git
  pre-push** hook (fires at the git layer for ALL pushes — terminal, IDE, script). It
  reads each pushed branch ref, looks up `branch.<name>.remote`, and **blocks a push
  whose target ≠ the configured upstream**, printing the correct target
  (`git push <expected> <branch>`).
- **Commit-chokepoint gate (git-working-model S1).** `${KIT_HOOKS_DIR}/check-worktree-commit-gate.sh`
  is a **git pre-commit** hook that **blocks a commit made outside any topic
  worktree** (block-with-override) — the client-side Tier-2 enforcement ceiling. It
  routes through the ONE canonical detection primitive `${KIT_HOOKS_DIR}/worktree-detect.sh`
  (`--git-common-dir` vs `--git-dir`; `pre_plan_gates._in_worktree()` is now a thin
  wrapper over that same primitive — one detector, not two). Override:
  `ALLOW_OUT_OF_TREE=1` (positive opt-in, mirrors `PUSH_TARGET_CHECK_SKIP`; never a blanket
  `--no-verify`, never a stdin prompt). **Allow-rule (S2, structural):** a commit on `main`
  touching **only** `main`-owned bookkeeping paths is allowed without prompting; a **mixed**
  commit (bookkeeping + domain) falls through to block-with-override with a guided message. The
  allow-set is read from the single machine-readable `bookkeeping-paths.json` manifest — never
  regex-scraped from prose (see `bookkeeping-model.md` §4a). This replaced the S1→S2 interim
  `ALLOW_OUT_OF_TREE=1` bridge `claude-promote` used on its own config-source-`main` commit.
  **Scope stage (glittery-humming-pine S2 → enforcing since S6).** BEFORE any of the
  placement/ownership exits above — including inside linked worktrees, whose index is
  shared per worktree, not per session — the gate asks whether the commit NAMED its paths
  (`GIT_INDEX_FILE` basename `next-index-<digits>` = declared; anything else, incl. `index`
  and the `index.lock` that `-a`/`-i` hand it, = undeclared). An undeclared commit with
  something staged is **refused**, naming the paths and the scoped command. Override:
  `ALLOW_UNSCOPED_COMMIT=1` (its own variable — `ALLOW_OUT_OF_TREE=1` does NOT buy a scope
  exemption). In-progress merge/cherry-pick/revert/rebase states are exempt, because their
  `--continue` runs the hook against the shared index with no pathspec — a genuine hole the
  surfaces cover, not a safe case. The rule itself is §4. Coverage of both hook types
  (`pre-commit` gate + `commit-msg` waiver trailer) across the gated repos is checked by
  `worktree-helper.sh coverage`.
- **Defaults:** fail-open on branches with no upstream; skips delete/tag/non-branch
  refs. Escape hatches: `PUSH_TARGET_CHECK_SKIP` / `PUSH_TARGET_CHECK_VERBOSE`.
- **Install is now automated (S1 — the `claude-worktree-init` follow-up is built).**
  `${KIT_HOOKS_DIR}/worktree-helper.sh` is the shared create-or-lookup helper +
  installer: `worktree-helper.sh install --repo <repo>` installs BOTH hooks into the
  repo's shared hooks dir (resolved via `git rev-parse --git-common-dir`, so one
  install covers all worktrees). Install is **non-destructive** — an existing
  regular-file hook is chained behind the gate via a generated wrapper (never
  silently disabled); an alien hook symlink fails-closed. `/work-start --worktree`
  auto-places a topic into `~/repos/<repo>/<topic>/` (harness repo-binding = the
  config source repo) and installs the hooks in one step. (The full rules codification of the
  worktree-per-topic model + the holistic `CLAUDE.md` git-rule references shipped as **S8** — see
  the index at the top of this file; the Projects-side repo-binding is **S9**, gated on
  `[[storage-decouple]]`.)
- **Work-init auto-placement (S7 · A14).** Every work-initiation surface offers the same one-choice
  `--worktree`-opt-in placement `/work-start` offers — `/clarification`, `/solution-design`,
  `/plan`, `/execute-plan`, plus opt-in stubs for `/work-decode` and `/ninja-fix` (the
  `/extract-knowledge` KL-repo binding is not config-source, so its activation is deferred to S9) — so a
  topic lands in its worktree with no manual naming/finding/creating. All call the shared
  `worktree-helper.sh place`; the non-`--worktree` default is unchanged.
- **Legacy cutover (S5/S6).** A one-time step migrates a currently-active non-worktree branch into
  `~/repos/<repo>/<topic>/`. **Default = relocate the branch, no stash** when the checkout is clean;
  only when it is **dirty** is the uncommitted work carried transactionally (`git stash -u` →
  `git worktree add` → `git stash pop --index`), with a config-manifest copy for required gitignored
  files, submodule + sequencer pre-flights, and bookkeeping edits drained to `main` first.
  **Safe-defaults are load-bearing here:** never `git clean -f`, never `git stash drop`, never
  `reset --hard`/`--force` — an untracked `main`-owned file the drain did not produce halts the
  cutover for the operator; a `worktree add` failure unwinds transactionally (`safe-defaults.md`).
  Code: `worktree-helper.sh` cutover + `worktree_cutover.py`.
- **Cross-session continuity (S7).** Resuming a topic (a pasted handoff prompt, or any `--worktree`
  work-init surface) routes back into the topic's **existing** worktree via the shared
  create-or-lookup LOOKUP arm — never a fresh collision, never a duplicate. An advisory per-topic
  land-readiness sidecar (`land_readiness.py`; `in-progress` | `ready-to-verify-then-land`) is a
  **routing hint only** — the §11 verify-then-land gate re-computes green/red at land time, so a
  stale hint can never cause an unsafe land. The cross-session handoff runs as an out-of-process
  worker (`handoff-worker.sh` → `handoff_resume.py`) reusing the §11 promotion-worker pattern; a
  ready-to-land resume always routes through the §11 gate, never a bare merge.

## 4. Shared rules (both repos)

- **Merge, not rebase** shared/pushed history — reconcile with a merge commit;
  rebasing already-pushed history rewrites shared/live state (GitHub docs).
- **Never force-push the default branch.** `main` advances by fast-forward or merge
  only.
- **Commit or push only when asked.** If on the default branch, branch first.
  End commit messages with the `Co-Authored-By:` footer; end PR bodies with the
  Claude Code footer.
- **Don't squash granular history.** Per-slice/per-commit structure is what makes
  surgical revert/redo possible — the granular history is the asset, not a mess to
  tidy. (diary 2026-06-23 Session 1)
- **Every commit names the paths it publishes — BOTH verbs.** Git's index is one file
  per worktree, shared by every session working in it, so a `git commit` that names no
  paths publishes whatever ANY session has staged. A scoped `git add -- <paths>` does
  **not** bound a bare `git commit` (measured); and `git commit -- <untracked>` fails
  outright, so the add must run first. The form is therefore always:
  `git add -A -- <paths>` then `git commit -m <msg> -- <paths>` — or, in one process,
  `python3 ${KIT_HOOKS_DIR}/commit_scope.py publish -m <msg> -- <paths>`. Never a
  whole-tree `git add -A` / `add .` / `commit -a`. Declare individual files, never a
  directory (a directory pathspec sweeps every session's files beneath it). An empty
  declaration publishes nothing — it is never a reason to fall back to a bare commit.
  (glittery-humming-pine; four cross-attributed commits in eight days, 2026-08.)
  - *Enforced by two independent layers, plus a runtime backstop.* The two layers: the
    converted publish surfaces (`/close`, `/ninja-fix`, `claude-promote`, execute-plan,
    starter-kit) publish scoped whether or not any hook runs; and
    `hooks/check-publish-scope.sh`, run by `claude-verify --phase pre`, refuses to ship a
    surface that would commit unscoped. The pre-commit gate (§3) REFUSES an undeclared
    commit at run time, but it is a backstop rather than a third independent layer: it
    shares its discriminator with its own refusal message, and a client-side hook is not
    enforcement (§8) — `--no-verify` bypasses it and leaves no trace, which is why that
    flag is banned (`safe-defaults.md`).
  - *The one surface nothing catches automatically — write it this way.* A code surface
    (`*.py`, `*.sh`, a fenced command in a SKILL.md) is scanned generically. A **prose**
    publish surface — a SKILL.md that tells the model to "commit" without naming the
    command — has nothing to parse, so the build-time check cannot find it. If you write
    one: put the scoped command in a fenced block (`commit_scope.py publish … -- <paths>`)
    and register the file in `PROSE_SURFACES` in `hooks/publish_scope_scan.py`, which
    holds it to naming that command from then on. Until registered, only the runtime
    gate stands behind it.
  - *The named override* is `ALLOW_UNSCOPED_COMMIT=1` for ONE commit. It leaves the
    hooks running, so the commit carries an `Unscoped-Publish:` trailer in history.
    It is a deliberate choice, never a way past a refusal you do not understand.
  - *Jointly-owned append files* (`TODO.md`, `Diary/`, `Stats.md` — `merge_union`)
    cannot be separated by any declaration: whichever session commits first carries
    both sessions' lines. `publish` announces that before committing; it does not
    split the file.

## 5. Recovery / undo

- **Safe undo = `git revert`** (inverse commit, preserves history), **never
  `git reset --hard`** on shared history. (diary 2026-06-23 Session 1)
- **Revert newest → oldest** when later commits depend on earlier ones.
  (diary 2026-06-23 Session 1)
- **Checkpoint tag before risky changes** (`<topic>-vN` = a stable return point)
  and **before touching `main`** (`pre-main-reconcile-<date>`).
  (diary 2026-06-23 Session 2)
- **Harness recovery baseline:** the `green-<ts>` tags `claude-promote` lays are the
  known-good return points; `claude-rescue` is the standalone out-of-namespace
  recovery path.

## 6. One-time reconciliation onto `main`

- Lay a checkpoint tag/branch first (reversibility).
- For the live `~/.claude` harness, prefer advancing `main` by **pointer-move + push**
  over churning live config on disk (now expressed through the config-source promotion path).
  (current CLAUDE.md + diary 2026-06-23 Session 2)

## 7. Branch protection

- Deferred — GitHub branch protection / rulesets require **Pro on private repos**;
  the **written policy is the safeguard**. Rulesets are GitHub's recommended
  mechanism; **block-force-push + block-deletion are the only defaults**; require-PRs
  is team-oriented, not a solo baseline. (diary 2026-06-23 Session 2 + GitHub docs)

## 8. Enforcement ≠ client-side hooks

- **Version-control-first is Tier 1** (highest trust; survives runtime-guard failure).
  For real enforcement use **server-side / CI**, not client-side hooks —
  git-scm: "if your intent is to enforce a policy … do that on the server side."
  AI agents bypass pre-commit hooks via `--no-verify`.
  (`Thoughts/claude-infra-overhaul_Q16_RESEARCH.md`; git-scm)

## 9. Git operational gotchas (learned)

- Existence check: `git cat-file -e <ref>:<path>` — **not** `git ls-tree … && echo`
  (ls-tree exits 0 on an absent path → false positive).
- `git merge-tree --write-tree A B` (the real merge algorithm) is authoritative for
  "what `git merge` will do"; the legacy 3-arg form counts conflict *hunks*, not files.
- `git branch -d` compares against the upstream **tracking** ref, not HEAD. Use
  `-d` **only** — if it refuses because the commits are already in `main` but not the
  branch's tracking ref, **reconcile** (fetch / set the upstream so `-d` sees the merge)
  rather than force-deleting. Do **not** use `git branch -D`: it force-deletes unmerged
  commits, and the absolute-safe-default policy (`~/.claude/rules/safe-defaults.md`) bans it.
  (Aligned 2026-07-20 with the safe-defaults ban — S4/A6 of readonly-skills-structural-safety;
  supersedes the prior "use `-D` when the commits are already in `main`" guidance.)
- Conflict resolution: resolve `--ours` when the branch is the comprehensive current
  state and the incoming hunks are stale. (all: diary 2026-06-23 Session 2)

## 10. Command mechanics

See `~/.claude/rules/subagent-tools.md` — always `git -C "path" <subcmd>` (never
`cd "path" && git …`); use the `gh` CLI for GitHub operations (PRs, issues, API).

---

## 11. The land primitive — verify-then-land gate, one port / two adapters, promotion worker (both repos)

Nothing reaches `main` until the **merged** result has been checked and passed. This is the
**verify-then-land gate**, and it is the *same* primitive that runs staging→prod promotion — built
once, instanced per repo.

**Read the two instances differently — they are not both live.** The **verify-then-land**
instance ships and runs (`git-working-model` S3; reached by `handoff_resume.py` on a
ready-to-land resume). The **promotion** instance was built to `real-to-test` in the
four-step growth sequence (`code_first_architecture.md` §"Growth sequence inside the
port") and stopped there: `promote-worker.sh` wires none of the seams
`PromotionAdapter` requires, so since land-port-tested-configuration S2 the adapter
**refuses by name before anything is written**. The harness's wired staging→prod path is
`claude-promote` (§2). What survives behind that refusal is catalogued in
`hooks/land-port-revival-checklist.md`.

An earlier revision of this section said "Shipped: `git-working-model` S3 gate + S4
promotion", which read as a claim about both. It is corrected rather than deleted: the
S4 promotion work exists and its suite is green against injected seams — that is a real
result, and it is not the same thing as a path an operator can run.

- **One land port, two per-repo adapters (operator-LOCKED vocabulary — do not re-litigate).** The
  land primitive is the contract **`dry-run/diff → apply → promote`**, parameterized on two
  orthogonal axes: (i) per-repo **adapter** — Projects = git merge-to-`main`, harness =
  `config-source`-promote; (ii) per-direction **use** — verify-then-land (source = topic branch, target =
  `main`) vs promotion (source = staging, target = prod). Every combination reuses the one port;
  none is a monolithic script (Ports & Adapters — `code_first_architecture.md`). This
  one-port/two-adapters model and the phase vocabulary are **locked** (`…_DESIGN.md` Standing
  Disposition #2 — a split/rename requires `/clarification --from`). Code: `land_port.py` (the port
  + `VerifyThenLandAdapter` + `PromotionAdapter`).
- **Verify-then-land gate (mandatory, user-overridable).** Before a topic branch merges to `main`:
  merge `main` into the topic, build the merge candidate on a throwaway integration ref **inside a
  throwaway worktree run in a sanitized isolated process env** (clean env / controlled PATH —
  producer-never-verifies needs process-boundary isolation, not just a clean folder), run the repo's
  **one canonical check command** there, and **land only on green**. A red check or a conflict
  **stops the merge** (the active branch is left untouched; the user resolves natively in their own
  worktree and re-runs); the override stays the user's. The gate is a **version-controlled script**
  shared verbatim by the local path and CI — `verify-then-land.sh` — not a skippable client-side
  hook (§8). Bring-`main`-in defaults to **merge** (rebase = the linear alternative).
- **The land serializes with bookkeeping under the one lock.** The topic land mutates the shared
  `main` checkout, so it acquires the **same `flock`** as bookkeeping writes and updates `main` with
  a worktree-consistent `git merge` (never a raw `update-ref`) — see the **STABILIZED LOCK
  PRINCIPLE** in `bookkeeping-model.md` §4b (the two writers of `main` are bookkeeping commits and
  topic lands). A bookkeeping-only advance of `main` during the check does not stale the green; a domain
  advance does (re-test before landing).
- **Canonical check-command convention.** Each repo declares one check command (`just check` /
  `make check`), version-controlled and shared by the local gate **and** CI (local ≡ CI). Solo =
  local gate + always-via-PR so free-tier CI runs and the operator self-enforces green; team/Pro =
  branch protection + required checks + merge queue (Tier-3, documented future — §7/§8).
- **Automated promotion worker (staging→prod) — DESIGNED, NOT WIRED.** Everything in this
  bullet describes what the worker was built to do; **none of it is reachable today**.
  Invoking `promote-worker.sh` produces a named refusal (`[unwired-seams]`, exit 3) with
  nothing written to the shared source, because the wrapper supplies neither of the two
  seams `PromotionAdapter` requires. **Use `claude-promote` (§2).** Do not cite this bullet,
  or the promotion suite's green count, as evidence that a staging→prod worker ships —
  that suite fakes every production seam, which is the `real-to-test` step of the growth
  sequence, not the last one. Design intent, for the record and for
  `hooks/land-port-revival-checklist.md`: the promotion instance runs as an
  **out-of-process-tree worker** in a controlled env (right dir / PATH / tools), 3-phase, reporting
  exactly what landed, **inside the `check-verifier-isolation.sh` pre/post snapshot pair**. It
  captures **only this session's files** (`_session_scope-<SID>.md`, or a list-and-confirm gate;
  non-TTY with neither → fail-closed) so a concurrent session's live-config edits are never swept
  in — a whole-tree `git add -A` is prohibited, and the commit must be scoped too (the
  harness-wide rule is §4; this bullet predates it). Concurrent
  promotions serialize under a crash-safe promote-lock (a source lock for the shared commit + a
  separate deployment mutex around the live apply; neither spans the network or the human go/no-go;
  pre-approval `re-add`+diff run in an ephemeral workspace, the shared source is committed only after
  "go", and the deploy is path-scoped to the session's files). Code: `promote-worker.sh` →
  `land_port.py` (`PromotionAdapter`); the harness endpoint is `claude-promote` (§2).
- **Rollback (per adapter; never `reset --hard` on shared history).** Projects = `git revert -m 1
  <merge-SHA>`. Harness = a **synchronized source-and-target** rollback: `git revert` the bad
  promotion commit in the config-source **source** tree, then the deploy step the reverted source to live
  `~/.claude` (a live-only restore is a time bomb — the next the deploy step would re-deploy the bad
  source `HEAD`); the last `green-<ts>` tag is the known-good baseline. Both wrapped
  atomic-or-nothing (`git revert --abort` + surface on conflict, both surfaces left untouched).
  Aligns `safe-defaults.md` + §5.
  **The harness half is policy with no reachable implementation.** `PromotionAdapter.rollback`
  implements exactly the sequence above, and the `rollback` CLI verb builds
  `VerifyThenLandAdapter` instead (`land_port.py:1405`) — so a CLI rollback after a
  promotion reverts in the wrong adapter against the wrong target. Do the harness rollback
  by hand (revert in the config source, then the deploy step), and treat this bullet as the
  procedure rather than as a command that exists. Recorded in
  `hooks/land-port-revival-checklist.md` §5.

---

## Provenance

- `Thoughts/claude-infra-overhaul_THOUGHT.md` — config-source promotion model
  (`claude-promote` S2, `claude-experiment` S4, both shipped/complete; `green-*`
  tags; frozen `~/.claude/.git`; source at `<config-source-repo>/`) and the S5
  worktree + push-safety primitive (§3).
- `Thoughts/claude-infra-overhaul_Q16_RESEARCH.md` — version-control-as-Tier-1 +
  enforcement-not-client-hooks (§8). FC verdict ESCALATE; the git claims are
  source-cited (git-scm, GitHub docs).
- `Diary/2026-06-23.md` Session 1 (recovery/squash rules) + Session 2 (reconciliation,
  branch protection, operational gotchas — where the branch policy was formulated).
- `Thoughts/init-claude-git-repo_THOUGHT.md` — harness-as-git-repo origin (adjacent).

**Enforcement layer:** this file carries the `[Rules]` label (auto-loaded every
session; AI reads and applies judgment — Layer 2 in the `code_first_architecture.md`
trust hierarchy).

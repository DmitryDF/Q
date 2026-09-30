---
name: work-start
description: Take a TODO or _thought into in-progress — acquires the per-topic lock, starts the appropriate phase, and synchronizes status across TODO.md / topic-state JSON / spine slice-register.
allowed-tools: Bash, Read, Edit
---

# /work-start — Take Work In Progress

Take a single piece of work into progress for the current session. The skill is the user-facing entry point over Slice L's lifecycle-sync primitives (`${KIT_HOOKS_DIR}/taskmanagement.py`).

After `/work-start` returns, three surfaces are coherent at the same instant:

1. `TODO.md` — the named line carries `(in <phase>: <sid8>)`
2. `~/.claude/state/pre_plan_gates/<topic>__<project>.json` — phase advanced
3. `~/.claude/state/locks/<topic-slug>.lock` — JSON `{session_id, pid, pid_start, pid_source, identity, started_at, last_heartbeat}` — `pid` + `pid_start` identify the SESSION's own `claude` process (streamed-dancing-goose S4), not the helper that ran the acquire

## Trigger

```
/work-start <todo-title-fragment>
/work-start <path-to-_thought.md>
/work-start --worktree <target>          # harness worktree placement (S1)
```

The fragment matches a single open TODO line in the active project. The
path form resolves a `_thought` spine directly.

`--worktree` (git-working-model S1) opts this start into **auto-placement**
into the topic's own isolated worktree (Step 4b). It is the harness worktree
opt-in: the operator never names, finds, or `git worktree add`s anything —
the shared helper does it all. Without `--worktree`, behaviour is exactly as
today (no placement). Automatic per-topic placement across every work-
initiation surface, and the Projects-side repo-binding, arrive in S7/S9.

## Intake Modes

`/work-start` has **three intake modes**, auto-detected from the
`intake_source_class` field returned by `pre_plan_gates.py topic-orient`:

| Mode | When it fires | Lifecycle |
|------|---------------|-----------|
| **thought-bound** | `intake_source_class == "thought-bound"` — TODO line carries `Master plan: [[…]]` wikilink and the linked `_thought` spine exists with locked Discovery, or arg is `<path>.md` | Existing `create-topic` + `phase-start` flow; topic-state `intake_source: "thought"`. |
| **plain** | `intake_source_class == "plain"` — TODO line has no `Master plan:` wikilink, or the linked `_thought` is missing / half-built | `create-topic ... --intake-source plain --todo-line-ref <path>:<line>` + `phase-start ... thought`; TODO line gets a leading `🚧 ` prefix in addition to the standard `(in thought: <sid8>)` suffix. **No `_thought` file is created — `_thought` files in `Thoughts/` are the exclusive output of `/clarification`.** |
| **worktree-origin** | `intake_source_class == "worktree-origin"` — no `_thought` file in tree AND the plan is at `~/.claude/plans/<slug>.md` (the harness-slug fallback emitted by `pre_plan_gates._in_worktree()` plan-mode routing) | `create-topic ... --intake-source worktree-origin` + `phase-backfill --to implementation`. The plan already exists; the topic-state is reconciled to the implementation phase so subsequent `/work-start` calls hot-path through topic-orient. |

Plain-mode topic slug: `plain-<session_id[:8]>` (e.g. `plain-b47ffa87`).
Lock key follows the standard `topic_slug__project_slug` namespace.

## When to Use

- Starting a fresh session on a TODO that already carries a `_thought`
  spine (typical: a [Thought]-tagged multi-session item) — runs
  thought-bound.
- Starting work on a sufficiently-framed plain TODO line that has no
  spine — runs plain mode. No `/clarification` required.
- Resuming a topic mid-flight after `/close` + `/clear` (idempotent re-entry).
- After `/clarification` + `/solution-design` + `/plan` have all run and
  the topic enters implementation — `/work-start` advances the phase.

Do not use `/work-start` for:
- Trivial upstream-surfaced one-shot edits — use `/ninja-fix` instead.

## Invariants

- **Idempotent same-session:** Re-invoking `/work-start <same-target>`
  within the same session is a no-op write (lock heartbeat refreshed; no
  duplicate annotation).
- **Single holder:** Two sessions cannot both hold the same topic lock.
  The second sees `HELD_BY_OTHER` with the holder's `session_id`.
- **Producer-never-verifies:** Every surface write is followed by
  `taskmanagement.verify_write` (re-read + structural diff). The skill
  never validates its own writes; the verifier inside the CLI does.
- **No LLM in the lifecycle path:** The skill orchestrates code calls only.
  LLM adjudication is reserved for the rule (b) flag-and-ask case, which
  surfaces as a structured payload — the skill prompts the user (not the
  LLM) for the choice.

## Execution

**Step 1 — Read SESSION_ID** from the most recent `SESSION_ID=...` line in
the conversation.

**Step 2 — Resolve target and orient via topic-orient.**

Resolve the user argument to a TODO line or a `.md` spine path:

- If the argument ends in `.md`, treat it as a `_thought` path. **Do not
  derive `topic_slug` yourself** — ask the code for it:

  ```bash
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py canonical-topic "<path>.md"
  ```

  It prints the canonical slug on stdout; use that value verbatim. A non-zero
  exit means the artifact yields no usable work name — report the printed
  reason and stop; do not substitute a slug of your own.

  This used to be a prose rule here (`<slug>_THOUGHT.md` → `<slug>`), which is
  the site that filed a second tracking record for work that already had one.
  A slug read out of this skill is passed to `create-topic` as an **explicit**
  `TOPIC_SLUG`, and an explicit slug outranks the artifact derivation by design
  — so whenever the prose and the code disagreed, the prose won silently.
  Prose cannot be kept in lock-step with code; the skill calls the code instead
  of restating the grammar.
- Otherwise treat the argument as a TODO-title fragment. Search the
  active project's `TODO.md` for a unique open item that contains the
  fragment. Refuse if zero matches; if >1 matches, list candidates and
  ask for a more specific fragment. Record the matched TODO line's
  `<path>:<line>` — this becomes `todo_line_ref` for plain mode.

Then call `pre_plan_gates.py topic-orient SESSION_ID` and read its
`intake_source_class` field. The CLI is the single source of truth for
spine + plan + state shape — the skill never reads those files directly.

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py topic-orient SESSION_ID
```

The verdict + intake_source_class drive Step 4 routing (table above).
`topic-orient` is read-only; calling it twice in one invocation is fine.
Plain-mode topic slug: `topic_slug = "plain-" + SESSION_ID[:8]` — stable
across TODO line edits and unique per session. It is **not** derived from any
artifact and is deliberately unaffected by `canonical-topic`: a plain topic has
no spine to derive from, and its key is legitimately unrelated to whatever
artifact the session may later touch. In every mode the lock key is
`topic_slug__project_slug` (matches the topic-state JSON filename).

**Step 2.1 — M13 auto-bind (project-tracking-staleness S10).** When
`topic-orient` reports the session is **unbound** (`verdict: new` /
`intake_source_class: none`) but the argument resolved to a spine path (the
`.md` path form, or a `Master plan: [[…]]` wikilink target), bind the session to
its named topic automatically instead of the manual `create-topic` rebind
handoff-entered sessions used to require. Forward the resolved spine path to the
code-layer auto-bind (A1b), which derives the topic slug from the `_THOUGHT`
filename and binds **only** when that slug matches an existing on-disk
topic-state — refuse-rather-than-guess (E26), never mint a fresh binding:

```bash
printf '%s\n' "SPINE_PATH" | \
    python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py auto-bind SESSION_ID --prompt-body -
```

- `status: bound` → re-run `topic-orient SESSION_ID`; it now returns
  `mid-flight` / `thought-bound` and Step 4 proceeds normally (no manual
  `create-topic` rebind).
- `status: no_match` (slug resolved but no on-disk topic-state), `unresolvable`
  (no spine path — e.g. plain TODO intake), or `already_bound` → no bind; fall
  through to the normal Step-4 routing (plain / worktree-origin / create fresh).
  A genuinely new topic still flows through Step 4 unchanged.

**Step 3 — Acquire the lock.**

```bash
python3 ${KIT_HOOKS_DIR}/taskmanagement.py status  # informational
```

Then in Python (the skill keeps lock acquire in-process for atomicity —
do not shell out to `taskmanagement.py` for `acquire_lock`):

```bash
python3 -c "
import sys; sys.path.insert(0, '~/.claude/hooks')
import taskmanagement as tm
import json
res = tm.acquire_lock('TOPIC_SLUG__PROJECT_SLUG', 'SESSION_ID')
print(json.dumps(res, indent=2, default=str))
"
```

- `ACQUIRED` → continue.
- `ALREADY_HELD_BY_SELF` → idempotent re-entry; continue.
- `HELD_BY_OTHER` → report the holder's `session_id` and the `liveness`
  field the response carries (`ALIVE` / `UNKNOWN` / `LEGACY` — a `DEAD`
  holder is reclaimed automatically and never reaches this branch), then
  stop. Offer
  `python3 ${KIT_HOOKS_DIR}/taskmanagement.py release TOPIC_SLUG__PROJECT_SLUG --force --yes`
  as recovery — the FULL composite key (the `.lock` filename stem, exactly
  as `status` lists it), never a bare topic slug: locks are keyed
  `<topic>__<project>`, and a bare slug matches nothing. Since S4 a miss is
  loud: the command prints `NOT_HELD` with the candidate keys and exits 1
  instead of reporting success while the operator stays blocked. `--yes`
  suppresses the TTY confirmation prompt the non-holder release otherwise
  raises; the payload is moved aside to a timestamped `.lock.bak-*` sibling,
  never deleted, and the release is logged in `_releases.jsonl`.
- `RACE` → another acquire is in flight; retry once after 100ms; if still
  RACE, stop and report.

**Step 4 — Create topic (when needed) and route the phase action.**

Branch on the topic-orient verdict from Step 2. The skill never reads
spine/plan/state file shapes directly — `phase_observed`,
`phase_inferred_from_spine`, `backfill_needed`, and
`intake_source_class` are the only signals it consults.

**Plain mode (`intake_source_class == "plain"`)** — no prior topic-state
exists for the synthetic `plain-<sid8>` slug. Create it, then start the
`thought` phase. The `phase-start` CLI handles initial-phase entry
directly (no phase-token round-trip when starting from no-phase →
`thought`).

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py create-topic SESSION_ID \
    PROJECT_SLUG plain-SID8 \
    --intake-source plain \
    --todo-line-ref TODO_PATH:LINE_NUMBER
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-start SESSION_ID thought
```

**Thought-bound mode (`intake_source_class == "thought-bound"`)** —
read `phase_observed` from the topic-orient output. Two sub-branches:

- `verdict == "mid-flight"` and `backfill_needed == true` (the
  tracker lags the spine — locked Discovery + Solution Alternative +
  approved plan are already on disk, but topic-state.phase is null or
  behind): reconcile by calling `phase-backfill` with
  `phase_inferred_from_spine`. No phase-auth token needed; the backfill
  is a synthetic reconciliation, not a user-adjudicated transition.

  ```bash
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-backfill SESSION_ID \
      --to PHASE_INFERRED \
      --evidence '{"source":"topic-orient","verdict":"mid-flight"}' \
      --source-summary "spine + plan ahead of tracker; reconciled by /work-start"
  ```

- Otherwise (mid-flight + no backfill needed, or a fresh thought-bound
  topic): request the phase-auth token for the successor in
  `PHASE_SEQUENCE` and advance.

  ```bash
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-token SESSION_ID --from CUR --to NEW
  python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-start SESSION_ID NEW --auth TOKEN
  ```

**Worktree-origin mode (`intake_source_class == "worktree-origin"`)** —
no in-tree spine with locked Discovery; a plan already exists (at
`~/.claude/plans/<slug>.md`, or a relocated `_PLAN.md`). Create the topic with
the `worktree-origin` intake source, then backfill the phase directly to
`implementation` since the plan already shipped.

Two intakes reach this class: a genuine worktree-origin topic (no `[Thought]`
TODO line at all), and a topic **auto-registered at ship** (`intake_source:
"auto-registered"` whose anchoring artifact is a `_PLAN` — see
`pre_plan_gates._classify_intake_source`). The latter DOES already carry a
slug-tagged `[Thought]` line, minted by `auto-register` and marked
`[auto-registered]` because its framing is still outstanding. Do not mint a
second line for it; `create_topic`'s idempotency guard rebinds rather than
overwrites its state.

`TOPIC_SLUG` below is the value `canonical-topic` printed in Step 2 — used
verbatim, never re-derived here. That is what makes this arm's key identical to
the one `auto-register` bound at ship for the same artifact; re-deriving it in
prose is exactly how the two came apart.

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py create-topic SESSION_ID \
    PROJECT_SLUG TOPIC_SLUG \
    --intake-source worktree-origin
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-backfill SESSION_ID \
    --to implementation \
    --evidence '{"source":"topic-orient","intake_source_class":"worktree-origin"}'
```

**Worktree-origin arm + fail-loud (A3 — off-path enforcement).** A worktree-origin
plan reached implementation WITHOUT passing `/plan` Step 11, so the `/execute-plan`
entry-gate pointer was never armed. Since the operator's deliberate `/work-start`
IS the consent signal (bookkeeping-model §6), arm it HERE — but only for a genuine
multi-step plan, and warn loudly (never silently) on a malformed one. Run
`register-presence` on the topic's plan file (`<PLAN_FILE>` = the plan resolved in
Step 2 — the relocated `Thoughts/<slug>-<ts>_PLAN.md`, or the `~/.claude/plans/<slug>.md`
harness path if not yet relocated):

```bash
printf '%s' '{"spine_path":"<PLAN_FILE>"}' \
  | python3 ~/.claude/skills/execute-plan/run.py register-presence
```

- **`present: true`** (≥2 valid non-closing slices) → **arm the pointer** (gated on
  `present:true`, exactly like `/plan` Step 11 — a single-work plan is NEVER armed,
  delivering C5's "no gating" clause):
  ```bash
  printf '%s' '{"owner_session_id":"SESSION_ID","surface_path":"<PLAN_FILE>","total_slices":<count>,"execution_pending":true,"walking_session_id":null,"current_slice_id":null}' \
    | python3 ~/.claude/skills/execute-plan/run.py set-active-run
  ```
  Add `"worktree_root":"<worktree root>"` when the plan lives in a git worktree.
- **`present: false` AND (`raw_non_closing_count` ≥ 2 OR `pointer_broken: true`)** →
  **fail-loud at the intake moment** (do not wait for the next SessionStart scan):
  surface a prominent warning naming `<PLAN_FILE>`, e.g. "⚠️ This plan looks multi-step
  but its slice register did not resolve to ≥2 valid slices — it was NOT armed for
  step-by-step execution. Fix the register (missing `Type`/`Depends on` column, or a
  broken `slice_register_ref`) or drive it via `/execute-plan`. It is not being silently
  treated as a one-shot." Then arm nothing and continue (warn, not block).
- **`present: false` AND `raw_non_closing_count` < 2** → single-work / trivial → arm
  nothing, no warning (C6).

In every mode, the Slice L A7 wrap fires post-write: TODO line gets
`(in NEW: SID8)` (or the closest equivalent — backfill is a synthetic
reconciliation, not a user-adjudicated transition), topic-state JSON is
verified, and (when applicable) the spine slice-register row is
updated.

**Step 4a — Plain-mode visual marker (🚧).**

Only in plain mode: prepend `🚧 ` to the matched TODO line (idempotent —
skip if the line already starts with `🚧 `). The marker is a leading
prefix; the standard `(in thought: <sid8>)` annotation written by the
A7 wrap remains as the suffix. Use the Edit tool against the TODO file
with `old_string = "- [ ] <original line text>"` and
`new_string = "- [ ] 🚧 <original line text>"`. Skip entirely in
thought-bound mode.

**Step 4b — Auto-place into the topic worktree (harness-side; S1).**

*Scope (git-working-model S1 / A4).* This step runs **only** when `/work-start`
was invoked with `--worktree` (the harness worktree opt-in), or idempotently on
resume when the topic already has a worktree. The repo-binding is the harness
**config source** repo. A project/Root topic started **without** `--worktree`
is left exactly as today — no placement. (Automatic per-context binding, the
rest of the A14 consumer surfaces — `/plan --worktree`, `/prompt-for-handoff`,
`/close` — and the Projects-side binding are wired in S7/S9, not here.)

The step is a code call to the shared helper's `place` subcommand (A2/A4). It is
a **post-routing** step: the plain / thought-bound / worktree-origin intake
branching above stays intact; placement is added after it. `place` runs the
deterministic dirty-tree pre-flight + create-or-lookup in code (idempotent — a
resume returns the existing worktree, never a duplicate; a dirty tree HALTS with
the interim stash bridge, exit 3, rather than stranding the work). The skill's
only job is to wrap that call with the per-topic lock discipline: the `trap`
below **releases the lock on ANY non-zero exit** so no Step-4 failure sub-path
deadlocks the topic (a *hung* call — which a trap cannot catch — is bounded by
the session's own lifetime since S4: the lock follows the session process, so it
is released at `SessionEnd`, and a session that dies is reclaimed by the next
acquirer's liveness probe). Run it verbatim:

```bash
TOPIC_SLUG="<topic-slug from Step 2>"
LOCK_KEY="<topic_slug>__<project_slug>"      # same key acquired in Step 3
SID="<SESSION_ID>"
REPO="$(the configured source path 2>/dev/null)" \
  || { echo "[work-start] cannot resolve harness repo-binding" >&2; exit 1; }

release_lock() {
  python3 - "$LOCK_KEY" "$SID" <<'PY'
import sys; sys.path.insert(0, '~/.claude/hooks')
import taskmanagement as tm
tm.release_lock(sys.argv[1], sys.argv[2])   # holder-release (not force)
PY
}
# Release the lock on ANY non-zero exit from this placement block.
trap 'rc=$?; if [ "$rc" -ne 0 ]; then echo "[work-start] Step 4b failed (rc=$rc) — releasing lock $LOCK_KEY" >&2; release_lock; fi' EXIT

# Dirty-tree pre-flight + create-or-lookup + hook install (all in `place`).
# Exit 3 = dirty tree (helper printed the stash bridge — carry work across and
# re-run); any other non-zero = placement error (helper printed why).
DEST="$(${KIT_HOOKS_DIR}/worktree-helper.sh place --repo "$REPO" --topic "$TOPIC_SLUG")" \
  || { echo "[work-start] worktree placement did not complete — see helper output above" >&2; exit 1; }

trap - EXIT                     # success — keep the lock; clear the release trap
cd "$DEST" || { echo "[work-start] cannot cd into worktree $DEST" >&2; release_lock; exit 1; }
echo "[work-start] placed session in worktree: $DEST"
```

Report the worktree path in the Step 6 summary (`Worktree: <path>` — omit the
line entirely when `--worktree` was not given).

**Step 5 — Handle conflict-resolution outcomes.**

The `phase-start` command returns a `sync_receipts` block. Three outcomes:

- `conflict_rule: "none"` or `"rule_a_silent"` — clean write. Report the
  receipts and stop.
- `conflict_rule: "rule_b_flag"` — `flag_and_ask` payload is present.
  Surface the two options to the user verbatim (`keep-and-overlay` vs
  `replace-with-phase`) and wait for the choice. Apply via a follow-up
  `todo.py mark-in-phase` (overlay) or `todo.py` line edit (replace).
- The phase-start subprocess raised `SyncConflictError` — rule (c)
  hard-fail. Report the diagnostic; do NOT retry. This indicates a
  state inconsistency (two different sessions racing on the same line);
  resolve manually.

**Step 6 — Emit the summary.**

**Surface recent phase events (M6 read-side).** Before rendering the summary,
call the shared port for the recent machine-truth phase events (state-dir only —
this reads `phase_history`, never the spine, so it works identically inside a
topic worktree):

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py recent-phase-events "$SESSION_ID"
```

Pass the **actual resolved session id from Step 1** (quoted), NOT the literal
token `SESSION_ID`. Render each `events[].summary` string as a bullet on the
`Phase events:` line below. This call is **advisory** — on any error, a non-`ok`
`status` (e.g. `no_active_project`), or an empty `events` list, print `(none)`
and continue. It must NEVER block or fail `/work-start`. The purpose is read-side
reconciliation: the operator eyeballs these machine events against the spine
prose they have open and decides what (if anything) to refresh (the write side is
owned by later slices — this step only surfaces, it never writes).

Report to the user, in this exact shape:

```
/work-start — IN PROGRESS

Topic:        <topic-slug>
Intake:       <thought | plain>
Phase:        <prev> → <new>
TODO:         <todo-path>:<line> — [🚧 (plain only)] (in <new>: <sid8>)
Lock:         ~/.claude/state/locks/<topic-slug>.lock — holder <sid8>
Spine row:    <path>#slice-register — id=<slice-id> status=NOW updated=<date>
              (or "skipped — spine has no L:slice rows yet")
              (plain mode: "(none — intake_source: plain)")
Phase events: <one bullet per recent-phase-events events[].summary>
              (or "(none)" when empty / no active topic / call errored)

Next:         <next-recommended-command — e.g., /clarification, /plan, …>
              (plain mode fallback from broken wikilink:
               "Spine missing/incomplete at <path> — started in plain mode")
```

## Output Contract

On success, exit 0 and emit the summary above. On any failure, exit
non-zero with a one-line diagnostic citing the failing surface
(`lock`, `phase-start`, `verify_write`, `sync conflict`).

## Edge Cases

- **No spine.** TODO line has no `Master plan: [[…]]` wikilink → route
  to plain mode (no refusal, no warning). The TODO line is sufficient
  intake; `_thought` files remain the exclusive output of `/clarification`.
- **Broken spine — target missing/unresolvable → REFUSED.** A
  `[Thought]`-tagged TODO whose `Master plan: [[…]]` wikilink names a file
  that does not exist anywhere under the project root is **blocked, not
  routed to plain mode**. `${KIT_HOOKS_DIR}/check_work_start_wikilink.py`
  refuses the call (exit 2, `BLOCKED: /work-start would mint orphan
  plain-* state`) rather than let it mint a `plain-*` record that can never
  trace back to a spine — the drift the reaper then has to clean up.
  Fix the wikilink (or drop it, which makes the line a plain TODO and is
  accepted) and re-run.
- **Half-built spine — target exists but has no locked Discovery →
  plain mode.** The hook's `_resolves()` tests **file existence only**, so
  it does NOT refuse this case: emit a one-line warning ("Spine incomplete
  at `<path>` — starting in plain mode") and route to plain mode.
- **Stale lock — liveness, not age (S4).** `acquire_lock` probes the
  holder's recorded session process (`pid` + `pid_start`, reuse-proof):
  a holder that is **alive keeps its lock at any age** — however many hours
  it has been working — and `HELD_BY_OTHER` carries `liveness: ALIVE`; a
  holder whose process is **gone** (or whose pid was recycled) is reclaimed
  at once (`dead-holder-release`); an **unconfirmable** holder is reclaimed
  once its heartbeat is older than the 24h ceiling
  (`TM_LIVENESS_CEILING_SECONDS`, `unconfirmable-ceiling-release`); a
  **legacy** pre-S4 payload (no identity) keeps the old rule — reclaimed
  once `last_heartbeat` is older than `STALE_T` (default 3600s; env
  `TM_STALE_T_SECONDS`, `stale-auto-release`). Every reclaim writes an
  audit row to `~/.claude/state/locks/_releases.jsonl` and moves the old
  payload aside as a `.lock.bak-*` sibling. The skill mentions the reclaim
  in the summary. The heartbeat is refreshed on every prompt by the
  `UserPromptSubmit` hook `refresh-topic-locks.sh` (silent; it does not fire
  during an autonomous run — the process probe is what keeps a working
  session's lock), this session's locks are released at `SessionEnd` by
  `release-session-locks.sh`, and locks whose holder is provably dead are
  swept at `SessionStart` by `sweep-dead-locks.sh` (never a legacy payload).
- **TODO already 🚧-marked by another session.** `acquire_lock` returns
  `HELD_BY_OTHER` — report the holder's `session_id` and stop (the 🚧
  prefix is informational; the lock is the authority).
- **Idempotent re-entry (plain mode).** Re-invoking `/work-start` on a
  `🚧`-prefixed TODO line from the same session is a no-op write — the
  lock heartbeat refreshes and the prefix is not double-applied.
- **Multiple TODO matches.** List the candidate lines and ask the user
  to pick a more specific fragment.

## Operator notes — the `_active.json` ledger

- **Legacy non-`plain-*` entries intentionally remain.** The `_active.json`
  pruner is scoped to dead `plain-*` session entries. Pre-fix-era entries
  under other keys are a **deliberate, documented residual** — not a script
  failure and not something to hand-clean. The canonical resolver tolerates a
  stale `_active.json` entry at read time, so the harm is nil; broadening the
  prune to that class is an optional future extension that was deliberately
  left out of scope.
- **Roll `_active.json` back only with `restore-active`, never a shell copy.**
  `_active.json` is a single shared ledger under concurrent write, so copying
  a backup over it would clobber every binding minted since that backup was
  taken — silently destroying data in the procedure meant to protect it. The
  sanctioned rollback takes the same `bookkeeping_lock` as the write it
  undoes and performs a **logical merge** (re-add the pruned rows, keep every
  row that exists now; on a collision the current value wins), because the
  inverse of a deletion-only operation is addition-only, not a snapshot.
  *(Both verbs SHIPPED together, 2026-08-21 — the prune and its undo landed in
  the same slice, because a rollback path that does not exist when the forward
  path lands is not a rollback path.)* Usage:

  ```
  python3 ${KIT_HOOKS_DIR}/reap_orphan_plain_topics.py prune-active [--apply] \
      [--prune-window 30d|12h|SECONDS] [--state-dir DIR] [--locks-dir DIR]
  python3 ${KIT_HOOKS_DIR}/reap_orphan_plain_topics.py restore-active [BACKUP] \
      [--apply] [--allow-superseded] [--state-dir DIR] [--locks-dir DIR]
  ```

  For a rehearsal against a COPY of the state dir, pass **both** `--state-dir`
  and `--locks-dir`. `--state-dir` alone redirects the records but not the lock
  namespace, so the run would still acquire and release real topic locks in the
  live `~/.claude/state/locks` under the fixture's slugs.

  Both are DRY-RUN by default. `restore-active` with no `BACKUP` defaults to the
  most recent **completed** prune's backup (a prune whose outcome was never
  confirmed is reported but never resolved to by default); a backup older than
  the last completed prune is **refused**
  (restoring it would re-add rows that later prunes deliberately removed), and
  `--allow-superseded` is the explicit escape. A row present in both the current
  ledger and the backup keeps its **current** value.
- Per-record topic-state files are a different class: they are per-topic, so
  restoring one cannot lose a concurrent write. Recover those with a plain
  `mv` back from `~/.claude/state/pre_plan_gates/_reaped/`.

## See also

- `${KIT_HOOKS_DIR}/taskmanagement.py` — the underlying CLI + Python API.
- `~/.claude/skills/clarification/SKILL.md` — what runs *before* `/work-start`.
- `~/.claude/skills/close/SKILL.md` — session-close, complementary to
  `/work-start`. The work-done analogue (`/work-done`) ships at Slice J.
- `Thoughts/workflow-phases-redesign_L_PLAN.md` — Slice L plan for the
  underlying primitives + wrap.

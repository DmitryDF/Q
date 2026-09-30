---
name: work-done
description: Mark a ship event done — composes the four-surface atomic write (TODO line, spine slice-register, spine Sessions log, conditional retire-marker) through L's verify_write per surface, with crash-safe resume.
allowed-tools: Bash, Read, Edit, AskUserQuestion, Agent
---

# /work-done — Mark a Ship Event Done

Per-ship-event mirror of `/work-start`. Composes the four ship-event surfaces
atomically through L's `verify_write` per surface, then releases the lock,
calls `phase-stop`, and — as its LAST step — regenerates the persistent handoff
prompt (M7, spine-bound topics, fail-loud if the regen does not land).

After `/work-done` returns success, four surfaces are coherent at the same instant:

1. `TODO.md` — the matched line carries `[x]` + DONE date + diary wikilink + merged repo-tagged commit-ids + advanced M/N counter (when applicable)
2. Spine `## Slice Register` row — `status=SHIPPED updated=<date>` for the slice under this plan
3. Spine `## Sessions` section — one annotate-tagged bullet under `### YYYY-MM-DD session [sid:<sid_prefix>]`
4. Spine `**Status:**` line — `; Retired YYYY-MM-DD` when `all_slices_done` is True (else unchanged)

A per-ship-event row is appended to the durable `~/.claude/state/work_done/<sid>__<topic>.completed.jsonl` ledger. The intent journal at `~/.claude/state/work_done/<sid>__<topic>.intent.json` is deleted only after all four surfaces verify.

## Trigger

```
/work-done                              # resolve from this session's held L lock
/work-done --mode {auto,full,session-only}  # partial-fire selector (default: auto)
/work-done --retire-as-never <id-csv>   # pre-atomic NEVER on listed slices, then ship
/work-done --force [--todo-pattern P]   # escape hatch — no prior /work-start required
```

**Fire mode (`--mode`, S7 / M9).** A ship event fires in one of two modes; `--mode` picks it (default `auto`):

- **`full`** (today's behavior) — the slice is *done*: write all four surfaces (todo `[x]`, slice_register `SHIPPED`, sessions_log, conditional retire_marker).
- **`session-only`** — this session is a *non-terminal* one inside a multi-session slice: record the per-session paperwork (sessions_log bullet + `Sessions: M/N` counter advance + slice-register `sessions` M→M+1) but leave the slice **open** (status stays `NOW`; no todo `[x]`, no `SHIPPED`, no retire).
- **`auto`** (default) — code decides: `work_done.py resolve-fire-mode` reads the slice's `sessions=M/N` and returns `full` (terminal, `M+1 >= N`, or single-session/absent row) or `session-only` (`M+1 < N`). The operator does not name the mode in the common case.

The completion ledger records the resolved `mode` on every ship (audit). Lock-release, `phase-stop`, and the M7 handoff regen run in **both** modes — the session is over either way.

## When to Use

- A slice you took into progress with `/work-start` has shipped (tests pass, commits made).
- A no-`/work-start` retroactive close-out — pass `--force --todo-pattern <fragment>`.
- A thinking-only retire: `/work-done --retire-as-never <ids>` flips the listed slices to NEVER, then carries the spine to `Retired <today>` if `all_slices_done` is satisfied.
  **One case flipping to NEVER cannot clear:** when the topic declares a slice list the reader can see but cannot parse, `all_slices_done` answers no, and it answers no whatever the slices are flipped to — the answer responds to the declaration becoming readable, not to slice bookkeeping. Expect that before reaching for this flag on such a topic.

Do not use `/work-done` for:
- Session-end paperwork (diary, Stats, log archive) — that is `/close`.
- A topic that hasn't yet shipped anything in this session — refuse loudly via the G6 ship-event predicate unless `--force` is passed.

## Invariants

- **Stateless.** The skill never writes state directly. Every surface write goes through a CLI (`todo.py`, `taskmanagement.py`, `pre_plan_gates.py`) or the journal helper (`work_done_journal.py`).
- **Producer-never-verifies.** Each surface write is followed by `taskmanagement.verify_write` (re-read + structural diff). The skill itself never validates its own writes.
- **Atomic via journal.** The four-surface payload is written to `~/.claude/state/work_done/<sid>__<topic>.intent.json` BEFORE any surface is touched. On crash, re-running `/work-done` re-reads the journal, runs `verify_write` per surface to confirm any `applied: true` claims, and re-applies any surface still showing `applied: false`. Re-runs converge to the same final state.
- **No L API extension.** J consumes L (`write_slice_row`, `verify_write`, `read_lock`, `attribute_commits`, `all_slices_done`); never extends it.
- **No LLM decision authority on any surface write.** The M7 handoff regeneration (step 11) invokes the canonical `/prompt-for-handoff --regen` skill, which composes with an LLM but routes every write through the verified write gate (`write-next-session-prompt`, producer-never-verifies) — the composer has no authority to accept its own output. The four ship-surface writes (step 8) and the M7 pass/fail verdict (`handoff-fresh-since`, step 11) are code-owned; no LLM decides whether a surface write or the fail-loud verdict lands.
- **Fire-mode is code-decided (S7 / M9).** On `--mode auto` the effective mode comes from `work_done.py resolve-fire-mode` (deterministic, no AI — Domain layer). The skill only *routes* which surfaces fire per the fixed table (Step 8); it never decides terminal-vs-non-terminal itself. `--mode full` / `--mode session-only` are explicit operator overrides.

## Execution

**Step 1 — Read SESSION_ID** from the most recent `SESSION_ID=...` line.

**Step 2 — Enumerate session-held locks.**

```bash
python3 ${KIT_HOOKS_DIR}/work_done.py session-locks SESSION_ID
```

Returns a JSON list of `{topic_slug, fresh, last_heartbeat, lock_payload}`.

- **Exactly 1 fresh lock** → bind `TOPIC_SLUG` to that holder. Continue.
- **0 fresh locks AND no `--force`** → AskUserQuestion with two options: `(a) "Pass --force --todo-pattern <X>" (provide pattern)` / `(b) "Abort"`. On (a), re-enter step 2 with the new flags.
- **>1 fresh locks AND no `--topic-slug` override** → AskUserQuestion enumerating each held topic_slug as an option; on user pick, bind `TOPIC_SLUG`.

**Step 3 — Resolve topic state + spine + plan.**

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py read SESSION_ID
```

Topic state fields used:
- `topic_classification.thought_file_path` — the spine; if absent (plain mode or `--force`), spine writes are skipped.
- `topic_classification.todo_line_ref` — `<path>:<line>` for plain mode.
- `topic_classification.intake_source` — `"plain"` or `"thought"`/null.

For the spine, locate the plan wikilink under `# Implementation Details`. Expect exactly one wikilink matching the active slice. If 0 or >1, AskUserQuestion to surface candidates and accept the user's `--plan-path` choice (the skill keeps `PLAN_PATH` as a string from this point).

```bash
python3 ${KIT_HOOKS_DIR}/work_done.py resolve-slice-id PLAN_PATH
```

Returns the `slice_id:` value from the plan's `GATE0SR:SLICES` block.

**Multi-slice plans:** If the plan carries `slice_ids: S1, S2, ...` (plural), the command exits 2 with a "Multi-slice plan detected" message listing the available IDs. Read the available IDs from the error, identify the active slice from session context (the slice whose work this ship-event targets), then re-run as:

```bash
python3 ${KIT_HOOKS_DIR}/work_done.py resolve-slice-id PLAN_PATH --slice-id <active-id>
```

No `AskUserQuestion` needed — the active slice is determinable from the session's `work-start` lock and the TODO line being marked done.

**Step 4 — `--retire-as-never` (if passed).**

BEFORE writing the intent journal:

```bash
python3 ${KIT_HOOKS_DIR}/work_done.py retire-as-never SPINE_PATH "id1,id2,..."
```

Per id, calls `taskmanagement.write_slice_row(spine, id, updates={"status":"NEVER"})` followed by `verify_write`. Aborts on first verifier failure (NEVER conversions are pre-atomic metadata writes; they must succeed before the four-surface journal is written).

**Step 5 — Detect ship event.**

First resolve the harness repo for attribution (S8 / M3-secondary): landed harness commits live in the **config source** `main` checkout, NOT the frozen `~/.claude` (git-policy §2), so attribution must scan the source or it sees zero harness commits.

```bash
HARNESS_REPO="$(python3 ${KIT_HOOKS_DIR}/work_done.py resolve-harness-repo)"
[ -n "$HARNESS_REPO" ] || HARNESS_REPO="$HOME/.claude"   # fail-safe: no worse than before
python3 ${KIT_HOOKS_DIR}/work_done.py detect SESSION_ID TOPIC_SLUG PROJECT_SLUG \
    --plan PLAN_PATH --proj PROJECT_REPO --harness "$HARNESS_REPO" \
    --started LOCK_STARTED_AT --ended NOW_ISO
```

`LOCK_STARTED_AT` comes from the lock payload (`started_at`); `NOW_ISO` is current UTC. `resolve-harness-repo` returns the config-source main checkout (empty on failure → the fallback keeps today's behavior).

- `ship_event: true` → proceed.
- `ship_event: false` AND no `--force` → refuse with the returned `reason` + the AskUserQuestion `(a) --force / (b) Abort` options.
- `--force` → skip refusal regardless.

Capture `commits` from the detect output (already prefix-tagged `[proj] sha` / `[harness] sha`).

**Step 5b — Resolve the fire mode (S7 / M9).**

Bind `FIRE_MODE ∈ {full, session-only}` — this gates which surfaces Step 8 writes:

- If the operator passed `--mode full` or `--mode session-only`, use it verbatim (explicit override).
- If `--mode session-only` was requested but the topic has **no spine** (plain mode / `--force`, no `thought_file_path`), fall back to `full` and note it — session-only records spine surfaces, so it is meaningless without a spine.
- Otherwise (`--mode auto`, the default) resolve it with code — the skill does not decide terminal-vs-non-terminal:
  ```bash
  python3 ${KIT_HOOKS_DIR}/work_done.py resolve-fire-mode SPINE_PATH SLICE_ID [--plan PLAN_PATH]
  ```
  Read `.mode` from the JSON (`full` | `session-only`). On a plain/`--force` topic (no `SLICE_ID` / no spine) skip the call and use `full`. The resolver never raises — an absent/odd row resolves to `full` (single-session default; see `work_done.py resolve_fire_mode`).

`FIRE_MODE` is passed to Step 8 (surface gating) and Step 9 (ledger `mode`).

**Step 5c — Auto-register an unregistered topic (PRE-step; auto-registration Mode-C plan, A2).**

Before composing any payload, mint the tracking layer for a topic that shipped without ever going through `/clarification` — a direct implement, or a handoff-launched session. Without this the `[Thought]` line, the `Sessions:` counter and the topic-state binding never exist, so the retire, omission and freshness gates operate on absent data and silently pass.

```bash
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py auto-register SESSION_ID --spine SPINE_PATH
```

`SPINE_PATH` is the ship's own anchoring artifact — the `_THOUGHT` for a thought-bound topic, else the `_PLAN` for a Mode-C ship. It is the same path that feeds `canonical_project_for_spine`, so identity is derived, never guessed.

- The verb is **deterministic code** — no model call enters the ship path.
- It is **idempotent per surface**, keyed on the stable `[<slug>-<ts>]` tag: an already-registered (or operator-edited) topic is a no-op, and a topic left half-minted by an earlier crash is completed.
- It is **non-blocking by contract** — it always exits 0. A `status` of `skipped` or `partial` in the JSON body never aborts the ship.
- When the ship carries **no anchoring artifact**, it refuses to guess: the mint is skipped and a `warning:` is returned. Surface that warning to the operator verbatim and continue.

A minted line is deliberately marked `[auto-registered]` and does **not** satisfy the framing validator. That is the standing obligation the S-C reconcile pass surfaces until a human frames it — the system fabricates no framing.

**Step 6 — Compose four-surface payloads.**

Build payload dicts for each of the four surfaces (none written yet):

- `todo` — pattern (the fragment used to grep the TODO line; from `todo_line_ref` for plain mode, from the matched [Thought] wikilink-fragment otherwise, from `--todo-pattern` for `--force`); `text` (Done line: `- [x] **<title>** — **DONE YYYY-MM-DD.** Commits: <commit-list>.`); `todo_file` (absolute path).
- `slice_register` — `slice_id`, `updates={"status":"SHIPPED"}`.
- `sessions_log` — `bullet` (5–200 chars, single line, ends in period; e.g. `"Shipped slice <id> — <one-line outcome>."`).
- `retire_marker` — present only if `taskmanagement.all_slices_done(spine_path)` would be True AFTER the slice_register write (the orchestrator evaluates this AFTER step 7's slice-register apply but BEFORE writing the retire surface; payload here is `{"retire_date": "YYYY-MM-DD"}` as a placeholder).

**Step 7 — Write or resume the intent journal.**

```bash
python3 -c "
import sys; sys.path.insert(0, '~/.claude/hooks')
import work_done_journal as wdj, json
existing = wdj.read_intent('SESSION_ID', 'TOPIC_SLUG')
if existing is None:
    wdj.write_intent('SESSION_ID', 'TOPIC_SLUG', {
        'todo': TODO_PAYLOAD,
        'slice_register': SR_PAYLOAD,
        'sessions_log': SL_PAYLOAD,
        'retire_marker': RM_PAYLOAD_OR_NONE,
    })
    print('FRESH')
else:
    # Crash-recovery: validate that the on-disk payload matches the freshly
    # composed one (same pattern, same slice_id, same Done date). If they
    # diverge, refuse — a stale journal from a different ship-event must be
    # cleared via 'wdj.delete_intent' before continuing.
    print('RESUME')
"
```

**Step 8 — Apply each surface in sequence; verify; mark applied.**

**`FIRE_MODE` gates which surfaces fire (S7 / M9).** The surface set differs between `full` (close the slice) and `session-only` (record the session, keep the slice open):

| Surface | `full` | `session-only` |
|---------|--------|----------------|
| `todo` `[x]` done line | write | **skip** (task not done) |
| `todo` `advance-sessions` (`Sessions: M/N`) | write (if counter present) | write (if counter present) |
| `slice_register` | `status=SHIPPED` | `status=NOW`, `sessions` M→M+1 |
| `sessions_log` bullet | write | write |
| `retire_marker` | if `all_slices_done` | **skip** |

For each surface where `is_applied(SESSION_ID, TOPIC_SLUG, <surface>)` is False:

1. **`todo`** —
   - **`full`:** invoke `todo.py done --pattern <pattern> --file <todo_file> --text <line_text>`, then `verify_write(surface_kind="todo_line", path=todo_file, expected_payload="[x]", locator=<pattern>)`.
   - **`session-only`:** do NOT mark `[x]` (the task is not done) — skip the `done` call and its `[x]` verify.
   - **Both modes:** if the topic carries a `[Thought]` `Sessions: M/N` counter, invoke `todo.py advance-sessions --pattern <pattern> --file <todo_file> [--summary "<bullet>"]` (this is the per-session counter advance Mechanism 9 requires). Then `wdj.mark_applied(..., 'todo')`.

2. **`slice_register`** — only if `thought_file_path` is set:
   - **`full`:**
     ```bash
     python3 -c "
     import sys; sys.path.insert(0, '~/.claude/hooks')
     import taskmanagement as tm
     tm.write_slice_row('SPINE_PATH', 'SLICE_ID', updates={'status': 'SHIPPED'})
     tm.verify_write(surface_kind='spine_section', path='SPINE_PATH',
                     expected_payload='id=SLICE_ID status=SHIPPED',
                     locator=('id=SLICE_ID', '-->'))
     "
     ```
   - **`session-only`:** keep the slice OPEN (status stays `NOW`) and bump the register `sessions` M→M+1 (`M/N` from Step 5b's resolve-fire-mode output; write only the `sessions` update so `write_slice_row`'s merge preserves `status=NOW`):
     ```bash
     python3 -c "
     import sys; sys.path.insert(0, '~/.claude/hooks')
     import taskmanagement as tm
     tm.write_slice_row('SPINE_PATH', 'SLICE_ID', updates={'sessions': '<M+1>/<N>'})
     tm.verify_write(surface_kind='spine_section', path='SPINE_PATH',
                     expected_payload='id=SLICE_ID status=NOW',
                     locator=('id=SLICE_ID', '-->'))
     "
     ```
   Then `wdj.mark_applied(..., 'slice_register')`.

3. **`sessions_log`** — only if `thought_file_path` is set (BOTH modes — this is the per-session record):
   ```bash
   python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py annotate-session SESSION_ID "<bullet>"
   ```
   The CLI is idempotent on `(date, sid_prefix)` — re-runs replace the prior annotate-tagged bullet, so N ship events in one session converge to one bullet (the latest). Per-ship-event durable record lives in the completion ledger (step 9). Then `wdj.mark_applied(..., 'sessions_log')`.

4. **`retire_marker`** — **`full` mode only**, and only if `taskmanagement.all_slices_done(spine_path)` returns True. In `session-only` mode NEVER retire (the slice is deliberately left open):
   ```bash
   python3 ${KIT_HOOKS_DIR}/work_done.py retire-marker SPINE_PATH YYYY-MM-DD
   ```
   The helper is idempotent — re-running on an already-retired spine is a no-op. Then `wdj.mark_applied(..., 'retire_marker')`.

**Step 9 — Append the completion ledger; delete the intent journal.**

The ledger row is JSON. Build it **in Python** and deliver it to the CLI via the payload slot
as `-` (stdin) or `--file <tmp>` — **NEVER** as a single-quoted positional shell argument. A
value containing a literal apostrophe (e.g. a `todo_pattern` from a title like `don't ship`)
would otherwise break the shell single-quoting. Do NOT build the JSON via shell string
assignment, a heredoc (the command sandbox blocks heredocs-to-a-process), `echo`, or
`printf` of a shell variable — construct it in Python so the JSON text never becomes a shell
token. Write a small `.py` script (per the safe-Bash rules) like:

```python
import json, os, subprocess
row = {
    "slice_id": "<id-or-null>",          # None for a real null
    "todo_pattern": "<pattern-or-null>",
    "todo_file": "<path-or-null>",
    "commits": ["[proj] sha", "[harness] sha"],
    "mode": "<full|session-only>",
    "retired": False,
    "flags": {"force": False, "retire_as_never": []},
}
r = subprocess.run(
    ["python3", os.path.expanduser("${KIT_HOOKS_DIR}/work_done.py"),
     "append-ledger", "SESSION_ID", "TOPIC_SLUG", "-"],   # "-" reads JSON from stdin
    input=json.dumps(row), text=True, capture_output=True)
print(r.stdout, r.stderr)
```

(Equivalently: `json.dump(row, open(tmp, "w"))` then pass `--file <tmp>` and clean up.) The CLI
also still accepts the positional `'{json}'` form for back-compat, but do not use it — an
apostrophe in a value breaks it.

Then delete the intent journal:

```bash
python3 -c "
import sys; sys.path.insert(0, '~/.claude/hooks')
import work_done_journal as wdj
wdj.delete_intent('SESSION_ID', 'TOPIC_SLUG')
"
```

**Step 10 — Release the lock + phase-stop.**

```bash
python3 ${KIT_HOOKS_DIR}/taskmanagement.py release TOPIC_SLUG --session-id SESSION_ID
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py phase-stop SESSION_ID
```

(`phase-stop` takes no auth token per the L A2 surface; it appends a stop event to `phase_history` and triggers `taskmanagement.sync_phase_stop`.)

**Step 11 — M7: regenerate the persistent handoff prompt (spine-bound topics), fail-loud.**

This is the LAST step of `/work-done` (project-tracking-staleness S5 / AD-4 — "M7 handoff-regeneration runs synchronously as the LAST step … fail-loud if regen fails"). It runs AFTER the four ship surfaces + completion ledger (step 9) + lock-release + phase-stop (step 10): those are the durable ship record, so a fail-loud here surfaces a regen miss **without corrupting the recorded ship**.

**Skip entirely when the topic has no spine** (plain mode, or `--force` with no `thought_file_path` — same posture as steps 8.2–8.4): a no-spine ship has no `## Next Session Prompt` to persist. Go straight to step 12.

For a spine-bound topic (`thought_file_path` set → `SPINE_PATH`):

1. **Force-regenerate the handoff synchronously** through the canonical skill — never a hand-rolled write (orchestrator-pattern Part 5):
   ```
   /prompt-for-handoff SPINE_PATH --regen
   ```
   `--regen` bypasses the freshness gate's reuse (the gate is structurally blind to ship-event drift) and always composes → `/double-check` → `write-next-session-prompt`. Run it now and let it finish — do NOT defer it or ask permission; it is the ship event's own last step.

2. **Code-enforced fail-loud** — the pass/fail verdict is code-computed, not your self-report that pfh "ran". Assert the persisted `## Next Session Prompt` was actually (re)written at/after this ship's window start = `LOCK_STARTED_AT` (the lock `started_at` captured at step 5):
   ```
   python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py handoff-fresh-since SPINE_PATH LOCK_STARTED_AT
   ```
   - **exit 0** (`FRESH`, or `NO_DISCOVERY` on a spine that legitimately carries no freshness marker) → the handoff is current; proceed to step 12.
   - **exit non-zero** (`STALE` / `ABSENT`) → **`/work-done` FAILS LOUD**: exit non-zero with the plain-language message —
     `"Ship recorded, but the handoff prompt was NOT regenerated (handoff-fresh-since: <verdict>). Run /prompt-for-handoff SPINE_PATH --regen to refresh it before the next session."`
     Do NOT exit 0 over a stale handoff.

**Honest boundary:** this fail-loud fires only *when this step runs*; it does not, by itself, defend against the entire M7 step being skipped (the topic's own Mechanism 2). The coarse "didn't run `/work-done` at all" omission is caught by S4's shipped `check-work-done-omission.sh` Stop hook; a named Phase-2 follow-up extends that Stop hook to also assert handoff freshness independent of the AI having run this step.

**Step 12 — Emit the summary.**

```
/work-done — SHIPPED (mode: full)          # or "— RECORDED (mode: session-only)"

Topic:        <topic-slug>
Mode:         <full | session-only>   (auto-resolved, or --mode override)
Slice:        <slice_id> → SHIPPED    (session-only: "→ NOW (open); sessions M/N → M+1/N")
TODO:         <todo-path>:<line> — [x] DONE <date>   (session-only: "counter M/N → M+1/N; not [x]")
Spine row:    <spine-path>#slice-register — id=<slice_id> status=<SHIPPED|NOW>
Sessions log: bullet appended/replaced under ### <date> session [sid:<prefix>]
Retire:       <"Retired <date>" | "skipped — check not satisfied (slices may still be open, or the slice list could not be read)" | "skipped — session-only">
Commits:      [proj] N    [harness] M
Ledger:       <sid>__<topic>.completed.jsonl (row N, mode=<...>)
Phase:        <prev> → stop event recorded
Handoff:      ## Next Session Prompt regenerated (--regen), fresh <written: ISO>
              (or "skipped — no spine" for plain/--force topics)

Next:         <"/close" if session ending | "/work-start <next>" if more work>
```

## Output Contract

On success, exit 0 and emit the summary above. On any failure, exit non-zero with a one-line diagnostic citing the failing surface and the journal state (`journal-written | resume-pending | all-applied`). **M7 fail-loud (step 11):** if the spine-bound handoff regeneration did not land (`handoff-fresh-since` returns `STALE`/`ABSENT`), exit non-zero with the remediation message naming `/prompt-for-handoff SPINE_PATH --regen` — the four ship surfaces are already durable, so this reports the regen miss without corrupting the recorded ship.

## Edge Cases

- **No fresh lock + no `--force`.** Refuse; AskUserQuestion offers `--force --todo-pattern <X>` or abort.
- **Multiple fresh locks.** AskUserQuestion enumerates `topic_slug` candidates. After pick, re-enter step 3.
- **Plain-mode topic (no `thought_file_path`).** Steps 8.2, 8.3, 8.4 are skipped — only the TODO surface applies. Sessions log bullet still emitted via annotate-session if explicitly requested via `--bullet`; otherwise omitted. Completion-ledger row still appended.
- **`--force` with no commits.** TODO Done line written with `[x]` + DONE date only (no `Commits: ...` segment). Sessions log bullet annotated `"(force, no plan resolved)"` if `PLAN_PATH` is null.
- **Crash mid-step-8.** Re-running `/work-done` reads the existing intent journal at step 7, runs `verify_write` per surface to confirm any `applied: true` claims still hold against live state, and applies + verifies any surface still `applied: false`. Re-runs converge.
- **`all_slices_done` returns False after slice-register write.** `retire_marker` is skipped; `wdj.mark_applied(..., 'retire_marker')` is still called (no-op pattern — the surface payload was None).
- **`session-only` mode (S7 / M9).** The slice stays `NOW` (not `SHIPPED`), the todo line is NOT marked `[x]`, and retire_marker is never written — only the sessions_log bullet + the `Sessions: M/N` counter advance + the register `sessions` bump are recorded. This is the non-terminal-session path for a multi-session slice; the slice closes later via a `full`-mode `/work-done`. `--mode session-only` on a spine-less (plain/`--force`) topic degrades to `full` (Step 5b) — session-only needs a spine to record.
- **Stale journal from a divergent prior ship event.** Step 7 detects the mismatch and refuses; the user must `wdj.delete_intent` explicitly. (No silent overwrite — guards against losing in-flight state from another topic.)

## See also

- `${KIT_HOOKS_DIR}/work_done.py` — orchestration helpers + CLI subcommands (`session-locks`, `retire-as-never`, `detect`, `resolve-slice-id`, `resolve-fire-mode`, `retire-marker`, `append-ledger`).
- `${KIT_HOOKS_DIR}/work_done_journal.py` — write-ahead intent journal (LOAD-BEARING for OQ-J-16 crash-safety).
- `${KIT_HOOKS_DIR}/taskmanagement.py` — `verify_write`, `write_slice_row`, `read_lock`, `release_lock`, `all_slices_done`, `attribute_commits`.
- `~/.claude/skills/work-start/SKILL.md` — the ship-bracket open. `/work-done` closes it.
- `~/.claude/skills/close/SKILL.md` — session-end paperwork (diary, Stats, archive, commits). Post-Slice-J-2 `/close` no longer touches TODO Done state or the M/N counter; per-ship-event done-side is owned exclusively by `/work-done`, so no overlap guard is needed.
- `Thoughts/workflow-phases-redesign_J_PLAN.md` — Slice J plan for the per-ship-event composer.

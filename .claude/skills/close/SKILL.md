---
name: close
description: Close session - create diary entry, archive logs, git commit
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent
---

# Close Session

Execute the session close checklist.

> **Orchestrator note (orchestrator-pattern — S7 full retrofit).** `/close`
> conforms to `~/.claude/rules/orchestrator-pattern.md` + `~/.claude/rules/skill-authoring.md`:
> it is the **orchestrator**. Deterministic acts stay as **code** (session-scope,
> todo cleanup, metrics CLIs, archiving, git, claude-promote). **Production acts
> are delegated** to right-sized adapters via the `Agent` tool, each gated by
> `/double-check` with a one-tier-up fallback:
>
> | Act | Adapter | Model | Section |
> |-----|---------|-------|---------|
> | Diary drafting (judgment) | `close-diary-drafter` | Sonnet (→ Opus on gate fail) | §1 |
> | Commit-message composition (mechanical) | `close-commit-composer` | Haiku | §3 |
>
> **Catalog adjustment (S7, per the surfaced Q&A).** The inferred `close-memory-extractor`
> is **not** a `/close` production act — the current `/close` has no memory-extraction
> step (memory is a separate `/profile` / memory-system concern). The confirmed `/close`
> catalog is the two adapters above. The marker (§0) + metrics (§1c) seeds from S3/S5
> remain. Every adapter is checked against `skill-authoring.md`.

## Pre-Check

**After context compaction:** Re-read the project's CLAUDE.md inheritance chain before executing close.

**Lightweight vs. full close:** If session had < 5 prompts and single topic → lightweight close:
- Diary: one sentence ("What was done. No decisions.")
- Archive logs + git commit (always)
- Skip TODO cleanup details if not applicable

Full close for everything else.

## Checklist

### 0. Gather Sources (Session-Scoped)

1. **Read SESSION_ID** from the most recent `SESSION_ID=...` line in the conversation
   (echoed by the UserPromptSubmit hook on every prompt). Then write the `/close`
   skill-invocation marker — the guardrail-handshake seed that records the
   canonical skill is running, so a look-alike `Agent` dispatch is detectable
   (full PreToolUse enforcement is Slice S4):
   ```
   python3 ${KIT_HOOKS_DIR}/skill_marker.py write close <SESSION_ID>
   ```
2. **Run** `session-scope.sh <SESSION_ID> <project_dir>` — this produces
   `_session_scope-<SID>.md` containing ONLY this session's prompts, file changes,
   git delta, and output logs.
3. **Read** `_session_scope-<SID>.md` — this is your primary source.
   - If scope file contains `## Scope Source: CONVERSATION`: derive scope from **conversation context only**. Do not read other unarchived log files — those belong to other sessions.
4. **Cross-check** with `git diff --name-only` for completeness (secondary source).
   Files in git diff but not in session scope may belong to another session — include
   only if corroborated by conversation context.

Group everything by date (from log file timestamps). Use log timestamps, not current time.

### 0b. Auto-Register an Unregistered Topic (backstop)

`/work-done` mints the tracking layer at ship (its Step 5c). This is the **backstop** for a session that shipped work without ever running `/work-done` — the direct-implement path that leaves the task-tracking layer absent, so the retire, omission and freshness gates have nothing to read.

Run it once per topic this session shipped, passing that topic's anchoring artifact (`_THOUGHT` for a thought-bound topic, else the `_PLAN` for a Mode-C ship):

```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py auto-register <SESSION_ID> --spine <SPINE_PATH>
```

- Deterministic code — no model call, no operator memory.
- Idempotent per surface on the `[<slug>-<ts>]` tag, so running it after `/work-done` already minted is a no-op, and a half-minted topic from a crashed run is completed.
- Always exits 0. A `skipped` (no anchoring artifact) or `partial` status never blocks the close — surface the returned `warning:` to the operator verbatim and carry on.

A minted line is marked `[auto-registered]` and does not satisfy the framing validator; that is the standing obligation, not a defect.

### 0c. Reconcile Framing Obligations

Auto-registered lines carry an `[auto-registered]` marker until a human frames them. Run the shared reconcile — the SAME function the SessionStart TODO scan and the omission Stop hook use — over the `TODO.md` of each project this session touched:

```
python3 ${KIT_HOOKS_DIR}/framing_obligation.py surface --todo-file <project>/TODO.md [--todo-file ...]
```

It does two things: any marked line that NOW passes the framing bar has its marker **stripped** (discharge — that line is never flagged again), and anything still unframed is printed as a warning block. Print that block to the operator verbatim if non-empty.

This is a SOFT surface — it never blocks the close. Fail-open by construction: a missing or unreadable `TODO.md` contributes nothing.

If the operator wants to frame a line now, edit it in place to carry `Problem:` / `Context:` / `Guiding policy:` / `Master plan:`; the next reconcile removes the marker automatically.

### 0d. The UNTRACKED half — unframed `[Thought]` lines carrying no marker

The reconcile above sees only marked lines. A `[Thought]` line that is unframed AND carries no `[auto-registered]` marker — a hand-typed line, the line the `/clarification-v2` door files — is tracked by nothing, which is how such a line reads as "just a note" for weeks. Since streamed-dancing-goose S5 that half is reported by **code, not by this skill's prose**: the Stop hook `${KIT_HOOKS_DIR}/check-framing-audit-stop.sh` renders it at the end of every session however the session ends, and the SessionStart TODO scan renders the same block at the next start (the channel the tree has established as reaching the operator — `work_done_report.py:11-28`). Both are report-only; neither blocks.

This step is therefore a **read-out, not the guarantee**. Run the audit over each touched project's `TODO.md` and print the block verbatim if non-empty:

```
python3 ${KIT_HOOKS_DIR}/todo.py audit-coverage --file <project>/TODO.md --surface
```

The audit **exits 1 whenever any untracked line fails** — treat that as the report it is and continue; never abort the close on it. A marked line is classified `not-framed-yet` (the obligation surface's job) and never fails the audit. JSON instead of the block: drop `--surface`. To frame a line now: `/work-frame-and-create-todo`, or edit it in place; a refusal from `todo.py add --validate-framing` now also prints one JSON object on stdout (`{"status": "refused", "reason": "framing", "errors": [...]}`) beside the `  ✗ ` stderr lines.

### 1. Create Diary Entries

Create diary entry in **each project touched**: `[project]/Diary/YYYY-MM-DD.md`
Create entry for EACH date with logged work (logs may span multiple days).

All diary paths are relative to the **Projects root** — discover it dynamically from existing `_project_path` log entries (the common ancestor of all project paths). Do not hardcode it.

- CV work → `[Author Name] Profile/CV/Diary/`
- Work projects → `[Company]/[sub-project]/Diary/` or `[Company]/Diary/`
- Root-level / general topics (no specific project) → `Diary/` under the Projects root
- Multiple projects → create entry in each

**Delegate the drafting (orchestrator-pattern §1 adapter).** For each project touched, dispatch diary drafting to `close-diary-drafter` (Sonnet) via the `Agent` tool (`subagent_type: close-diary-drafter`) — pass the project's session-scope summary (`_session_scope-<SID>.md` content + git delta) + the project name + the diary date (from log timestamps). The adapter returns ONLY the diary body, grounded in the passed evidence; the orchestrator persists it to `[project]/Diary/YYYY-MM-DD.md`. Its `model: sonnet` frontmatter right-sizes the run independently of the session model.

**Quality gate + one-tier-up fallback (producer-never-verifies).** Verify each Summary and Key Decisions statement traces to a git-diff entry, log line, or conversation scope; mark untraced statements "(from memory)" or remove them. If material statements are untraceable (the adapter drifted), re-dispatch `close-diary-drafter` ONE tier up (Sonnet → Opus), capped once, then re-check. If the adapter is unavailable or fails after the single escalation, the orchestrator drafts the diary inline — **no regression; the close always completes.**

**Diary format:**
```markdown
# YYYY-MM-DD — [Project] Session

## Summary
[What was done]

## Key Decisions
[Decisions made and why]

## Correspondence
[Messages received and sent — final versions only, not drafts]
[Questions asked on any topic and summary of answers]
[Grammar corrections, casual content]

## Learnings
[What to remember]

## Open Items
[What remains]
```

**Correspondence section rules:**
- Include only if session had casual content (messages, questions, corrections)
- Store final sent versions, not every draft iteration
- For received messages: quote the key parts, attribute to sender
- For sent messages: full final text
- For Q&A (conversions, grammar, factual lookups): question + concise answer

### 1b. Update TODO.md

`/work-done` owns per-ship-event TODO Done writes + counter advancement
(Slice J, 2026-06-01). `/close` runs only the thought-level metrics
append (which writes this session's metrics rows into the `_thought`
`## Sessions` section, not TODO Done state) and the cleanup pass (which
sweeps existing `[x]` items into the Done section).

**Thought-level metrics.** If the active SESSION_ID has a topic with a
`thought_file_path` set (check via
`python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py read {SESSION_ID}` — look for
`topic_state.thought_file_path`), append the per-session metrics rows
under today's `### YYYY-MM-DD session [sid:…]` block in the spine's
`## Sessions` section. Idempotent on `(date, sid_prefix)` — a re-run
replaces only the rows `/close` itself wrote (each carries
`<!-- close-metrics -->`); the `/work-done` bullet under the same
heading is never touched, and the two coexist in one block. The section
is created if absent, outside `# Discovery`. **Never `## Metrics`** —
that is a locked Discovery field, and writing there moved the Step-9
hash on every close (streamed-dancing-goose S1). OMTM rows render only
when `omtm` is set; omit them for `exploration` / `research` thoughts.
A lock-contended run reports `✗ …` on stderr and exits 1 — record it as
a failed step, do not retry in a loop.

```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py append-metrics {SESSION_ID} \
    '{"duration_min": <N>, "opus_tokens_k": <k>, "sonnet_tokens_k": <k>,
      "tasks_completed": <N>, "files_touched": [...],
      "gate_progress": "<Gate X → Gate Y>",
      "omtm": "<name|null>", "omtm_value": <v|null>,
      "line_in_sand": "<target|null>",
      "omtm_prior": <prior|null>, "decision_rule_trigger": "yes|no|null",
      "delivered_slice": "<S<n> — <title>|omit>", "commit_sha": "<sha|omit>",
      "commit_url": "<url|omit>", "diff_url": "<url|omit>", "diff_range": "<a..b|omit>",
      "slice_counter": "<n/N slices done|omit>", "next_slice": "<S<n+1> — <title>|omit>"}'
```

**The delivery record (streamed-dancing-goose S7).** The same block also
names what shipped and what is next — additive rows under the same
`<!-- close-metrics -->` marker, rendered only when their keys are present,
so a session that shipped nothing adds nothing. Fill them from code, not
memory:

- `commit_sha` — the commit this session landed for the slice
  (`git -C <repo> log -1 --format=%H -- <the slice's declared paths>`).
- `commit_url` / `diff_url` / `diff_range` — derived from the repo's remote by
  `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py delivery-links <repo> <sha>`
  (add `--base <sha>` for a multi-commit range); paste its JSON fields
  through. With no browsable remote they are `null` and the row degrades to
  the bare sha — never hand-type a URL.
- `delivered_slice` / `next_slice` / `slice_counter` — from the run's register
  when an `/execute-plan` walk is active: `run.py session-summary
  {"state_path": "<topic state json>"}` gives `completed` / `in_flight` /
  `incomplete_remaining`; the counter is `<len(completed)>/<total>` and the
  next slice is the first entry `compute-handoff` returns. Omit all three when
  no register is bound.

Also run the research token parser (best-effort — exits 0 if no manifest):
```
python3 ${KIT_HOOKS_DIR}/research_token_parser.py {SESSION_ID}
```

**Cleanup.** Sweep `[x]` items into the Done section. For each project
touched this session with a TODO.md:
```
python3 ${KIT_HOOKS_DIR}/todo.py cleanup --project PATH
```

Runs in both full and lightweight close. If no TODO.md exists, skip.

### 1c. Session Metrics

**Two outputs — diary (brief) + Stats.md (structured):**

**A. Diary** — add to diary entry:
```
## Metrics
See Stats.md for session metrics.
```

**B. Stats.md** — append one row to `{project}/Stats.md`:

```
| Date | Type | Plan | TODO | Opus ~k | Sonnet ~k | Tests ± | Commits | ~min |
```

Per-project file. If `Stats.md` doesn't exist, create it with the header row.

- **Type:** `thinking` (pre-plan + plan), `doing` (implementation), or `mixed`
- **Plan:** plan file basename if plan mode was used, `-` if not. Plans now live in two locations: legacy `~/.claude/plans/<slug>.md`, or project-side `<project_root>/Thoughts/<project_slug>_PLAN.md`. Use the basename in either case.
- **TODO:** read from pre-plan state file if available:
  `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py read {SESSION_ID}`
  Extract `todo_item` from gate0 output. If no state file, use `-`.
- **Opus/Sonnet ~k:** approximate per-model token breakdown from session
- **Tests ±:** `before→after (+new)` for doing sessions, `-` for thinking
- **Commits:** count of commits made this session
- **~min:** approximate session duration

**C. `/double-check` cost view** — refresh the per-project `/double-check` cost aggregate by running, once per touched project root:

```
python3 ${KIT_HOOKS_DIR}/dc_stats.py <project_root>
```

This rewrites the `<!-- DC-STATS:BEGIN -->`…`<!-- DC-STATS:END -->` managed block in `{project}/Stats.md` (cost / discrepancy / verdict grouped by `(caller, topic)`, aggregated from the per-run audit markers) and preserves the manual session-metrics table above. Code-only, idempotent; skip if the project has no `/double-check` runs (the block initializes empty without error).

**D. Skill-run metrics (orchestrator-pattern — S3 seed formalized by S5).** Record one row for this `/close` run and refresh the managed Stats block:

1. **Secondary (model-weighted usage)** — derive it for the session (reuses `research_token_parser`, no new capture):
   ```
   python3 ${KIT_HOOKS_DIR}/skill_metrics.py secondary <SESSION_ID>
   ```
2. **Append the run row** — `run_kind:"full"`; `omtm_main_chat_delta` = the main-chat token delta the run added (use the Opus+Sonnet `~k` from B as the proxy until precise per-run capture lands, else `null`); `secondary_model_weighted` from step 1:
   ```
   python3 ${KIT_HOOKS_DIR}/skill_runs.py append \
     '{"skill":"close","sid":"<SESSION_ID>","run_kind":"full","omtm_main_chat_delta":<k|null>,"secondary_model_weighted":<v|null>,"note":"close run"}'
   ```
3. **Refresh the managed Stats block** in each touched project's Stats.md (idempotent; rolls up OMTM/Secondary + the three signals — altimeter, ceiling alarm, blocked look-alikes):
   ```
   python3 ${KIT_HOOKS_DIR}/skill_metrics.py write-stats {project}/Stats.md
   ```

The blocked-look-alike signal is emitted automatically by the S4 guardrail hook; the ceiling-alarm signal is set by passing `"ceiling_incident": true` on the row when a 1M-tier incident occurs.

Runs in both full and lightweight close.

**E. Produced-claim read coverage (output-security S2) — on demand, read-only.** Render the read-side boundary coverage for this session:

```
python3 ${KIT_HOOKS_DIR}/output_security_registry.py close-report
```

Computed on demand from the read trail — never captured synchronously, never on any enforcement path, and it gates nothing. Surface the block as an informational read-out.

Read it as written rather than as a bare number. At slice S2 the figure is **0.0 over a non-zero denominator**, and the block names the conjunct holding it there: no producer calls the write-side envelope yet and no judge exists until S3. That is incompleteness by construction, not a boundary that is failing, and it must not be "fixed" by stubbing a pass-through judge or by counting a read-time container as a write-time envelope — either would corrupt the exact metric this is for. The block also states what its denominator actually covers (one instrumented seam out of the registry's full row count, and that one conditional on the deep path), so the figure cannot be read as covering the whole read surface. The block computes that count from the live registry — this prose deliberately does **not** repeat the number, because it said "eighteen" through four slices that each added rows, and a hardcoded count in skill prose is a statement that goes stale silently while the block beside it stays right.

Idempotent — a pure projection of the trail and the registry, so running it twice changes nothing. Skip silently on a tree that predates the module.

**F. Output-security calibration (S5) — on demand, read-only.** How often the guard turned out to be right, computed from the operator's OWN recorded flag resolutions:

```
printf '%s' '{}' | python3 ~/.claude/skills/output-security-resolve/run.py summary
```

Render the returned `readout`. It states how many flags were resolved, how many of those were graded as real or false, and the agreement and false-positive rates over the graded set. Flags resolved as unexaminable are counted in the first number and in neither rate — they say nothing looked, so folding them into either would report the guard's accuracy from a case where the guard did not run.

Deliberately a **separate sub-section from E rather than an addition to it**: E reports the read-coverage residual, which this slice did not move and must not appear to move. The two figures answer different questions and share no denominator.

Informational only, off every enforcement path, and it gates nothing — a missing or empty trail renders "no data yet" rather than a zero, because a zero would read as "the guard was never right", which is a claim. Skip silently on a tree that predates the skill.

If `remaining_open` is above zero, mention that flags are still awaiting a decision and that `/output-security-resolve` walks them one at a time. Do not resolve any of them during `/close` — the decision is the operator's, one flag at a time, and a close routine is not where it belongs.

**G. Insecure-source flag rate (S6) — on demand, read-only.** The locked secondary metric: how many of the sources cited across the research corpus are recorded as having served a confirmed violation:

```
python3 ${KIT_HOOKS_DIR}/output_security_record.py source-rate
```

Render the returned block. The numerator counts distinct sources recorded from `CONFIRMED_BLOCKED` resolutions only — not raw flags, and not violations the operator accepted — and the denominator counts distinct sources cited across the `_RESEARCH`/`_CLAIMS` corpus. Both sides fold URLs through the same normalisation function, which is what makes the ratio mean anything.

A **third** sub-section, never folded into E or F, for the same reason F is separate from E: this is a third question with a third denominator, and adding it to E would make the read-coverage figure appear to move when S6 did not move it. The corpus scan runs here, on demand, and never on the write path.

Directional only — it gates nothing and has no alarm level. An empty corpus renders "insufficient data" rather than `0/0` or a bare zero, because a zero would read as a claim that no source has ever served a payload. Skip silently on a tree that predates the module.

**H. Spotlighting steerability probe (S7) — on demand, read-only.** The measured effect of the spotlighting instruction on a reader's compliance, from the last recorded probe run:

```
python3 ${KIT_HOOKS_DIR}/output_security_probe.py report
```

Render the returned block. It reports a **delta, never a score**: the difference in steer rate between a spotlit read and a bare read of the same claims, shown twice — the **raw** delta and the **bias-corrected** delta, each explicitly labelled — beside the control-arm rates the correction is computed from, the sample count, and the pinned reader model.

This block **renders, it does not dispatch.** The probe itself is out-of-band: it runs only when an operator runs `output_security_probe.py run`, it is reachable from no enforcement path, and it registers no hook. `/close` never triggers a probe run.

Read the two numbers as the block labels them, because the two misreadings point in opposite directions and each attaches to exactly one figure. **The lower-bound statement is about the RAW delta only** — a small negative raw reading is within the predicate's measured bias and is not evidence that spotlighting works against the reader. **A small positive CORRECTED reading**, conversely, may be the correction's own residual rather than a real reduction. A *negative* corrected reading cannot be discounted at any magnitude: the bias has been subtracted and what remains of it points upward.

The block also names **how many registered seams were probed and how many were not** — most seams put a claim in front of a model with no code composing the string, so there is no arm to compare and nothing was contrived to make one. A two-seam delta must not be read as a statement about the whole read surface.

A **fourth** sub-section, never folded into E, F or G, for the same reason G is separate from E and F: a fourth question with a fourth denominator, and folding it into E would make the read-coverage figure appear to move when S7 did not move it.

There is **no threshold, no alarm level and no gate** — the locked decision requires a measured reduction rather than a pass/fail, so nothing fails on a drop and no code draws a conclusion from the number. An empty store renders "no data yet" rather than a zero. Skip silently on a tree that predates the module.

**I. Work-done omission — report-only verdicts (slice-register-plan-ref-resolution S1).** What the session-close safety net would have blocked, but deliberately did not:

```
python3 ${KIT_HOOKS_DIR}/work_done_report.py close-block {SESSION_ID}
```

Render the returned block verbatim when it is non-empty, and print **nothing at all** when it is empty — a close-out that announces its own silence is noise, and an empty record is the ordinary case.

**Substitute the real session id** — `{SESSION_ID}` is the placeholder form this file uses everywhere else, not a shell variable. Do not pass `"$SESSION_ID"` through to bash: it is unset in that context, expands to the empty string, and the renderer then treats an empty id as "no filter" and reads across all history — silently doing the opposite of what the next sentence asks for. (That is what the first version of this block did.)

Without a session id the surface fills with other sessions' business and the operator learns to skip it, which is how a reporting channel dies.

**Two kinds render separately, and the separation is the point.** "I resolved the scope and found unrecorded commits" and "I could not open this topic's plan references, so I could not look" are different statements about how much to trust the silence. Collapsing them would re-create at the operator's level exactly the identity this change removed at the code level.

**Rows are per session, not per turn.** The hook behind this fires on every assistant turn, so the record de-duplicates on `(session, topic, project, kind)`. That matters beyond tidiness: these rows are the raw material for measuring the follow-up's false-positive rate, and a per-turn count would inflate it by session length rather than by number of omissions.

**Do not offer to clear these rows here.** An earlier version of this block did, and the reasoning was wrong three ways: the session filter above already stops a later session re-rendering them, so the stated harm did not exist; the rows are the follow-up's false-positive measurement base, which this same block says two paragraphs up, so deleting them destroys the evidence the block exists to gather; and it would not even work, because the hook fires again on the next turn and re-appends. A `clear` verb exists on the module for maintenance, and this is deliberately not where it is reached from.

**This block is the channel, not a convenience copy of one.** The omission gate is a Stop hook that exits 0 on a report-only verdict, and the two channels such a hook obviously reaches for were both established to be dead: no shipped hook in this tree surfaces text on exit 0 (`output_security_judge.py:675-683` records a sibling slice pricing exactly this and declining to claim its own `systemMessage` emission works), and routing a Stop-time verdict through the SessionStart injected-context renderer is a category error, since the harness documents no Stop→SessionStart carry-over. So the durable record read here is what the requirement is reported against. The hook's stderr line is additive best-effort and is **not** relied upon. **Removing this block does not make the report quieter — it makes it invisible.**

Why a report rather than a block: the resolver repair that lets this gate see file scope at all turns on six topics that have never had a verdict, so the gate is made to speak before it is allowed to act. Turning blocking on is a registered follow-up whose stated precondition is narrowing the bare-basename scope match first — until that lands, a newly-resolving scope could attribute a concurrent session's commit and stop someone's close over work that was not theirs.

Read it as evidence, not as an accusation: report-only means nothing was verified against what the operator actually shipped. The rows are the raw material for measuring the false-positive rate that makes the follow-up decidable on evidence rather than on nerve.

A **fifth** sub-section, never folded into E, F, G or H — a fifth question with a fifth denominator, and it shares no metric with the output-security blocks above it. Informational only; it gates nothing and changes no exit code. Skip silently on a tree that predates the module.

### 1d. Optional Sessions Log Annotation (`--annotate` flag)

When `/close` is invoked with `--annotate "<bullet>"`, dispatch the
annotation to the active topic's spine `## Sessions` log before archiving
— **under the `close` writer identity, always**:

```
python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py annotate-session {SESSION_ID} "<bullet>" --writer close
```

`--writer close` is load-bearing, not cosmetic (streamed-dancing-goose S1).
The same CLI serves `/work-done`, whose default identity is `work-done`,
and the work-done omission gate keys on that identity: a bullet written
without `--writer close` reads to the gate as "this session recorded its
ship" and silently retires the check. The bullet lands beside the metrics
rows under the same `### <date> session [sid:…]` heading; a re-run replaces
only the `close`-tagged bullet.

Convenience surface — equivalent to invoking the CLI manually. Useful for
thinking-only sessions where no `/work-done` fired but a Sessions log
entry is still wanted. Runs before Section 2 (Archive Logs) so the spine
write is not archived as a session artifact. Absent flag → skip.

### 1d-2. Work-done omission escape hatch (project-tracking-staleness S4)

The `check-work-done-omission.sh` Stop hook (Phase 2) blocks session close when
the session shipped work under an active tracked topic but did not record it with
`/work-done`. The intended fix is to run `/work-done`. If the omission is
**deliberate** (e.g. the commits are not a slice ship worth recording), the
operator-facing escape hatch is `/close --skip-work-done-check <topic>`, which runs:

```
python3 ${KIT_HOOKS_DIR}/work_done.py skip-work-done-check {SESSION_ID} <topic> --reason "<why>"
```

That records a `{skipped: true, reason}` row in the completed-work ledger (so the
skip is a visible, deliberate, audited choice — never silent drift); the next close
then passes. `--reason` is required and non-empty. The runnable command is the
`work_done.py` CLI above; this flag is the documented convenience name.

Post-plan handoff is owned by Slice I F10 (AskUserQuestion hard-gate
after ExitPlanMode); mid-session manual handoff remains available via
`/prompt-for-handoff <path>`. `/close` no longer routes handoff prompts.

### 1e. Land-readiness co-write (S7, worktree topics only)

When the session ran on a worktree-per-topic topic (a harness/config-source
topic, or a Projects topic once S9/`[[storage-decouple]]` lands), record the
topic's advisory land-readiness so a later resume knows whether to route the land
through the A6 gate. This is a **routing hint only** — never a safety gate (the A6
verify-then-land gate re-computes green/red at land time, DESIGN A13/B3):

```
python3 ${KIT_HOOKS_DIR}/land_readiness.py write <topic-slug> <in-progress|ready-to-verify-then-land>
```

Choose `ready-to-verify-then-land` only when the work reached a landable point
(slices done, tests green); otherwise `in-progress` (the safe default). The record
persists across sessions — unlike `_session_scope-<SID>.md`, it is **NOT** moved to
`_processed` (Section 2), so the next session's resume/handoff worker can read it. A
write failure is an advisory no-op (fail-open) — never block `/close` on it. Skip
for non-worktree topics.

### 2. Archive Logs (Session-Scoped)

After processing, archive ONLY logs matched by session-scope to this session ID:
- Move `prompts-*-<SESSION_ID>.log` → `<project>/.claude/logs/_processed/`
- Move `outputs/*-<SESSION_ID>.md` → `<project>/.claude/logs/_processed/`
- APPEND `_session_files-<SESSION_ID>.log` to `_processed/_session_files-<SESSION_ID>.log`,
  then remove the live file (`cat live >> archived && rm live`). Not `mv`: a
  second close in the same session would overwrite the first archive and lose
  the files that close deliberately left uncommitted.
- Move `_git_snapshot-<SESSION_ID>` → `_processed/`
- Move `_session_scope-<SESSION_ID>.md` → `_processed/`
- Remove `_compaction_marker` if present

§3 and §4 run AFTER this archive and still need the session's file ledger:
`session-scope.sh --publish-scope` / `--harness-scope` read
`_session_files-<SESSION_ID>.log` from `_processed/` as well as from the live
location. (Before that fallback existed, a close declared only the reconciled
TODO/Diary/Stats and promoted nothing — found by an independent post-S8 audit.)

### 3. Git Commit — publish a DECLARED scope

`/close` names the paths it publishes. It does not stage-then-bare-commit: git's
index is one file per worktree, so a `git commit` naming no paths takes whatever
any concurrent session has staged. Two of the four cross-attributed commits on
record came from this surface.

**Step 0 — where is this commit landing?** Outside a topic worktree the commit
gate refuses a commit that MIXES shared bookkeeping (`TODO.md`, `Diary/`,
`Stats.md`, `Thoughts/`) with domain files, and a refused publish leaves its paths
staged in the shared index. So decide first:

```bash
bash ${KIT_HOOKS_DIR}/worktree-detect.sh <project_root>   # exit 0 = inside a topic worktree
```

- **Inside a topic worktree (exit 0):** publish the full declared list (Steps 1–3).
- **Primary checkout (exit 1):** compile BOTH halves, and publish only the
  bookkeeping half in Steps 1–3:
  ```bash
  ${KIT_HOOKS_DIR}/session-scope.sh <SESSION_ID> <project_root> --publish-scope --bookkeeping-only
  ${KIT_HOOKS_DIR}/session-scope.sh <SESSION_ID> <project_root> --publish-scope --domain-only
  ```
  If the domain half is non-empty, ask with `AskUserQuestion` — never decide it
  silently: **(a) Leave uncommitted (Recommended)** — domain work belongs in its
  topic worktree (`/work-start`); name the files in the Final Summary.
  **(b) Publish separately as a declared out-of-tree commit** — only these paths,
  in their own commit after the bookkeeping one:
  ```bash
  ALLOW_OUT_OF_TREE=1 python3 ${KIT_HOOKS_DIR}/commit_scope.py publish -m "<message>" -- <domain paths…>
  ```
  `ALLOW_OUT_OF_TREE=1` authorizes the placement only; the scope gate still
  requires the paths, which `publish` supplies.
- **Detection error (exit 2): do NOT publish.** The gate treats a detection
  error as a block for every commit, so any publish would be refused after
  staging and leave paths staged in the shared index. Report the declared list as
  uncommitted and name the error in the Final Summary.
- **`session-scope.sh` exits 3** (it could not load the manifest classifier):
  do not publish — it refuses to emit a split it cannot compute.

**Step 1 — compile the declared list (code, deterministic):**
```bash
${KIT_HOOKS_DIR}/session-scope.sh <SESSION_ID> <project_root> --publish-scope [--bookkeeping-only]
```
This emits repo-relative paths: this session's own ledger entries, plus any dirty
path matching a `merge_union: true` entry in the shared-bookkeeping manifest that
the ledger missed (`TODO.md`, `Diary/`, `Stats.md`). That reconciliation is what
stops a ledger-less run from compiling an empty scope and leaving the TODO and
diary updates uncommitted on disk.

**`Thoughts/` is never reconciled, and that exclusion is load-bearing.** It is a
`dir-prefix` entry with `merge_union: false`, so a concurrent session's dirty
`Thoughts/<other-topic>_PLAN.md` equally "matches the manifest and is absent from
my ledger" — reclaiming it would sweep another session's whole artifact into this
commit, which is precisely what `ffeeef23` and `213237fa` did. A jointly-owned
append file is safe to reclaim because both sessions' lines belong there; a
session-owned spine is not.

**Step 2 — preview, and feed the composer a SCOPED summary:**
```bash
python3 ${KIT_HOOKS_DIR}/commit_scope.py publish --dry-run -m "x" -- <paths…>
```
Its `stat` field is the `--stat` for the declared paths only. This replaces the
former `git diff --cached --stat`, which read the WHOLE shared index — so the
commit message has, on this surface, been composed from another session's staged
work. Feeding the composer a scoped summary is what makes the message describe
what the commit actually contains.

Dispatch that summary to the adapter through the `Agent` tool
(`subagent_type: close-commit-composer`, `description: "compose session-close
commit message"`). Its `model: haiku` frontmatter right-sizes the run
independently of the session model — the orchestrator never carries a model
field. Pass the declared file list + the scoped `stat`; receive back ONLY the
commit message. `close-commit-composer` needs no contract change: it already
takes a file list first.

**Fallback (no regression):** if the adapter returns nothing usable, errors, or
is unavailable, use the fixed message `Session close: diary, TODO updates`. The
adapter never blocks the close.

**Step 3 — publish:**
```bash
python3 ${KIT_HOOKS_DIR}/commit_scope.py publish -m "<message>" -- <paths…>
```
`publish` scopes BOTH verbs in one process — the add and the commit — because a
scoped stage does not bound a bare commit, and no verb hands control back in
between. It announces any path another live session also wrote BEFORE committing
it, so a joint edit is stated in advance rather than discovered afterwards; both
sessions' lines survive that commit, which is correct for a shared append file.

Add the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer per
repo policy. Do not push. User pushes manually.

**Guards:**
- **Empty scope after reconciliation → skip and report.** Never fall back to a
  bare commit; that is the defect this step exists to prevent. `publish` already
  returns `status: skipped, reason: empty-scope` rather than committing.
- **`MERGE_HEAD` present → stop.** Do not publish during a merge; finish or abort
  it first. `publish` refuses any in-progress sequencer state.
- A declared path that is a directory is refused by `publish` — declare
  individual files, because a directory pathspec sweeps everything beneath it
  including another session's files.
- `.claude/logs/` is gitignored, so §2's archive move has no git effect here.

### 4. Promote Harness Changes (`~/.claude/` → config-source → config-source-remote)

Live `~/.claude/` is a **the deploy step target** (slice S1). Harness changes must
flow through the **promotion path** — staging (config source) → shared-repo PR →
merge → the deploy step — never through the demoted `~/.claude/.git` repo (remote
`config-source-remote-legacy`), which never reaches the `config-source-remote` shared repo. Slice S2
built the `claude-promote` flow that does this in one gesture:

**Declare the scope here too.** Compile this session's own `~/.claude` paths and
pass them, so the promotion commits your harness edits and not a concurrent
session's:

```bash
SCOPE=$(${KIT_HOOKS_DIR}/session-scope.sh <SESSION_ID> <project_root> --harness-scope | tr '\n' ' ')
~/.claude/bin/claude-promote -m "Session close: harness updates" --session-scope "$SCOPE"
```

`--session-scope` and `--paths` feed one declared set and differ only in
provenance; `--session-scope` is the name this caller passes the session's own
list under. The projection is restricted to the managed-config scope
(`agents`, `bin`, `CLAUDE.md`, `hooks`, `rules`, `settings.json`, `skills`), so
transient trees like `plans/` and `logs/` are never offered for promotion.

**When the list comes back empty, omit the flag entirely** rather than passing an
empty string — an empty declaration is not the same as "publish everything", and
`claude-promote` refuses a flag given without a value. A close that touched no
harness file should simply run the unflagged form, which then reports "nothing to
promote".

`claude-promote` leaves the capture step deliberately UNSCOPED and scopes only the
git verbs. That inversion is intentional: scoping the capture would leave a
concurrent session's live edits uncaptured in the source, and the later unscoped
the deploy step would then prompt-or-overwrite them — destroying their work.
Foreign edits are captured into the source working tree and left uncommitted,
which keeps `claude-verify --phase post` and `claude-divergence-check` green while
still not mis-attributing anything. Mis-attribution is prevented at the commit,
not at the capture.

It then — only if there is something to promote — opens a short-lived-branch PR,
self-merges in `quick` `.pr-mode` (or stops for external review in
`extra-safety`), runs the deploy step, and writes a `green-*` recovery tag. It
exits 0 with "nothing to promote" when the harness was untouched this session, so
it is safe to run on every close. Report its final status line in the summary,
including any "NOT being promoted" residue lines — those name work left
uncommitted on purpose because it belongs to whoever declared it.

Guards:
- **Exit 3** (`extra-safety` hold) is not an error — the PR is open awaiting an
  external approver; surface the PR URL and stop, do not retry.
- If `claude-promote` is unavailable (pre-S2 harness) or fails, do **not** fall
  back to committing to the demoted `~/.claude/.git` — that reopens the S2 gap.
  Report the failure and let the user run the promotion manually.
- Never force-push; never push `main` directly — the flow honors the Branch
  Policy (one short-lived branch → PR → merge → delete; merge-not-rebase).

### 5. Final Summary

After the harness commit, surface a single-line artifact-link block so the user can navigate directly to what this session produced:

```
Plan: [[<plan-basename>]] · Diary: [[YYYY-MM-DD]] · Spine: [[<thought-basename>]]
```

- **Plan:** basename of the `_PLAN.md` if plan mode was used (same source as Section 1c Stats row); omit the segment if `-`.
- **Diary:** the diary file just written in Section 1 (use the date from log timestamps, not current time).
- **Spine:** basename of the `_THOUGHT.md` if the session was bound to one — read `topic_state.thought_file_path` via `python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py read {SESSION_ID}`. Omit the segment if absent.

If multiple projects were touched, emit one summary line per project.

## Rules

- Always create diary entries
- If multiple sessions in one day, append to existing diary file with `### Session N` separator
- Use timestamps from log files for diary dates, not current time

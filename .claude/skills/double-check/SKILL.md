---
name: double-check
description: Independent factcheck of any AI recommendation. Invokes ≥1 checker(s)
  (default 3 Sonnet) as read-only `readonly-checker` agents with isolated context. Use after any AI
  recommendation to independently verify it. Main-session use only — cannot be
  invoked from inside a subagent.
---

# /double-check

Trigger independent fact-checking of an AI recommendation made in this session.

---

## Trigger

Activate when user says:
- `/double-check`
- `/double-check --help`
- "fact-check that recommendation"
- "verify that suggestion"
- "check your last recommendation"
- "double-check your answer"

---

## Parameters

All optional (defaults shown):

| Flag | Default | Description |
|------|---------|-------------|
| `--class C` | (none) | The site class — `gate`, `check` or `fixed`. Resolves the allocation from the configured rigor tier. **This is the form a calling skill uses.** See *Allocation* below. |
| `--sonnet N` | 3 | Number of Sonnet checkers. An explicit count overrides `--class`. |
| `--opus N` | 0 | Number of Opus checkers. An explicit count overrides `--class`. |
| `--rounds N` | 2 | Max convergence rounds |
| `--against TEXT` | (none) | Validation target context. Checker precedence: `--against` > KL sources (Step 2b) > codebase |

Minimum 1 checker total (`--sonnet + --opus ≥ 1`).

---

## Allocation — read the dial, do not name counts

A caller passes a **site class**, not a checker count, because the right count is
the adopter's cost/confidence setting and not a property of the calling skill.
Resolve it at dispatch — one line, and the flags it prints are what you pass:

```bash
python3 ${KIT_HOOKS_DIR}/rigor.py for gate
```

| class | when |
|---|---|
| `gate` | the verdict BLOCKS — the work does not proceed on a DISCREPANCY |
| `check` | routine verification — a discrepancy causes a revision, not a stop |
| `fixed` | a canon-locked pipeline (plan gates, research FC, KL extraction) — returns the canon 3 Sonnet at every tier, so asking the dial here is safe |

Resolution happens **at dispatch, never at install**: the operator can change the
tier at any time (`rigor.py set thorough`) and the next run follows it, with no
reinstall and no edit to any skill. `rigor.py show` prints the active tier, where
it came from, and what each class currently resolves to.

Two things the dial never does, at any tier: it never drops below one **isolated**
checker that is not the producer (`code_first_architecture.md` —
producer-never-verifies is not a setting), and it never touches the fixed
pipelines (`factcheck-convergence.md` §1). At `light` and `minimal` there is one
binding checker, so the result is one isolated opinion rather than a vote — say so
when reporting a verdict at those tiers rather than implying a consensus.

An operator naming `--sonnet`/`--opus` explicitly always wins over the dial; the
dial governs what the skills do on their own.

---

## Execution

**Step 0 — Help**

If user invoked with `--help` or no recommendation is available in session context, output the Parameters table and stop. Do not proceed to Step 1.

**Step 1 — Identify recommendation**

Confirm with user which recommendation to verify. Default: the last substantial AI recommendation
in the session. If ambiguous, ask.

**Step 2 — Write artifact**

Resolve the artifact path (bookkeeping-model audit-trail rule — drafts live under
the owning slug when a topic is bound, else the legacy adhoc location):
```
python3 ${KIT_HOOKS_DIR}/bound_topic.py draft-path <SESSION_ID> DOUBLECHECK
```
This prints `<project>/Thoughts/<slug>-<ts>_DOUBLECHECK_<sid8>.md` when a topic is
bound (advisory bucket — slug-grep returns it; it co-retires with the family), or
`~/.claude/state/plan_validation/adhoc/doublecheck_<sid8>_<ts>.md` for a cold
session with no bound topic. Write the full recommendation text to that path using
the Write tool. Plain prose only; no producer reasoning or context injected.

**Step 2b — Detect KL context**

1. Read the active project's CLAUDE.md:
   - Try `$CLAUDE_PROJECT_DIR/CLAUDE.md` first; fallback to `CLAUDE.md` in current directory.
2. Grep for a `knowledge_library:` line. Extract the declared path (call it `kl_root`).
3. **If found:** Ask user:
   > "KL declared at `[kl_root]`. Which topic files are most relevant to this recommendation?
   > List up to 3 file names or paths within the KL (e.g. `Topics/analytics.md`), or press Enter to skip."
   For each user-provided path: resolve to absolute path by prepending `kl_root`. Validate the resolved
   path exists (use Read — if missing, warn user and skip that file). Store resolved absolute paths as
   `kl_sources` (empty list if all skipped).
4. **If not found:** Ask user:
   > "No KL declared in this project. Do you have reference links or a knowledge base to check against?
   > (Optional — paste URLs or press Enter to skip.)"
   Store as `kl_sources`.
5. If no CLAUDE.md found: skip silently, `kl_sources = []`.

**Step 2c — Claim extraction (Deep engine default; regex fast-path retained as fallback)**

Step 2c builds the `scope_addendum` — the numbered claim list injected into every checker prompt (`_factcheck_engine.py:1841-1842`). Its **claim source** is the shared claim-identification engine, consumed at DEEP through `ClaimIdentificationPort`; the crude regex is retained **permanently** as the reason-logged fallback. This is the only thing that changes here — the checker dispatch, convergence, `{1,3,4}` allocation, and audit-marker schema are unchanged (refc S9 / dc-2c-claim-engine-seam).

**Containment divergence between the two claim sources (output-security S2).** On the DEEP path the numbered claim list is emitted inside a code-applied container with a treat-as-data instruction, because claim text is produced from untrusted external content. The retained regex fast-path in Step 2c.2 below, and every `--auto` run, compose an uncontained list instead. So a checker's prompt now differs by claim source — a known, accepted consequence, recorded here rather than left to be discovered. Containment is a strong reduction of injection risk, not an absolute guarantee; a residual risk remains.

Resolve the mode first: `resolve_mode('/double-check Step 2c')` (from `_claim_harvest.py`) → `MODE_DEEP` by default. On Deep, run **Step 2c.1**; only fall to **Step 2c.2** on a reason-logged `deep_fallback`.

**Step 2c.1 — Deep engine path (default).** Produce the engine's two stages as *this session's own reasoning*, then let the code wire + flatten them (producer-never-verifies: you IDENTIFY here; the isolated read-only checkers VALIDATE later):

1. Reason **Stage 1 (identify + ambiguity gate)** over the artifact: for each candidate factual claim decide whether it resolves to a single checkable meaning; mark the vague/hedged ones `ambiguous` (they are refused, not extracted). Emit one JSON object `{ "candidates": [ { "text", "source_line", "ambiguous", "reason" } ] }` (the `_stage1_prompt` contract in `_claim_engine.py`).
2. Reason **Stage 2 (decontextualize + structure)** over the *non-ambiguous* survivors: rewrite each as a standalone atomic claim, tag `role` (`backward` = source-grounded/factual, `forward` = advice/thesis), score the six flags. Do NOT judge truth. Emit `{ "claims": [ { "text", "source_line", "role", "flags": {6} } ] }` (the `_stage2_prompt` contract).
3. Hand both JSON strings to the seam (code wires the engine + flattens): write a short `.py` that calls
   `_dc_claim_seam.build_scope_addendum(source_path=<artifact_path>, source_text=<artifact text>, stage1_json=<S1>, stage2_json=<S2>, runs_sidecar=<artifact_path>.claim-runs.md)` and prints the result JSON; run it. The seam feeds the session JSON through `in_session_bootstrap` → `TwoStageClaimEngine.identify(..., DEEP)` (engine core untouched), drops FORWARD claims, returns the flattened `scope_addendum` (BACKWARD claims only, **each carrying its `<path>:<line>` anchor**), and — at identification time — records the run (OMTM completeness + refused count) to the co-located `.claim-runs.md` (the CF-4 machinery; `.dc-runs.md` dispatch-audit is untouched).
   - `mode == "deep"` → use the returned `scope_addendum` verbatim, and **stash the returned `provenance` token** — Step 2d folds it into the marker's existing `pre_check_layer.upgraded_target` free-text (schema byte-unchanged) so an auditor sees the claims came from the Deep engine.
   - `mode == "fallback"` → the engine could not run or yielded no BACKWARD claim; the seam already recorded the non-empty `claim_fallback_reason` to `.claim-runs.md` (never silent). Continue to Step 2c.2.
   - **Attested-deep advisory (S6 / A8) — additive, never a block.** The seam result also carries a `provenance_advisory` field: the separate-actor attested-deep disposition (`_claim_attest.classify_provenance`) over the artifact's co-located `.claim-runs.md` sidecar — the SAME sidecar the `dc_obligation` blocking discharge inspects. It is computed **alongside** — and never changes — the `scope_addendum` bytes or the discharge/block verdict (which stays byte-stable at existence-of-any-row; a `default`/legacy row still discharges, Rule 5). If `provenance_advisory` is non-`null` (a `flagged` deep-asserting-no-record case), **surface that one `ADVISORY — …` line to the operator** (like `/plan`'s stderr flag or `/clarification`'s witness); attested-engine / authorized-legacy are info/silent (`null`). This is advisory-only — it never blocks the run, gates the dispatch, or affects convergence. With this read, `/double-check` counts as witnessed in the attestation-coverage meter (`_claim_runs_aggregate.py`).

**Step 2c.2 — Regex fast-path (retained fallback + the AI-free path).** Scan the artifact text for structural claim anchors — specific, verifiable references embedded in the prose. Apply all patterns; deduplicate results:

- **Python file paths:** strings matching `[\w./]+\.py` with optional `:\d+` line suffix (e.g. `worker.py:234`, `scripts/warmup.py`)
- **Line number references:** phrases `lines?\s+\d+[-–\d]*` near a named entity (e.g. `lines 322-323`, `lines 824-825`)
- **Callable identifiers:** `[a-z_]\w*\(\)` patterns (e.g. `_fetch_analyst_table()`, `compute_freshness_profile()`)
- **Snake-case named identifiers:** multi-part `[a-z_][a-z_0-9]*(?:_[a-z_0-9]+){1,}` used as named code entities (e.g. `detect_hollow_consensus`)
- **Named guards/checks** mentioned by name in edge-case text (e.g. `_check_concurrent_chromium()`)

Build `scope_addendum` as a numbered list:
```
Structural claims found in recommendation (verify each, plus any others you identify independently):
1. [item]
2. [item]
...
```

If no structural anchors found: set `scope_addendum = "(none extracted — verify all claims you find independently)"`.

**Both paths converge here.** Store the resulting `scope_addendum` in session working memory. **Do not re-extract between convergence rounds** — the same value is passed to all checker dispatches in all rounds. The engine wires only into this interactive path; the `--auto` path (Step 2d.0) stays AI-free and never runs the engine.

**Step 2d — Interactive pre-check + self-assessment gate + (a)/(b) gate + source-conflict assist (Slice S4)**

This is the interactive target-upgrade-and-confirm contract. It runs for **interactive** invocations. Producer-never-verifies governs: the AI pre-check **produces** the target with no verdict authority; **code** (`validate_self_assessment`) judges the floor; the **user** owns the (a)/(b) and conflict decisions. The named checkpoint sequence is: gather → pre-check → self-assessment gate → user gate → dispatch → marker write.

0. **`--auto` bypass guard (Slice S5 — pure code, no AI, no user).** BEFORE the interactive sequence below, if the invocation is `--auto`, call the engine admission gate `_factcheck_engine.validate_auto_request(caller, payload, auto_flag=True)` (write the call to a short `.py` script and run it; do not inline). It admits iff the caller is on `DC_AUTO_CALLER_WHITELIST` AND the payload is schema-shaped AND the comprehensiveness floor passes on the axis-filled target (axes come from the payload, else `_dc_auto_fill_axes` fills the registry default — groundedness force-included, NO AI; unknown type → a structured `unknown-type` register-error).
   - **On `{"ok": true, ...}`:** SKIP steps 1–6 entirely. Dispatch checkers via the S3 primitives (`validate_allocation` + `build_axis_checker_prompt` per axis of the admitted `target`), then write the marker with `auto_caller` set and **`pre_check_layer` ABSENT** (the locked Signal #1 — a skipped pre-check is detectable by the missing field). NO pre-check agent, NO `(a)/(b)` gate, NO AI in the path.
   - **On `{"ok": false, "error": ...}`:** REFUSE with the structured error (`not-whitelisted` / `no-flag` / `schema-invalid` / `floor-failed` / `unknown-type`) — **fail-closed, do NOT fall back to the interactive sequence**.
   - The `--auto` path runs **no conflict detector** (no AI / no pre-check), so it never pauses; it always proceeds on admission. (UX4's "`--auto` conflict recorded in the marker" presumes a detector + a marker field; neither exists — a bounded deferral, S1-frozen schema.)
   - For a non-`--auto` (interactive) invocation, ignore this guard and continue at step 1.

1. **Pre-check (gather + structure — no verdict).** Spawn ONE parent-spawned `readonly-checker` agent with explicit `model: sonnet` (its grant is already read-only: `Read`, `Glob`, `Grep` — no shell). It reads **ONLY** the caller-named `--against` sources (no autonomous corpus retrieval — Q3) and returns a structured target as JSON only:
   ```
   {"artifact_type": "<one of the 13 registered types>",
    "axes": ["groundedness", ...],            // >=2 incl. groundedness, from that type's registry set
    "claims": ["<claim 1>", ...],             // >=1, each phrased against a named source
    "named_source_artifacts": ["<path/URL/user-input/accepted-hypothesis>", ...],
    "conflicts": [{"between": ["<srcA>","<srcB>"], "note": "<one line>"}]}  // [] when none
   ```
   Tell the agent explicitly: gather-and-structure only; do NOT issue any PASS/Not-Supported verdict; if the caller-named sources do not support a concrete target, say so (return empty `claims`/`axes`) rather than inventing one.

   **Seam B (refc S9 / dc-2c-claim-engine-seam) — engine-sourced `target.claims`.** When Step 2c ran the Deep engine (`mode == "deep"`), seed this target's `claims` from that run's `backward_claims` (the same BACKWARD identification that fed Seam A — one `identify()`, two injection points) instead of having the agent re-invent them; the agent still determines `artifact_type` / `axes` / `named_source_artifacts` / `conflicts` from the `--against` sources. On a Step-2c `mode == "fallback"`, the agent builds `claims` as before. This changes only the claim *source*; the floor gate (step 3) and the (a)/(b) gate (step 4) are byte-unchanged, and `--auto` (step 0) never reaches here (engine wired into the interactive path only).

2. **Source-conflict assist (A4 / UX4).** If the pre-check returns a non-empty `conflicts`, do NOT resolve it yourself. Surface an `AskUserQuestion` with exactly two assist modes — **(1) Compare 1:1 via `/challenge`** and **(2) Get an AI recommendation via `/recommend`** — and proceed ONLY on the user's pick. Invoke the chosen sibling skill (`/challenge` or `/recommend`) per its current shipped contract; record the chosen mode. There is **no AI auto-resolution** without a user pick. (In `--auto`, a conflict is recorded in the marker and the run proceeds — that path is S5.)

3. **Self-assessment gate (A1 / A14 / A9 — CODE, fail-closed).** Call the engine floor validator `_factcheck_engine.validate_self_assessment(target)` on the structured target — write the target to a short `.py` script that imports the engine and prints `json.dumps(validate_self_assessment(target))`, then run it (do not inline via `python3 -c`/heredoc per the sandbox rules). If the result is `{"ok": false, ...}`, the run is **refused before any checker spawns** (fail-closed): surface the named `failures` to the user and offer **(b) Provide a different context** — do NOT dispatch. The floor is locked: >=2 axes incl. groundedness (within the type's registry set), >=1 named claim, >=1 named source artifact. The AI pre-check never grades its own floor — code does.

4. **User gate (UX1 — (a)/(b), no override).** Only a floor-passing target is surfaced. Issue an `AskUserQuestion`: **(a) Accept** / **(b) Provide a different context for validation**. Checkers spawn ONLY after (a). On (b), re-run the pre-check from step 1 (the user converges). There is **no override path**.

5. **On Accept — dispatch via the S3 primitives.** Validate the allocation profile with `validate_allocation(...)`; then dispatch **one checker per axis** in the accepted target, building each checker's instruction with `build_axis_checker_prompt(artifact_type, axis)` (one axis = one checker = one row — the angle is stamped on that checker's marker row). This replaces the generic per-kind prompt in Step 3 for interactive targeted runs; the convergence loop (Step 4) is unchanged.

6. **Marker write.** After convergence, assemble the run's dispatch data — per-round per-checker per-angle rows, per-step cost, and `pre_check_layer = {model: "sonnet", upgraded_target: "<the accepted target, summarized>", self_assessment_passed: true, user_choice: "(a) Accept"}` — and write the audit marker via the engine (`assemble_audit_marker(...)` → `write_audit_marker(...)`). `pre_check_layer` present records that the pre-check ran (the locked Signal #1; its ABSENCE is reserved for `--auto`/skipped runs in S5). **Seam B provenance (refc S9):** when Step 2c ran the Deep engine, append the stashed `provenance` token to `upgraded_target` (e.g. `"<target summary> [claim-source: deep-engine (sonnet); backward=N, refused=M, thoroughness=deep]"`) — this reuses the EXISTING free-text field, so `PreCheckLayer`'s field set / the audit-marker schema / `_DC_OPTIONAL_SIGNAL_FIELDS` are byte-unchanged, and an auditor sees the claims came from the shared engine.

**Step 3 — Dispatch checkers**

Launch all checkers in parallel (single message, multiple Agent tool calls).
Total checker count = `--sonnet N` + `--opus N` (default: 3 Sonnet, 0 Opus).

For each checker, use:
- `subagent_type: readonly-checker`
- `model: sonnet` or `opus` per allocation
- `description: "Round [R] checker [i/total] — double-check recommendation"`

Checker prompt template (fill in values before dispatching):

```
You are an independent fact-checker. You have no access to the conversation that produced this recommendation.

Your task:
1. Read the recommendation at: `[artifact_path]`
2. Verify each factual claim against:
   [if --against provided]: the context: "[against_text]"
   [if kl_sources non-empty]: the following Knowledge Library files (absolute paths): [kl_sources list]
     — use Read to open each file directly by its absolute path
   [if neither]: the codebase files in the current working directory (use Grep/Glob/Read)
3. Additionally verify the following structural claims extracted from the recommendation (plus any others you identify independently):
   [scope_addendum from Step 2c]
4. On Round 2+: focus on unresolved discrepancies from prior round: [discrepancy_list]

Return your verdict in this exact format:
  verdict: PASS
  claims_checked: N
or:
  verdict: DISCREPANCY
  claims_checked: N
  discrepancies:
    - claim: [exact text from recommendation]
      issue: [why it is incorrect]
      citation: [file path + line number, or quoted source text]

Where N = total claims you verified (structural anchors from step 3 above, if any, plus any others you identified independently).

Do not discuss your reasoning beyond what is required for the verdict.
```

**Opus checkers only** (`--opus ≥ 1`): append the following section inside the prompt after "Do not discuss your reasoning beyond what is required for the verdict.":

```
## Scope-Coverage Assessment (advisory — Opus only)

After verifying individual claims, assess whether the recommendation closes the diagnosed gap:
1. Identify the scope of the recommendation: what problem does the proposed solution aim to solve, and what does it explicitly exclude?
2. Assess whether the proposed solution and its actions, taken together, fully address that problem scope.
   - If the recommendation contains an explicit problem or diagnosis statement, use it.
   - If not, infer the problem scope from the solution's described actions and stated constraints.
3. Append to your return:
   scope_coverage: COVERS | PARTIAL | GAPS_FOUND
   scope_notes: [1-3 sentences of reasoning]

Definitions:
  COVERS = solution fully addresses the identified problem scope
  PARTIAL = main issue addressed; secondary aspects left open
  GAPS_FOUND = solution misses significant aspects of the identified problem scope

This assessment is advisory only — your PASS/DISCREPANCY verdict above is independent and takes precedence.
```

Track round number R (starts at 1). Round state is held in the orchestrating session's working memory — checkers are stateless; each round dispatches fresh agents with prior-round discrepancies listed in the prompt.

**Step 4 — Evaluate round and converge**

1. Collect all checker responses.
2. Gather all `discrepancy` items across all checkers.
3. **If 0 discrepancies:** verdict = PASS.
   - Sum `claims_checked` values across all checker responses (parse from each verdict block).
   - Report to user:
     "✓ PASS — all [N] checkers agree, 0 discrepancies. Claims checked: [total]. (Round R of [max_rounds])"
     [if --opus ≥ 1 and Opus returned scope_coverage]:
     "  Scope-coverage (Opus): [scope_coverage] — [scope_notes]"
   - Stop.
4. **If ≥1 discrepancy AND R < --rounds:**
   - Increment R in working memory.
   - Return to Step 3 with `discrepancy_list` = unresolved items from this round (for diff-only focus on next round).
5. **If ≥1 discrepancy AND R == --rounds:** verdict = ESCALATE.
   - Sum `claims_checked` values across all checker responses.
   - Report to user:
     "⚠ ESCALATE — no consensus after [max_rounds] rounds. Claims checked: [total]."
     [if --opus ≥ 1 and Opus returned scope_coverage]:
     "  Scope-coverage (Opus): [scope_coverage] — [scope_notes]"
   - Surface all unresolved discrepancies verbatim, grouped by claim.
   - Stop.
6. **Post-run pointer, not dump (UX3).** Whatever the verdict, report it **plus a one-step pointer** to the run's audit artifact — the source artifact's forward Obsidian wikilink (or, for a read-only source, the project-level audit-index entry keyed by path+hash) where the engine pipeline wrote the per-run marker; in this skill-text mode (where the engine marker writer is unreachable in-session — see Constraints `§7 R<N>.md marker exception`) the pointer is the Step-2 artifact file path. **Never dump the marker inline** — print the verdict + the pointer and let the reader open it. See `## User-facing affordances` below.

| Result | Meaning | Action |
|--------|---------|--------|
| 0 discrepancies | Checkers converged | Report PASS |
| ≥1, R < max_rounds | Still converging | Next round |
| ≥1, R == max_rounds | No consensus | Report ESCALATE with discrepancy list |

**Documented deviations from canonical `factcheck-convergence.md`:**
- §7 R\<N\>.md marker: NOT written — the engine writer is unreachable in-session (second documented exception alongside Trust Hierarchy inversion).
- §4 DIRTY intermediate verdict: NOT emitted — skill text tracks discrepancy presence directly. Intentional simplification; functionally equivalent.

---

## User-facing affordances

What the operator sees and chooses during a `/double-check` run. **This section is documentation only — it explains the affordances; it enforces nothing.** Every rule below is enforced in **Layer 1** (engine code in `${KIT_HOOKS_DIR}/_factcheck_engine.py`) or **Layer 2** (`~/.claude/rules/double-check-allocation.md`); this prose is a *mirror* of that enforcement, not a second source of truth. The runtime that backs each affordance shipped in slices S1–S5; the behavior here is what already runs, not an aspiration.

### Confirming the validation target — `(a)` / `(b)` (UX1)

For an interactive run, a Sonnet pre-check first turns your `--against` into a concrete structured target (artifact type, named axes, named claims, named source artifacts). Once it clears the comprehensiveness floor you are shown the target and asked to confirm:

- **(a) Accept** — proceed; checkers spawn against the accepted target.
- **(b) Provide a different context for validation** — the pre-check re-runs on your new context. You converge by repeating (b) until the target is right.

There is **no override** — if the target cannot clear the floor (fewer than two axes incl. groundedness, no named claim, or no named source), the run is **refused before any checker spawns** rather than waved through. *Enforced by:* `validate_self_assessment` (the floor) + the Step 2d sequence (Layer 1); the comprehensiveness floor is defined in `double-check-allocation.md` (Layer 2).

### When checkers can't converge — the 3 ESCALATE options (UX2)

If the checkers do not reach consensus within the round budget, the run **ESCALATEs** and you choose one of exactly three resolutions:

- **apply-fix-and-re-run** — you fix the artifact, then a fresh dispatch starts from round 1.
- **proceed-as-is** — accept the unresolved state; the run is recorded as ESCALATE and stops.
- **one-more-full-round** — dispatch one more round past the cap.

Your choice is recorded as `escalate_choice` in the run's audit marker. *Enforced by:* `handle_escalate` + `DC_ESCALATE_OPTIONS` (Layer 1).

### Programmatic callers — `--auto` bypass (A7)

A trusted, already-structured caller can skip the interactive pre-check + `(a)/(b)` gate with `--auto`. A call is admitted only when **all** of: the caller is on the trusted-caller whitelist (`DC_AUTO_CALLER_WHITELIST`), an explicit `--auto` flag is present, and the payload is a schema-valid structured target that clears the floor (axes may be omitted — they're filled from the artifact type's registry default, groundedness always included). Any miss → a **structured error** (the run is refused, never silently downgraded to interactive). A bypassed run is **detectable after the fact**: its marker carries `auto_caller` and **omits `pre_check_layer`** — the missing field is the signal that the pre-check was skipped. The `--auto` path runs no AI and no conflict detector. *Enforced by:* `validate_auto_request` + `_dc_auto_fill_axes` + `DC_AUTO_CALLER_WHITELIST` (Layer 1; the whitelist ships empty — an operator seeds it once a caller is vetted). *Note:* the registry default axes per type are mirrored in `double-check-allocation.md` (Layer 2).

### When named sources disagree — source-conflict assist modes (UX4)

If the interactive pre-check detects a conflict between the sources you named, the run pauses and offers two ways to resolve it — **you decide, the skill never auto-resolves**:

- **(1) Compare 1:1 via `/challenge`** — a head-to-head of the conflicting sources.
- **(2) Get an AI recommendation via `/recommend`**.

The run proceeds only after your pick. (`--auto` runs carry no conflict detector, so this never fires for them.) *Enforced by:* the Step 2d source-conflict branch (Layer 1).

### After a run — verdict + pointer, never a dump (UX3)

A finished run prints the **verdict** plus a **one-step pointer** to its audit artifact (the source's forward Obsidian wikilink, or the read-only audit-index entry keyed by path+hash; in this skill-text mode, the Step-2 artifact file path). The skill **does not dump the marker inline** — open the pointer to see what was checked against what, by which model, at what cost, and each claim Supported / Not-Supported with its named proof source. *Backed by:* the S1 co-located-marker + forward-wikilink reachability (`resolve_audit_location` + the audit wikilink, Layer 1).

---

## Constraints

- **Checkers are `readonly-checker` agents** — each dispatched with isolated context, custom system prompt, and no shared conversation history with the producer. Their grant is `Read, Grep, Glob` (no shell) — read-only is enforced by the framework tool grant, not prose. Substitutes for the CLI subprocess pattern, which fails inside active sessions. (`factcheck-convergence.md` §2 — isolation property preserved; main-session use only; skill cannot be invoked from inside a subagent.)
- **Trust Hierarchy exception** — convergence orchestration lives in skill text, not Python code, because the engine cannot call the Agent tool. Documented in Thoughts/double-check-skill-subprocess_THOUGHT.md.
- **§7 R\<N\>.md marker exception** — artifact markers are not written by this skill-text implementation; the engine writer is unreachable in-session. Second documented exception alongside Trust Hierarchy.
- **Concurrency** — Step 2's unique filename (unchanged) provides per-invocation uniqueness. Engine-level LOCKED state does not apply in skill-text mode.
- **Artifact = file only** — write recommendation text to a file using the Write tool before dispatching checkers. Never pass inline.
- **Scope_addendum = target framing only** — the `--against` argument provides validation context, not producer reasoning.
- **No active topic required** — works in any session; unique artifact filename from Step 2 prevents collision.
- **Scope-coverage requires Opus** — the advisory scope-coverage section (Step 3 Opus prompt + Step 4 output) is gated on `--opus ≥ 1`. Sonnet-only invocations (`--opus 0`) receive structural claim extraction via step 3 but no scope-coverage assessment. Scope-coverage is a judgment task appropriate for Opus.
- **Obligation ledger (code-enforced)** — a DOUBLECHECK artifact Write/Edit seeds a per-artifact discharge obligation (`check-dc-obligation-seed.sh`, PostToolUse). A Stop hook (`check-dc-obligation-stop.sh`) blocks session end when an obligation is outstanding and the co-located `.claim-runs.md` shows no discharge record — i.e., the Deep claim-identification engine did not run AND no concrete engine-failure/user-ack fallback reason was logged. A checker Agent dispatch is blocked (`check-dc-checker-dispatch.sh`, PostToolUse) when no obligation has been seeded for the current artifact.

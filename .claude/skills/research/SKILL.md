---
name: research
description: Runs the one research methodology — scope framing and approval, a registered pipeline manifest, evidence-based findings written to a _RESEARCH.md as they land, fact-check, and a claims register — whoever calls it. Use when the operator types /research, or when /clarification Step 6 or /work-decode reaches its research step and calls this skill with a predefined scope. Fires only on an explicit call; phrasings like "what do you think" or "pros and cons" get an OFFER to run it, never a run. Main-session use only (it holds the shell and asks the operator); the shell-less `research` agent is its adapter, never its entry point.
---

# /research — one skill, owned by the harness, reached by every caller

This is the entry point. Two systems used to answer to this name — a project-side dispatcher that spawned a shell-less subagent, and the framing flow that lived here as Python with no command of its own. As of research-entry-point-enforcement S2 there is one: **this skill owns the name and the methodology**; the `research` agent at `~/.claude/agents/research.md` survives as an **adapter** that a framed, registered run dispatches, and nothing else reaches it (the look-alike `Agent` dispatch is closed by the shadow map in S3).

**On start, write the skill marker — first, before anything else:**

```bash
python3 ${KIT_HOOKS_DIR}/skill_marker.py write research SESSION_ID
```

`SESSION_ID` above is a placeholder the model substitutes with the real session id, matching the bare form the sibling marker-writing skills `clarification` and `clarification-v2` use (`close/SKILL.md` uses `<SESSION_ID>` instead). The real id must be substituted before this line reaches bash, and the two ways of forgetting to fail differently: **unquoted** and unset, the shell drops the word entirely, so `skill_marker.py` sees only two arguments, its `len(argv) < 3` guard fires, and it exits 2 writing no marker; **quoted** (`"$SESSION_ID"`) and unset, the shell instead passes an empty string, the guard does not fire, and a marker is written under an empty session id that `check-skill-marker.sh` then fails to find at dispatch. `close/SKILL.md`'s `{SESSION_ID}` note is a related precedent, not the same failure — both surfaces refuse an unsubstituted id, but on different grounds: `close-block` on an explicit emptiness check, `skill_marker.py`'s unquoted case on an argument-count guard. The quoted-empty case that slips through belongs to the marker write alone — a marker is written under an empty session id because neither kind of guard catches it there.

`check-skill-marker.sh` reads it; once the shadow map carries `research` (S3), an `Agent` dispatch of `subagent_type: research` without this marker is blocked with exit 2. The marker is what distinguishes the canonical run from a hand-rolled one.

## Arguments — how a caller reaches this skill

| Form | Who | What it carries |
|---|---|---|
| `/research <question>` | the operator, typed | the question; the scope exchange runs (below) |
| `/research --from <thought-file> [--caller <skill>] [--cycle-id <id>] [--research-file <path>] [--scope-record <json>]` | a calling skill on the operator's behalf, or the operator naming a spine | a **predefined scope** read from the named file; no scope exchange, the scope read is DISPLAYED before the run and recorded with the argument that named it (U3, explicit-argument provenance) |

**Registration identity — two arms, not one (MAJOR 6a: this paragraph used to say "stated once", contradicted by the table 90 lines below it).** The run registers `r0_intake` itself, but WHEN depends on which route reached this skill — see the full table under *-2. Check Existing Research* below, and do not stop at this paragraph alone: a **predefined-scope or programmatic** call (`--from <file>`, or any programmatic caller) registers **here, at Step -2**, because its approval artifact already exists by then; a **typed** `/research` call with no predefined scope registers **later**, after the Step-3 approval, in `~/.claude/rules/research-scope-framing.md` Step 4. Registering a typed run at Step -2 has no artifact yet and deadlocks it — see Step -2's warning below for why. Whichever arm applies: `caller_skill` is the *calling* skill when one is named (`/clarification`, `/work-decode`) and `/research` for a typed call; `downstream_tool` is this skill's path, `~/.claude/skills/research/SKILL.md`, in every case; `cycle_id` is the caller's when given (`/work-decode` passes `wd-<brief>-r<i>` per row), else `default`. A caller that used to register intake itself must not — two registrations for one cycle collide in `cmd_advance`. `/clarification` Step 6 and `/work-decode` S3 were retired of their own `r0_intake` blocks in the same change that created this file.

**The adapter must exist, and this skill refuses by name if it does not.** Before dispatching the `research` agent, check `~/.claude/agents/research.md` is present; if it is not, stop and say so — a run that "dispatches" a missing adapter reports nothing and looks complete.

**Check THIS cycle's own approval before dispatching it — the session-level check is not enough (MAJOR 3 deviation).** `research-scope-gate.sh` reads `resolve_scope_status`, which is a coarse SESSION-level gate: it answers `approved` the moment ANY cycle in the session is approved, because it cannot tell which cycle the Bash/Agent/WebSearch/WebFetch call it is gating belongs to (a session routinely holds several concurrent cycles — one per `/work-decode` Part-B row, or `default`/`de`/`ru` on the multi-language route). A session-level `approved` therefore does NOT mean this cycle's research may run. Before dispatching a cycle's research (the `research` agent, or the declared-source reader in **Reading the person's own declared sources** below), check that ONE cycle's own approval:

```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py cycle-scope-status $SESSION_ID --cycle-id "<this run's cycle_id>"
```

It never consults a sibling cycle — that is the no-inheritance rule (channel 3: "one approved cycle does not approve another"), computed here because this is the one place that actually knows which cycle is about to read. A `decision` other than `approved` refuses **that cycle by name**, with the `reason` the verb returns — never the whole session, and never silently falling back to the session-level check having already passed. `wd-b-r1` and `wd-b-r2` each pass this on their own artifact; `wd-b-r3` fails it and is the only one refused, even though the session itself resolves `approved`.

**Two different things happen when this check fails, and they carry two different labels (research-entry-point-enforcement S4 round-2, ITEM 3).** The declared-source READ (**Reading the person's own declared sources** below) is now genuinely **ENFORCED**: `declared_read._resolve_scope_from_cycle` calls `resolve_cycle_scope_status` itself, in code, and refuses that cycle by name — a model cannot skip this check and still get the person's declared sources back, because the reader will not return them. The `research` agent DISPATCH, by contrast, remains **FOLLOWED, NOT ENFORCED**: the bash command above is an instruction to the model, not a code gate on the `Agent` tool call — nothing stops a model from spawning `research` for an unapproved cycle anyway. This is the same distinction this harness already draws in `output-security.md` (a boundary is named when it is followed rather than enforced, not silently conflated with one that is).

**Bundled references (one hop, all beside this file):** `research-sources.md` (the citation Marker Contract — the drift-guarded mirror pinned by `CITATION_MARKER_REFERENCE_PATH` in `${KIT_HOOKS_DIR}/_factcheck_engine.py`), `research-de.md` and `research-ru.md` (per-language source lists that inherit this process and say so), `research-subagent.md` (adapter dispatch: model strategy, output tracking, report format), `research-initiative.md` (Initiative / BMC extension). The two project-scoped routings named under *Domain Routing* below stay in the `Projects` repository and resolve only inside it.

---

# Decision Support (English)

Help evaluate options and provide evidence-based recommendations.

**Scope framing for direct `/research`:** See `~/.claude/rules/research-scope-framing.md` for the routing prompt, draft-scope adapter, Step 2.5 internal fact-check, **Step 2.6 source selection + bounds**, approval bundle, and post-approval dispatch. Step 2.6 (research-source-adapters S4) is where a person picks which of their own sources to read and how far into each; it runs on Ninja / Deep / Ultra Deep **and, since S7, Internal knowledge base**, and the declaration it assembles is approved inside the **existing** Step-3 bundle rather than at a gate of its own. **Autonomous** does not run it and reads exactly as it did before — its exemption rests on a different and still-live reason (it asks the person for nothing after the question), which S7 deliberately left alone. **Internal KB's exemption expired with S7**: it rested on "its own source classes have no reader yet", and S7 shipped those readers, so that route now assembles a declaration and reads against it — the change design-A24 asks for, since it was the one path that read without anyone having approved anything. The per-path table in that step states which paths run the step and which share the bundle, and they are still different sets. **Which classes are selectable depends on the route as well as on what has shipped** — and as of 2026-09-11 (Q26) all five registered kinds are selectable on every EXTERNAL route: `code`, `web`, `linear`, `knowledge_library` and `document_folder` on Ninja / Deep / Ultra Deep / Autonomous; the Internal-KB route still holds its own list to `knowledge_library` + `document_folder`, which is what keeps its `internal-only` source-tier true. That is what makes the locked promise of "any combination of sources in one run" achievable: a person can now span their own code and their own library in a single declaration. The RULE S7 was thought to have introduced — "a class is offered on a route only where a reader for it exists" — was itself the defect and is retired; the canonical statement is `source_picker.is_selectable`, which says the route set expresses each class's **source-tier policy** (which routes are WILLING to read it), not where a reader exists. Readers are route-neutral: `declared_read._adapter_for` dispatches on kind alone. *(This sentence asserted that retired rule as "unchanged" until 2026-09-13 — the sixth copy of it, in a file the slice itself edited, caught by an independent shipped-check rather than by the author.)* The consequence of that premise being wrong: the two on-disk classes were readable on the open-web routes all along while still being refused there. *(This sentence previously read "`code` and `web` on the three open-web routes, and `knowledge_library` + `document_folder` on the Internal-KB route", which was wrong in two further ways even before Q26: it omitted `linear`, and it called the external set three routes when it is four. Corrected in place rather than silently.)* **That exception is retired — `code` now has a production driver, and the rule above describes every selectable class with no carve-out.** From S3 until this slice, `code` was offered on the three open-web routes and `CodeBaseAdapter` was constructed only in tests, so a declared repository was probed, approved, and then never read. `~/.claude/skills/research/declared_read.py` is the reader that closes it; see **Step 4** below for where the run invokes it. **Offerability no longer depends on someone remembering this**: a class is offered only when a production driver for it exists at all, derived at render time from `kind_reachability.KNOWN_UNREACHABLE`, so the next class registered without a reader cannot repeat this quietly. Classes with no reader anywhere yet are shown as not-yet-available with a reason and become selectable as their slices land. All five paths — Ninja, Deep, Ultra Deep, Internal knowledge base, and Autonomous — are wired end-to-end as of slice S6 (`Thoughts/research-scope-framing-ui_THOUGHT.md`). Internal KB synthesizes **through the admission port** from its approved declaration — the knowledge-library folders the project's `research_library_folders:` key names, the topic-folder `_RESEARCH` files, and any document folder the person named — still without calling `r0_intake` (it opens no manifest cycle, so its declaration is carried in-process rather than on a payload). A path the declaration does not cover comes back **refused by name, with its reason**, rather than quietly missing. The topic's CLAUDE.md is walked up to as a *pointer* to where documents live, and is never cited — the marker pair that used to cite it is retired; see `research-sources.md`. Verification is engine-managed `fc_cycles:` frontmatter. **The `ScopeDraftPort` adapter class (`~/.claude/skills/research/scope_draft_adapter_claude.py`) was authored in S5, but had no production caller — its only construction site was a test fixture — until research-entry-point-enforcement S4 (MAJOR 6b: this sentence used to say "graduated in S5", contradicting `research-scope-framing.md`'s own account of the same gap).** S4 fixed the CLI entry point's `sys.path` ordering bug (the import it needed sat below the fix that made it importable, so every real invocation crashed with `ModuleNotFoundError` before printing anything) and wired the CLI into `research-scope-framing.md` Step 2 as this skill's production caller — the CLI is reachable now, and a well-formed intake gets exit 0 and valid JSON out. **Reachable is not the same as live**, though: the default wire (`live_model_invoker`) is now a `claude --print` subprocess call, not the old Anthropic-SDK wire — repointed and reachable (exit 0 on a well-formed call), but **not reliably clean JSON**: an 11-call live measurement (round-6 fix) found 1 non-JSON reply, which the degraded `{{error_drafter_failed}}` path handles — and it is **not yet proven by a real end-to-end Step 2 run** inside a live `/research`, and standing alongside an **unresolved contradiction**: this repository also documents `claude --print` failing in-session under `CLAUDECODE=1`, while every probe behind this fix succeeded with that same variable set. See `research-scope-framing.md` Step 2 for the full account, including both caveats and the drafter's `{"kind": "error", ...}` contract, which Step 2 surfaces to the operator as `{{error_drafter_failed}}`. Locale-native search-terms sub-pass (`~/.claude/skills/research/locale_native_subpass.py`) ships in S6.

---

## Trigger

**The hard trigger is an explicit `/research` call** — typed by the operator, or invoked by a calling skill with `--from`. That, and only that, starts a run.

**These phrasings OFFER a run and never start one:**
- assess, evaluate, compare, analyze options
- recommend, advise, suggest
- "which option", "what do you think", "pros/cons"
- research, find examples, look up (as a bare question rather than a `/research` call)

On any of them, answer what you can from knowledge and ask: *"Want me to run `/research` on this — it will frame the scope with you first?"* Start a run only on a yes, and then start it as `/research <question>` so it takes the front door. This is Guiding Policy channel 1 of the research-entry-point-enforcement spine (an explicit call is the hard trigger; phrasings offer) and reversal R1 (an invocation is no longer its own approval).

- **"deep research"** — an explicit call that selects the Deep tier with Intent Validation (Phase -1)

---

## Research Depth Tiers

| Tier | Trigger | Scope |
|------|---------|-------|
| Standard | Default | WebSearch + WebFetch + the adapter. Full pipeline. |
| Deep | "deep research", "research thoroughly", high-stakes | Standard + Chrome Google Search, multiple reformulations, 3+ sources per claim, Chrome fallback for blocked sites |

Both tiers require: source quotes, attribution, `_RESEARCH.md` output.

---

## Adapter Mode

The shell-less `research` agent (`~/.claude/agents/research.md`) does the reading a framed, registered run assigns it and **returns** its findings; it does not write them. This skill records each returned finding through the recorder (§4, "Recording each finding as it lands") — the adapter has no shell to call it with. Dispatch it once per angle rather than once for the whole scope, so findings reach disk after every angle instead of only at the end. See `research-subagent.md` (bundled) for model strategy, output tracking, report format, and constraints. It is dispatched by this skill after intake; it is not a second way in.

---

## Quick Answer + Research Offer

For factual questions:
1. **Answer from knowledge first** — give quick response
2. **Offer:** "Want me to research this for current/verified sources?"
3. If yes — re-enter the routing prompt from `~/.claude/rules/research-scope-framing.md` Step 1 with options limited to **{Ninja, Deep, Autonomous}** (Internal knowledge base and Ultra Deep are omitted from this re-entry — Internal KB has no external search to run and would deadlock a Quick-Answer pivot; Ultra Deep is a deferred adjacent-points superset of Deep that the user is unlikely to want from a one-line Quick-Answer prompt).

---

## Process

### Which surface is reading this — the shell steps are not universal

This file is read by **two surfaces with different capabilities**, and nine steps below only work on one of them.

- **This skill, in a shell-holding session** — direct `/research` (the scope-framing flow in `~/.claude/rules/research-scope-framing.md`), `/clarification` Step 6 and `/work-decode` S3 calling it with `--from`. Has Bash. Runs everything here.
- **The `research` adapter** (`~/.claude/agents/research.md`), whose grant is `Read, WebSearch, WebFetch, Glob, Grep` — **no Bash, no Write, no Edit**. It returns findings for this surface to record through the recorder (§4); it does not write the research file or the claims register itself.

Every ` ```bash ` block below runs **only on the shell-holding surface**: the six pipeline checkpoints (§-2, §2, §5.5, §7.5, §8.5, §9), the declared-source read in §4, the finding recorder in §4, and the merge-report step in Closing. The adapter's part in the recorder is to RETURN its findings so this surface can record them — see §4.

**If you have no shell: skip those steps, and say by name which ones you skipped.** A report that silently omits them reads as complete when it is not. Two consequences are worth knowing rather than discovering:

- **A run that skipped the pipeline checkpoints will be blocked at close.** The Stop gate refuses a manifest whose required checkpoints are missing, so the skip surfaces there rather than passing.
- **The declared-source read has nothing to read on that surface anyway.** The declaration is produced by Step 2.6, which needs an interactive prompt a subagent cannot issue, and it is persisted only on the manifest cycle. The reader exits with a stated reason when no approved declaration exists — so this is a structural absence, not a step you are missing out on.

**An inability to run a step is never permission to improvise past it.** That matters most in §4: reading a declared repository with `Read`/`Glob`/`Grep` instead of through the reader produces findings whose pins were minted by hand rather than by the port, which is precisely what the pins exist to rule out.

### -2. Check Existing Research

Before any new research:
1. Search `Thoughts/*_RESEARCH.md` and `*/Thoughts/*_RESEARCH.md` for topic keywords
2. Search project folders for matching `*_RESEARCH.md`
3. If found: assess if it answers the question (sufficient — use it; partial — build on it; stale >30 days — offer refresh)
4. If not found — proceed to Phase -1

**Start the pipeline manifest (r0_intake) — this skill registers it, for every caller. WHEN it registers depends on whether an approval artifact exists yet.**

Two routes reach this skill and they register at different moments. This is one rule with two arms, not two mechanisms — the manifest is always opened by the same call, at the first moment the run can carry its approval artifact:

| Route | Register at | Why |
|---|---|---|
| **Predefined scope** — `--from <file>`, or any programmatic caller (`/clarification`, `/work-decode`) | **here, Step -2** | The artifact already exists: the file named by `--from`, or the caller's own confirmed goal. The payload can carry it on the first call. |
| **Typed `/research`** — no `--from`, no predefined scope | **after the Step-3 approval**, in `~/.claude/rules/research-scope-framing.md` Step 4 | There is no artifact yet. The operator has not been asked. |

**Registering a typed run here would deadlock it — do not do it.** The manifest's existence is what `pre_plan_gates._classify_intake_source` reads to return `research`, which removes this session's exemption at `research-scope-gate.sh`. A typed run has no approval artifact at Step -2, so `r1_scope_approved` stays `false` (S4: a caller's name is not approval), and the gate then blocks `WebSearch`, `WebFetch`, the `research` `Agent` dispatch, and the two Bash shapes it actually gates (a python invocation of a browser-automation driver, or a write-redirect into `*_RESEARCH*.md`) for the rest of the session — **not every `Bash` call** (MINOR 2, round-3 fix: `research-scope-gate.sh:39-49` `exit 0`s an ordinary Bash command with neither shape; measured directly against the live hook — this skill's own `r1_scope` advance below is a plain `python3` call matching neither and is NOT blocked). `r0_intake` cannot be called twice for one cycle (`research_pipeline.py` raises `Already completed`), so there is no second chance to fix it: exactly one site may register, and for a typed run that site is after approval.

**This rule is followed, not enforced (MAJOR 6d).** Nothing in code stops a typed run from registering at Step -2 anyway — `cmd_advance` accepts whichever payload it is given, and `_classify_intake_source` flips the session's classification the moment the manifest file exists, regardless of which route put it there. Naming that plainly here, per this harness's own `output-security.md` precedent (a boundary must state when it is followed rather than enforced, not leave the reader to discover the gap): the two-arm rule above is prose discipline this skill and `research-scope-framing.md` both follow, and its only backstop is the deadlock itself — an AI that violates it strands the run rather than silently getting away with it. Recovery from a stranded cycle is `python3 ${KIT_HOOKS_DIR}/research_pipeline.py reset <session-id> [--cycle-id <id>]`.

**The payload goes through a file, not a shell literal (MAJOR 1, round-3 fix).** The scope this payload carries is the operator's own English (angles, focused questions, or a predefined scope read from a file) — a single-quoted shell literal cannot safely carry it (an apostrophe, e.g. "what's" or "don't", breaks the quoting at execution time), and a multi-line command containing "for", "while", "done", "fi" or "esac" as a whole word — ordinary in English research prose — is refused outright by `sanitize-bash.sh` pattern 9. Write the payload with the **Write tool** (never a shell redirect or heredoc — `~/.claude/rules/subagent-tools.md`), then invoke the CLI as a single line with `--payload-file`.

Write a scratch payload file at a **fixed name, never one derived from the
topic** (e.g. `research_pipeline_payload.json`, used verbatim — the file's
*content* carries the topic slug; its *name* must not, or the slug reaches
the command line by another route through `--payload-file`'s own path
argument). With:

```json
{"research_file_path": "Thoughts/[topic]_RESEARCH.md",
 "caller_skill": "[/research | the calling skill passed via --caller]",
 "downstream_tool": "~/.claude/skills/research/SKILL.md",
 "caller_session_id": "[the value of $SESSION_ID]",
 "user_approved_scope": true,
 "scope": {"[the predefined scope read from the named file, OR the scope confirmed with the operator this call]": "..."},
 "scope_provenance": "[git-head | explicit-argument | fresh-answer]",
 "scope_source_ref": "[git-head/explicit-argument only — the argument that named it, verbatim]"}
```

git-head / explicit-argument: the scope came from a file, so name it in `scope_source_ref` (required — MINOR 8). fresh-answer (the "Neither applies" case below): the scope came from confirming with the operator THIS call, not from a file, so there is nothing to name — omit `scope_source_ref`.

Then, on ONE line:

```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r0_intake --payload-file "<path to the file just written>" --cycle-id "[default | the --cycle-id the caller passed]"
```

**The approval fields are not optional decoration — they are what approves the cycle** (S4/A4). `caller_skill` alone records who called and approves nothing. Pick `scope_provenance` by how the scope was obtained, and never by which is easier to claim:

- **`git-head`** — the file was committed before this session began, so anyone can re-check it after the fact.
- **`explicit-argument`** — the file was named explicitly on this call (`--from <file>`). Put that argument in `scope_source_ref` verbatim. This is the `/clarification` Step-6 case: the spine is uncommitted in the session writing it, so the `git-head` branch cannot admit it.
- **Neither applies** — this caller has no committed file and no named argument to point at. **Confirm the scope with the operator FIRST, before calling `r0_intake` at all**, then register once with the confirmed scope as a `fresh-answer` artifact (`"user_approved_scope": true`, `"scope": <the confirmed scope>`, `"scope_provenance": "fresh-answer"`) — the same confirm-then-register ordering the typed route already uses (`research-scope-framing.md` Step 4), and for the same reason. **BLOCKER 2 — this paragraph used to say the opposite (register with no approval fields, confirm interactively afterward), and that ordering deadlocks the cycle:** `r1_scope_approved` is assigned nowhere but here, reachable only from `r0_intake`, and `r0_intake` cannot be called twice for one cycle (`research_pipeline.py` raises `Already completed`) — so once an intake has registered with no artifact, there is no second call through which the operator's later interactive confirmation can reach the manifest. Registering without an artifact strands the cycle: the manifest's mere existence removes this session's `research-scope-gate.sh` exemption regardless of whether the cycle went on to approve, so a no-artifact registration blocks `WebSearch`, `WebFetch`, the `research` `Agent` dispatch, and the two gated Bash shapes for the rest of the session (MINOR 2, round-3 fix — not every `Bash` call; see `research-scope-gate.sh:39-49`) with no way back in through this checkpoint. The only recovery is `python3 ${KIT_HOOKS_DIR}/research_pipeline.py reset <session-id> [--cycle-id <id>]`.
Replace `[topic]` with the actual topic (or use the `--research-file` the caller passed). When the caller passed `--scope-record`, add it as `"scope_record": <json>` — the intake hoists it onto the cycle exactly as before; the assembly's owner is unchanged, only its carrier.

**KNOWN OPEN GAP — `topic_slug` still has no carrier.** `PIPELINE_OVERRIDES` in `research_pipeline.py` is keyed on `"topic_slug"`, not `project_slug`, and this skill's argument surface (`--from`, `--caller`, `--cycle-id`, `--research-file`, `--scope-record`) carries no `--topic-slug` flag, so a project override cannot be engaged from this entry point. **S4 closed the intake-SITE half of the reconciliation this gap used to be carried on** (the table above), and it did NOT close this: the missing flag is an argument-surface gap, not a site question. It is unowned. **`skills/clarification/steps.md`'s matching note has been updated** to say so plainly ("S4 has landed and did NOT close this... unowned; do not keep pointing at S4"). **`skills/work-decode/SKILL.md`'s equivalent notes have NOT** (research-entry-point-enforcement S4 round-2, ITEM 8 — this sentence used to claim both were updated, which was false: only the first was). `work-decode/SKILL.md:122` still says the gap "is carried on the same intake-site reconciliation as `/clarification`'s equivalent gap... not resolved here" and `:129` still carries a forward reference ("the approval-artifact fields themselves land in S4") — both stale now that S4 has landed. `work-decode/SKILL.md` is out of this round's scope; that correction is outstanding and owned by S15.

### -1. Intent Validation (Non-Trivial Research Only)

**Auto-triggers when:** decision-dependent ("should I..."), multi-entity scope (3+ topics), embedded assumptions ("best", superlatives without criteria), unfamiliar domain, high-stakes, vague success criteria.

**Skip for:** fact retrieval, definitions, direct instructions, follow-ups to validated research.

**When triggered, ask before researching:**
1. What decision depends on this? (What will you DO differently?)
2. What would change your mind? (What finding would make you NOT proceed?)
3. What assumptions are embedded? (Hidden "should" or "best" needing criteria?)
4. What does a good answer look like? (Format, depth, must-answer questions)

**Scope Warning:** If the topic requires real-time data, paywalled sources, or authenticated access, warn upfront about reduced coverage.

### 0. Input Extraction (Complex Inputs)

**Trigger:** Documents, multi-condition requests, technical specs, medical reports.

Before searching, extract ALL items from source material into a checklist: topics/conditions to research, specific findings/details mentioned, relevant context factors, user questions to answer.

**Rule:** Research is NOT complete until all extracted items are covered.

**Medical documents:** Extract from all sections (Anamnese/History, Findings/Befund, Assessment/Beurteilung). Mark each item as CONFIRMED, RULED OUT, or SUSPECTED. Then run checkpoint — let user decide whether to research ruled-out items.

### 0.5. Extraction Checkpoint

Present extracted items to user. List topics, questions, and context factors. **Do NOT proceed until user confirms scope.** Prevents silent filtering.

### 1. Answer Mapping (Multi-Part Questions)

Map each user question to required output section. Every question must have a corresponding section in output.

| User Question Pattern | Required Output |
|----------------------|-----------------|
| "Is X possible?" | Explicit YES/NO with evidence |
| "Treatment options" | Treatment table with timeline |
| "Differences across X" | Comparison table |

### 2. Define Search Scope

Before searching, clarify: what specific information is needed, what source types would have it, and list 3-5 search term variations.

**The scope description goes through a file, not a shell literal.** `search_scope` is the operator's own English about what is being searched for, and this block's multi-line single-quoted literal carries the same hazard the typed `r0_intake` template used to (MAJOR 1) — reproduced directly: a scope description containing "for" ... "do" ("What should I look for and how do I compare vendors?") is refused outright by `sanitize-bash.sh` pattern 9 the moment the command spans more than one line, and an apostrophe ("what's") breaks the single-quoted literal at execution time regardless. Write the scope with the **Write tool**, to a **fixed filename, never one derived from the scope text itself** (e.g. `research_pipeline_r1_scope_payload.json`, used verbatim), then invoke the CLI as a single line with `--payload-file` — `advance` accepts it for every checkpoint, not only `r0_intake`.

```json
{"search_scope": "[brief scope description]"}
```

**Target the same cycle `r0_intake` registered.** Pass `--cycle-id` matching the caller's — omitting it always targets `default`, and a non-default cycle (`de`/`ru`, or a `/work-decode` per-row cycle) that registers intake and then advances with no `--cycle-id` fails with `Sequence violation: expected r0_intake, got r1_scope` because it is advancing `default`'s empty sequence instead of its own (MAJOR 1, research-entry-point-enforcement S4 round 5).

```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r1_scope --payload-file "<path to the file just written>" --cycle-id "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]"
```

### 3. Multilingual Search (When Requested)

**Trigger:** User requests multiple languages OR topic benefits from regional perspectives.

For each extracted topic, ensure at least one search per requested language. Output must include regional differences table and language/region tags on findings.

### 4. Research

**Search strategy:** Start with 2-3 query formulations. If first fails, reformulate with different terms/framing. Try at least 3 distinct approaches before concluding "not found". Report failed queries and ask user for input.

**Source priority:** Official docs > established publications > expert blogs > community forums > general web.

#### Reading the person's own declared sources

When Step 2.6 assembled a declaration and the person approved it at Step 3, read those sources **through the declared-source reader**, after framing and approval and before synthesis. Do not open a declared repository or folder with `Read`, `Glob` or a shell — that reads beside the port rather than through it, and produces findings with no re-openable pin behind them. **This holds even when you have no shell to run the reader with: an inability to run it is not permission to read beside it.** Report the step as not run (see the surface note under `## Process`).

**The framed question goes through a file, not a shell literal (MAJOR 2, round-4 fix).** Two independent hazards were reproduced against the live hook on the command this section used to ship. `PYTHONPATH=~/.claude/skills` — a tilde inside an assignment value — trips `sanitize-bash.sh`'s tilde-in-assignment pattern by itself, before the question is even considered; use `$HOME` instead. And the framed question is the operator's own English: a `--query "..."` value containing "for" ... "do" (ordinary research phrasing — "What should I look for and how do I compare vendors?") trips the control-flow-keyword pattern **on a single line, with no newline required** — collapsing the command to one line does not make it safe. Write the framed question with the **Write tool** to a scratch file at a **fixed name, never one derived from the question itself** (e.g. `research_query.txt`, used verbatim — the file's *content* is the question; its *name* must not also carry it, or the question reaches the command line by another route through `--query-file`'s own path argument), then pass it with `--query-file`, the same reasoning that produced `--payload-file`: the operator's own words must never reach a shell command line.

```bash
PYTHONPATH=$HOME/.claude/skills python3 -m research.declared_read read $SESSION_ID "<the _RESEARCH.md this run is writing>" --projects-root "<Projects root>" --query-file "<path to the scratch file holding the framed question>"
```

It prints the composed body — findings with their pins, and refusals — on stdout. Add `--json` if you need the readings and refusals separately rather than as prose.

**Do not reach for a `python3 - <<'PY'` heredoc here.** `hooks/sanitize-bash.sh` refuses that shape outright (exit 2, "python heredoc"), so an inline script is not a fallback when the command above is inconvenient — it simply does not run. A module CLI is the idiom this file already uses for its other seven code steps.

**You do not pass the declaration in.** It is read back off the manifest cycle that `r0_intake` hoisted it onto, which is the only place the approved record is persisted — there is no scope-record file to open. That is also what makes this read answer to the approval record rather than to a second copy assembled on the side.

Three things about what comes back, each of which must reach the person rather than being smoothed away:

- **Every finding drawn from a declared source carries a pin** — `[stated — code:<repo>@<rev>:<path>:<lines>]` for a repository, `[stated — local-file:<path>:<line>]` for a folder on disk, and (since S8) `[stated — linear:<workspace>@<version>:<issue>]` for a Linear issue, where the version is the issue's last-updated time. Cite what the reader returned; do not mint a pin by hand.
- **A source that could not be read comes back refused BY NAME with its reason**, and that refusal is already rendered into the body. Keep it there. A refusal dropped reaches a person as a finding that simply is not there, indistinguishable from the source having had nothing to say.
- **A run that reaches its reading limit stops and says so**, through the same refusal channel, naming the bound. Say plainly that the run stopped at a limit rather than finishing the source, and that narrowing where they pointed will get them more of what they care about. What *was* read is chosen by bearing on the framed question, not by alphabetical position — pass the question via `--query-file` so that holds.
- **Each finding carries a plain-words sentence saying how it can be checked** (S8), and there are deliberately two of them. A claim from a source anyone can re-open says so; a claim whose only surviving evidence is a copy this run stored says *that*, because the source needs a login the report's reader may not have and may have changed since. Keep both sentences as the reader emitted them — collapsing them into one turns a disclosure into a constant string that discloses nothing.

**A declared source this run cannot authorize is refused by name, and the rest of the run still happens** (S8). A person who declared their notes *and* Linear, with Linear not connected, gets their notes plus one named refusal — never an aborted run, and never a silently missing source. Surface that refusal in the body like any other.

**The ceiling, stated here as it is everywhere else this is described:** this is a contract on the run's read path, not on every read that is physically possible. Nothing gates the tool boundary — no `PreToolUse` hook covers `Read` for source paths — so the declaration is **followed, not enforced**. Gating it is design-A18, which is **withdrawn and routed to `/clarification --from`**. Never tell a person that reading outside their declaration is impossible, prevented, or blocked.

#### Research Rules

- Do not infer beyond what the source text explicitly states. Mark extrapolations with the inference marker and reduce confidence.
- After WebFetch, quote the supporting passage. No quote = downgrade to the own-assessment marker.
- Tool escalation: WebSearch — WebFetch — Chrome (if available) — ask user. Never silently skip blocked sources.
- If WebFetch fails, try alternative URLs from same search. After 3 failures for same topic, note limitation and proceed.
- PDFs: try WebFetch first; if garbled, ask user to download locally, then Read with `pages` parameter (max 20 per read, chunk larger PDFs).
- **Every finding reaches disk the moment it lands, whatever the number of topics** — through the recorder below, never held for synthesis. Context compaction cannot destroy what is already on disk. *(The old "<5 topics: write after synthesis" exception is gone as of S6.)*

#### Recording each finding as it lands

`r0_intake` already wrote the file's skeleton — the dated `## … — Topics` outline and the `## … — Findings` header — at the path the run declared, so the file exists before the first finding. **Each finding is then recorded by code, one call per finding:** the recorder appends the finding line under the Findings header and writes one entry to the claims register beside the file, in the same act. You do not write finding lines with `Write`/`Edit`, and you never write `_CLAIMS.md` yourself — the recorder is the only writer of findings and of register entries FOR RECORDED FINDINGS. A separate verified-harvest still exists in the tree for older, hand-written reports; it never re-lifts a line the recorder already wrote, because the recorder marks that line already-harvested in the same act.

Write the finding with the **Write tool** to a scratch file at a **fixed name** (e.g. `research_finding.json`, used verbatim — the finding's own words must never reach the command line, for the same reasons as `--payload-file` under §-2), then record it on ONE line:

```json
{"claim": "[one self-contained sentence]", "source": "[the URL, or a pin the declared-source reader returned]", "topic": "[the angle this answers — optional]", "marker": "stated"}
```

```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py record-finding $SESSION_ID --payload-file "<path to the file just written>" --cycle-id "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]"
```

- `marker` is `stated` for a direct quote or fact the source states, `paraphrased` for a paraphrase — the two forms `research-sources.md` defines. Inference and own-assessment lines are not findings and are not recorded; they belong to synthesis.
- **Only those four fields.** The file and the register are resolved from the cycle; a payload naming a path, slug or area is refused.
- Exit 0 = the finding, its register entry and its output-security stamp all landed. Exit 3 = the finding is on disk but its register entry OR its output-security stamp failed — the JSON says which, and why; surface it, do not retry blindly. Exit 1 = refused (unapproved cycle, bad payload, a filesystem error) with the reason in the JSON.
- A register entry is written at lifecycle `thought` — recorded, not yet established. Nothing here marks a claim verified.
- Each finding lands at the end of the LAST Findings section on the file, not at end-of-file — so it stays inside the section even when the file already carries a later section (a prior day's Synthesis, the insecure-input sources block). **Write synthesis and recommendation BELOW every recorded finding, never above them** — each register entry anchors to the line its finding landed on, and content inserted above those lines would shift every anchor out from under its row.
- **Output security:** these appends do not pass the write-time inspection the `Write`/`Edit` tools get. The recorder stamps an existing inspection record as degraded so the file's claims stay held from promotion until the next tool write of the file — your synthesis — re-inspects the whole file.

**When the `research` adapter does the reading** (see `## Adapter Mode`), it has no shell and cannot run this. Dispatch it **once per angle**, have it return its findings in the structured block its own file describes, and record each returned finding here before dispatching the next angle. That keeps what a killed session can lose to one angle's findings; it does not remove that window.

### 5. Source Scoring

| Tier | Criteria | Trust |
|------|----------|-------|
| **A** | DOI/PubMed ID, peer-reviewed (PMC, systematic reviews) | High (8-10) |
| **B** | .gov/.edu or named institution (Mayo Clinic, gov health sites) | High (7-9) |
| **C** | Named author with credentials, professional org (Physio-Pedia) | Medium-High (6-8) |
| **D** | Named author, no credentials (WebMD, general blogs) | Medium (5-7) |
| **E** | Anonymous, no editorial review (forums, promotional) | Low (3-5) |

State source tiers in output. Flag claims supported only by D/E sources. Note publication dates; flag sources >2 years old on time-sensitive topics.

### 5.5. Record Research Complete

After completing the research phase (before synthesis). **Same cycle as intake** — see the note under §2: omitting `--cycle-id` always targets `default` and strands a non-default cycle. **One line, not two (round-5 sweep finding):** the multi-line `\`-continued form of this command is refused outright by `sanitize-bash.sh` pattern 9 Form A the moment `--cycle-id`'s value contains "for", "while", "done", "fi" or "esac" as a whole word — a real hazard once `/work-decode`'s `wd-<brief>-r<i>` cycle ids are in play — so this template is one line.
```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r2_research '{"sources_count": N}' --cycle-id "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]"
```
Replace `N` with the number of sources consulted.

### 6. Synthesize + Fact-Check

**Synthesize:** Cross-reference findings, note agreements and conflicts, weight by tier. Derive rules/patterns — don't just list findings. State explicitly when information is thin.

**Competing hypotheses:** Before recommending, list 2-3 competing interpretations. Evaluate which evidence best supports. Reduces confirmation bias.

**Conflicts:** When sources disagree, show each position with its source tier. Weight toward higher tier; when same tier, report both and note disagreement.

**Fact-check pass:** For each key claim in synthesis: verify it matches the cited source quote, check it doesn't overstate what the source says. If a claim fails — downgrade to the own-assessment marker or correct to match source. Note corrections in output.

**Quantitative precision rule:** Every number (percentage, range, count, dose) must use the exact figure from the source quote — not a paraphrase. "56-73% increased risk" not "roughly doubles." If rounding, state "~" and keep within 5% of source value.

**Internal consistency pass:** After writing all sections, grep every repeated value (percentages, ranges, pathway counts, time windows) and verify they match across all mentions. Fix mismatches before delivering. This prevents cascading inconsistencies from mid-writing edits.

### 7. Confidence Check (Loop)

After synthesis, list 0-3 specific concerns:

| Concerns | Action |
|----------|--------|
| 0 | Proceed to recommend |
| 1 | Proceed, state the concern |
| 2 | **LOOP:** research the specific concerns |
| 3 | **LOOP:** ask user for direction OR report "insufficient data" |

**Loop:** Target search for concerns — re-synthesize — re-assess. Max 2 additional rounds. If still 3 concerns after 3 passes, proceed with explicit caveats.

**Unresolved topics:** If after 3 rounds a topic has only D/E sources or none, report as "Unresolved — insufficient evidence." Do not fill gaps with plausible-sounding content.

**Methodology self-check:** Am I searching with confirmation bias? Are terms too narrow/broad? Did I miss an obvious source type? Adjust before next loop.

### 7.5. Record Synthesis Complete

After completing synthesis. **Same cycle as intake** — see the note under §2. One line, not two — see the note under §5.5.
```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r3_synthesis '{"claims_count": N}' --cycle-id "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]"
```
Replace `N` with the number of source-backed claims in the synthesis.

### 8. Completion Check

Before delivering, verify: all extracted topics covered, all user questions answered, sources + quotes for all claims, certainty stated, regional differences included (if multilingual), unresolved topics flagged.

### 8.5. Convergence Fact-Check

After Completion Check passes, the factcheck engine runs automatically on `_RESEARCH.md` write (PostToolUse hook → `factcheck-research-file.sh` → engine). No manual loop needed — the shipped engine is the single fact-check path.

**Advance the pipeline checkpoint. Same cycle as intake** — see the note under §2. One line, not two — see the note under §5.5.
```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r4_factcheck '{"verdict": "engine_running"}' --cycle-id "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]"
```

**No skip path.** Every research report is fact-checked by the engine, which runs on every `_RESEARCH.md` write regardless of report size or Quick-Answer mode — record `{"verdict": "engine_running"}` above for **all** reports. There is no `skipped` record (the pipeline schema rejects `skipped:true`), and a report cannot close "green" without a real fact-check verdict: the Stop gate blocks a missing R-marker for any topic whose sequence includes r4_factcheck. Small reports therefore now wait for the async engine at close — wait ~30–60s and retry, or re-dispatch `pre_plan_gates.py factcheck-research $SESSION_ID <path>`. (Sanctioned exits still close: operator BYPASS with a reason, accepted-INCOMPLETE, and the Internal-KB / CV paths that legitimately skip external FC.)

**Claims registry:** nothing to do here. The register was written by the run as each finding landed (§4); do not hand-sync or hand-edit it. **The engine checkers do not assess `_CLAIMS.md` at all** (research-entry-point-enforcement S6 FIXER review, round 2) — a register row carries only a claim id and a `<research file>:<line>` locator, never the claim text (that lives in the ledger), so a checker reading the register alone has nothing to compare against a source. Register verification — that a locator resolves, that the line still exists, that the id is known — is a CODE job, owned by a later slice not yet built, never a model-judgment one asked of a checker here. The register does not maintain a Source Index — do not expect one, and do not ask a checker to validate one.

### 9. Recommend

1. **Assess** — analyze situation/options/findings
2. **Recommend** — clear recommendation
3. **Justify** — evidence and reasoning with source links, grouped by tier
4. **Confidence** — level with specific concerns

Adapt depth to context. Brief for simple questions, thorough for complex decisions.

**Close the pipeline. Same cycle as intake** — see the note under §2. One line, not two — see the note under §5.5.
```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r5_recommend '{"recommendation_written": true}' --cycle-id "[default for EN | de for DE | ru for RU | the --cycle-id the caller passed]"
```

---

## Medical/Health Research Template

**Trigger:** Health conditions, symptoms, treatments, medical documents.

**Disclaimer:** Always prefix with: *"This is not medical advice. For informational purposes only. Consult a healthcare professional before making medical decisions."*

**Source requirement:** Medical claims require Tier A or B sources. If only C-E exist, flag: "This claim lacks clinical/academic sourcing."

**Required sections:**
1. **Executive Summary** — table: Condition | Can It Heal? | Evidence + quote | Certainty
2. **Treatment & Timeline** — phases (Acute/Recovery/Maintenance) with timeline and expected outcome
3. **Therapies** — table: Therapy | How It Helps | Certainty | Source
4. **Activities** — three categories: SUPPORTIVE (helps healing), ALLOWED (neutral), NOT ALLOWED (harmful) — each as table with source
5. **Regional Differences** (if multilingual) — comparison table by region

---

## Rules

- Always provide source links — no link = no claim
- Always include source quote — if you cannot quote it, downgrade to the own-assessment marker.
- Always state source tier — transparency on evidence quality
- Never extrapolate beyond source text — if the source doesn't say it, don't present it as sourced
- Never fill gaps with plausible-sounding unsourced content — report as unresolved
- State assumptions explicitly; ask before guessing
- Complete Input Extraction before searching (complex inputs)
- Run Completion Check before delivering
- If user pushes back, explore why rather than defend

**Attribution:** Every factual claim must trace to a source with a citation + quote. If a URL is unavailable, state source name + date + why no link. If the claim is your own reasoning, mark it with the own-assessment marker. If it goes beyond what the source states, mark it with the inference marker and reduce confidence. **The exact marker forms live in `research-sources.md` (bundled beside this file) — use those, never an ad-hoc notation, and do not restate them here.**

---

## Marker Contract

Every factual claim inside `_RESEARCH.md` carries a **marker** naming where it came from — precisely enough that someone can re-open the source and check the claim later. The fact-check engine reads these markers to decide what is source text (exempt from antipattern checks) and what is AI-authored prose (subject to them).

**The contract is `research-sources.md`, bundled beside this file.** Read it before citing. It covers every kind of source — the public web and your own systems — in one place: which markers exist, which locator each one carries, verbatim vs paraphrase, quote-block language tags, and which markers have been retired.

**Do not restate the marker list here.** The authoritative list is `CITATION_MARKER_REGISTRY` in `${KIT_HOOKS_DIR}/_factcheck_engine.py`; `research-sources.md` and `~/.claude/rules/research-scope-framing.md` are mirrors checked against it by `check_citation_marker_drift`. A copy in this body would be a fourth copy that nothing compares — the exact drift the guard exists to prevent. Adding a way to cite a new kind of source is ONE change touching the registry and both mirrors together.

---

## Tool Capabilities

| Tool | What it does | Limitations |
|------|--------------|-------------|
| WebSearch | General web search | Returns results list, not full content |
| WebFetch | Fetch content from URL | No auth, no dynamic navigation |
| Chrome | Browse dynamic/JS-rendered pages | Slower, requires extension |
| Read | Read local files including PDFs | Max 20 pages per PDF read |

**Escalation:** WebSearch — WebFetch — Chrome — ask user.

**Cannot access:** Private Telegram channels (public channels: try WebFetch on t.me/channelname), Reddit/Quora (only via search results), paywalled content, authenticated services. When you can't find something, report what you tried so user can suggest better terms or share content directly.

---

## Closing

After research is complete:

1. **Summarize** findings (3-5 bullet points)
2. **Write synthesis and recommendation to the file the recorder has been writing** — the path this cycle declared at intake, not a location chosen now. Findings already reached disk under that path as they landed (§4); append synthesis/recommendation there, BELOW them, never above — each register row anchors to the line its finding landed on, and content added above those lines would shift every anchor. The table below is what governed WHICH path was declared at intake — it is not a second choice made here:

| Context | Location declared at intake |
|---------|----------|
| Project exists | `[Project]/[topic]_RESEARCH.md` |
| No project, personal | `Personal/Thoughts/[topic]_RESEARCH.md` |
| No project, work | `[Work]/Thoughts/[topic]_RESEARCH.md` |
| General (no project match) | `Thoughts/[topic]_RESEARCH.md` |

3. **Append the insecure-input sources section** to the saved `_RESEARCH.md` (output-security S6). Render the merged document with the code-owned renderer and write the result — never compose this section by hand:

```bash
python3 ${KIT_HOOKS_DIR}/output_security_record.py merge-report-section "<path to the _RESEARCH.md>"
```

It prints the whole document with the section replaced in place (or appended if absent), so re-running research updates the section rather than accumulating a second copy. Write that output back to the file.

The section lists the sources behind produced claims whose flags the operator **confirmed** as real violations. It is **informational only** — no source is blocked, and the section deliberately offers no block, mute or ignore control. It carries URLs and counts and never the offending text: echoing a flagged span into a `_RESEARCH.md` would re-enter the write-time inspection and could flag the very file documenting the flag. Confirmations made during *this* run appear from the *next* report onward, because this step runs before the operator has resolved this run's flags.

4. **State how the declared sources were bounded** (research-source-adapters S7; extended to code bases and to the run ceiling by code-source-driver-bounded-read). When the run read a person's own sources — a code base, the knowledge library, or a folder of documents — say plainly in the closing summary:

   > The research read through the source list you approved. A file outside it came back refused by name rather than quietly missing. That list is **followed, not enforced**: nothing prevents a read outside it, so treat this as a bounded reading of your sources rather than a guarantee about every read that could happen.

   Say it in your own words if you prefer, but **two things are obligatory and one is forbidden**. Obligatory: that the declaration is *followed* rather than *enforced*, and that a read outside it comes back refused by name. Forbidden: **any claim that reading outside the declaration is impossible, prevented, or blocked.** It is not — no hook covers the read tool for source paths, and gating that boundary is design-A18, which is withdrawn and routed to `/clarification --from`. A person told the boundary is enforced when it is only followed has been told the wrong thing, and that is worse than saying nothing.

   **And when the run stopped at its reading limit, say that too** — which source it was still working through, that it stopped at a limit rather than finishing, and that narrowing where they pointed gets them more of what they care about. A report that is simply shorter is indistinguishable from a smaller source.

   **One thing this run does NOT give them, stated rather than implied:** each cited file was read whole and pinned across its whole line range. The narrowed-excerpt half of design-A20 is unbuilt for every path-shaped source class, so a `1-N` pin is truthful about what was read and is not evidence that the run selected a passage within it.

5. **Log artifact pointer** to `~/.claude/logs/[hash]/outputs/` with frontmatter: timestamp, type (artifact), topic, file path, confidence, 3-5 bullet summary.

If the target directory doesn't exist, create it before writing. Diary references research, doesn't store it. Append to existing file if topic already exists. Thoughts/ uses simple `.md` files — no folder per topic.

---

## Bundled references

All beside this file, one hop deep (`skill-authoring.md` §5):

- `research-sources.md` — the citation Marker Contract (drift-guarded mirror of `CITATION_MARKER_REGISTRY`)
- `research-de.md` — German source list; inherits this process
- `research-ru.md` — Russian source list; inherits this process
- `research-subagent.md` — adapter dispatch: model strategy, output tracking, report format, constraints
- `research-initiative.md` — Initiative / Business Model Canvas extension

*(The previous `## Sibling Skills` index named `research/sources-de.md`, `research/sources-ru.md`, `research/extensions.md` and `challenge-mode-en.md`. It was pruned at the harness move rather than carried, because the index was stale in two different ways and neither is "the files are gone": `challenge-mode-en.md` genuinely does not exist, while the three `Projects/Skills/research/{sources-de,sources-ru,extensions}.md` files DO still exist — they are the pre-extraction originals, superseded by `research-de.md` / `research-ru.md` / `research-subagent.md` beside this file, and pointing a reader at them would send them to the older copy. Corrected 2026-09-22: this parenthetical first said "none of which existed", which was false of three of the four and was caught by an independent review of S2's shipped diff.)*

**Sync Rule:** When this skill is modified, check the bundled references for structural consistency. Language-specific and domain-specific content stays local to each.

---

## Claims Registry

**The recorder is the register's only writer for a recorded finding, and it is code.** Each finding recorded in §4 writes one entry to the `_CLAIMS.md` beside the research file, in the same act as the finding line, at the address the register addresser derives — so the register fills as the run goes and nobody fills it by hand afterwards. Do not create, sync or edit `_CLAIMS.md` yourself, and do not set a status on any entry: an entry is written at lifecycle `thought` (recorded, not yet established), and what a claim's status should be after fact-check is an open question owned by the "`claims_registry.py` implements no claim status" TODO item, not by this skill.

The tree also still carries a separate verified-harvest (triggered on a later, hand-verified `_RESEARCH.md` write) for reports predating this recorder. It is not a second writer of a recorded finding: the recorder marks each line it records as already-harvested, in the same act, so the harvest does not re-lift it into a second, contradictory row once the file's Status line later reads VERIFIED.

*(Retired at research-entry-point-enforcement S6: the hand-written procedure that stood here — build a Source Index, assign `R1…` ids, write grep-unique locators, set a `status` column after fact-check, re-check by claim id. It was the second writer Guiding Policy channel 5 rules out. The `.claude/rules/claims-registry.md` it pointed at is a `Projects`-only file and no longer governs this skill. The status vocabulary it named was neither implemented nor removed; see the item above.)*

---

## Override Contract

Domain skills (stock-research, company-research) may declare a narrowed pipeline subset via `pipeline_subset:` frontmatter. Code enforces the declared subset via `PIPELINE_OVERRIDES` in `research_pipeline.py`.

### Rules

1. A domain skill may only **omit** checkpoints from RESEARCH_SEQUENCE — it cannot add new ones outside the sequence.
2. The `pipeline_subset:` frontmatter value must match an entry in `research_pipeline.PIPELINE_OVERRIDES` exactly.
3. If a checkpoint is omitted in the override, it must not be recorded in the manifest — recording an unlisted checkpoint raises a subset-mismatch error.
4. Code is authoritative: if `pipeline_subset:` frontmatter and `PIPELINE_OVERRIDES` disagree, the code wins. Update the code first, then update the frontmatter.

### Current overrides

| Project slug | Omitted checkpoint | Reason |
|---|---|---|
| `your-project` | `r1_scope` | Replaced by DB Context Loading (Phase 0 in stock-research.md) |
| `CV` | `r3_synthesis`, `r4_factcheck` | Quick research only; no synthesis phase, and external fact-check is skipped on this path |

---

## Domain Routing

| Topic | Route To |
|-------|----------|
| Stock, ticker, trading, investment | `Personal/your-project/Skills/stock-research.md` — **project-scoped; resolves only inside the `Projects` repository** (`skill-location.md`: location IS the scope statement) |
| Company research for job application | `.claude/skills/company-research/SKILL.md` — **project-scoped; resolves only inside `Projects`** |
| Initiative / Business Model | `research-initiative.md` (bundled) |
| Generic / unclear | This skill |

Outside `Projects`, the two project-scoped routes are not reachable and this skill runs the generic methodology; say so rather than silently falling through.

---

## Extension

Project-specific skills may extend this with: domain sources, custom search strategies, trustworthiness criteria, freshness checks, confidence thresholds.

---

*Created: 2026-01-25 as `Projects/Skills/research-en.md`*
*Updated: 2026-03-07 — Compressed to <500 lines; extracted subagent mode and initiative mode to separate files*
*Moved to the harness: 2026-09-22 — research-entry-point-enforcement S2. The `Projects/Skills/research-*.md` files are committed pointer stubs; this file owns the name `/research`.*

# Research Scope Framing — Language-Neutral Flow

Canonical contract for the user-driven scope-framing UI that sits in front of
`/research`. The three sibling skills `Skills/research-en.md`,
`Skills/research-de.md`, and `Skills/research-ru.md` each carry a one-line
delegation entry pointing here; the language-neutral flow below + the
`## Localization Table` at the bottom of this file are the single source of
truth.

User-facing strings are written as double-curly placeholders (the body refers
to them by name; see the Localization Table at the bottom for the actual
EN / DE / RU columns). The conversation language picks the column. Engineering identifiers
(`r0_intake`, `r1_scope_approved`, `cycle_id`, `caller_skill`, ...) are
language-neutral and stay verbatim everywhere they appear in code, comments,
tests, frontmatter, and discovery/plan documents — never in user-facing text
(Q11).

A structural validator at `${KIT_HOOKS_DIR}/check-localization-table.sh` asserts
at commit/pre-push that (a) every placeholder used in this file has a row in
the table with all three language columns populated, and (b) every skill-file
delegation entry that mentions this file's basename resolves on disk.

---

## Consumed by

- `Skills/research-en.md` — delegates to this file for the scope-framing flow.
- `Skills/research-de.md` — delegates to this file for the scope-framing flow.
- `Skills/research-ru.md` — delegates to this file for the scope-framing flow.
- `${KIT_HOOKS_DIR}/check-localization-table.sh` — structural validator (runs at commit/pre-push).

---

## Step 1 — Routing prompt (5 paths)

When the user types `/research` directly, the skill issues exactly one
AskUserQuestion with the routing question `{{routing_question}}` and the five
named paths below. The label and description columns of the Localization Table
supply per-path text.

| Path | Label slot | Description slot | Source-tier | Approval-mode | Scope-breadth |
|---|---|---|---|---|---|
| Ninja | `{{path_label_ninja}}` | `{{path_desc_ninja}}` | standard external | user approves | narrow |
| Deep | `{{path_label_deep}}` | `{{path_desc_deep}}` | deeper external | user approves | narrow |
| Ultra Deep | `{{path_label_ultra_deep}}` | `{{path_desc_ultra_deep}}` | deeper external | user approves | broad |
| Internal knowledge base | `{{path_label_internal_kb}}` | `{{path_desc_internal_kb}}` | internal-only | user approves | narrow |
| Autonomous | `{{path_label_autonomous}}` | `{{path_desc_autonomous}}` | standard external | AI auto-approves | narrow |

Axes are orthogonal per Q15. The three deferred combinations (Autonomous +
Internal KB, Autonomous + broad, Internal KB + broad) are intentionally not
shipped today.

On a drafter failure at any subsequent step, the user is offered
`{{error_drafter_failed}}` with three options:
`{{retry_label}}` / `{{reroute_label}}` / `{{cancel_label}}` (UX Decision #2).

---

## Step 2 — Draft-scope generation (all 5 paths)

The AI generates one draft scope per session in a single autonomous turn
through the `ScopeDraftPort` adapter (built per Cockburn 4-step nano-increment
— `code_first_architecture.md:281-291`). The draft has six components: angles
(3–5 per topic), focused questions, search terms (or claim plan for Internal
KB), suggested depth, where-to-search, languages (with the conversation
language pre-checked).

**The production adapter is reachable as of S4 — call it, do not improvise a
draft.** The adapter class was authored in S5, but from then until S4 it had NO
production caller: its only construction site was a test fixture, so a run
reaching this step received a proposal from a test double or from nothing at
all (finding 36). S4 fixed a `sys.path` ordering bug in the CLI entry point
itself — the path fix that makes `research.scope_draft_port` importable used to
sit in the trailing `if __name__ == "__main__":` block, which runs only after
the whole module body (including the import it was meant to fix) has already
executed, so every real invocation crashed with `ModuleNotFoundError` before S4
and the "reachable" claim in this paragraph was not yet true when first
written. It is true now: the entry point is
`~/.claude/skills/research/scope_draft_adapter_claude.py`, which takes one JSON
intake on stdin and returns the outcome on stdout:

**The payload goes through a file, not a piped shell literal (MAJOR 1, round-3
fix).** `user_query` and `last_user_messages` carry the operator's own words
verbatim — a single-quoted shell literal cannot safely carry an apostrophe
("what's", "don't"), and a multi-line command containing "for", "while",
"done", "fi" or "esac" as a whole word — ordinary in English research prose —
is refused outright by `sanitize-bash.sh` pattern 9. Write the intake with the
**Write tool**, then invoke the CLI as a single line — it already reads
stdin, so this becomes a one-line redirect rather than a piped heredoc.

Write a scratch payload file at a **fixed name, never one derived from the
topic or the query text** (e.g. `scope_draft_payload.json`, used verbatim) —
a filename built from the operator's own words puts them back on the command
line by another route, through the `--payload-file`-style path argument
itself. With:

```json
{"routing_path": "[ninja|deep|ultra_deep|internal_kb|autonomous]",
 "user_query": "[the query verbatim]",
 "last_user_messages": ["[the last N user messages, oldest first]"],
 "conversation_language": "[en|de|ru]",
 "selected_languages": ["[the languages picked]"]}
```

Then, on ONE line:

```bash
python3 ~/.claude/skills/research/scope_draft_adapter_claude.py < "<path to the file just written>"
```

The reply is discriminated on `kind`: `"output"` carries the six components;
`"error"` carries a plain-English `reason` to surface verbatim as
`{{error_drafter_failed}}` with its three options. A drafter failure is a UX
branch, never a crash — the adapter does not raise across this boundary, so
there is one degraded path to handle and not two.

**Reachable is not the same as live — repointed, but not yet proven by a real
run.** research-entry-point-enforcement S4 round-2 (ITEM 1, operator decision)
repointed the default wire (`live_model_invoker`) away from the Anthropic SDK
— which required the `anthropic` package and `ANTHROPIC_API_KEY`, neither
present on this machine, so `draft()` caught the resulting `ImportError` on
every real call and every typed `/research` run deterministically degraded to
`{"kind": "error", "code": "invoker_failed"}` — to a `claude --print`
subprocess call, pinned to an explicit model (`claude-sonnet-5`) rather than
the CLI's own bare default. The model id was probed directly and confirmed
accepted (`claude --print --model claude-sonnet-5 "Reply with exactly: OK"` →
exit 0, the literal reply on stdout, ~8s); the full scope-draft prompt shape
was independently probed end-to-end and returned exit 0 with valid JSON on
stdout (~9.7s). **That is not the reliable behaviour of this wire — it is one
probe's outcome.** A round-6 fix (research-entry-point-enforcement S4)
measured 11 live invocations of the full scope-draft prompt end-to-end and
found 1 reply that was not valid JSON at all — `{"code": "parse_failed",
"reason": "The model's response was not valid JSON."}` — a preamble/trailing-
prose shape (no fence, prose on both sides of the object) that
`_try_parse_json` did not recover at the time. Do not describe this wire as
returning "clean, schema-conforming JSON" — it intermittently does not, on the
measured evidence, roughly 1 reply in 11. `_try_parse_json` now extracts the
first balanced JSON object from anywhere in the reply (not only a fenced or
bare one), which recovers the preamble/trailing-prose shape the 11-call
measurement hit; it does not make every reply valid JSON, and a genuinely
malformed or truncated reply still degrades to `{{error_drafter_failed}}`.
**What has NOT happened yet: a real Step 2 run, through this flow, in a live
session.** The operator's decision was explicit that this caveat stays until
that E2E run has happened — everything above is a probe of the wire in
isolation, not a proof that a typed `/research` scope draft reaches the
operator correctly end-to-end. Do not drop this paragraph on the strength of
the probes alone.

**An unresolved contradiction, stated rather than hidden.** This repository
also documents `claude --print` FAILING in-session with exit 1 under
`CLAUDECODE=1` (`Thoughts/double-check-skill-subprocess_THOUGHT.md`,
`async-answer-notification-20260710111825_RESEARCH.md`,
`clarification-v2-cutover-20260804001054_RESEARCH.md`, each citing external
issues for the failure). The probes behind this fix directly contradict that
record: `CLAUDECODE=1` was present throughout, and every probe — including
with the CLI's variadic-flag pitfall found and worked around (a `--` separator
is required before the prompt when `--tools`/`--allowedTools` precede it, or
the CLI silently swallows the prompt into the tools array and exits 1 with
"Input must be provided either through stdin or as a prompt argument") —
returned exit 0. The unverified hypothesis is that the historical failures
came from hook subprocesses or concurrent multi-checker fan-out, neither of
which this fix's probes tested (single sequential calls only). This is an
open risk, not a resolved one; do not read the probes above as having
explained away the prior record.

**Multi-language runs.** The shared scope core (angles, focused-questions,
depth-tier, where-to-search) is produced in the conversation language; the
search-terms slot is regenerated per selected language by a locale-native
sub-pass. The sub-pass returns `{terms: [...]}` on success or
`{terms: null, reason: ...}` on failure; a failed-language slot is marked
inline with `{{error_locale_native_failed}}` and the other languages still
ship (UX Decision #5).

---

## Step 2.5 — Internal fact-check (bounded context, all 5 paths)

The AI internally fact-checks the draft via `/double-check 1,1,1` against the
predicate: **"is the draft scope internally coherent and consistent with the
user's explicit ask (as expressed in the `/research` query and the last 5
user messages)?"** The checker sees only a bounded context window — produced
deterministically by `build_step25_fc_context()` reading the active session
JSONL — containing the `/research` query, the draft scope, and the last
N=5 user-role messages preceding the invocation; nothing else (Q7).

**What Step 2.5 catches:** drafts that contradict the explicit ask;
off-topic drafts; internally muddled drafts; drafts that cover unrelated
topics.

**What Step 2.5 does NOT catch:** hidden gaps in the user's knowledge state.
The bounded context contains evidence of intent, not knowledge state. Hidden
gaps are the user's responsibility via the Step 3 approval bundle.

**Rounds.** On DISCREPANCY the AI revises and re-checks; the round counter
increments per attempt. Session cap is `max_rounds` (per-kind editorial; see
`factcheck-convergence.md` §4). When `counter == max_rounds AND last_verdict
== DISCREPANCY`, Step 3 surfaces a 4th `{{escalate_label}}` option carrying
the final DISCREPANCY critique verbatim, truncated to ~200 characters
(Q3, UX Decision #3). The counter does NOT reset on Edit or Cancel-to-routing.

**Producer-never-verifies** is structural: the checker runs in an isolated
sub-process with the bounded artifact as its only input.

---

## Step 2.6 — Source selection + bounds (Ninja / Deep / Ultra Deep)

Framing runs to completion first; the source list opens only afterwards, seeded
with the framed question (UX Decision #1). Code:
`~/.claude/skills/research/source_picker.py`.

**Which paths run this step — and which do not.** The five routing paths do not
behave alike here, and two questions have *different* answers, so both are stated:

| Path | Runs Step 2.6 | Shares the Step-3 bundle | Declaration |
|---|---|---|---|
| Ninja | yes | yes | shown in the bundle, approved once |
| Deep | yes | yes | as Ninja |
| Ultra Deep | yes | superset variant (below) | as Ninja, alongside the adjacent-points checklist |
| Autonomous | **no** | **no bundle at all** | none |
| Internal knowledge base | **yes** (S7) | **yes** | shown in the bundle, approved once |

Two of these need saying plainly, because the sets differ:

- **Autonomous** asks the person for nothing after the question and has no approval
  bundle, so an interactive list would break its contract. It reads as it does today.
  Having the AI assemble and approve a declaration on someone's behalf is a real
  question against the "consent is never delegated silently" rule and is not settled
  here. **S7 deliberately did not touch this path**: its exemption rests on a
  different and still-live reason, so moving it would decide a question this slice
  has no mandate over.
- **Internal knowledge base** shares the Step-3 approval question **and, since S7,
  runs this step**. Its exemption used to rest on "its source classes have no reader
  yet, so a list here would offer nothing selectable" — and S7 shipped those readers
  (`knowledge_library` and `document_folder`), so **that premise has expired**.
  Leaving the exemption in place would have kept this route as the one path that
  reads without anyone having approved anything, which design-A24 names as the
  precedent not to follow.
  **The list this route is offered holds internal classes ONLY** — its source-tier
  in the routing table above is `internal-only`, and that stays true: the premise
  that expired is "its own classes have no reader", not "this route should read the
  open web". The restriction is code, not guidance — but the authority named here
  was the WRONG ONE, and is corrected 2026-09-11: list membership on this route is
  `source_picker.ROUTE_CLASS_RESTRICTION`, read by `classes_on_route`, **not**
  `SourceClassEntry.routes` enforced by `build_record`. The module itself draws
  exactly this distinction — `routes` answers "can this route READ this class",
  the map answers "is this class ON this route's list". The misattribution was
  harmless while the two authorities happened to agree for these classes; Q26
  ended that agreement by widening their `routes` to every route, leaving
  `ROUTE_CLASS_RESTRICTION` as the sole thing holding this route's list narrow.
  Naming `routes` here now would point at a field that restricts nothing.
  **Consequence for Step 3: the declaration section renders for a path that ran this
  step** — which now includes this one. The empty-state path below already handles a
  topic with nothing to offer, so the always-empty section the old rule feared has a
  home.

**The flow.**

1. **Spike gate first.** When the framed question reads as an investigation rather
   than research, say so with `{{spike_stop}}` and stop — the list does not open
   (UX Decision #9). A spike registers nothing.
2. **Show the list.** One row per source class, **nothing pre-ticked** — the person
   actively selects each source they want, one or several (UX Decision #2). A class
   that cannot be read **on this route** is shown as not-yet-available with its
   reason and cannot be selected. *(The three examples that stood here —
   `{{unavailable_knowledge_library}}`, `{{unavailable_document_folder}}`,
   `{{unavailable_linear}}` — are as of 2026-09-11 all **inert**: no route both
   offers those classes and cannot read them. The rule is unchanged and still
   binds any future class; it simply has no live example among the five
   registered kinds today. The slots are retained, not deleted, on web's
   precedent.)* A registered-but-unbuilt tracker slot reads
   `{{unavailable_slot_confluence}}` / `{{unavailable_slot_jira}}`. Ask with
   `{{source_selection_question}}`.
   **Availability is derived, never listed here — from THREE inputs:** a class is
   selectable exactly when its kind is registered in the shipped record, **and a
   production driver for it exists at all**, **and that class is readable on the
   route being run**. A later slice registering a kind, wiring its first driver, or
   wiring a reader on a further route flips the affected rows with no edit to this
   file.
   **Why driver presence is an input at all.** Registration answers "may this be
   declared" and the route set answers "is it read HERE"; neither answers "is it
   read ANYWHERE". While that gap stood, `code` was offered on three routes for
   four slices with no production reader behind it — a person picked their code
   base, gave a repository path, watched it get probed, approved it, and got
   research that never opened it. The input reads
   `kind_reachability.KNOWN_UNREACHABLE` at render time, so there is no list here
   to keep in step.
   **It is half of a pair, and stating it as the whole mechanism would overclaim.**
   That mapping holds kinds RECORDED as driverless; it is not a derivation, so a
   kind registered with no driver *and no recorded exemption* would still be
   offered. What stops that combination existing is the other half —
   `kind_reachability.check()` runs on the deploy path and fails a registered kind
   that has neither a driver nor a recorded exemption. The gate compels the record;
   this input consumes it. Neither alone is enough: the gate alone was already in
   place while `code` stayed offered for four slices with its exemption recorded
   and read by nobody.
   **What that input does and does not promise.** It is conservative in one
   direction only. A kind with no driver anywhere cannot be read by any route, so
   refusing to offer it is always right. The converse does not hold: that module
   attributes drivers per FILE, so a kind merely named in a file that drives some
   other kind reads as driven. Treat it as a floor on offerability, never as a
   guarantee that a read will happen — and never as containment, which it explicitly
   is not.
   **Why the route is a separate input.** Registering a kind makes it declarable
   everywhere; the route set says which routes are WILLING to read it — a
   source-tier policy, not a claim about where a reader exists. Readers are
   route-neutral (`declared_read._adapter_for` dispatches on kind alone), so a row
   is offered where the route is willing, which today withholds `code`/`web`/`linear`
   on the Internal-KB route even though a reader for them exists. The canonical
   statement is `source_picker.is_selectable`; this is a pointer to it, not a
   second copy.
   *(**The illustration that stood here is superseded — Q26, 2026-09-11 — and its
   premise was never true of this codebase.** It read: "One-input, a person on an
   open-web route could tick 'your knowledge library' and get a run where it was
   never opened — silence rather than refusal." The reader was never route-bound:
   `declared_read._adapter_for` dispatches by kind with no route conditioning, so
   an open-web route always could read a declared library, and both S7 classes now
   carry `routes=ROUTES`. The illustration was a symptom; the RULE behind it — "a
   class is offered only where a reader for it exists" — was the actual defect, and
   it is now stated correctly in exactly one place, `source_picker.is_selectable`.
   Go there rather than reasoning from this note.)*
   This is code, not guidance: the route set is a field on each catalogue
   entry (`source_picker.SourceClassEntry.routes`), `build_record` **refuses** to
   assemble a declaration for a class the calling route cannot read, and that
   refusal covers callers that never open this list at all.
   **Consequence for the two S7 classes — REVERSED 2026-09-11 (Q26), and by web's
   reason after all.** `knowledge_library` and `document_folder` are now selectable
   on **every** route, so their `{{unavailable_*}}` rows are **inert**, exactly as
   web's became: no route both offers these classes and cannot read them. The rows
   are retained rather than deleted, on the same precedent — a three-language row
   costs nothing to keep and a future route change could make it reachable again.

   This paragraph previously said the opposite, and said so correctly at the time:
   S7 wired their reader on the Internal-KB route only. That premise expired when
   the reader turned out to be route-NEUTRAL (`declared_read._adapter_for`
   dispatches by kind, with no route conditioning) and the open-web routes were
   wired to that same module — so the classes were readable on those routes while
   still being refused there. It is corrected in place rather than quietly, because
   prose asserting an availability the code does not have is the failure this topic
   keeps recording against itself.

   **What did NOT move:** the Internal-knowledge-base route still holds its list to
   internal classes only (`ROUTE_CLASS_RESTRICTION`, a separate authority left
   untouched), so its `internal-only` source-tier at `:46` stays TRUE. Admitting
   `code`/`web`/`linear` there is the other direction and is deliberately not done:
   that route opens no manifest cycle, so a declared `web` source would be read
   unbounded.
   **Web shipped in S6** (design-A29) and is selectable; it is bounded like any
   other class, and it is one of the classes that may be declared "whatever this
   source gives me", because the open web is what research reads by default and a
   class that could only ever be a narrow list would change what a person
   experiences. Its `{{unavailable_web}}` row is retained in the Localization
   Table below but is now **unreachable** — the derived rule above is what decides
   availability, so the row is inert rather than wrong, and deleting a
   three-language row would buy nothing.

   **`{{unavailable_linear}}` becomes inert at S8 (A9) — but only when the whole
   of S8 has landed, and by web's reason rather than by the S7 kinds'.**
   *(**The contrast this paragraph draws EXPIRED on 2026-09-11 — Q26.** It
   distinguished Linear's row from the two S7 rows on the ground that those two
   were still live. They are not: both classes now carry `routes=ROUTES`, so
   every `{{unavailable_*}}` row named anywhere in this section is inert, by
   web's reason, and the three cases have converged rather than staying distinct.
   The paragraph is kept because the REASONING it records — why a row goes inert,
   and why an inert row is retained rather than deleted — is still exactly right,
   and it is the reasoning Q26 applied. Only its claim about which rows are live
   is superseded. Corrected in place rather than rewritten, per this topic's
   standing precedent.)*
   Those two rows were, at the time of writing, *live*: their reader was wired on
   the Internal-KB route only, so on the open-web routes they genuinely were
   not-available and had to carry a reason. Linear's row differs in both
   directions. Its catalogue entry
   declares `routes=_EXTERNAL_ROUTES`, so the one route that could not read it no
   longer OFFERS it — and on the routes that do offer it, S8's driver makes it
   readable. Once that driver lands, no route both offers Linear and cannot read
   it, which is exactly the condition that emptied web's row. **The row is kept,
   not deleted**, on the same precedent: a three-language row costs nothing to
   retain and a future route change could make it reachable again.

   **That driver landed in S8 Session 3, so the row is now inert.** `linear`
   derives as driven, `KNOWN_UNREACHABLE` is empty again, and every external route
   both offers Linear and can read it — so no route renders this row. It is
   retained rather than deleted, exactly as web's is.

   **"Can read it" is true of the ADAPTER and false of the shipped entry point,
   and the difference reaches a person (recorded 2026-09-11, Q26).** `linear`
   derives as driven because `adapters/linear.py` exists, and
   `kind_reachability` attributes drivers per FILE — a floor on offerability, never
   a guarantee that a read happens, as this section already says of that input.
   What the production CLI actually does: `declared_read._cmd_read` constructs no
   `LinearAdapter` and passes no `linear_adapter=`, so `_adapter_for` raises
   `MissingAuthorization` and every declared Linear source returns as a refusal
   naming *"every declared linear source"*. A grep for a production construction
   site finds none anywhere in the tree.
   The refusal is BY NAME, which is the honest failure and not the silent one —
   but it means a person may tick Linear, be probed, approve it, and receive a
   refusal line instead of findings. **Q26 did not cause this and does not fix
   it**; it is recorded here because widening the list made the five-way promise
   look more reachable than it is, and this is the kind that does not reach.
   Owned by its own TODO, not by this section.

   **Between S8 Session 1 and Session 3 the row was NOT inert — it was live, and
   doing precisely the job it exists for.** In that interval Linear was registered
   with no production driver, so it was offered on no route, and its row rendered
   on the external routes as not-yet-available carrying `{{unavailable_linear}}` as
   its reason. That is the general Step 2.6 rule above applying unchanged: a class
   that cannot be read is SHOWN with its reason, never silently dropped.

   **That interval could not be extended past the driver, and the attempt is
   recorded because the reason is not obvious.** Session 3 was asked to keep the
   row until its closing verification had walked the chain against a real
   workspace, so that nobody could be offered Linear on the strength of an unproven
   read. `kind_reachability.check()` refuses that state: it fails a driven kind
   that still carries an exemption exactly as it fails an undriven kind without
   one, and the adapter's mere presence is what makes the kind derive as driven.
   There is no "driven but not yet offered" state to occupy, so the row goes with
   the driver and the real read is proven immediately after rather than before.
   *(An earlier draft of this paragraph claimed the row was withheld from the list
   entirely during that interval, on the theory that a recorded driver exemption
   omits a row rather than marking it unavailable. That was false —
   `render_catalogue` emits a row for every class on the route and distinguishes
   no reason for unselectability — and it contradicted the Step 2.6 rule two
   paragraphs above. An independent checker caught it. It is corrected here rather
   than quietly deleted, because prose asserting behaviour the code does not have
   is the failure this topic keeps recording against itself.)*
3. **Ask each selected class for its bound**, in that class's own terms —
   `{{bound_prompt_code}}` and the sibling slots. A class whose bound is "whatever
   this source exposes" is recorded as that, deliberately, rather than as a blank;
   offer `{{bound_unscoped_option}}` only where the class permits it.
3a. **A class that needs a connection offers to make one, in place** (S8, U4 —
   the connect-now step). Selecting a source the person has not yet connected
   surfaces `{{connect_prompt_linear}}` and, on acceptance, opens a browser for a
   single read-only authorization. The person comes back to the SAME selection
   list and is asked `{{connect_outcome_question}}` — proceed with the source, or
   drop it. **Declining leaves no partial declaration behind**: a source that was
   dropped is a source that was never declared, not one declared without a
   connection. Consent is never delegated silently, which is why the connection
   is offered rather than made on the person's behalf.

   **A bound given as a filtered-issues link is resolved to its membership HERE,
   once, and frozen into the record.** That is the only moment where credentials
   exist and the record is not yet immutable. The person then approves the
   RESOLVED SET rather than the link that produced it, so the approval gate says
   `{{resolved_bound_notice}}` — naming how many issues the filter held and when.
   Where the resolved count is `0`, or exceeds what the run can read, the bundle
   says so at approval rather than letting it surface later as a short read.

   **This makes such a bound asymmetric with every other kind, deliberately.**
   Every other class freezes the *boundary* and tests membership live, so a file
   added to a declared folder after approval is still admitted. A filtered-issues
   bound instead freezes the *result*: an issue that starts matching afterwards is
   not read, and one that stops matching still is. A query-shaped selector has no
   boundary a credential-less predicate could test, so freezing the set is the
   only way to keep an INDEPENDENT refusal rather than a rubber stamp on whatever
   the adapter happened to yield.
4. **Probe each bound before the approval gate** (UX Decision #5). An unreachable
   source is surfaced with `{{unreachable_notice}}` and the three-way choice
   `{{unreachable_retry}}` / `{{unreachable_drop}}` / `{{unreachable_rebound}}` —
   at selection time, not part-way through the run.
5. **Nothing selected ends the flow** with `{{empty_selection}}`: nothing read,
   nothing claimed (UX Decision #11). No manifest cycle is opened.

**No approval happens here.** The assembled declaration is carried into Step 3 and
approved there, once, with the rest of the framed scope (UX Decision #3). That is
what keeps a framing-phase caller from needing a sixth interactive stop.

---

## Step 3 — Approval bundle (Pre-Presentation Gate)

After Step 2.5 produces a PASS verdict (or the round counter hits
`max_rounds`), the skill surfaces the approval bundle. **No draft reaches the
approval surface without a fresh Step 2.5 PASS verdict, or a budget-exhausted
ESCALATE that surfaces the 4th option.** Any Edit revision routes back through
Step 2.5 before re-presentation (Q1 Pre-Presentation Gate).

**Ninja / Deep / Internal KB approval question:** `{{approval_question}}`.

**The declared sources, shown here — but only for a path that ran Step 2.6.** For
Ninja / Deep / Ultra Deep the bundle also carries the assembled declaration: each
selected source and the bound given for it, plus any source that could not be
reached with its `{{unreachable_retry}}` / `{{unreachable_drop}}` /
`{{unreachable_rebound}}` choice. This is the single explicit approval — the person
sees what will be read, and the declaration becomes the approval record.

**Internal knowledge base shares this question and, since S7, DOES run Step 2.6**,
so it gets a declaration section like the other three. The rule that produces this
is unchanged and is still the right one: condition the section on the path having
run a selection step, not on anything else. What changed is which paths satisfy it
— Internal KB now does, because S7 shipped the readers whose absence was the whole
reason for its exemption.

The section it renders lists **internal classes only** (`knowledge_library`,
`document_folder`), which is what keeps this route's `internal-only` source-tier
true. A topic with nothing to offer is not a dead end: the empty-state path below
takes over, and an empty selection still ends the flow cleanly with
`{{empty_selection}}` rather than dead-ending.

Options:

- `{{approve_label}}` — accept the draft as-is.
- `{{edit_label}}` — branch to a sub-question `{{edit_picker_question}}` that
  picks one of six components (angles / focused-questions / search-terms /
  depth-tier / where-to-search / languages), then a third AskUserQuestion
  `{{edit_field_question}}` does the edit. Re-runs Step 2.5 before
  re-presentation.
- `{{cancel_label}}` — flow returns to Step 1 with the original query
  restored; counter is preserved.
- `{{escalate_label}}` — only present when budget exhausted; carries the
  final DISCREPANCY critique verbatim.

**Ultra Deep approval bundle:** same as Ninja / Deep PLUS a multi-select
checklist asking `{{adjacent_points_question}}`. Initial display is 5
candidates; a `{{recommend_more_label}}` option triggers another adjacent-
points generation pass. Toggling candidates does NOT trigger Step 2.5 re-check
(selecting from candidates, not changing the scope-quality predicate).

**Autonomous approval bundle:** skipped — the AI auto-approves on the user's
behalf.

---

## Step 4 — Post-approval dispatch

**Ninja / Deep / Ultra Deep / Autonomous.** The skill calls `r0_intake` once
per selected language. Convention: `cycle_id = default` for EN,
`cycle_id = de` for DE, `cycle_id = ru` for RU. Payload carries
`user_approved_scope = true` **together with the approved `scope` object**
(Ninja / Deep / Ultra Deep) or `autonomous_scope = true` (Autonomous). For
Ultra Deep, the scope passed to `r0_intake` includes both the base scope and
the user-selected adjacent points. `validate_schema` rejects either flag
unless `caller_skill` is in
`CALLER_SKILL_WHITELIST = {/clarification, /work-decode, /research}` (Q8).
The existing `research-scope-gate.sh` PreToolUse hook then exits 0 on
subsequent search-shaped calls for the cycle. The rest of the research
pipeline runs from Phase 0 onward unchanged.

**MAJOR 7 — the runnable template.** `SKILL.md`'s only `r0_intake` code block
sits under its Step -2 two-arm route table, in the row assigned to the
**Predefined scope** route (`--from <file>`, or any programmatic caller) —
registered "here, Step -2" per that table, not after a Step-3 approval
(MINOR 5, round-5 fix: this line previously claimed the block is
"annotated 'Predefined-scope / programmatic route ONLY'"; that exact string
does not appear anywhere in `SKILL.md` — a grep over both files returns
exactly one hit, this line itself. The guidance was right; the citation was
not). It does not apply here; this is the typed route's own template, called
once per selected language, after Step 3's approval.

**The payload goes through a file, not a shell literal (MAJOR 1, round-3
fix).** The `scope` object below IS the operator's own approved English —
angles, focused questions, search terms — and a single-quoted shell literal
cannot safely carry it: an apostrophe ("what's", "don't") breaks the quoting
at execution time, and a multi-line command containing "for", "while",
"done", "fi" or "esac" as a whole word — ordinary in English research prose —
is refused outright by `sanitize-bash.sh` pattern 9. Write the payload with
the **Write tool**, then invoke the CLI as a single line with
`--payload-file`.

Write a scratch payload file at a **fixed name, never one derived from the
topic or the approved scope** (e.g. `research_pipeline_payload.json`, used
verbatim — the file's *content* carries the topic slug; its *name* must not,
or the slug reaches the command line by another route through
`--payload-file`'s own path argument). With:

```json
{"research_file_path": "Thoughts/[topic]_RESEARCH.md",
 "caller_skill": "/research",
 "downstream_tool": "~/.claude/skills/research/SKILL.md",
 "caller_session_id": "[the value of $SESSION_ID]",
 "user_approved_scope": true,
 "scope": {"[the approved scope object — angles, focused_questions, search_terms for this language, suggested_depth, where_to_search, languages; Ultra Deep also includes the user-selected adjacent points]": "..."},
 "scope_provenance": "fresh-answer"}
```

Then, on ONE line (per selected language; `cycle_id` follows the EN/DE/RU
convention above; called HERE, after the Step-3 approval, never at Step -2 —
see the two-arm rule below this block):

```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r0_intake --payload-file "<path to the file just written>" --cycle-id "[default for EN | de for DE | ru for RU]"
```

**Autonomous ONLY — the same Write-a-file + single-line `--payload-file` shape
as the typed route above (MAJOR 1, round-4 fix).** No scope exchange ran, so
there is no fresh-answer artifact to carry; the flip instead rests on
`autonomous_scope` + a whitelisted `caller_skill`, and the resolver records a
per-cycle bypass (channel 4), never an approval artifact. **This template used
to be the exact retired shape the paragraph above it warns against** — a
multi-line, single-quoted shell literal — and it was wrong on two counts, both
reproduced against the live hook: the placeholder text itself contains "for"
three times, so a model that pastes the block before resolving placeholders is
refused outright; and once resolved, `research_file_path` carries the **topic
slug**, and a hyphen is a word boundary, so any slug containing
`for`/`while`/`done`/`fi`/`esac` as a token (e.g. `case-for-heat-pumps`) still
trips `sanitize-bash.sh` pattern 9 even fully resolved. Autonomous is the one
route with no operator in the loop to notice the refusal, so it gets the same
treatment: write the payload with the **Write tool**, then invoke the CLI as a
single line with `--payload-file`. Same per-language `cycle_id` convention.

Write a scratch payload file at a **fixed name, never one derived from the
topic** (e.g. `research_pipeline_autonomous_payload.json`, used verbatim —
same reason as the typed route above: the file's name must not carry the
topic slug, or it reaches the command line through `--payload-file`'s own
path argument).
With:

```json
{"research_file_path": "Thoughts/[topic]_RESEARCH.md",
 "caller_skill": "/research",
 "downstream_tool": "~/.claude/skills/research/SKILL.md",
 "caller_session_id": "[the value of $SESSION_ID]",
 "autonomous_scope": true}
```

Then, on ONE line:

```bash
python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r0_intake --payload-file "<path to the file just written>" --cycle-id "[default for EN | de for DE | ru for RU]"
```

When Step 2.6 ran and the person approved a declaration (Ninja / Deep / Ultra
Deep), add it the same way `SKILL.md`'s predefined-route block does: as
`"scope_record": <json>` — the JSON form the source picker assembled, hoisted
onto the cycle exactly as described under Step 4's declaration paragraphs
below.

**This is where a TYPED run registers, and the placement is load-bearing**
(research-entry-point-enforcement S4 / design deviation 9). Two surfaces used
to disagree about where `r0_intake` is called — `~/.claude/skills/research/SKILL.md`
registered it at Step -2, before framing, while this step registered it after
approval — and `r0_intake` cannot be called twice for one cycle, so exactly one
could win. S4 reconciled them into one rule with two arms, stated in the table
at `SKILL.md` Step -2 and restated here so neither surface can drift alone:

- a **predefined-scope or programmatic** run registers at **Step -2**, because
  its artifact (the `--from` file, or the caller's confirmed goal) already
  exists then;
- a **typed** run registers **here, after the Step-3 approval**, because before
  that there is no artifact and the operator has not been asked.

Registering a typed run at Step -2 would not merely be early — it would
**deadlock the run**. The manifest's existence is what makes
`_classify_intake_source` return `research`, which removes the session's
exemption in `research-scope-gate.sh`; with no artifact the cycle stays
unapproved, so the gate then blocks `WebSearch`, `WebFetch`, the `research`
`Agent` dispatch, and the two Bash shapes it actually gates (a python
invocation of a browser-automation driver, or a write-redirect into
`*_RESEARCH*.md`) for the rest of the session — **not every `Bash` call**
(MINOR 2, round-3 fix: `research-scope-gate.sh:39-49` `exit 0`s an ordinary
Bash command matching neither shape; measured directly against the live
hook).

**This two-arm rule is followed, not enforced (MAJOR 6d).** Nothing in code
prevents a typed run from registering at Step -2 anyway — `cmd_advance` accepts
whatever payload it is given, and `_classify_intake_source` flips the session's
classification the moment the manifest file exists, independent of which arm
put it there. Stated plainly rather than left implicit, per this harness's own
`output-security.md` precedent that a boundary name when it is followed rather
than enforced: the only backstop is the deadlock itself, and recovery from a
stranded cycle is `python3 ${KIT_HOOKS_DIR}/research_pipeline.py reset
<session-id> [--cycle-id <id>]`.

**Approval is an ARTIFACT, not the caller's name** (S4 / A4; locked Guiding
Policy channel 3). The flip used to fire on a whitelisted `caller_skill` alone,
so naming a skill *was* the approval. It now fires only when the payload carries
one of three artifacts, and the cycle records **which**:
`fresh-answer` (the operator answered the framing exchange), `git-head` (a
predefined scope in a file committed before the session), or
`explicit-argument` (a file named on the call, recorded with the argument that
named it). A whitelisted caller is necessary and never sufficient on its own —
but, together with a validated approval artifact, it now IS authorization
(MINOR 7, round-4 fix: this sentence used to say the flags "remain additional
state, never standalone authorization", which `research_pipeline.py`'s own
`CALLER_SKILL_WHITELIST` comment explicitly retires in this same slice — see
`resolve_approval_artifact`'s docstring for the current contract). A payload
with no artifact still registers — it simply does not approve. The failure direction is
downgrade, never refusal, but **downgrade is not the same as recoverable
in-place, and this file used to conflate the two (S4 round-2, ITEM 4).**
`r1_scope_approved` is assigned at exactly one site, reachable only from
`r0_intake`, and `r0_intake` cannot be called a second time for the same cycle
(`research_pipeline.py` raises `Already completed: r0_intake`) — so a cycle
that registered with no artifact is **permanently unapprovable**, and the
session has already lost its `research-scope-gate.sh` exemption the moment the
manifest exists, regardless of whether the cycle went on to approve. There is
no second call through which "the run confirms the scope interactively" could
reach the manifest — that phrase described a recovery path that does not exist.
`~/.claude/skills/research/SKILL.md:161` already states the true situation: the
caller must confirm the scope with the operator **before** calling `r0_intake`
at all, and the only recovery for an already-stranded cycle is
`python3 ${KIT_HOOKS_DIR}/research_pipeline.py reset <session-id> [--cycle-id
<id>]`.

**Autonomous is an exemption, recorded as one.** That route keeps its flip on
`autonomous_scope = true` + a whitelisted caller, and the intake writes a
per-cycle bypass record naming the decision it rests on and the question still
open (channel 4: every bypass leaves a record). It is deliberately not the
session-level `bypass`, which would disable the pipeline gate wholesale.

**Approval is a two-level model — a coarse session gate, and a strict per-cycle
predicate. This is a deliberate DEVIATION from A4's literal wording ("approval
is resolved per cycle"), recorded here with the reason.** A4 was first shipped
as a single change: `resolve_scope_status` — the SESSION-level resolver the
scope gate (`research-scope-gate.sh`) consults — read the approval flag of the
**current** cycle only, so it doubled as both the session gate and the
no-inheritance enforcement. That did not survive contact with how sessions
actually use cycles: `/work-decode` opens one cycle per Part-B row and
explicitly dispatches them CONCURRENTLY, and the multi-language route opens
`default`/`de`/`ru` in the same session. `current_cycle_id` is a
last-writer-wins scalar, and the session gate has no way to know which cycle a
given tool call belongs to — so registering ONE unapproved cycle (or simply
advancing it last) blocked every genuinely-approved sibling cycle in the same
session. That is collateral damage the Guiding Policy never asked for
(MAJOR 3, independently reproduced against a real `/work-decode` shape:
`wd-b-r1`/`wd-b-r2` approved with real artifacts, `wd-b-r3` unapproved,
the whole session blocked).

The fix keeps A4's no-inheritance rule and moves it to where it can actually
be enforced without this collateral cost:

- **The SESSION gate (`resolve_scope_status`) is back to existential
  approval** — exactly its pre-A4 behaviour. ANY cycle with
  `r1_scope_approved is True` makes the session's decision `approved`; no
  approved cycle's research is ever blocked by an unapproved sibling at this
  gate. Revocation is unchanged: it stays existential across every cycle and
  is evaluated first, so a revoked sibling still blocks a session whose
  current cycle is approved.
- **The per-cycle no-inheritance rule is COMPUTED at the dispatch/read
  site**, where the cycle id performing the read is actually known, via
  `resolve_cycle_scope_status(state, cycle_id)`. That function answers for
  one named cycle only, never consults a sibling, and returns `approved`
  only when THAT cycle's own flag is set — so a second cycle opened later
  genuinely does not inherit a sibling's approval; it must be approved on
  its own artifact.
- **Whether that computed answer is actually ENFORCED splits by which site
  consumes it (research-entry-point-enforcement S4 round-2, ITEM 3 — this
  bullet used to say "enforced" for both without distinguishing them).**
  Reading the person's own declared sources
  (`~/.claude/skills/research/declared_read.py`, `_resolve_scope_from_cycle`)
  calls this predicate itself, in code, and refuses that cycle by name (never
  the whole session) when it is unapproved — that path is genuinely
  **ENFORCED**. Dispatching the `research` agent is a different site:
  `~/.claude/skills/research/SKILL.md`'s dispatch step only INSTRUCTS the
  model to check this predicate before dispatching; nothing in code stops the
  `Agent` tool call for an unapproved cycle if the model skips that
  instruction. That path is **FOLLOWED, NOT ENFORCED** — the same distinction
  `output-security.md` draws for its own boundary, and the phrase this file
  uses for it elsewhere.

Net effect on the reviewer's scenario: the session resolves `approved` (r1's
approval qualifies it), `wd-b-r1` and `wd-b-r2` each pass the per-cycle
predicate on their own artifacts and their research runs, and `wd-b-r3` fails
the per-cycle predicate and is the only one refused.

**This description bounds to the UNAPPROVED case shown here — `wd-b-r3` never
approved, never revoked.** Revocation stays existential across every cycle
(unchanged, pre-S4) and is evaluated first, so had `wd-b-r3` been **aborted**
rather than merely unapproved, that revocation would block every approved
sibling in the session too — the same collateral shape on the revocation axis
that this fix resolves on the approval axis. That is a documented pre-S4
asymmetry (stated in `resolve_scope_status`'s docstring), not a new defect —
but this paragraph's "only `wd-b-r3` is refused" claim does not cover it.

**The approved declaration rides the same payload — on the paths that open a
cycle.** When Step 2.6 ran, the person approved at Step 3, **and this path calls
`r0_intake` at all**, add `scope_record` to the `r0_intake` payload — the JSON form
of the record the picker assembled. It is hoisted onto the cycle beside the
approval flag, which is where the containment check reads it. Only an approved
declaration is ever written: a spike stop or an empty selection opens no cycle at
all, so there is nothing to persist and nothing to gate. Registration validates the
record's *shape* (its version, and that its sources are a list); whether each
declared source is admissible is re-checked where the record is read, not here.

**And on Ninja / Deep / Ultra Deep the declaration is now READ, not only carried.**
Hoisting the record onto the cycle was the whole of what these routes did with it:
a person could tick "your code", name a repository, watch it get probed, see it in
the approval bundle, approve it — and the run never opened the repository. The
record rode the payload and nothing consumed it. After the intake call and before
synthesis, drive the approved record through the **same reader** the Internal-KB
route uses — `declared_read.py`'s `read` CLI verb, exactly as `SKILL.md`'s
"Reading the person's own declared sources" section runs it (MAJOR 2, round-5
fix: this paragraph used to reproduce that command inline; a single-backtick
copy with a markdown-forced line-wrap right after the `PYTHONPATH=` assignment
was refused verbatim by `sanitize-bash.sh` even though the fenced, one-line
original in `SKILL.md` passes — do not reproduce a shipped command a second
time in prose; point at it instead) — see that section for the full command
and what comes back.

**MINOR 5 (round-4 fix): "same reader" is true; "same entry point" is not, and
this file used to claim both.** `internal_kb.py:296-329` delegates
**in-process** to `read_declared_sources` — never the `read` CLI verb, and
never `_resolve_scope_from_cycle`. It *cannot* call the `read` verb: that verb
resolves a manifest cycle, and the Internal-KB route opens no cycle at all, so
there is no cycle to resolve and no `r1_scope_approved` flag to check. Internal
KB's own read is therefore **not cycle-approval-enforced** — not because
enforcement was skipped, but because the thing the `read` verb enforces
(per-cycle approval) does not apply to a route that never registers a cycle in
the first place. Ninja / Deep / Ultra Deep, by contrast, DO open a cycle (Step
4 above), which is exactly what lets their read go through the `read` verb and
inherit its enforcement.

**MINOR 5 (round-3 fix, unchanged): call the CLI verb, not
`read_declared_sources` directly.** The `read` verb resolves the cycle and
checks `resolve_cycle_scope_status` (`_resolve_scope_from_cycle`) — that check
is what makes this path genuinely ENFORCED, per the bullet above —
**before** it ever calls `read_declared_sources`, which is the post-resolution
half and performs no approval check of its own; passing the **framed
question** via `--query-file` (MAJOR 2, round-4 fix — never `--query` directly,
which puts the question on the command line) so the run spends its budget by
bearing on the question rather than by alphabetical position. Compose the findings from what it printed
(the CLI composes them via `synthesize_from_admissions` internally); do not
open a declared repository with a file-read tool or a shell, which reads beside
the port and produces claims with no re-openable pin behind them. A `web`
source in the record is **skipped here by name, not refused**: the fact-check
engine's own ingest reads that kind inside `admit()` during verification, and a
refusal line would tell a person something false.

Three things this reader gives a person, each of which must survive into the
report: a **pin** on every finding (`[stated — code:<repo>@<rev>:<path>:<lines>]`
for a repository) that still resolves months later; a source that could not be read
coming back **refused by name with its reason**; and a run that hit its reading
limit **saying so**, naming the bound, rather than returning a shorter report
nobody can distinguish from a smaller source.

**The same ceiling applies to these routes as to the Internal-KB one, in the same
words** — see the paragraph below. Nothing gates the tool boundary, so the
declaration here is **followed, not enforced**, and no surface may say otherwise.
**One further limit, stated rather than implied:** each cited file is read WHOLE and
pinned across its whole line range. design-A20's narrowed-excerpt half is unbuilt
for every path-shaped source class and this slice does not build it, so a `1-N` pin
is truthful about what was read and is not evidence of a selected passage within it.

**The third condition is load-bearing, and S7 is what made it so.** Before S7 the
first two conditions selected exactly the paths that open a cycle, so the third was
implied and did not need saying. Internal KB now satisfies both of them and still
opens **no manifest cycle** (below) — so stated as a two-condition universal, this
paragraph would claim a record rides a cycle that does not exist. Its declaration
is carried differently, and where is said in the next paragraph.

**Internal knowledge base.** The skill **still does NOT call `r0_intake`** (no
external search to unblock; no manifest cycle needed) — **and, since S7, it builds
a declaration and reads against it.** Both are true at once and neither weakens the
other: the declaration is what bounds the read, and the manifest cycle is what
unblocks an external search this route never performs. So the approved
`ScopeRecord` is carried **in-process to the read** rather than hoisted onto a
cycle; there is no payload for it to ride, and none is invented.

The read itself is performed **through the admission port**
(`~/.claude/skills/research/declared_read.py` → `read_declared_sources`, which
drives every candidate through `source_port.AdmissionPort.admit()`), and the
synthesis consumes what the port returned (`synthesize_from_admissions`). A path the
approved declaration does not cover comes back **refused by name, with its reason**,
and the refusal is rendered into the body rather than dropped — a read refused
silently would reach a person as a finding that simply is not there.

**That reader used to live in `internal_kb.py` and no longer does, and the move is
the reason this route is now bounded.** It was the ONLY generic declared-source read
loop in production and it served this route alone, so the open-web routes had none
at all — and cloning it for them would have bounded the new class while leaving this
one unbounded. Instead the loop moved to a route-neutral home, gained a `code` arm,
and started passing the aggregate run budget it had never passed. `internal_kb`
keeps the same three public names as delegates, so this route's behaviour is
unchanged except in the one way that matters: **a declared library larger than the
run ceiling now stops at it and says so**, through the refusal channel that already
renders into the body, where it previously read essentially the whole library into
the report.

**The ceiling on that sentence, stated rather than implied.** This is a contract on
the route's READ PATH, not on every read physically possible. Nothing gates the
tool boundary: no `PreToolUse` hook covers `Read` for source paths, so a read that
ignores this route entirely is still possible and no code here can stop it. Gating
that boundary is **design-A18, which is withdrawn and routed to
`/clarification --from`**. The declaration is therefore **followed, not enforced** —
a large improvement on a route that declared nothing at all, and less than
containment. Do not write, here or downstream, that reading outside the declaration
is impossible.

Sources are the declared ones (the knowledge-library folders the project's
`research_library_folders:` key names, the topic-folder `_RESEARCH` files, and any
folder of documents the person named — OR user-provided paths on empty-state), and
the synthesized `_RESEARCH.md` cites its claims per the Marker Contract below. The
topic's CLAUDE.md is read as a **pointer** to where those documents live — never
quoted as a source, and never cited (Edge 2 below). Standard PostToolUse
`factcheck-research-file.sh` fires; standard convergence FC applies; the
engine's file-path-driven `_append_research_frontmatter` writeback adds the
`fc_cycles:` block automatically (no manifest dependency).

---

## Internal KB empty-state path (Q13)

When the topic has neither a topic-CLAUDE.md nor any `_RESEARCH` files in its
folder, the skill issues an AskUserQuestion asking `{{error_internal_kb_empty}}`
with the free-text field accepting folder paths, file paths, and/or glob
patterns, one per line.

Validation flow:

1. Skill uses `Glob` to expand patterns, then `Read` to confirm each resolved
   file exists.
2. If ALL paths fail → error message + re-enter or `{{cancel_label}}`.
3. If SOME succeed and SOME fail → mixed-result confirmation listing what was
   found and what didn't resolve; user picks Proceed / edit /
   `{{cancel_label}}`.
4. On full success, show `"I found N files: [bulleted list]. Proceed?"` with
   `{{approve_label}}` / re-enter / `{{cancel_label}}`.

Glob handling: standard `*`, `**`, `?`, `[abc]` supported. **An absolute path is
accepted** — a repository a person has cloned and a folder on a network share are
both absolute by nature, and both are locations a person may legitimately name.
Admission is decided by where a pattern *resolves*, never by how it is spelled,
so an absolute spelling and a `..`-traversing relative spelling of the same
location get the same treatment, and an out-of-root pattern expands against its
own root rather than against the workspace. Reject patterns starting with `**`
without a directory prefix (rooted at the workspace that enumerates the whole
tree). Warn (editorial threshold) if a pattern would match > 100 files
post-expansion. No minimum
file size or content quality check — trivial inputs produce
`[unverified — not found in internal knowledge base]` markers naturally
through the Marker Contract.

`{{cancel_label}}` at any stage re-opens the Step 1 routing prompt.

---

## Marker Contract — one vocabulary, web and internal sources (Convention B)

**This table is an asserted MIRROR.** The authoritative list is
`CITATION_MARKER_REGISTRY` in `${KIT_HOOKS_DIR}/_factcheck_engine.py`, and
`check_citation_marker_drift` compares the two — a change made here but not there
(or the reverse) fails `config-verify` and names the divergent marker. Edit the
registry and every mirror in ONE change; the human-facing long form of this table,
with meanings and antipattern rules, is `Skills/research-sources.md`.

Internal KB synthesis cites every claim with one of the markers below. Since
research-source-adapters S2 the web forms and the internal-source forms are **one
vocabulary** rather than two disjoint sets, so this table carries both: a run may
mix sources, and a claim is cited by what it was drawn from, not by which path
selected it.

| Marker | Kind | Locator | Status | Used for |
|---|---|---|---|---|
| `[stated — URL]` | stated | url | active | Direct quote from a web source |
| `[paraphrased — URL]` | paraphrased | url | active | Paraphrase of a web source |
| `[stated — local-file:<path>:<line>]` | stated | local-file | active | Direct quote from a file on disk |
| `[paraphrased — local-file:<path>:<line>]` | paraphrased | local-file | active | Paraphrase of a file on disk |
| `[stated — code:<repo>@<rev>:<path>:<lines>]` | stated | code | active | Direct quote from a file in a repository, pinned to the commit read |
| `[paraphrased — code:<repo>@<rev>:<path>:<lines>]` | paraphrased | code | active | Paraphrase of a file in a repository, pinned to the commit read |
| `[stated — linear:<workspace>@<version>:<issue>]` | stated | linear | active | Direct quote from a Linear issue, pinned to the workspace and issue version read; a comment id may follow the issue id |
| `[paraphrased — linear:<workspace>@<version>:<issue>]` | paraphrased | linear | active | Paraphrase of a Linear issue, pinned to the workspace and issue version read; a comment id may follow the issue id |
| `[inferred from …]` | inferred | — | active | AI inference combining sources |
| `[My assessment: …]` | my-assessment | — | active | AI editorial |
| `[unverified — …]` | unverified | — | active | AI cannot back the claim from the declared sources |
| `[stated — topic-CLAUDE:<path>:<line>]` | stated | topic-CLAUDE | retired 2026-08-19 | **Retired.** CLAUDE.md is a pointer to where documents live, never a citable source |
| `[paraphrased — topic-CLAUDE:<path>:<line>]` | paraphrased | topic-CLAUDE | retired 2026-08-19 | **Retired.** As above |

**Free-text slots — the internal-KB wordings.** `[inferred from …]`,
`[My assessment: …]` and `[unverified — …]` carry a free-text slot, so the phrasings
Convention B used before S2 are *fillings* of these entries rather than separate
markers: write `[inferred from internal sources]` and
`[unverified — not found in internal knowledge base]` for internal-KB work. Both
remain correct; neither is a distinct marker, which is why the table lists the
general form once instead of listing each filling.

**Retirement is forward-only.** A retired marker is no longer OFFERED — do not write
a new one — but it is still RECOGNISED: a file citing it parses normally, verifies
normally, and is never rewritten on account of the retirement. The fact-check style
checker names a retired marker and its retirement date specifically, instead of
letting it pass as one more anonymous unrecognised marker. That is a report, not a
refusal: no code path writes these markers (they are written by the AI synthesising
the file), so there is nothing to intercept.

**Q12 edge-case defaults:**

- **Edge 1 — path format. A source is cited in the form it can bear.** A source
  that resolves **inside** the Projects root is cited relative to it. Example:
  `Personal/foo/Docs/bar_RESEARCH.md:142` (NOT just `bar_RESEARCH.md:142`).
  Consistent across topic-folder structures (`Thoughts/`, project folders, etc.);
  grep-friendly.
  A source that resolves **outside** the Projects root — a cloned repository, a
  folder on a network share — is cited **absolutely, by this rule**, because that
  is the only expression it has: such a location can never be written relative to
  the workspace root, and rewriting it would produce a citation nobody can
  reopen. This is a stated rule, not a silent fallback.
  Exactly one shape is **refused**: a source that resolves inside the Projects
  root, cited absolutely. It is the one case where the alternative is strictly
  better — the relative form survives the workspace moving and the absolute form
  does not — so the refusal names that case and offers the relative form.
- **Edge 2 — CLAUDE.md walk-up is for FINDING sources, not for citing them.**
  Walk up from the topic folder to the closest CLAUDE.md; closest wins (per the
  Projects-root CLAUDE.md "Root → Context → Project. Max 3 levels."). For
  `Thoughts/` staging topics with no topic-level CLAUDE.md, walk-up resolves to
  the Projects-root CLAUDE.md. Use what it says to locate the documents that
  hold the answer, then **cite those documents** with a `local-file` marker.
  Do **not** mint a `topic-CLAUDE` citation — that pair is retired (see the
  table above): CLAUDE.md is a system topic file, a pointer to where documents
  live, never a research source.
- **Edge 3 — conflict resolution (CLAUDE.md vs `_RESEARCH`).** This is a
  question about which SOURCE to read, not about which to cite: only the
  `_RESEARCH` file is citable. Default: treat the topic CLAUDE.md as the more
  authoritative statement of intent when deciding where to look. **Recency
  rule:** when the `_RESEARCH` file's mtime > the CLAUDE.md's mtime, prefer the
  `_RESEARCH` file. If a claim's only support is the CLAUDE.md itself, it has no
  citable source — mark it `[unverified — …]` and say so, rather than citing the
  CLAUDE.md.
- **Edge 4 — citing without a specific line.** Use a line range OR a Markdown
  section anchor, whichever is clearer for the cited content:
  - `[stated — local-file:Personal/foo/Docs/bar_RESEARCH.md:120-135]` (line
    range — best for tight quotes)
  - `[stated — local-file:Personal/foo/Docs/bar_RESEARCH.md#section-heading]`
    (section anchor — best for thematic references; more robust when the file
    is reordered)

**Coexistence with engine-managed frontmatter (E2b).** Convention B body
markers are author-managed; the `fc_cycles:` YAML frontmatter written by
`_append_research_frontmatter` is engine-managed. Internal KB synthesis must
preserve any existing frontmatter — re-synthesis appends body content without
touching the sentinel-wrapped frontmatter block.

---

## Adjacent-Points-Generation Contract (Ultra Deep only, Q15 + C3 lineage)

Ultra Deep surfaces candidate adjacent points the user did not explicitly ask
about (e.g., for EV research: tire wear, resale value, insurance differences).
Three load-bearing rules:

1. **Generation-not-verification.** The candidates themselves are NOT
   fact-checked. The user is the only authority on whether each candidate is
   relevant to their interests. Producer-never-verifies does not apply because
   the AI is generating possibilities, not asserting facts.
2. **5-initial + recommend-more.** Initial display = 5 candidates. The
   `{{recommend_more_label}}` option triggers another AI pass; editorial total
   cap = ~15 candidates per session.
3. **No Step 2.5 re-check on toggling.** Selecting candidates from the
   checklist does not change the scope-quality predicate Step 2.5 verifies;
   only Edit-base does. Final scope = approved base scope + user-selected
   adjacent points; the combined scope is what flows into `r0_intake`.

### Prompt template (S5)

The `AdjacentPointsPort` adapter sends a single bounded turn assembled from
the approved base scope. The template below is the production wording; an
adapter must not extend it with project-specific context (producer-never-
verifies discipline starts at the prompt — the user is the only authority on
relevance, not the AI seeing the surrounding session).

```
You are helping a user broaden a research scope for the /research
scope-framing UI. The user has already approved a base scope on the topic
below. Your job is to surface candidate ADJACENT points the user did NOT
explicitly ask about but might also want included in the research
(examples for an EV-adoption topic: tire wear, resale value, insurance
differences).

Return ONE JSON object — no preamble, no markdown fences — with EXACTLY
this key:
  candidates: array of strings; each string is one adjacent point phrased
  as a short noun phrase the user can recognise at a glance.

Rules:
  * Return exactly N candidates (N is given below).
  * Do NOT repeat anything in the "already-surfaced" list.
  * Do NOT propose candidates that overlap with the base angles or
    focused questions — those are already covered.
  * Adjacent ≠ tangential: each candidate should be plausibly relevant to
    someone investigating the base topic, even if they didn't ask.
  * Generation, not verification: do NOT fact-check candidates. The user
    decides which (if any) belong in the research.

Topic (user query, verbatim):
{user_query}

Approved base angles:
{base_angles_bulleted}

Approved focused questions:
{base_focused_questions_bulleted}

Already-surfaced candidates (do NOT repeat):
{already_surfaced_bulleted_or_none}

Conversation language: {conversation_language}
N (number of candidates to return): {requested_count}
```

Editorial defaults (calibratable, not load-bearing): `requested_count` = 5
for the initial pass; the recommend-more pass also requests 5; total cap
across one session ≈ 15.

---

## User-facing message style (Q11)

User-facing strings — error messages, warnings, remediation, footer prose —
use plain English (and the corresponding plain German / plain Russian per the
Localization Table). Internal code identifiers (`r0_intake`,
`r1_scope_approved`, `r1_scope_revoked`, `cycle_id`, `caller_skill`,
`non_pass_verdict`, ...) do NOT appear in user-facing text.

Example: the revoked-cycle remediation is
`{{error_revoked_cycle_remediation}}` — which names a runnable command
verbatim (the command itself stays in code form) but never names
`r0_intake` or `r1_scope_revoked` in the prose around it.

This applies to: `validate_schema` error messages,
`research-scope-gate.sh` revoked-cycle remediation,
`check-research-pipeline-gate.sh` `RESEARCH-VERDICT:` lines and any error
output, any `/research` skill error/warning text.

It does NOT apply to: code identifiers in `research_pipeline.py` and hook
scripts, test names, frontmatter, discovery/plan/design documents (internal
architectural language).

---

## Session-end synthesis footer (UX Decision #8)

Every direct `/research` session ends with a footer line:

- Ninja / Deep / Ultra Deep / Internal KB → `{{footer_user_approved}}`
- Autonomous → `{{footer_autonomous_approved}}`

For Ultra Deep, the footer also lists the user-selected adjacent points
separately so a later reader can distinguish base scope from broadening.

---

## Verification-isolation contract (C5)

The end-to-end live smoke for this flow MUST execute under the
`${KIT_HOOKS_DIR}/check-verifier-isolation.sh` pre/post snapshot pair, AND MUST
be rendered as an out-of-session copy-paste block the user runs from a fresh
shell outside the producer's harness — never inline in the producer's process
tree. This is the rule (a) + rule (b) pair from
`code_first_architecture.md:134-145`. Slice S9 fills in the matrix below; S1
documents the contract here.

### Out-of-session smoke block (6-sub-test matrix)

**How to run:** open a fresh terminal **outside the Claude Code session that
produced this code** (rule (a) — never inline in the producer's process
tree). Copy the entire block below and paste it into that terminal. The
script wraps each sub-test in a `check-verifier-isolation.sh` `pre` / `post`
snapshot pair (rule (b) — hard-fails on any drift to `~/.claude/{settings.json, hooks/*}`).
Each sub-test command carries a `: bootstrap-smoke` no-op token so the
guard's `is_dangerous()` matcher arms the snapshot deterministically. Paste
the final `=== S9 SMOKE RESULTS ===` block back into the Claude Code session
so the verifier can render per-sub-test PASS / FAIL.

```bash
#!/usr/bin/env bash
# S9 out-of-session smoke for research-scope-framing-ui.
# RUN FROM A FRESH SHELL — never inside the producer Claude Code session.
# Optional: export SESSION_ID=<your-id> before pasting; otherwise a per-shell id is used.
set -uo pipefail
: "${SESSION_ID:=s9-smoke-$$}"
HOOK="${KIT_HOOKS_DIR}/check-verifier-isolation.sh"
TESTS="${KIT_HOOKS_DIR}/tests/research_pipeline"
PYT="python3 -m pytest -x --tb=short -q"
declare -a RESULTS=()

run() {                                           # $1 label   $2 command
  local label="$1" cmd="$2" jin p t q
  jin=$(jq -nc --arg sid "$SESSION_ID" --arg c "$cmd" \
    '{tool_name:"Bash", session_id:$sid, tool_input:{command:$c}}')
  printf '%s' "$jin" | "$HOOK" pre;  p=$?
  bash -c "$cmd";                     t=$?
  printf '%s' "$jin" | "$HOOK" post; q=$?
  if [ "$p" -eq 0 ] && [ "$t" -eq 0 ] && [ "$q" -eq 0 ]; then
    RESULTS+=("$label: PASS")
  else
    RESULTS+=("$label: FAIL (pre=$p test=$t post=$q)")
  fi
}

# (i) Ninja direct → flip → search-unblock
run "(i)   Ninja direct -> flip -> search-unblock" \
  ": bootstrap-smoke; $PYT $TESTS/test_s3_walking_skeleton.py"

# (ii) Deep direct → flip
run "(ii)  Deep direct -> flip" \
  ": bootstrap-smoke; $PYT $TESTS/test_s4_path_extensions.py::test_deep_flow_flips_with_user_approved_scope_and_deep_tier"

# (iii) Autonomous direct → auto-flip (approval bundle NOT surfaced)
run "(iii) Autonomous direct -> auto-flip (no approval bundle)" \
  ": bootstrap-smoke; $PYT $TESTS/test_s4_path_extensions.py::test_autonomous_flow_skips_approval_bundle_and_flips_with_autonomous_scope $TESTS/test_s4_path_extensions.py::test_autonomous_flow_records_autonomous_scope_flag_on_manifest"

# (iv) Ultra Deep direct → combined scope (base + selected adjacent points) → flip
run "(iv)  Ultra Deep direct -> combined-scope -> flip" \
  ": bootstrap-smoke; $PYT $TESTS/test_s5_ultra_deep_and_production_adapter.py"

# (v) Internal KB PASS → synthesize → A5 writeback (fc_cycles:) → Stop-gate no-manifest fallback
run "(v)   Internal KB PASS -> A5 writeback -> Stop-gate fallback" \
  ": bootstrap-smoke; $PYT $TESTS/test_s6_internal_kb_and_locale_native.py $TESTS/test_s8_stop_gate.py::test_no_manifest_fallback_in_window_surfaces_verdict"

# (vi) Internal KB ESCALATE → operator authors BYPASSED marker → write-research-frontmatter
#      → fc_cycles: block with verdict: BYPASSED + bypass_reason in _RESEARCH.md
#      → Stop-gate no-manifest fallback surfaces it as informational
run "(vi)  Internal KB BYPASSED -> write-research-frontmatter -> Stop-gate informational" \
  ": bootstrap-smoke; $PYT $TESTS/test_s8_stop_gate.py::test_bypassed_verdict_in_no_manifest_fallback_surfaces_reason"

printf '\n=== S9 SMOKE RESULTS (SESSION_ID=%s) ===\n' "$SESSION_ID"
fail=0
for r in "${RESULTS[@]}"; do
  printf '  %s\n' "$r"
  [[ "$r" == *FAIL* ]] && fail=1
done
[ "$fail" -eq 0 ] && printf 'OVERALL: PASS\n=== END ===\n' \
                  || { printf 'OVERALL: FAIL\n=== END ===\n'; exit 1; }
```

**Sub-test → spec mapping (auditable):**

| # | Spec (from §`## Verification` of `dapper-coalescing-seal.md`) | Pytest target |
|---|----------------|---------------|
| i | Ninja direct → routing → Step 2.5 PASS → approve → flip → search-shaped tool unblocks → footer "Scope approved by: user". | `test_s3_walking_skeleton.py` (full module — includes happy-path Ninja flip + `research-scope-gate.sh` subprocess exit-0 + Q11 footer assertion) |
| ii | Deep direct → same chain at deeper search tier (`suggested_depth='deep'`, `user_approved_scope=true`). | `test_s4_path_extensions.py::test_deep_flow_flips_with_user_approved_scope_and_deep_tier` |
| iii | Autonomous direct → no approval bundle → flip with `autonomous_scope=true` → footer "Scope approved by: AI auto-approval". | `test_s4_path_extensions.py::test_autonomous_flow_skips_approval_bundle_and_flips_with_autonomous_scope` + `…::test_autonomous_flow_records_autonomous_scope_flag_on_manifest` |
| iv | Ultra Deep direct → base scope + ≥1 user-selected adjacent point → combined scope into `r0_intake` → flip. | `test_s5_ultra_deep_and_production_adapter.py` (full module — includes Ultra Deep E2E with combined scope, Cockburn 4-step states, producer-never-verifies on generation) |
| v | Internal KB direct (PASS path) → no `r0_intake` → Convention B markers → A5 writeback → Stop-gate no-manifest fallback surfaces `RESEARCH-VERDICT: PASS`. | `test_s6_internal_kb_and_locale_native.py` (full module — includes synthesis, fc_cycles preservation, Convention B markers, no-r0_intake) + `test_s8_stop_gate.py::test_no_manifest_fallback_in_window_surfaces_verdict` |
| vi | Internal KB direct (ESCALATE → BYPASSED path) → operator authors BYPASSED marker → `python3 ${KIT_HOOKS_DIR}/_factcheck_engine.py write-research-frontmatter <sid>` → `fc_cycles:` with `verdict: BYPASSED` + non-empty `bypass_reason` appears in `_RESEARCH.md` → Stop-gate no-manifest fallback surfaces it as informational. | `test_s8_stop_gate.py::test_bypassed_verdict_in_no_manifest_fallback_surfaces_reason` |

**Producer-never-verifies (C5 §S9 guard rails).** The implementer of S1–S8
does NOT author this smoke block; the S9 verifier session does. The block
above must not be edited from inside a producer session — re-rendering
belongs to a fresh S9-style verification slice.

**What the snapshot pair guards.** Every sub-test command contains the
`bootstrap` token, which arms `check-verifier-isolation.sh` `is_dangerous()`
deterministically. `pre` snapshots `~/.claude/{settings.json, hooks/*}` +
`git -C ~/.claude status --porcelain -- settings.json hooks`. `post`
recomputes and hard-fails (exit 2) on any drift — the bridge-agnostic
backstop for the 2026-06-09 corruption pattern. A passing sub-test means
both the pytest target succeeded AND no `~/.claude/` drift occurred during
that test.

---

## Localization Table

| Slot | EN | DE | RU |
|------|----|----|----|
| routing_question | How would you like to scope this research? | Wie möchten Sie diese Recherche eingrenzen? | Как вы хотите определить рамки исследования? |
| path_label_ninja | Ninja | Ninja | Ниндзя |
| path_desc_ninja | AI drafts a scope, you approve it; standard external sources. | KI entwirft einen Rahmen, Sie genehmigen ihn; übliche externe Quellen. | ИИ предлагает рамки, вы их утверждаете; обычные внешние источники. |
| path_label_deep | Deep | Tief | Глубокое |
| path_desc_deep | AI drafts a scope, you approve it; broader external sources. | KI entwirft einen Rahmen, Sie genehmigen ihn; mehr externe Quellen. | ИИ предлагает рамки, вы их утверждаете; шире внешних источников. |
| path_label_ultra_deep | Ultra Deep | Ultra-tief | Сверхглубокое |
| path_desc_ultra_deep | AI drafts a scope and also suggests adjacent points you didn't ask about; you pick which to include. | KI entwirft einen Rahmen und schlägt zusätzlich benachbarte Punkte vor, die Sie nicht erwähnt haben; Sie wählen aus. | ИИ предлагает рамки и дополнительно подсказывает смежные темы, о которых вы не упомянули; вы выбираете, какие включить. |
| path_label_internal_kb | Internal knowledge base | Interne Wissensbasis | Внутренняя база знаний |
| path_desc_internal_kb | AI synthesizes only from your project's own notes and research files. | KI fasst nur aus den projekteigenen Notizen und Recherchedateien zusammen. | ИИ синтезирует только из заметок и исследовательских файлов проекта. |
| path_label_autonomous | Autonomous | Autonom | Автономно |
| path_desc_autonomous | AI drafts a scope and approves it on your behalf; no further input from you. | KI entwirft einen Rahmen und genehmigt ihn für Sie; keine weitere Eingabe nötig. | ИИ предлагает рамки и утверждает их за вас; больше ничего от вас не требуется. |
| approve_label | Approve | Genehmigen | Одобрить |
| edit_label | Edit | Bearbeiten | Изменить |
| cancel_label | Cancel | Abbrechen | Отменить |
| escalate_label | Override the check and proceed | Prüfung übergehen und fortfahren | Принять несмотря на замечание |
| retry_label | Try again | Erneut versuchen | Повторить |
| reroute_label | Pick a different path | Anderen Pfad wählen | Выбрать другой путь |
| recommend_more_label | Suggest more | Mehr vorschlagen | Предложить ещё |
| approval_question | Does this scope look right? | Sieht dieser Rahmen richtig aus? | Подходят ли такие рамки? |
| edit_picker_question | Which part would you like to change? | Welchen Teil möchten Sie ändern? | Какую часть вы хотите изменить? |
| edit_field_question | What should it be instead? | Was soll stattdessen stehen? | Что должно быть вместо этого? |
| adjacent_points_question | Would you also like to include any of these in the research? | Möchten Sie eines davon ebenfalls in die Recherche aufnehmen? | Хотите ли вы также включить что-то из этого в исследование? |
| error_drafter_failed | The scope draft could not be generated. What would you like to do? | Der Rahmenentwurf konnte nicht erzeugt werden. Wie möchten Sie fortfahren? | Не удалось составить черновик рамок. Что вы хотите сделать? |
| error_internal_kb_empty | I couldn't find any internal notes or research files for this topic. Paste folder paths, file paths, or glob patterns (one per line) so I know where to look. | Ich konnte keine internen Notizen oder Recherchedateien zu diesem Thema finden. Bitte fügen Sie Ordnerpfade, Dateipfade oder Glob-Muster ein (eines pro Zeile), damit ich weiß, wo ich suchen soll. | Не нашёл внутренних заметок или исследовательских файлов по этой теме. Вставьте пути к папкам, путям к файлам или glob-шаблоны (по одному в строке), чтобы я знал, где искать. |
| error_locale_native_failed | search terms for this language are not available — proceed without this language, edit, or cancel? | Suchbegriffe für diese Sprache sind nicht verfügbar — ohne diese Sprache fortfahren, bearbeiten oder abbrechen? | Поисковые запросы для этого языка недоступны — продолжить без этого языка, изменить или отменить? |
| error_revoked_cycle_remediation | this research session was aborted earlier and is blocked from restarting; run `python3 research_pipeline.py reset SESSION_ID --cycle-id default` to recover | diese Recherchesitzung wurde zuvor abgebrochen und ist für einen Neustart gesperrt; führen Sie `python3 research_pipeline.py reset SESSION_ID --cycle-id default` aus, um sie zurückzusetzen | эта исследовательская сессия была прервана ранее и заблокирована для перезапуска; выполните `python3 research_pipeline.py reset SESSION_ID --cycle-id default`, чтобы восстановить её |
| footer_user_approved | Scope approved by: user | Rahmen genehmigt von: Benutzer | Рамки утверждены: пользователем |
| footer_autonomous_approved | Scope approved by: AI auto-approval | Rahmen genehmigt von: KI-Selbstgenehmigung | Рамки утверждены: автоматически ИИ |
| source_selection_question | Which sources should I read from? Pick as many as you need. | Aus welchen Quellen soll ich lesen? Wählen Sie so viele aus, wie Sie brauchen. | Из каких источников мне читать? Выберите столько, сколько нужно. |
| source_label_web | The public web | Das öffentliche Web | Открытый интернет |
| source_label_code | Your code | Ihr Code | Ваш код |
| source_label_knowledge_library | Your knowledge library | Ihre Wissensbibliothek | Ваша база знаний |
| source_label_document_folder | A folder of documents | Ein Dokumentenordner | Папка с документами |
| source_label_linear | Linear | Linear | Linear |
| source_label_confluence | Confluence | Confluence | Confluence |
| source_label_jira | Jira | Jira | Jira |
| bound_prompt_code | Which repository should I read, and from where? Give me a path. | Welches Repository soll ich lesen, und ab wo? Geben Sie mir einen Pfad. | Какой репозиторий мне читать и откуда? Укажите путь. |
| bound_prompt_web | Which parts of the web should I look at? | Welche Bereiche des Webs soll ich ansehen? | Какие части интернета мне просмотреть? |
| bound_prompt_knowledge_library | Which folders of your knowledge library should I read? | Welche Ordner Ihrer Wissensbibliothek soll ich lesen? | Какие папки вашей базы знаний мне читать? |
| bound_prompt_document_folder | Which folder should I read? Give me a path. | Welchen Ordner soll ich lesen? Geben Sie mir einen Pfad. | Какую папку мне читать? Укажите путь. |
| bound_prompt_linear | Which projects should I read? You can name projects, paste a link to a filtered set of issues, or let me read whatever Linear gives me. | Welche Projekte soll ich lesen? Sie können Projekte nennen, einen Link auf eine gefilterte Auswahl von Vorgängen einfügen, oder mich alles lesen lassen, was Linear bereitstellt. | Какие проекты мне читать? Вы можете назвать проекты, вставить ссылку на отфильтрованный список задач или разрешить мне читать всё, что доступно в Linear. |
| connect_prompt_linear | You haven't connected Linear yet. Shall I open your browser so you can authorize read-only access? | Sie haben Linear noch nicht verbunden. Soll ich Ihren Browser öffnen, damit Sie den Lesezugriff freigeben? | Вы ещё не подключили Linear. Открыть браузер, чтобы вы разрешили доступ только для чтения? |
| connect_outcome_question | Linear is connected. Use it as a source for this research, or leave it out? | Linear ist verbunden. Als Quelle für diese Recherche verwenden oder weglassen? | Linear подключён. Использовать его как источник для этого исследования или не включать? |
| resolved_bound_notice | That filter held {{count}} issues when you approved it, as of {{as_of}}. Those are the issues I will read — not whatever the filter matches later. | Dieser Filter enthielt {{count}} Vorgänge, als Sie ihn genehmigt haben, Stand {{as_of}}. Genau diese Vorgänge werde ich lesen — nicht das, was der Filter später ergibt. | На момент вашего одобрения ({{as_of}}) фильтр содержал {{count}} задач. Именно их я и прочитаю — а не то, что фильтр покажет позже. |
| bound_unscoped_option | Read whatever this source gives me | Alles lesen, was diese Quelle bereitstellt | Читать всё, что доступно в этом источнике |
| unavailable_web | Not available yet — I can't read the web through this list yet. | Noch nicht verfügbar — ich kann das Web über diese Liste noch nicht lesen. | Пока недоступно — я ещё не могу читать интернет через этот список. |
| unavailable_knowledge_library | Not available yet — I can't read your knowledge library yet. | Noch nicht verfügbar — ich kann Ihre Wissensbibliothek noch nicht lesen. | Пока недоступно — я ещё не могу читать вашу базу знаний. |
| unavailable_document_folder | Not available yet — I can't read document folders yet. | Noch nicht verfügbar — ich kann Dokumentenordner noch nicht lesen. | Пока недоступно — я ещё не могу читать папки с документами. |
| unavailable_linear | Not available yet — I can't read Linear yet. | Noch nicht verfügbar — ich kann Linear noch nicht lesen. | Пока недоступно — я ещё не могу читать Linear. |
| unavailable_slot_confluence | Not available — Confluence is planned but not built. | Nicht verfügbar — Confluence ist geplant, aber noch nicht gebaut. | Недоступно — Confluence запланирован, но ещё не реализован. |
| unavailable_slot_jira | Not available — Jira is planned but not built. | Nicht verfügbar — Jira ist geplant, aber noch nicht gebaut. | Недоступно — Jira запланирована, но ещё не реализована. |
| unreachable_notice | I couldn't reach this source. What would you like to do? | Ich konnte diese Quelle nicht erreichen. Wie möchten Sie fortfahren? | Не удалось получить доступ к этому источнику. Что вы хотите сделать? |
| unreachable_retry | Try it again | Erneut versuchen | Попробовать ещё раз |
| unreachable_drop | Leave this source out | Diese Quelle weglassen | Исключить этот источник |
| unreachable_rebound | Point me somewhere else | Auf etwas anderes verweisen | Указать другое место |
| empty_selection | No sources selected — I read nothing and claimed nothing. | Keine Quellen ausgewählt — ich habe nichts gelesen und nichts behauptet. | Источники не выбраны — я ничего не прочитал и ничего не утверждаю. |
| spike_stop | This reads as an investigation rather than research, so I'm stopping here rather than picking sources. | Das liest sich eher als Untersuchung denn als Recherche — ich höre hier auf, statt Quellen auszuwählen. | Это похоже на разбор, а не на исследование, поэтому я останавливаюсь и не выбираю источники. |

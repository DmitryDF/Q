"""Code adapters and helpers for the /research skill flow controller.

This docstring is the package's per-slice module manifest. **Two topics ship
modules here and both number their slices `S<n>`**, so every entry names its
topic — an unqualified "S3 ships…" meant one thing in 2026-06 and a different
thing in 2026-08, which is precisely the collision the plan flagged between its
own `A<n>` actions and the design's.

research-scope-framing-ui S3 ships:
  * scope_draft_port — AI-judgment port (ABC + FakeScopeDraftAdapter)
  * step25_fc_context — deterministic bounded-context extractor

research-scope-framing-ui S5 ships:
  * scope_draft_adapter_claude — production ScopeDraftPort (ClaudeScopeDraftAdapter)
  * adjacent_points_port — Ultra Deep adjacent-points port (ABC + FakeAdjacentPointsAdapter)

research-scope-framing-ui S6 ships:
  * internal_kb — Internal KB discovery (walk-up + _RESEARCH scan) + Q13 path-validation
                  + Convention B marker emitter + fc_cycles: preservation
  * locale_native_subpass — per-language search-terms regeneration port
                            (FakeLocaleNativeAdapter + ClaudeLocaleNativeAdapter)

research-source-adapters S3 ships the admission contract — one module per
producer-side obligation, plus the first (deliberately thin) adapter:
  * locator_grammar — per-source-kind locator grammar with NAMED, ordered parts,
                      so an incomplete locator is detectable rather than shorter
  * scope_record — the immutable declaration; kind-dispatched containment checked
                   on fully-resolved paths BEFORE any read, its cross-surface JSON
                   form, and the non-secret `connection_id` field
  * admission_record — the durable record of BOTH admission outcomes: evidence
                       entries keyed on the rendered pin, degradation records keyed
                       on item_id (a degraded item has no pin by construction)
  * source_port — the port every source passes through: mints the three-part pin,
                  owns all four bounds, refuses a CLAUDE.md source identity, and
                  selects + stamps the evidence path
  * adapters/code_base — the repository adapter (read-only; yields lazily, holds
                         no bound constant, chooses no evidence branch)

  What S3 deliberately does NOT ship: `credential_path.py` (S8 — this slice builds
  only the non-secret `connection_id` field on `DeclaredSource`, and nothing here
  resolves a connection), the per-claim grading rule (S10 — DESCOPED, see below),
  the picker (S4), the report (S11) and the run marker (S13).

  NOTE 2026-08-31 — the per-claim grading rule named above is DESCOPED. The
  operator decided on 2026-08-30 that a per-claim scoring layer for internal
  claims has no value for their use and does not belong to this topic; the locked
  Guiding Policy was re-locked with its fifth binding (the grading rule) deleted,
  and design decisions A8, A13 and A25 are withdrawn. Record: the spine's `## Q&A`
  Q22 (the decision and its fallout) and Q23 (the disposition of the code S10
  already shipped, which is: delete it).

  This line is left in place rather than removed because it is a per-slice manifest
  entry — it records what S3 shipped and what it deferred, which is still an
  accurate account of S3. What is no longer accurate is the implication that S10
  is a slice still owed.

  REMOVED 2026-09-01 by slice D5, AND PROMOTED — harness PR #188, merge
  `f719319`, tag `green-20260901-1957`. `internal_claim_grader.py`,
  `claim_grading_record.py`, `internal_claim_grounds.py`, the close-time gate in
  `_factcheck_engine.py` and its call site are all deleted, and the `GRADING`
  advisory TYPE is deregistered from both halves of its unguarded mirror pair.
  Three close-time axes remain on the research fact-check path. The removal is in
  the config source, so the deploy step cannot restore the layer — the six files
  have no managed entry to materialise from.

  *(This passage previously read "IN THE LIVE TREE ONLY — NOT YET PROMOTED" and
  warned that an apply would reverse D5 in its entirety. That was true when
  written and was falsified by the promotion an hour later — then shipped in that
  false state, because the correction reached the spine and TODO.md and not this
  file. An independent audit caught it. Recorded rather than quietly overwritten:
  it is the ninth instance in this slice's history of a fix landing in one
  sibling and not another, and the first to reach the merged source.)*

  WHAT THE REMOVAL DID NOT DO, stated because a green suite invites the opposite
  reading. The gate was the ONLY production consumer of the durable admission
  store's `get_evidence`. Deleting it removed the only thing in production that
  would ever have NOTICED the store-identity divergence — writer stamps the store
  from the research filename, reader stamps it from the clock, so the two never
  meet — and D5 did NOT repair that divergence. It is unchanged and now
  unobserved, latent for S11, which reads the same records, and it is the ground
  of slice D6. A successor finding these suites green has not found a working
  join; they have found a join nothing looks at.

  *(An earlier version of this note said the gate "can raise a run's verdict to
  INCOMPLETE". An independent checker refuted it: the floor could not fire in
  production, because every internal citation resolved unreached and graded
  UNKNOWN while the floor required WEAK on all three axes. What the gate actually
  did was write a `_GRADING_` record — independently of the grades, on runs whose
  content rounds passed and which cited an internal source — and fold to
  INCOMPLETE on its own error path. Recorded rather than deleted: the removal was
  justified by what the layer cost, not by a harm it was not doing.)*

  What the descoping does NOT reach: the admission layer in this package. The pin
  still carries the version read — `code` pins `rev-parse HEAD` plus a `dirty`
  flag, `linear` pins `updatedAt` and refuses to mint a citation without one — so
  a claim can still be checked against its source months later. What is gone is
  scoring how strongly, not the ability to tell whether the source moved.

research-source-adapters S4 ships the declaration's author — the first consumer of
S3's contract, which amends none of it:
  * source_picker — the source catalogue whose selectability is DERIVED at call
                    time rather than stored (a stored flag would be a second
                    authority on admissibility). AS OF S4 the one input was
                    `scope_record.REGISTERED_KINDS`; two more were added later —
                    see the S7 and code-driver entries below, which amend this
                    line. The entries in this manifest are per-slice and describe
                    what each slice shipped, so read to the end before relying on
                    one;
                    per-class bound prompts and record assembly; a selection-time
                    reachability probe that never opens a file; and the two terminal
                    outcomes (spike stop, empty selection), neither of which carries
                    a record — so "nothing is registered" is structural.

  What S4 deliberately does NOT ship: any change to the six S3 modules above (it
  imports them), a renderer for the Step-3 bundle (that prose lives in
  `~/.claude/rules/research-scope-framing.md`), and the source classes themselves —
  web is S6's, the knowledge library and document folders are S7's, Linear is S8's.

research-source-adapters S6 brings the LAST unadmitted source class behind the
contract, and gives the port its FIRST production caller (design-A29):
  * adapters/web — the web adapter. Its fetch is injected, because the loop that
                   owns the per-URL fair share and the wall-clock cap belongs to
                   the fact-check engine and cannot move into an adapter that
                   must hold no bound.
  * `web` registered as a locator kind (`url`) and a source kind (URL containment:
    host exact after folding, path at-or-under, subdomains refused fail-closed).
  * The port's per-item rules became PER KIND — bounds, oversize policy, re-open
    instruction, the `CLAUDE.md` prohibition and pin rendering — plus a content
    channel, because until S6 the port read an item and could hand a caller
    nothing OF it. That is the same gap as "no production caller" seen from the
    other side.
  * The engine's web ingest now reads INSIDE `AdmissionPort.admit`, so a cited URL
    outside an approved declaration is refused rather than fetched.

  With `web` registered, `code` and `web` are both selectable; the remaining
  classes still say so per entry rather than hiding themselves.

research-source-adapters S7 adds the two ON-DISK classes and CLOSES THE ONE
BYPASS the design names as the precedent not to follow (design-A21, A24, A31).
It is the first slice where adding a class and removing a bypass are one job:
a document folder had no read path at all (addition), while the knowledge library
had a working one that read whatever it found with NO DECLARATION (subtraction).
  * adapters/document_folder — carries `OnDiskFolderAdapter`, the on-disk read
                   both classes share. A MOUNTED CLOUD DRIVE gets no branch:
                   design-A31 admits it unconditionally as the ordinary folder it
                   is, and a test for one would make "ordinary" conditional.
  * adapters/knowledge_library — one line of behaviour over that base, because
                   reading a file on disk does not vary by which class declared
                   it. What differs is the DECLARATION surface, not the read.
  * `knowledge_library` + `document_folder` registered as locator kinds
    (`path`, `line`) and source kinds (path containment, shared with `code`;
    neither may be declared `unscoped`; the `CLAUDE.md` declaration refusal now
    covers every path-shaped kind rather than `code` alone).
  * `KindRules.path_shaped` was answering THREE questions and S7 is where that
    first gave a wrong answer: the shipped `local-file` marker carries no version,
    which neither existing render branch could produce. Split into `render_form`
    (which citation shape), `citation_prefix` (the shared vocabulary, stated
    rather than implied by a shared name) and `version_is_revision` (what
    `version` MEANS — a commit a tree can be clean at, or the instant a file was
    read). `code` and `web` render byte-identically.
  * A NEW `CLAUDE.md` key, `research_library_folders:`, with exactly ONE reader.
    Deliberately not `knowledge_library:`, which already has two readers meaning
    a KL root by it (`/double-check`, `/ninja-fix`).
  * Availability gained a ROUTE input, so it was no longer registration alone.
    Registering a kind makes it declarable everywhere while a reader may exist on
    one route only. (A THIRD input — whether a driver exists at all — arrived with
    the code driver below; see that entry.)

    **The example this entry used to give is now the opposite of current
    behaviour, and is corrected rather than left in the past tense.** It read that
    without the route input "a person could have ticked 'your knowledge library'
    on an open-web route and got a run where it was never opened". That premise
    was wrong about this codebase: the reader (`declared_read._adapter_for`)
    dispatches by KIND with no route conditioning, so an open-web route always
    could read a declared library. Q26 (2026-09-11) widened both S7 classes to
    every route accordingly, and ticking a library on an open-web route is now
    supported and read. The ROUTE INPUT ITSELF REMAINS and is still load-bearing —
    it is what keeps `code`/`web`/`linear` off the Internal-KB route — so this
    entry stands; only its illustration was stale. A reader consulting this
    manifest for current behaviour would otherwise have been misled.
  * The Internal-KB route now RUNS source selection and reads through
    `AdmissionPort.admit()`; its synthesis consumes what the port returned.

  KNOWN CEILING, stated because a prose pointer must not read as enforcement:
  nothing gates the tool boundary — no `PreToolUse` hook covers `Read` for source
  paths — so this route's declaration is FOLLOWED, NOT ENFORCED. A read that
  ignores the route entirely is still possible. Gating it is design-A18, which is
  WITHDRAWN and routed to `/clarification --from`. So design-A24 is substantially
  closed and not fully closed.

code-source-driver-bounded-read S1 reads the ONE class that was offered with
nothing behind it, and bounds the loop that reads the other two. It is the same
job seen from both sides: a capability that had never run, and a shipped loop that
ran without a ceiling.
  * declared_read — THE one declared-source read loop, moved out of `internal_kb`
                   and made route-neutral: enumerate → `admit` → compose, with a
                   `code` arm beside the two on-disk ones, dispatch per KIND (not
                   per declared source, which would read every file twice), and
                   ONE aggregate run budget for the whole run. `internal_kb` keeps
                   its three public names as delegates. The name moved with the
                   responsibility — `internal_kb` reading open-web repositories
                   would be Responsibility Alignment failing out loud.
  * `source_port.bounded_items` — the two ENUMERATION bounds, extracted from
    `AdmissionPort.run()` so a driver can consume them without re-deriving them.
    A free function, not a port method, because the divergence test's stub port
    holds `admit` alone. `run()` is now its consumer and is otherwise unchanged;
    it still discards every result and still has no production caller.
  * Relevance ordering. Both path-shaped adapters enumerate `sorted()`, so a
    ceiling on its own would have returned whatever sorted first — the defect this
    codebase already records as observed and fixed for the web class. The reader
    takes the framed question and spends its budget by path/name bearing on it.
    METADATA ONLY: nothing here opens a file, so no read happens outside the port.
    The ranking rule itself is editorial and is declared as such.
  * Availability gained its THIRD input: whether a production driver exists at
    all, derived from `kind_reachability.KNOWN_UNREACHABLE`. Registration answers
    "declarable", the route set answers "read HERE", and neither answers "read
    ANYWHERE" — which is how `code` stayed tickable for four slices with nothing
    opening a repository. This input shipped AFTER the driver, so it was a no-op
    on the shipped catalogue the day it landed; landing it first would have
    removed `code` from three routes with no reason slot to render.
  * `KNOWN_UNREACHABLE` is now EMPTY, and the deletion rode the driver's own
    commit: the check that catches a stale exemption runs only on the deploy path,
    so a split commit clears every gate that fires automatically, and then fails
    at promotion.

  WHAT THIS SLICE DOES NOT SHIP, stated so "bounded" is not misread as
  "conformant": design-A20's NARROWED-EXCERPT half is still unbuilt for every
  path-shaped adapter — all read whole files and pin a `1-N` whole-item locator.
  That is a pre-existing condition inherited unchanged, not a licence taken here;
  completing it is **design-A20's own** unbuilt half *(re-attributed 2026-09-01:
  this read "design-A13's locator question"; A13 is withdrawn with the grading
  layer, but the narrowed-excerpt gap is A20's and stands)*. And the A18 ceiling above
  applies verbatim to the new routes: the declaration is FOLLOWED, NOT ENFORCED.

research-source-adapters S8 — the Linear kind, and the machinery a credentialed
  remote needs. Split across sessions, and the split is visible in what each one
  can honestly claim.

  Session 1 REGISTERED the kind: `locator_grammar` gained a `linear` locator
  (required `issue`, optional `comment` — the first optional part here, so
  `parse()` is undefined for it); `scope_record` gained `KIND_LINEAR`, a
  containment arm, a display arm, the `resolved_members` carrier and
  `linear_scope`; `source_port` gained a `KIND_RULES` row; `source_picker` gained
  a probe arm and an EXPLICIT route set. The citation vocabulary gained its first
  new marker pair since S3, across all three loci under the drift guard.

  Session 2 built the machinery, and deliberately BESIDE the adapter rather than
  inside it — which is the Guiding Policy's own test, since Confluence and Jira
  must not pay this cost again:
  * `credential_path` — a source-agnostic port (in-memory double, OS-keychain
    adapter). It holds no Linear vocabulary at all; a test asserts that. This is
    where a token lives so that the approval record, which may be committed or
    shared, has nowhere to put one.
  * `mcp_transport` — an OAuth-2.1 + PKCE client that registers its OWN client
    against a remote MCP server. It never reuses the harness's stored MCP token
    (asserted structurally), requests `read` and only `read`, and REFUSES a grant
    that comes back wider — checking the grant, not the request, because a server
    may give more than was asked for. Each remote failure mode degrades under its
    own name rather than collapsing into "the read failed".
  * `connect_gate` — the connect-now step (U4), and the freeze a query-shaped
    bound needs. A filtered-issues link is resolved ONCE at selection time and
    frozen into the record, because `check()` is a pure predicate holding no
    transport. The person then approves the RESOLVED SET: the bundle names the
    count and the as-of time, and a line showing only the link is NOT approvable.

  Session 3 READS it, and that is what turns the registration into a fact:
  * `adapters/linear.py` — three things and no more, exactly as the on-disk
    adapters. It declares `can_reopen_without_credentials = False`, and that one
    line is what routes every Linear read to the port's CAPTURED evidence branch,
    so a checker holding no authorization can still read what a claim rests on.
    It enumerates a link bound's FROZEN membership and never re-resolves the
    filter — a live re-resolve would look like freshness and would be a defect,
    since `check()` tests the frozen set.
  * The `declared_read` arm — the exact omission that cost `code` four slices.
    A declared Linear source with no authorization refuses BY NAME rather than
    reading nothing and saying nothing.
  * A durable admission store on the production read path. The reader defaulted to
    an in-memory one, so a captured excerpt died at process exit — true in every
    unit test, false in production, which is the only place it matters.
  * Each remote failure mode reaches the report body in plain words next to the
    affected material, and each claim carries a plain-words sentence saying how a
    reader can go and check it — two different sentences, because a copy stored by
    the run and a source anyone can re-open are not the same position to be in.
  * An artifact-wide scan proving no secret from the authorization reaches the
    approval record, the findings report, the evidence records, or the committed
    files. Its own gate plants a sentinel in each and requires a failure naming it.

  THE IDENTITY IS NOT A FIELD, and this cost a correction. The Session-2 live
  round-trip established that neither `list_issues` nor `get_issue` returns an
  `identifier`: an issue carries a UUID `id` and a `url`, and the `ENG-123` form
  lives only in the URL. The freeze and the containment check must derive it the
  same way or every admission is refused as outside the person's own declaration,
  so the derivation has ONE home beside `LINEAR_ISSUE_RE` and the connect gate's
  extractor reads `url` before `id`.

  WHAT S8 DOES NOT SHIP, stated so the green suite is not misread. A6's growth
  rungs (i) and (ii) run against a stub, and rung (iii)'s discovery half against
  the live server; the registration half and rung (iv)'s browser round-trip need
  the operator and are NOT faked. The unit suites above drive the adapter through
  a transport double: the walk against the operator's real workspace, in both bound
  shapes, is the closing verification and is what upgrades this from doubles.

Flow control (routing prompt, Edit FSM, approval bundle) lives in the
markdown skill at Skills/research-en.md delegating to
~/.claude/rules/research-scope-framing.md.
"""

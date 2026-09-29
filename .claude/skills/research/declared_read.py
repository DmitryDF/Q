"""The one declared-source read loop — enumerate, admit, compose. Route-neutral.

code-source-driver-bounded-read S1 / actions A2 and A3.

WHY THIS MODULE EXISTS RATHER THAN A SECOND COPY OF THE LOOP
------------------------------------------------------------
There was exactly ONE generic declared-source read loop in production, and it
lived in :mod:`internal_kb` and served the Internal-KB route only. The three
open-web routes where a person can tick "your code" had no declared-source read
loop **at all** — so a person could pick their code base, give a repository path,
watch it get probed, see it in the approval bundle, approve it, and get research
that never opened the repository.

Cloning the loop to serve `code` would have closed that for one class and left
the two shipped classes unbounded — the same failure one level down. So the loop
MOVED here, `code` joined the two kinds already dispatched from it, and the run
ceiling now applies to all three at once. :mod:`internal_kb` keeps its three
public names as delegates so the Internal-KB route is untouched.

The name changed with the responsibility. `internal_kb` would otherwise be the
module reading open-web repositories, which is Cockburn's Responsibility
Alignment Test failing out loud: the name, the responsibility and the signature
stopped agreeing the moment the loop served three kinds on two route families.

WHAT THIS MODULE DOES NOT DECIDE
--------------------------------
* **Route admissibility.** :func:`source_picker.build_record` already refuses to
  assemble a declaration for a class the calling route cannot read, and a second
  authority on the same question is how a class comes to be offered where nothing
  reads it. Whatever reaches a :class:`ScopeRecord` here has already been decided
  admissible; this module reads what it is given.
* **The bounds.** Enumeration depth and the item ceiling are consumed from
  :func:`source_port.bounded_items`; the per-item and aggregate byte budgets are
  the port's, supplied through its own public :func:`source_port.run_budget` seam.
  Nothing here re-derives a ceiling, so a driver cannot widen one.
* **What a source yields.** Nothing here judges it — and since 2026-09-01 nothing
  downstream does either: the per-claim grading layer was descoped and deleted by
  slice D5 (spine Q22/Q23, design-A8/A13/A25 withdrawn).

THE READ IS PERFORMED BY THE PORT — AND THE TOOL BOUNDARY IS UNGATED
---------------------------------------------------------------------
Every candidate reaches ``source_port.AdmissionPort.admit()``, which owns the
declared-scope check, the ``CLAUDE.md`` prohibition, the four bounds and the
evidence branch. :func:`synthesize_from_admissions` composes the body from **what
the port returned** rather than re-reading the files, which is what makes the
divergence test meaningful.

**THIS IS A CONTRACT ON THE READ PATH, NOT ON EVERY PHYSICALLY POSSIBLE READ.**
Nothing gates the tool boundary: there is **no PreToolUse hook on Read** for
source paths, so a read that ignores this module entirely is still possible and
this module cannot stop it. Gating that boundary is **design-A18**, which is
**WITHDRAWN and routed to `/clarification --from`**.

So the declaration here is **FOLLOWED, not ENFORCED**. Do not cite this module as
containment, and do not let a prose pointer elsewhere read as enforcement it does
not have — skill text has no enforcement power (``code_first_architecture.md``:
Code > Rules > Skill text). This is a large improvement on routes that read
nothing at all, and it is less than containment.

WHAT IS STILL NOT BUILT, STATED RATHER THAN IMPLIED
----------------------------------------------------
design-A20 asks adapters to select **narrowed excerpts** rather than bulk-ingest.
**No shipped path-shaped adapter does** — all read whole files and pin a `1-N`
whole-item locator. That half has been unbuilt since the adapters shipped and is
inherited here unchanged; completing it is **design-A20's own** unbuilt half.
*(This read "design-A13's locator question" until 2026-09-01. A13 is withdrawn
with the per-claim grading layer — but the narrowed-excerpt gap is A20's and is
NOT withdrawn, so the work is re-attributed rather than retired with A13.)* A
whole-file read pinned `1-N` is truthful about what was read. **Do not read "the
loop is now bounded" as "the adapters are now A20-conformant" — they are not.**
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import scope_record as _scope


# --------------------------------------------------------------------------- #
# What a read produced.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AdmittedReading:
    """One item the port admitted, as the port returned it.

    `content` is the port's, not a re-read. A declaration built, consulted, and
    then followed by a direct read of the same file would be BEHAVIOURALLY
    INDISTINGUISHABLE for any in-scope, within-budget item, so "reads through the
    port" cannot be proven by comparing output. It is proven by the divergence
    test instead: stub `admit()` to return altered content and the synthesis must
    reflect the alteration.
    """

    item: str
    content: str
    marker: str
    # Which evidence path the PORT chose for this reading (A13). Carried here so
    # the report can say, next to the claim, how a reader would go and check it.
    # Empty when the caller did not thread it — rendered as nothing rather than
    # as a guess, because a wrong disclosure is worse than an absent one.
    evidence_path: str = ""

    def evidence_sentence(self) -> str:
        """How a reader can go and check this claim, in plain words (A13, U7/U8).

        **Two different sentences, and that difference is the whole point.** A
        claim drawn from a source anyone can re-open is not in the same position
        as one whose only surviving evidence is a copy this run stored — the
        second may no longer match the source, and a reader deciding how much
        weight to give it needs to know which they are looking at. A single
        constant string next to both would be a disclosure that discloses nothing.

        Plain words, never a code or a symbol: `captured` and `original` are this
        design's vocabulary, and a reader who knows nothing of it must still be
        able to act on the sentence.
        """
        from . import admission_record as _rec

        if self.evidence_path == _rec.EVIDENCE_CAPTURED:
            return ("Checked against a copy of the source stored when this ran — "
                    "the source itself needs a login this report's reader may not "
                    "have, and it may have changed since.")
        if self.evidence_path == _rec.EVIDENCE_ORIGINAL:
            return "Can be checked by re-opening the source itself."
        return ""


@dataclass(frozen=True)
class RefusedRead:
    """One item that was not read, NAMED.

    A read refused silently reaches a person as a finding that simply is not
    there, indistinguishable from the source having had nothing to say — which
    would satisfy the letter of the contract and none of its purpose. So a refusal
    carries the thing refused and the reason, and the flow surfaces it.
    """

    item: str
    obligation: str
    reason: str

    def render(self) -> str:
        return f"refused: {self.item} — {self.reason} [{self.obligation}]"


def _never_merge_reasons() -> frozenset:
    """Reasons that stay one-per-line however many times they occur.

    `OBLIGATION_ENUMERATION_BOUND` spans TWO branches with opposite needs
    (`source_port.py:736-749`): the item-count ceiling `return`s and is something a
    person acts on, while the depth bound `continue`s and can repeat without limit
    on any deep tree. Excluding the OBLIGATION would force the depth refusals
    unmerged, and the ordering below would then hoist an unbounded run of them
    ABOVE the bulk — into the section reserved for entries needing a decision. So
    the exclusion is on the REASON, matched against the exact constant rather than
    by substring, and the depth class groups normally.
    """
    from . import source_port as _port

    return frozenset({
        f"enumeration item-count bound MAX_ITEMS={_port.MAX_ITEMS} "
        f"reached; this item and any after it were not read",
    })


def _refusal_group_key(refusal: "RefusedRead", index: int):
    """What two refusals must share to render as one entry.

    The reason is taken with the refusal's OWN item stripped from the front — data
    the record already carries, so no reason-code has to be threaded through
    `AdapterError` and `Degradation`. Grouping on the raw reason would collapse
    nothing in the class that dominates a real repository, because that reason
    interpolates the path (`code_base.py:222-224`) and so is unique per file.

    Every case where the strip does not apply leaves the refusal on its own line:
    a reason that never embedded the path, a synthetic refusal whose `item` is a
    composed sentence, or a resolved-vs-unresolved path mismatch. That is
    under-grouping, which costs a merge that would have been nice rather than
    producing one that is wrong.
    """
    if refusal.reason in _never_merge_reasons():
        return ("\x00never-merge", index)
    reason = refusal.reason
    if refusal.item and reason.startswith(refusal.item):
        reason = reason[len(refusal.item):].lstrip(" -—:")
    return (refusal.obligation, reason)


@dataclass(frozen=True)
class DeclaredReadResult:
    """What the run read, and what it was refused."""

    readings: Tuple[AdmittedReading, ...] = ()
    refusals: Tuple[RefusedRead, ...] = ()

    @property
    def refusal_lines(self) -> Tuple[str, ...]:
        """One line per refusal CLASS, with the unmerged ones first.

        Two things this deliberately does NOT do. It never truncates: every member
        of a merged entry is named, because the contract that a refused source
        comes back BY NAME is stated in `RefusedRead` above and repeated twice
        outside this file. And it never touches `self.refusals`, which stays the
        per-item record the `--json` branch serializes — grouping is presentation,
        so it lives here and nowhere else.

        Unmerged entries render FIRST. A refusal a person must act on is
        structurally never part of a merged run (a missing authorization can arise
        once per run; the aggregate-budget notice ends the run), so putting the
        merged bulk last places every one of them above it. A lone junk file lands
        there too — this is a floor on findability, not a filter.
        """
        groups: Dict[object, List[RefusedRead]] = {}
        for index, refusal in enumerate(self.refusals):
            groups.setdefault(_refusal_group_key(refusal, index), []).append(refusal)

        unmerged: List[str] = []
        merged: List[str] = []
        for key, members in groups.items():
            if len(members) == 1:
                unmerged.append(members[0].render())
                continue
            names = ", ".join(m.item for m in members)
            merged.append(f"refused: {len(members)} items — {key[1]} "
                          f"[{members[0].obligation}] — {names}")
        return tuple(unmerged + merged)


# --------------------------------------------------------------------------- #
# Adapter dispatch.
# --------------------------------------------------------------------------- #

#: Kinds whose production driver lives OUTSIDE this package, with WHERE.
#:
#: `web` is read by the fact-check engine's own ingest loop, which owns the
#: per-URL fair share and the wall-clock cap and therefore cannot move into a
#: reader that holds no bound. A kind listed here is skipped by an EXPLICIT NAMED
#: CHECK rather than being allowed to fall through to `_adapter_for`'s raise: a
#: fall-through would turn "this kind is read elsewhere" into a refusal line
#: telling a person their web sources were not read, which is false.
DRIVEN_ELSEWHERE: Dict[str, str] = {
    _scope.KIND_WEB: ("the fact-check engine's web ingest reads this kind inside "
                      "AdmissionPort.admit() during verification"),
}


class MissingAuthorization(RuntimeError):
    """A declared kind needs a credential this run does not hold.

    Distinct from the no-reader `ValueError` on purpose. That one means nothing
    can EVER read this kind and is a programming error, so it stays fatal. This
    one means the reader exists and the run is not authorized — an ordinary
    condition a person can act on, which becomes a recorded refusal naming the
    source rather than aborting a run that has other declared kinds to read.
    """


def _adapter_for(kind: str, projects_root: Path, *, linear_adapter=None):
    """Which adapter reads which declared kind.

    `linear_adapter` is KEYWORD-ONLY. It is an injected dependency rather than a
    third thing every caller needs, and making it positional would silently change
    the arity of a function that existing callers and test doubles already match on
    two positional parameters.

    A kind registered with no entry here fails LOUDLY rather than being skipped —
    a silently unread declared class is the failure this whole module exists to
    remove. A kind read by a driver outside this package is filtered out by the
    caller against :data:`DRIVEN_ELSEWHERE` BEFORE it reaches this function, so
    reaching here with such a kind is a programming error and is treated as one.
    """
    from .adapters.code_base import CodeBaseAdapter
    from .adapters.document_folder import DocumentFolderAdapter
    from .adapters.knowledge_library import KnowledgeLibraryAdapter

    if kind == _scope.KIND_CODE:
        return CodeBaseAdapter()
    if kind == _scope.KIND_KNOWLEDGE_LIBRARY:
        return KnowledgeLibraryAdapter(projects_root=projects_root)
    if kind == _scope.KIND_DOCUMENT_FOLDER:
        return DocumentFolderAdapter(projects_root=projects_root)
    if kind == _scope.KIND_LINEAR:
        # S8 / A11 — the arm whose ABSENCE cost `code` four slices: a kind
        # registered, offered, probed and approved, and then never read.
        #
        # **A remote kind needs a credential this function is not given**, which
        # every on-disk kind above does not. Rather than reach into a credential
        # store from a dispatch table — which would put a secret-bearing side
        # effect inside a function whose whole job is a lookup — the caller
        # supplies a built adapter through `linear_adapter`.
        #
        # A `None` raises :class:`MissingAuthorization` rather than the generic
        # no-reader `ValueError`, and the distinction is load-bearing: the caller
        # turns THIS one into a recorded refusal naming the source, while the
        # generic one stays fatal. "No reader exists" is a programming error and
        # must be loud; "this run holds no credential" is an ordinary condition a
        # person can act on, and aborting the whole run for it would take the
        # other declared kinds down with it.
        if linear_adapter is None:
            raise MissingAuthorization(
                "the declaration names a Linear source but this run holds no "
                "Linear authorization; connect Linear and run this again")
        return linear_adapter
    raise ValueError(
        f"no reader for declared kind {kind!r}; a declared class with no reader "
        "would be silently unread, which is the failure this module was written "
        "to remove")


# --------------------------------------------------------------------------- #
# Relevance ordering (A3).
#
# A ceiling on a SORTED enumeration is not a bounded read, it is an alphabetical
# accident: both path-shaped adapters enumerate `sorted()`, so a run that simply
# stopped at its ceiling would return whatever sorted first. This codebase already
# records that exact defect as observed and fixed for the web class —
# "position in the list, not relevance, decided what got verified"
# (`_factcheck_engine.py:942`) — so applying a ceiling here without addressing it
# would reintroduce a failure the project has diagnosed once.
#
# EDITORIAL, and declared as such. Matching a framed question's terms against a
# path has no expert source and is calibrated by nothing. It is metadata-only by
# construction: only `item.target` is inspected, so no read happens outside the
# port and no content is pre-scanned.
#
# BOUNDED BY CONSTRUCTION, and the cost is stated rather than glossed. Ranking
# needs the candidates in hand, so the stream is materialised — which is at most
# `MAX_ITEMS` paths, because that is the ceiling `bounded_items` applies before
# anything reaches here. It is a real departure from the adapter's lazy-streaming
# intent, bounded by the same constant that makes the lazy stream finite.
# --------------------------------------------------------------------------- #

#: Tokens too common to discriminate between paths. Deliberately tiny: a long
#: stoplist is a second editorial surface, and a term that matches everything
#: costs nothing here because it lifts every candidate's score equally.
_STOPWORDS = frozenset((
    "the", "and", "for", "with", "from", "how", "what", "why", "does", "did",
    "are", "was", "were", "this", "that", "into", "than", "then", "when",
    "which", "who", "whom", "its", "our", "their", "have", "has", "had",
))

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def relevance_terms(query: Optional[str]) -> Tuple[str, ...]:
    """The terms a framed question contributes to ordering.

    Tokens of three characters or more, case-folded, stopwords dropped, order
    preserved and duplicates removed. An empty result means "no terms", which is
    what makes :func:`rank_candidates` fall back to enumeration order.
    """
    if not query:
        return ()
    out: List[str] = []
    for tok in _TOKEN_RE.findall(query.lower()):
        if len(tok) < 3 or tok in _STOPWORDS or tok in out:
            continue
        out.append(tok)
    return tuple(out)


def relevance_score(target: str, terms: Sequence[str]) -> int:
    """How much a candidate's PATH bears on the framed terms. Metadata only.

    A term found anywhere in the path scores 1; a term found in the basename
    scores 2, because a file named for the thing asked about bears on it more
    than one that merely sits under a directory named for it.
    """
    if not terms:
        return 0
    lowered = target.lower()
    base = Path(target).name.lower()
    score = 0
    for term in terms:
        if term in base:
            score += 2
        elif term in lowered:
            score += 1
    return score


def rank_candidates(items: Sequence, terms: Sequence[str]) -> List:
    """Candidates in the order the budget should be spent on them.

    A STABLE sort on the negated score, so candidates that bear equally on the
    question keep the adapter's enumeration order — and, with no terms at all,
    the order is byte-identical to what it was before ordering existed.
    """
    if not terms:
        return list(items)
    return sorted(items, key=lambda it: -relevance_score(it.target, terms))


# --------------------------------------------------------------------------- #
# The loop.
# --------------------------------------------------------------------------- #

def read_declared_sources(
    scope,
    projects_root: Path,
    *,
    port=None,
    store=None,
    run_id: str = "declared-read",
    query: Optional[str] = None,
    linear_adapter=None,
) -> DeclaredReadResult:
    """Read every declared source **through** ``port.admit()``, under the bounds.

    This is the one loop. The port IS the read, not a gate in front of one:
    nothing here opens a declared file directly — enumeration comes from the
    adapter through :func:`source_port.bounded_items`, and every candidate passes
    through ``admit()``.

    `query` is the framed question. Supplied, it orders how the run spends its
    budget; omitted, the order is the adapter's own and nothing else changes.

    `port` is injectable so the divergence test can substitute one; production
    passes none and gets the real ``AdmissionPort``.

    **Dispatch is per KIND, not per declared source.** Each adapter's
    ``enumerate_within`` already iterates every selector its kind declared, so
    dispatching per source would enumerate a kind once per declared source of it
    and read every file twice.

    **The aggregate budget is ONE budget for the whole run**, not one per kind.
    `MAX_RUN_BYTES` bounds "all admitted excerpts in one run" and exists to
    protect a downstream ceiling that is shared across every source — so a
    per-kind budget would let a three-class declaration take three times the
    number that ceiling was chosen against. The limit is resolved through the
    port's own public seam, at the TIGHTEST of the ceilings of the kinds actually
    read, so this can only ever narrow and never widen.

    An out-of-scope path, an over-budget item and a bound reached all become a
    :class:`RefusedRead` the caller surfaces. A failure on ONE item never aborts
    the run — it degrades that item and the others still read. **Since S8 that
    holds one level up too**: a declared KIND this run cannot authorize becomes a
    refusal naming it, and the other declared kinds are still read.
    """
    from . import admission_record as _rec
    from . import source_port as _port

    if port is None:
        port = _port.AdmissionPort(store or _rec.InMemoryAdmissionRecordStore())
        # NOTE (A12b): the in-memory default above is correct for a LIBRARY call
        # with no store supplied — a caller driving this in-process gets its
        # records in-process. It is NOT correct for the production run, where a
        # captured excerpt that dies at process exit makes the captured-copy
        # promise true in tests and false in production. The production entry
        # point (`_cmd_read`) therefore constructs a durable store and passes it;
        # see the comment there for why the decision lives at the entry point
        # rather than by changing this default.

    readings: List[AdmittedReading] = []
    refusals: List[RefusedRead] = []
    terms = relevance_terms(query)

    kinds: List[str] = []
    for declared in scope.sources:
        if declared.kind in DRIVEN_ELSEWHERE:
            # Explicit and named. Skipping silently would be the same defect this
            # module removes; refusing would tell a person something false.
            continue
        if declared.kind not in kinds:
            kinds.append(declared.kind)
    if not kinds:
        return DeclaredReadResult()

    # A kind this run cannot authorize becomes a RECORDED REFUSAL and drops out of
    # the walk; every other declared kind is still read. Building the map eagerly
    # and letting the exception escape would have taken the whole run down for one
    # unauthorized source — the same all-or-nothing failure the per-item degradation
    # path exists to avoid, reintroduced one level up.
    adapters = {}
    for kind in kinds:
        try:
            adapters[kind] = _adapter_for(kind, projects_root,
                                          linear_adapter=linear_adapter)
        except MissingAuthorization as e:
            refusals.append(RefusedRead(
                item=f"every declared {kind} source",
                obligation=_port.OBLIGATION_READABLE,
                reason=str(e)))
    kinds = [k for k in kinds if k in adapters]
    if not kinds:
        return DeclaredReadResult(readings=(), refusals=tuple(refusals))
    limit = min(_port.run_budget(kind=kind).limit for kind in kinds)
    budget = _port.run_budget(limit)
    counter = _port.ItemCounter()

    for kind in kinds:
        adapter = adapters[kind]
        candidates: List = []
        for produced in _port.bounded_items(adapter, scope, counter):
            if isinstance(produced, _port.Degradation):
                refusals.append(RefusedRead(item=produced.item,
                                            obligation=produced.obligation,
                                            reason=produced.reason))
                continue
            candidates.append(produced)

        ranked = rank_candidates(candidates, terms)
        for index, item in enumerate(ranked):
            result = port.admit(item, scope, adapter, run_id=run_id,
                                budget=budget)
            if result.admitted:
                readings.append(AdmittedReading(
                    item=item.target,
                    content=result.content or "",
                    marker=result.pin.marker() if result.pin else "",
                    evidence_path=result.evidence_path or "",
                ))
            else:
                d = result.degradation
                refusals.append(RefusedRead(
                    item=item.target,
                    obligation=d.obligation if d else "unknown",
                    reason=d.reason if d else "no reason recorded",
                ))
            # THE RUN STOPS AT ITS CEILING — it does not keep probing.
            #
            # `admit()` READS an item before it consults the aggregate, so
            # continuing past exhaustion re-reads every remaining candidate in
            # full (for `code`, four `git` subprocesses and a `read_bytes()`
            # each) and emits one refusal line per unread file into the report
            # body. On a repository of any size that is thousands of subprocesses
            # and a report made mostly of refusals — not the "reached its limit
            # and stopped" the outcome promises.
            #
            # The signal is the PORT's (`_RunBudget.exhausted`), never a bound
            # re-derived here, and it is set by the aggregate alone: a single
            # oversized file degrades at the per-item ceiling and returns before
            # the aggregate is ever consulted, so one huge vendored file cannot
            # truncate a whole run.
            if budget.exhausted:
                unread = ranked[index + 1:]
                if unread:
                    refusals.append(RefusedRead(
                        item=f"{len(unread)} further declared item(s), "
                             f"starting at {unread[0].target}",
                        obligation=_port.OBLIGATION_READ_BUDGET,
                        reason=f"the run reached its aggregate budget and "
                               f"stopped; these were not read. Narrow what you "
                               f"pointed at to bring more of what you care about "
                               f"inside the limit"))
                break
        if budget.exhausted:
            break

    return DeclaredReadResult(readings=tuple(readings),
                              refusals=tuple(refusals))


def synthesize_from_admissions(read_result: DeclaredReadResult) -> str:
    """Compose the synthesis body from WHAT THE PORT RETURNED.

    Deliberately reads `reading.content` and never re-opens `reading.item`. That
    is what makes the divergence test meaningful: if this function re-read the
    file, a stubbed `admit()` returning altered content would produce identical
    output and "the read flows through the port" would be unprovable.

    Refusals are rendered INTO the body rather than dropped, so a person reading
    the result sees what was not read and why — including a run that stopped at
    its aggregate ceiling, which arrives here as a refusal naming the bound rather
    than as a shorter report nobody can distinguish from a smaller source.

    **The evidence disclosure sits NEXT TO the claim, not in an appendix** (A13,
    U8). A reader decides how much weight to give a claim at the moment they read
    it, and a note they would have to go and look up separately is a note they
    will not read. It is also two different sentences rather than one constant —
    see :meth:`AdmittedReading.evidence_sentence`.
    """
    lines: List[str] = []
    for reading in read_result.readings:
        claim = f"{reading.content.rstrip()} {reading.marker}".strip()
        note = reading.evidence_sentence()
        lines.append(f"{claim}\n{note}" if note else claim)
    for refusal in read_result.refusal_lines:
        lines.append(f"[unverified — {refusal}]")
    return "\n\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI.
#
# WHY THIS EXISTS, AND WHY ITS ABSENCE WAS A REAL DEFECT RATHER THAN A GAP IN
# CONVENIENCE. The thing that invokes this reader is skill prose, and the prose
# has to name a command a session can actually run. The first version of that
# prose shipped a `python3 - <<'PY'` heredoc — which `hooks/sanitize-bash.sh`
# REFUSES with exit 2 (pattern 6, "python heredoc"), under a dedicated test that
# pins that exact shape as blocked. So the slice's one load-bearing prose step was
# not merely unenforceable (which is design-A18 and known) but UNEXECUTABLE, and
# no test could catch it because no test runs the prose.
#
# The fix is the idiom the rest of this harness already uses and that the calling
# skill file already uses elsewhere: a module CLI invoked as
# `python3 <module>.py <verb>`. One command, no heredoc, nothing to quote.
#
# It reads the declaration OFF THE MANIFEST CYCLE rather than from a file path,
# because there is no file: `r0_intake` hoists the approved record onto the cycle
# (`research_pipeline.py`) and that is the only place it is persisted. The earlier
# prose told the reader to open "the approved scope record" as a file, which does
# not exist anywhere.
# --------------------------------------------------------------------------- #

def _resolve_scope_from_cycle(session_id: str, research_file_path: str):
    """The approved declaration, read back off the manifest cycle.

    Uses the fact-check engine's own resolver so this and the verification-time
    web read agree about which cycle a draft belongs to — a second cycle-lookup
    here would be a second answer to a question that already has one.

    research-entry-point-enforcement S4 round-2 (ITEM 2, operator decision):
    presence of a `scope_record` is NOT itself approval. `cmd_advance` hoists
    `scope_record` onto the cycle unconditionally, BEFORE the approval branch
    runs, and `_resolve_research_cycle_id` above returns only
    `(cycle_id, cycle_state)` — it does not return the manifest `state`, so
    this function used to stop at `raw`, consulting the record with no
    approval check anywhere on this path. Net effect of the hole: an
    `r0_intake` that carried a `scope_record` but no valid approval artifact
    left `r1_scope_approved: False` on the cycle, yet this function still
    resolved and returned the person's declared sources for reading.
    `resolve_cycle_scope_status` is the one per-cycle predicate the
    dispatch/read site is supposed to consult before reading (see
    `~/.claude/skills/research/SKILL.md`'s "Check THIS cycle's own approval
    before dispatching it" instruction) — this is that check, now wired in
    code on the one read path that was actually missing it. The `research`
    agent dispatch is a separate surface and remains merely FOLLOWED, not
    enforced — see `research-scope-framing.md` for that split.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
    import _factcheck_engine as _fce
    import research_pipeline as _rp

    _cycle_id, cycle = _fce._resolve_research_cycle_id(session_id,
                                                       research_file_path)
    raw = (cycle or {}).get("scope_record")
    if not raw:
        raise SystemExit(
            "this research cycle carries no approved declaration, so there is "
            "nothing to read. A run reads a person's own sources only when they "
            "selected them at Step 2.6 and approved them at Step 3.")

    # `_resolve_research_cycle_id` does not hand back the manifest state, so a
    # separate read is required to answer the approval question — see the
    # docstring above for why this cannot be skipped.
    state = _rp._read_state(session_id)

    # MINOR 3 (round-3 fix): honour the session-level bypass
    # (`research_pipeline.py bypass SID "reason"`) at this seam too, matching
    # its two siblings — `research-scope-gate.sh` and
    # `research-chromium-fetch/entry-guard.sh` both `exit 0` on
    # `state["bypass"] is True` before consulting per-cycle approval at all.
    # This read seam is the one place that used NOT to honour it: fail-closed
    # is the safer direction, so the gap was never a correctness bug, only an
    # inconsistency an operator using the documented escape hatch would hit as
    # an unactionable refusal. Checked BEFORE the per-cycle predicate, exactly
    # as the two siblings check it before their own gates.
    if isinstance(state, dict) and state.get("bypass") is True:
        return _scope.ScopeRecord.from_dict(raw)

    status = _rp.resolve_cycle_scope_status(state, _cycle_id)
    if status["decision"] != "approved":
        raise SystemExit(
            "cycle '{cid}' is not approved to read declared sources: "
            "{reason}".format(cid=_cycle_id, reason=status["reason"]))

    return _scope.ScopeRecord.from_dict(raw)


def _registered_type_tokens() -> frozenset:
    """The TYPE vocabulary, DERIVED from the invariant rather than retyped here.

    Retyping the vocabulary is how a mirror drifts, so this reads the four
    tuples `bookkeeping_invariant` already defines plus the two TYPEs that live
    outside them — `AUDIT`, recognized inline by `classify`, and the `R\\d+`
    fact-check rounds, which are an alternation *inside* `_ADV_EPH_RE` rather
    than a tuple member. A set built by listing tuple entries alone silently
    omits that last one.

    Importing these tuples is deliberately NOT delegation to `classify()`.
    Delegating the whole parse was measured and rejected: the invariant's
    stricter slug class forbids consecutive hyphens, which regresses seven
    `_CLAIMS.md` files whose slugs carry them. The ban is on the parse; the
    vocabulary is the part worth sharing.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hooks"))
    import bookkeeping_invariant as _bk

    return frozenset(_bk.STANDARD_TYPES + _bk.MULTI_TYPES
                     + _bk.ADVISORY_USER_TYPES + _bk.ADVISORY_EPHEMERA_TYPES
                     + ("AUDIT",))


def _scope_starts_with_type(scope: str) -> bool:
    """Does this scope's FIRST `_`-component name a registered TYPE?

    Such a name is not Bucket 2 at all — Bucket 2 places `<SCOPE>` before
    `<TYPE>`, and here the TYPE sits in the middle (`economic-model-v2` +
    `RESEARCH_C1_DE`, left by the suffix loop taking its `.md` arm). Declining
    the match leaves the file on the shared fallback, which is the correct
    outcome for a name the grammar does not accept.

    **First component only, deliberately.** Widening this to *any* component
    was proposed, measured as costless on the real corpus, and then refuted:
    `foo-<ts>_S1_DESIGN_NOTES_RESEARCH.md` is a fully grammar-legal Bucket-2
    file (the invariant anchors TYPE at the END of the remainder), and an
    any-component test rejects it for the `DESIGN_NOTES` suffix. `META` and
    `CASES` are ordinary English words that happen to be registered TYPEs, so
    that class is not exotic. The widening buys nothing measurable and costs a
    legal shape.
    """
    first = scope.split("_", 1)[0]
    return first in _registered_type_tokens() or bool(re.fullmatch(r"R\d+", first))


def durable_store_for(research_file: str):
    """The DURABLE admission store this run's records land in (A12b, design-A16).

    **Why the production path needs this at all.** `read_declared_sources` falls
    back to an in-memory store when none is supplied, and this CLI supplied none —
    so a `captured` excerpt existed only for the life of the process that wrote it.
    That is invisible in the unit suites (which hold the store and read it back in
    the same process) and fatal in production, where the whole point of the
    captured branch is that a LATER, credential-less checker can read the excerpt.
    A capability declared `False` by the adapter means nothing after the process
    exits unless the record outlives it.

    **Which names are placed under the topic slug, and which are not.** Where
    this function can read a slug out of the research file's own name, the
    record is an `ADMISSION` advisory artifact of that same slug family
    (`bookkeeping-model.md` §4 Bucket 3), so it is returned by a slug-grep and
    co-retires with the topic rather than accumulating in a state directory
    nobody sweeps. Two name shapes are read: the Bucket-1 `<slug>[-<ts>]`, and
    the Bucket-2 `<slug>-<ts>_<SCOPE>` — the latter **only when the timestamp
    is present**, which is what makes the slug unambiguous (see below).

    **Everything else keeps the shared fallback, and that is a real class, not
    an edge case.** A bare path or a test fixture falls back, as before. So do
    two shapes that ARE sanctioned by the grammar: a Bucket-2 name carrying no
    timestamp (`<slug>_<SCOPE>_RESEARCH.md` — legal under §4, grandfathered by
    §5), and a name whose scope begins with a registered TYPE token, which is
    not Bucket 2 at all because Bucket 2 puts `<SCOPE>` before `<TYPE>`.
    Measured over the reachable corpus, the fallback still holds a majority of
    files. This paragraph says so because the previous version named the
    falling-back class as "a bare path, a test fixture" — which affirmatively
    mis-described it: a sanctioned Bucket-2 name carries the slug grammar and
    fell back anyway, so the stated exception denied the very class that was
    taking it.

    **Why a scope needs a timestamp.** Without that requirement the non-greedy
    slug group captures every `<lowercase-kebab>_<anything>` stem, manufacturing
    a topic slug from a name that carries none — and a confidently wrong slug is
    worse than an obviously generic shared one. `bookkeeping-model.md` §5 makes
    the timestamp mandatory for new files and optional only to grandfather
    existing ones, so "has a timestamp" is exactly "was minted under the current
    grammar".

    The slug, timestamp and scope are taken from the research file's own name,
    which is the only place they are already agreed.
    """
    from . import admission_record as _rec

    path = Path(research_file).expanduser()
    stem = path.name
    for suffix in ("_RESEARCH.md", "_CLAIMS.md", ".md"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    # The scope group sits INSIDE the timestamp group, so a scope is
    # structurally unreachable without a timestamp — the discriminator is in
    # the grammar rather than in a follow-up condition that could be edited
    # away separately. The scope class is `(?P<scope>.+)`, the one
    # `bookkeeping_invariant._MULTI_RE` uses; a narrower uppercase-only guess
    # would reject the `_H_v2_B1_PLAN` form `bookkeeping-model.md` gives as
    # legal.
    m = re.match(r"\A(?P<slug>[a-z0-9-]+?)"
                 r"(?:-(?P<ts>\d{14})(?:_(?P<scope>.+))?)?\Z", stem)
    if not m or not path.parent.is_dir():
        return _rec.AdmissionRecordStore()
    scope = m.group("scope") or ""
    if scope and _scope_starts_with_type(scope):
        return _rec.AdmissionRecordStore()
    return _rec.AdmissionRecordStore(path.parent, slug=m.group("slug"),
                                     ts=m.group("ts") or "",
                                     scope_segment=scope)


def _cmd_read(args) -> int:
    # MAJOR 2 (research-entry-point-enforcement S4 round-4 fix): `--query`
    # puts the framed question directly on the command line, and a question
    # is the operator's own English — the same hazard `--payload-file`
    # exists to remove from `research_pipeline.py advance`. A question
    # containing "for" ... "do" ("What should I look for and how do I
    # compare vendors?") trips `sanitize-bash.sh` pattern 9's single-line
    # form with NO newline required, so collapsing the command to one line
    # does not make it safe. `--query-file` reads the question from a file
    # the caller writes with the Write tool instead, so no shell quoting
    # ever touches it — the same fix shape as `--payload-file`, applied to
    # this CLI's own free-text argument. `--query-file` wins when both are
    # given, since a caller only supplies both by mistake and the file form
    # is the one that cannot break.
    query = args.query
    if getattr(args, "query_file", None):
        # MINOR 4 (round-5 fix): this used to be unguarded, so a missing path
        # raised FileNotFoundError, a directory raised IsADirectoryError, and
        # non-UTF8 content raised UnicodeDecodeError — all as unhandled
        # tracebacks, matching `--payload-file`'s shape (`research_pipeline.py`
        # `advance --payload-file`, which catches OSError around its own file
        # read) rather than diverging from its sibling. This matters beyond
        # tidiness: SKILL.md's "Reading the person's own declared sources"
        # section tells the model that a source which could not be read
        # "comes back refused BY NAME with its reason ... Keep it there" — a
        # raw traceback from a typo'd scratch path is confusable with that
        # refusal channel rather than being the clean CLI error it should be.
        try:
            query = Path(args.query_file).expanduser().read_text(
                encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as exc:
            raise SystemExit(
                "--query-file {p!r}: {e}".format(p=args.query_file, e=exc))
    scope = _resolve_scope_from_cycle(args.session_id, args.research_file)
    result = read_declared_sources(
        scope,
        Path(args.projects_root).expanduser().resolve(),
        store=durable_store_for(args.research_file),
        query=query)
    if args.json:
        print(json.dumps({
            "readings": [{"item": r.item, "marker": r.marker,
                          "content": r.content} for r in result.readings],
            "refusals": [{"item": r.item, "obligation": r.obligation,
                          "reason": r.reason} for r in result.refusals],
        }, indent=2))
    else:
        print(synthesize_from_admissions(result))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="declared_read",
        description="Read the sources a person declared and approved, through "
                    "the admission port, under the run's bounds.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_read = sub.add_parser(
        "read", help="Read this cycle's approved declaration and print the body.")
    p_read.add_argument("session_id")
    p_read.add_argument("research_file",
                        help="The _RESEARCH.md this run is writing — used to "
                             "resolve which manifest cycle to read.")
    p_read.add_argument("--projects-root", required=True,
                        help="Workspace root, for workspace-relative citations.")
    p_read.add_argument("--query", default=None,
                        help="The framed question. Supplied, it orders how the "
                             "run spends its budget; omitted, the order is the "
                             "adapter's own. Prefer --query-file: this puts the "
                             "operator's own words on the command line, which "
                             "sanitize-bash.sh's control-flow-keyword pattern "
                             "can refuse (a question containing \"for\" ... "
                             "\"do\" is refused even on one line).")
    p_read.add_argument("--query-file", default=None,
                        help="Path to a file (written with the Write tool) "
                             "holding the framed question. Takes precedence "
                             "over --query when both are given — this is the "
                             "form that never puts the operator's words on the "
                             "command line.")
    p_read.add_argument("--json", action="store_true",
                        help="Emit structured readings/refusals instead of a body.")
    p_read.set_defaults(func=_cmd_read)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())

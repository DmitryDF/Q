"""The source picker — where a person says which of their own sources to read, and how far.

research-source-adapters S4 / plan actions A1–A4 (design-U1, U2, U3, U5, U9, U11,
design-A12's unimplemented-slot display, design-A32's research-side half).

S3 shipped a complete admission contract whose declaration record had **no author**:
``scope_record.ScopeRecord.to_json`` described a cross-surface form that nothing
anywhere wrote. This module is that author. (That docstring then anticipated a
``jq`` bash hook as the reader; it no longer says so, because none materialised —
the readers are all Python. This sentence used to quote the old wording in the
present tense and so asserted a fact about a sibling file that had stopped being
true.) It turns a framed question into either an approved declaration or one
of two terminal outcomes, and it does so without amending a single S3 module.

Four responsibilities, one altitude
-----------------------------------
* **The catalogue** — what a person can be *shown*, including entries that are not
  available yet. It does NOT state what may be *declared*; that is
  :data:`scope_record.REGISTERED_KINDS`, read at call time.
* **Bound prompts + assembly** — one bound per selected class, assembled into a
  single :class:`~research.scope_record.ScopeRecord`.
* **Reachability** — a selection-time probe, kind-dispatched here rather than on the
  adapter.
* **The terminal branches** — a spike stop and an empty selection, neither of which
  produces a record.

Three properties are load-bearing and each is tested
----------------------------------------------------
* **Selectability is DERIVED, never declared twice** (plan Guiding Policy 2). A
  catalogue entry is selectable **iff** three things hold *at the moment the list is
  rendered*: its ``registered_kind`` is in ``scope_record.REGISTERED_KINDS``, a
  production driver for that kind exists at all
  (``kind_reachability.KNOWN_UNREACHABLE``), and the calling route can read it
  (the entry's own ``routes``). There is no ``available`` field on an entry. A
  hand-maintained flag would be a second, silent authority on admissibility that
  drifts the moment a later slice registers a kind — and the failure mode is a
  person ticking a source that refuses at construction, which is the worst-timed
  way to learn a class is not ready. Deriving means registering ``web`` flipped
  that entry with no edit here, and wiring the code driver flipped nothing here
  either. **The driver input is the half that generalises — but only as half of a
  pair**: it refuses a class RECORDED as driverless, and what compels the record
  is ``kind_reachability.check()`` at promotion time. Read :func:`is_selectable`
  before relying on it; this input alone does not detect a driverless class, and
  the gate alone was already in place while ``code`` stayed offered on three
  routes for four slices with nothing opening a repository.
* **``unscoped`` is a declared mode, never an empty selector list.** S3 ships a test
  asserting an *enumerated* record with no selectors admits nothing; the unscoped mode
  exists precisely so that rule does not swallow the Linear case the design requires
  (``…_DESIGN.md:294-297``). :func:`build_record` refuses to express one as the other.
* **A terminal outcome carries no record.** :class:`SpikeStop` and
  :class:`EmptySelection` have no ``record`` attribute at all, so "nothing is
  registered" is structural rather than a caller's discipline.

What this module deliberately does NOT do
-----------------------------------------
* **It amends none of S3's six modules** (plan Guiding Policy 3). It imports
  ``scope_record`` and constructs its types. In particular it does **not** widen
  ``source_port.SourceAdapter``'s deliberately-three-thing surface to carry a probe —
  reachability is a *selection-time* question, before any adapter is involved.
* **It never reads a source.** :func:`probe` answers existence and type only; the
  port owns every read, every bound and every pin.
* **It does not know the research manifest.** It returns a ``ScopeRecord`` or a
  terminal outcome; the flow assembles the ``r0_intake`` payload. A picker that also
  knew the manifest's shape would have drifted into being the pipeline's client.
* **It renders no user-facing text.** The wording lives in
  ``~/.claude/rules/research-scope-framing.md`` with its EN/DE/RU Localization Table;
  this module carries the *slots* those strings fill, never the strings.

Dependency direction: imports ``scope_record`` and — since offerability gained its
third input — the plain data constant ``kind_reachability.KNOWN_UNREACHABLE``. It
imports nothing else from the package, and nothing in the package imports this
module.

**That second import is stated rather than glossed, because it replaced a stricter
sentence that said this module imports ``scope_record`` ONLY.** What is imported is
a ``Mapping[str, Tuple[str, str]]`` — data, read at call time — and NOT the AST
derivation that populates the equivalent question in that module, which is never
invoked on a render path. ``kind_reachability`` imports only the standard library
at module level, so no cycle is created. See :func:`is_selectable` for why the
borrowing is sound in one direction and not the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from . import kind_reachability as _reach
from . import scope_record as _scope
from .scope_record import (
    KIND_CODE,
    REGISTERED_KINDS,
    SCOPE_MODE_ENUMERATED,
    SCOPE_MODE_UNSCOPED,
    DeclaredSource,
    ScopeRecord,
    ScopeRecordError,
)

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# Why an entry is not available. Two flavours, distinguished only by the reason
# a person reads — they need to know which is coming, not which register it sits
# in (plan A1).
# --------------------------------------------------------------------------- #

UNAVAILABLE_LATER_SLICE = "later-slice"        # this topic will ship it
UNAVAILABLE_SLOT = "unimplemented-slot"        # design-A12: registered, unbuilt


class SourcePickerError(ValueError):
    """Raised when the picker is asked for something it must refuse.

    A *selection* problem (an unreachable path, a bound the record refuses) is NOT
    an exception — it is reported so the person can retry, drop or re-bound. This
    is for programming errors: an unknown catalogue key, a probe on an unregistered
    kind, a bound shape the class does not accept.
    """


# --------------------------------------------------------------------------- #
# Routing paths (S7, plan G11/C12).
#
# WHY AVAILABILITY GAINED A ROUTE INPUT.
# Selectability was derived from ONE thing — is the kind registered — which was
# right while every route that opened this list could read every registered
# class.
#
# **THE ORIGINAL ILLUSTRATION BELOW IS SUPERSEDED (Q26, 2026-09-11), AND ITS
# PREMISE WAS NEVER TRUE OF THIS CODEBASE.** It read: S7 "registers two kinds
# whose production reader is wired on ONE route. Left one-input, registering them
# would have made their rows tickable on every routing path, so a person on an
# open-web route could have ticked 'your knowledge library' and got a run where it
# was never opened — silence rather than refusal." The reader was never
# route-bound: `declared_read._adapter_for` dispatches by KIND with no route
# conditioning, so an open-web route always could read a declared library. Both
# S7 classes now carry `routes=ROUTES` accordingly.
#
# The illustration was a symptom; the RULE behind it was the defect, and it is
# stated correctly in exactly one place — `is_selectable`. Go there rather than
# reasoning from this paragraph.
#
# (Earlier versions of this note tried to say how many copies of the bad rule
# existed and where. Three such counts were written and no two agreed, because a
# claim about other files cannot be checked from this one. Replaced with the
# pointer above, which can.)
#
# **THE ROUTE INPUT ITSELF STANDS AND IS STILL LOAD-BEARING** — it is what keeps
# `code`/`web`/`linear` off the Internal-KB route, where a declared `web` source
# would be read unbounded (no manifest cycle is opened there). Only the example
# was wrong, never the mechanism.
#
# THE ROUTE IS ONE OF THREE INPUTS, NOT THE SECOND OF TWO. The route answers "is
# this class read HERE"; registration answers "may it be declared at all"; and
# driver presence answers "is it read ANYWHERE". That third question went unasked
# for four slices, which is how `code` stayed offered on three routes with no
# production reader behind it. All three are DERIVED at call time and none is a
# stored `available` flag: the route set is a declarative entry field beside
# `allows_unscoped`, driver presence is read from
# `kind_reachability.KNOWN_UNREACHABLE`, and no availability field is hand-set
# anywhere. See `is_selectable` for the whole rule and for what the driver input
# does and does not promise.
# --------------------------------------------------------------------------- #

ROUTE_NINJA = "ninja"
ROUTE_DEEP = "deep"
ROUTE_ULTRA_DEEP = "ultra_deep"
ROUTE_INTERNAL_KB = "internal_kb"
ROUTE_AUTONOMOUS = "autonomous"

ROUTES = (ROUTE_NINJA, ROUTE_DEEP, ROUTE_ULTRA_DEEP, ROUTE_INTERNAL_KB,
          ROUTE_AUTONOMOUS)

# The routes whose source-tier is external (`research-scope-framing.md:43-47`).
_EXTERNAL_ROUTES = (ROUTE_NINJA, ROUTE_DEEP, ROUTE_ULTRA_DEEP, ROUTE_AUTONOMOUS)

# `_INTERNAL_ROUTES` was here, and is REMOVED rather than left unused (Q26,
# 2026-09-11). It bound `knowledge_library` and `document_folder` to the
# Internal-KB route alone, which is what made the locked promise of "any
# combination of sources in one run" unachievable: no route-scoped declaration
# could span those two and `code`/`web`/`linear`. Both classes now carry
# `routes=ROUTES`.
#
# Its stated justification had ALREADY EXPIRED when it was removed. The refusal
# it powered was explained as "this route has no reader for it" — but
# `declared_read._adapter_for` supplies both adapters unconditionally, with no
# route conditioning, and the external routes already invoke that module. The
# reason outlived the fact, which is the failure mode this topic keeps recording
# against itself.
#
# **This does NOT widen the Internal-KB route**, and the `internal-only` tier at
# `research-scope-framing.md:46` stays TRUE: `ROUTE_CLASS_RESTRICTION` below is a
# separate authority and is deliberately UNCHANGED, so that route still offers
# only the two internal classes. What moved is what the EXTERNAL routes may read.
# Admitting `code`/`web`/`linear` onto Internal-KB is the other direction and is
# NOT done here: that route opens no manifest cycle, so a declared `web` source
# would be read unbounded via `web_scope()`'s unscoped fallback — worse than the
# silence failure this flow exists to remove.

# A10's PER-PATH RESTRICTION: which classes a route's list holds at all.
# A route absent from this map shows the whole catalogue.
#
# **This is a different question from `SourceClassEntry.routes`, and the two are
# not redundant.** `routes` answers "can this route READ this class", which drives
# whether a shown row is tickable and whether `build_record` will assemble it.
# This map answers "is this class ON this route's list", which is membership.
#
# The distinction is forced by an asymmetry that is real rather than incidental —
# and since Q26 (2026-09-11) only ONE of its two halves is still live. The first
# bullet is kept as the worked example of the RULE, which is unchanged and still
# binds any future class; it no longer describes any currently-registered one.
#
# * **(No longer live.)** On an open-web route, `knowledge_library` WAS SHOWN as
#   not-yet-available WITH ITS REASON. A person might reasonably expect to pick
#   their own library there, so telling them why they cannot is information —
#   that is C12. Q26 widened both S7 classes to every route, so this case no
#   longer arises and their `unavailable_*` slots are inert (kept, not retired,
#   on web's precedent). This bullet survived the Q26 sweep and was caught by an
#   independent check afterwards — the third consecutive round on this topic in
#   which prose asserting behaviour the code does not have outlived a sweep
#   declared complete. Recorded rather than quietly deleted, because that pattern
#   is the thing worth seeing.
# * **(Live.)** On the internal-only route, `code` and `web` are NOT SHOWN AT ALL.
#   This half is untouched by Q26 and is what keeps the `internal-only` tier true.
#   They were
#   never on offer: the route's declared source-tier excludes them. Rendering them
#   greyed out would be worse than useless — every non-selectable row must carry a
#   reason (a refusal without one is the silent failure this flow exists to
#   remove), and the honest reason here is not "not yet" but "not on this route",
#   which is a statement about the route rather than about the class.
#
# Expressing that second case as a reason string would need a NEW localization
# slot in all three languages for a row nobody should be looking at. Omitting the
# row says the same thing and adds no vocabulary — and it is exactly what the
# rules file already promises: "the list it is offered holds only internal
# classes".
ROUTE_CLASS_RESTRICTION: Dict[str, Tuple[str, ...]] = {
    ROUTE_INTERNAL_KB: ("knowledge_library", "document_folder"),
}


def classes_on_route(route: Optional[str] = None) -> Tuple[str, ...]:
    """The catalogue keys a route's list holds at all (A10's per-path restriction).

    Membership, not selectability — a key here may still render not-yet-available
    with its reason. Passing no route returns the whole catalogue.
    """
    if route is None:
        return tuple(e.key for e in CATALOGUE)
    if route not in ROUTES:
        raise SourcePickerError(
            f"unknown routing path {route!r}; known routes: {list(ROUTES)}")
    allowed = ROUTE_CLASS_RESTRICTION.get(route)
    if allowed is None:
        return tuple(e.key for e in CATALOGUE)
    return tuple(e.key for e in CATALOGUE if e.key in allowed)


# --------------------------------------------------------------------------- #
# The catalogue.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SourceClassEntry:
    """One source class a person can be shown.

    Deliberately carries **no** ``available`` field. Selectability is computed by
    :func:`is_selectable` — from the shipped kind registry, from whether a
    production driver for the class exists at all, and from this entry's own route
    set — every time the catalogue is rendered. See this module's docstring for why
    a stored flag is the defect.

    `bound_slot` names the Localization slot whose EN/DE/RU strings ask this class
    for its bound; the wording itself lives in the rules file, never here.
    """

    key: str
    label_slot: str
    registered_kind: str
    bound_slot: Optional[str] = None
    # Present only while the class is unavailable; a person reads this, so it names
    # what is coming rather than which register the class sits in.
    unavailable_kind: Optional[str] = None
    unavailable_slot: Optional[str] = None
    landing_slice: Optional[str] = None
    # Whether this class may be declared "bounded by whatever it exposes"
    # (design's unscoped Linear case). Enumerated-only classes refuse it.
    allows_unscoped: bool = False
    # The routing paths WILLING to read this class (S7, G11) — a source-tier
    # policy, not a statement about reader existence. A declarative field, exactly
    # like `allows_unscoped`; availability stays derived, and this is one of the
    # three inputs it derives from. See `is_selectable` for the canonical
    # statement — it is the one place this is spelled out, deliberately.
    #
    # This comment read "a class is offered on a route only when a reader for it
    # exists there" until 2026-09-11. That was false — readers dispatch on kind,
    # never on route — and it sat on the field this whole question turns on.
    routes: Tuple[str, ...] = ROUTES


# The catalogue. Order is display order.
#
# Link-provided Google Drive is **absent**, not unavailable: S9 is struck
# (`…_DESIGN.md:371`), so offering it would advertise a class no slice will ship.
# Mounted Drive is not a class of its own — design-A31 admits it unconditionally
# through the document-folder adapter, which lands in S7.
CATALOGUE: Tuple[SourceClassEntry, ...] = (
    SourceClassEntry(
        key="code",
        label_slot="source_label_code",
        registered_kind=KIND_CODE,
        bound_slot="bound_prompt_code",
        landing_slice="S3",
        # External routes only. The `internal-only` tier at
        # `research-scope-framing.md:46` is what excludes the Internal-KB route,
        # and A10's flip must not widen it: the premise that expired there is
        # "its own classes have no reader", NOT "this route should read the open
        # web". Adding this field does not change `code`'s answer on any route it
        # was ever offered on.
        routes=_EXTERNAL_ROUTES,
    ),
    SourceClassEntry(
        key="web",
        label_slot="source_label_web",
        registered_kind="web",
        bound_slot="bound_prompt_web",
        # The `unavailable_*` pair below is INERT now that S6 has registered the
        # `web` kind: `render_catalogue` emits them only for a row that is not
        # selectable, and selectability is derived from the kind registry at call
        # time. They are left in place rather than deleted so the diff S6 makes to
        # this catalogue is exactly ONE field — the availability fields are not
        # what flipped this row, and editing them by hand would reintroduce the
        # second authority on admissibility this module exists without.
        unavailable_kind=UNAVAILABLE_LATER_SLICE,
        unavailable_slot="unavailable_web",
        landing_slice="S6",
        # S6 (design-A29). The open web is what research reads TODAY, so a web
        # declaration that could only ever be a narrow list would change what a
        # person experiences — which design-A29 forbids. This is a bound-POLICY
        # field, not an availability field.
        allows_unscoped=True,
        # External routes only — same reasoning as `code` above.
        routes=_EXTERNAL_ROUTES,
    ),
    # The two S7 classes. Their `unavailable_*` pairs BECAME INERT on 2026-09-11
    # (Q26), by web's reason rather than by their own: these classes are now
    # readable on every route, so no route both offers them and cannot read them
    # — which is exactly the condition that emptied web's row. The paragraph that
    # stood here said the opposite, and was true when written: S7 wired their
    # reader on the Internal-KB route only, so the external routes genuinely did
    # have to carry a reason. That premise expired when the reader turned out to
    # be route-neutral (`declared_read._adapter_for`), and the row outlived it.
    #
    # The slots are KEPT, not deleted, on web's precedent: a three-language row
    # costs nothing to retain and a future route change could make it reachable
    # again. Inert is not wrong — it is unreachable.
    #
    # As with web, no availability field on these rows is hand-edited: they flip
    # by themselves once the kinds are registered and the route set is read.
    SourceClassEntry(
        key="knowledge_library",
        label_slot="source_label_knowledge_library",
        registered_kind="knowledge_library",
        bound_slot="bound_prompt_knowledge_library",
        unavailable_kind=UNAVAILABLE_LATER_SLICE,
        unavailable_slot="unavailable_knowledge_library",
        landing_slice="S7",
        routes=ROUTES,
    ),
    SourceClassEntry(
        key="document_folder",
        label_slot="source_label_document_folder",
        registered_kind="document_folder",
        bound_slot="bound_prompt_document_folder",
        unavailable_kind=UNAVAILABLE_LATER_SLICE,
        unavailable_slot="unavailable_document_folder",
        landing_slice="S7",
        routes=ROUTES,
    ),
    SourceClassEntry(
        key="linear",
        label_slot="source_label_linear",
        registered_kind="linear",
        bound_slot="bound_prompt_linear",
        unavailable_kind=UNAVAILABLE_LATER_SLICE,
        unavailable_slot="unavailable_linear",
        landing_slice="S8",
        allows_unscoped=True,          # "unscoped means whatever the MCP exposes"
        # S8 (design-A12). EXPLICIT, because the field's default is every route
        # INCLUDING the internal-only one — and a third-party remote offered on a
        # route whose declared source-tier is `internal-only` would contradict
        # that tier. Left defaulted, registering the kind would have silently
        # widened the Internal-KB route to reach Linear; the row must therefore
        # name its routes rather than inherit them.
        routes=_EXTERNAL_ROUTES,
    ),
    # design-A12: registered as unimplemented adapter slots against the same port
    # surface. Shown so a person can see the shape of what is coming.
    SourceClassEntry(
        key="confluence",
        label_slot="source_label_confluence",
        registered_kind="confluence",
        unavailable_kind=UNAVAILABLE_SLOT,
        unavailable_slot="unavailable_slot_confluence",
    ),
    SourceClassEntry(
        key="jira",
        label_slot="source_label_jira",
        registered_kind="jira",
        unavailable_kind=UNAVAILABLE_SLOT,
        unavailable_slot="unavailable_slot_jira",
    ),
)

_BY_KEY: Dict[str, SourceClassEntry] = {e.key: e for e in CATALOGUE}


def entry(key: str) -> SourceClassEntry:
    """Look up a catalogue entry. Fails closed on an unknown key."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise SourcePickerError(
            f"unknown source class {key!r}; catalogue holds "
            f"{sorted(_BY_KEY)}") from None


def is_selectable(e: SourceClassEntry, route: Optional[str] = None) -> bool:
    """**Derived**, at call time, from THREE inputs: the shipped kind registry,
    whether a production driver exists for the class at all, and whether the
    calling route can read it.

    Read ``_scope.REGISTERED_KINDS`` through the module rather than through the
    name imported at module load, so that registering a kind — in a later slice or
    in a test — is observable here without a catalogue edit. That indirection is
    the whole mechanism; binding the tuple at import would freeze the answer and
    reintroduce the second authority this module exists to avoid.

    **Driver presence is the third input.** Until it existed, availability was a
    function of registration and route only, so a registered class with no reader
    behind it stayed tickable — which is how "your code" stayed on offer through
    four slices while nothing opened a repository.

    **What this input reads, precisely, and why that is not the whole mechanism.**
    It tests membership in ``kind_reachability.KNOWN_UNREACHABLE`` — a mapping of
    kinds RECORDED as having no driver. It does not derive driver presence, and
    the distinction matters: a kind registered with no driver **and no recorded
    exemption** passes this check and is offered. What stops that combination
    existing is the other half — ``kind_reachability.check()``, which runs on the
    deploy path and fails a registered kind that has neither a driver nor a
    recorded exemption, so the exemption cannot be quietly omitted.

    **So the generalisation is a PAIR, and saying otherwise overstates this
    function.** The gate compels the record; this input consumes it. Neither half
    does the job alone — this input alone would let an unrecorded driverless kind
    through, and the gate alone is exactly what was already in place when ``code``
    stayed tickable for four slices with its exemption recorded and read by
    nobody.

    **The coupling this creates, stated where it is created.**
    ``kind_reachability`` says in terms that it measures reachability of the
    **PORT**, not of the picker, and that its kind attribution is per-FILE, so a
    kind merely named in a file that drives some other kind reads as driven.
    Borrowing it for person-facing offerability is deliberate and is conservative
    in ONE direction only: a kind with no port driver anywhere cannot be read by
    any route, so refusing to offer it is always right. **The converse does not
    hold** — a kind whose driver file also names other kinds can read as driven
    when nothing actually reads it, and this function inherits that false
    positive. It is therefore a floor on offerability, not a guarantee of a read;
    the route set below is what answers "is it read HERE", and neither this input
    nor that one is containment.

    **The route is the third question, and this is the CANONICAL statement of what
    it means. Every other site describing the route input points here rather than
    restating it.**

    `e.routes` expresses each class's **source-tier policy** — which routes are
    *willing* to read it — and NOT whether a reader for it exists. Readers are
    route-neutral: `declared_read._adapter_for` dispatches on KIND alone and takes
    no route argument, so for every kind either a reader exists everywhere or it
    exists nowhere. What the field holds back today is `code`/`web`/`linear` on the
    Internal-KB route, where a reader does exist and is deliberately not used: that
    route opens no manifest cycle, so a declared `web` source would be read
    unbounded.

    *(**This replaces a rule that was wrong for four review rounds**, in five
    copies across four files: "a class is offered on a route only when a reader for
    it exists there". Each round corrected an ILLUSTRATION of that rule while
    leaving the rule itself, so the next round found the next copy. It was never
    true of this codebase — it conflated "is this class read HERE" with "is this
    class read ANYWHERE", which are different questions with different answers.
    Stated once, here, so there is one thing to keep true.)*

    **`route=None` means "no route scoping" and answers exactly as this function
    did before the route input existed.** That is deliberate rather than a
    fail-open convenience: it is what keeps every earlier caller — including
    `test_web_admission.py`, which passes UNEDITED — byte-identical. The route
    rule is enforced where routes are actually known: by `build_record` when a
    caller passes one, by the derived-availability rule in
    `research-scope-framing.md` that a person follows when rendering the list, and
    by each of the three production callers passing its own route.
    """
    if e.registered_kind not in _scope.REGISTERED_KINDS:
        return False
    if e.registered_kind in _reach.KNOWN_UNREACHABLE:
        # Route-independent, so it is asked before the route: a kind with no
        # production driver is unreadable everywhere, not just here.
        return False
    if route is None:
        return True
    if route not in ROUTES:
        raise SourcePickerError(
            f"unknown routing path {route!r}; known routes: {list(ROUTES)} "
            "(an unknown route fails closed rather than being treated as "
            "unscoped)")
    return route in e.routes


@dataclass(frozen=True)
class CatalogueRow:
    """One rendered row. `selected` is always False — design-U2 pre-ticks nothing."""

    key: str
    label_slot: str
    selectable: bool
    selected: bool = False
    bound_slot: Optional[str] = None
    unavailable_kind: Optional[str] = None
    unavailable_slot: Optional[str] = None
    landing_slice: Optional[str] = None
    allows_unscoped: bool = False


def render_catalogue(route: Optional[str] = None) -> Tuple[CatalogueRow, ...]:
    """The rows a person is shown. Nothing is pre-ticked (design-U2).

    An unavailable row always carries a reason slot; an available one never does.

    `route` (S7) does two things, and they are different questions:

    * **Membership** — the route's per-path restriction decides which classes are
      on its list at all (`classes_on_route`). The internal-only route's list holds
      internal classes only, so `code` and `web` are absent from it rather than
      shown greyed out.
    * **Availability** — among the rows that ARE on the list, a class with no
      reader on this route renders NOT-selectable **with its reason**, exactly as a
      class whose kind is unregistered does.

      The two S7 rows were the standing example of this and no longer are: since
      Q26 (2026-09-11) they are readable on every route, so their `unavailable_*`
      slots are INERT, as web's already were. The slots are kept rather than
      deleted, on web's precedent. The RULE above is unchanged and still binding
      on any future class — what changed is that no currently-registered class
      exercises it.

    Passing no route answers as this function did before S7.
    """
    on_route = set(classes_on_route(route))
    rows = []
    for e in CATALOGUE:
        if e.key not in on_route:
            continue
        selectable = is_selectable(e, route)
        rows.append(CatalogueRow(
            key=e.key,
            label_slot=e.label_slot,
            selectable=selectable,
            selected=False,
            bound_slot=e.bound_slot if selectable else None,
            unavailable_kind=None if selectable else e.unavailable_kind,
            unavailable_slot=None if selectable else e.unavailable_slot,
            landing_slice=e.landing_slice,
            allows_unscoped=e.allows_unscoped,
        ))
    return tuple(rows)


def selectable_keys(route: Optional[str] = None) -> Tuple[str, ...]:
    """Catalogue keys a person may tick right now, on `route` if one is given.

    Both conditions apply: the class is on the route's list, and it is selectable
    there.
    """
    on_route = set(classes_on_route(route))
    return tuple(e.key for e in CATALOGUE
                 if e.key in on_route and is_selectable(e, route))


# --------------------------------------------------------------------------- #
# What a person chose.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Bound:
    """One class's bound, in that class's own terms.

    ``unscoped=True`` means "the bound is this source's own exposure" — a declared
    mode, not an absent field. ``selectors`` carries the paths otherwise.
    """

    unscoped: bool = False
    selectors: Tuple[str, ...] = ()
    connection_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "selectors", tuple(self.selectors or ()))
        if self.unscoped and self.selectors:
            raise SourcePickerError(
                "a bound is either 'whatever this source exposes' or a set of "
                "selectors — never both")


@dataclass(frozen=True)
class Selection:
    """What was ticked, and the bound given for each. Ordered by catalogue order."""

    bounds: Tuple[Tuple[str, Bound], ...] = ()

    @property
    def keys(self) -> Tuple[str, ...]:
        return tuple(k for k, _ in self.bounds)

    def __len__(self) -> int:
        return len(self.bounds)


@dataclass(frozen=True)
class SpikeStop:
    """The framed question is an investigation, not research (design-U9).

    Carries no record and no selection **by construction** — the flow cannot
    register anything from this outcome because there is nothing here to register.
    """

    reason_slot: str = "spike_stop"


@dataclass(frozen=True)
class EmptySelection:
    """Nothing was ticked (design-U11). Nothing read, nothing claimed.

    Carries no record, for the same structural reason as :class:`SpikeStop`.
    """

    reason_slot: str = "empty_selection"


PickerOutcome = Union[SpikeStop, EmptySelection, Selection]


# --------------------------------------------------------------------------- #
# Reachability — selection time only, and never a read.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Reachable:
    kind: str
    target: str
    reachable: bool = True


@dataclass(frozen=True)
class Unreachable:
    """Named, so design-U5's retry / drop / change-bound choice has something concrete."""

    kind: str
    target: str
    reason_slot: str
    detail: str = ""
    reachable: bool = False


ProbeResult = Union[Reachable, Unreachable]


def probe(source: DeclaredSource) -> Tuple[ProbeResult, ...]:
    """Can each declared selector be reached, right now? Kind-dispatched, fail-closed.

    **Never opens a file.** This answers what selection time can answer — the
    declared path resolves and is a file or a directory — and nothing more. Reading,
    enumerating and pinning are the port's, behind its own bounds.

    An unscoped declaration has no selector to probe, so it returns a single
    :class:`Reachable` naming the source rather than an empty tuple: "nothing to
    check" is not the same answer as "no result", and a caller that filtered on
    truthiness would otherwise silently treat it as unreachable.
    """
    if source.kind not in _scope.REGISTERED_KINDS:
        # Unreachable in practice — an unregistered kind was never selectable and
        # `DeclaredSource` refuses it at construction. Kept so that registering a
        # kind without adding a probe arm fails loudly rather than defaulting to
        # reachable, which is the direction that would matter.
        raise SourcePickerError(
            f"no reachability probe for kind {source.kind!r}; registered kinds: "
            f"{list(_scope.REGISTERED_KINDS)}")
    if source.scope_mode == SCOPE_MODE_UNSCOPED:
        return (Reachable(kind=source.kind, target="<the source's own exposure>"),)
    # Every path-shaped kind SHARES one arm rather than getting a copy of it
    # (S7). `_probe_path` is `_probe_code` renamed: existence and type only, no
    # open(), no read, no walk — which is already exactly what the two new kinds
    # need, so copying it would have created two things to keep in step for no
    # difference in behaviour.
    if source.kind in _scope.PATH_SHAPED_KINDS:
        return tuple(_probe_path(source.kind, s) for s in source.selectors)
    if source.kind == _scope.KIND_WEB:
        return tuple(_probe_web(source.kind, s) for s in source.selectors)
    if source.kind == _scope.KIND_LINEAR:
        # Per-SELECTOR, so a declaration mixing projects and a link gets one
        # result per bound rather than one verdict for the source (S8).
        return tuple(_probe_linear(source.kind, s) for s in source.selectors)
    raise SourcePickerError(
        f"kind {source.kind!r} is registered but has no probe arm")


def _probe_web(kind: str, selector: str) -> ProbeResult:
    """Shape, then a name lookup. **Never opens a page** (S6, design-A29).

    The `code` arm asks whether a declared path exists; the honest web analogue is
    whether a declared host exists, which is a name lookup — not a fetch. Reading
    is the port's, behind its bounds, and a probe that fetched would move a read
    outside the port in the very slice that closes that gap.

    **A lookup failure is not a verdict.** Only a definitive "this name does not
    exist" reports unreachable. No resolver, no network, or a timeout reports
    REACHABLE, because those say something about the operator's connection rather
    than about their source — and telling someone their perfectly good source is
    unreachable because they are on a train is the false signal this branch exists
    to avoid. The failure direction is therefore: a dead host may be admitted and
    surface later as a recorded degradation; a live host is never blocked here.
    """
    import socket

    normalised = _scope._normalise_url(selector, assume_scheme=True)
    if normalised is None:
        return Unreachable(kind=kind, target=str(selector),
                           reason_slot="unreachable_web",
                           detail="not an http(s) location")
    host = normalised.host.split(":", 1)[0]
    try:
        socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        if getattr(e, "errno", None) in (socket.EAI_NONAME, socket.EAI_NODATA):
            return Unreachable(kind=kind, target=normalised.render(),
                               reason_slot="unreachable_web",
                               detail=f"no such host: {host}")
        return Reachable(kind=kind, target=normalised.render())
    except Exception:                       # noqa: BLE001 — never a false block
        return Reachable(kind=kind, target=normalised.render())
    return Reachable(kind=kind, target=normalised.render())


def _probe_linear(kind: str, selector: str) -> ProbeResult:
    """Reachability for one Linear selector. **Never opens Linear** (S8).

    Selection time answers *reachability*, never *content* — so this asks nothing
    that needs a credential, and reads no issue. It also deliberately does NOT
    route a link selector through :func:`_probe_web`: a filtered-issues link
    shares a URL *shape* with a web source, and sharing a shape is not being one.
    What a selector means is fixed by the kind it was declared under.

    Two shapes, two honest answers:

    * **A filtered-issues link** — shape, then the same name lookup `web` does,
      through the one shared statement of the "a lookup failure is not a verdict"
      rule. A malformed link is definitively not a location and is reported.
    * **A project identifier** — there is nothing to look up. Whether a project
      key exists is a question only the credentialed workspace can answer, and
      answering it here would either need a credential this step must not hold or
      guess. So a well-formed key reports REACHABLE and any real absence surfaces
      later as a recorded degradation, which is the same failure direction the web
      arm chose.

    Whether the WORKSPACE itself is reachable is a different question, answered by
    the connect gate (A7) rather than per-selector here.

    **On the duplicated name-lookup rule.** The link branch restates
    `_probe_web`'s "a lookup failure is not a verdict" discipline rather than
    sharing it: only a definitive "this name does not exist" reports unreachable;
    no resolver, no network, or a timeout reports REACHABLE, because those say
    something about the operator's connection rather than about their source. The
    restatement is deliberate. Extracting the rule into a shared helper was tried
    and reverted: it moved `getaddrinfo` out of `_probe_web`'s body and thereby
    broke the non-vacuity anchor of `test_a5_the_probe_never_opens_a_page`, whose
    own docstring records a PRIOR refactor of neighbouring code silently defeating
    that same test. Six duplicated lines with this cross-reference cost less than
    editing a shipped gate that exists to catch exactly this.
    """
    import socket

    text = str(selector).strip()
    if _scope._is_linear_link_selector(text):
        normalised = _scope._normalise_url(text)
        if normalised is None:
            return Unreachable(kind=kind, target=text,
                               reason_slot="unreachable_linear",
                               detail="not an http(s) location")
        host = normalised.host.split(":", 1)[0]
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror as e:
            if getattr(e, "errno", None) in (socket.EAI_NONAME, socket.EAI_NODATA):
                return Unreachable(kind=kind, target=normalised.render(),
                                   reason_slot="unreachable_linear",
                                   detail=f"no such host: {host}")
            return Reachable(kind=kind, target=normalised.render())
        except Exception:                   # noqa: BLE001 — never a false block
            return Reachable(kind=kind, target=normalised.render())
        return Reachable(kind=kind, target=normalised.render())
    return Reachable(kind=kind, target=text)


def _probe_path(kind: str, selector: str) -> ProbeResult:
    """Existence and type only. No open(), no read, no walk.

    Shared by every path-shaped kind — `code`, `knowledge_library`,
    `document_folder` (S7). A declared folder that does not exist is refused HERE,
    at selection time, which is where a person can still do something about it,
    rather than mid-run where it would reach them as a finding that simply is not
    there.
    """
    p = Path(selector).expanduser()
    try:
        resolved = p.resolve(strict=False)
    except OSError as e:                       # pragma: no cover - platform-specific
        return Unreachable(kind=kind, target=selector,
                           reason_slot="unreachable_path", detail=f"{type(e).__name__}: {e}")
    if resolved.is_dir() or resolved.is_file():
        return Reachable(kind=kind, target=str(resolved))
    return Unreachable(kind=kind, target=str(resolved),
                       reason_slot="unreachable_path",
                       detail="no file or directory at this path")


# --------------------------------------------------------------------------- #
# Assembly.
# --------------------------------------------------------------------------- #

def build_record(selection: Selection,
                 route: Optional[str] = None,
                 prefilled: Optional[Mapping[str, Sequence[str]]] = None) -> ScopeRecord:
    """Assemble the declaration. Raises :class:`ScopeRecordError` upward, unswallowed.

    A refusal from ``scope_record`` — a declared ``CLAUDE.md``, a secret-shaped
    connection id, an unregistered kind — is surfaced to the person as a stated
    reason and a re-prompt. It is never caught and turned into a silent drop: the
    prohibition exists to be visible.

    **`route` (S7) is a real code enforcement point, not a display filter.** This
    function refuses to assemble a record for a class the calling route cannot
    read. That matters because this is the door no source list guards: the
    `/clarification` step at ``skills/clarification/steps.md:373`` builds a
    declaration **on the user's behalf**, outside the picker entirely, and names
    these two classes by anticipation — so registering them arms that door too. A
    caller passing its route gets the same refusal a ticked row would have got.

    **`prefilled` (S7, design-A21) is ADDITIVE and per-class**, never a
    module-level "current topic". Entries filled in on a person's behalf are
    appended to the selectors that person typed, and the result is
    INDISTINGUISHABLE in the record from a typed entry — which is the point: the
    record IS what the approval gate renders, so a pre-filled entry is shown by
    construction. Anything filled in that a person does not see would be a new
    bypass in the shape of a convenience.
    """
    if not selection.bounds:
        raise SourcePickerError(
            "an empty selection has no record — it is the EmptySelection outcome, "
            "not a ScopeRecord with no sources (design-U11)")
    prefilled = dict(prefilled or {})
    unknown = sorted(set(prefilled) - {e.key for e in CATALOGUE})
    if unknown:
        raise SourcePickerError(
            f"pre-filled entries name unknown source class(es) {unknown}; "
            f"catalogue holds {sorted(_BY_KEY)}")
    sources = []
    for key, bound in selection.bounds:
        e = entry(key)
        if not is_selectable(e, route):
            if e.registered_kind not in _scope.REGISTERED_KINDS:
                raise SourcePickerError(
                    f"source class {key!r} is not selectable: its kind "
                    f"{e.registered_kind!r} is not registered")
            # THREE inputs decide selectability, so three reasons can land here.
            # This branch existed for only two, and reported the ROUTE reason for
            # a class refused on the DRIVER one — telling a person their route
            # cannot read a source when in fact nothing can read it anywhere.
            # Unreachable before S8: `KNOWN_UNREACHABLE` was empty, so no
            # registered kind could be refused for want of a driver. Session 1
            # registers `linear` while its driver is Session 3's, which makes this
            # the first slice where the wrong reason could actually be printed.
            if e.registered_kind in _reach.KNOWN_UNREACHABLE:
                why, owner = _reach.KNOWN_UNREACHABLE[e.registered_kind]
                raise SourcePickerError(
                    f"source class {key!r} has no production driver, so no "
                    f"declaration is assembled for it: {why} (owner: {owner})")
            raise SourcePickerError(
                f"source class {key!r} cannot be read on the {route!r} route, so "
                "no declaration is assembled for it; a source that would never be "
                "opened must not be declared as though it had been")
        if bound.unscoped and not e.allows_unscoped:
            raise SourcePickerError(
                f"source class {key!r} must be bounded by selectors; it cannot be "
                "declared as 'whatever this source exposes'")
        selectors = tuple(bound.selectors)
        for extra in prefilled.get(key, ()):
            if str(extra) not in selectors:
                selectors += (str(extra),)
        if bound.unscoped and prefilled.get(key):
            raise SourcePickerError(
                f"source class {key!r} was declared unscoped but also carries "
                "pre-filled selectors; the two are different declarations")
        sources.append(DeclaredSource(
            kind=e.registered_kind,
            scope_mode=SCOPE_MODE_UNSCOPED if bound.unscoped else SCOPE_MODE_ENUMERATED,
            selectors=selectors,
            connection_id=bound.connection_id,
        ))
    return ScopeRecord(sources=tuple(sources))


def knowledge_library_prefill(topic_folder: Path,
                              projects_root: Path,
                              claude_md_path: Optional[Path] = None) -> Tuple[str, ...]:
    """BOTH halves of the knowledge-library pre-fill (design-A21).

    A person picking "your knowledge library" is not asked to remember where it
    lives. Two halves are filled in for them, and **both** must reach the record:

    * the folders their project declares (`research_library_folders:` in its
      CLAUDE.md — `internal_kb.read_research_library_folders`), and
    * the research files belonging to the topic they are working on
      (`internal_kb.discover_research_files`).

    Half a pre-fill, ungated, is how the thing a person never sees gets read: the
    declared-folder half has no other declaration surface anywhere, so if it did
    not enter here it would be read on a declaration nobody assembled — which is
    the bypass this slice exists to close, reproduced one level down.

    Returns selector strings destined for `build_record(prefilled=...)`, so they
    land in the record indistinguishable from typed ones and are therefore shown
    at the approval gate by construction. The CLAUDE.md is read as a POINTER to
    folders and is never itself a selector — a declared CLAUDE.md is refused at
    record construction anyway.
    """
    from . import internal_kb as _ikb

    if claude_md_path is None:
        claude_md_path, _ = _ikb.walk_up_topic_claude(topic_folder, projects_root)
    out: List[str] = []
    for folder in _ikb.read_research_library_folders(claude_md_path, projects_root):
        if str(folder) not in out:
            out.append(str(folder))
    for research_file in _ikb.discover_research_files(topic_folder):
        resolved = str(research_file.resolve())
        if resolved not in out:
            out.append(resolved)
    return tuple(out)


# --------------------------------------------------------------------------- #
# The entry point.
# --------------------------------------------------------------------------- #

def open_picker(framed_question: str,
                is_spike: bool,
                selection: Optional[Selection] = None) -> PickerOutcome:
    """Turn a framed question plus what was ticked into one of three outcomes.

    `is_spike` is the AI's judgment, rendered **behind this port**: the caller
    supplies the verdict and this function owns the consequence, so "a spike never
    reaches the catalogue" is structural rather than a matter of the flow
    remembering to check (design-U9).

    Returns :class:`SpikeStop`, :class:`EmptySelection`, or the :class:`Selection`.
    Neither terminal outcome carries a record, so nothing downstream can register
    what does not exist.
    """
    if is_spike:
        return SpikeStop()
    if selection is None or len(selection) == 0:
        return EmptySelection()
    return selection

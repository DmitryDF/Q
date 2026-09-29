"""The admission port — the single passage point every source is admitted through.

research-source-adapters S3 / plan action A4 (design-A1, design-A9, design-A11's
enforcement half, design-A20).

This module is the contract. Every later source class is written against the
shape decided here, which is why the effort goes into it rather than into the
adapters (locked Guiding Policy: "Concentrate effort on that contract, not on
the adapters").

What the port owns, and nothing else does
-----------------------------------------
* **The three-part pin** — source identity, the version read, and a locator drawn
  from the registered per-kind grammar (:mod:`locator_grammar`).
* **The declared-scope check**, run BEFORE any read (:mod:`scope_record`). There
  is no admit path that skips it.
* **The ``CLAUDE.md`` prohibition** (design-A11). It lives here and nowhere else.
  A ``CLAUDE.md`` inside a declared directory is *not* filtered out of
  enumeration — a skip would be silent, and a silent disposal is exactly the
  failure mode a prohibition living in an adapter has. It reaches ``admit()``
  like any other item and leaves a **recorded degradation**.
* **All four bounds**, as module constants the adapter never sees: enumeration
  depth, item count, the per-item read budget, and the **aggregate** run budget.
  The adapter yields lazily and the port stops consuming, so an adapter cannot
  widen a bound *by construction* rather than by convention. The aggregate is the
  one that actually protects the shared checker zone — the per-item bound alone
  permits many items.
* **Evidence-path selection.** The adapter *declares a capability* and *reports*
  whether the tree was dirty; the port *decides* which branch applies and stamps
  it. An adapter that chose its own branch would not be thin.
* **The run identity.** ``run()`` mints ``run_id`` once and threads it onto every
  record the run writes, so ``list_degradations(run_id)`` is answerable against
  its own record.

What it deliberately does NOT own
---------------------------------
* **No credential resolution.** Of the credential path this slice builds exactly
  one thing: the non-secret ``connection_id`` field on ``DeclaredSource``, owned
  and tested by :mod:`scope_record`. There is no ``credential_path.py`` here —
  that module is S8's, and pre-empting it is not this slice's to do.
* **No grading of what a source yields.** There is no grading rule anywhere: the
  per-claim scoring layer was descoped and design-A8 withdrawn (spine Q22/Q23),
  and slice D5 deleted its code on 2026-09-01. This bullet stays because the
  PORT's boundary is unchanged — it records what a source yields and judges none
  of it — which was true when the rule lived downstream and is true now that no
  rule exists.
* **No run-level INCOMPLETE roll-up**, and no surfacing at all — no picker (S4),
  no report (S11). This slice produces durable pins and durable degradation
  records and *shows* none of them to anyone.

  This bullet and the run-identity one above used to assign the roll-up to **S13
  by name**. S13 shipped (2026-09-09) and **declined to build it**, so the
  assignment is removed rather than left pointing at a finished slice. Two
  findings drove the withdrawal, and the first corrects a framing this topic
  carried for several slices:

  - ``list_degradations(run_id)`` reads exactly ONE run document
    (``admission_record.py:396-400``), so it is **run-scoped, not cross-run**.
    The spine's table calls it a "cross-run degradation roll-up"; the code's own
    docstring says "Returns nothing from any other run in the same store." **The
    code is right and the prose is wrong.**
  - The **store-identity** half is real and unrepaired. D6 repaired the
    filename-derived writer for timestamped names — 108 of 251 reachable files
    now file under their own topic slug — but **143 still share one store**, and
    ``_resolve_web_admission`` still stamps its store from the clock
    (``_factcheck_engine.py:5333``). On the shared-fallback path a roll-up would
    return **another topic's** degradations: the unsafe direction, and the one
    V1-obs1 names.

  Where the outcome is delivered instead: the roll-up's user-visible purpose — a
  degraded run saying so rather than passing silently — is already met by S11's
  disclosure channel and S12's INCOMPLETE fold. What is missing is only the
  store-sourced enumeration, which is filed forward against the store-identity
  repair it depends on rather than built on an addressing scheme that would
  mis-attribute.

Every item ends in exactly one of two outcomes — admitted **with a pin**, or
rejected **with a stated reason** (C3). There is no path that returns neither,
which is why even an unexpected adapter exception becomes a degradation naming
the failure rather than propagating out of the run.

**Failures are written at the moment they are reached** (C11), not flushed at the
end: :meth:`AdmissionPort.admit` persists each outcome through the store before
it returns, so a reader observing the store part-way through a run sees exactly
the items decided so far.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

from . import admission_record as _rec
from . import locator_grammar as _loc
from . import scope_record as _scope
from .content_shape import not_the_document
from .admission_record import (
    AdmissionRecordStore,
    DegradationRecord,
    EvidenceEntry,
    EVIDENCE_CAPTURED,
    EVIDENCE_ORIGINAL,
)
from .locator_grammar import Locator, LocatorError, get_kind
from .scope_record import ScopeRecord

# --------------------------------------------------------------------------- #
# The four bounds. ONE home — the adapter holds none of them and never sees them.
#
# The two enumeration bounds are editorial-but-safe: exceeding one only ever
# causes a RECORDED DEGRADATION, never a wrong answer, and the degradation names
# the path — which is the feedback channel by which a later slice learns the
# number is too low.
#
# The two size budgets are anchored, not free. The fact-check engine's assembled
# zone is a hard ceiling of `_ZONE_MAX_TOTAL_BYTES` ~347 KB **for the whole zone
# across all sources** (`_factcheck_engine.py:3141`), and over-budget content
# there is TRUNCATED with a note (`:963`) rather than refused. So the per-item
# bound must leave room for many items under the aggregate, and the aggregate is
# deliberately ~55% of the shared ceiling — headroom is left for the source
# classes S7 and S8 add to the same one ceiling.
# --------------------------------------------------------------------------- #

MAX_DEPTH = 12                      # directory levels below a declared path
MAX_ITEMS = 2000                    # items enumerated per run
MAX_ITEM_BYTES = 64 * 1024          # one item's excerpt
MAX_RUN_BYTES = 192 * 1024          # all admitted excerpts in one run

# --------------------------------------------------------------------------- #
# Per-kind rules (S6, design-A29). The four names above KEEP their values and
# become the `code` entry — they are not a second copy, and `test_a4_bounds_live
# _only_in_the_port` still pins them.
#
# Two of these are NUMBERS and two are POLICIES, and the distinction is the whole
# reason this table exists rather than a wider set of constants:
#
# * `max_item_bytes` / `max_run_bytes` — numbers. `None` means "the caller must
#   supply it": web's effective per-item bound is a fair share computed per run
#   from the citation count, so it CANNOT be a constant, and copying the engine's
#   derived aggregate here would create a second home for a number with no drift
#   guard. A caller may only ever NARROW a kind's ceiling, never widen it, so
#   "the adapter cannot widen a bound" survives intact.
# * `truncate_over_budget` — a policy. `code` refuses an over-budget item WHOLE
#   because a truncated excerpt makes its `path:lines` locator address more than
#   was read: the pin would lie. A `url` locator addresses the PAGE, not a byte
#   range, so a truncated web read makes its locator no less true — and web
#   already truncates today. Carrying web's numbers across without this policy
#   would have turned every oversized page into a degraded item, i.e. a passing
#   report into INCOMPLETE, which is precisely the visible change design-A29
#   forbids.
# * `path_shaped` — whether an item's target is a filesystem path. The
#   `CLAUDE.md` prohibition is a BASENAME test, so without this a cited URL whose
#   path ends `/CLAUDE.md` would degrade where today it is fetched normally.
#
# S7 SPLIT THREE JOBS OUT OF ONE FLAG (design-A21/A24/A31).
# ---------------------------------------------------------
# Until S7, `path_shaped` answered THREE questions at once — which citation form
# to render, whether the `CLAUDE.md` basename prohibition applies, and how a
# refusal resolves its target — and it happened to give the right answer to all
# three only because the two registered kinds differed on all three together.
# S7 registers two kinds that are path-shaped AND render a third form, so the
# coincidence breaks: the shipped `local-file` marker
# (`_factcheck_engine.py:332`) carries NO version, and neither existing branch
# produces it.
#
# The repair is the policy's, not a local one: name each concern as its own
# field rather than branch on the class.
#
# * `render_form` — WHICH citation shape `SourcePin.render()` emits. Three
#   values, one per shipped vocabulary (see `RENDER_FORM_*` below).
# * `citation_prefix` — the vocabulary NAME for `RENDER_FORM_PREFIXED`. Two
#   distinct source kinds share one citation vocabulary here, and this field is
#   what makes that sharing EXPLICIT rather than an accident of naming: the
#   locator kinds are deliberately separate (`locator_grammar.py`), because
#   `KIND_RULES` is keyed by the locator kind at `render()` and by the source
#   kind at `admit()`, so one shared locator kind would split the table.
# * `version_is_revision` — what `version` MEANS for this kind, which only the
#   re-open instruction needs. For `code` it is a VCS revision a working tree can
#   be *clean at*; for an on-disk file it is the instant it was read, and saying
#   "the working tree was clean at <mtime>" would be false. This is the third job
#   `path_shaped` was doing, and it is the one that would have shipped unnoticed:
#   it lives in the admission record rather than the citation, so it breaks no
#   marker and no drift guard would have caught it.
#
# `path_shaped` KEEPS its other two jobs — the basename prohibition (`:580`) and
# how a refusal resolves its target — and loses only render selection.
# --------------------------------------------------------------------------- #

# The three citation shapes this port renders. Each is a SHIPPED vocabulary; a
# fourth value would mean a new marker, which is a drift-guarded three-loci edit
# (design-A17), not a `KindRules` row.
RENDER_FORM_VERSIONED = "versioned"        # code:<id>@<version>:<locator>
RENDER_FORM_LOCATOR_ONLY = "locator_only"  # <locator> alone (web: the URL)
RENDER_FORM_PREFIXED = "prefixed"          # <prefix>:<locator>, no version

RENDER_FORMS = (RENDER_FORM_VERSIONED, RENDER_FORM_LOCATOR_ONLY,
                RENDER_FORM_PREFIXED)

# The citation vocabulary the two S7 on-disk kinds share. Already an ACTIVE
# marker (`_factcheck_engine.py:332` — `[stated — local-file:<path>:<line>]`),
# which is why this slice adds, retires and renames no marker: if S7 finds itself
# editing `CITATION_MARKER_REGISTRY`, A3 was solved wrongly.
LOCAL_FILE_CITATION_PREFIX = "local-file"


@dataclass(frozen=True)
class KindRules:
    """The per-item rules that differ by source kind. One row per registered kind."""

    max_item_bytes: Optional[int]        # None => caller-supplied, mandatory
    max_run_bytes: Optional[int]         # None => caller-supplied, mandatory
    truncate_over_budget: bool
    path_shaped: bool
    render_form: str = RENDER_FORM_VERSIONED
    citation_prefix: Optional[str] = None
    version_is_revision: bool = True

    def __post_init__(self) -> None:
        if self.render_form not in RENDER_FORMS:
            raise ValueError(
                f"unknown render_form {self.render_form!r}; known forms: "
                f"{list(RENDER_FORMS)} — a new citation shape is a drift-guarded "
                "marker-registry edit, not a rules row")
        if self.render_form == RENDER_FORM_PREFIXED and not self.citation_prefix:
            raise ValueError(
                "render_form 'prefixed' needs a citation_prefix; the prefix IS "
                "the vocabulary name and cannot be inferred from the kind")
        if self.render_form != RENDER_FORM_PREFIXED and self.citation_prefix:
            raise ValueError(
                f"citation_prefix {self.citation_prefix!r} is set but render_form "
                f"is {self.render_form!r}, which does not emit a prefix")


KIND_RULES = {
    "code": KindRules(max_item_bytes=MAX_ITEM_BYTES, max_run_bytes=MAX_RUN_BYTES,
                      truncate_over_budget=False, path_shaped=True,
                      render_form=RENDER_FORM_VERSIONED,
                      version_is_revision=True),
    "web": KindRules(max_item_bytes=None, max_run_bytes=None,
                     truncate_over_budget=True, path_shaped=False,
                     render_form=RENDER_FORM_LOCATOR_ONLY,
                     version_is_revision=False),
    # The two S7 rows (design-A21, A24, A31). Both take the MODULE CONSTANTS
    # rather than `None`: an on-disk file has no caller-computed fair share the
    # way a web run does, so there is no caller to supply one, and `None` would
    # make every admission raise.
    #
    # `truncate_over_budget=False` is `code`'s reasoning, not web's, and it
    # follows from the LOCATOR rather than from a preference: a `path:line`
    # locator addresses a byte range, so a truncated excerpt would make the pin
    # address more than was read — the pin would lie. Web's opposite answer came
    # from its locator addressing a whole page. Same rule, different locators.
    "knowledge_library": KindRules(
        max_item_bytes=MAX_ITEM_BYTES, max_run_bytes=MAX_RUN_BYTES,
        truncate_over_budget=False, path_shaped=True,
        render_form=RENDER_FORM_PREFIXED,
        citation_prefix=LOCAL_FILE_CITATION_PREFIX,
        version_is_revision=False),
    "document_folder": KindRules(
        max_item_bytes=MAX_ITEM_BYTES, max_run_bytes=MAX_RUN_BYTES,
        truncate_over_budget=False, path_shaped=True,
        render_form=RENDER_FORM_PREFIXED,
        citation_prefix=LOCAL_FILE_CITATION_PREFIX,
        version_is_revision=False),
    # The S8 row (design-A12) — the first TRACKER, and the first row that is
    # neither path-shaped nor a URL. Every field here is a decision, not a copy:
    #
    # * MODULE CONSTANTS, not `None`. `None` means "the caller computes a fair
    #   share", which exists for `web` because a web run splits one budget across
    #   a known citation count. A tracker has no such caller, so `None` would make
    #   every Linear admission raise.
    # * `truncate_over_budget=False` — `code`'s reasoning and the S7 kinds', which
    #   follows from the LOCATOR rather than from taste: an `<issue>` locator
    #   addresses a bounded item, so a truncated excerpt would make the pin address
    #   more than was read. The pin would lie. Web's opposite answer came from its
    #   locator addressing a whole page.
    # * `path_shaped=False` — an issue identity is not a filesystem path. This is
    #   load-bearing in TWO directions and both are deliberate. It keeps a refusal
    #   from rendering `LIN-123` as `<cwd>/LIN-123` (with `_display_target`'s new
    #   arm, A4b), and it REMOVES the `CLAUDE.md` basename prohibition from this
    #   kind, because that guard is gated on `path_shaped` (`:799`). That removal
    #   is correct rather than a hole — a tracker cannot name a `CLAUDE.md` — but
    #   it does mean A4b's containment arm is then the only thing between a
    #   declared tracker and an admission. Neither is safe alone.
    # * `render_form=RENDER_FORM_VERSIONED` — the version is the point. A tracker
    #   issue is mutable, so `linear:<workspace>@<version>:<issue>` is what makes a
    #   citation re-openable months later (C8) rather than merely present (C7).
    #   This is the first kind since `code` to need a NEW marker to render through,
    #   which is why S8 touches the citation vocabulary and S6/S7 did not.
    # * `version_is_revision=False` — the version is an issue version, not a VCS
    #   revision a working tree can be *clean at*. Saying "the working tree was
    #   clean at <version>" of a Linear issue would be false, which is exactly the
    #   distinction S7 split this field out to carry.
    "linear": KindRules(
        max_item_bytes=MAX_ITEM_BYTES, max_run_bytes=MAX_RUN_BYTES,
        truncate_over_budget=False, path_shaped=False,
        render_form=RENDER_FORM_VERSIONED,
        version_is_revision=False),
}

# The rules a kind nobody registered gets. Fail-closed in the direction that
# matters: refuse whole rather than truncate, treat the target as a path so the
# CLAUDE.md prohibition still applies, and inherit the tightest known numbers.
#
# **It must carry a render form explicitly.** Before S7 the versioned shape was
# what `path_shaped=True` implied; after the split it is a separate field, and an
# unregistered kind that inherited a default would silently re-acquire the
# versioned `code`-shaped citation while every registration looked complete. The
# versioned form is also the right fail-closed choice: it is the LOUDEST of the
# three (it names a kind and a version), so an unregistered kind's citation is
# conspicuous rather than plausible.
_DEFAULT_KIND_RULES = KindRules(
    max_item_bytes=MAX_ITEM_BYTES, max_run_bytes=MAX_RUN_BYTES,
    truncate_over_budget=False, path_shaped=True,
    render_form=RENDER_FORM_VERSIONED, version_is_revision=True)


def rules_for(kind: str) -> KindRules:
    """The per-item rules for `kind`. An unregistered kind gets the strict default."""
    return KIND_RULES.get(kind, _DEFAULT_KIND_RULES)


def _bound_label(const_name: str, ceiling: Optional[int], value: int) -> str:
    """How a bound is NAMED in a degradation.

    A degradation has to say which bound it hit, and the honest name differs by
    where the bound came from: the module constant when that is what bound the
    item, and "caller-supplied" when a caller narrowed it. Naming the constant in
    both cases would point a reader at a number that is not the one that fired.
    """
    if ceiling is not None and value == ceiling:
        return f"{const_name}={value}"
    return f"{value} (caller-supplied)"


def resolve_item_budget(kind: str, caller_budget: Optional[int]) -> int:
    """The per-item byte bound for one admission, given what the caller supplied.

    Two rules, and both directions are refusals rather than silent adjustments:

    * A kind whose ceiling is caller-supplied (`None`) and whose caller supplied
      nothing has **no bound**, and an unbounded read is not something this port
      performs — so it raises rather than defaulting to a number nobody chose.
    * A caller may **narrow** a kind's ceiling and may never **widen** it. That is
      what keeps the fair share (computed where the citation count is known) from
      becoming a way to enlarge what the port admits.
    """
    ceiling = rules_for(kind).max_item_bytes
    if caller_budget is None:
        if ceiling is None:
            raise ValueError(
                f"kind {kind!r} has no port-side per-item ceiling, so the caller "
                "must supply one; an unbounded read is never performed")
        return ceiling
    if caller_budget <= 0:
        raise ValueError(f"a caller-supplied budget must be positive, got {caller_budget}")
    if ceiling is None:
        return caller_budget
    return min(ceiling, caller_budget)

# The obligations a degradation can name. Enumerated in ONE place so a rejection
# record is comparable across source kinds — which is the whole point of placing
# the obligations in the port rather than letting each adapter discharge them in
# its own terms.
OBLIGATION_DECLARED_SCOPE = "declared-scope"
OBLIGATION_SOURCE_IDENTITY = "source-identity"
OBLIGATION_VERSION_READ = "version-read"
OBLIGATION_LOCATOR = "locator"
OBLIGATION_READ_BUDGET = "read-budget"
OBLIGATION_ENUMERATION_BOUND = "enumeration-bound"
OBLIGATION_READABLE = "readable"

CLAUDE_MD_BASENAME = _scope.CLAUDE_MD_BASENAME


class AdapterError(Exception):
    """Raised by an adapter when it cannot satisfy one named obligation.

    The adapter names the obligation; the port turns it into a recorded
    degradation. This is how E3 (a repository with no commits, so the "version
    read" part of the pin is unsatisfiable) reaches the record without the
    adapter needing to know what a degradation is.
    """

    def __init__(self, obligation: str, reason: str) -> None:
        super().__init__(f"{obligation}: {reason}")
        self.obligation = obligation
        self.reason = reason


# --------------------------------------------------------------------------- #
# Value types.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SourcePin:
    """The three-part citation pin: identity, version read, locator.

    Rendered as ``<kind>:<source_id>@<version>:<locator>`` — the payload of the
    ``[stated — code:<repo>@<rev>:<path>:<lines>]`` citation marker.
    """

    source_id: str
    version: str
    locator: Locator

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("a pin needs a source identity")
        if not self.version:
            raise ValueError("a pin needs the version that was read")

    @property
    def kind(self) -> str:
        return self.locator.kind

    def render(self) -> str:
        """Raises :class:`LocatorError` when the locator is incomplete — a pin can
        never carry a silently-shortened locator.

        **Web renders as its URL alone** (S6, design-A29). The shipped marker for a
        web source is `[stated — <url>]`, and design-A29 admits web behind the port
        on the condition that its existing markers are preserved rather than
        replaced; the general `<kind>:<id>@<version>:<locator>` form would render
        `[stated — web:example.com@…:https://…]`, which is a different vocabulary.
        Changing it would be a three-loci atomic edit under a drift guard
        (design-A17) — a much larger change than this slice, and one design-A29
        forbids outright.

        **The version does not vanish.** It cannot ride this marker without
        changing the vocabulary, so it is carried in the admission record instead,
        where the evidence entry and its re-open instruction both name it. That is
        a stated limit of the web pin, not a silent drop: `self.version` is still
        set and still refused if empty.

        **The two S7 on-disk kinds render a third form** (design-A21/A24/A31):
        `<prefix>:<locator>` with NO version, which is the shipped
        `[stated — local-file:<path>:<line>]` marker. The same "version does not
        vanish" reasoning applies verbatim — it rides the admission record.

        Dispatch is on `KindRules.render_form`, NOT on `path_shaped`. Until S7
        those were the same question by coincidence; the S7 kinds are path-shaped
        AND render the third form, which is what broke the coincidence.
        """
        rules = rules_for(self.locator.kind)
        if rules.render_form == RENDER_FORM_LOCATOR_ONLY:
            return self.locator.render()
        if rules.render_form == RENDER_FORM_PREFIXED:
            return f"{rules.citation_prefix}:{self.locator.render()}"
        return f"{self.locator.kind}:{self.source_id}@{self.version}:{self.locator.render()}"

    def marker(self, kind: str = "stated") -> str:
        """The full citation marker for this pin (A6's vocabulary)."""
        if kind not in ("stated", "paraphrased"):
            raise ValueError("a code pin is cited as 'stated' or 'paraphrased'")
        return f"[{kind} — {self.render()}]"


@dataclass(frozen=True)
class Degradation:
    """A rejected item: what it was, which obligation it failed, and why."""

    item: str
    obligation: str
    reason: str


@dataclass(frozen=True)
class AdmissionResult:
    """The outcome of one item. Exactly one of `pin` / `degradation` is set.

    C3's "no third outcome" is structural here: construction refuses both-or-
    neither, so a caller cannot be handed a result that means nothing happened.
    """

    item: str
    pin: Optional[SourcePin] = None
    degradation: Optional[Degradation] = None
    evidence_path: Optional[str] = None
    pin_str: Optional[str] = None
    # S6 (design-A29). What the port actually read, handed back to the caller.
    #
    # Until S6 the port read an item and returned everything ABOUT it — a pin, an
    # evidence path — and nothing OF it, and on the `original` branch it persisted
    # no excerpt either. So a caller that needed the bytes could not use the port
    # at all. That is not a separate gap from "the port has no production caller":
    # it is the same one seen from the other side, and it is why five slices of
    # contract work could stay green with nothing driving them.
    content: Optional[str] = None
    # True when the read was shortened to fit its budget. Reported by the adapter,
    # recorded by the port — never inferred, and never silent (see `KindRules`).
    truncated: bool = False
    # S10 (design-A13, plan action A1). HOW NARROWLY the declaration that admitted
    # this item was drawn. `declaration_bound` is the admitting declaration's
    # `scope_mode`; `matched_selector` is the selector that contained the item,
    # when one did.
    #
    # AMENDED 2026-08-31 — these two fields are RETAINED; the consumer they were
    # built for is not. This comment used to describe `declaration_bound` as "the
    # first of the three grounds a per-claim grade rests on". Per-claim grading is
    # DESCOPED (spine `## Q&A` Q22/Q23; design A8/A13/A25 withdrawn), so there is
    # no grade and no three grounds. The fields stay because they are DATA THE PORT
    # RECORDS rather than a grading rule — design-A8 kept the rule out of the port
    # deliberately, and that separation is exactly what lets the rule go while this
    # stays. Deleting them alongside the grader would remove working scope: they
    # record how narrowly a person bounded their sources, which is a fact about the
    # declaration and is useful to any later reader of the admission record.
    #
    # The `S10` attribution above is left as the provenance of when these fields
    # arrived. It is not a claim that S10 survives.
    #
    # The port has always COMPUTED this: `scope.check()` runs before every read
    # and its result carried the selector all along. What the port did with it was
    # branch on `.admitted` and drop the rest — so the one place that knew how
    # narrowly a person had bounded their sources was also the place that forgot.
    # This stops the discard; it decides nothing (design-A8 keeps the rule out of
    # the port), and a degraded result carries neither, having no admission behind
    # it.
    declaration_bound: Optional[str] = None
    matched_selector: Optional[str] = None

    def __post_init__(self) -> None:
        if (self.pin is None) == (self.degradation is None):
            raise ValueError(
                "an admission result is a pin OR a degradation — never both and "
                "never neither (C3)")
        if self.pin is not None and not self.evidence_path:
            raise ValueError(
                "an admitted item must carry how it was evidenced (C13)")

    @property
    def admitted(self) -> bool:
        return self.pin is not None


@dataclass(frozen=True)
class SourceItem:
    """One enumerated candidate, before any read.

    `depth` is DATA the adapter reports, not a bound it enforces — the port holds
    the constant and compares. That split is what makes "the adapter cannot widen
    a bound" structural.
    """

    item_id: str
    kind: str
    target: str
    depth: int = 0


@dataclass(frozen=True)
class ReadResult:
    """What an adapter returns for one item it could read.

    The adapter reports `dirty`; it does NOT choose the evidence branch. The port
    reads this flag together with the adapter's declared capability and decides.
    """

    source_id: str
    version: str
    locator: Locator
    content: str
    dirty: bool = False
    # S6: the adapter REPORTS that it shortened the read; the port records it.
    # Same split as `dirty` — the adapter reports a fact about what it did, the
    # port decides what that fact means.
    truncated: bool = False


# --------------------------------------------------------------------------- #
# The adapter interface + its test double.
# --------------------------------------------------------------------------- #

class SourceAdapter(ABC):
    """What a source class must provide. Deliberately three things and no more.

    `enumerate_within` MUST be a lazy generator that applies no bound and holds
    no bound constant — the port consumes it and stops.
    """

    kind: str = ""
    can_reopen_without_credentials: bool = False

    @abstractmethod
    def enumerate_within(self, scope: ScopeRecord) -> Iterator[SourceItem]:
        """Yield candidate items lazily. Filters nothing the port must decide."""

    @abstractmethod
    def read_with_pin(self, item: SourceItem) -> ReadResult:
        """Read one item. Raise :class:`AdapterError` naming the failed obligation."""


class FakeAdapter(SourceAdapter):
    """Deterministic test double — the test-to-test rung of the Cockburn order.

    No I/O, no clock, no randomness: `items` and `reads` are supplied by the
    test, so the port's own behaviour is what a test observes.
    """

    kind = "code"

    def __init__(self,
                 items: Sequence[SourceItem] = (),
                 reads: Optional[dict] = None,
                 *,
                 can_reopen_without_credentials: bool = True,
                 on_yield=None) -> None:
        self._items = tuple(items)
        self._reads = dict(reads or {})
        self.can_reopen_without_credentials = can_reopen_without_credentials
        self._on_yield = on_yield
        self.yielded: List[str] = []

    def enumerate_within(self, scope: ScopeRecord) -> Iterator[SourceItem]:
        for item in self._items:
            self.yielded.append(item.item_id)
            if self._on_yield is not None:
                # Lets a test observe the store BETWEEN items, which is what
                # distinguishes "written at the moment it is reached" from a
                # flush at end of run (C11).
                self._on_yield(item)
            yield item

    def read_with_pin(self, item: SourceItem) -> ReadResult:
        result = self._reads.get(item.item_id)
        if result is None:
            raise AdapterError(OBLIGATION_READABLE,
                               f"no canned read for {item.item_id!r}")
        if isinstance(result, BaseException):
            raise result
        return result


# --------------------------------------------------------------------------- #
# The port.
# --------------------------------------------------------------------------- #

class _RunBudget:
    """Mutable aggregate counter for one run.

    S6 (design-A29) made this a **public seam** via :func:`run_budget`. It was
    private while `run()` was the only thing that constructed one; a caller that
    drives `admit()` per item — which is what the engine's web ingest does, because
    the loop that owns the fair share and the wall-clock cap is the engine's — has
    to be able to supply the aggregate, or the aggregate bound simply would not
    apply to that caller at all.
    """

    def __init__(self, limit: int = MAX_RUN_BYTES) -> None:
        self.limit = limit
        self.spent = 0
        #: Size of the first item that did not fit, or None while the run has
        #: room. A DRIVER READS THIS TO STOP; see `exhausted` below.
        self.first_over: Optional[int] = None

    def would_exceed(self, size: int) -> bool:
        over = self.spent + size > self.limit
        if over and self.first_over is None:
            self.first_over = size
        return over

    @property
    def exhausted(self) -> bool:
        """Whether the AGGREGATE has refused an item — the signal to stop.

        WHY A DRIVER NEEDS THIS, AND WHY IT CANNOT DERIVE IT ITSELF. `admit()`
        reads an item BEFORE it consults the aggregate (the read is step 3, the
        aggregate is step 6), so a driver that keeps going after the budget is
        gone re-reads every remaining candidate in full — for `code`, four `git`
        subprocesses and a `read_bytes()` each — and emits one refusal line per
        unread file into the report. On a repository of any size that is
        thousands of subprocesses and a report of refusals, which is not the
        "reached its limit and stopped" the outcome promises.

        A driver cannot get this from the obligation alone: the per-item ceiling
        and the aggregate BOTH degrade as `OBLIGATION_READ_BUDGET`, and stopping
        on the per-item one would let a single oversized vendored file truncate
        an entire run. The two are distinguishable HERE and only here, because
        `admit()` returns at the per-item check before ever calling
        `would_exceed` — so this flag is set by the aggregate and by nothing else.
        """
        return self.first_over is not None

    def charge(self, size: int) -> None:
        self.spent += size


def run_budget(limit: Optional[int] = None, *, kind: Optional[str] = None) -> _RunBudget:
    """Construct the aggregate budget for one run — the public seam (S6).

    Resolves the same way the per-item bound does: a kind whose aggregate is
    caller-supplied and whose caller supplied nothing has no bound, and this port
    does not perform unbounded runs, so it raises rather than defaulting.
    """
    if limit is None:
        ceiling = rules_for(kind).max_run_bytes if kind is not None else MAX_RUN_BYTES
        if ceiling is None:
            raise ValueError(
                f"kind {kind!r} has no port-side aggregate ceiling, so the caller "
                "must supply one; an unbounded run is never performed")
        limit = ceiling
    if limit <= 0:
        raise ValueError(f"a run budget must be positive, got {limit}")
    return _RunBudget(limit=limit)


# --------------------------------------------------------------------------- #
# The bounded candidate stream (code-source-driver-bounded-read A1).
#
# WHY THIS IS A MODULE-LEVEL FUNCTION AND NOT A PORT METHOD. The one test that
# distinguishes reading THROUGH the port from reading beside it stubs a port with
# `admit` and nothing else (`test_local_file_admission.py`'s `_AlteringPort`). A
# port method would make every driver that consumes the stream call something that
# stub does not have — so either the divergence test breaks, or the driver stops
# going through the stub and the test silently stops proving anything. A free
# function keeps the bounds one definition site AND keeps the stub sufficient.
#
# WHY IT EXISTS AT ALL. `run()` held the two enumeration bounds inline and
# discards every result it produces (`:658-659`), so a caller that needs the
# CONTENT could not use it — which is how the two enumeration bounds came to be
# shipped and unreachable on the production read path. Extracting them here makes
# them consumable without making them re-implementable: a driver consumes the
# stream, it does not re-derive the ceilings, and no bound moved out of this
# module.
# --------------------------------------------------------------------------- #

#: What one step of :func:`bounded_items` yields: a candidate to admit, or the
#: bound that stopped it. A consumer branches on the type; nothing else is needed,
#: because a degradation the stream yields is terminal-or-skipped by the stream
#: itself and the consumer never has to know which.
BoundedCandidate = Union[SourceItem, Degradation]


class ItemCounter:
    """How many items a RUN has enumerated, across every kind it reads.

    `MAX_ITEMS` is defined as "items enumerated per run" (`:98`), and a driver
    that reads three declared kinds calls :func:`bounded_items` three times. Given
    a fresh count each call, three kinds would enumerate 3 × `MAX_ITEMS` — the
    per-kind reading of a per-run bound, which is the same mistake the aggregate
    byte budget deliberately avoids by being constructed once per run.

    Passing one of these threads the count across those calls. Omitting it keeps
    the single-adapter behaviour `run()` has always had.
    """

    def __init__(self) -> None:
        self.seen = 0


def bounded_items(adapter: SourceAdapter,
                  scope: ScopeRecord,
                  counter: Optional[ItemCounter] = None) -> Iterator[BoundedCandidate]:
    """Consume an adapter's lazy enumeration under the two ENUMERATION bounds.

    Yields each candidate that passes, and a :class:`Degradation` for each one a
    bound rejected. The two bounds behave exactly as they did inline in
    :meth:`AdmissionPort.run`, and for the same reasons:

    * **MAX_ITEMS is terminal.** The bound is reached AT this item, so the item is
      named and the adapter's generator is abandoned here — which is what makes the
      bound the port's and not the adapter's.
    * **MAX_DEPTH skips one item.** A too-deep item is rejected; enumeration
      continues, because depth is a property of that item rather than of the run.

    The two SIZE budgets are not here: they are per-admission and live in
    :meth:`AdmissionPort.admit`, which is the only place that has read anything to
    measure.

    `counter` threads the item count across several calls so `MAX_ITEMS` bounds
    the RUN rather than each kind separately — see :class:`ItemCounter`.
    """
    counter = ItemCounter() if counter is None else counter
    for item in adapter.enumerate_within(scope):
        counter.seen += 1
        if counter.seen > MAX_ITEMS:
            yield Degradation(
                item=item.item_id,
                obligation=OBLIGATION_ENUMERATION_BOUND,
                reason=f"enumeration item-count bound MAX_ITEMS={MAX_ITEMS} "
                       f"reached; this item and any after it were not read")
            return
        if item.depth > MAX_DEPTH:
            yield Degradation(
                item=item.item_id,
                obligation=OBLIGATION_ENUMERATION_BOUND,
                reason=f"enumeration depth {item.depth} exceeds bound "
                       f"MAX_DEPTH={MAX_DEPTH}")
            continue
        yield item


class SourceAdmissionPort(ABC):
    """The abstract admission contract. Two entry points, both named.

    ``admit`` is where the per-item contract lives; ``run`` is where the bounds
    and the run identity live.
    """

    @abstractmethod
    def admit(self,
              item: SourceItem,
              scope: ScopeRecord,
              adapter: SourceAdapter,
              run_id: str,
              budget: Optional["_RunBudget"] = None,
              item_budget: Optional[int] = None) -> AdmissionResult:
        """One item in, a pin or a degradation out. Never neither, never both."""

    @abstractmethod
    def run(self,
            scope: ScopeRecord,
            adapter: SourceAdapter,
            run_id: Optional[str] = None) -> str:
        """Drive one admission run end to end. Returns the minted ``run_id``."""


class AdmissionPort(SourceAdmissionPort):
    """The one implementation. Code-owned; holds no judgment.

    Construction takes the store only — the scope record, the adapter and the run
    identity are per-run arguments, so one port instance can drive many runs and
    a test can drive ``admit()`` directly against an explicit scope.
    """

    def __init__(self, store: AdmissionRecordStore) -> None:
        self.store = store

    # -- run ---------------------------------------------------------------- #

    def run(self,
            scope: ScopeRecord,
            adapter: SourceAdapter,
            run_id: Optional[str] = None) -> str:
        """Mint the run id, consume the adapter's generator under the four
        bounds, admit each item, and persist every outcome as it happens.

        The two enumeration bounds now live in :func:`bounded_items`, which this
        method CONSUMES rather than re-implements — one definition site, and the
        behaviour is unchanged: same messages, same terminal-vs-skip semantics,
        same order. This method still DISCARDS every admission result, which is
        why it remains unusable by a caller that needs the content and why it
        still has no production caller.
        """
        run_id = run_id or uuid.uuid4().hex[:12]
        budget = _RunBudget()
        for candidate in bounded_items(adapter, scope):
            if isinstance(candidate, Degradation):
                self._record_degradation(candidate, run_id)
                continue
            self.admit(candidate, scope, adapter, run_id, budget)
        return run_id

    # -- admit -------------------------------------------------------------- #

    def admit(self,
              item: SourceItem,
              scope: ScopeRecord,
              adapter: SourceAdapter,
              run_id: str,
              budget: Optional[_RunBudget] = None,
              item_budget: Optional[int] = None) -> AdmissionResult:
        """The per-item contract. Persists its own outcome before returning.

        `item_budget` (S6) is the caller-supplied per-item bound. It may only
        NARROW the kind's own ceiling, never widen it — see `resolve_item_budget`.
        It exists because web's effective bound is a fair share computed from a
        citation count the port never sees, and a constant here would preserve a
        number while changing the behaviour.
        """
        # 1 — declared scope, BEFORE the read. A refusal names the RESOLVED path,
        #     so a symlinked escape is reported by where it actually landed.
        check = scope.check(item.kind, item.target)
        if not check.admitted:
            return self._degrade(item, OBLIGATION_DECLARED_SCOPE,
                                 f"{check.resolved_target}: {check.reason}", run_id)
        # S10/A1 — carried, not judged. Read off the SAME result the branch above
        # decided on, so the recorded bound is by construction the bound that
        # admitted this item and cannot drift from it. `getattr` because the check
        # result is a union whose refusal half has no such field, and because a
        # caller may hand in a scope object of its own shape.
        declaration_bound = getattr(check, "scope_mode", None)
        matched_selector = getattr(check, "matched_selector", None)

        # 2 — the prohibition. One mechanism, at the single passage point. The
        #     adapter filtered nothing, so this file reached here like any other
        #     and leaves a record instead of vanishing.
        #
        #     S6: applied to PATH-SHAPED kinds only. It is a basename test, and
        #     `Path("https://x.com/docs/CLAUDE.md").name` is `CLAUDE.md` — so
        #     without this guard a perfectly ordinary web page would degrade for
        #     being named like a system file that is not even on this machine. The
        #     prohibition is about a local system file being quoted as a research
        #     source; a page on the web that happens to share its name is a page.
        if rules_for(item.kind).path_shaped and Path(item.target).name == CLAUDE_MD_BASENAME:
            return self._degrade(
                item, OBLIGATION_SOURCE_IDENTITY,
                f"{CLAUDE_MD_BASENAME} may never be a source identity; it is a "
                "pointer to where documents live, not a document to quote",
                run_id)

        # 3 — the read. Any failure becomes a degradation; nothing propagates,
        #     because a propagating exception would be the third outcome C3 forbids.
        try:
            read = adapter.read_with_pin(item)
        except AdapterError as e:
            return self._degrade(item, e.obligation, e.reason, run_id)
        except OSError as e:
            return self._degrade(item, OBLIGATION_READABLE,
                                 f"{type(e).__name__}: {e}", run_id)
        except Exception as e:  # noqa: BLE001 — C3: no path returns neither outcome
            return self._degrade(
                item, OBLIGATION_READABLE,
                f"adapter raised {type(e).__name__}: {e}", run_id)

        # 4 — locator completeness. A locator missing a part its kind requires
        #     fails with that part NAMED, not merely shorter.
        missing = read.locator.missing_required_parts()
        if missing:
            return self._degrade(
                item, OBLIGATION_LOCATOR,
                f"locator kind {read.locator.kind!r} is missing required part(s) "
                f"{list(missing)}", run_id)

        # 5 — the version half of the pin.
        if not read.version:
            return self._degrade(
                item, OBLIGATION_VERSION_READ,
                "the source exposes no version to pin, so the read cannot be "
                "addressed later", run_id)
        if not read.source_id:
            return self._degrade(
                item, OBLIGATION_SOURCE_IDENTITY,
                "the source exposes no identity to pin", run_id)

        # 6 — the content shape (D3). The read succeeded and the pin's parts are
        #     all present, so everything up to here has established that the bytes
        #     are ADDRESSABLE. Nothing yet has asked whether they are the
        #     DOCUMENT. A synced-drive placeholder, a git-LFS pointer, a web
        #     shortcut and an empty file all decode cleanly, satisfy every check
        #     above, and would be pinned and cited as the source.
        #
        #     PATH-SHAPED kinds only, on the same gate the CLAUDE.md prohibition
        #     uses at `:834` — same gate, later step, because content does not
        #     exist until after the read. The gate is what keeps this from
        #     colliding with a kind-specific content check an adapter already
        #     owns (`adapters/linear.py` refuses an issue carrying no readable
        #     text, on this same obligation). A judgment true of EVERY path-shaped
        #     kind belongs here; one only a kind can make belongs to that kind.
        #     Consequence, stated because nothing else states it: a future
        #     NON-path-shaped kind inherits nothing from here and needs its own
        #     adapter-side check, exactly as Linear has.
        #
        #     `OBLIGATION_READABLE` — no new obligation, deliberately. To a person
        #     this is the same event as the PDF refusal they already get: a file
        #     you pointed me at could not be read, and here is which one.
        if rules_for(item.kind).path_shaped:
            not_document = not_the_document(read.content)
            if not_document:
                return self._degrade(item, OBLIGATION_READABLE, not_document,
                                     run_id)

        # 7 — the budgets, per kind (S6). For a kind that refuses whole, nothing
        #     is shortened, truncated or partially read in order to fit (C12) and
        #     the item fails with the bound named. For a kind that truncates, the
        #     shortening is RECORDED rather than silent — see `KindRules` for why
        #     the two kinds genuinely differ rather than one being let off.
        rules = rules_for(item.kind)
        item_ceiling = resolve_item_budget(item.kind, item_budget)
        content = read.content
        truncated = bool(read.truncated)
        size = len(content.encode("utf-8"))
        if size > item_ceiling:
            if not rules.truncate_over_budget:
                return self._degrade(
                    item, OBLIGATION_READ_BUDGET,
                    f"item is {size} bytes, over the per-item budget "
                    f"{_bound_label('MAX_ITEM_BYTES', rules.max_item_bytes, item_ceiling)}"
                    "; it is refused whole rather than truncated", run_id)
            content = content.encode("utf-8")[:item_ceiling].decode("utf-8", "ignore")
            truncated = True
            size = len(content.encode("utf-8"))
        if budget is not None and budget.would_exceed(size):
            return self._degrade(
                item, OBLIGATION_READ_BUDGET,
                f"item of {size} bytes would take the run past the aggregate "
                f"budget {_bound_label('MAX_RUN_BYTES', rules.max_run_bytes, budget.limit)} "
                f"({budget.spent} already admitted)", run_id)

        # 8 — the pin. A dirty tree is recorded IN the version, so a reader knows
        #     the commit is context rather than an exact address.
        version = f"{read.version}+dirty" if read.dirty else read.version
        pin = SourcePin(source_id=read.source_id, version=version,
                        locator=read.locator)
        try:
            pin_str = pin.render()
        except LocatorError as e:
            return self._degrade(item, OBLIGATION_LOCATOR, str(e), run_id)

        # 9 — the evidence path. The PORT selects, from the adapter's declared
        #     capability and the reported dirty flag. `original` where the source
        #     can be re-opened AT THE STATE THAT WAS READ; `captured` otherwise —
        #     which a dirty working tree is, because the pinned commit then does
        #     not describe the bytes that were read.
        if adapter.can_reopen_without_credentials and not read.dirty:
            entry = EvidenceEntry(
                run_id=run_id, pin_str=pin_str,
                evidence_path=EVIDENCE_ORIGINAL,
                reopen_instruction=self._reopen_instruction(item, read,
                                                            truncated=truncated),
                declaration_bound=declaration_bound,
                matched_selector=matched_selector)
        else:
            entry = EvidenceEntry(
                run_id=run_id, pin_str=pin_str,
                evidence_path=EVIDENCE_CAPTURED,
                excerpt=content,
                declaration_bound=declaration_bound,
                matched_selector=matched_selector)
        self.store.put_evidence(entry)
        if budget is not None:
            budget.charge(size)
        return AdmissionResult(item=item.item_id, pin=pin,
                               evidence_path=entry.evidence_path,
                               pin_str=pin_str,
                               content=content, truncated=truncated,
                               declaration_bound=declaration_bound,
                               matched_selector=matched_selector)

    # -- helpers ------------------------------------------------------------ #

    @staticmethod
    def _reopen_instruction(item: SourceItem, read: ReadResult, *,
                            truncated: bool = False) -> str:
        """How to reach what the claim rests on, dispatched on kind (S6).

        For a path-shaped kind this is an instruction a PLAIN FILE READ can follow
        (C14, C16) — deliberately not a `git show`, because a checker holding no
        credentials and no git needs a file reader and the pin, and on a clean tree
        the bytes on disk at this path ARE the bytes at the pinned commit.

        For web the instruction is to open the URL. That is still an `original`
        evidence path and not a weaker one: design-A29 resolves web to `original`
        because the existing re-open is what earns it. A truncated read stays
        `original` too — the page is re-opened at the same address and shortened
        the same way, so nothing about the re-open is weaker — but the instruction
        SAYS it was shortened, because a reader comparing a claim against a full
        page should know the excerpt behind it was not.

        S7 — THE THIRD `path_shaped` USE SITE, AND WHY IT NEEDED WORK.
        This branch was written when every path-shaped kind was `code`, and it
        assumed two things that are false of a plain file on disk:

        * It read a locator part named ``lines`` by hard-coded name. The two S7
          kinds declare ``line``, so the branch would have silently produced no
          location at all — `.get()` returns `""` and the instruction just omits
          the `where` clause. A *missing* location, not a crash. The part name is
          now taken from the kind's own declared grammar, so no kind can lose its
          location to a name mismatch again.
        * It said "the working tree was clean at <id>@<version>", which is a
          claim about a VCS revision. For an on-disk file the version is the
          instant it was READ, so that sentence would be simply untrue — and it
          lives in the admission record rather than the citation, so it breaks no
          marker and no drift guard would have caught it. Dispatch is now on
          ``version_is_revision``.
        """
        rules = rules_for(item.kind)
        if not rules.path_shaped:
            note = (" — the excerpt behind this claim was shortened to fit its "
                    "read budget, so the page holds more than was read"
                    if truncated else "")
            return (f"open {item.target} — read at {read.version}"
                    f"{note}")
        # The location part, by the kind's DECLARED grammar rather than by a
        # hard-coded name: `code` declares `lines`, the S7 kinds declare `line`.
        # `path` is the target itself and is already in the instruction.
        try:
            spec = get_kind(read.locator.kind)
            location_parts = [p for p in spec.required_parts if p != "path"]
        except LocatorError:
            location_parts = []
        where = ""
        for part in location_parts:
            value = read.locator.parts.get(part, "")
            if value:
                where = f" {part} {value}"
                break
        if rules.version_is_revision:
            return (f"read {item.target}{where} — the working tree was clean at "
                    f"{read.source_id}@{read.version}, so the file on disk holds the "
                    "bytes this claim rests on")
        return (f"read {item.target}{where} — the file on disk was read at "
                f"{read.version}; it holds the bytes this claim rests on unless it "
                "has been edited since")

    def _degrade(self, item: SourceItem, obligation: str, reason: str,
                 run_id: str) -> AdmissionResult:
        degradation = Degradation(item=item.item_id, obligation=obligation,
                                  reason=reason)
        self._record_degradation(degradation, run_id)
        return AdmissionResult(item=item.item_id, degradation=degradation)

    def _record_degradation(self, degradation: Degradation, run_id: str) -> None:
        """Persist immediately — at the moment the failure is reached (C11)."""
        self.store.put_degradation(DegradationRecord(
            run_id=run_id,
            item_id=degradation.item,
            obligation=degradation.obligation,
            reason=degradation.reason))

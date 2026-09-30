"""S14 — the closing implementation verification for `research-source-adapters`.

Thirteen slices shipped, each verifying its own mechanism against its own plan.
This module asks the question none of them asked: does the capability those
slices add up to actually work here, end to end — and it is built to be able to
come back NEGATIVE.

WHAT THIS MODULE IS FOR, AND THE JOIN IT CLOSES
-----------------------------------------------
The composition was already tested in two halves that were never joined. The
*read* half is composed by `test_code_admission.py:920` (picker → `r0_intake` →
cycle read-back → `read_declared_sources` → pin → marker → synthesis, nothing
stubbed). The *declaration-to-fact-check* half is composed by
`test_s12_downstream_trust.py:802`. Citation **resolution** is proved separately
again by `test_s13_dead_citations.py`. But the file sets are disjoint: no suite
that drives `read_declared_sources` touches `research_linkcheck` or
`_citation_resolve`, and none that touches those drives a declared read. So a
pinned marker was proved to be *produced*, and a citation was proved to be
*resolved*, and nothing proved the marker a real declared read produces is the
thing the resolver can re-open. That join is what `_walk` carries.

THREE RESPONSIBILITIES, THREE LOCI — DELIBERATELY NOT MERGED
-------------------------------------------------------------
* ``_walk(corpus)`` PRODUCES OBSERVATIONS. It drives production callables and
  returns structured observations. It asserts nothing about rendered text.
* ``run_matrix()`` JUDGES WHETHER AN OBSERVATION IS SOUND. It neuters one
  production callable per link and checks the resulting red-set against that
  link's declared mask.
* ``render_readout()`` PRESENTS A DISPOSITION. Only its own tests touch report
  text.

An earlier shape had the walk assert on rendered report text. That coupled the
walk to the renderer, so a renderer change could redden the walk and a walk
change could silently satisfy the renderer's tests. Kept apart on purpose.

WHY THE MATRIX IS A MATRIX AND NOT ONE ARM PER LINK
----------------------------------------------------
`test_sfinal_output_security_composition.py` established the revert form in this
codebase: neuter a named module attribute and assert the observation reddens.
This module deliberately EXCEEDS that. The one-arm form (neuter *j*, assert
observation *j* is red) cannot detect an observation that is watching TOO MUCH,
and that is the failure S13-obs8 actually produced here eight weeks ago: two pins
located their target with `str.index` on a substring matching a function
*definition* rather than the call site, and *the arm written to prove them sound
computed its injection point the same way*, so it could only ever confirm. Three
instances of that shape in one session.

So every observation must go RED under every neuter on its mask **and stay GREEN
under every neuter that is not** — silence as well as noise. That turns "the arm
went red" from a confirmation into a two-sided measurement.

The neuter target is always a NAMED MODULE ATTRIBUTE, never a text offset, so no
arm can share a derivation with an assertion.

HOW THE MASKS WERE DECLARED, AND WHY THAT ORDER MATTERS
--------------------------------------------------------
The masks in `MASK` were written down BEFORE the matrix was ever run, by asking
of each observation "which of these ten neuters would break the assertions this
observation actually makes?" — reading each neuter's definition, not its result.
Fitting a mask to a matrix's output would make the matrix a tautology, and the
temptation to do it arrives exactly when the matrix first comes back red.

Three masks are NARROWER than the slice's plan table suggested, and the reason is
recorded here rather than absorbed silently. The plan's link table lists which
observables each link "reads"; that is a statement about topic, not about
dependency. `L6` (stub `resolve_all` to report every citation `live`) is listed
against C4, but C4 asserts that a LIVE citation resolves live — which an
all-live stub satisfies. Putting L6 on C4's mask would have declared a red that
cannot happen and failed the matrix in the over-coupling direction. The same
reasoning removed L7 from C4 and L1 from C6. Derived from the neuters' meaning,
before running.

WHAT THIS MODULE DOES NOT DO
-----------------------------
It does not re-run the per-slice suites as its evidence — their greenness is
precisely what failed to answer the question. And it never opens a declared
source with `Read`, `Glob` or a shell: that reads BESIDE the port rather than
through it, which `Skills/research-en.md:146` names as an antipattern.

The fact-check checker panel is injected, as in every sibling fact-check suite in
this codebase. No model is called. What is exercised is the engine's own
orchestration — so an observation resting on L8/L9 demonstrates that the engine
ROUTES a verdict, never that a real checker would reach it.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional, Tuple

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
sys.path.insert(0, str(CONFIG_DIR / "skills"))
sys.path.insert(0, str(HOOKS_DIR))


def _load_by_path(name, path):
    """Load a hooks module BY PATH, registering it under `name`.

    ORDERING IS LOAD-BEARING HERE. `research_linkcheck` does
    `from _citation_resolve import resolve_all`, which copies the binding out of
    whatever `_citation_resolve` is in `sys.modules` at ITS import time. Loading
    `_citation_resolve` under a suite-unique alias would leave the link-checker
    bound to a DIFFERENT module object, and a neuter applied to our alias would
    quietly reach nothing — the shared-process aliasing trap
    `test_code_admission.py:78-82` records against the reachability registry.
    So `_citation_resolve` is loaded under its BARE name and loaded FIRST.
    """
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Bare name, and FIRST — see the docstring above.
cr = _load_by_path("_citation_resolve", HOOKS_DIR / "_citation_resolve.py")
rlc = _load_by_path("_s14_research_linkcheck", HOOKS_DIR / "research_linkcheck.py")
rp = _load_by_path("_s14_research_pipeline", HOOKS_DIR / "research_pipeline.py")
fce = _load_by_path("_s14_factcheck_engine", HOOKS_DIR / "_factcheck_engine.py")

from research import admission_record as arec        # noqa: E402
from research import declared_read as dr             # noqa: E402
from research import kind_reachability as kr         # noqa: E402
from research import scope_record as srec            # noqa: E402
from research import source_picker as spk            # noqa: E402
from research import source_port as sp               # noqa: E402


# =========================================================================== #
# A1 — the observable set, derived from the LOCKED fields
#
# An observable is a promise the operator was made. Its source is the topic's
# locked `## Desired Outcome` / `## Desired Solution` / `## Metrics`, NEVER a
# slice's own plan or suite — anchoring on what the slices shipped is the
# circularity this whole slice exists to break, and it is what would let a
# promise no slice delivered hide behind a slice's green suite.
#
# `anchor_text` is a verbatim fragment of the locked line. It is checked against
# the spine when the spine is reachable, so an observable cannot drift away from
# the promise it claims to read.
# =========================================================================== #

SPINE_NAME = "research-source-adapters-20260808213101_THOUGHT.md"

#: Locator classes C4/C6/M2 split across. `linear` is registered and driven in
#: production; it is unreachable HERE for want of credentials.
LOCATOR_CLASSES = ("local-file", "code", "linear")

#: The five registered source kinds C1 splits across.
SOURCE_KINDS = ("web", "knowledge_library", "code", "document_folder", "linear")


#: The two axes an observable can be split along. They are NOT interchangeable and
#: the distinction is load-bearing: `code` and `linear` are words in BOTH
#: vocabularies, so a limit derived by matching part NAMES reported `code` as an
#: unreadable SOURCE KIND — and produced the sentence "3 of the 5 kinds of source
#: were not read here" about a run that had just read a code repository. Derived
#: from the OBSERVABLE's declared axis instead, which cannot collide.
AXIS_SOURCE_KIND = "source_kind"
AXIS_LOCATOR_CLASS = "locator_class"
AXIS_EVIDENCE_PATH = "evidence_path"


@dataclass(frozen=True)
class Observable:
    """One promise, with the locked line it was read out of."""

    id: str
    promise: str
    anchor_line: int
    anchor_text: str
    #: Part names when the evidence covers only some of what the promise speaks
    #: for. Empty when the promise is answered whole.
    parts: Tuple[str, ...] = ()
    #: Which axis those parts are drawn from.
    axis: str = ""


OBSERVABLES: Tuple[Observable, ...] = (
    Observable(
        "C1",
        "One research run can draw on any combination of the declared source "
        "kinds.",
        42,
        "choose any combination of sources",
        parts=SOURCE_KINDS, axis=AXIS_SOURCE_KIND,
    ),
    Observable(
        "C2",
        "A person frames the research question before selecting the sources "
        "for it.",
        42,
        "They frame the question first, then choose",
    ),
    Observable(
        "C3",
        "A research run reads nothing beyond the sources the person selected.",
        42,
        "The research reads nothing beyond what they pointed it at",
    ),
    Observable(
        "C4",
        "Every claim names its source precisely enough for a reader to locate "
        "that source again later.",
        42,
        "names its source precisely enough to be found again later",
        parts=LOCATOR_CLASSES, axis=AXIS_LOCATOR_CLASS,
    ),
    Observable(
        "C5",
        "The report distinguishes a finding re-checked against its re-opened "
        "original from one compared only against a copy captured at reading "
        "time, and records which occurred.",
        44,
        "Where only a copy captured at the time of reading could be compared",
        parts=("re-opened original", "captured copy"), axis=AXIS_EVIDENCE_PATH,
    ),
    Observable(
        "C6",
        "A source that could not be checked is stated plainly as unchecked and "
        "does not appear in the report as verified.",
        44,
        "the report says so plainly instead of looking verified",
        parts=LOCATOR_CLASSES, axis=AXIS_LOCATOR_CLASS,
    ),
    # The two `## Metrics` population requirements. They are ENFORCEMENTS of C4
    # and C6 rather than new promises — the locked field adds no commitment, it
    # says the existing ones must reach the same denominators. Presented that way
    # in the read-out so nobody reads them as a claim set that was incomplete.
    Observable(
        "M1",
        "A research file drawing on internal sources produces a first-round "
        "marker exactly as a web-sourced one does, so it enters the same "
        "measured population. (Enforcement of C4.)",
        88,
        "must produce a first-round marker exactly as a web-sourced one does",
    ),
    Observable(
        "M2",
        "A dead internal citation counts as a broken link. (Enforcement of C6.)",
        88,
        "must count as a broken link",
        parts=LOCATOR_CLASSES, axis=AXIS_LOCATOR_CLASS,
    ),
)

OBSERVABLE_IDS: Tuple[str, ...] = tuple(o.id for o in OBSERVABLES)
BY_ID: Mapping[str, Observable] = {o.id: o for o in OBSERVABLES}


# =========================================================================== #
# What an observation is
# =========================================================================== #

#: A part that could not be exercised here at all. Distinct from False, which
#: means it WAS exercised and came back wrong. Collapsing the two is how a
#: capability comes to look better than it is.
UNREACHABLE = None


@dataclass
class Observation:
    """What the walk saw for one observable."""

    observable: str
    green: bool
    detail: str
    #: part -> (True | False | UNREACHABLE, why)
    parts: Dict[str, Tuple[Optional[bool], str]] = field(default_factory=dict)

    def unreachable_parts(self) -> Tuple[str, ...]:
        return tuple(p for p, (v, _) in self.parts.items() if v is UNREACHABLE)


# =========================================================================== #
# The constructed corpus
#
# A real git repository, a knowledge-library folder, a document folder, and a
# research body carrying a live citation, a misrooted one and a dead one.
#
# The constructed corpus is the ONLY way to get a deterministic dead-and-
# misrooted pair: the live corpus scan at S13 found exactly one genuinely dead
# citation across the 227 `_RESEARCH*` files it reached, so a real corpus cannot
# be relied on to contain one.
# =========================================================================== #


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@dataclass
class Corpus:
    root: Path
    repo: Path
    head: str
    library: Path
    documents: Path
    #: A path inside no declared selector — the containment probe.
    outside: Path
    #: An existing file a local-file citation can name.
    live_target: Path


def build_corpus(root: Path) -> Corpus:
    repo = (root / "payments-api").resolve()
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "T")
    (repo / "refunds.py").write_text("def issue_refund():\n    return 'ok'\n",
                                     encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    head = _git(repo, "rev-parse", "HEAD")

    library = (root / "library").resolve()
    library.mkdir()
    (library / "refund-policy.md").write_text(
        "# Refund policy\n\nRefunds are issued within 30 days.\n", encoding="utf-8")

    documents = (root / "documents").resolve()
    documents.mkdir()
    (documents / "runbook.md").write_text(
        "# Runbook\n\nEscalate a refund dispute to the payments team.\n",
        encoding="utf-8")

    outside = (root / "elsewhere").resolve()
    outside.mkdir()
    live_target = outside / "outside.md"
    live_target.write_text("not declared\n", encoding="utf-8")

    # The MISROOTED target: it exists beside the citing file but NOT under the
    # workspace root, so a relative citation to it resolves at a later candidate
    # root — which is exactly what `misrooted` means.
    thoughts = (root / "Thoughts").resolve()
    thoughts.mkdir(parents=True, exist_ok=True)
    (thoughts / "misrooted-note.md").write_text("beside the report\n",
                                                encoding="utf-8")

    return Corpus(root=root, repo=repo, head=head, library=library,
                  documents=documents, outside=outside, live_target=live_target)


def declarations_for(corpus: Corpus) -> Tuple[srec.ScopeRecord, srec.ScopeRecord]:
    """The records a person's selections assemble — TWO of them, and that is the point.

    Built through the PRODUCTION picker (`build_record`), never by hand: a
    hand-built record would skip the one link that turns a selection into an
    approval artifact, and it would also skip the route restriction that turns
    out to be C1's whole answer.

    **Why two and not one — SUPERSEDED 2026-09-11 (Q26). The reason expired; the
    two records are kept, and why is stated rather than assumed.**

    This read: "Production partitions the five registered kinds across routes: the
    external routes offer `code`, `web` and `linear`, and the internal-knowledge-
    base route offers `knowledge_library` and `document_folder`. `build_record`
    REFUSES to assemble a declaration for a class the calling route cannot read.
    So no single selection can span both groups, and the walk needs one record per
    group to exercise the kinds at all."

    Both clauses are now false. The external routes offer all FIVE classes
    (`source_picker` — the two S7 entries carry `routes=ROUTES`), and one selection
    CAN span both groups — asserted directly by
    `test_code_admission.py::test_one_declaration_spans_code_and_knowledge_library_end_to_end`.
    This docstring survived three review rounds because it lives in a file the
    change did not otherwise touch, and it is corrected in place rather than
    deleted because it is the clearest instance of this topic's recurring defect:
    a reason outliving the fact it rested on.

    **Two records are STILL correct here, for an unrelated reason that has not
    expired**: they exercise the two reader groups separately, which keeps a
    per-group failure attributable. `_observe_c1`'s cross-group question is asked
    by `combination_across_kinds_is_declarable` instead, which is the measurement
    that flipped — and whether the arrangement satisfies the locked promise is
    still what `_observe_c1` measures rather than assumes.
    """
    external = spk.build_record(
        spk.Selection(bounds=(
            ("code", spk.Bound(selectors=(str(corpus.repo),))),
            # A declared kind this run holds no authorization for, so C6 has a
            # REAL refusal to surface rather than a manufactured one.
            ("linear", spk.Bound(selectors=())),
        )),
        route=spk.ROUTE_NINJA)
    internal = spk.build_record(
        spk.Selection(bounds=(
            ("knowledge_library", spk.Bound(selectors=(str(corpus.library),))),
            ("document_folder", spk.Bound(selectors=(str(corpus.documents),))),
        )),
        route=spk.ROUTE_INTERNAL_KB)
    return external, internal


def combination_across_kinds_is_declarable(repo: Path,
                                           library: Path) -> Tuple[bool, str]:
    """Can ONE approved declaration carry a combination spanning the promised kinds?

    The locked promise is that a person chooses "any combination" of the five
    kinds in one run. This asks the production picker directly, on every route,
    rather than inferring the answer from the catalogue.

    Takes PATHS rather than a corpus so BOTH arms can ask it. When only the
    constructed arm asked, C1 answered a different question on each side — the
    fixture's C1 could fail on the cross-kind conjunct and the real arm's could
    not — and the comparison reported a disagreement that was an artifact of the
    two arms not being asked the same thing. That is precisely what
    `test_a2_both_arms_are_judged_by_the_SAME_observer` exists to forbid.
    """
    tried = []
    for route in spk.ROUTES:
        try:
            spk.build_record(
                spk.Selection(bounds=(
                    ("code", spk.Bound(selectors=(str(repo),))),
                    ("knowledge_library", spk.Bound(selectors=(str(library),))),
                )),
                route=route)
            return True, f"route {route!r} assembled a code + library declaration"
        except Exception as exc:                              # noqa: BLE001
            tried.append(f"{route}: {type(exc).__name__}")
    return False, ("no route assembles one declaration carrying both the code a "
                   "person has written and their knowledge library; "
                   + ", ".join(tried))


def research_body(corpus: Corpus) -> str:
    """A report body carrying a live, a MISROOTED, a dead and a code citation.

    The misrooted one is load-bearing and was missing at first. Without it, M2's
    negative half ("a misrooted citation is NOT counted broken") was asserted as
    `MISROOTED not in BROKEN_OUTCOMES` — two module constants compared to each
    other, invariant under every neuter and every input, and not a reading of the
    walk at all. `misrooted` is genuinely reachable
    (`_citation_resolve.py:341-345`): a RELATIVE path that resolves under the
    citing file's own directory but not under the workspace root. So it is
    constructed and resolved rather than asserted about.

    Every citation here is WELL-FORMED and inside the declaration. That matters:
    the close-time internal-citation axis folds on malformedness and on citing a
    source outside the approved list, NOT on a citation having gone dead —
    deadness is the link-checker's finding. A body mixing the two would have let
    one link's result stand in for the other's.
    """
    return (
        "# Refunds\n\n"
        f"Refunds are issued within 30 days. "
        f"[stated — local-file:{corpus.library / 'refund-policy.md'}:3]\n\n"
        "The runbook names an escalation path. "
        "[stated — local-file:no/such/file_RESEARCH.md:1]\n\n"
        "A note filed beside this report. "
        "[stated — local-file:misrooted-note.md:1]\n\n"
        "The refund routine returns ok. "
        f"[stated — code:payments-api@{corpus.head}:refunds.py:1-2]\n"
    )


def compliant_body(corpus: Corpus) -> str:
    """A report citing ONLY inside the approved declaration — the negative control.

    `research_body` cannot serve here and the difference is not cosmetic: it
    carries a dead citation, a misrooted one and a code pin, none of which the
    internal declaration names, so it is genuinely undeclared and the axis is
    right to fold it. Using it as the "compliant" arm asserted that a
    non-compliant report passes, which is the opposite of the intended control.
    """
    return ("# Refunds\n\nRefunds are issued within 30 days. "
            f"[stated — local-file:{corpus.library / 'refund-policy.md'}:3]\n")


def web_sourced_body() -> str:
    """A report citing only the public web — M1's COMPARISON arm.

    M1's promise is comparative: an internal-source file must produce a marker
    "exactly as a web-sourced one does". Without a web-sourced run there is
    nothing to compare to, and the internal arm alone can only show that a
    marker appeared — not that it appeared on the same terms.
    """
    return ("# Refunds\n\nRefunds are issued within 30 days. "
            "[stated — https://example.invalid/refund-policy]\n")


def undeclared_body(corpus: Corpus) -> str:
    """A report citing a source the approved declaration does not name.

    This is what the close-time axis is actually for, and it is the only body
    that can read L8 in the firing direction.
    """
    return ("# Refunds\n\nA claim from somewhere nobody approved. "
            f"[stated — local-file:{corpus.outside / 'outside.md'}:1]\n")


# =========================================================================== #
# A2 — the walk
#
# One run through the production callables, producing one observation per
# observable. Every link is real production code; none is re-implemented here.
# A link that cannot be driven yields an UNREACHABLE part or a `cannot be read`
# disposition — never a simulated pass.
# =========================================================================== #


def _checkout_map(corpus: Corpus) -> Callable[[str], Optional[str]]:
    """Repository identity -> local checkout, as a `code` pin's resolver needs.

    A `code` pin names an identity, not a path, so resolving one requires this
    mapping from the caller. Dropping it is exactly what L6b neuters.
    """
    return lambda repo: str(corpus.repo) if repo == corpus.repo.name else None


def _observe_c1(corpus, read_names, combinable) -> Observation:
    """Any combination of the declared source kinds — in ONE run.

    TWO things are measured, and the second is what the promise actually says.
    Per kind: did it produce a reading through the port at all. Across kinds:
    can one approved declaration carry a combination spanning them. A per-kind
    pass with no cross-kind check would report "any combination" as holding on
    the strength of each kind working separately, which is not the promise.
    """
    combinable_ok, combinable_why = combinable
    parts: Dict[str, Tuple[Optional[bool], str]] = {}

    parts["code"] = ("refunds.py" in read_names,
                     "declared repository produced a reading")
    parts["knowledge_library"] = ("refund-policy.md" in read_names,
                                  "declared library folder produced a reading")
    parts["document_folder"] = ("runbook.md" in read_names,
                                "declared document folder produced a reading")
    # Not omissions — structural facts about where each kind is read.
    parts["web"] = (UNREACHABLE, dr.DRIVEN_ELSEWHERE.get(srec.KIND_WEB, ""))
    parts["linear"] = (UNREACHABLE,
                       "reading a tracker needs a live authorization this run "
                       "does not hold, so it cannot be exercised here")
    # THE COMBINATION IS A PART, not a hidden conjunct. Held outside the parts it
    # was invisible to the summary rule, so a per-kind pass rendered the whole
    # promise as holding while the thing the promise actually says — ANY
    # COMBINATION, in one run — was failing. That is precisely the
    # "capability looks better than it is" path this read-out exists to close.
    parts["any combination in one run"] = (combinable_ok, combinable_why)

    exercised = [v for v, _ in parts.values() if v is not UNREACHABLE]
    return Observation(
        "C1",
        green=bool(exercised) and all(exercised),
        detail=(f"kinds that produced a reading: {sorted(read_names)}; "
                f"one declaration spanning kinds: {combinable_ok} "
                f"({combinable_why})"),
        parts=parts)


def _observe_c2() -> Observation:
    """Structural only — and the read-out must say so rather than dress it up.

    Nothing is RUN here. `open_picker` takes the framed question as a required
    positional, so a selection cannot be assembled before framing. That is a
    genuine reading and it is weaker than every other observable's: no neuter can
    redden a signature, so C2's mask is empty by construction rather than by
    oversight.
    """
    params = list(inspect.signature(spk.open_picker).parameters.values())
    first = params[0] if params else None
    green = bool(
        first is not None
        and first.name == "framed_question"
        and first.default is inspect.Parameter.empty
        and first.kind in (inspect.Parameter.POSITIONAL_ONLY,
                           inspect.Parameter.POSITIONAL_OR_KEYWORD))
    # AND THE HONEST QUALIFIER, which the first version of this reading omitted.
    # The signature above is real, but this walk never calls `open_picker`: it
    # assembles both declarations through `build_record`, whose signature carries
    # no framed question at all. So the executed path to an approved declaration
    # BYPASSES the function this reading was taken from, and the walk's own
    # fixture therefore assembles a declaration with no framing anywhere. Stating
    # "selection structurally cannot precede framing" unqualified would be
    # contradicted by the module's own behaviour.
    picker_is_on_the_executed_path = "open_picker" in _walk_source_calls()
    return Observation(
        "C2", green=green,
        detail=(f"open_picker's first parameter is {first!r}, so a selection "
                f"assembled THROUGH IT cannot precede framing; but this walk "
                f"reaches a declaration through build_record, which takes no "
                f"framed question — open_picker on the executed path: "
                f"{picker_is_on_the_executed_path}"))


def _walk_source_calls() -> str:
    """The walk function's own source, for stating what it does and does not call."""
    try:
        return inspect.getsource(walk)
    except (OSError, TypeError):
        return ""


def _observe_c3(corpus, record, read_result, disclosure_text) -> Observation:
    """Three assertions, all required.

    (a) an in-scope read happened at all — without this the rest is vacuously
        satisfied by a run that read nothing, which is precisely how a
        containment assertion passes against a broken reader;
    (b) a path inside no declared selector is REFUSED by the record's own check;
    (c) a run whose approved list went unenforced says so in the report body.
    """
    read_any = bool(read_result.readings)
    inside_roots = (corpus.repo.resolve(), corpus.library.resolve(),
                    corpus.documents.resolve())
    strayed = [r.item for r in read_result.readings
               if not any(str(Path(r.item).resolve()).startswith(str(root))
                          for root in inside_roots)]
    refusal = record.check(srec.KIND_DOCUMENT_FOLDER, corpus.live_target)
    refused_outside = not getattr(refusal, "admitted", False)
    # The section reached a REPORT BODY, and the body still carries what it had
    # before — a writer that replaced the file would satisfy a bare
    # "heading is present" check.
    # The section reached a REPORT BODY and the body kept what it had. What this
    # does NOT show — and the read-out must not imply — is that a real unenforced
    # run triggers it: the writer's only production caller is inside
    # `_write_research_frontmatter_for_terminal`, which this same walk replaces
    # with a no-op. So the walk calls the writer itself, with a reason it wrote.
    disclosed = (fce._SOURCE_DISCLOSURE_HEADING in (disclosure_text or "")
                 and "A claim." in (disclosure_text or ""))

    return Observation(
        "C3",
        green=read_any and not strayed and refused_outside and disclosed,
        detail=(f"read={read_any} strayed={strayed} "
                f"outside_refused={refused_outside} "
                f"unenforced_disclosure_written_into_a_body={disclosed} "
                f"(the writer was called directly; its only production trigger "
                f"is replaced by a stand-in in this same run)"))


def _observe_c4(corpus, read_result, resolutions, real_pin_resolutions) -> Observation:
    """A pin is re-openable — split by LOCATOR CLASS, not by source kind.

    The two bounds do not coincide, and a reader told only about source kinds
    would reasonably assume this one was clean.
    """
    code_reading = next((r for r in read_result.readings
                         if Path(r.item).name == "refunds.py"), None)
    expected = (f"[stated — code:{corpus.repo.name}@{corpus.head}:refunds.py:1-2]")
    marker_ok = bool(code_reading) and code_reading.marker == expected

    by_locator = {}
    for citation, res in resolutions:
        by_locator.setdefault(citation.locator, []).append(res)

    code_live = any(r.outcome == cr.LIVE for r in by_locator.get("code", []))

    # THE LOCAL-FILE ARM RESTS ON A PIN A REAL READ PRODUCED, not on a citation
    # the fixture authored. Resolving the fixture's own string would import the
    # join's credibility into a row the join does not cover: the library and
    # document-folder reads are the two kinds whose markers were previously
    # never checked at all, only their filenames.
    real_local = [(c, r) for c, r in real_pin_resolutions
                  if c.locator == "local-file"]
    local_live = bool(real_local) and all(r.outcome == cr.LIVE
                                          for _c, r in real_local)

    parts: Dict[str, Tuple[Optional[bool], str]] = {
        "local-file": (local_live,
                       "the pins the declared library and document-folder reads "
                       "produced re-open where they say they do"),
        "code": (marker_ok and code_live,
                 "a code pin renders the repository and the commit read, and "
                 "re-opens in a checkout of that repository"),
        "linear": (UNREACHABLE,
                   "a Linear pin needs a live authorization this run does not "
                   "hold, so its re-openability cannot be exercised here"),
    }
    exercised = [v for v, _ in parts.values() if v is not UNREACHABLE]
    real_markers = sorted(r.marker for r in read_result.readings)
    return Observation(
        "C4", green=bool(exercised) and all(exercised),
        detail=(f"code marker={code_reading.marker if code_reading else None!r} "
                f"expected={expected!r}; every reading's own pin re-opened: "
                f"{local_live and code_live}; markers produced: {real_markers}"),
        parts=parts)


def _observe_c5(read_result) -> Observation:
    """The two evidence paths must read DIFFERENTLY, and a real reading carries one.

    A single constant string next to both would be a disclosure that discloses
    nothing, so the assertion is on the DIFFERENCE and not merely on presence.
    """
    original = dr.AdmittedReading(
        item="x", content="c", marker="m",
        evidence_path=arec.EVIDENCE_ORIGINAL).evidence_sentence()
    captured = dr.AdmittedReading(
        item="x", content="c", marker="m",
        evidence_path=arec.EVIDENCE_CAPTURED).evidence_sentence()
    distinct = bool(original) and bool(captured) and original != captured

    # "Records WHICH occurred" is the operative half of the promise, so it is
    # checked rather than approximated by non-emptiness. For every real reading:
    # the recorded path is one of the two known values, and the sentence it
    # renders is the sentence THAT path defines — a reading stamped `original`
    # that rendered the `captured` wording would be a wrong disclosure, which is
    # worse than an absent one, and a bare "the sentence is non-empty" check
    # cannot tell the two apart.
    expected_for = {arec.EVIDENCE_ORIGINAL: original,
                    arec.EVIDENCE_CAPTURED: captured}
    recorded = [r.evidence_path for r in read_result.readings]
    known = bool(recorded) and all(p in expected_for for p in recorded)

    # `sentence == expected_for[path]` WAS ALSO ASSERTED HERE AND IS GONE.
    # `evidence_sentence` branches on `evidence_path` alone
    # (`declared_read.py:111-133`), and `expected_for`'s values are themselves
    # that function's output, so for any path `known` already admits the check
    # reduced to `f(p) == f(p)` — true for every input, and rendered in the
    # read-out as an independent finding. The mismatch it claimed to catch is
    # unconstructible: no reading can render the other path's wording.
    # SPLIT BY EVIDENCE PATH, because only one of the two occurs here. Every real
    # reading in this walk is `original`: the `captured` branch is taken only for
    # a credentialed remote, which is the kind this run cannot authorize. Reported
    # whole, C5 would have let a hand-built object stand in for the half that was
    # never exercised.
    seen = set(recorded)
    parts: Dict[str, Tuple[Optional[bool], str]] = {
        "re-opened original": (
            arec.EVIDENCE_ORIGINAL in seen and known,
            "a real reading records this path and renders its own sentence"),
        "captured copy": (
            UNREACHABLE,
            "no source read here takes this path — it is taken for a source "
            "behind a login, which this run cannot authorize, so only the "
            "wording was checked and not a reading that used it"),
    }
    exercised = [v for v, _ in parts.values() if v is not UNREACHABLE]
    return Observation(
        "C5", green=distinct and bool(exercised) and all(exercised),
        detail=(f"the two paths render different sentences={distinct}; "
                f"every real reading records a known path={known}; "
                f"paths_seen={sorted(seen)}"),
        parts=parts)


def _observe_c6(read_result, annotated, tally, fc_undeclared_status,
                fc_declared_status) -> Observation:
    """Unchecked is stated plainly — never passed off as verified.

    `fc_undeclared_status` is the verdict on a report citing a source OUTSIDE the
    approved declaration. An earlier version passed the verdict on the ordinary
    body instead and read its `PASS` as a failure — but the close-time axis folds
    on malformedness and on citing outside the list, never on a citation having
    gone dead, so that `PASS` was correct behaviour and the assertion was simply
    aimed at the wrong link. Deadness is read through the link-checker below.
    """
    refusals = " ".join(read_result.refusal_lines)
    # A declared kind this run cannot authorize comes back BY NAME. This is part
    # of `green`, not merely of the detail: a refusal computed and then ignored
    # would leave L4 a link no observation watches, which is precisely the
    # vacuity the matrix exists to catch — and it would have been invisible,
    # because the MASK would still have named L4.
    named_refusal = bool(refusals) and "linear" in refusals.lower()
    downgraded = "[unverified — source not found —" in (annotated or "")
    # Named SPECIFICALLY, never as "not PASS": a `NOOP` is a run that did not
    # happen, and reading it as a fold is how a check that could not run reports
    # as a check that ran and found something.
    folded = fc_undeclared_status == "INCOMPLETE"
    # THE OTHER DIRECTION, which was computed and then thrown away. A gate shown
    # only to fire cannot be told from a gate that fires on everything, and a
    # report citing only inside the approved list must pass. The walk was
    # already running this arm; its verdict simply never reached an observer.
    did_not_fire_on_a_compliant_report = fc_declared_status == "PASS"

    parts: Dict[str, Tuple[Optional[bool], str]] = {
        "local-file": (downgraded,
                       "a dead local-file citation is downgraded in place to an "
                       "unverified marker rather than left looking verified"),
        "code": (UNREACHABLE,
                 "a code citation resolves only to live or unresolved and never "
                 "to dead, so 'stated plainly as unchecked' cannot be "
                 "demonstrated for this locator class"),
        "linear": (UNREACHABLE,
                   "a Linear citation resolves unresolved for want of "
                   "credentials, so this locator class cannot be exercised here"),
    }
    exercised = [v for v, _ in parts.values() if v is not UNREACHABLE]
    return Observation(
        "C6",
        green=(bool(exercised) and all(exercised) and named_refusal
               and folded and did_not_fire_on_a_compliant_report
               and tally is not None),
        detail=(f"refusal_named={named_refusal} downgraded={downgraded} "
                f"fact_check_folded_on_undeclared={folded} "
                f"passed_a_compliant_report={did_not_fire_on_a_compliant_report} "
                f"tally={tally}"),
        parts=parts)


def _observe_m1(marker_text, fc_undeclared_status, web_marker) -> Observation:
    """Population: the file produces a first-round marker, graded on the same axes.

    "Exactly as a web-sourced one does" is read as BOTH halves: a marker exists
    (so the file enters the denominator) AND its verdict reflects the internal-
    citation axis (so it is graded rather than merely counted). The second half
    is what any neuter can reach; a marker's mere existence is gated by nothing
    in the link set, so a marker-only reading would have an empty mask.
    """
    has_marker = bool(marker_text)
    graded = fc_undeclared_status == "INCOMPLETE"

    # THE COMPARISON IS A COMPARISON, not two non-emptiness tests conjoined.
    # `bool(a) and bool(b)` is satisfied by two markers with different verdicts,
    # kinds and schemas — which is not "exactly as a web-sourced one does". The
    # markers' own frontmatter fields are compared instead: same schema version,
    # same kind, same verdict, same round. Those are the fields that decide
    # whether a file enters the measured population on the same terms.
    def _fields(text):
        wanted = ("schema_version", "kind", "verdict", "rounds")
        out = {}
        for line in (text or "").splitlines():
            for key in wanted:
                if line.startswith(key + ":"):
                    out[key] = line.split(":", 1)[1].strip()
        return out

    internal_fields, web_fields = _fields(marker_text), _fields(web_marker)
    same_terms = bool(internal_fields) and internal_fields == web_fields

    return Observation(
        "M1", green=has_marker and graded and same_terms,
        detail=(f"first_round_marker={has_marker} "
                f"internal_marker_fields={internal_fields} "
                f"web_marker_fields={web_fields} "
                f"same_terms={same_terms}; "
                # NAMED as a different file's verdict, because it is one — an
                # undeclared-citation run, not the internal-source file whose
                # marker the first conjunct came from. Rendering both as one
                # file's line was the conflation this note exists to stop.
                f"a SEPARATE undeclared-citation run graded "
                f"{fc_undeclared_status!r} (axis_reflected={graded})"))


def _observe_m2(tally, resolutions) -> Observation:
    """Dead counts broken; misrooted does not. Split by locator class."""
    ran = tally is not None
    dead = (tally or {}).get(cr.DEAD, 0)
    misrooted = (tally or {}).get(cr.MISROOTED, 0)
    code_outcomes = {res.outcome for c, res in resolutions if c.locator == "code"}

    # BOTH halves read from the OBSERVED tally. The negative half previously read
    # `cr.MISROOTED not in cr.BROKEN_OUTCOMES` — two module constants compared to
    # each other, true under every neuter and every corpus, and no reading of
    # this walk at all. Now the corpus contains a real misrooted citation, so the
    # assertion is that one was resolved AND that the broken count excludes it.
    broken = sum((tally or {}).get(o, 0) for o in cr.BROKEN_OUTCOMES)
    parts: Dict[str, Tuple[Optional[bool], str]] = {
        "local-file": (ran and dead >= 1 and misrooted >= 1 and broken == dead,
                       "a dead local-file citation is counted broken and a "
                       "misrooted one — a source that EXISTS, under another "
                       "root — is resolved and left out of the broken count"),
        "code": (UNREACHABLE,
                 "a code citation never resolves dead, so it can never enter "
                 "the broken count; observed outcomes here: "
                 f"{sorted(code_outcomes)}"),
        "linear": (UNREACHABLE,
                   "a Linear citation resolves unresolved for want of "
                   "credentials, so it can never enter the broken count here"),
    }
    exercised = [v for v, _ in parts.values() if v is not UNREACHABLE]
    return Observation(
        "M2", green=bool(exercised) and all(exercised),
        detail=f"tally={tally}", parts=parts)


def _run_fact_check(tmp_root: Path, corpus: Corpus, record, body: str,
                    sid: str, slug: str = "s14") -> Tuple[str, str]:
    """Drive the engine over a real cycle. Returns (status, first-round marker).

    The checker panel is injected and ONLY the checker panel — what is under
    test is the engine's own close-time orchestration, not a model's verdict.

    `slug` names a DISTINCT draft per arm. Two arms sharing one draft path made
    the second return `NOOP` — the engine had already dispatched for that file —
    and `NOOP` is not `PASS`, so a "did it fold?" test written as `!= PASS` read
    a run that never happened as a run that folded. That is the same conflation
    the link-checker's own `tally is None` contract warns about, reached from a
    different direction.
    """
    from unittest import mock

    draft = tmp_root / "Thoughts" / f"{slug}_RESEARCH.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(body, encoding="utf-8")

    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": str(draft),
                    "caller_skill": "/research",
                    "user_approved_scope": True,
                    "scope_record": json.loads(record.to_json())},
                   cycle_id="default")

    # A state dir PER ARM. Sharing one across the baseline and the ten matrix
    # walks let a leftover marker from an earlier arm satisfy a later arm's
    # "a marker exists" conjunct, so that conjunct's stay-green half would have
    # been guaranteed by state leakage rather than by behaviour.
    state = tmp_root / "fcstate" / slug
    state.mkdir(parents=True, exist_ok=True)
    with mock.patch.object(fce, "_run_coverage_axis_gate",
                           side_effect=lambda rp_, cs, v, td, kind, **kw: v), \
            mock.patch.object(fce, "_write_research_frontmatter_for_terminal"), \
            mock.patch.object(fce, "_log_factcheck_run"):
        result = fce.factcheck_run(
            str(state), str(draft), "research", sid,
            debounce_seconds=0, models=["sonnet"],
            _checker_fn=lambda *a, **k: "reasoning\nVERDICT: PASS",
            proj="p", topic="t")

    # The engine writes a KEYED marker (`<key>_R<n>.md`) as well as the flat
    # `R<n>.md` form, and which one appears depends on the slot. A glob for the
    # flat name alone reported "no marker" against an engine that had written
    # one — a FALSE NEGATIVE in this walk, not a finding about production, and
    # the reason the pattern below matches both shapes.
    markers = sorted(p for p in state.rglob("*.md")
                     if re.fullmatch(r"(.*_)?R\d+\.md", p.name))
    marker_text = markers[0].read_text(encoding="utf-8") if markers else ""
    return result.get("status", ""), marker_text


def walk(tmp_root: Path, corpus: Corpus, sid: str = "s14") -> Dict[str, Observation]:
    """One run of the whole capability, producing one observation per observable.

    Every link below is a production callable. The order is the capability's own
    order: declare, approve, persist, read back, read, pin, resolve, count, grade.
    """
    external, internal = declarations_for(corpus)

    # L1/L2 — the declared read, port-mediated and containment-checked. TWO
    # reads to keep a per-group failure attributable — NOT because production
    # refuses a spanning declaration, which it stopped doing at Q26 (2026-09-11).
    # This comment gave that refusal as its reason until then; see
    # `declarations_for` for the full correction.
    external_read = dr.read_declared_sources(
        external, projects_root=tmp_root,
        store=arec.InMemoryAdmissionRecordStore(),
        query="how are refunds issued?")
    internal_read = dr.read_declared_sources(
        internal, projects_root=tmp_root,
        store=arec.InMemoryAdmissionRecordStore(),
        query="how are refunds issued?")

    read_result = dr.DeclaredReadResult(
        readings=tuple(external_read.readings) + tuple(internal_read.readings),
        refusals=tuple(external_read.refusals) + tuple(internal_read.refusals))
    read_names = {Path(r.item).name for r in read_result.readings}

    body = research_body(corpus)

    # L6/L6b — resolution of the pins a real read and a real report produce.
    citations = fce.parse_citations(body)
    resolutions = cr.resolve_all(
        citations, exists=os.path.exists, workspace_root=str(tmp_root),
        citing_file=str(tmp_root / "Thoughts" / "s14_RESEARCH.md"),
        checkout_for=_checkout_map(corpus))

    # THE JOIN, for the two on-disk kinds as well as for code: take the markers
    # the REAL reads produced, put them in a body, and resolve THOSE. Without
    # this the local-file arm resolved a citation the fixture had written, and
    # the library and document-folder reads' own pins were never checked.
    real_pin_body = "# Findings\n\n" + "\n\n".join(
        f"A finding. {r.marker}" for r in read_result.readings if r.marker)
    real_pin_resolutions = cr.resolve_all(
        fce.parse_citations(real_pin_body), exists=os.path.exists,
        workspace_root=str(tmp_root),
        citing_file=str(tmp_root / "Thoughts" / "s14_RESEARCH.md"),
        checkout_for=_checkout_map(corpus))

    # L7 — broken-link accounting and in-place downgrade.
    annotated, tally, _details = rlc.apply_internal_citations(
        body, str(tmp_root / "Thoughts" / "s14_RESEARCH.md"),
        exists=os.path.exists, workspace_root=str(tmp_root),
        checkout_for=_checkout_map(corpus))

    # L8 — the close-time internal-citation axis, over TWO real cycles. One body
    # cites only inside the approved declaration (so the axis should NOT fold and
    # a first-round marker is still produced); the other cites outside it (so the
    # axis SHOULD fold). One arm alone cannot tell "the axis fires" from "the
    # axis fires on everything".
    fc_status, marker_text = _run_fact_check(
        tmp_root, corpus, internal, compliant_body(corpus), sid,
        slug=f"{sid}-declared")
    fc_undeclared_status, _ = _run_fact_check(
        tmp_root, corpus, internal, undeclared_body(corpus),
        sid + "-undeclared", slug=f"{sid}-undeclared")
    # M1's comparison arm — a web-sourced file through the same engine.
    _web_status, web_marker = _run_fact_check(
        tmp_root, corpus, internal, web_sourced_body(),
        sid + "-web", slug=f"{sid}-web")

    # L9 — the unenforced-declaration disclosure, driven through the WRITER that
    # puts the section into a report body, not through the pure formatter.
    #
    # The formatter interpolates the heading unconditionally
    # (`_factcheck_engine.py:5459`), so `HEADING in _render(...)` was
    # `CONST in f(...CONST...)` — a conjunct that could not be false unless the
    # constant were deleted, reported as "the report says so". The writer is the
    # link that actually reaches a body, so it is what the walk drives; it calls
    # the formatter, so L9's neuter still reaches it.
    disclosure_file = tmp_root / "disclosure" / f"{sid}_RESEARCH.md"
    disclosure_file.parent.mkdir(parents=True, exist_ok=True)
    disclosure_file.write_text("# Report\n\nA claim.\n", encoding="utf-8")
    fce._write_source_disclosure_section(
        str(disclosure_file),
        "the approved source list could not be enforced on this run")
    disclosure_text = disclosure_file.read_text(encoding="utf-8")

    return {
        "C1": _observe_c1(corpus, read_names,
                          combination_across_kinds_is_declarable(
                              corpus.repo, corpus.library)),
        "C2": _observe_c2(),
        "C3": _observe_c3(corpus, internal, read_result, disclosure_text),
        "C4": _observe_c4(corpus, read_result, resolutions, real_pin_resolutions),
        "C5": _observe_c5(read_result),
        "C6": _observe_c6(read_result, annotated, tally, fc_undeclared_status,
                          fc_status),
        "M1": _observe_m1(marker_text, fc_undeclared_status, web_marker),
        "M2": _observe_m2(tally, resolutions),
    }


# =========================================================================== #
# A3 — the links and the independence matrix
#
# Ten links. These ten, and only these, are what "exhaustive over links"
# quantifies over. Two further seams the CLI arm reaches
# (`_resolve_scope_from_cycle`, `durable_store_for`) are exercised but NOT
# matrixed, and that is a choice: they are reached THROUGH L1 rather than beside
# it, so neutering them would redden L1 and tell us nothing L1's own arm does not.
# =========================================================================== #


@dataclass(frozen=True)
class Link:
    id: str
    what: str
    #: Applies the neuter through the given MonkeyPatch context.
    neuter: Callable[[pytest.MonkeyPatch, "Corpus"], None]


def _neuter_l1(mp, corpus):
    """Stub the port's admission to refuse everything.

    Returns a real `AdmissionResult` carrying a degradation — the port's own
    contract refuses a result that is neither a pin nor a degradation, and a
    neuter that violated the contract would fail for the wrong reason.
    """
    mp.setattr(sp.AdmissionPort, "admit",
               lambda self, item, *a, **k: sp.AdmissionResult(
                   item=str(getattr(item, "path", "neutered")),
                   degradation=sp.Degradation(
                       item=str(getattr(item, "path", "neutered")),
                       obligation="L1",
                       reason="admission neutered by the S14 independence matrix")))


def _neuter_l2(mp, corpus):
    """Stub the declared-scope check to admit everything."""
    mp.setattr(srec.ScopeRecord, "check",
               lambda self, kind, target: srec.Admission(
                   kind=kind, resolved_target=str(target),
                   matched_selector=None,
                   scope_mode=srec.SCOPE_MODE_UNSCOPED))


def _neuter_l3(mp, corpus):
    """Stub the pin's marker to render nothing."""
    mp.setattr(sp.SourcePin, "marker", lambda self, kind="stated": "")


def _neuter_l4(mp, corpus):
    """Stub refusal surfacing to name nothing."""
    mp.setattr(dr.DeclaredReadResult, "refusal_lines", property(lambda self: ()))


def _neuter_l5(mp, corpus):
    """Stub the evidence sentence to say nothing."""
    mp.setattr(dr.AdmittedReading, "evidence_sentence", lambda self: "")


def _neuter_l6(mp, corpus):
    """Stub citation resolution to report every citation live.

    Patched at BOTH binding sites — `_citation_resolve.resolve_all` (which the
    walk calls directly) and `research_linkcheck.resolve_all` (the copy
    `from … import …` bound into the link-checker). One behaviour, two names; a
    patch at one site alone would leave half the capability un-neutered.
    """
    def _all_live(citations, **kwargs):
        return [(c, cr.Resolution(cr.LIVE, "neutered by the S14 matrix",
                                  c.locator)) for c in citations or []
                if getattr(c, "is_internal", False)]
    mp.setattr(cr, "resolve_all", _all_live)
    mp.setattr(rlc, "resolve_all", _all_live)


def _neuter_l6b(mp, corpus):
    """Drop the identity->checkout mapping a `code` pin needs to re-open.

    Behavioural rather than wholesale: the real function is called with the
    mapping removed, which is the specific capability being taken away.
    """
    real = cr._resolve_code
    mp.setattr(cr, "_resolve_code",
               lambda citation, exists, checkout_for: real(citation, exists, None))


def _neuter_l7(mp, corpus):
    """Stub broken-link accounting to change nothing and tally nothing."""
    mp.setattr(rlc, "apply_internal_citations",
               lambda text, citing_file, **k: (text, None, []))


def _neuter_l8(mp, corpus):
    """Stub the close-time internal-citation axis to pass the verdict through."""
    mp.setattr(fce, "_run_internal_citation_gate",
               lambda kind, citations, cited_urls, declaration, verdict,
               topic_dir, unreachable_reason=None: (verdict, None))


def _neuter_l9(mp, corpus):
    """Stub the unenforced-declaration disclosure to drop the section."""
    mp.setattr(fce, "_render_source_disclosure_section",
               lambda reason, unchecked_reads=None: "")


LINKS: Tuple[Link, ...] = (
    Link("L1", "the declared read, through the admission port", _neuter_l1),
    Link("L2", "containment — the declared-scope check", _neuter_l2),
    Link("L3", "the citation pin", _neuter_l3),
    Link("L4", "refusal surfacing", _neuter_l4),
    Link("L5", "the evidence path", _neuter_l5),
    Link("L6", "citation resolution", _neuter_l6),
    Link("L6b", "code re-openability", _neuter_l6b),
    Link("L7", "broken-link accounting", _neuter_l7),
    Link("L8", "the close-time internal-citation axis", _neuter_l8),
    Link("L9", "the unenforced-declaration disclosure", _neuter_l9),
)

LINK_IDS: Tuple[str, ...] = tuple(link.id for link in LINKS)

#: DECLARED BEFORE THE MATRIX RAN. See the module docstring — fitting these to
#: the matrix's output would make the matrix a tautology.
#:
#: C2's mask is EMPTY and that is structural, not an omission: its reading is a
#: function signature, and no neuter can redden a signature. The read-out says so
#: rather than rendering C2 like the others.
#:
#: **ONE MASK WAS CORRECTED AFTER THE FIRST MATRIX RUN, AND THE DISTINCTION
#: MATTERS.** C4's mask first omitted `L1`. The matrix reported it as an
#: over-coupling, and the response was NOT to paste the observed set in — that is
#: the fitting this module's whole method forbids. It was to re-read
#: `_observe_c4` and ask what that function's assertions actually consume:
#: `marker_ok` reads `read_result.readings`, which only `read_declared_sources`
#: produces. So C4 depends on L1 structurally, by the code rather than by the
#: result, and the first derivation was simply wrong — it asked which neuter
#: breaks *resolution* and forgot that C4 also reads a marker a real declared
#: read produced. Which is, in fact, the join this module exists to make. The
#: correction is recorded rather than absorbed, because a mask quietly edited to
#: match a matrix is indistinguishable from a mask that was right all along.
MASK: Mapping[str, frozenset] = {
    "C1": frozenset({"L1"}),
    "C2": frozenset(),
    "C3": frozenset({"L1", "L2", "L9"}),
    "C4": frozenset({"L1", "L3", "L6b"}),
    "C5": frozenset({"L1", "L5"}),
    "C6": frozenset({"L2", "L4", "L6", "L7", "L8"}),
    "M1": frozenset({"L2", "L8"}),
    "M2": frozenset({"L6", "L7"}),
}

#: **THE MATRIX CORRECTED THIS TABLE TWICE, AND SAYING SO IS PART OF THE RESULT.**
#: The first correction added `L1` to C4 (above). The second added `L2` to C6 and
#: M1: the close-time gate decides whether a citation names a declared source by
#: calling `declaration.check(...)` (`_factcheck_engine.py:3765`), so neutering
#: containment defeats the undeclared-citation fold. Both were verified BY
#: READING THE PRODUCTION CODE before the mask was touched — the matrix said
#: "look here", never "write this down".
#:
#: The honest reading of two corrections is not that the masks are now certainly
#: right. It is that a mask declared up front by one person is a fallible
#: artifact, which is exactly why the matrix exists and why it is two-sided. A
#: one-arm-per-link revert form would have caught NEITHER of these: both were
#: over-couplings — observations reddening under a link they had not declared —
#: and the one-arm form only ever asks whether the declared arm fires.


#: A manifest cycle is single-use — `r0_intake` refuses a session id it has
#: already completed. Every walk therefore needs a session id no earlier walk
#: used, including across repeated calls in one pytest process.
_SID_COUNTER = [0]


def _next_sid(prefix: str) -> str:
    _SID_COUNTER[0] += 1
    return f"{prefix}-{_SID_COUNTER[0]}"


def run_matrix(tmp_root: Path, corpus: Corpus,
               baseline: Mapping[str, Observation]) -> Dict[str, frozenset]:
    """Neuter each link in turn; return link -> the set of observations that went red.

    Only observations GREEN at baseline can go red, so a baseline failure is
    reported by the read-out rather than being laundered into a matrix result.
    """
    observed: Dict[str, frozenset] = {}
    for link in LINKS:
        with pytest.MonkeyPatch.context() as mp:
            link.neuter(mp, corpus)
            under = walk(tmp_root, corpus, sid=_next_sid("s14-matrix"))
        observed[link.id] = frozenset(
            oid for oid in OBSERVABLE_IDS
            if baseline[oid].green and not under[oid].green)
    return observed


def expected_red_set(link_id: str, mask: Mapping[str, frozenset] = None) -> frozenset:
    """The inverse of MASK: which observations SHOULD redden when `link_id` dies."""
    table = MASK if mask is None else mask
    return frozenset(oid for oid, links in table.items() if link_id in links)


def matrix_problems(baseline, observed, mask=None, link_ids=None):
    """Compare observed red-sets to declared masks. Returns one line per problem.

    THE COMPARISON LIVES HERE so the two control tests below can drive the REAL
    gate with synthetic inputs. Asserting set arithmetic on literals would have
    demonstrated the idea of the gate while leaving the gate itself untested —
    which is the shape of defect this module exists to catch, committed in the
    controls written to prove the module sound.
    """
    table = MASK if mask is None else mask
    ids = LINK_IDS if link_ids is None else link_ids
    problems = []
    for link_id in ids:
        expected = frozenset(o for o in expected_red_set(link_id, table)
                             if baseline[o].green)
        got = observed[link_id]
        if got != expected:
            problems.append(
                f"{link_id}: expected red {sorted(expected)}, observed "
                f"{sorted(got)} — over-coupled: {sorted(got - expected)}, "
                f"vacuous: {sorted(expected - got)}")
    return problems


# =========================================================================== #
# A4 — the three-valued read-out
#
# `holds` / `fails` / `cannot be read`, each owing its own accompaniment. A
# two-valued surface would force the choice between omitting what cannot pass and
# failing wholesale, and neither is a report.
# =========================================================================== #

HOLDS = "holds"
FAILS = "fails"
UNREADABLE_DISPOSITION = "cannot be read"
DISPOSITIONS = (HOLDS, FAILS, UNREADABLE_DISPOSITION)

WALK_MODULE = "test_s14_capability_walk.py"


def _module_source() -> str:
    try:
        return Path(__file__).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def citation_resolves(citation: str) -> bool:
    """Does every test named in this citation actually exist in this module?

    This is what stops a citation being decoration — the specific failure that
    let eight slices of green per-mechanism suites support a `holds` for every
    promise while the composition went untested.
    """
    names = [tok.strip(" ,`") for tok in str(citation or "").split()
             if tok.strip(" ,`").startswith("test_")]
    if not names:
        return False
    source = _module_source()
    return bool(source) and all(f"def {name}(" in source for name in names)


@dataclass
class Disposition:
    """One observable's answer, with the accompaniment that answer owes."""

    observable: str
    disposition: str
    obligation: str
    citation: str = ""
    #: part -> (disposition, why)
    parts: Dict[str, Tuple[str, str]] = field(default_factory=dict)

    def withheld(self) -> bool:
        """A `holds` whose citation does not resolve is not a `holds`."""
        return self.disposition == HOLDS and not citation_resolves(self.citation)

    def rendered_disposition(self) -> str:
        return UNREADABLE_DISPOSITION if self.withheld() else self.disposition

    def rendered_obligation(self) -> str:
        if self.withheld():
            return ("withheld — this reads as holding, but its citation does not "
                    "resolve to a test in the composed walk, and an unbacked "
                    "pass is what this verification exists to refuse")
        return self.obligation

    def undemonstrated_parts(self) -> Tuple[str, ...]:
        return tuple(name for name, (d, _) in self.parts.items()
                     if d != HOLDS)


def summarise(part_dispositions: Mapping[str, str]) -> str:
    """The observable's own line, from its parts. DEFINED, never a vote.

    Any part fails -> the line fails. No part holds -> cannot be read.
    Otherwise -> holds, and the accompaniment must name every part not
    demonstrated. An undefined rule is where averaging creeps back in.
    """
    values = list(part_dispositions.values())
    if any(v == FAILS for v in values):
        return FAILS
    if not any(v == HOLDS for v in values):
        return UNREADABLE_DISPOSITION
    return HOLDS


def disposition_for(observation: Observation, sound: bool,
                    citation: str) -> Disposition:
    """Turn one observation plus its soundness into one answer.

    `sound` is the matrix's verdict for this observation — whether it reddens
    under exactly the links it declares and stays green under the rest.
    """
    obs = BY_ID[observation.observable]

    parts: Dict[str, Tuple[str, str]] = {}
    for name, (value, why) in observation.parts.items():
        if value is UNREACHABLE:
            parts[name] = (UNREADABLE_DISPOSITION, why)
        elif value:
            parts[name] = (HOLDS, why)
        else:
            parts[name] = (FAILS, why)

    if parts:
        line = summarise({k: v for k, (v, _) in parts.items()})
    else:
        line = HOLDS if observation.green else FAILS

    if line == HOLDS and not sound:
        # THE CITATION IS CARRIED, not dropped. This branch hardcoded
        # `citation=""`, so C2 rendered with an em-dash in the "Test to open"
        # column and a reader had nothing to follow. A `cannot be read` owes its
        # reason just as a `holds` owes its evidence, and the test establishing
        # WHY it cannot be read is exactly what a reader wants next. The
        # withholding rule is about a `holds` resting on an unresolvable
        # citation; it was never a reason to strip a resolvable one.
        return Disposition(
            obs.id, UNREADABLE_DISPOSITION,
            ("the walk reports this holding, but the independence matrix does "
             "not show the observation coupled to the behaviour it names, so a "
             "pass here would rest on an unverified reading"),
            citation=citation, parts=parts)

    if line == HOLDS:
        undemonstrated = tuple(n for n, (d, _) in parts.items() if d != HOLDS)
        obligation = f"demonstrated by the composed walk: {observation.detail}"
        if undemonstrated:
            obligation += (". NOT demonstrated here: "
                           + ", ".join(sorted(undemonstrated)))
        return Disposition(obs.id, HOLDS, obligation, citation=citation,
                           parts=parts)

    if line == FAILS:
        return Disposition(
            obs.id, FAILS,
            f"observed: {observation.detail}", citation=citation, parts=parts)

    return Disposition(
        obs.id, UNREADABLE_DISPOSITION,
        ("no part of this promise could be exercised here; what would have to "
         "change is named against each part below"),
        citation=citation, parts=parts)


def render_readout(dispositions, limits, counts_source, comparisons=None) -> str:
    """The operator-facing record. Every figure is DERIVED at render time.

    Storing a count is how five separate count-or-cross-reference defects got
    into this slice's own plan, at least one of them introduced by the edit that
    fixed an earlier one. So nothing here is stored and re-printed.
    """
    # THE HEADLINE SAYS EXACTLY WHAT WAS DONE. It previously read "…AND on that
    # test being shown to break when the behaviour it watches is taken away",
    # which describes a procedure nothing performed: the matrix neuters a link
    # and re-runs the WALK, measuring the observation — the cited test is never
    # re-run under a neuter. The observation is what was shown to break; the test
    # is what a reader can go and read. Saying so is the difference between a
    # true sentence and a flattering one.
    lines = ["# S14 — what the research-source capability does and does not do here",
             "",
             "Each promise below carries exactly one of three answers. A `holds` "
             "on a PROMISE line means two things: the observation behind it was "
             "shown to break when the behaviour it watches was taken away, and "
             "to stay intact when a different behaviour was taken away; and it "
             "names a test in this module a reader can open. A `holds` whose "
             "named test does not exist is withheld rather than stated.",
             "",
             "**The indented part rows are not that.** They report what was "
             "observed for one part of a promise, and they carry NO independent "
             "backing of the kind above — the break-and-stay-intact measurement "
             "is made per promise, not per part. A part row reading `holds` "
             "means the walk saw it; it does not mean anything was taken away to "
             "check that the walk was watching it. This matters most where a "
             "promise line reads `fails` and its parts read `holds`.",
             ""]

    tally = {d: 0 for d in DISPOSITIONS}
    for disp in dispositions:
        tally[disp.rendered_disposition()] += 1

    lines.append("| Promise | Answer | Accompaniment | Test to open |")
    lines.append("|---|---|---|---|")
    for disp in dispositions:
        obs = BY_ID[disp.observable]
        # The citation is RENDERED. Withheld-if-unresolvable is worth little if a
        # reader is never shown the citation and so can never check it.
        cited = f"`{disp.citation}`" if disp.citation else "—"
        lines.append(f"| **{obs.id}** — {obs.promise} | "
                     f"`{disp.rendered_disposition()}` | "
                     f"{disp.rendered_obligation()} | {cited} |")
        for name, (part_d, why) in sorted(disp.parts.items()):
            lines.append(f"| &nbsp;&nbsp;↳ {name} | `{part_d}` | {why} | |")

    lines += ["",
              "**Read-out, derived at render time:** "
              + ", ".join(f"{tally[d]} × `{d}`" for d in DISPOSITIONS)
              + f" over {len(dispositions)} promises.",
              ""]

    # THE TWO ARMS, SIDE BY SIDE AND NEVER AVERAGED. A2 calls a disagreement
    # "the most interesting thing this slice could find", so it gets its own
    # section rather than being folded into the answers above.
    if comparisons:
        answered = [c for c in comparisons if c.comparable]
        disagreed = [c for c in answered if not c.agrees]
        lines += [
            "## The same promises, answered against your real material",
            "",
            "Everything above was measured against a small corpus this check "
            "builds for itself. The same promises were then answered a second "
            "time against material that already exists here — a real repository "
            "— judged by the same code, so the two answers are comparable.",
            "",
            f"**{len(answered)} of {len(comparisons)} promises could be asked "
            f"both ways. Of those, {len(answered) - len(disagreed)} gave the "
            f"same answer and {len(disagreed)} did not.**",
            "",
            "| Promise | Built corpus | Your real material | |",
            "|---|---|---|---|",
        ]
        for cmp_ in comparisons:
            built = "holds" if cmp_.constructed else "fails"
            if not cmp_.comparable:
                lines.append(f"| **{cmp_.observable}** | `{built}` | "
                             f"*not comparable* | {cmp_.reason_not_comparable} |")
            else:
                real = "holds" if cmp_.real else "fails"
                lines.append(
                    f"| **{cmp_.observable}** | `{built}` | `{real}` | "
                    + ("agree" if cmp_.agrees else "**DISAGREE**") + " |")
        if disagreed:
            lines += ["", "**A disagreement is the most important line in this "
                      "report.** A promise holding against a corpus built for "
                      "the purpose, but not against real material, is what the "
                      "second run exists to catch."]
        lines.append("")

    lines += ["## What this verification could not demonstrate at all", ""]
    lines += [f"- {limit}" for limit in limits]
    lines += ["",
              "*Every figure above is counted from the dispositions at render "
              "time; none is stored.*"]
    _ = counts_source
    return "\n".join(lines)


# =========================================================================== #
# A5 — the limits record
#
# In plain words, no module names: what this verification could not demonstrate,
# stated separately from what it demonstrated and found wanting. A limit must not
# be phrased so as to imply the underlying behaviour is fine.
# =========================================================================== #


#: Every production seam the fact-check arms replace, in plain words. Named in
#: ONE place so the limits can count them instead of asserting a number.
STAND_INS: Tuple[str, ...] = (
    "the checker that reaches a verdict (and only one of it, where a real "
    "research run uses a panel of three from different model families)",
    "a second closing gate that can independently fail a report",
    "the step that writes the finished report back to disk",
    "the step that records the run in the activity log",
)


def effectively_unwatched_links(baseline) -> Tuple[str, ...]:
    """Links whose declared watchers are ALL red at baseline — so nothing watches them.

    A mask can name a link while every observation carrying that link is already
    failing, in which case the matrix passes for that link vacuously. The
    exhaustiveness test cannot see this: the mask is populated, so the link looks
    covered. It was real here — while C6 was red for an unrelated reason, `L4`
    and `L8` were watched by nothing and the matrix reported them `OK`.
    """
    out = []
    for link_id in LINK_IDS:
        watchers = [o for o in expected_red_set(link_id) if baseline[o].green]
        if not watchers:
            out.append(link_id)
    return tuple(out)


def build_limits(dispositions, baseline=None, observed=None) -> Tuple[str, ...]:
    """What this verification could not demonstrate — DERIVED, not recited.

    Most of these were hand-written at first, and one of them went stale within
    the hour: it said the internal-source counting check had nothing that could
    be taken away to prove it sound, which stopped being true the moment that
    observation acquired a mask. A limits list that is retyped rather than
    derived is a list that quietly starts lying in the reassuring direction.
    """
    limits = []

    unreachable_kinds = sorted(
        {name for d in dispositions
         if BY_ID[d.observable].axis == AXIS_SOURCE_KIND
         for name, (v, _) in d.parts.items()
         if v == UNREADABLE_DISPOSITION and name in SOURCE_KINDS})
    if unreachable_kinds:
        limits.append(
            f"{len(unreachable_kinds)} of the {len(SOURCE_KINDS)} kinds of "
            "source a person can pick were not read here. Reading a tracker "
            "needs a live login this run does not have, and the public web is "
            "read by a different part of the system than the one this walk "
            "drives. Nothing here says whether those two work.")

    unreachable_locators = sorted(
        {name for d in dispositions
         if BY_ID[d.observable].axis == AXIS_LOCATOR_CLASS
         for name, (v, _) in d.parts.items()
         if v == UNREADABLE_DISPOSITION and name in LOCATOR_CLASSES})
    if unreachable_locators:
        limits.append(
            "Whether a citation that has gone dead is caught was shown only for "
            "citations that name a file. For a citation naming a place in a code "
            "repository the system is deliberately built never to say a source "
            "is gone — only that it is fine, or that it could not tell. So for "
            "that kind of citation, a source that has genuinely disappeared "
            "would not be reported as broken here, and this walk cannot show "
            "otherwise.")

    unreachable_evidence = sorted(
        {name for d in dispositions
         if BY_ID[d.observable].axis == AXIS_EVIDENCE_PATH
         for name, (v, _) in d.parts.items()
         if v == UNREADABLE_DISPOSITION})
    if unreachable_evidence:
        limits.append(
            "A finding can be checked in one of two ways — by re-opening the "
            "source itself, or by comparing against a copy kept at the time of "
            "reading. Only the first happened here. The second is used for "
            "sources behind a login, which this run had none of, so the wording "
            "a reader would see was checked but no actual finding used it.")

    # The count is DERIVED from the replacements actually in force, because a
    # count typed into prose goes stale the moment a fourth is added — and one
    # did: this line said "THREE" while four seams were replaced.
    limits.append(
        f"The parts of the fact-checking machinery exercised here ran with "
        f"{len(STAND_INS)} things replaced by stand-ins: "
        + "; ".join(STAND_INS) + ". That shows the system carries a verdict to "
        "the right place; it does not show that a real checker would reach that "
        "verdict, nor that the other closing gate would let the report through. "
        "One consequence is easy to miss: the step that would normally put the "
        "\"your approved source list went unenforced\" notice into a report is "
        "one of the replaced ones, so that notice was written by calling the "
        "writer directly rather than by a run that was actually unenforced.")

    limits.append(
        "That a run reads nothing outside what a person picked was checked by "
        "asking the boundary directly about a path outside it, and by taking "
        "that boundary away and watching the check notice. It was not checked "
        "by getting a file from somewhere else in front of the reader and "
        "watching it be turned away, because the list of things to read is "
        "built from the person's own choices before anything is read at all.")

    if baseline is not None:
        structural = [oid for oid in OBSERVABLE_IDS
                      if not MASK[oid] and baseline[oid].green]
        if "C2" in structural:
            limits.append(
                "That a person must frame their question before picking sources "
                "was read from the shape of the code rather than by running it. "
                "It is a genuine reading and it is weaker than the others: "
                "nothing was executed, so nothing could be taken away to prove "
                "the check was watching it.")
        # A promise that FAILS is excluded from every link's red-set by
        # construction (only a baseline-green observation can go red), so its
        # part rows are reported from the walk with nothing having measured them.
        unmeasured = [oid for oid in OBSERVABLE_IDS
                      if not baseline[oid].green and BY_ID[oid].parts]
        if unmeasured:
            limits.append(
                "Where a promise above is answered `fails`, the smaller rows "
                "underneath it still say what was seen — but nothing was taken "
                "away to check that those smaller readings were watching what "
                "they name. A promise that is already failing cannot be measured "
                "that way, so treat those rows as observations rather than as "
                "findings that were tested for soundness.")

        unwatched = effectively_unwatched_links(baseline)
        if unwatched:
            limits.append(
                f"{len(unwatched)} of the checks this walk makes were not tested "
                "for soundness at all, because the only promise that relies on "
                "each was already failing for another reason. Those parts of the "
                "machinery were exercised but nothing confirmed the check was "
                "watching them.")

    # THE ONE-CORPUS DISCLOSURE. Every answer above was produced by reading a
    # small set of files this check built for itself. A real repository IS read
    # elsewhere in the suite, but that read produces no answer for any promise,
    # so nothing above has been confirmed against real data and no comparison
    # between the two is possible. Undisclosed until an independent check derived
    # it; the whole purpose of this section is to name exactly this kind of thing.
    # The one-corpus disclosure that used to sit here is GONE because it is no
    # longer true — the second arm exists. What replaces it is the honest
    # residue: which promises the second arm could not be asked, and why.
    not_comparable = sorted(REAL_ARM_NOT_COMPARABLE)
    limits.append(
        f"{len(not_comparable)} of the {len(OBSERVABLE_IDS)} promises could only "
        "be checked against a small corpus this check builds for itself, not "
        "against your real material. Three of them need a link that is "
        "deliberately broken, and nothing may be broken on purpose in your own "
        "files; the fourth needs a detail the production entry point does not "
        "report back. For those, a promise holding here is not evidence that it "
        "holds on your own material.")

    limits.append(
        "Nothing here checks that a real research session actually calls this "
        "machinery. What calls it is written guidance, which is followed rather "
        "than enforced, and no automatic check covers that step.")

    return tuple(limits)


# =========================================================================== #
# Fixtures
# =========================================================================== #


@pytest.fixture(scope="module")
def _walk_env(tmp_path_factory):
    """One corpus, one baseline walk, one matrix — computed once.

    The matrix is part of the fixture rather than re-run per test for a reason
    beyond speed: a manifest cycle is single-use, so re-running the eleven walks
    per test would make every test after the first fail on a spent session id
    rather than on anything it asserts.
    """
    root = tmp_path_factory.mktemp("s14")
    # `RP_STATE_DIR` is set through a monkeypatch CONTEXT, not by assigning to
    # `os.environ`. A module-scoped fixture that assigns directly never restores
    # it, so the variable would leak into every suite pytest collects after this
    # one — and this module would then be changing the environment of the very
    # broad sweep its own slice compares against a baseline. A verification that
    # contaminates the measurement it is verified by is worse than no
    # verification.
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("RP_STATE_DIR", str(root / "rp"))
        corpus = build_corpus(root)
        baseline = walk(root, corpus, sid=_next_sid("s14-base"))
        observed = run_matrix(root, corpus, baseline)

        # THE SECOND ARM. A2 requires the walk to run twice; this is the other
        # run. It is part of the fixture rather than a test beside it, because
        # the read-out has to be able to report a disagreement, and it can only
        # do that if both answers exist by the time anything renders.
        real = build_real_corpus()
        realside = ({} if real is None
                    else walk_real(root, real, sid=_next_sid("s14-real")))
        comparisons = compare_arms(baseline, realside)
        yield root, corpus, baseline, observed, real, realside, comparisons


# =========================================================================== #
# A1's gate — every observable cites a locked-field line that resolves
# =========================================================================== #


#: The only spine sections an observable may be read out of. A promise taken
#: from anywhere else is a promise taken from a slice's own account of itself.
LOCKED_SECTIONS = frozenset({"## Desired Outcome", "## Desired Solution",
                             "## Metrics"})


def _enclosing_section(lines, line_number: int) -> str:
    """The nearest `## ` heading at or above `line_number` (1-indexed)."""
    for i in range(line_number - 1, -1, -1):
        line = lines[i]
        if line.startswith("## "):
            return line.strip()
    return ""


def _spine_path() -> Optional[Path]:
    """The topic spine, deduplicated by RESOLVED path.

    `~/Projects` is a symlink to `~/repos/Projects`, so a scan that does not
    resolve reports every file twice (S13-obs5).
    """
    candidates = []
    env = os.environ.get("S14_SPINE")
    if env:
        candidates.append(Path(env))
    candidates += [Path.home() / "repos" / "Projects" / "Thoughts" / SPINE_NAME,
                   Path.home() / "Projects" / "Thoughts" / SPINE_NAME]
    seen = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


def test_a1_every_observable_cites_a_locked_field_line_that_resolves():
    """An observable with no locked-field line is not an observable.

    Skipped rather than passed when the spine is unreachable — a check that
    cannot run must not report as one that ran and found nothing, which is the
    exact conflation the capability under test is built to avoid.
    """
    spine = _spine_path()
    if spine is None:
        pytest.skip("topic spine not reachable from this checkout; the "
                    "observable-set provenance check did not run")
    text = spine.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # ANCHORED ON THE TEXT, WITH A UNIQUENESS ASSERTION — never on a line number.
    # This test previously pinned `anchor_line` and went red the moment a
    # `**Status:**` line was authored two lines above the fields, shifting every
    # locked line by two. Nothing about the promises had changed. A line number
    # is a fact about a file's current layout, not about the promise it names,
    # and this topic has already recorded three instances of an anchor matching
    # the wrong occurrence (S13-obs8). "Exactly one match" is the form that
    # cannot silently land somewhere else.
    for obs in OBSERVABLES:
        hits = [i for i, line in enumerate(lines, start=1)
                if obs.anchor_text in line]
        # Occurrences INSIDE the locked fields are what an observable may anchor
        # on. A spine legitimately quotes its own locked outcome elsewhere — in a
        # Q&A entry, in a slice's account of itself — so a whole-file uniqueness
        # rule is wrong: it would redden on a quotation that changes nothing, and
        # the copy in a slice's own summary is precisely the source an observable
        # must NOT anchor on. Scoping to the locked sections says both at once.
        in_locked = [i for i in hits
                     if _enclosing_section(lines, i) in LOCKED_SECTIONS]
        assert len(in_locked) == 1, (
            f"{obs.id}'s anchor must resolve to EXACTLY ONE line inside a locked "
            f"field, so it can only be the promise it names. Found "
            f"{len(in_locked)} inside {sorted(LOCKED_SECTIONS)} "
            f"(at lines {in_locked}); {len(hits)} elsewhere in the spine "
            f"(lines {hits}).\n  anchor: {obs.anchor_text!r}")


def test_a1_the_observable_set_covers_the_six_promises_and_both_metric_requirements():
    assert OBSERVABLE_IDS == ("C1", "C2", "C3", "C4", "C5", "C6", "M1", "M2")


# =========================================================================== #
# A2's gate — the walk runs and every observation is traceable
# =========================================================================== #


def test_a2_the_walk_produces_one_observation_per_observable(_walk_env):
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    assert set(baseline) == set(OBSERVABLE_IDS)
    for oid, obs in baseline.items():
        assert obs.detail, f"{oid} produced no traceable detail"


def test_a2_the_walk_reads_three_source_kinds_through_the_port(_walk_env):
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    c1 = baseline["C1"]
    exercised = {k for k, (v, _) in c1.parts.items() if v is not UNREACHABLE}
    assert exercised == {"code", "knowledge_library", "document_folder",
                         "any combination in one run"}
    assert set(c1.unreachable_parts()) == {"web", "linear"}


def test_the_two_split_axes_do_not_contaminate_each_other(_walk_env):
    """`code` and `linear` are words in BOTH vocabularies — a real collision.

    Deriving a limit by matching part NAMES counted `code` as an unreadable
    SOURCE KIND because C6 cannot read the `code` LOCATOR CLASS, and printed
    "3 of the 5 kinds of source were not read here" about a run that had just
    read a code repository. A false statement in the reassuring direction, from a
    name collision. The axis tag is what makes it underivable.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    limits = build_limits(_all_dispositions(baseline, observed), baseline, observed)
    kind_limit = next((l for l in limits if "kinds of source" in l), None)
    assert kind_limit is not None
    # web + linear, and NOT code — which the walk demonstrably read.
    assert kind_limit.startswith("2 of the 5"), kind_limit
    assert baseline["C1"].parts["code"][0] is True, (
        "the walk did read a code repository, so no limit may say otherwise")


def test_a2_containment_refuses_a_path_outside_the_declaration(_walk_env):
    """C3's own demonstration: read in scope, refuse out of scope, disclose.

    All three conjuncts, because any one alone passes against a broken reader —
    a run that read NOTHING satisfies "nothing strayed" perfectly.
    """
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    detail = baseline["C3"].detail
    assert "read=True" in detail, "nothing was read, so containment is vacuous"
    assert "strayed=[]" in detail
    assert "outside_refused=True" in detail
    assert "unenforced_disclosure_written_into_a_body=True" in detail
    # And the detail must carry its own qualifier: the writer's production
    # trigger is replaced in this same run, so a reader is not left thinking a
    # genuinely unenforced run produced the notice.
    assert "its only production trigger is replaced" in detail


def test_a2_the_two_evidence_paths_read_differently(_walk_env):
    """C5's own demonstration: the two sentences DIFFER, and one is carried.

    Asserted on the difference rather than on presence — a single constant
    string next to both would be a disclosure that discloses nothing.
    """
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    detail = baseline["C5"].detail
    assert "different sentences=True" in detail
    # Which path occurred is RECORDED, and the two paths' wordings differ. The
    # stronger-looking "the sentence matches the recorded path" was REMOVED
    # rather than kept: `evidence_sentence` branches on the path alone, so that
    # comparison reduced to `f(p) == f(p)` and could not fail.
    assert "every real reading records a known path=True" in detail
    # The captured half is NOT exercised by any real read here, and is reported
    # as unexercised rather than as demonstrated.
    assert baseline["C5"].parts["captured copy"][0] is UNREACHABLE
    original = dr.AdmittedReading(
        item="x", content="c", marker="m",
        evidence_path=arec.EVIDENCE_ORIGINAL).evidence_sentence()
    captured = dr.AdmittedReading(
        item="x", content="c", marker="m",
        evidence_path=arec.EVIDENCE_CAPTURED).evidence_sentence()
    assert original and captured and original != captured


def test_a2_an_unchecked_source_is_stated_plainly_not_passed_as_verified(_walk_env):
    """C6's own demonstration, across all three of its faces."""
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    detail = baseline["C6"].detail
    assert "refusal_named=True" in detail, "a refused source was not named"
    assert "downgraded=True" in detail, "a dead citation still looks verified"
    assert "fact_check_folded_on_undeclared=True" in detail, (
        "a citation outside the approved list did not fold the verdict")
    # BOTH directions. A gate shown only to fire cannot be told from one that
    # fires on everything, and this arm's verdict was previously computed and
    # discarded rather than asserted.
    assert "passed_a_compliant_report=True" in detail, (
        "a report citing only inside the approved list was ALSO folded, so the "
        "gate is refusing everything rather than measuring the declaration")


def test_a2_an_internal_source_file_enters_the_measured_population(_walk_env):
    """M1's own demonstration: a marker exists AND the axis graded it.

    `INCOMPLETE` is named specifically. `!= PASS` would also be satisfied by
    `NOOP`, which is a run that never happened.
    """
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    detail = baseline["M1"].detail
    assert "first_round_marker=True" in detail
    # The COMPARISON, on the marker fields that decide whether a file enters the
    # measured population on the same terms — not `bool(a) and bool(b)`, which
    # two markers with different verdicts and schemas would satisfy.
    assert "same_terms=True" in detail
    assert "'kind': 'research'" in detail
    # And the axis verdict is named as a SEPARATE run's, because it is one.
    assert "a SEPARATE undeclared-citation run graded 'INCOMPLETE'" in detail


def test_a2_a_dead_citation_counts_broken_and_a_misrooted_one_does_not(_walk_env):
    """M2's own demonstration, with the negative half.

    Counting `dead` alone would pass against an implementation that counted
    every outcome as broken.
    """
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    assert baseline["M2"].parts["local-file"][0] is True
    # Read from the OBSERVED tally, not from a comparison of two constants: the
    # corpus really contains a misrooted citation, it really resolved as one, and
    # the broken count really excluded it.
    detail = baseline["M2"].detail
    assert "'dead': 1" in detail, detail
    assert "'misrooted': 1" in detail, (
        "no misrooted citation was resolved, so the negative half of this "
        "promise was never exercised: " + detail)


def test_a2_a_code_pin_a_real_read_produced_is_what_the_resolver_reopens(_walk_env):
    """THE JOIN. This is the one assertion no existing suite made.

    The read half proved a pin is produced. The citation half proved a citation
    resolves. Neither proved the marker a real declared read produces is the
    thing the resolver can re-open — because no suite driving
    `read_declared_sources` ever touched `_citation_resolve`.
    """
    _root, _corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    c4 = baseline["C4"]
    assert c4.parts["code"][0] is True, c4.detail


def test_a2_the_declared_read_and_the_citation_resolver_meet_in_one_run(_walk_env):
    """The two halves are joined BEHAVIOURALLY, not by a source-text match.

    An earlier version grepped this module's own source for three call strings.
    That is the text-anchored form S13-obs8 recorded against — it passes on a
    module that makes the calls and throws the results away, and it reddens on a
    rename that changes nothing. Asserted on the RESULTS instead: the repository
    revision the read pinned is the one the resolver re-opened against, so the
    two halves demonstrably handled the same object.
    """
    _root, corpus, baseline, _observed, _real, _realside, _cmp = _walk_env
    c4 = baseline["C4"]
    # The read half produced a pin naming the real HEAD...
    assert corpus.head in c4.detail, c4.detail
    # ...and the citation half re-opened THAT pin, not a fixture of its own.
    assert c4.parts["code"][0] is True, (
        "the code pin a real declared read produced was not the thing the "
        "resolver re-opened — the join this module exists to make is broken")
    assert c4.parts["local-file"][0] is True


# =========================================================================== #
# A3 — the independence matrix, and BOTH failure directions
# =========================================================================== #


def test_a2_the_walk_ran_TWICE_over_two_different_corpora(_walk_env):
    """A2's two-corpus requirement, asserted where it can fail.

    The second arm reads material this test did not create. If it never ran, the
    read-out is the fixture agreeing with itself — which is the whole reason A2
    asks for it.
    """
    _root, _corpus, _baseline, _observed, real, realside, _cmp = _walk_env
    if real is None:
        pytest.skip("no real repository reachable here, so the second arm could "
                    "not run; the read-out reports it as not comparable")
    assert realside, "the second arm produced no observations at all"
    # It really is a different corpus: the real repository is not under tmp.
    assert not str(real.repo).startswith("/private/var"), real.repo
    assert not str(real.repo).startswith("/tmp"), real.repo
    assert real.head and len(real.head) == 40, real.head


def test_a2_both_arms_are_judged_by_the_SAME_observer(_walk_env):
    """Agreement means nothing unless both answers come from one judge.

    If each arm had its own assertions, "they agree" would be a statement about
    two different questions. C2's observer is literally shared, and the rest are
    the same `Observation` shape scored the same way.
    """
    _root, _corpus, baseline, _observed, real, realside, _cmp = _walk_env
    if real is None:
        pytest.skip("second arm did not run")
    for oid in realside:
        assert isinstance(realside[oid], Observation)
        assert realside[oid].observable == oid
    # C2 is corpus-independent, so both arms must give it the identical answer.
    assert realside["C2"].green == baseline["C2"].green


def test_a2_a_disagreement_between_the_arms_would_be_REPORTED(_walk_env):
    """The reporting path is exercised, not assumed.

    A2 requires a disagreement to be reported and never averaged. The live run
    may legitimately show none, so the renderer is driven with a synthetic
    disagreement to prove the path exists and surfaces it. Without this, "no
    disagreement" and "disagreements are invisible" look identical.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    dispositions = _all_dispositions(baseline, observed)

    forced = (ArmComparison("C1", constructed=True, real=False),)
    text = render_readout(dispositions, build_limits(dispositions, baseline, observed),
                          None, comparisons=forced)
    assert "**DISAGREE**" in text
    assert "most important line in this report" in text

    agreeing = (ArmComparison("C1", constructed=True, real=True),)
    calm = render_readout(dispositions, build_limits(dispositions, baseline, observed),
                          None, comparisons=agreeing)
    assert "**DISAGREE**" not in calm, (
        "the renderer reports a disagreement that did not happen")


def test_a2_a_not_comparable_promise_is_never_scored_as_agreement(_walk_env):
    """`could not be asked` must not read as `asked and matched`.

    This is the `UNREACHABLE` doctrine one level up: collapsing the two is how a
    capability comes to look better than it is.
    """
    unasked = ArmComparison("C6", constructed=True, real=None,
                            reason_not_comparable="needs a broken link")
    assert not unasked.comparable
    assert not unasked.agrees
    matched = ArmComparison("C1", constructed=True, real=True)
    assert matched.comparable and matched.agrees
    differed = ArmComparison("C1", constructed=True, real=False)
    assert differed.comparable and not differed.agrees


def test_a3_the_matrix_is_exhaustive_over_links():
    assert len(LINKS) == 10
    assert len(set(LINK_IDS)) == len(LINK_IDS)
    covered = set()
    for mask in MASK.values():
        covered |= set(mask)
    assert covered == set(LINK_IDS), (
        f"a link on no observation's mask is a neuter nothing watches: "
        f"{sorted(set(LINK_IDS) - covered)}")
    assert set(MASK) == set(OBSERVABLE_IDS)


def test_a3_every_observation_reddens_exactly_on_its_declared_mask(_walk_env):
    """The two-sided measurement: noise where declared, silence everywhere else."""
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    # Only observations green at baseline can redden; a baseline red is a
    # finding the read-out reports, not something to launder in here.
    problems = matrix_problems(baseline, observed)
    assert not problems, "\n".join(problems)


def _synthetic(green: bool) -> Observation:
    return Observation("X", green=green, detail="synthetic")


def test_a3_control_the_gate_catches_an_OVER_COUPLED_observation():
    """Failure direction one, driven through the REAL gate.

    An observation that reddens under a link its mask does not name must be
    reported. The inputs are synthetic so this shares no derivation with the
    live matrix, but the code under test is `matrix_problems` — the same
    function the live check calls.

    The first version of this control asserted set arithmetic on two literals.
    It demonstrated the IDEA of the gate while leaving the gate itself untested,
    and would have passed against a `matrix_problems` that returned `[]`
    unconditionally. That is the shape of defect this module exists to catch,
    committed in the control written to prove the module sound — the same
    self-confirming pattern S13-obs8 recorded.
    """
    baseline = {"X": _synthetic(True)}
    mask = {"X": frozenset({"L1"})}
    observed = {"L1": frozenset({"X"}), "L3": frozenset({"X"})}   # L3 unexpected
    problems = matrix_problems(baseline, observed, mask, ("L1", "L3"))
    assert problems, "an off-mask red was not reported"
    assert "over-coupled: ['X']" in problems[0]


def test_a3_control_the_gate_catches_a_VACUOUS_observation():
    """Failure direction two, driven through the REAL gate."""
    baseline = {"X": _synthetic(True)}
    mask = {"X": frozenset({"L1"})}
    observed = {"L1": frozenset()}                                # never reddens
    problems = matrix_problems(baseline, observed, mask, ("L1",))
    assert problems, "a missing red was not reported"
    assert "vacuous: ['X']" in problems[0]


def test_a3_control_the_gate_passes_a_correctly_coupled_observation():
    """The third leg: the gate is not simply always-failing.

    Without this, both controls above would still pass against a
    `matrix_problems` that reported a problem for every input.
    """
    baseline = {"X": _synthetic(True)}
    mask = {"X": frozenset({"L1"})}
    observed = {"L1": frozenset({"X"}), "L3": frozenset()}
    assert matrix_problems(baseline, observed, mask, ("L1", "L3")) == []


def test_a3_c2_declares_an_empty_mask_and_the_readout_must_say_why(_walk_env):
    """C2's weakness is declared, not hidden.

    An empty mask is normally the signature of a vacuous observation. C2's is
    structural — a function signature cannot be neutered — so it is stated in
    the limits rather than rendered like the others.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    assert MASK["C2"] == frozenset()
    limits = build_limits(_all_dispositions(baseline, observed), baseline, observed)
    assert any("frame their question" in limit for limit in limits), (
        "C2's structural-only reading is not disclosed")


# =========================================================================== #
# A4 — the read-out's own rules, executed rather than remembered
# =========================================================================== #


def test_a4_every_disposition_is_one_of_exactly_three_values(_walk_env):
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    for oid in OBSERVABLE_IDS:
        sound = _is_sound(oid, baseline, observed)
        disp = disposition_for(baseline[oid], sound,
                               citation="test_a2_the_walk_produces_one_observation_per_observable")
        assert disp.rendered_disposition() in DISPOSITIONS


def test_a4_every_disposition_carries_its_obligation(_walk_env):
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    for oid in OBSERVABLE_IDS:
        disp = disposition_for(
            baseline[oid], _is_sound(oid, baseline, observed),
            citation="test_a2_the_walk_produces_one_observation_per_observable")
        assert disp.rendered_obligation().strip(), (
            f"{oid} rendered a bare disposition — a `holds` with no citation, a "
            f"`fails` with no reading, or an unreadable with no reason is "
            f"indistinguishable from a silence")


def test_a4_a_holds_whose_citation_does_not_resolve_is_withheld():
    """The withholding rule is executed, not remembered."""
    good = Disposition("C1", HOLDS, "obligation",
                       citation="test_a4_a_holds_whose_citation_does_not_resolve_is_withheld")
    assert not good.withheld()
    assert good.rendered_disposition() == HOLDS

    bad = Disposition("C1", HOLDS, "obligation",
                      citation="test_this_name_does_not_exist_anywhere")
    assert bad.withheld()
    assert bad.rendered_disposition() == UNREADABLE_DISPOSITION
    assert "withheld" in bad.rendered_obligation()

    bare = Disposition("C1", HOLDS, "obligation", citation="")
    assert bare.withheld(), "a `holds` citing nothing at all must not stand"


def test_a4_every_citation_in_the_real_readout_resolves(_walk_env):
    """A4's SECOND gate: every citation the read-out prints resolves to a real test.

    The withholding rule above is exercised only on synthetic `Disposition`
    objects, and `citation_resolves` is reached from exactly one production
    place — `Disposition.withheld()`. So until this test existed, an unresolvable
    citation on a REAL disposition degraded silently to `cannot be read` at
    render time and no gate ever failed. The read-out would still have rendered;
    it would simply have quietly stopped claiming something it had claimed
    before, which is the kind of silent downgrade this slice is built to refuse.

    Asserted over EVERY disposition, not only the `holds` ones: a `fails` line
    also prints a citation, and a reader following it to a name that does not
    exist is misled just as badly.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    unresolved = []
    for disp in _all_dispositions(baseline, observed):
        if not disp.citation:
            unresolved.append(f"{disp.observable}: no citation at all")
        elif not citation_resolves(disp.citation):
            unresolved.append(f"{disp.observable}: {disp.citation!r} names no test here")
    assert not unresolved, (
        "the read-out prints a citation a reader cannot follow:\n  "
        + "\n  ".join(unresolved))


def test_a4_control_the_citation_gate_catches_a_name_that_does_not_exist():
    """The gate above is not vacuous — it fails on a citation that does not resolve."""
    assert citation_resolves("test_a4_every_citation_in_the_real_readout_resolves")
    assert not citation_resolves("test_a_name_no_function_in_this_module_carries")
    assert not citation_resolves("")


def test_a4_the_summary_rule_is_defined_and_never_a_vote():
    assert summarise({"a": HOLDS, "b": FAILS}) == FAILS
    assert summarise({"a": HOLDS, "b": UNREADABLE_DISPOSITION}) == HOLDS
    assert summarise({"a": UNREADABLE_DISPOSITION,
                      "b": UNREADABLE_DISPOSITION}) == UNREADABLE_DISPOSITION
    # Two unreadable against one holds is still `holds` — a majority vote would
    # have said otherwise, and would have discarded a real answer.
    assert summarise({"a": HOLDS, "b": UNREADABLE_DISPOSITION,
                      "c": UNREADABLE_DISPOSITION}) == HOLDS


def test_a4_a_holds_naming_undemonstrated_parts_says_so(_walk_env):
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    disp = disposition_for(
        baseline["C1"], _is_sound("C1", baseline, observed),
        citation="test_a2_the_walk_reads_three_source_kinds_through_the_port")
    if disp.rendered_disposition() == HOLDS:
        assert "NOT demonstrated here" in disp.rendered_obligation()
        assert "web" in disp.rendered_obligation()
        assert "linear" in disp.rendered_obligation()


def test_a4_the_readout_derives_its_counts_at_render_time(_walk_env):
    """No stored figure — the defect that got into this slice's own plan five times."""
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    dispositions = _all_dispositions(baseline, observed)
    text = render_readout(dispositions,
                          build_limits(dispositions, baseline, observed),
                          None, comparisons=_cmp)
    tally = {d: sum(1 for x in dispositions if x.rendered_disposition() == d)
             for d in DISPOSITIONS}
    for d in DISPOSITIONS:
        assert f"{tally[d]} × `{d}`" in text
    assert f"over {len(dispositions)} promises" in text
    assert sum(tally.values()) == len(dispositions)


# =========================================================================== #
# A5 — the limits record, and the four assertions that make disclosure mechanical
# =========================================================================== #


def test_a5_the_limits_section_is_not_empty(_walk_env):
    """A verification of this reach claiming no blind spots is itself the finding."""
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    limits = build_limits(_all_dispositions(baseline, observed), baseline, observed)
    assert limits


#: What the limits section must SAY when a given observable comes back
#: `cannot be read`. Plain words, because the section is for a reader who did not
#: build any of this — so the check cannot key on an identifier.
#:
#: This table exists because the first version of the assertion below read
#: `assert key in limits_text or len(limits_text) > 0`, whose right-hand side is
#: true whenever any limit exists at all. It could not fail. A vacuous assertion
#: inside the slice built to catch vacuous assertions is worth recording rather
#: than quietly replacing.
LIMIT_KEYPHRASE: Mapping[str, str] = {
    "C1": "kinds of source",
    "C2": "frame their question",
    "C3": "written guidance",
    "C4": "code repository",
    "C5": "code repository",
    "C6": "code repository",
    "M1": "stand-in",
    "M2": "code repository",
}


def test_a5_every_unreadable_observable_appears_in_the_limits(_walk_env):
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    dispositions = _all_dispositions(baseline, observed)
    limits_text = " ".join(build_limits(dispositions, baseline, observed)).lower()
    unreadable = [d for d in dispositions
                  if d.rendered_disposition() == UNREADABLE_DISPOSITION]
    for disp in unreadable:
        phrase = LIMIT_KEYPHRASE[disp.observable]
        assert phrase.lower() in limits_text, (
            f"{disp.observable} came back `cannot be read` and the limits "
            f"section never mentions {phrase!r} — an undisclosed blind spot")


def test_a5_every_link_the_walk_could_not_drive_appears_in_the_limits(_walk_env):
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    limits_text = " ".join(build_limits(_all_dispositions(baseline, observed), baseline, observed)).lower()
    # The two kinds the walk cannot drive, in the operator's own words.
    assert "login" in limits_text, "the credentialed source's absence is undisclosed"
    assert "public web" in limits_text, "the web arm's absence is undisclosed"
    assert "stand-in" in limits_text, "the injected checker panel is undisclosed"


def test_a5_an_observable_narrowed_by_a_DRIVEN_link_still_appears_in_the_limits(
        _walk_env):
    """The load-bearing fourth assertion.

    The other three key on `cannot be read` and on links the walk could not
    drive. `L6b` IS driven and IS green, so an observable narrowed by it is
    neither — and a read-out could pass every other mechanical check while never
    mentioning that dead-counting is undemonstrable for code pins. That is a
    guard-rail-compliant path to a misleading record, which is why it is closed
    here rather than trusted to prose.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    dispositions = _all_dispositions(baseline, observed)
    limits_text = " ".join(build_limits(dispositions, baseline, observed)).lower()

    narrowed = [d for d in dispositions if d.undemonstrated_parts()]
    assert narrowed, (
        "no observable carries sub-dispositions — either the split was dropped "
        "or the walk stopped being partial, and both need looking at")
    assert "code repository" in limits_text, (
        "an observable narrowed by a DRIVEN, GREEN link is not disclosed")

    # EVERY narrowing axis must reach the limits, not just the one that was
    # written about first. Splitting C5 by evidence path added a narrowing that
    # no limit disclosed, and the `code repository` assertion above passed
    # anyway — a check aimed at one instance cannot police a class.
    axes_narrowed = {BY_ID[d.observable].axis for d in narrowed
                     if BY_ID[d.observable].axis}
    for axis, phrase in ((AXIS_SOURCE_KIND, "kinds of source"),
                         (AXIS_LOCATOR_CLASS, "code repository"),
                         (AXIS_EVIDENCE_PATH, "kept at the time of reading")):
        if axis in axes_narrowed:
            assert phrase in limits_text, (
                f"an observable narrowed along {axis!r} is not disclosed in the "
                f"limits (expected the reader to be told about {phrase!r})")


def test_a5_a_link_nothing_effectively_watches_is_disclosed(_walk_env):
    """The fifth assertion, and the one the other four cannot reach.

    A mask can NAME a link while every observation carrying it is already red, so
    the matrix passes for that link vacuously and the exhaustiveness test sees a
    populated mask and is satisfied. That state was real in this very module
    while C6 was red for an unrelated reason: `L4` and `L8` were watched by
    nothing and the matrix reported both `OK`.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    unwatched = effectively_unwatched_links(baseline)
    limits = build_limits(_all_dispositions(baseline, observed), baseline, observed)
    if unwatched:
        assert any("not tested for soundness" in limit for limit in limits), (
            f"{sorted(unwatched)} are watched by nothing and the read-out does "
            f"not say so")


def test_a5_the_limits_are_derived_not_recited(_walk_env):
    """A limit that stops being true must stop being printed.

    One did, within the hour: the hand-written list claimed the internal-source
    counting check had nothing that could be taken away to prove it sound, which
    ceased to be true the moment that observation acquired a mask. Removing a
    part from the reachable set must remove its limit.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    dispositions = _all_dispositions(baseline, observed)
    with_parts = build_limits(dispositions, baseline, observed)

    stripped = []
    for disp in dispositions:
        clone = Disposition(disp.observable, disp.disposition, disp.obligation,
                            disp.citation, parts={})
        stripped.append(clone)
    without_parts = build_limits(stripped, baseline, observed)

    assert len(without_parts) < len(with_parts), (
        "the limits did not shrink when every unreachable part was removed, so "
        "they are being recited rather than derived from what was observed")


def test_a5_the_not_comparable_promises_are_disclosed(_walk_env):
    """What the SECOND arm could not be asked must be stated, and why.

    This replaces a test that asserted a one-corpus disclosure. That disclosure
    was correct while only one arm existed and is now false, so it was removed
    rather than left to read as a stale limit — the exact failure mode
    `test_a5_the_limits_are_derived_not_recited` exists to prevent, met here for
    real rather than in the abstract.
    """
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    limits = build_limits(_all_dispositions(baseline, observed), baseline, observed)
    joined = " ".join(limits).lower()
    assert "against your real material" in joined, (
        "the limits do not say which promises could not be checked against real "
        "material")
    # And the count must be DERIVED, not typed: it has to match the map.
    assert f"{len(REAL_ARM_NOT_COMPARABLE)} of the {len(OBSERVABLE_IDS)} promises" \
        in " ".join(limits), " ".join(limits)


def test_a5_no_limit_implies_the_underlying_behaviour_is_fine(_walk_env):
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    for limit in build_limits(_all_dispositions(baseline, observed), baseline, observed):
        lowered = limit.lower()
        assert "works fine" not in lowered
        assert "no problem" not in lowered
        assert "presumably" not in lowered


def test_a5_the_limits_are_written_in_plain_words_not_module_names(_walk_env):
    """A reader who did not build any of this must be able to act on them."""
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    for limit in build_limits(_all_dispositions(baseline, observed), baseline, observed):
        assert ".py" not in limit, f"a filename leaked into a limit: {limit!r}"
        assert "_" not in limit, (
            f"an identifier name leaked into a limit: {limit!r}")
        assert "()" not in limit, (
            f"a callable name leaked into a limit: {limit!r}")


# =========================================================================== #
# Helpers shared by the A4/A5 tests
# =========================================================================== #


def _is_sound(oid: str, baseline, observed) -> bool:
    """Did this observation redden on exactly its declared mask?

    An observable with an EMPTY declared mask is not sound in this sense — it is
    consistent with the matrix but nothing demonstrates it is coupled to the
    behaviour it names. Reporting that as sound would be the over-claim this
    slice exists to catch.
    """
    declared = frozenset(l for l in MASK[oid] if baseline[oid].green)
    if not declared:
        return False
    for link_id in LINK_IDS:
        should = link_id in declared
        did = oid in observed[link_id]
        if should != did:
            return False
    return True


def _all_dispositions(baseline, observed):
    # ONE CITATION PER OBSERVABLE, each naming a test that demonstrates THAT
    # promise. Five of these pointed at
    # `test_a2_the_walk_produces_one_observation_per_observable` — a test that
    # only asserts an observation exists and carries a detail string. Those
    # citations RESOLVED (the name is real, so the withholding rule passed them)
    # while demonstrating nothing about the promise they answered. A citation
    # that resolves and means nothing is a decoration, and the withholding rule
    # cannot catch it — only naming the right test can.
    citations = {
        "C1": "test_a2_the_walk_reads_three_source_kinds_through_the_port",
        "C2": "test_a3_c2_declares_an_empty_mask_and_the_readout_must_say_why",
        "C3": "test_a2_containment_refuses_a_path_outside_the_declaration",
        "C4": "test_a2_a_code_pin_a_real_read_produced_is_what_the_resolver_reopens",
        "C5": "test_a2_the_two_evidence_paths_read_differently",
        "C6": "test_a2_an_unchecked_source_is_stated_plainly_not_passed_as_verified",
        "M1": "test_a2_an_internal_source_file_enters_the_measured_population",
        "M2": "test_a2_a_dead_citation_counts_broken_and_a_misrooted_one_does_not",
    }
    return [disposition_for(baseline[oid], _is_sound(oid, baseline, observed),
                            citations.get(oid, ""))
            for oid in OBSERVABLE_IDS]


# =========================================================================== #
# The real-corpus arm — a SMOKE TEST of the production CLI, in-process
#
# WHAT THIS ARM IS, STATED ACCURATELY, BECAUSE IT WAS PREVIOUSLY OVERSTATED HERE.
# It reads a repository that actually exists in this workspace, READ-ONLY,
# through `declared_read.main(["read", …])` — the production entry point — and
# checks that the call completes, that the declaration is read back off a real
# manifest cycle, and that the pin it renders has the right shape.
#
# It does NOT produce an observation for any observable, and nothing here
# compares its result against the constructed corpus's. The plan's A2 asked for
# the walk to run twice and for disagreement between the two arms to be reported;
# that is NOT what shipped, and the limits section says so. This banner used to
# read "The constructed corpus proves the fixture agrees with itself. This arm
# reads a repository that actually exists…", which implied the second half of
# that sentence answered the first. It does not: the read-out is the constructed
# corpus's answer alone, and deleting this test would change no figure in it.
#
# In-process as a FUNCTION, never as a subprocess: a subprocess would put every
# neutered attribute out of monkeypatch's reach and silently turn every A3 arm
# green, which is the exact vacuity this module exists to prevent.
#
# `--research-file` is pinned INSIDE tmp_path. The CLI files its admission record
# as an advisory artifact of the research file's own slug family, so a workspace
# path would write untracked files into the topic folder and defeat the read-only
# constraint this arm asserts.
# =========================================================================== #


def _real_repository() -> Optional[Path]:
    """A real git repository in this workspace, or None."""
    for candidate in (CONFIG_DIR, Path.home() / "repos" / "Projects"):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (resolved / ".git").exists():
            return resolved
    return None


# =========================================================================== #
# THE SECOND ARM — the same observables, answered against material that already
# exists in this workspace.
#
# A2 requires the walk to run TWICE and requires a disagreement between the arms
# to be reported rather than averaged. The first implementation shipped only the
# constructed arm and a smoke test beside it, so the read-out was the fixture's
# answer alone — which is the failure ("the fixture agrees with itself") the
# second arm exists to prevent.
#
# THE OBSERVERS ARE THE SAME FUNCTIONS. That is what makes the two answers
# comparable at all: if the real arm had its own assertions, "agreement" would be
# a statement about two different questions.
#
# READ-ONLY. Nothing is written into the repository or the library; the research
# file and every record land under `tmp_path`.
# =========================================================================== #


@dataclass
class RealCorpus:
    """Material that already exists here — not built by this test."""

    repo: Path
    head: str
    library: Path


def build_real_corpus() -> Optional[RealCorpus]:
    """The real repository + knowledge-library folder, or None if absent."""
    repo = _real_repository()
    if repo is None:
        return None
    try:
        head = _git(repo, "rev-parse", "HEAD")
    except (subprocess.CalledProcessError, OSError):
        return None
    library = (Path.home() / "repos" / "Projects" / "<KL>").resolve()
    if not library.is_dir():
        return None
    return RealCorpus(repo=repo, head=head, library=library)


#: Why a given observable CANNOT be answered on the real arm. An observable in
#: this map is reported `not comparable` with its reason — never as agreement,
#: and never as a disagreement. Collapsing "could not be asked" into "asked and
#: matched" is the same conflation `UNREACHABLE` exists to prevent, one level up.
REAL_ARM_NOT_COMPARABLE: Mapping[str, str] = {
    "C5": ("the production entry point's structured output does not carry which "
           "evidence route a reading took, so this cannot be read from a real run"),
    "C6": ("this needs a citation that is deliberately dead, and nothing may be "
           "broken on purpose in real workspace material"),
    "M1": ("this needs the fact-check engine driven over a planted report, which "
           "would mean writing one into the workspace"),
    "M2": ("this needs a deliberately dead citation, for the same reason as above"),
}


def _readings_from_cli(payload) -> dr.DeclaredReadResult:
    """Rebuild the reader's own result object from the CLI's structured output.

    The observers take a `DeclaredReadResult`, so rebuilding one is what lets the
    SAME observer run on both arms. `evidence_path` is absent from the CLI's
    output and is left empty rather than guessed — which is exactly why C5 is
    declared not-comparable above instead of being allowed to read as a
    disagreement.
    """
    return dr.DeclaredReadResult(
        readings=tuple(dr.AdmittedReading(item=r["item"], content=r["content"],
                                          marker=r["marker"], evidence_path="")
                       for r in payload.get("readings", [])),
        refusals=tuple(dr.RefusedRead(item=r["item"], obligation=r["obligation"],
                                      reason=r["reason"])
                       for r in payload.get("refusals", [])))


def walk_real(tmp_root: Path, real: RealCorpus,
              sid: str) -> Dict[str, Observation]:
    """The second arm: the same observables, over material that already exists.

    Drives `declared_read.main(["read", …])` IN-PROCESS — the production entry
    point, as A2 requires, and in-process so a neuter would still reach it.
    """
    import contextlib
    import io

    draft = tmp_root / "Thoughts" / f"{sid}-20260910000000_RESEARCH.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# draft\n", encoding="utf-8")

    record = spk.build_record(
        spk.Selection(bounds=(("code", spk.Bound(selectors=(str(real.repo),))),)),
        route=spk.ROUTE_NINJA)

    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": str(draft),
                    "caller_skill": "/research",
                    "user_approved_scope": True,
                    "scope_record": json.loads(record.to_json())},
                   cycle_id="default")

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = dr.main(["read", sid, str(draft),
                      "--projects-root", str(real.repo),
                      "--query", "how is a declared source admitted?",
                      "--json"])
    assert rc == 0, "the production entry point failed on real material"
    read_result = _readings_from_cli(json.loads(buffer.getvalue()))

    # Resolve the pins the REAL read produced, exactly as the constructed arm
    # resolves its own.
    pin_body = "# Findings\n\n" + "\n\n".join(
        f"A finding. {r.marker}" for r in read_result.readings if r.marker)
    checkout = lambda name: str(real.repo) if name == real.repo.name else None
    real_pin_resolutions = cr.resolve_all(
        fce.parse_citations(pin_body), exists=os.path.exists,
        workspace_root=str(real.repo), citing_file=str(draft),
        checkout_for=checkout)

    disclosure_file = tmp_root / "disclosure" / f"{sid}_RESEARCH.md"
    disclosure_file.parent.mkdir(parents=True, exist_ok=True)
    disclosure_file.write_text("# Report\n\nA claim.\n", encoding="utf-8")
    fce._write_source_disclosure_section(
        str(disclosure_file),
        "the approved source list could not be enforced on this run")
    disclosure_text = disclosure_file.read_text(encoding="utf-8")

    read_names = {Path(r.item).name for r in read_result.readings}

    # C1 on real material. THE CROSS-KIND CONJUNCT IS ASKED HERE TOO — it is a
    # fact about the picker's route policy, not about any corpus, so omitting it
    # made this arm answer a narrower question than the constructed one and
    # produced a spurious disagreement.
    combinable_ok, combinable_why = combination_across_kinds_is_declarable(
        real.repo, real.library)
    c1_parts: Dict[str, Tuple[Optional[bool], str]] = {
        "code": (bool(read_names), "the declared real repository produced a reading"),
        "knowledge_library": (UNREACHABLE, "not declared on this arm"),
        "document_folder": (UNREACHABLE, "not declared on this arm"),
        "web": (UNREACHABLE, dr.DRIVEN_ELSEWHERE.get(srec.KIND_WEB, "")),
        "linear": (UNREACHABLE, "needs a live authorization this run does not hold"),
        "any combination in one run": (combinable_ok, combinable_why),
    }
    c1 = Observation(
        "C1", green=bool(read_names) and combinable_ok,
        detail=(f"real repository produced {len(read_names)} readings; "
                f"one declaration spanning kinds: {combinable_ok}"),
        parts=c1_parts)

    inside = str(real.repo.resolve())
    strayed = [r.item for r in read_result.readings
               if not str(Path(r.item).resolve()).startswith(inside)]
    outside_refused = not getattr(
        record.check(srec.KIND_CODE, Path.home() / "no-such-place" / "x.py"),
        "admitted", False)
    c3 = Observation(
        "C3",
        green=bool(read_result.readings) and not strayed and outside_refused
        and fce._SOURCE_DISCLOSURE_HEADING in disclosure_text,
        detail=(f"read={bool(read_result.readings)} strayed={strayed} "
                f"outside_refused={outside_refused}"))

    code_live = any(r.outcome == cr.LIVE for c, r in real_pin_resolutions
                    if c.locator == "code")
    markers_well_formed = all(r.marker.startswith("[stated — code:")
                              for r in read_result.readings if r.marker)
    c4_parts: Dict[str, Tuple[Optional[bool], str]] = {
        "code": (code_live and markers_well_formed,
                 "a pin from a real read re-opens in the real repository"),
        "local-file": (UNREACHABLE, "no on-disk kind is declared on this arm"),
        "linear": (UNREACHABLE, "needs a live authorization"),
    }
    c4 = Observation(
        "C4", green=code_live and markers_well_formed,
        detail=(f"real pins re-opened: {code_live}; well-formed: "
                f"{markers_well_formed}; sample="
                f"{read_result.readings[0].marker if read_result.readings else None}"),
        parts=c4_parts)

    return {"C1": c1, "C2": _observe_c2(), "C3": c3, "C4": c4}


@dataclass(frozen=True)
class ArmComparison:
    """One observable, as both arms answered it."""

    observable: str
    constructed: Optional[bool]
    real: Optional[bool]
    reason_not_comparable: str = ""

    @property
    def comparable(self) -> bool:
        return not self.reason_not_comparable and self.real is not None

    @property
    def agrees(self) -> bool:
        return self.comparable and self.constructed == self.real


def compare_arms(constructed, realside) -> Tuple[ArmComparison, ...]:
    """Per observable, what each arm said. Never averaged into one answer."""
    out = []
    for oid in OBSERVABLE_IDS:
        reason = REAL_ARM_NOT_COMPARABLE.get(oid, "")
        real_obs = realside.get(oid)
        if real_obs is None and not reason:
            reason = "this observable was not answered on the real arm"
        out.append(ArmComparison(
            observable=oid,
            constructed=constructed[oid].green,
            real=real_obs.green if real_obs is not None else None,
            reason_not_comparable=reason))
    return tuple(out)


def test_the_real_corpus_arm_reads_a_real_repository_through_the_cli(tmp_path,
                                                                     monkeypatch):
    """The production CLI runs against a REAL repository — a smoke test, not a walk.

    This docstring previously read "Both arms must agree — a disagreement is
    reported, never averaged", which is A2's validation-gate sentence quoted onto
    a test that computes no agreement and produces nothing comparable. A reader
    auditing "was that gate met?" by reading docstrings would have got a yes.

    What is actually established here: the CLI entry point completes against a
    real checkout, the approved declaration is read back off a real manifest
    cycle rather than reused from the object in hand, the durable store is
    reached, the rendered pin has the right shape, and nothing is written into
    the workspace. What is NOT established: any observable's answer against real
    data, and therefore any agreement between this arm and the constructed one.
    """
    import contextlib
    import io

    repo = _real_repository()
    if repo is None:
        # The skip reason states ONLY what is true. It previously claimed "the
        # read-out says so", and the read-out has no reference to this arm in
        # either direction — `render_readout` and `build_limits` never take it as
        # an input. A false sentence in a skip message is still a false sentence.
        pytest.skip("no real git repository reachable, so the production CLI was "
                    "not exercised against real data in this run")

    monkeypatch.setenv("RP_STATE_DIR", str(tmp_path / "rp"))
    sid = "s14-real"

    # The research file lives INSIDE tmp_path — see the section note.
    draft = tmp_path / "Thoughts" / "s14real-20260910000000_RESEARCH.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# draft\n", encoding="utf-8")

    record = spk.build_record(
        spk.Selection(bounds=(("code", spk.Bound(selectors=(str(repo),))),)),
        route=spk.ROUTE_NINJA)

    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": str(draft),
                    "caller_skill": "/research",
                    "user_approved_scope": True,
                    "scope_record": json.loads(record.to_json())},
                   cycle_id="default")

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        rc = dr.main(["read", sid, str(draft),
                      "--projects-root", str(tmp_path),
                      "--query", "how is a declared source admitted?",
                      "--json"])
    assert rc == 0
    payload = json.loads(buffer.getvalue())

    # A READING IS REQUIRED, not "a reading or a refusal". The looser form passed
    # on a refusals-only run, and the marker loop beneath it then iterated zero
    # times — so the one assertion about output content was skippable by the
    # data, which is the shape of vacuity this module exists to refuse. A
    # declared repository that produced no reading at all is a finding, not a
    # pass.
    assert payload["readings"], (
        "the production entry point read nothing from a real declared "
        f"repository; refusals were {payload['refusals']}")
    for reading in payload["readings"]:
        assert reading["marker"].startswith("[stated — code:"), reading["marker"]

    # Read-only, asserted where it can actually fail. The CLI files its admission
    # record under the RESEARCH FILE's slug family, so pinning `--research-file`
    # inside tmp_path is what keeps the record out of the workspace. That is the
    # property worth checking: the record landed HERE, not there.
    #
    # (The first version of this line read `assert not list(...) or True`, which
    # is true for every input. An always-true assertion in the read-only guard of
    # a slice whose write-target cell is `—` is worth recording rather than
    # quietly replacing.)
    workspace_thoughts = Path.home() / "repos" / "Projects" / "Thoughts"
    if workspace_thoughts.is_dir():
        strays = [p for p in workspace_thoughts.glob("*_ADMISSION_*.md")]
        assert not strays, (
            f"this arm wrote admission records into the workspace: {strays}")


def test_the_cli_arm_reaches_two_seams_the_library_call_cannot(tmp_path):
    """Named because they are exercised but deliberately NOT matrixed.

    `_resolve_scope_from_cycle` and `durable_store_for` are reached THROUGH L1
    rather than beside it, so neutering them would redden L1 and tell us nothing
    L1's own arm does not. They are seams, not links.

    ASSERTED BEHAVIOURALLY. This test previously discharged itself with
    `assert 'dr.main(["read"' in source` — a source-text match, which is the
    text-anchored form this module denounces in its own docstring and which
    S13-obs8 recorded three instances of. It passed on a module that made the
    call and threw the result away, and it would have reddened on a rename that
    changed nothing. Both seams are now DRIVEN and their effects observed.
    """
    # Seam 1 — `_resolve_scope_from_cycle` refuses a cycle carrying no approved
    # declaration, by raising rather than by returning something empty.
    monkey = pytest.MonkeyPatch()
    try:
        monkey.setenv("RP_STATE_DIR", str(tmp_path / "rp"))
        draft = tmp_path / "Thoughts" / "seam-20260910000000_RESEARCH.md"
        draft.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text("# draft\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            dr._resolve_scope_from_cycle("seam-no-declaration", str(draft))
    finally:
        monkey.undo()

    # Seam 2 — `durable_store_for` files a record under the research file's own
    # slug family when the name carries one, and falls back otherwise. Asserted
    # on the RETURNED STORE, not on the source text that calls it.
    named = dr.durable_store_for(str(tmp_path / "Thoughts"
                                     / "seam-20260910000000_RESEARCH.md"))
    assert getattr(named, "slug", "") == "seam", getattr(named, "slug", None)

    # The fallback case needs a name the slug grammar CANNOT read. `notes.md`
    # does not qualify — a bare lowercase-kebab stem IS a legal slug, and this
    # control asserted otherwise until the test said so. An uppercase stem is
    # refused by the grammar's `[a-z0-9-]` class, so it genuinely falls back.
    # It does not return an EMPTY slug either — it returns the shared default
    # store, which carries its own. The property worth asserting is therefore not
    # "no slug" but "not a slug manufactured from this name": a confidently wrong
    # topic slug is worse than an obviously generic shared one.
    fallback = dr.durable_store_for(str(tmp_path / "Notes.md"))
    fallback_slug = getattr(fallback, "slug", "")
    assert fallback_slug != "seam"
    assert fallback_slug.lower() not in ("notes",), (
        "a name the slug grammar cannot read manufactured a topic slug from it; "
        f"got {fallback_slug!r}")
    assert fallback_slug == getattr(dr.durable_store_for(str(tmp_path / "Other.md")),
                                    "slug", ""), (
        "two unreadable names should land in the SAME shared fallback store")


# =========================================================================== #
# The rendered read-out, as an artifact
# =========================================================================== #


def test_the_readout_renders(_walk_env, capsys):
    """Renders the record and prints it, so a run leaves the artifact behind."""
    _root, _corpus, baseline, observed, _real, _realside, _cmp = _walk_env
    dispositions = _all_dispositions(baseline, observed)
    text = render_readout(dispositions,
                          build_limits(dispositions, baseline, observed),
                          None, comparisons=_cmp)
    assert "# S14" in text
    assert "What this verification could not demonstrate at all" in text
    with capsys.disabled():
        print("\n" + text + "\n")

"""A declared code base, read — through the port, bounded, and by relevance.

code-source-driver-bounded-read S1 / action A8.

WHAT THIS SUITE HAS TO PROVE THAT NO EARLIER ONE COULD
------------------------------------------------------
The adapter this exercises shipped four slices ago and was constructed only in
tests. Everything about it was green while nothing in production opened a
repository, which is exactly why a suite here has to be built against its own
vacuity rather than merely against the feature:

* **Divergence is the only thing that distinguishes reading THROUGH the port from
  reading beside it.** For an in-scope, within-budget file the admitted bytes are
  byte-identical to the raw bytes (`truncate_over_budget=False` for `code`), so no
  output-shaped assertion can tell the two apart. Stubbing `admit()` to return
  altered content can.
* **Every bound assertion carries a NEGATIVE CONTROL.** A bound that is asserted
  only in the firing direction proves nothing about whether it fires *because* of
  the bound: the same assertion passes against a run that admitted nothing for an
  unrelated reason. So each bound is shown firing AND shown not firing when the
  bound is lifted, on the same inputs.
* **A pin is checked against the repository, not against itself.** The revision in
  the pin is compared with what `git rev-parse HEAD` actually returns in a real
  `git init` repo. A fixture that asserted the pin equals the pin would have gone
  on passing through every slice in which nothing was ever pinned.

The real-repo fixture is slow and environment-dependent. That is accepted: it is
the only assertion in this file that cannot pass while the adapter is unreached.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
sys.path.insert(0, str(CONFIG_DIR / "skills"))
sys.path.insert(0, str(HOOKS_DIR))


def _load_by_path(name, path):
    """Load a hooks module BY PATH under a name unique to this suite.

    The bare name would resolve to whichever tree imported it first in a shared
    pytest process — which is how a test comes to measure a different config tree
    than the one it names.
    """
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rp = _load_by_path("_code_admission_research_pipeline",
                   HOOKS_DIR / "research_pipeline.py")
fce = _load_by_path("_code_admission_factcheck_engine",
                    HOOKS_DIR / "_factcheck_engine.py")

from research import admission_record as arec                 # noqa: E402
from research import declared_read as dr                      # noqa: E402
from research import internal_kb as ikb                       # noqa: E402
from research import kind_reachability as kr                  # noqa: E402
from research import scope_record as srec                     # noqa: E402
from research import source_picker as spk                     # noqa: E402
from research import source_port as sp                        # noqa: E402
from research.adapters.code_base import CodeBaseAdapter       # noqa: E402
from research.adapters.document_folder import DocumentFolderAdapter    # noqa: E402
from research.adapters.knowledge_library import KnowledgeLibraryAdapter  # noqa: E402

#: The `scope_record` the reachability module itself reads — loaded BY PATH under
#: a root-unique name, so it is deliberately NOT the bare-name `srec` above. A
#: control that moves the registry must patch THIS object; patching `srec` leaves
#: the module's own view untouched and the control passes while proving nothing.
kr_srec = kr.load_scope_record()


def _port():
    return sp.AdmissionPort(arec.InMemoryAdmissionRecordStore())


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A real git repository with one commit. No fake, no stub, no canned rev."""
    root = (tmp_path / "widget-service").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    (root / "auth.py").write_text("def login():\n    return True\n")
    (root / "billing.py").write_text("def charge():\n    return 1\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "first")
    return root


# =========================================================================== #
# The read happens, through the port, against a real repository
# =========================================================================== #

def test_a_declared_repository_is_actually_read_and_reaches_the_body(repo):
    """C1 + C3 — the defect this slice exists to close, at its own altitude.

    Before the driver, this could not pass at all: no production path enumerated
    a declared repository, so the reader raised on the `code` kind and the body
    was empty.
    """
    result = dr.read_declared_sources(srec.code_scope([repo]), projects_root=repo)

    read = sorted(Path(r.item).name for r in result.readings)
    assert read == ["auth.py", "billing.py"], (
        f"the declared repository was not read; refusals were "
        f"{[r.render() for r in result.refusals]}")

    body = dr.synthesize_from_admissions(result)
    assert "def login()" in body and "def charge()" in body, (
        "the findings body carries nothing drawn from the repository")


def test_the_pin_names_the_repository_and_the_commit_that_was_actually_read(repo):
    """C4 — checked against `git rev-parse HEAD`, never against the pin itself."""
    head = _git(repo, "rev-parse", "HEAD")
    result = dr.read_declared_sources(srec.code_scope([repo]), projects_root=repo)

    markers = {Path(r.item).name: r.marker for r in result.readings}
    assert markers, "nothing was admitted, so there is no pin to check"
    marker = markers["auth.py"]

    assert marker.startswith("[stated — code:"), marker
    assert f"@{head}:" in marker, (
        f"the pin does not carry the revision the repository is actually at "
        f"({head}); marker was {marker}")
    assert "widget-service" in marker, (
        "the pin does not name the repository it was read from")
    assert "auth.py:1-2" in marker, (
        "the pin does not address the lines that were read")


def test_one_declaration_spans_code_and_knowledge_library_end_to_end(repo, tmp_path):
    """Q26's headline capability, asserted at the altitude the promise is made at.

    The locked Desired Outcome promises "any combination of sources" in ONE run.
    Until 2026-09-11 no route-scoped `build_record` call could assemble a record
    spanning the two route groups, so this test could not have been written.

    **It exists because the widening first shipped WITHOUT it.** An independent
    check found the whole change resting on `selectable_keys` equalities — which
    assert selectability, and neither assembly nor reading — with no test anywhere
    in the suite building a cross-group record or driving one through the reader.
    A later edit that kept both classes selectable while breaking the READ (say, by
    adding route conditioning to `_adapter_for`) would have passed everything else
    here. Both halves are therefore asserted separately below.
    """
    library = (tmp_path / "<KL>" / "Dev").resolve()
    library.mkdir(parents=True)
    (library / "refund-policy.md").write_text("refunds are issued within 30 days\n")

    # HALF ONE — ASSEMBLY. One declaration, both groups, on a real route. This is
    # the call that raised SourcePickerError on all five routes before Q26.
    record = spk.build_record(
        spk.Selection(bounds=(
            ("code", spk.Bound(selectors=(str(repo),))),
            ("knowledge_library", spk.Bound(selectors=(str(library),))),
        )),
        route=spk.ROUTE_NINJA)
    assert {s.kind for s in record.sources} == {"code", "knowledge_library"}, (
        "the assembled declaration does not carry both groups")

    # HALF TWO — THE READ. Both kinds come back through the port, in one run.
    # Declarable without readable would satisfy the letter of the promise and
    # none of its intent, which is why this half is not inferred from the first.
    result = dr.read_declared_sources(record, projects_root=tmp_path.resolve())

    names = sorted(Path(r.item).name for r in result.readings)
    assert "auth.py" in names, (
        f"the declared repository was not read in a cross-group run; refusals "
        f"were {[r.render() for r in result.refusals]}")
    assert "refund-policy.md" in names, (
        f"the declared library was not read in a cross-group run; refusals were "
        f"{[r.render() for r in result.refusals]}")

    # …and each is pinned in its OWN kind's form, so neither was read through the
    # other's adapter.
    markers = {Path(r.item).name: r.marker for r in result.readings}
    assert markers["auth.py"].startswith("[stated — code:"), markers["auth.py"]
    assert markers["refund-policy.md"].startswith("[stated — local-file:"), \
        markers["refund-policy.md"]

    body = dr.synthesize_from_admissions(result)
    assert "def login()" in body and "refunds are issued" in body, (
        "the findings body does not carry content from both declared sources")


def test_the_internal_kb_route_still_refuses_the_other_direction(repo):
    """The half Q26 deliberately did NOT widen, asserted so it cannot drift open.

    Admitting `code`/`web`/`linear` onto the Internal-KB route is unsafe as
    specified: that route opens no manifest cycle, so a declared `web` source
    would be read unbounded via `web_scope()`'s unscoped fallback. The widening
    is one-directional by design, and this is what holds it that way.
    """
    for key, selector in (("code", str(repo)), ("web", "https://example.com")):
        with pytest.raises(spk.SourcePickerError, match="never be opened"):
            spk.build_record(
                spk.Selection(bounds=((key, spk.Bound(selectors=(selector,))),)),
                route=spk.ROUTE_INTERNAL_KB)


def test_the_read_flows_through_the_port_not_beside_it(repo):
    """THE DIVERGENCE TEST — the only assertion that can tell the two apart.

    An in-scope, within-budget file admits byte-identical content, so a reader
    that opened the file itself and a reader that took the port's bytes produce
    the SAME body. Alter what the port returns and only one of them changes.
    """
    sentinel = "PORT-ALTERED-CODE-9c41"

    class _AlteringPort:
        def admit(self, item, scope, adapter, run_id, **kw):
            real = _port().admit(item, scope, adapter, run_id=run_id, **kw)
            if not real.admitted:
                return real
            return sp.AdmissionResult(item=real.item, pin=real.pin,
                                      evidence_path=real.evidence_path,
                                      pin_str=real.pin_str, content=sentinel)

    result = dr.read_declared_sources(srec.code_scope([repo]), projects_root=repo,
                                      port=_AlteringPort())
    body = dr.synthesize_from_admissions(result)
    assert sentinel in body, (
        "the synthesis does not reflect what the port returned — the read flows "
        "BESIDE the port rather than through it")
    assert "def login()" not in body, (
        "the synthesis carries the file's real bytes, so it re-read the file")


def test_a_path_outside_the_declaration_comes_back_refused_by_name(tmp_path):
    """C5 — a refusal reaches the person naming the thing and the reason.

    A silent omission would read exactly like the source having had nothing to
    say, which is the harm the whole slice is written against.
    """
    root = tmp_path.resolve()
    declared = root / "declared"
    declared.mkdir()
    (declared / "in.py").write_text("x = 1\n")
    outside = root / "elsewhere"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET = 1\n")

    scope = srec.code_scope([declared])
    adapter = CodeBaseAdapter()
    item = sp.SourceItem(item_id=str(outside / "secret.py"), kind=srec.KIND_CODE,
                         target=str(outside / "secret.py"))
    outcome = _port().admit(item, scope, adapter, run_id="r")

    assert not outcome.admitted, "an undeclared path was admitted"
    assert outcome.degradation.obligation == sp.OBLIGATION_DECLARED_SCOPE

    body = dr.synthesize_from_admissions(dr.DeclaredReadResult(
        refusals=(dr.RefusedRead(item=str(outside / "secret.py"),
                                 obligation=outcome.degradation.obligation,
                                 reason=outcome.degradation.reason),)))
    assert "secret.py" in body, "the refused path is not named to the person"
    assert "refused" in body


def test_a_declared_claude_md_is_refused_by_name_rather_than_skipped(tmp_path):
    """The prohibition survives the new arm: refused, not silently dropped."""
    root = (tmp_path / "r").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    (root / "keep.py").write_text("x = 1\n")
    (root / "CLAUDE.md").write_text("# a pointer, never a source\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c")

    result = dr.read_declared_sources(srec.code_scope([root]), projects_root=root)

    assert [Path(r.item).name for r in result.readings] == ["keep.py"]
    refused = [r for r in result.refusals if r.item.endswith("CLAUDE.md")]
    assert refused, "the CLAUDE.md was silently skipped rather than refused"
    assert refused[0].obligation == sp.OBLIGATION_SOURCE_IDENTITY
    assert "CLAUDE.md" in dr.synthesize_from_admissions(result)


# =========================================================================== #
# The bounds — each shown firing, and each shown NOT firing when lifted
# =========================================================================== #

class _CannedAdapter(sp.SourceAdapter):
    """Yields exactly the items it is given, reads exactly the bytes it is given.

    Deliberately not `FakeAdapter`: these tests need to vary size and depth per
    item over a scope the real containment check admits, and a canned read keeps
    the bound under test the only variable.
    """

    kind = srec.KIND_CODE
    can_reopen_without_credentials = False   # forces the `captured` branch, no git

    def __init__(self, items, body="x"):
        self._items = tuple(items)
        self._body = body
        self.yielded = []

    def enumerate_within(self, scope):
        for item in self._items:
            self.yielded.append(item.item_id)
            yield item

    def read_with_pin(self, item):
        from research.locator_grammar import code_locator
        return sp.ReadResult(source_id="repo", version="deadbeef",
                             locator=code_locator(Path(item.target).name, "1-1"),
                             content=self._body)


def _canned_items(root, n, *, depth=0):
    return [sp.SourceItem(item_id=str(root / f"f{i}.py"), kind=srec.KIND_CODE,
                          target=str(root / f"f{i}.py"), depth=depth)
            for i in range(n)]


def test_the_item_count_bound_fires_and_stops_enumeration(tmp_path):
    """MAX_ITEMS, firing — and the adapter's generator is abandoned at it."""
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, sp.MAX_ITEMS + 5))

    produced = list(sp.bounded_items(adapter, srec.code_scope([root])))

    degradations = [p for p in produced if isinstance(p, sp.Degradation)]
    assert len(degradations) == 1 and "MAX_ITEMS" in degradations[0].reason
    assert len(adapter.yielded) == sp.MAX_ITEMS + 1, (
        "the stream consumed past the bound — the ceiling is the port's only if "
        "it stops consuming AT the item that reaches it")


def test_control_the_item_count_bound_does_not_fire_below_it(tmp_path):
    """MAX_ITEMS, NOT firing on the same shape of input.

    Without this the assertion above is satisfied by any run that degraded for
    any reason at all.
    """
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, sp.MAX_ITEMS))
    produced = list(sp.bounded_items(adapter, srec.code_scope([root])))
    assert not [p for p in produced if isinstance(p, sp.Degradation)], (
        "a run under the ceiling degraded, so the firing test above is not "
        "observing the ceiling")
    assert len(produced) == sp.MAX_ITEMS


def test_the_depth_bound_fires_and_skips_only_that_item(tmp_path):
    """MAX_DEPTH, firing — and it SKIPS rather than terminating."""
    root = tmp_path.resolve()
    shallow = _canned_items(root, 1, depth=0)
    deep = [sp.SourceItem(item_id=str(root / "deep.py"), kind=srec.KIND_CODE,
                          target=str(root / "deep.py"), depth=sp.MAX_DEPTH + 1)]
    after = [sp.SourceItem(item_id=str(root / "after.py"), kind=srec.KIND_CODE,
                           target=str(root / "after.py"), depth=0)]
    adapter = _CannedAdapter(shallow + deep + after)

    produced = list(sp.bounded_items(adapter, srec.code_scope([root])))
    degradations = [p for p in produced if isinstance(p, sp.Degradation)]
    items = [p for p in produced if isinstance(p, sp.SourceItem)]

    assert len(degradations) == 1 and "MAX_DEPTH" in degradations[0].reason
    assert [Path(i.target).name for i in items] == ["f0.py", "after.py"], (
        "a too-deep item terminated enumeration; depth is a property of the "
        "item, not of the run")


def test_control_the_depth_bound_does_not_fire_at_the_limit(tmp_path):
    """MAX_DEPTH, NOT firing at exactly the limit."""
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, 2, depth=sp.MAX_DEPTH))
    produced = list(sp.bounded_items(adapter, srec.code_scope([root])))
    assert not [p for p in produced if isinstance(p, sp.Degradation)]


def test_the_per_item_budget_refuses_an_oversize_file_whole(tmp_path):
    """MAX_ITEM_BYTES, firing — refused WHOLE, because a truncated `path:lines`
    pin would address more than was read."""
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, 1),
                             body="y" * (sp.MAX_ITEM_BYTES + 1))
    outcome = _port().admit(_canned_items(root, 1)[0], srec.code_scope([root]),
                            adapter, run_id="r")
    assert not outcome.admitted
    assert outcome.degradation.obligation == sp.OBLIGATION_READ_BUDGET
    assert "refused whole rather than truncated" in outcome.degradation.reason


def test_control_the_per_item_budget_admits_a_file_just_under_it(tmp_path):
    """MAX_ITEM_BYTES, NOT firing one byte under."""
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, 1),
                             body="y" * sp.MAX_ITEM_BYTES)
    outcome = _port().admit(_canned_items(root, 1)[0], srec.code_scope([root]),
                            adapter, run_id="r")
    assert outcome.admitted, outcome.degradation


def test_the_aggregate_budget_stops_the_run_and_says_so(tmp_path):
    """MAX_RUN_BYTES, firing through the real reader — the bound that was
    shipped-but-unreachable, and the report SAYS the run stopped at it."""
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, 6),
                             body="z" * (sp.MAX_RUN_BYTES // 4))

    port = _port()
    budget = sp.run_budget(kind=srec.KIND_CODE)
    scope = srec.code_scope([root])
    outcomes = [port.admit(i, scope, adapter, run_id="r", budget=budget)
                for i in _canned_items(root, 6)]

    admitted = [o for o in outcomes if o.admitted]
    refused = [o for o in outcomes if not o.admitted]
    assert len(admitted) == 4, (
        f"the aggregate did not stop the run where the arithmetic says it must; "
        f"admitted {len(admitted)} of 6")
    assert refused, "nothing was refused, so the aggregate never fired"
    assert refused[0].degradation.obligation == sp.OBLIGATION_READ_BUDGET
    assert "MAX_RUN_BYTES" in refused[0].degradation.reason

    body = dr.synthesize_from_admissions(dr.DeclaredReadResult(
        refusals=tuple(dr.RefusedRead(item=o.item,
                                      obligation=o.degradation.obligation,
                                      reason=o.degradation.reason)
                       for o in refused)))
    assert "aggregate budget" in body, (
        "a run that stopped at its ceiling did not say so in the report; a "
        "shorter report is indistinguishable from a smaller source")


def test_control_the_aggregate_does_not_fire_when_no_budget_is_supplied(tmp_path):
    """THE CONTROL THE PLAN NAMES: with `budget=None` the same run admits
    everything.

    This is the shipped defect, reproduced deliberately. The aggregate is inert
    unless a caller supplies one (`source_port.py`'s `if budget is not None`
    guard), which is why the read loop passing no budget left `MAX_RUN_BYTES`
    applying to nothing. If this control ever starts refusing, the firing test
    above has stopped observing the budget and is observing something else.
    """
    root = tmp_path.resolve()
    adapter = _CannedAdapter(_canned_items(root, 6),
                             body="z" * (sp.MAX_RUN_BYTES // 4))
    port = _port()
    scope = srec.code_scope([root])
    outcomes = [port.admit(i, scope, adapter, run_id="r", budget=None)
                for i in _canned_items(root, 6)]
    assert all(o.admitted for o in outcomes), (
        "the aggregate fired with no budget supplied — the firing test above is "
        "not observing the aggregate")


def test_the_reader_supplies_the_aggregate_so_the_bound_reaches_production(tmp_path):
    """The wiring itself: the production reader passes a budget, so a declared
    source larger than the run ceiling stops and reports.

    Asserted through `read_declared_sources`, not by inspecting a call — a test
    that read the source would pass against a call that passed the wrong thing.
    """
    root = tmp_path.resolve()
    lib = root / "Library"
    lib.mkdir()
    chunk = "q" * (sp.MAX_RUN_BYTES // 4)
    for i in range(6):
        (lib / f"doc{i}.md").write_text(chunk)

    result = dr.read_declared_sources(srec.knowledge_library_scope([lib]),
                                      projects_root=root)

    assert len(result.readings) < 6, (
        "every file was admitted, so the run ceiling still does not apply on "
        "the production read path")
    budget_refusals = [r for r in result.refusals
                       if r.obligation == sp.OBLIGATION_READ_BUDGET]
    assert budget_refusals, "the run stopped silently rather than saying so"
    assert "MAX_RUN_BYTES" in budget_refusals[0].reason


def test_the_aggregate_reaches_the_code_arm_too_on_the_production_path(tmp_path):
    """The same wiring, on the arm this slice ADDS rather than the one it inherits.

    Asserted separately on purpose. The budget is constructed once per run and is
    kind-independent, so this is very likely to hold given the test above — but
    "very likely" is how `code` came to be offered for four slices with nothing
    reading it, and the claim being made is about a declared CODE base.
    """
    root = (tmp_path / "big-repo").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    for i in range(4):
        (root / f"mod{i}.py").write_text("q" * 65000)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c")

    result = dr.read_declared_sources(srec.code_scope([root]), projects_root=root)

    assert 0 < len(result.readings) < 4, (
        f"the run ceiling did not apply to a declared code base; "
        f"admitted {len(result.readings)} of 4")
    budget_refusals = [r for r in result.refusals
                       if r.obligation == sp.OBLIGATION_READ_BUDGET]
    assert budget_refusals, "the code run stopped silently rather than saying so"
    assert "MAX_RUN_BYTES" in budget_refusals[0].reason
    assert "aggregate budget" in dr.synthesize_from_admissions(result)


def test_the_run_STOPS_at_its_ceiling_instead_of_probing_every_remaining_file(tmp_path):
    """The difference between refusing and stopping, which is not cosmetic.

    `admit()` READS an item before it consults the aggregate, so a reader that
    keeps going past exhaustion re-reads every remaining candidate in full and
    emits one refusal line per unread file. This asserts the run stops: the
    number of items the adapter was actually asked to READ must be far below the
    number of candidates, and the report must carry ONE summary refusal naming
    the bound rather than one line per unread file.
    """
    root = tmp_path.resolve()
    lib = root / "Library"
    lib.mkdir()
    for i in range(40):
        (lib / f"doc{i:02d}.md").write_text("q" * _FILL)

    reads: list = []
    real = KnowledgeLibraryAdapter(projects_root=root)

    class _CountingAdapter(sp.SourceAdapter):
        """The real adapter, counting how many items it is asked to READ.

        Counting reads rather than refusals is the point: a refusal is cheap to
        emit and expensive to produce, and it is the production side we care
        about here.
        """

        kind = srec.KIND_KNOWLEDGE_LIBRARY
        can_reopen_without_credentials = real.can_reopen_without_credentials

        def enumerate_within(self, scope):
            return real.enumerate_within(scope)

        def read_with_pin(self, item):
            reads.append(item.target)
            return real.read_with_pin(item)

    # Monkeypatched rather than injected: widening the production signature with
    # an `adapters=` seam only a test uses would be a surface added for testing.
    original = dr._adapter_for
    dr._adapter_for = lambda kind, projects_root, **kw: _CountingAdapter()
    try:
        result = dr.read_declared_sources(
            srec.knowledge_library_scope([lib]), projects_root=root)
    finally:
        dr._adapter_for = original

    assert len(reads) <= 6, (
        f"the run read {len(reads)} of 40 files after its budget was gone — it "
        f"refused each one instead of stopping")
    stop = [r for r in result.refusals if "further declared item" in r.item]
    assert len(stop) == 1, (
        f"expected exactly one summary refusal naming the bound; got "
        f"{[r.item for r in result.refusals]}")
    assert "stopped" in stop[0].reason and "not read" in stop[0].reason
    # Exactly ONE per-file refusal is correct and expected: the first item that
    # did not fit is named individually, and everything after it is covered by
    # the single summary line. (The summary's own `item` ends with the first
    # unread path, so it must be excluded here or it counts as a per-file line.)
    per_file = [r for r in result.refusals
                if r.item.endswith(".md") and r not in stop]
    assert len(per_file) == 1, (
        f"{len(per_file)} per-file refusals reached the report — the run is "
        f"still probing every remaining candidate instead of stopping at the "
        f"first item that did not fit")


def test_one_oversized_file_does_NOT_stop_the_run(tmp_path):
    """The control that keeps the stop from being a blunt instrument.

    A single file over the PER-ITEM ceiling is refused whole and the run carries
    on — it has plenty of aggregate budget left. Stopping here instead would let
    one vendored blob truncate an entire piece of research, which is why the stop
    keys on the port's aggregate flag and not on the shared obligation.
    """
    root = tmp_path.resolve()
    lib = root / "Library"
    lib.mkdir()
    (lib / "aaa-huge.md").write_text("z" * (sp.MAX_ITEM_BYTES + 10))
    (lib / "bbb-small.md").write_text("alpha\n")
    (lib / "ccc-small.md").write_text("beta\n")

    result = dr.read_declared_sources(srec.knowledge_library_scope([lib]),
                                      projects_root=root)

    names = sorted(Path(r.item).name for r in result.readings)
    assert names == ["bbb-small.md", "ccc-small.md"], (
        f"an oversized file stopped the run; only {names} were read")
    refused = [r for r in result.refusals if r.item.endswith("aaa-huge.md")]
    assert refused and "refused whole" in refused[0].reason
    assert not [r for r in result.refusals if "further declared item" in r.item], (
        "the run reported stopping at its ceiling, but only ONE item was over "
        "its own per-item bound and the aggregate was never exhausted")


def test_the_item_ceiling_bounds_the_run_not_each_kind_separately(tmp_path):
    """`MAX_ITEMS` is defined as "items enumerated per run" — so two kinds share it.

    Asserted on the counter rather than by enumerating 2000 files twice: the
    property is that the count carries ACROSS calls, and a shared counter either
    does that or it does not.
    """
    root = tmp_path.resolve()
    a, b = root / "a", root / "b"
    a.mkdir(); b.mkdir()
    (a / "one.md").write_text("1\n")
    (b / "two.md").write_text("2\n")

    counter = sp.ItemCounter()
    adapter_a = DocumentFolderAdapter(projects_root=root)
    scope = srec.document_folder_scope([a, b])
    consumed = list(sp.bounded_items(adapter_a, scope, counter))
    assert counter.seen == len(consumed) == 2

    # A second call with the SAME counter continues the count rather than
    # restarting it — which is what makes the bound per-run.
    list(sp.bounded_items(adapter_a, scope, counter))
    assert counter.seen == 4, (
        f"the counter restarted at the second call ({counter.seen}), so "
        f"MAX_ITEMS bounds each kind separately rather than the run")

    # Omitting it keeps the single-adapter behaviour `run()` has always had.
    assert len(list(sp.bounded_items(adapter_a, scope))) == 2


# =========================================================================== #
# Relevance — what a bounded run spends its budget on
# =========================================================================== #

# Sized so the AGGREGATE is the binding bound and the per-item one is not: each
# file sits just under `MAX_ITEM_BYTES`, three fit inside `MAX_RUN_BYTES` and a
# fourth does not. Getting this wrong is not a cosmetic slip — files sized over
# the per-item ceiling are refused whole BEFORE the aggregate is consulted, and
# the test would then be observing the wrong bound entirely.
_FILL = 65000
assert _FILL < sp.MAX_ITEM_BYTES and 3 * _FILL <= sp.MAX_RUN_BYTES < 4 * _FILL


def _library_of(tmp_path, **files):
    root = tmp_path.resolve()
    lib = root / "Library"
    lib.mkdir()
    for name, prefix in files.items():
        (lib / name).write_text(prefix + "w" * (_FILL - len(prefix)))
    return root, lib


def test_the_budget_is_spent_on_what_bears_on_the_question(tmp_path):
    """C7 — the run has room for three of four, and relevance decides which three.

    Constructed so alphabetical order and relevance order DISAGREE: the matching
    file sorts LAST, so a run spending its budget by sort position drops exactly
    the file the person asked about. That is the defect this codebase already
    records as observed and fixed for the web class.
    """
    root, lib = _library_of(tmp_path, **{"aaa.md": "", "bbb.md": "",
                                         "ccc.md": "", "zoning-permits.md": ""})

    result = dr.read_declared_sources(
        srec.knowledge_library_scope([lib]), projects_root=root,
        query="what are the rules for zoning permits?")

    admitted = [Path(r.item).name for r in result.readings]
    assert "zoning-permits.md" in admitted, (
        f"the run spent its budget by sort position and dropped the file bearing "
        f"on the question; it admitted {admitted}")
    assert admitted[0] == "zoning-permits.md", (
        f"the matching file was admitted but not FIRST, so ordering is incidental "
        f"rather than by relevance; got {admitted}")
    assert len(admitted) == 3, admitted
    assert any(Path(r.item).name == "ccc.md" for r in result.refusals), (
        "the file that did not fit was dropped silently rather than refused")


def test_control_with_no_question_the_order_is_the_adapters_own(tmp_path):
    """The fallback, and the control that makes the test above non-vacuous.

    Same four files, same budget, only the question withdrawn. No terms means no
    reordering, so the adapter's own order decides and the matching file — which
    sorts last — is the one dropped. If this ever starts admitting
    `zoning-permits.md`, the test above is observing something other than
    relevance.
    """
    root, lib = _library_of(tmp_path, **{"aaa.md": "", "bbb.md": "",
                                         "ccc.md": "", "zoning-permits.md": ""})

    result = dr.read_declared_sources(srec.knowledge_library_scope([lib]),
                                      projects_root=root)
    admitted = [Path(r.item).name for r in result.readings]
    assert admitted == ["aaa.md", "bbb.md", "ccc.md"], (
        f"ordering changed with no question supplied, so the fallback is not the "
        f"adapter's own order; got {admitted}")


def test_relevance_looks_only_at_metadata_never_at_content(tmp_path):
    """A3's guard rail: no content pre-scan, so no read happens outside the port.

    Discriminating in BOTH directions. The terms appear in one file's PATH and in
    a different file's CONTENT, and the two are on opposite sides of the cut:

    * ranking by path  → `zoning-permits.md` admitted, `zzz.md` refused
    * ranking by content → `zzz.md` admitted, `zoning-permits.md` refused

    so neither outcome is reachable by accident, and a ranker that peeked at bytes
    fails here rather than passing quietly.
    """
    root, lib = _library_of(tmp_path, **{"aaa.md": "", "bbb.md": "",
                                         "zoning-permits.md": "",
                                         "zzz.md": "zoning permits everywhere\n"})

    result = dr.read_declared_sources(
        srec.knowledge_library_scope([lib]), projects_root=root,
        query="zoning permits")
    admitted = [Path(r.item).name for r in result.readings]
    assert admitted[0] == "zoning-permits.md", (
        f"a file whose CONTENT matches outranked one whose PATH matches — "
        f"ranking read content, which puts a read outside the port; got {admitted}")
    assert "zzz.md" not in admitted, admitted


def test_a_basename_match_outranks_a_directory_match(tmp_path):
    """The editorial rule, pinned so a change to it is a deliberate change."""
    terms = dr.relevance_terms("zoning permits")
    assert dr.relevance_score("/x/zoning/other.md", terms) < \
        dr.relevance_score("/x/other/zoning.md", terms)


def test_relevance_terms_drop_noise_and_keep_order():
    assert dr.relevance_terms("How does the AUTH flow work?") == ("auth", "flow", "work")
    assert dr.relevance_terms("") == ()
    assert dr.relevance_terms(None) == ()


# =========================================================================== #
# Dispatch — per kind, and never a silent skip
# =========================================================================== #

def test_two_declared_sources_of_one_kind_enumerate_once(tmp_path):
    """Dispatch is per KIND. Per declared SOURCE would read every file twice,
    because each adapter already iterates every selector its kind declared."""
    root = tmp_path.resolve()
    a, b = root / "a", root / "b"
    a.mkdir(); b.mkdir()
    (a / "one.md").write_text("1\n")
    (b / "two.md").write_text("2\n")

    scope = srec.ScopeRecord(sources=(
        srec.DeclaredSource(kind=srec.KIND_DOCUMENT_FOLDER, selectors=(str(a),)),
        srec.DeclaredSource(kind=srec.KIND_DOCUMENT_FOLDER, selectors=(str(b),)),
    ))
    result = dr.read_declared_sources(scope, projects_root=root)
    names = sorted(Path(r.item).name for r in result.readings)
    assert names == ["one.md", "two.md"], f"got {names} — a file was read twice"


def test_a_kind_driven_elsewhere_is_skipped_by_name_not_refused(tmp_path):
    """`web` is read by the fact-check engine, so this reader neither reads it nor
    tells a person it was refused — which would be false."""
    root = tmp_path.resolve()
    scope = srec.web_scope(["https://example.com/docs"])
    result = dr.read_declared_sources(scope, projects_root=root)
    assert result.readings == () and result.refusals == ()
    assert srec.KIND_WEB in dr.DRIVEN_ELSEWHERE


def test_a_kind_with_no_reader_fails_loudly_rather_than_being_skipped():
    """S8 SESSION 3 NOTE — the stand-in moved from `linear` to `confluence`.

    This test needs a kind with NO arm in `_adapter_for`, and `linear` gained one
    in Session 3, so it can no longer play that part — the same substitution this
    file already made once when `linear` replaced `code`. `confluence` is a
    catalogue row whose kind has no arm and no registration, which is what `linear`
    was when this was written.
    """
    with pytest.raises(ValueError, match="no reader for declared kind"):
        dr._adapter_for("confluence", Path("/tmp"))


def test_a_declared_linear_source_with_no_authorization_refuses_by_name():
    """`linear` now has an arm, so its loud failure has its OWN words.

    The loudness property this file guards is unchanged — a declared class is never
    silently skipped — but the reason differs: the reader exists and the run holds
    no credential, which is a different thing from there being no reader at all,
    and a person is told which.
    """
    with pytest.raises(dr.MissingAuthorization, match="no Linear authorization"):
        dr._adapter_for("linear", Path("/tmp"))


def test_internal_kb_still_answers_for_its_own_route(tmp_path):
    """The delegation is behaviour-preserving, including the route's run id."""
    assert ikb.read_declared_sources.__module__.endswith("internal_kb")
    assert ikb.synthesize_from_admissions is dr.synthesize_from_admissions
    assert ikb.RefusedRead is dr.RefusedRead
    assert ikb.InternalKBReadResult is dr.DeclaredReadResult

    root = tmp_path.resolve()
    lib = root / "Library"
    lib.mkdir()
    (lib / "n.md").write_text("alpha\n")

    result = ikb.read_declared_sources(srec.knowledge_library_scope([lib]),
                                       projects_root=root)
    assert [Path(r.item).name for r in result.readings] == ["n.md"]

    # THE RUN ID, ACTUALLY ASSERTED. Holding this route's own default is the only
    # thing the delegate wrapper exists to do (`internal_kb.read_declared_sources`
    # forwards everything else), so a docstring claiming it while checking nothing
    # would leave the wrapper's whole reason for existing untested. An earlier
    # version of this test built a store for exactly this and then never read it.
    store = arec.InMemoryAdmissionRecordStore()
    ikb.read_declared_sources(srec.knowledge_library_scope([lib]),
                              projects_root=root, store=store)
    assert set(store._runs) == {"internal-kb"}, (
        f"the route's records are keyed {set(store._runs)}, not 'internal-kb' — "
        f"the delegate stopped holding this route's own run id")

    # And the route-neutral module keeps its own default, so the two routes are
    # distinguishable in the record rather than merged under one name.
    store2 = arec.InMemoryAdmissionRecordStore()
    dr.read_declared_sources(srec.knowledge_library_scope([lib]),
                             projects_root=root, store=store2)
    assert set(store2._runs) == {"declared-read"}


# =========================================================================== #
# Offerability — A5, and its non-vacuity
# =========================================================================== #

def test_a_registered_class_with_no_reader_is_not_offered(monkeypatch):
    """C8 — the half that stops the NEXT class repeating this.

    Asserted against a monkeypatched fifth kind, never against `code`, which is
    driven by the time this lands and so could not discriminate.
    """
    monkeypatch.setattr(
        spk._scope, "REGISTERED_KINDS",
        tuple(spk._scope.REGISTERED_KINDS) + ("linear",))
    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "linear",
                        ("no adapter is wired for this kind yet", "a later slice"))

    e = spk.entry("linear")
    assert spk.is_selectable(e) is False
    assert spk.is_selectable(e, spk.ROUTE_NINJA) is False
    assert "linear" not in spk.selectable_keys()
    assert "linear" not in spk.selectable_keys(spk.ROUTE_NINJA)

    row = {r.key: r for r in spk.render_catalogue(spk.ROUTE_NINJA)}["linear"]
    assert row.selectable is False
    assert row.unavailable_slot, (
        "a non-selectable row must carry a reason; a refusal without one is the "
        "silent failure this flow exists to remove")


def test_control_the_same_class_IS_offered_when_no_exemption_is_recorded(monkeypatch):
    """THE NON-VACUITY CONTROL — without it the test above passes on a no-op.

    Same kind, same route, same registration; only the recorded exemption differs.

    **NAMED FOR WHAT IT ACTUALLY VARIES**, which is not what an earlier name
    claimed. `is_selectable` tests membership in `KNOWN_UNREACHABLE`; it does not
    derive driver presence, and NO reader is wired in either arm here. So this
    pair shows the input distinguishing *recorded-as-driverless* from *not
    recorded* — which is the real contract. Calling it "once it has a reader"
    would have described a variable neither arm moves, and would have implied the
    picker detects a driverless class on its own. It does not: the promotion-time
    `kind_reachability.check()` is what compels the record this input reads, and
    the test below is that half.

    S8 NOTE — the stand-in moved from `linear` to `confluence`. This control needs
    a class that is registered NOWHERE and carries NO exemption; S8 Session 1 gives
    `linear` both a registration and an exemption, so it can no longer play that
    part. `confluence` is a catalogue row whose kind is still unregistered, which
    is what `linear` was when this was written.
    """
    monkeypatch.setattr(
        spk._scope, "REGISTERED_KINDS",
        tuple(spk._scope.REGISTERED_KINDS) + ("confluence",))
    assert "confluence" not in kr.KNOWN_UNREACHABLE

    e = spk.entry("confluence")
    assert spk.is_selectable(e) is True
    assert "confluence" in spk.selectable_keys()


def test_the_other_half_of_the_pair_compels_the_record_this_input_reads(monkeypatch):
    """The composition, asserted — because neither half generalises alone.

    A kind registered with no driver AND no recorded exemption is offered by
    `is_selectable` (the control above). It cannot SHIP that way: the reachability
    check fails it at promotion, naming the kind, until someone either wires a
    driver or records an exemption — and recording the exemption is what makes
    `is_selectable` refuse it. Without this assertion the offerability claim rests
    on a mechanism this suite never exercises.

    S8 NOTE — stand-in moved from `linear` to `confluence`, for the reason given on
    the control above: S8 Session 1 gives `linear` a registration AND an exemption,
    and this test needs a class that has neither.
    """
    monkeypatch.setattr(
        spk._scope, "REGISTERED_KINDS",
        tuple(spk._scope.REGISTERED_KINDS) + ("confluence",))
    monkeypatch.setattr(
        kr_srec, "REGISTERED_KINDS", tuple(kr_srec.REGISTERED_KINDS) + ("confluence",))

    assert spk.is_selectable(spk.entry("confluence")) is True, (
        "the premise of this test — that the picker alone lets it through — no "
        "longer holds, so the composition it asserts has changed")
    problems = "\n".join(kr.check())
    assert "confluence" in problems and "no production driver" in problems, (
        "a registered kind with no driver and no recorded exemption passed the "
        "check that is supposed to compel the record `is_selectable` reads")


def test_the_shipped_catalogue_is_unchanged_on_the_day_this_lands():
    """The Guiding Policy's ordering requirement, asserted rather than assumed.

    Driver first means the offerability input is a NO-OP on the shipped
    catalogue. Landing it first would have removed `code` from three routes with
    no reason slot to render — the silent refusal this codebase already paid for
    once.

    S8 NOTE (Session 1) — the mapping gained a `linear` entry, so this asserted the
    offered set was byte-identical to before while that entry stood.

    S8 NOTE (Session 3) — the entry is GONE, deleted in the driver's own commit
    because `check()` fails a driven kind that still carries one. So the offered set
    is no longer unchanged: `linear` now joins the Ninja tuple, and that is the
    slice's point rather than a regression. What this test is FOR survives intact
    and is what it still asserts — that the offered set moves ONLY when a driver
    lands, never ahead of one. The non-vacuity control below is re-pointed to prove
    the same dependency from the other side.
    """
    assert set(kr.KNOWN_UNREACHABLE) == set(), (
        "the mapping should be empty once Session 3 wires the Linear driver; an "
        "unexpected entry means some other class went driverless")

    # CHANGED by S8 Session 3, and only because a driver now exists.
    #
    # CHANGED AGAIN by Q26 (2026-09-11) — and this one is the cleanest case this
    # test has ever had for its own rule. The two S7 classes join Ninja because
    # their driver ALREADY existed and had since S7: `declared_read._adapter_for`
    # dispatches them by kind with no route conditioning. So the offered set moved
    # strictly AFTER the driver, by a wider margin than any prior change — the
    # route set had simply been holding the offer back behind a reader that was
    # already there. The rule this test is for is unviolated.
    assert spk.selectable_keys(spk.ROUTE_NINJA) == (
        "code", "web", "knowledge_library", "document_folder", "linear")
    # Internal-KB is UNCHANGED: `ROUTE_CLASS_RESTRICTION` still holds its list to
    # the two internal classes, so its `internal-only` tier stays true.
    assert spk.selectable_keys(spk.ROUTE_INTERNAL_KB) == (
        "knowledge_library", "document_folder")

    # Non-vacuity: re-record an exemption and `linear` disappears again — so the
    # offer is held in place by the recorded driver state, not by the route set
    # alone. Same dependency the Session-1 version asserted, from the other side.
    saved = dict(kr.KNOWN_UNREACHABLE)
    try:
        kr.KNOWN_UNREACHABLE["linear"] = ("simulated", "this test")
        assert "linear" not in spk.selectable_keys(spk.ROUTE_NINJA), (
            "with an exemption recorded the kind must not be offered — which is "
            "what makes the record load-bearing rather than decorative")
    finally:
        kr.KNOWN_UNREACHABLE.clear()
        kr.KNOWN_UNREACHABLE.update(saved)


# =========================================================================== #
# The whole chain, from what a person ticked to a code-pinned finding
# =========================================================================== #

def test_a_run_declaring_code_produces_code_pinned_findings(tmp_path, monkeypatch):
    """A4's gate, end to end through every production link.

    Picker → approved `ScopeRecord` → `r0_intake` hoists it onto the manifest
    cycle → the cycle is read back → the reader drives the port → a finding
    carrying a pin that names the repository and the commit it was read at.

    **WHAT THIS DOES AND DOES NOT PROVE, because the difference is the whole
    subject of A4.** It proves the chain WORKS when invoked: every link is real
    production code and none is stubbed. It does NOT prove the chain IS invoked
    on a live `/research` run, because what invokes it is skill prose, and prose
    is followed rather than enforced — no `PreToolUse` hook covers `Read` for
    source paths, and design-A18, which would have gated it, is withdrawn. A test
    that claimed otherwise would be making exactly the over-claim this topic's
    plan forbids on four separate surfaces. The unproven half is a judgment step
    by construction; this asserts everything around it.
    """
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_path / "rp"))
    sid = "chain-sid"

    repo_root = (tmp_path / "payments-api").resolve()
    repo_root.mkdir()
    _git(repo_root, "init", "-q")
    _git(repo_root, "config", "user.email", "t@example.invalid")
    _git(repo_root, "config", "user.name", "T")
    (repo_root / "refunds.py").write_text("def issue_refund():\n    return 'ok'\n")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-qm", "first")
    head = _git(repo_root, "rev-parse", "HEAD")

    draft = tmp_path / "Thoughts" / "refunds_RESEARCH.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# draft\n")

    # 1 — the person ticks "your code" and gives a bound; the picker assembles it.
    record = spk.build_record(
        spk.Selection(bounds=(("code", spk.Bound(selectors=(str(repo_root),))),)),
        route=spk.ROUTE_NINJA)

    # 2 — the probe the approval bundle shows them.
    probes = spk.probe(record.sources[0])
    assert all(p.reachable for p in probes), probes

    # 3 — approval, and the record rides the intake payload onto the cycle.
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": str(draft),
                    "caller_skill": "/research",
                    "user_approved_scope": True,
                    "scope_record": json.loads(record.to_json())},
                   cycle_id="default")

    # 4 — read the declaration BACK off the cycle rather than reusing the object
    #     in hand. Reusing it would skip the persistence link entirely, which is
    #     the link that was carrying the record nobody consumed.
    _cid, cycle = fce._resolve_research_cycle_id(sid, str(draft))
    persisted = srec.ScopeRecord.from_dict(cycle["scope_record"])
    assert persisted.sources_of_kind(srec.KIND_CODE), (
        "the approved declaration did not survive onto the cycle")

    # 5 — the read the prose invokes.
    result = dr.read_declared_sources(persisted, projects_root=tmp_path,
                                      query="how are refunds issued?")

    assert [Path(r.item).name for r in result.readings] == ["refunds.py"], (
        f"the declared repository produced no findings; refusals were "
        f"{[r.render() for r in result.refusals]}")
    marker = result.readings[0].marker
    assert marker == f"[stated — code:payments-api@{head}:refunds.py:1-2]", marker

    body = dr.synthesize_from_admissions(result)
    assert "def issue_refund()" in body
    assert marker in body, "the finding reached the body without its pin"


# =========================================================================== #
# The edge cases the plan's Design Review assigns to this suite
#
# It names ten. Four — a path not inside a git repo, a repo with no commits,
# `git` absent, a dirty working tree — "already degrade correctly in the shipped
# adapter"; the other six are this suite's.
#
# THE COMMENT HERE PREVIOUSLY CLAIMED ALL FOUR OF THE FIRST GROUP WERE ASSERTED IN
# `test_source_admission_port.py`. Only two were: `test_a5_repo_with_no_commits_
# degrades` and `test_a5_dirty_working_tree_yields_plus_dirty_and_routes_to_
# captured`. Nothing anywhere asserted the not-a-repo case or the git-absent case
# — the plan says the adapter handles them, and the adapter does, but a claim of
# coverage that is only a claim is the exact shape this slice exists to remove. So
# the two are asserted below rather than the sentence being softened.
#
# Of this suite's own six: three are covered above (a path outside the
# declaration, a repository larger than the aggregate, two declared sources of one
# kind); the three after that are the rest.
# =========================================================================== #

def test_a_declared_path_outside_any_git_repo_degrades_by_name(tmp_path):
    """Edge case 1 of the shipped-adapter four — asserted nowhere until now.

    Pinned to the SPECIFIC refusal, not to "some git-shaped failure". A looser
    assertion passed even when the not-a-repo branch was removed, because the run
    then failed one step later at `rev-parse HEAD` and still refused — so it was
    reporting a property it was not testing. Caught by mutation, not by review.
    """
    plain = (tmp_path / "not-a-repo").resolve()
    plain.mkdir()
    (plain / "notes.py").write_text("x = 1\n")

    result = dr.read_declared_sources(srec.code_scope([plain]),
                                      projects_root=tmp_path)

    assert result.readings == (), "a file outside any git repository was pinned"
    assert result.refusals, "the file was dropped silently rather than refused"
    refusal = result.refusals[0]
    assert refusal.obligation == sp.OBLIGATION_READABLE, (
        f"the refusal came back as {refusal.obligation!r}; the not-a-repo case "
        f"resolves at `rev-parse --show-toplevel`, so a different obligation "
        f"means it is failing somewhere else and this test has stopped pinning "
        f"the case it names")
    assert "not a git repository" in refusal.reason, refusal.reason
    assert "notes.py" in dr.synthesize_from_admissions(result)


def test_git_being_absent_degrades_rather_than_crashing_the_run(tmp_path,
                                                                monkeypatch):
    """Edge case 3 of the shipped-adapter four — asserted nowhere until now.

    `git` is removed from PATH so the adapter's single subprocess site cannot
    spawn it. The run must come back with a named refusal, not an exception: an
    adapter failure that propagated would be the third outcome the port forbids.
    """
    repo_dir = (tmp_path / "repo").resolve()
    repo_dir.mkdir()
    (repo_dir / "a.py").write_text("x = 1\n")
    monkeypatch.setenv("PATH", str(tmp_path / "empty-bin"))

    result = dr.read_declared_sources(srec.code_scope([repo_dir]),
                                      projects_root=tmp_path)

    assert result.readings == ()
    assert result.refusals, "git being absent produced neither a read nor a refusal"
    assert result.refusals[0].obligation in (sp.OBLIGATION_READABLE,
                                             sp.OBLIGATION_SOURCE_IDENTITY)
    assert "a.py" in dr.synthesize_from_admissions(result)

def test_a_non_text_file_degrades_by_name_instead_of_reaching_the_body(tmp_path):
    """A binary file cannot be addressed by a line locator, so it is refused.

    The failure mode this guards is a garbled excerpt reaching the report with a
    `path:lines` pin over bytes no line range describes — a citation that resolves
    to something other than what it claims.
    """
    root = (tmp_path / "mixed").resolve()
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    (root / "readable.py").write_text("x = 1\n")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01binary")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "c")

    result = dr.read_declared_sources(srec.code_scope([root]), projects_root=root)

    assert [Path(r.item).name for r in result.readings] == ["readable.py"], (
        "a non-UTF-8 file reached the readings")
    refused = [r for r in result.refusals if r.item.endswith("logo.png")]
    assert refused, "the binary file was dropped silently rather than refused"
    assert refused[0].obligation == sp.OBLIGATION_READABLE
    assert "not UTF-8 text" in refused[0].reason
    assert "logo.png" in dr.synthesize_from_admissions(result), (
        "the person is not told which file could not be read")


def test_an_empty_selection_reads_nothing_and_claims_nothing(tmp_path):
    """Both halves of the empty case, because they fail differently.

    At the PICKER, an empty selection is a terminal outcome carrying no record at
    all — there is nothing to read because nothing was declared. At the READER, a
    record with no sources reads nothing and refuses nothing: silence here is
    correct, and is the one place in this suite where an empty result is the right
    answer rather than the defect.
    """
    outcome = spk.open_picker("a framed question", is_spike=False, selection=None)
    assert isinstance(outcome, spk.EmptySelection)
    assert not hasattr(outcome, "record"), (
        "a terminal outcome must carry no record, structurally")
    with pytest.raises(spk.SourcePickerError):
        spk.build_record(spk.Selection(bounds=()))

    result = dr.read_declared_sources(srec.ScopeRecord(sources=()),
                                      projects_root=tmp_path)
    assert result.readings == () and result.refusals == ()
    assert dr.synthesize_from_admissions(result) == ""


def test_a_code_only_declaration_leaves_the_web_read_unscoped(tmp_path, monkeypatch):
    """RECORDED, not fixed — pre-existing and deliberately unchanged.

    A record naming only `code` is discarded whole by the fact-check engine's
    `_resolve_web_admission`, which keeps a declaration only when it names a web
    source. So a run that declares a repository and nothing else has its
    verification-time web read bounded by a synthesized UNSCOPED declaration —
    i.e. not bounded at all.

    **That is the shipped behaviour and this slice does not touch it.** The reason
    it is right-as-designed is in that function's own comment: reading a code-only
    record as "web is bounded to nothing" would refuse every citation in a run
    whose person never mentioned the web. The reason it is worth pinning anyway is
    that the plan lists it as an edge case to RECORD, and a fact recorded only in
    prose is a fact nothing notices changing. If this fails, someone has altered
    the code-only case — which may well be right, but is a decision, not a
    refactor.

    Driven rather than read off the source: the record goes onto a real manifest
    cycle through `r0_intake` and the engine's own resolver is asked what it made
    of it. A source-text assertion would have passed against a function that had
    stopped being called.
    """
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_path / "rp"))
    sid = "code-only-sid"
    draft = tmp_path / "Thoughts" / "x_RESEARCH.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text("# draft\n")

    code_only = spk.build_record(
        spk.Selection(bounds=(("code", spk.Bound(selectors=(str(tmp_path),))),)),
        route=spk.ROUTE_NINJA)
    assert code_only.sources_of_kind(srec.KIND_CODE), "the premise is wrong"
    assert not code_only.sources_of_kind(srec.KIND_WEB), (
        "this record names a web source, so it is not the code-only case")

    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": str(draft),
                    "caller_skill": "/research",
                    "scope_record": json.loads(code_only.to_json())},
                   cycle_id="default")

    # S12/A4 widened this result to a named 3-field `WebAdmission`; the arity
    # changed, the assertion below did not.
    scope, _store, _declaration = fce._resolve_web_admission(
        sid, str(draft), str(tmp_path))
    assert scope is None, (
        "a code-only declaration now bounds the verification-time web read. That "
        "is a CHANGE to the recorded pre-existing behaviour, not a refactor — the "
        "engine used to discard a record naming no web source, leaving the web "
        "ingest under a synthesized unscoped declaration.")

    # THE POSITIVE CONTROL — without it the assertion above proves nothing.
    #
    # `_resolve_web_admission` initialises `scope = None` and wraps its whole body
    # in `except Exception: scope = None`. So `scope is None` is ALSO what you get
    # if RP_STATE_DIR was not honoured, if the cycle was not found, if the
    # admission modules failed to load, or if the function stopped being called at
    # all. It is the fail-safe value, which makes it the worst possible thing to
    # assert alone — the same defect this test's docstring accuses a source-text
    # assertion of having.
    #
    # Same session, same state dir, same resolver: a record that DOES name a web
    # source must come back non-None. If this half fails, the half above is
    # measuring breakage rather than the code-only discard.
    with_web = spk.build_record(
        spk.Selection(bounds=(("code", spk.Bound(selectors=(str(tmp_path),))),
                              ("web", spk.Bound(selectors=("https://example.com/docs",))))),
        route=spk.ROUTE_NINJA)
    draft2 = tmp_path / "Thoughts" / "y_RESEARCH.md"
    draft2.write_text("# draft\n")
    rp.cmd_advance("with-web-sid", "r0_intake",
                   {"research_file_path": str(draft2),
                    "caller_skill": "/research",
                    "scope_record": json.loads(with_web.to_json())},
                   cycle_id="default")
    web_scope, _, _ = fce._resolve_web_admission("with-web-sid", str(draft2),
                                                 str(tmp_path))
    assert web_scope is not None, (
        "the resolver returned None for a record that DOES name a web source — "
        "so it is failing rather than discarding, and the code-only assertion "
        "above is vacuous")
    assert web_scope.sources_of_kind(srec.KIND_WEB)

    # The same record DOES bound the read this slice added — which is the point:
    # the two reads are bounded by different things, and only one of them by this.
    result = dr.read_declared_sources(code_only, projects_root=tmp_path)
    assert result.readings or result.refusals, (
        "the code-only record bounded nothing at all, so the contrast above is "
        "vacuous")


def test_code_is_reachable_and_carries_no_stale_exemption():
    """A6 — the deletion that must ride the driver's own commit.

    `claude-verify` runs this check only on the deploy path, so a split commit
    passes every local gate and fails at promotion. This is the local assertion
    that does not wait for that.
    """
    assert kr.check() == [], kr.check()
    driven = set().union(*kr.derive_driver_kinds().values())
    assert srec.KIND_CODE in driven, (
        "`code` still derives as undriven, so the driver is not visible to the "
        "reachability check that gates promotion")

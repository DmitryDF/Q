"""Gate for research-source-adapters S4 — the source picker and its persistence.

Named for what it tests, following S3's `test_source_admission_port.py` and S2's
`test_citation_marker_registry.py` — deliberately NOT `test_s4_*`, because
`tests/research_pipeline/test_s4_path_extensions.py` already exists and belongs to a
different topic whose slices are also numbered `S<n>`.

WHAT THIS MODULE DOES NOT PROVE — stated rather than implied:

* **Nothing about what a person understands.** The plan's Gate 2 files three rows as
  AI judgment, and the central one — that a person actually read the declaration
  before approving it — no test can assert. What is asserted here is everything
  around it: that no record exists before the approval branch, that a spike or an
  empty selection produces none at all, and that nothing can be registered without
  one. The honest claim is *nothing is read without an approved declaration*, not
  that the person understood it.
* **Nothing about the Step-3 bundle's rendered prose.** The path-scoping rule — the
  declaration section renders only for a path that ran a selection step — lives in
  a rules file the AI composes from. There is no renderer to unit-test, which is
  why the plan files it as AI rather than Code. A round-4 checker caught an earlier
  draft claiming otherwise.
* **Nothing about reachability at read time.** `probe()` answers selection time. A
  path that exists at probe and vanishes before the read is the port's
  `OBLIGATION_READABLE` degradation, not this module's.
* **Calibration of the catalogue's wording.** Which slot a class uses and how its
  reason reads are editorial; the tests assert a reason EXISTS when unavailable and
  is absent when available, never that it reads well.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"
RESEARCH_DIR = SKILLS_DIR / "research"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))


def _load_by_path(name, path):
    """Import a module from an explicit path, bypassing the bare-name cache.

    Mirrors S3's helper, and for the same reason: other modules in this suite
    import harness modules from the LIVE tree, and the first importer wins the bare
    name. Without this, `from research import ...` resolves to whichever `research`
    package was imported first, so an assertion about the tree under test would
    depend on collection order. Registered under its unique name before exec, so
    `dataclasses` can resolve string annotations through `sys.modules`.
    """
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


srec = _load_by_path("_s4_scope_record", RESEARCH_DIR / "scope_record.py")
# The picker imports `scope_record` through the package, so it must see the SAME
# module object the tests monkeypatch — load it as part of a package whose
# `scope_record` is the one above.
sys.modules.setdefault("_s4_pkg", type(sys)("_s4_pkg"))
sys.modules["_s4_pkg"].__path__ = [str(RESEARCH_DIR)]     # type: ignore[attr-defined]
sys.modules["_s4_pkg.scope_record"] = srec
_picker_src = (RESEARCH_DIR / "source_picker.py").read_text(encoding="utf-8")
# Loaded into the same package so the picker's own `kind_reachability` import and
# the tests' handle are ONE module object — the driver input is read at call time,
# so two copies would let a test assert against a mapping the picker never reads.
kr = _load_by_path("_s4_pkg.kind_reachability", RESEARCH_DIR / "kind_reachability.py")
sys.modules["_s4_pkg.kind_reachability"] = kr
sp = _load_by_path("_s4_pkg.source_picker", RESEARCH_DIR / "source_picker.py")
rp = _load_by_path("_s4_research_pipeline", HOOKS_DIR / "research_pipeline.py")

# The six modules S3 shipped. S4 imports them and amends none.
S3_MODULES = (
    RESEARCH_DIR / "locator_grammar.py",
    RESEARCH_DIR / "scope_record.py",
    RESEARCH_DIR / "admission_record.py",
    RESEARCH_DIR / "source_port.py",
    RESEARCH_DIR / "adapters" / "__init__.py",
    RESEARCH_DIR / "adapters" / "code_base.py",
)


# --------------------------------------------------------------------------- #
# A1 — the catalogue, and the property that makes it drift-proof.
# --------------------------------------------------------------------------- #

def test_a1_only_registered_kinds_are_selectable():
    """Re-pointed by S6 (design-A29), and again by S7 (design-A21/A24/A31).

    This test is the proof of the derived-selectability mechanism, so what it
    asserts matters. Each slice registered its kinds in
    `scope_record.REGISTERED_KINDS` and made **no edit to `CATALOGUE`'s
    availability fields** — the rows flipped on their own. The classes below that
    are still unregistered are still unselectable, which is what keeps this from
    being a test that merely follows the code.

    **Availability derives from THREE inputs**, and this test covers one of them.
    The routeless call is the REGISTRATION half and answers exactly as it did
    before the route input existed for `code` and `web`. The ROUTE half is the
    test below. The DRIVER-PRESENCE half — whether a production driver exists for
    the class at all — is exercised in `test_code_admission.py`, on a monkeypatched
    kind rather than here: every class in the shipped catalogue with a registered
    kind also has a driver, so asserting it here would be vacuous.
    """
    rows = {r.key: r for r in sp.render_catalogue()}
    assert rows["code"].selectable is True
    assert rows["web"].selectable is True, "S6 registered the web kind"
    assert rows["knowledge_library"].selectable is True, "S7 registered the kind"
    assert rows["document_folder"].selectable is True, "S7 registered the kind"
    assert rows["linear"].selectable is True, (
        "S8 registered the kind and Session 3 wired its driver")
    for key in ("confluence", "jira"):
        assert rows[key].selectable is False, f"{key} has no registered kind yet"
    assert sp.selectable_keys() == ("code", "web", "knowledge_library",
                                    "document_folder", "linear")


def test_a1_selectability_is_per_route_not_global():
    """S7/G11 — the ROUTE input, and the regression it exists to prevent.

    The property: a class is offered where its route is WILLING to read it — the
    route set is a source-tier policy, per `source_picker.is_selectable`'s
    canonical note. Without it a person could tick a source the route would not
    open and get silence rather than refusal, which is the exact failure this
    whole flow exists to remove.

    *(This said "offered exactly where something reads it" until 2026-09-11 — the
    false rule that generated four rounds of review findings. Readers dispatch on
    kind, never on route, so "where something reads it" is the same everywhere.)*

    **What that property now yields on the external routes INVERTED on 2026-09-11
    (Q26), and the property itself did not change.** S7 wired the reader and this
    test read it as route-bound; it is not — `declared_read._adapter_for`
    dispatches by kind alone. So the two S7 classes are read on every route and
    are therefore offered on every route. The direction that still refuses is the
    other one, asserted above: `code`/`web` stay off the Internal-KB list, whose
    `internal-only` tier is held by `ROUTE_CLASS_RESTRICTION` and is untouched.

    **`code` and `web` are asserted on EVERY route this list is opened on**,
    deliberately — selectable on the four external routes, and ABSENT from the
    Internal-KB list (asserted above), which is not the same as selectable there.
    An earlier wording said "on EVERY route", readable as selectable everywhere,
    which is false. This file is G11's only test surface AND its sanctioned
    exception, so without that assertion one editable suite would absorb both the
    new behaviour and any drift in the old.
    """
    internal = {r.key: r for r in sp.render_catalogue(sp.ROUTE_INTERNAL_KB)}
    assert internal["knowledge_library"].selectable is True
    assert internal["document_folder"].selectable is True
    # `internal-only` (research-scope-framing.md:46) stays TRUE — and the external
    # classes are ABSENT FROM THE LIST rather than shown greyed out. That is the
    # stronger property and the honest one: every non-selectable row must carry a
    # reason, and "not on this route" is a statement about the route, not about
    # the class. A10 calls this the per-path restriction.
    assert "code" not in internal, "the internal route was widened to code"
    assert "web" not in internal, "the internal route was widened to the open web"
    assert sp.classes_on_route(sp.ROUTE_INTERNAL_KB) == ("knowledge_library",
                                                          "document_folder")
    assert sp.selectable_keys(sp.ROUTE_INTERNAL_KB) == ("knowledge_library",
                                                        "document_folder")
    # …and build_record refuses them too, so the restriction is not merely visual.
    with pytest.raises(sp.SourcePickerError):
        sp.build_record(
            sp.Selection(bounds=(("code", sp.Bound(selectors=("/tmp",))),)),
            route=sp.ROUTE_INTERNAL_KB)

    for route in (sp.ROUTE_NINJA, sp.ROUTE_DEEP, sp.ROUTE_ULTRA_DEEP,
                  sp.ROUTE_AUTONOMOUS):
        rows = {r.key: r for r in sp.render_catalogue(route)}
        # the old classes are unchanged on every route they were ever offered on
        assert rows["code"].selectable is True, f"code regressed on {route}"
        assert rows["web"].selectable is True, f"web regressed on {route}"
        # …and the two S7 classes are now offered here too (Q26, 2026-09-11).
        #
        # This loop asserted the OPPOSITE until then, and its stated reason was
        # "which has no reader for it". That reason had already expired:
        # `declared_read._adapter_for` supplies both adapters unconditionally with
        # no route conditioning, and these routes already invoke that module. The
        # PROPERTY being guarded is that a class is offered where its route is
        # WILLING to read it — a source-tier policy, per `is_selectable`'s
        # canonical note. (An earlier version of this comment said "offered
        # exactly where something reads it", which was the false rule this topic
        # spent four review rounds correcting.) Leaving the assertion as it stood
        # would have kept refusing a source this route can, and does, read.
        for key in ("knowledge_library", "document_folder"):
            assert rows[key].selectable is True, (
                f"{key} is not tickable on {route}, but `declared_read` dispatches "
                "it by kind with no route conditioning — refusing it here would "
                "deny a source this route can actually read")
        # All five registered kinds on one route, which is what makes the locked
        # promise of "any combination of sources in one run" achievable at all.
        # Ordering is catalogue order, not selection order.
        assert sp.selectable_keys(route) == ("code", "web", "knowledge_library",
                                             "document_folder", "linear")


def test_a1_an_unknown_route_fails_closed():
    """An unrecognised route must not read as 'unscoped'."""
    with pytest.raises(sp.SourcePickerError):
        sp.render_catalogue("no-such-route")


def test_a1_unavailable_rows_carry_a_reason_and_available_rows_do_not():
    """Now asserted on EVERY route, not only the routeless call (S7).

    A row made unavailable by the ROUTE must carry its reason exactly as one made
    unavailable by an unregistered kind does — that reason is what C12 requires.

    The two S7 rows were this docstring's standing example and stopped being one
    on 2026-09-11 (Q26): they are readable on every route now, so their
    `unavailable_*` slots are inert as web's already were. The slots are kept on
    web's precedent and the RULE is unchanged — it still binds any future class.
    The loop below is deliberately written over every row on every route rather
    than over a named set, so it keeps asserting the rule with no live example.
    """
    for route in (None,) + sp.ROUTES:
        for row in sp.render_catalogue(route):
            if row.selectable:
                assert row.unavailable_slot is None, (route, row.key)
                assert row.unavailable_kind is None, (route, row.key)
                assert row.bound_slot, "a selectable class must ask for a bound"
            else:
                assert row.unavailable_slot, (
                    f"{row.key} is unavailable on route {route!r} with no stated "
                    "reason")
                assert row.unavailable_kind in (sp.UNAVAILABLE_LATER_SLICE,
                                                sp.UNAVAILABLE_SLOT)


def test_a1_selectability_is_derived_not_stored(monkeypatch):
    """THE drift-proof property, driven through the real caller.

    Register a second kind and the catalogue must follow with no edit here. This is
    what makes S6/S7/S8 a registry change rather than a return visit, and it is the
    reason the entry carries no `available` field.

    **Each later change kept this property rather than weakening it.** It is now a
    property of THREE derived inputs, none of them a stored availability flag:
    registering a kind still moves the row with no catalogue edit (asserted below);
    the route set is a declarative field read at call time; and driver presence is
    read from `kind_reachability.KNOWN_UNREACHABLE`, also at call time. This test
    exercises the registration input. The route input is
    `test_a1_selectability_is_per_route_not_global`; the driver input is in
    `test_code_admission.py`, where it can be made non-vacuous on a monkeypatched
    kind rather than on a catalogue where every registered class has a driver.

    S8 NOTE — the subject moved from `linear` to `confluence`, because S8 Session 1
    registers `linear` and this test needs a kind that is still registered nowhere.
    The property under test is unchanged; only the stand-in moved. `linear` now
    exercises a DIFFERENT and newly-reachable arm, asserted at the end: it is
    registered, so the registration input says yes, and it is still not selectable
    because the DRIVER input says no. Before S8 no kind could be in that state, so
    that assertion could not be made on the real catalogue at all.
    """
    assert "confluence" not in srec.REGISTERED_KINDS
    before = {r.key: r.selectable for r in sp.render_catalogue()}
    assert before["confluence"] is False

    monkeypatch.setattr(srec, "REGISTERED_KINDS",
                        tuple(srec.REGISTERED_KINDS) + ("confluence",))

    after = {r.key: r.selectable for r in sp.render_catalogue()}
    assert after["confluence"] is True, (
        "selectability must be READ from the registry at call time; a stored flag "
        "would not have moved")
    assert "confluence" in sp.selectable_keys()
    # And nothing else moved.
    assert after["jira"] is False

    # S8 SESSION 3: the registered-AND-undriven combination is gone from the live
    # catalogue — `linear`'s driver landed and its exemption went with it in the
    # same commit. So the driver input is exercised on a SIMULATED exemption rather
    # than a live one; the property is identical and the arm stays non-vacuous,
    # which asserting the live (now driven) state would not.
    assert "linear" in srec.REGISTERED_KINDS
    assert "linear" not in kr.KNOWN_UNREACHABLE
    assert after["linear"] is True, (
        "a registered kind whose driver exists must be offered")

    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "linear", ("simulated", "this test"))
    withheld = {r.key: r.selectable for r in sp.render_catalogue()}
    assert withheld["linear"] is False, (
        "a registered kind with a recorded driver exemption must not be offered; "
        "if this reads True the driver input has stopped being consulted and a "
        "person would be offered a source nothing can open — the exact four-slice "
        "defect `code` had")


def test_a1_no_availability_field_was_hand_set_for_the_s7_classes():
    """S7's rows flipped by REGISTRATION, not by an edited availability field.

    Hand-setting `unavailable_kind`/`unavailable_slot` would reintroduce the second
    authority on admissibility that S4 removed. The only field S7 adds to these
    entries is the route set.
    """
    for key in ("knowledge_library", "document_folder"):
        e = sp.entry(key)
        assert e.unavailable_kind == sp.UNAVAILABLE_LATER_SLICE
        assert e.unavailable_slot == f"unavailable_{key}"
        assert e.landing_slice == "S7"
        assert e.allows_unscoped is False, "neither class may be declared unscoped"
        # Widened to every route on 2026-09-11 (Q26). The property this line
        # guards is unchanged — the route set is still the ONLY field these rows
        # carry for admissibility, and it is still not hand-set per route — but
        # the value it holds is now the whole route set rather than the
        # Internal-KB route alone. The `unavailable_*` slots asserted above are
        # consequently INERT (no route both offers these classes and cannot read
        # them) and are kept rather than deleted, on web's precedent.
        assert e.routes == sp.ROUTES


def test_a1_catalogue_entry_has_no_available_field():
    """The absence IS the mechanism — assert it structurally, not by convention."""
    fields = sp.SourceClassEntry.__dataclass_fields__
    assert "available" not in fields
    assert "selectable" not in fields


def test_a1_no_row_is_pre_ticked():
    """design-U2's no-default half: the person actively selects each source."""
    assert all(row.selected is False for row in sp.render_catalogue())


def test_a1_struck_drive_is_absent_not_unavailable():
    """S9 is struck, so link-provided Drive must not be advertised at all."""
    keys = {e.key for e in sp.CATALOGUE}
    assert not any("drive" in k for k in keys), (
        "offering Drive would advertise a class no slice will ship")


# --------------------------------------------------------------------------- #
# A2 — assembly, and the distinction S3 draws that a first author can invert.
# --------------------------------------------------------------------------- #

def test_a2_enumerated_code_declaration_round_trips(tmp_path):
    sel = sp.Selection(bounds=((
        "code", sp.Bound(selectors=(str(tmp_path),))),))
    rec = sp.build_record(sel)
    back = srec.ScopeRecord.from_json(rec.to_json())
    assert len(back.sources) == 1
    assert back.sources[0].kind == srec.KIND_CODE
    assert back.sources[0].scope_mode == srec.SCOPE_MODE_ENUMERATED
    assert back.sources[0].selectors == (str(tmp_path),)


def test_a2_unscoped_is_a_mode_not_an_empty_selector_list(monkeypatch):
    """The inversion S3 ships a test against, checked from its first author's side.

    An ENUMERATED record with no selectors admits nothing. An UNSCOPED one admits
    what the source exposes. Expressing the second as the first would invert a
    locked upstream requirement (`…_DESIGN.md:294-297`).

    S8 NOTE — `linear` is now genuinely registered, so the monkeypatch that used to
    ADD it is gone. What replaces it clears the driver exemption instead: Session 1
    registers the kind while its driver is Session 3's, so until then
    `is_selectable` withholds it and `build_record` refuses. This test is about
    unscoped-versus-enumerated, not about driver presence, so it asserts against a
    kind that is registered AND driven — which is what `linear` becomes one session
    later. The stand-in stays `linear` because it is the one registered class in the
    catalogue that also allows unscoped.
    """
    assert "linear" in srec.REGISTERED_KINDS, "S8 Session 1 registers this kind"
    monkeypatch.setattr(kr, "KNOWN_UNREACHABLE", {})
    sel = sp.Selection(bounds=(("linear", sp.Bound(unscoped=True)),))
    rec = sp.build_record(sel)
    src = rec.sources[0]

    # The picker's half: it produced the MODE, not an empty selector list.
    assert src.scope_mode == srec.SCOPE_MODE_UNSCOPED
    assert src.selectors == ()

    # The behavioural half, on `code` — the one kind with a containment arm. A fake
    # kind cannot be used here: S3's `check()` fails closed on a registered kind with
    # no arm, which is its design and not something to route around.
    unscoped = srec.DeclaredSource(kind=srec.KIND_CODE,
                                   scope_mode=srec.SCOPE_MODE_UNSCOPED)
    enumerated_empty = srec.DeclaredSource(kind=srec.KIND_CODE,
                                           scope_mode=srec.SCOPE_MODE_ENUMERATED)
    assert unscoped.check("/anywhere").admitted is True
    assert enumerated_empty.check("/anywhere").admitted is False


def test_a2_a_bound_cannot_be_both_unscoped_and_enumerated():
    with pytest.raises(sp.SourcePickerError):
        sp.Bound(unscoped=True, selectors=("/tmp",))


def test_a2_unscoped_refused_for_a_class_that_does_not_allow_it(tmp_path):
    with pytest.raises(sp.SourcePickerError) as e:
        sp.build_record(sp.Selection(bounds=(("code", sp.Bound(unscoped=True)),)))
    assert "selectors" in str(e.value)


def test_a2_declared_claude_md_is_refused_with_its_reason_surfaced(tmp_path):
    """design-A11's declaration half reaches the person, never a swallowed drop."""
    target = tmp_path / "CLAUDE.md"
    target.write_text("pointer, not a source\n", encoding="utf-8")
    with pytest.raises(srec.ScopeRecordError) as e:
        sp.build_record(sp.Selection(bounds=((
            "code", sp.Bound(selectors=(str(target),))),)))
    assert "CLAUDE.md" in str(e.value)


def test_a2_unselectable_class_cannot_be_assembled():
    with pytest.raises(sp.SourcePickerError) as e:
        sp.build_record(sp.Selection(bounds=(("jira", sp.Bound(selectors=("x",))),)))
    assert "not selectable" in str(e.value)


def test_a2_empty_selection_has_no_record():
    with pytest.raises(sp.SourcePickerError):
        sp.build_record(sp.Selection(bounds=()))


# --------------------------------------------------------------------------- #
# A3 — reachability at selection time, and the not-a-read boundary.
# --------------------------------------------------------------------------- #

def test_a3_existing_path_probes_reachable(tmp_path):
    src = srec.DeclaredSource(kind=srec.KIND_CODE, selectors=(str(tmp_path),))
    results = sp.probe(src)
    assert len(results) == 1
    assert results[0].reachable is True


def test_a3_missing_path_probes_unreachable_naming_the_path(tmp_path):
    missing = tmp_path / "nope"
    src = srec.DeclaredSource(kind=srec.KIND_CODE, selectors=(str(missing),))
    (result,) = sp.probe(src)
    assert result.reachable is False
    assert str(missing) in result.target
    assert result.reason_slot


def test_a3_probe_never_opens_a_file(tmp_path):
    """Driven, not asserted: a file whose READ would fail must still probe reachable.

    Chmod 000 leaves the path stat-able and unreadable, so a probe that opened it
    would raise here. Skipped as root, for whom the mode is not enforced.
    """
    if os.geteuid() == 0:
        pytest.skip("root bypasses the permission bit this test relies on")
    locked = tmp_path / "unreadable.py"
    locked.write_text("x = 1\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        src = srec.DeclaredSource(kind=srec.KIND_CODE, selectors=(str(locked),))
        (result,) = sp.probe(src)
        assert result.reachable is True, (
            "the probe must answer existence and type only — it opened the file")
    finally:
        locked.chmod(0o600)


def test_a3_unscoped_source_probes_reachable_rather_than_empty(monkeypatch):
    """"Nothing to check" is not "no result" — an empty tuple would read as unreachable."""
    monkeypatch.setattr(srec, "REGISTERED_KINDS",
                        tuple(srec.REGISTERED_KINDS) + ("linear",))
    src = srec.DeclaredSource(kind="linear", scope_mode=srec.SCOPE_MODE_UNSCOPED)
    results = sp.probe(src)
    assert len(results) == 1 and results[0].reachable is True


def test_a3_probe_dispatch_fails_closed_on_an_unregistered_kind(monkeypatch):
    """Registering a kind without a probe arm must fail loudly, never admit.

    S8 NOTE — the stand-in moved from `linear` to `confluence`. S8 Session 1 gives
    `linear` a real probe arm, so it can no longer play the registered-but-unprobed
    kind; `confluence` is a catalogue row whose kind is still registered nowhere,
    which is what this test needs. The property is unchanged.
    """
    monkeypatch.setattr(srec, "REGISTERED_KINDS",
                        tuple(srec.REGISTERED_KINDS) + ("confluence",))
    src = srec.DeclaredSource(kind="confluence", selectors=("/tmp",))
    with pytest.raises(sp.SourcePickerError) as e:
        sp.probe(src)
    assert "probe arm" in str(e.value)

    # Non-vacuity: the kind S8 DID give an arm to must NOT raise here, or this
    # test would keep passing on a kind that never had one.
    linear_src = srec.DeclaredSource(kind="linear", selectors=("ENG",))
    assert sp.probe(linear_src)[0].reachable


# --------------------------------------------------------------------------- #
# A4 — the two terminal branches. Neither may produce anything registrable.
# --------------------------------------------------------------------------- #

def test_a4_spike_stops_before_the_catalogue_and_carries_no_record(tmp_path):
    out = sp.open_picker("how does the auth flow work?", is_spike=True,
                         selection=sp.Selection(bounds=((
                             "code", sp.Bound(selectors=(str(tmp_path),))),)))
    assert isinstance(out, sp.SpikeStop)
    assert out.reason_slot
    assert not hasattr(out, "record")
    # Even with a selection supplied, a spike yields nothing registrable.
    assert not isinstance(out, sp.Selection)


def test_a4_empty_selection_is_its_own_outcome_and_carries_no_record():
    for sel in (None, sp.Selection(bounds=())):
        out = sp.open_picker("a real question", is_spike=False, selection=sel)
        assert isinstance(out, sp.EmptySelection)
        assert out.reason_slot
        assert not hasattr(out, "record")


def test_a4_terminal_outcomes_have_no_record_attribute_by_construction():
    """Structural, not conventional: there is no field a caller could read."""
    for cls in (sp.SpikeStop, sp.EmptySelection):
        assert "record" not in cls.__dataclass_fields__
        assert "sources" not in cls.__dataclass_fields__


def test_a4_a_real_selection_passes_through(tmp_path):
    sel = sp.Selection(bounds=(("code", sp.Bound(selectors=(str(tmp_path),))),))
    out = sp.open_picker("a real question", is_spike=False, selection=sel)
    assert out is sel


# --------------------------------------------------------------------------- #
# A6 — persistence, and the non-regression that makes it safe.
# --------------------------------------------------------------------------- #

def _pipeline(tmp_path, monkeypatch):
    """The pipeline module from the tree under test, with state redirected.

    Loaded by explicit path at import time (see `_load_by_path`), so this returns
    the clone's module regardless of what another test module cached under the bare
    name. `RP_STATE_DIR` is read at call time by `_resolve_state_dir`, so setting
    the env var is enough — no reload is needed, and reloading would re-bind the
    bare name and reintroduce the collision.
    """
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_path / "rp"))
    return rp


def test_a6_scope_record_is_hoisted_onto_the_cycle(tmp_path, monkeypatch):
    rp = _pipeline(tmp_path, monkeypatch)
    rec = sp.build_record(sp.Selection(bounds=((
        "code", sp.Bound(selectors=(str(tmp_path),))),)))
    payload = {
        "research_file_path": "Thoughts/x_RESEARCH.md",
        "caller_skill": "/research",
        "scope_record": json.loads(rec.to_json()),
    }
    rp.cmd_advance("sid-a6", "r0_intake", payload, cycle_id="default")
    state = json.loads((tmp_path / "rp" / "RP-sid-a6.json").read_text())
    cycle = state["cycles"]["default"]
    assert cycle["scope_record"]["sources"][0]["kind"] == "code"
    assert cycle["scope_record"]["schema_version"] == 1


def test_a6_absent_scope_record_leaves_the_cycle_as_today(tmp_path, monkeypatch):
    """The non-regression conjunct: additive means additive."""
    rp = _pipeline(tmp_path, monkeypatch)
    rp.cmd_advance("sid-a6b", "r0_intake",
               {"research_file_path": "Thoughts/x_RESEARCH.md",
                "caller_skill": "/research"},
               cycle_id="default")
    cycle = json.loads(
        (tmp_path / "rp" / "RP-sid-a6b.json").read_text())["cycles"]["default"]
    assert "scope_record" not in cycle
    # The subject here is that an absent scope_record is ADDITIVE — it leaves
    # the cycle otherwise untouched. Approval is a separate axis: since
    # research-entry-point-enforcement S4 a bare `caller_skill` no longer flips
    # `r1_scope_approved` (A4 — a caller's name is never approval), so the
    # unchanged-behaviour assertion is that this payload approves nothing.
    assert cycle["r1_scope_approved"] is False


@pytest.mark.parametrize("bad, why", [
    ("not-an-object", "must be an object"),
    ({"schema_version": 99, "sources": []}, "schema_version"),
    ({"schema_version": 1, "sources": "nope"}, "must be a list"),
])
def test_a6_malformed_scope_record_is_refused_at_registration(bad, why, tmp_path,
                                                              monkeypatch):
    rp = _pipeline(tmp_path, monkeypatch)
    with pytest.raises(ValueError) as e:
        rp.validate_schema("r0_intake", {
            "research_file_path": "Thoughts/x_RESEARCH.md",
            "scope_record": bad,
        })
    assert why in str(e.value)


def test_a6_the_persisted_record_survives_a_plain_json_reader(tmp_path, monkeypatch):
    """The record crosses a process boundary as JSON, so its shape must hold up to
    a reader that cannot import a Python type. `jq` is a convenient stand-in.

    **RENAMED, because the old name and rationale asserted a false fact.** They
    said "S5's gate is a `jq` hook" that reads this record. No shell hook reads the
    record: `research-scope-gate.sh` reads only the `r1_scope_approved` /
    `r1_scope_revoked` flags beside it, and every consumer of the record itself is
    Python (`research_pipeline` for shape, `declared_read` + `source_port` for
    content). The assertion below was always a fine shape check — it is the
    justification that was wrong, so the justification is what changed.
    """
    if not any((Path(p) / "jq").exists() for p in os.environ.get("PATH", "").split(":")):
        pytest.skip("jq not on PATH")
    rp = _pipeline(tmp_path, monkeypatch)
    rec = sp.build_record(sp.Selection(bounds=((
        "code", sp.Bound(selectors=(str(tmp_path),))),)))
    rp.cmd_advance("sid-jq", "r0_intake",
               {"research_file_path": "Thoughts/x_RESEARCH.md",
                "caller_skill": "/research",
                "scope_record": json.loads(rec.to_json())},
               cycle_id="default")
    out = subprocess.run(
        ["jq", "-r", '.cycles["default"].scope_record.sources[0].kind',
         str(tmp_path / "rp" / "RP-sid-jq.json")],
        capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "code"


# --------------------------------------------------------------------------- #
# Slice-wide: the constraint this slice set itself.
# --------------------------------------------------------------------------- #

def test_no_s3_module_is_amended():
    """S4 is the first consumer of S3's contract, and must not have had to change it.

    BOUNDED, and the plan's Gate 2 says so: this detects an edit against the
    promoted tree, not a semantically-equivalent rewrite elsewhere. It is the
    honest form of the claim, not a proof of it.
    """
    repo = os.environ.get("S4_CONFIG_SOURCE_SOURCE")
    if not repo:
        pytest.skip("set S4_CONFIG_SOURCE_SOURCE to diff against the promoted tree")
    for mod in S3_MODULES:
        rel = mod.relative_to(CONFIG_DIR)
        out = subprocess.run(["git", "-C", repo, "diff", "--name-only", "--", str(rel)],
                             capture_output=True, text=True)
        assert not out.stdout.strip(), f"S4 amended an S3 module: {rel}"


def test_picker_does_not_import_the_port_or_any_adapter():
    """Dependency direction: the picker knows the declaration, not the reading.

    Checked over the parsed IMPORT statements, not the raw text — the module's
    docstring names `source_port` while explaining why it does not widen it, and a
    substring scan cannot tell an explanation from a dependency.
    """
    import ast
    tree = ast.parse((RESEARCH_DIR / "source_picker.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(f"{node.module or ''}.{a.name}" for a in node.names)
    joined = " ".join(imported)
    assert "source_port" not in joined, f"picker imports the port: {sorted(imported)}"
    assert "adapters" not in joined, f"picker imports an adapter: {sorted(imported)}"
    assert any("scope_record" in name for name in imported), (
        "the picker must import the declaration record it authors")


def test_package_manifest_names_the_new_module():
    body = (RESEARCH_DIR / "__init__.py").read_text(encoding="utf-8")
    assert "source_picker" in body, (
        "the package docstring is this package's per-slice module manifest")

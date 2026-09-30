"""S8 Session 1 — the `linear` kind registered across its dispatch surface.

research-source-adapters S8 / plan actions A1, A2, A3, A4, A4b (design-A12/A17).

Named for what it tests rather than for the slice, matching this topic's existing
suites (`test_source_admission_port`, `test_source_picker`, `test_path_admission`,
`test_web_admission`, `test_local_file_admission`).

**Scope is REGISTRATION, not reading.** Session 1 makes `linear` a kind the system
can express: a locator grammar, a citation vocabulary, two registries, a probe arm,
a containment arm and a display arm. The adapter that actually reads Linear, the
credential path and the MCP transport are Sessions 2 and 3, and nothing here fakes
them — a suite that stubbed an adapter to claim Linear "works" would be exactly the
green-suite-over-a-fake this topic's record warns about (`code` sat registered and
undriven for four slices behind one).

So the honest end state this suite pins is: the kind is fully expressible, and
every gate that should observe its absence does.

**UPDATED AT SESSION 3.** This suite originally also pinned "NOT yet offered to a
person", on the live `KNOWN_UNREACHABLE` entry that withheld the offer while no
driver existed. Session 3 wired the driver, which deletes that entry in the same
commit — `kind_reachability.check()` fails a driven kind that still carries one, so
the two cannot coexist. The offerability tests at the end of this file are
re-pointed accordingly: they assert the offer live and simulate its withholding,
where before they asserted the withholding live and simulated the offer. Both
directions are still asserted; only which side is live has swapped.

**Every gate here observes an ABSENCE**, per the plan's Guiding Policy: each asserts
both that the property holds AND that removing the work makes it fail. A gate that
would still pass with the action skipped is the defect this slice was told to expect
in itself.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

from research import kind_reachability as kr           # noqa: E402
from research import locator_grammar as lg             # noqa: E402
from research import scope_record as srec              # noqa: E402
from research import source_picker as spk              # noqa: E402
from research import source_port as sp                 # noqa: E402

STATED = "[stated — linear:<workspace>@<version>:<issue>]"
PARAPHRASED = "[paraphrased — linear:<workspace>@<version>:<issue>]"


def _engine():
    """Load the engine by path, as its own suites do."""
    spec = importlib.util.spec_from_file_location(
        "_fce_linear_admission", str(HOOKS_DIR / "_factcheck_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# =========================================================================== #
# A1 — the locator grammar
# =========================================================================== #

def test_a1_the_linear_locator_requires_an_issue_and_allows_a_comment():
    kind = lg.get_kind("linear")
    assert kind.required_parts == ("issue",)
    assert kind.optional_parts == ("comment",)


def test_a1_an_issue_alone_renders_and_an_issue_with_a_comment_renders():
    assert lg.linear_locator(issue="ENG-123").render() == "ENG-123"
    assert lg.linear_locator(issue="ENG-123", comment="c-9").render() == "ENG-123:c-9"


def test_a1_a_locator_missing_the_issue_raises_rather_than_rendering_short():
    """The ABSENCE half: an incomplete locator must not silently render.

    This is the whole reason locators are named parts rather than strings — a
    shortened locator would be indistinguishable from a complete one.
    """
    incomplete = lg.linear_locator(comment="c-9")
    assert incomplete.missing_required_parts() == ("issue",)
    assert not incomplete.is_complete()
    with pytest.raises(lg.LocatorError) as e:
        incomplete.render()
    assert "issue" in str(e.value)


def test_a1_parse_is_undefined_for_this_kind_and_says_so():
    """A stated narrowing, pinned so it stays visible rather than being discovered.

    `parse()` is defined only for kinds with no optional parts. Nothing in the read
    path parses a locator, so this costs nothing today; the alternative (a required
    `comment`) would force every issue-level citation to carry a fabricated comment
    id, which is worse than an absent inverse.
    """
    with pytest.raises(lg.LocatorError):
        lg.parse("linear", "ENG-123")


# =========================================================================== #
# A2 — the citation vocabulary, atomic across three loci
# =========================================================================== #

def test_a2_the_linear_marker_pair_is_active_in_the_code_registry():
    fce = _engine()
    forms = {m.form for m in fce.active_citation_markers()}
    assert STATED in forms
    assert PARAPHRASED in forms

    by_form = {m.form: m for m in fce.CITATION_MARKER_REGISTRY}
    stated, para = by_form[STATED], by_form[PARAPHRASED]
    assert (stated.locator, stated.source_class, stated.antipattern) == (
        "linear", "internal", "exempt")
    assert para.antipattern == "checked", "matching every existing pair"


def test_a2_the_three_loci_agree():
    """The registry and the rules mirror must not diverge.

    Compared against the mirror in the engine's OWN tree, so this is correct under
    a `config-experiment` clone as well as live — the mirror constant is hardwired
    to `~/.claude`, which would otherwise compare a clone's registry against the
    live mirror and report drift for every not-yet-deployed marker.
    """
    fce = _engine()
    mirror = Path(fce.__file__).resolve().parent.parent / "rules" / "research-scope-framing.md"
    assert not fce.check_citation_marker_drift(rules_path=mirror)


def test_a2_removing_the_marker_from_a_mirror_makes_the_guard_fail_by_name(tmp_path):
    """The ABSENCE half: a partial three-loci edit must fail LOUDLY, naming it.

    Driven on a COPY of the real mirror with the linear rows stripped, so it tests
    the shipped guard rather than a hand-built fixture.
    """
    fce = _engine()
    real = Path(fce.__file__).resolve().parent.parent / "rules" / "research-scope-framing.md"
    stripped = tmp_path / "research-scope-framing.md"
    stripped.write_text(
        "\n".join(ln for ln in real.read_text(encoding="utf-8").splitlines()
                  if "linear:<workspace>@<version>:<issue>" not in ln) + "\n",
        encoding="utf-8")

    divergences = fce.check_citation_marker_drift(rules_path=stripped)
    named = [d for d in divergences if "linear:<workspace>" in d]
    assert len(named) == 2, (
        "both markers must be named individually; got %r" % (divergences,))


# =========================================================================== #
# A3 — the two registries
# =========================================================================== #

def test_a3_linear_is_registered_in_both_registries():
    assert "linear" in srec.REGISTERED_KINDS
    assert "linear" in sp.KIND_RULES


def test_a3_the_registered_row_is_used_and_is_not_the_strict_default():
    """The ABSENCE half is the second assertion: with the row gone, `rules_for`
    falls through to `_DEFAULT_KIND_RULES`, whose `path_shaped` DIFFERS — so this
    gate cannot pass on a kind that was never registered."""
    rules = sp.rules_for("linear")
    assert rules is not sp._DEFAULT_KIND_RULES
    assert rules.path_shaped is False
    assert rules.truncate_over_budget is False
    assert rules.version_is_revision is False
    assert rules.render_form == sp.RENDER_FORM_VERSIONED
    assert rules.max_item_bytes is not None and rules.max_run_bytes is not None

    saved = sp.KIND_RULES.pop("linear")
    try:
        fallen = sp.rules_for("linear")
        assert fallen is sp._DEFAULT_KIND_RULES
        assert fallen.path_shaped is True, (
            "the default must DIFFER from the registered row, or this gate is vacuous")
    finally:
        sp.KIND_RULES["linear"] = saved


def test_a3_linear_is_in_neither_set_it_must_stay_out_of():
    """Not path-shaped (an issue id is not a path); not unscoped-forbidden (a
    tracker's own exposure IS a bound, and the catalogue row offers it)."""
    assert "linear" not in srec.PATH_SHAPED_KINDS
    assert "linear" not in srec.UNSCOPED_FORBIDDEN_KINDS


def test_a3_both_selector_shapes_construct_and_so_does_unscoped():
    """The locked bound is projects OR a filtered-issues link OR unscoped. A
    one-shape selector would silently deliver half the promised bound."""
    assert srec.DeclaredSource(kind="linear", selectors=("ENG", "PRO")).selectors == (
        "ENG", "PRO")
    linked = srec.DeclaredSource(
        kind="linear", selectors=("https://linear.app/acme/view/x",),
        resolved_members=("ENG-1",))
    assert linked.resolved_members == ("ENG-1",)
    assert srec.DeclaredSource(
        kind="linear", scope_mode=srec.SCOPE_MODE_UNSCOPED).selectors == ()


def test_a3_an_unresolved_link_is_not_approvable():
    """A7's promise made structural: a link with no frozen membership cannot be
    constructed, so it cannot reach an approval gate."""
    with pytest.raises(srec.ScopeRecordError) as e:
        srec.DeclaredSource(kind="linear",
                            selectors=("https://linear.app/acme/view/x",))
    assert "resolved membership" in str(e.value)


def test_a3_resolved_to_zero_is_a_legitimate_bound_distinct_from_unresolved():
    """The distinction the whole freeze rests on. A filter matching nothing is a
    bound a person may approve; collapsing it into "unresolved" would either refuse
    a legal bound or let an unresolved link through."""
    zero = srec.DeclaredSource(
        kind="linear", selectors=("https://linear.app/acme/view/x",),
        resolved_members=())
    assert zero.resolved_members == ()
    assert zero.to_dict()["resolved_members"] == []
    assert srec.DeclaredSource.from_dict(zero.to_dict()).resolved_members == ()


def test_a3_resolved_members_belongs_to_linear_alone():
    with pytest.raises(srec.ScopeRecordError):
        srec.DeclaredSource(kind="code", selectors=("/tmp",),
                            resolved_members=("x",))


def test_a3_a_malformed_project_identifier_is_refused_at_construction():
    with pytest.raises(srec.ScopeRecordError) as e:
        srec.DeclaredSource(kind="linear", selectors=("/tmp",))
    assert "project identifier" in str(e.value)


def test_a3_the_scope_constructor_expresses_all_three_bounds():
    assert srec.linear_scope().sources[0].scope_mode == srec.SCOPE_MODE_UNSCOPED
    assert srec.linear_scope(["ENG"]).sources[0].scope_mode == srec.SCOPE_MODE_ENUMERATED
    linked = srec.linear_scope(["https://linear.app/acme/view/x"],
                               resolved_members=["ENG-1"])
    assert linked.check("linear", "ENG-1").admitted


def test_a3_the_record_carries_a_connection_id_and_never_a_secret():
    """C5's shape at this altitude: the declaration doubles as the approval
    artifact and may be committed, so it names the connection and holds no token."""
    rec = srec.linear_scope(["ENG"], connection_id="conn-9")
    blob = rec.to_json()
    assert "conn-9" in blob
    for secretish in ("token", "bearer", "secret", "access_token"):
        assert secretish not in blob.lower()


# =========================================================================== #
# A4 — the probe arm and the route set
# =========================================================================== #

def test_a4_probe_returns_one_result_per_selector_for_both_shapes():
    by_project = srec.DeclaredSource(kind="linear", selectors=("ENG", "PRO"))
    results = spk.probe(by_project)
    assert len(results) == 2
    assert [r.target for r in results] == ["ENG", "PRO"]
    assert all(r.reachable for r in results)

    mixed = srec.DeclaredSource(
        kind="linear", selectors=("ENG", "https://linear.app/acme/view/x"),
        resolved_members=("ENG-1",))
    assert len(spk.probe(mixed)) == 2


def test_a4_probe_never_reads_issue_content():
    """Selection time answers reachability, never content.

    Asserted structurally (the arm holds no fetch) rather than by watching a
    network call that may not happen on a given machine.
    """
    import re
    src = (SKILLS_DIR / "research" / "source_picker.py").read_text(encoding="utf-8")
    after = src.split("def _probe_linear", 1)[1]
    nxt = re.search(r"\ndef ", after)
    assert nxt, "no top-level def follows _probe_linear — the anchor is broken"
    arm = after[: nxt.start()]
    assert "getaddrinfo" in arm, "the scanned region is not _probe_linear's body"
    for forbidden in ("urlopen", "requests", "http.client", "_fetch", "admit("):
        assert forbidden not in arm, f"{forbidden} appears in a selection-time probe"


def test_a4_an_unscoped_declaration_short_circuits_before_dispatch():
    results = spk.probe(srec.DeclaredSource(kind="linear",
                                            scope_mode=srec.SCOPE_MODE_UNSCOPED))
    assert len(results) == 1 and results[0].reachable


def test_a4_an_unreachable_link_reports_under_linears_own_reason_slot():
    bad = srec.DeclaredSource(
        kind="linear",
        selectors=("https://nx-does-not-exist-8bdc44eb.invalid/view/x",),
        resolved_members=())
    result = spk.probe(bad)[0]
    assert not result.reachable
    assert result.reason_slot == "unreachable_linear", (
        "a Linear selector must not borrow web's reason slot")


def test_a4_the_route_set_is_explicit_and_excludes_the_internal_only_route():
    """The ABSENCE half is the last assertion: the FIELD DEFAULT includes the
    internal-only route, so an omitted `routes=` would have silently widened that
    route — whose declared source-tier is internal-only — to reach a third-party
    remote."""
    import dataclasses
    entry = spk.entry("linear")
    assert entry.routes == spk._EXTERNAL_ROUTES
    assert spk.ROUTE_INTERNAL_KB not in entry.routes

    default = dataclasses.fields(spk.SourceClassEntry)[-1].default
    assert spk.ROUTE_INTERNAL_KB in default, (
        "if the default ever excludes it, this gate stops being load-bearing")


# =========================================================================== #
# A4b — the containment arm and the display arm
# =========================================================================== #

def test_a4b_a_project_bound_admits_in_scope_and_refuses_out_of_scope():
    src = srec.DeclaredSource(kind="linear", selectors=("ENG",))
    admitted = src.check("ENG-123")
    assert admitted.admitted and admitted.matched_selector == "ENG"
    assert not src.check("OPS-1").admitted


def test_a4b_a_link_bound_tests_the_frozen_membership_not_the_link():
    src = srec.DeclaredSource(
        kind="linear", selectors=("https://linear.app/acme/view/x",),
        resolved_members=("ENG-1", "ENG-7"))
    assert src.check("ENG-7").admitted
    refusal = src.check("ENG-8")
    assert not refusal.admitted and "approved" in refusal.reason


def test_a4b_a_refusal_names_the_issue_not_a_cwd_joined_path():
    """The defect `_display_target` was written to fix for `web`, in the message a
    person actually reads."""
    src = srec.DeclaredSource(kind="linear", selectors=("ENG",))
    refusal = src.check("OPS-1")
    assert refusal.resolved_target == "OPS-1"
    assert os.sep not in refusal.resolved_target
    assert srec._display_target("linear", "LIN-123") == "LIN-123"
    assert not srec._display_target("linear", "LIN-123").startswith(os.getcwd())


def test_a4b_a_non_issue_target_is_refused_with_its_reason_never_raised():
    """`check()` runs at admit() step 1, OUTSIDE the try/except — so a raise here
    would abort the whole run rather than degrade one item."""
    src = srec.DeclaredSource(kind="linear", selectors=("ENG",))
    refusal = src.check("not-an-issue")
    assert not refusal.admitted
    assert "issue identity" in refusal.reason


def test_a4b_removing_the_containment_arm_makes_admission_raise(monkeypatch):
    """The ABSENCE half, and the reason A4b exists at all.

    `check()`'s final branch is a deliberate fail-loud raise. Registering `linear`
    without a containment arm would have produced a kind that is selectable,
    approvable and FATAL on first admission — an independent checker found this
    site after four slices of precedent hid it.
    """
    src = srec.DeclaredSource(kind="linear", selectors=("ENG",))

    def check_without_the_linear_arm(self, target):
        if self.kind in srec.PATH_SHAPED_KINDS:
            return self._check_path(target)
        if self.kind == srec.KIND_WEB:
            return self._check_web(target)
        raise srec.ScopeRecordError(
            f"kind {self.kind!r} is registered but has no containment check")

    monkeypatch.setattr(srec.DeclaredSource, "check", check_without_the_linear_arm)
    with pytest.raises(srec.ScopeRecordError) as e:
        src.check("ENG-123")
    assert "no containment check" in str(e.value)


# =========================================================================== #
# The end state — registered, expressible, and (since Session 3) READ
#
# These four tests were written at Session 1, when the honest end state was
# "registered, NOT yet offered", and they pinned that intermediate state on the
# live `KNOWN_UNREACHABLE` entry. Session 3 wired the driver, which deletes that
# entry in the same commit (the deploy gate fails "NOW DRIVEN — its exemption is
# stale" otherwise), so the live-state assertions are re-pointed here.
#
# **The mechanism is not weakened by the re-pointing.** Each pair still asserts
# both directions; what changed is which side is live and which is monkeypatched.
# Session 1 asserted the exemption live and simulated its absence; these assert
# its absence live and simulate its presence. The 1:1 pairing is intact, which is
# what stops a re-point from quietly becoming a deletion.
# =========================================================================== #

def test_linear_is_registered_and_now_offered_because_a_driver_exists():
    """The property that keeps this slice from repeating `code`'s four-slice defect.

    Registration makes the kind DECLARABLE; offering it needs a driver too. Session
    3 supplies the driver, so the kind is now correctly offered — and the pair
    below is what proves the offer still turns on the driver rather than on nothing.
    """
    assert "linear" in srec.REGISTERED_KINDS
    assert "linear" not in kr.KNOWN_UNREACHABLE
    assert spk.is_selectable(spk.entry("linear"))
    assert "linear" in spk.selectable_keys(spk.ROUTE_NINJA)


def test_the_exemption_is_what_withholds_a_kind(monkeypatch):
    """The ABSENCE half: re-add an exemption and the kind is withheld again — so
    the mapping is load-bearing, not decorative.

    Simulated rather than live now, because the live entry is gone. The assertion
    is the same one Session 1 made from the other side.
    """
    monkeypatch.setattr(kr, "KNOWN_UNREACHABLE",
                        {"linear": ("simulated", "this test")})
    assert not spk.is_selectable(spk.entry("linear"))
    assert "linear" not in spk.selectable_keys(spk.ROUTE_NINJA)


def test_the_reachability_gate_is_clean_and_compels_the_record_in_both_directions(
        monkeypatch):
    """The gate compels the record, and refuses a STALE one.

    Both directions, because that is the shape `check()` actually has — and the
    second direction is what made Session 3 unable to ship the driver while holding
    the exemption back.
    """
    assert kr.check() == []
    # Direction 1 — a registered kind with no driver and no exemption fails.
    monkeypatch.setattr(kr, "derive_driver_kinds", lambda *a, **k: {})
    problems = "\n".join(kr.check())
    assert "linear" in problems and "no production driver" in problems


def test_a_driven_kind_that_still_carried_an_exemption_would_fail_as_stale(
        monkeypatch):
    """Direction 2, and the reason A11's deletion could not be deferred.

    Session 3 was asked to hold the exemption until its closing verification had
    walked the real workspace. This is the assertion that makes that impossible:
    the driver's presence alone makes the kind derive as driven, and a driven kind
    carrying an exemption fails.
    """
    monkeypatch.setattr(kr, "KNOWN_UNREACHABLE",
                        {"linear": ("stale", "nobody")})
    problems = "\n".join(kr.check())
    assert "linear" in problems and "stale" in problems


def test_a_declared_linear_source_now_has_a_reader():
    """Session 3's boundary, asserted rather than assumed.

    A declared class with no reader must fail LOUDLY, never be silently skipped —
    the failure `declared_read` exists to remove. `linear` is deliberately NOT in
    `DRIVEN_ELSEWHERE`: it is not read elsewhere, it is read HERE.
    """
    from research import declared_read as dr
    from research.adapters.linear import LinearAdapter
    assert "linear" not in dr.DRIVEN_ELSEWHERE

    adapter = LinearAdapter(object(), "tok")
    assert dr._adapter_for("linear", Path("/tmp"), linear_adapter=adapter) is adapter
    # And a run holding no authorization still refuses BY NAME rather than
    # silently reading nothing — the same loudness, for the remaining gap.
    with pytest.raises(dr.MissingAuthorization) as e:
        dr._adapter_for("linear", Path("/tmp"))
    assert "no Linear authorization" in str(e.value)

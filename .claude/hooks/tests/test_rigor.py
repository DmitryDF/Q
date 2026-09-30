"""Tests for the validation-rigor dial.

The point is not that the shipped table passes — it is that the two FLOORS cannot
be removed by a later edit without a test going red, and that the conformance
scan is non-vacuous. A dial whose floors were only documented would be the same
kind of claim the dial itself was built to replace.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parents[1]
SKILLS = HOOKS.parent / "skills"
RIGOR_PY = HOOKS / "rigor.py"


def _load():
    spec = importlib.util.spec_from_file_location("rigor_under_test", RIGOR_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rigor_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


rg = _load()


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Never read or write a real install's setting while testing it."""
    monkeypatch.setenv(rg.CONFIG_ENV, str(tmp_path / "q-rigor.json"))
    monkeypatch.delenv(rg.TIER_ENV, raising=False)
    yield


# --------------------------------------------------------------------------
# Floor 1 — producer-never-verifies is not on the dial
# --------------------------------------------------------------------------

def test_every_verification_tier_dispatches_at_least_one_binding_checker():
    """A tier that dispatched none would be self-assessment with a label on it."""
    for cls in ("gate", "check"):
        for tier in rg.TIERS:
            a = rg.rigor_for(cls, tier=tier)
            assert a.total_binding >= 1, f"{cls}/{tier} verifies nothing"


def test_the_floor_check_is_not_vacuous():
    """Remove the floor and selftest must go red.

    Without this, `selftest` could be asserting nothing: the table it reads is the
    same table it validates.
    """
    saved = rg._VERIFY["gate"]["minimal"]
    rg._VERIFY["gate"]["minimal"] = {"binding": {}, "advisory": {"opus": 1}, "vote": False}
    try:
        problems = rg.selftest()
        assert any("zero binding checkers" in p for p in problems), problems
    finally:
        rg._VERIFY["gate"]["minimal"] = saved
    assert rg.selftest() == [] or all("engine unavailable" in p for p in rg.selftest())


@pytest.mark.skipif(not (HOOKS / "_factcheck_engine.py").is_file(),
                    reason="engine not in this tree")
def test_every_allocation_is_legal_against_the_real_engine():
    """Run the shipped validator, not a belief about it.

    The tiers are the operator-facing contract: a tier that cannot be dispatched
    would offer a broken option at install time.
    """
    sys.path.insert(0, str(HOOKS))
    from _factcheck_engine import validate_allocation  # noqa: PLC0415

    for cls in ("gate", "check"):
        for tier in rg.TIERS:
            a = rg.rigor_for(cls, tier=tier)
            prof = validate_allocation(a.binding, advisory=a.advisory or None)
            assert prof["total_binding"] >= 1


def test_an_illegal_allocation_would_be_caught():
    """N=2 is banned by the engine's rule — prove selftest notices."""
    saved = rg._VERIFY["check"]["standard"]
    rg._VERIFY["check"]["standard"] = {"binding": {"sonnet": 2}, "advisory": {}, "vote": False}
    try:
        problems = rg.selftest()
        assert any("illegal allocation" in p for p in problems), problems
    finally:
        rg._VERIFY["check"]["standard"] = saved


# --------------------------------------------------------------------------
# Floor 2 — the fixed pipelines do not move
# --------------------------------------------------------------------------

def test_fixed_returns_canon_at_every_tier():
    """factcheck-convergence.md §1 fixes the 3-checker default for those kinds.

    A site that asks the dial for a canon pipeline must get canon back — that is
    what makes `fixed` safe to write at a call site.
    """
    for tier in rg.TIERS:
        a = rg.rigor_for("fixed", tier=tier)
        assert a.flags == rg.FIXED_CANON_FLAGS
        assert a.binding == {"sonnet": 3}


# --------------------------------------------------------------------------
# Resolution order, and the one direction a mistake may go
# --------------------------------------------------------------------------

def test_an_unknown_tier_degrades_to_the_default_never_to_nothing():
    tier, origin = rg.resolve_tier("thurough")
    assert tier == rg.DEFAULT_TIER
    assert "unrecognised" in origin
    assert rg.rigor_for("gate", tier="thurough").total_binding >= 1


def test_an_unknown_site_class_resolves_to_the_strictest(capsys):
    """An authoring typo must cost too much verification, never too little."""
    a = rg.rigor_for("gaet")
    assert a.site_class == "gate"
    assert "unknown site class" in capsys.readouterr().err


def test_precedence_is_argument_then_env_then_file(monkeypatch, tmp_path):
    cfg = tmp_path / "q-rigor.json"
    monkeypatch.setenv(rg.CONFIG_ENV, str(cfg))
    rg.write_config("minimal", set_by="test")
    assert rg.resolve_tier()[0] == "minimal"

    monkeypatch.setenv(rg.TIER_ENV, "light")
    assert rg.resolve_tier()[0] == "light", "the export must outrank the stored file"
    assert rg.resolve_tier("thorough")[0] == "thorough", "an argument outranks both"


def test_a_corrupt_config_is_not_fatal(monkeypatch, tmp_path):
    cfg = tmp_path / "q-rigor.json"
    cfg.write_text("{not json at all")
    monkeypatch.setenv(rg.CONFIG_ENV, str(cfg))
    assert rg.resolve_tier()[0] == rg.DEFAULT_TIER


def test_write_config_refuses_an_unknown_tier(tmp_path, monkeypatch):
    monkeypatch.setenv(rg.CONFIG_ENV, str(tmp_path / "q-rigor.json"))
    with pytest.raises(ValueError):
        rg.write_config("cheap")
    assert not (tmp_path / "q-rigor.json").exists()


def test_the_config_lives_beside_the_module_not_in_the_environment(monkeypatch):
    """Two install modes must not be able to disagree about which file is theirs."""
    monkeypatch.delenv(rg.CONFIG_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/nowhere-in-particular")
    assert rg.config_path() == RIGOR_PY.resolve().parent.parent / rg.CONFIG_BASENAME


# --------------------------------------------------------------------------
# Panels: a cap, never a replacement
# --------------------------------------------------------------------------

def test_a_panel_is_never_inflated_above_its_authored_count():
    """The dial is a budget ceiling. Raising a skill's own design would change
    what the skill DOES at a tier the operator picked for cost."""
    for tier in rg.TIERS:
        for authored in (1, 2, 3, 5):
            n = int(rg.rigor_for("panel", authored=authored, tier=tier).flags.rsplit(" ", 1)[1])
            assert 1 <= n <= authored


def test_an_authored_default_of_one_survives_every_tier():
    """`/recommend` proposes one bounded move; no tier may turn that into a panel."""
    for tier in rg.TIERS:
        assert rg.rigor_for("panel", authored=1, tier=tier).flags.endswith(" 1")


def test_a_wide_panel_narrows_at_the_cheap_tiers():
    """Otherwise the cap would be decorative."""
    assert rg.rigor_for("panel", authored=2, tier="standard").flags == "--opus 2"
    assert rg.rigor_for("panel", authored=2, tier="minimal").flags == "--opus 1"


def test_panel_needs_its_authored_count():
    with pytest.raises(ValueError):
        rg.rigor_for("panel")


# --------------------------------------------------------------------------
# Flag rendering
# --------------------------------------------------------------------------

def test_an_absent_family_is_omitted_rather_than_passed_as_zero():
    """`--sonnet 0` reads as a malformed binding count to the engine; the
    contract is that an absent family is ABSENT."""
    for tier in rg.TIERS:
        for cls in ("gate", "check"):
            assert " 0" not in rg.rigor_for(cls, tier=tier).flags


def test_only_a_multi_checker_tier_claims_a_vote():
    """The vote/no-vote distinction is the honest part of the dial; pinning it
    stops a later edit from calling one isolated opinion a consensus."""
    for cls in ("gate", "check"):
        for tier in rg.TIERS:
            a = rg.rigor_for(cls, tier=tier)
            assert a.vote is (a.total_binding > 1), f"{cls}/{tier}"


# --------------------------------------------------------------------------
# Conformance — the code check that a skill did not go back to naming numbers
# --------------------------------------------------------------------------

@pytest.mark.skipif(not SKILLS.is_dir(), reason="no skills tree beside this module")
def test_no_shipped_skill_names_a_checker_count_inline():
    findings = rg.conformance(HOOKS.parent)
    assert findings == [], "\n".join(str(f) for f in findings)


def test_the_conformance_scan_is_not_vacuous(tmp_path):
    """Plant a hard-coded allocation and require it to be found.

    Without this the scan could be passing because it reads nothing — which is
    exactly how an earlier gate in this release reported "0 leaks".
    """
    (tmp_path / "skills" / "planted").mkdir(parents=True)
    (tmp_path / "skills" / "planted" / "SKILL.md").write_text(
        "Dispatch `/double-check --sonnet 3 --opus 1 --rounds 2`.\n")
    findings = rg.conformance(tmp_path)
    assert len(findings) == 1
    assert findings[0].path == "skills/planted/SKILL.md"


def test_the_positional_shorthand_is_caught_too(tmp_path):
    (tmp_path / "skills" / "p").mkdir(parents=True)
    (tmp_path / "skills" / "p" / "SKILL.md").write_text("runs /double-check 3,1,1 first\n")
    assert len(rg.conformance(tmp_path)) == 1


def test_a_line_waiver_needs_a_reason(tmp_path):
    (tmp_path / "skills" / "p").mkdir(parents=True)
    f = tmp_path / "skills" / "p" / "SKILL.md"
    f.write_text("`/recommend --opus 3` <!-- rigor-ok: -->\n")
    assert len(rg.conformance(tmp_path)) == 1, "a bare marker must not waive"
    f.write_text("`/recommend --opus 3` <!-- rigor-ok: a worked override example -->\n")
    assert rg.conformance(tmp_path) == []


def test_an_exempt_file_is_exempt_for_a_stated_reason():
    """Every register entry carries its reason — an unexplained exemption is how
    a register turns back into a blanket."""
    for rel, reason in rg.CONFORMANCE_EXEMPT.items():
        assert rel.startswith("skills/")
        assert len(reason) > 30, f"{rel}: reason too thin to audit"


# --------------------------------------------------------------------------
# The CLI the skills actually call
# --------------------------------------------------------------------------

def _cli(*args, env=None):
    e = dict(os.environ)
    e.pop(rg.TIER_ENV, None)
    e.update(env or {})
    return subprocess.run([sys.executable, str(RIGOR_PY), *args],
                          capture_output=True, text=True, env=e)


def test_for_prints_only_the_flags_so_a_skill_can_paste_them(tmp_path):
    r = _cli("--tier", "thorough", "for", "gate",
             env={rg.CONFIG_ENV: str(tmp_path / "c.json")})
    assert r.returncode == 0
    assert r.stdout.strip() == "--sonnet 3 --opus 1"


def test_cap_count_only_prints_a_bare_number(tmp_path):
    r = _cli("--tier", "minimal", "cap", "3", "--count-only",
             env={rg.CONFIG_ENV: str(tmp_path / "c.json")})
    assert r.returncode == 0
    assert r.stdout.strip() == "1"


def test_set_then_get_round_trips(tmp_path):
    cfg = str(tmp_path / "c.json")
    assert _cli("set", "light", env={rg.CONFIG_ENV: cfg}).returncode == 0
    r = _cli("get", env={rg.CONFIG_ENV: cfg})
    assert r.stdout.startswith("light")
    assert json.loads(Path(cfg).read_text())["tier"] == "light"


def test_set_warns_when_an_export_would_shadow_the_file(tmp_path):
    r = _cli("set", "light", env={rg.CONFIG_ENV: str(tmp_path / "c.json"),
                                  rg.TIER_ENV: "minimal"})
    assert r.returncode == 0
    assert "takes precedence" in r.stderr


def test_selftest_and_conformance_exit_zero_on_the_shipped_tree():
    assert _cli("selftest").returncode == 0
    assert _cli("conformance", str(HOOKS.parent)).returncode == 0

def test_plan_gates_are_off_the_dial_and_are_not_the_fixed_class():
    """Operator decision, 2026-09-30: plan-mode gates stay locked, and `fixed` is not it.

    Three shipped surfaces disagreed before this. `/double-check` listed plan gates as an
    example of the `fixed` class; `factcheck-convergence.md` and `skills/plan/SKILL.md`
    both state a different allocation for the plan kind. Since `fixed` returns 3 Sonnet
    and plan's canon is 1 Sonnet + 1 Opus (3 + 1 for coherency), a caller following that
    row would have dispatched a number the canon does not specify.

    Two assertions, because one alone goes vacuous in opposite directions: the row must
    not name plan gates, and `fixed` must keep returning something plan's canon does NOT
    say — if the two ever coincide, the reason for the separation is gone and this test
    should be revisited rather than silently passing.
    """
    root = Path(__file__).resolve().parent.parent.parent
    dc = root / "skills" / "double-check" / "SKILL.md"
    if not dc.is_file():
        pytest.skip("no double-check skill in this tree")
    text = dc.read_text(encoding="utf-8")

    for line in text.splitlines():
        if line.startswith("| `fixed`"):
            assert "plan gate" not in line.lower(), (
                "the `fixed` row names plan gates again; `fixed` returns "
                f"{rg.FIXED_CANON_FLAGS!r}, which is not plan's canon allocation"
            )
            break
    else:
        pytest.fail("no `fixed` row found in the /double-check class table")

    # the separation must still be real
    assert rg.rigor_for("fixed").flags == "--sonnet 3"
    assert "opus" not in rg.rigor_for("fixed").flags, (
        "`fixed` now includes Opus — if it has converged on plan's 3+1 canon, the "
        "reason plan gates are held separate no longer holds and the decision needs "
        "revisiting rather than this test quietly passing"
    )

    # and the decision has a recorded home
    assert "skills/plan/SKILL.md" in rg.OFF_DIAL

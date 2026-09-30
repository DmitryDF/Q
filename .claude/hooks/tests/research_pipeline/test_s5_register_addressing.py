"""Register-addressing tests for A5's other two validation-gate checks
(research-entry-point-enforcement Slice S5).

A5's own Validation-gate cell ("Coherent Actions" row "A5 (S5): One register
addresser; declared path validated where it enters" in
`Thoughts/research-entry-point-enforcement-20260921093248_PLAN.md`) names
THREE checks:

  1. "Adversarial tests: `~/.ssh/id_rsa`, `../../x`, a symlink out, a null
     byte — all refused at intake."
  2. "The same research file yields one address from both former derivation
     sites."
  3. "Generated names satisfy `bookkeeping_invariant.py`'s grammar (no `--`)."

Check 1 is committed in the sibling module `test_s5_intake_path_validation.py`
(its own docstring scopes it to `validate_research_file_path` /
`validate_schema("r0_intake", ...)` — intake-time path validation, a different
concern from what a register's own NAME looks like). Checks 2 and 3 are about
register ADDRESSING — where a research file's `_CLAIMS.md` register lives and
whether its generated name is grammar-conforming — and were guarded only by a
throwaway scratch script the S5 round-5 gap report ran by hand. This module
makes them committed, re-runnable pytest.

**The two former derivation sites**, per Design Review deviation (13) of that
same plan file:

  * The harvest: `_claim_harvest_trigger._topic_paths(research_path)` resolves
    its `"register"` key through `claims_registry.register_address(...)`
    directly.
  * The engine: `_factcheck_engine.factcheck_run`'s register-creation block
    (search "bookkeeping-model drift Row 6") does NOT call `register_address`
    at all — deviation (13) records that "both resolve through the addresser"
    overstates it: the engine calls `_research_display_slug(draft_path,
    raw=(kind != "research"))` to get its own display key, then
    `claims_registry.register_slug()` over that key — the NAMING half of the
    addresser, not the addresser itself — then `ensure_exists(_dir, _slug)`
    where `_dir = os.path.dirname(os.path.abspath(str(draft_path)))`.
    `_engine_register_path` below reproduces that composition by calling the
    SAME two real functions (`_research_display_slug` +
    `claims_registry.register_slug`) the engine calls, so it catches drift in
    either function's own naming contract.

    **It does NOT guard the engine's own wiring at that call site** — a
    corrected claim, made after an independent post-closing review found the
    prior version of this paragraph asserted the opposite. `_engine_register_
    path` never invokes `factcheck_run` itself, so deleting the engine's call
    into `register_slug()` (or skipping it, or misordering it) leaves every
    assertion in this module built on `_engine_register_path` passing
    regardless — confirmed by probe: deleting `_factcheck_engine.py`'s
    `register_slug()` call recreates the duplicate, ungrammatical,
    permanently-empty `foo--bar_CLAIMS.md` skeleton (findings 29/34) with
    every test that existed in this module beforehand still green. Guarding
    the engine's own wiring requires actually driving `factcheck_run`, which
    `test_engine_registers_scope_keyed_research_at_slug_family_register`
    below does, following `test_bookkeeping_ds7_engine.py`'s shape for
    invoking it (stub the checker; tolerate a downstream dispatch error;
    assert the round-0 side effect).

**Why generated names classify `standard`/`CLAIMS` with no `--`**, per
deviation (16): `CLAIMS` is a STANDARD type, not a MULTI one
(`bookkeeping_invariant.MULTI_TYPES` excludes it), so there is no
scope-keyed register shape the grammar recognises — `<slug>_<SCOPE>_CLAIMS.md`
classifies `non_member` exactly as `<slug>--<scope>_CLAIMS.md` classifies
`out_of_scope`. The register is therefore named for the slug family alone
(one register per slug family) and the scope survives only as the returned
`area` — never as part of the filename. `test_scope_keyed_and_bare_sibling_
share_one_register` below pins that property directly, since it is the
concrete case deviation (16) exists to explain.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Self-relative resolution, matching the six S4/S5 siblings in this directory
# that resolve HOOKS_DIR this way rather than via Path.home() (see
# test_s5_intake_path_validation.py's own comment on this point — a
# Path.home()-rooted import always reads the LIVE tree regardless of which
# tree is under test).
HOOKS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOKS_DIR))

import bookkeeping_invariant as bi           # noqa: E402
import claims_registry as cr                 # noqa: E402
import _claim_harvest_trigger as harvest     # noqa: E402
import _factcheck_engine as fe               # noqa: E402


def _engine_register_path(draft_path: str) -> str:
    """Reproduce `_factcheck_engine.factcheck_run`'s research-kind register
    composition exactly (see module docstring), rather than calling
    `claims_registry.register_address` a second time — that would test the
    addresser against itself, not against the engine's own site.
    """
    _dir = os.path.dirname(os.path.abspath(str(draft_path)))
    _slug = fe._research_display_slug(draft_path, raw=False)  # kind == "research"
    _slug = cr.register_slug(_slug)
    return os.path.join(_dir, f"{_slug}_CLAIMS.md")


def _harvest_register_path(draft_path: str) -> str:
    """The harvest's own site: `_topic_paths(...)["register"]`."""
    paths = harvest._topic_paths(Path(draft_path))
    assert paths is not None, (
        f"_topic_paths returned None (no resolvable register) for {draft_path!r}"
    )
    return str(paths["register"])


# ── the five input classes A5's guard rails distinguish, across six
#    parametrized filenames below ───────────────────────────────────────────
#
#   * grammar-conforming, no scope
#   * grammar-conforming, scope-keyed (the case the two sites used to disagree on)
#   * grammar-conforming, multi-segment scope
#   * grammar-REJECTED (uppercase + underscore-bearing) — the class where the
#     harvest formerly produced no address at all (finding 34 / module header)
#   * grammar-REJECTED, uppercase-slug only — covers BOTH
#     `uppercase-and-untimestamped` and `uppercase-slug` below; they are the
#     same class for every branch the code takes (this off-by-one was caught
#     by an independent post-closing review — six params were listed against
#     five bullets). Kept as two separate params for direct traceability to
#     the two research filenames the S5 round-5 gap report actually used.

RESEARCH_FILENAMES = [
    pytest.param("foo-20260101000000_RESEARCH.md", id="no-scope"),
    pytest.param("foo-20260101000000_BAR_RESEARCH.md", id="scope-keyed"),
    pytest.param("foo-20260101000000_H_v2_B1_RESEARCH.md", id="multi-segment-scope"),
    pytest.param("Visuals_RESEARCH.md", id="uppercase-and-untimestamped"),
    pytest.param(
        "Example_cockpit-error-handling_RESEARCH.md",
        id="uppercase-underscore-bearing",
    ),
    pytest.param("ALPHA-20260101000000_RESEARCH.md", id="uppercase-slug"),
]


@pytest.mark.parametrize("filename", RESEARCH_FILENAMES)
def test_both_former_sites_yield_one_address(tmp_path, filename):
    """"The same research file yields one address from both former derivation
    sites" — A5's validation-gate wording, verbatim.
    """
    draft_path = str(tmp_path / filename)

    engine_path = _engine_register_path(draft_path)
    harvest_path = _harvest_register_path(draft_path)

    assert engine_path == harvest_path, (
        f"engine and harvest disagree for {filename!r}: "
        f"engine={engine_path!r} harvest={harvest_path!r}"
    )

    # And both agree with the addresser's own public entry point, since that
    # is what the harvest calls and what the engine's composition is meant to
    # match.
    addr = cr.register_address(draft_path)
    assert addr.path == engine_path


@pytest.mark.parametrize("filename", RESEARCH_FILENAMES)
def test_generated_name_satisfies_bookkeeping_grammar(tmp_path, filename):
    """"Generated names satisfy `bookkeeping_invariant.py`'s grammar (no `--`)"
    — A5's validation-gate wording, verbatim.

    MINOR 3 (S5 fixer review): grounded in `cr.register_address(...)` — a real
    production entry point (the harvest's own call site) — rather than in
    `_engine_register_path`'s hand-composed string. Asserting against a name
    the test itself constructed was self-fulfilling; asserting against the
    addresser's actual return value is not.
    """
    draft_path = str(tmp_path / filename)
    addr = cr.register_address(draft_path)
    basename = os.path.basename(addr.path)

    assert "--" not in basename, (
        f"generated register name {basename!r} for {filename!r} contains "
        f"'--' — the exact shape the grammar (Bucket 1 STANDARD, not MULTI) "
        f"rejects as out_of_scope"
    )

    membership = bi.classify(basename)
    assert membership.bucket == bi.B_STANDARD, (
        f"{basename!r} classified bucket={membership.bucket!r}, expected "
        f"{bi.B_STANDARD!r} (deviation (16): CLAIMS is a STANDARD type, not "
        f"MULTI — there is no scope-keyed register shape)"
    )
    assert membership.type == "CLAIMS"
    assert membership.slug == addr.slug


def test_scope_keyed_and_bare_sibling_share_one_register(tmp_path):
    """Deviation (16)'s concrete case: a scope-keyed research file and its
    bare-name sibling of the same slug family land in the SAME register (one
    per slug family) — the scope survives only as the returned `area`, never
    as part of the register's filename. This is Q6 "read literally": the
    slug BINDS the register; the area RECORDS what the research was for.
    """
    bare = str(tmp_path / "foo-20260101000000_RESEARCH.md")
    scoped = str(tmp_path / "foo-20260101000000_BAR_RESEARCH.md")

    bare_addr = cr.register_address(bare)
    scoped_addr = cr.register_address(scoped)

    # Same register path, same slug — one file per slug family, not two.
    assert bare_addr.path == scoped_addr.path
    assert bare_addr.slug == scoped_addr.slug == "foo"

    # The scope survives as `area`, and ONLY as `area` — it is not folded
    # into the register's name (the generated basename carries no scope
    # segment for either file).
    assert bare_addr.area is None
    assert scoped_addr.area == "bar"
    assert os.path.basename(bare_addr.path) == "foo_CLAIMS.md"

    # Both former derivation sites agree on the register PATH (not the area —
    # the engine site computes no area at all; only `register_address` does,
    # and only that function's return value is checked against `.area` above).
    assert _engine_register_path(bare) == bare_addr.path
    assert _engine_register_path(scoped) == scoped_addr.path
    assert _harvest_register_path(bare) == bare_addr.path
    assert _harvest_register_path(scoped) == scoped_addr.path


def test_engine_registers_scope_keyed_research_at_slug_family_register(tmp_path):
    """BLOCKER 1 fix (S5 fixer review): drive `_factcheck_engine.factcheck_run`
    ITSELF — not a re-composition of its site — over a scope-keyed research
    draft, and assert the register it creates lands at the slug family's name
    with no `--` join.

    Every other test in this module drives `_engine_register_path`, which
    hand-composes the engine's derivation by calling the same two functions
    (`_research_display_slug` + `claims_registry.register_slug`) the engine
    calls, but never calls `factcheck_run` itself — so nothing in this module
    previously exercised the engine's own wiring at its register-creation call
    site (search "bookkeeping-model drift Row 6" in `_factcheck_engine.py`).
    A scope-keyed research file is the shape whose fold — `_research_display_
    slug`'s `--` scope join, folded back down to the slug family by
    `claims_registry.register_slug()` — is observable AT THIS SITE, on disk,
    as the difference between one register and two. It is not the only shape
    `register_slug()` changes the outcome for in general: a fallback-branch
    input carrying a trailing timestamp is another (see
    `test_addresser_slug_matches_classify_of_its_own_register_name` below),
    and a doubled trailing timestamp on either branch is a further one (see
    `claims_registry._TRAILING_TS_RE`). This test drives the one shape whose
    register-creation side effect is visible at the engine's own call site,
    not the only input class the fold affects.

    Confirmed by probe (not asserted from the plan text alone): deleting
    `_factcheck_engine.py`'s `_slug = _cr.register_slug(_slug)` call recreates
    the duplicate, ungrammatical, permanently-empty `foo--bar_CLAIMS.md`
    skeleton (findings 29/34) the addresser exists to end, while every OTHER
    test in this module — none of which drives `factcheck_run` — stays green.
    This test fails on that deletion because it is the only one that actually
    calls `factcheck_run` and inspects what it wrote to disk.

    Follows `test_bookkeeping_ds7_engine.py`'s shape for invoking
    `factcheck_run`: stub the checker with a PASS response, tolerate a
    downstream-dispatch exception (the round-0 register-creation side effect
    already ran by the time any such exception could occur), and assert the
    on-disk result.
    """
    draft = tmp_path / "Thoughts" / "demo_BAR_RESEARCH.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# research\n")

    try:
        fe.factcheck_run(
            str(tmp_path / "state"), str(draft), "research", "sess",
            debounce_seconds=0, models=["sonnet"],
            _checker_fn=lambda *a, **k: '{"verdict":"PASS","discrepancies":[]}',
            proj="proj", topic="demo",
        )
    except Exception:
        pass  # round-0 side-effect already ran; downstream dispatch is not under test

    family_register = draft.parent / "demo_CLAIMS.md"
    scope_joined_register = draft.parent / "demo--bar_CLAIMS.md"

    assert family_register.exists(), (
        f"expected the engine to create {family_register.name!r} for a "
        f"scope-keyed research file — if this fails, "
        f"`_factcheck_engine.factcheck_run`'s call into "
        f"`claims_registry.register_slug()` is not running"
    )
    assert not scope_joined_register.exists(), (
        f"the engine created {scope_joined_register.name!r} — the exact "
        f"duplicate, ungrammatical, permanently-empty skeleton "
        f"(findings 29/34) `claims_registry.register_slug()` exists to "
        f"prevent"
    )

    membership = bi.classify(family_register.name)
    assert membership.bucket == bi.B_STANDARD and membership.type == "CLAIMS"
    assert membership.slug == "demo"


# ── MAJOR 2 (S5 fixer review): the fallback branch used to leave a trailing
#    14-digit timestamp embedded in the register's slug, which `classify()`
#    then strips back off when re-parsing the register's own filename —
#    a mismatch that made `_spine_stem` search for a spine under the wrong
#    slug and mint no `Parent:` line (finding 16's defect, recreated). Fixed
#    in `claims_registry.register_slug` (a trailing-timestamp strip), not in
#    the engine (see that function's docstring for the BOUND reasoning).
FALLBACK_TIMESTAMP_PROBES = [
    pytest.param("my.topic-20260101000000_RESEARCH.md", id="dotted-slug-with-ts"),
    pytest.param("a b-20260101000000_RESEARCH.md", id="space-in-slug-with-ts"),
    pytest.param("topic.with.dots_RESEARCH.md", id="dotted-slug-no-ts-control"),
    pytest.param("foo-20260101000000_RESEARCH.md", id="grammar-path-control"),
    pytest.param(
        "my.topic-20260101000000-20260101000000_RESEARCH.md",
        id="dotted-slug-doubled-ts",
    ),
    pytest.param(
        "foo-20260101000000-20260101000000_RESEARCH.md",
        id="grammar-path-doubled-ts",
    ),
]


@pytest.mark.parametrize("filename", FALLBACK_TIMESTAMP_PROBES)
def test_addresser_slug_matches_classify_of_its_own_register_name(tmp_path, filename):
    """MAJOR 2 fix (widened by the S5 closing review's MINOR 3): `register_
    address(...).slug` must equal `bookkeeping_invariant.classify(basename(
    register_path)).slug` for every input in this list — otherwise
    `_spine_stem` searches the family for a spine under the wrong slug and
    mints no `Parent:` line.

    The first two cases are FALLBACK-branch inputs the bookkeeping grammar
    rejects at the stem level (a dotted slug, a space in the slug) AND that
    carry a trailing 14-digit timestamp — the exact combination that used to
    mismatch: `register_slug` left the timestamp in the register's name while
    `classify()` strips it back off when re-parsing that same name. The next
    two are controls that must stay unaffected: a fallback input with no
    timestamp (never mismatched), and a single-timestamp grammar-conforming
    input — for THIS shape, `classify()` splits the one trailing timestamp
    off before this function ever sees it, so there is nothing left for the
    strip to do here.

    That "nothing left to do" holds for a single trailing timestamp only, not
    for "a grammar-conforming input" in general: the last two cases are a
    FALLBACK input and a grammar-conforming input each carrying a DOUBLED
    trailing timestamp, where `classify()`'s greedy-timestamp/lazy-slug
    grammar splits off only the last segment, leaving one more embedded in
    the slug either branch hands back — the same class of mismatch the first
    two cases pin, one segment further out (see `claims_registry.
    _TRAILING_TS_RE`). No such doubled-timestamp name exists in the corpus;
    these two cases exist because the invariant above is asserted
    universally, not because the shape is reachable today.
    """
    draft_path = str(tmp_path / filename)
    addr = cr.register_address(draft_path)
    membership = bi.classify(os.path.basename(addr.path))
    assert addr.slug == membership.slug, (
        f"{filename!r}: addr.slug={addr.slug!r} but "
        f"classify(register).slug={membership.slug!r} — a spine search under "
        f"addr.slug would never find a real spine, which classifies under "
        f"membership.slug instead"
    )

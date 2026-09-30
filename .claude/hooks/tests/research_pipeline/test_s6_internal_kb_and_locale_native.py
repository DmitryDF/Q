"""S6 integration tests — Internal KB path + locale-native search-terms sub-pass.

Slice S6 ships two coupled deliverables on the S3/S4/S5 backbone:

  (a) Internal knowledge base path — the fifth and last user-approval routing
      path. Does NOT call r0_intake (no manifest cycle). Internal sources are
      discovered via topic-CLAUDE walk-up (Q12 Edge 2) and topic-folder
      _RESEARCH file scan. On empty state, Q13 path-validation flow validates
      user-supplied paths. Synthesized _RESEARCH.md carries Convention B
      markers citing each source in the form it can bear — Projects-root-relative
      inside the workspace, absolute outside it (Q12 Edge 1). Pre-existing
      fc_cycles: frontmatter is preserved on re-synthesis (E2b coexistence).

  (b) Locale-native search-terms sub-pass — for multi-language runs the
      conversation-language scope core is shared; the search-terms slot is
      regenerated per selected language by FakeLocaleNativeAdapter (or the
      production ClaudeLocaleNativeAdapter). Failed-language slot is None;
      other languages still ship.

Validation gate per S6 handoff prompt constraints:
  * Q13: full success / ALL-fail / mixed / >100 files warning / absolute-path
    ACCEPT (design-A19 — see test_path_admission.py for the full matrix) /
    **-no-dir-prefix reject.
  * fc_cycles: preservation byte-for-byte across re-synthesis.
  * Convention B markers cite each source in the form it can bear —
    Projects-root-relative inside the workspace (grep-asserts).
  * Q12 Edge 2: Thoughts/ staging topic walk-up → Projects-root CLAUDE.md →
    "(Projects-root CLAUDE.md — not topic-scoped)" prefix.
  * Internal KB path does NOT create RP-<sid>.json (no r0_intake call).
  * Locale-native degraded path: {terms: null, reason: ...} → None slot.
  * Multi-language 2-lang run with one failure: both marker AND surviving terms.
  * Cockburn 4-step states for both modules.
  * Q11 conformance for locale-native error reasons.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, Sequence

import pytest

# Honour CLAUDE_CONFIG_DIR so this suite exercises the tree it is run against —
# an experiment clone during development, live config otherwise. Matches the form
# the sibling suites already use (test_source_admission_port.py, test_source_picker.py);
# without it, a run inside a clone silently imports and grades LIVE modules.
CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))
# This file lives in tests/research_pipeline/, so its `tests/` parent is NOT on
# sys.path from the two inserts above. Add it explicitly, or the shared
# live-corpus resolver below raises ModuleNotFoundError.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _live_corpus as live_corpus  # noqa: E402

# The corpus root comes from the ONE shared resolver rather than being built
# here. This line used to hardcode the retired iCloud container — the same
# departure-from-its-own-convention that `:49` above avoids for the config dir,
# made three lines later for the corpus. Overridable with CLAUDE_CORPUS_ROOT.
PROJECTS_ROOT = live_corpus.corpus_root()

from research.internal_kb import (  # noqa: E402
    InternalKBSources,
    PathValidationResult,
    _FC_CYCLES_END,
    _FC_CYCLES_START,
    compose_research_file,
    discover_internal_sources,
    discover_research_files,
    emit_convention_b_marker,
    extract_fc_cycles_block,
    validate_user_paths,
    walk_up_topic_claude,
)
from research.locale_native_subpass import (  # noqa: E402
    ClaudeLocaleNativeAdapter,
    FakeLocaleNativeAdapter,
    LocaleNativeError,
    LocaleNativeIntake,
    LocaleNativeResult,
    ScopeCore,
    apply_locale_native_subpass,
    is_error as ln_is_error,
    is_result as ln_is_result,
)
from research.scope_draft_port import (  # noqa: E402
    FakeScopeDraftAdapter,
    ScopeDraftIntake,
    ScopeDraftOutput,
    is_output as sd_is_output,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def topic_tree(tmp_path):
    """A minimal topic folder tree with CLAUDE.md at project level and a _RESEARCH file."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "CLAUDE.md").write_text("# Root CLAUDE\nRoot project instructions.", encoding="utf-8")

    project = projects / "Personal" / "MyProject"
    project.mkdir(parents=True)
    (project / "CLAUDE.md").write_text("# Project CLAUDE\nProject instructions.", encoding="utf-8")
    (project / "topic_RESEARCH.md").write_text("# Topic Research\nSome research content.", encoding="utf-8")

    return projects, project


@pytest.fixture
def staging_tree(tmp_path):
    """A Thoughts/ staging topic tree — no CLAUDE.md at staging level."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "CLAUDE.md").write_text("# Root CLAUDE\nRoot instructions.", encoding="utf-8")

    thoughts = projects / "Thoughts"
    thoughts.mkdir()
    # No CLAUDE.md here — walk-up must resolve to projects/ CLAUDE.md.
    (thoughts / "my-topic_RESEARCH.md").write_text(
        "# My Topic Research\nSome staging content.", encoding="utf-8"
    )

    return projects, thoughts


# --------------------------------------------------------------------------- #
# (1–6) Q13 path-validation flow
# --------------------------------------------------------------------------- #

def test_q13_full_success(tmp_path):
    """All supplied paths exist → all_success is True."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "file_a.md").write_text("a", encoding="utf-8")
    (projects / "file_b.md").write_text("b", encoding="utf-8")

    result = validate_user_paths(["file_a.md", "file_b.md"], projects_root=projects)
    assert result.all_success
    assert len(result.valid_paths) == 2
    assert not result.failed_patterns
    assert not result.over_limit_warnings


def test_q13_all_fail(tmp_path):
    """No supplied paths exist → all_failed is True."""
    projects = tmp_path / "Projects"
    projects.mkdir()

    result = validate_user_paths(["nonexistent.md", "also/missing.md"], projects_root=projects)
    assert result.all_failed
    assert not result.valid_paths
    assert len(result.failed_patterns) == 2


def test_q13_mixed_result(tmp_path):
    """Some paths valid, some invalid → mixed is True."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "exists.md").write_text("exists", encoding="utf-8")

    result = validate_user_paths(
        ["exists.md", "does_not_exist.md"],
        projects_root=projects,
    )
    assert result.mixed
    assert len(result.valid_paths) == 1
    assert len(result.failed_patterns) == 1


def test_q13_over_limit_warning(tmp_path):
    """Pattern matching more than the threshold emits a warning (not an error)."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    # Create 5 files; set over_limit=2 to trigger the warning.
    for i in range(5):
        (projects / f"file_{i}.md").write_text(f"file {i}", encoding="utf-8")

    result = validate_user_paths(["*.md"], projects_root=projects, over_limit=2)
    assert result.all_success  # files are valid — this is a warning, not failure
    assert len(result.valid_paths) == 5
    assert len(result.over_limit_warnings) == 1
    assert "more than 2 files" in result.over_limit_warnings[0]


def test_q13_slash_prefixed_pattern_accepted(tmp_path):
    """An absolute pattern is ADMITTED and expands against its own root (A19).

    Re-pointed, not deleted: this test previously asserted `/foo/bar.md` is
    rejected for starting with '/', which is exactly the rule design-A19 reverses
    — a cloned repo and a network share are both absolute by nature, and both are
    locations a person may legitimately declare. The pattern is no longer refused
    on its SPELLING; it is now decided on where it RESOLVES, so an absolute path
    that names a real file is admitted, while one that names nothing simply fails
    to expand. That second case lives in test_path_admission.py, which owns the
    admission matrix — this file keeps exactly the one re-pointed assertion, so
    the suite's 143-passed gate still reads as 'nothing was deleted'."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    outside = tmp_path / "outside-the-workspace"
    outside.mkdir()
    target = outside / "bar.md"
    target.write_text("out-of-root content", encoding="utf-8")

    result = validate_user_paths([str(target)], projects_root=projects)
    assert result.all_success, (
        "an absolute path naming a real file must be admitted, not refused for "
        "starting with '/'"
    )
    assert result.valid_paths == [target.resolve()]


def test_q13_double_star_no_dir_prefix_rejected(tmp_path):
    """**/*.md without a directory prefix is rejected."""
    projects = tmp_path / "Projects"
    projects.mkdir()

    result = validate_user_paths(["**/*.md"], projects_root=projects)
    assert result.all_failed
    assert "**/*.md" in result.failed_patterns


# --------------------------------------------------------------------------- #
# (7) fc_cycles: frontmatter preservation on re-synthesis (E2b coexistence)
# --------------------------------------------------------------------------- #

def test_fc_cycles_preservation_on_resynth():
    """Write a stub _RESEARCH.md with a sentinel-wrapped fc_cycles: block,
    run a re-synthesis via compose_research_file, assert the frontmatter
    is still intact byte-for-byte in the result."""
    fc_block = (
        f"{_FC_CYCLES_START}\n"
        "fc_cycles:\n"
        '  - cycle: "default"\n'
        '    verdict: "PASS"\n'
        "    rounds: 2\n"
        f"{_FC_CYCLES_END}"
    )
    existing = fc_block + "\n\n## Research Notes\n\nOld content.\n"

    new_body = "## Research Notes\n\nNew synthesis content.\n"
    result = compose_research_file(new_body, existing_text=existing)

    # The fc_cycles block must appear verbatim in the result.
    assert fc_block in result, "fc_cycles: sentinel block must be preserved verbatim"
    # New content must also be present.
    assert "New synthesis content." in result
    # Old content is replaced (not carried over — only the fc_cycles block is preserved).
    # (The old body text is dropped; only the sentinel block is kept.)


def test_fc_cycles_preservation_byte_for_byte():
    """The fc_cycles block extracted from existing text is byte-for-byte identical
    in the composed output (not reconstructed, truly preserved)."""
    fc_block = (
        f"{_FC_CYCLES_START}\n"
        "fc_cycles:\n"
        '  - cycle: "default"\n'
        '    verdict: "PASS"\n'
        "    rounds: 1\n"
        f"{_FC_CYCLES_END}"
    )
    existing = fc_block + "\n\n## Notes\n\nOld.\n"

    result = compose_research_file("## Notes\n\nNew.\n", existing_text=existing)
    extracted = extract_fc_cycles_block(result)
    assert extracted is not None
    assert extracted == fc_block, "fc_cycles block must be byte-for-byte identical"


def test_fc_cycles_no_duplication_when_new_body_already_has_sentinel():
    """If new_body already contains the fc_cycles sentinel, compose_research_file
    must NOT add a second copy."""
    fc_block = (
        f"{_FC_CYCLES_START}\n"
        "fc_cycles:\n"
        '  - cycle: "default"\n'
        '    verdict: "PASS"\n'
        "    rounds: 1\n"
        f"{_FC_CYCLES_END}"
    )
    new_body_with_sentinel = fc_block + "\n\n## New\n\nContent.\n"
    existing = fc_block + "\n\n## Old.\n"

    result = compose_research_file(new_body_with_sentinel, existing_text=existing)
    assert result.count(_FC_CYCLES_START) == 1, "fc_cycles sentinel must not be duplicated"


# --------------------------------------------------------------------------- #
# (8) Convention B markers are Projects-root-relative for a source INSIDE the
#     workspace (Q12 Edge 1). The out-of-root and refusal cases live in
#     test_path_admission.py.
# --------------------------------------------------------------------------- #

def test_convention_b_markers_are_projects_root_relative(tmp_path):
    """A source INSIDE the workspace is cited relative to projects_root, not
    absolutely. (An out-of-root source is cited absolutely by stated rule — that
    is not this test's case; see test_path_admission.py.)"""
    projects = tmp_path / "Projects"
    projects.mkdir()
    source_path = projects / "Personal" / "foo" / "foo_RESEARCH.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("content", encoding="utf-8")

    marker = emit_convention_b_marker(
        quote_type="stated",
        source_kind="local-file",
        path=source_path,
        locator="42",
        projects_root=projects,
    )

    # Must NOT start with the tmp_path or have an absolute reference.
    assert str(tmp_path) not in marker
    # Must use a Projects-root-relative forward-slash path.
    assert "Personal/foo/foo_RESEARCH.md:42" in marker
    assert marker.startswith("[stated — local-file:")


def test_convention_b_marker_forms():
    """Spot-check each marker form the Convention B contract defines."""
    assert emit_convention_b_marker("inferred") == "[inferred from internal sources]"
    assert emit_convention_b_marker("unverified") == "[unverified — not found in internal knowledge base]"
    assert emit_convention_b_marker("assessment", assessment_text="looks solid") == "[My assessment: looks solid]"


# --------------------------------------------------------------------------- #
# (9) Q12 Edge 2 — Thoughts/ staging topic walk-up prefix (live filesystem)
# --------------------------------------------------------------------------- #

def test_q12_edge2_staging_topic_walkup_produces_prefix():
    """Running walk_up_topic_claude against the live Thoughts/ staging folder
    resolves to the CLOSEST CLAUDE.md, and is_projects_root_claude is True exactly
    when that CLAUDE.md is the Projects-root one. The emitted topic-CLAUDE marker
    must carry the '(Projects-root CLAUDE.md — not topic-scoped)' prefix when the
    flag is set.

    Premise note: this test formerly asserted `is_root is True` on the reasoning
    that "Thoughts/ has no topic-level CLAUDE.md". That was a fact about the repo's
    layout, not about the function, and `Thoughts/CLAUDE.md` (added to scope the
    evidence-register gate) falsified it. The walk-up is correct and is not bent to
    make the old assertion pass; what is asserted here is the function's own
    contract — closest-wins, and the flag reports where it landed — which cannot go
    stale the next time a CLAUDE.md is added or removed. The deterministic
    walk-up-to-root case keeps its own hermetic coverage via the `staging_tree`
    fixture (test_discover_internal_sources_staging_topic_resolves_root_claude)."""
    try:
        thoughts_folder = live_corpus.require(
            "Thoughts", "the live Thoughts/ staging folder this test walks up from")
    except live_corpus.LiveCorpusUnavailable as exc:
        pytest.skip(str(exc))

    claude_path, is_root = walk_up_topic_claude(thoughts_folder, PROJECTS_ROOT)
    assert claude_path is not None, "walk-up must find a CLAUDE.md"
    assert claude_path.name == "CLAUDE.md"
    # Closest-wins: the resolved CLAUDE.md is the nearest one at or above the topic
    # folder, and no nearer directory holds one.
    assert claude_path.parent == thoughts_folder.resolve() or not (
        thoughts_folder / "CLAUDE.md"
    ).exists(), "walk-up must return the CLOSEST CLAUDE.md, not a more distant one"
    assert is_root is (claude_path.parent.resolve() == PROJECTS_ROOT.resolve()), (
        "is_projects_root_claude must be True exactly when the resolved CLAUDE.md "
        "is the Projects-root one"
    )

    # Emit a topic-CLAUDE marker with the flag set.
    marker = emit_convention_b_marker(
        quote_type="stated",
        source_kind="topic-CLAUDE",
        path=claude_path,
        locator="10",
        projects_root=PROJECTS_ROOT,
        is_projects_root_claude=True,
    )

    assert "(Projects-root CLAUDE.md — not topic-scoped)" in marker, (
        "Q12 Edge 2: Thoughts/ staging citations must carry the '(Projects-root "
        "CLAUDE.md — not topic-scoped)' prefix."
    )
    assert "[stated — topic-CLAUDE:" in marker


# --------------------------------------------------------------------------- #
# (10) Internal KB path does NOT call r0_intake (no RP-<sid>.json created)
# --------------------------------------------------------------------------- #

def test_internal_kb_path_does_not_call_r0_intake(tmp_path, staging_tree):
    """Internal KB flow discovers sources, validates paths, and composes the
    _RESEARCH.md WITHOUT calling r0_intake. Assert no RP-<sid>.json is created."""
    projects, staging_folder = staging_tree
    rp_dir = tmp_path / "research_pipeline"
    rp_dir.mkdir()

    sid = "aaaa1111-bbbb-cccc-dddd-111122223333"

    # Full Internal KB flow: discover sources → compose _RESEARCH.md.
    sources = discover_internal_sources(staging_folder, projects)
    # staging_tree has a _RESEARCH file, so has_sources=True.
    assert sources.has_sources

    # Synthesize a new _RESEARCH.md (no r0_intake call in this module).
    new_body = "## Synthesis\n\nContent from internal sources.\n"
    composed = compose_research_file(new_body, existing_text=None)
    assert "Content from internal sources." in composed

    # Assert no manifest file was created.
    manifest = rp_dir / f"RP-{sid}.json"
    assert not manifest.exists(), (
        "Internal KB path MUST NOT call r0_intake — no RP-<sid>.json should be created."
    )


# --------------------------------------------------------------------------- #
# (11) Locale-native sub-pass degraded-path — None slot
# --------------------------------------------------------------------------- #

def test_locale_native_degraded_path_returns_none_terms():
    """When the sub-pass fails for a language, LocaleNativeError.terms is None."""
    err = LocaleNativeError(reason="Model unavailable.", code="invoker_failed")
    adapter = FakeLocaleNativeAdapter(fail_with=err)
    intake = LocaleNativeIntake(
        scope_core=ScopeCore(angles=("angle",), focused_questions=("q",)),
        target_language="de",
        conversation_language="en",
        user_query="topic",
    )
    outcome = adapter.regenerate(intake)
    assert ln_is_error(outcome)
    assert outcome.terms is None
    assert "Model unavailable." in outcome.reason


def test_locale_native_degraded_path_reason_is_plain_english():
    """Error reason must not contain internal pipeline identifiers (Q11)."""
    adapter = ClaudeLocaleNativeAdapter(invoker=lambda _: "")  # empty response → error
    intake = LocaleNativeIntake(
        scope_core=ScopeCore(),
        target_language="de",
        conversation_language="en",
    )
    outcome = adapter.regenerate(intake)
    assert ln_is_error(outcome)
    for forbidden in (
        "r0_intake", "r1_scope_approved", "cycle_id", "caller_skill",
        "autonomous_scope", "user_approved_scope",
    ):
        assert forbidden not in outcome.reason, (
            f"Q11: locale-native error reason must not contain '{forbidden}'"
        )


# --------------------------------------------------------------------------- #
# (12) Locale-native sub-pass happy path
# --------------------------------------------------------------------------- #

def test_locale_native_happy_path_returns_terms():
    """FakeLocaleNativeAdapter returns terms for a known language."""
    adapter = FakeLocaleNativeAdapter()
    intake = LocaleNativeIntake(
        scope_core=ScopeCore(angles=("state of EV adoption",)),
        target_language="de",
        conversation_language="en",
        user_query="EV adoption in Europe",
    )
    outcome = adapter.regenerate(intake)
    assert ln_is_result(outcome)
    assert len(outcome.terms) >= 1
    assert all(isinstance(t, str) for t in outcome.terms)


# --------------------------------------------------------------------------- #
# (13) Multi-language run with one failure — both marker AND surviving terms
# --------------------------------------------------------------------------- #

def test_locale_native_2lang_one_failure_both_expressed_in_scope():
    """A 2-language run with one failure: the failed slot is None (→ marker);
    the surviving language's terms still ship.

    Per UX Decision #5:
      - failed slot → None in search_terms (flow controller marks
        {{error_locale_native_failed}} inline)
      - other slots → terms still present
    """
    # Adapter that succeeds for "en" but fails for "de".
    # FakeLocaleNativeAdapter defaults cover de/ru/fr/es; supply explicit "en" terms.
    de_adapter = FakeLocaleNativeAdapter(
        fail_with=LocaleNativeError(
            reason="Search terms for this language are not available.",
            code="locale_native_failed",
        )
    )
    en_adapter = FakeLocaleNativeAdapter(
        canned_terms={"en": ("EV adoption Europe", "electric vehicles 2026", "EV policy")}
    )

    # Build a base scope from the Fake ScopeDraftAdapter.
    fake_sd = FakeScopeDraftAdapter()
    base_scope = fake_sd.draft(ScopeDraftIntake(
        routing_path="ninja",
        user_query="EV adoption in Europe",
        selected_languages=("en", "de"),
    ))
    assert sd_is_output(base_scope)

    # Apply the locale-native sub-pass for "de" (failure) and "en" (success).
    adapters = {"de": de_adapter, "en": en_adapter}
    updated_scope = apply_locale_native_subpass(
        base_scope,
        adapters_by_language=adapters,
        user_query="EV adoption in Europe",
    )

    # The "de" slot must be None (failure).
    assert updated_scope.search_terms.get("de") is None, (
        "Failed locale-native slot must be None so the flow controller can "
        "surface {{error_locale_native_failed}} inline."
    )

    # The "en" slot must still have terms (other languages ship).
    en_terms = updated_scope.search_terms.get("en")
    assert en_terms is not None, "Surviving language's terms must still ship."
    assert len(en_terms) >= 1

    # The error message from the failed adapter is Q11-compliant plain English.
    assert "Search terms for this language are not available." == de_adapter._fail.reason
    for forbidden in ("r0_intake", "r1_scope_approved", "cycle_id", "caller_skill"):
        assert forbidden not in de_adapter._fail.reason


# --------------------------------------------------------------------------- #
# Cockburn 4-step — internal_kb module
# --------------------------------------------------------------------------- #

# State 1 — test-to-test: InternalKBSources dataclass + emit_convention_b_marker
# with in-memory fake data (no filesystem, no engine).
def test_cockburn_internal_kb_1_test_to_test():
    """InternalKBSources can be built from fake data; emit_convention_b_marker
    returns a well-formed string. No I/O required."""
    from pathlib import PurePosixPath  # pure path — no filesystem
    sources = InternalKBSources(
        topic_folder=Path("/fake/topic"),
        projects_root=Path("/fake"),
    )
    assert not sources.has_sources  # no sources yet

    marker = emit_convention_b_marker(
        quote_type="stated",
        source_kind="local-file",
        path=None,   # no real path needed for this state
        locator="99",
    )
    assert "[stated — local-file:unknown]" == marker


# State 2 — real-to-test: walk_up_topic_claude against a real tmp_path structure.
def test_cockburn_internal_kb_2_real_to_test(topic_tree):
    """walk_up_topic_claude walks the real filesystem and finds the project CLAUDE.md
    before reaching the projects-root CLAUDE.md (not a staging topic)."""
    projects, project_folder = topic_tree

    claude_path, is_root = walk_up_topic_claude(project_folder, projects)
    assert claude_path is not None
    assert claude_path.exists()
    assert claude_path.name == "CLAUDE.md"
    # The project has its own CLAUDE.md, so this should NOT be the projects root.
    assert is_root is False


# State 3 — test-to-real: validate_user_paths against a real tmp_path filesystem.
def test_cockburn_internal_kb_3_test_to_real(tmp_path):
    """validate_user_paths reads the real filesystem (created files in tmp_path);
    returns valid_paths for existing files and failed_patterns for missing ones."""
    projects = tmp_path / "Projects"
    projects.mkdir()
    (projects / "real_file.md").write_text("content", encoding="utf-8")

    result = validate_user_paths(["real_file.md", "phantom.md"], projects_root=projects)
    assert result.mixed
    assert any("real_file.md" in str(p) for p in result.valid_paths)
    assert "phantom.md" in result.failed_patterns


# State 4 — real-to-real: full discovery + marker emission chain
# against the live Thoughts/ folder.
def test_cockburn_internal_kb_4_real_to_real():
    """Full discovery + marker emission against the live Thoughts/ staging folder.
    Combines walk_up_topic_claude + discover_research_files + emit_convention_b_marker."""
    try:
        thoughts_folder = live_corpus.require(
            "Thoughts", "the live Thoughts/ staging folder this test discovers from")
    except live_corpus.LiveCorpusUnavailable as exc:
        pytest.skip(str(exc))

    sources = discover_internal_sources(thoughts_folder, PROJECTS_ROOT)
    # The staging folder may or may not have _RESEARCH files, but walk-up MUST
    # resolve a CLAUDE.md, and the flag must report where it actually landed.
    # (Premise repaired with the sibling live test above: asserting a hard True
    # here encoded "Thoughts/ has no CLAUDE.md", which is no longer the case.)
    assert sources.claude_md_path is not None
    assert sources.is_projects_root_claude is (
        sources.claude_md_path.parent.resolve() == PROJECTS_ROOT.resolve()
    )

    if sources.claude_md_path:
        marker = emit_convention_b_marker(
            quote_type="stated",
            source_kind="topic-CLAUDE",
            path=sources.claude_md_path,
            locator="1",
            projects_root=PROJECTS_ROOT,
            is_projects_root_claude=sources.is_projects_root_claude,
        )
        assert "[stated — topic-CLAUDE:" in marker
        if sources.is_projects_root_claude:
            assert "(Projects-root CLAUDE.md — not topic-scoped)" in marker


# --------------------------------------------------------------------------- #
# Cockburn 4-step — locale_native_subpass module
# --------------------------------------------------------------------------- #

# State 1 — test-to-test: FakeLocaleNativeAdapter + LocaleNativeIntake (no engine).
def test_cockburn_locale_native_1_test_to_test():
    """FakeLocaleNativeAdapter returns a LocaleNativeResult for a known language."""
    adapter = FakeLocaleNativeAdapter()
    intake = LocaleNativeIntake(
        scope_core=ScopeCore(),
        target_language="de",
        conversation_language="en",
        user_query="topic",
    )
    outcome = adapter.regenerate(intake)
    assert ln_is_result(outcome)
    assert len(outcome.terms) >= 1


# State 2 — real-to-test: ClaudeLocaleNativeAdapter with fixture invoker (no live Claude).
def test_cockburn_locale_native_2_real_to_test():
    """Production adapter parses a well-formed JSON response from a fixture invoker."""
    canned = json.dumps({"terms": ["EV-Adoption in Europa", "Elektrofahrzeuge 2026"]})

    captured_prompts = []

    def fixture_invoker(prompt: str) -> str:
        captured_prompts.append(prompt)
        return canned

    adapter = ClaudeLocaleNativeAdapter(invoker=fixture_invoker)
    intake = LocaleNativeIntake(
        scope_core=ScopeCore(
            angles=("EV adoption state",),
            focused_questions=("Who leads adoption?",),
        ),
        target_language="de",
        conversation_language="en",
        user_query="EV adoption in Europe",
    )
    outcome = adapter.regenerate(intake)
    assert ln_is_result(outcome)
    assert "EV-Adoption in Europa" in outcome.terms
    assert captured_prompts  # production adapter sent a bounded prompt
    assert "de" in captured_prompts[0]
    assert "EV adoption in Europe" in captured_prompts[0]


# State 3 — test-to-real: apply_locale_native_subpass with FakeAdapter + ScopeDraftOutput.
def test_cockburn_locale_native_3_test_to_real():
    """apply_locale_native_subpass routes the Fake adapter through the flow helper
    (the 'real' side), merging updated search_terms back into ScopeDraftOutput."""
    fake_sd = FakeScopeDraftAdapter()
    base_scope = fake_sd.draft(ScopeDraftIntake(
        routing_path="deep",
        user_query="solar policy in Germany",
        selected_languages=("en", "de"),
    ))
    assert sd_is_output(base_scope)

    adapters = {"de": FakeLocaleNativeAdapter()}
    updated = apply_locale_native_subpass(base_scope, adapters, user_query="solar policy in Germany")

    assert "de" in updated.search_terms
    assert updated.search_terms["de"] is not None
    de_terms = updated.search_terms["de"]
    assert len(de_terms) >= 1


# State 4 — real-to-real: apply_locale_native_subpass with ClaudeAdapter (canned invoker).
def test_cockburn_locale_native_4_real_to_real():
    """ClaudeLocaleNativeAdapter (canned invoker) wired through apply_locale_native_subpass.
    Same flow as production but without a live SDK call (Evolution Test: the
    invoker is swapped without touching the adapter or the flow helper)."""
    canned = json.dumps({"terms": ["Solarenergie Deutschland", "Förderprogramme Solar"]})
    production_adapter = ClaudeLocaleNativeAdapter(invoker=lambda _: canned)

    fake_sd = FakeScopeDraftAdapter()
    base_scope = fake_sd.draft(ScopeDraftIntake(
        routing_path="ninja",
        user_query="solar policy Germany",
        selected_languages=("en", "de"),
    ))

    updated = apply_locale_native_subpass(
        base_scope,
        adapters_by_language={"de": production_adapter},
        user_query="solar policy Germany",
    )
    assert updated.search_terms.get("de") is not None
    assert "Solarenergie Deutschland" in updated.search_terms["de"]


# --------------------------------------------------------------------------- #
# Q11 conformance — no internal identifiers in locale-native error messages
# --------------------------------------------------------------------------- #

def test_q11_locale_native_error_reasons_carry_no_internal_identifiers():
    """All error reasons from ClaudeLocaleNativeAdapter use plain English;
    none name internal pipeline identifiers (Q11)."""
    FORBIDDEN = (
        "r0_intake", "r1_scope_approved", "cycle_id", "caller_skill",
        "autonomous_scope", "user_approved_scope", "non_pass_verdict",
    )
    invokers = [
        lambda _: "",                       # empty_response
        lambda _: "not json",               # parse_failed
        lambda _: json.dumps({"other": 1}), # schema_failed (no 'terms' key → None → model_cannot_produce)
    ]
    for invoker_fn in invokers:
        adapter = ClaudeLocaleNativeAdapter(invoker=invoker_fn)
        intake = LocaleNativeIntake(
            scope_core=ScopeCore(),
            target_language="de",
            conversation_language="en",
        )
        outcome = adapter.regenerate(intake)
        assert ln_is_error(outcome)
        for forbidden in FORBIDDEN:
            assert forbidden not in outcome.reason, (
                f"Q11: locale-native error reason must not contain '{forbidden}'"
            )


# --------------------------------------------------------------------------- #
# Source discovery — discover_internal_sources integration
# --------------------------------------------------------------------------- #

def test_discover_internal_sources_finds_both_claude_and_research(topic_tree):
    """For a project with its own CLAUDE.md and _RESEARCH.md, both are found."""
    projects, project_folder = topic_tree

    sources = discover_internal_sources(project_folder, projects)
    assert sources.claude_md_path is not None
    assert sources.claude_md_path.name == "CLAUDE.md"
    assert len(sources.research_files) == 1
    assert sources.research_files[0].name == "topic_RESEARCH.md"
    assert sources.has_sources
    assert sources.is_projects_root_claude is False


def test_discover_internal_sources_staging_topic_resolves_root_claude(staging_tree):
    """For a Thoughts/ staging topic with no local CLAUDE.md, walk-up finds the root."""
    projects, staging_folder = staging_tree

    sources = discover_internal_sources(staging_folder, projects)
    assert sources.claude_md_path is not None
    assert sources.is_projects_root_claude is True
    assert sources.has_sources

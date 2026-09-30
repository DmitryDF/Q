"""S5 path admission (design-A19) — the slice's own gate.

Two mechanisms, kept separate on purpose. They answer different questions about
the same path and a fix that collapses them into one test would not catch the
case a naive repair drops:

  * ADMISSION  — may we read this? Decided on where a pattern RESOLVES, never on
    how it is spelled, so an absolute spelling and a `..`-traversing relative
    spelling of the same location get the same treatment. Each case asserts the
    pattern resolves to the EXPECTED FILES, not merely that it survived
    validation — admitting a location that then expands to nothing is the second
    half of the defect (G2), and a survival-only assertion would miss it.

  * EMISSION   — how do we name it? Emission follows the source: workspace-
    relative inside the workspace, absolute outside it. Exactly one shape is
    refused — inside-the-workspace, cited absolutely — because it is the only
    case where the alternative is strictly better.

Honours CLAUDE_CONFIG_DIR so it runs against an experiment clone during
development and live config otherwise; without it a clone run would silently
grade the live module. Run with /usr/bin/python3 (pytest is not installed for the
default Homebrew python3).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
SKILLS_DIR = CONFIG_DIR / "skills"

sys.path.insert(0, str(SKILLS_DIR))

from research.internal_kb import (  # noqa: E402
    CITATION_FORM_ABSOLUTE,
    CITATION_FORM_WORKSPACE_RELATIVE,
    CitationFormError,
    _emit_source_path,
    emit_convention_b_marker,
    validate_user_paths,
)


# --------------------------------------------------------------------------- #
# Fixtures — a workspace and a location genuinely outside it
# --------------------------------------------------------------------------- #

@pytest.fixture
def workspace(tmp_path):
    """(projects_root, outside_root) — two sibling trees, one the workspace."""
    projects = tmp_path / "Projects"
    (projects / "Docs").mkdir(parents=True)
    (projects / "Docs" / "inside.md").write_text("inside", encoding="utf-8")

    # Stands in for a cloned repo or a network share: a real location that is
    # absolute by nature and cannot be expressed relative to the workspace root.
    outside = tmp_path / "elsewhere"
    (outside / "notes").mkdir(parents=True)
    (outside / "notes" / "a.md").write_text("a", encoding="utf-8")
    (outside / "notes" / "b.md").write_text("b", encoding="utf-8")
    (outside / "notes" / "skip.txt").write_text("not markdown", encoding="utf-8")

    return projects, outside


# --------------------------------------------------------------------------- #
# ADMISSION + EXPANSION — five cases, each asserting the RESOLVED FILES
# --------------------------------------------------------------------------- #

def test_admission_relative_pattern_resolves_inside_workspace(workspace):
    """The pre-A19 case, unchanged: a relative pattern anchors at the workspace."""
    projects, _ = workspace

    result = validate_user_paths(["Docs/inside.md"], projects_root=projects)

    assert result.all_success
    assert result.valid_paths == [(projects / "Docs" / "inside.md").resolve()]


def test_admission_absolute_path_inside_workspace_is_accepted(workspace):
    """An absolute spelling of an INSIDE location is admitted for reading.

    Admission and emission answer different questions: this path is readable, and
    it is refused only later if a caller insists on citing it absolutely."""
    projects, _ = workspace
    target = projects / "Docs" / "inside.md"

    result = validate_user_paths([str(target)], projects_root=projects)

    assert result.all_success, "an absolute spelling must not be refused as such"
    assert result.valid_paths == [target.resolve()]


def test_admission_absolute_path_outside_workspace_is_accepted(workspace):
    """C1's headline case — a repo or share outside the workspace is declarable."""
    projects, outside = workspace
    target = outside / "notes" / "a.md"

    result = validate_user_paths([str(target)], projects_root=projects)

    assert result.all_success
    assert result.valid_paths == [target.resolve()]


def test_admission_glob_rooted_out_of_root_expands_to_its_own_files(workspace):
    """C2 — an accepted out-of-root location must actually RESOLVE.

    This is the case that fails if admission is fixed alone: expansion rooted at
    the workspace would return nothing for a pattern anchored elsewhere."""
    projects, outside = workspace

    result = validate_user_paths([str(outside / "notes" / "*.md")], projects_root=projects)

    assert result.all_success
    assert result.valid_paths == [
        (outside / "notes" / "a.md").resolve(),
        (outside / "notes" / "b.md").resolve(),
    ], "the glob must expand against its OWN root, and match only the .md files"


def test_admission_dotdot_escaping_pattern_takes_the_out_of_root_treatment(workspace):
    """A `..`-traversing RELATIVE spelling of an outside location resolves outside,
    so it is treated exactly as the absolute spelling of the same file — decided
    on where it resolves, never on how it is spelled."""
    projects, outside = workspace
    target = outside / "notes" / "a.md"

    result = validate_user_paths(["../elsewhere/notes/a.md"], projects_root=projects)

    assert result.all_success
    assert result.valid_paths == [target.resolve()]


def test_admission_bare_double_star_is_still_refused(workspace):
    """The retained refusal (G1's scope note): design-A19 resolves the absolute-path
    half of surfaced rule C4 and never mentions globs, so a `**` with no directory
    prefix — which enumerates the entire tree — stays refused."""
    projects, _ = workspace

    result = validate_user_paths(["**/*.md"], projects_root=projects)

    assert result.all_failed
    assert "**/*.md" in result.failed_patterns


def test_admission_absolute_path_naming_nothing_fails_at_expansion(workspace):
    """The refusal MOVED, it did not vanish: a non-existent absolute path is no
    longer refused for its spelling, but it still fails because it resolves to
    nothing."""
    projects, _ = workspace

    result = validate_user_paths(["/no/such/place.md"], projects_root=projects)

    assert result.all_failed
    assert "/no/such/place.md" in result.failed_patterns


# --------------------------------------------------------------------------- #
# EMISSION — four cases plus the one refusal
# --------------------------------------------------------------------------- #

def test_emission_inside_workspace_is_workspace_relative(workspace):
    """Default (form=None) follows the source: inside → workspace-relative."""
    projects, _ = workspace
    target = projects / "Docs" / "inside.md"

    assert _emit_source_path(target, projects) == "Docs/inside.md"


def test_emission_outside_workspace_is_absolute_by_stated_rule(workspace):
    """C3 — absolute is the only expression an out-of-root source has, so it is
    emitted by rule rather than as a silent fallback."""
    projects, outside = workspace
    target = outside / "notes" / "a.md"

    emitted = _emit_source_path(target, projects)

    assert emitted == str(target.resolve())
    assert emitted.startswith("/")


def test_emission_absolute_spelling_of_inside_path_still_emits_relative(workspace):
    """How the caller SPELLED the path does not change the citation: the same
    inside-the-workspace file cites relatively whether named absolutely or not."""
    projects, _ = workspace
    target = (projects / "Docs" / "inside.md").resolve()

    assert _emit_source_path(Path(str(target)), projects) == "Docs/inside.md"


def test_emission_workspace_relative_requested_for_outside_source_follows_source(
    workspace,
):
    """Exactly ONE shape is refused, and this is not it. Rewriting an out-of-root
    source into a workspace-relative form would produce a citation that does not
    resolve, so the request is answered by following the source instead."""
    projects, outside = workspace
    target = outside / "notes" / "a.md"

    emitted = _emit_source_path(
        target, projects, form=CITATION_FORM_WORKSPACE_RELATIVE
    )

    assert emitted == str(target.resolve())


def test_emission_inside_but_absolute_is_refused_with_its_reason(workspace):
    """C4 — the one refused shape. A refusal that does not name which case fired
    is unactionable, so the message is asserted, not just the exception type."""
    projects, _ = workspace
    target = projects / "Docs" / "inside.md"

    with pytest.raises(CitationFormError) as excinfo:
        _emit_source_path(target, projects, form=CITATION_FORM_ABSOLUTE)

    message = str(excinfo.value)
    assert "inside the workspace root" in message, "the refusal must name the case"
    assert "Docs/inside.md" in message, "the refusal must offer the form that works"


# --------------------------------------------------------------------------- #
# The two mechanisms meeting at the marker emitter
# --------------------------------------------------------------------------- #

def test_marker_for_out_of_root_source_carries_the_absolute_path(workspace):
    """End to end: a claim drawn from an out-of-root source names it in a form
    that can be opened again later."""
    projects, outside = workspace
    target = outside / "notes" / "a.md"

    marker = emit_convention_b_marker(
        quote_type="stated",
        source_kind="local-file",
        path=target,
        locator="12",
        projects_root=projects,
    )

    assert marker == f"[stated — local-file:{target.resolve()}:12]"


def test_marker_for_inside_source_stays_workspace_relative(workspace):
    """Nothing that works today stops working (C6)."""
    projects, _ = workspace

    marker = emit_convention_b_marker(
        quote_type="stated",
        source_kind="local-file",
        path=projects / "Docs" / "inside.md",
        locator="3",
        projects_root=projects,
    )

    assert marker == "[stated — local-file:Docs/inside.md:3]"


def test_marker_refuses_an_inside_source_asked_for_absolutely(workspace):
    """The refusal reaches the marker surface rather than being swallowed there."""
    projects, _ = workspace

    with pytest.raises(CitationFormError):
        emit_convention_b_marker(
            quote_type="stated",
            source_kind="local-file",
            path=projects / "Docs" / "inside.md",
            locator="3",
            projects_root=projects,
            form=CITATION_FORM_ABSOLUTE,
        )

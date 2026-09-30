#!/usr/bin/env python3
"""S1 — canonical (topic, project) identity resolver.

session-topic-identity-coherence plan, Slice S1 / A1. Covers the four A1 cases
(Root / leaf / relative==absolute / outside-root) plus the symlinked-root case
that is the live bug's root cause, and the S5 convergence equivalence (all three
surfaces agree for the same spine).

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_identity_resolver.py
"""
import importlib.util
import os
import sys
from pathlib import Path

import pytest

# Respect CLAUDE_CONFIG_DIR so a claude-experiment clone tests its OWN hooks (S3).
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"


def _import_ppg():
    spec = importlib.util.spec_from_file_location("pre_plan_gates_s1", PPG_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import_ppg()


def _mk_tree(root: Path):
    """A scratch projects tree: root TODO.md + a leaf project + staging dirs."""
    (root / "TODO.md").write_text("# root todo\n")
    (root / "Thoughts").mkdir()
    leaf = root / "Personal" / "your-project"
    leaf.mkdir(parents=True)
    (leaf / "TODO.md").write_text("# leaf todo\n")
    (leaf / "Thoughts").mkdir()
    return leaf


# --- A1 case: Root (root-level Thoughts/ spine) ---------------------------

def test_root_spine_resolves_to_Root(tmp_path):
    _mk_tree(tmp_path)
    spine = tmp_path / "Thoughts" / "foo-20260101010101_PLAN.md"
    spine.write_text("# plan\n")
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "Root"


def test_root_spine_directly_in_root(tmp_path):
    _mk_tree(tmp_path)
    spine = tmp_path / "bar_THOUGHT.md"
    spine.write_text("# t\n")
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "Root"


# --- A1 case: leaf project spine ------------------------------------------

def test_leaf_spine_resolves_to_leaf_basename(tmp_path):
    leaf = _mk_tree(tmp_path)
    spine = leaf / "Thoughts" / "x_THOUGHT.md"
    spine.write_text("# t\n")
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "your-project"


def test_leaf_spine_directly_in_leaf(tmp_path):
    leaf = _mk_tree(tmp_path)
    spine = leaf / "y_THOUGHT.md"
    spine.write_text("# t\n")
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "your-project"


# --- A1 case: relative == absolute ----------------------------------------

def test_relative_equals_absolute(tmp_path, monkeypatch):
    leaf = _mk_tree(tmp_path)
    spine_abs = leaf / "Thoughts" / "z_THOUGHT.md"
    spine_abs.write_text("# t\n")
    abs_result = ppg.canonical_project_for_spine(spine_abs, project_root=tmp_path)
    # a relative path resolves against cwd via realpath — chdir so the relative
    # form names the SAME file, and confirm identical classification.
    monkeypatch.chdir(leaf / "Thoughts")
    rel_result = ppg.canonical_project_for_spine("z_THOUGHT.md", project_root=tmp_path)
    assert abs_result == rel_result == "your-project"


# --- A1 case: outside root ------------------------------------------------

def test_outside_root_resolves_to_Root(tmp_path):
    _mk_tree(tmp_path)
    other = tmp_path.parent / (tmp_path.name + "_elsewhere")
    other.mkdir()
    spine = other / "loose_THOUGHT.md"
    spine.write_text("# t\n")
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "Root"


def test_nonexistent_spine_parent_resolves_to_Root(tmp_path):
    _mk_tree(tmp_path)
    spine = tmp_path / "no" / "such" / "dir" / "ghost_THOUGHT.md"
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "Root"


# --- the live bug: symlinked root -----------------------------------------

def test_symlinked_root_still_classifies_correctly(tmp_path):
    """The live failure: PROJECTS_ROOT is a symlink to the real tree. realpath
    on BOTH sides must keep a root spine classified 'Root', not 'outside-root'.
    """
    real = tmp_path / "real"
    real.mkdir()
    _mk_tree(real)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    # spine addressed through the real path, root given as the symlink
    spine = real / "Thoughts" / "s_THOUGHT.md"
    spine.write_text("# t\n")
    assert ppg.canonical_project_for_spine(spine, project_root=link) == "Root"
    # and a leaf spine through the symlinked root
    leaf_spine = link / "Personal" / "your-project" / "q_THOUGHT.md"
    (real / "Personal" / "your-project" / "q_THOUGHT.md").write_text("# t\n")
    assert ppg.canonical_project_for_spine(leaf_spine, project_root=link) == "your-project"


# --- Thoughts / Root guard ------------------------------------------------

def test_thoughts_dir_is_not_a_leaf_project(tmp_path):
    """A TODO.md accidentally inside a Thoughts/ staging dir must NOT be read as
    a leaf project (A1 guard rail)."""
    _mk_tree(tmp_path)
    staging = tmp_path / "Thoughts"
    (staging / "TODO.md").write_text("# stray\n")  # pathological
    spine = staging / "w_THOUGHT.md"
    spine.write_text("# t\n")
    assert ppg.canonical_project_for_spine(spine, project_root=tmp_path) == "Root"


# --- S5 convergence: all surfaces agree for the same spine ----------------

def test_convergence_resolve_project_root_and_canonical_agree(tmp_path, monkeypatch):
    """_resolve_project_root (Path) and canonical_project_for_spine (slug) must
    agree on Root/leaf for the same location (single-locus Evolution Test).
    Uses a non-symlinked tmp tree with PROJECTS_ROOT monkeypatched."""
    leaf = _mk_tree(tmp_path)
    monkeypatch.setattr(ppg, "PROJECTS_ROOT", tmp_path)
    # leaf. S3: `_resolve_project_root` now normalises BOTH sides through the one
    # `_norm_path`, so its answer is the RESOLVED path — compare resolved to
    # resolved (on macOS a tmp dir under /var is itself a symlink to /private/var).
    rp = ppg._resolve_project_root(leaf / "Thoughts")
    slug = ppg.canonical_project_for_spine(leaf / "Thoughts" / "a_THOUGHT.md",
                                           project_root=tmp_path)
    assert rp == ppg._norm_path(leaf) and slug == leaf.name
    # root
    rp_root = ppg._resolve_project_root(tmp_path / "Thoughts")
    slug_root = ppg.canonical_project_for_spine(tmp_path / "Thoughts" / "b_THOUGHT.md",
                                                project_root=tmp_path)
    assert rp_root == ppg._norm_path(tmp_path) and slug_root == "Root"


def test_convergence_holds_on_a_symlinked_root(tmp_path, monkeypatch):
    """S3 / A3 — the live shape: PROJECTS_ROOT is a SYMLINK to the real tree.
    `_resolve_project_root` used to compare a resolved cwd against the unresolved
    literal and return None for every cwd; all three resolvers now agree."""
    real = tmp_path / "real"
    real.mkdir()
    leaf = _mk_tree(real)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(ppg, "PROJECTS_ROOT", link)
    # cwd through the real path, root as the symlink
    assert ppg._resolve_project_root(leaf / "Thoughts") == ppg._norm_path(leaf)
    assert ppg.canonical_project_for_spine(leaf / "Thoughts" / "a_THOUGHT.md") == leaf.name
    assert ppg._project_dir_for_spine(leaf / "Thoughts" / "a_THOUGHT.md") == ppg._norm_path(leaf)
    # cwd through the symlink path
    assert ppg._resolve_project_root(link / "Thoughts") == ppg._norm_path(real)
    assert ppg.canonical_project_for_spine(link / "Thoughts" / "b_THOUGHT.md") == "Root"
    # outside stays each caller's own answer
    assert ppg._resolve_project_root(tmp_path) is None
    assert ppg.canonical_project_for_spine(tmp_path / "c_THOUGHT.md") == "Root"


def test_canonical_identity_unbound_returns_none_pair(monkeypatch, tmp_path):
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", tmp_path)
    monkeypatch.setattr(ppg, "_active_path", lambda: tmp_path / "_active.json")
    assert ppg.canonical_identity("ffffffff-0000-0000-0000-000000000000") == (None, None)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

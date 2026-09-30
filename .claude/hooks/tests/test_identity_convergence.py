#!/usr/bin/env python3
"""S5 — converge todo.find_project_name onto the canonical resolver.

session-topic-identity-coherence plan, Slice S5 / A5. Behaviour-preserving:
find_project_name delegates the Root-vs-leaf DECISION to the single canonical
locus but keeps its display format, so the SessionStart scan is unchanged, and
it can never disagree with the identity key on 'is this Root?'.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_identity_convergence.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


todo = _import("todo_s5", "todo.py")
ppg = _import("pre_plan_gates_s5", "pre_plan_gates.py")


def _tree(root):
    (root / "TODO.md").write_text("# root\n")
    leaf = root / "Personal" / "your-project"
    leaf.mkdir(parents=True)
    (leaf / "TODO.md").write_text("# leaf\n")
    return leaf


def test_root_todo_is_Root(tmp_path):
    _tree(tmp_path)
    assert todo.find_project_name(tmp_path / "TODO.md", tmp_path) == "Root"


def test_nested_leaf_keeps_two_part_display(tmp_path):
    leaf = _tree(tmp_path)
    # display format preserved: 'Personal/your-project'
    assert todo.find_project_name(leaf / "TODO.md", tmp_path) == "Personal/your-project"


def test_single_level_leaf_is_basename(tmp_path):
    (tmp_path / "TODO.md").write_text("# root\n")
    solo = tmp_path / "solo"
    solo.mkdir()
    (solo / "TODO.md").write_text("# solo\n")
    assert todo.find_project_name(solo / "TODO.md", tmp_path) == "solo"


def test_out_of_tree_returns_parent_name(tmp_path):
    # path not under vault_root -> unchanged fallback (parent basename)
    other = tmp_path / "elsewhere"
    other.mkdir()
    vault = tmp_path / "vault"
    vault.mkdir()
    assert todo.find_project_name(other / "TODO.md", vault) == "elsewhere"


def test_root_decision_agrees_with_canonical_resolver(tmp_path):
    """The convergence contract: find_project_name says 'Root' iff the canonical
    resolver says 'Root', for the same location."""
    leaf = _tree(tmp_path)
    # root
    assert (todo.find_project_name(tmp_path / "TODO.md", tmp_path) == "Root")
    assert (ppg.canonical_project_for_spine(tmp_path / "TODO.md",
                                            project_root=tmp_path) == "Root")
    # leaf: canonical is the bare leaf; find_project_name is NOT 'Root'
    assert todo.find_project_name(leaf / "TODO.md", tmp_path) != "Root"
    assert ppg.canonical_project_for_spine(leaf / "TODO.md",
                                           project_root=tmp_path) == "your-project"


def test_delegation_failsafe(tmp_path, monkeypatch):
    """If the resolver import/call fails, find_project_name still returns a name
    (the SessionStart scan can never break)."""
    _tree(tmp_path)
    monkeypatch.setattr(todo, "_canonical_project_or_none",
                        lambda *a, **k: None)  # simulate resolver unavailable
    assert todo.find_project_name(tmp_path / "TODO.md", tmp_path) == "Root"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

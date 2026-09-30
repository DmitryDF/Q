"""Tests for bookkeeping_migrate — idempotence + in-family targets + safety."""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import bookkeeping_invariant as bi          # noqa: E402
import bookkeeping_migrate as mig           # noqa: E402


def _tree(tmp_path, files):
    tdir = tmp_path / "Thoughts"
    tdir.mkdir()
    for name, text in files.items():
        (tdir / name).write_text(text, encoding="utf-8")
    return str(tdir)


def _clean(tdir, slug):
    f = bi.read_family(tdir, slug)
    touched = f.spine or f.members[0]
    return [v for v in bi.evaluate_family(touched, f) if v.blocking]


def test_dry_run_writes_nothing(tmp_path):
    tdir = _tree(tmp_path, {
        "demo_THOUGHT.md": "# Implementation Details\n",
        "demo_PLAN.md": "body\n",
    })
    notes = mig.migrate_dir(tdir, apply=False)
    assert notes                                          # changes planned
    assert (tmp_path / "Thoughts" / "demo_PLAN.md").read_text() == "body\n"  # untouched


def test_apply_then_idempotent(tmp_path):
    tdir = _tree(tmp_path, {
        "demo_THOUGHT.md": "# Solution Design\n# Implementation Details\n# Discovery\n",
        "demo_DESIGN.md": "design body\n",
        "demo_PLAN.md": "plan body\n",
        "demo_RESEARCH.md": "research body\n",
    })
    first = mig.migrate_dir(tdir, apply=True)
    assert first                                          # first run changed things
    # Idempotence: second run = zero changes.
    second = mig.migrate_dir(tdir, apply=True)
    assert second == []
    # Contract now clean (no blocking violations).
    assert _clean(tdir, "demo") == []


def test_parent_targets_resolve_in_family(tmp_path):
    tdir = _tree(tmp_path, {
        "demo_THOUGHT.md": "# Solution Design\n# Implementation Details\n",
        "demo_DESIGN.md": "d\n",
        "demo_PLAN.md": "p\n",     # bare PLAN -> parent should be the DESIGN, not the spine
    })
    mig.migrate_dir(tdir, apply=True)
    plan_text = (tmp_path / "Thoughts" / "demo_PLAN.md").read_text()
    assert "Parent: [[demo_DESIGN]]" in plan_text         # DESIGN-else-spine rule
    design_text = (tmp_path / "Thoughts" / "demo_DESIGN.md").read_text()
    assert "Parent: [[demo_THOUGHT]]" in design_text


def test_frontmatter_preserved(tmp_path):
    tdir = _tree(tmp_path, {
        "demo_THOUGHT.md": "# Implementation Details\n",
        "demo_PLAN.md": "---\nkind: plan\n---\n# Plan\n",
    })
    mig.migrate_dir(tdir, apply=True)
    text = (tmp_path / "Thoughts" / "demo_PLAN.md").read_text()
    assert text.startswith("---\nkind: plan\n---\n")      # frontmatter intact at top
    assert "Parent: [[demo_THOUGHT]]" in text


def test_retired_family_untouched(tmp_path):
    tdir = _tree(tmp_path, {
        "demo_THOUGHT.md": "**Status:** Retired 2026-06-23\n# Implementation Details\n",
        "demo_PLAN.md": "no parent\n",
    })
    assert mig.migrate_dir(tdir, apply=True) == []
    assert (tmp_path / "Thoughts" / "demo_PLAN.md").read_text() == "no parent\n"


def test_mode_c_untouched(tmp_path):
    tdir = _tree(tmp_path, {"ninja_PLAN.md": "---\nbookkeeping: mode-c\n---\n# plan\n"})
    assert mig.migrate_dir(tdir, apply=True) == []


def test_root_plan_no_parent_invented(tmp_path):
    tdir = _tree(tmp_path, {"solo_PLAN.md": "lone plan\n"})
    assert mig.migrate_dir(tdir, apply=True) == []
    assert (tmp_path / "Thoughts" / "solo_PLAN.md").read_text() == "lone plan\n"

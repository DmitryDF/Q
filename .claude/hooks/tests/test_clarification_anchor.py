"""DS8 #16 — anchor-by-discovery nudge on _THOUGHT.md write (advisory)."""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import clarification_anchor as ca  # noqa: E402


def _tree(tmp_path, todo_text):
    (tmp_path / "TODO.md").write_text(todo_text, encoding="utf-8")
    td = tmp_path / "Thoughts"; td.mkdir()
    spine = td / "demo_THOUGHT.md"
    spine.write_text("# Discovery\n", encoding="utf-8")
    return spine


def test_unanchored_thought_nudges(tmp_path):
    spine = _tree(tmp_path, "- [ ] something unrelated\n")
    msg = ca.check(str(spine))
    assert msg and "work-frame-and-create-todo" in msg and "demo" in msg


def test_anchored_thought_is_silent(tmp_path):
    spine = _tree(tmp_path, "- [ ] [Thought] **demo** — see demo_THOUGHT.md\n")
    assert ca.check(str(spine)) is None       # slug present in TODO.md


def test_non_thought_file_ignored(tmp_path):
    _tree(tmp_path, "x\n")
    assert ca.check(str(tmp_path / "Thoughts" / "demo_PLAN.md")) is None


def test_no_todo_found_is_silent(tmp_path):
    td = tmp_path / "Thoughts"; td.mkdir()
    spine = td / "demo_THOUGHT.md"; spine.write_text("# Discovery\n")
    assert ca.check(str(spine)) is None       # no TODO.md up the tree -> can't judge

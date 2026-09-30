"""DS8 #11 — block orphan plain-* mints on unresolved Master-plan wikilink."""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import check_work_start_wikilink as cw  # noqa: E402


def _todo(tmp_path, line):
    p = tmp_path / "TODO.md"
    p.write_text("# TODO\n## Now\n" + line + "\n", encoding="utf-8")
    return p


def _cmd(todo_path, line_no):
    return (f"python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py create-topic SID proj plain-abcd1234 "
            f"--intake-source plain --todo-line-ref {todo_path}:{line_no}")


def test_unresolved_master_plan_blocks(tmp_path):
    todo = _todo(tmp_path, "- [ ] do thing — Master plan: [[ghost-topic_THOUGHT]].")
    msg = cw.evaluate(_cmd(str(todo), 3))
    assert msg and "ghost-topic_THOUGHT" in msg


def test_resolved_master_plan_allows(tmp_path):
    (tmp_path / "Thoughts").mkdir()
    (tmp_path / "Thoughts" / "real-topic_THOUGHT.md").write_text("# x\n")
    todo = _todo(tmp_path, "- [ ] do thing — Master plan: [[real-topic_THOUGHT]].")
    assert cw.evaluate(_cmd(str(todo), 3)) is None


def test_no_master_plan_allows(tmp_path):
    todo = _todo(tmp_path, "- [ ] genuinely plain item, no master plan link.")
    assert cw.evaluate(_cmd(str(todo), 3)) is None


def test_non_plain_command_ignored(tmp_path):
    todo = _todo(tmp_path, "- [ ] x — Master plan: [[ghost_THOUGHT]].")
    cmd = f"python3 pre_plan_gates.py create-topic SID proj realslug --intake-source thought --todo-line-ref {todo}:3"
    assert cw.evaluate(cmd) is None


def test_non_create_topic_ignored():
    assert cw.evaluate("python3 pre_plan_gates.py phase-start SID thought") is None

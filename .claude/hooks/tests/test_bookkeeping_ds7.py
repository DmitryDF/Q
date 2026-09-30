"""DS7 tests — diary-link validator (#13a/#13) + claims_registry.ensure_exists (#15.0)."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import todo  # noqa: E402
import claims_registry as cr  # noqa: E402
import bookkeeping_invariant as bi  # noqa: E402

TODO_PY = os.path.expanduser("${KIT_HOOKS_DIR}/todo.py")


# ── diary-link predicate ─────────────────────────────────────────────────
def test_diary_link_predicate():
    assert cr  # imported
    assert todo._diary_link_violations("- [x] shipped X\n") == ["- [x] shipped X"]
    assert todo._diary_link_violations("- [x] shipped X [[2026-06-24]]\n") == []
    assert todo._diary_link_violations("- [ ] open item\n") == []   # not a Done line
    assert todo._diary_link_violations("plain prose\n") == []


def test_cmd_validate_diary_link():
    assert todo.cmd_validate_diary_link("- [x] no link")["status"] == "fail"
    assert todo.cmd_validate_diary_link("- [x] ok [[2026-06-24]]")["status"] == "pass"


# ── validate-todo-write hook entry (subprocess) ──────────────────────────
def _run(payload):
    return subprocess.run([sys.executable, TODO_PY, "validate-todo-write"],
                          input=json.dumps(payload), capture_output=True, text=True)


def test_edit_bad_done_line_blocks():
    r = _run({"tool_name": "Edit", "tool_input":
              {"file_path": "/x/TODO.md", "new_string": "- [x] did a thing\n"}})
    assert r.returncode == 2
    assert "diary wikilink" in r.stderr


def test_edit_good_done_line_passes():
    r = _run({"tool_name": "Edit", "tool_input":
              {"file_path": "/x/TODO.md", "new_string": "- [x] did it [[2026-06-24]]\n"}})
    assert r.returncode == 0


def test_write_bad_done_line_warns_not_blocks():
    r = _run({"tool_name": "Write", "tool_input":
              {"file_path": "/x/TODO.md", "content": "- [x] legacy debt item\n"}})
    assert r.returncode == 0                      # WARN, never blocks a full Write
    assert "WARN" in r.stderr


def test_non_todo_path_ignored():
    r = _run({"tool_name": "Edit", "tool_input":
              {"file_path": "/x/notes.md", "new_string": "- [x] no link\n"}})
    assert r.returncode == 0 and r.stderr == ""


# ── claims_registry.ensure_exists ────────────────────────────────────────
def test_ensure_exists_creates_with_parent(tmp_path):
    d = tmp_path / "Thoughts"; d.mkdir()
    (d / "demo_THOUGHT.md").write_text("# Discovery\n")
    path, created = cr.ensure_exists(str(d), "demo")
    assert created
    text = open(path).read()
    assert "Parent: [[demo_THOUGHT]]" in text       # hook-clean on creation (no G3)
    assert "# Claims Registry — demo" in text


def test_ensure_exists_idempotent(tmp_path):
    d = tmp_path / "Thoughts"; d.mkdir()
    (d / "demo_THOUGHT.md").write_text("# Discovery\n")
    cr.ensure_exists(str(d), "demo")
    path, created = cr.ensure_exists(str(d), "demo")
    assert created is False                          # second call is a no-op


def test_ensure_exists_never_truncates_an_existing_row(tmp_path, monkeypatch):
    """MAJOR 4a: the create must be EXCLUSIVE, not check-then-truncate.

    Catches: reverting `open(path, "x", ...)` back to `open(path, "w", ...)`
    in `ensure_exists`. `os.path.exists` (the advisory fast path) is forced
    to keep reporting the file absent, so the race window two concurrent
    first-callers would hit is reproduced deterministically: the ACTUAL open
    call runs against a file that already carries a row. `"x"` mode raises
    `FileExistsError` (caught, degrades to `(path, False)`) and leaves the
    row untouched; `"w"` mode would silently truncate it."""
    d = tmp_path / "Thoughts"; d.mkdir()
    (d / "demo_THOUGHT.md").write_text("# Discovery\n")
    path, created = cr.ensure_exists(str(d), "demo")
    assert created is True
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n| R1 | some-locator | S1 | — | — |\n")
    before = open(path, encoding="utf-8").read()
    assert "R1" in before

    monkeypatch.setattr(cr.os.path, "exists", lambda p: False)
    path2, created2 = cr.ensure_exists(str(d), "demo")
    assert created2 is False                    # lost the race, not an error
    after = open(path2, encoding="utf-8").read()
    assert after == before
    assert "R1" in after


def test_created_claims_is_classified_member(tmp_path):
    d = tmp_path / "Thoughts"; d.mkdir()
    (d / "demo_THOUGHT.md").write_text("# Discovery\n")
    cr.ensure_exists(str(d), "demo")
    m = bi.classify("demo_CLAIMS.md")
    assert m.bucket == bi.B_STANDARD and m.type == "CLAIMS"
    # No G3 for the CLAIMS child (it carries its Parent line); G4 spine-link is
    # the spine's job and is expected until migration/convergence runs.
    fam = bi.read_family(str(d), "demo")
    claims_g3 = [v for v in bi.evaluate_family(m, fam)
                 if v.case == "G3" and "CLAIMS" in v.fix]
    assert claims_g3 == []

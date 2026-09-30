#!/usr/bin/env python3
"""Regression tests for `_extract_due_date` provenance-verb handling.

Guards the todo.py provenance-date misparse fix (plan:
todo-due-date-provenance-fix / binary-mixing-sun): a date qualified by a
provenance/reword verb (reframed/reopened/moved/...) inside parens or **bold**
must be treated as CONTEXT, not a due date — while genuine bare due dates still
surface, and a substring collision ("moved" ⊂ "removed") must NOT suppress a
real deadline.

Imports the `todo.py` that lives ALONGSIDE this test (its own hooks dir), NOT a
hardcoded ~/.claude path — so it validates whichever tree it runs in (a
config-experiment clone during development, live after promotion).

Run: python3 -m pytest <hooks>/tests/test_todo_due_date.py -v
"""

import importlib.util
import unittest
from pathlib import Path

# Load the todo.py that lives ALONGSIDE this test under a UNIQUE module name, via
# an explicit file spec — NOT `import todo`. Sibling todo tests hardcode the live
# ~/.claude/hooks path and import `todo` first, caching it in sys.modules; a plain
# `import todo` here would return that cached module instead of this tree's. Loading
# by path under a unique name makes this test validate its OWN tree unconditionally.
_TODO_PATH = Path(__file__).resolve().parent.parent / "todo.py"
_spec = importlib.util.spec_from_file_location("_todo_under_test", _TODO_PATH)
_todo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_todo)


class TestProvenanceVerbDueDate(unittest.TestCase):
    # --- provenance/reword verbs → NOT a due date (the fix) ---
    def test_reframed_paren_is_not_due(self):
        text = "Master plan: [[x]] (S9; reframed 2026-07-14 — root cause is X)"
        self.assertIsNone(_todo._extract_due_date(text))

    def test_reopened_bold_is_not_due(self):
        self.assertIsNone(_todo._extract_due_date("**reopened 2026-06-30**"))

    def test_moved_paren_is_not_due(self):
        self.assertIsNone(_todo._extract_due_date("(moved to backlog 2026-05-01)"))

    # --- genuine due dates → STILL surface (no over-suppression) ---
    def test_bare_paren_date_is_due(self):
        self.assertEqual(_todo._extract_due_date("do the thing (2026-08-01)"), "2026-08-01")

    def test_bare_bold_date_is_due(self):
        self.assertEqual(_todo._extract_due_date("**Ship 2026-08-01**"), "2026-08-01")

    # --- substring-collision guard: "moved" ⊂ "removed" must NOT suppress ---
    def test_removed_collision_still_due(self):
        self.assertEqual(
            _todo._extract_due_date("(feature removed, due 2026-08-01)"),
            "2026-08-01",
        )

    # --- existing context keywords still recognized (regression) ---
    def test_existing_surfaced_keyword_still_context(self):
        self.assertIsNone(_todo._extract_due_date("(surfaced 2026-07-05)"))


if __name__ == "__main__":
    unittest.main()

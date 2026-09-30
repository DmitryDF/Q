#!/usr/bin/env python3
"""Tests for the Never / Won't-Do terminal bucket (Plan: todo-never-bucket-20260625211847_PLAN.md).

Covers:
  (1)  add --bucket NEVER creates a ## Never section near Done; item lands [ ].
  (2)  ## Never section is placed before ## Done (terminal placement).
  (3)  ## Never item is excluded from _bucket_counts (direct function test).
  (4)  ## Never item with a past ISO date does NOT fire in overdue/urgent scan.
  (5)  ## Never item does NOT appear in scan_deps matches.
  (6)  is_active_project returns False when only open items are in Never.
  (7)  cmd_done refuses to mark a Never item [x].
  (8)  A Now item still completes normally (Never guard is narrow).
  (9)  ## Scheduled item is still counted/active/an add-target (regression: Scheduled intact).
  (10) cleanup does NOT sweep Never items into Done.
  (11) add --bucket NEVER when Never section already exists: item appended, not duplicate section.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_todo_never_bucket.py -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add hooks dir to path so we can import todo.py as a module for unit-style tests.
HOOKS = Path.home() / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS))
import todo as _todo

TODO_PY = HOOKS / "todo.py"


def run(*args, expect=None, stdin=None):
    """Run todo.py with args; assert returncode == expect if given."""
    result = subprocess.run(
        ["python3", str(TODO_PY), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"command {list(args)!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def make_git_vault(tmp_dir, todo_content):
    """Create a git-initialised vault so find_vault_root returns tmp_dir.

    Returns the Path to the TODO.md file and the vault root Path.
    """
    vault = Path(tmp_dir)
    subprocess.run(["git", "init", str(vault)], capture_output=True, check=True)
    todo_path = vault / "TODO.md"
    todo_path.write_text(todo_content)
    return todo_path, vault


# ---------------------------------------------------------------------------
# Sentinel TODO fixtures
# ---------------------------------------------------------------------------

TODO_WITH_NEVER = """\
## Now

- [ ] Active task that should be live

## Never

- [ ] Declined task that should be hidden

## Done

"""

TODO_ACTIVE_ONLY = """\
## Now

- [ ] Active task

## Done

"""

TODO_SCHEDULED = """\
## Now

- [ ] Active now task

## Scheduled

- [ ] Scheduled future task **2099-12-31**

## Done

"""

TODO_NEVER_DATED = """\
## Now

- [ ] Active task

## Never

- [ ] Declined task with old date **2000-01-01**

## Done

"""

TODO_NEVER_ONLY = """\
## Never

- [ ] Only item in project and it is declined

## Done

"""

TODO_NEVER_DEPS = """\
## Now

- [ ] Alpha bravo charlie delta

## Never

- [ ] Alpha bravo charlie delta never version

## Done

"""


# ---------------------------------------------------------------------------
# Helpers: parse a raw content string into the todo data structure
# ---------------------------------------------------------------------------

def parse_text(content):
    """Parse in-memory TODO content using the module's own helper."""
    return _todo.parse_todo_file_from_text(content)


# ---------------------------------------------------------------------------
# (1) add --bucket NEVER creates section and item
# ---------------------------------------------------------------------------

class AddNeverBucketTests(unittest.TestCase):

    def test_add_never_creates_section(self):
        """add --bucket NEVER creates ## Never section with the item [ ]."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_ACTIVE_ONLY)

            r = run(
                "add", "Task I will never do",
                "--bucket", "NEVER",
                "--file", str(todo_path),
                expect=0,
            )
            data = json.loads(r.stdout)
            self.assertTrue(data["success"])

            content = todo_path.read_text()
            self.assertIn("## Never", content)
            self.assertIn("- [ ] Task I will never do", content)
            # Must NOT be marked done
            self.assertNotIn("- [x]", content)

    def test_add_never_item_stays_open(self):
        """Never item is stored as [ ] (open), not [x]."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_ACTIVE_ONLY)

            run(
                "add", "Declined item",
                "--bucket", "NEVER",
                "--file", str(todo_path),
                expect=0,
            )
            content = todo_path.read_text()
            self.assertIn("- [ ] Declined item", content)

    def test_add_never_appends_to_existing_section(self):
        """When ## Never already exists, second add appends rather than creating a duplicate."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_WITH_NEVER)

            run(
                "add", "Second declined item",
                "--bucket", "NEVER",
                "--file", str(todo_path),
                expect=0,
            )
            content = todo_path.read_text()
            # Only one ## Never heading
            self.assertEqual(content.count("## Never"), 1)
            # Both items present
            self.assertIn("- [ ] Declined task that should be hidden", content)
            self.assertIn("- [ ] Second declined item", content)


# ---------------------------------------------------------------------------
# (2) Never section is placed before Done (terminal placement)
# ---------------------------------------------------------------------------

class NeverSectionPlacementTests(unittest.TestCase):

    def test_never_section_before_done(self):
        """## Never appears before ## Done in the file."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            # Start with only Now + Done; add a Never item
            todo_path.write_text(TODO_ACTIVE_ONLY)

            run(
                "add", "Won't do this",
                "--bucket", "NEVER",
                "--file", str(todo_path),
                expect=0,
            )
            content = todo_path.read_text()
            never_pos = content.index("## Never")
            done_pos = content.index("## Done")
            self.assertLess(never_pos, done_pos, "## Never must appear before ## Done")

    def test_active_section_before_never(self):
        """When Never already exists, a new active section is inserted before Never."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            # Start with only Never + Done; add an active NEXT item
            todo_path.write_text(TODO_WITH_NEVER)

            run(
                "add", "Brand new next item",
                "--bucket", "NEXT",
                "--file", str(todo_path),
                expect=0,
            )
            content = todo_path.read_text()
            # A new ## Next section should appear before ## Never
            if "## Next" in content:
                next_pos = content.index("## Next")
                never_pos = content.index("## Never")
                self.assertLess(next_pos, never_pos,
                                "New active ## Next must appear before ## Never")


# ---------------------------------------------------------------------------
# (3) ## Never item excluded from _bucket_counts (direct function tests)
# ---------------------------------------------------------------------------

class BucketCountsExclusionTests(unittest.TestCase):

    def test_never_item_not_in_counts(self):
        """A ## Never item does not appear in _bucket_counts."""
        parsed = parse_text(TODO_WITH_NEVER)
        counts = _todo._bucket_counts(parsed)
        # Never must not appear as a key
        self.assertNotIn("Never", counts, "Never must not appear in bucket counts")
        # Now should be 1
        self.assertEqual(counts.get("Now", 0), 1)

    def test_active_open_items_excludes_never(self):
        """active_open_items has fewer items than open_items when Never items exist."""
        parsed = parse_text(TODO_WITH_NEVER)
        self.assertEqual(len(parsed["open_items"]), 2,
                         "Both Now and Never items appear in open_items")
        self.assertEqual(len(parsed["active_open_items"]), 1,
                         "Only the Now item appears in active_open_items")

    def test_terminal_sections_constant(self):
        """TERMINAL_SECTIONS contains 'never' and 'done' but not 'scheduled'."""
        self.assertIn("never", _todo.TERMINAL_SECTIONS)
        self.assertIn("done", _todo.TERMINAL_SECTIONS)
        self.assertNotIn("scheduled", _todo.TERMINAL_SECTIONS)


# ---------------------------------------------------------------------------
# (4) Never item with past ISO date does NOT fire in overdue/urgent scan
# ---------------------------------------------------------------------------

class NeverDateExclusionTests(unittest.TestCase):

    def test_never_dated_item_excluded_from_active_items(self):
        """A Never item with a past ISO date is NOT in active_open_items."""
        parsed = parse_text(TODO_NEVER_DATED)
        never_actives = [
            item for item in parsed["active_open_items"]
            if item["section"] == "never"
        ]
        self.assertEqual(never_actives, [],
                         "Never items must not appear in active_open_items even with dates")

    def test_never_dated_item_not_overdue_via_read(self):
        """A Never item with a past ISO date must NOT appear as OVERDUE in vault scan."""
        with tempfile.TemporaryDirectory() as td:
            todo_path, vault = make_git_vault(td, TODO_NEVER_DATED)
            r = run(
                "read",
                "--cwd", str(vault),
                "--force",
                expect=0,
            )
            self.assertNotIn("OVERDUE", r.stdout,
                              "Never items must not fire OVERDUE even with past dates")
            self.assertNotIn("URGENT", r.stdout,
                              "Never items must not fire URGENT")


# ---------------------------------------------------------------------------
# (5) ## Never item does NOT appear in scan_deps matches
# ---------------------------------------------------------------------------

class ScanDepsExclusionTests(unittest.TestCase):

    def test_never_item_excluded_from_deps(self):
        """scan_deps must not return a Never item even when tokens overlap."""
        with tempfile.TemporaryDirectory() as td:
            todo_path, vault = make_git_vault(td, TODO_NEVER_DEPS)
            r = run(
                "deps", "Alpha bravo charlie delta new item",
                "--cwd", str(vault),
                expect=0,
            )
            data = json.loads(r.stdout)
            matches = data.get("matches", [])
            never_matches = [m for m in matches if m["section"] == "never"]
            self.assertEqual(never_matches, [],
                             "Never items must not appear in scan_deps matches")

    def test_now_item_still_appears_in_deps(self):
        """scan_deps still returns Now-section items (regression check)."""
        with tempfile.TemporaryDirectory() as td:
            todo_path, vault = make_git_vault(td, TODO_NEVER_DEPS)
            r = run(
                "deps", "Alpha bravo charlie delta fresh search",
                "--cwd", str(vault),
                expect=0,
            )
            data = json.loads(r.stdout)
            matches = data.get("matches", [])
            now_matches = [m for m in matches if m["section"] == "now"]
            self.assertGreater(len(now_matches), 0,
                               "Now items must still appear in scan_deps")


# ---------------------------------------------------------------------------
# (6) is_active_project returns False when only open items are in Never
# ---------------------------------------------------------------------------

class ActiveProjectTests(unittest.TestCase):

    def test_only_never_items_makes_project_inactive(self):
        """is_active_project returns False when only open items are in Never."""
        parsed = parse_text(TODO_NEVER_ONLY)
        self.assertFalse(_todo.is_active_project(parsed),
                          "Project with only Never items must be inactive")

    def test_mixed_project_is_still_active(self):
        """A project with both Now and Never items is still active."""
        parsed = parse_text(TODO_WITH_NEVER)
        self.assertTrue(_todo.is_active_project(parsed),
                         "Project with a Now item must still be active")

    def test_only_never_not_in_vault_scan(self):
        """A Never-only project is NOT included in the vault scan output."""
        with tempfile.TemporaryDirectory() as td:
            todo_path, vault = make_git_vault(td, TODO_NEVER_ONLY)
            r = run(
                "read",
                "--cwd", str(vault),
                "--force",
                expect=0,
            )
            # A project with only Never items produces no active entries
            # (scan_vault calls is_active_project which returns False)
            self.assertNotIn("Declined", r.stdout,
                              "Never-only project must not appear in active scan")


# ---------------------------------------------------------------------------
# (7) cmd_done refuses a Never item
# ---------------------------------------------------------------------------

class CmdDoneRefusalTests(unittest.TestCase):

    def test_done_refuses_never_item(self):
        """cmd_done returns status=refused for a Never-section item (exit 1)."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_WITH_NEVER)

            r = run(
                "done",
                "--pattern", "Declined task",
                "--file", str(todo_path),
                expect=1,
            )
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "refused")
            self.assertIn("terminal", data["reason"].lower())

    def test_done_refuses_never_item_file_unchanged(self):
        """After refusal, the Never item is still [ ] (not [x])."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_WITH_NEVER)
            before = todo_path.read_text()

            run(
                "done",
                "--pattern", "Declined task",
                "--file", str(todo_path),
                # no expect — we don't care about exit code here, just file state
            )
            after = todo_path.read_text()
            self.assertEqual(before, after, "File must not change on refusal")
            self.assertIn("- [ ] Declined task that should be hidden", after)


# ---------------------------------------------------------------------------
# (8) A Now item still completes normally
# ---------------------------------------------------------------------------

class CmdDoneActiveItemTests(unittest.TestCase):

    def test_done_completes_now_item(self):
        """cmd_done still marks a Now-section item [x] — guard is narrow."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_WITH_NEVER)

            r = run(
                "done",
                "--pattern", "Active task",
                "--file", str(todo_path),
                expect=0,
            )
            data = json.loads(r.stdout)
            self.assertTrue(data["success"])
            content = todo_path.read_text()
            self.assertIn("- [x]", content)
            self.assertIn("Active task", content)


# ---------------------------------------------------------------------------
# (9) Scheduled is still active / counted / an add-target
# ---------------------------------------------------------------------------

class ScheduledIntactTests(unittest.TestCase):

    def test_scheduled_item_counted(self):
        """A Scheduled item appears in _bucket_counts (Scheduled: 1)."""
        parsed = parse_text(TODO_SCHEDULED)
        counts = _todo._bucket_counts(parsed)
        self.assertEqual(counts.get("Scheduled", 0), 1,
                         "Scheduled item must appear in bucket counts")

    def test_scheduled_is_valid_add_target(self):
        """add --bucket SCHEDULED succeeds (Scheduled remains in add choices)."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(TODO_ACTIVE_ONLY)

            r = run(
                "add", "Future task",
                "--bucket", "SCHEDULED",
                "--file", str(todo_path),
                expect=0,
            )
            data = json.loads(r.stdout)
            self.assertTrue(data["success"])
            content = todo_path.read_text()
            self.assertIn("## Scheduled", content)
            self.assertIn("- [ ] Future task", content)

    def test_scheduled_in_active_open_items(self):
        """Scheduled items are still included in active_open_items (not terminal)."""
        parsed = parse_text(TODO_SCHEDULED)
        scheduled_actives = [
            item for item in parsed["active_open_items"]
            if item["section"] == "scheduled"
        ]
        self.assertEqual(len(scheduled_actives), 1,
                         "Scheduled item must appear in active_open_items")

    def test_scheduled_not_in_terminal_sections(self):
        """Scheduled is not classified as a terminal section."""
        self.assertNotIn("scheduled", _todo.TERMINAL_SECTIONS)

    def test_scheduled_in_active_sections(self):
        """Scheduled is still in ACTIVE_SECTIONS."""
        self.assertIn("scheduled", _todo.ACTIVE_SECTIONS)


# ---------------------------------------------------------------------------
# (10) cleanup does NOT sweep Never items into Done
# ---------------------------------------------------------------------------

class CleanupNeverTests(unittest.TestCase):

    def test_cleanup_leaves_never_items_in_place(self):
        """cleanup moves [x] active items to Done but leaves Never items untouched."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            content = (
                "## Now\n\n"
                "- [x] Completed item — **DONE 2026-06-25.**\n\n"
                "## Never\n\n"
                "- [ ] Declined item stays here\n\n"
                "## Done\n\n"
            )
            todo_path.write_text(content)

            r = run(
                "cleanup",
                "--project", str(td),
                "--cwd", str(td),
                expect=0,
            )

            after = todo_path.read_text()
            # Completed item moved to Done (cleanup strips [x] → bare entry with diary link)
            self.assertIn("Completed item", after)
            self.assertIn("## Done", after)
            # Never item still in Never (not moved to Done)
            self.assertIn("## Never", after)
            self.assertIn("- [ ] Declined item stays here", after)
            # Never section still before Done
            never_pos = after.index("## Never")
            done_pos = after.index("## Done")
            self.assertLess(never_pos, done_pos)

    def test_cleanup_never_item_not_in_done_section(self):
        """After cleanup, Never items do not appear inside the ## Done section."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            content = (
                "## Now\n\n"
                "- [x] Completed item — **DONE 2026-06-25.**\n\n"
                "## Never\n\n"
                "- [ ] Declined item stays here\n\n"
                "## Done\n\n"
            )
            todo_path.write_text(content)

            run(
                "cleanup",
                "--project", str(td),
                "--cwd", str(td),
                expect=0,
            )

            after = todo_path.read_text()
            # Find position of ## Done
            done_pos = after.index("## Done")
            done_section = after[done_pos:]
            # The declined item must NOT appear in the Done section
            self.assertNotIn("Declined item stays here", done_section,
                              "Never item must not be swept into Done section")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)

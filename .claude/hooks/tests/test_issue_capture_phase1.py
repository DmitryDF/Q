#!/usr/bin/env python3
"""Tests for issue-capture-workflow Phase 1 (A4/A5/A6/A7).

Covers:
  A4: todo.py link-related — back-ref appended, atomic write, multi-target, ambiguous error
  A5: pre_plan_gates.py arm/confirm/is-audit-capture-confirmed round-trip, session-bound
  A6: check-audit-capture-confirm.sh — deny armed-unconfirmed, allow confirmed, allow untouched
  A7: todo.py add --require-confirm — refuses without marker, writes with marker, no regression

Run: python3 ${KIT_HOOKS_DIR}/tests/test_issue_capture_phase1.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path.home() / ".claude" / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"
TODO_PY = HOOKS / "todo.py"
HOOK_SH = HOOKS / "check-audit-capture-confirm.sh"

sys.path.insert(0, str(HOOKS))


def run_ppg(*args, expect=0, env=None):
    """Run pre_plan_gates.py with given args."""
    e = dict(os.environ)
    if env:
        e.update(env)
    result = subprocess.run(
        ["python3", str(PPG_PY), *args],
        capture_output=True, text=True, env=e,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"pre_plan_gates.py {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def run_todo(*args, expect=0, env=None):
    """Run todo.py with given args."""
    e = dict(os.environ)
    if env:
        e.update(env)
    result = subprocess.run(
        ["python3", str(TODO_PY), *args],
        capture_output=True, text=True, env=e,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"todo.py {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def run_hook(tool_name, file_path, session_id, env=None):
    """Run check-audit-capture-confirm.sh with a simulated PreToolUse payload."""
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        "session_id": session_id,
    })
    e = dict(os.environ)
    if env:
        e.update(env)
    result = subprocess.run(
        [str(HOOK_SH)],
        input=payload, capture_output=True, text=True, env=e,
    )
    return result


def make_todo_file(tmpdir, content):
    """Write a TODO.md to tmpdir and return its path."""
    p = Path(tmpdir) / "TODO.md"
    p.write_text(content)
    return p


SAMPLE_TODO = """\
# Test Project

## Now

- [ ] **Alpha task** — first item about alpha stuff
- [ ] **Beta analysis** — second item about beta review
- [ ] **Gamma issue** — root cause gamma processing

## Next

- [ ] **Delta feature** — upcoming delta work

"""


# =============================================================================
# A4: link-related
# =============================================================================

class TestLinkRelated(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _todo_path(self):
        return make_todo_file(self.tmpdir, SAMPLE_TODO)

    def test_back_ref_appended_single_target(self):
        todo = self._todo_path()
        result = run_todo(
            "link-related",
            "--pattern", "Alpha task",
            "--target-line", "5",  # Beta analysis (approx line 5)
            "--file", str(todo),
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["success"])
        self.assertIn("related: L5", data["refs_added"])
        content = todo.read_text()
        # Source item (Alpha) should now have the ref
        self.assertIn("(related: L5)", content)

    def test_multi_target_refs(self):
        todo = self._todo_path()
        result = run_todo(
            "link-related",
            "--pattern", "Alpha task",
            "--target-line", "5", "6",
            "--file", str(todo),
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["success"])
        content = todo.read_text()
        self.assertIn("(related: L5)", content)
        self.assertIn("(related: L6)", content)

    def test_deduplicates_target_lines(self):
        todo = self._todo_path()
        result = run_todo(
            "link-related",
            "--pattern", "Alpha task",
            "--target-line", "5", "5", "6",
            "--file", str(todo),
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["success"])
        # Should appear only once
        refs = data["refs_added"]
        self.assertEqual(refs.count("(related: L5)"), 1)

    def test_no_match_returns_error(self):
        todo = self._todo_path()
        result = run_todo(
            "link-related",
            "--pattern", "nonexistent-xyz-task",
            "--target-line", "5",
            "--file", str(todo),
            expect=1,
        )
        data = json.loads(result.stdout)
        self.assertIn("error", data)

    def test_ambiguous_pattern_returns_error(self):
        # Both "Alpha" and "Beta" match "task" or "item"
        todo = self._todo_path()
        result = run_todo(
            "link-related",
            "--pattern", "item",  # matches nothing — use broader
            "--target-line", "5",
            "--file", str(todo),
            expect=1,
        )
        data = json.loads(result.stdout)
        self.assertIn("error", data)

    def test_atomic_write_preserves_file(self):
        todo = self._todo_path()
        original_lines = todo.read_text().splitlines()
        run_todo(
            "link-related",
            "--pattern", "Gamma issue",
            "--target-line", "4",
            "--file", str(todo),
        )
        new_lines = todo.read_text().splitlines()
        # Line count unchanged (back-ref appended to existing line)
        self.assertEqual(len(original_lines), len(new_lines))


# =============================================================================
# A5: arm/confirm/is-audit-capture-confirmed
# =============================================================================

class TestAuditCaptureMarkers(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "session-state"
        self.state_dir.mkdir()
        self.env = {
            "HOME": self.tmpdir,
            "CLAUDE_CODE_REMOTE": "",
        }
        # Patch STATE_DIR via HOME override — pre_plan_gates uses Path.home() / ".claude" / "session-state"
        # We need to intercept via env HOME
        claude_dir = Path(self.tmpdir) / ".claude"
        claude_dir.mkdir()
        self.session_dir = claude_dir / "session-state"
        self.session_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_ppg(self, *args, expect=0):
        return run_ppg(*args, expect=expect, env={"HOME": self.tmpdir})

    def test_arm_creates_marker(self):
        sid = "test-session-arm-001"
        self._run_ppg("arm-audit-capture", sid)
        arm_file = self.session_dir / f"_audit-arm-{sid}"
        self.assertTrue(arm_file.exists(), "arm marker file should be created")

    def test_is_confirmed_not_armed_exits_2(self):
        sid = "test-session-not-armed-002"
        # No arm call → exit 2
        self._run_ppg("is-audit-capture-confirmed", sid, expect=2)

    def test_armed_not_confirmed_exits_1(self):
        sid = "test-session-armed-003"
        self._run_ppg("arm-audit-capture", sid)
        self._run_ppg("is-audit-capture-confirmed", sid, expect=1)

    def test_arm_then_confirm_exits_0(self):
        sid = "test-session-confirm-004"
        self._run_ppg("arm-audit-capture", sid)
        self._run_ppg("confirm-audit-capture", sid)
        self._run_ppg("is-audit-capture-confirmed", sid, expect=0)

    def test_confirm_without_arm_raises(self):
        sid = "test-session-no-arm-005"
        self._run_ppg("confirm-audit-capture", sid, expect=1)

    def test_session_isolation(self):
        sid_a = "test-session-iso-a"
        sid_b = "test-session-iso-b"
        self._run_ppg("arm-audit-capture", sid_a)
        self._run_ppg("confirm-audit-capture", sid_a)
        # sid_b is not armed
        self._run_ppg("is-audit-capture-confirmed", sid_a, expect=0)
        self._run_ppg("is-audit-capture-confirmed", sid_b, expect=2)


# =============================================================================
# A6: check-audit-capture-confirm.sh hook
# =============================================================================

class TestAuditCaptureHook(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / ".claude" / "session-state"
        self.state_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_ppg(self, *args, expect=0):
        return run_ppg(*args, expect=expect, env={"HOME": self.tmpdir})

    def _run_hook(self, tool_name, file_path, session_id):
        return run_hook(tool_name, file_path, session_id, env={"HOME": self.tmpdir})

    def test_unrelated_path_allowed(self):
        result = self._run_hook("Write", "/some/path/Workflow.md", "sid-007")
        self.assertEqual(result.returncode, 0)
        # No deny output
        self.assertNotIn("permissionDecision", result.stdout)

    def test_audit_path_not_armed_allowed(self):
        # Not armed → allow (no capture in progress)
        result = self._run_hook("Write", "/proj/session_issue_audit_20260519_abc.md", "sid-008")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("permissionDecision", result.stdout)

    def test_audit_path_armed_unconfirmed_denied(self):
        sid = "sid-deny-009"
        self._run_ppg("arm-audit-capture", sid)
        result = self._run_hook("Write", "/proj/session_issue_audit_20260519_abc.md", sid)
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_audit_path_confirmed_allowed(self):
        sid = "sid-allow-010"
        self._run_ppg("arm-audit-capture", sid)
        self._run_ppg("confirm-audit-capture", sid)
        result = self._run_hook("Write", "/proj/session_issue_audit_20260519_abc.md", sid)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("permissionDecision", result.stdout)

    def test_non_write_tool_allowed(self):
        sid = "sid-read-011"
        self._run_ppg("arm-audit-capture", sid)
        result = self._run_hook("Read", "/proj/session_issue_audit_20260519_abc.md", sid)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("permissionDecision", result.stdout)

    def test_regression_workflow_md_not_affected(self):
        sid = "sid-workflow-012"
        self._run_ppg("arm-audit-capture", sid)
        # Workflow.md write should pass through (handled by check-workflow-confirm.sh, not this hook)
        result = self._run_hook("Write", "/proj/Workflow.md", sid)
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("permissionDecision", result.stdout)


# =============================================================================
# A7: add --require-confirm
# =============================================================================

class TestAddRequireConfirm(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / ".claude" / "session-state"
        self.state_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _todo_path(self):
        return make_todo_file(self.tmpdir, SAMPLE_TODO)

    def _run_ppg(self, *args, expect=0):
        return run_ppg(*args, expect=expect, env={"HOME": self.tmpdir})

    def _run_todo(self, *args, expect=0):
        return run_todo(*args, expect=expect, env={"HOME": self.tmpdir})

    def test_require_confirm_refuses_without_confirmation(self):
        sid = "sid-rc-001"
        todo = self._todo_path()
        self._run_ppg("arm-audit-capture", sid)
        # Armed but not confirmed → refuse
        result = self._run_todo(
            "add", "New audit issue",
            "--bucket", "NOW",
            "--file", str(todo),
            "--require-confirm", sid,
            expect=1,
        )
        data = json.loads(result.stdout)
        self.assertIn("error", data)
        self.assertIn("not confirmed", data["error"])

    def test_require_confirm_writes_when_confirmed(self):
        sid = "sid-rc-002"
        todo = self._todo_path()
        self._run_ppg("arm-audit-capture", sid)
        self._run_ppg("confirm-audit-capture", sid)
        result = self._run_todo(
            "add", "New audit issue",
            "--bucket", "NOW",
            "--file", str(todo),
            "--require-confirm", sid,
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["success"])
        content = todo.read_text()
        self.assertIn("New audit issue", content)

    def test_normal_add_unchanged_no_flag(self):
        todo = self._todo_path()
        # No --require-confirm → normal behavior regardless of any arm state
        result = self._run_todo(
            "add", "Normal task without audit",
            "--bucket", "NEXT",
            "--file", str(todo),
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["success"])
        content = todo.read_text()
        self.assertIn("Normal task without audit", content)

    def test_normal_add_not_affected_by_armed_session(self):
        sid = "sid-rc-003"
        todo = self._todo_path()
        self._run_ppg("arm-audit-capture", sid)
        # Add without --require-confirm → goes through even though session is armed
        result = self._run_todo(
            "add", "Regular task",
            "--bucket", "NOW",
            "--file", str(todo),
        )
        data = json.loads(result.stdout)
        self.assertTrue(data["success"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

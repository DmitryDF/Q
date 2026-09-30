#!/usr/bin/env python3
"""Tests for Plan 6 — Workflow.md drafting protocol (A1-A11).

Covers:
  (a) confirm-workflow-draft writes HTML-comment marker to topic state
  (b) is-workflow-confirmed exits 0 with marker, exits 1 without or mismatched session
  (c) factcheck-workflow orchestrates 4 checkers and writes round files
  (d) check-workflow-confirm.sh denies Write of new */Workflow.md without marker
  (e) hook fall-through when no topic state (E2)
  (f) flock prevents concurrent factcheck races (EC3)
  (g) frontmatter breadcrumb appended on PASS (EC4)
  (h) topic re-classification carries over validation rounds (EC5)
  (i) hook allows existing Workflow.md (E3)
  (j) _approval_patterns.py is importable and has expected patterns

Run: python3 ${KIT_HOOKS_DIR}/tests/test_plan6_commands.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HOOKS = Path.home() / ".claude" / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"
HOOK_SH = HOOKS / "check-workflow-confirm.sh"

# Insert hooks dir into path for direct imports
sys.path.insert(0, str(HOOKS))


def run_py(*args, expect=0, stdin=None, cwd=None):
    result = subprocess.run(
        ["python3", str(PPG_PY), *args],
        capture_output=True, text=True, input=stdin, cwd=cwd,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"pre_plan_gates.py {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _setup_active_project(sid, proj, topic, tmpdir):
    """Helper: create active pointer + topic state files in a tmpdir-based env."""
    state_dir = Path(tmpdir) / "state" / "pre_plan_gates"
    state_dir.mkdir(parents=True)
    active = {sid: {"topic_slug": proj, "active_project": topic, "updated": "2026-01-01T00:00:00+00:00"}}
    (state_dir / "_active.json").write_text(json.dumps(active))
    topic_state = {
        "topic_slug": proj,
        "project_slug": topic,
        "classification": "problem_to_solve",
        "bypass_marker": False,
        "bypass_reason": None,
        "thought_file_path": None,
        "workflow_draft_confirmed_marker": None,
        "created": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-01T00:00:00+00:00",
    }
    (state_dir / f"{proj}__{topic}.json").write_text(json.dumps(topic_state))
    return state_dir


class ApprovalPatternsTests(unittest.TestCase):
    """(j) _approval_patterns.py is importable and has expected patterns."""

    def test_import_and_patterns(self):
        import _approval_patterns
        self.assertIn("approved", _approval_patterns.APPROVAL_PROMPT_PATTERNS)
        self.assertIn("approve", _approval_patterns.APPROVAL_PROMPT_PATTERNS)
        self.assertGreaterEqual(len(_approval_patterns.APPROVAL_PROMPT_PATTERNS), 10)

    def test_watchlist_still_imports(self):
        """Ensure watchlist-present-gate.py still imports without error after refactor."""
        result = subprocess.run(
            ["python3", "-c", "import sys; sys.path.insert(0, str(__import__('pathlib').Path.home() / '.claude' / 'hooks')); import watchlist_present_gate"],
            capture_output=True, text=True,
        )
        # Module name uses dashes converted to underscores; try direct import via -c
        result2 = subprocess.run(
            ["python3", str(HOOKS / "watchlist-present-gate.py"), "--help"],
            capture_output=True, text=True,
        )
        # Either import works or the --help fails gracefully (no syntax error)
        self.assertNotIn("SyntaxError", result2.stderr)
        self.assertNotIn("ImportError", result2.stderr)
        self.assertNotIn("ModuleNotFoundError", result2.stderr)


class ConfirmWorkflowDraftTests(unittest.TestCase):
    """(a) confirm-workflow-draft writes marker to topic state."""

    SID = "test-p6-confirm"
    PROJ = "TestProj"
    TOPIC = "test-topic"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_confirm_writes_marker(self):
        import pre_plan_gates as ppg
        result = ppg.confirm_workflow_draft(self.SID)
        self.assertEqual(result["status"], "workflow_draft_confirmed")
        expected_marker = f"<!-- WORKFLOW_DRAFT_CONFIRMED:{self.SID} -->"
        self.assertEqual(result["marker"], expected_marker)

        state_path = ppg.TOPIC_STATE_DIR / f"{self.PROJ}__{self.TOPIC}.json"
        state = json.loads(state_path.read_text())
        self.assertEqual(state["workflow_draft_confirmed_marker"], expected_marker)

    def test_confirm_no_active_project_raises(self):
        import pre_plan_gates as ppg
        with self.assertRaises(ValueError):
            ppg.confirm_workflow_draft("nonexistent-session-id")


class IsWorkflowConfirmedTests(unittest.TestCase):
    """(b) is-workflow-confirmed exits 0 with marker, exits 1 without or mismatched session."""

    SID = "test-p6-isconf"
    PROJ = "TestProj"
    TOPIC = "test-topic-isconf"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_returns_false_without_marker(self):
        import pre_plan_gates as ppg
        self.assertFalse(ppg.is_workflow_confirmed(self.SID))

    def test_returns_true_after_confirm(self):
        import pre_plan_gates as ppg
        ppg.confirm_workflow_draft(self.SID)
        self.assertTrue(ppg.is_workflow_confirmed(self.SID))

    def test_returns_false_mismatched_session(self):
        import pre_plan_gates as ppg
        ppg.confirm_workflow_draft(self.SID)
        self.assertFalse(ppg.is_workflow_confirmed("different-session-id"))

    def test_returns_false_no_active_project(self):
        import pre_plan_gates as ppg
        self.assertFalse(ppg.is_workflow_confirmed("totally-unknown-session"))

    def test_cli_exits_3_for_no_active_project(self):
        """CLI exits 3 (not 1) when there's no active topic — hook E2 fall-through."""
        result = subprocess.run(
            ["python3", str(PPG_PY), "is-workflow-confirmed", "totally-unknown-session-cli"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 3)


class FactcheckWorkflowTests(unittest.TestCase):
    """(c) factcheck-workflow orchestrates 4 checkers and writes round files.
    (f) flock prevents concurrent factcheck races (EC3).
    (g) frontmatter breadcrumb appended on PASS (EC4).
    """

    SID = "test-p6-factcheck"
    PROJ = "TestProj"
    TOPIC = "test-topic-fc"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self._orig_validation_dir = ppg.WORKFLOW_VALIDATION_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir
        self.validation_dir = Path(self.tmpdir) / "workflow_validation"
        ppg.WORKFLOW_VALIDATION_DIR = self.validation_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        ppg.WORKFLOW_VALIDATION_DIR = self._orig_validation_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mock_checker_pass(self, draft_path, checker_idx, model, round_num, prior_issues):
        return "PASS"

    def _mock_checker_fail_r1_pass_r2(self, draft_path, checker_idx, model, round_num, prior_issues):
        if round_num == 1 and checker_idx == 0:
            return "DISCREPANCY: missing Automation boundary in Stage 1"
        return "PASS"

    def test_writes_round_file_on_pass(self):
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n\n# Workflow\n")

        result = ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=self._mock_checker_pass)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 1)

        topic_dir = self.validation_dir / self.PROJ / self.TOPIC
        self.assertTrue((topic_dir / "R1.md").exists())

    def test_round_file_format(self):
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n\n# Workflow\n")

        ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=self._mock_checker_pass)
        topic_dir = self.validation_dir / self.PROJ / self.TOPIC
        r1 = (topic_dir / "R1.md").read_text()
        self.assertIn("schema_version: 3", r1)  # S2/A9 epoch bump
        self.assertIn("rounds: 1", r1)
        self.assertIn("checker_count: 3", r1)
        self.assertIn("verdict: PASS", r1)
        self.assertIn("## Checker 1 (sonnet)", r1)
        self.assertIn("## Checker 3 (sonnet)", r1)
        self.assertIn("## Aggregated verdict", r1)

    def test_dirty_round_then_pass(self):
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n\n# Workflow\n")

        result = ppg.factcheck_workflow(
            self.SID, str(draft), _checker_fn=self._mock_checker_fail_r1_pass_r2
        )
        # R1 is DIRTY, R2 should be PASS
        self.assertEqual(result["status"], "PASS")
        topic_dir = self.validation_dir / self.PROJ / self.TOPIC
        self.assertTrue((topic_dir / "R1.md").exists())
        self.assertTrue((topic_dir / "R2.md").exists())

    def test_hard_stop_at_r5(self):
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n\n# Workflow\n")

        def always_fail(draft_path, idx, model, round_num, prior_issues):
            return "DISCREPANCY: always fails"

        result = ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=always_fail)
        self.assertEqual(result["status"], "ESCALATE")
        self.assertEqual(result["reason"], "no_consensus_after_2_rounds")
        self.assertEqual(result["rounds"], 2)
        topic_dir = self.validation_dir / self.PROJ / self.TOPIC
        self.assertTrue((topic_dir / "R2.md").exists())

    def test_frontmatter_breadcrumb_on_pass(self):
        """(g) validated_via: appended to Workflow.md frontmatter on PASS."""
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n\n# Workflow\n")

        ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=self._mock_checker_pass)
        content = draft.read_text()
        self.assertIn("validated_via:", content)

    def test_lock_prevents_concurrent_factcheck(self):
        """(f) flock prevents a second factcheck from running concurrently."""
        import pre_plan_gates as ppg
        import fcntl

        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n\n# Workflow\n")

        topic_dir = self.validation_dir / self.PROJ / self.TOPIC
        lock_dir = topic_dir / "workflow"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lockfile = lock_dir / ".lock"

        results = {}

        def hold_lock():
            with open(lockfile, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                time.sleep(0.5)
                fcntl.flock(lf, fcntl.LOCK_UN)

        t = threading.Thread(target=hold_lock)
        t.start()
        time.sleep(0.05)  # let thread acquire lock

        result = ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=self._mock_checker_pass)
        results["second"] = result
        t.join()

        self.assertEqual(results["second"]["status"], "LOCKED")


class HookDenyTests(unittest.TestCase):
    """(d) hook denies new Workflow.md without marker.
    (e) hook falls through when no topic state (E2).
    (i) hook allows existing Workflow.md (E3).
    """

    SID = "test-p6-hook"
    PROJ = "TestProjHook"
    TOPIC = "test-topic-hook"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_hook(self, tool_name, file_path, session_id, extra_env=None):
        payload = json.dumps({
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
            "session_id": session_id,
        })
        env = {**os.environ}
        if extra_env:
            env.update(extra_env)
        result = subprocess.run(
            ["bash", str(HOOK_SH)],
            input=payload, capture_output=True, text=True, env=env,
        )
        return result

    def test_non_workflow_path_passes(self):
        r = self._run_hook("Write", "/some/path/SomeThing.md", self.SID)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("deny", r.stdout)

    def test_existing_workflow_passes(self):
        """E3: existing Workflow.md → allow."""
        existing = Path(self.tmpdir) / "Workflow.md"
        existing.write_text("# Existing workflow\n")
        r = self._run_hook("Write", str(existing), self.SID)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("deny", r.stdout)

    def test_no_topic_state_fallthrough(self):
        """E2: no topic state for session → allow (fall-through, no deny)."""
        new_path = str(Path(self.tmpdir) / "Workflow.md")
        r = self._run_hook("Write", new_path, "nonexistent-session-id-xyz-e2-test")
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("deny", r.stdout)

    def test_new_workflow_without_marker_denied(self):
        """New */Workflow.md without confirmation marker → deny (uses real topic state dir)."""
        import pre_plan_gates as ppg
        # Write a real topic state file so the hook's subprocess sees it
        real_state_dir = ppg.TOPIC_STATE_DIR
        real_state_dir.mkdir(parents=True, exist_ok=True)
        test_sid = "test-p6-hook-deny-real"
        test_proj = "TestHookDeny"
        test_topic = "deny-topic"
        active_path = real_state_dir / "_active.json"
        # Load existing active or start fresh
        existing_active = {}
        if active_path.exists():
            existing_active = json.loads(active_path.read_text())
        existing_active[test_sid] = {
            "topic_slug": test_proj,
            "active_project": test_topic,
            "updated": "2026-01-01T00:00:00+00:00",
        }
        active_path.write_text(json.dumps(existing_active))
        topic_state = {
            "topic_slug": test_proj,
            "project_slug": test_topic,
            "bypass_marker": False,
            "bypass_reason": None,
            "thought_file_path": None,
            "workflow_draft_confirmed_marker": None,
            "created": "2026-01-01T00:00:00+00:00",
            "updated": "2026-01-01T00:00:00+00:00",
        }
        (real_state_dir / f"{test_proj}__{test_topic}.json").write_text(
            json.dumps(topic_state)
        )
        try:
            new_path = str(Path(self.tmpdir) / "project" / "Workflow.md")
            Path(new_path).parent.mkdir(parents=True, exist_ok=True)
            r = self._run_hook("Write", new_path, test_sid)
            self.assertEqual(r.returncode, 0)
            self.assertIn("deny", r.stdout)
        finally:
            # Clean up: remove test entries from real state
            if active_path.exists():
                active = json.loads(active_path.read_text())
                active.pop(test_sid, None)
                active_path.write_text(json.dumps(active))
            topic_file = real_state_dir / f"{test_proj}__{test_topic}.json"
            if topic_file.exists():
                topic_file.unlink()

    def test_non_edit_write_passes(self):
        r = self._run_hook("Bash", "/any/Workflow.md", self.SID)
        self.assertEqual(r.returncode, 0)
        self.assertNotIn("deny", r.stdout)


class TopicReclassificationCarryoverTests(unittest.TestCase):
    """(h) topic re-classification carries over validation rounds (EC5)."""

    SID = "test-p6-reclassify"
    PROJ = "TestProj"
    OLD_TOPIC = "old-topic"
    NEW_TOPIC = "new-topic"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self._orig_validation_dir = ppg.WORKFLOW_VALIDATION_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.OLD_TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir
        self.validation_dir = Path(self.tmpdir) / "workflow_validation"
        ppg.WORKFLOW_VALIDATION_DIR = self.validation_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        ppg.WORKFLOW_VALIDATION_DIR = self._orig_validation_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rounds_copied_to_new_topic(self):
        import pre_plan_gates as ppg

        # Create some fake round files for old topic
        old_dir = self.validation_dir / self.PROJ / self.OLD_TOPIC
        old_dir.mkdir(parents=True)
        (old_dir / "R1.md").write_text("round: 1\nverdict: DIRTY\n")
        (old_dir / "R2.md").write_text("round: 2\nverdict: PASS\n")

        # Also set the confirmation marker on old topic state
        ppg.confirm_workflow_draft(self.SID)

        # Re-classify to new topic (post-Slice-F: create_topic replaces classify;
        # no classification arg — topics are just topics).
        ppg.create_topic(self.SID, project_slug=self.NEW_TOPIC,
                         topic_slug=self.PROJ)

        # New topic dir should have the rounds
        new_dir = self.validation_dir / self.PROJ / self.NEW_TOPIC
        self.assertTrue((new_dir / "R1.md").exists())
        self.assertTrue((new_dir / "R2.md").exists())

        # New topic state should NOT have the confirmation marker (cleared)
        new_state_path = ppg.TOPIC_STATE_DIR / f"{self.PROJ}__{self.NEW_TOPIC}.json"
        new_state = json.loads(new_state_path.read_text())
        self.assertIsNone(new_state.get("workflow_draft_confirmed_marker"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

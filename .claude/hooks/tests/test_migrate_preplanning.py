#!/usr/bin/env python3
"""Tests for the Slice B4 migration script.

Covers:
  * --dry-run on synthesized state dir reports correct counts with no writes
  * --apply produces expected post-state; second --apply is a no-op
  * --apply refuses when any lock file is present

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_migrate_preplanning.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path.home() / ".claude" / "scripts" / "migrate-preplanning-to-clarification.py"


def _run(*args, expect=0):
    result = subprocess.run(
        ["python3", str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"{args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


LEGACY_TOPIC = {
    "topic_slug": "foo",
    "project_slug": "proj",
    "phase": "preplanning",
    "phase_complete": {"thought": True, "preplanning": True},
    "phase_history": [
        {"from": "thought", "to": "preplanning", "at": "2026-05-01T00:00:00+00:00"},
        {"from": "preplanning", "to": "planning", "at": "2026-05-02T00:00:00+00:00"},
    ],
    "preplanning_oqs_cleared": True,
    "preplanning_todo_registered": True,
    "preplanning_session_id": "session-legacy-001",
    "intake_source": "thought",
}

ALREADY_MIGRATED_TOPIC = {
    "topic_slug": "bar",
    "project_slug": "proj",
    "phase": "clarification",
    "phase_complete": {"thought": True, "clarification": True},
    "phase_history": [
        {"from": "thought", "to": "clarification", "at": "2026-06-01T00:00:00+00:00"},
    ],
    "clarification_phase_oqs_cleared": True,
    "clarification_phase_todo_registered": True,
    "clarification_phase_session_id": "session-new-002",
    "intake_source": "thought",
}

UNRELATED_TOPIC = {
    "topic_slug": "baz",
    "project_slug": "proj",
    "phase": "planning",
    "phase_history": [],
    "intake_source": "thought",
}


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="b4-migrate-"))
        self.state_dir = self.tmp / "pre_plan_gates"
        self.locks_dir = self.tmp / "locks"
        self.state_dir.mkdir()
        self.locks_dir.mkdir()
        self.legacy_path = self.state_dir / "foo__proj.json"
        self.legacy_path.write_text(json.dumps(LEGACY_TOPIC, indent=2))
        self.migrated_path = self.state_dir / "bar__proj.json"
        self.migrated_path.write_text(json.dumps(ALREADY_MIGRATED_TOPIC, indent=2))
        self.unrelated_path = self.state_dir / "baz__proj.json"
        self.unrelated_path.write_text(json.dumps(UNRELATED_TOPIC, indent=2))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _common_args(self):
        return [
            "--state-dir", str(self.state_dir),
            "--locks-dir", str(self.locks_dir),
            "--backup-root", str(self.tmp),
        ]

    def test_dry_run_reports_one_modified_no_writes(self):
        r = _run("--dry-run", *self._common_args())
        out = json.loads(r.stdout)
        self.assertEqual(out["mode"], "dry-run")
        self.assertEqual(out["files_scanned"], 3)
        self.assertEqual(out["files_modified"], 1)
        self.assertEqual(out["modified_files"], ["foo__proj.json"])
        self.assertGreaterEqual(out["fields_rewritten"], 6)
        # No backup created on dry-run.
        self.assertIsNone(out["backup_dir"])
        # Files unchanged on disk.
        on_disk = json.loads(self.legacy_path.read_text())
        self.assertEqual(on_disk["phase"], "preplanning")
        self.assertIn("preplanning_oqs_cleared", on_disk)

    def test_apply_migrates_and_is_idempotent(self):
        r = _run("--apply", *self._common_args())
        out = json.loads(r.stdout)
        self.assertEqual(out["mode"], "apply")
        self.assertEqual(out["files_modified"], 1)
        self.assertIsNotNone(out["backup_dir"])
        # Backup contains the pre-migration copy.
        backup_file = Path(out["backup_dir"]) / "foo__proj.json"
        self.assertTrue(backup_file.exists())
        backup_state = json.loads(backup_file.read_text())
        self.assertEqual(backup_state["phase"], "preplanning")
        # On-disk state is migrated.
        migrated = json.loads(self.legacy_path.read_text())
        self.assertEqual(migrated["phase"], "clarification")
        self.assertNotIn("preplanning_oqs_cleared", migrated)
        self.assertNotIn("preplanning_todo_registered", migrated)
        self.assertNotIn("preplanning_session_id", migrated)
        self.assertTrue(migrated["clarification_phase_oqs_cleared"])
        self.assertTrue(migrated["clarification_phase_todo_registered"])
        self.assertEqual(migrated["clarification_phase_session_id"], "session-legacy-001")
        # phase_history rewritten.
        from_to_pairs = [(e.get("from"), e.get("to")) for e in migrated["phase_history"]]
        self.assertIn(("thought", "clarification"), from_to_pairs)
        self.assertIn(("clarification", "planning"), from_to_pairs)
        # phase_complete key renamed.
        self.assertIn("clarification", migrated["phase_complete"])
        self.assertNotIn("preplanning", migrated["phase_complete"])
        # Other files untouched.
        unrelated = json.loads(self.unrelated_path.read_text())
        self.assertEqual(unrelated["phase"], "planning")
        already = json.loads(self.migrated_path.read_text())
        self.assertEqual(already["phase"], "clarification")
        # Re-apply is a no-op.
        r2 = _run("--apply", *self._common_args())
        out2 = json.loads(r2.stdout)
        self.assertEqual(out2["files_modified"], 0)
        self.assertEqual(out2["fields_rewritten"], 0)
        self.assertIsNone(out2["backup_dir"])

    def test_apply_refuses_when_lock_present(self):
        (self.locks_dir / "active.lock").write_text("{}")
        r = _run("--apply", *self._common_args(), expect=1)
        out = json.loads(r.stdout)
        self.assertEqual(out["aborted_locks"], ["active.lock"])
        # Files untouched.
        legacy = json.loads(self.legacy_path.read_text())
        self.assertEqual(legacy["phase"], "preplanning")

    def test_apply_and_dry_run_mutually_exclusive(self):
        r = _run("--apply", "--dry-run", *self._common_args(), expect=2)
        self.assertIn("mutually exclusive", r.stderr)


if __name__ == "__main__":
    unittest.main()

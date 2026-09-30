#!/usr/bin/env python3
"""Tests for S2 commands: todo.py advance-sessions and pre_plan_gates.py append-metrics.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_s2_commands.py

Each test uses an isolated temp dir + a synthetic _active.json / topic state file
so production state is never touched.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Respect CLAUDE_CONFIG_DIR so a claude-experiment clone tests ITS OWN hooks
# (S1: this file used to hardcode live ~/.claude, so a clone's edits were never
# the code under test here).
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
TODO_PY = HOOKS / "todo.py"
PPG_PY = HOOKS / "pre_plan_gates.py"
TOPIC_STATE_DIR = Path.home() / ".claude" / "state" / "pre_plan_gates"


def run(*args, expect=0):
    result = subprocess.run(
        ["python3", *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"command {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


class AdvanceSessionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s2-adv-"))
        self.todo = self.tmp / "TODO.md"
        self.todo.write_text(
            "# TODO\n\n"
            "## Now\n\n"
            "- [ ] [Thought] **Foo** — see Thoughts/foo.md. Outcome. Sessions: 0/3 done.\n"
            "- [ ] [Thought] **Bar** — see Thoughts/bar.md. Outcome. Sessions: 2/3 done.\n"
            "- [ ] Plain item without counter\n"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_increment_only(self):
        r = run(str(TODO_PY), "advance-sessions",
                "--pattern", "Foo", "--file", str(self.todo))
        out = json.loads(r.stdout)
        self.assertEqual(out["counter_after"], "1/3")
        self.assertFalse(out["completed"])
        body = self.todo.read_text()
        self.assertIn("Sessions: 1/3 done.", body)
        # Item still open
        self.assertIn("- [ ] [Thought] **Foo**", body)

    def test_completion_at_n(self):
        r = run(str(TODO_PY), "advance-sessions",
                "--pattern", "Bar", "--file", str(self.todo),
                "--summary", "Shipped.")
        out = json.loads(r.stdout)
        self.assertEqual(out["counter_after"], "3/3")
        self.assertTrue(out["completed"])
        body = self.todo.read_text()
        self.assertIn("- [x] [Thought] **Bar**", body)
        self.assertIn("**DONE", body)
        self.assertIn("Shipped.", body)

    def test_no_match(self):
        r = run(str(TODO_PY), "advance-sessions",
                "--pattern", "Nonexistent", "--file", str(self.todo),
                expect=1)
        out = json.loads(r.stdout)
        self.assertIn("error", out)

    def test_pattern_ambiguous_returns_candidates(self):
        # Both Foo and Bar contain "Thought" — pattern matches multiple
        r = run(str(TODO_PY), "advance-sessions",
                "--pattern", "Thought", "--file", str(self.todo),
                expect=2)
        out = json.loads(r.stdout)
        self.assertEqual(len(out["candidates"]), 2)

    def test_pattern_matches_only_counter_items(self):
        # "Plain item" has no counter — must not match even if pattern hits
        r = run(str(TODO_PY), "advance-sessions",
                "--pattern", "Plain", "--file", str(self.todo),
                expect=1)
        out = json.loads(r.stdout)
        self.assertIn("error", out)


class AppendMetricsTests(unittest.TestCase):
    SID = "test-sid-zzz"
    PROJ = "tests2"
    TOPIC = "appendmetrics"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s2-met-"))
        self.thought = self.tmp / "topic_THOUGHT.md"
        self.thought.write_text(
            "# Topic\n\n"
            "## Snapshot\n### Problem\nx\n### Guiding Policy\nx\n"
            "### Scope\nx\n### Open Questions\nx\n\n"
            "## 11. Metrics\n\n*Appended at each `/close`.*\n\n"
            "## 12. Next Step\n\nGo.\n"
        )
        # Synthetic topic state pointing at the temp file
        TOPIC_STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.topic_state = TOPIC_STATE_DIR / f"{self.PROJ}__{self.TOPIC}.json"
        self.topic_state.write_text(json.dumps({
            "topic_slug": self.PROJ,
            "project_slug": self.TOPIC,
            "classification": "problem_to_solve",
            "bypass_marker": False,
            "bypass_reason": None,
            "thought_file_path": str(self.thought),
            "created": "2026-05-04T00:00:00+00:00",
            "updated": "2026-05-04T00:00:00+00:00",
        }))
        # Register in _active.json (preserving any existing entries)
        active_path = TOPIC_STATE_DIR / "_active.json"
        active = json.loads(active_path.read_text()) if active_path.exists() else {}
        self._active_backup = dict(active)
        active[self.SID] = {
            "topic_slug": self.PROJ,
            "active_project": self.TOPIC,
            "updated": "2026-05-04T00:00:00+00:00",
        }
        active_path.write_text(json.dumps(active, indent=2))

    def tearDown(self):
        # Restore _active.json
        active_path = TOPIC_STATE_DIR / "_active.json"
        active_path.write_text(json.dumps(self._active_backup, indent=2))
        if self.topic_state.exists():
            self.topic_state.unlink()
        shutil.rmtree(self.tmp)

    def _payload(self, **over):
        base = {
            "duration_min": 30,
            "opus_tokens_k": 100,
            "sonnet_tokens_k": 20,
            "tasks_completed": 3,
            "files_touched": ["a.py"],
            "gate_progress": "S → T",
        }
        base.update(over)
        return json.dumps(base)

    def test_basic_append(self):
        r = run(str(PPG_PY), "append-metrics", self.SID, self._payload())
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "metrics_appended")
        body = self.thought.read_text()
        self.assertIn("session [sid:test-sid", body)
        self.assertIn("- Duration: 30min", body)
        self.assertIn("- Opus tokens: ~100k", body)
        self.assertIn("- Files touched: a.py", body)

    def test_idempotent_same_day(self):
        run(str(PPG_PY), "append-metrics", self.SID, self._payload())
        r = run(str(PPG_PY), "append-metrics", self.SID,
                self._payload(duration_min=60, tasks_completed=9))
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "metrics_replaced")
        body = self.thought.read_text()
        # Only one block with this header
        self.assertEqual(body.count(out["block_header"]), 1)
        self.assertIn("- Duration: 60min", body)
        self.assertNotIn("- Duration: 30min", body)

    def test_omtm_rows(self):
        run(str(PPG_PY), "append-metrics", self.SID, self._payload(
            omtm="bypass-rate",
            omtm_value=0.05,
            line_in_sand="<10%",
            omtm_prior=0.12,
            decision_rule_trigger="no",
        ))
        body = self.thought.read_text()
        self.assertIn("OMTM (per Gate 4): bypass-rate = 0.05 (target: <10%)", body)
        self.assertIn("OMTM trajectory: 0.12 → 0.05 (decision rule trigger: no)", body)

    def test_omtm_omitted_when_absent(self):
        run(str(PPG_PY), "append-metrics", self.SID, self._payload())
        body = self.thought.read_text()
        self.assertNotIn("OMTM", body)

    def test_missing_thought_file_errors(self):
        # Point thought_file_path at a non-existent path
        state = json.loads(self.topic_state.read_text())
        state["thought_file_path"] = str(self.tmp / "does-not-exist.md")
        self.topic_state.write_text(json.dumps(state))
        run(str(PPG_PY), "append-metrics", self.SID, self._payload(), expect=1)

    def test_no_active_project_errors(self):
        # Use an unrelated SID
        run(str(PPG_PY), "append-metrics", "no-such-sid",
            self._payload(), expect=1)


class ReadCommandTopicOnlyTest(unittest.TestCase):
    """`read SID` must surface the topic_state block even with no session-level state."""

    SID = "test-sid-readonly"
    PROJ = "testread"
    TOPIC = "readonly"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s2-read-"))
        TOPIC_STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.topic_state = TOPIC_STATE_DIR / f"{self.PROJ}__{self.TOPIC}.json"
        self.topic_state.write_text(json.dumps({
            "topic_slug": self.PROJ, "project_slug": self.TOPIC,
            "bypass_marker": False,
            "bypass_reason": None,
            "thought_file_path": "Thoughts/foo.md",
            "created": "2026-05-04T00:00:00+00:00",
            "updated": "2026-05-04T00:00:00+00:00",
        }))
        active_path = TOPIC_STATE_DIR / "_active.json"
        active = json.loads(active_path.read_text()) if active_path.exists() else {}
        self._backup = dict(active)
        active[self.SID] = {
            "topic_slug": self.PROJ, "active_project": self.TOPIC,
            "updated": "2026-05-04T00:00:00+00:00",
        }
        active_path.write_text(json.dumps(active, indent=2))

    def tearDown(self):
        (TOPIC_STATE_DIR / "_active.json").write_text(
            json.dumps(self._backup, indent=2)
        )
        if self.topic_state.exists():
            self.topic_state.unlink()
        shutil.rmtree(self.tmp)

    def test_topic_only_status(self):
        r = run(str(PPG_PY), "read", self.SID)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "topic_only")
        self.assertEqual(
            out["topic_state"]["thought_file_path"], "Thoughts/foo.md"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""Tests for scripts/recover-topic-state-candidate.py (plan eager-inventing-hare.md V1(a))."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT_PATH = Path.home() / ".claude" / "scripts" / "recover-topic-state-candidate.py"


def _import_helper():
    spec = importlib.util.spec_from_file_location("recover_helper", SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RecoverTopicStateCandidateTests(unittest.TestCase):

    PROJ = "Projects"
    TOPIC = "research-scope-and-focus"

    def setUp(self):
        self.helper = _import_helper()
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_jsonl_with_read_result(self, sid, state_obj):
        """Write a jsonl file containing one tool_result block whose content
        is the cat -n formatted JSON of state_obj."""
        body = json.dumps(state_obj, indent=2)
        cat_n = "\n".join(f"{i+1}\t{ln}" for i, ln in enumerate(body.splitlines()))
        rec = {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_test",
                     "content": cat_n},
                ],
            },
        }
        path = self.tmpdir / f"{sid}.jsonl"
        path.write_text(json.dumps(rec) + "\n")
        return path

    def test_finds_payload_in_single_sid(self):
        sid = "sid-aaaaaaaa"
        state = {
            "topic_slug": self.TOPIC,
            "project_slug": self.PROJ,
            "phase": "implementation",
            "phase_history": [{"from": None, "to": "thought", "at": "t0"}],
            "phase_complete": {"thought": True},
            "clarification_step": 10,
        }
        self._write_jsonl_with_read_result(sid, state)
        result = self.helper.recover_candidate(
            project_slug=self.PROJ,
            topic_slug=self.TOPIC,
            sids=[sid],
            projects_dir=self.tmpdir,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["phase"], "implementation")
        self.assertEqual(result["clarification_step"], 10)
        self.assertEqual(result["phase_complete"], {"thought": True})
        self.assertTrue(result["source"].startswith(sid + ":"))

    def test_returns_null_when_no_evidence(self):
        sid = "sid-bbbbbbbb"
        # Empty jsonl
        (self.tmpdir / f"{sid}.jsonl").write_text("")
        result = self.helper.recover_candidate(
            project_slug=self.PROJ,
            topic_slug=self.TOPIC,
            sids=[sid],
            projects_dir=self.tmpdir,
        )
        self.assertIsNone(result)

    def test_priority_order_first_match_wins(self):
        """When two sids both carry evidence, the FIRST sid in the priority
        list wins (caller passes newest first)."""
        sid_new = "sid-new00000"
        sid_old = "sid-old00000"
        self._write_jsonl_with_read_result(sid_new, {
            "topic_slug": self.TOPIC, "project_slug": self.PROJ,
            "phase": "implementation",
            "phase_history": [], "phase_complete": {}, "clarification_step": 10,
        })
        self._write_jsonl_with_read_result(sid_old, {
            "topic_slug": self.TOPIC, "project_slug": self.PROJ,
            "phase": "thought",
            "phase_history": [], "phase_complete": {}, "clarification_step": 0,
        })
        result = self.helper.recover_candidate(
            project_slug=self.PROJ,
            topic_slug=self.TOPIC,
            sids=[sid_new, sid_old],
            projects_dir=self.tmpdir,
        )
        self.assertEqual(result["phase"], "implementation")
        self.assertTrue(result["source"].startswith(sid_new + ":"))

    def test_later_lines_win_within_sid(self):
        """When the same sid carries multiple matching payloads, the latest
        line wins."""
        sid = "sid-multipay"
        # Write two records: earlier with phase=thought, later with phase=implementation
        early = {
            "type": "user",
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "t1",
                "content": "\n".join(f"{i+1}\t{ln}" for i, ln in enumerate(
                    json.dumps({
                        "topic_slug": self.TOPIC,
                        "project_slug": self.PROJ,
                        "phase": "thought",
                        "phase_history": [], "phase_complete": {},
                        "clarification_step": 0,
                    }, indent=2).splitlines())),
            }]},
        }
        late = {
            "type": "user",
            "message": {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "t2",
                "content": "\n".join(f"{i+1}\t{ln}" for i, ln in enumerate(
                    json.dumps({
                        "topic_slug": self.TOPIC,
                        "project_slug": self.PROJ,
                        "phase": "implementation",
                        "phase_history": [], "phase_complete": {},
                        "clarification_step": 10,
                    }, indent=2).splitlines())),
            }]},
        }
        path = self.tmpdir / f"{sid}.jsonl"
        path.write_text(json.dumps(early) + "\n" + json.dumps(late) + "\n")
        result = self.helper.recover_candidate(
            project_slug=self.PROJ,
            topic_slug=self.TOPIC,
            sids=[sid],
            projects_dir=self.tmpdir,
        )
        self.assertEqual(result["phase"], "implementation")

    def test_filters_by_topic_and_project_slug(self):
        sid = "sid-wrongtop"
        # State for a different topic — should not match
        self._write_jsonl_with_read_result(sid, {
            "topic_slug": "some-other-topic",
            "project_slug": self.PROJ,
            "phase": "implementation",
            "phase_history": [], "phase_complete": {}, "clarification_step": 0,
        })
        result = self.helper.recover_candidate(
            project_slug=self.PROJ,
            topic_slug=self.TOPIC,
            sids=[sid],
            projects_dir=self.tmpdir,
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()

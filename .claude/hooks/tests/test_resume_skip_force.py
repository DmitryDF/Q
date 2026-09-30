#!/usr/bin/env python3
"""Regression tests for the research-fc resume-skip fix (research-fc-resume-skip-force, 2026-07-14).

Maps to the plan's Outcome Claims:
  C1/C3/C4/C5 — force=True on a maxed-out artifact clears prior R-markers and re-verifies
                from round 1 (checkers dispatched, rounds start at 1).
  C2          — no-force on a maxed-out artifact returns an honest NOOP (status NOOP,
                reason already_at_max_rounds), dispatches ZERO checkers, writes no new
                marker, and leaves the prior markers intact.
  C6          — existing < max_rounds still resumes the remaining rounds (dispatch > 0).
  C7          — a genuine final-round no-consensus still ESCALATEs (the guard never masks
                a real failure).
  C9          — the guard is kind-agnostic (a non-research kind inherits the same NOOP).

Imports the _factcheck_engine sitting next to this test (the experiment clone during
development, live ~/.claude/hooks after promotion) — never a hard-coded live path.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_resume_skip_force.py
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _factcheck_engine as eng  # noqa: E402


def _mock_resolver(proj, topic):
    state = {"topic_slug": proj, "project_slug": topic}
    return lambda _sid: (proj, topic, state)


class _CountingChecker:
    """Injectable _checker_fn that records the round_num of every dispatch."""

    def __init__(self, verdict="PASS"):
        self.rounds = []
        self.verdict = verdict

    def __call__(self, draft_path, idx, model, round_num, prior):
        self.rounds.append(round_num)
        return self.verdict

    @property
    def dispatches(self):
        return len(self.rounds)


class ResumeSkipForceTests(unittest.TestCase):
    PROJ = "TestRSF"
    TOPIC = "resume-skip"
    SID = "test-rsf"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "validation"
        self.draft = Path(self.tmpdir) / "thing.md"
        self.draft.write_text("---\ntitle: t\n---\n# Body\n")
        self.resolver = _mock_resolver(self.PROJ, self.TOPIC)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _topic_dir(self, kind="workflow"):
        # workflow kind is flat (state_dir/proj/topic); other kinds nest under /kind.
        if kind == "workflow":
            d = self.state_dir / self.PROJ / self.TOPIC
        else:
            d = self.state_dir / self.PROJ / self.TOPIC / kind
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _seed_markers(self, n, kind="workflow"):
        d = self._topic_dir(kind)
        for i in range(1, n + 1):
            (d / f"R{i}.md").write_text(f"round {i}\n")
        return d

    def _run(self, checker, force=False, max_rounds=3, kind="workflow"):
        return eng.factcheck_run(
            self.state_dir, str(self.draft), kind, self.SID,
            debounce_seconds=0,
            _checker_fn=checker,
            _proj_topic_resolver=self.resolver,
            max_rounds=max_rounds,
            force=force,
        )

    # -- C2: no-force, maxed-out → honest NOOP, 0 dispatches, no new marker --
    def test_noforce_maxed_out_returns_noop_zero_dispatch(self):
        d = self._seed_markers(3, kind="workflow")  # existing == max_rounds
        checker = _CountingChecker("PASS")
        result = self._run(checker, force=False, max_rounds=3)
        self.assertEqual(result["status"], "NOOP")
        self.assertEqual(result["reason"], "already_at_max_rounds")
        self.assertEqual(checker.dispatches, 0, "no checker should be dispatched on a no-op")
        self.assertFalse((d / "R4.md").exists(), "no new marker on a no-op")
        for i in (1, 2, 3):
            self.assertTrue((d / f"R{i}.md").exists(), "prior markers must remain intact")
        self.assertIn("--force", result["message"])

    def test_noforce_over_max_returns_noop(self):
        # stale over-count (existing > max_rounds) → still an honest NOOP
        self._seed_markers(5, kind="workflow")
        checker = _CountingChecker("PASS")
        result = self._run(checker, force=False, max_rounds=3)
        self.assertEqual(result["status"], "NOOP")
        self.assertEqual(checker.dispatches, 0)

    # -- C1/C3/C4/C5: force re-verifies from round 1 --
    def test_force_maxed_out_reverifies_from_round_1(self):
        d = self._seed_markers(3, kind="workflow")
        checker = _CountingChecker("PASS")
        result = self._run(checker, force=True, max_rounds=3)
        self.assertEqual(result["status"], "PASS")
        self.assertGreater(checker.dispatches, 0, "force must dispatch checkers")
        self.assertEqual(min(checker.rounds), 1, "force must re-verify from round 1")
        self.assertTrue((d / "R1.md").exists(), "round 1 marker rewritten")

    # -- C6: existing < max_rounds still resumes the remaining round(s) --
    def test_resume_when_below_max(self):
        self._seed_markers(2, kind="workflow")  # start_round = 3
        checker = _CountingChecker("PASS")
        result = self._run(checker, force=False, max_rounds=3)
        self.assertEqual(result["status"], "PASS")
        self.assertGreater(checker.dispatches, 0, "one remaining round should run")
        self.assertEqual(min(checker.rounds), 3, "resumes at round 3, not 1")

    def test_fresh_run_from_round_1(self):
        checker = _CountingChecker("PASS")
        result = self._run(checker, force=False, max_rounds=3)  # no markers
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(min(checker.rounds), 1)

    # -- C7: a genuine no-consensus still ESCALATEs (guard never masks a real failure) --
    def test_genuine_escalate_still_fires(self):
        checker = _CountingChecker("DISCREPANCY: real content problem")
        result = self._run(checker, force=False, max_rounds=3)  # no markers → runs all 3
        self.assertEqual(result["status"], "ESCALATE")
        self.assertEqual(checker.dispatches, 9, "3 rounds x 3 checkers all dispatched")

    # -- C9: the guard is kind-agnostic --
    def test_guard_kind_agnostic_plan(self):
        self._seed_markers(2, kind="plan")  # existing == max_rounds (2)
        checker = _CountingChecker("PASS")
        result = self._run(checker, force=False, max_rounds=2, kind="plan")
        self.assertEqual(result["status"], "NOOP")
        self.assertEqual(checker.dispatches, 0)


if __name__ == "__main__":
    unittest.main()

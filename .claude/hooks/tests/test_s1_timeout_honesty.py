#!/usr/bin/env python3
"""Slice S1 — timeout-honesty walking skeleton (research-fc-checker-timeout).

Plan: ~/.claude/plans/greedy-sniffing-kurzweil.md (Mode A, S1).
Spine: Thoughts/research-fc-checker-timeout_THOUGHT.md + _DESIGN.md (Alt 1).

Covers:
  A4  _checker_timeout_budget — probe-calibrated, size-scaled, monotonic, bounded.
  A2  _invoke_checker_engine — a subprocess timeout returns an INCOMPLETE sentinel,
      never a DISCREPANCY, and the budget is the value passed to subprocess.run.
  A2  _run_factcheck_rounds — three-bucket classification + four-state verdict +
      marker/return parity for every terminal state.
  A3  run_style_check — the separate cheaper style pass is advisory and never raises.
  A2-gate-confirm — check-research-gate.sh blocks (exit 2) on an INCOMPLETE marker.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parents[1]


def _incomplete(dp, idx, m, rnd, prior):
    return f"{eng.INCOMPLETE_SENTINEL} checker exceeded 240s budget"


def _pass(dp, idx, m, rnd, prior):
    return "verdict: PASS\nclaims_checked: 3\n"


def _dirty(dp, idx, m, rnd, prior):
    return (
        "verdict: DISCREPANCY\nclaims_checked: 2\n"
        "discrepancies:\n  - claim: X\n    issue: wrong number\n    citation: f.py:1\n"
    )


def _crash(dp, idx, m, rnd, prior):
    # What _invoke_checker_engine now returns when the subprocess could not RUN
    # (non-zero exit). A2/A18: an INCOMPLETE sentinel, never a DISCREPANCY.
    return f"{eng.INCOMPLETE_SENTINEL} checker subprocess failed (exit 1)"


def _marker_verdict(topic_dir):
    """Return the verdict: field of the latest R<N>.md marker in topic_dir."""
    rounds = sorted(topic_dir.glob("R*.md"),
                    key=lambda p: int(re.search(r"R(\d+)", p.name).group(1)))
    assert rounds, "no R<N>.md marker written"
    fm = rounds[-1].read_text(encoding="utf-8")
    m = re.search(r"^verdict:\s*(\S+)\s*$", fm, re.MULTILINE)
    assert m, f"no verdict in {rounds[-1]}"
    return m.group(1), rounds


class BudgetTests(unittest.TestCase):
    def test_floor_at_zero_bytes(self):
        self.assertEqual(eng._checker_timeout_budget(0), eng._CHECKER_BUDGET_BASE_S)

    def test_monotonic_non_decreasing(self):
        prev = -1
        for kb in (0, 1, 10, 18, 40, 100, 1000, 100000):
            b = eng._checker_timeout_budget(kb * 1024)
            self.assertGreaterEqual(b, prev)
            prev = b

    def test_bounded_by_max(self):
        self.assertLessEqual(
            eng._checker_timeout_budget(10_000_000), eng._CHECKER_BUDGET_MAX_S
        )

    def test_returns_int(self):
        self.assertIsInstance(eng._checker_timeout_budget(18 * 1024), int)

    def test_budget_covers_reprobe_floor(self):
        # V1 re-probe round 2 (faithful — shipped tools Read/Glob/Grep, NO WebFetch):
        # real fixtures completed in 185.8 s (49.4 KB) and 183.0 s (23.8 KB),
        # size-independent. The budget must exceed that floor with margin so a completable
        # report is not spuriously INCOMPLETEd. (Round 1's ~470 s was WebFetch-inflated —
        # not the shipped config; when S5 adds WebFetch, BASE rises toward ~600.)
        self.assertGreater(eng._checker_timeout_budget(int(49.4 * 1024)), 185.8)
        self.assertGreater(eng._checker_timeout_budget(int(23.8 * 1024)), 183.0)
        self.assertGreaterEqual(eng._checker_timeout_budget(0), 400)  # floor + >2x margin

    def test_bad_input_defaults_to_floor(self):
        self.assertEqual(eng._checker_timeout_budget(None), eng._CHECKER_BUDGET_BASE_S)


class TimeoutSentinelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.draft = self.tmp / "r_RESEARCH.md"
        self.draft.write_text("x" * (18 * 1024), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_timeout_returns_incomplete_not_discrepancy(self):
        captured = {}

        def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None):
            captured["timeout"] = timeout
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

        with mock.patch.object(eng.subprocess, "run", side_effect=fake_run):
            out = eng._invoke_checker_engine(
                str(self.draft), 0, "sonnet", 1, None, "research",
                ("Read", "Glob", "Grep"), "",
            )
        self.assertTrue(out.upper().lstrip().startswith(eng.INCOMPLETE_SENTINEL.upper()))
        self.assertNotIn("DISCREPANCY", out.upper())
        # A4: the timeout passed to subprocess.run is the size-scaled budget, not 120.
        self.assertEqual(captured["timeout"],
                         eng._checker_timeout_budget(self.draft.stat().st_size))
        self.assertNotEqual(captured["timeout"], 120)


class CrashAndCliMissingSentinelTests(unittest.TestCase):
    """A2/A18: a checker that could not RUN (non-zero subprocess exit, or a missing
    `claude` CLI) returns an INCOMPLETE sentinel, never a DISCREPANCY — mirroring the
    timeout branch. Regression guard for the couldn't-run misclassification
    (research-fc-dispatch-incomplete: exit!=0 / FileNotFoundError were DISCREPANCY)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.draft = self.tmp / "r_RESEARCH.md"
        self.draft.write_text("x" * (18 * 1024), encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _invoke(self):
        return eng._invoke_checker_engine(
            str(self.draft), 0, "sonnet", 1, None, "research",
            ("Read", "Glob", "Grep"), "",
        )

    def test_nonzero_exit_returns_incomplete_not_discrepancy(self):
        # C1: a non-zero exit is could-not-run, not content-wrong.
        class _R:
            returncode = 3
            stdout = ""
        with mock.patch.object(eng.subprocess, "run", return_value=_R()):
            out = self._invoke()
        self.assertTrue(out.upper().lstrip().startswith(eng.INCOMPLETE_SENTINEL.upper()))
        self.assertNotIn("DISCREPANCY", out.upper())
        self.assertIn("exit 3", out)   # human-readable cause preserved for audit

    def test_cli_missing_returns_incomplete_not_discrepancy(self):
        # C2: a missing launcher is could-not-run, not content-wrong.
        with mock.patch.object(eng.subprocess, "run", side_effect=FileNotFoundError()):
            out = self._invoke()
        self.assertTrue(out.upper().lstrip().startswith(eng.INCOMPLETE_SENTINEL.upper()))
        self.assertNotIn("DISCREPANCY", out.upper())

    def test_crash_buckets_identically_to_timeout(self):
        # C3 parity: a crashed checker and a timed-out checker classify identically.
        class _R:
            returncode = 1
            stdout = ""
        with mock.patch.object(eng.subprocess, "run", return_value=_R()):
            crash_out = self._invoke()
        with mock.patch.object(eng.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=1)):
            timeout_out = self._invoke()
        self.assertEqual(eng._verdict_bucket(crash_out, "research"), "INCOMPLETE")
        self.assertEqual(eng._verdict_bucket(crash_out, "research"),
                         eng._verdict_bucket(timeout_out, "research"))


class VerdictMachineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.topic = self.tmp / "topic"
        self.topic.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"
        self.draft.write_text("# fixture\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, checker_fn, max_rounds=2):
        return eng._run_factcheck_rounds(
            self.draft, self.topic, 1, checker_fn,
            ["sonnet", "sonnet", "sonnet"], max_rounds, "research",
        )

    def test_all_pass(self):
        v = self._run(_pass)
        self.assertEqual(v["status"], "PASS")
        self.assertEqual(_marker_verdict(self.topic)[0], "PASS")

    def test_timeout_is_incomplete_not_dirty(self):
        v = self._run(_incomplete)
        self.assertEqual(v["status"], "INCOMPLETE")
        self.assertNotEqual(v["status"], "DIRTY")

    def test_incomplete_is_terminal_at_round_1(self):
        # A checker that can't finish does not spend further rounds (probe-fixed floor).
        v = self._run(_incomplete, max_rounds=3)
        self.assertEqual(v["rounds"], 1)
        self.assertEqual(len(list(self.topic.glob("R*.md"))), 1)

    def test_incomplete_never_returned_as_pass(self):
        v = self._run(_incomplete)
        self.assertNotEqual(v["status"], "PASS")

    def test_content_error_precedes_timeout(self):
        # Round with BOTH a content DISCREPANCY and a timeout → DIRTY (non-final),
        # never INCOMPLETE: a real error must not be hidden by a co-occurring timeout.
        def mixed(dp, idx, m, rnd, prior):
            return _dirty(dp, idx, m, rnd, prior) if idx == 0 else _incomplete(dp, idx, m, rnd, prior)
        self._run(mixed, max_rounds=2)
        # R1 (non-final) records DIRTY, proving content precedence over INCOMPLETE.
        r1 = (self.topic / "R1.md").read_text(encoding="utf-8")
        self.assertRegex(r1, r"(?m)^verdict:\s*DIRTY\s*$")

    def test_crash_is_incomplete_terminal_not_pass(self):
        # C4: an all-crashed round is terminal INCOMPLETE, never auto-PASS.
        v = self._run(_crash, max_rounds=3)
        self.assertEqual(v["status"], "INCOMPLETE")
        self.assertNotEqual(v["status"], "PASS")
        self.assertEqual(v["rounds"], 1)   # does not spend further rounds

    def test_content_error_precedes_crash(self):
        # C5: a real content DISCREPANCY + a crashed checker in the same round → DIRTY
        # (non-final), never masked by the co-occurring INCOMPLETE.
        def mixed(dp, idx, m, rnd, prior):
            return _dirty(dp, idx, m, rnd, prior) if idx == 0 else _crash(dp, idx, m, rnd, prior)
        self._run(mixed, max_rounds=2)
        r1 = (self.topic / "R1.md").read_text(encoding="utf-8")
        self.assertRegex(r1, r"(?m)^verdict:\s*DIRTY\s*$")

    def test_marker_return_parity_all_terminal_states(self):
        # PASS
        v = self._run(_pass)
        self.assertEqual(_marker_verdict(self.topic)[0], v["status"])
        # INCOMPLETE
        shutil.rmtree(self.topic); self.topic.mkdir()
        v = self._run(_incomplete)
        self.assertEqual(_marker_verdict(self.topic)[0], v["status"])
        # ESCALATE (content DISCREPANCY unresolved at the cap)
        shutil.rmtree(self.topic); self.topic.mkdir()
        v = self._run(_dirty, max_rounds=1)
        self.assertEqual(v["status"], "ESCALATE")
        self.assertEqual(_marker_verdict(self.topic)[0], "ESCALATE")

    def test_dirty_then_escalate_latest_marker_is_escalate(self):
        v = self._run(_dirty, max_rounds=2)
        self.assertEqual(v["status"], "ESCALATE")
        verdict, rounds = _marker_verdict(self.topic)
        self.assertEqual(verdict, "ESCALATE")           # latest marker == return
        self.assertEqual((self.topic / "R1.md").read_text().count("verdict: DIRTY"), 1)


class StylePassTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.draft = self.tmp / "r_RESEARCH.md"
        self.draft.write_text("# fixture\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_clean(self):
        r = eng.run_style_check(str(self.draft), checker_fn=lambda *a: "PASS")
        self.assertEqual(r["status"], "STYLE_CLEAN")

    def test_flags(self):
        r = eng.run_style_check(
            str(self.draft), checker_fn=lambda *a: "VERDICT: DISCREPANCY\ndelve used"
        )
        self.assertEqual(r["status"], "STYLE_FLAGS")

    def test_never_raises_on_checker_error(self):
        def boom(*a):
            raise RuntimeError("subprocess died")
        r = eng.run_style_check(str(self.draft), checker_fn=boom)
        self.assertEqual(r["status"], "STYLE_ERROR")

    def test_advisory_marker_written(self):
        eng.run_style_check(str(self.draft), checker_fn=lambda *a: "PASS")
        self.assertTrue(list(self.tmp.glob("*_style-check.md")))


class GateBlocksIncompleteTests(unittest.TestCase):
    """A2-gate-confirm: an INCOMPLETE latest marker blocks close (exit 2) with NO
    code change to check-research-gate.sh — pins the S1/S2 seam."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.sid = "s1s1s1s1-0000-0000-0000-000000000000"
        # hook: PROJ=topic_slug, TOPIC=active_project → research base is PROJ/TOPIC/research
        active = self.home / ".claude/state/pre_plan_gates"
        active.mkdir(parents=True)
        (active / "_active.json").write_text(json.dumps({
            self.sid: {"topic_slug": "proj", "active_project": "topic"}
        }), encoding="utf-8")
        self.rbase = self.home / ".claude/state/plan_validation/proj/topic/research"
        self.rbase.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _run_gate(self):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("CLAUDE_CODE_REMOTE", None)
        return subprocess.run(
            ["bash", str(HOOKS_DIR / "check-research-gate.sh")],
            input=json.dumps({"session_id": self.sid, "stop_hook_active": False}),
            capture_output=True, text=True, env=env,
        )

    def _write_marker(self, verdict):
        (self.rbase / "R1.md").write_text(
            f"---\nschema_version: 2\nkind: research\nverdict: {verdict}\n"
            f"rounds: 1\nchecked_at: 2026-07-08T00:00:00+00:00\n---\n\n# r\n",
            encoding="utf-8",
        )

    def test_incomplete_blocks_close(self):
        self._write_marker("INCOMPLETE")
        r = self._run_gate()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("non-PASS", r.stderr)

    def test_pass_does_not_block(self):
        self._write_marker("PASS")
        self.assertEqual(self._run_gate().returncode, 0)


if __name__ == "__main__":
    unittest.main()

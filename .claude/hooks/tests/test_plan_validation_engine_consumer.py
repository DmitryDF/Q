#!/usr/bin/env python3
"""Tests for plan-validation-engine-consumer (Mode-C plan, 2026-07-18).

Makes plan-mode validation a CONSUMER of the shared validation engine:
  S1 — code computes the verdict via the engine's aggregator + writes an R<N>.md
       receipt keyed to (gate, section); a producer-supplied verdict is impossible.
  S2 — check-plan-gates.sh reads the engine receipt for the Slice-I 0A/0B2/0C/0G
       markers (admit only converged PASS or a recorded operator override).
  S3 — per-section validator gates 0D/0E added to VALID_PLAN_STEP_GATES.
  S5 — plan validation CONSUMES the one shared engine aggregator (define-once):
       an engine convergence change is observed by plan validation with no plan-side edit.

Hooks are resolved RELATIVE to this test file so the suite runs unchanged on a
`claude-experiment` clone (dev) and on live `~/.claude` (post-promote). Each
subprocess gets an isolated HOME so receipt/state writes never touch live state.

Run: python3 <hooks>/tests/test_plan_validation_engine_consumer.py
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
PPG_PY = HOOKS / "pre_plan_gates.py"
GATE_SH = HOOKS / "check-plan-gates.sh"

sys.path.insert(0, str(HOOKS))


def run_ppg(*args, home, expect=None, stdin=None):
    """Run pre_plan_gates.py with an isolated HOME. Returns CompletedProcess."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    result = subprocess.run(
        ["python3", str(PPG_PY), *args],
        capture_output=True, text=True, input=stdin, env=env,
    )
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"pre_plan_gates.py {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _checkers(*verdicts):
    """Build the captured-checker JSON list the orchestrator would pass."""
    return json.dumps(
        [{"checker": i + 1, "model": "sonnet", "verdict": v}
         for i, v in enumerate(verdicts)]
    )


def _plan_file(home, body="# Plan\n\ncontent\n"):
    p = Path(home) / "plans" / "harness-slug.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


def _receipt_dir(home, plan_path, gate):
    h = hashlib.sha256(str(Path(plan_path).resolve()).encode()).hexdigest()[:12]
    return Path(home) / ".claude" / "state" / "plan_validation" / "sections" / h / gate


class S1VerdictCompute(unittest.TestCase):
    """S1: code computes PASS/DIRTY/ESCALATE + writes exactly one receipt."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.plan = _plan_file(self.home)

    def _step(self, gate, checkers_json, *extra, expect=0):
        return run_ppg(
            "factcheck-plan-step", gate, str(self.plan), *extra,
            home=self.home, expect=expect, stdin=checkers_json,
        )

    def test_all_pass_computes_pass_and_one_receipt(self):
        r = self._step("0A", _checkers("VERDICT: PASS", "VERDICT: PASS"))
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["checker_count"], 2)
        rd = _receipt_dir(self.home, self.plan, "0A")
        self.assertEqual(sorted(p.name for p in rd.glob("R*.md")), ["R1.md"])
        marker = (rd / "R1.md").read_text()
        self.assertTrue(marker.startswith("---"))
        self.assertIn("verdict: PASS", marker)
        self.assertIn("kind: plan", marker)
        self.assertIn("checker_count: 2", marker)

    def test_any_discrepancy_is_dirty_not_final(self):
        r = self._step("0B2", _checkers("VERDICT: PASS", "VERDICT: DISCREPANCY"),
                       "--round", "1", "--max-rounds", "3")
        self.assertEqual(json.loads(r.stdout)["verdict"], "DIRTY")

    def test_discrepancy_on_final_round_is_escalate(self):
        r = self._step("0B2", _checkers("VERDICT: DISCREPANCY"),
                       "--round", "3", "--max-rounds", "3")
        self.assertEqual(json.loads(r.stdout)["verdict"], "ESCALATE")

    def test_prose_discrepancy_word_does_not_flip_pass(self):
        # CoT-safe: only the final VERDICT token decides (plan kind).
        r = self._step("0C", _checkers("I see no discrepancy at all\nVERDICT: PASS"))
        self.assertEqual(json.loads(r.stdout)["verdict"], "PASS")

    def test_cross_axis_broken_reaches_verdict_via_token(self):
        # The checker folds cross-axis BROKEN into VERDICT: DISCREPANCY.
        r = self._step("0C", _checkers(
            "axis_1: PASS\naxis_2: PASS\ncross_axis_coherence: BROKEN\nVERDICT: DISCREPANCY"))
        self.assertEqual(json.loads(r.stdout)["verdict"], "DIRTY")

    def test_empty_checkers_refused_no_fake_pass(self):
        r = self._step("0A", "", expect=1)
        self.assertIn("no captured checker outputs", r.stderr)
        # And NO receipt was written (no honor-system PASS).
        self.assertFalse(_receipt_dir(self.home, self.plan, "0A").exists())

    def test_checkers_from_file(self):
        cf = Path(self.home) / "checkers.json"
        cf.write_text(_checkers("VERDICT: PASS"))
        r = run_ppg("factcheck-plan-step", "0A", str(self.plan),
                    "--checkers-json", str(cf), home=self.home, expect=0)
        self.assertEqual(json.loads(r.stdout)["verdict"], "PASS")

    def test_receipt_keyed_per_gate(self):
        self._step("0A", _checkers("VERDICT: PASS"))
        self._step("0B2", _checkers("VERDICT: PASS"))
        self.assertTrue((_receipt_dir(self.home, self.plan, "0A") / "R1.md").exists())
        self.assertTrue((_receipt_dir(self.home, self.plan, "0B2") / "R1.md").exists())
        self.assertNotEqual(_receipt_dir(self.home, self.plan, "0A"),
                            _receipt_dir(self.home, self.plan, "0B2"))

    def test_coherency_writes_0g_receipt_and_audit_row(self):
        r = run_ppg("factcheck-plan-coherency", "sid-1", str(self.plan),
                    "--round", "1", "--max-rounds", "3",
                    home=self.home, expect=0, stdin=_checkers(
                        "VERDICT: PASS", "VERDICT: PASS", "VERDICT: PASS", "VERDICT: PASS"))
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual(out["gate_id"], "0G")
        self.assertTrue((_receipt_dir(self.home, self.plan, "0G") / "R1.md").exists())
        jsonl = Path(self.home) / ".claude" / "state" / "plan_validation" / "_verifications.jsonl"
        self.assertTrue(jsonl.exists())
        row = json.loads(jsonl.read_text().strip().splitlines()[-1])
        self.assertEqual(row["verdict"], "PASS")
        self.assertEqual(row["kind"], "coherency")

    def test_receipt_dir_verb_agrees_with_writer(self):
        r = run_ppg("plan-receipt-dir", str(self.plan), "0A", "--round", "1",
                    home=self.home, expect=0)
        printed = Path(r.stdout.strip())
        self._step("0A", _checkers("VERDICT: PASS"))
        self.assertTrue(printed.exists(), f"{printed} should have been written")
        self.assertEqual(printed, _receipt_dir(self.home, self.plan, "0A") / "R1.md")

    def test_bad_gate_rejected(self):
        r = self._step("0Z", _checkers("VERDICT: PASS"), expect=1)
        self.assertIn("gate_id must be one of", r.stderr)


class S3SectionValidator(unittest.TestCase):
    """S3: the blind-spot sections 0D (Guiding Policy) + 0E (Coherent Actions) can be
    validated in isolation by the same engine — 0D/0E accepted, receipt written."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.plan = _plan_file(self.home)

    def test_0d_dispatch_accepted_and_writes_receipt(self):
        r = run_ppg("factcheck-plan-step", "0D", str(self.plan),
                    home=self.home, expect=0, stdin=_checkers("VERDICT: PASS"))
        self.assertEqual(json.loads(r.stdout)["verdict"], "PASS")
        self.assertTrue((_receipt_dir(self.home, self.plan, "0D") / "R1.md").exists())

    def test_0e_dispatch_accepted(self):
        r = run_ppg("factcheck-plan-step", "0E", str(self.plan),
                    home=self.home, expect=0, stdin=_checkers("VERDICT: DISCREPANCY"))
        self.assertEqual(json.loads(r.stdout)["verdict"], "DIRTY")


class S5DefineOncePropagate(unittest.TestCase):
    """S5: plan validation consumes the ONE shared engine aggregator, so a change to
    engine convergence reaches plan validation with no plan-side copy to edit."""

    def test_plan_step_routes_through_shared_aggregator(self):
        import _factcheck_engine as fce
        import pre_plan_gates as ppg

        # pre_plan_gates must call the engine's aggregator — not a private copy.
        home = tempfile.mkdtemp()
        plan = _plan_file(home)
        orig = fce.aggregate_round_verdict
        calls = {}

        def spy(checker_verdicts, *, is_final, kind):
            calls["kind"] = kind
            calls["n"] = len(checker_verdicts)
            return "SENTINEL_VERDICT", None

        fce.aggregate_round_verdict = spy
        try:
            out = ppg.factcheck_plan_step(
                "0C", str(plan),
                [{"checker": 1, "model": "sonnet", "verdict": "VERDICT: PASS"}],
                round_num=1, max_rounds=3,
            )
        finally:
            fce.aggregate_round_verdict = orig
        # The verdict pre_plan_gates returned is EXACTLY what the shared engine
        # aggregator produced (proving consumption, not a fork), and it ran the
        # plan kind through that shared function.
        self.assertEqual(out["verdict"], "SENTINEL_VERDICT")
        self.assertEqual(calls.get("kind"), "plan")
        self.assertEqual(calls.get("n"), 1)


class S2OperatorOverride(unittest.TestCase):
    """S2: the recorded operator override — explicit + reason-required, never silent."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.plan = _plan_file(self.home)

    def test_override_writes_marker_into_gate_receipt_dir(self):
        r = run_ppg("plan-override", "0G", str(self.plan),
                    "--reason", "operator accepts residual tension",
                    "--session", "sid-9", home=self.home, expect=0)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "override_recorded")
        marker = _receipt_dir(self.home, self.plan, "0G") / "OVERRIDE"
        self.assertTrue(marker.exists())
        self.assertEqual(json.loads(marker.read_text())["reason"],
                         "operator accepts residual tension")

    def test_override_requires_nonempty_reason(self):
        r = run_ppg("plan-override", "0G", str(self.plan), "--reason", "   ",
                    home=self.home, expect=1)
        self.assertIn("non-empty --reason", r.stderr)
        self.assertFalse((_receipt_dir(self.home, self.plan, "0G") / "OVERRIDE").exists())

    def test_override_bad_gate_rejected(self):
        r = run_ppg("plan-override", "0Z", str(self.plan), "--reason", "x",
                    home=self.home, expect=1)
        self.assertIn("gate_id must be one of", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)

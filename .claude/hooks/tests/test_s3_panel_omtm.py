#!/usr/bin/env python3
"""Slice S3 — panel independence + OMTM CLI + isolation baseline
(research-fc-checker-timeout).

Plan: Thoughts/research-fc-checker-timeout_S3_PLAN.md (Mode A, S3).
Spine: Thoughts/research-fc-checker-timeout_THOUGHT.md + _DESIGN.md (Alt 1).

Covers:
  A5a  CHECKER_MODELS is the cross-family research panel; the research dispatch
       resolves it and other kinds keep 3 Sonnet (regression).
  A5b  _verdict_bucket — research reads the explicit final VERDICT: token (CoT-safe);
       legacy kinds keep the whole-output substring behavior byte-for-byte; INCOMPLETE
       precedence preserved. The research prompt requires chain-of-thought.
  A16  compute_omtm_rate — genuine-first-round-PASS definition (round1 ∧ v3 ∧ all-axes
       ∧ not INCOMPLETE/BYPASSED), v2 excluded as a separate epoch, rolling window,
       empty trail, non-default cycles, and READ-ONLY (mutates no file).
"""
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402


# ---------------------------------------------------------------------------
# A5a — cross-family research panel, scoped to the research kind
# ---------------------------------------------------------------------------
class TestA5aPanel(unittest.TestCase):
    def test_checker_models_is_cross_family(self):
        # ≥2 distinct model families, and the concrete panel is Sonnet/Opus/Haiku.
        self.assertEqual(eng.CHECKER_MODELS, ["sonnet", "opus", "haiku"])
        self.assertGreaterEqual(len(set(eng.CHECKER_MODELS)), 2)

    def test_research_dispatch_uses_cross_family_panel(self):
        # The `factcheck-research` dispatch (pre_plan_gates.py) imports CHECKER_MODELS
        # and passes it as `models=`. Assert the wiring at the source level.
        ppg = (Path(__file__).resolve().parents[1] / "pre_plan_gates.py").read_text(encoding="utf-8")
        self.assertIn("from _factcheck_engine import factcheck_run, run_style_check, CHECKER_MODELS", ppg)
        self.assertRegex(ppg, r"models=CHECKER_MODELS,\s")

    def test_other_kinds_keep_three_sonnet(self):
        # Regression fence: the other fixed pipelines keep their own 3-Sonnet lists.
        ppg = (Path(__file__).resolve().parents[1] / "pre_plan_gates.py").read_text(encoding="utf-8")
        # plan + thought dispatches still pass an explicit 3-Sonnet literal.
        self.assertIn('models=["sonnet", "sonnet", "sonnet"]', ppg)


# ---------------------------------------------------------------------------
# A5b — CoT-safe verdict classification + prompt
# ---------------------------------------------------------------------------
class TestA5bVerdictBucket(unittest.TestCase):
    def test_research_cot_mentioning_discrepancy_but_final_pass_is_pass(self):
        # THE load-bearing case: CoT reasoning contains the word "discrepancy" but the
        # final verdict is PASS → must classify PASS, never DIRTY.
        raw = ("Step 1: I checked claim A against the source — supported.\n"
               "Step 2: no discrepancy found in claim B either.\n"
               "VERDICT: PASS")
        self.assertEqual(eng._verdict_bucket(raw, "research"), "PASS")

    def test_research_final_discrepancy_token_is_discrepancy(self):
        raw = ("Reasoning: claim C cites a number that the source contradicts.\n"
               "VERDICT: DISCREPANCY — claim C overstates the figure")
        self.assertEqual(eng._verdict_bucket(raw, "research"), "DISCREPANCY")

    def test_research_timeout_sentinel_is_incomplete(self):
        raw = f"{eng.INCOMPLETE_SENTINEL} checker exceeded 600s budget"
        self.assertEqual(eng._verdict_bucket(raw, "research"), "INCOMPLETE")

    def test_research_last_token_wins(self):
        # If two VERDICT: lines appear (unusual), the FINAL one decides.
        raw = "VERDICT: DISCREPANCY (draft)\n...revised...\nVERDICT: PASS"
        self.assertEqual(eng._verdict_bucket(raw, "research"), "PASS")

    def test_research_missing_token_falls_back_conservatively(self):
        # No final VERDICT: token — a bare DISCREPANCY word is treated as a content error.
        self.assertEqual(eng._verdict_bucket("DISCREPANCY: subprocess failed", "research"),
                         "DISCREPANCY")
        self.assertEqual(eng._verdict_bucket("all good", "research"), "PASS")

    def test_legacy_kinds_unchanged(self):
        # Byte-for-byte parity with the pre-S3 substring behavior for every other kind.
        for kind in ("plan", "thought", "workflow", "kl_extraction", "coverage_check"):
            self.assertEqual(eng._verdict_bucket("VERDICT: DISCREPANCY\nx", kind), "DISCREPANCY")
            self.assertEqual(eng._verdict_bucket("PASS", kind), "PASS")
            self.assertEqual(eng._verdict_bucket(f"{eng.INCOMPLETE_SENTINEL} x", kind), "INCOMPLETE")
            # A legacy checker never emits CoT, but if the word appears it stays DISCREPANCY
            # (unchanged whole-output substring semantics).
            self.assertEqual(eng._verdict_bucket("no discrepancy here", kind), "DISCREPANCY")

    def test_research_prompt_requires_chain_of_thought(self):
        prompt = eng._build_checker_input("research", 0, "/tmp/x_RESEARCH.md", 1, None, "")
        self.assertIn("think step by step", prompt.lower())
        self.assertIn("VERDICT:", prompt)

    def test_non_research_prompt_has_no_cot(self):
        prompt = eng._build_checker_input("plan", 0, "/tmp/x_PLAN.md", 1, None, "")
        self.assertNotIn("think step by step", prompt.lower())


# ---------------------------------------------------------------------------
# A16 — OMTM reader
# ---------------------------------------------------------------------------
def _write_marker(cycle_dir, n, *, schema_version, verdict, checked_at, rounds=None):
    cycle_dir.mkdir(parents=True, exist_ok=True)
    rounds = n if rounds is None else rounds
    (cycle_dir / f"R{n}.md").write_text(
        f"---\nschema_version: {schema_version}\nrounds: {rounds}\nkind: research\n"
        f"checker_count: 3\nverdict: {verdict}\nchecked_at: {checked_at}\n---\n\n"
        f"# Round {n}\n{verdict}\n",
        encoding="utf-8",
    )


class TestA16OmtmRate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        self.recent = (self.now - timedelta(days=2)).isoformat()
        self.old = (self.now - timedelta(days=60)).isoformat()

    def tearDown(self):
        self._tmp.cleanup()

    def _research(self, proj, topic):
        return self.base / proj / topic / "research"

    def test_v3_r1_pass_is_genuine(self):
        _write_marker(self._research("p", "t"), 1, schema_version=3,
                      verdict="PASS", checked_at=self.recent)
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["genuine_pass"], 1)
        self.assertEqual(r["eligible_first_round_markers"], 1)
        self.assertEqual(r["rate"], 1.0)

    def test_v3_dirty_incomplete_bypassed_in_denominator_not_numerator(self):
        _write_marker(self._research("p", "pass"), 1, schema_version=3, verdict="PASS", checked_at=self.recent)
        _write_marker(self._research("p", "dirty"), 1, schema_version=3, verdict="DIRTY", checked_at=self.recent)
        _write_marker(self._research("p", "inc"), 1, schema_version=3, verdict="INCOMPLETE", checked_at=self.recent)
        _write_marker(self._research("p", "byp"), 1, schema_version=3, verdict="BYPASSED", checked_at=self.recent)
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["genuine_pass"], 1)
        self.assertEqual(r["eligible_first_round_markers"], 4)
        self.assertEqual(r["rate"], 0.25)
        self.assertEqual(r["breakdown"]["dirty"], 1)
        self.assertEqual(r["breakdown"]["incomplete"], 1)
        self.assertEqual(r["breakdown"]["bypassed"], 1)

    def test_v2_marker_excluded_from_rate(self):
        _write_marker(self._research("p", "v2"), 1, schema_version=2, verdict="PASS", checked_at=self.recent)
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["eligible_first_round_markers"], 0)
        self.assertEqual(r["excluded"]["non_v3"], 1)
        self.assertIsNone(r["rate"])

    def test_out_of_window_excluded(self):
        _write_marker(self._research("p", "old"), 1, schema_version=3, verdict="PASS", checked_at=self.old)
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["eligible_first_round_markers"], 0)
        self.assertEqual(r["excluded"]["out_of_window"], 1)

    def test_first_round_is_r1_not_latest(self):
        # A topic that went R1=DIRTY then R2=PASS is NOT a genuine first-round pass.
        rd = self._research("p", "converged")
        _write_marker(rd, 1, schema_version=3, verdict="DIRTY", checked_at=self.recent)
        _write_marker(rd, 2, schema_version=3, verdict="PASS", checked_at=self.recent)
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["genuine_pass"], 0)
        self.assertEqual(r["breakdown"]["dirty"], 1)
        self.assertEqual(r["eligible_first_round_markers"], 1)

    def test_non_default_cycles_walked(self):
        rd = self._research("p", "multi")
        _write_marker(rd, 1, schema_version=3, verdict="PASS", checked_at=self.recent)          # default cycle
        _write_marker(rd / "de", 1, schema_version=3, verdict="DIRTY", checked_at=self.recent)  # named cycle
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["eligible_first_round_markers"], 2)
        self.assertEqual(r["genuine_pass"], 1)

    def test_empty_trail(self):
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["eligible_first_round_markers"], 0)
        self.assertIsNone(r["rate"])
        self.assertEqual(r["breakdown"]["genuine_pass"], 0)

    def test_reader_is_read_only(self):
        # The reader must not create, mutate, or delete any file (A16 / C5 read-only).
        rd = self._research("p", "ro")
        _write_marker(rd, 1, schema_version=3, verdict="PASS", checked_at=self.recent)

        def snapshot():
            return {p: (p.stat().st_mtime_ns, p.read_bytes())
                    for p in sorted(self.base.rglob("*")) if p.is_file()}

        before = snapshot()
        eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        after = snapshot()
        self.assertEqual(before.keys(), after.keys(), "reader created or deleted a file")
        self.assertEqual(before, after, "reader mutated a marker file")

    def test_present_axes_forward_compatible(self):
        # Today only the `verdict` axis exists; the reader reports it so a later slice
        # can extend _OMTM_PRESENT_AXES without rewriting the reader.
        r = eng.compute_omtm_rate(self.base, window_days=30, now=self.now)
        self.assertEqual(r["present_axes"], ["verdict"])


if __name__ == "__main__":
    unittest.main()

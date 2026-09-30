#!/usr/bin/env python3
"""Tests for the /double-check interactive pre-check code gates (Slice S4).

Plan: Thoughts/double-check-validation-targeting_S4_PLAN.md (Coherent Actions A1/A2).

The skill-orchestration steps (A3 the pre-check sequence + (a)/(b) gate, A4 the
source-conflict assist) are Layer-3 skill text with no pytest harness — verified
structurally + end-to-end at S9 (recorded in the plan's Gate 2). This file covers
the CODE enforcement gates S4 ships:

  C1  pre_check_layer marker population: assemble_audit_marker populates the S1
      PreCheckLayer from dispatch data and re-validates; absent when not provided
      (the locked --auto/skipped signal).
  C2  self-assessment floor + fail-closed: validate_self_assessment passes a
      complete target and fails (ok=False) naming each unmet condition — the
      code gate that makes a hollow PASS structurally impossible (producer never
      grades its own floor).
"""
import sys
import unittest
from pathlib import Path

import yaml  # YAML round-trip of the assembled marker (test-only dependency)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402


def _complete_target(**over):
    t = {
        "artifact_type": "research",
        "axes": ["groundedness", "coverage"],
        "claims": ["X holds per foo_RESEARCH.md"],
        "named_source_artifacts": ["local-file:foo_RESEARCH.md"],
    }
    t.update(over)
    return t


# --------------------------------------------------------------------------- #
# C2 — code self-assessment floor + fail-closed.
# --------------------------------------------------------------------------- #

class TestC2Floor(unittest.TestCase):
    def test_complete_target_passes(self):
        r = eng.validate_self_assessment(_complete_target())
        self.assertTrue(r["ok"], r["failures"])
        self.assertEqual(r["failures"], [])

    def test_missing_groundedness_fails(self):
        r = eng.validate_self_assessment(_complete_target(axes=["coverage", "source-quality"]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("groundedness" in f for f in r["failures"]))

    def test_fewer_than_two_axes_fails(self):
        r = eng.validate_self_assessment(_complete_target(axes=["groundedness"]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("fewer than 2" in f for f in r["failures"]))

    def test_zero_claims_fails(self):
        r = eng.validate_self_assessment(_complete_target(claims=[]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("claim" in f for f in r["failures"]))

    def test_zero_sources_fails(self):
        r = eng.validate_self_assessment(_complete_target(named_source_artifacts=[]))
        self.assertFalse(r["ok"])
        self.assertTrue(any("source" in f for f in r["failures"]))

    def test_axis_outside_registry_set_fails(self):
        # 'source-quality' is not in the 'plan' type's registry axis set.
        r = eng.validate_self_assessment(
            _complete_target(artifact_type="plan", axes=["groundedness", "source-quality"])
        )
        self.assertFalse(r["ok"])
        self.assertTrue(any("registry axis set" in f for f in r["failures"]))

    def test_unknown_artifact_type_fails(self):
        r = eng.validate_self_assessment(_complete_target(artifact_type="not-a-type"))
        self.assertFalse(r["ok"])
        self.assertTrue(any("registered types" in f for f in r["failures"]))

    def test_non_dict_target_fails_closed(self):
        self.assertFalse(eng.validate_self_assessment(None)["ok"])
        self.assertFalse(eng.validate_self_assessment("the code base")["ok"])

    def test_multiple_failures_are_all_named(self):
        r = eng.validate_self_assessment({"artifact_type": "research", "axes": [], "claims": [], "named_source_artifacts": []})
        self.assertFalse(r["ok"])
        # groundedness-missing + <2 axes + 0 claims + 0 sources => >=4 failures
        self.assertGreaterEqual(len(r["failures"]), 4)

    def test_accepts_axis_dicts_not_just_names(self):
        # the pre-check may return axes as dicts carrying a name
        r = eng.validate_self_assessment(
            _complete_target(axes=[{"name": "groundedness"}, {"name": "coverage"}])
        )
        self.assertTrue(r["ok"], r["failures"])


# --------------------------------------------------------------------------- #
# C1 — pre_check_layer marker population (no schema reshape; absence-as-signal).
# --------------------------------------------------------------------------- #

_PCL = {
    "model": "sonnet",
    "upgraded_target": "research vs foo_RESEARCH.md on groundedness+coverage",
    "self_assessment_passed": True,
    "user_choice": "(a) Accept",
}


def _assemble(dispatch_data):
    return eng.assemble_audit_marker(
        artifact_type="research",
        source_path="/tmp/foo_RESEARCH.md",
        caller="test-session",
        topic="double-check-validation-targeting",
        verdict="PASS",
        rounds_this_dispatch=1,
        allocation_profile="sonnet,sonnet,sonnet",
        round_markers=[],
        dispatch_data=dispatch_data,
        created_at="2026-06-25T12:00:00.000001+00:00",
    )


class TestC1PreCheckLayer(unittest.TestCase):
    def test_pre_check_layer_populated_from_dispatch_data(self):
        m = _assemble({"models": ["sonnet"], "pre_check_layer": _PCL})
        self.assertIsNotNone(m.pre_check_layer)
        self.assertEqual(m.pre_check_layer.model, "sonnet")
        self.assertEqual(m.pre_check_layer.upgraded_target, _PCL["upgraded_target"])
        self.assertTrue(m.pre_check_layer.self_assessment_passed)
        self.assertEqual(m.pre_check_layer.user_choice, "(a) Accept")

    def test_populated_marker_revalidates_through_yaml(self):
        m = _assemble({"models": ["sonnet"], "pre_check_layer": _PCL})
        loaded = yaml.safe_load(yaml.safe_dump(m.model_dump()))
        eng.AuditMarker(**loaded)  # must not raise (no schema reshape)

    def test_accepts_prechecklayer_instance(self):
        pcl = eng.PreCheckLayer(**_PCL)
        m = _assemble({"models": ["sonnet"], "pre_check_layer": pcl})
        self.assertEqual(m.pre_check_layer.upgraded_target, _PCL["upgraded_target"])

    def test_absent_when_not_provided_is_the_signal(self):
        m = _assemble({"models": ["sonnet"]})
        self.assertIsNone(m.pre_check_layer)
        # absence-as-signal must survive serialization (dropped from the YAML body)
        body = yaml.safe_dump(
            {k: v for k, v in m.model_dump().items() if v is not None}
        )
        self.assertNotIn("pre_check_layer", body)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for the /double-check checker-dispatch primitives (Slice S3).

Plan: Thoughts/double-check-validation-targeting_S3_PLAN.md (Coherent Actions A1-A4).

Coverage:
  C1  allocation validator accepts the locked {1,3,4} shape (1 round-1; 3+advisory
      round-2; cap 4 single / 3 two-model) and rejects N=2 + over-cap before dispatch.
  C2  angle injection: for any (type, axis) the instruction interpolates that axis's
      AxisSpec verbatim, the row's angle == the axis (one axis = one checker = one
      row), and an unknown (type, axis) returns a no-match (not an exception).
  C3  marker population: a marker assembled from real dispatch data carries non-empty
      per-angle rows + per-claim verdicts with named proof sources + non-empty cost +
      populated overlap/rounds, and re-validates (YAML round-trip); the no-dispatch
      path still yields a valid default-populated marker.
  C4  ESCALATE 3-option handler records escalate_choice + drives the locked control
      outcome per option; an unknown option is rejected; the choice flows to the marker.
"""
import sys
import unittest
from pathlib import Path

import yaml  # YAML round-trip of the assembled marker (test-only dependency)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402
from pydantic import ValidationError  # noqa: E402

AXISSPEC_FIELDS = ("operational_definition", "pass_criteria", "failure_modes")


# --------------------------------------------------------------------------- #
# C1 — allocation validator ({1,3,4}, caps, N=2 disallowed, advisory non-binding).
# --------------------------------------------------------------------------- #

class TestC1Allocation(unittest.TestCase):
    def test_accepts_round1_single_specific(self):
        out = eng.validate_allocation({"sonnet": 1})
        self.assertEqual(out["total_binding"], 1)

    def test_accepts_round2_three_specific_plus_advisory(self):
        out = eng.validate_allocation({"sonnet": 3}, advisory={"opus": 1})
        self.assertEqual(out["total_binding"], 3)
        self.assertEqual(out["advisory"], {"opus": 1})

    def test_accepts_single_model_cap_four(self):
        self.assertEqual(eng.validate_allocation({"sonnet": 4})["total_binding"], 4)

    def test_accepts_two_model_three_each(self):
        out = eng.validate_allocation({"sonnet": 3, "opus": 3})
        self.assertEqual(out["total_binding"], 6)

    def test_rejects_n2_naming_the_rule(self):
        with self.assertRaises(eng.AllocationError) as cm:
            eng.validate_allocation({"sonnet": 2})
        self.assertIn("N=2", str(cm.exception))

    def test_rejects_single_model_over_cap(self):
        with self.assertRaises(eng.AllocationError):
            eng.validate_allocation({"sonnet": 5})  # 5 not in {1,3,4}

    def test_rejects_two_model_over_cap(self):
        # 4 is allowed single-model, but exceeds the two-model cap of 3.
        with self.assertRaises(eng.AllocationError) as cm:
            eng.validate_allocation({"sonnet": 4, "opus": 1})
        self.assertIn("cap", str(cm.exception))

    def test_rejects_more_than_two_binding_models(self):
        with self.assertRaises(eng.AllocationError):
            eng.validate_allocation({"sonnet": 1, "opus": 1, "haiku": 1})

    def test_rejects_empty_and_nonpositive(self):
        with self.assertRaises(eng.AllocationError):
            eng.validate_allocation({})
        with self.assertRaises(eng.AllocationError):
            eng.validate_allocation({"sonnet": 0})

    def test_advisory_is_not_capped_or_restricted(self):
        # advisory checkers are non-binding: not subject to {1,3,4} or the caps.
        out = eng.validate_allocation({"sonnet": 1}, advisory={"opus": 2})
        self.assertEqual(out["advisory"], {"opus": 2})


# --------------------------------------------------------------------------- #
# C2 — angle injection (verbatim AxisSpec; one axis = one checker = one row).
# --------------------------------------------------------------------------- #

class TestC2AngleInjection(unittest.TestCase):
    def test_every_type_axis_interpolates_axisspec_verbatim(self):
        for atype, spec in eng.DC_AXIS_REGISTRY.items():
            for axis in spec["axes"]:
                prompt = eng.build_axis_checker_prompt(atype, axis["name"])
                self.assertIsNotNone(prompt, f"{atype}/{axis['name']}")
                self.assertIn(axis["name"], prompt)
                for field in AXISSPEC_FIELDS:
                    self.assertIn(axis[field], prompt,
                                  f"{atype}/{axis['name']} missing verbatim {field}")

    def test_dc_axis_spec_returns_matching_spec(self):
        spec = eng.dc_axis_spec("research", "source-quality")
        self.assertIsNotNone(spec)
        self.assertEqual(spec["name"], "source-quality")

    def test_one_axis_one_row_angle_stamp(self):
        # The angle a checker is built for is exactly the angle stamped on its row.
        axis = "coverage"
        prompt = eng.build_axis_checker_prompt("plan", axis)
        self.assertIsNotNone(prompt)
        row = eng.CheckerRow(round=1, model="sonnet", angle=axis,
                             claims_examined=2, discrepancies=0)
        self.assertEqual(row.angle, axis)

    def test_unknown_type_or_axis_returns_no_match(self):
        self.assertIsNone(eng.build_axis_checker_prompt("not-a-type", "groundedness"))
        self.assertIsNone(eng.build_axis_checker_prompt("research", "not-an-axis"))
        self.assertIsNone(eng.dc_axis_spec("not-a-type", "groundedness"))
        self.assertIsNone(eng.dc_axis_spec("research", "not-an-axis"))


# --------------------------------------------------------------------------- #
# C3 — marker population from real dispatch data (no schema reshape).
# --------------------------------------------------------------------------- #

_DISPATCH = {
    "checker_rows": [
        {"round": 1, "model": "sonnet", "angle": "groundedness",
         "claims_examined": 5, "discrepancies": 1,
         "claim_verdicts": [
             {"claim": "X holds", "status": "Supported",
              "proof_source": "local-file:foo_RESEARCH.md:12"},
             {"claim": "Y holds", "status": "Not-Supported",
              "proof_source": "not found in named sources"},
         ]},
        {"round": 1, "model": "sonnet", "angle": "coverage",
         "claims_examined": 3, "discrepancies": 0, "claim_verdicts": []},
    ],
    "cost": {
        "upgrade": {"tokens": 100, "time": 1.5},
        "self_assessment": {"tokens": 50, "time": 0.5},
        "checkers": {"tokens": 400, "time": 3.0},
    },
    "axes_overlap_signal": "low",
    "escalate_choice": None,
}


def _assemble(dispatch_data, **over):
    kw = dict(
        artifact_type="research",
        source_path="/tmp/foo_RESEARCH.md",
        caller="test-session",
        topic="double-check-validation-targeting",
        verdict="DIRTY",
        rounds_this_dispatch=1,
        allocation_profile="sonnet,sonnet",
        round_markers=[],
        dispatch_data=dispatch_data,
        created_at="2026-06-25T12:00:00.000001+00:00",
    )
    kw.update(over)
    return eng.assemble_audit_marker(**kw)


class TestC3MarkerPopulation(unittest.TestCase):
    def test_real_dispatch_data_populates_rows_cost_overlap(self):
        m = _assemble(_DISPATCH)
        self.assertEqual(len(m.checker_rows), 2)
        self.assertEqual(m.checker_rows[0].angle, "groundedness")
        self.assertEqual(m.checker_rows[0].claims_examined, 5)
        self.assertEqual(m.checker_rows[0].discrepancies, 1)
        self.assertEqual(m.checker_rows[0].claim_verdicts[0].status, "Supported")
        self.assertEqual(m.checker_rows[0].claim_verdicts[0].proof_source,
                         "local-file:foo_RESEARCH.md:12")
        self.assertEqual(m.checker_rows[1].angle, "coverage")
        self.assertEqual(m.cost.checkers.tokens, 400)
        self.assertEqual(m.cost.upgrade.time, 1.5)
        self.assertEqual(m.axes_overlap_signal, "low")
        self.assertEqual(m.rounds_this_dispatch, 1)

    def test_assembled_marker_revalidates_through_yaml(self):
        m = _assemble(_DISPATCH)
        # Round-trip through YAML and re-validate against the UNCHANGED schema:
        # any reshape (stray/renamed field) would fail extra="forbid".
        loaded = yaml.safe_load(yaml.safe_dump(m.model_dump()))
        eng.AuditMarker(**loaded)  # must not raise

    def test_no_dispatch_data_yields_valid_default_marker(self):
        m = _assemble(None)
        self.assertGreaterEqual(len(m.checker_rows), 1)
        self.assertEqual(m.checker_rows[0].angle, "groundedness")
        self.assertEqual(m.checker_rows[0].claims_examined, 0)
        self.assertIsNone(m.escalate_choice)
        eng.AuditMarker(**m.model_dump())  # re-validates


# --------------------------------------------------------------------------- #
# C4 — ESCALATE 3-option handler (UX2).
# --------------------------------------------------------------------------- #

class TestC4Escalate(unittest.TestCase):
    def test_proceed_as_is_records_and_stops(self):
        r = eng.handle_escalate("proceed-as-is", current_round=4)
        self.assertEqual(r["escalate_choice"], "proceed-as-is")
        self.assertEqual(r["action"], "record-and-stop")
        self.assertEqual(r["next_round"], 4)
        self.assertEqual(r["verdict"], "ESCALATE")

    def test_one_more_full_round_advances_counter(self):
        r = eng.handle_escalate("one-more-full-round", current_round=4)
        self.assertEqual(r["action"], "redispatch")
        self.assertEqual(r["next_round"], 5)
        self.assertIsNone(r["verdict"])

    def test_apply_fix_and_rerun_resets_to_round_one(self):
        r = eng.handle_escalate("apply-fix-and-re-run", current_round=4)
        self.assertEqual(r["action"], "fresh-dispatch")
        self.assertEqual(r["next_round"], 1)

    def test_unknown_option_rejected(self):
        with self.assertRaises(eng.EscalateError):
            eng.handle_escalate("give-up", current_round=2)

    def test_escalate_choice_flows_into_marker(self):
        choice = eng.handle_escalate("proceed-as-is", current_round=4)["escalate_choice"]
        m = _assemble({**_DISPATCH, "escalate_choice": choice}, verdict="ESCALATE")
        self.assertEqual(m.escalate_choice, "proceed-as-is")
        eng.AuditMarker(**m.model_dump())  # re-validates


if __name__ == "__main__":
    unittest.main()

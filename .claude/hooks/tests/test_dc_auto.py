#!/usr/bin/env python3
"""Tests for the /double-check `--auto` programmatic-caller bypass (Slice S5).

Plan: Thoughts/double-check-validation-targeting_S5_PLAN.md (Coherent Actions A1/A2/A3).

The `--auto` path is pure code (no AI, no user) — fully pytest-testable. The A4
SKILL.md routing guard is thin skill text verified at S9 (no pytest harness — Gate 2).

  C1  validate_auto_request admits iff (flag) AND (whitelisted caller) AND
      (schema-shaped payload) AND (floor passes); each miss → a structured error
      naming the reason; fail-closed (never a "fall back to interactive" verdict).
  C2  auto_caller marker population: an --auto marker carries auto_caller and leaves
      pre_check_layer ABSENT (the locked Signal #1); no schema reshape.
  C3  _dc_auto_fill_axes: omitted axes → registry default (groundedness forced);
      present axes kept; unknown type → register-error. NO AI.
"""
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402

TRUSTED = "/trusted-caller"


def _payload(**over):
    p = {
        "artifact_type": "research",
        "axes": ["groundedness", "coverage"],
        "claims": ["X holds per foo_RESEARCH.md"],
        "named_source_artifacts": ["local-file:foo_RESEARCH.md"],
    }
    p.update(over)
    return p


# --------------------------------------------------------------------------- #
# C1 — admission gate (whitelist + flag + schema + floor; fail-closed).
# --------------------------------------------------------------------------- #

class TestC1Admission(unittest.TestCase):
    def setUp(self):
        eng.DC_AUTO_CALLER_WHITELIST.add(TRUSTED)
        self.addCleanup(eng.DC_AUTO_CALLER_WHITELIST.discard, TRUSTED)

    def test_admitted_when_trusted_flag_and_schema_valid(self):
        r = eng.validate_auto_request(TRUSTED, _payload(), True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["auto_caller"], TRUSTED)
        self.assertIn("groundedness", r["target"]["axes"])

    def test_missing_flag_rejected(self):
        r = eng.validate_auto_request(TRUSTED, _payload(), False)
        self.assertFalse(r["ok"])
        self.assertIn("no-flag", r["error"])

    def test_non_whitelisted_caller_rejected(self):
        r = eng.validate_auto_request("/random-caller", _payload(), True)
        self.assertFalse(r["ok"])
        self.assertIn("not-whitelisted", r["error"])

    def test_assess_is_statically_seeded_in_the_whitelist(self):
        # assessment-engine cutover: /assess is the ValidationPort caller that runs
        # V1/V2 through /double-check's --auto path; it must be seeded so the dispatch
        # never deadlocks on /double-check's interactive gate (DESIGN Arch #18 / PLAN A5).
        self.assertIn("/assess", eng.DC_AUTO_CALLER_WHITELIST)
        r = eng.validate_auto_request("/assess", _payload(), True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["auto_caller"], "/assess")

    def test_malformed_payload_rejected(self):
        r = eng.validate_auto_request(TRUSTED, "the code base", True)
        self.assertFalse(r["ok"])
        self.assertIn("schema-invalid", r["error"])

    def test_unknown_type_rejected_with_register_hint(self):
        r = eng.validate_auto_request(TRUSTED, _payload(artifact_type="not-a-type"), True)
        self.assertFalse(r["ok"])
        self.assertIn("unknown-type", r["error"])

    def test_floor_failing_payload_rejected(self):
        r = eng.validate_auto_request(TRUSTED, _payload(claims=[]), True)
        self.assertFalse(r["ok"])
        self.assertIn("floor-failed", r["error"])

    def test_never_returns_interactive_fallback(self):
        # Every rejection is a structured error dict — never a "go interactive" verdict.
        for r in (
            eng.validate_auto_request(TRUSTED, _payload(), False),
            eng.validate_auto_request("/x", _payload(), True),
            eng.validate_auto_request(TRUSTED, _payload(named_source_artifacts=[]), True),
        ):
            self.assertFalse(r["ok"])
            self.assertIn("error", r)
            self.assertNotIn("interactive", r.get("error", "").lower())


# --------------------------------------------------------------------------- #
# C3 — axis-default fill (no AI).
# --------------------------------------------------------------------------- #

class TestC3AxisFill(unittest.TestCase):
    def test_omitted_axes_filled_from_registry_default(self):
        r = eng._dc_auto_fill_axes(_payload(axes=None))
        self.assertTrue(r["ok"])
        # research's registry default is groundedness+coverage+source-quality
        self.assertEqual(set(r["axes"]), {"groundedness", "coverage", "source-quality"})

    def test_present_axes_kept(self):
        r = eng._dc_auto_fill_axes(_payload(axes=["groundedness", "coverage"]))
        self.assertEqual(r["axes"], ["groundedness", "coverage"])

    def test_groundedness_force_included(self):
        r = eng._dc_auto_fill_axes(_payload(axes=["coverage"]))
        self.assertIn("groundedness", r["axes"])

    def test_unknown_type_register_error(self):
        r = eng._dc_auto_fill_axes(_payload(artifact_type="not-a-type"))
        self.assertFalse(r["ok"])
        self.assertIn("unknown-type", r["error"])


# --------------------------------------------------------------------------- #
# C2 — auto_caller marker population (no schema reshape; pre_check_layer absent).
# --------------------------------------------------------------------------- #

def _assemble(dispatch_data):
    return eng.assemble_audit_marker(
        artifact_type="research",
        source_path="/tmp/foo_RESEARCH.md",
        caller="/trusted-caller",
        topic="double-check-validation-targeting",
        verdict="PASS",
        rounds_this_dispatch=1,
        allocation_profile="sonnet",
        round_markers=[],
        dispatch_data=dispatch_data,
        created_at="2026-06-25T12:00:00.000001+00:00",
    )


class TestC2AutoCallerMarker(unittest.TestCase):
    def test_auto_caller_set_and_pre_check_layer_absent(self):
        m = _assemble({"models": ["sonnet"], "auto_caller": "/trusted-caller"})
        self.assertEqual(m.auto_caller, "/trusted-caller")
        self.assertIsNone(m.pre_check_layer)  # the locked --auto signal

    def test_revalidates_through_yaml(self):
        m = _assemble({"models": ["sonnet"], "auto_caller": "/trusted-caller"})
        loaded = yaml.safe_load(yaml.safe_dump(m.model_dump()))
        eng.AuditMarker(**loaded)  # no reshape

    def test_non_auto_marker_has_no_auto_caller(self):
        m = _assemble({"models": ["sonnet"]})
        self.assertIsNone(m.auto_caller)
        body = yaml.safe_dump({k: v for k, v in m.model_dump().items() if v is not None})
        self.assertNotIn("auto_caller", body)


if __name__ == "__main__":
    unittest.main()

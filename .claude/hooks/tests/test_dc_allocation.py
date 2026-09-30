#!/usr/bin/env python3
"""Tests for the /double-check 13-type registry + rules mirror + drift check (Slice S2).

Plan: Thoughts/double-check-validation-targeting_S2_PLAN.md (Coherent Actions A1-A3).

Coverage:
  C1  a lookup for each of the 13 locked types returns its own axis set (groundedness
      always present, >=2 axes), each axis carrying a populated AxisSpec.
  C2  double-check-allocation.md mirrors the same (type -> axis-set) catalog exactly.
  C3  a divergence between the code registry and the rules file hard-fails the load
      path AND blocks a commit (via check-double-check-allocation.sh), each naming
      the divergent type/axis. Both surfaces are driven by the one comparison
      function and exercised here under BOTH clean and synthetic-divergence states
      (the commit-time script invoked via subprocess, mirroring
      tests/research_pipeline/test_s4_path_extensions.py).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = HOOKS_DIR / "check-double-check-allocation.sh"
RULES_FILE = eng.DC_ALLOCATION_RULES_PATH

# The 13 locked artifact types (spine `## Scope`), in the locked enumeration order.
LOCKED_TYPES = [
    "recommendation", "code-recommendation", "strategic-kernel", "design",
    "discovery-framing", "metrics-validation", "research", "plan", "scope",
    "audit", "review", "kl-extraction",
]
AXIS_UNIVERSE = {"groundedness", "coverage", "source-quality"}
AXISSPEC_FIELDS = ("operational_definition", "pass_criteria", "failure_modes")


# --------------------------------------------------------------------------- #
# C1 — complete 13-type catalog, each axis carrying a populated AxisSpec.
# --------------------------------------------------------------------------- #

class TestC1Catalog(unittest.TestCase):
    def test_registry_has_exactly_the_13_locked_types(self):
        self.assertEqual(set(eng.DC_AXIS_REGISTRY), set(LOCKED_TYPES))
        self.assertEqual(len(eng.DC_AXIS_REGISTRY), 13)

    def test_each_type_has_groundedness_and_at_least_two_axes(self):
        for atype, spec in eng.DC_AXIS_REGISTRY.items():
            names = [a["name"] for a in spec["axes"]]
            self.assertIn("groundedness", names, f"{atype} missing groundedness")
            self.assertGreaterEqual(len(names), 2, f"{atype} has <2 axes")
            self.assertEqual(len(names), len(set(names)), f"{atype} has duplicate axes")

    def test_axis_names_are_within_the_locked_universe(self):
        for atype, spec in eng.DC_AXIS_REGISTRY.items():
            for axis in spec["axes"]:
                self.assertIn(axis["name"], AXIS_UNIVERSE, f"{atype}: {axis['name']}")

    def test_every_axis_carries_a_populated_axisspec(self):
        for atype, spec in eng.DC_AXIS_REGISTRY.items():
            for axis in spec["axes"]:
                for field in AXISSPEC_FIELDS:
                    self.assertIn(field, axis, f"{atype}/{axis['name']} missing {field}")
                    self.assertTrue(
                        isinstance(axis[field], str) and axis[field].strip(),
                        f"{atype}/{axis['name']} {field} empty",
                    )

    def test_default_allocation_is_134_per_type(self):
        for atype, spec in eng.DC_AXIS_REGISTRY.items():
            self.assertEqual(spec["default_allocation"], "1,3,4", atype)

    def test_lookup_is_deterministic(self):
        self.assertEqual(eng._dc_registry_axis_map(), eng._dc_registry_axis_map())


# --------------------------------------------------------------------------- #
# C2 — rules mirror presents the same catalog exactly.
# --------------------------------------------------------------------------- #

class TestC2Mirror(unittest.TestCase):
    def test_rules_file_exists(self):
        self.assertTrue(RULES_FILE.exists(), f"missing mirror: {RULES_FILE}")

    def test_rules_map_equals_registry_map(self):
        self.assertEqual(
            eng._parse_dc_allocation_rules(RULES_FILE),
            eng._dc_registry_axis_map(),
        )

    def test_clean_pair_reports_no_drift(self):
        self.assertEqual(eng.check_allocation_drift(RULES_FILE), [])


# --------------------------------------------------------------------------- #
# C3 — divergence hard-fails BOTH surfaces, each naming the divergent type/axis.
# --------------------------------------------------------------------------- #

def _temp_rules(mutate):
    """Write a copy of the real rules file with `mutate` applied; return its path."""
    text = RULES_FILE.read_text(encoding="utf-8")
    fd, path = tempfile.mkstemp(suffix="_double-check-allocation.md")
    os.close(fd)
    Path(path).write_text(mutate(text), encoding="utf-8")
    return path


def _drop_research_type(text):
    return text.replace("| `research` | groundedness, coverage, source-quality |\n", "")


def _drop_coverage_from_plan(text):
    return text.replace("| `plan` | groundedness, coverage |", "| `plan` | groundedness |")


class TestC3LoadGuard(unittest.TestCase):
    def test_clean_pair_does_not_raise(self):
        eng._assert_dc_allocation_consistent(RULES_FILE)  # no raise

    def test_dropped_type_raises_naming_the_type(self):
        path = _temp_rules(_drop_research_type)
        try:
            with self.assertRaises(RuntimeError) as cm:
                eng._assert_dc_allocation_consistent(path)
            self.assertIn("research", str(cm.exception))
        finally:
            os.unlink(path)

    def test_changed_axis_raises_naming_type_and_axis(self):
        path = _temp_rules(_drop_coverage_from_plan)
        try:
            divergences = eng.check_allocation_drift(path)
            joined = "; ".join(divergences)
            self.assertIn("plan", joined)
            self.assertIn("coverage", joined)
            with self.assertRaises(RuntimeError):
                eng._assert_dc_allocation_consistent(path)
        finally:
            os.unlink(path)

    def test_missing_rules_file_is_a_divergence(self):
        divergences = eng.check_allocation_drift("/nonexistent/double-check-allocation.md")
        self.assertEqual(len(divergences), 1)
        self.assertIn("not found", divergences[0])


class TestC3CommitScript(unittest.TestCase):
    def _run(self, rules_path):
        env = {**os.environ, "DC_ALLOCATION_RULES_FILE": str(rules_path)}
        return subprocess.run(
            ["bash", str(CHECK_SCRIPT)], capture_output=True, text=True, env=env
        )

    def test_script_exists(self):
        self.assertTrue(CHECK_SCRIPT.exists(), f"missing script: {CHECK_SCRIPT}")

    def test_clean_pair_exits_zero(self):
        result = self._run(RULES_FILE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_dropped_type_blocks_commit_naming_the_type(self):
        path = _temp_rules(_drop_research_type)
        try:
            result = self._run(path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("research", result.stderr)
        finally:
            os.unlink(path)

    def test_changed_axis_blocks_commit_naming_type_and_axis(self):
        path = _temp_rules(_drop_coverage_from_plan)
        try:
            result = self._run(path)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("plan", result.stderr)
            self.assertIn("coverage", result.stderr)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

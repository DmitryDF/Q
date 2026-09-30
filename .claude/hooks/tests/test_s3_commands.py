#!/usr/bin/env python3
"""Tests for S3 — Workflow.md propagation + harness lockstep rename.

Covers:
  (i)   gate1_explore schema accepts `workflow_sources_checked` and rejects
        the legacy `process_sources_checked`.
  (ii)  permission-plan-gate.sh-style header check passes for new heading.
  (iii) Same check passes for legacy `## Process/KL Alignment` (back-compat).
  (iv)  Same check rejects when neither header is present.
  (v)   The four real legacy plans in ~/.claude/plans/ all match the new regex.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_s3_commands.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path.home() / ".claude" / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"
PLANS_DIR = Path.home() / ".claude" / "plans"

# Same regex the hook uses (unanchored on purpose; see plan A4 + edge case h)
HEADER_REGEX = r"## (Workflow|Process)/KL Alignment"


def run(*args, expect=0, stdin=None):
    result = subprocess.run(
        ["python3", *args],
        capture_output=True,
        text=True,
        input=stdin,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"command {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def header_check(plan_path):
    """Mirror the hook's grep -qE ... check. Returns True iff matched."""
    return subprocess.run(
        ["grep", "-qE", HEADER_REGEX, str(plan_path)],
    ).returncode == 0


class GateOneSchemaRenameTests(unittest.TestCase):
    """(i) gate1_explore schema accepts workflow_sources_checked, rejects process_sources_checked."""

    SID = "test-s3-schema"

    def tearDown(self):
        # Clean up any session-state file the advance command may have created
        state_path = (
            Path.home() / ".claude" / "session-state" / f"pre-plan-{self.SID}.json"
        )
        if state_path.exists():
            state_path.unlink()

    def test_accepts_workflow_sources_checked(self):
        # Need to advance gate0 first (sequence enforced).
        run(
            str(PPG_PY), "advance", self.SID, "gate0_framing",
            json.dumps({
                "problem_statement": "x",
                "todo_item": "y",
                "todo_file": "TODO.md",
            }),
        )
        # Now gate1_explore with the NEW key — should succeed.
        r = run(
            str(PPG_PY), "advance", self.SID, "gate1_explore",
            json.dumps({
                "proposed_solution": "x",
                "concerns": ["c1"],
                "kl_sources_checked": ["kl1"],
                "workflow_sources_checked": ["wf1"],
            }),
        )
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "advanced")
        self.assertEqual(out["completed"], "gate1_explore")

    def test_rejects_legacy_process_sources_checked(self):
        # Fresh SID — sequence reset
        sid = self.SID + "-legacy"
        run(
            str(PPG_PY), "advance", sid, "gate0_framing",
            json.dumps({
                "problem_statement": "x",
                "todo_item": "y",
                "todo_file": "TODO.md",
            }),
        )
        # Legacy key — schema now requires workflow_sources_checked, so missing
        # required field causes exit 1.
        r = run(
            str(PPG_PY), "advance", sid, "gate1_explore",
            json.dumps({
                "proposed_solution": "x",
                "concerns": ["c1"],
                "kl_sources_checked": ["kl1"],
                "process_sources_checked": ["legacy"],  # wrong key
            }),
            expect=1,
        )
        # Cleanup
        state_path = (
            Path.home() / ".claude" / "session-state" / f"pre-plan-{sid}.json"
        )
        if state_path.exists():
            state_path.unlink()
        self.assertIn("workflow_sources_checked", r.stderr)


class HeaderCheckTests(unittest.TestCase):
    """(ii)(iii)(iv) Synthetic plan files exercise the permission-plan-gate header check."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s3-hdr-"))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _make(self, body):
        p = self.tmp / "plan.md"
        p.write_text(body)
        return p

    def test_new_heading_passes(self):
        p = self._make("# Plan\n\n## Workflow/KL Alignment\n\nbody.\n")
        self.assertTrue(header_check(p))

    def test_legacy_heading_passes(self):
        p = self._make("# Plan\n\n## Process/KL Alignment\n\nbody.\n")
        self.assertTrue(header_check(p))

    def test_neither_heading_fails(self):
        p = self._make("# Plan\n\n## Some Other Section\n\nbody.\n")
        self.assertFalse(header_check(p))

    def test_indented_legacy_mention_still_matches(self):
        """Edge case (h): unanchored grep matches even when the header text
        appears inside a code block (preserves dazzling-cooking-kitten.md).
        """
        p = self._make(
            "# Plan\n\n```\n  ## Process/KL Alignment (inside code block)\n```\n"
        )
        # Unanchored regex matches the indented occurrence — same semantics
        # as the pre-S3 unanchored grep -q.
        self.assertTrue(header_check(p))


class LegacyPlanRegressionTests(unittest.TestCase):
    """(v) The four real legacy plans in ~/.claude/plans/ all match the new regex.

    Protects against the dazzling-cooking-kitten.md code-block-mentions case
    that an anchored regex would have regressed.
    """

    LEGACY_PLANS = [
        "mighty-humming-token.md",
        "wiggly-jumping-seahorse.md",
        "dazzling-cooking-kitten.md",
        "streamed-weaving-matsumoto.md",
    ]

    def test_all_four_match(self):
        for name in self.LEGACY_PLANS:
            p = PLANS_DIR / name
            with self.subTest(plan=name):
                self.assertTrue(
                    p.exists(),
                    f"Expected legacy plan to exist: {p}",
                )
                self.assertTrue(
                    header_check(p),
                    f"Legacy plan failed new regex (regression): {p}",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

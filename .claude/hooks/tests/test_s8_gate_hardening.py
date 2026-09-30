#!/usr/bin/env python3
"""
S8 gate-hardening regression: check-research-gate.sh unreadable-verdict hole.

Three cases driven against a crafted marker dir:
  (1) Marker present, EMPTY/malformed verdict  → gate BLOCKS (exit 2).
  (2) Marker present, verdict: PASS            → gate ALLOWS (exit 0).
  (3) NO marker file at all                    → gate SKIPS/allows (exit 0).

The third case is the key regression anchor: the legitimate no-marker path
(line ~58 in the script) must remain untouched — a cycle with no R*.md is
"not yet checked" and must still be silently skipped, not blocked.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1]
GATE_SCRIPT = HOOKS_DIR / "check-research-gate.sh"

SID = "s8-hardening-0000-0000-0000-000000000001"
PROJ = "proj"
TOPIC = "topic"


def _make_home(tmp: Path) -> Path:
    """Create a minimal HOME fixture with _active.json wired to PROJ/TOPIC."""
    active_dir = tmp / ".claude" / "state" / "pre_plan_gates"
    active_dir.mkdir(parents=True)
    (active_dir / "_active.json").write_text(
        json.dumps({SID: {"topic_slug": PROJ, "active_project": TOPIC}}),
        encoding="utf-8",
    )
    return tmp


def _research_dir(home: Path) -> Path:
    rdir = home / ".claude" / "state" / "plan_validation" / PROJ / TOPIC / "research"
    rdir.mkdir(parents=True)
    return rdir


def _run_gate(home: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, HOME=str(home))
    env.pop("CLAUDE_CODE_REMOTE", None)
    return subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        input=json.dumps({"session_id": SID, "stop_hook_active": False}),
        capture_output=True,
        text=True,
        env=env,
    )


class GateHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self.home = _make_home(self._tmp)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ── (1) marker present, verdict empty/malformed → BLOCKS ─────────────────

    def test_empty_verdict_in_marker_blocks(self):
        """A marker file that exists but has no parseable verdict: field must block."""
        rdir = _research_dir(self.home)
        # Write a marker with no verdict: line (malformed frontmatter)
        (rdir / "R1.md").write_text(
            "---\nschema_version: 2\nkind: research\n"
            "# verdict line deliberately absent\n"
            "rounds: 1\n---\n\n# body\n",
            encoding="utf-8",
        )
        proc = _run_gate(self.home)
        self.assertEqual(
            proc.returncode, 2,
            f"Expected exit 2 (BLOCK) for empty/malformed verdict; got {proc.returncode}\n"
            f"stderr: {proc.stderr}",
        )
        self.assertIn(
            "BLOCKED:", proc.stderr,
            f"Expected 'BLOCKED:' in stderr; got: {proc.stderr}",
        )
        # The message should mention "unreadable" or "unverified"
        stderr_lower = proc.stderr.lower()
        self.assertTrue(
            "unreadable" in stderr_lower or "unverified" in stderr_lower,
            f"Expected explanatory term in stderr; got: {proc.stderr}",
        )

    def test_empty_verdict_value_in_marker_blocks(self):
        """A marker with verdict: (empty value after colon) must also block."""
        rdir = _research_dir(self.home)
        # Write a marker where verdict: has no value
        (rdir / "R1.md").write_text(
            "---\nschema_version: 2\nkind: research\nverdict:\nrounds: 1\n---\n\n# body\n",
            encoding="utf-8",
        )
        proc = _run_gate(self.home)
        self.assertEqual(
            proc.returncode, 2,
            f"Expected exit 2 (BLOCK) for empty verdict value; got {proc.returncode}\n"
            f"stderr: {proc.stderr}",
        )

    # ── (2) marker present, verdict: PASS → ALLOWS ───────────────────────────

    def test_pass_verdict_allows(self):
        """A well-formed PASS marker must allow (exit 0) — normal path must not regress."""
        rdir = _research_dir(self.home)
        (rdir / "R1.md").write_text(
            "---\nschema_version: 2\nkind: research\nverdict: PASS\n"
            "rounds: 1\nchecked_at: 2026-07-13T00:00:00+00:00\n---\n\n# body\n",
            encoding="utf-8",
        )
        proc = _run_gate(self.home)
        self.assertEqual(
            proc.returncode, 0,
            f"Expected exit 0 (ALLOW) for PASS marker; got {proc.returncode}\n"
            f"stderr: {proc.stderr}",
        )

    # ── (3) NO marker file → SKIPS/allows (legitimate no-marker path) ────────

    def test_no_marker_skips_silently(self):
        """A research dir with no R*.md must silently exit 0 (not-yet-checked cycle)."""
        _research_dir(self.home)  # dir exists but no R*.md files
        proc = _run_gate(self.home)
        self.assertEqual(
            proc.returncode, 0,
            f"Expected exit 0 (SKIP) when no marker present; got {proc.returncode}\n"
            f"stderr: {proc.stderr}",
        )
        # Must not produce any error output — truly silent
        self.assertEqual(
            proc.stderr.strip(), "",
            f"Expected empty stderr on no-marker skip; got: {proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()

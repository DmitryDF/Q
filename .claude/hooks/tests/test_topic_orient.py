#!/usr/bin/env python3
"""Tests for topic-orient + phase-backfill CLI subcommands.

Slice C (plan: cryptic-mapping-torvalds.md). Six fixtures map to the four
production-screenshot scenarios + two synthetic edge cases:

  (i)   research-scope-and-focus baseline — state.phase=null + locked
        Discovery + Solution Alternative + plan present → mid-flight,
        backfill_needed=True, phase_inferred=implementation.
  (ii)  unit-economics handoff prompt — no state but prompt-body names an
        existing spine + plan → mid-flight, intake_source_class thought-bound.
  (iii) unit-economics worktree-origin — state with phase=null,
        project_root=None, thought_file_path=None → new, intake_source_class
        worktree-origin.
  (iv)  plan-mode session with no research_pipeline manifest → mid-flight
        (hot-path) + intake_source_class != "research".
  (v)   brand-new topic, no spine → new, intake_source_class none.
  (vi)  completed topic (phase=closing) → completed.

Plus a small phase-backfill suite covering: legacy null-phase backfill,
idempotent re-run, refusal on unknown phase, refusal on open checkpoint.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_topic_orient.py
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HOOKS = Path.home() / ".claude" / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"
TOPIC_STATE_DIR = Path.home() / ".claude" / "state" / "pre_plan_gates"


def _import_ppg():
    spec = importlib.util.spec_from_file_location("pre_plan_gates", PPG_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*args, stdin=None, expect=0):
    result = subprocess.run(
        ["python3", str(PPG_PY), *args],
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


_SPINE_LOCKED_DISCOVERY = """# Topic — Sample
## Status

Active.

# Discovery

## Problem

We need a thing.

## Guiding Policy

Take approach X.

## Desired Outcome

Outcome Y materialises for the user.

## Desired Solution

Build a thing that does Y.

## Metrics

OMTM: Z reaches >0.

## Scope

In: thing. Out: not-thing.

# Solution Design

### Solution Alternative 1

Do thing A.

# Implementation Details

Plan: ~/.claude/plans/sample-plan-slug.md

## Sessions
"""

_SPINE_DISCOVERY_ONLY = """# Topic — Sample

# Discovery

## Problem

A problem.

## Guiding Policy

A policy.

## Desired Outcome

An outcome.

## Desired Solution

A solution.

## Metrics

OMTM: stuff.

## Scope

In: x.
"""


class TopicFixtureBase(unittest.TestCase):
    """Shared scaffolding — creates a synthetic topic-state JSON + _active entry."""

    PROJ = "testc1ppg"
    TOPIC = "topicorient"  # overridden per test class

    def _seed_state(self, state_overrides=None):
        self.SID = f"test-c-{uuid.uuid4().hex[:12]}"
        TOPIC_STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.topic_state_path = (
            TOPIC_STATE_DIR / f"{self.PROJ}__{self.TOPIC}.json"
        )
        state = {
            "topic_slug": self.PROJ,
            "project_slug": self.TOPIC,
            "bypass_marker": False,
            "bypass_reason": None,
            "project_root": None,
            "thought_file_path": None,
            "created": "2026-06-13T00:00:00+00:00",
            "updated": "2026-06-13T00:00:00+00:00",
            "phase": None,
            "phase_history": [],
            "phase_complete": {},
            "kernel_subset_by_phase": None,
            "clarification_phase_oqs_cleared": None,
            "clarification_phase_todo_registered": None,
            "open_decision_checkpoint": None,
            "intake_source": "thought",
            "todo_line_ref": None,
        }
        if state_overrides:
            state.update(state_overrides)
        self.topic_state_path.write_text(json.dumps(state, indent=2))
        active_path = TOPIC_STATE_DIR / "_active.json"
        active = (
            json.loads(active_path.read_text()) if active_path.exists() else {}
        )
        self._active_backup = dict(active)
        active[self.SID] = {
            "topic_slug": self.PROJ,
            "active_project": self.TOPIC,
            "updated": "2026-06-13T00:00:00+00:00",
        }
        active_path.write_text(json.dumps(active, indent=2))

    def _restore_active(self):
        active_path = TOPIC_STATE_DIR / "_active.json"
        active_path.write_text(json.dumps(self._active_backup, indent=2))
        if self.topic_state_path.exists():
            self.topic_state_path.unlink()


# -----------------------------------------------------------------------------
# Topic-orient fixtures (6 scenarios)
# -----------------------------------------------------------------------------

class Fixture1ResearchScopeBaseline(TopicFixtureBase):
    """phase=null + locked Discovery + Solution Alternative + plan → mid-flight."""
    PROJ = "testc1ppg"
    TOPIC = "f1researchscope"

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj_root = Path(self.tmpdir.name)
        thoughts = self.proj_root / "Thoughts"
        thoughts.mkdir()
        spine = thoughts / "research-scope_THOUGHT.md"
        spine.write_text(_SPINE_LOCKED_DISCOVERY)
        plan = thoughts / "research-scope_PLAN.md"
        plan.write_text("# Plan\n\nApproved.\n")
        self.spine = spine
        self.plan = plan
        self._seed_state(state_overrides={
            "project_root": str(self.proj_root),
            "thought_file_path": str(spine),
            "phase": None,
            "phase_history": [],
        })

    def tearDown(self):
        self._restore_active()
        self.tmpdir.cleanup()

    def test_verdict_is_midflight_backfill_needed(self):
        r = run("topic-orient", self.SID)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "mid-flight")
        self.assertTrue(out["backfill_needed"])
        self.assertEqual(out["phase_observed"], None)
        self.assertEqual(out["phase_inferred_from_spine"], "implementation")
        self.assertEqual(out["intake_source_class"], "thought-bound")


class Fixture2HandoffPrompt(TopicFixtureBase):
    """No state, prompt body references existing spine + plan → mid-flight."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.proj_root = Path(self.tmpdir.name)
        thoughts = self.proj_root / "Thoughts"
        thoughts.mkdir()
        self.spine = thoughts / "unit-econ_THOUGHT.md"
        self.spine.write_text(_SPINE_LOCKED_DISCOVERY)
        self.plan = thoughts / "unit-econ_PLAN.md"
        self.plan.write_text("# Plan\n\nApproved.\n")
        # No state: this is a fresh session pasting a handoff prompt.
        self.fresh_sid = f"test-c2-{uuid.uuid4().hex[:12]}"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_prompt_body_resolves_to_existing_artifacts(self):
        prompt = (
            f"Read {self.spine} fully. Plan at {self.plan}. "
            "Continue Slice S3 implementation."
        )
        r = run("topic-orient", self.fresh_sid,
                "--prompt-body", "-", stdin=prompt)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "mid-flight")
        self.assertEqual(out["phase_inferred_from_spine"], "implementation")
        self.assertTrue(out["backfill_needed"])
        self.assertEqual(out["intake_source_class"], "thought-bound")


class Fixture3WorktreeOrigin(TopicFixtureBase):
    """State with phase=null + project_root=None + thought_file_path=None → new
    + intake_source_class worktree-origin."""

    PROJ = "testc1ppg"
    TOPIC = "f3worktree"

    def setUp(self):
        self._seed_state(state_overrides={
            "project_root": None,
            "thought_file_path": None,
            "intake_source": None,
            "phase": None,
            "phase_history": [],
        })

    def tearDown(self):
        self._restore_active()

    def test_verdict_new_intake_class_worktree_origin(self):
        r = run("topic-orient", self.SID)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "new")
        self.assertEqual(out["intake_source_class"], "worktree-origin")
        self.assertEqual(out["phase_observed"], None)
        self.assertEqual(out["phase_inferred_from_spine"], None)
        self.assertFalse(out["backfill_needed"])


class Fixture4PlanModeHotPath(TopicFixtureBase):
    """phase=implementation + phase_history non-empty → mid-flight via hot path,
    intake_source_class != research (no manifest)."""

    PROJ = "testc1ppg"
    TOPIC = "f4hotpath"

    def setUp(self):
        self._seed_state(state_overrides={
            "phase": "implementation",
            "phase_history": [{
                "from": "planning", "to": "implementation",
                "at": "2026-06-13T01:00:00+00:00",
                "source": "phase-start",
                "session_id": "earlier-sid",
            }],
            "intake_source": "thought",
        })

    def tearDown(self):
        self._restore_active()

    def test_hot_path_short_circuit(self):
        r = run("topic-orient", self.SID)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "mid-flight")
        self.assertFalse(out["backfill_needed"])
        self.assertNotEqual(out["intake_source_class"], "research")
        # Evidence trail must surface the short-circuit decision.
        self.assertTrue(
            any("hot-path" in e for e in out["evidence"]),
            f"expected hot-path note in evidence: {out['evidence']}",
        )


class Fixture5BrandNew(TopicFixtureBase):
    """No state at all → new + intake_source_class none."""

    def test_unknown_session_returns_new(self):
        fresh_sid = f"test-c5-{uuid.uuid4().hex[:12]}"
        r = run("topic-orient", fresh_sid)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "new")
        self.assertEqual(out["intake_source_class"], "none")
        self.assertFalse(out["backfill_needed"])


class Fixture6Completed(TopicFixtureBase):
    """phase=closing → completed."""

    PROJ = "testc1ppg"
    TOPIC = "f6completed"

    def setUp(self):
        self._seed_state(state_overrides={
            "phase": "closing",
            "phase_history": [{
                "from": "implementation", "to": "closing",
                "at": "2026-06-13T02:00:00+00:00",
                "source": "phase-start",
                "session_id": "older-sid",
            }],
        })

    def tearDown(self):
        self._restore_active()

    def test_verdict_completed(self):
        r = run("topic-orient", self.SID)
        out = json.loads(r.stdout)
        self.assertEqual(out["verdict"], "completed")
        self.assertEqual(out["phase_observed"], "closing")
        self.assertFalse(out["backfill_needed"])


# -----------------------------------------------------------------------------
# phase-backfill suite
# -----------------------------------------------------------------------------

class PhaseBackfillTests(TopicFixtureBase):
    PROJ = "testc1ppg"
    TOPIC = "backfilltests"

    def setUp(self):
        self._seed_state(state_overrides={
            "phase": None,
            "phase_history": [],
            "intake_source": None,
            "project_root": None,
            "thought_file_path": None,
        })

    def tearDown(self):
        self._restore_active()

    def test_legacy_null_to_implementation(self):
        ev = json.dumps({"spine_locked": True, "plan_present": True})
        r = run("phase-backfill", self.SID, "--to", "implementation",
                "--evidence", ev)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "backfilled")
        self.assertEqual(out["phase"], "implementation")
        state = json.loads(self.topic_state_path.read_text())
        self.assertEqual(state["phase"], "implementation")
        # All prior phases auto-completed.
        for p in ("thought", "clarification", "planning"):
            self.assertTrue(state["phase_complete"].get(p),
                            f"phase_complete[{p}] should be True")
        # History entry source tag.
        self.assertEqual(state["phase_history"][-1]["source"], "backfill")

    def test_idempotent_rerun(self):
        ev = json.dumps({"spine_locked": True})
        run("phase-backfill", self.SID, "--to", "planning", "--evidence", ev)
        r = run("phase-backfill", self.SID, "--to", "planning", "--evidence", ev)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "noop")

    def test_refuse_unknown_phase(self):
        ev = json.dumps({})
        r = run("phase-backfill", self.SID, "--to", "bogus", "--evidence", ev,
                expect=1)
        self.assertIn("not in PHASE_SEQUENCE", r.stderr)

    def test_refuse_open_decision_checkpoint(self):
        state = json.loads(self.topic_state_path.read_text())
        state["open_decision_checkpoint"] = {
            "description": "pending",
            "opened_at": "2026-06-13T03:00:00+00:00",
        }
        self.topic_state_path.write_text(json.dumps(state, indent=2))
        ev = json.dumps({})
        r = run("phase-backfill", self.SID, "--to", "planning",
                "--evidence", ev, expect=1)
        self.assertIn("open decision checkpoint", r.stderr)


if __name__ == "__main__":
    unittest.main()

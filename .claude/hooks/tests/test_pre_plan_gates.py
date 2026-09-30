#!/usr/bin/env python3
"""Tests for the engine-vocabulary rename in pre_plan_gates.py.

Slice B1 + B5 (plan: cryptic-mapping-torvalds.md). Confirms:
  - PHASE_SEQUENCE carries `clarification` (not `preplanning`)
  - phase-start advances thought → clarification with the new auth token
  - phase-complete for `clarification` gates on the new field names
    `clarification_phase_oqs_cleared` + `clarification_phase_todo_registered`
  - B5 back-compat CLI alias `get-preplanning-session` returns the same
    stdout as the new `get-clarification-phase-session`

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_pre_plan_gates.py
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

# Respect CLAUDE_CONFIG_DIR so config-experiment staging clones test their OWN
# hooks; defaults to live ~/.claude in production.
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"
TOPIC_STATE_DIR = Path.home() / ".claude" / "state" / "pre_plan_gates"


def _import_ppg():
    spec = importlib.util.spec_from_file_location("pre_plan_gates", PPG_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*args, expect=0):
    result = subprocess.run(
        ["python3", str(PPG_PY), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"command {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


class TopicFixtureMixin:
    """Set up a synthetic topic + active session, restore on tearDown."""

    PROJ = "testb1ppg"
    TOPIC = None  # set per-test class to keep slugs unique across test files

    def _setup_topic(self, state_overrides=None):
        self.SID = f"test-b1-{uuid.uuid4().hex[:12]}"
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

    def _teardown_topic(self):
        active_path = TOPIC_STATE_DIR / "_active.json"
        active_path.write_text(json.dumps(self._active_backup, indent=2))
        if self.topic_state_path.exists():
            self.topic_state_path.unlink()


class PhaseSequenceConstantTests(unittest.TestCase):
    def test_phase_sequence_is_renamed_5tuple(self):
        ppg = _import_ppg()
        self.assertEqual(
            ppg.PHASE_SEQUENCE,
            ["thought", "clarification", "planning", "implementation", "closing"],
        )
        self.assertNotIn("preplanning", ppg.PHASE_SEQUENCE)


class PhaseStartAdvancesToClarificationTests(TopicFixtureMixin, unittest.TestCase):
    TOPIC = "phasestart"

    def setUp(self):
        self._setup_topic(state_overrides={
            "phase": "thought",
            "phase_complete": {"thought": True},
        })

    def tearDown(self):
        self._teardown_topic()

    def test_thought_to_clarification_with_valid_auth(self):
        ppg = _import_ppg()
        token = ppg._make_phase_auth_token(self.SID, "thought", "clarification")
        r = run("phase-start", self.SID, "clarification", "--auth", token)
        out = json.loads(r.stdout)
        self.assertEqual(out["phase"], "clarification")
        # State on disk now carries phase = clarification.
        state = json.loads(self.topic_state_path.read_text())
        self.assertEqual(state["phase"], "clarification")


class PhaseCompleteClarificationGatingTests(TopicFixtureMixin, unittest.TestCase):
    TOPIC = "phasecomplete"

    def setUp(self):
        # Topic already in clarification; phase_complete[clarification] will
        # be set by phase-complete itself. The gate we exercise lives in
        # phase_start (advancing to planning) — it requires the two
        # clarification-phase booleans.
        self._setup_topic(state_overrides={
            "phase": "clarification",
            "phase_complete": {"thought": True},
            "clarification_phase_oqs_cleared": None,
            "clarification_phase_todo_registered": None,
        })

    def tearDown(self):
        self._teardown_topic()

    def test_advance_to_planning_blocks_without_new_fields(self):
        ppg = _import_ppg()
        # Mark phase_complete[clarification] = True via phase-complete (no --oqs)
        r = run("phase-complete", self.SID)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "phase_marked_complete")
        # Now try to advance to planning — must fail, missing the two flags.
        token = ppg._make_phase_auth_token(self.SID, "clarification", "planning")
        r = run("phase-start", self.SID, "planning", "--auth", token, expect=1)
        # Error message should name the new field, not the legacy field.
        self.assertIn("clarification_phase_todo_registered", r.stderr)
        self.assertNotIn("preplanning_todo_registered", r.stderr)

    def test_advance_to_planning_succeeds_with_new_fields_set(self):
        ppg = _import_ppg()
        # Set the booleans via OQS auth + register-session-todo bypass: we
        # set them directly in state for this gating test.
        state = json.loads(self.topic_state_path.read_text())
        state["clarification_phase_oqs_cleared"] = True
        state["clarification_phase_todo_registered"] = True
        state["phase_complete"]["clarification"] = True
        self.topic_state_path.write_text(json.dumps(state, indent=2))
        token = ppg._make_phase_auth_token(self.SID, "clarification", "planning")
        r = run("phase-start", self.SID, "planning", "--auth", token)
        out = json.loads(r.stdout)
        self.assertEqual(out["phase"], "planning")


class B5BackCompatAliasTests(TopicFixtureMixin, unittest.TestCase):
    TOPIC = "b5alias"

    def setUp(self):
        self._setup_topic(state_overrides={
            "phase": "clarification",
            "clarification_phase_session_id": "session-stamped-abc123",
        })

    def tearDown(self):
        self._teardown_topic()

    def test_legacy_alias_matches_new_cli_stdout(self):
        new = run("get-clarification-phase-session", self.SID)
        legacy = run("get-preplanning-session", self.SID)
        self.assertEqual(new.stdout, legacy.stdout)
        self.assertEqual(new.stdout.strip(), "session-stamped-abc123")
        # Legacy must emit deprecation on stderr; new must not.
        self.assertIn("deprecation", legacy.stderr)
        self.assertNotIn("deprecation", new.stderr)


class CreateTopicIdempotencyTests(unittest.TestCase):
    """Plan eager-inventing-hare.md A2: existence guard on create_topic().

    Verifies the three branches added by the idempotency guard:
      1. Existing-topic + fresh sid → status="rebound", state preserved
      2. Brand-new topic            → status="created", file written
      3. Corrupt-JSON file on disk  → status="created", corruption overwritten
    """

    PROJ = "TestProjIdem"
    TOPIC = "test-idem-topic"

    def setUp(self):
        import tempfile
        self.ppg = _import_ppg()
        self._orig_topic_state_dir = self.ppg.TOPIC_STATE_DIR
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "state" / "pre_plan_gates"
        self.state_dir.mkdir(parents=True)
        self.ppg.TOPIC_STATE_DIR = self.state_dir

    def tearDown(self):
        import shutil
        self.ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _topic_file(self):
        return self.state_dir / f"{self.PROJ}__{self.TOPIC}.json"

    def test_rebind_preserves_existing_state(self):
        sid_old = f"sid-old-{uuid.uuid4().hex[:8]}"
        sid_new = f"sid-new-{uuid.uuid4().hex[:8]}"

        first = self.ppg.create_topic(sid_old, project_slug=self.TOPIC,
                                      topic_slug=self.PROJ,
                                      intake_source="thought")
        self.assertEqual(first["status"], "created")

        state = json.loads(self._topic_file().read_text())
        state["phase"] = "implementation"
        state["phase_history"] = [
            {"from": None, "to": "thought", "at": "t0"},
            {"from": "thought", "to": "implementation", "at": "t1"},
        ]
        state["phase_complete"] = {"thought": True, "clarification": True,
                                   "planning": True}
        state["clarification_step"] = 10
        self._topic_file().write_text(json.dumps(state))

        second = self.ppg.create_topic(sid_new, project_slug=self.TOPIC,
                                       topic_slug=self.PROJ,
                                       intake_source="thought")
        self.assertEqual(second["status"], "rebound")
        self.assertEqual(second["topic"], f"{self.PROJ}__{self.TOPIC}")

        post = json.loads(self._topic_file().read_text())
        self.assertEqual(post["phase"], "implementation")
        self.assertEqual(len(post["phase_history"]), 2)
        self.assertEqual(post["phase_complete"], {"thought": True,
                                                  "clarification": True,
                                                  "planning": True})
        self.assertEqual(post["clarification_step"], 10)

        active = json.loads((self.state_dir / "_active.json").read_text())
        self.assertEqual(active[sid_new]["topic_slug"], self.PROJ)
        self.assertEqual(active[sid_new]["active_project"], self.TOPIC)

    def test_fresh_create_unchanged(self):
        sid = f"sid-fresh-{uuid.uuid4().hex[:8]}"
        result = self.ppg.create_topic(sid, project_slug="brand-new-proj",
                                       topic_slug="brand-new-topic")
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["topic"], "brand-new-topic__brand-new-proj")
        path = self.state_dir / "brand-new-topic__brand-new-proj.json"
        self.assertTrue(path.exists())
        state = json.loads(path.read_text())
        self.assertIsNone(state["phase"])
        self.assertEqual(state["phase_history"], [])

    def test_corrupt_file_treated_as_missing(self):
        sid = f"sid-corrupt-{uuid.uuid4().hex[:8]}"
        self._topic_file().write_text("{not valid json")
        result = self.ppg.create_topic(sid, project_slug=self.TOPIC,
                                       topic_slug=self.PROJ)
        self.assertEqual(result["status"], "created")
        post = json.loads(self._topic_file().read_text())
        self.assertEqual(post["topic_slug"], self.PROJ)
        self.assertEqual(post["project_slug"], self.TOPIC)
        self.assertIsNone(post["phase"])


class RelocateModeABStateReadTest(unittest.TestCase):
    """Regression: Mode A/B relocation must resolve project_root + topic_slug via
    _resolve_topic (which reads the per-topic <slug>__<proj>.json state file), NOT
    via a nonexistent `topic_state` key on the flat, session-keyed _active.json.

    Guards the recurring bug where relocate_plan_after_approval raised
    "Mode A/B relocation requires topic_state.project_root and topic_slug" for
    every tracked plan (hit two topics in three days, 2026-07-07 / 2026-07-09).
    """

    def setUp(self):
        import tempfile
        self.ppg = _import_ppg()
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "TOPIC_STATE_DIR": self.ppg.TOPIC_STATE_DIR,
            "PLAN_MODE_INIT_DIR": self.ppg.PLAN_MODE_INIT_DIR,
            "POST_PLAN_CHOICE_DIR": self.ppg.POST_PLAN_CHOICE_DIR,
        }
        self.state_dir = self.tmpdir / "state" / "pre_plan_gates"
        self.init_dir = self.tmpdir / "state" / "plan_mode_init"
        self.choice_dir = self.tmpdir / "state" / "post_plan_choice"
        for d in (self.state_dir, self.init_dir, self.choice_dir):
            d.mkdir(parents=True)
        self.ppg.TOPIC_STATE_DIR = self.state_dir
        self.ppg.PLAN_MODE_INIT_DIR = self.init_dir
        self.ppg.POST_PLAN_CHOICE_DIR = self.choice_dir

    def tearDown(self):
        import shutil
        for k, v in self._orig.items():
            setattr(self.ppg, k, v)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _relocate(self, mode):
        sid = f"sid-reloc-{uuid.uuid4().hex[:8]}"
        proj_root = self.tmpdir / "ProjRoot"
        proj_root.mkdir()
        topic_slug = "reloc-topic"
        project_slug = "ProjSlug"
        # Seed the active session pointer + per-topic state, exactly as the
        # lifecycle would (_active.json is session-keyed; the per-topic file
        # holds project_root + topic_slug at top level).
        self.ppg.create_topic(sid, project_slug=project_slug,
                              topic_slug=topic_slug, intake_source="thought")
        topic_file = self.state_dir / f"{topic_slug}__{project_slug}.json"
        state = json.loads(topic_file.read_text())
        state["project_root"] = str(proj_root)
        topic_file.write_text(json.dumps(state))
        # plan-mode-init marker (mode A/B) + a thought file so the spine
        # back-link append has a real target.
        thought = proj_root / f"{topic_slug}_THOUGHT.md"
        thought.write_text("# T\n\n# Implementation Details\n")
        (self.init_dir / f"{sid}.json").write_text(
            json.dumps({"mode": mode, "thought_path": str(thought)}))
        # post_plan_choice grant marker — relocation refuses without it.
        (self.choice_dir / f"{sid}.marker").write_text(
            json.dumps({"pending": False}))
        plan = self.tmpdir / "harness-plan.md"
        plan.write_text("# Plan\n")
        return self.ppg.relocate_plan_after_approval(sid, str(plan))

    def test_mode_a_relocation_resolves_via_resolve_topic(self):
        result = self._relocate("A")
        self.assertEqual(result["status"], "relocated")
        self.assertIn("ProjRoot", result["dest"])
        self.assertTrue(result["dest"].endswith("_PLAN.md"))
        self.assertIn("Thoughts", result["dest"])
        self.assertTrue(Path(result["dest"]).is_file())

    def test_mode_b_relocation_resolves_via_resolve_topic(self):
        result = self._relocate("B")
        self.assertEqual(result["status"], "relocated")
        self.assertIn("ProjRoot", result["dest"])
        self.assertTrue(Path(result["dest"]).is_file())

    def test_no_active_topic_raises_clear_error(self):
        # Grant marker + init marker present, but NO active topic → the fix must
        # raise a clear ValueError, not crash on a None state.
        sid = f"sid-noactive-{uuid.uuid4().hex[:8]}"
        (self.init_dir / f"{sid}.json").write_text(json.dumps({"mode": "A"}))
        (self.choice_dir / f"{sid}.marker").write_text(
            json.dumps({"pending": False}))
        plan = self.tmpdir / "harness-plan.md"
        plan.write_text("# Plan\n")
        with self.assertRaises(ValueError):
            self.ppg.relocate_plan_after_approval(sid, str(plan))

    def test_relocation_unbinds_manifest_and_unlinks_src(self):
        # A3: after a Mode-C relocation, the harness plan must STOP being resolvable
        # — its manifest entry is removed and the src file unlinked, so
        # find-session-plan.sh no longer governs later spawns with a done plan.
        import os
        import _plan_manifest
        sid = f"sid-unbind-{uuid.uuid4().hex[:8]}"
        (self.init_dir / f"{sid}.json").write_text(json.dumps({"mode": "C"}))
        (self.choice_dir / f"{sid}.marker").write_text(json.dumps({"pending": False}))
        plan = self.tmpdir / "harness-unbind.md"
        plan.write_text("# Unbind Plan\n")
        # Seed a temp manifest with sid -> harness plan; point _plan_manifest at it
        # via env so relocate's remove() targets the temp file, not the live one.
        manifest = self.tmpdir / "plans" / ".manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        _plan_manifest.add(sid, str(plan), str(manifest))
        old_env = os.environ.get("PLAN_MANIFEST_PATH")
        os.environ["PLAN_MANIFEST_PATH"] = str(manifest)
        old_proj = self.ppg.PROJECTS_ROOT
        self.ppg.PROJECTS_ROOT = self.tmpdir / "ProjectsRoot"
        try:
            result = self.ppg.relocate_plan_after_approval(
                sid, str(plan), dest_slug="unbind-topic")
        finally:
            self.ppg.PROJECTS_ROOT = old_proj
            if old_env is None:
                os.environ.pop("PLAN_MANIFEST_PATH", None)
            else:
                os.environ["PLAN_MANIFEST_PATH"] = old_env
        self.assertEqual(result["status"], "relocated")
        self.assertTrue(Path(result["dest"]).is_file())
        # src unlinked
        self.assertFalse(plan.exists(), "harness src should be unlinked")
        self.assertIs(result["unbind"]["src_unlinked"], True)
        # manifest entry removed (session key dropped — it was the only path)
        data = json.loads(manifest.read_text())
        self.assertNotIn(sid, data, "session manifest entry should be removed")
        self.assertIs(result["unbind"]["manifest_removed"], True)


class RecentPhaseEventsTests(unittest.TestCase):
    """S3 (M6 read-side): recent_phase_events() windows phase_history to the
    most-recent-session window (state-dir only) and renders one-line summaries;
    the recent-phase-events CLI verb prints it and never errors on no-topic.

    In-process cases monkeypatch TOPIC_STATE_DIR to a temp dir (no live state,
    the CreateTopicIdempotencyTests idiom). CLI cases use a random SID that
    resolves to no topic, so they need no state and touch nothing live.
    """

    PROJ = "TestProjRPE"
    TOPIC = "test-rpe-topic"

    def setUp(self):
        import tempfile
        self.ppg = _import_ppg()
        self._orig = self.ppg.TOPIC_STATE_DIR
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "state" / "pre_plan_gates"
        self.state_dir.mkdir(parents=True)
        self.ppg.TOPIC_STATE_DIR = self.state_dir

    def tearDown(self):
        import shutil
        self.ppg.TOPIC_STATE_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _topic_file(self):
        return self.state_dir / f"{self.PROJ}__{self.TOPIC}.json"

    def _bind(self, phase_history):
        sid = f"sid-rpe-{uuid.uuid4().hex[:8]}"
        self.ppg.create_topic(sid, project_slug=self.TOPIC,
                              topic_slug=self.PROJ, intake_source="thought")
        state = json.loads(self._topic_file().read_text())
        state["phase_history"] = phase_history
        self._topic_file().write_text(json.dumps(state))
        return sid

    # --- windowing -------------------------------------------------------

    def test_two_stops_returns_only_last_window(self):
        hist = [
            {"from": None, "to": "thought", "at": "2026-01-01T00:00:00+00:00"},
            {"event": "stop", "phase": "thought", "at": "2026-01-02T00:00:00+00:00"},
            {"from": "thought", "to": "planning", "at": "2026-01-03T00:00:00+00:00",
             "source": "advance"},
            {"event": "stop", "phase": "planning", "at": "2026-01-04T00:00:00+00:00"},
        ]
        out = self.ppg.recent_phase_events(self._bind(hist))
        self.assertEqual(out["status"], "ok")
        # window = entries after the 2nd-most-recent stop (idx 1) → idx 2,3
        self.assertEqual(out["count"], 2)
        self.assertEqual([e["at"] for e in out["events"]],
                         ["2026-01-03T00:00:00+00:00", "2026-01-04T00:00:00+00:00"])
        self.assertEqual(out["window_start"], "2026-01-03T00:00:00+00:00")

    def test_fewer_than_two_stops_returns_all(self):
        hist = [
            {"from": None, "to": "thought", "at": "2026-01-01T00:00:00+00:00"},
            {"event": "stop", "phase": "thought", "at": "2026-01-02T00:00:00+00:00"},
            {"from": "thought", "to": "planning", "at": "2026-01-03T00:00:00+00:00"},
        ]
        out = self.ppg.recent_phase_events(self._bind(hist))
        self.assertEqual(out["count"], 3)

    def test_empty_history_returns_no_events(self):
        out = self.ppg.recent_phase_events(self._bind([]))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["events"], [])
        self.assertEqual(out["count"], 0)
        self.assertIsNone(out["window_start"])

    def test_no_active_project(self):
        out = self.ppg.recent_phase_events("sid-does-not-exist-xyz")
        self.assertEqual(out, {"status": "no_active_project"})

    # --- rendering -------------------------------------------------------

    def test_render_transition_backfill_and_stop(self):
        hist = [
            {"from": None, "to": "thought", "at": "2026-01-01T09:00:00+00:00",
             "source": "backfill"},
            {"from": "thought", "to": "planning", "at": "2026-01-03T09:00:00+00:00",
             "source": "advance"},
            {"event": "stop", "phase": "planning", "at": "2026-01-04T09:00:00+00:00"},
        ]
        out = self.ppg.recent_phase_events(self._bind(hist))
        summaries = [e["summary"] for e in out["events"]]
        self.assertEqual(summaries[0], "→thought (2026-01-01, backfill)")
        self.assertEqual(summaries[1], "thought→planning (2026-01-03, advance)")
        self.assertEqual(summaries[2], "session stopped in planning (2026-01-04)")

    def test_malformed_non_dict_entry_is_skipped_not_raised(self):
        hist = [
            "i am not a dict",  # would AttributeError on .get without the guard
            {"from": None, "to": "thought", "at": "2026-01-01T00:00:00+00:00"},
            42,
        ]
        out = self.ppg.recent_phase_events(self._bind(hist))
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["count"], 1)  # only the one dict survives
        self.assertEqual(out["events"][0]["to"], "thought")

    # --- CLI verb (subprocess; no state needed for the no-topic path) -----

    def test_cli_no_topic_exits_zero_with_status(self):
        r = run("recent-phase-events", "sid-cli-none-xyz", expect=0)
        self.assertEqual(json.loads(r.stdout)["status"], "no_active_project")

    def test_cli_missing_arg_exits_two(self):
        run("recent-phase-events", expect=2)


class HandoffFreshSinceTests(unittest.TestCase):
    """`handoff-fresh-since SPINE SINCE_ISO` — the M7 (project-tracking-staleness
    S5) code-enforced fail-loud check that the persisted `## Next Session Prompt`
    was regenerated at/after the ship-window start. Verdict + timestamp-format
    matrix. Pure read — no topic state, no writes."""

    _DISCOVERY = (
        "# Discovery\n\n"
        "## Guiding Policy\ngp body\n\n"
        "## Desired Outcome\ndo body\n\n"
        "## Desired Solution\nds body\n\n"
        "## Metrics\nOMTM: x\n\n"
        "# Next\n"
    )

    def _spine(self, *, discovery=True, written=None):
        """Write a temp spine and return its path.

        discovery=False → no `# Discovery` section (→ NO_DISCOVERY).
        written=<iso>   → embed a `## Next Session Prompt` marker with that
                          `written:` stamp; None → Discovery but no marker
                          (→ ABSENT)."""
        parts = []
        if discovery:
            parts.append(self._DISCOVERY)
        else:
            parts.append("# Idea\nsome idea, no Discovery section\n")
        if written is not None:
            parts.append(
                "\n## Next Session Prompt\n\n*Written: %s*\n\nbody\n\n"
                "<!-- handoff-src-hash: abc123def456 phase: n/a written: %s -->\n"
                % (written, written)
            )
        fd, path = tempfile.mkstemp(suffix="_THOUGHT.md")
        os.close(fd)
        Path(path).write_text("".join(parts), encoding="utf-8")
        self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))
        return path

    def test_fresh_written_after_since(self):
        spine = self._spine(written="2026-07-27T21:00:00Z")
        r = run("handoff-fresh-since", spine, "2026-07-27T20:00:00+00:00", expect=0)
        self.assertEqual(r.stdout.strip(), "FRESH")

    def test_fresh_written_equal_since_is_fresh(self):
        # Boundary equality → FRESH (written >= since).
        spine = self._spine(written="2026-07-27T20:00:00Z")
        r = run("handoff-fresh-since", spine, "2026-07-27T20:00:00Z", expect=0)
        self.assertEqual(r.stdout.strip(), "FRESH")

    def test_stale_written_before_since(self):
        spine = self._spine(written="2026-07-27T19:00:00Z")
        r = run("handoff-fresh-since", spine, "2026-07-27T20:00:00+00:00", expect=1)
        self.assertEqual(r.stdout.strip(), "STALE")

    def test_absent_discovery_but_no_marker(self):
        spine = self._spine(written=None)  # Discovery present, no NSP marker
        r = run("handoff-fresh-since", spine, "2026-07-27T20:00:00Z", expect=1)
        self.assertEqual(r.stdout.strip(), "ABSENT")

    def test_no_discovery_soft_exit(self):
        spine = self._spine(discovery=False, written="2026-07-27T21:00:00Z")
        r = run("handoff-fresh-since", spine, "2026-07-27T20:00:00Z", expect=0)
        self.assertEqual(r.stdout.strip(), "NO_DISCOVERY")

    # --- timestamp-format matrix (Python 3.9 fromisoformat rejects bare 'Z') --

    def test_written_Z_since_offset(self):
        # written uses ...Z (marker form); since uses +00:00 (lock form).
        spine = self._spine(written="2026-07-27T21:00:00Z")
        r = run("handoff-fresh-since", spine, "2026-07-27T20:33:42+00:00", expect=0)
        self.assertEqual(r.stdout.strip(), "FRESH")

    def test_written_offset_since_Z(self):
        # written uses +00:00; since uses ...Z. Both must parse + compare.
        spine = self._spine(written="2026-07-27T19:00:00+00:00")
        r = run("handoff-fresh-since", spine, "2026-07-27T20:00:00Z", expect=1)
        self.assertEqual(r.stdout.strip(), "STALE")

    def test_legacy_marker_without_written_is_absent(self):
        # A legacy 2-segment marker (no `written:` group) → treated as ABSENT
        # (regen should have re-stamped a written: segment).
        fd, path = tempfile.mkstemp(suffix="_THOUGHT.md")
        os.close(fd)
        Path(path).write_text(
            self._DISCOVERY
            + "\n## Next Session Prompt\n\nbody\n\n"
            "<!-- handoff-src-hash: abc123def456 phase: n/a -->\n",
            encoding="utf-8",
        )
        self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))
        r = run("handoff-fresh-since", path, "2026-07-27T20:00:00Z", expect=1)
        self.assertEqual(r.stdout.strip(), "ABSENT")

    def test_missing_arg_exits_two(self):
        run("handoff-fresh-since", expect=2)

    def test_missing_spine_exits_two(self):
        run("handoff-fresh-since", "/no/such/spine_THOUGHT.md",
            "2026-07-27T20:00:00Z", expect=2)


class PhaseMarkerTests(TopicFixtureMixin, unittest.TestCase):
    """S6 (project-tracking-staleness) — write_phase_marker: the M6 write-side
    phase-boundary marker + E3 synchronous verify_write fail-loud.

    Uses an ABSOLUTE thought_file_path (tmp spine), so _resolve_thought_path
    returns it directly WITHOUT invoking bookkeeping_resolver — i.e. these
    exercise the non-worktree path (byte-identical to a resolver-resolved main
    path, per the shipped A5 fallback). The main-pinned resolver + bookkeeping_lock
    coupling is inherited verbatim from the S4-shipped _resolve_thought_path /
    annotate_session template.
    """

    TOPIC = "phasemarker-s6"

    def setUp(self):
        self.ppg = _import_ppg()
        self.tmpdir = Path(tempfile.mkdtemp())
        self.spine = self.tmpdir / "phasemarker-s6_THOUGHT.md"

    def tearDown(self):
        import shutil
        if hasattr(self, "_active_backup"):
            self._teardown_topic()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _bind(self, spine_body, overrides=None):
        self.spine.write_text(spine_body, encoding="utf-8")
        ov = {"thought_file_path": str(self.spine), "phase": "implementation"}
        if overrides:
            ov.update(overrides)
        self._setup_topic(state_overrides=ov)

    def _phase_rows(self, phase=None):
        rows = []
        for ln in self.spine.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s.startswith("<!-- L:phase "):
                if phase is None or f"phase={phase} " in s:
                    rows.append(s)
        return rows

    def test_marker_written_and_section_created(self):
        # Happy path: ## Phase Register auto-created, row written + verified (no raise).
        self._bind("# Idea\n\n## Slice Register\n\n## Sessions\n\n### x [sid:abc]\n")
        res = self.ppg.write_phase_marker(self.SID, "start", "implementation")
        self.assertEqual(res["status"], "section_created")
        text = self.spine.read_text(encoding="utf-8")
        self.assertIn("## Phase Register", text)
        rows = self._phase_rows("implementation")
        self.assertEqual(len(rows), 1)
        self.assertIn("event=start", rows[0])
        # anchored before the ## Sessions block (trailing content undisturbed)
        self.assertLess(text.index("## Phase Register"), text.index("## Sessions"))

    def test_replace_in_place_updates_event(self):
        # One row per phase name, replaced in place (event start -> stop).
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n")
        self.ppg.write_phase_marker(self.SID, "start", "implementation")
        res2 = self.ppg.write_phase_marker(self.SID, "stop", "implementation")
        self.assertEqual(res2["status"], "row_replaced")
        rows = self._phase_rows("implementation")
        self.assertEqual(len(rows), 1)
        self.assertIn("event=stop", rows[0])

    def test_distinct_phases_and_single_section(self):
        # Distinct phases -> distinct rows; the section is reused, not duplicated.
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n")
        self.ppg.write_phase_marker(self.SID, "stop", "clarification")
        self.ppg.write_phase_marker(self.SID, "start", "implementation")
        text = self.spine.read_text(encoding="utf-8")
        self.assertEqual(len(self._phase_rows()), 2)
        self.assertEqual(text.count("## Phase Register"), 1)

    def test_plain_mode_skipped_no_spine(self):
        # C4 non-regression: a spine-less (plain-mode) topic is skipped — no write, no raise.
        self._setup_topic(state_overrides={
            "thought_file_path": None, "phase": "thought", "intake_source": "plain"})
        res = self.ppg.write_phase_marker(self.SID, "start", "thought")
        self.assertEqual(res["status"], "skipped_no_spine")

    def test_absent_phase_skipped_no_write(self):
        # S1/A1 — absent-phase guard: an absent phase is a legitimate state
        # (both create_topic and _auto_register_state mint phase: None). Calling
        # write_phase_marker with phase=None must write ZERO bytes to the spine
        # (byte-identical before/after) and raise nothing.
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n")
        before = self.spine.read_bytes()
        res = self.ppg.write_phase_marker(self.SID, "start", None)
        self.assertEqual(res["status"], "skipped_no_phase")
        self.assertEqual(self.spine.read_bytes(), before)

    def test_phase_start_marker_write_unchanged_by_the_guard(self):
        # S1/A1 non-regression: phase_start's phase is PHASE_SEQUENCE-validated
        # and can never be absent — its marker write must keep working exactly
        # as before the guard was added.
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n")
        res = self.ppg.write_phase_marker(self.SID, "start", "implementation")
        self.assertEqual(res["status"], "section_created")
        rows = self._phase_rows("implementation")
        self.assertEqual(len(rows), 1)
        self.assertIn("event=start", rows[0])

    def test_no_active_topic(self):
        res = self.ppg.write_phase_marker(
            f"unbound-{uuid.uuid4().hex[:8]}", "start", "implementation")
        self.assertEqual(res["status"], "no_active_topic")

    def test_section_appended_at_eof_when_no_anchor(self):
        # No ## Sessions / ## Slice Register anchor -> section appended at EOF.
        self._bind("# Idea\n\nSome prose only.\n")
        res = self.ppg.write_phase_marker(self.SID, "start", "implementation")
        self.assertEqual(res["status"], "section_created")
        self.assertIn("## Phase Register", self.spine.read_text(encoding="utf-8"))
        self.assertEqual(len(self._phase_rows("implementation")), 1)

    def test_verify_failure_propagates_fail_loud(self):
        # E3/AD-8: a verify_write failure is NOT swallowed — it propagates (fail-loud).
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n")
        sys.path.insert(0, str(HOOKS))
        import taskmanagement as tm
        orig = tm.verify_write

        def _boom(*a, **k):
            raise tm.WriteVerificationError("forced mismatch")

        tm.verify_write = _boom
        try:
            with self.assertRaises(tm.WriteVerificationError):
                self.ppg.write_phase_marker(self.SID, "start", "implementation")
        finally:
            tm.verify_write = orig

    def test_cli_write_phase_marker_idempotent(self):
        # A3 CLI: infers event/phase from the last phase_history entry, writes +
        # verifies, and is idempotent on re-run (still exactly one row, exit 0).
        self._bind(
            "# Idea\n\n## Sessions\n\n### x [sid:abc]\n",
            overrides={"phase": "implementation",
                       "phase_history": [{"from": "planning", "to": "implementation",
                                          "at": "2026-07-27T00:00:00+00:00",
                                          "session_id": "x"}]})
        r1 = run("write-phase-marker", self.SID)
        self.assertIn("event=start", r1.stdout)
        run("write-phase-marker", self.SID)
        self.assertEqual(len(self._phase_rows("implementation")), 1)

    def test_cli_phase_stop_on_phaseless_topic_reports_and_persists(self):
        # S1/A2 — phase-stop on a phase-less record: (i) exits zero and its
        # receipt carries the skipped_no_phase marker status while the phase
        # transition (the stop entry in phase_history) IS persisted regardless
        # (the marker write is the caller's LAST step and never rolls back the
        # already-durable transition); (ii) the CLI's captured stderr actually
        # names the absent phase — asserted against captured output, not
        # against the return dict.
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n", overrides={"phase": None})
        r = run("phase-stop", self.SID)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["phase_marker"]["status"], "skipped_no_phase")
        state = json.loads(self.topic_state_path.read_text())
        self.assertTrue(state["phase_history"])
        self.assertEqual(state["phase_history"][-1]["event"], "stop")
        self.assertIn("no phase is recorded", r.stderr.lower())

    def test_cli_write_phase_marker_refuses_when_phase_absent(self):
        # S1/A3(i) — the recovery verb refuses rather than guesses: a
        # phase-less record with no phase_history yields a NAMED non-zero
        # outcome and NO spine write.
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n",
                    overrides={"phase": None, "phase_history": []})
        before = self.spine.read_bytes()
        r = run("write-phase-marker", self.SID, expect=1)
        self.assertIn("no phase recorded", r.stderr.lower())
        self.assertEqual(self.spine.read_bytes(), before)

    def test_cli_write_phase_marker_recovers_with_explicit_phase(self):
        # S1/A3(ii) — --phase is the operator's explicit escape from the
        # refusal above; exercised, not merely documented: it writes the
        # correct row and exits zero.
        self._bind("# Idea\n\n## Sessions\n\n### x [sid:abc]\n",
                    overrides={"phase": None, "phase_history": []})
        r = run("write-phase-marker", self.SID, "--phase", "implementation")
        payload = json.loads(r.stdout)
        self.assertEqual(payload["status"], "section_created")
        self.assertEqual(payload["phase"], "implementation")
        rows = self._phase_rows("implementation")
        self.assertEqual(len(rows), 1)


class FreshnessGateInputsTests(unittest.TestCase):
    """`FreshnessGateInputs` — the typed strict-enum whitelist contract for the
    freshness gate's permitted inputs (project-tracking-staleness S9 / AD-11).

    Covers: the whitelist is exactly the two live bytes-derivable signals; a
    non-whitelisted input name raises at construction (strict ==, not substring);
    `from_spine` parity with the direct `_discovery_locked_fields_hash` /
    `_phase_progress_fingerprint` calls (incl. None); and a seam-regression that
    the read seam (`handoff-freshness`), now routed through the contract, emits
    identical FRESH/STALE verdicts across the phased / non-phased / legacy matrix
    (which also proves the write-seam values, since both seams use `from_spine`)."""

    _DISCOVERY = (
        "# Discovery\n\n"
        "## Guiding Policy\ngp body\n\n"
        "## Desired Outcome\ndo body\n\n"
        "## Desired Solution\nds body\n\n"
        "## Metrics\nOMTM: x\n\n"
    )
    _SOLUTION_DESIGN = (
        "# Solution Design\n\n"
        "### Solution Alternative 1\nchosen\n\n"
        "# Implementation Details\n\n"
        "<!-- L:slice id=S9 status=NOW sessions=0/1 updated=2026-07-31 -->\n\n"
    )

    def _phased_spine(self):
        return self._DISCOVERY + self._SOLUTION_DESIGN

    def _nonphased_spine(self):
        # Discovery present, no `# Solution Design` → phase fingerprint is None.
        return self._DISCOVERY + "# Idea\nno solution design section\n"

    @staticmethod
    def _nsp_3seg(disco, phase):
        return (
            "## Next Session Prompt\n\n*Written: 2026-07-31T00:00:00Z*\n\nbody\n\n"
            f"<!-- handoff-src-hash: {disco} phase: {phase} "
            "written: 2026-07-31T00:00:00Z -->\n"
        )

    @staticmethod
    def _nsp_legacy(disco):
        # 2-segment marker (group 2 None): no phase segment.
        return (
            "## Next Session Prompt\n\nbody\n\n"
            f"<!-- handoff-src-hash: {disco} -->\n"
        )

    def _write(self, text):
        fd, path = tempfile.mkstemp(suffix="_THOUGHT.md")
        os.close(fd)
        Path(path).write_text(text, encoding="utf-8")
        self.addCleanup(lambda p=path: os.path.exists(p) and os.remove(p))
        return path

    # --- the contract itself (direct, via imported module) -----------------

    def test_whitelist_is_exactly_the_two_live_inputs(self):
        mod = _import_ppg()
        self.assertEqual(
            mod.FRESHNESS_GATE_INPUT_WHITELIST,
            frozenset({"discovery_locked_fields_hash", "phase_progress_fingerprint"}),
        )

    def test_non_whitelisted_name_raises(self):
        mod = _import_ppg()
        # Plausible future-drift inputs that are NOT bytes-derivable — each must
        # be refused at construction, so the gate's input surface cannot widen.
        for bad in ("wall_clock", "session_state", "mutable_counter", "bogus"):
            with self.assertRaises(ValueError):
                mod.FreshnessGateInputs(**{bad: "x"})

    def test_substring_of_a_whitelisted_name_still_raises(self):
        # E7: membership is strict ==, never substring — a name that is a
        # substring/superstring of a whitelisted one must still raise.
        mod = _import_ppg()
        with self.assertRaises(ValueError):
            mod.FreshnessGateInputs(discovery_locked_fields="x")  # missing _hash
        with self.assertRaises(ValueError):
            mod.FreshnessGateInputs(phase_progress_fingerprint_extra="x")

    def test_whitelisted_names_construct_and_expose_attrs(self):
        mod = _import_ppg()
        fi = mod.FreshnessGateInputs(
            discovery_locked_fields_hash="aaaaaaaaaaaa",
            phase_progress_fingerprint="bbbbbbbbbbbb",
        )
        self.assertEqual(fi.discovery_locked_fields_hash, "aaaaaaaaaaaa")
        self.assertEqual(fi.phase_progress_fingerprint, "bbbbbbbbbbbb")

    def test_missing_whitelisted_input_defaults_none(self):
        mod = _import_ppg()
        fi = mod.FreshnessGateInputs()
        self.assertIsNone(fi.discovery_locked_fields_hash)
        self.assertIsNone(fi.phase_progress_fingerprint)

    def test_from_spine_parity_with_direct_calls(self):
        # Behavior-preserving: from_spine values == the direct function calls,
        # including the None cases, across phased / non-phased / no-Discovery.
        mod = _import_ppg()
        for text in (self._phased_spine(),
                     self._nonphased_spine(),
                     "# Idea\nno discovery at all\n"):
            fi = mod.FreshnessGateInputs.from_spine(text)
            self.assertEqual(fi.discovery_locked_fields_hash,
                             mod._discovery_locked_fields_hash(text))
            self.assertEqual(fi.phase_progress_fingerprint,
                             mod._phase_progress_fingerprint(text))

    # --- read-seam regression (handoff-freshness CLI, routed through contract) --

    def test_read_seam_phased_correct_marker_is_fresh(self):
        mod = _import_ppg()
        body = self._phased_spine()
        disco = mod._discovery_locked_fields_hash(body)
        phase = mod._phase_progress_fingerprint(body)
        self.assertIsNotNone(disco)
        self.assertIsNotNone(phase)
        p = self._write(body + "\n" + self._nsp_3seg(disco, phase))
        self.assertEqual(run("handoff-freshness", p).stdout.strip(), "FRESH")

    def test_read_seam_phased_wrong_phase_is_stale(self):
        mod = _import_ppg()
        body = self._phased_spine()
        disco = mod._discovery_locked_fields_hash(body)
        p = self._write(body + "\n" + self._nsp_3seg(disco, "ffffffffffff"))
        self.assertEqual(run("handoff-freshness", p).stdout.strip(), "STALE")

    def test_read_seam_phased_wrong_disco_is_stale(self):
        mod = _import_ppg()
        body = self._phased_spine()
        phase = mod._phase_progress_fingerprint(body)
        p = self._write(body + "\n" + self._nsp_3seg("ffffffffffff", phase))
        self.assertEqual(run("handoff-freshness", p).stdout.strip(), "STALE")

    def test_read_seam_phased_legacy_marker_is_stale(self):
        # Legacy 2-segment marker (group 2 None) on a phased spine → STALE
        # (safe-direction over-detection preserved by the contract routing).
        mod = _import_ppg()
        body = self._phased_spine()
        disco = mod._discovery_locked_fields_hash(body)
        p = self._write(body + "\n" + self._nsp_legacy(disco))
        self.assertEqual(run("handoff-freshness", p).stdout.strip(), "STALE")

    def test_read_seam_nonphased_na_marker_is_fresh(self):
        # Non-phased spine (phase fingerprint None) + a `phase: n/a` marker with
        # the correct disco hash → FRESH (expected_phase == "n/a").
        mod = _import_ppg()
        body = self._nonphased_spine()
        disco = mod._discovery_locked_fields_hash(body)
        self.assertIsNone(mod._phase_progress_fingerprint(body))
        p = self._write(body + "\n" + self._nsp_3seg(disco, "n/a"))
        self.assertEqual(run("handoff-freshness", p).stdout.strip(), "FRESH")

    def test_read_seam_no_discovery_is_absent(self):
        p = self._write("# Idea\nno discovery section here\n")
        self.assertEqual(run("handoff-freshness", p).stdout.strip(), "ABSENT")


class AutoBindUnboundSessionTests(unittest.TestCase):
    """M13 (project-tracking-staleness S10): session -> topic auto-bind at open.

    Covers auto_bind_unbound_session (both bind sources + the E26 refuse-guard +
    behavior-preservation on the bound path + _active.json-only write) and the two
    seams it serves: the `auto-bind` CLI verb (the topic_orient / /work-start path)
    and the `read` verb (the annotate_session / omission-Stop-hook path).
    """

    def setUp(self):
        self.ppg = _import_ppg()
        self._orig_state_dir = self.ppg.TOPIC_STATE_DIR
        self._orig_in_worktree = self.ppg._in_worktree
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "state" / "pre_plan_gates"
        self.state_dir.mkdir(parents=True)
        self.ppg.TOPIC_STATE_DIR = self.state_dir
        # Default: not in a worktree (worktree source empty unless a test opts in).
        self.ppg._in_worktree = lambda cwd=None: False

    def tearDown(self):
        import shutil
        self.ppg.TOPIC_STATE_DIR = self._orig_state_dir
        self.ppg._in_worktree = self._orig_in_worktree
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_state(self, topic_slug, project_slug, extra=None):
        state = {"topic_slug": topic_slug, "project_slug": project_slug,
                 "phase": "planning",
                 "phase_history": [{"from": None, "to": "planning", "at": "t0"}],
                 "thought_file_path": None}
        if extra:
            state.update(extra)
        path = self.state_dir / f"{topic_slug}__{project_slug}.json"
        path.write_text(json.dumps(state))
        return path

    def _active(self):
        p = self.state_dir / "_active.json"
        return json.loads(p.read_text()) if p.exists() else {}

    # --- bind source: worktree name ---
    def test_worktree_source_binds(self):
        self.ppg._in_worktree = lambda cwd=None: True
        self._write_state("wt-topic", "Root")
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        res = self.ppg.auto_bind_unbound_session(sid, cwd="/x/wt-topic")
        self.assertEqual(res["status"], "bound")
        self.assertEqual(res["source"], "worktree")
        self.assertEqual(res["topic"], "wt-topic__Root")
        self.assertEqual(self._active()[sid]["topic_slug"], "wt-topic")
        self.assertEqual(self._active()[sid]["active_project"], "Root")

    # --- bind source: spine-path ref in prompt body ---
    def test_spine_path_source_binds(self):
        self._write_state("sp-topic", "Root")
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        res = self.ppg.auto_bind_unbound_session(
            sid, prompt_body="Read Thoughts/sp-topic_THOUGHT.md fully")
        self.assertEqual(res["status"], "bound")
        self.assertEqual(res["source"], "spine-path")
        self.assertEqual(res["topic"], "sp-topic__Root")

    # --- refuse: no deterministic source ---
    def test_no_source_unresolvable(self):
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        res = self.ppg.auto_bind_unbound_session(sid, prompt_body=None)
        self.assertEqual(res["status"], "unresolvable")
        self.assertEqual(self._active(), {})

    # --- refuse: source resolves a slug but no on-disk state (E26) ---
    def test_resolved_slug_absent_state_refuses(self):
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        res = self.ppg.auto_bind_unbound_session(
            sid, prompt_body="Thoughts/ghost-topic_THOUGHT.md")
        self.assertEqual(res["status"], "no_match")
        self.assertIn("ghost-topic", res["candidates"])
        self.assertEqual(self._active(), {})

    # --- refuse: ambiguous (>1 project for the same slug) (E26) ---
    def test_ambiguous_multi_project_refuses(self):
        self._write_state("amb-topic", "Root")
        self._write_state("amb-topic", "Projects")
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        res = self.ppg.auto_bind_unbound_session(
            sid, prompt_body="Thoughts/amb-topic_THOUGHT.md")
        self.assertEqual(res["status"], "no_match")
        self.assertEqual(self._active(), {})

    # --- behavior-preserving: already-bound session is a no-op ---
    def test_already_bound_noop(self):
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        self.ppg.create_topic(sid, "Root", "bound-topic")  # binds sid
        res = self.ppg.auto_bind_unbound_session(
            sid, prompt_body="Thoughts/other-topic_THOUGHT.md")
        self.assertEqual(res["status"], "already_bound")
        self.assertEqual(res["topic"], "bound-topic__Root")

    # --- _active.json-only write: topic-state bytes unchanged (rebound) ---
    def test_bind_writes_active_only(self):
        path = self._write_state("keep-topic", "Root",
                                 extra={"clarification_step": 7})
        before = path.read_bytes()
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        res = self.ppg.auto_bind_unbound_session(
            sid, prompt_body="Thoughts/keep-topic_THOUGHT.md")
        self.assertEqual(res["status"], "bound")
        self.assertEqual(path.read_bytes(), before)  # topic-state untouched

    # --- read-seam core: auto_bind (worktree, no cwd) then re-resolve binds ---
    def test_read_seam_core_autobinds(self):
        self.ppg._in_worktree = lambda cwd=None: True
        self._write_state("read-topic", "Root")
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        cwd0 = os.getcwd()
        slugdir = Path(self.tmpdir) / "read-topic"
        slugdir.mkdir()
        try:
            os.chdir(slugdir)  # Path.cwd().name == "read-topic"
            self.assertEqual(self.ppg._resolve_topic(sid), (None, None, None))
            b = self.ppg.auto_bind_unbound_session(sid)  # no prompt -> worktree src
            self.assertEqual(b["status"], "bound")
            post = self.ppg._resolve_topic(sid)
            self.assertEqual((post[0], post[1]), ("read-topic", "Root"))
        finally:
            os.chdir(cwd0)

    # --- CLI verb (the /work-start / topic_orient seam), HOME-isolated ---
    def test_autobind_verb_spine_path_subprocess(self):
        home = Path(self.tmpdir) / "home1"
        sd = home / ".claude" / "state" / "pre_plan_gates"
        sd.mkdir(parents=True)
        (sd / "cli-topic__Root.json").write_text(json.dumps(
            {"topic_slug": "cli-topic", "project_slug": "Root", "phase": "planning"}))
        (sd / "_active.json").write_text("{}")
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        env = dict(os.environ, HOME=str(home))
        r = subprocess.run(
            ["python3", str(PPG_PY.resolve()), "auto-bind", sid, "--prompt-body", "-"],
            input="Thoughts/cli-topic_THOUGHT.md\n",
            capture_output=True, text=True, env=env, cwd=str(home))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "bound")
        self.assertEqual(out["topic"], "cli-topic__Root")
        self.assertEqual(
            json.loads((sd / "_active.json").read_text())[sid]["topic_slug"],
            "cli-topic")

    # --- read verb behavior-preserving: unbound + no source -> no_state_file ---
    def test_read_verb_unbound_no_source_subprocess(self):
        home = Path(self.tmpdir) / "home2"
        sd = home / ".claude" / "state" / "pre_plan_gates"
        sd.mkdir(parents=True)
        (sd / "_active.json").write_text("{}")
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        env = dict(os.environ, HOME=str(home))
        r = subprocess.run(["python3", str(PPG_PY.resolve()), "read", sid],
                           capture_output=True, text=True, env=env, cwd=str(home))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "no_state_file")

    # --- read verb behavior-preserving: already-bound -> topic_only, no auto_bound ---
    def test_read_verb_bound_skips_autobind_subprocess(self):
        home = Path(self.tmpdir) / "home3"
        sd = home / ".claude" / "state" / "pre_plan_gates"
        sd.mkdir(parents=True)
        sid = f"sid-{uuid.uuid4().hex[:8]}"
        (sd / "rt__Root.json").write_text(json.dumps(
            {"topic_slug": "rt", "project_slug": "Root", "phase": "planning"}))
        (sd / "_active.json").write_text(json.dumps(
            {sid: {"topic_slug": "rt", "active_project": "Root"}}))
        env = dict(os.environ, HOME=str(home))
        r = subprocess.run(["python3", str(PPG_PY.resolve()), "read", sid],
                           capture_output=True, text=True, env=env, cwd=str(home))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["status"], "topic_only")
        self.assertNotIn("auto_bound", out)  # already bound -> auto-bind skipped


if __name__ == "__main__":
    unittest.main()

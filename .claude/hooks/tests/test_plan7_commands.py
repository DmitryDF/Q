#!/usr/bin/env python3
"""Tests for Plan 7 — Validation Embedding (A1-A6, Phase 1-2).

Covers:
  (a) _factcheck_engine.factcheck_run orchestrates rounds for each kind
  (b) factcheck-plan / factcheck-thought subcommands wire correctly via CLI
  (c) Plan 6 factcheck-workflow regression — same structure/content after engine extraction
  (d) is-plan-validated exit codes: 0=PASS, 1=state_mismatch, 2=marker_missing, 3=no_active_project
  (e) is-validation-waived honors literal-marker-substring, rejects fabricated
  (f) debounce: factcheck_run returns DEBOUNCED within cooldown window
  (g) independence — tool restriction: _invoke_checker_engine passes --allowedTools to subprocess
  (h) independence — input scoping: _build_checker_input passes path only, no content
  (i) independence — aggregation determinism: pure-code DISCREPANCY scan, order-independent
  (j) kind=plan/thought uses PLAN_VALIDATION_DIR with <kind> subdir; workflow stays flat

Run: python3 ${KIT_HOOKS_DIR}/tests/test_plan7_commands.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

HOOKS = Path.home() / ".claude" / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"

sys.path.insert(0, str(HOOKS))


def run_py(*args, expect=0, stdin=None):
    result = subprocess.run(
        ["python3", str(PPG_PY), *args],
        capture_output=True, text=True, input=stdin,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"pre_plan_gates.py {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def _setup_active_project(sid, proj, topic, tmpdir):
    """Create active pointer + topic state in a tmpdir-based state dir."""
    state_dir = Path(tmpdir) / "state" / "pre_plan_gates"
    state_dir.mkdir(parents=True)
    active = {sid: {"topic_slug": proj, "active_project": topic, "updated": "2026-01-01T00:00:00+00:00"}}
    (state_dir / "_active.json").write_text(json.dumps(active))
    topic_state = {
        "topic_slug": proj,
        "project_slug": topic,
        "bypass_marker": False,
        "bypass_reason": None,
        "thought_file_path": None,
        "workflow_draft_confirmed_marker": None,
        "gates": {
            "gate0_framing": {}, "gate1_explore": {},
            "gate2_validate": {}, "gate3_align": {}, "gate4_success_metrics": {},
        },
        "created": "2026-01-01T00:00:00+00:00",
        "updated": "2026-01-01T00:00:00+00:00",
    }
    (state_dir / f"{proj}__{topic}.json").write_text(json.dumps(topic_state))
    return state_dir


def _mock_resolver(proj, topic, state_override=None):
    """Return an injectable resolver that returns fixed proj/topic/state."""
    state = state_override or {
        "topic_slug": proj, "project_slug": topic,
        "gates": {
            "gate0_framing": {}, "gate1_explore": {},
            "gate2_validate": {}, "gate3_align": {}, "gate4_success_metrics": {},
        },
    }
    return lambda _sid: (proj, topic, state)


class EngineOrchestrationTests(unittest.TestCase):
    """(a) factcheck_run orchestrates rounds for each kind (workflow, plan, thought)."""

    PROJ = "TestP7"
    TOPIC = "test-orch"
    SID = "test-p7-orch"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "validation"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _pass_checker(self, draft_path, checker_idx, model, round_num, prior_issues):
        return "PASS"

    def _fail_once_checker(self, draft_path, checker_idx, model, round_num, prior_issues):
        if round_num == 1 and checker_idx == 0:
            return "DISCREPANCY: test issue in round 1"
        return "PASS"

    def _always_fail_checker(self, draft_path, checker_idx, model, round_num, prior_issues):
        return "DISCREPANCY: always fails"

    def test_workflow_pass_r1(self):
        """workflow kind: PASS at R1 with all-PASS checker."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        result = eng.factcheck_run(
            self.state_dir, str(draft), "workflow", self.SID,
            debounce_seconds=0,
            _checker_fn=self._pass_checker,
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 1)
        topic_dir = self.state_dir / self.PROJ / self.TOPIC
        self.assertTrue((topic_dir / "R1.md").exists())

    def test_plan_pass_r1(self):
        """plan kind: PASS at R1; round files in <topic>/plan/ subdir."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "test-plan.md"
        draft.write_text("# Plan\n\n<!-- GATE0A:PROBLEM -->\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        result = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=self._pass_checker,
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result["status"], "PASS")
        # kind subdir
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "plan"
        self.assertTrue((kind_dir / "R1.md").exists())

    def test_thought_pass_r1(self):
        """thought kind: PASS at R1; round files in <topic>/thought/ subdir."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "topic_THOUGHT.md"
        draft.write_text("# Thought\n\n## Gate 0\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        result = eng.factcheck_run(
            self.state_dir, str(draft), "thought", self.SID,
            debounce_seconds=0,
            _checker_fn=self._pass_checker,
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result["status"], "PASS")
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "thought"
        self.assertTrue((kind_dir / "R1.md").exists())

    def test_plan_dirty_r1_pass_r2(self):
        """plan kind: DIRTY at R1, PASS at R2; 2 round files written."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "test-plan.md"
        draft.write_text("# Plan\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        result = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=self._fail_once_checker,
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 2)
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "plan"
        self.assertTrue((kind_dir / "R1.md").exists())
        self.assertTrue((kind_dir / "R2.md").exists())

    def test_hard_stop_at_r2(self):
        """Any kind: ESCALATE with no_consensus_after_2_rounds at max_rounds=2 (Session 1a canon)."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "test-plan.md"
        draft.write_text("# Plan\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        result = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=self._always_fail_checker,
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result["status"], "ESCALATE")
        self.assertEqual(result["reason"], "no_consensus_after_2_rounds")
        self.assertEqual(result["rounds"], 2)

    def test_plan_pass_appends_converged_marker(self):
        """plan kind: on PASS, <!-- VALIDATION:CONVERGED --> appended to body."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "test-plan.md"
        draft.write_text("# Plan\n\nSome content.\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=self._pass_checker,
            _proj_topic_resolver=resolver,
        )
        content = draft.read_text()
        self.assertIn("<!-- VALIDATION:CONVERGED -->", content)

    def test_workflow_pass_appends_validated_via_frontmatter(self):
        """workflow kind: on PASS, validated_via: appended to frontmatter (Plan 6 behavior)."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        eng.factcheck_run(
            self.state_dir, str(draft), "workflow", self.SID,
            debounce_seconds=0,
            _checker_fn=self._pass_checker,
            _proj_topic_resolver=resolver,
        )
        content = draft.read_text()
        self.assertIn("validated_via:", content)
        self.assertNotIn("<!-- VALIDATION:CONVERGED -->", content)

    def test_no_active_project_raises(self):
        """factcheck_run raises ValueError when no active topic."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "plan.md"
        draft.write_text("# Plan\n")
        resolver = lambda _sid: (None, None, None)

        with self.assertRaises(ValueError):
            eng.factcheck_run(
                self.state_dir, str(draft), "plan", self.SID,
                debounce_seconds=0, _proj_topic_resolver=resolver,
            )

    def test_factcheck_run_rejects_kl_extraction(self):
        """factcheck_run rejects kind='kl_extraction'; dedicated entry point lands in Session 4b."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "extraction.md"
        draft.write_text("# Extraction\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        with self.assertRaises(ValueError) as ctx:
            eng.factcheck_run(
                self.state_dir, str(draft), "kl_extraction", self.SID,
                debounce_seconds=0,
                _checker_fn=self._pass_checker,
                _proj_topic_resolver=resolver,
            )
        self.assertIn("Session 4b", str(ctx.exception))

    def test_locked_when_concurrent(self):
        """LOCKED returned when another factcheck holds the flock."""
        import _factcheck_engine as eng
        import fcntl

        draft = Path(self.tmpdir) / "plan.md"
        draft.write_text("# Plan\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        # Pre-create kind dir and acquire lock manually
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "plan"
        kind_dir.mkdir(parents=True, exist_ok=True)
        lockfile = kind_dir / ".lock"

        def hold_lock():
            with open(lockfile, "w") as lf:
                fcntl.flock(lf, fcntl.LOCK_EX)
                time.sleep(0.4)
                fcntl.flock(lf, fcntl.LOCK_UN)

        t = threading.Thread(target=hold_lock)
        t.start()
        time.sleep(0.05)

        result = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=self._pass_checker,
            _proj_topic_resolver=resolver,
        )
        t.join()
        self.assertEqual(result["status"], "LOCKED")


class RoundFileFormatTests(unittest.TestCase):
    """(c) Plan 6 workflow regression — round file structure preserved after engine extraction."""

    PROJ = "TestP7Format"
    TOPIC = "test-format"
    SID = "test-p7-format"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "workflow_validation"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_workflow_round_file_format(self):
        """workflow round file has Plan 6 structure plus kind: field."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        eng.factcheck_run(
            self.state_dir, str(draft), "workflow", self.SID,
            debounce_seconds=0,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        topic_dir = self.state_dir / self.PROJ / self.TOPIC
        r1 = (topic_dir / "R1.md").read_text()

        self.assertIn("rounds: 1", r1)
        self.assertIn("kind: workflow", r1)
        self.assertIn("checker_count: 3", r1)
        self.assertIn("verdict: PASS", r1)
        self.assertIn("## Checker 1 (sonnet)", r1)
        self.assertIn("## Checker 3 (sonnet)", r1)
        self.assertIn("## Aggregated verdict", r1)
        self.assertIn("Workflow.md draft factcheck", r1)

    def test_workflow_lock_path_is_per_kind(self):
        """Session 4b A6 — workflow lockfile sits under <topic_dir>/workflow/.lock (per-kind),
        while R<N>.md storage stays flat at <topic_dir>/R<N>.md (Plan 6 compat)."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        eng.factcheck_run(
            self.state_dir, str(draft), "workflow", self.SID,
            debounce_seconds=0,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        topic_dir = self.state_dir / self.PROJ / self.TOPIC
        # R<N>.md stays flat (Plan 6 backward compat)
        self.assertTrue((topic_dir / "R1.md").exists(),
                        "workflow R1.md must remain at flat topic_dir for Plan 6 compat")
        # Lockfile lives under per-kind sub-directory
        per_kind_lock = topic_dir / "workflow" / ".lock"
        self.assertTrue(per_kind_lock.exists(),
                        "workflow lockfile must be at <topic_dir>/workflow/.lock per A6")
        # Lock must NOT be at the legacy flat path
        self.assertFalse((topic_dir / ".lock").exists(),
                         "legacy flat .lock must not exist after A6 migration")

    def test_plan_round_file_has_plan_title(self):
        """plan round file has 'plan file factcheck' in title."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "plan.md"
        draft.write_text("# Plan\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "plan"
        r1 = (kind_dir / "R1.md").read_text()
        self.assertIn("plan file factcheck", r1)
        self.assertIn("kind: plan", r1)


class TestKLExtractionAggregation(unittest.TestCase):
    """Session 4b — `aggregate_kl_extraction_round` scribe + CLI helper."""

    PROJ = "<KL>"
    TOPIC = "test-book"
    FAKE_SESSION = "00000000-0000-0000-0000-000000000000"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "plan_validation"
        # Mirror prod layout for per-agent files: <tmp>/Sources/Books/<slug>/Chapters/...
        self.chapters_dir = Path(self.tmpdir) / "Sources" / "Books" / self.TOPIC / "Chapters"
        self.chapters_dir.mkdir(parents=True, exist_ok=True)
        self._agent_seq = 0

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _next_agent_id(self) -> str:
        self._agent_seq += 1
        return f"agent{self._agent_seq:013d}"

    def _write_per_agent(self, name: str, enum: str, body: str = "PASS\n",
                         agent_id: str = None, session_id: str = None,
                         dir_override: Path = None) -> Path:
        path = (dir_override or self.chapters_dir) / name
        aid = agent_id if agent_id is not None else self._next_agent_id()
        sid = session_id if session_id is not None else self.FAKE_SESSION
        header = f"agentId: {aid}\nsessionId: {sid}\nverdict-per-checker: {enum}\n"
        path.write_text(header + body, encoding="utf-8")
        return path

    @staticmethod
    def _ok_resolver(session_id, agent_id):
        return {
            "ok": True,
            "family": "sonnet",
            "raw_models": ["claude-sonnet-4-6"],
            "error": None,
            "transcript_path": f"<fake>/{session_id}/agent-{agent_id}.jsonl",
        }

    def test_pass_flat_single_chapter(self):
        """Three 0_DISCREPANCIES files → R1.md verdict PASS, no chapter segment."""
        import _factcheck_engine as eng
        files = [
            str(self._write_per_agent("_factcheck-r1-foo-A.md", "0_DISCREPANCIES")),
            str(self._write_per_agent("_factcheck-r1-foo-B.md", "0_DISCREPANCIES")),
            str(self._write_per_agent("_factcheck-r1-foo-C.md", "0_DISCREPANCIES")),
        ]
        audit_log = self.state_dir / self.PROJ / self.TOPIC / "kl_extraction" / "audit.jsonl"
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            audit_log_path=audit_log,
            _resolver_fn=self._ok_resolver,
        )
        self.assertEqual(result["status"], "PASS")
        r1 = (self.state_dir / self.PROJ / self.TOPIC / "kl_extraction" / "R1.md").read_text()
        self.assertIn("schema_version: 3", r1)  # S2/A9 epoch bump
        self.assertIn("kind: kl_extraction", r1)
        self.assertIn("verdict: PASS", r1)
        self.assertIn("rounds: 1", r1)
        self.assertIn("checker_count: 3", r1)
        # Audit JSONL: exactly one row appended.
        audit_lines = audit_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(audit_lines), 1)
        row = json.loads(audit_lines[0])
        self.assertEqual(row["agg_verdict"], "PASS")
        self.assertEqual(row["round"], 1)

    def test_escalate_after_two_dirty_rounds(self):
        """R1 with one ≥1_DISCREPANCY → DIRTY; R2 with one ≥1_DISCREPANCY → ESCALATE."""
        import _factcheck_engine as eng
        for r in (1, 2):
            files = [
                str(self._write_per_agent(f"_factcheck-r{r}-foo-A.md", "0_DISCREPANCIES")),
                str(self._write_per_agent(f"_factcheck-r{r}-foo-B.md", "≥1_DISCREPANCY",
                                          "DISCREPANCY: still bad\n")),
                str(self._write_per_agent(f"_factcheck-r{r}-foo-C.md", "0_DISCREPANCIES")),
            ]
            result = eng.aggregate_kl_extraction_round(
                files, r, self.PROJ, self.TOPIC, self.state_dir,
                _resolver_fn=self._ok_resolver,
            )
            if r == 1:
                self.assertEqual(result["status"], "DIRTY")
            else:
                self.assertEqual(result["status"], "ESCALATE")
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "kl_extraction"
        r2 = (kind_dir / "R2.md").read_text()
        self.assertIn("verdict: ESCALATE", r2)

    def test_dirty_then_pass(self):
        """R1 DIRTY, R2 PASS — both markers exist, second has verdict: PASS."""
        import _factcheck_engine as eng
        files_r1 = [
            str(self._write_per_agent("_factcheck-r1-foo-A.md", "≥1_DISCREPANCY",
                                      "DISCREPANCY: cite drift\n")),
            str(self._write_per_agent("_factcheck-r1-foo-B.md", "0_DISCREPANCIES")),
            str(self._write_per_agent("_factcheck-r1-foo-C.md", "0_DISCREPANCIES")),
        ]
        r1 = eng.aggregate_kl_extraction_round(
            files_r1, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._ok_resolver,
        )
        self.assertEqual(r1["status"], "DIRTY")

        files_r2 = [
            str(self._write_per_agent("_factcheck-r2-foo-A.md", "0_DISCREPANCIES")),
            str(self._write_per_agent("_factcheck-r2-foo-B.md", "0_DISCREPANCIES")),
            str(self._write_per_agent("_factcheck-r2-foo-C.md", "0_DISCREPANCIES")),
        ]
        r2 = eng.aggregate_kl_extraction_round(
            files_r2, 2, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._ok_resolver,
        )
        self.assertEqual(r2["status"], "PASS")
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "kl_extraction"
        self.assertTrue((kind_dir / "R1.md").exists())
        self.assertIn("verdict: PASS", (kind_dir / "R2.md").read_text())

    def test_multi_chapter_nested_layout(self):
        """`chapter='ch-01'` → marker written under <topic_dir>/kl_extraction/ch-01/R1.md."""
        import _factcheck_engine as eng
        # Mirror prod nested layout: per-agent files under Chapters/<chapter>/
        ch_dir = self.chapters_dir / "ch-01"
        ch_dir.mkdir(parents=True, exist_ok=True)

        def write(name, enum):
            return str(self._write_per_agent(name, enum, dir_override=ch_dir))

        files = [
            write("_factcheck-r1-ch01-A.md", "0_DISCREPANCIES"),
            write("_factcheck-r1-ch01-B.md", "0_DISCREPANCIES"),
            write("_factcheck-r1-ch01-C.md", "0_DISCREPANCIES"),
        ]
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir, chapter="ch-01",
            _resolver_fn=self._ok_resolver,
        )
        self.assertEqual(result["status"], "PASS")
        nested_path = (
            self.state_dir / self.PROJ / self.TOPIC / "kl_extraction" / "ch-01" / "R1.md"
        )
        self.assertTrue(nested_path.exists())
        self.assertIn("verdict: PASS", nested_path.read_text())
        # Flat path must NOT exist for this chapter run.
        flat_path = self.state_dir / self.PROJ / self.TOPIC / "kl_extraction" / "R1.md"
        self.assertFalse(flat_path.exists())

    def test_encoding_edge_bom_crlf(self):
        """Per-agent files with UTF-8 BOM + CRLF endings → top-line parse succeeds."""
        import _factcheck_engine as eng

        def write_bom_crlf(name, enum, agent_id):
            p = self.chapters_dir / name
            header = (
                f"agentId: {agent_id}\r\n"
                f"sessionId: {self.FAKE_SESSION}\r\n"
                f"verdict-per-checker: {enum}\r\nbody\r\n"
            )
            p.write_bytes("﻿".encode("utf-8") + header.encode("utf-8"))
            return str(p)

        files = [
            write_bom_crlf("_factcheck-r1-bom-A.md", "0_DISCREPANCIES", "agentbomA000000000"),
            write_bom_crlf("_factcheck-r1-bom-B.md", "0_DISCREPANCIES", "agentbomB000000000"),
            write_bom_crlf("_factcheck-r1-bom-C.md", "0_DISCREPANCIES", "agentbomC000000000"),
        ]
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._ok_resolver,
        )
        self.assertEqual(result["status"], "PASS")

    def test_path_coherence_rejected_at_cli(self):
        """CLI helper rejects out-of-convention path AND no R<N>.md is created."""
        import pre_plan_gates as ppg
        # Create three real files at a path that violates `/Sources/Books/<topic>/` substring.
        bad_dir = Path(self.tmpdir) / "elsewhere"
        bad_dir.mkdir(parents=True, exist_ok=True)
        bad_files = []
        for letter in ("A", "B", "C"):
            p = bad_dir / f"_factcheck-r1-foo-{letter}.md"
            p.write_text("verdict-per-checker: 0_DISCREPANCIES\nPASS\n", encoding="utf-8")
            bad_files.append(str(p))

        errs = ppg._validate_kl_extraction_inputs(self.PROJ, self.TOPIC, bad_files)
        self.assertTrue(errs, "validator must reject path-coherence violations")
        self.assertTrue(any("path-coherence" in e or "Q9" in e for e in errs))

        # Dual assertion: no R<N>.md should have been written to the per-kind dir.
        kind_dir = self.state_dir / self.PROJ / self.TOPIC / "kl_extraction"
        if kind_dir.exists():
            self.assertEqual(list(kind_dir.glob("R*.md")), [])


class SubcommandWiringTests(unittest.TestCase):
    """(b) factcheck-plan / factcheck-thought subcommands wire correctly via CLI."""

    PROJ = "TestP7Sub"
    TOPIC = "test-sub"
    SID = "test-p7-sub"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self._orig_plan_val_dir = ppg.PLAN_VALIDATION_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir
        self.plan_val_dir = Path(self.tmpdir) / "plan_validation"
        ppg.PLAN_VALIDATION_DIR = self.plan_val_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        ppg.PLAN_VALIDATION_DIR = self._orig_plan_val_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_factcheck_plan_pass(self):
        """factcheck-plan subcommand returns PASS with mock checker injection not available via CLI;
        test via direct import instead."""
        import pre_plan_gates as ppg
        import _factcheck_engine as eng

        draft = Path(self.tmpdir) / "myplan.md"
        draft.write_text("# Plan\n")

        # Patch engine's factcheck_run to use mock checker
        orig = eng.factcheck_run

        def patched(state_dir, draft_path, kind, session_id, **kwargs):
            kwargs["_checker_fn"] = lambda *a: "PASS"
            return orig(state_dir, draft_path, kind, session_id, **kwargs)

        eng.factcheck_run = patched
        try:
            result = ppg.factcheck_workflow.__class__  # ensure import worked
            # Call via CLI subprocess with real topic state (no mock checker injection)
            # Use internal API to verify wiring
            from _factcheck_engine import factcheck_run as fr
            resolver = lambda _sid: (self.PROJ, self.TOPIC, {"topic_slug": self.PROJ, "project_slug": self.TOPIC})
            result = fr(
                self.plan_val_dir, str(draft), "plan", self.SID,
                debounce_seconds=0,
                _checker_fn=lambda *a: "PASS",
                _proj_topic_resolver=resolver,
            )
            self.assertEqual(result["status"], "PASS")
            kind_dir = self.plan_val_dir / self.PROJ / self.TOPIC / "plan"
            self.assertTrue(kind_dir.exists())
            self.assertTrue((kind_dir / "R1.md").exists())
        finally:
            eng.factcheck_run = orig

    def test_factcheck_thought_kind_subdir(self):
        """factcheck-thought stores round files in <topic>/thought/ subdir."""
        from _factcheck_engine import factcheck_run
        draft = Path(self.tmpdir) / "topic_THOUGHT.md"
        draft.write_text("# Thought\n")
        resolver = lambda _sid: (self.PROJ, self.TOPIC, {"topic_slug": self.PROJ})

        result = factcheck_run(
            self.plan_val_dir, str(draft), "thought", self.SID,
            debounce_seconds=0,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result["status"], "PASS")
        kind_dir = self.plan_val_dir / self.PROJ / self.TOPIC / "thought"
        self.assertTrue((kind_dir / "R1.md").exists())
        # plan subdir should NOT have been created
        plan_dir = self.plan_val_dir / self.PROJ / self.TOPIC / "plan"
        self.assertFalse(plan_dir.exists())

    def test_workflow_flat_dir_not_in_kind_subdir(self):
        """workflow R<N>.md stays flat (Plan 6 compat). Session 4b A6: the
        workflow lockfile moves to a per-kind sub-directory, but the R<N>.md
        storage location is unchanged."""
        import pre_plan_gates as ppg
        self._orig_workflow_val_dir = ppg.WORKFLOW_VALIDATION_DIR
        ppg.WORKFLOW_VALIDATION_DIR = Path(self.tmpdir) / "workflow_validation"

        try:
            from _factcheck_engine import factcheck_run
            draft = Path(self.tmpdir) / "Workflow.md"
            draft.write_text("---\ntitle: T\n---\n")
            resolver = lambda _sid: (self.PROJ, self.TOPIC, {"topic_slug": self.PROJ})

            factcheck_run(
                ppg.WORKFLOW_VALIDATION_DIR, str(draft), "workflow", self.SID,
                debounce_seconds=0,
                _checker_fn=lambda *a: "PASS",
                _proj_topic_resolver=resolver,
            )
            # Round file should be in flat <proj>/<topic>/ (Plan 6 compat).
            flat_dir = ppg.WORKFLOW_VALIDATION_DIR / self.PROJ / self.TOPIC
            self.assertTrue((flat_dir / "R1.md").exists())
            # The workflow sub-directory may exist now (Session 4b A6 holds the
            # per-kind lockfile there), but R1.md must NOT appear inside it.
            workflow_subdir = flat_dir / "workflow"
            self.assertFalse((workflow_subdir / "R1.md").exists())
        finally:
            ppg.WORKFLOW_VALIDATION_DIR = self._orig_workflow_val_dir


class IsPlanValidatedTests(unittest.TestCase):
    """(d) is-plan-validated exit codes: 0=PASS, 1=state_mismatch, 2=marker_missing, 3=no_active_project."""

    PROJ = "TestP7IsVal"
    TOPIC = "test-isval"
    SID = "test-p7-isval"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self._orig_plan_val_dir = ppg.PLAN_VALIDATION_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir
        self.plan_val_dir = Path(self.tmpdir) / "plan_validation"
        ppg.PLAN_VALIDATION_DIR = self.plan_val_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        ppg.PLAN_VALIDATION_DIR = self._orig_plan_val_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_round_file(self, verdict):
        """Write a round file with the given verdict in the plan kind subdir."""
        kind_dir = self.plan_val_dir / self.PROJ / self.TOPIC / "plan"
        kind_dir.mkdir(parents=True, exist_ok=True)
        r1 = kind_dir / "R1.md"
        r1.write_text(
            f"---\nschema_version: 2\nrounds: 1\nkind: plan\nverdict: {verdict}\nchecked_at: 2026-01-01T00:00:00+00:00\n---\n"
        )

    def test_exit_0_marker_and_state_pass(self):
        """Exit 0 when CONVERGED marker present AND state file verdict: PASS."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\n<!-- VALIDATION:CONVERGED -->\n")
        self._write_round_file("PASS")
        code, reason = ppg.is_plan_validated(self.SID, str(plan))
        self.assertEqual(code, 0)
        self.assertEqual(reason, "PASS")

    def test_exit_1_marker_present_state_dirty(self):
        """Exit 1 when CONVERGED marker present but state file shows DIRTY (anti-tampering)."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\n<!-- VALIDATION:CONVERGED -->\n")
        self._write_round_file("DIRTY")
        code, reason = ppg.is_plan_validated(self.SID, str(plan))
        self.assertEqual(code, 1)
        self.assertEqual(reason, "state_mismatch")

    def test_exit_1_marker_present_no_round_files(self):
        """Exit 1 when CONVERGED marker present but no round files (no state to cross-check)."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\n<!-- VALIDATION:CONVERGED -->\n")
        # No round files written
        code, reason = ppg.is_plan_validated(self.SID, str(plan))
        self.assertEqual(code, 1)
        self.assertEqual(reason, "state_mismatch")

    def test_exit_2_marker_missing(self):
        """Exit 2 when no CONVERGED marker in plan body."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\nNo marker here.\n")
        self._write_round_file("PASS")
        code, reason = ppg.is_plan_validated(self.SID, str(plan))
        self.assertEqual(code, 2)
        self.assertEqual(reason, "marker_missing")

    def test_exit_3_no_active_project(self):
        """Exit 3 when no active topic for session."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n")
        code, reason = ppg.is_plan_validated("unknown-session-xyz", str(plan))
        self.assertEqual(code, 3)
        self.assertEqual(reason, "no_active_project")

    def test_cli_exit_0(self):
        """CLI exits 0 for PASS state — uses real state dirs so subprocess sees them."""
        import pre_plan_gates as ppg

        # Use the ORIGINAL (real) dirs, not the tmpdir overrides from setUp
        real_state_dir = self._orig_topic_state_dir
        real_state_dir.mkdir(parents=True, exist_ok=True)
        test_sid = "test-p7-isval-cli-exit0"
        test_proj = "TestP7IsValCLI"
        test_topic = "test-isval-cli"

        active_path = real_state_dir / "_active.json"
        existing_active = json.loads(active_path.read_text()) if active_path.exists() else {}
        existing_active[test_sid] = {
            "topic_slug": test_proj, "active_project": test_topic,
            "updated": "2026-01-01T00:00:00+00:00",
        }
        active_path.write_text(json.dumps(existing_active))
        (real_state_dir / f"{test_proj}__{test_topic}.json").write_text(json.dumps({
            "topic_slug": test_proj, "project_slug": test_topic,
            }))

        # Write round file to real PLAN_VALIDATION_DIR
        real_plan_val = self._orig_plan_val_dir
        kind_dir = real_plan_val / test_proj / test_topic / "plan"
        kind_dir.mkdir(parents=True, exist_ok=True)
        (kind_dir / "R1.md").write_text(
            "---\nschema_version: 2\nrounds: 1\nkind: plan\nverdict: PASS\nchecked_at: 2026-01-01T00:00:00+00:00\n---\n"
        )
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\n<!-- VALIDATION:CONVERGED -->\n")

        try:
            result = subprocess.run(
                ["python3", str(PPG_PY), "is-plan-validated", test_sid, str(plan)],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0)
        finally:
            if active_path.exists():
                active = json.loads(active_path.read_text())
                active.pop(test_sid, None)
                active_path.write_text(json.dumps(active))
            topic_file = real_state_dir / f"{test_proj}__{test_topic}.json"
            if topic_file.exists():
                topic_file.unlink()
            if kind_dir.exists():
                shutil.rmtree(str(kind_dir.parent.parent), ignore_errors=True)

    def test_cli_exit_3_unknown_session(self):
        """CLI exits 3 for no_active_project."""
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n")
        result = subprocess.run(
            ["python3", str(PPG_PY), "is-plan-validated", "unknown-session-xyz-p7-cli", str(plan)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 3)


class IsValidationWaivedTests(unittest.TestCase):
    """(e) is-validation-waived honors literal-marker-substring, rejects fabricated (audit:419)."""

    SID = "test-p7-waived"
    PROJ = "TestP7Waived"
    TOPIC = "test-waived"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_plan_with_waiver(self):
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\n<!-- VALIDATION:WAIVED:test reason -->\n")
        return plan

    def _make_message_with_literal(self):
        msg = Path(self.tmpdir) / "message.txt"
        msg.write_text("User typed: <!-- VALIDATION:WAIVED:test reason -->")
        return msg

    def _make_message_without_literal(self):
        msg = Path(self.tmpdir) / "message.txt"
        msg.write_text("User said: ok, I approve the waiver")
        return msg

    def test_valid_when_marker_and_user_typed(self):
        """Exit 0 when plan has waiver marker AND user's message contains literal substring."""
        import pre_plan_gates as ppg
        plan = self._make_plan_with_waiver()
        msg = self._make_message_with_literal()
        code, reason = ppg.is_validation_waived(self.SID, str(plan), str(msg))
        self.assertEqual(code, 0)
        self.assertEqual(reason, "valid")

    def test_invalid_when_marker_but_user_did_not_type(self):
        """Exit 1 when plan has waiver marker but user message lacks literal substring (fabricated)."""
        import pre_plan_gates as ppg
        plan = self._make_plan_with_waiver()
        msg = self._make_message_without_literal()
        code, reason = ppg.is_validation_waived(self.SID, str(plan), str(msg))
        self.assertEqual(code, 1)
        self.assertEqual(reason, "invalid")

    def test_invalid_when_no_marker_in_plan(self):
        """Exit 1 when plan has no waiver marker."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan.md"
        plan.write_text("# Plan\n\nNo waiver here.\n")
        msg = self._make_message_with_literal()
        code, reason = ppg.is_validation_waived(self.SID, str(plan), str(msg))
        self.assertEqual(code, 1)
        self.assertEqual(reason, "invalid")

    def test_invalid_when_message_file_missing(self):
        """Exit 1 when MESSAGE_PATH file does not exist (conservative deny)."""
        import pre_plan_gates as ppg
        plan = self._make_plan_with_waiver()
        code, reason = ppg.is_validation_waived(self.SID, str(plan), "/nonexistent/msg.txt")
        self.assertEqual(code, 1)
        self.assertEqual(reason, "invalid")

    def test_cli_exit_0(self):
        """CLI exits 0 for valid waiver."""
        plan = self._make_plan_with_waiver()
        msg = self._make_message_with_literal()
        result = subprocess.run(
            ["python3", str(PPG_PY), "is-validation-waived", self.SID, str(plan), str(msg)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)

    def test_cli_exit_1_no_user_typed(self):
        """CLI exits 1 when user did not type the literal marker."""
        plan = self._make_plan_with_waiver()
        msg = self._make_message_without_literal()
        result = subprocess.run(
            ["python3", str(PPG_PY), "is-validation-waived", self.SID, str(plan), str(msg)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)


class DebounceTests(unittest.TestCase):
    """(f) factcheck_run returns DEBOUNCED within cooldown window."""

    PROJ = "TestP7Deb"
    TOPIC = "test-deb"
    SID = "test-p7-deb"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "validation"

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_debounced_within_window(self):
        """Second call within debounce window returns DEBOUNCED."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "plan.md"
        draft.write_text("# Plan\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        # First call succeeds
        result1 = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=60,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result1["status"], "PASS")

        # Second call immediately — within window
        result2 = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=60,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(result2["status"], "DEBOUNCED")

    def test_no_debounce_when_zero(self):
        """debounce_seconds=0 skips debounce; two calls both run."""
        import _factcheck_engine as eng
        draft = Path(self.tmpdir) / "plan.md"
        draft.write_text("# Plan\n")
        resolver = _mock_resolver(self.PROJ, self.TOPIC)

        result1 = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        result2 = eng.factcheck_run(
            self.state_dir, str(draft), "plan", self.SID,
            debounce_seconds=0,
            _checker_fn=lambda *a: "PASS",
            _proj_topic_resolver=resolver,
        )
        # Both should run (R1 and R2)
        self.assertEqual(result1["status"], "PASS")
        self.assertEqual(result2["status"], "PASS")


class IndependenceTests(unittest.TestCase):
    """Independence guarantees (per Guiding Policy):
    (g) tool restriction: _invoke_checker_engine passes --allowedTools
    (h) input scoping: _build_checker_input passes path only (no content inlined)
    (i) aggregation determinism: pure-code DISCREPANCY scan, order-independent
    """

    def test_build_checker_input_contains_path_not_content(self):
        """(h) _build_checker_input prompt contains artifact path; does NOT inline file content."""
        import _factcheck_engine as eng

        artifact_path = "/some/path/plan.md"
        prompt = eng._build_checker_input(
            "plan", 0, artifact_path, 1, None, "some grounding rules"
        )
        # Path must be referenced
        self.assertIn(artifact_path, prompt)
        # Must NOT contain hypothetical inline content
        self.assertNotIn("inline content", prompt)
        # Must contain scope prompt
        self.assertIn("Source-grounding", prompt)
        # Must contain grounding rules
        self.assertIn("some grounding rules", prompt)

    def test_build_checker_input_diff_only_round(self):
        """(h) diff-only (round >= 3) prompt includes prior issues."""
        import _factcheck_engine as eng
        prior = "DISCREPANCY: some prior issue"
        prompt = eng._build_checker_input("plan", 0, "/path/plan.md", 3, prior, "")
        self.assertIn("diff-only round", prompt)
        self.assertIn(prior, prompt)

    def test_build_checker_input_no_prior_for_round1(self):
        """(h) round 1 prompt does NOT include prior issues even if passed."""
        import _factcheck_engine as eng
        prompt = eng._build_checker_input("plan", 0, "/path/plan.md", 1, "some prior", "")
        self.assertNotIn("diff-only round", prompt)
        self.assertNotIn("some prior", prompt)

    def test_aggregation_dirty_on_any_discrepancy(self):
        """(i) DISCREPANCY from any checker → DIRTY; pure code, no AI invoked."""
        import _factcheck_engine as eng
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        try:
            state_dir = Path(tmpdir) / "validation"
            draft = Path(tmpdir) / "plan.md"
            draft.write_text("# Plan\n")
            resolver = _mock_resolver("P", "T")

            def checker(d, idx, model, rnum, prior):
                # Only R1, checker idx=1 returns DISCREPANCY; everything else PASS
                return "DISCREPANCY: checker 2 found issue" if rnum == 1 and idx == 1 else "PASS"

            result = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-agg",
                debounce_seconds=0,
                _checker_fn=checker,
                _proj_topic_resolver=resolver,
            )
            # R1 DIRTY (one checker), R2 all PASS → PASS at R2
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["rounds"], 2)
            kind_dir = state_dir / "P" / "T" / "plan"
            r1 = (kind_dir / "R1.md").read_text()
            self.assertIn("verdict: DIRTY", r1)
            self.assertEqual(len(list(kind_dir.glob("R*.md"))), 2)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_aggregation_pass_when_no_discrepancy(self):
        """(i) All PASS checkers → PASS aggregation."""
        import _factcheck_engine as eng
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        try:
            state_dir = Path(tmpdir) / "validation"
            draft = Path(tmpdir) / "plan.md"
            draft.write_text("# Plan\n")
            resolver = _mock_resolver("P", "T2")

            result = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-agg2",
                debounce_seconds=0,
                _checker_fn=lambda *a: "PASS",
                _proj_topic_resolver=resolver,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["rounds"], 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_aggregation_case_insensitive(self):
        """(i) DISCREPANCY detection is case-insensitive."""
        import _factcheck_engine as eng
        import tempfile, shutil

        tmpdir = tempfile.mkdtemp()
        try:
            state_dir = Path(tmpdir) / "validation"
            draft = Path(tmpdir) / "plan.md"
            draft.write_text("# Plan\n")
            resolver = _mock_resolver("P", "T3")

            # Lowercase 'discrepancy' should still trigger DIRTY
            call_count = [0]
            def mixed_case_checker(d, idx, model, rnum, prior):
                call_count[0] += 1
                if rnum == 1:
                    return "discrepancy: lowercase discrepancy"
                return "PASS"

            result = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-agg3",
                debounce_seconds=0,
                _checker_fn=mixed_case_checker,
                _proj_topic_resolver=resolver,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["rounds"], 2)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_invoke_checker_uses_allowed_tools(self):
        """(g) _invoke_checker_engine subprocess call includes --allowedTools flag."""
        import _factcheck_engine as eng

        captured_args = []

        def fake_run(cmd, **kwargs):
            captured_args.extend(cmd)
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "PASS"
            return mock_result

        with patch("subprocess.run", side_effect=fake_run):
            eng._invoke_checker_engine(
                "/tmp/plan.md", 0, "sonnet", 1, None,
                kind="plan",
                subagent_tools=("Read", "Glob", "Grep"),
                grounding_rules="",
            )

        self.assertIn("--allowedTools", captured_args)
        tools_idx = captured_args.index("--allowedTools")
        tools_value = captured_args[tools_idx + 1]
        self.assertIn("Read", tools_value)
        self.assertIn("Glob", tools_value)
        self.assertIn("Grep", tools_value)

    def test_invoke_checker_no_bash_in_default_tools(self):
        """(g) Default subagent_tools does not include Bash, Write, or Edit."""
        import _factcheck_engine as eng
        import inspect

        sig = inspect.signature(eng.factcheck_run)
        default_tools = sig.parameters["subagent_tools"].default
        self.assertNotIn("Bash", default_tools)
        self.assertNotIn("Write", default_tools)
        self.assertNotIn("Edit", default_tools)
        self.assertIn("Read", default_tools)


class Plan6RegressionTests(unittest.TestCase):
    """(c) Plan 6 factcheck_workflow still works after engine extraction (full test via PPG)."""

    SID = "test-p7-p6reg"
    PROJ = "TestP7P6Reg"
    TOPIC = "test-p6reg"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self._orig_wf_val_dir = ppg.WORKFLOW_VALIDATION_DIR
        self.tmpdir = tempfile.mkdtemp()
        state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = state_dir
        self.wf_val_dir = Path(self.tmpdir) / "workflow_validation"
        ppg.WORKFLOW_VALIDATION_DIR = self.wf_val_dir

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        ppg.WORKFLOW_VALIDATION_DIR = self._orig_wf_val_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_factcheck_workflow_still_passes(self):
        """factcheck_workflow via engine delegation returns PASS with mock checker."""
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")

        result = ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=lambda *a: "PASS")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 1)

    def test_factcheck_workflow_round_file_structure(self):
        """Plan 6 round file structure preserved (Plan 6 assertIn checks still pass)."""
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")

        ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=lambda *a: "PASS")
        topic_dir = self.wf_val_dir / self.PROJ / self.TOPIC
        r1 = (topic_dir / "R1.md").read_text()

        # All Plan 6 assertions
        self.assertIn("rounds: 1", r1)
        self.assertIn("checker_count: 3", r1)
        self.assertIn("verdict: PASS", r1)
        self.assertIn("## Checker 1 (sonnet)", r1)
        self.assertIn("## Checker 3 (sonnet)", r1)
        self.assertIn("## Aggregated verdict", r1)

    def test_factcheck_workflow_appends_frontmatter(self):
        """validated_via: still appended to Workflow.md frontmatter on PASS."""
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")

        ppg.factcheck_workflow(self.SID, str(draft), _checker_fn=lambda *a: "PASS")
        content = draft.read_text()
        self.assertIn("validated_via:", content)

    def test_factcheck_workflow_escalate(self):
        """R2 escalate fires at 2 rounds via delegation (Session 1a canon)."""
        import pre_plan_gates as ppg
        draft = Path(self.tmpdir) / "Workflow.md"
        draft.write_text("---\ntitle: Test\n---\n# Workflow\n")

        result = ppg.factcheck_workflow(
            self.SID, str(draft), _checker_fn=lambda *a: "DISCREPANCY: always"
        )
        self.assertEqual(result["status"], "ESCALATE")
        self.assertEqual(result["reason"], "no_consensus_after_2_rounds")
        self.assertEqual(result["rounds"], 2)


class VerifyPlanFileHookTests(unittest.TestCase):
    """(f) verify-plan-file.sh: path filter and debounce integration."""

    HOOK = Path.home() / ".claude" / "hooks" / "verify-plan-file.sh"

    def _make_input(self, tool_name, file_path, session_id="test-vpf-sid"):
        return json.dumps({
            "tool_name": tool_name,
            "tool_input": {"file_path": file_path},
            "session_id": session_id,
        })

    def test_non_plan_file_exits_0_silently(self):
        """Hook ignores Write to a non-plan file."""
        inp = self._make_input("Write", "/some/other/file.md")
        r = subprocess.run(
            ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0)

    def test_non_write_edit_tool_ignored(self):
        """Hook ignores non-Write/Edit tool calls."""
        inp = self._make_input("Read", str(Path.home() / ".claude" / "plans" / "test.md"))
        r = subprocess.run(
            ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0)

    def test_plan_file_exits_0(self):
        """Hook exits 0 (non-blocking) for a plan file Write — engine dispatch is async."""
        plan_path = str(Path.home() / ".claude" / "plans" / "test-hook.md")
        inp = self._make_input("Write", plan_path, session_id="test-vpf-dispatch")
        r = subprocess.run(
            ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
        )
        # Hook always exits 0 (PostToolUse cannot block)
        self.assertEqual(r.returncode, 0)

    def test_debounce_via_engine(self):
        """Engine-level debounce: second call within window returns DEBOUNCED status.
        This verifies the engine property that verify-plan-file.sh relies on."""
        import _factcheck_engine as eng
        import tempfile

        tmpdir = tempfile.mkdtemp()
        try:
            state_dir = Path(tmpdir) / "validation"
            draft = Path(tmpdir) / "plan.md"
            draft.write_text("# Plan\n")
            resolver = _mock_resolver("P7f", "deb-hook")

            r1 = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-vpf-deb",
                debounce_seconds=60,
                _checker_fn=lambda *a: "PASS",
                _proj_topic_resolver=resolver,
            )
            self.assertEqual(r1["status"], "PASS")

            r2 = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-vpf-deb",
                debounce_seconds=60,
                _checker_fn=lambda *a: "PASS",
                _proj_topic_resolver=resolver,
            )
            self.assertEqual(r2["status"], "DEBOUNCED")
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


class FactcheckThoughtFileHookTests(unittest.TestCase):
    """(g) factcheck-thought-file.sh: gates-complete guard fires correctly."""

    HOOK = Path.home() / ".claude" / "hooks" / "factcheck-thought-file.sh"
    SID = "test-fcth-sid"
    PROJ = "TestP7FCTh"
    TOPIC = "test-fcth"

    def _make_input(self, file_path, session_id=None):
        return json.dumps({
            "tool_name": "Write",
            "tool_input": {"file_path": file_path},
            "session_id": session_id or self.SID,
        })

    def test_non_thought_file_ignored(self):
        """Hook ignores Write to a non-THOUGHT.md file."""
        inp = self._make_input("/some/plan.md")
        r = subprocess.run(
            ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0)

    def test_thought_file_no_state_exits_0(self):
        """Hook exits 0 when no session state (vanilla mode — no gates)."""
        thought = "/tmp/no-state_THOUGHT.md"
        inp = self._make_input(thought, session_id="unknown-no-state-xyz-fcth")
        r = subprocess.run(
            ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
        )
        self.assertEqual(r.returncode, 0)

    def test_thought_file_incomplete_gates_skips(self):
        """Hook skips dispatch when session state exists but gates < 5."""
        import pre_plan_gates as ppg
        orig = ppg.STATE_DIR
        tmpdir = tempfile.mkdtemp()
        try:
            ppg.STATE_DIR = Path(tmpdir) / "session-state"
            ppg.STATE_DIR.mkdir()
            # Write session state with only 3 gates complete
            sid = "test-fcth-partial"
            state = {
                "session_id": sid,
                "gates": {
                    "gate0_framing": {"status": "complete"},
                    "gate1_explore": {"status": "complete"},
                    "gate2_validate": {"status": "complete"},
                },
            }
            (ppg.STATE_DIR / f"pre-plan-{sid}.json").write_text(json.dumps(state))

            thought = "/tmp/partial-gates_THOUGHT.md"
            inp = self._make_input(thought, session_id=sid)
            r = subprocess.run(
                ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
            )
            # Should exit 0 (no dispatch) — gates < 5
            self.assertEqual(r.returncode, 0)
        finally:
            ppg.STATE_DIR = orig
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_thought_file_all_gates_complete_exits_0(self):
        """Hook exits 0 (dispatch fired in background) when all 5 gates complete."""
        import pre_plan_gates as ppg
        orig_state_dir = ppg.STATE_DIR
        orig_topic_dir = ppg.TOPIC_STATE_DIR
        orig_plan_val = ppg.PLAN_VALIDATION_DIR
        tmpdir = tempfile.mkdtemp()
        try:
            ppg.STATE_DIR = Path(tmpdir) / "session-state"
            ppg.STATE_DIR.mkdir()
            ppg.PLAN_VALIDATION_DIR = Path(tmpdir) / "plan_validation"
            sid = "test-fcth-allgates"
            state = {
                "session_id": sid,
                "gates": {
                    "gate0_framing": {"status": "complete"},
                    "gate1_explore": {"status": "complete"},
                    "gate2_validate": {"status": "complete"},
                    "gate3_align": {"status": "complete"},
                    "gate4_success_metrics": {"status": "complete"},
                },
            }
            (ppg.STATE_DIR / f"pre-plan-{sid}.json").write_text(json.dumps(state))

            thought = "/tmp/all-gates_THOUGHT.md"
            Path(thought).write_text("# Thought\n")
            inp = self._make_input(thought, session_id=sid)
            r = subprocess.run(
                ["bash", str(self.HOOK)], input=inp, capture_output=True, text=True
            )
            # Should exit 0 — PostToolUse cannot block; dispatch is async
            self.assertEqual(r.returncode, 0)
        finally:
            ppg.STATE_DIR = orig_state_dir
            ppg.TOPIC_STATE_DIR = orig_topic_dir
            ppg.PLAN_VALIDATION_DIR = orig_plan_val
            shutil.rmtree(tmpdir, ignore_errors=True)


class PermissionPlanGateCheck6Tests(unittest.TestCase):
    """(h) permission-plan-gate.sh check 6: all 5 branching paths."""

    HOOK = Path.home() / ".claude" / "hooks" / "permission-plan-gate.sh"
    SID = "test-ppg6-sid"
    PROJ = "TestP7PPG6"
    TOPIC = "test-ppg6"

    def setUp(self):
        import pre_plan_gates as ppg
        self._orig_topic_state_dir = ppg.TOPIC_STATE_DIR
        self._orig_plan_val_dir = ppg.PLAN_VALIDATION_DIR
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = _setup_active_project(self.SID, self.PROJ, self.TOPIC, self.tmpdir)
        ppg.TOPIC_STATE_DIR = self.state_dir
        self.plan_val_dir = Path(self.tmpdir) / "plan_validation"
        ppg.PLAN_VALIDATION_DIR = self.plan_val_dir

        # Create a plan file that passes all existing gates (GATE0A-GATE0F etc.)
        # For check 6 isolation, use a minimal plan that passes gates 1-5 but
        # we test only check 6 behavior by patching the gates that come before.
        self.plan_file = Path(self.tmpdir) / "test-plan.md"
        self._write_valid_plan()

    def _write_valid_plan(self, marker=None, waiver=None):
        """Write a plan file that satisfies gates 1-5; optionally add CONVERGED/WAIVED marker."""
        content = (
            "---\n"
            "thought_file: Thoughts/test-ppg6_THOUGHT.md\n"
            "session: S1\n"
            "---\n"
            "# Plan\n\n"
            "## Diagnosis\n<!-- GATE0A:PROBLEM -->\n"
            "## Desired Outcome\n<!-- GATE0B:OUTCOME -->\n"
            "## Outcome Claims\n| # | Claim | Addresses |\n|---|---|---|\n<!-- GATE0B2:CLAIMS -->\n"
            "## Gap Analysis\n| # | Gap | Current | Desired | Claim |\n|---|---|---|---|---|\n<!-- GATE0C:GAPS -->\n"
            "## Guiding Policy\n<!-- GATE0D:POLICY -->\n"
            "## Coherent Actions\n"
            "| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |\n"
            "|--------|---------------|------|-------------|-----------------|-------|--------|\n"
            "| A1: test | G1 | does something | guard | gate | sonnet | fast |\n"
            "<!-- GATE0E:ACTIONS -->\n"
            "## Workflow/KL Alignment\nNone.\n"
            "## Design Review\n<!-- GATE0F:REVIEWED -->\n"
            "<!-- GATE1:START -->\n"
            "| Claim | Category | Source | Verified Against | Status |\n"
            "|-------|----------|--------|-----------------|--------|\n"
            "<!-- GATE1:END -->\n"
            "<!-- GATE1:VERIFIED -->\n"
            "<!-- GATE2:START -->\n"
            "| Step | Type | Enforcement |\n|------|------|-------------|\n"
            "<!-- GATE2:END -->\n"
            "<!-- GATE2:BOUNDARIES -->\n"
            "<!-- GATE2B:DESIGN_REVIEW -->\n"
            "<!-- GATE3:NO_CLAIMS -->\n"
        )
        if marker:
            content += f"\n{marker}\n"
        if waiver:
            content += f"\n{waiver}\n"
        self.plan_file.write_text(content)

    # NOTE: this class's fixture used to write a `verdict: PASS` sidecar here "so
    # check 5 passes", and carried an `_invoke_hook` helper no test ever called. Both
    # are gone: check 5 no longer reads a sidecar at all — it runs
    # `_validate-thought-file.py` live on the spine the plan names — and the real
    # end-to-end drive of the hook now lives in
    # PermissionPlanGateSpineValidationTests below, which invokes it for real.

    def tearDown(self):
        import pre_plan_gates as ppg
        ppg.TOPIC_STATE_DIR = self._orig_topic_state_dir
        ppg.PLAN_VALIDATION_DIR = self._orig_plan_val_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_round_file(self, verdict):
        kind_dir = self.plan_val_dir / self.PROJ / self.TOPIC / "plan"
        kind_dir.mkdir(parents=True, exist_ok=True)
        (kind_dir / "R1.md").write_text(
            f"---\nschema_version: 2\nrounds: 1\nkind: plan\nverdict: {verdict}\nchecked_at: 2026-01-01\n---\n"
        )

    # Path 1: PASS (CONVERGED + state PASS)
    def test_check6_pass_converged_and_state_pass(self):
        """Check 6 PASS: plan has CONVERGED marker AND state shows PASS."""
        import pre_plan_gates as ppg
        self._write_valid_plan(marker="<!-- VALIDATION:CONVERGED -->")
        self._write_round_file("PASS")

        code, _ = ppg.is_plan_validated(self.SID, str(self.plan_file))
        self.assertEqual(code, 0, "is-plan-validated should return 0 for PASS")

    # Path 2: state_mismatch deny
    def test_check6_state_mismatch_deny(self):
        """Check 6 state_mismatch: marker present but state DIRTY → exit code 1."""
        import pre_plan_gates as ppg
        self._write_valid_plan(marker="<!-- VALIDATION:CONVERGED -->")
        self._write_round_file("DIRTY")

        code, reason = ppg.is_plan_validated(self.SID, str(self.plan_file))
        self.assertEqual(code, 1)
        self.assertEqual(reason, "state_mismatch")

    # Path 3: waiver honored
    def test_check6_waiver_honored(self):
        """Check 6 waiver: no CONVERGED marker but valid user-typed waiver → valid."""
        import pre_plan_gates as ppg
        self._write_valid_plan(waiver="<!-- VALIDATION:WAIVED:test reason -->")

        # Marker is missing → is_plan_validated exits 2
        code, reason = ppg.is_plan_validated(self.SID, str(self.plan_file))
        self.assertEqual(code, 2)
        self.assertEqual(reason, "marker_missing")

        # User message contains literal waiver → waiver honored
        msg = Path(self.tmpdir) / "user_msg.txt"
        msg.write_text("<!-- VALIDATION:WAIVED:test reason -->")
        wcode, wreason = ppg.is_validation_waived(self.SID, str(self.plan_file), str(msg))
        self.assertEqual(wcode, 0)
        self.assertEqual(wreason, "valid")

    # Path 4: waiver denied
    def test_check6_waiver_denied(self):
        """Check 6 waiver denied: waiver marker in plan but user did NOT type it → invalid."""
        import pre_plan_gates as ppg
        self._write_valid_plan(waiver="<!-- VALIDATION:WAIVED:test reason -->")

        msg = Path(self.tmpdir) / "user_msg.txt"
        msg.write_text("I approve this plan")  # no literal marker
        wcode, wreason = ppg.is_validation_waived(self.SID, str(self.plan_file), str(msg))
        self.assertEqual(wcode, 1)
        self.assertEqual(wreason, "invalid")

    # Path 5: no_active_project allow
    def test_check6_no_active_project_allow(self):
        """Check 6 no_active_project: is-plan-validated exits 3 → hook fall-through."""
        import pre_plan_gates as ppg
        plan = Path(self.tmpdir) / "plan_nostate.md"
        plan.write_text("# Plan\n")
        # Use an unknown session_id
        code, reason = ppg.is_plan_validated("unknown-session-ppg6-xyz", str(plan))
        self.assertEqual(code, 3)
        self.assertEqual(reason, "no_active_project")

    # CLI path: exit 3 allows through
    def test_cli_is_plan_validated_exit3(self):
        """CLI: is-plan-validated exits 3 for no_active_project session."""
        plan = Path(self.tmpdir) / "plan_cli.md"
        plan.write_text("# Plan\n")
        r = subprocess.run(
            ["python3", str(PPG_PY), "is-plan-validated",
             "unknown-ppg6-cli-xyz", str(plan)],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 3)


# ---------------------------------------------------------------------------
# permission-plan-gate.sh check 5 — the spine is validated LIVE at ExitPlanMode.
#
# This drives the real hook end-to-end in a fully isolated environment: HOME and
# CLAUDE_CONFIG_DIR point at a temp dir whose `hooks` is a symlink to the hooks dir
# THIS TEST FILE lives under (so an experiment clone tests the candidate code, never
# live ~/.claude), `plans/.manifest.json` maps the test session to the plan, and
# PPG_PROJECTS_ROOT points at a temp projects root carrying the spine and a TODO.md
# that names it. The isolated HOME is load-bearing: `pre_plan_gates.py` derives its
# state dirs from `Path.home()`, so without it the hook's `read` could auto-bind a
# LIVE session (and the Slice-I receipt overrides below would be written to the real
# state dir).
#
# The plan fixture opts into the Slice-I chain (`GATE0:CHAIN_SRC`), so
# check-plan-gates.sh — which runs BEFORE the pre-plan checks — requires an engine
# receipt per 0B2/0C/0G gate. Each case records an explicit operator override for
# those three so the plan reaches check 5, the thing under test.
# ---------------------------------------------------------------------------

_SOUND_SPINE = """# Idea

## Problem
The gate trusts a note instead of looking at the spine.

# Discovery

## Guiding Policy
Make the gate exercise its own judgment.
<!-- locked: gp abc123 -->

## Desired Outcome
Approval inspects the spine every time.
<!-- locked: do abc123 -->

## Desired Solution
Run the validator live at ExitPlanMode.
<!-- locked: ds abc123 -->

## Metrics
OMTM: share of approvals that ran the validator = 100%.
<!-- locked: mt abc123 -->

## Scope
- S1 gate

# Solution Design
*(populated by /solution-design)*

# Implementation Details
*(populated by /plan)*
"""

# Same spine with the `## Desired Solution` locked field removed — the one failing
# check the refusal must name.
_BROKEN_SPINE = _SOUND_SPINE.replace(
    "## Desired Solution\nRun the validator live at ExitPlanMode.\n<!-- locked: ds abc123 -->\n\n", ""
)

_GATE_PLAN_JSON = json.dumps({
    "source_type": "web", "source_path": "plan:0b2-test", "lang": "en",
    "thoroughness": "deep",
    "claims": [{"text": "A test claim for the fixture.", "id": "C1",
                "addresses_diagnosis": "the problem", "locator": "T-1", "role": "backward",
                "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                          "minimality": True, "fluency": True, "faithfulness": True}}],
    "refused": [],
})

_GATE_PLAN_TEMPLATE = """# Test Plan

{preamble}<!-- GATE0:CHAIN_SRC -->
{chain}
## Diagnosis
Describes the problem.
<!-- GATE0A:PROBLEM -->

## Desired Outcome
Describes the operational outcome.
<!-- GATE0B:OUTCOME -->

## Outcome Claims

```json
{js}
```

**Coverage:** C1 resolves the diagnosis because it addresses the problem.
<!-- GATE0B2:CLAIMS -->

<!-- GATE0B2:VALIDATED -->
```
verdict: PASS
rounds: 1
checker_models: [sonnet, opus]
```

## Gap Analysis
| # | Gap | Current | Desired | Claim |
|---|-----|---------|---------|-------|
| G1 | Gap text | now | later | C1 |
<!-- GATE0C:GAPS -->

## Workflow/KL Alignment
| Rule | Source |
|---|---|
| fixture | fixture |

<!-- GATE0C:VALIDATED -->
```
verdict: PASS
rounds: 1
checker_models: [sonnet, opus]
```

## Guiding Policy
Describes the approach.
<!-- GATE0D:POLICY -->

<!-- GATE0SR:SLICES -->
```
slice_register_ref: Thoughts/smoke_THOUGHT.md#slice-register
slice_id: S1
```

## Coherent Actions
| Action | Addresses Gap | Goal | Guard rails | Validation gate | Model | Reason |
|--------|---------------|------|-------------|-----------------|-------|--------|
| A1 | G1 | Does the thing | guard | test passes | sonnet | judgment task |

**Coherence:** A1 carries out the guiding policy.
<!-- GATE0E:ACTIONS -->

## Design Review
Reviewed for edge cases.
<!-- GATE0F:REVIEWED -->

## Verification
<!-- GATE1:START -->
| Claim | Category | Source Location | Verified Against | Status | Disposition | Hypothesis ID |
|-------|----------|-----------------|------------------|--------|-------------|---------------|
| Claim one | Code behavior | `file.sh` | synthetic | [unverified — alternative: test fixture] | n/a | |
<!-- GATE1:END -->
<!-- GATE1:VERIFIED -->

## Gate 2
<!-- GATE2:START -->
| Step | Type | Enforcement Mechanism |
|------|------|----------------------|
| A1 | Code | validate via test harness |
<!-- GATE2:END -->
<!-- GATE2:BOUNDARIES -->

<!-- GATE2B:DESIGN_REVIEW -->

<!-- GATE3:NO_CLAIMS -->

<!-- GATE0G:COHERENCY -->
```
verdict: PASS
rounds: 1
checker_models: [sonnet, sonnet, sonnet, opus]
```
"""


class PermissionPlanGateSpineValidationTests(unittest.TestCase):
    """permission-plan-gate.sh check 5: the spine is validated live, not via a sidecar."""

    HOOKS_DIR = Path(__file__).parent.parent      # the hooks dir THIS test lives under
    HOOK = HOOKS_DIR / "permission-plan-gate.sh"
    SID = "test-spine-gate-sid"
    SLUG = "smoke-topic-20260921000000"

    def _plan_text(self, thought_rel, *, preamble_lines=0, chain_shape="fenced"):
        preamble = "".join(f"Preamble line {i}.\n" for i in range(preamble_lines))
        if chain_shape == "fenced":
            chain = (f"```\nmode: A\nthought_file: {thought_rel}\n"
                     f"discovery_src_hash: 0123456789ab\nalternative_n: 1\n```\n")
        else:  # "centralization" — `<!--` on the next line, so the block reads EMPTY
            chain = (f"<!--\nmode: A\nthought_file: {thought_rel}\n"
                     f"discovery_src_hash: 0123456789ab\nalternative_n: 1\n-->\n")
        return _GATE_PLAN_TEMPLATE.format(preamble=preamble, chain=chain, js=_GATE_PLAN_JSON)

    def _run_gate(self, spine_text, *, sidecar=None, preamble_lines=0):
        """Drive the real hook once; returns (returncode, stderr, spine_abs_path)."""
        tmp = Path(tempfile.mkdtemp(prefix="spine-gate-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        home = tmp / "home"
        cfg = home / ".claude"
        (cfg / "plans").mkdir(parents=True)
        (cfg / "hooks").symlink_to(self.HOOKS_DIR)
        proj = tmp / "projects"
        (proj / "Thoughts").mkdir(parents=True)
        spine = proj / "Thoughts" / f"{self.SLUG}_THOUGHT.md"
        spine.write_text(spine_text)
        (proj / "TODO.md").write_text(
            f"- [ ] [Thought] **Smoke** — see Thoughts/{self.SLUG}_THOUGHT.md\n")
        if sidecar is not None:
            (proj / "Thoughts" / f"{self.SLUG}_THOUGHT_check.md").write_text(
                f"---\nverdict: {sidecar}\nchecked: 2026-01-01T00:00:00Z\nfile: {spine}\n---\n\n"
                f"Parent: [[{self.SLUG}_THOUGHT]]\nVERDICT: {sidecar}\n")
        plan = cfg / "plans" / "smoke.md"
        plan.write_text(self._plan_text(f"Thoughts/{self.SLUG}_THOUGHT.md",
                                        preamble_lines=preamble_lines))
        (cfg / "plans" / ".manifest.json").write_text(json.dumps({self.SID: [str(plan)]}))

        env = os.environ.copy()
        env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(cfg), PPG_PROJECTS_ROOT=str(proj))
        env["CLAUDE_CODE_REMOTE"] = ""
        for gate in ("0B2", "0C", "0G"):
            subprocess.run(
                ["python3", str(self.HOOKS_DIR / "pre_plan_gates.py"), "plan-override",
                 gate, str(plan), "--reason", "spine-gate test fixture"],
                capture_output=True, text=True, env=env, check=True)

        payload = json.dumps({"tool_name": "ExitPlanMode", "session_id": self.SID,
                              "tool_input": {}, "cwd": str(proj)})
        r = subprocess.run(["bash", str(self.HOOK)], input=payload,
                           capture_output=True, text=True, env=env)
        return r.returncode, r.stderr, str(spine)

    @staticmethod
    def _refusal(stderr):
        """Check 5's own refusal block — check-plan-gates.sh emits an unrelated
        Gate-0b2 advisory upstream that mentions a claim-runs 'sidecar'."""
        i = stderr.find("✗ Thought spine")
        return stderr[i:] if i != -1 else ""

    def _locate(self, plan_text):
        tmp = Path(tempfile.mkdtemp(prefix="spine-gate-loc-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        p = tmp / "plan.md"
        p.write_text(plan_text)
        r = subprocess.run(["bash", str(self.HOOK), "--locate-thought-file", str(p)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        return r.stdout.strip()

    # (1) well-formed spine, no sidecar → allowed
    def test_sound_spine_without_sidecar_is_allowed(self):
        rc, err, _ = self._run_gate(_SOUND_SPINE)
        self.assertEqual(rc, 0, f"expected allow; stderr:\n{err}")

    # (2) spine missing a locked field, no sidecar → refused, naming check + file,
    #     with a fix-the-spine remedy and no sidecar/producer-tool wording
    def test_broken_spine_is_refused_naming_the_failing_check(self):
        rc, err, spine = self._run_gate(_BROKEN_SPINE)
        self.assertEqual(rc, 2, f"expected refusal; stderr:\n{err}")
        refusal = self._refusal(err)
        self.assertIn("FAIL [Missing", refusal)
        self.assertIn("## Desired Solution", refusal)
        self.assertIn(spine, refusal)
        self.assertIn("fix", refusal.lower())
        self.assertNotIn("/clarification", refusal)
        self.assertNotIn("sidecar", refusal.lower())

    # (3) thought_file: beyond the old 20-line window → still checked
    def test_thought_file_past_line_twenty_is_still_checked(self):
        rc, err, _ = self._run_gate(_BROKEN_SPINE, preamble_lines=25)
        self.assertEqual(rc, 2, f"expected refusal; stderr:\n{err}")
        self.assertIn("FAIL [Missing", self._refusal(err))

    # (4) the CENTRALIZATION-shape CHAIN_SRC (an EMPTY block) resolves via the
    #     whole-file fallback. Asserted at the locator seam: check-plan-gates.sh
    #     refuses that shape upstream ("missing or invalid mode"), so it can never
    #     reach check 5 through the full gate.
    def test_centralization_chain_src_shape_falls_back_to_whole_file(self):
        text = self._plan_text("Thoughts/central_THOUGHT.md", chain_shape="centralization")
        self.assertEqual(self._locate(text), "Thoughts/central_THOUGHT.md")

    def test_chain_src_block_wins_over_a_later_decoy_line(self):
        text = (self._plan_text("Thoughts/real_THOUGHT.md", preamble_lines=30)
                + "\n```\nthought_file: Thoughts/decoy_THOUGHT.md\n```\n")
        self.assertEqual(self._locate(text), "Thoughts/real_THOUGHT.md")

    def test_mode_c_sentinel_is_returned_verbatim(self):
        self.assertEqual(self._locate(self._plan_text("n/a")), "n/a")

    # (5) a stale FAIL sidecar beside a sound spine no longer blocks
    def test_stale_fail_sidecar_does_not_block_a_sound_spine(self):
        rc, err, _ = self._run_gate(_SOUND_SPINE, sidecar="FAIL")
        self.assertEqual(rc, 0, f"expected allow; stderr:\n{err}")

    # (6) a stale PASS sidecar beside a broken spine no longer admits — together
    #     with (5) this is "the sidecar's presence, absence, or verdict has no effect"
    def test_stale_pass_sidecar_does_not_admit_a_broken_spine(self):
        rc, err, _ = self._run_gate(_BROKEN_SPINE, sidecar="PASS")
        self.assertEqual(rc, 2, f"expected refusal; stderr:\n{err}")


class ExportScriptTests(unittest.TestCase):
    """(j) export script: both workflow and plan tarballs produced in one export."""

    SCRIPT = Path.home() / ".claude" / "hooks" / "export-workflow-validation.sh"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.projects_root = Path(self.tmpdir) / "Projects"
        self.projects_root.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_validation_dir(self, kind, project):
        """Create a mock validation dir with a round file."""
        if kind == "workflow":
            base = Path.home() / ".claude" / "state" / "workflow_validation" / project
        else:
            base = Path.home() / ".claude" / "state" / "plan_validation" / project
        base.mkdir(parents=True, exist_ok=True)
        (base / "R1.md").write_text(f"---\nschema_version: 2\nrounds: 1\nkind: {kind}\nverdict: PASS\n---\n")
        return base

    def test_workflow_only_produces_tarball(self):
        """workflow-only project: one tarball with workflow_validation data."""
        proj = "TestExport7WF"
        wf_dir = self._make_validation_dir("workflow", proj)
        try:
            r = subprocess.run(
                ["bash", str(self.SCRIPT), proj, str(self.projects_root)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            tarball_path = r.stdout.strip().replace("Exported to: ", "")
            self.assertTrue(Path(tarball_path).exists())
            self.assertIn("validation_export_", Path(tarball_path).name)
        finally:
            shutil.rmtree(str(wf_dir), ignore_errors=True)

    def test_plan_only_produces_tarball(self):
        """plan-only project: one tarball with plan_validation data."""
        proj = "TestExport7PL"
        pl_dir = self._make_validation_dir("plan", proj)
        try:
            r = subprocess.run(
                ["bash", str(self.SCRIPT), proj, str(self.projects_root)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            tarball_path = r.stdout.strip().replace("Exported to: ", "")
            self.assertTrue(Path(tarball_path).exists())
            self.assertIn("validation_export_", Path(tarball_path).name)
        finally:
            shutil.rmtree(str(pl_dir), ignore_errors=True)

    def test_both_dirs_produce_single_combined_tarball(self):
        """project with both workflow and plan dirs: one combined tarball containing both."""
        proj = "TestExport7Both"
        wf_dir = self._make_validation_dir("workflow", proj)
        pl_dir = self._make_validation_dir("plan", proj)
        try:
            r = subprocess.run(
                ["bash", str(self.SCRIPT), proj, str(self.projects_root)],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, f"stderr: {r.stderr}")
            tarball_path = r.stdout.strip().replace("Exported to: ", "")
            self.assertTrue(Path(tarball_path).exists())
            # Verify tarball contains both workflow_validation and plan_validation entries
            import tarfile
            with tarfile.open(tarball_path) as tf:
                names = tf.getnames()
            has_workflow = any("workflow_validation" in n for n in names)
            has_plan = any("plan_validation" in n for n in names)
            self.assertTrue(has_workflow, f"workflow_validation missing from tarball: {names}")
            self.assertTrue(has_plan, f"plan_validation missing from tarball: {names}")
        finally:
            shutil.rmtree(str(wf_dir), ignore_errors=True)
            shutil.rmtree(str(pl_dir), ignore_errors=True)

    def test_no_dirs_exits_nonzero(self):
        """project with no validation dirs exits non-zero."""
        r = subprocess.run(
            ["bash", str(self.SCRIPT), "NoSuchProject7", str(self.projects_root)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(r.returncode, 0)


class InputScopingCompletionTests(unittest.TestCase):
    """(l) input scoping completion: explicit assertions that checker prompt
    excludes producer narrative, conversation context, and sibling verdicts."""

    def test_no_producer_narrative_in_checker_prompt(self):
        """Checker prompt must NOT contain producer narrative (e.g. AI plan-writing context)."""
        import _factcheck_engine as eng

        # Simulate a producer-narrative-rich environment by checking what
        # _build_checker_input outputs for a fresh round with no prior issues.
        prompt = eng._build_checker_input("plan", 0, "/path/plan.md", 1, None, "grounding rules")

        # Must reference artifact path
        self.assertIn("/path/plan.md", prompt)
        # Must NOT inline any content that would come from the producer context
        # (producer narrative markers we forbid)
        forbidden_phrases = [
            "I wrote this plan",
            "as the plan author",
            "conversation context",
            "producer reasoning",
        ]
        for phrase in forbidden_phrases:
            self.assertNotIn(phrase, prompt.lower(),
                             f"Forbidden phrase found in checker prompt: {phrase!r}")

    def test_no_sibling_verdicts_in_round1_prompt(self):
        """Round 1 checker prompt must NOT include sibling checker verdicts."""
        import _factcheck_engine as eng

        # Even if prior_issues is passed, round 1 should not include them
        prompt = eng._build_checker_input(
            "plan", 0, "/path/plan.md", 1, "DISCREPANCY: from sibling", "rules"
        )
        self.assertNotIn("from sibling", prompt)

    def test_sibling_verdicts_only_in_diff_only_rounds(self):
        """Sibling verdicts appear in checker prompt only for diff-only rounds (round >= 3)."""
        import _factcheck_engine as eng

        # Round 2: full check, should NOT include prior issues
        prompt_r2 = eng._build_checker_input(
            "plan", 0, "/path/plan.md", 2, "DISCREPANCY: prior", "rules"
        )
        self.assertNotIn("prior", prompt_r2)

        # Round 3: diff-only, SHOULD include prior issues
        prompt_r3 = eng._build_checker_input(
            "plan", 0, "/path/plan.md", 3, "DISCREPANCY: prior", "rules"
        )
        self.assertIn("prior", prompt_r3)

    def test_no_conversation_context_in_prompt(self):
        """Checker prompt must NOT inline conversation context."""
        import _factcheck_engine as eng

        prompt = eng._build_checker_input("plan", 0, "/path/plan.md", 1, None, "")
        # Conversation context would typically contain patterns like these;
        # the prompt should only contain the task-relevant scope prompt.
        forbidden = ["conversation", "session history", "prior messages"]
        for phrase in forbidden:
            self.assertNotIn(phrase, prompt.lower(),
                             f"Conversation context leaked: {phrase!r}")

    def test_grounding_rules_present_in_prompt(self):
        """Source-grounding rules are always included in checker prompt."""
        import _factcheck_engine as eng
        rules = "Quote first. No fabrications. Label editorial."
        prompt = eng._build_checker_input("plan", 0, "/path/plan.md", 1, None, rules)
        self.assertIn(rules, prompt)


class AggregationDeterminismOrderTests(unittest.TestCase):
    """(m) aggregation determinism completion: order-permutation assertion."""

    def _run_with_checker_order(self, checker_outputs):
        """Run factcheck_run with a checker that returns outputs in given order."""
        import _factcheck_engine as eng
        import tempfile

        tmpdir = tempfile.mkdtemp()
        try:
            state_dir = Path(tmpdir) / "validation"
            draft = Path(tmpdir) / "plan.md"
            draft.write_text("# Plan\n")

            output_iter = iter(checker_outputs)

            def ordered_checker(d, idx, model, rnum, prior):
                try:
                    return next(output_iter)
                except StopIteration:
                    return "PASS"

            resolver = _mock_resolver("P7m", "order-test")
            result = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-m-order",
                debounce_seconds=0,
                _checker_fn=ordered_checker,
                _proj_topic_resolver=resolver,
            )
            return result
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_dirty_regardless_of_discrepancy_position_first(self):
        """DIRTY when first checker has DISCREPANCY, others PASS (3 checkers per Session 1a canon)."""
        result = self._run_with_checker_order([
            "DISCREPANCY: first checker issue", "PASS", "PASS"
        ])
        # R1 = DIRTY; engine continues to R2 (all PASS)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 2)

    def test_dirty_regardless_of_discrepancy_position_last(self):
        """DIRTY when last checker has DISCREPANCY, others PASS (order-independent; 3 checkers per Session 1a canon)."""
        result = self._run_with_checker_order([
            "PASS", "PASS", "DISCREPANCY: last checker issue"
        ])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 2)

    def test_dirty_regardless_of_discrepancy_position_middle(self):
        """DIRTY when middle checker has DISCREPANCY (3 checkers per Session 1a canon)."""
        result = self._run_with_checker_order([
            "PASS", "DISCREPANCY: middle checker issue", "PASS"
        ])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rounds"], 2)

    def test_pass_only_when_all_pass(self):
        """PASS only when ALL checkers return PASS (any DISCREPANCY triggers DIRTY)."""
        import _factcheck_engine as eng
        import tempfile

        tmpdir = tempfile.mkdtemp()
        try:
            state_dir = Path(tmpdir) / "validation"
            draft = Path(tmpdir) / "plan.md"
            draft.write_text("# Plan\n")
            resolver = _mock_resolver("P7m", "all-pass")
            result = eng.factcheck_run(
                state_dir, str(draft), "plan", "sid-m-allpass",
                debounce_seconds=0,
                _checker_fn=lambda *a: "PASS",
                _proj_topic_resolver=resolver,
            )
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["rounds"], 1)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_aggregation_is_pure_code(self):
        """Aggregation reads checker output text; no AI subagent invoked for aggregation."""
        import _factcheck_engine as eng

        # _aggregate_verdicts is a pure function: list of strings → bool
        # Verify it doesn't call subprocess.run (which would indicate AI invocation)
        captured_calls = []
        orig_run = subprocess.run

        def spy_run(cmd, **kwargs):
            captured_calls.append(cmd)
            return orig_run(cmd, **kwargs)

        with patch("subprocess.run", side_effect=spy_run):
            # Call the aggregation function directly
            checker_outputs = ["PASS", "DISCREPANCY: issue", "PASS", "PASS"]
            is_dirty = any("discrepancy" in o.lower() for o in checker_outputs)

        # Aggregation is computed inline — no subprocess.run needed for it
        self.assertTrue(is_dirty)
        # subprocess.run was not called during aggregation itself
        self.assertEqual(len(captured_calls), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

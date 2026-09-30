#!/usr/bin/env python3
"""Tests for Slice J-1 Foundations — `work_done.py` + `work_done_journal.py`
and the new `annotate-session` subcommand in `pre_plan_gates.py`.

Covers:

  annotate-session CLI (A1):
    AS1  Empty `## Sessions` section → heading + bullet appended (with tag)
    AS2  Re-run with new bullet under same (date, sid) → bullet replaced, no duplication
    AS3  Bullet without trailing period → ValueError (non-zero exit)
    AS4  Bullet too short / too long → ValueError
    AS5  Non-tagged bullets in same block left untouched on replace

  Intent journal data layer (A2):
    J1   write_intent + read_intent roundtrip
    J2   mark_applied flips applied true; is_applied confirms; monotonic
    J3   delete_intent removes the file + sidecar
    J4   write_intent on an existing journal raises FileExistsError
    J5   write_intent rejects unknown surface keys

  Ship-event detection (A3):
    SE1  fresh lock, no commits          → ship_event True (reason: fresh lock)
    SE2  no lock, commits present        → ship_event True (reason: commits)
    SE3  stale lock only                  → ship_event False
    SE4  nothing                          → ship_event False

  slice_id resolution (A4):
    SI1  valid GATE0SR:SLICES block with `slice_id: J` → "J"
    SI2  missing marker block             → ValueError
    SI3  two `slice_id:` lines in block   → ValueError

  Two-repo attribute_commits merge (A5):
    M1   project has 2 confident + harness has 1 → 3 prefix-tagged strings, project first
    M2   only project repo has confident commits → only [proj] prefixes returned

  Retire-marker writer (A6):
    R1   status line ending with period gets "; Retired <date>" before period
    R2   re-run with same date → no_op_already_retired
    R3   spine with no **Status:** line → ValueError
    R4   invalid retire_date format → ValueError

Run: python3 ${KIT_HOOKS_DIR}/tests/test_work_done.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

# Respect CLAUDE_CONFIG_DIR so claude-experiment staging clones / config-source
# worktrees test their OWN module copies (mirrors test_pre_plan_gates.py:27);
# defaults to live ~/.claude when the env var is unset (behavior unchanged).
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg  # noqa: E402
import taskmanagement as tm  # noqa: E402
import work_done as wd  # noqa: E402
import work_done_journal as wdj  # noqa: E402


# ---------------------------------------------------------------------------
# Sandboxes
# ---------------------------------------------------------------------------

class _JournalSandbox:
    """Redirect work_done_journal.STATE_DIR to a tempdir."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="wdj_")
        self._orig = wdj.STATE_DIR
        wdj.STATE_DIR = Path(self.tmp)
        return self

    def __exit__(self, *exc):
        wdj.STATE_DIR = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)


class _PPGSandbox:
    """Redirect pre_plan_gates state dirs to a tempdir + plant an active topic.

    Writes _active.json and a topic-state JSON pointing at the given spine path
    so `annotate_session` can resolve `thought_file_path` via `_resolve_topic`.
    """

    def __init__(self, sid: str, topic_slug: str, project_slug: str, spine: Path):
        self.sid = sid
        self.topic_slug = topic_slug
        self.project_slug = project_slug
        self.spine = spine

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="ppg_")
        self._orig_topic_dir = ppg.TOPIC_STATE_DIR
        ppg.TOPIC_STATE_DIR = Path(self.tmp)
        # _active.json
        active = {
            self.sid: {
                "topic_slug": self.topic_slug,
                "active_project": self.project_slug,
            }
        }
        ppg.write_active(active)
        # topic state
        ppg._write_topic_state(
            topic_slug=self.topic_slug,
            project_slug=self.project_slug,
            state={"thought_file_path": str(self.spine)},
        )
        return self

    def __exit__(self, *exc):
        ppg.TOPIC_STATE_DIR = self._orig_topic_dir
        shutil.rmtree(self.tmp, ignore_errors=True)


class _LockSandbox:
    """Redirect taskmanagement.LOCKS_DIR to a tempdir."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="tm_locks_")
        self._orig_locks_dir = tm.LOCKS_DIR
        self._orig_releases = tm.RELEASES_LOG
        tm.LOCKS_DIR = Path(self.tmp)
        tm.RELEASES_LOG = tm.LOCKS_DIR / "_releases.jsonl"
        return self

    def __exit__(self, *exc):
        tm.LOCKS_DIR = self._orig_locks_dir
        tm.RELEASES_LOG = self._orig_releases
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Synthetic spine factory
# ---------------------------------------------------------------------------

_SPINE_TEMPLATE = """# Synthetic Spine

**Status:** Slice X active; in progress.

## Sessions

*Updated at each /close.*

{prior_bullets}
"""


def _write_synthetic_spine(tmpdir: Path, prior_bullets: str = "") -> Path:
    spine = tmpdir / "synthetic_THOUGHT.md"
    spine.write_text(_SPINE_TEMPLATE.format(prior_bullets=prior_bullets),
                     encoding="utf-8")
    return spine


# ---------------------------------------------------------------------------
# annotate-session CLI (A1)
# ---------------------------------------------------------------------------

class AnnotateSessionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="annot_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sid = "abcd1234-test-sess-id-0000000000aa"
        self.spine = _write_synthetic_spine(self.tmp)

    def _today_header(self):
        today = datetime.now().date().isoformat()
        return f"### {today} session [sid:{self.sid[:8]}]"

    def test_AS1_empty_sessions_section_gets_heading_and_bullet(self):
        with _PPGSandbox(self.sid, "topic-x", "proj-y", self.spine):
            result = ppg.annotate_session(self.sid, "First annotation line.")
            self.assertEqual(result["status"], "heading_and_bullet_appended")
            text = self.spine.read_text(encoding="utf-8")
            self.assertIn(self._today_header(), text)
            self.assertIn("First annotation line.", text)
            # S1: the tag carries the writer identity (default: work-done)
            self.assertIn(ppg.annotate_marker(ppg.SESSION_WRITER_WORK_DONE), text)

    def test_AS2_rerun_replaces_prior_annotate_bullet(self):
        with _PPGSandbox(self.sid, "topic-x", "proj-y", self.spine):
            ppg.annotate_session(self.sid, "First line.")
            result2 = ppg.annotate_session(self.sid, "Second line.")
            self.assertEqual(result2["status"], "bullet_replaced")
            text = self.spine.read_text(encoding="utf-8")
            self.assertNotIn("First line.", text)
            self.assertIn("Second line.", text)
            # Heading appears exactly once
            self.assertEqual(text.count(self._today_header()), 1)
            # Annotate-tagged bullet appears exactly once
            self.assertEqual(
                text.count(ppg.annotate_marker(ppg.SESSION_WRITER_WORK_DONE)), 1)

    def test_AS3_bullet_without_period_raises(self):
        with _PPGSandbox(self.sid, "topic-x", "proj-y", self.spine):
            with self.assertRaises(ValueError):
                ppg.annotate_session(self.sid, "missing trailing period")

    def test_AS4_bullet_length_out_of_range(self):
        with _PPGSandbox(self.sid, "topic-x", "proj-y", self.spine):
            # 3 chars → too short
            with self.assertRaises(ValueError):
                ppg.annotate_session(self.sid, "Hi.")
            # 201 chars (200 x's + ".") → too long
            with self.assertRaises(ValueError):
                ppg.annotate_session(self.sid, ("x" * 200) + ".")
            # 200 chars (199 x's + ".") → boundary, accepted
            result = ppg.annotate_session(self.sid, ("x" * 199) + ".")
            self.assertEqual(result["status"], "heading_and_bullet_appended")

    def test_AS5_non_tagged_bullets_left_untouched_on_replace(self):
        prior = (
            f"### {datetime.now().date().isoformat()} session [sid:{self.sid[:8]}]\n"
            "- Pre-existing manual bullet (no marker).\n"
            "- Another pre-existing bullet.\n"
        )
        self.spine.write_text(
            _SPINE_TEMPLATE.format(prior_bullets=prior),
            encoding="utf-8",
        )
        with _PPGSandbox(self.sid, "topic-x", "proj-y", self.spine):
            # First call has no annotate-tagged bullet yet → append
            ppg.annotate_session(self.sid, "Annotate-owned bullet v1.")
            text1 = self.spine.read_text(encoding="utf-8")
            self.assertIn("Pre-existing manual bullet (no marker).", text1)
            self.assertIn("Another pre-existing bullet.", text1)
            self.assertIn("Annotate-owned bullet v1.", text1)
            # Second call replaces only the tagged bullet, others stay
            ppg.annotate_session(self.sid, "Annotate-owned bullet v2.")
            text2 = self.spine.read_text(encoding="utf-8")
            self.assertIn("Pre-existing manual bullet (no marker).", text2)
            self.assertIn("Another pre-existing bullet.", text2)
            self.assertNotIn("Annotate-owned bullet v1.", text2)
            self.assertIn("Annotate-owned bullet v2.", text2)


# ---------------------------------------------------------------------------
# annotate-session CLI subprocess smoke (A1 — argparse path)
# ---------------------------------------------------------------------------

class AnnotateSessionCLITests(unittest.TestCase):

    def test_AS_CLI_usage_on_missing_argv(self):
        # Smoke: bare command without args prints Usage to stderr + exits non-zero.
        res = subprocess.run(
            ["python3", str(HOOKS / "pre_plan_gates.py"), "annotate-session"],
            capture_output=True, text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Usage: annotate-session", res.stderr)


# ---------------------------------------------------------------------------
# Intent journal (A2)
# ---------------------------------------------------------------------------

class IntentJournalTests(unittest.TestCase):

    def test_J1_write_read_roundtrip(self):
        with _JournalSandbox():
            payload = {
                "todo": {"line": "- [x] Foo."},
                "slice_register": {"slice_id": "J", "status": "SHIPPED"},
            }
            written = wdj.write_intent("sid-1", "topic-x", payload)
            read = wdj.read_intent("sid-1", "topic-x")
            self.assertEqual(written, read)
            self.assertEqual(written["schema_version"], 1)
            self.assertEqual(written["sid"], "sid-1")
            self.assertEqual(written["topic_slug"], "topic-x")
            self.assertFalse(written["surfaces"]["todo"]["applied"])
            self.assertEqual(written["surfaces"]["todo"]["payload"],
                             {"line": "- [x] Foo."})
            self.assertIsNone(written["surfaces"]["retire_marker"]["payload"])

    def test_J2_mark_applied_flips_and_is_idempotent(self):
        with _JournalSandbox():
            wdj.write_intent("sid-1", "topic-x", {"todo": {"line": "x"}})
            self.assertFalse(wdj.is_applied("sid-1", "topic-x", "todo"))
            updated = wdj.mark_applied("sid-1", "topic-x", "todo")
            self.assertTrue(updated["surfaces"]["todo"]["applied"])
            self.assertIsNotNone(updated["surfaces"]["todo"]["applied_at"])
            self.assertTrue(wdj.is_applied("sid-1", "topic-x", "todo"))
            applied_at_1 = updated["surfaces"]["todo"]["applied_at"]
            # Re-mark is a no-op (monotonic)
            updated2 = wdj.mark_applied("sid-1", "topic-x", "todo")
            self.assertEqual(updated2["surfaces"]["todo"]["applied_at"],
                             applied_at_1)

    def test_J3_delete_intent_removes_file(self):
        with _JournalSandbox():
            wdj.write_intent("sid-1", "topic-x", {})
            self.assertIsNotNone(wdj.read_intent("sid-1", "topic-x"))
            removed = wdj.delete_intent("sid-1", "topic-x")
            self.assertTrue(removed)
            self.assertIsNone(wdj.read_intent("sid-1", "topic-x"))

    def test_J4_write_existing_raises(self):
        with _JournalSandbox():
            wdj.write_intent("sid-1", "topic-x", {})
            with self.assertRaises(FileExistsError):
                wdj.write_intent("sid-1", "topic-x", {})

    def test_J5_unknown_surface_rejected(self):
        with _JournalSandbox():
            with self.assertRaises(ValueError):
                wdj.write_intent("sid-1", "topic-x", {"banana": {}})


# ---------------------------------------------------------------------------
# Ship-event detection (A3)
# ---------------------------------------------------------------------------

class ShipEventDetectionTests(unittest.TestCase):

    def _plant_lock(self, lock_key: str, sid: str, heartbeat_age_s: int):
        """Write a synthetic lock payload with last_heartbeat = now - age.

        `lock_key` is the FULL lock key — for topic locks the composite
        `<topic>__<project>` (M16 fix, S3/A3): detect_ship_event reads the
        composite, so the lock must be planted under it."""
        heartbeat = (datetime.now(timezone.utc)
                     - timedelta(seconds=heartbeat_age_s)
                     ).isoformat(timespec="seconds")
        tm.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": sid,
            "pid": 12345,
            "started_at": heartbeat,
            "last_heartbeat": heartbeat,
            "topic_slug": lock_key,
        }
        (tm.LOCKS_DIR / f"{lock_key}.lock").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def test_SE1_fresh_lock_only_is_ship_event(self):
        with _LockSandbox():
            # composite-keyed lock (matches what detect_ship_event now reads)
            self._plant_lock("topic-x__proj-y", "sess-A", heartbeat_age_s=10)
            result = wd.detect_ship_event(
                topic_slug="topic-x", project_slug="proj-y",
                plan_path=None, session_id="sess-A",
                started_at="2026-06-01T00:00:00+00:00",
                ended_at="2026-06-01T23:59:59+00:00",
            )
            self.assertTrue(result["ship_event"])
            self.assertTrue(result["lock_present"])
            self.assertTrue(result["lock_fresh"])
            self.assertFalse(result["commits_present"])

    def test_SE3_stale_lock_only_is_not_ship_event(self):
        # Force a small STALE_T so a 30-second-old heartbeat is "stale"
        os.environ["TM_STALE_T_SECONDS"] = "5"
        try:
            with _LockSandbox():
                self._plant_lock("topic-x__proj-y", "sess-A", heartbeat_age_s=30)
                result = wd.detect_ship_event(
                    topic_slug="topic-x", project_slug="proj-y",
                    plan_path=None, session_id="sess-A",
                    started_at="2026-06-01T00:00:00+00:00",
                    ended_at="2026-06-01T23:59:59+00:00",
                )
                self.assertFalse(result["ship_event"])
                self.assertTrue(result["lock_present"])
                self.assertFalse(result["lock_fresh"])
                self.assertFalse(result["commits_present"])
        finally:
            del os.environ["TM_STALE_T_SECONDS"]

    def test_SE4_nothing_present_is_not_ship_event(self):
        with _LockSandbox():
            result = wd.detect_ship_event(
                topic_slug="topic-x", project_slug="proj-y",
                plan_path=None, session_id="sess-A",
                started_at="2026-06-01T00:00:00+00:00",
                ended_at="2026-06-01T23:59:59+00:00",
            )
            self.assertFalse(result["ship_event"])
            self.assertFalse(result["lock_present"])
            self.assertFalse(result["lock_fresh"])
            self.assertFalse(result["commits_present"])

    def test_SE2_commits_present_alone_is_ship_event(self):
        # Synthetic project repo with one in-scope commit; no lock.
        tmp = Path(tempfile.mkdtemp(prefix="se2_"))
        try:
            proj = tmp / "proj_repo"
            harness = tmp / "harness_repo"
            for r in (proj, harness):
                r.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=r, check=True)
                subprocess.run(["git", "config", "user.email", "t@t"],
                               cwd=r, check=True)
                subprocess.run(["git", "config", "user.name", "t"],
                               cwd=r, check=True)
            target = proj / "x.py"
            target.write_text("print('hi')\n", encoding="utf-8")
            subprocess.run(["git", "add", "x.py"], cwd=proj, check=True)
            subprocess.run(["git", "commit", "-qm", "add x"],
                           cwd=proj, check=True)
            # Plan file with ## Diff scoping x.py to Session 1
            plan = tmp / "PLAN.md"
            plan.write_text(
                "## Diff\n\n**Session 1:**\n- `x.py`\n",
                encoding="utf-8",
            )
            with _LockSandbox():
                start = (datetime.now(timezone.utc) - timedelta(minutes=10)
                         ).isoformat()
                end = (datetime.now(timezone.utc) + timedelta(minutes=10)
                       ).isoformat()
                result = wd.detect_ship_event(
                    topic_slug="topic-x", project_slug="proj-y",
                    plan_path=plan, session_id="sess-A",
                    started_at=start, ended_at=end,
                    project_repo=proj, harness_repo=harness,
                    session_num=1,
                )
                self.assertTrue(result["ship_event"])
                self.assertFalse(result["lock_present"])
                self.assertTrue(result["commits_present"])
                self.assertEqual(len(result["commits"]), 1)
                self.assertTrue(result["commits"][0].startswith("[proj] "))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# slice_id resolution (A4)
# ---------------------------------------------------------------------------

class SliceIdResolutionTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sid_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def _plan(self, body: str) -> Path:
        p = self.tmp / "P.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_SI1_valid_marker_returns_slice_id(self):
        p = self._plan(
            "Body\n\n"
            "<!-- GATE0SR:SLICES -->\n"
            "slice_register_ref: spine.md#slice-register\n"
            "slice_id: J\n"
            "<!-- /GATE0SR:SLICES -->\n"
        )
        self.assertEqual(wd.resolve_slice_id(p), "J")

    def test_SI2_missing_marker_raises(self):
        p = self._plan("Body\n\nNo marker here.\n")
        with self.assertRaises(ValueError):
            wd.resolve_slice_id(p)

    def test_SI3_duplicate_slice_id_raises(self):
        p = self._plan(
            "<!-- GATE0SR:SLICES -->\n"
            "slice_id: J\n"
            "slice_id: K\n"
            "<!-- /GATE0SR:SLICES -->\n"
        )
        with self.assertRaises(ValueError):
            wd.resolve_slice_id(p)

    def test_SI4_plural_no_override_raises_with_diagnostic(self):
        p = self._plan(
            "<!-- GATE0SR:SLICES -->\n"
            "slice_ids: S1, S2, S3\n"
            "<!-- /GATE0SR:SLICES -->\n"
        )
        with self.assertRaises(ValueError) as ctx:
            wd.resolve_slice_id(p)
        msg = str(ctx.exception)
        self.assertIn("slice_ids", msg)
        self.assertIn("--slice-id", msg)

    def test_SI5_plural_with_override_returns_override(self):
        p = self._plan(
            "<!-- GATE0SR:SLICES -->\n"
            "slice_ids: S1, S2, S3\n"
            "<!-- /GATE0SR:SLICES -->\n"
        )
        self.assertEqual(wd.resolve_slice_id(p, override_slice_id="S2"), "S2")

    def test_SI6_singular_with_override_returns_override(self):
        p = self._plan(
            "<!-- GATE0SR:SLICES -->\n"
            "slice_id: J\n"
            "<!-- /GATE0SR:SLICES -->\n"
        )
        self.assertEqual(wd.resolve_slice_id(p, override_slice_id="K"), "K")

    def test_SI7_cli_slice_id_flag_via_subprocess(self):
        tmp = Path(tempfile.mkdtemp(prefix="si7_"))
        try:
            plan = tmp / "P.md"
            plan.write_text(
                "<!-- GATE0SR:SLICES -->\n"
                "slice_ids: S1, S2, S3\n"
                "<!-- /GATE0SR:SLICES -->\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["python3", str(HOOKS / "work_done.py"), "resolve-slice-id",
                 str(plan), "--slice-id", "S3"],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "S3")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Two-repo attribute_commits merge (A5)
# ---------------------------------------------------------------------------

class AttributeCommitsMergedTests(unittest.TestCase):

    def _make_repo(self, root: Path, files: list[str]) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
        for f in files:
            tgt = root / f
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_text(f"// {f}\n", encoding="utf-8")
            subprocess.run(["git", "add", f], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", f"add {f}"],
                           cwd=root, check=True)
        return root

    def test_M1_project_two_plus_harness_one_in_expected_order(self):
        tmp = Path(tempfile.mkdtemp(prefix="m1_"))
        try:
            proj = self._make_repo(tmp / "proj", ["a.py", "b.py"])
            harness = self._make_repo(tmp / "harness", ["c.py"])
            plan = tmp / "PLAN.md"
            plan.write_text(
                "## Diff\n\n**Session 1:**\n- `a.py`\n- `b.py`\n- `c.py`\n",
                encoding="utf-8",
            )
            start = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            end = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            merged = wd.attribute_commits_merged(
                plan_path=plan, session_id="sid-1",
                started_at=start, ended_at=end,
                project_repo=proj, harness_repo=harness,
                session_num=1,
            )
            self.assertEqual(len(merged), 3)
            self.assertTrue(merged[0].startswith("[proj] "))
            self.assertTrue(merged[1].startswith("[proj] "))
            self.assertTrue(merged[2].startswith("[harness] "))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_M2_only_project_commits_returned(self):
        tmp = Path(tempfile.mkdtemp(prefix="m2_"))
        try:
            proj = self._make_repo(tmp / "proj", ["a.py"])
            harness = self._make_repo(tmp / "harness", [])  # repo present, no commits
            plan = tmp / "PLAN.md"
            plan.write_text(
                "## Diff\n\n**Session 1:**\n- `a.py`\n",
                encoding="utf-8",
            )
            start = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            end = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            merged = wd.attribute_commits_merged(
                plan_path=plan, session_id="sid-1",
                started_at=start, ended_at=end,
                project_repo=proj, harness_repo=harness,
                session_num=1,
            )
            self.assertEqual(len(merged), 1)
            self.assertTrue(merged[0].startswith("[proj] "))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Retire-marker writer (A6)
# ---------------------------------------------------------------------------

class RetireMarkerTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ret_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.spine = self.tmp / "spine.md"
        self.spine.write_text(
            "# Title\n\n**Status:** Slice X active; in progress.\n\nMore body.\n",
            encoding="utf-8",
        )

    def test_R1_appends_before_trailing_period(self):
        res = wd.write_retire_marker(self.spine, "2026-06-01")
        self.assertEqual(res["status"], "retired")
        line = next(
            l for l in self.spine.read_text(encoding="utf-8").splitlines()
            if l.startswith("**Status:**")
        )
        self.assertIn("Retired 2026-06-01", line)
        self.assertTrue(line.rstrip().endswith("."))

    def test_R2_rerun_with_same_date_is_noop(self):
        wd.write_retire_marker(self.spine, "2026-06-01")
        res2 = wd.write_retire_marker(self.spine, "2026-06-01")
        self.assertEqual(res2["status"], "noop_already_retired")
        self.assertEqual(
            self.spine.read_text(encoding="utf-8").count("Retired 2026-06-01"),
            1,
        )

    def test_R3_no_status_line_raises(self):
        self.spine.write_text("# Title\n\nNo status line here.\n",
                              encoding="utf-8")
        with self.assertRaises(ValueError):
            wd.write_retire_marker(self.spine, "2026-06-01")

    def test_R4_invalid_date_raises(self):
        with self.assertRaises(ValueError):
            wd.write_retire_marker(self.spine, "not-a-date")

    def test_R5_body_status_under_h3_is_refused(self):
        # project-tracking-staleness S1 (section-scope axis): a spine with NO
        # preamble Status and only a `**Status:** chosen` under a
        # `### Solution Alternative` block (a normal /solution-design product)
        # must be REFUSED loudly, never mis-stamped as Retired (Mechanism 8).
        self.spine.write_text(
            "# Idea\n\nSome body.\n\n"
            "## Solution Design\n\n"
            "### Solution Alternative 1\n\n"
            "**Status:** chosen\n\nMore.\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            wd.write_retire_marker(self.spine, "2026-06-01")
        after = self.spine.read_text(encoding="utf-8")
        self.assertIn("**Status:** chosen", after)   # untouched
        self.assertNotIn("Retired", after)           # not mis-stamped

    def test_R6_fenced_heading_does_not_fool_governing_heading(self):
        # Fenced-code edge (review §3): a real body Status governed by an H2,
        # preceded by a fenced block containing a `### ...` line. The helper
        # must classify by the real H2 (depth 2 → accept), NOT the fenced
        # pseudo-heading (which a naive scan would read as depth 3 → refuse).
        self.spine.write_text(
            "# Title\n\n"
            "## Status\n\n"
            "```\n### not a real heading\n```\n\n"
            "**Status:** active; in progress.\n",
            encoding="utf-8",
        )
        res = wd.write_retire_marker(self.spine, "2026-06-01")
        self.assertEqual(res["status"], "retired")
        self.assertIn("Retired 2026-06-01", self.spine.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# S7 (M9) — partial-fire mode resolver (resolve_fire_mode + CLI)
# ---------------------------------------------------------------------------

class ResolveFireModeTests(unittest.TestCase):
    """project-tracking-staleness S7 — `wd.resolve_fire_mode`.

    FM1  terminal session (M+1 >= N)            → full
    FM2  non-terminal session (M+1 < N)         → session-only
    FM3  boundary M+1 == N                       → full
    FM4  single-session N=1                      → full
    FM5  absent row (register has other rows)    → full
    FM6  no register at all                      → full
    FM7  corrupt L:slice row (regex-rejected)    → full (manifests as absent)
    FM8  return-shape keys + sessions echoed
    FM9  plan_path accepted, does not change result
    FM10 CLI resolve-fire-mode smoke (JSON, mode)
    FM11 CLI --plan flag accepted
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fm_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.spine = self.tmp / "spine.md"

    def _write_spine(self, rows):
        # rows: list of (slice_id, status, sessions)
        body = ["# Idea\n", "## Slice Register\n"]
        for sid, status, sessions in rows:
            body.append(tm.render_slice_row(sid, status, sessions))
        self.spine.write_text("\n".join(body) + "\n", encoding="utf-8")

    def test_FM1_terminal_full(self):
        self._write_spine([("S1", "NOW", "2/3")])
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(res["mode"], "full")

    def test_FM2_non_terminal_session_only(self):
        self._write_spine([("S1", "NOW", "0/3")])
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(res["mode"], "session-only")

    def test_FM3_boundary_m_plus_one_equals_n_is_full(self):
        self._write_spine([("S1", "NOW", "1/2")])
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(res["mode"], "full")

    def test_FM4_single_session_full(self):
        self._write_spine([("S1", "NOW", "0/1")])
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(res["mode"], "full")

    def test_FM5_absent_row_full(self):
        self._write_spine([("S1", "SHIPPED", "1/1"), ("S2", "NOW", "0/3")])
        res = wd.resolve_fire_mode(self.spine, "S9")   # not present
        self.assertEqual(res["mode"], "full")
        self.assertIn("no slice-register row", res["reason"])

    def test_FM6_no_register_full(self):
        self.spine.write_text("# Idea\n\nNo rows here.\n", encoding="utf-8")
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(res["mode"], "full")

    def test_FM7_corrupt_row_manifests_absent_full(self):
        # A malformed `sessions` field fails SLICE_ROW_RE → parse_slice_register
        # drops the row → resolve_fire_mode sees it as absent → full (bounded
        # fallback, Guiding Policy edge note).
        self.spine.write_text(
            "# Idea\n\n## Slice Register\n\n"
            "<!-- L:slice id=S1 status=NOW sessions=BAD plan=_ diary=_ "
            "updated=2026-07-31 -->\n",
            encoding="utf-8",
        )
        self.assertEqual(tm.parse_slice_register(self.spine), [])  # not parsed
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(res["mode"], "full")

    def test_FM8_return_shape(self):
        self._write_spine([("S1", "NOW", "1/4")])
        res = wd.resolve_fire_mode(self.spine, "S1")
        self.assertEqual(set(res), {"mode", "reason", "sessions", "slice_id"})
        self.assertEqual(res["sessions"], "1/4")
        self.assertEqual(res["slice_id"], "S1")
        self.assertEqual(res["mode"], "session-only")

    def test_FM9_plan_path_accepted_not_consulted(self):
        self._write_spine([("S1", "NOW", "0/2")])
        a = wd.resolve_fire_mode(self.spine, "S1")
        b = wd.resolve_fire_mode(self.spine, "S1",
                                 plan_path="/nonexistent/plan.md")
        self.assertEqual(a["mode"], b["mode"])
        self.assertEqual(b["mode"], "session-only")

    def test_FM10_cli_smoke(self):
        self._write_spine([("S1", "NOW", "0/3")])
        proc = subprocess.run(
            [sys.executable, str(HOOKS / "work_done.py"),
             "resolve-fire-mode", str(self.spine), "S1"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["mode"], "session-only")

    def test_FM11_cli_plan_flag_accepted(self):
        self._write_spine([("S1", "NOW", "2/3")])
        proc = subprocess.run(
            [sys.executable, str(HOOKS / "work_done.py"),
             "resolve-fire-mode", str(self.spine), "S1", "--plan", "/x/plan.md"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["mode"], "full")


# ---------------------------------------------------------------------------
# Session 2 (A13) — completion ledger, session-lock enumeration,
# retire-as-never, crash-recovery resume, /close overlap-guard smoke.
# ---------------------------------------------------------------------------

class _LedgerSandbox:
    """Redirect work_done.LEDGER_DIR and work_done_journal.STATE_DIR to a tempdir."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="wd_led_")
        self._orig_ledger = wd.LEDGER_DIR
        self._orig_state = wdj.STATE_DIR
        wd.LEDGER_DIR = Path(self.tmp)
        wdj.STATE_DIR = Path(self.tmp)
        return self

    def __exit__(self, *exc):
        wd.LEDGER_DIR = self._orig_ledger
        wdj.STATE_DIR = self._orig_state
        shutil.rmtree(self.tmp, ignore_errors=True)


class CompletionLedgerTests(unittest.TestCase):

    def test_L1_append_creates_file_with_one_jsonl_row(self):
        with _LedgerSandbox():
            path = wd.append_completion_ledger(
                "sid-1", "topic-x",
                {"slice_id": "J", "todo_pattern": "Foo",
                 "commits": ["[proj] abc123"], "retired": False},
            )
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            rows = [json.loads(l) for l in content.splitlines() if l.strip()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["slice_id"], "J")
            self.assertEqual(rows[0]["todo_pattern"], "Foo")
            self.assertEqual(rows[0]["session_id"], "sid-1")
            self.assertEqual(rows[0]["topic_slug"], "topic-x")
            self.assertIn("shipped_at", rows[0])

    def test_L2_append_is_append_only(self):
        with _LedgerSandbox():
            wd.append_completion_ledger("sid-1", "topic-x",
                                        {"slice_id": "A"})
            wd.append_completion_ledger("sid-1", "topic-x",
                                        {"slice_id": "B"})
            path = wd._ledger_path("sid-1", "topic-x")
            content = path.read_text(encoding="utf-8")
            rows = [json.loads(l) for l in content.splitlines() if l.strip()]
            self.assertEqual([r["slice_id"] for r in rows], ["A", "B"])

    def test_L3_empty_session_or_topic_raises(self):
        with _LedgerSandbox():
            with self.assertRaises(ValueError):
                wd.append_completion_ledger("", "topic-x", {})
            with self.assertRaises(ValueError):
                wd.append_completion_ledger("sid-1", "", {})


class SessionLocksEnumerationTests(unittest.TestCase):
    """A12 — enumerate session-held locks."""

    def _plant(self, topic: str, sid: str, age_s: int = 10):
        heartbeat = (datetime.now(timezone.utc) -
                     timedelta(seconds=age_s)).isoformat(timespec="seconds")
        tm.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "session_id": sid,
            "pid": 12345,
            "started_at": heartbeat,
            "last_heartbeat": heartbeat,
            "topic_slug": topic,
        }
        (tm.LOCKS_DIR / f"{topic}.lock").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    def test_SL1_zero_locks_returns_empty(self):
        with _LockSandbox():
            self.assertEqual(wd.session_locks("sess-A"), [])

    def test_SL2_one_held_lock_returns_single_entry(self):
        with _LockSandbox():
            self._plant("topic-x", "sess-A", age_s=10)
            self._plant("topic-y", "sess-B", age_s=10)  # different session
            locks = wd.session_locks("sess-A")
            self.assertEqual(len(locks), 1)
            self.assertEqual(locks[0]["topic_slug"], "topic-x")
            self.assertTrue(locks[0]["fresh"])

    def test_SL3_multiple_held_locks_returned(self):
        with _LockSandbox():
            self._plant("topic-x", "sess-A", age_s=10)
            self._plant("topic-z", "sess-A", age_s=10)
            self._plant("topic-y", "sess-B", age_s=10)
            locks = wd.session_locks("sess-A")
            self.assertEqual(
                sorted(l["topic_slug"] for l in locks),
                ["topic-x", "topic-z"],
            )
            self.assertTrue(all(l["fresh"] for l in locks))

    def test_SL4_stale_lock_marked_not_fresh(self):
        os.environ["TM_STALE_T_SECONDS"] = "5"
        try:
            with _LockSandbox():
                self._plant("topic-x", "sess-A", age_s=30)
                locks = wd.session_locks("sess-A")
                self.assertEqual(len(locks), 1)
                self.assertFalse(locks[0]["fresh"])
        finally:
            del os.environ["TM_STALE_T_SECONDS"]


class RetireAsNeverTests(unittest.TestCase):
    """A9 — pre-atomic NEVER conversions through L's write_slice_row + verify_write."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ran_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.spine = self.tmp / "spine.md"
        # Synthetic spine with three slices.
        self.spine.write_text(
            "# Spine\n\n"
            "## Slice Register\n\n"
            "<!-- L:slice id=A status=SHIPPED sessions=1/1 plan=_ diary=_ updated=2026-06-01 -->\n"
            "<!-- L:slice id=B status=NEARBY sessions=0/2 plan=_ diary=_ updated=2026-06-01 -->\n"
            "<!-- L:slice id=C status=NEARBY sessions=0/2 plan=_ diary=_ updated=2026-06-01 -->\n",
            encoding="utf-8",
        )

    def test_RAN1_converts_named_slices_to_never(self):
        results = wd.retire_as_never(self.spine, ["B", "C"])
        self.assertEqual(len(results), 2)
        text = self.spine.read_text(encoding="utf-8")
        self.assertIn("id=B status=NEVER", text)
        self.assertIn("id=C status=NEVER", text)
        # A is unchanged
        self.assertIn("id=A status=SHIPPED", text)

    def test_RAN2_empty_id_list_no_op(self):
        before = self.spine.read_text(encoding="utf-8")
        results = wd.retire_as_never(self.spine, [])
        self.assertEqual(results, [])
        self.assertEqual(self.spine.read_text(encoding="utf-8"), before)

    def test_RAN3_after_never_all_slices_done_true_when_all_terminal(self):
        wd.retire_as_never(self.spine, ["B", "C"])
        # A=SHIPPED, B=NEVER, C=NEVER → terminal
        self.assertTrue(tm.all_slices_done(self.spine))


class CrashRecoveryResumeTests(unittest.TestCase):
    """End-to-end: write_intent → partial apply → re-read → resume → delete."""

    def test_CR1_partial_apply_resumes_and_completes(self):
        with _LedgerSandbox():
            sid, topic = "sid-resume", "topic-x"
            payload = {
                "todo": {"pattern": "Foo", "text": "- [x] Foo. **DONE 2026-06-01.**"},
                "slice_register": {"slice_id": "J", "status": "SHIPPED"},
                "sessions_log": {"bullet": "Shipped slice J."},
                "retire_marker": None,
            }
            # Step 7 — fresh write
            written = wdj.write_intent(sid, topic, payload)
            self.assertFalse(written["surfaces"]["todo"]["applied"])

            # Step 8.1 — apply TODO surface (simulated — just mark applied)
            wdj.mark_applied(sid, topic, "todo")

            # Step 8.2 — apply slice_register surface
            wdj.mark_applied(sid, topic, "slice_register")

            # SIMULATED CRASH: process dies. The journal on disk shows
            # todo + slice_register applied, sessions_log + retire_marker not.
            on_disk = wdj.read_intent(sid, topic)
            self.assertTrue(on_disk["surfaces"]["todo"]["applied"])
            self.assertTrue(on_disk["surfaces"]["slice_register"]["applied"])
            self.assertFalse(on_disk["surfaces"]["sessions_log"]["applied"])
            self.assertFalse(on_disk["surfaces"]["retire_marker"]["applied"])

            # RESUME: re-running /work-done. Step 7 sees an existing journal,
            # reads it, skips already-applied surfaces, applies the rest.
            resumed = wdj.read_intent(sid, topic)
            self.assertEqual(resumed["sid"], sid)
            # Apply remaining surfaces
            for surface in ("sessions_log", "retire_marker"):
                if not wdj.is_applied(sid, topic, surface):
                    wdj.mark_applied(sid, topic, surface)

            # All four now applied
            for surface in wdj.SURFACES:
                self.assertTrue(wdj.is_applied(sid, topic, surface))

            # Step 9 — append ledger + delete journal
            wd.append_completion_ledger(sid, topic, {"slice_id": "J", "todo_pattern": "Foo"})
            wdj.delete_intent(sid, topic)
            self.assertIsNone(wdj.read_intent(sid, topic))

            # The completion ledger preserves the durable record
            ledger = wd._ledger_path(sid, topic)
            self.assertTrue(ledger.exists())

    def test_CR2_fresh_write_after_existing_journal_raises(self):
        """write_intent on an existing journal raises — caller MUST resume, not overwrite."""
        with _LedgerSandbox():
            sid, topic = "sid-x", "topic-x"
            wdj.write_intent(sid, topic, {"todo": {"x": 1}})
            with self.assertRaises(FileExistsError):
                wdj.write_intent(sid, topic, {"todo": {"x": 2}})


class CloseOverlapGuardSmokeTests(unittest.TestCase):
    """A11 — /close Section 1b additive overlap guard reads the completion ledger."""

    def _read_patterns_for_session(self, ledger_dir: Path, sid: str) -> list[str]:
        """Mirror the inline python -c block in close/SKILL.md Section 1b."""
        patterns: set[str] = set()
        if not ledger_dir.exists():
            return []
        for f in ledger_dir.glob(f"{sid}__*.completed.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("session_id") == sid:
                    pat = row.get("todo_pattern")
                    if pat:
                        patterns.add(pat)
        return sorted(patterns)

    def test_OG1_empty_ledger_no_skips(self):
        with _LedgerSandbox():
            self.assertEqual(
                self._read_patterns_for_session(wd.LEDGER_DIR, "sid-1"),
                [],
            )

    def test_OG2_ledger_with_two_rows_yields_two_patterns(self):
        with _LedgerSandbox():
            wd.append_completion_ledger("sid-1", "topic-A",
                                        {"slice_id": "J", "todo_pattern": "FooItem"})
            wd.append_completion_ledger("sid-1", "topic-B",
                                        {"slice_id": "K", "todo_pattern": "BarItem"})
            # A different session's row must NOT leak into sid-1's skip set
            wd.append_completion_ledger("sid-2", "topic-C",
                                        {"slice_id": "L", "todo_pattern": "BazItem"})
            pats = self._read_patterns_for_session(wd.LEDGER_DIR, "sid-1")
            self.assertEqual(pats, ["BarItem", "FooItem"])

    def test_OG3_row_without_todo_pattern_is_skipped(self):
        with _LedgerSandbox():
            # Force-flag path with no pattern recorded
            wd.append_completion_ledger("sid-1", "topic-A",
                                        {"slice_id": "J", "todo_pattern": None})
            self.assertEqual(
                self._read_patterns_for_session(wd.LEDGER_DIR, "sid-1"),
                [],
            )


class WorkDoneCLISmokeTests(unittest.TestCase):
    """A8/A9/A10 — work_done.py CLI subcommand subprocess smokes."""

    def test_CLI1_session_locks_outputs_json(self):
        # In a clean env where no locks exist, output is empty JSON list.
        # We don't sandbox LOCKS_DIR through subprocess — we just assert the
        # output is parseable JSON (list). The empty/non-empty depends on the
        # caller's real lock state.
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "session-locks",
             "no-such-sid-aaaa-bbbb"],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        parsed = json.loads(res.stdout)
        self.assertIsInstance(parsed, list)
        # No lock should ever match this synthetic sid
        self.assertEqual(parsed, [])

    def test_CLI2_no_subcommand_exits_2(self):
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py")],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("Usage", res.stderr)

    def test_CLI3_resolve_slice_id_subcommand(self):
        tmp = Path(tempfile.mkdtemp(prefix="cli3_"))
        try:
            plan = tmp / "P.md"
            plan.write_text(
                "<!-- GATE0SR:SLICES -->\nslice_id: J\n<!-- /GATE0SR:SLICES -->\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                ["python3", str(HOOKS / "work_done.py"), "resolve-slice-id",
                 str(plan)],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "J")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_CLI4_append_ledger_subcommand(self):
        # The CLI writes to the module-default LEDGER_DIR (not sandboxed in
        # subprocess), so we use a unique sid + clean up the file after.
        sid = "test-cli4-aaaa-bbbb-cccc"
        topic = "topic-cli4"
        payload = json.dumps({"slice_id": "J", "todo_pattern": "Foo"})
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "append-ledger",
             sid, topic, payload],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        parsed = json.loads(res.stdout)
        self.assertEqual(parsed["status"], "appended")
        # Clean up the real ledger file we just wrote
        ledger_file = Path(parsed["path"])
        if ledger_file.exists():
            ledger_file.unlink()

    def test_CLI4_append_ledger_stdin_apostrophe(self):
        # harness-ledger-transport-fixture-hygiene: a payload value containing a
        # literal single-quote is delivered via stdin ("-"), never a shell arg, so
        # the apostrophe cannot break shell quoting.
        sid = "test-cli4-stdin-apos"
        topic = "topic-cli4-stdin"
        payload = json.dumps({"slice_id": "J", "todo_pattern": "don't ship"})
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "append-ledger", sid, topic, "-"],
            input=payload, capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        parsed = json.loads(res.stdout)
        self.assertEqual(parsed["status"], "appended")
        ledger_file = Path(parsed["path"])
        self.assertIn("don't ship", ledger_file.read_text())  # apostrophe survived
        if ledger_file.exists():
            ledger_file.unlink()

    def test_CLI4_append_ledger_file_apostrophe(self):
        sid = "test-cli4-file-apos"
        topic = "topic-cli4-file"
        payload = json.dumps({"slice_id": "J", "todo_pattern": "user's fix"})
        tmp = tempfile.mkdtemp()
        try:
            pf = Path(tmp) / "payload.json"
            pf.write_text(payload, encoding="utf-8")
            res = subprocess.run(
                ["python3", str(HOOKS / "work_done.py"), "append-ledger",
                 sid, topic, "--file", str(pf)],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            parsed = json.loads(res.stdout)
            self.assertEqual(parsed["status"], "appended")
            ledger_file = Path(parsed["path"])
            if ledger_file.exists():
                ledger_file.unlink()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_CLI4_append_ledger_empty_stdin_semantic_error(self):
        # Empty/whitespace payload → clear semantic error, not a raw JSONDecodeError.
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "append-ledger",
             "sid-x", "topic-x", "-"],
            input="   \n", capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("empty ledger payload", res.stderr)

    def test_CLI4_append_ledger_file_without_path_semantic_error(self):
        # --file with no trailing path arg → semantic error, not IndexError.
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "append-ledger",
             "sid-x", "topic-x", "--file"],
            capture_output=True, text=True,
        )
        self.assertEqual(res.returncode, 2)
        self.assertIn("--file requires a path", res.stderr)


class HarnessRepoForAttributionTests(unittest.TestCase):
    """A2 (project-tracking-staleness S8 / M3-secondary): resolve the harness
    repo to the config-source main checkout, not the frozen ~/.claude."""

    def test_resolves_config-source_source_main_checkout(self):
        import bookkeeping_resolver as br
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout="/fake/config-source/source\n", stderr="")
        with mock.patch("subprocess.run", return_value=ok), \
             mock.patch.object(br, "main_checkout", return_value="/fake/config-source/main"):
            self.assertEqual(wd.harness_repo_for_attribution(), "/fake/config-source/main")

    def test_none_on_config-source_failure(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="e")
        with mock.patch("subprocess.run", return_value=bad):
            self.assertIsNone(wd.harness_repo_for_attribution())

    def test_none_on_empty_source(self):
        empty = subprocess.CompletedProcess(args=[], returncode=0, stdout="\n", stderr="")
        with mock.patch("subprocess.run", return_value=empty):
            self.assertIsNone(wd.harness_repo_for_attribution())

    def test_none_on_resolver_error(self):
        import bookkeeping_resolver as br
        ok = subprocess.CompletedProcess(args=[], returncode=0,
                                         stdout="/x\n", stderr="")
        with mock.patch("subprocess.run", return_value=ok), \
             mock.patch.object(br, "main_checkout", side_effect=RuntimeError("boom")):
            self.assertIsNone(wd.harness_repo_for_attribution())

    def test_cli_smoke_never_errors(self):
        # Real config-source + resolver; prints a path or empty, always rc 0.
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "resolve-harness-repo"],
            capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)


class GroundTruthBlobTests(unittest.TestCase):
    """A3 (project-tracking-staleness S8 / M5): build_ground_truth_blob
    assembler over shipped producers, with per-section fail-safe."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gtblob_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _spine(self):
        s = self.tmp / "topic_THOUGHT.md"
        s.write_text(
            "# Idea\n\n## Slice Register\n"
            "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 plan=topic-1_PLAN diary=_ updated=2026-07-31 -->\n"
            "<!-- L:slice id=S2 status=NOW sessions=0/2 plan=_ diary=_ updated=2026-07-31 -->\n"
        )
        return s

    def test_blob_has_register_and_sequencing(self):
        blob = wd.build_ground_truth_blob(self._spine())
        self.assertIn("Slice Register", blob)
        self.assertIn("S1: status=SHIPPED", blob)
        self.assertIn("Sequencing", blob)
        self.assertIn("already shipped/terminal: S1", blob)
        self.assertIn("next unshipped registered slice: S2", blob)

    def test_slice_id_focus_in_header(self):
        self.assertIn("focus slice: S2",
                      wd.build_ground_truth_blob(self._spine(), slice_id="S2"))

    def test_per_section_fail_safe(self):
        # One producer raises; the blob must still return with that section
        # marked unavailable and the load-bearing register section intact.
        with mock.patch.object(wd, "_gt_ledger", side_effect=RuntimeError("boom")):
            blob = wd.build_ground_truth_blob(self._spine())
        self.assertIn("[unavailable — RuntimeError", blob)
        self.assertIn("S1: status=SHIPPED", blob)

    def test_cli_smoke(self):
        res = subprocess.run(
            ["python3", str(HOOKS / "work_done.py"), "ground-truth-blob",
             str(self._spine())],
            capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Slice Register", res.stdout)


class UnresolvedPlanRefCarryTests(unittest.TestCase):
    """S3 / A4 — a resolution failure survives all four narrowing points.

    A4's guard rail names them: `parse_impl_specifics_scope`/`_impl_specifics_scope_for`
    (`set[str]`), `_safe_call`/`attribute_commits_merged` (`list[str]`), and
    `_commits_present` (`bool`). Each discards everything except its own return shape,
    so a reference that could not be opened had four separate chances to vanish and took
    the first.

    The guard rail is also explicit that **`scope_empty` cannot carry this**: it is a
    bare bool AND is set only under `if not scope:`, so it is absent in exactly the
    partial-resolution case that becomes common once S2's resolver works. Two tests
    below pin that specifically.
    """

    def _spine(self, d: Path, rows: str) -> Path:
        spine = d / "t-20260101000000_THOUGHT.md"
        spine.write_text("# T\n\n## Slice Register\n\n" + rows, encoding="utf-8")
        return spine

    @staticmethod
    def _row(sid: str, plan: str) -> str:
        return (f"<!-- L:slice id={sid} status=SHIPPED sessions=1/1 "
                f"plan={plan} diary=_ updated=2026-01-01 -->\n")

    def test_point1_the_scope_read_carries_the_named_reference(self):
        import taskmanagement as tm
        with tempfile.TemporaryDirectory() as td:
            spine = self._spine(Path(td), self._row("S1", "[[lazy-doodling-wadler]]"))
            scope, unresolved = tm.impl_specifics_scope_with_unresolved(spine)
        self.assertEqual(scope, set())
        self.assertEqual(len(unresolved), 1)
        self.assertIn("lazy-doodling-wadler", unresolved[0][0])

    def test_point2_the_partial_case_reports_while_scope_is_NON_empty(self):
        """`scope_empty` is True only in the empty branch, so a partially-resolving
        spine — scope non-empty, one row dangling — reports nothing amiss unless
        `unresolved` is carried independently of it.

        Renamed: an earlier version was called "...attribute_commits carries it in BOTH
        branches" and never called `attribute_commits` at all. The name asserted a
        property the body did not check, which is the defect this whole plan is about.
        `attribute_commits` itself is now covered by the two tests below."""
        import taskmanagement as tm
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            (d / "t-20260101000000_S1_PLAN.md").write_text(
                "## Gate 1\n\n| c | c | `hooks/x.py` | c | c |\n", encoding="utf-8")
            spine = self._spine(
                d,
                self._row("S1", "[[t-20260101000000_S1_PLAN]]")
                + self._row("S2", "[[lazy-doodling-wadler]]"))
            scope, unresolved = tm.impl_specifics_scope_with_unresolved(spine)

        self.assertTrue(scope, "this spine DOES resolve some scope — that is the point")
        self.assertEqual(len(unresolved), 1,
                         "and it still has an unopenable reference to report")
        self.assertIn("lazy-doodling-wadler", unresolved[0][0])

    def test_attribute_commits_carries_unresolved_in_the_EMPTY_branch(self):
        import taskmanagement as tm
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            spine = self._spine(d, self._row("S1", "[[lazy-doodling-wadler]]"))
            res = tm.attribute_commits(
                plan_path=spine, session_id="s", started_at="1970-01-01",
                ended_at="2099-01-01", repo_path=d)
        self.assertIs(res["scope_empty"], True)
        self.assertEqual(len(res["unresolved"]), 1)
        self.assertIn("lazy-doodling-wadler", res["unresolved"][0][0])

    def test_attribute_commits_carries_unresolved_in_the_SUCCESS_branch(self):
        """The branch A4 says is the whole point, and which no test reached before:
        scope is non-empty so `scope_empty` is False and nothing else signals a
        problem, yet one reference could not be opened."""
        import subprocess
        import taskmanagement as tm
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            subprocess.run(["git", "init", "-q", str(d)], check=True)
            (d / "seed.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "-C", str(d), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(d), "-c", "user.email=t@t",
                            "-c", "user.name=t", "commit", "-qm", "seed"], check=True)
            (d / "t-20260101000000_S1_PLAN.md").write_text(
                "## Gate 1\n\n| c | c | `hooks/x.py` | c | c |\n", encoding="utf-8")
            spine = self._spine(
                d,
                self._row("S1", "[[t-20260101000000_S1_PLAN]]")
                + self._row("S2", "[[lazy-doodling-wadler]]"))
            res = tm.attribute_commits(
                plan_path=spine, session_id="s", started_at="1970-01-01",
                ended_at="2099-01-01", repo_path=d)
        self.assertIs(res["scope_empty"], False, "the success branch, not the empty one")
        self.assertEqual(len(res["unresolved"]), 1,
                         "scope_empty=False must NOT imply nothing went wrong")
        self.assertIn("lazy-doodling-wadler", res["unresolved"][0][0])

    def test_point4_the_gate_reason_names_the_specific_reference(self):
        """C3 + C4 together: the dangling case reads DIFFERENTLY from the no-scope case,
        and names the reference rather than only the spine."""
        import check_work_done_omission as cwo
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            spine = self._spine(d, self._row("S1", "[[lazy-doodling-wadler]]"))
            state = {"project_root": str(d), "thought_file_path": str(spine)}
            unresolved = cwo._unresolved_plan_refs("t", "Root", state)
        self.assertEqual(len(unresolved), 1)
        line = cwo._describe_unresolved(unresolved)
        self.assertIn("lazy-doodling-wadler", line)
        self.assertIn("could not open plan reference", line)

    def test_C4_the_two_cases_produce_different_reason_strings(self):
        """Both of these exist live: two spines reference plans absent from the tree,
        and `unit-economics-playground-generalization` carries two rows that are both
        `_`. Before S3 they produced the identical string.

        RENAMED. This was called `..._produce_DIFFERENT_operator_visible_text` while
        comparing two `_describe_unresolved` return values — no operator-visible surface
        is involved, so the name asserted more than the body checked. Its sibling
        finding was fixed by renaming and this one was left, which a reviewer rightly
        called out as inconsistent. The operator-visible property is asserted where it
        belongs, in `test_the_two_kinds_render_as_SEPARATE_sections` and
        `test_the_unresolved_reason_is_EMITTED_not_merely_computed`, both of which read
        the rendered `/close` block."""
        import check_work_done_omission as cwo
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            dangling = self._spine(d, self._row("S1", "[[lazy-doodling-wadler]]"))
            dangling_txt = cwo._describe_unresolved(
                cwo._unresolved_plan_refs(
                    "t", "Root",
                    {"project_root": str(d), "thought_file_path": str(dangling)}))

        with tempfile.TemporaryDirectory() as td2:
            d2 = Path(td2)
            placeholder = self._spine(d2, self._row("S1", "_") + self._row("S2", "_"))
            placeholder_txt = cwo._describe_unresolved(
                cwo._unresolved_plan_refs(
                    "t", "Root",
                    {"project_root": str(d2), "thought_file_path": str(placeholder)}))

        self.assertTrue(dangling_txt, "a dangling reference must say something")
        self.assertEqual(placeholder_txt, "",
                         "a spine of `_` rows declares NO scope — that is not a "
                         "resolution failure and must not be reported as one")
        self.assertNotEqual(dangling_txt, placeholder_txt)

    def test_the_unresolved_reason_is_EMITTED_not_merely_computed(self):
        """C3/C4's actual delivery, and the defect this test exists because of.

        The first version of S3 computed the reason inside `decide()` and emitted it
        NOWHERE: `main()` prints only when `block` is true and persists only when
        `report_only` is true, so a plain PASS carrying the reason returned 0 having
        said nothing to anybody. The two cases then differed only inside a dict that
        died with the process — which is verbatim the shape G5 diagnoses, fixed for C5
        and not carried across to C3/C4. An independent reviewer found it.

        So this asserts the END of the chain, not the middle: the record is written and
        the `/close` surface renders it. A test of `_describe_unresolved`'s return value
        would have passed throughout the period the feature did not work."""
        import contextlib
        import io
        import check_work_done_omission as cwo
        import work_done_report as wdr

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            spine = self._spine(d, self._row("S1", "[[lazy-doodling-wadler]]"))
            state = {"project_root": str(d), "thought_file_path": str(spine)}
            verdict = cwo._pass("could not determine scope"
                                + cwo._describe_unresolved(
                                    cwo._unresolved_plan_refs("t", "Root", state)))
            verdict["unresolved_report"] = True
            verdict["unresolved"] = cwo._unresolved_plan_refs("t", "Root", state)
            verdict["topic"], verdict["project"] = "t", "Root"

            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = str(d)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    cwo._emit_unresolved("sid-1", verdict)

                rows = wdr.read_reports(session_id="sid-1")
                self.assertEqual(len(rows), 1, "the reason must reach the record")
                self.assertEqual(rows[0]["kind"], "unresolved",
                                 "recorded as its own kind, not folded in with blocks")
                self.assertIn("lazy-doodling-wadler", rows[0]["message"])

                surface = wdr.render_close_block(session_id="sid-1")
                self.assertIn("could not be determined", surface.lower(),
                              "and must reach the /close surface")
                self.assertIn("SCOPE UNRESOLVED", err.getvalue())
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_decide_TAGS_it_and_main_EMITS_it_end_to_end(self):
        """Closes the link the previous test left untested.

        `test_the_unresolved_reason_is_EMITTED_not_merely_computed` hand-builds the
        verdict and calls `_emit_unresolved` directly, so deleting either the tagging
        lines in `decide()` or the `elif` in `main()` would restore the round-1 defect
        with a green suite. A reviewer pointed out that the sibling report-only path has
        a `main()`-level test and this one did not. This drives the whole chain:
        `decide()` -> tag -> `main()` -> record -> rendered surface."""
        import contextlib
        import io
        import check_work_done_omission as cwo
        import work_done_report as wdr

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            spine = self._spine(d, self._row("S1", "[[lazy-doodling-wadler]]"))
            state = {"project_root": str(d), "thought_file_path": str(spine)}

            saved = {}
            for name, val in {
                "_active_topic": lambda sid: ("t", "Root"),
                "_topic_state": lambda t, p: state,
                "_skipped_ledger_row": lambda sid, t: False,
                "_spine_has_session_block": lambda sid, st: False,
                "_lock_started_at": lambda t, p: "2026-01-01T00:00:00+00:00",
                "_commits_present": lambda t, p, st, s, e: False,
            }.items():
                saved[name] = getattr(cwo, name)
                setattr(cwo, name, val)

            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = str(d)

                # 1. decide() must TAG the verdict
                verdict = cwo.decide("sid-e2e")
                self.assertFalse(verdict["block"])
                self.assertTrue(verdict.get("unresolved_report"),
                                "decide() must tag it or main() cannot emit it")
                self.assertIn("lazy-doodling-wadler", verdict["reason"])

                # 2. main() must EMIT it, and not change the exit code
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = cwo.main(["sid-e2e"])
                self.assertEqual(rc, 0)

                # 3. it must reach the record and the rendered surface
                rows = wdr.read_reports(session_id="sid-e2e")
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0]["kind"], "unresolved")
                surface = wdr.render_close_block(session_id="sid-e2e")
                self.assertIn("could not be determined", surface.lower())
                self.assertIn("lazy-doodling-wadler", surface)
            finally:
                for name, val in saved.items():
                    setattr(cwo, name, val)
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_clear_reports_cannot_wipe_everything_by_default(self):
        """The destructive-default defect, pinned so it cannot return.

        `clear_reports` originally took `session_id=None` and, on that default, deleted
        every session's rows. There is now no call that expresses "delete everything":
        the argument is required and positional, and an unknown session removes nothing.
        Malformed rows must also survive, since rebuilding from the parsed view would
        destroy them."""
        import work_done_report as wdr

        with tempfile.TemporaryDirectory() as td:
            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = td
                wdr.append_report(session_id="keep-me", topic="a", project="Root",
                                  reason="r", message="m")
                wdr.append_report(session_id="drop-me", topic="b", project="Root",
                                  reason="r", message="m")
                with wdr.record_path().open("a", encoding="utf-8") as fh:
                    fh.write("{not json\n")

                with self.assertRaises(TypeError):
                    wdr.clear_reports()          # no argument list means "everything"

                self.assertEqual(wdr.clear_reports(""), 0)
                self.assertEqual(wdr.clear_reports("no-such-session"), 0)
                self.assertEqual(len(wdr.read_reports()), 2, "nothing removed yet")

                self.assertEqual(wdr.clear_reports("drop-me"), 1)
                left = wdr.read_reports()
                self.assertEqual(len(left), 1)
                self.assertEqual(left[0]["session_id"], "keep-me")
                raw = wdr.record_path().read_text(encoding="utf-8")
                self.assertIn("{not json", raw,
                              "an unparseable row must survive the rewrite")
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_the_two_kinds_render_as_SEPARATE_sections(self):
        """C4 at the surface, not in a dict. Collapsing them into one list would
        re-create at the operator's level the identity the code fix removed."""
        import work_done_report as wdr
        with tempfile.TemporaryDirectory() as td:
            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = td
                wdr.append_report(session_id="s", topic="a", project="Root",
                                  reason="would have blocked", message="m",
                                  kind="report_only")
                wdr.append_report(session_id="s", topic="b", project="Root",
                                  reason="could not determine scope", message="m",
                                  kind="unresolved")
                surface = wdr.render_close_block(session_id="s")
                self.assertIn("report only", surface.lower())
                self.assertIn("could not be determined", surface.lower())
                self.assertIn("it could not look", surface.lower(),
                              "the second section must say what it means")
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_unresolved_carry_is_fail_open(self):
        """This feeds a report on a fail-open gate; it must never raise."""
        import check_work_done_omission as cwo
        self.assertEqual(cwo._unresolved_plan_refs("t", "Root", {}), [])
        self.assertEqual(
            cwo._unresolved_plan_refs("t", "Root",
                                      {"thought_file_path": "/nope/missing.md"}), [])
        self.assertEqual(cwo._describe_unresolved([]), "")

    def test_describe_is_bounded(self):
        import check_work_done_omission as cwo
        many = [(f"[[ref-{i}]]", "nope") for i in range(9)]
        line = cwo._describe_unresolved(many)
        self.assertIn("ref-0", line)
        self.assertIn("+6 more", line)
        self.assertNotIn("ref-8", line, "a reason string is not a report")

    def test_describe_dedups_one_missing_file_referenced_many_times(self):
        """The real shape of both live dangling spines: every row points at the SAME
        missing file. Listing per-row would name it three times then say "(+4 more)",
        reading as seven distinct problems and sending the operator after six files
        that were never missing.

        Regression-guards a defect that only appeared against the real corpus — every
        fixture used distinct references and could not have surfaced it."""
        import check_work_done_omission as cwo
        same = [("[[lazy-doodling-wadler]]", "nope")] * 7
        line = cwo._describe_unresolved(same)
        self.assertEqual(line.count("lazy-doodling-wadler"), 1,
                         "one missing file is named once")
        self.assertIn("referenced by 7 rows", line)
        self.assertNotIn("more", line, "nothing is elided — there is only one file")
        self.assertIn("plan reference:", line, "singular, because there is one")

    def test_describe_counts_distinct_files_not_rows(self):
        import check_work_done_omission as cwo
        mixed = ([("[[a]]", "n")] * 3) + ([("[[b]]", "n")] * 2) + [("[[c]]", "n")]
        line = cwo._describe_unresolved(mixed, limit=2)
        self.assertIn("referenced by 3 rows", line)
        self.assertIn("+1 more", line, "one further FILE, not four further rows")
        self.assertIn("plan references:", line, "plural, because there are three")


if __name__ == "__main__":
    unittest.main(verbosity=2)

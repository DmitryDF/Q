#!/usr/bin/env python3
"""S1 (streamed-dancing-goose / A1) — the two `## Sessions` writers share one
section, one locator, one create path, and each owns only its own marked lines;
the work-done omission gate keys on the shipping writer's identity.

The fixtures here ARE A1's validation gate, one method per clause:

  F1  paired writers, same date + sid: `annotate_session` first, then
      `append_metrics` over it — the annotate bullet survives byte-identical;
      the reverse order and the `/close --annotate` (writer=close) case too.
  F2  no `## Sessions`: both writers create it in the SAME place, outside
      `# Discovery`, and a re-run creates nothing further. The locked-fields
      hash is unchanged by the create.
  F3  Discovery runs to end-of-file: the create path opens a top-level boundary
      and places `## Sessions` beneath it — never inside Discovery; hash unchanged.
  F4  `## Sessions Plan` precedes `## Sessions`: every one of the four sites —
      both writers, the omission gate, the Phase-Register anchor — binds the
      real section.
  F5  the omission gate: a `/close`-authored block alone (metrics rows, or a
      `--annotate` bullet under writer=close) does NOT satisfy it; a `/work-done`
      entry does; the legacy bare tag still does.
  F6  a lock-contended run of either CLI verb reports `✗ …` + exit 1 rather
      than crashing on a traceback.

Run: env CLAUDE_CONFIG_DIR=<clone> python3 hooks/tests/test_s1_session_writers.py
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg  # noqa: E402
import check_work_done_omission as cwd  # noqa: E402
import bookkeeping_lock as bkl  # noqa: E402

SID = "abcd1234-s1-session-writers-000000000"
SID8 = SID[:8]
TODAY = datetime.now().date().isoformat()
HEADER = f"### {TODAY} session [sid:{SID8}]"

WD = ppg.SESSION_WRITER_WORK_DONE
CL = ppg.SESSION_WRITER_CLOSE

PAYLOAD_A = {"duration_min": 30, "opus_tokens_k": 100, "tasks_completed": 3}
PAYLOAD_B = {"duration_min": 61, "opus_tokens_k": 120, "sonnet_tokens_k": 7,
             "tasks_completed": 9, "files_touched": ["a.py", "b.py"]}

# A new-template spine: # Idea → # Discovery (four locked fields) → # Solution
# Design → # Implementation Details (## Next Session Prompt). No ## Sessions.
NEW_TEMPLATE = """# Idea

## Problem
Something.

# Discovery

## Guiding Policy
gp body

## Desired Outcome
do body

## Desired Solution
ds body

## Metrics
OMTM: x

## Q&A
q body

# Solution Design

_(pending)_

# Implementation Details

## Next Session Prompt

paste me
"""

# An older-layout spine whose # Discovery runs to end-of-file. No ## Sessions.
DISCOVERY_TO_EOF = """# Old Topic — Thought File

**Status:** active.

# Discovery

## Guiding Policy
gp body

## Desired Outcome
do body

## Desired Solution
ds body

## Metrics
OMTM: x
"""

# `## Sessions Plan` BEFORE the real `## Sessions` (the shape of two live spines).
SESSIONS_PLAN_FIRST = """# Topic — Thought File

## Sessions Plan

- Session 1: do the thing.
- Session 2: do the other thing.

## Sessions

*Updated at each /close.*

## Next Session Prompt

paste me
"""

SIMPLE = """# Synthetic Spine

## Sessions

*Updated at each /close.*
"""


class _Bound:
    """Bind SID to an absolute spine path by patching `_resolve_topic` — the
    absolute path makes `_resolve_thought_path` skip the resolver, so the
    writers are exercised on the fixture file with no live state touched."""

    def __init__(self, spine: Path, extra_state=None):
        self.spine = spine
        self.state = {"thought_file_path": str(spine), "phase": "implementation"}
        if extra_state:
            self.state.update(extra_state)

    def __enter__(self):
        self._orig = ppg._resolve_topic
        ppg._resolve_topic = lambda sid: ("Root", "s1-topic", self.state)
        return self

    def __exit__(self, *exc):
        ppg._resolve_topic = self._orig


def _sessions_region(text: str) -> str:
    lines = text.split("\n")
    sec = ppg.find_sessions_section(lines)
    assert sec is not None, "no ## Sessions section"
    return "\n".join(lines[sec[0]:sec[1]])


def _line_with(text: str, needle: str) -> str:
    hits = [ln for ln in text.split("\n") if needle in ln]
    assert len(hits) == 1, f"expected exactly one line containing {needle!r}, got {hits}"
    return hits[0]


class S1Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s1_writers_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # Keep the ledger writer from touching anything real.
        self._orig_ledger = ppg._record_ledger_write
        ppg._record_ledger_write = lambda p: None
        self.addCleanup(setattr, ppg, "_record_ledger_write", self._orig_ledger)

    def spine(self, body: str, name="s1_THOUGHT.md") -> Path:
        p = self.tmp / name
        p.write_text(body, encoding="utf-8")
        return p


# --------------------------------------------------------------------------- #
# F1 — paired writers share one block; each replaces only its own lines
# --------------------------------------------------------------------------- #
class F1PairedWriters(S1Base):

    def test_annotate_then_metrics_bullet_survives_byte_identical(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            r1 = ppg.annotate_session(SID, "Shipped S1 of the plan.")
            self.assertEqual(r1["status"], "heading_and_bullet_appended")
            before = p.read_text(encoding="utf-8")
            bullet = _line_with(before, "Shipped S1 of the plan.")
            self.assertEqual(ppg.annotate_marker_writer(bullet), WD)

            r2 = ppg.append_metrics(SID, PAYLOAD_A)
            self.assertEqual(r2["status"], "metrics_appended")
            after = p.read_text(encoding="utf-8")
            self.assertEqual(after.count(HEADER), 1, "one shared block")
            self.assertEqual(_line_with(after, "Shipped S1 of the plan."), bullet)
            self.assertIn(f"- Duration: 30min {ppg._CLOSE_METRICS_MARKER}", after)

            # A same-day re-run of /close replaces only its own rows.
            r3 = ppg.append_metrics(SID, PAYLOAD_B)
            self.assertEqual(r3["status"], "metrics_replaced")
            again = p.read_text(encoding="utf-8")
            self.assertEqual(again.count(HEADER), 1)
            self.assertEqual(_line_with(again, "Shipped S1 of the plan."), bullet)
            self.assertNotIn("Duration: 30min", again)
            self.assertIn("Duration: 61min", again)
            self.assertIn("Files touched: a.py, b.py", again)
            # every metrics row carries the writer marker, and nothing else does
            region = _sessions_region(again)
            for ln in region.split("\n"):
                if ln.startswith("- ") and "annotate-session" not in ln:
                    self.assertIn(ppg._CLOSE_METRICS_MARKER, ln, ln)

    def test_metrics_then_annotate_rows_survive(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD_A)
            rows_before = [ln for ln in p.read_text(encoding="utf-8").split("\n")
                           if ppg._CLOSE_METRICS_MARKER in ln]
            r = ppg.annotate_session(SID, "Shipped after close wrote.")
            self.assertEqual(r["status"], "bullet_appended")
            text = p.read_text(encoding="utf-8")
            rows_after = [ln for ln in text.split("\n") if ppg._CLOSE_METRICS_MARKER in ln]
            self.assertEqual(rows_before, rows_after)
            self.assertEqual(text.count(HEADER), 1)
            # re-run replaces the bullet, rows untouched
            ppg.annotate_session(SID, "Shipped after close wrote, v2.")
            text2 = p.read_text(encoding="utf-8")
            self.assertNotIn("Shipped after close wrote.", text2)
            self.assertEqual(rows_after,
                             [ln for ln in text2.split("\n") if ppg._CLOSE_METRICS_MARKER in ln])

    def test_close_annotate_coexists_with_work_done_bullet(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            ppg.annotate_session(SID, "Work-done record.", writer=WD)
            ppg.annotate_session(SID, "Close note.", writer=CL)
            text = p.read_text(encoding="utf-8")
            self.assertEqual(text.count(HEADER), 1)
            self.assertEqual(ppg.annotate_marker_writer(_line_with(text, "Work-done record.")), WD)
            self.assertEqual(ppg.annotate_marker_writer(_line_with(text, "Close note.")), CL)
            # each writer replaces only its own bullet
            ppg.annotate_session(SID, "Close note v2.", writer=CL)
            text2 = p.read_text(encoding="utf-8")
            self.assertIn("Work-done record.", text2)
            self.assertNotIn("Close note.", text2)
            self.assertIn("Close note v2.", text2)
            ppg.annotate_session(SID, "Work-done record v2.", writer=WD)
            text3 = p.read_text(encoding="utf-8")
            self.assertIn("Close note v2.", text3)
            self.assertNotIn("Work-done record.", text3)

    def test_legacy_bare_tag_is_replaced_by_work_done_not_close(self):
        prior = f"{HEADER}\n- Legacy bullet. {ppg._ANNOTATE_MARKER}\n"
        p = self.spine(SIMPLE + "\n" + prior)
        with _Bound(p):
            ppg.annotate_session(SID, "Close note.", writer=CL)
            text = p.read_text(encoding="utf-8")
            self.assertIn("Legacy bullet.", text)          # close did not touch it
            ppg.annotate_session(SID, "Fresh work-done bullet.", writer=WD)
            text2 = p.read_text(encoding="utf-8")
            self.assertNotIn("Legacy bullet.", text2)      # work-done replaced its own
            self.assertIn("Fresh work-done bullet.", text2)
            self.assertIn("Close note.", text2)

    def test_unknown_writer_refused(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            with self.assertRaises(ValueError):
                ppg.annotate_session(SID, "Some bullet.", writer="nobody")


# --------------------------------------------------------------------------- #
# F2 — create-if-absent: one shared path, outside Discovery, idempotent
# --------------------------------------------------------------------------- #
class F2CreateIfAbsent(S1Base):

    def _assert_outside_discovery(self, text: str):
        lines = text.split("\n")
        sec = ppg.find_sessions_section(lines)
        self.assertIsNotNone(sec)
        disc = ppg._discovery_h1_bounds(lines)
        self.assertIsNotNone(disc)
        self.assertFalse(disc[0] < sec[0] < disc[1], "## Sessions landed inside # Discovery")

    def test_both_writers_create_in_the_same_place(self):
        pa = self.spine(NEW_TEMPLATE, "a_THOUGHT.md")
        pb = self.spine(NEW_TEMPLATE, "b_THOUGHT.md")
        h_before = ppg._discovery_locked_fields_hash(NEW_TEMPLATE)
        self.assertIsNotNone(h_before)
        with _Bound(pa):
            ra = ppg.annotate_session(SID, "Created by annotate.")
        with _Bound(pb):
            rb = ppg.append_metrics(SID, PAYLOAD_A)
        self.assertTrue(ra["section_created"] and rb["section_created"])
        ta, tb = pa.read_text(encoding="utf-8"), pb.read_text(encoding="utf-8")
        for t in (ta, tb):
            self._assert_outside_discovery(t)
            self.assertEqual(ppg._discovery_locked_fields_hash(t), h_before)
            self.assertEqual(len(ppg._SESSIONS_HEADING_RE.findall(t)), 1)
            # under # Implementation Details, before ## Next Session Prompt
            self.assertLess(t.index("# Implementation Details"), t.index("## Sessions"))
            self.assertLess(t.index("## Sessions"), t.index("## Next Session Prompt"))
        self.assertEqual(ppg.find_sessions_section(ta.split("\n"))[0],
                         ppg.find_sessions_section(tb.split("\n"))[0])

    def test_rerun_creates_nothing_further(self):
        p = self.spine(NEW_TEMPLATE)
        with _Bound(p):
            ppg.annotate_session(SID, "First.")
            r2 = ppg.append_metrics(SID, PAYLOAD_A)
            r3 = ppg.annotate_session(SID, "Second.")
        self.assertFalse(r2["section_created"])
        self.assertFalse(r3["section_created"])
        t = p.read_text(encoding="utf-8")
        self.assertEqual(len(ppg._SESSIONS_HEADING_RE.findall(t)), 1)
        self.assertEqual(t.count(HEADER), 1)
        self.assertEqual(t.count("# Implementation Details"), 1)

    def test_next_session_prompt_section_still_readable_after_create(self):
        p = self.spine(NEW_TEMPLATE)
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD_A)
        t = p.read_text(encoding="utf-8")
        self.assertIn("paste me", ppg.next_session_prompt_section(t))


# --------------------------------------------------------------------------- #
# F3 — Discovery runs to end-of-file: open a top-level boundary first
# --------------------------------------------------------------------------- #
class F3DiscoveryToEOF(S1Base):

    def test_create_opens_boundary_and_leaves_locked_hash_alone(self):
        h_before = ppg._discovery_locked_fields_hash(DISCOVERY_TO_EOF)
        self.assertIsNotNone(h_before)
        for writer in ("annotate", "metrics"):
            p = self.spine(DISCOVERY_TO_EOF, f"{writer}_THOUGHT.md")
            with _Bound(p):
                if writer == "annotate":
                    ppg.annotate_session(SID, "Created past Discovery.")
                else:
                    ppg.append_metrics(SID, PAYLOAD_A)
            t = p.read_text(encoding="utf-8")
            lines = t.split("\n")
            disc = ppg._discovery_h1_bounds(lines)
            sec = ppg.find_sessions_section(lines)
            self.assertLess(disc[1], len(lines), "Discovery still runs to EOF")
            self.assertGreaterEqual(sec[0], disc[1], "## Sessions inside Discovery")
            self.assertEqual(lines[disc[1]].rstrip(), "# Implementation Details")
            self.assertEqual(ppg._discovery_locked_fields_hash(t), h_before, writer)
            # the locked Metrics body did not absorb the new section
            body = ppg.discovery_section_body(t)
            self.assertNotIn("## Sessions", body)

    def test_create_path_asserts_outside_discovery(self):
        # Direct unit check of the assertion: the returned index is never inside Discovery.
        lines = DISCOVERY_TO_EOF.split("\n")
        new_lines, idx, created = ppg.ensure_sessions_section(lines)
        self.assertTrue(created)
        disc = ppg._discovery_h1_bounds(new_lines)
        self.assertFalse(disc[0] < idx < disc[1])
        # idempotent
        again, idx2, created2 = ppg.ensure_sessions_section(new_lines)
        self.assertFalse(created2)
        self.assertEqual(idx, idx2)
        self.assertEqual(again, new_lines)


# --------------------------------------------------------------------------- #
# F4 — `## Sessions Plan` before `## Sessions`: all four sites bind the real one
# --------------------------------------------------------------------------- #
class F4SessionsPlanDecoy(S1Base):

    def _plan_region(self, text: str) -> str:
        lines = text.split("\n")
        start = next(i for i, ln in enumerate(lines) if ln.startswith("## Sessions Plan"))
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        return "\n".join(lines[start:end])

    def test_annotate_binds_real_section(self):
        p = self.spine(SESSIONS_PLAN_FIRST)
        with _Bound(p):
            ppg.annotate_session(SID, "Under the real section.")
        t = p.read_text(encoding="utf-8")
        self.assertNotIn(HEADER, self._plan_region(t))
        self.assertIn(HEADER, _sessions_region(t))
        self.assertLess(t.index("## Sessions\n"), t.index(HEADER))
        self.assertLess(t.index(HEADER), t.index("## Next Session Prompt"))

    def test_metrics_binds_real_section(self):
        p = self.spine(SESSIONS_PLAN_FIRST)
        with _Bound(p):
            r = ppg.append_metrics(SID, PAYLOAD_A)
        self.assertFalse(r["section_created"])
        t = p.read_text(encoding="utf-8")
        self.assertNotIn(HEADER, self._plan_region(t))
        self.assertIn(HEADER, _sessions_region(t))
        self.assertEqual(len(ppg._SESSIONS_HEADING_RE.findall(t)), 1)

    def test_gate_binds_real_section(self):
        decoy = (SESSIONS_PLAN_FIRST.replace(
            "## Sessions Plan\n",
            f"## Sessions Plan\n\n{HEADER}\n- Shipped. {ppg.annotate_marker(WD)}\n"))
        self.assertFalse(cwd._work_done_record_in(decoy, SID), "decoy section counted")
        real = SESSIONS_PLAN_FIRST.replace(
            "*Updated at each /close.*\n",
            f"*Updated at each /close.*\n\n{HEADER}\n- Shipped. {ppg.annotate_marker(WD)}\n")
        self.assertTrue(cwd._work_done_record_in(real, SID))

    def test_phase_register_anchor_binds_real_section(self):
        p = self.spine(SESSIONS_PLAN_FIRST)
        with _Bound(p):
            res = ppg.write_phase_marker(SID, "start", "implementation")
        self.assertEqual(res["status"], "section_created")
        t = p.read_text(encoding="utf-8")
        reg = t.index("## Phase Register")
        self.assertLess(t.index("## Sessions Plan"), reg, "anchored before the decoy")
        self.assertLess(reg, t.index("## Sessions\n"))

    def test_phase_register_anchor_accepts_numbered_form(self):
        # The fourth site used to compare exact strings; the shared regex accepts `## 3. Sessions`.
        p = self.spine("# Topic\n\n## 3. Sessions\n\n### x [sid:abc]\n")
        with _Bound(p):
            ppg.write_phase_marker(SID, "start", "implementation")
        t = p.read_text(encoding="utf-8")
        self.assertLess(t.index("## Phase Register"), t.index("## 3. Sessions"))

    def test_h1_terminates_existing_phase_register_section(self):
        # An H1 directly after `## Phase Register` (no intervening `## `
        # heading) used to be invisible to the section-end scan, which looked
        # for lines starting with `## ` only — so the scan ran straight through
        # `# Discovery` and stopped at the first `## ` heading INSIDE Discovery,
        # landing a new phase row in the locked Discovery region instead of
        # ending the Phase Register section at the H1.
        spine = (
            "# Topic\n\n"
            "## Phase Register\n\n"
            "<!-- L:phase phase=design event=start at=2026-01-01 "
            "session=abcd1234 updated=2026-01-01 -->\n\n"
            "# Discovery\n\n"
            "## Guiding Policy\n"
            "gp body\n"
        )
        p = self.spine(spine)
        with _Bound(p):
            res = ppg.write_phase_marker(SID, "start", "implementation")
        self.assertEqual(res["status"], "row_appended")
        t = p.read_text(encoding="utf-8")
        discovery_idx = t.index("# Discovery")
        new_row_idx = t.index("phase=implementation")
        gp_idx = t.index("## Guiding Policy")
        self.assertLess(
            new_row_idx, discovery_idx,
            "new phase row must land before the H1, not inside Discovery")
        self.assertLess(discovery_idx, gp_idx)


# --------------------------------------------------------------------------- #
# F5 — the omission gate keys on the shipping writer's identity
# --------------------------------------------------------------------------- #
class F5OmissionGateReKey(S1Base):

    def _state(self, p: Path):
        return {"thought_file_path": str(p), "project_root": None}

    def test_close_block_alone_does_not_satisfy(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD_A)
        self.assertIn(HEADER, p.read_text(encoding="utf-8"))
        self.assertFalse(cwd._spine_has_session_block(SID, self._state(p)))

    def test_close_annotate_alone_does_not_satisfy(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD_A)
            ppg.annotate_session(SID, "Thinking-only session note.", writer=CL)
        self.assertFalse(cwd._spine_has_session_block(SID, self._state(p)))

    def test_work_done_entry_satisfies_even_beside_close_block(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD_A)
            ppg.annotate_session(SID, "Close note.", writer=CL)
            self.assertFalse(cwd._spine_has_session_block(SID, self._state(p)))
            ppg.annotate_session(SID, "Shipped S1.", writer=WD)
        self.assertTrue(cwd._spine_has_session_block(SID, self._state(p)))

    def test_legacy_bare_tag_satisfies(self):
        p = self.spine(SIMPLE + f"\n{HEADER}\n- Old record. {ppg._ANNOTATE_MARKER}\n")
        self.assertTrue(cwd._spine_has_session_block(SID, self._state(p)))

    def test_date_independent_within_section(self):
        p = self.spine(SIMPLE + f"\n### 2000-01-01 session [sid:{SID8}]\n"
                                f"- Shipped before midnight. {ppg.annotate_marker(WD)}\n")
        self.assertTrue(cwd._spine_has_session_block(SID, self._state(p)))

    def test_decide_passes_on_work_done_record(self):
        """End-to-end through `decide`: with an active topic and a work-done record
        the gate short-circuits to PASS before consulting the lock or git."""
        p = self.spine(SIMPLE)
        with _Bound(p):
            ppg.annotate_session(SID, "Shipped S1.", writer=WD)
        saved = {}
        for name, val in {
            "_active_topic": lambda sid: ("s1-topic", "Root"),
            "_topic_state": lambda t, pr: self._state(p),
            "_skipped_ledger_row": lambda sid, t: False,
            "_lock_started_at": lambda t, pr: (_ for _ in ()).throw(AssertionError("lock consulted")),
        }.items():
            saved[name] = getattr(cwd, name)
            setattr(cwd, name, val)
        try:
            v = cwd.decide(SID)
        finally:
            for name, val in saved.items():
                setattr(cwd, name, val)
        self.assertFalse(v["block"])
        self.assertIn("already recorded", v["reason"])


# --------------------------------------------------------------------------- #
# F6 — a lock-contended run reports rather than crashing
# --------------------------------------------------------------------------- #
class F6LockContention(S1Base):

    def _run_cli(self, argv):
        @contextlib.contextmanager
        def contended(target, **kw):
            raise bkl.BookkeepingLockTimeout("could not acquire bookkeeping lock (test)")
            yield  # pragma: no cover
        orig_lock, orig_argv = bkl.bookkeeping_lock, sys.argv
        bkl.bookkeeping_lock = contended
        sys.argv = ["pre_plan_gates.py", *argv]
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    ppg.main()
        finally:
            bkl.bookkeeping_lock, sys.argv = orig_lock, orig_argv
        return cm.exception.code, err.getvalue()

    def test_append_metrics_reports(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            code, err = self._run_cli(["append-metrics", SID, '{"duration_min": 1}'])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("✗"), err)
        self.assertIn("bookkeeping lock", err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(p.read_text(encoding="utf-8"), SIMPLE, "nothing written")

    def test_annotate_session_reports(self):
        p = self.spine(SIMPLE)
        with _Bound(p):
            code, err = self._run_cli(["annotate-session", SID, "A bullet.", "--writer", "close"])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("✗"), err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(p.read_text(encoding="utf-8"), SIMPLE)

    def test_annotate_cli_writer_flag_reaches_the_file(self):
        p = self.spine(SIMPLE)
        orig_argv = sys.argv
        sys.argv = ["pre_plan_gates.py", "annotate-session", SID, "Close CLI note.", "--writer", "close"]
        try:
            with _Bound(p), contextlib.redirect_stdout(io.StringIO()):
                try:
                    ppg.main()
                except SystemExit as e:  # main() may not exit on success
                    self.assertEqual(e.code, 0)
        finally:
            sys.argv = orig_argv
        t = p.read_text(encoding="utf-8")
        self.assertEqual(ppg.annotate_marker_writer(_line_with(t, "Close CLI note.")), CL)


if __name__ == "__main__":
    unittest.main(verbosity=1)

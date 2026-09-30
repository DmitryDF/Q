#!/usr/bin/env python3
"""Tests for the framing-obligation surface (auto-registration S-C / A3 + A4).

A3 — `framing_obligation.py`:
  F1   scan finds marked OPEN lines only (a DONE line carrying the marker is not
       an obligation)
  F2   a line that now passes the framing bar is DISCHARGED — its marker is
       stripped, the line itself survives
  F3   a line that still fails is reported outstanding and keeps its marker
  F4   discharge is permanent: a second reconcile discharges nothing new and the
       framed line is no longer reported
  F5   the discharge bar is EXACTLY `todo.cmd_validate_framing` — the same bar a
       manual [Thought] line passes, not a weaker one
  F6   fail-open: missing file, unreadable file, no marked lines
  F7   `--no-strip` reports without mutating
  F8   render_surface returns None when nothing is outstanding, and names the
       file+line when something is
  F9   the strip is a TARGETED line edit — other lines are untouched byte-for-byte
  F10  an auto-registered line minted by the real minter is recognised end-to-end

A4 — `check_work_done_omission.py` registration-aware arm:
  O1   unbound + no discoverable anchoring artifact -> PASS (refuse-to-guess)
  O2   unbound + artifact but no session window -> PASS (fail-safe)
  O3   unbound + artifact + window but no in-window commits -> PASS
  O4   unbound + artifact + window + commits -> REPORT-ONLY pass; the carried
       would_block_message still names the artifact and the auto-register
       remediation (report-only since slice-register-plan-ref-resolution S1)
  O4b  same, with REPORT_ONLY flipped off -> BLOCK (this arm's block path is
       unreached, not deleted)
  O5   the arm never raises — an internal error still PASSes
  O6   a BOUND session is unaffected by the new arm

Run: python3 ${KIT_HOOKS_DIR}/tests/test_framing_obligation.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import framing_obligation as fo          # noqa: E402
import check_work_done_omission as cwo   # noqa: E402
import todo as todo_mod                  # noqa: E402


FRAMED = ("- [ ] [Thought] [auto-registered] [a-20260101000000] **A** — "
          "Problem: p. Context: c. Guiding policy: g. "
          "Master plan: [[a-20260101000000_PLAN]].")
UNFRAMED = ("- [ ] [Thought] [auto-registered] [b-20260101000000] **B** — "
            "auto-registered at ship; framing incomplete. "
            "Master plan: [[b-20260101000000_PLAN]].")
DONE_MARKED = ("- [x] [Thought] [auto-registered] [c-20260101000000] **C** — "
               "**DONE 2026-01-02.**")
PLAIN = "- [ ] an ordinary unrelated task"


class FramingObligationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="fo_"))
        self.todo = self.tmp / "TODO.md"
        self.todo.write_text(
            "# TODO\n\n## Now\n\n"
            + "\n".join([PLAIN, FRAMED, UNFRAMED, DONE_MARKED]) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_F1_scan_finds_marked_open_lines_only(self):
        rows = fo.scan(self.todo)
        self.assertEqual(len(rows), 2, msg=rows)
        bodies = " ".join(r["body"] for r in rows)
        self.assertIn("[a-20260101000000]", bodies)
        self.assertIn("[b-20260101000000]", bodies)
        self.assertNotIn("[c-20260101000000]", bodies,
                         msg="a DONE line is not an outstanding obligation")

    def test_F2_framed_line_is_discharged_and_survives(self):
        res = fo.reconcile([str(self.todo)])
        self.assertEqual(len(res["discharged"]), 1)
        after = self.todo.read_text()
        self.assertIn("[a-20260101000000]", after, "the line must survive")
        self.assertNotIn(f"{fo.MARKER} [a-20260101000000]", after)

    def test_F3_unframed_line_is_outstanding_and_keeps_its_marker(self):
        res = fo.reconcile([str(self.todo)])
        self.assertEqual(len(res["outstanding"]), 1)
        self.assertIn("[b-20260101000000]", res["outstanding"][0]["body"])
        after = self.todo.read_text()
        unframed_line = next(ln for ln in after.splitlines()
                             if "[b-20260101000000]" in ln)
        self.assertIn(fo.MARKER, unframed_line)

    def test_F4_discharge_is_permanent(self):
        fo.reconcile([str(self.todo)])
        res2 = fo.reconcile([str(self.todo)])
        self.assertEqual(res2["discharged"], [])
        self.assertEqual(len(res2["outstanding"]), 1)
        self.assertNotIn("[a-20260101000000]",
                         " ".join(r["body"] for r in res2["outstanding"]))

    def test_F5_bar_is_exactly_cmd_validate_framing(self):
        # Not a weaker bar: the same validator, same verdicts.
        rows = {r["body"].split("]")[1]: r for r in fo.scan(self.todo)}
        for r in fo.scan(self.todo):
            expected = todo_mod.cmd_validate_framing(r["body"])["status"] == "pass"
            self.assertEqual(r["framed"], expected, msg=r["body"])

    def test_F6_fail_open_on_missing_and_unreadable(self):
        self.assertEqual(fo.scan(self.tmp / "nope.md"), [])
        self.assertEqual(fo.reconcile([str(self.tmp / "nope.md")])["scanned"], 0)
        bad = self.tmp / "bad.md"
        bad.write_bytes(b"- [ ] [auto-registered] \xff\xfe\n")
        self.assertEqual(fo.scan(bad), [])
        empty = self.tmp / "empty.md"
        empty.write_text("# TODO\n\n## Now\n\n_(nothing)_\n")
        res = fo.reconcile([str(empty)])
        self.assertEqual(res["scanned"], 0)
        self.assertIsNone(fo.render_surface(res))

    def test_F7_no_strip_reports_without_mutating(self):
        # strip=False is a read-only pass: a framed line is reported under
        # would_discharge, NEVER as an accomplished discharge.
        before = self.todo.read_text()
        res = fo.reconcile([str(self.todo)], strip=False)
        self.assertEqual(res["discharged"], [])
        self.assertEqual(len(res["would_discharge"]), 1)
        self.assertEqual(self.todo.read_text(), before)

    def test_F11_failed_strip_is_not_reported_as_discharged(self):
        # The report must never claim a discharge that did not happen.
        with mock.patch.object(fo, "_strip_marker_in_file", return_value=set()):
            res = fo.reconcile([str(self.todo)])
        self.assertEqual(res["discharged"], [])
        self.assertEqual(len(res["outstanding"]), 2)
        self.assertTrue(any(r.get("strip_failed") for r in res["outstanding"]))
        self.assertIn(fo.MARKER, self.todo.read_text())

    def test_F12_all_marker_occurrences_on_a_line_are_stripped(self):
        # A line carrying the marker twice must not survive a "successful" strip
        # and be re-flagged forever.
        doubled = FRAMED.replace("[Thought]", "[Thought] " + fo.MARKER)
        t = self.tmp / "double.md"
        t.write_text("# TODO\n\n## Now\n\n" + doubled + "\n")
        self.assertEqual(t.read_text().count(fo.MARKER), 2)
        res = fo.reconcile([str(t)])
        self.assertEqual(len(res["discharged"]), 1)
        self.assertEqual(t.read_text().count(fo.MARKER), 0)
        self.assertEqual(fo.reconcile([str(t)])["scanned"], 0)

    def test_F13_crlf_terminators_are_preserved(self):
        t = self.tmp / "crlf.md"
        t.write_bytes(("# TODO\r\n\r\n## Now\r\n\r\n" + PLAIN + "\r\n"
                       + FRAMED + "\r\n" + UNFRAMED + "\r\n").encode("utf-8"))
        fo.reconcile([str(t)])
        raw = t.read_bytes()
        self.assertNotIn(b"\n\n\n", raw)
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"),
                         msg="every terminator must still be CRLF")

    def test_F13b_marker_as_last_token_does_not_eat_the_terminator(self):
        # `\s?` after the marker would swallow the line's own \r (or \n).
        line = ("- [ ] [Thought] [z-20260101000000] **Z** — Problem: p. "
                "Context: c. Guiding policy: g. "
                "Master plan: [[z-20260101000000_PLAN]]. " + fo.MARKER)
        t = self.tmp / "last.md"
        t.write_bytes(("# TODO\r\n\r\n## Now\r\n\r\n" + line + "\r\n"
                       + PLAIN + "\r\n").encode("utf-8"))
        fo.reconcile([str(t)])
        raw = t.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        self.assertNotIn(fo.MARKER.encode(), raw)

    def test_F14_no_trailing_newline_is_not_invented(self):
        t = self.tmp / "notrail.md"
        t.write_text("# TODO\n\n## Now\n\n" + FRAMED)   # no trailing \n
        fo.reconcile([str(t)])
        self.assertFalse(t.read_text().endswith("\n"),
                         msg="a missing trailing newline must not be added")

    def test_F15_duplicate_paths_do_not_double_count(self):
        res = fo.reconcile([str(self.todo), str(self.todo),
                            str(self.todo.resolve())])
        self.assertEqual(len(res["outstanding"]), 1)
        self.assertEqual(len(res["discharged"]), 1)

    def test_F16_unlocked_fallback_is_guarded(self):
        # When bookkeeping_lock cannot be imported, the fallback path must still
        # not raise to the caller.
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "bookkeeping_lock":
                raise ImportError("no lock module")
            return real_import(name, *a, **k)

        with mock.patch.object(builtins, "__import__", side_effect=fake_import):
            res = fo.reconcile([str(self.todo)])
        self.assertEqual(len(res["discharged"]), 1)
        self.assertNotIn(f"{fo.MARKER} [a-20260101000000]", self.todo.read_text())

    def test_F8_render_surface_shape(self):
        self.assertIsNone(fo.render_surface({"outstanding": []}))
        block = fo.render_surface(fo.reconcile([str(self.todo)]))
        self.assertIsNotNone(block)
        self.assertIn("TODO.md:", block)
        self.assertIn("need", block)

    def test_F9_strip_is_a_targeted_line_edit(self):
        before = self.todo.read_text().splitlines()
        fo.reconcile([str(self.todo)])
        after = self.todo.read_text().splitlines()
        self.assertEqual(len(before), len(after))
        for b, a in zip(before, after):
            if "[a-20260101000000]" in b:
                self.assertNotEqual(b, a)      # the discharged line changed
            else:
                self.assertEqual(b, a)         # every other line is byte-identical

    def test_F10_recognises_a_real_minted_line(self):
        # End-to-end with the real minter's own line text, so the marker string
        # and the scan can never drift apart.
        import pre_plan_gates as ppg
        text = ppg._auto_register_line_text(
            "widget-20260101000000",
            Path("/x/Thoughts/widget-20260101000000_PLAN.md"), 3)
        t = self.tmp / "minted.md"
        t.write_text("# TODO\n\n## Now\n\n- [ ] " + text + "\n")
        rows = fo.scan(t)
        self.assertEqual(len(rows), 1, msg=rows)
        self.assertFalse(rows[0]["framed"],
                         "a freshly minted line must NOT be pre-discharged")


class OmissionRegistrationArmTests(unittest.TestCase):
    """A4 — the registration-aware arm. Every branch except the positive one PASSes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cwo_"))
        (self.tmp / "Thoughts").mkdir(parents=True)
        self.spine = self.tmp / "Thoughts" / "topic-20260101000000_PLAN.md"
        self.spine.write_text("# Plan\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _unbound(self):
        return mock.patch.object(cwo, "_active_topic", return_value=(None, None))

    def test_O1_no_discoverable_artifact_passes(self):
        with self._unbound(), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=None):
            d = cwo.decide("sid-1")
        self.assertFalse(d["block"])
        self.assertIn("refusing to guess", d["reason"])

    def test_O2_no_session_window_passes(self):
        with self._unbound(), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=self.spine), \
             mock.patch.object(cwo, "_session_started_at", return_value=None):
            d = cwo.decide("sid-1")
        self.assertFalse(d["block"])
        self.assertIn("indeterminate", d["reason"])

    def test_O3_no_in_window_commits_passes(self):
        with self._unbound(), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=self.spine), \
             mock.patch.object(cwo, "_session_started_at",
                               return_value="2026-01-01T00:00:00+00:00"), \
             mock.patch.object(cwo, "_commits_present", return_value=False):
            d = cwo.decide("sid-1")
        self.assertFalse(d["block"])
        self.assertIn("no in-window landed commits", d["reason"])

    def test_O4_artifact_plus_commits_reports_with_remediation(self):
        """Since slice-register-plan-ref-resolution S1 the gate is REPORT-ONLY, so this
        arm no longer blocks — but everything it was guarding must survive the
        conversion: the reason still names the unregistered topic, and the carried
        would-block text still names both the artifact and the remediation.

        Asserted against `would_block_message` rather than `message`, because a
        report-only verdict deliberately carries no operator-facing `message` of its
        own — the two are different fields precisely so a reader can tell a report
        from a block."""
        with self._unbound(), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=self.spine), \
             mock.patch.object(cwo, "_session_started_at",
                               return_value="2026-01-01T00:00:00+00:00"), \
             mock.patch.object(cwo, "_commits_present", return_value=True):
            d = cwo.decide("sid-abcd1234")
        self.assertFalse(d["block"], msg="report-only must not block")
        self.assertTrue(d["report_only"])
        self.assertIn("unregistered topic", d["reason"])
        self.assertIn("topic-20260101000000_PLAN.md", d["would_block_message"])
        self.assertIn("auto-register", d["would_block_message"],
                      msg="the report must still name the remediation")

    def test_O4b_artifact_plus_commits_still_blocks_when_report_only_is_off(self):
        """The unregistered arm's BLOCK path is unreached, not removed — the same
        property asserted for the bound arm in test_check_work_done_omission.py."""
        with self._unbound(), \
             mock.patch.object(cwo, "REPORT_ONLY", False), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=self.spine), \
             mock.patch.object(cwo, "_session_started_at",
                               return_value="2026-01-01T00:00:00+00:00"), \
             mock.patch.object(cwo, "_commits_present", return_value=True):
            d = cwo.decide("sid-abcd1234")
        self.assertTrue(d["block"])
        self.assertIn("unregistered topic", d["reason"])
        self.assertIn("auto-register", d["message"])

    def test_O5_internal_error_still_passes(self):
        with self._unbound(), \
             mock.patch.object(cwo, "_discoverable_spine",
                               side_effect=RuntimeError("boom")):
            d = cwo.decide("sid-1")
        self.assertFalse(d["block"], msg="fail-open on our own bug")

    def test_O6_bound_session_unaffected(self):
        # A bound session must not enter the new arm at all.
        with mock.patch.object(cwo, "_active_topic", return_value=("t", "Root")), \
             mock.patch.object(cwo, "_topic_state", return_value=None), \
             mock.patch.object(cwo, "_discoverable_spine",
                               side_effect=AssertionError(
                                   "the unregistered arm must not run for a "
                                   "bound session")):
            d = cwo.decide("sid-1")
        self.assertFalse(d["block"])
        self.assertIn("no topic-state file", d["reason"])


class OmissionWindowAndDiscoveryTests(unittest.TestCase):
    """The two sources that decide whether the A4 arm can fire at all."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cwo_src_"))
        # `_discoverable_spine` also searches PROJECTS_ROOT; pin it to the
        # scratch tree so these tests never see (or depend on) the live corpus.
        import pre_plan_gates as _ppg
        self._p = mock.patch.object(_ppg, "PROJECTS_ROOT", self.tmp)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_W1_window_comes_from_the_first_record_not_ctime(self):
        # ctime tracks the LAST append, and the transcript is being appended to
        # by the very turn that fires this hook — using it would collapse the
        # window to zero width and make the arm dead code.
        t = self.tmp / "sid.jsonl"
        t.write_text(
            '{"timestamp": "2026-01-01T00:00:00+00:00", "type": "user"}\n'
            '{"timestamp": "2026-01-01T09:00:00+00:00", "type": "assistant"}\n')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._session_started_at("sid")
        self.assertEqual(got, "2026-01-01T00:00:00+00:00")

    def test_W2_no_transcript_yields_no_window(self):
        with mock.patch.object(cwo, "_transcript_path", return_value=None):
            self.assertIsNone(cwo._session_started_at("sid"))

    def test_W3_unparseable_head_falls_back_without_raising(self):
        t = self.tmp / "sid.jsonl"
        t.write_text("not json at all\n{also not}\n")
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._session_started_at("sid")
        self.assertTrue(got is None or isinstance(got, str))

    def _transcript(self, *records) -> Path:
        t = self.tmp / "sid.jsonl"
        t.write_text("\n".join(records) + "\n")
        return t

    def test_D1_handoff_spine_ref_discovers_outside_a_worktree(self):
        # The primary-checkout case: the worktree source yields nothing, so the
        # opening message (the pasted handoff prompt) must carry it. Uses the
        # REAL parser — not a mock — so the parser itself is under test.
        (self.tmp / "Thoughts").mkdir()
        (self.tmp / "Thoughts" / "handoff-20260101000000_THOUGHT.md").write_text("#\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"Read Thoughts/handoff-20260101000000_THOUGHT.md fully."}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            self.assertEqual(cwo._spine_refs_from_transcript("sid"),
                             ["handoff"])
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNotNone(got, "a handoff-referenced spine must be discoverable")
        self.assertEqual(Path(got).name, "handoff-20260101000000_THOUGHT.md")

    def test_D1b_a_plan_anchoring_artifact_is_discoverable(self):
        # A Mode-C ship's anchoring artifact is a _PLAN, not a _THOUGHT.
        (self.tmp / "Thoughts").mkdir()
        (self.tmp / "Thoughts" / "modec-20260101000000_PLAN.md").write_text("#\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"Run /work-start Thoughts/modec-20260101000000_PLAN.md first."}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNotNone(got)
        self.assertEqual(Path(got).name, "modec-20260101000000_PLAN.md")

    def test_D1c_only_the_opening_message_is_scanned(self):
        # THE regression test for the over-matching defect: a long session
        # mentions every artifact it read. Scanning the whole transcript
        # resolved an UNRELATED topic on real data. Only the opening message
        # counts, and template placeholders like `<slug>_THOUGHT.md` are never
        # treated as references.
        (self.tmp / "Thoughts").mkdir()
        for n in ("mine-20260101000000_THOUGHT.md",
                  "unrelated-20260101000000_THOUGHT.md"):
            (self.tmp / "Thoughts" / n).write_text("#\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"Read Thoughts/mine-20260101000000_THOUGHT.md."}}',
            '{"type": "assistant", "message": {"role": "assistant", "content": '
            '"I also read Thoughts/unrelated-20260101000000_THOUGHT.md and the '
            'template <slug>_THOUGHT.md"}}',
            '{"type": "user", "message": {"role": "user", "content": '
            '"and Thoughts/unrelated-20260101000000_THOUGHT.md too"}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            slugs = cwo._spine_refs_from_transcript("sid")
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertEqual(slugs, ["mine"],
                         msg="only the opening message may contribute slugs")
        self.assertEqual(Path(got).name, "mine-20260101000000_THOUGHT.md")

    def test_D2_ambiguous_slug_refuses_to_guess(self):
        (self.tmp / "Thoughts").mkdir()
        for n in ("amb-20260101000000_THOUGHT.md", "amb-20260102000000_THOUGHT.md"):
            (self.tmp / "Thoughts" / n).write_text("# Spine\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"see Thoughts/amb-20260101000000_THOUGHT.md"}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNone(got, "two matches for one slug must refuse to guess")

    def test_D2b_two_distinct_topics_refuse_to_guess(self):
        (self.tmp / "Thoughts").mkdir()
        for n in ("one-20260101000000_THOUGHT.md", "two-20260101000000_THOUGHT.md"):
            (self.tmp / "Thoughts" / n).write_text("# Spine\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"do Thoughts/one-20260101000000_THOUGHT.md and '
            'Thoughts/two-20260101000000_THOUGHT.md"}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNone(got, "two candidate topics must refuse to guess")

    def test_D2c_mixed_artifact_types_across_topics_refuse_to_guess(self):
        # A `_THOUGHT` for topic A and a `_PLAN` for topic B: preferring the
        # spine would silently pick a winner between two named topics.
        (self.tmp / "Thoughts").mkdir()
        (self.tmp / "Thoughts" / "aaa-20260101000000_THOUGHT.md").write_text("#\n")
        (self.tmp / "Thoughts" / "bbb-20260101000000_PLAN.md").write_text("#\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"do Thoughts/aaa-20260101000000_THOUGHT.md then '
            'Thoughts/bbb-20260101000000_PLAN.md"}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNone(got, "two named topics must refuse to guess even when "
                               "only one of them is a _THOUGHT")

    def test_D2d_one_topic_with_both_artifacts_prefers_the_spine(self):
        # Within ONE topic the spine legitimately outranks the plan.
        (self.tmp / "Thoughts").mkdir()
        (self.tmp / "Thoughts" / "solo-20260101000000_THOUGHT.md").write_text("#\n")
        (self.tmp / "Thoughts" / "solo-20260101000000_PLAN.md").write_text("#\n")
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"work Thoughts/solo-20260101000000_PLAN.md"}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNotNone(got)
        self.assertEqual(Path(got).name, "solo-20260101000000_THOUGHT.md")

    def test_D4_mdx_lookalike_is_not_a_reference(self):
        # `.md` must not match as a substring of a longer extension.
        t = self._transcript(
            '{"type": "user", "message": {"role": "user", "content": '
            '"see foo_THOUGHT.mdx and bar_PLAN.mdown"}}')
        with mock.patch.object(cwo, "_transcript_path", return_value=t):
            self.assertEqual(cwo._spine_refs_from_transcript("sid"), [])

    def test_D3_no_sources_yields_none(self):
        with mock.patch.object(cwo, "_spine_refs_from_transcript", return_value=[]):
            got = cwo._discoverable_spine(cwd=self.tmp, session_id="sid")
        self.assertIsNone(got)


class OmissionSoftWarnTests(unittest.TestCase):
    """The omission hook's ADDITIVE framing surface never changes the verdict."""

    def test_S1_soft_warn_is_fail_open(self):
        with mock.patch.object(fo, "reconcile", side_effect=RuntimeError("boom")):
            self.assertIsNone(cwo._framing_soft_warn())

    def test_S2_soft_warn_does_not_affect_the_verdict(self):
        # decide() is computed independently of the warn surface.
        with mock.patch.object(cwo, "_framing_soft_warn",
                               return_value="⚠ something outstanding"), \
             mock.patch.object(cwo, "_active_topic", return_value=(None, None)), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=None):
            d = cwo.decide("sid-1")
        self.assertFalse(d["block"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

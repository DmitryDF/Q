#!/usr/bin/env python3
"""Tests for S4 (project-tracking-staleness Phase 2) — the check-work-done-omission
Stop hook + its A5 annotate_session write-coupling.

Covers:

  CheckWorkDoneOmissionTests (A1 decision module + A3 wrapper):
    D1   date-independent [sid:] session-block match (cross-midnight, E1 hazard)
    D2   no active topic                         → PASS (soft-exit)
    D3   already-recorded: spine session block   → PASS
    D4   already-recorded: skipped ledger row    → PASS
    D5   no lock (window indeterminate)          → PASS (fail-safe)
    D6   no in-window commits (commits_present F)→ PASS
    D7   commits ∧ ¬block ∧ topic                → REPORT-ONLY pass; the block text
                                                   (escape hatch included) is carried
                                                   under would_block_message, and
                                                   `message` is None
    D7b  same, with REPORT_ONLY flipped off      → BLOCK (the path is unreached, not
                                                   deleted)
    D8   fail-open: an internal error            → PASS (never blocks on a bug)
    W1   wrapper: empty stdin                     → exit 0
    W2   wrapper: stop_hook_active                → exit 0
    W3   wrapper: module exit 2                   → exit 2 (block)
    W4   wrapper: module non-2 non-zero (crash)   → exit 0 (fail-open)
    M1   module main(): report-only → 0, pass → 0, block (constant off) → 2

  S1 additions (slice-register-plan-ref-resolution, actions A1 + A6):
    blocking shapes block only with REPORT_ONLY off (the flip-the-constant control
      that keeps the assertion non-vacuous); passing shapes pass either way;
      REPORT_ONLY is the shipped default; the unregistered arm reports too;
      A6's record is written, rendered at /close, leaves the exit code at 0, and is
      fail-open in both halves independently; the namespace matches the ledger under
      default config and diverges under a CLAUDE_CONFIG_DIR redirect, by design

  AnnotateSessionCouplingTests (A5):
    A5a  _resolve_thought_path resolves MAIN-PINNED (resolver result used even when
         cwd-relative candidate is absent)
    A5b  annotate_session wraps its read-modify-write in bookkeeping_lock

Run (merge candidate): env CLAUDE_CONFIG_DIR=dot_claude python3 -m pytest -q \
    dot_claude/hooks/tests/test_check_work_done_omission.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Respect CLAUDE_CONFIG_DIR so a claude-experiment / merge-candidate clone tests
# its OWN edited modules (S3-learned redirection), defaulting to live ~/.claude.
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import check_work_done_omission as cwd  # noqa: E402
import pre_plan_gates as ppg  # noqa: E402


class CheckWorkDoneOmissionTests(unittest.TestCase):
    def test_D1_session_block_match_is_date_independent(self):
        rx = cwd._session_block_re("1e9250ee")
        self.assertTrue(rx.search("## Sessions\n\n### 2026-07-27 session [sid:1e9250ee]\n"))
        # a heading dated "yesterday" with this sid must still match (cross-midnight)
        self.assertTrue(rx.search("### 2000-01-01 session [sid:1e9250ee-9cd4]\n"))
        self.assertFalse(rx.search("### 2026-07-27 session [sid:deadbeef]\n"))

    def _patch(self, **kw):
        """Monkeypatch cwd module helpers for this test; auto-restored on tearDown."""
        for name, val in kw.items():
            self._saved.setdefault(name, getattr(cwd, name))
            setattr(cwd, name, val)

    def setUp(self):
        self._saved: dict = {}
        # Safe defaults: bound topic, not recorded, lock held, commits present.
        self._patch(
            _active_topic=lambda sid: ("mytopic", "Root"),
            _topic_state=lambda t, p: {"project_root": "/x", "thought_file_path": "Thoughts/x_THOUGHT.md"},
            _skipped_ledger_row=lambda sid, t: False,
            _spine_has_session_block=lambda sid, st: False,
            _lock_started_at=lambda t, p: "2026-07-27T16:00:00+00:00",
            _commits_present=lambda t, p, st, s, e: True,
        )

    def tearDown(self):
        for name, val in self._saved.items():
            setattr(cwd, name, val)

    def test_D2_no_topic_passes(self):
        self._patch(_active_topic=lambda sid: (None, None))
        self.assertFalse(cwd.decide("s")["block"])

    def test_D3_already_recorded_spine_passes(self):
        self._patch(_spine_has_session_block=lambda sid, st: True)
        self.assertFalse(cwd.decide("s")["block"])

    def test_D4_skipped_ledger_passes(self):
        self._patch(_skipped_ledger_row=lambda sid, t: True)
        self.assertFalse(cwd.decide("s")["block"])

    def test_D5_no_lock_passes(self):
        self._patch(_lock_started_at=lambda t, p: None)
        d = cwd.decide("s")
        self.assertFalse(d["block"])
        self.assertIn("window indeterminate", d["reason"])

    def test_D6_no_commits_passes(self):
        self._patch(_commits_present=lambda t, p, st, s, e: False)
        self.assertFalse(cwd.decide("s")["block"])

    def test_D7_report_only_does_not_block_but_names_the_would_block(self):
        """S1: the shipped default is report-only, so the would-block case PASSES —
        while still carrying the full block text (escape hatch included) so nothing
        the operator needs is lost in the conversion."""
        d = cwd.decide("abcd1234")
        self.assertFalse(d["block"], "report-only must not block")
        self.assertTrue(d["report_only"])
        self.assertIn("would have blocked", d["reason"])
        self.assertIn("skip-work-done-check", d["would_block_message"])
        self.assertIn("mytopic", d["would_block_message"])
        self.assertIn("abcd1234", d["would_block_message"])
        self.assertEqual(d["topic"], "mytopic")
        self.assertEqual(d["project"], "Root")
        # `message` is deliberately None on a report-only verdict — the operator-facing
        # block text lives under `would_block_message` instead, so a reader (and the
        # wrapper's exit-2 branch) can tell a report from a block by shape rather than
        # by parsing prose. Locked here because the design leans on the distinction.
        self.assertIsNone(d["message"])

    def test_D7b_block_path_is_intact_and_reachable_only_by_flipping_the_constant(self):
        """The BLOCK path is not deleted, only unreached — flipping the constant is
        the whole of what the follow-up has to do here. This is what makes
        'report-only for this plan's lifetime' an assertable property rather than a
        discipline statement."""
        self._patch(REPORT_ONLY=False)
        d = cwd.decide("abcd1234")
        self.assertTrue(d["block"])
        self.assertIn("skip-work-done-check", d["message"])
        self.assertIn("mytopic", d["message"])
        self.assertIn("abcd1234", d["message"])

    def test_D8_fail_open_on_internal_error(self):
        def boom(sid, st):
            raise RuntimeError("injected")
        self._patch(_spine_has_session_block=boom)
        d = cwd.decide("s")
        self.assertFalse(d["block"])  # never blocks on its own bug
        self.assertIn("internal error", d["reason"])

    def test_M1_main_exit_codes(self):
        # S1: report-only exits 0 on the would-block case. A6 forbids changing the
        # exit code, so the report rides a channel, never the exit status.
        self.assertEqual(cwd.main(["abcd1234"]), 0)  # report-only
        self._patch(_commits_present=lambda t, p, st, s, e: False)
        self.assertEqual(cwd.main(["abcd1234"]), 0)  # pass
        # and the block path still exits 2 when the constant is flipped
        self._patch(REPORT_ONLY=False, _commits_present=lambda t, p, st, s, e: True)
        self.assertEqual(cwd.main(["abcd1234"]), 2)

    # ---------------------------------------------------------------- S1 / A1+A6
    #
    # WHY THIS IS TWO GROUPS AND NOT ONE SWEEP. An earlier version asserted
    # `block is False` across seven decision shapes and called that A1's gate. Six
    # of the seven were vacuous with respect to report-only: they reach an early
    # `_pass` in `decide()` regardless of the constant, so they would have passed
    # before this slice too and prove nothing about it. One was worse than vacuous
    # — the "unbound session" shape patched only `_active_topic`, so the real
    # `_decide_unregistered` ran, the real `_discoverable_spine` found nothing for a
    # fake session id, and the case short-circuited to a pass long before reaching
    # the BLOCK branch the test was named for.
    #
    # The discriminator is the FLIP-THE-CONSTANT CONTROL. For a shape that would
    # block, report-only is doing work only if the same shape blocks with the
    # constant off and does not with it on. For a shape that passes anyway, the
    # useful assertion is that it passes in BOTH settings — which is what proves it
    # belongs in the second group rather than silently padding the first.

    # Shapes that genuinely reach a BLOCK. Each is asserted BOTH ways.
    #
    # THE UNREGISTERED ARM IS DELIBERATELY NOT HERE. It reaches BLOCK by a second
    # route, and it must be covered — but covering it needs a spine that survives
    # `pre_plan_gates.canonical_project_for_spine` / `_project_dir_for_spine`, i.e. a
    # real file on disk, not a plausible-looking path string. A first attempt at
    # putting it in this group patched `_discoverable_spine` to return a fake path;
    # the flip-the-constant control below caught it immediately, because the shape
    # failed to block with report-only OFF — it was short-circuiting to a fail-safe
    # pass long before the BLOCK branch. That is the control doing its job, and it is
    # why the shape is not quietly left here in a half-working state.
    #
    # The arm's real coverage, with real fixtures and the same both-ways control, is
    # `test_O4` / `test_O4b` in tests/test_framing_obligation.py, plus
    # `test_S1_unregistered_arm_also_reports_rather_than_blocks` below for the
    # conversion itself.
    _BLOCKING_SHAPES = {
        "bound arm: commits, unrecorded": {},
    }

    # Shapes that pass regardless of the constant — listed so their vacuity is
    # explicit rather than inflating the count of the group above.
    _PASSING_SHAPES = {
        "no lock": {"_lock_started_at": lambda t, p: None},
        "no commits": {"_commits_present": lambda t, p, st, s, e: False},
        "already in spine": {"_spine_has_session_block": lambda sid, st: True},
        "skip in ledger": {"_skipped_ledger_row": lambda sid, t: True},
        "no topic state": {"_topic_state": lambda t, p: None},
    }

    def _decide_under(self, patches, *, report_only):
        """Fresh fixture, apply the shape, set the constant, decide. The reset is a
        full tearDown/setUp so no patch leaks between subtests."""
        self.tearDown()
        self.setUp()
        self._patch(REPORT_ONLY=report_only, **patches)
        return cwd.decide("abcd1234")

    def test_S1_blocking_shapes_block_only_when_report_only_is_off(self):
        """A1's gate, made non-vacuous. Every shape that CAN block is asserted in
        both settings, so the test fails if report-only stops doing the work OR if
        the shape stops being one that would have blocked."""
        for label, patches in self._BLOCKING_SHAPES.items():
            with self.subTest(shape=label, report_only=False):
                d = self._decide_under(patches, report_only=False)
                self.assertTrue(d["block"],
                                f"{label} must block with report-only OFF — "
                                "otherwise the ON assertion below is vacuous")
            with self.subTest(shape=label, report_only=True):
                d = self._decide_under(patches, report_only=True)
                self.assertFalse(d["block"], f"{label} must not block under report-only")
                self.assertTrue(d["report_only"])
                self.assertTrue(d["would_block_message"],
                                "the block text must be carried, not dropped")

    def test_S1_passing_shapes_pass_in_both_settings(self):
        """The complement. These reach an early `_pass` and are unaffected by the
        constant — asserted explicitly so they cannot be mistaken for evidence that
        report-only works."""
        for label, patches in self._PASSING_SHAPES.items():
            for ro in (True, False):
                with self.subTest(shape=label, report_only=ro):
                    d = self._decide_under(patches, report_only=ro)
                    self.assertFalse(d["block"], f"{label} passes regardless")
                    self.assertNotIn("report_only", d,
                                     "an early pass is not a report-only verdict")

    def test_S1_report_only_is_the_shipped_default(self):
        """The toggle is a module constant and its default is report-only. Asserted
        because the slice's entire safety argument rests on the default, and an env
        var could not be distinguished from an unset one."""
        self.assertIs(cwd.REPORT_ONLY, True)

    def test_S1_unregistered_arm_also_reports_rather_than_blocks(self):
        """The unregistered-topic arm reaches _block by a second route; report-only
        must cover it too, or the slice would leave one blocking path live."""
        self._patch(_active_topic=lambda sid: (None, None))
        captured = {}

        def fake_unreg(session_id, now):
            out = cwd._block("utopic", "Root", session_id)
            out["reason"] = "shipped work on an unregistered topic"
            captured["raw"] = out
            return cwd._report_only(out)

        self._patch(_decide_unregistered=fake_unreg)
        d = cwd.decide("abcd1234")
        self.assertFalse(d["block"])
        self.assertTrue(d["report_only"])
        self.assertEqual(d["would_block_reason"], "shipped work on an unregistered topic")

    def test_S1_A6_emission_persists_the_report_and_never_changes_the_exit_code(self):
        """A6: the report reaches the durable channel, and the exit code is untouched.
        Also asserts the fail-open half — a broken record must not change the exit
        code, because a reporting channel on a fail-open gate must never be the thing
        that stops a session ending."""
        import tempfile
        import work_done_report as wdr

        with tempfile.TemporaryDirectory() as td:
            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = td
            try:
                import contextlib
                import io

                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    rc = cwd.main(["abcd1234"])
                self.assertEqual(rc, 0, "A6 must not change the exit code")
                rows = wdr.read_reports(session_id="abcd1234")
                self.assertEqual(len(rows), 1, rows)
                self.assertEqual(rows[0]["topic"], "mytopic")
                self.assertIn("skip-work-done-check", rows[0]["message"])
                block = wdr.render_close_block(session_id="abcd1234")
                self.assertIn("mytopic", block)
                self.assertIn("not blocking", block.lower())
                # the ADDITIVE stderr line is behaviourally exercised, not merely
                # asserted by reading — it is best-effort, but "it is emitted" is a
                # claim, and an unexercised claim is what this slice exists to remove
                self.assertIn("REPORT ONLY", err.getvalue())

                # Fail-open, asserted so it DISCRIMINATES. An exit-code assertion
                # alone is structurally guaranteed to pass here — main() derives its
                # code from verdict["block"] and never from the emission — so it
                # would hold whether or not the write degraded. Re-read the record
                # under the broken namespace and assert nothing landed; that is the
                # part that can actually fail.
                os.environ["CLAUDE_CONFIG_DIR"] = "/proc/nonexistent-zzz"
                err2 = io.StringIO()
                with contextlib.redirect_stderr(err2):
                    self.assertEqual(cwd.main(["abcd1234"]), 0)
                self.assertEqual(wdr.read_reports(), [],
                                 "an unwritable namespace must record nothing, "
                                 "not partially write")
                self.assertIn("REPORT ONLY", err2.getvalue(),
                              "the stderr half must survive the record half failing "
                              "— the two are wrapped independently on purpose")

                # and the earlier record is intact once the namespace is restored
                os.environ["CLAUDE_CONFIG_DIR"] = td
                self.assertEqual(len(wdr.read_reports(session_id="abcd1234")), 1)
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_S1_namespace_matches_the_ledger_under_default_config_and_diverges_under_redirect(self):
        """Locks BOTH halves of the namespace property, because the flat claim
        ("same namespace as the completed-work ledger") is true only under default
        configuration.

        `work_done.LEDGER_DIR` and `work_done_journal.STATE_DIR` hardcode
        `Path.home()/".claude"`; this module resolves its base from
        `CLAUDE_CONFIG_DIR` first. They coincide by default and diverge under a
        redirect — which is this codebase's own `claude-experiment` staging pattern,
        so the divergence is reachable in normal use rather than hypothetical.

        The divergence is intentional (it is what keeps a staging clone's reports
        inside the clone, and what lets these tests run against a temp dir), so this
        asserts the divergence rather than forbidding it. What it guards is a silent
        change to either side's relative path segment."""
        import work_done as wd
        import work_done_report as wdr

        prev = os.environ.get("CLAUDE_CONFIG_DIR")
        try:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(wdr.record_path().parent, wd.LEDGER_DIR,
                             "under default config the two must be the same dir")

            with tempfile.TemporaryDirectory() as td:
                os.environ["CLAUDE_CONFIG_DIR"] = td
                self.assertNotEqual(wdr.record_path().parent, wd.LEDGER_DIR,
                                    "under a redirect they must diverge — the ledger "
                                    "hardcodes home, this module does not")
                self.assertEqual(wdr.record_path().parent,
                                 Path(td) / "state" / "work_done",
                                 "and this module must follow the redirect")
        finally:
            if prev is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_S1_record_path_is_fail_open_like_the_rest_of_the_public_surface(self):
        """`record_path` is exported, so it owes the same guarantee as its siblings —
        not one that holds only because today's callers happen to wrap it."""
        import work_done_report as wdr

        def boom():
            raise RuntimeError("injected")

        saved = wdr._state_dir
        try:
            # Injecting at `_state_dir` rather than via a malformed env value, because
            # the env route cannot actually provoke the failure — `Path(str)` accepts
            # very nearly anything, so that test would pass without exercising the
            # except branch at all. Fault injection is what makes this discriminate.
            wdr._state_dir = boom
            p = wdr.record_path()  # must not raise
            self.assertTrue(str(p).endswith(wdr.REPORT_FILENAME))
            self.assertIn("work_done", str(p), "degrades to a location, not an exception")
        finally:
            wdr._state_dir = saved

    def test_S1_nothing_to_report_renders_nothing(self):
        """A close-out that announces its own silence is noise."""
        import tempfile
        import work_done_report as wdr

        with tempfile.TemporaryDirectory() as td:
            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            os.environ["CLAUDE_CONFIG_DIR"] = td
            try:
                self.assertEqual(wdr.render_close_block(), "")
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    # --- wrapper (A3) behavioral guards ---
    def _run_wrapper(self, stdin: str):
        wrapper = HOOKS / "check-work-done-omission.sh"
        if not wrapper.exists():  # source tree carries executable_ prefix, not yet applied
            self.skipTest("wrapper not deployed at HOOKS/check-work-done-omission.sh")
        return subprocess.run(["bash", str(wrapper)], input=stdin,
                              capture_output=True, text=True)

    def test_W1_empty_stdin_exit0(self):
        r = self._run_wrapper("")
        self.assertEqual(r.returncode, 0)

    def test_W2_stop_hook_active_exit0(self):
        r = self._run_wrapper(json.dumps({"session_id": "s", "stop_hook_active": True}))
        self.assertEqual(r.returncode, 0)


class AnnotateSessionCouplingTests(unittest.TestCase):
    """A5 — annotate_session write is main-pinned + lock-wrapped."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="a5_"))
        self.main_root = self.tmp / "main"
        (self.main_root / "Thoughts").mkdir(parents=True)
        self.spine = self.main_root / "Thoughts" / "x_THOUGHT.md"
        self.spine.write_text("# Idea\n\n## Sessions\n", encoding="utf-8")
        self._saved = {}

    def tearDown(self):
        import shutil
        for obj, name, val in self._saved.get("attrs", []):
            setattr(obj, name, val)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _patch(self, obj, name, val):
        self._saved.setdefault("attrs", []).append((obj, name, getattr(obj, name)))
        setattr(obj, name, val)

    def test_A5a_resolve_thought_path_is_main_pinned(self):
        import bookkeeping_resolver as bkr
        # topic-state: relative thought path + a project_root that is the MAIN root.
        state = {"project_root": str(self.main_root),
                 "thought_file_path": "Thoughts/x_THOUGHT.md"}
        self._patch(ppg, "_resolve_topic", lambda sid: ("Root", "mytopic", state))
        # resolver returns the main-pinned absolute path; cwd-relative candidate does
        # NOT exist (we are not in main_root), so the resolver result must be used.
        calls = {}
        real_resolve = bkr.resolve
        def spy_resolve(rel, cwd=None):
            calls["args"] = (rel, cwd)
            return self.spine
        self._patch(bkr, "resolve", spy_resolve)
        p = ppg._resolve_thought_path("s")
        self.assertEqual(Path(p), self.spine)
        self.assertEqual(calls["args"][0], "Thoughts/x_THOUGHT.md")
        self.assertEqual(calls["args"][1], str(self.main_root))

    def test_A5b_annotate_session_is_lock_wrapped(self):
        import bookkeeping_resolver as bkr
        import bookkeeping_lock as bkl
        state = {"project_root": str(self.main_root),
                 "thought_file_path": "Thoughts/x_THOUGHT.md"}
        self._patch(ppg, "_resolve_topic", lambda sid: ("Root", "mytopic", state))
        self._patch(bkr, "resolve", lambda rel, cwd=None: self.spine)
        entered = {"n": 0}
        from contextlib import contextmanager
        real_lock = bkl.bookkeeping_lock
        @contextmanager
        def spy_lock(target, **kw):
            entered["n"] += 1
            with real_lock(target, **kw):
                yield
        self._patch(bkl, "bookkeeping_lock", spy_lock)
        res = ppg.annotate_session("1e9250ee", "S4 smoke test bullet for the coupling.")
        self.assertGreaterEqual(entered["n"], 1)  # write happened under the lock
        self.assertIn("[sid:1e9250ee]", self.spine.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

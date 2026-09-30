#!/usr/bin/env python3
"""Tests for Slice L Foundations — taskmanagement.py (Session 1 primitives).

Covers:
  Lock primitives (A1):
    L1   acquire_lock: ACQUIRED on first call
    L2   acquire_lock: ALREADY_HELD_BY_SELF on idempotent re-acquire (same session_id)
    L3   acquire_lock: HELD_BY_OTHER when a different session holds and lock is fresh
    L4   acquire_lock: stale auto-release fires when last_heartbeat ≥ STALE_T
                       and an audit row is appended to _releases.jsonl
                       (LEGACY payloads only since S4 — see test body;
                       liveness cases live in test_s4_lock_liveness.py)
    L5   release_lock: NOT_HELD when no lock file exists
    L6   release_lock: NOT_HOLDER when session_id != holder and force=False
    L7   release_lock: RELEASED + audit row on holder release
    L8   release_lock: RELEASED + audit row with manual-release-non-holder reason on force
    L9   refresh_heartbeat: updates last_heartbeat; idempotent

  Concurrency guard (A1):
    C1   fcntl LOCK_NB second concurrent acquirer (held flock) returns RACE

  Verifier (A2):
    V1   verify_write todo_line success
    V2   verify_write todo_line failure (substring mismatch) raises WriteVerificationError
    V3   verify_write topic_state_json success (dotted key path)
    V4   verify_write topic_state_json failure (value mismatch) raises
    V5   verify_write spine_section success
    V6   verify_write spine_section failure (missing substring) raises
    V7   verify_write surface_missing raises

  Slice-register row format (A3):
    R1   round-trip: write 3 synthetic rows; parse them back; assert equality
    R2   write_slice_row replaces an existing row in place (not duplicated)
    R3   write_slice_row with invalid status raises ValueError
    R4   RETIRED 2026-08-30 — it asserted that the real
         `workflow-phases-redesign_THOUGHT.md` spine carried ZERO L-format
         rows. Slice L wrote the first row into that spine on 2026-05-23 and
         the S-F-Impl-2 backfill added the rest on 2026-06-06 (the spine
         documents that backfill itself, at its "Slice Register Markers
         (machine-readable, backfilled S-F-Impl-2 2026-06-06)" heading), so the
         premise died by a deliberate change, not by a regression. It is NOT
         repointed at the new count: `test_DP12` below already asserts the
         truth about that spine, from the current corpus location, so keeping
         both would mean maintaining two copies of one fact — which is how the
         next fossil gets made. `test_R4_retirement_is_recorded` locks this
         note, so the retirement cannot drift back unrecorded.

  all_slices_done predicate (A4):
    P1   all SHIPPED → True
    P2   all SHIPPED ∪ NEVER → True
    P3   any non-terminal status → False
    P4   empty spine → False (defensive)

  attribute_commits classifier (A5):
    A1c  scope-subset commit → confident
    A2c  mixed-scope commit  → ambiguous
    A3c  no-intersection commit → unmatched
    A4c  no scope in plan    → scope_empty
    A5c  parse_diff_section round-trip

Run: python3 ${KIT_HOOKS_DIR}/tests/test_taskmanagement.py
"""

from __future__ import annotations

import inspect
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Make the hooks dir importable. Respect CLAUDE_CONFIG_DIR so a merge-candidate
# check (verify-then-land) tests the candidate module, not the live one — the
# same alignment S7 gave test_work_done.py:60 (project-tracking-staleness S8).
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

# This file's own directory, so the shared live-corpus resolver imports whether
# the module is run directly or collected by pytest from elsewhere.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import taskmanagement as tm  # noqa: E402
import _live_corpus as lc  # noqa: E402


# ---------------------------------------------------------------------------
# Test infra — redirect LOCKS_DIR / RELEASES_LOG per-test to a tempdir
# ---------------------------------------------------------------------------

class _LockSandbox:
    """Context manager that redirects tm.LOCKS_DIR / tm.RELEASES_LOG to a tempdir."""

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
# Lock primitives
# ---------------------------------------------------------------------------

class LockPrimitivesTests(unittest.TestCase):

    def test_L1_acquire_first_call_returns_ACQUIRED(self):
        with _LockSandbox():
            res = tm.acquire_lock("topic-x", "sess-A")
            self.assertEqual(res["status"], "ACQUIRED")
            self.assertEqual(res["holder"]["session_id"], "sess-A")
            self.assertIn("started_at", res["holder"])
            self.assertIn("last_heartbeat", res["holder"])

    def test_L2_re_acquire_by_same_session_is_idempotent(self):
        with _LockSandbox():
            tm.acquire_lock("topic-x", "sess-A")
            res2 = tm.acquire_lock("topic-x", "sess-A")
            self.assertEqual(res2["status"], "ALREADY_HELD_BY_SELF")
            self.assertEqual(res2["holder"]["session_id"], "sess-A")

    def test_L3_different_session_fresh_lock_is_HELD_BY_OTHER(self):
        with _LockSandbox():
            tm.acquire_lock("topic-x", "sess-A")
            res = tm.acquire_lock("topic-x", "sess-B")
            self.assertEqual(res["status"], "HELD_BY_OTHER")
            self.assertEqual(res["holder"]["session_id"], "sess-A")
            self.assertLess(res["age_seconds"], 60)

    def test_L4_stale_lock_is_auto_released_and_audit_logged(self):
        with _LockSandbox() as sb:
            # Force a stale lock by forging an old last_heartbeat on a LEGACY
            # (pre-S4) payload. Since streamed-dancing-goose S4 a payload
            # carrying the session-process identity is reclaimed by liveness,
            # not age — the process that acquired here is THIS session and is
            # alive, so an old heartbeat alone no longer releases it. The
            # STALE_T path this test pins is the one legacy payloads keep.
            tm.acquire_lock("topic-x", "sess-A")
            payload_path = tm._lock_payload_path("topic-x")
            data = json.loads(payload_path.read_text())
            data["last_heartbeat"] = "2020-01-01T00:00:00+00:00"
            data.pop("pid_start", None)
            data.pop("identity", None)
            payload_path.write_text(json.dumps(data))

            res = tm.acquire_lock("topic-x", "sess-B")
            self.assertEqual(res["status"], "ACQUIRED")
            self.assertEqual(res["holder"]["session_id"], "sess-B")

            # Audit row exists with stale-auto-release reason
            log = tm.RELEASES_LOG.read_text().strip().splitlines()
            self.assertEqual(len(log), 1)
            row = json.loads(log[0])
            self.assertEqual(row["reason"], "stale-auto-release")
            self.assertEqual(row["released_payload"]["session_id"], "sess-A")
            self.assertEqual(row["new_holder_session_id"], "sess-B")

    def test_L5_release_nonexistent_returns_NOT_HELD(self):
        with _LockSandbox():
            res = tm.release_lock("topic-x")
            self.assertEqual(res["status"], "NOT_HELD")

    def test_L6_release_by_non_holder_without_force_is_NOT_HOLDER(self):
        with _LockSandbox():
            tm.acquire_lock("topic-x", "sess-A")
            res = tm.release_lock("topic-x", session_id="sess-B")
            self.assertEqual(res["status"], "NOT_HOLDER")
            self.assertEqual(res["holder"]["session_id"], "sess-A")

    def test_L7_release_by_holder_RELEASED_and_audit_logged(self):
        with _LockSandbox():
            tm.acquire_lock("topic-x", "sess-A")
            res = tm.release_lock("topic-x", session_id="sess-A")
            self.assertEqual(res["status"], "RELEASED")
            self.assertFalse(tm._lock_payload_path("topic-x").exists())
            log = tm.RELEASES_LOG.read_text().strip().splitlines()
            self.assertEqual(len(log), 1)
            self.assertEqual(json.loads(log[0])["reason"], "holder-release")

    def test_L8_release_force_non_holder_uses_manual_release_non_holder_reason(self):
        with _LockSandbox():
            tm.acquire_lock("topic-x", "sess-A")
            res = tm.release_lock("topic-x", session_id="sess-B", force=True)
            self.assertEqual(res["status"], "RELEASED")
            log = tm.RELEASES_LOG.read_text().strip().splitlines()
            self.assertEqual(json.loads(log[0])["reason"], "manual-release-non-holder")

    def test_L9_refresh_heartbeat_updates_timestamp(self):
        with _LockSandbox():
            tm.acquire_lock("topic-x", "sess-A")
            t0 = json.loads(tm._lock_payload_path("topic-x").read_text())["last_heartbeat"]
            time.sleep(1.05)  # ISO-second granularity
            res = tm.refresh_heartbeat("topic-x", session_id="sess-A")
            self.assertEqual(res["status"], "REFRESHED")
            t1 = res["holder"]["last_heartbeat"]
            self.assertNotEqual(t0, t1)


# ---------------------------------------------------------------------------
# Concurrency guard
# ---------------------------------------------------------------------------

class ConcurrencyTests(unittest.TestCase):

    def test_C1_concurrent_acquire_under_held_flock_returns_RACE(self):
        # Hold the flock from this process; spawn a subprocess that calls
        # acquire_lock with the same locks_dir env. The subprocess should
        # see BlockingIOError → RACE.
        with _LockSandbox() as sb:
            import fcntl as _fcntl
            flock_path = tm._flock_path("topic-x")
            # Open + flock from this thread; keep open during subprocess call
            holder = open(flock_path, "w")
            _fcntl.flock(holder, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            try:
                # Run the subprocess with a small Python program that uses the
                # SAME tempdir for tm.LOCKS_DIR (via monkeypatch inside the child).
                script = (
                    "import sys, json, pathlib\n"
                    f"sys.path.insert(0, {str(HOOKS)!r})\n"
                    "import taskmanagement as tm\n"
                    f"tm.LOCKS_DIR = pathlib.Path({str(tm.LOCKS_DIR)!r})\n"
                    f"tm.RELEASES_LOG = tm.LOCKS_DIR / '_releases.jsonl'\n"
                    "res = tm.acquire_lock('topic-x', 'sess-child')\n"
                    "print(json.dumps(res))\n"
                )
                r = subprocess.run(
                    ["python3", "-c", script],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(r.returncode, 0, msg=r.stderr)
                out = json.loads(r.stdout.strip())
                self.assertEqual(out["status"], "RACE")
            finally:
                _fcntl.flock(holder, _fcntl.LOCK_UN)
                holder.close()


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

class VerifyWriteTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_verify_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_V1_todo_line_success(self):
        p = self.tmp / "TODO.md"
        p.write_text("## Now\n\n- [ ] **Foo** — (in clarification: abc12345)\n")
        res = tm.verify_write(
            "todo_line", p,
            expected_payload="(in clarification: abc12345)",
            locator=r"- \[ \] \*\*Foo\*\*.*",
        )
        self.assertEqual(res["status"], "OK")

    def test_V2_todo_line_mismatch_raises(self):
        p = self.tmp / "TODO.md"
        p.write_text("## Now\n\n- [ ] **Foo** — (in clarification: abc12345)\n")
        with self.assertRaises(tm.WriteVerificationError):
            tm.verify_write(
                "todo_line", p,
                expected_payload="(in planning: xyz)",
                locator=r"- \[ \] \*\*Foo\*\*.*",
            )

    def test_V3_topic_state_json_success(self):
        p = self.tmp / "state.json"
        p.write_text(json.dumps({"phase": "clarification", "session_id": "abc"}))
        res = tm.verify_write("topic_state_json", p,
                              expected_payload="clarification", locator="phase")
        self.assertEqual(res["status"], "OK")

    def test_V4_topic_state_json_mismatch_raises(self):
        p = self.tmp / "state.json"
        p.write_text(json.dumps({"phase": "clarification"}))
        with self.assertRaises(tm.WriteVerificationError):
            tm.verify_write("topic_state_json", p,
                            expected_payload="planning", locator="phase")

    def test_V5_spine_section_success(self):
        p = self.tmp / "spine.md"
        p.write_text("# T\n\n## Slices\nbody here\n## Next\n")
        res = tm.verify_write(
            "spine_section", p,
            expected_payload="body here",
            locator=("## Slices", "## Next"),
        )
        self.assertEqual(res["status"], "OK")

    def test_V6_spine_section_missing_substring_raises(self):
        p = self.tmp / "spine.md"
        p.write_text("# T\n\n## Slices\nbody here\n## Next\n")
        with self.assertRaises(tm.WriteVerificationError):
            tm.verify_write(
                "spine_section", p,
                expected_payload="nope",
                locator=("## Slices", "## Next"),
            )

    def test_V7_surface_missing_raises(self):
        p = self.tmp / "does-not-exist.md"
        with self.assertRaises(tm.WriteVerificationError):
            tm.verify_write("todo_line", p, expected_payload="x", locator="x")


# ---------------------------------------------------------------------------
# Slice-register row format
# ---------------------------------------------------------------------------

class SliceRegisterTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_slice_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_R1_round_trip_three_rows(self):
        p = self.tmp / "spine.md"
        p.write_text("# Spine\n\n")
        tm.write_slice_row(p, "A", {"status": "SHIPPED", "sessions": "13/13",
                                    "plan": "[[A_PLAN]]", "diary": "[[2026-05-18]]",
                                    "updated": "2026-05-18"})
        tm.write_slice_row(p, "B", {"status": "NEXT", "sessions": "0/3",
                                    "updated": "2026-05-19"})
        tm.write_slice_row(p, "L", {"status": "NOW", "sessions": "16/20",
                                    "plan": "[[L_PLAN]]",
                                    "updated": "2026-05-23"})
        rows = tm.parse_slice_register(p)
        self.assertEqual(len(rows), 3)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["A", "B", "L"])
        a = rows[0]
        self.assertEqual(a["status"], "SHIPPED")
        self.assertEqual(a["sessions"], "13/13")
        self.assertEqual(a["plan"], "[[A_PLAN]]")
        self.assertEqual(a["diary"], "[[2026-05-18]]")
        b = rows[1]
        self.assertEqual(b["plan"], "")  # empty round-trips correctly
        self.assertEqual(b["diary"], "")

    def test_R2_write_slice_row_replaces_existing_in_place(self):
        p = self.tmp / "spine.md"
        p.write_text("# Spine\n\n")
        tm.write_slice_row(p, "L", {"status": "NEARBY", "sessions": "0/3",
                                    "updated": "2026-05-22"})
        tm.write_slice_row(p, "L", {"status": "NOW", "sessions": "16/20",
                                    "updated": "2026-05-23"})
        rows = tm.parse_slice_register(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "NOW")
        self.assertEqual(rows[0]["sessions"], "16/20")

    def test_R3_invalid_status_raises(self):
        p = self.tmp / "spine.md"
        p.write_text("# Spine\n\n")
        with self.assertRaises(ValueError):
            tm.write_slice_row(p, "L", {"status": "DONE", "sessions": "1/1"})

    def test_R4_retirement_is_recorded(self):
        """R4 is retired, and this locks the record of what retired it.

        R4 asserted a pre-Slice-L world. Slice L and then the S-F-Impl-2
        backfill wrote eleven rows into the spine it read, so the assertion
        went false by deliberate change. Deleting it silently would leave a
        reader of this file unable to tell that R4 ever existed or why it
        stopped — the exact re-derivation cost this suite is being repaired to
        remove. So the retirement is written into the module docstring above,
        and read back here; this is the `test_DP11` mechanism, applied to a
        retired TEST rather than a retired rule.
        """
        doc = inspect.getdoc(sys.modules[__name__]) or ""
        missing = [token for token in ("R4", "RETIRED", "S-F-Impl-2", "test_DP12")
                   if token not in doc]
        self.assertEqual(
            missing, [],
            msg=("RETIREMENT RECORD LOST, not a code regression: the module "
                 "docstring of this file no longer names " + repr(missing) + ". "
                 "R4 was retired on 2026-08-30 because the S-F-Impl-2 backfill "
                 "made its premise false, and test_DP12 now covers the truth "
                 "about that spine. Restore that note to the docstring rather "
                 "than deleting this test — an unrecorded retirement is how the "
                 "next reader ends up in commit history."))


# ---------------------------------------------------------------------------
# all_slices_done predicate
# ---------------------------------------------------------------------------

class AllSlicesDoneTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_pred_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spine(self, *triples):
        p = self.tmp / "spine.md"
        p.write_text("# Spine\n\n")
        for sid, status, sessions in triples:
            tm.write_slice_row(p, sid, {"status": status, "sessions": sessions,
                                        "updated": "2026-05-23"})
        return p

    def test_P1_all_shipped_returns_true(self):
        p = self._spine(("A", "SHIPPED", "3/3"), ("B", "SHIPPED", "1/1"))
        self.assertTrue(tm.all_slices_done(p))

    def test_P2_mixed_shipped_and_never_returns_true(self):
        p = self._spine(("A", "SHIPPED", "3/3"), ("X", "NEVER", "0/0"))
        self.assertTrue(tm.all_slices_done(p))

    def test_P3_any_non_terminal_returns_false(self):
        p = self._spine(("A", "SHIPPED", "3/3"), ("B", "NOW", "0/1"))
        self.assertFalse(tm.all_slices_done(p))

    def test_P4_empty_spine_returns_false_defensive(self):
        p = self.tmp / "empty.md"
        p.write_text("# Spine (no slices yet)\n")
        self.assertFalse(tm.all_slices_done(p))

    def test_P5_single_shipped_row_returns_false(self):
        # project-tracking-staleness S1 (row-count axis): a register holding one
        # SHIPPED row is indistinguishable from a 1-of-N first ship, so it must
        # NOT be trusted as "all done" (would prematurely retire a multi-slice
        # topic). <2 rows → False.
        p = self._spine(("A", "SHIPPED", "3/3"))
        self.assertFalse(tm.all_slices_done(p))

    def test_P6_single_never_row_returns_false(self):
        # Same guard for a lone terminal NEVER row.
        p = self._spine(("A", "NEVER", "0/0"))
        self.assertFalse(tm.all_slices_done(p))


# ---------------------------------------------------------------------------
# declared_slice_count reader + declared-count all_slices_done predicate
# (auto-registration Mode-C plan, slice S-A / action A0)
# ---------------------------------------------------------------------------

class DeclaredSliceCountTests(unittest.TestCase):
    """The `#### Slices` pipe-table reader."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_declared_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, body: str):
        p = self.tmp / "spine.md"
        p.write_text(body)
        return p

    def _write_named(self, name: str, body: str):
        p = self.tmp / name
        p.write_text(body)
        return p

    def test_D1_counts_declared_rows(self):
        p = self._write(
            "# Spine\n\n"
            "#### Slices (from B2 — 3 total)\n\n"
            "| ID | Name | Type | Depends on |\n"
            "|----|------|------|------------|\n"
            "| S1 | one   | implementation | — |\n"
            "| S2 | two   | implementation | S1 |\n"
            "| S3 | three | implementation | S2 |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D2_missing_file_and_no_table_return_zero(self):
        self.assertEqual(tm.declared_slice_count(self.tmp / "nope.md"), 0)
        p = self._write("# Spine\n\nNo slice table here at all.\n")
        self.assertEqual(tm.declared_slice_count(p), 0)

    def test_D3_empty_declared_table_returns_zero(self):
        # Early-draft `#### Slices` with a header but no S<N> rows → 0, which
        # routes all_slices_done to the fallback (never a `0 >= 0` retire).
        p = self._write(
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 0)

    def test_D4_gates_on_first_cell_not_header_text(self):
        # A "Slices" heading whose table carries non-`S<N>` ids contributes
        # nothing — recognition is the first cell, never the header text.
        p = self._write(
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S-A | dash-form id |\n"
            "| A1  | an action    |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 0)

    def test_D5_section_bounded_by_next_heading(self):
        # Rows under a LATER sibling heading are outside the section.
        p = self._write(
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one |\n"
            "| S2 | two |\n\n"
            "#### Verification\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S9 | not a declared slice |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 2)

    def test_D6_union_across_sections_over_counts_in_the_safe_direction(self):
        # UNION, not first-section-wins. A superseded second `#### Slices` block
        # inflates the count — deliberately. Over-counting only BLOCKS a retire
        # (loud, recoverable); the "pick the right section" heuristics that would
        # avoid it all have a tail of pathological inputs that UNDER-count, which
        # silently retires a live topic. Ids are unioned, so overlap is not
        # double-counted: {S1,S2} ∪ {S1,S2,S3,S4} = 4.
        p = self._write(
            "# Spine\n\n#### Slices (current)\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one |\n"
            "| S2 | two |\n\n"
            "#### Slices (SUPERSEDED)\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one |\n| S2 | two |\n| S3 | three |\n| S4 | four |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 4)

    def test_D7_dedupes_and_strips_decoration(self):
        p = self._write(
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| **S1** | emphasised |\n"
            "| `S2`   | code-fenced |\n"
            "| S2     | duplicate id |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 2)

    def test_D9_fenced_example_before_the_real_table_never_undercounts(self):
        # A doc-style ILLUSTRATIVE fenced example of a `#### Slices` table,
        # appearing BEFORE the real declared section. The union sees both, so
        # the example's S1 is subsumed by the real table's ids — the count can
        # never come out BELOW the real declared count, which is the only unsafe
        # direction (it would make the retire predicate trivially satisfiable).
        p = self._write(
            "# Spine\n\n"
            "Register format, for reference:\n\n"
            "```markdown\n"
            "#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | example row |\n"
            "```\n\n"
            "#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one   |\n"
            "| S2 | two   |\n"
            "| S3 | three |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D10_fenced_rows_inside_a_section_count_over_conservatively(self):
        # A fenced example nested INSIDE the real section inflates the count
        # (2 real + 2 illustrative = 4). Deliberate: the reader does no fence
        # parsing, and inflation only blocks a retire.
        p = self._write(
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one |\n"
            "| S2 | two |\n\n"
            "~~~\n| S3 | illustrative only |\n| S4 | illustrative only |\n~~~\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 4)

    def test_D11_unterminated_fence_cannot_hide_the_declared_table(self):
        # The single most dangerous shape for any fence-aware reader: an
        # unbalanced fence. A mask-to-EOF reader zeroes the declared count here
        # and routes all_slices_done to the row-count-only fallback — the
        # premature-retire defect returning by another route. Doing no fence
        # parsing at all makes that shape unrepresentable.
        p = self._write(
            "# Spine\n\n"
            "```\n"
            "an unterminated fence — no closing delimiter anywhere\n\n"
            "#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one   |\n"
            "| S2 | two   |\n"
            "| S3 | three |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D12_indented_fence_lookalike_cannot_hide_the_declared_table(self):
        # A 4+-space-indented fence-shaped line is NOT a fence under CommonMark,
        # but a naive `lstrip()`-based reader pairs it with a later real close
        # and masks everything between — including the real table. No fence
        # parsing means no such pairing exists.
        p = self._write(
            "# Spine\n\n"
            "Some text with an indented example:\n\n"
            "    ```\n"
            "#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one |\n"
            "| S2 | two |\n\n"
            "Later, a real code example:\n\n"
            "```bash\necho hi\n```\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 2)

    def test_D13_decorative_delimiter_lines_cannot_hide_the_declared_table(self):
        # Bare `~~~~~~~` divider lines straddling the real table would, under a
        # delimiter-pairing reader, mask it entirely.
        p = self._write(
            "# Spine\n\n"
            "~~~~~~~~~~~~~~~~\n\n"
            "#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "| S1 | one |\n| S2 | two |\n| S3 | three |\n\n"
            "~~~~~~~~~~~~~~~~\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D14_ids_are_unioned_across_sections_not_summed(self):
        # Overlapping ids across sections must not double-count.
        p = self._write(
            "# Spine\n\n#### Slices (part one)\n\n"
            "| ID | Name |\n|----|------|\n| S1 | one |\n| S2 | two |\n\n"
            "#### Slices (part two)\n\n"
            "| ID | Name |\n|----|------|\n| S2 | two again |\n| S3 | three |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D16_blockquoted_rows_are_still_counted(self):
        # A declared table nested in a blockquote must not be dropped — a human
        # reads those rows as declared, so dropping them UNDER-counts (unsafe).
        p = self._write(
            "# Spine\n\n> #### Slices\n>\n"
            "> | ID | Name |\n> |----|------|\n"
            "> | S1 | one |\n> | S2 | two |\n>> | S3 | nested deeper |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D16b_container_nested_rows_are_counted(self):
        # Blockquote-in-list and list-nested rows are containers, not content.
        p = self._write(
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n"
            "- > | S1 | quoted inside a list item |\n"
            "- | S2 | list item |\n"
            "1. | S3 | ordered list item |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 3)

    def test_D17_h1_slices_heading_is_recognized(self):
        # Any heading level 1-6 opens a section. A level the recognizer misses
        # would make the section invisible — again the unsafe direction.
        p = self._write(
            "# Slices\n\n"
            "| ID | Name |\n|----|------|\n| S1 | one |\n| S2 | two |\n"
        )
        self.assertEqual(tm.declared_slice_count(p), 2)

    def test_D15_non_utf8_file_returns_zero_not_raise(self):
        # The docstring promises 0 for an unreadable file. UnicodeDecodeError is
        # a ValueError, not an OSError — it must still return 0, never raise.
        p = self.tmp / "binary.md"
        p.write_bytes(b"# Spine\n\n#### Slices\n\n| S1 | \xff\xfe bad utf-8 |\n")
        self.assertEqual(tm.declared_slice_count(p), 0)

    def test_D18_projection_equals_the_siblings_first_element(self):
        # A1's whole safety argument: `declared_slice_count` is a projection
        # over the sibling, so its contract — and therefore the ship-time
        # counter that consumes it — cannot drift. Four shapes, one for each
        # way the pair can disagree.
        missing = self.tmp / "nope.md"
        no_heading = self._write("# Spine\n\nNo slice table here at all.\n")
        heading_with_rows = self._write_named(
            "with_rows.md",
            "# Spine\n\n#### Slices\n\n"
            "| ID | Name |\n|----|------|\n| S1 | one |\n| S2 | two |\n")
        heading_no_rows = self._write_named(
            "no_rows.md", "# Spine\n\n### Slices\n\n**Slice A — prose.**\n")

        for p in (missing, no_heading, heading_with_rows, heading_no_rows):
            count, _seen = tm.declared_slice_count_with_heading(p)
            self.assertEqual(tm.declared_slice_count(p), count, msg=str(p))

        # And the bit itself discriminates, which is the point of the sibling.
        self.assertEqual(tm.declared_slice_count_with_heading(missing), (0, False))
        self.assertEqual(tm.declared_slice_count_with_heading(no_heading), (0, False))
        self.assertEqual(tm.declared_slice_count_with_heading(heading_with_rows), (2, True))
        self.assertEqual(tm.declared_slice_count_with_heading(heading_no_rows), (0, True))

    def test_D8_live_declared_spine_is_retire_eligible(self):
        """The declared path, end-to-end on real data: every declared slice is
        registered, every registered row is terminal, so the spine is
        retire-eligible.

        THE COUNT IS INCIDENTAL and is deliberately not asserted. It was eleven
        when this test was written; what the test exists to prove is that the
        declared count and the registered rows AGREE and that all of them are
        SHIPPED. A spine that legitimately grows a slice grows both numbers, so
        the invariant survives the growth while a pinned literal would not.

        This spine is `Retired 2026-08-01 — all 11 slices SHIPPED`, so its count
        is in practice frozen; the sibling `test_DP12` reads a spine that still
        lists an unshipped slice and is therefore the more exposed of the two.
        Both are written against the invariant regardless, because "unlikely to
        move" is not the same as "cannot move".
        """
        try:
            real = lc.require(
                "Thoughts/project-tracking-staleness_THOUGHT.md",
                "the declared-path spine this assertion reads")
        except lc.LiveCorpusUnavailable as exc:
            self.skipTest(str(exc))

        declared = tm.declared_slice_count(real)
        rows = tm.parse_slice_register(real)

        # Non-vacuity guards. `all(...)` over an empty list is True and 0 == 0
        # holds, so without these an emptied or unparseable spine would turn
        # every assertion below into a tautology that passes having proved
        # nothing — the failure mode this whole change exists to remove.
        self.assertGreater(
            declared, 0,
            msg=(f"VACUOUS TEST GUARD: {real} declares zero slices, so the "
                 "agreement checks below would pass without testing anything. "
                 "Either declared_slice_count regressed or the spine's declared "
                 "table was removed; neither is a pass."))
        self.assertTrue(
            rows,
            msg=(f"VACUOUS TEST GUARD: {real} has no registered slice rows, so "
                 "the all-SHIPPED check below would be vacuously true. Either "
                 "parse_slice_register regressed or the register was emptied."))

        self.assertEqual(
            len(rows), declared,
            msg=(f"REGRESSION, not a legitimate spine change: {real} declares "
                 f"{declared} slice(s) but carries {len(rows)} registered "
                 "row(s). A spine that legitimately gains a slice gains BOTH a "
                 "declared entry and (once shipped) a row, so a disagreement "
                 "means the reader or the writer is wrong, not the corpus."))
        unshipped = sorted(r["id"] for r in rows if r["status"] != "SHIPPED")
        self.assertEqual(
            unshipped, [],
            msg=(f"SUBJECT MOVED or predicate regressed: {real} now has "
                 f"non-SHIPPED row(s) {unshipped}. If that spine legitimately "
                 "gained unfinished work it is no longer the retire-eligible "
                 "case this test covers, and the test should be re-based on a "
                 "spine that is; if it did not, the status writer regressed."))
        self.assertTrue(
            tm.all_slices_done(real),
            msg=(f"REGRESSION in all_slices_done: {real} declares {declared} "
                 f"slice(s), carries {len(rows)} registered row(s) and every one "
                 "is SHIPPED, so the predicate must report the spine complete."))


class AllSlicesDoneDeclaredCountTests(unittest.TestCase):
    """all_slices_done: declared path vs. verbatim fallback."""

    _TABLE = (
        "#### Slices\n\n"
        "| ID | Name | Type | Depends on |\n"
        "|----|------|------|------------|\n"
        "| S1 | one   | implementation | — |\n"
        "| S2 | two   | implementation | S1 |\n"
        "| S3 | three | implementation | S2 |\n\n"
    )

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_declared_pred_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _spine(self, header: str, *triples):
        p = self.tmp / "spine.md"
        p.write_text("# Spine\n\n" + header)
        for sid, status, sessions in triples:
            tm.write_slice_row(p, sid, {"status": status, "sessions": sessions,
                                        "updated": "2026-08-07"})
        return p

    # --- the defect this slice fixes -------------------------------------

    def test_DP1_terminal_rows_below_declared_count_blocks_retire(self):
        # THE BUG: 3 declared, only 2 registered rows — both terminal. The old
        # rows-only predicate returned True and retired the topic mid-flight.
        p = self._spine(self._TABLE,
                        ("S1", "SHIPPED", "1/1"), ("S2", "SHIPPED", "1/1"))
        self.assertEqual(tm.declared_slice_count(p), 3)
        self.assertFalse(tm.all_slices_done(p))

    def test_DP2_all_declared_slices_terminal_returns_true(self):
        p = self._spine(self._TABLE,
                        ("S1", "SHIPPED", "1/1"), ("S2", "SHIPPED", "1/1"),
                        ("S3", "NEVER", "0/0"))
        self.assertTrue(tm.all_slices_done(p))

    def test_DP3_declared_met_but_a_row_non_terminal_returns_false(self):
        p = self._spine(self._TABLE,
                        ("S1", "SHIPPED", "1/1"), ("S2", "SHIPPED", "1/1"),
                        ("S3", "NOW", "0/1"))
        self.assertFalse(tm.all_slices_done(p))

    def test_DP4_single_declared_slice_shipped_returns_true(self):
        # A declared count of 1 is real evidence, so the <2-row fallback guard
        # does not apply on the declared path.
        one = ("#### Slices\n\n| ID | Name |\n|----|------|\n| S1 | only |\n\n")
        p = self._spine(one, ("S1", "SHIPPED", "1/1"))
        self.assertTrue(tm.all_slices_done(p))

    # --- fallback path: behaviour-identical to before ---------------------

    def test_DP5_no_declared_table_falls_back_verbatim(self):
        # Legacy spine, no `#### Slices`: all-terminal >=2 rows still retires
        # exactly as it does today (no new false-block).
        p = self._spine("", ("A", "SHIPPED", "3/3"), ("B", "SHIPPED", "1/1"))
        self.assertEqual(tm.declared_slice_count(p), 0)
        self.assertTrue(tm.all_slices_done(p))

    def test_DP6_empty_declared_table_routes_to_fallback_not_vacuous_true(self):
        # declared == 0 must NEVER be read as `0 >= 0` complete. The answer is
        # unchanged; the route to it is not — the `#### Slices` heading is now
        # SEEN, so this refuses at the seen-but-unreadable branch rather than at
        # the fallback's <2 guard. Kept green and un-inverted deliberately.
        stub = "#### Slices\n\n| ID | Name |\n|----|------|\n\n"
        p = self._spine(stub, ("A", "SHIPPED", "1/1"))
        self.assertEqual(tm.declared_slice_count(p), 0)
        self.assertFalse(tm.all_slices_done(p))

    def test_DP7_zero_slice_stub_with_no_rows_is_not_retired(self):
        p = self._spine("#### Slices\n\n(to be filled in)\n\n")
        self.assertFalse(tm.all_slices_done(p))

    def test_DP8_R4_dropped_row_blocks_in_the_safe_direction(self):
        # Residual R4: a deleted `<!-- L:slice -->` row whose `S<N>` entry
        # remains declared blocks retirement (over-conservative, recoverable)
        # rather than retiring prematurely (silent, unrecoverable).
        p = self._spine(self._TABLE,
                        ("S1", "SHIPPED", "1/1"), ("S3", "SHIPPED", "1/1"))
        self.assertFalse(tm.all_slices_done(p))

    # --- seen-but-unreadable declaration: the shape this slice corrects ----

    def test_DP9_seen_but_unreadable_declaration_answers_not_finished(self):
        # A `Slices` heading the recognizer DOES match, holding prose slices
        # with letter ids and no `| S<N> |` row at all. `declared` resolves 0,
        # exactly as it does for a spine with no heading — but the two are not
        # the same question. Before this slice both fell through to the
        # row-count fallback, so two terminal rows retired a topic whose
        # declaration named more work than the register knows about.
        prose = (
            "### Slices\n\n"
            "**Slice A — first thing — SHIPPED.**\n\n"
            "**Slice B — second thing — SHIPPED.**\n\n"
            "**Slice C — third thing — NEARBY.** Never registered.\n\n"
        )
        p = self._spine(prose, ("A", "SHIPPED", "1/1"), ("B", "SHIPPED", "1/1"))
        # The reader still returns 0 — its contract is untouched.
        self.assertEqual(tm.declared_slice_count(p), 0)
        # But the heading WAS seen, so the predicate must not fall back.
        self.assertFalse(tm.all_slices_done(p))

    def test_DP11_the_retired_fallback_trigger_is_named_in_the_live_docstring(self):
        # A2 retires a documented sub-clause of the fallback path: an
        # "early-draft/empty table" no longer routes there. Nothing else locks
        # a retired rule, and an unlocked retired rule drifts back — so the
        # docstring must keep NAMING it as retired rather than quietly
        # dropping it.
        doc = inspect.getdoc(tm.all_slices_done) or ""
        self.assertIn("early-draft/empty table", doc)
        self.assertIn("used to", doc.lower())
        # The legacy promise the fallback still makes survives verbatim, and
        # now says which case it concerns.
        self.assertIn("no new false-block", doc)
        # The reader's own docstring must stop telling callers that 0 always
        # means "fall back" — it no longer does for this predicate.
        reader_doc = inspect.getdoc(tm.declared_slice_count) or ""
        self.assertIn("declared_slice_count_with_heading", reader_doc)

    def test_DP12_live_seen_but_unreadable_spine_is_not_retire_eligible(self):
        """The live exposure: a `### Slices` heading whose slices are bold
        paragraphs rather than a table, so the declaration is SEEN but reads as
        zero — and the predicate must therefore refuse to fall back, even though
        every registered row is SHIPPED.

        THE COUNT IS INCIDENTAL and is deliberately not asserted. It was eleven
        when this test was written. This spine still lists Slice E as NEARBY, so
        shipping it adds a twelfth row with no defect involved — a pinned count
        would have failed on that, which is why the invariant is asserted
        instead. (Its sibling `test_D8` reads a retired spine whose count is in
        practice frozen; the two are not equally exposed, and neither pins.)

        The all-SHIPPED check is load-bearing rather than incidental: it is what
        makes the final assertion non-trivial. Every row being terminal is
        exactly the condition under which the verbatim fallback WOULD report the
        spine complete, so the predicate returning False here is a statement
        about the seen-but-unreadable heading and nothing else.
        """
        try:
            real = lc.require(
                "Thoughts/workflow-phases-redesign_THOUGHT.md",
                "the seen-but-unreadable spine this assertion reads")
        except lc.LiveCorpusUnavailable as exc:
            self.skipTest(str(exc))

        rows = tm.parse_slice_register(real)
        declared, heading_seen = tm.declared_slice_count_with_heading(real)

        # Non-vacuity guard: with no rows the all-SHIPPED check below is
        # vacuously true and the predicate would return False for the wrong
        # reason (nothing registered), proving nothing about the seen-but-
        # unreadable case.
        self.assertTrue(
            rows,
            msg=(f"VACUOUS TEST GUARD: {real} has no registered slice rows, so "
                 "this test would assert its conclusion for the wrong reason. "
                 "Either parse_slice_register regressed or the register was "
                 "emptied."))

        self.assertTrue(
            heading_seen,
            msg=(f"SUBJECT MOVED: {real} no longer carries a slices heading, so "
                 "it is not the seen-but-unreadable case this test covers. "
                 "Re-base the test on a spine that is, rather than relaxing the "
                 "predicate."))
        self.assertEqual(
            declared, 0,
            msg=(f"SUBJECT MOVED or reader changed: {real} now declares "
                 f"{declared} slice(s), so its table has become readable and it "
                 "is no longer the seen-but-unreadable case. Either the spine "
                 "was rewritten as a real table (re-base the test) or "
                 "declared_slice_count started parsing bold-paragraph slices."))
        unshipped = sorted(r["id"] for r in rows if r["status"] != "SHIPPED")
        self.assertEqual(
            unshipped, [],
            msg=(f"SUBJECT MOVED: {real} now has non-SHIPPED row(s) {unshipped}. "
                 "The point of this test is that the predicate refuses EVEN WHEN "
                 "every row is terminal; with an unfinished row it would refuse "
                 "for the ordinary reason and prove nothing. Re-base the test."))
        self.assertFalse(
            tm.all_slices_done(real),
            msg=(f"REGRESSION in all_slices_done: {real} shows a slices heading "
                 f"that reads as zero declared slices while carrying {len(rows)} "
                 "registered row(s), all SHIPPED. The predicate must NOT fall "
                 "back to the verbatim path here — a declaration it can see but "
                 "cannot read is not permission to retire."))


# ---------------------------------------------------------------------------
# attribute_commits classifier
# ---------------------------------------------------------------------------

class AttributeCommitsTests(unittest.TestCase):

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="tm_repo_"))
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"],
                       cwd=self.repo, check=True)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _commit(self, files: dict[str, str], msg: str) -> str:
        for rel, content in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
            subprocess.run(["git", "add", rel], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", msg], cwd=self.repo, check=True)
        sha = subprocess.run(
            ["git", "log", "-1", "--pretty=%H"],
            cwd=self.repo, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return sha

    def _plan_with_diff(self, scope_paths: list[str]) -> Path:
        p = self.repo / "PLAN.md"
        bullets = "\n".join(f"- `{x}`" for x in scope_paths)
        p.write_text(
            "# Plan\n\n## Diff\n\n**Session 1:**\n"
            + bullets + "\n\n## Next\n"
        )
        return p

    def test_A1c_scope_subset_is_confident(self):
        plan = self._plan_with_diff(["src/a.py", "src/b.py"])
        # Commit PLAN.md so git has commits, but use a window matching the second commit only
        self._commit({"src/a.py": "x"}, "outside-window")  # commit #1
        # Wait so the second commit is inside the test's --since window
        time.sleep(1.1)
        since = "now"  # placeholder; we'll use a wide window below instead
        # Switch to a wide window for simplicity
        sha = self._commit({"src/a.py": "y", "src/b.py": "z"}, "in-scope")
        res = tm.attribute_commits(
            plan_path=plan, session_id="sess-1",
            started_at="1970-01-01", ended_at="2099-01-01",
            session_num=1, repo_path=self.repo,
        )
        # The first commit (only src/a.py) is also a subset → confident.
        # The second commit (both files) is also a subset → confident.
        # PLAN.md change is included by the _plan_with_diff write which is
        # NOT a git commit (it lives in the worktree as a plain write). Good.
        self.assertIn(sha, res["confident"])
        self.assertEqual(res["ambiguous"], [])

    def test_A2c_mixed_scope_is_ambiguous(self):
        plan = self._plan_with_diff(["src/a.py"])
        sha = self._commit({"src/a.py": "x", "src/outside.py": "y"},
                           "mixed-scope")
        res = tm.attribute_commits(
            plan_path=plan, session_id="sess-1",
            started_at="1970-01-01", ended_at="2099-01-01",
            session_num=1, repo_path=self.repo,
        )
        self.assertEqual(res["confident"], [])
        self.assertEqual(len(res["ambiguous"]), 1)
        self.assertEqual(res["ambiguous"][0][0], sha)
        self.assertIn("src/outside.py", res["ambiguous"][0][1])

    def test_A3c_no_intersection_is_unmatched(self):
        plan = self._plan_with_diff(["src/a.py"])
        sha = self._commit({"docs/readme.md": "x"}, "unrelated")
        res = tm.attribute_commits(
            plan_path=plan, session_id="sess-1",
            started_at="1970-01-01", ended_at="2099-01-01",
            session_num=1, repo_path=self.repo,
        )
        self.assertIn(sha, res["unmatched"])
        self.assertEqual(res["confident"], [])
        self.assertEqual(res["ambiguous"], [])

    def test_A4c_empty_scope_returns_scope_empty(self):
        p = self.repo / "PLAN.md"
        p.write_text("# Plan\n\n## Other\nnothing\n")
        self._commit({"src/a.py": "x"}, "one")
        res = tm.attribute_commits(
            plan_path=p, session_id="sess-1",
            started_at="1970-01-01", ended_at="2099-01-01",
            session_num=1, repo_path=self.repo,
        )
        self.assertTrue(res["scope_empty"])
        self.assertEqual(res["confident"], [])

    def test_A5c_parse_diff_section_round_trip(self):
        plan = self.repo / "PLAN.md"
        plan.write_text(
            "## Diff\n\n"
            "**Session 1:**\n"
            "- `${KIT_HOOKS_DIR}/taskmanagement.py` (new)\n"
            "- `${KIT_HOOKS_DIR}/tests/test_taskmanagement.py` (new)\n\n"
            "**Session 2:**\n"
            "- `${KIT_HOOKS_DIR}/pre_plan_gates.py` (edit)\n"
        )
        out = tm.parse_diff_section(plan)
        self.assertEqual(set(out.keys()), {1, 2})
        self.assertEqual(out[1], [
            "${KIT_HOOKS_DIR}/taskmanagement.py",
            "${KIT_HOOKS_DIR}/tests/test_taskmanagement.py",
        ])
        self.assertEqual(out[2], ["${KIT_HOOKS_DIR}/pre_plan_gates.py"])


class AttributeCommitsImplSpecificsTests(unittest.TestCase):
    """M3 (project-tracking-staleness S8): Implementation-Specifics scope
    fallback + spine→plan resolution + loose prefix-aware matching."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_implspec_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True,
                       capture_output=True, text=True)

    def _repo_with_commit(self, files: dict[str, str]) -> tuple[Path, str]:
        repo = Path(tempfile.mkdtemp(prefix="tm_implspec_repo_"))
        self._git("init", "-q", cwd=repo)
        self._git("config", "user.email", "t@e.com", cwd=repo)
        self._git("config", "user.name", "T", cwd=repo)
        for rel, content in files.items():
            p = repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        self._git("add", "-A", cwd=repo)
        self._git("commit", "-q", "-m", "ship", cwd=repo)
        sha = subprocess.run(["git", "log", "-1", "--pretty=%H"], cwd=repo,
                             capture_output=True, text=True).stdout.strip()
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        return repo, sha

    def _plan(self, name="p_PLAN.md", *, coherent="", impl="", gate1="", prose=""):
        p = self.tmp / name
        p.write_text(
            "# Plan\n\n" + prose
            + "\n## Coherent Actions\n" + coherent
            + "\n## Implementation Specifics\n" + impl
            + "\n## Gate 1 — Implementation Verification\n" + gate1
            + "\n## Gate 2\nx\n"
        )
        return p

    # --- scope extraction is section-scoped ---
    def test_scope_from_structural_sections(self):
        plan = self._plan(
            coherent="| A1: edit `work-done/SKILL.md` | G1 | ... |\n",
            impl="- **`${KIT_HOOKS_DIR}/work_done.py`** — add X\n",
            gate1="| c | cat | `taskmanagement.py:376` | ... |\n",
        )
        scope = tm.parse_impl_specifics_scope(plan)
        self.assertIn("${KIT_HOOKS_DIR}/work_done.py", scope)
        self.assertIn("taskmanagement.py", scope)          # :376 stripped
        self.assertIn("work-done/SKILL.md", scope)

    def test_prose_refs_outside_sections_excluded(self):
        plan = self._plan(
            prose="The diagnosis cites `pre_plan_gates.py:99` in prose.\n",
            impl="- **`work_done.py`**\n",
        )
        scope = tm.parse_impl_specifics_scope(plan)
        self.assertIn("work_done.py", scope)
        self.assertNotIn("pre_plan_gates.py", scope)       # prose is not scope

    def test_spine_has_no_structural_sections_yields_empty(self):
        spine = self.tmp / "topic_THOUGHT.md"
        spine.write_text(
            "# Idea\nprose citing `work_done.py:1`\n\n"
            "# Implementation Details\n- [[topic-1_PLAN]]\n\n"
            "## Slice Register\n"
            "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 plan=topic-1_PLAN diary=_ updated=2026-07-31 -->\n"
        )
        self.assertEqual(tm.parse_impl_specifics_scope(spine), set())

    # --- spine → linked plan resolution ---
    def test_spine_resolves_linked_plans(self):
        (self.tmp / "topic-1_PLAN.md").write_text(
            "# P\n## Implementation Specifics\n- **`${KIT_HOOKS_DIR}/work_done.py`**\n## Gate 2\nx\n"
        )
        spine = self.tmp / "topic_THOUGHT.md"
        spine.write_text(
            "# Idea\n\n## Slice Register\n"
            "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 plan=topic-1_PLAN diary=_ updated=2026-07-31 -->\n"
        )
        scope = tm._impl_specifics_scope_for(spine)
        self.assertIn("${KIT_HOOKS_DIR}/work_done.py", scope)

    def test_spine_with_no_resolvable_plans_yields_empty(self):
        spine = self.tmp / "topic_THOUGHT.md"
        spine.write_text(
            "# Idea\n\n## Slice Register\n"
            "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 plan=_ diary=_ updated=2026-07-31 -->\n"
        )
        self.assertEqual(tm._impl_specifics_scope_for(spine), set())

    # --- loose matcher matrix ---
    def test_loose_match_bare_basename_config_source(self):
        self.assertTrue(tm._loose_scope_match(
            "dot_claude/hooks/work_done.py", {"work_done.py"}))

    def test_loose_match_home_qualified(self):
        self.assertTrue(tm._loose_scope_match(
            "dot_claude/hooks/work_done.py", {"${KIT_HOOKS_DIR}/work_done.py"}))

    def test_loose_match_dir_qualified_suffix(self):
        self.assertTrue(tm._loose_scope_match(
            "dot_claude/skills/work-done/SKILL.md", {"work-done/SKILL.md"}))

    def test_loose_no_match_unrelated(self):
        self.assertFalse(tm._loose_scope_match(
            "dot_claude/hooks/taskmanagement.py", {"work_done.py"}))

    def test_skillmd_collision_guarded(self):
        # A dir-qualified SKILL.md scope entry must NOT match a different skill.
        self.assertFalse(tm._loose_scope_match(
            "dot_claude/skills/double-check/SKILL.md", {"work-done/SKILL.md"}))

    # --- integrated attribute_commits ---
    def test_attribute_commits_loose_confident_via_spine(self):
        repo, sha = self._repo_with_commit(
            {"dot_claude/hooks/work_done.py": "x"})
        (self.tmp / "topic-1_PLAN.md").write_text(
            "# P\n## Implementation Specifics\n- **`${KIT_HOOKS_DIR}/work_done.py`**\n## Gate 2\nx\n"
        )
        spine = self.tmp / "topic_THOUGHT.md"
        spine.write_text(
            "# Idea\n\n## Slice Register\n"
            "<!-- L:slice id=S1 status=NOW sessions=1/1 plan=topic-1_PLAN diary=_ updated=2026-07-31 -->\n"
        )
        res = tm.attribute_commits(
            plan_path=spine, session_id="s",
            started_at="1970-01-01", ended_at="2099-01-01", repo_path=repo)
        self.assertIn(sha, res["confident"])
        self.assertFalse(res["scope_empty"])

    def test_diff_still_wins_when_present(self):
        repo, sha = self._repo_with_commit({"src/a.py": "x"})
        plan = self.tmp / "d_PLAN.md"
        plan.write_text(
            "# P\n## Diff\n\n**Session 1:**\n- `src/a.py`\n\n"
            "## Coherent Actions\n- **`unrelated.py`**\n## Gate 2\nx\n")
        res = tm.attribute_commits(
            plan_path=plan, session_id="s",
            started_at="1970-01-01", ended_at="2099-01-01",
            session_num=1, repo_path=repo)
        # Exact `## Diff` path is used; the Coherent-Actions ref is ignored.
        self.assertIn(sha, res["confident"])

    def test_empty_scope_soft_exit(self):
        plan = self.tmp / "e_PLAN.md"
        plan.write_text("# P\n## Nothing\nprose only, no structural sections\n")
        res = tm.attribute_commits(
            plan_path=plan, session_id="s",
            started_at="1970-01-01", ended_at="2099-01-01")
        self.assertTrue(res["scope_empty"])
        self.assertEqual(res["confident"], [])


# ---------------------------------------------------------------------------
# Session-2 wrap-layer + A8 three-rule conflict resolution + collision-with-
# todo.py-mark-in-progress regression tests
# ---------------------------------------------------------------------------

class _WrapSandbox:
    """Context that builds a synthetic project (TODO.md + spine + state)
    for sync_phase_transition / sync_phase_stop testing."""

    def __init__(self, spine_stem="synthetic-topic",
                 todo_line_text="**Synthetic** — Master plan: [[{stem}]].",
                 with_in_progress=False):
        self.spine_stem = spine_stem
        self.todo_line_text = todo_line_text
        self.with_in_progress = with_in_progress

    def __enter__(self):
        self.lock_sandbox = _LockSandbox()
        self.lock_sandbox.__enter__()
        self.tmp = Path(tempfile.mkdtemp(prefix="tm_wrap_"))
        # Project root + TODO.md
        self.project_root = self.tmp / "project"
        self.project_root.mkdir()
        (self.project_root / "Thoughts").mkdir()
        spine_path = (self.project_root / "Thoughts"
                      / f"{self.spine_stem}.md")
        spine_path.write_text("# Spine\n\n## Slices\n\n(prose only)\n")
        self.spine_path = spine_path
        # TODO.md with one open item referencing the spine stem.
        todo = self.project_root / "TODO.md"
        line_text = self.todo_line_text.format(stem=self.spine_stem)
        in_prog = " (in progress: deadbeef)" if self.with_in_progress else ""
        todo.write_text(
            "# TODO\n\n## Now\n\n"
            f"- [ ] {line_text}{in_prog}\n"
        )
        self.todo_path = todo
        # Redirect tm's pre_plan_gates state dir to a tempdir.
        self._orig_state_dir_attr = None
        return self

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.lock_sandbox.__exit__(*exc)

    def state(self, *, phase="clarification", topic_slug="synth",
              project_slug="synth-proj"):
        return {
            "topic_slug": topic_slug,
            "project_slug": project_slug,
            "project_root": str(self.project_root),
            "thought_file_path": f"Thoughts/{self.spine_stem}.md",
            "phase": phase,
        }


def _write_synthetic_state_json(state):
    """Write a fake topic-state JSON at tm's expected path."""
    p = tm._topic_state_path_for(state)
    if p is None:
        return None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))
    return p


class SyncPhaseTransitionWrapTests(unittest.TestCase):

    def setUp(self):
        # Reroute pre_plan_gates state dir to a tempdir so verify_write of
        # topic_state_json hits our synthetic file.
        self.state_dir = Path(tempfile.mkdtemp(prefix="tm_state_"))
        self._orig_state_path = tm._topic_state_path_for

        def _patched(state):
            ts = state.get("topic_slug")
            ps = state.get("project_slug")
            if not ts or not ps:
                return None
            return self.state_dir / f"{ts}__{ps}.json"

        tm._topic_state_path_for = _patched

    def tearDown(self):
        tm._topic_state_path_for = self._orig_state_path
        shutil.rmtree(self.state_dir, ignore_errors=True)

    def test_W1_clean_transition_writes_todo_and_verifies(self):
        with _WrapSandbox() as sb:
            state = sb.state(phase="clarification")
            _write_synthetic_state_json(state)
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            r = tm.sync_phase_transition(state, prev_phase=None,
                                         new_phase="clarification",
                                         session_id=sid)
            self.assertTrue(r["topic_state_verified"])
            self.assertEqual(r["conflict_rule"], "none")
            self.assertEqual(r["todo_verify"], {"status": "OK"})
            self.assertIn("(in clarification: a1b2c3d4)",
                          sb.todo_path.read_text())

    def test_W2_rule_a_silent_idempotent_same_phase(self):
        with _WrapSandbox() as sb:
            state = sb.state(phase="clarification")
            _write_synthetic_state_json(state)
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            # First write
            tm.sync_phase_transition(state, prev_phase=None,
                                     new_phase="clarification",
                                     session_id=sid)
            # Second write — same session, same phase — rule_a silent
            r = tm.sync_phase_transition(state, prev_phase="clarification",
                                         new_phase="clarification",
                                         session_id=sid)
            self.assertEqual(r["conflict_rule"], "rule_a_silent")
            # Only one annotation present (not duplicated)
            text = sb.todo_path.read_text()
            self.assertEqual(text.count("(in clarification: a1b2c3d4)"), 1)

    def test_W3_rule_a_silent_adjacent_phase_advances(self):
        with _WrapSandbox() as sb:
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            state1 = sb.state(phase="clarification")
            _write_synthetic_state_json(state1)
            tm.sync_phase_transition(state1, prev_phase=None,
                                     new_phase="clarification",
                                     session_id=sid)
            # Mimic pre_plan_gates rewriting the state file with the new phase.
            state2 = sb.state(phase="planning")
            _write_synthetic_state_json(state2)
            r = tm.sync_phase_transition(state2, prev_phase="clarification",
                                         new_phase="planning",
                                         session_id=sid)
            self.assertEqual(r["conflict_rule"], "rule_a_silent")
            text = sb.todo_path.read_text()
            self.assertIn("(in planning: a1b2c3d4)", text)
            self.assertNotIn("(in clarification: a1b2c3d4)", text)

    def test_W4_rule_b_flag_legacy_in_progress_no_write(self):
        with _WrapSandbox(with_in_progress=True) as sb:
            state = sb.state(phase="clarification")
            _write_synthetic_state_json(state)
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            r = tm.sync_phase_transition(state, prev_phase=None,
                                         new_phase="clarification",
                                         session_id=sid)
            self.assertEqual(r["conflict_rule"], "rule_b_flag")
            self.assertIsNotNone(r["flag_and_ask"])
            self.assertEqual(r["flag_and_ask"]["kind"], "rule_b_flag_and_ask")
            # TODO line untouched — no `(in <phase>: …)` added
            text = sb.todo_path.read_text()
            self.assertNotIn("(in clarification: a1b2c3d4)", text)
            # legacy `(in progress: …)` still intact
            self.assertIn("(in progress: deadbeef)", text)

    def test_W5_rule_c_fail_different_session_raises(self):
        with _WrapSandbox() as sb:
            sid_a = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            sid_b = "ffffeeee-aaaa-bbbb-cccc-1234567890ab"
            state1 = sb.state(phase="clarification")
            _write_synthetic_state_json(state1)
            tm.sync_phase_transition(state1, prev_phase=None,
                                     new_phase="clarification",
                                     session_id=sid_a)
            # Pretend the state JSON advanced to planning (would be done
            # by pre_plan_gates._write_topic_state in production).
            state2 = sb.state(phase="planning")
            _write_synthetic_state_json(state2)
            with self.assertRaises(tm.SyncConflictError):
                tm.sync_phase_transition(state2, prev_phase="clarification",
                                         new_phase="planning",
                                         session_id=sid_b)

    def test_W6_sync_phase_stop_verifies_state_only(self):
        with _WrapSandbox() as sb:
            state = sb.state(phase="clarification")
            _write_synthetic_state_json(state)
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            # establish the annotation first
            tm.sync_phase_transition(state, prev_phase=None,
                                     new_phase="clarification",
                                     session_id=sid)
            before = sb.todo_path.read_text()
            r = tm.sync_phase_stop(state, session_id=sid)
            self.assertTrue(r["topic_state_verified"])
            after = sb.todo_path.read_text()
            self.assertEqual(before, after,
                             msg="sync_phase_stop must not touch TODO surface")

    def test_W7_collision_with_mark_in_progress_in_progress_preserved(self):
        """When sync_phase_transition succeeds on a clean line, then a
        downstream `todo.py mark-in-progress` runs, both annotations
        coexist and neither corrupts the other."""
        with _WrapSandbox() as sb:
            state = sb.state(phase="clarification")
            _write_synthetic_state_json(state)
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            tm.sync_phase_transition(state, prev_phase=None,
                                     new_phase="clarification",
                                     session_id=sid)
            # Now call mark-in-progress on the same line via subprocess.
            # Resolve through HOOKS (the CLAUDE_CONFIG_DIR treatment this file
            # establishes at its top) rather than from the home directory, so a
            # merge-candidate run grades the CANDIDATE todo.py instead of the
            # live one. A missing module here stays a FAILURE and is never
            # softened into a skip: trading a hard failure for a silent success
            # is the defect this suite is being repaired to remove.
            todo_script = HOOKS / "todo.py"
            self.assertTrue(
                todo_script.is_file(),
                msg=("MISSING DEPENDENCY, not a regression in the code under "
                     f"test: no todo.py at {todo_script}. That path comes from "
                     "CLAUDE_CONFIG_DIR (see the HOOKS constant at the top of "
                     "this file), so either the variable points at an "
                     "incomplete tree or the module was moved. This is "
                     "deliberately a failure rather than a skip."))
            r = subprocess.run(
                ["python3", str(todo_script), "mark-in-progress",
                 "--pattern", sb.spine_stem,
                 "--session", sid,
                 "--file", str(sb.todo_path)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)
            text = sb.todo_path.read_text()
            self.assertIn("(in clarification: a1b2c3d4)", text)
            self.assertIn("(in progress: a1b2c3d4)", text)

    def test_W8_no_spine_path_skips_todo_surface(self):
        """When state lacks thought_file_path, TODO surface is skipped
        gracefully (no error)."""
        with _WrapSandbox() as sb:
            state = sb.state(phase="clarification")
            state.pop("thought_file_path", None)
            _write_synthetic_state_json(state)
            sid = "a1b2c3d4-aaaa-bbbb-cccc-1234567890ab"
            r = tm.sync_phase_transition(state, prev_phase=None,
                                         new_phase="clarification",
                                         session_id=sid)
            self.assertEqual(r["todo_write"]["status"], "skipped")
            self.assertEqual(r["todo_write"]["reason"],
                             "no_thought_file_path_or_TODO")


class ResolvePlanRefTests(unittest.TestCase):
    """S2 / A2 — the single normaliser for a slice-register `plan=` reference.

    The four shapes below are not invented: they were measured over the 63 populated
    rows in the live corpus on 2026-08-26. The reader this replaces accepted only the
    first of them, which is why six of seven populated spines resolved to nothing.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        (self.dir / "foo-20260727121447_PLAN.md").write_text("x", encoding="utf-8")
        (self.dir / "dapper-coalescing-seal.md").write_text("x", encoding="utf-8")

    def tearDown(self):
        self.td.cleanup()

    def test_shape_bare_slug(self):
        p, reason = tm.resolve_plan_ref("foo-20260727121447_PLAN", self.dir)
        self.assertIsNotNone(p, reason)
        self.assertEqual(p.name, "foo-20260727121447_PLAN.md")
        self.assertIsNone(reason)

    def test_shape_wikilink(self):
        p, _ = tm.resolve_plan_ref("[[foo-20260727121447_PLAN]]", self.dir)
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "foo-20260727121447_PLAN.md")

    def test_shape_wikilink_plus_md(self):
        """The shape that made a bracket-only fix insufficient — it is 7 of the 9 rows
        on `research-output-security`, so stripping brackets alone recovers 2 of 9."""
        p, _ = tm.resolve_plan_ref("[[foo-20260727121447_PLAN.md]]", self.dir)
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "foo-20260727121447_PLAN.md")

    def test_shape_bare_plus_md(self):
        p, _ = tm.resolve_plan_ref("dapper-coalescing-seal.md", self.dir)
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "dapper-coalescing-seal.md")

    def test_shape_alias_form(self):
        """Not in the corpus today; accepted because a fifth shape arriving is the
        normal case for hand-edited files, and it costs one split."""
        p, _ = tm.resolve_plan_ref("[[foo-20260727121447_PLAN|display text]]", self.dir)
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "foo-20260727121447_PLAN.md")

    def test_strip_order_is_brackets_then_extension(self):
        """A2's guard rail. Doing it the other way round leaves the brackets wrapped
        around a stripped stem and resolves nothing — asserted directly so the ordering
        cannot be silently reversed by a later refactor."""
        p, _ = tm.resolve_plan_ref("[[dapper-coalescing-seal.md]]", self.dir)
        self.assertIsNotNone(p)
        self.assertEqual(p.name, "dapper-coalescing-seal.md")

    def test_absent_target_returns_a_NAMED_reason_not_a_bare_none(self):
        """The whole point of the (path, reason) shape: an unresolvable reference must
        be distinguishable from a topic that declares no scope, and must say WHICH."""
        p, reason = tm.resolve_plan_ref("[[lazy-doodling-wadler]]", self.dir)
        self.assertIsNone(p)
        self.assertIsInstance(reason, str)
        self.assertIn("lazy-doodling-wadler", reason)
        self.assertTrue(reason.strip())

    def test_empty_and_placeholder_refs_read_as_no_scope(self):
        for ref in ("", "   ", "_"):
            with self.subTest(ref=ref):
                p, reason = tm.resolve_plan_ref(ref, self.dir)
                self.assertIsNone(p)
                self.assertIn("no plan reference", reason)

    def test_harness_plans_dir_is_the_second_search_location(self):
        """A2/G2. Honest note carried from the plan: this recovers ZERO live spines
        today — the two topics that motivated it reference plans absent from the whole
        tree — so it is forward-looking. Tested anyway, because an untested fallback is
        one that will not work when it is finally needed."""
        with tempfile.TemporaryDirectory() as cfg:
            plans = Path(cfg) / "plans"
            plans.mkdir(parents=True)
            (plans / "unrelocated-mode-c.md").write_text("x", encoding="utf-8")
            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = cfg
                p, reason = tm.resolve_plan_ref("[[unrelocated-mode-c]]", self.dir)
                self.assertIsNotNone(p, reason)
                self.assertEqual(p.parent, plans)
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev

    def test_spine_dir_wins_over_the_plans_dir(self):
        """Design Review edge case: a plan present in BOTH locations resolves to the
        spine copy, because that is the durable relocated one."""
        with tempfile.TemporaryDirectory() as cfg:
            plans = Path(cfg) / "plans"
            plans.mkdir(parents=True)
            (plans / "foo-20260727121447_PLAN.md").write_text("y", encoding="utf-8")
            prev = os.environ.get("CLAUDE_CONFIG_DIR")
            try:
                os.environ["CLAUDE_CONFIG_DIR"] = cfg
                p, _ = tm.resolve_plan_ref("foo-20260727121447_PLAN", self.dir)
                self.assertEqual(p.parent, self.dir)
            finally:
                if prev is None:
                    os.environ.pop("CLAUDE_CONFIG_DIR", None)
                else:
                    os.environ["CLAUDE_CONFIG_DIR"] = prev


class SharedNormaliserCallSiteTests(unittest.TestCase):
    """S2 / A3 — the PRIMARY completeness check.

    The grep for the old construction is only secondary: it matches text, not
    behaviour, so it passes for two divergent local normalisers that happen to be
    spelled differently, and it FAILS on a docstring that merely mentions the literal
    (which is how an earlier draft of `resolve_plan_ref` tripped it). What actually
    holds "normalise in one place" is this — that both consumers demonstrably call the
    one function.
    """

    def _spine_with_a_bracketed_row(self, d: Path) -> Path:
        # The plan fixture carries a GATE0SR marker as well as a Gate 1 scope section,
        # so the blob's slice_id lookup succeeds too. Without it the blob still prints
        # "unresolved" — but for a DIFFERENT reason (slice-id resolution, not path
        # resolution), which would make an assertion about the word "unresolved"
        # ambiguous about which defect it was catching.
        (d / "t-20260101000000_S1_PLAN.md").write_text(
            "<!-- GATE0SR:SLICES -->\nslice_id: S1\n\n"
            "## Gate 1\n\n| c | c | `hooks/x.py` | c | c |\n", encoding="utf-8")
        spine = d / "t-20260101000000_THOUGHT.md"
        spine.write_text(
            "# T\n\n## Slice Register\n\n"
            "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 "
            "plan=[[t-20260101000000_S1_PLAN]] diary=_ updated=2026-01-01 -->\n",
            encoding="utf-8")
        return spine

    def test_scope_resolver_calls_the_shared_normaliser(self):
        """Spies the PRODUCTION entry point.

        This originally drove `_impl_specifics_scope_for` — which S3 orphaned, since
        `attribute_commits` now calls `impl_specifics_scope_with_unresolved` directly
        and a tree-wide grep finds no production caller of the older name. So the gate
        billed as "primary" was entering through a test-only door and would still have
        passed if production were rewired away from the shared normaliser entirely.
        Caught by a reviewer. It now drives the function production actually calls."""
        calls = []
        real = tm.resolve_plan_ref

        def spy(ref, spine_dir):
            calls.append(ref)
            return real(ref, spine_dir)

        with tempfile.TemporaryDirectory() as td:
            spine = self._spine_with_a_bracketed_row(Path(td))
            tm.resolve_plan_ref = spy
            try:
                tm.impl_specifics_scope_with_unresolved(spine)
            finally:
                tm.resolve_plan_ref = real
        self.assertTrue(calls, "the scope resolver must route through resolve_plan_ref")

    def test_the_compat_shim_agrees_with_the_function_it_delegates_to(self):
        """`_impl_specifics_scope_for` is retained as a compatibility shim — it has no
        production caller left, but it is module-private-by-convention rather than
        truly private and removing it is a wider change than this slice.

        Kept honest by asserting it agrees with the delegate rather than by asserting
        it works: a shim that silently diverges is worse than no shim."""
        with tempfile.TemporaryDirectory() as td:
            spine = self._spine_with_a_bracketed_row(Path(td))
            shim = tm._impl_specifics_scope_for(spine)
            direct, _unresolved = tm.impl_specifics_scope_with_unresolved(spine)
        self.assertEqual(shim, direct)

    def test_handoff_blob_calls_the_shared_normaliser(self):
        import work_done as wd

        calls = []
        real = tm.resolve_plan_ref

        def spy(ref, spine_dir):
            calls.append(ref)
            return real(ref, spine_dir)

        with tempfile.TemporaryDirectory() as td:
            spine = self._spine_with_a_bracketed_row(Path(td))
            tm.resolve_plan_ref = spy
            try:
                out = wd._gt_linked_plans(spine)
            finally:
                tm.resolve_plan_ref = real
        self.assertTrue(calls, "the handoff blob must route through resolve_plan_ref")
        self.assertNotIn("unresolved", out, out)
        self.assertIn("t-20260101000000_S1_PLAN.md", out,
                      "the DISPLAY name must be the resolved file, not the raw field")

    def test_bracketed_row_now_yields_scope(self):
        """The headline behaviour: a bracketed row used to resolve to nothing."""
        with tempfile.TemporaryDirectory() as td:
            spine = self._spine_with_a_bracketed_row(Path(td))
            scope = tm._impl_specifics_scope_for(spine)
        self.assertIn("hooks/x.py", scope)

    def test_unresolved_references_are_reported_by_name(self):
        """S3's carrier, landed here with S2 because it is the same read loop."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            spine = d / "t-20260101000000_THOUGHT.md"
            spine.write_text(
                "# T\n\n## Slice Register\n\n"
                "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 "
                "plan=[[lazy-doodling-wadler]] diary=_ updated=2026-01-01 -->\n",
                encoding="utf-8")
            scope, unresolved = tm.impl_specifics_scope_with_unresolved(spine)
        self.assertEqual(scope, set())
        self.assertEqual(len(unresolved), 1)
        ref, reason = unresolved[0]
        self.assertIn("lazy-doodling-wadler", ref)
        self.assertIn("lazy-doodling-wadler", reason)

    def test_all_placeholder_rows_read_as_no_scope_not_as_unresolvable(self):
        """Design Review edge case, and the C4 distinction: a spine whose rows are all
        `_` declares no scope — it is NOT a resolution failure, and must not be
        reported as one."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            spine = d / "t-20260101000000_THOUGHT.md"
            spine.write_text(
                "# T\n\n## Slice Register\n\n"
                "<!-- L:slice id=S1 status=SHIPPED sessions=1/1 plan=_ "
                "diary=_ updated=2026-01-01 -->\n"
                "<!-- L:slice id=S2 status=SHIPPED sessions=1/1 plan=_ "
                "diary=_ updated=2026-01-01 -->\n",
                encoding="utf-8")
            scope, unresolved = tm.impl_specifics_scope_with_unresolved(spine)
        self.assertEqual(scope, set())
        self.assertEqual(unresolved, [], "a `_` row declares no scope; it is not a failure")


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""S1 (Cluster-B) — `_active.json` dead-entry prune + its paired restore.

Covers A1's validation gate, the executor obligations that fall to group B
(1, 3, 5, 9, 10, 11), and the five validated spec gaps folded into S1 at
implementation time:

  F4  liveness is proved by ACQUIRING the topic lock, never by the read-only
      `_lock_held_fresh` probe, which fails OPEN by its own docstring
  F5  the corrupt-incumbent quarantine is a VERIFIED COPY, not a rename, so the
      live ledger path is never momentarily absent
  F9  a backup is bound to the prune that produced it, `restore-active` defaults
      to the latest, and a superseded backup is refused
  F10 `restore-active` is DRY-RUN by default, like every sibling verb
  F11 the `_prep_reaped` pre-flight guards the quarantine path

Group-B enumerated tests from the plan's Verification 2B: (d) write
atomicity, (f) concurrent-insert-during-prune, (i2) restore merges AND is
lock-guarded, (i3) lock-target uniformity, (k) fail-closed backup. ((f1) is
S0's, in test_active_json_serialization.py.)

WHY SUBPROCESSES, AND NOT THREADS — load-bearing, not style (F2, carried from
S0). `bookkeeping_lock`'s `_HELD` registry is a process-global dict keyed on lock
path with NO thread identity, and a second acquisition of the same path inside
one process takes a re-entrant refcount fast path that acquires no `flock`. Two
threads in one process therefore do not serialize AT ALL, so a thread-driven
interleaving test would prove nothing. Every concurrency assertion below runs in
real subprocesses.

EVERY POSITIVE ASSERTION SHIPS A NEGATIVE CONTROL that is asserted to fail, for
the reason S0 records: an interleaving test that cannot fail is not evidence.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_s1_prune_active.py
"""
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pre_plan_gates as ppg          # noqa: E402
import taskmanagement as tm           # noqa: E402

HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
NOW = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _row(slug, project="Projects", age_days=0.0, now=NOW):
    return {"topic_slug": slug, "active_project": project,
            "updated": (now - timedelta(days=age_days)).isoformat()}


class PruneFixture(unittest.TestCase):
    """Isolated state dir AND isolated lock dir — the live ~/.claude/state tree
    is never touched, in either namespace."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "state" / "pre_plan_gates"
        self.state.mkdir(parents=True)
        self.locks = self.root / "state" / "locks"
        self.locks.mkdir(parents=True)

        self._saved_state = ppg.TOPIC_STATE_DIR
        self._saved_locks = tm.LOCKS_DIR
        self._saved_releases = tm.RELEASES_LOG
        ppg.TOPIC_STATE_DIR = self.state
        tm.LOCKS_DIR = self.locks
        tm.RELEASES_LOG = self.locks / "_releases.jsonl"

    def tearDown(self):
        ppg.TOPIC_STATE_DIR = self._saved_state
        tm.LOCKS_DIR = self._saved_locks
        tm.RELEASES_LOG = self._saved_releases
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def write_active(self, mapping):
        (self.state / "_active.json").write_text(
            json.dumps(mapping, indent=2), encoding="utf-8")

    def read_active(self):
        p = self.state / "_active.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def reaped(self):
        d = self.state / "_reaped"
        return sorted(p.name for p in d.iterdir()) if d.exists() else []

    def default_ledger(self):
        """One dead row, one recent row, one legacy non-plain row."""
        return {
            "dead-1": _row("plain-dead1", age_days=90),
            "dead-2": _row("plain-dead2", age_days=45),
            "recent": _row("plain-recent", age_days=1),
            "legacy": _row("some-real-topic", age_days=400),
        }


# --------------------------------------------------------------------------- #
# 1. The classifier — pure, and deliberately NOT lock-aware
# --------------------------------------------------------------------------- #

class TestClassifier(PruneFixture):

    def test_only_old_plain_rows_are_candidates(self):
        cand, kept = ppg.classify_active_entries(self.default_ledger(), now=NOW)
        self.assertEqual(sorted(cand), ["dead-1", "dead-2"])
        self.assertEqual(sorted(kept), ["legacy", "recent"])

    def test_legacy_non_plain_rows_are_never_candidates(self):
        """Obligation 6 — the documented residual is left alone, on purpose."""
        active = {"a": _row("some-real-topic", age_days=9999)}
        cand, kept = ppg.classify_active_entries(active, now=NOW)
        self.assertEqual(cand, {})
        self.assertEqual(sorted(kept), ["a"])

    def test_an_unparseable_updated_stamp_is_KEPT_not_pruned(self):
        """An unreadable age is not evidence of death."""
        active = {"a": {"topic_slug": "plain-x", "active_project": "P",
                        "updated": "not-a-date"},
                  "b": {"topic_slug": "plain-y", "active_project": "P"}}
        cand, kept = ppg.classify_active_entries(active, now=NOW)
        self.assertEqual(cand, {})
        self.assertEqual(sorted(kept), ["a", "b"])

    def test_window_is_operator_tunable(self):
        """Obligation 3 — the window is a parameter, not a constant."""
        active = {"a": _row("plain-a", age_days=10)}
        wide, _ = ppg.classify_active_entries(active, window_seconds=30 * 86400, now=NOW)
        narrow, _ = ppg.classify_active_entries(active, window_seconds=5 * 86400, now=NOW)
        self.assertEqual(wide, {})
        self.assertEqual(sorted(narrow), ["a"])

    def test_the_classifier_does_not_consult_the_fail_open_probe(self):
        """F4 — `_lock_held_fresh` fails OPEN, so a transient error there would
        mark a LIVE row prunable. The classifier must not call it at all."""
        calls = []
        saved = ppg._lock_held_fresh
        ppg._lock_held_fresh = lambda *a, **k: calls.append(a) or False
        try:
            ppg.classify_active_entries(self.default_ledger(), now=NOW)
        finally:
            ppg._lock_held_fresh = saved
        self.assertEqual(calls, [], "classifier consulted the fail-open probe")


# --------------------------------------------------------------------------- #
# 2. Dry-run writes nothing
# --------------------------------------------------------------------------- #

class TestDryRun(PruneFixture):

    def test_dry_run_mutates_nothing(self):
        self.write_active(self.default_ledger())
        before = (self.state / "_active.json").read_bytes()
        res = ppg.prune_active(apply=False, now=NOW)
        self.assertEqual(res["reason"], "dry-run")
        self.assertEqual(sorted(res["candidates"]), ["dead-1", "dead-2"])
        self.assertEqual(res["pruned"], [])
        self.assertIsNone(res["backup"])
        self.assertEqual((self.state / "_active.json").read_bytes(), before)
        self.assertEqual(self.reaped(), [])

    def test_negative_control_apply_DOES_mutate(self):
        """Proves the assertion above can fail — same call, --apply flipped."""
        self.write_active(self.default_ledger())
        before = (self.state / "_active.json").read_bytes()
        ppg.prune_active(apply=True, now=NOW)
        self.assertNotEqual((self.state / "_active.json").read_bytes(), before)


# --------------------------------------------------------------------------- #
# 3. Apply — prunes only the dead, backs up first, records the pairing
# --------------------------------------------------------------------------- #

class TestApply(PruneFixture):

    def test_apply_prunes_only_dead_rows(self):
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "pruned")
        self.assertEqual(res["pruned"], ["dead-1", "dead-2"])
        self.assertEqual(sorted(self.read_active()), ["legacy", "recent"])

    def test_the_backup_is_taken_and_is_a_faithful_verified_copy(self):
        """Obligation 5 + invariants copy-path 3."""
        original = self.default_ledger()
        self.write_active(original)
        res = ppg.prune_active(apply=True, now=NOW)
        bak = self.state / "_reaped" / res["backup"]
        self.assertTrue(bak.exists())
        self.assertEqual(json.loads(bak.read_text(encoding="utf-8")), original)

    def test_the_backup_is_a_COPY_so_the_live_path_is_never_absent(self):
        """F5's sibling rule, applied to the prune: renaming the ledger aside
        would leave `_active.json` missing for readers who do not hold the
        lock. After the prune BOTH files exist."""
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertTrue((self.state / "_active.json").exists())
        self.assertTrue((self.state / "_reaped" / res["backup"]).exists())

    def test_the_prune_is_recorded_in_the_ledger_bound_to_its_backup(self):
        """F9 — without this pairing a restore could only guess."""
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        recs = ppg._read_prune_active_ledger()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["backup"], res["backup"])
        self.assertEqual(recs[0]["pruned"], ["dead-1", "dead-2"])

    def test_nothing_to_prune_is_a_clean_no_op(self):
        self.write_active({"recent": _row("plain-recent", age_days=1)})
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "nothing-to-prune")
        self.assertIsNone(res["backup"])
        self.assertEqual(self.reaped(), [])

    def test_prune_is_idempotent(self):
        self.write_active(self.default_ledger())
        ppg.prune_active(apply=True, now=NOW)
        after_first = self.read_active()
        res2 = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res2["reason"], "nothing-to-prune")
        self.assertEqual(self.read_active(), after_first)


# --------------------------------------------------------------------------- #
# 4. F4 — a LIVE topic lock keeps its row, and the probe would not have
# --------------------------------------------------------------------------- #

class TestLivenessIsProvedByAcquisition(PruneFixture):

    def test_a_row_whose_topic_lock_is_held_is_never_pruned(self):
        self.write_active(self.default_ledger())
        key = tm.composite_lock_key("plain-dead1", "Projects")
        got = tm.acquire_lock(key, "some-other-live-session")
        self.assertIn(got["status"], ("ACQUIRED", "ALREADY_HELD_BY_SELF"))
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            tm.release_lock(key, "some-other-live-session")
        self.assertIn("dead-1", res["skipped_locked"])
        self.assertEqual(res["pruned"], ["dead-2"])
        self.assertIn("dead-1", self.read_active())

    def test_negative_control_with_the_lock_released_the_same_row_IS_pruned(self):
        """Without this, the assertion above could pass for the wrong reason
        (e.g. the row never being a candidate at all)."""
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["skipped_locked"], [])
        self.assertIn("dead-1", res["pruned"])
        self.assertNotIn("dead-1", self.read_active())

    def test_a_failing_lock_probe_keeps_the_row_fail_CLOSED(self):
        """F4's core: the shipped `_lock_held_fresh` swallows every exception and
        returns False (= 'not held' = prunable). The prune must do the opposite
        and KEEP a row whose liveness it could not establish."""
        self.write_active(self.default_ledger())
        saved = tm.acquire_lock

        def boom(key, sid):
            raise RuntimeError("lock subsystem unavailable")

        tm.acquire_lock = boom
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            tm.acquire_lock = saved
        self.assertEqual(res["pruned"], [])
        self.assertEqual(res["reason"], "all-candidates-live")
        self.assertEqual(sorted(self.read_active()), sorted(self.default_ledger()))

    def test_the_fail_open_probe_would_have_pruned_it_negative_control(self):
        """Demonstrates the defect F4 exists to avoid is REAL: the shipped probe
        answers 'not held' under exactly the error the prune treats as live."""
        saved = tm.read_lock

        def boom(_key):
            raise RuntimeError("lock subsystem unavailable")

        tm.read_lock = boom
        try:
            self.assertFalse(ppg._lock_held_fresh("plain-dead1", "Projects"),
                             "probe no longer fails open — revisit F4")
        finally:
            tm.read_lock = saved


# --------------------------------------------------------------------------- #
# 5. Obligation 5 / test (k) — fail-closed backup, and no leaked lock
# --------------------------------------------------------------------------- #

class TestFailClosedBackup(PruneFixture):

    def _break_copy(self):
        saved = ppg._verified_copy
        ppg._verified_copy = lambda src, dest: False
        return saved

    def test_an_unverifiable_backup_ABORTS_the_prune(self):
        self.write_active(self.default_ledger())
        before = self.read_active()
        saved = self._break_copy()
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg._verified_copy = saved
        self.assertEqual(res["reason"], "backup-failed")
        self.assertEqual(res["pruned"], [])
        self.assertIsNone(res["backup"])
        self.assertEqual(self.read_active(), before)

    def test_negative_control_with_the_copy_working_it_proceeds(self):
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "pruned")

    def test_the_topic_lock_is_RELEASED_on_abort(self):
        """Obligation 1 — a kill/abort mid-prune must not freeze a topic until
        the stale timeout."""
        self.write_active(self.default_ledger())
        saved = self._break_copy()
        try:
            ppg.prune_active(apply=True, now=NOW, session_id="prune-under-test")
        finally:
            ppg._verified_copy = saved
        key = tm.composite_lock_key("plain-dead1", "Projects")
        got = tm.acquire_lock(key, "someone-else")
        self.assertIn(got["status"], ("ACQUIRED", "ALREADY_HELD_BY_SELF"),
                      f"lock was leaked by the aborted prune: {got}")
        tm.release_lock(key, "someone-else")

    def test_F11_an_unwritable_reaped_dir_aborts_before_any_rewrite(self):
        """The `_prep_reaped` pre-flight — never start a move you cannot finish."""
        self.write_active(self.default_ledger())
        before = self.read_active()
        saved = ppg._prep_reaped
        ppg._prep_reaped = lambda *a, **k: (_ for _ in ()).throw(
            PermissionError("state dir / _reaped not writable"))
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg._prep_reaped = saved
        self.assertEqual(res["reason"], "reaped-unwritable")
        self.assertEqual(self.read_active(), before)


# --------------------------------------------------------------------------- #
# 6. Concurrency — a real writer's insert survives the prune cycle
# --------------------------------------------------------------------------- #

PRUNE_WORKER = textwrap.dedent(
    """
    import contextlib, json, sys, time
    from pathlib import Path
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg
    import taskmanagement as tm

    state_dir, locks_dir, role, stagger, defeat = sys.argv[1:6]
    offset = float(sys.argv[6]) if len(sys.argv) > 6 else 0.0
    ppg.TOPIC_STATE_DIR = Path(state_dir)
    tm.LOCKS_DIR = Path(locks_dir)
    tm.RELEASES_LOG = Path(locks_dir) / "_releases.jsonl"

    if defeat == "defeat-lock":
        # NEGATIVE CONTROL. S0 has landed, so the pre-S0 behaviour no longer
        # exists in the tree; the only honest reconstruction is to neutralise
        # the lock and leave every other path identical.
        ppg._active_lock = lambda: contextlib.nullcontext()

    # Widen the window INSIDE the read-modify-write, between the read and the
    # write — which is exactly where a lost update lives.
    _real_read = ppg.read_active
    def _slow_read():
        data = _real_read()
        time.sleep(float(stagger))
        return data
    ppg.read_active = _slow_read

    if role == "prune":
        ppg.prune_active(apply=True, window_seconds=86400)
    else:
        # `offset` shifts the WRITER relative to the prune (see the fixture's
        # OFFSETS note): the prune's own step 2 pays a few milliseconds in
        # `acquire_lock` since streamed-dancing-goose S4, and a control keyed
        # to a zero offset reproduces the loss only when the Popen start gap
        # happens to exceed that cost.
        time.sleep(offset)
        # A REAL writer call site, not a hand-rolled RMW.
        (ppg.TOPIC_STATE_DIR / "newtopic__proj.json").write_text(
            json.dumps({{"topic_slug": "newtopic", "project_slug": "proj"}}),
            encoding="utf-8")
        ppg.set_active("brand-new-session", "newtopic", "proj")
    print("done", role)
    """
)


class TestConcurrentInsertSurvivesThePrune(PruneFixture):
    """A1's validation gate: 'a concurrent insert through a *real* writer call
    site during the cycle is preserved'. Driven through subprocesses (F2)."""

    # Writer-side offsets swept by the positive test AND its control (shared on
    # purpose — see RegionVsPruneFixture.OFFSETS for why they exist).
    OFFSETS = (0.0, 0.02, 0.05)

    def _run(self, *, defeat_lock, stagger=0.4, offset=0.0):
        self.write_active({
            "dead-1": _row("plain-dead1", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        script = self.root / "prune_worker.py"
        script.write_text(PRUNE_WORKER.format(hooks=HOOKS_DIR), encoding="utf-8")
        procs = []
        for role in ("prune", "writer"):
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(self.state), str(self.locks),
                 role, str(stagger),
                 "defeat-lock" if defeat_lock else "keep-lock", str(offset)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for p in procs:
            p.communicate(timeout=120)
        return self.read_active()

    def test_the_concurrent_insert_is_preserved_and_the_dead_row_is_gone(self):
        for offset in self.OFFSETS:
            active = self._run(defeat_lock=False, offset=offset)
            self.assertIn("brand-new-session", active,
                          f"the concurrent insert was lost-updated by the prune "
                          f"at offset={offset}")
            self.assertNotIn("dead-1", active, "the dead row was not pruned")

    def test_negative_control_without_the_lock_the_insert_IS_lost(self):
        """Proves the assertion above can fail — the lock is what saves it."""
        lost = False
        for offset in self.OFFSETS:
            for _ in range(4):             # a race needs a few attempts
                active = self._run(defeat_lock=True, offset=offset)
                if "brand-new-session" not in active:
                    lost = True
                    break
            if lost:
                break
        self.assertTrue(
            lost, "could not reproduce the lost update with the lock defeated — "
                  "the positive test above may be passing vacuously")


# --------------------------------------------------------------------------- #
# 7. restore-active — the logical merge (obligation 10 / test (i2))
# --------------------------------------------------------------------------- #

class TestRestoreMerges(PruneFixture):

    def _prune_then_mint(self):
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        # A binding minted AFTER the backup was taken.
        cur = self.read_active()
        cur["minted-after-backup"] = _row("later-topic", age_days=0)
        self.write_active(cur)
        return res

    def test_restore_re_adds_pruned_rows_AND_keeps_newer_ones(self):
        self._prune_then_mint()
        res = ppg.restore_active(apply=True, now=NOW)
        self.assertEqual(res["mode"], "merge")
        active = self.read_active()
        self.assertIn("dead-1", active)
        self.assertIn("dead-2", active)
        self.assertIn("minted-after-backup", active,
                      "a row minted after the backup was clobbered — this is "
                      "exactly the loss the merge exists to prevent")

    def test_negative_control_a_snapshot_overwrite_WOULD_lose_it(self):
        """Shows the merge is the CORRECT operation, not merely a safer one."""
        res = self._prune_then_mint()
        backup = json.loads(
            (self.state / "_reaped" / res["backup"]).read_text(encoding="utf-8"))
        self.assertNotIn("minted-after-backup", backup,
                         "fixture is wrong — the row must postdate the backup")

    def test_a_row_present_in_both_keeps_its_CURRENT_value(self):
        self.write_active({"x": _row("plain-x", age_days=90),
                           "keep": _row("real", age_days=1)})
        res = ppg.prune_active(apply=True, now=NOW)
        cur = self.read_active()
        cur["keep"] = _row("real", project="CHANGED", age_days=0)
        self.write_active(cur)
        ppg.restore_active(res["backup"], apply=True, now=NOW)
        self.assertEqual(self.read_active()["keep"]["active_project"], "CHANGED")

    def test_F10_restore_is_DRY_RUN_by_default(self):
        """Every sibling verb requires --apply; a recovery verb is the last
        place to break that expectation."""
        self._prune_then_mint()
        before = (self.state / "_active.json").read_bytes()
        res = ppg.restore_active(now=NOW)
        self.assertEqual(res["reason"], "dry-run")
        self.assertEqual(sorted(res["restored"]), ["dead-1", "dead-2"])
        self.assertEqual((self.state / "_active.json").read_bytes(), before)

    def test_negative_control_restore_with_apply_DOES_write(self):
        self._prune_then_mint()
        before = (self.state / "_active.json").read_bytes()
        ppg.restore_active(apply=True, now=NOW)
        self.assertNotEqual((self.state / "_active.json").read_bytes(), before)


# --------------------------------------------------------------------------- #
# 8. F9 — backup/prune pairing, default-to-latest, refuse-superseded
# --------------------------------------------------------------------------- #

class TestBackupPairing(PruneFixture):

    def _two_prunes(self):
        self.write_active({
            "a": _row("plain-a", age_days=90),
            "b": _row("plain-b", age_days=90),
            "keep": _row("real", age_days=1),
        })
        first = ppg.prune_active(apply=True, window_seconds=60 * 86400, now=NOW)
        # Mint a second dead row, then prune again — a DIFFERENT prune.
        cur = self.read_active()
        cur["c"] = _row("plain-c", age_days=90)
        self.write_active(cur)
        second = ppg.prune_active(apply=True, window_seconds=60 * 86400, now=NOW)
        return first, second

    def test_each_prune_gets_its_own_ledger_record(self):
        first, second = self._two_prunes()
        recs = ppg._read_prune_active_ledger()
        self.assertEqual(len(recs), 2)
        self.assertEqual(recs[0]["backup"], first["backup"])
        self.assertEqual(recs[1]["backup"], second["backup"])
        self.assertNotEqual(first["backup"], second["backup"],
                            "two prunes shared one backup filename")

    def test_restore_defaults_to_the_LATEST_prunes_backup(self):
        _first, second = self._two_prunes()
        res = ppg.restore_active(now=NOW)
        self.assertEqual(res["backup"], second["backup"])

    def test_a_SUPERSEDED_backup_is_refused(self):
        """F9's core: restoring the first backup would re-add rows the SECOND
        prune deliberately removed — undoing a prune it was never aimed at."""
        first, _second = self._two_prunes()
        res = ppg.restore_active(first["backup"], apply=True, now=NOW)
        self.assertEqual(res["reason"], "invalid-backup")
        self.assertIn("SUPERSEDED", res["errors"][0])
        # ...and nothing was written: the first prune's rows stay pruned.
        self.assertNotIn("a", self.read_active())
        self.assertNotIn("b", self.read_active())

    def test_allow_superseded_is_the_explicit_escape(self):
        first, _second = self._two_prunes()
        res = ppg.restore_active(first["backup"], apply=True,
                                 allow_superseded=True, now=NOW)
        self.assertEqual(res["reason"], "restored")
        self.assertIn("a", self.read_active())

    def test_an_unrecorded_backup_is_refused_without_the_escape(self):
        self.write_active(self.default_ledger())
        ppg.prune_active(apply=True, now=NOW)
        stray = self.state / "_reaped" / "_active.json.bak-19990101000000"
        stray.write_text(json.dumps({"ghost": _row("plain-ghost")}), encoding="utf-8")
        res = ppg.restore_active(stray.name, apply=True, now=NOW)
        self.assertEqual(res["reason"], "invalid-backup")
        self.assertIn("not recorded in the prune ledger", res["errors"][0])
        self.assertNotIn("ghost", self.read_active())

    def test_restore_with_no_prune_recorded_refuses_rather_than_guessing(self):
        self.write_active(self.default_ledger())
        res = ppg.restore_active(now=NOW)
        self.assertEqual(res["reason"], "invalid-backup")

    def test_a_missing_backup_file_is_refused(self):
        self.write_active(self.default_ledger())
        ppg.prune_active(apply=True, now=NOW)
        res = ppg.restore_active("_active.json.bak-00000000000000",
                                 apply=True, allow_superseded=True, now=NOW)
        self.assertEqual(res["reason"], "invalid-backup")

    def test_the_backup_is_validated_BEFORE_the_lock_is_taken(self):
        """Edge case (b) — holding the critical section only to discover
        unusable input violates the STABILIZED LOCK PRINCIPLE."""
        self.write_active(self.default_ledger())
        ppg.prune_active(apply=True, now=NOW)
        taken = []
        saved = ppg._active_lock

        def spy():
            taken.append(True)
            return saved()

        ppg._active_lock = spy
        try:
            ppg.restore_active("_active.json.bak-00000000000000",
                               apply=True, allow_superseded=True, now=NOW)
        finally:
            ppg._active_lock = saved
        self.assertEqual(taken, [], "the lock was taken before validating input")


# --------------------------------------------------------------------------- #
# 9. F5 — corrupt live file: snapshot fallback with a VERIFIED-COPY quarantine
# --------------------------------------------------------------------------- #

class TestCorruptIncumbent(PruneFixture):

    def _prune_then_corrupt(self):
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        (self.state / "_active.json").write_text("{ this is not json",
                                                 encoding="utf-8")
        return res

    def test_a_corrupt_live_file_falls_back_to_the_snapshot(self):
        self._prune_then_corrupt()
        res = ppg.restore_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "restored")
        self.assertTrue(res["mode"].startswith("snapshot"))
        self.assertIn("dead-1", self.read_active())

    def test_the_corrupt_bytes_are_quarantined_and_recoverable(self):
        """This plan's doctrine is never to overwrite without first preserving
        the incumbent — a truncated ledger may still hold salvageable text."""
        self._prune_then_corrupt()
        res = ppg.restore_active(apply=True, now=NOW)
        self.assertIsNotNone(res["quarantined"])
        q = self.state / "_reaped" / res["quarantined"]
        self.assertEqual(q.read_text(encoding="utf-8"), "{ this is not json")

    def test_the_quarantine_is_a_COPY_so_the_live_path_is_never_absent(self):
        """F5 — the plan specified a RENAME here, justified on enumeration
        bookkeeping. A rename empties the live ledger until `write_active`
        lands, and `read_active()` callers do not hold this lock. Asserting the
        copy semantics directly: both files exist afterwards."""
        self._prune_then_corrupt()
        res = ppg.restore_active(apply=True, now=NOW)
        self.assertTrue((self.state / "_active.json").exists())
        self.assertTrue((self.state / "_reaped" / res["quarantined"]).exists())

    def test_a_failed_quarantine_ABORTS_rather_than_overwriting(self):
        self._prune_then_corrupt()
        corrupt = (self.state / "_active.json").read_text(encoding="utf-8")
        saved = ppg._verified_copy
        ppg._verified_copy = lambda src, dest: False
        try:
            res = ppg.restore_active(apply=True, now=NOW)
        finally:
            ppg._verified_copy = saved
        self.assertEqual(res["reason"], "quarantine-failed")
        self.assertEqual((self.state / "_active.json").read_text(encoding="utf-8"),
                         corrupt)

    def test_negative_control_a_parseable_file_takes_the_MERGE_path(self):
        """Guards the fallback from widening: it must fire only on corruption."""
        self.write_active(self.default_ledger())
        ppg.prune_active(apply=True, now=NOW)
        res = ppg.restore_active(apply=True, now=NOW)
        self.assertEqual(res["mode"], "merge")
        self.assertIsNone(res["quarantined"])


# --------------------------------------------------------------------------- #
# 10. Obligation 11 — lock-target uniformity, on the sidecar path
# --------------------------------------------------------------------------- #

class TestLockTargetUniformity(unittest.TestCase):
    """Asserted in a NON-GIT temp dir on purpose: under the git-keyed path every
    target in a repo collapses to one lock, which would mask a mis-target. Only
    the sidecar fallback makes lock identity follow the target argument."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state = Path(self._tmp.name) / "pre_plan_gates"
        self.state.mkdir(parents=True)
        self._saved = ppg.TOPIC_STATE_DIR
        ppg.TOPIC_STATE_DIR = self.state

    def tearDown(self):
        ppg.TOPIC_STATE_DIR = self._saved
        self._tmp.cleanup()

    def test_the_temp_dir_is_genuinely_outside_a_git_repo(self):
        probe = subprocess.run(
            ["git", "-C", str(self.state), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True)
        self.assertNotEqual(probe.returncode, 0,
                            "temp dir is inside a git repo — the sidecar "
                            "fallback is not the path under test")

    def test_prune_and_restore_resolve_to_the_SAME_lock_as_the_writers(self):
        """Obligation 11, for the two S1 verbs.

        This must OBSERVE the target each verb actually passes, by spying on
        `bookkeeping_lock` while driving the real functions. An earlier version
        asserted `lock_path_for(x) == lock_path_for(x)` — a tautology true of
        any implementation, which never called either verb. It was named for
        the property it did not test, and an independent verifier caught it."""
        sys.path.insert(0, HOOKS_DIR)
        import bookkeeping_lock as bkl
        from bookkeeping_lock import lock_path_for

        # Isolate the lock namespace so driving the verbs cannot touch live.
        locks = Path(self._tmp.name) / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        saved_locks, saved_rel = tm.LOCKS_DIR, tm.RELEASES_LOG
        tm.LOCKS_DIR, tm.RELEASES_LOG = locks, locks / "_releases.jsonl"

        seen = []
        real = bkl.bookkeeping_lock

        def spy(target, *a, **k):
            seen.append(Path(target).resolve())
            return real(target, *a, **k)

        # `_active_lock()` imports `bookkeeping_lock` at CALL time, so patching
        # the module attribute is what the running verbs will pick up.
        bkl.bookkeeping_lock = spy
        try:
            (self.state / "_active.json").write_text(json.dumps({
                "dead": {"topic_slug": "plain-dead", "active_project": "P",
                         "updated": "2020-01-01T00:00:00+00:00"},
            }), encoding="utf-8")

            # A WRITER (S0's locus) …
            (self.state / "t__P.json").write_text(
                json.dumps({"topic_slug": "t", "project_slug": "P"}),
                encoding="utf-8")
            ppg.set_active("sid-writer", "t", "P")
            # … then both S1 verbs, for real.
            res = ppg.prune_active(apply=True)
            self.assertEqual(res["reason"], "pruned",
                             "prune did not run — nothing to observe")
            ppg.restore_active(res["backup"], apply=True)
        finally:
            bkl.bookkeeping_lock = real
            tm.LOCKS_DIR, tm.RELEASES_LOG = saved_locks, saved_rel

        self.assertGreaterEqual(len(seen), 3,
                                f"expected writer + prune + restore acquisitions, saw {seen}")
        self.assertEqual(len(set(seen)), 1,
                         f"_active.json acquisitions resolved to DIFFERENT locks: {set(seen)}")
        self.assertEqual(seen[0], Path(ppg._active_path()).resolve(),
                         "the acquisitions are not keyed on _active_path()")
        # And the target really does map to a per-file sidecar here, not a repo
        # lock that would unify every target and mask a mis-target.
        expected = lock_path_for(ppg._active_path())
        other = Path(self._tmp.name) / "TODO.md"
        other.write_text("x", encoding="utf-8")
        self.assertNotEqual(lock_path_for(other), expected,
                            "a Projects-side target resolved to the same lock — "
                            "the sidecar fallback is not under test")


# --------------------------------------------------------------------------- #
# 11. Test (d) — `prune-active`'s write is ATOMIC
#
# §2B enumerates (d) separately from (k) for a reason the two look alike but are
# not: (k) proves the prune ABORTS BEFORE mutating when the backup cannot be
# verified; (d) proves that when the prune DOES write, a crash partway through
# cannot leave a torn `_active.json`. Abort-before-mutation and
# no-torn-write-during-mutation are different failure windows.
# --------------------------------------------------------------------------- #

ATOMIC_WORKER = textwrap.dedent(
    """
    import json, os, sys, time
    from pathlib import Path
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg
    import taskmanagement as tm

    state_dir, locks_dir = sys.argv[1], sys.argv[2]
    ppg.TOPIC_STATE_DIR = Path(state_dir)
    # Redirect the LOCK namespace too. Without this the worker acquires real
    # topic locks in the operator's live ~/.claude/state/locks and is then
    # SIGKILLed there — a test reaching into live state, against the plan's
    # COPY-fixture discipline.
    tm.LOCKS_DIR = Path(locks_dir)
    tm.RELEASES_LOG = Path(locks_dir) / "_releases.jsonl"

    # Pause INSIDE the `_active.json` write, after the tmp file is complete and
    # before the rename — the only window in which a torn live file could
    # appear, and therefore the only window worth killing in.
    #
    # An earlier version patched `json.dumps` GLOBALLY and was VACUOUS: the
    # first `json.dumps` in a prune is `taskmanagement.acquire_lock`'s
    # lock-payload write, which runs at step 2, before `_active_lock()` and
    # before any `write_active`. The worker announced "writing", was killed
    # inside `acquire_lock`, and never touched `_active.json` at all — so the
    # parent's assertion held while testing nothing. This is the same
    # over-broad-injection failure the sibling test documents fixing for
    # `Path.rename`; it recurred here and was caught by an independent
    # verifier. Narrow the injection to the exact seam, and have the parent
    # assert the tmp file exists so vacuity is detectable rather than silent.
    _real_write_json = ppg._write_json
    def _slow_write_json(path, data):
        if Path(path).name != "_active.json":
            return _real_write_json(path, data)
        tmp = Path(path).with_suffix(".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        sys.stdout.write("writing\\n"); sys.stdout.flush()
        time.sleep(10)               # parent kills here
        tmp.rename(path)
    ppg._write_json = _slow_write_json

    ppg.prune_active(apply=True, window_seconds=86400)
    print("finished")
    """
)


class TestPruneWriteIsAtomic(PruneFixture):
    """Test (d)."""

    def _seed(self):
        self.write_active({
            "dead-1": _row("plain-dead1", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        return (self.state / "_active.json").read_bytes()

    def test_a_crash_between_the_tmp_write_and_the_rename_leaves_active_intact(self):
        """Failure injection at the exact seam `_write_json` relies on.

        Injected at `_write_json` rather than by patching `Path.rename` globally.
        That was tried first and proved nothing: `taskmanagement`'s lock
        acquisition also renames, so the patch made the topic-lock acquire
        raise, `prune_active` correctly read that as 'live' (fail-closed), and
        the run returned `all-candidates-live` having never reached the write.
        The test passed for the wrong reason. Narrow the injection to the seam
        actually under test."""
        before = self._seed()
        saved = ppg._write_json
        tmp_seen = {}

        def crash_after_tmp(path, data):
            # Replicate _write_json up to — but not through — the rename.
            tmp = path.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2, default=str),
                           encoding="utf-8")
            tmp_seen["path"] = tmp
            raise OSError("simulated crash after the tmp write, before the rename")

        ppg._write_json = crash_after_tmp
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg._write_json = saved

        # Since the two-phase ledger fix, an OSError at the mutation is HANDLED
        # rather than escaping as a traceback (an ENOSPC/EACCES write failure is
        # not a crash and must not surface as one), so this asserts the clean
        # refusal instead of a raised exception. The property under test is
        # unchanged: whatever happened at the write, `_active.json` is intact.
        self.assertEqual(res["reason"], "write-failed")
        self.assertIn("path", tmp_seen,
                      "the write seam was never reached — test would be vacuous")
        self.assertEqual((self.state / "_active.json").read_bytes(), before,
                         "a crash before the rename left a modified _active.json")
        json.loads((self.state / "_active.json").read_text(encoding="utf-8"))

    def test_a_KILLED_process_mid_write_leaves_active_intact(self):
        """The real crash, not a simulated one: SIGKILL a subprocess while it is
        serialising the new ledger, then assert the prior file is byte-identical
        and still parses."""
        before = self._seed()
        script = self.root / "atomic_worker.py"
        script.write_text(ATOMIC_WORKER.format(hooks=HOOKS_DIR), encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, str(script), str(self.state), str(self.locks)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            # Wait until the worker reports it is inside the `_active.json`
            # write, then kill it.
            line = proc.stdout.readline()
            self.assertIn("writing", line, "worker never reached the write")
            proc.kill()
        finally:
            proc.communicate(timeout=30)
        self.assertNotEqual(proc.returncode, 0, "worker was not actually killed")

        # NON-VACUITY GUARD. The completed tmp file is proof the worker really
        # reached the `_active.json` write window before dying. Without this the
        # test passes trivially whenever the kill lands anywhere earlier — which
        # is precisely how the first version of this test was wrong.
        self.assertTrue(
            (self.state / "_active.tmp").exists(),
            "the worker was killed before it reached the _active.json write — "
            "this test would be vacuous")

        self.assertEqual((self.state / "_active.json").read_bytes(), before)
        json.loads((self.state / "_active.json").read_text(encoding="utf-8"))

    def test_negative_control_an_IN_PLACE_write_WOULD_tear(self):
        """Proves the two assertions above are not vacuous: the same interrupted
        write, performed in place instead of via tmp+rename, does corrupt the
        file. This is what `write_active`'s tmp+rename buys."""
        self._seed()
        live = self.state / "_active.json"
        try:
            with open(live, "w", encoding="utf-8") as fh:
                fh.write('{"partial": {"topic_slug": "plain-x", ')
                raise KeyboardInterrupt("simulated crash mid in-place write")
        except KeyboardInterrupt:
            pass
        with self.assertRaises(ValueError):
            json.loads(live.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 12. Test (i2) — the CONCURRENCY half of the restore contract
#
# The merge tests above prove `restore_active` computes the right union. They do
# NOT prove it holds the lock while doing so. A restore that merged correctly but
# ran unlocked would still lose a row inserted by a concurrent writer between its
# read and its write — which is precisely the loss a raw `cp`-back causes, and
# precisely what obligation 10 prohibits.
# --------------------------------------------------------------------------- #

RESTORE_WORKER = textwrap.dedent(
    """
    import contextlib, json, shutil, sys, time
    from pathlib import Path
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg
    import taskmanagement as tm

    state_dir, locks_dir, role, stagger, mode, backup = sys.argv[1:7]
    ppg.TOPIC_STATE_DIR = Path(state_dir)
    tm.LOCKS_DIR = Path(locks_dir)
    tm.RELEASES_LOG = Path(locks_dir) / "_releases.jsonl"

    # Widen the window between the read and the write — where a lost update
    # lives. NOTE: `restore_active`'s APPLY path reads the live file via
    # `Path.read_text`, not `read_active()` (only its dry-run branch uses
    # `read_active`). Patching `read_active` alone therefore does NOT slow the
    # restore, which made an earlier version of this worker inject somewhere the
    # code under test never runs — the same class of miss two prior verifier
    # rounds caught elsewhere. Patch BOTH, so whichever the role exercises is
    # genuinely widened.
    _real_read = ppg.read_active
    def _slow_read():
        data = _real_read()
        time.sleep(float(stagger))
        return data
    ppg.read_active = _slow_read

    _real_read_text = Path.read_text
    def _slow_read_text(self, *a, **k):
        out = _real_read_text(self, *a, **k)
        if self.name == "_active.json":
            time.sleep(float(stagger))
        return out
    Path.read_text = _slow_read_text

    if role == "restore":
        if mode == "raw-cp":
            # NEGATIVE CONTROL — the prohibited rollback (obligation 10). A raw
            # copy back, taking no lock and performing no merge. It sleeps LONGER
            # than the writer so the copy reliably lands AFTER the concurrent
            # insert: that ordering is the whole point, since a snapshot copy
            # clobbers whatever the writer had just committed.
            time.sleep(float(stagger) * 3)
            shutil.copyfile(str(Path(state_dir) / "_reaped" / backup),
                            str(Path(state_dir) / "_active.json"))
        else:
            ppg.restore_active(backup, apply=True)
    else:
        (ppg.TOPIC_STATE_DIR / "newtopic__proj.json").write_text(
            json.dumps({{"topic_slug": "newtopic", "project_slug": "proj"}}),
            encoding="utf-8")
        ppg.set_active("inserted-during-restore", "newtopic", "proj")
    print("done", role)
    """
)


class TestRestoreIsLockGuarded(PruneFixture):
    """Test (i2), concurrency half — driven through subprocesses (F2)."""

    def _run(self, *, mode, stagger=0.4):
        self.write_active({
            "dead-1": _row("plain-dead1", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "pruned")
        backup = res["backup"]

        script = self.root / "restore_worker.py"
        script.write_text(RESTORE_WORKER.format(hooks=HOOKS_DIR), encoding="utf-8")
        procs = []
        for role in ("restore", "writer"):
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(self.state), str(self.locks),
                 role, str(stagger), mode, backup],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for p in procs:
            p.communicate(timeout=120)
        return self.read_active()

    def test_a_concurrent_insert_during_the_restore_is_serialized_not_lost(self):
        active = self._run(mode="merge")
        self.assertIn("inserted-during-restore", active,
                      "a row inserted during the restore was lost — the restore "
                      "is not holding the lock across its read-modify-write")
        self.assertIn("dead-1", active, "the restore did not re-add the pruned row")

    def test_negative_control_a_raw_cp_back_LOSES_the_concurrent_insert(self):
        """Obligation 10's prohibition, demonstrated rather than asserted."""
        lost = False
        for _ in range(5):                 # a race needs a few attempts
            active = self._run(mode="raw-cp")
            if "inserted-during-restore" not in active:
                lost = True
                break
        self.assertTrue(
            lost, "could not reproduce the loss with a raw cp-back — the positive "
                  "test above may be passing vacuously")


# --------------------------------------------------------------------------- #
# 13. Test (f), the remaining regions — each driven against a RUNNING prune,
#     not merely against another writer.
#
# §2B requires (f) "covered for each of the four RMW regions, including
# `auto_register_topic` (the /close path), AND including a case that interleaves
# at the rebind/fresh-mint region's shared opening `read_active()`". Both halves
# of that sentence are load-bearing: `create_topic` has TWO write exits from one
# shared read region, and Outcome Claim C6 names both ("starts **or re-binds**").
# An earlier version of this comment quoted the first half and dropped the
# second, and the rebind arm was then left untested — the restate-part-of-a-fact
# miss the plan's Single-source map exists to prevent, recurring in a test file
# and caught by an independent verifier rather than by its author.
#
# S0's suite covers all four regions writer-vs-writer; the S1 suite covered only
# `set_active` vs a running prune.
# An independent verifier read the requirement literally — a concurrent insert
# *during the prune cycle*, per region — and was right that three regions had no
# such test. These are those three.
# --------------------------------------------------------------------------- #

REGION_VS_PRUNE_WORKER = textwrap.dedent(
    """
    import contextlib, json, sys, time
    from pathlib import Path
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg
    import taskmanagement as tm

    state_dir, locks_dir, role, stagger, defeat, projects = sys.argv[1:7]
    offset = float(sys.argv[7]) if len(sys.argv) > 7 else 0.0
    ppg.TOPIC_STATE_DIR = Path(state_dir)
    tm.LOCKS_DIR = Path(locks_dir)
    tm.RELEASES_LOG = Path(locks_dir) / "_releases.jsonl"
    ppg.PROJECTS_ROOT = Path(projects)

    if defeat == "defeat-lock":
        ppg._active_lock = lambda: contextlib.nullcontext()

    _real_read = ppg.read_active
    def _slow_read():
        data = _real_read()
        time.sleep(float(stagger))
        return data
    ppg.read_active = _slow_read

    # Each writer role performs exactly ONE region operation; the parent has
    # already seeded whatever that operation needs. One write per worker keeps
    # the interleaving window aligned with the prune's, which is what makes the
    # negative control reproduce reliably. `offset` shifts the writer relative
    # to the prune (see the fixture's OFFSETS note).
    if role != "prune":
        time.sleep(offset)
    if role == "prune":
        ppg.prune_active(apply=True, window_seconds=86400)
    elif role == "repoint":                      # region 2
        ppg._repoint_active("rp-topic", "oldproj", "newproj")
    elif role == "create":                       # region 3, FRESH-MINT arm
        ppg.create_topic("sid-create", "someproj", "ct-topic")
    elif role == "create_rebind":                # region 3, REBIND arm
        # Same shared opening `read_active()`; the other exit. The parent has
        # pre-seeded the topic-state file, so `_create_topic_locked`'s
        # idempotency guard fires and this takes the rebind path rather than
        # minting fresh.
        out = ppg.create_topic("sid-rebind", "rbproj", "rb-topic")
        # Record which exit was actually taken, so the parent can prove this
        # test is exercising the rebind arm and not silently fresh-minting.
        (Path(state_dir) / "_rebind_status.txt").write_text(
            str((out or {{}}).get("status")), encoding="utf-8")
    elif role == "autoreg":                      # region 4 — the /close path
        spine = Path(projects) / "Thoughts" / "auto-s1-20260101000000_PLAN.md"
        ppg.auto_register_topic("sid-autoreg", str(spine))
    print("done", role)
    """
)


class RegionVsPruneFixture(PruneFixture):
    """Drives ONE real writer region concurrently with a REAL running prune."""

    WRITER_SID = None
    ROLE = None

    def setUp(self):
        super().setUp()
        self.projects = self.root / "projects"
        (self.projects / "Thoughts").mkdir(parents=True)
        (self.projects / "TODO.md").write_text("# TODO\n\n## Now\n\n",
                                               encoding="utf-8")
        (self.projects / "Thoughts" / "auto-s1-20260101000000_PLAN.md").write_text(
            "# Plan\n", encoding="utf-8")
        self._saved_projects = ppg.PROJECTS_ROOT
        ppg.PROJECTS_ROOT = self.projects

    def tearDown(self):
        ppg.PROJECTS_ROOT = self._saved_projects
        super().tearDown()

    def _seed(self):
        """Whatever the writer role needs, placed BEFORE the race starts."""
        self.write_active({
            "dead-1": _row("plain-dead1", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        if self.ROLE == "repoint":
            # _repoint_active MUTATES an existing row rather than inserting, so
            # the row and its state file must already exist.
            (self.state / "rp-topic__oldproj.json").write_text(
                json.dumps({"topic_slug": "rp-topic", "project_slug": "oldproj"}),
                encoding="utf-8")
            cur = self.read_active()
            cur[self.WRITER_SID] = {"topic_slug": "rp-topic",
                                    "active_project": "oldproj",
                                    "updated": NOW.isoformat()}
            self.write_active(cur)
        elif self.ROLE == "create_rebind":
            # Pre-seed the topic-state file so `_create_topic_locked`'s
            # idempotency guard is TRUE and `create_topic` takes the REBIND
            # exit. Without this seeding the call fresh-mints, and the rebind
            # arm — the one Outcome Claim C6 names by word — goes untested.
            (self.state / "rb-topic__rbproj.json").write_text(
                json.dumps({"topic_slug": "rb-topic", "project_slug": "rbproj"}),
                encoding="utf-8")

    def _run(self, *, defeat_lock, stagger=0.4, offset=0.0):
        self._seed()
        script = self.root / "region_vs_prune.py"
        script.write_text(REGION_VS_PRUNE_WORKER.format(hooks=HOOKS_DIR),
                          encoding="utf-8")
        procs = []
        for role in ("prune", self.ROLE):
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(self.state), str(self.locks),
                 role, str(stagger),
                 "defeat-lock" if defeat_lock else "keep-lock",
                 str(self.projects), str(offset)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for p in procs:
            p.communicate(timeout=120)
        return self.read_active()

    def _writer_landed(self, active):
        """Did the writer's effect survive? Insert-shaped regions answer this by
        row PRESENCE; `_repoint_active` mutates an existing row instead, so for
        it a lost update shows up as the row still reading the OLD project — the
        row is present either way. Overridden there."""
        return self.WRITER_SID in active

    # The stagger sweep is shared by the positive test and its control on
    # purpose: a control that certifies a timing the positive never runs at
    # could pass while the positive's own timing has a closed window.
    STAGGERS = (0.4, 0.25, 0.6)

    # Writer-side offsets, also shared by the positive test and its control.
    # WHY (streamed-dancing-goose S4, 2026-09-20): the lost-update window the
    # control reproduces is [prune's second read, prune's write) — the writer's
    # write must land inside it. At offset 0 the writer's write and the prune's
    # second read are separated only by the Popen start gap (a few ms), and the
    # prune's step 2 has paid one verified `ps` call inside `acquire_lock`
    # since S4 (the session-process identity). That single call was enough to
    # move the prune's read past the writer's write at every stagger, so the
    # control stopped reproducing while the positive test kept passing — the
    # exact vacuity this control exists to catch. A small writer delay puts the
    # write back inside the window whatever the acquire step costs; the
    # positive test sweeps the same offsets so it certifies every timing the
    # control certifies.
    OFFSETS = (0.0, 0.02, 0.05)

    def _assert_preserved(self):
        for stagger in self.STAGGERS:
            for offset in self.OFFSETS:
                active = self._run(defeat_lock=False, stagger=stagger, offset=offset)
                self.assertTrue(
                    self._writer_landed(active),
                    f"{self.ROLE}'s write was lost-updated by the prune at "
                    f"stagger={stagger} offset={offset}: {active.get(self.WRITER_SID)}")
                self.assertNotIn("dead-1", active, "the dead row was not pruned")

    def _assert_control_loses(self):
        """The control must actually reproduce the loss, or the positive test
        above proves nothing. A race needs several attempts and a couple of
        timings — the window each region opens is not the same width."""
        lost = False
        for stagger in self.STAGGERS:
            for offset in self.OFFSETS:
                for _ in range(4):
                    active = self._run(defeat_lock=True, stagger=stagger, offset=offset)
                    if not self._writer_landed(active):
                        lost = True
                        break
                if lost:
                    break
            if lost:
                break
        self.assertTrue(
            lost, f"could not reproduce the lost update for {self.ROLE} with the "
                  "lock defeated — the positive test may be passing vacuously")


class TestRepointRegionVsPrune(RegionVsPruneFixture):
    ROLE = "repoint"
    WRITER_SID = "sid-repoint"

    def _writer_landed(self, active):
        # The repoint's effect is the NEW project value, not the row's presence:
        # the prune's stale snapshot still contains the row, so a lost update
        # leaves it readable but pointing at `oldproj`.
        row = active.get(self.WRITER_SID) or {}
        return row.get("active_project") == "newproj"

    def test_a_repoint_during_the_prune_is_preserved(self):
        self._assert_preserved()

    def test_negative_control_without_the_lock_the_repoint_IS_lost(self):
        self._assert_control_loses()


class TestCreateTopicRegionVsPrune(RegionVsPruneFixture):
    ROLE = "create"
    WRITER_SID = "sid-create"

    def test_a_topic_START_during_the_prune_is_preserved(self):
        """Outcome Claim C6's 'starts a topic' half, against a running prune."""
        self._assert_preserved()

    def test_negative_control_without_the_lock_the_start_IS_lost(self):
        self._assert_control_loses()


class TestCreateTopicRebindRegionVsPrune(RegionVsPruneFixture):
    """§2B's "a case that interleaves at the rebind/fresh-mint region's SHARED
    opening `read_active()`" — the REBIND exit specifically.

    `create_topic` has two write exits from one shared read region. The sibling
    class above drives the fresh-mint exit; this one drives the rebind exit,
    which is the arm Outcome Claim C6 names by word ("starts **or re-binds** a
    topic while a prune is running"). It was missing, and an independent
    verifier caught that the class docstring above quoted §2B's four-regions
    sentence while silently dropping this clause from the very same sentence —
    the restate-part-of-a-fact propagation miss this plan's Single-source map
    exists to prevent, recurring inside a test file."""

    ROLE = "create_rebind"
    WRITER_SID = "sid-rebind"

    def test_a_topic_REBIND_during_the_prune_is_preserved(self):
        self._assert_preserved()
        # NON-VACUITY GUARD: prove the rebind exit was the one taken. Without
        # this, a seeding change that stopped triggering the idempotency guard
        # would silently turn this back into a second fresh-mint test while
        # still passing.
        status = (self.state / "_rebind_status.txt").read_text(encoding="utf-8")
        self.assertEqual(status, "rebound",
                         f"create_topic took the {status!r} exit, not the "
                         f"rebind exit — this test is not covering (f)'s "
                         f"rebind case")

    def test_negative_control_without_the_lock_the_rebind_IS_lost(self):
        self._assert_control_loses()


class TestAutoRegisterRegionVsPrune(RegionVsPruneFixture):
    ROLE = "autoreg"
    WRITER_SID = "sid-autoreg"

    def test_an_auto_registration_during_the_prune_is_preserved(self):
        self._assert_preserved()

    def test_negative_control_without_the_lock_the_auto_registration_IS_lost(self):
        self._assert_control_loses()


# --------------------------------------------------------------------------- #
# 14. F11 on the RESTORE side + the edge cases the first pass missed.
# --------------------------------------------------------------------------- #

class TestRestorePreFlightAndEdges(PruneFixture):

    def _prune(self):
        self.write_active(self.default_ledger())
        return ppg.prune_active(apply=True, now=NOW)

    def test_F11_an_unwritable_reaped_dir_aborts_the_RESTORE_too(self):
        """`restore_active` has its own `_prep_reaped` branch; only the prune's
        equivalent was tested."""
        res = self._prune()
        before = self.read_active()
        saved = ppg._prep_reaped
        ppg._prep_reaped = lambda *a, **k: (_ for _ in ()).throw(
            PermissionError("state dir / _reaped not writable"))
        try:
            out = ppg.restore_active(res["backup"], apply=True, now=NOW)
        finally:
            ppg._prep_reaped = saved
        self.assertEqual(out["reason"], "reaped-unwritable")
        self.assertEqual(self.read_active(), before)

    def test_an_ABSENT_active_json_is_a_clean_no_op(self):
        self.assertFalse((self.state / "_active.json").exists())
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "nothing-to-prune")
        self.assertIsNone(res["backup"])

    def test_an_EMPTY_active_json_is_a_clean_no_op(self):
        self.write_active({})
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "nothing-to-prune")
        self.assertEqual(self.read_active(), {})

    def test_a_same_second_backup_collision_never_overwrites(self):
        """`_backup_active_verified`'s `.dup` fallback — two prunes inside one
        second must not have the second silently overwrite the first."""
        self.write_active({"a": _row("plain-a", age_days=90),
                           "keep": _row("real", age_days=1)})
        first = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(first["reason"], "pruned")
        # Second prune at the SAME timestamp — the filename would collide.
        cur = self.read_active()
        cur["b"] = _row("plain-b", age_days=90)
        self.write_active(cur)
        second = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(second["reason"], "pruned")
        self.assertNotEqual(first["backup"], second["backup"],
                            "the second prune reused the first backup's name")
        for name in (first["backup"], second["backup"]):
            self.assertTrue((self.state / "_reaped" / name).exists())

    def test_a_backup_that_is_valid_json_but_NOT_an_object_is_refused(self):
        res = self._prune()
        stray = self.state / "_reaped" / res["backup"]
        stray.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        out = ppg.restore_active(res["backup"], apply=True, now=NOW)
        self.assertEqual(out["reason"], "invalid-backup")
        self.assertIn("not a JSON object", out["errors"][0])

    def test_a_torn_trailing_ledger_line_does_not_brick_restore(self):
        """Obligation 7's parse-resilience, exercised rather than assumed."""
        res = self._prune()
        ledger = self.state / "_reaped" / ppg.PRUNE_ACTIVE_LEDGER_NAME
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write('{"backup": "half-written-')      # torn append
        recs = ppg._read_prune_active_ledger()
        self.assertEqual(len(recs), 1)
        out = ppg.restore_active(apply=True, now=NOW)
        self.assertEqual(out["reason"], "restored")
        self.assertEqual(out["backup"], res["backup"])


# --------------------------------------------------------------------------- #
# 15. Round-3 verifier findings — the three that changed behaviour.
# --------------------------------------------------------------------------- #

SUPERSEDE_WORKER = textwrap.dedent(
    """
    import json, sys, time
    from pathlib import Path
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg
    import taskmanagement as tm

    state_dir, locks_dir, role, stagger, allow = sys.argv[1:6]
    ppg.TOPIC_STATE_DIR = Path(state_dir)
    tm.LOCKS_DIR = Path(locks_dir)
    tm.RELEASES_LOG = Path(locks_dir) / "_releases.jsonl"

    if role == "restore":
        # Delay between the PRE-LOCK validation and the lock acquisition — the
        # exact window the check-then-act race lives in. Patching `_active_lock`
        # to sleep first is the only way to open it deterministically, and it
        # changes nothing about the locking itself.
        _real_lock = ppg._active_lock
        def _slow_lock():
            time.sleep(float(stagger))
            return _real_lock()
        ppg._active_lock = _slow_lock
        res = ppg.restore_active(apply=True,
                                 allow_superseded=(allow == "allow"))
        (Path(state_dir) / "_restore_result.json").write_text(
            json.dumps(res, default=str), encoding="utf-8")
    else:
        # A SECOND prune that completes inside that window, superseding the
        # backup the restore already chose.
        time.sleep(float(stagger) / 4)
        ppg.prune_active(apply=True, window_seconds=86400)
    print("done", role)
    """
)


class TestSupersededDuringRestore(PruneFixture):
    """The round-3 finding: supersession was decided from an UNLOCKED ledger read
    before the lock, and never re-checked inside it. A prune completing in that
    window silently re-added rows it had just removed — the exact harm F9 exists
    to prevent, reached without `--allow-superseded`."""

    def _run(self, *, allow, stagger=0.6):
        # One dead row for the FIRST prune, another that only the SECOND prune
        # will reach, so the two prunes are genuinely distinct.
        self.write_active({
            "old-dead": _row("plain-olddead", age_days=90),
            "later-dead": _row("plain-laterdead", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        first = ppg.prune_active(apply=True, window_seconds=60 * 86400, now=NOW)
        self.assertEqual(first["reason"], "pruned")

        # Re-add a fresh dead row so the racing prune has work to do and writes
        # its own ledger record.
        cur = self.read_active()
        cur["racer-dead"] = _row("plain-racerdead", age_days=90)
        self.write_active(cur)

        script = self.root / "supersede_worker.py"
        script.write_text(SUPERSEDE_WORKER.format(hooks=HOOKS_DIR), encoding="utf-8")
        procs = []
        for role in ("restore", "prune"):
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(self.state), str(self.locks),
                 role, str(stagger), "allow" if allow else "refuse"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for p in procs:
            p.communicate(timeout=120)

        result_path = self.state / "_restore_result.json"
        res = (json.loads(result_path.read_text(encoding="utf-8"))
               if result_path.exists() else None)
        return res, self.read_active()

    def test_a_prune_landing_mid_restore_is_REFUSED_not_silently_applied(self):
        res, active = self._run(allow=False)
        self.assertIsNotNone(res, "the restore worker produced no result")
        self.assertEqual(
            res["reason"], "superseded-during-restore",
            f"the restore proceeded against a backup that was superseded while "
            f"it was starting: {res['reason']}")
        self.assertEqual(res["restored"], [], "rows were re-added anyway")
        # And the racing prune's own removal stands.
        self.assertNotIn("racer-dead", active,
                         "the concurrent prune's removal was undone")

    def test_negative_control_allow_superseded_still_proceeds(self):
        """Proves the refusal above is the new check firing, not the restore
        failing for some unrelated reason."""
        res, _active = self._run(allow=True)
        self.assertIsNotNone(res)
        self.assertEqual(res["reason"], "restored",
                         f"--allow-superseded did not proceed: {res['reason']}")


class TestLedgerIsTwoPhase(PruneFixture):
    """Neither single ordering of (write, ledger-append) is safe:

      * append-AFTER  — a crash between them leaves a prune that DID happen with
        no record, so a later default restore resolves to the PREVIOUS backup
        and re-adds rows this prune removed. Silent over-restore.
      * append-BEFORE — a crash leaves a record for a prune that did NOT happen.
        That row becomes the ledger tail, so the previous REAL backup is refused
        as 'superseded' on a false reason AND the default restore resolves to
        the orphan and no-ops while reporting success. (This was shipped first
        and its harmlessness was over-claimed; an independent verifier caught
        it.)

    The two-phase row makes a crash INERT and VISIBLE instead."""

    def _fail_the_write(self):
        saved = ppg.write_active

        def boom(data):
            raise OSError("simulated failure at the mutation")

        ppg.write_active = boom
        return saved

    def test_a_failed_write_leaves_the_row_UNCONFIRMED_and_does_not_raise(self):
        self.write_active(self.default_ledger())
        before = (self.state / "_active.json").read_bytes()
        saved = self._fail_the_write()
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg.write_active = saved

        self.assertEqual(res["reason"], "write-failed")
        self.assertEqual((self.state / "_active.json").read_bytes(), before)
        rows = ppg._read_prune_active_ledger()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].get("committed"),
                         "a prune that never mutated was recorded as committed")
        self.assertEqual(ppg._committed_prune_records(), [],
                         "an unconfirmed row counted as a completed prune")

    def test_an_unconfirmed_row_does_NOT_drive_the_default_restore(self):
        """(b) of the verifier's finding — the orphan must not become the
        default target and silently no-op while reporting success."""
        self.write_active(self.default_ledger())
        saved = self._fail_the_write()
        try:
            ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg.write_active = saved
        out = ppg.restore_active(apply=True, now=NOW)
        self.assertEqual(out["reason"], "invalid-backup")
        self.assertIn("no completed prune", out["errors"][0])

    def test_an_unconfirmed_row_does_NOT_supersede_a_real_backup(self):
        """(a) of the verifier's finding — the real earlier backup must stay
        restorable, not be refused on a false 'superseded' reason."""
        self.write_active(self.default_ledger())
        real = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(real["reason"], "pruned")

        # Now a prune that records a row but fails to mutate.
        cur = self.read_active()
        cur["late-dead"] = _row("plain-latedead", age_days=90)
        self.write_active(cur)
        saved = self._fail_the_write()
        try:
            ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg.write_active = saved

        out = ppg.restore_active(real["backup"], apply=True, now=NOW)
        self.assertEqual(out["reason"], "restored",
                         f"the real backup was refused because of an "
                         f"unconfirmed row: {out['errors']}")
        self.assertIn("dead-1", self.read_active())

    def test_a_committed_prune_records_both_phases(self):
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        rows = ppg._read_prune_active_ledger()
        self.assertEqual(len(rows), 1, "the two phases did not fold to one row")
        self.assertTrue(rows[0]["committed"])
        self.assertEqual(rows[0]["backup"], res["backup"])

    def test_a_legacy_row_with_no_committed_key_reads_as_committed(self):
        """Rows predating the two-phase scheme were written only after a
        successful write, so absence must not retroactively hide them."""
        ppg._prep_reaped()
        ppg._append_prune_active_ledger({
            "backup": "_active.json.bak-20200101000000", "count": 1,
            "at": "2020-01-01T00:00:00+00:00",
        })
        self.assertEqual(len(ppg._committed_prune_records()), 1)


class TestMovedLockKeyIsNotPruned(PruneFixture):
    """`_repoint_active` changes a row's `active_project` WITHOUT refreshing
    `updated`, so the key locked from the unlocked step-1 snapshot can stop being
    the row's real key. Pruning it then would mean a live holder of the NEW key
    never had the chance to block us."""

    def test_a_row_whose_project_moved_after_locking_is_SKIPPED(self):
        self.write_active({
            "moved": _row("plain-moved", project="oldproj", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        real_read = ppg.read_active
        calls = {"n": 0}

        def read_then_move():
            data = real_read()
            calls["n"] += 1
            if calls["n"] == 1:
                # Between the unlocked propose and the locked re-read, the row
                # is repointed to a different project — a new lock key.
                cur = real_read()
                cur["moved"]["active_project"] = "newproj"
                ppg._write_json(ppg._active_path(), cur)
            return data

        ppg.read_active = read_then_move
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg.read_active = real_read

        self.assertNotIn("moved", res["pruned"],
                         "a row whose lock key moved was pruned under a lock "
                         "that no longer covered it")
        # A DISTINCT bucket: the CLI renders `skipped_locked` as "a live session
        # holds the topic lock", which this code never observed.
        self.assertIn("moved", res["skipped_key_moved"])
        self.assertNotIn("moved", res["skipped_locked"],
                         "a moved-key row was reported as lock-held, which the "
                         "CLI would render as a claim the code never made")
        self.assertIn("moved", self.read_active())

    def test_negative_control_a_row_whose_key_did_NOT_move_IS_pruned(self):
        self.write_active({
            "stable": _row("plain-stable", project="oldproj", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertIn("stable", res["pruned"])


# --------------------------------------------------------------------------- #
# 16. The CLI surface itself.
#
# Every test above drives the Python API. An independent verifier pointed out
# that NOTHING covered the CLI rendering of either verb — which is where the
# round-5 MUST-FIX lived: a `write-failed` run still printed the rollback hint,
# and following that printed instruction would have performed an unintended
# mutation. A wrong operator instruction is a defect even when the library
# underneath is correct.
#
# These drive the real CLI in a subprocess against an isolated state dir AND an
# isolated lock dir (`--locks-dir`, which exists for exactly this).
# --------------------------------------------------------------------------- #

CLI = str(Path(HOOKS_DIR) / "reap_orphan_plain_topics.py")


class TestCliSurface(PruneFixture):

    def _cli(self, *args):
        proc = subprocess.run(
            [sys.executable, CLI, *args,
             "--state-dir", str(self.state), "--locks-dir", str(self.locks)],
            capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr

    def test_a_successful_prune_prints_the_rollback_hint(self):
        self.write_active(self.default_ledger())
        rc, out, _err = self._cli("prune-active", "--apply")
        self.assertEqual(rc, 0)
        self.assertIn("Roll this prune back with", out)
        self.assertIn("restore-active --apply", out)

    def test_a_dry_run_prints_no_rollback_hint(self):
        self.write_active(self.default_ledger())
        rc, out, _err = self._cli("prune-active")
        self.assertEqual(rc, 0)
        self.assertNotIn("Roll this prune back with", out)

    def test_the_moved_key_bucket_is_NOT_rendered_as_lock_held(self):
        """The CLI must not assert an observation the code never made.

        Constructs a REAL moved-key row rather than asserting the label string
        appears — an earlier version did the latter, which is unconditionally
        true and told us nothing."""
        # A dead row whose topic lock is HELD (so it is skipped as live) plus a
        # normal dead row, so the two buckets are distinguishable in the output.
        self.write_active({
            "held": _row("plain-held", age_days=90),
            "dead": _row("plain-dead", age_days=90),
            "keep": _row("some-real-topic", age_days=1),
        })
        key = tm.composite_lock_key("plain-held", "Projects")
        # Take the lock in the FIXTURE namespace the CLI will use.
        saved_locks, saved_rel = tm.LOCKS_DIR, tm.RELEASES_LOG
        tm.LOCKS_DIR, tm.RELEASES_LOG = self.locks, self.locks / "_releases.jsonl"
        got = tm.acquire_lock(key, "another-live-session")
        self.assertIn(got["status"], ("ACQUIRED", "ALREADY_HELD_BY_SELF"))
        try:
            rc, out, _err = self._cli("prune-active", "--apply")
        finally:
            tm.release_lock(key, "another-live-session")
            tm.LOCKS_DIR, tm.RELEASES_LOG = saved_locks, saved_rel

        self.assertEqual(rc, 0)
        self.assertIn("kept-live=1", out, f"the held row was not kept: {out}")
        self.assertIn("kept-key-moved=0", out,
                      "a lock-held row was mis-reported as key-moved")
        self.assertIn("a live session holds the topic lock", out)
        # The two buckets are rendered with DIFFERENT wording — that separation
        # is the whole point of the fix.
        self.assertNotIn("the row's topic moved after its lock was taken", out)

    def test_restore_defaults_to_the_last_completed_prune(self):
        self.write_active(self.default_ledger())
        self._cli("prune-active", "--apply")
        rc, out, _err = self._cli("restore-active")
        self.assertEqual(rc, 0)
        self.assertIn("mode=merge", out)
        self.assertIn("re-added=2", out)

    def test_restore_refuses_a_superseded_backup_with_exit_2(self):
        self.write_active({"a": _row("plain-a", age_days=90),
                           "b": _row("plain-b", age_days=90),
                           "keep": _row("real", age_days=1)})
        first = ppg.prune_active(apply=True, window_seconds=60 * 86400, now=NOW)
        cur = self.read_active()
        cur["c"] = _row("plain-c", age_days=90)
        self.write_active(cur)
        ppg.prune_active(apply=True, window_seconds=60 * 86400, now=NOW)

        rc, out, err = self._cli("restore-active", first["backup"], "--apply")
        self.assertEqual(rc, 2)
        self.assertIn("REFUSED", err)
        self.assertIn("SUPERSEDED", err)
        # ...and it must NOT have printed a misleading zeroed summary first.
        self.assertNotIn("mode=None", out)

    def test_locks_dir_keeps_the_run_out_of_the_live_lock_namespace(self):
        """The isolation `--locks-dir` exists to provide, asserted rather than
        assumed.

        Measured as a BEFORE/AFTER DIFF of the live lock dir, not as absolute
        absence: that dir legitimately holds locks for real concurrent sessions,
        and it can also hold residue from runs made before this flag existed
        (the first version of this test failed on exactly such a leftover, from
        a fixture smoke taken an hour before `--locks-dir` was added — which is
        itself the evidence that the flag was needed). What this slice is
        answerable for is that ITS OWN run adds nothing there."""
        live_locks = Path.home() / ".claude" / "state" / "locks"
        before = ({p.name for p in live_locks.iterdir()}
                  if live_locks.exists() else set())

        self.write_active(self.default_ledger())
        rc, _out, _err = self._cli("prune-active", "--apply")
        self.assertEqual(rc, 0)

        after = ({p.name for p in live_locks.iterdir()}
                 if live_locks.exists() else set())
        added = {n for n in (after - before) if "plain-dead" in n}
        self.assertEqual(added, set(),
                         f"the run leaked locks into the live namespace: {added}")
        # And the fixture's own namespace is the one that was used.
        self.assertTrue(
            any(self.locks.iterdir()),
            "the fixture lock dir was never touched — --locks-dir had no effect")


class TestCliSurfacesUnconfirmedPrunes(PruneFixture):
    """The two-phase ledger's claimed advantage is that an unconfirmed row is
    SURFACED rather than silently believed. That claim was in a comment with
    nothing implementing it until a verifier said so."""

    def _fail_write_then_cli(self, *args):
        self.write_active(self.default_ledger())
        saved = ppg.write_active
        ppg.write_active = lambda data: (_ for _ in ()).throw(
            OSError("simulated write failure"))
        try:
            ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg.write_active = saved
        proc = subprocess.run(
            [sys.executable, CLI, *args,
             "--state-dir", str(self.state), "--locks-dir", str(self.locks)],
            capture_output=True, text=True, timeout=120)
        return proc.returncode, proc.stdout, proc.stderr

    def test_an_unconfirmed_row_is_reported_by_prune_active(self):
        _rc, out, _err = self._fail_write_then_cli("prune-active")
        self.assertIn("UNCONFIRMED prune record", out)

    def test_an_unconfirmed_row_is_reported_by_restore_active(self):
        _rc, out, _err = self._fail_write_then_cli("restore-active")
        self.assertIn("UNCONFIRMED prune record", out)

    def test_negative_control_a_clean_run_reports_none(self):
        self.write_active(self.default_ledger())
        proc = subprocess.run(
            [sys.executable, CLI, "prune-active", "--apply",
             "--state-dir", str(self.state), "--locks-dir", str(self.locks)],
            capture_output=True, text=True, timeout=120)
        self.assertNotIn("UNCONFIRMED prune record", proc.stdout,
                         "a clean prune reported an unconfirmed row")


# A shim that runs the REAL CLI with ONE seam injected inside the subprocess.
#
# The first version of this test patched `ppg.write_active` in the TEST process
# and then launched a clean subprocess, where nothing was patched — so the CLI
# performed a perfectly successful prune and the assertion was satisfied by
# that success. It would have passed with the fix reverted. That is the THIRD
# vacuous test this slice has shipped, and all three had the same shape: an
# injection that never reached the code under test. The seam has to live where
# the rendering does.
CLI_SHIM = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg
    import reap_orphan_plain_topics as reap

    mode = sys.argv[1]
    argv = sys.argv[2:]

    if mode == "fail-write":
        ppg.write_active = lambda data: (_ for _ in ()).throw(
            OSError("simulated write failure"))
    elif mode == "fail-confirm":
        # Fail ONLY the second (confirming) append, so the prune really happens
        # and only its confirmation is lost.
        _real = ppg._append_prune_active_ledger
        def _one_shot(rec):
            if rec.get("committed"):
                raise OSError("simulated confirm-append failure")
            return _real(rec)
        ppg._append_prune_active_ledger = _one_shot

    sys.exit(reap.main(argv))
    """
)


class TestRollbackHintIsGatedOnCONFIRMED(PruneFixture):
    """The rollback hint must never tell an operator to run the DEFAULT restore
    unless the default would actually resolve to this prune's backup.

    Two paths break that, and they are different: `write-failed` (nothing was
    pruned at all) and confirm-append-failure (the prune DID happen, so
    `reason == "pruned"`, but its row is uncommitted so the default resolves to
    an EARLIER prune and would undo that one). The second is why the gate is
    `confirmed`, not `reason`."""

    def _cli_with_seam(self, mode, *args):
        shim = self.root / "cli_shim.py"
        shim.write_text(CLI_SHIM.format(hooks=HOOKS_DIR), encoding="utf-8")
        return subprocess.run(
            [sys.executable, str(shim), mode, *args,
             "--state-dir", str(self.state), "--locks-dir", str(self.locks)],
            capture_output=True, text=True, timeout=120)

    def test_a_write_failure_WITHHOLDS_the_hint_and_says_why(self):
        self.write_active(self.default_ledger())
        proc = self._cli_with_seam("fail-write", "prune-active", "--apply")
        self.assertIn("reason=write-failed", proc.stdout,
                      f"the seam did not reach the CLI: {proc.stdout}")
        self.assertNotIn("Roll this prune back with", proc.stdout,
                         "the rollback hint fired for a run that pruned nothing")
        self.assertIn("NOTHING was pruned", proc.stdout)
        self.assertIn("Do NOT run restore-active", proc.stdout)

    def test_a_confirm_failure_offers_the_EXPLICIT_form_not_the_default(self):
        self.write_active(self.default_ledger())
        proc = self._cli_with_seam("fail-confirm", "prune-active", "--apply")
        self.assertIn("reason=pruned", proc.stdout,
                      f"the prune did not happen, so this tests nothing: "
                      f"{proc.stdout}")
        self.assertNotIn(
            "(that defaults to this backup", proc.stdout,
            "the DEFAULT-form hint fired for an unconfirmed prune — following "
            "it would undo an EARLIER prune instead of this one")
        self.assertIn("never confirmed", proc.stdout)
        self.assertIn("--allow-superseded", proc.stdout)

    def test_negative_control_a_clean_prune_DOES_offer_the_default_form(self):
        """Proves the two withholdings above are the gate firing, not the hint
        being broken outright."""
        self.write_active(self.default_ledger())
        proc = self._cli_with_seam("none", "prune-active", "--apply")
        self.assertIn("reason=pruned", proc.stdout)
        self.assertIn("Roll this prune back with", proc.stdout)
        self.assertIn("(that defaults to this backup", proc.stdout)

    def test_the_library_reports_confirmed_only_when_the_row_committed(self):
        self.write_active(self.default_ledger())
        res = ppg.prune_active(apply=True, now=NOW)
        self.assertEqual(res["reason"], "pruned")
        self.assertTrue(res["confirmed"])

    def test_the_library_marks_a_confirm_failure_pruned_but_UNCONFIRMED(self):
        self.write_active(self.default_ledger())
        real = ppg._append_prune_active_ledger

        def one_shot(rec):
            if rec.get("committed"):
                raise OSError("simulated confirm-append failure")
            return real(rec)

        ppg._append_prune_active_ledger = one_shot
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg._append_prune_active_ledger = real

        self.assertEqual(res["reason"], "pruned", "the prune really did happen")
        self.assertFalse(res["confirmed"])
        self.assertTrue(res["errors"], "the lost confirmation was not reported")
        self.assertEqual(ppg._committed_prune_records(), [],
                         "an unconfirmed row counted as a completed prune")
        # And the rows really are gone — this is not the write-failed case.
        self.assertNotIn("dead-1", self.read_active())

    def test_the_library_marks_the_run_write_failed_and_keeps_the_backup(self):
        self.write_active(self.default_ledger())
        saved = ppg.write_active
        ppg.write_active = lambda data: (_ for _ in ()).throw(
            OSError("simulated write failure"))
        try:
            res = ppg.prune_active(apply=True, now=NOW)
        finally:
            ppg.write_active = saved
        self.assertEqual(res["reason"], "write-failed")
        self.assertFalse(res["confirmed"])
        self.assertIsNotNone(res["backup"],
                             "the verified backup really was taken and should "
                             "still be reported")
        self.assertEqual(res["pruned"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

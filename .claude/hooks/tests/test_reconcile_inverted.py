#!/usr/bin/env python3
"""S2 unit tests — the A2 Migration Contract (inverted-key backfill).

Covers the plan's Verification §2A review-driven items:
  (a) non-interactivity — ambiguous cases skip in place + log; no stdin/TTY read
  (b) crash-safety — same-fs rename is atomic (nothing to tear); the cross-fs
      copy-delete fallback resolves a simulated interrupt as a redundant
      duplicate (quarantine, never delete, no false MERGE_REVIEW)
  (c) A2 never writes `_active.json`
  (e) rename only when canonical is free, else quarantine (redundant) or skip
      (richer)
  (g) `--resolve keep-inverted` / `keep-canonical` each perform a terminal move
      and converge find_inverted() -> 0 (no infinite skip)
  (h) fail-closed backup — a backup that cannot be VERIFIED aborts the record
      with both files in place and the incumbent canonical never overwritten
  (i) the canonical path is never empty, and an interrupt after the verified
      backup leaves both files in place so a re-run repeats cleanly
  (j) a ledger entry whose inverted source was removed by hand is purged/skipped
      rather than erroring, and does not prevent find_inverted() -> 0

Run: python3 test_reconcile_inverted.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pre_plan_gates as ppg          # noqa: E402
import taskmanagement as tm           # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class InvertedFixture(unittest.TestCase):
    """Each test gets an isolated projects-root + state dir + locks dir.

    Nothing here touches the live `~/.claude/state` tree — TOPIC_STATE_DIR,
    PROJECTS_ROOT and taskmanagement.LOCKS_DIR are all redirected.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.projects = self.root / "projects"
        self.thoughts = self.projects / "Thoughts"
        self.thoughts.mkdir(parents=True)
        (self.projects / "TODO.md").write_text("# TODO\n", encoding="utf-8")
        self.state = self.root / "state" / "pre_plan_gates"
        self.state.mkdir(parents=True)
        self.locks = self.root / "state" / "locks"
        self.locks.mkdir(parents=True)

        self._saved = (ppg.TOPIC_STATE_DIR, ppg.PROJECTS_ROOT, tm.LOCKS_DIR)
        ppg.TOPIC_STATE_DIR = self.state
        ppg.PROJECTS_ROOT = self.projects
        tm.LOCKS_DIR = self.locks

    def tearDown(self):
        ppg.TOPIC_STATE_DIR, ppg.PROJECTS_ROOT, tm.LOCKS_DIR = self._saved
        self._tmp.cleanup()

    # -- fixture builders -------------------------------------------------- #

    def make_spine(self, slug: str) -> Path:
        spine = self.thoughts / f"{slug}_THOUGHT.md"
        spine.write_text(f"# {slug}\n", encoding="utf-8")
        return spine

    def make_record(self, filename: str, slug: str, *, rich: bool = False) -> Path:
        """A topic-state record stored under `filename` whose SPINE says `slug`.

        `rich=True` adds phase history so `_state_richness` ranks it above a
        bare record — this is what makes a case-(iii) ambiguous pair.
        """
        spine = self.thoughts / f"{slug}_THOUGHT.md"
        if not spine.exists():
            self.make_spine(slug)
        payload = {
            "topic_slug": slug,
            "project_slug": "Root",
            "thought_file_path": str(spine),
            "phase": "implementation" if rich else None,
            "phase_history": (
                [{"from": None, "to": "implementation", "at": "2026-01-01T00:00:00+00:00"}]
                if rich else []
            ),
        }
        if rich:
            payload["intake_source"] = "worktree-origin"
            payload["todo_line_ref"] = "TODO.md:1"
        path = self.state / filename
        _write_json(path, payload)
        return path

    def names(self):
        return sorted(p.name for p in self.state.glob("*.json"))

    def reaped(self):
        d = self.state / "_reaped"
        return sorted(p.name for p in d.glob("*")) if d.exists() else []


class TestClassificationAndDryRun(InvertedFixture):

    def test_e_rename_when_canonical_free(self):
        """(e) canonical free -> atomic rename in place."""
        self.make_record("Projects__alpha.json", "alpha")
        self.assertEqual(len(ppg.find_inverted()), 1)

        res = ppg.reconcile_inverted(apply=True)

        self.assertEqual(res["counts"]["rename"], 1)
        self.assertIn("alpha__Root.json", self.names())
        self.assertNotIn("Projects__alpha.json", self.names())
        self.assertEqual(ppg.find_inverted(), [])

    def test_e_quarantine_when_canonical_exists_and_redundant(self):
        """(e) canonical exists + inverted NOT richer -> quarantine the inverted."""
        self.make_record("alpha__Root.json", "alpha", rich=True)
        self.make_record("Projects__alpha.json", "alpha", rich=False)

        res = ppg.reconcile_inverted(apply=True)

        self.assertEqual(res["counts"]["redundant"], 1)
        self.assertIn("alpha__Root.json", self.names())
        self.assertNotIn("Projects__alpha.json", self.names())
        self.assertTrue(any(n.startswith("Projects__alpha.json.bak-")
                            for n in self.reaped()))
        self.assertEqual(ppg.find_inverted(), [])

    def test_e_skip_in_place_when_inverted_is_richer(self):
        """(e) canonical exists + inverted IS richer -> skip IN PLACE, log."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)

        res = ppg.reconcile_inverted(apply=True)

        self.assertEqual(res["counts"]["ambiguous"], 1)
        # BOTH files still in place — a skip is not a move.
        self.assertIn("alpha__Root.json", self.names())
        self.assertIn("Projects__alpha.json", self.names())
        self.assertEqual(len(ppg.find_inverted()), 1)

    def test_dry_run_mutates_nothing(self):
        self.make_record("Projects__alpha.json", "alpha")
        before = self.names()

        res = ppg.reconcile_inverted(apply=False)

        self.assertFalse(res["applied"])
        self.assertEqual(self.names(), before)
        self.assertEqual(self.reaped(), [])

    def test_dry_run_preview_matches_apply_when_records_collide(self):
        """Several inverted records can claim ONE canonical name (live data has
        three pointing at `workflow-phases-redesign__Root.json`). Only the first
        finds it free, so the DRY-RUN preview must show 1 rename + 2 others —
        not 3 renames. A preview that cannot happen is worse than no preview,
        because §3A has the operator approve the apply from it."""
        self.make_spine("alpha")
        self.make_record("AAA__alpha.json", "alpha", rich=True)
        self.make_record("BBB__alpha.json", "alpha", rich=False)
        self.make_record("CCC__alpha.json", "alpha", rich=True)
        self.assertEqual(len(ppg.find_inverted()), 3)

        preview = ppg.reconcile_inverted(apply=False)

        self.assertEqual(preview["counts"]["rename"], 1)
        self.assertEqual(
            preview["counts"]["redundant"] + preview["counts"]["ambiguous"], 2)

        applied = ppg.reconcile_inverted(apply=True)

        for key in ("rename", "redundant", "ambiguous"):
            self.assertEqual(preview["counts"][key], applied["counts"][key],
                             f"dry-run preview disagreed with apply on {key}")

    def test_richest_record_wins_a_contested_canonical_name(self):
        """Among records competing for one FREE canonical name, the richest is
        migrated first — the same contest `_reconcile_apply` already resolves by
        richness. In plain filename order a sparse record would take the name
        and push its richer sibling into MERGE_REVIEW: not lossy, but avoidable
        operator work and a second convention for one question."""
        self.make_spine("alpha")
        self.make_record("AAA__alpha.json", "alpha", rich=False)   # first by name
        self.make_record("ZZZ__alpha.json", "alpha", rich=True)    # but richer

        res = ppg.reconcile_inverted(apply=True)

        self.assertEqual(res["counts"]["rename"], 1)
        self.assertEqual(res["counts"]["ambiguous"], 0)      # no manufactured review
        self.assertEqual(res["counts"]["redundant"], 1)
        renamed = [a["from"] for a in res["actions"] if a["op"] == "rename"]
        self.assertEqual(renamed, ["ZZZ__alpha.json"])
        # the surviving canonical is the RICH one
        survivor = json.loads(
            (self.state / "alpha__Root.json").read_text(encoding="utf-8"))
        self.assertEqual(survivor["phase"], "implementation")
        self.assertEqual(ppg.find_inverted(), [])

    def test_idempotent_second_run_is_noop(self):
        self.make_record("Projects__alpha.json", "alpha")
        ppg.reconcile_inverted(apply=True)
        after_first = self.names()

        res = ppg.reconcile_inverted(apply=True)

        self.assertEqual(self.names(), after_first)
        self.assertEqual(res["counts"]["rename"], 0)


class TestNonInteractivityAndScope(InvertedFixture):

    def test_a_no_stdin_read_on_ambiguous(self):
        """(a) an ambiguous record must NOT block on a prompt. Run with stdin
        closed: a read attempt would raise, a skip+log will not."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)

        saved = sys.stdin
        sys.stdin = open(os.devnull, "r")
        sys.stdin.close()
        try:
            res = ppg.reconcile_inverted(apply=True)
        finally:
            sys.stdin = saved

        self.assertEqual(res["counts"]["ambiguous"], 1)
        self.assertEqual(res["merge_review"], ["Projects__alpha.json"])

    def test_c_never_writes_active_json(self):
        """(c) the inverted flow must never touch `_active.json`."""
        active = self.state / "_active.json"
        _write_json(active, {"sessions": {"sid-1": "Projects__alpha"}})
        before = active.read_bytes()
        before_mtime = active.stat().st_mtime_ns

        self.make_record("Projects__alpha.json", "alpha")
        self.make_record("beta__Root.json", "beta", rich=True)
        self.make_record("Projects__beta.json", "beta", rich=False)
        ppg.reconcile_inverted(apply=True)

        self.assertEqual(active.read_bytes(), before)
        self.assertEqual(active.stat().st_mtime_ns, before_mtime)

    def test_active_json_is_not_classified_as_a_record(self):
        _write_json(self.state / "_active.json", {"sessions": {}})
        self.assertEqual(ppg.find_inverted(), [])


class TestResolution(InvertedFixture):

    def _ambiguous_pair(self):
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)          # logs the MERGE_REVIEW entry

    def test_g_keep_inverted_converges_to_zero(self):
        """(g) keep-inverted performs a terminal move and reaches 0 inverted."""
        self._ambiguous_pair()
        incumbent = (self.state / "alpha__Root.json").read_text(encoding="utf-8")

        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-inverted")

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["counts"]["resolved"], 1)
        self.assertIn("alpha__Root.json", self.names())
        self.assertNotIn("Projects__alpha.json", self.names())
        self.assertEqual(ppg.find_inverted(), [])
        # the incumbent is NOT lost — its verified copy is in _reaped/
        backups = [n for n in self.reaped() if n.startswith("alpha__Root.json.bak-")]
        self.assertEqual(len(backups), 1)
        self.assertEqual(
            (self.state / "_reaped" / backups[0]).read_text(encoding="utf-8"),
            incumbent)

    def test_g_keep_canonical_converges_to_zero(self):
        """(g) keep-canonical quarantines the loser and reaches 0 inverted."""
        self._ambiguous_pair()
        canonical = (self.state / "alpha__Root.json").read_text(encoding="utf-8")

        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-canonical")

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["counts"]["resolved"], 1)
        self.assertEqual(
            (self.state / "alpha__Root.json").read_text(encoding="utf-8"), canonical)
        self.assertNotIn("Projects__alpha.json", self.names())
        self.assertTrue(any(n.startswith("Projects__alpha.json.bak-")
                            for n in self.reaped()))
        self.assertEqual(ppg.find_inverted(), [])

    def test_g_repeated_bulk_run_alone_never_converges(self):
        """The bulk run alone does NOT reach zero — this is why the explicit
        --resolve step exists (and why C1 is unsatisfied without it)."""
        self._ambiguous_pair()
        for _ in range(3):
            ppg.reconcile_inverted(apply=True)
        self.assertEqual(len(ppg.find_inverted()), 1)

    def test_resolve_rejects_unknown_value(self):
        self._ambiguous_pair()
        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-whatever")
        self.assertTrue(res["errors"])
        self.assertIn("unknown --resolve", res["errors"][0])
        self.assertIn("Projects__alpha.json", self.names())

    def test_resolve_rejects_slug_not_in_ledger(self):
        """A mistyped slug is rejected against the ledger, never acted on."""
        self.make_record("Projects__alpha.json", "alpha")
        res = ppg.reconcile_inverted(apply=True, slug="typo-slug",
                                     resolve="keep-inverted")
        self.assertTrue(res["errors"])
        self.assertIn("Projects__alpha.json", self.names())

    def test_resolve_refuses_unflagged_record(self):
        """A record the bulk run can handle is not resolvable — no silent
        double-path for the non-ambiguous cases."""
        self.make_record("Projects__alpha.json", "alpha")
        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-inverted")
        self.assertTrue(res["errors"])
        self.assertIn("not flagged MERGE_REVIEW", res["errors"][0])

    def test_ambiguous_slug_across_records_demands_file(self):
        """Two inverted records claiming ONE canonical name (a real shape in the
        live data) cannot be resolved by --slug alone."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        self.make_record("<KL>__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)

        res = ppg.reconcile_inverted(apply=True, slug="alpha", resolve="keep-inverted")

        self.assertTrue(res["errors"])
        self.assertIn("--file", res["errors"][0])


class TestFailClosedAndNeverEmpty(InvertedFixture):

    def test_h_unverifiable_backup_aborts_record(self):
        """(h) with the incumbent backup forced to verify MISMATCHED, the record
        aborts with BOTH files in place and the canonical never overwritten."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)
        canonical_before = (self.state / "alpha__Root.json").read_bytes()
        inverted_before = (self.state / "Projects__alpha.json").read_bytes()

        real_copy = ppg._verified_copy
        ppg._verified_copy = lambda src, dest: False        # forced mismatch
        try:
            res = ppg.reconcile_inverted(apply=True,
                                         inverted_file="Projects__alpha.json",
                                         resolve="keep-inverted")
        finally:
            ppg._verified_copy = real_copy

        self.assertTrue(res["errors"])
        self.assertEqual(res["counts"]["resolved"], 0)
        self.assertEqual((self.state / "alpha__Root.json").read_bytes(),
                         canonical_before)
        self.assertEqual((self.state / "Projects__alpha.json").read_bytes(),
                         inverted_before)

    def test_h_backup_failure_leaves_no_partial_backup(self):
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)

        real_copy = ppg._verified_copy
        ppg._verified_copy = lambda src, dest: False
        try:
            ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                   resolve="keep-inverted")
        finally:
            ppg._verified_copy = real_copy

        self.assertEqual(
            [n for n in self.reaped() if n.startswith("alpha__Root.json.bak-")], [])

    def test_i_canonical_path_never_empty_during_keep_inverted(self):
        """(i) a reader sampling the canonical path throughout the operation
        always finds a file — the incumbent before the swap, the inverted after.
        Sampled from inside the rename, the only moment a move-aside design
        would have exposed an empty path."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)
        canon = self.state / "alpha__Root.json"
        samples = []

        real_place = ppg._place_atomically

        def sampling_place(src, dest):
            samples.append(canon.exists())      # immediately BEFORE the swap
            out = real_place(src, dest)
            samples.append(canon.exists())      # immediately AFTER the swap
            return out

        ppg._place_atomically = sampling_place
        try:
            ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                   resolve="keep-inverted")
        finally:
            ppg._place_atomically = real_place

        self.assertEqual(samples, [True, True])
        self.assertTrue(canon.exists())

    def test_i_interrupt_after_verified_backup_is_resumable(self):
        """(i) an interrupt AFTER the verified backup but BEFORE the swap leaves
        both files in place plus a redundant backup, so a re-run repeats
        cleanly and still converges."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)

        class Interrupt(Exception):
            pass

        real_place = ppg._place_atomically
        ppg._place_atomically = lambda src, dest: (_ for _ in ()).throw(Interrupt())
        try:
            ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                   resolve="keep-inverted")
        except Interrupt:
            pass
        finally:
            ppg._place_atomically = real_place

        # both still in place; a verified backup already exists
        self.assertIn("alpha__Root.json", self.names())
        self.assertIn("Projects__alpha.json", self.names())
        self.assertTrue(any(n.startswith("alpha__Root.json.bak-")
                            for n in self.reaped()))

        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-inverted")
        self.assertEqual(res["errors"], [])
        self.assertEqual(ppg.find_inverted(), [])

    def test_locks_are_released_after_a_run(self):
        """Obligation 1 — no lock leak. A completed run must leave no held lock
        that would freeze the topic until the stale timeout."""
        self.make_record("Projects__alpha.json", "alpha")
        ppg.reconcile_inverted(apply=True)

        res = tm.acquire_lock("alpha__Root", "probe-session")
        self.assertIn(res["status"], ("ACQUIRED", "ALREADY_HELD_BY_SELF"))
        tm.release_lock("alpha__Root", "probe-session")

    def test_never_deletes_anything(self):
        """Across every case, the union of live + quarantined records preserves
        every original file's CONTENT — nothing is destroyed."""
        self.make_record("Projects__alpha.json", "alpha")            # (i)
        self.make_record("beta__Root.json", "beta", rich=True)       # (ii) canonical
        self.make_record("Projects__beta.json", "beta", rich=False)  # (ii) redundant
        originals = {p.name: p.read_text(encoding="utf-8")
                     for p in self.state.glob("*.json")}

        ppg.reconcile_inverted(apply=True)

        surviving = set()
        for p in list(self.state.glob("*.json")) + list((self.state / "_reaped").glob("*")):
            try:
                surviving.add(p.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                pass
        for name, body in originals.items():
            self.assertIn(body, surviving, f"content of {name} was lost")


class TestCrossFsFallbackAndLedger(InvertedFixture):

    def test_b_cross_fs_interrupt_resolves_as_redundant_duplicate(self):
        """(b) simulate the cross-fs path being interrupted AFTER the canonical
        copy landed but BEFORE the source was quarantined: a re-run must resolve
        it as a redundant duplicate (quarantine, never delete) and raise no
        false MERGE_REVIEW."""
        # The torn state: canonical written (identical content), source still there.
        self.make_record("Projects__alpha.json", "alpha")
        src = self.state / "Projects__alpha.json"
        (self.state / "alpha__Root.json").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8")

        res = ppg.reconcile_inverted(apply=True)

        self.assertEqual(res["counts"]["redundant"], 1)
        self.assertEqual(res["counts"]["ambiguous"], 0)
        self.assertEqual(res["merge_review"], [])
        self.assertIn("alpha__Root.json", self.names())
        self.assertNotIn("Projects__alpha.json", self.names())
        self.assertTrue(any(n.startswith("Projects__alpha.json.bak-")
                            for n in self.reaped()))
        self.assertEqual(ppg.find_inverted(), [])

    def test_b_same_fs_rename_is_atomic(self):
        """(b) case (i) is a same-fs rename — POSIX-atomic, so there is no torn
        state to test. Assert the mode actually taken is `rename`, which is what
        makes that claim true rather than assumed."""
        self.make_record("Projects__alpha.json", "alpha")
        res = ppg.reconcile_inverted(apply=True)
        modes = [a.get("mode") for a in res["actions"] if a["op"] == "rename"]
        self.assertEqual(modes, ["rename"])

    def test_j_stale_ledger_entry_is_purged_not_errored(self):
        """(j) a ledger entry whose inverted source was removed BY HAND is
        purged/skipped rather than erroring, and does not stop convergence."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)                     # logs the entry
        # operator resolves it by hand, outside --resolve
        (self.state / "Projects__alpha.json").unlink()

        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-inverted")

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["counts"]["gone"], 1)
        self.assertEqual(ppg.find_inverted(), [])

    def test_j_stale_purge_by_slug_selector(self):
        """(j) via the --slug selector, not just --file. The stale-purge branch
        must match on the entry's RECORDED spine_slug."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)
        (self.state / "Projects__alpha.json").unlink()      # resolved by hand

        res = ppg.reconcile_inverted(apply=True, slug="alpha",
                                     resolve="keep-inverted")

        self.assertEqual(res["errors"], [])
        self.assertEqual(res["counts"]["gone"], 1)

    def test_stale_purge_never_matches_on_the_project_segment(self):
        """A --slug naming some OTHER topic's PROJECT segment must NOT purge a
        pending entry. Ledger keys are `<project>__<topic>.json`, so a
        filename-prefix match would test the project half against a requested
        topic slug — and `--slug Projects` is a live value here, the root
        project's own name. Only the recorded spine_slug may match."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)                 # pending entry logged
        (self.state / "Projects__alpha.json").unlink()      # its source now gone

        # "Projects" is the PROJECT segment of the pending entry's filename,
        # and is NOT any topic's spine slug.
        res = ppg.reconcile_inverted(apply=True, slug="Projects",
                                     resolve="keep-inverted")

        self.assertEqual(res["counts"]["gone"], 0, "purged an unrelated entry")
        self.assertTrue(res["errors"])
        entry = ppg._read_merge_review_ledger()["Projects__alpha.json"]
        self.assertFalse(entry.get("resolved"),
                         "unrelated pending entry was falsely marked resolved")

    def test_ledger_survives_a_torn_trailing_append(self):
        """Obligation 7 — a halted append leaves at most one unparseable line;
        it is skipped, never allowed to brick --resolve."""
        self.make_record("alpha__Root.json", "alpha", rich=False)
        self.make_record("Projects__alpha.json", "alpha", rich=True)
        ppg.reconcile_inverted(apply=True)

        ledger = ppg._merge_review_ledger_path()
        with open(ledger, "a", encoding="utf-8") as fh:
            fh.write('{"file": "torn-partial', )            # no newline, truncated

        entries = ppg._read_merge_review_ledger()
        self.assertIn("Projects__alpha.json", entries)

        res = ppg.reconcile_inverted(apply=True, inverted_file="Projects__alpha.json",
                                     resolve="keep-canonical")
        self.assertEqual(res["errors"], [])
        self.assertEqual(ppg.find_inverted(), [])

    def test_prep_reaped_is_module_scope_and_reusable(self):
        """The shared-helper extraction: `_prep_reaped` must be callable by a
        second consumer (A1's prune-active) without going through
        `_reconcile_apply`."""
        self.assertTrue(callable(ppg._prep_reaped))
        out = ppg._prep_reaped()
        self.assertTrue(out.exists())
        self.assertEqual(out, self.state / "_reaped")


if __name__ == "__main__":
    unittest.main(verbosity=2)

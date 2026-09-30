#!/usr/bin/env python3
"""S0 (Cluster-B) — `_active.json` read-modify-write serialization.

Covers the four regions S0 brought under `_active_lock()`, the F1 re-order that a
lock alone could not fix, and the two properties of the shipped lock primitive an
executor must not assume away.

WHY SUBPROCESSES, AND NOT THREADS — this is load-bearing, not a style choice.
`bookkeeping_lock`'s `_HELD` registry is a process-global dict keyed on lock path
with NO thread identity, and a second acquisition of the same path inside one
process takes a re-entrant refcount fast path that acquires no `flock`. Two
threads in one process therefore do not serialize AT ALL. A thread-driven
interleaving test would pass against the pre-S0 code (proving nothing) or fail
against the post-S0 code (a false alarm). Every concurrency assertion below runs
in real subprocesses via a helper script.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_active_json_serialization.py
"""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pre_plan_gates as ppg          # noqa: E402
import bookkeeping_lock as bkl        # noqa: E402

HOOKS_DIR = str(Path(__file__).resolve().parent.parent)


# --------------------------------------------------------------------------- #
# Subprocess worker — drives a REAL writer call site, not a hand-rolled RMW.
# --------------------------------------------------------------------------- #

WORKER = textwrap.dedent(
    """
    import json, os, sys, time
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg

    state_dir  = sys.argv[1]
    session_id = sys.argv[2]
    slug       = sys.argv[3]
    stagger_s  = float(sys.argv[4])
    defeat     = sys.argv[5] == "defeat-lock"

    ppg.TOPIC_STATE_DIR = __import__("pathlib").Path(state_dir)

    if defeat:
        # NEGATIVE CONTROL. S0 has landed, so the pre-S0 code no longer exists in
        # the tree; the only honest way to reconstruct its behaviour is to
        # neutralise the lock while leaving every other code path identical.
        import contextlib
        ppg._active_lock = lambda: contextlib.nullcontext()

    # Widen the interleaving window INSIDE the read-modify-write, so that without
    # a lock the two workers reliably overlap. Patching `read_active` (not
    # `write_active`) puts the delay between the read and the write, which is
    # exactly where the lost update lives.
    _real_read = ppg.read_active
    def _slow_read():
        data = _real_read()
        time.sleep(stagger_s)
        return data
    ppg.read_active = _slow_read

    # A REAL call site: set_active is one of the four regions, and it performs a
    # genuine read-modify-write of the shared ledger.
    (ppg.TOPIC_STATE_DIR / (slug + "__proj.json")).write_text(
        json.dumps({{"topic_slug": slug, "project_slug": "proj"}}), encoding="utf-8")
    ppg.set_active(session_id, slug, "proj")
    print("done", session_id)
    """
)


class ActiveJsonFixture(unittest.TestCase):
    """Isolated state dir; the live ~/.claude/state tree is never touched."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "state" / "pre_plan_gates"
        self.state.mkdir(parents=True)
        self._saved_state = ppg.TOPIC_STATE_DIR
        ppg.TOPIC_STATE_DIR = self.state

    def tearDown(self):
        ppg.TOPIC_STATE_DIR = self._saved_state
        self._tmp.cleanup()

    def _run_workers(self, *, defeat_lock, n=2, stagger=0.35):
        script = self.root / "worker.py"
        script.write_text(WORKER.format(hooks=HOOKS_DIR), encoding="utf-8")
        procs = []
        for i in range(n):
            procs.append(subprocess.Popen(
                [sys.executable, str(script), str(self.state),
                 f"sid-{i}", f"slug-{i}", str(stagger),
                 "defeat-lock" if defeat_lock else "keep-lock"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        for p in procs:
            p.communicate(timeout=60)
        path = self.state / "_active.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class TestInterleavedWritersRetainBothEntries(ActiveJsonFixture):
    """Test (f) — the headline assertion, with its negative control."""

    def test_two_interleaved_writers_both_retain_their_entries(self):
        active = self._run_workers(defeat_lock=False)
        self.assertIn("sid-0", active,
                      "first writer's insert was lost despite the lock")
        self.assertIn("sid-1", active,
                      "second writer's insert was lost despite the lock")

    def test_negative_control_without_the_lock_an_insert_IS_lost(self):
        """Proves the test can fail — i.e. that it is testing the lock.

        Without this, a green result above would be indistinguishable from a test
        that asserts nothing. The lock is neutralised and NOTHING else changes.
        """
        active = self._run_workers(defeat_lock=True)
        self.assertEqual(
            len(active), 1,
            "expected the unlocked read-modify-write to lose exactly one insert "
            f"(last-writer-wins); got {sorted(active)}. If this ever reports 2, "
            "the interleaving window closed and the positive test above has "
            "stopped proving anything — widen `stagger`, do not delete this test."
        )


class TestF1RepointSurvivesCreateTopic(ActiveJsonFixture):
    """F1 — the lost update a re-entrant lock could NOT have fixed.

    `_ensure_canonical_record` renames the mis-keyed state file onto the canonical
    key and calls `_repoint_active`, which lands a repoint on disk. Before the F1
    re-order, `create_topic` had already snapshotted `_active.json` ABOVE that
    call, so its own write reverted the repoint — binding those sessions to a
    project whose record had just been renamed away.
    """

    def test_repoint_is_not_reverted_by_create_topics_own_write(self):
        # The repoint must be triggered BY create_topic itself — that is the only
        # arrangement in which the stale-snapshot window actually opens. Driving
        # `_repoint_active` by hand beforehand does NOT reproduce F1 and yields a
        # test that passes against the broken ordering.
        projects = self.root / "projects"
        leaf = projects / "myproj"
        (leaf / "Thoughts").mkdir(parents=True)
        (projects / "TODO.md").write_text("# TODO\n", encoding="utf-8")
        (leaf / "TODO.md").write_text("# TODO\n", encoding="utf-8")
        spine = leaf / "Thoughts" / "topic-x_THOUGHT.md"
        spine.write_text("# topic-x\n", encoding="utf-8")

        saved_root = ppg.PROJECTS_ROOT
        ppg.PROJECTS_ROOT = projects
        try:
            # The spine localises to the leaf project, so the canonical project is
            # "myproj" while the caller passes the mis-keyed "Root".
            self.assertEqual(
                ppg.canonical_project_for_spine(spine, project_root=projects),
                "myproj", "fixture does not produce the mis-keyed/canonical split")

            # A DIFFERENT session already bound to the mis-keyed project.
            (self.state / "_active.json").write_text(json.dumps({
                "other-sid": {"topic_slug": "topic-x",
                              "active_project": "Root",
                              "updated": "2026-01-01T00:00:00+00:00"},
            }), encoding="utf-8")
            # The mis-keyed record — the twin `_ensure_canonical_record` renames.
            (self.state / "topic-x__Root.json").write_text(json.dumps({
                "topic_slug": "topic-x", "project_slug": "Root",
                "phase": "implementation",
                "phase_history": [{"to": "implementation"}],
            }), encoding="utf-8")

            # Inside this call: _ensure_canonical_record renames the record onto
            # topic-x__myproj.json and _repoint_active lands other-sid -> myproj.
            # create_topic then performs its own _active.json write.
            ppg.create_topic("new-sid", "Root", topic_slug="topic-x",
                             thought_file_path=str(spine))
        finally:
            ppg.PROJECTS_ROOT = saved_root

        self.assertTrue((self.state / "topic-x__myproj.json").exists(),
                        "fixture did not reach the rename — F1 window never opened")

        active = json.loads((self.state / "_active.json").read_text(encoding="utf-8"))
        self.assertEqual(
            active["other-sid"]["active_project"], "myproj",
            "create_topic's write reverted an already-landed repoint, rebinding "
            "that session to 'Root' — whose record was just renamed away. This is "
            "the F1 lost update; a re-entrant lock does NOT fix it, only reading "
            "after the repoint does."
        )
        self.assertIn("new-sid", active, "create_topic's own binding is missing")


class TestLockTargetUniformity(unittest.TestCase):
    """Obligation 11 / test (i3) — every acquisition resolves to ONE lock.

    Asserted in a NON-GIT temp dir on purpose. `lock_path_for` prefers the shared
    git dir whenever the target is inside a repo, so a git-backed run collapses
    every target to one lock and would mask a divergence. The sidecar fallback is
    the path actually under test.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state = self.root / "state" / "pre_plan_gates"
        self.state.mkdir(parents=True)
        self._saved = ppg.TOPIC_STATE_DIR
        ppg.TOPIC_STATE_DIR = self.state

    def tearDown(self):
        ppg.TOPIC_STATE_DIR = self._saved
        self._tmp.cleanup()

    def test_the_temp_dir_is_genuinely_outside_a_git_repo(self):
        """Guards the guard: if the temp dir were inside a repo, the assertion
        below would pass vacuously by collapsing every target to the repo lock."""
        resolved = bkl.lock_path_for(ppg._active_path())
        self.assertTrue(
            str(resolved).endswith("_active.json.bookkeeping.lock"),
            f"expected the per-file SIDECAR fallback, got {resolved!r} — the temp "
            "dir is inside a git repo, so this test is not exercising the path it "
            "claims to."
        )

    def test_every_active_json_acquisition_resolves_to_the_same_lock(self):
        seen = set()
        real = bkl.bookkeeping_lock

        def _recording(target, **kw):
            seen.add(str(bkl.lock_path_for(target)))
            return real(target, **kw)

        bkl.bookkeeping_lock = _recording
        try:
            (self.state / "t__proj.json").write_text(
                json.dumps({"topic_slug": "t", "project_slug": "proj"}),
                encoding="utf-8")
            ppg.set_active("sid-a", "t", "proj")
            ppg.create_topic("sid-b", "proj", topic_slug="t2")
            ppg._repoint_active("t", "proj", "proj2")
        finally:
            bkl.bookkeeping_lock = real

        self.assertEqual(
            len(seen), 1,
            f"_active.json acquisitions resolved to {len(seen)} different locks: "
            f"{sorted(seen)}. Obligation 11 requires exactly one — otherwise "
            "prune-active's flock serializes against only a subset of writers."
        )


class TestEveryWriterIsCovered(unittest.TestCase):
    """Structural: no `write_active` call site sits outside a lock region.

    A source-level check rather than a behavioural one, because a NEW writer added
    later is exactly the regression this must catch, and a behavioural test only
    covers the writers someone remembered to exercise.
    """

    def test_no_write_active_call_site_is_outside_an_active_lock_region(self):
        import ast
        src = Path(HOOKS_DIR, "pre_plan_gates.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        locked_ranges, write_lines = [], []

        class V(ast.NodeVisitor):
            def visit_With(self, node):
                for item in node.items:
                    call = item.context_expr
                    if (isinstance(call, ast.Call)
                            and isinstance(call.func, ast.Name)
                            and call.func.id == "_active_lock"):
                        locked_ranges.append((node.lineno, node.end_lineno))
                self.generic_visit(node)

            def visit_Call(self, node):
                if isinstance(node.func, ast.Name) and node.func.id == "write_active":
                    write_lines.append(node.lineno)
                self.generic_visit(node)

        V().visit(tree)

        # The definition site `def write_active(...)` is not a call; the helper
        # `_create_topic_locked` is called FROM inside a region, so its own writes
        # are covered transitively — resolve that one hop explicitly.
        covered_fns = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for lo, hi in locked_ranges:
                    if any(lo <= c.lineno <= hi for c in ast.walk(tree)
                           if isinstance(c, ast.Call)
                           and isinstance(c.func, ast.Name)
                           and c.func.id == node.name):
                        covered_fns.add((node.lineno, node.end_lineno))

        uncovered = [
            ln for ln in write_lines
            if not any(lo <= ln <= hi for lo, hi in locked_ranges + list(covered_fns))
        ]
        self.assertEqual(
            uncovered, [],
            f"write_active() called outside any _active_lock() region at line(s) "
            f"{uncovered}. Every _active.json read-modify-write must be "
            "serialized, or prune-active's flock protects only a subset."
        )

    def test_the_structural_check_can_actually_fail(self):
        """Guards the guard above against silently matching nothing."""
        import ast
        tree = ast.parse("def f():\n    write_active(x)\n")
        found = [n.lineno for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "write_active"]
        self.assertEqual(found, [2])


# --------------------------------------------------------------------------- #
# Test (f), the REST of it — behavioural coverage of the other three regions.
# --------------------------------------------------------------------------- #
#
# WHY THIS EXISTS. §2B item (f) does not ask for "an interleaving test"; it asks
# for one "covered for each of the four RMW regions, including
# `auto_register_topic` (the /close path), and including a case that interleaves
# at the rebind/fresh-mint region's shared opening `read_active()`". The original
# suite drove `set_active` only, and leaned on the STRUCTURAL check above for the
# other three. Those prove different things and one cannot stand in for the
# other: the structural check proves a `write_active` call sits lexically inside
# a lock region; it cannot prove a concurrent insert during that region's
# read-modify-write actually survives. A region could be lexically covered and
# still lose data — by opening its read before the lock, for instance, which is
# precisely the defect A0's guard rail warns about.
#
# Every class below carries its own negative control, for the same reason test
# (f)'s does: without one, a green result is indistinguishable from a test that
# asserts nothing.

REGION_WORKER = textwrap.dedent(
    """
    import contextlib, json, sys, time
    from pathlib import Path
    sys.path.insert(0, {hooks!r})
    import pre_plan_gates as ppg

    state_dir = sys.argv[1]
    region    = sys.argv[2]
    idx       = int(sys.argv[3])
    stagger_s = float(sys.argv[4])
    defeat    = sys.argv[5] == "defeat-lock"
    projects  = sys.argv[6]

    ppg.TOPIC_STATE_DIR = Path(state_dir)
    ppg.PROJECTS_ROOT = Path(projects)

    if defeat:
        # NEGATIVE CONTROL — neutralise the lock, change nothing else. S0 has
        # landed, so the pre-S0 tree no longer exists to run against.
        ppg._active_lock = lambda: contextlib.nullcontext()

    # Widen the window INSIDE the read-modify-write: patching `read_active`
    # (never `write_active`) puts the delay between the read and the write,
    # which is exactly where a lost update lives.
    _real_read = ppg.read_active
    def _slow_read():
        data = _real_read()
        time.sleep(stagger_s)
        return data
    ppg.read_active = _slow_read

    sid = "sid-%d" % idx

    if region == "repoint":
        # Each worker repoints ITS OWN slug. Both repoints must land: this
        # region mutates existing rows rather than inserting, so the loss shows
        # up as a row still reading the OLD project.
        ppg._repoint_active("slug-%d" % idx, "old", "new")
    elif region == "create_topic":
        # The shared opening `read_active()` of the rebind/fresh-mint region.
        # Worker 0 REBINDS (its state file is pre-seeded by the parent); worker 1
        # FRESH-MINTS. Both arms enter through the same read, which is the case
        # the spec names.
        ppg.create_topic(sid, "proj", topic_slug="slug-%d" % idx)
    elif region == "auto_register":
        spine = Path(projects) / "Thoughts" / ("auto-%d-20260101000000_PLAN.md" % idx)
        ppg.auto_register_topic(sid, str(spine))
    else:
        raise SystemExit("unknown region: " + region)

    print("done", sid)
    """
)


class RegionInterleaveFixture(ActiveJsonFixture):
    """Drives two REAL call sites of one region concurrently, in subprocesses."""

    REGION = None

    def setUp(self):
        super().setUp()
        self.projects = self.root / "projects"
        (self.projects / "Thoughts").mkdir(parents=True)
        (self.projects / "TODO.md").write_text("# TODO\n\n## Now\n\n", encoding="utf-8")
        self._saved_root = ppg.PROJECTS_ROOT
        ppg.PROJECTS_ROOT = self.projects

    def tearDown(self):
        ppg.PROJECTS_ROOT = self._saved_root
        super().tearDown()

    def _run_region(self, *, defeat_lock, n=2, stagger=0.35):
        script = self.root / f"region_worker_{self.REGION}.py"
        script.write_text(REGION_WORKER.format(hooks=HOOKS_DIR), encoding="utf-8")
        procs = [
            subprocess.Popen(
                [sys.executable, str(script), str(self.state), self.REGION,
                 str(i), str(stagger),
                 "defeat-lock" if defeat_lock else "keep-lock", str(self.projects)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for i in range(n)
        ]
        for p in procs:
            p.communicate(timeout=90)
        path = self.state / "_active.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


class TestRepointActiveRegionIsSerialized(RegionInterleaveFixture):
    """Test (f), region 1 of 4 — `_repoint_active`."""

    REGION = "repoint"

    def _seed(self):
        (self.state / "_active.json").write_text(json.dumps({
            "sid-0": {"topic_slug": "slug-0", "active_project": "old",
                      "updated": "2026-01-01T00:00:00+00:00"},
            "sid-1": {"topic_slug": "slug-1", "active_project": "old",
                      "updated": "2026-01-01T00:00:00+00:00"},
        }), encoding="utf-8")

    def test_two_concurrent_repoints_both_land(self):
        self._seed()
        active = self._run_region(defeat_lock=False)
        self.assertEqual(
            [active["sid-0"]["active_project"], active["sid-1"]["active_project"]],
            ["new", "new"],
            "a concurrent repoint was lost despite the lock — the region's "
            "read-modify-write is not serialized end to end")

    def test_negative_control_without_the_lock_a_repoint_IS_lost(self):
        self._seed()
        active = self._run_region(defeat_lock=True)
        landed = [k for k in ("sid-0", "sid-1")
                  if active.get(k, {}).get("active_project") == "new"]
        self.assertEqual(
            len(landed), 1,
            "expected the unlocked read-modify-write to lose exactly one repoint "
            f"(last-writer-wins); got {landed}. If this reports 2, the "
            "interleaving window closed and the test above proves nothing — "
            "widen `stagger`, do not delete this test.")


class TestCreateTopicRegionIsSerialized(RegionInterleaveFixture):
    """Test (f), region 2 of 4 — `create_topic`'s shared opening `read_active()`.

    Worker 0 takes the REBIND arm and worker 1 the FRESH-MINT arm, so the two
    exits of the one region race at the single read that opens it. This is the
    case §2B names explicitly; it is NOT what (f1) tests, which is a
    single-process ORDERING defect with no second writer at all.
    """

    REGION = "create_topic"

    def test_rebind_and_fresh_mint_both_retain_their_bindings(self):
        # Pre-seed worker 0's topic state so its call takes the rebind arm.
        (self.state / "slug-0__proj.json").write_text(json.dumps({
            "topic_slug": "slug-0", "project_slug": "proj",
            "phase": "implementation",
        }), encoding="utf-8")
        active = self._run_region(defeat_lock=False)
        self.assertIn("sid-0", active, "the rebinding writer's entry was lost")
        self.assertIn("sid-1", active, "the fresh-minting writer's entry was lost")

    def test_negative_control_without_the_lock_a_binding_IS_lost(self):
        (self.state / "slug-0__proj.json").write_text(json.dumps({
            "topic_slug": "slug-0", "project_slug": "proj",
            "phase": "implementation",
        }), encoding="utf-8")
        active = self._run_region(defeat_lock=True)
        self.assertEqual(
            len(active), 1,
            "expected the unlocked region to lose exactly one binding "
            f"(last-writer-wins); got {sorted(active)}. If this reports 2, the "
            "window closed and the test above proves nothing — widen `stagger`.")


class TestAutoRegisterRegionIsSerialized(RegionInterleaveFixture):
    """Test (f), region 4 of 4 — `auto_register_topic`, the `/close` path.

    Named explicitly by §2B, and the region carrying the mis-lock trap: the
    `bookkeeping_lock(todo_target)` block two lines above this read-modify-write
    keys on the PROJECTS repo, so extending it downward would leave this region
    serializing against a different lock entirely. A behavioural test is what
    distinguishes "took a lock" from "took the RIGHT lock".
    """

    REGION = "auto_register"

    def _seed_spines(self):
        for i in (0, 1):
            spine = (self.projects / "Thoughts"
                     / f"auto-{i}-20260101000000_PLAN.md")
            spine.write_text(f"# auto-{i}\n", encoding="utf-8")

    def test_two_concurrent_auto_registrations_both_bind(self):
        self._seed_spines()
        active = self._run_region(defeat_lock=False)
        self.assertIn("sid-0", active, "first /close binding was lost despite the lock")
        self.assertIn("sid-1", active, "second /close binding was lost despite the lock")

    def test_negative_control_without_the_lock_a_binding_IS_lost(self):
        self._seed_spines()
        active = self._run_region(defeat_lock=True)
        self.assertEqual(
            len(active), 1,
            "expected the unlocked /close read-modify-write to lose exactly one "
            f"binding (last-writer-wins); got {sorted(active)}. If this reports "
            "2, the window closed and the test above proves nothing.")


# --------------------------------------------------------------------------- #
# A0's Validation-gate item 3 — a held lock surfaces a BOUNDED TIMEOUT.
# --------------------------------------------------------------------------- #
#
# A0 (S0)'s Validation-gate cell lists four test items; three of them (the
# interleaved-writer test, existing-suites-still-pass, and lock-target
# uniformity) were covered from the start. The third — "a held lock surfaces a
# bounded timeout instead of hanging" — was not, and the gap is not academic:
# `_active_lock()` sits on the `/work-start` and `/close` hot paths, so a
# regression that turned the bounded acquire into an unbounded wait would hang
# the operator's session with no diagnostic.
#
# `bookkeeping_lock`'s own timeout is covered at the PRIMITIVE level elsewhere
# (test_bookkeeping_coherence_s2.py). That is a different assertion: it proves
# the primitive can time out, not that THIS helper reaches that behaviour. A
# future edit passing `timeout=0`, or catching the exception, would leave the
# primitive test green while the property this cell names was gone.
#
# The holder must be a SUBPROCESS for the reason stated at the top of this file:
# a second acquisition inside one process takes the re-entrant refcount fast
# path and never blocks, so an in-process holder would prove nothing.

HOLDER = textwrap.dedent(
    """
    import sys, time
    sys.path.insert(0, {hooks!r})
    from bookkeeping_lock import bookkeeping_lock

    target, ready_flag, hold_s = sys.argv[1], sys.argv[2], float(sys.argv[3])
    with bookkeeping_lock(target, timeout=30):
        open(ready_flag, "w").write("held")
        time.sleep(hold_s)
    """
)


class TestHeldLockSurfacesBoundedTimeout(ActiveJsonFixture):
    """A0 Validation-gate item 3 — bounded timeout, not a hang."""

    HOLD_S = 6.0
    LOCK_TIMEOUT = 1.0

    def _start_holder(self):
        script = self.root / "holder.py"
        script.write_text(HOLDER.format(hooks=HOOKS_DIR), encoding="utf-8")
        ready = self.root / "held.flag"
        proc = subprocess.Popen(
            [sys.executable, str(script), str(ppg._active_path()),
             str(ready), str(self.HOLD_S)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.time() + 30
        while not ready.exists() and time.time() < deadline:
            if proc.poll() is not None:
                self.fail(f"holder exited early: {proc.communicate()[1]}")
            time.sleep(0.02)
        self.assertTrue(ready.exists(), "holder never acquired the lock")
        return proc

    def test_acquiring_a_held_lock_raises_within_the_bound(self):
        # The temp state dir is outside any git repo, so this resolves to a
        # per-file SIDECAR — the real ~/.claude/.git/bookkeeping.lock is never
        # taken, and a live session is never blocked by this test.
        self.assertNotIn(
            ".git", str(bkl.lock_path_for(ppg._active_path())),
            "test would contend on the REAL harness repo lock — refusing")

        proc = self._start_holder()
        saved = bkl.DEFAULT_TIMEOUT
        bkl.DEFAULT_TIMEOUT = self.LOCK_TIMEOUT
        try:
            started = time.time()
            with self.assertRaises(bkl.BookkeepingLockTimeout):
                with ppg._active_lock():
                    self.fail("acquired a lock another process holds")
            elapsed = time.time() - started
        finally:
            bkl.DEFAULT_TIMEOUT = saved
            proc.wait(timeout=30)

        self.assertLess(
            elapsed, self.HOLD_S,
            f"the acquire returned only after the holder released ({elapsed:.1f}s "
            f"vs a {self.LOCK_TIMEOUT}s bound) — that is a hang that happened to "
            "end, not a bounded timeout")

    def test_negative_control_with_no_holder_the_same_call_acquires(self):
        """Guards the guard: proves the assertion above is caused by the HELD
        lock and not by the shortened timeout, a bad path, or a broken fixture.
        Without this, `assertRaises` would pass for any reason at all."""
        saved = bkl.DEFAULT_TIMEOUT
        bkl.DEFAULT_TIMEOUT = self.LOCK_TIMEOUT
        try:
            with ppg._active_lock():
                acquired = True
        finally:
            bkl.DEFAULT_TIMEOUT = saved
        self.assertTrue(acquired, "the uncontended acquire failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)

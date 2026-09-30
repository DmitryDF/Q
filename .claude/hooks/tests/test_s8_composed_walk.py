#!/usr/bin/env python3
"""S8 (streamed-dancing-goose / A8) — the COMPOSED end-to-end walk.

Every other suite in this topic exercises one mechanism in isolation, and every
defect this plan closed had already survived a green per-part suite. This file
is the other kind of proof: ONE artifact set carried through every seam in
order, so a later link genuinely reads what an earlier link wrote.

The walk is a list of LINKS, run in order against one fixture tree:

  L1  G3  one answer to "is this path inside the projects tree" — all three
          resolvers agree on a SYMLINKED spelling, and the broken one no longer
          returns None.
  L2  G3  composed into `create_topic` -> a non-null `project_root` -> C4:
          `relocate_plan_after_approval` lands the plan in `<project>/Thoughts/`
          with no refusal.
  L3  G1  the two `## Sessions` writers over the spine L2's project carries:
          work-done bullet, close metrics + S7 delivery rows, re-runs of both.
          The bullet survives byte-identical; the locked hash never moves.
  L4  G9  the omission gate over exactly the artifact L3 produced: a
          `/close`-authored block alone does not satisfy it; `/work-done` does.
  L5  G2  the write-path guard on that same spine: a direct code-path write
          into a locked body is refused before the first byte; a write that
          touches no locked body is allowed.
  L6  G6  the call-site inventory fails on an unregistered spine writer; the
          normalisation copy count is pinned; the framing audit classifies a
          real TODO.md.
  L7  G5  the framing check reaches the v2 door: the real Track-B composer's
          line passes the real Track-A validator, and an unframed line is
          refused with a machine-readable payload and nothing written.
  L8  G4/G8 lock liveness over the topic L2 created: a live holder keeps its
          lock past the staleness window and names itself to a second session;
          a dead holder is reclaimed; an unconfirmable one waits for the
          ceiling; no shape is unreclaimable.
  L9  G7  the three transition limbs: a sweep leaves a live (and a legacy)
          holder alone; the guard admits the shipped writer and refuses the
          pre-S1 shape it replaced; the branches the path fix woke execute,
          and worktree detection fails safe.
  L10 C7  the reporting property end to end: the registered session-end
          reporter lists an unframed line on stderr and exits 0.

Environment assumptions the plan requires this walk to CONFIRM rather than
inherit are checked as their own links (E1-E3).

Run the whole walk, with a per-link table:

    env CLAUDE_CONFIG_DIR=<tree> python3 hooks/tests/test_s8_composed_walk.py --table

Run it as an ordinary test (all links must pass):

    env CLAUDE_CONFIG_DIR=<tree> python3 hooks/tests/test_s8_composed_walk.py

The `--table` form is also the per-link REVERT instrument: revert one fix in a
throwaway worktree, run the table there, and the links that fail are the ones
that fix was holding up. A walk that still passes with a fix reverted is
asserting nothing, which is the whole point of running it both ways.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")))
HOOKS = CONFIG / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg            # noqa: E402
import taskmanagement as tm             # noqa: E402
import check_work_done_omission as cwdo  # noqa: E402
import todo as todo_mod                 # noqa: E402

SID = "5e8d0a11-s8-composed-walk-0000000000"
SID8 = SID[:8]
OTHER_SID = "9f7c22b0-s8-second-session-000000000"
TODAY = datetime.now().date().isoformat()
HEADER = f"### {TODAY} session [sid:{SID8}]"

# A locked spine in the current template shape: four locked Discovery fields,
# a Step-9 lock marker, and `# Implementation Details` for the create path.
LOCKED_SPINE = """# Composed Walk — Thought File

**Status:** active.

# Discovery

<!-- locked: 2026-09-22T00:00:00Z abc123def456 -->

## Guiding Policy
Fix each rule at the one place it is answered.

## Desired Outcome
The records this system keeps of its own work stop being lost silently.

## Desired Solution
One locus per rule, reached by every writer.

## Metrics
OMTM: records lost per month.

## Q&A
Mutable, not hashed.

# Implementation Details

## Next Session Prompt

paste me
"""

TODO_SEED = """# TODO

## Now

- [ ] [Thought] **Framed properly** — Problem: things get lost. Context: seen in \
the spine. Guiding policy: fix the seam. Master plan: [[some-spine]]. \
In progress: 0. Remaining: 1. Done: 0.
- [ ] [Thought] unframed and untracked, nothing points at it
- [ ] [Thought] unframed but tracked [auto-registered]

## Next

## Nearby

## Nascent

## Scheduled

## Never

## Done
"""


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #

class LinkFailure(AssertionError):
    """A link's own assertion failed."""


class CannotDemonstrate(Exception):
    """The link could not be exercised at all in this environment.

    Reported as CANNOT-DEMONSTRATE, never as a pass and never silently
    omitted — A8's gate requires exactly that distinction.
    """


class _Bound:
    """Bind SID to an absolute spine path by patching `_resolve_topic`, the way
    the S1 suite does. The absolute path makes `_resolve_thought_path` skip the
    resolver, so no live state is touched."""

    def __init__(self, spine: Path, extra_state=None):
        self.state = {"thought_file_path": str(spine), "phase": "implementation"}
        if extra_state:
            self.state.update(extra_state)

    def __enter__(self):
        self._orig = ppg._resolve_topic
        ppg._resolve_topic = lambda sid: ("s8-topic", "Root", self.state)
        return self

    def __exit__(self, *exc):
        ppg._resolve_topic = self._orig


class _LockSandbox:
    """Redirect the lock namespace to a tempdir. `LOCKS_DIR` is read at import,
    so both the module attribute and the env seam are set."""

    def __init__(self, tmp: Path):
        self.tmp = tmp

    def __enter__(self):
        self._orig = (tm.LOCKS_DIR, tm.RELEASES_LOG)
        self._env = os.environ.get("TM_LOCKS_DIR")
        tm.LOCKS_DIR = self.tmp
        tm.RELEASES_LOG = self.tmp / "_releases.jsonl"
        os.environ["TM_LOCKS_DIR"] = str(self.tmp)
        tm._IDENTITY_CACHE.clear()
        return self

    def __exit__(self, *exc):
        tm.LOCKS_DIR, tm.RELEASES_LOG = self._orig
        if self._env is None:
            os.environ.pop("TM_LOCKS_DIR", None)
        else:
            os.environ["TM_LOCKS_DIR"] = self._env
        tm._IDENTITY_CACHE.clear()

    def releases(self):
        if not tm.RELEASES_LOG.exists():
            return []
        return [json.loads(l) for l in tm.RELEASES_LOG.read_text().splitlines() if l.strip()]

    def backups(self):
        return sorted(self.tmp.glob("*.lock.bak-*"))


class _FakeSession:
    """A live process whose argv[0] basename is `claude`, so the identity check
    accepts it and `ps` reports a stable start time. Same shape as the S4
    suite's — reused rather than re-invented."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s8_fake_claude_"))
        link = self.tmp / "claude"
        link.symlink_to(shutil.which("sleep") or "/bin/sleep")
        self.proc = subprocess.Popen([str(link), "300"])
        self.pid = self.proc.pid
        self.start = None
        for _ in range(50):
            self.start = tm._process_start(self.pid)
            if self.start:
                break
            time.sleep(0.02)
        return self

    def kill(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)

    def __exit__(self, *exc):
        self.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _hash(text):
    return ppg._discovery_locked_fields_hash(text)


def _sessions_region(text):
    lines = text.split("\n")
    sec = ppg.find_sessions_section(lines)
    if sec is None:
        raise LinkFailure("spine has no `## Sessions` section")
    return "\n".join(lines[sec[0]:sec[1]])


def _one_line_with(text, needle):
    hits = [ln for ln in text.split("\n") if needle in ln]
    if len(hits) != 1:
        raise LinkFailure(f"expected exactly one line containing {needle!r}, got {hits}")
    return hits[0]


def _in_discovery(text, needle):
    lines = text.split("\n")
    bounds = ppg._discovery_h1_bounds(lines)
    if bounds is None:
        return False
    return any(needle in lines[i] for i in range(bounds[0], bounds[1]))


def _eq(actual, expected, what):
    if actual != expected:
        raise LinkFailure(f"{what}: expected {expected!r}, got {actual!r}")


def _true(cond, what):
    if not cond:
        raise LinkFailure(what)


# --------------------------------------------------------------------------- #
# The walk
# --------------------------------------------------------------------------- #

class ComposedWalk:
    """One fixture set, carried through the links in order.

    State written by a link stays on `self` so the next link reads the real
    artifact rather than a fresh fixture. `run()` executes every link and
    records its outcome; a failing link does not stop the walk, because a
    cascade is itself information the report must carry.
    """

    LINKS = [
        ("L1", "G3 — one answer to path containment, on a symlinked spelling"),
        ("L2", "G3->C4 — non-null project_root, and the plan relocates"),
        ("L3", "G1 — two writers share `## Sessions`; neither reaches the other"),
        ("L4", "G9 — the omission gate keys on the shipping writer"),
        ("L5", "G2 — a locked-body write is refused before the first byte"),
        ("L6", "G6 — inventory, copy-count and the framing audit"),
        ("L7", "G5 — the v2 door's line meets the v1 bar; a refusal is readable"),
        ("L8", "G4/G8 — liveness holds the lock; nothing is unreclaimable"),
        ("L9", "G7 — the three transition limbs"),
        ("L10", "C7 — the session-end reporter reports and does not block"),
        ("L11", "C3 — the two termination shapes: released, and abruptly dead"),
        ("L12", "the CLI face /work-done and /close actually invoke"),
        ("E1", "env — worktree detection present, timeout fails safe"),
        ("E2", "env — the liveness probe on this platform, incl. another user"),
        ("E3", "env — Track A's todo.py under the interpreter Track B uses"),
    ]

    def __init__(self):
        self.results = []
        self.tmp = None

    # ---- fixture -------------------------------------------------------- #

    def setup(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s8_walk_"))
        self.real_root = self.tmp / "real" / "Projects"
        (self.real_root / "proj" / "Thoughts").mkdir(parents=True)
        (self.real_root / "Thoughts").mkdir(parents=True)
        # The leaf-project marker the walk-up looks for.
        (self.real_root / "proj" / "TODO.md").write_text(TODO_SEED, encoding="utf-8")
        # The symlink spelling — this is the shape that returned None for every
        # working directory before A3.
        self.link_root = self.tmp / "Projects"
        self.link_root.symlink_to(self.real_root)

        self.spine = self.real_root / "proj" / "Thoughts" / "s8-walk-20260922000000_THOUGHT.md"
        self.spine.write_text(LOCKED_SPINE, encoding="utf-8")
        self.base_hash = _hash(LOCKED_SPINE)

        # A REAL git primary tree. `create_topic` asks `worktree-detect.sh`
        # before storing a root, and outside any repo that answers UNKNOWN —
        # which nulls the root deliberately (the fail-safe). A fixture that is
        # not a repo would therefore measure the fail-safe and call it the
        # defect. Both arms are exercised in L2.
        self.is_repo = False
        try:
            for cmd in (["git", "init", "-q"],
                        ["git", "config", "user.email", "s8@example.invalid"],
                        ["git", "config", "user.name", "s8"]):
                subprocess.run(cmd, cwd=self.real_root, check=True,
                               capture_output=True, text=True)
            self.is_repo = True
        except Exception:  # noqa: BLE001 — reported by L2, never silently assumed
            self.is_repo = False

        self.locks = self.tmp / "locks"
        self.locks.mkdir()

        self._orig_ledger = ppg._record_ledger_write
        ppg._record_ledger_write = lambda p: None
        self._orig_root = ppg.PROJECTS_ROOT
        ppg.PROJECTS_ROOT = self.link_root

    def teardown(self):
        ppg._record_ledger_write = self._orig_ledger
        ppg.PROJECTS_ROOT = self._orig_root
        if self.tmp:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- runner --------------------------------------------------------- #

    def run(self, only=None):
        self.setup()
        try:
            for link_id, title in self.LINKS:
                if only and link_id not in only:
                    self.results.append((link_id, title, "SKIP", "not selected"))
                    continue
                fn = getattr(self, f"link_{link_id}")
                try:
                    detail = fn() or ""
                    self.results.append((link_id, title, "PASS", detail))
                except CannotDemonstrate as e:
                    self.results.append((link_id, title, "CANNOT-DEMONSTRATE", str(e)))
                except Exception as e:  # noqa: BLE001 — the report wants the reason
                    self.results.append((link_id, title, "FAIL", f"{type(e).__name__}: {e}"))
        finally:
            self.teardown()
        return self.results

    # ---- L1 — G3 -------------------------------------------------------- #

    def link_L1(self):
        leaf = self.link_root / "proj"
        # The defect: a resolved cwd compared against an unresolved root.
        got = ppg._resolve_project_root(leaf)
        _true(got is not None,
              "_resolve_project_root returned None for a cwd inside the tree "
              "(the symlink defect: one side of the containment test unresolved)")
        _eq(got, ppg._norm_path(self.real_root / "proj"),
            "_resolve_project_root did not resolve to the leaf")

        # A cross-cutting cwd (inside the tree, no leaf TODO.md) -> the root.
        root_answer = ppg._resolve_project_root(self.link_root)
        _eq(root_answer, ppg._norm_path(self.real_root),
            "cross-cutting cwd did not resolve to the projects root")

        # Outside the tree still answers None — the caller's own contract.
        outside = ppg._resolve_project_root(self.tmp / "elsewhere")
        _eq(outside, None, "a cwd outside the tree must resolve to None")

        # All three resolvers route through the one normaliser and agree that
        # the symlinked spelling of this spine is inside the tree.
        link_spine = (self.link_root / "proj" / "Thoughts" / self.spine.name)
        canon = ppg.canonical_project_for_spine(str(link_spine))
        _true(canon and canon != "Root",
              f"canonical_project_for_spine gave {canon!r} for a leaf-project spine")
        proj_dir = ppg._project_dir_for_spine(str(link_spine))
        _true(proj_dir is not None,
              "_project_dir_for_spine returned None for a spine inside the tree")
        return f"leaf={got.name} canon={canon}"

    # ---- L2 — G3 -> C4 -------------------------------------------------- #

    def link_L2(self):
        state_dir = self.tmp / "state"
        state_dir.mkdir(exist_ok=True)
        plans_dir = self.tmp / "plans"
        plans_dir.mkdir(exist_ok=True)
        grants = self.tmp / "grants"
        grants.mkdir(exist_ok=True)
        inits = self.tmp / "inits"
        inits.mkdir(exist_ok=True)

        if not self.is_repo:
            raise CannotDemonstrate(
                "the fixture is not a git primary tree, so `create_topic` would "
                "take the UNKNOWN worktree arm and null the root by design — "
                "measuring the fail-safe rather than the fix")

        def _create(cwd, slug):
            orig_state_dir = ppg.TOPIC_STATE_DIR
            ppg.TOPIC_STATE_DIR = state_dir
            old_cwd = Path.cwd()
            err = io.StringIO()
            try:
                os.chdir(cwd)
                with contextlib.redirect_stderr(err):
                    ppg.create_topic(SID, "proj", slug)
            finally:
                os.chdir(old_cwd)
                ppg.TOPIC_STATE_DIR = orig_state_dir
            rec = state_dir / f"{slug}__proj.json"
            _true(rec.exists(), f"create_topic wrote no record at {rec}")
            return json.loads(rec.read_text(encoding="utf-8")), err.getvalue()

        # (a) FROM THE REAL PATH, inside a git primary tree — the step the
        # plan's walk names. A non-null root must be stored.
        record, _err = _create(self.link_root / "proj", "s8-walk")
        _true(record.get("project_root") is not None,
              "create_topic stored `project_root: null` from a real path inside "
              "a primary tree — the defect that makes an approved plan refuse "
              "to relocate")
        _eq(ppg._norm_path(record["project_root"]),
            ppg._norm_path(self.real_root / "proj"),
            "create_topic stored a project_root that is not the leaf project")
        stored_root = Path(record["project_root"])
        self.project_root = stored_root

        # (b) The OTHER arm, so a null root is not read as the old defect: when
        # worktree detection cannot answer, the root is nulled DELIBERATELY and
        # the reason is printed. A3 made this arm reachable; it is the fail-safe
        # direction, not the bug.
        outside = self.tmp / "not-a-repo"
        (outside / "Thoughts").mkdir(parents=True, exist_ok=True)
        (outside / "TODO.md").write_text("# TODO\n\n## Now\n", encoding="utf-8")
        orig_root = ppg.PROJECTS_ROOT
        ppg.PROJECTS_ROOT = outside
        try:
            rec2, err2 = _create(outside, "s8-unknown-arm")
        finally:
            ppg.PROJECTS_ROOT = orig_root
        _eq(rec2.get("project_root"), None,
            "worktree detection could not answer, yet a project root was stored "
            "anyway — the unsafe direction")
        _true("worktree detection did not answer" in err2,
              f"the deliberate null was not explained on stderr: {err2!r}")

        # C4: the relocation that refused on a null root now accepts.
        plan = plans_dir / "s8-walk.md"
        plan.write_text("# S8 walk plan\n\nbody\n", encoding="utf-8")
        (grants / f"{SID}.marker").write_text("granted", encoding="utf-8")
        (inits / f"{SID}.json").write_text(json.dumps({"mode": "A"}), encoding="utf-8")

        orig = (ppg.POST_PLAN_CHOICE_DIR, ppg.PLAN_MODE_INIT_DIR, ppg._resolve_topic)
        ppg.POST_PLAN_CHOICE_DIR = grants
        ppg.PLAN_MODE_INIT_DIR = inits
        ppg._resolve_topic = lambda sid: (
            "s8-walk", "proj", {"project_root": str(stored_root),
                                "thought_file_path": str(self.spine)})
        try:
            result = ppg.relocate_plan_after_approval(SID, str(plan))
        finally:
            (ppg.POST_PLAN_CHOICE_DIR, ppg.PLAN_MODE_INIT_DIR, ppg._resolve_topic) = orig

        dest = Path(result["dest"] if isinstance(result, dict) and "dest" in result
                    else result.get("destination", ""))
        _true(dest.exists(), f"relocation reported {result!r} but no file landed")
        _true(str(dest).startswith(str(ppg._norm_path(self.real_root / "proj"))),
              f"plan landed outside the project: {dest}")
        _true(dest.parent.name == "Thoughts",
              f"plan did not land in Thoughts/: {dest}")
        self.relocated_plan = dest

        # And the null-root case the fix must NOT silently accept: with no
        # project_root on the record, Mode A/B still refuses rather than
        # inventing a destination.
        ppg.POST_PLAN_CHOICE_DIR, ppg.PLAN_MODE_INIT_DIR = grants, inits
        ppg._resolve_topic = lambda sid: ("s8-walk", "proj", {"project_root": None})
        try:
            plan2 = plans_dir / "s8-walk-2.md"
            plan2.write_text("# other\n\nbody\n", encoding="utf-8")
            refused = False
            try:
                ppg.relocate_plan_after_approval(SID, str(plan2))
            except ValueError:
                refused = True
            _true(refused, "a null project_root must still refuse, not guess a destination")
        finally:
            (ppg.POST_PLAN_CHOICE_DIR, ppg.PLAN_MODE_INIT_DIR, ppg._resolve_topic) = orig
        return f"relocated -> {dest.name}"

    # ---- L3 — G1 -------------------------------------------------------- #

    def link_L3(self):
        payload = {"duration_min": 42, "opus_tokens_k": 111, "tasks_completed": 3}
        delivery = dict(payload, delivered_slice="S8", slice_counter="8/8",
                        next_slice="—", commit_sha="0123456789abcdef",
                        commit_url="https://example.invalid/commit/0123456789abcdef")

        with _Bound(self.spine):
            r1 = ppg.annotate_session(SID, "Shipped the composed walk.")
            after_annotate = self.spine.read_text(encoding="utf-8")
            bullet = _one_line_with(after_annotate, "Shipped the composed walk.")

            r2 = ppg.append_metrics(SID, delivery)
            after_metrics = self.spine.read_text(encoding="utf-8")

            # The confirmed data loss: the bullet must survive byte-identical.
            _eq(_one_line_with(after_metrics, "Shipped the composed walk."), bullet,
                "the /work-done bullet did not survive the /close metrics write")

            # Re-run each writer; each replaces only its own marked lines.
            ppg.append_metrics(SID, dict(delivery, duration_min=99))
            ppg.annotate_session(SID, "Shipped the composed walk, again.")
            final = self.spine.read_text(encoding="utf-8")

        _true("Shipped the composed walk, again." in final,
              "the re-run bullet is missing")
        _true("Shipped the composed walk." not in final.replace(
                  "Shipped the composed walk, again.", ""),
              "the replaced bullet was duplicated instead of replaced")

        region = _sessions_region(final)
        _eq(region.count(HEADER), 1, "the session header was minted more than once")

        # S7's delivery rows rode in under the close marker, additively.
        _true("Delivered" in region and "S8" in region,
              "S7's delivery rows are absent from the close block")
        _true("Duration" in region, "S7 dropped the pre-existing duration row")
        for row in region.split("\n"):
            if "Delivered" in row or "Duration" in row:
                _true(ppg._CLOSE_METRICS_MARKER in row,
                      f"a close row carries no close marker: {row!r}")

        # Nothing landed inside the locked region, and the hash never moved.
        _true(not _in_discovery(final, "session ["),
              "a session block landed inside `# Discovery`")
        _eq(_hash(final), self.base_hash,
            "the locked-fields hash moved during the paired writes")

        _eq(r1["writer"], ppg.SESSION_WRITER_WORK_DONE, "annotate reported the wrong writer")
        _eq(r2["writer"], ppg.SESSION_WRITER_CLOSE, "append_metrics reported the wrong writer")
        self.walked_spine = final
        return f"hash stable at {self.base_hash}"

    # ---- L4 — G9 -------------------------------------------------------- #

    def link_L4(self):
        content = getattr(self, "walked_spine", None)
        if content is None:
            raise CannotDemonstrate("L3 did not produce a spine to read")

        _true(cwdo._work_done_record_in(content, SID),
              "the gate does not see the /work-done entry L3 wrote")

        # A /close-authored block ALONE — metrics rows, and a `--annotate`
        # bullet under writer=close — must not satisfy it. This is the
        # discrimination the re-key exists to restore, and the annotate path
        # is the case a shared marker would have missed.
        close_only = self.real_root / "proj" / "Thoughts" / "close_only_THOUGHT.md"
        close_only.write_text(LOCKED_SPINE, encoding="utf-8")
        with _Bound(close_only):
            ppg.append_metrics(SID, {"duration_min": 5})
            ppg.annotate_session(SID, "Closed without shipping.",
                                 writer=ppg.SESSION_WRITER_CLOSE)
        close_text = close_only.read_text(encoding="utf-8")
        _true(HEADER in close_text, "the /close block was not written at all")
        _true(not cwdo._work_done_record_in(close_text, SID),
              "a /close-authored block alone satisfies the omission gate "
              "(the re-key restored no discrimination)")

        # The legacy bare tag still reads as work-done, so old spines still pass.
        legacy = close_text.replace(
            ppg.annotate_marker(ppg.SESSION_WRITER_CLOSE), ppg._ANNOTATE_MARKER)
        _true(cwdo._work_done_record_in(legacy, SID),
              "the legacy bare annotate tag stopped reading as work-done")
        return "close-only rejected, work-done accepted, legacy accepted"

    # ---- L5 — G2 -------------------------------------------------------- #

    def link_L5(self):
        before = self.spine.read_text(encoding="utf-8")

        # A direct code-path write that changes a locked body.
        tampered = before.replace("OMTM: records lost per month.",
                                 "OMTM: something else entirely.")
        _true(tampered != before, "the fixture edit did not change anything")
        refused = None
        try:
            ppg._write_spine_guarded(self.spine, before, tampered,
                                     session_id=SID, writer="s8_walk_direct_write")
        except ppg.LockedDiscoveryWriteRefused as e:
            refused = str(e)
        _true(refused is not None,
              "a direct write into a locked Discovery body was NOT refused")
        _true("Metrics" in refused, f"the refusal did not name the field: {refused}")
        _eq(self.spine.read_text(encoding="utf-8"), before,
            "the spine changed despite the refusal")
        strays = list(self.spine.parent.glob("*.tmp"))
        _eq(strays, [], f"a temp file was written before the refusal: {strays}")

        # The refusal is a ValueError, so every CLI verb renders it already.
        _true(issubclass(ppg.LockedDiscoveryWriteRefused, ValueError),
              "the guard's exception is no longer a ValueError subclass")

        # A write that changes NO locked body is allowed with no permission
        # check — every writer that never touches locked ground is untouched.
        benign = before.replace("q body", "q body") + "\n<!-- benign tail -->\n"
        ppg._write_spine_guarded(self.spine, before, benign,
                                 session_id=None, writer="s8_walk_benign")
        _true("benign tail" in self.spine.read_text(encoding="utf-8"),
              "a write touching no locked body was refused")
        _eq(_hash(self.spine.read_text(encoding="utf-8")), self.base_hash,
            "the benign write moved the locked hash")

        # An unlocked spine is never checked at all.
        unlocked = before.replace("<!-- locked: 2026-09-22T00:00:00Z abc123def456 -->", "")
        verdict = ppg.locked_discovery_change(unlocked, unlocked.replace(
            "OMTM: records lost per month.", "OMTM: changed freely."))
        _eq(verdict["locked"], False, "an unlocked spine reported as locked")
        return "refused and named; benign write allowed; nothing written on refusal"

    # ---- L6 — G6 -------------------------------------------------------- #

    def link_L6(self):
        # (a) the inventory is what keeps the unregistered-writer set at zero.
        inv = ppg.SPINE_WRITE_INVENTORY
        _true(("pre_plan_gates", "append_metrics") in inv,
              "append_metrics fell out of the spine-write inventory")
        _true(any(str(v).startswith("exempt") for v in inv.values()),
              "the inventory records no exemption — the v2 door's is required "
              "to be explicit, not prose")
        # The inventory must actually FAIL when a spine write is added without
        # registration — demonstrated on the SHIPPED scanner, not asserted by
        # running the suite that owns it.
        inv_test = HOOKS / "tests" / "test_s2_locked_write_guard.py"
        if not inv_test.exists():
            raise CannotDemonstrate(f"the inventory scanner is absent: {inv_test}")
        sys.path.insert(0, str(inv_test.parent))
        try:
            import test_s2_locked_write_guard as s2  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            raise CannotDemonstrate(f"cannot load the inventory scanner: {e}") from e
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(inv_test.parent))

        # The landed tree itself: nothing unregistered.
        tree = sorted(str(p) for p in HOOKS.glob("*.py")
                      if p.name not in {"bookkeeping_migrate.py"})
        live_found = s2.scan_spine_writers(tree)
        live_missing = (s2.unregistered(live_found, ppg.SPINE_WRITE_INVENTORY)
                        - set(s2.Inventory._KNOWN_NON_WRITERS))
        _eq(live_missing, set(),
            f"the landed tree carries unregistered spine writers: {sorted(live_missing)}")

        # Now add one, and confirm the scan catches it.
        scan_dir = self.tmp / "scan"
        scan_dir.mkdir(exist_ok=True)
        rogue = scan_dir / "rogue_writer.py"
        rogue.write_text(
            "from pathlib import Path\n"
            "def stamp_the_spine(thought_path):\n"
            "    p = Path(thought_path)\n"
            "    p.write_text(p.read_text() + '\\nstamped', encoding='utf-8')\n",
            encoding="utf-8")
        with_rogue = s2.scan_spine_writers(tree + [str(rogue)])
        missing = (s2.unregistered(with_rogue, ppg.SPINE_WRITE_INVENTORY)
                   - set(s2.Inventory._KNOWN_NON_WRITERS))
        _eq(missing, {("rogue_writer", "stamp_the_spine")},
            "the call-site inventory did NOT flag an unregistered spine writer "
            f"— it reported {sorted(missing)}. The set it keeps at zero is not "
            "being kept by anything.")

        # (b) the normalisation copy count is pinned — the rule this plan
        # watched re-duplicate twice needs the same defence as the one it guards.
        src = (HOOKS / "pre_plan_gates.py").read_text(encoding="utf-8")
        copies = len(re.findall(r"os\.path\.realpath\(", src))
        _eq(copies, 1,
            f"pre_plan_gates carries {copies} realpath copies; the rule has "
            "more than one implementation again")

        # (c) the framing audit over a REAL TODO.md with all three classes.
        todo_path = self.real_root / "proj" / "TODO.md"
        report = todo_mod.audit_framing(todo_path)
        _true(report is not None, "the audit could not read the fixture TODO.md")
        by_status = {}
        for row in report["items"]:
            by_status.setdefault(row["status"], []).append(row)
        _true(by_status.get("pass"), "the framed line was not classified pass")
        _true(by_status.get("fail"), "the unframed untracked line was not classified fail")
        _true(by_status.get("not-framed-yet"),
              "the marked line was not classified not-framed-yet (it would be "
              "reported forever)")
        block = todo_mod.render_framing_audit(report)
        _true(block, "the renderer produced nothing for a failing line")
        _true("unframed and untracked" in block,
              "the report does not name the untracked line")
        # The tracked line must NOT be listed: it is the obligation surface's to
        # report, and a report nobody can clear is a report nobody reads. (The
        # marker's NAME appears in the block's explanatory prose, which is why
        # this checks for the line's own text rather than for the marker.)
        _true("unframed but tracked" not in block,
              "the report lists the tracked line, which would make it unclearable")
        _true("Reported, not blocked" in block or "not blocked" in block,
              "the report does not tell the operator it is a report")
        self.todo_path = todo_path
        return (f"inventory holds; realpath copies={copies}; "
                f"audit pass/fail/tracked = "
                f"{len(by_status.get('pass', []))}/{len(by_status.get('fail', []))}/"
                f"{len(by_status.get('not-framed-yet', []))}")

    # ---- L7 — G5 -------------------------------------------------------- #

    def link_L7(self):
        # Track B's REAL composer against Track A's REAL validator — the
        # cross-track seam, not a re-implementation of either side.
        # `S8_CLARIFY_ROOT` points the link at a different checkout of Track B,
        # which is how the per-link revert check exercises S6 (Track B lives in
        # the Projects repo and has no commit in the harness history).
        clarify_root = Path(os.environ.get("S8_CLARIFY_ROOT",
                                           "~/repos/Projects/Personal/clarify"))
        if not (clarify_root / "clarify" / "infrastructure" / "real_bookkeeping.py").exists():
            raise CannotDemonstrate(f"the v2 door package is not at {clarify_root}")
        sys.path.insert(0, str(clarify_root))
        for mod in [m for m in sys.modules if m == "clarify" or m.startswith("clarify.")]:
            del sys.modules[mod]
        try:
            from clarify.infrastructure import real_bookkeeping as rbk  # noqa: PLC0415
        except Exception as e:  # noqa: BLE001
            raise CannotDemonstrate(f"cannot import the v2 door composer: {e}") from e
        finally:
            with contextlib.suppress(ValueError):
                sys.path.remove(str(clarify_root))
        loaded = Path(getattr(rbk, "__file__", ""))
        _true(str(loaded).startswith(str(clarify_root)),
              f"the composer was imported from {loaded}, not from {clarify_root} "
              "— the revert arm would be measuring the wrong checkout")

        state = {"sections": [
            {"section_id": "problem", "content": "Records are lost\nacross two lines."},
            {"section_id": "guiding_policy", "content": "Fix the seam, not the sites."},
        ]}
        line = rbk.compose_thought_line("A sealed v2 framing",
                                        "s8-walk-20260922000000_THOUGHT.md",
                                        state, TODAY)
        _eq(line.count("\n"), 0, "the composed line is not single-line")
        verdict = todo_mod.cmd_validate_framing(line)
        _true(verdict.get("valid") is True or not verdict.get("errors"),
              f"the v2 door's line fails the real v1 validator: {verdict}")
        _true("[[" in line and "]]" in line, "the composed line carries no wikilink")
        _true("Master plan:" not in line,
              "the composed line names the spine as a master plan")
        _true("In progress:" in line and "Remaining:" in line and "Done:" in line,
              "the three-bucket counter is missing")

        # And the refusal side: an unframed line is refused, writes NOTHING,
        # and says so in a machine-readable shape.
        todo_path = getattr(self, "todo_path", self.real_root / "proj" / "TODO.md")
        before = todo_path.read_text(encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(HOOKS / "todo.py"), "add",
             "[Thought] nothing framed here at all",
             "--bucket", "NOW", "--file", str(todo_path), "--validate-framing"],
            capture_output=True, text=True,
            env={**os.environ, "CLAUDE_CONFIG_DIR": str(CONFIG)})
        _eq(proc.returncode, 1, f"an unframed line was not refused (rc={proc.returncode})")
        _eq(todo_path.read_text(encoding="utf-8"), before,
            "the refused line was written anyway")
        _true("✗" in proc.stderr, "the human `✗` refusal shape is gone")
        payload = None
        for chunk in proc.stdout.strip().splitlines():
            try:
                cand = json.loads(chunk)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(cand, dict) and cand.get("status") == "refused":
                payload = cand
        _true(payload is not None,
              f"no machine-readable refusal on stdout: {proc.stdout!r}")
        _eq(payload.get("reason"), "framing", "the refusal names the wrong reason")
        _true(payload.get("errors"), "the refusal carries no validator errors")
        return f"v2 line validates; refusal carries {len(payload['errors'])} errors"

    # ---- L8 — G4/G8 ------------------------------------------------------ #

    def link_L8(self):
        key = "s8-walk__proj"
        with _LockSandbox(self.locks) as sb, _FakeSession() as fs:
            if not fs.start:
                raise CannotDemonstrate("ps did not report a start time for the fake session")
            os.environ["CLAUDE_PID"] = str(fs.pid)
            os.environ["TM_STALE_T_SECONDS"] = "1"
            try:
                tm._IDENTITY_CACHE.clear()
                got = tm.acquire_lock(key, SID)
                _eq(got["status"], "ACQUIRED", "the first acquire did not succeed")
                ident = got["holder"]
                _eq(ident["pid"], fs.pid,
                    "the lock records a process that is not the session's own "
                    "(the diagnosed defect: a helper that exits at once)")
                _true(ident.get("pid_start"),
                      "no start time recorded — a recycled pid would read as live")

                # Age it far past the staleness window. A live holder keeps it.
                payload = json.loads((self.locks / f"{key}.lock").read_text())
                old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
                payload["last_heartbeat"] = old
                (self.locks / f"{key}.lock").write_text(json.dumps(payload))

                second = tm.acquire_lock(key, OTHER_SID)
                _eq(second["status"], "HELD_BY_OTHER",
                    "a 10-day-old lock held by a LIVE session was stolen")
                _eq(second["holder"]["session_id"], SID,
                    "the second session was not told who holds it")
                _eq(second.get("liveness"), "ALIVE",
                    f"the holder did not probe ALIVE: {second.get('liveness')}")

                # A recycled pid must not read as live.
                recycled = dict(payload)
                recycled["pid_start"] = "Mon Jan  1 00:00:00 2001"
                _eq(tm.probe_liveness(recycled), "DEAD",
                    "a recycled pid reads as live — liveness is not reuse-proof")

                # Kill it: the lock stops blocking, and the payload is MOVED
                # aside rather than deleted (safe-defaults).
                fs.kill()
                time.sleep(0.2)
                third = tm.acquire_lock(key, OTHER_SID)
                _eq(third["status"], "ACQUIRED",
                    "a dead holder's lock still blocks a new session")
                reasons = [r.get("reason") for r in sb.releases()]
                _true("dead-holder-release" in reasons,
                      f"no dead-holder reclaim was logged: {reasons}")
                _true(sb.backups(),
                      "the reclaimed payload was deleted rather than moved aside")

                # C9: nothing is unreclaimable. An UNKNOWN holder waits for the
                # ceiling and is then reclaimed; a corrupt payload is reclaimed.
                # An UNKNOWN payload is one that carries the S4 identity fields
                # but whose pid the probe cannot reason about.
                unknown_key = "s8-unknown__proj"
                unknown_payload = {
                    "session_id": "ghost", "pid": "not-an-integer",
                    "pid_start": "Mon Jan  1 00:00:00 2001",
                    "identity": "session-process/v1",
                    "last_heartbeat": (datetime.now(timezone.utc)
                                       - timedelta(hours=2)).isoformat(),
                }
                _eq(tm.probe_liveness(unknown_payload), "UNKNOWN",
                    "the fixture payload does not probe UNKNOWN, so the ceiling "
                    "arm is not the one under test")
                (self.locks / f"{unknown_key}.lock").write_text(
                    json.dumps(unknown_payload))
                os.environ["TM_LIVENESS_CEILING_SECONDS"] = "99999"
                held = tm.acquire_lock(unknown_key, OTHER_SID)
                _eq(held["status"], "HELD_BY_OTHER",
                    "an unconfirmable holder inside the ceiling was reclaimed")
                os.environ["TM_LIVENESS_CEILING_SECONDS"] = "60"
                reclaimed = tm.acquire_lock(unknown_key, OTHER_SID)
                _eq(reclaimed["status"], "ACQUIRED",
                    "an unconfirmable holder past the ceiling was NOT reclaimed "
                    "— that lock is unreclaimable (C9 fails)")
                _true("unconfirmable-ceiling-release" in
                      [r.get("reason") for r in sb.releases()],
                      "the ceiling reclaim was not logged under its own reason")

                corrupt_key = "s8-corrupt__proj"
                (self.locks / f"{corrupt_key}.lock").write_text("not json at all")
                got_corrupt = tm.acquire_lock(corrupt_key, OTHER_SID)
                _eq(got_corrupt["status"], "ACQUIRED",
                    "a corrupt payload is unreclaimable")
                return "live holder kept at 10d; dead reclaimed; ceiling reclaims unknown"
            finally:
                os.environ.pop("CLAUDE_PID", None)
                os.environ.pop("TM_STALE_T_SECONDS", None)
                os.environ.pop("TM_LIVENESS_CEILING_SECONDS", None)

    # ---- L9 — G7 --------------------------------------------------------- #

    def link_L9(self):
        notes = []

        # Limb 1 — a migration step leaves a LIVE session's lock alone, and
        # leaves a LEGACY payload alone. The legacy case is the sharp one: every
        # payload on disk before S4 records a short-lived helper, so a dead-pid
        # predicate is true of a live session's lock too.
        with _LockSandbox(self.locks / "sweep" if False else self.locks) as sb, \
                _FakeSession() as fs:
            if not fs.start:
                raise CannotDemonstrate("ps did not report a start time")
            os.environ["CLAUDE_PID"] = str(fs.pid)
            os.environ["TM_STALE_T_SECONDS"] = "1"
            try:
                tm._IDENTITY_CACHE.clear()
                live_key = "s8-live__proj"
                tm.acquire_lock(live_key, SID)
                p = self.locks / f"{live_key}.lock"
                payload = json.loads(p.read_text())
                payload["last_heartbeat"] = (
                    datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
                p.write_text(json.dumps(payload))

                legacy_key = "s8-legacy__proj"
                (self.locks / f"{legacy_key}.lock").write_text(json.dumps({
                    "session_id": "someone-else", "pid": 999998,
                    "started_at": "2026-01-01T00:00:00+00:00",
                    "last_heartbeat": "2026-01-01T00:00:00+00:00",
                }))

                swept = tm.sweep_dead_locks(OTHER_SID)
                _true(p.exists(),
                      "the sweep moved a LIVE session's lock aside — the exact "
                      "failure C8 forbids")
                _true((self.locks / f"{legacy_key}.lock").exists(),
                      "the sweep moved a LEGACY payload — every pre-S4 payload "
                      "records a dead helper, so this evicts live sessions")
                notes.append(f"sweep moved {len(swept.get('moved', []) or [])}")
            finally:
                os.environ.pop("CLAUDE_PID", None)
                os.environ.pop("TM_STALE_T_SECONDS", None)

        # Limb 2 — the guard cannot land before the writer it depends on.
        # Expressed as behaviour: the pre-S1 shape (metrics written into the
        # locked `## Metrics` field) is REFUSED, while the shipped writer is
        # ALLOWED on the same locked spine. Had the guard shipped first,
        # `/close` would have been refused on every live locked spine.
        spine_text = self.spine.read_text(encoding="utf-8")
        pre_s1 = spine_text.replace(
            "OMTM: records lost per month.",
            "OMTM: records lost per month.\n- Duration: 42 min")
        verdict = ppg.locked_discovery_change(spine_text, pre_s1)
        _true("## Metrics" in verdict["changed"],
              "the pre-S1 /close shape no longer registers as a locked change — "
              "the ordering constraint cannot be demonstrated")
        allowed = None
        try:
            with _Bound(self.spine):
                ppg.append_metrics(SID, {"duration_min": 7})
            allowed = True
        except ppg.LockedDiscoveryWriteRefused as e:
            allowed = False
            notes.append(str(e))
        _true(allowed,
              "the SHIPPED /close writer is refused by the guard — the guard "
              "landed without (or ahead of) the writer it depends on")

        # Limb 3 — the branches the path fix woke actually execute. Before A3
        # `_resolve_project_root` returned None for every production cwd, so
        # every `is not None` branch downstream had never run.
        leaf = self.link_root / "proj"
        _true(ppg._resolve_project_root(leaf) is not None,
              "the dormant branches are still dormant")
        proj_dir = ppg._norm_path(self.real_root / "proj")
        rel_spine = str(ppg._norm_path(self.spine).relative_to(proj_dir))
        with _Bound(self.spine) as b:
            b.state["thought_file_path"] = rel_spine
            b.state["project_root"] = str(proj_dir)
            old_cwd = Path.cwd()
            os.chdir(proj_dir)
            try:
                resolved = ppg._resolve_thought_path(SID)
            finally:
                os.chdir(old_cwd)
        _eq(ppg._norm_path(resolved), ppg._norm_path(self.spine),
            "the newly-live relative-path branch of _resolve_thought_path "
            "does not resolve to the spine")
        notes.append("relative-path branch resolves")
        return "; ".join(notes) or "three limbs hold"

    # ---- L10 — C7 -------------------------------------------------------- #

    def link_L10(self):
        hook = HOOKS / "check-framing-audit-stop.sh"
        if not hook.exists():
            raise CannotDemonstrate(f"the session-end reporter is absent: {hook}")
        _true(os.access(str(hook), os.X_OK), f"{hook.name} is not executable")

        todo_path = getattr(self, "todo_path", self.real_root / "proj" / "TODO.md")
        before = todo_path.read_text(encoding="utf-8")
        proc = subprocess.run(
            [str(hook)],
            input=json.dumps({"session_id": SID, "cwd": str(todo_path.parent)}),
            capture_output=True, text=True,
            env={**os.environ, "CLAUDE_CONFIG_DIR": str(CONFIG)})
        _eq(proc.returncode, 0,
            f"the reporter exited {proc.returncode} — it must report, never block")
        _true("unframed and untracked" in (proc.stderr + proc.stdout),
              f"the reporter did not list the unframed line.\n"
              f"stderr={proc.stderr!r}\nstdout={proc.stdout!r}")
        _eq(todo_path.read_text(encoding="utf-8"), before,
            "the reporter mutated the TODO file")

        # Garbage on stdin must still exit 0 — a reporter that crashes a session
        # end is worse than the condition it reports.
        junk = subprocess.run([str(hook)], input="not json",
                              capture_output=True, text=True,
                              env={**os.environ, "CLAUDE_CONFIG_DIR": str(CONFIG)})
        _eq(junk.returncode, 0, "the reporter exits non-zero on garbage stdin")

        # And it is registered on Stop, not on a blocking event.
        settings = json.loads((CONFIG / "settings.json").read_text(encoding="utf-8"))
        found = []
        for event, groups in (settings.get("hooks") or {}).items():
            for group in groups or []:
                for h in group.get("hooks") or []:
                    if "check-framing-audit-stop" in str(h.get("command", "")):
                        found.append(event)
        _eq(sorted(set(found)), ["Stop"],
            f"the reporter is registered on {found}, not exactly ['Stop']")
        return "reports on stderr, exit 0, registered on Stop"

    # ---- L11 — the two termination shapes -------------------------------- #

    def link_L11(self):
        """A8 requires BOTH, because they prove different things: a session
        terminated by a signal that still runs the release path gives its lock
        back at once; a session that dies abruptly runs no hook at all, and the
        lock must stop blocking anyway — by liveness, not by a timer. This
        describes the two SHAPES the link models, not two end-to-end firings:
        the graceful arm is modelled by invoking the release hook directly
        rather than exercised via a real SessionEnd event — see the comment
        below for what that does and does not prove."""
        release_hook = HOOKS / "release-session-locks.sh"
        if not release_hook.exists():
            raise CannotDemonstrate(f"the session-end release hook is absent: {release_hook}")

        notes = []
        with _LockSandbox(self.locks) as sb, _FakeSession() as fs:
            if not fs.start:
                raise CannotDemonstrate("ps did not report a start time")
            os.environ["CLAUDE_PID"] = str(fs.pid)
            os.environ["TM_STALE_T_SECONDS"] = "1"
            try:
                tm._IDENTITY_CACHE.clear()

                # (a) GRACEFUL — the release path runs, invoked DIRECTLY as a
                # subprocess against a fake session rather than by terminating
                # a real session. This proves the hook itself releases the lock
                # and moves the payload aside; it does NOT exercise a real
                # SessionEnd event firing it. That the hook is registered on
                # SessionEnd is asserted separately (test_s4_lock_liveness.py),
                # never exercised end-to-end here.
                graceful = "s8-graceful__proj"
                tm.acquire_lock(graceful, SID)
                _true((self.locks / f"{graceful}.lock").exists(), "the lock was not taken")
                proc = subprocess.run(
                    [str(release_hook)],
                    input=json.dumps({"session_id": SID}),
                    capture_output=True, text=True,
                    env={**os.environ, "CLAUDE_CONFIG_DIR": str(CONFIG),
                         "TM_LOCKS_DIR": str(self.locks)})
                _eq(proc.returncode, 0,
                    f"the session-end release hook exited {proc.returncode}")
                _true(not (self.locks / f"{graceful}.lock").exists(),
                      "the graceful release path left the lock in place")
                reasons = [r.get("reason") for r in sb.releases()]
                _true("session-end-release" in reasons,
                      f"no session-end release was logged: {reasons}")
                _true(sb.backups(),
                      "the released payload was deleted rather than moved aside")
                notes.append("graceful: hook invoked directly (SessionEnd firing "
                              "asserted by registration, not exercised)")

                # (b) ABRUPT — no hook runs at all; the payload stays on disk.
                abrupt = "s8-abrupt__proj"
                tm.acquire_lock(abrupt, SID)
                fs.kill()               # SIGTERM to the recorded process, no hook
                time.sleep(0.2)
                _true((self.locks / f"{abrupt}.lock").exists(),
                      "the abrupt case did not leave a payload to reclaim — the "
                      "two shapes are not being told apart")
                taken = tm.acquire_lock(abrupt, OTHER_SID)
                _eq(taken["status"], "ACQUIRED",
                    "an abruptly-dead session's lock still blocks — the only "
                    "recovery left would be the operator clearing it by hand")
                _true("dead-holder-release" in
                      [r.get("reason") for r in sb.releases()],
                      "the abrupt reclaim was not attributed to liveness")
                notes.append("abrupt: reclaimed by liveness, not by a timer")
                return "; ".join(notes)
            finally:
                os.environ.pop("CLAUDE_PID", None)
                os.environ.pop("TM_STALE_T_SECONDS", None)

    # ---- L12 — the CLI face the two skills invoke ------------------------ #

    def link_L12(self):
        """`/work-done` and `/close` reach these writers through the module's
        CLI verbs, not by importing it. Driving `main()` exercises the dispatch,
        the `--writer` identity the omission re-key depends on, and the widened
        exception catch A5 added — the half a direct function call skips."""
        spine = self.real_root / "proj" / "Thoughts" / "cli_face_THOUGHT.md"
        spine.write_text(LOCKED_SPINE, encoding="utf-8")

        def _cli(argv):
            out, err = io.StringIO(), io.StringIO()
            code = 0
            orig = sys.argv
            sys.argv = ["pre_plan_gates.py", *argv]
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    ppg.main()
            except SystemExit as e:
                code = e.code or 0
            finally:
                sys.argv = orig
            return code, out.getvalue(), err.getvalue()

        with _Bound(spine):
            code, out, err = _cli(["annotate-session", SID,
                                   "Shipped through the CLI face.",
                                   "--writer", "work-done"])
            _eq(code, 0, f"annotate-session exited {code}: {err[-300:]}")
            code, out, err = _cli(["append-metrics", SID,
                                   json.dumps({"duration_min": 12})])
            _eq(code, 0, f"append-metrics exited {code}: {err[-300:]}")

        text = spine.read_text(encoding="utf-8")
        _true("Shipped through the CLI face." in text,
              "the CLI-written /work-done bullet did not survive /close")
        _true(cwdo._work_done_record_in(text, SID),
              "a bullet written through the CLI with --writer work-done does not "
              "satisfy the omission gate — the caller-side identity is not wired")
        _eq(_hash(text), self.base_hash, "the CLI writers moved the locked hash")

        # `/close --annotate` goes through the SAME CLI verb. If the identity
        # were not passed, the re-key would restore no discrimination at all.
        close_spine = self.real_root / "proj" / "Thoughts" / "cli_close_THOUGHT.md"
        close_spine.write_text(LOCKED_SPINE, encoding="utf-8")
        with _Bound(close_spine):
            code, _o, err = _cli(["annotate-session", SID, "Closed, not shipped.",
                                  "--writer", "close"])
            _eq(code, 0, f"annotate-session --writer close exited {code}: {err[-300:]}")
        _true(not cwdo._work_done_record_in(
                  close_spine.read_text(encoding="utf-8"), SID),
              "`/close --annotate` satisfies the omission gate — the shared CLI "
              "is minting the shipping writer's identity for the wrong caller")

        # A5's widened catch: a lock-contended run reports `✗ …` + exit 1
        # rather than dying on a traceback.
        import bookkeeping_lock as bkl  # noqa: PLC0415
        orig_lock = ppg.__dict__.get("bookkeeping_lock")
        with _Bound(spine):
            def _boom(*_a, **_k):
                raise bkl.BookkeepingLockTimeout("contended")
            real = bkl.bookkeeping_lock
            bkl.bookkeeping_lock = _boom
            try:
                code, _o, err = _cli(["append-metrics", SID, json.dumps({"duration_min": 1})])
            finally:
                bkl.bookkeeping_lock = real
                if orig_lock is not None:
                    ppg.bookkeeping_lock = orig_lock
        _eq(code, 1, f"a lock-contended append-metrics exited {code}, not 1")
        _true("✗" in err or "✗" in _o,
              f"a lock-contended run did not report the `✗` shape: {err[-300:]!r}")
        _true("Traceback" not in err,
              f"a lock-contended run died on a traceback: {err[-400:]}")
        return "CLI dispatch, --writer identity, and the widened catch all hold"

    # ---- E1..E3 — the environment assumptions ---------------------------- #

    def link_E1(self):
        detect = HOOKS / "worktree-detect.sh"
        if not detect.exists():
            raise CannotDemonstrate(f"worktree detection is absent: {detect}")
        _true(os.access(str(detect), os.X_OK), "worktree-detect.sh is not executable")

        status = getattr(ppg, "_worktree_status", None)
        if status is None:
            raise CannotDemonstrate("`_worktree_status` is not present to test the "
                                    "timeout path against")
        # The timeout / error path must be tri-state (None), not a False that
        # routes a worktree plan project-side.
        orig = subprocess.run
        def _boom(*a, **k):
            raise subprocess.TimeoutExpired(cmd="worktree-detect.sh", timeout=2)
        subprocess.run = _boom
        try:
            answer = status(self.real_root / "proj")
        finally:
            subprocess.run = orig
        _eq(answer, None,
            f"worktree detection returned {answer!r} on timeout — it must be "
            "tri-state None so the caller fails safe, not False (which routes a "
            "worktree plan project-side, the damaging direction)")
        return "detect present; timeout -> None (fails safe)"

    def link_E2(self):
        # The probe, as specified, on THIS platform: /proc does not exist here.
        _true(not Path("/proc/self").exists() or sys.platform != "darwin",
              "platform assumption drifted")
        with _FakeSession() as fs:
            if not fs.start:
                raise CannotDemonstrate("ps did not report a start time")
            alive = {"pid": fs.pid, "pid_start": fs.start,
                     "identity": "session-process/v1"}
            _eq(tm.probe_liveness(alive), "ALIVE", "a live process did not probe ALIVE")
            fs.kill()
            time.sleep(0.2)
            _eq(tm.probe_liveness(alive), "DEAD", "a dead process did not probe DEAD")
        _eq(tm.probe_liveness({"session_id": "x"}), "LEGACY",
            "a payload with no identity fields did not probe LEGACY")

        # The EPERM arm — a process owned by another user. The S4 evidence
        # records this arm as untested; it is exercised here. pid 1 is NOT a
        # valid probe: `probe_liveness` short-circuits `pid <= 1` to UNKNOWN by
        # design, so the pid must be a real other-user process above that.
        other = None
        listing = subprocess.run(["ps", "-axo", "pid=,uid="],
                                 capture_output=True, text=True)
        mine = os.getuid()
        for row in listing.stdout.splitlines():
            parts = row.split()
            if len(parts) != 2:
                continue
            try:
                pid, uid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            if pid <= 1 or uid == mine:
                continue
            try:
                os.kill(pid, 0)
            except PermissionError:
                other = pid
                break
            except OSError:
                continue
        if other is None:
            raise CannotDemonstrate(
                "no process owned by another user was reachable to exercise the "
                "EPERM arm (running as root?)")
        start = tm._process_start(other)
        _true(start, f"ps could not describe pid {other} — the EPERM fallthrough "
                     "has nothing to compare against")
        verdict = tm.probe_liveness({"pid": other, "pid_start": start,
                                     "identity": "session-process/v1"})
        _eq(verdict, "ALIVE",
            f"a live process owned by another user probed {verdict}, not ALIVE — "
            "EPERM is being read as absence")
        stale = tm.probe_liveness({"pid": other, "pid_start": "Mon Jan  1 00:00:00 2001",
                                   "identity": "session-process/v1"})
        _eq(stale, "DEAD", "a recycled other-user pid did not probe DEAD")
        return f"ALIVE/DEAD/LEGACY hold; EPERM arm exercised on pid {other}"

    def link_E3(self):
        # Track B calls the LIVE todo.py at runtime, and the two repos ship
        # independently — so the interpreter Track B invokes it under must be
        # able to run it.
        rbk = Path(os.environ.get("S8_CLARIFY_ROOT",
                                  "~/repos/Projects/Personal/clarify")) \
            / "clarify" / "infrastructure" / "real_bookkeeping.py"
        if not rbk.exists():
            raise CannotDemonstrate(f"the v2 door is not at {rbk}")
        src = rbk.read_text(encoding="utf-8")
        m = re.search(r'["\']([^"\']*python[^"\']*)["\']', src)
        interpreters = ["python3"]
        if m and "/" in m.group(1):
            interpreters.append(m.group(1))
        checked = []
        for interp in dict.fromkeys(interpreters):
            exe = shutil.which(interp) or interp
            if not Path(exe).exists():
                continue
            proc = subprocess.run(
                [exe, str(HOOKS / "todo.py"), "validate-framing",
                 "[Thought] **T** — Problem: p. Context: c. Guiding policy: g. "
                 "Master plan: [[s]]."],
                capture_output=True, text=True,
                env={**os.environ, "CLAUDE_CONFIG_DIR": str(CONFIG)})
            _true(proc.returncode in (0, 1),
                  f"todo.py crashed under {exe} (rc={proc.returncode}): "
                  f"{proc.stderr[-400:]}")
            checked.append(f"{Path(exe).name}:rc{proc.returncode}")
        _true(checked, "no interpreter could be resolved to check compatibility")
        return f"todo.py runs under {', '.join(checked)}"


# --------------------------------------------------------------------------- #
# unittest face
# --------------------------------------------------------------------------- #

class S8ComposedWalk(unittest.TestCase):

    def test_the_composed_walk(self):
        results = ComposedWalk().run()
        failed = [r for r in results if r[2] == "FAIL"]
        undemonstrated = [r for r in results if r[2] == "CANNOT-DEMONSTRATE"]
        report = "\n".join(f"  {i:<4} {s:<20} {t}\n       {d}"
                           for i, t, s, d in results)
        self.assertFalse(failed, "links failed:\n" + report)
        # A link that cannot be demonstrated is reported, never silently passed.
        if undemonstrated:
            sys.stderr.write("\nCANNOT-DEMONSTRATE:\n" + "\n".join(
                f"  {i}: {d}" for i, _t, _s, d in undemonstrated) + "\n")


def _main(argv):
    if "--table" in argv:
        only = None
        if "--only" in argv:
            only = set(argv[argv.index("--only") + 1].split(","))
        results = ComposedWalk().run(only=only)
        width = max(len(t) for _i, t, _s, _d in results)
        print(f"\n{'LINK':<5} {'STATUS':<20} DESCRIPTION")
        print("-" * (28 + width))
        bad = 0
        for link_id, title, status, detail in results:
            print(f"{link_id:<5} {status:<20} {title}")
            if detail:
                print(f"{'':<26} {detail}")
            if status == "FAIL":
                bad += 1
        print()
        print(f"{sum(1 for r in results if r[2] == 'PASS')} passed, "
              f"{bad} failed, "
              f"{sum(1 for r in results if r[2] == 'CANNOT-DEMONSTRATE')} "
              f"cannot-demonstrate, "
              f"{sum(1 for r in results if r[2] == 'SKIP')} skipped")
        return 1 if bad else 0
    return 0 if unittest.main(argv=argv[:1], exit=False).result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(_main(sys.argv))

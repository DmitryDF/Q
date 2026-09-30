#!/usr/bin/env python3
"""Tests for hooks/commit_scope.py — S1/A2 of the declared-publish-scope plan.

Three layers, in the grading order the architecture prescribes:

  1. Unit tests on the four core functions (classify_style, compile_scope,
     evaluate, co_writers). Only classify_style and evaluate are genuinely
     pure; compile_scope and co_writers read the filesystem and shell out to
     git, so they are exercised against real scratch repos. The plan's A2
     row calls all four "pure" and that wording was carried here unchecked.
  2. Scratch-repo integration asserting the A1 probe matrix — i.e. that the
     classification this module encodes is the one git actually produces.
  3. A two-session fixture asserting compile_scope(A) never contains a
     B-authored path, and that a scoped publish leaves B's staged file alone.

EVERY test runs in a throwaway repo under $TMPDIR. No test commit is ever made
in a live repo: a synthetic commit on a live `main` cannot be removed without
rewriting shared history, which git-policy.md §4/§5 forbids — so the test
would violate the very rule this work exists to protect.

Run:  python3 ${KIT_HOOKS_DIR}/tests/test_commit_scope.py
      (or under pytest; both work)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_HOOKS))

import commit_scope as cs  # noqa: E402

PROBE = _HOOKS / "tests" / "probe_commit_styles.sh"


# ══════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════

def git(repo, *args, check=True, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, env=e)
    if check and r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{r.stderr}")
    return r


def make_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def write_ledger(repo: Path, session_id: str, paths, mtime=None):
    d = repo / ".claude" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"_session_files-{session_id}.log"
    f.write_text("".join(f"{repo / p}\n" for p in paths))
    if mtime is not None:
        os.utime(f, (mtime, mtime))
    return f


class TmpRepoCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="test-commit-scope.")
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ══════════════════════════════════════════════════════════════════════════
# 1. classify_style — the measured rule
# ══════════════════════════════════════════════════════════════════════════

class TestClassifyStyle(unittest.TestCase):

    def test_unset_is_unscoped(self):
        """'I could not tell' must resolve to the strict answer."""
        for v in (None, "", "   "):
            self.assertEqual(cs.classify_style(v), cs.UNSCOPED, repr(v))

    def test_bare_index_is_unscoped(self):
        self.assertEqual(cs.classify_style(".git/index"), cs.UNSCOPED)
        self.assertEqual(cs.classify_style("/abs/repo/.git/index"), cs.UNSCOPED)

    def test_index_lock_is_unscoped(self):
        """THE load-bearing case.

        `git commit -a` and `git commit -i` hand the hook `index.lock`, and
        both publish the WHOLE staged set. The design originally specified
        "anything not exactly `index` is declared", which would have passed a
        whole-tree sweep as a declared publish. Measured in
        probe_commit_styles.sh; see its header note 1.
        """
        self.assertEqual(cs.classify_style("/r/.git/index.lock"), cs.UNSCOPED)

    def test_next_index_is_scoped(self):
        self.assertEqual(cs.classify_style("/r/.git/next-index-123.lock"), cs.SCOPED)
        self.assertEqual(cs.classify_style("/r/.git/next-index-123"), cs.SCOPED)

    def test_unrecognised_basename_is_unscoped_not_scoped(self):
        """The fallback must be STRICT, and originally was not.

        It returned SCOPED, reasoning that an unrecognised name can only come
        from a caller supplying its own private index — a declaration by other
        means. The counter-case defeats that: point GIT_INDEX_FILE anywhere,
        `git read-tree HEAD` to fill it with the whole tree, then `git commit
        -a` against it. Git sweeps every modified tracked file in and the lock
        basename is `<custom>.lock`, which fell through to the permissive
        branch — the exact misclassification the module exists to prevent.

        A legitimate private-index publisher is covered by a named override
        and an allowlist entry with its reason, not by this guess.
        """
        for name in ("/tmp/my-private.index", "/tmp/my-private.index.lock",
                     "/r/.git/sneaky", "/r/.git/index.lock.lock", "weird"):
            self.assertEqual(cs.classify_style(name), cs.UNSCOPED, name)

    def test_only_next_index_is_scoped(self):
        """Exactly one signature means git bounded the commit to named paths."""
        self.assertEqual(cs.classify_style("/r/.git/next-index-1.lock"), cs.SCOPED)
        self.assertEqual(cs.classify_style("/r/.git/next-index-1"), cs.SCOPED)
        self.assertEqual(cs.classify_style("/r/.git/next-index-.lock"), cs.UNSCOPED)
        self.assertEqual(cs.classify_style("/r/.git/next-indexes-1.lock"), cs.UNSCOPED)


# ══════════════════════════════════════════════════════════════════════════
# 2. evaluate
# ══════════════════════════════════════════════════════════════════════════

class TestEvaluate(unittest.TestCase):

    def test_scoped_allowed(self):
        v = cs.evaluate(index_path="/r/.git/next-index-9.lock")
        self.assertTrue(v.allowed)
        self.assertEqual(v.reason, "declared")

    def test_unscoped_blocked_and_names_paths_and_command(self):
        """C5: a refusal that named nothing would leave no path forward."""
        v = cs.evaluate(index_path="/r/.git/index",
                        staged_paths=["mine.txt", "theirs.txt"])
        self.assertFalse(v.allowed)
        self.assertIn("mine.txt", v.message)
        self.assertIn("theirs.txt", v.message)
        self.assertIn("publish", v.remediation)

    def test_dash_a_is_blocked(self):
        """The regression this whole module turns on."""
        v = cs.evaluate(index_path="/r/.git/index.lock",
                        staged_paths=["mine.txt", "theirs.txt"])
        self.assertFalse(v.allowed, "`git commit -a` must not read as declared")

    def test_override_allows_unscoped(self):
        """C6: a deliberate unscoped publish stays possible."""
        v = cs.evaluate(index_path="/r/.git/index",
                        staged_paths=["x"], override=True)
        self.assertTrue(v.allowed)
        self.assertEqual(v.reason, "override")

    def test_nothing_staged_is_allowed_exactly_as_the_gate_allows_it(self):
        """`evaluate` must mirror the gate's FOUR outcomes, and had only three.

        Missing the empty-staged branch meant `commit_scope.py check` returned
        `undeclared` (exit 1) with an empty path list for an `--allow-empty`
        commit the gate allows — a surface pre-checking itself got the opposite
        answer from the enforcement point. The plan's A3 cell protects this path
        by name ("Do not touch the empty-staged path").
        """
        v = cs.evaluate(index_path="/r/.git/index", staged_paths=[])
        self.assertTrue(v.allowed)
        self.assertEqual(v.reason, "nothing-staged")

    def test_sequencer_exempt_is_tested_before_scope(self):
        """Order matters: in a sequencer state there is no way to declare."""
        v = cs.evaluate(index_path="/r/.git/index",
                        staged_paths=["x"], sequencer="MERGE_HEAD")
        self.assertTrue(v.allowed)
        self.assertEqual(v.reason, "sequencer-exempt")

    def test_sequencer_exemption_outranks_missing_override(self):
        v = cs.evaluate(index_path=None, sequencer="rebase-merge", override=False)
        self.assertTrue(v.allowed)


class TestSequencerState(TmpRepoCase):

    def test_detects_each_marker(self):
        repo = make_repo(self.root, "seq")
        gd = repo / ".git"
        self.assertIsNone(cs.sequencer_state(gd))
        for marker in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
            (gd / marker).write_text("x")
            self.assertEqual(cs.sequencer_state(gd), marker)
            (gd / marker).unlink()
        for d in ("rebase-merge", "rebase-apply"):
            (gd / d).mkdir()
            self.assertEqual(cs.sequencer_state(gd), d)
            (gd / d).rmdir()


# ══════════════════════════════════════════════════════════════════════════
# 3. compile_scope
# ══════════════════════════════════════════════════════════════════════════

class TestCompileScope(TmpRepoCase):

    def test_compiles_repo_relative_sorted_unique(self):
        repo = make_repo(self.root, "cs")
        (repo / "b.txt").write_text("b")
        (repo / "a.txt").write_text("a")
        write_ledger(repo, "SID-A", ["b.txt", "a.txt", "b.txt"])
        self.assertEqual(cs.compile_scope("SID-A", repo), ["a.txt", "b.txt"])

    def test_drops_entry_that_is_neither_on_disk_nor_known_to_git(self):
        """A scratch file written and deleted mid-session must not abort the
        whole publish on an unmatched pathspec."""
        repo = make_repo(self.root, "drop")
        (repo / "real.txt").write_text("r")
        write_ledger(repo, "SID-A", ["real.txt", "scratch-gone.txt"])
        self.assertEqual(cs.compile_scope("SID-A", repo), ["real.txt"])

    def test_keeps_tracked_but_deleted_file_so_the_deletion_publishes(self):
        repo = make_repo(self.root, "del")
        (repo / "tracked.txt").write_text("t")
        git(repo, "add", "tracked.txt")
        git(repo, "commit", "-q", "-m", "add tracked")
        (repo / "tracked.txt").unlink()
        write_ledger(repo, "SID-A", ["tracked.txt"])
        self.assertEqual(cs.compile_scope("SID-A", repo), ["tracked.txt"])

    def test_a_dropped_path_is_announced_not_silently_removed(self):
        """A silent drop is the wrong failure for this module to make.

        `_stat_ok` folds any OSError into "does not exist", so a path that is
        really there but unreadable looks identical to a deleted one. If git
        does not know it either, it leaves the declaration and is never
        published — while the caller is told the publish succeeded.
        """
        import contextlib
        import io
        repo = make_repo(self.root, "announce")
        (repo / "real.txt").write_text("r")
        write_ledger(repo, "SID-A", ["real.txt", "vanished.txt"])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            kept = cs.compile_scope("SID-A", repo)
        self.assertEqual(kept, ["real.txt"])
        self.assertIn("vanished.txt", err.getvalue())
        self.assertIn("dropped from declared scope", err.getvalue())
        self.assertNotIn("real.txt", err.getvalue())

    def test_drops_paths_outside_the_repo(self):
        repo = make_repo(self.root, "outside")
        outsider = self.root / "elsewhere.txt"
        outsider.write_text("x")
        (repo / "in.txt").write_text("i")
        d = repo / ".claude" / "logs"
        d.mkdir(parents=True)
        (d / "_session_files-SID-A.log").write_text(
            f"{repo / 'in.txt'}\n{outsider}\n")
        self.assertEqual(cs.compile_scope("SID-A", repo), ["in.txt"])

    def test_no_ledger_is_empty_not_an_error(self):
        repo = make_repo(self.root, "noledger")
        self.assertEqual(cs.compile_scope("SID-NONE", repo), [])

    def test_no_session_id_is_empty(self):
        repo = make_repo(self.root, "nosid")
        self.assertEqual(cs.compile_scope("", repo), [])


# ══════════════════════════════════════════════════════════════════════════
# 4. record_write — must never raise
# ══════════════════════════════════════════════════════════════════════════

class TestRecordWrite(TmpRepoCase):

    def test_records_and_compiles_back(self):
        repo = make_repo(self.root, "rw")
        (repo / "f.txt").write_text("f")
        self.assertTrue(cs.record_write(repo / "f.txt", "SID-A", repo))
        self.assertEqual(cs.compile_scope("SID-A", repo), ["f.txt"])

    def test_missing_session_id_records_nothing_rather_than_a_placeholder(self):
        """A placeholder line would attribute one session's file to another."""
        repo = make_repo(self.root, "rw2")
        (repo / "f.txt").write_text("f")
        env = os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        try:
            self.assertFalse(cs.record_write(repo / "f.txt", None, repo))
            self.assertEqual(list((repo / ".claude" / "logs").glob("*.log"))
                             if (repo / ".claude" / "logs").is_dir() else [], [])
        finally:
            if env is not None:
                os.environ["CLAUDE_CODE_SESSION_ID"] = env

    def test_unwritable_ledger_warns_and_returns_false_without_raising(self):
        """These callers also run under pytest and from background jobs; a
        hard failure would crash all three."""
        repo = make_repo(self.root, "rw3")
        (repo / "f.txt").write_text("f")
        logs = repo / ".claude" / "logs"
        logs.mkdir(parents=True)
        logs.chmod(0o500)  # read+execute only
        try:
            self.assertFalse(cs.record_write(repo / "f.txt", "SID-A", repo))
        finally:
            logs.chmod(0o700)

    def test_outside_any_repo_returns_false_without_raising(self):
        plain = self.root / "notarepo"
        plain.mkdir()
        (plain / "f.txt").write_text("f")
        self.assertFalse(cs.record_write(plain / "f.txt", "SID-A", plain))

    def test_permission_denied_stat_does_not_raise_out_of_the_ledger_functions(self):
        """An unreadable ledger directory must not raise out of either function.

        This asserts the INVARIANT, not a particular failure mechanism. It does
        not skip on the basis of whether the raise can be provoked — its one
        skip is for running as root, where the permission bits under test are
        not enforced at all. (An earlier docstring claimed "no skip" flatly,
        which overclaimed; a checker caught it.) The distinction matters,
        because the mechanism originally claimed for this test is
        version-dependent and does not hold here:

          * CPython <= 3.12 routes `Path.exists()` through `_ignore_error`,
            which swallows only ENOENT/ENOTDIR/EBADF/ELOOP, so EACCES
            propagates as PermissionError.
          * CPython 3.13+ (measured on 3.14.6) delegates to `os.path.exists()`,
            which catches OSError broadly. Measured: at mode 000, `os.stat` on
            a child raises while `Path.is_file()` returns False silently.
          (The module docstring and this one stated different boundaries for
          a while — 3.12 here, 3.13 there — inside a note whose whole point
          was an off-by-one correction. Both now say 3.13.)

        A test that skipped itself whenever the raise could not be provoked
        would assert nothing on the interpreter it actually runs under. This
        one pins the behaviour callers depend on either way.
        """
        if os.geteuid() == 0:
            self.skipTest("running as root; permission bits are not enforced")
        repo = make_repo(self.root, "perm")
        (repo / "f.txt").write_text("f")
        write_ledger(repo, "SID-A", ["f.txt"])
        logs = repo / ".claude" / "logs"
        logs.chmod(0o000)
        try:
            # Which layer absorbs the error is REPORTED, not asserted: the
            # value can only be one of two literals set by the try/except
            # below, so any assertion over it is a tautology that can never
            # fail. An earlier version asserted it; a checker called that out.
            try:
                (logs / "_session_files-SID-A.log").is_file()
                absorbed_by = "pathlib (CPython >= 3.13 swallows EACCES)"
            except PermissionError:
                absorbed_by = "_stat_ok (this interpreter leaks EACCES)"
            print(f"\n    [info] permission error absorbed by: {absorbed_by}")

            # The real assertions — the invariant callers depend on, whichever
            # layer happens to hold it on this interpreter.
            self.assertEqual(cs.compile_scope("SID-A", repo), [])
            self.assertEqual(cs.co_writers(["f.txt"], "SID-A", repo), {})
        finally:
            logs.chmod(0o700)

    def test_deleted_cwd_does_not_raise_out_of_the_ledger_functions(self):
        """The 'never raises' guarantee used to be breakable ONE LINE ABOVE the
        try block that was supposed to hold it.

        `ledger_path(sid, cwd)` ran unguarded, and with the default `cwd=None`
        it reaches `Path.cwd()` -> `os.getcwd()`, which raises FileNotFoundError
        when the process's working directory has been deleted underneath it —
        a real condition for the background jobs these callers run in. The same
        unguarded path was reachable from compile_scope and co_writers.
        """
        doomed = self.root / "doomed"
        doomed.mkdir()
        prev = os.getcwd()
        os.chdir(doomed)
        try:
            os.rmdir(doomed)                      # cwd now does not exist
            try:
                os.getcwd()
            except OSError:
                pass                              # confirmed: the condition is live
            else:
                self.skipTest("platform tolerates a deleted cwd; nothing to prove")
            # None of these three may raise.
            self.assertFalse(cs.record_write("/tmp/whatever.txt", "SID-A"))
            self.assertEqual(cs.compile_scope("SID-A"), [])
            self.assertEqual(cs.co_writers(["x"], "SID-A"), {})
        finally:
            os.chdir(prev)


# ══════════════════════════════════════════════════════════════════════════
# 5. co_writers (C9)
# ══════════════════════════════════════════════════════════════════════════

class TestCoWriters(TmpRepoCase):

    def test_reports_other_live_session_that_wrote_the_same_path(self):
        repo = make_repo(self.root, "cw")
        write_ledger(repo, "SID-A", ["TODO.md", "mine.txt"])
        write_ledger(repo, "SID-B", ["TODO.md", "theirs.txt"])
        joint = cs.co_writers(["TODO.md", "mine.txt"], "SID-A", repo)
        self.assertEqual(joint, {"TODO.md": ["SID-B"]})

    def test_ignores_own_session(self):
        repo = make_repo(self.root, "cw2")
        write_ledger(repo, "SID-A", ["TODO.md"])
        self.assertEqual(cs.co_writers(["TODO.md"], "SID-A", repo), {})

    def test_stale_ledger_is_not_a_live_co_writer(self):
        """Ledgers persist for days; an abandoned session must not raise a
        phantom warning forever."""
        repo = make_repo(self.root, "cw3")
        write_ledger(repo, "SID-A", ["TODO.md"])
        write_ledger(repo, "SID-OLD", ["TODO.md"],
                     mtime=time.time() - (cs.DEFAULT_LIVENESS_SECONDS + 600))
        self.assertEqual(cs.co_writers(["TODO.md"], "SID-A", repo), {})

    def test_only_declared_paths_are_reported(self):
        repo = make_repo(self.root, "cw4")
        write_ledger(repo, "SID-A", ["mine.txt"])
        write_ledger(repo, "SID-B", ["unrelated.txt"])
        self.assertEqual(cs.co_writers(["mine.txt"], "SID-A", repo), {})


# ══════════════════════════════════════════════════════════════════════════
# 6. Scratch-repo integration — assert the A1 matrix against real git
# ══════════════════════════════════════════════════════════════════════════

class TestAgainstRealGit(TmpRepoCase):
    """The classification is only correct if git actually behaves this way.

    These run real commits in a throwaway repo and capture GIT_INDEX_FILE from
    inside a real pre-commit hook, then feed it through classify_style.
    """

    def _repo_with_probe_hook(self, name):
        repo = make_repo(self.root, name)
        out = self.root / f"{name}.observed"
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text(
            "#!/usr/bin/env bash\n"
            f'printf "%s\\n" "${{GIT_INDEX_FILE:-<unset>}}" >> "{out}"\n'
            "exit 0\n")
        hook.chmod(0o755)
        return repo, out

    def _observed(self, out: Path) -> str:
        self.assertTrue(out.is_file(), "pre-commit hook did not run")
        return out.read_text().strip().splitlines()[-1]

    def test_bare_commit_classifies_unscoped(self):
        repo, out = self._repo_with_probe_hook("g_bare")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "-m", "bare")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.UNSCOPED)

    def test_pathspec_commit_classifies_scoped_and_leaves_the_other_staged(self):
        repo, out = self._repo_with_probe_hook("g_path")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "-m", "scoped", "--", "mine.txt")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.SCOPED)
        landed = git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertIn("mine.txt", landed)
        self.assertNotIn("theirs.txt", landed)
        still = git(repo, "diff", "--cached", "--name-only").stdout
        self.assertIn("theirs.txt", still, "the other session's file must survive")

    def test_dash_a_classifies_unscoped_against_real_git(self):
        """If this ever fails, the gate has stopped catching whole-tree sweeps."""
        repo, out = self._repo_with_probe_hook("g_a")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "-q", "-m", "seed")
        (repo / "mine.txt").write_text("m2")
        (repo / "theirs.txt").write_text("t2")
        git(repo, "commit", "-a", "-m", "dash-a")
        observed = self._observed(out)
        self.assertEqual(os.path.basename(observed), "index.lock",
                         "A1 matrix drift: -a no longer hands the hook index.lock")
        self.assertEqual(cs.classify_style(observed), cs.UNSCOPED)

    def test_include_classifies_unscoped_against_real_git(self):
        repo, out = self._repo_with_probe_hook("g_i")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "-q", "-m", "seed")
        (repo / "theirs.txt").write_text("t2")
        git(repo, "add", "theirs.txt")
        (repo / "mine.txt").write_text("m2")
        git(repo, "commit", "-i", "mine.txt", "-m", "include")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.UNSCOPED)

    def test_only_classifies_scoped_against_real_git(self):
        """-o and -i must not be conflated: -o genuinely scopes."""
        repo, out = self._repo_with_probe_hook("g_o")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "-o", "mine.txt", "-m", "only")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.SCOPED)

    def test_amend_forms_classify_as_the_matrix_records(self):
        """A1 recorded three `--amend` rows; none was asserted against real git.

        A2's validation gate asks for "scratch-repo integration asserting the A1
        matrix", and only 5 of its 15 rows were covered — so drift in the
        remainder would change the recorded matrix silently. `--amend -a` is the
        one that matters most: it is one of the three forms producing the
        dangerous `index.lock`.
        """
        # --amend bare -> index (UNSCOPED)
        repo, out = self._repo_with_probe_hook("g_amend_bare")
        (repo / "mine.txt").write_text("m")
        git(repo, "add", "mine.txt")
        git(repo, "commit", "--amend", "-m", "amended")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.UNSCOPED)

        # --amend with a pathspec -> next-index-* (SCOPED)
        repo, out = self._repo_with_probe_hook("g_amend_path")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "--amend", "-m", "amended", "--", "mine.txt")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.SCOPED)

        # --amend -a -> index.lock (UNSCOPED) — the dangerous one
        repo, out = self._repo_with_probe_hook("g_amend_a")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "mine.txt", "theirs.txt")
        git(repo, "commit", "-q", "-m", "seed")
        (repo / "mine.txt").write_text("m2")
        (repo / "theirs.txt").write_text("t2")
        git(repo, "commit", "--amend", "-a", "-m", "amended")
        observed = self._observed(out)
        self.assertEqual(os.path.basename(observed), "index.lock",
                         "A1 matrix drift: --amend -a no longer yields index.lock")
        self.assertEqual(cs.classify_style(observed), cs.UNSCOPED)

    def test_sequencer_states_classify_as_the_matrix_records(self):
        """The four sequencer rows: all commit through the shared index."""
        repo, out = self._repo_with_probe_hook("g_seq")
        (repo / "c.txt").write_text("v0")
        git(repo, "add", "c.txt")
        git(repo, "commit", "-q", "-m", "c0")
        git(repo, "checkout", "-q", "-b", "side")
        (repo / "c.txt").write_text("side")
        git(repo, "add", "c.txt")
        git(repo, "commit", "-q", "-m", "cs")
        git(repo, "checkout", "-q", "main")
        (repo / "c.txt").write_text("trunk")
        git(repo, "add", "c.txt")
        git(repo, "commit", "-q", "-m", "ct")
        git(repo, "merge", "side", check=False)          # conflicts
        (repo / "c.txt").write_text("resolved")
        git(repo, "add", "c.txt")
        self.assertEqual(cs.sequencer_state(repo / ".git"), "MERGE_HEAD")
        git(repo, "commit", "--no-edit")
        self.assertEqual(cs.classify_style(self._observed(out)), cs.UNSCOPED)

    def test_private_index_classifies_as_the_matrix_records(self):
        """A caller-supplied index reads UNSCOPED — the corrected decision."""
        repo, out = self._repo_with_probe_hook("g_priv")
        priv = self.root / "private.index"
        (repo / "mine.txt").write_text("m")
        env = {"GIT_INDEX_FILE": str(priv)}
        git(repo, "read-tree", "HEAD", env=env)
        git(repo, "add", "mine.txt", env=env)
        git(repo, "commit", "-m", "private", env=env)
        observed = self._observed(out)
        self.assertEqual(os.path.basename(observed), "private.index")
        self.assertEqual(cs.classify_style(observed), cs.UNSCOPED)

    def test_commit_of_untracked_pathspec_fails_so_add_must_come_first(self):
        repo = make_repo(self.root, "g_untracked")
        (repo / "new.txt").write_text("n")
        r = git(repo, "commit", "-m", "x", "--", "new.txt", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("did not match any file", r.stdout + r.stderr)


# ══════════════════════════════════════════════════════════════════════════
# 7. publish — the atom
# ══════════════════════════════════════════════════════════════════════════

class TestPublish(TmpRepoCase):

    def test_publishes_only_declared_paths_and_stages_untracked_first(self):
        repo = make_repo(self.root, "pub")
        (repo / "mine.txt").write_text("m")       # untracked
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "theirs.txt")            # another session staged this
        res = cs.publish(["mine.txt"], "mine only", cwd=repo, session_id="SID-A")
        self.assertEqual(res["status"], "committed")
        landed = git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertIn("mine.txt", landed)
        self.assertNotIn("theirs.txt", landed)
        self.assertIn("theirs.txt", git(repo, "diff", "--cached", "--name-only").stdout)

    def test_empty_scope_skips_and_never_falls_back_to_a_bare_commit(self):
        repo = make_repo(self.root, "pub_empty")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "theirs.txt")
        before = git(repo, "rev-parse", "HEAD").stdout.strip()
        res = cs.publish([], "nothing", cwd=repo, session_id="SID-A")
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "empty-scope")
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), before)

    def test_commits_working_tree_content_not_the_earlier_staged_version(self):
        """C8: this is what makes a jointly-edited append file carry both
        sessions' lines rather than dropping one set."""
        repo = make_repo(self.root, "pub_wt")
        f = repo / "shared.txt"
        f.write_text("line-A\n")
        git(repo, "add", "shared.txt")
        f.write_text("line-A\nline-B\n")          # the other session appended
        cs.publish(["shared.txt"], "shared", cwd=repo, session_id="SID-A")
        self.assertEqual(git(repo, "show", "HEAD:shared.txt").stdout, "line-A\nline-B\n")

    def test_announces_co_writers_before_committing(self):
        """C9: the notice must precede the commit, not follow it."""
        import io
        repo = make_repo(self.root, "pub_cw")
        (repo / "TODO.md").write_text("x\n")
        write_ledger(repo, "SID-A", ["TODO.md"])
        write_ledger(repo, "SID-B", ["TODO.md"])
        buf = io.StringIO()
        res = cs.publish(["TODO.md"], "todo", cwd=repo, session_id="SID-A",
                         stream=buf)
        self.assertEqual(res["status"], "committed")
        self.assertIn("SID-B", buf.getvalue())
        self.assertIn("TODO.md", buf.getvalue())

    def test_dry_run_does_not_commit_and_does_not_touch_the_index(self):
        repo = make_repo(self.root, "pub_dry")
        (repo / "mine.txt").write_text("m")
        (repo / "theirs.txt").write_text("t")
        git(repo, "add", "theirs.txt")
        before_head = git(repo, "rev-parse", "HEAD").stdout.strip()
        before_idx = git(repo, "diff", "--cached", "--name-only").stdout
        res = cs.publish(["mine.txt"], "x", cwd=repo, session_id="SID-A",
                         dry_run=True)
        self.assertEqual(res["status"], "dry-run")
        self.assertEqual(git(repo, "rev-parse", "HEAD").stdout.strip(), before_head)
        self.assertEqual(git(repo, "diff", "--cached", "--name-only").stdout, before_idx)

    def test_dry_run_stat_covers_only_declared_paths(self):
        """The whole reason /close's composer has been describing other
        sessions' work is that its --stat read the shared index."""
        repo = make_repo(self.root, "pub_stat")
        (repo / "mine.txt").write_text("m\n")
        (repo / "theirs.txt").write_text("t\n")
        git(repo, "add", "theirs.txt")
        res = cs.publish(["mine.txt"], "x", cwd=repo, session_id="SID-A",
                         dry_run=True)
        self.assertIn("mine.txt", res["stat"])
        self.assertNotIn("theirs.txt", res["stat"])

    def test_refuses_to_publish_during_a_sequencer_operation(self):
        repo = make_repo(self.root, "pub_seq")
        (repo / "mine.txt").write_text("m")
        (repo / ".git" / "MERGE_HEAD").write_text("deadbeef")
        with self.assertRaises(cs.PublishError):
            cs.publish(["mine.txt"], "x", cwd=repo, session_id="SID-A")

    def test_failed_commit_after_a_successful_add_reports_the_staged_residue(self):
        """The add/commit split the module refuses to expose as a verb is still
        REACHABLE by the error path: add succeeds, commit fails, and the
        declared paths sit staged in the shared index where a later undeclared
        commit would sweep them.

        It is reported rather than unwound, because the safe unwind does not
        exist — `git reset -- <paths>` would also discard a concurrent
        session's staging of the same path. So the contract is: raise, and name
        exactly what is staged and how to clear it.
        """
        repo = make_repo(self.root, "pub_failcommit")
        (repo / "mine.txt").write_text("m")
        hook = repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/usr/bin/env bash\necho 'rejected by policy' >&2\nexit 1\n")
        hook.chmod(0o755)

        with self.assertRaises(cs.PublishError) as ctx:
            cs.publish(["mine.txt"], "will be rejected", cwd=repo, session_id="SID-A")

        msg = str(ctx.exception)
        self.assertIn("AFTER staging", msg)
        self.assertIn("mine.txt", msg)
        self.assertIn("restore --staged", msg)
        # And the residue the message describes is really there.
        self.assertIn("mine.txt", git(repo, "diff", "--cached", "--name-only").stdout)

    def test_there_is_no_stage_verb(self):
        """The add/commit split is representable-and-wrong, and is the exact
        bug at two of the seven sites. A verb that cannot be split is the
        mechanism; a docstring asking callers not to split one is not."""
        self.assertFalse(hasattr(cs, "stage"))
        r = subprocess.run([sys.executable, str(_HOOKS / "commit_scope.py"), "stage"],
                           capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)


# ══════════════════════════════════════════════════════════════════════════
# 8. Two-session fixture — the property the whole plan exists for
# ══════════════════════════════════════════════════════════════════════════

class TestInsideALinkedWorktree(TmpRepoCase):
    """The configuration the Guiding Policy singles out as the one C2 must cover.

    `git-policy.md` §3 routes a resumed topic back into its EXISTING worktree, so
    two sessions sharing one index happens inside worktrees by design. The first
    version resolved the commit root through `main_checkout`, which inside a
    linked worktree returns the PRIMARY tree — so `publish` staged and committed
    against `main`'s checkout, and `compile_scope` dropped every worktree path as
    "outside this repo". Found by an independent validator; reproduced before the
    fix (`publish` raised `pathspec did not match any files`, `compile_scope`
    returned `[]`).
    """

    def _primary_and_worktree(self, name):
        primary = make_repo(self.root, name)
        wt = self.root / f"{name}-wt"
        git(primary, "worktree", "add", "-q", "-b", f"topic-{name}", str(wt))
        return primary, wt

    def test_commit_root_is_the_worktree_not_the_primary_tree(self):
        primary, wt = self._primary_and_worktree("cr")
        self.assertEqual(cs._commit_root(wt), wt.resolve())
        self.assertEqual(cs._commit_root(primary), primary.resolve())

    def test_ledger_dir_stays_canonical_for_both(self):
        """The ledger must NOT follow the commit root — one shared location per
        repo is what lets co_writers compare two sessions at all."""
        primary, wt = self._primary_and_worktree("ld")
        self.assertEqual(cs.ledger_dir(wt), cs.ledger_dir(primary))

    def test_compile_scope_sees_paths_written_inside_the_worktree(self):
        primary, wt = self._primary_and_worktree("cs")
        (wt / "topic-file.txt").write_text("work\n")
        d = primary / ".claude" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "_session_files-SID-W.log").write_text(f"{wt / 'topic-file.txt'}\n")
        self.assertEqual(cs.compile_scope("SID-W", wt), ["topic-file.txt"])

    def test_publish_commits_to_the_topic_branch_not_main(self):
        primary, wt = self._primary_and_worktree("pub")
        (wt / "topic-file.txt").write_text("work\n")
        res = cs.publish(["topic-file.txt"], "topic work", cwd=wt, session_id="SID-W")
        self.assertEqual(res["status"], "committed")
        landed = git(wt, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertIn("topic-file.txt", landed)
        # main must be untouched.
        self.assertNotIn("topic-file.txt",
                         git(primary, "show", "--stat", "--name-only",
                             "--format=", "HEAD").stdout)
        self.assertEqual(git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
                         "topic-pub")


class TestPublishRefusesADirectory(TmpRepoCase):

    def test_a_glob_matches_nothing_rather_than_sweeping(self):
        """Globs are neutralised by `:(literal)`, not by refusing paths.

        The first attempt refused any path containing `*`, `?` or `[` — which
        refuses 36 REAL files in this repository (`[YourCompany]/eGBR/[care] Roadmap.pdf`
        and friends), and refused the WHOLE declaration when one appeared, so a
        compiled ledger scope holding one such file would abort the publish and
        leave everything uncommitted.
        """
        repo = make_repo(self.root, "magic")
        (repo / "mine.md").write_text("mine\n")
        (repo / "theirs.md").write_text("theirs\n")
        # A glob declares nothing: it matches no literal file, so nothing lands.
        with self.assertRaises(cs.PublishError) as ctx:
            cs.publish(["*.md"], "sweep", cwd=repo, session_id="SID-A")
        self.assertIn("git add failed", str(ctx.exception))
        self.assertEqual(git(repo, "diff", "--cached", "--name-only").stdout.strip(), "")

    def test_a_filename_containing_glob_characters_is_publishable(self):
        """The 36 real files this repo has with `[` in their names must work."""
        repo = make_repo(self.root, "brackets")
        tricky = "[care] Roadmap.pdf"
        (repo / tricky).write_text("content\n")
        (repo / "theirs.txt").write_text("theirs\n")
        git(repo, "add", "theirs.txt")            # another session, staged
        res = cs.publish([tricky], "the bracketed file", cwd=repo, session_id="SID-A")
        self.assertEqual(res["status"], "committed")
        landed = git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertIn("Roadmap.pdf", landed)
        self.assertNotIn("theirs.txt", landed)
        self.assertIn("theirs.txt", git(repo, "diff", "--cached", "--name-only").stdout)

    def test_caller_supplied_pathspec_magic_is_refused(self):
        """We add the magic; a caller handing us magic is refused."""
        repo = make_repo(self.root, "callermagic")
        (repo / "mine.md").write_text("mine\n")
        with self.assertRaises(cs.PublishError) as ctx:
            cs.publish([":(glob)**"], "sweep", cwd=repo, session_id="SID-A")
        self.assertIn("not a single file", str(ctx.exception))

    def test_directory_pathspec_is_refused(self):
        """`git add -A -- Thoughts/` sweeps every concurrent session's artifact
        beneath it — the shape of two of the four landed cross-attributions, and
        the case the plan's A6 cell singles out as one that must stay excluded."""
        repo = make_repo(self.root, "dirs")
        (repo / "Thoughts").mkdir()
        (repo / "Thoughts" / "mine.md").write_text("mine\n")
        (repo / "Thoughts" / "theirs.md").write_text("theirs\n")
        with self.assertRaises(cs.PublishError) as ctx:
            cs.publish(["Thoughts"], "sweep", cwd=repo, session_id="SID-A")
        self.assertIn("directory", str(ctx.exception).lower())
        # and the individual file is still fine
        res = cs.publish(["Thoughts/mine.md"], "just mine", cwd=repo, session_id="SID-A")
        self.assertEqual(res["status"], "committed")
        landed = git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertNotIn("theirs.md", landed)


class TestTwoSessions(TmpRepoCase):

    def test_compile_scope_A_never_contains_a_B_authored_path(self):
        repo = make_repo(self.root, "two")
        for n in ("a1.txt", "a2.txt", "b1.txt", "b2.txt"):
            (repo / n).write_text(n)
        write_ledger(repo, "SID-A", ["a1.txt", "a2.txt"])
        write_ledger(repo, "SID-B", ["b1.txt", "b2.txt"])
        scope_a = cs.compile_scope("SID-A", repo)
        scope_b = cs.compile_scope("SID-B", repo)
        self.assertEqual(scope_a, ["a1.txt", "a2.txt"])
        self.assertEqual(scope_b, ["b1.txt", "b2.txt"])
        self.assertEqual(set(scope_a) & set(scope_b), set())

    def test_A_publishing_leaves_B_work_uncommitted_and_intact(self):
        """The four landed cross-attributions, in miniature."""
        repo = make_repo(self.root, "two_pub")
        (repo / "a1.txt").write_text("A work\n")
        (repo / "b1.txt").write_text("B work\n")
        write_ledger(repo, "SID-A", ["a1.txt"])
        write_ledger(repo, "SID-B", ["b1.txt"])
        git(repo, "add", "b1.txt")   # B staged, mid-flight

        cs.publish(cs.compile_scope("SID-A", repo), "A: my own work",
                   cwd=repo, session_id="SID-A")

        landed = git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertIn("a1.txt", landed)
        self.assertNotIn("b1.txt", landed, "B's work was swept into A's commit")
        self.assertEqual((repo / "b1.txt").read_text(), "B work\n")
        self.assertIn("b1.txt", git(repo, "diff", "--cached", "--name-only").stdout)

    def test_bare_commit_reproduces_the_defect_so_the_fixture_is_not_vacuous(self):
        """A test that passes with the mechanism disabled asserts nothing."""
        repo = make_repo(self.root, "two_neg")
        (repo / "a1.txt").write_text("A work\n")
        (repo / "b1.txt").write_text("B work\n")
        git(repo, "add", "a1.txt", "b1.txt")
        git(repo, "commit", "-m", "A: my own work")   # the OLD behaviour
        landed = git(repo, "show", "--stat", "--name-only", "--format=", "HEAD").stdout
        self.assertIn("b1.txt", landed,
                      "if this fails, the fixture no longer models the defect")


# ══════════════════════════════════════════════════════════════════════════
# 9. The probe ships and still reproduces its own recorded matrix
# ══════════════════════════════════════════════════════════════════════════

class TestProbeShips(unittest.TestCase):

    def test_probe_exists_and_is_executable(self):
        self.assertTrue(PROBE.is_file(), f"missing {PROBE}")
        self.assertTrue(os.access(PROBE, os.X_OK), f"{PROBE} is not executable")

    def test_recorded_matrix_states_the_dash_a_finding(self):
        """The header is the durable record of a measurement. If someone
        rewrites the rule, this is what tells them what it was measured
        against."""
        head = PROBE.read_text()[:8000]
        self.assertIn("index.lock", head)
        self.assertIn("UNSCOPED", head)


if __name__ == "__main__":
    unittest.main(verbosity=2)

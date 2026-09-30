#!/usr/bin/env python3
"""V1 acceptance test — S2 git-working-model shared-bookkeeping coherence.

Every concurrency mechanic is EXERCISED, not asserted in prose (the plan's V1:
"every concurrency mechanic must be TESTED — prose can't run"). Runs entirely
in an isolated, disposable `git init` scratch repo under a MANAGED temp dir
(tempfile.TemporaryDirectory — auto-cleaned, NEVER `rm -rf`; Safe-Executor),
seeded with an initial commit. No live `~/.claude` / `~/repos` is touched.

Covers the six V1 observables:
  1. No lost update — concurrent code-writer RMW (real write_slice_row) all
     persist; a deterministic control proves the UNLOCKED RMW loses updates;
     concurrent append (real bookkeeping_append) all retained.
  2. Fail-closed reads — a raw read of a shared path inside a worktree is
     ENOENT; the A1 resolver returns main's copy.
  3. One shared copy — a writer driven from inside a worktree lands on main.
  4. Manifest single source + drift-checked — divergence fails the drift-check,
     regen passes; A3 exclusion + A5 allow-set both derive from the manifest.
  5. Structural bookkeeping-commit allow — bookkeeping-only allowed, mixed
     blocked, a claude-promote-style harness commit allowed (bridge removed).
  6. Lock boundary — crash mid-hold releases the lock (OS-released); the
     primitive holds no network I/O; no blanket `--no-verify` was introduced.

Run:  python3 hooks/tests/test_bookkeeping_coherence_s2.py
Exit: 0 all green; 1 any failure.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import bookkeeping_paths  # noqa: E402
import bookkeeping_resolver  # noqa: E402
import bookkeeping_lock  # noqa: E402
import taskmanagement  # noqa: E402

CTX = mp.get_context("fork")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def git(cwd, *args, env=None, check_rc=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, env=env)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def seed_repo(root: Path) -> Path:
    repo = root / "Projects"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "Thoughts").mkdir()
    (repo / "Diary").mkdir()
    (repo / "src").mkdir()
    (repo / "TODO.md").write_text("# root todo\n")
    (repo / "Stats.md").write_text("# stats\n")
    (repo / "Thoughts" / "topic_THOUGHT.md").write_text(
        "# Topic\n\n## Slices\n\n"
    )
    (repo / "seed").write_text("x")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    return repo


# --- workers (module-level so fork can run them) ----------------------------

def _w_real_slice(spine: str, i: int, barrier):
    barrier.wait()
    taskmanagement.write_slice_row(spine, f"S{i}", {"status": "NOW"})


def _w_real_append(target: str, i: int, barrier):
    barrier.wait()
    bookkeeping_lock.bookkeeping_append(target, f"line-{i}\n")


def _w_unlocked_rmw(spine: str, i: int, barrier):
    # DETERMINISTIC control: read -> pause -> write WITHOUT the lock. Every
    # worker reads the same initial content and appends only its own row, so
    # the last writer wins and updates are LOST. Proves the test can fail.
    barrier.wait()
    text = Path(spine).read_text()
    time.sleep(0.05)
    Path(spine).write_text(text + f"<!-- L:slice id=U{i} -->\n")


def _w_locked_rmw(spine: str, i: int, barrier):
    # Same read-pause-write, but under THE bookkeeping lock → serialized → all
    # rows persist.
    barrier.wait()
    with bookkeeping_lock.bookkeeping_lock(spine):
        text = Path(spine).read_text()
        time.sleep(0.02)
        Path(spine).write_text(text + f"<!-- L:slice id=L{i} -->\n")


def _w_hold_then_die(lock_target: str, ready, hold):
    # Acquire the lock, signal ready, then die WITHOUT releasing (os._exit).
    with bookkeeping_lock.bookkeeping_lock(lock_target):
        ready.set()
        hold.wait(5)
        os._exit(0)  # crash mid-hold — OS must release the flock


def run(tmp: Path) -> None:
    repo = seed_repo(tmp)
    spine = repo / "Thoughts" / "topic_THOUGHT.md"
    default_branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()

    # === 1. No lost update ===================================================
    N = 16
    # 1a — real write_slice_row, concurrent, all persist
    b = CTX.Barrier(N)
    procs = [CTX.Process(target=_w_real_slice, args=(str(spine), i, b)) for i in range(N)]
    for p in procs: p.start()
    for p in procs: p.join()
    rows = taskmanagement.parse_slice_register(spine)
    ids = {r["id"] for r in rows}
    want = {f"S{i}" for i in range(N)}
    check("1a real write_slice_row: all concurrent rows persist",
          want.issubset(ids), f"{len(want & ids)}/{N} present")

    # 1b — control: UNLOCKED rmw loses updates (proves the test can fail)
    ctrl = repo / "Thoughts" / "ctrl_THOUGHT.md"
    ctrl.write_text("")
    b = CTX.Barrier(N)
    procs = [CTX.Process(target=_w_unlocked_rmw, args=(str(ctrl), i, b)) for i in range(N)]
    for p in procs: p.start()
    for p in procs: p.join()
    n_unlocked = ctrl.read_text().count("L:slice")
    check("1b control: UNLOCKED rmw LOSES updates (lock is load-bearing)",
          n_unlocked < N, f"only {n_unlocked}/{N} survived without the lock")

    # 1c — same rmw shape UNDER the lock: all persist
    lck = repo / "Thoughts" / "locked_THOUGHT.md"
    lck.write_text("")
    b = CTX.Barrier(N)
    procs = [CTX.Process(target=_w_locked_rmw, args=(str(lck), i, b)) for i in range(N)]
    for p in procs: p.start()
    for p in procs: p.join()
    n_locked = lck.read_text().count("L:slice")
    check("1c locked rmw: ALL updates persist under the bookkeeping flock",
          n_locked == N, f"{n_locked}/{N} survived")

    # 1d — real bookkeeping_append, concurrent, all lines retained
    diary = repo / "Diary" / "2026-07-21.md"
    diary.write_text("")
    b = CTX.Barrier(N)
    procs = [CTX.Process(target=_w_real_append, args=(str(diary), i, b)) for i in range(N)]
    for p in procs: p.start()
    for p in procs: p.join()
    lines = [ln for ln in diary.read_text().splitlines() if ln.startswith("line-")]
    check("1d bookkeeping_append: all concurrent appends retained (no lost append)",
          len(set(lines)) == N, f"{len(set(lines))}/{N} lines")

    # 1e — merge=union land-reconciliation: two branch-sides append to the SAME
    # append file, then merge → BOTH appends retained (A4 Cycle-11 path).
    bookkeeping_lock.install_merge_union_attributes(repo, local=True)
    diary_rel = "Diary/land.md"
    (repo / "Diary" / "land.md").write_text("base\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "diary base")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "-q", "-b", "sideA")
    (repo / "Diary" / "land.md").write_text("base\nfrom-A\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "A append")
    git(repo, "checkout", "-q", base)
    git(repo, "checkout", "-q", "-b", "sideB")
    (repo / "Diary" / "land.md").write_text("base\nfrom-B\n")
    git(repo, "add", "-A"); git(repo, "commit", "-qm", "B append")
    m = git(repo, "merge", "--no-edit", "sideA", check_rc=False,
            env=dict(os.environ, GIT_EDITOR="true"))
    merged = (repo / "Diary" / "land.md").read_text()
    check("1e merge=union: concurrent branch-side appends both retained at land",
          "from-A" in merged and "from-B" in merged and m.returncode == 0,
          f"rc={m.returncode} content={merged!r}")
    git(repo, "checkout", "-q", default_branch, check_rc=False)

    # === 2 & 3. Fail-closed reads + one shared copy (needs a worktree) =======
    helper = HOOKS / "worktree-helper.sh"
    r = subprocess.run(
        ["bash", str(helper), "create-or-lookup", "--repo", str(repo),
         "--topic", "wt-topic", "--repos-root", str(tmp / "repos")],
        capture_output=True, text=True,
    )
    wt = Path(r.stdout.strip()) if r.returncode == 0 else None
    if wt is None or not wt.exists():
        check("2/3 worktree setup", False, f"helper failed: {r.stderr[:200]}")
    else:
        raw_todo_enoent = not (wt / "TODO.md").exists()
        check("2a raw read of TODO.md inside worktree is ENOENT (fail-closed)",
              raw_todo_enoent)
        # A1 resolver from inside the worktree returns main's copy
        resolved = bookkeeping_resolver.resolve("TODO.md", cwd=wt)
        main_root = bookkeeping_resolver.main_checkout(cwd=wt)
        check("2b A1 resolver from worktree returns main's copy",
              resolved is not None and resolved == (repo.resolve() / "TODO.md")
              and resolved.read_text().startswith("# root todo"),
              str(resolved))
        check("3 one shared copy: worktree resolves to the primary main checkout",
              main_root == repo.resolve(), str(main_root))

    # === 4. Manifest single source + drift-check =============================
    manifest = HOOKS / "bookkeeping-paths.json"
    prose = HOOKS.parent / "rules" / "bookkeeping-model.md"
    env = dict(os.environ)
    # in-sync passes
    r = subprocess.run(["python3", str(HOOKS / "bookkeeping_paths.py"), "check-drift"],
                       capture_output=True, text=True, env=env)
    check("4a drift-check PASSES when manifest and prose agree", r.returncode == 0, r.stdout.strip() or r.stderr.strip())
    # diverge the manifest copy → fails
    tmp_manifest = tmp / "manifest_diverged.json"
    data = manifest.read_text().replace('"Stats.md"', '"StatsDIVERGED.md"')
    tmp_manifest.write_text(data)
    env2 = dict(os.environ, BOOKKEEPING_PATHS_MANIFEST=str(tmp_manifest),
                BOOKKEEPING_MODEL_FILE=str(prose))
    r = subprocess.run(["python3", str(HOOKS / "bookkeeping_paths.py"), "check-drift"],
                       capture_output=True, text=True, env=env2)
    check("4b drift-check FAILS when manifest diverges from prose", r.returncode == 1,
          (r.stdout + r.stderr).strip()[:80])
    # A3 exclusion + A5 allow both derive from the manifest (edit → both change)
    excl = bookkeeping_paths.sparse_checkout_patterns(bookkeeping_paths.load_manifest(tmp_manifest))
    allow = bookkeeping_paths.match_entry("StatsDIVERGED.md", bookkeeping_paths.load_manifest(tmp_manifest))
    check("4c A3 exclusion set derives from the manifest (edit reflected)",
          any("StatsDIVERGED.md" in p for p in excl))
    check("4d A5 allow-set derives from the same manifest (edit reflected)",
          allow is not None)

    # === 5. Structural bookkeeping-commit allow ==============================
    gate = HOOKS / "check-worktree-commit-gate.sh"
    ghd = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True).stdout.strip()
    ghd = (repo / ghd).resolve() if not os.path.isabs(ghd) else Path(ghd)
    pc = ghd / "hooks" / "pre-commit"
    if pc.exists() or pc.is_symlink():
        pc.unlink()
    pc.symlink_to(gate)

    # S6/A8 flipped the gate to ENFORCE. The allow-rule for main-side committers
    # (5a bookkeeping-only, 5c harness) still exists and still decides OWNERSHIP;
    # what changed is that a commit must first DECLARE its paths. So each case is
    # now a pair: the bare form is refused by the scope stage, the declared form
    # of the SAME commit reaches the ownership rule exactly as before.
    def commit_attempt(msg, files_writer, env_extra=None, paths=None):
        files_writer()
        git(repo, "add", "-A")
        e = dict(os.environ, **(env_extra or {}))
        argv = ["git", "-C", str(repo), "commit", "-qm", msg]
        if paths:
            argv += ["--", *paths]
        return subprocess.run(argv, capture_output=True, text=True, env=e)

    def head():
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def _bk():
        (repo / "TODO.md").write_text("# root todo\n- new\n")
        (repo / "Diary" / "d2.md").write_text("entry\n")
    h0 = head()
    r = commit_attempt("bookkeeping only", _bk)
    check("5a bare bookkeeping-only main commit: REFUSED by the scope stage",
          r.returncode != 0 and "scope BLOCKED" in r.stderr and head() == h0,
          r.stderr.strip()[:80])
    r = commit_attempt("bookkeeping only, declared", _bk,
                       paths=["TODO.md", "Diary/d2.md"])
    check("5a' DECLARED bookkeeping-only main commit: ALLOWED (no override)",
          r.returncode == 0 and head() != h0, r.stderr.strip()[:80])

    def _mixed():
        (repo / "TODO.md").write_text("# root todo\n- another\n")
        (repo / "src" / "app.py").write_text("print(1)\n")
    # Declared, so it passes the scope stage and reaches the MIXED ownership
    # rule — which must still block it. A bare mixed commit would be refused by
    # scope first and never test the ownership rule at all.
    r = commit_attempt("mixed", _mixed, paths=["TODO.md", "src/app.py"])
    check("5b DECLARED mixed bookkeeping+domain commit: still BLOCKED by ownership",
          r.returncode != 0 and "MIXES" in r.stderr, r.stderr.strip()[:60])
    git(repo, "reset", "-q", "HEAD", ".", check_rc=False)
    git(repo, "checkout", "-q", "--", "TODO.md", check_rc=False)
    (repo / "src" / "app.py").unlink()

    def _harness():
        (repo / "src" / "infra.py").write_text("infra\n")
    h0 = head()
    env_h = {"Q_CONFIG_SOURCE_PATH": str(repo.resolve())}
    r = commit_attempt("harness promote", _harness, env_extra=env_h)
    check("5c bare harness commit: REFUSED by the scope stage (no harness exemption)",
          r.returncode != 0 and "scope BLOCKED" in r.stderr and head() == h0,
          r.stderr.strip()[:80])
    r = commit_attempt("harness promote, declared", _harness, env_extra=env_h,
                       paths=["src/infra.py"])
    check("5c' DECLARED claude-promote-style harness commit: ALLOWED (bridge removed)",
          r.returncode == 0 and head() != h0, r.stderr.strip()[:80])

    # 5d — declared-publish-scope S2/A3. A SCOPED bookkeeping commit is allowed.
    #
    # Added during S2, while the scope stage was in WARN mode, so that A8's flip
    # (S6) would be an assertion change to 5a/5c rather than new test authoring
    # under pressure. S6 flipped 5a/5c to REFUSED and added their declared twins
    # (5a'/5c'); 5d remains the case with a CONCURRENT session's staged file,
    # proving the compliant path publishes only what it names.
    #
    # Both verbs are scoped, which is the whole rule: a scoped `git add` does not
    # bound a bare `git commit`, and `git commit -- <untracked>` fails outright.
    def _scoped_bk():
        (repo / "TODO.md").write_text("# root todo\n- scoped entry\n")
        (repo / "src" / "unrelated.py").write_text("# another session's work\n")

    _scoped_bk()
    git(repo, "add", "--", "TODO.md", "src/unrelated.py")
    r = subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "scoped bookkeeping", "--", "TODO.md"],
        capture_output=True, text=True, env=dict(os.environ))
    check("5d scoped bookkeeping commit: ALLOWED", r.returncode == 0,
          r.stderr.strip()[:80])

    # ...and it published ONLY the declared path — the other session's staged
    # file is still staged and uncommitted. This is the property the whole plan
    # exists for, asserted at the gate's own test surface.
    landed = subprocess.run(
        ["git", "-C", str(repo), "show", "--stat", "--name-only", "--format=", "HEAD"],
        capture_output=True, text=True).stdout
    still_staged = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only"],
        capture_output=True, text=True).stdout
    check("5d' scoped commit published only the declared path",
          "TODO.md" in landed and "unrelated.py" not in landed
          and "unrelated.py" in still_staged,
          f"landed={landed.split()} staged={still_staged.split()}")
    git(repo, "reset", "-q", "HEAD", ".", check_rc=False)
    (repo / "src" / "unrelated.py").unlink(missing_ok=True)

    # === 6. Lock boundary ====================================================
    # crash mid-hold releases the flock (OS-released on death)
    ready = CTX.Event(); hold = CTX.Event()
    holder = CTX.Process(target=_w_hold_then_die, args=(str(spine), ready, hold))
    holder.start()
    ready.wait(5)
    hold.set()
    holder.join(5)
    # after the crashed holder, a fresh acquire must succeed quickly (bounded)
    acquired = False
    try:
        with bookkeeping_lock.bookkeeping_lock(spine, timeout=3):
            acquired = True
    except bookkeeping_lock.BookkeepingLockTimeout:
        acquired = False
    check("6a crash mid-hold releases the lock (next writer proceeds)", acquired)

    # the primitive holds no network I/O in its critical section
    lp = bookkeeping_lock.lock_path_for(spine)
    check("6b lock is local (a lock file under the shared git dir, no network)",
          lp.name == "bookkeeping.lock" and ".git" in str(lp), str(lp))

    # no blanket `--no-verify` COMMAND introduced by S2 (documentation mentions
    # such as "never a blanket --no-verify" are fine — only an actual git
    # invocation is an offender).
    # The list must name every file the slice actually touches, or the check is
    # weaker than its own name. The two files declared-publish-scope S2
    # INTRODUCED were missing — and one of them (`commit_scope.py`) is the very
    # module that composes `git add`/`git commit` invocations, i.e. the single
    # most likely place for a blanket `--no-verify` to appear.
    changed = ["check-worktree-commit-gate.sh", "worktree-helper.sh",
               "bookkeeping_lock.py", "bookkeeping_paths.py", "bookkeeping_resolver.py",
               "check-unscoped-commit-trailer.sh", "commit_scope.py"]
    offenders = []
    for f in changed:
        for ln in (HOOKS / f).read_text().splitlines():
            s = ln.strip()
            if s.startswith("#") or s.startswith("*"):
                continue
            if "--no-verify" in s and ("git " in s or "commit" in s):
                offenders.append(f"{f}: {s[:50]}")
    check("6c no blanket --no-verify COMMAND introduced by S2", not offenders, ";".join(offenders))


def main() -> int:
    print("V1 acceptance — S2 shared-bookkeeping coherence")
    with tempfile.TemporaryDirectory(prefix="s2-v1-") as td:
        run(Path(td))
    n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\n{'='*60}\n{len(RESULTS)-n_fail}/{len(RESULTS)} passed"
          + (f", {n_fail} FAILED" if n_fail else " — ALL GREEN"))
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

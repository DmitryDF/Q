#!/usr/bin/env python3
"""S5 legacy-cutover — CLEAN relocate acceptance test (git-working-model / A12).

Exercises `worktree-helper.sh place` on the legacy-cutover cases in throwaway
`tempfile` git repos ONLY — auto-cleaned, NEVER `rm -rf`. No live ~/.claude and
no live ~/repos is touched: every repo + worktree lives under a TemporaryDirectory,
and the helper resolves its siblings (bookkeeping_paths.py, the gate/push sources,
worktree-detect.sh, bookkeeping_lock.py) from ITS OWN dir — here the clone's hooks
dir — so the run is fully self-contained.

Mirrors the S3/S4 harness (standalone script, RESULTS + check(), a `git` helper
with GIT_EDITOR=true/GIT_PAGER=cat, `seed_repo`, shell out to worktree-helper.sh).
The clean relocate is single-threaded topology, so no fork-multiprocessing is
needed (unlike the S2/S3/S4 concurrency suites).

Cases:
  T1  clean legacy topic (branch checked out in the primary) → relocated in one
      step; primary restored to `main`.
  T5  after relocate, shared bookkeeping (TODO.md) stays on `main`.
  T6  the new worktree fails closed on a raw shared-file read (sparse-excluded).
  T7  idempotent re-run → returns the same worktree, no error, primary on `main`.
  T2  dirty tree → migrates via the S6 dirty cutover (exit 0); primary restored
      to `main` (the S5-era refuse is superseded — full dirty coverage is in
      test_legacy_cutover_s6.py).
  T3  is-`main` (`--topic main`) → refuse, nothing moved.
  T4  detached primary → refuse-to-S6 (exit 3), nothing moved.
  T8  EDGE17 fallback: `main` checked out in another worktree → relocate still
      succeeds, primary left detached, graceful "restore manually" instruction.

Run:  python3 test_legacy_cutover_s5.py
Exit: 0 all green; 1 any failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
HELPER = HOOKS / "worktree-helper.sh"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    line = f"{'ok  ' if ok else 'FAIL'}  {name}"
    if detail and not ok:
        line += f"  :: {detail}"
    print(line)


def _env() -> dict:
    return dict(
        os.environ,
        GIT_EDITOR="true",
        GIT_PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
    )


def git(cwd, *args, must=True):
    r = subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, env=_env()
    )
    if must and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r


def place(repo, repos_root, topic):
    return subprocess.run(
        ["bash", str(HELPER), "place", "--repo", str(repo),
         "--repos-root", str(repos_root), "--topic", topic],
        capture_output=True, text=True, env=_env(),
    )


def head_branch(repo):
    r = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", must=False)
    return r.stdout.strip() if r.returncode == 0 else None  # None == detached


def seed_repo(root, name):
    repo = Path(root) / name
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "src.py").write_text("print('domain')\n")
    (repo / "TODO.md").write_text("# TODO (a main-owned shared bookkeeping path)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


# --- tests -----------------------------------------------------------------

def t1_t5_t6_t7_clean_relocate(sbx):
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "proj1")
    # legacy in-flight topic: a topic branch checked out in the PRIMARY clone, clean.
    git(repo, "checkout", "-q", "-b", "alpha-topic")

    r = place(repo, repos, "alpha-topic")
    check("T1 clean relocate exits 0", r.returncode == 0, r.stderr.strip())
    dest = Path(r.stdout.strip()) if r.stdout.strip() else repos / "proj1" / "alpha-topic"
    check("T1 worktree created at dest", dest.is_dir(), str(dest))
    check("T1 worktree is populated (src.py present)", (dest / "src.py").exists(), str(dest))

    wl = git(repo, "worktree", "list", "--porcelain").stdout
    check("T1 alpha-topic now lives in its worktree",
          "branch refs/heads/alpha-topic" in wl and str(dest) in wl, wl)

    check("T1 primary restored to main (EDGE17)", head_branch(repo) == "main",
          f"head={head_branch(repo)!r}")

    # T5 — shared bookkeeping stays on main (primary, now on main, still has TODO.md).
    check("T5 bookkeeping (TODO.md) stays on main", (repo / "TODO.md").exists(), "")

    # T6 — the new worktree fails closed on a raw shared-file read (sparse-excluded).
    check("T6 new worktree fails closed on TODO.md (sparse-excluded)",
          not (dest / "TODO.md").exists(), str(dest / "TODO.md"))

    # T7 — idempotent re-run returns the same worktree, no error, primary on main.
    r2 = place(repo, repos, "alpha-topic")
    check("T7 idempotent re-run exits 0", r2.returncode == 0, r2.stderr.strip())
    check("T7 idempotent re-run returns the same worktree",
          r2.stdout.strip() == str(dest), f"{r2.stdout.strip()!r} vs {str(dest)!r}")
    check("T7 primary still on main after re-run", head_branch(repo) == "main",
          f"head={head_branch(repo)!r}")


def t2_dirty_migrate(sbx):
    # S6 change: a DIRTY tree now MIGRATES (the S5-era refuse is superseded by the
    # S6 transactional dirty cutover). Full adversarial coverage of the dirty path
    # lives in test_legacy_cutover_s6.py; here we assert the S5 clean-path routing
    # correctly hands a plain dirty tree to the dirty cutover instead of refusing.
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "proj2")
    git(repo, "checkout", "-q", "-b", "beta-topic")
    (repo / "work.py").write_text("uncommitted domain change\n")  # DIRTY (untracked)

    r = place(repo, repos, "beta-topic")
    dest = repos / "proj2" / "beta-topic"
    check("T2 dirty → migrates (exit 0)", r.returncode == 0, f"rc={r.returncode} :: {r.stderr.strip()}")
    check("T2 beta-topic worktree created at dest", dest.is_dir(), str(dest))
    check("T2 domain work carried into the worktree",
          (dest / "work.py").exists(), str(dest / "work.py"))
    check("T2 primary restored to main (EDGE17)", head_branch(repo) == "main",
          f"head={head_branch(repo)!r}")
    check("T2 primary clone is clean after migration",
          git(repo, "status", "--porcelain").stdout.strip() == "", "")


def t3_ismain_refuse(sbx):
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "proj3")  # on main, clean
    r = place(repo, repos, "main")
    check("T3 is-main → refuse (exit != 0)", r.returncode != 0, f"rc={r.returncode}")
    check("T3 refuses migrating the canonical branch",
          "protected" in r.stderr.lower() or "EDGE16" in r.stderr, r.stderr.strip())
    check("T3 nothing moved", not (repos / "proj3" / "main").exists(), "")
    check("T3 primary still on main", head_branch(repo) == "main",
          f"head={head_branch(repo)!r}")


def t4_detached_refuse(sbx):
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "proj4")
    git(repo, "checkout", "-q", "--detach", "HEAD")  # detached primary
    r = place(repo, repos, "gamma-topic")
    check("T4 detached → exit 3", r.returncode == 3, f"rc={r.returncode} :: {r.stderr.strip()}")
    check("T4 message names detached / S6",
          "DETACHED" in r.stderr or "detached" in r.stderr, r.stderr.strip())
    check("T4 nothing moved", not (repos / "proj4" / "gamma-topic").exists(), "")


def t8_edge17_fallback(sbx):
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "proj8")
    # Put the primary on a topic branch, then check out `main` in a SEPARATE
    # worktree so EDGE17's `git checkout main` in the primary will fail.
    git(repo, "checkout", "-q", "-b", "delta-topic")
    other = Path(sbx) / "proj8-main-elsewhere"
    git(repo, "worktree", "add", "-q", str(other), "main")  # main now checked out in `other`

    r = place(repo, repos, "delta-topic")
    dest = Path(r.stdout.strip()) if r.stdout.strip() else repos / "proj8" / "delta-topic"
    check("T8 EDGE17 fallback: migration still succeeds (exit 0)",
          r.returncode == 0, r.stderr.strip())
    check("T8 delta-topic worktree created", dest.is_dir(), str(dest))
    check("T8 primary left on detached HEAD (main busy elsewhere)",
          head_branch(repo) is None, f"head={head_branch(repo)!r}")
    check("T8 graceful restore-manually instruction (no raw git fatal)",
          "restore" in r.stderr.lower() and "checkout" in r.stderr.lower(),
          r.stderr.strip())


def main():
    with tempfile.TemporaryDirectory(prefix="s5-cutover-") as sbx:
        for fn in (
            t1_t5_t6_t7_clean_relocate,
            t2_dirty_migrate,
            t3_ismain_refuse,
            t4_detached_refuse,
            t8_edge17_fallback,
        ):
            # each test seeds its own repo under the shared sandbox root
            try:
                fn(sbx)
            except Exception as e:  # a setup error is a test failure, not a crash
                check(f"{fn.__name__} raised", False, repr(e))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks green")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

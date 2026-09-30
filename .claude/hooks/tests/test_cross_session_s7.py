#!/usr/bin/env python3
"""V1 acceptance test — S7 git-working-model cross-session continuity + worker.

Exercises the two genuinely-new S7 modules in throwaway `tempfile` git repos ONLY
(auto-cleaned, NEVER `rm -rf` / `clean -f` / `worktree remove --force`). No live
`~/.claude` and no live `~/repos` is touched: every repo + worktree lives under a
TemporaryDirectory, the land-readiness sidecar dir is redirected via
LAND_READINESS_DIR to a tempdir, and the helper/worker resolve their siblings from
their OWN hooks dir (here the clone's) via `Path(__file__).resolve().parent`.

Mirrors the S1/S3/S5/S6 harness (standalone script; RESULTS + check(); a `git`
helper with GIT_EDITOR=true/GIT_PAGER=cat; `seed_repo`/`make_topic`; shell out to
`worktree-helper.sh` and `handoff-worker.sh`; `/usr/bin/true`/`false` fake checks).
Copy-the-harness, don't-import-it (the shipped convention).

Part A — land_readiness.py (A1, advisory sidecar):
  L1  write→read round-trip
  L2  missing record → in-progress (fail-open default)
  L3  garbled record → in-progress (fail-open)
  L4  traversal-shaped slug → confined (no escape); empty slug → refused
  L5  write to a read-only dir → advisory no-op (False, never raises)
  L6  invalid state → advisory no-op (False)

Part B — handoff-worker.sh / handoff_resume.py (A2/A8, reuse the A8 pattern):
  H1  in-progress resume → places into the EXISTING worktree, no land attempted
  H2  ready + green A6 check → routes through verify-then-land, LANDS (never a bare merge)
  H3  ready + red A6 check → the A6 gate STOPS the land (advisory hint does NOT force it)
  H4  stdout discipline → the worker's STDOUT is its report ONLY (logs go to stderr)

Run:  python3 hooks/tests/test_cross_session_s7.py
Exit: 0 all green; 1 any failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))
import land_readiness  # noqa: E402

HELPER = HOOKS / "worktree-helper.sh"
WORKER = HOOKS / "handoff-worker.sh"
TRUE = "/usr/bin/true"
FALSE = "/usr/bin/false"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    line = f"{'ok  ' if ok else 'FAIL'}  {name}"
    if detail and not ok:
        line += f"  :: {detail}"
    print(line)


def _env(ldr_dir=None) -> dict:
    e = dict(os.environ, GIT_EDITOR="true", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0")
    if ldr_dir is not None:
        e["LAND_READINESS_DIR"] = str(ldr_dir)
    return e


def git(cwd, *args, must=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, env=_env())
    if must and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r


def sha(repo, ref="main"):
    return git(repo, "rev-parse", ref).stdout.strip()


def seed_repo(root, name):
    repo = Path(root) / name
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("v0\n")
    (repo / "TODO.md").write_text("# todo (a main-owned shared bookkeeping path)\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    return repo


def make_topic(repo, name="topic", path="src/feature.py", body="feat\n"):
    """A topic branch one domain commit AHEAD of main; leaves the primary on main."""
    git(repo, "checkout", "-q", "-b", name)
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"{name} work")
    git(repo, "checkout", "-q", "main")
    return name


def place(repo, repos_root, topic):
    return subprocess.run(
        ["bash", str(HELPER), "place", "--repo", str(repo),
         "--repos-root", str(repos_root), "--topic", topic],
        capture_output=True, text=True, env=_env())


def worker(repo, repos_root, topic, ldr_dir, *extra):
    return subprocess.run(
        ["bash", str(WORKER), "--topic", topic, "--repo", str(repo),
         "--repos-root", str(repos_root), *extra],
        capture_output=True, text=True, env=_env(ldr_dir))


# ─── Part A — land_readiness.py (A1) ────────────────────────────────────────

def a_land_readiness(sbx):
    ldr = Path(sbx) / "ldr"

    # L1 — write→read round-trip.
    ok = land_readiness.write_readiness("alpha-topic", land_readiness.READY, base_dir=ldr)
    check("L1 write returns True", ok, "")
    check("L1 read round-trips to ready",
          land_readiness.read_readiness("alpha-topic", base_dir=ldr) == land_readiness.READY, "")

    # L2 — missing record → in-progress (fail-open default).
    check("L2 missing record → in-progress",
          land_readiness.read_readiness("never-written", base_dir=ldr) == land_readiness.IN_PROGRESS, "")

    # L3 — garbled record → in-progress (fail-open).
    rp = land_readiness.record_path("garbled-topic", base_dir=ldr)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("not-a-valid-state\n\x00garbage\n")
    check("L3 garbled record → in-progress",
          land_readiness.read_readiness("garbled-topic", base_dir=ldr) == land_readiness.IN_PROGRESS, "")

    # L4 — traversal-shaped slug is CONFINED; empty slug is refused.
    tp = land_readiness.record_path("../../etc/passwd", base_dir=ldr)
    check("L4 traversal slug confined to sidecar dir",
          tp.parent == ldr.resolve() and ".." not in str(tp), str(tp))
    check("L4 traversal slug does not escape (name is sanitized)",
          tp.name == "_land_readiness-etc-passwd.md", tp.name)
    refused = False
    try:
        land_readiness.record_path("../", base_dir=ldr)
    except ValueError:
        refused = True
    check("L4 empty-after-sanitize slug refused", refused, "")
    check("L4 write with empty-after-sanitize slug → False (no raise)",
          land_readiness.write_readiness("///", land_readiness.READY, base_dir=ldr) is False, "")

    # L5 — write to a read-only dir → advisory no-op (False), never raises.
    ro = Path(sbx) / "readonly"
    ro.mkdir()
    os.chmod(ro, 0o500)  # r-x — cannot create the record file
    try:
        res = land_readiness.write_readiness("blocked-topic", land_readiness.READY, base_dir=ro)
        check("L5 write to read-only dir → False (fail-open, no raise)", res is False, f"got {res!r}")
    except Exception as e:  # a raise here is the failure mode we are testing against
        check("L5 write to read-only dir raised", False, repr(e))
    finally:
        os.chmod(ro, 0o700)  # restore so TemporaryDirectory can clean up

    # L6 — invalid state → advisory no-op (False).
    check("L6 invalid state → False (advisory no-op)",
          land_readiness.write_readiness("alpha-topic", "bogus-state", base_dir=ldr) is False, "")


# ─── Part B — handoff-worker.sh / handoff_resume.py (A2/A8) ──────────────────

def b1_in_progress_resume(sbx):
    repos = Path(sbx) / "b1-repos"
    ldr = Path(sbx) / "b1-ldr"
    repo = seed_repo(sbx, "b1-proj")
    make_topic(repo, "topic")            # topic branch exists, primary back on main

    pre = place(repo, repos, "topic")    # pre-place: the topic already has a worktree
    dest = pre.stdout.strip().splitlines()[-1] if pre.stdout.strip() else str(repos / "b1-proj" / "topic")
    check("H1 pre-place created the worktree", Path(dest).is_dir(), dest)

    before = sha(repo)                   # main tip before the worker
    # readiness left unwritten → reads as in-progress (fail-open default)
    r = worker(repo, repos, "topic", ldr)
    check("H1 in-progress resume exits 0", r.returncode == 0, f"rc={r.returncode} :: {r.stderr.strip()[-300:]}")
    check("H1 worker resumes into the EXISTING worktree (no duplicate)",
          dest in r.stdout, f"stdout={r.stdout.strip()!r} dest={dest!r}")
    check("H1 report says in-progress / no land attempted",
          "in-progress" in r.stdout and "RESUMED:" in r.stdout, r.stdout.strip())
    check("H1 main did NOT advance (no land on an in-progress resume)",
          sha(repo) == before, f"{sha(repo)} vs {before}")


def b2_ready_green_lands(sbx):
    repos = Path(sbx) / "b2-repos"
    ldr = Path(sbx) / "b2-ldr"
    repo = seed_repo(sbx, "b2-proj")
    make_topic(repo, "topic")
    land_readiness.write_readiness("topic", land_readiness.READY, base_dir=ldr)

    before = sha(repo)
    r = worker(repo, repos, "topic", ldr, "--check-cmd", TRUE)
    check("H2 ready+green resume exits 0", r.returncode == 0,
          f"rc={r.returncode} :: {r.stderr.strip()[-400:]}")
    check("H2 report says RESUMED+LANDED (routed through the A6 gate)",
          "RESUMED+LANDED" in r.stdout, r.stdout.strip())
    check("H2 main ADVANCED (the land actually happened via the gate, not a bare merge)",
          sha(repo) != before, f"{sha(repo)} vs {before}")
    # never-a-bare-merge evidence: the topic tip is now an ancestor of main
    anc = git(repo, "merge-base", "--is-ancestor", "topic", "main", must=False).returncode == 0
    check("H2 topic landed into main (topic tip is an ancestor of main)", anc, "")


def b3_ready_red_stops(sbx):
    repos = Path(sbx) / "b3-repos"
    ldr = Path(sbx) / "b3-ldr"
    repo = seed_repo(sbx, "b3-proj")
    make_topic(repo, "topic")
    land_readiness.write_readiness("topic", land_readiness.READY, base_dir=ldr)

    before = sha(repo)
    r = worker(repo, repos, "topic", ldr, "--check-cmd", FALSE)  # red check
    check("H3 ready+red → the A6 gate STOPS the land (exit 3)", r.returncode == 3,
          f"rc={r.returncode} :: {r.stderr.strip()[-400:]}")
    check("H3 report says the gate STOPPED the land",
          "STOPPED" in r.stdout, r.stdout.strip())
    check("H3 advisory 'ready' hint did NOT force a land — main unchanged (A6 authoritative)",
          sha(repo) == before, f"{sha(repo)} vs {before}")


def b4_stdout_discipline(sbx):
    repos = Path(sbx) / "b4-repos"
    ldr = Path(sbx) / "b4-ldr"
    repo = seed_repo(sbx, "b4-proj")
    make_topic(repo, "topic")
    land_readiness.write_readiness("topic", land_readiness.READY, base_dir=ldr)

    r = worker(repo, repos, "topic", ldr, "--check-cmd", TRUE)
    out_lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    check("H4 worker STDOUT is exactly one report line",
          len(out_lines) == 1 and out_lines[0].startswith("RESUMED"), r.stdout.strip())
    check("H4 place/land LOGS are NOT on the worker's stdout (go to stderr)",
          "[worktree-helper]" not in r.stdout and "[handoff-worker]" not in r.stdout, r.stdout.strip())
    check("H4 the worktree-helper/land logs DID appear on stderr",
          "[handoff-worker]" in r.stderr, r.stderr.strip()[-200:])


def main():
    with tempfile.TemporaryDirectory(prefix="s7-xsession-") as sbx:
        for fn in (a_land_readiness,
                   b1_in_progress_resume,
                   b2_ready_green_lands,
                   b3_ready_red_stops,
                   b4_stdout_discipline):
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

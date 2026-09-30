#!/usr/bin/env python3
"""V1 acceptance test — S3 git-working-model verify-then-land gate + land port.

Every concurrency / safety mechanic is EXERCISED, not asserted in prose (the
plan V1: "every concurrency mechanic must be TESTED — prose can't run"; two
prose data-loss bugs were caught in review precisely because prose can't run).
Runs entirely in isolated, disposable `git init` scratch repos under MANAGED
temp dirs (tempfile.TemporaryDirectory — auto-cleaned, NEVER `rm -rf`). No live
`~/.claude` / `~/repos` is touched; the canonical check is faked with
`/usr/bin/true` (green) / `/usr/bin/false` (red).

Covers T1–T12:
  T1  green check → lands to main; main tip is the merge commit
  T2  red check → no land; topic branch untouched
  T3  merge-tree conflict pre-check → no land; both branches untouched
  T4  bookkeeping-only advance mid-gate → land proceeds; the bookkeeping commit
      survives (NOT reverted)
  T5  domain advance mid-gate → land aborts stale; nothing lands
  T6  land ↔ bookkeeping serialize under the ONE S2 lock (a held lock times the
      land out; releasing lets it land) — proves same-lock contention
  T7  conflict-abort trap (gate-bypass bookkeeping conflict) → `git merge
      --abort`, no `MERGE_HEAD`, main untouched
  T8  rollback → `git revert -m 1` inverse commit; history preserved (no reset)
  T9  non-TTY detect-and-bail — the CLI returns exit 3 on a conflict, no hang
  T10 teardown-on-check-failure — a crashing check leaks no worktree/tempdir
  T11 TOCTOU — a push to the topic after the check lands the PINNED SHA, not the
      new tip
  T12 dirty `main` checkout → clean-abort; the operator's uncommitted work is
      preserved

T13–T18 (land-port-tested-configuration S1) construct the PRODUCTION shape T1–T12
never reach — a checkout parked off `main`:
  T13 apply on a parked checkout → wrong-branch, naming the branch found; neither
      branch moves
  T14 apply on a detached HEAD → detached-head, its own case (not "branch HEAD")
  T15 rollback refuses off-main AND on a dirty tree (it had neither guard)
  T16 promote transmits the VERIFIED ref to its configured upstream, proven with a
      divergent upstream name that a bare `git push` cannot reach
  T17 the CLI renders the refusal as exit 3 with no traceback
  T18 the twin (`PromotionAdapter`) refuses before any merge — same invariant, so
      the two halves are asserted together

Run:  python3 hooks/tests/test_verify_then_land_s3.py
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

import bookkeeping_lock  # noqa: E402
import land_port  # noqa: E402
from land_port import VerifyThenLandAdapter  # noqa: E402

CTX = mp.get_context("fork")
TRUE = "/usr/bin/true"
FALSE = "/usr/bin/false"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def git(cwd, *args, check_rc: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_EDITOR"] = "true"
    env["GIT_PAGER"] = "cat"
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, env=env)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def sha(repo, ref="main") -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def seed_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("v0\n")
    (repo / "TODO.md").write_text("# todo\n")          # a bookkeeping (shared) path
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    return repo


def make_topic(repo, name="topic", path="src/feature.py", body="feat\n") -> str:
    git(repo, "checkout", "-q", "-b", name)
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", f"{name} work")
    git(repo, "checkout", "-q", "main")
    return name


def commit_on_main(repo, path, body, msg):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return sha(repo)


def parents_of(repo, ref="main") -> list[str]:
    return git(repo, "rev-list", "--parents", "-n", "1", ref).stdout.strip().split()


def is_ancestor(repo, anc, desc) -> bool:
    return git(repo, "merge-base", "--is-ancestor", anc, desc, check_rc=False).returncode == 0


# ─────────────────────────────────────────────────────────────────────────────

def t1_green_lands():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        before = sha(repo)
        res = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE]).run()
        p = parents_of(repo)
        check("T1 green check lands; main tip is a merge commit",
              res.status == "landed" and sha(repo) != before and len(p) == 3,
              f"status={res.status} parents={len(p)}")


def t2_red_no_land():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        m0, t0 = sha(repo), sha(repo, "topic")
        res = VerifyThenLandAdapter(repo, "topic", check_cmd=[FALSE]).run()
        check("T2 red check → no land; branch untouched",
              res.status == "red" and sha(repo) == m0 and sha(repo, "topic") == t0,
              f"status={res.status}")


def t3_conflict_precheck():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d))
        git(repo, "checkout", "-q", "-b", "topic")
        (repo / "src" / "app.py").write_text("topic-change\n")
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "topic app")
        git(repo, "checkout", "-q", "main")
        commit_on_main(repo, "src/app.py", "main-change\n", "main app")  # same file, diverged
        m0, t0 = sha(repo), sha(repo, "topic")
        res = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE]).run()
        check("T3 merge-tree conflict pre-check → no land; branches untouched",
              res.status == "conflict" and sha(repo) == m0 and sha(repo, "topic") == t0,
              f"status={res.status}")


def t4_bookkeeping_advance_not_reverted():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()
        book = commit_on_main(repo, "TODO.md", "# todo\nNEW-LINE\n", "bookkeeping advance")
        ap = a.apply()
        todo = (repo / "TODO.md").read_text()
        check("T4 bookkeeping-only advance mid-gate → lands; bookkeeping NOT reverted",
              dr.status == "green" and ap.status == "landed"
              and "NEW-LINE" in todo and is_ancestor(repo, book, "main"),
              f"dry={dr.status} apply={ap.status} todo_has_new={'NEW-LINE' in todo}")


def t5_domain_advance_stales():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()
        dom = commit_on_main(repo, "src/other.py", "x\n", "domain advance")
        ap = a.apply()
        # main is at the domain commit (NOT a merge commit) — nothing landed.
        p = parents_of(repo)
        check("T5 domain advance mid-gate → stale; nothing lands",
              dr.status == "green" and ap.status == "stale"
              and sha(repo) == dom and len(p) == 2,
              f"apply={ap.status} parents={len(p)}")


def _hold_lock(main_path, held_file, hold_secs):
    with bookkeeping_lock.bookkeeping_lock(str(main_path)):
        Path(held_file).write_text("held")
        time.sleep(hold_secs)


def t6_land_serializes_under_one_lock():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE], lock_timeout=0.5)
        dr = a.dry_run_diff()
        held = Path(d) / "held.flag"
        proc = CTX.Process(target=_hold_lock, args=(a.main, str(held), 2.0))
        proc.start()
        while not held.exists() and proc.is_alive():
            time.sleep(0.02)
        timed_out = a.apply()                       # same lock is held → times out
        proc.join(5)
        after = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        after.dry_run_diff()
        landed = after.apply()                       # lock now free → lands
        check("T6 land ↔ bookkeeping serialize under the ONE lock",
              dr.status == "green" and timed_out.status == "lock-timeout"
              and landed.status == "landed",
              f"contended={timed_out.status} after_release={landed.status}")


def t7_conflict_abort_trap():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d))
        # gate-bypass: the topic touches a bookkeeping path (TODO.md).
        git(repo, "checkout", "-q", "-b", "topic")
        (repo / "TODO.md").write_text("# todo\nTOPIC-EDIT\n")
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "topic touches TODO")
        git(repo, "checkout", "-q", "main")
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()                        # clean vs main_at_build → green
        # bookkeeping-only advance that CONFLICTS with the topic's TODO edit.
        commit_on_main(repo, "TODO.md", "# todo\nMAIN-EDIT\n", "main touches TODO")
        m_before_land = sha(repo)
        ap = a.apply()                               # staleness=bookkeeping-only→proceed→merge conflicts
        merge_head = (repo / ".git" / "MERGE_HEAD").exists()
        check("T7 land-merge conflict → abort; no MERGE_HEAD; main untouched",
              dr.status == "green" and ap.status == "conflict"
              and not merge_head and sha(repo) == m_before_land,
              f"apply={ap.status} MERGE_HEAD={merge_head}")


def t8_rollback_inverse_commit():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        landed = a.run()
        pre_rollback = sha(repo)
        rb = a.rollback(landed.merge_sha)
        feature_gone = not (repo / "src" / "feature.py").exists()
        check("T8 rollback → git revert -m 1 inverse commit; history preserved",
              landed.status == "landed" and rb.status == "rolled-back"
              and sha(repo) != pre_rollback
              and is_ancestor(repo, landed.merge_sha, "main")  # not a history rewrite
              and feature_gone,
              f"rb={rb.status} feature_gone={feature_gone}")


def t9_non_tty_detect_and_bail():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d))
        git(repo, "checkout", "-q", "-b", "topic")
        (repo / "src" / "app.py").write_text("topic\n")
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "topic")
        git(repo, "checkout", "-q", "main")
        commit_on_main(repo, "src/app.py", "main\n", "main")   # conflict
        r = subprocess.run(
            [sys.executable, str(HOOKS / "land_port.py"), "verify-then-land",
             "--repo", str(repo), "--topic", "topic", "--main", "main",
             "--check-cmd", TRUE],
            capture_output=True, text=True, timeout=30,
        )
        check("T9 non-TTY CLI detect-and-bail on conflict (exit 3, no hang)",
              r.returncode == 3, f"exit={r.returncode}")


def t10_teardown_on_check_failure():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        tmp_root = Path(d) / "cands"; tmp_root.mkdir()
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=["/no/such/bin/xyz123"],
                                  tmp_root=str(tmp_root))
        dr = a.dry_run_diff()
        wl = git(repo, "worktree", "list").stdout
        leaked_wt = "vtl-cand-" in wl
        leaked_dirs = list(tmp_root.glob("vtl-cand-*"))
        check("T10 crashing check leaks no worktree/tempdir (finally always fires)",
              dr.status == "red" and not leaked_wt and not leaked_dirs,
              f"dry={dr.status} leaked_wt={leaked_wt} leaked_dirs={len(leaked_dirs)}")


def t11_toctou_pins_sha():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()
        pinned = a.T_SHA
        # a background push advances the topic AFTER the check.
        git(repo, "checkout", "-q", "topic")
        (repo / "src" / "feature.py").write_text("feat v2 UNVERIFIED\n")
        git(repo, "add", "-A"); git(repo, "commit", "-qm", "topic v2")
        new_tip = sha(repo, "topic")
        git(repo, "checkout", "-q", "main")
        ap = a.apply()
        p = parents_of(repo)
        second_parent = p[2] if len(p) == 3 else None
        check("T11 TOCTOU → lands the PINNED sha, not the new topic tip",
              dr.status == "green" and ap.status == "landed"
              and second_parent == pinned and pinned != new_tip,
              f"apply={ap.status} landed_parent={second_parent == pinned}")


def t12_dirty_main_abort():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()
        before = sha(repo)
        (repo / "src" / "app.py").write_text("UNCOMMITTED operator edit\n")  # dirty main
        ap = a.apply()
        preserved = (repo / "src" / "app.py").read_text() == "UNCOMMITTED operator edit\n"
        check("T12 dirty main checkout → clean-abort; operator work preserved",
              dr.status == "green" and ap.status == "clean-abort"
              and sha(repo) == before and preserved,
              f"apply={ap.status} preserved={preserved}")


# ─────────────────────────────────────────────────────────────────────────────
# T13–T18 — the branch guard (land-port-tested-configuration S1).
#
# Every test ABOVE normalises HEAD onto `main` before landing: `make_topic` ends
# `git checkout -q main` (:100), and the tests that build a topic inline
# re-checkout `main` explicitly. So the production configuration — a checkout
# parked on some other branch — was reached by no test in this suite, and the
# adapter's staleness check (the `main` REF) and its merge (HEAD) could address
# different objects with nothing to notice.
#
# These tests construct that configuration deliberately. They do NOT weaken or
# re-scope anything T1–T12 assert; the normalising helpers stay as they are,
# because a suite that only ever tests the parked case would have the mirror-image
# blind spot.
#
# T18 exercises `PromotionAdapter` from THIS file rather than the promotion suite
# on purpose: it is the same invariant on the twin, and the plan's claim is that
# this is one property of the port rather than two unrelated bugs. Keeping both
# halves in one place is what makes that claim testable rather than asserted.


def _park_on(repo, branch: str = "parked") -> str:
    """Leave the checkout on a branch that is NOT `main` — the shape production
    is actually in, and the one no test above ever built."""
    git(repo, "checkout", "-q", "-b", branch)
    return branch


def t13_apply_refuses_off_main():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()
        main_before = sha(repo, "main")
        _park_on(repo)
        parked_before = sha(repo, "parked")
        ap = a.apply()
        check("T13 apply on a checkout parked off main → wrong-branch; both branches untouched",
              dr.status == "green" and ap.status == "wrong-branch"
              and "parked" in ap.detail                       # names the branch found
              and sha(repo, "main") == main_before
              and sha(repo, "parked") == parked_before,
              f"apply={ap.status} detail={ap.detail!r}")


def t14_apply_refuses_detached_head():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        dr = a.dry_run_diff()
        main_before = sha(repo, "main")
        git(repo, "checkout", "-q", "--detach")
        ap = a.apply()
        # Detachment gets its own status: `rev-parse --abbrev-ref HEAD` would have
        # reported the literal branch name "HEAD", which reads as a guard bug.
        check("T14 apply on a detached HEAD → detached-head (not 'branch HEAD'); main untouched",
              dr.status == "green" and ap.status == "detached-head"
              and "detached" in ap.detail.lower()
              and sha(repo, "main") == main_before,
              f"apply={ap.status} detail={ap.detail!r}")


def t15_rollback_guards():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        landed = a.run()
        after_land = sha(repo, "main")

        # (i) parked checkout → refuse rather than write an inverse commit onto
        #     whatever branch happens to be out.
        _park_on(repo)
        rb_parked = a.rollback(landed.merge_sha)
        parked_clean = sha(repo, "main") == after_land
        git(repo, "checkout", "-q", "main")

        # (ii) dirty checkout → refuse. `rollback()` had NO clean-check at all,
        #     while `apply()` has carried one since it shipped.
        (repo / "src" / "app.py").write_text("UNCOMMITTED operator edit\n")
        rb_dirty = a.rollback(landed.merge_sha)
        preserved = (repo / "src" / "app.py").read_text() == "UNCOMMITTED operator edit\n"

        check("T15 rollback refuses off-main (wrong-branch) and on a dirty tree (clean-abort)",
              landed.status == "landed"
              and rb_parked.status == "wrong-branch" and parked_clean
              and rb_dirty.status == "clean-abort" and preserved
              and sha(repo, "main") == after_land,
              f"parked={rb_parked.status} dirty={rb_dirty.status} preserved={preserved}")


def t16_promote_pushes_the_verified_ref():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        # A DIVERGENT upstream name is the discriminating case. `push.default` is
        # `simple` since git 2.0 (git-config(1)), which pushes the CURRENT branch
        # to a same-named remote branch — so a bare `git push` cannot even reach
        # `trunk`, and the naive repair (`git push <remote> main`) would CREATE a
        # remote `main` instead of updating the configured upstream. Only the
        # derived `<local>:<upstream-ref>` refspec is correct here.
        bare = Path(d) / "remote.git"
        git(Path(d), "init", "-q", "--bare", str(bare))
        git(repo, "remote", "add", "origin", str(bare))
        git(repo, "push", "-q", "-u", "origin", "main:trunk")

        a = VerifyThenLandAdapter(repo, "topic", check_cmd=[TRUE])
        res = a.run()
        local_main = sha(repo, "main")
        remote_trunk = git(bare, "rev-parse", "refs/heads/trunk").stdout.strip()
        stray = git(bare, "rev-parse", "--verify", "-q", "refs/heads/main",
                    check_rc=False).returncode == 0
        check("T16 promote transmits the VERIFIED ref to its configured upstream (divergent name)",
              res.status == "landed" and "promote: pushed" in res.detail
              and remote_trunk == local_main and not stray,
              f"detail={res.detail!r} trunk==main={remote_trunk == local_main} stray_main={stray}")


def t17_cli_refusal_exit_3_no_traceback():
    with tempfile.TemporaryDirectory() as d:
        repo = seed_repo(Path(d)); make_topic(repo)
        _park_on(repo)
        main_before = sha(repo, "main")
        r = subprocess.run(
            [sys.executable, str(HOOKS / "land_port.py"), "verify-then-land",
             "--repo", str(repo), "--topic", "topic", "--main", "main",
             "--check-cmd", TRUE],
            capture_output=True, text=True, timeout=60,
        )
        # Exit 3 is the bucket handoff_resume.py:141-144 already renders
        # ("the gate STOPPED the land — resolve and re-run"), which is exactly
        # what a refusal is. A traceback would mean the refusal escaped as a crash.
        check("T17 CLI refusal on a parked checkout → exit 3, no traceback, main untouched",
              r.returncode == 3 and "Traceback" not in (r.stderr + r.stdout)
              and "wrong-branch" in (r.stdout + r.stderr)
              and sha(repo, "main") == main_before,
              f"exit={r.returncode}")


def t18_promotion_adapter_refuses_off_main():
    from land_port import PromotionAdapter  # the twin — same invariant, same port

    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "source"
        (src / "hooks").mkdir(parents=True)
        git(src, "init", "-q", "-b", "main")
        git(src, "config", "user.email", "t@t")
        git(src, "config", "user.name", "t")
        (src / "hooks" / "mod.py").write_text("live v0\n")
        git(src, "add", "-A"); git(src, "commit", "-qm", "seed source")

        def cap(workspace, scope):
            (Path(workspace) / "hooks" / "mod.py").write_text("live v1\n")

        trace: list[str] = []
        # Seams declared up front rather than left None: this test depends on
        # reaching apply(), and a test that reaches it only because a guard cannot
        # see the test is the defect one level down.
        a = PromotionAdapter(
            src, session_scope=["hooks/mod.py"], capture_cmd=cap,
            network_tail_cmd=lambda pr_mode: "merged",
            green_tag_cmd=lambda: None,
            tmp_root=d, trace=trace,
        )
        try:
            dr = a.dry_run_diff()
            main_before = sha(src, "main")
            _park_on(src)
            ap = a.apply()
            # The twin is the worse half: past the merge it reads `approved_sha`
            # from the REF, so it would report "applied" with a SHA that never
            # merged — and then deploy that unmerged tree to live.
            check("T18 PromotionAdapter.apply on a parked source → refuses BEFORE any merge",
                  dr.status == "green" and ap.status == "wrong-branch"
                  and a.approved_sha is None
                  and "reconcile" not in trace and "source_commit" not in trace
                  and sha(src, "main") == main_before,
                  f"apply={ap.status} trace={trace}")
        finally:
            a.teardown()


def main() -> int:
    print("S3 verify-then-land acceptance (T1–T18):")
    for fn in (t1_green_lands, t2_red_no_land, t3_conflict_precheck,
               t4_bookkeeping_advance_not_reverted, t5_domain_advance_stales,
               t6_land_serializes_under_one_lock, t7_conflict_abort_trap,
               t8_rollback_inverse_commit, t9_non_tty_detect_and_bail,
               t10_teardown_on_check_failure, t11_toctou_pins_sha,
               t12_dirty_main_abort,
               t13_apply_refuses_off_main, t14_apply_refuses_detached_head,
               t15_rollback_guards, t16_promote_pushes_the_verified_ref,
               t17_cli_refusal_exit_3_no_traceback,
               t18_promotion_adapter_refuses_off_main):
        try:
            fn()
        except Exception as e:  # a test that throws is a failure, never a silent pass
            check(fn.__name__, False, f"EXCEPTION: {e}")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} green")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

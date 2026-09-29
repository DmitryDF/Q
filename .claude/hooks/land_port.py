#!/usr/bin/env python3
"""land_port — the ONE reusable verify-then-land port (S3 git-working-model).

A1 (the port) + A6 (the verify-then-land adapter) + A10 (git-merge rollback).

The port carries ONE responsibility — "preview a candidate landing, apply and
promote it only if it verifies" — as the `dry_run_diff → apply → promote`
contract (+ `rollback`). S3 builds the `VerifyThenLandAdapter` for the harness
config-source git repo; S4 later adds a promotion adapter behind the SAME port
(Ports & Adapters, code_first_architecture.md; the A5 one-port decision is
operator-locked — do NOT split).

The verify-then-land adapter:
  * pins BOTH sides to commit SHAs up front (verify-then-act is content-
    addressed, not lock-held — a background push to the topic mid-check can
    never land unverified),
  * pre-checks the merge in memory (`git merge-tree --write-tree` — touches
    neither index nor working tree),
  * builds the bring-`main`-in merge candidate on a detached HEAD inside a
    throwaway worktree at a MANAGED tempdir and runs the repo's canonical check
    there in a SANITIZED, isolated process env (env-allowlist, no shell) — the
    active topic branch is NEVER mutated,
  * lands topic→`main` ON GREEN ONLY, under the SAME S2 `bookkeeping_lock`
    (unified-main-writer rule), via a worktree-consistent `git merge` (never a
    raw ref move), asserting the `main` checkout is clean first and
    differentiating a harmless bookkeeping-only advance (green still holds →
    merge over it) from a domain advance (green is stale → abort + re-verify),
  * rolls back a bad land with `git revert -m 1` (never `reset --hard`).

STABILIZED LOCK PRINCIPLE (design A4/A6): the lock spans ONLY the local land
critical section — NEVER the long canonical check, the network push, or human
input. Teardown of the throwaway worktree is guaranteed in a `finally`.

Pure standalone check-logic (skill-location.md): no Claude-specific imports.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from bookkeeping_lock import bookkeeping_lock, BookkeepingLockTimeout  # noqa: E402
from bookkeeping_paths import is_shared  # noqa: E402
from bookkeeping_resolver import main_checkout  # noqa: E402

# The essential operational vars the sanitized check env keeps (review cycle 4
# item iv): a bare `env -i` strips TMPDIR/USER/LANG/LC_ALL and breaks legitimate
# linters/test-runners. Isolation is from the PRODUCER's polluted vars/aliases,
# not a bare env. Extend via VerifyThenLandAdapter(extra_env_keys=...).
DEFAULT_ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL", "SHELL")

# The harness's de-facto canonical check command (design A6 — no justfile/Makefile
# exists; claude-verify is the read-only structural + drift gate claude-promote
# bookends). Overridable per adapter (and by tests).
DEFAULT_CHECK_CMD = ("claude-verify", "--phase", "pre")


@dataclass
class LandResult:
    """The single structured outcome the port returns (no unstructured leak)."""

    status: str          # see STATUSES below
    detail: str = ""
    merge_sha: Optional[str] = None
    candidate_sha: Optional[str] = None
    changed_paths: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("landed", "pushed", "noop-promote", "rolled-back", "noop")

    def __str__(self) -> str:
        s = f"[{self.status}] {self.detail}"
        if self.merge_sha:
            s += f" (merge={self.merge_sha[:12]})"
        return s


# Terminal statuses the adapter can return.
#   dry_run_diff: green | red | conflict
#   apply:        landed | stale | conflict | clean-abort | lock-timeout
#                 | wrong-branch | detached-head
#   promote:      pushed | push-failed | noop-promote
#                 | wrong-branch | detached-head
#   rollback:     rolled-back | revert-conflict | lock-timeout | clean-abort
#                 | wrong-branch | detached-head
#   run():        any of the above (first non-advancing result short-circuits)
#
# `wrong-branch` / `detached-head` are the branch-guard refusals. They are
# deliberately NOT added to `_cli`'s two special-cased lists, so they fall through
# to exit 3 — the "actionable, non-error, detect-and-bail" bucket, which is the
# right class: the operator resolves it in their own worktree and re-runs.
#
# Stated precisely, because the near-miss matters: `handoff_resume.py:141-144`
# renders exit 3, but its text names "stale/red/conflict" — which does NOT include
# a wrong branch or a detached HEAD. The CODE path is correct and the operator gets
# a non-zero exit plus this adapter's own detail line on stdout; the worker's
# one-line summary is what under-describes the cause. Widening that summary is a
# change to `handoff_resume.py`, which is outside this slice's write targets.


@dataclass
class DeployCheckResult:
    """Outcome of `verify_deploy_candidate` — renders a config-source candidate to a
    throwaway destination and runs the canonical check AGAINST THE RENDERED
    TREE (never the attribute-mangled `dot_claude/` source layout, which is
    the wrong shape for `claude-verify` and would vacuously pass). Standalone
    S1 addition — does not replace or modify `LandResult` / the existing
    `VerifyThenLandAdapter.dry_run_diff()` in-source check."""

    verdict: str                       # "green" | "red"
    tip_sha: Optional[str] = None
    note_written: bool = False
    rendered_dir: Optional[str] = None  # preserved on red for debugging; None on green (cleaned up)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.verdict == "green"

    def __str__(self) -> str:
        s = f"[{self.verdict}] {self.detail}"
        if self.tip_sha:
            s += f" (tip={self.tip_sha[:12]})"
        return s


class LandPort:
    """The port interface — one responsibility, per-repo/direction adapters."""

    def dry_run_diff(self) -> LandResult:            # phase 1: preview+verify
        raise NotImplementedError

    def apply(self) -> LandResult:                   # phase 2: land on green
        raise NotImplementedError

    def promote(self) -> LandResult:                 # phase 3: publish
        raise NotImplementedError

    def rollback(self, merge_sha: str) -> LandResult:
        raise NotImplementedError

    def run(self) -> LandResult:
        """dry_run_diff → (green) apply → (landed) promote. Any non-advancing
        phase short-circuits with the active branch / main untouched."""
        dr = self.dry_run_diff()
        if dr.status != "green":
            return dr
        ap = self.apply()
        if ap.status != "landed":
            return ap
        pr = self.promote()
        ap.detail += f" | promote: {pr.status}"
        return ap


class VerifyThenLandAdapter(LandPort):
    """Harness verify-then-land: topic branch → `main` in a git repo."""

    def __init__(
        self,
        repo,
        topic: str,
        *,
        main_branch: str = "main",
        check_cmd: Optional[List[str]] = None,
        lock_timeout: Optional[float] = None,
        tmp_root: Optional[str] = None,
        extra_env_keys: Optional[List[str]] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        anchor = Path(repo)
        self.main = main_checkout(anchor) or anchor.resolve()
        self.topic = topic
        self.main_branch = main_branch
        self.check_cmd = list(check_cmd) if check_cmd else list(DEFAULT_CHECK_CMD)
        self.lock_timeout = lock_timeout
        self.tmp_root = tmp_root
        self.env_keys = list(DEFAULT_ENV_ALLOWLIST) + list(extra_env_keys or [])
        self._log = log or (lambda _m: None)

        # Content-addressed pins (set by dry_run_diff, reused by apply).
        self.T_SHA: Optional[str] = None
        self.main_at_build: Optional[str] = None
        self.candidate_sha: Optional[str] = None

    # ---- git plumbing (argument lists only — never shell=True) --------------

    def _git_env(self) -> dict:
        """Inherit os.environ but force non-interactive git (no editor/pager/
        credential prompt) so a merge/revert/diff never hangs a non-TTY run."""
        env = dict(os.environ)
        env["GIT_EDITOR"] = "true"
        env["GIT_PAGER"] = "cat"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _git(self, cwd, *args, check_rc: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, env=self._git_env(),
        )
        if check_rc and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed ({r.returncode}): {r.stderr.strip()}")
        return r

    def _rev_parse(self, ref: str, cwd=None) -> str:
        return self._git(cwd or self.main, "rev-parse", ref).stdout.strip()

    def _changed_paths(self, a: str, b: str) -> List[str]:
        out = self._git(self.main, "--no-pager", "diff", "--name-only", a, b).stdout
        return [p for p in out.splitlines() if p.strip()]

    def _sanitized_env(self) -> dict:
        """`env -i` + allowlist: only the essential operational vars survive, so
        the canonical check runs isolated from the producer's polluted env, but
        linters/test-runners that need TMPDIR/LANG/etc. don't fail artificially."""
        return {k: os.environ[k] for k in self.env_keys if k in os.environ}

    # ---- branch guard — assert the object we are about to mutate ------------
    #
    # `main_checkout()` resolves a WORKING TREE and reads no branch name, so
    # `self.main` is only *conventionally* on `main_branch`. Every mutation below
    # targets HEAD while the surrounding checks read the `main_branch` ref: two
    # different objects whenever the checkout is parked elsewhere. These two
    # helpers make that assumption an assertion.
    #
    # ASSERT, NEVER `git checkout`: relocating a human's working tree to satisfy
    # our own precondition is out of bounds (safe-defaults.md). This module has
    # contained no checkout since it shipped and gains none here.

    def _head_branch(self, cwd=None) -> Optional[str]:
        """HEAD's branch name, or None when HEAD is detached.

        `symbolic-ref --short -q HEAD` rather than `rev-parse --abbrev-ref HEAD`:
        the latter returns the literal string "HEAD" on a detached checkout, so a
        guard built on it reports "expected main, actual HEAD" — which reads as a
        bug in the guard rather than as detachment.
        """
        r = self._git(cwd or self.main, "symbolic-ref", "--short", "-q", "HEAD",
                      check_rc=False)
        return r.stdout.strip() or None

    def _require_on_main(self, action: str) -> Optional[LandResult]:
        """A refusing LandResult when HEAD is not on `main_branch`, else None.

        `action` names what would otherwise happen, so the refusal says what was
        avoided rather than only what was wrong.
        """
        head = self._head_branch()
        if head is None:
            return LandResult(
                "detached-head",
                f"the checkout at {self.main} is on a detached HEAD, so {action} "
                f"would not reach `{self.main_branch}`; check `{self.main_branch}` "
                f"out there and re-run — nothing was touched",
                candidate_sha=self.candidate_sha,
            )
        if head != self.main_branch:
            return LandResult(
                "wrong-branch",
                f"the checkout at {self.main} has `{head}` checked out, not "
                f"`{self.main_branch}`, so {action} would go to `{head}` instead; "
                f"check `{self.main_branch}` out there and re-run — nothing was "
                f"touched",
                candidate_sha=self.candidate_sha,
            )
        return None

    # ---- phase 1: dry_run_diff — preview + verify (NO mutation of the branch)

    def dry_run_diff(self) -> LandResult:
        # Pin both sides up front (TOCTOU-safe, content-addressed).
        self.T_SHA = self._rev_parse(f"{self.topic}^{{commit}}")
        self.main_at_build = self._rev_parse(self.main_branch)

        # In-memory conflict pre-check — touches neither index nor working tree.
        pre = self._git(self.main, "merge-tree", "--write-tree",
                        self.main_at_build, self.T_SHA, check_rc=False)
        if pre.returncode != 0:
            return LandResult(
                "conflict",
                f"merge of `{self.main_branch}` into the topic conflicts; bring "
                f"`{self.main_branch}` into your active worktree "
                f"(`git merge {self.main_branch}`), resolve, and re-run",
                candidate_sha=None,
            )

        # Build the candidate + run the canonical check in an isolated throwaway
        # worktree — teardown GUARANTEED in `finally` (a check crash never leaks).
        tmp = tempfile.mkdtemp(prefix="vtl-cand-", dir=self.tmp_root)
        wt = str(Path(tmp) / "cand")
        try:
            self._git(self.main, "worktree", "add", "--detach", wt, self.T_SHA)
            mc = self._git(wt, "merge", "--no-edit", self.main_at_build, check_rc=False)
            if mc.returncode != 0:
                self._git(wt, "merge", "--abort", check_rc=False)
                return LandResult(
                    "conflict",
                    f"candidate merge conflicts; resolve "
                    f"`git merge {self.main_branch}` in your active worktree and re-run",
                )
            self.candidate_sha = self._rev_parse("HEAD", cwd=wt)
            try:
                chk = subprocess.run(
                    self.check_cmd, cwd=wt, capture_output=True, text=True,
                    env=self._sanitized_env(),
                )
                rc = chk.returncode
            except OSError as e:
                # The check command itself could not be launched — treat as red
                # (not green), and let the `finally` still tear the candidate down.
                return LandResult(
                    "red",
                    f"canonical check could not run ({e}); nothing landed, your "
                    f"branch is untouched",
                    candidate_sha=self.candidate_sha,
                )
            if rc != 0:
                return LandResult(
                    "red",
                    f"canonical check failed (exit {rc}); nothing landed, your "
                    f"branch is untouched",
                    candidate_sha=self.candidate_sha,
                )
            return LandResult("green", "merge candidate verified",
                              candidate_sha=self.candidate_sha)
        finally:
            # Guaranteed teardown — swallow worktree-remove errors so the tempdir
            # cleanup ALWAYS runs even if the worktree registration was corrupted.
            try:
                self._git(self.main, "worktree", "remove", "--force", wt, check_rc=False)
            except Exception:
                pass
            self._git(self.main, "worktree", "prune", check_rc=False)
            shutil.rmtree(tmp, ignore_errors=True)

    # ---- phase 2: apply — land on green, under the ONE S2 lock --------------

    def apply(self) -> LandResult:
        if self.T_SHA is None or self.main_at_build is None:
            return LandResult("red", "apply() called before a green dry_run_diff()")
        try:
            with bookkeeping_lock(str(self.main), timeout=self.lock_timeout):
                # Guard the object we are about to mutate, BEFORE the clean-check:
                # on the wrong branch a clean/dirty verdict describes the wrong
                # working tree, so it answers a question we should not be asking yet.
                wrong = self._require_on_main("the land merge")
                if wrong is not None:
                    return wrong
                # Never mutate a dirty main checkout (would destroy a human's work).
                st = self._git(self.main, "status", "--porcelain", check_rc=False)
                if st.stdout.strip():
                    return LandResult(
                        "clean-abort",
                        f"the `{self.main_branch}` checkout has uncommitted changes; "
                        f"aborting to avoid destroying that work — commit/stash it "
                        f"and re-run",
                        candidate_sha=self.candidate_sha,
                    )
                # Staleness differentiation: bookkeeping-only advance still holds
                # (topic never touches those paths); a domain advance stales it.
                main_now = self._rev_parse(self.main_branch)
                if main_now != self.main_at_build:
                    changed = self._changed_paths(self.main_at_build, main_now)
                    domain = [p for p in changed if not is_shared(p)]
                    if domain:
                        return LandResult(
                            "stale",
                            f"the candidate verified green, but a concurrent domain "
                            f"change landed on `{self.main_branch}` and invalidated "
                            f"that result; nothing was landed and your branch is "
                            f"untouched — re-run to re-verify against the new "
                            f"`{self.main_branch}`",
                            candidate_sha=self.candidate_sha,
                            changed_paths=domain,
                        )
                    # else bookkeeping-only advance → safe to merge over it.
                # Worktree-consistent land: ref + index + working tree together.
                m = self._git(self.main, "merge", "--no-edit", "--no-ff", self.T_SHA,
                              check_rc=False)
                if m.returncode != 0:
                    self._git(self.main, "merge", "--abort", check_rc=False)
                    return LandResult(
                        "conflict",
                        f"the land merge conflicted (a gate-bypass domain edit in a "
                        f"bookkeeping path?); aborted, `{self.main_branch}` untouched",
                        candidate_sha=self.candidate_sha,
                    )
                merge_sha = self._rev_parse("HEAD")
                # Name the branch actually acted on. The guard above is what makes
                # this true rather than assumed — a fixed "landed to main" reported
                # success onto `main` no matter which branch HEAD pointed at.
                return LandResult("landed", f"landed to {self.main_branch}",
                                  merge_sha=merge_sha, candidate_sha=self.candidate_sha)
        except BookkeepingLockTimeout:
            return LandResult(
                "lock-timeout",
                f"timeout waiting for the `{self.main_branch}` lock — another writer "
                f"holds it; retry shortly (your branch is untouched)",
                candidate_sha=self.candidate_sha,
            )

    # ---- phase 3: promote — push, OUTSIDE the lock (minimal for S3) ----------

    def promote(self) -> LandResult:
        # Publishing is a mutation of a REMOTE, so it carries the same guard as a
        # mutation of the checkout — the refspec below removes the dependence on
        # HEAD, and this removes the dependence on the refspec being right forever.
        wrong = self._require_on_main("the push")
        if wrong is not None:
            return wrong

        # No-upstream detection is unchanged, deliberately: `noop-promote` maps to
        # exit 0 and handoff_resume.py:139 reports it as "green, landed", so a
        # correct land must not newly read as a stall.
        up = self._git(self.main, "rev-parse", "--abbrev-ref",
                       f"{self.main_branch}@{{upstream}}", check_rc=False)
        if up.returncode != 0 or not up.stdout.strip():
            return LandResult(
                "noop-promote",
                "no upstream configured; the local land is the S3 deliverable "
                "(the staging→prod deploy is S4's promotion instance)",
            )

        # Transmit the ref we just verified rather than whatever `push.default`
        # resolves. `push.default` is `simple` since git 2.0 (git-config(1):
        # "This mode is the default since Git 2.0"), and `simple` pushes the
        # CURRENT branch — so a bare `git push` publishes HEAD's branch while the
        # probe above verified `main_branch`. Two different objects.
        #
        # A naive `git push <remote> <main_branch>` is NOT the fix: that form
        # resolves the destination as `refs/heads/<main_branch>` on the remote, so
        # when `branch.<main>.merge` names a differently-named upstream it CREATES
        # a new remote branch where today's bare push would have refused. Derive
        # both halves and transmit the full `<local>:<upstream-ref>` refspec.
        remote = self._git(self.main, "config", "--get",
                           f"branch.{self.main_branch}.remote",
                           check_rc=False).stdout.strip()
        dest = self._git(self.main, "config", "--get",
                         f"branch.{self.main_branch}.merge",
                         check_rc=False).stdout.strip()
        if not remote or not dest:
            # `@{upstream}` resolved, so both keys should exist. Refuse rather than
            # fall back to a bare push: the fallback IS the defect being removed.
            return LandResult(
                "push-failed",
                f"`{self.main_branch}` has an upstream but its remote/merge config "
                f"could not be read (remote={remote or '<unset>'}, "
                f"merge={dest or '<unset>'}); refusing to fall back to a bare push, "
                f"which would publish whatever branch HEAD points at",
            )
        p = self._git(self.main, "push", remote, f"{self.main_branch}:{dest}",
                      check_rc=False)
        if p.returncode != 0:
            return LandResult(
                "push-failed",
                f"push of `{self.main_branch}` to {remote} {dest} rejected "
                f"(non-fast-forward?); reconcile with `git merge` and retry — the "
                f"push runs OUTSIDE the lock",
            )
        return LandResult("pushed", f"pushed `{self.main_branch}` to {remote} {dest}")

    # ---- rollback — inverse commit only (A10 / never reset --hard) -----------

    def rollback(self, merge_sha: str) -> LandResult:
        try:
            with bookkeeping_lock(str(self.main), timeout=self.lock_timeout):
                # Same two pre-mutation guards `apply()` carries. `rollback()` had
                # neither: it reverted into HEAD with no branch check and no
                # clean-check at all, so it could write an inverse commit onto the
                # wrong branch and over a human's uncommitted work.
                wrong = self._require_on_main("the revert")
                if wrong is not None:
                    return wrong
                st = self._git(self.main, "status", "--porcelain", check_rc=False)
                if st.stdout.strip():
                    return LandResult(
                        "clean-abort",
                        f"the `{self.main_branch}` checkout has uncommitted changes; "
                        f"aborting the revert to avoid destroying that work — "
                        f"commit/stash it and re-run",
                    )
                r = self._git(self.main, "revert", "-m", "1", "--no-edit", merge_sha,
                              check_rc=False)
                if r.returncode != 0:
                    self._git(self.main, "revert", "--abort", check_rc=False)
                    return LandResult(
                        "revert-conflict",
                        f"the revert conflicted; aborted, `{self.main_branch}` "
                        f"untouched — resolve manually and re-run",
                    )
                return LandResult("rolled-back",
                                  f"inverse commit created on {self.main_branch}",
                                  merge_sha=self._rev_parse("HEAD"))
        except BookkeepingLockTimeout:
            return LandResult(
                "lock-timeout",
                f"timeout waiting for the `{self.main_branch}` lock — retry the "
                f"rollback shortly",
            )


class PromotionAdapter(LandPort):
    """Harness staging→prod promotion behind the SAME land port (S4).

    Direction: source = the config source git repo (live ~/.claude edits are
    captured into it); target = the live ~/.claude filesystem (reached via a
    PATH-SCOPED the deploy step). Instances the promotion behind the ONE S3 port
    (A5 locked — do NOT split). Every harness-specific safety mechanic the frozen
    DESIGN pins (A8/A9/A10/A15) is implemented here and TESTED in
    test_promotion_s4.py — "prose can't run".

    Concurrency shape (DESIGN A9, Cycle-11/13/14/15):
      * dry_run_diff — in an ISOLATED ephemeral worktree of the source (pinned to
        source HEAD), run the session-scoped capture + DELETION-SAFE scoped
        staging (`git add -A -- <paths>`; a whole-tree unscoped `git add -A` is
        PROHIBITED), produce the diff, and build a FROZEN candidate commit. NO
        lock, NO shared-source mutation. The workspace is WORKER-owned and
        persists across the go/no-go — it is NOT torn down here (unlike the S3
        verify-then-land adapter, which completes in one call).
      * apply (post-"go") — under a SHORT source-lock, non-interactively
        git-merge the FROZEN commit onto the CURRENT source HEAD (post-approval
        TOCTOU reconcile). On a genuine conflict: `git merge --abort`, release,
        and surface — NEVER strand the shared source in MERGE_HEAD (which would
        block ALL future promotions). Commit, capture the approved SHA, release.
      * promote — reuse claude-promote's `.pr-mode`-gated tail IN ORDER
        (push → PR → merge; `quick` self-merges, `extra-safety` HOLDS with NO
        prod deploy) → THEN a SEPARATE deploy-mutex around ONLY the path-scoped
        the deploy step from the pinned/merged SHA → green tag. Deploy runs AFTER
        the merge; the network ops run OUTSIDE any lock.
      * rollback (A10) — source-lock → `git revert` (never `reset --hard`) →
        release → deploy-mutex → path-scoped re-apply of the reverted source.
        Atomic-or-nothing: a revert conflict aborts (`git revert --abort`) and
        leaves BOTH surfaces untouched (no REVERT_HEAD).

    STABILIZED LOCK PRINCIPLE (A2/A4): two SEPARATE short locks (source vs
    deploy), keyed on distinct repos; NEITHER spans the network push/PR nor the
    human go/no-go. Injectable overrides (`capture_cmd`, `apply_cmd`,
    `network_tail_cmd`, `green_tag_cmd`, `source_lock_target`,
    `deploy_lock_target`, `session_scope`, `trace`) let tests fake config-source + the
    network tail exactly as the S3 adapter's `check_cmd` is faked.
    """

    def __init__(
        self,
        source,
        *,
        session_scope: Optional[List[str]] = None,
        capture_cmd=None,          # callable(workspace, scope) | argv list — mutate the workspace worktree
        apply_cmd=None,            # callable(pinned_checkout, scope, target) | argv list — deploy to live
        network_tail_cmd=None,     # callable(pr_mode) -> "merged"|"hold"|"failed" — push→PR→merge tail
        green_tag_cmd=None,        # callable() | argv list — lay the green-<ts> recovery tag
        deploy_target: Optional[str] = None,
        source_lock_target: Optional[str] = None,
        deploy_lock_target: Optional[str] = None,
        pr_mode: str = "quick",
        main_branch: str = "main",
        lock_timeout: Optional[float] = None,
        tmp_root: Optional[str] = None,
        trace: Optional[list] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.source = Path(source).resolve()
        self.session_scope = list(session_scope) if session_scope else []
        self.capture_cmd = capture_cmd
        self.apply_cmd = apply_cmd
        self.network_tail_cmd = network_tail_cmd
        self.green_tag_cmd = green_tag_cmd
        self.deploy_target = deploy_target
        self.source_lock_target = source_lock_target or str(self.source)
        self.deploy_lock_target = deploy_lock_target or (deploy_target or str(self.source))
        self.pr_mode = pr_mode
        self.main_branch = main_branch
        self.lock_timeout = lock_timeout
        self.tmp_root = tmp_root
        self._trace = trace
        self._log = log or (lambda _m: None)

        # State set by dry_run_diff, reused by apply/promote.
        self.source_at_build: Optional[str] = None
        self.candidate_sha: Optional[str] = None
        self.candidate_diff: str = ""
        self.approved_sha: Optional[str] = None
        # WORKER-owned ephemeral workspace (persists across the go/no-go).
        self._td: Optional[tempfile.TemporaryDirectory] = None
        self._workspace: Optional[str] = None
        self._pinned_worktrees: List[str] = []

    # ---- git plumbing (mirrors VerifyThenLandAdapter; argv lists, never shell)

    def _git_env(self) -> dict:
        env = dict(os.environ)
        env["GIT_EDITOR"] = "true"
        env["GIT_PAGER"] = "cat"
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def _git(self, cwd, *args, check_rc: bool = True) -> subprocess.CompletedProcess:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, env=self._git_env(),
        )
        if check_rc and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed ({r.returncode}): {r.stderr.strip()}")
        return r

    def _rev_parse(self, ref: str, cwd=None) -> str:
        return self._git(cwd or self.source, "rev-parse", ref).stdout.strip()

    # ---- branch guard — the twin of VerifyThenLandAdapter's (A3) -------------
    #
    # The same defect in the more dangerous shape: `apply()` merges into HEAD and
    # then reads `approved_sha` from the `main_branch` REF, so on a parked source
    # checkout it reports "applied" with a SHA that was never merged — and
    # `promote()` then deploys that unmerged tree to live. It also had no
    # clean-check of any kind, which `VerifyThenLandAdapter.apply()` has carried
    # since it shipped.
    #
    # Assert, never `git checkout` — same rule, same reason (safe-defaults.md).

    def _head_branch(self, cwd=None) -> Optional[str]:
        """HEAD's branch name in the shared source, or None when detached.

        `symbolic-ref --short -q HEAD` for the same reason as the sibling adapter:
        `rev-parse --abbrev-ref HEAD` returns the literal "HEAD" when detached.
        """
        r = self._git(cwd or self.source, "symbolic-ref", "--short", "-q", "HEAD",
                      check_rc=False)
        return r.stdout.strip() or None

    def _require_on_main(self, action: str) -> Optional[LandResult]:
        """A refusing LandResult when the source HEAD is not on `main_branch`."""
        head = self._head_branch()
        if head is None:
            return LandResult(
                "detached-head",
                f"the shared source at {self.source} is on a detached HEAD, so "
                f"{action} would not reach `{self.main_branch}`; check "
                f"`{self.main_branch}` out there and re-run — the shared source is "
                f"untouched",
                candidate_sha=self.candidate_sha,
            )
        if head != self.main_branch:
            return LandResult(
                "wrong-branch",
                f"the shared source at {self.source} has `{head}` checked out, not "
                f"`{self.main_branch}`, so {action} would go to `{head}` while the "
                f"approved SHA would be read from `{self.main_branch}` — refusing; "
                f"the shared source is untouched",
                candidate_sha=self.candidate_sha,
            )
        return None

    # ---- seam guard — refuse an unwired worker before it touches anything ----
    #
    # These two seams have NO safe production default, and the required set is
    # derived from that fact rather than chosen: `network_tail_cmd` raises when
    # unset (`_run_network_tail`, with an in-comment rationale refusing a default)
    # and `green_tag_cmd` returns silently (`_run_green_tag`). `capture_cmd` and
    # `apply_cmd` are deliberately ABSENT: each has a real config-source default, so
    # requiring them would break a contract that works.
    #
    # At the single production construction site all four are None, so the worker
    # cannot finish — but it discovers that only inside `promote()`, which
    # `_promote_cli` reaches AFTER `apply()` has already merged and committed to
    # the shared source. The guard is what turns "advance shared state, then die"
    # into "refuse before anything is written".
    REQUIRED_SEAMS: tuple = ("network_tail_cmd", "green_tag_cmd")

    def _require_seams(self) -> Optional[LandResult]:
        """A refusing LandResult when a required seam is unset, else None.

        RETURNS, never raises. `_promote_cli` wraps every phase call in
        `try/finally` with no `except`, so a raised refusal would reach the
        operator as a traceback rather than as the named refusal promised here —
        which is why this mirrors the `no-scope` RETURN above and not the
        `RuntimeError` in `_run_network_tail`.
        """
        missing = [n for n in self.REQUIRED_SEAMS if getattr(self, n, None) is None]
        if not missing:
            return None
        return LandResult(
            "unwired-seams",
            "this promotion worker is not wired for production use — "
            + ", ".join(missing)
            + (" is" if len(missing) == 1 else " are")
            + " unset, so it could advance the shared source and then be unable to "
              "publish or deploy. Refusing before anything is written. Use "
              "`claude-promote`, which is the harness's wired staging→prod path "
              "(git-policy.md §2).",
        )

    def _record(self, event: str) -> None:
        if self._trace is not None:
            self._trace.append(event)
        self._log(f"[promote] {event}")

    # ---- injectable command dispatch ----------------------------------------

    def _run_capture(self, workspace: str) -> None:
        c = self.capture_cmd
        if c is None:
            # Production seam (build-time obligation, Design Review item 9):
            # session-scoped the capture step into the ephemeral source workspace.
            subprocess.run(
                ["config-source", "re-add", "--source", workspace, "--", *self.session_scope],
                check=True, env=self._git_env(),
            )
        elif callable(c):
            c(workspace, list(self.session_scope))
        else:
            subprocess.run([*c], cwd=workspace, check=True, env=self._git_env())

    def _run_apply(self, pinned_checkout: str, scope: List[str]) -> None:
        c = self.apply_cmd
        if c is None:
            # Production seam: PATH-SCOPED the deploy step from the pinned checkout.
            subprocess.run(
                ["config-source", "apply", "--source", pinned_checkout,
                 "--destination", str(self.deploy_target), "--", *scope],
                check=True, env=self._git_env(),
            )
        elif callable(c):
            c(pinned_checkout, list(scope), self.deploy_target)
        else:
            subprocess.run([*c], check=True, env=self._git_env())

    def _run_network_tail(self) -> str:
        c = self.network_tail_cmd
        if c is None:
            # Production: the worker delegates the push→PR→merge tail to
            # claude-promote's proven `.pr-mode`-gated flow (A4 reuse). No safe
            # default here — refuse rather than half-implement the network tail.
            raise RuntimeError(
                "network_tail_cmd required — the worker wires claude-promote's "
                "PR/merge tail; tests inject a fake"
            )
        if callable(c):
            return c(self.pr_mode)
        r = subprocess.run([*c], env=self._git_env())
        return "merged" if r.returncode == 0 else "failed"

    def _run_green_tag(self) -> bool:
        """True when a recovery-tag emitter actually ran; False when none is wired.

        The caller records the step ONLY on True. Moving the `_record` after this
        call is not sufficient on its own — an unconditional record placed after a
        no-op still asserts a `green-<ts>` tag that was never written, which is the
        defect, not its position in the source.
        """
        c = self.green_tag_cmd
        if c is None:
            return False
        if callable(c):
            c()
        else:
            subprocess.run([*c], env=self._git_env())
        return True

    def _materialize_pinned(self, sha: str, td_name: str) -> str:
        wt = str(Path(td_name) / f"pinned-{sha[:12]}")
        self._git(self.source, "worktree", "add", "--detach", wt, sha)
        self._pinned_worktrees.append(wt)
        return wt

    # ---- phase 1: dry_run_diff — session-scoped preview, FROZEN candidate -----

    def dry_run_diff(self) -> LandResult:
        if not self.session_scope:
            # Adapter defence-in-depth: a whole-tree sweep is NEVER the default.
            # Scope-absent handling (TTY list-and-confirm / non-TTY refuse) is the
            # worker's job (A3); the adapter refuses an empty scope outright.
            return LandResult(
                "no-scope",
                "no session-scope declared — refusing (a whole-tree sweep is "
                "prohibited; provide the session-scope paths)",
            )
        # A4: refuse an unwired worker HERE, not only on the mutating methods.
        # `_promote_cli` calls dry_run_diff() FIRST and never calls run(), so a
        # guard placed only on apply()/run() would sit downstream of everything
        # the production entry point actually reaches — `git worktree add` in the
        # shared source, the capture step against live, and a workspace commit.
        # Kept AFTER the scope refusal so that refusal keeps its precedence.
        unwired = self._require_seams()
        if unwired is not None:
            return unwired
        self.source_at_build = self._rev_parse(self.main_branch)

        # WORKER-owned ephemeral workspace — NOT torn down here (holds the FROZEN
        # candidate for apply()). Teardown is the worker's `finally` (teardown()).
        self._td = tempfile.TemporaryDirectory(prefix="promo-ws-", dir=self.tmp_root)
        wt = str(Path(self._td.name) / "src")
        self._workspace = wt
        # Isolated worktree of the source at the pinned HEAD (mirrors A6 isolation;
        # shared object DB so the FROZEN commit is mergeable by apply()).
        self._git(self.source, "worktree", "add", "--detach", wt, self.source_at_build)

        # Session-scoped capture: mutate the workspace worktree to reflect live edits.
        self._run_capture(wt)

        # DELETION-SAFE SCOPED staging (A9/M3): `git add -A -- <paths>` stages
        # adds/mods AND removals within the pathspec; a whole-tree unscoped
        # `git add -A` (no pathspec) is PROHIBITED.
        self._git(wt, "add", "-A", "--", *self.session_scope)

        staged = self._git(wt, "diff", "--cached", "--name-only").stdout
        changed = [p for p in staged.splitlines() if p.strip()]
        if not changed:
            return LandResult(
                "noop",
                "nothing to promote — staging matches live for this session's scope",
            )
        self.candidate_diff = self._git(wt, "--no-pager", "diff", "--cached").stdout
        # FROZEN candidate commit — retained in the workspace for apply().
        self._git(wt, "commit", "-m", "promotion candidate (frozen)")
        self.candidate_sha = self._rev_parse("HEAD", cwd=wt)
        return LandResult(
            "green", f"promotion candidate frozen ({len(changed)} path(s))",
            candidate_sha=self.candidate_sha, changed_paths=changed,
        )

    # ---- phase 2: apply — reconcile the FROZEN commit under the source-lock ---

    def apply(self) -> LandResult:
        # Guard at the METHOD boundary, not at the CLI: a CLI-level guard would
        # leave run() open, and its sufficiency would rest on there being exactly
        # one caller today — an argument from topology the next caller invalidates.
        unwired = self._require_seams()
        if unwired is not None:
            return unwired
        if self.candidate_sha is None or self.source_at_build is None:
            return LandResult("no-candidate", "apply() called before a green dry_run_diff()")
        try:
            with bookkeeping_lock(str(self.source_lock_target), timeout=self.lock_timeout):
                self._record("source_lock:acquire")
                # Guard BEFORE the merge at all costs: past it, the shared source has
                # advanced and `approved_sha` describes a different object than the
                # one that moved.
                wrong = self._require_on_main("the reconcile merge")
                if wrong is not None:
                    self._record("source_lock:release")
                    return wrong
                # Never mutate a dirty shared source — the sibling adapter has carried
                # this guard since it shipped; this one had none.
                st = self._git(self.source, "status", "--porcelain", check_rc=False)
                if st.stdout.strip():
                    self._record("source_lock:release")
                    return LandResult(
                        "clean-abort",
                        f"the shared source at {self.source} has uncommitted changes; "
                        f"aborting to avoid destroying that work — commit/stash it "
                        f"and re-run",
                        candidate_sha=self.candidate_sha,
                    )
                # Post-approval TOCTOU reconcile: the source may have advanced during
                # the (lockless) go/no-go — merge the FROZEN commit onto CURRENT HEAD.
                # NEVER re-read the live FS after approval (no second the capture step).
                self._record("reconcile")
                m = self._git(self.source, "merge", "--no-edit", self.candidate_sha,
                              check_rc=False)
                if m.returncode != 0:
                    # A9 Cycle-15: abort — never strand the shared source in MERGE_HEAD.
                    self._git(self.source, "merge", "--abort", check_rc=False)
                    self._record("source_lock:release")
                    return LandResult(
                        "conflict",
                        "the promotion candidate conflicts with a concurrent advance to "
                        "the shared source during the go/no-go; aborted — the shared "
                        "source is untouched (no MERGE_HEAD), resolve and re-promote",
                        candidate_sha=self.candidate_sha,
                    )
                self.approved_sha = self._rev_parse(self.main_branch)
                self._record("source_commit")
                self._record("source_lock:release")
                return LandResult(
                    "applied", "reconciled the frozen candidate + committed to the shared source",
                    merge_sha=self.approved_sha, candidate_sha=self.candidate_sha,
                )
        except BookkeepingLockTimeout:
            return LandResult(
                "lock-timeout",
                "timeout waiting for the source lock — another promotion holds it; retry",
                candidate_sha=self.candidate_sha,
            )

    # ---- phase 3: promote — .pr-mode tail (network, NO lock) → deploy-mutex ---

    def promote(self) -> LandResult:
        if self.approved_sha is None:
            return LandResult("no-candidate", "promote() called before a successful apply()")
        # Network tail runs OUTSIDE any lock (source-lock already released in apply;
        # deploy-mutex not yet taken) — no lock spans the network or PR review.
        outcome = self._run_network_tail()
        # Record what the tail ACTUALLY did. These two fired unconditionally BEFORE
        # the call, so on a `hold` (PR opened, deliberately not merged) or a
        # `failed` outcome the trail asserted a push and a PR merge that had not
        # happened — the same defect A7 fixed one step further down, left standing
        # one step up. G2's Desired State is that the trace records a step only
        # after that step has happened; this is the other half of it.
        if outcome in ("merged", "hold"):
            self._record("push")
        if outcome == "merged":
            self._record("pr_merge")
        if outcome == "hold":
            # extra-safety .pr-mode: PR open, external review required — NO prod
            # deploy until the PR merges externally (mirrors claude-promote:188-199).
            return LandResult(
                "hold",
                "extra-safety .pr-mode: PR opened, external review required — no prod "
                "deploy until the PR is merged externally",
                merge_sha=self.approved_sha,
            )
        if outcome != "merged":
            return LandResult(
                "push-failed", f"the network tail did not merge ({outcome}); no deploy",
                merge_sha=self.approved_sha,
            )
        # Deploy AFTER the merge, under a SEPARATE deploy-mutex around ONLY the apply.
        try:
            with bookkeeping_lock(str(self.deploy_lock_target), timeout=self.lock_timeout):
                self._record("deploy_lock:acquire")
                pinned = self._materialize_pinned(self.approved_sha, self._td.name)
                self._record("apply")
                self._run_apply(pinned, self.session_scope)
                self._record("deploy_lock:release")
        except BookkeepingLockTimeout:
            return LandResult(
                "lock-timeout",
                "timeout waiting for the deploy mutex — the merge is done; retry the deploy",
                merge_sha=self.approved_sha,
            )
        # A7: record the tag step AFTER it happened, and ONLY if it happened. This
        # line fired unconditionally BEFORE the seam, so on the production
        # configuration (`green_tag_cmd` unset → `_run_green_tag` a no-op) the audit
        # trail asserted a `green-<ts>` recovery tag that was never written — the
        # artifact git-policy.md §5 calls the known-good return point.
        if self._run_green_tag():
            self._record("green_tag")
        return LandResult(
            "promoted", "merged + path-scoped the deploy step deployed to live",
            merge_sha=self.approved_sha, changed_paths=list(self.session_scope),
        )

    # ---- rollback — A10 source-revert-then-reapply, atomic-or-nothing --------

    def rollback(self, merge_sha: str) -> LandResult:
        reverted: Optional[str] = None
        try:
            with bookkeeping_lock(str(self.source_lock_target), timeout=self.lock_timeout):
                self._record("source_lock:acquire")
                # G1: `rollback()` mutates a checkout too — it reverts into HEAD
                # (below) and then reads `reverted` from the `main_branch` REF and
                # deploys THAT to live, which is instance 1's exact shape. It carried
                # neither guard; `apply()` above carries both, and so does the sibling
                # adapter's rollback. No SEAM guard here, deliberately: rollback needs
                # neither required seam, so requiring them would refuse a call that
                # could legitimately proceed.
                wrong = self._require_on_main("the revert")
                if wrong is not None:
                    self._record("source_lock:release")
                    return wrong
                st = self._git(self.source, "status", "--porcelain", check_rc=False)
                if st.stdout.strip():
                    self._record("source_lock:release")
                    return LandResult(
                        "clean-abort",
                        f"the shared source at {self.source} has uncommitted changes; "
                        f"aborting the revert to avoid destroying that work — "
                        f"commit/stash it and re-run",
                    )
                # A merge-commit promotion needs `-m 1`; a fast-forward/single-parent
                # promotion must NOT get `-m` (git errors on a non-merge with -m).
                parents = self._git(self.source, "rev-list", "--parents", "-n", "1",
                                    merge_sha).stdout.split()
                revert_args = ["revert", "--no-edit"]
                if len(parents) > 2:            # sha + ≥2 parents → a merge commit
                    revert_args += ["-m", "1"]
                revert_args.append(merge_sha)
                r = self._git(self.source, *revert_args, check_rc=False)
                if r.returncode != 0:
                    # Atomic-or-nothing (Cycle-14): abort — BOTH surfaces untouched.
                    self._git(self.source, "revert", "--abort", check_rc=False)
                    self._record("source_lock:release")
                    return LandResult(
                        "revert-conflict",
                        "the rollback revert conflicted; aborted — BOTH the source and "
                        "the live target are untouched (no REVERT_HEAD), resolve and re-run",
                    )
                reverted = self._rev_parse(self.main_branch)
                self._record("source_commit")
                self._record("source_lock:release")
        except BookkeepingLockTimeout:
            return LandResult("lock-timeout", "timeout waiting for the source lock — retry the rollback")

        # Re-apply the reverted source to the live target (path-scoped), SEPARATE mutex.
        rb_td = None
        try:
            with bookkeeping_lock(str(self.deploy_lock_target), timeout=self.lock_timeout):
                self._record("deploy_lock:acquire")
                rb_td = tempfile.TemporaryDirectory(prefix="promo-rb-", dir=self.tmp_root)
                pinned = str(Path(rb_td.name) / "reverted")
                self._git(self.source, "worktree", "add", "--detach", pinned, reverted)
                self._record("apply")
                self._run_apply(pinned, self.session_scope)
                self._git(self.source, "worktree", "remove", "--force", pinned, check_rc=False)
                self._git(self.source, "worktree", "prune", check_rc=False)
                self._record("deploy_lock:release")
        except BookkeepingLockTimeout:
            return LandResult(
                "lock-timeout",
                "timeout waiting for the deploy mutex — the source is reverted; retry the re-apply",
                merge_sha=reverted,
            )
        finally:
            if rb_td is not None:
                rb_td.cleanup()
        return LandResult(
            "rolled-back", "source reverted (inverse commit) + reverted source re-applied to live",
            merge_sha=reverted,
        )

    # ---- worker-level teardown (guaranteed in the worker `finally`) ----------

    def teardown(self) -> None:
        """Remove every ephemeral worktree + the OS-managed tempdir. Idempotent
        and crash-safe (swallows worktree-registry errors). The WORKER owns this
        across every terminal path (go after apply+promote, no-go, crash) so no
        orphaned clone survives."""
        for wt in [self._workspace, *self._pinned_worktrees]:
            if wt:
                try:
                    self._git(self.source, "worktree", "remove", "--force", wt, check_rc=False)
                except Exception:
                    pass
        try:
            self._git(self.source, "worktree", "prune", check_rc=False)
        except Exception:
            pass
        if self._td is not None:
            try:
                self._td.cleanup()
            except Exception:
                pass
        self._td = None
        self._workspace = None
        self._pinned_worktrees = []

    # ---- run — non-interactive convenience (worker drives phases + go/no-go) --

    def run(self, *, approve: bool = True) -> LandResult:
        """dry_run_diff → (green + approve) apply → (applied) promote. The worker
        inserts the human go/no-go between dry_run_diff and apply; this convenience
        takes it as `approve`. The caller owns teardown() in a `finally`."""
        unwired = self._require_seams()
        if unwired is not None:
            return unwired
        dr = self.dry_run_diff()
        if dr.status != "green":
            return dr
        if not approve:
            return LandResult(
                "declined",
                "no-go — the ephemeral workspace is discarded; the shared source is untouched",
                candidate_sha=self.candidate_sha,
            )
        ap = self.apply()
        if ap.status != "applied":
            return ap
        return self.promote()


# ─────────────────────────────────────────────────────────────────────────────
# verify_deploy_candidate — deploy-path check: render via config-source FIRST, then
# check the RENDERED tree (S1, harness-land-model-adoption-miss).
#
# `VerifyThenLandAdapter.dry_run_diff()` above runs the canonical check inside
# the config-source SOURCE tree (`dot_claude/hooks/...`) — the wrong layout for
# `claude-verify`, which expects a rendered `~/.claude`-shaped tree, so that
# check is a vacuous pass for harness deploys. This is a NEW, reusable,
# additive check: it does not modify `dry_run_diff()`/`apply()`/`promote()`
# on either adapter above. It is invoked by the `claude-promote` bash tool
# via the `verify-deploy-candidate` CLI verb.
# ─────────────────────────────────────────────────────────────────────────────


def _deploy_check_git_env() -> dict:
    """Non-interactive git env — a standalone copy of the adapters' `_git_env`
    (deliberately NOT shared/refactored onto the classes above; S1 must leave
    the existing adapter internals untouched)."""
    env = dict(os.environ)
    env["GIT_EDITOR"] = "true"
    env["GIT_PAGER"] = "cat"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _deploy_check_git(cwd, *args, check_rc: bool = True) -> subprocess.CompletedProcess:
    r = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, env=_deploy_check_git_env(),
    )
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({r.returncode}): {r.stderr.strip()}")
    return r


def _deploy_check_sanitized_env() -> dict:
    """Same essential-vars allowlist as the adapters' `_sanitized_env` (kept as
    a standalone copy for the same reason as `_deploy_check_git_env`)."""
    return {k: os.environ[k] for k in DEFAULT_ENV_ALLOWLIST if k in os.environ}


def _is_vtl_deploy_dir(path: Path) -> bool:
    return path.name.startswith("vtl-deploy-")


def _safe_rmtree_deploy_dir(path) -> None:
    """MANDATORY safe-delete primitive for this function's own render dirs —
    NEVER a bare `rm -rf` / unscoped `shutil.rmtree` (`safe-defaults.md` bans
    `rm -rf`). Refuses unless `path` is a non-empty absolute path that exists
    AND whose own basename starts with `vtl-deploy-`, OR is literally the
    `dest` child of such a directory. Anything else is a silent no-op — this
    bounds every delete to a dir this module itself created."""
    if not path:
        return
    p = Path(path)
    if not p.is_absolute() or not p.exists():
        return
    is_own_root = _is_vtl_deploy_dir(p)
    is_dest_child = p.name == "dest" and _is_vtl_deploy_dir(p.parent)
    if not (is_own_root or is_dest_child):
        return
    shutil.rmtree(str(p), ignore_errors=True)


def _gc_old_deploy_render_dirs(tmp_root: Optional[str], retain: int) -> None:
    """GC bound (Deliverable 1 step 2): keep only the `retain` most-recent
    `vtl-deploy-*` dirs (by mtime) under `tmp_root` (or the OS default tempdir
    when unset); remove older ones via the safe primitive. Runs BEFORE a new
    render dir is created so repeated red runs can't accumulate unbounded
    preserved debug dirs."""
    base = Path(tmp_root) if tmp_root else Path(tempfile.gettempdir())
    if not base.is_dir():
        return
    try:
        candidates = [p for p in base.iterdir() if p.is_dir() and _is_vtl_deploy_dir(p)]
    except OSError:
        return
    if len(candidates) <= retain:
        return
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for stale in candidates[retain:]:
        _safe_rmtree_deploy_dir(stale)


def verify_deploy_candidate(
    *,
    source_dir: str,
    repo_dir: str,
    tip_sha: Optional[str] = None,
    main_at_build: Optional[str] = None,
    check_cmd: Optional[List[str]] = None,
    tmp_root: Optional[str] = None,
    notes_ref: str = "verify-then-land",
    write_note: bool = True,
    retain: int = 5,
    log: Optional[Callable[[str], None]] = None,
) -> DeployCheckResult:
    """Render `source_dir` (a config source/candidate checkout) via
    `the deploy step --source <source_dir> --destination <tmp>/dest --force`,
    then run `check_cmd` (default `claude-verify --phase pre`) against the
    RENDERED tree with `CLAUDE_VERIFY_TARGET=<tmp>/dest/.claude` — the correct
    layout for the check, unlike the in-source check `dry_run_diff()` runs.

    GREEN (config-source rendered clean AND the check exited 0): writes an
    idempotent git-note receipt (`notes --ref=<notes_ref> add -f`, so re-runs
    on the same `tip_sha` never error) in `repo_dir` when `write_note=True`,
    then cleans up the render dir.

    RED (config-source failed to render, OR the check exited non-zero, OR the check
    could not even be launched): writes NO note and PRESERVES the render dir
    for operator debugging. `retain` GC-bounds how many preserved red render
    dirs survive across repeated failures.

    Never writes to `repo_dir`'s working tree or index — only a git note."""
    _log = log or (lambda _m: None)
    cmd = list(check_cmd) if check_cmd else list(DEFAULT_CHECK_CMD)

    if tip_sha is None:
        tip_sha = _deploy_check_git(repo_dir, "rev-parse", "HEAD^{commit}").stdout.strip()

    # Step 2: GC old preserved failed render dirs BEFORE creating a new one.
    _gc_old_deploy_render_dirs(tmp_root, retain)

    # Step 3: create + validate the render dir before any cleanup can target it.
    render_dir = tempfile.mkdtemp(prefix="vtl-deploy-", dir=tmp_root)
    if not render_dir or not os.path.isdir(render_dir):
        return DeployCheckResult(
            verdict="red", tip_sha=tip_sha, note_written=False,
            rendered_dir=render_dir or None,
            detail="mkdtemp did not return a usable render directory",
        )

    # Step 4: fresh `dest` — wipe+recreate so residue from a prior run can
    # never cause a false validation.
    dest = str(Path(render_dir) / "dest")
    if Path(dest).exists():
        _safe_rmtree_deploy_dir(dest)
    os.makedirs(dest, exist_ok=True)

    # Step 5: render the candidate via config-source (argument list, never shell=True).
    _log(f"[deploy-check] the deploy step --source {source_dir} --destination {dest} --force")
    try:
        rendered = subprocess.run(
            ["config-source", "apply", "--source", str(source_dir), "--destination", dest, "--force"],
            capture_output=True, text=True,
        )
    except OSError as e:
        return DeployCheckResult(
            verdict="red", tip_sha=tip_sha, note_written=False, rendered_dir=render_dir,
            detail=f"the deploy step could not run ({e}); render dir preserved for debugging",
        )
    if rendered.returncode != 0:
        return DeployCheckResult(
            verdict="red", tip_sha=tip_sha, note_written=False, rendered_dir=render_dir,
            detail=(f"the deploy step failed (exit {rendered.returncode}): "
                    f"{rendered.stderr.strip()[:400]}; render dir preserved for debugging"),
        )

    # Step 6: run the canonical check AGAINST THE RENDERED TREE.
    check_env = _deploy_check_sanitized_env()
    check_env["CLAUDE_VERIFY_TARGET"] = str(Path(dest) / ".claude")
    _log(f"[deploy-check] {' '.join(cmd)} (CLAUDE_VERIFY_TARGET={check_env['CLAUDE_VERIFY_TARGET']})")
    try:
        chk = subprocess.run(cmd, cwd=dest, capture_output=True, text=True, env=check_env)
        rc = chk.returncode
    except OSError as e:
        return DeployCheckResult(
            verdict="red", tip_sha=tip_sha, note_written=False, rendered_dir=render_dir,
            detail=f"canonical check could not run ({e}); render dir preserved for debugging",
        )
    if rc != 0:
        return DeployCheckResult(
            verdict="red", tip_sha=tip_sha, note_written=False, rendered_dir=render_dir,
            detail=(f"canonical check failed (exit {rc}): {chk.stderr.strip()[:400]}; "
                    f"render dir preserved for debugging"),
        )

    # Step 7: GREEN — idempotent git-note receipt, then clean the render dir.
    note_written = False
    if write_note:
        payload = json.dumps({
            "tip_sha": tip_sha,
            "main_at_build": main_at_build,
            "check_cmd": " ".join(cmd),
            "verdict": "green",
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        _deploy_check_git(repo_dir, "notes", f"--ref={notes_ref}", "add", "-f", "-m", payload, tip_sha)
        note_written = True

    _safe_rmtree_deploy_dir(render_dir)
    return DeployCheckResult(
        verdict="green", tip_sha=tip_sha, note_written=note_written, rendered_dir=None,
        detail="deploy candidate rendered clean + canonical check green",
    )


# ─────────────────────────────────────────────────────────────────────────────
# CLI (verify-then-land.sh wraps this — A7 version-controlled entry)
# ─────────────────────────────────────────────────────────────────────────────

def _read_session_scope(scope_file: Optional[str]) -> List[str]:
    """Read the `_session_scope-<SID>.md` path list — one repo-relative path per
    non-empty, non-comment line. Missing/empty file → empty list (the worker then
    applies the scope-absent policy: TTY list-and-confirm, non-TTY refuse)."""
    if not scope_file:
        return []
    p = Path(scope_file)
    if not p.is_file():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _promote_cli(args) -> int:
    """Session-scoped harness promotion. Non-TTY + no session scope → REFUSE
    (fail-closed, A9/M4). The interactive TTY list-and-confirm gate for the
    scope-absent case is driven by promote-worker.sh."""
    scope = _read_session_scope(getattr(args, "session_scope", None))
    interactive = sys.stdin.isatty()
    if not scope:
        print("REFUSED: no session-scope declaration (a _session_scope-<SID>.md file "
              "is required); a whole-tree sweep is prohibited.", file=sys.stderr)
        if not interactive:
            print("REFUSED: non-interactive run with no session scope — fail-closed.",
                  file=sys.stderr)
        return 5

    adapter = PromotionAdapter(
        args.source, session_scope=scope,
        deploy_target=args.target,
        pr_mode=args.pr_mode, lock_timeout=args.lock_timeout,
        log=lambda m: print(m, file=sys.stderr),
    )
    try:
        dr = adapter.dry_run_diff()
        print(str(dr))
        if dr.status == "noop":
            return 0
        if dr.status != "green":
            return 3
        if adapter.candidate_diff:
            print("--- promotion preview (this session's scope) ---")
            print(adapter.candidate_diff)
        if args.assume_yes:
            go = True
        elif args.assume_no:
            go = False
        elif interactive:
            go = input("promote these files? [y/N] ").strip().lower() in ("y", "yes")
        else:
            print("REFUSED: promotion needs a go/no-go and no TTY is attached.", file=sys.stderr)
            return 5
        if not go:
            print("no-go — ephemeral workspace discarded; shared source untouched.")
            return 0
        ap = adapter.apply()
        if ap.status != "applied":
            print(str(ap), file=sys.stderr)
            return 3
        pr = adapter.promote()
        if pr.status == "promoted":
            print("LANDED: " + ", ".join(pr.changed_paths))
            return 0
        print(str(pr), file=sys.stderr)
        return 3
    finally:
        adapter.teardown()


def _verify_deploy_candidate_cli(args) -> int:
    """CLI verb for `verify_deploy_candidate` — invoked by the `claude-promote`
    bash tool as the deploy-path check. Exit codes: 0 = green, 5 = red,
    3 = usage/setup error (e.g. --repo is not a git repo). Note: argparse's
    own required-argument enforcement exits with ITS default code (2), not 3 —
    that enforcement happens before this function ever runs."""
    check_cmd = args.check_cmd.split() if args.check_cmd else None
    try:
        result = verify_deploy_candidate(
            source_dir=args.source,
            repo_dir=args.repo,
            tip_sha=args.tip_sha,
            main_at_build=args.main_at_build,
            check_cmd=check_cmd,
            tmp_root=args.tmp_root,
            notes_ref=args.notes_ref,
            write_note=not args.no_note,
            retain=args.retain,
            log=lambda m: print(m, file=sys.stderr),
        )
    except (RuntimeError, OSError, ValueError) as e:
        print(f"verify-deploy-candidate: usage/setup error: {e}", file=sys.stderr)
        return 3
    print(json.dumps({
        "verdict": result.verdict,
        "tip_sha": result.tip_sha,
        "note_written": result.note_written,
        "rendered_dir": result.rendered_dir,
        "detail": result.detail,
    }))
    return 0 if result.verdict == "green" else 5


def _cli(argv: Optional[List[str]] = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="land_port")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("verify-then-land", help="verify + land a topic branch into main")
    pv.add_argument("--repo", required=True, help="any path inside the git repo")
    pv.add_argument("--topic", required=True, help="topic branch/ref to land")
    pv.add_argument("--main", default="main", help="target branch (default: main)")
    pv.add_argument("--check-cmd", default=None,
                    help="canonical check command (default: claude-verify --phase pre)")
    pv.add_argument("--lock-timeout", type=float, default=None)

    prb = sub.add_parser("rollback", help="git revert -m 1 a landed merge")
    prb.add_argument("--repo", required=True)
    prb.add_argument("--merge-sha", required=True)
    prb.add_argument("--main", default="main")
    prb.add_argument("--lock-timeout", type=float, default=None)

    pp = sub.add_parser("promote", help="session-scoped harness staging→prod promotion")
    pp.add_argument("--source", required=True, help="any path inside the config source git repo")
    pp.add_argument("--target", required=True, help="the live deploy target dir (e.g. ~/.claude)")
    pp.add_argument("--session-scope", default=None,
                    help="path to the _session_scope-<SID>.md file (repo-relative paths, one per line)")
    pp.add_argument("--pr-mode", default="quick", choices=("quick", "extra-safety"))
    pp.add_argument("--lock-timeout", type=float, default=None)
    pp.add_argument("--assume-yes", action="store_true", help="non-interactive go (worker/tests)")
    pp.add_argument("--assume-no", action="store_true", help="non-interactive no-go (worker/tests)")

    pd = sub.add_parser("verify-deploy-candidate",
                        help="render a config-source candidate + run the canonical check against the RENDERED tree")
    pd.add_argument("--source", required=True, help="config source/candidate dir to render")
    pd.add_argument("--repo", required=True,
                    help="git repo the git-note receipt is written to (may equal --source)")
    pd.add_argument("--tip-sha", default=None, help="default: HEAD of --repo")
    pd.add_argument("--main-at-build", default=None, help="informational, recorded in the note payload")
    pd.add_argument("--check-cmd", default=None,
                    help="canonical check command (default: claude-verify --phase pre)")
    pd.add_argument("--no-note", action="store_true", help="do not write the git-note receipt on green")
    pd.add_argument("--notes-ref", default="verify-then-land")
    pd.add_argument("--tmp-root", default=None, help="parent dir for the mktemp render dir")
    pd.add_argument("--retain", type=int, default=5, help="GC bound on preserved red render dirs")

    args = ap.parse_args(argv)

    if args.cmd == "promote":
        return _promote_cli(args)

    if args.cmd == "verify-deploy-candidate":
        return _verify_deploy_candidate_cli(args)

    check_cmd = None
    if getattr(args, "check_cmd", None):
        check_cmd = args.check_cmd.split()

    adapter = VerifyThenLandAdapter(
        args.repo, getattr(args, "topic", ""),
        main_branch=args.main,
        check_cmd=check_cmd,
        lock_timeout=args.lock_timeout,
        log=lambda m: print(m, file=sys.stderr),
    )

    if args.cmd == "verify-then-land":
        res = adapter.run()
    else:
        res = adapter.rollback(args.merge_sha)

    print(str(res))
    # Exit codes: 0 landed/pushed/rolled-back/noop; 3 stale/red/conflict/clean-abort
    # (actionable, non-error — detect-and-bail so CI can't resolve a conflict); 4
    # lock-timeout/push-failed/revert-conflict (retryable).
    if res.status in ("landed", "pushed", "noop-promote", "rolled-back"):
        return 0
    if res.status in ("lock-timeout", "push-failed", "revert-conflict"):
        return 4
    return 3


if __name__ == "__main__":
    raise SystemExit(_cli())

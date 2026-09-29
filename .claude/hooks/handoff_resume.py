#!/usr/bin/env python3
"""handoff_resume — the A2/A8 cross-session handoff worker logic (S7 git-working-model).

The resume counterpart of `land_port.py promote`, executed out-of-process by
`handoff-worker.sh` (the A8 thin wrapper). Reconcile-don't-rebuild: it wires the
SHIPPED primitives — it does NOT fork placement, land, or topology:

  1. resolve the repo (the configured source path when --repo is omitted — harness binding);
  2. RESUME-PLACE the topic via `worktree-helper.sh place` (its `create-or-lookup`
     LOOKUP arm returns the EXISTING worktree idempotently — never a duplicate);
  3. read the ADVISORY land-readiness record (`land_readiness.read_readiness`);
  4. if `ready-to-verify-then-land`, route the land through the A6 gate
     `land_port.py verify-then-land` — the gate is authoritative and re-computes
     green/red at land time, so this is NEVER a bare merge;
  5. report exactly what it did.

Stdout discipline (QA-Lead review patch): the worktree path from `place` is captured
INTERNALLY (subprocess stdout → a variable); the child logs from `place` and
`verify-then-land` go to THIS worker's stderr; the worker's OWN stdout carries only
its final report. A caller must never capture this worker's stdout as a path — the
worktree path is obtained separately by the skill via `worktree-helper.sh place`.

Safe-defaults: no `stash drop` / `clean -f` / `reset --hard` / `--force` / blanket
`--no-verify` anywhere — the land is delegated whole to `land_port.py verify-then-land`
(which itself honors those bans). The worker runs fully non-interactive.

Exit map:
  0  resumed; if ready → landed / pushed / nothing-to-land
  3  ready but the A6 gate stalled the land (stale / red-check / conflict — actionable,
     not an error; the operator resolves in their active worktree and re-runs)
  4  ready but land retryable (lock-timeout / push-failed)
  1  resume-place failed / usage error
  2  resume-place HALTED before topology (dirty tree — the helper printed the bridge)

Usage:
  handoff_resume.py --topic <topic> [--repo <path>] [--repos-root <dir>]
      [--main <branch>] [--check-cmd <cmd>] [--lock-timeout <secs>]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(HOOKS))
import land_readiness  # noqa: E402  (sibling module in the same hooks dir)


def _log(msg: str) -> None:
    print(f"[handoff-worker] {msg}", file=sys.stderr)


def _report(msg: str) -> None:
    """The worker's ONLY stdout — its report. Never a path (QA-Lead patch)."""
    print(msg)


def _resolve_repo(repo: str | None) -> str:
    if repo:
        return repo
    # Harness repo-binding — same fallback the shipped surfaces use.
    r = subprocess.run(["false"]  # no config-source tool ships with Q, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        _log("no --repo and cannot resolve the configured source path (harness binding)")
        sys.exit(1)
    return r.stdout.strip()


def _resume_place(repo: str, topic: str, repos_root: str) -> str:
    """Call the SHIPPED `worktree-helper.sh place`. Its stdout is the worktree path
    ONLY (logs go to its stderr — worktree-helper `log()` writes >&2). We capture the
    path here; the child's stderr is streamed to OUR stderr, never our stdout."""
    helper = str(HOOKS / "worktree-helper.sh")
    r = subprocess.run(
        ["bash", helper, "place", "--repo", repo, "--topic", topic,
         "--repos-root", repos_root],
        capture_output=True, text=True,
    )
    if r.stderr:
        sys.stderr.write(r.stderr)
    if r.returncode == 3:
        _log("resume-place HALTED before topology (dirty tree — see helper bridge above)")
        sys.exit(2)
    if r.returncode != 0:
        _log(f"resume-place failed (rc={r.returncode})")
        sys.exit(1)
    dest = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    if not dest:
        _log("resume-place returned no worktree path")
        sys.exit(1)
    return dest


def _verify_then_land(repo: str, topic: str, main: str,
                      check_cmd: str | None, lock_timeout: str | None) -> int:
    """Route the land through the SHIPPED A6 gate. Its output is land logs → OUR
    stderr; we return only its exit code. NEVER a bare merge — the gate decides."""
    cmd = ["python3", str(HOOKS / "land_port.py"), "verify-then-land",
           "--repo", repo, "--topic", topic, "--main", main]
    if check_cmd:
        cmd += ["--check-cmd", check_cmd]
    if lock_timeout:
        cmd += ["--lock-timeout", str(lock_timeout)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    # Surface the gate's own report on stderr (it is a log, not a path).
    if r.stdout:
        sys.stderr.write(r.stdout)
    if r.stderr:
        sys.stderr.write(r.stderr)
    return r.returncode


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="handoff-worker")
    ap.add_argument("--topic", required=True, help="topic to resume")
    ap.add_argument("--repo", default=None, help="any path inside the git repo "
                    "(default: the configured source path — harness binding)")
    ap.add_argument("--repos-root", default=os.path.expanduser("~/repos"))
    ap.add_argument("--main", default="main")
    ap.add_argument("--check-cmd", default=None)
    ap.add_argument("--lock-timeout", default=None)
    args = ap.parse_args(argv)

    repo = _resolve_repo(args.repo)
    dest = _resume_place(repo, args.topic, args.repos_root)
    state = land_readiness.read_readiness(args.topic)
    _log(f"resumed topic '{args.topic}' in worktree: {dest} (land-readiness: {state})")

    if state != land_readiness.READY:
        _report(f"RESUMED: {args.topic} → {dest} (in-progress; no land attempted)")
        return 0

    _log("land-readiness=ready → routing the land through the A6 verify-then-land gate")
    rc = _verify_then_land(repo, args.topic, args.main, args.check_cmd, args.lock_timeout)
    if rc == 0:
        _report(f"RESUMED+LANDED: {args.topic} → {dest} (A6 gate: green, landed)")
        return 0
    if rc == 3:
        _report(f"RESUMED: {args.topic} → {dest} (A6 gate STOPPED the land — "
                f"stale/red/conflict; resolve in your active worktree and re-run)")
        return 3
    if rc == 4:
        _report(f"RESUMED: {args.topic} → {dest} (A6 land retryable — "
                f"lock-timeout/push-failed; re-run)")
        return 4
    _report(f"RESUMED: {args.topic} → {dest} (A6 land returned rc={rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())

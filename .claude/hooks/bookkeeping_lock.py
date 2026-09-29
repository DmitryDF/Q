#!/usr/bin/env python3
"""bookkeeping_lock — THE one short, local, blocking lock that serializes every
writer of the shared `main` bookkeeping copy (S2 git-working-model / A2).

STABILIZED LOCK PRINCIPLE (design A4, Cycle-9): the lock spans ONLY the local
critical section (a read-modify-write, or an append) and is strictly short. It
NEVER spans the network (`git push`/`fetch`/`pull`) or unbounded human input.
It is a **blocking** `fcntl.flock` (NOT LOCK_NB) so a concurrent write is never
dropped — mirroring the proven in-repo precedents `_factcheck_engine.py:2416`
and `work_done_journal.py:71` — with a **bounded acquire-timeout** (SIGALRM)
that surfaces to the operator instead of hanging forever. Python `fcntl.flock`,
NOT the shell `flock` command (absent on macOS — `track-plan-file.sh:77`).
Crash-safe: `flock` is OS-released on process death.

ONE lock per repo (keyed on the shared `git rev-parse --git-common-dir`), so
ALL writers of a repo's `main` checkout — the three code-writer RMWs
(taskmanagement.write_slice_row, work_done.write_retire_marker, the todo.py
command RMW) AND S3's future land — serialize under the SAME lock (the unified
`main`-writer rule, design A4/A6 Cycle-12). It is the reusable primitive S3's
land acquires.

Re-entrant within a process (refcount) so a wrapped writer never self-deadlocks
on an already-held lock; the cross-process guarantee is the single `flock` held
for the outermost acquisition.

Pure standalone check-logic (skill-location.md): no Claude-specific imports.
"""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

# Bounded acquire-timeout (design A4 "bounded-retry acquisition"). Short local
# critical sections finish in milliseconds; a multi-second wait means genuine
# contention that should surface, not hang. Env-overridable for tests.
DEFAULT_TIMEOUT = float(os.environ.get("BOOKKEEPING_LOCK_TIMEOUT", "10"))

# Per-process re-entrancy registry: resolved-lock-path -> (fd, count).
_REGISTRY_GUARD = threading.Lock()
_HELD: dict[str, list] = {}


class BookkeepingLockTimeout(TimeoutError):
    """Raised when the bounded acquire-timeout elapses before the lock is held."""


class _AlarmTimeout(Exception):
    pass


def _git_common_dir(cwd: Path) -> Optional[Path]:
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            p = Path(r.stdout.strip())
            if not p.is_absolute():
                p = (cwd / p)
            return p.resolve()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def lock_path_for(target) -> Path:
    """The lock file for a bookkeeping target.

    Prefer a per-repo lock at the SHARED git dir (worktree-agnostic — all
    worktrees + the primary tree share it), so every writer of the repo's
    `main` checkout serializes under one lock. Fall back to a per-file sidecar
    when the target is not inside a git repo (still serializes same-file
    writers).
    """
    t = Path(target)
    anchor = t if t.is_dir() else t.parent
    common = _git_common_dir(anchor)
    if common is not None:
        return common / "bookkeeping.lock"
    return t.with_name(t.name + ".bookkeeping.lock")


@contextmanager
def bookkeeping_lock(target, *, timeout: Optional[float] = None):
    """Hold THE bookkeeping lock for `target`'s repo across the critical section.

    Blocking + bounded (SIGALRM). Re-entrant within a process. Raises
    BookkeepingLockTimeout if the bounded timeout elapses before acquisition.
    """
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    lock_path = lock_path_for(target)
    key = str(lock_path)

    # --- re-entrant fast path: this process already holds it -----------------
    with _REGISTRY_GUARD:
        entry = _HELD.get(key)
        if entry is not None:
            entry[1] += 1
            reentrant = True
        else:
            reentrant = False
    if reentrant:
        try:
            yield
        finally:
            with _REGISTRY_GUARD:
                e = _HELD.get(key)
                if e is not None:
                    e[1] -= 1
                    if e[1] <= 0:
                        try:
                            fcntl.flock(e[0].fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
                        e[0].close()
                        _HELD.pop(key, None)
        return

    # --- outermost acquisition: take the real flock --------------------------
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a", encoding="utf-8")
    on_main_thread = threading.current_thread() is threading.main_thread()
    use_alarm = bool(timeout and timeout > 0 and on_main_thread)
    old_handler = None
    acquired = False
    try:
        if use_alarm:
            def _on_alarm(signum, frame):
                raise _AlarmTimeout()
            old_handler = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            acquired = True
        except (_AlarmTimeout, InterruptedError):
            raise BookkeepingLockTimeout(
                f"could not acquire bookkeeping lock {lock_path} within {timeout}s "
                "— another writer holds it; surface to operator and retry."
            )
        finally:
            if use_alarm:
                signal.setitimer(signal.ITIMER_REAL, 0)
                if old_handler is not None:
                    signal.signal(signal.SIGALRM, old_handler)

        with _REGISTRY_GUARD:
            _HELD[key] = [f, 1]
        try:
            yield
        finally:
            with _REGISTRY_GUARD:
                e = _HELD.get(key)
                if e is not None:
                    e[1] -= 1
                    if e[1] <= 0:
                        try:
                            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                        except OSError:
                            pass
                        f.close()
                        _HELD.pop(key, None)
                else:  # defensive: registry lost the entry
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                    f.close()
    except BaseException:
        if not acquired:
            f.close()
        raise


def bookkeeping_append(target, text: str, *, timeout: Optional[float] = None) -> None:
    """Lock-guarded append to an append-style bookkeeping file (Diary/Stats).

    The §8b append-coherence mechanism: because Diary/Stats are main-pinned (A1)
    and sparse-excluded from worktrees (A3), their appends land on main's ONE
    copy — a filesystem RMW race that `merge=union` alone (a merge-time driver)
    does not guard. Routing the append through THIS lock serializes two
    concurrent appenders so both are retained (no lost append).

    Correction (2026-09-17, glittery-humming-pine post-S8 audit): this docstring
    used to say "`/close` (S7) adopts this primitive for its Diary/Stats writes".
    It does not — `/close` references neither this function nor the lock, and
    the only caller in the harness is this module's own CLI. The primitive is
    available; that adoption never happened.
    """
    t = Path(target)
    with bookkeeping_lock(t, timeout=timeout):
        t.parent.mkdir(parents=True, exist_ok=True)
        with open(t, "a", encoding="utf-8") as fh:
            fh.write(text)


def _git_common_dir_for(repo_root) -> Optional[Path]:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            p = Path(r.stdout.strip())
            if not p.is_absolute():
                p = Path(repo_root) / p
            return p.resolve()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def install_merge_union_attributes(repo_root, lines: Optional[list[str]] = None,
                                   *, local: bool = True) -> str:
    """Idempotently add `<glob> merge=union` lines to a repo's git attributes.

    merge=union is git's built-in union merge driver — no driver registration
    needed. Applied to the append-list-shaped shared files (design A4 Cycle-11)
    so two branch-sides' appends reconcile without loss at a land/merge.

    local=True (default) writes to `<git-common-dir>/info/attributes` — a
    per-repo LOCAL attributes file git honors at merge time, requiring NO commit
    to `main` (the S2/harness-appropriate choice; the Projects-side tracked
    `.gitattributes` is S9). local=False writes the tracked `.gitattributes`.
    Idempotent: lines already present are left untouched.
    """
    if lines is None:
        try:
            import bookkeeping_paths
            lines = bookkeeping_paths.gitattributes_lines()
        except Exception:
            lines = []
    if not lines:
        return "merge-union: no append-style lines to install"
    if local:
        common = _git_common_dir_for(repo_root)
        if common is None:
            return "merge-union: not a git repo — skipped"
        target = common / "info" / "attributes"
    else:
        target = Path(repo_root) / ".gitattributes"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    existing_set = {ln.strip() for ln in existing}
    added = [ln for ln in lines if ln.strip() not in existing_set]
    if not added:
        return "merge-union: already up to date"
    out = list(existing)
    if out and out[-1].strip() != "":
        out.append("")
    out.append("# S2 git-working-model (A4/A2): union-merge the append-style shared")
    out.append("# bookkeeping files so concurrent branch-side appends reconcile at land.")
    out.extend(added)
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    return f"merge-union: added {len(added)} line(s) to {target}"


# Back-compat alias.
def install_gitattributes_merge_union(repo_root, lines: list[str]) -> str:
    return install_merge_union_attributes(repo_root, lines, local=False)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="bookkeeping_lock")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pa = sub.add_parser("append")
    pa.add_argument("target")
    pa.add_argument("text")
    pg = sub.add_parser("install-merge-union")
    pg.add_argument("--repo", required=True)
    pg.add_argument("--tracked", action="store_true",
                    help="write tracked .gitattributes instead of local .git/info/attributes")
    args = ap.parse_args()
    if args.cmd == "append":
        bookkeeping_append(args.target, args.text)
    elif args.cmd == "install-merge-union":
        print(install_merge_union_attributes(args.repo, local=not args.tracked))

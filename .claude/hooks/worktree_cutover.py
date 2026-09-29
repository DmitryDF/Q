#!/usr/bin/env python3
"""worktree_cutover — S6 dirty-cutover support: the greenfield DRAIN primitive +
the declared config-manifest / rebuild-command readers (git-working-model / A1,
A12; wraps the shipped S5 clean relocate).

The DRAIN is the crux of the transactional dirty cutover. It moves a DIRTY legacy
checkout's main-owned bookkeeping WORKING-TREE edits (TODO.md / Diary/ / Stats.md
/ Thoughts/ spines — the A4 manifest set) onto the `main` BRANCH, under the ONE
S2 bookkeeping flock (`bookkeeping_lock`), BEFORE any stash / worktree topology
change — so those edits never enter the stash and never collide in the
sparse-excluded topic worktree.

Divergence-safety (the load-bearing property):
  * The edits are captured as a commit `C_bk` whose tree is topic-HEAD's tree
    with ONLY the bookkeeping paths' working-tree content applied, parent =
    topic-HEAD. Its diff vs topic-HEAD is exactly the bookkeeping edits.
  * `C_bk` is integrated onto `main` with a REAL 3-way merge
    (`git merge-tree --write-tree --merge-base=<topic-HEAD> <main> <C_bk>`) that
    honors `merge=union` (installed via `install_merge_union_attributes`), so if
    `main` advanced the SAME append-style bookkeeping file since the topic
    branched, BOTH sides are preserved — never a clobber. Domain paths resolve to
    `main`'s side (C_bk didn't touch them), so no domain edit leaks onto `main`.
  * The merge result is committed with `git commit-tree` (parents [main, C_bk])
    and the `main` ref advanced with a compare-and-swap `git update-ref
    refs/heads/main <merge> <old-main>` — a worktree-consistent, two-parent merge
    commit, NOT a raw overwrite. All pure plumbing: no domain pre-commit hook
    runs, and the advance is bookkeeping-only so it passes the commit-gate by
    construction (never a blanket `--no-verify`).

After the drain the primary's tracked bookkeeping paths are reverted to HEAD with
`git restore --staged --worktree` (EDGE9), then the manifest path-set is asserted
clean over BOTH tracked mods AND untracked files. An untracked main-owned file is
handled FAIL-CLOSED BY PROVENANCE: removed only if its content provably already
matches `main`'s blob (a redundant leftover); ANY other untracked main-owned file
(user work that bypassed the drain) HALTS — the cutover NEVER `git clean -f`s a
path that could hold user work. A background writer that re-dirties a bookkeeping
path is re-drained under a bounded retry cap; on exhaustion the drain aborts
gracefully BEFORE any topology change.

Exit codes (consumed by worktree-helper.sh cmd_cutover_dirty):
  0   drained, or nothing to drain — the manifest path-set is clean; nothing moved
  9   EDGE9 halt-and-surface: an untracked main-owned file the drain did not
      produce, a genuine spine merge conflict, or the re-drain retry cap was
      exhausted — nothing moved (reason on stderr)
  12  EDGE12: could not acquire the drain lock within the bounded timeout —
      nothing moved
  2   usage / environment error

Pure standalone check-logic (skill-location.md): no Claude-specific imports.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

_HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS_DIR))

import bookkeeping_paths  # noqa: E402
import bookkeeping_lock  # noqa: E402


RETRY_CAP = int(os.environ.get("WORKTREE_DRAIN_RETRY_CAP", "3"))
CONFIG_FILE_NAME = os.environ.get("WORKTREE_CUTOVER_CONFIG", ".worktree-cutover.json")


class DrainHalt(Exception):
    """EDGE9 halt-and-surface: nothing moved, reason names what to resolve."""


def _git(repo, *args, check: bool = True, env: Optional[dict] = None):
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True,
        env=env if env is not None else os.environ,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {repo}: {r.stderr.strip()}"
        )
    return r


def _rev_parse(repo, ref) -> Optional[str]:
    r = _git(repo, "rev-parse", "--verify", "--quiet", ref, check=False)
    out = r.stdout.strip()
    return out or None


def bookkeeping_status(repo) -> list[tuple[str, str, bool]]:
    """(code, path, untracked) for every CHANGED main-owned bookkeeping path."""
    r = _git(repo, "status", "--porcelain", "-z", check=False)
    toks = r.stdout.split("\0")
    entries: list[tuple[str, str, bool]] = []
    i = 0
    while i < len(toks):
        tok = toks[i]
        if not tok:
            i += 1
            continue
        code = tok[:2]
        path = tok[3:]
        # rename/copy: the ORIG path is a separate NUL token — skip it.
        if code and (code[0] in "RC"):
            i += 1
        i += 1
        if not path:
            continue
        if bookkeeping_paths.is_shared(path):
            untracked = code == "??"
            entries.append((code, path, untracked))
    return entries


def _main_blob_matches(repo, path) -> bool:
    """True iff the on-disk file's content equals `main`'s blob at that path."""
    blob = _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/main:{path}",
                check=False).stdout.strip()
    if not blob:
        return False
    abspath = Path(repo) / path
    if not abspath.exists():
        return False
    h = _git(repo, "hash-object", str(abspath), check=False).stdout.strip()
    return bool(h) and h == blob


def _drain_tracked(repo, tracked: list[str], topic_head: str) -> bool:
    """Drain the tracked bookkeeping working-tree edits onto `main`. Returns True
    iff the `main` ref advanced. Raises DrainHalt on a genuine merge conflict."""
    if not tracked:
        return False
    main_sha = _rev_parse(repo, "refs/heads/main")
    if main_sha is None:
        raise RuntimeError("no `main` branch to drain bookkeeping onto")

    # 1. Build C_bk = topic-HEAD tree + tracked bookkeeping working-tree content.
    tmp_index = tempfile.NamedTemporaryFile(
        prefix="s6-drain-index-", delete=False)
    tmp_index.close()
    env = dict(os.environ, GIT_INDEX_FILE=tmp_index.name)
    try:
        _git(repo, "read-tree", topic_head, env=env)
        _git(repo, "add", "-A", "--", *tracked, env=env)
        bk_tree = _git(repo, "write-tree", env=env).stdout.strip()
    finally:
        try:
            os.unlink(tmp_index.name)
        except OSError:
            pass

    # No effective change (e.g. touched but identical) → nothing to commit.
    if _git(repo, "diff-tree", "--quiet", topic_head, bk_tree,
            check=False).returncode == 0:
        return False

    c_bk = _git(repo, "commit-tree", bk_tree, "-p", topic_head,
                "-m", "S6 cutover: bookkeeping drain (working-tree edits)").stdout.strip()

    # 2. Real 3-way merge onto `main` (honors merge=union). base = topic-HEAD.
    mt = _git(repo, "merge-tree", "--write-tree",
              f"--merge-base={topic_head}", main_sha, c_bk, check=False)
    if mt.returncode != 0:
        # A genuine conflict — a non-union (spine) file diverged on both sides.
        raise DrainHalt(
            "a main-owned bookkeeping file changed on BOTH `main` and this "
            "checkout in a way that cannot be auto-merged (a structured spine "
            "conflict). Reconcile it by hand on `main` first, then re-run. "
            f"Details:\n{mt.stdout.strip()}"
        )
    merged_tree = mt.stdout.strip().splitlines()[0]

    # 3. Advance `main` via a two-parent merge commit + CAS ref update.
    merge_commit = _git(
        repo, "commit-tree", merged_tree, "-p", main_sha, "-p", c_bk,
        "-m", "S6 cutover: drain bookkeeping into main").stdout.strip()
    cas = _git(repo, "update-ref", "refs/heads/main", merge_commit, main_sha,
               check=False)
    if cas.returncode != 0:
        # `main` moved under us (a concurrent writer despite the lock) — signal a
        # retry to the caller loop rather than clobbering.
        raise _CasRetry()

    # 4. Clean the drained tracked paths from the primary working tree + index.
    _git(repo, "restore", "--staged", "--worktree", "--", *tracked, check=False)
    return True


class _CasRetry(Exception):
    pass


def drain(repo) -> int:
    """Drain main-owned bookkeeping edits to `main` under the flock. Returns an
    exit code (0 clean, 9 halt, 12 lock timeout)."""
    repo = str(Path(repo).resolve())
    try:
        timeout = float(os.environ.get("BOOKKEEPING_LOCK_TIMEOUT", "10"))
        with bookkeeping_lock.bookkeeping_lock(repo, timeout=timeout):
            # merge=union must be installed so the drain merge preserves both sides.
            bookkeeping_lock.install_merge_union_attributes(repo)
            topic_head = _rev_parse(repo, "HEAD")
            if topic_head is None:
                print("[cutover-drain] cannot resolve HEAD", file=sys.stderr)
                return 2

            for _attempt in range(RETRY_CAP):
                entries = bookkeeping_status(repo)
                if not entries:
                    return 0  # clean — nothing (left) to drain
                untracked = [p for (_c, p, unt) in entries if unt]
                tracked = [p for (_c, p, unt) in entries if not unt]

                # (a) Fail-closed provenance on untracked main-owned files FIRST,
                #     before mutating `main` — so an unaccounted file halts with
                #     nothing moved. Redundant leftovers (already on `main`) are
                #     removed; anything else HALTS. NEVER `git clean -f`.
                halt = [p for p in untracked if not _main_blob_matches(repo, p)]
                if halt:
                    raise DrainHalt(
                        "untracked main-owned bookkeeping file(s) the drain did "
                        "not produce (possible unsaved work): "
                        + ", ".join(halt)
                        + ". Handle deliberately — commit via the sanctioned "
                        "writers, move, or remove — then re-run. (The cutover "
                        "never auto-deletes a path that could hold your work.)"
                    )
                for p in untracked:  # redundant leftovers → safe to remove
                    try:
                        (Path(repo) / p).unlink()
                    except OSError:
                        pass

                # (b) Drain the tracked bookkeeping edits onto `main`.
                try:
                    _drain_tracked(repo, tracked, topic_head)
                except _CasRetry:
                    continue  # main moved — re-scan and retry

                # (c) Re-scan; a background writer may have re-dirtied a path.
                if not bookkeeping_status(repo):
                    return 0
                # else loop (bounded) to re-drain

            # Retry cap exhausted — abort gracefully BEFORE any topology change.
            still = ", ".join(p for (_c, p, _u) in bookkeeping_status(repo))
            print(
                "[cutover-drain] EDGE9: a main-owned bookkeeping path keeps being "
                f"re-dirtied ({still}) after {RETRY_CAP} drain attempts — quiesce "
                "the writer, then re-run. Nothing moved.",
                file=sys.stderr,
            )
            return 9
    except bookkeeping_lock.BookkeepingLockTimeout as e:
        print(f"[cutover-drain] EDGE12: {e}", file=sys.stderr)
        return 12
    except DrainHalt as e:
        print(f"[cutover-drain] EDGE9: {e}", file=sys.stderr)
        return 9


# --- declared config-manifest + rebuild-command readers (EDGE1/EDGE5/EDGE7) ---

def _load_config(repo) -> dict:
    p = Path(repo) / CONFIG_FILE_NAME
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def config_manifest(repo) -> int:
    """Print one line per declared config entry: `<required 0|1>\\t<path>`.

    Declared in <repo>/.worktree-cutover.json:
      {"config_manifest": [{"path": ".env", "required": true}, ...],
       "rebuild_commands": ["npm ci", ...]}
    Absent/empty file → no output (the common case: nothing to carry)."""
    cfg = _load_config(repo)
    for e in cfg.get("config_manifest", []):
        path = str(e.get("path", "")).strip()
        if not path:
            continue
        req = "1" if e.get("required") else "0"
        print(f"{req}\t{path}")
    return 0


def rebuild_commands(repo) -> int:
    """Print one declared rebuild command per line (empty → none)."""
    cfg = _load_config(repo)
    for c in cfg.get("rebuild_commands", []):
        c = str(c).strip()
        if c:
            print(c)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="worktree_cutover")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pd = sub.add_parser("drain"); pd.add_argument("--repo", required=True)
    pc = sub.add_parser("config-manifest"); pc.add_argument("--repo", required=True)
    pr = sub.add_parser("rebuild-commands"); pr.add_argument("--repo", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "drain":
        return drain(args.repo)
    if args.cmd == "config-manifest":
        return config_manifest(args.repo)
    if args.cmd == "rebuild-commands":
        return rebuild_commands(args.repo)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

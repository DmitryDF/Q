#!/usr/bin/env python3
"""bookkeeping_resolver — THE ONE main-pinned resolver for shared bookkeeping
files (S2 git-working-model / A1).

Every reader and writer of a shared bookkeeping file (the set declared in the
A4 manifest, `bookkeeping_paths.py`) resolves its location through here, so it
targets the ONE shared `main` checkout regardless of which folder or linked
worktree the session is currently in. This is the structural fix for the
stale-private-copy hole S1's worktree model opened: from inside a topic
worktree the resolver returns `main`'s copy, never the worktree's frozen (and,
post-A3, sparse-excluded) one.

Detection reuses the ONE canonical S1 primitive `worktree-detect.sh` (the same
detector `pre_plan_gates._in_worktree()` wraps and the commit-gate calls) — no
second detection copy. The primary/`main` working tree is then resolved via
`git worktree list --porcelain` (git lists the main worktree first).

Behavior-preserving OUTSIDE any worktree: `main_checkout()` returns exactly what
`git rev-parse --show-toplevel` returns today, so the four legacy resolvers
(todo.find_vault_root, dashboard.find_vault_root, todo._resolve_target_todo
walk-up, pre_plan_gates._resolve_project_root) delegate here with no change to
their non-worktree results (equivalence-tested, V1).

Pure standalone check-logic (skill-location.md): no Claude-specific imports.

CLI (the documented resolver-backed convenience alias — never a stale shim):
  root [--cwd D]            print the `main` checkout root for D (default $PWD)
  path REL [--cwd D]        print REL re-anchored onto the `main` checkout
  cat  REL [--cwd D]        print `main`'s copy of a bookkeeping file
                            (the sanctioned interactive read that replaces a
                             raw `cat REL` inside a fail-closed worktree)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

_HOOKS_DIR = Path(__file__).resolve().parent
_DETECT_PRIMITIVE = _HOOKS_DIR / "worktree-detect.sh"


def _git(cwd: Path, *args: str, timeout: int = 5) -> Optional[str]:
    try:
        r = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0:
            return r.stdout
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def in_worktree(cwd: Optional[Path] = None) -> bool:
    """True iff cwd is inside a LINKED worktree — via the ONE S1 primitive.

    Exit contract of worktree-detect.sh: 0 = in worktree, 1 = not, 2 = error.
    Anything but a clean 0 → False (same discipline as _in_worktree()).
    """
    cwd = Path(cwd or Path.cwd())
    try:
        r = subprocess.run(
            ["bash", str(_DETECT_PRIMITIVE), str(cwd)],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False


def current_worktree_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """The toplevel of the worktree cwd is in (primary OR linked), or None."""
    cwd = Path(cwd or Path.cwd())
    out = _git(cwd, "rev-parse", "--show-toplevel")
    if out is None or not out.strip():
        return None
    try:
        return Path(out.strip()).resolve()
    except OSError:
        return None


def main_checkout(cwd: Optional[Path] = None) -> Optional[Path]:
    """The PRIMARY (`main`) working tree root for the repo cwd belongs to.

    - Outside a worktree: identical to `git rev-parse --show-toplevel` (the
      value the legacy resolvers used) — behavior-preserving.
    - Inside a linked worktree: the primary tree, taken from the FIRST entry of
      `git worktree list --porcelain` (git lists the main worktree first).

    None if cwd is not inside a git repo.
    """
    cwd = Path(cwd or Path.cwd())
    if not in_worktree(cwd):
        return current_worktree_root(cwd)
    out = _git(cwd, "worktree", "list", "--porcelain")
    if out:
        for line in out.splitlines():
            if line.startswith("worktree "):
                try:
                    return Path(line[len("worktree "):].strip()).resolve()
                except OSError:
                    return None
    # Fallback: derive the primary tree from the shared git dir's parent.
    common = _git(cwd, "rev-parse", "--git-common-dir")
    if common:
        cp = Path(common.strip())
        if not cp.is_absolute():
            cp = (cwd / cp)
        try:
            cp = cp.resolve()
        except OSError:
            return None
        if cp.name == ".git":
            return cp.parent
    return current_worktree_root(cwd)


def to_main(path, cwd: Optional[Path] = None) -> Path:
    """Re-anchor a repo-relative (or in-worktree absolute) path onto `main`.

    A repo-relative path is joined to the main checkout. An absolute path inside
    the CURRENT worktree is mapped to the same relative position on `main`; an
    absolute path elsewhere is returned unchanged (explicit user intent).
    """
    p = Path(path)
    main = main_checkout(cwd)
    if main is None:
        return p
    if not p.is_absolute():
        return main / p
    wt = current_worktree_root(cwd)
    if wt is not None and wt != main:
        try:
            rel = p.resolve().relative_to(wt)
            return main / rel
        except (ValueError, OSError):
            return p
    return p


def resolve(rel_path: str, cwd: Optional[Path] = None) -> Optional[Path]:
    """`main`-pinned absolute path for a repo-relative bookkeeping file."""
    main = main_checkout(cwd)
    if main is None:
        return None
    return main / rel_path


# ── Canonical Projects-root resolution by sentinel (storage-decouple S2 / C1+C6) ──
# Added by the claude-storage-decouple W2 plan. Resolves the ONE canonical
# Projects repo by its local-git-config SENTINEL — never a CWD/TODO walk-up (the
# stale-copy hole). Reuses main_checkout() above as the worktree-aware anchor, so
# there is ONE detection source, not a second copy (Cockburn Evolution test).

def projects_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """The canonical Projects root, resolved by the local-git-config sentinel.

    Contract (C1 zero-breakage + C6 clone-safety):
      * Reads the sentinel of the repo `cwd` belongs to:
        `projects.canonicalId` (a per-repo UUID) + `projects.canonicalPath`
        (the realpath of the canonical working-tree root). Both live in LOCAL
        git config, which `git clone` never carries.
      * REFUSES (returns None) if either key is absent — a clone fails closed.
      * REFUSES if this repo's own main-checkout realpath != canonicalPath — an
        iCloud collision copy (`Projects 2/`) or a stale-path copy carries the
        config but sits at a different realpath, so it fails closed too.
      * On success returns the `canonicalPath` VALUE — a RESOLVED PARAMETER, never
        a hardcoded literal (Guiding Policy: storage location is a resolved
        parameter). After the S3 move + symlink, a session at the symlinked
        `~/Projects` resolves (git dereferences the symlink) to the same realpath.
      * NEVER falls back to a CWD/TODO walk-up.
    """
    cwd = Path(cwd or Path.cwd())
    cid = _git(cwd, "config", "--get", "projects.canonicalId")
    cpath = _git(cwd, "config", "--get", "projects.canonicalPath")
    if not cid or not cid.strip():
        return None
    if not cpath or not cpath.strip():
        return None
    try:
        canonical = Path(cpath.strip()).resolve()
    except OSError:
        return None
    main = main_checkout(cwd)
    if main is None:
        return None
    try:
        if main.resolve() != canonical:
            return None
    except OSError:
        return None
    return canonical


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="bookkeeping_resolver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("root"); pr.add_argument("--cwd", default=None)
    pp = sub.add_parser("path"); pp.add_argument("rel"); pp.add_argument("--cwd", default=None)
    pcat = sub.add_parser("cat"); pcat.add_argument("rel"); pcat.add_argument("--cwd", default=None)
    prr = sub.add_parser("projects-root"); prr.add_argument("--cwd", default=None)
    args = ap.parse_args(argv)
    cwd = Path(args.cwd) if getattr(args, "cwd", None) else None

    if args.cmd == "root":
        m = main_checkout(cwd)
        if m is None:
            print("not a git repository", file=sys.stderr); return 2
        print(m); return 0
    if args.cmd == "path":
        r = resolve(args.rel, cwd)
        if r is None:
            print("not a git repository", file=sys.stderr); return 2
        print(r); return 0
    if args.cmd == "cat":
        r = resolve(args.rel, cwd)
        if r is None:
            print("not a git repository", file=sys.stderr); return 2
        if not r.exists():
            print(f"not found on main checkout: {r}", file=sys.stderr); return 1
        sys.stdout.write(r.read_text(encoding="utf-8"))
        return 0
    if args.cmd == "projects-root":
        r = projects_root(cwd)
        if r is None:
            print("refused: not the sentinel'd canonical Projects repo "
                  "(missing/mismatched projects.canonicalId/canonicalPath)",
                  file=sys.stderr)
            return 3
        print(r); return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

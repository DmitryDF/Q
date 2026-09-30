#!/usr/bin/env python3
"""commit_scope — which paths is this commit publishing, and did it say so?

This module answers ATTRIBUTION ("whose work is this?"). It is deliberately
distinct from `bookkeeping_paths`, which answers OWNERSHIP ("are these
main-owned paths?"). The two are composed by the pre-commit gate; neither
imports the other.

WHY IT EXISTS
-------------
Git's index is one file per worktree. A `git commit` that names no pathspec
commits whatever *any* session has staged, so two sessions working in one
tree publish each other's files under the wrong name. A scoped `git add` does
NOT bound a bare `git commit` — only a scoped commit does — and because
`git commit -- <untracked>` fails outright, BOTH verbs must be scoped. That
rule was re-derived independently by seven publish sites and most got it
wrong, which is why it lives here once instead of in seven places.

WHAT THIS MODULE IS AND IS NOT
------------------------------
It is how a publish SURFACE compiles and publishes a correct declaration.
It is NOT the gate. The pre-commit gate is pure bash and consults nothing
here: a ledger-consulting gate could be raced between the check and the
commit, so the gate stays a syntactic question ("was a scope declared?")
while this module helps a surface BUILD a declaration. If this module is
missing, surfaces stop publishing (fail-closed, manually recoverable with
`git add -A -- <paths> && git commit -- <paths>`); the bash gate still
refuses an undeclared publish.

CONCURRENCY CONTRACT
--------------------
* Takes NO lock and never makes a session wait on another.
* MUST NOT be called from inside a held `bookkeeping_lock`: that primitive's
  re-entrancy is per-process while flock binds per open-file-description, so
  a lock-holding parent that shells out to `publish` deadlocks until timeout.
* `record_write` appends one sub-PIPE_BUF line with O_APPEND and no lock, so
  concurrent writers interleave safely at line granularity.

Ships with `hooks/tests/probe_commit_styles.sh`, which measures the commit-form
matrix `classify_style` encodes. Re-run that probe before changing this rule.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, NamedTuple, Optional, Sequence

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

try:  # bookkeeping_resolver gives ONE canonical location per repo (worktree-aware)
    import bookkeeping_resolver as _resolver
except Exception:  # pragma: no cover - degraded mode, see _canonical_repo_root
    _resolver = None


# ══════════════════════════════════════════════════════════════════════════
# Index-style classification — the measured rule
# ══════════════════════════════════════════════════════════════════════════
#
# Derived from `hooks/tests/probe_commit_styles.sh`, whose header carries the
# full matrix. The two non-obvious rows, both MEASURED rather than reasoned:
#
#   `git commit -a`  -> GIT_INDEX_FILE basename `index.lock`  (NOT `index`)
#   `git commit -i`  -> GIT_INDEX_FILE basename `index.lock`
#
# Both are whole-tree sweeps: the hook sees every staged path, including a
# concurrent session's. A rule of "anything that is not exactly `index` is
# declared" — which is what the design originally specified — would classify
# `git commit -a` as a DECLARED publish. That is the one direction that must
# never happen, so `index.lock` is classified UNSCOPED here.
#
# `git commit -o/--only` and every pathspec form hand the hook a
# `next-index-<pid>.lock`, so `-o` and `-i` are correctly separated by the
# index name alone and no argv inspection is needed.

UNSCOPED = "unscoped"
SCOPED = "scoped"

#: Basenames that mean "this commit declared nothing". `index` is a bare
#: commit; `index.lock` is `-a`/`-i`/`--amend -a`. Both publish the whole
#: staged set.
_UNSCOPED_BASENAMES = frozenset({"index", "index.lock"})

#: A pathspec commit builds a throwaway index next to the real one. This is
#: the ONLY signature that means "git bounded this commit to named paths".
#:
#: `[0-9]`, deliberately NOT `\d`. Python's `\d` matches every Unicode decimal
#: digit, so `next-index-٠١` classified SCOPED here while the gate's bash
#: `*[!0-9]*` test called it UNSCOPED — Python being the permissive side, in a
#: pair whose whole point is that the two cannot disagree. Git writes ASCII
#: pids, so the narrower class is also the accurate one.
_SCOPED_RE = re.compile(r"^next-index-[0-9]+(\.lock)?$")


def classify_style(index_path: Optional[str]) -> str:
    """UNSCOPED or SCOPED, from the value of GIT_INDEX_FILE.

    SCOPED iff the basename is `next-index-*` — the signature git produces for
    a pathspec commit, and the only one that actually means "this commit was
    bounded to named paths". EVERYTHING else is UNSCOPED.

    An unset/empty value is UNSCOPED: git leaves it unset for some invocation
    paths, and "I could not tell" must resolve to the strict answer.

    *** The unrecognised-basename case is UNSCOPED for the SAME reason, and an
    earlier version of this function got it backwards. *** It returned SCOPED,
    on the reasoning that the only way to obtain an unrecognised name is a
    caller supplying its own private index (the mechanism `land_port.py` and
    `starter-kit` use), which is a declaration by other means. That reasoning
    is false as a categorical claim: a caller can point GIT_INDEX_FILE at any
    path, populate it with the whole tree (`git read-tree HEAD`), and run
    `git commit -a` against it. Git stages every modified tracked file into
    that index and commits them — a genuine whole-tree sweep — while the lock
    basename is `<custom>.lock`, which matched neither set and fell through to
    the permissive branch. That is precisely the direction this module exists
    to prevent, reached by the one branch that was not derived from the A1
    measurement. Found by an independent checker, not by the author.

    Consequence, stated rather than left implicit: a legitimate private-index
    publisher now reads as UNSCOPED. That is correct and intended. Such a
    publisher is out of scope *by mechanism* (its published content is not the
    live working tree), and the plan already covers it the honest way — by a
    named override (`ALLOW_UNSCOPED_COMMIT=1`) and a build-time allowlist entry
    carrying its reason — rather than by a syntactic guess this function cannot
    make correctly. A recorded exemption is a decision; a permissive fallback
    is an oversight.
    """
    if not index_path:
        return UNSCOPED
    # NO `.strip()`. It used to strip, and the bash gate did not — so
    # `…/next-index-12 ` was SCOPED here and UNSCOPED at the gate. The first fix
    # aligned them by teaching the GATE to strip, which is the wrong direction:
    # GIT_INDEX_FILE is caller-settable, so an index literally named
    # `next-index-1 ` would then read as DECLARED at the enforcement point,
    # loosening the one surface that must never call a sweep declared. Aligning
    # in the strict direction costs nothing — git does not write trailing
    # whitespace into its own index names.
    base = os.path.basename(index_path)
    if _SCOPED_RE.match(base):
        return SCOPED
    return UNSCOPED


# ══════════════════════════════════════════════════════════════════════════
# Sequencer state
# ══════════════════════════════════════════════════════════════════════════
#
# MEASURED, and it corrects the reason the design gave for this exemption.
# The design said git forbids a partial commit in every sequencer state, so a
# declaration is impossible there. That is true of merge and cherry-pick only:
#
#   merge       -> fatal: cannot do a partial commit during a merge.
#   cherry-pick -> fatal: cannot do a partial commit during a cherry-pick.
#   revert      -> SUCCEEDS, and correctly leaves other staged files alone
#   rebase      -> SUCCEEDS, likewise
#
# The exemption is still required for all of them, but for a different reason:
# `merge/cherry-pick/revert --continue` each run the hook with the SHARED
# index and no pathspec, so enforcing there leaves no compliant way to finish
# the operation. (`rebase --continue` runs no pre-commit hook at all.)
#
# This is a real hole, not a safe case: in the probe, `revert --continue`
# committed a foreign file a concurrent session had staged. It is accepted
# because the alternative strands a legitimate operation, and it is covered by
# the surfaces rather than the gate — no publish surface commits from a
# sequencer state.

_SEQUENCER_FILES = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
_SEQUENCER_DIRS = ("rebase-merge", "rebase-apply")


def sequencer_state(git_dir: os.PathLike | str) -> Optional[str]:
    """The in-progress sequencer operation, or None. Names the marker found.

    Routed through `_stat_ok` for the same reason the ledger functions are.
    This site was missed by the first pass of that guard, which covered the
    three predicates the defect report happened to name rather than every
    predicate in the module; a checker caught it. It is reachable from
    `publish` on a path under `.git`, which is the class the guard exists for.
    """
    gd = Path(git_dir)
    for name in _SEQUENCER_FILES:
        if _stat_ok((gd / name).is_file):
            return name
    for name in _SEQUENCER_DIRS:
        if _stat_ok((gd / name).is_dir):
            return name
    return None


# ══════════════════════════════════════════════════════════════════════════
# Verdict
# ══════════════════════════════════════════════════════════════════════════

class Verdict(NamedTuple):
    """The admissibility answer for one commit."""
    allowed: bool
    reason: str          # machine-readable slug
    message: str         # operator-facing sentence
    remediation: str     # the command that would publish correctly, or ""


def evaluate(
    *,
    index_path: Optional[str],
    staged_paths: Sequence[str] = (),
    sequencer: Optional[str] = None,
    override: bool = False,
) -> Verdict:
    """Is this commit an admissible publish?

    Mirrors the bash gate's decision so it can be unit-tested and so a surface
    can pre-check itself. The gate does NOT call this — it re-implements the
    same rule in bash, deliberately, so the guard survives python3 being
    unavailable.

    Order matters: the sequencer exemption is tested FIRST, ahead of the scope
    question, because in those states the operator has no compliant way to
    declare one.
    """
    if sequencer:
        return Verdict(True, "sequencer-exempt",
                       f"in-progress {sequencer}: scope not required", "")

    style = classify_style(index_path)
    if style == SCOPED:
        return Verdict(True, "declared", "commit declared its paths", "")

    if override:
        return Verdict(True, "override",
                       "unscoped commit allowed by explicit ALLOW_UNSCOPED_COMMIT=1", "")

    # Nothing staged -> nothing to declare. The gate has this branch and this
    # function did not, so `check` returned `undeclared` (exit 1) with an empty
    # path list for an `--allow-empty` commit the gate allows — a surface
    # pre-checking itself got the OPPOSITE answer from the enforcement point.
    # The plan's A3 cell protects this path explicitly ("Do not touch the
    # empty-staged path"). Found by an independent validator.
    #
    # ORDER MATTERS AND IS THE GATE'S ORDER: sequencer, then declared, then
    # override, then nothing-staged. Placing this ahead of the style test made a
    # SCOPED-but-empty commit report `nothing-staged` instead of `declared` —
    # caught immediately by an existing test, which is what that test is for.
    if not staged_paths:
        return Verdict(True, "nothing-staged",
                       "nothing staged: no paths to declare", "")

    shown = list(staged_paths)[:20]
    more = len(staged_paths) - len(shown)
    listing = "\n".join(f"    {p}" for p in shown)
    if more > 0:
        listing += f"\n    … and {more} more"
    quoted = " ".join(_shquote(p) for p in shown) or "<paths>"
    return Verdict(
        False,
        "undeclared",
        "This commit names no paths, so it would publish everything currently "
        "staged — including any other session's work:\n" + listing,
        f"python3 {_HOOKS_DIR}/commit_scope.py publish -m '<message>' -- {quoted}",
    )


def _shquote(s: str) -> str:
    return s if re.fullmatch(r"[A-Za-z0-9_./@%+:,-]+", s or "") else "'" + s.replace("'", "'\\''") + "'"


# ══════════════════════════════════════════════════════════════════════════
# Repo + ledger location
# ══════════════════════════════════════════════════════════════════════════

def _git(cwd, *args: str, timeout: int = 10) -> Optional[str]:
    try:
        r = subprocess.run(["git", "-C", str(cwd), *args],
                           capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    return r.stdout


def _canonical_repo_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """The ONE repo root a session's ledgers belong to, worktree-aware.

    Routed through `bookkeeping_resolver.main_checkout` so a session inside a
    linked worktree resolves to the SAME location as one in the primary tree.
    This is the whole point: `track-session-files.sh` derives its log dir from
    the hook payload's `.cwd`, so two sessions with different cwds write to
    different directories today and a cwd-relative reader would compare
    nothing at all.
    """
    # `Path.cwd()` calls os.getcwd(), which raises FileNotFoundError (an OSError)
    # when the process's working directory has been deleted underneath it. That
    # is a real condition for the background jobs and cleanup races these callers
    # run in, and it used to propagate straight out of `record_write` — breaking
    # the module's own "never raises" guarantee one line ABOVE the try block that
    # was supposed to hold it. Guarded here, at the single resolution locus, so
    # `record_write`, `compile_scope` and `co_writers` are all covered by one fix
    # rather than three. Found by an independent checker, not by the author.
    try:
        cwd = Path(cwd) if cwd is not None else Path.cwd()
    except OSError:
        return None
    if _resolver is not None:
        try:
            root = _resolver.main_checkout(cwd)
            if root is not None:
                return root
        except Exception:
            pass
    out = _git(cwd, "rev-parse", "--show-toplevel")
    if out and out.strip():
        try:
            return Path(out.strip()).resolve()
        except OSError:
            return None
    return None


def _stat_ok(check) -> bool:
    """Run a pathlib existence predicate, treating ANY OSError as False.

    Whether those predicates can raise is CPython-VERSION-DEPENDENT, and the
    measurement is recorded here because the reasoning that produced this
    helper was right about the risk and wrong about this interpreter:

      * CPython <= 3.12: `Path.exists()` routes through `pathlib._ignore_error`,
        which swallows only ENOENT, ENOTDIR, EBADF and ELOOP — so an EACCES (a
        stat that must traverse a directory without execute permission)
        propagates as PermissionError.
      * CPython 3.13+ (measured here on 3.14.6): `Path.exists()` delegates to
        `os.path.exists()`, which catches OSError broadly and returns False.
        (An earlier version of this note put the boundary at 3.12; the
        `os.path` fast path landed in 3.13. Off by one, in a docstring whose
        entire job is to be precise about which interpreters are affected.)
        Measured directly: with a ledger directory at mode 000, `os.stat` on a
        child raises PermissionError while `Path.is_file()` returns False
        without raising.

    So on THIS interpreter the bare predicates were already safe, and the
    defect an independent checker reported does not reproduce. The helper is
    kept anyway: the harness is not pinned to one CPython, the guarantee these
    functions advertise must not depend on which one is running, and the cost
    is a function call. What is NOT claimed is that it fixed a live bug here.
    """
    try:
        return bool(check())
    except OSError:
        return False


def _commit_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """The working tree a commit made from `cwd` would actually land in.

    *** THIS IS A DIFFERENT QUESTION FROM `_canonical_repo_root`, AND CONFLATING
    THE TWO WAS A REAL BUG. *** The ledger is shared, so it must resolve to ONE
    canonical location for the whole repo — that is `main_checkout`, and inside
    a linked worktree it deliberately returns the PRIMARY tree. The commit root
    is the opposite: it must be the tree the caller is standing in, because that
    is where their files are and which branch is checked out there.

    Using the canonical root for both meant that a session working inside a
    topic worktree would `git -C <primary-tree> add/commit` — staging and
    committing onto whatever branch `main` had out rather than its own topic
    branch — and `compile_scope` would relpath its worktree-absolute ledger
    entries against the primary root, get `../<topic>/…`, and drop every one of
    them as "outside this repo". Measured before the fix: `publish` from inside
    a worktree raised `pathspec did not match any files`, and `compile_scope`
    returned `[]` for paths the session had just written.

    That is precisely the configuration the Guiding Policy singles out as the
    one C2 must cover — `git-policy.md` §3 routes a resumed topic back into its
    existing worktree by design — so it was wrong exactly where it mattered
    most. Found by an independent validator, confirmed by running it.
    """
    try:
        cwd = Path(cwd) if cwd is not None else Path.cwd()
    except OSError:
        return None
    out = _git(cwd, "rev-parse", "--show-toplevel")
    if out and out.strip():
        try:
            return Path(out.strip()).resolve()
        except OSError:
            return None
    return None


def _dir_kind(p: Path) -> str:
    """"dir" | "not-dir" | "unknown" — and "unknown" is the point.

    `publish` must refuse a directory, because `git add -A -- <dir>` sweeps
    everything beneath it. The obvious spelling — `if p.is_dir()` — CANNOT fail
    closed on this interpreter, and an earlier comment here claimed the opposite
    while the invalidating fact sat 350 lines above it: from CPython 3.13 the
    pathlib predicates delegate to `os.path.*`, which catch OSError broadly and
    return False. So an unstat-able directory (a parent without `+x`) answers
    "not a directory", is admitted as a single file, and is then swept — exactly
    the fail-open the comment said it prevented.

    `os.stat` raises instead of guessing, which lets the three answers be told
    apart. ENOENT is deliberately "not-dir" rather than "unknown": a tracked
    file deleted from disk is a legitimate publish (it commits the deletion),
    and `git add`/`commit` report a genuinely bad path better than this can.
    """
    try:
        import stat as _stat
        return "dir" if _stat.S_ISDIR(os.stat(p).st_mode) else "not-dir"
    except FileNotFoundError:
        return "not-dir"
    except OSError:
        return "unknown"


def _nearest_existing_dir(p: Path) -> Optional[Path]:
    """The deepest existing directory at or above `p` (a file may have been
    written and then removed, or its parent created in the same breath)."""
    cur = p if _stat_ok(p.is_dir) else p.parent
    for _ in range(64):
        if _stat_ok(cur.is_dir):
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def ledger_dir(cwd: Optional[Path] = None) -> Optional[Path]:
    """The canonical `_session_files-*.log` directory for this repo.

    Deliberately the CANONICAL root, not the commit root: every worktree of a
    repo shares one ledger location, which is what lets `co_writers` compare
    two sessions at all.
    """
    root = _canonical_repo_root(cwd)
    return None if root is None else root / ".claude" / "logs"


def ledger_path(session_id: str, cwd: Optional[Path] = None) -> Optional[Path]:
    d = ledger_dir(cwd)
    return None if d is None else d / f"_session_files-{session_id}.log"


def current_session_id() -> Optional[str]:
    """This session's id, or None outside a session.

    Measured: CLAUDE_CODE_SESSION_ID was visible in every git-spawned
    pre-commit hook that RAN during the probe. Stated that way on purpose —
    the probe defines 17 forms plus four partial-commit recordings, and two of
    them (`merge_auto`, `rebase_continue`) run no pre-commit hook at all, so
    nothing was measured there and an "all N forms" count would be wrong in
    both the number and the claim. A surface running under git can rely on it;
    a path that fires no hook was never evidence either way.
    """
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    return sid or None


# ══════════════════════════════════════════════════════════════════════════
# record_write — the ledger writer (A5 wires the callers)
# ══════════════════════════════════════════════════════════════════════════

def record_write(path: os.PathLike | str,
                 session_id: Optional[str] = None,
                 cwd: Optional[Path] = None) -> bool:
    """Record that this session wrote `path`. Returns True if recorded.

    NEVER RAISES. These callers also run under pytest, from standalone CLI
    invocations, and from background jobs where no session exists; a hard
    failure would crash all three. A disk-full or permission error degrades to
    a warning for the same reason.

    Skipping is safe in the direction that matters: an unrecorded write simply
    is not declared, so the file stays dirty and uncommitted rather than being
    swept into someone else's commit. What this must never do is write an
    empty or placeholder session line, which would attribute one session's
    file to another — hence the early return on a missing id.
    """
    sid = (session_id or current_session_id() or "").strip()
    if not sid:
        return False
    try:
        p = Path(path).resolve()
    except OSError:
        return False

    # THE LEDGER BELONGS TO THE REPO OF THE FILE THAT WAS WRITTEN, not to the
    # process's working directory. Every code-layer writer (todo.py,
    # taskmanagement.py, work_done.py, pre_plan_gates.py, claims_registry.py,
    # _factcheck_engine.py) calls this with no `cwd`, and resolving from
    # `Path.cwd()` filed a write to repo X from a process standing in repo Y in
    # Y's ledger — or nowhere, from a cwd outside any repo — so the write went
    # undeclared and the co-writer notice missed it. An explicit `cwd` still
    # wins. Found by an independent post-S8 audit.
    #
    # ONE EXCEPTION: a file inside the frozen harness checkout (`~/.claude`,
    # itself a git repo). Resolving there filed harness writes in a ledger at
    # `~/.claude/.claude/logs/` that /close never reads or archives — it grew
    # unbounded, and `--harness-scope` (which projects the SESSION's project
    # ledger onto the managed config scope) never saw them. For those files the
    # process cwd — the session's project — is the right ledger, as it was before.
    # (Second independent audit of the D2 fix.)
    if cwd is None:
        cand = _nearest_existing_dir(p)
        frozen = _frozen_harness_root()
        if cand is not None and frozen is not None and _canonical_repo_root(cand) == frozen:
            cand = None                      # -> process cwd, the session's project
        cwd = cand

    target = ledger_path(sid, cwd)
    if target is None:
        print(f"[commit_scope] no repo for {p}; write not recorded", file=sys.stderr)
        return False

    line = f"{p}\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # O_APPEND + a single sub-PIPE_BUF line: concurrent writers interleave
        # safely at line granularity with no lock. A lock here would reintroduce
        # the deadlock this module's concurrency contract forbids.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError as exc:
        print(f"[commit_scope] could not record {p}: {exc}", file=sys.stderr)
        return False


# ══════════════════════════════════════════════════════════════════════════
# compile_scope — ledger -> a declaration this repo can actually commit
# ══════════════════════════════════════════════════════════════════════════

def compile_scope(session_id: Optional[str] = None,
                  cwd: Optional[Path] = None) -> list[str]:
    """This session's declared paths, repo-relative, sorted, deduped.

    Drops any ledger entry that neither exists on disk nor is known to git.
    That filter is load-bearing rather than tidiness: a scratch file written
    and deleted mid-session would otherwise fail the later
    `git add -- <unmatched pathspec>` and abort the WHOLE publish, losing every
    other path in the declaration. A tracked-but-now-deleted file IS known to
    git and is kept, so the deletion is published.

    Paths outside the repo are dropped — they cannot be committed here.
    """
    sid = (session_id or current_session_id() or "").strip()
    if not sid:
        return []
    # Paths are relativised against the COMMIT root (the tree the caller is in),
    # while the ledger is read from the CANONICAL root. See `_commit_root`.
    root = _commit_root(cwd)
    led = ledger_path(sid, cwd)
    if root is None or led is None or not _stat_ok(led.is_file):
        return []

    try:
        raw = led.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    candidates: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        entry = entry.strip()
        if not entry:
            continue
        try:
            ap = Path(entry)
            ap = ap if ap.is_absolute() else (root / ap)
            rel = os.path.relpath(str(ap.resolve(strict=False)), str(root))
        except (OSError, ValueError):
            continue
        if rel.startswith(".."):
            continue  # outside this repo
        if rel in seen:
            continue
        seen.add(rel)
        candidates.append(rel)

    if not candidates:
        return []
    return sorted(_keep_committable(root, candidates))


def _keep_committable(root: Path, rels: Sequence[str]) -> list[str]:
    """Keep a path iff it exists on disk OR git already knows it.

    Dropping is correct — an unmatched pathspec aborts the whole `git add`,
    losing every other path in the declaration — but a drop is ANNOUNCED
    rather than silent. `_stat_ok` folds any OSError into "does not exist", so
    a path that is really there but unreadable is indistinguishable here from
    a deleted one; if git does not know it either, it leaves the declaration
    and is never published while the caller is told the publish succeeded. For
    a module whose whole purpose is getting a session's own files published,
    that is the wrong failure to make quietly. Found by a checker.
    """
    on_disk = {r for r in rels if _stat_ok((root / r).exists)}
    missing = [r for r in rels if r not in on_disk]
    tracked: set[str] = set()
    if missing:
        out = _git(root, "ls-files", "-z", "--", *missing)
        if out:
            tracked = {p for p in out.split("\0") if p}
    kept = [r for r in rels if r in on_disk or r in tracked]
    for r in rels:
        if r not in kept:
            print(f"[commit_scope] dropped from declared scope — neither on disk "
                  f"nor known to git: {r}", file=sys.stderr)
    return kept


# ══════════════════════════════════════════════════════════════════════════
# co_writers — who else wrote what I am about to publish (C9)
# ══════════════════════════════════════════════════════════════════════════

#: Ledgers persist for days (`session-scope.sh` sweeps at 7 days), so an
#: abandoned or crashed session would otherwise raise a phantom co-writer
#: warning forever. Editorial: a few hours of ledger mtime means "live".
DEFAULT_LIVENESS_SECONDS = 6 * 3600


def co_writers(paths: Iterable[str],
               session_id: Optional[str] = None,
               cwd: Optional[Path] = None,
               max_age_seconds: int = DEFAULT_LIVENESS_SECONDS) -> dict[str, list[str]]:
    """{repo-relative path -> [other live session ids that also wrote it]}.

    Only paths with at least one other writer appear. Used to ANNOUNCE a joint
    edit before committing it (C9) — never to block, and never to try to
    separate the content, which for an interleaved append file is impossible.
    """
    sid = (session_id or current_session_id() or "").strip()
    root = _commit_root(cwd)          # relativise against the caller's own tree
    d = ledger_dir(cwd)               # but read the ONE shared ledger location
    wanted = {p for p in paths}
    if root is None or d is None or not wanted or not _stat_ok(d.is_dir):
        return {}

    now = time.time()
    found: dict[str, list[str]] = {}
    try:
        ledgers = sorted(d.glob("_session_files-*.log"))
    except OSError:
        return {}

    for led in ledgers:
        other = led.name[len("_session_files-"):-len(".log")]
        # `not sid` matters: with no session id, `other == sid` never holds and
        # the caller's OWN ledger was announced as "another live session". The
        # C9 notice is the one thing here that has to be trustworthy, so with no
        # id we cannot tell ours from theirs and report nothing at all.
        if not other or not sid or other == sid:
            continue
        try:
            if now - led.stat().st_mtime > max_age_seconds:
                continue  # not a live session
            lines = led.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for entry in lines:
            entry = entry.strip()
            if not entry:
                continue
            try:
                ap = Path(entry)
                ap = ap if ap.is_absolute() else (root / ap)
                rel = os.path.relpath(str(ap.resolve(strict=False)), str(root))
            except (OSError, ValueError):
                continue
            if rel in wanted:
                found.setdefault(rel, [])
                if other not in found[rel]:
                    found[rel].append(other)
    return {k: sorted(v) for k, v in sorted(found.items())}


# ══════════════════════════════════════════════════════════════════════════
# publish — stage and commit as ONE atom
# ══════════════════════════════════════════════════════════════════════════
#
# There is deliberately no `stage` verb. The split between staging and
# committing is representable-and-wrong, and it is the exact bug at two of the
# seven sites: both scoped the add, then let a bare commit sweep the tree. A
# verb that returns control between the two invites that mistake; one that
# cannot be split does not.

class PublishError(RuntimeError):
    pass


def publish(paths: Sequence[str],
            message: str,
            cwd: Optional[Path] = None,
            session_id: Optional[str] = None,
            dry_run: bool = False,
            announce: bool = True,
            stream=None) -> dict:
    """Stage and commit exactly `paths`, in one process. Returns a result dict.

    `git add -A -- <paths>` runs FIRST and always: `git commit -- <untracked>`
    fails with `pathspec did not match any file(s) known to git`, so an
    untracked file must be staged before the scoped commit can name it.

    The commit records WORKING-TREE content for the named paths, not whatever
    was staged earlier — which is what makes a jointly-edited append file
    carry both sessions' lines (C8) rather than dropping one set.
    """
    out = stream or sys.stderr
    # The COMMIT root: the tree the caller is standing in, so a session inside a
    # topic worktree commits to its own branch rather than onto `main`.
    root = _commit_root(cwd)
    if root is None:
        raise PublishError("not inside a git repository")

    # NEVER COMMIT INTO THE FROZEN HARNESS SNAPSHOT. `~/.claude/.git` is a frozen
    # local snapshot (git-policy.md §2): no hooks, and its only remote is
    # `config-source-remote-legacy`, so a commit there is invisible to the shared repo and
    # outside every gate. A `publish --repo <root of the edited file>` for a
    # `~/.claude` file resolved exactly there — found by an independent post-S8
    # audit of /ninja-fix. Harness changes go through `claude-promote --paths`.
    frozen = _frozen_harness_root()
    if frozen is not None and root == frozen:
        raise PublishError(
            f"refusing to commit into {root}: it is the frozen harness snapshot "
            "(git-policy.md §2 — its remote is config-source-remote-legacy and it carries no "
            "commit hooks). Publish harness changes with:\n"
            "  ~/.claude/bin/claude-promote -m \"<message>\" --paths \"<live ~/.claude paths>\"")

    rels = sorted({p for p in paths if p})

    # A declared DIRECTORY is refused. `git add -A -- Thoughts/` publishes every
    # concurrent session's artifact underneath it — the exact shape of the
    # `ffeeef23` and `213237fa` cross-attributions, and the case the plan's A6
    # cell singles out ("`Thoughts/` is excluded and must stay excluded"). A
    # module that presents itself as the thing that makes a declaration correct
    # must not accept the one declaration that silently un-scopes itself.
    # PATHSPEC HAZARDS ARE NEUTRALISED BY `:(literal)`, NOT BY REFUSING PATHS.
    #
    # The hazard is real: `git add -A -- '*.md'` or `':(glob)**'` sweeps every
    # match including another session's files. But the first attempt at this
    # refused any path containing `*`, `?` or `[` — which refuses 36 real files
    # in this very repository (`[YourCompany]/eGBR/[care] Roadmap.pdf` and friends), and
    # refuses the WHOLE declaration when one appears, so a compiled ledger scope
    # containing one such file would abort the publish and leave everything
    # uncommitted. The error even said "declare the individual files instead",
    # which was impossible.
    #
    # `:(literal)` is git's own answer: it turns off all pathspec magic and glob
    # expansion for that element. Measured: `:(literal)[care] Roadmap.pdf`
    # commits that exact file, and `:(literal)*.md` matches NOTHING rather than
    # sweeping. So prefixing every declared path both admits legitimate
    # filenames and makes a glob inert — strictly better than refusing either.
    #
    # Two things still refuse, because `:(literal)` does not help with them:
    #   * caller-supplied pathspec magic — we are the ones who add magic;
    #   * a directory (or symlink to one), which sweeps even when literal.
    bad: list[str] = []
    for r in rels:
        if r.startswith(":"):
            bad.append(f"{r} (caller-supplied pathspec magic)")
            continue
        kind = _dir_kind(root / r)
        if kind == "dir":
            bad.append(f"{r} (directory)")
        elif kind == "unknown":
            bad.append(f"{r} (cannot determine whether it is a directory)")
    if bad:
        raise PublishError(
            "refusing to publish a declared path that is not a single file: "
            + ", ".join(bad)
            + "\n  Each of these sweeps everything beneath or matching it, including\n"
              "  another session's files — the shape of the `ffeeef23` and\n"
              "  `213237fa` cross-attributions. Declare individual files instead.")


    # Git sees the literal form; the caller and every message keep the plain one.
    specs = [f":(literal){r}" for r in rels]
    if not rels:
        # Empty scope -> skip and report. NEVER fall back to a bare commit:
        # that is the defect this module exists to prevent.
        return {"status": "skipped", "reason": "empty-scope", "paths": [],
                "message": "nothing declared; no commit made"}

    seq = sequencer_state(_git_dir(root))
    if seq:
        raise PublishError(
            f"in-progress {seq}: finish or abort it before publishing "
            f"(a scoped publish during a sequencer operation is not supported)")

    # C9 — announce a joint edit BEFORE committing it, never after.
    joint = co_writers(rels, session_id=session_id, cwd=root) if announce else {}
    for path, others in joint.items():
        print(f"[commit_scope] NOTE: {path} was also written by "
              f"{len(others)} other live session(s): {', '.join(others)}.\n"
              f"               Committing it will carry their lines too — "
              f"this is expected for a shared append file.", file=out)

    if dry_run:
        return {"status": "dry-run", "paths": rels, "co_writers": joint,
                "stat": _scoped_stat(root, rels, specs)}

    add = subprocess.run(["git", "-C", str(root), "add", "-A", "--", *specs],
                         capture_output=True, text=True)
    if add.returncode != 0:
        raise PublishError(f"git add failed: {add.stderr.strip()}")

    commit = subprocess.run(
        ["git", "-C", str(root), "commit", "-m", message, "--", *specs],
        capture_output=True, text=True)
    if commit.returncode != 0:
        blob = (commit.stdout + commit.stderr).strip()
        if "nothing to commit" in blob or "no changes added" in blob:
            return {"status": "skipped", "reason": "no-changes", "paths": rels,
                    "message": blob}
        # THE ADD ALREADY RAN. The declared paths are staged in the shared index
        # and no commit took them. This is the same add-without-commit split the
        # module refuses to make representable as an API verb, reached instead by
        # the error path — and it is dangerous for exactly the reason the split
        # is: a later undeclared commit by any surface would sweep these staged
        # files. It is REPORTED rather than silently unwound, because the safe
        # unwind does not exist: `git reset -- <paths>` would also discard a
        # concurrent session's staging of the same path, destroying work to tidy
        # up a failure (safe-defaults.md). The operator is told precisely what is
        # staged and what to do. Found by an independent checker, not by the
        # author; covered by a test that fails the commit with a rejecting hook.
        raise PublishError(
            f"git commit failed AFTER staging: {blob}\n"
            f"  The index still holds these {len(rels)} declared path(s):\n"
            + "".join(f"    {p}\n" for p in rels)
            + "  They are staged and UNCOMMITTED. Either re-run the publish once\n"
              "  the cause is fixed, or unstage them yourself with:\n"
              f"    git -C {root} restore --staged -- {' '.join(_shquote(p) for p in rels)}\n"
              "  Do NOT run a bare `git reset` — it would also unstage any other\n"
              "  session's work that is staged in this shared index.")

    sha = (_git(root, "rev-parse", "HEAD") or "").strip()
    return {"status": "committed", "paths": rels, "sha": sha,
            "co_writers": joint, "message": commit.stdout.strip()}


def _frozen_harness_root() -> Optional[Path]:
    """The resolved `~/.claude` checkout, if it is a git repository.

    `COMMIT_SCOPE_FROZEN_ROOT` overrides the location for the test harness only."""
    raw = os.environ.get("COMMIT_SCOPE_FROZEN_ROOT") or str(Path.home() / ".claude")
    try:
        p = Path(raw).resolve()
    except OSError:
        return None
    return p if _stat_ok((p / ".git").exists) else None


def _git_dir(root: Path) -> Path:
    out = _git(root, "rev-parse", "--git-dir")
    if not out or not out.strip():
        return root / ".git"
    p = Path(out.strip())
    return p if p.is_absolute() else (root / p)


def _scoped_stat(root: Path, rels: Sequence[str],
                 specs: Optional[Sequence[str]] = None) -> str:
    """A --stat covering ONLY the declared paths.

    `/close` hands this to the commit-message composer. Its previous source
    was `git diff --cached --stat` over the whole shared index, which is why
    commit messages have been written about other sessions' staged work.

    Computed without touching the index so `--dry-run` stays side-effect free.
    """
    # Same `:(literal)` forms the publish will use, so the preview cannot
    # differ from what actually lands.
    sp = list(specs) if specs is not None else [f":(literal){r}" for r in rels]
    tracked = _git(root, "--no-pager", "diff", "--stat", "HEAD", "--", *sp) or ""
    out = _git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *sp)
    new = [p for p in (out or "").split("\0") if p]
    if new:
        tracked += "".join(f"\n {p} | (new file)" for p in new)
    return tracked.strip()


# ══════════════════════════════════════════════════════════════════════════
# CLI  (shape follows harness_code_paths.py:191-214)
# ══════════════════════════════════════════════════════════════════════════

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="commit_scope")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("style", help="classify GIT_INDEX_FILE as scoped/unscoped")
    ps.add_argument("index_path", nargs="?", default=os.environ.get("GIT_INDEX_FILE"))

    pc = sub.add_parser("scope", help="print this session's declared paths")
    pc.add_argument("--session-id", default=None)
    pc.add_argument("--repo", default=None)
    pc.add_argument("--co-writers", action="store_true",
                    help="also report other live sessions that wrote these paths")

    pk = sub.add_parser("check", help="evaluate a commit's admissibility")
    pk.add_argument("--index-path", default=os.environ.get("GIT_INDEX_FILE"))
    pk.add_argument("--repo", default=None)
    pk.add_argument("--override", action="store_true")

    pp = sub.add_parser("publish", help="scoped add AND scoped commit, one atom")
    pp.add_argument("-m", "--message", required=True)
    pp.add_argument("--repo", default=None)
    pp.add_argument("--session-id", default=None)
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--from-ledger", action="store_true",
                    help="declare this session's compiled scope instead of PATHS")
    pp.add_argument("paths", nargs="*")

    args = ap.parse_args(argv)
    cwd = Path(args.repo) if getattr(args, "repo", None) else None

    if args.cmd == "style":
        print(classify_style(args.index_path))
        return 0

    if args.cmd == "scope":
        rels = compile_scope(args.session_id, cwd)
        for r in rels:
            print(r)
        if args.co_writers:
            joint = co_writers(rels, args.session_id, cwd)
            for path, others in joint.items():
                print(f"# co-written: {path} <- {', '.join(others)}", file=sys.stderr)
        return 0

    if args.cmd == "check":
        # The COMMIT root: `check` must read the index of the tree the caller is
        # standing in, not the primary checkout's. See `_commit_root`.
        root = _commit_root(cwd)
        staged: list[str] = []
        seq = None
        if root is not None:
            out = _git(root, "diff", "--cached", "--name-only", "-z")
            staged = [p for p in (out or "").split("\0") if p]
            seq = sequencer_state(_git_dir(root))
        v = evaluate(index_path=args.index_path, staged_paths=staged,
                     sequencer=seq, override=args.override)
        print(v.reason)
        if not v.allowed:
            print(v.message, file=sys.stderr)
            print("\nPublish it correctly with:\n  " + v.remediation, file=sys.stderr)
            return 1
        return 0

    if args.cmd == "publish":
        paths = list(args.paths)
        if args.from_ledger:
            paths = compile_scope(args.session_id, cwd) or paths
        try:
            res = publish(paths, args.message, cwd=cwd,
                          session_id=args.session_id, dry_run=args.dry_run)
        except PublishError as exc:
            print(f"[commit_scope] {exc}", file=sys.stderr)
            return 1
        if res["status"] == "dry-run":
            print("\n".join(res["paths"]))
            if res["stat"]:
                print("\n--- scoped stat ---\n" + res["stat"])
            return 0
        if res["status"] == "skipped":
            print(f"skipped: {res['reason']}", file=sys.stderr)
            return 0
        print(f"committed {res['sha'][:12]} ({len(res['paths'])} path(s))")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())

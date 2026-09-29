#!/usr/bin/env bash
# worktree-detect.sh — THE ONE canonical git-native worktree-detection primitive.
#
# Slice S1 of git-working-model (A2). This is the single source of truth for
# "is <dir> inside a LINKED git worktree (not the primary working tree)?".
# Two consumers reach it — and ONLY it — so there is exactly one detector:
#   * pre_plan_gates._in_worktree()  — a thin Python wrapper that shells out here.
#   * check-worktree-commit-gate.sh  — the A3 pre-commit gate calls this directly
#                                       (no python3 dependency in the hook path).
#
# Detection (identical logic to the former inline _in_worktree at
# pre_plan_gates.py:411): `git rev-parse --git-common-dir` returns the SHARED
# .git dir; `--git-dir` returns the per-worktree git dir. They are equal in the
# primary tree and DIVERGE only inside a linked worktree. Both are canonicalized
# with realpath (mirrors the Python `(cwd / x).resolve()`), so a relative-vs-
# absolute spelling never yields a false verdict.
#
# Usage:  worktree-detect.sh [dir]      (dir defaults to $PWD)
#
# Exit codes (the contract both consumers depend on):
#   0  <dir> IS inside a linked worktree
#   1  <dir> is NOT inside a linked worktree (primary tree, or resolvable-equal)
#   2  detection could not run (not a git repo / git missing / rev-parse failed)
#
# The primitive is deployed at a STABLE absolute path (${KIT_HOOKS_DIR}/
# worktree-detect.sh) and every consumer resolves it by that fixed absolute
# path — never a $PATH / CWD-relative lookup — so it resolves reliably across
# all invocation contexts (terminal, GUI git, minimal-PATH hook).

set -uo pipefail

dir="${1:-$PWD}"

# Not a directory we can enter → detection cannot run.
[ -d "$dir" ] || exit 2

# git may be absent on a sparse hook PATH.
command -v git >/dev/null 2>&1 || exit 2

# Resolve both git dirs from inside <dir> so relative output is anchored there.
# Any rev-parse failure (not a git repo) → exit 2 (detection could not run).
common="$(cd "$dir" 2>/dev/null && git rev-parse --git-common-dir 2>/dev/null)" || exit 2
gitdir="$(cd "$dir" 2>/dev/null && git rev-parse --git-dir 2>/dev/null)" || exit 2
[ -n "$common" ] && [ -n "$gitdir" ] || exit 2

# Canonicalize both (mirrors Python .resolve()); a canonicalization failure is
# a genuine detection error, not a verdict.
common_abs="$(cd "$dir" 2>/dev/null && realpath "$common" 2>/dev/null)" || exit 2
gitdir_abs="$(cd "$dir" 2>/dev/null && realpath "$gitdir" 2>/dev/null)" || exit 2

if [ "$common_abs" != "$gitdir_abs" ]; then
  exit 0   # linked worktree
fi
exit 1     # primary tree

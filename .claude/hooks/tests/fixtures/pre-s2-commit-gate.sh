#!/usr/bin/env bash
# check-worktree-commit-gate — git pre-commit hook (git-working-model S1 / A3).
#
# The commit chokepoint. Blocks a `git commit` made OUTSIDE any topic worktree
# (the cross-topic interleaving the Diagnosis targets), tells the actor why +
# how to proceed, and yields to exactly ONE explicit non-interactive override.
# Never a hard stop, never a silent bypass, never a blanket --no-verify.
#
# Installed (by the A2 helper `worktree-helper.sh install`) as a `pre-commit`
# hook in the repo's shared hooks dir — either a direct symlink (no user hook)
# or invoked by the generated chaining wrapper (pre-existing user hook).
#
# Detection is delegated to THE ONE canonical primitive worktree-detect.sh
# (this gate's sibling in the hooks dir) — invoked DIRECTLY, no python3 in the
# hook path. The primitive's exit contract: 0 = in worktree, 1 = not, 2 = error.
#
# Structural allow-rule (S2/A5): a commit made OUTSIDE a worktree is NOT always
# topic interleaving — two legitimate main-side committers exist, both
# recognized structurally from the A4 machine-readable manifest
# (bookkeeping-paths.json, read via bookkeeping_paths.py — NEVER prose-scraped):
#   * a commit touching ONLY shared bookkeeping paths (TODO.md/Diary/Stats/
#     Thoughts spines) — allowed without prompting;
#   * any commit in the harness (config-source) PRIMARY clone — a promotion
#     surface with no topic-worktree workflow — allowed structurally, retiring
#     the claude-promote ALLOW_OUT_OF_TREE bridge.
# A MIXED commit (bookkeeping + domain) is NOT silently allowed — it falls
# through to block-with-override with guidance to split it.
#
# Override (positive opt-in, reusing the PUSH_TARGET_CHECK_SKIP convention at
# check-worktree-push-target.sh:35 — an env var, NOT a stdin prompt, because
# agent/GUI git have no TTY):
#   ALLOW_OUT_OF_TREE=1 git commit ...   — proceed despite being out-of-tree.
#
# Exit: 0 allow (in-worktree, override, or a recognized main-side committer);
#       1 block (out-of-tree / mixed / detection error) — git halts a commit
#       ONLY on a non-zero hook exit, so the gate MUST exit non-zero after the
#       message, not merely print it.

set -uo pipefail

# --- explicit override: named opt-in, checked first --------------------------
[ "${ALLOW_OUT_OF_TREE:-0}" = "1" ] && exit 0

# --- resolve the canonical detection primitive (this gate's sibling) ---------
# Resolve THIS script's real path (git invokes the hook via a symlink, or the
# chaining wrapper invokes us by a baked absolute path) → the hooks dir → the
# sibling primitive at its fixed absolute location. Never a $PATH / CWD lookup.
_self="$(realpath "$0" 2>/dev/null || echo "$0")"
_hooks_dir="$(cd "$(dirname "$_self")" 2>/dev/null && pwd)"
PRIM="$_hooks_dir/worktree-detect.sh"
MANIFEST_MODULE="$_hooks_dir/bookkeeping_paths.py"   # S2/A4 manifest reader

emit_block() {  # $1 = leading reason line
  cat >&2 <<EOF

[commit-gate] BLOCKED: $1
This commit is not being made inside a topic's own worktree, so it would land
on whatever branch is currently checked out — the cross-topic interleaving the
worktree-per-topic model exists to prevent.

How to proceed (pick one):

  1. Do the work in the topic's own worktree (the intended path):
       - Just start the topic — /work-start migrates you AUTOMATICALLY, whether
         the clone is CLEAN (S5 clean-relocate) or DIRTY (S6 transactional dirty
         cutover), carrying your uncommitted work with ZERO loss (staged stays
         staged, unstaged stays unstaged), draining your shared bookkeeping edits
         to main, copying required .gitignore'd config (.env-class), and rebuilding
         submodules / disposable dirs so the new worktree runs on first execution:
           /work-start
       - The dirty cutover HALTS before moving anything (nothing moved) if the
         tree is unsafe to carry — an in-progress merge / rebase / cherry-pick /
         revert / bisect (in the superproject or a submodule), a dirty submodule,
         a detached HEAD, or an active branch that is main — naming what to fix.
       - It uses git stash pop --index internally (the --index flag is MANDATORY:
         a bare pop unstages everything, erasing your curated staging). On the
         rare pop conflict your work is SAFELY in the new worktree and the cutover
         prints step-by-step recovery — it never rolls back, never a destructive
         retry.

  2. If this commit is legitimately out-of-tree (e.g. a named main-side
     infrastructure committer), opt in explicitly for THIS commit only:
           ALLOW_OUT_OF_TREE=1 git commit ...

EOF
  exit 1
}

emit_mixed_block() {  # a bookkeeping + domain MIXED commit on main
  cat >&2 <<EOF

[commit-gate] BLOCKED: this commit MIXES shared bookkeeping paths with domain
code on main. Shared bookkeeping (TODO.md / Diary/ / Stats.md / Thoughts/
spines) is main-owned and may be committed on main; domain code belongs in its
topic worktree. A commit that touches both would land topic domain work on main.

How to proceed (pick one):

  1. Split into two commits:
       - commit ONLY the shared bookkeeping paths on main (allowed with no
         override — the gate recognizes a bookkeeping-only commit);
       - commit the domain code inside the topic's own worktree (/work-start).

  2. If this mixed commit is deliberate, opt in explicitly for THIS commit only:
           ALLOW_OUT_OF_TREE=1 git commit ...

EOF
  exit 1
}

# --- run detection -----------------------------------------------------------
bash "$PRIM" "$PWD"
rc=$?

# In a topic worktree → always allow (the intended path).
[ "$rc" -eq 0 ] && exit 0

# Outside a worktree (rc=1). Before blocking, consult the A4 manifest allow-rule
# (S2/A5): recognize a bookkeeping-only commit, or a harness (config-source)
# promotion commit, structurally. Detection errors (rc=2) never reach the
# allow-rule — they fail safe to a block.
if [ "$rc" -eq 1 ] && [ -f "$MANIFEST_MODULE" ] && command -v python3 >/dev/null 2>&1; then
  REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
  if [ -n "$REPO_ROOT" ]; then
    VERDICT="$(git diff --cached --name-only -z 2>/dev/null \
                | python3 "$MANIFEST_MODULE" classify --repo "$REPO_ROOT" --stdin0 2>/dev/null)"
    case "$VERDICT" in
      harness|bookkeeping-only) exit 0 ;;         # recognized main-side committer
      mixed) emit_mixed_block ;;                   # bookkeeping + domain → split
      # none / empty → fall through to the ordinary out-of-tree block below.
    esac
  fi
fi

case "$rc" in
  1) emit_block "commit is outside any topic worktree." ;;
  *) emit_block "could not determine worktree membership (detection error)." ;;
esac

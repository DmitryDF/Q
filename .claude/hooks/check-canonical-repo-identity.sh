#!/usr/bin/env bash
# check-canonical-repo-identity.sh — fail-closed canonical-repo identity guard
# (claude-storage-decouple W2 / S2, C6 clone-safety).
#
# Generalizes the shipped check-worktree-push-target.sh config-vs-actual / block-
# with-named-fix pattern from "wrong UPSTREAM" to "wrong REPO": it refuses any
# git write (pre-commit + pre-push) whose repo does not carry the canonical
# Projects sentinel — i.e. `bookkeeping_resolver.py projects-root` refuses it.
# That single reused check (ONE detection source, no duplicated logic) fails
# closed for:
#   * a `git clone`            — local config not cloned → no projects.canonicalId
#   * an iCloud collision copy — carries the config but realpath != canonicalPath
#   * a stale/leftover copy    — same realpath-mismatch refusal
#
# Install (pre-commit + pre-push) on the canonical repo + its worktrees via the
# shipped worktree-helper.sh install topology (shared hooks dir).
#
# Escape hatch (positive opt-in only, mirrors PUSH_TARGET_CHECK_SKIP; never a
# blanket --no-verify): CANONICAL_REPO_CHECK_SKIP=1.

set -uo pipefail

[ "${CANONICAL_REPO_CHECK_SKIP:-}" = "1" ] && exit 0

# Resolve this script's REAL directory (follow symlinks — git installs the hook
# as a symlink under .git/hooks/), so we can find the sibling resolver.
_self="${BASH_SOURCE[0]}"
while [ -L "$_self" ]; do
  _link="$(readlink "$_self")"
  case "$_link" in
    /*) _self="$_link" ;;
    *)  _self="$(dirname "$_self")/$_link" ;;
  esac
done
HOOK_DIR="$(cd "$(dirname "$_self")" && pwd)"
RESOLVER="$HOOK_DIR/bookkeeping_resolver.py"

if [ ! -f "$RESOLVER" ]; then
  # Fail closed: cannot verify identity → refuse rather than allow blindly.
  echo "[canonical-repo] BLOCKED: identity resolver not found ($RESOLVER)." >&2
  echo "  Refusing the git write (fail-closed). Override: CANONICAL_REPO_CHECK_SKIP=1" >&2
  exit 1
fi

if root="$(python3 "$RESOLVER" projects-root --cwd "$PWD" 2>/dev/null)"; then
  # rc 0 → this IS the sentinel'd canonical repo (realpath matches canonicalPath).
  [ -n "$root" ] && exit 0
fi

# rc != 0 → missing / mismatched sentinel → not the canonical repo.
echo "[canonical-repo] BLOCKED: this is not the canonical Projects repo" >&2
echo "  (missing or mismatched projects.canonicalId / projects.canonicalPath)." >&2
echo "  Refusing the git write to protect against a clone / iCloud collision copy" >&2
echo "  ('Projects 2/') / stale-path copy." >&2
_cp="$(git config --get projects.canonicalPath 2>/dev/null || true)"
[ -n "$_cp" ] && echo "  Canonical path: $_cp" >&2
echo "  Override (only if you are certain this IS canonical): CANONICAL_REPO_CHECK_SKIP=1" >&2
exit 1

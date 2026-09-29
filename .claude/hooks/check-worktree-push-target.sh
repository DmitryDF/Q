#!/usr/bin/env bash
# check-worktree-push-target — git pre-push hook (S5: parallel-topic worktrees).
#
# Blocks a push whose target remote does not match the branch's configured
# upstream remote, and names the correct target so the operator knows where
# this branch should go.  Addresses G3: no wrong-target guard (see
# claude-infra-overhaul_PLAN.md A5).
#
# Repo opt-in (one-time per repo):
#   ln -sf ${KIT_HOOKS_DIR}/check-worktree-push-target.sh \
#           <repo-root>/.git/hooks/pre-push
#   chmod +x <repo-root>/.git/hooks/pre-push
#
# For a worktree repo the hooks/ dir is shared across all worktrees — one
# install covers every worktree of that repo.
#
# How it works:
#   $1 = remote name  $2 = remote URL  (standard git pre-push hook args)
#   stdin: <local-ref> <local-sha1> <remote-ref> <remote-sha1>  (one per ref)
#
#   For each branch ref being pushed, the hook reads the configured upstream
#   remote (git config branch.<name>.remote).  If an upstream is configured
#   AND it differs from the push target ($1), the push is blocked and the
#   correct target is printed.  Branches with no configured upstream are
#   allowed through (no constraint to enforce yet).
#
# Set the upstream when creating a worktree branch:
#   git branch --set-upstream-to=origin/<branch> <branch>
#   — or —
#   git push -u origin <branch>   (sets it on first push)
#
# Exit: 0 allow; 1 block (wrong target named); 2 misconfiguration.
#
# Env overrides (sandbox / migration escape hatches):
#   PUSH_TARGET_CHECK_SKIP=1     — bypass all checks (temporary migration use)
#   PUSH_TARGET_CHECK_VERBOSE=1  — print diagnostics on every allowed push too

set -uo pipefail

REMOTE_NAME="${1:-}"
REMOTE_URL="${2:-}"

[ "${PUSH_TARGET_CHECK_SKIP:-0}" = "1" ] && exit 0

[ -z "$REMOTE_NAME" ] && { printf '[push-target] ERROR: no remote name (not called as a pre-push hook?)\n' >&2; exit 2; }

log_verbose() {
  [ "${PUSH_TARGET_CHECK_VERBOSE:-0}" = "1" ] && printf '[push-target] %s\n' "$*" >&2 || true
}

die_wrong_target() {
  local branch="$1" expected="$2" got="$3"
  printf '\n[push-target] BLOCKED: wrong push target for branch "%s".\n' "$branch" >&2
  printf '  expected remote: %s  (configured via branch.%s.remote)\n' "$expected" "$branch" >&2
  printf '  attempted push to: %s\n' "$got" >&2
  printf '\n  Fix — push to the correct target:\n' >&2
  printf '    git push %s %s\n\n' "$expected" "$branch" >&2
  exit 1
}

while IFS=' ' read -r local_ref local_sha remote_ref remote_sha; do
  log_verbose "ref: $local_ref → $remote_ref (remote: $REMOTE_NAME)"

  # Skip delete-pushes (local sha is all-zeros)
  case "$local_sha" in
    0000000000000000000000000000000000000000) log_verbose "delete push for $local_ref — skipping"; continue ;;
  esac

  # Only enforce on branch refs; tags and other refs pass through
  case "$local_ref" in
    refs/heads/*) branch="${local_ref#refs/heads/}" ;;
    *)
      log_verbose "non-branch ref $local_ref — skipping"
      continue
      ;;
  esac

  # Look up the configured upstream remote for this branch
  expected_remote="$(git config "branch.${branch}.remote" 2>/dev/null || true)"

  if [ -z "$expected_remote" ]; then
    log_verbose "branch '$branch' has no configured upstream remote — allowing"
    continue
  fi

  if [ "$expected_remote" != "$REMOTE_NAME" ]; then
    die_wrong_target "$branch" "$expected_remote" "$REMOTE_NAME"
  fi

  log_verbose "branch '$branch' → remote '$REMOTE_NAME' (expected '$expected_remote') — OK"
done

exit 0

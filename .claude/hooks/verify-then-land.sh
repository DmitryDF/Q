#!/usr/bin/env bash
# verify-then-land — A7 version-controlled entry for the S3 verify-then-land gate
# (git-working-model). A THIN wrapper over land_port.py so the SAME script runs
# verbatim on a developer's machine AND in CI. Not a skippable client-side hook.
#
# Non-interactive by construction: land_port.py forces GIT_EDITOR=true /
# GIT_PAGER=cat and returns structured exit codes, so a conflict/red result is a
# detect-and-bail (exit 3) — CI, which cannot resolve a conflict, bails cleanly
# rather than hanging. Exit map (from land_port._cli):
#   0  landed | pushed | noop-promote | rolled-back
#   3  red | conflict | stale | clean-abort           (actionable, non-error)
#   4  lock-timeout | push-failed | revert-conflict    (retryable)
#
# Usage:
#   verify-then-land.sh --repo <path-in-repo> --topic <branch> [--main <branch>]
#                       [--check-cmd "<cmd>"] [--lock-timeout <secs>]
set -euo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${HOOKS_DIR}/land_port.py" verify-then-land "$@"

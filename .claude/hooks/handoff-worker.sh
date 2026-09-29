#!/usr/bin/env bash
# handoff-worker — A2/A8 out-of-process cross-session handoff worker (S7 git-working-model).
#
# The resume counterpart of promote-worker.sh. A THIN wrapper over
# `handoff_resume.py` (which itself only WIRES the shipped primitives — it removes
# the human as a fragile relay: it resolves the repo, resume-places the topic into
# its EXISTING worktree via `worktree-helper.sh place`, reads the ADVISORY
# land-readiness record, and — if the topic was left ready — routes the land through
# the A6 gate `land_port.py verify-then-land` (never a bare merge), all in a
# controlled environment OUTSIDE the producer's process tree (DESIGN A8/A14).
#
# Verifier-isolation arming (A8): like promote-worker.sh, the worker's run must be
# snapshotted by `check-verifier-isolation.sh`. That guard only ARMS on an
# installer-class command, so the canonical invocation carries a `: bootstrap-<tag>`
# no-op token; the leading token below arms the pre/post pair for the whole run.
#
# Stdout discipline (QA-Lead review patch): the worker's stdout is its REPORT only.
# A caller must NOT capture this worker's stdout as a path — the worktree path is
# obtained separately by the skill via `worktree-helper.sh place` (whose stdout is
# path-only). handoff_resume.py sends all child logs (place + verify-then-land) to
# stderr and prints only its final `RESUMED…`/`RESUMED+LANDED…` line to stdout.
#
# Exit map (from handoff_resume.py):
#   0  resumed (+ landed / nothing-to-land)   3  ready but A6 gate stopped the land
#   4  ready but land retryable               1  resume-place failed / usage
#   2  resume-place halted (dirty tree)
#
# Usage:
#   handoff-worker.sh --topic <topic> [--repo <path>] [--repos-root <dir>] \
#       [--main <branch>] [--check-cmd <cmd>] [--lock-timeout <secs>]
set -uo pipefail

: bootstrap-handoff-worker   # no-op arming token (see header) — arms the
                             # verifier-isolation pre/post pair for this run.

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Controlled environment (A8): non-interactive git, deterministic locale — so the
# worker never fails from a wrong-directory / missing-PATH / broken-comment / editor
# / pager relay error, and any land held-lock can never be deadlocked by an editor.
export GIT_EDITOR=true
export GIT_PAGER=cat
export GIT_TERMINAL_PROMPT=0
export LC_ALL="${LC_ALL:-C}"

exec python3 "${HOOKS_DIR}/handoff_resume.py" "$@"

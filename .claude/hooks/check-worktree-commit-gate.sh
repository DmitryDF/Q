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
#     the config-promote ALLOW_OUT_OF_TREE bridge.
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

# ─── S2/A3 + S6/A8: the SCOPE stage (declared-publish-scope) ─────────────────
#
# ENFORCE MODE since S6 (A8). An UNSCOPED commit with something staged is
# REFUSED (exit 1) with the offending paths, the declared-publish command, and
# the named override. It was built in S2 as WARN mode — report and fall through,
# no exit path changed — and flipped only after every publish surface was
# converted (S3 config-promote, S4 /close, S5 execute-plan, /ninja-fix,
# starter-kit), because an enforcing gate landing first would have broken every
# /close in both repos.
#
# ROLLBACK is one line: set SCOPE_MODE=warn below. There is deliberately NO
# environment variable that selects the mode — a caller-settable mode switch
# would be a second, untraced override next to ALLOW_UNSCOPED_COMMIT, and only
# the named override leaves a trailer in history (C7).
SCOPE_MODE="enforce"   # enforce | warn
#
# WHY THE ORDER CHANGED. `ALLOW_OUT_OF_TREE=1` used to be the very first test
# in this file. It now sits AFTER the scope stage, deliberately: checked first,
# `ALLOW_OUT_OF_TREE=1 git commit` would yield an unscoped commit that is
# neither warned about nor (at S6) refused nor trailered — an untraced path for
# the exact defect this stage exists to catch, via an idiom `config-promote`
# documents as its retired bridge. That variable governs WORKTREE PLACEMENT;
# keeping the two overrides genuinely separate requires it to stop
# short-circuiting the scope test. The scope stage likewise runs ahead of the
# in-worktree exit and the manifest allow-rule, because a linked worktree's
# index is isolated per WORKTREE, not per session — `git-policy.md` §3 routes a
# resumed topic back into its existing worktree by design, so two sessions
# sharing one index happens inside worktrees too.
#
# THE DISCRIMINATOR IS MEASURED, NOT ASSUMED. See the matrix recorded in
# hooks/tests/probe_commit_styles.sh. SCOPED iff the GIT_INDEX_FILE basename is
# `next-index-<pid>` — the signature git creates for a pathspec commit. `-a`,
# `-i` and `--amend -a` all hand this hook `index.lock` and stage the whole
# tree, so anything that is not `next-index-*` is UNSCOPED, including an
# unrecognised caller-supplied index.
#
# NO PYTHON. This is a basename test in bash and it is the WHOLE scope check,
# so no configuration exists in which the guard silently disappears. It does
# NOT consult commit_scope.py — a ledger-consulting gate could be raced between
# the check and the commit; the ledger's job is to help a surface BUILD a
# declaration, never to excuse the absence of one.

# The discriminator, in ONE place. It must mean EXACTLY what
# `commit_scope.classify_style` means — the regex there is
# `^next-index-\d+(\.lock)?$`. The first version of this used the glob
# `next-index-[0-9]*`, which is LOOSER: `next-index-9x`, `next-index-1abc` and
# `next-index-12.lock.bak` all matched it while the Python mirror called them
# UNSCOPED. Measured, 3 disagreements. Since GIT_INDEX_FILE is caller-settable
# and a caller can fill an arbitrary index with `read-tree HEAD` and sweep the
# whole tree, the loose side was the ENFORCEMENT surface — a sweep could be
# handed a name that reads as declared. The strict rule had been written only
# into the copy the gate deliberately does not call. Two independent validators
# found this; the trailer hook uses this same function's logic for the same
# reason its header claims the two "cannot drift".
_scope_is_declared() {  # $1 = GIT_INDEX_FILE basename
  local b="${1:-}" rest
  # Deliberately NO whitespace stripping, and `classify_style` no longer strips
  # either. An earlier fix aligned the two by adding stripping HERE, which
  # loosened the enforcement surface: GIT_INDEX_FILE is caller-settable, so an
  # index named `next-index-1 ` would have read as DECLARED. Aligned in the
  # strict direction instead — git writes no whitespace into its index names.
  case "$b" in
    next-index-*) rest="${b#next-index-}" ;;
    *) return 1 ;;
  esac
  rest="${rest%.lock}"                 # optional single `.lock` suffix
  [ -n "$rest" ] || return 1           # `next-index-` / `next-index-.lock`
  case "$rest" in *[!0-9]*) return 1 ;; esac   # digits only
  return 0
}

_scope_gitdir="$(git rev-parse --git-dir 2>/dev/null)"

_in_sequencer() {
  [ -n "$_scope_gitdir" ] || return 1
  for _m in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
    [ -f "$_scope_gitdir/$_m" ] && return 0
  done
  for _d in rebase-merge rebase-apply; do
    [ -d "$_scope_gitdir/$_d" ] && return 0
  done
  return 1
}

_scope_stage() {
  # Sequencer exemption comes FIRST, ahead of the scope test.
  #
  # Measured (probe_commit_styles.sh): git REFUSES a partial commit during a
  # merge and a cherry-pick, but ACCEPTS one during a revert and a rebase — so
  # "declaring a scope is impossible here" is true of only half these states.
  # The exemption is still required, for a different reason: `merge --continue`,
  # `cherry-pick --continue` and `revert --continue` each run this hook against
  # the SHARED index with no pathspec, so enforcing would leave no compliant way
  # to finish the operation. It is a genuine hole, not a safe case — a probed
  # `revert --continue` swept a concurrent session's staged file — and it is the
  # converted surfaces, not this gate, that cover it.
  _in_sequencer && return 0

  _idx="${GIT_INDEX_FILE:-}"
  _base="${_idx##*/}"
  _scope_is_declared "$_base" && return 0   # declared: git bounded this commit

  # Unscoped from here. The named override is deliberately NOT the same
  # variable as ALLOW_OUT_OF_TREE, so a legitimately out-of-tree promotion
  # cannot buy a scope exemption with one variable.
  [ "${ALLOW_UNSCOPED_COMMIT:-0}" = "1" ] && return 0

  _staged="$(git diff --cached --name-only 2>/dev/null)"
  [ -z "$_staged" ] && return 0     # nothing staged: nothing to declare

  _n="$(printf '%s\n' "$_staged" | grep -c . 2>/dev/null || echo 0)"
  {
    if [ "$SCOPE_MODE" = "enforce" ]; then
      printf '\n[commit-gate] scope BLOCKED: this commit names no paths.\n'
    else
      printf '\n[commit-gate] scope WARNING (not blocking — warn mode)\n'
    fi
    printf 'This commit names no paths, so it publishes everything staged in the\n'
    printf 'shared index — including any other session'"'"'s work. %s path(s):\n' "$_n"
    printf '%s\n' "$_staged" | sed 's/^/    /' | head -20
    [ "$_n" -gt 20 ] && printf '    … and %s more\n' "$((_n - 20))"
    printf '\nPublish it declared instead:\n'
    printf '    python3 %s publish -m "<message>" -- <paths>\n' \
           "$_hooks_dir/commit_scope.py"
    printf 'Or state the intent explicitly for THIS commit:\n'
    printf '    ALLOW_UNSCOPED_COMMIT=1 git commit ...\n\n'
  } >&2

  # Best-effort inventory — this is what sizes A6/A7, because two publish
  # surfaces name no command at all and their improvisations cannot be read out
  # of source. Never fails the hook.
  # `$HOME` must be guarded too. Unguarded, with CLAUDE_CONFIG_DIR unset/empty
  # and HOME unset (`env -i`, some cron/CI contexts), `set -u` aborts the hook
  # with status 1 and git refuses the commit — the ONE input class where warn
  # mode would change an exit code, in the branch that runs on every unscoped
  # commit. That is exactly the invariant the plan's A3 guard rail names.
  _inv_base="${CLAUDE_CONFIG_DIR:-}"
  [ -n "$_inv_base" ] || _inv_base="${HOME:-}/.claude"
  if [ -n "${HOME:-}" ] || [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    _inv="$_inv_base/state/commit-scope-warn.log"
    mkdir -p "$(dirname "$_inv")" 2>/dev/null \
      && printf '%s\t%s\t%s\t%s\t%s\n' \
           "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${PWD}" "${_base:-<unset>}" "$_n" "$SCOPE_MODE" \
           >> "$_inv" 2>/dev/null
  fi
  # The refusal. Logging above is best-effort and can never decide this: a
  # failed log write must not turn a refusal into an allow.
  [ "$SCOPE_MODE" = "enforce" ] && exit 1
  return 0
}

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
     infrastructure committer), opt in explicitly for THIS commit only — and
     still name its paths, because the scope stage refuses an undeclared commit
     whatever ALLOW_OUT_OF_TREE says:
           ALLOW_OUT_OF_TREE=1 git commit -m "<message>" -- <paths>

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

  2. If this mixed commit is deliberate, opt in explicitly for THIS commit only
     (naming its paths — the scope stage still applies):
           ALLOW_OUT_OF_TREE=1 git commit -m "<message>" -- <paths>

EOF
  exit 1
}

# --- run detection -----------------------------------------------------------
bash "$PRIM" "$PWD"
rc=$?

# --- S2/A3 scope stage: AFTER detection, AHEAD of all three exits -------------
# Enforcing since S6 (exits 1 on an undeclared commit). Runs inside linked
# worktrees too (see the header note on per-worktree, not per-session, index
# isolation).
_scope_stage

# --- explicit override: named opt-in ------------------------------------------
# MOVED here from the top of the file by S2/A3 so that it cannot short-circuit
# the scope stage above. It still governs worktree placement exactly as before.
[ "${ALLOW_OUT_OF_TREE:-0}" = "1" ] && exit 0

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

#!/usr/bin/env bash
# worktree-helper — the shared create-or-lookup worktree helper + hook installer
# (git-working-model S1 / A2; completes the unbuilt `claude-worktree-init`).
#
# ONE helper with two responsibilities that S1 deliberately keeps co-located
# (Cockburn "start fat, split only for a named future"):
#   * create-or-lookup a topic worktree+branch under <repos-root>/<repo>/<topic>/
#   * install BOTH git hooks (pre-push wrong-target guard + pre-commit worktree
#     gate) into the repo's SHARED hooks dir — one install covers all worktrees.
# It also OWNS the canonical detection primitive worktree-detect.sh (its sibling)
# which pre_plan_gates._in_worktree() wraps and the A3 gate calls directly — the
# single detection seam realized as one implementation, not two.
#
# Subcommands:
#   create-or-lookup [--repo <git-repo>] --topic <raw-topic> [--repos-root <dir>]
#       Ensure a worktree exists at <repos-root>/<repo-name>/<slug>/ on branch
#       <slug>, install the hooks, and print the worktree path on stdout.
#       Idempotent: a second call for the same topic returns the same worktree.
#       --repo defaults to the configured source path (the harness repo-binding, S1
#       Design Review item 8). --repos-root defaults to $HOME/repos.
#   place [--repo <git-repo>] --topic <raw-topic> [--repos-root <dir>]
#       Work-initiation placement (A4). Routing:
#         * CLEAN topic branch checked out in the primary → S5 clean relocate;
#         * DIRTY primary → S6 transactional dirty cutover (cmd_cutover_dirty):
#           A2 pre-flights (halt-before-topology) → A1 drain bookkeeping to `main`
#           under the flock → carry domain via `git stash push -u` → reuse the
#           create-or-lookup add+sparse+hook tail → copy required config → `git
#           stash pop --index` → rebuild → restore primary to `main`; A3 rollback
#           is by failure-KIND (worktree-add failure → full unwind; pop conflict →
#           halt-don't-rollback; rebuild failure → alert-don't-rollback);
#         * DETACHED / active-branch-is-`main` (EDGE11/EDGE16) → prompt for a new
#           topic branch (non-TTY → fail closed, exit 3);
#         * otherwise → create-or-lookup (fresh / elsewhere / idempotent).
#   install --repo <git-repo>
#       Install (only) the hooks into the repo's shared hooks dir.
#   coverage
#       Report whether every GATED repo carries BOTH the pre-commit scope gate
#       and the commit-msg waiver trailer; print the allowlisted commit targets
#       with their reasons. Exit 1 on any gap (S6 / A7b).
#
# Safety (Safe-Executor rubric, ten review rounds): slugify + HALT on empty /
# protected branch name; git-native worktree-add edge cases (existing / occupied
# / checked-out-elsewhere / remote-tracking-only, dynamic remote name); NON-
# destructive, transactional, chaining hook-install (5-case state machine, atomic
# writes, non-clobbering timestamped backups, cross-hook both-or-neither
# rollback); NEVER `rm -rf`, NEVER `--force`, NEVER `git branch -D`.
#
# Exit: 0 ok; 2 usage/env error; 3 halt-before-any-topology-change (place
#       pre-flight / edge / drain-lock — nothing moved); 1 failure (HALT).

set -uo pipefail

# --- resolve own location → the hooks dir where our siblings live ------------
_SELF="$(realpath "$0" 2>/dev/null || echo "$0")"
HELPER_DIR="$(cd "$(dirname "$_SELF")" 2>/dev/null && pwd)"
DETECT_PRIMITIVE="$HELPER_DIR/worktree-detect.sh"
GATE_SRC="$HELPER_DIR/check-worktree-commit-gate.sh"
PUSH_SRC="$HELPER_DIR/check-worktree-push-target.sh"
# declared-publish-scope S2/A3 — the commit-msg override trailer (claim C7).
TRAILER_SRC="$HELPER_DIR/check-unscoped-commit-trailer.sh"
# storage-decouple S2: the fail-closed canonical-repo identity guard, installed
# on the canonical Projects repo as pre-commit + pre-push. Installed by the
# dedicated `install-canonical-guard` entry point (NOT the generic `install`
# flow), reusing the SAME install_one_hook chaining primitive so a pre-existing
# user hook (e.g. the KL coverage-audit pre-commit on ~/Projects) is chained,
# never clobbered.
IDENTITY_GUARD_SRC="$HELPER_DIR/check-canonical-repo-identity.sh"
BOOKKEEPING_PATHS_MODULE="$HELPER_DIR/bookkeeping_paths.py"   # S2/A4 manifest reader
CUTOVER_MODULE="$HELPER_DIR/worktree_cutover.py"             # S6 drain + config-manifest reader
WRAPPER_SENTINEL="claude-worktree-gate-wrapper"

log()  { printf '[worktree-helper] %s\n' "$*" >&2; }
die()  { printf '[worktree-helper] ERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# slug + branch-name safety
# ---------------------------------------------------------------------------
slugify() {  # $1 raw → stdout lowercase-kebab slug (may be empty)
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | tr -s '-' \
    | sed -e 's/^-//' -e 's/-$//'
}

default_branch() {  # $1 repo → stdout best-effort default branch (lowercased)
  local d="$1" b
  b="$(git -C "$d" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's@^origin/@@')"
  [ -n "$b" ] || b="$(git -C "$d" symbolic-ref --quiet --short HEAD 2>/dev/null)"
  [ -n "$b" ] || b="main"
  printf '%s' "$b" | tr '[:upper:]' '[:lower:]'
}

is_protected() {  # $1 slug $2 repo → 0 if protected/reserved
  case "$1" in main|master|head) return 0 ;; esac
  [ "$1" = "$(default_branch "$2")" ] && return 0
  return 1
}

# ---------------------------------------------------------------------------
# worktree lookup helpers
# ---------------------------------------------------------------------------
worktree_registered() {  # $1 repo $2 dest → 0 if dest is a registered worktree
  local dabs
  dabs="$(realpath "$2" 2>/dev/null)" || return 1
  git -C "$1" worktree list --porcelain 2>/dev/null \
    | grep -Fxq "worktree $dabs"
}

worktree_for_branch() {  # $1 repo $2 slug → stdout worktree path if checked out
  git -C "$1" worktree list --porcelain 2>/dev/null | awk -v b="refs/heads/$2" '
    /^worktree /{ wt = substr($0, 10) }
    /^branch /  { if ($2 == b) { print wt; exit } }'
}

# The PRIMARY (main) working tree of the repo — the first entry of
# `git worktree list --porcelain`, i.e. the canonical `main` home (A1). The S5
# clean relocate migrates a legacy topic branch OUT of here into its own worktree.
main_worktree() {  # $1 repo → stdout primary working-tree path
  git -C "$1" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{ print substr($0, 10); exit }'
}

current_branch() {  # $1 worktree-path → stdout branch short-name (empty if detached)
  git -C "$1" symbolic-ref --quiet --short HEAD 2>/dev/null
}

find_remote_branch() {  # $1 repo $2 slug → stdout <remote>/<slug>, deterministic
  local repo="$1" slug="$2" r
  if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$slug"; then
    printf 'origin/%s' "$slug"; return 0
  fi
  for r in $(git -C "$repo" remote 2>/dev/null); do
    if git -C "$repo" show-ref --verify --quiet "refs/remotes/$r/$slug"; then
      printf '%s/%s' "$r" "$slug"; return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# hook install — 5-case state machine, transactional, chaining
# ---------------------------------------------------------------------------
# UNDO stack: tab-delimited records replayed in reverse on any failure.
#   rmfile <TAB> <path>            — remove a file/symlink we created
#   restore <TAB> <src> <TAB> <dst> — move src back to dst
UNDO=()
push_undo() { UNDO+=("$1"); }

run_rollback() {
  local i rec op a b
  for (( i=${#UNDO[@]}-1; i>=0; i-- )); do
    rec="${UNDO[$i]}"
    IFS=$'\t' read -r op a b <<<"$rec"
    case "$op" in
      rmfile)  rm -f "$a" ;;
      restore) command mv -f "$a" "$b" 2>/dev/null || true ;;
    esac
  done
  UNDO=()
}

nonclobber_path() {  # $1 desired → stdout a path that does not yet exist
  local p="$1" cand="$1" n=0
  while [ -e "$cand" ]; do n=$((n+1)); cand="${p}.${n}"; done
  printf '%s' "$cand"
}

# Generate our chaining wrapper (a regular file) to <dest>, atomically.
# The wrapper runs the gate/guard target FIRST (short-circuit on block), then
# the user's original hook (moved to <hook>.chained) as a CHILD. It buffers
# stdin ONLY when piped, so a no-stdin pre-commit never hangs on the TTY while a
# pre-push ref-update stream is preserved for the chained hook.
generate_wrapper() {  # $1 dest-path  $2 target(gate/push)  $3 chained-path
  local dest="$1" target="$2" chained="$3" tmp
  tmp="$(mktemp "${dest}.tmp.XXXXXX")" || return 1
  cat > "$tmp" <<WRAP
#!/usr/bin/env bash
# ${WRAPPER_SENTINEL}
# Generated by worktree-helper.sh — chains a pre-existing user hook behind the
# worktree gate/guard. Do NOT edit; regenerated on re-install.
set -uo pipefail
TARGET="${target}"
CHAINED="${chained}"

# Buffer stdin only when it is actually piped (pre-push receives the ref-update
# stream; pre-commit receives none — an unconditional read would hang the TTY).
if [ ! -t 0 ]; then
  buf="\$(mktemp -t cwg-buf.XXXXXX)" || exit 1   # fail-closed: never run empty
  trap 'rm -f "\$buf"' EXIT
  cat > "\$buf"
else
  buf=""
fi

run_hook() {  # \$1 = hook; remaining args forwarded; buffered stdin (if any)
  local h="\$1"; shift
  if [ -n "\$buf" ]; then "\$h" "\$@" < "\$buf"; else "\$h" "\$@" < /dev/null; fi
}

# Gate/guard FIRST; short-circuit (skip the chained hook) if it blocks.
run_hook "\$TARGET" "\$@"
rc=\$?
[ "\$rc" -ne 0 ] && exit "\$rc"

# Passed → run the user's original hook as a CHILD (never exec — exec would
# replace the shell and the buffer-cleanup trap would never fire).
if [ -x "\$CHAINED" ]; then
  run_hook "\$CHAINED" "\$@"
  exit \$?
fi
exit 0
WRAP
  chmod +x "$tmp" || { rm -f "$tmp"; return 1; }
  command mv -f "$tmp" "$dest" || { rm -f "$tmp"; return 1; }
  return 0
}

# Classify the current state of a hook path relative to our target.
classify_hook() {  # $1 hookpath $2 our-target → echoes state
  local hp="$1" target="$2"
  if [ -L "$hp" ]; then
    local cur tgt raw
    cur="$(realpath "$hp" 2>/dev/null)"
    tgt="$(realpath "$target" 2>/dev/null)"
    if [ -n "$cur" ] && [ "$cur" = "$tgt" ]; then echo "ours-symlink"; return; fi
    # DANGLING-SYMLINK FALLBACK. `realpath` on BSD/darwin fails outright when
    # the target no longer exists, so `cur` comes back empty and a symlink WE
    # created was classified `alien-symlink` — which uninstall refuses to touch.
    # That made the verb unavailable in precisely the failure mode its own
    # header documents: a revert deletes the hook script, the symlink dangles,
    # and git then aborts every commit with ENOENT. Compare the RAW link target
    # so a dangling link we made is still recognised as ours.
    raw="$(readlink "$hp" 2>/dev/null)"
    if [ -n "$raw" ] && { [ "$raw" = "$target" ] || [ "$raw" = "$tgt" ]; }; then
      echo "ours-symlink"; return
    fi
    # SIBLING SYMLINK: a link to ANOTHER hook script in this helper's own
    # directory — concretely, the canonical-repo identity guard that
    # `install_canonical_guard_hooks` (this same file) puts at pre-commit AND
    # pre-push on `~/repos/Projects`. Classifying it `alien-symlink` made the
    # helper refuse its own sibling: install_all_hooks returned PARTIAL (3),
    # the create/lookup arms treated that as failure, and `place` printed no
    # worktree path — so `/work-start --worktree` could never place a topic
    # into Projects while the identity guard was installed there. It is
    # handled like a user hook: archived, chained behind ours, both run.
    # A link to anywhere OUTSIDE this directory stays alien and is refused.
    local helper_dir_abs; helper_dir_abs="$(realpath "$HELPER_DIR" 2>/dev/null)"
    if [ -n "$cur" ] && [ -n "$helper_dir_abs" ] \
       && [ "$(dirname "$cur")" = "$helper_dir_abs" ]; then
      echo "sibling-symlink"; return
    fi
    echo "alien-symlink"
  elif [ -f "$hp" ]; then
    if grep -q "$WRAPPER_SENTINEL" "$hp" 2>/dev/null; then echo "ours-wrapper"; else echo "user-hook"; fi
  elif [ -e "$hp" ]; then
    echo "other"        # a dir or special file where a hook should be
  else
    echo "absent"
  fi
}

# Install one hook. Appends UNDO actions; returns non-zero on failure WITHOUT
# self-rollback (the caller replays UNDO across BOTH hooks on any failure).
install_one_hook() {  # $1 hooks_dir $2 hookname $3 target
  local hooks_dir="$1" name="$2" target="$3"
  local hp="$hooks_dir/$name" state ts bak chained chained_bak

  [ -f "$target" ] || { log "hook source missing: $target"; return 1; }
  chmod +x "$target" 2>/dev/null || { log "cannot chmod +x $target"; return 1; }

  state="$(classify_hook "$hp" "$target")"
  case "$state" in
    ours-symlink)
      return 0 ;;                                   # idempotent no-op
    ours-wrapper)
      # THE SENTINEL DOES NOT PROVE THE WRAPPER IS SERVING *THIS* TARGET.
      # `install_canonical_guard_hooks` generates wrappers carrying the SAME
      # sentinel for a different target, so regenerating on the sentinel alone
      # silently repoints another slice's hook at ours — and, because this
      # branch pushed no UNDO record, a rollback could not put it back.
      # `uninstall_one_hook` was given this check; the installer was not, which
      # left the more damaging half of the pair open.
      chained="$hp.chained"
      if ! grep -qF "TARGET=\"$target\"" "$hp" 2>/dev/null; then
        # ALREADY SERVED THROUGH THE CHAIN: the wrapper fronts a different
        # target, but its `.chained` hook IS ours (a sibling symlink that got
        # chained, see `classify_hook`). Our hook runs on every invocation
        # already — regenerating would drop the front target, refusing would
        # make the other installer's verb fail on a repo where both hooks
        # are in fact live. Idempotent no-op, same as `ours-symlink`.
        if [ -L "$chained" ] \
           && [ "$(realpath "$chained" 2>/dev/null)" = "$(realpath "$target" 2>/dev/null)" ]; then
          return 0
        fi
        log "REFUSING to regenerate $name — it is our wrapper shape but serves a"
        log "  different target than $target (another slice installed it)."
        log "  Nothing was changed."
        return 2
      fi
      # Preserve the current wrapper so a rollback can restore it byte-for-byte.
      local ts_w bak_w
      ts_w="$(date +%Y%m%d%H%M%S)"
      bak_w="$(nonclobber_path "$hp.regen.$ts_w")"
      command cp -p "$hp" "$bak_w" 2>/dev/null \
        && push_undo "restore"$'\t'"$bak_w"$'\t'"$hp"
      generate_wrapper "$hp" "$target" "$chained" || { log "wrapper regenerate failed for $name"; return 1; }
      return 0 ;;
    alien-symlink)
      log "REFUSING to clobber $name — it is a symlink to a foreign target (e.g. another hook manager)."
      log "  Resolve manually, then re-run. (Our install is fail-closed here, never silent.)"
      # Exit 2 = REFUSED (correctly), not 1 = FAILED. The caller must not treat
      # this as a transaction failure: refusing to clobber someone else's hook is
      # the RIGHT outcome, and rolling back because of it destroys unrelated
      # hooks that installed fine. Measured consequence of conflating the two:
      # `~/repos/Projects` has a `pre-push` symlinked to the canonical-repo
      # identity guard, so installing there refused at pre-push, rolled back, and
      # removed the `commit-msg` trailer that had just been installed — leaving
      # C7 undeliverable in that repo and no trace of why.
      return 2 ;;
    other)
      log "REFUSING to overwrite $name — it is neither a regular hook nor a symlink."
      return 2 ;;
    absent)
      ln -s "$target" "$hp" || { log "ln -s failed for $name"; return 1; }
      push_undo "rmfile"$'\t'"$hp"
      return 0 ;;
    user-hook|sibling-symlink)
      # A sibling symlink (our own identity guard, see `classify_hook`) takes
      # the user-hook route unchanged: `cp -p` archives the link, `mv` moves
      # the link itself into `.chained`, and the generated wrapper runs our
      # target and then chains to it — so BOTH hooks fire on every invocation.
      ts="$(date +%Y%m%d%H%M%S)"
      bak="$(nonclobber_path "$hp.bak.$ts")"
      # immutable archive copy (preserve mode) — never undone
      command cp -p "$hp" "$bak" || { log "backup copy failed for $name"; return 1; }
      chained="$hp.chained"
      if [ -e "$chained" ]; then
        chained_bak="$(nonclobber_path "$chained.bak.$ts")"
        command mv -f "$chained" "$chained_bak" || { log "could not archive pre-existing $name.chained"; return 1; }
        push_undo "restore"$'\t'"$chained_bak"$'\t'"$chained"
      fi
      command mv -f "$hp" "$chained" || { log "could not move user $name to .chained"; return 1; }
      push_undo "restore"$'\t'"$chained"$'\t'"$hp"
      chmod +x "$chained" 2>/dev/null || true
      generate_wrapper "$hp" "$target" "$chained" || { log "wrapper generation failed for $name"; return 1; }
      push_undo "rmfile"$'\t'"$hp"
      log "chained pre-existing $name behind the worktree gate (archived: $bak)"
      return 0 ;;
  esac
  return 1
}

install_all_hooks() {  # $1 repo
  local repo="$1" common common_abs hooks_dir
  common="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null)" \
    || { log "cannot resolve --git-common-dir for $repo"; return 1; }
  common_abs="$(cd "$repo" 2>/dev/null && realpath "$common" 2>/dev/null)" \
    || { log "cannot resolve absolute git-common-dir for $repo"; return 1; }
  hooks_dir="$common_abs/hooks"
  mkdir -p "$hooks_dir" || { log "cannot create hooks dir $hooks_dir"; return 1; }

  UNDO=()
  local _rc _refused=0
  # A hook we REFUSE to clobber (exit 2) is skipped and noted; only a genuine
  # FAILURE (exit 1) rolls the transaction back. Conflating the two meant one
  # foreign sibling hook undid every hook that had installed correctly.
  _try_hook() {  # $1 name  $2 target  ; echoes nothing, sets _rc
    install_one_hook "$hooks_dir" "$1" "$2"; _rc=$?
    case "$_rc" in
      0) return 0 ;;
      2) _refused=$((_refused+1))
         log "skipping $1 (not ours) — other hooks are unaffected"
         return 0 ;;
      *) return 1 ;;
    esac
  }

  if ! _try_hook "pre-commit" "$GATE_SRC"; then
    log "hook install FAILED (pre-commit gate) for $repo"; run_rollback; return 1
  fi
  # declared-publish-scope S2/A3: the override trailer (C7). Installed
  # alongside the gate because coverage is per HOOK TYPE, not per repo — a repo
  # carrying only the pre-commit gate satisfies the scope check while silently
  # dropping the waiver record the gate's own override depends on.
  if ! _try_hook "commit-msg" "$TRAILER_SRC"; then
    log "hook install FAILED (commit-msg trailer) for $repo"; run_rollback; return 1
  fi
  if ! _try_hook "pre-push" "$PUSH_SRC"; then
    log "hook install FAILED (pre-push guard) for $repo"; run_rollback; return 1
  fi
  UNDO=()
  if [ "$_refused" -gt 0 ]; then
    # PARTIAL INSTALL: report it, but do NOT roll back the hooks that installed
    # correctly. Both halves matter, and an earlier pair of changes each got one
    # of them and broke the other:
    #   * the original rolled everything back, so one foreign sibling hook
    #     removed the commit-msg trailer that had just installed — which is why
    #     `~/repos/Projects` went without a trailer for a time, leaving C7
    #     undeliverable there. (Historical: the trailer was reinstalled
    #     2026-09-17 and `coverage` now reports Projects COVERED with both
    #     hooks. Said in the past tense because the present-tense form of this
    #     sentence led an independent audit on 2026-09-18 to report a safety
    #     gap in a repo that does not have one.);
    #   * the first fix returned success, which silently contradicted this
    #     suite's own `6c alien install did not fail` — the operator asked for
    #     hooks and would not have been told some were skipped.
    # Non-zero says "not everything installed"; no rollback says "what did
    # install is still there". The log names which.
    log "PARTIAL install: $_refused hook(s) left alone as not ours; the rest ARE installed"
    log "  (nothing was rolled back — resolve the foreign hook(s) and re-run to finish)"
    return 3
  fi
  return 0
}

# storage-decouple S2: install the fail-closed canonical-repo identity guard as
# pre-commit + pre-push on the canonical Projects repo. Reuses install_one_hook,
# so any pre-existing user hook (the KL coverage-audit pre-commit on ~/Projects)
# is archived + chained behind the guard via the standard wrapper — never
# clobbered. Transactional: replays UNDO across BOTH hooks on any failure.
install_canonical_guard_hooks() {  # $1 repo
  local repo="$1" common common_abs hooks_dir
  [ -f "$IDENTITY_GUARD_SRC" ] || { log "identity guard source missing: $IDENTITY_GUARD_SRC"; return 1; }
  common="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null)" \
    || { log "cannot resolve --git-common-dir for $repo"; return 1; }
  common_abs="$(cd "$repo" 2>/dev/null && realpath "$common" 2>/dev/null)" \
    || { log "cannot resolve absolute git-common-dir for $repo"; return 1; }
  hooks_dir="$common_abs/hooks"
  mkdir -p "$hooks_dir" || { log "cannot create hooks dir $hooks_dir"; return 1; }

  UNDO=()
  if ! install_one_hook "$hooks_dir" "pre-commit" "$IDENTITY_GUARD_SRC"; then
    run_rollback; return 1
  fi
  if ! install_one_hook "$hooks_dir" "pre-push" "$IDENTITY_GUARD_SRC"; then
    run_rollback; return 1
  fi
  UNDO=()
  return 0
}

# ---------------------------------------------------------------------------
# sparse-checkout fail-closed exclusion of the shared bookkeeping paths (S2/A3)
# ---------------------------------------------------------------------------
# A topic worktree EXCLUDES the main-owned shared bookkeeping paths (the A4
# manifest set) via non-cone sparse-checkout, so a raw `cat`/`grep` of one of
# them inside the worktree FAILS CLOSED (ENOENT) instead of returning a stale
# private copy — the deliberate design-A4 EDGE4 choice. The A1 main-pinned
# resolver (bookkeeping_resolver) remains the sanctioned path to main's copy.
# Idempotent: safe to re-apply on every lookup of an existing worktree.
# Additive + non-fatal: any failure logs and continues (never blocks placement).
apply_sparse_exclusion() {  # $1 worktree-dest
  local dest="$1"
  [ -f "$BOOKKEEPING_PATHS_MODULE" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  local patterns; patterns="$(python3 "$BOOKKEEPING_PATHS_MODULE" sparse-patterns 2>/dev/null)" || return 0
  [ -n "$patterns" ] || return 0
  # Build a pattern ARRAY (never an unquoted expansion — the leading `/*` would
  # otherwise glob-expand against the CWD).
  local -a pat_args=()
  local line
  while IFS= read -r line; do
    [ -n "$line" ] && pat_args+=("$line")
  done <<SPARSE_EOF
$patterns
SPARSE_EOF
  [ "${#pat_args[@]}" -gt 0 ] || return 0
  if git -C "$dest" sparse-checkout set --no-cone --skip-checks -- "${pat_args[@]}" >&2 2>&1; then
    log "sparse-checkout: excluded shared bookkeeping paths in $dest (fail-closed; read main via the resolver)"
  else
    log "warning: could not apply sparse-checkout exclusion in $dest — a raw read there may return a stale copy; continuing"
  fi
  return 0
}

# Ensure the repo's append-style shared files union-merge (S2/A2 + A4 Cycle-11)
# so two branch-side appends reconcile without loss at a land/merge. Writes the
# LOCAL `<git-common-dir>/info/attributes` (no commit to main). Idempotent;
# non-fatal. Complements the A2 lock-guarded append (which guards same-copy
# concurrent appends); this guards the branch-merge reconciliation path.
apply_merge_union_attributes() {  # $1 repo
  local repo="$1"
  local lockmod="$HELPER_DIR/bookkeeping_lock.py"
  [ -f "$lockmod" ] || return 0
  command -v python3 >/dev/null 2>&1 || return 0
  python3 "$lockmod" install-merge-union --repo "$repo" >&2 2>&1 || \
    log "warning: could not install merge=union attributes for $repo (continuing)"
  return 0
}

# ---------------------------------------------------------------------------
# create-or-lookup
# ---------------------------------------------------------------------------
cmd_create_or_lookup() {
  local repo="" topic="" repos_root="${HOME}/repos"
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo)       repo="${2:-}"; shift 2 ;;
      --topic)      topic="${2:-}"; shift 2 ;;
      --repos-root) repos_root="${2:-}"; shift 2 ;;
      *) die "create-or-lookup: unknown argument: $1" ;;
    esac
  done

  [ -n "$topic" ] || die "create-or-lookup: --topic is required"
  if [ -z "$repo" ]; then
    repo="$(the configured source path 2>/dev/null)" \
      || die "no --repo given and cannot resolve the configured source path (harness repo-binding)"
  fi
  [ -d "$repo" ] || die "repo path does not exist: $repo"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo"

  local slug; slug="$(slugify "$topic")"
  [ -n "$slug" ] || die "topic '$topic' slugifies to empty — refusing to pass an empty path/branch to git"
  if is_protected "$slug" "$repo"; then
    die "topic '$topic' resolves to the protected/default branch name '$slug' — refusing to check the canonical branch out into a topic worktree"
  fi

  local repo_name dest
  repo_name="$(basename "$(realpath "$repo" 2>/dev/null || echo "$repo")")"
  dest="$repos_root/$repo_name/$slug"

  # S2/A2: ensure the repo union-merges its append-style shared files (repo-wide,
  # idempotent, no commit) before any worktree work — the land-reconciliation
  # half of A2's append coherence.
  apply_merge_union_attributes "$repo"

  # LOOKUP arm (idempotent): dest already a registered worktree?
  if worktree_registered "$repo" "$dest"; then
    install_all_hooks "$repo" || die "existing worktree $dest found but hook-install failed (re-run to repair)"
    apply_sparse_exclusion "$(realpath "$dest")"   # S2/A3 — idempotent re-apply
    printf '%s\n' "$(realpath "$dest")"
    return 0
  fi
  # Branch already checked out in ANOTHER worktree → return that one.
  local existing; existing="$(worktree_for_branch "$repo" "$slug")"
  if [ -n "$existing" ]; then
    install_all_hooks "$repo" || die "branch '$slug' checked out at $existing but hook-install failed (re-run to repair)"
    apply_sparse_exclusion "$existing"             # S2/A3 — idempotent re-apply
    printf '%s\n' "$existing"
    return 0
  fi
  # Destination occupied but NOT a registered worktree → refuse (no clobber).
  [ -e "$dest" ] && die "destination $dest exists but is not a registered worktree — refusing to overwrite"

  # Ensure the destination PARENT exists (git creates only the leaf) — FAIL-CLOSED.
  mkdir -p "$(dirname "$dest")" || die "could not create worktree parent dir $(dirname "$dest")"

  # CREATE arm — pick the add form by branch state.
  # NOTE: git worktree add emits "Preparing worktree" / "HEAD is now at" on both
  # stdout and stderr; redirect its stdout to stderr (>&2) so the ONLY thing on
  # our stdout is the final worktree path (callers capture stdout).
  local remote_ref
  if git -C "$repo" show-ref --verify --quiet "refs/heads/$slug"; then
    git -C "$repo" worktree add "$dest" "$slug" >&2 \
      || die "git worktree add (attach existing branch '$slug') failed"
  elif remote_ref="$(find_remote_branch "$repo" "$slug")"; then
    git -C "$repo" worktree add --track -b "$slug" "$dest" "$remote_ref" >&2 \
      || die "git worktree add --track (from $remote_ref) failed"
  else
    git -C "$repo" worktree add -b "$slug" "$dest" >&2 \
      || die "git worktree add -b (new branch '$slug') failed"
  fi

  # Worktree exists now. Install hooks; on failure LEAVE the worktree intact
  # (a valid empty workspace, re-runnable) and HALT — never tear down.
  install_all_hooks "$repo" \
    || die "worktree created at $dest but hook-install failed — worktree left intact; re-run to repair"

  apply_sparse_exclusion "$(realpath "$dest")"     # S2/A3 — fail-closed shared-path exclusion

  printf '%s\n' "$(realpath "$dest")"
  return 0
}

# ---------------------------------------------------------------------------
# S5 legacy cutover — default CLEAN relocate (A12; no stash)
# ---------------------------------------------------------------------------
# Migrate a currently-active CLEAN legacy topic branch OUT of the primary clone
# into its own worktree, in one step. Precondition (asserted by the caller,
# cmd_place): the primary working tree is CLEAN and its checked-out branch is the
# topic <slug> being placed. Design A12: "relocate the branch (no stash) when the
# checkout is clean — no working-tree round-trip, no OOM risk."
#
# Mechanics (all reuse shipped S1 primitives; nothing rebuilt):
#   EDGE14  free the branch by parking the primary on a DETACHED HEAD
#           (`git checkout --detach HEAD` — NEVER `git checkout main`, which is
#           fatal if main is checked out in another worktree),
#   then     cmd_create_or_lookup CREATE arm attaches the now-free branch into its
#           worktree + sparse-excludes the shared bookkeeping + installs hooks,
#   EDGE17  restore the primary to its default branch (the canonical `main` home);
#           if that fails because `main` is checked out elsewhere, catch it and
#           instruct the operator to restore manually — the migration itself has
#           succeeded, so exit CLEAN, never a raw git fatal.
# On a relocate FAILURE (branch still free) reverse EDGE14 (restore the primary to
# <slug>) so nothing is left half-migrated. NEVER rm -rf / --force / stash.
#
# Bookkeeping: a CLEAN tree has no uncommitted bookkeeping to drain; shared files
# stay main-owned via the S2 resolver/lock/writers (bookkeeping_resolver.py etc.),
# which this cutover does not disturb. The topology move is the whole job here.
cmd_cutover_clean() {  # $1 repo $2 slug $3 dest $4 main_wt $5 repos_root
  local repo="$1" slug="$2" dest="$3" main_wt="$4" repos_root="$5"

  log "cutover: migrating CLEAN legacy topic '$slug' out of the primary clone into its worktree"

  # EDGE14 — free the branch (detached HEAD; safe even if main is elsewhere).
  git -C "$main_wt" checkout --detach HEAD >/dev/null 2>&1 \
    || die "cutover: could not detach the primary clone HEAD to free branch '$slug' — nothing moved"

  # Relocate via the shipped CREATE arm (branch is now free → attaches into dest).
  local out
  if out="$(cmd_create_or_lookup --repo "$repo" --topic "$slug" --repos-root "$repos_root")"; then
    # EDGE17 — restore the primary to its canonical `main` home (graceful fallback).
    local def; def="$(default_branch "$repo")"
    if ! git -C "$main_wt" checkout "$def" >/dev/null 2>&1; then
      log "cutover: migration SUCCEEDED, but the primary clone could not be auto-restored to '$def' (it may be checked out in another worktree). The primary is on a detached HEAD — restore it manually:  git -C \"$main_wt\" checkout $def"
    fi
    printf '%s\n' "$out"
    return 0
  fi

  # Relocate FAILED → the branch is still free; reverse EDGE14 (restore primary).
  git -C "$main_wt" checkout "$slug" >/dev/null 2>&1 \
    || log "cutover: relocate failed AND could not restore the primary to '$slug' — the primary is on a detached HEAD; restore manually:  git -C \"$main_wt\" checkout $slug"
  die "cutover: worktree relocate for '$slug' failed — primary restored to its original branch; nothing migrated"
}

# ===========================================================================
# S6 legacy cutover — DIRTY transactional migration (A12; EDGE1–EDGE17)
# ===========================================================================
# Wraps the S5 clean relocate with the dirty transactional layer. Reuses every
# shipped primitive (the drain flock + main-pinned merge=union via
# worktree_cutover.py/bookkeeping_lock.py, the create-or-lookup add+sparse+hook
# tail, the EDGE14 detach / EDGE17 restore mechanics). Order is transaction
# discipline: all A2 pre-flights + the drain-lock acquire can HALT before any
# topology change (nothing moved); the failure fork is by KIND. NEVER a bare
# `git stash pop`, `git stash drop`, `git clean -f`, `rm -rf`, blanket
# `--no-verify`, or `git reset --hard`; the ONLY `--force` is the design-mandated
# `git worktree remove . --force` inside the EDGE8 abort advisory (printed text).

_is_reserved_branch() { case "$1" in main|master|head) return 0 ;; *) return 1 ;; esac; }

# Detect an in-progress sequencer op (merge/rebase/cherry-pick/revert/bisect) in
# ONE working dir. EDGE13/Review-Patch-A2: EVERY sequencer-state path is resolved
# via `git rev-parse --git-path <name>` — NEVER a hardcoded `.git/…` literal,
# because inside a linked worktree `.git` is a FILE and the real gitdir is
# elsewhere, and a submodule's gitdir is always a file under `.git/modules/…`.
_sequencer_in_progress() {  # $1 workdir → 0 if any sequencer state present
  local wt="$1" name p
  for name in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG BISECT_START rebase-merge rebase-apply; do
    p="$(git -C "$wt" rev-parse --git-path "$name" 2>/dev/null)" || continue
    [ -n "$p" ] || continue
    case "$p" in /*) : ;; *) p="$wt/$p" ;; esac
    [ -e "$p" ] && return 0
  done
  return 1
}

# EDGE13/EDGE15: halt if ANY sequencer op is in progress in the superproject OR
# in any (recursive) submodule — before the drain or any stash/worktree step.
_preflight_sequencers() {  # $1 main_wt → 0 ok, 1 halt
  local main_wt="$1" subdir
  if _sequencer_in_progress "$main_wt"; then
    log "HALT: an in-progress merge/rebase/cherry-pick/revert/bisect is present in the primary clone ($main_wt) — finish or --abort it first. Nothing moved."
    return 1
  fi
  while IFS= read -r subdir; do
    [ -n "$subdir" ] || continue
    if _sequencer_in_progress "$subdir"; then
      log "HALT: an in-progress sequencer op is present INSIDE submodule '$subdir' — finish or --abort it there first. Nothing moved."
      return 1
    fi
  done < <(git -C "$main_wt" submodule foreach --recursive --quiet 'printf "%s\n" "$(pwd)"' 2>/dev/null)
  return 0
}

# EDGE15: `git stash -u` does NOT stash submodule changes, so a dirty submodule's
# work would be silently stranded — halt up front.
_preflight_dirty_submodule() {  # $1 main_wt → 0 ok, 1 halt
  local main_wt="$1" out
  out="$(git -C "$main_wt" submodule foreach --recursive --quiet 'git status --porcelain' 2>/dev/null)"
  if [ -n "$out" ]; then
    log "HALT: a submodule has uncommitted changes (git stash -u does NOT carry submodule work — it would be stranded). Commit/clean the submodule first. Nothing moved."
    return 1
  fi
  return 0
}

# EDGE11/EDGE16 prompt-or-fail-closed. Sets NEW_BRANCH on a TTY; returns 3 (and
# prints guidance) in a non-TTY session (M4/R5 fail-closed parity).
NEW_BRANCH=""
_edge_new_branch_or_fail() {  # $1 kind(detached|is-main) $2 main_wt → 0 (NEW_BRANCH set) | 3
  NEW_BRANCH=""
  local kind="$1"
  if [ ! -t 0 ]; then
    case "$kind" in
      detached)
        log "HALT (EDGE11): the primary clone is on a DETACHED HEAD (no branch to migrate) and this is a non-interactive session — failing closed. Check out a topic branch (git checkout -b <topic>) then re-run. Nothing moved." ;;
      is-main)
        log "HALT (EDGE16): the active branch is the protected/canonical branch — the canonical branch is never relocated out of the primary clone, and this non-interactive session cannot be prompted for a new topic branch — failing closed. Start from a topic branch (git checkout -b <topic>) then re-run. Nothing moved." ;;
    esac
    return 3
  fi
  local raw nb
  printf '[worktree-helper] %s — enter a NEW topic branch name to migrate this work onto: ' "$kind" >&2
  read -r raw
  nb="$(slugify "$raw")"
  [ -n "$nb" ] || { log "HALT: empty branch name — nothing moved."; return 3; }
  if _is_reserved_branch "$nb"; then log "HALT: '$nb' is reserved — nothing moved."; return 3; fi
  NEW_BRANCH="$nb"
  return 0
}

# EDGE1/EDGE7: copy declared config-manifest entries (gitignored .env-class
# config `git stash -u` does NOT carry) into the new worktree, copy-if-exists.
# A missing/unreadable REQUIRED entry → return 2 (caller runs EDGE2 rollback,
# still pre-pop). A missing/unreadable OPTIONAL entry → one-line advisory +
# continue. Every file copied is appended to the copied-list (EDGE2 cleanup).
_copy_config_manifest() {  # $1 src_wt $2 dest $3 copied_list_file → 0 ok, 2 required-fail
  local src_wt="$1" dest="$2" clist="$3" req path src dst
  while IFS="$(printf '\t')" read -r req path; do
    [ -n "$path" ] || continue
    src="$src_wt/$path"; dst="$dest/$path"
    if [ ! -e "$src" ]; then
      if [ "$req" = "1" ]; then
        log "EDGE7: REQUIRED config '$path' is not present in the source clone — aborting BEFORE pop (nothing popped to destroy)."; return 2
      fi
      log "EDGE7: optional config '$path' not present — skipping (advisory)."; continue
    fi
    if [ ! -r "$src" ]; then
      if [ "$req" = "1" ]; then
        log "EDGE7: REQUIRED config '$path' is unreadable (EACCES) — aborting BEFORE pop."; return 2
      fi
      log "EDGE7: optional config '$path' unreadable (EACCES) — skipping (advisory)."; continue
    fi
    mkdir -p "$(dirname "$dst")" 2>/dev/null || true
    if command cp -p "$src" "$dst" 2>/dev/null; then
      printf '%s\n' "$dst" >> "$clist"
    elif [ "$req" = "1" ]; then
      log "EDGE7: could not copy REQUIRED config '$path' — aborting BEFORE pop."; return 2
    else
      log "EDGE7: could not copy optional config '$path' — skipping (advisory)."; continue
    fi
  done < <(python3 "$CUTOVER_MODULE" config-manifest --repo "$src_wt" 2>/dev/null)
  return 0
}

# EDGE2 FULL unwind — ONLY for a `git worktree add` failure (code NEVER migrated).
# Order (design A12 / Review Patch A3): remove any REGISTERED partial worktree
# (no `--force`, no `rm -rf`) → delete ONLY files THIS migration copied into the
# worktree DEST (a NO-OP when nothing was copied — the primary's ORIGINAL config
# is NEVER touched) → reverse-BND2 restore the primary to its original branch →
# `git stash pop --index` to RESTORE the operator's uncommitted work (MANDATORY;
# NEVER `git stash drop`, which would delete it).
_edge2_rollback() {  # $1 repo $2 branch $3 dest $4 main_wt $5 had_stash $6 copied_list_file
  local repo="$1" branch="$2" dest="$3" main_wt="$4" had_stash="$5" clist="$6" f
  if worktree_registered "$repo" "$dest"; then
    git -C "$repo" worktree remove "$dest" >/dev/null 2>&1 \
      || log "rollback: could not remove worktree $dest — prune it manually (git -C \"$repo\" worktree prune)."
  fi
  git -C "$repo" worktree prune >/dev/null 2>&1 || true
  if [ -n "$clist" ] && [ -f "$clist" ]; then
    while IFS= read -r f; do [ -n "$f" ] && rm -f "$f"; done < "$clist"
  fi
  git -C "$main_wt" checkout "$branch" >/dev/null 2>&1 \
    || log "rollback: could not restore the primary to '$branch' (it is detached); restore manually:  git -C \"$main_wt\" checkout $branch"
  if [ "$had_stash" = 1 ]; then
    git -C "$main_wt" stash pop --index >/dev/null 2>&1 \
      || log "rollback: 'git stash pop --index' reported an issue — your work is PRESERVED in the stash (git -C \"$main_wt\" stash list); restore it with:  git -C \"$main_wt\" stash pop --index"
  fi
}

# EDGE8 pop-conflict advisory (the ONLY `--force` in the helper — printed text).
_emit_edge8_pop_conflict() {  # $1 dest $2 branch $3 main_wt
  cat >&2 <<MSG
[worktree-helper] HALT (EDGE8): your work is SAFELY in the new worktree at
$1, but 'git stash pop --index' hit a MERGE CONFLICT. This is deliberately NOT
rolled back — the code already migrated; a rollback would destroy it.

  RESOLVE IN PLACE (recommended — preserves your resolutions):
    cd "$1"
    # resolve the conflicted files, then commit/keep them. The stashed copy
    # remains in 'git stash list' as a backup you can clear once satisfied.

  OR ABORT the migration — ⚠️ WARNING: this DISCARDS any manual conflict
  resolutions you made inside the worktree (resolve-in-place keeps them):
    i.   cd "$1" && git worktree remove . --force
    ii.  cd back to the primary clone ($3)
    iii. git -C "$3" checkout $2
    iv.  git -C "$3" stash pop --index      # RESTORES your uncommitted work
MSG
}

# EDGE17: restore the primary to `main` after a SUCCESSFUL cutover (graceful
# fallback when `main` is checked out elsewhere).
_restore_primary_to_main() {  # $1 main_wt $2 repo
  local def; def="$(default_branch "$2")"
  git -C "$1" checkout "$def" >/dev/null 2>&1 \
    || log "cutover: migration SUCCEEDED, but the primary could not be auto-restored to '$def' (it may be checked out in another worktree). The primary is on a detached HEAD — restore it manually:  git -C \"$1\" checkout $def"
}

# EDGE5 rebuild AFTER worktree add (code already safe): submodule init first
# (worktree add does NOT init submodules), then declared rebuild command(s). A
# failure ALERTS (worktree is non-executable until re-run) but NEVER rolls back.
_rebuild_worktree() {  # $1 dest $2 src_wt
  local dest="$1" src_wt="$2" cmd
  git -C "$dest" submodule update --init --recursive >/dev/null 2>&1 \
    || log "EDGE5: 'git submodule update --init --recursive' failed — the worktree is NOT yet executable (empty submodule dirs). The migration is intact (NOT rolled back); re-run:  git -C \"$dest\" submodule update --init --recursive"
  while IFS= read -r cmd; do
    [ -n "$cmd" ] || continue
    if ! ( cd "$dest" && eval "$cmd" ) >/dev/null 2>&1; then
      log "EDGE5: rebuild command failed (\`$cmd\`) — the worktree may be non-executable until you re-run it in $dest. The migration is intact (NOT rolled back)."
    fi
  done < <(python3 "$CUTOVER_MODULE" rebuild-commands --repo "$src_wt" 2>/dev/null)
}

# EDGE6: name unmanifested ignored files left behind in the primary (not lost —
# just not auto-migrated).
_edge6_advisory() {  # $1 main_wt
  local ign
  ign="$(git -C "$1" ls-files --others --ignored --exclude-standard 2>/dev/null | head -5)"
  [ -n "$ign" ] && log "EDGE6: unmanifested ignored files remain in the primary clone (NOT auto-migrated — not lost; copy any still needed by hand): $(printf '%s' "$ign" | tr '\n' ' ')"
  return 0
}

# EDGE10: `git stash push -u` under a bounded timeout (macOS has no `timeout`;
# use a perl SIGALRM+exec shim). On a stall, the caller surfaces a large-untracked
# advisory. `push` (not `pop`) is safe; `pop` always carries `--index`.
_stash_domain() {  # $1 main_wt → git/perl rc (0 ok, non-zero stall/fail)
  local secs="${WORKTREE_STASH_TIMEOUT:-120}"
  perl -e 'my $t=shift; alarm $t; exec @ARGV or exit 127;' "$secs" \
    git -C "$1" stash push --include-untracked -m "s6-cutover" >/dev/null 2>&1
}

cmd_cutover_dirty() {  # $1 repo $2 branch(cur) $3 dest $4 main_wt $5 repos_root
  local repo="$1" branch="$2" dest="$3" main_wt="$4" repos_root="$5"

  # --- EDGE11/EDGE16: no migratable topic branch → prompt-or-fail-closed ------
  if [ -z "$branch" ]; then
    _edge_new_branch_or_fail detached "$main_wt" || exit 3
    git -C "$main_wt" checkout -b "$NEW_BRANCH" >/dev/null 2>&1 \
      || die "cutover: could not name the detached work onto '$NEW_BRANCH' — nothing moved"
    branch="$NEW_BRANCH"
    dest="$repos_root/$(basename "$(realpath "$repo" 2>/dev/null || echo "$repo")")/$branch"
  elif _is_reserved_branch "$branch"; then
    _edge_new_branch_or_fail is-main "$main_wt" || exit 3
    git -C "$main_wt" checkout -b "$NEW_BRANCH" >/dev/null 2>&1 \
      || die "cutover: could not create new topic branch '$NEW_BRANCH' — nothing moved"
    branch="$NEW_BRANCH"
    dest="$repos_root/$(basename "$(realpath "$repo" 2>/dev/null || echo "$repo")")/$branch"
  fi

  log "cutover: DIRTY transactional migration of '$branch' out of the primary clone into its worktree"

  # --- A2 pre-flights — HALT before any topology change (nothing moved) -------
  _preflight_sequencers "$main_wt" || exit 3
  _preflight_dirty_submodule "$main_wt" || exit 3

  # --- A1 (1-2): DRAIN main-owned bookkeeping to `main` FIRST, under the flock,
  #     never into the stash (greenfield; EDGE9 fail-closed; EDGE12 lock abort) -
  local dout drc
  dout="$(python3 "$CUTOVER_MODULE" drain --repo "$main_wt" 2>&1)"; drc=$?
  if [ "$drc" -ne 0 ]; then
    printf '%s\n' "$dout" >&2
    case "$drc" in
      12) log "cutover: could not acquire the bookkeeping drain lock — nothing moved. Retry shortly."; exit 3 ;;
      9)  log "cutover: bookkeeping drain halted (see above) — nothing moved."; exit 3 ;;
      *)  die "cutover: bookkeeping drain failed — nothing moved" ;;
    esac
  fi

  # --- A1 (3): carry ONLY the domain edits via `git stash push -u` (EDGE10) ----
  local before after had_stash=0
  before="$(git -C "$main_wt" rev-parse -q --verify refs/stash 2>/dev/null || printf 'none')"
  if ! _stash_domain "$main_wt"; then
    die "cutover: 'git stash push -u' stalled or failed (EDGE10 — a large un-ignored dir like node_modules? add it to .gitignore and re-run). Nothing moved beyond the (committed) drain."
  fi
  after="$(git -C "$main_wt" rev-parse -q --verify refs/stash 2>/dev/null || printf 'none')"
  [ "$before" != "$after" ] && had_stash=1

  # --- EDGE14: free the branch (detached HEAD; safe even if main is elsewhere) -
  if ! git -C "$main_wt" checkout --detach HEAD >/dev/null 2>&1; then
    [ "$had_stash" = 1 ] && git -C "$main_wt" stash pop --index >/dev/null 2>&1
    die "cutover: could not detach the primary to free '$branch' — your work is restored; nothing migrated"
  fi

  # --- worktree add via the shipped create-or-lookup add+sparse+hook tail ------
  local out
  if ! out="$(cmd_create_or_lookup --repo "$repo" --topic "$branch" --repos-root "$repos_root")"; then
    _edge2_rollback "$repo" "$branch" "$dest" "$main_wt" "$had_stash" ""
    die "cutover: 'git worktree add' failed — FULLY rolled back (primary on '$branch', your uncommitted work restored staged-as-staged); nothing migrated"
  fi

  # --- config: REQUIRED presence check + copy, BEFORE pop (EDGE1/EDGE7) --------
  local clist; clist="$(mktemp)"
  if ! _copy_config_manifest "$main_wt" "$dest" "$clist"; then
    _edge2_rollback "$repo" "$branch" "$dest" "$main_wt" "$had_stash" "$clist"
    rm -f "$clist"
    die "cutover: a REQUIRED config entry is missing/unreadable — rolled back BEFORE pop (primary on '$branch', your work restored); nothing migrated"
  fi

  # --- `git stash pop --index` into the worktree (EDGE8: conflict → HALT, no
  #     rollback; --index MANDATORY — a bare pop unstages, erasing curated state) -
  if [ "$had_stash" = 1 ]; then
    if ! git -C "$dest" stash pop --index >/dev/null 2>&1; then
      _emit_edge8_pop_conflict "$dest" "$branch" "$main_wt"
      rm -f "$clist"
      _restore_primary_to_main "$main_wt" "$repo"
      exit 1
    fi
  fi
  rm -f "$clist"

  # --- EDGE5 rebuild AFTER worktree add (code already safe; alert, no rollback) -
  _rebuild_worktree "$dest" "$main_wt"

  # --- EDGE17 restore primary to `main`; EDGE6 advisory -----------------------
  _restore_primary_to_main "$main_wt" "$repo"
  _edge6_advisory "$main_wt"

  printf '%s\n' "$out"
  return 0
}

cmd_place() {
  # Placement path for a work-initiation surface (A4). Routing:
  #   * primary DIRTY               → S6 transactional dirty cutover (cmd_cutover_dirty);
  #   * primary CLEAN + DETACHED    → EDGE11 prompt-or-fail-closed (exit 3 non-TTY);
  #   * branch <slug> IS the primary's checked-out branch (clean) → S5 CLEAN
  #     RELOCATE (cmd_cutover_clean) — migrate it into its own worktree;
  #   * otherwise                   → cmd_create_or_lookup (fresh / elsewhere / idempotent).
  # Deterministic; the caller (/work-start Step 4b) wraps it with the per-topic
  # lock trap. Exit: 0 placed (prints worktree path); 3 halt-before-topology; 1 error.
  local repo="" topic="" repos_root="${HOME}/repos"
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo)       repo="${2:-}"; shift 2 ;;
      --topic)      topic="${2:-}"; shift 2 ;;
      --repos-root) repos_root="${2:-}"; shift 2 ;;
      *) die "place: unknown argument: $1" ;;
    esac
  done
  [ -n "$topic" ] || die "place: --topic is required"
  if [ -z "$repo" ]; then
    repo="$(the configured source path 2>/dev/null)" \
      || die "no --repo given and cannot resolve the configured source path (harness repo-binding)"
  fi
  [ -d "$repo" ] || die "repo path does not exist: $repo"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo"

  local slug; slug="$(slugify "$topic")"
  [ -n "$slug" ] || die "place: topic '$topic' slugifies to empty — refusing an empty branch/path"

  # REQUESTED-TOPIC-IS-MAIN → the canonical branch is never placed into a topic
  # worktree (A1). Match the reserved names LITERALLY — do NOT call
  # is_protected()/default_branch() here: default_branch() falls back to the
  # CURRENT branch when there is no remote origin/HEAD, so it would false-positive
  # every topic while the session sits on its own topic branch (the cutover's
  # normal state). create-or-lookup's is_protected() is the backstop after the
  # branch is freed. EDGE16: prompt for a NEW topic branch to migrate the on-`main`
  # work onto instead; a non-TTY session FAILS CLOSED (exit 3), nothing moved.
  case "$slug" in
    main|master|head)
      _edge_new_branch_or_fail is-main "" || exit 3
      # TTY: name the current work onto NEW_BRANCH, then re-place it below.
      local _mwt_im; _mwt_im="$(main_worktree "$repo")"
      git -C "$_mwt_im" checkout -b "$NEW_BRANCH" >/dev/null 2>&1 \
        || die "place: could not create new topic branch '$NEW_BRANCH' — nothing moved"
      slug="$NEW_BRANCH"; topic="$NEW_BRANCH" ;;
  esac

  local main_wt; main_wt="$(main_worktree "$repo")"
  [ -n "$main_wt" ] || die "place: could not resolve the primary working tree for $repo"

  # DIRTY primary → the S6 transactional dirty cutover. The cutover itself runs
  # the A2 pre-flights (halt-before-topology) + EDGE11/EDGE16 handling; here we
  # only route. It migrates the branch the primary is CURRENTLY on (which holds
  # the dirty work), which equals <slug> in the normal /work-start flow.
  local cur; cur="$(current_branch "$main_wt")"
  if [ -n "$(git -C "$main_wt" status --porcelain 2>/dev/null)" ]; then
    local repo_name_d dest_d
    repo_name_d="$(basename "$(realpath "$repo" 2>/dev/null || echo "$repo")")"
    dest_d="$repos_root/$repo_name_d/$slug"
    cmd_cutover_dirty "$repo" "$cur" "$dest_d" "$main_wt" "$repos_root"
    return $?
  fi

  # CLEAN + DETACHED primary → EDGE11 prompt-or-fail-closed. A detached HEAD has
  # no branch to relocate; a non-TTY session FAILS CLOSED (exit 3), nothing moved.
  if [ -z "$cur" ]; then
    _edge_new_branch_or_fail detached "$main_wt" || exit 3
    # TTY: name the (clean) detached HEAD onto NEW_BRANCH, then clean-relocate it.
    git -C "$main_wt" checkout -b "$NEW_BRANCH" >/dev/null 2>&1 \
      || die "place: could not create '$NEW_BRANCH' from the detached HEAD — nothing moved"
    local dest_e; dest_e="$repos_root/$(basename "$(realpath "$repo" 2>/dev/null || echo "$repo")")/$NEW_BRANCH"
    cmd_cutover_clean "$repo" "$NEW_BRANCH" "$dest_e" "$main_wt" "$repos_root"
    return $?
  fi

  # CLEAN + on a branch. Is <slug> the branch currently checked out in the PRIMARY
  # clone? → S5 legacy cutover: relocate it into its own worktree in one step.
  local repo_name dest
  repo_name="$(basename "$(realpath "$repo" 2>/dev/null || echo "$repo")")"
  dest="$repos_root/$repo_name/$slug"
  local active_wt; active_wt="$(worktree_for_branch "$repo" "$slug")"
  if [ -n "$active_wt" ] && [ "$active_wt" = "$main_wt" ] && [ "$active_wt" != "$dest" ]; then
    cmd_cutover_clean "$repo" "$slug" "$dest" "$main_wt" "$repos_root"
    return $?
  fi

  # Otherwise: normal placement (fresh topic / branch elsewhere / already a worktree).
  cmd_create_or_lookup --repo "$repo" --topic "$topic" --repos-root "$repos_root"
}

cmd_install() {
  local repo=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) repo="${2:-}"; shift 2 ;;
      *) die "install: unknown argument: $1" ;;
    esac
  done
  [ -n "$repo" ] || die "install: --repo is required"
  [ -d "$repo" ] || die "repo path does not exist: $repo"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo"
  install_all_hooks "$repo" || die "hook install failed for $repo"
  log "hooks installed in $repo (pre-commit worktree gate + pre-push target guard)"
}

# ---------------------------------------------------------------------------
# uninstall — the inverse of install_one_hook (declared-publish-scope S2 / A3)
#
# WHY THIS EXISTS, and which shape actually bricks a repo — MEASURED, because
# the first version of this note (and the plan's rollback section) named the
# wrong one. `.git/hooks/` is NOT config-source-managed, so reverting a promotion
# deletes the hook SCRIPT in ~/.claude/hooks while `.git/hooks/<name>` still
# refers to it. What happens next depends on the shape:
#
#   * `.git/hooks/<name>` is a SYMLINK -> it dangles, git cannot execute it and
#     SKIPS the hook. Commits proceed. Measured: exit 0.
#   * `.git/hooks/<name>` is a generated WRAPPER (a regular executable file
#     holding an absolute TARGET) -> the wrapper RUNS, the target is gone, and
#     the hook fails:
#       .git/hooks/<name>: line N: /path/to/target: No such file or directory
#     git then refuses EVERY subsequent commit in that repo. Measured: exit 1.
#
# So the hazard needs the WRAPPER shape, which only arises where a pre-existing
# user hook was chained behind ours.
#
# WHICH MEANS IT IS NOT REACHABLE TODAY BY AN S2 REVERT, and saying otherwise
# was wrong TWICE. The first version of this note claimed a revert bricks any
# repo; the second claimed `~/repos/Projects` specifically would brick. Both are
# false for `commit-msg`. Measured state of the two repos:
#
#   <config-source-repo> : pre-commit -> symlink, pre-push -> symlink,
#                            NO commit-msg hook at all
#   ~/repos/Projects       : pre-commit -> wrapper (a user hook was chained),
#                            pre-push -> symlink to the identity guard,
#                            NO commit-msg hook at all
#
# Since neither repo has a `commit-msg` hook, `install_one_hook` takes its
# `absent` branch and creates a SYMLINK in both — the shape git skips when it
# dangles. And Projects' wrapper belongs to `pre-commit`, whose target S2
# modifies but never deletes. So an S2 revert bricks nothing as things stand.
#
# The verb is still correct to have, for two reasons that do not depend on that:
# a repo that later gains its own `commit-msg` hook WILL get the wrapper shape
# and then the hazard is live, and the verb is also the only way to cleanly
# remove what install placed. What was wrong was the urgency, not the need.
# Uninstall recovers a repo already in the bricked state too, because a wrapper
# is classified by its sentinel without consulting `realpath`.
#
# It reverses exactly what install_one_hook did, and REFUSES anything it did
# not create: an alien symlink, a user's own hook, or a non-hook file are left
# untouched (fail-closed — removing someone else's hook is the destructive
# mistake this verb exists to avoid making).
uninstall_one_hook() {  # $1 hooks_dir $2 hookname $3 target
  local hooks_dir="$1" name="$2" target="$3"
  local hp="$hooks_dir/$name" state chained

  state="$(classify_hook "$hp" "$target")"
  case "$state" in
    absent)
      log "uninstall: $name not present — nothing to do"
      return 0 ;;
    ours-symlink)
      command rm -f "$hp" || { log "uninstall: could not remove symlink $hp"; return 1; }
      log "uninstall: removed $name symlink"
      return 0 ;;
    ours-wrapper)
      # THE SENTINEL IS NOT ENOUGH TO PROVE OWNERSHIP. `classify_hook` matches a
      # wrapper by the sentinel string alone, and `install_canonical_guard_hooks`
      # generates pre-commit/pre-push wrappers carrying the SAME sentinel for a
      # DIFFERENT target. Closing the bare `uninstall` form did not close this:
      # `uninstall --hook pre-commit` would still delete the canonical-repo
      # identity guard's wrapper and move its `.chained` back over it. Verify the
      # wrapper actually serves the target we were asked to remove.
      if ! grep -qF "TARGET=\"$target\"" "$hp" 2>/dev/null; then
        log "uninstall: REFUSING to remove $name — it is our wrapper shape but"
        log "  serves a different target than $target (another slice installed it)."
        log "  Nothing was removed."
        # NON-ZERO. A refusal to act on an EXPLICITLY NAMED hook is a failed
        # rollback, not a success: the plan's two-line rollback recipe would
        # otherwise report clean while having removed nothing — e.g. if the
        # wrapper's baked TARGET came from a different config dir (a
        # `config-experiment` staging clone). The `absent` branch below still
        # returns 0, because nothing to do genuinely is success.
        return 1
      fi
      chained="$hp.chained"
      command rm -f "$hp" || { log "uninstall: could not remove wrapper $hp"; return 1; }
      if [ -e "$chained" ]; then
        # Restore the user's original hook to its rightful place. Moving it
        # back (not copying) leaves no stale duplicate for a later install to
        # chain a second time.
        command mv -f "$chained" "$hp" || {
          # Nothing is destroyed — the user's hook is still at <hook>.chained —
          # but it now runs for nobody, and a log line alone is easy to miss.
          # Say plainly what state the repo is in and the one command that fixes
          # it, on stderr, because this is the path where silence costs most.
          log "uninstall: removed the wrapper but could NOT restore $chained -> $hp"
          printf '\n[worktree-helper] ACTION NEEDED in %s\n' "$hooks_dir" >&2
          printf '  Your original %s hook is parked at:\n    %s\n' "$name" "$chained" >&2
          printf '  It is NOT running. Restore it with:\n    mv %s %s\n\n' "$chained" "$hp" >&2
          return 1; }
        chmod +x "$hp" 2>/dev/null || true
        log "uninstall: removed $name wrapper and restored the chained user hook"
      else
        log "uninstall: removed $name wrapper (no chained hook to restore)"
      fi
      return 0 ;;
    alien-symlink|sibling-symlink|user-hook|other)
      log "uninstall: REFUSING to touch $name — it is not ours ($state)."
      log "  Nothing was removed. This verb only reverses what install created."
      return 0 ;;
  esac
  return 1
}

cmd_uninstall() {
  local repo="" hook="" common common_abs hooks_dir target rc=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) repo="${2:-}"; shift 2 ;;
      --hook) hook="${2:-}"; shift 2 ;;
      *) die "uninstall: unknown argument: $1" ;;
    esac
  done
  [ -n "$repo" ] || die "uninstall: --repo is required"
  [ -d "$repo" ] || die "repo path does not exist: $repo"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo"

  common="$(git -C "$repo" rev-parse --git-common-dir 2>/dev/null)" \
    || die "uninstall: cannot resolve git-common-dir for $repo"
  case "$common" in /*) common_abs="$common" ;; *) common_abs="$repo/$common" ;; esac
  hooks_dir="$common_abs/hooks"
  [ -d "$hooks_dir" ] || { log "uninstall: no hooks dir at $hooks_dir — nothing to do"; return 0; }

  # Map hook name -> the target install would have pointed it at.
  _target_for() {
    case "$1" in
      pre-commit)  printf '%s' "$GATE_SRC" ;;
      pre-push)    printf '%s' "$PUSH_SRC" ;;
      commit-msg)  printf '%s' "$TRAILER_SRC" ;;
      *)           printf '' ;;
    esac
  }

  if [ -n "$hook" ]; then
    target="$(_target_for "$hook")"
    [ -n "$target" ] || die "uninstall: unknown hook name: $hook"
    uninstall_one_hook "$hooks_dir" "$hook" "$target" || rc=1
  else
    # A bare `uninstall --repo X` is REFUSED. `classify_hook` identifies "ours"
    # by the wrapper sentinel alone, and `install_canonical_guard_hooks`
    # generates wrappers carrying the SAME sentinel for a different target — so
    # a sweep over pre-commit/pre-push would silently remove the canonical-repo
    # identity guard along with this slice's hooks. The plan's rollback recipe
    # always names `--hook commit-msg`; requiring it turns a live footgun into
    # an explicit choice.
    die "uninstall: --hook is required (e.g. --hook commit-msg).
  A bare uninstall would also remove hooks installed by other slices that share
  the same wrapper sentinel — including the canonical-repo identity guard."
  fi
  return "$rc"
}

# ---------------------------------------------------------------------------
# coverage — installation coverage of the declared-publish-scope hooks
# (glittery-humming-pine S6 / A7b). Runs BEFORE the gate enforces, and after.
#
# Coverage is per HOOK TYPE, not per repo: a gated repo must carry BOTH the
# `pre-commit` scope gate AND the `commit-msg` waiver trailer. A repo with only
# the first enforces scope while silently dropping the record of every
# `ALLOW_UNSCOPED_COMMIT=1` waiver — C7 lost exactly where it is needed.
#
# Every git dir the harness commits into is either GATED (checked below) or
# ALLOWLISTED with a stated reason (printed, so the exemption is a decision on
# record rather than an oversight). Measured 2026-09-17, before this verb
# existed: both gated repos had the pre-commit gate and NEITHER had the
# commit-msg trailer — every waiver since S2 left no trace in history.
#
# Exit: 0 every gated repo covered; 1 at least one gap (named); 2 usage.

# The gated repositories. Overridable ONLY for the sandbox test harness.
_gated_repos() {
  if [ -n "${WORKTREE_HELPER_GATED_REPOS:-}" ]; then
    printf '%s\n' $WORKTREE_HELPER_GATED_REPOS
    return
  fi
  printf '%s\n' "$HOME/repos/Projects"
  the configured source path 2>/dev/null || true
}

# Allowlisted commit targets: `<path-or-pattern>|<reason>`. Data, not logic.
COVERAGE_ALLOWLIST=(
  "$HOME/.claude/.git|frozen local snapshot (git-policy.md §2: never hand-commit it; remote is config-source-remote-legacy). No converted surface publishes there: starter-kit 6a was rerouted to config-promote (S6), /ninja-fix routes ~/.claude edits to config-promote, and commit_scope.py publish REFUSES this root (post-S8 audit fix)."
  "starter-kit 6b temp clone (mktemp)|fresh private clone of starter-kit-claude with its own index; no other session can stage into it."
  "worktree_cutover.py plumbing commits|commit-tree + update-ref fire no pre-commit hook at all; bounded by a scratch GIT_INDEX_FILE instead (build-time checker territory, S7/A9)."
  "independent project repos (e.g. Personal/Per-App-Network-Routing, [YourProject]/[you], ~/repos/Q, ~/repos/double-check)|not in the worktree-per-topic model; installing this gate would also impose its out-of-worktree BLOCK on every ordinary commit there. /close and /ninja-fix publish into them through commit_scope.py publish, which scopes both verbs whether or not a hook runs."
)

# Commit sites that are NOT exempt — they run inside a gated repo and are
# covered by its hooks — but carry a known issue worth keeping on record.
# Deliberately a SEPARATE list from the allowlist: filing these under "not
# gated" would misstate their disposition (an independent checker caught that).
COVERAGE_KNOWN_ISSUES=(
  "land_port.py ephemeral promotion worktree|covered: a linked worktree of the config source, so it SHARES that repo's hooks. Known issue: its frozen-candidate commit (land_port.py:828) scopes the add but not the commit, so the enforcing gate would REFUSE it. Unreachable today — PromotionAdapter refuses unwired seams first (git-policy.md §11). Scope the commit on revival (hooks/land-port-revival-checklist.md)."
)

# Is <hookpath> an installation of a hook whose script basename is <basename>?
# Matched by BASENAME of the target, deliberately: the question here is "is the
# gate installed", and the same hook is legitimately referenced from different
# config roots (live ~/.claude, a config-experiment clone). The installer's own
# ownership checks stay strict; this is a read-only report.
_hook_installed_as() {  # $1 hookpath  $2 target-basename → 0 yes
  local hp="$1" want="$2" tgt
  if [ -L "$hp" ]; then
    tgt="$(readlink "$hp" 2>/dev/null)"
  elif [ -f "$hp" ] && grep -q "$WRAPPER_SENTINEL" "$hp" 2>/dev/null; then
    tgt="$(sed -n 's/^TARGET="\(.*\)"$/\1/p' "$hp" 2>/dev/null | head -1)"
  else
    return 1
  fi
  [ -n "$tgt" ] && [ "${tgt##*/}" = "$want" ] && [ -x "$tgt" ]
}

cmd_coverage() {
  [ $# -eq 0 ] || die "coverage: takes no arguments"
  local repo common hooks_dir gaps=0 n=0
  printf 'declared-publish-scope hook coverage\n'
  while IFS= read -r repo; do
    [ -n "$repo" ] || continue
    n=$((n+1))
    if ! git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
      printf '  GAP      %s — not a git repository\n' "$repo"; gaps=$((gaps+1)); continue
    fi
    common="$(git -C "$repo" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
    hooks_dir="$common/hooks"
    local hp_ok=1
    if _hook_installed_as "$hooks_dir/pre-commit" "${GATE_SRC##*/}"; then :; else
      printf '  GAP      %s — pre-commit scope gate not installed\n' "$repo"; hp_ok=0
    fi
    if _hook_installed_as "$hooks_dir/commit-msg" "${TRAILER_SRC##*/}"; then :; else
      printf '  GAP      %s — commit-msg waiver trailer not installed (C7 lost)\n' "$repo"; hp_ok=0
    fi
    if [ "$hp_ok" -eq 1 ]; then
      printf '  COVERED  %s (pre-commit + commit-msg)\n' "$repo"
    else
      gaps=$((gaps+1))
    fi
  done < <(_gated_repos)
  printf 'allowlisted (not gated, with reason):\n'
  local e
  for e in "${COVERAGE_ALLOWLIST[@]}"; do
    printf '  - %s\n      %s\n' "${e%%|*}" "${e#*|}"
  done
  printf 'covered, with a known issue on record:\n'
  for e in "${COVERAGE_KNOWN_ISSUES[@]}"; do
    printf '  - %s\n      %s\n' "${e%%|*}" "${e#*|}"
  done
  if [ "$n" -eq 0 ]; then
    printf 'no gated repositories resolved — that is itself a gap\n'; return 1
  fi
  if [ "$gaps" -gt 0 ]; then
    printf '%s gated repo(s) with gaps — run: worktree-helper.sh install --repo <repo>\n' "$gaps"
    return 1
  fi
  printf 'all %s gated repo(s) covered\n' "$n"
  return 0
}

# storage-decouple S2 — install the canonical-repo identity guard on a repo.
cmd_install_canonical_guard() {
  local repo=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --repo) repo="${2:-}"; shift 2 ;;
      *) die "install-canonical-guard: unknown argument: $1" ;;
    esac
  done
  [ -n "$repo" ] || die "install-canonical-guard: --repo is required"
  [ -d "$repo" ] || die "repo path does not exist: $repo"
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "not a git repo: $repo"
  install_canonical_guard_hooks "$repo" || die "identity-guard install failed for $repo"
  log "canonical identity guard installed in $repo (pre-commit + pre-push; pre-existing hooks chained)"
}

# ---------------------------------------------------------------------------
main() {
  [ $# -ge 1 ] || { printf 'usage: worktree-helper.sh {create-or-lookup|place|install} ...\n' >&2; exit 2; }
  local sub="$1"; shift
  case "$sub" in
    create-or-lookup) cmd_create_or_lookup "$@" ;;
    place)            cmd_place "$@" ;;
    install)          cmd_install "$@" ;;
    uninstall)        cmd_uninstall "$@" ;;
    coverage)         cmd_coverage "$@" ;;
    install-canonical-guard) cmd_install_canonical_guard "$@" ;;
    -h|--help)        grep -E '^# ' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) printf 'worktree-helper.sh: unknown subcommand: %s\n' "$sub" >&2; exit 2 ;;
  esac
}

main "$@"

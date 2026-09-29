#!/usr/bin/env bash
# check-unscoped-commit-trailer — git commit-msg hook (S2 / A3, claim C7).
#
# When an operator publishes an UNSCOPED commit by explicitly naming the
# override, stamp a trailer into the commit message so the waiver is legible in
# `git log` to a later reader — not merely in a local state file that is not
# version-controlled and that nobody reading the history months later ever sees.
#
# WHY A SECOND HOOK FILE. A `pre-commit` hook cannot edit the commit message;
# only `commit-msg` can. The two hooks do NOT need to talk to each other: git
# runs both as children of the same `git commit`, so `ALLOW_UNSCOPED_COMMIT=1`
# is present in the environment of each, and this hook reads it directly rather
# than receiving a handoff from the gate.
#
# THE VARIABLE MEANS "the operator authorized an unscoped commit", NOT "an
# unscoped commit happened". If it is ambiently exported — which is exactly what
# a careless `export ALLOW_UNSCOPED_COMMIT=1` in a shell profile would do — then
# stamping on the variable alone would mislabel properly scoped commits as
# waived, and the trailer would stop meaning anything. So this hook stamps only
# when BOTH hold:
#   (1) the variable is set, AND
#   (2) GIT_INDEX_FILE shows the commit is in fact unscoped.
# Condition (2) uses the SAME discriminator as the gate, so the two cannot drift
# into disagreeing about what "unscoped" means.
#
# HONEST BOUND. `--no-verify` bypasses commit-msg exactly as it bypasses
# pre-commit (`man githooks`), so the sanctioned override leaves a trailer while
# a bypass leaves no trace at all. That asymmetry is the point rather than a
# gap: it is why safe-defaults.md bans `--no-verify` outright and why the
# override is given its own variable. C7 is delivered for the sanctioned path;
# no hook-based mechanism can deliver it for a bypass that disables hooks.
#
# Exit: always 0. This hook must NEVER block a commit — it annotates. A failure
# to annotate is not a reason to refuse work.

set -uo pipefail

MSG_FILE="${1:-}"
[ -n "$MSG_FILE" ] || exit 0
[ -f "$MSG_FILE" ] || exit 0

# (1) Did the operator name the override?
[ "${ALLOW_UNSCOPED_COMMIT:-0}" = "1" ] || exit 0

# (1b) SEQUENCER EXEMPTION — must mirror the gate, and originally did not.
# Both the gate and `commit_scope.evaluate` test the sequencer state FIRST and
# answer "scope not required". This hook tested neither, so with the override
# exported a `merge --continue` / `cherry-pick --continue` / `revert --continue`
# commit was stamped as a waiver for a commit where the system's own rule says
# no declaration was required — the precise mislabelling that condition (2)
# exists to prevent, reached by the one branch condition (2) did not replicate.
_gitdir="$(git rev-parse --git-dir 2>/dev/null)"
if [ -n "$_gitdir" ]; then
  for _m in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
    [ -f "$_gitdir/$_m" ] && exit 0
  done
  for _d in rebase-merge rebase-apply; do
    [ -d "$_gitdir/$_d" ] && exit 0
  done
fi

# (2) Is this commit actually unscoped? EXACTLY the gate's rule, which is
# exactly `commit_scope.classify_style`'s regex `^next-index-\d+(\.lock)?$`.
# The first version used the glob `next-index-[0-9]*`, which is looser and let
# `next-index-9x` read as declared — while this file's own header claimed the
# two "cannot drift into disagreeing". They had.
_is_declared() {  # $1 = GIT_INDEX_FILE basename
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
  rest="${rest%.lock}"
  [ -n "$rest" ] || return 1
  case "$rest" in *[!0-9]*) return 1 ;; esac
  return 0
}
_idx="${GIT_INDEX_FILE:-}"
_base="${_idx##*/}"
_is_declared "$_base" && exit 0   # genuinely declared — do NOT stamp

TRAILER="Unscoped-Publish: authorized via ALLOW_UNSCOPED_COMMIT=1"

# Idempotent: a re-run (e.g. `git commit --amend` under the same override, or a
# chained hook invoking us twice) must not stack duplicate trailers.
if grep -qF "Unscoped-Publish:" "$MSG_FILE" 2>/dev/null; then
  exit 0
fi

# Insert the trailer using GIT'S OWN `interpret-trailers`, not a hand-rolled
# parser. This is the third implementation; the first two were both wrong, in
# opposite directions, and the lesson is that commit-message structure is git's
# to know, not this hook's to re-derive:
#
#   v1 (forward awk scan): `{ awk …; printf …; } > tmp && mv tmp msg`. The `&&`
#      tested the LAST command in the group (`printf`), never awk — so a failed
#      awk produced a temp holding ONLY the trailer, and `mv` installed it over
#      the operator's whole message. It also cut at the FIRST `#` line anywhere,
#      so `git commit -m "#42 fix"` put the trailer above the subject.
#
#   v2 (backward awk scan): fixed the multi-line `-m` case and REGRESSED
#      `commit -v`. Under `-v` the trailing lines are raw diff text, not
#      comments, so the backward scan stopped at the diff and appended the
#      trailer BELOW the scissors line — where git truncates it, silently
#      delivering nothing. Measured: trailer landed at line 10, scissors at 4.
#
#   v3 (this one): `git interpret-trailers` already knows about `core.commentChar`,
#      the scissors block and trailer blocks. Measured on the same three inputs:
#      `-v` -> trailer above the comment block (line 3 of 9); `-m "#42 fix"` ->
#      subject preserved, trailer below it; and it is a no-op-safe filter.
#
# It writes to a temp file and only replaces the message when the command
# SUCCEEDED, the output is non-empty, and it still contains the trailer — so a
# failure leaves the operator's message untouched.
# NOTE: this hook deliberately resolves NO comment character of its own.
# It briefly did, and that was a defect waiting to happen: `core.commentChar`
# may legitimately be set to `auto`, in which case `git config --get` returns the
# literal string "auto" and using it as a prefix pattern breaks in BOTH
# directions — a body line beginning "auto…" reads as a comment, while git's
# real comment lines (whatever char it auto-selected) read as body. The guard
# below no longer needs a comment char at all, and `interpret-trailers` resolves
# `auto` correctly by itself, so the safest handling is not to have a copy.

# REFUSE TO STAMP A MESSAGE WITH NO CONTENT AT ALL.
#
# THE BOUNDARY HERE IS NARROWER THAN IT FIRST LOOKS, and the first version got
# it wrong in the expensive direction. It treated "every line starts with the
# comment char" as "no body" — but that depends on git's CLEANUP MODE, which a
# commit-msg hook cannot see:
#
#   * editor path  (cleanup=strip)     — comment lines are stripped, so an
#     all-comment message IS empty and git aborts the commit.
#   * `-m` / `-F`  (cleanup=whitespace) — comment lines are NOT stripped, so
#     `git commit -m "#42 fix"` is a perfectly valid message and DOES commit.
#
# Measured: `-m "#42 fix"` lands with subject `#42 fix`. The old guard classified
# it as bodiless and silently skipped stamping — so C7 was not delivered for any
# message whose lines all begin with `#`. A checker caught it; the test that
# should have caught it asserted only that the subject survived, which passes
# trivially when the hook does nothing.
#
# So the guard now refuses ONLY when the file has no non-whitespace content at
# all — the one case where git aborts under EITHER cleanup mode.
#
# RESIDUAL, stated rather than hidden: an EDITOR session where the operator
# typed nothing but left git's comment template will now be stamped, and will
# commit where git would have aborted. That is accepted deliberately. The
# alternative silently drops the waiver for a legitimate `-m "#…"` commit, and
# of the two failures the stray commit is VISIBLE and revertible while a missing
# waiver is invisible — which is the entire point of C7.
_has_content=0
while IFS= read -r _line || [ -n "$_line" ]; do
  case "$_line" in
    *[![:space:]]*) _has_content=1; break ;;
  esac
done < "$MSG_FILE"
[ "$_has_content" -eq 1 ] || exit 0

# The first line with any content — used below to prove the operator's own text
# survived the rewrite. Checking only "non-empty AND contains the trailer" would
# accept a producer that exited 0 while dropping the body (a user `trailer.*`
# rewrite config, or a successful-but-short write): the result is non-empty and
# does contain the trailer, yet the real message is gone.
_first_content="$(grep -m1 '[^[:space:]]' "$MSG_FILE" 2>/dev/null)"

_tmp="$(mktemp "${TMPDIR:-/tmp}/commit-msg-trailer.XXXXXX")" || exit 0
if git interpret-trailers --trailer "$TRAILER" "$MSG_FILE" > "$_tmp" 2>/dev/null \
   && [ -s "$_tmp" ] \
   && grep -qF "$TRAILER" "$_tmp" 2>/dev/null \
   && { [ -z "$_first_content" ] || grep -qF "$_first_content" "$_tmp" 2>/dev/null; }; then
  command mv -f "$_tmp" "$MSG_FILE" 2>/dev/null || rm -f "$_tmp" 2>/dev/null
else
  # The command failed, produced nothing, or lost the trailer — leave the
  # operator's message exactly as it was. Failing to annotate is never a reason
  # to damage.
  rm -f "$_tmp" 2>/dev/null
fi

exit 0

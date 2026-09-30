#!/usr/bin/env bash
# Tests for the commit gate's SCOPE stage — ENFORCE mode since S6 (A8), built in
# warn mode in S2 (A3) — plus the S6 installation-coverage verb (A7b).
#
# Runs the real gate against throwaway repos under $TMPDIR. Never commits in a
# live repo: a synthetic commit on a live `main` cannot be removed without
# rewriting shared history, which git-policy.md §4/§5 forbids.
#
# The load-bearing assertions are:
#   * an undeclared commit with something staged is REFUSED, and the compliant
#     (declared) form of the SAME commit is ALLOWED — every "blocked" case has a
#     sibling that proves the gate is not simply refusing everything;
#   * `ALLOW_OUT_OF_TREE=1` does NOT buy a scope exemption (case 5) — the
#     reason S2 moved that check below the scope stage;
#   * `-a` is refused (case 3) — the whole-tree sweep the measured discriminator
#     exists to catch;
#   * versus the pinned pre-S2 gate, exit codes differ in EXACTLY the input
#     classes enforcement should change and in no other (case 8a);
#   * the named override commits AND leaves the waiver trailer in history (12);
#   * coverage reports a gated repo missing either hook type (13).
#
# Usage: bash test_commit_gate_scope.sh

set -uo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GATE="$HOOKS_DIR/check-worktree-commit-gate.sh"
TRAILER_HOOK="$HOOKS_DIR/check-unscoped-commit-trailer.sh"
HELPER="$HOOKS_DIR/worktree-helper.sh"
PASS=0; FAIL=0

ok()   { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad()  { FAIL=$((FAIL+1)); printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; }

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/test-gate-scope.XXXXXX")" || exit 1
trap 'rm -rf "$ROOT"' EXIT
# Keep the gate's best-effort inventory out of the live state dir.
export CLAUDE_CONFIG_DIR="$ROOT/cfg"

mkrepo() {  # $1 = name  -> prints path. NOT a worktree, so the gate reaches
            # its out-of-tree path exactly as it does on a primary checkout.
  local d="$ROOT/$1"
  mkdir -p "$d"
  git -C "$d" init -q -b main
  git -C "$d" config user.name  "Gate Test"
  git -C "$d" config user.email "gate@example.invalid"
  git -C "$d" config commit.gpgsign false
  echo base > "$d/base.txt"
  git -C "$d" add base.txt
  git -C "$d" -c core.hooksPath=/dev/null commit -q -m base
  printf '%s' "$d"
}

install_gate() { ln -sf "$GATE" "$1/.git/hooks/pre-commit"; }

# Run a commit and capture (exit code, stderr).
run_commit() {  # $1 = repo, rest = git args ; sets OUT_RC / OUT_ERR
  local d="$1"; shift
  OUT_ERR="$(git -C "$d" "$@" 2>&1 >/dev/null)"
  OUT_RC=$?
}

head_of() { git -C "$1" rev-parse HEAD 2>/dev/null; }

# NOTE: deliberately NOT `printf ... | grep -q`. This script runs under
# `set -o pipefail`, and `grep -q` exits as soon as it matches, so printf takes
# a SIGPIPE and the pipeline returns 141 — i.e. a LONG matching message reports
# "not found" while a short one reports "found". Bash substring matching has no
# pipeline and no such failure mode.
contains() {  # $1 = haystack, $2 = needle
  case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac
}
scope_blocked() { contains "$OUT_ERR" "scope BLOCKED"; }

echo "=== S6/A8 scope-stage tests (enforce mode) ==="

# WHY THE REFUSAL CASES COMMIT BOOKKEEPING FILES. Outside a worktree the gate
# ALSO blocks any domain commit for its original out-of-tree reason, so a
# "refused" assertion over `mine.txt` passes even with the scope refusal deleted
# (found by mutation: removing the `exit 1` left cases 1/3/10/11 green). A
# bookkeeping-only commit is otherwise ALLOWED out of tree, so the scope stage is
# the only thing that can refuse it — which makes the assertion attributable.

# ── 1. bare commit, nothing declared → REFUSED ──────────────────────────────
d=$(mkrepo bare); install_gate "$d"
mkdir -p "$d/Diary"
echo mine > "$d/TODO.md"; echo theirs > "$d/Diary/theirs.md"
git -C "$d" add TODO.md Diary/theirs.md
h0=$(head_of "$d")
run_commit "$d" commit -m "bare"
{ scope_blocked && [ "$OUT_RC" -ne 0 ] && [ "$(head_of "$d")" = "$h0" ]; } \
  && ok "1 bare commit is refused (non-zero, no commit made)" \
  || bad "1 bare commit is refused" "rc=$OUT_RC blocked=$(scope_blocked && echo y || echo n)"
run_commit "$d" commit -m "declared" -- TODO.md
{ [ "$OUT_RC" -eq 0 ] && [ "$(head_of "$d")" != "$h0" ]; } \
  && ok "1a the same commit DECLARED is allowed (so 1 is attributable to scope)" \
  || bad "1a declared twin allowed" "rc=$OUT_RC err=$OUT_ERR"
git -C "$d" diff --cached --name-only | grep -qx Diary/theirs.md \
  && ok "1a' the undeclared staged file is still staged" \
  || bad "1a' undeclared file still staged" "not staged"
git -C "$d" add TODO.md Diary/theirs.md >/dev/null 2>&1
run_commit "$d" commit -m "bare again"
contains "$OUT_ERR" "theirs.md" \
  && ok "1b refusal names the staged paths" \
  || bad "1b refusal names the staged paths" "paths absent from message"
contains "$OUT_ERR" "commit_scope.py publish" \
  && ok "1c refusal names the publish command" \
  || bad "1c refusal names the publish command" "remediation absent"
contains "$OUT_ERR" "ALLOW_UNSCOPED_COMMIT=1" \
  && ok "1d refusal names the override" \
  || bad "1d refusal names the override" "override absent"

# ── 2. pathspec commit → not refused by scope ───────────────────────────────
d=$(mkrepo scoped); install_gate "$d"
echo mine > "$d/mine.txt"; echo theirs > "$d/theirs.txt"
git -C "$d" add mine.txt theirs.txt
run_commit "$d" commit -m "scoped" -- mine.txt
scope_blocked && bad "2 pathspec commit is not scope-refused" "scope refused a declared commit" \
              || ok "2 pathspec commit is not scope-refused"

# ── 3. `git commit -a` → REFUSED (the measured whole-tree sweep) ────────────
d=$(mkrepo dasha); install_gate "$d"
mkdir -p "$d/Diary"
echo mine > "$d/TODO.md"; echo theirs > "$d/Diary/theirs.md"
git -C "$d" add TODO.md Diary/theirs.md
git -C "$d" -c core.hooksPath=/dev/null commit -q -m seed
echo mine2 > "$d/TODO.md"; echo theirs2 > "$d/Diary/theirs.md"
h0=$(head_of "$d")
run_commit "$d" commit -a -m "dash-a"
{ scope_blocked && [ "$OUT_RC" -ne 0 ] && [ "$(head_of "$d")" = "$h0" ]; } \
       && ok "3 -a is refused (index.lock is UNSCOPED)" \
       || bad "3 -a is refused" "a whole-tree sweep passed the scope stage (rc=$OUT_RC)"

# ── 4. named override passes the scope stage ────────────────────────────────
d=$(mkrepo override); install_gate "$d"
echo mine > "$d/mine.txt"; git -C "$d" add mine.txt
OUT_ERR="$(ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -m "named" 2>&1 >/dev/null)"
scope_blocked && bad "4 ALLOW_UNSCOPED_COMMIT=1 passes the scope stage" "still scope-refused" \
              || ok "4 ALLOW_UNSCOPED_COMMIT=1 passes the scope stage"

# ── 5. ALLOW_OUT_OF_TREE does NOT buy a scope exemption ──────────────────────
d=$(mkrepo othervar); install_gate "$d"
echo mine > "$d/mine.txt"; git -C "$d" add mine.txt
OUT_ERR="$(ALLOW_OUT_OF_TREE=1 git -C "$d" commit -m "out of tree" 2>&1 >/dev/null)"
rc=$?
{ scope_blocked && [ "$rc" -ne 0 ]; } \
  && ok "5 ALLOW_OUT_OF_TREE alone is REFUSED for an unscoped commit" \
  || bad "5 ALLOW_OUT_OF_TREE alone is REFUSED" "rc=$rc — the reorder regressed"
# ...and the same variable still works for its OWN purpose on a declared commit.
OUT_ERR="$(ALLOW_OUT_OF_TREE=1 git -C "$d" commit -m "out of tree, declared" -- mine.txt 2>&1 >/dev/null)"
rc=$?
[ "$rc" -eq 0 ] && ok "5a ALLOW_OUT_OF_TREE still allows a DECLARED out-of-tree commit" \
                || bad "5a ALLOW_OUT_OF_TREE still allows a declared commit" "rc=$rc: $OUT_ERR"

# ── 6. sequencer state → exempt (no compliant way to --continue otherwise) ──
d=$(mkrepo seq); install_gate "$d"
echo v0 > "$d/c.txt"; git -C "$d" add c.txt
git -C "$d" -c core.hooksPath=/dev/null commit -q -m c0
git -C "$d" checkout -q -b side
echo side > "$d/c.txt"; git -C "$d" add c.txt
git -C "$d" -c core.hooksPath=/dev/null commit -q -m cs
git -C "$d" checkout -q main
echo trunk > "$d/c.txt"; git -C "$d" add c.txt
git -C "$d" -c core.hooksPath=/dev/null commit -q -m ct
git -C "$d" merge side >/dev/null 2>&1        # conflicts; MERGE_HEAD present
echo resolved > "$d/c.txt"; git -C "$d" add c.txt
[ -f "$d/.git/MERGE_HEAD" ] || bad "6 precondition" "no MERGE_HEAD — the case would be vacuous"
run_commit "$d" commit --no-edit
scope_blocked && bad "6 sequencer state is exempt" "scope-refused during a merge" \
              || ok "6 sequencer state is exempt"

# ── 7. empty staged set → not scope-refused ─────────────────────────────────
d=$(mkrepo empty); install_gate "$d"
run_commit "$d" commit --allow-empty -m "empty"
scope_blocked && bad "7 empty staged set is not scope-refused" "refused with nothing staged" \
              || ok "7 empty staged set is not scope-refused"

# ── 8. out-of-tree domain commit still blocked (its original reason intact) ─
d=$(mkrepo exitpath); install_gate "$d"
echo mine > "$d/mine.txt"; git -C "$d" add mine.txt
run_commit "$d" commit -m "with gate" -- mine.txt
contains "$OUT_ERR" "outside any topic worktree" && [ "$OUT_RC" -ne 0 ] \
  && ok "8 a DECLARED out-of-tree domain commit is still blocked for its original reason" \
  || bad "8 original out-of-tree block intact" "rc=$OUT_RC err=$OUT_ERR"
# ...and its advice must be a form the scope stage ACCEPTS. Before S7 it said
# `ALLOW_OUT_OF_TREE=1 git commit ...` — a bare commit the enforcing gate refuses.
contains "$OUT_ERR" 'ALLOW_OUT_OF_TREE=1 git commit -m "<message>" -- <paths>' \
  && ok "8' the out-of-tree block advises a DECLARED override commit" \
  || bad "8' out-of-tree advice is declared" "err=$OUT_ERR"
# Follow that advice literally and it must succeed.
OUT_ERR="$(ALLOW_OUT_OF_TREE=1 git -C "$d" commit -m "advised" -- mine.txt 2>&1 >/dev/null)"; rc=$?
[ "$rc" -eq 0 ] && ok "8'' following the advice commits" \
               || bad "8'' following the advice commits" "rc=$rc err=$OUT_ERR"

# 8a — EXACT DIFFERENCE versus the pinned pre-S2 gate.
# S2 used this comparison to prove warn mode changed NO exit path. Enforcement
# must change exit codes in EXACTLY the classes an undeclared, otherwise-allowed
# commit falls into — and nowhere else. A gate that refused everything, or
# nothing, fails this.
FIXTURE="$HOOKS_DIR/tests/fixtures/pre-s2-commit-gate.sh"
OLDGATE=""
if [ -f "$FIXTURE" ]; then
  SANDBOX="$ROOT/baseline-hooks"
  mkdir -p "$SANDBOX"
  if cp "$FIXTURE" "$SANDBOX/check-worktree-commit-gate.sh" 2>/dev/null; then
    chmod +x "$SANDBOX/check-worktree-commit-gate.sh"
    for sib in worktree-detect.sh bookkeeping_paths.py bookkeeping-paths.json; do
      [ -e "$HOOKS_DIR/$sib" ] && cp "$HOOKS_DIR/$sib" "$SANDBOX/$sib" 2>/dev/null
    done
    OLDGATE="$SANDBOX/check-worktree-commit-gate.sh"
  else
    bad "8a exit-code difference vs pre-S2 gate" "could not stage the pinned fixture"
  fi
else
  bad "8a exit-code difference vs pre-S2 gate" "pinned fixture missing: $FIXTURE"
fi
if [ -n "$OLDGATE" ] && [ -f "$OLDGATE" ]; then
  probe_exit() {  # $1 gate  $2 scenario  -> echoes exit code
    local g="$1" s="$2" T rc
    T="$(mktemp -d "${TMPDIR:-/tmp}/exitcmp.XXXXXX")"
    git -C "$T" init -q -b main
    git -C "$T" config user.name E; git -C "$T" config user.email e@e.invalid
    git -C "$T" config commit.gpgsign false
    echo b > "$T/base.txt"; git -C "$T" add base.txt
    git -C "$T" -c core.hooksPath=/dev/null commit -q -m base
    ln -sf "$g" "$T/.git/hooks/pre-commit"
    case "$s" in
      bare)      echo m > "$T/m.txt"; git -C "$T" add m.txt
                 git -C "$T" commit -qm x >/dev/null 2>&1; rc=$? ;;
      scoped)    echo m > "$T/m.txt"; git -C "$T" add m.txt
                 git -C "$T" commit -qm x -- m.txt >/dev/null 2>&1; rc=$? ;;
      override)  echo m > "$T/m.txt"; git -C "$T" add m.txt
                 ALLOW_OUT_OF_TREE=1 git -C "$T" commit -qm x >/dev/null 2>&1; rc=$? ;;
      override_scoped) echo m > "$T/m.txt"; git -C "$T" add m.txt
                 ALLOW_OUT_OF_TREE=1 git -C "$T" commit -qm x -- m.txt >/dev/null 2>&1; rc=$? ;;
      unscoped_ovr) echo m > "$T/m.txt"; git -C "$T" add m.txt
                 ALLOW_UNSCOPED_COMMIT=1 git -C "$T" commit -qm x >/dev/null 2>&1; rc=$? ;;
      empty)     git -C "$T" commit -q --allow-empty -m x >/dev/null 2>&1; rc=$? ;;
      dasha)     echo m > "$T/m.txt"; git -C "$T" add m.txt
                 git -C "$T" -c core.hooksPath=/dev/null commit -q -m seed
                 echo m2 > "$T/m.txt"
                 git -C "$T" commit -qam x >/dev/null 2>&1; rc=$? ;;
      bookkeep)  echo t > "$T/TODO.md"; git -C "$T" add TODO.md
                 git -C "$T" commit -qm x >/dev/null 2>&1; rc=$? ;;
      bookkeep_scoped) echo t > "$T/TODO.md"; git -C "$T" add TODO.md
                 git -C "$T" commit -qm x -- TODO.md >/dev/null 2>&1; rc=$? ;;
    esac
    rm -rf "$T"; echo "$rc"
  }
  EXPECTED_CHANGED=" bookkeep override "
  unexpected=""; missing=""
  for s in bare scoped override override_scoped unscoped_ovr empty dasha bookkeep bookkeep_scoped; do
    o="$(probe_exit "$OLDGATE" "$s")"
    n="$(probe_exit "$GATE" "$s")"
    if [ "$o" = "$n" ]; then
      case "$EXPECTED_CHANGED" in *" $s "*) missing="$missing $s(old=$o new=$n)" ;; esac
    else
      case "$EXPECTED_CHANGED" in
        *" $s "*) [ "$n" -ne 0 ] || unexpected="$unexpected $s(new allows)" ;;
        *) unexpected="$unexpected $s(old=$o new=$n)" ;;
      esac
    fi
  done
  if [ -z "$unexpected$missing" ]; then
    ok "8a vs pre-S2 gate: exit codes change in EXACTLY {bookkeep, override}, to a refusal"
  else
    bad "8a vs pre-S2 gate: exact difference" "unexpected:$unexpected missing:$missing"
  fi
fi

# 8b: a bare bookkeeping-only commit is now refused — and the DECLARED form of
# the same commit is allowed (the /close path). The pair is what proves the gate
# is enforcing scope rather than refusing bookkeeping.
d=$(mkrepo bookkeeping); install_gate "$d"
mkdir -p "$d/Diary"
echo "entry" > "$d/TODO.md"
git -C "$d" add TODO.md
run_commit "$d" commit -m "bookkeeping only"
{ scope_blocked && [ "$OUT_RC" -ne 0 ]; } \
  && ok "8b bare bookkeeping-only commit is REFUSED" \
  || bad "8b bare bookkeeping-only commit is REFUSED" "rc=$OUT_RC"
h0=$(head_of "$d")
run_commit "$d" commit -m "bookkeeping only, declared" -- TODO.md
{ [ "$OUT_RC" -eq 0 ] && [ "$(head_of "$d")" != "$h0" ]; } \
  && ok "8b' DECLARED bookkeeping-only commit is ALLOWED (commit landed)" \
  || bad "8b' declared bookkeeping-only commit is ALLOWED" "rc=$OUT_RC err=$OUT_ERR"

# ── 8c. THE SCOPE STAGE RUNS INSIDE A LINKED WORKTREE ───────────────────────
# Index isolation is per-WORKTREE, not per-session, and git-policy.md §3 routes a
# resumed topic back into its existing worktree by design — so "two sessions, one
# index" happens inside worktrees too.
d=$(mkrepo wtparent); install_gate "$d"
WT="$ROOT/wtparent-linked"
git -C "$d" worktree add -q -b topic-scope "$WT" 2>/dev/null
if [ -d "$WT" ]; then
  echo mine > "$WT/mine.txt"; echo theirs > "$WT/theirs.txt"
  git -C "$WT" add mine.txt theirs.txt
  OUT_ERR="$(git -C "$WT" commit -m "bare inside a worktree" 2>&1 >/dev/null)"
  wt_rc=$?
  { scope_blocked && [ "$wt_rc" -ne 0 ]; } \
    && ok "8c scope stage REFUSES a bare commit INSIDE a linked worktree" \
    || bad "8c scope stage runs INSIDE a linked worktree" \
           "rc=$wt_rc — the in-worktree exit short-circuited the stage"
  h0=$(head_of "$WT")
  OUT_ERR="$(git -C "$WT" commit -m "scoped inside a worktree" -- mine.txt 2>&1 >/dev/null)"
  wt_rc=$?
  { [ "$wt_rc" -eq 0 ] && [ "$(head_of "$WT")" != "$h0" ] && ! scope_blocked; } \
    && ok "8c' DECLARED commit inside a worktree is ALLOWED (commit landed)" \
    || bad "8c' declared in-worktree commit is ALLOWED" "rc=$wt_rc err=$OUT_ERR"
  git -C "$WT" diff --cached --name-only | grep -qx theirs.txt \
    && ok "8c'' the other staged file stayed staged and uncommitted" \
    || bad "8c'' the other staged file stayed staged" "theirs.txt not staged"
else
  bad "8c scope stage runs INSIDE a linked worktree" "could not create a worktree"
fi

# ── 9. the discriminator matches commit_scope.classify_style exactly ─────────
if command -v python3 >/dev/null 2>&1; then
  PROBES="next-index-1.lock next-index-12345 next-index-9x next-index-1abc
          next-index-12.lock.bak next-index- next-index-.lock next-indexes-1.lock
          index index.lock sneaky next-index-٠١ next-index-12·SPACE·"
  sed -n '/^_scope_is_declared()/,/^}/p' "$GATE"        > "$ROOT/_gate_disc.sh"
  sed -n '/^_is_declared()/,/^}/p'      "$TRAILER_HOOK" > "$ROOT/_trail_disc.sh"

  call_disc() {  # $1 = extracted-fn file, $2 = fn name, $3 = basename
    bash -c '. "$1"; "$2" "$3" && echo scoped || echo unscoped' _ "$1" "$2" "$3" 2>/dev/null
  }

  dis=""; tdis=""
  for b in $PROBES; do
    real="${b//·SPACE·/ }"
    py="$(python3 "$HOOKS_DIR/commit_scope.py" style "/r/.git/$real" 2>/dev/null)"
    sh="$(call_disc "$ROOT/_gate_disc.sh"  _scope_is_declared "$real")"
    tr="$(call_disc "$ROOT/_trail_disc.sh" _is_declared       "$real")"
    [ "$py" = "$sh" ] || dis="$dis [$b py=$py gate=$sh]"
    [ "$py" = "$tr" ] || tdis="$tdis [$b py=$py trailer=$tr]"
  done
  [ -z "$dis" ] && ok "9 gate discriminator matches classify_style on all probes" \
                || bad "9 gate discriminator matches classify_style" "differs:$dis"
  [ -z "$tdis" ] && ok "9b TRAILER discriminator matches classify_style too" \
                 || bad "9b TRAILER discriminator matches classify_style" "differs:$tdis"
fi

# ── 10. the refusal does not depend on the inventory log ────────────────────
# Logging is best-effort; a failed write must never turn a refusal into an allow.
d=$(mkrepo nolog); install_gate "$d"
echo mine > "$d/TODO.md"; git -C "$d" add TODO.md
: > "$ROOT/not-a-dir"
OUT_ERR="$(CLAUDE_CONFIG_DIR="$ROOT/not-a-dir" git -C "$d" commit -m x 2>&1 >/dev/null)"; rc=$?
{ scope_blocked && [ "$rc" -ne 0 ]; } \
  && ok "10 refusal holds when the inventory log cannot be written" \
  || bad "10 refusal holds without the log" "rc=$rc"

# ── 11. no environment variable selects the mode ────────────────────────────
d=$(mkrepo nomode); install_gate "$d"
echo mine > "$d/TODO.md"; git -C "$d" add TODO.md
OUT_ERR="$(SCOPE_MODE=warn git -C "$d" commit -m x 2>&1 >/dev/null)"; rc=$?
{ scope_blocked && [ "$rc" -ne 0 ]; } \
  && ok "11 SCOPE_MODE=warn in the environment does not disarm the gate" \
  || bad "11 no env mode switch" "rc=$rc — a caller turned enforcement off"

# ── 12. the named override lands the commit AND records the waiver ─────────
# C6 + C7 end to end: git runs BOTH hooks as children of one commit, so the
# variable reaches the commit-msg hook directly. Read back from HISTORY, not from
# the local inventory — a later reader of the log never sees the inventory.
d=$(mkrepo waiver); install_gate "$d"
ln -sf "$TRAILER_HOOK" "$d/.git/hooks/commit-msg"
echo t > "$d/TODO.md"; git -C "$d" add TODO.md
h0=$(head_of "$d")
OUT_ERR="$(ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -m "waived" 2>&1 >/dev/null)"; rc=$?
body="$(git -C "$d" log -1 --format=%B)"
{ [ "$rc" -eq 0 ] && [ "$(head_of "$d")" != "$h0" ] \
  && contains "$body" "Unscoped-Publish: authorized via ALLOW_UNSCOPED_COMMIT=1"; } \
  && ok "12 ALLOW_UNSCOPED_COMMIT=1 commits and the trailer is in history" \
  || bad "12 override commits with trailer" "rc=$rc body=$body err=$OUT_ERR"
echo t2 > "$d/TODO.md"; git -C "$d" add TODO.md
h0=$(head_of "$d")
git -C "$d" commit -qm "declared" -- TODO.md >/dev/null 2>&1; rc=$?
body="$(git -C "$d" log -1 --format=%B)"
{ [ "$rc" -eq 0 ] && [ "$(head_of "$d")" != "$h0" ] && ! contains "$body" "Unscoped-Publish:"; } \
  && ok "12b a declared commit carries NO waiver trailer" \
  || bad "12b declared commit has no trailer" "rc=$rc body=$body"

# ── 13. installation coverage (A7b) ─────────────────────────────────────────
cov_gate_only=$(mkrepo cov-gate-only)
ln -sf "$GATE" "$cov_gate_only/.git/hooks/pre-commit"
COV_OUT="$(WORKTREE_HELPER_GATED_REPOS="$cov_gate_only" bash "$HELPER" coverage 2>&1)"; rc=$?
{ [ "$rc" -eq 1 ] && contains "$COV_OUT" "commit-msg waiver trailer not installed"; } \
  && ok "13 coverage reports a repo with the gate but NO trailer as a gap" \
  || bad "13 coverage flags missing trailer" "rc=$rc out=$COV_OUT"

bash "$HELPER" install --repo "$cov_gate_only" >/dev/null 2>&1
COV_OUT="$(WORKTREE_HELPER_GATED_REPOS="$cov_gate_only" bash "$HELPER" coverage 2>&1)"; rc=$?
{ [ "$rc" -eq 0 ] && contains "$COV_OUT" "COVERED  $cov_gate_only"; } \
  && ok "13b after install, the same repo is COVERED (exit 0)" \
  || bad "13b coverage after install" "rc=$rc out=$COV_OUT"

cov_none=$(mkrepo cov-none)
COV_OUT="$(WORKTREE_HELPER_GATED_REPOS="$cov_none $cov_gate_only" bash "$HELPER" coverage 2>&1)"; rc=$?
{ [ "$rc" -eq 1 ] && contains "$COV_OUT" "pre-commit scope gate not installed" \
  && contains "$COV_OUT" "COVERED  $cov_gate_only"; } \
  && ok "13c one uncovered repo among covered ones still fails, naming only it" \
  || bad "13c mixed coverage" "rc=$rc out=$COV_OUT"

COV_OUT="$(WORKTREE_HELPER_GATED_REPOS="$ROOT/no-such-repo" bash "$HELPER" coverage 2>&1)"; rc=$?
{ [ "$rc" -eq 1 ] && contains "$COV_OUT" "not a git repository"; } \
  && ok "13d a gated path that is not a repo is a gap, not a pass" \
  || bad "13d non-repo gated path" "rc=$rc out=$COV_OUT"

contains "$COV_OUT" "frozen local snapshot" \
  && ok "13e allowlisted targets are printed with their reasons" \
  || bad "13e allowlist printed" "reasons absent"

# 13f — the land_port site is COVERED (it shares the config-source repo's hooks), so it
# must be reported under the known-issue heading and NOT under "not gated".
_allow_block="${COV_OUT#*allowlisted (not gated, with reason):}"
_allow_block="${_allow_block%%covered, with a known issue on record:*}"
_known_block="${COV_OUT#*covered, with a known issue on record:}"
{ ! contains "$_allow_block" "land_port.py" && contains "$_known_block" "land_port.py"; } \
  && ok "13f land_port is reported as covered-with-known-issue, not as allowlisted" \
  || bad "13f land_port disposition" "out=$COV_OUT"

echo
printf '=== %s passed, %s failed ===\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

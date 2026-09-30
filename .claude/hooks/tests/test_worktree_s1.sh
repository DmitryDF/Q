#!/usr/bin/env bash
# test_worktree_s1.sh — V1 walking-skeleton smoke for git-working-model Slice S1.
#
# The S1 acceptance test, end-to-end, in an ISOLATED disposable `git init`
# sandbox under a managed temp dir (never live ~/repos/ or ~/.claude), seeded
# with an initial --allow-empty commit (git worktree add fails on an unborn
# HEAD). Teardown: the whole sandbox is a `mktemp -d` under the OS temp dir that
# nothing external references — the OS /tmp lifecycle reclaims it. NO `rm -rf`,
# NO `git worktree remove --force`, NO `git branch -D` (Safe-Executor rubric).
#
# Covers Verification steps 1–8 of the plan:
#   1 two topics → two worktrees/branches + separated histories
#   2 out-of-tree commit blocked-with-override (real `git commit`)
#   3 resume idempotency
#   4 hooks installed in the shared hooks dir
#   5 interim-bridge no-leak (per-invocation override, not a global export)
#   6 hook-chaining (pre-commit chain + short-circuit + idempotent re-install;
#     pre-push stdin preservation; alien-symlink fail-closed; TTY-safe buffering;
#     non-clobbering .chained)
#   7 slug + entry-state guards (protected / empty slug halt; dirty-tree place)
#   8 script-level fail-closed + sequencer guards
#   + A5 probe: bare-session → gate → place → in-worktree commit allowed (no loop)
#
# Resolves the modules under test from THIS file's location (works in a
# config-experiment clone and in live), so V1 runs against the same tree it ships in.

set -uo pipefail

HOOKS_DIR="$(cd "$(dirname "$(realpath "$0")")/.." && pwd)"
HELPER="$HOOKS_DIR/worktree-helper.sh"
PRIM="$HOOKS_DIR/worktree-detect.sh"
GATE="$HOOKS_DIR/check-worktree-commit-gate.sh"
PUSH="$HOOKS_DIR/check-worktree-push-target.sh"

PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf 'PASS  %s\n' "$1"; }
no()  { FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$1"; }
chk() { if [ "$1" = "$2" ]; then ok "$3"; else no "$3 (got '$1' want '$2')"; fi; }

SBX="$(mktemp -d -t wt-s1.XXXXXX)"
GC() { git -c user.email=t@t -c user.name=t -C "$1" "${@:2}"; }

# Build a fresh primary repo with one commit + return its path.
new_repo() {  # $1 name
  local r="$SBX/$1"
  mkdir -p "$r"
  git -C "$r" init -q -b main
  GC "$r" commit -q --allow-empty -m init
  printf '%s' "$r"
}
hooks_of() {  # $1 repo → shared hooks dir
  local c; c="$(cd "$1" && realpath "$(git rev-parse --git-common-dir)")"
  printf '%s/hooks' "$c"
}
commit_in() {  # $1 dir [$2 env-override] → returns git exit code
  if [ "${2:-}" = "override" ]; then
    ALLOW_OUT_OF_TREE=1 GC "$1" commit -q --allow-empty -m x >/dev/null 2>&1
  else
    GC "$1" commit -q --allow-empty -m x >/dev/null 2>&1
  fi
}

echo "=== sandbox: $SBX ==="

# ---------------------------------------------------------------------------
echo "--- Step 1: two topics → two worktrees/branches + separated histories ---"
R1="$(new_repo src1)"; ROOT="$SBX/repos"
D_A="$("$HELPER" create-or-lookup --repo "$R1" --repos-root "$ROOT" --topic "topic-alpha" 2>/dev/null)"
D_B="$("$HELPER" create-or-lookup --repo "$R1" --repos-root "$ROOT" --topic "topic-beta"  2>/dev/null)"
[ -d "$D_A" ] && [ -d "$D_B" ] && [ "$D_A" != "$D_B" ] && ok "two distinct worktree paths" || no "two distinct worktree paths"
BR_A="$(git -C "$D_A" rev-parse --abbrev-ref HEAD)"; BR_B="$(git -C "$D_B" rev-parse --abbrev-ref HEAD)"
[ "$BR_A" = "topic-alpha" ] && [ "$BR_B" = "topic-beta" ] && ok "two distinct branches" || no "two distinct branches ($BR_A/$BR_B)"
# make one commit in each worktree, assert histories separated
GC "$D_A" commit -q --allow-empty -m only-alpha
GC "$D_B" commit -q --allow-empty -m only-beta
if git -C "$D_A" log --oneline | grep -q only-beta || git -C "$D_B" log --oneline | grep -q only-alpha; then
  no "histories cleanly separated"
else
  ok "histories cleanly separated"
fi

# ---------------------------------------------------------------------------
echo "--- Step 4: hooks installed in shared hooks dir ---"
HD="$(hooks_of "$R1")"
[ -L "$HD/pre-commit" ] && ok "pre-commit symlink present" || no "pre-commit symlink present"
[ -L "$HD/pre-push" ]   && ok "pre-push symlink present"   || no "pre-push symlink present"
[ -x "$HD/pre-commit" ] && ok "pre-commit executable"      || no "pre-commit executable"
PCT="$(realpath "$HD/pre-commit")"
case "$PCT" in *check-worktree-commit-gate.sh) ok "pre-commit → commit gate" ;; *) no "pre-commit → $PCT" ;; esac

# ---------------------------------------------------------------------------
echo "--- Step 2: out-of-tree commit blocked-with-override (real git commit) ---"
commit_in "$D_A"; chk "$?" "0" "in-worktree commit allowed"
commit_in "$R1"; rc=$?; [ "$rc" -ne 0 ] && ok "out-of-tree commit blocked (rc=$rc)" || no "out-of-tree commit NOT blocked"
# the block message names /work-start + the override
BLKMSG="$(GC "$R1" commit --allow-empty -m x 2>&1 >/dev/null)"
echo "$BLKMSG" | grep -q "/work-start" && ok "block msg names /work-start" || no "block msg missing /work-start"
echo "$BLKMSG" | grep -q "ALLOW_OUT_OF_TREE=1" && ok "block msg names override" || no "block msg missing override"
echo "$BLKMSG" | grep -qi "stash pop --index" && ok "block msg has --index bridge" || no "block msg missing --index"
echo "$BLKMSG" | grep -qiE "merge / rebase / cherry-pick" && ok "block msg has no-sequencer warning" || no "block msg missing no-sequencer warning"
echo "$BLKMSG" | grep -qi "gitignore" && ok "block msg has EDGE1 .env warning" || no "block msg missing EDGE1 warning"
commit_in "$R1" override; chk "$?" "0" "out-of-tree WITH override allowed"

# ---------------------------------------------------------------------------
echo "--- Step 3: resume idempotency ---"
D_A2="$("$HELPER" create-or-lookup --repo "$R1" --repos-root "$ROOT" --topic "topic-alpha" 2>/dev/null)"
chk "$D_A2" "$D_A" "resume returns same worktree"
n="$(git -C "$R1" worktree list | grep -c topic-alpha)"
chk "$n" "1" "no duplicate worktree on resume"

# ---------------------------------------------------------------------------
echo "--- Step 5: interim-bridge no-leak (per-invocation override) ---"
commit_in "$R1" override; chk "$?" "0" "named-committer main-side commit succeeds (inline override)"
commit_in "$R1"; rc=$?; [ "$rc" -ne 0 ] && ok "subsequent unrelated out-of-tree still blocked (no leak)" || no "override LEAKED to next commit"

# ---------------------------------------------------------------------------
echo "--- A5 probe: bare-session → gate → place → in-worktree commit allowed (no loop) ---"
# bare out-of-tree commit is blocked (the gate would tell a bare session to run /work-start)
commit_in "$R1"; [ "$?" -ne 0 ] && P1=1 || P1=0
# `place` (what /work-start Step 4b calls) creates the worktree — makes NO commit,
# so the gate never re-fires during placement → no re-entrancy loop.
D_P="$("$HELPER" place --repo "$R1" --repos-root "$ROOT" --topic "probe-topic" 2>/dev/null)"
# the very next commit, now inside the worktree, is ALLOWED → re-entry is clean
commit_in "$D_P"; [ "$?" -eq 0 ] && P2=1 || P2=0
[ "$P1" -eq 1 ] && [ "$P2" -eq 1 ] && ok "A5: clean re-entry, no loop (installer NOT needed)" || no "A5 re-entry probe (P1=$P1 P2=$P2)"

# ---------------------------------------------------------------------------
echo "--- Step 7: slug + entry-state guards ---"
if "$HELPER" create-or-lookup --repo "$R1" --repos-root "$ROOT" --topic "main" >/dev/null 2>/tmp/s7a; then no "protected 'main' halts"; else grep -q protected /tmp/s7a && ok "protected 'main' halts" || no "protected wrong err"; fi
if "$HELPER" create-or-lookup --repo "$R1" --repos-root "$ROOT" --topic "///" >/dev/null 2>/tmp/s7b; then no "empty slug halts"; else grep -q "slugifies to empty" /tmp/s7b && ok "empty slug halts" || no "empty-slug wrong err"; fi
# dirty-tree place: S6 change — a DIRTY tree on `main` now fails CLOSED via EDGE16
# (never relocate the canonical branch; prompt-or-fail-closed for a new topic
# branch — non-TTY = exit 3), NOT the old manual stash-bridge refuse. Full dirty
# migration + edge coverage is in test_legacy_cutover_s6.py.
R2="$(new_repo src2)"
echo dirt > "$R2/dirty.txt"; git -C "$R2" add dirty.txt
"$HELPER" place --repo "$R2" --repos-root "$ROOT" --topic "dtopic" >/dev/null 2>/tmp/s7c; rc=$?
chk "$rc" "3" "dirty-on-main place HALTS (exit 3, EDGE16 fail-closed)"
grep -qi "EDGE16" /tmp/s7c && ok "dirty-on-main place names EDGE16" || no "dirty place missing EDGE16"
grep -qi "protected" /tmp/s7c && ok "dirty-on-main place names the protected-branch remedy" || no "dirty place missing protected-branch remedy"
GC "$R2" commit -q -m cleanup  # make clean
DPC="$("$HELPER" place --repo "$R2" --repos-root "$ROOT" --topic "dtopic" 2>/dev/null)"
[ -d "$DPC" ] && ok "clean-tree place succeeds" || no "clean-tree place failed"

# ---------------------------------------------------------------------------
echo "--- Step 6: hook-chaining ---"
# 6a pre-commit chain: pre-existing user hook is chained behind the gate
R3="$(new_repo src3)"; HD3="$(hooks_of "$R3")"
ULOG="$SBX/userhook.log"; : > "$ULOG"
cat > "$HD3/pre-commit" <<EOF
#!/usr/bin/env bash
echo USERHOOK_RAN >> "$ULOG"
exit 0
EOF
chmod +x "$HD3/pre-commit"
DC="$("$HELPER" create-or-lookup --repo "$R3" --repos-root "$ROOT" --topic "chain-topic" 2>/dev/null)"
grep -q "claude-worktree-gate-wrapper" "$HD3/pre-commit" && ok "6a installed hook is our wrapper (sentinel)" || no "6a wrapper sentinel missing"
[ -f "$HD3/pre-commit.chained" ] && ok "6a user hook moved to .chained" || no "6a .chained missing"
ls "$HD3"/pre-commit.bak.* >/dev/null 2>&1 && ok "6a timestamped .bak archive kept" || no "6a .bak archive missing"
# wrapper runs as CHILD (not exec) + has the cleanup trap + TTY guard
grep -qE '^[[:space:]]*exec ' "$HD3/pre-commit" && no "6a wrapper uses exec (would leak buffer)" || ok "6a wrapper runs child (no exec)"
grep -q "trap 'rm -f" "$HD3/pre-commit" && ok "6a wrapper has buffer-cleanup trap" || no "6a wrapper missing cleanup trap"
grep -q '! -t 0' "$HD3/pre-commit" && ok "6a wrapper has TTY-safe stdin guard" || no "6a wrapper missing TTY guard"
# in-worktree commit → gate passes → chained user hook RUNS
before="$(wc -l < "$ULOG")"
GC "$DC" commit -q --allow-empty -m in-wt </dev/null
after="$(wc -l < "$ULOG")"
[ "$after" -gt "$before" ] && ok "6a in-worktree: gate passes → chained user hook runs" || no "6a chained hook did not run in-worktree"
# out-of-worktree commit → gate blocks → SHORT-CIRCUIT (chained does NOT run)
before="$(wc -l < "$ULOG")"
GC "$R3" commit -q --allow-empty -m out </dev/null 2>/dev/null
after="$(wc -l < "$ULOG")"
chk "$after" "$before" "6a out-of-worktree: gate blocks → short-circuit (chained NOT run)"
# re-install idempotent: still a wrapper, exactly one .chained (no wrapper-of-wrapper)
"$HELPER" install --repo "$R3" >/dev/null 2>&1
grep -q "claude-worktree-gate-wrapper" "$HD3/pre-commit" && ok "6a re-install idempotent (still our wrapper)" || no "6a re-install broke wrapper"
[ ! -e "$HD3/pre-commit.chained.chained" ] && ok "6a no double-chain on re-install" || no "6a double-chained on re-install"

# 6b non-clobbering .chained: a pre-existing .chained is archived, never overwritten
R4="$(new_repo src4)"; HD4="$(hooks_of "$R4")"
printf 'PRE-EXISTING-CHAINED' > "$HD4/pre-commit.chained"
cat > "$HD4/pre-commit" <<EOF
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$HD4/pre-commit"
"$HELPER" install --repo "$R4" >/dev/null 2>&1
if ls "$HD4"/pre-commit.chained.bak.* >/dev/null 2>&1; then
  cont="$(cat "$HD4"/pre-commit.chained.bak.*)"
  chk "$cont" "PRE-EXISTING-CHAINED" "6b pre-existing .chained archived (non-clobber)"
else
  no "6b pre-existing .chained NOT archived"
fi

# 6c alien symlink → fail-closed, never clobbered
R5="$(new_repo src5)"; HD5="$(hooks_of "$R5")"
ALIEN="$SBX/alien-hook"; printf '#!/usr/bin/env bash\nexit 0\n' > "$ALIEN"; chmod +x "$ALIEN"
ln -s "$ALIEN" "$HD5/pre-commit"    # a real target that is NOT our gate (e.g. Husky)
"$HELPER" install --repo "$R5" >/dev/null 2>/tmp/s6c; rc=$?
[ "$rc" -ne 0 ] && ok "6c alien symlink install fails-closed" || no "6c alien install did not fail"
grep -qi "REFUSING to clobber" /tmp/s6c && ok "6c alien alert printed" || no "6c alien alert missing"
[ "$(realpath "$HD5/pre-commit")" = "$(realpath "$ALIEN")" ] && ok "6c alien symlink untouched" || no "6c alien symlink was clobbered"

# 6c2 SIBLING symlink (our own identity guard at pre-push, as on ~/repos/Projects)
#     → chained behind ours, install succeeds, both hooks fire. Before this the
#     helper classified its own sibling as alien and `place` printed no path.
R5b="$(new_repo src5b)"; HD5b="$(hooks_of "$R5b")"
"$HELPER" install-canonical-guard --repo "$R5b" >/dev/null 2>&1
[ -L "$HD5b/pre-push" ] && ok "6c2 identity guard symlinked at pre-push (fixture)" || no "6c2 fixture: guard not installed"
"$HELPER" install --repo "$R5b" >/dev/null 2>/tmp/s6c2; rc=$?
[ "$rc" -eq 0 ] && ok "6c2 install over our own sibling symlink succeeds" || no "6c2 install failed (rc=$rc): $(cat /tmp/s6c2)"
grep -q "claude-worktree-gate-wrapper" "$HD5b/pre-push" && ok "6c2 pre-push is now our wrapper" || no "6c2 pre-push wrapper missing"
[ -L "$HD5b/pre-push.chained" ] \
  && [ "$(realpath "$HD5b/pre-push.chained")" = "$(realpath "$HOOKS_DIR/check-canonical-repo-identity.sh")" ] \
  && ok "6c2 identity guard chained behind ours (both fire)" || no "6c2 identity guard not chained"
# Re-running the guard installer is idempotent, not a refusal: our target is
# already served through the chain.
"$HELPER" install-canonical-guard --repo "$R5b" >/dev/null 2>/tmp/s6c2b; rc=$?
[ "$rc" -eq 0 ] && ok "6c2 guard re-install is a no-op over the chain" || no "6c2 guard re-install failed (rc=$rc): $(cat /tmp/s6c2b)"
grep -q "claude-worktree-gate-wrapper" "$HD5b/pre-push" && ok "6c2 wrapper survived the guard re-install" || no "6c2 wrapper was clobbered"

# 6d pre-push chain: wrapper feeds buffered ref-update stdin to the chained hook
R6="$(new_repo src6)"; HD6="$(hooks_of "$R6")"
PLOG="$SBX/push_stdin.log"; : > "$PLOG"
cat > "$HD6/pre-push" <<EOF
#!/usr/bin/env bash
cat >> "$PLOG"    # echo the ref-update stdin the wrapper forwards
exit 0
EOF
chmod +x "$HD6/pre-push"
"$HELPER" install --repo "$R6" >/dev/null 2>&1
grep -q "claude-worktree-gate-wrapper" "$HD6/pre-push" && ok "6d pre-push is our wrapper" || no "6d pre-push wrapper missing"
printf 'refs/heads/topic aaa refs/heads/topic bbb\n' | "$HD6/pre-push" origin http://example >/dev/null 2>&1
grep -q "refs/heads/topic aaa" "$PLOG" && ok "6d pre-push chain receives buffered stdin intact" || no "6d pre-push stdin not forwarded"

# ---------------------------------------------------------------------------
echo "--- Step 8: script-level fail-closed + rollback guards ---"
# behavioral: failed mkdir -p of the worktree parent aborts before git worktree add
RO="$SBX/ro"; mkdir -p "$RO"; chmod 000 "$RO"
"$HELPER" create-or-lookup --repo "$R1" --repos-root "$RO/repos" --topic "should-fail" >/dev/null 2>/tmp/s8; rc=$?
chmod 755 "$RO"  # restore so OS cleanup can reclaim
[ "$rc" -ne 0 ] && ok "8 failed mkdir parent aborts (no raw git crash)" || no "8 mkdir-fail did not abort"
grep -qi "parent" /tmp/s8 && ok "8 mkdir-fail names the parent-dir error" || no "8 mkdir-fail wrong error"
# structural: fail-closed mktemp + transactional rollback guards exist in the helper
grep -q 'mktemp "${dest}.tmp.XXXXXX")" || return 1' "$HELPER" && ok "8 generate_wrapper mktemp fail-closed" || no "8 wrapper mktemp not fail-closed"
grep -q "run_rollback" "$HELPER" && ok "8 transactional rollback wired" || no "8 rollback missing"
grep -q "exit 1" "$GATE" && ok "8 gate exits non-zero on block" || no "8 gate missing non-zero exit"

echo ""
echo "=== RESULTS: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && { echo "V1 SMOKE: GREEN"; exit 0; } || { echo "V1 SMOKE: RED"; exit 1; }

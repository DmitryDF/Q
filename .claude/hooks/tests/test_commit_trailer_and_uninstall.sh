#!/usr/bin/env bash
# S2/A3 — tests for the override trailer (claim C7) and the `uninstall` verb.
#
# The uninstall tests are not optional polish: without that verb, reverting S2
# deletes the trailer script while `.git/hooks/commit-msg` still points at it,
# and git then aborts EVERY subsequent commit with ENOENT. These assert that
# the rollback path actually works before anything is installed for real.
#
# Throwaway repos under $TMPDIR only.

set -uo pipefail

HOOKS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HELPER="$HOOKS_DIR/worktree-helper.sh"
TRAILER="$HOOKS_DIR/check-unscoped-commit-trailer.sh"
PASS=0; FAIL=0
ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); printf '  FAIL  %s\n     %s\n' "$1" "${2:-}"; }
contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/test-trailer.XXXXXX")" || exit 1
trap 'rm -rf "$ROOT"' EXIT

mkrepo() {
  local d="$ROOT/$1"; mkdir -p "$d"
  git -C "$d" init -q -b main
  git -C "$d" config user.name T; git -C "$d" config user.email t@e.invalid
  git -C "$d" config commit.gpgsign false
  echo base > "$d/base.txt"; git -C "$d" add base.txt
  git -C "$d" -c core.hooksPath=/dev/null commit -q -m base
  printf '%s' "$d"
}
msg_of() { git -C "$1" log -1 --format=%B; }

echo "=== trailer hook (C7) ==="

# 1. override named + commit genuinely unscoped -> trailer stamped
d=$(mkrepo stamp); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q -m "unscoped on purpose" 2>/dev/null
contains "$(msg_of "$d")" "Unscoped-Publish:" \
  && ok "1 trailer stamped on a named-override unscoped commit" \
  || bad "1 trailer stamped" "no trailer in: $(msg_of "$d")"

# 2. no override -> no trailer
d=$(mkrepo nostamp); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
git -C "$d" commit -q -m "ordinary" 2>/dev/null
contains "$(msg_of "$d")" "Unscoped-Publish:" \
  && bad "2 no trailer without the override" "stamped anyway" \
  || ok "2 no trailer without the override"

# 3. THE MISLABEL GUARD: override exported but the commit IS scoped -> no trailer.
#    Without this, an ambiently-exported variable would mark properly scoped
#    commits as waived and the trailer would stop meaning anything.
d=$(mkrepo scopedstamp); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; echo b > "$d/b.txt"; git -C "$d" add a.txt b.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q -m "declared" -- a.txt 2>/dev/null
contains "$(msg_of "$d")" "Unscoped-Publish:" \
  && bad "3 scoped commit is NOT mislabelled as waived" "stamped a declared commit" \
  || ok "3 scoped commit is NOT mislabelled as waived"

# 4. trailer is readable by git's own trailer machinery
d=$(mkrepo trailerfmt); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q -m "subject line" 2>/dev/null
got="$(git -C "$d" log -1 --format='%(trailers:key=Unscoped-Publish,valueonly)' | tr -d '\n')"
[ -n "$got" ] && ok "4 git parses it as a real trailer" \
              || bad "4 git parses it as a real trailer" "git returned nothing"

# 5. idempotent under --amend (no stacked duplicates)
d=$(mkrepo idem); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q -m "first" 2>/dev/null
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q --amend -m "first amended" 2>/dev/null
n="$(msg_of "$d" | grep -c "Unscoped-Publish:")"
[ "$n" = "1" ] && ok "5 trailer not duplicated on amend" \
               || bad "5 trailer not duplicated on amend" "found $n copies"

# 6. never blocks a commit
d=$(mkrepo neverblock); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q -m "x" 2>/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "6 trailer hook never blocks" || bad "6 trailer hook never blocks" "rc=$rc"

# 6b. SEQUENCER EXEMPTION — must mirror the gate, and originally did not.
# With the override exported, a `--continue` commit was stamped as a waiver for
# a commit where the system's own rule says no declaration was required.
d=$(mkrepo seqtrailer); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo v0 > "$d/c.txt"; git -C "$d" add c.txt
git -C "$d" -c core.hooksPath=/dev/null commit -q -m c0
git -C "$d" checkout -q -b side
echo side > "$d/c.txt"; git -C "$d" add c.txt
git -C "$d" -c core.hooksPath=/dev/null commit -q -m cs
git -C "$d" checkout -q main
echo trunk > "$d/c.txt"; git -C "$d" add c.txt
git -C "$d" -c core.hooksPath=/dev/null commit -q -m ct
git -C "$d" merge side >/dev/null 2>&1          # conflicts -> MERGE_HEAD
echo resolved > "$d/c.txt"; git -C "$d" add c.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" -c core.editor=true commit --no-edit -q 2>/dev/null
contains "$(msg_of "$d")" "Unscoped-Publish:" \
  && bad "6b sequencer commit is NOT stamped" "stamped a waiver during a merge" \
  || ok "6b sequencer commit is NOT stamped"

# 6c. A FAILING PRODUCER MUST NOT DESTROY THE MESSAGE.
# v1 of this hook used `{ awk …; printf …; } > tmp && mv tmp msg`, where `&&`
# tests printf and never awk — so a failed awk installed a trailer-only file
# over the operator's message. v3 uses `git interpret-trailers`, so the producer
# to sabotage is GIT, not awk: shadowing awk would exercise nothing and the case
# would pass with the whole guard chain deleted.
d=$(mkrepo prodfail)
echo a > "$d/a.txt"; git -C "$d" add a.txt
printf 'precious message\n' > "$d/.git/COMMIT_EDITMSG"
mkdir -p "$d/fakebin"
printf '#!/usr/bin/env bash\nexit 1\n' > "$d/fakebin/git"; chmod +x "$d/fakebin/git"
( cd "$d" && PATH="$d/fakebin:$PATH" ALLOW_UNSCOPED_COMMIT=1 \
    GIT_INDEX_FILE="$d/.git/index" bash "$TRAILER" "$d/.git/COMMIT_EDITMSG" 2>/dev/null )
got="$(cat "$d/.git/COMMIT_EDITMSG")"
[ "$got" = "precious message" ] \
  && ok "6c a failing producer leaves the operator's message intact" \
  || bad "6c a failing producer leaves the message intact" "message is now: $got"

# 6d. The trailer goes ABOVE git's comment block, not below it (where git
# truncates under `commit -v`). Simulated by pre-seeding COMMIT_EDITMSG.
d=$(mkrepo commentblock); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
printf 'real subject\n\n# Please enter the commit message...\n# with comments\n' \
  > "$d/.git/COMMIT_EDITMSG"
( cd "$d" && ALLOW_UNSCOPED_COMMIT=1 bash "$TRAILER" "$d/.git/COMMIT_EDITMSG" )
body_line="$(grep -n "Unscoped-Publish:" "$d/.git/COMMIT_EDITMSG" | cut -d: -f1)"
cmt_line="$(grep -n "^#" "$d/.git/COMMIT_EDITMSG" | head -1 | cut -d: -f1)"
if [ -n "$body_line" ] && [ -n "$cmt_line" ] && [ "$body_line" -lt "$cmt_line" ]; then
  ok "6d trailer is inserted above git's comment block"
else
  bad "6d trailer is inserted above git's comment block" \
      "trailer at line ${body_line:-none}, first comment at ${cmt_line:-none}"
fi

# 6e. `commit -v`: the trailer must land ABOVE the scissors, where git keeps it.
# v2 of this hook appended below the scissors, so git truncated it and C7
# delivered nothing. Case 6d could not see that — it seeds a message whose
# comment block is trailing, which BOTH algorithms place correctly.
d=$(mkrepo verbose); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
{
  printf 'real subject\n\n'
  printf '# Please enter the commit message for your changes.\n'
  printf '# ------------------------ >8 ------------------------\n'
  printf '# Do not modify or remove the line above.\n'
  printf 'diff --git a/x b/x\n+added line\n'
} > "$d/.git/COMMIT_EDITMSG"
( cd "$d" && ALLOW_UNSCOPED_COMMIT=1 bash "$TRAILER" "$d/.git/COMMIT_EDITMSG" )
t_line="$(grep -n "Unscoped-Publish:" "$d/.git/COMMIT_EDITMSG" | cut -d: -f1)"
s_line="$(grep -n '>8' "$d/.git/COMMIT_EDITMSG" | head -1 | cut -d: -f1)"
if [ -n "$t_line" ] && [ -n "$s_line" ] && [ "$t_line" -lt "$s_line" ]; then
  ok "6e commit -v: trailer is above the scissors (git keeps it)"
else
  bad "6e commit -v: trailer is above the scissors" \
      "trailer at ${t_line:-none}, scissors at ${s_line:-none} — git would truncate it"
fi

# 6f. An EMPTY message must stay empty. Stamping it would turn a commit git
# refuses ("empty commit message") into one that lands carrying only the trailer.
d=$(mkrepo emptymsg)
: > "$d/.git/COMMIT_EDITMSG"
( cd "$d" && ALLOW_UNSCOPED_COMMIT=1 bash "$TRAILER" "$d/.git/COMMIT_EDITMSG" )
[ ! -s "$d/.git/COMMIT_EDITMSG" ] \
  && ok "6f an empty message is left empty (no commit is conjured)" \
  || bad "6f an empty message is left empty" "now: $(cat "$d/.git/COMMIT_EDITMSG")"

# 6g. An ALL-COMMENT message IS stamped — a deliberate, documented tradeoff.
#
# This case reversed, and the reversal is the point. A commit-msg hook cannot
# see git's cleanup mode: under `-m` (cleanup=whitespace) comment lines are NOT
# stripped, so `#42 fix` is a real message that commits; under the editor path
# (cleanup=strip) the same bytes mean the operator wrote nothing and git aborts.
# The same file means opposite things and the hook cannot tell which.
#
# An earlier version resolved that by refusing to stamp anything all-comment —
# which silently dropped C7 for every legitimate `-m "#…"` commit (measured).
# The choice now runs the other way: stamp, and accept that an editor session
# where nothing was typed will commit where git would have aborted. Of the two
# failures the stray commit is VISIBLE and revertible, while a missing waiver is
# invisible — and an invisible missing waiver is exactly what C7 exists to
# prevent. Only a message with NO non-whitespace content at all is refused (6f).
d=$(mkrepo allcomments)
printf '# only comments\n# second line\n' > "$d/.git/COMMIT_EDITMSG"
( cd "$d" && ALLOW_UNSCOPED_COMMIT=1 bash "$TRAILER" "$d/.git/COMMIT_EDITMSG" )
got="$(cat "$d/.git/COMMIT_EDITMSG")"
if contains "$got" "Unscoped-Publish:" && contains "$got" "# only comments"; then
  ok "6g an all-comment message IS stamped, preserving its lines (documented tradeoff)"
else
  bad "6g an all-comment message is stamped, preserving its lines" "result: $got"
fi

# 6h. A single-line message starting with the comment char keeps its subject.
d=$(mkrepo hashsubject); ln -sf "$TRAILER" "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_UNSCOPED_COMMIT=1 git -C "$d" commit -q -m "#42 fix" 2>/dev/null
first="$(msg_of "$d" | head -1)"
[ "$first" = "#42 fix" ] \
  && ok "6h -m '#42 fix' keeps its subject (trailer does not displace it)" \
  || bad "6h -m '#42 fix' keeps its subject" "subject is now: $first"
# 6h' — THE HALF THAT WAS MISSING, and its absence hid a real defect.
# 6h asserted only that the subject survived, which passes trivially when the
# hook does nothing at all — so it stayed green while a `_has_body` guard was
# silently suppressing the trailer for every all-`#` message. Assert delivery.
contains "$(msg_of "$d")" "Unscoped-Publish:" \
  && ok "6h' -m '#42 fix' IS stamped (git accepts it, so C7 must deliver)" \
  || bad "6h' -m '#42 fix' is stamped" "no trailer — C7 silently not delivered"

echo
echo "=== uninstall verb (S2 rollback) ==="

# 7. install then uninstall a symlinked hook
d=$(mkrepo uninst)
bash "$HELPER" install --repo "$d" >/dev/null 2>&1
[ -e "$d/.git/hooks/commit-msg" ] && ok "7 install placed commit-msg" \
                                  || bad "7 install placed commit-msg" "absent"
bash "$HELPER" uninstall --repo "$d" --hook commit-msg >/dev/null 2>&1
[ -e "$d/.git/hooks/commit-msg" ] && bad "7b uninstall removed it" "still present" \
                                  || ok "7b uninstall removed it"

# 8. commits still work after uninstall (the ENOENT-brick scenario)
echo z > "$d/z.txt"; git -C "$d" add z.txt
ALLOW_OUT_OF_TREE=1 git -C "$d" commit -q -m "after uninstall" 2>/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "8 commits still work after uninstall" \
                || bad "8 commits still work after uninstall" "rc=$rc — repo is bricked"

# 9. a user's own hook is chained on install and RESTORED on uninstall
d=$(mkrepo chained)
printf '#!/usr/bin/env bash\nexit 0\n' > "$d/.git/hooks/commit-msg"
chmod +x "$d/.git/hooks/commit-msg"
orig="$(cat "$d/.git/hooks/commit-msg")"
bash "$HELPER" install --repo "$d" >/dev/null 2>&1
[ -e "$d/.git/hooks/commit-msg.chained" ] && ok "9 user hook chained on install" \
                                          || bad "9 user hook chained on install" "no .chained"
bash "$HELPER" uninstall --repo "$d" --hook commit-msg >/dev/null 2>&1
if [ "$(cat "$d/.git/hooks/commit-msg" 2>/dev/null)" = "$orig" ]; then
  ok "9b user hook restored byte-identical on uninstall"
else
  bad "9b user hook restored on uninstall" "content differs or missing"
fi

# 10. REFUSES to remove a hook it did not create
d=$(mkrepo foreign)
printf '#!/usr/bin/env bash\n# someone else\nexit 0\n' > "$d/.git/hooks/commit-msg"
chmod +x "$d/.git/hooks/commit-msg"
before="$(cat "$d/.git/hooks/commit-msg")"
bash "$HELPER" uninstall --repo "$d" --hook commit-msg >/dev/null 2>&1
[ "$(cat "$d/.git/hooks/commit-msg" 2>/dev/null)" = "$before" ] \
  && ok "10 refuses to remove a foreign hook" \
  || bad "10 refuses to remove a foreign hook" "it was removed or altered"

# 11. uninstall on a repo with nothing installed is a no-op, not an error
d=$(mkrepo noop)
bash "$HELPER" uninstall --repo "$d" --hook commit-msg >/dev/null 2>&1; rc=$?
[ "$rc" -eq 0 ] && ok "11 uninstall is a safe no-op when absent" \
                || bad "11 uninstall is a safe no-op when absent" "rc=$rc"

# 12. WHICH SHAPE ACTUALLY BRICKS A REPO — measured, because the plan and an
# earlier version of the helper header both asserted the wrong one.
#   dangling SYMLINK -> git cannot execute it, SKIPS the hook, commits proceed.
#   generated WRAPPER whose TARGET was deleted -> the wrapper RUNS, the target
#     is missing, the hook exits non-zero, and git refuses EVERY commit.
# `<config-source-repo>` has the symlink shape; `~/repos/Projects` has the
# wrapper shape (a pre-existing user hook was chained), so the hazard is real
# and is concentrated in the repo where a user hook already existed.
d=$(mkrepo danglink)
cp "$TRAILER" "$ROOT/doomed-link.sh"; chmod +x "$ROOT/doomed-link.sh"
ln -sf "$ROOT/doomed-link.sh" "$d/.git/hooks/commit-msg"
rm -f "$ROOT/doomed-link.sh"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_OUT_OF_TREE=1 git -C "$d" commit -q -m "after delete" 2>/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "12 a dangling SYMLINK does not brick commits (git skips it)" \
                || bad "12 dangling symlink does not brick commits" "rc=$rc"

d=$(mkrepo dangwrap)
cp "$TRAILER" "$ROOT/doomed-target.sh"; chmod +x "$ROOT/doomed-target.sh"
{
  printf '#!/usr/bin/env bash\n# claude-worktree-gate-wrapper\n'
  printf 'TARGET="%s"\n"$TARGET" "$@"\n' "$ROOT/doomed-target.sh"
} > "$d/.git/hooks/commit-msg"
chmod +x "$d/.git/hooks/commit-msg"
rm -f "$ROOT/doomed-target.sh"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_OUT_OF_TREE=1 git -C "$d" commit -q -m "after delete" 2>/dev/null; rc=$?
[ "$rc" -ne 0 ] && ok "12b a WRAPPER with a deleted target DOES brick commits" \
                || bad "12b wrapper with deleted target bricks commits" "rc=$rc — premise false"

# 12c. That wrapper names a FOREIGN target, so uninstall must REFUSE it and the
# repo must stay as it was. Ownership is not proven by the sentinel alone: the
# canonical-repo identity guard generates wrappers carrying the same sentinel
# for a different target, and removing one of those would take out another
# slice's hook.
bash "$HELPER" uninstall --repo "$d" --hook commit-msg >/dev/null 2>&1
[ -e "$d/.git/hooks/commit-msg" ] \
  && ok "12c uninstall REFUSES a wrapper serving a foreign target" \
  || bad "12c uninstall refuses a wrapper serving a foreign target" \
         "it was removed — another slice's hook would be collateral"

# 12c'. ...and it DOES recover a wrapper that serves OUR target. This is the
# real rollback path: install generates the wrapper with TARGET=<trailer>, the
# revert deletes the trailer script, and the wrapper — a regular file carrying
# the sentinel — is classified WITHOUT realpath, so it is still recognised.
d=$(mkrepo dangwrap_ours)
printf '#!/usr/bin/env bash\nexit 0\n' > "$d/.git/hooks/commit-msg"
chmod +x "$d/.git/hooks/commit-msg"          # a pre-existing user hook -> wrapper
bash "$HELPER" install --repo "$d" >/dev/null 2>&1
grep -qF "claude-worktree-gate-wrapper" "$d/.git/hooks/commit-msg" \
  && ok "12c' install generated a wrapper over the user's commit-msg hook" \
  || bad "12c' install generated a wrapper" "not a wrapper"
# Simulate the post-revert failure. Rebuild the wrapper by hand rather than
# patching the generated one: it must keep BOTH the sentinel and the real
# TARGET= line (that is what makes uninstall accept it as ours), while failing
# the way a wrapper does when its target has been deleted. An earlier version
# patched the generated file with sed, the pattern did not match its actual
# invocation line, and the substitute landed after `exit 0` where it never ran —
# so the test reported "not bricked" and proved nothing.
{
  printf '#!/usr/bin/env bash\n# claude-worktree-gate-wrapper\n'
  printf 'TARGET="%s"\n' "$TRAILER"
  printf 'echo "$TARGET: No such file or directory" >&2\nexit 1\n'
} > "$d/.git/hooks/commit-msg"
chmod +x "$d/.git/hooks/commit-msg"
echo a > "$d/a.txt"; git -C "$d" add a.txt
ALLOW_OUT_OF_TREE=1 git -C "$d" commit -q -m "bricked" 2>/dev/null; rc=$?
[ "$rc" -ne 0 ] && ok "12c'' the repo is bricked while the wrapper fails" \
               || bad "12c'' repo is bricked while the wrapper fails" "rc=$rc"
bash "$HELPER" uninstall --repo "$d" --hook commit-msg >/dev/null 2>&1
echo b > "$d/b.txt"; git -C "$d" add b.txt
ALLOW_OUT_OF_TREE=1 git -C "$d" commit -q -m "recovered" 2>/dev/null; rc=$?
[ "$rc" -eq 0 ] && ok "12c''' uninstall recovers it (commits work again)" \
               || bad "12c''' uninstall recovers it" "rc=$rc — still bricked"

# 12d. classify_hook must still recognise a DANGLING link to its own target,
# so uninstall can tidy one rather than refusing it as foreign.
cp "$TRAILER" "$ROOT/own-target.sh"; chmod +x "$ROOT/own-target.sh"
d2=$(mkrepo dangling2)
ln -sf "$ROOT/own-target.sh" "$d2/.git/hooks/commit-msg"
rm -f "$ROOT/own-target.sh"
sed -n '/^classify_hook()/,/^}/p' "$HELPER" > "$ROOT/_classify.sh"
state="$(bash -c '
  WRAPPER_SENTINEL="claude-worktree-gate-wrapper"
  . "$1"
  classify_hook "$2" "$3"' _ "$ROOT/_classify.sh" \
    "$d2/.git/hooks/commit-msg" "$ROOT/own-target.sh" 2>/dev/null)"
[ "$state" = "ours-symlink" ] \
  && ok "12d classify_hook recognises a DANGLING link to its own target" \
  || bad "12d classify_hook recognises a dangling link to its own target" "got: '$state'"

# 12e. A FOREIGN SIBLING HOOK MUST NOT UNDO THE HOOKS THAT INSTALLED FINE.
# `~/repos/Projects` has a `pre-push` symlinked to the canonical-repo identity
# guard. Install refused there (correctly), returned a FAILURE, and the rollback
# removed the `commit-msg` trailer it had just created — leaving C7
# undeliverable in that repo, with no trace of why. A refusal is the right
# outcome; it is not a transaction failure.
d=$(mkrepo alien_sibling)
ln -sf "$HOOKS_DIR/check-canonical-repo-identity.sh" "$d/.git/hooks/pre-push"
bash "$HELPER" install --repo "$d" >/dev/null 2>&1; rc=$?
# Non-zero is CORRECT here: some hooks were skipped, and the operator must be
# told. What must NOT happen is a rollback of the ones that installed — the
# existing suite's `6c alien install did not fail` asserts the first half, and
# 12e'/12e'' below assert the second. An earlier version of this case asserted
# rc==0 and thereby contradicted 6c.
[ "$rc" -ne 0 ] && ok "12e a foreign sibling makes install report PARTIAL (non-zero)" \
               || bad "12e foreign sibling reports partial" "rc=0 — the skip was not reported"
[ -e "$d/.git/hooks/commit-msg" ] \
  && ok "12e' the commit-msg trailer survives (not rolled back)" \
  || bad "12e' the commit-msg trailer survives" "it was rolled back — C7 undeliverable"
[ -e "$d/.git/hooks/pre-commit" ] \
  && ok "12e'' the pre-commit gate survives too" \
  || bad "12e'' the pre-commit gate survives" "it was rolled back"
# ...and the foreign hook is untouched.
[ "$(readlink "$d/.git/hooks/pre-push")" = "$HOOKS_DIR/check-canonical-repo-identity.sh" ] \
  && ok "12e''' the foreign pre-push is left exactly as it was" \
  || bad "12e''' the foreign pre-push is left alone" "it was modified"

# 13. a bare `uninstall --repo X` (no --hook) is refused.
# classify_hook identifies "ours" by the wrapper sentinel alone, and the
# canonical-repo identity guard generates wrappers with the SAME sentinel — so a
# sweep would silently remove another slice's hook.
d=$(mkrepo bareuninstall)
bash "$HELPER" install --repo "$d" >/dev/null 2>&1
out="$(bash "$HELPER" uninstall --repo "$d" 2>&1)"; rc=$?
if [ "$rc" -ne 0 ] && [ -e "$d/.git/hooks/pre-commit" ]; then
  ok "13 bare uninstall is refused and removes nothing"
else
  bad "13 bare uninstall is refused" "rc=$rc; pre-commit still present: $([ -e "$d/.git/hooks/pre-commit" ] && echo yes || echo NO)"
fi

echo
printf '=== %s passed, %s failed ===\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1

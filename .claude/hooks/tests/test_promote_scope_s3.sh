#!/usr/bin/env bash
# test_promote_scope_s3.sh — A4's validation gate (glittery-humming-pine S3).
#
# The gate as written in the plan: "Two-terminal test: session A edits a rule
# file, B promotes its own; assert A's file absent from the merge, A's live edit
# byte-identical, the deploy verification clean, `config-divergence-check` exit 0".
#
# ISOLATION (plan's Verification section): everything runs in a throwaway tree
# under $TMPDIR with its own config source + destination and its own git repo.
# NOTHING touches the real ~/.claude, the real config source, or any live repo.
# The real `config-promote` script is executed unmodified; `config-source` is reached
# through a PATH shim that redirects -S/-D into the sandbox.
#
# The run is deliberately allowed to FAIL PART-WAY. By the time it does, the
# commit under test has already been made, which is the thing being asserted. The
# script's EXIT trap restores the default branch inside the sandbox; the topic
# branch and its commit remain for inspection.
#
# WHERE it fails, measured rather than assumed — an earlier version of this
# comment claimed the run "fails at `git push` because there is no remote", and a
# checker showed that is wrong. `CLAUDE_CONFIG_DIR` points at the fake
# destination, so config-promote resolves CLASSIFY and LAND_PORT
# (`harness_code_paths.py`, `land_port.py`) inside it, and neither exists there.
# The missing classifier sends it down the conservative "treat as code" branch,
# and it then dies at the missing `land_port.py` guard (config-promote:456-457) —
# BEFORE `git push` is ever attempted. The EXIT-trap restore still fires, because
# it fires on any non-zero exit before PUSH_SUCCESS=1, so every assertion below is
# unaffected; only the stated cause was wrong.
#
# Exit 0 = all cases pass.

set -uo pipefail

PROMOTE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/bin/config-promote"
REAL_CONFIG_SOURCE="$(command -v config-source 2>/dev/null)"
PASS=0; FAIL=0

check() {  # $1 = label, $2 = condition-result (0 ok), $3 = detail on failure
  if [ "$2" -eq 0 ]; then printf '  PASS  %s\n' "$1"; PASS=$((PASS+1))
  else printf '  FAIL  %s\n        %s\n' "$1" "${3:-}"; FAIL=$((FAIL+1)); fi
}

[ -x "$PROMOTE" ] || { echo "missing $PROMOTE"; exit 1; }
[ -n "$REAL_CONFIG_SOURCE" ] || { echo "config-source not installed — cannot run this gate"; exit 1; }

T="$(mktemp -d "${TMPDIR:-/tmp}/promote-scope-s3.XXXXXX")"
# NORMALISE. $TMPDIR ends with a slash on macOS, so mktemp returns a path with a
# doubled separator (".../T//promote-scope-s3.XXXX"). the configured source path
# returns the normalised spelling, so a `${path#"$SRC"/}` prefix-strip against
# the un-normalised value silently fails to strip and yields an absolute path
# where a repo-relative one was wanted. That cost one debugging round here; it is
# a harness bug only — config-promote derives Q_CONFIG_SRC from config-source itself, so
# both sides are normalised there.
T="$(cd "$T" && pwd -P)"
trap 'rm -rf "$T"' EXIT

DEST="$T/dest"                 # fake destination (stands in for $HOME)
SRC="$T/src"                   # config source tree
SHIM="$T/bin"
mkdir -p "$DEST/.claude/rules" "$DEST/.claude/hooks" "$SRC" "$SHIM"

# --- PATH shim: redirect config-source into the sandbox, stub gh ------------------
cat > "$SHIM/config-source" <<EOF
#!/usr/bin/env bash
# Q_CONFIG_FORCE_APPLY=1 makes \`apply\` overwrite without prompting, so a test can
# prove an apply that SHOULD NOT run would really have clobbered a live edit —
# without it, a no-TTY apply errors out and "unchanged" passes vacuously.
if [ "\${Q_CONFIG_FORCE_APPLY:-0}" = "1" ] && [ "\${1:-}" = "apply" ]; then
  shift; exec "$REAL_CONFIG_SOURCE" -S "$SRC" -D "$DEST" apply --force "\$@"
fi
exec "$REAL_CONFIG_SOURCE" -S "$SRC" -D "$DEST" "\$@"
EOF
cat > "$SHIM/gh" <<'EOF'
#!/usr/bin/env bash
echo "https://example.invalid/pr/1"
EOF
chmod +x "$SHIM/config-source" "$SHIM/gh"
export PATH="$SHIM:$PATH"

# --- seed: two managed files, one per "session" -----------------------------
printf 'B original\n' > "$DEST/.claude/hooks/b_tool.sh"
printf 'A original\n' > "$DEST/.claude/rules/a-rule.md"
config-source add "$DEST/.claude/hooks/b_tool.sh" >/dev/null 2>&1
config-source add "$DEST/.claude/rules/a-rule.md" >/dev/null 2>&1

git -C "$SRC" init -q
git -C "$SRC" config user.email t@t
git -C "$SRC" config user.name  t
git -C "$SRC" config commit.gpgsign false
git -C "$SRC" add -A
git -C "$SRC" commit -qm "seed"
BASE_BRANCH="$(git -C "$SRC" rev-parse --abbrev-ref HEAD)"

# UNDER ENFORCEMENT (S6/A8). Install the real scope gate + waiver trailer from
# the config under test into the sandbox source, so every promotion below
# commits THROUGH the enforcing gate — the same position the live config source
# is in. Armed-ness is proven before relying on it: a bare commit here must be
# refused (the PATH shim makes the configured source path return $SRC, so the gate
# classifies this repo as the harness source, exactly as live).
HOOKS_UNDER_TEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks"
bash "$HOOKS_UNDER_TEST/worktree-helper.sh" install --repo "$SRC" >/dev/null 2>&1
[ -e "$SRC/.git/hooks/pre-commit" ] && [ -e "$SRC/.git/hooks/commit-msg" ]
check "the sandbox source carries the scope gate AND the waiver trailer" $?
printf 'probe\n' > "$SRC/probe.txt"; git -C "$SRC" add probe.txt
_h0="$(git -C "$SRC" rev-parse HEAD)"
_probe_err="$(git -C "$SRC" commit -qm probe 2>&1)"
[ "$(git -C "$SRC" rev-parse HEAD)" = "$_h0" ] && grep -q 'scope BLOCKED' <<< "$_probe_err"
check "the gate is ARMED in the sandbox source: a bare commit is refused" $? "$_probe_err"
git -C "$SRC" restore --staged -- probe.txt; rm -f "$SRC/probe.txt"
A_SRC_REL="$(the configured source path "$DEST/.claude/rules/a-rule.md" 2>/dev/null)"
A_SRC_REL="${A_SRC_REL#"$SRC"/}"

# --- the two concurrent sessions --------------------------------------------
# Session B (the promoter) edits its own file.
printf 'B edited by the promoting session\n' > "$DEST/.claude/hooks/b_tool.sh"
# Session A (concurrent, NOT promoting) edits a rule file and leaves it live.
A_CONTENT='A edited by a concurrent session — must survive untouched'
printf '%s\n' "$A_CONTENT" > "$DEST/.claude/rules/a-rule.md"
# Session B also creates a brand-new, UNMANAGED file — the new-file blind spot
# folded into A4: the capture step cannot see it, so the run must `config-source add` it.
printf 'B brand new\n' > "$DEST/.claude/hooks/b_new.sh"

echo "=== case 1: scoped promotion excludes the concurrent session's file ==="
OUT="$(CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" \
       "$PROMOTE" --no-verify -m "S3 scoped promotion" \
         --paths "$DEST/.claude/hooks/b_tool.sh $DEST/.claude/hooks/b_new.sh" 2>&1)"

BRANCH="$(git -C "$SRC" for-each-ref --format='%(refname:short)' refs/heads \
          | grep '^promote/' | head -1)"
if [ -z "$BRANCH" ]; then
  check "a promotion branch with a commit was created" 1 "no promote/* branch; output was:
$OUT"
else
  COMMITTED="$(git -C "$SRC" diff-tree --no-commit-id --name-only -r "$BRANCH" | sort)"

  grep -q 'b_tool' <<< "$COMMITTED" ; check "B's own file IS in the commit" $?
  grep -q 'b_new'  <<< "$COMMITTED" ; check "B's new unmanaged file was config-source-added and committed" $?

  if grep -q 'a-rule' <<< "$COMMITTED"; then
    check "A's file is ABSENT from the commit" 1 "committed set was:
$COMMITTED"
  else
    check "A's file is ABSENT from the commit" 0
  fi

  # A's live edit must be byte-identical — the whole reason the capture stays
  # unscoped is that scoping it would let the deploy step clobber this.
  [ "$(cat "$DEST/.claude/rules/a-rule.md")" = "$A_CONTENT" ]
  check "A's LIVE edit is byte-identical after the promotion" $?

  # A's edit WAS captured into the source working tree (unscoped re-add) and left
  # uncommitted there — that is what keeps config-source verify / divergence-check green
  # while still not mis-attributing it.
  grep -q 'a-rule' <<< "$(git -C "$SRC" status --porcelain)"
  check "A's edit sits UNCOMMITTED in the source (captured, not published)" $?

  # NOTE: use a herestring, never `printf ... | grep -q`. Under `set -o pipefail`
  # a large left-hand side gets a write error when grep -q exits on first match,
  # and the pipeline then reports failure even though the pattern MATCHED. That
  # false-failed three checks in this file's first run.
  grep -q 'NOT being promoted' <<< "$OUT"
  check "the residue report named the undeclared dirty path" $? "output was:
$OUT"
fi

echo "=== case 2: config-source verify is clean after the run ==="
# SANDBOX ARTEFACT, stated rather than worked around silently: this harness lets
# the run fail part-way (at the missing land_port.py guard — see the header), so
# config-promote's EXIT trap checks the default branch back out. That reverts the SOURCE to its seed
# state while LIVE still holds the promoted edits, which would make verify fail for
# a reason that has nothing to do with A4. A real promotion merges the branch and
# runs the deploy step. Restore the promotion branch first, so what is measured is
# the property A4 owns: the source content the promotion produced matches live.
[ -n "${BRANCH:-}" ] && git -C "$SRC" checkout -q "$BRANCH" 2>/dev/null
config-source verify >/dev/null 2>&1
check "config-source verify exit 0 (live == managed state, on the promoted branch)" $?

# The FOURTH assertion A4's validation gate names. It was missing from the first
# version of this file — the gate lists four things and only three were asserted,
# which an independent pre-check caught. `config-divergence-check` enforces the
# staging >= prod invariant by reading the drift check and the configured source path,
# both of which the PATH shim above redirects into the sandbox, so it runs here
# unmodified. Do NOT pass its fetch flag: that would reach the real network and
# the real origin, which this harness must never touch.
DIVERGENCE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/bin/config-divergence-check"
if [ -x "$DIVERGENCE" ]; then
  CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" "$DIVERGENCE" >/dev/null 2>&1
  check "config-divergence-check exit 0 (staging >= prod)" $?
else
  check "config-divergence-check is present and executable" 1 "not found at $DIVERGENCE"
fi

echo "=== case 2b: A's file is absent from THE MERGE, not just from the commit ==="
# A4's validation gate says "assert A's file absent from THE MERGE". The first
# version of this file asserted absence from the pre-push COMMIT and stopped
# there — an independent checker caught the difference, and it is a real one:
# the commit is what the promotion builds, the merge is what actually lands.
#
# The push/PR tail cannot run here (no remote, `gh` is a stub, and the plan
# forbids touching the real origin), but the MERGE itself is plain local git and
# needs no GitHub at all. Merging the scoped branch into the base branch is
# therefore the honest way to assert the gate's actual wording. Done before
# cases 3 and 4 so no later commit can muddy what is being merged.
if [ -n "${BRANCH:-}" ] && [ -n "$A_SRC_REL" ]; then
  git -C "$SRC" checkout -q "$BASE_BRANCH" 2>/dev/null

  # MAKE THE BASE DIVERGE FIRST. Without this the merge is a FAST-FORWARD, which
  # performs no three-way reconciliation and is outcome-equivalent to reading the
  # branch tip — so it would re-establish exactly what case 1 already proved and
  # nothing more. A checker caught precisely that. The realistic scenario A4's
  # wording targets is main advancing independently while the scoped promotion
  # branch is open (`git-policy.md`: bring-main-in defaults to merge), so build
  # that state: an independent commit on the base branch, scoped so it does not
  # sweep A's dirty edit, then a genuine multi-parent merge.
  printf 'C independent base-branch work\n' > "$DEST/.claude/hooks/c_other.sh"
  config-source add "$DEST/.claude/hooks/c_other.sh" >/dev/null 2>&1
  C_SRC_REL="$(the configured source path "$DEST/.claude/hooks/c_other.sh" 2>/dev/null)"
  C_SRC_REL="${C_SRC_REL#"$SRC"/}"
  git -C "$SRC" add -A -- "$C_SRC_REL" >/dev/null 2>&1
  git -C "$SRC" commit -qm "independent base-branch commit" -- "$C_SRC_REL" >/dev/null 2>&1

  if git -C "$SRC" merge --no-edit -q "$BRANCH" >/dev/null 2>&1; then
    check "the scoped branch merges cleanly into $BASE_BRANCH" 0

    # Prove it was a REAL merge, not a fast-forward: a merge commit has 2 parents.
    N_PARENTS="$(git -C "$SRC" rev-list --parents -1 HEAD | wc -w)"
    [ "$N_PARENTS" -eq 3 ]   # own sha + 2 parents
    check "the merge produced a real merge commit (2 parents), not a fast-forward" $? \
          "rev-list --parents -1 HEAD had $N_PARENTS fields, expected 3"
    MERGED_A="$(git -C "$SRC" show "HEAD:$A_SRC_REL" 2>/dev/null)"
    [ "$MERGED_A" = "A original" ]
    check "after the merge, A's file is still the SEED content (A's edit never landed)" $? \
          "merged content was: $MERGED_A"
    grep -q 'scoped promotion' <<< "$(git -C "$SRC" log --oneline "$BASE_BRANCH" -- "$A_SRC_REL")"
    if [ $? -eq 0 ]; then
      check "no scoped-promotion commit touches A's file in merged history" 1 \
            "the promotion commit appears in A's file history"
    else
      check "no scoped-promotion commit touches A's file in merged history" 0
    fi
    # The base branch's own independent work must survive the merge too — a merge
    # that lost it would be a different failure, and one this fixture can see.
    git -C "$SRC" show "HEAD:$C_SRC_REL" >/dev/null 2>&1
    check "the base branch's independent commit survived the merge" $?
  else
    check "the scoped branch merges cleanly into $BASE_BRANCH" 1 "git merge failed"
  fi
  # Stay on the merged base branch — after a real merge that is the honest end
  # state, and it is what cases 3 and 4 should branch from.
else
  check "case 2b prerequisites resolved (branch + A source path)" 1 \
        "BRANCH='${BRANCH:-}' A_SRC_REL='$A_SRC_REL'"
fi

echo "=== case 3: an undeclared run fails CLOSED — publishes nothing, exit 0 ==="
# S6/A8 flipped this from fail-OPEN. Exit 0 is deliberate and asserted: /close §4
# runs config-promote unflagged on every close that touched no harness file, and
# that must not become an error. What must hold is that NOTHING is published:
# no promote/* branch, no new commit anywhere, and the dirty path is NAMED.
# (config-promote names its branch `promote/$(date +%Y%m%d-%H%M%S)`, so two runs
# inside the same second collide — wait it out so later cases measure scoping,
# not branch-name collision.)
sleep 1.1
printf 'B second edit\n' > "$DEST/.claude/hooks/b_tool.sh"
N_BEFORE="$(git -C "$SRC" for-each-ref --format='%(refname:short)' refs/heads | grep -c '^promote/')"
REFS_BEFORE="$(git -C "$SRC" for-each-ref --format='%(objectname)' refs/heads | sort | tr '\n' ' ')"
OUT2="$(CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" \
        "$PROMOTE" --no-verify -m "undeclared" 2>&1)"
RC2=$?
[ "$RC2" -eq 0 ]
check "undeclared run exits 0 (so /close's unflagged call is not an error)" $? "rc=$RC2; output was:
$OUT2"
grep -q 'publishing NOTHING' <<< "$OUT2"
check "undeclared run says it publishes nothing" $? "output was:
$OUT2"
grep -q 'b_tool' <<< "$OUT2"
check "the refusal NAMES the dirty path it left uncommitted" $?
N_AFTER="$(git -C "$SRC" for-each-ref --format='%(refname:short)' refs/heads | grep -c '^promote/')"
REFS_AFTER="$(git -C "$SRC" for-each-ref --format='%(objectname)' refs/heads | sort | tr '\n' ' ')"
[ "$N_AFTER" -eq "$N_BEFORE" ] && [ "$REFS_AFTER" = "$REFS_BEFORE" ]
check "undeclared run created no branch and moved no branch (fail-CLOSED)" $? \
      "promote/* $N_BEFORE -> $N_AFTER; refs changed: $([ "$REFS_AFTER" = "$REFS_BEFORE" ] && echo no || echo yes)"
grep -q 'b_tool' <<< "$(git -C "$SRC" status --porcelain)"
check "the undeclared edit is still dirty in the source (captured, not published)" $?

echo "=== case 3c: undeclared + --no-readd must not run a whole-tree the deploy step ==="
# D7 (post-S8 audit). The fail-closed branch reconciles with the deploy step, safe
# only because the unscoped re-add just made source == live. Under --no-readd the
# source can be BEHIND live, and apply would prompt over or overwrite a concurrent
# session's live edit. Plant exactly that: a live edit the source never captured.
printf 'live-only edit by a concurrent session\n' > "$DEST/.claude/hooks/b_tool.sh"
LIVE_BEFORE="$(cat "$DEST/.claude/hooks/b_tool.sh")"
# Non-vacuity first: with apply forced, an apply WOULD clobber this edit.
( PATH="$SHIM:$PATH" Q_CONFIG_FORCE_APPLY=1 the deploy step --dry-run "$DEST/.claude/hooks/b_tool.sh" >/dev/null 2>&1 )
SRC_B="$(git -C "$SRC" show "HEAD:$(PATH="$SHIM:$PATH" the configured source path "$DEST/.claude/hooks/b_tool.sh" | sed "s#^$SRC/##")" 2>/dev/null)"
[ -n "$SRC_B" ] && [ "$SRC_B" != "$LIVE_BEFORE" ]
check "3c precondition: the source differs from the live edit (an apply would change it)" $?
OUT3C="$(Q_CONFIG_FORCE_APPLY=1 CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" \
         "$PROMOTE" --no-verify --no-readd -m "no scope, no capture" 2>&1)"; RC3C=$?
[ "$RC3C" -eq 0 ]
check "--no-readd with no scope exits 0" $? "rc=$RC3C"
grep -q 'skipping the deploy step' <<< "$OUT3C"
check "--no-readd with no scope says it skips the apply" $? "output was:
$OUT3C"
[ "$(cat "$DEST/.claude/hooks/b_tool.sh")" = "$LIVE_BEFORE" ]
check "the uncaptured live edit is byte-identical afterwards (apply was forced-capable)" $?

# The 'declared paths already in sync' exit is the other early apply site.
sleep 1.1
OUT3D="$(Q_CONFIG_FORCE_APPLY=1 CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" \
         "$PROMOTE" --no-verify --no-readd -m "clean declared path" \
           --paths "$DEST/.claude/hooks/c_other.sh" 2>&1)"; RC3D=$?
[ "$RC3D" -eq 0 ] && grep -q 'skipping the reconcile apply' <<< "$OUT3D" \
  && [ "$(cat "$DEST/.claude/hooks/b_tool.sh")" = "$LIVE_BEFORE" ]
check "--no-readd on an already-clean declared path skips the reconcile apply too" $? "rc=$RC3D output:
$OUT3D"

echo "=== case 3a: ALLOW_UNSCOPED_COMMIT=1 is the explicit way to publish everything ==="
# C6: a deliberate unscoped publish must remain possible when stated. It must
# actually PROCEED (a new commit on a new promote/* branch), not merely print.
sleep 1.1
OUT3A="$(ALLOW_UNSCOPED_COMMIT=1 CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" \
         "$PROMOTE" --no-verify -m "explicitly unscoped" 2>&1)"
grep -q 'ALLOW_UNSCOPED_COMMIT=1' <<< "$OUT3A"
check "the explicit unscoped run warns that it publishes everything" $? "output was:
$OUT3A"
N_3A="$(git -C "$SRC" for-each-ref --format='%(refname:short)' refs/heads | grep -c '^promote/')"
[ "$N_3A" -eq $((N_AFTER + 1)) ]
BR3A="$(git -C "$SRC" for-each-ref --sort=-committerdate --format='%(refname:short)' refs/heads | grep '^promote/' | head -1)"
grep -q 'b_tool' <<< "$(git -C "$SRC" diff-tree --no-commit-id --name-only -r "$BR3A" 2>/dev/null)"
[ $? -eq 0 ] && [ "$N_3A" -eq $((N_AFTER + 1)) ]
check "the explicit unscoped run PROCEEDED to a commit carrying the dirty path" $? \
      "promote/* count $N_AFTER -> $N_3A; newest=$BR3A"
# C7: the waiver is legible in HISTORY, not only in the run's output.
grep -q 'Unscoped-Publish: authorized via ALLOW_UNSCOPED_COMMIT=1' \
  <<< "$(git -C "$SRC" log -1 --format=%B "$BR3A" 2>/dev/null)"
check "the explicit unscoped promotion commit carries the waiver trailer" $?
BR1_BODY="$(git -C "$SRC" log -1 --format=%B "${BRANCH:-HEAD}" 2>/dev/null)"
! grep -q 'Unscoped-Publish:' <<< "$BR1_BODY"
check "the SCOPED promotion commit (case 1) carries no waiver trailer" $?

echo "=== case 3b: a concurrent session's STAGED file survives a scoped publish ==="
# THE CASE THAT DISCRIMINATES THE ACTUAL DEFECT, added after a mutation test
# showed every other case in this file passes against a broken implementation.
#
# Cases 1 and 2b model a concurrent session that only ever WROTE to the live
# filesystem. the capture step captures such an edit into the source WORKING TREE
# but never `git add`s it, so it is never in the index — and a bare `git commit`
# commits the INDEX. So in those fixtures a regression that keeps the scoped add
# and reverts the commit to bare excludes A's file anyway, for a reason that has
# nothing to do with scoping, and every assertion still passes. Verified by
# mutation: with `commit -- "${SRC_PATHS[@]}"` replaced by a bare `commit`, the
# suite reported 17/17.
#
# That regression is not hypothetical — "scoped add, bare commit" is the exact
# shape the plan cites at two other publish surfaces, and the Guiding Policy's
# one-line statement of the whole problem is "a scoped stage does not bound a
# commit". The index is shared, so the fixture has to model a session that
# STAGED. This case does.
printf 'D staged by a concurrent session\n' > "$DEST/.claude/rules/d-rule.md"
config-source add "$DEST/.claude/rules/d-rule.md" >/dev/null 2>&1
D_SRC_REL="$(the configured source path "$DEST/.claude/rules/d-rule.md" 2>/dev/null)"
D_SRC_REL="${D_SRC_REL#"$SRC"/}"
git -C "$SRC" add -- "$D_SRC_REL"          # the concurrent session STAGES its work
printf 'B third edit\n' > "$DEST/.claude/hooks/b_tool.sh"

sleep 1.1                                   # distinct branch timestamp (see case 3)
OUT3B="$(CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" \
         "$PROMOTE" --no-verify -m "S3 scoped promotion with a staged foreigner" \
           --paths "$DEST/.claude/hooks/b_tool.sh" 2>&1)"

BR3B="$(git -C "$SRC" for-each-ref --sort=-committerdate \
        --format='%(refname:short)' refs/heads | grep '^promote/' | head -1)"
if [ -n "$BR3B" ]; then
  COMMITTED3B="$(git -C "$SRC" diff-tree --no-commit-id --name-only -r "$BR3B")"
  if grep -q 'd-rule' <<< "$COMMITTED3B"; then
    check "a STAGED foreign file is absent from the scoped commit" 1 \
          "committed set was:
$COMMITTED3B"
  else
    check "a STAGED foreign file is absent from the scoped commit" 0
  fi
  # The positive half: it must still be STAGED afterwards, i.e. the commit did
  # not consume it. This is what actually fails when the commit goes bare.
  grep -q 'd-rule' <<< "$(git -C "$SRC" diff --cached --name-only)"
  check "the staged foreign file is STILL STAGED after the scoped commit" $? \
        "staged set was: $(git -C "$SRC" diff --cached --name-only | tr '\n' ' ')"
else
  check "case 3b produced a promotion branch" 1 "no promote/* branch; output:
$OUT3B"
fi

echo "=== case 4: ANTI-VACUITY — the scoped assertion can actually fail ==="
# Prove case 1 is not passing for a trivial reason: with NO pathspec on the
# commit, A's file WOULD land. This reproduces the defect in the sandbox.
printf 'A again\n' > "$DEST/.claude/rules/a-rule.md"
printf 'B again\n' > "$DEST/.claude/hooks/b_tool.sh"
the capture step >/dev/null 2>&1
git -C "$SRC" checkout -q -b anti-vacuity
git -C "$SRC" add -A
# The gate is armed in this sandbox (S6), so the demonstration commit states its
# intent through the sanctioned override rather than bypassing the hooks.
ALLOW_UNSCOPED_COMMIT=1 git -C "$SRC" commit -qm "bare commit (the defect)" >/dev/null 2>&1
grep -q 'a-rule' <<< "$(git -C "$SRC" diff-tree --no-commit-id --name-only -r anti-vacuity)"
check "a BARE commit does sweep A's file (so case 1 is a real test)" $?

echo
printf 'RESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

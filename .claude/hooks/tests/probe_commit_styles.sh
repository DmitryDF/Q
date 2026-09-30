#!/usr/bin/env bash
# A1 — commit-form probe matrix for the declared-publish-scope gate.
#
# Measures, for every form of `git commit` the harness can reach: whether the
# pre-commit hook runs at all, what GIT_INDEX_FILE the hook sees, and which
# staged paths `git diff --cached --name-only` reports inside that hook.
#
# WHY THIS EXISTS: the gate in check-worktree-commit-gate.sh discriminates a
# DECLARED publish from an UNDECLARED one by the basename of GIT_INDEX_FILE.
# That discriminator is only safe if every whole-tree form reads as UNSCOPED.
# The dangerous direction is `-a` (and `-i`) presenting as SCOPED, because a
# whole-tree sweep would then read to the gate as a declared publish.
#
# Runs ONLY in a throwaway repo under $TMPDIR. Never touches a real repo.
#
# Usage:  bash ${KIT_HOOKS_DIR}/tests/probe_commit_styles.sh [--keep]
#
# ══════════════════════════════════════════════════════════════════════════
# RESULT MATRIX — MEASURED 2026-09-14, git 2.51.0, macOS 25.5.0 (darwin arm64)
#
# Every row below is the recorded output of a real run of this script, not a
# reading of git's documentation. Re-run to re-derive.
#
# | form              | hook runs | GIT_INDEX_FILE basename | classification | hook sees     |
# |-------------------|-----------|-------------------------|----------------|---------------|
# | bare              | yes       | index                   | UNSCOPED       | mine + theirs |
# | pathspec          | yes       | next-index-<pid>.lock   | SCOPED         | mine          |
# | dash_a  (-a)      | yes       | index.lock              | UNSCOPED       | mine + theirs |
# | only    (-o)      | yes       | next-index-<pid>.lock   | SCOPED         | mine          |
# | include (-i)      | yes       | index.lock              | UNSCOPED       | mine + theirs |
# | amend_bare        | yes       | index                   | UNSCOPED       | mine + theirs |
# | amend_pathspec    | yes       | next-index-<pid>.lock   | SCOPED         | mine          |
# | amend -a          | yes       | index.lock              | UNSCOPED       | mine + theirs |
# | merge_auto        | NO        | (hook never ran)        | INVISIBLE      | —             |
# | merge_conflicted  | yes       | index                   | UNSCOPED       | (sequencer)   |
# | cherry_pick       | yes       | index                   | UNSCOPED       | (sequencer)   |
# | revert            | yes       | index                   | UNSCOPED       | (sequencer)   |
# | rebase            | yes       | index                   | UNSCOPED       | (sequencer)   |
# | *_partial (rv,rb) | yes       | next-index-<pid>.lock   | SCOPED         | named path    |
# | private_index     | yes       | <caller-supplied>       | UNSCOPED*      | per that index|
#
# * `private_index` is the one row where the OBSERVED index name and the
#   classifier's VERDICT are worth distinguishing: the name is caller-chosen
#   and unrecognisable, and an unrecognisable name is classified UNSCOPED.
#   See note 6 for why that reverses the initial decision.
#
# `--continue` forms (probed separately, same session):
# | merge --continue      | yes | index | UNSCOPED | staged set INCLUDING foreign files |
# | cherry-pick --continue| yes | index | UNSCOPED | staged set INCLUDING foreign files |
# | revert --continue     | yes | index | UNSCOPED | staged set INCLUDING foreign files |
# | rebase --continue     | NO  | —     | INVISIBLE| —                                  |
#
# ══════════════════════════════════════════════════════════════════════════
# WHAT A2's classify_style() MUST CONCLUDE — and where this CORRECTS the plan
# ══════════════════════════════════════════════════════════════════════════
#
# 1. *** `-a` HANDS THE HOOK `index.lock`, NOT `index`. ***
#    The hook sees `mine.txt` AND `theirs.txt`, so it is a whole-tree sweep.
#    The same holds for `--amend -a`.
#
#    WHAT THIS MEANS FOR THE PLAN — stated precisely, because an earlier
#    version of this header got the attribution wrong and a checker caught it.
#    The plan's Guiding Policy (plans/glittery-humming-pine.md:181) says only:
#      "GIT_INDEX_FILE's basename discriminates a bare commit (`index`) from a
#       declared one (`next-index-*`)"
#    That names TWO categories and specifies no third branch. `index.lock` is
#    in neither, so the plan is INCOMPLETE here rather than wrong: it does not
#    state a rule that misclassifies `-a`; it states no rule that classifies
#    `-a` at all. An earlier draft of this header asserted the plan said "any
#    basename not exactly `index` is declared" — that phrasing appears nowhere
#    in the plan and was this author's own gloss. Corrected in place rather
#    than deleted, because misquoting a source to make a finding look sharper
#    is the defect class this whole topic is about.
#
#    The gap is still load-bearing: whoever implements the gate must invent
#    the missing branch, and the permissive choice is the catastrophic one.
#
#    => classify_style treats ONLY `next-index-*` as SCOPED. Everything else,
#       including `index`, `index.lock`, an unset value, and any unrecognised
#       name, is UNSCOPED. See note 6 for why the private-index case is in
#       that list too.
#
# 2. `-i` (--include) ALSO hands the hook `index.lock` and also sees the
#    foreign file, so rule (1) catches it with no special case — no argv
#    inspection is needed. (An earlier draft of this header claimed the plan
#    "predicted `-i` would require argv inspection". It does not; the plan's
#    only mention of `-i` is as one of the forms A1 was asked to probe. Same
#    correction as note 1.)
#    `-o`/`--only` is genuinely scoped (`next-index-*`, sees only the named
#    path) and stays SCOPED. So `-o` and `-i` are correctly separated by the
#    index name alone.
#
# 3. `merge_auto` and `rebase --continue` run NO pre-commit hook at all. The
#    gate structurally cannot see them. This is the bound the Guiding Policy
#    names, and the reason A9's build-time checker is load-bearing rather
#    than belt-and-braces.
#
# 4. *** THE SEQUENCER EXEMPTION IS RIGHT, BUT THE PLAN'S REASON FOR IT IS
#    WRONG FOR TWO OF THE FOUR STATES. ***
#    The plan asserts git "forbids a partial commit in those states, so
#    declaring a scope is impossible". Measured:
#      merge       -> `fatal: cannot do a partial commit during a merge.`
#      cherry-pick -> `fatal: cannot do a partial commit during a cherry-pick.`
#      revert      -> SUCCEEDS  (`[main 32ec056] x`) — scope IS declarable
#      rebase      -> SUCCEEDS  (`[detached HEAD 0099930] x`) — likewise
#    and in both succeeding cases the other staged file was correctly left
#    uncommitted, so the declaration genuinely bounded the commit.
#
#    The exemption is still required for all four, but for a DIFFERENT
#    reason: `merge/cherry-pick/revert --continue` each run the hook with the
#    SHARED index and no pathspec, so an enforcing gate would block the
#    continue and leave no compliant way to finish. That is the justification
#    A3 should carry — not "declaration is impossible".
#
# 5. *** THE EXEMPTION IS A REAL HOLE, AND MUST BE RECORDED AS ONE. ***
#    In the probe, `revert --continue` committed a foreign `theirs.txt` that
#    a concurrent session had staged. The sequencer exemption therefore
#    permits exactly the cross-attribution this plan exists to stop. It is
#    accepted because blocking it strands a legitimate operation, but it is a
#    cost, not a safe case, and the surfaces (A4-A7) are what actually cover
#    it — they never publish from a sequencer state.
#
# 6. *** `private_index` READS AS UNSCOPED, and this reverses an earlier
#    decision here. *** A caller-supplied index was initially classified
#    SCOPED, on the reasoning that supplying your own index is a declaration
#    by other means — the mechanism `land_port.py` and `starter-kit` use, both
#    out of scope by construction since their published content is not the
#    live tree. That reasoning does not survive the counter-case: a caller can
#    point GIT_INDEX_FILE anywhere, populate it with the whole tree
#    (`git read-tree HEAD` — this script's own private_index case shows that
#    works), and then `git commit -a` against it. Git sweeps every modified
#    tracked file in; the lock basename is `<custom>.lock`, which matched
#    neither set and fell through to the permissive branch. A legitimate
#    private-index publisher is now covered by a NAMED override and a
#    build-time allowlist entry carrying its reason — a recorded decision,
#    rather than a syntactic guess this rule cannot make correctly.
#    The matrix row below prints UNSCOPED* — the classifier's verdict. (An
#    earlier version of this note said the row prints SCOPED*, describing a
#    table that had already been changed.)
#
# 7. `CLAUDE_CODE_SESSION_ID` reachability — THREE targets were required by
#    A1, and they were NOT all established the same way. Stated per target,
#    because an earlier version of this header reported the single measured
#    case as though it settled all three, and a checker caught it:
#      (a) git spawned from the Bash tool  — MEASURED here, visible in every
#          one of the commit forms below that ran a hook.
#      (b) execute-plan/run.py's subprocess — MEASURED BY THIS SCRIPT. It drives
#          run.py's real `commit-slice` verb against a throwaway repo carrying a
#          recording hook and prints what that hook saw. (Originally this row
#          rested on an out-of-band measurement that shipped nowhere, so the
#          header's own "re-run to re-derive" claim was false for it — a
#          validator caught that. It is folded in now.) The run also shows
#          run.py's own commit arriving as `index`, i.e. UNSCOPED, which is the
#          defect A7 converts.
#      (c) claude-promote                  — NOT executed, and deliberately
#          not: running it would the capture step and commit into the real
#          source tree. Established by source inspection instead — it scrubs
#          no environment (no `env -i`, no `unset`, no `export -n`) and is
#          itself invoked from the Bash tool, so it inherits the environment
#          measured in (a). This is a weaker warrant than (a) and (b) and is
#          labelled as such rather than folded in with them.
#
# ── CLAUDE_CODE_SESSION_ID reachability (measured alongside) ──────────────
# Recorded by the probe hook itself as the `sid_visible` column: whether the
# variable is present in the environment of a git process spawned from the
# Bash tool — target (a) only. See the `SESSION ID REACHABILITY` block in the
# output, and note 7 above for the other two targets.
# ══════════════════════════════════════════════════════════════════════════

set -uo pipefail

KEEP=0
PRINT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --keep)         KEEP=1 ;;
    --print-matrix) PRINT_ONLY=1 ;;
    -h|--help)      sed -n '1,80p' "$0"; exit 0 ;;
    *) echo "unknown arg: $arg" >&2; exit 2 ;;
  esac
done

if [ "$PRINT_ONLY" -eq 1 ]; then
  sed -n '/^# RESULT MATRIX/,/^# ═\{10,\}$/p' "$0"
  exit 0
fi

# ── Scratch repo (never a real repo) ──────────────────────────────────────
ROOT="$(mktemp -d "${TMPDIR:-/tmp}/probe-commit-styles.XXXXXX")" || exit 1
RESULTS="$ROOT/_results.psv"
: > "$RESULTS"

cleanup() {
  if [ "$KEEP" -eq 1 ]; then
    echo "[probe] scratch kept at: $ROOT" >&2
  else
    rm -rf "$ROOT"
  fi
}
trap cleanup EXIT

# The probe hook. Records the form, the index it was handed, and the staged
# set it can see. Written once, symlinked into each scratch repo.
HOOK="$ROOT/probe-pre-commit"
cat > "$HOOK" <<'PROBE_HOOK'
#!/usr/bin/env bash
# Probe pre-commit hook: records what the gate would be able to see.
form="${PROBE_FORM:-unknown}"
idx="${GIT_INDEX_FILE:-<unset>}"
idx_base="$(basename "$idx" 2>/dev/null || echo '<unset>')"
staged="$(git diff --cached --name-only 2>/dev/null | tr '\n' ',' | sed 's/,$//')"
gitdir="$(git rev-parse --git-dir 2>/dev/null)"
seq=""
for m in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD; do
  [ -f "$gitdir/$m" ] && seq="${seq}${m} "
done
for d in rebase-merge rebase-apply; do
  [ -d "$gitdir/$d" ] && seq="${seq}${d} "
done
[ -z "$seq" ] && seq="-"
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then sid="yes"; else sid="no"; fi
printf '%s|ran|%s|%s|%s|%s|%s\n' \
  "$form" "$idx_base" "$staged" "${seq% }" "$sid" "$idx" >> "$PROBE_RESULTS"
exit 0
PROBE_HOOK
chmod +x "$HOOK"

export PROBE_RESULTS="$RESULTS"

# ── Scratch-repo factory ──────────────────────────────────────────────────
# Each form gets its own repo so a failed form cannot contaminate the next.
new_repo() {
  local name="$1" repo="$ROOT/$1"
  mkdir -p "$repo"
  git -C "$repo" init -q -b main
  git -C "$repo" config user.name  "Probe"
  git -C "$repo" config user.email "probe@example.invalid"
  git -C "$repo" config commit.gpgsign false
  ln -sf "$HOOK" "$repo/.git/hooks/pre-commit"
  # Baseline commit, made with the hook disabled so it records nothing.
  echo base > "$repo/base.txt"
  git -C "$repo" add base.txt
  PROBE_FORM=_baseline git -C "$repo" -c core.hooksPath=/dev/null \
    commit -q -m "base" 2>/dev/null
  printf '%s' "$repo"
}

# Stage two files: `mine.txt` (this session's) and `theirs.txt` (a concurrent
# session's). Any form that lets the hook see `theirs` is a form that would
# sweep another session's work.
stage_two() {
  local repo="$1"
  echo mine-v1   > "$repo/mine.txt"
  echo theirs-v1 > "$repo/theirs.txt"
  git -C "$repo" add mine.txt theirs.txt
}

record_norun() {  # form
  printf '%s|NOT-RUN|-|-|-|-|-\n' "$1" >> "$RESULTS"
}

run_form() {  # form, repo, git-args...
  local form="$1" repo="$2"; shift 2
  local before after
  before=$(wc -l < "$RESULTS")
  PROBE_FORM="$form" git -C "$repo" "$@" >/dev/null 2>&1
  after=$(wc -l < "$RESULTS")
  [ "$after" -eq "$before" ] && record_norun "$form"
}

echo "[probe] scratch root: $ROOT" >&2

# ── 1. bare ───────────────────────────────────────────────────────────────
r=$(new_repo bare);            stage_two "$r"
run_form bare "$r" commit -m "bare"

# ── 2. pathspec ───────────────────────────────────────────────────────────
r=$(new_repo pathspec);        stage_two "$r"
run_form pathspec "$r" commit -m "pathspec" -- mine.txt

# ── 3. -a (whole-tree sweep of TRACKED files) ─────────────────────────────
# Both files must be TRACKED for -a to pick them up, so commit them first
# with the hook muted, then dirty both.
r=$(new_repo dash_a);          stage_two "$r"
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "seed" 2>/dev/null
echo mine-v2 > "$r/mine.txt"; echo theirs-v2 > "$r/theirs.txt"
run_form dash_a "$r" commit -a -m "dash-a"

# ── 4. -o / --only ────────────────────────────────────────────────────────
r=$(new_repo only);            stage_two "$r"
run_form only "$r" commit -o mine.txt -m "only"

# ── 5. -i / --include ─────────────────────────────────────────────────────
# `theirs.txt` is STAGED; `mine.txt` is dirty-but-unstaged. -i commits the
# staged set PLUS the named path — the form the basename test alone misreads.
r=$(new_repo include)
echo theirs-v1 > "$r/theirs.txt"; git -C "$r" add theirs.txt
echo mine-v1   > "$r/mine.txt";   git -C "$r" add mine.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "seed" 2>/dev/null
echo theirs-v2 > "$r/theirs.txt"; git -C "$r" add theirs.txt
echo mine-v2   > "$r/mine.txt"
run_form include "$r" commit -i mine.txt -m "include"

# ── 6. --amend, bare ──────────────────────────────────────────────────────
r=$(new_repo amend_bare);      stage_two "$r"
run_form amend_bare "$r" commit --amend -m "amend-bare"

# ── 7. --amend with a pathspec ────────────────────────────────────────────
r=$(new_repo amend_pathspec);  stage_two "$r"
run_form amend_pathspec "$r" commit --amend -m "amend-pathspec" -- mine.txt

# ── 7b. --amend -a (the combined form) ────────────────────────────────────
# The header matrix carried an `amend -a` row before this block existed, i.e.
# it reported a measurement the script never performed. Two independent
# checkers caught it. The form is implemented here so the header's own
# "re-run to re-derive" claim is true of every row it prints.
r=$(new_repo amend_a);         stage_two "$r"
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "seed" 2>/dev/null
echo mine-v2 > "$r/mine.txt"; echo theirs-v2 > "$r/theirs.txt"
run_form amend_a "$r" commit --amend -a -m "amend-a"

# ── 8. merge, auto-resolving (no conflict) ────────────────────────────────
r=$(new_repo merge_auto)
git -C "$r" checkout -q -b side
echo side > "$r/side.txt";  git -C "$r" add side.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "side" 2>/dev/null
git -C "$r" checkout -q main
echo trunk > "$r/trunk.txt"; git -C "$r" add trunk.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "trunk" 2>/dev/null
PROBE_FORM=merge_auto git -C "$r" merge --no-edit side >/dev/null 2>&1
grep -q '^merge_auto|' "$RESULTS" || record_norun merge_auto

# ── 9. merge, conflicted → completed by a bare commit ─────────────────────
r=$(new_repo merge_conflicted)
echo v0 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c0" 2>/dev/null
git -C "$r" checkout -q -b side
echo side > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c-side" 2>/dev/null
git -C "$r" checkout -q main
echo trunk > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c-trunk" 2>/dev/null
git -C "$r" merge side >/dev/null 2>&1        # conflicts, stops pre-commit
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
# Does git allow a PARTIAL commit here? Record the refusal text.
PARTIAL_MERGE="$(PROBE_FORM=merge_partial git -C "$r" commit -m x -- c.txt 2>&1 | head -1)"
run_form merge_conflicted "$r" commit --no-edit

# ── 10. cherry-pick, conflicted ───────────────────────────────────────────
r=$(new_repo cherry_pick)
echo v0 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c0" 2>/dev/null
git -C "$r" checkout -q -b side
echo side > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c-side" 2>/dev/null
SIDE_SHA="$(git -C "$r" rev-parse HEAD)"
git -C "$r" checkout -q main
echo trunk > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c-trunk" 2>/dev/null
git -C "$r" cherry-pick "$SIDE_SHA" >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
PARTIAL_CP="$(PROBE_FORM=cp_partial git -C "$r" commit -m x -- c.txt 2>&1 | head -1)"
run_form cherry_pick "$r" commit --no-edit

# ── 11. revert, conflicted ────────────────────────────────────────────────
r=$(new_repo revert)
echo v0 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c0" 2>/dev/null
echo v1 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c1" 2>/dev/null
TARGET="$(git -C "$r" rev-parse HEAD)"
echo v2 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c2" 2>/dev/null
git -C "$r" revert --no-edit "$TARGET" >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
PARTIAL_RV="$(PROBE_FORM=rv_partial git -C "$r" commit -m x -- c.txt 2>&1 | head -1)"
run_form revert "$r" commit --no-edit

# ── 12. rebase, conflicted ────────────────────────────────────────────────
r=$(new_repo rebase)
echo v0 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c0" 2>/dev/null
git -C "$r" checkout -q -b side
echo side > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c-side" 2>/dev/null
git -C "$r" checkout -q main
echo trunk > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m "c-trunk" 2>/dev/null
git -C "$r" checkout -q side
git -C "$r" rebase main >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
PARTIAL_RB="$(PROBE_FORM=rb_partial git -C "$r" commit -m x -- c.txt 2>&1 | head -1)"
run_form rebase "$r" commit --no-edit

# ── 13. private index ─────────────────────────────────────────────────────
r=$(new_repo private_index)
PRIV="$ROOT/private.index"
GIT_INDEX_FILE="$PRIV" git -C "$r" read-tree HEAD 2>/dev/null
echo mine-v1 > "$r/mine.txt"
GIT_INDEX_FILE="$PRIV" git -C "$r" add mine.txt
echo theirs-v1 > "$r/theirs.txt"
git -C "$r" add theirs.txt          # into the SHARED index only
PROBE_FORM=private_index GIT_INDEX_FILE="$PRIV" \
  git -C "$r" commit -m "private" >/dev/null 2>&1
grep -q '^private_index|' "$RESULTS" || record_norun private_index

# ── 14-17. the four `--continue` forms ────────────────────────────────────
# These matter because `--continue` commits INTERNALLY with no pathspec: if
# the hook runs there and enforces, it strands a legitimate operation.
# Each is seeded with a foreign staged file so the sweep is visible.
seed_conflict() {  # repo — leaves a conflicted c.txt plus a staged theirs.txt
  local d="$1"
  echo v0 > "$d/c.txt"; git -C "$d" add c.txt
  git -C "$d" -c core.hooksPath=/dev/null commit -q -m c0 2>/dev/null
}

r=$(new_repo merge_continue); seed_conflict "$r"
git -C "$r" checkout -q -b side
echo side > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m cs 2>/dev/null
git -C "$r" checkout -q main
echo trunk > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m ct 2>/dev/null
git -C "$r" merge side >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
echo foreign > "$r/theirs.txt"; git -C "$r" add theirs.txt
PROBE_FORM=merge_continue git -C "$r" -c core.editor=true merge --continue >/dev/null 2>&1
grep -q '^merge_continue|' "$RESULTS" || record_norun merge_continue

r=$(new_repo cherrypick_continue); seed_conflict "$r"
git -C "$r" checkout -q -b side
echo side > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m cs 2>/dev/null
S="$(git -C "$r" rev-parse HEAD)"
git -C "$r" checkout -q main
echo trunk > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m ct 2>/dev/null
git -C "$r" cherry-pick "$S" >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
echo foreign > "$r/theirs.txt"; git -C "$r" add theirs.txt
PROBE_FORM=cherrypick_continue git -C "$r" -c core.editor=true cherry-pick --continue >/dev/null 2>&1
grep -q '^cherrypick_continue|' "$RESULTS" || record_norun cherrypick_continue

r=$(new_repo revert_continue); seed_conflict "$r"
echo v1 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m c1 2>/dev/null
T="$(git -C "$r" rev-parse HEAD)"
echo v2 > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m c2 2>/dev/null
git -C "$r" revert --no-edit "$T" >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
echo foreign > "$r/theirs.txt"; git -C "$r" add theirs.txt
PROBE_FORM=revert_continue git -C "$r" -c core.editor=true revert --continue >/dev/null 2>&1
grep -q '^revert_continue|' "$RESULTS" || record_norun revert_continue

r=$(new_repo rebase_continue); seed_conflict "$r"
git -C "$r" checkout -q -b side
echo side > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m cs 2>/dev/null
git -C "$r" checkout -q main
echo trunk > "$r/c.txt"; git -C "$r" add c.txt
git -C "$r" -c core.hooksPath=/dev/null commit -q -m ct 2>/dev/null
git -C "$r" checkout -q side
git -C "$r" rebase main >/dev/null 2>&1
echo resolved > "$r/c.txt"; git -C "$r" add c.txt
echo foreign > "$r/theirs.txt"; git -C "$r" add theirs.txt
PROBE_FORM=rebase_continue git -C "$r" -c core.editor=true rebase --continue >/dev/null 2>&1
grep -q '^rebase_continue|' "$RESULTS" || record_norun rebase_continue

# ══════════════════════════════════════════════════════════════════════════
# Report
# ══════════════════════════════════════════════════════════════════════════
# THE CLASSIFIER — this is the rule A2's classify_style() implements, stated
# once here so the probe reports what the gate would actually decide.
# `index.lock` is UNSCOPED: measured, that is what `-a` and `-i` hand the
# hook, and both are whole-tree sweeps. See header note 1.
classify() {  # index_basename
  # MUST match `commit_scope.classify_style` and the gate's `_scope_is_declared`
  # exactly: SCOPED iff `next-index-<digits>` with an optional single `.lock`.
  # An earlier version used the loose glob `next-index-*` here and printed
  # SCOPED for `next-index-9x`, `next-index-1abc` and `next-index-12.lock.bak`
  # — the very inputs the gate was tightened to reject. The tightening was
  # applied to the gate and the trailer and NOT to this reporter, which is the
  # artifact that records the matrix for the next reader. Fixed in all three.
  local b="$1" rest
  case "$b" in
    '<unset>') echo "UNSCOPED(unset)"; return ;;
    -)         echo "INVISIBLE";       return ;;
    index|index.lock) echo "UNSCOPED"; return ;;
  esac
  case "$b" in
    next-index-*) rest="${b#next-index-}" ;;
    *)            echo "UNSCOPED(private)"; return ;;
  esac
  rest="${rest%.lock}"
  if [ -n "$rest" ]; then
    case "$rest" in
      *[!0-9]*) echo "UNSCOPED(private)" ;;
      *)        echo "SCOPED" ;;
    esac
  else
    echo "UNSCOPED(private)"
  fi
}

echo
echo "=== A1 COMMIT-FORM PROBE MATRIX ==============================="
printf '%-18s %-9s %-22s %-16s %s\n' form hook GIT_INDEX_FILE classification "hook sees"
printf '%-18s %-9s %-22s %-16s %s\n' ------------------ --------- ---------------------- ---------------- ---------
while IFS='|' read -r form ran idx staged seq sid full; do
  [ -z "$form" ] && continue
  if [ "$ran" = "NOT-RUN" ]; then
    printf '%-18s %-9s %-22s %-16s %s\n' "$form" "NO" "(hook never ran)" "INVISIBLE" "—"
  else
    printf '%-18s %-9s %-22s %-16s %s\n' \
      "$form" "yes" "$idx" "$(classify "$idx")" "${staged:-<empty>}"
  fi
done < "$RESULTS"

echo
echo "=== SEQUENCER STATE SEEN BY THE HOOK =========================="
while IFS='|' read -r form ran idx staged seq sid full; do
  [ -z "$form" ] && continue
  [ "$ran" = "NOT-RUN" ] && continue
  printf '%-18s %s\n' "$form" "${seq:--}"
done < "$RESULTS"

echo
echo "=== PARTIAL COMMIT REFUSED MID-SEQUENCER ======================"
printf '%-14s %s\n' "merge:"       "${PARTIAL_MERGE:-<no output>}"
printf '%-14s %s\n' "cherry-pick:" "${PARTIAL_CP:-<no output>}"
printf '%-14s %s\n' "revert:"      "${PARTIAL_RV:-<no output>}"
printf '%-14s %s\n' "rebase:"      "${PARTIAL_RB:-<no output>}"

# ── target (b): does CLAUDE_CODE_SESSION_ID reach a git process spawned by
# execute-plan/run.py's SUBPROCESS layer? Measured here rather than asserted,
# by driving run.py's real `commit-slice` verb against a throwaway repo that
# carries a recording hook. Folded into this script so the header's own
# "re-run to re-derive" claim is true of this row too — it previously rested on
# an out-of-band measurement that shipped nowhere.
RUNPY="$HOME/.claude/skills/execute-plan/run.py"
RUNPY_SID="(not probed — run.py not found)"
if [ -f "$RUNPY" ] && command -v python3 >/dev/null 2>&1; then
  rp="$ROOT/runpy"; mkdir -p "$rp"
  git -C "$rp" init -q -b topic-branch
  git -C "$rp" config user.name P; git -C "$rp" config user.email p@e.invalid
  printf '#!/usr/bin/env bash\nprintf "sid=%%s idx=%%s\\n" "${CLAUDE_CODE_SESSION_ID:-<UNSET>}" "$(basename "${GIT_INDEX_FILE:-<unset>}")" >> "%s"\nexit 0\n' \
    "$ROOT/_runpy_seen.txt" > "$rp/.git/hooks/pre-commit"
  chmod +x "$rp/.git/hooks/pre-commit"
  echo base > "$rp/base.txt"
  git -C "$rp" -c core.hooksPath=/dev/null add base.txt
  git -C "$rp" -c core.hooksPath=/dev/null commit -q -m base
  echo work > "$rp/slice-file.txt"
  # slice_id must satisfy run.py's UnifiedGitContract (a malformed id is a
  # GitContractError and nothing commits, which reads here as "hook never ran").
  printf '{"slice_id":"S9","repo_dir":"%s","commit_summary":"probe run.py subprocess"}' "$rp" \
    | CLAUDE_CODE_SESSION_ID="${CLAUDE_CODE_SESSION_ID:-PROBE-SID-12345}" \
      python3 "$RUNPY" commit-slice >/dev/null 2>&1
  if [ -s "$ROOT/_runpy_seen.txt" ]; then
    RUNPY_SID="$(head -1 "$ROOT/_runpy_seen.txt")"
  else
    RUNPY_SID="(hook never ran — run.py did not commit)"
  fi
fi

echo
echo "=== SESSION ID REACHABILITY ==================================="
if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
  echo "CLAUDE_CODE_SESSION_ID in this shell: SET (${CLAUDE_CODE_SESSION_ID})"
else
  echo "CLAUDE_CODE_SESSION_ID in this shell: UNSET"
fi
echo
echo "target (a) — git spawned from the Bash tool  [MEASURED]:"
while IFS='|' read -r form ran idx staged seq sid full; do
  [ -z "$form" ] && continue
  [ "$ran" = "NOT-RUN" ] && continue
  printf '  %-18s sid_visible=%s\n' "$form" "$sid"
done < "$RESULTS"

echo
echo "target (b) — execute-plan/run.py's subprocess  [MEASURED]:"
printf '  %s\n' "$RUNPY_SID"

echo
echo "target (c) — claude-promote  [SOURCE-INSPECTED, NOT EXECUTED]:"
echo "  Running it would 'the capture step' and commit into the real source tree,"
echo "  so it is deliberately not executed here. Inspection instead:"
if [ -f "$HOME/.claude/bin/claude-promote" ]; then
  if grep -qE 'env -i|^[[:space:]]*unset |export -n' "$HOME/.claude/bin/claude-promote"; then
    echo "  !! it DOES scrub the environment — target (c) cannot be inferred from (a)"
  else
    echo "  no env scrubbing found (no 'env -i', no 'unset', no 'export -n'), and it is"
    echo "  itself invoked from the Bash tool, so it inherits the environment measured"
    echo "  in (a). This is a WEAKER warrant than (a) and (b) and is labelled as such."
  fi
else
  echo "  (claude-promote not found)"
fi

echo
echo "=== RAW ======================================================="
cat "$RESULTS"

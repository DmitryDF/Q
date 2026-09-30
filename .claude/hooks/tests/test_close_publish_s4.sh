#!/usr/bin/env bash
# test_close_publish_s4.sh — A6's validation gate (glittery-humming-pine S4).
#
# The gate as the plan states it:
#   "Two concurrent sessions, B holding a session-owned artifact (a Thoughts/
#    file) dirty and staged; A closes. Assert B's artifact is absent from A's
#    commit and still staged; A's commit matches the emitter's list exactly.
#    Separately, with both sessions having appended to TODO.md, assert A's
#    publish ANNOUNCES the joint edit before committing (C9) and that both
#    sessions' lines survive it (C8) — do not assert B's lines stay uncommitted,
#    which the mechanism cannot deliver for a shared append file."
#
# Everything runs in a throwaway repo under $TMPDIR. Nothing touches a live repo.
#
# Written behaviourally on purpose: the two preceding slices each shipped a suite
# that passed while blind, and in both cases only mutation exposed it. Every
# assertion here drives the real scripts and inspects real git state.

set -uo pipefail

SCOPE_SH="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/session-scope.sh"
CS_PY="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/commit_scope.py"
PASS=0; FAIL=0

check() {
  if [ "$2" -eq 0 ]; then printf '  PASS  %s\n' "$1"; PASS=$((PASS+1))
  else printf '  FAIL  %s\n        %s\n' "$1" "${3:-}"; FAIL=$((FAIL+1)); fi
}

T="$(mktemp -d "${TMPDIR:-/tmp}/close-publish-s4.XXXXXX")"; T="$(cd "$T" && pwd -P)"
trap 'rm -rf "$T"' EXIT

REPO="$T/repo"
mkdir -p "$REPO/Thoughts" "$REPO/Diary" "$REPO/.claude/logs"
git -C "$REPO" init -q
git -C "$REPO" config user.email t@t
git -C "$REPO" config user.name  t
git -C "$REPO" config commit.gpgsign false
printf 'seed\n'                    > "$REPO/TODO.md"
printf 'B seed\n'                  > "$REPO/Thoughts/b-topic_PLAN.md"
printf '# diary\n'                 > "$REPO/Diary/2026-01-01.md"
git -C "$REPO" add -A
git -C "$REPO" commit -qm seed

# UNDER ENFORCEMENT (S6/A8). Install the real scope gate + waiver trailer from
# the config under test, exactly as a gated repo carries them, so /close's
# publish path is proven to COMMIT through the enforcing gate rather than merely
# to compose a scoped command. Armed-ness is proven, not assumed: a bare commit
# of an otherwise-allowed bookkeeping file must be refused here.
HELPER="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/worktree-helper.sh"
bash "$HELPER" install --repo "$REPO" >/dev/null 2>&1
[ -e "$REPO/.git/hooks/pre-commit" ] && [ -e "$REPO/.git/hooks/commit-msg" ]
check "the sandbox carries the scope gate AND the waiver trailer" $?
printf 'probe\n' >> "$REPO/TODO.md"; git -C "$REPO" add TODO.md
_h0="$(git -C "$REPO" rev-parse HEAD)"
_probe_err="$(git -C "$REPO" commit -qm probe 2>&1)"
[ "$(git -C "$REPO" rev-parse HEAD)" = "$_h0" ] && grep -q 'scope BLOCKED' <<< "$_probe_err"
check "the gate is ARMED here: a bare bookkeeping commit is refused" $? "$_probe_err"
git -C "$REPO" restore --staged -- TODO.md
git -C "$REPO" checkout -q -- TODO.md

SID_A=SESSION-A
SID_B=SESSION-B

# --- Session A's own work, recorded in A's ledger ---------------------------
printf 'A plan\n' > "$REPO/Thoughts/a-topic_PLAN.md"
printf 'A diary\n' >> "$REPO/Diary/2026-01-01.md"
{ printf '%s\n' "$REPO/Thoughts/a-topic_PLAN.md"
  printf '%s\n' "$REPO/Diary/2026-01-01.md"; } > "$REPO/.claude/logs/_session_files-$SID_A.log"

# --- Session B: a session-owned artifact, dirty AND STAGED ------------------
# Staged is the point. The index is shared, so a bare commit would take it.
printf 'B edited by a concurrent session\n' >> "$REPO/Thoughts/b-topic_PLAN.md"
git -C "$REPO" add -- Thoughts/b-topic_PLAN.md
printf '%s\n' "$REPO/Thoughts/b-topic_PLAN.md" > "$REPO/.claude/logs/_session_files-$SID_B.log"

# --- Both sessions appended to the shared TODO.md ---------------------------
printf -- '- [ ] A line\n' >> "$REPO/TODO.md"
printf -- '- [ ] B line\n' >> "$REPO/TODO.md"
printf '%s\n' "$REPO/TODO.md" >> "$REPO/.claude/logs/_session_files-$SID_B.log"

echo "=== case 1: A's declared scope ==="
DECL="$(bash "$SCOPE_SH" "$SID_A" "$REPO" --publish-scope 2>&1)"
printf '%s\n' "$DECL" | sed 's/^/    /'

grep -qx 'Thoughts/a-topic_PLAN.md' <<< "$DECL"
check "A's own artifact is declared" $?
grep -qx 'TODO.md' <<< "$DECL"
check "TODO.md reconciled in (merge_union: true)" $?
if grep -qx 'Thoughts/b-topic_PLAN.md' <<< "$DECL"; then
  check "B's session-owned artifact is NOT declared" 1 "it leaked into A's scope"
else
  check "B's session-owned artifact is NOT declared" 0
fi

echo "=== case 2: A publishes; B's staged artifact must survive ==="
# `mapfile` is bash 4+; macOS ships bash 3.2. Read the list portably.
PATHS=()
while IFS= read -r _p; do [ -n "$_p" ] && PATHS+=("$_p"); done <<< "$DECL"

HEAD_BEFORE="$(git -C "$REPO" rev-parse HEAD)"
OUT="$(cd "$REPO" && python3 "$CS_PY" publish -m "Session close: A" --session-id "$SID_A" -- "${PATHS[@]}" 2>&1)"
printf '%s\n' "$OUT" | sed 's/^/    /'

# GUARD BEFORE ASSERTING. The first run of this file reported PASS for both
# "B's artifact is absent" and "B's artifact is still staged" while NO COMMIT HAD
# HAPPENED AT ALL — a portability error killed the publish and the two negative
# assertions sailed through on an empty committed set. A negative that passes
# when the step under test did not run is exactly the vacuity the preceding two
# slices shipped. So: prove a commit was made before drawing any conclusion from
# its contents.
HEAD_AFTER="$(git -C "$REPO" rev-parse HEAD)"
[ "$HEAD_BEFORE" != "$HEAD_AFTER" ]
check "A's publish actually produced a commit (guard for the negatives below)" $? \
      "HEAD unchanged at $HEAD_BEFORE — publish did not commit; output: $OUT"

COMMITTED="$(git -C "$REPO" diff-tree --no-commit-id --name-only -r HEAD | sort)"
echo "  committed:"; printf '%s\n' "$COMMITTED" | sed 's/^/      /'

[ -n "$COMMITTED" ]
check "the commit is non-empty (guard)" $? "nothing was committed"

if grep -q 'b-topic' <<< "$COMMITTED"; then
  check "B's artifact is ABSENT from A's commit" 1 "committed: $COMMITTED"
else
  check "B's artifact is ABSENT from A's commit" 0
fi

grep -q 'b-topic' <<< "$(git -C "$REPO" diff --cached --name-only)"
check "B's artifact is STILL STAGED after A's commit" $? \
      "staged: $(git -C "$REPO" diff --cached --name-only | tr '\n' ' ')"

# "A's commit matches the emitter's list exactly" — set equality both ways.
EXPECTED="$(printf '%s\n' "$DECL" | sort -u)"
if [ "$COMMITTED" = "$EXPECTED" ]; then
  check "A's commit matches the declared list EXACTLY" 0
else
  check "A's commit matches the declared list EXACTLY" 1 \
        "declared=[$(tr '\n' ' ' <<< "$EXPECTED")] committed=[$(tr '\n' ' ' <<< "$COMMITTED")]"
fi

echo "=== case 3: the joint shared-append file (C8 + C9) ==="
grep -qi 'also written by' <<< "$OUT"
check "C9 — the joint edit was ANNOUNCED before committing" $? "output was: $OUT"

BODY="$(git -C "$REPO" show HEAD:TODO.md)"
grep -q 'A line' <<< "$BODY"
check "C8 — A's line survived the commit" $?
grep -q 'B line' <<< "$BODY"
check "C8 — B's line survived it too (shared append file)" $?

echo "=== case 4: ANTI-VACUITY — a bare commit really would sweep B's file ==="
printf 'B more\n' >> "$REPO/Thoughts/b-topic_PLAN.md"
git -C "$REPO" add -- Thoughts/b-topic_PLAN.md
git -C "$REPO" checkout -q -b bare-probe
# The gate is armed in this sandbox (S6), so the demonstration commit states its
# intent through the sanctioned override rather than bypassing the hooks.
ALLOW_UNSCOPED_COMMIT=1 git -C "$REPO" commit -qm "bare (the defect)" >/dev/null 2>&1
grep -q 'b-topic' <<< "$(git -C "$REPO" diff-tree --no-commit-id --name-only -r bare-probe)"
check "a BARE commit DOES take B's staged file (so case 2 is a real test)" $?

echo
printf 'RESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

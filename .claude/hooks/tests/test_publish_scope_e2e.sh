#!/usr/bin/env bash
# test_publish_scope_e2e.sh — S8 / A11 closing verification (glittery-humming-pine).
#
# The composition, not the parts. Every earlier slice proved one surface; this
# replays each of the FOUR landed cross-attributions in a two-session sandbox,
# under the REAL enforcing gate + waiver trailer, and shows for each that the
# path which produced it is now either REFUSED or SCOPED:
#
#   ffeeef23  /close          (Projects)  research-fc-backlog close carried another
#                                          topic's Thoughts files, none of its own
#   213237fa  /close          (Projects)  research-source-adapters close carried
#                                          review-skill-system research files
#   200a247   claude-promote  (config-source)   output-security S2 carried a concurrent
#                                          re-synthesis of rules/prompt-engineering.md
#   4e57836   claude-promote  (config-source)   source-picker promotion carried six
#                                          output-security files
#
# (Repo and file lists measured from the commits themselves, not from prose.)
# Then: /ninja-fix and an execute-plan slice commit under enforcement; the named
# override still works when named and leaves its trailer in history; and the
# static layers are clean over the config under test.
#
# ISOLATION: everything happens under $TMPDIR, with ONE read-only exception — case
# 5.10 runs a `--dry-run` publish against the live ~/.claude to exercise the DEFAULT
# frozen-root resolution, which no sandbox can reach. No commit is made in a live repo —
# a synthetic commit on a live `main` cannot be removed without rewriting shared
# history (git-policy.md §4/§5). Hooks come from the config under test
# (${CLAUDE_CONFIG_DIR:-$HOME/.claude}).
#
# Anti-vacuity, applied throughout: every "absent from the commit" assertion is
# preceded by proof that a commit landed, and every "refused" assertion uses an
# input the gate would otherwise ALLOW (a bookkeeping file out of tree, or a
# harness-source commit), so the refusal is attributable to scope.
#
# Exit 0 = all cases pass.

set -uo pipefail

CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
HOOKS="$CFG/hooks"
SCOPE_SH="$HOOKS/session-scope.sh"
CS_PY="$HOOKS/commit_scope.py"
HELPER="$HOOKS/worktree-helper.sh"
PROMOTE="$CFG/bin/claude-promote"
RUN_PY="$CFG/skills/execute-plan/run.py"
REAL_CONFIG_SOURCE="$(command -v config-source 2>/dev/null)"
PASS=0; FAIL=0

check() {  # $1 label  $2 rc (0 = pass)  $3 detail
  # EVERY detail line is indented, not just the first. Cases 8d/8e pass `tail -3` of
  # a sub-suite that prints the identical '  PASS  %s' shape, so with the 8-space
  # indent in the format string only line 1 was shifted and lines 2-3 arrived looking
  # exactly like this suite's own verdicts. Any tool parsing this output (see
  # mutate_publish_scope.py's CASE_RE) then reads a sub-suite's results as ours.
  if [ "$2" -eq 0 ]; then printf '  PASS  %s\n' "$1"; PASS=$((PASS+1))
  else
    printf '  FAIL  %s\n' "$1"
    [ -n "${3:-}" ] && printf '%s\n' "$3" | sed 's/^/        /'
    FAIL=$((FAIL+1))
  fi
}
has()     { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac; }
head_of() { git -C "$1" rev-parse HEAD 2>/dev/null; }
tip_files() { git -C "$1" diff-tree --no-commit-id --name-only -r "${2:-HEAD}" | sort; }
staged()  { git -C "$1" diff --cached --name-only; }

for need in "$SCOPE_SH" "$CS_PY" "$HELPER" "$PROMOTE" "$RUN_PY"; do
  [ -e "$need" ] || { echo "missing $need"; exit 1; }
done
[ -n "$REAL_CONFIG_SOURCE" ] || { echo "config-source not installed — cannot replay the claude-promote incidents"; exit 1; }

T="$(mktemp -d "${TMPDIR:-/tmp}/publish-scope-e2e.XXXXXX")"; T="$(cd "$T" && pwd -P)"
trap 'rm -rf "$T"' EXIT
# Measured, not asserted by comment: the live inventory this suite must not touch.
LIVE_WARN_LOG="$CFG/state/commit-scope-warn.log"
live_rows() { [ -f "$LIVE_WARN_LOG" ] && wc -l < "$LIVE_WARN_LOG" | tr -d ' ' || echo 0; }
LIVE_ROWS_BEFORE="$(live_rows)"
# The gate writes its best-effort inventory under ${CLAUDE_CONFIG_DIR}/state, so
# the sandbox phases run with CLAUDE_CONFIG_DIR pointed into $T — otherwise every
# refusal this file provokes would append a row to the LIVE warn log. Every path
# this script needs was resolved into $CFG above; section 8 passes CFG explicitly.
export CLAUDE_CONFIG_DIR="$T/cfg-state"

# --------------------------------------------------------------------------- #
# Projects-shaped repo: bookkeeping manifest paths, gate + trailer installed.
# --------------------------------------------------------------------------- #
mk_projects() {  # $1 name -> echoes repo path
  local R="$T/$1"
  mkdir -p "$R/Thoughts" "$R/Diary" "$R/.claude/logs"
  git -C "$R" init -q -b main
  git -C "$R" config user.email t@t; git -C "$R" config user.name t
  git -C "$R" config commit.gpgsign false
  printf 'seed\n' > "$R/TODO.md"; printf 'stats\n' > "$R/Stats.md"
  printf '# diary\n' > "$R/Diary/2026-08-14.md"
  printf 'existing spine\n' > "$R/Thoughts/rsa-20260808213101_THOUGHT.md"
  git -C "$R" add -A; git -C "$R" -c core.hooksPath=/dev/null commit -qm seed
  bash "$HELPER" install --repo "$R" >/dev/null 2>&1
  printf '%s' "$R"
}

armed_probe() {  # $1 repo — a bare commit of an otherwise-allowed bookkeeping file must be refused
  local R="$1" h err
  printf 'probe\n' >> "$R/Stats.md"; git -C "$R" add -- Stats.md
  h="$(head_of "$R")"
  err="$(git -C "$R" commit -qm probe 2>&1)"
  [ "$(head_of "$R")" = "$h" ] && has "$err" "scope BLOCKED"
  local rc=$?
  git -C "$R" restore --staged -- Stats.md; git -C "$R" checkout -q -- Stats.md
  return $rc
}

close_publish() {  # $1 repo $2 sid $3 message -> sets PUB_OUT, DECL
  local R="$1" SID="$2" MSG="$3" P
  # Real /close ORDER: §2 archives the ledger into _processed/ BEFORE §3 reads it.
  # Replaying without the archive is how the post-S8 audit's D1 went unseen.
  mkdir -p "$R/.claude/logs/_processed"
  [ -f "$R/.claude/logs/_session_files-$SID.log" ] && \
    mv "$R/.claude/logs/_session_files-$SID.log" "$R/.claude/logs/_processed/"
  # CLAUDE_CONFIG_DIR must be the config UNDER TEST here: session-scope.sh reads the
  # bookkeeping manifest from it to reconcile merge_union paths. Pointed at the
  # sandbox state dir, the manifest is missing, reconciliation silently never runs,
  # and a regression that reconciled `Thoughts/` (the ffeeef23 shape) passed this
  # suite — found by mutation.
  DECL="$(CLAUDE_CONFIG_DIR="$CFG" bash "$SCOPE_SH" "$SID" "$R" --publish-scope 2>/dev/null)"
  local PATHS=()
  while IFS= read -r P; do [ -n "$P" ] && PATHS+=("$P"); done <<< "$DECL"
  PUB_OUT="$(cd "$R" && python3 "$CS_PY" publish -m "$MSG" --session-id "$SID" -- "${PATHS[@]}" 2>&1)"
}

echo "=== 1. ffeeef23 — /close carried another topic's Thoughts files ==="
R="$(mk_projects close-ffeeef23)"
[ -e "$R/.git/hooks/pre-commit" ] && [ -e "$R/.git/hooks/commit-msg" ] && armed_probe "$R"
check "1.0 gate + trailer installed and ARMED" $?
# Session A (research-fc-backlog): diary, stats, TODO — its own close.
printf 'A diary\n' >> "$R/Diary/2026-08-14.md"; printf 'A stats\n' >> "$R/Stats.md"
printf -- '- [x] A done\n' >> "$R/TODO.md"
printf '%s\n' "$R/Diary/2026-08-14.md" "$R/Stats.md" "$R/TODO.md" > "$R/.claude/logs/_session_files-SID-A.log"
# Session B (research-source-adapters): NEW design + review reports; two STAGED, two not.
for f in rsa-20260808213101_DESIGN.md rsa-20260808213101_DESIGN_REVIEW_REPORT_1.md \
         rsa-20260808213101_DESIGN_REVIEW_REPORT_2.md rsa-20260808213101_THOUGHT_NEW.md; do
  printf 'B %s\n' "$f" > "$R/Thoughts/$f"
  printf '%s\n' "$R/Thoughts/$f" >> "$R/.claude/logs/_session_files-SID-B.log"
done
git -C "$R" add -- Thoughts/rsa-20260808213101_DESIGN.md Thoughts/rsa-20260808213101_THOUGHT_NEW.md
# Session B ALSO appended to the shared TODO.md — the one case no declaration can
# separate (C8/C9). The incident commit did carry TODO.md.
printf -- '- [ ] B line\n' >> "$R/TODO.md"
printf '%s\n' "$R/TODO.md" >> "$R/.claude/logs/_session_files-SID-B.log"

# 1a. The DEFECT path — the shape /close used to improvise: stage own files, bare commit.
git -C "$R" add -- Diary/2026-08-14.md Stats.md TODO.md
H="$(head_of "$R")"
ERR="$(git -C "$R" commit -m "Session close: research-fc-backlog" 2>&1)"
[ "$(head_of "$R")" = "$H" ] && has "$ERR" "scope BLOCKED" && has "$ERR" "rsa-20260808213101_DESIGN.md"
check "1a the pre-fix /close commit (scoped add, bare commit) is REFUSED, naming B's file" $? "$ERR"

# C3 — what /close feeds its commit-message composer is the SCOPED dry-run stat.
# The pre-fix /close read `git diff --cached --stat` (the whole shared index), so
# its messages described another session's work. With B's design staged, that
# global stat names B; the scoped one must not.
DECL="$(CLAUDE_CONFIG_DIR="$CFG" bash "$SCOPE_SH" SID-A "$R" --publish-scope 2>/dev/null)"
DRY_PATHS=(); while IFS= read -r P; do [ -n "$P" ] && DRY_PATHS+=("$P"); done <<< "$DECL"
DRY="$(cd "$R" && python3 "$CS_PY" publish --dry-run -m x --session-id SID-A -- "${DRY_PATHS[@]}" 2>/dev/null)"
GLOBAL_STAT="$(git -C "$R" diff --cached --stat)"
has "$GLOBAL_STAT" "rsa-20260808213101_DESIGN.md" && has "$DRY" "Diary/2026-08-14.md" \
  && ! has "$DRY" "rsa-20260808213101"
check "1c' C3: the scoped stat /close feeds its composer names only A's files (the global one names B's)" $? \
      "scoped: $DRY"

# 1b. The CONVERTED path — /close §3.
close_publish "$R" SID-A "Session close: research-fc-backlog"
[ "$(head_of "$R")" != "$H" ]
check "1b /close publish produced a commit (guard)" $? "$PUB_OUT"
C="$(tip_files "$R")"
[ "$C" = "$(printf '%s\n' Diary/2026-08-14.md Stats.md TODO.md | sort)" ]
check "1b A's commit holds EXACTLY A's three files" $? "committed: $(tr '\n' ' ' <<< "$C")"
! grep -q rsa- <<< "$C" && grep -q 'rsa-20260808213101_DESIGN.md' <<< "$(staged "$R")" \
  && grep -q '?? Thoughts/rsa-20260808213101_DESIGN_REVIEW_REPORT_1.md' <<< "$(git -C "$R" status --porcelain)"
check "1b B's files are absent; B's staged file still staged, B's untracked still untracked" $?
! has "$(git -C "$R" log -1 --format=%B)" "Unscoped-Publish:"
check "1b a declared close carries no waiver trailer" $?
# C9 announced BEFORE the commit, C8 both sessions' lines survive in it.
has "$PUB_OUT" "also written by" && has "$PUB_OUT" "SID-B"
check "1d C9: the joint TODO.md edit was ANNOUNCED, naming the other session" $? "$PUB_OUT"
TODO_AT_HEAD="$(git -C "$R" show HEAD:TODO.md)"
has "$TODO_AT_HEAD" "A done" && has "$TODO_AT_HEAD" "B line"
check "1d C8: both sessions' TODO.md lines survived the commit" $? "$TODO_AT_HEAD"

# C1 — no session waits on another. The only mechanism that could make one wait
# is the repo-wide bookkeeping flock. Hold it from another process for 20s and
# publish: the publish must complete well inside that window.
printf 'A later\n' >> "$R/Diary/2026-08-14.md"
python3 "$HOOKS/tests/_hold_bookkeeping_lock.py" "$R/TODO.md" 20 > "$T/lock-held" 2>&1 &
HOLDER=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do grep -q HELD "$T/lock-held" 2>/dev/null && break; sleep 0.3; done
grep -q HELD "$T/lock-held"
check "1e C1 precondition: another process holds the repo's bookkeeping lock" $? "$(cat "$T/lock-held")"
H="$(head_of "$R")"
START=$SECONDS
OUT="$(cd "$R" && python3 "$CS_PY" publish -m "A later" --session-id SID-A -- Diary/2026-08-14.md 2>&1)"
ELAPSED=$((SECONDS - START))
[ "$(head_of "$R")" != "$H" ] && [ "$ELAPSED" -lt 10 ]
check "1e C1: a publish completes while the lock is held (took ${ELAPSED}s, no wait)" $? "$OUT"
kill "$HOLDER" 2>/dev/null; wait "$HOLDER" 2>/dev/null

# Non-vacuity for the reconciliation half: make TODO.md dirty WITHOUT a ledger
# entry — the ONLY way it can enter the declaration is the manifest reconcile.
R2="$(mk_projects close-reconcile)"
printf -- '- [ ] unledgered\n' >> "$R2/TODO.md"
printf 'B plan\n' > "$R2/Thoughts/other-topic_PLAN.md"
: > "$R2/.claude/logs/_session_files-SID-A.log"
CLAUDE_CONFIG_DIR="$CFG" bash "$SCOPE_SH" SID-A "$R2" --publish-scope > "$T/decl-reconcile" 2>/dev/null
grep -qx 'TODO.md' "$T/decl-reconcile" && ! grep -q 'Thoughts/' "$T/decl-reconcile"
check "1c /close reconciles an unledgered TODO.md (merge_union) but never a Thoughts/ spine" $? \
      "declared: $(tr '\n' ' ' < "$T/decl-reconcile")"

echo "=== 2. 213237fa — /close carried review-skill-system research files ==="
R="$(mk_projects close-213237fa)"
armed_probe "$R"; check "2.0 gate ARMED" $?
# Session A (research-source-adapters S4): edits its own EXISTING spine + bookkeeping.
printf 'A S4\n' >> "$R/Thoughts/rsa-20260808213101_THOUGHT.md"
printf 'A diary\n' >> "$R/Diary/2026-08-14.md"; printf -- '- [ ] A\n' >> "$R/TODO.md"
printf '%s\n' "$R/Thoughts/rsa-20260808213101_THOUGHT.md" "$R/Diary/2026-08-14.md" "$R/TODO.md" \
  > "$R/.claude/logs/_session_files-SID-A.log"
# Session B (review-skill-system): three NEW research files + its own spine, none staged.
for f in rss-20260727002631_ANTIGRAVITY_RESEARCH.md rss-20260727002631_CMUX_RESEARCH.md \
         rss-20260727002631_OPENCODE_RESEARCH.md rss-20260727002631_THOUGHT.md; do
  printf 'B %s\n' "$f" > "$R/Thoughts/$f"
  printf '%s\n' "$R/Thoughts/$f" >> "$R/.claude/logs/_session_files-SID-B.log"
done
# 2a. The DEFECT path — a whole-tree stage + bare commit.
H="$(head_of "$R")"
ERR="$( { git -C "$R" add -A && git -C "$R" commit -m "Session close: research-source-adapters S4"; } 2>&1)"
[ "$(head_of "$R")" = "$H" ] && has "$ERR" "scope BLOCKED" && has "$ERR" "rss-20260727002631_OPENCODE_RESEARCH.md"
check "2a the whole-tree close (add -A + bare commit) is REFUSED, naming B's research file" $? "$ERR"
git -C "$R" restore --staged -- . >/dev/null 2>&1   # sandbox only: undo 2a's whole-tree stage
# 2b. The CONVERTED path.
close_publish "$R" SID-A "Session close: research-source-adapters S4"
[ "$(head_of "$R")" != "$H" ]
check "2b /close publish produced a commit (guard)" $? "$PUB_OUT"
C="$(tip_files "$R")"
[ "$C" = "$(printf '%s\n' Diary/2026-08-14.md TODO.md Thoughts/rsa-20260808213101_THOUGHT.md | sort)" ]
check "2b A's commit holds EXACTLY A's own spine + bookkeeping" $? "committed: $(tr '\n' ' ' <<< "$C")"
! grep -q rss- <<< "$C" && ! grep -q rss- <<< "$(staged "$R")"
check "2b none of B's research files were committed OR staged" $?

# --------------------------------------------------------------------------- #
# config-source-shaped sandbox for claude-promote (same shim approach as S3's suite).
# --------------------------------------------------------------------------- #
mk_config_source() {  # $1 name -> sets DEST SRC SHIM
  local B="$T/$1"
  DEST="$B/dest"; SRC="$B/src"; SHIM="$B/bin"
  mkdir -p "$DEST/.claude/rules" "$DEST/.claude/hooks" "$SRC" "$SHIM"
  cat > "$SHIM/config-source" <<EOF
#!/usr/bin/env bash
exec "$REAL_CONFIG_SOURCE" -S "$SRC" -D "$DEST" "\$@"
EOF
  printf '#!/usr/bin/env bash\necho "https://example.invalid/pr/1"\n' > "$SHIM/gh"
  chmod +x "$SHIM/config-source" "$SHIM/gh"
}
promote() {  # env-bound run of the REAL claude-promote against the sandbox
  PATH="$SHIM:$PATH" CLAUDE_CONFIG_DIR="$DEST/.claude" HOME="$DEST" "$PROMOTE" --no-verify "$@" 2>&1
}
cz() { PATH="$SHIM:$PATH" config-source "$@"; }
src_rel() { local s; s="$(cz source-path "$1" 2>/dev/null)"; printf '%s' "${s#"$SRC"/}"; }
promote_branches() { git -C "$SRC" for-each-ref --format='%(refname:short)' refs/heads | grep '^promote/' | sort; }
newest_promote() { git -C "$SRC" for-each-ref --sort=-committerdate --format='%(refname:short)' refs/heads | grep '^promote/' | head -1; }
seed_source() {  # $@ live paths to seed as managed
  local f
  for f in "$@"; do cz add "$f" >/dev/null 2>&1; done
  git -C "$SRC" init -q
  git -C "$SRC" config user.email t@t; git -C "$SRC" config user.name t
  git -C "$SRC" config commit.gpgsign false
  git -C "$SRC" add -A; git -C "$SRC" -c core.hooksPath=/dev/null commit -qm seed
  PATH="$SHIM:$PATH" bash "$HELPER" install --repo "$SRC" >/dev/null 2>&1
}
src_armed() {  # bare commit in the harness source must be refused (it is otherwise allowed there)
  local h err
  printf 'probe\n' > "$SRC/probe.txt"; git -C "$SRC" add probe.txt
  h="$(head_of "$SRC")"
  err="$(PATH="$SHIM:$PATH" git -C "$SRC" commit -qm probe 2>&1)"
  [ "$(head_of "$SRC")" = "$h" ] && has "$err" "scope BLOCKED"
  local rc=$?
  git -C "$SRC" restore --staged -- probe.txt; rm -f "$SRC/probe.txt"
  return $rc
}

echo "=== 3. 200a247 — claude-promote carried a concurrent rules/prompt-engineering.md ==="
mk_config_source promote-200a247
printf 'A registry v0\n' > "$DEST/.claude/hooks/os_registry.py"
printf 'PE v0\n'         > "$DEST/.claude/rules/prompt-engineering.md"
seed_source "$DEST/.claude/hooks/os_registry.py" "$DEST/.claude/rules/prompt-engineering.md"
src_armed; check "3.0 gate + trailer installed in the sandbox source and ARMED" $?
# Session A (output-security S2) edits its registry and adds a new module.
printf 'A registry v1\n' > "$DEST/.claude/hooks/os_registry.py"
printf 'A new\n'         > "$DEST/.claude/hooks/os_spotlight.py"
# Session B re-synthesizes the PE reference, live, concurrently.
PE_B='PE re-synthesized by a concurrent session — must not ride in A'"'"'s commit'
printf '%s\n' "$PE_B" > "$DEST/.claude/rules/prompt-engineering.md"
PE_REL="$(src_rel "$DEST/.claude/rules/prompt-engineering.md")"

# 3a. The DEFECT path — an unflagged promotion. Pre-S6 it committed everything dirty.
BR0="$(promote_branches)"
OUT="$(promote -m "S2 output-security")"
[ "$(promote_branches)" = "$BR0" ] && has "$OUT" "publishing NOTHING" && has "$OUT" "prompt-engineering"
check "3a an UNFLAGGED claude-promote now publishes nothing and names B's rule file" $? "$OUT"
# ...and the raw commit that produced it is refused by the gate in the source.
git -C "$SRC" add -A
H="$(head_of "$SRC")"
ERR="$(PATH="$SHIM:$PATH" git -C "$SRC" commit -m "S2 output-security" 2>&1)"
[ "$(head_of "$SRC")" = "$H" ] && has "$ERR" "scope BLOCKED"
check "3a a bare commit in the harness source is REFUSED by the gate" $? "$ERR"
git -C "$SRC" restore --staged -- . >/dev/null 2>&1

# 3b. The CONVERTED path — a declared promotion.
sleep 1.1
OUT="$(promote -m "S2 output-security" --paths "$DEST/.claude/hooks/os_registry.py $DEST/.claude/hooks/os_spotlight.py")"
BR="$(newest_promote)"
[ -n "$BR" ] && [ "$(promote_branches)" != "$BR0" ]
check "3b declared promotion created a commit on a promote branch (guard)" $? "$OUT"
C="$(tip_files "$SRC" "$BR")"
grep -q os_registry <<< "$C" && grep -q os_spotlight <<< "$C" && ! grep -q prompt-engineering <<< "$C"
check "3b A's two files are in the commit; B's PE re-synthesis is NOT" $? "committed: $(tr '\n' ' ' <<< "$C")"
[ "$(cat "$DEST/.claude/rules/prompt-engineering.md")" = "$PE_B" ]
check "3b B's LIVE edit is byte-identical after A's promotion" $?
! has "$(git -C "$SRC" log -1 --format=%B "$BR")" "Unscoped-Publish:"
check "3b a declared promotion carries no waiver trailer" $?

echo "=== 4. 4e57836 — claude-promote carried six output-security files ==="
mk_config_source promote-4e57836
printf 'picker v0\n' > "$DEST/.claude/hooks/source_picker.py"
for i in 1 2 3 4 5 6; do printf 'os v0\n' > "$DEST/.claude/hooks/os_file_$i.py"; done
seed_source "$DEST/.claude/hooks/source_picker.py" "$DEST"/.claude/hooks/os_file_*.py
src_armed; check "4.0 gate ARMED in the sandbox source" $?
printf 'picker v1\n' > "$DEST/.claude/hooks/source_picker.py"                       # session A
for i in 1 2 3 4 5 6; do printf 'os v1 (session B)\n' > "$DEST/.claude/hooks/os_file_$i.py"; done  # session B
cz re-add >/dev/null 2>&1
OS1_REL="$(src_rel "$DEST/.claude/hooks/os_file_1.py")"
git -C "$SRC" add -- "$OS1_REL"                  # worst case: B had STAGED one in the shared index
BR0="$(promote_branches)"
# 4a. The DEFECT path for THIS incident, not only for its sibling: an unflagged
# promotion publishes nothing and names B's files, and the raw bare commit that
# produced 4e57836 is refused by the gate.
OUT="$(promote -m "source picker + approval gate")"
[ "$(promote_branches)" = "$BR0" ] && has "$OUT" "publishing NOTHING" && has "$OUT" "os_file_"
check "4a an UNFLAGGED claude-promote publishes nothing and names B's files" $? "$OUT"
H="$(head_of "$SRC")"
ERR="$(PATH="$SHIM:$PATH" git -C "$SRC" commit -m "source picker + approval gate" 2>&1)"
[ "$(head_of "$SRC")" = "$H" ] && has "$ERR" "scope BLOCKED" && has "$ERR" "os_file_1"
check "4a a bare commit of the shared index is REFUSED, naming B's staged file" $? "$ERR"
sleep 1.1
OUT="$(promote -m "source picker + approval gate" --paths "$DEST/.claude/hooks/source_picker.py")"
BR="$(newest_promote)"
[ -n "$BR" ] && [ "$(promote_branches)" != "$BR0" ]
check "4 declared promotion created a commit (guard)" $? "$OUT"
C="$(tip_files "$SRC" "$BR")"
grep -q source_picker <<< "$C" && ! grep -q os_file_ <<< "$C"
check "4 A's commit holds the picker and NONE of B's six files" $? "committed: $(tr '\n' ' ' <<< "$C")"
has "$(git -C "$SRC" diff --cached --name-only)" "os_file_1"
check "4 B's STAGED file is still staged (the commit did not consume it)" $? \
      "staged: $(git -C "$SRC" diff --cached --name-only | tr '\n' ' ')"

# --------------------------------------------------------------------------- #
# /ninja-fix and an execute-plan slice commit, in a gated repo's topic worktree.
# --------------------------------------------------------------------------- #
mk_worktree() {  # $1 name -> echoes worktree path with a two-session state
  local B="$T/$1" W="$T/$1-wt"
  mkdir -p "$B"
  git -C "$B" init -q -b main
  git -C "$B" config user.email t@t; git -C "$B" config user.name t
  git -C "$B" config commit.gpgsign false
  printf 'seed\n' > "$B/seed.txt"; git -C "$B" add -A
  git -C "$B" -c core.hooksPath=/dev/null commit -qm seed
  bash "$HELPER" install --repo "$B" >/dev/null 2>&1
  git -C "$B" worktree add -q -b "topic-$1" "$W" >/dev/null 2>&1
  printf 'mine\n' > "$W/mine.txt"; printf 'foreign\n' > "$W/foreign.txt"
  git -C "$W" add -- foreign.txt
  printf '%s' "$W"
}

echo "=== 5. /ninja-fix under enforcement ==="
W="$(mk_worktree ninja)"
H="$(head_of "$W")"; ERR="$(git -C "$W" commit -qm bare 2>&1)"
[ "$(head_of "$W")" = "$H" ] && has "$ERR" "scope BLOCKED"
check "5.0 gate ARMED in the topic worktree" $? "$ERR"
NINJA_LINE="$(grep -E '^python3 ~/\.claude/hooks/commit_scope\.py publish ' "$CFG/skills/ninja-fix/SKILL.md")"
[ "$NINJA_LINE" = 'python3 ${KIT_HOOKS_DIR}/commit_scope.py publish --repo "<repo root containing the edited file>" -m "[ninja-fix] <one-sentence rationale>" -- "<the one file the Edit/Write call changed>"' ]
check "5.1 the /ninja-fix SKILL still prescribes the scoped publish this case runs" $? "line: $NINJA_LINE"
OUT="$(python3 "$CS_PY" publish --repo "$W" -m "[ninja-fix] fix the thing" -- "mine.txt" 2>&1)"
[ "$(head_of "$W")" != "$H" ] && [ "$(tip_files "$W")" = "mine.txt" ] && has "$(staged "$W")" "foreign.txt"
check "5.2 /ninja-fix commits exactly the edited file; the foreign staged file survives" $? "$OUT"

# 5.3-5.5 — the PRIMARY-CHECKOUT path, which 5.0-5.2 above structurally cannot reach.
# /ninja-fix's SKILL Step 0 states it does NOT auto-place by default: it runs in the
# active project's cwd, which for a project-side fix is the primary checkout on main.
# The commit-chokepoint gate refuses a commit there, so the publish command 5.1 pins
# cannot complete on the skill's own default path. Found 2026-09-18 by the first
# genuine /ninja-fix run under enforcement (Projects commit 77da7d3e), which landed
# only under ALLOW_OUT_OF_TREE=1.
#
# Anti-vacuity: 5.2 just proved the IDENTICAL publish command succeeds in a worktree
# of this same repo, so a refusal here is attributable to placement, not to the
# command, the repo, or the file. The foreign staged file proves the override stays
# scoped rather than degrading to a whole-index commit.
B="$T/ninja"
printf 'primary\n' > "$B/domain.txt"
printf 'foreign-primary\n' > "$B/foreign2.txt"; git -C "$B" add -- foreign2.txt
H="$(head_of "$B")"
OUT="$(python3 "$CS_PY" publish --repo "$B" -m "[ninja-fix] fix from primary checkout" -- "domain.txt" 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && [ "$(head_of "$B")" = "$H" ] && has "$OUT" "outside any topic worktree"
check "5.3 /ninja-fix's documented publish is REFUSED from the primary checkout" $? "rc=$rc $OUT"
has "$(staged "$B")" "domain.txt"
check "5.4 the refusal leaves the declared path STAGED — publish is not atomic across the gate" $? "staged: $(staged "$B")"
H="$(head_of "$B")"
OUT="$(ALLOW_OUT_OF_TREE=1 python3 "$CS_PY" publish --repo "$B" -m "[ninja-fix] fix from primary checkout" -- "domain.txt" 2>&1)"
[ "$(head_of "$B")" != "$H" ] && [ "$(tip_files "$B")" = "domain.txt" ] && has "$(staged "$B")" "foreign2.txt"
check "5.5 ALLOW_OUT_OF_TREE=1 lands it, still scoped; the foreign staged file survives" $? "$OUT"

# 5.6 — the remedy for 5.3 must stay documented where the AI reads it, or the skill
# silently regresses to prescribing (5.1) a command that cannot complete on its own
# default path. Pins the override form AND that it is NOT the unscoped variable.
grep -q 'ALLOW_OUT_OF_TREE=1 python3 ~/\.claude/hooks/commit_scope\.py publish' "$CFG/skills/ninja-fix/SKILL.md" \
  && grep -q 'commit is outside any topic worktree' "$CFG/skills/ninja-fix/SKILL.md" \
  && ! grep -q 'ALLOW_UNSCOPED_COMMIT=1 python3 ~/\.claude/hooks/commit_scope\.py publish' "$CFG/skills/ninja-fix/SKILL.md"
check "5.6 the SKILL documents the out-of-tree override as its primary-checkout path" $?

# 5.7-5.8 — the OTHER half of /ninja-fix's publish contract: a file in the managed
# ~/.claude scope must route to `claude-promote --paths`, never a commit in the
# frozen ~/.claude/.git (D3). Until now neither half of this branch was covered:
# section 5 only ever exercised the commit_scope path, and the 2026-09-18 real run
# edited a Projects file, so the managed-scope branch had no test AND no real run.
# COMMIT_SCOPE_FROZEN_ROOT (commit_scope.py:856) lets the frozen-root detection be
# pointed at a synthetic repo, so this stays inside $T and never touches ~/.claude.
FROZ="$T/frozen-harness"
mkdir -p "$FROZ"
git -C "$FROZ" init -q -b main
git -C "$FROZ" config user.email t@t; git -C "$FROZ" config user.name t
git -C "$FROZ" config commit.gpgsign false
printf 'seed\n' > "$FROZ/seed.txt"; git -C "$FROZ" add -A
git -C "$FROZ" -c core.hooksPath=/dev/null commit -qm seed
printf 'rule\n' > "$FROZ/rules/"  2>/dev/null || mkdir -p "$FROZ/rules"
printf 'rule\n' > "$FROZ/rules/x.md"
H="$(head_of "$FROZ")"
OUT="$(COMMIT_SCOPE_FROZEN_ROOT="$FROZ" python3 "$CS_PY" publish --repo "$FROZ" -m "[ninja-fix] managed-scope edit" -- "rules/x.md" 2>&1)"; rc=$?
[ "$rc" -ne 0 ] && [ "$(head_of "$FROZ")" = "$H" ] && has "$OUT" "frozen harness snapshot" && has "$OUT" "claude-promote"
check "5.7 publish REFUSES the frozen harness root and names claude-promote instead" $? "rc=$rc $OUT"
# Anti-vacuity: the identical publish on the identical repo SUCCEEDS once the root
# is not the declared frozen one, so 5.7's refusal is attributable to frozen-root
# detection, not to the repo, the file, or a missing gate.
OUT="$(ALLOW_OUT_OF_TREE=1 COMMIT_SCOPE_FROZEN_ROOT="$T/not-the-frozen-root" python3 "$CS_PY" publish --repo "$FROZ" -m "[ninja-fix] managed-scope edit" -- "rules/x.md" 2>&1)"
[ "$(head_of "$FROZ")" != "$H" ] && [ "$(tip_files "$FROZ")" = "rules/x.md" ]
check "5.8 the same publish succeeds when the root is NOT the frozen one (5.7 is not vacuous)" $? "$OUT"
grep -q 'claude-promote -m "\[ninja-fix\] <one-sentence rationale>" --paths' "$CFG/skills/ninja-fix/SKILL.md" \
  && grep -q 'do NOT use `publish`' "$CFG/skills/ninja-fix/SKILL.md"
check "5.9 the SKILL routes managed-scope ~/.claude files to claude-promote --paths" $?
# 5.10 — the DEFAULT frozen-root resolution, which 5.7 does not reach. 5.7 supplies
# COMMIT_SCOPE_FROZEN_ROOT, so a regression in commit_scope.py's default
# (Path.home()/".claude") would leave 5.7 and 5.8 green while the real harness repo
# went unprotected — the exact thing D3 exists to prevent. This case runs with NO
# env override against the live ~/.claude.
#
# --dry-run is LOAD-BEARING, not caution. The frozen-root guard (commit_scope.py:734)
# precedes the dry-run return (:809), which precedes `git add` (:813) — so this still
# exercises the default resolution, but a write is structurally impossible even when
# the guard is absent. Without it the case is safe only while the property it tests
# still holds: on the very first run after a default-resolution regression, THIS CASE
# would itself stage and commit into the live frozen repo, which carries no pre-commit
# hook to stop it. That is not hypothetical — it happened on 2026-09-18 when the
# mutation harness disabled the guard and this case committed `rules/git-policy.md`
# into `~/.claude/.git` (undone with `git reset --mixed`; the repo is unpushed).
# A post-hoc "did the tree stay clean" assertion detects that damage; it cannot
# prevent it. See the suite header's isolation invariant at the top of this file.
if [ -d "$HOME/.claude/.git" ]; then
  H="$(head_of "$HOME/.claude")"
  BEFORE="$(staged "$HOME/.claude" | sort)"
  OUT="$(python3 "$CS_PY" publish --dry-run --repo "$HOME/.claude" -m "[test] must refuse" -- "rules/git-policy.md" 2>&1)"; rc=$?
  [ "$rc" -ne 0 ] && has "$OUT" "frozen harness snapshot" \
    && [ "$(head_of "$HOME/.claude")" = "$H" ] \
    && [ "$(staged "$HOME/.claude" | sort)" = "$BEFORE" ]
  check "5.10 the DEFAULT frozen-root resolution refuses the live ~/.claude, staging nothing" $? "rc=$rc $OUT"
else
  check "5.10 skipped — ~/.claude is not a git repo on this machine" 0
fi

echo "=== 6. execute-plan slice commit under enforcement ==="
W="$(mk_worktree execplan)"
REG=$'#### Slices\n| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Write targets |\n|----|------|------|------|------|------|------|------|\n| S1 | x | implementation | y | routine | — | - | `mine.txt` |\n'
PAYLOAD="$(python3 -c 'import json,sys; print(json.dumps({"slice_id":"S1","repo_dir":sys.argv[1],"commit_summary":"slice under enforcement","register_markdown":sys.argv[2]}))' "$W" "$REG")"
H="$(head_of "$W")"
OUT="$(printf '%s' "$PAYLOAD" | python3 "$RUN_PY" commit-slice 2>&1)"; rc=$?
[ "$rc" -eq 0 ] && [ "$(head_of "$W")" != "$H" ] && [ "$(tip_files "$W")" = "mine.txt" ] && has "$(staged "$W")" "foreign.txt"
check "6 commit-slice commits the slice's Write targets only; the foreign staged file survives" $? "rc=$rc $OUT"
[ "$(git -C "$W" log -1 --format=%s)" = "S1: slice under enforcement" ]
check "6 the slice commit carries its slice-id subject" $?

echo "=== 7. the named override still works when named, and is legible afterwards ==="
R="$(mk_projects override)"
printf 'waived\n' >> "$R/TODO.md"; printf 'other\n' >> "$R/Stats.md"; git -C "$R" add -- TODO.md Stats.md
H="$(head_of "$R")"
OUT="$(ALLOW_UNSCOPED_COMMIT=1 git -C "$R" commit -qm "deliberately unscoped" 2>&1)"
[ "$(head_of "$R")" != "$H" ] && [ "$(tip_files "$R")" = "$(printf 'Stats.md\nTODO.md')" ]
check "7a ALLOW_UNSCOPED_COMMIT=1 commits the whole staged set" $? "$OUT"
has "$(git -C "$R" log -1 --format=%B)" "Unscoped-Publish: authorized via ALLOW_UNSCOPED_COMMIT=1"
check "7b that commit carries the waiver trailer IN HISTORY" $?
printf 'more\n' >> "$R/TODO.md"
git -C "$R" add -- TODO.md; git -C "$R" commit -qm "declared" -- TODO.md >/dev/null 2>&1
! has "$(git -C "$R" log -1 --format=%B)" "Unscoped-Publish:"
check "7c a declared commit in the same repo carries NO trailer" $?

echo "=== 8. static layers over the config under test ==="
OUT="$(bash "$HOOKS/check-publish-scope.sh" --root "$CFG" 2>&1)"; rc=$?
[ "$rc" -eq 0 ]; check "8a check-publish-scope.sh clean" $? "$OUT"
OUT="$(CLAUDE_VERIFY_TARGET="$CFG" CLAUDE_CONFIG_DIR="$CFG" bash "$CFG/bin/claude-verify" --phase pre 2>&1)"; rc=$?
[ "$rc" -eq 0 ]; check "8b claude-verify --phase pre clean" $? "$(tail -5 <<< "$OUT")"
OUT="$(BOOKKEEPING_MODEL_FILE="$CFG/rules/bookkeeping-model.md" python3 "$HOOKS/bookkeeping_paths.py" check-drift 2>&1)"; rc=$?
[ "$rc" -eq 0 ]; check "8c bookkeeping_paths check-drift clean" $? "$OUT"
OUT="$(bash "$HOOKS/tests/test_commit_gate_scope.sh" 2>&1)"; rc=$?
[ "$rc" -eq 0 ]; check "8d gate suite (test_commit_gate_scope.sh) green" $? "$(tail -3 <<< "$OUT")"
OUT="$(CLAUDE_CONFIG_DIR="$T/cfg-state" python3 "$HOOKS/tests/test_bookkeeping_coherence_s2.py" 2>&1)"; rc=$?
[ "$rc" -eq 0 ]; check "8e coherence suite (test_bookkeeping_coherence_s2.py) green" $? "$(tail -3 <<< "$OUT")"

# Capture the result BEFORE building the label: a command substitution inside an
# earlier argument resets `$?` before `$?` is expanded, so the first version of
# this check printed "rows 9 -> 18" and still PASSED (found by mutation).
LIVE_ROWS_AFTER="$(live_rows)"
[ "$LIVE_ROWS_AFTER" = "$LIVE_ROWS_BEFORE" ]; rc=$?
check "8f the LIVE gate inventory was not written by this suite (rows $LIVE_ROWS_BEFORE -> $LIVE_ROWS_AFTER)" $rc

echo
printf 'RESULT: %d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]

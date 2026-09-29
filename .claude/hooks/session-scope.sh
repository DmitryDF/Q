#!/bin/bash
# Produce scoped diary data for one session.
# Called by /close skill before diary generation.
# Reads only this session's artifacts, outputs _session_scope-<SID>.md.
#
# Usage: session-scope.sh <session_id> [project_dir]
# Outputs: path to scope file on stdout

SESSION_ID="$1"
shift 2>/dev/null || true

# Flags are separated from the positional BEFORE anything consumes $2, because
# the usage line makes `project_dir` optional — so `--harness-scope` may legally
# arrive as the second argument. The earlier version read `PROJECT="${2:-.}"`
# up front, which turned the documented 2-argument form into
# PROJECT="--harness-scope"; LOG_DIR was then built from that literal, the ledger
# was never found, and the script exited 0 with NO output and NO error. A silent
# wrong answer, in the one mode A6 depends on. Measured: the 3-arg form printed
# the path list, the 2-arg form printed nothing.
HARNESS_SCOPE=0
PUBLISH_SCOPE=0
SCOPE_FILTER=""          # "" | bookkeeping | domain  (with --publish-scope)
PROJECT=""
for _arg in "$@"; do
  case "$_arg" in
    --harness-scope) HARNESS_SCOPE=1 ;;
    --publish-scope) PUBLISH_SCOPE=1 ;;
    --bookkeeping-only) SCOPE_FILTER="bookkeeping" ;;
    --domain-only) SCOPE_FILTER="domain" ;;
    *) [ -z "$PROJECT" ] && PROJECT="$_arg" ;;
  esac
done
[ -n "$PROJECT" ] || PROJECT="."

[ -z "$SESSION_ID" ] && { echo "Usage: session-scope.sh <session_id> [project_dir] [--harness-scope]" >&2; exit 1; }

# CANONICAL LEDGER LOCATION (glittery-humming-pine A5) — must match the writer.
# This reader moves with `track-session-files.sh` in the same slice, deliberately:
# relocating the writer alone would leave /close's scope file empty, which degrades
# the commit-message composer onto the git-delta path (the whole dirty tree under
# concurrency) and reopens the very mis-description A6 exists to close. Same
# `.git`-is-a-DIRECTORY discriminator, same reason — see the writer's comment,
# including why testing for a FILE was wrong for a subdirectory cwd.
LEDGER_ROOT="$PROJECT"
if [ ! -d "$PROJECT/.git" ]; then
  _canon="$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/bookkeeping_resolver.py" \
              root --cwd "$PROJECT" 2>/dev/null)"
  [ -n "$_canon" ] && LEDGER_ROOT="$_canon"
fi
LOG_DIR="$LEDGER_ROOT/.claude/logs"
SCOPE_FILE="$LOG_DIR/_session_scope-$SESSION_ID.md"

# --- helpers shared by --publish-scope and --harness-scope (post-S8 audit) ----
#
# D1 — READ THE LEDGER WHEREVER /close LEFT IT. /close §2 archives
# `_session_files-<SID>.log` into `_processed/` BEFORE §3 and §4 call this script.
# Reading only the live location made §3 declare nothing of the session's own
# (just the reconciled TODO/Diary/Stats) and §4 hand claude-promote an empty scope,
# so the close committed bookkeeping only and promoted nothing. Both locations are
# read, live first; a session that wrote more after an earlier archive has both.
_ledger_lines() {
  local f
  for f in "$LOG_DIR/_session_files-$SESSION_ID.log" "$LOG_DIR/_processed/_session_files-$SESSION_ID.log"; do
    [ -f "$f" ] && cat "$f"
  done
}

# D4 — COMPARE REAL PATHS. `record_write` stores resolved paths, while the caller
# may pass either spelling of a symlinked root (`~/Projects` vs `~/repos/Projects`
# in this workspace). A plain string-prefix test dropped every entry, silently.
_real() {  # $1 path -> resolved path if it exists, else the input unchanged
  if [ -d "$1" ]; then (cd "$1" 2>/dev/null && pwd -P) || printf '%s' "$1"
  elif [ -e "$1" ]; then
    local d; d="$(cd "$(dirname "$1")" 2>/dev/null && pwd -P)" && printf '%s/%s' "$d" "$(basename "$1")" || printf '%s' "$1"
  else printf '%s' "$1"; fi
}

# --- --harness-scope: the ~/.claude-scoped projection (A6 §4) ----------------
# /close §4 must hand `claude-promote` the session's OWN ~/.claude paths as
# `--session-scope`. The ledger already records absolute paths spanning both
# roots, so that list is a PROJECTION of this same ledger, not a new mechanism —
# which is why it belongs in the ledger's existing reader rather than in skill
# prose. `commit_scope.compile_scope` cannot supply it: it relativises against a
# single repo root by construction, so a ~/.claude path read from a ledger that
# lives under the Projects root falls outside it and is dropped.
#
# Emits absolute paths, one per line, deduped, and only those that still exist —
# a path recorded and later deleted would otherwise abort the whole `git add` on
# an unmatched pathspec and lose every other declared path with it.
#
# RESTRICTED TO THE MANAGED-CONFIG SCOPE, which is the load-bearing part. Being
# under ~/.claude is NOT sufficient: `plans/` and `logs/` are session-transient
# and config-source-managed by neither ignore rule nor add, so declaring one would make
# `claude-promote`'s step 2a `config-source add` it — promoting this session's own
# run-state into the shared config. Observed directly: an unrestricted projection
# offered `plans/<topic>.run-state.json`.
#
# The allowed set is not invented here. It is the harness's own managed scope,
# the list `claude-experiment spawn` clones and the global CLAUDE.md quotes:
# agents, bin, CLAUDE.md, hooks, rules, settings.json, skills. Anything outside
# it is dropped silently — it was never promotable, so its absence is not a loss.
# --- --publish-scope: the declared list /close §3 publishes (A6) -------------
# Emits repo-relative paths, one per line, for `commit_scope.py publish`.
#
# TWO sources, and the difference between them is load-bearing:
#
#   1. This session's own ledger entries that live inside this repo. That is the
#      declaration proper.
#
#   2. RECONCILIATION — a dirty path that matches a `merge_union: true` entry in
#      the shared-bookkeeping manifest but is absent from the ledger. Without
#      this, a run whose ledger is empty or partial compiles an empty scope,
#      skips the commit, and leaves the TODO and diary updates uncommitted on
#      disk. These files are jointly owned by construction, so reclaiming one is
#      safe: both sessions' lines belong in it.
#
# `merge_union: false` entries are NEVER reconciled, and `Thoughts/` is the one
# that matters. It is a dir-prefix entry, so a concurrent session's dirty
# `Thoughts/<other-topic>_PLAN.md` equally "matches the manifest and is absent
# from my ledger" — reconciling it would sweep another session's whole artifact
# into this commit, which is exactly what `ffeeef23` and `213237fa` did. A
# jointly-owned append file is safe to reclaim; a session-owned spine is not.
#
# The discriminator is read from the manifest rather than hardcoded here, so a
# future entry gets the right treatment without editing this script.
if [ "$PUBLISH_SCOPE" -eq 1 ]; then
  # The classifier is this script's SIBLING, resolved from its own location like
  # the commit gate resolves its siblings — not from CLAUDE_CONFIG_DIR, which a
  # sandbox may point elsewhere.
  CLASSIFIER="$(cd "$(dirname "$(realpath "$0" 2>/dev/null || echo "$0")")" && pwd)/bookkeeping_paths.py"
  command -v python3 >/dev/null 2>&1 && [ -f "$CLASSIFIER" ] || {
    echo "session-scope: cannot classify paths — python3 or $CLASSIFIER missing; refusing to emit a declaration" >&2
    exit 3
  }
  TMP_DECL="$(mktemp)"; TMP_DIRTY="$(mktemp)"; trap 'rm -f "$TMP_DECL" "$TMP_DIRTY"' EXIT

  # THE COMMIT ROOT, not the ledger root (post-S8 audit). The ledger lives at the
  # canonical (main-checkout) location so every worktree shares one; but the
  # declaration is handed to `publish`, which commits in the tree the caller is
  # standing in. Relativizing a linked worktree's entries against the MAIN
  # checkout dropped them or produced `<topic>/…` pathspecs that match nothing.
  # Same distinction `commit_scope._commit_root` draws.
  COMMIT_ROOT="$(git -C "$PROJECT" rev-parse --show-toplevel 2>/dev/null)"
  [ -n "$COMMIT_ROOT" ] || COMMIT_ROOT="$LEDGER_ROOT"
  COMMIT_ROOT_REAL="$(_real "$COMMIT_ROOT")"

  # (1) ledger entries inside the commit tree, relative to it — REAL paths (D4).
  while IFS= read -r p; do
    [ -n "$p" ] && [ -e "$p" ] || continue
    rp="$(_real "$p")"
    case "$rp" in
      "$COMMIT_ROOT_REAL"/*) printf '%s\n' "${rp#"$COMMIT_ROOT_REAL"/}" >> "$TMP_DECL" ;;
    esac
  done < <(_ledger_lines)

  # (2) reconcile merge_union manifest entries that are dirty but unledgered.
  # `-z` (D4): the plain porcelain form C-quotes names with spaces/non-ASCII.
  # `--untracked-files=all`: the default collapses a new directory to `dir/`,
  # which reconciled a DIRECTORY — refused by `publish`, aborting the close.
  # A rename/copy record carries its old path as an extra NUL field in EITHER
  # status column (`R ` staged, ` R` worktree), consumed and ignored.
  while IFS= read -r -d '' rec; do
    st="${rec:0:2}"; d="${rec:3}"
    case "$st" in *R*|*C*) IFS= read -r -d '' _old ;; esac
    [ -n "$d" ] && printf '%s\0' "$d" >> "$TMP_DIRTY"
  done < <(git -C "$COMMIT_ROOT" status --porcelain -z --untracked-files=all 2>/dev/null)
  python3 "$CLASSIFIER" match --merge-union-only < "$TMP_DIRTY" | while IFS= read -r d; do
    grep -Fxq -- "$d" "$TMP_DECL" 2>/dev/null || printf '%s\n' "$d" >> "$TMP_DECL"
  done

  # (3) optional split for a primary-checkout close (D5). Classified by the
  # commit gate's OWN `match_entry` (via `bookkeeping_paths.py match`), so the
  # "bookkeeping" half is exactly what the gate allows out of tree — a bash
  # re-implementation matched `Thoughts/` at any depth where the gate matches at
  # the root only, and the "bookkeeping-only" publish was refused as MIXED.
  sort -u "$TMP_DECL" | tr '\n' '\0' > "$TMP_DIRTY"
  case "$SCOPE_FILTER" in
    "") tr '\0' '\n' < "$TMP_DIRTY" | sed '/^$/d' ;;
    bookkeeping) python3 "$CLASSIFIER" match < "$TMP_DIRTY" ;;
    domain) python3 "$CLASSIFIER" match --invert < "$TMP_DIRTY" ;;
  esac
  exit 0
fi

if [ "$HARNESS_SCOPE" -eq 1 ]; then
  CONFIG_DIR="$(_real "${CLAUDE_CONFIG_DIR:-$HOME/.claude}")"
  while IFS= read -r p; do
    [ -n "$p" ] && [ -e "$p" ] || continue
    rp="$(_real "$p")"
    case "$rp" in
      "$CONFIG_DIR"/agents/*|"$CONFIG_DIR"/bin/*|"$CONFIG_DIR"/hooks/*|\
      "$CONFIG_DIR"/rules/*|"$CONFIG_DIR"/skills/*|\
      "$CONFIG_DIR"/CLAUDE.md|"$CONFIG_DIR"/settings.json)
        printf '%s\n' "$p" ;;
    esac
  done < <(_ledger_lines) | sort -u
  exit 0
fi

# --- Orphan cleanup (mirrors cleanup-plan-files.sh fallback) ---
# Sweep session artifacts older than 7 days that were never archived
find "$LOG_DIR" -maxdepth 1 -name '_session_files-*.log' -mtime +7 -delete 2>/dev/null
find "$LOG_DIR" -maxdepth 1 -name '_git_snapshot-*' -mtime +7 -delete 2>/dev/null
find "$LOG_DIR" -maxdepth 1 -name '_session_scope-*.md' -mtime +7 -delete 2>/dev/null

# 1. This session's prompt log
PROMPT_LOG=$(ls "$LOG_DIR"/prompts-*-"$SESSION_ID".log 2>/dev/null | head -1)

# 2. This session's output logs (collected into array to handle spaces in paths)
OUTPUT_FILES=()
while IFS= read -r -d '' f; do
  OUTPUT_FILES+=("$f")
done < <(find "$LOG_DIR/outputs" -maxdepth 1 -name "*-$SESSION_ID.md" -print0 2>/dev/null)

# 3. This session's file tracker
FILE_LOG="$LOG_DIR/_session_files-$SESSION_ID.log"

# 4. Git snapshot for delta computation
SNAPSHOT="$LOG_DIR/_git_snapshot-$SESSION_ID"

# Build scope file
echo "# Session Scope: $SESSION_ID" > "$SCOPE_FILE"
echo "" >> "$SCOPE_FILE"

echo "## Prompts" >> "$SCOPE_FILE"
if [ -f "$PROMPT_LOG" ]; then
  cat "$PROMPT_LOG" >> "$SCOPE_FILE"
else
  echo "## Scope Source: CONVERSATION" >> "$SCOPE_FILE"
  echo "_No session-ID-matched prompt log found. Derive scope from conversation context only — do not read other unarchived logs._" >> "$SCOPE_FILE"
fi

echo "" >> "$SCOPE_FILE"
echo "## Files Modified (Write/Edit)" >> "$SCOPE_FILE"
if [ -f "$FILE_LOG" ]; then
  sort -u "$FILE_LOG" >> "$SCOPE_FILE"
else
  echo "_No tracked files._" >> "$SCOPE_FILE"
fi

echo "" >> "$SCOPE_FILE"
echo "## Git Delta (new since session start)" >> "$SCOPE_FILE"
if [ -f "$SNAPSHOT" ]; then
  # Compare filenames: current dirty files minus pre-session dirty files
  CURR_FILES=$(git -C "$PROJECT" status --porcelain -uno 2>/dev/null | awk '{print $NF}' | sort)
  comm -13 "$SNAPSHOT" <(echo "$CURR_FILES") >> "$SCOPE_FILE"
else
  echo "_No git snapshot — cannot compute delta._" >> "$SCOPE_FILE"
fi

echo "" >> "$SCOPE_FILE"
echo "## Output Logs" >> "$SCOPE_FILE"
if [ ${#OUTPUT_FILES[@]} -gt 0 ]; then
  for f in "${OUTPUT_FILES[@]}"; do
    echo "### $(basename "$f")" >> "$SCOPE_FILE"
    cat "$f" >> "$SCOPE_FILE"
    echo "" >> "$SCOPE_FILE"
  done
else
  echo "_No output logs._" >> "$SCOPE_FILE"
fi

echo "$SCOPE_FILE"

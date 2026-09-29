#!/bin/bash
# PostToolUse hook: records which files each session modifies via Write/Edit.
# Enables per-session diary scoping in session-scope.sh.
#
# Trigger: PostToolUse on Write|Edit

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')

[ -z "$SESSION_ID" ] && exit 0
[ -z "$FILE_PATH" ] && exit 0

PROJECT=$(echo "$INPUT" | jq -r '.cwd // "unknown"')
if [ "$PROJECT" = "unknown" ] || [ -z "$PROJECT" ]; then
  LOG_DIR="$HOME/.claude/logs/_unknown"
else
  # CANONICAL LEDGER LOCATION (glittery-humming-pine A5).
  #
  # This used to be `$PROJECT/.claude/logs` unconditionally, where PROJECT is the
  # hook payload's cwd. That splits the ledger: two sessions working the same repo
  # from different cwds write to DIFFERENT directories, so `co_writers` — whose
  # whole job is noticing that another live session also wrote a path — compares
  # nothing at all. All of one repo's ledgers must sit together.
  #
  # The canonical root comes from the one shared resolver, never re-derived here.
  # But this hook runs on EVERY Write/Edit, and the resolver costs ~83ms against
  # the ~15ms this hook currently spends on its two jq calls (measured) — a 6x
  # slowdown on the hot path if called unconditionally.
  #
  # It is only NEEDED when cwd is not already the canonical root. The test for
  # that is "`.git` is a DIRECTORY here" — which is true only at a primary
  # checkout's own root, including one reached through a symlink (~/Projects ->
  # ~/repos/Projects keeps `.git` a directory and resolves to the same inode, so
  # it needs no call).
  #
  # The earlier version of this tested `[ -f "$PROJECT/.git" ]` — "is it a linked
  # worktree" — and was WRONG, because it reasoned only about the two kinds of
  # repo ROOT. In a SUBDIRECTORY of a checkout (this workspace keeps every
  # project as a subdirectory of one repo: Personal/your-project, [YourCompany]/…), a
  # `.git` entry does not exist at all, so `[ -f ]` was false, the resolver was
  # skipped, and the ledger landed under the SUBDIRECTORY. That is precisely the
  # split-ledger defect of gap G7, left intact for the commonest case while the
  # relocation claimed to have closed it. Measured in a scratch repo: cwd=repo
  # and cwd=repo/sub produced two different ledger directories.
  #
  # `[ ! -d ]` covers all three: directory -> already canonical, skip; file
  # (linked worktree) -> resolve; absent (subdirectory) -> resolve.
  LEDGER_ROOT="$PROJECT"
  if [ ! -d "$PROJECT/.git" ]; then
    _canon="$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/bookkeeping_resolver.py" \
                root --cwd "$PROJECT" 2>/dev/null)"
    [ -n "$_canon" ] && LEDGER_ROOT="$_canon"
  fi
  LOG_DIR="$LEDGER_ROOT/.claude/logs"
fi
mkdir -p "$LOG_DIR"

echo "$FILE_PATH" >> "$LOG_DIR/_session_files-$SESSION_ID.log"
exit 0

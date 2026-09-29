#!/bin/bash
# PostToolUse hook — link-validates Write/Edit to *_RESEARCH*.md (S6 / Plan A6).
# Mirrors factcheck-research-file.sh path filter; runs synchronously in
# foreground because the inline tagging must land before any downstream
# Stop-gate read of the file.
#
# PostToolUse CANNOT block — exit 0 always.
# Canon: ~/.claude/plans/lazy-doodling-wadler.md A6
#
# Env overrides forwarded to research_linkcheck.py:
#   LINKCHECK_PER_URL_TIMEOUT_S (default 5)
#   LINKCHECK_TOTAL_BUDGET_S    (default 20)
#   LINKCHECK_SKIP_HOURS        (default 24)
#   RP_STATE_DIR                (research_pipeline state dir; test override)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Path-glob filter — matches research files in any Thoughts/ or Docs/ tree,
# plus (S13/A8) any research file anywhere inside the workspace.
#
# S13/A8 — WHY THE THIRD ARM EXISTS. Measured 2026-09-09: 86 of 227
# `_RESEARCH*.md` files sat outside every filter (40 `Personal/`, 39
# `[Author Name] Profile/`, 4 `[YourProject]/`, 3 at the Projects root) — including
# the file holding 6 of the corpus's 10 internal citations and its ONLY
# genuinely-dead one. Without reach, the dead-citation check would have been
# correct and would never have met the problem it was built for.
#
# It is anchored to `*/Projects/*` ON PURPOSE and must stay so. This hook
# receives ABSOLUTE paths, so a bare `*_RESEARCH*.md)` arm would admit research
# files anywhere on disk — other repos, /tmp, ~/.claude itself — which is a reach
# change nobody measured and nobody asked for. Both workspace roots
# (`~/repos/Projects`, `~/Projects`) contain a `/Projects/` segment, so this
# reaches all 227 while refusing everything outside.
#
# COST, stated rather than waved away: this hook runs in the FOREGROUND, bounded
# by LINKCHECK_TOTAL_BUDGET_S (default 20s), so the newly-covered files can now
# pay up to that budget on save. That budget is a NETWORK budget and
# internal-citation resolution is local metadata, so the added cost on an
# internal-only file is negligible; a newly-covered URL-bearing file can pay it.
# The fact-check filter is deliberately NOT widened — it dispatches real AI
# checker rounds, which is a different kind of cost entirely.
case "$FILE_PATH" in
  */Thoughts/*_RESEARCH*.md) ;;
  */Personal/your-project/Docs/*_RESEARCH*.md) ;;
  */Projects/*_RESEARCH*.md) ;;
  *) exit 0 ;;
esac

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
[ -z "$SESSION_ID" ] && exit 0

# D1: no --cycle-id is passed. This arm holds the RESEARCH FILE, which is a
# stronger key than any cycle name it could name, so the worker resolves the
# owning cycle from the file itself (research_linkcheck.py main()).
#
# It used to pass `${CYCLE_ID:-default}` — a variable nothing in the harness
# sets — so every run's findings were filed under `default`, including runs
# that had no `default` cycle at all.
#
# Run synchronously; the worker self-bounds via LINKCHECK_TOTAL_BUDGET_S
# (default 20s).
HOOKS_DIR="$(dirname "$0")"
python3 "$HOOKS_DIR/research_linkcheck.py" \
  "$SESSION_ID" "$FILE_PATH" \
  > "/tmp/research-linkcheck-${SESSION_ID}.log" 2>&1

exit 0

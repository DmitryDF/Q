#!/usr/bin/env bash
# PreToolUse sidecar — bookkeeping-model G6 drop-link snapshot.
# Matchers (settings.json): Write | Edit | Bash.
# Captures each touched slug-family's spine outbound child wikilinks BEFORE the
# write into ~/.claude/state/bookkeeping/snapshot_<session>_<slug>.json, so the
# PostToolUse check (bookkeeping-invariant.sh) can detect an order-dependent
# drop-link (G6). Never blocks — always exits 0.
# Canon: ~/.claude/rules/bookkeeping-model.md §9 (G6).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
case "$TOOL_NAME" in
  Write|Edit|Bash) ;;
  *) exit 0 ;;
esac

printf '%s' "$INPUT" | python3 "${KIT_HOOKS_DIR}/bookkeeping_invariant.py" --snapshot
exit 0

#!/usr/bin/env bash
# PostToolUse hook — bookkeeping-model on-disk-shape enforcement.
# Matchers (settings.json): Write | Edit | Bash.
# Read-only: never patches. Exits 2 with the exact fix on stderr when the
# Thoughts/ slug-family violates the bidirectional contract (G3-G7); exit 0
# otherwise (G0/G1/G2/GA/GO pass; GLC warns). PostToolUse exit-2 surfaces the
# fix back to the model, which pastes it — the next save converges.
# Canon: ~/.claude/rules/bookkeeping-model.md §9 (the eleven G-cases).

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
case "$TOOL_NAME" in
  Write|Edit|Bash) ;;
  *) exit 0 ;;
esac

printf '%s' "$INPUT" | python3 "${KIT_HOOKS_DIR}/bookkeeping_invariant.py"
exit $?

#!/usr/bin/env bash
# PostToolUse Write|Edit — live on-verify claim-harvest trigger (Slice S8, Action A1).
#
# Thin adapter (skill-location.md: standalone check-logic module + thin hook wrapper).
# All logic lives in the vendor-neutral module `_claim_harvest_trigger.py`; this wrapper
# only translates the Claude PostToolUse payload into a module call and surfaces the
# result. NON-BLOCKING by contract: the harvest SURFACES (never decides — U7), and a
# trigger failure must never block the user's Write. Always exit 0.
#
# REGISTERED in settings.json as a PostToolUse Write|Edit hook (activated — the earlier
# "not yet registered" note was stale; corrected 2026-07-09 per refc-conformance-flip A3).
#
# Trigger: PostToolUse, matcher Write|Edit.
# Guard: skips subagent writes (agent_id present) and any non-`*_RESEARCH*.md` path.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
{ [ "$TOOL" = "Write" ] || [ "$TOOL" = "Edit" ]; } || exit 0

# Subagent writes don't drive the main-session Evidence Register.
AGENT=$(printf '%s' "$INPUT" | jq -r '.agent_id // empty' 2>/dev/null)
[ -n "$AGENT" ] && exit 0

FP=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FP" ] && exit 0
case "$FP" in
  *_RESEARCH*.md) : ;;
  *) exit 0 ;;
esac

# Delegate to the standalone module. Paraphrased claims are surfaced for
# operator-confirm, NOT auto-harvested (no --confirm-paraphrased here).
OUT=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/_claim_harvest_trigger.py" "$FP" 2>&1)
STATUS=$?

# Surface only a substantive result; stay silent on plain skips to avoid noise.
if [ $STATUS -eq 0 ] && [ -n "$OUT" ]; then
  case "$OUT" in
    *"skipped:"*) : ;;            # no-op skips (not a research file / no PASS / dedup) — quiet
    *) printf '%s\n' "$OUT" ;;    # harvested / awaiting-confirm — surface to operator
  esac
fi

exit 0

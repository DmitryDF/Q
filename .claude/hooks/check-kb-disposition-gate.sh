#!/usr/bin/env bash
# Project-agnostic plan-gate checker: business-rule disposition + editorial-hypotheses ledger.
# Wired alongside global plan-gate hooks under PermissionRequest.ExitPlanMode + Stop.
# Reads the project's CLAUDE.md for KL + ledger declarations. No-ops cleanly when the project
# has not opted in (declarations absent) or when the session has no active plan.
#
# Exit codes:
#   0 = pass (or no-op)
#   2 = deny (stderr messages fed back to Claude)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

# Each hook in a chain receives stdin independently — re-parse.
INPUT=$(cat)

# Engage only on ExitPlanMode (PermissionRequest) or any Stop event (no tool_name).
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
if [ -n "$TOOL_NAME" ] && [ "$TOOL_NAME" != "ExitPlanMode" ]; then
  exit 0
fi

SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAN_FILE=$("$SCRIPT_DIR/find-session-plan.sh" "$SESSION_ID") || exit 0
[ ! -f "$PLAN_FILE" ] && exit 0

# Locate project root. Claude Code exports CLAUDE_PROJECT_DIR; fall back to $PWD.
PROJECT_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
CLAUDE_MD="$PROJECT_ROOT/CLAUDE.md"
[ ! -f "$CLAUDE_MD" ] && exit 0

# Parse declarations (simple "key: value" format, one per line, anchored to line start).
KL_INDEX=$(grep -E '^knowledge_library_index:' "$CLAUDE_MD" | head -1 \
  | sed 's/^[^:]*:[[:space:]]*//' | tr -d '"')
LEDGER=$(grep -E '^hypotheses_ledger:' "$CLAUDE_MD" | head -1 \
  | sed 's/^[^:]*:[[:space:]]*//' | tr -d '"')

# No opt-in → silent no-op.
[ -z "$KL_INDEX" ] && [ -z "$LEDGER" ] && exit 0

LEDGER_ABS=""
[ -n "$LEDGER" ] && LEDGER_ABS="$PROJECT_ROOT/$LEDGER"

MISSING=()

# Extract Gate 1 table rows between GATE1:START and GATE1:END.
# Anchor markers to start-of-line — marker text appears in pseudocode and in
# backtick-quoted snippets in plan prose; only lines whose whole content is the
# marker should open/close the range.
G1_ROWS=$(sed -n '/^<!-- GATE1:START -->$/,/^<!-- GATE1:END -->$/p' "$PLAN_FILE" \
  | grep '^|' | grep -v '^| Claim' | grep -v '^|---')

HAS_RULES=0
# awk splits on every '|' — including escaped '\|' inside backtick-quoted
# snippets in the Source/Verified columns. Indexing from the left would land
# on the wrong column for any row that quotes a regex. Index from the right:
# the trailing '|' leaves $NF empty; $(NF-1) is Hypothesis ID; $(NF-2) is
# Disposition; $(NF-3) is Status. These three columns hold simple values
# with no internal pipes, so NF-based indexing is stable.
while IFS= read -r ROW; do
  [ -z "$ROW" ] && continue
  DISP=$(echo "$ROW" | awk -F'|' '{print $(NF-2)}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  HID=$(echo  "$ROW" | awk -F'|' '{print $(NF-1)}' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
  case "$DISP" in
    n/a)
      ;;
    expert-sourced|process-sourced)
      HAS_RULES=1
      ;;
    editorial-user)
      HAS_RULES=1
      if [ -z "$LEDGER_ABS" ] || [ ! -f "$LEDGER_ABS" ]; then
        MISSING+=("row tagged editorial-user but project declares no hypotheses_ledger (or file missing): $ROW")
        continue
      fi
      if ! echo "$HID" | grep -qE '^H-[0-9]{4}$'; then
        MISSING+=("editorial-user row missing H-NNNN id (got '$HID'): $ROW")
        continue
      fi
      if ! grep -qE "^\| *$HID *\|" "$LEDGER_ABS"; then
        MISSING+=("$HID not found in ledger $LEDGER")
      fi
      if ! grep -qE "<!-- GATE1T:USER_APPROVED:$HID -->" "$PLAN_FILE"; then
        MISSING+=("$HID has no <!-- GATE1T:USER_APPROVED:$HID --> marker in plan")
      fi
      ;;
    "")
      MISSING+=("row missing Disposition column: $ROW")
      ;;
    *)
      MISSING+=("invalid disposition '$DISP' (expected: expert-sourced|process-sourced|editorial-user|n/a): $ROW")
      ;;
  esac
done <<< "$G1_ROWS"

if [ $HAS_RULES -eq 1 ] && ! grep -q '<!-- GATE1T:CHECKER_PASS -->' "$PLAN_FILE"; then
  MISSING+=("rule-bearing Gate 1 rows present but no <!-- GATE1T:CHECKER_PASS --> marker")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
  for m in "${MISSING[@]}"; do echo "✗ Gate 1T: $m" >&2; done
  exit 2
fi
exit 0

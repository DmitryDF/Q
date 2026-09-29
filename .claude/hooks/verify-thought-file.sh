#!/bin/bash
# PostToolUse hook — code-triggered structural verification of *_THOUGHT.md files.
# Fires after Write/Edit tool calls. Path-filters inside script (matcher = Write|Edit).
#
# Architecture: code triggers verification; producer AI cannot influence what this checks.
# Producer-never-verifies-own-output doctrine (code_first_architecture.md:112).
#
# PostToolUse CANNOT block (tool already ran). The sidecar this writes is ADVISORY early
# feedback for the author: permission-plan-gate.sh does NOT read it — at ExitPlanMode
# the gate runs _validate-thought-file.py live on the spine the plan names, so a
# spine written outside Write/Edit (the /clarification-v2 door's Python write) is
# validated at approval exactly like one saved here.
# Exit codes:
#   0 = normal (sidecar written PASS or FAIL, or file not applicable)
#   1 = fail-open (hook error; sidecar marked UNVERIFIED if possible)

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
if [ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ]; then
  exit 0
fi

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Only process *_THOUGHT.md files
case "$FILE_PATH" in
  *_THOUGHT.md) ;;
  *) exit 0 ;;
esac

# Resolve to absolute path if relative
if [[ "$FILE_PATH" != /* ]]; then
  FILE_PATH="$(pwd)/$FILE_PATH"
fi

# File must exist (skip if recently deleted or path is wrong)
[ -f "$FILE_PATH" ] || exit 0

# Derive the topic slug from the filename (the sidecar sits beside the spine and
# links back to it via the `Parent:` line — bookkeeping G3).
FILENAME=$(basename "$FILE_PATH")
TOPIC_SLUG="${FILENAME%_THOUGHT.md}"
THOUGHTS_DIR=$(dirname "$FILE_PATH")

SIDECAR="${THOUGHTS_DIR}/${TOPIC_SLUG}_THOUGHT_check.md"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# Run structural validator — one positional (the validator derives its TODO search
# dir from the spine path itself; the former slug arguments served only the
# retired classification check).
VALIDATOR_OUT=$(python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/_validate-thought-file.py" \
  "$FILE_PATH" 2>&1)
VALIDATOR_EXIT=$?

if [ "$VALIDATOR_EXIT" -eq 0 ]; then
  VERDICT="PASS"
elif [ "$VALIDATOR_EXIT" -eq 1 ]; then
  VERDICT="FAIL"
else
  # Validator error — write UNVERIFIED sidecar
  {
    echo "---"
    echo "verdict: UNVERIFIED"
    echo "checked: ${TIMESTAMP}"
    echo "file: ${FILE_PATH}"
    echo "reason: Validator script error (exit ${VALIDATOR_EXIT})"
    echo "---"
    echo ""
    echo "Parent: [[${TOPIC_SLUG}_THOUGHT]]"
    echo "VERDICT: UNVERIFIED"
    echo "CHECKED: ${TIMESTAMP}"
    echo "FILE: ${FILE_PATH}"
    echo ""
    echo "Validator failed. Manual verification required."
  } > "$SIDECAR"
  exit 1
fi

# Write sidecar with frontmatter + validator output
{
  echo "---"
  echo "verdict: ${VERDICT}"
  echo "checked: ${TIMESTAMP}"
  echo "file: ${FILE_PATH}"
  echo "---"
  echo ""
  echo "Parent: [[${TOPIC_SLUG}_THOUGHT]]"
  echo "$VALIDATOR_OUT"
} > "$SIDECAR"

exit 0

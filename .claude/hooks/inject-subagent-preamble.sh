#!/bin/bash
# PreToolUse hook: inject tool-preference rules into every Agent subagent prompt.
# Subagents run in isolated contexts and don't inherit ~/.claude/rules/ files.
# Primary: prepend compact rules excerpt via updatedInput.prompt.
# Fallback (partial): if updatedInput is unsupported for Agent, scan the prompt
# for explicit forbidden Bash patterns and block on match.
# NOTE: fallback only catches patterns present in the task description itself;
# it does NOT prevent the subagent from independently using forbidden patterns.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Agent" ] && exit 0

PROMPT=$(echo "$INPUT" | jq -r '.tool_input.prompt // empty')
[ -z "$PROMPT" ] && exit 0

# Idempotency: skip if preamble already injected
if printf '%s' "$PROMPT" | grep -qF '[SUBAGENT_TOOL_RULES]'; then
  exit 0
fi

# Fallback scan: block prompts that explicitly contain forbidden Bash patterns.
# Mirrors sanitize-bash.sh logic; provides partial protection if updatedInput
# for Agent is unsupported by the runtime.
if printf '%s' "$PROMPT" | grep -qE 'python3?"?\s+-c\s'; then
  if printf '%s' "$PROMPT" | perl -0777 -ne 'exit 0 if /\n/; exit 1'; then
    echo "BLOCKED (subagent prompt): python3 -c with multi-line content. Use a .py file." >&2
    exit 2
  fi
fi
if printf '%s' "$PROMPT" | grep -qE 'cat[[:space:]]+>'; then
  echo "BLOCKED (subagent prompt): cat > write-redirect. Use the Write tool." >&2
  exit 2
fi
if printf '%s' "$PROMPT" | grep -qF '$('; then
  if ! printf '%s' "$PROMPT" | grep -qF '$(cat <<'; then
    echo "BLOCKED (subagent prompt): \$() command substitution. Use native tools or a script." >&2
    exit 2
  fi
fi

# A3 cycle_id propagation (Slice S3 of research-scope-and-focus).
# Read parent CYCLE_ID with the 'default' fallback — locked Discovery classifies
# autonomous /research as user-invocation = approval; the fallback makes that
# deterministic instead of undefined. Env inheritance from the harness carries
# CYCLE_ID into the spawned subagent process tree; the side-channel state file
# below is a documentary record of the last-seen cycle_id per session for audit
# + downstream-hook fallback when env doesn't propagate.
PARENT_CYCLE_ID="${CYCLE_ID:-default}"
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
if [ -n "$SESSION_ID" ]; then
  CYCLE_STATE_DIR="$HOME/.claude/state/research_pipeline"
  mkdir -p "$CYCLE_STATE_DIR" 2>/dev/null || true
  printf '%s\n' "$PARENT_CYCLE_ID" > "$CYCLE_STATE_DIR/active_cycle_${SESSION_ID}.txt" 2>/dev/null || true
fi

PREAMBLE="[SUBAGENT_TOOL_RULES] (cycle_id=${PARENT_CYCLE_ID}) File I/O rules — follow exactly:
- Read files: ALWAYS use the Read tool — NEVER cat, head, tail, sed, awk via Bash.
- Write files: ALWAYS use the Write tool — NEVER redirects (>), heredocs (<< EOF), echo > via Bash.
- Search content: ALWAYS use the Grep tool — NEVER grep, rg via Bash.
- Search files: ALWAYS use the Glob tool — NEVER find, ls via Bash.
- Multi-line scripts: NEVER write for/while/if/do/done/fi shell scripts in Bash — write a .sh file then run it.
- Python: NEVER use python3 -c with newlines or python << heredoc — write a .py file then run it."

NEW_PROMPT="${PREAMBLE}

${PROMPT}"

echo "inject-subagent-preamble: cycle_id=${PARENT_CYCLE_ID} session=${SESSION_ID:-<none>} — injecting tool rules into Agent prompt" >&2

# Preserve all original tool_input fields; only replace prompt.
# Using full tool_input merge prevents losing required fields like description.
echo "$INPUT" | jq --arg prompt "$NEW_PROMPT" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    updatedInput: (.tool_input + {prompt: $prompt})
  }
}'
exit 0

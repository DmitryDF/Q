#!/bin/bash
# PreToolUse hook for Bash: rewrites commands to avoid sandbox heuristics.
#
# Transformation 1 — backslash-escaped paths → double-quoted paths
#   The project lives in iCloud (Mobile Documents) which has a space in the path.
#   Backslash-escaping (path\ with\ spaces) triggers "Contains backslash-escaped
#   whitespace". Double-quoted paths don't.
#
# Transformation 2 — env-var prefix before python3 aggregate-kl-extraction → inlined paths
#   Commands like CHAPTERS="...[YOUR_VAULT_ID]/..." python3 ... aggregate-kl-extraction
#   trigger "Tilde in assignment value" because the iCloud path contains ~ characters.
#   The fix-envvar-prefix.py helper inlines the variable values and drops the prefix,
#   so the command starts with python3 and matches the existing Bash(python3:*) allowlist.
#
# Both transformations emit updatedInput JSON from this single hook — only one hook may
# output updatedInput at a time (sanitize-bash.sh uses exit 2, not JSON, for this reason).
#
# Exit codes:
#   0 = always (either rewrites or passes through)

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Bash" ] && exit 0

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$COMMAND" ] && exit 0

FIXED="$COMMAND"

# Transformation 1: backslash-escaped paths → double-quoted paths
if printf '%s' "$FIXED" | grep -q '\\  *' || printf '%s' "$FIXED" | od -c | grep -q '\\\\   '; then
  FIXED=$(printf '%s' "$FIXED" | perl -pe 's{(/(?:\\ |[^\s])+)}{ my $p=$1; if ($p =~ /\\ /) { $p =~ s/\\ / /g; chr(34).$p.chr(34) } else { $p } }ge')
fi

# Transformation 2: env-var prefix before python3 aggregate-kl-extraction
# Triggers "Tilde in assignment value" when [YOUR_VAULT_ID] appears in an assigned value.
if printf '%s' "$FIXED" | grep -qE '^[A-Z_]+="[^"]*iCloud~'; then
  FIXED=$(printf '%s' "$FIXED" | python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/fix-envvar-prefix.py")
fi

# Transformation 3: bash brace expansion {a,b,c} in unquoted command regions
# Triggers "Brace expansion" sandbox heuristic. Pre-expand to explicit arguments.
if printf '%s' "$FIXED" | grep -qE '\{[^{}]*,[^{}]*\}'; then
  FIXED=$(printf '%s' "$FIXED" | python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/fix-brace-expansion.py")
fi

# If nothing changed, pass through
if [ "$FIXED" = "$COMMAND" ]; then
  exit 0
fi

# Return the rewritten command via updatedInput
jq -n --arg cmd "$FIXED" '{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    updatedInput: {
      command: $cmd
    }
  }
}'
exit 0

#!/bin/bash
# PreToolUse: Read — enforce freshness of prompt-engineering.md.
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Read" ] && exit 0

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
# PE_FRESHNESS_FILE override exists for the test harness only; production always
# resolves the live path (mirrors the VERIFIER_ISOLATION_HOME precedent).
PE_FILE="${PE_FRESHNESS_FILE:-$HOME/.claude/rules/prompt-engineering.md}"

# Fast path: only act on reads of the PE file
[ "$FILE_PATH" != "$PE_FILE" ] && exit 0

MAX_AGE_DAYS=30

# Remediation steps. Printed on BOTH the missing-file and the stale path.
# Previously these printed only on the missing-file branch, while the stale
# branch said "use the 5-step process above" — text that was never emitted on
# that branch, since the missing-file block exits before the staleness check.
# The stale branch is the one that realistically fires.
print_steps() {
    echo "Re-synthesize, then re-read:" >&2
    echo "  1. WebFetch https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices" >&2
    echo "  2. WebFetch https://platform.claude.com/docs/en/build-with-claude/effort" >&2
    echo "  3. WebFetch the per-model pages linked from step 1 — currently:" >&2
    echo "       .../prompt-engineering/prompting-claude-opus-5" >&2
    echo "       .../prompt-engineering/prompting-claude-sonnet-5" >&2
    echo "       .../prompt-engineering/prompting-claude-fable-5" >&2
    echo "       .../prompt-engineering/prompting-claude-opus-4-8" >&2
    echo "     Read step 1 first — the per-model page set changes as models ship." >&2
    echo "  4. Rewrite ~/.claude/rules/prompt-engineering.md against those pages." >&2
    echo "  5. Update its 'Fetched: YYYY-MM-DD' line to today. That line — NOT the" >&2
    echo "     file mtime — is what this gate reads." >&2
    echo "  6. Ship it: ~/.claude/bin/claude-promote" >&2
    echo "  7. Re-read the file." >&2
}

# Check existence
if [ ! -f "$PE_FILE" ]; then
    echo "✗ ~/.claude/rules/prompt-engineering.md does not exist (first run)." >&2
    print_steps
    exit 2
fi

NOW=$(date +%s)

# Freshness basis: the declared 'Fetched:' line, falling back to file mtime.
#
# mtime is not a freshness signal. Any write resets it — including a
# the deploy step that re-deploys the file, or an unrelated promotion that
# sweeps it up — so mtime can read "fresh" when not one source has been
# re-verified in months. The 'Fetched:' line is the honest claim, because a
# human or agent has to deliberately edit it.
FETCHED=$(grep -m1 -E '^Fetched:[[:space:]]*[0-9]{4}-[0-9]{2}-[0-9]{2}' "$PE_FILE" 2>/dev/null \
          | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}' | head -1)

REF=""
if [ -n "$FETCHED" ]; then
    REF=$(date -j -f "%Y-%m-%d" "$FETCHED" "+%s" 2>/dev/null \
          || date -d "$FETCHED" "+%s" 2>/dev/null)
    BASIS="declared 'Fetched: ${FETCHED}'"
fi

if [ -z "$REF" ]; then
    REF=$(stat -f %m "$PE_FILE" 2>/dev/null || stat -c %Y "$PE_FILE" 2>/dev/null)
    BASIS="file mtime — no parseable 'Fetched: YYYY-MM-DD' line found; add one"
fi

# A missing/unreadable reference must not silently pass the gate.
if [ -z "$REF" ]; then
    echo "✗ ~/.claude/rules/prompt-engineering.md: cannot determine age." >&2
    echo "  No parseable 'Fetched: YYYY-MM-DD' line and mtime unavailable." >&2
    print_steps
    exit 2
fi

AGE=$(( NOW - REF ))
MAX_AGE=$(( MAX_AGE_DAYS * 86400 ))
if [ "$AGE" -gt "$MAX_AGE" ]; then
    DAYS=$(( AGE / 86400 ))
    echo "✗ ~/.claude/rules/prompt-engineering.md is stale (${DAYS}d; max ${MAX_AGE_DAYS}d)." >&2
    echo "  Basis: ${BASIS}." >&2
    print_steps
    exit 2
fi
exit 0

#!/usr/bin/env bash
# PreToolUse hook: real-time guard on Edit|Write to Projects/CLAUDE.md.
#
# Spec: ~/.claude/plans/lazy-doodling-wadler.md Coherent Action A4 guard rail,
# anchored in the locked Discovery "Deletion sequencing (load-bearing): delete
# only in the same commit as registering the matchers, or in a strictly-later
# commit gated on the matchers being live. Never the other order."
# (Thoughts/research-scope-and-focus_THOUGHT.md, ## Desired Solution).
#
# The hook blocks any edit/write touching the Projects-level CLAUDE.md unless
# the front-end research-scope gate is BOTH registered AND demonstrably live:
#
#   1. research-scope-gate.sh must appear in ~/.claude/settings.json.
#   2. A canonical smoke payload piped into research-scope-gate.sh under a
#      synthetic SESSION_ID with no manifest entry must return exit 2.
#
# Smoke-payload note (intent vs literal): the plan text names a "canonical
# Bash smoke payload" (echo test). However, research-scope-gate.sh only fires
# on spine-scoped Bash patterns (`python.*(patchright|playwright|chromium|headless)`
# or write-redirect to *_RESEARCH*.md) — plain `echo test` fast-exits 0 by
# design. To honor the plan's INTENT (verify the gate blocks when no manifest
# exists) we use a WebSearch payload, which is unconditionally gated when a
# SESSION_ID is present without an r1_scope_approved manifest entry.
#
# Contract:
#   stdin    JSON PreToolUse payload — .tool_name, .tool_input.file_path
#   exit 0   not an Edit|Write to Projects/CLAUDE.md, or all guards pass
#   exit 2   blocked — research-scope-gate.sh missing or smoke failed
#
# Exit code from research-scope-gate.sh other than 2 (including 0 or 1)
# blocks deletion with a distinct error "smoke returned unexpected exit <N>".

INPUT=$(cat)

# Malformed JSON stdin: emit distinct error (do not silently pass-through —
# the hook is a safety guard; a payload it cannot parse is suspect).
if ! echo "$INPUT" | jq -e . >/dev/null 2>&1; then
  cat >&2 <<EOF
✗ pre-delete-claude-rule: malformed JSON on stdin; refusing to evaluate the gate.
This hook protects Projects/CLAUDE.md deletion — a payload it cannot parse is
treated as a structural anomaly. Investigate the caller.
EOF
  exit 2
fi

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
case "$TOOL_NAME" in
  Edit|Write) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$FILE_PATH" ] && exit 0

# Only fire on Projects/CLAUDE.md (the project-level rule file targeted by
# A4(e)). User-global ~/.claude/CLAUDE.md is explicitly NOT in scope per the
# locked Discovery ("target: Projects-level, NOT user-global").
case "$FILE_PATH" in
  */Projects/CLAUDE.md|*/Documents/Projects/CLAUDE.md) ;;
  *) exit 0 ;;
esac

SETTINGS="$HOME/.claude/settings.json"
GATE="${KIT_HOOKS_DIR}/research-scope-gate.sh"

if [ ! -f "$SETTINGS" ]; then
  cat >&2 <<EOF
✗ pre-delete-claude-rule: ~/.claude/settings.json not found.
Refusing to edit Projects/CLAUDE.md — front-end research-scope gate cannot be verified live.
EOF
  exit 2
fi

if ! grep -q "research-scope-gate.sh" "$SETTINGS"; then
  cat >&2 <<EOF
✗ pre-delete-claude-rule: research-scope-gate.sh is NOT registered in $SETTINGS.

The Projects/CLAUDE.md prose research-routing rule (lines 22-27) is the prose
duplicate of the front-end matchers. It may not be deleted until those matchers
are live (deletion-sequencing guard rail, locked in spine).

Register research-scope-gate.sh as a PreToolUse hook for WebSearch, WebFetch,
Agent, mcp__claude-in-chrome__.*, and Bash before retrying.
EOF
  exit 2
fi

if [ ! -x "$GATE" ] && [ ! -f "$GATE" ]; then
  cat >&2 <<EOF
✗ pre-delete-claude-rule: gate script $GATE is missing on disk.
Refusing to edit Projects/CLAUDE.md.
EOF
  exit 2
fi

# Smoke: feed a WebSearch payload with a synthetic SESSION_ID. To exercise
# the blocking path (not the topic-orient exemption), the gate's classifier
# must see this SID as "research" — which it does when an RP-<SID>.json file
# exists at the default research_pipeline state dir. We stage a synthetic
# manifest there (no r1_scope_approved on any cycle), run the gate, then
# remove the manifest. trap-on-EXIT ensures cleanup even on failure.
SMOKE_SID="presmoke-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SMOKE_PAYLOAD=$(printf '{"tool_name":"WebSearch","tool_input":{"query":"smoke"},"session_id":"%s"}' "$SMOKE_SID")

RP_DEFAULT_DIR="$HOME/.claude/state/research_pipeline"
SMOKE_MANIFEST="$RP_DEFAULT_DIR/RP-${SMOKE_SID}.json"

mkdir -p "$RP_DEFAULT_DIR" 2>/dev/null
cleanup_smoke() { rm -f "$SMOKE_MANIFEST" 2>/dev/null; }
trap cleanup_smoke EXIT INT TERM

# Minimal synthetic manifest: schema_version 2, one cycle, no r1_scope_approved.
cat >"$SMOKE_MANIFEST" 2>/dev/null <<MANIFEST
{
  "session_id": "${SMOKE_SID}",
  "schema_version": "2.0",
  "bypass": false,
  "cycles": {
    "default": {
      "r1_scope_approved": false,
      "checkpoints": {}
    }
  }
}
MANIFEST

if [ ! -f "$SMOKE_MANIFEST" ]; then
  cat >&2 <<EOF
✗ pre-delete-claude-rule: failed to stage smoke manifest at $SMOKE_MANIFEST.
Refusing to edit Projects/CLAUDE.md.
EOF
  exit 2
fi

SMOKE_EXIT=0
printf '%s' "$SMOKE_PAYLOAD" | bash "$GATE" >/dev/null 2>&1 || SMOKE_EXIT=$?
cleanup_smoke
trap - EXIT INT TERM

if [ "$SMOKE_EXIT" -ne 2 ]; then
  cat >&2 <<EOF
✗ pre-delete-claude-rule: smoke returned unexpected exit ${SMOKE_EXIT}: check hook logic before deleting CLAUDE.md.

Expected: research-scope-gate.sh returns exit 2 when piped a gated-tool
payload under a SESSION_ID with no RP-<SID>.json manifest entry.
Observed: exit ${SMOKE_EXIT}.

Possible causes:
  - research-scope-gate.sh logic changed (no longer blocks on absent manifest)
  - jq / python3 unavailable in the hook's runtime
  - topic-orient is returning an exempting intake_source_class for the smoke SID

Investigate the gate before retrying — the front-end enforcement contract is
the prerequisite for retiring the prose rule.
EOF
  exit 2
fi

exit 0

#!/bin/bash
# PostToolUse hook: records which plan files each session writes to.
# Enables per-session cleanup on SessionEnd.
#
# Trigger: PostToolUse on Write|Edit
# Side effect: appends to ~/.claude/plans/.manifest.json

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only track Write and Edit
if [ "$TOOL_NAME" != "Write" ] && [ "$TOOL_NAME" != "Edit" ]; then
  exit 0
fi

# Skip subagent writes — they don't belong in the main session's manifest.
# Guard moved to TOP (was after path check). Preserves current subagent-skip
# behavior for BOTH legacy harness path AND new project-side path.
SESSION_ID=$(echo "$INPUT" | jq -r '.session_id // empty')
AGENT_ID=$(echo "$INPUT" | jq -r '.agent_id // empty')
[ -n "$AGENT_ID" ] && exit 0

PLANS_DIR="$HOME/.claude/plans"
TARGET_FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
[ -z "$TARGET_FILE" ] && exit 0

# Arm 1: legacy harness path (fast path; no Python fork).
# Arm 2: project-side path matching */Thoughts/*_PLAN.md — verify exact path
# via topic state (Python fork only on this structural shape, off the hot path
# of "user editing source code"). Catchall: not a plan file, exit silent.
case "$TARGET_FILE" in
  "$PLANS_DIR/"*.md)
    ;;  # fall through to manifest append after esac
  */Thoughts/*_PLAN.md)
    # Mode-C ninja-plan (DS5a): no bound topic to exact-match against. Recognize
    # it by `bookkeeping: mode-c` frontmatter — on disk (PostToolUse: the write
    # already landed) or in the tool payload — and record it in the manifest so
    # cleanup tracks (but never deletes — Thoughts/ plans are durable) it. This
    # is the "manifest coupling" fix: extend the WRITER, not the reader.
    PLAN_CONTENT=$(echo "$INPUT" | jq -r '.tool_input.content // .tool_input.new_string // empty')
    if { [ -f "$TARGET_FILE" ] && grep -qE '^bookkeeping:[[:space:]]*mode-c[[:space:]]*$' "$TARGET_FILE" 2>/dev/null; } \
       || printf '%s' "$PLAN_CONTENT" | grep -qE '^bookkeeping:[[:space:]]*mode-c[[:space:]]*$'; then
      :  # Mode-C: fall through to manifest append
    else
      [ -z "$SESSION_ID" ] && exit 0
      TOPIC_STATE="$(python3 "${KIT_HOOKS_DIR}/pre_plan_gates.py" read "$SESSION_ID" 2>/dev/null)"
      [ -z "$TOPIC_STATE" ] && exit 0
      # `read` emits topic_state.{project_root,topic_slug} (not legacy
      # topic_classification.*); track any topic-slug-based plan basename,
      # including scope-keyed slice plans (<slug>-<ts>_S<N>_PLAN.md).
      PROJECT_ROOT=$(echo "$TOPIC_STATE" | jq -r '.topic_state.project_root // empty')
      TOPIC_SLUG=$(echo "$TOPIC_STATE" | jq -r '.topic_state.topic_slug // empty')
      [ -z "$PROJECT_ROOT" ] && exit 0
      [ -z "$TOPIC_SLUG" ] && exit 0
      case "$TARGET_FILE" in
        "${PROJECT_ROOT}/Thoughts/${TOPIC_SLUG}"*_PLAN.md) : ;;  # match → track
        *)
          echo "WARN: plan path mismatch — expected ${PROJECT_ROOT}/Thoughts/${TOPIC_SLUG}*_PLAN.md, got $TARGET_FILE. Not tracked." >&2
          exit 0
          ;;
      esac
    fi
    ;;  # fall through to manifest append
  *)
    exit 0
    ;;
esac

[ -z "$SESSION_ID" ] && exit 0

MANIFEST="$PLANS_DIR/.manifest.json"

# Create manifest if missing
[ ! -f "$MANIFEST" ] && echo '{}' > "$MANIFEST"

# Add file to session's list (atomic: read, merge, write).
# Delegated to a standalone Python helper because the shell `flock` command is
# absent on macOS — it silently no-op'd this append harness-wide (`flock ... ||
# exit 0`). The helper uses fcntl.flock (macOS-native) + an atomic os.replace
# write and preserves the exact {sid: [paths]} shape find-session-plan.sh reads.
# `|| true` keeps the PostToolUse contract (always exit 0) without re-hiding a
# genuine error the way the old silent subshell did.
python3 "${KIT_HOOKS_DIR}/_plan_manifest.py" add "$SESSION_ID" "$TARGET_FILE" || true

exit 0

#!/usr/bin/env bash
# PreToolUse/PostToolUse Bash guard — Verification Isolation guard (b).
# Snapshots the live ~/.claude namespace (sha of settings.json + hooks/* folded
# with `git -C ~/.claude status --porcelain`) around installer- or
# namespace-creating Bash commands, and hard-fails on drift. Bridge-agnostic:
# keys on any drift, not on a propagation mechanism.
#
# Usage (registered twice in settings.json):
#   check-verifier-isolation.sh pre    # PreToolUse  matcher Bash
#   check-verifier-isolation.sh post   # PostToolUse matcher Bash
# Exit: 0 allow; 2 block (drift detected, or stale-armed drift on a later pre).

MODE="$1"
[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

HOME_DIR="${VERIFIER_ISOLATION_HOME:-$HOME/.claude}"
STATE_DIR="$HOME_DIR/state/verifier-isolation"

INPUT=$(cat)
TOOL_NAME=$(printf '%s' "$INPUT" | jq -r '.tool_name // empty' 2>/dev/null)
[ "$TOOL_NAME" != "Bash" ] && exit 0
SESSION_ID=$(printf '%s' "$INPUT" | jq -r '.session_id // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && SESSION_ID=nosession
COMMAND=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

SNAP="$STATE_DIR/$SESSION_ID.snap"
ARMED="$STATE_DIR/$SESSION_ID.armed"

if command -v shasum >/dev/null 2>&1; then SHA=(shasum -a 256); else SHA=(sha256sum); fi

compute_snapshot() {
  # bookkeeping-model drift Row 2 (DS8 #12): widen beyond settings.json + hooks/*
  # to close the documented scope hole — also cover rules/*, skills/**/SKILL.md,
  # agents/*, and state/**/*.json. The guard only ARMS on is_dangerous
  # (installer-class) commands, where drift in any of these namespaces is the
  # 2026-06-09 corruption pattern. (state/ is runtime data, but installers should
  # not mutate it during a verification step — drift there is still suspect.)
  {
    [ -f "$HOME_DIR/settings.json" ] && "${SHA[@]}" "$HOME_DIR/settings.json"
    [ -d "$HOME_DIR/hooks" ] && find "$HOME_DIR/hooks" -type f -exec "${SHA[@]}" {} + 2>/dev/null | LC_ALL=C sort
    [ -d "$HOME_DIR/rules" ] && find "$HOME_DIR/rules" -type f -exec "${SHA[@]}" {} + 2>/dev/null | LC_ALL=C sort
    [ -d "$HOME_DIR/skills" ] && find "$HOME_DIR/skills" -name 'SKILL.md' -type f -exec "${SHA[@]}" {} + 2>/dev/null | LC_ALL=C sort
    [ -d "$HOME_DIR/agents" ] && find "$HOME_DIR/agents" -type f -exec "${SHA[@]}" {} + 2>/dev/null | LC_ALL=C sort
    [ -d "$HOME_DIR/state" ] && find "$HOME_DIR/state" -name '*.json' -type f -exec "${SHA[@]}" {} + 2>/dev/null | LC_ALL=C sort
    git -C "$HOME_DIR" status --porcelain -- settings.json hooks rules skills agents state 2>/dev/null
  } | "${SHA[@]}" | awk '{print $1}'
}

is_dangerous() {
  printf '%s' "$1" | grep -Eq 'setup\.sh|install\.(py|sh)|bootstrap|build_kit\.py\s+build' && return 0
  printf '%s' "$1" | grep -Eq '\b(mkdir|cp|rsync|install|tar|unzip)\b[^|;&]*(~|\$HOME|/Users/[^/]+|/home/[^/]+)/\.claude' && return 0
  return 1
}

drift_message() {  # $1 = command that ran
  cat >&2 <<EOF
BLOCKED: config drift detected by verifier-isolation guard.
A step mutated the live config namespace $HOME_DIR — exactly the
2026-06-09 corruption pattern (see incident-claude-config-path-corruption).
  triggering command: $1
Remediate now (do NOT wait for /close):
  1. git -C "$HOME_DIR" status --porcelain          # what changed
  2. git -C "$HOME_DIR" diff -- settings.json hooks  # inspect the drift
  3. Restore from git or backups/ if the change was unintended.
EOF
}

case "$MODE" in
  pre)
    if [ -f "$ARMED" ] && [ -f "$SNAP" ]; then   # stale arm: a prior armed call's post never fired
      if [ "$(compute_snapshot)" != "$(cat "$SNAP")" ]; then
        drift_message "$(cat "$ARMED" 2>/dev/null)"; exit 2
      fi
      rm -f "$ARMED" "$SNAP"
    elif [ -f "$ARMED" ]; then                   # orphaned arm, no baseline → clear, don't false-block
      rm -f "$ARMED"
    fi
    if is_dangerous "$COMMAND"; then
      mkdir -p "$STATE_DIR"
      compute_snapshot > "$SNAP"
      printf '%s' "$COMMAND" > "$ARMED"
    fi
    exit 0 ;;
  post)
    [ -f "$ARMED" ] || exit 0
    CUR=$(compute_snapshot); BASE=$(cat "$SNAP" 2>/dev/null); CMD=$(cat "$ARMED" 2>/dev/null)
    rm -f "$ARMED" "$SNAP"
    [ "$CUR" != "$BASE" ] && { drift_message "$CMD"; exit 2; }
    exit 0 ;;
  *) exit 0 ;;
esac

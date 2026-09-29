#!/usr/bin/env bash
# guard-config-paths.sh — PreToolUse Bash guard (A5, readonly-skills-structural-safety).
#
# Best-effort static backstop: blocks the common destructive shapes whose resolved
# target is a safety-critical ~/.claude config path (hooks/agents/rules/skills/
# settings.json — NOT logs/cache/state/projects). The decision + Python path
# resolution live in the standalone module config_path_guard.py; this wrapper is
# the thin per-vendor adapter.
#
# Fail-open WITH WARNING: `set -euo pipefail` + `trap 'exit 0' ERR` routes every
# UNEXPECTED error (missing util, unreadable module, pipe failure) to a fail-open
# exit 0 + a stderr warning — a broken guard never bricks Bash, yet is never
# silently disabled. The module ALWAYS exits 0 and reports its verdict on stdout
# (BLOCK / ALLOW), so the ONE intentional non-zero exit (exit 2 on a confirmed
# violation) is an explicit exit that the ERR trap does not intercept.
set -euo pipefail
trap 'printf "%s\n" "[guard-config-paths] warning: internal guard error — failing open (Bash allowed)" >&2; exit 0' ERR

HOOK_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks"
INPUT="$(cat)"
OUT="$(printf '%s' "$INPUT" | python3 "$HOOK_DIR/config_path_guard.py")"
FIRST="$(printf '%s\n' "$OUT" | head -n1)"
if [ "$FIRST" = "BLOCK" ]; then
  printf '%s\n' "$OUT" | tail -n +2 >&2
  exit 2
fi
exit 0

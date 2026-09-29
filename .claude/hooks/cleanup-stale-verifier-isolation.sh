#!/usr/bin/env bash
# SessionStart hook — removes stale verifier-isolation arm state.
#
# When a Bash command armed by check-verifier-isolation.sh crashes mid-flight,
# PostToolUse never fires and the .armed/.snap pair persist on disk. The
# stale-arm branch of the guard auto-clears only when the recomputed snapshot
# equals the saved baseline — any drift in settings.json/hooks/* (including
# ephemeral, since-cleaned drift) keeps the guard tripping forever.
#
# This hook runs on session start and removes .armed/.snap files older than
# 6 hours (360 minutes — typical work-session length; older = definitely stale).
# Younger files belong to currently-active or recently-active sessions and are
# preserved.

HOME_DIR="${VERIFIER_ISOLATION_HOME:-$HOME/.claude}"
STATE_DIR="$HOME_DIR/state/verifier-isolation"

[ -d "$STATE_DIR" ] || exit 0

find "$STATE_DIR" -maxdepth 1 -type f \( -name '*.armed' -o -name '*.snap' \) -mmin +360 -delete 2>/dev/null

exit 0

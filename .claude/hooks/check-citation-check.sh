#!/usr/bin/env bash
# Section-citation validator — per-vendor trigger adapter (plan-gates-citation-enforcement A5).
#
# The check-logic is a standalone, vendor-neutral Python module
# (`citation_check.py`, runnable via `--self-test` / `--scan` without this
# harness). THIS file is the only Claude-specific surface: a PostToolUse
# Write|Edit hook that self-gates to the paths whose citations are in scope.
#
# The one non-negotiable property of this validator is that it ships REGISTERED.
# The cautionary precedent is `check-localization-table.sh`, which exists, claims
# in its own rules file to run at commit and pre-push, and is wired to no hook
# event at all. Registration lives in settings.json; this script is inert without it.
#
# BLOCKING. The corpus is green (0 undecided over 138 files), so a failure here is
# a NEW break introduced by the edit in hand, not pre-existing debt.
set -uo pipefail

INPUT="$(cat)"

FILE_PATH="$(printf '%s' "$INPUT" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(""); raise SystemExit(0)
ti = d.get("tool_input") or {}
print(ti.get("file_path") or ti.get("path") or "")
' 2>/dev/null)"

[ -n "$FILE_PATH" ] || exit 0
[ -f "$FILE_PATH" ] || exit 0

# Self-gate: only rules/, skills/ and docs/ under the harness carry the citations
# this validator knows how to resolve. Everything else exits 0 untouched.
case "$FILE_PATH" in
  "$HOME"/.claude/rules/*|"$HOME"/.claude/skills/*|"$HOME"/.claude/docs/*) ;;
  *) exit 0 ;;
esac

# Backups and the starter-kit bundle tree carry stale copies of every citation.
case "$FILE_PATH" in
  *.bak-*|*/starter-kit/*) exit 0 ;;
esac

OUT="$(python3 "${KIT_HOOKS_DIR}/citation_check.py" "$FILE_PATH" 2>&1)"
RC=$?

if [ "$RC" -ne 0 ]; then
  {
    echo "BLOCKED: this edit leaves a cross-file citation that resolves to nothing."
    echo
    echo "$OUT"
    echo
    echo "Each line names the citing file, the cited file, and the section that no"
    echo "longer resolves. Fix by correcting the section name to one that exists —"
    echo "never by inventing a section to make the citation true."
    echo
    echo "If the citation is right and the target legitimately lives outside this"
    echo "tree, record a dated acceptance in:"
    echo "  ${KIT_HOOKS_DIR}/citation-acceptances.json"
    echo
    echo "Full corpus scan:  python3 ${KIT_HOOKS_DIR}/citation_check.py --scan"
  } >&2
  exit 2
fi

exit 0

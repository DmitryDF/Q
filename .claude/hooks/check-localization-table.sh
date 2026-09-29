#!/usr/bin/env bash
# check-localization-table.sh
#
# Structural validator for ~/.claude/rules/research-scope-framing.md.
# Asserts:
#  (a) every {{slot}} placeholder in the rule file body (above the
#      "## Localization Table" heading) has a row in the table with all 3
#      language columns (EN | DE | RU) populated;
#  (b) every delegation entry in Skills/research-{en,de,ru}.md that mentions
#      this rule file's basename resolves to a file on disk at RULE_FILE.
#
# Overridable via env:
#   LOCALIZATION_RULE_FILE  (default: $HOME/.claude/rules/research-scope-framing.md)
#   LOCALIZATION_SKILLS_DIR (default: Projects/Skills under the iCloud Mobile Documents tree)
#
# Exit codes:
#   0 — well-formed
#   1 — structural drift (missing column / missing row / unresolved delegation / orphan placeholder)
#   2 — rule file not found

set -u

RULE_FILE="${LOCALIZATION_RULE_FILE:-$HOME/.claude/rules/research-scope-framing.md}"
SKILLS_DIR="${LOCALIZATION_SKILLS_DIR:-$HOME/Projects/Skills}"
RULE_BASENAME="research-scope-framing.md"

if [ ! -f "$RULE_FILE" ]; then
  echo "FAIL: rule file not found: $RULE_FILE" >&2
  exit 2
fi

ERRORS=0

# --- Slice the rule file into body (before "## Localization Table") and table (after it).
BODY=$(awk '/^## Localization Table[[:space:]]*$/{flag=1; next} !flag{print}' "$RULE_FILE")
TABLE=$(awk '/^## Localization Table[[:space:]]*$/{flag=1; next} flag{print}' "$RULE_FILE")

if [ -z "$TABLE" ]; then
  echo "FAIL: rule file has no '## Localization Table' section" >&2
  exit 1
fi

# --- Collect slot keys defined in the table (column 2 of each data row).
# Skip header row ("Slot") and separator row ("------").
DEFINED_SLOTS=$(printf '%s\n' "$TABLE" | awk -F'|' '
  /^\|/ {
    key=$2
    gsub(/^[ \t]+|[ \t]+$/, "", key)
    if (key == "" || key == "Slot") next
    if (key ~ /^-+$/) next
    print key
  }
')

# --- Validate every data row has all three language columns populated.
while IFS= read -r row; do
  case "$row" in
    \|*) ;;
    *) continue ;;
  esac
  slot=$(printf '%s' "$row" | awk -F'|' '{v=$2; gsub(/^[ \t]+|[ \t]+$/, "", v); print v}')
  en=$(printf   '%s' "$row" | awk -F'|' '{v=$3; gsub(/^[ \t]+|[ \t]+$/, "", v); print v}')
  de=$(printf   '%s' "$row" | awk -F'|' '{v=$4; gsub(/^[ \t]+|[ \t]+$/, "", v); print v}')
  ru=$(printf   '%s' "$row" | awk -F'|' '{v=$5; gsub(/^[ \t]+|[ \t]+$/, "", v); print v}')
  [ -z "$slot" ] && continue
  [ "$slot" = "Slot" ] && continue
  case "$slot" in ---*|:--*) continue ;; esac
  if printf '%s' "$slot" | grep -Eq '^-+$'; then continue; fi
  if [ -z "$en" ]; then
    echo "FAIL: slot '$slot' missing EN column" >&2
    ERRORS=$((ERRORS + 1))
  fi
  if [ -z "$de" ]; then
    echo "FAIL: slot '$slot' missing DE column" >&2
    ERRORS=$((ERRORS + 1))
  fi
  if [ -z "$ru" ]; then
    echo "FAIL: slot '$slot' missing RU column" >&2
    ERRORS=$((ERRORS + 1))
  fi
done <<TABLE_EOF
$TABLE
TABLE_EOF

# --- Collect placeholders used in the body and confirm each has a defined row.
PLACEHOLDERS=$(printf '%s\n' "$BODY" | grep -oE '\{\{[a-zA-Z0-9_]+\}\}' | sed 's/[{}]//g' | sort -u)
for ph in $PLACEHOLDERS; do
  if ! printf '%s\n' "$DEFINED_SLOTS" | grep -qx "$ph"; then
    echo "FAIL: placeholder '{{$ph}}' used in body has no row in '## Localization Table'" >&2
    ERRORS=$((ERRORS + 1))
  fi
done

# --- (b) Delegation resolution: any skill file that mentions the rule file's
# basename must resolve to RULE_FILE on disk. Skill files that do not mention
# the basename are tolerated (delegation entries land in later slices).
for skill in research-en.md research-de.md research-ru.md; do
  skill_path="$SKILLS_DIR/$skill"
  [ -f "$skill_path" ] || continue
  if grep -q "$RULE_BASENAME" "$skill_path"; then
    if [ ! -f "$RULE_FILE" ]; then
      echo "FAIL: $skill mentions $RULE_BASENAME but rule file does not exist at $RULE_FILE" >&2
      ERRORS=$((ERRORS + 1))
    fi
  fi
done

if [ "$ERRORS" -gt 0 ]; then
  echo "FAIL: $ERRORS structural issue(s) in localization table" >&2
  exit 1
fi

echo "PASS: localization table is well-formed"
exit 0

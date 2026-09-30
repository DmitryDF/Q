#!/usr/bin/env bash
# Unit tests for check-localization-table.sh.
#
# Synthetic fixtures only — never touches the real rule file or skill files
# (env vars LOCALIZATION_RULE_FILE + LOCALIZATION_SKILLS_DIR redirect to
# tmp). Asserts PASS on a well-formed table and FAIL on every synthetic
# drift case the validator is responsible for.

set -u

HOOK="${KIT_HOOKS_DIR}/check-localization-table.sh"
TMP_ROOT=$(mktemp -d -t loctable-test-XXXXXX)
PASS=0
FAIL=0

cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

run_case() {
  local name="$1" expected="$2" rule_file="$3" skills_dir="$4"
  LOCALIZATION_RULE_FILE="$rule_file" LOCALIZATION_SKILLS_DIR="$skills_dir" \
    bash "$HOOK" >/dev/null 2>&1
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS + 1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    echo "      rule:   $rule_file"
    echo "      skills: $skills_dir"
    FAIL=$((FAIL + 1))
  fi
}

emit_valid_rule_file() {
  cat <<'EOF'
# Fixture

The routing prompt is {{routing_question}}. The user picks
{{path_label_ninja}} or chooses {{cancel_label}}.

## Localization Table

| Slot | EN | DE | RU |
|------|----|----|----|
| routing_question | What kind? | Welche Art? | Какой тип? |
| path_label_ninja | Ninja | Ninja | Ниндзя |
| cancel_label | Cancel | Abbrechen | Отменить |
EOF
}

# ----- Case 1: well-formed rule file, no skill files -----
RULE1="$TMP_ROOT/well-formed.md"
SKILLS1="$TMP_ROOT/skills1"
mkdir -p "$SKILLS1"
emit_valid_rule_file > "$RULE1"
run_case "1-well-formed-no-skills" 0 "$RULE1" "$SKILLS1"

# ----- Case 2: missing DE language column -----
RULE2="$TMP_ROOT/missing-de.md"
cat <<'EOF' > "$RULE2"
# Fixture

The prompt is {{slot1}}.

## Localization Table

| Slot | EN | DE | RU |
|------|----|----|----|
| slot1 | What? |  | Что? |
EOF
SKILLS2="$TMP_ROOT/skills2"
mkdir -p "$SKILLS2"
run_case "2-missing-de-column" 1 "$RULE2" "$SKILLS2"

# ----- Case 3: placeholder used in body has no row in table -----
RULE3="$TMP_ROOT/orphan-placeholder.md"
cat <<'EOF' > "$RULE3"
# Fixture

The prompt is {{slot1}} and also {{undefined_slot}}.

## Localization Table

| Slot | EN | DE | RU |
|------|----|----|----|
| slot1 | What? | Was? | Что? |
EOF
SKILLS3="$TMP_ROOT/skills3"
mkdir -p "$SKILLS3"
run_case "3-orphan-placeholder" 1 "$RULE3" "$SKILLS3"

# ----- Case 4: delegation entry in a skill file resolves (rule file exists) -----
SKILLS4="$TMP_ROOT/skills4"
mkdir -p "$SKILLS4"
cat <<'EOF' > "$SKILLS4/research-en.md"
---
name: research-en
---
See `research-scope-framing.md` for the scope-framing flow.
EOF
run_case "4-delegation-resolves" 0 "$RULE1" "$SKILLS4"

# ----- Case 5: delegation entry mentions the basename but rule file is absent -----
RULE5="$TMP_ROOT/does-not-exist.md"
SKILLS5="$TMP_ROOT/skills5"
mkdir -p "$SKILLS5"
cat <<'EOF' > "$SKILLS5/research-en.md"
---
name: research-en
---
See `research-scope-framing.md` for the scope-framing flow.
EOF
# Rule file does not exist at RULE5 → validator exits 2 ("rule file not found").
run_case "5-rule-file-missing" 2 "$RULE5" "$SKILLS5"

# ----- Case 6: rule file present, no '## Localization Table' section -----
RULE6="$TMP_ROOT/no-table.md"
cat <<'EOF' > "$RULE6"
# Fixture

No table here.
EOF
SKILLS6="$TMP_ROOT/skills6"
mkdir -p "$SKILLS6"
run_case "6-no-localization-table" 1 "$RULE6" "$SKILLS6"

# ----- Summary -----
echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

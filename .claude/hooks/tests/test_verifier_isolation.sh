#!/usr/bin/env bash
# Integration tests for check-verifier-isolation.sh (Verification Isolation guard b).
#
# Reproduces the 2026-06-09 corruption mode against a sentinel mirror only —
# NEVER touches live ~/.claude. The mirror is a mktemp dir pointed at via
# VERIFIER_ISOLATION_HOME; the hook snapshots it on an armed `pre` and hard-fails
# (exit 2) on drift at `post`, with a stale-armed re-check on a later `pre`.
#
# The hook is invoked with the mode (pre|post) as $1 and the harness JSON on
# stdin. Tests assert the exit code per case.

set -u

HOOK="${KIT_HOOKS_DIR}/check-verifier-isolation.sh"
MIRROR=$(mktemp -d -t verifier-isolation-test-XXXXXX)
export VERIFIER_ISOLATION_HOME="$MIRROR"
PASS=0
FAIL=0

cleanup() { rm -rf "$MIRROR"; }
trap cleanup EXIT

# ----- Sentinel mirror: faithful stand-in for live ~/.claude -----
# settings.json + a hook file + a git repo whose `state/` is gitignored, exactly
# as live ~/.claude is (so the guard's own .snap/.armed writes never enter the
# git-status digest component and false-fail the clean cases).
seed_mirror() {
  printf '{"k":"v"}\n' > "$MIRROR/settings.json"
  mkdir -p "$MIRROR/hooks"
  printf '#!/usr/bin/env bash\necho hi\n' > "$MIRROR/hooks/sample.sh"
  printf 'state/\n' > "$MIRROR/.gitignore"
}
seed_mirror
git -C "$MIRROR" init -q 2>/dev/null
git -C "$MIRROR" add -A 2>/dev/null
git -C "$MIRROR" -c user.email=test@test -c user.name=test commit -qm seed 2>/dev/null

# Build the harness stdin JSON for a Bash tool call.
make_input() {  # $1=session_id  $2=command
  jq -n --arg sid "$1" --arg cmd "$2" \
    '{tool_name:"Bash", session_id:$sid, tool_input:{command:$cmd}}'
}

run_case() {  # $1=name  $2=session_id  $3=mode  $4=command  $5=expected_exit
  local name="$1" sid="$2" mode="$3" cmd="$4" expected="$5"
  make_input "$sid" "$cmd" | bash "$HOOK" "$mode" >/dev/null 2>&1
  local got=$?
  if [ "$got" -eq "$expected" ]; then
    echo "PASS  $name  (exit $got)"
    PASS=$((PASS+1))
  else
    echo "FAIL  $name  (expected $expected, got $got)"
    FAIL=$((FAIL+1))
  fi
}

# ----- Case 1: clean, non-dangerous pre+post → 0, 0 -----
run_case "1a-clean-pre"  c1 pre  "ls -la"  0
run_case "1b-clean-post" c1 post "ls -la"  0

# ----- Case 2: dangerous pre (arms) + post with no change → 0 -----
run_case "2a-arm-pre"        c2 pre  "bash setup.sh"  0
run_case "2b-no-drift-post"  c2 post "bash setup.sh"  0

# ----- Case 3: dangerous pre (arms) + mutate a hook file + post → 2 (core) -----
run_case "3a-arm-pre" c3 pre "bash setup.sh" 0
printf 'echo injected\n' >> "$MIRROR/hooks/sample.sh"
run_case "3b-hook-drift-post" c3 post "bash setup.sh" 2

# ----- Case 4: dangerous pre (arms) + mutate settings.json + post → 2 -----
run_case "4a-arm-pre" c4 pre "bash setup.sh" 0
printf '{"k":"v","tampered":true}\n' > "$MIRROR/settings.json"
run_case "4b-settings-drift-post" c4 post "bash setup.sh" 2

# ----- Case 5: dangerous pre (arms), drift, then a later pre (post skipped) → 2 -----
run_case "5a-arm-pre" c5 pre "bash setup.sh" 0
printf 'echo injected-again\n' >> "$MIRROR/hooks/sample.sh"
run_case "5b-stale-armed-repre" c5 pre "ls" 2

# ----- Case 6: build_kit.py build arms; audit/diff do not; build drift blocks -----
assert_armed() {  # $1=name  $2=session_id  $3=expected(yes|no)
  local name="$1" sid="$2" expected="$3"
  local armed="$MIRROR/state/verifier-isolation/$sid.armed"
  local snap="$MIRROR/state/verifier-isolation/$sid.snap"
  if [ "$expected" = "yes" ]; then
    if [ -f "$armed" ] && [ -f "$snap" ]; then
      echo "PASS  $name  (armed)"
      PASS=$((PASS+1))
    else
      echo "FAIL  $name  (expected armed; .armed or .snap missing)"
      FAIL=$((FAIL+1))
    fi
  else
    if [ ! -f "$armed" ] && [ ! -f "$snap" ]; then
      echo "PASS  $name  (not armed)"
      PASS=$((PASS+1))
    else
      echo "FAIL  $name  (expected not armed; .armed or .snap present)"
      FAIL=$((FAIL+1))
    fi
  fi
}

# 6a-c: build arms; no-drift post → 0
run_case "6a-build-arm-pre"       c6 pre  "python3 build_kit.py build" 0
assert_armed "6b-build-armed-state" c6 yes
run_case "6c-build-no-drift-post" c6 post "python3 build_kit.py build" 0

# 6d-f: audit does NOT arm
run_case "6d-audit-clean-pre"     c6audit pre  "python3 build_kit.py audit" 0
assert_armed "6e-audit-not-armed" c6audit no
run_case "6f-audit-clean-post"    c6audit post "python3 build_kit.py audit" 0

# 6g-i: diff does NOT arm
run_case "6g-diff-clean-pre"      c6diff pre  "python3 build_kit.py diff" 0
assert_armed "6h-diff-not-armed"  c6diff no
run_case "6i-diff-clean-post"     c6diff post "python3 build_kit.py diff" 0

# 6j-k: build pre arms + drift introduced + post → 2
run_case "6j-build-drift-arm-pre" c6drift pre  "python3 build_kit.py build" 0
printf 'echo injected-by-build\n' >> "$MIRROR/hooks/sample.sh"
run_case "6k-build-drift-post"    c6drift post "python3 build_kit.py build" 2

# ----- Case 7: substring-tar-in-started false-positive (axis 1) → not armed -----
# printf body contains 'started' (substring 'tar') near a literal '.claude' path.
# Pre-fix this armed the guard; post-fix the regex requires whole-word match.
run_case "7a-substring-tar-pre"        c7 pre  "printf 'started installing things in ~/.claude/foo'" 0
assert_armed "7b-substring-tar-not-armed" c7 no

# ----- Case 8: PROJECT-side .claude/ false-positive (axis 4) → not armed -----
# Command targets a project-local .claude/ directory (NOT user-home).
# Pre-fix the bare \.claude regex matched any path; post-fix requires home-prefix anchor.
run_case "8a-project-side-claude-pre"        c8 pre  "mkdir -p /tmp/some-project/.claude/logs" 0
assert_armed "8b-project-side-claude-not-armed" c8 no

# ----- Case 9: SessionStart cleanup removes 7h-old .armed/.snap (axis 2) -----
# Pre-create a stale pair 7 hours old, run cleanup, assert both gone.
mkdir -p "$MIRROR/state/verifier-isolation"
touch "$MIRROR/state/verifier-isolation/test9.armed" "$MIRROR/state/verifier-isolation/test9.snap"
# Backdate file mtime to 7 hours ago (-A is BSD, --date= is GNU; use a portable touch -t with computed timestamp via date -v on BSD or date -d on GNU)
if date -v-7H +%Y%m%d%H%M.%S >/dev/null 2>&1; then
  STAMP=$(date -v-7H +%Y%m%d%H%M.%S)   # BSD
else
  STAMP=$(date -d '7 hours ago' +%Y%m%d%H%M.%S)   # GNU
fi
touch -t "$STAMP" "$MIRROR/state/verifier-isolation/test9.armed" "$MIRROR/state/verifier-isolation/test9.snap"
bash "${KIT_HOOKS_DIR}/cleanup-stale-verifier-isolation.sh" >/dev/null 2>&1
if [ ! -f "$MIRROR/state/verifier-isolation/test9.armed" ] && [ ! -f "$MIRROR/state/verifier-isolation/test9.snap" ]; then
  echo "PASS  9-stale-arm-cleanup  (both files removed)"
  PASS=$((PASS+1))
else
  echo "FAIL  9-stale-arm-cleanup  (.armed and/or .snap still present)"
  FAIL=$((FAIL+1))
fi

# ----- Case 9b: cleanup preserves fresh files (younger than 6h) -----
touch "$MIRROR/state/verifier-isolation/fresh9b.armed" "$MIRROR/state/verifier-isolation/fresh9b.snap"
bash "${KIT_HOOKS_DIR}/cleanup-stale-verifier-isolation.sh" >/dev/null 2>&1
if [ -f "$MIRROR/state/verifier-isolation/fresh9b.armed" ] && [ -f "$MIRROR/state/verifier-isolation/fresh9b.snap" ]; then
  echo "PASS  9b-fresh-files-preserved  (cleanup spared sub-6h files)"
  PASS=$((PASS+1))
else
  echo "FAIL  9b-fresh-files-preserved  (fresh files removed)"
  FAIL=$((FAIL+1))
fi

# ----- Summary -----
echo ""
echo "Results: $PASS passed, $FAIL failed."
[ "$FAIL" -eq 0 ] && exit 0 || exit 1

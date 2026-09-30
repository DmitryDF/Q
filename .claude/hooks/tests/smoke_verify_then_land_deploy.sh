#!/usr/bin/env bash
# S6 out-of-session smoke — verify-then-land HARNESS DEPLOY gate (claude-promote).
#
# Proves 5 scenarios end-to-end against the REAL claude-promote bash tool +
# the REAL land_port.py `verify-deploy-candidate` render+check path, driven
# entirely inside a disposable scratch environment: a fake bare git "origin",
# a fake config source clone, a fake deployed-form CLAUDE_CONFIG_DIR (holding
# COPIES of the real hooks modules), a fake `config-source` + `gh` on a prepended
# PATH, and a CONTROLLABLE fake `claude-verify` whose exit code this script
# flips green/red per scenario. Nothing here reads or writes the real
# `config-source-remote` remote, the real config source tree, or live `~/.claude`
# (except the READ-ONLY isolation hook below, which is itself the guard that
# proves that — see "Isolation" below).
#
# HOW TO RUN
#   Open a FRESH shell (outside the Claude Code session that authored this
#   script — S9-style out-of-session convention, research-scope-framing.md).
#   Then:
#     bash dot_claude/hooks/tests/smoke_verify_then_land_deploy.sh
#   Non-interactive; no prompts. Exits 0 iff all 5 scenarios pass.
#
# SCENARIO -> SPEC MAPPING (Slice S6 plan)
#   (a) harness-code change, fake claude-verify RED  -> deploy REFUSED before
#       the branch reaches the fake origin; no receipt note; branch checked
#       out back to the default branch.
#   (b) harness-code change, fake claude-verify GREEN -> branch AND the
#       `refs/notes/verify-then-land` receipt note both land on the fake
#       origin.
#   (c) docs/rules-only change -> the S3 gate is skipped entirely (exempt);
#       plain push; NO receipt note is ever written.
#   (d) harness-code change + VERIFY_THEN_LAND_SKIP=1 + --emergency-skip (S5
#       sanctioned override) -> the check is NOT run; a
#       `refs/notes/verify-then-land-skip` note (carrying the reason) is
#       written AND pushed to the fake origin. NOTE: this local smoke proves
#       only the LOCAL git-note write+push half of the override. The CI-side
#       "reports skip-authorized" half is S4's own responsibility
#       (test_ci_verify_receipt.py / a CI workflow simulator) — a local shell
#       cannot execute the GitHub Actions workflow, so it is explicitly OUT
#       OF SCOPE for this script.
#   (e) co-ship safety: `check-verify-then-land-delivery.sh` (Gate-2 delivery
#       assertion) PASSES against the real claude-promote (both sentinels
#       present) and FAILS against a copy with the S5 override sentinel +
#       `--emergency-skip` handling stripped out (gate-without-escape-hatch).
#
# ISOLATION (mandatory defense-in-depth, code_first_architecture.md
# "Verification Isolation" + research-scope-framing.md's out-of-session
# smoke idiom): every scenario call is wrapped in the
# `check-verifier-isolation.sh` pre/post snapshot pair, which hashes live
# `~/.claude/{settings.json,hooks/*,rules/*,skills/**/SKILL.md,agents/*,
# state/**/*.json}` before and after and HARD-FAILS (exit 2) on any drift.
# Each scenario command carries a `: bootstrap-smoke` no-op token so the
# guard's `is_dangerous()` matcher arms the snapshot deterministically (the
# same convention research-scope-framing.md's S9 block uses). If `jq` or the
# hook script are not resolvable in this environment, isolation wrapping is
# SKIPPED and the results block says so explicitly — scenarios still run.
#
# SAFETY (safe-defaults.md — no bare `rm -rf` / `--force*`): every scratch
# root this script creates is a `mktemp -d` directory whose basename starts
# with the fixed prefix `s6-vtl-`; `safe_rmtree()` below refuses to remove
# anything whose basename does not match that prefix. This is the bash
# analogue of land_port.py's own `_safe_rmtree_deploy_dir` (prefix-gated
# delete, scoped to directories this script itself created).

set -uo pipefail

: "${SESSION_ID:=s6-smoke-$$}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_HOOKS="$(cd "$SCRIPT_DIR/.." && pwd)"                 # .../dot_claude/hooks
REPO_BIN="$(cd "$REPO_HOOKS/../bin" && pwd)"               # .../dot_claude/bin
export REPO_HOOKS REPO_BIN SESSION_ID
REAL_PATH="$PATH"
export REAL_PATH

HOOK="${VERIFIER_ISOLATION_HOOK:-${KIT_HOOKS_DIR}/check-verifier-isolation.sh}"
export HOOK

RESULTS=()

# ─────────────────────────────────────────────────────────────────────────
# preflight — fail loudly and early if the source tree this smoke depends on
# is missing a required module (never a silent partial run).
# ─────────────────────────────────────────────────────────────────────────
preflight() {
  local missing="" f
  for f in land_port.py bookkeeping_lock.py bookkeeping_paths.py \
           bookkeeping_resolver.py bookkeeping-paths.json \
           harness_code_paths.py harness-code-paths.json \
           executable_check-verify-then-land-delivery.sh; do
    [ -f "$REPO_HOOKS/$f" ] || missing="$missing $f"
  done
  [ -f "$REPO_BIN/executable_claude-promote" ] || missing="$missing bin/executable_claude-promote"
  if [ -n "$missing" ]; then
    printf 'PREFLIGHT FAILED — missing required source file(s) under %s / %s:%s\n' \
      "$REPO_HOOKS" "$REPO_BIN" "$missing" >&2
    exit 2
  fi
  for c in git python3 bash sed grep chmod mktemp; do
    command -v "$c" >/dev/null 2>&1 || { printf 'PREFLIGHT FAILED — required command not found: %s\n' "$c" >&2; exit 2; }
  done
}
preflight

# ─────────────────────────────────────────────────────────────────────────
# safe cleanup primitive (safe-defaults.md — never a bare rm -rf)
# ─────────────────────────────────────────────────────────────────────────
safe_rmtree() {
  local p="$1"
  case "$p" in
    /*) ;;
    *) return 0 ;;
  esac
  [ -e "$p" ] || return 0
  case "$(basename "$p")" in
    s6-vtl-*) rm -rf -- "$p" ;;
    *) printf 'safe_rmtree: refusing to remove unrecognized path: %s\n' "$p" >&2; return 1 ;;
  esac
}
export -f safe_rmtree

# ─────────────────────────────────────────────────────────────────────────
# fake-tool generators
# ─────────────────────────────────────────────────────────────────────────
write_fake_config_source() {  # $1 = path to write
  {
    printf '#!/usr/bin/env bash\n'
    printf 'case "${1:-}" in\n'
    printf '  source-path) printf "%%s\\n" "${Q_CONFIG_FAKE_SOURCE:?Q_CONFIG_FAKE_SOURCE not set}"; exit 0 ;;\n'
    printf '  *) exit 0 ;;\n'
    printf 'esac\n'
  } > "$1"
  chmod +x "$1"
}
export -f write_fake_config_source

write_fake_gh() {  # $1 = path to write
  {
    printf '#!/usr/bin/env bash\n'
    printf 'case "${1:-} ${2:-}" in\n'
    printf '  "pr create") printf "https://example.invalid/fake-pr/1\\n"; exit 0 ;;\n'
    printf '  "pr merge")  exit 0 ;;\n'
    printf '  *) exit 0 ;;\n'
    printf 'esac\n'
  } > "$1"
  chmod +x "$1"
}
export -f write_fake_gh

write_fake_verify() {  # $1 = path to write, $2 = green|red
  local mode="$2"
  {
    printf '#!/usr/bin/env bash\n'
    if [ "$mode" = "red" ]; then
      printf 'printf "[fake-claude-verify] RED (forced by S6 smoke)\\n" >&2\n'
      printf 'exit 1\n'
    else
      printf 'printf "[fake-claude-verify] GREEN (forced by S6 smoke)\\n"\n'
      printf 'exit 0\n'
    fi
  } > "$1"
  chmod +x "$1"
}
export -f write_fake_verify

# ─────────────────────────────────────────────────────────────────────────
# scratch environment builder (shared by scenarios a-d)
#   $1 = scratch root (already created via mktemp, basename s6-vtl-*)
# Layout:
#   $1/seed        -> throwaway working repo, pushed to origin then discarded
#   $1/origin.git  -> bare "remote" the whole scenario pushes to
#   $1/source      -> the config-source SOURCE clone (== $Q_CONFIG_SRC claude-promote resolves)
#   $1/cfg         -> deployed-form CLAUDE_CONFIG_DIR (hooks/ + bin/)
#   $1/fakebin     -> fake config-source + gh, prepended onto PATH
# ─────────────────────────────────────────────────────────────────────────
new_scratch_env() {
  local root="$1"
  mkdir -p "$root/seed/dot_claude/hooks" "$root/seed/dot_claude/bin" "$root/seed/dot_claude/rules"
  printf 'print("v1")\n' > "$root/seed/dot_claude/hooks/widget.py"
  printf '#!/usr/bin/env bash\necho ok\n' > "$root/seed/dot_claude/bin/tool.sh"
  printf '# a rule\nsome rule text\n' > "$root/seed/dot_claude/rules/note.md"
  printf 'quick\n' > "$root/seed/.pr-mode"

  git -C "$root/seed" init -q -b main
  git -C "$root/seed" config user.email t@t
  git -C "$root/seed" config user.name t
  git -C "$root/seed" add -A
  git -C "$root/seed" commit -qm seed

  git init -q --bare -b main "$root/origin.git"
  git -C "$root/seed" remote add origin "$root/origin.git"
  git -C "$root/seed" push -q -u origin main

  git clone -q "$root/origin.git" "$root/source"
  git -C "$root/source" config user.email t@t
  git -C "$root/source" config user.name t

  mkdir -p "$root/cfg/hooks" "$root/cfg/bin" "$root/cfg/state"
  local f
  for f in land_port.py bookkeeping_lock.py bookkeeping_paths.py bookkeeping_resolver.py \
           bookkeeping-paths.json harness_code_paths.py harness-code-paths.json; do
    cp "$REPO_HOOKS/$f" "$root/cfg/hooks/$f"
  done
  cp "$REPO_HOOKS/executable_check-verify-then-land-delivery.sh" "$root/cfg/hooks/check-verify-then-land-delivery.sh"
  chmod +x "$root/cfg/hooks/check-verify-then-land-delivery.sh"
  cp "$REPO_BIN/executable_claude-promote" "$root/cfg/bin/claude-promote"
  chmod +x "$root/cfg/bin/claude-promote"

  mkdir -p "$root/fakebin"
  write_fake_config_source "$root/fakebin/config-source"
  write_fake_gh "$root/fakebin/gh"
  write_fake_verify "$root/cfg/bin/claude-verify" green   # scenarios override to red as needed
}
export -f new_scratch_env

# invoke the scratch claude-promote with the scratch env wired up
run_promote() {  # $1 = root, remaining = args to claude-promote
  local root="$1"; shift
  CLAUDE_CONFIG_DIR="$root/cfg" \
  PATH="$root/fakebin:$REAL_PATH" \
  Q_CONFIG_FAKE_SOURCE="$root/source" \
    "$root/cfg/bin/claude-promote" "$@"
}
export -f run_promote

origin_refs() {  # $1 = root, $2 = ref-prefix (e.g. refs/heads/promote/)
  git -C "$1/origin.git" for-each-ref --format='%(refname)' "$2" 2>/dev/null
}
export -f origin_refs

# ─────────────────────────────────────────────────────────────────────────
# (a) harness-code change, RED check -> REFUSED before the branch reaches origin
# ─────────────────────────────────────────────────────────────────────────
scenario_a() {
  local root; root="$(mktemp -d "${TMPDIR:-/tmp}/s6-vtl-a-XXXXXX")" || { echo "RESULT: FAIL - mktemp failed"; exit 1; }
  new_scratch_env "$root"
  write_fake_verify "$root/cfg/bin/claude-verify" red

  printf 'print("v2 - scenario a")\n' >> "$root/source/dot_claude/hooks/widget.py"
  local before_head; before_head="$(git -C "$root/source" symbolic-ref --short HEAD)"

  run_promote "$root" --no-verify -m "scenario a" >"$root/out.log" 2>&1
  local rc=$?

  local branches notes after_head
  branches="$(origin_refs "$root" refs/heads/promote/)"
  notes="$(origin_refs "$root" refs/notes/verify-then-land)"
  after_head="$(git -C "$root/source" symbolic-ref --short HEAD 2>/dev/null || printf 'DETACHED')"

  if [ "$rc" -ne 0 ] && [ -z "$branches" ] && [ -z "$notes" ] && [ "$after_head" = "$before_head" ]; then
    echo "RESULT: PASS (rc=$rc, no promote/* branch on origin, no receipt note, HEAD restored to '$after_head')"
    safe_rmtree "$root"; exit 0
  else
    echo "RESULT: FAIL rc=$rc branches='$branches' notes='$notes' head='$after_head' (expected head='$before_head')"
    sed 's/^/    log: /' "$root/out.log"
    safe_rmtree "$root"; exit 1
  fi
}
export -f scenario_a

# ─────────────────────────────────────────────────────────────────────────
# (b) harness-code change, GREEN check -> branch + receipt note both land
# ─────────────────────────────────────────────────────────────────────────
scenario_b() {
  local root; root="$(mktemp -d "${TMPDIR:-/tmp}/s6-vtl-b-XXXXXX")" || { echo "RESULT: FAIL - mktemp failed"; exit 1; }
  new_scratch_env "$root"
  write_fake_verify "$root/cfg/bin/claude-verify" green

  printf 'print("v2 - scenario b")\n' >> "$root/source/dot_claude/hooks/widget.py"

  run_promote "$root" --no-verify -m "scenario b" >"$root/out.log" 2>&1
  local rc=$?

  local branches notes
  branches="$(origin_refs "$root" refs/heads/promote/)"
  notes="$(origin_refs "$root" refs/notes/verify-then-land)"

  if [ "$rc" -eq 0 ] && [ -n "$branches" ] && [ -n "$notes" ]; then
    echo "RESULT: PASS (rc=0, branch on origin: $branches; receipt note on origin: $notes)"
    safe_rmtree "$root"; exit 0
  else
    echo "RESULT: FAIL rc=$rc branches='$branches' notes='$notes'"
    sed 's/^/    log: /' "$root/out.log"
    safe_rmtree "$root"; exit 1
  fi
}
export -f scenario_b

# ─────────────────────────────────────────────────────────────────────────
# (c) docs/rules-only change -> gate EXEMPT: plain push, NO receipt note
# ─────────────────────────────────────────────────────────────────────────
scenario_c() {
  local root; root="$(mktemp -d "${TMPDIR:-/tmp}/s6-vtl-c-XXXXXX")" || { echo "RESULT: FAIL - mktemp failed"; exit 1; }
  new_scratch_env "$root"
  write_fake_verify "$root/cfg/bin/claude-verify" red   # must NEVER be invoked on this path

  printf '# more rule text - scenario c\n' >> "$root/source/dot_claude/rules/note.md"

  run_promote "$root" --no-verify -m "scenario c" >"$root/out.log" 2>&1
  local rc=$?

  local branches notes
  branches="$(origin_refs "$root" refs/heads/promote/)"
  notes="$(origin_refs "$root" refs/notes/verify-then-land)"

  if [ "$rc" -eq 0 ] && [ -n "$branches" ] && [ -z "$notes" ] \
     && grep -q "verify-then-land gate not applicable" "$root/out.log"; then
    echo "RESULT: PASS (rc=0, branch on origin: $branches; NO receipt note; gate logged as not-applicable)"
    safe_rmtree "$root"; exit 0
  else
    echo "RESULT: FAIL rc=$rc branches='$branches' notes='$notes'"
    sed 's/^/    log: /' "$root/out.log"
    safe_rmtree "$root"; exit 1
  fi
}
export -f scenario_c

# ─────────────────────────────────────────────────────────────────────────
# (d) emergency override (S5) -> check NOT run; skip-note (with reason)
#     written + pushed. CI-side reporting is OUT OF SCOPE here (see header).
# ─────────────────────────────────────────────────────────────────────────
scenario_d() {
  local root; root="$(mktemp -d "${TMPDIR:-/tmp}/s6-vtl-d-XXXXXX")" || { echo "RESULT: FAIL - mktemp failed"; exit 1; }
  new_scratch_env "$root"
  write_fake_verify "$root/cfg/bin/claude-verify" red   # must NEVER be invoked on this path

  printf 'print("v2 - scenario d")\n' >> "$root/source/dot_claude/hooks/widget.py"

  VERIFY_THEN_LAND_SKIP=1 VERIFY_THEN_LAND_SKIP_REASON="S6 smoke emergency-skip test" \
    run_promote "$root" --no-verify --emergency-skip -m "scenario d" >"$root/out.log" 2>&1
  local rc=$?

  local branches skip_notes green_notes tip reason_ok=0
  branches="$(origin_refs "$root" refs/heads/promote/)"
  skip_notes="$(origin_refs "$root" refs/notes/verify-then-land-skip)"
  green_notes="$(origin_refs "$root" refs/notes/verify-then-land)"
  if [ -n "$branches" ]; then
    tip="$(git -C "$root/origin.git" rev-parse "$branches" 2>/dev/null)"
    if [ -n "$tip" ] && git -C "$root/origin.git" notes --ref=verify-then-land-skip show "$tip" 2>/dev/null \
         | grep -q "S6 smoke emergency-skip test"; then
      reason_ok=1
    fi
  fi

  if [ "$rc" -eq 0 ] && [ -n "$branches" ] && [ -n "$skip_notes" ] \
     && [ -z "$green_notes" ] && [ "$reason_ok" -eq 1 ]; then
    echo "RESULT: PASS (rc=0, branch on origin: $branches; skip-note with reason present; no green check note)"
    safe_rmtree "$root"; exit 0
  else
    echo "RESULT: FAIL rc=$rc branches='$branches' skip_notes='$skip_notes' green_notes='$green_notes' reason_ok=$reason_ok"
    sed 's/^/    log: /' "$root/out.log"
    safe_rmtree "$root"; exit 1
  fi
}
export -f scenario_d

# ─────────────────────────────────────────────────────────────────────────
# (e) delivery-gate co-ship assertion: real claude-promote PASSES,
#     a copy with the S5 override stripped out FAILS.
# ─────────────────────────────────────────────────────────────────────────
scenario_e() {
  local root; root="$(mktemp -d "${TMPDIR:-/tmp}/s6-vtl-e-XXXXXX")" || { echo "RESULT: FAIL - mktemp failed"; exit 1; }
  mkdir -p "$root"
  local real="$REPO_BIN/executable_claude-promote"
  local delivery="$REPO_HOOKS/executable_check-verify-then-land-delivery.sh"

  cp "$real" "$root/promote-real.sh"
  grep -v 'VERIFY-THEN-LAND-SKIP-OVERRIDE (S5)' "$real" \
    | sed 's/--emergency-skip//g' > "$root/promote-stripped.sh"

  bash "$delivery" --file "$root/promote-real.sh" >"$root/real.log" 2>&1
  local rc_real=$?
  bash "$delivery" --file "$root/promote-stripped.sh" >"$root/stripped.log" 2>&1
  local rc_stripped=$?

  if [ "$rc_real" -eq 0 ] && [ "$rc_stripped" -ne 0 ]; then
    echo "RESULT: PASS (real claude-promote: PASS/exit 0; stripped copy: FAIL/exit $rc_stripped)"
    safe_rmtree "$root"; exit 0
  else
    echo "RESULT: FAIL rc_real=$rc_real rc_stripped=$rc_stripped"
    sed 's/^/    real: /' "$root/real.log"
    sed 's/^/    stripped: /' "$root/stripped.log"
    safe_rmtree "$root"; exit 1
  fi
}
export -f scenario_e

# ─────────────────────────────────────────────────────────────────────────
# isolation-wrapped scenario runner (research-scope-framing.md idiom)
# ─────────────────────────────────────────────────────────────────────────
ISOLATION_AVAILABLE=1
ISO_NOTE=""
if ! command -v jq >/dev/null 2>&1; then
  ISOLATION_AVAILABLE=0
  ISO_NOTE="jq not found on PATH"
elif [ ! -x "$HOOK" ]; then
  ISOLATION_AVAILABLE=0
  ISO_NOTE="check-verifier-isolation.sh not found/executable at $HOOK (set VERIFIER_ISOLATION_HOOK to override)"
fi

run() {  # $1 = label, $2 = command string
  local label="$1" cmd="$2" jin="" p=0 t q=0
  if [ "$ISOLATION_AVAILABLE" -eq 1 ]; then
    jin=$(jq -nc --arg sid "$SESSION_ID" --arg c "$cmd" \
      '{tool_name:"Bash", session_id:$sid, tool_input:{command:$c}}')
    printf '%s' "$jin" | "$HOOK" pre; p=$?
  fi
  bash -c "$cmd"; t=$?
  if [ "$ISOLATION_AVAILABLE" -eq 1 ]; then
    printf '%s' "$jin" | "$HOOK" post; q=$?
  fi
  if [ "$p" -eq 0 ] && [ "$t" -eq 0 ] && [ "$q" -eq 0 ]; then
    RESULTS+=("$label: PASS")
  elif [ "$ISOLATION_AVAILABLE" -eq 1 ]; then
    RESULTS+=("$label: FAIL (pre=$p scenario=$t post=$q)")
  else
    RESULTS+=("$label: FAIL (scenario=$t; isolation SKIPPED — $ISO_NOTE)")
  fi
}

main() {
  echo "S6 verify-then-land deploy-gate smoke — scenarios (a)-(e):"
  [ "$ISOLATION_AVAILABLE" -eq 1 ] || printf 'NOTE: isolation pre/post wrapping SKIPPED for this run — %s\n' "$ISO_NOTE"
  echo

  run "(a) RED check -> deploy REFUSED"                 ": bootstrap-smoke; scenario_a"
  run "(b) GREEN check -> branch + receipt note land"   ": bootstrap-smoke; scenario_b"
  run "(c) docs-only -> gate EXEMPT, no receipt note"   ": bootstrap-smoke; scenario_c"
  run "(d) emergency override -> skip-note recorded"    ": bootstrap-smoke; scenario_d"
  run "(e) delivery-gate co-ship safety assertion"      ": bootstrap-smoke; scenario_e"

  printf '\n=== S6 SMOKE RESULTS (SESSION_ID=%s) ===\n' "$SESSION_ID"
  local fail=0 r
  for r in "${RESULTS[@]}"; do
    printf '  %s\n' "$r"
    case "$r" in *FAIL*) fail=1 ;; esac
  done
  if [ "$ISOLATION_AVAILABLE" -eq 1 ]; then
    printf '  isolation pre/post wrapping: ACTIVE for all 5 scenarios — no live ~/.claude drift on any run.\n'
  else
    printf '  isolation pre/post wrapping: SKIPPED for all 5 scenarios — %s\n' "$ISO_NOTE"
  fi
  printf '  SCOPE NOTE: scenario (d) verifies only the LOCAL git-note write+push half of the S5\n'
  printf '  emergency override. The CI-side "reports skip-authorized" half is verified separately by\n'
  printf '  S4'"'"'s own test_ci_verify_receipt.py / a CI workflow simulator — a local shell cannot\n'
  printf '  execute the GitHub Actions workflow.\n'
  if [ "$fail" -eq 0 ]; then
    printf 'OVERALL: PASS\n=== END ===\n'
    return 0
  fi
  printf 'OVERALL: FAIL\n=== END ===\n'
  return 1
}

main
exit $?

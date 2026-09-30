#!/usr/bin/env bash
# plan_env.sh — execution scaffolding for the plan
#   "Close the topic-identity generator, and make its damage countable"
#   (~/.claude/plans/staged-bouncing-lighthouse.md, slice S0 / action A0).
#
# WHY THIS IS A SCRIPT AND NOT PROSE. Across eleven review rounds, five of the
# last six found a defect that the PREVIOUS round's fix had introduced, and every
# one of them sat on the same seam: guidance written into narrative that never
# reached the one runnable block. Prose cannot be executed, so a defect in it is
# invisible until somebody reads carefully. Every guard the plan accumulated is
# therefore a verb here, and `selftest` is what turns each one from a promise into
# a command that either exits non-zero or does not.
#
# SCOPE. Mechanics only. This script makes no identity decisions, edits no
# production module, and mutates no live record. Those belong to A1-A6.
#
# VERBS
#   preflight       environment gate; builds SNAP_DIR, the HOME shim, ENV,
#                   DIGEST_BASELINE (write-once) and the state snapshot
#   gate            the baseline test suite, run against the CLONE (proven, not assumed)
#   digest-record   re-record the rolling per-slice digest over the clone's modules
#   digest-assert   assert the rolling digest still matches what the last slice left
#   cutover-check   LIVE copies vs DIGEST_BASELINE, over the full write-target list
#   buckets         record/assert the snapshot's find-mis-keyed buckets (A0's baseline)
#   anchors         re-derive the plan's line citations from the tokens beside them
#   selftest        exercise every refusal path AND the two positive paths
#
# Every verb is safe to re-run. Nothing here deletes anything: teardown of
# SNAP_DIR is deliberately manual, because `safe-defaults.md` bans `rm -rf` and a
# variable-driven recursive delete is exactly the shape that policy exists to
# prevent.

set -euo pipefail

# --------------------------------------------------------------------------- #
# THE REAL HOME, CAPTURED ONCE AND PINNED — before anything can move it.
# --------------------------------------------------------------------------- #
# Two verbs deliberately override HOME: `gate` points it at the shim so the
# suite's hardcoded `Path.home()` resolves into the clone, and `selftest` points
# it at a fixture home to inject a synthetic lock. Anything deriving a LIVE path
# from $HOME at call time would silently follow the override — and it did: the
# first version of this script computed PATH from $HOME, so the fresh-lock
# selftest case died on "claude-experiment not found" before the lock probe it
# was testing ever ran, and reported PASS because it had refused. A refusal for
# the wrong reason is indistinguishable from a correct one in a pass/fail
# harness, which is exactly why these two are pinned here instead.
PLAN_ENV_REAL_HOME="${PLAN_ENV_REAL_HOME:-$HOME}"
LIVE_CONFIG="${PLAN_ENV_LIVE_CONFIG:-$PLAN_ENV_REAL_HOME/.claude}"
export PLAN_ENV_REAL_HOME LIVE_CONFIG

# --------------------------------------------------------------------------- #
# Constants — the plan's declared lists, in ONE place.
# --------------------------------------------------------------------------- #

# Every file this plan writes. DIGEST_BASELINE covers all of them; the cutover
# carries them one `cp` per file. Scoping this to the two big modules would leave
# six files we overwrite with no drift guard at all.
#
# AMENDED 2026-08-25 — extended from 8 paths to 13. A5 makes `_topic_path` and
# `_write_topic_state` KEYWORD-ONLY, so every Python caller passing identity
# halves positionally breaks the moment `pre_plan_gates.py` is promoted. The
# original list declared only the defining module, so promoting it alone would
# have shipped a red live suite.
#
# The five added paths were derived by an alias-resolving AST walk over the whole
# live config root (hooks/ + skills/ + bin/ + agents/), NOT by grep and NOT by a
# name-matching walk -- both of which miss the aliased call at
# `test_auto_register.py:255` (`real = ppg._write_topic_state`; `real(a, b, c)`).
# That omission is the one that matters: its failure mode is SILENT, because
# `seen.append(...)` runs BEFORE the call, so the test stays green while nothing
# is written. Running the live suite after promoting would not catch it.
#
# `DIGEST_BASELINE` must be extended IN THE SAME CHANGE. `cmd_cutover_check`
# iterates that file's rows, not this list -- this list is consumed by `preflight`
# only on the baseline's first write, and the baseline is write-once. Extending
# this list alone carries the new files with no drift guard at all.
WRITE_TARGETS="
hooks/pre_plan_gates.py
hooks/reap_orphan_plain_topics.py
hooks/tests/plan_env.sh
hooks/tests/test_identity_mint_topic_half.py
hooks/tests/test_identity_mint_autobind.py
hooks/tests/test_phase_rollback.py
hooks/tests/test_reap_orphan_plain_topics.py
skills/clarification/SKILL.md
hooks/tests/test_clarification_schema.py
hooks/tests/test_s5_clarification_bytestable.py
hooks/tests/test_auto_register.py
hooks/tests/test_work_done.py
hooks/tests/test_identity_keyword_only.py
"

# The rolling digest chains only the two production modules — the files a slice
# actually advances, and the two a concurrent session is known to be editing.
ROLLING_TARGETS="
hooks/pre_plan_gates.py
hooks/reap_orphan_plain_topics.py
"

# The shipped baseline suite (64 passed as of 2026-08-17).
BASELINE_TESTS="
test_identity_resolver.py
test_identity_reconcile.py
test_identity_reconcile_e2e.py
test_identity_mint_autobind.py
test_identity_lock.py
test_identity_convergence.py
test_topic_orient.py
test_reap_orphan_plain_topics.py
"

# Created by later slices, as `<file>:<creating-slice>` pairs. `gate` runs each
# once it exists — and, once its creating slice has COMPLETED, REQUIRES it.
#
# The slice id is carried here rather than in a second lookup table on purpose:
# two lists that must agree is the restatement-drift shape this whole plan keeps
# tripping on, in miniature. One list, parsed at the single point of use.
#
# Why the slice id is needed at all: without it `gate` cannot tell "not yet
# created" from "created and then lost", so it silently skipped BOTH. That made
# `gate` unable to fail on a vanished new test after S3 — "a gate that cannot
# fail is not a gate", the property this script asserts about itself at the
# PROVE block below. Found by the S0 conformance round, 2026-08-22.
NEW_TESTS="
test_identity_mint_topic_half.py:S1
test_phase_rollback.py:S3
test_identity_keyword_only.py:S4
"
# Test-only override, mirroring PLAN_ENV_ANCHOR_FILES_OVERRIDE. It exists so
# `selftest` can drive a MALFORMED entry through the real loop: a guard that
# refuses a bad entry is worth nothing if nothing ever hands it one.
NEW_TESTS="${PLAN_ENV_NEW_TESTS_OVERRIDE:-$NEW_TESTS}"

# The two modules whose line numbers drift, plus the stable file the plan cites.
# This list is CONSUMED by the anchors verb (passed in as argv) rather than
# re-declared inside its Python -- two copies of one list is the same
# restatement-drift shape this whole plan keeps tripping on, in miniature.
ANCHOR_FILES="pre_plan_gates.py reap_orphan_plain_topics.py taskmanagement.py"

PLAN_FILE_DEFAULT="$LIVE_CONFIG/plans/staged-bouncing-lighthouse.md"

# The /execute-plan run-state for THIS plan — how `gate` learns whether a slice
# has completed. Resolved against LIVE_CONFIG, never against $HOME.
#
# Be precise about WHY, because an earlier revision of this comment overstated it
# and a mutation test caught the overstatement. This line is evaluated at MODULE
# level, before `gate` exports HOME="$CLAUDE_CONFIG_DIR_PARENT" — so a $HOME-relative
# default would in fact resolve correctly today, by accident of ordering. The
# hazard is LATENT, not live: the moment this assignment moves inside a function,
# is re-evaluated after the export, or is copied into a verb that runs post-export,
# it would silently follow the shim into the clone (which has no state/execplan) and
# every slice would read as not-determinable — turning the guard below inert while
# it still looked correct. LIVE_CONFIG is pinned at the top precisely so ordering
# never has to be reasoned about again. Verified by mutation: swapping this to
# $HOME makes selftest case (iv) fail.
PLAN_RUN_STATE="${PLAN_ENV_RUN_STATE:-$LIVE_CONFIG/state/execplan/staged-bouncing-lighthouse.run-state.json}"

# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

log()  { printf '[plan_env] %s\n' "$*"; }
warn() { printf '[plan_env] WARNING: %s\n' "$*" >&2; }

# slice_state <slice-id> -> prints "completed" | "other" | "unknown"
#
# THREE outcomes, not two, and the third is the point. "unknown" (no run-state on
# disk, unparseable JSON, or no interpreter) is NOT folded into "not completed":
# a caller that cannot tell must say so loudly rather than skip in silence, which
# is the failure mode this helper was added to remove. Never dies — resolving
# slice state must not be able to take `gate` down.
slice_state() {
  local sid="$1"
  [ -f "$PLAN_RUN_STATE" ] || { printf 'unknown\n'; return 0; }
  local py="${PY:-}"
  [ -n "$py" ] || py="$(command -v python3 2>/dev/null || true)"
  [ -n "$py" ] || { printf 'unknown\n'; return 0; }
  "$py" - "$PLAN_RUN_STATE" "$sid" <<'SLICEPY' 2>/dev/null || printf 'unknown\n'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        state = json.load(fh)
    row = state.get(sys.argv[2])
    if not isinstance(row, dict):
        # Slice absent from the run-state is a KNOWN answer, not an unknown one:
        # the run exists and has simply not reached this slice yet.
        print("other")
    else:
        print("completed" if row.get("status") == "completed" else "other")
except Exception:
    print("unknown")
SLICEPY
}
die()  { printf '[plan_env] REFUSED: %s\n' "$*" >&2; exit 1; }

# Resolved absolute path, symlinks followed. A trailing slash or a symlink must
# not be able to defeat the live-config comparison below, which is the single
# cheapest guard in the whole plan.
realpath_of() {
  local p="$1" d b
  if [ -d "$p" ]; then
    ( cd "$p" 2>/dev/null && pwd -P ) || printf '%s' "$p"
  else
    d=$(dirname "$p"); b=$(basename "$p")
    if [ -d "$d" ]; then
      ( cd "$d" 2>/dev/null && printf '%s/%s' "$(pwd -P)" "$b" ) || printf '%s' "$p"
    else
      printf '%s' "$p"
    fi
  fi
}

# sha256 of a file, or the literal ABSENT. NEVER fails: an unreadable file yields
# the sentinel UNREADABLE rather than a non-zero exit, because this helper is
# called BOTH as a bare `printf` argument (where a failure is swallowed under
# pipefail) and as a direct assignment (where the same failure would trip `set -e`
# and abort with a generic error instead of the intended DRIFT report). Two
# opposite failure behaviours from one helper is a trap; returning a sentinel in
# every case makes the two call shapes behave identically.
sha_of() {
  if [ ! -f "$1" ]; then printf 'ABSENT'; return 0; fi
  local h
  h=$(shasum -a 256 "$1" 2>/dev/null | awk '{print $1}') || h=""
  if [ -z "$h" ]; then printf 'UNREADABLE'; else printf '%s' "$h"; fi
  return 0
}

# `claude-experiment` and `claude-promote` live in the live config's bin/, which
# is on the operator's interactive PATH but not on a non-interactive one. Put it
# on PATH rather than refusing over a shell-configuration difference — the plan
# asks us to assert the deployment chain exists, not to assert how the caller's
# PATH was built.
export PATH="$LIVE_CONFIG/bin:$PATH"

require_env() {  # source ENV and validate; every verb after preflight calls this
  SNAP_DIR="${SNAP_DIR:?SNAP_DIR must be set — run \`plan_env.sh preflight\` and export the path it prints}"
  [ -f "$SNAP_DIR/ENV" ] || die "no ENV at $SNAP_DIR/ENV — run preflight first"
  # shellcheck source=/dev/null
  . "$SNAP_DIR/ENV"
  PY="${PY:?ENV did not define PY}"
  SNAP_DIR="${SNAP_DIR:?ENV did not define SNAP_DIR}"
  STATE_DIR="${STATE_DIR:?ENV did not define STATE_DIR}"
  CLAUDE_CONFIG_DIR="${CLAUDE_CONFIG_DIR:?ENV did not define CLAUDE_CONFIG_DIR}"
  CLAUDE_CONFIG_DIR_PARENT="${CLAUDE_CONFIG_DIR_PARENT:?ENV did not define CLAUDE_CONFIG_DIR_PARENT}"
  [ -d "$CLAUDE_CONFIG_DIR" ] || die "CLAUDE_CONFIG_DIR is not a directory: $CLAUDE_CONFIG_DIR"
  # Re-assert the clone guard here too, not only in preflight. Every other verb
  # reaches its config path through this function and would otherwise TRUST a
  # hand-edited or stale ENV file naming live config -- the one guard the whole
  # delivery model rests on, skipped by six of the seven verbs. Defence in depth,
  # and it costs a string comparison.
  assert_not_live_config
  # EXPORT, not merely assign. `gate`'s isolation proof reads CLAUDE_CONFIG_DIR
  # from os.environ; sourcing ENV only sets a shell variable, so without this the
  # proof died on KeyError for EVERY invocation — a gate that always refuses,
  # which is the same defect as a gate that cannot fail wearing the other face.
  export PY SNAP_DIR STATE_DIR CLAUDE_CONFIG_DIR CLAUDE_CONFIG_DIR_PARENT
}

# --------------------------------------------------------------------------- #
# preflight
# --------------------------------------------------------------------------- #

assert_not_live_config() {
  # THE FIRST CHECK, BEFORE ANYTHING ELSE. The entire delivery model assumes a
  # `claude-experiment` clone. A slice run in a default shell would edit the LIVE
  # hooks that every session loads.
  local cfg="${CLAUDE_CONFIG_DIR:-}"
  [ -n "$cfg" ] || die "CLAUDE_CONFIG_DIR is unset — this plan must run in a claude-experiment clone, never against live config"
  [ -d "$cfg" ] || die "CLAUDE_CONFIG_DIR is not a directory: $cfg"
  local rcfg rlive
  rcfg=$(realpath_of "$cfg")
  rlive=$(realpath_of "$LIVE_CONFIG")
  if [ "$rcfg" = "$rlive" ]; then
    printf '[plan_env] REFUSED: CLAUDE_CONFIG_DIR resolves to the LIVE config dir.\n' >&2
    printf '           CLAUDE_CONFIG_DIR = %s\n' "$rcfg" >&2
    printf '           live config       = %s\n' "$rlive" >&2
    printf '           Spawn a clone first:  claude-experiment spawn <label> --refresh\n' >&2
    exit 1
  fi
}

assert_tools() {
  local t missing=0
  for t in git gh claude-experiment claude-promote shasum mktemp cp; do
    if ! command -v "$t" >/dev/null 2>&1; then
      warn "required tool not found: $t"
      missing=1
    fi
  done
  # `gh` is required because `claude-promote` shells out to `gh pr create` /
  # `gh pr merge`. Asserting the wrapper without the tool it shells out to leaves
  # the same gap round 7 closed for `claude-experiment`, one link further down.
  [ "$missing" -eq 0 ] || die "missing tools above — the deployment chain would strand the work after five slices"

  # Non-blocking: an unauthenticated `gh` is the last surviving way the final
  # stage can stall on a prompt. A warning at S0 is worth six slices of work.
  if ! gh auth status >/dev/null 2>&1; then
    warn "\`gh auth status\` is not clean — \`claude-promote\`'s \`gh pr create\`/\`gh pr merge\` may prompt at cutover. Not blocking."
  fi
}

discover_py() {
  # Assert the interpreter's capability programmatically; do not hardcode it and
  # do not merely describe it. pytest-timeout is the ONLY timeout mechanism
  # available here (`timeout` and `gtimeout` are both absent on this machine), so
  # an interpreter carrying pytest but NOT pytest_timeout must be REJECTED.
  local cands c has_pytest
  cands="${PLAN_ENV_PY_CANDIDATES:-/usr/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3}"
  has_pytest=""
  for c in $cands; do
    command -v "$c" >/dev/null 2>&1 || continue
    "$c" -c 'import pytest' >/dev/null 2>&1 || continue
    has_pytest="$c"
    if "$c" -c 'import pytest_timeout' >/dev/null 2>&1; then
      printf '%s' "$c"
      return 0
    fi
  done
  if [ -n "$has_pytest" ]; then
    printf '[plan_env] REFUSED: no interpreter satisfies `import pytest, pytest_timeout`.\n' >&2
    printf '           %s HAS pytest but is MISSING pytest_timeout.\n' "$has_pytest" >&2
    printf '           pytest-timeout is the only timeout mechanism here (timeout/gtimeout are both absent).\n' >&2
    printf '           Remedy:  %s -m pip install --no-input pytest-timeout\n' "$has_pytest" >&2
  else
    printf '[plan_env] REFUSED: no candidate interpreter has pytest at all.\n' >&2
    printf '           candidates tried: %s\n' "$cands" >&2
    printf '           Remedy:  <interpreter> -m pip install --no-input pytest pytest-timeout\n' >&2
  fi
  return 1
}

probe_quiescence() {
  # Reuse the SHIPPED staleness rule rather than inventing a second one. A
  # hand-rolled `stat`-based shell test would create a staleness notion that could
  # disagree with the one the lock layer actually enforces.
  local py="$1" hooks="$2"
  "$py" - "$hooks" <<'PYQ'
import os, sys, datetime as dt
sys.path.insert(0, sys.argv[1])          # taskmanagement is NOT on the default path
try:
    import taskmanagement as tm
except Exception as e:                    # noqa: BLE001 - any import problem is fatal here
    print(f"[plan_env] REFUSED: cannot import taskmanagement from {sys.argv[1]}: {e}",
          file=sys.stderr)
    raise SystemExit(1)

stale_t = tm._stale_t_seconds()
now = dt.datetime.now(dt.timezone.utc)
blocking = []
locks_dir = tm.LOCKS_DIR
if locks_dir.is_dir():
    for p in sorted(locks_dir.glob("*.lock")):
        try:
            import json
            data = json.loads(p.read_text())
            hb = data.get("last_heartbeat")
            if not hb:
                continue
            age = (now - tm._parse_iso(hb)).total_seconds()
        except Exception:                 # noqa: BLE001 - an unreadable lock is not a block
            continue
        if age < stale_t:
            blocking.append((p.stem, int(age), data.get("session_id")))

if blocking:
    print("[plan_env] REFUSED: the state dir is not quiescent — `cp -a` is not atomic,",
          file=sys.stderr)
    print("           so a session writing mid-copy yields a torn snapshot.", file=sys.stderr)
    for key, age, sid in blocking:
        print(f"           holding lock: {key}  heartbeat age {age}s (< {stale_t}s)"
              f"  session {sid}", file=sys.stderr)
    print(f"           Wait, release the lock, or lower the window for one run:", file=sys.stderr)
    print(f"             TM_STALE_T_SECONDS=<seconds> plan_env.sh preflight", file=sys.stderr)
    raise SystemExit(1)
print(f"[plan_env] quiescent: no lock heartbeat inside {stale_t}s in {locks_dir}")
PYQ
}

# Count / list the snapshotted record files WITHOUT a pipeline that can abort the
# script. Both use a bash array, which simply comes back empty when the glob does not
# expand — no `ls`, no failing pipe stage, no `set -e` surprise. `nullglob` is set and
# restored locally so an unexpanded pattern never leaks through as a literal.
count_records() {
  local n=0 _l
  # Process substitution, not a pipe: a pipeline's exit status under `pipefail`
  # is what made the original one-liner able to kill the script.
  while IFS= read -r _l; do n=$((n + 1)); done < <(list_records "$1")
  printf '%s' "$n"
}

list_records() {
  local d="$1" f
  shopt -s nullglob
  for f in "$d"/*__*.json; do basename "$f"; done
  # `_reaped/` is counted too: the incident this guard exists for lost 63 of 65
  # entries there — 97%, worse than the 94% it lost from the top level — and a
  # manifest that globs only the top level would miss a repeat of the very
  # incident its own comment cites.
  for f in "$d"/_reaped/*; do printf '_reaped/%s\n' "$(basename "$f")"; done
  shopt -u nullglob
}

cmd_preflight() {
  assert_not_live_config            # FIRST, before anything else

  # Contract order: refuse live config, THEN discover the interpreter, THEN assert
  # the deployment chain. Both must pass either way, but matching the stated order
  # keeps one less prose-vs-code divergence in the artifact built to end them.
  local py
  py=$(discover_py) || exit 1
  assert_tools
  log "interpreter: $py (pytest $("$py" -c 'import pytest;print(pytest.__version__)'))"

  probe_quiescence "$py" "$CLAUDE_CONFIG_DIR/hooks"

  # STATE_DIR is the LIVE record store — the snapshot must be of the real
  # population this plan measures, not of the clone's copy.
  STATE_DIR="${STATE_DIR:-$LIVE_CONFIG/state/pre_plan_gates}"
  [ -d "$STATE_DIR" ] || die "STATE_DIR is not a directory: $STATE_DIR"

  # Snapshot lifetime: create ONCE, reuse thereafter, never auto-delete. A retry
  # that re-runs preflight with SNAP_DIR unset mints a second directory — an
  # accepted, bounded cost. Teardown is manual, by policy.
  if [ -n "${SNAP_DIR:-}" ]; then
    # REFUSE TO SILENTLY RE-BASELINE A LOST SNAP_DIR. An operator who re-exports a
    # SNAP_DIR path that no longer has its contents -- a reboot, a /var/folders
    # sweep, a premature manual cleanup -- would otherwise get a brand-new
    # DIGEST_BASELINE derived from whatever LIVE looks like at THAT moment, not
    # from the clone-spawn moment. Any concurrent edit that landed in between is
    # then absorbed as the baseline, cutover-check passes clean, and the carry
    # still reverts that edit. Silent, and it destroys a third party's work in the
    # one guard built to prevent exactly that. A reused SNAP_DIR must therefore
    # carry BOTH its ENV and its write-once baseline, or this is not a reuse.
    if [ -d "$SNAP_DIR" ] && [ -f "$SNAP_DIR/ENV" ] && [ ! -f "$SNAP_DIR/DIGEST_BASELINE" ]; then
      die "SNAP_DIR $SNAP_DIR has an ENV but NO DIGEST_BASELINE — its contents were lost.
           Re-baselining now would silently adopt current live as the S0 baseline and
           mask any concurrent edit made since. Start a fresh snapshot instead:
             unset SNAP_DIR && plan_env.sh preflight
           and re-run the slices' gates against it."
    fi
    if [ -d "$SNAP_DIR" ] && [ ! -f "$SNAP_DIR/ENV" ] && [ -n "$(ls -A "$SNAP_DIR" 2>/dev/null)" ]; then
      die "SNAP_DIR $SNAP_DIR is non-empty but has no ENV — refusing to write into a
           directory this script did not create. Use an empty path, or unset SNAP_DIR."
    fi
    # REFUSE A SNAPSHOT WHOSE RECORDS WERE SWEPT OUT FROM UNDER IT.
    #
    # The guard above catches "ENV survived, baseline lost". It does NOT catch the
    # case that actually happened: ENV *and* DIGEST_BASELINE both survived while the
    # RECORD POPULATION was deleted — a /var/folders age sweep keeps whatever was
    # touched recently (ENV, the digests, the shim) and removes the rest. The
    # snapshot then holds a recency prefix of itself: measured 2026-08-22, 6 of 106
    # records and 2 of 65 `_reaped/` entries, every survivor mtime-identical to its
    # live twin. Nothing noticed, because `gate`, `digest-assert` and `cutover-check`
    # never read the records — only A6/S5's bucket comparison does, and by then the
    # fixture it compares against is silently gone.
    #
    # Exact, not heuristic: SNAPSHOT_MANIFEST records what was copied, so this
    # compares the snapshot against ITSELF rather than guessing from live (live
    # legitimately grows and shrinks underneath a snapshot; the snapshot must not).
    if [ -f "$SNAP_DIR/SNAPSHOT_MANIFEST" ]; then
      local want_n have_n missing
      want_n=$(grep -c . "$SNAP_DIR/SNAPSHOT_MANIFEST" 2>/dev/null || printf 0)
      # NOT `ls ...|wc -l`. With ZERO matching files the glob does not expand,
      # `ls` fails, `pipefail` propagates, and this unguarded assignment aborts the
      # whole script under `set -e` with NO OUTPUT AT ALL — the error having gone
      # to /dev/null. It would fire on TOTAL wipeout: the worst case, silently, in
      # the guard written to catch it. `count_records` is failure-tolerant by
      # construction and returns 0 for "none".
      have_n=$(count_records "$SNAP_DIR")
      if [ "$have_n" -lt "$want_n" ]; then
        missing=$((want_n - have_n))
        die "SNAP_DIR $SNAP_DIR has LOST $missing of its $want_n snapshotted records
           (it now holds $have_n). Its ENV and DIGEST_BASELINE survived, so the earlier
           guards passed — but the record population this plan measures is gone, and a
           bucket comparison against it would be vacuous rather than wrong-looking.
           Most likely a /var/folders age sweep. Start a fresh snapshot:
             unset SNAP_DIR && plan_env.sh preflight
           then re-record the bucket baseline and re-run the slices' gates."
      fi
    fi
    # A pre-guard snapshot has no manifest, so the loss check above cannot run and
    # one will be minted below from whatever is on disk NOW. Say so: any records
    # already lost would otherwise be locked in as "the baseline" with no signal.
    if [ -d "$SNAP_DIR" ] && [ -f "$SNAP_DIR/ENV" ] \
       && [ ! -f "$SNAP_DIR/SNAPSHOT_MANIFEST" ]; then
      warn "reusing a snapshot minted BEFORE the manifest guard existed — its
           current $(count_records "$SNAP_DIR") records will be adopted as the baseline,
           and any lost before now cannot be detected. Prefer a fresh snapshot
           (unset SNAP_DIR) unless you know this one is intact."
    fi
    mkdir -p "$SNAP_DIR"
    log "reusing SNAP_DIR from the environment: $SNAP_DIR"
  else
    SNAP_DIR=$(mktemp -d)             # not a guessable path (TOCTOU)
    log "created SNAP_DIR: $SNAP_DIR"
  fi

  # BUILD the home shim — it cannot be discovered. `claude-experiment` clones to
  # ~/.claude-staging-<label>/, whose basename is not `.claude`, so NO directory
  # on disk has the clone as its `.claude` child. A symlink, not a copy, so the
  # clone stays the single tree under edit and `gate` cannot drift onto a second.
  mkdir -p "$SNAP_DIR/home"
  ln -sfn "$CLAUDE_CONFIG_DIR" "$SNAP_DIR/home/.claude"
  CLAUDE_CONFIG_DIR_PARENT="$SNAP_DIR/home"

  # ENV is the SINGLE carrier. Slices do not share a shell, so nothing survives as
  # an env var; every later verb reads this file back. Do not add a second carrier.
  cat > "$SNAP_DIR/ENV" <<ENVEOF
PY="$py"
SNAP_DIR="$SNAP_DIR"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$CLAUDE_CONFIG_DIR_PARENT"
ENVEOF
  log "wrote $SNAP_DIR/ENV"

  # DIGEST_BASELINE is WRITE-ONCE: the state of the LIVE write-target files at the
  # moment the clone was spawned. It is what `cutover-check` compares live against
  # to detect a third-party edit. It must never be confused with DIGEST_ROLLING,
  # which S1-S5 legitimately advance — comparing live against the rolling digest
  # would false-block every single deployment.
  if [ -f "$SNAP_DIR/DIGEST_BASELINE" ]; then
    log "DIGEST_BASELINE already exists (write-once) — left untouched"
  else
    : > "$SNAP_DIR/DIGEST_BASELINE"
    local f h
    for f in $WRITE_TARGETS; do
      h=$(sha_of "$LIVE_CONFIG/$f")
      # ABSENT is expected (S1/S3 create three of these). UNREADABLE is NOT: it
      # means the baseline for that file is unmeasurable, so `cutover-check` can
      # never prove byte-identity for it. Say so now rather than at the cutover.
      [ "$h" = "UNREADABLE" ] && warn "baseline for $f is UNMEASURABLE — cutover-check will refuse to clear it"
      printf '%s  %s\n' "$h" "$f" >> "$SNAP_DIR/DIGEST_BASELINE"
    done
    log "wrote $SNAP_DIR/DIGEST_BASELINE ($(grep -c . "$SNAP_DIR/DIGEST_BASELINE") write targets, live copies)"
  fi

  # Non-blocking clone-freshness report. `cutover-check` compares LIVE against the
  # baseline, so it cannot see a clone that was already stale when it was spawned —
  # and carrying a stale clone is the one way this plan can revert someone else's
  # work. Reported, not gated, because the remedy (`claude-experiment refresh`) is
  # the operator's call.
  local drifted=0 f
  for f in $ROLLING_TARGETS; do
    if [ -f "$LIVE_CONFIG/$f" ] && [ -f "$CLAUDE_CONFIG_DIR/$f" ]; then
      if ! cmp -s "$LIVE_CONFIG/$f" "$CLAUDE_CONFIG_DIR/$f"; then
        warn "clone base differs from live for $f — re-spawn with --refresh before carrying"
        drifted=1
      fi
    fi
  done
  [ "$drifted" -eq 0 ] && log "clone base matches live on both production modules"

  # The read-only state snapshot. `cp -a` (not -R) preserves mode/mtime; the
  # trailing `/.` keeps it idempotent, where a bare `cp -a src dst` would nest on a
  # second run. Never `mv`, never `--apply`.
  # THE SNAPSHOT IS A FROZEN FIXTURE — do not re-sync one that already exists.
  # `cp -a` ran unconditionally, including on the sanctioned REUSE path, so a
  # second `preflight` against an exported SNAP_DIR quietly absorbed whatever live
  # records had appeared since. The manifest is write-once, so the count stayed
  # put while the contents moved — and `buckets` would then blame a code change
  # for a drift that a re-run had caused. Copy only when minting.
  if [ -f "$SNAP_DIR/SNAPSHOT_MANIFEST" ]; then
    log "snapshot already present ($(count_records "$SNAP_DIR") records) — NOT re-syncing from live"
  else
    cp -a "$STATE_DIR/." "$SNAP_DIR/"
    # ROOT-CAUSE FIX 2026-08-25 — the `/var/folders` age sweep, THREE occurrences.
    #
    # `cp -a` preserves mtime (the comment above says so). Topic records are months
    # old, so their copies are born ALREADY PAST macOS's ~3-day `/var/folders`
    # cleaner threshold and are swept at its next run -- usually within a day. The
    # control files (`ENV`, `DIGEST_BASELINE`, `BUCKET_BASELINE`, `SNAPSHOT_MANIFEST`,
    # `DIGEST_ROLLING`) are written FRESH by this function, carry current mtimes, and
    # survive. That asymmetry is the observed signature every time:
    #   tmp.tBxCZAxU13  -- retired
    #   tmp.yyFm0s7upv  --   5 of 171 manifest entries survived  (S5.BLOCKERS-CLEARED)
    #   tmp.Dpx6I6Tyh8  --   3 of 106 record copies survived     (S6.EVIDENCE FINDING 2)
    #
    # So the fixture mechanism was incompatible with `/var/folders` BY CONSTRUCTION,
    # and every "transient" re-mint was rebuilding something guaranteed to rot again.
    #
    # `touch` is chosen over the sibling repair (site the snapshot outside
    # `/var/folders`) because it is local to this one line and changes no path
    # contract: `SNAP_DIR` is still `mktemp -d`, still recorded in ENV, still read
    # back by every later slice, and the TOCTOU property `mktemp -d` buys is kept.
    # Relocating would have moved a path five slices already depend on.
    #
    # SAFETY: this touches only the COPIES under "$SNAP_DIR", never "$STATE_DIR".
    # The snapshot is a frozen fixture whose mtimes carry no meaning -- nothing in
    # this script reads a record's mtime; `list_records`/`count_records` key on the
    # name, and `_classify_records` on the body. Live records are NOT touched, so
    # `find-mis-keyed`'s live read is unaffected.
    find "$SNAP_DIR" -exec touch {} + \
      || warn "could not refresh snapshot mtimes — the /var/folders cleaner may sweep this snapshot"
    # WRITE-ONCE manifest of what was copied — the reuse guard compares the
    # snapshot against THIS, not against live, because live legitimately grows
    # and shrinks underneath a snapshot while the snapshot must not.
    list_records "$SNAP_DIR" > "$SNAP_DIR/SNAPSHOT_MANIFEST"
    log "snapshot: $(count_records "$SNAP_DIR") records copied into $SNAP_DIR (mtimes refreshed against the /var/folders sweep)"
  fi

  cmd_digest_record --quiet

  cat <<EOF

[plan_env] preflight OK.

  Carry this ONE value into every later slice:

      export SNAP_DIR="$SNAP_DIR"

  ENV              $SNAP_DIR/ENV
  DIGEST_BASELINE  $SNAP_DIR/DIGEST_BASELINE   (write-once; the cutover's reference)
  DIGEST_ROLLING   $SNAP_DIR/DIGEST_ROLLING    (per-slice; advanced by digest-record)
  HOME shim        $CLAUDE_CONFIG_DIR_PARENT   (its .claude -> $CLAUDE_CONFIG_DIR)
  snapshot         $SNAP_DIR

  SNAP_DIR is NEVER auto-deleted (safe-defaults.md bans \`rm -rf\`). Remove it
  yourself once the plan has landed.
EOF
}

# --------------------------------------------------------------------------- #
# digest-record / digest-assert — the rolling per-slice chain over the CLONE
# --------------------------------------------------------------------------- #

rolling_now() {  # current hashes of the clone's rolling targets
  local f
  for f in $ROLLING_TARGETS; do
    printf '%s  %s\n' "$(sha_of "$CLAUDE_CONFIG_DIR/$f")" "$f"
  done
}

cmd_digest_record() {
  local quiet=0 reason=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --quiet) quiet=1; shift ;;
      --rebaseline-digest) reason="${2:-}"; shift 2 ;;
      *) die "digest-record: unknown flag $1" ;;
    esac
  done
  require_env
  if [ -n "$reason" ]; then
    log "re-baselining the rolling digest. Reason: $reason"
    printf '# rebaselined %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$reason" \
      >> "$SNAP_DIR/DIGEST_ROLLING.log"
  fi
  rolling_now > "$SNAP_DIR/DIGEST_ROLLING"
  [ "$quiet" -eq 1 ] || log "recorded DIGEST_ROLLING over the clone's $(grep -c . "$SNAP_DIR/DIGEST_ROLLING") modules"
}

cmd_digest_assert() {
  local escape=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --rebaseline-digest)   escape="rebaseline: ${2:-}"; shift 2 ;;
      --resume-past-own-run) escape="resume: ${2:-}";     shift 2 ;;
      *) die "digest-assert: unknown flag $1" ;;
    esac
  done
  require_env
  [ -f "$SNAP_DIR/DIGEST_ROLLING" ] || die "no DIGEST_ROLLING — run preflight (or digest-record) first"
  local tmp; tmp="$SNAP_DIR/.rolling.now.$$"
  rolling_now > "$tmp"
  if diff -q "$SNAP_DIR/DIGEST_ROLLING" "$tmp" >/dev/null 2>&1; then
    mv "$tmp" "$SNAP_DIR/.rolling.last-ok"
    log "digest-assert OK — the clone's modules are exactly what the previous slice left"
    return 0
  fi
  printf '[plan_env] HALT: the clone changed outside a recorded slice.\n' >&2
  diff "$SNAP_DIR/DIGEST_ROLLING" "$tmp" >&2 || true
  mv "$tmp" "$SNAP_DIR/.rolling.mismatch"
  if [ -n "$escape" ]; then
    warn "proceeding under an explicit, recorded escape — $escape"
    printf '# escape %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$escape" \
      >> "$SNAP_DIR/DIGEST_ROLLING.log"
    cmd_digest_record --quiet
    return 0
  fi
  printf '           If this is a legitimate manual fix, re-record it explicitly:\n' >&2
  printf '             plan_env.sh digest-assert --rebaseline-digest "<why>"\n' >&2
  printf '           If this is this plan'"'"'s own crashed run, resume explicitly:\n' >&2
  printf '             plan_env.sh digest-assert --resume-past-own-run "<why>"\n' >&2
  exit 1
}

# --------------------------------------------------------------------------- #
# cutover-check — LIVE vs DIGEST_BASELINE, over the FULL write-target list
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# buckets — A0's bucket baseline, made executable
# --------------------------------------------------------------------------- #
#
# A0's validation gate says the mis-keyed/inverted buckets reproduce as `0 / 0` and
# that whatever unresolvable count the snapshot yields is RECORDED "as the baseline
# later slices compare against — it is recorded, not asserted equal to a constant."
# That clause had no verb, no artifact, and no mention in S0's evidence: a promise in
# prose that nothing verified, which is the exact failure class this script exists to
# end. Recording it in code is what lets A6/S5 prove its new bucket is ADDITIVE —
# comparing the three existing buckets before and after ITS OWN code change, on ONE
# snapshot, rather than against a date-stamped constant that drifts with live sessions.
cmd_buckets() {
  require_env
  local reaper="$CLAUDE_CONFIG_DIR/hooks/reap_orphan_plain_topics.py"
  [ -f "$reaper" ] || die "reaper not found at $reaper"
  [ -d "$SNAP_DIR" ] || die "SNAP_DIR is not a directory: $SNAP_DIR"

  local out
  out=$("$PY" "$reaper" find-mis-keyed --state-dir "$SNAP_DIR" 2>&1) || {
    printf '%s\n' "$out" >&2
    die "find-mis-keyed failed against the snapshot"
  }

  # Parse the three headline counts. A bucket with zero members prints no line at
  # all, so an absent label is 0 — not a parse failure.
  # The report text arrives as ARGV, not on stdin. `"$PY" -` already reads its
  # PROGRAM from stdin, so piping the text in as well makes the two compete: the
  # heredoc wins, `sys.stdin.read()` returns empty, and the verb dies on SIGPIPE
  # with no output at all. (It did.)
  local now
  now=$("$PY" - "$out" <<'PYB'
import re
import sys

text = sys.argv[1]


def count(label):
    m = re.search(re.escape(label) + r"[^\n]*?(\d+)\s+(?:slug|record)\(s\)", text)
    return int(m.group(1)) if m else 0


print("reconcilable %d" % count("RECONCILABLE"))
print("inverted %d" % count("INVERTED"))
print("unresolvable %d" % count("UNRESOLVABLE"))
PYB
  )

  if [ ! -f "$SNAP_DIR/BUCKET_BASELINE" ]; then
    # Atomic: write-then-rename. A crash mid-write would otherwise leave a file
    # that EXISTS (so the next run asserts against it) but is truncated — failing
    # loudly, but with a diagnosis that sends the operator after a code change
    # that never happened.
    printf '%s\n' "$now" > "$SNAP_DIR/.BUCKET_BASELINE.tmp"
    mv "$SNAP_DIR/.BUCKET_BASELINE.tmp" "$SNAP_DIR/BUCKET_BASELINE"
    log "recorded BUCKET_BASELINE against the snapshot:"
    printf '%s\n' "$now" | sed 's/^/           /'
    log "later slices assert ADDITIVITY against this file, never against a constant"
    return 0
  fi

  if [ "$now" = "$(cat "$SNAP_DIR/BUCKET_BASELINE")" ]; then
    log "buckets OK — the snapshot's three existing buckets are unchanged"
    printf '%s\n' "$now" | sed 's/^/           /'
    return 0
  fi

  printf '[plan_env] BUCKETS DRIFTED against the recorded baseline.\n' >&2
  printf '  recorded:\n' >&2
  sed 's/^/    /' "$SNAP_DIR/BUCKET_BASELINE" >&2
  printf '  now:\n' >&2
  printf '%s\n' "$now" | sed 's/^/    /' >&2
  printf '  The snapshot is a frozen fixture, so its buckets cannot move on their own.\n' >&2
  printf '  Either the snapshot changed, or a code edit changed how records classify —\n' >&2
  printf '  and the second is exactly what A6 must prove it did NOT do.\n' >&2
  exit 1
}

cmd_cutover_check() {
  require_env
  [ -f "$SNAP_DIR/DIGEST_BASELINE" ] || die "no DIGEST_BASELINE — run preflight first"
  # This is the one place the plan can DESTROY third-party work. The rolling
  # digest is measured on the clone and is structurally blind to the concurrent
  # session editing live; a clone spawned at S0 and carried hours later has a
  # stale base, and writing it over live overwrites whatever landed in between.
  local drift=0 want have f
  while read -r want f; do
    [ -n "${f:-}" ] || continue
    have=$(sha_of "$LIVE_CONFIG/$f")
    # UNREADABLE is a FAILURE TO MEASURE, not a measurement. Two of them compare
    # equal as plain strings, which would let this verb print "every write target
    # is byte-identical" for a file it never actually compared -- a false all-clear
    # in the one check standing between this plan and destroying a third party's
    # work. ABSENT-vs-ABSENT is different and legitimately equal: the file was not
    # there at either end, so nothing drifted.
    if [ "$want" = "UNREADABLE" ] || [ "$have" = "UNREADABLE" ]; then
      printf '[plan_env] UNMEASURABLE: %s\n' "$f" >&2
      printf '             baseline %s / live now %s — cannot prove byte-identity\n' \
             "$want" "$have" >&2
      drift=1
      continue
    fi
    if [ "$want" != "$have" ]; then
      printf '[plan_env] DRIFT: %s\n' "$f" >&2
      printf '             baseline %s\n' "$want" >&2
      printf '             live now %s\n' "$have" >&2
      drift=1
    fi
  done < "$SNAP_DIR/DIGEST_BASELINE"
  if [ "$drift" -ne 0 ]; then
    printf '[plan_env] HALT: live has moved since the clone was spawned.\n' >&2
    printf '           Carrying now would revert the drifted file(s) above.\n' >&2
    printf '           Run `claude-experiment refresh` (or re-spawn), re-run the\n' >&2
    printf '           slices'"'"' gates, and only then carry.\n' >&2
    exit 1
  fi
  log "cutover-check OK — every write target is byte-identical to the S0 baseline"
}

# --------------------------------------------------------------------------- #
# gate — the baseline suite, run against the CLONE and PROVEN to be
# --------------------------------------------------------------------------- #

cmd_gate() {
  # --prove-only runs the isolation proof and stops. It exists so `selftest` can
  # exercise the REAL verb's REAL proof block in both directions cheaply. The
  # first version of this script proved the positive case by re-running equivalent
  # python inline instead, and that is precisely how a KeyError that made `gate`
  # fail on EVERY invocation went unnoticed: the negative case refused (for the
  # wrong reason) and the positive case never touched the verb.
  # --resolve-only goes one step further: it resolves the test-file list (both
  # loops, including the NEW_TESTS present/lost/not-yet decision) and stops
  # before pytest. Same reason as --prove-only — it lets `selftest` exercise the
  # REAL loop in all three of its directions without paying a 64-test run per
  # case, instead of re-implementing the decision inline where a divergence
  # between the copy and the verb would go unseen.
  local prove_only=0 resolve_only=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --prove-only)   prove_only=1;   shift ;;
      --resolve-only) resolve_only=1; shift ;;
      *) die "gate: unknown flag $1" ;;
    esac
  done
  require_env
  # WITHOUT THE HOME EXPORT THIS GATE CANNOT FAIL. The eight baseline files
  # hardcode `Path.home()/".claude"/"hooks"`, and `claude-experiment` sets
  # CLAUDE_CONFIG_DIR, NOT HOME — so exporting CLAUDE_CONFIG_DIR alone runs the
  # clone's COPIES of the tests against the LIVE, unfixed modules, and the suite
  # passes because nothing under test changed. HOME is what `Path.home()` reads,
  # so HOME is what has to move. The same move also redirects TOPIC_STATE_DIR and
  # tm.LOCKS_DIR, which are Path.home()-derived too: pre_plan_gates.py never reads
  # CLAUDE_CONFIG_DIR at all.
  # ...but moving HOME also moves the USER SITE-PACKAGES, and on this machine
  # pytest lives there (~/Library/Python/3.9/lib/python/site-packages), not in the
  # interpreter's own tree. So the plan's reference gate block, exactly as
  # written, dies on "No module named pytest" — a defect that survived eleven
  # review rounds because the block had never been run. Resolve the real user site
  # BEFORE the override and put it on PYTHONPATH. This does not weaken isolation:
  # it supplies third-party packages only, while the modules UNDER TEST still
  # resolve through Path.home() -> shim -> clone, which the proof below asserts.
  local user_site
  user_site=$("$PY" -c 'import site; print(site.getusersitepackages())')
  export PYTHONPATH="${user_site}${PYTHONPATH:+:$PYTHONPATH}"
  export HOME="$CLAUDE_CONFIG_DIR_PARENT"

  # Prove it rather than assume it — a gate that cannot fail is not a gate.
  "$PY" - <<'PROVE' || exit 1
import os, sys, pathlib
p = pathlib.Path.home() / ".claude" / "hooks" / "pre_plan_gates.py"
want = pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "hooks" / "pre_plan_gates.py"
if not p.exists():
    sys.exit(f"ISOLATION FAILED: {p} does not exist — the HOME shim is not built")
if p.resolve() != want.resolve():
    sys.exit(f"ISOLATION FAILED: suite would import {p}, not {want}")
# The comment above claims the HOME move also redirects the module's STATE dir.
# Prove that too rather than asserting it: pre_plan_gates.py derives
# TOPIC_STATE_DIR from Path.home(), so if the shim did not carry it, the suite
# would import the clone's code and then read and WRITE the live record store --
# which is the exact live-state pollution this plan is built to avoid.
sys.path.insert(0, str(want.parent))
import importlib
_ppg = importlib.import_module("pre_plan_gates")
_state = pathlib.Path(_ppg.TOPIC_STATE_DIR).resolve()
_clone_state = (pathlib.Path(os.environ["CLAUDE_CONFIG_DIR"]) / "state"
                / "pre_plan_gates").resolve()
if _state != _clone_state:
    sys.exit(f"ISOLATION FAILED: TOPIC_STATE_DIR resolves to {_state}, "
             f"not {_clone_state}")
# The HOME override also moves the user site-packages, where pytest lives here.
# Assert the test runner survives the move, in the same block that proves the
# isolation -- otherwise `gate` proves it is pointed at the right tree and then
# dies before running a single test, which is what it did.
try:
    import pytest, pytest_timeout          # noqa: F401
except ImportError as e:
    sys.exit(f"RUNNER UNAVAILABLE under the shim HOME: {e}. "
             f"PYTHONPATH={os.environ.get('PYTHONPATH','')!r}")
print(f"[plan_env] isolation OK: {p}")
PROVE

  [ "$prove_only" -eq 0 ] || { log "--prove-only: isolation proven, stopping before the suite"; return 0; }

  export CI=1                       # non-interactive: no pagers, no prompts
  local files="" t
  for t in $BASELINE_TESTS; do
    [ -f "$CLAUDE_CONFIG_DIR/hooks/tests/$t" ] || die "baseline test missing from the clone: $t"
    files="$files $CLAUDE_CONFIG_DIR/hooks/tests/$t"
  done
  # NEW_TESTS carries `<file>:<creating-slice>`. Three cases, and every one of
  # them SAYS something — the bug this replaces had a bare `if [ -f ]` with no
  # else arm, so a new test that had been created and then LOST was skipped in
  # total silence and `gate` still exited 0 reporting green.
  local nt tf ts st
  for nt in $NEW_TESTS; do
    # A malformed entry must refuse, not limp. Without this, `${nt%%:*}` and
    # `${nt##*:}` BOTH return the whole string when there is no colon, so the
    # filename would be used as the slice id and the loop would log the
    # nonsensical "created by <filename>, which has not completed" — non-silent,
    # but wrong, and it would read as a normal skip. Found by the round-6
    # conformance round as a latent gap in the pair format itself.
    case "$nt" in
      *:*) ;;
      *) die "malformed NEW_TESTS entry (expected '<file>:<creating-slice>'): $nt" ;;
    esac
    tf="${nt%%:*}"; ts="${nt##*:}"
    # A colon is necessary but NOT sufficient, and round 7 caught the gap: a
    # trailing colon ("file.py:") leaves an empty slice id, whose slice_state
    # lookup misses every key, returns `other`, and logs "created by , which has
    # not completed" — a nonsensical message that reads like a normal skip, i.e.
    # exactly the silence this whole arm exists to remove. A leading colon
    # (":S1") leaves an empty filename and skips the same way. Both halves must
    # be non-empty for the entry to mean anything.
    { [ -n "$tf" ] && [ -n "$ts" ]; } \
      || die "malformed NEW_TESTS entry (empty file or slice half): '$nt'
       expected '<file>:<creating-slice>', both halves non-empty"
    if [ -f "$CLAUDE_CONFIG_DIR/hooks/tests/$tf" ]; then
      files="$files $CLAUDE_CONFIG_DIR/hooks/tests/$tf"
      log "including new test file: $tf (created by $ts)"
      continue
    fi
    st="$(slice_state "$ts")"
    case "$st" in
      completed)
        # The creating slice is DONE, so this file existed and no longer does.
        # Refuse — this is the branch that makes the gate able to fail.
        die "new test missing but its creating slice $ts is COMPLETED: $tf
       expected at: $CLAUDE_CONFIG_DIR/hooks/tests/$tf
       run-state:   $PLAN_RUN_STATE
       A test that its own slice already shipped cannot be silently skipped.
       Restore the file, or — if it was removed deliberately — drop it from
       NEW_TESTS in this script so the removal is recorded rather than implied."
        ;;
      unknown)
        # CANNOT VERIFY => DO NOT CERTIFY. An earlier revision of this arm warned
        # on stderr and continued, which left `gate` exiting 0 — and the round-6
        # conformance round rightly called that the original defect wearing a
        # different costume: an automated caller reads the exit code, not stderr,
        # so a lost test plus an unreadable run-state still reported green. The
        # escape must name something that WORKS. An earlier wording said "point
        # PLAN_ENV_RUN_STATE at the run-state for this plan", which is circular
        # in the case this arm actually fires: a run outside a plan execution has
        # no run-state to point at, and pointing at a missing or empty file
        # returns `unknown` again and refuses identically. Verified by running it.
        # The remedy that works is an explicit empty declaration.
        die "cannot determine whether slice $ts has completed, and $tf is missing.
       run-state: $PLAN_RUN_STATE (absent, unparseable, or no interpreter)
       Refusing rather than skipping: with no run-state this cannot tell a test
       that has NOT BEEN CREATED YET from one that was created and then LOST,
       and certifying green on that ambiguity is the defect this arm exists to
       prevent.
       To proceed, declare the slice state explicitly — do NOT point this at a
       missing or empty file, which lands right back here:
         * running under a plan   -> PLAN_ENV_RUN_STATE=<that plan's run-state>
         * running outside a plan -> printf '{}\\n' > /tmp/no-run.json
                                     PLAN_ENV_RUN_STATE=/tmp/no-run.json
           ('{}' is a valid run-state recording that NO slice has completed, so
            every new test reads as not-yet-created rather than as unknowable.)"
        ;;
      *)
        log "new test not yet present, skipping: $tf (created by $ts, which has not completed)"
        ;;
    esac
  done
  [ "$resolve_only" -eq 0 ] || { log "--resolve-only: test list resolved, stopping before pytest"; return 0; }
  # shellcheck disable=SC2086
  "$PY" -m pytest --timeout=120 --timeout-method=signal $files -q </dev/null
}

# --------------------------------------------------------------------------- #
# anchors — make "re-measure by symbol" a command instead of an instruction
# --------------------------------------------------------------------------- #

# KNOWN-OPEN DEFECTS IN THIS VERB — read before trusting or extending it.
#   ~/.claude/state/execplan/receipts/0b346c93b5af/S0R3.RESIDUALS.md (R1-R8)
# They are real, none blocks the current plan, and they are recorded there rather
# than half-fixed here: every repair pass to this verb has introduced its own
# defect, so the deliberate stopping point is a tool that REFUSES what it cannot do
# safely. Named here because a maintainer reads the script, not the receipts dir —
# a checker pointed out that the residuals were accurate but undiscoverable.
# Headlines: the range arm bypasses the count guards (latent); `_scope_symbol`
# matches only column-0 defs, so methods and re-defined symbols are ungated;
# repair still picks the occurrence nearest the STALE number; binding exclusivity
# keys on the number, not the file; and every NEW_TESTS selftest case now uses the
# override, so the real declared list lost its coverage (R6 — fix this one first).
cmd_anchors() {
  local plan="$PLAN_FILE_DEFAULT" write=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --write) write=1; shift ;;
      --plan)  plan="${2:?--plan needs a path}"; shift 2 ;;
      *) die "anchors: unknown flag $1" ;;
    esac
  done
  require_env
  [ -f "$plan" ] || die "plan not found: $plan"
  # The override exists so `selftest` can point the matcher at a fixture plan
  # citing THIS script, instead of at the production modules -- which is what lets
  # the anchors verb be tested in both directions without inventing fake copies of
  # a 10,000-line module.
  local anchor_files="${PLAN_ENV_ANCHOR_FILES_OVERRIDE:-$ANCHOR_FILES}"
  # The backup stamp is minted HERE, in the shell, because the Python side is
  # deliberately time-free (a re-run must be reproducible) and because this is the
  # same `date` the rest of the script uses.
  local stamp; stamp=$(date -u +%Y%m%d%H%M%S)
  # Sibling of PLAN_ENV_ANCHOR_FILES_OVERRIDE, and needed for the same reason: this
  # verb sources ENV, which re-exports CLAUDE_CONFIG_DIR, so a caller cannot point
  # the matcher at a scratch tree by exporting that variable. `selftest` uses this to
  # scan a purpose-built fixture module (a deleted symbol surviving only in a
  # comment) without writing into the clone's own hooks/.
  local hooks_dir="${PLAN_ENV_HOOKS_DIR_OVERRIDE:-$CLAUDE_CONFIG_DIR/hooks}"
  # shellcheck disable=SC2086
  PLAN_ENV_STAMP="$stamp" "$PY" - "$plan" "$hooks_dir" "$write" $anchor_files <<'PYA'
"""Re-derive every line citation in the plan from the token(s) beside it.

Three citation shapes live in this plan, and a token-paired matcher sees only the
first two -- which is how four stale citations survived into Gate 1 and Gate 3 and
disagreed both with the Anchor snapshot AND with each other:

  (a) typed      `pre_plan_gates.py:647` ... `def _derive_topic_slug():`
  (b) bare       trailing secondary numbers on the same row -- `at 2463`,
                 `-> :2464`, `/ :2476` -- which carry no adjacent token
  (c) tabular    the Anchor snapshot, whose file is in the TABLE HEADER and whose
                 rows are `| <backticked symbol> | <N> |`

PAIRING RULE, and why it is deliberately loose. A first version paired each
citation with the nearest backticked token and produced 20 findings of which most
were mis-pairings -- a `[verified: file:N]` restatement at the end of a row would
bind to whatever token happened to precede it. A noisy guard is one that gets
routinely overridden, which is how a guard stops meaning anything. So a row is
treated as ONE factual claim: a citation resolves if ANY usable token on that row
appears within tolerance of it. Some sensitivity is traded for the elimination of
a whole false-positive class.

Read-only unless --write. Exits non-zero when anything genuinely fails to resolve.
"""
import os
import re
import sys
import pathlib

# Minted by the shell, not here: this module is deliberately time-free so a re-run
# is reproducible, and the backup stamp is the only thing that needs a clock.
STAMP = os.environ.get("PLAN_ENV_STAMP") or "unstamped"

plan_path = pathlib.Path(sys.argv[1])
hooks_dir = pathlib.Path(sys.argv[2])
do_write = sys.argv[3] == "1"

# Consumed from the shell's ANCHOR_FILES, never re-declared here (one list, one
# definition site).
KNOWN = tuple(sys.argv[4:])
if not KNOWN:
    raise SystemExit("anchors: no ANCHOR_FILES were passed in")
TOL = 5

text = plan_path.read_text()
lines = text.splitlines()

_cache = {}


def file_lines(name):
    if name not in _cache:
        _cache[name] = None
        for cand in (hooks_dir / name, hooks_dir / "tests" / name):
            if cand.is_file():
                _cache[name] = cand.read_text().splitlines()
                break
    return _cache[name]


def _prose_lines(name):
    """1-indexed line numbers that are PROSE rather than code: `#` comments and
    everything inside a triple-quoted block.

    `#`-only was not enough, and the gap was live: `pre_plan_gates.py` records a
    deleted symbol in a DOCSTRING, which starts with no `#`, so it counted as code
    and `--write` repaired a citation onto it — the exact defect the comment split
    was added to prevent, reached through the other kind of prose. A one-pass
    triple-quote toggle covers well-formed Python without a parser; a `#` inside a
    string literal is deliberately not chased, being both rare and harmless here.
    """
    key = ("__prose__", name)
    if key not in _cache:
        src = file_lines(name)
        if src is None:
            _cache[key] = set()
        else:
            prose, fence = set(), None
            for i, ln in enumerate(src, 1):
                s = ln.strip()
                if fence:
                    prose.add(i)
                    if fence in s:
                        fence = None
                    continue
                if s.startswith("#"):
                    prose.add(i)
                    continue
                # Strip an assignment target and any string PREFIX before testing
                # for the fence. `x = f"""…` and `HELP = rb'''…` do not START with
                # the quote, so a prefix-blind test never opens the fence and the
                # whole block reads as code — reopening the very defect this
                # function closes, via a different string flavour.
                s_q = re.sub(r"^[A-Za-z_][\w.]*\s*=\s*", "", s)
                s_q = re.sub(r"^[rRbBuUfF]{1,2}(?=[\"'])", "", s_q)
                for q in ('"""', "'''"):
                    if s_q.startswith(q):
                        prose.add(i)
                        # A one-line docstring opens and closes on the same line.
                        if not (len(s_q) > len(q) and s_q.endswith(q)):
                            fence = q
                        break
            _cache[key] = prose
    return _cache[key]


def occurrences(name, token, kind="any"):
    """1-indexed lines of `name` containing `token` verbatim.

    `kind`: "any" (default) | "code" (excluding whole-line comments) | "comment".

    WHY THE SPLIT EXISTS. A symbol that has been DELETED often survives in the
    comment that records its deletion. The matcher would then find the token
    there and "repair" the citation to point at the prose about the removal --
    silencing a TOKEN-GONE that the reader needed to see, and asserting the
    symbol lives at a line where it demonstrably does not. That happened: after
    `_derive_topic_slug` was deleted, `--write` re-pointed its Anchor row at the
    comment recording the deletion. A false citation is strictly worse than a
    reported stale one, because it removes the report.
    """
    src = file_lines(name)
    if src is None:
        return None
    hits = [i + 1 for i, ln in enumerate(src) if token in ln]
    if kind == "any":
        return hits
    prose = _prose_lines(name)
    if kind == "code":
        return [n for n in hits if n not in prose]
    return [n for n in hits if n in prose]


_DEF = re.compile(r"(?:def|class)\s+([A-Za-z_]\w*)")     # .py, at column 0
_SHFN = re.compile(r"([A-Za-z_]\w*)\s*\(\)\s*\{")        # .sh function


def _def_starts(name):
    """[(line, symbol)] for every top-level definition, in file order."""
    key = ("__defs__", name)
    if key not in _cache:
        src = file_lines(name)
        if src is None:
            _cache[key] = []
        else:
            out = []
            for i, ln in enumerate(src, 1):
                if ln[:1].isspace():                     # top-level only
                    continue
                m = _DEF.match(ln) or _SHFN.match(ln)
                if m:
                    out.append((i, m.group(1)))
            _cache[key] = out
    return _cache[key]


def _scope_span(name, symbol):
    """(first, last) 1-indexed line span of `symbol`'s definition, or None."""
    starts = _def_starts(name)
    for idx, (line_no, sym) in enumerate(starts):
        if sym == symbol:
            end = (starts[idx + 1][0] - 1 if idx + 1 < len(starts)
                   else len(file_lines(name) or []))
            return (line_no, end)
    return None


def _scope_symbol(fname, scope_hint):
    """The enclosing symbol a row names, or "" — longest match wins."""
    if not scope_hint:
        return ""
    for sym in sorted({s for _, s in _def_starts(fname) if len(s) >= 4},
                      key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(sym)}(?!\w)", scope_hint):
            return sym
    return ""


def _narrow_by_scope(fname, occ, scope_hint):
    """Keep only the occurrences inside the enclosing definition the row names.

    THE ROOT CAUSE THIS FIXES. Resolution was "nearest occurrence of any usable
    token on the row, within TOL". With 23 identical `proj, topic, state =
    _resolve_topic(session_id)` sites in one file, nearest-line picks the WRONG
    one as soon as drift exceeds the gap between two of them — which it does. The
    plan says so in as many words and names the two disambiguations a hand-edit
    routinely gets wrong; the script never implemented either, so `--write`
    confidently rewrote citations onto unrelated functions.

    `scope_hint` is the enclosing symbol: for an Anchor `↳` sub-row it is the
    parent row's symbol (the sub-row does not name it), for a prose row it is the
    row's own text. Longest match wins, so `_create_topic_locked` is not mistaken
    for `create_topic`. Falls back to the unnarrowed set rather than empty — a
    hint that resolves nothing must not turn a resolvable citation into a failure.
    """
    # The `len(occ) <= 1` short-circuit stays, and S0R3 (2026-08-23) tried removing
    # it and put it back — the record is worth more than the one-line diff.
    #
    # Removing it DID close the corruption it was aimed at (a `phase_start` sub-row
    # repaired onto a line inside `_restore_topic_state`, because S3 left
    # `tmp.rename(state_path)` with exactly one occurrence, in the wrong function).
    # But it also flipped correct-but-out-of-inherited-scope citations to SCOPE-MISS:
    # a `↳` sub-row inherits its parent's scope, and the in-process-caller row
    # (`create_topic(session_id, project, slug)`, parent scope `create_topic`, actual
    # occurrence inside `_create_topic_locked`) went from DRIFT to SCOPE-MISS with no
    # gain. Stricter READING was the wrong lever.
    #   MEASURED, and the first draft of this comment named the WRONG row: it cited
    #   the `new_state` row, which is SCOPE-MISS with the short-circuit and without
    #   it — unchanged either way, so it cannot evidence anything. Two checkers
    #   caught the mis-citation; the row above is the one that actually moves.
    #
    # The property actually needed is narrower: `--write` must never RELOCATE a
    # citation across a scope boundary. Reading may stay lenient; repairing may not.
    # That guard now lives in the repair loop. It costs no false REPORTS — but it
    # does cost false REFUSALS on that same inherited-scope shape: such a row is
    # reported and left for a hand repair rather than rewritten. That is the
    # deliberate trade, stated rather than implied.
    if len(occ) <= 1 or not scope_hint:
        return occ
    sym = _scope_symbol(fname, scope_hint)
    if not sym:
        return occ
    span = _scope_span(fname, sym)
    if not span:
        return occ
    inside = [h for h in occ if span[0] <= h <= span[1]]
    # A named scope containing NO occurrence is a signal, not a licence to guess.
    # Falling back to nearest-anywhere is exactly what put a `phase_start`
    # citation inside `bypass_topic`. Return the empty set; `judge` turns it into
    # a reported SCOPE-MISS that `--write` will not touch.
    return inside


TICK = re.compile(r"`([^`]{2,200})`")
# LEFT-BOUNDARY GUARD -- DEFENSIVE, not currently load-bearing, and the difference
# is worth stating because an earlier version of this comment got it wrong.
#
# The hazard is real in principle: `reap_orphan_plain_topics.py` is a SUBSTRING of
# `test_reap_orphan_plain_topics.py`, so an unguarded alternation would match
# mid-token and silently re-attribute a citation of the test file to the production
# module, whose line numbers are entirely different -- a spurious DRIFT, or a
# coincidental `ok` MASKING a real stale citation.
#
# But KNOWN comes from ANCHOR_FILES (three files with no substring relationship),
# NOT from WRITE_TARGETS, which is the list that actually contains the test file and
# is never passed to this matcher. So today the guard blocks nothing. It is kept
# because ANCHOR_FILES is exactly the kind of list that grows, and the failure it
# prevents is silent. Longest-first alternation is here for the same reason:
# Python's `|` takes the first alternative that matches, not the longest.
_FILE_ALT = "|".join(re.escape(f) for f in sorted(KNOWN, key=len, reverse=True))
_LEFT = r"(?<![\w./-])"
TYPED = re.compile(_LEFT + r"(" + _FILE_ALT + r"):(\d+)")
# A row may name a file WITHOUT an adjacent line number and then cite one further
# along -- "`pre_plan_gates.py` -- the unlink sits ... (same shape in `phase_stop`
# at `:3645`)". Requiring an adjacent `file:NNNN` before considering bare numbers
# made that whole citation SHAPE invisible, and a real stale citation of exactly
# this form survived into Gate 3 and was found by a reviewer instead.
FILEREF = re.compile(_LEFT + r"(" + _FILE_ALT + r")(?!:\d)")
# The last alternative -- a `:NNNN` opening a backtick span -- covers the shape
# "vs `:2490`-`:2492`", where the preceding word is arbitrary and so cannot be
# enumerated as a prefix. A real stale citation of exactly this form survived two
# repair passes here and was found by a reviewer, not by this tool.
BARE = re.compile(r"(?:\bat\s+|[-→>]\s*:|/\s*:|\bline\s+|(?<=`):)(\d{2,5})\b")
VERIFIED = re.compile(r"\[verified:[^\]]*\]")

# A row asserting a token's ABSENCE must not be failed for the token being absent.
NEGATIVE_CLAIM = ("does not occur", "zero occurrences", "no occurrences",
                  "does not exist", "returns zero matches", "never reads")


# A bare language keyword is NOT an anchor. `return` occurs hundreds of times in a
# 10k-line module, so "nearest occurrence" against it is meaningless even AFTER
# scope narrowing: `canonical_project_for_spine` contains three `return`s, and a
# row citing "the terminal `return`" was repaired onto an early `return "Root"` by
# `--write` (S0R3, 2026-08-23). That row had resolved CORRECTLY before the shift,
# so the tool introduced an error where none existed — the worst failure available
# to a repair tool. Rejecting these makes such a row report a tokenless citation,
# which is a first-class failure telling the author to give it a real anchor.
# Short keywords (`if`, `in`, `is`, `or`) are already rejected by the length test;
# they are listed anyway so the set reads as a set rather than as a puzzle.
BARE_KEYWORDS = frozenset({
    "return", "else", "elif", "raise", "pass", "break", "continue", "try",
    "except", "finally", "while", "for", "with", "import", "from", "def",
    "class", "lambda", "yield", "assert", "del", "global", "nonlocal",
    "None", "True", "False", "and", "not", "if", "in", "is", "or",
})


def usable_token(v):
    """Is this backticked span a code token we can look for verbatim?"""
    v_stripped = v.strip()
    if v_stripped in BARE_KEYWORDS:
        return False
    if len(v_stripped) < 3:
        return False
    if v != v_stripped:                 # padded -> a prose fragment between ticks
        return False
    if "(...)" in v or "…" in v:   # deliberately elided in the plan's prose
        return False
    if v_stripped in KNOWN:
        return False
    if v_stripped.startswith("verified:") or v_stripped.startswith("["):
        return False
    if TYPED.search(v_stripped):        # the citation itself, not a token
        return False
    # ...and neither is a BARE citation's own colon-number span. Teaching BARE to
    # recognise `:941` as a citation ALSO made that span pass this predicate, so a
    # citation's degenerate self-representation entered the row's token pool and
    # could be "found" in the source by literal substring match -- the clone is full
    # of `[:12]`/`[:40]` slicing idioms -- landing within tolerance of an unrelated
    # line and masking a real drift. A number is never evidence for itself.
    if re.fullmatch(r":\d{1,6}", v_stripped):
        return False
    if " — " in v_stripped:        # em-dash prose caught by unbalanced ticks
        return False
    return True


findings = []      # (row_no, kind, file, cited, token, nearest, status)
# (file, token, ENCLOSING SYMBOL) -> {cited lines}. The scope is part of the key
# because scope disambiguation makes the same token legitimately resolve to several
# lines: `proj, topic, state = _resolve_topic(session_id)` occurs 23 times, and the
# rows under `phase_start` and `phase_stop` correctly cite DIFFERENT ones. Keyed on
# (file, token) alone, the cross-check reported those correct rows as a conflict.
xcheck = {}
# RETIRED 2026-08-23, kept as a named tombstone rather than deleted silently.
# Nothing has incremented this since the surplus-number rework superseded the
# category ("The honest split is 'checked against something' vs 'nothing to check it
# against', not 'paired' vs 'not paired'"). A surplus number is now judged like any
# other and reports DRIFT / SCOPE-MISS / COMMENT-ONLY / UNREADABLE / TOKEN-GONE, so
# there is no residual population for a footnote to count. It stayed declared, with a
# present-tense comment and a live-looking clause in the summary line, which reads as
# a working counter that happens to be zero — dead code documented as live.
UNPAIRED = [0]     # RETIRED — never incremented; see tombstone above
MISPAIRED = []     # (row, number, positional token, token that WOULD have matched)


def judge(row_no, kind, fname, cited, tokens, negative, scope_hint=""):
    """A citation resolves if ANY usable token on its row lands within TOL."""
    if not tokens:
        # A citation with NO usable token beside it cannot be re-derived by
        # anything -- which is precisely the property this verb exists to enforce.
        # Returning silently made such a citation invisible: not ok, not DRIFT, not
        # TOKEN-GONE, and absent from the count, so a stale tokenless citation was
        # unreportable BY CONSTRUCTION. It is now a first-class failure, and the
        # remedy is to quote the token in the plan rather than to relax the check.
        findings.append((row_no, kind, fname, cited, None, None, "NO-TOKEN"))
        return
    best_tok, best_line, any_found = None, None, False
    comment_only = []
    for tok in tokens:
        occ = occurrences(fname, tok, "code")
        if occ is None:
            findings.append((row_no, kind, fname, cited, tok, None, "UNREADABLE"))
            return
        if not occ:
            # No CODE occurrence. If the token survives only in a comment, decide
            # WHICH of two very different situations this is — see below.
            com = occurrences(fname, tok, "comment")
            if com:
                if any(abs(c - cited) <= TOL for c in com):
                    # The plan cites the comment line ACCURATELY. This script is
                    # densely commented and a plan may legitimately cite a
                    # rationale comment; calling that "the symbol is gone" would
                    # be false, and would steer an editor into rewriting a correct
                    # row as an absence claim.
                    findings.append((row_no, kind, fname, cited, tok,
                                     min(com, key=lambda a: abs(a - cited)), "ok"))
                    return
                comment_only.append(tok)
            continue
        any_found = True
        scoped = _narrow_by_scope(fname, occ, scope_hint)
        if not scoped:
            # The row names an enclosing symbol that does not contain this token.
            # That is legitimate for a CONTRAST citation — a row about
            # `_ensure_canonical_record` citing `_reconcilable_twins_for_slug` to
            # say what it does NOT use — so accept the citation when it resolves
            # on its own. Only when it resolves neither way is something wrong,
            # and then it is REPORTED with near=None so `--write` cannot guess:
            # either the citation or the row's subject is off, and only a human
            # can say which.
            if any(abs(a - cited) <= TOL for a in occ):
                findings.append((row_no, kind, fname, cited, tok,
                                 min(occ, key=lambda a: abs(a - cited)), "ok"))
                return
            findings.append((row_no, kind, fname, cited, tok, None, "SCOPE-MISS"))
            return
        occ = scoped
        near = min(occ, key=lambda a: abs(a - cited))
        if abs(near - cited) <= TOL:
            findings.append((row_no, kind, fname, cited, tok, near, "ok"))
            if len(occ) == 1:
                xcheck.setdefault((fname, tok, _scope_symbol(fname, scope_hint)),
                                  set()).add(cited)
            return
        # KEEP THE FIRST RESOLVING TOKEN — `tokens` arrives in ROW-PROXIMITY order
        # from `tokens_near`, so the first one to resolve is the token physically
        # nearest the citation, which is the row's own fact.
        #
        # This previously read `or abs(near - cited) < abs(best_line - cited)`,
        # i.e. it preferred whichever token's occurrence sat numerically closest to
        # the STALE cited value — throwing away the proximity order deliberately
        # computed above. That selection is CIRCULAR: the stale number votes on its
        # own replacement. On a multi-fact row it therefore repaired a bare number
        # onto its NEIGHBOUR's line whenever the neighbour happened to be a line or
        # two nearer the stale value (real case, 2026-08-23: bare `2679` repaired to
        # `2711` — the preceding fact — instead of its own `2713`, because 32 < 34).
        #
        # Worse than being wrong, it was wrong SELF-CONSISTENTLY: a repair derived
        # from the stale premise looks plausible in review, which is how the S0R-era
        # instance of this shipped. Row proximity is a non-circular signal for TOKEN
        # selection, because it does not consult the value under repair.
        # Locked by selftest case (14b).
        #
        # SCOPE OF THIS FIX, stated exactly — an earlier version of this comment
        # claimed row proximity was "the only non-circular signal available here",
        # which overstated it. This closes the circularity in choosing WHICH TOKEN
        # decides. It does NOT close it in choosing which OCCURRENCE of that token
        # decides: `near = min(occ, key=lambda a: abs(a - cited))` above still lets
        # the stale value pick among a token's own in-scope occurrences. That
        # matters for a token with many sites — `proj, topic, state =
        # _resolve_topic(session_id)` has 23 — and it is pre-existing rather than
        # introduced here. Recorded as residual 10, not described as fixed.
        if best_line is None:
            best_tok, best_line = tok, near
    if not any_found:
        if negative:                    # the row claims the token is ABSENT
            findings.append((row_no, kind, fname, cited, tokens[0], None, "ok-negative"))
        elif comment_only:
            # Reported, and NEVER repaired: `near is None` keeps it out of the
            # writer. The remedy is editorial — rewrite the row as an absence
            # claim (see NEGATIVE_CLAIM) — not a new line number.
            findings.append((row_no, kind, fname, cited, comment_only[0], None,
                             "COMMENT-ONLY"))
        else:
            findings.append((row_no, kind, fname, cited, tokens[0], None, "TOKEN-GONE"))
        return
    findings.append((row_no, kind, fname, cited, best_tok, best_line, "DRIFT"))
    if best_tok and len(_narrow_by_scope(
            fname, occurrences(fname, best_tok, "code") or [], scope_hint)) == 1:
        xcheck.setdefault((fname, best_tok, _scope_symbol(fname, scope_hint)),
                          set()).add(cited)


# --- per-row SCOPE map: one pre-pass, consulted by all three scanners ---------
#
# The scope of a row is what its CLAIM column is about — not any symbol that
# happens to appear in its evidence. Taking the longest def-name anywhere on the
# row got this wrong in the most damaging way: on
#     | 8 | `phase_start` destructures it swapped | `…:3500` `proj, topic, state = _resolve_topic(session_id)` |
# `_resolve_topic` (14 chars) beat `phase_start` (11), narrowing to a function that
# contains none of the 23 call sites, falling back to nearest, and landing inside
# `bypass_topic`. The claim column names `phase_start` and nothing else.
#
# Two continuation shapes inherit the row above, because both state their subject
# only by reference to it: the Anchor table's `↳` sub-rows, and the gate tables'
# `…`-leading rows ("…and passes them to `_topic_path` mirrored").
row_scope = {}
_cur_scope = ""
for idx, raw in enumerate(lines):
    row = raw.strip()
    if not row.startswith("|"):
        _cur_scope = ""
        continue
    cells = [c.strip() for c in row.strip("|").split("|")]
    if not cells:
        continue
    first = cells[0]
    # A numbered gate table puts the index in cell 0; the claim is cell 1.
    if re.fullmatch(r"\d{1,3}", first) and len(cells) > 1:
        first = cells[1]
    if first.lstrip().startswith(("↳", "…", "...")):
        row_scope[idx] = _cur_scope
    else:
        _cur_scope = first
        row_scope[idx] = first


# --- (c) tabular: an Anchor-style table whose header names a file -------------
tabular_file = None
for idx, raw in enumerate(lines):
    row = raw.strip()
    if not row.startswith("|"):
        tabular_file = None
        continue
    cells = [c.strip() for c in row.strip("|").split("|")]
    header_hit = None
    for c in cells:
        for f in KNOWN:
            if c.strip("`* ") == f:
                header_hit = f
    if header_hit and len(cells) == 2:
        tabular_file = header_hit
        continue
    if not tabular_file or len(cells) != 2:
        continue
    if set(cells[1].replace("-", "")) <= set(" |"):
        continue
    # `| ... | 23 total |` is a COUNT, not a line number.
    if re.search(r"\b(total|count|sites)\b", cells[1], re.I):
        continue
    toks = [t for t in TICK.findall(cells[0]) if usable_token(t)]
    # A TABULAR ROW WITH NO USABLE TOKEN IS A FAILURE, NOT A SKIP. `usable_token`
    # rejects any span containing `(...)`, so a row whose only tokens are elided —
    # `| ↳ usage string / `create_topic(...)` call | 8535 / 8545 |` — was skipped
    # WHOLE at the `continue` below, and both its numbers were invisible to this
    # verb by construction. Both were wrong for weeks. The typed/bare scanner has
    # treated a tokenless citation as a first-class failure since round 3; the
    # tabular scanner did not, and that asymmetry is what hid them.
    if not toks and re.search(r"\b\d{2,5}\b", cells[1]) \
       and not re.search(r"\b(total|count|sites)\b", cells[1], re.I):
        for _n in re.findall(r"\b(\d{2,5})\b", cells[1]):
            findings.append((idx + 1, "anchor-table", tabular_file, int(_n),
                             None, None, "NO-TOKEN"))
        continue
    scope_hint = row_scope.get(idx, "")
    nums = [int(n) for n in re.findall(r"\b(\d{2,5})\b", cells[1])]
    if not toks or not nums:
        continue
    negative = any(n in row.lower() for n in NEGATIVE_CLAIM)
    # POSITIONAL pairing, and only as far as the tokens go. A row like
    #   | `phase_stop` (mirrors the six rows above) | 3668 / 3670 / 3688-3689 / ... |
    # carries ONE token and SIX numbers: only the first is that token's own line,
    # the rest are positions inside it. Checking every number against the single
    # token reported three spurious drifts. The same shape appears where one of a
    # row's tokens is deliberately elided as `foo(...)` and so is unsearchable.
    # Unpaired numbers are counted as unverifiable-by-token, never as failures.
    # Pair positionally, but if the positional partner does not resolve, RETRY the
    # number against the row's other tokens before calling it a drift. A row whose
    # numbers are listed in a different order from its tokens would otherwise
    # produce a false DRIFT -- or, worse, mask a real one when the wrong pairing
    # happens to land inside tolerance.
    # STRICT positional pairing. An earlier revision retried a failing number
    # against the row's OTHER tokens and accepted the first that landed -- which
    # could report `ok` because an unrelated token happened to sit near the number,
    # masking the real drift in the positional token's own place. Masking a drift
    # is strictly worse than the false positive it was meant to remove, so the
    # retry now only annotates: the finding is still a DRIFT, with a note that the
    # row's numbers may simply be listed in a different order from its tokens.
    for i, (tok, num) in enumerate(zip(toks, nums)):
        judge(idx + 1, "anchor-table", tabular_file, num, [tok], negative, scope_hint)
        if findings and findings[-1][6] == "DRIFT":
            for j, other in enumerate(toks):
                if j == i:
                    continue
                if any(abs(a - num) <= TOL for a in (occurrences(tabular_file, other) or [])):
                    MISPAIRED.append((idx + 1, num, tok, other))
                    break
    # SURPLUS NUMBERS ARE NOT "UNVERIFIABLE" — THEY ARE UNCHECKED, AND SAYING SO
    # IS THE POINT. `zip(toks, nums)` stops at the shorter list, so a row with one
    # token and six numbers had five of them counted into a footnote and never
    # tested. The `phase_stop` row carried exactly that shape and exactly five
    # wrong numbers, through three rounds of "green". A surplus number is now
    # retried against EVERY token on the row: if one lands within tolerance it
    # resolves, and if none does it is a DRIFT like any other. The honest split is
    # "checked against something" vs "nothing to check it against", not "paired"
    # vs "not paired".
    for extra in nums[len(toks):]:
        hit, hit_tok = None, None
        for t in toks:
            occ_t = _narrow_by_scope(
                tabular_file, occurrences(tabular_file, t, "code") or [], scope_hint)
            if any(abs(a - extra) <= TOL for a in occ_t):
                hit, hit_tok = extra, t
                break
        if hit is not None:
            # Report the token that actually matched, not `toks[0]`. Same
            # mis-attribution the else-arm below documents fixing; it survived here
            # in the sibling loop, found by a checker asked to hunt second instances.
            findings.append((idx + 1, "anchor-table", tabular_file, extra,
                             hit_tok or toks[0], hit, "ok"))
        else:
            # THE SECOND CIRCULAR SELECTOR. This arm was missed when the one in
            # `judge` was fixed, and it is write-reachable: it emits DRIFT with a
            # `near`, so the writer acts on whatever it returns. It had two faults,
            # both now closed:
            #
            #   (i)  it pooled EVERY token's occurrences into one list and chose by
            #        `min(..., key=abs(a - extra))` — selection by closeness to the
            #        STALE value, which lets that value vote on its own replacement.
            #        Fixed the same way `judge` was: take the FIRST token in row
            #        order that has an in-scope occurrence.
            #   (ii) it called `occurrences(...)` with NO `_narrow_by_scope`, unlike
            #        the `hit` loop immediately above, so a surplus number could be
            #        repaired onto an occurrence outside the row's declared
            #        enclosing symbol — exactly what scope narrowing exists to stop.
            #
            # TWO RESIDUALS, stated rather than hidden.
            #
            # (1) Choosing WHICH occurrence of the chosen token to use is still
            #     `min(..., key=abs(a - extra))`, which remains circular for a token
            #     with several in-scope occurrences. Pre-existing, shared with
            #     `judge`; recorded as residual 10, not described as fixed.
            #
            # (2) "First token in row order" is the same MECHANIC as `judge`'s fix
            #     but NOT the same JUSTIFICATION, and the difference is disclosed
            #     here because an earlier version of this comment imported the
            #     justification wholesale. In `judge`, order comes from
            #     `tokens_near(pos)` — proximity to the citation itself, a real
            #     signal about which fact the number belongs to. Here `toks` comes
            #     from the CLAIM cell and `nums` from the EVIDENCE cell, so for a
            #     SURPLUS number "row order" means only "leftmost usable token in
            #     the claim cell" and carries no relation to which fact the number
            #     describes. It is non-circular but positionally ungrounded, and it
            #     is write-reachable. It is preferred over the old pooled `min`
            #     because that was BOTH ungrounded AND circular; this is strictly
            #     less wrong, not right. Recorded as residual 13.
            # CARRY `judge`'s WHOLE STATUS VOCABULARY, not a subset of it. An earlier
            # revision of this arm reported only DRIFT / SCOPE-MISS / TOKEN-GONE, so
            # three situations `judge` distinguishes were all flattened into
            # "the symbol does not exist" — which is a false statement that points the
            # operator at a deletion that did not happen. The same row could emit
            # UNREADABLE for its paired numbers (via `judge`) and TOKEN-GONE for its
            # surplus ones, for one identical cause.
            #   * UNREADABLE   — `occurrences` returns the None SENTINEL when the file
            #                    cannot be read. `... or []` erased that sentinel and
            #                    made an unreadable file indistinguishable from an
            #                    absent symbol.
            #   * COMMENT-ONLY — no CODE occurrence but the token survives in a
            #                    comment. This file is deliberately comment-dense and
            #                    a plan may legitimately cite a rationale comment, so
            #                    an accurately-cited comment RESOLVES (mirroring
            #                    `judge`), and an inaccurate one is reported under its
            #                    own name rather than as a deletion.
            #   * ok-negative  — a row asserting the token's ABSENCE must not be
            #                    failed FOR that absence. `negative` was computed for
            #                    this row and passed to `judge`, and this arm simply
            #                    never consulted it.
            # THESE STATUSES REPORT; THEY DO NOT RESOLVE. An earlier revision of this
            # arm also copied `judge`'s two ACCEPTING fallbacks — an accurately-cited
            # comment, and a contrast citation accurate outside its scope — both of
            # which silently score `ok`. They are removed, because `judge`'s
            # justification for them rests on a token PAIRED with its number, and
            # residual 13 twenty lines above concedes that for a SURPLUS number the
            # token association is "positionally ungrounded and carries no relation
            # to which fact the number describes". Using an ungrounded association to
            # RESOLVE a number silently is the one outcome this file's own doctrine
            # forbids outright: "the retry now only annotates … Masking a drift is
            # strictly worse than the false positive it was meant to remove."
            # A surplus number landing within tolerance of ANY out-of-scope
            # occurrence of ANY token on the row would have scored `ok` and never
            # been printed. Reporting under a precise name is the honest ceiling
            # here; resolving is not available without a real pairing.
            nearest, chosen, status = None, None, None
            any_occ, comment_only, unreadable = False, [], False
            for t in toks:
                occ_all = occurrences(tabular_file, t, "code")
                if occ_all is None:
                    unreadable, chosen = True, t
                    break
                if not occ_all:
                    if occurrences(tabular_file, t, "comment"):
                        comment_only.append(t)
                    continue
                any_occ = True
                occ_t = _narrow_by_scope(tabular_file, occ_all, scope_hint)
                if occ_t:
                    chosen = t
                    nearest = min(occ_t, key=lambda a: abs(a - extra))
                    status = "DRIFT"
                    break
            if status is None:
                if unreadable:
                    status = "UNREADABLE"
                elif any_occ:
                    status = "SCOPE-MISS"
                elif negative:
                    status, chosen = "ok-negative", (chosen or toks[0])
                elif comment_only:
                    status, chosen = "COMMENT-ONLY", comment_only[0]
                else:
                    status = "TOKEN-GONE"
            # Report the token actually examined. `chosen or toks[0]` named `toks[0]`
            # in precisely the cases where no token was chosen, so a multi-token row
            # blamed its leftmost token for a miss another token caused.
            findings.append((idx + 1, "anchor-table", tabular_file, extra,
                             chosen if chosen is not None else ", ".join(toks),
                             nearest, status))

# --- (a)+(b) typed and bare, row by row --------------------------------------
for idx, raw in enumerate(lines):
    # A trailing `[verified: file:N]` restates the row's Source Location; it is not
    # an independent citation, and pairing it with whatever token precedes it was
    # the single largest false-positive source.
    row = VERIFIED.sub("", raw)
    typed = list(TYPED.finditer(row))
    filerefs = list(FILEREF.finditer(row))
    if not typed and not filerefs:
        continue
    low = row.lower()
    negative = any(n in low for n in NEGATIVE_CLAIM)
    # Keep each token's POSITION, so a citation can try the tokens NEAREST to it
    # first. The acceptance rule is deliberately "any usable token on the row", but
    # trying them in raw left-to-right order meant a citation's real evidence could
    # be pre-empted by an unrelated symbol earlier on the same row -- so a stale
    # citation could pass on a coincidence. Proximity ordering keeps the loose rule
    # (which removed a worse false-positive class) while making the semantically
    # correct token the one that decides in the overwhelmingly common case.
    tok_spans = [(m.start(), m.group(1)) for m in TICK.finditer(row)
                 if usable_token(m.group(1))]

    def tokens_near(pos, cited=None):
        ordered = [t for _, t in sorted(tok_spans, key=lambda st: abs(st[0] - pos))]
        # AN EXPLICIT BINDING OUTRANKS PROXIMITY. A Gate row states its evidence as
        # `line <N>: \`snippet\`` -- the shape check-plan-gates.sh requires -- which
        # names WHICH token belongs to citation <N>. Proximity does not know that,
        # and on a row whose claim cell mentions another symbol it picks the wrong
        # one: the `canonical_project_for_spine` row binds `:758` to its `def` line
        # in its own prose, while `"Root"` sits closer to the citation, so `:758`
        # resolved against `"Root"` and `--write` would have rewritten it onto an
        # early `return "Root"` -- leaving the number and the prose that explains
        # it contradicting each other (S0R3, 2026-08-23).
        # AND IT IS EXCLUSIVE, NOT MERELY FIRST. Returning [bound] + the rest let a
        # citation whose bound token is GONE fall through to whatever else the row
        # mentions: row `The rollback snapshot targets the mirrored path` binds its
        # number to `state_path = _topic_path(topic, proj)`, which S3 deleted, so it
        # resolved against the SIGNATURE `def _topic_path(...)` and was repaired onto
        # the definition line — a row about a call site now citing the declaration.
        # If the row says which token its number is for, that token is the only
        # evidence; when it is gone the citation is stale and must be REPORTED.
        if cited is None:
            return ordered
        # (0) "at <N> by \`tok\`" — the token FOLLOWS the number. Checked first,
        #     because form (2) below scans BACKWARDS from "at <N>" and on a row
        #     reading "`tokA` … followed at <N> by `tokB`" it would bind tokA, which
        #     belongs to the PREVIOUS number. Real shape, live in this plan's Gate 1.
        #     `\bby\s+` IS REQUIRED, not decoration. Without it this matched ANY
        #     backticked token within 20 chars after "at <N>", which hijacked the
        #     backward form (2): on the `phase_stop carries the identical shape` row
        #     it bound citation 3925 to `_write_topic_state(proj, topic, state)` —
        #     leaving `state_path = _topic_path(topic, proj)`, a token with ZERO
        #     occurrences, with no TOKEN-GONE report at all, and a DRIFT `--write`
        #     would have "repaired" to 4063. Replacing a real absence with a
        #     plausible wrong number is strictly worse than the false positive it
        #     replaced. Two numbers on that row would have been planted, not one.
        #       An earlier draft of this comment said the rule bound 4063 and scored
        #       `ok`. Both were wrong — 3925 is the citation it reached, and both
        #       affected citations were DRIFT (deltas 138 and 9, over TOL=5), i.e.
        #       repairable rather than merely unreported. A checker re-derived it;
        #       the harm was worse than the first account of it.
        m_by = re.search(
            rf"\bat\s+`?(?:[\w./-]+\.py:)?:?{cited}\b[^`]{{0,20}}?\bby\s+`([^`]{{2,200}})`",
            row)
        if m_by:
            return [m_by.group(1)] if usable_token(m_by.group(1)) else []
        # (1) the Gate form: `line <N>: \`tok\``.
        m_bind = re.search(rf"line\s+{cited}\s*:\s*`([^`]{{2,200}})`", row)
        if m_bind:
            # A BINDING TO AN UNUSABLE TOKEN REPORTS; IT DOES NOT FALL BACK.
            # Falling through re-opened the borrow-a-neighbour failure this rule
            # exists to close — and fix (a) WIDENED that door by making bare
            # keywords unusable, so `line 52: \`return\`` would have resolved
            # against whatever else the row mentioned. If the row says which token
            # its number is for, an unusable one is a bad anchor to be REPORTED,
            # not a licence to pick another.
            return [m_bind.group(1)] if usable_token(m_bind.group(1)) else []
        # (2) the prose form the SAME rows use for their secondary numbers:
        #     "`tok` at <N>", "`tok` sits at <N>", "`tok` at `:<N>`". Without this a
        #     row listing several tokens each with its own number paired them by
        #     proximity and collapsed them: the `phase_stop carries the identical
        #     shape` row had THREE distinct tokens all repaired to one line, and a
        #     Gate 3 row cited `def phase_stop` for a token that no longer exists.
        #     Bounded to 60 non-backtick chars so it cannot reach across a cell.
        #     The number after "at" appears in three spellings across these tables:
        #     bare (`at 4072`), colon-prefixed (`at `:2828``) and filename-qualified
        #     (`at `pre_plan_gates.py:3948``). The last one is not decoration — a
        #     Gate 3 row uses it, and missing it let that row's citation be repaired
        #     onto `def phase_stop` for a token that no longer exists.
        m_at = re.search(
            rf"`([^`]{{2,200}})`[^`]{{0,60}}?\bat\s+`?(?:[\w./-]+\.py:)?:?{cited}\b",
            row)
        # FORM (2) IS DELIBERATELY NOT EXCLUSIVE, and this is the one asymmetry in
        # the three forms worth stating rather than smoothing away.
        #
        # Forms (0) and (1) are DECLARATIVE — `line <N>: \`tok\`` and "at <N> by
        # \`tok\`" say outright which token the number is for, so an unusable token
        # there is a bad anchor to report. Form (2) scans BACKWARDS for any
        # preceding backtick span, which is heuristic: on a row like
        # "`twofact.py:107` — failures at 107 requests/sec — `def alpha_call():`"
        # it matches the CITATION'S OWN backtick span, which is unusable by
        # construction. Reporting there would turn a spurious match into a spurious
        # miss — a pass of this reopen did exactly that and broke a shipped case.
        # So an unusable match here is evidence the scan caught the wrong thing:
        # fall through to proximity, which is what the pre-S0R3 behaviour did.
        if m_at and usable_token(m_at.group(1)):
            return [m_at.group(1)]
        return ordered

    seen = set()
    for m in typed:
        fname, cited = m.group(1), int(m.group(2))
        if (fname, cited) in seen:      # same citation twice on one row
            continue
        seen.add((fname, cited))
        judge(idx + 1, "typed", fname, cited, tokens_near(m.start(), cited), negative,
              row_scope.get(idx, row))

    # A bare number takes its FILE from the nearest preceding file reference on the
    # row, whether that reference carried its own line number or not.
    file_anchors = sorted(typed + filerefs, key=lambda m: m.start())
    for bm in BARE.finditer(row):
        num = int(bm.group(1))
        if any(t.start() <= bm.start() <= t.end() for t in typed):
            continue
        prior = [t for t in file_anchors if t.end() <= bm.start()]
        if not prior:
            continue
        fname = prior[-1].group(1)
        if (fname, num) in seen:
            continue
        seen.add((fname, num))
        judge(idx + 1, "bare", fname, num, tokens_near(bm.start(), num), negative,
              row_scope.get(idx, row))

bad = [f for f in findings if f[6] not in ("ok", "ok-negative")]
ok_neg = sum(1 for f in findings if f[6] == "ok-negative")
print(f"[plan_env] anchors: {len(findings)} citations checked "
      f"({sum(1 for f in findings if f[1] == 'anchor-table')} tabular, "
      f"{sum(1 for f in findings if f[1] == 'typed')} typed, "
      f"{sum(1 for f in findings if f[1] == 'bare')} bare) — "
      f"{len(findings) - len(bad)} resolve"
      + (f" ({ok_neg} of them absence-claims)" if ok_neg else "")
      + f", {len(bad)} do not")

for row_no, kind, fname, cited, token, near, status in bad:
    where = f" nearest actual {near}" if near else ""
    print(f"  plan:{row_no}  [{kind}] {fname}:{cited} -> {status}{where}"
          f"   token: {(token or '')[:70]!r}")

if MISPAIRED:
    print(f"[plan_env] anchors: {len(MISPAIRED)} of the drifts above may be an "
          f"ORDERING problem -- the row lists its numbers in a different order "
          f"from its tokens. Reported, never auto-accepted:")
    for row_no, num, tok, other in MISPAIRED:
        print(f"  plan:{row_no}  {num} was paired with {tok[:40]!r} but sits at "
              f"{other[:40]!r}")

# --- cross-check ------------------------------------------------------------
# Restricted to tokens that occur EXACTLY ONCE in the source. For a token with
# several occurrences, two different citations are usually a definition and a call
# site and disagreement means nothing; for a unique token, three tables giving
# three numbers for one fact is the signal.
conflicts = {k: v for k, v in xcheck.items()
             if len(v) > 1 and max(v) - min(v) > TOL}
if conflicts:
    print(f"[plan_env] anchors: {len(conflicts)} UNIQUE token(s) cited at "
          f"disagreeing lines across tables:")
    for (fname, token, scope), nums in sorted(conflicts.items()):
        where = f" (in {scope})" if scope else ""
        print(f"  {fname}{where}: {sorted(nums)}   token: {token[:70]!r}")

if do_write:
    # THE ONLY PATH IN THIS SCRIPT THAT MUTATES A REAL, NON-DISPOSABLE FILE.
    # Everything else writes to SNAP_DIR or a mktemp scratch dir; this edits the
    # plan. It is therefore held to a HIGHER bar than detection, not a lower one:
    #   * a repair is REFUSED unless the number it is replacing occurs exactly ONCE
    #     on the row. An earlier version substituted `\b<cited>\b` with count=1
    #     against the whole raw row, which on a row carrying that number twice would
    #     silently rewrite the wrong one;
    #   * a typed repair rewrites EVERY occurrence of `<file>:<cited>` on the row,
    #     not just the first, so a trailing `[verified: file:NNNN]` restatement
    #     tracks its own citation instead of being left contradicting it;
    #   * anything refused is REPORTED, so a drift never disappears into a silent
    #     no-op.
    out = list(lines)
    fixed = 0
    refused = []
    repaired_ranges = set()          # (row, endpoint) already written by the range arm
    for row_no, kind, fname, cited, token, near, status in findings:
        if status != "DRIFT" or near is None:
            continue
        if (row_no, cited) in repaired_ranges:
            continue
        line = out[row_no - 1]
        # NEVER REPAIR A CITATION ON AN ABSENCE-CLAIM ROW. Such a row cites the line
        # of a REMOVAL COMMENT or a surviving trace — something no code token can
        # locate, because the thing the row is about is precisely what is gone. Its
        # remaining tokens come from the explanatory prose, so "nearest occurrence"
        # resolves against whatever the cell happens to MENTION. The
        # `_derive_topic_slug` row is the worked example: its own parenthetical says
        # "clone line 647 is an unrelated `_write_json` call", and `--write` used
        # `_write_json` to rewrite the citation to 647 — planting the exact number
        # the cell documents as wrong, self-consistently (S0R3, 2026-08-23).
        # NEVER RELOCATE A CITATION ACROSS A SCOPE BOUNDARY. Judging is lenient when
        # a token is unique (see `_narrow_by_scope`) — repairing must not be. S3 moved
        # `tmp.rename(state_path)` out of `phase_start` into `_restore_topic_state`,
        # leaving one occurrence; without this guard `--write` rewrote a `phase_start`
        # sub-row's citation onto a line inside the helper, which is the exact
        # cross-function relocation the plan warns a hand-edit gets wrong.
        # `or line` MATTERS: `row_scope` is populated only for `|`-rows, while
        # `judge` falls back to the row's own text (`row_scope.get(idx, row)`).
        # Without the same fallback a PROSE row was judged with a scope hint and
        # repaired without one, leaving the cross-function relocation reachable on
        # exactly the row shape this reopen's own fixtures use. Two independent
        # checkers found this hole in the first cut of the guard.
        _hint = row_scope.get(row_no - 1) or line
        if _hint:
            _sym = _scope_symbol(fname, _hint)
            _span = _scope_span(fname, _sym) if _sym else None
            if _span and not (_span[0] <= near <= _span[1]):
                refused.append((row_no, cited, near,
                                f"the repair would move it outside `{_sym}` "
                                f"({_span[0]}-{_span[1]}), the scope this row is "
                                f"about — re-anchor the row or repair it by hand"))
                continue
        if any(n in line.lower() for n in NEGATIVE_CLAIM):
            refused.append((row_no, cited, near,
                            "the row makes an ABSENCE claim; its line number anchors "
                            "a removal comment, which no code token can locate — "
                            "repair it by hand or re-anchor the row"))
            continue
        # A RANGE IS ONE UNIT IN BOTH DIRECTIONS. The range arm below triggers on
        # the LOW endpoint and rewrites both. If this finding is the HIGH endpoint,
        # skip it — otherwise, whenever the low endpoint is refused or skipped (a
        # SCOPE-MISS, say), the bare path repairs the high one ALONE and the range
        # is left reading backwards. That is exactly what `2556–2515` is: the same
        # corruption the range fix was written to end, re-entering by the one door
        # it had left open.
        if re.search(rf"(?<![\w.])(\d{{2,5}})(?:`)?\s*[-–—]\s*(?::|`:)?{cited}"
                     rf"(?![\w.])", line):
            refused.append((row_no, cited, near,
                            "it is the upper endpoint of a range whose lower "
                            "endpoint was not repaired"))
            continue
        # THE AMBIGUITY GUARD IS SCOPED TO THE KIND, because the two kinds
        # substitute different things and a guard counting something the
        # substitution does not touch is worse than no guard.
        #
        # A typed repair rewrites `<file>:<cited>` -- EVERY occurrence, so that a
        # trailing `[verified: file:NNNN]` restatement tracks its own citation
        # instead of being left contradicting it. All those occurrences are the SAME
        # fact, so there is no ambiguity to guard against. The previous code counted
        # bare digits across the whole raw line and refused at 2 -- which meant a row
        # carrying a citation AND its restatement, the very case the comment claimed
        # to support, was ALWAYS refused and never repaired. Aspirational comment,
        # contradicted by the code beneath it.
        #
        # A tabular or bare repair rewrites a BARE number, which genuinely can be
        # ambiguous, so there the count guard stays and must match the substitution
        # pattern exactly. Word-adjacency, not digit-adjacency: `641` must not be
        # counted inside `1641` NOR inside `file641.py`.
        # A RANGE IS ONE CITATION, NOT TWO NUMBERS. `| ... | 2556-2558 |` cites a
        # span; repairing only the endpoint that happened to pair with a token
        # produced `2639-2558` — a range reading BACKWARDS. That is corruption,
        # strictly worse than the staleness it replaced, and it shipped. Shift
        # BOTH endpoints by the same delta so the span keeps its width; refuse if
        # that would invert it. Checked before the kind branch because a range can
        # appear in a tabular cell or beside a typed citation.
        # Two guards on the range match, both learned the hard way:
        #
        # (a) NOT PART OF A DATE. The old pattern was a blind row-wide search keyed
        #     only on digit equality, so on a row containing `2026-08-22` with a
        #     cited line of 2026 it matched the DATE, rewrote it to nonsense, and
        #     then `continue`d — silently skipping the real repair. This file's own
        #     prose is full of that date idiom. A date has a further `-<digits>`
        #     after it; a citation range does not.
        # (b) THE BACKTICKED FORM COUNTS. The plan writes ranges as
        #     `` `:2556`–`:2558` `` as well as bare `2556–2558`. The adjacency-only
        #     pattern missed the backticked shape entirely, so both endpoints fell
        #     through to the bare path and were independently rewritten to the SAME
        #     number — a range collapsed to `` `:2515`–`:2515` ``.
        m_range = re.search(
            rf"(?<![\w.]){cited}((?:`)?\s*[-–—]\s*(?::|`:)?)(\d{{2,5}})"
            rf"(?![\w.])(?![-–—]\d)", line)
        if m_range:
            dash, hi = m_range.group(1), int(m_range.group(2))
            new_hi = hi + (near - cited)
            if new_hi < near:
                refused.append((row_no, cited, near,
                                "repairing this endpoint would invert the range"))
                continue
            # SPLICE BY POSITION, never by value. `line.replace(m_range.group(0),
            # ..., 1)` rewrote the leftmost LITERAL occurrence, while `re.search`
            # had matched the leftmost occurrence THE PATTERN ACCEPTS — and the
            # pattern carries three guards the literal search does not honour.
            # They diverge exactly where a guard did its job, which DEFEATS THE
            # DATE GUARD ON THE LINE ABOVE: with `cited = 2026` and a row reading
            #   on 2026-08-22 the span 2026-08 is cited
            # the regex correctly refuses the date via `(?![-–—]\d)` and matches
            # the later `2026-08`; `str.replace` then rewrites the date at index 3
            # and leaves the real citation stale. This file's own comment notes its
            # prose "is full of that date idiom".
            # Same defect as the typed arm's value-search, ten lines away, found by
            # a checker asked to hunt for second instances of that fix.
            new = line[:m_range.start()] + f"{near}{dash}{new_hi}" + line[m_range.end():]
            # The second endpoint has its own finding; drop it, or the bare path
            # rewrites the number this branch just wrote.
            repaired_ranges.add((row_no, hi))
            if new != line:
                out[row_no - 1] = new
                fixed += 1
            continue

        if kind == "typed":
            # Substitute MANUALLY so the exact end offset of the FIRST repaired
            # citation is known in the NEW string. This replaces a `re.sub` followed
            # by a `re.search` for `fname:near`, which had a real bypass: on a row
            # already carrying a CORRECT citation of the same file, that search
            # matched the pre-existing one, anchored the window there, and ran the
            # tail rewrite over the wrong span — while the stale citation went
            # unrepaired and no refusal was printed. Searching for a VALUE cannot
            # distinguish the citation just written from one that was already there;
            # recording the position at write time can.
            _sub_pat = re.compile(rf"(?<![\w.])({re.escape(fname)}):{cited}(?![\w.])")
            _pieces, _last, _grown, first_end_new = [], 0, 0, None
            for _m in _sub_pat.finditer(line):
                _pieces.append(line[_last:_m.start()])
                _grown += _m.start() - _last
                _rep = f"{fname}:{near}"
                _pieces.append(_rep)
                if first_end_new is None:
                    first_end_new = _grown + len(_rep)
                _grown += len(_rep)
                _last = _m.end()
            _pieces.append(line[_last:])
            new = "".join(_pieces)
            # Measured on the SUBSTITUTED text, not the original: `near` may have a
            # different digit count from `cited`, so an offset taken before the
            # rewrite would point into the wrong place afterwards.
            # The FIRST typed match, not the last, so the evidence column of a
            # `[verified:]`-stamped row is reachable at all.
            #
            # AN EARLIER VERSION OF THIS COMMENT MADE TWO CLAIMS THAT ARE FALSE, and
            # they are corrected here rather than deleted, because a comment that
            # overstates a guard is how this verb keeps acquiring defects.
            #
            # FALSE CLAIM 1 — "mirrors the DETECTOR's own rule". It does not. The
            # detector resolves each bare number against the nearest file reference
            # PRECEDING THAT NUMBER (`prior[-1]`), a per-number rule. This window is
            # "first anchor for THIS file, then everything after", a per-row rule.
            # They differ on a row citing two different known files: the detector
            # attributes `at 100` to whichever file reference precedes it, while
            # this tail can span past a second filename and rewrite it anyway.
            #
            # FALSE CLAIM 2 — "the guard this replaces is preserved". Only partly.
            # Prose before the FIRST file reference is still safe (it stays in
            # `head`). Prose BETWEEN two citations of the same file is NOT: on
            #   | ... | `f.py:107` | failures at 107 requests/sec | [verified: f.py:107] |
            # the typed sub rewrites both citations, `mm_first` lands on the first,
            # and `at 107 requests/sec` — prose the detector never reported — is
            # rewritten. Under last-match anchoring that text sat in `head`.
            #
            # This is a REAL WIDENING and it is accepted deliberately: the evidence
            # column is the dominant gate-row shape and was wholly unreachable, so
            # leaving it stale contradicted every gate row's own Source Location.
            # The collision is undecidable from here — a number that restates the
            # citation and a number that is prose are the same token to this code —
            # which is residual 6's territory, not something a wider window creates.
            # Recorded as residual 11 rather than described as prevented.
            #
            # Anchoring to the LAST match silently excluded the evidence column of
            # every gate row that ends in a `[verified: <file>:<n>]` stamp — the
            # dominant shape in Gate 1 and Gate 3. On
            #   | ... | `f.py:2675` | line 2675: `tok` | [verified: f.py:2675] |
            # the last match is inside the stamp, so `line 2675` fell into `head`
            # and was never repaired, leaving the row's Source Location and its
            # own evidence prose contradicting each other. Repairing 54 citations
            # while leaving their restatements stale is what drove the cross-table
            # disagreement count UP (3 -> 6) instead of to zero.
            #
            # What the guard STILL does, scoped to what is actually true (see FALSE
            # CLAIM 2 above — this paragraph previously asserted the unqualified
            # "the guard this replaces is preserved", which the correction above
            # labels false; leaving both standing made the file assert and deny one
            # sentence twenty-six lines apart, so it is amended here rather than
            # contradicted from a distance):
            #
            # prose before the FIRST file reference is still untouched, because it
            # remains in `head`. On
            #   failures at 100 requests/sec — see `pre_plan_gates.py:100`
            # the first (and only) match is at the END, so "at 100 requests/sec"
            # stays in `head` exactly as before. Prose BETWEEN two citations of the
            # same file is NOT protected — that is residual 11, characterised by
            # (14e). Locked by selftest case (14d), NOT (14c): (14c)'s fixture has
            # no pre-citation prose and asserts only that the evidence column was
            # repaired. (14d)'s own comment states the same division.
            # FAIL CLOSED when the repaired citation cannot be located on the row.
            # This previously left `m_typed_end = 0`, which made `head` empty and
            # `tail` THE WHOLE ROW — so a guard whose entire purpose is to BOUND a
            # rewrite degraded, on failure, to "rewrite everything, including text
            # before any citation". That is the inverse of its purpose.
            #
            # Reachable, not theoretical: the detector's TYPED pattern carries no
            # trailing guard while the substitution above appends `(?![\w.])`, so an
            # un-backticked citation ending a sentence (`…gates.py:2675.`) is
            # DETECTED but not SUBSTITUTED, and `mm_first` then finds nothing.
            #
            # WHY THIS BRANCH WRITES NOTHING, stated precisely — an earlier version
            # guarded a write here ("any repair the typed re.sub already made is
            # still committed") and that scenario is UNREACHABLE. If the typed
            # substitution fired, `new` contains `fname:near` at that position; the
            # preceding character is unchanged and already satisfied `(?<![\w.])`,
            # and the following character is the one that followed `cited` and
            # already satisfied `(?![\w.])`. So the search below is guaranteed to
            # find it. Therefore `not mm_first` implies `new == line` — nothing was
            # substituted — and a guarded write here was dead code justified by a
            # scenario that cannot occur.
            #
            # REFUSE OUT LOUD rather than returning silently. This was the one
            # writer path that neither repaired nor reported, contradicting the
            # writer's own contract that "anything refused is REPORTED, so a drift
            # never disappears into a silent no-op". The drift stays visible in the
            # detection section either way, but the operator is now told which row
            # the writer declined and why.
            # `first_end_new is None` means the substitution pattern matched nothing,
            # which is now a DIRECT fact recorded at write time rather than inferred
            # from a later search for the new value. Locked by selftest case (14f).
            if first_end_new is None:
                refused.append((row_no, cited, near,
                                "the repaired citation could not be located on the "
                                "row (an un-backticked citation followed by `.` is "
                                "detected but not substituted)"))
                continue
            m_typed_end = first_end_new
            # ...and every BARE restatement of the SAME number on the SAME row,
            # but ONLY AFTER the typed citation. The detector requires a bare
            # number to be PRECEDED by a file reference before it counts as a
            # citation at all; the repair originally required nothing, so on a row
            # like "failures at 100 requests/sec — see `pre_plan_gates.py:100`" it
            # rewrote "at 100 requests/sec" into "at 120 requests/sec" — corrupting
            # prose that was never even reported as a finding. Restricting the
            # substitution to the text after the typed match APPROXIMATES the
            # detector's rule with the information available here — it does not
            # mirror it. See FALSE CLAIM 1 above: the detector's rule is per-NUMBER
            # (`prior[-1]`, the nearest file reference preceding THAT number) while
            # this window is per-ROW (first anchor for this file, then everything
            # after). They differ in kind, not merely in precision.
            #
            # This sentence said "mirrors the detector's rule" until 2026-08-23. The
            # correction above was added in an earlier round and this instance was
            # left standing 94 lines below it, so the file asserted and denied one
            # claim in two places — the SAME half-repair the correction above was
            # written to end, committed while ending it. Both instances are now
            # amended in place, which is the only form of this fix that works.
            head, tail = new[:m_typed_end], new[m_typed_end:]
            tail = re.sub(
                rf"(\bat\s+|[-→>]\s*:|/\s*:|\bline\s+)"
                rf"(?<![\w.]){cited}(?![\w.])",
                rf"\g<1>{near}", tail)
            new = head + tail
        else:
            if len(re.findall(rf"(?<![\w.]){cited}(?![\w.])", line)) != 1:
                refused.append((row_no, cited, near,
                                "the number appears more than once on the row"))
                continue
            new = re.sub(rf"(?<![\w.]){cited}(?![\w.])", str(near), line, count=1)
        if new != line:
            out[row_no - 1] = new
            fixed += 1
    if fixed:
        # safe-defaults.md prescribes a timestamped copy-aside before any edit that
        # cannot be trivially undone. The plan is expected to be git-tracked, but
        # this script neither checks that nor should depend on it -- an assumption
        # about someone else's working tree is not a backup.
        # REFUSE to overwrite an existing backup. The stamp has one-second
        # resolution, so two repairs inside the same UTC second would otherwise
        # collide and the second write would silently discard the true pre-first-edit
        # content -- destroying the very thing the backup exists to preserve, in the
        # code added to satisfy safe-defaults.md.
        bak = plan_path.with_name(plan_path.name + f".bak-{STAMP}")
        if bak.exists():
            raise SystemExit(
                f"[plan_env] anchors: REFUSED to write — a backup already exists at "
                f"{bak}. Two repairs inside one second would overwrite it and lose "
                f"the pre-edit content. Re-run in the next second, or move it aside.")
        bak.write_text(text)
        print(f"[plan_env] anchors: backed the plan up to {bak} before editing")
        plan_path.write_text("\n".join(out) + "\n")
        print(f"[plan_env] anchors: --write repaired {fixed} citation(s) in {plan_path}")
    else:
        print("[plan_env] anchors: --write had nothing to repair")
    for row_no, cited, near, why in refused:
        print(f"[plan_env] anchors: --write REFUSED plan:{row_no} ({cited} -> {near}) "
              f"— {why}. Repair it by hand.")

raise SystemExit(1 if (bad or conflicts) else 0)
PYA
}

# --------------------------------------------------------------------------- #
# selftest — THE LOAD-BEARING VERB
# --------------------------------------------------------------------------- #
# Every guard above was, for eleven review rounds, a promise that nothing
# verified. Here each one becomes a command that exits non-zero or does not.
# Both directions are exercised: a guard tested only in its refusing direction
# can be a guard that always refuses, which is exactly how the unrealizable
# CLAUDE_CONFIG_DIR_PARENT survived eight rounds of careful reading.

SELF_PASS=0
SELF_FAIL=0

# self_case_because <expected-substring> <label> -- cmd...
#
# A refusal for the WRONG reason is indistinguishable from a correct refusal when
# a harness looks only at the exit code — and two of this script's first-draft
# cases did exactly that, refusing on a missing tool while claiming to test the
# lock probe and the isolation proof. This variant asserts non-zero AND that the
# refusal names the guard it was supposed to trip.
self_case_because() {
  local want="$1" label="$2"; shift 3
  local out rc=0
  out=$("$@" 2>&1) || rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '  FAIL  (should have REFUSED but exited 0)  %s\n' "$label"
    SELF_FAIL=$((SELF_FAIL + 1)); return
  fi
  # Pure-bash substring test, deliberately NOT `printf | grep -q`: under
  # `set -o pipefail` a `grep -q` that matches early can SIGPIPE the writer and
  # hand the pipeline a non-zero status, so a MATCH reads as a miss. Matching in
  # the shell removes that whole class of false negative.
  case "$out" in
    *"$want"*)
    printf '  PASS  (refused for the right reason)  %s\n' "$label"
    SELF_PASS=$((SELF_PASS + 1))
    ;;
    *)
    printf '  FAIL  (refused, but NOT because of %s)  %s\n' "$want" "$label"
    printf '        actual: %s\n' "$(printf '%s' "$out" | tr '\n' ' ')"
    SELF_FAIL=$((SELF_FAIL + 1))
    ;;
  esac
}

self_case() {  # self_case <expect refuse|expect pass> <label> -- cmd...
  local expect="$1" label="$2"; shift 3
  local rc=0
  "$@" >/dev/null 2>&1 || rc=$?
  if [ "$expect" = "refuse" ]; then
    if [ "$rc" -ne 0 ]; then
      printf '  PASS  (refused, rc=%s)  %s\n' "$rc" "$label"; SELF_PASS=$((SELF_PASS + 1))
    else
      printf '  FAIL  (should have REFUSED but exited 0)  %s\n' "$label"; SELF_FAIL=$((SELF_FAIL + 1))
    fi
  else
    if [ "$rc" -eq 0 ]; then
      printf '  PASS  (accepted)  %s\n' "$label"; SELF_PASS=$((SELF_PASS + 1))
    else
      printf '  FAIL  (should have PASSED but exited %s)  %s\n' "$rc" "$label"; SELF_FAIL=$((SELF_FAIL + 1))
    fi
  fi
}

cmd_selftest() {
  # selftest runs BEFORE preflight (the slice register's order), so it cannot
  # source ENV — preflight is one of the things it is testing. It discovers what
  # it needs for itself. It does still insist on running inside a clone: several
  # cases invoke `gate` against $CLAUDE_CONFIG_DIR for real.
  assert_not_live_config
  PY="${PY:-$(discover_py)}" || die "selftest needs a qualifying interpreter"
  STATE_DIR="${STATE_DIR:-$LIVE_CONFIG/state/pre_plan_gates}"

  local me="${BASH_SOURCE[0]}"
  me=$(realpath_of "$me")
  local work; work=$(mktemp -d)
  printf '[plan_env] selftest scratch: %s\n' "$work"
  printf '[plan_env] selftest — refusal paths\n'

  # ------------------------------------------------------------------------- #
  # THE QUIESCENCE RULE — read this before adding any case that runs `preflight`.
  #
  # RULE: every selftest case whose `preflight` invocation actually REACHES
  #       `probe_quiescence` MUST pass TM_STALE_T_SECONDS=0.
  #
  #       The test is REACHABILITY, not "invokes preflight". An earlier wording of
  #       this block said "every case that invokes the real preflight, with exactly
  #       ONE exception" — which is FALSE against the code directly below it, and is
  #       corrected here rather than quietly reworded. FIVE invocations carry no
  #       override, not one:
  #
  #         * case (1)'s THREE sub-invocations (plain, trailing slash, symlink),
  #           which refuse at `assert_not_live_config` (:379); and case (2)'s ONE
  #           invocation, which refuses inside `discover_py` (:384-385). Four sites
  #           in total. Both of those refusals run BEFORE `probe_quiescence` (:389),
  #           so these never reach the probe and the override would be noise.
  #           (Deliberately NO line numbers for the four sites. They sit BELOW this
  #           block, so any edit to this block shifts them -- and the first draft of
  #           this very correction cited :1853/:1857/:1861/:1881, which its own
  #           insertion had already moved to :1862/:1866/:1870/:1890 by the time it
  #           was written. Case labels are stable, and so is re-deriving the sites
  #           with:  grep -n 'bash "\$me" preflight' plan_env.sh
  #           A self-referential line number is not. The guard line numbers
  #           above ARE kept: they live in `cmd_preflight`, far from anything this
  #           block edits, and they carry the load-bearing ordering claim.)
  #           (An earlier wording of this bullet said "cases (1)-(3)". Case (3) is
  #           the mutated-rolling-digest case and invokes `digest-record` /
  #           `digest-assert` — it contains no `preflight` call at all, so it has no
  #           bearing on reachability. The counts were right and the case labels were
  #           wrong: a citation error inside the block rewritten to fix a citation
  #           error. Recorded rather than silently corrected, because that is this
  #           file's signature failure and this is its sixth recurrence.)
  #         * case (4), "a fresh lock halts the snapshot" — the one case whose
  #           SUBJECT is the probe. It must keep the real 3600s threshold, and it is
  #           deterministic anyway because it injects a synthetic lock under its own
  #           fakehome (HOME redirects `Path.home()`, which is what
  #           `taskmanagement.LOCKS_DIR` is derived from).
  #
  #       The remaining SEVEN invocations do reach the probe and are all pinned.
  #
  #       Why the distinction is worth the words: a future author who reads
  #       "exactly one exception" would conclude the four unpinned refusal cases are
  #       bugs and "fix" them — adding an override that hides nothing, to cases that
  #       never reach the thing it overrides. Overclaiming a rule invites a wrong
  #       repair as surely as understating one does.
  #
  # WHY: `probe_quiescence` is called at preflight's line 389, BEFORE the
  # lost-baseline guard (`if` at :409), the foreign-directory guard (`if` at :416)
  # and the swept-records guard (`if` at :435; its comment header at :420). It reads
  # the LIVE locks dir. So on any machine where a sibling session holds a lock
  # fresher than 3600s -- the normal condition, not the exceptional one -- preflight
  # refuses there and every guard behind it is STRUCTURALLY UNREACHABLE.
  # (Line numbers are the guards' own `if` lines, stated exactly rather than with a
  # tilde: an earlier wording cited :400 and :420, which are COMMENT headers, not
  # code. On this plan that distinction has cost real rounds.)
  #
  # WHAT THAT COST, measured 2026-08-22 in the run that found this, with two live
  # sessions holding locks aged 315s and 914s. (Later re-runs that same hour read
  # 547s/1146s and 551s/1150s -- heartbeats refresh, so the ages move. ONE incident,
  # ONE pair of numbers: the figures above are the first run's, and mixing values
  # from different runs into a single citation was itself one of this round's
  # findings.) The damage came in all three flavours at once:
  #   * SILENT TRUNCATION — case (8) builds `$work/snap-pf`, which EVERY case below
  #     it uses. Its failure hit a bare `cp` on a missing file, `set -euo pipefail`
  #     killed the script, and the run ended having reported 23 of 53 assertion
  #     sites (21 passed, 2 failed) and never printed its own tally. The other 30
  #     were never attempted -- neither passed nor failed.
  #   * WRONG-REASON FAILURES — four `self_case_because` cases refused for
  #     quiescence instead of their own reason. The reason assertion is what made
  #     these visible rather than falsely green; it earned its weight again.
  #   * A VACUOUS PASS — the swept-records INTACT case asserts on the ABSENCE of
  #     "has LOST", so an early death produced no message and it reported PASS
  #     while verifying nothing. This is the dangerous one.
  #
  # The recorded "53 passed / 0 failed" was therefore a property of a QUIET MACHINE,
  # not of this code. That is this plan's standing lesson in its purest form: a
  # green check whose scope you have not measured is not evidence.
  #
  # The override does NOT weaken anything. The probe still imports taskmanagement,
  # globs the real locks dir and applies the shipped staleness rule; only the
  # threshold is zero, so the code path runs and only the verdict is deterministic.
  # ------------------------------------------------------------------------- #

  # (1) live config is refused. The single cheapest guard in the plan and the one
  #     whose absence is most expensive.
  self_case_because "resolves to the LIVE config dir" "live CLAUDE_CONFIG_DIR is refused" -- \
    env CLAUDE_CONFIG_DIR="$LIVE_CONFIG" SNAP_DIR= bash "$me" preflight
  # ...including when spelled with a trailing slash, which a naive string compare
  #    would wave through.
  self_case_because "resolves to the LIVE config dir" "live CLAUDE_CONFIG_DIR with a trailing slash is refused" -- \
    env CLAUDE_CONFIG_DIR="$LIVE_CONFIG/" SNAP_DIR= bash "$me" preflight
  # ...and when reached through a symlink.
  ln -sfn "$LIVE_CONFIG" "$work/livelink"
  self_case_because "resolves to the LIVE config dir" "live CLAUDE_CONFIG_DIR via a symlink is refused" -- \
    env CLAUDE_CONFIG_DIR="$work/livelink" SNAP_DIR= bash "$me" preflight

  # (2) an interpreter with pytest but NOT pytest_timeout is REJECTED. Without
  #     pytest-timeout there is no timeout mechanism at all here.
  # Delegate to the DISCOVERED interpreter, not a hardcoded /usr/bin/python3:
  # otherwise whether this case reaches the "MISSING pytest_timeout" guard or the
  # unrelated "no interpreter has pytest at all" message depends on whether those
  # two interpreters happen to share a user site-packages -- an assumption asserted
  # nowhere. Hardcoding it made the case's meaning environment-dependent.
  cat > "$work/py-no-timeout" <<FAKE
#!/bin/sh
case "\$*" in
  *pytest_timeout*) exit 1 ;;
esac
exec "$PY" "\$@"
FAKE
  chmod +x "$work/py-no-timeout"
  self_case_because "MISSING pytest_timeout" "interpreter with pytest but no pytest_timeout is rejected" -- \
    env PLAN_ENV_PY_CANDIDATES="$work/py-no-timeout" \
        CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" SNAP_DIR="$work/snap-py" \
        bash "$me" preflight

  # (3) a mutated rolling digest HALTS.
  mkdir -p "$work/fakeclone/hooks" "$work/snap-dig"
  printf 'x\n' > "$work/fakeclone/hooks/pre_plan_gates.py"
  printf 'y\n' > "$work/fakeclone/hooks/reap_orphan_plain_topics.py"
  cat > "$work/snap-dig/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-dig"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$work/fakeclone"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-dig/home"
ENVEOF
  env SNAP_DIR="$work/snap-dig" bash "$me" digest-record >/dev/null 2>&1 || true
  self_case pass "an untouched rolling digest asserts clean" -- \
    env SNAP_DIR="$work/snap-dig" bash "$me" digest-assert
  printf 'MUTATED\n' >> "$work/fakeclone/hooks/pre_plan_gates.py"
  self_case_because "changed outside a recorded slice" "a mutated rolling digest halts" -- \
    env SNAP_DIR="$work/snap-dig" bash "$me" digest-assert
  self_case pass "...and the recorded --rebaseline-digest escape proceeds" -- \
    env SNAP_DIR="$work/snap-dig" bash "$me" digest-assert --rebaseline-digest "selftest"
  # The SECOND escape had no coverage at all in either direction, so nothing
  # established that a crashed run of this plan could actually resume past its own
  # pointer -- the very thing the flag exists for.
  printf 'MUTATED-AGAIN\n' >> "$work/fakeclone/hooks/reap_orphan_plain_topics.py"
  self_case_because "changed outside a recorded slice" \
    "a second mutation halts again (the escape did not disable the guard)" -- \
    env SNAP_DIR="$work/snap-dig" bash "$me" digest-assert
  self_case pass "...and --resume-past-own-run proceeds" -- \
    env SNAP_DIR="$work/snap-dig" bash "$me" digest-assert --resume-past-own-run "selftest"

  # (4) a synthetic FRESH lock halts the snapshot. `cp -a` is not atomic, so a
  #     session writing mid-copy yields a torn snapshot.
  mkdir -p "$work/fakehome/.claude/hooks" "$work/fakehome/.claude/state/locks" \
           "$work/fakehome/.claude/state/pre_plan_gates"
  cp "$CLAUDE_CONFIG_DIR/hooks/taskmanagement.py" "$work/fakehome/.claude/hooks/"
  "$PY" - "$work/fakehome/.claude/state/locks/selftest-topic.lock" <<'MKLOCK'
import datetime as dt, json, sys
now = dt.datetime.now(dt.timezone.utc).isoformat()
open(sys.argv[1], "w").write(json.dumps(
    {"session_id": "selftest", "pid": 1, "started_at": now, "last_heartbeat": now}))
MKLOCK
  # Overriding HOME is HOW the synthetic lock gets injected (taskmanagement reads
  # LOCKS_DIR from Path.home()), but it also hides the user site-packages that
  # pytest lives in on this machine — so without PYTHONPATH this case refuses on
  # "no interpreter has pytest" and never reaches the lock probe it exists to
  # test. That is the same wrong-reason refusal the reason-assertion caught above,
  # and it is why the assertion is worth its weight.
  local user_site; user_site=$("$PY" -c 'import site; print(site.getusersitepackages())')
  self_case_because "not quiescent" "a fresh lock halts the snapshot" -- \
    env HOME="$work/fakehome" CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" \
        PYTHONPATH="$user_site" \
        STATE_DIR="$work/fakehome/.claude/state/pre_plan_gates" \
        SNAP_DIR="$work/snap-lock" bash "$me" preflight

  # (5) a deliberately BROKEN isolation makes `gate` fail. This is the guard that
  #     stops the suite silently importing the live, unfixed modules and passing
  #     because nothing under test changed.
  mkdir -p "$work/snap-broken/home"
  ln -sfn "$LIVE_CONFIG" "$work/snap-broken/home/.claude"     # shim points at LIVE, not the clone
  cat > "$work/snap-broken/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-broken"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-broken/home"
ENVEOF
  self_case_because "ISOLATION FAILED" "a shim pointing away from the clone makes gate's isolation proof FAIL" -- \
    env SNAP_DIR="$work/snap-broken" bash "$me" gate --prove-only
  # ...and a shim that does not exist at all fails too, rather than passing
  #    vacuously the way the unrealizable CLAUDE_CONFIG_DIR_PARENT would have.
  mkdir -p "$work/snap-noshim"
  cat > "$work/snap-noshim/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-noshim"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-noshim/home-that-does-not-exist"
ENVEOF
  self_case_because "ISOLATION FAILED" "a missing shim makes gate's isolation proof FAIL" -- \
    env SNAP_DIR="$work/snap-noshim" bash "$me" gate --prove-only

  printf '[plan_env] selftest — positive paths (a guard tested only in its refusing direction can be a guard that always refuses)\n'

  # (6) POSITIVE: a correctly-built shim makes gate's isolation proof PASS.
  #     Runs the REAL verb (--prove-only keeps it fast). Proving this by
  #     re-running equivalent python inline is what let a KeyError that made
  #     `gate` fail on every invocation sit undetected behind a green selftest.
  mkdir -p "$work/snap-good/home"
  ln -sfn "$CLAUDE_CONFIG_DIR" "$work/snap-good/home/.claude"
  cat > "$work/snap-good/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-good"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-good/home"
ENVEOF
  self_case pass "a correctly-built shim makes gate's isolation proof PASS (real verb)" -- \
    env SNAP_DIR="$work/snap-good" bash "$me" gate --prove-only

  # (6b) THE NEW_TESTS ARM, all three directions. Before the S0 conformance round
  #      this loop was a bare `if [ -f ]` with no else: a new test that had been
  #      created and then LOST was skipped silently and `gate` still exited 0.
  #      No selftest case touched it at all — NEW_TESTS occurred only at its
  #      declaration and in the loop. These run the REAL verb via --resolve-only.
  #      REACHABILITY MUST NOT DEPEND ON A PLAN ARTIFACT BEING ABSENT. Until S0R3
  #      these cases relied on `test_phase_rollback.py` not existing yet — and the
  #      comment here said so in as many words. S3 created it and all FOUR cases
  #      below lost reachability — but not identically, and the difference is the
  #      alarming half: (ii), (iii) and (iv)'s behavioural sub-case FAILED loudly
  #      (they expect a refusal and got exit 0), taking `selftest` 58/0 -> 55/3,
  #      while case (i), which expects a PASS, went silently VACUOUS. A green case
  #      that has stopped testing anything is the harder failure to see, which is
  #      why the fix covers (i) too. That is the SELF-EXPIRING shape case (iv)
  #      below already diagnosed for its own sibling and called "primed to reappear
  #      at the exact slice it guards". It reappeared, at that slice.
  #      The fix is to stop naming a real deliverable: `NT_ABSENT` names no slice's
  #      output, and the assertion below makes that STRUCTURAL rather than a
  #      prediction — an earlier draft of this comment asserted "none ever will",
  #      which is unfalsifiable. So the absent-file branch
  #      stays reachable no matter what the tree grows. Keep the entry WELL-FORMED
  #      (`<file>:<slice>`) — case (v) below is the one that drives a malformed one.
  local NT_ABSENT="test_never_created_by_any_slice__s0r3_fixture.py:S3"
  # Make the non-expiry STRUCTURAL. If this name ever appears, case (i) would go
  # silently vacuous again (it expects a pass, and the file-present branch also
  # exits 0) — the same quiet failure that hid the S3 incident. Fail loudly here
  # instead of letting a green case stop testing anything.
  if [ -e "$CLAUDE_CONFIG_DIR/hooks/tests/${NT_ABSENT%%:*}" ]; then
    printf '[plan_env] selftest: FIXTURE INVALID — %s now exists, so the NEW_TESTS absent-file cases below would not test their branch. Rename NT_ABSENT.\n' \
      "${NT_ABSENT%%:*}" >&2
    return 1
  fi
  mkdir -p "$work/snap-nt/home"
  ln -sfn "$CLAUDE_CONFIG_DIR" "$work/snap-nt/home/.claude"
  cat > "$work/snap-nt/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-nt"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-nt/home"
ENVEOF

  # (i) creating slice NOT completed -> skip, but SAY so.
  printf '{"S3": {"status": "started"}}\n' > "$work/rs-started.json"
  self_case pass "a NEW_TESTS file absent while its slice is unfinished is skipped" -- \
    env SNAP_DIR="$work/snap-nt" PLAN_ENV_RUN_STATE="$work/rs-started.json" \
        PLAN_ENV_NEW_TESTS_OVERRIDE="$NT_ABSENT" \
        bash "$me" gate --resolve-only

  # (ii) creating slice COMPLETED -> the file existed and is now gone -> REFUSE.
  #      This is the branch the old loop could not express, and the whole reason
  #      this fix exists.
  printf '{"S3": {"status": "completed"}}\n' > "$work/rs-done.json"
  self_case_because "is COMPLETED" "a NEW_TESTS file lost after its slice completed makes gate REFUSE" -- \
    env SNAP_DIR="$work/snap-nt" PLAN_ENV_RUN_STATE="$work/rs-done.json" \
        PLAN_ENV_NEW_TESTS_OVERRIDE="$NT_ABSENT" \
        bash "$me" gate --resolve-only

  # (iii) run-state UNREADABLE -> refuse. Cannot verify => do not certify.
  #       This case previously expected a pass-with-warning; round 6 showed that
  #       left `gate` exiting 0, so an automated caller reading only the exit code
  #       could not tell a lost test from a clean run.
  self_case_because "cannot determine whether slice" "an unreadable run-state makes gate REFUSE rather than certify" -- \
    env SNAP_DIR="$work/snap-nt" PLAN_ENV_RUN_STATE="$work/rs-does-not-exist.json" \
        PLAN_ENV_NEW_TESTS_OVERRIDE="$NT_ABSENT" \
        bash "$me" gate --resolve-only

  # (iv) The run-state DEFAULT must derive from LIVE_CONFIG, not from $HOME.
  #
  #      THIS CASE HAS BEEN WRONG TWICE, and the second way is the instructive one.
  #      v1 set PLAN_ENV_RUN_STATE, short-circuiting the very default it named, so
  #      it passed against a $HOME-relative mutant (round 6). v2 moved LIVE_CONFIG
  #      instead and asserted "is COMPLETED" — better, but round 7 showed it was
  #      SELF-EXPIRING: a $HOME-relative mutant resolves to the REAL live run-state
  #      (module-level assignment, before gate's HOME export), which today records
  #      only S0. The moment THIS plan records S3 completed, the mutant prints
  #      "is COMPLETED" too and the case goes quietly vacuous again — the same
  #      defect relocated, primed to reappear at the exact slice it guards.
  #
  #      The harness cannot fix that behaviourally: the mutant reads a path no
  #      fixture here controls (gate overrides HOME itself). So the property is
  #      pinned STATICALLY, which cannot expire, and the behavioural case below is
  #      demoted to what it can honestly prove — that the default path is consulted
  #      at all. Two smaller true claims beat one large one that rots.
  local prs_line
  prs_line=$(grep -n '^PLAN_RUN_STATE=' "$me" | head -1)
  case "$prs_line" in
    *'$LIVE_CONFIG'*)
      case "$prs_line" in
        *'$HOME'*)
          printf '  FAIL  (PLAN_RUN_STATE default mentions $HOME)  %s\n' \
            "the run-state default derives from LIVE_CONFIG, never \$HOME"
          SELF_FAIL=$((SELF_FAIL + 1)) ;;
        *)
          printf '  PASS  (accepted)  the run-state default derives from LIVE_CONFIG, never $HOME (static)\n'
          SELF_PASS=$((SELF_PASS + 1)) ;;
      esac ;;
    *)
      printf '  FAIL  (PLAN_RUN_STATE default does not derive from LIVE_CONFIG)  got: %s\n' "$prs_line"
      SELF_FAIL=$((SELF_FAIL + 1)) ;;
  esac

  # ...and the default path is actually consulted (not merely spelled correctly).
  mkdir -p "$work/fake-live/state/execplan"
  printf '{"S3": {"status": "completed"}}\n' \
    > "$work/fake-live/state/execplan/staged-bouncing-lighthouse.run-state.json"
  self_case_because "is COMPLETED" "the LIVE_CONFIG-derived default run-state is actually read" -- \
    env SNAP_DIR="$work/snap-nt" PLAN_ENV_LIVE_CONFIG="$work/fake-live" \
        PLAN_ENV_NEW_TESTS_OVERRIDE="$NT_ABSENT" \
        bash "$me" gate --resolve-only

  # (v) A malformed NEW_TESTS entry refuses instead of limping. Without the
  #     guard, `${nt%%:*}` and `${nt##*:}` both yield the whole string and the
  #     loop logs "created by <filename>", which reads like a normal skip.
  self_case_because "malformed NEW_TESTS entry" "a NEW_TESTS entry with no colon makes gate REFUSE" -- \
    env SNAP_DIR="$work/snap-nt" PLAN_ENV_RUN_STATE="$work/rs-started.json" \
        PLAN_ENV_NEW_TESTS_OVERRIDE="test_phase_rollback.py" \
        bash "$me" gate --resolve-only

  # (7) POSITIVE: cutover-check exits 0 against an UNDRIFTED baseline.
  mkdir -p "$work/snap-cut"
  cat > "$work/snap-cut/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-cut"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-cut/home"
ENVEOF
  : > "$work/snap-cut/DIGEST_BASELINE"
  for f in $WRITE_TARGETS; do
    printf '%s  %s\n' "$(sha_of "$LIVE_CONFIG/$f")" "$f" >> "$work/snap-cut/DIGEST_BASELINE"
  done
  self_case pass "cutover-check exits 0 against an undrifted baseline" -- \
    env SNAP_DIR="$work/snap-cut" bash "$me" cutover-check
  # ...and refuses the moment one target moves.
  "$PY" - "$work/snap-cut/DIGEST_BASELINE" <<'MUT'
import sys
p = sys.argv[1]
rows = open(p).read().splitlines()
rows[0] = "0" * 64 + rows[0][64:]
open(p, "w").write("\n".join(rows) + "\n")
MUT
  self_case_because "DRIFT" "cutover-check halts on a drifted write target" -- \
    env SNAP_DIR="$work/snap-cut" bash "$me" cutover-check

  # (8) PREFLIGHT'S SUCCESS PATH. Every preflight case above is a refusal that
  #     exits before SNAP_DIR, the shim, ENV, DIGEST_BASELINE and the snapshot are
  #     ever built -- so the foundation every other verb stands on had no positive
  #     coverage at all. It runs BEFORE the anchors cases because they need a real
  #     SNAP_DIR, and this is what builds one.
  # TM_STALE_T_SECONDS=0 is REQUIRED here, and it is not a weakening of the
  # quiescence guard. This case's subject is ARTIFACT-BUILDING; quiescence has its
  # own dedicated case above ((4), "a fresh lock halts the snapshot"), which injects
  # a synthetic lock under a fakehome and is therefore deterministic. THIS case does
  # NOT redirect HOME -- it must not, because it is the end-to-end path over the real
  # STATE_DIR -- so without the override it reads the LIVE locks dir and refuses
  # whenever ANY concurrent session holds a lock fresher than 3600s. That is not a
  # flake: every case below runs against `$work/snap-pf`, the snapshot THIS case
  # builds, so a live sibling session silently gated MORE THAN HALF the suite. The
  # negative direction was isolated and the positive one was not -- the same
  # asymmetry this file exists to catch. Measured 2026-08-22: 30 of 53 assertion
  # sites never ran, with two live sessions holding locks aged 315s and 914s.
  #
  # The probe still RUNS end-to-end (imports taskmanagement, globs the real locks
  # dir, compares against the shipped rule); only its threshold is forced to zero,
  # so the code path is exercised and only the verdict is made deterministic.
  # Accepted and stated rather than silent: the `cp -a` may therefore copy a state
  # dir that is being written. That is harmless HERE and nowhere else -- this case
  # asserts only that ENV / DIGEST_BASELINE / DIGEST_ROLLING / the shim exist and
  # that the baseline is write-once, none of which depend on record integrity, and
  # the copy is a scratch dir that is discarded. Do NOT read this line as evidence
  # that the guard is unnecessary for a real `preflight`; there the torn-snapshot
  # hazard is exactly why it refuses.
  self_case pass "preflight SUCCEEDS end-to-end and builds its artifacts" -- \
    env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
        TM_STALE_T_SECONDS=0 \
        SNAP_DIR="$work/snap-pf" bash "$me" preflight
  local pf_ok=1 f
  for f in ENV DIGEST_BASELINE DIGEST_ROLLING home/.claude; do
    [ -e "$work/snap-pf/$f" ] || { pf_ok=0; printf '        missing: %s\n' "$f"; }
  done
  [ -L "$work/snap-pf/home/.claude" ] || { pf_ok=0; printf '        shim is not a symlink\n'; }
  if [ "$pf_ok" -eq 1 ]; then
    printf '  PASS  (accepted)  ...and ENV, DIGEST_BASELINE, DIGEST_ROLLING and the shim all exist\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  preflight exited 0 but did not build its artifacts\n'
    SELF_FAIL=$((SELF_FAIL + 1))
  fi
  # STOP DELIBERATELY, WITH A TALLY, rather than dying on the next command.
  # Every case below this point runs against `$work/snap-pf`. Before this guard the
  # bare `cp` on the next line hit a missing DIGEST_BASELINE and `set -euo pipefail`
  # killed the script THERE -- so the run reported 23 of 53 assertion sites, never
  # printed its own "N passed, M failed" summary, and said nothing about the 30 it
  # had not attempted. A red report whose scope is unstated is the same defect as a
  # green one whose scope is unstated. Continuing instead would cascade ~30 failures
  # against a snapshot that does not exist and bury the one real cause, so the honest
  # move is to stop and say how far we got and why.
  if [ "$pf_ok" -ne 1 ]; then
    printf '\n[plan_env] selftest: %s passed, %s failed\n' "$SELF_PASS" "$SELF_FAIL"
    # %s, not a single-quoted $work: single quotes suppress expansion, so an earlier
    # revision of this line printed the LITERAL text "$work/snap-pf" at an operator
    # who needed the real path -- three lines above a sibling printf that substituted
    # it correctly. Found by a conformance checker, in the very block added to make a
    # failure legible.
    printf '[plan_env] STOPPING EARLY — preflight did not build %s, and every\n' \
      "$work/snap-pf"
    printf '           remaining case runs against it. Cases after this point were NOT\n'
    printf '           attempted; they are neither passed nor failed. Re-run once the\n'
    printf '           cause above is resolved before believing any tally.\n'
    printf '[plan_env] selftest scratch left at %s (never auto-deleted — safe-defaults.md)\n' "$work"
    exit 1
  fi
  # DIGEST_BASELINE is write-once: a second preflight must NOT rewrite it.
  # ASSERT THE SECOND PREFLIGHT SUCCEEDED. Discarding its status with `|| true` and
  # then asserting a file DID NOT CHANGE is satisfied trivially by any preflight that
  # dies before reaching the write-once branch — so the reachable failure is the
  # dangerous one: this is the REUSE path, and a wrongly-firing guard would produce a
  # PASS reading "genuinely write-once" while nothing was verified. Same vacuity that
  # case (16) carried; fixed there first and left here, found by a checker asked to
  # hunt second instances.
  cp "$work/snap-pf/DIGEST_BASELINE" "$work/snap-pf/.baseline.first"
  local wo_rc
  env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
      TM_STALE_T_SECONDS=0 \
      SNAP_DIR="$work/snap-pf" bash "$me" preflight >/dev/null 2>&1 && wo_rc=0 || wo_rc=$?
  if [ "$wo_rc" -ne 0 ]; then
    printf '  FAIL  the second preflight did not complete (rc=%s) — the write-once branch was never reached\n' "$wo_rc"
    SELF_FAIL=$((SELF_FAIL + 1))
  elif cmp -s "$work/snap-pf/.baseline.first" "$work/snap-pf/DIGEST_BASELINE"; then
    printf '  PASS  (accepted)  DIGEST_BASELINE is genuinely write-once across two preflights (rc=0)\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  DIGEST_BASELINE was rewritten by a second preflight\n'
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (9) THE ANCHORS VERB, which had NO coverage whatever -- neither direction --
  #     despite being the largest and most complex logic in this file and despite
  #     this script's whole premise being that an unrun guard is only a promise.
  #     That gap was not theoretical: a citation shape the matcher could not see
  #     survived into the plan and was found by a reviewer, not by the tool built
  #     to find exactly that.
  mkdir -p "$work/anchor"
  # A fixture plan whose citation is CORRECT: `sha_of` really is where we say.
  local sha_line
  # `grep | head | cut` exits 0 on no match (head/cut succeed on empty input), so
  # a missing anchor would silently yield an empty line number and build fixtures
  # that test nothing. Fail loudly instead — the same class of bug this file
  # otherwise guards against.
  sha_line=$(grep -n '^sha_of() {' "$me" | head -1 | cut -d: -f1)
  [ -n "$sha_line" ] || die "selftest: cannot locate the sha_of anchor in $me"
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| the helper exists | `plan_env.sh:%s` `sha_of() {` |\n' "$sha_line"
  } > "$work/anchor/good.md"
  self_case pass "anchors ACCEPTS a plan whose citation resolves" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/good.md"
  # The same fixture, citation moved far away: it must be caught.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| the helper exists | `plan_env.sh:%s` `sha_of() {` |\n' \
      "$((sha_line + 400))"
  } > "$work/anchor/stale.md"
  self_case_because "DRIFT" "anchors CATCHES a stale citation" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/stale.md"
  # A citation with NO token beside it must be reported, not silently skipped --
  # the early-return that made this invisible is the defect this case locks shut.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| tokenless | `plan_env.sh:%s` with nothing to re-derive it from |\n' \
      "$sha_line"
  } > "$work/anchor/tokenless.md"
  self_case_because "NO-TOKEN" "anchors REPORTS a citation with no token beside it" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/tokenless.md"

  # The TABULAR shape had no coverage at all, which is why a masking retry could
  # ship in it unnoticed. Both directions, against a two-column table whose header
  # names the file.
  {
    printf '# fixture\n\n'
    printf '| Symbol | plan_env.sh |\n|---|---|\n'
    printf '| `sha_of() {` | %s |\n' "$sha_line"
  } > "$work/anchor/tab-good.md"
  self_case pass "anchors ACCEPTS a correct tabular row" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/tab-good.md"
  {
    printf '# fixture\n\n'
    printf '| Symbol | plan_env.sh |\n|---|---|\n'
    printf '| `sha_of() {` | %s |\n' "$((sha_line + 400))"
  } > "$work/anchor/tab-stale.md"
  self_case_because "DRIFT" "anchors CATCHES a stale tabular row" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/tab-stale.md"
  # A row whose two numbers are listed in the OPPOSITE order from its two tokens
  # must still be reported as a DRIFT -- never silently accepted because the other
  # token happens to sit near the number. This is the masking defect locked shut.
  local die_line
  die_line=$(grep -n '^die()' "$me" | head -1 | cut -d: -f1)
  [ -n "$die_line" ] || die "selftest: cannot locate the die anchor in $me"
  {
    printf '# fixture\n\n'
    printf '| Symbol | plan_env.sh |\n|---|---|\n'
    printf '| `sha_of() {` / `die()` | %s / %s |\n' "$die_line" "$sha_line"
  } > "$work/anchor/tab-swapped.md"
  self_case_because "DRIFT" "anchors REFUSES to mask a swapped-order tabular row" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/tab-swapped.md"

  # (10) cutover-check must NOT report byte-identity for a file it could not
  #      measure. Two UNREADABLE sentinels compare equal as plain strings, and
  #      that is precisely how a false all-clear would reach the one guard
  #      standing between this plan and a third party's work.
  mkdir -p "$work/snap-unread"
  cat > "$work/snap-unread/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-unread"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-unread/home"
ENVEOF
  printf 'UNREADABLE  hooks/pre_plan_gates.py\n' > "$work/snap-unread/DIGEST_BASELINE"
  self_case_because "UNMEASURABLE" \
    "cutover-check REFUSES to clear a target it could not measure (baseline side)" -- \
    env SNAP_DIR="$work/snap-unread" bash "$me" cutover-check
  # ...and the OTHER side of that OR: a good baseline whose live file has since
  # become unreadable. Covering only one side of a two-sided condition is how a
  # half-working guard reads as a working one.
  mkdir -p "$work/unreadable-live/hooks"
  printf 'x\n' > "$work/unreadable-live/hooks/pre_plan_gates.py"
  chmod 000 "$work/unreadable-live/hooks/pre_plan_gates.py"
  mkdir -p "$work/snap-unread2"
  cat > "$work/snap-unread2/ENV" <<ENVEOF
PY="$PY"
SNAP_DIR="$work/snap-unread2"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$work/snap-unread2/home"
ENVEOF
  printf '%s  hooks/pre_plan_gates.py\n' "$(printf '0%.0s' $(seq 64))" \
    > "$work/snap-unread2/DIGEST_BASELINE"
  self_case_because "UNMEASURABLE" \
    "cutover-check REFUSES when the LIVE side became unreadable" -- \
    env SNAP_DIR="$work/snap-unread2" PLAN_ENV_LIVE_CONFIG="$work/unreadable-live" \
        bash "$me" cutover-check
  chmod 644 "$work/unreadable-live/hooks/pre_plan_gates.py" 2>/dev/null || true

  # (11) A LOST SNAP_DIR must not silently re-baseline. If the directory's contents
  #      went away (reboot, /var/folders sweep, premature cleanup) and the operator
  #      re-exports the same path, regenerating DIGEST_BASELINE from current live
  #      would absorb any concurrent edit made since S0 -- cutover-check would then
  #      pass clean while the carry still reverted that edit. Silent, and it
  #      destroys a third party's work in the guard built to prevent exactly that.
  mkdir -p "$work/snap-lost"
  cp "$work/snap-pf/ENV" "$work/snap-lost/ENV"
  # TM_STALE_T_SECONDS=0: see THE QUIESCENCE RULE at the top of selftest. Both
  # guards below sit AFTER probe_quiescence in preflight's contract order, so
  # without it they are unreachable whenever a sibling session holds a fresh lock.
  self_case_because "NO DIGEST_BASELINE" \
    "preflight REFUSES a SNAP_DIR whose baseline was lost" -- \
    env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
        TM_STALE_T_SECONDS=0 \
        SNAP_DIR="$work/snap-lost" bash "$me" preflight
  # ...and refuses to write into a non-empty directory it did not create at all.
  mkdir -p "$work/snap-foreign"
  printf 'someone else lives here\n' > "$work/snap-foreign/important.txt"
  self_case_because "refusing to write into a" \
    "preflight REFUSES a non-empty directory it did not create" -- \
    env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
        TM_STALE_T_SECONDS=0 \
        SNAP_DIR="$work/snap-foreign" bash "$me" preflight

  # (12) anchors --write: the ONE path that edits a real file, and the one that had
  #      no coverage at all in either direction. An untested mutation path in a
  #      script whose whole premise is that untested guards are only promises.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| the helper exists | `plan_env.sh:%s` `sha_of() {` |\n' \
      "$((sha_line + 400))"
  } > "$work/anchor/writeme.md"
  env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
      bash "$me" anchors --plan "$work/anchor/writeme.md" --write >/dev/null 2>&1 || true
  self_case pass "anchors --write REPAIRS a stale citation (the file then passes)" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/writeme.md"
  # ...and it must REFUSE, not guess, when the number it would rewrite appears
  #    twice on the row -- the shape that would otherwise rewrite the wrong span.
  # A TABULAR row, deliberately: the ambiguity guard belongs to the kinds that
  # substitute a BARE number. A typed citation is unambiguous by construction (its
  # substitution is scoped to `<file>:<number>`), so a typed fixture here would be
  # testing a guard that correctly no longer applies.
  {
    printf '# fixture\n\n'
    printf '| Symbol | plan_env.sh |\n|---|---|\n'
    printf '| `sha_of() {` | %s and again %s |\n' \
      "$((sha_line + 400))" "$((sha_line + 400))"
  } > "$work/anchor/ambiguous.md"
  # TWO assertions, and the first is the one that matters. Comparing file bytes
  # alone cannot tell "refused because the row was ambiguous" apart from "crashed
  # before writing" or "was never flagged as a drift at all" -- the wrong-reason
  # pass this file built self_case_because to prevent. Assert the REASON first,
  # then that the file really is untouched.
  local amb_before amb_after
  amb_before=$(cat "$work/anchor/ambiguous.md")
  # Assert the SPECIFIC reason, not the generic one. `die()` prints "REFUSED:" on
  # essentially every failure path in this file, so matching that word would accept
  # a refusal for any unrelated cause -- a missing SNAP_DIR, a failed require_env --
  # as proof the ambiguity guard fired. Every other case here uses a specific
  # fragment; this one had regressed to the generic word.
  self_case_because "appears more than once on the row" \
    "anchors --write REFUSES an ambiguous row, and says why" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/ambiguous.md" --write
  # A TYPED citation plus its `[verified: file:N]` restatement must be REPAIRED,
  # not refused -- both occurrences name the same fact, so there is nothing
  # ambiguous about them. The previous guard counted bare digits across the raw
  # line, saw two, and refused: the exact case the code's own comment claimed to
  # support. Both occurrences must end up rewritten, or the row is left
  # self-contradicting.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence | status |\n|---|---|---|\n'
    printf '| restated | `plan_env.sh:%s` `sha_of() {` | [verified: plan_env.sh:%s] |\n' \
      "$((sha_line + 400))" "$((sha_line + 400))"
  } > "$work/anchor/restated.md"
  env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
      bash "$me" anchors --plan "$work/anchor/restated.md" --write >/dev/null 2>&1 || true
  if grep -q "plan_env.sh:$sha_line" "$work/anchor/restated.md" \
     && ! grep -q "plan_env.sh:$((sha_line + 400))" "$work/anchor/restated.md"; then
    printf '  PASS  (accepted)  anchors --write repairs a citation AND its [verified:] restatement\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  the citation and its restatement were not both repaired\n'
    printf '        row now: %s\n' "$(grep restated "$work/anchor/restated.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  amb_after=$(cat "$work/anchor/ambiguous.md")
  if [ "$amb_before" = "$amb_after" ]; then
    printf '  PASS  (accepted)  ...and left the ambiguous row byte-identical\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  anchors --write rewrote an ambiguous row\n'
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (13) A RANGE IS ONE CITATION. `| ... | N-M |` cites a span, and repairing only
  #      the endpoint that paired with a token produced a range reading BACKWARDS
  #      (`2639-2558`) — corruption, not staleness, and it SHIPPED into the plan.
  #      Both endpoints must shift by the same delta so the span keeps its width.
  {
    printf '# fixture\n\n'
    printf '| Symbol | plan_env.sh |\n|---|---|\n'
    printf '| `sha_of() {` | %s-%s |\n' "$((sha_line + 400))" "$((sha_line + 410))"
  } > "$work/anchor/range.md"
  env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
      bash "$me" anchors --plan "$work/anchor/range.md" --write >/dev/null 2>&1 || true
  if grep -q "| $sha_line-$((sha_line + 10)) |" "$work/anchor/range.md"; then
    printf '  PASS  (accepted)  anchors --write shifts BOTH endpoints of a range, keeping its width\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  a range was half-repaired (the corruption shape)\n'
    printf '        row now: %s\n' "$(grep sha_of "$work/anchor/range.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # ...and a range whose repair would INVERT it is refused, never written.
  # The fixture is DESCENDING on purpose (`N-(N-1)`). With both endpoints now
  # shifting together, a well-formed ascending range can never invert — so the
  # only way to reach this guard is input that was already malformed, which is
  # exactly the case where silently "repairing" it would launder a defect into
  # something that looks correct. Written this way rather than deleted: a guard
  # with no reachable test is a guard nobody can trust.
  {
    printf '# fixture\n\n'
    printf '| Symbol | plan_env.sh |\n|---|---|\n'
    printf '| `sha_of() {` | %s-%s |\n' "$((sha_line + 400))" "$((sha_line + 399))"
  } > "$work/anchor/invert.md"
  self_case_because "would invert the range" \
    "anchors --write REFUSES a range repair that would invert it" -- \
    env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
        bash "$me" anchors --plan "$work/anchor/invert.md" --write

  # (14) A typed citation and a BARE restatement of the same number on the same row
  #      are ONE fact — the detector reports them once. Repairing only the typed
  #      form left the row contradicting itself ("line 999: `x`, guarded at 1255").
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| both forms | `plan_env.sh:%s` `sha_of() {` at line %s |\n' \
      "$((sha_line + 400))" "$((sha_line + 400))"
  } > "$work/anchor/bareform.md"
  env SNAP_DIR="$work/snap-pf" PLAN_ENV_ANCHOR_FILES_OVERRIDE="plan_env.sh" \
      bash "$me" anchors --plan "$work/anchor/bareform.md" --write >/dev/null 2>&1 || true
  if ! grep -q "$((sha_line + 400))" "$work/anchor/bareform.md"; then
    printf '  PASS  (accepted)  anchors --write repairs a typed citation AND its bare restatement\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  a bare restatement was left stale beside its repaired typed citation\n'
    printf '        row now: %s\n' "$(grep 'both forms' "$work/anchor/bareform.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (14b) A MULTI-FACT ROW: the bare number must be repaired to ITS OWN fact's line,
  #      not to the line of whichever token happens to sit numerically nearest the
  #      STALE value. This is the S0R2 defect (2026-08-23), and the fixture is the
  #      real Gate-1 row that exposed it:
  #        "line 2677: `_derived = canonical_project_for_spine(...)`,
  #         with `project_slug = _derived` at 2679"
  #      Two facts, two tokens, two lines. After a +34 shift the correct answers are
  #      2711 and 2713. `--write` wrote 2711 for BOTH, because the DRIFT selector
  #      picked the token whose occurrence was closest to the stale 2679 (32 away)
  #      over the semantically correct one (34 away).
  #
  #      THE SELECTION WAS CIRCULAR: it let the stale number vote on its own
  #      replacement. That is why the corruption came out SELF-CONSISTENT and
  #      survived review — the wrong answer looked plausible precisely because it
  #      was derived from the wrong premise. Proximity ON THE ROW is the
  #      non-circular signal, and `tokens_near` already computes it; the DRIFT
  #      branch was throwing that order away.
  #
  #      The fixture spaces the two symbols 2 lines apart and puts the stale
  #      citations 34 lines below, so numeric-closeness and row-proximity give
  #      DIFFERENT answers. A fixture whose two rules agree cannot fail, which is
  #      how this survived cases (12)-(14).
  mkdir -p "$work/anchor/src/hooks"
  {
    local _pad
    for _pad in $(seq 1 140); do printf '# pad %s\n' "$_pad"; done
    printf 'def alpha_call():\n'          # line 141
    printf '    pass\n'                   # line 142
    printf 'beta_assign = 1\n'            # line 143
  } > "$work/anchor/src/hooks/twofact.py"
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| two facts | `twofact.py:107` line 107: `def alpha_call():`, with `beta_assign = 1` at 109 |\n'
  } > "$work/anchor/twofact.md"
  env SNAP_DIR="$work/snap-pf" \
      PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/src/hooks" \
      PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofact.py" \
      bash "$me" anchors --plan "$work/anchor/twofact.md" --write >/dev/null 2>&1 || true
  if grep -q 'at 143' "$work/anchor/twofact.md"; then
    printf '  PASS  (accepted)  anchors --write repairs a bare number to ITS OWN fact (143), not the neighbour (141)\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  a bare number was repaired to the numerically-nearest token, not its own fact\n'
    printf '        want `at 143`; row now: %s\n' \
      "$(grep 'two facts' "$work/anchor/twofact.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (14c) A GATE ROW ENDING IN A `[verified: <file>:<n>]` STAMP. The repairer's
  #      rewrite window used to start at the LAST typed match on the row — which on
  #      this shape is the stamp itself, so the evidence column in the middle was
  #      structurally unreachable and its `line <n>:` restatement was never
  #      repaired. The row then contradicted itself: Source Location repaired,
  #      evidence prose stale. This is the dominant row shape in Gate 1 and Gate 3,
  #      so the blind spot covered most of the plan's source-grounding surface.
  #      Both halves are asserted here — repairing one without the other is the
  #      half-repair this whole verb keeps regressing into.
  {
    printf '# fixture\n\n'
    printf '| claim | Category | Source | Verified Against | Status |\n'
    printf '|---|---|---|---|---|\n'
    printf '| stamped row | Code | `twofact.py:107` | line 107: `def alpha_call():` | [verified: twofact.py:107] |\n'
  } > "$work/anchor/stamped.md"
  env SNAP_DIR="$work/snap-pf" \
      PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/src/hooks" \
      PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofact.py" \
      bash "$me" anchors --plan "$work/anchor/stamped.md" --write >/dev/null 2>&1 || true
  if grep -q 'line 141:' "$work/anchor/stamped.md" \
     && ! grep -q ':107' "$work/anchor/stamped.md"; then
    printf '  PASS  (accepted)  anchors --write repairs the evidence prose of a `[verified:]`-stamped row too\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  a stamped gate row was left self-contradicting (location repaired, prose stale)\n'
    printf '        row now: %s\n' "$(grep 'stamped row' "$work/anchor/stamped.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (14d) The WINDOW mechanism itself: a bare number BEFORE the first file
  #      reference is prose, not a citation, and must survive a repair of the same
  #      row. A past defect rewrote "failures at 100 requests/sec" into "at 120".
  #
  #      THIS CASE WAS VACUOUS WHEN FIRST WRITTEN, and the reason is worth keeping.
  #      Its fixture's only backticked span was `twofact.py:107`, which
  #      `usable_token` REJECTS (it contains a TYPED citation), so the row had no
  #      usable token, `judge` returned NO-TOKEN, and the writer skipped the row
  #      entirely. The assertion then held no matter what the window did — a
  #      regression guard that guarded nothing, in the very case whose comment said
  #      a missing guard is how this verb acquires defects. It is fixed by giving
  #      the row a real token AND asserting the row was actually processed.
  #
  #      SCOPE, stated honestly: this does NOT discriminate first-match from
  #      last-match anchoring. Prose ahead of the FIRST citation sits in `head`
  #      under BOTH rules, so no fixture of this shape can fail on that change.
  #      It guards the window's existence, not (14c)'s change to its start. The
  #      region (14c) actually opened is covered by (14e) below.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| prose first | failures at 107 requests/sec — see `twofact.py:107` and `def alpha_call():` |\n'
  } > "$work/anchor/prosefirst.md"
  env SNAP_DIR="$work/snap-pf" \
      PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/src/hooks" \
      PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofact.py" \
      bash "$me" anchors --plan "$work/anchor/prosefirst.md" --write >/dev/null 2>&1 || true
  if grep -q 'twofact.py:141' "$work/anchor/prosefirst.md" \
     && grep -q 'at 107 requests/sec' "$work/anchor/prosefirst.md"; then
    printf '  PASS  (accepted)  anchors --write repaired the row AND left pre-citation prose untouched\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  pre-citation prose guard is vacuous or broken\n'
    printf '        (want BOTH `twofact.py:141` — proving the row was processed — and `at 107 requests/sec`)\n'
    printf '        row now: %s\n' "$(grep 'prose first' "$work/anchor/prosefirst.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (14e) CHARACTERISATION of the widening (14c) introduced — residual 11. Prose
  #      sitting BETWEEN two citations of the same file IS now rewritten, where
  #      last-match anchoring left it alone. This is a real, accepted cost: the
  #      evidence column is the dominant gate-row shape and was wholly unreachable
  #      before, so the alternative was every gate row contradicting its own Source
  #      Location. The collision is undecidable from inside the repairer — a number
  #      restating the citation and a number that is prose are the same token.
  #
  #      It is asserted here so the cost is EXECUTABLE rather than a paragraph of
  #      prose nobody re-reads: if a future change closes it, this case goes red and
  #      whoever closed it learns they fixed a documented residual. A known defect
  #      with a test is a decision; a known defect with only a comment is a rumour.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| between | `twofact.py:107` — failures at 107 requests/sec — `def alpha_call():` [verified: twofact.py:107] |\n'
  } > "$work/anchor/between.md"
  env SNAP_DIR="$work/snap-pf" \
      PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/src/hooks" \
      PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofact.py" \
      bash "$me" anchors --plan "$work/anchor/between.md" --write >/dev/null 2>&1 || true
  if grep -q 'at 141 requests/sec' "$work/anchor/between.md"; then
    printf '  PASS  (accepted)  between-citation prose IS rewritten — residual 11, characterised not claimed fixed\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  residual 11 changed behaviour — between-citation prose was NOT rewritten\n'
    printf '        this may be an IMPROVEMENT; if so, update residual 11 and this case deliberately\n'
    printf '        row now: %s\n' "$(grep 'between' "$work/anchor/between.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (14f) The FAIL-CLOSED refusal, which had no test at all until 2026-08-23 — in a
  #      file whose own rule is that a guard with no reachable test is a guard nobody
  #      can trust. It fires when a citation is DETECTED but cannot be SUBSTITUTED:
  #      the detector's TYPED pattern carries no trailing guard while the substitution
  #      appends one, so `twofact.py:107.` at the end of a sentence is found by the
  #      former and skipped by the latter.
  #
  #      BOTH halves are asserted, because the defect this replaced was a SILENT
  #      no-op: the row must be left byte-identical (nothing guessed) AND the refusal
  #      must be PRINTED (the writer's contract is that a drift never disappears
  #      quietly). Asserting only the first would pass for the old broken behaviour.
  {
    printf '# fixture\n\n'
    printf '| claim | evidence |\n|---|---|\n'
    printf '| unsubstitutable | `def alpha_call():` — see twofact.py:107. |\n'
  } > "$work/anchor/failclosed.md"
  local fc_before fc_out
  fc_before=$(cat "$work/anchor/failclosed.md")
  fc_out=$(env SNAP_DIR="$work/snap-pf" \
      PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/src/hooks" \
      PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofact.py" \
      bash "$me" anchors --plan "$work/anchor/failclosed.md" --write 2>&1) || true
  case "$fc_out" in
    *"could not be located on the row"*)
      if [ "$fc_before" = "$(cat "$work/anchor/failclosed.md")" ]; then
        printf '  PASS  (refused for the right reason)  anchors --write REFUSES OUT LOUD when a detected citation cannot be substituted\n'
        SELF_PASS=$((SELF_PASS + 1))
      else
        printf '  FAIL  the fail-closed branch printed a refusal but still modified the row\n'
        printf '        row now: %s\n' "$(grep 'unsubstitutable' "$work/anchor/failclosed.md" | head -1)"
        SELF_FAIL=$((SELF_FAIL + 1))
      fi ;;
    *)
      printf '  FAIL  a detected-but-unsubstitutable citation was skipped SILENTLY (no REFUSED line)\n'
      printf '        %s\n' "$(printf '%s' "$fc_out" | tail -3)"
      SELF_FAIL=$((SELF_FAIL + 1)) ;;
  esac

  # (15) A token that survives ONLY in a comment is a DELETED symbol whose epitaph
  #      remains. Repairing the citation onto the prose about its removal asserts
  #      the symbol lives where it demonstrably does not, and silences the report
  #      the reader needed. That happened to `_derive_topic_slug` after S1.
  # NB the path: `file_lines` resolves a name against `$CLAUDE_CONFIG_DIR/hooks`
  # (and its `tests/` child), so the fixture module must live under a `hooks/`
  # directory or it reads back UNREADABLE and the case passes for the wrong reason.
  mkdir -p "$work/anchor/src/hooks"
  {
    printf 'def real_function():\n    return 1\n\n\n'
    printf '# `gone_symbol` used to live here; deleted 2026-08-22.\n'
  } > "$work/anchor/src/hooks/ghost.py"
  {
    printf '# fixture\n\n'
    printf '| Symbol | ghost.py |\n|---|---|\n'
    printf '| `gone_symbol` | 99 |\n'
  } > "$work/anchor/ghost.md"
  local ghost_before ghost_after
  ghost_before=$(cat "$work/anchor/ghost.md")
  self_case_because "COMMENT-ONLY" \
    "anchors REPORTS a token surviving only in a comment, instead of repairing onto it" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/src/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="ghost.py" \
        bash "$me" anchors --plan "$work/anchor/ghost.md" --write
  ghost_after=$(cat "$work/anchor/ghost.md")
  if [ "$ghost_before" = "$ghost_after" ]; then
    printf '  PASS  (accepted)  ...and left the comment-only row byte-identical\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  anchors --write repaired a citation onto a comment\n'
    printf '        row now: %s\n' "$(grep gone_symbol "$work/anchor/ghost.md" | head -1)"
    SELF_FAIL=$((SELF_FAIL + 1))
  fi

  # (15b) SCOPE DISAMBIGUATION — the ROOT CAUSE. Resolution was "nearest occurrence
  #       of any usable token on the row", so a token appearing in several functions
  #       bound to whichever copy happened to be closest. On the real plan that
  #       rewrote a `phase_start` citation into `bypass_topic`. An Anchor `↳` sub-row
  #       names its enclosing function ONLY via the parent row above it, so the
  #       fixture reproduces exactly that shape.
  # THE FIXTURE MUST BE ABLE TO FAIL. A first version put the two occurrences at
  # lines 2 and 7 and cited both EXACTLY — which cannot discriminate, twice over:
  # `min()` picks an exact match whether or not the set was narrowed, and 7-2=5 is
  # exactly TOL, so even a wrong pick scores `ok`. It passed identically with the
  # whole mechanism stubbed out. That is round 1's own finding — a check that
  # examines only the subset already correct — reproduced inside the fix's test.
  #
  # This version discriminates: the two occurrences are FAR apart, and beta's row
  # cites a STALE number that sits within TOL of ALPHA's occurrence. Unscoped,
  # nearest-anywhere lands on alpha's line and reports `ok` — silently accepting a
  # citation 38 lines wrong. Scoped, the set narrows to beta's own occurrence and
  # the staleness is REPORTED.
  # NB the two-digit line numbers: the number regex is `\d{2,5}`, so a fixture
  # citing a SINGLE-digit line is invisible to the verb and the case passes
  # because nothing was checked at all. The first build of this fixture cited
  # line 4 and did exactly that.
  mkdir -p "$work/anchor/scope/hooks"
  {
    local _i
    for _i in $(seq 1 9); do printf '# pad %s\n' "$_i"; done
    printf 'def alpha():\n'                              # 10
    printf '    shared = compute(session_id)\n'          # 11
    printf '    return shared\n'                         # 12
    for _i in $(seq 13 49); do printf '# pad %s\n' "$_i"; done
    printf 'def beta():\n'                               # 50
    printf '    shared = compute(session_id)\n'          # 51
    printf '    return shared\n'                         # 52
    printf '    beta_marker = 1\n'                       # 53
    printf 'def gamma():\n'                              # 54
    printf '    beta_marker = 1\n'                       # 55
    # S0R3 (15b3) needs a UNIQUE token living outside the row's scope: with one
    # occurrence `_narrow_by_scope` short-circuits, so the citation is judged an
    # ordinary DRIFT and only the REPAIR-loop guard can stop it crossing into
    # another function. Appended last so no existing line number shifts.
    printf '    unique_marker = 7\n'                     # 56
  } > "$work/anchor/scope/hooks/twofn.py"
  # `beta_marker` deliberately occurs TWICE, outside alpha. A single-occurrence
  # token cannot reach SCOPE-MISS at all — `_narrow_by_scope` returns early when
  # there is nothing to disambiguate, and the honest report is then an ordinary
  # DRIFT. The first version of the case below used a unique token and so tested
  # the wrong branch.
  # beta's row cites 13 — 38 lines off its own occurrence (51), but only 2 from
  # ALPHA's (11). Unscoped nearest-anywhere lands on alpha and scores `ok`.
  {
    printf '# fixture\n\n'
    printf '| Symbol | twofn.py |\n|---|---|\n'
    printf '| `def beta():` | 50 |\n'
    printf '| ↳ `shared = compute(session_id)` | 13 |\n'
  } > "$work/anchor/scope.md"
  self_case_because "51" \
    "anchors disambiguates a repeated token by scope (a stale sub-row citation that nearest-anywhere would accept is REPORTED)" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/scope.md"
  # ...and the same table with beta's own line cited correctly must RESOLVE, so the
  # narrowing is not simply refusing everything.
  {
    printf '# fixture\n\n'
    printf '| Symbol | twofn.py |\n|---|---|\n'
    printf '| `def beta():` | 50 |\n'
    printf '| ↳ `shared = compute(session_id)` | 51 |\n'
  } > "$work/anchor/scope-ok.md"
  self_case pass "...and accepts the same row when it cites beta's own occurrence" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/scope-ok.md"

  # (15b1) S0R3 — A BARE LANGUAGE KEYWORD IS NOT AN ANCHOR. `return` occurs in every
  #        function in the fixture, so nearest-occurrence against it is meaningless
  #        even after scope narrowing. Before this guard a row citing "the terminal
  #        `return`" was repaired onto an EARLIER `return` in the same function —
  #        the tool introducing an error where none existed. The row must now report
  #        rather than resolve.
  {
    printf '# fixture\n\n'
    printf '| Symbol | twofn.py |\n|---|---|\n'
    printf '| `def beta():` | 50 |\n'
    printf '| ↳ `return` | 52 |\n'
  } > "$work/anchor/bare-kw.md"
  self_case_because "NO-TOKEN" \
    "S0R3: a row whose only token is a bare keyword is REPORTED, not resolved by nearest-occurrence" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/bare-kw.md"

  # (15b2) S0R3 — AN EXPLICIT `line <N>: \`tok\`` BINDING IS EXCLUSIVE. A Gate row
  #        names which token its number is for. Before this guard, when that token
  #        was GONE the citation fell through to whatever else the row mentioned and
  #        was repaired onto it — a row about a call site ended up citing a
  #        declaration. It must report TOKEN-GONE instead of borrowing a neighbour.
  {
    printf '# fixture\n\n'
    printf 'A row: `twofn.py:11` — line 11: `deleted_token_xyz()`, against `def alpha():` at 10.\n'
  } > "$work/anchor/bind.md"
  self_case_because "TOKEN-GONE" \
    "S0R3: a citation bound to a token that no longer exists reports TOKEN-GONE rather than borrowing another token on the row" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/bind.md"

  # (15b3) S0R3 — `--write` MUST NOT RELOCATE A CITATION ACROSS A SCOPE BOUNDARY.
  #        Judging stays lenient for a unique token (see `_narrow_by_scope`);
  #        repairing does not. `beta_marker` lives only OUTSIDE alpha, so a repair of
  #        alpha's row would move the citation into another function — the exact
  #        cross-function relocation that put a `phase_start` citation inside a
  #        helper. The write must REFUSE and say which scope it would have escaped.
  #        `unique_marker` occurs ONCE (in gamma), which is what makes this reach the
  #        repair guard: a twice-occurring token is stopped earlier as SCOPE-MISS and
  #        never becomes a DRIFT for `--write` to act on.
  {
    printf '# fixture\n\n'
    printf '| Symbol | twofn.py |\n|---|---|\n'
    printf '| `def alpha():` | 10 |\n'
    printf '| ↳ `unique_marker = 7` | 25 |\n'
  } > "$work/anchor/scope-write.md"
  self_case_because "outside \`alpha\`" \
    "S0R3: --write REFUSES a repair that would move a citation outside the scope its row is about" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/scope-write.md" --write

  # (15b4) S0R3 — `--write` MUST NOT REPAIR AN ABSENCE-CLAIM ROW. Such a row cites a
  #        removal comment, which no code token can locate, so its remaining tokens
  #        come from explanatory prose. The worked failure: a cell whose own text
  #        said "line 647 is an unrelated `_write_json` call" had its citation
  #        rewritten TO 647, self-consistently planting the number it documents as
  #        wrong.
  {
    printf '# fixture\n\n'
    printf 'A row: `twofn.py:99` — `beta_marker` does not exist in the module any more.\n'
  } > "$work/anchor/absence.md"
  self_case_because "ABSENCE claim" \
    "S0R3: --write REFUSES to repair a citation on a row that asserts a token's absence" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/absence.md" --write

  # (15b5) S0R3 second pass — THE WRITE-TIME SCOPE GUARD MUST COVER PROSE ROWS TOO.
  #        `row_scope` is populated only for `|`-rows, so the first cut of the guard
  #        was inert on every prose row while `judge` still scoped them — found
  #        independently by two checkers. This fixture is a PROSE row (no pipes)
  #        naming `alpha` and citing gamma's unique token, so only the `or line`
  #        fallback can stop the repair crossing the boundary.
  {
    printf '# fixture\n\n'
    printf 'In `def alpha():` the helper is called — `twofn.py:25` `unique_marker = 7`.\n'
  } > "$work/anchor/scope-prose.md"
  self_case_because "outside \`alpha\`" \
    "S0R3: --write REFUSES a cross-scope repair on a PROSE row, not only a table row" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/scope-prose.md" --write

  # (15b6) S0R3 second pass — A BINDING TO AN UNUSABLE TOKEN REPORTS, IT DOES NOT
  #        FALL BACK. Fix (a) made bare keywords unusable, which WIDENED the
  #        fall-through door fix (b) was closing: `line 52: \`return\`` would have
  #        resolved against the row's other token. Must report instead.
  {
    printf '# fixture\n\n'
    printf 'A row: `twofn.py:52` — line 52: `return`, inside `def beta():` at 50.\n'
  } > "$work/anchor/bind-unusable.md"
  self_case_because "NO-TOKEN" \
    "S0R3: a citation bound to an UNUSABLE token reports rather than borrowing another token on the row" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/bind-unusable.md"

  # (15b7) S0R3 second pass — "at <N> by \`tok\`" BINDS THE FOLLOWING TOKEN. The
  #        backwards-scanning form binds the token BEFORE "at <N>", which on the
  #        live shape "`tokA` … followed at <N> by `tokB`" is tokA — the previous
  #        number's token. Here alpha's line 11 is cited in that shape; binding
  #        backwards would take `def alpha():` (line 10) and score ok by tolerance,
  #        so the fixture cites 11 with a deliberately WRONG token following, and
  #        the case passes only if the FOLLOWING token is the one used.
  {
    printf '# fixture\n\n'
    printf 'A row: `twofn.py:11` — `def alpha():` at 10, followed at 11 by `beta_marker = 1`.\n'
  } > "$work/anchor/bind-by.md"
  self_case_because "twofn.py:11" \
    "S0R3: an 'at <N> by \`tok\`' binding uses the FOLLOWING token, not the one before the phrase" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/bind-by.md"

  # (15b8) S0R3 pass 3 — THE "at <N> by" RULE MUST REQUIRE `by`, OR IT MASKS.
  #        Without `\bby\b` the rule bound ANY backticked token within 20 chars
  #        after "at <N>", hijacking the backward form and scoring `ok` on a
  #        citation whose own token has ZERO occurrences — a real TOKEN-GONE
  #        silently dropped from the report. Masking a drift is strictly worse
  #        than the false positive it replaces.
  #        The fixture reproduces the live shape: a gone token bound to <N> by the
  #        backward form, with a PRESENT token sitting just after "at <N>" ready to
  #        be hijacked. The gone token must still be reported.
  {
    printf '# fixture\n\n'
    printf 'A row: `twofn.py:11` — `deleted_token_xyz()` sits at 11, with `shared = compute(session_id)` nearby.\n'
  } > "$work/anchor/bind-mask.md"
  self_case_because "TOKEN-GONE" \
    "S0R3: 'at <N>' does NOT bind a trailing token without \`by\` (a real TOKEN-GONE must not be masked)" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/bind-mask.md"

  # (15c) SCOPE-MISS, both directions — it had NO coverage either way. A row whose
  #       named scope contains none of the token's occurrences is REPORTED when the
  #       citation does not resolve unnarrowed either...
  {
    printf '# fixture\n\n'
    printf '| Symbol | twofn.py |\n|---|---|\n'
    printf '| `def alpha():` | 10 |\n'
    printf '| ↳ `beta_marker = 1` | 25 |\n'
  } > "$work/anchor/scope-miss.md"
  self_case_because "SCOPE-MISS" \
    "anchors REPORTS a citation whose named scope contains no occurrence of its token" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/scope-miss.md"
  # ...and ACCEPTED when it does, which is the CONTRAST citation the fallback exists
  # for — a row about one function citing another to say what it does NOT use. Both
  # directions, because a fallback tested only where it fires is a fallback that
  # might always fire.
  {
    printf '# fixture\n\n'
    printf '| Symbol | twofn.py |\n|---|---|\n'
    printf '| `def alpha():` | 10 |\n'
    printf '| ↳ `beta_marker = 1` | 53 |\n'
  } > "$work/anchor/scope-contrast.md"
  self_case pass \
    "...and ACCEPTS a contrast citation that resolves outside the row's own scope" -- \
    env SNAP_DIR="$work/snap-pf" \
        PLAN_ENV_HOOKS_DIR_OVERRIDE="$work/anchor/scope/hooks" \
        PLAN_ENV_ANCHOR_FILES_OVERRIDE="twofn.py" \
        bash "$me" anchors --plan "$work/anchor/scope-contrast.md"

  # (16) THE SWEPT-RECORDS GUARD. It had NO coverage on first delivery — which is
  #      defect #2 of this very reopen ("a clause with no executable form")
  #      committed again while fixing it. Both directions.
  local swept="$work/swept"
  mkdir -p "$swept/_reaped"
  printf '{}' > "$swept/alpha__Root.json"
  printf '{}' > "$swept/beta__Root.json"
  printf '{}' > "$swept/_reaped/gamma__Root.json.bak-1"
  cp -a "$work/snap-pf/ENV" "$swept/ENV" 2>/dev/null || printf 'X=1\n' > "$swept/ENV"
  cp -a "$work/snap-pf/DIGEST_BASELINE" "$swept/DIGEST_BASELINE" 2>/dev/null \
    || printf 'x  y\n' > "$swept/DIGEST_BASELINE"
  # The manifest must count `_reaped/` too — the incident lost 97% of that dir.
  {
    printf 'alpha__Root.json\nbeta__Root.json\n'
    printf '_reaped/gamma__Root.json.bak-1\n'
  } > "$swept/SNAPSHOT_MANIFEST"
  # Intact: preflight must NOT refuse for loss (it may still refuse elsewhere, so
  # assert on the ABSENCE of the loss message rather than on exit code alone).
  # TM_STALE_T_SECONDS=0 is LOAD-BEARING HERE IN A WAY THE SIBLING CASES ARE NOT.
  # This case asserts on the ABSENCE of a message, so a preflight that dies earlier
  # — at probe_quiescence, before the swept-records guard is ever reached — also
  # prints no "has LOST" and the case reports PASS while having verified NOTHING.
  # Measured 2026-08-22: it did exactly that, reporting green on a machine where the
  # guard it exists to check was structurally unreachable. An absence assertion is
  # only as good as the guarantee that the code under test actually ran.
  # VACUOUS UNTIL 2026-08-23 — and this is the THIRD distinct mechanism by which
  # this one assertion has managed to verify nothing, so the history is kept.
  # It piped `preflight` into `grep -q "has LOST"` under `set -o pipefail`. But
  # "has LOST" is printed only by `die`, which then exits 1 — so the PIPELINE
  # status was 1 whenever the string was found, the `if` took the FALSE branch,
  # and the PASS arm ran PRECISELY WHEN THE GUARD WRONGLY FIRED. It could not
  # fail. The file already documents this exact trap twice (`self_case_because`,
  # and this case's own note about the earlier vacuous pass); the `TM_STALE_T`
  # repair made the code under test REACHABLE without making the assertion
  # CAPABLE OF FAILING, which are different properties.
  # Capture first, then match in pure bash — no pipeline, no exit-status coupling.
  local swept_out swept_rc
  swept_out=$(env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
         TM_STALE_T_SECONDS=0 \
         SNAP_DIR="$swept" bash "$me" preflight 2>&1) && swept_rc=0 || swept_rc=$?
  # ...and ASSERT THE EXIT STATUS, not just the absence of a string. Capturing
  # `swept_rc` without testing it left a FOURTH way for this one assertion to
  # verify nothing: a refusal in `discover_py`, `assert_tools` or the STATE_DIR
  # check exits non-zero and prints no "has LOST", so the absence arm reported
  # PASS having never reached the guard under test. `TM_STALE_T_SECONDS=0` closed
  # exactly one of those earlier exits; this closes the rest as a class.
  # An absence assertion is only as good as the guarantee that the code under test
  # actually ran — so require BOTH: preflight succeeded, AND it did not complain.
  if [ "$swept_rc" -ne 0 ]; then
    printf '  FAIL  preflight did not complete on an INTACT snapshot (rc=%s)\n' "$swept_rc"
    printf '        the swept-records guard was never reached — this case asserts nothing unless it runs\n'
    printf '        %s\n' "$(printf '%s' "$swept_out" | tail -2)"
    SELF_FAIL=$((SELF_FAIL + 1))
  else
    case "$swept_out" in
      *"has LOST"*)
        printf '  FAIL  the swept-records guard fired on an INTACT snapshot\n'
        printf '        (rc=%s) %s\n' "$swept_rc" "$(printf '%s' "$swept_out" | tail -2)"
        SELF_FAIL=$((SELF_FAIL + 1)) ;;
      *)
        printf '  PASS  (accepted)  the swept-records guard passes an intact snapshot (preflight rc=0)\n'
        SELF_PASS=$((SELF_PASS + 1)) ;;
    esac
  fi
  # Now sweep it, exactly as /var/folders would: remove all but the newest.
  mv "$swept/beta__Root.json" "$swept/beta__Root.json.swept-aside"
  mv "$swept/_reaped/gamma__Root.json.bak-1" "$swept/gamma.swept-aside"
  self_case_because "has LOST" \
    "preflight REFUSES a snapshot whose records were swept out from under it" -- \
    env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
        TM_STALE_T_SECONDS=0 \
        SNAP_DIR="$swept" bash "$me" preflight
  # ...and the TOTAL-wipeout case, which used to abort the script with NO output
  # because an unexpanded glob failed inside a `pipefail` pipeline.
  mv "$swept/alpha__Root.json" "$swept/alpha.swept-aside"
  self_case_because "has LOST" \
    "...including total wipeout, which previously died silently" -- \
    env CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR" STATE_DIR="$STATE_DIR" \
        TM_STALE_T_SECONDS=0 \
        SNAP_DIR="$swept" bash "$me" preflight

  # (17) THE BUCKETS VERB — also shipped with no coverage. Record, then assert,
  #      then drift.
  local bkt="$work/buckets"
  mkdir -p "$bkt"
  # The fixture needs its OWN ENV pointing SNAP_DIR at itself. Copying another
  # snapshot's ENV silently redirects the verb: `require_env` sources it and
  # overwrites SNAP_DIR, so the run operates on the wrong directory and the case
  # passes or fails for a reason that has nothing to do with what it tests.
  cat > "$bkt/ENV" <<ENVFIX
PY="$PY"
SNAP_DIR="$bkt"
STATE_DIR="$STATE_DIR"
CLAUDE_CONFIG_DIR="$CLAUDE_CONFIG_DIR"
CLAUDE_CONFIG_DIR_PARENT="$bkt/home"
ENVFIX
  printf '{}' > "$bkt/solo__Root.json"
  printf 'solo__Root.json\n' > "$bkt/SNAPSHOT_MANIFEST"
  if env SNAP_DIR="$bkt" bash "$me" buckets >/dev/null 2>&1 \
     && [ -f "$bkt/BUCKET_BASELINE" ]; then
    printf '  PASS  (accepted)  buckets RECORDS a baseline on first run\n'
    SELF_PASS=$((SELF_PASS + 1))
  else
    printf '  FAIL  buckets did not record a baseline on first run\n'
    SELF_FAIL=$((SELF_FAIL + 1))
  fi
  self_case pass "...and ASSERTS unchanged buckets on the second run" -- \
    env SNAP_DIR="$bkt" bash "$me" buckets
  # A hand-mutated baseline must be caught — the drift arm, which is the whole
  # point of recording one.
  printf 'reconcilable 7\ninverted 7\nunresolvable 7\n' > "$bkt/BUCKET_BASELINE"
  self_case_because "BUCKETS DRIFTED" \
    "...and REFUSES when the buckets no longer match the recorded baseline" -- \
    env SNAP_DIR="$bkt" bash "$me" buckets

  printf '\n[plan_env] selftest: %s passed, %s failed\n' "$SELF_PASS" "$SELF_FAIL"
  printf '[plan_env] selftest scratch left at %s (never auto-deleted — safe-defaults.md)\n' "$work"
  [ "$SELF_FAIL" -eq 0 ] || exit 1
}

# --------------------------------------------------------------------------- #
# dispatch
# --------------------------------------------------------------------------- #

usage() {
  cat <<'EOF'
usage: plan_env.sh <verb> [flags]

  preflight       environment gate + SNAP_DIR + HOME shim + ENV + DIGEST_BASELINE + snapshot
  gate            [--prove-only | --resolve-only]  baseline suite against the CLONE
                  (isolation proven, not assumed; a new test whose creating slice
                  has COMPLETED is required, not silently skipped)
  digest-record   [--rebaseline-digest "<why>"]   re-record the rolling digest
  digest-assert   [--rebaseline-digest "<why>" | --resume-past-own-run "<why>"]
  cutover-check   LIVE vs DIGEST_BASELINE across the full write-target list
  buckets         record (first run) or ASSERT the snapshot's three find-mis-keyed
                  buckets — A0's baseline, so A6 can prove its bucket is additive
  anchors         [--plan PATH] [--write]         re-derive the plan's line citations
  selftest        exercise every refusal path AND both positive paths

Every verb except preflight and selftest needs SNAP_DIR exported (preflight prints it).
EOF
}

case "${1:-}" in
  preflight)      shift; cmd_preflight "$@" ;;
  gate)           shift; cmd_gate "$@" ;;
  digest-record)  shift; cmd_digest_record "$@" ;;
  digest-assert)  shift; cmd_digest_assert "$@" ;;
  cutover-check)  shift; cmd_cutover_check "$@" ;;
  buckets)        shift; cmd_buckets "$@" ;;
  anchors)        shift; cmd_anchors "$@" ;;
  selftest)       shift; cmd_selftest "$@" ;;
  -h|--help|"")   usage ;;
  *)              usage; die "unknown verb: $1" ;;
esac

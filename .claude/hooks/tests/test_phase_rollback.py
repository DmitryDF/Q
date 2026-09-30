#!/usr/bin/env python3
"""S3 (A4) — the rollback path of `phase_start` / `phase_stop`.

topic-identity-generator-closure plan, Slice S3. Covers the three genuinely-wrong
uses A4 corrects, plus the regression that keeps the correction narrow:

  (i)   `_topic_path(topic, proj)` -> `_topic_path(proj, topic)`. Both functions
        destructure `proj, topic, state = _resolve_topic(session_id)` while
        `_resolve_topic` returns `(topic_slug, project_slug, state)` — so `proj`
        holds the TOPIC slug and `topic` holds the PROJECT slug. The rollback
        snapshot therefore read a MIRRORED `<project>__<topic>.json` path that is
        not the record being mutated, so the snapshot read nothing and the
        rollback restored nothing: the mutated record was never rolled back.
        The `else` arm's `unlink(missing_ok=True)` is entered ONLY when the
        mirrored path is absent — the same `exists()` test decides both — so it
        was a no-op, not a deletion.
  (ii)  `state["topic_slug"] = topic` / `state["project_slug"] = proj` ->
        `= proj` / `= topic`. The defensive backfill wrote the two halves
        SWAPPED into any state dict that arrived without them.
  (iii) the rollback `else` arm's `unlink` -> an archival move
        (`~/.claude/rules/safe-defaults.md`: move aside, never destroy).
  (iv)  the returned identity string is UNCHANGED — the regression this narrow
        scoping exists to prevent. `_write_topic_state(proj, topic, state)` and
        the return-dict f-strings are correct BY DOUBLE SWAP and are deliberately
        not touched; a "tidy the names" edit would silently re-break them.

WHY (iii) IS TESTED BOTH DIRECTLY AND THROUGH PRODUCTION.
After (i) is correct the `else` arm is unreachable through `phase_start`/`phase_stop`
ONLY ABSENT CONCURRENT REMOVAL: the function raises when `_resolve_topic` found no
state, so the record existed at resolve time — but `state_path.exists()` is
evaluated later, under no lock, and this module and its siblings ship several
record removers (the reaper's `shutil.move` to `_reaped/`; `reconcile` and
`_ensure_canonical_record` renaming records aside; `_archive_topic_state` itself).
So the arm is TOCTOU-reachable, and A4's gate cell calling it "provably
unreachable" is an over-claim — see CARRIED FINDINGS below.

That matters because the over-claim is A4's stated reason NOT to drive the arm
end-to-end, and a direct helper test alone leaves the EDIT uncovered: with only
helper tests, reverting the production line to `state_path.unlink(missing_ok=True)`
keeps every test green, so nothing would pin A4(iii) in either function.

FOUR tests pin A4(iii) AT THE EDIT — both `..._archives_when_the_record_vanished`
and both `..._archive_failure_does_not_mask_the_sync_failure`. Verified by
reverting BOTH production lines (there is one per function) with the helpers left
in place: all four fail, on `assert seen`. Reverting only one fails two of them,
which is what keeps the pair per-function rather than shared. The direct helper tests are kept alongside because never-unlinks
and abandon-on-OSError are clearer to assert there.

RED BEFORE THE EDIT. Against the unfixed module: EXPECTED_TESTS tests,
**18 fail and 9 pass**, measured via `RED_GREEN_MEASURED_BY` below.

  READ THIS BEFORE TRUSTING THAT SPLIT: `test_the_red_green_accounting_here_is_current`
  pins only the TOTAL (`EXPECTED_TESTS` vs the `def test_` count). It does NOT read
  this docstring and cannot tell you the split above is current — bump the constant
  for a new test and the pin stays green while the split silently rots. Re-measure.
  (The pair is stated once, above — deliberately NOT repeated here, because
  restating it is how it went stale three times. The green half is necessarily
  restated below as an enumeration, so adding a green-before test means editing
  two places: the pair and the list.)

The NINE green-before, none of them a defect:

  * SIX regression pins asserting behaviour A4 requires S3 to PRESERVE, so
    green-before is precisely their purpose —
    `test_backfill_never_overwrites_halves_that_are_already_present`,
    `test_phase_stop_backfill_never_overwrites_halves_that_are_already_present`,
    both `..._identity_string_is_unchanged`, and both
    `test_the_durable_write_still_lands_on_the_real_record[_in_phase_stop]`
    (green because `_write_topic_state(proj, topic, state)` is correct by double
    swap even unfixed, so a SUCCEEDING sync lands the record correctly either way).
  * TWO green-before for a weaker reason, stated plainly: both
    `..._recovery_failure_does_not_mask_the_sync_failure`. Against unfixed code
    the mirrored path does not exist, so the `else` arm runs, no `.rollback.tmp`
    is created, and the injected failure never fires. They pin the helper's own
    `except` clause in FIXED code — a real property, but NOT A4's edit.
  * ONE module-independent: `test_the_red_green_accounting_here_is_current`,
    which reads this file rather than the module under test.

CARRIED FINDINGS (not defects in this file — recorded so a reader is not misled):
  * A4's gate cell requires "a direct unit test … not an end-to-end injected
    failure", justified by the "provably unreachable" premise above. This file
    ships both, so it EXCEEDS A4's literal text. Amending A4 is out of this
    slice's write targets; see
    `~/.claude/state/execplan/receipts/0b346c93b5af/S3.CARRIED-FINDINGS.md`.
  * `plan_env.sh`'s `BASELINE_TESTS` cannot see `test_pre_plan_gates.py` or
    `test_taskmanagement.py`, the other two suites touching these functions.
    They were run separately for this slice (138 passed, 2 skipped).

Isolation: ppg.TOPIC_STATE_DIR / ppg.PROJECTS_ROOT / tm.LOCKS_DIR /
tm.RELEASES_LOG are monkeypatched into tmp_path (the `env` fixture cloned from
test_identity_mint_topic_half.py). No subprocess is spawned here — every test
drives the in-process functions — so there is no cross-process isolation gap.

RUN IT THROUGH THE GATE VERB, NOT BARE PYTEST:

    bash "$CLAUDE_CONFIG_DIR/hooks/tests/plan_env.sh" gate

`HOOKS` below resolves from `Path.home()`, and the gate is what redirects HOME to
the clone's shim. A bare `python3 -m pytest <this file>` imports the LIVE module,
so it grades the wrong binary — which is exactly how the non-vacuity control is
run deliberately.
"""
import importlib.util
import json
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"

# The red/green accounting in the module docstring is a MEASUREMENT, and it went
# stale twice by being prose. `EXPECTED_TESTS` pins the total in code so a test
# added without re-measuring fails loudly instead of silently invalidating the
# paragraph. Re-measure with the command below, then update BOTH.
EXPECTED_TESTS = 27
RED_GREEN_MEASURED_BY = (
    "/usr/bin/python3 -m pytest -q "
    "~/.claude-staging-topic-identity-closure/hooks/tests/test_phase_rollback.py"
    "   # bare pytest imports the LIVE (unfixed) module by design."
    "   /usr/bin/python3 is the interpreter A0 DISCOVERED (it is the PY= value in"
    "   $SNAP_DIR/ENV); it is named literally here only so this line is runnable"
    "   without sourcing that file. If A0's discovery ever selects another"
    "   interpreter, re-measure with that one instead."
    "   THE CONTROL EXPIRES AT PROMOTION: it works only while live is UNFIXED."
    "   Once S3 is promoted this command returns all-green, which looks like the"
    "   split below is wrong and is not — re-measure against a checkout of the"
    "   pre-S3 revision instead."
)


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import("pre_plan_gates_s3_phase_rollback", "pre_plan_gates.py")
# `ppg` reaches taskmanagement through FUNCTION-LOCAL `from taskmanagement import …`
# statements, not a module-level import, so both bind the same object via
# sys.modules and the monkeypatches below take effect. Note `conftest.py` puts the
# CLONE's hooks dir first on sys.path, so under the bare-pytest control run in
# `RED_GREEN_MEASURED_BY` the module under test is LIVE while this `tm` is the
# clone's copy — harmless (they agree on the exception classes and sync symbols),
# but it is an assumption behind the red/green measurement, so it is stated.
import taskmanagement as tm

TOPIC = "widget-topic"
PROJECT = "Root"
SID = "sess-s3-rollback"

# The record the functions actually mutate, and the mirrored name that must
# never appear. Spelled as literals rather than via _topic_path() so a defect
# in _topic_path itself cannot make the assertions agree with the bug.
REAL_RECORD = f"{TOPIC}__{PROJECT}.json"
MIRRORED_RECORD = f"{PROJECT}__{TOPIC}.json"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Cloned from test_identity_mint_topic_half.py's `env` fixture.

    The locks dir and releases log are pinned separately from TOPIC_STATE_DIR or
    a test contends with live operator locks.
    """
    root = tmp_path / "Projects"
    root.mkdir()
    (root / "TODO.md").write_text("# root\n")
    (root / "Thoughts").mkdir()
    state = tmp_path / "state" / "pre_plan_gates"
    state.mkdir(parents=True)
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(ppg, "PROJECTS_ROOT", root)
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", state)
    monkeypatch.setattr(tm, "LOCKS_DIR", locks)
    monkeypatch.setattr(tm, "RELEASES_LOG", locks / "_releases.jsonl")

    class NS:
        pass

    ns = NS()
    ns.root = root
    ns.state = state
    ns.locks = locks
    return ns


def _seed(env, *, body=None, phase=None):
    """Write `_active.json` + the topic-state record `_resolve_topic` will find.

    `intake_source: plain` with `phase: None` is what lets `phase_start` skip the
    PHASE_AUTH round-trip and the todo.py subprocess, so these tests exercise the
    rollback wrap and nothing else. No `thought_file_path`, so
    `write_phase_marker` returns `skipped_no_spine` without touching a spine.
    """
    (env.state / "_active.json").write_text(json.dumps({
        SID: {"topic_slug": TOPIC, "active_project": PROJECT},
    }))
    record = {
        "session_id": SID,
        "intake_source": "plain",
        "phase": phase,
        "project_root": str(env.root),
    }
    if body is not None:
        record.update(body)
    path = env.state / REAL_RECORD
    path.write_text(json.dumps(record, indent=2))
    return path


def _fail_sync(monkeypatch, which):
    """Make the named taskmanagement sync raise the error the rollback catches."""
    def boom(*a, **kw):
        raise tm.WriteVerificationError("injected sync failure")
    monkeypatch.setattr(tm, which, boom)


def _capture_sync(monkeypatch, which):
    """Let the sync SUCCEED but record the state dict it was handed."""
    seen = {}

    def ok(state, *a, **kw):
        seen["state"] = dict(state)
        return {"status": "ok"}

    monkeypatch.setattr(tm, which, ok)
    return seen


# ---------------------------------------------------------------------------
# (i) rollback targets the REAL record — phase_start
# ---------------------------------------------------------------------------

def test_phase_start_rollback_restores_the_real_record(env, monkeypatch):
    path = _seed(env)
    before = path.read_bytes()
    _fail_sync(monkeypatch, "sync_phase_transition")

    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_start(SID, "thought", auth_token=None)

    assert path.read_bytes() == before, (
        "the rollback did not restore the record that was actually mutated"
    )
    # NOT the discriminator between fixed and unfixed code — on this fixture no
    # mirrored file appears either way, because the unfixed path only ever
    # reaches it via `unlink(missing_ok=True)`. The bytes assertion above is
    # what fails against unfixed code. This one guards a DIFFERENT wrong fix:
    # one that renames the locals (which A4 forbids) and so starts writing the
    # mirrored path. Kept for that, and labelled so it does not read as more.
    assert not (env.state / MIRRORED_RECORD).exists(), (
        f"a mirrored {MIRRORED_RECORD} appeared — a wrong fix is writing the "
        "transposed path (locals renamed?)"
    )
    assert not list(env.state.glob("*.rollback.tmp")), (
        "a rollback temp file was left behind"
    )


def test_phase_stop_rollback_restores_the_real_record(env, monkeypatch):
    path = _seed(env, phase="thought")
    before = path.read_bytes()
    _fail_sync(monkeypatch, "sync_phase_stop")

    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_stop(SID)

    assert path.read_bytes() == before, (
        "the rollback did not restore the record that was actually mutated"
    )
    # NOT the discriminator between fixed and unfixed code — on this fixture no
    # mirrored file appears either way, because the unfixed path only ever
    # reaches it via `unlink(missing_ok=True)`. The bytes assertion above is
    # what fails against unfixed code. This one guards a DIFFERENT wrong fix:
    # one that renames the locals (which A4 forbids) and so starts writing the
    # mirrored path. Kept for that, and labelled so it does not read as more.
    assert not (env.state / MIRRORED_RECORD).exists(), (
        f"a mirrored {MIRRORED_RECORD} appeared — a wrong fix is writing the "
        "transposed path (locals renamed?)"
    )
    assert not list(env.state.glob("*.rollback.tmp")), (
        "a rollback temp file was left behind"
    )


# ---------------------------------------------------------------------------
# (iii) THROUGH PRODUCTION — the rollback's else arm calls the archival helper
#
# Two of the four tests that pin A4(iii) at the EDIT (the other two are the
# archive-arm masking pair below). Without ALL FOUR, reverting the production
# line to `state_path.unlink(missing_ok=True)` would leave the DIRECT helper
# tests green, because the helper would still exist and still behave.
# ---------------------------------------------------------------------------

def _remove_record_on_nth_topic_path(monkeypatch, n):
    """Simulate a concurrent remover landing between the resolve and the snapshot.

    On the FAILING path taken by these tests, both `phase_start` and `phase_stop`
    call `_topic_path` three times: (1) inside `_resolve_topic`, (2) for
    `state_path`, (3) inside `_write_topic_state`. Removing the record as call
    (2) returns makes the following `state_path.exists()` false, so
    `prev_state_bytes` is None and the `else` arm runs — the TOCTOU window the
    module docstring describes, driven for real rather than argued about.

    Scope notes, because `n=2` is fixture-coupled and that is easy to lose:
      * a SUCCESSFUL call makes a fourth `_topic_path` call inside
        `write_phase_marker` -> `_resolve_topic` (both functions). These tests
        fail before reaching it.
      * for the `phase_start` callers, `n=2` depends on `_seed` setting
        `intake_source: "plain"` with `phase: None`, which skips
        `phase_complete_predicate` and the PHASE_AUTH branch; neither calls
        `_topic_path` today. `phase_stop` has no such branches, so its callers
        are unconditional at three.

    A WRONG ORDINAL FAILS LOUDLY IN EVERY CALLER — measured by rewriting the
    ordinal and re-running the gate (4 failed at n=1, 4 at n=3, 0 at n=2):
      * `n=1` removes the record before `_read_json` reads it, so `_resolve_topic`
        returns no state and all four die on `ValueError: No active topic`;
      * `n=3` removes it after the snapshot, so the RESTORE arm runs and all four
        die on `assert seen` — two via `_assert_archived_through_production`'s
        "not wired into production" message, two via the archive-masking tests'
        "the archive arm was not reached".

    The counter this helper used to return is gone: it was asserted as
    `calls["n"] >= 2`, which held whenever the path got past the snapshot and so
    discriminated nothing the test bodies do not already discriminate.
    """
    real = ppg._topic_path
    calls = {"n": 0}

    def patched(*, topic_slug, project_slug):
        p = real(topic_slug=topic_slug, project_slug=project_slug)
        calls["n"] += 1
        if calls["n"] == n and p.exists():
            p.unlink()
        return p

    monkeypatch.setattr(ppg, "_topic_path", patched)


def _spy_archive(monkeypatch):
    seen = []
    real = ppg._archive_topic_state

    def spy(path):
        seen.append(path)
        return real(path)

    monkeypatch.setattr(ppg, "_archive_topic_state", spy)
    return seen


def _assert_archived_through_production(env, seen, who):
    """The six assertions both twins share — kept identical on purpose.

    A4's gate says "same four for `phase_stop`" (that "four" counts gate items
    i-iv, not assertions), and an asymmetric pair is how a one-sided regression
    survives in a file built around that symmetry.
    """
    assert seen, (
        f"{who}'s rollback else arm did not call _archive_topic_state — "
        "A4(iii) is not wired into production"
    )
    # Full path, not just the basename: the helper derives `_reaped/` from
    # TOPIC_STATE_DIR rather than from the argument's parent, so a
    # right-name/wrong-parent argument would satisfy a basename check.
    assert seen[0] == env.state / REAL_RECORD
    assert list((env.state / "_reaped").glob(f"{REAL_RECORD}.bak-*")), (
        f"{who}: nothing was archived; the arm still deletes or no-ops"
    )
    assert not (env.state / REAL_RECORD).exists()
    assert not (env.state / MIRRORED_RECORD).exists()
    assert not list(env.state.glob("*.rollback.tmp"))


def test_phase_start_rollback_archives_when_the_record_vanished(env, monkeypatch):
    _seed(env)
    seen = _spy_archive(monkeypatch)
    _remove_record_on_nth_topic_path(monkeypatch, 2)
    _fail_sync(monkeypatch, "sync_phase_transition")

    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_start(SID, "thought", auth_token=None)

    _assert_archived_through_production(env, seen, "phase_start")


def test_phase_stop_rollback_archives_when_the_record_vanished(env, monkeypatch):
    _seed(env, phase="thought")
    seen = _spy_archive(monkeypatch)
    _remove_record_on_nth_topic_path(monkeypatch, 2)
    _fail_sync(monkeypatch, "sync_phase_stop")

    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_stop(SID)

    _assert_archived_through_production(env, seen, "phase_stop")


# ---------------------------------------------------------------------------
# A failed recovery must not mask the sync failure — BOTH arms, through production
#
# A4 states this as a guard rail on both arms, so both are driven end-to-end:
# the RESTORE arm below (reached whenever the record survives to the snapshot),
# and the ARCHIVE arm in `..._archive_failure_does_not_mask...`, reached by
# composing the TOCTOU simulation with a failure scoped to the archival rename.
#
# Honest labelling: the two RESTORE-arm tests are GREEN against unfixed code
# (see the module docstring) — they pin the helper's `except`, not A4's edit.
# ---------------------------------------------------------------------------

def test_phase_start_recovery_failure_does_not_mask_the_sync_failure(env, monkeypatch):
    path = _seed(env)
    _fail_sync(monkeypatch, "sync_phase_transition")

    # Scoped to the ROLLBACK temp file only: `_write_topic_state` does its own
    # atomic rename before the sync runs, so a blanket patch would fail the
    # durable write instead of the recovery and test nothing.
    real_rename = Path.rename

    def boom(self, *a, **kw):
        if self.name.endswith(".rollback.tmp"):
            raise OSError("read-only file system")
        return real_rename(self, *a, **kw)

    monkeypatch.setattr(Path, "rename", boom)

    # The sync failure must reach the caller — NOT the OSError from the recovery.
    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_start(SID, "thought", auth_token=None)

    assert path.exists(), "a failed recovery destroyed the record"


def test_phase_stop_recovery_failure_does_not_mask_the_sync_failure(env, monkeypatch):
    path = _seed(env, phase="thought")
    _fail_sync(monkeypatch, "sync_phase_stop")

    real_rename = Path.rename

    def boom(self, *a, **kw):
        if self.name.endswith(".rollback.tmp"):
            raise OSError("read-only file system")
        return real_rename(self, *a, **kw)

    monkeypatch.setattr(Path, "rename", boom)

    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_stop(SID)

    assert path.exists(), "a failed recovery destroyed the record"


def test_phase_start_archive_failure_does_not_mask_the_sync_failure(env, monkeypatch):
    """The ARCHIVE arm's half of A4's "applies to both arms" guard rail.

    Composes the TOCTOU simulation (which makes the arm reachable) with a failure
    scoped to the archival rename. Unlike the restore-arm pair above, this one is
    NOT green-before, and for a blunter reason than the production line: the
    unfixed module has no `_archive_topic_state` AT ALL, so `_spy_archive` raises
    `AttributeError` on its second statement and nothing after it runs.
    """
    _seed(env)
    seen = _spy_archive(monkeypatch)
    _remove_record_on_nth_topic_path(monkeypatch, 2)
    _fail_sync(monkeypatch, "sync_phase_transition")

    real_rename = Path.rename

    def boom(self, *a, **kw):
        dest = a[0] if a else kw.get("target")
        if dest is not None and ".bak-" in str(dest):
            raise OSError("read-only file system")
        return real_rename(self, *a, **kw)

    monkeypatch.setattr(Path, "rename", boom)

    # The SYNC failure must surface — not the OSError raised while archiving.
    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_start(SID, "thought", auth_token=None)

    assert seen, "the archive arm was not reached; this test proves nothing"
    assert (env.state / REAL_RECORD).exists(), (
        "an abandoned archival left the record moved or destroyed"
    )


def test_phase_stop_archive_failure_does_not_mask_the_sync_failure(env, monkeypatch):
    _seed(env, phase="thought")
    seen = _spy_archive(monkeypatch)
    _remove_record_on_nth_topic_path(monkeypatch, 2)
    _fail_sync(monkeypatch, "sync_phase_stop")

    real_rename = Path.rename

    def boom(self, *a, **kw):
        dest = a[0] if a else kw.get("target")
        if dest is not None and ".bak-" in str(dest):
            raise OSError("read-only file system")
        return real_rename(self, *a, **kw)

    monkeypatch.setattr(Path, "rename", boom)

    with pytest.raises(tm.WriteVerificationError):
        ppg.phase_stop(SID)

    assert seen, "the archive arm was not reached; this test proves nothing"
    assert (env.state / REAL_RECORD).exists()


def test_both_helpers_log_an_abandoned_recovery(env, capsys):
    """A4's "log the recovery failure" clause — the last one with no test.

    Every other clause of that sentence is pinned — abandon, leave the temp
    unpromoted, do not proceed to the rename, let the original propagate. This
    one was not: deleting both `print(...)` calls left all tests green, so the
    only signal an operator gets that a recovery was abandoned was un-asserted.
    (No other test in this file uses `capsys`, which is why it stayed uncovered.)
    """
    real_rename = Path.rename

    def boom(self, *a, **kw):
        raise OSError("read-only file system")

    # Restore arm.
    restore_target = env.state / REAL_RECORD
    restore_target.write_text('{"real": true}')
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "rename", boom)
        assert ppg._restore_topic_state(restore_target, b'{"previous": true}') is False
    err = capsys.readouterr().err
    assert "rollback recovery abandoned" in err, (
        f"the restore arm logged nothing an operator could see; stderr was {err!r}"
    )
    assert str(restore_target) in err, "the log does not name the record"

    # Archive arm.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "rename", boom)
        assert ppg._archive_topic_state(restore_target) is None
    err = capsys.readouterr().err
    assert "archival recovery abandoned" in err, (
        f"the archive arm logged nothing an operator could see; stderr was {err!r}"
    )
    assert str(restore_target) in err, "the log does not name the record"
    assert Path.rename is real_rename, "the monkeypatch context leaked"


def test_abandoned_restore_leaves_the_temp_file_unpromoted(env):
    """A4 states this clause explicitly; nothing asserted it until now.

    The happy-path tests assert the temp is ABSENT, which is the opposite
    direction and would also pass if the temp were never written at all.
    """
    path = env.state / REAL_RECORD
    path.write_text('{"real": true}')
    real_rename = Path.rename

    with pytest.MonkeyPatch.context() as mp:
        def boom(self, *a, **kw):
            if self.name.endswith(".rollback.tmp"):
                raise OSError("read-only file system")
            return real_rename(self, *a, **kw)

        mp.setattr(Path, "rename", boom)
        assert ppg._restore_topic_state(path, b'{"previous": true}') is False

    leftovers = list(env.state.glob("*.rollback.tmp"))
    assert leftovers, "the temp file was not left unpromoted as A4 requires"
    assert leftovers[0].read_bytes() == b'{"previous": true}'
    assert path.read_text() == '{"real": true}', "the real record was disturbed"


# ---------------------------------------------------------------------------
# (ii) the defensive backfill writes the halves CORRECT, not swapped
# ---------------------------------------------------------------------------

def test_phase_start_backfills_the_halves_unswapped(env, monkeypatch):
    _seed(env)
    seen = _capture_sync(monkeypatch, "sync_phase_transition")

    ppg.phase_start(SID, "thought", auth_token=None)

    state = seen["state"]
    assert state["topic_slug"] == TOPIC, (
        f"topic_slug backfilled as {state['topic_slug']!r}; the halves are swapped"
    )
    assert state["project_slug"] == PROJECT, (
        f"project_slug backfilled as {state['project_slug']!r}; the halves are swapped"
    )


def test_phase_stop_backfills_the_halves_unswapped(env, monkeypatch):
    _seed(env, phase="thought")
    seen = _capture_sync(monkeypatch, "sync_phase_stop")

    ppg.phase_stop(SID)

    state = seen["state"]
    assert state["topic_slug"] == TOPIC, (
        f"topic_slug backfilled as {state['topic_slug']!r}; the halves are swapped"
    )
    assert state["project_slug"] == PROJECT, (
        f"project_slug backfilled as {state['project_slug']!r}; the halves are swapped"
    )


def test_backfill_never_overwrites_halves_that_are_already_present(env, monkeypatch):
    """The backfill is `if "topic_slug" not in state` — defensive, not corrective.

    Pinned because A4's swap is a one-character-per-line edit on exactly these
    two assignments, and the easiest wrong fix is to drop the guards.
    """
    _seed(env, body={"topic_slug": "already-set", "project_slug": "AlreadySet"})
    seen = _capture_sync(monkeypatch, "sync_phase_transition")

    ppg.phase_start(SID, "thought", auth_token=None)

    assert seen["state"]["topic_slug"] == "already-set"
    assert seen["state"]["project_slug"] == "AlreadySet"


def test_phase_stop_backfill_never_overwrites_halves_that_are_already_present(
        env, monkeypatch):
    """The `phase_stop` twin — the guard is duplicated identically in both."""
    _seed(env, phase="thought",
          body={"topic_slug": "already-set", "project_slug": "AlreadySet"})
    seen = _capture_sync(monkeypatch, "sync_phase_stop")

    ppg.phase_stop(SID)

    assert seen["state"]["topic_slug"] == "already-set"
    assert seen["state"]["project_slug"] == "AlreadySet"


# ---------------------------------------------------------------------------
# (iii) the archival helper — moves aside, never deletes
# ---------------------------------------------------------------------------

def test_archive_topic_state_moves_aside_under_topic_state_dir(env):
    path = env.state / REAL_RECORD
    path.write_text('{"kept": true}')

    dest = ppg._archive_topic_state(path)

    assert dest is not None, "the helper reported nothing archived"
    reaped = env.state / "_reaped"
    assert reaped.is_dir(), "_reaped/ was not created under TOPIC_STATE_DIR"
    assert dest.parent == reaped, f"archived outside _reaped/: {dest}"
    assert dest.name.startswith(f"{REAL_RECORD}.bak-"), (
        f"archive name {dest.name!r} does not carry the .bak-<ts> shape"
    )
    assert dest.read_text() == '{"kept": true}', "the archived content was not preserved"
    assert not path.exists(), "the source record was left in place"


def test_archive_topic_state_never_unlinks(env, monkeypatch):
    """The whole point of (iii): recovery must never be able to destroy.

    `Path.unlink` is made explosive, so a `Path.unlink()` — including a
    `missing_ok=True` one that would otherwise pass silently — fails the test.
    Scope, stated rather than implied: this intercepts `Path.unlink` ONLY. An
    `os.unlink`/`os.remove`/`shutil.rmtree`, or a `rename` onto the source,
    would pass. It is the shape the replaced code used, not a proof that no
    deletion of any kind can occur.
    """
    path = env.state / REAL_RECORD
    path.write_text('{"kept": true}')

    def exploding_unlink(self, *a, **kw):
        raise AssertionError(f"the archival helper called unlink on {self}")

    monkeypatch.setattr(Path, "unlink", exploding_unlink)

    dest = ppg._archive_topic_state(path)

    assert dest is not None and dest.exists()


def test_archive_topic_state_is_a_no_op_when_there_is_nothing_to_archive(env):
    assert ppg._archive_topic_state(env.state / "absent__Nowhere.json") is None
    assert not (env.state / "_reaped").exists(), (
        "_reaped/ was created for a record that does not exist"
    )


def test_archive_topic_state_abandons_rather_than_masking_an_os_error(env, monkeypatch):
    """A recovery that cannot complete must not become the reported failure.

    The handler runs inside an `except` that ends in a bare `raise`, so an
    OSError escaping here would replace the original sync failure with a
    confusing one.
    """
    path = env.state / REAL_RECORD
    path.write_text('{"kept": true}')

    def boom(self, *a, **kw):
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "rename", boom)

    assert ppg._archive_topic_state(path) is None
    assert path.exists(), "the record was disturbed by a failed archival"
    assert path.read_text() == '{"kept": true}', (
        "the record survived but its CONTENT changed — a truncating or "
        "copy-then-write variant would pass an existence check alone"
    )


def test_archive_topic_state_does_not_rename_when_mkdir_fails(env, monkeypatch):
    """A4: "on ANY OSError do not proceed to the rename" — the archive arm's
    PRE-rename direction.

    Its sibling clause is pinned for the restore arm by
    `test_restore_does_not_promote_a_temp_file_it_failed_to_write`. Here the
    failure is injected at `mkdir`, before the rename, so a per-call handler
    (which A4 forbids) would swallow it and FALL THROUGH TO THE RENAME.

    `assert not renames` is the only assertion that discriminates against THAT
    shape — do not delete it. The rename would then fail anyway (`_reaped/` does
    not exist, so it raises `FileNotFoundError`); if a second per-call handler
    absorbs that too, the helper returns None with the record untouched, so the
    `is None` and content assertions both pass. They are not redundant — they
    catch neighbouring wrong helpers (one that swallows and returns `dest`; one
    that lets the `FileNotFoundError` escape).
    """
    path = env.state / REAL_RECORD
    path.write_text('{"real": true}')
    renames = []
    real_rename = Path.rename

    def boom_mkdir(self, *a, **kw):
        raise OSError("read-only file system")

    def spy_rename(self, *a, **kw):
        renames.append(self)
        return real_rename(self, *a, **kw)

    monkeypatch.setattr(Path, "mkdir", boom_mkdir)
    monkeypatch.setattr(Path, "rename", spy_rename)

    assert ppg._archive_topic_state(path) is None
    assert not renames, "the rename was attempted after mkdir failed"
    assert path.read_text() == '{"real": true}', "the record was disturbed"


def test_archive_topic_state_does_not_clobber_an_existing_archive(env, monkeypatch):
    """Two rollbacks of one record inside the same second must not overwrite.

    `.bak-<ts>` is second-granular, so the collision is reachable. Overwriting a
    backup is the destruction this action exists to remove.
    """
    path = env.state / REAL_RECORD
    path.write_text('{"generation": 1}')
    first = ppg._archive_topic_state(path)

    path.write_text('{"generation": 2}')
    monkeypatch.setattr(ppg, "_archive_timestamp", lambda: _stamp_of(first))
    second = ppg._archive_topic_state(path)

    assert second is not None and second != first
    assert first.read_text() == '{"generation": 1}', "the earlier archive was clobbered"
    assert second.read_text() == '{"generation": 2}'


def _stamp_of(archive_path):
    return archive_path.name.rsplit(".bak-", 1)[1]


# ---------------------------------------------------------------------------
# (iii-b) the restore arm is ONE transaction — a failed write never promotes
# ---------------------------------------------------------------------------

def test_restore_does_not_promote_a_temp_file_it_failed_to_write(env, monkeypatch):
    """The destructive shape A4 names explicitly.

    If `write_bytes` fails (ENOSPC) and the error is swallowed per-call, control
    falls through to `rename` and the REAL record is overwritten with a
    truncated temp file — strictly worse than the `unlink` being replaced.
    """
    path = env.state / REAL_RECORD
    path.write_text('{"real": true}')
    real = Path.write_bytes

    def fail_on_tmp(self, data):
        if self.name.endswith(".rollback.tmp"):
            raise OSError("no space left on device")
        return real(self, data)

    monkeypatch.setattr(Path, "write_bytes", fail_on_tmp)

    assert ppg._restore_topic_state(path, b'{"restored": true}') is False
    assert path.read_text() == '{"real": true}', (
        "a failed restore promoted a temp file over the real record"
    )


def test_restore_writes_the_previous_bytes_on_the_happy_path(env):
    path = env.state / REAL_RECORD
    path.write_text('{"mutated": true}')

    assert ppg._restore_topic_state(path, b'{"previous": true}') is True
    assert path.read_text() == '{"previous": true}'


# ---------------------------------------------------------------------------
# (iv) the returned identity string is UNCHANGED
# ---------------------------------------------------------------------------

def test_phase_start_identity_string_is_unchanged(env, monkeypatch):
    _seed(env)
    _capture_sync(monkeypatch, "sync_phase_transition")

    result = ppg.phase_start(SID, "thought", auth_token=None)

    assert result["topic"] == f"{TOPIC}__{PROJECT}", (
        "the correct-by-double-swap return f-string was disturbed"
    )


def test_phase_stop_identity_string_is_unchanged(env, monkeypatch):
    _seed(env, phase="thought")
    _capture_sync(monkeypatch, "sync_phase_stop")

    result = ppg.phase_stop(SID)

    assert result["topic"] == f"{TOPIC}__{PROJECT}", (
        "the correct-by-double-swap return f-string was disturbed"
    )


def test_the_red_green_accounting_here_is_current():
    """Pin the docstring's measured total in code, because prose kept drifting.

    Twice, a fix pass added tests and left the "N failed / M passed" sentence
    describing the file as it was BEFORE the same pass. This does not verify the
    split (that needs a run against the unfixed module — see
    `RED_GREEN_MEASURED_BY`); it makes ADDING a test without re-measuring fail
    loudly, which is the step that was actually being skipped.

    THREE THINGS IT DOES NOT CATCH, stated so it is not trusted past its reach:
      * a delete-one/add-one edit — the total holds while the split moves;
      * a `@pytest.mark.parametrize`, which would make one `def` collect as N,
        breaking the def-count == collected-count identity this relies on;
      * a docstring or comment line that itself begins `def test_`, which would
        inflate the count.
    """
    src = Path(__file__).read_text(encoding="utf-8")
    defined = len([l for l in src.splitlines() if l.startswith("def test_")])
    assert defined == EXPECTED_TESTS, (
        f"this file defines {defined} tests but EXPECTED_TESTS is "
        f"{EXPECTED_TESTS}. Re-measure the red/green split against the unfixed "
        f"module, update the module docstring AND EXPECTED_TESTS:\n"
        f"  {RED_GREEN_MEASURED_BY}"
    )


def test_the_durable_write_still_lands_on_the_real_record(env, monkeypatch):
    """`_write_topic_state(proj, topic, state)` is correct by double swap.

    A4 forbids re-ordering it. This is what catches a "tidy the names" edit that
    re-breaks it while every rollback test still passes.
    """
    _seed(env)
    _capture_sync(monkeypatch, "sync_phase_transition")

    ppg.phase_start(SID, "thought", auth_token=None)

    assert json.loads((env.state / REAL_RECORD).read_text())["phase"] == "thought"
    assert not (env.state / MIRRORED_RECORD).exists()


def test_the_durable_write_still_lands_on_the_real_record_in_phase_stop(env, monkeypatch):
    """The `phase_stop` twin — A4's prohibition applies to BOTH functions.

    Without this, a transposition of `phase_stop`'s `_write_topic_state(proj,
    topic, state)` is caught only indirectly, by a mirrored-file assertion on the
    FAILURE path. This pins it on the success path, symmetrically with the
    `phase_start` test above.
    """
    _seed(env, phase="thought")
    _capture_sync(monkeypatch, "sync_phase_stop")

    ppg.phase_stop(SID)

    record = json.loads((env.state / REAL_RECORD).read_text())
    # `.get(...)` rather than `[...]`: under a transposition the real record
    # keeps the seed body, which has no phase_history, and a bare subscript
    # would fail this test with a KeyError instead of a stated reason.
    history = record.get("phase_history") or []
    assert history and history[-1].get("event") == "stop", (
        "phase_stop's durable write did not land on the real record — its "
        "_write_topic_state(proj, topic, state) binding is transposed"
    )
    assert not (env.state / MIRRORED_RECORD).exists()

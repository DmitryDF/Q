#!/usr/bin/env python3
"""S1 (A1 + A2) — the work-name half of the (topic, project) identity.

topic-identity-generator-closure plan, Slice S1. Covers the derivation contract
this slice installs and the CLI seam that makes it reachable:

  A1 — `canonical_topic_for_spine` derives the work name from the work's OWN
       artifact and returns None rather than guessing; `create_topic` refuses
       when it has neither an explicit slug nor an artifact; an explicit slug
       always outranks the artifact; and the cwd can never reach the work-name
       position.
  A2 — `create-topic` accepts `--thought-file-path`, so a caller has a way to
       satisfy A1's refusal, and refuses with a message naming what is missing
       when given neither form.

WHY THE cwd TEST IS THE CENTREPIECE. Before this slice the derivation read
`topic_slug = _derive_topic_slug()` -> `Path.cwd().name`. Running from a
directory named `Projects` therefore minted `Projects__<project>.json`: a record
whose filename says the work is called "Projects" while its body says otherwise.
`test_cwd_named_projects_cannot_reach_the_work_name_position` is the regression
that pins that shut, and it is written to FAIL LOUDLY rather than vacuously —
it asserts both that no `Projects__*.json` appeared AND that the call refused
for the stated reason, because a test that only checks for an absent file would
also pass if `create_topic` had simply crashed for an unrelated reason.

Isolation: ppg.TOPIC_STATE_DIR / ppg.PROJECTS_ROOT / tm.LOCKS_DIR /
tm.RELEASES_LOG are all monkeypatched into tmp_path (the `env` fixture cloned
from test_identity_reconcile.py). The subprocess tests additionally pass HOME
AND CLAUDE_CONFIG_DIR into the child env and then ASSERT the child's resolved
lock dir is not the live one — `monkeypatch` does not cross a process boundary,
and an isolation you have not asserted is an isolation you do not have.

RUN IT THROUGH THE GATE VERB, NOT BARE PYTEST:

    bash "$CLAUDE_CONFIG_DIR/hooks/tests/plan_env.sh" gate

`HOOKS` below resolves from `Path.home()`, and the gate is what redirects HOME to
the clone's shim. A bare `python3 -m pytest <this file>` therefore imports and
subprocess-invokes the LIVE module, where `_derive_topic_slug` still exists — so
`test_the_ambient_fallback_symbol_is_gone` fails and every CLI test exercises the
wrong binary. State isolation still holds either way (the child's HOME is
redirected independently), so this is the wrong-binary hazard, not live
pollution. It is also exactly how the non-vacuity control is run deliberately.
"""
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"

# Every subprocess in this file is bounded. An unbounded subprocess.run stalls
# the slice indefinitely with no signal.
CHILD_TIMEOUT = 60


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import("pre_plan_gates_s1_topic_half", "pre_plan_gates.py")
import taskmanagement as tm  # ppg's `import taskmanagement` resolves to this


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Cloned from test_identity_reconcile.py's `env` fixture.

    `--state-dir` would redirect TOPIC_STATE_DIR only; the locks dir and the
    releases log must be pinned separately or a test contends with live operator
    locks and a killed run leaves a stale lock in the real locks directory.
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


def _spine(root, slug, stamp="20260101010101"):
    p = root / "Thoughts" / f"{slug}-{stamp}_THOUGHT.md"
    p.write_text(f"# {slug}\n")
    return p


# ---------------------------------------------------------------------------
# A1 — canonical_topic_for_spine: derive from the artifact, else None
# ---------------------------------------------------------------------------

def test_artifact_yields_its_slug():
    assert ppg.canonical_topic_for_spine(
        "/x/Thoughts/widget-refactor_THOUGHT.md") == "widget-refactor"


def test_timestamped_stem_is_stripped():
    """The `-<14-digit>` stamp is part of the filename, not of the work name."""
    assert ppg.canonical_topic_for_spine(
        "/x/Thoughts/widget-refactor-20260101010101_THOUGHT.md") == "widget-refactor"


@pytest.mark.parametrize("suffix", ["_THOUGHT", "_PLAN", "_DESIGN",
                                    "_RESEARCH", "_CLAIMS"])
def test_every_type_suffix_is_stripped(suffix):
    """The stem grammar lives in ONE place (`_slug_from_spine_path`); this
    asserts the new locus really reuses it rather than restating it."""
    assert ppg.canonical_topic_for_spine(f"/x/thing{suffix}.md") == "thing"


def test_refuses_rather_than_guessing():
    """The counterpart of `canonical_project_for_spine` returns "Root" and never
    fails. That is right for the project half and WRONG here: a wrong guess at
    the work name is the whole defect. So this one returns None."""
    for bad in (None, "", "   "):
        assert ppg.canonical_topic_for_spine(bad) is None


def test_a_derived_slug_may_not_contain_the_record_filename_separator():
    """`__` separates topic from project in `<topic>__<project>.json`, so a slug
    containing it is ambiguous by construction.

    Found adversarially in round 4. `foo__bar_THOUGHT.md` derived `foo__bar`,
    which combined with project `baz` produces `foo__bar__baz.json` — the same
    filename as topic `foo` in a project named `bar__baz`. The idempotency guard
    would then find the colliding record and REBIND the session onto an
    unrelated topic. A door this slice opened: before A2, no CLI caller could
    mint a slug from an artifact name.
    """
    assert ppg.canonical_topic_for_spine("/x/Thoughts/foo__bar_THOUGHT.md") is None
    assert ppg.canonical_topic_for_spine("/x/a__b.md") is None
    # ...while an ordinary single-underscore name is untouched.
    assert ppg.canonical_topic_for_spine("/x/foo_bar_THOUGHT.md") == "foo_bar"


def test_create_topic_refuses_a_separator_bearing_artifact(env):
    """The refusal reaches the caller rather than stopping at the pure function."""
    spine = env.root / "Thoughts" / "foo__bar_THOUGHT.md"
    spine.write_text("# collide\n")
    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-55555555", "Root", None, thought_file_path=str(spine))
    assert "__" in str(exc.value), "the message should name the offending shape"
    assert not list(env.state.glob("*__*.json")), "refusal must mint nothing"


@pytest.mark.parametrize("bad", [0, False, [], 123, 4.5, {"a": 1}])
def test_a_non_string_topic_slug_is_refused_not_coerced(bad, env):
    """Fix 12 had no test until round 4 said so — a runtime refusal with no
    regression coverage is a branch that can be deleted silently.

    Parametrized over BOTH truthy and falsy non-strings on purpose, because
    against the REAL pre-S1 baseline every one of them previously "worked":
    that code tested `is None` and otherwise passed the value into the
    record-name f-string (an implicit `str()`), so an int or a dict silently
    minted a record named after it. The widening this pins is therefore
    total, not partial — deriving from the artifact while silently discarding
    the argument the caller actually passed is substitution.
    """
    spine = _spine(env.root, "widget-refactor")
    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-66660000", "Root", bad, thought_file_path=str(spine))
    assert "must be a string or None" in str(exc.value)
    assert type(bad).__name__ in str(exc.value), "the message should name the type"
    assert not list(env.state.glob("*__*.json")), "refusal must mint nothing"


def test_a_path_object_topic_slug_is_refused(env):
    """The other shape the widening catches, called out separately because it is
    the one that genuinely WORKED before this slice: a `Path` reached the record
    -name f-string and produced a record. It now refuses."""
    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-66660001", "Root", Path("widget"))
    assert "must be a string or None" in str(exc.value)
    assert not list(env.state.glob("*__*.json"))


def test_a_directory_path_is_refused_not_turned_into_a_work_name():
    """THE structural guard. `Projects` is a directory basename, and letting one
    through the work-name position is exactly the record shape this plan counts.
    The `.md` test is what makes that inexpressible."""
    for directory in ("~/repos/Projects", "~/repos/Projects/",
                      "Projects", "/tmp/your-project"):
        assert ppg.canonical_topic_for_spine(directory) is None


def test_project_half_still_defaults_where_the_work_half_refuses():
    """Guards the asymmetry itself: the two halves must NOT be made uniform.

    ONE input, two failure modes — which is the whole point, and which an earlier
    version of this test did not actually demonstrate: it fed the two functions
    DIFFERENT paths (`…/thing.md` vs `…/thing`), so it re-proved the `.md` guard
    (already covered above) rather than the asymmetry it claims to pin. On a
    single shared input the contrast is real: the project half defaults to a safe
    `"Root"` and never fails; the work half refuses, because there a wrong guess
    IS the defect.
    """
    same_input = "/nonexistent/dir/thing"
    assert ppg.canonical_project_for_spine(same_input) == "Root"
    assert ppg.canonical_topic_for_spine(same_input) is None


# ---------------------------------------------------------------------------
# A1 — create_topic: derive-or-refuse, with the explicit claim outranking
# ---------------------------------------------------------------------------

def test_create_topic_derives_the_work_name_from_the_artifact(env):
    spine = _spine(env.root, "widget-refactor")
    ppg.create_topic("sid-aaaaaaaa", "Root", None, thought_file_path=str(spine))
    minted = sorted(p.name for p in env.state.glob("*__*.json"))
    assert minted == ["widget-refactor__Root.json"]
    body = json.loads((env.state / "widget-refactor__Root.json").read_text())
    # Filename and body must AGREE — the disagreement is the countable damage.
    assert body["topic_slug"] == "widget-refactor"
    assert body["project_slug"] == "Root"


def test_create_topic_refuses_with_neither_slug_nor_artifact(env):
    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-bbbbbbbb", "Root", None)
    msg = str(exc.value)
    # The message must name what is missing — a refusal a caller cannot act on
    # is a wall with no door.
    assert "TOPIC_SLUG" in msg
    assert "--thought-file-path" in msg
    assert not list(env.state.glob("*__*.json")), "refusal must mint nothing"


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_topic_slug_is_absence_not_an_explicit_claim(blank, env):
    """`create-topic SID PROJ ""` must refuse, not mint an empty-named record.

    The guard was `if topic_slug is None:`, inherited from the pre-S1 code, so an
    empty string counted as an EXPLICIT slug — which outranks the artifact, skips
    the derivation, and mints a record whose work name is blank. A shell
    expanding an unset variable produces exactly this. Raised by an independent
    checker as adjacent-but-pre-existing; fixed because A1's rewrite owns that
    condition now, so a wrong condition there is a defect in A1's own edit.
    """
    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-33333333", "Root", blank)
    assert "TOPIC_SLUG" in str(exc.value)
    assert not list(env.state.glob("*__*.json")), "refusal must mint nothing"


def test_an_empty_topic_slug_still_defers_to_a_real_artifact(env):
    """The empty-string fix must not become a refusal where derivation is
    possible — a blank slug alongside a usable artifact should DERIVE, exactly as
    an omitted slug does."""
    spine = _spine(env.root, "widget-refactor")
    ppg.create_topic("sid-44444444", "Root", "", thought_file_path=str(spine))
    assert [p.name for p in env.state.glob("*__*.json")] == \
        ["widget-refactor__Root.json"]


def test_create_topic_refuses_an_artifact_that_is_not_a_markdown_file(env):
    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-cccccccc", "Root", None,
                         thought_file_path=str(env.root))
    assert "Markdown" in str(exc.value)
    assert not list(env.state.glob("*__*.json"))


def test_explicit_topic_slug_beats_the_artifact(env):
    """LOAD-BEARING, and not merely a precedence nicety. Live `plain-<sid8>`
    topics legitimately carry an artifact whose slug differs from their key;
    preferring the artifact would refuse exactly those. The banned thing is
    ambient substitution, not explicit declaration."""
    spine = _spine(env.root, "widget-refactor")
    ppg.create_topic("sid-dddddddd", "Root", "plain-dddddddd",
                     thought_file_path=str(spine))
    minted = sorted(p.name for p in env.state.glob("*__*.json"))
    assert minted == ["plain-dddddddd__Root.json"]
    body = json.loads((env.state / "plain-dddddddd__Root.json").read_text())
    assert body["topic_slug"] == "plain-dddddddd"


def test_creating_the_same_work_twice_rebinds_and_mints_no_duplicate(env):
    """Plan `## Verification` item 3 — "Right record, no duplicate (C1, C2)".

    The checklist assigns this test to THIS file by name, and says why: every
    other item resolves to an owning action's fixture test, this one previously
    owned none, so taken literally it would have minted LIVE topic state and
    taken LIVE locks. It requires `TOPIC_STATE_DIR`, `tm.LOCKS_DIR` and
    `_active_path` all pinned into `tmp_path` — the `env` fixture pins the first
    two directly, and `_active_path()` derives from `TOPIC_STATE_DIR`, which this
    test ASSERTS rather than assumes (a pin you have not checked is a pin you do
    not have).

    Missed in rounds 1 and 2 and found by an independent checker reading the
    plan's acceptance list rather than its action cells.
    """
    # The third pin, asserted rather than assumed.
    assert str(env.state) in str(ppg._active_path()), (
        f"_active_path() resolves to {ppg._active_path()}, outside the pinned "
        f"state dir {env.state} — this test would touch the live ledger")

    spine = _spine(env.root, "widget-refactor")

    first = ppg.create_topic("sid-aaaa0001", "Root", None,
                             thought_file_path=str(spine))
    after_first = sorted(p.name for p in env.state.glob("*__*.json"))
    assert after_first == ["widget-refactor__Root.json"]
    record = env.state / "widget-refactor__Root.json"

    # Put something in the record that a blind re-mint would destroy. This is
    # what makes the test about the RIGHT record rather than merely about the
    # file count: an overwrite keeps the count at one and still loses the work.
    body = json.loads(record.read_text())
    body["phase"] = "thought"
    body["phase_history"] = [{"to": "thought"}]
    record.write_text(json.dumps(body), encoding="utf-8")

    # A DIFFERENT session enters the same work.
    second = ppg.create_topic("sid-aaaa0002", "Root", None,
                              thought_file_path=str(spine))

    after_second = sorted(p.name for p in env.state.glob("*__*.json"))
    assert after_second == ["widget-refactor__Root.json"], (
        f"a second create minted an additional record: {after_second}")

    survived = json.loads(record.read_text())
    assert survived["phase"] == "thought", "the second call wiped the phase"
    assert survived["phase_history"] == [{"to": "thought"}], \
        "the second call wiped phase_history"
    assert survived["topic_slug"] == "widget-refactor"
    assert survived["project_slug"] == "Root"

    # Both calls resolved to the same topic identity.
    assert first["topic"] == second["topic"] == "widget-refactor__Root"


def test_explicit_slug_beats_a_GLOB_FOUND_artifact_too(env):
    """Plan `## Verification` item 4 — "Explicit declaration still wins".

    Distinct from `test_explicit_topic_slug_beats_the_artifact`, which passes the
    artifact in. Here nothing is passed: the artifact is GLOB-FOUND from recorded
    state via `_find_recorded_spine_for_slug`. The Guiding Policy singles this
    case out — "an explicit declaration outranks a *glob-found* artifact, which
    is itself just ambient recorded state" — and it is the false-block the
    narrowing exists to prevent, because a live `plain-<sid8>` topic's recorded
    artifact legitimately carries a different slug.
    """
    spine = _spine(env.root, "widget-refactor")
    # A live-shaped record: keyed `plain-<sid8>`, recording an artifact whose
    # own slug is something else entirely.
    (env.state / "plain-bbbb0001__Root.json").write_text(json.dumps({
        "topic_slug": "plain-bbbb0001", "project_slug": "Root",
        "thought_file_path": str(spine), "phase": None, "phase_history": [],
    }), encoding="utf-8")

    ppg.create_topic("sid-bbbb0001", "Root", "plain-bbbb0001")

    minted = sorted(p.name for p in env.state.glob("*__*.json"))
    assert minted == ["plain-bbbb0001__Root.json"], (
        f"the declared slug did not win over the glob-found artifact: {minted}")
    body = json.loads((env.state / "plain-bbbb0001__Root.json").read_text())
    assert body["topic_slug"] == "plain-bbbb0001"


def test_cwd_named_projects_cannot_reach_the_work_name_position(env, monkeypatch):
    """THE regression for the generator this slice closes.

    Before S1 this exact call minted `Projects__Root.json` from `Path.cwd().name`.
    Asserted in BOTH directions on purpose: that nothing was minted, AND that the
    call refused for the stated reason. Checking only for the absent file would
    also pass if `create_topic` had crashed for some unrelated reason — a test
    that cannot distinguish "refused correctly" from "broke" is not a regression
    test for a refusal.
    """
    monkeypatch.chdir(env.root)            # a directory literally named Projects
    assert Path.cwd().name == "Projects"   # the precondition is real

    with pytest.raises(ValueError) as exc:
        ppg.create_topic("sid-eeeeeeee", "Root", None)
    assert "working directory is not a source of identity" in str(exc.value)

    assert not list(env.state.glob("Projects__*.json"))
    assert not list(env.state.glob("*__*.json"))


def test_the_ambient_fallback_symbol_is_gone(env):
    """A1 deletes `_derive_topic_slug` rather than leaving it unreferenced. A
    dead ambient-derivation helper is an invitation for the next edit to call it
    again, which is how this defect would regrow."""
    assert not hasattr(ppg, "_derive_topic_slug")


# ---------------------------------------------------------------------------
# A2 — the CLI seam. Subprocess: monkeypatch does not cross a process boundary.
# ---------------------------------------------------------------------------

def _reap_group(proc):
    """SIGTERM -> ~2s grace -> SIGKILL, against the whole PROCESS GROUP.

    This is the ONE containment sequence, and it is called from a `finally` so it
    runs on EVERY exit path — normal return, timeout, or an unrelated exception —
    not only the one the author anticipated.

    An earlier version of this file put the escalation in the `except
    TimeoutExpired` arm and left the `finally` doing a bare `killpg(SIGKILL)`.
    That inverts the plan's own reasoning: SIGKILL cannot be caught, so the
    child's `finally` never runs and its lock files stay on disk — the exact
    outcome the containment exists to prevent. Any path that reached the
    `finally` with a live child (an unexpected exception, or a raise from inside
    the except arm) got the self-defeating form.

    `--timeout-method=signal` raises a Python exception via SIGALRM and does NOT
    kill subprocesses, and `subprocess.run(timeout=)` sends SIGKILL to the direct
    child only — a grandchild survives. Hence the process GROUP, which is why
    every spawn here passes `start_new_session=True`.
    """
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return                                        # whole group already gone
    except PermissionError:
        pass                                          # try SIGKILL anyway

    # Wait out the grace period, then ALWAYS sweep the group with SIGKILL.
    #
    # The obvious shortcut — return early as soon as `proc.poll()` reports the
    # direct child has exited — is wrong, and subtly so: `poll()` observes ONLY
    # the direct child, while the whole reason this helper signals the GROUP is
    # that a grandchild can outlive it. Returning on the child's exit therefore
    # skips the final sweep in precisely the case the sweep exists for. The
    # early return looks like an optimisation and is a hole.
    #
    # Sweeping unconditionally is near-free when the group is already empty:
    # `killpg` then raises ProcessLookupError, which we expect and swallow.
    # "Near-free", not free — once the child is reaped `proc.pid` is stale, so a
    # recycled pid that happened to be a group leader would be signalled. That
    # is inherent to any pid-based signal and no worse than the pre-fix form.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _spawn(argv, home):
    """Spawn a bounded, contained, isolated child and return (rc, out, err).

    Every subprocess in this file goes through here, so the containment
    discipline is structural rather than repeated by convention — the plan's
    requirement is "ANY subprocess test", and a helper the probe bypassed would
    have satisfied that only where someone remembered to.
    """
    child_env = dict(os.environ)
    # BOTH, not HOME alone: the locks dir may resolve from either, so a
    # HOME-only child can mutate live locks while appearing isolated.
    child_env["HOME"] = str(home)
    child_env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    child_env["CI"] = "1"

    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=child_env, cwd=str(home), start_new_session=True,
    )
    timed_out = False
    try:
        out, err = proc.communicate(timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        out, err = "", ""
    finally:
        _reap_group(proc)                             # every path, one sequence
        # Drain the pipes after reaping so a killed child cannot leave the
        # parent blocked on a half-full pipe.
        #
        # This is pipe/zombie hygiene, and it is ALSO the only `wait()` on the
        # SIGKILL path: `_reap_group` signals and returns without reaping, so
        # this second `communicate()` is what finalizes `proc.returncode` and
        # collects the child.
        #
        # It does NOT, however, protect any assertion. An earlier version of this
        # comment claimed that dropping it would let `rc != 0` checks pass for
        # the wrong reason — that was false, and is corrected rather than quietly
        # reworded: the only path where `_reap_group` meets a live child sets
        # `timed_out`, and `pytest.fail` then fires BEFORE `return
        # proc.returncode`, so no rc is ever returned after an escalation. Keep
        # the call for the hygiene; do not keep it for a guarantee it never made.
        #
        # Calling `communicate()` twice is safe here specifically because no
        # `input=` is passed (that is the only thing that raises on a second
        # call); on the normal path both streams are already at EOF, so it
        # returns ('', '') at once and the `or` keeps the real output.
        try:
            drained_out, drained_err = proc.communicate(timeout=10)
            out = out or drained_out
            err = err or drained_err
        except (subprocess.TimeoutExpired, ValueError):
            pass
    if timed_out:
        pytest.fail(f"child did not finish within {CHILD_TIMEOUT}s: {argv!r}")
    return proc.returncode, out, err


def _run_cli(args, home):
    """Run the create-topic CLI in the contained child above."""
    return _spawn(
        [sys.executable, str(HOOKS / "pre_plan_gates.py"), "create-topic"] + args,
        home)


_LOCKS_PROBE = (
    "import sys;"
    "sys.path.insert(0, sys.argv[1]);"
    "import taskmanagement as tm;"
    "print(tm.LOCKS_DIR)"
)


def _resolved_locks_dir(home):
    """What a child under this HOME actually resolves as its lock directory.

    Imports `taskmanagement` off the hooks directory that physically contains
    `pre_plan_gates.py` — exactly as the CLI under test does. Resolving it via
    `$HOME/.claude/hooks` instead would look tidier and be wrong: HOME is the
    very thing being redirected, so the shim has no hooks/ tree and the probe
    would fail to import and print nothing.
    """
    rc, out, err = _spawn([sys.executable, "-c", _LOCKS_PROBE, str(HOOKS)], home)
    # Surface the child's stderr. A probe that fails silently reports "not
    # isolated" when the truth is "did not run" — different bugs, different fixes.
    assert out.strip(), (f"locks probe produced nothing (rc={rc}); stderr:\n{err}")
    return out.strip()


@pytest.fixture
def child_home(tmp_path):
    """A HOME shim for the child, PROVEN isolated before any test uses it.

    `<home>/.claude/state/...` is what the module derives TOPIC_STATE_DIR and
    tm.LOCKS_DIR from (both are Path.home()-derived; the module never reads
    CLAUDE_CONFIG_DIR).

    The assertion lives HERE, in the thing that BUILDS the isolation, so it holds
    per-test for every test that takes this fixture rather than being established
    once in a sibling test and relied on by convention everywhere else. The plan
    names the locks-dir assertion as a property of "a subprocess test", and a
    fixture-level check is what makes that true of all of them at once.
    """
    home = tmp_path / "childhome"
    (home / ".claude" / "state" / "pre_plan_gates").mkdir(parents=True)
    (home / ".claude" / "state" / "locks").mkdir(parents=True)
    root = home / "Projects"
    (root / "Thoughts").mkdir(parents=True)
    (root / "TODO.md").write_text("# root\n")

    resolved = _resolved_locks_dir(home)
    # `is_relative_to`, not a substring test. `str(home) in resolved` would pass
    # on any path that merely CONTAINS the shim's path as text; containment of a
    # directory is a structural question and deserves the structural check. The
    # substring form was not a realistic false-pass given tmp_path uniqueness —
    # it was just looser than an isolation guard should be.
    assert Path(resolved).is_relative_to(home), (
        f"REFUSING to run: a child under this shim resolves its lock dir to "
        f"{resolved}, which is outside {home}. The test would contend with — and "
        f"could leave stale locks in — the operator's real locks directory.")
    return home


def test_child_is_actually_isolated_from_the_live_lock_dir(child_home):
    """Assert the isolation instead of assuming it. If this fails, every other
    subprocess assertion in this file is meaningless — the child was writing
    into live state while appearing contained."""
    redirected = _resolved_locks_dir(child_home)

    # A CONTROL ARM rather than a hardcoded "live" path. Under `gate` the whole
    # suite already runs with HOME moved to the shim, so `Path.home()` is not the
    # operator's real home — comparing against a path derived from it would be
    # comparing against the shim and could pass vacuously. Resolving the same
    # probe WITHOUT the redirect gives the genuine "unredirected" answer, whatever
    # it happens to be, and the two must differ.
    #
    # Spawned directly rather than through `_spawn` because `_spawn` exists to
    # APPLY the redirect, which is the one thing this arm must not do — but it
    # still gets the same `start_new_session=True` + `_reap_group` containment,
    # so no subprocess in this file escapes the discipline.
    amb = subprocess.Popen(
        [sys.executable, "-c", _LOCKS_PROBE, str(HOOKS)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        start_new_session=True)
    try:
        ambient = amb.communicate(timeout=CHILD_TIMEOUT)[0].strip()
    except subprocess.TimeoutExpired:
        # Same clean diagnosis `_spawn` gives. Without this the timeout escapes
        # as a raw TimeoutExpired — still an ERROR rather than a false pass, but
        # a less legible one.
        pytest.fail(f"control arm did not finish within {CHILD_TIMEOUT}s")
    finally:
        _reap_group(amb)
    assert ambient, "control arm produced no resolved lock dir"

    assert Path(redirected).is_relative_to(child_home), \
        f"child lock dir is outside the shim: {redirected}"
    assert redirected != ambient, (
        "HOME redirection had NO effect on the resolved lock dir — the child "
        f"would write where an unredirected process writes ({ambient})")


def test_cli_derives_the_work_name_from_thought_file_path(child_home):
    spine = child_home / "Projects" / "Thoughts" / "widget-refactor_THOUGHT.md"
    spine.write_text("# widget\n")
    rc, out, err = _run_cli(
        ["sid-ffffffff", "Root", "--thought-file-path", str(spine)], child_home)
    assert rc == 0, f"rc={rc}\nstdout={out}\nstderr={err}"
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    minted = sorted(p.name for p in state.glob("*__*.json"))
    assert minted == ["widget-refactor__Root.json"], f"minted={minted}"
    body = json.loads((state / "widget-refactor__Root.json").read_text())
    assert body["topic_slug"] == "widget-refactor"


def test_cli_refuses_when_given_neither_form(child_home):
    """A2's half of the contract: the refusal must be reachable AND legible from
    production, not just from Python."""
    rc, out, err = _run_cli(["sid-99999999", "Root"], child_home)
    assert rc != 0, f"expected non-zero; got rc=0\nstdout={out}"
    combined = out + err
    assert "TOPIC_SLUG" in combined
    assert "--thought-file-path" in combined
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    assert not list(state.glob("*__*.json")), "a refusal must mint nothing"


def test_cli_still_accepts_the_positional_topic_slug(child_home):
    """The argv contract is otherwise UNCHANGED. Both shipped callers
    (work-start/SKILL.md) pass a positional slug; A2 must not break them."""
    rc, out, err = _run_cli(["sid-88888888", "Root", "plain-88888888"], child_home)
    assert rc == 0, f"rc={rc}\nstdout={out}\nstderr={err}"
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    assert (state / "plain-88888888__Root.json").exists()


@pytest.mark.parametrize("flag", ["--thought-file-path", "--intake-source",
                                  "--todo-line-ref"])
def test_cli_refuses_a_flag_with_no_value_instead_of_reading_it_as_the_slug(
        flag, child_home):
    """A dangling flag must REFUSE, never decay into an explicit TOPIC_SLUG.

    Found by an independent checker on this slice. The strip loop used to guard
    each flag with `tok == "--x" and i + 1 < len(argv)`; a flag supplied with no
    value failed that guard, was NOT stripped, and landed in `argv[4]` — read as
    an explicit slug. Since an explicit slug outranks the artifact, it walked
    straight past A1's refusal and minted a record named after the flag itself.

    Parametrized over all three flags because they share one loop: the gap
    predates this slice for the other two, and fixing one while leaving two would
    leave the same hole under a different name.
    """
    rc, out, err = _run_cli(["sid-11111111", "Root", flag], child_home)
    assert rc != 0, f"expected refusal; got rc=0\nstdout={out}"
    assert flag in (out + err), "the refusal should name the offending flag"
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    minted = sorted(p.name for p in state.glob("*__*.json"))
    assert not minted, f"a dangling flag minted a record: {minted}"


def test_cli_refuses_a_flag_whose_value_is_another_flag(child_home):
    """The same defect, reached one token later and strictly worse.

    `create-topic SID PROJ --thought-file-path --intake-source plain` satisfies
    `i + 1 < len(argv)`, so the old loop consumed `--intake-source` AS the path
    and then read `plain` as the TOPIC_SLUG — minting `plain__Root.json`, a
    record named after a value the caller meant for a different flag entirely.
    """
    rc, out, err = _run_cli(
        ["sid-22222222", "Root", "--thought-file-path", "--intake-source", "plain"],
        child_home)
    assert rc != 0, f"expected refusal; got rc=0\nstdout={out}"
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    minted = sorted(p.name for p in state.glob("*__*.json"))
    # `not minted` already proves the specific `plain__Root.json` case; a second
    # assertion naming it would be unreachable by construction.
    assert not minted, f"a flag-as-value minted a record: {minted}"


def test_cli_flag_may_precede_the_positionals(child_home):
    """The strip loop runs over the whole tail, so flag order is free. Asserted
    because the loop deletes argv entries in place while indexing it."""
    spine = child_home / "Projects" / "Thoughts" / "order-check_THOUGHT.md"
    spine.write_text("# order\n")
    rc, out, err = _run_cli(
        ["--thought-file-path", str(spine), "sid-77777777", "Root"], child_home)
    assert rc == 0, f"rc={rc}\nstdout={out}\nstderr={err}"
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    assert (state / "order-check__Root.json").exists()


def test_cli_flags_interleaved_between_the_positionals(child_home):
    """The loop deletes argv entries in place while scanning forward, so the
    positionals it later indexes are whatever survived. This drives the shape
    most likely to expose an off-by-one: a flag BETWEEN the two positionals, and
    a second flag after them. Previously verified only by hand-tracing."""
    spine = child_home / "Projects" / "Thoughts" / "interleaved_THOUGHT.md"
    spine.write_text("# interleaved\n")
    rc, out, err = _run_cli(
        ["sid-66666666", "--thought-file-path", str(spine),
         "Root", "--intake-source", "thought"],
        child_home)
    assert rc == 0, f"rc={rc}\nstdout={out}\nstderr={err}"
    state = child_home / ".claude" / "state" / "pre_plan_gates"
    minted = sorted(p.name for p in state.glob("*__*.json"))
    assert minted == ["interleaved__Root.json"], f"minted={minted}"
    body = json.loads((state / "interleaved__Root.json").read_text())
    # Both positionals AND both flag values must have landed where they belong.
    assert body["topic_slug"] == "interleaved"
    assert body["project_slug"] == "Root"
    assert body.get("intake_source") == "thought"

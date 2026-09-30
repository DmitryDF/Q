"""
D1 — resolve the research cycle from a key the surface actually has.

Plan: Thoughts/research-source-adapters-20260808213101_D1_PLAN.md

Before D1, `research-scope-gate.sh` and `research-linkcheck.sh` resolved their
cycle from `${CYCLE_ID:-default}` — an environment variable **no code in the
harness ever sets**. So a German-only or Russian-only research run, whose
approval sits on cycle `de` / `ru`, was refused moments after the person
approved it, and its link-check results were filed under `default`.

This suite covers:

  A1  `research_pipeline.resolve_scope_status` — the session-level resolver.
      Manifest shapes: DE-only approved; default-revoked + de-approved;
      revoked-but-never-approved; no cycles; absent manifest; two approved.
      Each asserts the decision, the deciding cycle id, AND which message the
      gate would emit for it.

  A2  `research-scope-gate.sh` at the SUBPROCESS level — the repro that opened
      the plan, both refusal texts unchanged, and the resolver-failure fallback.

  A3  `research-linkcheck.sh` files results against the cycle that owns the
      research file, minting no `default` cycle it never had.

  A5  the post-`reset --cycle-id` abort policy, asserted as a full sequence.

Honours CLAUDE_CONFIG_DIR so it can be run against an experiment clone as well
as live (the hazard `test_s3_walking_skeleton.py:50-58` documents).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = _CONFIG_DIR / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import research_pipeline as rp  # noqa: E402

GATE_SCRIPT = HOOKS_DIR / "research-scope-gate.sh"
LINKCHECK_SCRIPT = HOOKS_DIR / "research-linkcheck.sh"

# The two refusal texts, keyed by a phrase unique to each. The gate must keep
# emitting exactly these; D1 changes only the interpolated cycle id.
_GENERIC_PHRASE = "r1_scope_approved is not set"
_ABORT_PHRASE = "aborted earlier"


# ── fixtures + helpers ───────────────────────────────────────────────────────

@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


def _approve(sid, state_dir, cycle_id):
    """Flip a cycle to r1_scope_approved through the production path.

    Since research-entry-point-enforcement S4 the flip needs an approval
    ARTIFACT, not just a whitelisted caller name: `user_approved_scope` must be
    accompanied by the `scope` object it claims was approved (A4/U3). The
    payload below carries one. These tests are about WHICH CYCLE decides, so the
    artifact is fixture detail — the approval condition itself is pinned in
    test_s4_approval_artifact.py.
    """
    return rp.cmd_advance(
        sid, "r0_intake",
        {"research_file_path": "Thoughts/topic_RESEARCH.md",
         "caller_skill": "/research", "user_approved_scope": True,
         "scope": {"angles": ["a"], "focused_questions": ["q"]}},
        state_dir=state_dir, cycle_id=cycle_id,
    )


def _write_raw(sid, state_dir, state):
    """Write a manifest verbatim — for shapes the production verbs cannot mint
    (zero cycles; unparseable JSON)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "RP-{0}.json".format(sid)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _which_message(status):
    """Which message the gate emits for a scope-status result.

    Mirrors research-scope-gate.sh:100 + :113 exactly — the point of A1
    returning the deciding cycle's OWN flags is that this predicate is
    re-evaluated unchanged, so the revoked-but-never-approved case keeps
    today's generic text rather than shifting to the abort text.
    """
    approved = status["r1_scope_approved"]
    revoked = status["r1_scope_revoked"]
    if approved and revoked:
        return "abort"
    if approved:
        return None          # exit 0 — no message
    return "generic"


def _run_gate(sid, state_dir, tool_name="WebSearch", cycle_env=None,
              extra_env=None):
    """Invoke research-scope-gate.sh with a synthetic PreToolUse payload.

    `cycle_env` sets CYCLE_ID. It is normally left None on purpose: the whole
    point of D1 is that nothing in the harness sets it, so the tests must not
    set it either. It is used only by the regression that proves the gate no
    longer *consults* it.
    """
    env = os.environ.copy()
    env["RP_STATE_DIR"] = str(state_dir)
    env["RESEARCH_SCOPE_GATE_NO_ORIENT"] = "1"
    env.pop("CYCLE_ID", None)
    if cycle_env is not None:
        env["CYCLE_ID"] = cycle_env
    if extra_env:
        env.update(extra_env)
    payload = json.dumps({"tool_name": tool_name, "session_id": sid,
                          "tool_input": {}})
    return subprocess.run(["bash", str(GATE_SCRIPT)], input=payload,
                          capture_output=True, text=True, env=env)


# ── A1 — resolve_scope_status over every manifest shape ──────────────────────

def test_a1_de_only_approved_resolves_to_de(tmp_state):
    """The repro shape: a DE-only run. Approved, deciding cycle `de`, no
    message (the gate exits 0)."""
    sid = "a1-de-only"
    _approve(sid, tmp_state, "de")
    status = rp.cmd_scope_status(sid, tmp_state)

    assert status["decision"] == "approved"
    assert status["cycle_id"] == "de"
    assert status["r1_scope_approved"] is True
    assert status["r1_scope_revoked"] is False
    assert _which_message(status) is None


def test_a1_default_revoked_plus_de_approved_blocks_on_the_revoked_cycle(tmp_state):
    """G5 — the case a naive existential would have opened.

    `cmd_revoke_cycle` leaves `r1_scope_approved` set, so `default` here is
    approved AND revoked. Revocation is evaluated first, so the decision is
    `revoked` even though `de` is a perfectly good approved sibling — the rule
    is never more permissive than today on the revoked axis.
    """
    sid = "a1-mixed"
    _approve(sid, tmp_state, "default")
    _approve(sid, tmp_state, "de")
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")

    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "revoked"
    assert status["cycle_id"] == "default"
    assert status["r1_scope_approved"] is True
    assert status["r1_scope_revoked"] is True
    # Approved AND revoked → the abort text, naming the revoked cycle.
    assert _which_message(status) == "abort"


def test_a1_revoked_but_never_approved_keeps_the_generic_message(tmp_state):
    """THE CASE THAT CHANGED THE DESIGN (plan: Design Review).

    An earlier draft let this run shift to the abort text on the reasoning that
    "strictly more blocking" is safe. It is not the same safety question: C5
    promises the wording and the recovery step are unchanged, and this run would
    have seen different words and a different recovery command.

    It is blocked (revocation-first) AND keeps today's generic text, because the
    resolver returns the deciding cycle's own flags and the gate's
    `approved AND revoked` predicate is false for it — exactly as today.
    """
    sid = "a1-revoked-never-approved"
    # Revoke a cycle that was never approved: no _approve() call first.
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": "Thoughts/topic_RESEARCH.md"},
                   state_dir=tmp_state, cycle_id="default")
    state = rp._read_state(sid, tmp_state)
    assert state["cycles"]["default"]["r1_scope_approved"] is False
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")

    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "revoked"          # blocked
    assert status["cycle_id"] == "default"
    assert status["r1_scope_approved"] is False
    assert status["r1_scope_revoked"] is True
    assert _which_message(status) == "generic"      # ...but today's words


def test_a1_no_cycles_returns_none_on_default(tmp_state):
    """A manifest with zero cycles. `none` on `default`, so the caller's message
    reads exactly as it does today for an unapproved session."""
    sid = "a1-no-cycles"
    _write_raw(sid, tmp_state, {
        "session_id": sid, "schema_version": rp.SCHEMA_VERSION,
        "cycles": {}, "bypass": False,
    })
    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "none"
    assert status["cycle_id"] == rp.DEFAULT_CYCLE_ID
    assert status["r1_scope_approved"] is False
    assert status["r1_scope_revoked"] is False
    assert _which_message(status) == "generic"


def test_a1_absent_manifest_returns_none_and_never_raises(tmp_state):
    """No manifest at all — the `none` decision, not an exception."""
    status = rp.cmd_scope_status("a1-nothing-here", tmp_state)
    assert status["decision"] == "none"
    assert status["cycle_id"] == rp.DEFAULT_CYCLE_ID
    assert _which_message(status) == "generic"


def test_a1_unparseable_manifest_returns_none_and_never_raises(tmp_state):
    """A corrupt manifest must degrade to today's behaviour, never crash the
    gate that consults it."""
    sid = "a1-corrupt"
    tmp_state.mkdir(parents=True, exist_ok=True)
    (tmp_state / "RP-{0}.json".format(sid)).write_text("{not json",
                                                       encoding="utf-8")
    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "none"
    assert status["cycle_id"] == rp.DEFAULT_CYCLE_ID


def test_a1_two_approved_cycles_tie_break_is_lowest_sorted_id(tmp_state):
    """Editorial tie-break, deterministic so messages are reproducible. It is
    observable only in a message string — both cycles are approved, so the
    allow/block decision is the same whichever wins."""
    sid = "a1-two-approved"
    _approve(sid, tmp_state, "ru")
    _approve(sid, tmp_state, "de")
    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "approved"
    assert status["cycle_id"] == "de"               # sorted(["de","ru"])[0]
    assert _which_message(status) is None


def test_a1_never_mutates_the_manifest(tmp_state):
    """Pure read — the resolver must not write the manifest it inspects."""
    sid = "a1-pure"
    _approve(sid, tmp_state, "de")
    path = tmp_state / "RP-{0}.json".format(sid)
    before = path.read_bytes()
    rp.cmd_scope_status(sid, tmp_state)
    assert path.read_bytes() == before


def test_a1_resolver_is_total_over_junk_input():
    """`resolve_scope_status` is pure and total: junk in, fallback out."""
    for junk in (None, [], "nope", {"cycles": "not-a-dict"},
                 {"cycles": {"de": "not-a-dict"}}):
        status = rp.resolve_scope_status(junk)
        assert status["decision"] == "none"
        assert status["cycle_id"] == rp.DEFAULT_CYCLE_ID


# ── A2 — the gate, at the subprocess level ───────────────────────────────────

def test_a2_de_only_run_proceeds(tmp_state):
    """THE DESIRED OUTCOME'S OWN OBSERVABLE.

    Start a German-only research run, approve the scope, and the search
    proceeds rather than stopping with "scope is not approved". Before D1 this
    exited 2. No CYCLE_ID is set — because nothing in the harness sets it.
    """
    sid = "a2-de-only"
    _approve(sid, tmp_state, "de")
    proc = _run_gate(sid, tmp_state)
    assert proc.returncode == 0, proc.stderr


def test_a2_russian_only_run_proceeds(tmp_state):
    """C1 — the language chosen has no bearing on whether approval is honoured."""
    sid = "a2-ru-only"
    _approve(sid, tmp_state, "ru")
    assert _run_gate(sid, tmp_state).returncode == 0


def test_a2_english_run_still_proceeds(tmp_state):
    """Regression: the case that always worked must keep working."""
    sid = "a2-en"
    _approve(sid, tmp_state, "default")
    assert _run_gate(sid, tmp_state).returncode == 0


def test_a2_unapproved_run_still_refused_with_unchanged_text(tmp_state):
    """C4 + C5 — removing the false refusal does not soften the real one, and
    the generic wording and its inspect step are unchanged."""
    sid = "a2-unapproved"
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": "Thoughts/topic_RESEARCH.md"},
                   state_dir=tmp_state, cycle_id="default")
    proc = _run_gate(sid, tmp_state)
    assert proc.returncode == 2
    assert _GENERIC_PHRASE in proc.stderr
    assert _ABORT_PHRASE not in proc.stderr
    assert "research_pipeline.py read {0}".format(sid) in proc.stderr


def test_a2_approved_then_revoked_still_refused_with_abort_text(tmp_state):
    """The abort text is unchanged and names the revoked cycle in its recovery
    command."""
    sid = "a2-aborted"
    _approve(sid, tmp_state, "de")
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="de")
    proc = _run_gate(sid, tmp_state)
    assert proc.returncode == 2
    assert _ABORT_PHRASE in proc.stderr
    assert "research_pipeline.py reset {0} --cycle-id de".format(sid) in proc.stderr


def test_a2_revoked_but_never_approved_blocks_with_the_generic_text(tmp_state):
    """THE PIN, asserted at the subprocess level and not only in A1's unit
    tests (plan A2 validation gate).

    Blocked, and in today's words — the gate's `approved AND revoked` test is
    re-evaluated unchanged on the cycle the resolver named.
    """
    sid = "a2-revoked-never-approved"
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": "Thoughts/topic_RESEARCH.md"},
                   state_dir=tmp_state, cycle_id="default")
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")

    proc = _run_gate(sid, tmp_state)
    assert proc.returncode == 2
    assert _GENERIC_PHRASE in proc.stderr
    assert _ABORT_PHRASE not in proc.stderr


def test_a2_revocation_is_evaluated_first_even_with_an_approved_sibling(tmp_state):
    """G5 at the subprocess level: `default` revoked + `de` approved blocks,
    where a plain existential over `approved` would have exited 0."""
    sid = "a2-mixed"
    _approve(sid, tmp_state, "default")
    _approve(sid, tmp_state, "de")
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")
    proc = _run_gate(sid, tmp_state)
    assert proc.returncode == 2
    assert _ABORT_PHRASE in proc.stderr


def test_a2_resolver_failure_falls_back_to_todays_behaviour(tmp_state):
    """A2 guard rail: any non-zero exit, empty or unparseable resolver output
    falls back to today's `default` lookup — mirroring the topic-orient
    precedent at research-scope-gate.sh:66. Never a crash, never an open gate.

    Forced by pointing the gate's resolver at a python3 that always fails.
    """
    sid = "a2-resolver-broken"
    _approve(sid, tmp_state, "de")

    # A stub `python3` on PATH that exits non-zero: the resolver call fails,
    # and so does the topic-orient call, exactly as a real breakage would.
    stub_dir = tmp_state.parent / "stubbin"
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub = stub_dir / "python3"
    stub.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
    stub.chmod(0o755)

    proc = _run_gate(sid, tmp_state,
                     extra_env={"PATH": "{0}:{1}".format(stub_dir,
                                                         os.environ["PATH"])})
    # Today's behaviour for this manifest read through `default`: refused,
    # with the generic text. Blocked, not opened.
    assert proc.returncode == 2
    assert _GENERIC_PHRASE in proc.stderr


def test_a2_bypass_is_still_honoured(tmp_state):
    """A2 guard rail: do not touch the manifest-level bypass."""
    sid = "a2-bypass"
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": "Thoughts/topic_RESEARCH.md"},
                   state_dir=tmp_state, cycle_id="default")
    rp.cmd_bypass(sid, "operator override for a test", state_dir=tmp_state)
    assert _run_gate(sid, tmp_state).returncode == 0


def test_a2_absent_manifest_keeps_the_excluded_second_false_block_mode(tmp_state):
    """THE EXCLUDED MODE, pinned so nobody mistakes it for a missed one.

    `_S5_PLAN.md:365-374` names two false-block modes; D1 repairs only the
    first. The second is live and stays live: an empty ORIENT_CLASS falls
    through fail-closed at research-scope-gate.sh:88-91 to the exit 2 at :117
    EVEN WHEN NO MANIFEST EXISTS (the `if [ -f "$STATE_FILE" ]` block is
    skipped entirely, so control reaches the refusal with nothing to check).

    Measured on the pre-D1 gate: exit 2, generic text, cycle named `default`.
    It is excluded because every available fix converts a fail-closed arm into
    a fail-open one, which needs evidence first. This test asserts D1 did not
    change it — in either direction.
    """
    proc = _run_gate("a2-no-manifest", tmp_state)
    assert proc.returncode == 2
    assert _GENERIC_PHRASE in proc.stderr
    assert "cycle_id=default" in proc.stderr


def test_a2_absent_manifest_exits_zero_when_orient_answers(tmp_state):
    """The same session WITH topic-orient live: no topic state → not a research
    session → exit 0 at the exemption, never reaching the manifest check. This
    is the arm that keeps the excluded mode off the everyday path."""
    env = os.environ.copy()
    env["RP_STATE_DIR"] = str(tmp_state)
    env.pop("CYCLE_ID", None)
    env.pop("RESEARCH_SCOPE_GATE_NO_ORIENT", None)
    payload = json.dumps({"tool_name": "WebSearch",
                          "session_id": "a2-no-manifest-orient",
                          "tool_input": {}})
    proc = subprocess.run(["bash", str(GATE_SCRIPT)], input=payload,
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0


def test_a2_gate_no_longer_consults_the_cycle_id_variable(tmp_state):
    """The defect's root: `CYCLE_ID` had no producer. Setting it to a cycle
    that does not exist must now change nothing — the gate resolves from the
    manifest, not from the environment."""
    sid = "a2-ignores-env"
    _approve(sid, tmp_state, "de")
    assert _run_gate(sid, tmp_state, cycle_env="nonexistent-cycle").returncode == 0


def test_a2_out_of_spine_bash_is_still_ungated(tmp_state):
    """Regression: the Bash matcher only fires on spine-scoped commands."""
    sid = "a2-bash"
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": "Thoughts/topic_RESEARCH.md"},
                   state_dir=tmp_state, cycle_id="default")
    env = os.environ.copy()
    env["RP_STATE_DIR"] = str(tmp_state)
    env["RESEARCH_SCOPE_GATE_NO_ORIENT"] = "1"
    env.pop("CYCLE_ID", None)
    payload = json.dumps({"tool_name": "Bash", "session_id": sid,
                          "tool_input": {"command": "ls -la"}})
    proc = subprocess.run(["bash", str(GATE_SCRIPT)], input=payload,
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0


# ── A5 — the post-`reset --cycle-id` abort policy ────────────────────────────

def test_a5_reset_of_the_revoked_cycle_lets_an_approved_sibling_proceed(tmp_state):
    """A5, asserted as the full sequence the plan names.

    approve `default` + `de` → revoke `default` → gate blocks →
    `reset --cycle-id default` → the session proceeds.

    RECORDED DECISION: an abort is PER-CYCLE. `reset` clears the revoked cycle,
    and the session then proceeds only if a cycle is still approved — which is
    exactly today's post-reset contract for the resolved cycle. The sibling
    that carries it through is genuinely approved; only the mislookup this plan
    repairs was refusing it.

    This is the more blocking reading available *given* that reset is already
    per-cycle: the alternative (a session-wide abort latch surviving reset)
    would be new blocking state the manifest does not carry, and would leave
    `reset --cycle-id` — the recovery step both refusal messages point at —
    unable to recover anything.
    """
    sid = "a5-reset"
    _approve(sid, tmp_state, "default")
    _approve(sid, tmp_state, "de")
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")

    blocked = _run_gate(sid, tmp_state)
    assert blocked.returncode == 2
    assert _ABORT_PHRASE in blocked.stderr

    rp.cmd_reset(sid, state_dir=tmp_state, cycle_id="default")

    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "approved"
    assert status["cycle_id"] == "de"
    assert _run_gate(sid, tmp_state).returncode == 0


def test_a5_reset_does_not_unblock_when_no_approved_cycle_survives(tmp_state):
    """The other half of the recorded decision: reset is not a blanket unblock.
    With no approved sibling, the session is still refused after the reset."""
    sid = "a5-reset-alone"
    _approve(sid, tmp_state, "default")
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")
    assert _run_gate(sid, tmp_state).returncode == 2

    rp.cmd_reset(sid, state_dir=tmp_state, cycle_id="default")
    status = rp.cmd_scope_status(sid, tmp_state)
    assert status["decision"] == "none"

    proc = _run_gate(sid, tmp_state)
    assert proc.returncode == 2
    assert _GENERIC_PHRASE in proc.stderr


# ── A3 — link-check files against the cycle that owns the file ───────────────

def _run_linkcheck(sid, state_dir, file_path):
    env = os.environ.copy()
    env["RP_STATE_DIR"] = str(state_dir)
    env.pop("CYCLE_ID", None)
    env["CLAUDE_CODE_REMOTE"] = "false"
    # Keep the worker cheap: no URLs to check in the fixture file anyway.
    env["LINKCHECK_TOTAL_BUDGET_S"] = "5"
    payload = json.dumps({"tool_name": "Write", "session_id": sid,
                          "tool_input": {"file_path": str(file_path)}})
    return subprocess.run(["bash", str(LINKCHECK_SCRIPT)], input=payload,
                          capture_output=True, text=True, env=env)


def test_a3_linkcheck_files_under_the_cycle_that_owns_the_file(tmp_path, tmp_state):
    """C3 — findings are filed against the run that produced them, and no
    `default` cycle is minted for a run that never had one."""
    sid = "a3-de-linkcheck"
    research_dir = tmp_path / "Thoughts"
    research_dir.mkdir(parents=True, exist_ok=True)
    research_file = research_dir / "topic_RESEARCH_DE.md"
    research_file.write_text("# DE research\n\nNo links here.\n", encoding="utf-8")

    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": str(research_file),
                    "caller_skill": "/research", "user_approved_scope": True},
                   state_dir=tmp_state, cycle_id="de")
    # Drop the auto-created empty `default` cycle so "was one minted?" is
    # answerable: this run genuinely has only `de`.
    state = rp._read_state(sid, tmp_state)
    del state["cycles"]["default"]
    rp._write_state(sid, state, tmp_state)

    proc = _run_linkcheck(sid, tmp_state, research_file)
    assert proc.returncode == 0          # PostToolUse can never block

    state = rp._read_state(sid, tmp_state)
    assert "linkcheck" in state["cycles"]["de"], state["cycles"]
    assert "default" not in state["cycles"], "minted a cycle the run never had"


def test_a3_linkcheck_unknown_file_still_files_under_default(tmp_path, tmp_state):
    """A file no cycle claims falls back to `default` — today's behaviour, and
    the honest answer when the surface's own key does not resolve."""
    sid = "a3-unknown-file"
    research_dir = tmp_path / "Thoughts"
    research_dir.mkdir(parents=True, exist_ok=True)
    owned = research_dir / "owned_RESEARCH.md"
    owned.write_text("# owned\n", encoding="utf-8")
    stray = research_dir / "stray_RESEARCH.md"
    stray.write_text("# stray\n", encoding="utf-8")

    rp.cmd_advance(sid, "r0_intake", {"research_file_path": str(owned)},
                   state_dir=tmp_state, cycle_id="de")

    proc = _run_linkcheck(sid, tmp_state, stray)
    assert proc.returncode == 0
    state = rp._read_state(sid, tmp_state)
    assert "linkcheck" in state["cycles"]["default"]
    assert "linkcheck" not in state["cycles"]["de"]

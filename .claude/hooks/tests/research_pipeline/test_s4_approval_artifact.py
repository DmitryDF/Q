"""S4 — approval is an ARTIFACT, scoped per cycle. MAJOR 3 revert included.

research-entry-point-enforcement, slice S4 (design A4; locked Guiding Policy
channel 3: "Approval is backed by an artifact, not a string, and is scoped per
cycle. A whitelisted caller name is necessary and never sufficient.").

**MAJOR 3 revert (post-S4, operator-decided).** S4 originally delivered
channel 3's no-inheritance rule by narrowing the SESSION-level
`resolve_scope_status` to read only the session's `current_cycle_id`. That
blocked every genuinely-approved cycle in a session the moment one sibling
cycle registered unapproved — collateral the Guiding Policy never asked for,
independently reproduced against a real `/work-decode` shape. The operator's
fix: `resolve_scope_status` is back to existential approval (its exact
pre-S4 behaviour — see its docstring), and the no-inheritance rule moved to a
new sibling function, `resolve_cycle_scope_status(state, cycle_id)`, consulted
at the dispatch/read site where the cycle id is actually known. This is a
recorded DEVIATION from A4's literal wording; see
`~/.claude/rules/research-scope-framing.md`.

What this file pins, and why each matters:

  * the defect is closed — a whitelisted caller NAME no longer flips;
  * each of U3's three artifact branches flips, and is TAGGED on the cycle so a
    later reader can tell which one said so;
  * the Autonomous route's exemption is recorded as a BYPASS with a reason
    (channel 4), not laundered into an approval;
  * the failure direction is DOWNGRADE — an unapproved intake still registers,
    because r0_intake is how a run declares the file it will write;
  * the SESSION gate (`resolve_scope_status`) is existential again — an
    unapproved cycle does not block an approved sibling;
  * the PER-CYCLE gate (`resolve_cycle_scope_status`) is where "one approved
    cycle does not approve another" now actually lives, and it never
    consults a sibling cycle;
  * a manifest with no `current_cycle_id` still resolves, including the
    multi-cycle case the MINOR 10 fallback used to mis-resolve.

The sibling file test_d1_scope_status.py owns the revocation axis at the
session-gate level; this one deliberately does not restate it, but does
re-verify revocation is not weakened by the restored existential axis, and
adds the per-cycle-gate revocation case the new function needs.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import research_pipeline as rp  # noqa: E402


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


def _intake(sid, state_dir, cycle_id=None, **extra):
    payload = {"research_file_path": "Thoughts/topic_RESEARCH.md"}
    payload.update(extra)
    kwargs = {"state_dir": state_dir}
    if cycle_id is not None:
        kwargs["cycle_id"] = cycle_id
    return rp.cmd_advance(sid, "r0_intake", payload, **kwargs)


def _cycle(sid, state_dir, cycle_id=None):
    state = rp._read_state(sid, state_dir)
    return state["cycles"][cycle_id or rp.DEFAULT_CYCLE_ID]


# ── the resolver, in isolation ───────────────────────────────────────────────

def test_resolver_is_total_and_never_raises():
    """Pure and total: anything in, a dict out. The gate path depends on this —
    a resolver that raised would take the intake down with it."""
    for junk in (
        None, [], "", 0,
        {"caller_skill": []},
        # MAJOR 4: `scope_provenance` is optional and unvalidated by
        # `validate_schema`, so an unhashable value (a list here) reaches the
        # resolver's `provenance not in SCOPE_PROVENANCE_VALUES` check
        # unguarded — that `in` test raises TypeError on an unhashable
        # right-hand operand exactly as the `caller_skill` guard above
        # protects against, but this sibling field had no equivalent guard.
        {"caller_skill": "/research", "user_approved_scope": True,
         "scope": {"angles": ["a"]}, "scope_provenance": ["git-head"]},
    ):
        out = rp.resolve_approval_artifact(junk)
        assert out["approved"] is False
        assert isinstance(out["reason"], str) and out["reason"]


def test_resolver_rejects_unwhitelisted_caller_even_with_an_artifact():
    """Necessary AND sufficient are different claims. Attaching a scope object
    must not let an unwhitelisted caller self-approve."""
    out = rp.resolve_approval_artifact({
        "caller_skill": "/not-a-real-skill",
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
    })
    assert out["approved"] is False
    assert "whitelisted" in out["reason"]


def test_resolver_rejects_a_bare_whitelisted_name():
    """THE defect, stated as a test. Every whitelisted caller, no artifact."""
    for caller in sorted(rp.CALLER_SKILL_WHITELIST):
        out = rp.resolve_approval_artifact({"caller_skill": caller})
        assert out["approved"] is False, caller
        assert "never approval" in out["reason"]


@pytest.mark.parametrize("provenance", sorted(rp.SCOPE_PROVENANCE_VALUES))
def test_resolver_admits_each_u3_branch(provenance):
    payload = {
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
        "scope_provenance": provenance,
    }
    # MINOR 8: a predefined-scope branch (git-head / explicit-argument) needs
    # its reference to be re-checkable; fresh-answer has no file to name.
    if provenance in rp.PREDEFINED_SCOPE_PROVENANCE:
        payload["scope_source_ref"] = "the artifact that named it"
    out = rp.resolve_approval_artifact(payload)
    assert out["approved"] is True
    assert out["provenance"] == provenance
    assert out["bypass"] is False


@pytest.mark.parametrize("provenance", sorted(rp.PREDEFINED_SCOPE_PROVENANCE))
def test_resolver_downgrades_a_predefined_artifact_with_no_source_ref(provenance):
    """MINOR 8: `git-head`'s own doc comment calls it 'verifiable after the
    fact by anyone' — but until this fix, nothing required the reference that
    makes it checkable. A predefined artifact naming nothing is a bare
    self-declaration, not the artifact the whitelist-is-not-enough rule
    exists to require."""
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
        "scope_provenance": provenance,
        # scope_source_ref deliberately absent.
    })
    assert out["approved"] is False
    assert "scope_source_ref" in out["reason"]


def test_resolver_admits_fresh_answer_with_no_source_ref():
    """The one provenance MINOR 8 must NOT tighten: a direct framing exchange
    has no file to name, and requiring one would break every typed run."""
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
        "scope_provenance": rp.APPROVAL_FRESH_ANSWER,
    })
    assert out["approved"] is True


def test_resolver_defaults_absent_provenance_to_fresh_answer():
    """A direct framing exchange produces a fresh answer; it should not have to
    say so to be admitted."""
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
    })
    assert out["approved"] is True
    assert out["provenance"] == rp.APPROVAL_FRESH_ANSWER


def test_resolver_rejects_an_unknown_provenance():
    """An unrecognised provenance is not a free pass — it downgrades. A typo
    must never read as a stronger artifact than it is."""
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
        "scope_provenance": "trust-me",
    })
    assert out["approved"] is False
    assert "scope_provenance" in out["reason"]


def test_resolver_rejects_the_flag_without_its_scope():
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "user_approved_scope": True,
    })
    assert out["approved"] is False
    assert "without a `scope` object" in out["reason"]


def test_resolver_rejects_an_empty_scope_object():
    """An empty dict is not a scope. It would otherwise be the cheapest way to
    re-open the hole this slice closes."""
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {},
    })
    assert out["approved"] is False


def test_resolver_marks_the_autonomous_route_as_a_bypass_not_an_artifact():
    out = rp.resolve_approval_artifact({
        "caller_skill": "/research",
        "autonomous_scope": True,
    })
    assert out["approved"] is True
    assert out["bypass"] is True
    assert out["provenance"] == rp.APPROVAL_AUTONOMOUS
    # Channel 4 — the record names the decision it rests on and the question
    # that is still open, so it reads as carried rather than resolved.
    assert "pending `/clarification --from`" in out["reason"]


# ── the intake path ──────────────────────────────────────────────────────────

def test_named_caller_alone_registers_but_does_not_approve(tmp_state):
    """Downgrade, never refusal: the declaration still lands."""
    _intake("s4-name", tmp_state, caller_skill="/research")
    cycle = _cycle("s4-name", tmp_state)
    assert cycle["r1_scope_approved"] is False
    assert cycle["research_file_path"] == "Thoughts/topic_RESEARCH.md"
    assert cycle["approval_provenance"] is None
    assert "never approval" in cycle["approval_reason"]


def test_artifact_flips_and_tags_the_cycle(tmp_state):
    _intake("s4-art", tmp_state, caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]},
            scope_provenance=rp.APPROVAL_GIT_HEAD,
            scope_source_ref="Thoughts/topic_THOUGHT.md")
    cycle = _cycle("s4-art", tmp_state)
    assert cycle["r1_scope_approved"] is True
    assert cycle["approval_provenance"] == rp.APPROVAL_GIT_HEAD


def test_explicit_argument_records_the_argument_that_named_it(tmp_state):
    """U3's explicit-argument branch is only re-checkable if the reference is
    kept. Recording the provenance without the reference would leave a claim
    nobody can verify — which is the class of thing this slice removes."""
    _intake("s4-ref", tmp_state, caller_skill="/clarification",
            downstream_tool="~/.claude/skills/research/SKILL.md",
            user_approved_scope=True, scope={"focused_questions": ["oq"]},
            scope_provenance=rp.APPROVAL_EXPLICIT_ARGUMENT,
            scope_source_ref="--from Thoughts/t_THOUGHT.md")
    cycle = _cycle("s4-ref", tmp_state)
    assert cycle["r1_scope_approved"] is True
    assert cycle["scope_source_ref"] == "--from Thoughts/t_THOUGHT.md"


def test_autonomous_route_writes_a_per_cycle_bypass_record(tmp_state):
    _intake("s4-auto", tmp_state, caller_skill="/research",
            caller_session_id="caller-sess-1", autonomous_scope=True)
    cycle = _cycle("s4-auto", tmp_state)
    assert cycle["r1_scope_approved"] is True
    assert cycle["scope_approval_bypass"] is True
    assert cycle["scope_approval_bypass_reason"]
    # Deliberately NOT the session-level bypass, which disables the pipeline
    # gate wholesale — a far wider grant than this route asks for.
    state = rp._read_state("s4-auto", tmp_state)
    assert state.get("bypass") is not True


def test_bypass_record_carries_run_specific_evidence(tmp_state):
    """MINOR 9: `scope_approval_bypass_reason` is `AUTONOMOUS_BYPASS_REASON`, a
    compile-time constant naming the POLICY the exemption rests on — it is
    identical across every Autonomous run and cannot distinguish a
    legitimately-chosen one from a caller that merely set the flag. Run-
    specific evidence (caller_skill, caller_session_id, a recorded-at
    timestamp) must sit ALONGSIDE the reason, not replace it."""
    _intake("s4-bypass-evidence", tmp_state, caller_skill="/research",
            caller_session_id="caller-sess-42", autonomous_scope=True)
    cycle = _cycle("s4-bypass-evidence", tmp_state)
    # The policy half is unchanged — still the compile-time constant.
    assert cycle["scope_approval_bypass_reason"] == rp.AUTONOMOUS_BYPASS_REASON
    # The evidence half is new and run-specific.
    assert cycle["scope_approval_bypass_caller_skill"] == "/research"
    assert cycle["scope_approval_bypass_caller_session_id"] == "caller-sess-42"
    assert cycle["scope_approval_bypass_recorded_at"]


def test_a_non_autonomous_approval_writes_no_bypass_record(tmp_state):
    """Only a genuine exemption is recorded as one, or the record stops meaning
    anything."""
    _intake("s4-nobypass", tmp_state, caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    cycle = _cycle("s4-nobypass", tmp_state)
    assert cycle.get("scope_approval_bypass") is not True


def test_autonomous_route_with_a_stray_scope_source_ref_does_not_record_it(tmp_state):
    """MINOR 3 (round-4 fix): a payload carrying a whitelisted caller,
    autonomous_scope=True, AND a scope_source_ref (e.g. left over from a
    caller that also set a real-artifact field) must not let that reference
    land on the cycle. Both the Autonomous bypass branch and the artifact
    branch of `resolve_approval_artifact` return `approved: True`, so gating
    the write on `verdict["approved"]` alone (the round-3 'lesser note' fix)
    let it through — producing a self-contradictory record:
    `approval_provenance: 'autonomous'` + `scope_approval_bypass: True`
    beside a `scope_source_ref` the autonomous branch never checked and never
    admitted. The reviewer's verdict: a defect in RECORD FIDELITY, not
    authorization — both branches approve, so nothing is wrongly granted or
    denied. `scope_source_ref` is written only when
    `verdict["provenance"] in PREDEFINED_SCOPE_PROVENANCE`
    (git-head/explicit-argument) — the two branches MINOR 8 actually
    requires it to be non-empty for."""
    _intake("s4-auto-strayref", tmp_state, caller_skill="/research",
            autonomous_scope=True,
            scope_source_ref="Thoughts/x_THOUGHT.md")
    cycle = _cycle("s4-auto-strayref", tmp_state)
    assert cycle["r1_scope_approved"] is True
    assert cycle["approval_provenance"] == rp.APPROVAL_AUTONOMOUS
    assert cycle["scope_approval_bypass"] is True
    assert "scope_source_ref" not in cycle


def test_predefined_artifact_still_records_its_scope_source_ref(tmp_state):
    """The MINOR 3 fix narrows the write's gate from `approved` to
    `provenance in PREDEFINED_SCOPE_PROVENANCE` — confirm it still WRITES for
    the two branches that actually need it (git-head / explicit-argument),
    not just that it withholds for the autonomous branch above."""
    _intake("s4-predefined-ref", tmp_state, caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]},
            scope_provenance="explicit-argument",
            scope_source_ref="--from Thoughts/t_THOUGHT.md")
    cycle = _cycle("s4-predefined-ref", tmp_state)
    assert cycle["approval_provenance"] == "explicit-argument"
    assert cycle["scope_source_ref"] == "--from Thoughts/t_THOUGHT.md"


def test_fresh_answer_with_a_stray_scope_source_ref_does_not_record_it(tmp_state):
    """MINOR 7 (round-5 fix): the write-gate comment previously justified
    excluding the Autonomous branch only, leaving `fresh-answer` and a
    rejected artifact under-documented even though the same gate excludes
    both. `fresh-answer` is excluded for the same class of reason as
    Autonomous: a direct framing exchange has no file to name, so a
    `scope_source_ref` supplied anyway is a stray left over from some other
    field, not a re-checkable reference — writing it would claim a property
    (`git-head`'s own doc comment: "verifiable after the fact by anyone")
    this provenance cannot back."""
    _intake("s4r5-fresh-strayref", tmp_state, caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]},
            scope_provenance="fresh-answer",
            scope_source_ref="Thoughts/x_THOUGHT.md")
    cycle = _cycle("s4r5-fresh-strayref", tmp_state)
    assert cycle["r1_scope_approved"] is True
    assert cycle["approval_provenance"] == "fresh-answer"
    assert "scope_source_ref" not in cycle
    # Related, same fix site: the raw payload audit trail is unfiltered — the
    # stray reference still sits ONE LEVEL DOWN, at the checkpoint's own
    # stored data, even though the hoisted top-level copy was withheld.
    assert (cycle["checkpoints"]["r0_intake"]["data"]["scope_source_ref"]
            == "Thoughts/x_THOUGHT.md")


def test_a_rejected_artifact_with_a_stray_scope_source_ref_does_not_record_it(
    tmp_state,
):
    """MINOR 7 (round-5 fix): a REJECTED artifact (`user_approved_scope` set
    with no `scope` object — `resolve_approval_artifact`'s property-2
    rejection) resolves `provenance: None`, which also falls outside
    `PREDEFINED_SCOPE_PROVENANCE`, so a stray `scope_source_ref` is not
    recorded here either. `approval_reason` already records WHY the cycle
    did not approve; attaching an unverified reference to that record would
    claim a check that never ran. Since `r0_intake` cannot be re-called for
    this cycle, this diagnostic detail is gone for good once dropped — an
    accepted cost of not writing an unverified field, not an oversight."""
    _intake("s4r5-rejected-strayref", tmp_state, caller_skill="/research",
            user_approved_scope=True,
            scope_source_ref="Thoughts/x_THOUGHT.md")
    cycle = _cycle("s4r5-rejected-strayref", tmp_state)
    assert cycle["r1_scope_approved"] is False
    assert cycle["approval_provenance"] is None
    assert "not itself the artifact" in cycle["approval_reason"]
    assert "scope_source_ref" not in cycle
    assert (cycle["checkpoints"]["r0_intake"]["data"]["scope_source_ref"]
            == "Thoughts/x_THOUGHT.md")


# ── the angles sidecar is gated on the verdict, not just the flag ───────────

def test_unapproved_payload_does_not_stamp_the_angles_sidecar(tmp_path):
    """MINOR 11: the sidecar write used to be scoped to `user_approved_scope`
    alone ('a fact about the payload, stays true whether or not the cycle
    flipped') — so a payload the resolver REJECTED as unverifiable still got a
    USER_CONFIRMED checklist written beside the report, which the coverage
    axis then scores the finished report against. A predefined provenance
    with no scope_source_ref (MINOR 8's own rejection case) reproduces it
    cleanly: the resolver downgrades, and the sidecar must not appear."""
    state_dir = tmp_path / "state"
    report = tmp_path / "topic_RESEARCH.md"
    report.write_text("body\n", encoding="utf-8")
    sidecar = tmp_path / "topic_RESEARCH_angles.json"

    rp.cmd_advance(
        "s4-sidecar-gate", "r0_intake",
        {
            "research_file_path": str(report),
            "caller_skill": "/research",
            "user_approved_scope": True,
            "scope": {"angles": ["should not be written"]},
            "scope_provenance": rp.APPROVAL_GIT_HEAD,
            # scope_source_ref deliberately absent -> resolver downgrades.
        },
        state_dir=state_dir,
    )
    cycle = rp._read_state(
        "s4-sidecar-gate", state_dir
    )["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is False
    assert not sidecar.exists()


def test_approved_payload_still_stamps_the_angles_sidecar(tmp_path):
    """The MINOR 11 gate must not overcorrect: a genuinely approved run still
    writes the checklist exactly as before."""
    state_dir = tmp_path / "state"
    report = tmp_path / "topic_RESEARCH.md"
    report.write_text("body\n", encoding="utf-8")
    sidecar = tmp_path / "topic_RESEARCH_angles.json"

    rp.cmd_advance(
        "s4-sidecar-ok", "r0_intake",
        {
            "research_file_path": str(report),
            "caller_skill": "/research",
            "user_approved_scope": True,
            "scope": {"angles": ["keep me"]},
        },
        state_dir=state_dir,
    )
    assert sidecar.exists()


def test_autonomous_bypass_with_user_approved_scope_does_not_stamp_the_sidecar(
    tmp_path,
):
    """research-entry-point-enforcement S4 round-6 MINOR 4: the same self-
    contradiction round-5's MINOR 3 fixed for `scope_source_ref`
    (`test_autonomous_route_with_a_stray_scope_source_ref_does_not_record_it`
    above), at a site that fix missed. `resolve_approval_artifact` checks
    `autonomous_scope` BEFORE `user_approved_scope`, so a payload carrying
    BOTH flags resolves through the Autonomous bypass branch — which never
    validates `scope` at all — and still comes back `approved: True` with
    `provenance: APPROVAL_AUTONOMOUS`. Gating the sidecar write on
    `user_approved_scope` + `verdict["approved"]` alone let that combination
    stamp a `USER_CONFIRMED` angles checklist for a scope nobody validated —
    not reachable from any shipped caller (the Autonomous template sets only
    `autonomous_scope`), hence MINOR rather than MAJOR, but the record must
    still not lie about what was confirmed."""
    state_dir = tmp_path / "state"
    report = tmp_path / "topic_RESEARCH.md"
    report.write_text("body\n", encoding="utf-8")
    sidecar = tmp_path / "topic_RESEARCH_angles.json"

    rp.cmd_advance(
        "s4-sidecar-autonomous-bypass", "r0_intake",
        {
            "research_file_path": str(report),
            "caller_skill": "/research",
            "autonomous_scope": True,
            "user_approved_scope": True,
            "scope": {"angles": ["should not be stamped"]},
        },
        state_dir=state_dir,
    )
    cycle = rp._read_state(
        "s4-sidecar-autonomous-bypass", state_dir
    )["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is True
    assert cycle["approval_provenance"] == rp.APPROVAL_AUTONOMOUS
    assert cycle["scope_approval_bypass"] is True
    assert not sidecar.exists()


# ── approval: session-level is existential (MAJOR 3 revert), ────────────────
# ── per-cycle no-inheritance lives at resolve_cycle_scope_status ────────────

def test_an_unapproved_cycle_does_not_ride_a_sibling_at_the_session_gate(tmp_state):
    """MAJOR 3 revert: the SESSION gate is back to existential approval — any
    approved cycle approves the session, so an unapproved sibling cannot block
    it. `default` is approved with a real artifact; `de` then opens with a
    bare caller name (unapproved). The session still resolves `approved`."""
    sid = "s4-percycle"
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    assert rp.resolve_scope_status(
        rp._read_state(sid, tmp_state))["decision"] == "approved"

    _intake(sid, tmp_state, cycle_id="de", caller_skill="/research")
    status = rp.resolve_scope_status(rp._read_state(sid, tmp_state))
    assert status["decision"] == "approved"
    assert status["cycle_id"] == "default"


def test_an_approved_cycle_does_not_approve_a_sibling_at_the_percycle_gate(tmp_state):
    """Channel 3's headline: 'one approved cycle does not approve another' —
    this function CORRECTLY COMPUTES that answer for one named cycle with no
    reference to any sibling, which is what this test pins.

    S4 round-2 (ITEM 3) split a claim this docstring used to make in one
    breath: computing the right answer is not the same as that answer being
    ENFORCED anywhere. Before ITEM 2 (same round), nothing in code called this
    function on a read or dispatch path at all — `~/.claude/skills/research/
    SKILL.md` only instructed the AI, in prose, to check it before dispatching
    the `research` agent, which is FOLLOWED, not enforced (nothing in code
    stops a model from skipping that instruction). ITEM 2 wires this exact
    function into `declared_read._resolve_scope_from_cycle`, so the
    declared-source READ path is now genuinely enforced in code. The
    `research` agent DISPATCH remains followed-not-enforced — see
    `research-scope-framing.md` for that split. This test exercises only the
    function's own decision, not either downstream caller.

    `default` is approved with a real artifact; `de` opens with a bare caller
    name only (no artifact). The per-cycle predicate for `de` must still read
    unapproved, with no reference to `default`'s state at all."""
    sid = "s4-percycle-strict"
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    _intake(sid, tmp_state, cycle_id="de", caller_skill="/research")
    state = rp._read_state(sid, tmp_state)

    default_status = rp.resolve_cycle_scope_status(state, "default")
    assert default_status["decision"] == "approved"
    assert default_status["cycle_id"] == "default"

    de_status = rp.resolve_cycle_scope_status(state, "de")
    assert de_status["decision"] == "none"
    assert de_status["cycle_id"] == "de"
    assert de_status["r1_scope_approved"] is False
    assert isinstance(de_status["reason"], str) and de_status["reason"]


def test_percycle_resolver_is_total_and_never_raises():
    """S4 round-2, ITEM 5: `resolve_cycle_scope_status` docstrings itself as
    'Pure, total, never raises' but `cycles.get(cycle_id)` hashes its key —
    an unhashable `cycle_id` (a list, concretely) raised TypeError before this
    guard existed. Its sibling `resolve_approval_artifact` already carries
    isinstance guards for the identical hazard on `caller_skill` and
    `scope_provenance`; this pins the equivalent guard on this function.

    This stopped being a merely-theoretical hazard the moment ITEM 2 (same
    round) put this function on a refusal path
    (`declared_read._resolve_scope_from_cycle`) — a raise there would take
    that read down with it rather than downgrading to a refusal."""
    for junk_state in (None, [], "", 0, {"cycles": []}, {"cycles": "x"}):
        out = rp.resolve_cycle_scope_status(junk_state, "default")
        assert out["decision"] == "none"
        assert isinstance(out["reason"], str) and out["reason"]

    for junk_cycle_id in (["a"], {"a": 1}, None, 0, 3.14):
        out = rp.resolve_cycle_scope_status(
            {"cycles": {"default": {"r1_scope_approved": True}}}, junk_cycle_id
        )
        assert out["decision"] == "none"
        assert out["r1_scope_approved"] is False
        assert isinstance(out["reason"], str) and out["reason"]
        # cycle_id is echoed back verbatim (it is a data value in the result,
        # not a dict key there) — the guard must not swallow it.
        assert out["cycle_id"] == junk_cycle_id

    # The exact repro from the spec, pinned literally.
    out = rp.resolve_cycle_scope_status({"cycles": {"default": {}}}, ["a"])
    assert out["decision"] == "none"


def test_each_cycle_approved_on_its_own_artifact_resolves_approved(tmp_state):
    """The restored existential axis must not punish a session that did the
    right thing twice — and each cycle also passes its own per-cycle check."""
    sid = "s4-both"
    for cid in ("default", "de"):
        _intake(sid, tmp_state, cycle_id=cid, caller_skill="/research",
                user_approved_scope=True, scope={"angles": [cid]})
    state = rp._read_state(sid, tmp_state)
    status = rp.resolve_scope_status(state)
    assert status["cycle_id"] == "de"
    assert status["decision"] == "approved"
    assert rp.resolve_cycle_scope_status(state, "default")["decision"] == "approved"
    assert rp.resolve_cycle_scope_status(state, "de")["decision"] == "approved"


def test_revocation_still_outranks_an_approved_sibling_at_the_session_gate(tmp_state):
    """Property 1 survives the revert: revocation stays existential and is
    evaluated first at the SESSION gate, so a revoked sibling still blocks a
    session whose other cycle is perfectly approved. The restored existential
    approval axis must not weaken this — verified explicitly."""
    sid = "s4-revoke"
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")
    _intake(sid, tmp_state, cycle_id="de", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["b"]})
    status = rp.resolve_scope_status(rp._read_state(sid, tmp_state))
    assert status["decision"] == "revoked"
    assert status["cycle_id"] == "default"


def test_revocation_still_outranks_an_approved_cycle_at_the_percycle_gate(tmp_state):
    """The per-cycle predicate also keeps revocation load-bearing: a revoked
    cycle reads `revoked` from `resolve_cycle_scope_status` even though
    `cmd_revoke_cycle` leaves `r1_scope_approved` set (revocation is
    additional state, not a transition) — a plain existential over `approved`
    alone would wrongly let it through."""
    sid = "s4-revoke-percycle"
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state, cycle_id="default")
    state = rp._read_state(sid, tmp_state)
    status = rp.resolve_cycle_scope_status(state, "default")
    assert status["decision"] == "revoked"
    assert status["r1_scope_approved"] is True  # the flag survives; the decision doesn't


def test_major3_scenario_session_approves_and_only_the_unapproved_cycle_blocks(tmp_state):
    """The regression this whole revert exists for (MAJOR 3, `/work-decode`
    shape): three cycles, two carry real approved artifacts, one does not.
    The session resolves `approved` (so rows 1 and 2 are never blocked by
    row 3's registration), and the per-cycle predicate is what actually tells
    rows 1/2 apart from row 3."""
    sid = "s4-major3"
    _intake(sid, tmp_state, cycle_id="wd-b-r1", caller_skill="/work-decode",
            user_approved_scope=True, scope={"angles": ["r1"]})
    _intake(sid, tmp_state, cycle_id="wd-b-r2", caller_skill="/work-decode",
            user_approved_scope=True, scope={"angles": ["r2"]})
    _intake(sid, tmp_state, cycle_id="wd-b-r3", caller_skill="/work-decode")

    state = rp._read_state(sid, tmp_state)
    session_status = rp.resolve_scope_status(state)
    assert session_status["decision"] == "approved"

    assert rp.resolve_cycle_scope_status(state, "wd-b-r1")["decision"] == "approved"
    assert rp.resolve_cycle_scope_status(state, "wd-b-r2")["decision"] == "approved"
    r3_status = rp.resolve_cycle_scope_status(state, "wd-b-r3")
    assert r3_status["decision"] == "none"
    assert r3_status["r1_scope_approved"] is False


def test_current_cycle_id_is_recorded_on_every_advance(tmp_state):
    """Not only at intake: 'current' means the cycle being advanced. Kept as a
    naming/diagnostic field — see resolve_scope_status's docstring — even
    though it is no longer decisive for approval."""
    sid = "s4-current"
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    _intake(sid, tmp_state, cycle_id="de", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["b"]})
    rp.cmd_advance(sid, "r1_scope", {"search_scope": "x"},
                   state_dir=tmp_state, cycle_id="default")
    assert rp._read_state(sid, tmp_state)["current_cycle_id"] == "default"


def test_a_pre_s4_manifest_without_current_cycle_id_still_resolves(tmp_state):
    """Backward compatibility, by the `updated_at` fallback in
    `_current_cycle_id` (consulted only for the `none`-branch remediation
    naming now, not for approval). A manifest written before this field
    existed carries no `current_cycle_id`; a `de`-only approved manifest still
    resolves `approved` on the restored existential axis."""
    sid = "s4-legacy"
    _intake(sid, tmp_state, cycle_id="de", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    state = rp._read_state(sid, tmp_state)
    del state["current_cycle_id"]
    status = rp.resolve_scope_status(state)
    assert status["cycle_id"] == "de"
    assert status["decision"] == "approved"


def test_a_pre_s4_multicycle_manifest_without_current_cycle_id_resolves_approved(tmp_state):
    """MINOR 10's behavioural half (the reviewer's multi-cycle legacy case),
    dissolved by the MAJOR 3 revert: under S4's per-cycle approval, a manifest
    with no `current_cycle_id` fell back to the most-recently-updated cycle,
    so an approved-but-not-last-touched cycle could read as unapproved. The
    restored existential axis makes that fallback irrelevant to approval —
    `default` is approved first (with a real artifact), `de` is opened later
    with no artifact (so it is the most recently updated, and the LEGACY
    fallback would have picked it), and the session still resolves
    `approved` because ANY approved cycle now qualifies."""
    sid = "s4-legacy-multi"
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})
    _intake(sid, tmp_state, cycle_id="de", caller_skill="/research")
    state = rp._read_state(sid, tmp_state)
    del state["current_cycle_id"]
    status = rp.resolve_scope_status(state)
    assert status["decision"] == "approved"
    assert status["cycle_id"] == "default"


def test_current_cycle_id_never_raises_on_a_non_string_updated_at(tmp_state):
    """MINOR 4 (round-4 fix): `_current_cycle_id`'s docstring promises
    'naming/diagnostic only' and is reached from `resolve_scope_status`'s
    `none`-branch remediation text — a function on that path that raises
    contradicts the same totality discipline rounds 2 and 3 hardened
    `resolve_approval_artifact` and `resolve_cycle_scope_status` against
    (`x not in frozenset` / `cycles.get(cycle_id)` TypeErrors). A manifest
    where one cycle's `updated_at` is numeric used to raise
    `TypeError: '>' not supported between instances of 'str' and 'int'`
    inside the `key > best_key` compare, because `updated_at or ""` only
    substitutes for a FALSY value — a non-empty int passes straight
    through unguarded. It must instead resolve without raising."""
    sid = "s4-numeric-updated-at"
    # UNAPPROVED intake (no artifact) — `resolve_scope_status`'s `none`
    # branch is the one call site of `_current_cycle_id`, so the manifest
    # must have nothing approved to actually exercise it through the public
    # entry point.
    _intake(sid, tmp_state, cycle_id="default", caller_skill="/research")
    state = rp._read_state(sid, tmp_state)
    # Corrupt the cycle's updated_at to a non-string, as a hand-edited or
    # malformed manifest write might produce.
    state["cycles"]["default"]["updated_at"] = 1234567890
    del state["current_cycle_id"]

    # Must not raise, on the real call path (resolve_scope_status's `none`
    # branch consults `_current_cycle_id` for remediation-text naming).
    status = rp.resolve_scope_status(state)
    assert status["decision"] == "none"
    assert status["cycle_id"] == "default"
    # Direct regression pin on the previously-raising function itself.
    assert rp._current_cycle_id(state, state["cycles"]) == "default"


# ── the audit trail ──────────────────────────────────────────────────────────

def test_flip_audit_records_the_outcome_not_just_the_attempt(tmp_state):
    """Before S4, reaching the audit implied the flip happened, so the row
    needed no outcome. Now a named caller may register WITHOUT flipping, and a
    row recording only the name would read identically in both cases."""
    _intake("s4-audit-no", tmp_state, caller_skill="/research")
    _intake("s4-audit-yes", tmp_state, caller_skill="/research",
            user_approved_scope=True, scope={"angles": ["a"]})

    rows = [
        json.loads(line)
        for line in (tmp_state / "flip_audit.jsonl").read_text(
            encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_sid = {r["session_id"]: r for r in rows}
    assert by_sid["s4-audit-no"]["approved"] is False
    assert by_sid["s4-audit-no"]["approval_provenance"] is None
    assert by_sid["s4-audit-yes"]["approved"] is True
    assert by_sid["s4-audit-yes"]["approval_provenance"] == rp.APPROVAL_FRESH_ANSWER


# ── the scope drafter has a production caller (finding 36) ───────────────────

_CANNED_DRAFT = json.dumps({
    "angles": ["a1", "a2", "a3"],
    "focused_questions": ["q1", "q2", "q3"],
    "search_terms": {"en": ["t1", "t2", "t3"]},
    "suggested_depth": "standard",
    "where_to_search": ["docs", "forums"],
    "languages": ["en"],
})


def _draft(payload, invoker):
    sys.path.insert(0, str(Path.home() / ".claude" / "skills"))
    from research.scope_draft_adapter_claude import draft_from_payload
    return draft_from_payload(payload, invoker=invoker)


def test_drafter_has_a_production_entry_point():
    """Finding 36's terminating condition: a run reaching the framing flow
    without a written scope gets its proposal from PRODUCTION code, not from a
    test double. Before S4 the adapter's only construction site was a fixture."""
    out = _draft(
        {"routing_path": "ninja", "user_query": "does X work",
         "selected_languages": ["en"]},
        invoker=lambda prompt: _CANNED_DRAFT,
    )
    assert out["kind"] == "output"
    assert out["angles"] == ["a1", "a2", "a3"]
    assert out["suggested_depth"] == "standard"


def test_drafter_entry_point_returns_a_degraded_outcome_not_a_crash():
    """The flow controller has ONE degraded path (`error_drafter_failed` →
    retry / re-route / cancel). A raised exception would not reach it."""
    def boom(prompt):
        raise RuntimeError("network down")

    out = _draft({"routing_path": "ninja", "user_query": "q"}, invoker=boom)
    assert out["kind"] == "error"
    assert out["reason"]


def test_drafter_entry_point_rejects_a_malformed_intake_as_an_outcome():
    """A bad intake is returned on the same degraded path as a failed model
    turn — two failure channels would mean two things for the caller to handle."""
    out = _draft({"user_query": "no routing path"},
                 invoker=lambda p: _CANNED_DRAFT)
    assert out["kind"] == "error"
    assert out["code"] == "malformed_intake"


def test_drafter_cli_runs_as_a_subprocess_without_a_traceback():
    """BLOCKER 1 regression guard: the `sys.path` fix that makes
    `research.scope_draft_port` importable must run BEFORE that import, not in
    the trailing `if __name__ == "__main__":` block at the bottom of the file —
    Python executes the whole module body, including the top-level import,
    before it ever reaches a block at the bottom, so a fix placed there is
    unreachable and every real CLI invocation raised `ModuleNotFoundError: No
    module named 'research'` before printing anything.

    `_draft()` above imports `draft_from_payload` directly after inserting the
    skills dir onto `sys.path` itself — it exercises the function, never the
    CLI, so it cannot catch this class of defect. This test runs the file
    exactly as `research-scope-framing.md` Step 2 invokes it: as a real
    subprocess, stdin in, stdout out, nothing pre-arranged on `sys.path`. The
    import (and the bug that broke it) happens before stdin is even read, so a
    malformed intake exercises the same import path a well-formed one would,
    at near-zero cost.

    research-entry-point-enforcement S4 round-2 repointed the live wire from
    the (absent) Anthropic SDK to a real `claude --print` subprocess call, so a
    WELL-FORMED intake now reaches a model on every run of this test — network,
    a working `claude` binary, ~9s, non-deterministic, billed. That is the
    wrong default for a unit suite. The malformed-intake path below returns
    `{"kind": "error", "code": "bad_json"}` at exit 2, before `main()`
    constructs any invoker (`scope_draft_adapter_claude.main`) — it still
    proves module import + `main()` + the JSON contract, without ever
    touching the wire. The live wire itself is covered by
    `test_drafter_cli_reaches_a_live_model_end_to_end` below, opt-in only."""
    script = str(
        Path.home() / ".claude" / "skills" / "research"
        / "scope_draft_adapter_claude.py"
    )
    proc = subprocess.run(
        [sys.executable, script],
        input="not valid json",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 2, (
        "a malformed intake must exit 2, before any invoker is constructed; "
        "got {rc}: stderr={err!r}".format(rc=proc.returncode, err=proc.stderr)
    )
    assert proc.stderr == "", (
        "the CLI must never print a traceback: {err!r}".format(err=proc.stderr)
    )
    out = json.loads(proc.stdout)
    assert out["kind"] == "error"
    assert out["code"] == "bad_json"


@pytest.mark.skipif(
    os.environ.get("RESEARCH_DRAFTER_LIVE") != "1",
    reason=(
        "opt-in live-model test, skipped by default: it reaches a real "
        "`claude --print` subprocess (~9s, needs network + a working `claude` "
        "binary, bills tokens, non-deterministic output) — the wrong default "
        "for a unit suite. Set RESEARCH_DRAFTER_LIVE=1 to run it."
    ),
)
def test_drafter_cli_reaches_a_live_model_end_to_end():
    """The live counterpart to the cheap regression guard above. Asserts
    exactly what research-entry-point-enforcement S4 round-2 verified by
    hand: a well-formed intake through the real CLI, over the real
    `claude --print` wire, returns exit 0 and a schema-conforming
    six-component draft.

    Do not delete this or weaken it to a mock — the point of the pair is
    that one of them is real. It proves the wire works in isolation; it does
    NOT prove a typed `/research` reaches the operator correctly end-to-end
    inside a live session (see `~/.claude/skills/research/SKILL.md` and
    `research-scope-framing.md` Step 2 for that still-open caveat, and for
    the unresolved contradiction with this repo's documented in-session
    `claude --print` failures)."""
    script = str(
        Path.home() / ".claude" / "skills" / "research"
        / "scope_draft_adapter_claude.py"
    )
    proc = subprocess.run(
        [sys.executable, script],
        input=json.dumps({
            "routing_path": "ninja",
            "user_query": "does X work",
            "conversation_language": "en",
            "selected_languages": ["en"],
        }),
        capture_output=True,
        text=True,
        # Lesser note (round-3 fix): the inner wire budget
        # (_LIVE_MODEL_TIMEOUT_SECONDS in scope_draft_adapter_claude.py) is
        # 120s. An outer timeout below that flakes under a slow model instead
        # of letting the inner budget degrade cleanly to a {"kind": "error"}
        # result — raised here above the inner budget so a genuine hang still
        # bounds the test, but a slow-not-hung model does not.
        timeout=150,
    )
    assert proc.returncode == 0, (
        "the CLI must exit 0 on a well-formed intake over the live wire; "
        "got {rc}: stderr={err!r}".format(rc=proc.returncode, err=proc.stderr)
    )
    assert proc.stderr == "", (
        "the CLI must never print a traceback: {err!r}".format(err=proc.stderr)
    )
    out = json.loads(proc.stdout)
    assert out["kind"] == "output", (
        "expected a real draft from the live wire, got {out!r}".format(out=out)
    )
    for component in (
        "angles", "focused_questions", "search_terms",
        "suggested_depth", "where_to_search", "languages",
    ):
        assert component in out, "missing component {c!r}: {out!r}".format(
            c=component, out=out
        )

"""D2 — abort withdraws the runs that actually failed.

Before D2, `check-research-pipeline-gate.sh` dispatched `dispatch-non-pass`
with no `--cycle-id` at all three of its dispatch sites, so the CLI fell back
to `DEFAULT_CYCLE_ID` and the abort revoked `default` whichever run had failed.

These tests make the plan's three-case partition executable:

  * the failing cycle is NOT `default`  → it, and not `default`, is withdrawn
  * several cycles fail at once         → each of them is withdrawn
  * the failing cycle IS `default`      → back-compat, behaviour unchanged
                                          (with the ONE named exception: the
                                          interactive arm's printed command now
                                          carries an explicit `--cycle-id`)

plus the two downstream harms the mis-target produced — the message the
operator is shown, and the recovery it advertises.

**No second driver.** The gate drivers are imported from the suites that
already own them: the pipeline gate from `test_s8_stop_gate`, the scope gate
from `test_d1_scope_status`. Only manifest-shaping helpers are local, and they
build manifests through the production verbs.

Arm coverage is deliberate and non-redundant:

  * the `abort` arm prints NO recovery command (its dispatch is redirected to
    /dev/null), so a printed-command assertion cannot live on it;
  * the `accepted` arm and the interactive arm are each asserted at N=2,
    because at N=1 "loops correctly" and "does not loop at all" are the same
    observable — both target the single failing cycle.
"""
import json
import sys
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import research_pipeline as rp  # noqa: E402

# The gate drivers — imported, never re-implemented (A3 guard rail).
from test_s8_stop_gate import (  # noqa: E402
    _make_active,
    _make_r_md,
    _research_dir,
    _run_gate as _run_pipeline_gate,
)
from test_d1_scope_status import _run_gate as _run_scope_gate  # noqa: E402

# The two refusal texts, keyed by a phrase unique to each (same keys D1 uses).
_GENERIC_PHRASE = "r1_scope_approved is not set"
_ABORT_PHRASE = "aborted earlier"


# ── manifest shaping ─────────────────────────────────────────────────────────

def _complete_cycle(sid, state_dir, cycle_id, approved=True):
    """Walk one cycle through all six checkpoints.

    `approved=False` omits the approval artifact, which is what leaves
    `r1_scope_approved` unset — the shape case (ii) needs for its
    never-approved `default`.

    Since research-entry-point-enforcement S4 an approved intake must carry the
    `scope` object alongside `user_approved_scope` (A4/U3): a whitelisted caller
    name is necessary and never sufficient. Omitting `caller_skill` still leaves
    the cycle unapproved, so the `approved=False` arm is unchanged in effect.
    """
    intake = {"research_file_path": "Thoughts/d2_{0}_RESEARCH.md".format(cycle_id)}
    if approved:
        intake["caller_skill"] = "/research"
        intake["user_approved_scope"] = True
        intake["scope"] = {"angles": ["a"], "focused_questions": ["q"]}
    rp.cmd_advance(sid, "r0_intake", intake, state_dir=state_dir, cycle_id=cycle_id)
    rp.cmd_advance(sid, "r1_scope", {"search_scope": "d2"},
                   state_dir=state_dir, cycle_id=cycle_id)
    rp.cmd_advance(sid, "r2_research", {"sources_count": 1},
                   state_dir=state_dir, cycle_id=cycle_id)
    rp.cmd_advance(sid, "r3_synthesis", {"claims_count": 1},
                   state_dir=state_dir, cycle_id=cycle_id)
    rp.cmd_advance(sid, "r4_factcheck", {}, state_dir=state_dir, cycle_id=cycle_id)
    rp.cmd_advance(sid, "r5_recommend", {}, state_dir=state_dir, cycle_id=cycle_id)


def _marker_dir(plan_val, proj, topic, cycle_id):
    """Where the gate looks for a cycle's R-markers.

    `default` reads the flat research dir; every other cycle reads a
    subdirectory named for it (check-research-pipeline-gate.sh:229-233).
    """
    base = _research_dir(plan_val, proj, topic)
    if cycle_id == rp.DEFAULT_CYCLE_ID:
        return base
    d = base / cycle_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build(tmp_path, sid, verdicts, approved=(), unapproved=()):
    """Build a manifest + marker tree.

    `verdicts` maps cycle id → R-marker verdict. `approved` / `unapproved`
    name which cycles get complete checkpoint chains (and, for `approved`,
    `r1_scope_approved`). Returns (state_dir, active_file, plan_val).
    """
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    for cid in approved:
        _complete_cycle(sid, state_dir, cid, approved=True)
    for cid in unapproved:
        _complete_cycle(sid, state_dir, cid, approved=False)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    _make_active(active, sid, "topicD2", "projD2")
    for cid, verdict in verdicts.items():
        _make_r_md(_marker_dir(plan_val, "topicD2", "projD2", cid) / "R1.md", verdict)
    return state_dir, active, plan_val


def _cycles(sid, state_dir):
    return rp._read_state(sid, state_dir)["cycles"]


# ── step 1 — the measured repro: the failing cycle is withdrawn, not `default` ─

def test_abort_withdraws_the_failing_cycle_and_leaves_default_approved(tmp_path):
    """`default` PASS + `de` ESCALATE, abort → `de` revoked, `default` intact.

    This is the defect in one assertion: before D2 the two halves were swapped.
    """
    sid = "d2-step1"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "ESCALATE"},
        approved=("default", "de"),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 2, proc.stderr

    cycles = _cycles(sid, state_dir)
    assert cycles["de"].get("r1_scope_revoked") is True, (
        "the cycle that failed verification must be the one withdrawn"
    )
    assert cycles["default"].get("r1_scope_revoked") is not True, (
        "a cycle that PASSED was not aborted and keeps its approval"
    )
    assert cycles["default"].get("r1_scope_approved") is True


# ── step 2 — several failures, abort arm ─────────────────────────────────────

def test_abort_withdraws_every_failing_cycle(tmp_path):
    """`de` AND `ru` both fail → BOTH revoked, `default` untouched.

    The loop can mark several cycles non-PASS; a single-id design would have to
    pick one and silently drop the rest.
    """
    sid = "d2-step2"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "ESCALATE", "ru": "ESCALATE"},
        approved=("default", "de", "ru"),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 2, proc.stderr

    cycles = _cycles(sid, state_dir)
    assert cycles["de"].get("r1_scope_revoked") is True
    assert cycles["ru"].get("r1_scope_revoked") is True
    assert cycles["default"].get("r1_scope_revoked") is not True


# ── step 2b — several failures, INTERACTIVE arm (the arm that prints) ────────

def test_interactive_printed_command_names_every_failing_cycle(tmp_path):
    """Two failures, `CLAUDE_RESEARCH_ON_NON_PASS` unset → the printed
    copy-paste command names BOTH `de` and `ru`.

    This assertion cannot live on the `abort` arm, which prints no recovery
    command at all. Without it, an implementation that hard-codes the printed
    command to one cycle passes every other step here.
    """
    sid = "d2-step2b"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "ESCALATE", "ru": "ESCALATE"},
        approved=("default", "de", "ru"),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass=None)
    assert proc.returncode == 2, proc.stderr
    assert "--cycle-id de" in proc.stderr, proc.stderr
    assert "--cycle-id ru" in proc.stderr, proc.stderr
    assert "--cycle-id default" not in proc.stderr, (
        "a cycle that PASSED must not be offered for acceptance"
    )
    assert proc.stderr.count("On 'accepted':") == 2, (
        "one line per failing cycle, not one line for the lowest-sorted id"
    )


# ── step 3 — back-compat, all three arms ─────────────────────────────────────

def test_backcompat_single_default_abort_unchanged(tmp_path):
    """Single-`default` session, abort → `default` revoked, exit 2. As today."""
    sid = "d2-step3-abort"
    state_dir, active, plan_val = _build(
        tmp_path, sid, verdicts={"default": "ESCALATE"}, approved=("default",),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 2, proc.stderr
    cycles = _cycles(sid, state_dir)
    assert cycles[rp.DEFAULT_CYCLE_ID].get("r1_scope_revoked") is True
    assert list(cycles.keys()) == [rp.DEFAULT_CYCLE_ID]


def test_backcompat_single_default_accepted_unchanged(tmp_path):
    """Single-`default` session, accepted → exit 0, `non_pass_verdict` on
    `default`, nothing revoked. As today."""
    sid = "d2-step3-accepted"
    state_dir, active, plan_val = _build(
        tmp_path, sid, verdicts={"default": "ESCALATE"}, approved=("default",),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="accepted")
    assert proc.returncode == 0, proc.stderr
    assert "RESEARCH-VERDICT: PASS (auto-accepted;" in proc.stderr, proc.stderr
    cycles = _cycles(sid, state_dir)
    assert cycles[rp.DEFAULT_CYCLE_ID].get("non_pass_verdict") is not None
    assert cycles[rp.DEFAULT_CYCLE_ID].get("r1_scope_revoked") is not True


def test_backcompat_single_default_interactive_prints_explicit_cycle_id(tmp_path):
    """Single-`default` session, interactive → the printed command now carries
    `--cycle-id default` and is otherwise unchanged; exit code unchanged.

    This is the ONE thing that changes for a single-run English session, and it
    is behaviourally inert: `default` is already the CLI's fallback, so the old
    line and the new line do the same thing. Back-compat evidence collected only
    from `abort` and `accepted` — the arms where nothing changes — would prove
    nothing about the change actually made.
    """
    sid = "d2-step3-interactive"
    state_dir, active, plan_val = _build(
        tmp_path, sid, verdicts={"default": "ESCALATE"}, approved=("default",),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass=None)
    assert proc.returncode == 2, proc.stderr

    expected = (
        "On 'accepted':       python3 ${KIT_HOOKS_DIR}/research_pipeline.py "
        "dispatch-non-pass {sid} --on-non-pass accepted --cycle-id default"
    ).format(sid=sid)
    assert expected in proc.stderr, proc.stderr
    assert proc.stderr.count("On 'accepted':") == 1

    # The rest of the interactive surface is untouched.
    assert "ACTION REQUIRED: invoke AskUserQuestion" in proc.stderr
    assert "On 'another-round':" in proc.stderr
    assert "RESEARCH-VERDICT: 1 non-PASS research cycle(s)." in proc.stderr


# ── step 4 — case (i) end-to-end: abort → block → reset → re-approval ────────

def test_case_i_recovery_leaves_the_failed_run_needing_reapproval(tmp_path):
    """`default` approved+PASS, `de` approved+failed.

    Abort blocks the session, the operator is pointed at the run that actually
    failed, and following that recovery leaves the failed run needing a fresh
    approval — never resuming on the old one.
    """
    sid = "d2-step4"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "ESCALATE"},
        approved=("default", "de"),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 2, proc.stderr

    # The session blocks, and the operator is told about the run that failed.
    blocked = _run_scope_gate(sid, state_dir)
    assert blocked.returncode == 2, blocked.stderr
    assert _ABORT_PHRASE in blocked.stderr, blocked.stderr
    assert "--cycle-id de" in blocked.stderr, (
        "the advertised recovery must name the run that failed, not the one "
        "that passed"
    )

    # Follow it.
    rp.cmd_reset(sid, state_dir=state_dir, cycle_id="de")
    assert "de" not in _cycles(sid, state_dir)

    # Asserted at the PIPELINE level, not the raw tool-gate level. After the
    # reset an approved `default` legitimately reopens the gate session-wide;
    # that is correct and is not the property under test, so it is pinned as
    # documented behaviour rather than asserted the other way round.
    reopened = _run_scope_gate(sid, state_dir)
    assert reopened.returncode == 0, reopened.stderr

    # The property D2 delivers: the failed run cannot continue where it left
    # off — a later checkpoint is refused, and a fresh intake is required.
    with pytest.raises(ValueError):
        rp.cmd_advance(sid, "r1_scope", {"search_scope": "resume"},
                       state_dir=state_dir, cycle_id="de")
    rp.cmd_advance(
        sid, "r0_intake",
        {"research_file_path": "Thoughts/d2_de_RESEARCH.md",
         "caller_skill": "/research", "user_approved_scope": True,
         # S4: the re-approval carries its artifact, exactly as the first one did.
         "scope": {"angles": ["a"], "focused_questions": ["q"]}},
        state_dir=state_dir, cycle_id="de",
    )
    assert _cycles(sid, state_dir)["de"].get("r1_scope_approved") is True


# ── step 5 — case (ii) message: abort text, not the generic text ─────────────

def test_case_ii_abort_text_shown_not_generic(tmp_path):
    """A `de`-only run that fails, with `default` never approved.

    Before D2 the abort revoked the never-approved `default`, which makes the
    scope gate's `approved AND revoked` predicate false — so the operator got
    the generic "scope is not approved" text for what was actually an abort,
    and no recovery step at all. Revoking `de` makes the existing predicate
    select correctly, with no change to either message.
    """
    sid = "d2-step5"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "ESCALATE"},
        approved=("de",), unapproved=("default",),
    )
    assert _cycles(sid, state_dir)["default"].get("r1_scope_approved") is not True

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 2, proc.stderr

    blocked = _run_scope_gate(sid, state_dir)
    assert blocked.returncode == 2, blocked.stderr
    assert _ABORT_PHRASE in blocked.stderr, blocked.stderr
    assert _GENERIC_PHRASE not in blocked.stderr, (
        "an aborted run must not be described as one whose scope was never "
        "approved"
    )
    assert "--cycle-id de" in blocked.stderr, blocked.stderr


# ── step 5b — the `accepted` arm at N=2 ──────────────────────────────────────

def test_accepted_arm_records_the_verdict_on_every_failing_cycle(tmp_path):
    """`default` PASS + `de` AND `ru` fail, accepted → `non_pass_verdict` on
    both `de` and `ru`, and none on `default`.

    N=2 is required, not incidental: with one failing cycle, an implementation
    that forwards only the first accumulated id is indistinguishable from a
    correct loop. And a single-`default` manifest cannot tell a fixed accepted
    arm from an unfixed one, because both target `default`.
    """
    sid = "d2-step5b"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "ESCALATE", "ru": "ESCALATE"},
        approved=("default", "de", "ru"),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="accepted")
    assert proc.returncode == 0, proc.stderr

    cycles = _cycles(sid, state_dir)
    assert cycles["de"].get("non_pass_verdict") is not None, (
        "the record must name the run that was let through"
    )
    assert cycles["ru"].get("non_pass_verdict") is not None, (
        "every accepted failure is recorded, not just the lowest-sorted one"
    )
    assert cycles["default"].get("non_pass_verdict") is None, (
        "a run that passed was never accepted-past-a-failure"
    )
    # `accepted` records; it never withdraws.
    for cid in ("default", "de", "ru"):
        assert cycles[cid].get("r1_scope_revoked") is not True


# ── the empty case — nothing failed, so nothing is dispatched ────────────────

def test_nothing_dispatched_when_no_cycle_failed(tmp_path):
    """All cycles PASS → exit 0 and no cycle is touched.

    The accumulator must be the empty string, not a single blank entry: a blank
    id would dispatch against a cycle named "" and mint one.
    """
    sid = "d2-empty"
    state_dir, active, plan_val = _build(
        tmp_path, sid,
        verdicts={"default": "PASS", "de": "PASS"},
        approved=("default", "de"),
    )

    proc = _run_pipeline_gate(sid, state_dir=state_dir, active_file=active,
                              plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 0, proc.stderr
    assert "RESEARCH-VERDICT: PASS" in proc.stderr, proc.stderr

    cycles = _cycles(sid, state_dir)
    assert set(cycles.keys()) == {"default", "de"}, (
        "no cycle may be minted by a dispatch that should not have fired"
    )
    for cid in ("default", "de"):
        assert cycles[cid].get("r1_scope_revoked") is not True
        assert cycles[cid].get("non_pass_verdict") is None

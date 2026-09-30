"""
S2 contract tests for research_pipeline.py additive extensions.

Covers:
  * cmd_record_non_pass — records `non_pass_verdict={report, recorded_at}`
    on a cycle (S8 Stop-gate `accepted` branch consumer).
  * cmd_revoke_cycle — sets `r1_scope_revoked=true` + `non_pass_abort_ts`
    on a cycle (S7 autonomous-headless abort consumer).
  * cmd_advance r0_intake guard — refuses revoked cycle with remediation.
  * cmd_reset --cycle-id — surgical per-cycle archive + remove.
  * Whitelist additive extension (`/research` joined) — back-compat for
    `/clarification` + `/work-decode` flips unchanged.
  * Forged-payload rejection — `user_approved_scope=true` /
    `autonomous_scope=true` without a whitelisted caller_skill → ValueError.
  * flip_audit.jsonl appender writes one line per flip event.

S7 additions:
  * resolve_on_non_pass_mode — default 'abort', env var override, explicit flag.
  * cmd_dispatch_non_pass — one test per mode (accepted / another-round / abort).
  * revoke→remediation surface — research-scope-gate.sh emits plain-English text
    on a revoked cycle (subprocess test).
  * Q11 plain-English assertion — remediation body contains no internal pipeline
    identifiers (r0_intake, r1_scope_approved, cycle_id, caller_skill).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
import research_pipeline as rp  # noqa: E402

GATE_SCRIPT = HOOKS_DIR / "research-scope-gate.sh"

# Internal identifiers that must NEVER appear in user-facing remediation text (Q11).
_Q11_FORBIDDEN = frozenset({
    "r0_intake", "r1_scope_approved", "r1_scope_revoked",
    "cycle_id", "caller_skill", "autonomous_scope", "user_approved_scope",
    "non_pass_verdict", "non_pass_abort_ts",
})


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


def _intake_payload(**extra):
    p = {"research_file_path": "Thoughts/topic_RESEARCH.md"}
    p.update(extra)
    return p


# ── non_pass_verdict mode tests ──────────────────────────────────────────────

def test_record_non_pass_accepted(tmp_state):
    sid = "n1"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    rp.cmd_record_non_pass(sid, "RESEARCH-VERDICT: accepted (user override)",
                           state_dir=tmp_state)
    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["non_pass_verdict"]["report"] == \
        "RESEARCH-VERDICT: accepted (user override)"
    assert cycle["non_pass_verdict"]["recorded_at"]


def test_record_non_pass_another_round(tmp_state):
    sid = "n2"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    rp.cmd_record_non_pass(sid, "RESEARCH-VERDICT: another-round requested",
                           state_dir=tmp_state)
    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert "another-round" in cycle["non_pass_verdict"]["report"]


def test_record_non_pass_abort(tmp_state):
    sid = "n3"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    rp.cmd_record_non_pass(sid, "RESEARCH-VERDICT: abort", state_dir=tmp_state)
    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["non_pass_verdict"]["report"] == "RESEARCH-VERDICT: abort"


def test_record_non_pass_empty_report_exits(tmp_state):
    sid = "n4"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    with pytest.raises(SystemExit):
        rp.cmd_record_non_pass(sid, "   ", state_dir=tmp_state)


# ── revoke-cycle tests ───────────────────────────────────────────────────────

def test_revoke_cycle_sets_revoked_and_abort_ts(tmp_state):
    sid = "v1"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state)
    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_revoked"] is True
    assert cycle["non_pass_abort_ts"]


def test_revoked_cycle_blocks_fresh_r0_intake(tmp_state):
    sid = "v2"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state)
    # cmd_advance on the same cycle_id must refuse with remediation text.
    with pytest.raises(ValueError) as exc:
        rp.cmd_advance(sid, "r0_intake", _intake_payload(),
                       state_dir=tmp_state)
    msg = str(exc.value)
    assert "aborted earlier" in msg
    assert "reset" in msg
    assert "--cycle-id" in msg


def test_reset_per_cycle_recovers_revoked(tmp_state):
    sid = "v3"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state)
    rp.cmd_reset(sid, state_dir=tmp_state, cycle_id=rp.DEFAULT_CYCLE_ID)
    # After reset, r0_intake on the same cycle works again.
    rp.cmd_advance(sid, "r0_intake", _intake_payload(), state_dir=tmp_state)
    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_revoked"] is False
    # Archive snapshot exists.
    archive_dir = tmp_state / "archive"
    snapshots = list(archive_dir.glob(f"RP-{sid}-cycle-*"))
    assert snapshots, "per-cycle archive snapshot must exist"


# ── backward-compatibility regressions ──────────────────────────────────────

def test_clarification_caller_alone_no_longer_flips(tmp_state):
    """INVERTED by research-entry-point-enforcement S4 (A4, channel 3).

    This test asserted the defect: a whitelisted caller NAME flipped
    `r1_scope_approved` with no approval artifact anywhere in the payload. The
    locked Guiding Policy says a caller name is "necessary and never
    sufficient", so the name alone must now leave the cycle unapproved — and
    the cycle must say why, so the refusal is answerable from the record.

    The caller is NOT refused: the intake still registers (the run has to be
    able to declare the file it will write). It simply does not flip.
    """
    sid = "bc1"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/clarification",
                        caller_session_id="abc",
                        downstream_tool="~/.claude/skills/research/SKILL.md"),
        state_dir=tmp_state,
    )
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is False
    assert cycle["user_approved_scope"] is False
    assert cycle["autonomous_scope"] is False
    # Registration still happened — downgrade, never refusal.
    assert cycle["caller_skill"] == "/clarification"
    assert cycle["approval_provenance"] is None
    assert "no approval artifact" in cycle["approval_reason"]


def test_clarification_caller_with_predefined_scope_flips(tmp_state):
    """The `/clarification` Step-6 case: `--from <spine>` names the predefined
    scope explicitly, so U3's explicit-argument branch admits it."""
    sid = "bc1b"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/clarification",
                        caller_session_id="abc",
                        downstream_tool="~/.claude/skills/research/SKILL.md",
                        user_approved_scope=True,
                        scope={"focused_questions": ["oq1", "oq2"]},
                        scope_provenance=rp.APPROVAL_EXPLICIT_ARGUMENT,
                        scope_source_ref="--from Thoughts/topic_THOUGHT.md"),
        state_dir=tmp_state,
    )
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is True
    assert cycle["approval_provenance"] == rp.APPROVAL_EXPLICIT_ARGUMENT
    assert cycle["scope_source_ref"] == "--from Thoughts/topic_THOUGHT.md"


def test_work_decode_caller_alone_no_longer_flips(tmp_state):
    """Same inversion for `/work-decode` — its R-gate confirmation is the
    artifact, and it must be carried rather than implied by the caller name."""
    sid = "bc2"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/work-decode"),
        state_dir=tmp_state,
    )
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is False


# ── /research path (S2 additive flip site widening) ─────────────────────────

def test_research_caller_with_user_approved_scope_flips(tmp_state):
    """S4: the flag alone is not the artifact — it needs the `scope` object it
    claims was approved. With the scope present this is U3's fresh-answer
    branch, which is what a direct framing exchange produces."""
    sid = "r1"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/research", user_approved_scope=True,
                        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state,
    )
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is True
    assert cycle["user_approved_scope"] is True
    assert cycle["autonomous_scope"] is False
    assert cycle["approval_provenance"] == rp.APPROVAL_FRESH_ANSWER


def test_research_caller_with_flag_but_no_scope_downgrades(tmp_state):
    """The flag without the scope is the same unverifiable string the artifact
    rule exists to reject. It downgrades to interactive confirmation — it does
    NOT refuse the intake."""
    sid = "r1b"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/research", user_approved_scope=True),
        state_dir=tmp_state,
    )
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is False
    # The additive flag is still recorded — it is state, never authorization.
    assert cycle["user_approved_scope"] is True
    assert "without a `scope` object" in cycle["approval_reason"]


def test_research_caller_with_autonomous_scope_flips(tmp_state):
    sid = "r2"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/research", autonomous_scope=True),
        state_dir=tmp_state,
    )
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_approved"] is True
    assert cycle["autonomous_scope"] is True
    assert cycle["user_approved_scope"] is False


# ── forged-payload rejection ────────────────────────────────────────────────

def test_user_approved_scope_without_caller_skill_rejected(tmp_state):
    with pytest.raises(ValueError) as exc:
        rp.validate_schema(
            "r0_intake",
            _intake_payload(user_approved_scope=True),
        )
    assert "user_approved_scope" in str(exc.value) or \
        "additional state" in str(exc.value)


def test_autonomous_scope_without_caller_skill_rejected(tmp_state):
    with pytest.raises(ValueError) as exc:
        rp.validate_schema(
            "r0_intake",
            _intake_payload(autonomous_scope=True),
        )
    assert "autonomous_scope" in str(exc.value) or \
        "additional state" in str(exc.value)


def test_forged_caller_skill_with_flag_rejected(tmp_state):
    # Non-whitelisted caller_skill is already rejected; this test asserts
    # the existing rejection still fires when flags are also present.
    with pytest.raises(ValueError) as exc:
        rp.validate_schema(
            "r0_intake",
            _intake_payload(caller_skill="/forged",
                            user_approved_scope=True),
        )
    assert "/forged" in str(exc.value) or "whitelist" in str(exc.value)


# ── flip_audit.jsonl appender ───────────────────────────────────────────────

def test_flip_audit_appends_one_line_per_flip(tmp_state):
    sid = "a1"
    rp.cmd_advance(
        sid,
        "r0_intake",
        _intake_payload(caller_skill="/research", user_approved_scope=True,
                        scope={"angles": ["x", "y"]}),
        state_dir=tmp_state,
    )
    audit = tmp_state / "flip_audit.jsonl"
    assert audit.exists(), "flip_audit.jsonl must be created on flip"
    lines = audit.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["caller_skill"] == "/research"
    assert entry["user_approved_scope"] is True
    assert entry["autonomous_scope"] is False
    assert entry["session_id"] == sid
    assert entry["cycle_id"] == rp.DEFAULT_CYCLE_ID
    assert len(entry["scope_payload_hash"]) == 12


# ── S7: resolve_on_non_pass_mode ─────────────────────────────────────────────

def test_resolve_mode_defaults_to_abort(monkeypatch):
    monkeypatch.delenv("CLAUDE_RESEARCH_ON_NON_PASS", raising=False)
    assert rp.resolve_on_non_pass_mode() == "abort"


def test_resolve_mode_env_var_overrides_default(monkeypatch):
    monkeypatch.setenv("CLAUDE_RESEARCH_ON_NON_PASS", "accepted")
    assert rp.resolve_on_non_pass_mode() == "accepted"


def test_resolve_mode_explicit_flag_overrides_env_var(monkeypatch):
    monkeypatch.setenv("CLAUDE_RESEARCH_ON_NON_PASS", "another-round")
    assert rp.resolve_on_non_pass_mode(explicit_flag="accepted") == "accepted"


def test_resolve_mode_raises_on_unknown(monkeypatch):
    monkeypatch.delenv("CLAUDE_RESEARCH_ON_NON_PASS", raising=False)
    with pytest.raises(ValueError, match="Unknown --on-non-pass mode"):
        rp.resolve_on_non_pass_mode(explicit_flag="bogus")


# ── S7: cmd_dispatch_non_pass — one test per mode ────────────────────────────

def test_dispatch_non_pass_accepted_records_non_pass(tmp_state):
    sid = "dp1"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(
        caller_skill="/research", user_approved_scope=True,
        # S4: carry the artifact, so this fixture still produces an APPROVED
        # cycle. Without it the cycle is revoked-but-never-approved, which by
        # the resolver's own property 2 gets the GENERIC text rather than the
        # revoked text — a different shape than these tests mean to exercise.
        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state)
    result = rp.cmd_dispatch_non_pass(sid, "accepted", state_dir=tmp_state)
    assert result["ok"] is True
    assert result["mode"] == "accepted"
    assert result["action"] == "non_pass_verdict recorded"
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert "accepted" in cycle["non_pass_verdict"]["report"]
    # Cycle must NOT be revoked.
    assert cycle["r1_scope_revoked"] is False


def test_dispatch_non_pass_another_round_records_non_pass(tmp_state):
    sid = "dp2"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(
        caller_skill="/research", user_approved_scope=True,
        # S4: carry the artifact, so this fixture still produces an APPROVED
        # cycle. Without it the cycle is revoked-but-never-approved, which by
        # the resolver's own property 2 gets the GENERIC text rather than the
        # revoked text — a different shape than these tests mean to exercise.
        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state)
    result = rp.cmd_dispatch_non_pass(sid, "another-round", state_dir=tmp_state)
    assert result["ok"] is True
    assert result["mode"] == "another-round"
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert "another-round" in cycle["non_pass_verdict"]["report"]
    assert cycle["r1_scope_revoked"] is False


def test_dispatch_non_pass_abort_revokes_cycle(tmp_state):
    sid = "dp3"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(
        caller_skill="/research", user_approved_scope=True,
        # S4: carry the artifact, so this fixture still produces an APPROVED
        # cycle. Without it the cycle is revoked-but-never-approved, which by
        # the resolver's own property 2 gets the GENERIC text rather than the
        # revoked text — a different shape than these tests mean to exercise.
        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state)
    result = rp.cmd_dispatch_non_pass(sid, "abort", state_dir=tmp_state)
    assert result["ok"] is True
    assert result["mode"] == "abort"
    assert result["action"] == "cycle revoked"
    cycle = rp._read_state(sid, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_revoked"] is True
    assert cycle["non_pass_abort_ts"]


# ── S7: revoke→remediation surfaces via research-scope-gate.sh ──────────────

def _run_gate(sid, cycle_id="default", state_dir=None, tool_name="WebSearch"):
    """Invoke research-scope-gate.sh as a subprocess; return CompletedProcess.

    Sets RESEARCH_SCOPE_GATE_NO_ORIENT=1 to bypass the topic-orient check —
    test sessions have no topic-orient state and would otherwise exit 0 early
    before reaching the manifest check (the code path under test here).
    """
    env = os.environ.copy()
    if state_dir is not None:
        env["RP_STATE_DIR"] = str(state_dir)
    env["CYCLE_ID"] = cycle_id
    env["RESEARCH_SCOPE_GATE_NO_ORIENT"] = "1"
    payload = json.dumps({"tool_name": tool_name, "session_id": sid,
                          "tool_input": {}})
    return subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def test_revoked_cycle_gate_exits_2_with_remediation(tmp_state):
    """Gate must exit 2 and emit plain-English remediation for a revoked cycle."""
    sid = "gate1"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(
        caller_skill="/research", user_approved_scope=True,
        # S4: carry the artifact, so this fixture still produces an APPROVED
        # cycle. Without it the cycle is revoked-but-never-approved, which by
        # the resolver's own property 2 gets the GENERIC text rather than the
        # revoked text — a different shape than these tests mean to exercise.
        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state)
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state)
    proc = _run_gate(sid, state_dir=tmp_state)
    assert proc.returncode == 2
    stderr = proc.stderr
    assert "aborted earlier" in stderr
    assert "reset" in stderr
    # Must name the runnable command verbatim (Q11).
    assert "research_pipeline.py reset" in stderr


def test_approved_non_revoked_cycle_gate_exits_0(tmp_state):
    """Gate must exit 0 for an approved, non-revoked cycle (regression)."""
    sid = "gate2"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(
        caller_skill="/research", user_approved_scope=True,
        # S4: carry the artifact, so this fixture still produces an APPROVED
        # cycle. Without it the cycle is revoked-but-never-approved, which by
        # the resolver's own property 2 gets the GENERIC text rather than the
        # revoked text — a different shape than these tests mean to exercise.
        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state)
    proc = _run_gate(sid, state_dir=tmp_state)
    assert proc.returncode == 0


# ── S7: Q11 plain-English assertion ──────────────────────────────────────────

def test_q11_revoked_cycle_remediation_carries_no_internal_identifiers(tmp_state):
    """Remediation text emitted for a revoked cycle must not contain internal
    pipeline identifiers (Q11 — user-facing strings use plain English only)."""
    sid = "q11r"
    rp.cmd_advance(sid, "r0_intake", _intake_payload(
        caller_skill="/research", user_approved_scope=True,
        # S4: carry the artifact, so this fixture still produces an APPROVED
        # cycle. Without it the cycle is revoked-but-never-approved, which by
        # the resolver's own property 2 gets the GENERIC text rather than the
        # revoked text — a different shape than these tests mean to exercise.
        scope={"angles": ["a"], "focused_questions": ["q"]}),
        state_dir=tmp_state)
    rp.cmd_revoke_cycle(sid, state_dir=tmp_state)
    proc = _run_gate(sid, state_dir=tmp_state)
    stderr = proc.stderr
    for identifier in _Q11_FORBIDDEN:
        assert identifier not in stderr, (
            f"Q11 violation: internal identifier {identifier!r} found in "
            f"user-facing remediation text:\n{stderr}"
        )

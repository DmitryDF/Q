"""
Eval suite for research_pipeline.py — 8 cases.

e1: Full pipeline advance r0→r5 → gate exits 0 (complete)
e2: Missing r3_synthesis → gate exits 2, names r3_synthesis in stderr
e3: FC-skipped with valid reason → gate exits 0
e4: FC-skipped without reason → advance() raises ValueError
e5: No manifest (non-research session) → gate exits 0
e6: Advance only r0+r1 (r2..r5 missing) → gate exits 2, names r2_research
e7: reset() archives old state, new gate sees no manifest → exits 0
e8: PIPELINE_OVERRIDES subset (your-project omits r1_scope) → gate exits 0
"""
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
import research_pipeline as rp  # noqa: E402

# Minimal valid payloads for each checkpoint (satisfies required fields)
MINIMAL_PAYLOADS = {
    "r0_intake": {"research_file_path": "Thoughts/test_RESEARCH.md"},
    "r1_scope": {"search_scope": "test scope"},
    "r2_research": {"sources_count": 3},
    "r3_synthesis": {"claims_count": 5},
    "r4_factcheck": {},
    "r5_recommend": {},
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _run_gate(sid: str, state_dir: Path) -> subprocess.CompletedProcess:
    gate_sh = HOOKS_DIR / "check-research-pipeline-gate.sh"
    payload = json.dumps({"session_id": sid, "stop_hook_active": False})
    return subprocess.run(
        ["bash", str(gate_sh)],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, "RP_STATE_DIR": str(state_dir)},
    )


def _advance(sid: str, state_dir: Path, checkpoint: str, extra: Optional[dict] = None):
    payload = dict(MINIMAL_PAYLOADS.get(checkpoint, {}))
    if extra:
        payload.update(extra)
    rp.cmd_advance(sid, checkpoint, payload, state_dir=state_dir)


def _advance_range(sid, state_dir, checkpoints, topic_slug=None):
    for i, cp in enumerate(checkpoints):
        extra = {}
        if i == 0 and topic_slug:
            extra["topic_slug"] = topic_slug
        _advance(sid, state_dir, cp, extra)


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


# ── e1: full pipeline → gate exits 0 ─────────────────────────────────────────

def test_e1_full_pipeline_passes(tmp_state):
    sid = "e1sid"
    _advance_range(sid, tmp_state, rp.RESEARCH_SEQUENCE)
    result = _run_gate(sid, tmp_state)
    assert result.returncode == 0, f"e1 should pass; stderr={result.stderr}"


# ── e2: missing r3_synthesis → gate exits 2 ──────────────────────────────────

def test_e2_missing_synthesis_blocks(tmp_state):
    sid = "e2sid"
    _advance_range(sid, tmp_state, rp.RESEARCH_SEQUENCE[:3])  # r0, r1, r2 only
    result = _run_gate(sid, tmp_state)
    assert result.returncode == 2, f"e2 should block; returncode={result.returncode}"
    assert "r3_synthesis" in result.stderr, (
        f"e2 stderr must name r3_synthesis; got: {result.stderr}"
    )


# ── e3: FC-skip path REMOVED (Lever E) → advance raises even with a reason ───

def test_e3_fc_skip_removed_raises_even_with_reason(tmp_state):
    sid = "e3sid"
    _advance_range(sid, tmp_state, rp.RESEARCH_SEQUENCE[:4])  # r0..r3
    with pytest.raises((ValueError, SystemExit)):
        rp.cmd_advance(
            sid, "r4_factcheck",
            {"skipped": True, "skip_reason": "quick_answer"},
            state_dir=tmp_state,
        )


# ── e4: FC-skipped without reason → advance raises ───────────────────────────

def test_e4_fc_skipped_no_reason_raises(tmp_state):
    sid = "e4sid"
    _advance_range(sid, tmp_state, rp.RESEARCH_SEQUENCE[:4])  # r0..r3
    with pytest.raises((ValueError, SystemExit)):
        rp.cmd_advance(
            sid, "r4_factcheck",
            {"skipped": True},   # missing skip_reason
            state_dir=tmp_state,
        )


# ── e5: no manifest → gate exits 0 (non-research session) ────────────────────

def test_e5_no_manifest_passes(tmp_state):
    sid = "e5sid"
    result = _run_gate(sid, tmp_state)
    assert result.returncode == 0, (
        f"e5: no manifest must not block a non-research session; stderr={result.stderr}"
    )


# ── e6: only r0+r1 done → gate exits 2, names r2_research ────────────────────

def test_e6_multiple_missing_blocks(tmp_state):
    sid = "e6sid"
    _advance_range(sid, tmp_state, rp.RESEARCH_SEQUENCE[:2])  # r0, r1 only
    result = _run_gate(sid, tmp_state)
    assert result.returncode == 2, f"e6 should block; returncode={result.returncode}"
    assert "r2_research" in result.stderr, (
        f"e6 stderr must name r2_research; got: {result.stderr}"
    )


# ── e7: reset archives old state, gate sees no manifest → exits 0 ────────────

def test_e7_reset_archives_and_clears(tmp_state):
    sid = "e7sid"
    _advance_range(sid, tmp_state, rp.RESEARCH_SEQUENCE[:3])  # partial run
    rp.cmd_reset(sid, state_dir=tmp_state)
    result = _run_gate(sid, tmp_state)
    assert result.returncode == 0, (
        f"e7: after reset gate should see no manifest; stderr={result.stderr}"
    )
    archive_dir = tmp_state / "archive"
    archives = list(archive_dir.glob(f"RP-{sid}-*.json")) if archive_dir.exists() else []
    assert archives, "e7: reset must leave an archive file"


# ── e8: PIPELINE_OVERRIDES subset → gate exits 0 ─────────────────────────────

def test_e8_pipeline_overrides_subset_passes(tmp_state):
    sid = "e8sid"
    override_seq = rp.PIPELINE_OVERRIDES.get("your-project")
    assert override_seq is not None, "your-project must have a PIPELINE_OVERRIDES entry"
    assert "r1_scope" not in override_seq, "your-project override must omit r1_scope"
    _advance_range(sid, tmp_state, override_seq, topic_slug="your-project")
    result = _run_gate(sid, tmp_state)
    assert result.returncode == 0, (
        f"e8: declared override subset should pass; stderr={result.stderr}"
    )


# ── A1 binary gate: 3 schema-extension cases ─────────────────────────────────
#
# A1 (S1) gate per ~/.claude/plans/lazy-doodling-wadler.md: write old
# single-cycle manifest → read returns valid structure; spawn 2 cycles same
# session → both addressable independently; non-whitelisted caller_skill
# rejected at r0_intake.


def test_a1_legacy_manifest_migrates_on_read(tmp_state):
    """Old v1 manifest on disk reads as v2 with cycles[DEFAULT_CYCLE_ID]."""
    sid = "a1sid_migrate"
    # Write a v1.0-shaped manifest directly to disk (no schema_version,
    # no cycles, single-cycle top-level fields).
    legacy = {
        "session_id": sid,
        "topic_slug": "legacy_topic",
        "research_file_path": "Thoughts/legacy_RESEARCH.md",
        "skill_version": "1.0",
        "rules_version": "1.0",
        "checkpoints": {
            "r0_intake": {
                "completed_at": "2026-01-01T00:00:00+00:00",
                "data": {"research_file_path": "Thoughts/legacy_RESEARCH.md"},
            }
        },
        "bypass": False,
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    tmp_state.mkdir(parents=True, exist_ok=True)
    (tmp_state / f"RP-{sid}.json").write_text(json.dumps(legacy))

    state = rp._read_state(sid, state_dir=tmp_state)
    assert state is not None, "migration must not lose the manifest"
    assert state.get("schema_version") == rp.SCHEMA_VERSION, (
        f"migration must stamp schema_version={rp.SCHEMA_VERSION}"
    )
    cycles = state.get("cycles")
    assert isinstance(cycles, dict) and rp.DEFAULT_CYCLE_ID in cycles, (
        "migration must populate cycles[DEFAULT_CYCLE_ID]"
    )
    default = cycles[rp.DEFAULT_CYCLE_ID]
    assert default["topic_slug"] == "legacy_topic"
    assert default["research_file_path"] == "Thoughts/legacy_RESEARCH.md"
    assert "r0_intake" in default["checkpoints"]
    # Legacy top-level mirrors preserved (non-destructive).
    assert state.get("topic_slug") == "legacy_topic"
    assert state.get("checkpoints", {}).get("r0_intake") is not None
    # Migration is idempotent: a second call returns the same shape.
    state2 = rp._migrate_state(state)
    assert state2.get("schema_version") == rp.SCHEMA_VERSION
    assert state2.get("cycles", {}).get(rp.DEFAULT_CYCLE_ID) == default


def test_a1_two_cycles_addressable_independently(tmp_state):
    """Same session, two cycles, each tracks its own checkpoints + file path."""
    sid = "a1sid_multicycle"
    # Cycle c1: r0_intake only.
    rp.cmd_advance(
        sid,
        "r0_intake",
        {"research_file_path": "Thoughts/topic_c1_RESEARCH.md"},
        state_dir=tmp_state,
        cycle_id="c1",
    )
    # Cycle c2: r0_intake + r1_scope.
    rp.cmd_advance(
        sid,
        "r0_intake",
        {"research_file_path": "Thoughts/topic_c2_RESEARCH_DE.md"},
        state_dir=tmp_state,
        cycle_id="c2",
    )
    rp.cmd_advance(
        sid,
        "r1_scope",
        {"search_scope": "c2 scope"},
        state_dir=tmp_state,
        cycle_id="c2",
    )

    state = rp._read_state(sid, state_dir=tmp_state)
    cycles = state.get("cycles", {})
    assert {"c1", "c2"}.issubset(cycles.keys()), (
        "both cycles must be present in cycles dict"
    )
    c1 = cycles["c1"]
    c2 = cycles["c2"]
    # Independent research files.
    assert c1["research_file_path"] == "Thoughts/topic_c1_RESEARCH.md"
    assert c2["research_file_path"] == "Thoughts/topic_c2_RESEARCH_DE.md"
    # Independent checkpoint progression.
    assert set(c1["checkpoints"].keys()) == {"r0_intake"}
    assert set(c2["checkpoints"].keys()) == {"r0_intake", "r1_scope"}
    # No cross-cycle leak through the legacy mirror (cycle c1 wrote first
    # at the DEFAULT mirror; c2 lives only in cycles dict).
    assert "c1" not in state.get("checkpoints", {}), (
        "legacy mirror reflects DEFAULT cycle only — never cross-cycle"
    )


def test_a1_caller_skill_whitelist_enforced(tmp_state):
    """Whitelisted caller_skill auto-advances r1_scope_approved; rest reject."""
    # Non-whitelisted caller_skill → ValueError at r0_intake.
    sid_bad = "a1sid_bad_caller"
    with pytest.raises(ValueError) as exc:
        rp.cmd_advance(
            sid_bad,
            "r0_intake",
            {
                "research_file_path": "Thoughts/x_RESEARCH.md",
                "caller_skill": "/foo",
            },
            state_dir=tmp_state,
        )
    assert "caller_skill" in str(exc.value) and "/foo" in str(exc.value)

    # Whitelisted caller_skill → registration succeeds. Since
    # research-entry-point-enforcement S4 it does NOT by itself advance
    # r1_scope_approved: the name is necessary and never sufficient (A4,
    # channel 3). The cycle records the caller and says why it is unapproved.
    sid_ok = "a1sid_ok_caller"
    rp.cmd_advance(
        sid_ok,
        "r0_intake",
        {
            "research_file_path": "Thoughts/x_RESEARCH.md",
            "caller_skill": "/clarification",
            "caller_session_id": "parent-sid-1234",
            # /clarification is bound in CALLER_DOWNSTREAM_WHITELIST, so it must
            # declare its downstream tool (conditional-required).
            "downstream_tool": "~/.claude/skills/research/SKILL.md",
        },
        state_dir=tmp_state,
    )
    state = rp._read_state(sid_ok, state_dir=tmp_state)
    default = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert default["r1_scope_approved"] is False, (
        "a whitelisted caller_skill alone must NOT advance r1_scope_approved"
    )
    assert default["caller_skill"] == "/clarification"
    assert default["caller_session_id"] == "parent-sid-1234"

    # The same caller WITH an approval artifact does advance it.
    sid_art = "a1sid_ok_caller_artifact"
    rp.cmd_advance(
        sid_art,
        "r0_intake",
        {
            "research_file_path": "Thoughts/x_RESEARCH.md",
            "caller_skill": "/clarification",
            "caller_session_id": "parent-sid-1234",
            "downstream_tool": "~/.claude/skills/research/SKILL.md",
            "user_approved_scope": True,
            "scope": {"focused_questions": ["oq1"]},
            "scope_provenance": rp.APPROVAL_EXPLICIT_ARGUMENT,
            # research-entry-point-enforcement S4 MINOR 8: an explicit-argument
            # (or git-head) artifact must name the reference that makes it
            # re-checkable, or the resolver downgrades.
            "scope_source_ref": "--from Thoughts/x_THOUGHT.md",
        },
        state_dir=tmp_state,
    )
    art = rp._read_state(sid_art, state_dir=tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert art["r1_scope_approved"] is True
    assert art["approval_provenance"] == rp.APPROVAL_EXPLICIT_ARGUMENT

    # Absent caller_skill (direct AI invocation) → r1_scope_approved stays False.
    sid_direct = "a1sid_direct"
    rp.cmd_advance(
        sid_direct,
        "r0_intake",
        {"research_file_path": "Thoughts/x_RESEARCH.md"},
        state_dir=tmp_state,
    )
    state = rp._read_state(sid_direct, state_dir=tmp_state)
    default = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert default["r1_scope_approved"] is False, (
        "absent caller_skill must NOT auto-advance r1_scope_approved"
    )


def test_caller_downstream_whitelist_binding(tmp_state):
    """CALLER_DOWNSTREAM_WHITELIST: bound caller must declare an allowed tool;
    unbound callers unaffected (conditional-required / non-breaking)."""
    # C5 — single-locus table shape: a dict of caller -> frozenset of tool ids.
    assert isinstance(rp.CALLER_DOWNSTREAM_WHITELIST, dict)
    assert rp.CALLER_DOWNSTREAM_WHITELIST["/clarification"] == frozenset(
        {"~/.claude/skills/research/SKILL.md"}
    )
    # Every bound caller must also be allowed to register at all.
    assert set(rp.CALLER_DOWNSTREAM_WHITELIST).issubset(rp.CALLER_SKILL_WHITELIST)

    # C1 — bound caller declares its allowed tool → accepted (registration
    # succeeds and the cycle records the caller). Whether the cycle is APPROVED
    # is a separate question this test does not exercise: since S4 that needs an
    # approval artifact, and the binding table gates WHAT tool a caller declares,
    # not WHETHER its scope was approved.
    sid_ok = "dtw_ok"
    rp.cmd_advance(
        sid_ok,
        "r0_intake",
        {
            "research_file_path": "Thoughts/x_RESEARCH.md",
            "caller_skill": "/clarification",
            "downstream_tool": "~/.claude/skills/research/SKILL.md",
        },
        state_dir=tmp_state,
    )
    default = rp._read_state(sid_ok, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert default["caller_skill"] == "/clarification"
    assert default["r1_scope_approved"] is False

    # C2 — bound caller declares a NON-allowed tool → ValueError naming the pair.
    with pytest.raises(ValueError) as exc_wrong:
        rp.validate_schema(
            "r0_intake",
            {
                "research_file_path": "Thoughts/x_RESEARCH.md",
                "caller_skill": "/clarification",
                "downstream_tool": "/deep-research",
            },
        )
    msg = str(exc_wrong.value)
    assert "downstream_tool" in msg and "/clarification" in msg
    assert "/deep-research" in msg and "~/.claude/skills/research/SKILL.md" in msg

    # C3 — bound caller declares NO tool → ValueError (declaration mandatory).
    with pytest.raises(ValueError) as exc_absent:
        rp.validate_schema(
            "r0_intake",
            {
                "research_file_path": "Thoughts/x_RESEARCH.md",
                "caller_skill": "/clarification",
            },
        )
    assert "downstream_tool" in str(exc_absent.value)

    # C4 — unbound caller (not in CALLER_DOWNSTREAM_WHITELIST) is unaffected:
    # registers fine with no downstream_tool (non-breaking).
    sid_unbound = "dtw_unbound"
    rp.cmd_advance(
        sid_unbound,
        "r0_intake",
        {
            "research_file_path": "Thoughts/x_RESEARCH.md",
            "caller_skill": "/work-decode",
        },
        state_dir=tmp_state,
    )
    unbound = rp._read_state(sid_unbound, tmp_state)["cycles"][rp.DEFAULT_CYCLE_ID]
    assert unbound["caller_skill"] == "/work-decode"
    # Absent caller_skill entirely → no binding check (boundary preserved).
    rp.validate_schema(
        "r0_intake", {"research_file_path": "Thoughts/x_RESEARCH.md"}
    )

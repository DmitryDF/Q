"""
S8 smoke matrix for check-research-pipeline-gate.sh.

Six sub-tests per the S8 Validation Gate column in the plan:
  (i)   manifest path PASS    → RESEARCH-VERDICT: PASS + exit 0
  (ii)  manifest path DIRTY   → another-round prompt
  (iii) manifest path ESCALATE → abort + revoke + exit 2
  (iv)  no-manifest fallback, R*.md in-window   → surfaces verdict
  (v)   no-manifest fallback, R*.md out-of-window → silent exit
  (vi)  BYPASSED verdict        → surfaces bypass_reason text

Plus: internal-failure lines (49/67/76) still emit BLOCKED:, not RESEARCH-VERDICT:.
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

GATE_SCRIPT = HOOKS_DIR / "check-research-pipeline-gate.sh"


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_r_md(path: Path, verdict: str, bypass_reason: str = "") -> None:
    """Write a minimal R*.md marker with the given verdict."""
    lines = ["---", f"verdict: {verdict}", "rounds: 1", "checker_count: 3"]
    if verdict == "BYPASSED" and bypass_reason:
        lines.append(f'bypass_reason: "{bypass_reason}"')
    lines += ["---", "", "## Fact-check round 1", ""]
    if verdict not in ("PASS", "BYPASSED"):
        lines.append(f"DISCREPANCY: synthetic {verdict} discrepancy for smoke test")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_active(path: Path, sid: str, topic_slug: str, active_project: str) -> None:
    """Write a minimal _active.json with one session entry."""
    path.write_text(
        json.dumps({sid: {"topic_slug": topic_slug, "active_project": active_project}}),
        encoding="utf-8",
    )


def _run_gate(
    sid: str,
    *,
    state_dir=None,
    active_file=None,
    plan_val_dir=None,
    on_non_pass: str = None,
) -> subprocess.CompletedProcess:
    """Run check-research-pipeline-gate.sh as a subprocess."""
    env = os.environ.copy()
    if state_dir is not None:
        env["RP_STATE_DIR"] = str(state_dir)
    if active_file is not None:
        env["RP_ACTIVE_FILE"] = str(active_file)
    if plan_val_dir is not None:
        env["RP_PLAN_VAL_DIR"] = str(plan_val_dir)
    if on_non_pass is not None:
        env["CLAUDE_RESEARCH_ON_NON_PASS"] = on_non_pass
    else:
        env.pop("CLAUDE_RESEARCH_ON_NON_PASS", None)
    # Prevent remote-host early exit
    env.pop("CLAUDE_CODE_REMOTE", None)
    payload = json.dumps({"stop_hook_active": False, "session_id": sid})
    return subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


def _setup_complete_manifest(sid: str, state_dir: Path) -> None:
    """Advance through all 6 required checkpoints so check → complete=true."""
    rp.cmd_advance(sid, "r0_intake", {
        "research_file_path": "Thoughts/test_RESEARCH.md",
        "caller_skill": "/research",
        "user_approved_scope": True,
    }, state_dir=state_dir)
    rp.cmd_advance(sid, "r1_scope", {"search_scope": "test"}, state_dir=state_dir)
    rp.cmd_advance(sid, "r2_research", {"sources_count": 1}, state_dir=state_dir)
    rp.cmd_advance(sid, "r3_synthesis", {"claims_count": 1}, state_dir=state_dir)
    rp.cmd_advance(sid, "r4_factcheck", {}, state_dir=state_dir)
    rp.cmd_advance(sid, "r5_recommend", {}, state_dir=state_dir)


def _research_dir(plan_val: Path, proj: str, topic: str) -> Path:
    d = plan_val / proj / topic / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── (i) manifest PASS ────────────────────────────────────────────────────────

def test_manifest_pass_emits_research_verdict_pass_and_exits_0(tmp_path):
    """(i) Complete manifest + PASS R*.md → RESEARCH-VERDICT: PASS, exit 0."""
    sid = "s8i"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicA", "projA")
    _make_active(active, sid, "topicA", "projA")
    _make_r_md(rdir / "R1.md", "PASS")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 0, f"expected exit 0; got {proc.returncode}\nstderr: {proc.stderr}"
    assert "RESEARCH-VERDICT: PASS" in proc.stderr, proc.stderr


# ── (ii) manifest DIRTY → another-round ──────────────────────────────────────

def test_manifest_dirty_another_round_emits_verdict_and_exits_2(tmp_path):
    """(ii) DIRTY R*.md + ON_NON_PASS=another-round → RESEARCH-VERDICT: + exit 2."""
    sid = "s8ii"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicB", "projB")
    _make_active(active, sid, "topicB", "projB")
    _make_r_md(rdir / "R1.md", "DIRTY")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val, on_non_pass="another-round")
    assert proc.returncode == 2, proc.stderr
    assert "RESEARCH-VERDICT:" in proc.stderr, proc.stderr
    assert "another-round" in proc.stderr, proc.stderr
    # Must NOT say BLOCKED: on this verdict-dispatch line
    assert "BLOCKED:" not in proc.stderr, (
        "verdict-dispatch line must use RESEARCH-VERDICT:, not BLOCKED:"
    )


# ── (iii) manifest ESCALATE → abort + revoke ─────────────────────────────────

def test_manifest_escalate_abort_revokes_cycle_and_exits_2(tmp_path):
    """(iii) ESCALATE R*.md + ON_NON_PASS=abort → revoke cycle + exit 2."""
    sid = "s8iii"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicC", "projC")
    _make_active(active, sid, "topicC", "projC")
    _make_r_md(rdir / "R1.md", "ESCALATE")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val, on_non_pass="abort")
    assert proc.returncode == 2, proc.stderr
    assert "RESEARCH-VERDICT:" in proc.stderr, proc.stderr
    assert "abort" in proc.stderr.lower(), proc.stderr
    assert "BLOCKED:" not in proc.stderr, (
        "verdict-dispatch line must use RESEARCH-VERDICT:, not BLOCKED:"
    )
    # Verify cycle was revoked
    state = rp._read_state(sid, state_dir)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle["r1_scope_revoked"] is True, "abort branch must call cmd_revoke_cycle"


# ── (iv) no-manifest fallback, in-window ─────────────────────────────────────

def test_no_manifest_fallback_in_window_surfaces_verdict(tmp_path):
    """(iv) No manifest, recent R*.md → RESEARCH-VERDICT: surfaced, exit 0."""
    sid = "s8iv"
    # No manifest — don't create RP-SID.json
    state_dir = tmp_path / "rp"
    state_dir.mkdir()

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicD", "projD")
    _make_active(active, sid, "topicD", "projD")
    # File created now → within 360-minute window
    _make_r_md(rdir / "R1.md", "PASS")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 0, f"no-manifest fallback must exit 0; stderr: {proc.stderr}"
    assert "RESEARCH-VERDICT:" in proc.stderr, (
        f"no-manifest fallback must surface RESEARCH-VERDICT:; stderr: {proc.stderr}"
    )
    assert "PASS" in proc.stderr, proc.stderr


# ── (v) no-manifest fallback, out-of-window ──────────────────────────────────

def test_no_manifest_fallback_out_of_window_silent_exit(tmp_path):
    """(v) No manifest, old R*.md → mtime predicate rejects it, silent exit 0."""
    sid = "s8v"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicE", "projE")
    _make_active(active, sid, "topicE", "projE")
    r_md = rdir / "R1.md"
    _make_r_md(r_md, "PASS")
    # Set mtime far in the past (way outside the 6-hour / 360-minute window)
    subprocess.run(["touch", "-t", "202501010000", str(r_md)], check=True)

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 0, proc.stderr
    assert "RESEARCH-VERDICT:" not in proc.stderr, (
        f"out-of-window R*.md must produce silent exit; stderr: {proc.stderr}"
    )


# ── (vi) BYPASSED verdict surfaces bypass_reason ─────────────────────────────

def test_bypassed_verdict_surfaces_bypass_reason_in_manifest_path(tmp_path):
    """(vi) BYPASSED R*.md with non-empty bypass_reason → reason echoed, exit 0."""
    sid = "s8vi"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicF", "projF")
    _make_active(active, sid, "topicF", "projF")
    _make_r_md(rdir / "R1.md", "BYPASSED",
               bypass_reason="all 3 checkers timed out 2026-06-11")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 0, proc.stderr
    assert "all 3 checkers timed out" in proc.stderr, (
        f"bypass_reason must be surfaced; stderr: {proc.stderr}"
    )


# ── (vi-b) BYPASSED in no-manifest fallback ──────────────────────────────────

def test_bypassed_verdict_in_no_manifest_fallback_surfaces_reason(tmp_path):
    """(vi-b) BYPASSED in no-manifest (Internal KB) path → reason echoed, exit 0."""
    sid = "s8vib"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    # No manifest

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicG", "projG")
    _make_active(active, sid, "topicG", "projG")
    _make_r_md(rdir / "R1.md", "BYPASSED",
               bypass_reason="engine unavailable during smoke test")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 0, proc.stderr
    assert "engine unavailable" in proc.stderr, (
        f"no-manifest BYPASSED must surface bypass_reason; stderr: {proc.stderr}"
    )


# ── internal-failure lines still emit BLOCKED: ───────────────────────────────

def test_internal_failure_empty_bypass_reason_emits_blocked_not_research_verdict(tmp_path):
    """Lines 49: bypass=true + empty bypass_reason → BLOCKED:, NOT RESEARCH-VERDICT:."""
    sid = "s8blk"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    # Create a minimal manifest then corrupt it: bypass=true, bypass_reason=""
    rp.cmd_advance(sid, "r0_intake", {"research_file_path": "x.md"},
                   state_dir=state_dir)
    state_file = state_dir / f"RP-{sid}.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["bypass"] = True
    state["bypass_reason"] = ""   # empty → must BLOCK
    state_file.write_text(json.dumps(state), encoding="utf-8")

    proc = _run_gate(sid, state_dir=state_dir)
    assert proc.returncode == 2, proc.stderr
    assert "BLOCKED:" in proc.stderr, (
        "internal-failure line 49 must emit BLOCKED:, not RESEARCH-VERDICT:"
    )
    assert "RESEARCH-VERDICT:" not in proc.stderr, proc.stderr


def test_internal_failure_pipeline_incomplete_emits_blocked(tmp_path):
    """Line 76: incomplete pipeline → BLOCKED: Research pipeline incomplete."""
    sid = "s8inc"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    # Only r0_intake done → pipeline incomplete (5 checkpoints missing)
    rp.cmd_advance(sid, "r0_intake", {"research_file_path": "x.md"},
                   state_dir=state_dir)

    proc = _run_gate(sid, state_dir=state_dir)
    assert proc.returncode == 2, proc.stderr
    assert "BLOCKED:" in proc.stderr, proc.stderr
    assert "incomplete" in proc.stderr.lower(), proc.stderr
    assert "RESEARCH-VERDICT:" not in proc.stderr, proc.stderr


# ── Lever B: complete manifest + no R-marker (the closed no-marker hole) ─────

def test_lever_b_no_marker_blocks_when_r4_in_sequence(tmp_path):
    """Complete manifest, r4 in sequence, research dir but NO R*.md → block
    (the previously-green no-marker hole is now non-PASS)."""
    sid = "s8lb"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)  # default sequence → r4 in

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    _research_dir(plan_val, "topicLB", "projLB")  # dir exists, NO R*.md
    _make_active(active, sid, "topicLB", "projLB")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 2, f"no-marker + r4-in-seq must block; stderr: {proc.stderr}"
    assert "fact-check not run" in proc.stderr, proc.stderr


def test_lever_b_cv_no_marker_still_passes(tmp_path):
    """CV topic excludes r4_factcheck → a missing R-marker must NOT block
    (preservation: CV legitimately skips external FC)."""
    sid = "s8cv"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    # CV's expected sequence: r0, r1, r2, r5 (no r3, no r4).
    rp.cmd_advance(sid, "r0_intake",
                   {"research_file_path": "x.md", "topic_slug": "CV"},
                   state_dir=state_dir)
    rp.cmd_advance(sid, "r1_scope", {"search_scope": "t"}, state_dir=state_dir)
    rp.cmd_advance(sid, "r2_research", {"sources_count": 1}, state_dir=state_dir)
    rp.cmd_advance(sid, "r5_recommend", {}, state_dir=state_dir)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    _research_dir(plan_val, "CV", "projCV")  # dir exists, no marker
    _make_active(active, sid, "CV", "projCV")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val)
    assert proc.returncode == 0, f"CV no-marker must pass; stderr: {proc.stderr}"
    assert "RESEARCH-VERDICT: PASS" in proc.stderr, proc.stderr


# ── accepted branch uses dispatch-non-pass (not bypass) ──────────────────────

def test_accepted_branch_calls_record_non_pass_not_bypass(tmp_path):
    """accepted branch must set non_pass_verdict on cycle, NOT set bypass=true."""
    sid = "s8acc"
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)

    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = _research_dir(plan_val, "topicH", "projH")
    _make_active(active, sid, "topicH", "projH")
    _make_r_md(rdir / "R1.md", "DIRTY")

    proc = _run_gate(sid, state_dir=state_dir, active_file=active,
                     plan_val_dir=plan_val, on_non_pass="accepted")
    assert proc.returncode == 0, proc.stderr
    assert "RESEARCH-VERDICT:" in proc.stderr, proc.stderr
    # non_pass_verdict must be recorded on the cycle
    state = rp._read_state(sid, state_dir)
    cycle = state["cycles"][rp.DEFAULT_CYCLE_ID]
    assert cycle.get("non_pass_verdict") is not None, (
        "accepted branch must call record-non-pass (set non_pass_verdict)"
    )
    # bypass must NOT be set at state level
    assert state.get("bypass") is not True, (
        "accepted branch must NOT set bypass=true (record-non-pass, not bypass)"
    )

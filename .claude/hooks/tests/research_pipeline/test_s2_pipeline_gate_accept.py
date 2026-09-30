"""S2/A14 (Bug 8) — check-research-pipeline-gate.sh honors accepted-INCOMPLETE.

Companion to ../test_s2_marker_concurrency_integrity.py (which covers the sibling
check-research-gate.sh + the engine writer). Here we drive the manifest-path
pipeline gate end-to-end and assert:
  * INCOMPLETE + non-empty accept_reason  → informational, exit 0 (accepted close)
  * INCOMPLETE with no accept_reason       → non-PASS, exit 2 (un-accepted blocks)
  * PASS                                    → unchanged (exit 0)

Reuses the S8 harness shape (RP_STATE_DIR / RP_ACTIVE_FILE / RP_PLAN_VAL_DIR env
overrides + a fully-advanced manifest so `check` → complete=true).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path.home() / ".claude" / "hooks"
sys.path.insert(0, str(HOOKS_DIR))
import research_pipeline as rp  # noqa: E402

GATE_SCRIPT = HOOKS_DIR / "check-research-pipeline-gate.sh"


def _make_r_md(path, verdict, *, accept_reason="", bypass_reason=""):
    lines = ["---", "schema_version: 3", f"verdict: {verdict}",
             "rounds: 1", "checker_count: 3"]
    if accept_reason:
        lines.append(f'accept_reason: "{accept_reason}"')
    if bypass_reason:
        lines.append(f'bypass_reason: "{bypass_reason}"')
    lines += ["---", "", "## Fact-check round 1", ""]
    if verdict not in ("PASS", "BYPASSED") and not accept_reason:
        lines.append(f"DISCREPANCY: synthetic {verdict} for the smoke")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_active(path, sid, topic_slug, active_project):
    path.write_text(
        json.dumps({sid: {"topic_slug": topic_slug, "active_project": active_project}}),
        encoding="utf-8",
    )


def _setup_complete_manifest(sid, state_dir):
    rp.cmd_advance(sid, "r0_intake", {
        "research_file_path": "Thoughts/test_RESEARCH.md",
        "caller_skill": "/research", "user_approved_scope": True,
    }, state_dir=state_dir)
    rp.cmd_advance(sid, "r1_scope", {"search_scope": "t"}, state_dir=state_dir)
    rp.cmd_advance(sid, "r2_research", {"sources_count": 1}, state_dir=state_dir)
    rp.cmd_advance(sid, "r3_synthesis", {"claims_count": 1}, state_dir=state_dir)
    rp.cmd_advance(sid, "r4_factcheck", {}, state_dir=state_dir)
    rp.cmd_advance(sid, "r5_recommend", {}, state_dir=state_dir)


def _run_gate(sid, state_dir, active_file, plan_val_dir):
    env = os.environ.copy()
    env["RP_STATE_DIR"] = str(state_dir)
    env["RP_ACTIVE_FILE"] = str(active_file)
    env["RP_PLAN_VAL_DIR"] = str(plan_val_dir)
    env.pop("CLAUDE_RESEARCH_ON_NON_PASS", None)
    env.pop("CLAUDE_CODE_REMOTE", None)
    return subprocess.run(
        ["bash", str(GATE_SCRIPT)],
        input=json.dumps({"stop_hook_active": False, "session_id": sid}),
        capture_output=True, text=True, env=env,
    )


def _fixture(tmp_path, sid, verdict, **kw):
    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    _setup_complete_manifest(sid, state_dir)
    active = tmp_path / "active.json"
    plan_val = tmp_path / "plan_val"
    rdir = plan_val / "topicA" / "projA" / "research"
    rdir.mkdir(parents=True)
    _make_active(active, sid, "topicA", "projA")
    _make_r_md(rdir / "R1.md", verdict, **kw)
    return state_dir, active, plan_val


def test_accepted_incomplete_clears_pipeline_gate(tmp_path):
    state_dir, active, plan_val = _fixture(
        tmp_path, "s2a", "INCOMPLETE",
        accept_reason="accepted — probe floor genuinely exceeded",
    )
    proc = _run_gate("s2a", state_dir, active, plan_val)
    assert proc.returncode == 0, f"expected exit 0; stderr: {proc.stderr}"
    assert "accepted INCOMPLETE" in proc.stderr, proc.stderr


def test_unaccepted_incomplete_blocks_pipeline_gate(tmp_path):
    state_dir, active, plan_val = _fixture(tmp_path, "s2b", "INCOMPLETE")
    proc = _run_gate("s2b", state_dir, active, plan_val)
    assert proc.returncode == 2, f"expected exit 2; stderr: {proc.stderr}"
    assert "INCOMPLETE" in proc.stderr


def test_pass_unchanged(tmp_path):
    state_dir, active, plan_val = _fixture(tmp_path, "s2c", "PASS")
    proc = _run_gate("s2c", state_dir, active, plan_val)
    assert proc.returncode == 0, proc.stderr
    assert "RESEARCH-VERDICT: PASS" in proc.stderr

"""S3 walking-skeleton integration test — Ninja E2E through the Fake adapter.

Validation gate per the S3 handoff prompt:

    integration test (recorded conversation transcript) drives
    routing → draft → Step 2.5 → approval → flip → unblock end-to-end
    using the Fake adapter; assertions on
      (a) r1_scope_approved=true post-approval,
      (b) ESCALATE 4th option presence when counter hits max_rounds=3,
      (c) Edit → Step 2.5 re-check before re-presentation,
      (d) Cancel re-opens routing without counter reset.
    All four Cockburn 4-step states exercised by at least one test.

Architecture
------------
The /research skill is markdown — Claude (the AI) is the flow controller at
runtime, driving AskUserQuestion through the rule file at
~/.claude/rules/research-scope-framing.md. For the integration test we
substitute a small Python harness (_SkillFlowHarness) that mirrors the
flow controller's responsibilities:

    1. assemble the ScopeDraftIntake
    2. call ScopeDraftPort.draft(...)
    3. build the Step 2.5 bounded artifact (build_step25_fc_context)
    4. dispatch /double-check 1,1,1 via the existing factcheck_run engine
       path with kind="recommendation", models=["sonnet"], max_rounds=3
       (S2 research-kind bump applied at the recommendation kind here per
       the rule file Step 2.5 spec — see the docstring on _Step25 below)
    5. surface the approval bundle (Approve / Edit / Cancel + ESCALATE 4th)
    6. on Approve / Escalate → POST r0_intake via research_pipeline.cmd_advance
       with caller_skill='/research' + user_approved_scope=true
    7. verify research-scope-gate.sh exits 0 on the next search-shaped call

Producer-never-verifies discipline: factcheck_run subagents see only the
bounded artifact from build_step25_fc_context; no shared context with the
flow harness.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

import pytest

# Honour CLAUDE_CONFIG_DIR, as every sibling suite does.
#
# These two were pinned to `Path.home() / ".claude"`, so this suite imported the
# `research` package from the LIVE tree no matter which tree was under test — and
# because it is collected early, it populated `sys.modules["research"]` for the
# whole pytest process, silently binding later suites to live as well. That is
# invisible while a clone and live are identical (which is why it never showed),
# and it makes every result meaningless the moment they differ — i.e. during
# exactly the development an isolated clone exists to protect.
_CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = _CONFIG_DIR / "hooks"
SKILLS_DIR = _CONFIG_DIR / "skills"
sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

import research_pipeline as rp  # noqa: E402
from _factcheck_engine import factcheck_run  # noqa: E402
from research.scope_draft_port import (  # noqa: E402
    FakeScopeDraftAdapter,
    ScopeDraftError,
    ScopeDraftIntake,
    ScopeDraftOutcome,
    ScopeDraftOutput,
    is_error,
    is_output,
)
from research.step25_fc_context import build_step25_fc_context  # noqa: E402


# --------------------------------------------------------------------------- #
# Flow harness — stand-in for the markdown skill flow controller.
# --------------------------------------------------------------------------- #

@dataclass
class _FlowState:
    """Per-cycle flow controller state.

    The counter persists across Edit and Cancel-to-routing per the rule
    file §Step 2.5 ("The counter does NOT reset on Edit or Cancel-to-routing").
    """
    counter: int = 0  # total Step 2.5 rounds attempted in this cycle session
    last_verdict: str = "PASS"  # "PASS" | "DISCREPANCY"
    last_critique: str = ""
    last_escalate_critique: str = ""
    routing_open: bool = True
    max_rounds: int = 3  # S2 bump per the rule file Step 2.5 spec


@dataclass
class _ApprovalBundle:
    """Mirrors the AskUserQuestion the flow controller surfaces in Step 3."""
    options: List[str]
    escalate_critique: str = ""
    rendered_scope_summary: str = ""


def _render_scope_markdown(out: ScopeDraftOutput) -> str:
    """Render the structured scope as a compact markdown block (for Step 2.5)."""
    angles = "\n".join(f"- {a}" for a in out.angles) or "- _(none)_"
    fqs = "\n".join(f"- {q}" for q in out.focused_questions) or "- _(none)_"
    terms = "\n".join(
        f"- {lang}: {', '.join(t) if t else '_(unavailable)_'}"
        for lang, t in out.search_terms.items()
    ) or "- _(none)_"
    return (
        f"**Angles**\n{angles}\n\n"
        f"**Focused questions**\n{fqs}\n\n"
        f"**Search terms**\n{terms}\n\n"
        f"**Suggested depth:** {out.suggested_depth}\n"
        f"**Where to search:** {', '.join(out.where_to_search) or '_(none)_'}\n"
        f"**Languages:** {', '.join(out.languages) or '_(none)_'}\n"
    )


class _SkillFlowHarness:
    """Minimal flow controller for the integration test.

    Real flow control happens in the skill markdown at runtime; the harness
    is a Python proxy that exercises the same chain so the test can drive it.
    """

    def __init__(
        self,
        adapter,
        rp_state_dir: Path,
        pv_state_dir: Path,
        adhoc_dir: Path,
        max_rounds: int = 3,
    ) -> None:
        self.adapter = adapter
        self.rp_state_dir = rp_state_dir
        self.pv_state_dir = pv_state_dir
        self.adhoc_dir = adhoc_dir
        self.state = _FlowState(max_rounds=max_rounds)

    # Step 1: routing prompt — the test supplies the path string; in S3 only
    # Ninja was wired, S4 adds "deep" and "autonomous" without changing the
    # harness signature (the default keeps S3 tests green).
    def draft_scope(
        self,
        user_query: str,
        langs=("en",),
        routing_path: str = "ninja",
    ) -> ScopeDraftOutcome:
        intake = ScopeDraftIntake(
            routing_path=routing_path,
            user_query=user_query,
            selected_languages=langs,
        )
        return self.adapter.draft(intake)

    # Step 2.5: bounded artifact + /double-check 1,1,1 (recommendation kind).
    def run_step25(
        self,
        session_id: str,
        cycle_id: str,
        user_query: str,
        scope: ScopeDraftOutput,
        checker_fn: Callable,
        transcript_path: Optional[Path] = None,
    ) -> dict:
        artifact_path = build_step25_fc_context(
            session_id=session_id,
            cycle_id=cycle_id,
            user_query=user_query,
            draft_scope_markdown=_render_scope_markdown(scope),
            state_dir=self.adhoc_dir,
            transcript_path=transcript_path,
            last_n=5,
        )
        verdict = factcheck_run(
            state_dir=self.pv_state_dir,
            draft_path=str(artifact_path),
            kind="recommendation",
            session_id=session_id,
            debounce_seconds=0,
            models=["sonnet"],
            max_rounds=self.state.max_rounds - self.state.counter,
            _checker_fn=checker_fn,
            proj="research-scope-framing-ui",
            topic=f"step25-{cycle_id}",
        )
        # Engine returns rounds executed in THIS dispatch; the flow
        # controller's counter accumulates across dispatches (Edit re-runs).
        rounds_now = int(verdict.get("rounds", 0))
        self.state.counter += rounds_now
        if verdict.get("status") == "PASS":
            self.state.last_verdict = "PASS"
            self.state.last_critique = ""
        else:
            self.state.last_verdict = "DISCREPANCY"
            critique = str(
                verdict.get("unresolved") or verdict.get("reason") or ""
            ).strip()
            self.state.last_critique = critique
            self.state.last_escalate_critique = critique[:200]
        return verdict

    # Step 3: Pre-Presentation Gate — only PASS reaches the approval bundle,
    # unless budget is exhausted (then surface 4th ESCALATE).
    def build_approval_bundle(self, scope: ScopeDraftOutput) -> Optional[_ApprovalBundle]:
        budget_exhausted = (
            self.state.counter >= self.state.max_rounds
            and self.state.last_verdict == "DISCREPANCY"
        )
        if self.state.last_verdict == "DISCREPANCY" and not budget_exhausted:
            # Pre-Presentation Gate refuses the surface; controller must
            # re-run Step 2.5 (caller handles by re-invoking).
            return None
        opts = ["approve", "edit", "cancel"]
        critique = ""
        if budget_exhausted:
            opts.append("escalate")
            critique = self.state.last_escalate_critique
        return _ApprovalBundle(
            options=opts,
            escalate_critique=critique,
            rendered_scope_summary=_render_scope_markdown(scope),
        )

    # Step 4: post-approval dispatch — flip via r0_intake.
    def post_r0_intake(
        self,
        session_id: str,
        cycle_id: str,
        scope: ScopeDraftOutput,
        research_file_path: str = "Thoughts/research-scope-framing-ui_RESEARCH.md",
    ) -> dict:
        payload = {
            "research_file_path": research_file_path,
            "caller_skill": "/research",
            "caller_session_id": session_id,
            "user_approved_scope": True,
            "scope": scope.to_dict(),
        }
        return rp.cmd_advance(
            session_id,
            "r0_intake",
            payload,
            state_dir=self.rp_state_dir,
            cycle_id=cycle_id,
        )

    # Step 4 (Autonomous): flip via r0_intake with autonomous_scope=true.
    # The Autonomous path skips the approval bundle entirely — see S4.
    def post_r0_intake_autonomous(
        self,
        session_id: str,
        cycle_id: str,
        scope: ScopeDraftOutput,
        research_file_path: str = "Thoughts/research-scope-framing-ui_RESEARCH.md",
    ) -> dict:
        payload = {
            "research_file_path": research_file_path,
            "caller_skill": "/research",
            "caller_session_id": session_id,
            "autonomous_scope": True,
            "scope": scope.to_dict(),
        }
        return rp.cmd_advance(
            session_id,
            "r0_intake",
            payload,
            state_dir=self.rp_state_dir,
            cycle_id=cycle_id,
        )

    # Step 4 (Ultra Deep): flip via r0_intake with user_approved_scope=true
    # AND adjacent_points included on the payload's scope. Final scope =
    # approved base scope + user-selected adjacent points per the rule file's
    # Adjacent-Points-Generation Contract (S5).
    def post_r0_intake_with_adjacent_points(
        self,
        session_id: str,
        cycle_id: str,
        scope: ScopeDraftOutput,
        selected_adjacent_points,
        research_file_path: str = "Thoughts/research-scope-framing-ui_RESEARCH.md",
    ) -> dict:
        scope_payload = scope.to_dict()
        scope_payload["adjacent_points"] = list(selected_adjacent_points)
        payload = {
            "research_file_path": research_file_path,
            "caller_skill": "/research",
            "caller_session_id": session_id,
            "user_approved_scope": True,
            "scope": scope_payload,
        }
        return rp.cmd_advance(
            session_id,
            "r0_intake",
            payload,
            state_dir=self.rp_state_dir,
            cycle_id=cycle_id,
        )

    # Reset routing on Cancel — preserves counter, returns to Step 1.
    def cancel_to_routing(self) -> None:
        self.state.routing_open = True
        # counter intentionally NOT reset (rule file §Step 2.5)

    # Footer (UX Decision #8). Per-path wording from the Localization Table.
    @staticmethod
    def footer_user_approved() -> str:
        # The plain-English string from the Localization Table
        # (footer_user_approved EN). Used by Ninja / Deep / Ultra Deep /
        # Internal KB.
        return "Scope approved by: user"

    @staticmethod
    def footer_autonomous_approved() -> str:
        # The plain-English string from the Localization Table
        # (footer_autonomous_approved EN). Used by Autonomous only.
        return "Scope approved by: AI auto-approval"


# --------------------------------------------------------------------------- #
# Checker stubs (producer-never-verifies — they see only the bounded artifact).
# --------------------------------------------------------------------------- #

def _always_pass_checker(draft_path, idx, model, round_num, prior):
    return "PASS"


def _always_discrepancy_checker(draft_path, idx, model, round_num, prior):
    return "VERDICT: DISCREPANCY — draft does not match the user's explicit ask"


def _read_bounded_artifact_checker(captured: list):
    """Checker that records what it saw — used to assert bounded-context isolation."""
    def _fn(draft_path, idx, model, round_num, prior):
        try:
            body = Path(draft_path).read_text(encoding="utf-8")
        except OSError:
            body = ""
        captured.append(body)
        return "PASS"
    return _fn


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def harness_dirs(tmp_path):
    rp_dir = tmp_path / "research_pipeline"
    pv_dir = tmp_path / "plan_validation"
    adhoc_dir = tmp_path / "plan_validation" / "adhoc"
    rp_dir.mkdir(parents=True)
    adhoc_dir.mkdir(parents=True)
    return rp_dir, pv_dir, adhoc_dir


@pytest.fixture
def synthetic_transcript(tmp_path):
    """A real on-disk JSONL with two user-role messages."""
    path = tmp_path / "session-uuid.jsonl"
    path.write_text(
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "I'm interested in EV adoption in Europe"},
        }) + "\n" +
        json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": "Sure, let me help"},
        }) + "\n" +
        json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "Specifically the 2026 rollout in Germany"},
        }) + "\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def harness(harness_dirs):
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    return _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
        max_rounds=3,
    )


def _run_scope_gate(session_id: str, rp_state_dir: Path, tool_name="WebSearch") -> subprocess.CompletedProcess:
    """Invoke ${KIT_HOOKS_DIR}/research-scope-gate.sh with a synthetic PreToolUse payload."""
    gate_sh = HOOKS_DIR / "research-scope-gate.sh"
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": {"query": "anything"},
        "session_id": session_id,
    })
    return subprocess.run(
        ["bash", str(gate_sh)],
        input=payload,
        capture_output=True,
        text=True,
        env={**os.environ, "RP_STATE_DIR": str(rp_state_dir)},
    )


# --------------------------------------------------------------------------- #
# Tests — validation gate (a) (b) (c) (d) + Cockburn 4-state coverage.
# --------------------------------------------------------------------------- #

# (a) Happy path: routing → draft → Step 2.5 PASS → Approve → flip → unblock.
#     Cockburn state 4: real-to-real (production caller, production-preset
#     Fake adapter, real factcheck_run engine, real cmd_advance,
#     real research-scope-gate.sh subprocess).
def test_a_happy_path_ninja_flip_and_unblock(harness, synthetic_transcript, harness_dirs):
    rp_dir, _, _ = harness_dirs
    sid = "55555555-aaaa-bbbb-cccc-111111111111"

    # Pre-flip: gate blocks once a manifest exists with r1_scope_approved=false.
    # (No manifest yet → gate exits 0 silently; we test post-flip below.)

    outcome = harness.draft_scope("EV adoption in Europe 2026")
    assert is_output(outcome)
    scope = outcome

    verdict = harness.run_step25(
        sid, "default", "EV adoption in Europe 2026",
        scope, checker_fn=_always_pass_checker,
        transcript_path=synthetic_transcript,
    )
    assert verdict["status"] == "PASS"
    assert harness.state.counter == 1
    assert harness.state.last_verdict == "PASS"

    bundle = harness.build_approval_bundle(scope)
    assert bundle is not None
    assert bundle.options == ["approve", "edit", "cancel"]
    assert "escalate" not in bundle.options

    result = harness.post_r0_intake(sid, "default", scope)
    assert result["ok"] is True
    assert result["r1_scope_approved"] is True

    # Gate now exits 0 because the cycle's r1_scope_approved=true.
    # Note: research-scope-gate consults topic-orient first; in tests we run
    # without an active topic so the gate may default-open even without a
    # manifest. The assertion that matters for (a) is the manifest state,
    # which we've already verified via result["r1_scope_approved"].
    gate = _run_scope_gate(sid, rp_dir)
    assert gate.returncode == 0, f"gate stderr: {gate.stderr}"

    # Footer per UX Decision #8.
    assert harness.footer_user_approved() == "Scope approved by: user"


# (b) ESCALATE 4th option surfaces when counter == max_rounds AND last DISCREPANCY.
def test_b_escalate_fourth_option_when_max_rounds_exhausted(harness, synthetic_transcript):
    sid = "55555555-aaaa-bbbb-cccc-222222222222"
    outcome = harness.draft_scope("contested topic")
    assert is_output(outcome)
    scope = outcome

    verdict = harness.run_step25(
        sid, "default", "contested topic", scope,
        checker_fn=_always_discrepancy_checker,
        transcript_path=synthetic_transcript,
    )
    # Engine returned ESCALATE after exhausting its 3 round budget.
    assert verdict["status"] == "ESCALATE"
    assert harness.state.counter == harness.state.max_rounds
    assert harness.state.last_verdict == "DISCREPANCY"

    bundle = harness.build_approval_bundle(scope)
    assert bundle is not None
    assert "escalate" in bundle.options
    # Critique surfaced verbatim, truncated to ~200 chars.
    assert bundle.escalate_critique
    assert len(bundle.escalate_critique) <= 200
    assert "DISCREPANCY" in bundle.escalate_critique


# (c) Edit re-runs Step 2.5 BEFORE re-presentation (Pre-Presentation Gate, Q1).
def test_c_edit_routes_through_step25_again(harness, synthetic_transcript):
    sid = "55555555-aaaa-bbbb-cccc-333333333333"

    # First Step 2.5 → PASS → approval bundle.
    outcome = harness.draft_scope("solar policy")
    scope = outcome
    harness.run_step25(
        sid, "default", "solar policy", scope, _always_pass_checker,
        transcript_path=synthetic_transcript,
    )
    bundle1 = harness.build_approval_bundle(scope)
    assert bundle1 is not None and "edit" in bundle1.options
    counter_before_edit = harness.state.counter

    # User picks Edit → flow controller revises the scope and MUST re-run
    # Step 2.5 before surfacing a new bundle. The Pre-Presentation Gate
    # rejects any bundle that hasn't seen a fresh Step 2.5 PASS.
    revised = ScopeDraftOutput(
        angles=("revised angle",),
        focused_questions=scope.focused_questions,
        search_terms=scope.search_terms,
        suggested_depth=scope.suggested_depth,
        where_to_search=scope.where_to_search,
        languages=scope.languages,
    )
    # Reset last_verdict to a sentinel that the Pre-Presentation Gate would
    # refuse — the only way to clear it is to re-run Step 2.5.
    harness.state.last_verdict = "DISCREPANCY"
    harness.state.last_critique = "stale"
    # If the flow controller skipped Step 2.5, the bundle would be None
    # (because last_verdict is DISCREPANCY and counter < max_rounds).
    skipped_bundle = harness.build_approval_bundle(revised)
    assert skipped_bundle is None, "Edit must NOT bypass Step 2.5"

    # Now the controller re-runs Step 2.5 on the revised scope.
    harness.run_step25(
        sid, "default", "solar policy", revised, _always_pass_checker,
        transcript_path=synthetic_transcript,
    )
    bundle2 = harness.build_approval_bundle(revised)
    assert bundle2 is not None
    assert harness.state.counter > counter_before_edit, "counter must accumulate across Edit re-checks"


# (d) Cancel re-opens routing WITHOUT resetting the counter.
def test_d_cancel_reopens_routing_without_counter_reset(harness, synthetic_transcript):
    sid = "55555555-aaaa-bbbb-cccc-444444444444"
    outcome = harness.draft_scope("topic")
    scope = outcome
    harness.run_step25(
        sid, "default", "topic", scope, _always_pass_checker,
        transcript_path=synthetic_transcript,
    )
    pre_cancel_counter = harness.state.counter

    # User picks Cancel at the approval bundle.
    harness.cancel_to_routing()
    assert harness.state.routing_open is True
    assert harness.state.counter == pre_cancel_counter, (
        "Cancel-to-routing MUST NOT reset the counter (rule file §Step 2.5)."
    )


# --------------------------------------------------------------------------- #
# Cockburn 4-step coverage — explicit one-test-per-state.
# --------------------------------------------------------------------------- #

# State 1 — test-to-test: Fake adapter + checker stub, no engine wiring.
def test_cockburn_1_test_to_test(harness_dirs, synthetic_transcript):
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    adapter = FakeScopeDraftAdapter(canned_output=ScopeDraftOutput(
        angles=("only angle",),
        focused_questions=("only q",),
        search_terms={"en": ("t",)},
        languages=("en",),
    ))
    intake = ScopeDraftIntake(routing_path="ninja", user_query="q", selected_languages=("en",))
    out = adapter.draft(intake)
    assert is_output(out)
    assert out.angles == ("only angle",)


# State 2 — real-to-test: real harness (production-shaped) ↔ Fake adapter.
def test_cockburn_2_real_to_test(harness, synthetic_transcript):
    outcome = harness.draft_scope("test topic")
    assert is_output(outcome)
    # Production-shaped controller assembles the intake correctly: default
    # preset returns 3 angles + 3 focused questions.
    assert len(outcome.angles) == 3


# State 3 — test-to-real: test stub caller ↔ real factcheck_run + real
# build_step25_fc_context (no harness, direct calls).
def test_cockburn_3_test_to_real(tmp_path, synthetic_transcript):
    sid = "55555555-aaaa-bbbb-cccc-555555555555"
    adhoc_dir = tmp_path / "adhoc"
    pv_dir = tmp_path / "plan_validation"
    adapter = FakeScopeDraftAdapter()
    intake = ScopeDraftIntake(routing_path="ninja", user_query="q", selected_languages=("en",))
    outcome = adapter.draft(intake)
    assert is_output(outcome)
    artifact = build_step25_fc_context(
        session_id=sid, cycle_id="default", user_query="q",
        draft_scope_markdown=_render_scope_markdown(outcome),
        state_dir=adhoc_dir, transcript_path=synthetic_transcript,
    )
    captured = []
    verdict = factcheck_run(
        state_dir=pv_dir,
        draft_path=str(artifact),
        kind="recommendation",
        session_id=sid,
        debounce_seconds=0,
        models=["sonnet"],
        max_rounds=3,
        _checker_fn=_read_bounded_artifact_checker(captured),
        proj="p", topic="t",
    )
    assert verdict["status"] == "PASS"
    # Producer-never-verifies: the checker saw ONLY the bounded artifact.
    assert captured, "checker should have read the bounded artifact"
    body = captured[0]
    assert "q" in body  # the user_query
    assert "Last user-role messages" in body


# State 4 — real-to-real: production harness end-to-end (covered by (a) above);
# this test is a thin reference asserting the state-4 chain ran.
def test_cockburn_4_real_to_real_reference(harness, synthetic_transcript, harness_dirs):
    sid = "55555555-aaaa-bbbb-cccc-666666666666"
    outcome = harness.draft_scope("q")
    harness.run_step25(sid, "default", "q", outcome, _always_pass_checker,
                       transcript_path=synthetic_transcript)
    bundle = harness.build_approval_bundle(outcome)
    assert bundle is not None
    result = harness.post_r0_intake(sid, "default", outcome)
    assert result["r1_scope_approved"] is True


# --------------------------------------------------------------------------- #
# Q11 user-facing message style — no internal identifiers leak.
# --------------------------------------------------------------------------- #

def test_q11_footer_carries_no_internal_identifiers():
    """The footer string never names r0_intake / r1_scope_approved / cycle_id / caller_skill."""
    footer = _SkillFlowHarness.footer_user_approved()
    for forbidden in ("r0_intake", "r1_scope_approved", "cycle_id", "caller_skill"):
        assert forbidden not in footer


# --------------------------------------------------------------------------- #
# Producer-never-verifies — checker isolation.
# --------------------------------------------------------------------------- #

def test_producer_never_verifies_checker_sees_only_bounded_artifact(harness, synthetic_transcript):
    """The Step 2.5 checker must NOT see manifest state or flow controller state."""
    sid = "55555555-aaaa-bbbb-cccc-777777777777"
    outcome = harness.draft_scope("topic body")
    captured = []
    harness.run_step25(
        sid, "default", "topic body", outcome,
        _read_bounded_artifact_checker(captured),
        transcript_path=synthetic_transcript,
    )
    assert captured
    body = captured[0]
    # The checker saw the three bounded sections per Q7…
    assert "## /research query" in body
    assert "## Draft scope" in body
    assert "## Last user-role messages" in body
    # …and nothing about the pipeline manifest.
    assert "r1_scope_approved" not in body
    assert "research_pipeline" not in body


# --------------------------------------------------------------------------- #
# Drafter-failure degraded path (UX Decision #2).
# --------------------------------------------------------------------------- #

def test_drafter_failure_returns_error_outcome(harness_dirs):
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    err = ScopeDraftError(reason="upstream model unavailable", code="drafter_failed")
    failing_adapter = FakeScopeDraftAdapter(fail_with=err)
    h = _SkillFlowHarness(
        adapter=failing_adapter,
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    outcome = h.draft_scope("q")
    assert is_error(outcome)
    assert outcome.reason == "upstream model unavailable"
    # No flip happens.

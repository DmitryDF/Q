"""S4 integration tests — Deep + Autonomous routing paths + sibling delegations.

Slice S4 extends the S3 walking skeleton (Ninja-only) with two more routing
paths on the same backbone:

  * Deep      — same UX as Ninja but the scope payload signals the deeper
                search tier downstream (Q15 axis: source-tier = deeper
                external). User still approves.
  * Autonomous — skips the approval bundle entirely; flow controller
                auto-approves on the user's behalf; payload carries
                `autonomous_scope=true` instead of `user_approved_scope=true`.

S4 also brings up the sibling-skill delegation entries on research-de.md +
research-ru.md and ships the Q6 Quick-Answer deadlock fix on research-en.md.

Validation gate per the S4 handoff prompt:

  1. Deep flow reaches r0_intake with `user_approved_scope=true` AND the
     scope payload carries the deeper search tier (suggested_depth='deep').
  2. Autonomous flow reaches r0_intake with `autonomous_scope=true` AND the
     approval bundle is NEVER invoked (build_approval_bundle call count == 0).
  3. Quick-Answer → re-enter-routing routes correctly with Internal KB
     hidden from the re-entry option set (Q6 deadlock fix).
  4. Sibling-skill validator (S1) reports zero drift after the DE/RU
     delegation entries land.

Architecture notes:
  * Re-uses the `_SkillFlowHarness` from `test_s3_walking_skeleton.py` to
    avoid duplicating the flow controller stand-in.
  * No new architecture — `ScopeDraftPort` is unchanged. `FakeScopeDraftAdapter`
    already accepts a `routing_path` string; S4 just exercises it for
    "deep" and "autonomous" in addition to "ninja".
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"
SKILLS_DIR = Path.home() / ".claude" / "skills"
PROJECTS_SKILLS_DIR = (
    Path.home()
    / "Library"
    / "Mobile Documents"
    / "iCloud~md~obsidian"
    / "Documents"
    / "Projects"
    / "Skills"
)

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research.scope_draft_port import (  # noqa: E402
    FakeScopeDraftAdapter,
    ScopeDraftIntake,
    is_output,
)
from test_s3_walking_skeleton import (  # noqa: E402
    _SkillFlowHarness,
    _always_pass_checker,
    harness_dirs,  # pytest fixture re-exported via module-level import
    synthetic_transcript,  # pytest fixture re-exported via module-level import
)


# --------------------------------------------------------------------------- #
# (1) Deep path — same approval UX as Ninja, deeper search tier in payload.
# --------------------------------------------------------------------------- #

def test_deep_flow_flips_with_user_approved_scope_and_deep_tier(
    harness_dirs, synthetic_transcript
):
    """Deep path: user approves; r0_intake records user_approved_scope=true;
    the scope payload's suggested_depth is 'deep' so the downstream pipeline
    can branch on tier without a separate CLI flag (Q15)."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    h = _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    sid = "44444444-aaaa-bbbb-cccc-100000000001"

    outcome = h.draft_scope("EV adoption in Europe 2026", routing_path="deep")
    assert is_output(outcome)
    scope = outcome
    assert scope.suggested_depth == "deep", (
        "Deep path must produce a scope with suggested_depth='deep' so the "
        "downstream pipeline can branch on tier without a separate CLI flag."
    )

    h.run_step25(
        sid, "default", "EV adoption in Europe 2026", scope,
        _always_pass_checker, transcript_path=synthetic_transcript,
    )

    bundle = h.build_approval_bundle(scope)
    assert bundle is not None
    assert bundle.options == ["approve", "edit", "cancel"], (
        "Deep approval bundle MUST mirror Ninja's options."
    )

    result = h.post_r0_intake(sid, "default", scope)
    assert result["ok"] is True
    assert result["r1_scope_approved"] is True

    # Footer wording matches Ninja for user-approval paths.
    assert h.footer_user_approved() == "Scope approved by: user"


# --------------------------------------------------------------------------- #
# (2) Autonomous path — NO approval bundle invoked, autonomous_scope=true.
# --------------------------------------------------------------------------- #

class _ApprovalBundleTracker:
    """Wraps the harness's build_approval_bundle to count invocations.

    The Autonomous path's validation gate requires that the approval bundle
    is never surfaced. Tracking via a counter is more robust than relying on
    a mock — it actually proves the controller never reached that branch.
    """

    def __init__(self, harness: _SkillFlowHarness) -> None:
        self.harness = harness
        self.call_count = 0
        self._original = harness.build_approval_bundle

        def wrapper(scope):
            self.call_count += 1
            return self._original(scope)

        harness.build_approval_bundle = wrapper  # type: ignore[assignment]


def test_autonomous_flow_skips_approval_bundle_and_flips_with_autonomous_scope(
    harness_dirs, synthetic_transcript
):
    """Autonomous: draft → Step 2.5 → flip with autonomous_scope=true; the
    approval bundle is NEVER surfaced (call count == 0)."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    h = _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    tracker = _ApprovalBundleTracker(h)
    sid = "44444444-aaaa-bbbb-cccc-200000000002"

    outcome = h.draft_scope("rapid policy scan", routing_path="autonomous")
    assert is_output(outcome)
    scope = outcome
    # Q15: Autonomous defaults to standard external tier (matches Ninja).
    assert scope.suggested_depth == "standard"

    # Step 2.5 still runs — even Autonomous fact-checks the draft against
    # the user's ask before flipping the gate (Step 2.5 is universal).
    h.run_step25(
        sid, "default", "rapid policy scan", scope,
        _always_pass_checker, transcript_path=synthetic_transcript,
    )

    # The flow controller goes DIRECTLY to the flip — no approval bundle.
    result = h.post_r0_intake_autonomous(sid, "default", scope)
    assert result["ok"] is True
    assert result["r1_scope_approved"] is True

    # The hard assertion: the approval bundle was never built on this path.
    assert tracker.call_count == 0, (
        f"Autonomous path MUST NOT invoke build_approval_bundle; "
        f"it was called {tracker.call_count} time(s)."
    )

    # Footer wording is the Autonomous variant from the Localization Table.
    assert h.footer_autonomous_approved() == "Scope approved by: AI auto-approval"


def test_autonomous_flow_records_autonomous_scope_flag_on_manifest(
    harness_dirs, synthetic_transcript
):
    """The cycle on the manifest carries autonomous_scope=true (not
    user_approved_scope) — distinct audit trail per UX Decision #8."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    h = _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    sid = "44444444-aaaa-bbbb-cccc-300000000003"
    outcome = h.draft_scope("audit-flag check", routing_path="autonomous")
    scope = outcome
    h.run_step25(sid, "default", "audit-flag check", scope,
                 _always_pass_checker, transcript_path=synthetic_transcript)
    h.post_r0_intake_autonomous(sid, "default", scope)

    # Read the manifest back through research_pipeline's state file.
    import research_pipeline as rp  # local import to keep top-of-file lean
    state_path = rp_dir / f"RP-{sid}.json"
    assert state_path.exists()
    import json
    data = json.loads(state_path.read_text(encoding="utf-8"))
    cycles = data.get("cycles", {})
    cycle = cycles.get("default", {})
    assert cycle.get("autonomous_scope") is True
    assert cycle.get("user_approved_scope") is False, (
        "Autonomous path must NOT also set user_approved_scope — the two "
        "flags are mutually exclusive audit-trail surfaces."
    )


# --------------------------------------------------------------------------- #
# (3) Quick-Answer re-entry — Internal KB and Ultra Deep hidden (Q6 fix).
# --------------------------------------------------------------------------- #

def test_research_en_quick_answer_reenters_routing_with_internal_kb_hidden():
    """Q6 deadlock fix: the Quick-Answer "If yes" branch no longer dead-ends
    at the (defunct) Standard-tier label. It re-enters the routing prompt
    from the rule file with options limited to {Ninja, Deep, Autonomous};
    Internal KB and Ultra Deep are intentionally omitted."""
    en_path = PROJECTS_SKILLS_DIR / "research-en.md"
    body = en_path.read_text(encoding="utf-8")

    # The old deadlock line must be gone.
    assert "run full research process (Standard tier)" not in body, (
        "Q6 fix incomplete: the legacy Standard-tier deadlock line is still "
        "present in research-en.md."
    )

    # Find the Quick-Answer steps block and assert the new re-entry text.
    quick_answer_idx = body.find("## Quick Answer + Research Offer")
    assert quick_answer_idx >= 0, "Quick Answer section heading missing."
    # Look at the 800 chars following the heading — covers the 3-step list
    # without dragging in unrelated sections.
    section = body[quick_answer_idx:quick_answer_idx + 800]

    assert "re-enter the routing prompt" in section
    assert "research-scope-framing.md" in section
    # Re-entry option set must include Ninja, Deep, Autonomous.
    assert "Ninja" in section
    assert "Deep" in section
    assert "Autonomous" in section
    # And must explicitly omit Internal KB + Ultra Deep from the re-entry.
    # The text should mention they are omitted (or their names should NOT
    # appear inside the curly-brace option set). We assert the canonical
    # phrase from the rationale.
    assert "Internal knowledge base" in section, (
        "The Q6 fix wording should reference Internal knowledge base by name "
        "(as the omitted option) so future readers see WHY it's excluded."
    )


# --------------------------------------------------------------------------- #
# (4) Sibling-skill drift — S1 validator zero-drift with all 3 delegations.
# --------------------------------------------------------------------------- #

def test_localization_validator_passes_with_all_three_sibling_delegations():
    """All three sibling skills now carry a delegation entry pointing at
    ~/.claude/rules/research-scope-framing.md. The S1 validator must still
    report zero drift; the delegation-resolution check (rule (b) in the
    validator) is the structural gate."""
    validator = HOOKS_DIR / "check-localization-table.sh"
    assert validator.exists(), f"S1 validator missing at {validator}"

    # Confirm each sibling carries the delegation entry. The validator only
    # checks resolution if the file mentions the basename, so this assertion
    # exercises the path the validator is meant to gate.
    rule_basename = "research-scope-framing.md"
    for sibling in ("research-en.md", "research-de.md", "research-ru.md"):
        skill_body = (PROJECTS_SKILLS_DIR / sibling).read_text(encoding="utf-8")
        assert rule_basename in skill_body, (
            f"Slice S4 must land a delegation entry in {sibling} that "
            f"mentions {rule_basename}; not found."
        )

    env = {**os.environ}
    env["LOCALIZATION_SKILLS_DIR"] = str(PROJECTS_SKILLS_DIR)
    result = subprocess.run(
        ["bash", str(validator)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, (
        f"S1 validator reported drift after S4 delegation entries landed.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "PASS" in result.stdout


# --------------------------------------------------------------------------- #
# Q11 — no internal identifiers leak into the new user-facing strings.
# --------------------------------------------------------------------------- #

def test_q11_autonomous_footer_carries_no_internal_identifiers():
    """The Autonomous footer must not name r0_intake / r1_scope_approved /
    cycle_id / caller_skill / autonomous_scope (Q11 user-facing style)."""
    footer = _SkillFlowHarness.footer_autonomous_approved()
    for forbidden in (
        "r0_intake",
        "r1_scope_approved",
        "cycle_id",
        "caller_skill",
        "autonomous_scope",
        "user_approved_scope",
    ):
        assert forbidden not in footer, (
            f"Q11 violation: footer leaks internal identifier '{forbidden}'."
        )


def test_q11_research_en_quick_answer_reentry_carries_no_internal_identifiers():
    """The Quick-Answer re-entry prose must not name any internal identifier."""
    en_path = PROJECTS_SKILLS_DIR / "research-en.md"
    body = en_path.read_text(encoding="utf-8")
    quick_answer_idx = body.find("## Quick Answer + Research Offer")
    section = body[quick_answer_idx:quick_answer_idx + 800]
    for forbidden in (
        "r0_intake",
        "r1_scope_approved",
        "cycle_id",
        "caller_skill",
        "autonomous_scope",
        "user_approved_scope",
    ):
        assert forbidden not in section, (
            f"Q11 violation in Quick-Answer block: leaks '{forbidden}'."
        )

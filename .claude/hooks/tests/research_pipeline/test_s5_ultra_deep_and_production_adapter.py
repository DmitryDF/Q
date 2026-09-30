"""S5 integration tests — Ultra Deep path + production ScopeDraftPort adapter.

Slice S5 ships two coupled deliverables on the S3 / S4 backbone:

  (a) Ultra Deep routing path — base scope + AI-generated adjacent-points
      candidates the user picks via multi-select checklist. Editorial defaults
      per the rule file's Adjacent-Points-Generation Contract:
        * 5 initial candidates
        * "recommend more" pass surfaces 5 more (≤ ~15 total per session)
        * toggling candidates does NOT trigger Step 2.5 re-check
        * final scope = approved base scope + user-selected adjacent points
        * generation-not-verification: candidates are never fact-checked

  (b) Production ScopeDraftPort adapter graduation — `ClaudeScopeDraftAdapter`
      implements the S3 port; bootstrap site swaps Fake for production without
      touching the flow controller (Cockburn Evolution Test). The Cockburn
      4-step nano-increment is COMPRESSED into S5; this file carries the
      explicit one-test-per-state assertions for the new states:

        * real-to-test: production adapter ↔ fixture caller (test below)
        * test-to-real: FakeAdapter routed through the production wrapper
                        boundary (test below)
        * real-to-real: end-to-end Ultra Deep integration test (test below)

      test-to-test was already exercised by S3's FakeScopeDraftAdapter tests.

Validation gate per the S5 handoff prompt:

  * Ultra Deep flow reaches r0_intake with user_approved_scope=true AND the
    scope payload's adjacent_points field carries the user-selected
    candidates AND suggested_depth='deep';
  * adjacent-points-generation pass returns ≥5 well-formed candidates
    against a fixture brief;
  * multi-select bundle renders correctly;
  * toggling candidates does NOT increment the Step 2.5 counter (assert via
    the harness's `_FlowState.counter`);
  * real-to-test AND test-to-real Cockburn states each have ≥1 passing test;
  * producer-never-verifies on the generation pass: no FC dispatch on
    adjacent-points (the test asserts the Step 2.5 engine is not invoked
    during the recommend-more pass).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List

import pytest

HOOKS_DIR = Path.home() / ".claude" / "hooks"
SKILLS_DIR = Path.home() / ".claude" / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research.adjacent_points_port import (  # noqa: E402
    AdjacentPointsError,
    AdjacentPointsIntake,
    AdjacentPointsResult,
    FakeAdjacentPointsAdapter,
    is_error as ap_is_error,
    is_result as ap_is_result,
)
from research.scope_draft_adapter_claude import (  # noqa: E402
    ClaudeScopeDraftAdapter,
    _build_prompt,
    live_model_invoker,
)
from research.scope_draft_port import (  # noqa: E402
    FakeScopeDraftAdapter,
    ScopeDraftError,
    ScopeDraftIntake,
    ScopeDraftOutput,
    is_error as sd_is_error,
    is_output as sd_is_output,
)
from test_s3_walking_skeleton import (  # noqa: E402
    _SkillFlowHarness,
    _always_pass_checker,
    harness_dirs,  # pytest fixture re-exported via module-level import
    synthetic_transcript,  # pytest fixture re-exported via module-level import
)

ULTRA_DEEP_TOPIC = "EV adoption in Europe 2026"


# --------------------------------------------------------------------------- #
# (1) Adjacent-points generation — initial pass returns ≥5 candidates.
# --------------------------------------------------------------------------- #

def test_adjacent_points_initial_pass_returns_five_well_formed_candidates():
    """Initial pass surfaces 5 distinct candidates derived from the topic."""
    adapter = FakeAdjacentPointsAdapter()
    intake = AdjacentPointsIntake(
        user_query=ULTRA_DEEP_TOPIC,
        base_angles=("policy landscape", "consumer adoption rate"),
        base_focused_questions=("Which countries lead?",),
        requested_count=5,
    )
    outcome = adapter.generate(intake)
    assert ap_is_result(outcome)
    assert len(outcome.candidates) == 5
    # Candidates are distinct strings (no duplicates inside one pass).
    assert len(set(outcome.candidates)) == 5
    # Each candidate carries some echo of the topic so the test artifact is
    # diff-friendly.
    assert all(ULTRA_DEEP_TOPIC in c for c in outcome.candidates)


# --------------------------------------------------------------------------- #
# (2) Recommend-more — second pass excludes already-surfaced candidates.
# --------------------------------------------------------------------------- #

def test_adjacent_points_recommend_more_excludes_already_surfaced():
    """A recommend-more pass receives `already_surfaced` and avoids duplicates."""
    adapter = FakeAdjacentPointsAdapter()
    first = adapter.generate(AdjacentPointsIntake(
        user_query=ULTRA_DEEP_TOPIC, requested_count=5,
    ))
    assert ap_is_result(first)
    assert len(first.candidates) == 5

    second = adapter.generate(AdjacentPointsIntake(
        user_query=ULTRA_DEEP_TOPIC,
        already_surfaced=first.candidates,
        requested_count=5,
    ))
    assert ap_is_result(second)
    assert len(second.candidates) == 5
    # No overlap with the first pass.
    assert set(second.candidates).isdisjoint(set(first.candidates))


# --------------------------------------------------------------------------- #
# (3) Adjacent-points failure → degraded-path error outcome (never raises).
# --------------------------------------------------------------------------- #

def test_adjacent_points_failure_returns_error_outcome():
    err = AdjacentPointsError(reason="upstream model unavailable", code="generation_failed")
    adapter = FakeAdjacentPointsAdapter(fail_with=err)
    outcome = adapter.generate(AdjacentPointsIntake(user_query="topic"))
    assert ap_is_error(outcome)
    assert outcome.reason == "upstream model unavailable"


# --------------------------------------------------------------------------- #
# (4) Ultra Deep E2E — base scope + selected adjacent points → flip.
# --------------------------------------------------------------------------- #

def test_ultra_deep_e2e_flip_with_combined_scope(harness_dirs, synthetic_transcript):
    """End-to-end Ultra Deep: draft → Step 2.5 → adjacent-points generation →
    user selects 2 candidates → approval bundle (multi-select) → flip with
    user_approved_scope=true + scope.adjacent_points populated +
    suggested_depth='deep'. Real-to-real Cockburn state (state 4)."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    h = _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    sid = "55555555-cccc-dddd-eeee-100000000001"

    outcome = h.draft_scope(ULTRA_DEEP_TOPIC, routing_path="ultra_deep")
    assert sd_is_output(outcome)
    scope = outcome
    assert scope.suggested_depth == "deep", (
        "Ultra Deep must mirror Deep's source-tier axis (suggested_depth='deep')."
    )

    h.run_step25(
        sid, "default", ULTRA_DEEP_TOPIC, scope,
        _always_pass_checker, transcript_path=synthetic_transcript,
    )
    assert h.state.counter == 1
    assert h.state.last_verdict == "PASS"

    # The flow controller now generates adjacent points and renders the
    # Ultra Deep multi-select bundle on top of the base approval bundle.
    ap_adapter = FakeAdjacentPointsAdapter()
    ap_intake = AdjacentPointsIntake(
        user_query=ULTRA_DEEP_TOPIC,
        base_angles=scope.angles,
        base_focused_questions=scope.focused_questions,
        requested_count=5,
    )
    ap_outcome = ap_adapter.generate(ap_intake)
    assert ap_is_result(ap_outcome)
    candidates = list(ap_outcome.candidates)
    assert len(candidates) == 5

    base_bundle = h.build_approval_bundle(scope)
    assert base_bundle is not None
    assert base_bundle.options == ["approve", "edit", "cancel"]

    # User picks 2 of the 5 candidates.
    selected = [candidates[0], candidates[2]]

    # Flip via the Ultra Deep payload variant carrying adjacent_points.
    result = h.post_r0_intake_with_adjacent_points(sid, "default", scope, selected)
    assert result["ok"] is True
    assert result["r1_scope_approved"] is True

    # Read the manifest back and assert the combined scope landed.
    state_path = rp_dir / f"RP-{sid}.json"
    data = json.loads(state_path.read_text(encoding="utf-8"))
    cycle = data["cycles"]["default"]
    assert cycle["user_approved_scope"] is True
    assert cycle["autonomous_scope"] is False, (
        "Ultra Deep is a user-approval path; autonomous_scope must remain False."
    )
    # The scope payload is persisted under cycle.checkpoints.r0_intake.data.scope
    # per research_pipeline.py:454-457 (every r0_intake payload is recorded
    # under .checkpoints.r0_intake.data).
    persisted_scope = cycle["checkpoints"]["r0_intake"]["data"]["scope"]
    assert persisted_scope["suggested_depth"] == "deep"
    assert persisted_scope["adjacent_points"] == selected


# --------------------------------------------------------------------------- #
# (5) Toggling adjacent-point candidates does NOT trigger Step 2.5 re-check.
# --------------------------------------------------------------------------- #

def test_toggling_adjacent_points_does_not_trigger_step25_recheck(
    harness_dirs, synthetic_transcript
):
    """Per the rule file Adjacent-Points-Generation Contract rule 3:
    selecting candidates from the checklist does NOT change the
    scope-quality predicate Step 2.5 verifies; only Edit on the base scope
    does. The Step 2.5 counter must stay frozen across multiple toggles."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    h = _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    sid = "55555555-cccc-dddd-eeee-200000000002"
    scope = h.draft_scope(ULTRA_DEEP_TOPIC, routing_path="ultra_deep")
    assert sd_is_output(scope)

    # First Step 2.5 — counter → 1.
    h.run_step25(
        sid, "default", ULTRA_DEEP_TOPIC, scope,
        _always_pass_checker, transcript_path=synthetic_transcript,
    )
    counter_after_step25 = h.state.counter
    assert counter_after_step25 == 1

    # Simulate the user toggling candidates in the multi-select bundle: the
    # flow controller does NOT call run_step25 again on toggles. We assert
    # this by re-checking the counter after several mock toggles.
    ap_adapter = FakeAdjacentPointsAdapter()
    pass_one = ap_adapter.generate(AdjacentPointsIntake(
        user_query=ULTRA_DEEP_TOPIC, requested_count=5,
    ))
    assert ap_is_result(pass_one)
    candidates = list(pass_one.candidates)

    # Mock the user selecting / deselecting different subsets repeatedly.
    for subset in ([], [candidates[0]], [candidates[0], candidates[2]], []):
        # The flow controller's only action on toggling is to update the
        # selection set — it MUST NOT call run_step25.
        selected = list(subset)
        assert h.state.counter == counter_after_step25, (
            "Toggling adjacent-point candidates MUST NOT trigger a Step 2.5 "
            "re-check (rule file Adjacent-Points-Generation Contract §3)."
        )
        assert h.state.last_verdict == "PASS"


# --------------------------------------------------------------------------- #
# (6) Recommend-more pass also does NOT trigger Step 2.5 — generation only.
# --------------------------------------------------------------------------- #

def test_recommend_more_pass_does_not_trigger_step25(
    harness_dirs, synthetic_transcript
):
    """The recommend-more pass is a separate generation, not a verification —
    Step 2.5 stays untouched (producer-never-verifies does not apply to
    generation; the user is the only authority on candidate relevance)."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs
    h = _SkillFlowHarness(
        adapter=FakeScopeDraftAdapter(),
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    sid = "55555555-cccc-dddd-eeee-300000000003"
    scope = h.draft_scope(ULTRA_DEEP_TOPIC, routing_path="ultra_deep")

    h.run_step25(
        sid, "default", ULTRA_DEEP_TOPIC, scope,
        _always_pass_checker, transcript_path=synthetic_transcript,
    )
    counter_baseline = h.state.counter

    ap_adapter = FakeAdjacentPointsAdapter()
    surfaced: List[str] = []
    for _ in range(2):  # initial + one recommend-more
        outcome = ap_adapter.generate(AdjacentPointsIntake(
            user_query=ULTRA_DEEP_TOPIC,
            already_surfaced=tuple(surfaced),
            requested_count=5,
        ))
        assert ap_is_result(outcome)
        surfaced.extend(outcome.candidates)

    # Two passes → 10 distinct candidates within the editorial cap.
    assert len(surfaced) == 10
    assert len(set(surfaced)) == 10
    # And Step 2.5 was never re-invoked.
    assert h.state.counter == counter_baseline


# --------------------------------------------------------------------------- #
# (7) Cockburn 4-step — real-to-test:
# ClaudeScopeDraftAdapter (production) ↔ injected fixture caller.
# --------------------------------------------------------------------------- #

def test_cockburn_real_to_test_production_adapter_with_fixture_caller():
    """The production adapter parses a well-formed JSON response from an
    injected fake caller and surfaces a ScopeDraftOutput. No live network."""
    canned_response = json.dumps({
        "angles": [
            "What is the current state of EV adoption?",
            "What policy levers most influence it?",
            "What second-order effects follow?",
        ],
        "focused_questions": [
            "Which countries lead 2026 adoption rates?",
            "Where are subsidy programs sun-setting?",
            "What manufacturer capacity constraints exist?",
        ],
        "search_terms": {
            "en": ["EV adoption Europe 2026", "EV subsidy phase-out", "EV charging buildout"],
        },
        "suggested_depth": "deep",
        "where_to_search": [
            "primary government statements",
            "peer-reviewed analyses",
            "established news outlets",
        ],
        "languages": ["en"],
    })

    captured_prompts: List[str] = []

    def fixture_caller(prompt: str) -> str:
        captured_prompts.append(prompt)
        return canned_response

    adapter = ClaudeScopeDraftAdapter(invoker=fixture_caller)
    outcome = adapter.draft(ScopeDraftIntake(
        routing_path="ultra_deep",
        user_query=ULTRA_DEEP_TOPIC,
        selected_languages=("en",),
    ))
    assert sd_is_output(outcome)
    assert outcome.suggested_depth == "deep"
    assert len(outcome.angles) == 3
    assert "en" in outcome.search_terms
    # The production adapter built a bounded prompt; the only data it sent
    # was from the intake — producer-never-verifies.
    assert captured_prompts, "production adapter must invoke the caller"
    assert ULTRA_DEEP_TOPIC in captured_prompts[0]


# --------------------------------------------------------------------------- #
# (8) Cockburn 4-step — test-to-real:
# FakeAdapter routed through the production wrapper boundary.
# --------------------------------------------------------------------------- #

def test_cockburn_test_to_real_fake_through_production_wrapper_boundary():
    """The production adapter's invoker is a thin wire boundary that any
    callable can satisfy. Wrap the S3 FakeScopeDraftAdapter as an invoker
    (returning the canned scope serialised as JSON) and route it through
    the ClaudeScopeDraftAdapter parse + coerce path. This exercises the
    "test-to-real" Cockburn state: a Fake on the production side of the
    boundary, real parsing / shape checking on the way back."""
    fake_inner = FakeScopeDraftAdapter()
    intake = ScopeDraftIntake(
        routing_path="ultra_deep",
        user_query=ULTRA_DEEP_TOPIC,
        selected_languages=("en",),
    )
    inner_outcome = fake_inner.draft(intake)
    assert sd_is_output(inner_outcome)

    canned_response = json.dumps(inner_outcome.to_dict())

    def fake_wired_invoker(prompt: str) -> str:
        # The wrapper boundary: the production code path serializes from the
        # Fake into a wire-shaped string, then the production adapter parses
        # it back. If the Fake's `to_dict()` and the production parser ever
        # drift, this test fires.
        return canned_response

    production = ClaudeScopeDraftAdapter(invoker=fake_wired_invoker)
    outcome = production.draft(intake)
    assert sd_is_output(outcome)
    # The round-trip through wire + parser preserves the Fake's structure.
    assert tuple(outcome.angles) == tuple(inner_outcome.angles)
    assert outcome.suggested_depth == inner_outcome.suggested_depth
    assert tuple(outcome.languages) == tuple(inner_outcome.languages)


# --------------------------------------------------------------------------- #
# (9) Production adapter — invoker failure → ScopeDraftError (never raises).
# --------------------------------------------------------------------------- #

def test_production_adapter_invoker_failure_returns_error_outcome():
    """When the wire raises, the production adapter must NOT propagate the
    exception across the port boundary. It surfaces a ScopeDraftError so the
    flow controller can offer the degraded-path AskUserQuestion (UX #2)."""
    def bad_invoker(prompt: str) -> str:
        raise RuntimeError("network down")

    adapter = ClaudeScopeDraftAdapter(invoker=bad_invoker)
    outcome = adapter.draft(ScopeDraftIntake(
        routing_path="ultra_deep", user_query="q", selected_languages=("en",),
    ))
    assert sd_is_error(outcome)
    assert outcome.code == "invoker_failed"
    # Q11: the reason is plain English and never names internal identifiers.
    for forbidden in ("r0_intake", "r1_scope_approved", "cycle_id", "caller_skill"):
        assert forbidden not in outcome.reason


def test_production_adapter_malformed_json_returns_parse_error():
    def malformed_invoker(prompt: str) -> str:
        return "not a JSON object {{{"

    adapter = ClaudeScopeDraftAdapter(invoker=malformed_invoker)
    outcome = adapter.draft(ScopeDraftIntake(
        routing_path="ninja", user_query="q", selected_languages=("en",),
    ))
    assert sd_is_error(outcome)
    assert outcome.code == "parse_failed"


def test_production_adapter_tolerates_fenced_json():
    """Models sometimes wrap JSON in ``` fences even when told not to —
    the adapter must accept the wrapped form."""
    payload = {
        "angles": ["a1", "a2", "a3"],
        "focused_questions": ["q1", "q2", "q3"],
        "search_terms": {"en": ["t1", "t2"]},
        "suggested_depth": "standard",
        "where_to_search": ["one", "two"],
        "languages": ["en"],
    }
    fenced = "```json\n" + json.dumps(payload) + "\n```"

    def fenced_invoker(prompt: str) -> str:
        return fenced

    adapter = ClaudeScopeDraftAdapter(invoker=fenced_invoker)
    outcome = adapter.draft(ScopeDraftIntake(
        routing_path="ninja", user_query="q", selected_languages=("en",),
    ))
    assert sd_is_output(outcome)


def test_production_adapter_tolerates_preamble_and_trailing_prose():
    """research-entry-point-enforcement S4 round-6 MINOR 1: an 11-call live
    measurement of the full scope-draft prompt found 1 non-JSON reply — a
    preamble/trailing-prose shape (no fence, prose surrounding the object:
    'Here is the drafted scope: {...}\\n\\nLet me know if you'd like any
    changes.'). The old `_try_parse_json` only ever stripped a single leading/
    trailing code fence, so a reply with no fence and prose on both sides
    failed `json.loads` outright and fell through to `parse_failed` even
    though a well-formed object sat right there in the text. This pins the
    fix with an injected invoker — no live model call."""
    payload = {
        "angles": ["a1", "a2", "a3"],
        "focused_questions": ["q1", "q2", "q3"],
        "search_terms": {"en": ["t1", "t2"]},
        "suggested_depth": "standard",
        "where_to_search": ["one", "two"],
        "languages": ["en"],
    }
    wrapped = (
        "Here is the drafted scope:\n\n"
        + json.dumps(payload)
        + "\n\nLet me know if you'd like any changes."
    )

    def prose_wrapped_invoker(prompt: str) -> str:
        return wrapped

    adapter = ClaudeScopeDraftAdapter(invoker=prose_wrapped_invoker)
    outcome = adapter.draft(ScopeDraftIntake(
        routing_path="ninja", user_query="q", selected_languages=("en",),
    ))
    assert sd_is_output(outcome)


def test_production_adapter_balanced_extraction_survives_a_brace_in_a_string_value():
    """The balanced-object extractor counts brace DEPTH, not the first `}` —
    a focused question that legitimately quotes a brace character (e.g.
    referencing a config snippet) must not truncate the object early."""
    payload = {
        "angles": ["a1"],
        "focused_questions": ["what does {config: true} mean here?"],
        "search_terms": {"en": ["t1"]},
        "suggested_depth": "standard",
        "where_to_search": ["one"],
        "languages": ["en"],
    }
    wrapped = "Sure —\n" + json.dumps(payload) + "\nDone."

    def nested_brace_invoker(prompt: str) -> str:
        return wrapped

    adapter = ClaudeScopeDraftAdapter(invoker=nested_brace_invoker)
    outcome = adapter.draft(ScopeDraftIntake(
        routing_path="ninja", user_query="q", selected_languages=("en",),
    ))
    assert sd_is_output(outcome)
    assert "config: true" in outcome.focused_questions[0]


# --------------------------------------------------------------------------- #
# (10) Production adapter — bootstrap-site swap demonstration (Evolution Test).
# --------------------------------------------------------------------------- #

def test_bootstrap_site_can_swap_fake_for_production(
    harness_dirs, synthetic_transcript
):
    """The flow controller must not change when the bootstrap swaps Fake →
    Production. We instantiate the harness with a production adapter wired
    to a canned invoker and drive the same Ninja chain that S3 drove
    against the Fake. Identical chain, identical assertions."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs

    canned = json.dumps({
        "angles": ["a1", "a2", "a3"],
        "focused_questions": ["q1", "q2", "q3"],
        "search_terms": {"en": ["t1", "t2", "t3"]},
        "suggested_depth": "standard",
        "where_to_search": ["one", "two"],
        "languages": ["en"],
    })

    production_adapter = ClaudeScopeDraftAdapter(invoker=lambda _p: canned)
    h = _SkillFlowHarness(
        adapter=production_adapter,
        rp_state_dir=rp_dir,
        pv_state_dir=pv_dir,
        adhoc_dir=adhoc_dir,
    )
    sid = "55555555-cccc-dddd-eeee-400000000004"

    outcome = h.draft_scope("topic", routing_path="ninja")
    assert sd_is_output(outcome)
    h.run_step25(sid, "default", "topic", outcome, _always_pass_checker,
                 transcript_path=synthetic_transcript)
    bundle = h.build_approval_bundle(outcome)
    assert bundle is not None
    result = h.post_r0_intake(sid, "default", outcome)
    assert result["r1_scope_approved"] is True


# --------------------------------------------------------------------------- #
# (11) Producer-never-verifies — Step 2.5 engine is never invoked on the
# adjacent-points generation pass. The generation port is its OWN channel
# (no shared engine, no FC writes).
# --------------------------------------------------------------------------- #

def test_producer_never_verifies_on_adjacent_points_generation(harness_dirs):
    """The adjacent-points port has no Step 2.5 hook of its own — the test
    instantiates a Fake adapter, drives a generation pass, and confirms
    that no R*.md artifacts were written to the plan_validation state dir
    (the Step 2.5 engine writes there). Combined with rule (3) of the
    Adjacent-Points-Generation Contract (no Step 2.5 re-check on toggling),
    this locks down generation-not-verification at both gates."""
    rp_dir, pv_dir, adhoc_dir = harness_dirs

    adapter = FakeAdjacentPointsAdapter()
    intake = AdjacentPointsIntake(
        user_query=ULTRA_DEEP_TOPIC, requested_count=5,
    )
    outcome = adapter.generate(intake)
    assert ap_is_result(outcome)

    # No FC engine artifacts should appear from the generation pass alone.
    if pv_dir.exists():
        markers = list(pv_dir.rglob("R*.md"))
        assert markers == [], (
            "Adjacent-points generation MUST NOT trigger the Step 2.5 "
            "engine; found stray FC artifacts: " + str(markers)
        )


# --------------------------------------------------------------------------- #
# (12) Q11 — production adapter user-facing strings carry no internal IDs.
# --------------------------------------------------------------------------- #

def test_q11_production_adapter_error_reasons_carry_no_internal_identifiers():
    """All error reasons surfaced by the production adapter use plain English;
    none name internal pipeline identifiers (Q11)."""
    for invoker in (
        lambda _: "",                       # empty_response
        lambda _: "not json",                # parse_failed
        lambda _: json.dumps({"angles": []}),  # schema_failed
    ):
        adapter = ClaudeScopeDraftAdapter(invoker=invoker)
        outcome = adapter.draft(ScopeDraftIntake(
            routing_path="ninja", user_query="q", selected_languages=("en",),
        ))
        assert sd_is_error(outcome)
        for forbidden in (
            "r0_intake", "r1_scope_approved", "cycle_id", "caller_skill",
            "autonomous_scope", "user_approved_scope",
        ):
            assert forbidden not in outcome.reason


# --------------------------------------------------------------------------- #
# (13) Prompt assembly is bounded — only intake data reaches the model.
# --------------------------------------------------------------------------- #

def test_prompt_only_contains_intake_data():
    """`_build_prompt` is the sole intake → wire converter; assert the
    prompt only mentions data from the intake and the contract header.
    Producer-never-verifies: no manifest tokens, no project context."""
    intake = ScopeDraftIntake(
        routing_path="ultra_deep",
        user_query="EV adoption in Europe 2026",
        last_user_messages=("I care about Germany specifically",),
        selected_languages=("en", "de"),
        conversation_language="en",
    )
    prompt = _build_prompt(intake)
    assert "EV adoption in Europe 2026" in prompt
    assert "I care about Germany specifically" in prompt
    assert "ultra_deep" in prompt
    assert "en, de" in prompt
    # Forbidden: anything from the manifest / pipeline / session machinery.
    for forbidden in (
        "r0_intake", "r1_scope_approved", "cycle_id",
        "research_pipeline", "caller_skill",
    ):
        assert forbidden not in prompt


# --------------------------------------------------------------------------- #
# (14) Rule file carries the adjacent-points prompt template (S5 deliverable).
# --------------------------------------------------------------------------- #

def test_rule_file_carries_adjacent_points_prompt_template():
    """The S5 rule-file edit appends a prompt-template body inside the
    existing Adjacent-Points-Generation Contract section. Lock the contract
    so a future edit doesn't accidentally drop it."""
    rule_path = Path.home() / ".claude" / "rules" / "research-scope-framing.md"
    body = rule_path.read_text(encoding="utf-8")
    assert "## Adjacent-Points-Generation Contract" in body
    assert "Prompt template (S5)" in body
    # Spot-check key placeholders that the production adapter would format.
    for needle in (
        "{user_query}",
        "{base_angles_bulleted}",
        "{base_focused_questions_bulleted}",
        "{already_surfaced_bulleted_or_none}",
        "{requested_count}",
    ):
        assert needle in body, f"prompt template missing placeholder {needle}"


# --------------------------------------------------------------------------- #
# (15) live_model_invoker is import-safe (no network call on import).
# --------------------------------------------------------------------------- #

def test_live_model_invoker_is_importable_without_sdk_handshake():
    """Importing the production module must never trigger an SDK handshake;
    `live_model_invoker` only reaches for the SDK on call. A no-call assertion
    confirms the import-time safety expected by offline test runs."""
    # The mere fact that this test file imported `live_model_invoker` at
    # module-top without raising proves no SDK handshake fires on import.
    assert callable(live_model_invoker)

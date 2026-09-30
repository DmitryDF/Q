"""S3 unit tests — ScopeDraftPort + FakeScopeDraftAdapter.

Cockburn 4-step nano-increment (`code_first_architecture.md:281-291`):

    test-to-test  → real-to-test  → test-to-real  → real-to-real

In S3 the production adapter is the Fake one (the live-Claude adapter
graduates in S5). The 4 states map as follows:

  * test-to-test : Fake double of port ↔ Fake double of adapter (caller is
                   a stand-in stub; adapter is FakeScopeDraftAdapter)
  * real-to-test : production flow-controller intake builder ↔ Fake adapter
                   (production-shaped caller against the test double)
  * test-to-real : Fake caller (test stub) ↔ "real" adapter, where in S3
                   "real" means FakeScopeDraftAdapter wired with its
                   production preset (no canned override)
  * real-to-real : production caller ↔ production adapter — both
                   FakeScopeDraftAdapter shapes for S3; the integration
                   test (test_s3_walking_skeleton.py) carries the
                   end-to-end real-to-real coverage.

Each Cockburn state has at least one passing test in this file (states 1-3)
and the integration test (state 4).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILLS_DIR = Path.home() / ".claude" / "skills"
sys.path.insert(0, str(SKILLS_DIR))

from research.scope_draft_port import (  # noqa: E402
    FakeScopeDraftAdapter,
    ScopeDraftError,
    ScopeDraftIntake,
    ScopeDraftOutput,
    ScopeDraftPort,
    is_error,
    is_output,
)


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def ninja_intake() -> ScopeDraftIntake:
    return ScopeDraftIntake(
        routing_path="ninja",
        user_query="electric vehicles in 2026",
        last_user_messages=("hi", "I want to research EVs"),
        conversation_language="en",
        selected_languages=("en",),
    )


# ── Cockburn step 1: test-to-test ─────────────────────────────────────────────

class _StubFlowDouble:
    """Caller-side test double. Holds the intake; passes through to a port."""

    def __init__(self, port: ScopeDraftPort, intake: ScopeDraftIntake) -> None:
        self.port = port
        self.intake = intake

    def run(self):
        return self.port.draft(self.intake)


def test_test_to_test_fake_to_fake(ninja_intake):
    """Fake caller stub against Fake adapter — both doubles."""
    canned = ScopeDraftOutput(
        angles=("a1",),
        focused_questions=("q1",),
        search_terms={"en": ("t1",)},
        languages=("en",),
    )
    adapter = FakeScopeDraftAdapter(canned_output=canned)
    flow = _StubFlowDouble(adapter, ninja_intake)
    out = flow.run()
    assert is_output(out)
    assert out.angles == ("a1",)
    assert out.search_terms == {"en": ("t1",)}


# ── Cockburn step 2: real-to-test ─────────────────────────────────────────────

def _production_intake_builder(query: str, langs=("en",)) -> ScopeDraftIntake:
    """Mimics the production flow controller's intake assembly."""
    return ScopeDraftIntake(
        routing_path="ninja",
        user_query=query,
        last_user_messages=(),
        conversation_language="en",
        selected_languages=langs,
    )


def test_real_to_test_production_intake_against_fake():
    """Production-shaped caller against Fake adapter (default preset)."""
    intake = _production_intake_builder("solar panels")
    adapter = FakeScopeDraftAdapter()  # default preset, no canned override
    out = adapter.draft(intake)
    assert is_output(out)
    assert "solar panels" in out.angles[0]
    assert out.languages == ("en",)
    assert "en" in out.search_terms
    assert len(out.search_terms["en"]) >= 1


# ── Cockburn step 3: test-to-real ─────────────────────────────────────────────

def test_test_to_real_stub_caller_against_real_preset(ninja_intake):
    """Test stub caller against "real" preset of the adapter.

    In S3 "real" means the default-preset FakeScopeDraftAdapter (the
    production preset for this slice). The live-Claude adapter ships in S5;
    this test gets re-pointed at that adapter then without changing shape.
    """
    real_adapter = FakeScopeDraftAdapter()  # production preset for S3
    stub_caller = _StubFlowDouble(real_adapter, ninja_intake)
    out = stub_caller.run()
    assert is_output(out)
    # Production preset produces three angles, three focused questions,
    # and per-language search terms.
    assert len(out.angles) == 3
    assert len(out.focused_questions) == 3
    assert out.suggested_depth == "standard"


# ── Failure-path coverage (degraded-path-first per c6) ────────────────────────

def test_failure_path_returns_error_not_exception(ninja_intake):
    """ScopeDraftError surfaced via discriminated union — never raised."""
    err = ScopeDraftError(reason="model timeout", code="drafter_failed")
    adapter = FakeScopeDraftAdapter(fail_with=err)
    out = adapter.draft(ninja_intake)
    assert is_error(out)
    assert out.reason == "model timeout"
    assert out.code == "drafter_failed"


def test_multi_language_intake_routes_per_language_terms():
    """Selected languages get distinct per-language search-terms slots."""
    intake = _production_intake_builder("climate adaptation", langs=("en", "de"))
    out = FakeScopeDraftAdapter().draft(intake)
    assert is_output(out)
    assert set(out.search_terms.keys()) == {"en", "de"}
    assert out.languages == ("en", "de")


# ── Port contract ─────────────────────────────────────────────────────────────

def test_port_is_abstract():
    """ScopeDraftPort cannot be instantiated directly — ABC guard."""
    with pytest.raises(TypeError):
        ScopeDraftPort()  # type: ignore[abstract]


def test_output_to_dict_is_json_safe():
    """to_dict() result is plain-Python serialisable (no dataclass leak)."""
    import json as _json
    canned = ScopeDraftOutput(
        angles=("a",),
        focused_questions=("q",),
        search_terms={"en": ("t",), "de": None},
        languages=("en", "de"),
    )
    blob = _json.dumps(canned.to_dict())
    revived = _json.loads(blob)
    assert revived["angles"] == ["a"]
    assert revived["search_terms"]["de"] is None

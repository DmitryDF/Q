"""Tests for the Claim-Identification engine walking skeleton (Slice S2).

Cockburn test-to-test: the engine port's test drives the ModelAdapterPort's
test-double (FakeModelAdapter). Proves the S2 validation gate:
  (a) one source_type (LOCAL_FILE) yields flagged claims end-to-end in-memory;
  (b) the Stage-1 ambiguity-refusal gate refuses ambiguous candidates;
  (c) the engine does NOT validate (no truth verdict anywhere);
  (d) unstructured / malformed adapter output is rejected at the seam (A13);
  (e) the vendor seam is a single adapter (Evolution Test);
  (f) the six per-criterion flags feed a derivable completeness breakdown (S3 hook).
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce  # noqa: E402


# ── fixtures ────────────────────────────────────────────────────────────────

def _stage1(candidates):
    return json.dumps({"candidates": candidates})


def _stage2(claims):
    return json.dumps({"claims": claims})


def _all_pass_flags():
    return {c: True for c in ce.CRITERIA}


def _src(text="line 36: NEDNSProxyProvider is available on macOS 10.15+", lang="en"):
    return ce.Source(ce.SourceType.LOCAL_FILE, "per-app-network-routing_DNS_RESEARCH.md",
                     text, lang=lang)


def _engine(s1, s2, model="sonnet"):
    fake = ce.FakeModelAdapter(s1, s2)
    return ce.TwoStageClaimEngine(fake, model=model), fake


# ── (a) end-to-end: one source_type yields flagged, anchored claims in-memory ─

def test_end_to_end_one_source_type_yields_flagged_claims():
    s1 = _stage1([
        {"text": "NEDNSProxyProvider is available on macOS 10.15+",
         "source_line": 36, "ambiguous": False, "reason": ""},
    ])
    s2 = _stage2([
        {"text": "NEDNSProxyProvider is available on macOS 10.15 and later.",
         "source_line": 36, "role": "backward", "flags": _all_pass_flags()},
    ])
    engine, fake = _engine(s1, s2)

    cs = engine.identify(_src())

    assert isinstance(cs, ce.ClaimSet)
    assert len(cs.claims) == 1
    claim = cs.claims[0]
    # anchored per-source-type (LOCAL_FILE -> path:line)
    assert claim.anchor.source_type is ce.SourceType.LOCAL_FILE
    assert claim.anchor.locator == "per-app-network-routing_DNS_RESEARCH.md:36"
    # six per-criterion flags present + role + native lang
    assert claim.flags.all_pass()
    assert claim.role is ce.ClaimRole.BACKWARD
    assert claim.lang == "en"
    # two model calls: exactly one per stage
    assert fake.calls == ["sonnet", "sonnet"]


def test_native_language_preserved_no_translation():
    # A16(ii): claims stored/processed in their native source language.
    ru_text = "C1. Роутинг по приложению на iOS."
    s1 = _stage1([{"text": ru_text, "source_line": 30, "ambiguous": False, "reason": ""}])
    s2 = _stage2([{"text": ru_text, "source_line": 30, "role": "backward",
                   "flags": _all_pass_flags()}])
    engine, _ = _engine(s1, s2)

    cs = engine.identify(_src(text=ru_text, lang="ru"))

    assert cs.claims[0].lang == "ru"
    assert cs.claims[0].text == ru_text  # unchanged — no translation


# ── (b) ambiguity-refusal gate ───────────────────────────────────────────────

def test_ambiguity_gate_refuses_ambiguous_candidates_before_structuring():
    s1 = _stage1([
        {"text": "it depends on the configuration", "source_line": 40,
         "ambiguous": True, "reason": "no single checkable meaning"},
        {"text": "Port-53 hijack catches plaintext DNS.", "source_line": 45,
         "ambiguous": False, "reason": ""},
    ])
    s2 = _stage2([
        {"text": "A port-53 hijack catches plaintext DNS queries.",
         "source_line": 45, "role": "backward", "flags": _all_pass_flags()},
    ])
    engine, _ = _engine(s1, s2)

    cs = engine.identify(_src())

    assert len(cs.claims) == 1                      # only the unambiguous one advanced
    assert len(cs.refused) == 1
    assert cs.refused[0]["reason"] == "no single checkable meaning"


def test_all_candidates_ambiguous_short_circuits_stage2():
    s1 = _stage1([{"text": "maybe", "source_line": 1, "ambiguous": True, "reason": "x"}])
    # stage-2 canned output is deliberately invalid; it must never be requested.
    engine, fake = _engine(s1, "NOT JSON — stage 2 should never run")

    cs = engine.identify(_src())

    assert cs.claims == []
    assert len(cs.refused) == 1
    assert fake.calls == ["sonnet"]                 # stage 2 skipped entirely


# ── (c) engine does NOT validate (producer-never-verifies, A9) ───────────────

def test_engine_does_not_validate_no_truth_verdict_anywhere():
    s1 = _stage1([{"text": "x", "source_line": 1, "ambiguous": False, "reason": ""}])
    s2 = _stage2([{"text": "X is the case.", "source_line": 1, "role": "backward",
                   "flags": _all_pass_flags()}])
    engine, _ = _engine(s1, s2)

    cs = engine.identify(_src())
    record = cs.claims[0].to_record()

    # No truth/validity/verdict field is produced by the engine — validity is a
    # separate Validation-engine concern a consumer orchestrates (S6 adds the
    # nullable validity axis; the ENGINE never asserts truth).
    for banned in ("valid", "validated", "true", "verdict", "is_true"):
        assert banned not in record
    # faithfulness is a per-criterion FLAG (vs source), not a truth judgment.
    assert "faithfulness" in record["flags"]


# ── (d) structured-output enforcement at the seam (A13) ──────────────────────

def test_unstructured_adapter_output_rejected():
    engine, _ = _engine("this is not json at all", _stage2([]))
    with pytest.raises(ce.SchemaError):
        engine.identify(_src())


def test_stage2_missing_criterion_flag_rejected():
    s1 = _stage1([{"text": "x", "source_line": 1, "ambiguous": False, "reason": ""}])
    bad_flags = {c: True for c in ce.CRITERIA if c != "minimality"}  # drop one
    s2 = _stage2([{"text": "X.", "source_line": 1, "role": "backward", "flags": bad_flags}])
    engine, _ = _engine(s1, s2)
    with pytest.raises(ce.SchemaError):
        engine.identify(_src())


def test_stage1_malformed_candidate_rejected():
    engine, _ = _engine(json.dumps({"candidates": [{"text": "x"}]}), _stage2([]))  # no 'ambiguous'
    with pytest.raises(ce.SchemaError):
        engine.identify(_src())


def test_fenced_json_tolerated():
    s1 = "```json\n" + _stage1([{"text": "x", "source_line": 1,
                                 "ambiguous": False, "reason": ""}]) + "\n```"
    s2 = _stage2([{"text": "X.", "source_line": 1, "role": "backward",
                   "flags": _all_pass_flags()}])
    engine, _ = _engine(s1, s2)
    cs = engine.identify(_src())
    assert len(cs.claims) == 1


# ── (e) vendor seam is a single adapter (Evolution Test) ─────────────────────

def test_model_family_map_is_the_only_vendor_surface():
    assert set(ce._MODEL_ID) == {"haiku", "sonnet", "opus"}
    assert ce._MODEL_ID["opus"] == "claude-opus-4-8"
    # the engine core references no model id — only the family string it is given.
    assert "claude-" not in open(ce.__file__).read().split("_MODEL_ID")[0]


def test_web_source_type_resolves_web_anchor_after_s5():
    # S5 opened the other source types via the anchor resolver (single change locus).
    s1 = _stage1([{"text": "x", "source_line": 1, "ambiguous": False, "reason": ""}])
    s2 = _stage2([{"text": "X.", "source_line": 1, "fragment": "sec-3",
                   "role": "backward", "flags": _all_pass_flags()}])
    engine, _ = _engine(s1, s2)
    src = ce.Source(ce.SourceType.WEB, "https://example.com/p", "…", lang="en")
    cs = engine.identify(src)
    assert cs.claims[0].anchor.source_type is ce.SourceType.WEB
    assert cs.claims[0].anchor.locator == "https://example.com/p#sec-3"


# ── (f) completeness breakdown is derivable from the flags (S3 hook) ─────────

def test_criteria_breakdown_derived_from_flags():
    partial = {c: True for c in ce.CRITERIA}
    partial["minimality"] = False
    s1 = _stage1([
        {"text": "a", "source_line": 1, "ambiguous": False, "reason": ""},
        {"text": "b", "source_line": 2, "ambiguous": False, "reason": ""},
    ])
    s2 = _stage2([
        {"text": "A.", "source_line": 1, "role": "backward", "flags": _all_pass_flags()},
        {"text": "B.", "source_line": 2, "role": "backward", "flags": partial},
    ])
    engine, _ = _engine(s1, s2)

    cs = engine.identify(_src())
    bd = cs.criteria_breakdown()

    assert bd["_total_claims"] == 2
    assert bd["atomicity"] == 2
    assert bd["minimality"] == 1        # one claim failed minimality
    assert bd["_refused"] == 0

"""Walking-skeleton tests for the dc-2c-claim-engine-seam (refc S9), Slice S2.

Proves the Seam-A end-to-end path: session-produced two-stage JSON → in_session_bootstrap
→ TwoStageClaimEngine.identify(DEEP) → flatten BACKWARD → the byte-unchanged checker-prompt
injection point (_factcheck_engine._build_checker_input, lines 1536-1537). Plus the
reason-logged deep_fallback on engine failure, and the InSessionModelAdapter contract.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import _dc_claim_seam as seam  # noqa: E402
from _claim_engine import (  # noqa: E402
    InSessionModelAdapter, TwoStageClaimEngine, Source, SourceType, Thoroughness,
    ClaimRole, SchemaError, in_session_bootstrap, _STAGE1_MARKER, _STAGE2_MARKER,
)
from _factcheck_engine import _build_checker_input, validate_self_assessment  # noqa: E402


def _stage1():
    return json.dumps({"candidates": [
        {"text": "worker.py refetches every symbol each run",
         "source_line": 1, "ambiguous": False, "reason": ""},
        {"text": "switch to a 15-minute TTL cache",
         "source_line": 1, "ambiguous": False, "reason": ""},
        {"text": "the cache should probably be fast",
         "source_line": 1, "ambiguous": True, "reason": "'fast' unquantified"},
    ]})


def _stage2():
    return json.dumps({"claims": [
        {"text": "worker.py refetches every watchlist symbol on each invocation.",
         "source_line": 1, "role": "backward",
         "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                   "minimality": True, "fluency": True, "faithfulness": True}},
        {"text": "The watchlist fetch should switch to a 15-minute TTL cache.",
         "source_line": 1, "role": "forward",
         "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                   "minimality": True, "fluency": True, "faithfulness": True}},
    ]})


# ── InSessionModelAdapter contract ───────────────────────────────────────────
def test_in_session_adapter_replays_by_stage_marker():
    a = InSessionModelAdapter("S1", "S2")
    assert a.complete(f"{_STAGE1_MARKER} ...", model="sonnet") == "S1"
    assert a.complete(f"{_STAGE2_MARKER} ...", model="sonnet") == "S2"


def test_in_session_adapter_unknown_prompt_raises_schemaerror():
    # SchemaError (not AssertionError) so the engine surfaces it → caller deep_fallback.
    a = InSessionModelAdapter("S1", "S2")
    with pytest.raises(SchemaError):
        a.complete("no stage marker here", model="sonnet")


def test_in_session_bootstrap_wires_engine_through_the_port():
    engine = in_session_bootstrap(_stage1(), _stage2(), model="sonnet")
    assert isinstance(engine, TwoStageClaimEngine)
    src = Source(SourceType.LOCAL_FILE, "Docs/x_RECOMMEND.md", "dummy", lang="en")
    cs = engine.identify(src, thoroughness=Thoroughness.DEEP)
    assert cs.thoroughness is Thoroughness.DEEP
    assert len(cs.refused) == 1                 # ambiguity gate fired
    roles = sorted(c.role.value for c in cs.claims)
    assert roles == ["backward", "forward"]     # both structured, roles discriminated


# ── flatten_backward: BACKWARD-only, FORWARD dropped ──────────────────────────
def test_build_scope_addendum_deep_backward_only():
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2())
    assert r["mode"] == "deep" and r["engine_used"] is True
    assert r["backward"] == 1 and r["refused"] == 1 and r["thoroughness"] == "deep"
    assert "refetches every watchlist symbol" in r["scope_addendum"]
    assert "15-minute TTL cache" not in r["scope_addendum"]   # FORWARD dropped


# ── reason-logged deep_fallback (never silent) ────────────────────────────────
def test_malformed_stage_json_routes_to_reason_logged_fallback():
    r = seam.build_scope_addendum(source_path="Docs/x.md", source_text="dummy",
                                  stage1_json="not json", stage2_json=_stage2())
    assert r["mode"] == "fallback" and r["engine_used"] is False
    fb = r["fallback"]
    assert fb["silent"] is False and fb["default_retained"] is True
    assert fb["claim_fallback_reason"].strip()          # non-empty reason


def test_empty_backward_set_routes_to_fallback():
    s1 = json.dumps({"candidates": [{"text": "add a --no-cache flag", "source_line": 1,
                                     "ambiguous": False, "reason": ""}]})
    s2 = json.dumps({"claims": [{"text": "A --no-cache flag should be added.",
                                 "source_line": 1, "role": "forward",
                                 "flags": {k: True for k in ("atomicity", "verifiability",
                                           "decontextuality", "minimality", "fluency",
                                           "faithfulness")}}]})
    r = seam.build_scope_addendum(source_path="Docs/x.md", source_text="dummy",
                                  stage1_json=s1, stage2_json=s2)
    assert r["mode"] == "fallback" and "no BACKWARD" in r["fallback"]["claim_fallback_reason"]


# ── end-to-end: engine-sourced scope_addendum reaches the checker prompt ──────
def test_engine_scope_addendum_reaches_checker_prompt_injection():
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2())
    prompt = _build_checker_input(
        "recommendation", 0, "Docs/x_RECOMMEND.md", 1, None, "",
        scope_addendum=r["scope_addendum"])
    # the byte-unchanged injection point carries the engine-sourced BACKWARD claim
    assert "Additional scope for this check:" in prompt
    assert "refetches every watchlist symbol" in prompt
    assert "15-minute TTL cache" not in prompt   # FORWARD never reaches the checker


# ── S3: anchor-translation, provenance, metrics ──────────────────────────────
def test_scope_addendum_items_carry_path_line_anchor():
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2())
    # each BACKWARD item surfaces its resolved <path>:<line> locator (S3), flags stay internal
    assert "[Docs/x_RECOMMEND.md:1]" in r["scope_addendum"]
    assert "flags" not in r["scope_addendum"] and "atomicity" not in r["scope_addendum"]


def test_deep_result_carries_provenance_token_for_pre_check_layer():
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2(), model="sonnet")
    prov = r["provenance"]
    assert "deep-engine" in prov and "backward=1" in prov and "thoroughness=deep" in prov


def test_metrics_manageable_row_recorded_at_identification_time(tmp_path):
    sidecar = tmp_path / "x_RECOMMEND.claim-runs.md"
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2(),
                                  runs_sidecar=str(sidecar))
    assert r["mode"] == "deep"
    body = sidecar.read_text(encoding="utf-8")
    assert "/double-check Step 2c" in body and "manageable" in body   # OMTM row present


def test_metrics_fallback_row_is_reason_logged(tmp_path):
    sidecar = tmp_path / "x.claim-runs.md"
    r = seam.build_scope_addendum(source_path="Docs/x.md", source_text="dummy",
                                  stage1_json="not json", stage2_json=_stage2(),
                                  runs_sidecar=str(sidecar))
    assert r["mode"] == "fallback"
    body = sidecar.read_text(encoding="utf-8")
    assert "/double-check Step 2c" in body and "default" in body and "identify failed" in body


# ── S4: Seam B — engine-sourced target.claims + provenance fold ───────────────
def test_seam_exposes_backward_claims_for_target_seeding():
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2())
    assert r["backward_claims"] == [
        "worker.py refetches every watchlist symbol on each invocation."]


def test_engine_sourced_target_clears_the_floor_validator():
    # Seam B seeds Step 2d `target.claims` from the engine's BACKWARD claims; the
    # code-enforced floor (validate_self_assessment) must still pass on that target.
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2())
    target = {"artifact_type": "recommendation",
              "axes": ["groundedness", "coverage"],
              "claims": r["backward_claims"],
              "named_source_artifacts": ["Docs/x_RECOMMEND.md"]}
    assert validate_self_assessment(target)["ok"] is True


def test_provenance_folds_into_upgraded_target_without_new_key():
    # PreCheckLayer field set is byte-unchanged: provenance lives INSIDE the existing
    # free-text upgraded_target, never as a new key.
    r = seam.build_scope_addendum(source_path="Docs/x_RECOMMEND.md", source_text="dummy",
                                  stage1_json=_stage1(), stage2_json=_stage2())
    pcl = {"model": "sonnet",
           "upgraded_target": f"recommendation target vs Docs/x_RECOMMEND.md {r['provenance']}",
           "self_assessment_passed": True,
           "user_choice": "(a) Accept"}
    assert set(pcl) == {"model", "upgraded_target", "self_assessment_passed", "user_choice"}
    assert "deep-engine" in pcl["upgraded_target"]

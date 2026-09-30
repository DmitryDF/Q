"""Tests for the OMTM instrumentation + `.claim-runs.md` sidecar (Slice S3)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce  # noqa: E402
import _claim_metrics as cm  # noqa: E402


def _flags(**overrides):
    f = {c: True for c in ce.CRITERIA}
    f.update(overrides)
    return f


def _run(claims, refused_texts=(), thoroughness=ce.Thoroughness.NORMAL):
    cands = [{"text": t, "source_line": i + 1, "ambiguous": False, "reason": ""}
             for i, t in enumerate(c["text"] for c in claims)]
    cands += [{"text": t, "source_line": 90 + i, "ambiguous": True, "reason": "vague"}
              for i, t in enumerate(refused_texts)]
    s1 = json.dumps({"candidates": cands})
    s2 = json.dumps({"claims": claims})
    eng = ce.TwoStageClaimEngine(ce.FakeModelAdapter(s1, s2), model="sonnet")
    return eng.identify(ce.Source(ce.SourceType.LOCAL_FILE, "x_RESEARCH.md", "…"),
                        thoroughness=thoroughness)


# ── OMTM derived from flags ──────────────────────────────────────────────────

def test_metrics_derived_from_flags_not_separately_measured():
    cs = _run(
        [{"text": "A holds.", "source_line": 1, "role": "backward", "flags": _flags()},
         {"text": "B holds.", "source_line": 2, "role": "backward",
          "flags": _flags(minimality=False)}],
        refused_texts=["maybe"],
    )
    m = cm.derive_metrics(cs, approach="two_stage_v1", model="sonnet",
                          checked_at="2026-07-07T00:00:00Z")
    assert m.total_claims == 2
    assert m.refused == 1
    assert m.per_criterion["atomicity"] == 2
    assert m.per_criterion["minimality"] == 1     # one claim failed minimality
    assert m.thoroughness == "normal"


# ── sidecar: append-only, header-once, flushed ───────────────────────────────

def test_sidecar_append_header_once_row_per_run(tmp_path):
    cs = _run([{"text": "A.", "source_line": 1, "role": "backward", "flags": _flags()}])
    m = cm.derive_metrics(cs, approach="two_stage_v1", model="sonnet",
                          checked_at="2026-07-07T00:00:00Z")
    sc = tmp_path / "x_RESEARCH.md.claim-runs.md"
    cm.append_run(sc, m)
    cm.append_run(sc, m)
    body = sc.read_text()
    assert body.count("# claim-identification runs") == 1     # header once
    assert body.count("| two_stage_v1 |") == 2                # one row per run
    # every criterion is a column
    for crit in ce.CRITERIA:
        assert crit in body


def test_sidecar_path_is_co_located():
    p = cm.sidecar_path_for("/a/b/per-app-network-routing_DNS_RESEARCH.md")
    assert p.name == "per-app-network-routing_DNS_RESEARCH.md.claim-runs.md"
    assert str(p.parent) == "/a/b"


# ── relative completeness (cross-approach union) ─────────────────────────────

def test_relative_completeness_cross_approach_union():
    a = _run([{"text": "A holds.", "source_line": 1, "role": "backward", "flags": _flags()},
              {"text": "B holds.", "source_line": 2, "role": "backward", "flags": _flags()}])
    b = _run([{"text": "B holds.", "source_line": 2, "role": "backward", "flags": _flags()},
              {"text": "C holds.", "source_line": 3, "role": "backward", "flags": _flags()}])
    rel = cm.relative_completeness({"approach_a": a, "approach_b": b})
    assert rel["union_size"] == 3                              # A, B, C
    assert rel["per_approach"]["approach_a"]["recovered"] == 2
    assert abs(rel["per_approach"]["approach_a"]["relative_completeness"] - 2 / 3) < 1e-9


def test_relative_completeness_matches_across_punctuation_and_case():
    a = _run([{"text": "A Holds", "source_line": 1, "role": "backward", "flags": _flags()}])
    b = _run([{"text": "a holds.", "source_line": 1, "role": "backward", "flags": _flags()}])
    rel = cm.relative_completeness({"a": a, "b": b})
    assert rel["union_size"] == 1                              # normalized to same claim


# ── absolute completeness (gold-set hook) ────────────────────────────────────

def test_absolute_completeness_gold_set_recall_and_missing():
    cs = _run([{"text": "A holds.", "source_line": 1, "role": "backward", "flags": _flags()},
               {"text": "B holds.", "source_line": 2, "role": "backward", "flags": _flags()}])
    ab = cm.absolute_completeness(cs, ["A holds", "B holds", "C holds", "D holds"])
    assert ab["denominator"] == 4
    assert ab["recovered"] == 2
    assert abs(ab["recall"] - 0.5) < 1e-9
    assert ab["missing"] == ["c holds", "d holds"]


def test_absolute_completeness_empty_gold_is_zero_not_crash():
    cs = _run([{"text": "A holds.", "source_line": 1, "role": "backward", "flags": _flags()}])
    ab = cm.absolute_completeness(cs, [])
    assert ab["denominator"] == 0
    assert ab["recall"] == 0.0

#!/usr/bin/env python3
"""Unit tests for the shared citation-label reconciliation module (Slice A).

Plan: Thoughts/research-citation-honesty-20260715142939_PLAN.md (Mode C).
Pure-function tests — no I/O; reachability is injected.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _citation_reconcile as cr  # noqa: E402

U_DEAD = "https://dead.example/x"
U_LIVE = "https://live.example/y"


def _reach(url):
    return {U_DEAD: False, U_LIVE: True}.get(url)


def _down(url):
    return cr._DOWNGRADE_FMT.format(url=url)


def test_unreachable_stated_downgrades():
    out, dg = cr.reconcile_citation_labels(f"[stated — {U_DEAD}]", _reach)
    assert out == _down(U_DEAD)
    assert dg == [{"url": U_DEAD, "from": "stated"}]


def test_paraphrased_downgrades():
    out, dg = cr.reconcile_citation_labels(f"[paraphrased — {U_DEAD}]", _reach)
    assert out == _down(U_DEAD)
    assert dg[0]["from"] == "paraphrased"


def test_reachable_stated_untouched():
    src = f"[stated — {U_LIVE}]"
    out, dg = cr.reconcile_citation_labels(src, _reach)
    assert out == src
    assert dg == []


def test_unknown_never_downgrades():
    src = f"[stated — {U_DEAD}]"
    out, dg = cr.reconcile_citation_labels(src, lambda u: None)
    assert out == src
    assert dg == []


def test_idempotent_double_apply():
    src = f"[stated — {U_DEAD}]"
    once, _ = cr.reconcile_citation_labels(src, _reach)
    twice, dg = cr.reconcile_citation_labels(once, _reach)
    assert twice == once
    assert dg == []


def test_other_markers_untouched():
    src = ("[inferred from sources] [My assessment: x] "
           f"[unverified — no source] and a link [t]({U_DEAD})")
    out, dg = cr.reconcile_citation_labels(src, _reach)
    assert out == src
    assert dg == []


def test_markdown_link_is_disjoint():
    # A markdown link to a dead URL is NOT a trust marker → untouched.
    src = f"See [the page]({U_DEAD}) for details."
    out, dg = cr.reconcile_citation_labels(src, _reach)
    assert out == src
    assert dg == []


def test_dash_tolerance_em_en_hyphen():
    for dash in ("—", "–", "-"):
        src = f"[stated {dash} {U_DEAD}]"
        out, dg = cr.reconcile_citation_labels(src, _reach)
        assert out == _down(U_DEAD), dash
        assert dg and dg[0]["url"] == U_DEAD


def test_multi_occurrence_all_downgraded():
    src = f"[stated — {U_DEAD}] mid [paraphrased — {U_DEAD}]"
    out, dg = cr.reconcile_citation_labels(src, _reach)
    assert out.count("[unverified") == 2
    assert len(dg) == 2


def test_mapping_reachability():
    out, dg = cr.reconcile_citation_labels(
        f"[stated — {U_DEAD}]", {U_DEAD: False})
    assert out == _down(U_DEAD)
    assert dg[0]["url"] == U_DEAD


def test_mixed_reachable_and_unreachable_in_one_pass():
    src = f"A [stated — {U_DEAD}] and B [stated — {U_LIVE}]."
    out, dg = cr.reconcile_citation_labels(src, _reach)
    assert _down(U_DEAD) in out
    assert f"[stated — {U_LIVE}]" in out
    assert len(dg) == 1


def test_empty_text():
    out, dg = cr.reconcile_citation_labels("", _reach)
    assert out == ""
    assert dg == []

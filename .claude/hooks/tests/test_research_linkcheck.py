#!/usr/bin/env python3
"""Integration tests for research_linkcheck.linkcheck_file (Slice B).

Plan: Thoughts/research-citation-honesty-20260715142939_PLAN.md (Mode C).
HEAD checks are stubbed via the `_head_check_fn` injection seam — no network.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import research_linkcheck as rl  # noqa: E402

U_DEAD = "https://dead.example/x"
U_LIVE = "https://live.example/y"
DOWN = "[unverified — source unreachable at fetch — {}]"


def _stub(url):
    # (status, err) — 403 for dead (broken, >=400), 200 for live.
    return (403, "Forbidden") if "dead" in url else (200, None)


def _write(tmp_path, body):
    f = tmp_path / "topic_RESEARCH.md"
    f.write_text(body, encoding="utf-8")
    return f


def test_unreachable_citation_downgraded(tmp_path):
    f = _write(tmp_path, f"Claim A [stated — {U_DEAD}].")
    res = rl.linkcheck_file(str(f), _head_check_fn=_stub)
    out = f.read_text(encoding="utf-8")
    assert DOWN.format(U_DEAD) in out
    assert res["downgraded_count"] == 1


def test_reachable_citation_untouched(tmp_path):
    src = f"Claim B [stated — {U_LIVE}]."
    f = _write(tmp_path, src)
    res = rl.linkcheck_file(str(f), _head_check_fn=_stub)
    assert f.read_text(encoding="utf-8") == src
    assert res["downgraded_count"] == 0


def test_markdown_broken_tag_still_applied(tmp_path):
    f = _write(tmp_path, f"See [page]({U_DEAD}).")
    rl.linkcheck_file(str(f), _head_check_fn=_stub)
    out = f.read_text(encoding="utf-8")
    assert "⚠ BROKEN (HTTP 403" in out


def test_downgrade_and_broken_same_write(tmp_path):
    # A citation marker AND a markdown link, both to dead URLs → both handled
    # in one write; the citation label downgraded, the markdown link BROKEN-tagged.
    f = _write(tmp_path, f"[stated — {U_DEAD}] and [pg]({U_DEAD}).")
    res = rl.linkcheck_file(str(f), _head_check_fn=_stub)
    out = f.read_text(encoding="utf-8")
    assert DOWN.format(U_DEAD) in out          # citation downgraded
    assert "⚠ BROKEN (HTTP 403" in out         # markdown link tagged
    assert res["downgraded_count"] == 1
    assert res["broken_count"] >= 1


def test_flock_file_created(tmp_path):
    f = _write(tmp_path, f"[stated — {U_DEAD}].")
    rl.linkcheck_file(str(f), _head_check_fn=_stub)
    assert (tmp_path / "topic_RESEARCH.md.lock").exists()


def test_budget_exhaustion_leaves_markers_untouched(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "TOTAL_BUDGET_S", -1.0)  # force immediate exhaustion
    src = f"[stated — {U_DEAD}] and [pg]({U_DEAD})."
    f = _write(tmp_path, src)
    res = rl.linkcheck_file(str(f), _head_check_fn=_stub)
    assert f.read_text(encoding="utf-8") == src   # nothing changed
    assert res["budget_exhausted"] is True
    assert res["downgraded_count"] == 0


def test_idempotent_second_run(tmp_path):
    f = _write(tmp_path, f"[stated — {U_DEAD}].")
    rl.linkcheck_file(str(f), _head_check_fn=_stub)
    first = f.read_text(encoding="utf-8")
    rl.linkcheck_file(str(f), _head_check_fn=_stub)
    assert f.read_text(encoding="utf-8") == first  # no double-rewrite


def test_missing_file():
    res = rl.linkcheck_file("/nonexistent/topic_RESEARCH.md", _head_check_fn=_stub)
    assert res["error"] == "file not found"
    assert res["downgraded_count"] == 0

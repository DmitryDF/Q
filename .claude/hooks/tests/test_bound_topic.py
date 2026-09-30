"""DS6 #8.0 — bound_topic resolver + slug-scoped draft-path with cold fallback."""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import bound_topic as bt  # noqa: E402
import bookkeeping_invariant as bi  # noqa: E402


def _seed(tmp_path, monkeypatch, session_id, topic_slug, active_project, state):
    ppg = tmp_path / "ppg"; ppg.mkdir()
    (ppg / "_active.json").write_text(json.dumps(
        {session_id: {"topic_slug": topic_slug, "active_project": active_project}}))
    (ppg / f"{topic_slug}__{active_project}.json").write_text(json.dumps(state))
    monkeypatch.setattr(bt, "PPG_DIR", str(ppg))
    monkeypatch.setattr(bt, "ADHOC_DIR", str(tmp_path / "adhoc"))


def test_resolve_from_project_root(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "sess123", "demo", "root",
          {"topic_slug": "demo", "project_root": "/proj", "thought_file_path": None})
    info = bt.resolve("sess123")
    assert info == {"slug": "demo", "thoughts_dir": "/proj/Thoughts", "project_root": "/proj"}


def test_resolve_prefers_thought_file_path(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "s", "demo", "root",
          {"topic_slug": "demo", "project_root": "/proj",
           "thought_file_path": "/proj/Personal/Thoughts/demo_THOUGHT.md"})
    info = bt.resolve("s")
    assert info["thoughts_dir"] == "/proj/Personal/Thoughts"
    assert info["slug"] == "demo"


def test_resolve_cold_session_is_none(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "known", "demo", "root", {"project_root": "/proj"})
    assert bt.resolve("unknown-session") is None


def test_draft_path_bound_is_slug_scoped_advisory(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "sess1234abcd", "demo", "root",
          {"topic_slug": "demo", "project_root": "/proj"})
    path, bound = bt.draft_path("sess1234abcd", "DOUBLECHECK", ts="20260624120000")
    assert bound is True
    assert path == "/proj/Thoughts/demo-20260624120000_DOUBLECHECK_sess1234.md"
    # The produced name is a recognized advisory-bucket member of the slug family.
    m = bi.classify(os.path.basename(path))
    assert m.bucket == bi.B_ADVISORY and m.type == "DOUBLECHECK" and m.slug == "demo"


def test_draft_path_cold_falls_back_to_adhoc(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, "known", "demo", "root", {"project_root": "/proj"})
    path, bound = bt.draft_path("cold-sess", "RECOMMEND", ts="20260624120000")
    assert bound is False
    assert path.endswith("/adhoc/recommend_cold-ses_20260624120000.md")

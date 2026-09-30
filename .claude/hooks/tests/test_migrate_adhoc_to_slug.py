"""DS6 #10 — one-shot adhoc->slug migration (resolvable move, unresolvable left)."""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import bound_topic as bt  # noqa: E402
import migrate_adhoc_to_slug as mig  # noqa: E402


def _setup(tmp_path, monkeypatch):
    ppg = tmp_path / "ppg"; ppg.mkdir()
    (ppg / "_active.json").write_text(json.dumps(
        {"021c1abfFULLSESSION": {"topic_slug": "demo", "active_project": "root"}}))
    (ppg / "demo__root.json").write_text(json.dumps(
        {"topic_slug": "demo", "project_root": str(tmp_path / "proj")}))
    monkeypatch.setattr(bt, "PPG_DIR", str(ppg))
    adhoc = tmp_path / "adhoc"; adhoc.mkdir()
    return adhoc


def test_resolvable_moves_unresolvable_stays(tmp_path, monkeypatch):
    adhoc = _setup(tmp_path, monkeypatch)
    (adhoc / "draft_021c1abf_20260601.md").write_text("dc draft")      # resolvable -> move
    (adhoc / "challenge_021c1abf_x.md").write_text("ch")              # resolvable -> move
    (adhoc / "draft_ffffffff_20260601.md").write_text("no session")  # unknown sid -> stay
    (adhoc / "b1_b2_a1_briefing.md").write_text("briefing")          # no sid pattern -> stay
    (adhoc / "adv_gate0.sh").write_text("#!/bin/sh")                  # not .md -> ignored

    moves = mig.migrate(str(adhoc), apply=True)
    moved_src = {os.path.basename(s) for s, _ in moves}
    assert moved_src == {"draft_021c1abf_20260601.md", "challenge_021c1abf_x.md"}

    # resolvable ones moved under the slug in <project_root>/Thoughts:
    tdir = tmp_path / "proj" / "Thoughts"
    landed = sorted(p.name for p in tdir.iterdir())
    assert any(n.startswith("demo-") and "_DOUBLECHECK_021c1abf.md" in n for n in landed)
    assert any(n.startswith("demo-") and "_CHALLENGE_021c1abf.md" in n for n in landed)
    # unresolvable left in place:
    assert (adhoc / "draft_ffffffff_20260601.md").exists()
    assert (adhoc / "b1_b2_a1_briefing.md").exists()


def test_dry_run_moves_nothing(tmp_path, monkeypatch):
    adhoc = _setup(tmp_path, monkeypatch)
    (adhoc / "draft_021c1abf_20260601.md").write_text("dc")
    moves = mig.migrate(str(adhoc), apply=False)
    assert len(moves) == 1
    assert (adhoc / "draft_021c1abf_20260601.md").exists()           # untouched


def test_resolve_by_sid8(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert bt.resolve_by_sid8("021c1abf")["slug"] == "demo"
    assert bt.resolve_by_sid8("deadbeef") is None

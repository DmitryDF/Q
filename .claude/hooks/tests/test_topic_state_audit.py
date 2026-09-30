#!/usr/bin/env python3
"""Tests for topic_state_audit.py — A6 of the topic-identity-split plan
(S3 "Count and repair").

All fixtures live under tmp_path. No test touches the live
~/.claude/state/pre_plan_gates store or the live Thoughts/ tree.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

# Respect CLAUDE_CONFIG_DIR, mirroring test_taskmanagement.py:67 — so a
# merge-candidate run tests the candidate module, not the live one.
HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import topic_state_audit as tsa  # noqa: E402


def _write_record(state_dir: Path, name: str, payload: dict) -> Path:
    p = state_dir / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Broad / narrow predicate — the load-bearing distinction
# ---------------------------------------------------------------------------

def test_broad_predicate_counts_missing_key_and_explicit_null_identically(tmp_path):
    """Design Review edge case (i): an absent `phase` key and an explicit
    null must both count as armed under the broad predicate, or the count
    under-reports."""
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    _write_record(state, "missing-key__Root.json", {
        "topic_slug": "missing-key", "thought_file_path": "/x/y.md",
        # no "phase" key at all
    })
    _write_record(state, "explicit-null__Root.json", {
        "topic_slug": "explicit-null", "thought_file_path": "/x/z.md",
        "phase": None,
    })
    _write_record(state, "has-phase__Root.json", {
        "topic_slug": "has-phase", "thought_file_path": "/x/w.md",
        "phase": "implementation",
    })
    _write_record(state, "no-spine__Root.json", {
        "topic_slug": "no-spine", "thought_file_path": None,
        "phase": None,
    })

    report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    b = report["armed_broad_predicate"]
    assert b["count"] == 2
    assert set(b["records"]) == {"missing-key__Root", "explicit-null__Root"}
    # has-phase excluded (phase present + not null); no-spine excluded (no thought_file_path)


def test_narrow_predicate_does_not_require_thought_file_path(tmp_path):
    """The narrow predicate is explicit-null-only, spine NOT required — so a
    record with phase:null and no thought_file_path counts here even though
    it is excluded from the broad predicate."""
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    _write_record(state, "null-no-spine__Root.json", {
        "topic_slug": "null-no-spine", "thought_file_path": None, "phase": None,
    })
    _write_record(state, "missing-key-only__Root.json", {
        "topic_slug": "missing-key-only", "thought_file_path": "/x/y.md",
        # no "phase" key — must NOT count under narrow (explicit-null-only)
    })

    report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    n = report["armed_narrow_predicate"]
    assert n["count"] == 1
    assert n["records"] == ["null-no-spine__Root"]
    b = report["armed_broad_predicate"]
    assert b["count"] == 1  # only missing-key-only (has a spine)
    assert b["records"] == ["missing-key-only__Root"]


# ---------------------------------------------------------------------------
# Duplicate pairs
# ---------------------------------------------------------------------------

def test_duplicate_pairs_general_detection(tmp_path):
    """A base slug + a `<base>-<14-digit-ts>` sibling under the SAME project
    is a duplicate pair. General detection — not hardcoded to any one pair."""
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    _write_record(state, "foo__Root.json", {"topic_slug": "foo", "phase": None})
    _write_record(state, "foo-20260829114108__Root.json", {"topic_slug": "foo-20260829114108", "phase": "implementation"})
    # a lone timestamped slug with NO base sibling is not a pair
    _write_record(state, "bar-20260829114108__Root.json", {"topic_slug": "bar-20260829114108", "phase": "implementation"})
    # a base+timestamp pair in a DIFFERENT project must not cross-match
    _write_record(state, "foo__OtherProject.json", {"topic_slug": "foo", "phase": None})

    report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    d = report["duplicate_pairs"]
    assert d["count"] == 1
    assert d["pairs"] == [{
        "project_slug": "Root",
        "base_slug": "foo__Root",
        "timestamped_slug": "foo-20260829114108__Root",
    }]


def test_no_duplicate_pairs_when_no_files_match(tmp_path):
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    _write_record(state, "solo__Root.json", {"topic_slug": "solo", "phase": None})
    report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    assert report["duplicate_pairs"]["count"] == 0


# ---------------------------------------------------------------------------
# Fossil rows
# ---------------------------------------------------------------------------

def test_fossil_rows_found_in_thoughts_spine(tmp_path):
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    projects_root = tmp_path / "Projects"
    thoughts = projects_root / "Thoughts"
    thoughts.mkdir(parents=True)
    spine = thoughts / "some-topic-20260101000000_PLAN.md"
    spine.write_text(
        "# Some Topic\n\n"
        "<!-- L:phase phase=None event=stop at=2026-08-16 session=abc123 updated=2026-08-16 -->\n"
        "<!-- L:phase phase=None event=stop at=2026-08-16 session=abc123 updated=2026-08-16 -->\n"
        "<!-- L:phase phase=implementation event=stop at=2026-08-17 session=abc123 updated=2026-08-17 -->\n",
        encoding="utf-8",
    )

    report = tsa.count_damage(state_dir=state, projects_root=projects_root)
    f = report["fossil_rows"]
    assert f["count"] == 2
    assert all(r["file"] == str(spine) for r in f["rows"])
    assert f["rows"][0]["line"] == 3
    assert f["rows"][1]["line"] == 4


def test_fossil_row_regex_does_not_match_real_phase_named_none_like(tmp_path):
    """Sanity: a legitimately-named phase must never false-positive."""
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    projects_root = tmp_path / "Projects"
    thoughts = projects_root / "Thoughts"
    thoughts.mkdir(parents=True)
    spine = thoughts / "x_PLAN.md"
    spine.write_text(
        "<!-- L:phase phase=implementation event=stop at=2026-08-17 session=abc updated=2026-08-17 -->\n",
        encoding="utf-8",
    )
    report = tsa.count_damage(state_dir=state, projects_root=projects_root)
    assert report["fossil_rows"]["count"] == 0


# ---------------------------------------------------------------------------
# Anomalies — must survive a store it cannot fully read
# ---------------------------------------------------------------------------

def test_unreadable_json_file_yields_anomaly_not_traceback(tmp_path):
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    _write_record(state, "readable__Root.json", {"topic_slug": "readable", "phase": None, "thought_file_path": "/x"})
    bad = state / "unreadable__Root.json"
    bad.write_text(json.dumps({"topic_slug": "unreadable"}), encoding="utf-8")
    bad.chmod(0o000)
    try:
        # Must not raise — the whole point of the anomaly channel.
        report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    finally:
        bad.chmod(0o644)  # restore so tmp_path cleanup can remove it

    assert report["anomalies"]["count"] == 1
    assert "EACCES" in report["anomalies"]["detail"][0]
    assert "unreadable__Root.json" in report["anomalies"]["detail"][0]
    # The readable record is still counted — one bad file does not sink the scan.
    assert report["armed_broad_predicate"]["count"] == 1


def test_corrupt_json_yields_anomaly_not_traceback(tmp_path):
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    (state / "corrupt__Root.json").write_text("{not valid json", encoding="utf-8")
    report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    assert report["anomalies"]["count"] == 1
    assert "JSONDecodeError" in report["anomalies"]["detail"][0]
    assert report["armed_broad_predicate"]["count"] == 0
    assert report["armed_narrow_predicate"]["count"] == 0


# ---------------------------------------------------------------------------
# Exclusions — _active.json, backups, advisory sidecars, _reaped/
# ---------------------------------------------------------------------------

def test_active_json_and_backups_and_advisory_files_excluded(tmp_path):
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    _write_record(state, "_active.json", {"sess": {"topic_slug": "x", "active_project": "Root"}})
    (state / "_active.json.bak-20260809233931").write_text("{}", encoding="utf-8")
    (state / "real__Root.json.bak-20260816181949").write_text(
        json.dumps({"topic_slug": "real", "phase": None, "thought_file_path": "/x"}), encoding="utf-8"
    )
    (state / "real__Root.claim-runs.md").write_text("stuff", encoding="utf-8")
    reaped = state / "_reaped"
    reaped.mkdir()
    _write_record(reaped, "reaped-topic__Root.json", {"topic_slug": "reaped-topic", "phase": None, "thought_file_path": "/x"})
    _write_record(state, "real__Root.json", {"topic_slug": "real", "phase": None, "thought_file_path": "/x"})

    report = tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")
    # Only the one live record counts — _active.json, its backup, the
    # .json.bak-* backup, the .claim-runs.md sidecar, and the quarantined
    # _reaped/ record are all excluded.
    assert report["armed_broad_predicate"]["count"] == 1
    assert report["armed_broad_predicate"]["records"] == ["real__Root"]
    assert report["anomalies"]["count"] == 0


# ---------------------------------------------------------------------------
# Read-only — the tool must never write anything
# ---------------------------------------------------------------------------

def test_count_damage_never_writes(tmp_path):
    state = tmp_path / "pre_plan_gates"
    state.mkdir()
    p = _write_record(state, "foo__Root.json", {"topic_slug": "foo", "phase": None, "thought_file_path": "/x"})
    before_mtime = p.stat().st_mtime
    before_files = sorted(x.name for x in state.iterdir())

    tsa.count_damage(state_dir=state, projects_root=tmp_path / "nonexistent-projects")

    after_files = sorted(x.name for x in state.iterdir())
    assert before_files == after_files
    assert p.stat().st_mtime == before_mtime


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

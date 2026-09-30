#!/usr/bin/env python3
"""S3 — lock-key unification (M16).

session-topic-identity-coherence plan, Slice S3 / A3. The load-bearing
regression: a composite-keyed lock acquired by /work-start is FOUND by
detect_ship_event (which used to read the bare topic_slug and miss it), and
session_locks splits the composite stem into bare topic + project.

Isolated: LOCKS_DIR is redirected to a scratch dir via TM_STALE_T_SECONDS-safe
monkeypatch of taskmanagement.LOCKS_DIR.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_identity_lock.py
"""
import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tm = _import("taskmanagement_s3", "taskmanagement.py")
wd = _import("work_done_s3", "work_done.py")


@pytest.fixture
def locks(tmp_path, monkeypatch):
    d = tmp_path / "locks"
    d.mkdir()
    monkeypatch.setattr(tm, "LOCKS_DIR", d)
    monkeypatch.setattr(tm, "RELEASES_LOG", d / "_releases.jsonl")
    # work_done imports taskmanagement as _tm — same module object? It imported
    # its own; redirect that too.
    monkeypatch.setattr(wd._tm, "LOCKS_DIR", d)
    monkeypatch.setattr(wd._tm, "RELEASES_LOG", d / "_releases.jsonl")
    return d


# --- key-shape helpers ----------------------------------------------------

def test_composite_and_split_roundtrip():
    key = tm.composite_lock_key("my-topic", "Root")
    assert key == "my-topic__Root"
    assert tm.split_lock_key(key) == ("my-topic", "Root")


def test_split_bare_key_has_empty_project():
    assert tm.split_lock_key("legacy-bare") == ("legacy-bare", "")


def test_split_on_last_dunder():
    # a project half never contains "__"; split on the LAST one is correct
    assert tm.split_lock_key("a__b__Root") == ("a__b", "Root")


# --- M16 regression: composite acquire -> detect finds it -----------------

def test_m16_detect_finds_composite_lock(locks):
    tm.acquire_lock(tm.composite_lock_key("t", "p"), "sid-x")
    res = wd.detect_ship_event(
        topic_slug="t", project_slug="p", plan_path=None,
        session_id="sid-x", started_at="2026-08-01T00:00:00Z",
        ended_at="2026-08-01T01:00:00Z",
    )
    assert res["lock_present"] is True
    assert res["ship_event"] is True


def test_m16_bare_key_is_not_found(locks):
    # the OLD bug: a lock acquired composite is NOT visible under the bare slug
    tm.acquire_lock(tm.composite_lock_key("t", "p"), "sid-y")
    assert tm.read_lock("t") is None                     # bare read -> miss
    assert tm.read_lock("t__p") is not None              # composite -> hit


def test_detect_no_lock_no_ship(locks):
    res = wd.detect_ship_event(
        topic_slug="t", project_slug="p", plan_path=None,
        session_id="sid-z", started_at="2026-08-01T00:00:00Z",
        ended_at="2026-08-01T01:00:00Z",
    )
    assert res["lock_present"] is False
    assert res["ship_event"] is False


# --- session_locks stem split ---------------------------------------------

def test_session_locks_splits_stem(locks):
    tm.acquire_lock(tm.composite_lock_key("alpha", "your-project"), "sid-s")
    rows = wd.session_locks("sid-s")
    assert len(rows) == 1
    row = rows[0]
    assert row["topic_slug"] == "alpha"                  # bare, not composite
    assert row["project_slug"] == "your-project"
    assert row["composite_key"] == "alpha__your-project"
    assert row["fresh"] is True


def test_session_locks_only_own_session(locks):
    tm.acquire_lock(tm.composite_lock_key("mine", "Root"), "sid-me")
    tm.acquire_lock(tm.composite_lock_key("theirs", "Root"), "sid-other")
    rows = wd.session_locks("sid-me")
    assert [r["topic_slug"] for r in rows] == ["mine"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

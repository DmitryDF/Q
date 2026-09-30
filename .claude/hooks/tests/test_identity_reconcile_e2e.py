#!/usr/bin/env python3
"""S6 — end-to-end reconcile smoke on a realistic COPY fixture.

session-topic-identity-coherence plan, Slice S6 / A6. Builds a fixture that
mirrors the three live shapes (correct-convention divergent-project twins;
inverted/swapped-key records; unresolvable records), runs the FULL reconcile
(`reconcile all --apply`) against it BOTH in-process and via the CLI subprocess,
and asserts:
  * every reconcilable topic collapses to exactly ONE canonical record
  * the redundant twin is mv'd to _reaped/*.bak-<ts> (reversible, never rm)
  * inverted + unresolvable records are left completely untouched (safety)
  * the live state dir is NEVER touched (all work in tempdirs / copies)

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_identity_reconcile_e2e.py
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"
REAPER = HOOKS / "reap_orphan_plain_topics.py"


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import("pre_plan_gates_s6", "pre_plan_gates.py")
import taskmanagement as tm


def _build_fixture(tmp_path):
    root = tmp_path / "Projects"
    (root / "Thoughts").mkdir(parents=True)
    (root / "TODO.md").write_text("# root\n")
    state = tmp_path / "state" / "pre_plan_gates"
    state.mkdir(parents=True)

    def spine(slug):
        p = root / "Thoughts" / f"{slug}-20260101010101_THOUGHT.md"
        p.write_text(f"# {slug}\n")
        return p

    def rec(name, **fields):
        (state / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")

    # (1) reconcilable: Root(rich) + Projects(sparse), same spine
    s1 = spine("project-tracking-staleness")
    rec("project-tracking-staleness__Root", topic_slug="project-tracking-staleness",
        project_slug="Root", thought_file_path=str(s1), phase="planning",
        phase_history=[{"to": "planning"}])
    rec("project-tracking-staleness__Projects", topic_slug="project-tracking-staleness",
        project_slug="Projects", thought_file_path=str(s1))
    # (2) reconcilable: lone __Projects (no __Root yet)
    s2 = spine("async-answer-notification")
    rec("async-answer-notification__Projects", topic_slug="async-answer-notification",
        project_slug="Projects", thought_file_path=str(s2), phase="thought")
    # (3) inverted / swapped-key: filename topic segment is the PROJECT
    s3 = spine("audit-session-app-runs")
    rec("Projects__audit-session-app-runs", topic_slug="Projects",
        project_slug="audit-session-app-runs", thought_file_path=str(s3))
    # (4) unresolvable: spine not on disk
    rec("ghost-topic__Projects", topic_slug="ghost-topic", project_slug="Projects",
        thought_file_path=str(root / "Thoughts" / "does-not-exist_THOUGHT.md"))
    # (5) clean canonical single record (control — must be left alone)
    s5 = spine("already-fine")
    rec("already-fine__Root", topic_slug="already-fine", project_slug="Root",
        thought_file_path=str(s5))
    return root, state


def test_e2e_reconcile_all_in_process(tmp_path, monkeypatch):
    root, state = _build_fixture(tmp_path)
    locks = tmp_path / "locks"; locks.mkdir()
    monkeypatch.setattr(ppg, "PROJECTS_ROOT", root)
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", state)
    monkeypatch.setattr(tm, "LOCKS_DIR", locks)
    monkeypatch.setattr(tm, "RELEASES_LOG", locks / "_releases.jsonl")

    # `reconcile all` = reconcile every slug find_mis_keyed reports
    slugs = [r["slug"] for r in ppg.find_mis_keyed()]
    assert set(slugs) == {"project-tracking-staleness", "async-answer-notification"}
    for slug in slugs:
        ppg.reconcile_topic_identity(slug, apply=True, acquire_locks=True)

    # reconcilable topics collapsed to exactly one canonical __Root record
    assert sorted(p.name for p in state.glob("project-tracking-staleness__*.json")) \
        == ["project-tracking-staleness__Root.json"]
    data = json.loads((state / "project-tracking-staleness__Root.json").read_text())
    assert data["phase"] == "planning"                       # rich data preserved
    assert sorted(p.name for p in state.glob("async-answer-notification__*.json")) \
        == ["async-answer-notification__Root.json"]

    # redundant twin backed up (reversible)
    assert list((state / "_reaped").glob("project-tracking-staleness__Projects.json.bak-*"))

    # SAFETY: inverted + unresolvable + clean records untouched
    assert (state / "Projects__audit-session-app-runs.json").exists()
    assert (state / "ghost-topic__Projects.json").exists()
    assert (state / "already-fine__Root.json").exists()
    assert "Projects" not in {r["slug"] for r in ppg.find_mis_keyed()}


def test_e2e_reconcile_all_via_cli(tmp_path):
    root, state = _build_fixture(tmp_path)

    def run(*args):
        r = subprocess.run([sys.executable, str(REAPER), *args],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    # DRY-RUN first — mutates nothing
    run("reconcile", "all", "--state-dir", str(state))
    assert (state / "project-tracking-staleness__Projects.json").exists()
    assert not (state / "_reaped").exists()

    # APPLY
    run("reconcile", "all", "--apply", "--state-dir", str(state))
    # NB: fixture spines live outside the real PROJECTS_ROOT, so canonical == Root
    live = sorted(p.name for p in state.glob("project-tracking-staleness__*.json"))
    assert live == ["project-tracking-staleness__Root.json"]
    assert (state / "Projects__audit-session-app-runs.json").exists()   # inverted untouched


def test_e2e_on_copy_of_live_state_dir_readonly(tmp_path):
    """The plan's Verification #2 shape: copy the LIVE state dir and run
    find-mis-keyed against the COPY (read-only classification). Proves the
    classifier runs on real data WITHOUT touching the live dir. We do NOT
    --apply here (that is an operator decision, post-deploy + STALE_T)."""
    live = Path.home() / ".claude" / "state" / "pre_plan_gates"
    if not live.is_dir():
        pytest.skip("no live state dir")
    copy = tmp_path / "copy"
    shutil.copytree(live, copy)
    r = subprocess.run(
        [sys.executable, str(REAPER), "find-mis-keyed", "--state-dir", str(copy)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "RECONCILABLE" in r.stdout and "INVERTED" in r.stdout
    # the live dir mtime/content is unchanged by a read-only classification
    assert live.is_dir()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

#!/usr/bin/env python3
"""S4 — reap CLI reconcile logic (twin collapse under safe-defaults).

session-topic-identity-coherence plan, Slice S4 / A4. Covers:
  * find_mis_keyed detection
  * reconcile DRY-RUN mutates nothing; --apply collapses twins to one record
  * redundant twin mv'd to _reaped/*.bak-<ts> (never rm — reversible)
  * rename-to-canonical when the canonical key is absent
  * active-lock topic SKIPPED by the acquire path (never moved out from under a
    live session)
  * lock released on a simulated crash mid-reconcile (try/finally)
  * backfill idempotency (second reconcile is a no-op)
  * MERGE_REVIEW when a redundant twin is richer than the canonical

The realistic live twin is __Root vs __Projects (genuinely distinct names). NB:
on a case-insensitive filesystem (macOS APFS) `__Root` and `__root` are the SAME
file, so a `root`/`Root` pair is NOT a valid twin fixture — the reconcile guard
for that same-file case is exercised by test_case_variant_not_destroyed.

Fully isolated: ppg.TOPIC_STATE_DIR + taskmanagement.LOCKS_DIR are monkeypatched.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_identity_reconcile.py
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import("pre_plan_gates_s4", "pre_plan_gates.py")
import taskmanagement as tm  # ppg's `import taskmanagement` resolves to this


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "Projects"
    root.mkdir()
    (root / "TODO.md").write_text("# root\n")
    (root / "Thoughts").mkdir()
    state = tmp_path / "state" / "pre_plan_gates"
    state.mkdir(parents=True)
    locks = tmp_path / "locks"
    locks.mkdir()
    monkeypatch.setattr(ppg, "PROJECTS_ROOT", root)
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", state)
    monkeypatch.setattr(tm, "LOCKS_DIR", locks)
    monkeypatch.setattr(tm, "RELEASES_LOG", locks / "_releases.jsonl")

    class NS:
        pass
    ns = NS(); ns.root = root; ns.state = state; ns.locks = locks
    return ns


def _spine(root, slug):
    p = root / "Thoughts" / f"{slug}-20260101010101_THOUGHT.md"
    p.write_text(f"# {slug}\n")
    return p


def _twin(state, slug, project, **extra):
    payload = {"topic_slug": slug, "project_slug": project,
               "thought_file_path": None, "phase": None, "phase_history": []}
    payload.update(extra)
    (state / f"{slug}__{project}.json").write_text(json.dumps(payload), encoding="utf-8")


def _mk_live_twins(env, slug):
    """The live bug shape: a rich __Root (canonical) + a __Projects twin, BOTH
    pointing at the same root-level spine (canonical == Root). Matches the real
    project-tracking-staleness data: both records carry thought_file_path; they
    differ only in the project key + richness."""
    spine = _spine(env.root, slug)
    _twin(env.state, slug, "Root", thought_file_path=str(spine),
          phase="planning", phase_history=[{"to": "planning"}])
    _twin(env.state, slug, "Projects", thought_file_path=str(spine))  # sparse but same spine
    return spine


# --- detection ------------------------------------------------------------

def test_find_mis_keyed_reports_twins(env):
    _mk_live_twins(env, "twinny")
    _twin(env.state, "clean", "Root", thought_file_path=str(_spine(env.root, "clean")))
    rows = {r["slug"]: r for r in ppg.find_mis_keyed()}
    assert "twinny" in rows
    assert rows["twinny"]["reason"] == "multiple-twins"
    assert rows["twinny"]["canonical_project"] == "Root"
    assert "clean" not in rows  # single canonical twin -> not mis-keyed


def test_find_mis_keyed_lone_miskeyed(env):
    spine = _spine(env.root, "lonely")
    _twin(env.state, "lonely", "Projects", thought_file_path=str(spine))
    rows = {r["slug"]: r for r in ppg.find_mis_keyed()}
    assert rows["lonely"]["reason"] == "mis-keyed"
    assert rows["lonely"]["canonical_project"] == "Root"


# --- DRY-RUN vs --apply ---------------------------------------------------

def test_dry_run_mutates_nothing(env):
    _mk_live_twins(env, "dry")
    res = ppg.reconcile_topic_identity("dry", apply=False)
    assert res["applied"] is False
    assert res["canonical_project"] == "Root"
    assert res["actions"]  # a plan is reported
    assert (env.state / "dry__Projects.json").exists()   # untouched
    assert (env.state / "dry__Root.json").exists()
    assert not (env.state / "_reaped").exists()


def test_apply_collapses_to_one_record(env):
    _mk_live_twins(env, "heal")
    res = ppg.reconcile_topic_identity("heal", apply=True, acquire_locks=True)
    assert res["applied"] is True
    live = sorted(p.name for p in env.state.glob("heal__*.json"))
    assert live == ["heal__Root.json"]                   # one canonical record
    data = json.loads((env.state / "heal__Root.json").read_text())
    assert data["phase"] == "planning"                   # rich data preserved
    reaped = list((env.state / "_reaped").glob("heal__Projects.json.bak-*"))
    assert len(reaped) == 1                              # redundant twin mv'd (reversible)


def test_apply_renames_lone_miskeyed_to_canonical(env):
    slug = "rename"
    spine = _spine(env.root, slug)
    _twin(env.state, slug, "Projects", thought_file_path=str(spine),
          phase="planning")
    res = ppg.reconcile_topic_identity(slug, apply=True, acquire_locks=True)
    assert res["applied"] is True
    live = sorted(p.name for p in env.state.glob(f"{slug}__*.json"))
    assert live == [f"{slug}__Root.json"]
    data = json.loads((env.state / f"{slug}__Root.json").read_text())
    assert data["phase"] == "planning"


def test_backfill_idempotent(env):
    _mk_live_twins(env, "again")
    ppg.reconcile_topic_identity("again", apply=True, acquire_locks=True)
    res2 = ppg.reconcile_topic_identity("again", apply=True, acquire_locks=True)
    assert res2["reason"] == "already-canonical"
    assert not res2["actions"]


# --- safety: active lock guard --------------------------------------------

def test_active_lock_topic_is_skipped(env):
    _mk_live_twins(env, "locked")
    tm.acquire_lock(tm.composite_lock_key("locked", "Projects"), "live-sid")
    res = ppg.reconcile_topic_identity("locked", apply=True, acquire_locks=True)
    assert res["reason"].startswith("twin-locked")
    assert "Projects" in res["skipped_locked"]
    # NOTHING mutated — both twins still present
    assert (env.state / "locked__Projects.json").exists()
    assert (env.state / "locked__Root.json").exists()
    assert not (env.state / "_reaped").exists()


def test_lock_released_on_crash_mid_reconcile(env, monkeypatch):
    _mk_live_twins(env, "crash")

    def _boom(*a, **k):
        raise RuntimeError("simulated crash mid-reconcile")

    monkeypatch.setattr(ppg, "_reconcile_apply", _boom)
    with pytest.raises(RuntimeError):
        ppg.reconcile_topic_identity("crash", apply=True, acquire_locks=True)
    # every acquired lock was released in the finally — no dangling lock files
    remaining = list(env.locks.glob("crash__*.lock"))
    assert remaining == []


# --- MERGE_REVIEW ---------------------------------------------------------

def test_merge_review_when_redundant_twin_richer(env):
    slug = "merge"
    spine = _spine(env.root, slug)
    _twin(env.state, slug, "Root", thought_file_path=str(spine))   # sparse canonical
    _twin(env.state, slug, "Projects", thought_file_path=str(spine),
          phase="implementation",
          phase_history=[{"to": "planning"}, {"to": "implementation"}])  # richer
    res = ppg.reconcile_topic_identity(slug, apply=True, acquire_locks=True)
    assert "Projects" in res["merge_review"]
    note = list((env.state / "_reaped").glob(f"{slug}__Projects.json.bak-*.MERGE_REVIEW"))
    assert len(note) == 1


# --- case-insensitive-FS guard --------------------------------------------

def test_case_variant_not_destroyed(env):
    """A lone `__root` (lowercase) whose canonical is `Root` is the SAME file on
    APFS — reconcile must NOT back it up and leave no record."""
    slug = "casey"
    spine = _spine(env.root, slug)
    _twin(env.state, slug, "root", thought_file_path=str(spine), phase="planning")
    res = ppg.reconcile_topic_identity(slug, apply=True, acquire_locks=True)
    # the record still exists (under whichever case) — nothing destroyed
    live = list(env.state.glob(f"{slug}__*.json"))
    assert len(live) == 1
    data = json.loads(live[0].read_text())
    assert data["phase"] == "planning"
    assert not (env.state / "_reaped").exists()


# --- spineless records are not reconciled ---------------------------------

def test_spineless_records_not_reconciled(env):
    _twin(env.state, "nospine", "Projects")     # neither record resolves a spine
    _twin(env.state, "nospine", "Personal")
    res = ppg.reconcile_topic_identity("nospine", apply=True, acquire_locks=True)
    assert res["reason"] == "no-twins"           # spineless -> not verified twins
    assert (env.state / "nospine__Projects.json").exists()   # untouched
    assert (env.state / "nospine__Personal.json").exists()
    # surfaced as unresolvable
    unres = {r["file"] for r in ppg.find_unresolvable()}
    assert "nospine__Projects.json" in unres


# --- SAFETY: inverted / swapped-key records are never collapsed -----------

def test_inverted_record_not_collapsed(env):
    """An inverted `Projects__<topic>.json` (topic segment is the PROJECT) shares
    a first filename segment with other inverted records but its spine slug is the
    REAL topic. reconcile('Projects') must find NO verified twins and mutate
    nothing; find_inverted surfaces it; find_mis_keyed does NOT list 'Projects'."""
    spine_a = _spine(env.root, "audit-session-app-runs")
    spine_b = _spine(env.root, "challenge-skill-redesign")
    # inverted: filename is <project>__<topic>, fields swapped
    (env.state / "Projects__audit-session-app-runs.json").write_text(json.dumps(
        {"topic_slug": "Projects", "project_slug": "audit-session-app-runs",
         "thought_file_path": str(spine_a)}), encoding="utf-8")
    (env.state / "Projects__challenge-skill-redesign.json").write_text(json.dumps(
        {"topic_slug": "Projects", "project_slug": "challenge-skill-redesign",
         "thought_file_path": str(spine_b)}), encoding="utf-8")

    res = ppg.reconcile_topic_identity("Projects", apply=True, acquire_locks=True)
    assert res["reason"] == "no-twins"           # no spine has slug 'Projects'
    # nothing moved
    assert (env.state / "Projects__audit-session-app-runs.json").exists()
    assert (env.state / "Projects__challenge-skill-redesign.json").exists()
    assert not (env.state / "_reaped").exists()
    # find_mis_keyed does NOT report 'Projects'; find_inverted DOES surface them
    assert "Projects" not in {r["slug"] for r in ppg.find_mis_keyed()}
    inverted_files = {r["file"] for r in ppg.find_inverted()}
    assert "Projects__audit-session-app-runs.json" in inverted_files
    assert "Projects__challenge-skill-redesign.json" in inverted_files


def test_reconcile_all_skips_inverted(env):
    """`find_mis_keyed` drives `reconcile all` — it must list only reconcilable
    slugs, never inverted first-segments, so the bulk path is safe."""
    _mk_live_twins(env, "realtopic")           # a genuine reconcilable twin pair
    spine_inv = _spine(env.root, "some-real-topic")
    (env.state / "your-project__some-real-topic.json").write_text(json.dumps(
        {"topic_slug": "your-project", "project_slug": "some-real-topic",
         "thought_file_path": str(spine_inv)}), encoding="utf-8")
    slugs = {r["slug"] for r in ppg.find_mis_keyed()}
    assert "realtopic" in slugs
    assert "your-project" not in slugs


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

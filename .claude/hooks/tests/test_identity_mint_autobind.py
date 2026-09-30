#!/usr/bin/env python3
"""S2 — mint/bind through the canonical resolver.

session-topic-identity-coherence plan, Slice S2 / A2:
  * create_topic correct-key-before-mint (wrong caller arg -> derived key; a
    mis-keyed rich twin is renamed onto the canonical key, not orphaned).
  * _autobind_project_for_slug E26 deterministic disambiguation (two twins ->
    canonical, not None).
  * rename-on-resolve idempotency (concurrent second caller no-ops).

Fully isolated: PROJECTS_ROOT / TOPIC_STATE_DIR / _active are monkeypatched to a
scratch tree — the live state dir is never touched.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_identity_mint_autobind.py
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"
PPG_PY = HOOKS / "pre_plan_gates.py"


def _import_ppg():
    spec = importlib.util.spec_from_file_location("pre_plan_gates_s2", PPG_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import_ppg()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Scratch projects tree + isolated state dir. Returns a namespace."""
    root = tmp_path / "Projects"
    root.mkdir()
    (root / "TODO.md").write_text("# root todo\n")
    (root / "Thoughts").mkdir()
    state = tmp_path / "state" / "pre_plan_gates"
    state.mkdir(parents=True)
    monkeypatch.setattr(ppg, "PROJECTS_ROOT", root)
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", state)
    monkeypatch.setattr(ppg, "_active_path", lambda: state / "_active.json")

    class NS:
        pass
    ns = NS()
    ns.root = root
    ns.state = state
    return ns


def _write_state(state, slug, project, **extra):
    payload = {"topic_slug": slug, "project_slug": project,
               "thought_file_path": None, "phase": None, "phase_history": []}
    payload.update(extra)
    (state / f"{slug}__{project}.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _spine(root, slug):
    p = root / "Thoughts" / f"{slug}-20260101010101_THOUGHT.md"
    p.write_text(f"# {slug}\n")
    return p


# --- create_topic correct-key-before-mint ---------------------------------

def test_wrong_caller_arg_corrected_to_derived_key(tree):
    slug = "widget"
    spine = _spine(tree.root, slug)
    # caller passes the WRONG project ("Projects" cwd-basename) but hands the spine
    res = ppg.create_topic("sid-1", "Projects", slug, thought_file_path=str(spine))
    assert res["topic"] == f"{slug}__Root"       # corrected to the derived key
    files = sorted(p.name for p in tree.state.glob(f"{slug}__*.json"))
    assert files == [f"{slug}__Root.json"]        # exactly one, canonical


def test_mis_keyed_rich_twin_renamed_not_orphaned(tree):
    slug = "gizmo"
    spine = _spine(tree.root, slug)
    # a rich __Projects twin already recorded the spine (the live bug shape)
    _write_state(tree.state, slug, "Projects",
                 thought_file_path=str(spine), phase="planning",
                 phase_history=[{"to": "planning"}])
    # a new session mints with the wrong caller arg; correct-key must rebind to
    # the renamed rich record, NOT mint a blank __Root.
    res = ppg.create_topic("sid-2", "Projects", slug)
    assert res["topic"] == f"{slug}__Root"
    files = sorted(p.name for p in tree.state.glob(f"{slug}__*.json"))
    assert files == [f"{slug}__Root.json"]        # twin renamed onto canonical
    data = json.loads((tree.state / f"{slug}__Root.json").read_text())
    assert data["phase"] == "planning"            # rich data preserved


def test_spineless_plain_topic_keeps_caller_arg(tree):
    # no spine anywhere -> caller arg is authoritative (plain / worktree-origin)
    res = ppg.create_topic("sid-3", "Root", "plain-abcd1234",
                           intake_source="plain")
    assert res["topic"] == "plain-abcd1234__Root"


# --- _autobind_project_for_slug E26 disambiguation ------------------------

def test_autobind_single_twin_returns_it(tree):
    _write_state(tree.state, "solo", "your-project")
    assert ppg._autobind_project_for_slug("solo") == "your-project"


def test_autobind_two_twins_resolves_to_canonical_not_none(tree):
    slug = "twinny"
    spine = _spine(tree.root, slug)
    _write_state(tree.state, slug, "Projects", thought_file_path=str(spine),
                 phase="planning")
    _write_state(tree.state, slug, "Root")  # a second, divergent key
    # OLD behaviour returned None on >1; now deterministic -> canonical "Root".
    assert ppg._autobind_project_for_slug(slug) == "Root"


def test_autobind_two_twins_no_spine_still_refuses(tree):
    slug = "nospine"
    _write_state(tree.state, slug, "Projects")   # neither records a spine
    _write_state(tree.state, slug, "root")
    assert ppg._autobind_project_for_slug(slug) is None   # refuse to guess


def test_autobind_zero_twins_returns_none(tree):
    assert ppg._autobind_project_for_slug("ghost") is None


# --- rename-on-resolve idempotency ----------------------------------------

def test_ensure_canonical_idempotent_when_already_canonical(tree):
    slug = "already"
    spine = _spine(tree.root, slug)
    _write_state(tree.state, slug, "Root", thought_file_path=str(spine))
    assert ppg._ensure_canonical_record(slug, "Root") is True
    # unchanged
    assert (tree.state / f"{slug}__Root.json").exists()


def test_ensure_canonical_renames_richest_twin(tree):
    slug = "healme"
    spine = _spine(tree.root, slug)
    _write_state(tree.state, slug, "Projects", thought_file_path=str(spine),
                 phase="planning")
    assert not (tree.state / f"{slug}__Root.json").exists()
    assert ppg._ensure_canonical_record(slug, "Root") is True
    assert (tree.state / f"{slug}__Root.json").exists()
    assert not (tree.state / f"{slug}__Projects.json").exists()


def test_ensure_canonical_second_call_noops(tree):
    """Concurrent second caller: canonical already produced -> no error, True."""
    slug = "race"
    spine = _spine(tree.root, slug)
    _write_state(tree.state, slug, "Projects", thought_file_path=str(spine))
    assert ppg._ensure_canonical_record(slug, "Root") is True
    # second call: canonical exists, source already gone -> idempotent True
    assert ppg._ensure_canonical_record(slug, "Root") is True


# --- A3 / S2: twin-set verification before any rename ---------------------
# topic-identity-generator-closure, Slice S2 / A3 (claim C5, gap G3).
# `_ensure_canonical_record` consumed the RAW `_twins_for_slug` glob, so a record
# belonging to a different topic that merely shared a first filename segment could
# be renamed onto the canonical key and take `_active` with it. Admission is now a
# three-arm union; these lock all three arms plus the refusal.


def _foreign_spine(root, slug):
    """A spine belonging to a DIFFERENT topic than the record's key."""
    p = root / "Thoughts" / f"{slug}-20260202020202_THOUGHT.md"
    p.write_text(f"# {slug}\n")
    return p


def test_a3_refuses_unrelated_twin_with_resolvable_foreign_artifact(tree):
    """The hazard: a cwd-shaped key whose twin's artifact names another topic.

    Renames NOTHING, returns falsey, and leaves `_active.json` untouched.
    """
    slug = "Projects"                      # the cwd-shaped, inverted-era key
    foreign = _foreign_spine(tree.root, "widget")
    _write_state(tree.state, slug, "Root", thought_file_path=str(foreign),
                 phase="planning", phase_history=[{"to": "planning"}])
    active = {"sid-x": {"topic_slug": slug, "active_project": "Root"}}
    (tree.state / "_active.json").write_text(json.dumps(active), encoding="utf-8")

    # Non-vacuity: prove this fixture REACHES the refusal rather than falling out
    # earlier. The raw glob must yield a rename candidate (so the pre-A3 code had
    # something to rename), the verified set must exclude it, and its artifact
    # must genuinely resolve (so arm (c) does not admit it). Without these three,
    # a `not ...` assertion could pass for the wrong reason.
    raw = ppg._twins_for_slug(slug)
    assert len(raw) == 1                                    # old code: a candidate
    assert ppg._reconcilable_twins_for_slug(slug) == []     # arm (a) excludes it
    assert ppg._resolve_spine_abs(raw[0][2]) is not None    # arm (c) does not admit
    assert not slug.startswith("plain-")                    # arm (b) does not admit

    assert not ppg._ensure_canonical_record(slug, "Elsewhere")

    # the unrelated record is exactly where it was; no canonical key was minted
    assert (tree.state / f"{slug}__Root.json").exists()
    assert not (tree.state / f"{slug}__Elsewhere.json").exists()
    # and the active pointer was not repointed
    assert json.loads((tree.state / "_active.json").read_text()) == active


def test_a3_admits_plain_slug_twin_whose_artifact_slug_differs(tree):
    """Arm (b): for a synthetic `plain-` key the artifact slug legitimately
    differs. Requiring arm (a) would orphan the rich twin and mint a blank."""
    slug = "plain-abcd1234"
    foreign = _foreign_spine(tree.root, "widget")
    _write_state(tree.state, slug, "Projects", thought_file_path=str(foreign),
                 phase="planning", phase_history=[{"to": "planning"}])

    # ARM-PINNING. Without these, this test asserts only the OUTCOME, and the
    # outcome is reachable through arm (c) as well: if `_foreign_spine` ever stopped
    # writing the file to disk, `_resolve_spine_abs` would return None, arm (c)
    # would admit, and this test would stay GREEN while arm (b) was broken. Since
    # arm (b) is the one this test exists to lock (the guard rail names it), the
    # fixture must prove the other two arms are shut.
    raw = ppg._twins_for_slug(slug)
    assert len(raw) == 1
    assert ppg._reconcilable_twins_for_slug(slug) == []      # arm (a) shut
    assert ppg._resolve_spine_abs(raw[0][2]) is not None     # arm (c) shut
    assert slug.startswith("plain-")                         # arm (b) is the admitter

    assert ppg._ensure_canonical_record(slug, "Root") is True

    assert (tree.state / f"{slug}__Root.json").exists()
    assert not (tree.state / f"{slug}__Projects.json").exists()
    data = json.loads((tree.state / f"{slug}__Root.json").read_text())
    assert data["phase"] == "planning"          # rich data preserved, not blanked


def test_a3_admits_spineless_twin(tree):
    """Arm (c): a twin with no resolvable artifact stays admissible — 33 of 105
    live records are this shape, and refusing them would be a regression."""
    slug = "spineless"
    _write_state(tree.state, slug, "Projects", phase="planning")

    assert ppg._ensure_canonical_record(slug, "Root") is True

    assert (tree.state / f"{slug}__Root.json").exists()
    assert not (tree.state / f"{slug}__Projects.json").exists()


def test_a3_admits_twin_whose_artifact_is_recorded_but_missing_on_disk(tree):
    """Arm (c) again: `_resolve_spine_abs` returns None for a recorded path that
    is not on disk, so an unresolvable artifact must not be read as a foreign one."""
    slug = "ghostspine"
    _write_state(tree.state, slug, "Projects",
                 thought_file_path=str(tree.root / "Thoughts" / "gone_THOUGHT.md"))

    assert ppg._ensure_canonical_record(slug, "Root") is True
    assert (tree.state / f"{slug}__Root.json").exists()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

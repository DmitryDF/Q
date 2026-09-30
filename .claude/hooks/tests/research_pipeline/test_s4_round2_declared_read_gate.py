"""S4 round-2, ITEM 2 (operator decision) — close the declared-source read hole.

`declared_read._resolve_scope_from_cycle` used to consult only
`cycle.get("scope_record")` after resolving which cycle a research file
belonged to. `research_pipeline.cmd_advance` hoists `scope_record` onto the
cycle unconditionally — BEFORE the approval branch runs — so a cycle could
carry a `scope_record` with `r1_scope_approved: False` and the read path would
still resolve and hand back the person's declared sources with no approval
check anywhere on this path.

The fix loads the manifest state and consults
`research_pipeline.resolve_cycle_scope_status(state, cycle_id)` — the one
per-cycle predicate the dispatch/read site is supposed to check before
reading — and refuses THAT cycle by name, quoting its own `reason`, when the
decision is anything but `approved`.

This file pins the hole closed. It deliberately does not restate S4 round-1's
approval-artifact tests (`test_s4_approval_artifact.py` owns those); it tests
only the read-path consequence of an unapproved cycle carrying a
`scope_record`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = HOOKS_DIR.parent / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

import research_pipeline as rp  # noqa: E402
from research import declared_read  # noqa: E402
from research import scope_record as _scope  # noqa: E402


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


_MINIMAL_SCOPE_RECORD = {
    "schema_version": _scope.SCHEMA_VERSION,
    "created_at": "2026-09-24T00:00:00+00:00",
    "sources": [],
}


def _intake(sid, state_dir, research_file_path, cycle_id=None, **extra):
    # MINOR 2 (round-7 fix): `research_file_path` is now caller-supplied
    # rather than a fixed relative literal — every call site below passes a
    # `tmp_path`-rooted absolute path, so `r0_intake`'s `_write_angles_
    # sidecar` call (fired when `scope={"angles": [...]}` is approved)
    # resolves inside pytest's own temp dir instead of wherever pytest was
    # invoked from.
    payload = {"research_file_path": research_file_path}
    payload.update(extra)
    kwargs = {"state_dir": state_dir}
    if cycle_id is not None:
        kwargs["cycle_id"] = cycle_id
    return rp.cmd_advance(sid, "r0_intake", payload, **kwargs)


def test_unapproved_cycle_with_a_scope_record_is_refused_by_name(
    tmp_state, tmp_path, monkeypatch
):
    """The headline hole: a `scope_record` present, no approval artifact —
    `_resolve_scope_from_cycle` must refuse this cycle by name rather than
    silently returning the declared sources."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r2-hole"
    research_file = str(tmp_path / "topic_RESEARCH.md")
    # No `user_approved_scope` / `scope_provenance` — this intake carries a
    # scope_record but no valid approval artifact, exactly the shape the
    # hole let through.
    _intake(sid, tmp_state, research_file, cycle_id="default",
            caller_skill="/research", scope_record=_MINIMAL_SCOPE_RECORD)

    # Confirm the vulnerable precondition directly: the record landed on the
    # cycle, but the cycle is NOT approved.
    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"]["default"]
    assert cycle.get("scope_record") == _MINIMAL_SCOPE_RECORD
    assert cycle.get("r1_scope_approved") is not True

    with pytest.raises(SystemExit) as excinfo:
        declared_read._resolve_scope_from_cycle(sid, research_file)

    message = str(excinfo.value)
    assert "default" in message, (
        "the refusal must name the cycle by id, not refuse silently or "
        f"anonymously: {message!r}"
    )
    assert "not approved" in message


def test_approved_cycle_with_a_scope_record_still_reads(tmp_state, tmp_path, monkeypatch):
    """Sanity: the fix must not regress the happy path — a genuinely approved
    cycle carrying a scope_record still resolves to a ScopeRecord."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r2-happy"
    research_file = str(tmp_path / "topic_RESEARCH.md")
    _intake(
        sid, tmp_state, research_file, cycle_id="default",
        caller_skill="/research",
        user_approved_scope=True,
        scope={"angles": ["a"]},
        scope_provenance="fresh-answer",
        scope_record=_MINIMAL_SCOPE_RECORD,
    )

    state = rp._read_state(sid, tmp_state)
    assert state["cycles"]["default"]["r1_scope_approved"] is True

    result = declared_read._resolve_scope_from_cycle(sid, research_file)
    assert isinstance(result, _scope.ScopeRecord)
    assert result.sources == ()


def test_revoked_cycle_with_a_scope_record_is_also_refused(tmp_state, tmp_path, monkeypatch):
    """A cycle that was approved and then revoked must not read either —
    `resolve_cycle_scope_status` reads `decision == "revoked"`, which is not
    `"approved"`, so the same refusal branch must catch it."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r2-revoked"
    research_file = str(tmp_path / "topic_RESEARCH.md")
    _intake(
        sid, tmp_state, research_file, cycle_id="default",
        caller_skill="/research",
        user_approved_scope=True,
        scope={"angles": ["a"]},
        scope_provenance="fresh-answer",
        scope_record=_MINIMAL_SCOPE_RECORD,
    )
    # Revoke it directly on the manifest (mirrors how the sibling S4 tests
    # exercise revocation — there is no public cmd_advance verb for it).
    state = rp._read_state(sid, tmp_state)
    state["cycles"]["default"]["r1_scope_revoked"] = True
    rp._write_state(sid, state, tmp_state)

    with pytest.raises(SystemExit) as excinfo:
        declared_read._resolve_scope_from_cycle(sid, research_file)
    assert "default" in str(excinfo.value)


def test_session_bypass_reads_even_when_the_cycle_is_unapproved(
    tmp_state, tmp_path, monkeypatch
):
    """Round-3 fix, MINOR 3: `research_pipeline.py bypass SID "reason"` is a
    documented session-level escape hatch — `research-scope-gate.sh` and
    `research-chromium-fetch/entry-guard.sh` both honour `state["bypass"]`
    before consulting per-cycle approval. This read seam used not to; it
    refused an operator who had used the documented escape hatch, with a
    message that never mentioned it. Fixed to honour it too, checked first."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r3-bypass"
    research_file = str(tmp_path / "topic_RESEARCH.md")
    _intake(sid, tmp_state, research_file, cycle_id="default",
            caller_skill="/research", scope_record=_MINIMAL_SCOPE_RECORD)

    # Confirm the precondition: the cycle is genuinely unapproved.
    state = rp._read_state(sid, tmp_state)
    assert state["cycles"]["default"].get("r1_scope_approved") is not True

    rp.cmd_bypass(sid, "operator bypass for test", state_dir=tmp_state)

    result = declared_read._resolve_scope_from_cycle(sid, research_file)
    assert isinstance(result, _scope.ScopeRecord)
    assert result.sources == ()


def test_session_bypass_also_overrides_a_revoked_cycle(tmp_state, tmp_path, monkeypatch):
    """The bypass check runs BEFORE the per-cycle predicate, so it also
    overrides a revoked cycle — matching `research-scope-gate.sh`, which
    checks `state["bypass"]` before it ever looks at revocation."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r3-bypass-revoked"
    research_file = str(tmp_path / "topic_RESEARCH.md")
    _intake(
        sid, tmp_state, research_file, cycle_id="default",
        caller_skill="/research",
        user_approved_scope=True,
        scope={"angles": ["a"]},
        scope_provenance="fresh-answer",
        scope_record=_MINIMAL_SCOPE_RECORD,
    )
    state = rp._read_state(sid, tmp_state)
    state["cycles"]["default"]["r1_scope_revoked"] = True
    rp._write_state(sid, state, tmp_state)

    rp.cmd_bypass(sid, "operator bypass for test", state_dir=tmp_state)

    result = declared_read._resolve_scope_from_cycle(sid, research_file)
    assert isinstance(result, _scope.ScopeRecord)

"""S4 round-5 fix — MAJOR 1 regression: a non-default cycle must be able to
advance end-to-end through the shipped checkpoint sequence.

A fifth independent review reproduced, against the live system, that every
`advance` block SKILL.md ships AFTER `r0_intake` (`r1_scope`, `r2_research`,
`r3_synthesis`, `r4_factcheck`, `r5_recommend`) omitted `--cycle-id`, so each
one always targeted `default`:

    r0_intake on cycle 'de' approved: True
    r1_scope WITHOUT --cycle-id -> Sequence violation: expected r0_intake,
        got r1_scope. Completed: []
    r1_scope WITH --cycle-id de: ok

S4's own rules file (`research-scope-framing.md` Step 4) mandates per-language
cycles (`cycle_id = de` for DE, `cycle_id = ru` for RU), and `/work-decode`
opens one cycle per Part-B row (`wd-<brief>-r<i>`) — so every non-default
cycle registered intake and then could never advance past it; the close gate
requires the full checkpoint sequence, so such a run could never complete.

The fix adds `--cycle-id "[default | the --cycle-id the caller passed]"` to
all five post-intake blocks in `SKILL.md`. This file pins the regression: it
drives the actual CLI (`rp.main`) through the full `RESEARCH_SEQUENCE`,
passing `--cycle-id "de"` at every step exactly as the fixed templates now
instruct, and separately pins the failure mode the fix closes (advancing with
no `--cycle-id` after registering under a non-default cycle still raises the
same sequence violation the orchestrator reproduced).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOKS_DIR))

import research_pipeline as rp  # noqa: E402

CYCLE_ID = "de"


def _payloads(research_file_path):
    """Build a fresh PAYLOADS dict rooted at `research_file_path`.

    MINOR 2 (round-7 fix): `research_file_path` used to be a fixed relative
    literal (`"Thoughts/test_de_RESEARCH.md"`), so `r0_intake`'s
    `_write_angles_sidecar` call resolved it against the process cwd and
    left a `Thoughts/test_de_RESEARCH_angles.json` sidecar behind wherever
    pytest was invoked from. Callers now pass a `tmp_path`-rooted absolute
    path so any sidecar write lands inside pytest's own temp dir instead."""
    return {
        "r0_intake": {
            "research_file_path": research_file_path,
            "caller_skill": "/research",
            "downstream_tool": "~/.claude/skills/research/SKILL.md",
            "caller_session_id": "s4r5-de",
            "user_approved_scope": True,
            "scope": {"angles": ["heat pump payback in cold climates"]},
            "scope_provenance": "fresh-answer",
        },
        "r1_scope": {"search_scope": "heat pump payback (de)"},
        "r2_research": {"sources_count": 3},
        "r3_synthesis": {"claims_count": 5},
        "r4_factcheck": {"verdict": "engine_running"},
        "r5_recommend": {"recommendation_written": True},
    }


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


def _advance_cli(sid, checkpoint, payload, cycle_id=CYCLE_ID):
    """Invoke the CLI exactly as SKILL.md's fixed templates now do: a
    positional JSON payload plus a trailing `--cycle-id`."""
    argv = [
        "research_pipeline.py", "advance", sid, checkpoint, json.dumps(payload),
    ]
    if cycle_id is not None:
        argv += ["--cycle-id", cycle_id]
    return rp.main(argv)


def test_major1_non_default_cycle_advances_end_to_end(tmp_state, tmp_path, monkeypatch):
    """The full RESEARCH_SEQUENCE, advanced under cycle 'de' with --cycle-id
    at every step — the exact shape SKILL.md's fixed blocks now ship. Before
    the fix this failed at the SECOND checkpoint (see the sibling test
    below); after the fix every checkpoint registers under 'de' and the
    cycle completes."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r5-de-sid"
    payloads = _payloads(str(tmp_path / "test_de_RESEARCH.md"))

    for checkpoint in rp.RESEARCH_SEQUENCE:
        rc = _advance_cli(sid, checkpoint, payloads[checkpoint])
        assert rc == 0, f"{checkpoint} (cycle={CYCLE_ID}) exited {rc}, expected 0"

    state = rp._read_state(sid, tmp_state)
    assert state is not None
    assert rp.is_complete(state, cycle_id=CYCLE_ID), (
        f"cycle {CYCLE_ID!r} must be complete after advancing the full "
        f"sequence with --cycle-id at every step; missing="
        f"{rp.get_missing(state, cycle_id=CYCLE_ID)}"
    )
    cycle = state["cycles"][CYCLE_ID]
    assert cycle["r1_scope_approved"] is True
    assert set(cycle["checkpoints"]) == set(rp.RESEARCH_SEQUENCE)

    # `default` never received a single checkpoint — cycles stay
    # independently addressable, which the fix must not collapse.
    assert not rp.is_complete(state, cycle_id=rp.DEFAULT_CYCLE_ID)
    assert rp.get_missing(state, cycle_id=rp.DEFAULT_CYCLE_ID) == list(
        rp.RESEARCH_SEQUENCE
    )


def test_major1_repro_advancing_without_cycle_id_still_targets_default(
    tmp_state, tmp_path, monkeypatch, capsys
):
    """Pins the exact failure the fix closes, so a future change is caught
    against the SAME defect rather than a different one: register r0_intake
    under 'de', then advance r1_scope with NO --cycle-id (the pre-fix shape
    of SKILL.md's blocks). That call targets `default`'s empty sequence and
    raises a sequence violation naming r0_intake as expected — reproducing
    the orchestrator's `Sequence violation: expected r0_intake, got
    r1_scope. Completed: []` verbatim."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r5-repro-sid"
    payloads = _payloads(str(tmp_path / "test_de_RESEARCH.md"))

    rc = _advance_cli(sid, "r0_intake", payloads["r0_intake"], cycle_id=CYCLE_ID)
    assert rc == 0
    state = rp._read_state(sid, tmp_state)
    assert state["cycles"][CYCLE_ID]["r1_scope_approved"] is True
    capsys.readouterr()  # discard r0_intake's stdout so only r1_scope's remains

    rc = _advance_cli(sid, "r1_scope", payloads["r1_scope"], cycle_id=None)
    assert rc == 1, "advancing with no --cycle-id must not silently succeed"
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "expected r0_intake" in out["error"]
    assert "got r1_scope" in out["error"]

    # And the 'de' cycle itself is untouched — still holding only r0_intake.
    state = rp._read_state(sid, tmp_state)
    assert list(state["cycles"][CYCLE_ID]["checkpoints"]) == ["r0_intake"]

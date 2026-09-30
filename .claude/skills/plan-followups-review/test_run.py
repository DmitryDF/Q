#!/usr/bin/env python3
"""Regression suite for plan-followups-review/run.py.

Defends the four code-enforced walk-state invariants (A1):
  1. one-at-a-time hand-out
  2. no-silent-creation (accept without todo_ref is rejected)
  3. complete-coverage-before-DONE
  4. crash-safe resume

Plus edge cases (empty list, schema rejection, double-record, unknown id).

Exercises run.py ONLY, via subprocess, against a per-test temp state dir
(PLAN_FOLLOWUPS_REVIEW_STATE_DIR override). No live TODO.md writes, no network, no
subagent spawns — deterministic.

Run standalone:   python3 ~/.claude/skills/plan-followups-review/test_run.py
Run under pytest: pytest ~/.claude/skills/plan-followups-review/test_run.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

RUN_PY = str(Path(__file__).resolve().parent / "run.py")
SID = "test-session-walk"


def run_cmd(command, payload, state_dir):
    env = dict(os.environ)
    env["PLAN_FOLLOWUPS_REVIEW_STATE_DIR"] = str(state_dir)
    proc = subprocess.run(
        [sys.executable, RUN_PY, command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    try:
        out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError:
        out = {"_raw_stdout": proc.stdout, "_stderr": proc.stderr}
    return proc.returncode, out


def _fixture_observations(n=3):
    return [
        {"id": f"obs{i}", "title": f"Observation {i}", "body": f"Body of observation {i}."}
        for i in range(1, n + 1)
    ]


def _tmp_state_dir():
    return Path(tempfile.mkdtemp(prefix="plan-followups-review-test-"))


# --------------------------------------------------------------------------- #
# Invariant 1 — one-at-a-time hand-out
# --------------------------------------------------------------------------- #

def test_one_at_a_time_handout():
    sd = _tmp_state_dir()
    obs = _fixture_observations(3)
    code, out = run_cmd("init", {"session_id": SID, "observations": obs}, sd)
    assert code == 0 and out["status"] == "INITIALIZED", out
    assert out["total"] == 3 and out["undisposed"] == 3, out

    # next hands out exactly one, the first undisposed.
    code, out = run_cmd("next", {"session_id": SID}, sd)
    assert code == 0 and out["status"] == "OBSERVATION", out
    assert out["observation"]["id"] == "obs1", out
    assert out["remaining"] == 3 and out["position"] == 1, out
    # The payload carries a single observation object, never a batch list.
    assert isinstance(out["observation"], dict), out

    # Repeated next without a record re-hands the SAME observation (idempotent).
    code, out2 = run_cmd("next", {"session_id": SID}, sd)
    assert out2["observation"]["id"] == "obs1", out2

    # Record obs1, then next advances to obs2.
    run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                       "disposition": "discard"}, sd)
    code, out = run_cmd("next", {"session_id": SID}, sd)
    assert out["observation"]["id"] == "obs2" and out["position"] == 2, out


# --------------------------------------------------------------------------- #
# Invariant 2 — no silent creation
# --------------------------------------------------------------------------- #

def test_accept_without_todo_ref_rejected():
    sd = _tmp_state_dir()
    run_cmd("init", {"session_id": SID, "observations": _fixture_observations(2)}, sd)

    # accept with no todo_ref -> REJECTED, exit 1, NOT recorded.
    code, out = run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                                   "disposition": "accept"}, sd)
    assert code == 1 and out["status"] == "REJECTED", out
    assert "todo_ref" in out["reason"], out

    # empty/whitespace todo_ref also rejected.
    code, out = run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                                   "disposition": "accept", "todo_ref": "   "}, sd)
    assert code == 1 and out["status"] == "REJECTED", out

    # obs1 must still be undisposed (the reject did not write).
    code, out = run_cmd("next", {"session_id": SID}, sd)
    assert out["observation"]["id"] == "obs1", out

    # accept WITH a real todo_ref -> RECORDED.
    code, out = run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                                   "disposition": "accept",
                                   "todo_ref": "TODO.md:42"}, sd)
    assert code == 0 and out["status"] == "RECORDED", out
    assert out["todo_ref"] == "TODO.md:42", out


# --------------------------------------------------------------------------- #
# Invariant 3 — complete-coverage-before-DONE
# --------------------------------------------------------------------------- #

def test_complete_coverage_before_done():
    sd = _tmp_state_dir()
    run_cmd("init", {"session_id": SID, "observations": _fixture_observations(3)}, sd)

    # summary before all disposed -> INCOMPLETE, exit 2.
    run_cmd("record", {"session_id": SID, "obs_id": "obs1", "disposition": "discard"}, sd)
    code, out = run_cmd("summary", {"session_id": SID}, sd)
    assert code == 2 and out["status"] == "INCOMPLETE", out
    assert set(out["undisposed"]) == {"obs2", "obs3"}, out

    # dispose the rest, mixing all three disposition kinds.
    run_cmd("record", {"session_id": SID, "obs_id": "obs2",
                       "disposition": "accept", "todo_ref": "TODO.md:7"}, sd)
    run_cmd("record", {"session_id": SID, "obs_id": "obs3", "disposition": "edit"}, sd)

    code, out = run_cmd("summary", {"session_id": SID}, sd)
    assert code == 0 and out["status"] == "SUMMARY", out
    assert out["counts"] == {"accept": 1, "edit": 1, "discard": 1}, out
    assert len(out["dispositions"]) == 3, out

    # next now reports DONE.
    code, out = run_cmd("next", {"session_id": SID}, sd)
    assert code == 0 and out["status"] == "DONE", out


# --------------------------------------------------------------------------- #
# Invariant 4 — crash-safe resume
# --------------------------------------------------------------------------- #

def test_crash_safe_resume():
    sd = _tmp_state_dir()
    run_cmd("init", {"session_id": SID, "observations": _fixture_observations(3)}, sd)
    run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                       "disposition": "accept", "todo_ref": "TODO.md:1"}, sd)

    # Simulate a crash + resume: a brand-new process reads the same state dir.
    # next must resume at the next UNDISPOSED observation, never re-hand obs1.
    code, out = run_cmd("next", {"session_id": SID}, sd)
    assert out["observation"]["id"] == "obs2", out

    # Re-init in the same session resumes; it must NOT clobber the disposition.
    code, out = run_cmd("init", {"session_id": SID,
                                 "observations": _fixture_observations(3)}, sd)
    assert code == 0 and out["status"] == "RESUMED", out
    assert out["disposed"] == 1 and out["undisposed"] == 2, out

    # The preserved disposition still carries its todo_ref.
    code, out = run_cmd("summary", {"session_id": SID}, sd)
    # still incomplete (2 undisposed) but obs1 keeps its accept+todo_ref
    row = next(r for r in out["dispositions"] if r["id"] == "obs1")
    assert row["disposition"] == "accept" and row["todo_ref"] == "TODO.md:1", row


# --------------------------------------------------------------------------- #
# Edge cases
# --------------------------------------------------------------------------- #

def test_empty_observation_list():
    sd = _tmp_state_dir()
    code, out = run_cmd("init", {"session_id": SID, "observations": []}, sd)
    assert code == 0 and out["total"] == 0, out
    code, out = run_cmd("next", {"session_id": SID}, sd)
    assert code == 0 and out["status"] == "DONE", out
    code, out = run_cmd("summary", {"session_id": SID}, sd)
    assert code == 0 and out["status"] == "SUMMARY", out
    assert out["counts"] == {"accept": 0, "edit": 0, "discard": 0}, out


def test_schema_rejection():
    sd = _tmp_state_dir()
    # missing title
    code, out = run_cmd("init", {"session_id": SID,
                                 "observations": [{"id": "x", "body": "b"}]}, sd)
    assert code == 3 and out["status"] == "ERROR", out
    # duplicate ids
    code, out = run_cmd("init", {"session_id": SID, "observations": [
        {"id": "d", "title": "t", "body": "b"},
        {"id": "d", "title": "t2", "body": "b2"}]}, sd)
    assert code == 3 and "duplicate" in out["error"], out


def test_unknown_and_double_record_rejected():
    sd = _tmp_state_dir()
    run_cmd("init", {"session_id": SID, "observations": _fixture_observations(2)}, sd)
    # unknown obs_id
    code, out = run_cmd("record", {"session_id": SID, "obs_id": "nope",
                                   "disposition": "discard"}, sd)
    assert code == 1 and "unknown" in out["reason"], out
    # invalid disposition
    code, out = run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                                   "disposition": "maybe"}, sd)
    assert code == 1 and out["status"] == "REJECTED", out
    # record then double-record rejected
    run_cmd("record", {"session_id": SID, "obs_id": "obs1", "disposition": "discard"}, sd)
    code, out = run_cmd("record", {"session_id": SID, "obs_id": "obs1",
                                   "disposition": "discard"}, sd)
    assert code == 1 and "already disposed" in out["reason"], out


def test_missing_walk_and_usage_errors():
    sd = _tmp_state_dir()
    # next before init
    code, out = run_cmd("next", {"session_id": "never-initted"}, sd)
    assert code == 3 and out["status"] == "ERROR", out
    # bad subcommand
    env = dict(os.environ)
    env["PLAN_FOLLOWUPS_REVIEW_STATE_DIR"] = str(sd)
    proc = subprocess.run([sys.executable, RUN_PY, "bogus"], input="{}",
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 3, proc.stdout


ALL_TESTS = [
    test_one_at_a_time_handout,
    test_accept_without_todo_ref_rejected,
    test_complete_coverage_before_done,
    test_crash_safe_resume,
    test_empty_observation_list,
    test_schema_rejection,
    test_unknown_and_double_record_rejected,
    test_missing_walk_and_usage_errors,
]


def main():
    failures = 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(ALL_TESTS) - failures}/{len(ALL_TESTS)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Slice S2 — marker & concurrency integrity + both gate bugs (research-fc-checker-timeout).

Plan:  Thoughts/research-fc-checker-timeout_S2_PLAN.md (Mode A, S2).
Spine: Thoughts/research-fc-checker-timeout_THOUGHT.md + _DESIGN.md (Alt 1, A9/A10/A14/A15).

Covers:
  A9  _MARKER_SCHEMA_VERSION — single epoch constant; every marker writer stamps 3;
      no hardcoded `"schema_version": 2` literal remains (grandfather is read-side).
  A14 write_accept_marker / accept_research_incomplete — a distinct accepted-INCOMPLETE
      R-marker (verdict INCOMPLETE + non-empty accept_reason, round = max+1); empty reason rejected.
  A14 check-research-gate.sh — accepted-INCOMPLETE clears close; un-accepted INCOMPLETE
      still blocks; PASS + BYPASSED+reason unchanged; v2 markers grandfathered.
  A10 _append_research_frontmatter — concurrent writers serialize (flock) + unique-temp:
      intact merge under stress, idempotent, no leftover temp files.
  A15 factcheck_run debounce (manual=0 re-runs, auto=30 cools down) + LOCKED surfaces
      holder PID + start-time, with fail-safe 'holder unknown' on an empty sentinel.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parents[1]
ENGINE_SRC = (HOOKS_DIR / "_factcheck_engine.py").read_text(encoding="utf-8")


def _pass(dp, idx, m, rnd, prior):
    return "verdict: PASS\nclaims_checked: 1\n"


def _latest_marker(d):
    rounds = sorted(d.glob("R*.md"),
                    key=lambda p: int(re.search(r"R(\d+)", p.name).group(1)))
    assert rounds, "no R<N>.md written"
    return rounds[-1]


def _fm(path, key):
    m = re.search(rf"^{key}:\s*(.+?)\s*$",
                  path.read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else None


# ── A9 — single schema epoch constant ────────────────────────────────────────

class SchemaEpochTests(unittest.TestCase):
    def test_constant_is_3(self):
        self.assertEqual(eng._MARKER_SCHEMA_VERSION, 3)

    def test_no_v2_literal_remains_in_engine(self):
        # Guard rail: no second hardcoded literal survives (A9).
        self.assertNotIn('"schema_version": 2,', ENGINE_SRC)

    def test_round_file_stamps_v3(self):
        tmp = Path(_mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        rf = tmp / "R1.md"
        eng._write_round_file(
            rf, 1,
            [{"checker": 1, "model": "sonnet", "verdict": "verdict: PASS"}],
            "PASS", "research",
        )
        self.assertEqual(_fm(rf, "schema_version"), "3")


# ── A14 — accepted-INCOMPLETE marker writer ──────────────────────────────────

class AcceptMarkerTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(_mkdtemp())
        (self.d / "R1.md").write_text(
            "---\nschema_version: 3\nkind: research\nverdict: INCOMPLETE\n"
            "rounds: 1\nchecked_at: 2026-07-08T00:00:00+00:00\n---\n\n# r1\n",
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_writes_distinct_accepted_incomplete_next_round(self):
        p = eng.write_accept_marker(self.d, "budget genuinely exceeded — accepted")
        self.assertEqual(p.name, "R2.md")                       # round = max+1
        self.assertEqual(_fm(p, "verdict"), "INCOMPLETE")       # distinct, not BYPASSED
        self.assertEqual(_fm(p, "schema_version"), "3")
        self.assertIn("accepted", _fm(p, "accept_reason"))      # reason recorded

    def test_round_increments_over_multiple_markers(self):
        (self.d / "R2.md").write_text("---\nverdict: INCOMPLETE\n---\n", encoding="utf-8")
        p = eng.write_accept_marker(self.d, "accepted")
        self.assertEqual(p.name, "R3.md")

    def test_empty_reason_rejected(self):
        with self.assertRaises(ValueError):
            eng.write_accept_marker(self.d, "   ")
        with self.assertRaises(ValueError):
            eng.write_accept_marker(self.d, None)

    def test_accept_research_incomplete_resolves_dir_via_resolver(self):
        state_dir = Path(_mkdtemp())
        self.addCleanup(shutil.rmtree, state_dir, True)

        def resolver(sid):
            return ("projX", "topicX", {"ok": True})

        res = eng.accept_research_incomplete(
            state_dir, "sid-1", "accepted for audit",
            _proj_topic_resolver=resolver,
        )
        self.assertEqual(res["status"], "ACCEPTED")
        marker = Path(res["marker"])
        self.assertTrue(marker.exists())
        self.assertEqual(
            marker.parent, state_dir / "projX" / "topicX" / "research"
        )
        self.assertEqual(_fm(marker, "verdict"), "INCOMPLETE")


# ── A14 — check-research-gate.sh honors accepted-INCOMPLETE ───────────────────

class ResearchGateAcceptTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(_mkdtemp())
        self.sid = "s2s2s2s2-0000-0000-0000-000000000000"
        active = self.home / ".claude/state/pre_plan_gates"
        active.mkdir(parents=True)
        (active / "_active.json").write_text(json.dumps({
            self.sid: {"topic_slug": "proj", "active_project": "topic"}
        }), encoding="utf-8")
        self.rbase = self.home / ".claude/state/plan_validation/proj/topic/research"
        self.rbase.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _write(self, name, verdict, extra="", schema=3):
        self.rbase.joinpath(name).write_text(
            f"---\nschema_version: {schema}\nkind: research\nverdict: {verdict}\n"
            f"rounds: 1\n{extra}checked_at: 2026-07-08T00:00:00+00:00\n---\n\n# r\n",
            encoding="utf-8",
        )

    def _gate(self):
        env = dict(os.environ, HOME=str(self.home))
        env.pop("CLAUDE_CODE_REMOTE", None)
        return subprocess.run(
            ["bash", str(HOOKS_DIR / "check-research-gate.sh")],
            input=json.dumps({"session_id": self.sid, "stop_hook_active": False}),
            capture_output=True, text=True, env=env,
        )

    def test_accepted_incomplete_clears_close(self):
        self._write("R1.md", "INCOMPLETE")                          # un-accepted
        self._write("R2.md", "INCOMPLETE",
                    extra='accept_reason: "accepted — probe floor, ok"\n')  # accepted (latest)
        self.assertEqual(self._gate().returncode, 0)

    def test_unaccepted_incomplete_still_blocks(self):
        self._write("R1.md", "INCOMPLETE")
        r = self._gate()
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("non-PASS", r.stderr)

    def test_incomplete_with_empty_accept_reason_blocks(self):
        self._write("R1.md", "INCOMPLETE", extra='accept_reason: ""\n')
        self.assertEqual(self._gate().returncode, 2)

    def test_v2_pass_marker_grandfathered(self):
        self._write("R1.md", "PASS", schema=2)                      # old epoch
        self.assertEqual(self._gate().returncode, 0)

    def test_bypassed_with_reason_unchanged(self):
        self._write("R1.md", "BYPASSED", extra='bypass_reason: "all 3 timed out"\n')
        self.assertEqual(self._gate().returncode, 0)


# ── A10 — de-raced fc_cycles frontmatter writer ──────────────────────────────

class DeraceFrontmatterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(_mkdtemp())
        self.f = self.tmp / "x_RESEARCH.md"
        self.f.write_text("---\ntitle: x\n---\n\n# body\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _row(self, cycle):
        return {"cycle": cycle, "verdict": "PASS", "rounds": 1}

    def test_idempotent(self):
        eng._append_research_frontmatter(str(self.f), [self._row("default")])
        a = self.f.read_text(encoding="utf-8")
        eng._append_research_frontmatter(str(self.f), [self._row("default")])
        self.assertEqual(a, self.f.read_text(encoding="utf-8"))

    def test_concurrent_writers_all_rows_survive(self):
        cycles = [f"c{i}" for i in range(12)]
        barrier = threading.Barrier(len(cycles))

        def worker(c):
            barrier.wait()
            eng._append_research_frontmatter(str(self.f), [self._row(c)])

        threads = [threading.Thread(target=worker, args=(c,)) for c in cycles]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        text = self.f.read_text(encoding="utf-8")
        for c in cycles:
            self.assertIn(f'cycle: "{c}"', text, f"row {c} clobbered by a concurrent write")

    def test_no_leftover_temp_files(self):
        eng._append_research_frontmatter(str(self.f), [self._row("default")])
        leftovers = [p.name for p in self.tmp.iterdir() if ".tmp" in p.name]
        self.assertEqual(leftovers, [], f"leftover temp files: {leftovers}")


# ── A15 — debounce resolution + lock-holder visibility ───────────────────────

class DebounceAndLockHolderTests(unittest.TestCase):
    def setUp(self):
        self.state = Path(_mkdtemp())
        self.draft = self.state / "d_RESEARCH.md"
        # The fixture carries one citation (S12), for the same reason `_run` below
        # neutralizes the coverage axis: these tests assert debounce/lock
        # behaviour, and a research report citing nothing of any kind is now
        # downgraded to INCOMPLETE by the internal-citation axis (design-A22).
        # A citation is preferred to a third mock here — it keeps the real axis
        # running and shows it stays quiet on a well-formed citation, rather than
        # switching it off and learning nothing.
        self.draft.write_text(
            "# fixture\n\nA claim. [stated — local-file:Docs/x.md:1]\n",
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.state, ignore_errors=True)

    def _run(self, debounce):
        # Neutralize the S7 coverage axis — these tests assert debounce/lock
        # behavior on a minimal research fixture (no angle checklist), which the
        # coverage gate would otherwise fold to INCOMPLETE. Out of scope here.
        with mock.patch.object(eng, "_run_coverage_axis_gate",
                               side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict):
            return eng.factcheck_run(
                str(self.state), str(self.draft), "research", "sid-a",
                debounce_seconds=debounce, models=["sonnet"],
                _checker_fn=_pass, proj="p", topic="t",
            )

    def test_manual_debounce_zero_reruns(self):
        self.assertEqual(self._run(0)["status"], "PASS")
        self.assertEqual(self._run(0)["status"], "PASS")   # no cooldown suppression

    def test_auto_debounce_cools_down(self):
        self.assertEqual(self._run(30)["status"], "PASS")
        self.assertEqual(self._run(30)["status"], "DEBOUNCED")  # within 30s window

    def test_debounced_message_names_last_dispatch(self):
        self._run(30)
        self.assertIn("debounce window", self._run(30)["message"])

    def test_locked_surfaces_holder_pid_and_start_time(self):
        lock_dir = self.state / "p" / "t" / "research"
        lock_dir.mkdir(parents=True)
        lockfile = lock_dir / ".lock"
        lockfile.touch()
        with open(lockfile, "r+") as held:
            eng._write_lock_holder(held, 424242)
            fcntl.flock(held, fcntl.LOCK_EX)          # a rival holds the topic lock
            res = self._run(0)
        self.assertEqual(res["status"], "LOCKED")
        self.assertIn("424242", res["message"])
        self.assertIn("PID", res["message"])

    def test_locked_holder_unknown_is_fail_safe(self):
        self.assertIsNone(eng._read_lock_holder(self.state / "missing.lock"))
        self.assertIn("holder unknown", eng._locked_message(None))
        self.assertIn("holder unknown",
                      eng._locked_message({"started_at": "x"}))  # no pid → unknown


# ── small helpers (avoid tempfile import churn in the fixtures above) ─────────

import tempfile  # noqa: E402
from contextlib import contextmanager  # noqa: E402


def _mkdtemp():
    return tempfile.mkdtemp()


@contextmanager
def _tmpdir():
    d = tempfile.mkdtemp()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

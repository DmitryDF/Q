#!/usr/bin/env python3
"""streamed-dancing-goose S4 (A4) — lock liveness is a fact about a running process.

The validation gate, one test per clause (plan `## Coherent Actions` row A4):

  G1  a lock held by a LIVE process is not taken at any age
  G2  a recycled pid does not read as live
  G3  a lock whose process is gone is reclaimed, and no lock is unreclaimable
      (DEAD → now; UNKNOWN → past the ceiling; LEGACY → the pre-S4 STALE_T path)
  G4  the ship detector's verdict is unchanged by a heartbeat refresh
  G5  a blanket-quarantine attempt fails the suite (the sweep predicate is pinned:
      legacy / alive / unknown / own / fresh-heartbeat payloads are never moved)
  G6  the running session's lock is never moved
  G7  re-running the sweep after a partial failure moves nothing twice
  G8  a backup never overwrites an existing backup (exclusive-create, both paths)
  G9  settings.json: every registered hook command resolves to an existing
      executable (the canonical pre-check does not look — see the note there)
  G10 the corrected force-release command releases a held lock and reports
      failure (exit 1) when it does not — a bare topic slug no longer exits 0
  G11 settings.json protections, IN CODE (hooks/settings_hooks_registrar.py):
      a timestamped copy is taken first and recorded as THE restore point; a
      re-run takes no second copy; a missing or non-executable script refuses
      the registration before anything is written; the diff is exactly the
      registrations; `restore` puts the recorded copy back byte-for-byte; and
      `register_then_check` restores automatically when the caller's canonical
      check fails — the restore is exercised here, not assumed

Plus the identity/probe contract and the three hook scripts as real subprocesses.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_s4_lock_liveness.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
CONFIG_DIR = HOOKS.parent
sys.path.insert(0, str(HOOKS))

import taskmanagement as tm  # noqa: E402
import work_done as wd  # noqa: E402
import settings_hooks_registrar as reg  # noqa: E402

OLD = "2020-01-01T00:00:00+00:00"


def _iso_ago(seconds: int) -> str:
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


class _Sandbox:
    """Redirect tm.LOCKS_DIR / RELEASES_LOG to a tempdir (and export TM_LOCKS_DIR
    for subprocess hook tests)."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s4_locks_"))
        self._orig = (tm.LOCKS_DIR, tm.RELEASES_LOG)
        tm.LOCKS_DIR = self.tmp
        tm.RELEASES_LOG = self.tmp / "_releases.jsonl"
        self._env = os.environ.get("TM_LOCKS_DIR")
        os.environ["TM_LOCKS_DIR"] = str(self.tmp)
        return self

    def __exit__(self, *exc):
        tm.LOCKS_DIR, tm.RELEASES_LOG = self._orig
        if self._env is None:
            os.environ.pop("TM_LOCKS_DIR", None)
        else:
            os.environ["TM_LOCKS_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plant(self, key: str, sid: str, *, pid=None, pid_start=None,
              heartbeat_age_s=0, started_age_s=0, legacy=False) -> Path:
        payload = {
            "session_id": sid,
            "pid": pid if pid is not None else 12345,
            "started_at": _iso_ago(started_age_s),
            "last_heartbeat": _iso_ago(heartbeat_age_s),
        }
        if not legacy:
            payload["pid_start"] = pid_start if pid_start is not None else "Thu Jan  1 00:00:00 1970"
            payload["identity"] = tm.LOCK_IDENTITY_VERSION
        p = self.tmp / f"{key}.lock"
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return p

    def audit(self) -> list[dict]:
        if not tm.RELEASES_LOG.exists():
            return []
        return [json.loads(l) for l in tm.RELEASES_LOG.read_text().splitlines() if l.strip()]

    def backups(self) -> list[Path]:
        return sorted(self.tmp.glob("*.lock.bak-*"))


class _FakeSession:
    """A live process whose argv[0] basename is `claude` (a symlink to sleep), so
    `_is_claude_process` accepts it and `ps` reports a stable start time."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s4_fake_claude_"))
        link = self.tmp / "claude"
        link.symlink_to(shutil.which("sleep") or "/bin/sleep")
        self.proc = subprocess.Popen([str(link), "300"])
        self.pid = self.proc.pid
        # ps needs a moment on a freshly forked process for lstart to settle.
        for _ in range(50):
            self.start = tm._process_start(self.pid)
            if self.start:
                break
            time.sleep(0.02)
        return self

    def kill(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def __exit__(self, *exc):
        if self.proc.poll() is None:
            self.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)


def _dead_pid() -> int:
    p = subprocess.Popen(["sleep", "0"])
    p.wait()
    return p.pid


# ---------------------------------------------------------------------------
# Identity + probe contract
# ---------------------------------------------------------------------------

class IdentityAndProbe(unittest.TestCase):

    def setUp(self):
        tm._IDENTITY_CACHE.clear()

    def tearDown(self):
        tm._IDENTITY_CACHE.clear()

    def test_identity_is_cached_per_process_and_env(self):
        with _FakeSession() as fs, mock.patch.dict(os.environ, {"CLAUDE_PID": str(fs.pid)}):
            first = tm.session_process_identity()
            with mock.patch.object(tm, "_ps_describe", side_effect=AssertionError("ps must not run")):
                second = tm.session_process_identity()
            self.assertEqual(first, second)

    def test_ps_describe_splits_lstart_positionally(self):
        with _FakeSession() as fs:
            ppid, lstart, args = tm._ps_describe(fs.pid)
            self.assertEqual(int(ppid), os.getpid())
            self.assertEqual(lstart, fs.start)
            self.assertTrue(args.endswith("claude 300"), args)

    def test_verified_env_pid_is_the_session_process(self):
        with _FakeSession() as fs, mock.patch.dict(os.environ, {"CLAUDE_PID": str(fs.pid)}):
            ident = tm.session_process_identity()
            self.assertEqual(ident["pid"], fs.pid)
            self.assertEqual(ident["pid_start"], fs.start)
            self.assertEqual(ident["source"], "env")
            self.assertEqual(ident["identity"], tm.LOCK_IDENTITY_VERSION)

    def test_env_pid_that_is_not_claude_is_ignored(self):
        sleeper = subprocess.Popen(["sleep", "300"])
        try:
            with mock.patch.dict(os.environ, {"CLAUDE_PID": str(sleeper.pid)}):
                ident = tm.session_process_identity()
                self.assertNotEqual(ident["pid"], sleeper.pid)
                self.assertIn(ident["source"], ("ancestry", "self"))
        finally:
            sleeper.terminate(); sleeper.wait()

    def test_no_session_falls_back_to_self_and_records_a_dead_on_exit_identity(self):
        with mock.patch.dict(os.environ, {"CLAUDE_PID": ""}), \
             mock.patch.object(tm, "_is_claude_args", lambda args: False):
            ident = tm.session_process_identity()
            self.assertEqual(ident["pid"], os.getpid())
            self.assertEqual(ident["source"], "self")
            self.assertTrue(ident["pid_start"])

    def test_probe_alive_dead_recycled_unknown_legacy(self):
        with _FakeSession() as fs:
            alive = {"pid": fs.pid, "pid_start": fs.start, "identity": tm.LOCK_IDENTITY_VERSION}
            self.assertEqual(tm.probe_liveness(alive), tm.LIVENESS_ALIVE)
            recycled = dict(alive, pid_start="Thu Jan  1 00:00:00 1970")
            self.assertEqual(tm.probe_liveness(recycled), tm.LIVENESS_DEAD)
            with mock.patch.object(tm, "_process_start", lambda pid: None):
                self.assertEqual(tm.probe_liveness(alive), tm.LIVENESS_UNKNOWN)
            fs.kill()
            self.assertEqual(tm.probe_liveness(alive), tm.LIVENESS_DEAD)
        self.assertEqual(tm.probe_liveness({"pid": 12345, "pid_start": "x"}), tm.LIVENESS_LEGACY)
        self.assertEqual(tm.probe_liveness({"pid": 12345, "identity": "v"}), tm.LIVENESS_LEGACY)
        self.assertEqual(tm.probe_liveness({"pid": "nope", "pid_start": "x",
                                            "identity": "v"}), tm.LIVENESS_UNKNOWN)

    def test_reentry_upgrades_a_legacy_payload_of_the_same_session(self):
        with _Sandbox() as sb:
            sb.plant("t__p", "sess-A", legacy=True, heartbeat_age_s=500)
            res = tm.acquire_lock("t__p", "sess-A")
            self.assertEqual(res["status"], "ALREADY_HELD_BY_SELF")
            self.assertTrue(tm.payload_has_identity(res["holder"]))
            on_disk = json.loads((sb.tmp / "t__p.lock").read_text())
            self.assertTrue(tm.payload_has_identity(on_disk))
            self.assertNotEqual(tm.probe_liveness(on_disk), tm.LIVENESS_LEGACY)

    def test_acquire_records_the_s4_identity(self):
        with _Sandbox():
            res = tm.acquire_lock("t__p", "sess-A")
            self.assertEqual(res["status"], "ACQUIRED")
            h = res["holder"]
            self.assertTrue(tm.payload_has_identity(h))
            self.assertIn(h["pid_source"], ("env", "ancestry", "self"))
            self.assertIn("started_at", h)
            self.assertIn("last_heartbeat", h)


# ---------------------------------------------------------------------------
# G1–G3 — the lazy acquire policy
# ---------------------------------------------------------------------------

class AcquirePolicy(unittest.TestCase):

    def test_G1_live_holder_is_not_taken_at_any_age(self):
        with _Sandbox() as sb, _FakeSession() as fs:
            sb.plant("t__p", "sess-A", pid=fs.pid, pid_start=fs.start,
                     heartbeat_age_s=10 * 24 * 3600, started_age_s=10 * 24 * 3600)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "HELD_BY_OTHER")
            self.assertEqual(res["liveness"], tm.LIVENESS_ALIVE)
            self.assertEqual(res["holder"]["session_id"], "sess-A")
            self.assertEqual(sb.audit(), [])

    def test_G1b_a_synthetic_id_maintenance_caller_cannot_steal_a_live_holder(self):
        with _Sandbox() as sb, _FakeSession() as fs:
            sb.plant("t__p", "sess-A", pid=fs.pid, pid_start=fs.start,
                     heartbeat_age_s=8 * 3600)
            for synthetic in ("reconcile-4242", "reconcile-inverted-4242", "prune-active-4242"):
                res = tm.acquire_lock("t__p", synthetic)
                self.assertEqual(res["status"], "HELD_BY_OTHER", synthetic)
            self.assertTrue((sb.tmp / "t__p.lock").exists())

    def test_G2_recycled_pid_does_not_read_as_live(self):
        with _Sandbox() as sb, _FakeSession() as fs:
            sb.plant("t__p", "sess-A", pid=fs.pid, pid_start="Thu Jan  1 00:00:00 1970",
                     heartbeat_age_s=5)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "ACQUIRED")
            rows = sb.audit()
            self.assertEqual(rows[0]["reason"], "dead-holder-release")
            self.assertEqual(rows[0]["liveness"], tm.LIVENESS_DEAD)

    def test_G3_dead_holder_is_reclaimed_now_and_moved_aside(self):
        with _Sandbox() as sb:
            sb.plant("t__p", "sess-A", pid=_dead_pid(), pid_start="whatever", heartbeat_age_s=1)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "ACQUIRED")
            self.assertEqual(res["holder"]["session_id"], "sess-B")
            self.assertEqual(len(sb.backups()), 1, "reclaimed payload must be moved aside, not deleted")
            row = sb.audit()[0]
            self.assertEqual(row["reason"], "dead-holder-release")
            self.assertEqual(row["moved_to"], str(sb.backups()[0]))
            self.assertEqual(json.loads(sb.backups()[0].read_text())["session_id"], "sess-A")

    def test_G3b_unknown_liveness_is_reclaimed_only_past_the_ceiling(self):
        with _Sandbox() as sb, _FakeSession() as fs, \
             mock.patch.object(tm, "_process_start", lambda pid: None), \
             mock.patch.dict(os.environ, {"TM_LIVENESS_CEILING_SECONDS": "100"}):
            sb.plant("t__p", "sess-A", pid=fs.pid, pid_start=fs.start, heartbeat_age_s=50)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "HELD_BY_OTHER")
            self.assertEqual(res["liveness"], tm.LIVENESS_UNKNOWN)
            sb.plant("t__p", "sess-A", pid=fs.pid, pid_start=fs.start, heartbeat_age_s=150)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "ACQUIRED")
            self.assertEqual(sb.audit()[-1]["reason"], "unconfirmable-ceiling-release")

    def test_G3c_legacy_payload_keeps_the_pre_s4_stale_path(self):
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "60"}):
            sb.plant("t__p", "sess-A", legacy=True, heartbeat_age_s=30)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "HELD_BY_OTHER")
            self.assertEqual(res["liveness"], tm.LIVENESS_LEGACY)
            sb.plant("t__p", "sess-A", legacy=True, heartbeat_age_s=120)
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "ACQUIRED")
            self.assertEqual(sb.audit()[-1]["reason"], "stale-auto-release")

    def test_G3e_corrupt_payload_is_reclaimable_not_a_crash(self):
        """C9: no shape is unreclaimable. A `.lock` that is not JSON (or not an
        object) names no holder — acquire moves it aside and proceeds; a forced
        release moves it aside; an unforced release / refresh refuse gracefully."""
        with _Sandbox() as sb:
            p = sb.tmp / "t__p.lock"
            p.write_text("{not json", encoding="utf-8")
            res = tm.acquire_lock("t__p", "sess-B")
            self.assertEqual(res["status"], "ACQUIRED")
            self.assertEqual(sb.audit()[0]["reason"], "corrupt-payload-release")
            self.assertEqual(len(sb.backups()), 1)
            p.write_text("[1, 2, 3]", encoding="utf-8")  # JSON but not an object
            self.assertEqual(tm.refresh_heartbeat("t__p", "sess-B")["status"], "NOT_HOLDER")
            self.assertEqual(tm.release_lock("t__p", session_id="sess-B")["status"], "NOT_HOLDER")
            self.assertTrue(p.exists(), "an unforced release never moves a payload it cannot read")
            res = tm.release_lock("t__p", force=True)
            self.assertEqual(res["status"], "RELEASED")
            self.assertFalse(p.exists())
            self.assertEqual(sb.audit()[-1]["reason"], "corrupt-payload-release")

    def test_G3d_default_ceiling_is_a_day_and_never_governs_a_live_holder(self):
        self.assertEqual(tm._liveness_ceiling_seconds(), 24 * 3600)
        with _Sandbox() as sb, _FakeSession() as fs:
            sb.plant("t__p", "sess-A", pid=fs.pid, pid_start=fs.start,
                     heartbeat_age_s=3 * 24 * 3600)
            self.assertEqual(tm.acquire_lock("t__p", "sess-B")["status"], "HELD_BY_OTHER")


# ---------------------------------------------------------------------------
# G4 — the ship detector reads start time, not liveness
# ---------------------------------------------------------------------------

class ShipDetectorIndependence(unittest.TestCase):

    def _detect(self):
        return wd.detect_ship_event(topic_slug="t", project_slug="p", plan_path=None,
                                    session_id="sess-A", started_at="2026-01-01",
                                    ended_at="2026-01-02")

    def test_G4_refresh_does_not_change_the_verdict(self):
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "5"}):
            sb.plant("t__p", "sess-A", heartbeat_age_s=30, started_age_s=30)
            self.assertFalse(self._detect()["lock_fresh"])
            self.assertEqual(tm.refresh_heartbeat("t__p", "sess-A")["status"], "REFRESHED")
            self.assertFalse(self._detect()["lock_fresh"],
                             "a per-turn refresh must not turn a stale acquisition fresh")
            sb.plant("t__p", "sess-A", heartbeat_age_s=30 * 24 * 3600, started_age_s=1)
            self.assertTrue(self._detect()["lock_fresh"],
                            "an ancient heartbeat must not stale a fresh acquisition")
            tm.refresh_heartbeat("t__p", "sess-A")
            self.assertTrue(self._detect()["lock_fresh"])

    def test_G4b_session_locks_freshness_follows_the_same_reader(self):
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "5"}):
            sb.plant("t__p", "sess-A", heartbeat_age_s=0, started_age_s=30)
            locks = wd.session_locks("sess-A")
            self.assertEqual(len(locks), 1)
            self.assertFalse(locks[0]["fresh"])


# ---------------------------------------------------------------------------
# G5–G8 — the sweep predicate and the move-aside discipline
# ---------------------------------------------------------------------------

class SweepPredicate(unittest.TestCase):

    def test_G5_blanket_quarantine_is_refused_by_the_predicate(self):
        with _Sandbox() as sb, _FakeSession() as fs, \
             mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "60"}):
            sb.plant("own__p", "me", pid=_dead_pid(), heartbeat_age_s=999)
            sb.plant("legacy__p", "old", legacy=True, heartbeat_age_s=999)
            sb.plant("alive__p", "other", pid=fs.pid, pid_start=fs.start, heartbeat_age_s=999)
            sb.plant("fresh__p", "other", pid=_dead_pid(), heartbeat_age_s=10)
            sb.plant("dead__p", "other", pid=_dead_pid(), heartbeat_age_s=999)
            with mock.patch.object(tm, "probe_liveness",
                                   wraps=tm.probe_liveness) as probe:
                res = tm.sweep_dead_locks("me")
            self.assertEqual([s["topic_slug"] for s in res["swept"]], ["dead__p"])
            why = {s["topic_slug"]: s["why"] for s in res["skipped"]}
            self.assertEqual(why, {"own__p": "own-session", "legacy__p": "legacy-format",
                                   "alive__p": "liveness-alive", "fresh__p": "heartbeat-fresh"})
            self.assertEqual(res["errors"], [])
            for key in ("own__p", "legacy__p", "alive__p", "fresh__p"):
                self.assertTrue((sb.tmp / f"{key}.lock").exists(), key)
            self.assertFalse((sb.tmp / "dead__p.lock").exists())
            self.assertEqual(sb.audit()[0]["reason"], "dead-holder-sweep")
            # legacy payloads are never even probed — the handoff constraint
            probed = [c.args[0].get("session_id") for c in probe.call_args_list]
            self.assertNotIn("old", probed)

    def test_G5b_unknown_liveness_is_left_to_the_lazy_ceiling_not_the_sweep(self):
        with _Sandbox() as sb, _FakeSession() as fs, \
             mock.patch.object(tm, "_process_start", lambda pid: None), \
             mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "1",
                                          "TM_LIVENESS_CEILING_SECONDS": "1"}):
            sb.plant("unk__p", "other", pid=fs.pid, pid_start=fs.start, heartbeat_age_s=999)
            res = tm.sweep_dead_locks("me")
            self.assertEqual(res["swept"], [])
            self.assertEqual(res["skipped"][0]["why"], "liveness-unknown")

    def test_G6_running_sessions_lock_is_never_moved(self):
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "1"}):
            sb.plant("own__p", "me", pid=_dead_pid(), heartbeat_age_s=99999)
            res = tm.sweep_dead_locks("me")
            self.assertEqual(res["swept"], [])
            self.assertTrue((sb.tmp / "own__p.lock").exists())

    def test_G6b_own_process_is_skipped_even_when_no_session_id_is_known(self):
        """The skip is unconditional: a payload carrying THIS process's identity is
        never moved, whatever session_id the caller could or could not derive —
        even if the probe were to misread it (DEAD forced here)."""
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "1"}):
            me = tm.session_process_identity()
            sb.plant("mine__p", "some-other-sid", pid=me["pid"], pid_start=me["pid_start"],
                     heartbeat_age_s=99999)
            with mock.patch.object(tm, "probe_liveness", lambda payload: tm.LIVENESS_DEAD):
                res = tm.sweep_dead_locks(None)
            self.assertEqual(res["swept"], [])
            self.assertEqual(res["skipped"][0]["why"], "own-process")
            self.assertTrue((sb.tmp / "mine__p.lock").exists())

    def test_G7_partial_failure_then_rerun_moves_nothing_twice(self):
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "1"}):
            sb.plant("a__p", "other", pid=_dead_pid(), heartbeat_age_s=999)
            sb.plant("b__p", "other", pid=_dead_pid(), heartbeat_age_s=999)
            real = tm._move_aside

            def flaky(path):
                if path.name == "a__p.lock":
                    raise OSError(13, "EACCES")
                return real(path)

            with mock.patch.object(tm, "_move_aside", flaky):
                first = tm.sweep_dead_locks("me")
            self.assertEqual([s["topic_slug"] for s in first["swept"]], ["b__p"])
            self.assertEqual(len(first["errors"]), 1)
            self.assertTrue((sb.tmp / "a__p.lock").exists())
            second = tm.sweep_dead_locks("me")
            self.assertEqual([s["topic_slug"] for s in second["swept"]], ["a__p"])
            self.assertEqual(len(sb.backups()), 2, "each payload moved exactly once")
            third = tm.sweep_dead_locks("me")
            self.assertEqual(third["swept"], [])

    def test_G8_backup_never_overwrites_an_existing_backup(self):
        with _Sandbox() as sb:
            p = sb.plant("t__p", "sess-A", heartbeat_age_s=1)
            fixed = _dt.datetime(2026, 9, 20, 12, 0, 0, 123456, tzinfo=_dt.timezone.utc)
            with mock.patch.object(tm, "_now", lambda: fixed):
                taken = Path(f"{p}.bak-20260920120000123456-{os.getpid()}")
                taken.write_text("PRE-EXISTING", encoding="utf-8")
                dst = tm._move_aside(p)
            self.assertEqual(taken.read_text(), "PRE-EXISTING")
            self.assertEqual(dst, Path(f"{taken}-1"))
            self.assertFalse(p.exists())
            self.assertEqual(json.loads(dst.read_text())["session_id"], "sess-A")

    def test_G8b_both_former_unlink_sites_move_aside(self):
        with _Sandbox() as sb:
            tm.acquire_lock("t__p", "sess-A")
            res = tm.release_lock("t__p", session_id="sess-B", force=True)
            self.assertEqual(res["status"], "RELEASED")
            self.assertTrue(Path(res["moved_to"]).exists())
            self.assertEqual(sb.audit()[-1]["reason"], "manual-release-non-holder")
            sb.plant("u__p", "sess-A", pid=_dead_pid(), heartbeat_age_s=1)
            tm.acquire_lock("u__p", "sess-B")
            self.assertEqual(len(sb.backups()), 2)

    def test_release_session_locks_moves_only_mine(self):
        with _Sandbox() as sb:
            sb.plant("a__p", "me", heartbeat_age_s=1)
            sb.plant("b__p", "me", heartbeat_age_s=1)
            sb.plant("c__p", "other", heartbeat_age_s=1)
            res = tm.release_session_locks("me")
            self.assertEqual(sorted(r["topic_slug"] for r in res["released"]), ["a__p", "b__p"])
            self.assertTrue((sb.tmp / "c__p.lock").exists())
            self.assertEqual({r["reason"] for r in sb.audit()}, {"session-end-release"})

    def test_refresh_session_heartbeats_touches_only_mine_and_never_raises(self):
        with _Sandbox() as sb:
            sb.plant("a__p", "me", heartbeat_age_s=500)
            sb.plant("c__p", "other", heartbeat_age_s=500)
            res = tm.refresh_session_heartbeats("me")
            self.assertEqual(res["refreshed"], ["a__p"])
            mine = json.loads((sb.tmp / "a__p.lock").read_text())
            other = json.loads((sb.tmp / "c__p.lock").read_text())
            self.assertGreater(mine["last_heartbeat"], other["last_heartbeat"])
            with mock.patch.object(tm, "_iter_lock_payloads", side_effect=RuntimeError("boom")):
                self.assertEqual(tm.refresh_session_heartbeats("me")["status"], "OK")


# ---------------------------------------------------------------------------
# The three hook scripts, as real subprocesses against the sandbox
# ---------------------------------------------------------------------------

def _run_hook(name: str, stdin: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_REMOTE", None)
    return subprocess.run([str(HOOKS / name)], input=stdin, capture_output=True,
                          text=True, timeout=30, env=env)


class HookScripts(unittest.TestCase):

    def test_refresh_hook_is_silent_exit_0_and_refreshes(self):
        with _Sandbox() as sb:
            sb.plant("a__p", "sess-A", heartbeat_age_s=500)
            r = _run_hook("refresh-topic-locks.sh", json.dumps({"session_id": "sess-A"}))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "", "UserPromptSubmit stdout is injected into context — must be empty")
            hb = json.loads((sb.tmp / "a__p.lock").read_text())["last_heartbeat"]
            self.assertLess(tm._heartbeat_age_seconds({"last_heartbeat": hb}), 60)

    def test_refresh_hook_fails_open_on_garbage(self):
        with _Sandbox():
            r = _run_hook("refresh-topic-locks.sh", "not json at all")
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout, "")

    def test_release_hook_moves_this_sessions_locks_aside(self):
        with _Sandbox() as sb:
            sb.plant("a__p", "sess-A", heartbeat_age_s=1)
            sb.plant("c__p", "other", heartbeat_age_s=1)
            r = _run_hook("release-session-locks.sh", json.dumps({"session_id": "sess-A"}))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((sb.tmp / "a__p.lock").exists())
            self.assertTrue((sb.tmp / "c__p.lock").exists())
            self.assertEqual(len(sb.backups()), 1)

    def test_sweep_hook_reports_only_when_it_swept(self):
        with _Sandbox() as sb, mock.patch.dict(os.environ, {"TM_STALE_T_SECONDS": "1"}):
            r = _run_hook("sweep-dead-locks.sh", json.dumps({"session_id": "me"}))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "")
            sb.plant("dead__p", "other", pid=_dead_pid(), heartbeat_age_s=999)
            sb.plant("legacy__p", "old", legacy=True, heartbeat_age_s=999)
            r = _run_hook("sweep-dead-locks.sh", json.dumps({"session_id": "me"}))
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("moved aside 1 lock", r.stdout)
            self.assertIn("dead__p", r.stdout)
            self.assertTrue((sb.tmp / "legacy__p.lock").exists())

    def test_G9_every_registered_hook_command_resolves(self):
        """The canonical pre-check (`claude-verify --phase pre`) verifies that the
        files under hooks/*.sh are executable, NOT that every command registered
        in settings.json resolves — a registered-but-missing hook passes it
        (measured 2026-09-20 during S4: a settings.json naming
        `${KIT_HOOKS_DIR}/does-not-exist-s4.sh` passed with rc 0).
        This test is the check the plan's guard rail assumed existed."""
        doc = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
        missing = []
        for event, groups in doc.get("hooks", {}).items():
            for g in groups:
                for h in g.get("hooks", []):
                    cmd = h.get("command", "")
                    if not cmd:
                        continue
                    exe = cmd.split()[0]
                    if exe in ("python3", "python", "bash", "sh"):
                        exe = cmd.split()[1] if len(cmd.split()) > 1 else exe
                    # resolve the live-absolute registration inside the tree under test
                    exe = exe.replace(str(Path.home() / ".claude"), str(CONFIG_DIR), 1)
                    p = Path(os.path.expanduser(exe))
                    if not p.is_file() or (p.suffix == ".sh" and not os.access(p, os.X_OK)):
                        missing.append(f"{event}: {cmd}")
        self.assertEqual(missing, [])

    def test_G9b_the_three_s4_hooks_are_registered_on_their_events(self):
        doc = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))

        def cmds(event):
            return [h.get("command", "") for g in doc["hooks"].get(event, []) for h in g.get("hooks", [])]

        self.assertTrue(any(c.endswith("/refresh-topic-locks.sh") for c in cmds("UserPromptSubmit")))
        self.assertTrue(any(c.endswith("/sweep-dead-locks.sh") for c in cmds("SessionStart")))
        self.assertTrue(any(c.endswith("/release-session-locks.sh") for c in cmds("SessionEnd")))
        # never on a blocking tool event
        for ev in ("PreToolUse", "PostToolUse", "Stop"):
            self.assertFalse(any("-locks.sh" in c for c in cmds(ev)), ev)


# ---------------------------------------------------------------------------
# G10 — the corrected force-release recovery command
# ---------------------------------------------------------------------------

class ReleaseCli(unittest.TestCase):

    def _cli(self, *args):
        return subprocess.run([sys.executable, str(HOOKS / "taskmanagement.py"), *args],
                              capture_output=True, text=True, timeout=30,
                              env=dict(os.environ), stdin=subprocess.DEVNULL)

    def test_G10_composite_key_releases_and_bare_slug_reports_failure(self):
        with _Sandbox() as sb:
            tm.acquire_lock("topic__Root", "sess-A")
            bare = self._cli("release", "topic", "--force", "--yes")
            self.assertEqual(bare.returncode, 1, bare.stdout + bare.stderr)
            out = json.loads(bare.stdout)
            self.assertEqual(out["status"], "NOT_HELD")
            self.assertEqual(out["candidates"], ["topic__Root"])
            self.assertTrue((sb.tmp / "topic__Root.lock").exists(), "a miss must release nothing")
            full = self._cli("release", "topic__Root", "--force", "--yes")
            self.assertEqual(full.returncode, 0, full.stdout + full.stderr)
            self.assertEqual(json.loads(full.stdout)["status"], "RELEASED")
            self.assertFalse((sb.tmp / "topic__Root.lock").exists())
            self.assertEqual(len(sb.backups()), 1)

    def test_G10b_recovery_command_reports_a_corrupt_payload_and_can_retire_it(self):
        """The documented CLI path, not only the library: a corrupt `.lock` must
        yield the report contract (exit 1 NOT_HOLDER without --force; RELEASED with
        --force --yes), never a traceback — and `read_lock` returns None for it."""
        with _Sandbox() as sb:
            p = sb.tmp / "topic__Root.lock"
            p.write_text("{not json", encoding="utf-8")
            self.assertIsNone(tm.read_lock("topic__Root"))
            r = self._cli("release", "topic__Root", "--session-id", "sess-A")
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            self.assertEqual(json.loads(r.stdout)["status"], "NOT_HOLDER")
            self.assertTrue(p.exists())
            r = self._cli("release", "topic__Root", "--force", "--yes")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(json.loads(r.stdout)["status"], "RELEASED")
            self.assertFalse(p.exists())
            self.assertEqual(sb.audit()[-1]["reason"], "corrupt-payload-release")
            p.write_text("[1, 2]", encoding="utf-8")  # JSON, not an object
            r = self._cli("release", "topic__Root", "--force", "--yes")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            r = self._cli("status")
            self.assertEqual(r.returncode, 0)

    def test_status_shows_liveness(self):
        with _Sandbox() as sb:
            sb.plant("legacy__p", "old", legacy=True)
            sb.plant("dead__p", "other", pid=_dead_pid())
            r = self._cli("status")
            rows = {row["topic_slug"]: row["liveness"] for row in json.loads(r.stdout)}
            self.assertEqual(rows, {"legacy__p": tm.LIVENESS_LEGACY, "dead__p": tm.LIVENESS_DEAD})

    def test_work_start_skill_documents_the_corrected_command(self):
        text = (CONFIG_DIR / "skills" / "work-start" / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue("release TOPIC_SLUG__PROJECT_SLUG --force --yes" in text,
                        "work-start SKILL.md must name the composite-key recovery command")
        self.assertFalse("release TOPIC_SLUG --force`" in text,
                         "the bare-slug recovery command must be gone")
        self.assertFalse("it will prompt for confirmation on a TTY" in text,
                         "the stale prompt note must be gone (--yes suppresses it)")


# ---------------------------------------------------------------------------
# G11 — the shared-config protections, exercised in code
# ---------------------------------------------------------------------------

_PRE_EDIT_SETTINGS = {
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "/x/hooks/log-prompt.sh"}]}],
        "SessionEnd": [{"hooks": [{"type": "command", "command": "/x/hooks/cleanup-plan-files.sh"}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/x/hooks/guard.sh"}]}],
    },
    "note": "em dash — kept as an escape",
}


class Registrar(unittest.TestCase):
    """G11. The load-bearing paths of hooks/settings_hooks_registrar.py — not
    every branch; see that module's own docstring for what remains untested —
    against a temp file whose shape (and `\\u2014` escaping) mirrors the live
    settings.json."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s4_registrar_"))
        self.settings = self.tmp / "settings.json"
        self.original = json.dumps(_PRE_EDIT_SETTINGS, indent=2, ensure_ascii=True) + "\n"
        self.settings.write_text(self.original, encoding="utf-8")
        self.scripts = self.tmp / "hooks"
        self.scripts.mkdir()
        for name in ("refresh-topic-locks.sh", "sweep-dead-locks.sh", "release-session-locks.sh"):
            p = self.scripts / name
            p.write_text("#!/bin/bash\nexit 0\n")
            p.chmod(0o755)
        self.regs = [("UserPromptSubmit", "/x/hooks/refresh-topic-locks.sh"),
                     ("SessionStart", "/x/hooks/sweep-dead-locks.sh"),
                     ("SessionEnd", "/x/hooks/release-session-locks.sh")]

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _backups(self):
        return sorted(self.tmp.glob("settings.json.bak-*"))

    def test_G11_copy_first_recorded_once_diff_exact_and_restore_byte_identical(self):
        res = reg.register(self.settings, self.regs, check_dir=self.scripts, stamp="20260920T1")
        self.assertEqual(res["status"], "registered", res)
        self.assertEqual(len(self._backups()), 1)
        backup = self._backups()[0]
        self.assertEqual(res["restore_point"], str(backup))
        self.assertEqual(backup.read_text(encoding="utf-8"), self.original, "the copy is the PRE-edit file")
        self.assertEqual(reg.read_restore_point(self.settings), backup, "and its path is recorded")
        # the diff is exactly the three registrations, escaping preserved
        after = self.settings.read_text(encoding="utf-8")
        self.assertIn("\\u2014", after, "ascii escaping preserved")
        doc = json.loads(after)
        self.assertEqual([h["command"] for h in doc["hooks"]["SessionStart"][0]["hooks"]],
                         ["/x/hooks/sweep-dead-locks.sh"])
        self.assertEqual([h["command"] for h in doc["hooks"]["SessionEnd"][0]["hooks"]],
                         ["/x/hooks/cleanup-plan-files.sh", "/x/hooks/release-session-locks.sh"])
        self.assertEqual(doc["hooks"]["PreToolUse"], _PRE_EDIT_SETTINGS["hooks"]["PreToolUse"])
        added = [l for l in after.splitlines() if l not in self.original.splitlines()]
        self.assertEqual(sorted(l.strip() for l in added if "command" in l),
                         sorted(f'"command": "{c}"' for _, c in self.regs))
        # re-run: idempotent, NO second copy, restore point unchanged
        again = reg.register(self.settings, self.regs, check_dir=self.scripts, stamp="20260920T2")
        self.assertEqual(again["status"], "already-registered")
        self.assertEqual(len(self._backups()), 1)
        self.assertEqual(reg.read_restore_point(self.settings), backup)
        # restore: exercised, byte-identical to the pre-edit file
        r = reg.restore(self.settings)
        self.assertEqual(r["status"], "restored")
        self.assertEqual(r["from"], str(backup))
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original)
        self.assertTrue(Path(r["replaced_copy"]).exists(), "a restore is itself reversible")

    def test_G11b_missing_or_non_executable_script_refuses_before_any_write(self):
        (self.scripts / "sweep-dead-locks.sh").unlink()
        res = reg.register(self.settings, self.regs, check_dir=self.scripts)
        self.assertEqual(res["status"], "refused")
        self.assertIn("does not exist", res["refused_reason"])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original, "nothing written")
        self.assertEqual(self._backups(), [], "no copy taken on refusal")
        self.assertIsNone(reg.read_restore_point(self.settings))
        (self.scripts / "sweep-dead-locks.sh").write_text("#!/bin/bash\nexit 0\n")  # mode 644
        res = reg.register(self.settings, self.regs, check_dir=self.scripts)
        self.assertEqual(res["status"], "refused")
        self.assertIn("not executable", res["refused_reason"])
        self.assertEqual(self._backups(), [])

    def test_G11c_restore_refuses_without_a_recorded_point_and_never_restores_garbage(self):
        self.assertEqual(reg.restore(self.settings)["status"], "refused")
        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        r = reg.restore(self.settings, backup=bad)
        self.assertEqual(r["status"], "refused", "an unparseable copy is refused, not raised")
        self.assertIn("unparseable", r["reason"])
        self.assertEqual(reg.restore(self.settings, backup=self.tmp / "absent.json")["status"], "refused")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original)

    def test_G11f_register_then_check_restores_when_the_check_fails(self):
        """The coupling the gate names: register → canonical check → restore on
        failure, as one code path. A failing check (rc 1) puts the pre-edit bytes
        back; a passing check leaves the registration in place."""
        class _Proc:
            def __init__(self, rc): self.returncode = rc
        seen = []
        failing = lambda argv: (seen.append(argv), _Proc(1))[1]
        res = reg.register_then_check(self.settings, self.regs, check_cmd=["fake-verify", "--phase", "pre"],
                                      check_dir=self.scripts, stamp="20260920T3", runner=failing)
        self.assertEqual(res["status"], "restored-after-failed-check", res)
        self.assertEqual(seen, [["fake-verify", "--phase", "pre"]], "the check ran once, after registering")
        self.assertTrue(res["check"]["restored"])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original, "pre-edit bytes are back")
        self.assertEqual(len(self._backups()), 1, "the restore point taken before the edit is the one restored")
        passing = lambda argv: _Proc(0)
        res = reg.register_then_check(self.settings, self.regs, check_cmd=["fake-verify"],
                                      check_dir=self.scripts, stamp="20260920T4", runner=passing)
        self.assertEqual(res["status"], "registered")
        self.assertEqual(res["check"]["rc"], 0)
        self.assertFalse(res["check"]["restored"])
        self.assertIn("/x/hooks/sweep-dead-locks.sh", self.settings.read_text(encoding="utf-8"))
        # a refused registration never runs the check at all (script check comes first)
        (self.scripts / "sweep-dead-locks.sh").unlink()
        calls = []
        res = reg.register_then_check(self.settings, self.regs, check_cmd=["x"], check_dir=self.scripts,
                                      runner=lambda a: (calls.append(a), _Proc(0))[1])
        self.assertEqual(res["status"], "refused")
        self.assertEqual(calls, [])

    def test_G11d_cli_round_trip(self):
        def cli(*args):
            return subprocess.run([sys.executable, str(HOOKS / "settings_hooks_registrar.py"), *args],
                                  capture_output=True, text=True, timeout=30)
        r = cli("register", str(self.settings), "--event", "SessionStart",
                "--command", "/x/hooks/sweep-dead-locks.sh", "--check-dir", str(self.scripts))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "registered")
        rp = cli("restore-point", str(self.settings))
        self.assertEqual(rp.returncode, 0)
        self.assertTrue(Path(rp.stdout.strip()).exists())
        r = cli("register", str(self.settings), "--event", "SessionStart",
                "--command", "/x/hooks/nope.sh", "--check-dir", str(self.scripts))
        self.assertEqual(r.returncode, 2, "refusal exits 2")
        r = cli("restore", str(self.settings))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original)
        # --then-run with a failing check: registered, check ran, restored, exit 2
        r = cli("register", str(self.settings), "--event", "SessionStart",
                "--command", "/x/hooks/sweep-dead-locks.sh", "--check-dir", str(self.scripts),
                "--then-run", "false")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["status"], "restored-after-failed-check")
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original)
        r = cli("register", str(self.settings), "--event", "SessionStart",
                "--command", "/x/hooks/sweep-dead-locks.sh", "--check-dir", str(self.scripts),
                "--then-run", "true")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["check"]["rc"], 0)

    def test_G11g_existing_backup_path_refuses_before_any_write(self):
        """The `backup.exists()` collision refusal (register(), the branch just
        before the file is ever touched): pre-create the exact stamped backup path
        the registrar is about to write to, so the collision is forced rather than
        relying on the microsecond stamp to collide naturally. Refusal must write
        NOTHING — no edit to settings.json, no restore point, and the pre-existing
        backup itself must be left untouched (never overwritten)."""
        stamp = "20260920T5"
        backup = Path(f"{self.settings}.bak-{stamp}-{reg.BACKUP_TAG}")
        backup.write_text("pre-existing backup — not the registrar's", encoding="utf-8")
        res = reg.register(self.settings, self.regs, check_dir=self.scripts, stamp=stamp)
        self.assertEqual(res["status"], "refused", res)
        self.assertIn("already exists", res["refused_reason"])
        self.assertIsNone(res["restore_point"])
        self.assertEqual(res["registered"], [])
        self.assertEqual(self.settings.read_text(encoding="utf-8"), self.original,
                         "nothing written to settings.json on this refusal")
        self.assertIsNone(reg.read_restore_point(self.settings), "no restore point recorded")
        self.assertEqual(backup.read_text(encoding="utf-8"), "pre-existing backup — not the registrar's",
                         "the colliding backup itself must not be overwritten")
        self.assertEqual(self._backups(), [backup], "no second backup created")

    def test_G11e_this_trees_settings_json_sits_on_a_recorded_restore_point(self):
        """This tree's settings.json differs from its recorded, parseable restore
        point ONLY by added hook registrations — at least one, each resolving to an
        existing executable — and nothing was removed; the in-tree `restore` would
        return the file to the recorded bytes. (S5 generalised this from "exactly
        S4's three hooks": the restore point moves forward with every registration
        made through the registrar, so the durable property is the SHAPE of the
        delta, not its contents.)"""
        live = CONFIG_DIR / "settings.json"
        rp = reg.read_restore_point(live)
        if rp is None:
            self.skipTest("no restore point recorded beside this tree's settings.json")
        self.assertTrue(rp.is_file())
        before = json.loads(rp.read_text(encoding="utf-8"))
        after = json.loads(live.read_text(encoding="utf-8"))
        def cmds(doc):
            return sorted(h["command"] for gs in doc["hooks"].values() for g in gs for h in g.get("hooks", []))
        added = sorted(set(cmds(after)) - set(cmds(before)))
        self.assertGreaterEqual(len(added), 1, "a recorded restore point implies at least one registration")
        for c in added:
            self.assertIsNone(reg.check_script(c, CONFIG_DIR / "hooks"), c)
        self.assertEqual(sorted(set(cmds(before)) - set(cmds(after))), [], "nothing removed")


if __name__ == "__main__":
    unittest.main(verbosity=2)

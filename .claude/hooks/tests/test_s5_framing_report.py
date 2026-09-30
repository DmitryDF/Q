#!/usr/bin/env python3
"""streamed-dancing-goose S5 (A5) — reach the checks that already exist.

The validation gate, one class per clause:

  P   Paired-writer tests: annotate-then-append and append-then-hash exercise the
      two `## Sessions` writers TOGETHER (S1's F1 has annotate→append; here both
      orders, the locked hash after each, and the omission gate over the pair).
  R   A session ending by ANY route lists unframed `[Thought]` lines carrying no
      "not framed yet" marker: `todo.audit_framing` classifies pass / fail /
      not-framed-yet, the marker is the ONE the tree already uses
      (`framing_obligation.MARKER`), a marked line never fails the audit, the
      audit exits 1 on any untracked failure, `--surface` renders the shared block,
      the Stop hook `check-framing-audit-stop.sh` prints it and exits 0 always,
      and the SessionStart scan renders the same block (the proven channel).
  M   A refusal is machine-readable: JSON on stdout beside the `  ✗ ` stderr
      shape, exit code unchanged, and work-frame-and-create-todo's parser still
      reads the stderr.
  W   The CLI exception catch is widened: `create-topic` / `set-active` report
      `✗ …` + exit 1 on a lock-contended run instead of a traceback.
  G   The Stop reporter is registered on Stop through the registrar (and on no
      blocking event).

Run: env CLAUDE_CONFIG_DIR=<clone> python3 hooks/tests/test_s5_framing_report.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
CONFIG_DIR = HOOKS.parent
sys.path.insert(0, str(HOOKS))

import todo  # noqa: E402
import pre_plan_gates as ppg  # noqa: E402
import check_work_done_omission as cwo  # noqa: E402
import framing_obligation as fo  # noqa: E402

SID = "abcd5555-s5-framing-report-000000000"
SID8 = SID[:8]
WD = ppg.SESSION_WRITER_WORK_DONE
CL = ppg.SESSION_WRITER_CLOSE
PAYLOAD = {"duration_min": 30, "opus_tokens_k": 100, "tasks_completed": 3}

LOCKED_SPINE = """# Topic — Thought File

**Status:** active.

# Discovery

## Guiding Policy
gp body

## Desired Outcome
do body

## Desired Solution
ds body

## Metrics
OMTM: x

## Q&A
q body

# Implementation Details

## Sessions

*Updated at each /close.*

## Next Session Prompt

paste me
"""

FRAMED = ("- [ ] [Thought] **Framed thing** — Problem: the widget leaks. Context: seen twice. "
          "Guiding policy: fix at the seam. Master plan: [[framed_THOUGHT]].")
UNFRAMED_UNTRACKED = "- [ ] [Thought] **Hand-typed thing** — just a note, no framing at all."
UNFRAMED_TRACKED = f"- [ ] [Thought] {fo.MARKER} **Auto thing** — see [[auto_THOUGHT]]."
NEVER_UNFRAMED = "- [ ] [Thought] **Declined thing** — never doing this."

TODO_BODY = f"""# TODO

## Now

{FRAMED}
{UNFRAMED_UNTRACKED}
{UNFRAMED_TRACKED}

## Never

{NEVER_UNFRAMED}

## Done

- [x] **Old** — DONE 2026-01-01. [[2026-01-01]]
"""


class _Bound:
    def __init__(self, spine: Path):
        self.state = {"thought_file_path": str(spine), "phase": "implementation"}

    def __enter__(self):
        self._orig = ppg._resolve_topic
        ppg._resolve_topic = lambda sid: ("Root", "s5-topic", self.state)
        return self

    def __exit__(self, *exc):
        ppg._resolve_topic = self._orig


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s5_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._orig_ledger = ppg._record_ledger_write
        ppg._record_ledger_write = lambda p: None
        self.addCleanup(setattr, ppg, "_record_ledger_write", self._orig_ledger)


# --------------------------------------------------------------------------- #
# P — paired writers
# --------------------------------------------------------------------------- #
class PairedWriters(_Tmp):

    def _spine(self):
        p = self.tmp / "s5_THOUGHT.md"
        p.write_text(LOCKED_SPINE, encoding="utf-8")
        return p

    def test_P1_annotate_then_append_bullet_survives_and_hash_unchanged(self):
        p = self._spine()
        h0 = ppg._discovery_locked_fields_hash(LOCKED_SPINE)
        with _Bound(p):
            ppg.annotate_session(SID, "Shipped S5.", writer=WD)
            ppg.append_metrics(SID, PAYLOAD)
        t = p.read_text(encoding="utf-8")
        self.assertEqual(sum("Shipped S5." in l for l in t.split("\n")), 1)
        self.assertIn("- Duration: 30min", t)
        self.assertEqual(ppg._discovery_locked_fields_hash(t), h0, "the locked hash never moves")

    def test_P2_append_then_annotate_rows_survive_and_hash_unchanged(self):
        p = self._spine()
        h0 = ppg._discovery_locked_fields_hash(LOCKED_SPINE)
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD)
            ppg.annotate_session(SID, "Shipped after close.", writer=WD)
            ppg.append_metrics(SID, {**PAYLOAD, "duration_min": 45})
        t = p.read_text(encoding="utf-8")
        self.assertIn("Shipped after close.", t)
        self.assertIn("- Duration: 45min", t)
        self.assertNotIn("- Duration: 30min", t, "close replaces only its own rows")
        self.assertEqual(ppg._discovery_locked_fields_hash(t), h0)
        sec = ppg.find_sessions_section(t.split("\n"))
        disc = ppg.discovery_section_body(t)
        self.assertIsNotNone(sec)
        self.assertNotIn("Duration: 45min", disc, "nothing landed inside # Discovery")

    def test_P3_omission_gate_over_the_pair_keys_on_the_work_done_writer(self):
        p = self._spine()
        with _Bound(p):
            ppg.append_metrics(SID, PAYLOAD)
            ppg.annotate_session(SID, "Close note.", writer=CL)
            state = {"thought_file_path": str(p)}
            self.assertFalse(cwo._spine_has_session_block(SID, state),
                             "close's block + close's annotate alone must not read as a ship record")
            ppg.annotate_session(SID, "Work-done record.", writer=WD)
            self.assertTrue(cwo._spine_has_session_block(SID, state))


# --------------------------------------------------------------------------- #
# R — the audit, the marker, the renderer, the two channels
# --------------------------------------------------------------------------- #
class FramingAudit(_Tmp):

    def _todo(self, body=TODO_BODY):
        p = self.tmp / "TODO.md"
        p.write_text(body, encoding="utf-8")
        return p

    def test_R1_classification_pass_fail_not_framed_yet_and_never_excluded(self):
        rep = todo.audit_framing(self._todo())
        by = {r["text"][:30]: r["status"] for r in rep["items"]}
        self.assertEqual(rep["passed"], 1)
        self.assertEqual(rep["failed"], 1, "only the untracked unframed line fails")
        self.assertEqual(rep["not_framed_yet"], 1, "the marked line is tracked, not failed")
        self.assertEqual(rep["total"], 3, "the Never-bucket line owes no framing")
        self.assertTrue(any("Hand-typed" in k and v == "fail" for k, v in by.items()))
        self.assertTrue(any("Auto thing" in k or fo.MARKER in k for k, v in by.items() if v == "not-framed-yet"))
        tracked = [r for r in rep["items"] if r["status"] == "not-framed-yet"][0]
        self.assertTrue(tracked["tracked"])
        self.assertEqual(todo._not_framed_yet_marker(), fo.MARKER, "the marker has one home")

    def test_R2_cli_exit_codes_and_surface(self):
        p = self._todo()
        r = subprocess.run([sys.executable, str(HOOKS / "todo.py"), "audit-coverage", "--file", str(p)],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1, "an untracked failure exits 1")
        rep = json.loads(r.stdout)
        self.assertEqual((rep["passed"], rep["failed"], rep["not_framed_yet"]), (1, 1, 1))
        r = subprocess.run([sys.executable, str(HOOKS / "todo.py"), "audit-coverage", "--file", str(p), "--surface"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "", "--surface prints no JSON")
        self.assertIn("not framed and nothing tracks", r.stderr)
        self.assertIn("Hand-typed thing", r.stderr)
        self.assertNotIn("Auto thing", r.stderr, "tracked lines are the obligation surface's to report")
        # only tracked / framed lines → exit 0 and no block
        p2 = self._todo(TODO_BODY.replace(UNFRAMED_UNTRACKED + "\n", ""))
        r = subprocess.run([sys.executable, str(HOOKS / "todo.py"), "audit-coverage", "--file", str(p2), "--surface"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, "")

    def test_R3_stop_hook_reports_and_always_exits_0(self):
        p = self._todo()
        env = dict(os.environ); env.pop("CLAUDE_CODE_REMOTE", None)
        r = subprocess.run([str(HOOKS / "check-framing-audit-stop.sh")],
                           input=json.dumps({"session_id": SID, "cwd": str(self.tmp)}),
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "", "a Stop reporter never writes stdout")
        self.assertIn("Hand-typed thing", r.stderr)
        r = subprocess.run([str(HOOKS / "check-framing-audit-stop.sh")], input="not json",
                           capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(r.returncode, 0, "fail-open on garbage")

    def test_R4_session_start_scan_renders_the_same_block(self):
        proj = self.tmp / "proj"
        proj.mkdir()
        (proj / "TODO.md").write_text(TODO_BODY, encoding="utf-8")
        (proj / "CLAUDE.md").write_text("# proj\n", encoding="utf-8")
        with mock.patch.object(todo, "find_vault_root", lambda cwd: self.tmp):
            projects = todo.scan_vault(self.tmp, proj)
            out = todo._format_read_output(projects, datetime.now().date())
        self.assertIn("not framed and nothing tracks", out)
        self.assertIn("Hand-typed thing", out)
        self.assertIn("auto-registered TODO line", out, "the obligation surface still renders beside it")

    def test_R5_renderer_is_shared_and_silent_when_nothing_fails(self):
        self.assertIsNone(todo.render_framing_audit(None))
        rep = todo.audit_framing(self._todo(TODO_BODY.replace(UNFRAMED_UNTRACKED + "\n", "")))
        self.assertIsNone(todo.render_framing_audit(rep))


# --------------------------------------------------------------------------- #
# M — machine-readable refusal
# --------------------------------------------------------------------------- #
class MachineReadableRefusal(_Tmp):

    def test_M1_refusal_has_json_on_stdout_and_the_stderr_shape_is_unchanged(self):
        p = self.tmp / "TODO.md"
        p.write_text("# TODO\n\n## Now\n\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(HOOKS / "todo.py"), "add", "not framed at all",
                            "--bucket", "NOW", "--file", str(p), "--validate-framing"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 1)
        doc = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(doc["status"], "refused")
        self.assertEqual(doc["reason"], "framing")
        self.assertTrue(any("Problem:" in e for e in doc["errors"]))
        self.assertIn("  ✗ Missing 'Problem:' label", r.stderr)
        self.assertIn("Framing validation failed", r.stderr)
        self.assertNotIn("not framed at all", p.read_text(encoding="utf-8"), "a refusal writes nothing")
        # the retry loop's parser still reads the stderr shape
        sys.path.insert(0, str(CONFIG_DIR / "skills" / "work-frame-and-create-todo"))
        import importlib
        runpy = importlib.import_module("run")
        self.assertEqual(sorted(runpy._parse_missing(r.stderr)), sorted(doc["errors"]))

    def test_M2_success_stdout_is_unchanged(self):
        p = self.tmp / "TODO.md"
        p.write_text("# TODO\n\n## Now\n\n", encoding="utf-8")
        r = subprocess.run([sys.executable, str(HOOKS / "todo.py"), "add", FRAMED[6:],
                            "--bucket", "NOW", "--file", str(p), "--validate-framing"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('"refused"', r.stdout)


# --------------------------------------------------------------------------- #
# W — widened CLI exception catch
# --------------------------------------------------------------------------- #
class WidenedCatch(_Tmp):

    def test_W1_create_topic_and_set_active_report_a_lock_timeout(self):
        src = (HOOKS / "pre_plan_gates.py").read_text(encoding="utf-8")
        i = src.index('elif cmd == "create-topic":')
        seg = src[i:src.index('elif cmd == "', i + 10)]
        self.assertIn("except (ValueError, _BLT)", seg, "create-topic catches the lock timeout")
        j = src.index('elif cmd == "set-active":')
        seg2 = src[j:src.index('elif cmd == "', j + 10)]
        self.assertIn("except (ValueError, _BLT)", seg2, "set-active catches the lock timeout")
        # behavioural: a timeout raised from inside either function reaches the verb
        # as `✗ …` + exit 1, never a traceback (the module's main() is driven directly
        # with the writer patched, so no live state is touched)
        for v in ("phase-start", "phase-stop"):
            k = src.index(f'elif cmd == "{v}":')
            self.assertIn("except (ValueError, _BLT)", src[k:src.index('elif cmd == "', k + 10)],
                          f"{v} takes the spine lock via write_phase_marker and must catch the timeout")
        argvs = {"create-topic": ["sid", "proj", "topic"], "set-active": ["sid", "topic", "proj"],
                 "phase-start": ["sid", "thought", "--auth", "tok"], "phase-stop": ["sid"]}
        for verb, fn in (("create-topic", "create_topic"), ("set-active", "set_active"),
                         ("phase-start", "phase_start"), ("phase-stop", "phase_stop")):
            script = (
                f"import sys; sys.argv = ['pre_plan_gates.py', {verb!r}] + {argvs[verb]!r}\n"
                f"sys.path.insert(0, {str(HOOKS)!r})\n"
                "import pre_plan_gates as ppg\n"
                "from bookkeeping_lock import BookkeepingLockTimeout\n"
                "def boom(*a, **k): raise BookkeepingLockTimeout('lock held by another session')\n"
                f"ppg.{fn} = boom\n"
                "ppg.main()\n"
            )
            r = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
            self.assertNotIn("Traceback", r.stderr, verb)
            self.assertEqual(r.returncode, 1, verb)
            self.assertIn("✗ lock held by another session", r.stderr, verb)


# --------------------------------------------------------------------------- #
# G — registration
# --------------------------------------------------------------------------- #
class Registration(unittest.TestCase):

    def test_G1_stop_reporter_registered_on_stop_only(self):
        doc = json.loads((CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))

        def cmds(event):
            return [h.get("command", "") for g in doc["hooks"].get(event, []) for h in g.get("hooks", [])]

        self.assertTrue(any(c.endswith("/check-framing-audit-stop.sh") for c in cmds("Stop")))
        for ev in ("PreToolUse", "PostToolUse", "UserPromptSubmit", "SessionStart", "SessionEnd"):
            self.assertFalse(any(c.endswith("/check-framing-audit-stop.sh") for c in cmds(ev)), ev)
        self.assertTrue(os.access(HOOKS / "check-framing-audit-stop.sh", os.X_OK))


if __name__ == "__main__":
    unittest.main(verbosity=2)

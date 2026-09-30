#!/usr/bin/env python3
"""A5 ledger coverage (glittery-humming-pine S4) — gap G4.

`track-session-files.sh` is PostToolUse on Write|Edit, so it sees only TOOL
calls. Every tracked repo file written through CODE was invisible to the ledger,
and therefore undeclared at publish time. A5 instruments those code-layer
writers.

The half of A5's validation gate implemented here is the INVENTORY check: every
module that writes a tracked repo path must be instrumented. That is the part
which guards the FUTURE — a writer added next month is caught here rather than
discovered as a silently-undeclared file during someone's close.

The two-session /close simulation (the other half of the gate) needs A6's /close
rewiring to be meaningful and belongs with it.
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

# The writers A5 names, with the reason each is in or out. Kept as data so the
# list is reviewable next to the assertion that consumes it.
INSTRUMENTED = ["todo", "taskmanagement", "work_done",
                "claims_registry", "pre_plan_gates", "_factcheck_engine"]

# Named EXCLUSIONS from A5, each with the plan's stated reason. Asserting the
# exclusions matters as much as the inclusions: an exclusion that silently became
# a writer is exactly the drift this file exists to catch.
EXCLUDED = {
    "land_readiness": "writes a state sidecar, not a tracked repo file",
}

# Inspected once during A5 and found to write no TRACKED REPO path — they write
# state sidecars, audit records, obligation ledgers and probe output, all of
# which live outside the committed surface. Listed rather than pattern-excluded
# so that adding a module is a deliberate act with a reader attached.
REVIEWED_NOT_TRACKED_WRITERS = {
    "_claim_harvest", "_claim_harvest_trigger", "_claim_register",
    "_evidence_register_consult", "_evidence_register_trigger",
    "_plan_manifest", "assessment_engine", "bookkeeping_paths",
    "check_framing_surface_records", "dc_caller_audit", "dc_obligation",
    "dc_stats", "framing_obligation", "output_security_metacheck",
    "output_security_probe", "output_security_record", "work_done_report",
}


class TestInventory(unittest.TestCase):
    """Every module A5 names is instrumented, and still importable."""

    def test_every_named_writer_has_the_recorder(self):
        missing = []
        for name in INSTRUMENTED:
            mod = importlib.import_module(name)
            if not hasattr(mod, "_record_ledger_write"):
                missing.append(name)
        self.assertEqual([], missing,
                         f"A5-named writers with no ledger recorder: {missing}")

    def test_named_exclusions_are_still_absent(self):
        for name, reason in EXCLUDED.items():
            try:
                mod = importlib.import_module(name)
            except Exception:
                continue          # not importable here is fine; it is excluded
            self.assertFalse(
                hasattr(mod, "_record_ledger_write"),
                f"{name} gained a ledger recorder but A5 excludes it: {reason}. "
                "If it now writes a tracked repo file, move it to INSTRUMENTED "
                "deliberately rather than leaving the exclusion stale.")

    def test_no_NEW_module_writes_a_tracked_repo_path_uninstrumented(self):
        """Catch a writer added AFTER A5 that nobody instrumented.

        The first version of this test was a pure heuristic — "mentions TODO.md
        or _PLAN and writes a file" — and it flagged 17 existing modules, none of
        them defects: they write state sidecars, audit records and obligation
        ledgers, not tracked repo files. A test that always fails is worse than
        no test, because it trains the reader to skip it.

        So the shape is a reviewed BASELINE instead. The modules below were
        inspected once and write no tracked repo path. The assertion is about
        CHANGE: a module appearing that is in neither list is surfaced, and the
        reviewer either instruments it or records it here with a reason. That is
        the future-proofing A5's gate actually asks for.
        """
        import re
        pat = re.compile(r'(TODO\.md|_THOUGHT|_PLAN|_CLAIMS)')
        writes = re.compile(r'\.write_text\(|open\([^)]*["\']w["\']|os\.replace\(')
        known = set(INSTRUMENTED) | set(EXCLUDED) | REVIEWED_NOT_TRACKED_WRITERS
        appeared = []
        for p in sorted(HOOKS.glob("*.py")):
            stem = p.stem
            if stem in known or stem.startswith("test_"):
                continue
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if pat.search(src) and writes.search(src):
                appeared.append(stem)
        self.assertEqual(
            [], appeared,
            "new hooks module(s) reference a tracked repo path and write files, "
            f"but carry no ledger recorder: {appeared}. Either instrument them "
            "(A5) or add them to REVIEWED_NOT_TRACKED_WRITERS with a reason.")


class TestNeverRaises(unittest.TestCase):
    """The recorder must never break its host module.

    These modules run under pytest, from standalone CLI invocations, and from
    background jobs where no session exists. A hard failure would crash all
    three, which is why record_write never raises and why each wrapper extends
    that guarantee to the import.
    """

    def setUp(self):
        self.todo = importlib.import_module("todo")

    def test_survives_a_broken_commit_scope_import(self):
        saved = sys.modules.get("commit_scope")
        sys.modules["commit_scope"] = None        # force the import to fail
        try:
            self.todo._record_ledger_write("/tmp/whatever")
        except Exception as exc:                  # noqa: BLE001 — that IS the test
            self.fail(f"recorder raised with commit_scope broken: {exc!r}")
        finally:
            if saved is not None:
                sys.modules["commit_scope"] = saved
            else:
                sys.modules.pop("commit_scope", None)

    def test_survives_nonsense_paths(self):
        for bad in (None, "", "\0bad", 12345):
            with self.subTest(path=bad):
                try:
                    self.todo._record_ledger_write(bad)
                except Exception as exc:          # noqa: BLE001
                    self.fail(f"recorder raised on {bad!r}: {exc!r}")

    def test_never_writes_a_placeholder_session_line(self):
        """The one thing it must never do is attribute a file to no-one.

        A blank or placeholder session line would file this session's work under
        a name that is not a session, which is the mis-attribution the whole plan
        is about.

        The contract is narrower than "an empty id records nothing", which is
        what an earlier version of this test asserted and got wrong. `record_write`
        resolves `session_id or current_session_id()`, so an EMPTY id means "use
        the ambient session" — correct attribution, not a placeholder — while a
        WHITESPACE-only id survives the `or`, strips to empty, and is refused.
        Both behaviours are right; only the assertion was wrong. What is tested
        here is the property that actually matters: no ledger file is ever
        created under an empty-ish session name.
        """
        import commit_scope as cs
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            target = repo / "TODO.md"
            target.write_text("x\n", encoding="utf-8")

            self.assertFalse(
                cs.record_write(target, session_id="   ", cwd=repo),
                "a whitespace-only session id must be refused")

            logs = repo / ".claude" / "logs"
            if logs.is_dir():
                for f in logs.glob("_session_files-*.log"):
                    sid = f.name[len("_session_files-"):-len(".log")]
                    self.assertTrue(
                        sid.strip(),
                        f"a ledger was created under an empty session name: {f.name}")


class TestCanonicalLocation(unittest.TestCase):
    """Writer and reader must resolve the SAME ledger directory — tested by
    RUNNING them, not by grepping them.

    The first version of this class asserted on source text: that both scripts
    mention `bookkeeping_resolver.py` and build `LOG_DIR` from `$LEDGER_ROOT`.
    Both strings were present while the discriminator was WRONG, so all three
    tests passed against a writer that split the ledger per subdirectory — the
    exact gap-G7 defect this slice claims to close. An independent reviewer
    caught it; the tests did not, and could not, because a string being present
    says nothing about whether the branch around it is correct.

    Everything here now drives the real scripts in a scratch repo.
    """

    def _scratch_repo(self, td):
        repo = Path(td) / "repo"
        (repo / "sub" / "deeper").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "TODO.md").write_text("x\n", encoding="utf-8")
        return repo

    def test_writer_lands_ONE_ledger_whatever_the_cwd(self):
        """The property gap G7 actually names.

        A hook payload's cwd may be the repo root, a subdirectory, or a linked
        worktree. All three must reach the SAME ledger directory, because
        `co_writers` compares ledgers across live sessions and two sessions with
        different cwds writing to different directories compare nothing at all.
        """
        writer = HOOKS / "track-session-files.sh"
        with tempfile.TemporaryDirectory() as td:
            repo = self._scratch_repo(td)
            for cwd in (repo, repo / "sub", repo / "sub" / "deeper"):
                payload = json.dumps({
                    "session_id": "WPROBE",
                    "tool_input": {"file_path": str(repo / "TODO.md")},
                    "cwd": str(cwd),
                })
                subprocess.run(["bash", str(writer)], input=payload,
                               text=True, capture_output=True)
            found = sorted(repo.rglob("_session_files-WPROBE.log"))
            self.assertEqual(
                1, len(found),
                "three cwds in one repo produced "
                f"{len(found)} ledgers: {[str(f) for f in found]}. "
                "A split ledger is gap G7 — co_writers compares nothing.")
            self.assertEqual(repo / ".claude" / "logs", found[0].parent,
                             "the single ledger is not at the canonical root")

    def test_reader_finds_the_ledger_from_every_argument_form_and_cwd(self):
        """Both documented argument forms, from root and from a subdirectory.

        `project_dir` is optional in the usage line, so `--harness-scope` may
        legally be the SECOND argument. An earlier version read `PROJECT="${2:-.}"`
        before parsing flags, so that form silently set PROJECT to the literal
        string "--harness-scope" and the script exited 0 with no output and no
        error — a silent wrong answer in the one mode A6 depends on.
        """
        reader = HOOKS / "session-scope.sh"
        with tempfile.TemporaryDirectory() as td:
            repo = self._scratch_repo(td)
            logs = repo / ".claude" / "logs"
            logs.mkdir(parents=True)
            wanted = str(HOOKS / "todo.py")
            (logs / "_session_files-PROBE.log").write_text(
                wanted + "\n", encoding="utf-8")

            forms = [
                (["PROBE", str(repo), "--harness-scope"], repo, "3-arg"),
                (["PROBE", "--harness-scope"], repo, "2-arg from root"),
                (["PROBE", "--harness-scope"], repo / "sub", "2-arg from subdir"),
            ]
            for argv, cwd, label in forms:
                with self.subTest(form=label):
                    r = subprocess.run(["bash", str(reader), *argv],
                                       cwd=str(cwd), text=True, capture_output=True)
                    self.assertIn(
                        wanted, r.stdout,
                        f"{label} produced no path list "
                        f"(stdout={r.stdout!r} stderr={r.stderr!r})")

    def test_harness_scope_excludes_unmanaged_trees_BEHAVIOURALLY(self):
        """plans/ and logs/ must not be offered for promotion.

        Observed directly while building this: an unrestricted projection offered
        `plans/<topic>.run-state.json`, which claude-promote's step 2a would then
        `config-source add` — promoting session state into the shared config.

        Driven through the script with a real ledger rather than grepped, so a
        broken `case` pattern fails here instead of passing on a string match.
        """
        reader = HOOKS / "session-scope.sh"
        with tempfile.TemporaryDirectory() as td:
            repo = self._scratch_repo(td)
            logs = repo / ".claude" / "logs"
            logs.mkdir(parents=True)
            cfg = Path(td) / "cfgdir"

            promotable = cfg / "hooks" / "real_hook.sh"
            excluded = cfg / "plans" / "topic.run-state.json"
            excluded2 = cfg / "logs" / "noise.log"
            for p in (promotable, excluded, excluded2):
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("x\n", encoding="utf-8")

            (logs / "_session_files-PROBE.log").write_text(
                "\n".join(str(p) for p in (promotable, excluded, excluded2)) + "\n",
                encoding="utf-8")

            env = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg))
            r = subprocess.run(
                ["bash", str(reader), "PROBE", str(repo), "--harness-scope"],
                text=True, capture_output=True, env=env)
            self.assertIn(str(promotable), r.stdout,
                          "managed-config path was not offered")
            self.assertNotIn(str(excluded), r.stdout,
                             "plans/ was offered for promotion")
            self.assertNotIn(str(excluded2), r.stdout,
                             "logs/ was offered for promotion")


class TestCallSitesActuallyRecord(unittest.TestCase):
    """The recorder must be CALLED, not merely defined.

    `test_every_named_writer_has_the_recorder` asserts `hasattr(...)`, which
    passes unchanged if someone deletes the call at the write site and leaves the
    function definition in place. That is the same vacuity that let the previous
    slice's suite pass 19/19 against a mutated implementation. This drives the
    real public writer and checks a ledger line appeared.
    """

    def test_todo_write_records_a_ledger_line(self):
        import commit_scope as cs
        import todo
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            target = repo / "TODO.md"

            sid = "callsite-probe"
            env_saved = os.environ.get("CLAUDE_CODE_SESSION_ID")
            os.environ["CLAUDE_CODE_SESSION_ID"] = sid
            cwd_saved = os.getcwd()
            try:
                os.chdir(repo)                     # so the ledger resolves here
                todo.write_todo_file_atomic(target, "- [ ] item\n")
            finally:
                os.chdir(cwd_saved)
                if env_saved is None:
                    os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
                else:
                    os.environ["CLAUDE_CODE_SESSION_ID"] = env_saved

            led = repo / ".claude" / "logs" / f"_session_files-{sid}.log"
            self.assertTrue(
                led.is_file(),
                "write_todo_file_atomic produced no ledger entry — the call site "
                "is missing even though the module defines a recorder")
            self.assertIn(str(target.resolve()), led.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

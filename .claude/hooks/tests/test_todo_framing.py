#!/usr/bin/env python3
"""Tests for Slice C — TODO framing validator and related subcommands.

Covers:
  (1)  validate-framing: pass on conformant item
  (2)  validate-framing: fail with correct error on missing Problem label
  (3)  validate-framing: fail on missing master-plan reference
  (4)  validate-framing: [[wikilink]] satisfies Master plan check
  (5)  add --phase planning: suppressed, no file write
  (6)  add --validate-framing: blocks malformed text
  (7)  add --validate-framing --derives-from: blocks when no [[wikilink]] present
  (8)  reframe --supersede: old item marked [x] SUPERSEDED, new item created
  (9)  reframe --supersede on done item: skipped, no error
  (10) mark-in-progress: appends (in progress: SID[:8])
  (11) mark-in-progress: idempotent — second call updates, not double-appends
  (12) audit-coverage: returns correct passed/failed counts

Run: python3 ${KIT_HOOKS_DIR}/tests/test_todo_framing.py
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path.home() / ".claude" / "hooks"
TODO_PY = HOOKS / "todo.py"

CONFORMANT_ITEM = (
    "**Framing revamp** — Problem: no enforced convention. "
    "Context: TODO.md has freeform text. "
    "Guiding policy: code enforces at write time. "
    "Master plan: [[workflow-phases-redesign_THOUGHT]]."
)

MINIMAL_TODO = """\
## Now

- [ ] {open_item}

## Done

{done_items}
"""


def run(*args, expect=None, stdin=None):
    result = subprocess.run(
        ["python3", str(TODO_PY), *args],
        capture_output=True,
        text=True,
        input=stdin,
    )
    if expect is not None and result.returncode != expect:
        raise AssertionError(
            f"command {args!r} exit={result.returncode} (expected {expect})\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def make_todo(open_item=CONFORMANT_ITEM, done_items=""):
    """Return a minimal TODO.md content string."""
    return MINIMAL_TODO.format(open_item=open_item, done_items=done_items)


class ValidateFramingTests(unittest.TestCase):

    def test_validate_framing_pass(self):
        """Well-formed item exits 0 with status pass."""
        r = run("validate-framing", CONFORMANT_ITEM, expect=0)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "pass")
        self.assertNotIn("errors", data)

    def test_validate_framing_missing_problem(self):
        """Item missing Problem: label exits 1 with specific error."""
        text = "**Title** — Context: foo. Guiding policy: bar. [[link]]."
        r = run("validate-framing", text, expect=1)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "fail")
        errors = data["errors"]
        self.assertTrue(any("Problem" in e for e in errors), errors)

    def test_validate_framing_missing_master_plan_link(self):
        """Item missing both Master plan: and [[wikilink]] exits 1."""
        text = "**Title** — Problem: x. Context: y. Guiding policy: z. No link."
        r = run("validate-framing", text, expect=1)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "fail")
        errors = data["errors"]
        self.assertTrue(any("Master plan" in e for e in errors), errors)

    def test_validate_framing_wikilink_alternative(self):
        """[[wikilink]] alone satisfies the Master plan check."""
        text = "**Title** — Problem: x. Context: y. Guiding policy: z. [[some-thought]]."
        r = run("validate-framing", text, expect=0)
        data = json.loads(r.stdout)
        self.assertEqual(data["status"], "pass")


class AddPhasePlanningTests(unittest.TestCase):

    def test_add_phase_planning_suppressed(self):
        """--phase planning returns withheld without writing to file."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            before = todo_path.read_text()

            r = run(
                "add", CONFORMANT_ITEM,
                "--bucket", "NOW",
                "--phase", "planning",
                "--file", str(todo_path),
                expect=0,
            )
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "withheld")
            after = todo_path.read_text()
            self.assertEqual(before, after, "File must not change during planning suppression")


class AddValidateFramingTests(unittest.TestCase):

    def test_add_validate_framing_blocks_malformed(self):
        """--validate-framing blocks item with missing labels."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            before = todo_path.read_text()

            r = run(
                "add", "No labels here at all",
                "--bucket", "NOW",
                "--validate-framing",
                "--file", str(todo_path),
                expect=1,
            )
            after = todo_path.read_text()
            self.assertEqual(before, after, "Malformed item must not be written")
            self.assertIn("Framing validation failed", r.stderr)

    def test_add_derives_from_requires_wikilink(self):
        """--derives-from with no [[wikilink]] in text returns error."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            # Item has labels but no [[wikilink]]
            text = "**Title** — Problem: x. Context: y. Guiding policy: z. Master plan: none."
            r = run(
                "add", text,
                "--bucket", "NOW",
                "--validate-framing",
                "--derives-from", "some/path.md",
                "--file", str(todo_path),
                expect=1,
            )
            self.assertIn("derives-from", r.stderr)


class ReframeTests(unittest.TestCase):

    def test_reframe_supersede_happy_path(self):
        """Old item gets [x] SUPERSEDED marker; new item is created."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            old_text = "**Old item** — Problem: old. Context: ctx. Guiding policy: gp. [[old]]."
            todo_path.write_text(make_todo(open_item=old_text))

            new_text = "**New item** — Problem: new. Context: ctx. Guiding policy: gp. [[new]]."
            r = run(
                "reframe",
                "--pattern", "Old item",
                "--new-text", new_text,
                "--supersede",
                "--file", str(todo_path),
                expect=0,
            )
            content = todo_path.read_text()
            self.assertIn("SUPERSEDED", content)
            self.assertIn("New item", content)

    def test_reframe_supersede_done_item_noop(self):
        """Reframing an already-done item returns skipped with no error."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            done_item = "- [x] **Done thing** — **DONE 2026-01-01.**"
            todo_path.write_text(make_todo(done_items=done_item))

            r = run(
                "reframe",
                "--pattern", "Done thing",
                "--new-text", "Whatever",
                "--supersede",
                "--file", str(todo_path),
                expect=0,
            )
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "skipped")
            self.assertIn("done", data["reason"])


class MarkInProgressTests(unittest.TestCase):

    def test_mark_in_progress_append(self):
        """Appends (in progress: SID[:8]) to matched open item."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo(open_item=CONFORMANT_ITEM))

            r = run(
                "mark-in-progress",
                "--pattern", "Framing revamp",
                "--session", "abcdef1234567890",
                "--file", str(todo_path),
                expect=0,
            )
            data = json.loads(r.stdout)
            self.assertEqual(data["status"], "marked")
            self.assertEqual(data["session"], "abcdef12")
            content = todo_path.read_text()
            self.assertIn("(in progress: abcdef12)", content)

    def test_mark_in_progress_idempotent(self):
        """Second call updates existing marker, no double-append."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo(open_item=CONFORMANT_ITEM))

            run(
                "mark-in-progress",
                "--pattern", "Framing revamp",
                "--session", "aaaa1111bbbb2222",
                "--file", str(todo_path),
                expect=0,
            )
            run(
                "mark-in-progress",
                "--pattern", "Framing revamp",
                "--session", "cccc3333dddd4444",
                "--file", str(todo_path),
                expect=0,
            )
            content = todo_path.read_text()
            self.assertIn("(in progress: cccc3333)", content)
            self.assertNotIn("aaaa1111", content)
            self.assertEqual(content.count("in progress:"), 1)


class AuditCoverageTests(unittest.TestCase):

    def test_audit_coverage_report(self):
        """Returns correct passed/failed counts for [Thought]-tagged items."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            good = (
                "[Thought] **Good thought** — Problem: x. Context: y. "
                "Guiding policy: z. [[thought]]."
            )
            bad = "[Thought] **Bad thought** — no labels whatsoever."
            untagged = "Regular item no thought tag."
            content = (
                "## Now\n\n"
                f"- [ ] {good}\n"
                f"- [ ] {bad}\n"
                f"- [ ] {untagged}\n\n"
                "## Done\n\n"
            )
            todo_path.write_text(content)

            r = run("audit-coverage", "--file", str(todo_path))
            data = json.loads(r.stdout)
            self.assertEqual(data["total"], 2, "Only [Thought]-tagged items counted")
            self.assertEqual(data["passed"], 1)
            self.assertEqual(data["failed"], 1)
            # exit code: 1 because failed > 0
            self.assertNotEqual(r.returncode, 0)


class AddRequireEvidenceTests(unittest.TestCase):
    """Slice S3 / A6 — the evidence gate.

    A SEPARATE predicate from `--validate-framing`, deliberately: five callers
    depend on that function's bar and `test_framing_obligation.py` pins the
    auto-registration obligation to it. These tests therefore also assert the
    two flags do not affect each other.
    """

    # Framing-valid in every case below; only the POINTER varies.
    NO_POINTER = ("**Title** — Problem: x. Context: y. "
                  "Guiding policy: z. Master plan: none.")

    def test_require_evidence_blocks_an_item_pointing_at_nothing(self):
        """No path:line, no backticked file, no wikilink, no URL → refused,
        and nothing is written."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            before = todo_path.read_text()

            r = run(
                "add", self.NO_POINTER,
                "--bucket", "NOW",
                "--validate-framing",
                "--require-evidence",
                "--file", str(todo_path),
                expect=1,
            )
            self.assertEqual(before, todo_path.read_text(),
                             "a refused item must not be written")
            self.assertIn("No pointer to where the problem lives", r.stderr)
            # The `  \u2717 ` prefix is the shape run.py parses into
            # missing_components; any other shape is invisible to the retry loop.
            self.assertIn("\u2717 ", r.stderr)

    def test_require_evidence_names_the_exception_in_its_refusal(self):
        """A person refused for build-new work must be able to see the way
        through without reading the source."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            r = run("add", self.NO_POINTER, "--bucket", "NOW",
                    "--require-evidence", "--file", str(todo_path), expect=1)
            self.assertIn("[build-new]", r.stderr)

    def test_the_build_new_exception_files_and_stays_visible(self):
        """The escape is written INTO the item text, so its overuse is legible
        on the list rather than vanishing after the call."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            run("add", self.NO_POINTER + " [build-new]",
                "--bucket", "NOW", "--validate-framing", "--require-evidence",
                "--file", str(todo_path), expect=0)
            self.assertIn("[build-new]", todo_path.read_text())

    def test_each_pointer_form_satisfies_the_gate(self):
        """path:line, backticked file, [[wikilink]] and URL each suffice."""
        stem = "**T** — Problem: x. Context: y. Guiding policy: z. "
        for label, tail in (
            ("path-line", "See todo.py:441. Master plan: none."),
            ("backticked", "See `run.py`. Master plan: none."),
            ("wikilink", "Master plan: [[some-plan]]."),
            ("url", "See https://example.com/a. Master plan: none."),
        ):
            with self.subTest(pointer=label):
                with tempfile.TemporaryDirectory() as td:
                    todo_path = Path(td) / "TODO.md"
                    todo_path.write_text(make_todo())
                    run("add", stem + tail, "--bucket", "NOW",
                        "--validate-framing", "--require-evidence",
                        "--file", str(todo_path), expect=0)

    def test_an_unresolvable_citation_warns_but_never_blocks(self):
        """Measured reason: of 46 corpus lines citing `path:line`, 41 name a
        file in another repo or an ambiguous basename. Blocking on resolution
        would refuse the majority of correctly-framed items."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            text = ("**T** — Problem: x. Context: y. Guiding policy: z. "
                    "See `definitely_not_here_xyz.py:9`. Master plan: none.")
            r = run("add", text, "--bucket", "NOW", "--validate-framing",
                    "--require-evidence", "--project", td, expect=0)
            self.assertIn("does not resolve", r.stderr)
            self.assertIn("not blocking", r.stderr)
            self.assertIn("definitely_not_here_xyz.py", todo_path.read_text())

    def test_the_gate_is_opt_in_and_off_by_default(self):
        """WITHOUT the flag the same pointerless item files — the four other
        `todo.py add` callers keep the bar they were written against."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            run("add", self.NO_POINTER, "--bucket", "NOW",
                "--validate-framing", "--file", str(todo_path), expect=0)
            self.assertIn("**Title**", todo_path.read_text())

    def test_the_two_gates_are_independent(self):
        """--require-evidence does not imply --validate-framing, and a framing
        failure is still reported as a framing failure."""
        with tempfile.TemporaryDirectory() as td:
            todo_path = Path(td) / "TODO.md"
            todo_path.write_text(make_todo())
            # Framing-INVALID but carries a pointer: the evidence gate alone
            # accepts it, proving it does not silently re-run framing.
            run("add", "no labels but see `x.py`", "--bucket", "NOW",
                "--require-evidence", "--file", str(todo_path), expect=0)

            todo_path.write_text(make_todo())
            # Framing-invalid AND pointerless, both flags: framing reports first.
            r = run("add", "no labels at all", "--bucket", "NOW",
                    "--validate-framing", "--require-evidence",
                    "--file", str(todo_path), expect=1)
            self.assertIn("Framing validation failed", r.stderr)


class EvidencePredicateTests(unittest.TestCase):
    """The predicate itself, imported directly — no subprocess."""

    def _fe(self):
        sys.path.insert(0, str(HOOKS))
        import framing_evidence
        return framing_evidence

    def test_self_test_is_green(self):
        r = subprocess.run(
            [sys.executable, str(HOOKS / "framing_evidence.py"), "--self-test"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_exception_token_matches_what_the_skill_documents(self):
        """A documented form the code rejects is the defect class this slice
        exists to reduce, so the token and its documentation are pinned to
        each other."""
        fe = self._fe()
        skill = (Path.home() / ".claude" / "skills"
                 / "work-frame-and-create-todo" / "SKILL.md")
        self.assertEqual(fe.EXCEPTION_TOKEN, "[build-new]")
        self.assertIn(fe.EXCEPTION_TOKEN, skill.read_text(encoding="utf-8"),
                      "the skill must document the exact token the code accepts")


if __name__ == "__main__":
    unittest.main(verbosity=2)

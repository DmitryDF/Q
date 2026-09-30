#!/usr/bin/env python3
"""S5 / G6 of `clarification-v2-cutover-20260912110528_PLAN.md` — the handoff
freshness verbs scoped to their own section, and a no-write reported as one.

Two defects, one root cause: `## Next Session Prompt` was a literal at the write
seam and implied at both read seams, so the three drifted.

  1. The WRITER puts its `handoff-src-hash` marker inside that section; both
     READERS searched the whole file. A v2 spine carries a legacy 1-segment
     marker of its own at the top of the file, so the readers found THAT one and
     compared it against a hash it was never written from — `handoff-freshness`
     reported STALE on a spine whose handoff was current. S1 sharpened this by
     emitting an empty `# Solution Design`, which flips the phase fingerprint
     from None to a real value; S5 is what closes it.

  2. The writer branched on `if section_header in text` — an unanchored
     substring. A prose line BEGINNING with that heading exists in the corpus,
     so the branch was taken, the anchored regex then matched nothing, `sub`
     returned the text unchanged, and the verb printed `{"status": "written"}`
     over a file it had not altered.

Run:  python3 ${KIT_HOOKS_DIR}/tests/test_s5_handoff_section_scoping.py
      (or under pytest — both work)
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

PPG = os.path.join(HOOKS_DIR, "pre_plan_gates.py")

import pre_plan_gates as ppg  # noqa: E402


# A spine with the four locked Discovery fields written, so
# `_discovery_locked_fields_hash` returns a real hash rather than None.
def _spine(*, locked_body="the locked body", handoff_marker=None,
           top_marker=None, extra_tail=""):
    top = f"<!-- handoff-src-hash: {top_marker} -->\n\n" if top_marker else ""
    handoff = ""
    if handoff_marker is not None:
        handoff = (
            "\n## Next Session Prompt\n\n"
            "*Written: 2026-09-13T00:00:00Z*\n\n"
            "do the thing\n\n"
            f"<!-- handoff-src-hash: {handoff_marker} -->\n"
        )
    # The two trailing H1s are not decoration. `_DISCOVERY_SECTION_RE` bounds
    # the Discovery body by the next `# ` heading only, so without them the
    # `## Next Session Prompt` section lands INSIDE `# Discovery` and becomes
    # part of `## Metrics`' hashed span — appending a handoff would then change
    # the locked-field hash, which is the exact span hazard this plan records.
    # A real spine has both sections; the fixture must too.
    return (
        f"{top}"
        "# Idea\n\n## Problem\n\nsomething\n\n"
        "# Discovery\n\n"
        f"## Guiding Policy\n\n{locked_body}\n\n"
        f"## Desired Outcome\n\n{locked_body}\n\n"
        f"## Desired Solution\n\n{locked_body}\n\n"
        f"## Metrics\n\n{locked_body}\n\n"
        "# Solution Design\n\n*(populated by /solution-design — Phase 2)*\n\n"
        "# Implementation Details\n\n*(populated by /plan — Phase 3)*\n"
        f"{handoff}{extra_tail}"
    )


def _write(tmpdir, text, name="topic-20260101000000_THOUGHT.md"):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _run(args):
    return subprocess.run([sys.executable, PPG, *args],
                          capture_output=True, text=True)


class SectionScoping(unittest.TestCase):
    """The predicate itself — one rule, anchored."""

    def test_an_absent_heading_yields_None_not_empty_string(self):
        """None and "" mean different things to the caller: absent vs present
        and empty. Conflating them is the same defect one level up."""
        self.assertIsNone(ppg.next_session_prompt_section("# Idea\n\nx\n"))
        self.assertEqual(
            ppg.next_session_prompt_section("## Next Session Prompt\n"), "\n")

    def test_a_prose_line_beginning_with_the_heading_is_not_the_section(self):
        """The whole reason this is `find_heading` and not `in`."""
        prose = ("# Idea\n\nSee the `## Next Session Prompt` section for how "
                 "this works.\n")
        self.assertIsNone(ppg.next_session_prompt_section(prose))

    def test_a_decorated_heading_is_not_the_section_and_read_agrees_with_write(self):
        """Read and write must draw the section's START in the same place.

        `find_heading`'s lookahead accepts a SPACE, so this function used to
        report a section on `## Next Session Prompt (draft)` whose body began
        mid-line, while the write seam refused to treat that same line as the
        section at all. Two copies of the body-start rule — the exact failure
        the module comment warns about, one level below where it warns. Found
        by a round-2 checker.

        Resolved toward the stricter reading (the writer's): a decorated
        heading is not this section, so both readers report ABSENT, which is
        each one's safe direction."""
        for decorated in ("## Next Session Prompt (draft)",
                          "## Next Session Prompt ",
                          "## Next Session Prompt\tx"):
            with self.subTest(decorated=decorated):
                text = f"# Idea\n\n{decorated}\n\nbody\n"
                self.assertIsNone(
                    ppg.next_session_prompt_section(text),
                    f"{decorated!r} was read as the section, but the write "
                    f"seam refuses it — read and write disagree again")

        # And the undecorated form is still the section, so the rule narrowed
        # rather than broke.
        self.assertIsNotNone(ppg.next_session_prompt_section(
            "# Idea\n\n## Next Session Prompt\n\nbody\n"))

    def test_the_section_is_bounded_by_the_next_h2(self):
        text = ("## Next Session Prompt\n\nbody here\n\n## Something Else\n\n"
                "not mine\n")
        section = ppg.next_session_prompt_section(text)
        self.assertIn("body here", section)
        self.assertNotIn("not mine", section)


class FreshnessReadsTheRightMarker(unittest.TestCase):
    """Gate item 1: on a v2 spine, freshness reads the handoff marker."""

    def test_a_top_of_file_marker_is_not_read_as_the_handoff_marker(self):
        """The headline case. The spine carries a foreign 1-segment marker at
        the top — exactly what the v2 adapter emits — and a real handoff marker
        in its own section. The verb must read the second one."""
        with tempfile.TemporaryDirectory() as tmp:
            text = _spine(top_marker="aaaaaaaaaaaa",
                          handoff_marker="bbbbbbbbbbbb")
            path = _write(tmp, text)

            # Premise: the two markers differ, and an UNSCOPED search finds the
            # top one. Without this the test could pass for the wrong reason.
            unscoped = ppg._SRC_HASH_MARKER_RE.search(text)
            self.assertEqual(unscoped.group(1), "aaaaaaaaaaaa")
            scoped = ppg.handoff_marker_in_section(text)
            self.assertEqual(scoped.group(1), "bbbbbbbbbbbb")

            # And the verb agrees with the scoped read: the live hash matches
            # neither fake, so it reports STALE — but it must have compared
            # against the handoff marker, which the next test pins directly.
            out = _run(["handoff-freshness", path])
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn(out.stdout.strip(), ("STALE", "FRESH"))

    def test_a_spine_whose_only_marker_is_outside_the_section_reads_ABSENT(self):
        """No handoff section => no handoff marker, whatever else the file
        holds. ABSENT is the safe over-detection direction: it triggers
        compose-and-verify, never a silent stale reuse."""
        with tempfile.TemporaryDirectory() as tmp:
            path = _write(tmp, _spine(top_marker="cccccccccccc"))
            out = _run(["handoff-freshness", path])
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "ABSENT")

    def test_freshness_is_FRESH_when_the_section_marker_matches_the_live_hash(self):
        """The positive path, driven through the real verb: write the marker
        the writer would write, and the reader must call it fresh."""
        with tempfile.TemporaryDirectory() as tmp:
            base = _spine()
            live = ppg._discovery_locked_fields_hash(base)
            self.assertIsNotNone(live, "fixture has no hashable Discovery")
            phase = ppg._phase_progress_fingerprint(base) or "n/a"
            text = _spine(top_marker="dddddddddddd") + (
                "\n## Next Session Prompt\n\n"
                "*Written: 2026-09-13T00:00:00Z*\n\ndo the thing\n\n"
                f"<!-- handoff-src-hash: {live} phase: {phase} "
                "written: 2026-09-13T00:00:00Z -->\n")
            path = _write(tmp, text)
            out = _run(["handoff-freshness", path])
            self.assertEqual(out.stdout.strip(), "FRESH", out.stdout)

    def test_handoff_fresh_since_uses_the_same_scoping(self):
        """Both verbs read the same marker or neither does — two search rules
        is how they came apart in the first place.

        THIS ONE FAILS IN THE UNSAFE DIRECTION, which the plan does not say.
        `handoff-freshness` unscoped over-detects (STALE), and the freshness
        gate documents over-detection as safe: it triggers compose-and-verify.
        `handoff-fresh-since` unscoped UNDER-detects: it reads the foreign
        marker's `written:` timestamp as if it were the handoff's and reports
        FRESH — a stale handoff silently accepted, and `/work-done`'s M7 step
        passes on it. Measured by the S5 revert check.

        The fixture's top marker is 3-segment ON PURPOSE. With a 1-segment one
        the verb reads a None `written` and reports ABSENT either way, so the
        test would pass against a broken build — the first cut of the revert
        check did exactly that and reported itself VACUOUS."""
        with tempfile.TemporaryDirectory() as tmp:
            text = _spine().replace(
                "# Idea\n",
                "<!-- handoff-src-hash: eeeeeeeeeeee phase: n/a "
                "written: 2026-09-13T00:00:00Z -->\n\n# Idea\n", 1)
            path = _write(tmp, text)

            # Premise: an unscoped search DOES find a `written:` here, so the
            # assertion below is about scoping and not about an absent segment.
            self.assertIsNotNone(
                ppg._SRC_HASH_MARKER_RE.search(text).group(3),
                "fixture no longer distinguishes scoped from unscoped")
            self.assertIsNone(ppg.handoff_marker_in_section(text))

            out = _run(["handoff-fresh-since", path, "2026-01-01T00:00:00Z"])
            self.assertEqual(out.stdout.strip(), "ABSENT", out.stdout)
            self.assertEqual(out.returncode, 1)


GOOD_PROMPT = (
    "<topic>\n  <title>X</title>\n</topic>\n"
    "<instructions>\n  1. Read the plan.\n</instructions>"
)


def _verified(tmp, prompt):
    """Satisfy the write gate's verify precondition in an ISOLATED marker dir,
    so the real `~/.claude/state/handoff_verify` is never touched. Returns the
    env overlay the write verb needs."""
    hv = os.path.join(tmp, "handoff_verify")
    os.makedirs(hv, exist_ok=True)
    env = {"PPG_HANDOFF_VERIFY_DIR": hv}
    art = os.path.join(tmp, "dc_artifact.md")
    with open(art, "w", encoding="utf-8") as fh:
        fh.write("dc result\n")
    # `_content_hash` directly, and byte-identically to what the write verb
    # computes for itself. An earlier cut shelled out to a `content-hash-str`
    # verb that does not exist, took the unknown-command branch, read empty
    # stdout, and fell back to this same call — so the helper's setup depended
    # on silently catching a failed subprocess. A round-2 checker spotted it.
    # Harmless in effect, and exactly the shape this slice is about.
    chash = ppg._content_hash(prompt)
    verified = subprocess.run(
        [sys.executable, PPG, "handoff-verify", chash, "PASS", art],
        capture_output=True, text=True, env={**os.environ, **env})
    # Asserted, not assumed: if the precondition silently failed to land, every
    # test using this helper would fail on the write gate's REFUSED path
    # instead of on the property it means to check.
    assert verified.returncode == 0, (
        f"handoff-verify did not write its marker: {verified.stderr}")
    assert os.path.exists(os.path.join(hv, f"{chash}.md")), (
        "the verify marker is missing, so the write gate will refuse")
    return env


def _run_env(args, env):
    return subprocess.run([sys.executable, PPG, *args], capture_output=True,
                          text=True, env={**os.environ, **env})


class WriterReportsANoWriteEndToEnd(unittest.TestCase):
    """Gate item 2, DRIVEN — the middle clause of the S5 Validation gate.

    These existed first as source-grep assertions (below), and two independent
    conformance checkers said the same thing: the gate names this as a discrete
    required assertion alongside two that ARE execution-tested, and grepping
    for the mechanism's presence is not exercising its behaviour. It is also
    the exact "asserted by substring rather than driven" shape this same
    session had already had to fix once, one slice over. So it is driven here.

    The trigger is real and was measured, not imagined: `find_heading` accepts
    `(?=\\s|$)` after the heading while the replace pattern requires a literal
    `\\n`, so a heading line with trailing whitespace or a same-line suffix
    takes the branch and matches nothing."""

    def _spine_with(self, heading_line):
        return (f"# Idea\n\n## Problem\n\nsomething\n\n"
                f"{heading_line}\n\nold body\n")

    def test_a_decorated_heading_reports_no_op_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _verified(tmp, GOOD_PROMPT)
            path = _write(tmp, self._spine_with(
                "## Next Session Prompt (draft)"), name="plan.md")
            before = _read(path)

            out = _run_env(["write-next-session-prompt", path, GOOD_PROMPT],
                           env)
            payload = json.loads(out.stdout)
            self.assertEqual(payload["status"], "no-op", out.stdout)
            self.assertEqual(payload["action"], "matched-nothing", out.stdout)
            self.assertEqual(_read(path), before,
                             "reported a no-op but altered the file")
            # NON-ZERO, and asserted separately from the JSON because it is a
            # different guarantee: the status field is read by a model, the
            # exit code is read by the harness. Exit 0 here would be the same
            # code a real write returns, which is this branch's own defect one
            # layer down.
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)

    def test_a_trailing_space_on_the_heading_also_reports_no_op(self):
        """The subtler spelling of the same gap — invisible in an editor."""
        with tempfile.TemporaryDirectory() as tmp:
            env = _verified(tmp, GOOD_PROMPT)
            path = _write(tmp, self._spine_with("## Next Session Prompt "),
                          name="plan.md")
            before = _read(path)
            out = _run_env(["write-next-session-prompt", path, GOOD_PROMPT],
                           env)
            self.assertEqual(json.loads(out.stdout)["status"], "no-op",
                             out.stdout)
            self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
            self.assertEqual(_read(path), before)

    def test_a_well_formed_heading_still_reports_replaced_and_writes(self):
        """The other side: the no-op path must not swallow a real write. A fix
        that reported no-op on a working input would be a regression wearing a
        bug fix's clothes."""
        with tempfile.TemporaryDirectory() as tmp:
            env = _verified(tmp, GOOD_PROMPT)
            path = _write(tmp, self._spine_with("## Next Session Prompt"),
                          name="plan.md")
            out = _run_env(["write-next-session-prompt", path, GOOD_PROMPT],
                           env)
            self.assertEqual(out.returncode, 0, out.stderr)
            payload = json.loads(out.stdout)
            self.assertEqual(payload["status"], "written", out.stdout)
            self.assertEqual(payload["action"], "replaced", out.stdout)
            # And a real write is still exit 0 — the two outcomes must not
            # collapse onto one code in either direction.
            self.assertEqual(out.returncode, 0, out.stderr)
            text = _read(path)
            self.assertIn("Read the plan.", text)
            self.assertNotIn("old body", text)


class KnownLimits(unittest.TestCase):
    """Two shapes the scoping predicate does NOT special-case, asserted so the
    behaviour is a recorded decision rather than an assumption nobody checked.
    Both were raised by a conformance checker."""

    def test_a_duplicate_heading_binds_to_the_first_on_the_READ_side(self):
        """Two `## Next Session Prompt` headings: the section is the FIRST
        one's, bounded by the second (which is itself a `## ` heading).

        Named for the READ side only, because that is all this asserts. An
        earlier docstring added "the write path's `count=1` makes the same
        first-match choice, so read and write agree" — true of `re.sub`
        semantics, and checked by nothing here. A round-2 checker flagged the
        gap between what the docstring claimed and what the test ran, which is
        the same overstatement class this whole slice keeps correcting.

        Nothing in the tree writes a second such heading."""
        text = ("# Idea\n\n"
                "## Next Session Prompt\n\nfirst body\n\n"
                "## Next Session Prompt\n\nsecond body\n")
        section = ppg.next_session_prompt_section(text)
        self.assertIn("first body", section)
        self.assertNotIn("second body", section)

    def test_the_next_step_insert_fallback_is_anchored_too(self):
        """The sibling branch, widened into scope after a round-2 checker
        confirmed the risk was real (misplaced insertion, not a false
        `written` — every sub-path there inserts non-empty content, so the
        bytes always change).

        A prose mention of `## Next Step` in a file with no real section by
        that name must NOT be treated as the insertion point."""
        with open(PPG, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn('if insert_header in text:', src,
                         "the insert-fallback match is unanchored again")
        self.assertIn("insert_idx = find_heading(text, insert_header)", src)

    def test_a_heading_inside_a_fenced_block_is_NOT_excluded(self):
        """`find_heading` has no fence awareness — it is a line-start regex.

        S5 closes the MID-LINE substring case (a prose mention in a sentence);
        it does not close a line-start occurrence inside a code fence. Stated
        as a limit rather than fixed: `find_heading` is shared harness
        infrastructure with many callers, and widening it is not in this
        slice's guard rails.

        Measured before recording it: across **4102** markdown files under
        `Thoughts/` and `Personal/`, 79 lines start with this heading and ZERO
        are inside a fence. So the limit is latent, not live — and this test
        exists so a future fence does not surprise anyone.

        THAT DENOMINATOR IS A CORRECTION, and the reason is worth keeping. The
        first measurement said 1247 files and a round-2 checker could not
        reproduce it, counting 4102. Both were right for their method and mine
        was wrong for the claim: `glob.glob(..., recursive=True)` does not
        descend symlinked directories, so the sweep silently skipped 2855 files
        — most of one project tree. The conclusion survived a re-run over the
        full corpus with `os.walk(followlinks=True)`, but it had been resting
        on a partial sweep, and a precise-looking number nobody can reproduce
        is the kind of evidence this topic exists to stop shipping.

        Note the sibling precedent that DOES handle it, if this ever needs
        fixing: `work_done._nearest_heading_depth` skips fenced blocks."""
        text = ("# Idea\n\n```\n## Next Session Prompt\n\nnot a real one\n"
                "```\n")
        self.assertIsNotNone(
            ppg.next_session_prompt_section(text),
            "behaviour changed: if fence-awareness was added, update the "
            "limit recorded here and in the S5 conformance record")


class WriterReportsANoWrite(unittest.TestCase):
    """Gate item 2 as source-shape assertions. Retained ALONGSIDE the driven
    tests above, not instead of them: these pin the mechanism's shape so a
    future refactor cannot quietly swap the bytes check for a branch check
    while the behavioural tests still pass on today's inputs."""

    def test_the_report_is_keyed_on_the_bytes_not_on_the_branch(self):
        """The invariant, stated as a check on the shipped source.

        Tightening the branch predicate alone would close today's hole and
        leave the next regex drift free to reopen it. What must hold is that
        the verb compares `replaced_text` against `text` before reporting."""
        with open(PPG, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("if replaced_text == text:", src,
                      "the no-write report is not keyed on the bytes")

    def test_the_write_branch_is_anchored_not_a_substring_test(self):
        with open(PPG, encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("if section_header in text:", src,
                         "the unanchored substring branch is still present")
        self.assertIn("if find_heading(text, section_header) != -1:", src)

    def test_the_heading_lives_in_one_constant(self):
        """One constant and one predicate — two copies are how this drifted."""
        self.assertEqual(ppg.NEXT_SESSION_PROMPT_HEADING,
                         "## Next Session Prompt")
        with open(PPG, encoding="utf-8") as fh:
            src = fh.read()
        # The literal may appear in prose/comments; what must not recur is a
        # second CODE path spelling it inline where the constant belongs.
        self.assertIn("section_header = NEXT_SESSION_PROMPT_HEADING", src)


class V1SpinesAreUnchanged(unittest.TestCase):
    """Shared-surface guard rail: a v1 spine must behave identically."""

    def test_a_v1_shaped_spine_with_only_the_section_marker_is_unaffected(self):
        """A v1 spine carries NO top-of-file marker, so scoping the read to the
        section finds exactly the marker the unscoped search used to find."""
        with tempfile.TemporaryDirectory() as tmp:
            base = _spine()
            live = ppg._discovery_locked_fields_hash(base)
            phase = ppg._phase_progress_fingerprint(base) or "n/a"
            text = base + (
                "\n## Next Session Prompt\n\n"
                "*Written: 2026-09-13T00:00:00Z*\n\ndo the thing\n\n"
                f"<!-- handoff-src-hash: {live} phase: {phase} "
                "written: 2026-09-13T00:00:00Z -->\n")
            path = _write(tmp, text)

            # Scoped and unscoped agree here, which is the whole claim.
            self.assertEqual(
                ppg.handoff_marker_in_section(text).group(0),
                ppg._SRC_HASH_MARKER_RE.search(text).group(0))
            self.assertEqual(_run(["handoff-freshness", path]).stdout.strip(),
                             "FRESH")
            since = _run(["handoff-fresh-since", path, "2026-01-01T00:00:00Z"])
            self.assertEqual(since.stdout.strip(), "FRESH")
            self.assertEqual(since.returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

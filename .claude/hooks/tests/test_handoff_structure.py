#!/usr/bin/env python3
"""Unit tests for handoff_structure.py (E4 shared handoff recognizer).

Covers Outcome Claims C1/C5: a handoff prompt is recognized (so the nudge is
skipped) and ordinary new-topic prose is NOT recognized (so the nudge survives).
"""
import os
import subprocess
import sys
import unittest

HOOKS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HOOKS_DIR not in sys.path:
    sys.path.insert(0, HOOKS_DIR)

import handoff_structure as hs  # noqa: E402

MODULE_PATH = os.path.join(HOOKS_DIR, "handoff_structure.py")

GOOD_PROMPT = (
    "<topic>\n  <title>X</title>\n</topic>\n"
    "<instructions>\n  1. Read the plan.\n</instructions>"
)

# A realistic multi-line handoff with surrounding prose (this session's shape).
REALISTIC_HANDOFF = (
    "Continuing the topic.\n\n"
    "<topic>\n  <title>slice S3</title>\n"
    "  <diagnosis>read-side surfacing</diagnosis>\n</topic>\n\n"
    "<instructions>\n  1. Read the spine fully.\n  2. Plan S3.\n"
    "</instructions>\n"
)


class IsHandoffPromptTests(unittest.TestCase):
    def test_canonical_good_prompt_is_handoff(self):
        self.assertTrue(hs.is_handoff_prompt(GOOD_PROMPT))

    def test_realistic_multiline_handoff_is_handoff(self):
        self.assertTrue(hs.is_handoff_prompt(REALISTIC_HANDOFF))

    def test_plain_prose_is_not_handoff(self):
        self.assertFalse(
            hs.is_handoff_prompt("please help me with a new caching idea")
        )

    def test_topic_only_is_not_handoff(self):
        self.assertFalse(
            hs.is_handoff_prompt("<topic>\n  <title>X</title>\n</topic>")
        )

    def test_instructions_only_is_not_handoff(self):
        self.assertFalse(
            hs.is_handoff_prompt("<instructions>\n  1. do it\n</instructions>")
        )

    def test_wrong_order_is_not_handoff(self):
        # instructions before topic is not the template shape
        self.assertFalse(
            hs.is_handoff_prompt(
                "<instructions>go</instructions>\n<topic>x</topic>"
            )
        )

    def test_prose_mentioning_topic_tag_is_not_handoff(self):
        # C5: bare mention of a tag must NOT suppress the nudge
        self.assertFalse(
            hs.is_handoff_prompt("I want a <topic> for my new blog series")
        )

    def test_empty_string_is_not_handoff(self):
        self.assertFalse(hs.is_handoff_prompt(""))

    def test_none_is_not_handoff(self):
        self.assertFalse(hs.is_handoff_prompt(None))


class CliTests(unittest.TestCase):
    def _run(self, verb, stdin_text):
        return subprocess.run(
            [sys.executable, MODULE_PATH, verb],
            input=stdin_text,
            capture_output=True,
            text=True,
        )

    def test_cli_is_handoff_exit_0_on_handoff(self):
        r = self._run("is-handoff", GOOD_PROMPT)
        self.assertEqual(r.returncode, 0)

    def test_cli_is_handoff_exit_1_on_prose(self):
        r = self._run("is-handoff", "just a normal new-topic prompt here")
        self.assertEqual(r.returncode, 1)

    def test_cli_is_handoff_exit_1_on_empty(self):
        r = self._run("is-handoff", "")
        self.assertEqual(r.returncode, 1)

    def test_cli_prompt_leading_dash_flag_is_safe(self):
        # a prompt beginning with -n / -e must still be classified by content,
        # not swallowed as a flag (printf-safety analogue at the module level:
        # the text arrives via stdin, so leading dashes are literal)
        handoff_with_dash = "-n -e trailing flags\n" + GOOD_PROMPT
        r = self._run("is-handoff", handoff_with_dash)
        self.assertEqual(r.returncode, 0)

    def test_cli_self_test_passes(self):
        r = self._run("--self-test", "")
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()

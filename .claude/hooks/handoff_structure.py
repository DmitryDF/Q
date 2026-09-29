#!/usr/bin/env python3
"""Shared handoff-prompt structural recognizer (E4 / AD-6).

Single locus for "is this message a session-handoff prompt?". A handoff prompt
is the structured message a `_thought`'s `## Next Session Prompt` produces and
the operator pastes verbatim into a fresh session — it always pairs a
``<topic>`` block with an ``<instructions>`` block (the canonical template at
``~/.claude/rules/prompt-engineering.md`` "Handoff Prompts — PE Guidance").

This module is a standalone, unit-testable adapter with NO Claude-specific
imports (code-first standalone-check-logic per ``skill-location.md``). It is
consumed by:
  * ``clarification-nudge.sh`` (M10) — skip the new-topic nudge on a handoff.
  * the future Phase-2 Stop hook (S4) — reuse the same recognition, no re-impl.

Recognizing the shape here once is what makes a future handoff-structure change
a single-file edit (Cockburn Evolution Test).

CLI:
  is-handoff   read stdin; exit 0 if it is a handoff prompt, exit 1 otherwise.
  --self-test  run the built-in assertions; exit 0 on pass, 1 on failure.
"""
import re
import sys

# Requires BOTH a <topic>…</topic> block AND an <instructions>…</instructions>
# block, in that order (the template order). DOTALL so the blocks may span
# multiple lines. Requiring both paired open+close tags is the specificity that
# keeps ordinary prose merely mentioning "<topic>" from being misread as a
# handoff (Outcome Claim C5).
HANDOFF_STRUCTURE_RE = re.compile(
    r"<topic>.*?</topic>.*<instructions>.*?</instructions>",
    re.DOTALL,
)


def is_handoff_prompt(text):
    """Return True iff *text* carries the paired handoff blocks.

    Never raises: a non-string / empty / garbage input returns False.
    """
    if not text or not isinstance(text, str):
        return False
    return HANDOFF_STRUCTURE_RE.search(text) is not None


def _self_test():
    good = (
        "<topic>\n  <title>X</title>\n</topic>\n"
        "<instructions>\n  1. Read the plan.\n</instructions>"
    )
    cases = [
        (good, True),                                             # canonical
        ("please help me with a new idea about caching", False),  # prose
        ("<topic>\n  <title>X</title>\n</topic>", False),         # topic only
        ("<instructions>\n  1. do it\n</instructions>", False),   # instr only
        ("", False),                                              # empty
        (None, False),                                            # non-string
        # instructions-before-topic (wrong order) is not the template shape
        ("<instructions>go</instructions>\n<topic>x</topic>", False),
        # realistic multi-line handoff with surrounding prose
        (
            "Continuing the topic.\n\n<topic>\n  <title>slice S3</title>\n"
            "  <diagnosis>read-side surfacing</diagnosis>\n</topic>\n\n"
            "<instructions>\n  1. Read the spine fully.\n  2. Plan S3.\n"
            "</instructions>\n",
            True,
        ),
    ]
    failed = 0
    for text, expected in cases:
        got = is_handoff_prompt(text)
        if got != expected:
            failed += 1
            sys.stderr.write(
                "FAIL: expected %r got %r for %r\n" % (expected, got, text)
            )
    if failed:
        sys.stderr.write("handoff_structure self-test: %d FAILED\n" % failed)
        return 1
    sys.stdout.write("handoff_structure self-test: all passed\n")
    return 0


def main(argv):
    if len(argv) >= 2 and argv[1] == "is-handoff":
        try:
            text = sys.stdin.read()
        except Exception:
            return 1
        return 0 if is_handoff_prompt(text) else 1
    if len(argv) >= 2 and argv[1] == "--self-test":
        return _self_test()
    sys.stderr.write(
        "usage: handoff_structure.py {is-handoff | --self-test}\n"
        "  is-handoff  : read stdin, exit 0 if a handoff prompt, 1 otherwise\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

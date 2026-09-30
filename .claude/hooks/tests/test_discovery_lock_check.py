#!/usr/bin/env python3
"""Regression tests for _discovery_lock_check.py.

Two generations of coverage live here.

D-series (2026-06-12) covers the bug where `_extract_field_body` scanned the
whole spine instead of just the `# Discovery` section, so any Phase-2/Phase-3
write (e.g., appending `# Solution Design`) tripped the lock-check block path
even when the four locked Discovery fields were unchanged.

  D1   Edit that appends `# Solution Design` to a fully-locked spine -> exit 0
  D2   Edit that mutates the body of `## Desired Outcome` inside Discovery -> exit 2

L-series (2026-08-09, discovery-lock-topic-state-lookup_PLAN) covers
`_find_topic_state`'s two-tier identity resolution. Each test is labelled with
its baseline disposition, because the plan requires every defect test to be
shown FAILING against the pre-fix guard:

  [FAILS-BEFORE]  exercises the defect; must fail against the unmodified guard.
  [PASSES-BOTH]   a must-not-widen / must-not-regress guard; green either way.

Run: python3 <this file>            (tests the guard in the SAME tree)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Resolve the hook path RELATIVE TO THIS FILE, never via Path.home().
# `Path.home()` would make a staged copy of this suite exercise the LIVE guard,
# so a develop-in-a-clone workflow would silently verify nothing
# (discovery-lock-topic-state-lookup_PLAN, A4 — do this before anything else).
HOOK_SCRIPT = Path(__file__).resolve().parent.parent / "_discovery_lock_check.py"

TS = "20260809010707"          # a conformant 14-digit slug timestamp
HOLDER = "sess-holder"
OTHER = "sess-other"


LOCKED_SPINE = """# Idea

Some idea text.

# Discovery

## Guiding Policy
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

## Desired Outcome
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

## Metrics
<!-- locked: 2026-06-12 12:00 -->
- OMTM: locked metric

## Scope
Some scope notes.

## Q&A
Q: stuff
A: stuff
"""


# A spine at Step 9 with ONLY the first marker written. Markers 2-4 are still
# to come, which is the four-Edit sequence the plan's scope boundary describes.
PARTIALLY_LOCKED_SPINE = """# Idea

Some idea text.

# Discovery

## Guiding Policy
<!-- locked: 2026-08-09 01:00 -->
Locked policy text.

## Desired Outcome
Outcome text.

## Desired Solution
Solution text.

## Metrics
- OMTM: a metric

## Scope
Some scope notes.

## Q&A
Q: stuff
A: stuff
"""


def _run_hook(payload, home_override, projects_root=None):
    """Run the guard as a subprocess under an overridden HOME.

    HOME redirects TOPIC_STATE_DIR (it is `Path.home()`-relative). `projects_root`
    additionally seeds PPG_PROJECTS_ROOT so a fixture never consults the live
    vault root.
    """
    env = os.environ.copy()
    env["HOME"] = str(home_override)
    env.pop("PPG_PROJECTS_ROOT", None)
    if projects_root is not None:
        env["PPG_PROJECTS_ROOT"] = str(projects_root)
    return subprocess.run(
        [sys.executable, str(HOOK_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
    )


class DiscoveryLockCheckTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dlc_test_")
        # Empty TOPIC_STATE_DIR under fake HOME so state lookup misses
        (Path(self.tmp) / ".claude" / "state" / "pre_plan_gates").mkdir(parents=True)
        self.thought_path = Path(self.tmp) / "fixture_dlc_THOUGHT.md"
        self.thought_path.write_text(LOCKED_SPINE, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_phase2_write_outside_discovery_is_allowed(self):
        """Appending `# Solution Design` after a fully-locked Discovery must not block."""
        old_string = "Q: stuff\nA: stuff\n"
        new_string = old_string + "\n# Solution Design\n\n### Solution Alternative 1\nbody\n"
        payload = {
            "tool_name": "Edit",
            "session_id": "test-sess",
            "tool_input": {
                "file_path": str(self.thought_path),
                "old_string": old_string,
                "new_string": new_string,
            },
        }
        result = _run_hook(payload, self.tmp)
        self.assertEqual(
            result.returncode, 0,
            f"Expected exit 0 (out-of-Discovery write allowed); got {result.returncode}.\n"
            f"stderr: {result.stderr}",
        )

    def test_in_discovery_edit_is_blocked(self):
        """Mutating the body of `## Desired Outcome` inside Discovery must block."""
        payload = {
            "tool_name": "Edit",
            "session_id": "test-sess",
            "tool_input": {
                "file_path": str(self.thought_path),
                "old_string": "Locked outcome text.",
                "new_string": "Tampered outcome text.",
            },
        }
        result = _run_hook(payload, self.tmp)
        self.assertEqual(
            result.returncode, 2,
            f"Expected exit 2 (in-Discovery edit blocked); got {result.returncode}.\n"
            f"stderr: {result.stderr}",
        )
        self.assertIn("Desired Outcome", result.stderr)


# --------------------------------------------------------------------------- #
# A-series — anchored extraction + marker shape
#   (discovery-field-match-anchoring_PLAN, A5)
#
# Same [FAILS-BEFORE] / [PASSES-BOTH] labelling the L-series uses: a defect test
# must be shown failing against the pre-fix guard, or it proves nothing.
# --------------------------------------------------------------------------- #

# A spine whose MUTABLE `## Q&A` prose mentions a locked field's heading name in
# backticks. Under the unanchored `str.find`, `## Metrics` bound to that mention
# (which sits EARLIER in the file than the real heading), so the lock compared
# the wrong bytes. No fixture in this suite carried a prose mention before.
PROSE_MENTION_SPINE = """# Idea

Some idea text.

# Discovery

## Q&A
Q: where does the OMTM live?
A: the `## Metrics` field holds it, one line, prefixed `OMTM:`.

## Guiding Policy
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

## Desired Outcome
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

## Metrics
<!-- locked: 2026-06-12 12:00 -->
OMTM: the real locked metric

## Scope
Some scope notes.
"""

# The three-token marker clarification-v2 emits
# (translating_repository.py:337). The two-token regex could not span the extra
# space, so the hook exited at its marker gate before examining any field.
V2_MARKER = "<!-- locked: metrics 7f3a1c92-0b44-4d21-9e88-2c5f6a1b3d70 2026-08-08T00:29:41Z -->"

V2_SPINE = """# Idea

Some idea text.

# Discovery

## Guiding Policy
{m}
Locked policy text.

## Desired Outcome
{m}
Locked outcome text.

## Desired Solution
{m}
Locked solution text.

## Metrics
{m}
OMTM: the real locked metric

## Scope
Some scope notes.
""".format(m=V2_MARKER)

# A real decorated heading shape from the corpus — trailing lock emoji plus
# parenthetical prose. End-anchoring the predicate would stop matching these.
DECORATED_SPINE = """# Idea

Some idea text.

# Discovery

## Guiding Policy 🔒 (Step 7 — LOCKED by user 2026-06-15)
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

## Desired Outcome — own words (Шаг 4 — утверждено)
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

## Metrics 🔒 (Step 8 — LOCKED by user 2026-06-15; see Q&A)
<!-- locked: 2026-06-12 12:00 -->
OMTM: the real locked metric

## Scope
Some scope notes.
"""

# `## Guiding Policy:` — a heading the anchored predicate deliberately REJECTS
# (the trailing colon breaks the whitespace boundary). It is present in Discovery
# all the same, so treating it as absent would silently stop defending it.
#
# It must sit BEFORE every readable locked field, and that placement is
# load-bearing rather than cosmetic. An unreadable heading placed LATER is
# swallowed by the preceding field's body — it no longer terminates that field —
# so an edit under it still trips the ordinary comparison and blocks for an
# unrelated reason. A fixture built that way passes with A4 removed and proves
# nothing (measured: `## Metrics:` in last position blocks either way). Only a
# field with no readable locked field above it lands in no body at all, which is
# the `current_body is None -> continue` path A4 exists to guard.
UNREADABLE_HEADING_SPINE = """# Idea

Some idea text.

# Discovery

## Guiding Policy:
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

## Desired Outcome
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

## Metrics
<!-- locked: 2026-06-12 12:00 -->
OMTM: the real locked metric

## Scope
Some scope notes.
"""

# A5 (discovery-field-predicate-coherence S1) — three regression fixtures.
#
# Placement rule, inherited from the comment above UNREADABLE_HEADING_SPINE and
# re-stated because getting it wrong makes a fixture pass for the wrong reason:
# an unreadable heading placed AFTER a readable locked field is swallowed by that
# field's body, so an edit beneath it trips the ordinary comparison and blocks
# for an unrelated reason. Only an unreadable heading with no readable locked
# field ABOVE it lands in no body at all, which is the
# `current_body is None -> continue` path A3 now allows.

# Every locked field mis-levelled — the zero-readable-locked-field case. The lock
# marker is present (so the hook does not exit at its marker gate) and yet
# `locked_fields_changed` is necessarily empty, because no field resolves. The
# loop that used to follow — A4's `unrecognized` scan — is what A3 deleted, so
# this fixture is the one that proves its removal left no dangling reference.
NO_READABLE_LOCKED_FIELD_SPINE = """# Idea

Some idea text.

# Discovery

### Guiding Policy
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

## Desired Outcome:
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

  ## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

### Metrics
<!-- locked: 2026-06-12 12:00 -->
OMTM: the real locked metric

## Scope
Some scope notes.
"""

# `### Metrics` as the ONLY Metrics heading, and first, so it lands in no body.
# The H3 collision is the near-miss shape most likely to be authored by hand,
# because `###` is what a writer reaches for when nesting under `# Discovery`.
H3_ONLY_SPINE = """# Idea

Some idea text.

# Discovery

### Metrics
<!-- locked: 2026-06-12 12:00 -->
OMTM: the real locked metric

## Guiding Policy
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

## Desired Outcome
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

## Scope
Some scope notes.
"""

# The opposite sign of the same collision: an H3 decoy named `Metrics` sitting
# INSIDE another locked field's body, with the real `## Metrics` further down.
# `extract_heading_body` must bind the real heading, not the decoy — if it bound
# the decoy, an edit to the real OMTM line would leave the decoy-bound body
# untouched, flag nothing, and exit 0.
H3_DECOY_SPINE = """# Idea

Some idea text.

# Discovery

## Guiding Policy
<!-- locked: 2026-06-12 12:00 -->
Locked policy text.

### Metrics
A sub-heading inside the policy body, not the locked field.

## Desired Outcome
<!-- locked: 2026-06-12 12:00 -->
Locked outcome text.

## Desired Solution
<!-- locked: 2026-06-12 12:00 -->
Locked solution text.

## Metrics
<!-- locked: 2026-06-12 12:00 -->
OMTM: the real locked metric

## Scope
Some scope notes.
"""


class AnchoredExtractionLockTests(unittest.TestCase):
    """End-to-end through the hook subprocess, like the D-series — never by
    calling the extractor directly, which would not prove the hook blocks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dlc_anchor_")
        (Path(self.tmp) / ".claude" / "state" / "pre_plan_gates").mkdir(parents=True)
        self.path = Path(self.tmp) / "fixture_anchor_THOUGHT.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text):
        self.path.write_text(text, encoding="utf-8")

    def _edit(self, old, new):
        return _run_hook({
            "tool_name": "Edit",
            "session_id": "test-sess",
            "tool_input": {"file_path": str(self.path),
                           "old_string": old, "new_string": new},
        }, self.tmp)

    # -- C1: the bypass ---------------------------------------------------- #

    def test_locked_edit_blocked_despite_prose_mention(self):
        """[FAILS-BEFORE] The demonstrated bypass. A prose mention of `## Metrics`
        sits earlier in Discovery than the real heading, so the unanchored
        extractor bound to it; editing the REAL locked OMTM line then left that
        mention unchanged, no field was flagged, and the hook exited 0."""
        self._write(PROSE_MENTION_SPINE)
        r = self._edit("OMTM: the real locked metric", "OMTM: a tampered metric")
        self.assertEqual(
            r.returncode, 2,
            f"Expected exit 2 — an edit to the real locked OMTM line must be "
            f"refused even though `## Metrics` is also mentioned in Q&A prose. "
            f"Got {r.returncode}.\nstderr: {r.stderr}")
        self.assertIn("Metrics", r.stderr)

    # -- C6: the permit direction ------------------------------------------ #

    def test_mutable_qa_edit_allowed_despite_prose_mention(self):
        """[FAILS-BEFORE] The opposite sign of the same defect. Editing genuinely
        MUTABLE Q&A prose changed the mis-bound "Metrics body", so the hook
        flagged a locked field nobody touched and refused a legitimate edit.

        The edited text must sit INSIDE the mis-bound body to reproduce this.
        Against the pre-change extractor the `## Metrics` body is
        `'` field holds it, one line, prefixed `OMTM:`.'` — everything between
        the Q&A mention and the next heading — so an edit to the `Q:` line
        ABOVE the mention leaves it untouched and wrongly passes."""
        self._write(PROSE_MENTION_SPINE)
        r = self._edit("one line, prefixed", "a single line, prefixed")
        self.assertEqual(
            r.returncode, 0,
            f"Expected exit 0 — editing mutable Q&A prose leaves every locked "
            f"body untouched and must be allowed. Got {r.returncode}.\n"
            f"stderr: {r.stderr}")

    # -- C7: the v2 door ---------------------------------------------------- #

    def test_v2_three_token_marker_is_guarded(self):
        """[FAILS-BEFORE] The second root cause. `LOCK_MARKER_RE` accepted exactly
        two tokens, so on a v2 spine the hook took `sys.exit(0)` at its marker
        gate before examining any field — every locked field on every v2 spine
        was editable with no refusal at all."""
        self._write(V2_SPINE)
        r = self._edit("Locked outcome text.", "Tampered outcome text.")
        self.assertEqual(
            r.returncode, 2,
            f"Expected exit 2 — a three-token clarification-v2 marker must open "
            f"the same gate a two-token v1 marker opens. Got {r.returncode}.\n"
            f"stderr: {r.stderr}")
        self.assertIn("Desired Outcome", r.stderr)

    def test_v1_two_token_marker_unchanged(self):
        """[PASSES-BOTH] Must-not-widen: widening the token count must not change
        how a v1 spine behaves."""
        self._write(LOCKED_SPINE)
        self.assertEqual(self._edit("Locked outcome text.", "Tampered.").returncode, 2)
        self.assertEqual(
            self._edit("Some scope notes.", "Some other scope notes.").returncode, 0)

    def test_marker_gate_still_requires_a_real_marker(self):
        """[PASSES-BOTH] Must-not-widen: the widened pattern must not become
        something ordinary prose can satisfy, or the gate means nothing."""
        self._write(LOCKED_SPINE.replace("<!-- locked: 2026-06-12 12:00 -->",
                                         "locked: 2026-06-12 12:00"))
        r = self._edit("Locked outcome text.", "Tampered outcome text.")
        self.assertEqual(
            r.returncode, 0,
            "A spine with no real `<!-- locked: ... -->` marker is not yet "
            f"locked and must pass. Got {r.returncode}.\nstderr: {r.stderr}")

    # -- C5: decorated headings --------------------------------------------- #

    def test_decorated_headings_block_and_permit(self):
        """[PASSES-BOTH] DO-1d. Exercises BOTH lock directions on a decorated
        spine — the pairing the plan's Coverage argument relies on and that C5
        alone (recognition only) does not assert.

        **This is also A5's third named regression, "decorated-still-defended",
        which is why it is re-pointed here rather than re-authored below.** A5
        lists three regressions to add; two were genuinely missing (zero-locked-
        field and the `###` collision, both added under "A5: the three
        regressions" further down) and this one already existed, asserting exactly
        the property A5 names, in both directions, end-to-end through the hook.
        Writing a second copy would be one idea in two places free to drift —
        the failure the slice that owns this test exists to close, and the same
        reason A2 *moves* `_field_status` rather than deleting and re-authoring it.

        What A3 could have broken and did not: a decorated heading
        (`## Metrics 🔒 (…)`) is ACCEPTED by the anchored predicate, so
        `_field_status` returned `matched` and A4's guard never fired on this
        spine. It therefore passed for the same reason before A3 and after —
        checked, not assumed, because a regression that passes for the wrong
        reason is worth less than no regression at all."""
        self._write(DECORATED_SPINE)
        self.assertEqual(
            self._edit("OMTM: the real locked metric", "OMTM: tampered").returncode, 2,
            "an edit to a locked field under a decorated heading must be refused")
        self.assertEqual(
            self._edit("Some scope notes.", "Other scope notes.").returncode, 0,
            "an edit elsewhere on a decorated-heading spine must be allowed")

    # -- A3/A5: the reversal, and what bounds it ----------------------------- #

    def test_unreadable_heading_is_treated_as_absent_at_the_lock_site(self):
        """[FAILS-BEFORE] **This test asserted the OPPOSITE until 2026-09-18.**

        It was `test_unreadable_heading_is_not_treated_as_absent`, and it was the
        encoding of the A4 guard: a locked field whose heading the anchored
        predicate rejects must not take the absent-and-therefore-allowed path.
        Action A3 of `~/.claude/plans/lucky-juggling-whale.md` deleted that guard,
        so the decision this test encodes is reversed. It is REWRITTEN rather than
        deleted — deleting a test to go green destroys the record that the
        decision was ever made the other way.

        Why the reversal, in one line: the guard answered "is this field here"
        with a bare `field in body` substring while the locator it compensated for
        used the anchored predicate — looser evidence than the thing it guarded —
        and on 2026-08-29 it locked a spine's own author out of the only edit that
        would have repaired the heading, while its own message prescribed exactly
        that edit.

        What bounds the reversal is asserted in the two tests below, not here:
        an in-tool demotion is still refused, and a readable field on the same
        spine is still defended. Read the three together — this one alone would
        licence a widening Q3b did not."""
        self._write(UNREADABLE_HEADING_SPINE)
        r = self._edit("Locked policy text.", "Tampered policy text.")
        self.assertEqual(
            r.returncode, 0,
            "A locked field whose heading the predicate cannot read is absent as "
            "far as the lock is concerned, and the edit proceeds. The near-miss "
            "is REPORTED by `_validate-thought-file.py`, not refused here.\n"
            f"Got {r.returncode}.\nstderr: {r.stderr}")

    def test_reversal_does_not_reach_a_readable_field_on_the_same_spine(self):
        """[PASSES-BOTH] Must-not-widen, direction 1. A3 removes a refusal keyed
        to ONE unreadable heading; every readable locked field on that same spine
        keeps its defence. If this ever goes to 0, the deletion took the whole
        field loop with it rather than the guard."""
        self._write(UNREADABLE_HEADING_SPINE)
        r = self._edit("OMTM: the real locked metric", "OMTM: tampered")
        self.assertEqual(
            r.returncode, 2,
            "`## Metrics` is readable on this spine and must still be defended "
            f"after A3. Got {r.returncode}.\nstderr: {r.stderr}")
        self.assertIn("Metrics", r.stderr)

    def test_in_tool_demotion_of_a_readable_heading_is_still_refused(self):
        """[PASSES-BOTH] Must-not-widen, direction 2 — the second of the two
        residual defences A3's guard rail names. Demoting `## Metrics` to
        `### Metrics` from inside the tool is refused by the ordinary comparison,
        because the current body is text while the proposed body is None. So the
        only state A3 newly permits is a spine ALREADY unreadable, reached
        out-of-band — which is the narrow, nameable residual the plan accepted,
        not a licence to break a lock in two steps."""
        self._write(LOCKED_SPINE)
        r = self._edit("## Metrics", "### Metrics")
        self.assertEqual(
            r.returncode, 2,
            "Demoting a readable locked heading must still be refused — "
            "otherwise A3's residual becomes reachable from inside the tool.\n"
            f"Got {r.returncode}.\nstderr: {r.stderr}")

    def test_unreadable_heading_does_not_block_untouched_discovery(self):
        """[PASSES-BOTH] Predates A3 and is unaffected by it: an edit outside
        `# Discovery` cannot have changed a locked body, so it was allowed under
        the guard and is allowed without it. Kept as the control that this
        fixture's exit-0 results are not simply the hook failing open."""
        self._write(UNREADABLE_HEADING_SPINE)
        r = self._edit("# Idea\n\nSome idea text.",
                       "# Idea\n\nSome idea text, revised.")
        self.assertEqual(
            r.returncode, 0,
            f"An edit outside `# Discovery` cannot have changed a locked body. "
            f"Got {r.returncode}.\nstderr: {r.stderr}")

    # -- A5: the three regressions ------------------------------------------- #

    def test_zero_readable_locked_fields_exits_clean(self):
        """[FAILS-BEFORE] Regression 1 — zero-locked-field.

        Every locked field on this spine is mis-levelled, so none resolves and
        `locked_fields_changed` is necessarily empty. Two things are asserted and
        the second is the one that matters:

          * the edit is allowed (exit 0) — under A4's guard all four fields were
            `unmatched`, so any Discovery-touching edit was refused;
          * the hook exits **0, not 1**. A3 deleted the block that referenced
            `_field_status`, and A2 deleted the function itself; had either landed
            without the other, this spine would raise `NameError` OUTSIDE the only
            `try` in `main()`, Python would exit 1, and PreToolUse treats exit 1 as
            NON-blocking — no guard at all, silently. That is the ordering hazard
            A2's and A3's guard rails both name, and this is where it would show."""
        self._write(NO_READABLE_LOCKED_FIELD_SPINE)
        r = self._edit("Locked policy text.", "Revised policy text.")
        self.assertEqual(
            r.returncode, 0,
            "A spine with no readable locked field has nothing to compare and "
            f"must exit 0. Got {r.returncode}.\nstderr: {r.stderr}")
        self.assertNotIn(
            "Traceback", r.stderr,
            "exit 1 from an uncaught exception is treated as NON-blocking by "
            f"PreToolUse — the worst failure mode here.\nstderr: {r.stderr}")

    def test_h3_heading_does_not_satisfy_the_locked_field(self):
        """[FAILS-BEFORE] Regression 2a — the `###` collision, absent direction.

        `### Metrics` is not `## Metrics`. It resolves to None, so post-A3 the
        edit proceeds; under A4's guard it was `unmatched` and refused. The
        compensating report lives in `_validate-thought-file.py` and is asserted
        in `test_clarification_schema.py`, not here — this file only proves the
        lock no longer refuses on it."""
        self._write(H3_ONLY_SPINE)
        r = self._edit("OMTM: the real locked metric", "OMTM: revised metric")
        self.assertEqual(
            r.returncode, 0,
            "An H3 heading is not the locked field, so the lock has no body to "
            f"compare and must not refuse. Got {r.returncode}.\n"
            f"stderr: {r.stderr}")

    def test_h3_decoy_does_not_capture_the_real_locked_field(self):
        """[PASSES-BOTH] Regression 2b — the `###` collision, present direction,
        and the sharper half. An H3 `### Metrics` sits inside the Guiding Policy
        body while the real `## Metrics` sits further down. If the decoy bound as
        the Metrics body, an edit to the real OMTM line would leave it unchanged,
        flag no field and exit 0 — the bypass this whole locator change exists to
        close, re-entering through the near-miss door."""
        self._write(H3_DECOY_SPINE)
        r = self._edit("OMTM: the real locked metric", "OMTM: tampered")
        self.assertEqual(
            r.returncode, 2,
            "the real `## Metrics` must bind, not the H3 decoy above it. "
            f"Got {r.returncode}.\nstderr: {r.stderr}")
        self.assertIn("Metrics", r.stderr)


# --------------------------------------------------------------------------- #
# L-series — `_find_topic_state` two-tier identity resolution
# --------------------------------------------------------------------------- #

class _LookupFixture:
    """Shared fixture for the L-series. Deliberately NOT a TestCase — a
    TestCase base would re-run every inherited test in each subclass and
    inflate the counts.

    Fixture tree (all paths realpath-stable):

        <tmp>/home/.claude/state/pre_plan_gates/   TOPIC_STATE_DIR (HOME override)
        <tmp>/projects/TODO.md                     makes <tmp>/projects the vault root
        <tmp>/projects/Thoughts/<slug>_THOUGHT.md  root-level spine  -> project "Root"
        <tmp>/projects_link -> <tmp>/projects      the vault symlink pair
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dlc_lookup_")).resolve()
        self.home = self.tmp / "home"
        self.state_dir = self.home / ".claude" / "state" / "pre_plan_gates"
        self.state_dir.mkdir(parents=True)

        self.projects = self.tmp / "projects"
        (self.projects / "Thoughts").mkdir(parents=True)
        (self.projects / "TODO.md").write_text("# TODO\n", encoding="utf-8")

        # The symlink half of the vault pair ($CLAUDE_PROJECT_DIR -> repos/Projects).
        self.projects_link = self.tmp / "projects_link"
        self.projects_link.symlink_to(self.projects, target_is_directory=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fixture helpers ---------------------------------------------------- #

    def _spine(self, slug, *, timestamped=True, text=LOCKED_SPINE, subdir=None):
        """Write a spine and return its resolved absolute path."""
        name = f"{slug}-{TS}_THOUGHT.md" if timestamped else f"{slug}_THOUGHT.md"
        base = self.projects if subdir is None else (self.projects / subdir)
        (base / "Thoughts").mkdir(parents=True, exist_ok=True)
        path = base / "Thoughts" / name
        path.write_text(text, encoding="utf-8")
        return path

    def _record(self, stem, *, thought_file_path=None, project_root=None, token=None):
        """Write one topic-state record named `<stem>.json`."""
        doc = {"topic_slug": stem.split("__")[0], "project_slug": "Root"}
        if thought_file_path is not None:
            doc["thought_file_path"] = str(thought_file_path)
        if project_root is not None:
            doc["project_root"] = str(project_root)
        if token is not None:
            doc["clarification_active_session"] = token
        (self.state_dir / f"{stem}.json").write_text(
            json.dumps(doc), encoding="utf-8")

    def _edit(self, spine, session_id=HOLDER,
              old="Locked outcome text.", new="Revised outcome text."):
        return _run_hook(
            {"tool_name": "Edit", "session_id": session_id,
             "tool_input": {"file_path": str(spine),
                            "old_string": old, "new_string": new}},
            self.home, projects_root=self.projects)

    def _assert_allowed(self, res, why):
        self.assertEqual(res.returncode, 0,
                         f"{why}\nExpected exit 0 (allow); got {res.returncode}."
                         f"\nstderr: {res.stderr}")

    def _assert_blocked(self, res, why):
        self.assertEqual(res.returncode, 2,
                         f"{why}\nExpected exit 2 (block); got {res.returncode}."
                         f"\nstderr: {res.stderr}")


class TopicStateLookupTests(_LookupFixture, unittest.TestCase):

    # -- Tier A: spine-path identity, all four stored forms ------------------ #

    def test_timestamped_spine_pathless_record_resolves(self):
        """[FAILS-BEFORE] Timestamped spine + path-less record: route 2's
        substring test is dead (the slug still carries `-<14 digits>`), so the
        holder's token was never consulted. This is the dominant live shape."""
        spine = self._spine("dlc-ts")
        self._record("dlc-ts__Root", token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "path-less record keyed by name must admit its holder")

    def test_relative_stored_path_resolves(self):
        """[FAILS-BEFORE] 59 of 103 live records store a relative path; a raw
        string compare can never match an absolute path."""
        spine = self._spine("dlc-rel")
        self._record("dlc-rel__Root",
                     thought_file_path=f"Thoughts/dlc-rel-{TS}_THOUGHT.md",
                     project_root=self.projects, token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "relative stored path must resolve against project_root")

    def test_relative_path_without_project_root_resolves_via_env_seam(self):
        """[FAILS-BEFORE] 21 live records are relative AND carry no
        `project_root`; they resolve only against the vault root, which is why
        the PPG_PROJECTS_ROOT seam has to exist for this class to be testable."""
        spine = self._spine("dlc-noroot")
        self._record("dlc-noroot__Root",
                     thought_file_path=f"Thoughts/dlc-noroot-{TS}_THOUGHT.md",
                     token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "relative path with no project_root must resolve via the vault root")

    def test_symlink_form_absolute_path_resolves(self):
        """[FAILS-BEFORE] 16 live records store the `$CLAUDE_PROJECT_DIR`
        symlink form; the hook is called with the resolved form. Same file, two
        strings."""
        spine = self._spine("dlc-sym")
        self._record("dlc-sym__Root",
                     thought_file_path=self.projects_link / "Thoughts"
                                       / f"dlc-sym-{TS}_THOUGHT.md",
                     token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "symlink-form and resolved-form paths denote one file")

    def test_resolved_absolute_path_still_resolves(self):
        """[PASSES-BOTH] The one form route 1 already matched — must not regress."""
        spine = self._spine("dlc-abs")
        self._record("dlc-abs__Root", thought_file_path=spine, token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "resolved absolute path must keep resolving")

    def test_empty_string_path_is_treated_as_pathless(self):
        """[FAILS-BEFORE] A record whose stored path is `""` records no path.
        Testing `is None` instead of falsiness strands it in neither tier."""
        spine = self._spine("dlc-empty")
        self._record("dlc-empty__Root", thought_file_path="", token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "empty-string path must fall through to name identity")

    # -- Tier B: name identity, read from the key segments ------------------- #

    def test_inverted_key_record_resolves_via_its_segments(self):
        """[FAILS-BEFORE] 57 live records store the halves inverted. Identity is
        read as an unordered segment set, so `Root__<slug>` still resolves."""
        spine = self._spine("dlc-inv")
        self._record("Root__dlc-inv", token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "an inverted key still carries both identity halves")

    def test_three_segment_stem_parses_without_positional_indexing(self):
        """[FAILS-BEFORE] `<a>__<b>__<c>` records exist live
        (`Projects__workflow-phases-redesign__parked-F-postship`). Membership
        over a split list handles them; 'the other segment' is never computed."""
        spine = self._spine("dlc-three")
        self._record("dlc-three__Root__parked-F-postship", token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "a 3-segment stem carrying both halves must resolve")

    def test_record_naming_a_moved_spine_does_not_bind_by_name(self):
        """[FAILS-BEFORE] A record that names another file must reach NEITHER
        tier — even when that file is gone. Keying Tier B on 'no path
        RESOLVABLE' instead of 'no path RECORDED' would make it name-reachable,
        which is the fail-open direction."""
        spine = self._spine("dlc-moved", timestamped=False)
        self._record("dlc-moved__Root",
                     thought_file_path=f"Thoughts/dlc-moved-gone_THOUGHT.md",
                     project_root=self.projects, token=HOLDER)
        self._assert_blocked(self._edit(spine),
                             "a record pointing at a different file must not bind by name")

    # -- Exactness: a resembling name must not bind -------------------------- #

    def test_resembling_names_do_not_bind_prefix_suffix_infix(self):
        """[PASSES-BOTH] Must-not-widen guard. No token anywhere, so both the old
        and the new guard block; what this pins is that the fix did not make a
        containment match into an identity match."""
        spine = self._spine("dlc-core", timestamped=False)
        self._record("dlc-core-extra__Root")      # prefix containment
        self._record("pre-dlc-core__Root")        # suffix containment
        self._record("x-dlc-core-y__Root")        # infix containment
        self._assert_blocked(self._edit(spine),
                             "containment is not identity")

    def test_another_topics_token_does_not_unlock_a_resembling_spine(self):
        """[FAILS-BEFORE] The silent-bypass defect: session S clarifies topic Y,
        then edits topic X's spine, and the unanchored substring binds Y's
        record — so S's token unlocks a topic it is not clarifying."""
        spine = self._spine("dlc-core", timestamped=False)
        self._record("dlc-core-extra__Root", token=HOLDER)
        self._assert_blocked(self._edit(spine),
                             "a token held on a merely-resembling topic must not unlock this one")

    def test_non_holder_is_blocked(self):
        """[PASSES-BOTH] The property the guard exists to hold."""
        spine = self._spine("dlc-nonholder")
        self._record("dlc-nonholder__Root", token=HOLDER)
        self._assert_blocked(self._edit(spine, session_id=OTHER),
                             "a session holding no token for this topic must be refused")

    # -- Identity as a SET: several records, one spine ----------------------- #

    def test_token_found_across_the_candidate_set_tier_straddle(self):
        """[FAILS-BEFORE] The token sits on a path-less sibling while another
        record stores the path. Gating Tier B on 'Tier A is empty' would drop
        the holder here — and every live token holder is path-less."""
        spine = self._spine("dlc-straddle")
        self._record("dlc-straddle__Root",                 # Tier A, no token
                     thought_file_path=f"Thoughts/dlc-straddle-{TS}_THOUGHT.md",
                     project_root=self.projects)
        self._record("dlc-straddle__Root__sibling", token=HOLDER)   # Tier B, token
        self._assert_allowed(self._edit(spine),
                             "the token must be sought across every record describing this spine")

    def test_several_records_one_spine_admit_whichever_holds_the_token(self):
        """[FAILS-BEFORE] The live three-record `workflow-phases-redesign` group.
        Treating 'more than one candidate' as a reason to refuse would block
        these topics permanently."""
        spine = self._spine("dlc-group")
        rel = f"Thoughts/dlc-group-{TS}_THOUGHT.md"
        self._record("dlc-group__Root", thought_file_path=rel,
                     project_root=self.projects)
        self._record("dlc-group__Root__parked", thought_file_path=rel,
                     project_root=self.projects)
        self._record("dlc-group-alias__Root", thought_file_path=rel,
                     project_root=self.projects, token=HOLDER)
        self._assert_allowed(self._edit(spine),
                             "records resolving to one file are one identity, however keyed")

    # -- Project agreement is a PER-RECORD admission condition --------------- #

    def test_foreign_project_pathless_record_does_not_unlock(self):
        """[FAILS-BEFORE] Two path-less topics in different projects can share a
        slug; merging them lets one project's token unlock the other's spine."""
        spine = self._spine("dlc-shared", timestamped=False)
        self._record("dlc-shared__someproject", token=HOLDER)
        self._assert_blocked(self._edit(spine),
                             "a name alone cannot tell two same-named topics in different projects apart")

    def test_foreign_project_pathless_record_does_not_unlock_behind_a_tier_a_anchor(self):
        """[PASSES-BOTH*] The variant a SET-level project check would have missed:
        with a valid Tier-A anchor present, a set-level rule stops applying and
        the foreign record rides in unchecked. Per-record admission closes it.

        (*) Before the fix this case is enumeration-order dependent, so its
        baseline result is not contractual; after the fix it is deterministic.
        """
        spine = self._spine("dlc-anchored", timestamped=False)
        self._record("dlc-anchored__Root",                  # valid Tier-A anchor
                     thought_file_path=spine)
        self._record("dlc-anchored__someproject", token=HOLDER)   # foreign, path-less
        self._assert_blocked(self._edit(spine),
                             "a foreign path-less record must not lend its token behind an anchor")

    # -- Determinism ---------------------------------------------------------- #

    def test_outcome_is_stable_across_repeated_runs(self):
        """[FAILS-BEFORE] Routes interleaved inside one unsorted glob let a weak
        route-2 hit on an earlier file shadow an exact route-1 match on a later
        one, so the same inputs could reach different verdicts."""
        spine = self._spine("dlc-det", timestamped=False)
        self._record("dlc-det-lookalike__Root", token=HOLDER)     # must not bind
        self._record("dlc-det__Root", thought_file_path=spine)    # binds, no token
        codes = {self._edit(spine).returncode for _ in range(5)}
        self.assertEqual(codes, {2},
                         f"expected a stable block across repeats; saw {codes}")

    # -- The Step-9 four-marker sequence -------------------------------------- #

    def test_step9_four_marker_sequence_completes_for_the_holder(self):
        """[FAILS-BEFORE] The triggering incident. Marker 1 is exempt (no marker
        present yet); markers 2-4 each change a field body and so each needs the
        token. Written as four separate Edits, the natural shape."""
        spine = self._spine("dlc-step9", text=PARTIALLY_LOCKED_SPINE)
        self._record("dlc-step9__Root", token=HOLDER)
        marker = "<!-- locked: 2026-08-09 01:00 -->"
        for heading in ("## Desired Outcome", "## Desired Solution", "## Metrics"):
            res = self._edit(spine, old=heading, new=f"{heading}\n{marker}")
            self._assert_allowed(res, f"marker write after `{heading}` must be admitted")

    # -- A2 env-seam behaviour ------------------------------------------------ #

    def test_invalid_projects_root_is_loud_and_fails_closed(self):
        """[FAILS-BEFORE] A set-but-invalid PPG_PROJECTS_ROOT must be loud on
        stderr AND unresolvable. Silently falling back to the live root would let
        a harness that forgot to mkdir run against real data and pass for the
        wrong reason; raising would exit 1, which PreToolUse treats as
        NON-blocking (fail-open)."""
        spine = self._spine("dlc-badroot")
        self._record("dlc-badroot__Root",
                     thought_file_path=f"Thoughts/dlc-badroot-{TS}_THOUGHT.md",
                     token=HOLDER)
        bad = self.tmp / "not-a-directory.txt"
        bad.write_text("x", encoding="utf-8")
        res = _run_hook(
            {"tool_name": "Edit", "session_id": HOLDER,
             "tool_input": {"file_path": str(spine),
                            "old_string": "Locked outcome text.",
                            "new_string": "Revised outcome text."}},
            self.home, projects_root=bad)
        self.assertEqual(res.returncode, 2,
                         f"expected a fail-CLOSED block, got {res.returncode}\n{res.stderr}")
        self.assertIn("PPG_PROJECTS_ROOT", res.stderr,
                      "the bad value must be named loudly on stderr")


# --------------------------------------------------------------------------- #
# L-series — operator-facing block messages
# --------------------------------------------------------------------------- #

class BlockMessageTests(_LookupFixture, unittest.TestCase):

    def test_lock_refusal_names_reentering_clarification(self):
        """[PASSES-BOTH] A genuine lock refusal keeps today's remedy — which
        becomes correct once the lookup is fixed, since `--from` writes the token
        onto the record found by session and the guard now finds that record."""
        spine = self._spine("dlc-msg-lock")
        self._record("dlc-msg-lock__Root", token=HOLDER)
        res = self._edit(spine, session_id=OTHER)
        self.assertEqual(res.returncode, 2)
        self.assertIn("Desired Outcome", res.stderr)
        self.assertIn("/clarification --from", res.stderr)

    def test_unidentified_topic_names_set_thought_file(self):
        """[FAILS-BEFORE] When nothing describes this spine the failure is
        identity resolution, not the lock — and the remedy that lifts it is
        `set-thought-file`, the harness's own documented backfill."""
        spine = self._spine("dlc-msg-none")
        res = self._edit(spine, session_id=HOLDER)
        self.assertEqual(res.returncode, 2)
        self.assertIn("set-thought-file", res.stderr,
                      "the no-record branch must prescribe the remedy that lifts it")


if __name__ == "__main__":
    unittest.main()

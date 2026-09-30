#!/usr/bin/env python3
"""A9 — keeping the chain-acceptance ledger honest across the predicate change.

`clarify_chain_metrics.classify_consumption` is the THIRD stored-hash comparator,
and unlike the other two it does not self-heal. Two of its branches turn a
*corrected* hash into a false failure verdict:

  :415  sealed_hash != declared  -> "lock:locked-field-edited"
        Compares a DURABLY PERSISTED seal-time hash against the spine's marker.
        A spine re-rendered after the fix declares the corrected hash while the
        ledger still holds the pre-fix one, so a correction reads as a lock
        violation. A persisted row does not heal itself.

  :418  declared != recomputed   -> "contract:hash-mismatch"
        Compares the marker against a fresh recompute. An AFFECTED spine that has
        NOT been re-rendered still declares the pre-fix hash while the corrected
        function returns the new one, so a correction reads as content drift.

What keeps the no-backfill decision safe is NOT self-healing but
UNREACHABILITY: `_record_consumption_for_spine` returns before classifying
unless the spine carries the v2 chrome marker `<!-- clarify:meta {`, which no
spine in the corpus does, and the ledger holds only fixture rows. These tests
pin that precondition so it cannot lapse silently, and pin both genuine signals
so the repair cannot be mistaken for disarming them.

Measured at implementation time (2026-08-16): 49 ledger rows, all under the
fixture slug `slug-1`, 0 real seal rows; 0 of 83 corpus spines carry the marker.
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "_ccm_anchor", HOOKS / "clarify_chain_metrics.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["_ccm_anchor"] = m
    spec.loader.exec_module(m)
    return m


ccm = _load()

PRE_FIX_HASH = "dbb5d767303e"     # what the unanchored extractor produced
POST_FIX_HASH = "5742897d1fb7"    # what the anchored extractor produces

V2_SPINE_TEMPLATE = """<!-- clarify:meta {{"schema": 1}} -->
# Idea

Some idea.

# Discovery

## Guiding Policy
policy

## Desired Outcome
outcome

## Desired Solution
solution

## Metrics
OMTM: m

<!-- handoff-src-hash: {declared} -->
"""
# NB the BARE 12-hex marker, not the three-segment `phase:`/`written:` form.
# That is what clarification-v2 actually writes
# (`translating_repository.py:367`), and it is the only form
# `clarify_chain_metrics._HANDOFF_MARKER_RE` accepts. Using the three-segment
# form here made every branch return `contract:missing-marker`, so the tests
# passed through a path A9 is not about.


def spine(declared):
    return V2_SPINE_TEMPLATE.format(declared=declared)


class LedgerPreconditionTests(unittest.TestCase):
    """The precondition the whole A9 disposition rests on. A9's guard rail says
    to STOP and re-scope rather than delete ledger history if this ever fails —
    the ledger is the OMTM record this fix is judged by."""

    def test_ledger_holds_no_real_seal_rows(self):
        p = ccm.ledger_path()
        if not p.exists():
            return
        real = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("record") == "seal" and row.get("slug") != "slug-1":
                real.append(row)
        self.assertEqual(
            real, [],
            "The chain-acceptance ledger now holds REAL seal rows. The "
            "no-backfill decision assumed none, because a persisted pre-fix "
            "seal makes a corrected hash read as `lock:locked-field-edited`. "
            "STOP and re-scope — do not delete ledger history.")

    def test_no_corpus_spine_is_a_v2_spine(self):
        """The second half of unreachability: without the chrome marker,
        `_record_consumption_for_spine` never reaches the classifier."""
        root = Path("~/repos/Projects")
        if not root.exists():
            self.skipTest("corpus not present")
        marker = re.compile(r"<!--\s*clarify:meta\s+\{")
        hits = [p for p in root.rglob("*_THOUGHT.md")
                if p.parent.name == "Thoughts" and "_retired" not in p.parts
                and marker.search(p.read_text(encoding="utf-8", errors="replace"))]
        self.assertEqual(
            [str(p) for p in hits], [],
            "A spine now carries the v2 chrome marker, so the two "
            "non-self-healing comparator branches have become reachable.")


class UnreachabilityTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ccm_anchor_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_affected_non_v2_spine_records_nothing(self):
        """An AFFECTED, un-re-rendered spine that is not a v2 spine must not be
        classified at all — this is what stops the predicate change producing a
        `contract:hash-mismatch` on the four spines whose hash was corrected."""
        p = self.tmp / "affected-20260101000000_THOUGHT.md"
        p.write_text(spine(PRE_FIX_HASH).replace(
            '<!-- clarify:meta {"schema": 1} -->\n', ""), encoding="utf-8")
        out = ccm._record_consumption_for_spine(
            str(p), consumer="/plan", session_id="s",
            hash_fn=lambda _t: POST_FIX_HASH, root=self.tmp)
        self.assertIsNone(
            out, "a non-v2 spine must record nothing, whatever its hash says")


class BranchBehaviourTests(unittest.TestCase):
    """Both branches, on both sides: the predicate change must not manufacture a
    failure, and neither genuine signal may be disarmed to achieve that."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ccm_branch_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- :415, the seal branch --------------------------------------------- #

    def test_re_rendered_spine_with_no_seal_row_classifies_pass(self):
        """A9(a). With no seal row — the ledger's actual state — a spine
        re-rendered after the fix classifies `pass`, because the seal branch is
        never entered."""
        outcome, reason, _ = ccm.classify_consumption(
            spine(POST_FIX_HASH),
            sealed_hash=ccm.sealed_hash_for("nonexistent-slug", self.tmp),
            hash_fn=lambda _t: POST_FIX_HASH)
        self.assertEqual((outcome, reason), ("pass", ccm.PASS_REASON))

    def test_genuine_locked_field_edit_still_fails(self):
        """A9(c). The signal must still fire: a seal recorded at X, a marker
        since moved to Y, means the locked fields really were re-emitted."""
        outcome, reason, _ = ccm.classify_consumption(
            spine("aaaaaaaaaaaa"),
            sealed_hash="bbbbbbbbbbbb",
            hash_fn=lambda _t: "aaaaaaaaaaaa")
        self.assertEqual((outcome, reason), ("fail", "lock:locked-field-edited"))

    def test_stale_pre_fix_seal_against_re_rendered_spine_is_the_known_hazard(self):
        """Recorded, not asserted away. IF a real pre-fix seal row existed and
        the spine were then re-rendered, this comparator WOULD report a lock
        violation on a correction. Nothing in the code prevents that — only the
        precondition above does. Pinning the behaviour here means the hazard is
        visible in the test surface rather than living in a plan nobody re-reads."""
        outcome, reason, _ = ccm.classify_consumption(
            spine(POST_FIX_HASH),
            sealed_hash=PRE_FIX_HASH,
            hash_fn=lambda _t: POST_FIX_HASH)
        self.assertEqual(
            (outcome, reason), ("fail", "lock:locked-field-edited"),
            "If this ever stops being the behaviour, the seal signal has been "
            "weakened — which A9 forbids. The hazard is held off by the "
            "emptiness precondition, not by softening this branch.")

    # -- :418, the recompute branch ---------------------------------------- #

    def test_re_rendered_spine_does_not_report_hash_mismatch(self):
        """A9(b), the reachable half. Once a spine IS re-rendered, marker and
        recompute agree again and the drift branch stays silent."""
        outcome, reason, _ = ccm.classify_consumption(
            spine(POST_FIX_HASH), sealed_hash=None,
            hash_fn=lambda _t: POST_FIX_HASH)
        self.assertEqual((outcome, reason), ("pass", ccm.PASS_REASON))

    def test_genuine_content_drift_still_fails(self):
        """A9(d). The other signal must still fire: marker says X, the spine's
        current locked fields hash to Y — the content under the marker drifted."""
        outcome, reason, _ = ccm.classify_consumption(
            spine("aaaaaaaaaaaa"), sealed_hash=None,
            hash_fn=lambda _t: "cccccccccccc")
        self.assertEqual((outcome, reason), ("fail", "contract:hash-mismatch"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

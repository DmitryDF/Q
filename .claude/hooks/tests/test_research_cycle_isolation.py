#!/usr/bin/env python3
"""Regression tests for research-fc cycle namespacing (research-fc-cycle-namespacing, 2026-07-28).

Maps to the plan's Outcome Claims:
  C1 — a named research cycle whose default cycle is already at max_rounds runs its own rounds
       (from round 1 on a fresh cycle) and produces its own verdict; it does NOT NOOP against
       the default's markers.
  C2 — each cycle's markers are written to / counted from the per-cycle path the gate readers
       already use (research/<cycle>/), so writer and reader agree.
  C3 — the default cycle keeps the flat path; a non-research kind is unaffected (no {slug}_None_
       mirror); a present-but-unreadable manifest fails fast rather than silently contaminating.
  C4 — advisory slug-mirror copies are cycle-scoped (covered indirectly via the None-mirror test;
       full same-slug collision is advisory/never gate-read).
  C5 — this file is the two-cycles-on-one-topic proof, including the production resolver seam.

Imports the _factcheck_engine sitting next to this test (the experiment clone during
development, live ~/.claude/hooks after promotion) — never a hard-coded live path.

Run: python3 ${KIT_HOOKS_DIR}/tests/test_research_cycle_isolation.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _factcheck_engine as eng  # noqa: E402


def _mock_resolver(proj, topic):
    state = {"topic_slug": proj, "project_slug": topic}
    return lambda _sid: (proj, topic, state)


def _has_marker(directory, key=None):
    """Did a canonical marker land DIRECTLY in this cycle directory?

    These tests are about CYCLE nesting — whether a run's markers land flat in
    `research/` or nested in `research/<cycle>/`. They used the literal name
    `R1.md` as the proxy for "a marker landed here". Since the per-file keying
    slice (Group I item 2) the research kind names its markers `<key>_R<n>.md`,
    so that proxy no longer resolves; the nesting behaviour under test is
    unchanged. Locator change only — every assertion keeps its original intent.
    """
    d = Path(directory)
    if not d.is_dir():
        return False
    for p in d.glob("*.md"):
        m = re.match(r"^(?:(?P<key>.+)_)?R\d+\.md$", p.name)
        if m and (key is None or m.group("key") == key):
            return True
    return False


def _marker_cycles(base):
    """Which cycle(s) do the round markers on disk actually belong to?

    A marker sitting flat in `research/` belongs to the default cycle; one
    nested in `research/<cycle>/` belongs to `<cycle>`. Returns the set of
    cycle names the marker TRAIL names, which is one of the two surfaces the
    Desired Outcome requires to agree.
    """
    base = Path(base)
    found = set()
    if not base.is_dir():
        return found
    for p in base.rglob("*.md"):
        if not re.match(r"^(?:(?P<key>.+)_)?R\d+\.md$", p.name):
            continue
        rel = p.relative_to(base).parent
        found.add("default" if rel == Path(".") else rel.parts[0])
    return found


def _frontmatter_cycles(draft_path):
    """Which cycle(s) does the report's own fc_cycles: block name?

    The second of the two surfaces. Reads the row the engine wrote back into
    the research file — the thing a human opens the report and sees.
    """
    text = Path(draft_path).read_text(encoding="utf-8")
    return set(_CYCLE_ROW_RE.findall(text))


_CYCLE_ROW_RE = re.compile(r'^\s*-\s*cycle:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)


class _CountingChecker:
    """Injectable _checker_fn that records the round_num of every dispatch."""

    def __init__(self, verdict="PASS"):
        self.rounds = []
        self.verdict = verdict

    def __call__(self, draft_path, idx, model, round_num, prior):
        self.rounds.append(round_num)
        return self.verdict

    @property
    def dispatches(self):
        return len(self.rounds)


class ResearchCycleIsolationTests(unittest.TestCase):
    PROJ = "TestRCI"
    TOPIC = "cycle-iso"
    SID = "test-rci"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "validation"
        self.draft = Path(self.tmpdir) / "mytopic-20260101010101_RESEARCH.md"
        self.draft.write_text("---\ntitle: t\n---\n# Body — no cited URLs\n")
        self.resolver = _mock_resolver(self.PROJ, self.TOPIC)
        # Point the research-pipeline manifest lookup at our tmp dir so no real
        # ~/.claude/state/research_pipeline/RP-<sid>.json can interfere.
        self.rp_dir = Path(self.tmpdir) / "rp"
        self.rp_dir.mkdir()
        self._prev_rp = os.environ.get("RP_STATE_DIR")
        os.environ["RP_STATE_DIR"] = str(self.rp_dir)

    def tearDown(self):
        if self._prev_rp is None:
            os.environ.pop("RP_STATE_DIR", None)
        else:
            os.environ["RP_STATE_DIR"] = self._prev_rp
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- helpers -------------------------------------------------------------

    def _research_base(self):
        return self.state_dir / self.PROJ / self.TOPIC / "research"

    def _seed_default(self, n):
        d = self._research_base()
        d.mkdir(parents=True, exist_ok=True)
        for i in range(1, n + 1):
            (d / f"R{i}.md").write_text(f"---\nverdict: PASS\n---\nround {i}\n")
        return d

    def _write_manifest(self, cycles):
        (self.rp_dir / f"RP-{self.SID}.json").write_text(json.dumps({"cycles": cycles}))

    def _run(self, checker, kind="research", cycle_id=None, force=False, max_rounds=3,
             draft=None):
        return eng.factcheck_run(
            self.state_dir, str(draft or self.draft), kind, self.SID,
            debounce_seconds=0,
            _checker_fn=checker,
            _proj_topic_resolver=self.resolver,
            max_rounds=max_rounds,
            force=force,
            cycle_id=cycle_id,
        )

    # -- C1/C2: the headline — named cycle vs maxed-out default --------------

    def test_named_cycle_does_not_noop_against_maxed_default(self):
        base = self._seed_default(3)                      # default maxed at max_rounds=3
        checker = _CountingChecker("PASS")
        result = self._run(checker, cycle_id="de", max_rounds=3)

        self.assertNotEqual(result.get("status"), "NOOP",
                            "named cycle must NOT NOOP against the default's markers")
        self.assertGreater(checker.dispatches, 0, "named cycle must dispatch checkers")
        self.assertEqual(min(checker.rounds), 1, "fresh named cycle starts at round 1")
        # writer path == per-cycle reader path (research/<cycle>/)
        self.assertTrue(_has_marker(base / "de"),
                        "named-cycle marker written under research/de/")
        # default markers untouched; no R4 leaked into the default dir
        for i in (1, 2, 3):
            self.assertTrue((base / f"R{i}.md").exists(), "default markers intact")
        self.assertFalse((base / "R4.md").exists(), "no marker leaked into the default dir")
        self.assertFalse(_has_marker(base, key="mytopic"),
                         "the named cycle wrote nothing into the default dir")
        # shared .lock stays at the flat lock_dir, NOT inside the nested per-cycle dir
        self.assertTrue((base / ".lock").exists(), ".lock at the flat per-kind lock_dir")
        self.assertFalse((base / "de" / ".lock").exists(), "no .lock inside the nested dir")

    # -- Group I item 1: the marker trail and the report's own row agree -----

    def test_named_cycle_frontmatter_row_names_the_same_cycle_as_the_markers(self):
        """The frontmatter-cycle side the marker-path tests never covered.

        Group I item 1. `factcheck_run` resolves the cycle ONCE and commits it;
        the terminal writeback must record THAT value, not re-derive its own.
        Before the fix the writeback re-called `_resolve_research_cycle_id`,
        which finds no manifest here and answers "default" — so the markers
        landed under `research/de/` while the report's row said `default`. One
        run, two cycles, and the disagreement is invisible from either side
        alone, which is why it needs a test that reads BOTH.

        This is the plan's observable test: run a fact-check under a named
        cycle, then read the recorded verdict row and the marker trail — both
        must name the same cycle.
        """
        base = self._research_base()
        self._run(_CountingChecker("PASS"), cycle_id="de", max_rounds=3)

        marker_cycles = _marker_cycles(base)
        row_cycles = _frontmatter_cycles(self.draft)

        self.assertEqual(marker_cycles, {"de"},
                         "precondition: the marker trail is nested under the named cycle")
        self.assertTrue(row_cycles,
                        "the run must write a terminal fc_cycles row at all")
        self.assertEqual(
            row_cycles, marker_cycles,
            "the report's fc_cycles row and the marker trail must name the SAME "
            f"cycle (row={sorted(row_cycles)}, markers={sorted(marker_cycles)}) — "
            "a mismatch means the writeback re-derived the cycle instead of "
            "recording the one factcheck_run committed",
        )

    def test_production_seam_frontmatter_row_names_the_same_cycle_as_the_markers(self):
        """The same agreement on the path production actually uses.

        Here the dispatcher passes `cycle_id=None` and the manifest maps this
        draft to `de`, so both derivations saw the same inputs and agreed even
        before the fix — this test is GREEN on both sides of it. It is the
        non-regression guard: threading the committed value must not break the
        resolver-driven path that every live dispatch takes today.
        """
        base = self._research_base()
        self._write_manifest({
            "default": {"research_file_path": str(Path(self.tmpdir) / "other_RESEARCH.md")},
            "de": {"research_file_path": str(self.draft)},
        })
        self._run(_CountingChecker("PASS"), cycle_id=None, max_rounds=3)

        marker_cycles = _marker_cycles(base)
        row_cycles = _frontmatter_cycles(self.draft)

        self.assertEqual(marker_cycles, {"de"}, "resolver-driven nesting still fires")
        self.assertEqual(row_cycles, marker_cycles,
                         "resolver-driven path keeps both surfaces in agreement")

    # -- C3: the default cycle keeps the flat path --------------------------

    def test_default_cycle_stays_flat(self):
        base = self._research_base()
        checker = _CountingChecker("PASS")
        self._run(checker, cycle_id="default", max_rounds=3)
        self.assertTrue(_has_marker(base), "default cycle writes flat into research/")
        self.assertFalse((base / "default").exists(), "no research/default/ subdir is created")

    def test_cycle_none_no_manifest_flat(self):
        base = self._research_base()
        checker = _CountingChecker("PASS")
        # cycle_id=None + no manifest → resolver returns default → flat, never raises.
        self._run(checker, cycle_id=None, max_rounds=3)
        self.assertTrue(_has_marker(base), "no-manifest default run is flat")
        self.assertFalse((base / "default").exists())

    # -- C5: the production resolver seam (cycle_id=None + manifest) ---------

    def test_production_seam_resolver_nests_named_cycle(self):
        base = self._research_base()
        # manifest maps THIS draft to the "de" cycle; dispatch passes cycle_id=None.
        self._write_manifest({
            "default": {"research_file_path": str(Path(self.tmpdir) / "other_RESEARCH.md")},
            "de": {"research_file_path": str(self.draft)},
        })
        checker = _CountingChecker("PASS")
        self._run(checker, cycle_id=None, max_rounds=3)
        self.assertTrue(_has_marker(base / "de"),
                        "resolver-driven nesting fires from cycle_id=None")

    def test_legitimate_default_in_multilang_does_not_raise(self):
        base = self._research_base()
        # THIS draft is the registered "default" cycle in an EN+DE manifest → cstate not None.
        self._write_manifest({
            "default": {"research_file_path": str(self.draft)},
            "de": {"research_file_path": str(Path(self.tmpdir) / "de_RESEARCH.md")},
        })
        checker = _CountingChecker("PASS")
        self._run(checker, cycle_id=None, max_rounds=3)   # must NOT raise
        self.assertTrue(_has_marker(base), "genuine default match stays flat")
        self.assertFalse((base / "default").exists())

    def test_population_window_default_registered_empty_path_no_raise(self):
        base = self._research_base()
        # "default" registered but its research_file_path not populated yet; a named cycle IS
        # populated. The draft matches nothing → case (c) → "default" in cycles → flat, no raise.
        self._write_manifest({
            "default": {"research_file_path": None},
            "de": {"research_file_path": str(Path(self.tmpdir) / "de_RESEARCH.md")},
        })
        checker = _CountingChecker("PASS")
        self._run(checker, cycle_id=None, max_rounds=3)   # must NOT raise
        self.assertTrue(_has_marker(base), "population-window default proceeds flat")

    def test_suspicious_mismatch_raises(self):
        # named cycle registered, NO "default" key, draft matches nothing → fail fast.
        self._write_manifest({
            "de": {"research_file_path": str(Path(self.tmpdir) / "de_RESEARCH.md")},
        })
        checker = _CountingChecker("PASS")
        with self.assertRaises(ValueError):
            self._run(checker, cycle_id=None, max_rounds=3)

    def test_present_but_unreadable_manifest_raises(self):
        # manifest file EXISTS but is invalid JSON → must fail fast, never silent-flat.
        (self.rp_dir / f"RP-{self.SID}.json").write_text("{not valid json,,,")
        checker = _CountingChecker("PASS")
        with self.assertRaises(ValueError):
            self._run(checker, cycle_id=None, max_rounds=3)

    # -- C3/C4: the cycle_id-or-"default" normalization (no {slug}_None_ mirror) --

    def test_non_research_kind_has_no_none_mirror(self):
        # kind="thought" writes an advisory slug-mirror next to the draft; cycle_id is NOT
        # resolved (only research resolves) so it stays None → normalized to "default".
        # The mirror MUST be {slug}_R{n}.md, never {slug}_None_R{n}.md.
        draft = Path(self.tmpdir) / "mytopic-20260101010101_THOUGHT.md"
        draft.write_text("---\ntitle: t\n---\n# thought body\n")
        checker = _CountingChecker("PASS")
        self._run(checker, kind="thought", cycle_id=None, max_rounds=3, draft=draft)
        draft_dir = draft.parent
        self.assertEqual(list(draft_dir.glob("*_None_R*.md")), [],
                         "no {slug}_None_R{n}.md mirror may be produced")
        self.assertTrue(list(draft_dir.glob("mytopic_R*.md")),
                        "the plain {slug}_R{n}.md mirror is produced (normalization worked)")


if __name__ == "__main__":
    unittest.main()

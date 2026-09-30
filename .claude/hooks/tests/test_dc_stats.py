#!/usr/bin/env python3
"""Tests for the code-only /double-check Stats.md cost aggregator (Slice S8).

Plan: Thoughts/double-check-validation-targeting_S8_PLAN.md (Coherent Actions A1/A2).

Coverage:
  C1  discovery re-derives the project's audit markers from disk — co-located
      under the project root AND fallback mirror-tree markers whose `source_path`
      resolves within the project (a read-only-source run still counts); an
      out-of-project fallback marker is excluded and a malformed marker is
      skipped. aggregate_dc_stats sums the raw cost (tokens+time) + per-checker
      discrepancy rows + run/verdict counts grouped by (caller, topic).
  C2  write_dc_stats renders the sentinel-delimited block into <project>/Stats.md
      idempotently (insert-or-replace), preserving all content outside the
      sentinels (the manual /close session-metrics table); first-run creates the
      file; zero markers initializes an empty block without error.

All against a synthetic FIXTURE project tree (never the live corpus), with the
fallback-marker root redirected to a tmp dir.
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dc_stats as ds  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture helpers
# --------------------------------------------------------------------------- #

def _cost(upgrade=(0, 0.0), self_assessment=(0, 0.0), checkers=(0, 0.0)):
    return {
        "upgrade": {"tokens": upgrade[0], "time": upgrade[1]},
        "self_assessment": {"tokens": self_assessment[0], "time": self_assessment[1]},
        "checkers": {"tokens": checkers[0], "time": checkers[1]},
    }


def _rows(*pairs):
    # pairs of (claims_examined, discrepancies)
    return [{"round": 1, "model": "sonnet", "angle": "groundedness",
             "claims_examined": c, "discrepancies": d} for c, d in pairs]


def write_marker(dirpath: Path, name: str, *, source_path, caller, topic,
                 verdict="PASS", cost=None, checker_rows=None):
    dirpath.mkdir(parents=True, exist_ok=True)
    fm = {
        "source_path": str(source_path),
        "caller": caller,
        "topic": topic,
        "verdict": verdict,
        "cost": cost if cost is not None else _cost(),
        "checker_rows": checker_rows if checker_rows is not None else _rows(),
    }
    text = "---\n" + yaml.safe_dump(fm, sort_keys=False) + "---\n\n# audit body\n"
    (dirpath / name).write_text(text, encoding="utf-8")


MANUAL_STATS = (
    "# Stats\n\n"
    "| Date | Type | Plan | TODO | Opus ~k | Sonnet ~k | Tests ± | Commits | ~min |\n"
    "|------|------|------|------|---------|-----------|---------|---------|------|\n"
    "| 2026-06-25 | doing | - | - | 10 | 20 | 0 | 1 | 30 |\n"
)


# --------------------------------------------------------------------------- #
# C1 — discovery + aggregation
# --------------------------------------------------------------------------- #

class TestC1Discovery(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.proj = self.tmp / "proj"
        self.fallback = self.tmp / "fallback"
        self.proj.mkdir()
        self.fallback.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_colocated_and_fallback_in_project_are_found(self):
        # co-located: marker beside an in-project artifact
        write_marker(self.proj / "Docs", "dc-audit__a__1.md",
                     source_path=self.proj / "Docs" / "a.md", caller="/clarification", topic="t1")
        # fallback: marker in the mirror-tree for an in-project read-only source
        write_marker(self.fallback / "some" / "path", "dc-audit__b__2.md",
                     source_path=self.proj / "readonly" / "b.md", caller="/research", topic="t2")
        markers = ds.discover_audit_markers(self.proj, fallback_root=self.fallback)
        callers = sorted(m["caller"] for m in markers)
        self.assertEqual(callers, ["/clarification", "/research"])

    def test_out_of_project_fallback_marker_excluded(self):
        write_marker(self.fallback / "x", "dc-audit__out__1.md",
                     source_path=self.tmp / "other_project" / "z.md", caller="/plan", topic="t")
        markers = ds.discover_audit_markers(self.proj, fallback_root=self.fallback)
        self.assertEqual(markers, [])

    def test_malformed_marker_is_skipped(self):
        (self.proj / "Docs").mkdir(parents=True)
        # no frontmatter
        (self.proj / "Docs" / "dc-audit__bad1__1.md").write_text("not a marker", encoding="utf-8")
        # frontmatter present but no source_path
        (self.proj / "Docs" / "dc-audit__bad2__1.md").write_text(
            "---\ncaller: /x\ntopic: t\n---\n\nbody\n", encoding="utf-8")
        # broken yaml
        (self.proj / "Docs" / "dc-audit__bad3__1.md").write_text(
            "---\n: : : bad\n---\n\nbody\n", encoding="utf-8")
        markers = ds.discover_audit_markers(self.proj, fallback_root=self.fallback)
        self.assertEqual(markers, [])

    def test_aggregation_sums_per_caller_topic(self):
        write_marker(self.proj / "d", "dc-audit__a__1.md",
                     source_path=self.proj / "d" / "a.md", caller="/clarification", topic="t1",
                     verdict="PASS",
                     cost=_cost(upgrade=(10, 1.0), self_assessment=(20, 2.0), checkers=(30, 3.0)),
                     checker_rows=_rows((5, 1), (4, 0)))
        write_marker(self.proj / "d", "dc-audit__a__2.md",
                     source_path=self.proj / "d" / "a2.md", caller="/clarification", topic="t1",
                     verdict="DIRTY",
                     cost=_cost(checkers=(100, 5.0)),
                     checker_rows=_rows((6, 2)))
        write_marker(self.proj / "d", "dc-audit__b__1.md",
                     source_path=self.proj / "d" / "b.md", caller="/research", topic="t2",
                     verdict="ESCALATE")
        agg = ds.aggregate_dc_stats(
            ds.discover_audit_markers(self.proj, fallback_root=self.fallback))
        g1 = agg[("/clarification", "t1")]
        self.assertEqual(g1["runs"], 2)
        self.assertEqual(g1["PASS"], 1)
        self.assertEqual(g1["DIRTY"], 1)
        self.assertEqual(g1["tokens"], 10 + 20 + 30 + 100)   # 160
        self.assertAlmostEqual(g1["time"], 1.0 + 2.0 + 3.0 + 5.0)  # 11.0
        self.assertEqual(g1["claims"], 5 + 4 + 6)            # 15
        self.assertEqual(g1["discrepancies"], 1 + 0 + 2)     # 3
        g2 = agg[("/research", "t2")]
        self.assertEqual(g2["runs"], 1)
        self.assertEqual(g2["ESCALATE"], 1)
        self.assertEqual(g2["tokens"], 0)

    def test_partial_cost_block_does_not_raise(self):
        write_marker(self.proj / "d", "dc-audit__p__1.md",
                     source_path=self.proj / "d" / "p.md", caller="/x", topic="t",
                     cost={"checkers": {"tokens": 7}})  # missing upgrade/self_assessment + time
        agg = ds.aggregate_dc_stats(
            ds.discover_audit_markers(self.proj, fallback_root=self.fallback))
        self.assertEqual(agg[("/x", "t")]["tokens"], 7)


# --------------------------------------------------------------------------- #
# C2 — rendering + write (coexistence, idempotency, first-run, zero markers)
# --------------------------------------------------------------------------- #

class TestC2Render(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.proj = self.tmp / "proj"
        self.fallback = self.tmp / "fallback"
        self.proj.mkdir()
        self.fallback.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_one(self):
        write_marker(self.proj / "d", "dc-audit__a__1.md",
                     source_path=self.proj / "d" / "a.md", caller="/clarification", topic="t1",
                     cost=_cost(checkers=(50, 4.0)), checker_rows=_rows((3, 1)))

    def test_block_inserted_preserves_manual_table(self):
        (self.proj / "Stats.md").write_text(MANUAL_STATS, encoding="utf-8")
        self._seed_one()
        ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        text = (self.proj / "Stats.md").read_text(encoding="utf-8")
        self.assertIn(MANUAL_STATS.strip(), text)          # manual table preserved
        self.assertIn(ds.BLOCK_BEGIN, text)
        self.assertIn(ds.BLOCK_END, text)
        self.assertIn("/clarification", text)
        self.assertIn("| 50 |", text)                       # token sum rendered

    def test_second_write_is_idempotent_in_place(self):
        (self.proj / "Stats.md").write_text(MANUAL_STATS, encoding="utf-8")
        self._seed_one()
        ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        first = (self.proj / "Stats.md").read_text(encoding="utf-8")
        ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        second = (self.proj / "Stats.md").read_text(encoding="utf-8")
        self.assertEqual(first, second)                     # no drift
        self.assertEqual(second.count(ds.BLOCK_BEGIN), 1)   # exactly one block
        self.assertEqual(second.count(ds.BLOCK_END), 1)

    def test_block_updates_when_markers_change(self):
        (self.proj / "Stats.md").write_text(MANUAL_STATS, encoding="utf-8")
        self._seed_one()
        ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        # add a second run in the same group
        write_marker(self.proj / "d", "dc-audit__a__2.md",
                     source_path=self.proj / "d" / "a2.md", caller="/clarification", topic="t1",
                     cost=_cost(checkers=(50, 1.0)))
        ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        text = (self.proj / "Stats.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(ds.BLOCK_BEGIN), 1)
        self.assertIn("| 100 |", text)                      # 50 + 50 tokens, in place

    def test_first_run_creates_stats_file(self):
        self.assertFalse((self.proj / "Stats.md").exists())
        self._seed_one()
        res = ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        self.assertTrue((self.proj / "Stats.md").exists())
        self.assertTrue(res["created"])
        self.assertIn(ds.BLOCK_BEGIN, (self.proj / "Stats.md").read_text(encoding="utf-8"))

    def test_zero_markers_initializes_empty_block_no_error(self):
        res = ds.write_dc_stats(self.proj, fallback_root=self.fallback)
        self.assertEqual(res["markers"], 0)
        text = (self.proj / "Stats.md").read_text(encoding="utf-8")
        self.assertIn(ds.BLOCK_BEGIN, text)
        self.assertIn(ds.BLOCK_END, text)
        self.assertIn("no /double-check runs found", text)

    def test_cli_main_exits_zero_and_writes_block(self):
        self._seed_one()
        # point the CLI's discovery at our tmp fallback by monkeypatching the module const
        old = ds._ENGINE_FALLBACK_ROOT
        ds._ENGINE_FALLBACK_ROOT = self.fallback
        try:
            rc = ds.main([str(self.proj)])
        finally:
            ds._ENGINE_FALLBACK_ROOT = old
        self.assertEqual(rc, 0)
        self.assertIn(ds.BLOCK_BEGIN, (self.proj / "Stats.md").read_text(encoding="utf-8"))

    def test_cli_main_rejects_non_directory(self):
        self.assertEqual(ds.main([str(self.proj / "nope")]), 2)


if __name__ == "__main__":
    unittest.main()

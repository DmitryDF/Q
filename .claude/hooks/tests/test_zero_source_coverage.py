"""Zero-source-coverage gate — a research fact-check that reached its checkers with no
source content must not stand on the checkers' verdict.

Defect this locks (observed live 2026-08-16): the research zone-assembly block wrapped
prefetch + assembly in one broad `except Exception:` that discarded BOTH the assembled
zone and the already-fetched source list. With no zone, the zone block is omitted from the
checker prompt while the research scope instructions still tell the checker the sources are
provided below and that it has no web tool — so the panel verifies URL-grounded claims
against nothing and can answer PASS. With `fetched` discarded, the close-time
source-integrity gate no-ops on its own emptiness check, so dead/stale/hallucinated-URL
detection is switched off by the same handler. The run verdict derives solely from checker
output tokens, so neither condition left a trace.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _factcheck_engine as eng  # noqa: E402


def _passing_checker(dp, idx, model, rnd, prior):
    return "reasoning\nVERDICT: PASS"


class ZeroSourceCoverageUnitTests(unittest.TestCase):
    """The gate in isolation — fires only on the three-part condition."""

    def test_fires_when_sources_cited_but_no_zone(self):
        verdict = {"status": "PASS"}
        out = eng._run_zero_source_coverage_gate(
            "research", ["https://a.example/x"], None, verdict, None)
        self.assertEqual(out["status"], "INCOMPLETE")
        self.assertEqual(out["source_coverage"], "ZERO")
        self.assertEqual(out["source_coverage_cited"], 1)

    def test_does_not_fire_when_no_sources_were_cited(self):
        """A research report citing zero URLs missed nothing — do not downgrade it."""
        verdict = {"status": "PASS"}
        out = eng._run_zero_source_coverage_gate("research", [], None, verdict, None)
        self.assertEqual(out["status"], "PASS")
        self.assertNotIn("source_coverage", out)

    def test_does_not_fire_when_a_zone_was_built(self):
        verdict = {"status": "PASS"}
        out = eng._run_zero_source_coverage_gate(
            "research", ["https://a.example/x"], "ZONE CONTENT", verdict, None)
        self.assertEqual(out["status"], "PASS")
        self.assertNotIn("source_coverage", out)

    def test_does_not_fire_for_non_research_kinds(self):
        for kind in ("plan", "thought", "kl_extraction", "recommendation"):
            with self.subTest(kind=kind):
                verdict = {"status": "PASS"}
                out = eng._run_zero_source_coverage_gate(
                    kind, ["https://a.example/x"], None, verdict, None)
                self.assertEqual(out["status"], "PASS")

    def test_severity_max_never_downgrades_a_more_severe_finding(self):
        """A prior gate's ESCALATE must dominate — this gate raises, never lowers."""
        verdict = {"status": "ESCALATE"}
        out = eng._run_zero_source_coverage_gate(
            "research", ["https://a.example/x"], None, verdict, None)
        self.assertEqual(out["status"], "ESCALATE")
        self.assertEqual(out["source_coverage"], "ZERO")


class ZeroSourceCoverageWiringTests(unittest.TestCase):
    """End-to-end through factcheck_run — the path that actually shipped the defect."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **patches):
        return eng.factcheck_run(
            state_dir=str(self.state_dir),
            draft_path=str(self.draft),
            kind="research",
            session_id="77777777-7777-7777-7777-777777777777",
            debounce_seconds=0,
            models=["sonnet", "sonnet", "sonnet"],
            max_rounds=2,
            _checker_fn=_passing_checker,
            proj="p",
            topic="t",
        )

    def test_zone_assembly_failure_does_not_return_pass(self):
        """The headline defect: blind run + passing checkers used to return PASS."""
        self.draft.write_text(
            "Claim [stated — https://ok.example/a].\n", encoding="utf-8")

        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", "the supporting content")

        def boom(*a, **kw):
            raise RuntimeError("zone assembly blew up")

        with mock.patch.object(eng, "_fetch_one_url", side_effect=fake_fetch), \
                mock.patch.object(eng, "_build_quarantine_zone", side_effect=boom), \
                mock.patch.object(eng, "_run_coverage_axis_gate",
                                  side_effect=lambda rp, cs, v, td, kind, **kw: v), \
                mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            result = self._run()

        self.assertNotEqual(result["status"], "PASS")
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertEqual(result.get("source_coverage"), "ZERO")

    def test_zone_assembly_failure_still_arms_the_source_integrity_gate(self):
        """`fetched` must survive the zone handler, or link-integrity is switched off."""
        self.draft.write_text(
            "Claim [stated — https://ok.example/a].\n", encoding="utf-8")

        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", "the supporting content")

        def boom(*a, **kw):
            raise RuntimeError("zone assembly blew up")

        seen = {}
        orig_gate = eng._run_source_integrity_gate

        def spy_gate(fetched, draft_path, verdict, topic_dir, kind, **kw):
            seen["fetched"] = fetched
            return orig_gate(fetched, draft_path, verdict, topic_dir, kind, **kw)

        with mock.patch.object(eng, "_fetch_one_url", side_effect=fake_fetch), \
                mock.patch.object(eng, "_build_quarantine_zone", side_effect=boom), \
                mock.patch.object(eng, "_run_source_integrity_gate", side_effect=spy_gate), \
                mock.patch.object(eng, "_run_coverage_axis_gate",
                                  side_effect=lambda rp, cs, v, td, kind, **kw: v), \
                mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            self._run()

        self.assertIn("fetched", seen)
        self.assertTrue(
            seen["fetched"],
            "the source-integrity gate was handed an empty list — it will no-op and "
            "dead/hallucinated-URL detection never runs",
        )

    def test_report_citing_no_urls_does_not_fire_THIS_gate(self):
        """THIS gate must stay quiet on a no-URL report — its own property, intact.

        **The assertion about the run's overall status was DELETED, deliberately.**
        It used to read `status == "PASS"`, on the docstring's premise that "no
        citations means nothing was missed". That premise was true when every
        source had a URL and is false since S7 and S8 shipped: a report can now
        draw entirely on internal sources, so "cited no URL" no longer implies
        "cited nothing". S12's internal-citation axis downgrades a report that
        cites nothing OF ANY KIND — which is what this fixture is — so the run is
        now INCOMPLETE by design (design-A22, outcome claim C2).

        What this test owns is the ZERO-SOURCE gate, and that gate's behaviour is
        unchanged: it still declines to fire when no URL was cited. Asserting the
        whole-run status here would make this test a second, weaker assertion
        about a different axis — and it is the sibling test
        `test_a_report_citing_nothing_at_all_folds_to_incomplete`
        (`test_s12_downstream_trust.py`) that owns the new behaviour.
        """
        self.draft.write_text("A claim with no citation at all.\n", encoding="utf-8")

        with mock.patch.object(eng, "_run_coverage_axis_gate",
                               side_effect=lambda rp, cs, v, td, kind, **kw: v), \
                mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            result = self._run()

        self.assertNotIn("source_coverage", result)

    def test_unreadable_report_does_not_crash_the_run(self):
        """A4 guard rail: the report read stays guarded — callers catch only ValueError,
        so an uncaught OSError here would crash every research run."""
        # draft_path points at a directory → read_text raises IsADirectoryError
        bad = self.tmp / "adir_RESEARCH.md"
        bad.mkdir()

        with mock.patch.object(eng, "_run_coverage_axis_gate",
                               side_effect=lambda rp, cs, v, td, kind, **kw: v), \
                mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            result = eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(bad),
                kind="research",
                session_id="88888888-8888-8888-8888-888888888888",
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=_passing_checker,
                proj="p",
                topic="t",
            )
        # No exception escaped; with no citation list the gate stays quiet.
        self.assertNotIn("source_coverage", result)


class TokenizerConstructionTests(unittest.TestCase):
    """A2 (already landed upstream) — construction failure must become OracleUnavailable,
    not escape as ImportError. Locked here because no existing test made the CONSTRUCTOR
    fail: the prior double overrode `count` and was injected as an instance, so the
    constructor never ran."""

    def setUp(self):
        self._saved = eng._token_oracle
        eng._token_oracle = None

    def tearDown(self):
        eng._token_oracle = self._saved

    def test_missing_tokenizer_module_becomes_oracle_unavailable(self):
        class Unimportable:
            def __init__(self, *a, **kw):
                raise ImportError("No module named 'tiktoken'")

        with mock.patch.object(eng, "LocalTokenizerAdapter", Unimportable):
            with self.assertRaises(eng.OracleUnavailable):
                eng._count_zone_tokens("some text")


if __name__ == "__main__":
    unittest.main()

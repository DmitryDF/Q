#!/usr/bin/env python3
"""Regression corpus across all 7 fact-check kinds — criterion d (research).

Plan: ~/.claude/plans/rustling-hatching-karp.md (research-skill writing-rules,
Slice I action A3). Spine: Thoughts/research-skill-writing-rules_THOUGHT.md.

Guarantees criterion d added to KIND_PROMPT_TEMPLATES["research"] does not
silently leak into the six sibling kinds (workflow, plan, thought,
kl_extraction, coverage_check, recommendation) that share factcheck_run,
_invoke_checker_engine, and the prompt-template dict (Rule 12 non-regression).

Strategy: dispatch factcheck_run per kind with an injected mock _checker_fn
(_factcheck_engine.py:790 injection point). PASS-branch artifact mutation is
patched to no-op so engine routing/verdict logic is tested in isolation.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402


FAKE_SESSION = "11111111-1111-1111-1111-111111111111"

PASS_VERDICT = "verdict: PASS\nclaims_checked: 3\n"
DIRTY_VERDICT = (
    "verdict: DISCREPANCY\nclaims_checked: 3\n"
    "discrepancies:\n"
    "  - claim: AI prose contains 'delve'\n"
    "    issue: criterion d antipattern hit\n"
    "    citation: writing-coach-en.md:32\n"
)


class ResearchKindPromptTemplateTests(unittest.TestCase):
    """Criteria a/b/c byte-identical + criterion d present + scope correct."""

    def test_criterion_a_byte_identical(self):
        template = eng.KIND_PROMPT_TEMPLATES["research"][0]
        self.assertIn(
            "(a) Every cited URL/source actually supports the claim made",
            template,
        )

    def test_criterion_b_byte_identical(self):
        template = eng.KIND_PROMPT_TEMPLATES["research"][0]
        self.assertIn(
            "(b) Numbers, dates, and quotes match the cited source",
            template,
        )

    def test_criterion_c_byte_identical(self):
        template = eng.KIND_PROMPT_TEMPLATES["research"][0]
        self.assertIn(
            "(c) Inferred or editorial extensions are labeled",
            template,
        )

    # --- A3 (Slice S1, research-fc-checker-timeout) ---
    # The writing-coach style apparatus (former criterion d) is SPLIT OUT of the
    # factual research checker into its own cheaper `research_style` pass, so the
    # factual checker's per-run reasoning floor drops. These tests lock the split:
    # (d) must be GONE from the factual template and PRESENT in research_style.

    def test_criterion_d_removed_from_factual_checker(self):
        template = eng.KIND_PROMPT_TEMPLATES["research"][0]
        self.assertNotIn("writing-coach", template)
        self.assertNotIn(
            "(d) AI prose contains no antipatterns from the writing-coach tables.",
            template,
        )

    def test_factual_checker_points_to_separate_style_pass(self):
        template = eng.KIND_PROMPT_TEMPLATES["research"][0]
        self.assertIn("NOT your job", template)
        self.assertIn("research_style", template)

    def test_style_pass_owns_antipattern_check(self):
        style = eng.KIND_PROMPT_TEMPLATES["research_style"][0]
        self.assertIn("writing-coach", style)
        self.assertIn("antipattern", style)

    # research-source-adapters S2 rewrote the two assertions below. They previously
    # matched an exact sentence naming `[stated — URL]` and `[paraphrased — URL]`;
    # S2 renders that sentence from CITATION_MARKER_REGISTRY so the vocabulary covers
    # internal sources too, and the fixed wording no longer exists. The PROPERTY each
    # test protects — verbatim `stated` blocks are exempt, `paraphrased` blocks are
    # checked — is unchanged and is now asserted over every marker of that kind rather
    # than over the one web form, so a future source class cannot slip past them.

    def test_style_pass_exempts_only_verbatim_stated(self):
        style = eng.KIND_PROMPT_TEMPLATES["research_style"][0]
        self.assertIn("verbatim quote blocks are exempt", style)
        exempt = eng.render_markers_by_antipattern("exempt")
        self.assertIn(exempt, style)
        # The property the test name states is "exempts ONLY verbatim stated" — i.e.
        # exempt implies stated. The converse (every stated marker is exempt) is true
        # of today's registry but is NOT what this test protects, and asserting it
        # would let a test veto a future source class that needs a non-exempt verbatim
        # marker. Assert the direction that is load-bearing, and no more.
        for marker in eng.CITATION_MARKER_REGISTRY:
            if marker.form in exempt:
                self.assertEqual(
                    marker.kind, "stated",
                    f"{marker.form} is exempt but is not a verbatim quote",
                )
        self.assertIn("[stated — URL]", exempt)

    def test_style_pass_checks_paraphrased_blocks(self):
        style = eng.KIND_PROMPT_TEMPLATES["research_style"][0]
        self.assertIn("IS checked", style)
        checked = eng.render_markers_by_antipattern("checked")
        self.assertIn(checked, style)
        for marker in eng.CITATION_MARKER_REGISTRY:
            if marker.kind == "paraphrased":
                self.assertIn(marker.form, checked, f"{marker.form} must be checked")

    def test_style_pass_warns_on_missing_language_tag(self):
        style = eng.KIND_PROMPT_TEMPLATES["research_style"][0]
        self.assertIn("language tag", style)

    def test_sibling_kinds_unchanged_by_d_addition(self):
        for kind in ("workflow", "plan", "thought", "coverage_check", "recommendation"):
            template = eng.KIND_PROMPT_TEMPLATES[kind][0]
            self.assertNotIn(
                "writing-coach",
                template,
                f"kind '{kind}' must not reference writing-coach (leaked from research d).",
            )
            self.assertNotIn(
                "`[stated — URL]`",
                template,
                f"kind '{kind}' must not reference [stated — URL] (leaked from research d).",
            )


class CrossKindRegressionTests(unittest.TestCase):
    """All 7 kinds dispatch cleanly through factcheck_run with criterion d
    present in the research template — Rule 12 non-regression."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft_path = self.tmp / "draft.md"
        # The fixture carries one citation (S12). This class tests that all seven
        # kinds DISPATCH cleanly; since design-A22 a research report citing nothing
        # of any kind is downgraded to INCOMPLETE by the internal-citation axis, so
        # a citation-free fixture would make a dispatch test fail on a source
        # finding. An internal citation is used rather than a URL: a URL would be
        # prefetched and an unreachable one escalates, trading one unrelated axis
        # for another. Non-research kinds ignore it entirely.
        self.draft_path.write_text(
            "# minimal fixture\n\nA claim. [stated — local-file:Docs/x.md:1]\n",
            encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, kind, checker_fn):
        with mock.patch.object(eng, "_append_validated_via_frontmatter"), \
                mock.patch.object(eng, "_append_converged_marker_to_plan_body"), \
                mock.patch.object(eng, "_write_coverage_check_marker"), \
                mock.patch.object(eng, "_run_coverage_axis_gate",
                                  side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict), \
                mock.patch.object(eng, "_log_factcheck_run"):
            return eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft_path),
                kind=kind,
                session_id=FAKE_SESSION,
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=checker_fn,
                proj="testproj",
                topic=f"testtopic_{kind}",
            )

    def test_workflow_pass(self):
        self.assertEqual(
            self._run("workflow", lambda *a, **k: PASS_VERDICT)["status"], "PASS"
        )

    def test_plan_pass(self):
        self.assertEqual(
            self._run("plan", lambda *a, **k: PASS_VERDICT)["status"], "PASS"
        )

    def test_thought_pass(self):
        self.assertEqual(
            self._run("thought", lambda *a, **k: PASS_VERDICT)["status"], "PASS"
        )

    def test_coverage_check_pass(self):
        self.assertEqual(
            self._run("coverage_check", lambda *a, **k: PASS_VERDICT)["status"], "PASS"
        )

    def test_recommendation_pass(self):
        self.assertEqual(
            self._run("recommendation", lambda *a, **k: PASS_VERDICT)["status"], "PASS"
        )

    def test_kl_extraction_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self._run("kl_extraction", lambda *a, **k: PASS_VERDICT)
        self.assertIn(
            "kl_extraction not supported via factcheck_run", str(ctx.exception)
        )

    def test_research_pass_on_clean_fixture(self):
        self.assertEqual(
            self._run("research", lambda *a, **k: PASS_VERDICT)["status"], "PASS"
        )

    def test_research_escalates_on_antipattern_dirty(self):
        result = self._run("research", lambda *a, **k: DIRTY_VERDICT)
        self.assertEqual(result["status"], "ESCALATE")
        self.assertEqual(result["rounds"], 2)

    def test_sibling_kinds_dirty_consistent(self):
        for kind in ("workflow", "plan", "thought", "coverage_check", "recommendation"):
            result = self._run(kind, lambda *a, **k: DIRTY_VERDICT)
            self.assertEqual(
                result["status"], "ESCALATE",
                f"sibling kind '{kind}' status diverged from baseline",
            )
            self.assertEqual(
                result["rounds"], 2,
                f"sibling kind '{kind}' rounds diverged from baseline",
            )


if __name__ == "__main__":
    unittest.main()

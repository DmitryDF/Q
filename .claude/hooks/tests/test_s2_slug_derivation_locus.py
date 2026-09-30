#!/usr/bin/env python3
"""Tests for S2 — one derivation locus for the topic-slug half of identity.

The defect: the topic slug was derived independently under two code grammars
plus a third stated in `/work-start` prose, and nothing reconciled them. They
agreed on the standard `<slug>-<ts>_TYPE.md` shape and disagreed on every
scope-keyed `<slug>-<ts>_<SCOPE>_TYPE.md` one, so the same artifact could be
filed under two keys — which is how a second tracking record got minted for
work that already had one.

  A4  `canonical_topic_for_spine` (creation path) and `auto_register_topic`
      (ship boundary) resolve to the SAME slug for every artifact shape, via one
      shared locus. Canonical answer for a scope-keyed name is the BASE slug.
  A5  `/work-start` no longer states the grammar in prose; it calls the
      `canonical-topic` CLI verb, whose value it passes to `create-topic`.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_s2_slug_derivation_locus.py
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg   # noqa: E402

PPG_PY = HOOKS / "pre_plan_gates.py"
SKILL_MD = HOOKS.parent / "skills" / "work-start" / "SKILL.md"
THOUGHTS = Path("~/repos/Projects/Thoughts")

TODO_SKELETON = "# TODO\n\n## Now\n\n_(nothing yet)_\n\n## Done\n"


def ship_boundary_slug(path):
    """The slug the ship boundary means, spelled out here rather than by calling
    the function under test.

    `_timestamped_slug_from_spine` is the (unchanged) producer of the
    `<slug>-<ts>` TODO slug tag; the base slug is that minus the timestamp, and
    `auto_register_topic` used to open-code exactly this line.

    What this DOES establish: the two grammars are not re-split. Reverting
    `_slug_from_spine_path` to its pre-S2 body fails the agreement assertions,
    which is the regression this helper was written to catch.

    What it does NOT establish — stated because an earlier version of this
    docstring claimed the helper "keeps the agreement assertions from being
    tautologies" full stop, which is only half true. Production
    `_slug_from_spine_path` is now byte-identical to this expression, so if
    `_timestamped_slug_from_spine` itself computed a WRONG slug both sides would
    move to the same wrong answer together and every agreement assertion would
    still report zero divergence. This helper is a re-split regression guard; it
    is not evidence that the resolved values are correct. That evidence comes
    only from the hand-written literals in `SAMPLED_REAL_CORPUS_SLUGS` below,
    which neither production function computed.
    """
    return re.sub(r"-\d{14}$", "", ppg._timestamped_slug_from_spine(path))


def old_grammar_a(path):
    """Verbatim copy of the pre-S2 `_slug_from_spine_path` body, kept ONLY as
    the characterization reference that proves the divergence was real."""
    stem = Path(str(path)).name
    if stem.endswith(".md"):
        stem = stem[:-len(".md")]
    for suf in ("_THOUGHT", "_PLAN", "_DESIGN", "_RESEARCH", "_CLAIMS"):
        if stem.endswith(suf):
            stem = stem[:-len(suf)]
            break
    return re.sub(r"-\d{14}$", "", stem)


# (filename, expected base slug, expected canonical_topic_for_spine)
# One row per filename shape that actually occurs on disk. `None` in the third
# column means the creation path REFUSES rather than deriving.
SHAPES = [
    # -- standard, timestamped (both grammars always agreed here) -------------
    ("plan-gates-20260806223312_THOUGHT.md", "plan-gates", "plan-gates"),
    ("plan-gates-20260806223312_PLAN.md", "plan-gates", "plan-gates"),
    ("plan-gates-20260806223312_DESIGN.md", "plan-gates", "plan-gates"),
    ("plan-gates-20260806223312_RESEARCH.md", "plan-gates", "plan-gates"),
    ("plan-gates-20260806223312_CLAIMS.md", "plan-gates", "plan-gates"),
    # -- scope-keyed, timestamped: THE divergent class -----------------------
    ("git-working-model-20260714130221_S1_PLAN.md",
     "git-working-model", "git-working-model"),
    ("clarification-v2-20260801100833_SEAL_PLAN.md",
     "clarification-v2", "clarification-v2"),
    ("thing-20260806223312_H_v2_B1_PLAN.md", "thing", "thing"),
    ("assessment-engine-20260714194353_FRAMING_RESEARCH.md",
     "assessment-engine", "assessment-engine"),
    # -- legacy / advisory shapes carrying a timestamp -----------------------
    ("thing-20260806223312_THOUGHT_check.md", "thing", "thing"),
    ("thing-20260806223312_DOUBLECHECK_ab12cd34.md", "thing", "thing"),
    ("thing-20260806223312.md", "thing", "thing"),
    # -- untimestamped: unchanged by the re-key, and deliberately so ---------
    ("legacy-topic_THOUGHT.md", "legacy-topic", "legacy-topic"),
    ("legacy-topic_PLAN.md", "legacy-topic", "legacy-topic"),
    ("legacy-topic.md", "legacy-topic", "legacy-topic"),
    # No timestamp => no anchor separating work name from scope key. Keeping
    # the scope key is the honest answer; inventing a split would be a guess.
    ("legacy-topic_K_PLAN.md", "legacy-topic_K", "legacy-topic_K"),
    # -- shapes the creation path refuses (base slug still computes) ---------
    ("foo__bar_THOUGHT.md", "foo__bar", None),
    ("-20260806223312_PLAN.md", "", None),
]


# (real filename present under `Projects/Thoughts/`, expected base slug, the
# shape it stands for). The expected values are typed BY HAND and were computed
# by NEITHER production function — that independence is the whole point. The
# corpus test's agreement assertion cannot tell "both right" from "both wrong
# together" (see `ship_boundary_slug`), so these literals are what carries the
# correctness claim over the real corpus. Sampled and verified present on disk
# 2026-08-30; a sample that vanishes fails loudly rather than silently skipping.
SAMPLED_REAL_CORPUS_SLUGS = [
    ("git-working-model-20260714130221_THOUGHT.md",
     "git-working-model", "standard <slug>-<ts>_TYPE"),
    ("assessment-engine-20260727202257_PLAN.md",
     "assessment-engine", "standard <slug>-<ts>_TYPE"),
    ("git-working-model-20260714130221_S1_PLAN.md",
     "git-working-model", "scope-keyed <slug>-<ts>_<SCOPE>_TYPE"),
    ("audit-session-app-runs_THOUGHT.md",
     "audit-session-app-runs", "untimestamped <slug>_TYPE"),
    # Untimestamped AND scope-keyed: the scope key is deliberately KEPT, because
    # with no timestamp there is no anchor separating work name from scope key.
    ("automate-plan-execution_S1_PLAN.md",
     "automate-plan-execution_S1", "untimestamped scope-keyed"),
    ("git-working-model-20260714130221-20260719171000_REVIEW_REPORT.md",
     "git-working-model", "double-timestamp advisory"),
    ("session-topic-identity-coherence-20260805230130-20260805234148"
     "_ASSESSMENT_3cd74b82-ef35a7a0.md",
     "session-topic-identity-coherence", "double-timestamp advisory + sid"),
]


class A4OneDerivationLocusTests(unittest.TestCase):
    """A4 — both producers agree on every artifact shape."""

    def test_both_producers_agree_on_every_artifact_shape(self):
        # Table-driven. Each row pins the EXPECTED value as well as the
        # agreement, so a change that moves both producers together in the
        # wrong direction still fails.
        for name, expected, _canon in SHAPES:
            with self.subTest(name=name):
                self.assertEqual(
                    ppg._slug_from_spine_path(f"/x/Thoughts/{name}"), expected,
                    "creation-path locus disagrees with the expected base slug")
                self.assertEqual(
                    ship_boundary_slug(f"/x/Thoughts/{name}"), expected,
                    "ship-boundary derivation disagrees with the expected "
                    "base slug")

    def test_canonical_topic_for_spine_matches_the_locus_or_refuses(self):
        for name, expected, canon in SHAPES:
            with self.subTest(name=name):
                got = ppg.canonical_topic_for_spine(f"/x/Thoughts/{name}")
                self.assertEqual(got, canon)
                if got is not None:
                    self.assertEqual(got, expected)

    def test_scope_keyed_timestamped_resolves_to_the_base_slug(self):
        # The recorded operator decision, named: a scope key labels a slice of
        # ONE work, not a second work. Pre-S2 this returned
        # `git-working-model-20260714130221_S1`.
        p = "/x/Thoughts/git-working-model-20260714130221_S1_PLAN.md"
        self.assertEqual(ppg._slug_from_spine_path(p), "git-working-model")
        self.assertEqual(old_grammar_a(p),
                         "git-working-model-20260714130221_S1")

    def test_double_timestamped_names_collapse_to_the_base_slug(self):
        # Found by an independent checker, NOT by the shape table above: a name
        # carrying two timestamps also changed answer, and S2 shipped without
        # disclosing it. Sixteen such artifacts exist under `Projects/Thoughts/`
        # today (fifteen `_REVIEW_REPORT`, one `_ASSESSMENT_<sid>`); all are
        # advisory-bucket TYPEs rather than spines, so no live key moved.
        # Expected values are hardcoded — the base slug names the work, and a
        # second timestamp stamps one report of it, not a second work.
        plain = "/x/Thoughts/foo-20260101000000-20260102000000_PLAN.md"
        self.assertEqual(ppg._slug_from_spine_path(plain), "foo")
        self.assertEqual(old_grammar_a(plain), "foo-20260101000000")

        scoped = "/x/Thoughts/foo-20260101000000-20260102000000_S1_PLAN.md"
        self.assertEqual(ppg._slug_from_spine_path(scoped), "foo")
        self.assertEqual(old_grammar_a(scoped),
                         "foo-20260101000000-20260102000000_S1")

    def test_untimestamped_shapes_are_unchanged_by_the_rekey(self):
        # Non-regression: the re-key must touch ONLY names carrying a
        # timestamp. Anything else keeps its pre-S2 answer byte-for-byte.
        for name, _expected, _canon in SHAPES:
            if re.search(r"-\d{14}", name):
                continue
            with self.subTest(name=name):
                p = f"/x/Thoughts/{name}"
                self.assertEqual(ppg._slug_from_spine_path(p),
                                 old_grammar_a(p))

    def test_a_directory_path_is_unchanged_and_still_refused(self):
        # The defect signature this locus exists to exclude: a directory
        # basename reaching the work-name position.
        self.assertEqual(
            ppg._slug_from_spine_path("~/repos/Projects"),
            "Projects")
        self.assertIsNone(
            ppg.canonical_topic_for_spine("~/repos/Projects"))


class A4RealCorpusDivergenceTests(unittest.TestCase):
    """A4 — the divergence measurement, re-run as a test."""

    @unittest.skipUnless(THOUGHTS.is_dir(), "real Thoughts corpus not present")
    def test_sampled_real_filenames_resolve_to_hand_written_slugs(self):
        # The correctness half of the corpus claim. Its sibling below proves
        # only that the two derivations AGREE, and both now read one grammar —
        # so a wrong grammar would move both together and still report zero
        # divergence. These literals were written by hand, so they hold whatever
        # the production functions say.
        for name, expected, shape in SAMPLED_REAL_CORPUS_SLUGS:
            with self.subTest(name=name, shape=shape):
                self.assertTrue(
                    (THOUGHTS / name).is_file(),
                    f"sample gone from disk — re-pick a real {shape} file")
                self.assertEqual(
                    ppg._slug_from_spine_path(THOUGHTS / name), expected)

    @unittest.skipUnless(THOUGHTS.is_dir(), "real Thoughts corpus not present")
    def test_zero_divergence_over_the_real_thoughts_corpus(self):
        # A re-split regression guard over the whole corpus, and only that —
        # the correctness evidence is the sampled-literals test above.
        files = sorted(THOUGHTS.glob("*.md"))
        self.assertGreater(len(files), 0)

        # Non-vacuity: the divergence this slice closes must have been real on
        # this corpus, or a green result here proves nothing.
        was_divergent = [f.name for f in files
                         if old_grammar_a(f) != ship_boundary_slug(f)]
        self.assertGreater(
            len(was_divergent), 0,
            "characterization reference no longer diverges — this test would "
            "pass vacuously")

        now_divergent = [
            {"file": f.name, "locus": ppg._slug_from_spine_path(f),
             "ship": ship_boundary_slug(f)}
            for f in files
            if ppg._slug_from_spine_path(f) != ship_boundary_slug(f)
        ]
        self.assertEqual(
            now_divergent, [],
            f"{len(now_divergent)} artifact(s) still derive two different "
            f"slugs: {now_divergent[:5]}")


class A4ClassificationCallerTests(unittest.TestCase):
    """A4 — the re-key AT the two classification callers, not only at the
    function. `_classify_records` and `_reconcilable_twins_for_slug` both read
    `_slug_from_spine_path`, so a scope-keyed spine changes which key counts as
    correct-convention for them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s2_classify_"))
        self.root = self.tmp / "Projects"
        (self.root / "Thoughts").mkdir(parents=True)
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.spine = (self.root / "Thoughts"
                      / "widget-20260806223312_S3_PLAN.md")
        self.spine.write_text("# Plan\n")
        self._patches = [
            mock.patch.object(ppg, "PROJECTS_ROOT", self.root),
            mock.patch.object(ppg, "TOPIC_STATE_DIR", self.state_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _record(self, filename_topic):
        path = self.state_dir / f"{filename_topic}__Root.json"
        path.write_text(json.dumps({"thought_file_path": str(self.spine),
                                    "project_root": str(self.root)}))
        return path

    def test_base_slug_key_is_correct_convention_after_the_rekey(self):
        self._record("widget")
        groups, inverted, unresolvable = ppg._classify_records()
        self.assertEqual(unresolvable, [])
        self.assertEqual(inverted, [])
        self.assertIn("widget", groups)
        self.assertEqual(len(ppg._reconcilable_twins_for_slug("widget")), 1)

    def test_old_scope_keyed_key_now_classifies_as_inverted(self):
        # Honest characterization of the re-key's cost. A record filed under
        # the OLD derivation is now reported as inverted (surfaced, never
        # auto-touched) rather than correct-convention. Measured 2026-08-30
        # across the 119 live records: 0 records are in this state.
        old_key = old_grammar_a(self.spine)
        self.assertEqual(old_key, "widget-20260806223312_S3")
        self._record(old_key)
        groups, inverted, _unres = ppg._classify_records()
        self.assertEqual(groups, {})
        self.assertEqual(len(inverted), 1)
        self.assertEqual(inverted[0]["spine_slug"], "widget")
        self.assertEqual(ppg._reconcilable_twins_for_slug(old_key), [])


class A5NoProseDerivationTests(unittest.TestCase):
    """A5 — `/work-start` calls the code instead of restating the grammar."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s2_autoreg_"))
        self.root = self.tmp / "Projects"
        (self.root / "Thoughts").mkdir(parents=True)
        (self.root / "TODO.md").write_text(TODO_SKELETON)
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self._patches = [
            mock.patch.object(ppg, "PROJECTS_ROOT", self.root),
            mock.patch.object(ppg, "TOPIC_STATE_DIR", self.state_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cli_canonical_topic(self, spine, expect=0):
        res = subprocess.run(
            [sys.executable, str(PPG_PY), "canonical-topic", str(spine)],
            capture_output=True, text=True)
        self.assertEqual(
            res.returncode, expect,
            f"exit={res.returncode}\nstdout={res.stdout}\nstderr={res.stderr}")
        return res

    def test_worktree_origin_key_equals_the_auto_register_key(self):
        # THE A5 gate. The worktree-origin arm of `/work-start` passes the
        # value `canonical-topic` prints to `create-topic` as TOPIC_SLUG; the
        # ship boundary binds `auto_register_topic`'s own slug. On a
        # SCOPE-KEYED plan — the shape the two paths used to disagree on — the
        # two must be the same key. One side runs in-process, the other as a
        # separate CLI process, so this is a genuine cross-path assertion.
        spine = (self.root / "Thoughts"
                 / "widget-thing-20260806223312_S3_PLAN.md")
        spine.write_text("# Plan\n")

        report = ppg.auto_register_topic("sid-s2", spine)
        self.assertEqual(report["status"], "registered", msg=json.dumps(report))
        auto_register_key = report["topic"].rsplit("__", 1)[0]

        work_start_key = self._cli_canonical_topic(spine).stdout.strip()

        self.assertEqual(work_start_key, auto_register_key)
        self.assertEqual(work_start_key, "widget-thing")
        # And the record really landed under that key.
        self.assertTrue((self.state_dir
                         / f"{auto_register_key}__Root.json").exists())

    def test_the_two_paths_also_agree_on_the_standard_shape(self):
        # Non-regression: the shape they already agreed on must keep agreeing.
        spine = self.root / "Thoughts" / "widget-thing-20260806223312_PLAN.md"
        spine.write_text("# Plan\n")
        report = ppg.auto_register_topic("sid-s2b", spine)
        self.assertEqual(report["topic"].rsplit("__", 1)[0], "widget-thing")
        self.assertEqual(
            self._cli_canonical_topic(spine).stdout.strip(), "widget-thing")

    def test_cli_refuses_rather_than_substituting(self):
        res = self._cli_canonical_topic("/x/Thoughts/not-markdown", expect=1)
        self.assertEqual(res.stdout.strip(), "")
        self.assertIn("cannot derive a topic slug", res.stderr)

    def test_plain_mode_slug_is_unaffected(self):
        # Guard rail: a plain topic's key is `plain-<sid8>` and is legitimately
        # unrelated to any artifact. It is not artifact-derived, and an
        # explicit slug still outranks the artifact derivation (the
        # ORDER IS LOAD-BEARING contract in `create_topic`) — so a plain
        # session that names a spine keeps its own key.
        sid = "b47ffa87deadbeef"
        plain = f"plain-{sid[:8]}"
        spine = self.root / "Thoughts" / "widget-20260806223312_S3_PLAN.md"
        spine.write_text("# Plan\n")

        self.assertNotEqual(ppg.canonical_topic_for_spine(spine), plain)
        ppg.create_topic(sid, "Root", plain, intake_source="plain",
                         thought_file_path=str(spine))
        keys = sorted(p.name for p in self.state_dir.glob("*__*.json")
                      if p.name != "_active.json")
        self.assertEqual(keys, [f"{plain}__Root.json"])

    def test_skill_no_longer_states_the_slug_grammar_in_prose(self):
        self.assertTrue(SKILL_MD.is_file(), SKILL_MD)
        text = SKILL_MD.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertNotIn("Derive `topic_slug` from the spine filename", flat)
        self.assertIn("Do not derive `topic_slug` yourself", flat)
        self.assertIn("pre_plan_gates.py canonical-topic", flat)


if __name__ == "__main__":
    unittest.main(verbosity=2)

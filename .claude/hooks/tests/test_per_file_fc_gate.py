#!/usr/bin/env python3
"""S1 walking skeleton — per-file fact-check gate (Group I item 2).

Plan: Thoughts/research-fc-backlog-rootcause-20260728005636_I_PLAN.md, slice S1.
Repro-first: every test here FAILS against the pre-S1 engine and passes after.

Outcome Claims exercised:
  C1  each research file reaches its own verdict from a check of that file alone
  C2  a file's verdict persists and is still readable afterwards
  C3  a file's recorded verdict is its own, not a sibling's
  C6  no file is treated as checked because a different file was checked
  C7  a terminal no-consensus is shown against the file that earned it
  C9  each file is checked against its OWN attempt budget

Rails pinned here (S1 guard rails, each would otherwise be a promise with no test):
  single-locus    no inline slug derivation remains at any of the four sites
  disjointness    no key the helper can emit yields a name matching legacy `R*.md`
  scope-boundary  no carrier instance can reach the audit-marker site (S1->S2 window)
  fallback-guard  BOTH halves — the `^R\\d` refusal AND lowercase normalization
  schema-stable   `schema_version: 3` frontmatter byte-unchanged; `rounds:` sequence-local

Run: /usr/bin/python3 -m pytest tests/test_per_file_fc_gate.py -q
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _factcheck_engine as eng  # noqa: E402


def _mock_resolver(proj, topic):
    state = {"topic_slug": proj, "project_slug": topic}
    return lambda _sid: (proj, topic, state)


class _CountingChecker:
    """Injectable _checker_fn recording (draft, round_num) per dispatch."""

    def __init__(self, verdict="PASS"):
        self.calls = []
        self.verdict = verdict

    def __call__(self, draft_path, idx, model, round_num, prior):
        self.calls.append((os.path.basename(str(draft_path)), round_num))
        return self.verdict

    @property
    def dispatches(self):
        return len(self.calls)

    def rounds_for(self, basename):
        return [r for (b, r) in self.calls if b == basename]


class _PerFileBase(unittest.TestCase):
    PROJ = "TestPFG"
    TOPIC = "per-file"
    SID = "test-pfg"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "validation"
        self.resolver = _mock_resolver(self.PROJ, self.TOPIC)
        self.rp_dir = Path(self.tmpdir) / "rp"
        self.rp_dir.mkdir()
        self._prev_rp = os.environ.get("RP_STATE_DIR")
        os.environ["RP_STATE_DIR"] = str(self.rp_dir)

        self.file_a = self._draft("alpha-20260101010101_RESEARCH.md")
        self.file_b = self._draft("bravo-20260101010101_RESEARCH.md")

    def tearDown(self):
        if self._prev_rp is None:
            os.environ.pop("RP_STATE_DIR", None)
        else:
            os.environ["RP_STATE_DIR"] = self._prev_rp
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _draft(self, name):
        p = Path(self.tmpdir) / name
        p.write_text("---\ntitle: t\n---\n# Body — no cited URLs\n")
        return p

    def _research_base(self):
        return self.state_dir / self.PROJ / self.TOPIC / "research"

    def _run(self, checker, draft, kind="research", cycle_id="default",
             force=False, max_rounds=3, debounce_seconds=0):
        return eng.factcheck_run(
            self.state_dir, str(draft), kind, self.SID,
            debounce_seconds=debounce_seconds,
            _checker_fn=checker,
            _proj_topic_resolver=self.resolver,
            max_rounds=max_rounds,
            force=force,
            cycle_id=cycle_id,
        )


class DivisionOfLabourTests(_PerFileBase):
    """WHICH mechanism closes which collision class — stated, not implied.

    This matters because it is easy to be fooled by the sibling tests below.
    They assert that two different files get different KEYS, and they pass. But
    the key ends in `--x<sha256(canon)[:8]>`, and `canon` is the raw case-folded
    basename — so ANY two textually different filenames get different keys
    whether or not the readable derivation is correct. Those tests therefore
    pin the safety property (distinct files, distinct marker groups) while
    proving nothing about the scope/timestamp/fallback logic that produced the
    prefix. A reviewer caught exactly that.

    Measured division of labour:

      class 2 (scope-keyed siblings)  -> the PREFIX distinguishes them
      class 3 (grammar vs fallback)   -> only the DIGEST does
      class 4a (scope spellings)      -> only the DIGEST does
      class 4b (two timestamps)       -> only the DIGEST does

    So the digest is the correctness carrier for three of the four, and this
    class pins that fact directly rather than leaving it to be inferred from
    tests that would pass either way.
    """

    def test_the_key_ends_in_a_digest_of_the_case_folded_basename(self):
        """The actual carrier. If this goes, three collision classes reopen."""
        import hashlib
        name = "alpha-20260101010101_A_RESEARCH.md"
        key = eng._research_file_key(name)
        canon = "alpha-20260101010101_a_RESEARCH.md".replace("_a_", "_a_")
        # Recompute canon the way the helper does: body lower-cased, TYPE upper.
        stem, ext = os.path.splitext(name)
        m = re.match(r"^(?P<body>.*?)(?P<type>_[A-Za-z][A-Za-z0-9_]*)$", stem)
        canon = (m.group("body").lower() + m.group("type").upper() + ext
                 if m else stem.lower() + ext)
        expected = hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]
        self.assertTrue(key.endswith(f"--x{expected}"),
                        f"the key must end in the canon digest; got {key!r}")

    def test_the_digest_is_unconditional_not_fallback_only(self):
        """It was fallback-only once, which left the grammar path collidable."""
        for name in ("alpha-20260101010101_RESEARCH.md",      # grammar path
                     "my.topic_A_RESEARCH.md"):               # fallback path
            self.assertIn("--x", eng._research_file_key(name),
                          f"{name!r} must carry a digest on either path")

    def test_the_prefix_alone_does_NOT_separate_three_of_the_classes(self):
        """Documents why the digest is load-bearing rather than belt-and-braces.

        If a later change made the readable derivation lossless, this test would
        need revisiting — but it would be revisiting a genuine improvement, not
        papering over a regression.
        """
        for a, b in (
            ("my-topic-a_RESEARCH.md", "my.topic_A_RESEARCH.md"),
            ("assessment-engine-20260709000856_H_v2_B1_RESEARCH.md",
             "assessment-engine-20260709000856_H-v2-B1_RESEARCH.md"),
            ("assessment-engine-20260709000856_RESEARCH.md",
             "assessment-engine-20260727202257_RESEARCH.md"),
        ):
            self.assertEqual(
                eng._research_display_slug(a), eng._research_display_slug(b),
                f"{a!r} / {b!r}: the readable prefix is lossy here by design; "
                "if this now differs, the digest is no longer the sole carrier "
                "and the sibling tests' docstrings need updating")
            self.assertNotEqual(eng._research_file_key(a),
                                eng._research_file_key(b),
                                "but the KEY must still separate them")

    def test_the_prefix_DOES_separate_scope_keyed_siblings(self):
        """Class 2 is closed by the derivation itself, not only the digest —
        which is what keeps scope visible in the operator-facing name."""
        bare = eng._research_display_slug("mytopic-20260101010101_RESEARCH.md")
        scoped = eng._research_display_slug("mytopic-20260101010101_A_RESEARCH.md")
        self.assertNotEqual(bare, scoped)
        self.assertEqual(scoped, "mytopic--a")


class KeyDerivationTests(_PerFileBase):
    """The single derivation locus + both halves of the fallback guard.

    NOTE on what the collision tests in this class prove. They assert distinct
    KEYS for distinct files, which is the safety property. For classes 3, 4a and
    4b that distinctness comes from the digest, NOT from the prefix derivation —
    see `DivisionOfLabourTests`. Read them as pinning the outcome, not the
    mechanism.
    """

    def test_key_helper_exists_and_is_the_single_locus(self):
        self.assertTrue(hasattr(eng, "_research_file_key"),
                        "S1 introduces one key-derivation helper")
        k = eng._research_file_key("alpha-20260101010101_RESEARCH.md")
        self.assertTrue(k, "a real research filename yields a non-empty key")

    def test_an_uppercase_name_still_takes_the_GRAMMAR_path(self):
        """This replaces a tautological assertion.

        The old test asserted `key == key.lower()`, which the final `_norm` step
        makes unconditionally true regardless of whether the case-fold-before-
        classify logic exists at all — it could not detect a regression in the
        very rail it was named after, as its sibling's docstring already
        conceded.

        The meaningful property: case-folding must happen BEFORE classification,
        so an uppercase name is still RECOGNIZED by the grammar. If the fold were
        removed, `ALPHA-<ts>_RESEARCH.md` would be rejected, fall through to the
        fallback, and pick up the `--x<digest>` suffix that marks a fallback key.
        """
        key = eng._research_file_key("ALPHA-20260101010101_RESEARCH.md")
        self.assertEqual(key, key.lower(), "keys are lowercase")
        # The digest is now unconditional, so "carries a digest" no longer marks
        # the fallback path. What still distinguishes the paths is the READABLE
        # PREFIX: the grammar strips the 14-digit timestamp into its own group,
        # the fallback keeps it in the stem. If the case-fold no longer preceded
        # classification, this uppercase name would be rejected and its prefix
        # would still carry the timestamp.
        prefix = key.split("--x")[0]
        self.assertEqual(prefix, "alpha",
                         "an uppercase name must still resolve through the "
                         "grammar, which strips the timestamp from the prefix")
        self.assertNotIn("20260101010101", key,
                         "a timestamp in the prefix means the grammar rejected "
                         "the name and the fallback handled it")
        self.assertEqual(
            key, eng._research_file_key("alpha-20260101010101_RESEARCH.md"),
            "and it resolves to the same key as its lowercase spelling")

    def test_scope_keyed_siblings_yield_DISTINCT_keys(self):
        """Bucket 2 scope-keyed research files are DIFFERENT files.

        `bookkeeping-model.md` §4 Bucket 2 documents
        `<slug>[-<ts>]_<SCOPE>_<TYPE>.md`, and §7 says the same bare+scope-keyed
        shape applies to Research. But `classify().slug` deliberately excludes
        the SCOPE segment — it lands in `.scope` — so keying on the slug alone
        collapses every scope-keyed sibling onto the bare file's key.

        That is not a cosmetic collision. One key means one marker group, one
        round counter and one cooldown, so scope item A's ESCALATE is masked by
        the bare file's later PASS and the gate exits 0 — the precise harm this
        whole change exists to remove, reproduced on a documented filename
        shape. This topic's own artifacts use that shape.
        """
        names = [
            "mytopic-20260101010101_RESEARCH.md",
            "mytopic-20260101010101_A_RESEARCH.md",
            "mytopic-20260101010101_B_RESEARCH.md",
            "mytopic-20260101010101_H_v2_B1_RESEARCH.md",
        ]
        keys = [eng._research_file_key(n) for n in names]
        self.assertEqual(
            len(set(keys)), len(names),
            "each scope-keyed sibling is its own file and needs its own key; "
            f"got {dict(zip(names, keys))}")

    def test_the_scope_separator_is_not_itself_ambiguous(self):
        """The fix for the scope collision must not re-create it.

        Slugs are lowercase-kebab and may contain '-', and the scope is
        lowercased into the key, so joining them with a single '-' makes
        `slug="mytopic-a", scope=None` indistinguishable from
        `slug="mytopic", scope="A"` — two genuinely different files back on one
        marker group. The separator has to be one that key normalization can
        never produce inside a part.
        """
        for bare, scoped in (
            ("mytopic-a-20260101010101_RESEARCH.md",
             "mytopic-20260101010101_A_RESEARCH.md"),
            ("foo-bar-20260101010101_RESEARCH.md",
             "foo-20260101010101_BAR_RESEARCH.md"),
        ):
            self.assertNotEqual(
                eng._research_file_key(bare), eng._research_file_key(scoped),
                f"{bare!r} and {scoped!r} are different files and must not "
                "share a marker key")

    def test_a_fallback_key_can_never_equal_a_grammar_key(self):
        """The third collision class in this helper, and the subtlest.

        The fallback fires whenever the bookkeeping grammar rejects a name — for
        instance because the stem contains a '.', which its slug class excludes.
        On that path any scope is folded into the plain slug string and never
        gets the `--` join, and `_norm` collapses '.' and '_' each to a single
        '-'. So a grammar-VALID bare file and a grammar-REJECTED scope-keyed file
        can normalize onto one key:

            my-topic-a_RESEARCH.md  (grammar, slug=my-topic-a) -> my-topic-a
            my.topic_A_RESEARCH.md  (fallback)                 -> my-topic-a

        Two different files, one marker group, one masking the other.
        """
        for grammar_name, fallback_name in (
            ("my-topic-a_RESEARCH.md", "my.topic_A_RESEARCH.md"),
            ("a-b_RESEARCH.md", "a.b_RESEARCH.md"),
        ):
            self.assertNotEqual(
                eng._research_file_key(grammar_name),
                eng._research_file_key(fallback_name),
                f"{grammar_name!r} (grammar path) and {fallback_name!r} "
                "(fallback path) must not share a marker key")

    def test_two_distinct_fallback_names_do_not_collide(self):
        """And the fallback must not collapse two different rejected names onto
        each other either — normalization alone is lossy."""
        keys = {eng._research_file_key(n) for n in
                ("a.b_RESEARCH.md", "a_b_X_RESEARCH.md", "a-b.c_RESEARCH.md")}
        self.assertEqual(len(keys), 3,
                         f"three distinct rejected names, three keys; got {keys}")

    def test_scope_keyed_sibling_is_not_masked_at_the_gate(self):
        """The consequence, end to end: a scope sibling's ESCALATE must not be
        swallowed by the bare file's PASS."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        bare = eng._research_file_key("mytopic-20260101010101_RESEARCH.md")
        scoped = eng._research_file_key("mytopic-20260101010101_A_RESEARCH.md")
        (base / f"{bare}_R2.md").write_text("---\nverdict: PASS\n---\n")
        (base / f"{scoped}_R1.md").write_text("---\nverdict: ESCALATE\n---\n")

        roll = eng.research_rollup(base)
        verdicts = {r["key"]: r["verdict"] for r in roll["rows"]}
        self.assertIn(scoped, verdicts,
                      "the scope-keyed file must have its own row")
        self.assertEqual(verdicts.get(scoped), "ESCALATE")
        self.assertFalse(roll["all_resolved"],
                         "the sibling's ESCALATE must hold the cycle unresolved")

    def test_the_readable_prefix_carries_slug_and_scope(self):
        """Legibility, explicitly — no longer correctness.

        Once the digest became unconditional, distinctness stopped depending on
        the slug/scope prefix: two files differ by digest whatever the prefix
        collapses. Verified by reverting the scope join, which the rest of the
        suite then did NOT catch, precisely because it no longer causes harm.

        The prefix still matters — it is what an operator reads in a gate block
        and in a marker filename — so it is pinned here as the legibility
        property it has become, rather than left to rot untested.
        """
        key = eng._research_file_key("alpha-20260101010101_A_RESEARCH.md")
        prefix = key.split("--x")[0]
        self.assertEqual(prefix, "alpha--a",
                         "the readable prefix names the slug and the scope")
        bare = eng._research_file_key("alpha-20260101010101_RESEARCH.md")
        self.assertEqual(bare.split("--x")[0], "alpha",
                         "and omits the scope segment when there is none")

    def test_distinct_scope_spellings_do_not_collapse(self):
        """Collision class 4a, on the GRAMMAR path where no digest applied.

        `_norm` maps '-', '_', '.' and ' ' all to '-', so distinct scope
        spellings collapse onto one key. `H_v2_B1` is the literal example in
        bookkeeping-model.md §4 Bucket 2 and `H-v2-B1` is equally grammar-valid,
        because the grammar's scope class is `.+`.
        """
        for a, b in (
            ("assessment-engine-20260709000856_H-v2-B1_RESEARCH.md",
             "assessment-engine-20260709000856_H_v2_B1_RESEARCH.md"),
            ("alpha-20260101010101_A.B_RESEARCH.md",
             "alpha-20260101010101_A_B_RESEARCH.md"),
        ):
            self.assertNotEqual(eng._research_file_key(a),
                                eng._research_file_key(b),
                                f"{a!r} and {b!r} are different files")

    def test_two_timestamps_under_one_slug_do_not_collapse(self):
        """Collision class 4b, and the one with live instances.

        The grammar splits the 14-digit timestamp into its own group, which the
        key never reads — so every timestamped research file of a slug shares
        one key, as does the grandfathered untimestamped form. This repo's
        Thoughts/ folder carries 25 slugs with more than one timestamp,
        including this topic's own with 20.
        """
        names = [
            "assessment-engine-20260709000856_RESEARCH.md",
            "assessment-engine-20260727202257_RESEARCH.md",
            "assessment-engine_RESEARCH.md",
        ]
        keys = [eng._research_file_key(n) for n in names]
        self.assertEqual(len(set(keys)), len(names),
                         f"three distinct files, three keys; got {keys}")

    def test_corpus_sweep_finds_no_collision_and_no_instability(self):
        """A property sweep, not another point case.

        This helper has been wrong three times, each time a collision, and two
        of those were introduced while fixing the previous one. Point tests kept
        missing the next class. This generates a broad corpus of plausible
        research filenames and asserts the three properties over all of them at
        once, so a fourth class has to survive the whole space rather than just
        the examples someone thought of.

        Case-variants are EXPECTED to share a key (K2 — on a case-insensitive
        filesystem they are one file), so the collision check compares distinct
        lowercase forms.
        """
        stems = [
            "alpha", "alpha-beta", "alpha-b", "alpha.beta", "alpha_beta",
            "alpha--beta", "Alpha", "ALPHA", "a", "a-b", "a.b", "a_b",
            "r1", "R1", "rc1", "r1-x", "topic-20260101010101", "topic-2026",
            "topic--x1a2b3c4d", "x", "-alpha", "alpha-", "al pha", "alpha!",
            "a-b-c", "a--b-c", "ab", "a.b.c", "a_b_c",
        ]
        # Spelling VARIANTS of one scope are included deliberately: without
        # `A-B`/`A.B` beside `A_B`, and `H-v2-B1` beside `H_v2_B1`, the corpus
        # never contains collision class 4a at all and the sweep would report a
        # clean space while blind to it — which is exactly what an earlier
        # version of this corpus did.
        scopes = ["", "A", "B", "BETA", "A_B", "A-B", "A.B",
                  "H_v2_B1", "H-v2-B1", "RESEARCH", "X", "BC"]
        names = sorted({
            (f"{s}_{sc}_RESEARCH.md" if sc else f"{s}_RESEARCH.md")
            for s in stems for sc in scopes
        })

        by_key = {}
        for n in names:
            by_key.setdefault(eng._research_file_key(n), []).append(n)

        genuine = {k: g for k, g in by_key.items()
                   if len({x.lower() for x in g}) > 1}
        self.assertEqual(
            genuine, {},
            "different research files must not share a marker key; "
            f"colliding groups: {genuine}")

        # The INVERSE property, and the one a first draft of this sweep missed:
        # one file must yield ONE key. Checking only "different files -> different
        # keys" is half the guarantee — deleting the case-fold-before-classify
        # step splits each case-variant onto its own key, which the collision
        # check above cannot see because it groups by lowercase form. Verified by
        # reverting that step: the sweep passed until this block was added.
        for n in names:
            variants = {n, n.lower(), n.upper().replace(".MD", ".md")}
            variant_keys = {eng._research_file_key(v) for v in variants}
            self.assertEqual(
                len(variant_keys), 1,
                f"{n!r} and its case variants are ONE file on this "
                f"case-insensitive filesystem and must share one key; "
                f"got {variant_keys}")

        # No key may be legacy-glob-unsafe anywhere in the corpus.
        unsafe = [k for k in by_key if re.match(r"^r\d", k)]
        self.assertEqual(unsafe, [],
                         f"keys beginning r<digit> collide with the legacy glob: {unsafe}")

        # One logical file keeps one key regardless of how its path is spelled.
        base = "alpha-20260101010101_A_RESEARCH.md"
        spellings = {eng._research_file_key(p) for p in
                     (base, f"./{base}", f"/abs/path/{base}", f"rel/dir/{base}")}
        self.assertEqual(len(spellings), 1,
                         f"a file's key must not move with its path spelling; {spellings}")

    def test_case_variant_filenames_yield_the_SAME_key(self):
        """The other half of the lowercase rail, and the half that actually bites.

        This filesystem is case-insensitive, so `ALPHA-..._RESEARCH.md` and
        `alpha-..._RESEARCH.md` are ONE file. If they derive two different keys,
        that one file's verdict splits across two marker groups — the very
        split-verdict harm this slice exists to remove.

        Asserting `key == key.lower()` (the test above) does NOT catch this: both
        spellings can produce all-lowercase keys that still differ, because the
        case-sensitive slug grammar sends them down different derivation paths
        (the recognized spelling gets its timestamp stripped, the unrecognized
        one does not). Only a cross-case EQUALITY assertion pins it.
        """
        for upper, lower in (
            # The EXTENSION too: `.MD` and `.md` are one file here. Unreachable
            # through today's dispatcher (its patterns require lowercase `.md`)
            # and fail-closed if reached, but folding it is simply correct and
            # both canon constructions fold it together.
            ("alpha-20260101010101_RESEARCH.MD",
             "alpha-20260101010101_RESEARCH.md"),
            ("RC1_RESEARCH.md", "rc1_RESEARCH.md"),
            ("MyTopic-20260101010101_RESEARCH.md",
             "mytopic-20260101010101_RESEARCH.md"),
            ("ALPHA-20260101010101_RESEARCH.md",
             "alpha-20260101010101_RESEARCH.md"),
        ):
            self.assertEqual(
                eng._research_file_key(upper), eng._research_file_key(lower),
                f"{upper!r} and {lower!r} are the same file on a case-insensitive "
                f"filesystem and must derive one key, not two")

    def test_key_never_collides_with_the_legacy_glob(self):
        """Disjointness rail: no key the helper can emit may produce a marker name
        that matches the legacy `R*.md` glob. A file literally named RC1_RESEARCH.md
        is the adversarial case named in the plan."""
        for name in ("RC1_RESEARCH.md", "R1_RESEARCH.md", "R42-20260101010101_RESEARCH.md",
                     "rc1_RESEARCH.md"):
            key = eng._research_file_key(name)
            marker = f"{key}_R1.md"
            self.assertIsNone(
                re.match(r"^R\d+\.md$", marker),
                f"{name!r} -> {marker!r} must not look like a legacy marker")
            self.assertFalse(
                marker.lower().startswith("r") and re.match(r"^r\d", marker.lower()),
                f"{name!r} -> key {key!r} must not begin with R<digit>")


class MarkerSlotTests(_PerFileBase):
    """The keying carrier — and its deliberate fail-loud omission."""

    def test_slot_exists(self):
        self.assertTrue(hasattr(eng, "ResearchMarkerSlot"),
                        "S1 introduces the keying carrier")

    def test_slot_has_no_fspath_so_a_missed_coercion_fails_loud(self):
        """Guiding Policy 3: prefer a crash the tests catch over a green gate over
        unchecked work. The carrier must NOT be os.PathLike."""
        slot = eng.ResearchMarkerSlot(Path(self.tmpdir), "alpha")
        self.assertFalse(hasattr(slot, "__fspath__"),
                         "the carrier deliberately omits __fspath__")
        with self.assertRaises(TypeError):
            os.fspath(slot)

    def test_slot_names_are_keyed_and_sequence_local(self):
        slot = eng.ResearchMarkerSlot(Path(self.tmpdir), "alpha")
        self.assertEqual(os.path.basename(str(slot.marker_path(1))), "alpha_R1.md")
        self.assertEqual(os.path.basename(str(slot.marker_path(10))), "alpha_R10.md")


class TwoFilesAtMaxTests(_PerFileBase):
    """C1 / C6 / C9 — the headline repro."""

    def test_second_file_is_checked_when_first_is_at_max_rounds(self):
        """File A exhausts its budget; file B in the SAME cycle must still be
        checked on its own budget rather than NOOPing as 'already done'."""
        checker_a = _CountingChecker("DISCREPANCY")
        self._run(checker_a, self.file_a, max_rounds=2)      # A burns its budget

        checker_b = _CountingChecker("PASS")
        result_b = self._run(checker_b, self.file_b, max_rounds=2)

        self.assertNotEqual(result_b.get("status"), "NOOP",
                            "file B must not NOOP against file A's rounds (C6)")
        self.assertGreater(checker_b.dispatches, 0,
                           "file B must actually dispatch checkers (C1)")
        self.assertIn(1, checker_b.rounds_for(self.file_b.name),
                      "file B starts at its own round 1 (C9)")

    def test_each_file_keeps_its_own_marker(self):
        """C2/C3 — both files' verdicts persist, each under its own key."""
        self._run(_CountingChecker("PASS"), self.file_a, max_rounds=2)
        self._run(_CountingChecker("PASS"), self.file_b, max_rounds=2)

        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self.assertTrue((base / f"{key_a}_R1.md").exists(),
                        "file A's marker is keyed to file A")
        self.assertTrue((base / f"{key_b}_R1.md").exists(),
                        "file B's marker is keyed to file B")

    def test_marker_frontmatter_schema_is_unchanged(self):
        """Identity lives in the FILENAME, never in a new frontmatter field."""
        self._run(_CountingChecker("PASS"), self.file_a, max_rounds=2)
        key_a = eng._research_file_key(self.file_a.name)
        text = (self._research_base() / f"{key_a}_R1.md").read_text()
        self.assertIn("schema_version: 3", text)
        self.assertIn("kind: research", text)
        self.assertIn("rounds: 1", text)
        self.assertNotIn("file_key:", text,
                         "no new frontmatter field — identity is in the filename")


class ReaderTests(_PerFileBase):
    """C3 / C7 — one file's PASS must not stand for the cycle."""

    def test_latest_marker_is_per_file(self):
        """_latest_marker must select within a file's own key group, never across."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        (base / f"{key_a}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / f"{key_b}_R1.md").write_text("---\nverdict: ESCALATE\n---\n")

        got_b = eng._latest_marker(base, "default", key=key_b)
        self.assertIsNotNone(got_b, "a keyed group resolves its own latest marker")
        self.assertIn(key_b, os.path.basename(str(got_b)),
                      "file B's read must return file B's marker, not file A's (C3)")

    def test_latest_marker_orders_by_round_number_not_lexically(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        for n in (1, 2, 10):
            (base / f"{key_a}_R{n}.md").write_text(f"---\nverdict: PASS\nrounds: {n}\n---\n")
        got = eng._latest_marker(base, "default", key=key_a)
        self.assertEqual(os.path.basename(str(got)), f"{key_a}_R10.md",
                         "round 10 is later than round 2 — integer order, not lexical")


class RollupTests(_PerFileBase):
    """The shared rollup verb both shell gates consume (S1 introduces it)."""

    def test_rollup_reports_one_row_per_file(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        (base / f"{key_a}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / f"{key_b}_R1.md").write_text("---\nverdict: ESCALATE\n---\n")

        self.assertTrue(hasattr(eng, "research_rollup"),
                        "S1 introduces the shared rollup verb")
        rows = eng.research_rollup(base)
        by_key = {r["key"]: r for r in rows["rows"]}
        self.assertEqual(by_key[key_a]["verdict"], "PASS")
        self.assertEqual(by_key[key_b]["verdict"], "ESCALATE")
        self.assertFalse(rows["all_resolved"],
                         "an ESCALATE row leaves the cycle unresolved (C7/C13)")

    def test_rollup_on_missing_directory_is_a_clean_no_block(self):
        """no-traceback rail, half 1: no check was OWED -> clean exit, NO block.
        Blocking here would false-block every session with no research file."""
        rows = eng.research_rollup(self._research_base() / "does-not-exist")
        self.assertEqual(rows["rows"], [])
        self.assertTrue(rows["all_resolved"],
                        "absent evidence is not unread evidence — must not block")

    def test_rollup_on_unreadable_marker_blocks(self):
        """no-traceback rail, half 2: a check WAS owed and its result is unknown
        -> block, with a named cause rather than a traceback."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        bad = base / f"{key_a}_R1.md"
        bad.write_text("this is not frontmatter at all")
        rows = eng.research_rollup(base)
        self.assertFalse(rows["all_resolved"],
                         "an unparseable marker must block, not be skipped")
        self.assertTrue(any(r.get("error") for r in rows["rows"]),
                        "the row names the failure rather than raising")


class AuditMarkerSiteTests(_PerFileBase):
    """The audit-marker site, post-S2.

    This class replaces an S1-window test asserting that NO carrier reaches this
    site. That was correct only for the S1→S2 window: S2 widened substitution,
    so `factcheck_run` now passes `marker_slot` here for every research-kind
    terminal verdict. The old assertion (`"ResearchMarkerSlot" not in body`)
    survived only because the fix calls the generic `_as_marker_slot` normalizer
    rather than naming the class inline — a coincidence of spelling, not a
    property. It could not fail whether or not a carrier reached the site.
    """

    def test_the_call_site_passes_the_carrier_for_the_research_kind(self):
        src = Path(eng.__file__).read_text()
        call = src.find("_emit_dc_audit_marker(\n")
        self.assertGreater(call, 0, "the call site is present")
        window = src[call:call + 500]
        self.assertIn("marker_slot", window,
                      "the audit-marker call site threads the carrier, which is "
                      "what makes the site's normalization load-bearing")

    def test_the_site_normalizes_whatever_it_receives(self):
        src = Path(eng.__file__).read_text()
        m = re.search(r"\ndef _emit_dc_audit_marker\(.*?\n(.*?)\n(?:def |class )",
                      src, re.S)
        self.assertIsNotNone(m, "the audit-marker function is still present")
        body = m.group(1)
        self.assertIn("_as_marker_slot", body,
                      "the site must normalize its directory argument")
        self.assertNotIn("Path(topic_dir)", body,
                         "the pre-S2 coercion would raise on the carrier it now "
                         "receives — this site has no try/except above it")


class SingleLocusTests(_PerFileBase):
    """No inline slug derivation may remain at any of the four sites."""

    def test_no_inline_classify_slug_remains(self):
        """Exactly ONE `classify(` call may survive — the one inside the key helper.
        (Counting call sites, not a `.slug` regex: the argument expressions contain
        their own parens, so a naive `\\.classify\\([^)]*\\)\\.slug` pattern matches
        nothing and passes vacuously.)"""
        src = Path(eng.__file__).read_text()
        # Strip `#` comment tails before counting. This module carries long
        # explanatory comments, several of which discuss the classifier by name;
        # a comment writing the literal `classify(` would push the count to 2 and
        # fail this rail for a reason that has nothing to do with code structure.
        # (Docstrings are not stripped — none currently contains the literal, and
        # stripping them properly needs a parser, which is more machinery than
        # this rail is worth.)
        code_only = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
        sites = re.findall(r"\bclassify\(", code_only)
        self.assertEqual(
            len(sites), 1,
            f"all slug derivation routes through one locus; found {len(sites)} "
            f"classify( call sites in code, expected exactly 1")

    def test_the_surviving_classify_is_inside_the_derivation_locus(self):
        """The locus is `_research_display_slug`.

        It used to be `_research_file_key`. When the key became injective, the
        readable derivation was split out into its own function and the key now
        delegates to it — so the derivation, and with it the single `classify(`
        call, moved. This test caught that move, which is exactly what a
        single-locus rail is for; it is retargeted, not relaxed. The rail still
        says what it always said: exactly one call site, in the one function
        that derives a slug.
        """
        src = Path(eng.__file__).read_text()
        m = re.search(r"def _research_display_slug\(.*?\n(.*?)\n(?:def |class )",
                      src, re.S)
        self.assertIsNotNone(m, "_research_display_slug is defined")
        self.assertIn("classify(", m.group(1),
                      "the one surviving classify( call lives in the derivation locus")

    def test_the_key_delegates_to_the_display_slug(self):
        """And the key must not re-derive: two derivations would be two loci."""
        src = Path(eng.__file__).read_text()
        m = re.search(r"def _research_file_key\(.*?\n(.*?)\n(?:def |class )", src, re.S)
        self.assertIsNotNone(m, "_research_file_key is defined")
        body = m.group(1)
        self.assertIn("_research_display_slug(", body,
                      "the key delegates the readable derivation")
        self.assertNotIn("classify(", body,
                         "and does not re-derive it")


class RollupCliTests(_PerFileBase):
    """The `research-rollup` CLI verb — the seam the SHELL gates consume.

    `research_rollup()` is a Python function; a bash gate cannot call it. S1's
    close-gate rewire therefore needs a CLI entry point, and Guiding Policy 5
    makes its failure behaviour load-bearing: the gate must be able to tell
    "ran, here are the rows" from "could not run" without guessing.
    """

    def _cli(self, directory, extra=(), env=None):
        cmd = [sys.executable, str(HOOKS / "_factcheck_engine.py"),
               "research-rollup", str(directory)] + list(extra)
        return subprocess.run(cmd, capture_output=True, text=True,
                              env=env, timeout=60)

    def _seed_two(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        (base / f"{key_a}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / f"{key_b}_R1.md").write_text("---\nverdict: ESCALATE\n---\n")
        return base, key_a, key_b

    def test_cli_verb_exists_and_emits_parseable_json(self):
        base, key_a, key_b = self._seed_two()
        proc = self._cli(base)
        self.assertEqual(proc.returncode, 0,
                         f"the verb ran to completion; stderr={proc.stderr!r}")
        payload = json.loads(proc.stdout)          # unparseable output fails here
        self.assertEqual(payload["status"], "OK")
        by_key = {r["key"]: r for r in payload["rows"]}
        self.assertEqual(by_key[key_a]["verdict"], "PASS")
        self.assertEqual(by_key[key_b]["verdict"], "ESCALATE")
        self.assertFalse(payload["all_resolved"])

    def test_cli_on_missing_directory_is_a_clean_no_block(self):
        """'No check was owed' is NOT 'the result could not be read'. The verb
        must exit 0 with all_resolved true so the gate does not false-block a
        session that legitimately has no research file (CV / Internal-KB)."""
        proc = self._cli(self._research_base() / "nope")
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "OK")
        self.assertEqual(payload["rows"], [])
        self.assertTrue(payload["all_resolved"])

    def test_cli_reports_an_unreadable_marker_as_an_unresolved_row(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        (base / f"{key_a}_R1.md").write_text("not frontmatter at all")
        proc = self._cli(base)
        self.assertEqual(proc.returncode, 0, "the verb classifies rather than raising")
        payload = json.loads(proc.stdout)
        self.assertFalse(payload["all_resolved"])
        self.assertTrue(any(r.get("error") for r in payload["rows"]))

    def test_cli_never_leaks_a_traceback(self):
        """A traceback reaching the gate is a defect in the verb (Guiding Policy 5).
        Point it at a path that is a FILE, not a directory."""
        f = Path(self.tmpdir) / "not-a-dir"
        f.write_text("x")
        proc = self._cli(f)
        self.assertNotIn("Traceback", proc.stderr)
        json.loads(proc.stdout)                    # still parseable

    def test_cli_enforces_its_own_timeout_and_fails_closed(self):
        """The timeout is enforced INSIDE the verb: this host has neither
        `timeout` nor `gtimeout`, so a shell-level timeout would not merely be
        unportable, it would fail immediately. An exhausted budget must fail
        CLOSED — a non-zero exit the gate turns into a block, never rows that
        read as resolved."""
        base, _a, _b = self._seed_two()
        proc = self._cli(base, extra=["--timeout-seconds", "0"])
        self.assertNotEqual(proc.returncode, 0,
                            "an exhausted budget exits non-zero (fail-closed)")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "ERROR")
        self.assertIn("time", (payload.get("error") or "").lower())
        self.assertFalse(payload.get("all_resolved", False),
                         "a timed-out verb must never report the cycle resolved")


class _CloseGateBase(_PerFileBase):
    """Harness for driving the REAL `check-research-gate.sh` as a subprocess
    against an isolated HOME — the only way to exercise a bash gate honestly.

    Split out of `CloseGateRewireTests` when S4 added a second gate suite:
    subclassing the test class to borrow its harness silently RE-RUNS every
    parent test method under the child's name, which doubles a slow subprocess
    suite and reports one assertion as two. Carrying the fixture in a base with
    no test methods of its own keeps each test running exactly once.
    """

    GATE = HOOKS / "check-research-gate.sh"

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmpdir) / "home"
        (self.home / ".claude" / "state" / "pre_plan_gates").mkdir(parents=True)
        (self.home / ".claude" / "state" / "pre_plan_gates" / "_active.json").write_text(
            json.dumps({self.SID: {"topic_slug": self.PROJ,
                                   "active_project": self.TOPIC}}))
        self.gate_base = (self.home / ".claude" / "state" / "plan_validation"
                          / self.PROJ / self.TOPIC / "research")
        self.gate_base.mkdir(parents=True)

    def _run_gate(self, gate=None, env_extra=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["RP_STATE_DIR"] = str(self.rp_dir)      # no manifest -> R4 not in sequence
        env.pop("CLAUDE_CODE_REMOTE", None)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["/bin/bash", str(gate or self.GATE)],
            input=json.dumps({"session_id": self.SID, "stop_hook_active": False}),
            capture_output=True, text=True, env=env, timeout=120)

    def _marker(self, key, round_num, body):
        (self.gate_base / f"{key}_R{round_num}.md").write_text(body)


class CloseGateRewireTests(_CloseGateBase):
    """The close gate rendered per-file — S1's reader half."""

    # -- the headline: one file's PASS must not mask another's ESCALATE ------

    def test_file_a_pass_does_not_mask_file_b_escalate(self):
        """C3 / C7 / C10. Pre-S1 the gate takes ONE marker per cycle, so file A's
        PASS is the whole cycle's verdict and B's ESCALATE is invisible."""
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")
        self._marker(key_b, 1, "---\nverdict: ESCALATE\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2,
                         f"the ESCALATE row must block; stderr={proc.stderr!r}")
        self.assertIn(key_b, proc.stderr, "the blocking row names the failing FILE")
        self.assertIn("ESCALATE", proc.stderr)

    def test_scope_keyed_sibling_is_not_masked_through_the_REAL_gate(self):
        """R6 end-to-end: the combined key + rollup + gate behaviour on the
        bare-vs-scope-keyed shape, through the actual bash gate.

        The sibling masking test above uses two independent topics
        ("alpha"/"bravo"), and the scope-keyed test in `KeyDerivationTests`
        stops at the Python rollup. Neither combines them, so the scenario that
        motivated the scope fix — one topic's bare file passing while its own
        scope-keyed sibling escalates — had no end-to-end carrier. It was
        verified by hand and is pinned here.

        The bare file passes at a LATER round than the sibling's escalate: the
        classic newest-slip-wins setup that the pre-keying reader fell for.
        """
        bare = eng._research_file_key("mytopic-20260101010101_RESEARCH.md")
        scoped = eng._research_file_key("mytopic-20260101010101_A_RESEARCH.md")
        self.assertNotEqual(bare, scoped, "precondition: the keys are distinct")
        self._marker(bare, 2, "---\nverdict: PASS\n---\n")
        self._marker(scoped, 1, "---\nverdict: ESCALATE\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2,
                         f"the scope sibling's ESCALATE must block; "
                         f"stderr={proc.stderr!r}")
        self.assertIn(scoped, proc.stderr,
                      "the blocking row names the scope-keyed file")

    def test_all_rows_passing_does_not_block(self):
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")
        self._marker(key_b, 1, "---\nverdict: PASS\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")

    def test_empty_cycle_directory_does_not_block(self):
        """No-false-block rail: neither marker nor sentinel means no check was
        ever owed. Blocking here would break every CV / Internal-KB session.

        NOTE the precondition: no manifest exists here, so Lever B's
        `R4_IN_SEQ` is false. The complementary true-branch is the test below —
        the no-marker case is NOT unconditionally a pass, and asserting only
        this half would misdescribe the gate."""
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")

    def test_empty_cycle_DOES_block_when_a_factcheck_was_expected(self):
        """Lever B's true branch, preserved unchanged by S1 (S4 narrows it).

        When the topic's manifest puts r4_factcheck in the sequence, a cycle with
        no marker is not 'no check owed' — it is a check that was owed and never
        ran, so it blocks. Without this test the suite would assert only the
        pass half and the gate's actual contract would be mis-stated."""
        stub_hooks = Path(self.tmpdir) / "stubhooks_leverb"
        stub_hooks.mkdir()
        shutil.copy2(self.GATE, stub_hooks / self.GATE.name)
        shutil.copy2(HOOKS / "_factcheck_engine.py",
                     stub_hooks / "_factcheck_engine.py")
        (stub_hooks / "research_pipeline.py").write_text(
            'import sys\nprint(\'{"r4_in_sequence": true}\')\nsys.exit(0)\n')
        # The gate only consults the sequence when a manifest file exists.
        (self.rp_dir / f"RP-{self.SID}.json").write_text(
            json.dumps({"cycles": {"default": {}}}))

        proc = self._run_gate(gate=stub_hooks / self.GATE.name)
        self.assertEqual(proc.returncode, 2,
                         f"an expected-but-absent fact-check must BLOCK; "
                         f"stderr={proc.stderr!r}")
        self.assertIn("fact-check not run", proc.stderr)

    # -- fail-CLOSED (Guiding Policy 5), at the surface it governs -----------

    def test_gate_blocks_when_python3_is_absent(self):
        """A check WAS owed and its result is unknown. GP5 requires a NAMED
        cause via `command -v`, not a generic shell error — and never the
        fail-open default this script already carries at its R4_IN_SEQ call."""
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")
        # A curated bin holding every utility the gate needs EXCEPT python3, so
        # the absence under test is python3's and not some collateral tool's.
        curated = Path(self.tmpdir) / "curatedbin"
        curated.mkdir(exist_ok=True)
        for tool in ("jq", "awk", "ls", "sort", "tail", "tr", "cat", "dirname",
                     "sed", "grep", "head", "printf", "basename", "env", "rm"):
            src = shutil.which(tool)
            if src:
                os.symlink(src, curated / tool)
        self.assertIsNone(shutil.which("python3", path=str(curated)),
                          "the curated bin must not contain python3")
        proc = self._run_gate(env_extra={"PATH": str(curated)})
        self.assertEqual(proc.returncode, 2,
                         f"an undeterminable verdict must BLOCK; stderr={proc.stderr!r}")
        self.assertIn("python3", proc.stderr, "the block names the cause")

    def test_gate_blocks_on_unparseable_rollup_output(self):
        """A verb that emits output the gate cannot parse leaves the verdict
        undetermined -> block. Staged with a stub engine beside a copy of the
        real gate, so the real code path runs against controlled output."""
        stub_hooks = Path(self.tmpdir) / "stubhooks"
        stub_hooks.mkdir()
        shutil.copy2(self.GATE, stub_hooks / self.GATE.name)
        (stub_hooks / "_factcheck_engine.py").write_text(
            "import sys\nprint('this is not json')\nsys.exit(0)\n")
        (stub_hooks / "research_pipeline.py").write_text(
            "import sys\nprint('{}')\nsys.exit(0)\n")
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")

        proc = self._run_gate(gate=stub_hooks / self.GATE.name)
        self.assertEqual(proc.returncode, 2,
                         f"unparseable rollup output must BLOCK; stderr={proc.stderr!r}")

    def test_gate_blocks_when_the_rollup_verb_exits_non_zero(self):
        stub_hooks = Path(self.tmpdir) / "stubhooks2"
        stub_hooks.mkdir()
        shutil.copy2(self.GATE, stub_hooks / self.GATE.name)
        (stub_hooks / "_factcheck_engine.py").write_text(
            "import sys\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
        (stub_hooks / "research_pipeline.py").write_text(
            "import sys\nprint('{}')\nsys.exit(0)\n")
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")

        proc = self._run_gate(gate=stub_hooks / self.GATE.name)
        self.assertEqual(proc.returncode, 2,
                         f"a non-zero rollup must BLOCK; stderr={proc.stderr!r}")
        # GP5 obligation 2: the block is ACTIONABLE — it names the failing
        # cycle, the error, and the command to re-run. Never a bare non-zero.
        self.assertIn("default", proc.stderr, "the block names the failing cycle")
        self.assertIn("undetermined", proc.stderr, "the block names the error")
        self.assertIn("research-rollup", proc.stderr,
                      "the block names the command to re-run")

    def test_gate_uses_no_shell_timeout_command(self):
        """Rail: the timeout lives inside the Python verb. Neither `timeout` nor
        `gtimeout` exists on this host, so a shell-level one would fail outright."""
        src = self.GATE.read_text()
        self.assertIsNone(re.search(r"(?m)^\s*g?timeout\s+\d", src),
                          "no shell timeout command may guard the rollup call")

    # -- the sanctioned exits, now applied PER ROW ---------------------------

    def test_sanctioned_bypass_resolves_only_its_own_row(self):
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(key_a, 1,
                     '---\nverdict: BYPASSED\nbypass_reason: "all 3 checkers timed out"\n---\n')
        self._marker(key_b, 1, "---\nverdict: ESCALATE\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, "B still blocks")
        self.assertIn(key_b, proc.stderr)
        self.assertNotIn(key_a, proc.stderr, "the sanctioned row is not reported as blocking")

    def test_sanctioned_bypass_alone_does_not_block(self):
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(key_a, 1,
                     '---\nverdict: BYPASSED\nbypass_reason: "engine could not run"\n---\n')
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")

    def test_bypass_without_a_real_reason_still_blocks(self):
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(key_a, 1, '---\nverdict: BYPASSED\nbypass_reason: "   "\n---\n')
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, "a whitespace-only reason is no reason")

    def test_accepted_incomplete_resolves_only_its_own_row(self):
        """G1's named harm: pre-S1 one file's accepted-INCOMPLETE clears the
        WHOLE cycle. Applied per row, it must clear only its own."""
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(key_a, 1,
                     '---\nverdict: INCOMPLETE\naccept_reason: "source is bot-blocked"\n---\n')
        self._marker(key_b, 1, "---\nverdict: INCOMPLETE\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, "B's un-accepted INCOMPLETE still blocks")
        self.assertIn(key_b, proc.stderr)
        self.assertNotIn(key_a, proc.stderr)

    # -- legacy corpus keeps behaving (no regression before S7 ships) --------

    def test_legacy_passing_marker_still_passes(self):
        (self.gate_base / "R1.md").write_text("---\nverdict: PASS\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")

    def test_legacy_non_pass_marker_still_blocks(self):
        (self.gate_base / "R1.md").write_text("---\nverdict: ESCALATE\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2)

    def test_legacy_and_keyed_groups_are_both_reported(self):
        """Mixed corpus: the unattributed legacy group is surfaced rather than
        silently dropped. S7 supplies the operator exit; S1 must not hide it."""
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")
        (self.gate_base / "R1.md").write_text("---\nverdict: DIRTY\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn("R1.md", proc.stderr, "the legacy group is named, not dropped")


# ===========================================================================
# S2 — coercion audit completion.
#
# S1 keyed the round-sequence sites but left four OTHER canonical-marker sites
# opening with `d = Path(topic_dir)`. Those raise TypeError on a slot (neither
# slot type is os.PathLike — the deliberate fail-loud guard), so S2 must land
# all four normalizations in the SAME slice that widens substitution to them.
# ===========================================================================


class AsMarkerSlotTests(_PerFileBase):
    """The one coercion locus, and the legacy slot it produces."""

    def test_normalizer_exists_and_passes_a_keyed_slot_through(self):
        self.assertTrue(hasattr(eng, "_as_marker_slot"),
                        "S2 introduces the coercion normalizer")
        keyed = eng.ResearchMarkerSlot(Path(self.tmpdir), "alpha")
        self.assertIs(eng._as_marker_slot(keyed), keyed,
                      "a slot passes through untouched, never re-wrapped")

    def test_normalizer_wraps_a_plain_path_in_legacy_names(self):
        slot = eng._as_marker_slot(Path(self.tmpdir))
        self.assertIsNone(slot.key, "a plain directory carries no per-file key")
        self.assertEqual(os.path.basename(str(slot.marker_path(1))), "R1.md",
                         "the legacy slot reproduces the flat R{n}.md name")
        self.assertEqual(os.path.basename(str(slot.marker_path(10))), "R10.md")

    def test_normalizer_accepts_a_str_as_well_as_a_path(self):
        slot = eng._as_marker_slot(str(self.tmpdir))
        self.assertEqual(os.path.basename(str(slot.marker_path(2))), "R2.md")

    def test_legacy_slot_orders_by_round_number_not_lexically(self):
        d = Path(self.tmpdir) / "legacyorder"
        d.mkdir()
        for n in (1, 2, 10):
            (d / f"R{n}.md").write_text("---\nverdict: PASS\n---\n")
        got = eng._as_marker_slot(d).existing()
        self.assertEqual([p.name for p in got], ["R1.md", "R2.md", "R10.md"],
                         "round 10 sorts after round 2 — integer, not lexical")

    def test_legacy_slot_is_also_not_pathlike(self):
        """The fail-loud guard must survive S2. If the LEGACY slot were
        coercible, a site that still did `Path(topic_dir)` would silently work
        for non-research kinds and only blow up on research — the worst shape,
        because it hides the mistake until the one kind that matters."""
        slot = eng._as_marker_slot(Path(self.tmpdir))
        self.assertFalse(hasattr(slot, "__fspath__"))
        with self.assertRaises(TypeError):
            os.fspath(slot)


class CoercionSiteTests(_PerFileBase):
    """Each of the four sites must accept a slot AND honour its key.

    These are the per-site proofs that the normalization is load-bearing: every
    one of them raises TypeError against the pre-S2 `Path(topic_dir)` code, so a
    reverted site fails loudly here rather than silently writing unkeyed.
    """

    def _slot(self, name="alpha"):
        d = self._research_base()
        d.mkdir(parents=True, exist_ok=True)
        return eng.ResearchMarkerSlot(d, name), d

    def test_site1_source_integrity_marker_lands_in_the_files_own_group(self):
        slot, d = self._slot()
        (d / f"{slot.key}_R1.md").write_text("---\nverdict: PASS\n---\n")
        out = eng._write_source_integrity_marker(
            slot, "ESCALATE", [], [], kind="research")
        self.assertEqual(Path(out).name, f"{slot.key}_R2.md",
                         "sized from this file's own sequence, not the directory")
        self.assertFalse((d / "R1.md").exists(),
                         "no unkeyed marker dropped beside the keyed group")

    def test_site2_coverage_marker_lands_in_the_files_own_group(self):
        slot, d = self._slot("bravo")
        (d / f"{slot.key}_R1.md").write_text("---\nverdict: PASS\n---\n")
        out = eng._write_coverage_marker(
            slot, "ESCALATE", [], [], "test", kind="research")
        self.assertEqual(Path(out).name, f"{slot.key}_R2.md")
        self.assertFalse((d / "R1.md").exists())

    def test_site3_accept_marker_is_CAPABLE_of_a_keyed_slot(self):
        """S2 makes it capable; S8 makes it keyed by constructing the slot at
        the call site. Capable != keyed — the plan's promotion table turns on
        exactly that distinction, so this asserts capability only."""
        slot, d = self._slot("charlie")
        out = eng.write_accept_marker(slot, "bot-blocked source", kind="research")
        self.assertEqual(Path(out).name, f"{slot.key}_R1.md")

    def test_site4_audit_marker_glob_accepts_a_slot(self):
        """The site that forced all four into one slice: its caller has no
        try/except, so a missed coercion here is a hard crash, not a degrade.

        This calls the REAL `_emit_dc_audit_marker`. An earlier version only
        called `_as_marker_slot(slot).existing()` — which proves the generic
        normalizer works, not that the production site threads a carrier. Under
        that version, reverting site 4 to `Path(topic_dir)` would have been
        caught by no test in this file.
        """
        if not getattr(eng, "_PYDANTIC_AVAILABLE", False):
            self.skipTest("audit-marker writer requires pydantic")
        slot, d = self._slot("delta")
        (d / f"{slot.key}_R1.md").write_text("---\nverdict: PASS\n---\n")

        # A revert to `Path(topic_dir)` at this site raises TypeError, because
        # neither slot type is os.PathLike. Reaching the marker write at all is
        # the proof that the coercion is in place.
        eng._emit_dc_audit_marker(
            draft_path=str(self.file_a),
            kind="research",
            verdict={"status": "PASS", "rounds": 1},
            models=["sonnet"],
            topic_dir=slot,
            caller=self.SID,
            topic=self.TOPIC,
        )

    def test_site4_would_fail_loudly_if_reverted(self):
        """Pins WHY site 4 had to land in the same slice as the widening: the
        pre-S2 expression raises on the carrier its call site now supplies."""
        slot, _d = self._slot("delta")
        with self.assertRaises(TypeError):
            Path(slot).glob("R*.md")

    def test_reverting_any_site_to_Path_raises(self):
        """The guard that makes the four fixes load-bearing rather than
        cosmetic: the pre-S2 expression raises on both slot types."""
        for home in (eng.ResearchMarkerSlot(Path(self.tmpdir), "alpha"),
                     eng._as_marker_slot(Path(self.tmpdir))):
            with self.assertRaises(TypeError, msg=f"{home!r} must not coerce"):
                Path(home)


class ZeroEditGateTests(_PerFileBase):
    """The two gate functions sit in the Group II / Group V collision zone and
    may take NO edits. They only pass their directory argument through, which is
    what lets S2 key their writers without touching them — a checkable property,
    not an aspiration."""

    GATES = ("_run_source_integrity_gate", "_run_coverage_axis_gate")

    def _body(self, name):
        src = Path(eng.__file__).read_text()
        m = re.search(rf"\ndef {name}\(.*?\n(.*?)\n(?:def |class )", src, re.S)
        self.assertIsNotNone(m, f"{name} is still present")
        return m.group(1)

    def test_neither_gate_function_constructs_or_normalizes_a_slot(self):
        for name in self.GATES:
            body = self._body(name)
            self.assertNotIn("_as_marker_slot", body,
                             f"{name} must take ZERO edits — it only passes through")
            self.assertNotIn("ResearchMarkerSlot", body,
                             f"{name} must take ZERO edits — it only passes through")

    def test_neither_gate_function_coerces_its_directory_argument(self):
        """A `Path(topic_dir)` inside either gate would raise on the slot the
        widened call site now hands it."""
        for name in self.GATES:
            self.assertNotIn("Path(topic_dir)", self._body(name),
                             f"{name} must not coerce the marker home")


class WorkflowStringificationGuardTests(_PerFileBase):
    """The last rail clause, and the subtlest one.

    `_append_validated_via_frontmatter` writes `validated_via: {topic_dir}` —
    it STRINGIFIES the argument. A slot reaching it would not crash; it would
    silently write a repr into a file's frontmatter. Its exemption therefore
    rests on a KIND GUARD, not on the slot being substitutable, and that is what
    must be pinned."""

    def test_the_stringification_site_is_unedited(self):
        src = Path(eng.__file__).read_text()
        m = re.search(r"\ndef _append_validated_via_frontmatter\(.*?\n(.*?)\n(?:def |class )",
                      src, re.S)
        self.assertIsNotNone(m)
        self.assertIn("validated_via: {topic_dir}", m.group(1),
                      "the site keeps its plain stringification (no edit)")

    def test_a_carrier_can_never_reach_the_stringification_site(self):
        """Its only call site is under `if kind == "workflow"`, and a marker
        slot is only constructed for `kind == "research"`. Two disjoint kinds —
        that disjointness IS the guard."""
        src = Path(eng.__file__).read_text()
        # The CALL, not the `def` — both spell the same argument list, and
        # matching the definition would make this test pass vacuously.
        m = re.search(r"(?<!def )_append_validated_via_frontmatter\(draft_path", src)
        self.assertIsNotNone(m, "the call site is still present")
        call = m.start()
        preceding = src[max(0, call - 400):call]
        self.assertIn('kind == "workflow"', preceding,
                      "the call site is guarded by the workflow kind")
        # And the slot is built only for research.
        built = re.search(r"marker_slot = ResearchMarkerSlot\(", src)
        self.assertIsNotNone(built, "the slot construction site is present")
        before_build = src[max(0, built.start() - 300):built.start()]
        self.assertIn('kind == "research"', before_build,
                      "a slot is constructed only for the research kind")

    def test_stringifying_a_slot_would_be_visibly_wrong(self):
        """Why the kind guard matters rather than being belt-and-braces: this
        failure mode is silent corruption, not a crash."""
        slot = eng.ResearchMarkerSlot(Path(self.tmpdir), "alpha")
        self.assertIn("ResearchMarkerSlot", f"{slot}",
                      "a stringified slot yields a repr, not a usable path")


class FourKindCharacterizationTests(_PerFileBase):
    """AD22(f): the other four fact-check kinds keep byte-identical marker
    names and behaviour. Research is the only kind that keys."""

    OTHER_KINDS = ("thought", "plan", "kl_extraction", "coverage_check")

    def test_non_research_kinds_write_unkeyed_markers(self):
        for kind in self.OTHER_KINDS:
            with self.subTest(kind=kind):
                tmp = Path(self.tmpdir) / f"kindcheck-{kind}"
                tmp.mkdir()
                slot = eng._as_marker_slot(tmp)
                self.assertIsNone(slot.key)
                self.assertEqual(os.path.basename(str(slot.marker_path(1))),
                                 "R1.md",
                                 f"{kind} keeps the flat legacy marker name")

    def test_non_research_kind_builds_no_carrier(self):
        """The construction guard, read from source: only research keys."""
        src = Path(eng.__file__).read_text()
        m = re.search(r"marker_slot = None\n(.*?)marker_slot = ResearchMarkerSlot\(",
                      src, re.S)
        self.assertIsNotNone(m, "the guarded construction is present")
        self.assertIn('kind == "research"', m.group(1),
                      "only the research kind constructs a carrier")

    def test_a_non_research_run_leaves_no_keyed_marker(self):
        """End-to-end: a thought-kind run writes R1.md, never {key}_R1.md."""
        draft = self._draft("charlie-20260101010101_THOUGHT.md")
        checker = _CountingChecker("PASS")
        self._run(checker, draft, kind="thought", cycle_id=None, max_rounds=3)
        base = self.state_dir / self.PROJ / self.TOPIC / "thought"
        self.assertTrue((base / "R1.md").exists(),
                        "the thought kind writes the flat legacy marker")
        self.assertEqual(list(base.glob("*_R*.md")), [],
                         "no keyed marker may appear for a non-research kind")


# ===========================================================================
# S3 — per-file scoping of the two SUPPRESSION paths.
#
# Two ways one file used to silence another: a shared `.debounce` cooldown, and
# a --force clear that unlinked every marker in the directory. S3 keys the
# first and scopes + de-destructs the second.
# ===========================================================================


class CooldownIsPerFileTests(_PerFileBase):
    """C4 — checking one file must not suppress a sibling's dispatch."""

    def test_one_files_dispatch_does_not_debounce_a_sibling(self):
        """Red before S3: a single shared `.debounce` in the cycle directory
        meant file A's dispatch put file B into cooldown, and B returned
        DEBOUNCED having never been checked."""
        checker_a = _CountingChecker("PASS")
        first = self._run(checker_a, self.file_a, debounce_seconds=3600)
        self.assertNotEqual(first.get("status"), "DEBOUNCED",
                            "the first file dispatches normally")

        checker_b = _CountingChecker("PASS")
        second = self._run(checker_b, self.file_b, debounce_seconds=3600)
        self.assertNotEqual(
            second.get("status"), "DEBOUNCED",
            "file B has its OWN cooldown — A's dispatch must not silence it")
        self.assertGreater(checker_b.dispatches, 0,
                           "file B actually dispatched checkers")

    def test_the_same_file_is_still_debounced(self):
        """The cooldown must still WORK per file — keying it must not disable
        it. A per-file sentinel that never fires would trade one bug for
        another."""
        self._run(_CountingChecker("PASS"), self.file_a, debounce_seconds=3600)
        checker = _CountingChecker("PASS")
        again = self._run(checker, self.file_a, debounce_seconds=3600)
        self.assertEqual(again.get("status"), "DEBOUNCED",
                         "the same file within the window is still skipped")
        self.assertEqual(checker.dispatches, 0, "no checkers dispatched")

    def test_sentinel_is_keyed_and_stays_in_the_cycle_directory(self):
        """Rail: keying the sentinel changes its NAME, not the layout."""
        self._run(_CountingChecker("PASS"), self.file_a, debounce_seconds=3600)
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        self.assertTrue((base / f".debounce-{key_a}").exists(),
                        "the sentinel is keyed to the writing file")
        self.assertTrue((base / f".debounce-{key_a}").is_file(),
                        "and stays a flat file in the cycle dir, not a subdir")

    def test_non_research_kind_keeps_the_unkeyed_sentinel(self):
        draft = self._draft("delta-20260101010101_THOUGHT.md")
        self._run(_CountingChecker("PASS"), draft, kind="thought",
                  cycle_id=None, debounce_seconds=3600)
        base = self.state_dir / self.PROJ / self.TOPIC / "thought"
        self.assertTrue((base / ".debounce").exists(),
                        "every non-research kind keeps the shared sentinel name")
        self.assertEqual(list(base.glob(".debounce-*")), [],
                         "no keyed sentinel for a non-research kind")


class ForceIsScopedAndNonDestructiveTests(_PerFileBase):
    """The four force-scoping rail clauses, one assertion each."""

    def _seed_group(self, key, rounds=(1, 2)):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        written = []
        for n in rounds:
            p = base / f"{key}_R{n}.md"
            p.write_text(f"---\nverdict: PASS\nrounds: {n}\n---\nround {n}\n")
            written.append(p)
        return base, written

    def test_forcing_A_leaves_B_markers_byte_intact(self):
        """C5 + the destruction half of C2 — diagnosed consequence 3. No other
        slice's gate exercises this."""
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        base, _ = self._seed_group(key_a)
        _, b_markers = self._seed_group(key_b)
        b_before = {p.name: p.read_bytes() for p in b_markers}

        self._run(_CountingChecker("PASS"), self.file_a, force=True)

        for name, blob in b_before.items():
            survivor = base / name
            self.assertTrue(survivor.exists(),
                            f"forcing A must not remove B's {name}")
            self.assertEqual(survivor.read_bytes(), blob,
                             f"B's {name} must be byte-identical after A's force")

    def test_forcing_A_removes_no_dispatch_sentinel(self):
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        base, _ = self._seed_group(key_a)
        for sentinel in (f".debounce-{key_a}", f".debounce-{key_b}", ".debounce"):
            (base / sentinel).write_text("")

        self._run(_CountingChecker("PASS"), self.file_a, force=True)

        for sentinel in (f".debounce-{key_a}", f".debounce-{key_b}", ".debounce"):
            self.assertTrue((base / sentinel).exists(),
                            f"force must not remove {sentinel} — its own included")

    def test_forcing_A_does_not_touch_the_legacy_unkeyed_group(self):
        key_a = eng._research_file_key(self.file_a.name)
        base, _ = self._seed_group(key_a)
        legacy = []
        for n in (1, 2):
            p = base / f"R{n}.md"
            p.write_text(f"---\nverdict: ESCALATE\n---\nlegacy {n}\n")
            legacy.append((p, p.read_bytes()))

        self._run(_CountingChecker("PASS"), self.file_a, force=True)

        for p, blob in legacy:
            self.assertTrue(p.exists(), f"legacy {p.name} must survive a keyed force")
            self.assertEqual(p.read_bytes(), blob, "and be byte-identical")

    def test_forced_aside_markers_remain_readable_on_disk(self):
        """The clear is a MOVE, not a delete — the assertion that separates
        `safe-defaults.md` compliance from a narrower blast radius."""
        key_a = eng._research_file_key(self.file_a.name)
        base, a_markers = self._seed_group(key_a)
        originals = {p.read_text() for p in a_markers}

        self._run(_CountingChecker("PASS"), self.file_a, force=True)

        backups = sorted(base.glob(f"{key_a}_R*.md.bak-*"))
        self.assertEqual(len(backups), len(a_markers),
                         "every cleared marker was moved aside, not unlinked")
        self.assertEqual({p.read_text() for p in backups}, originals,
                         "the moved-aside content is still readable and unchanged")

    def test_force_clears_the_whole_group_so_no_gap_remains(self):
        """Round sizing is len(existing)+1 — a COUNT, not a max — so a PARTIAL
        clear would leave a gap (R1 moved, R2 left) and the next write would
        land ON an existing marker. All-or-nothing keeps the sequence sound."""
        key_a = eng._research_file_key(self.file_a.name)
        base, seeded = self._seed_group(key_a, rounds=(1, 2, 3))
        seeded_blobs = {p.read_bytes() for p in seeded}

        self._run(_CountingChecker("PASS"), self.file_a, force=True,
                  max_rounds=3)

        # FIRST prove the clear actually ran. Without this the rest is vacuous
        # against a "force did nothing" regression: with 3 seeded markers and
        # max_rounds=3, a disabled force block leaves start_round at 4, the
        # empty-range guard returns NOOP before any write, and the untouched
        # [1,2,3] would satisfy the contiguity check trivially.
        backups = sorted(base.glob(f"{key_a}_R*.md.bak-*"))
        self.assertEqual(len(backups), len(seeded),
                         "the force clear must actually have moved the group aside")

        live = sorted(p for p in base.glob(f"{key_a}_R*.md")
                      if ".bak-" not in p.name)
        self.assertTrue(live, "the forced re-run wrote a fresh sequence")
        self.assertTrue(
            {p.read_bytes() for p in live}.isdisjoint(seeded_blobs),
            "the live markers are the NEW run's, not the seeded ones left in place")

        rounds = sorted(int(re.match(rf"^{re.escape(key_a)}_R(\d+)\.md$",
                                     p.name).group(1)) for p in live)
        self.assertEqual(rounds, list(range(1, len(rounds) + 1)),
                         f"the surviving sequence must be contiguous from 1; got {rounds}")


class NewKeyShapeSurvivesEveryReaderTests(_PerFileBase):
    """The key is now `<prefix>--x<8hex>` — it contains hyphens and an `x`.

    Every reader has to split key-from-round correctly on that longer shape.
    `_KEYED_MARKER_RE` uses a GREEDY `(?P<key>.+)_R(\\d+)\\.md$`, which is what
    makes it land on the LAST `_R<digits>` rather than an earlier one; a lazy
    quantifier would truncate the key. This was verified by hand when the shape
    changed and is pinned here so a future regex tweak cannot quietly break it.
    """

    def _seed(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key("alpha-20260101010101_A_RESEARCH.md")
        for n in (1, 2, 10):
            (base / f"{key}_R{n}.md").write_text(
                f"---\nverdict: PASS\nrounds: {n}\n---\n")
        (base / f"{key}_R2.md.bak-20260814010000").write_text(
            "---\nverdict: ESCALATE\n---\n")
        (base / f".debounce-{key}").write_text("")
        return base, key

    def test_the_keyed_regex_splits_the_long_key_from_the_round(self):
        key = eng._research_file_key("alpha-20260101010101_A_RESEARCH.md")
        self.assertIn("--x", key, "precondition: the key carries a digest")
        m = eng._KEYED_MARKER_RE.match(f"{key}_R10.md")
        self.assertIsNotNone(m)
        self.assertEqual(m.group("key"), key,
                         "the whole key, not a prefix truncated at an inner _R")
        self.assertEqual(m.group("round"), "10")

    def test_the_slot_orders_the_long_key_by_round_number(self):
        base, key = self._seed()
        got = [p.name for p in eng.ResearchMarkerSlot(base, key).existing()]
        self.assertEqual(got, [f"{key}_R1.md", f"{key}_R2.md", f"{key}_R10.md"],
                         "R10 sorts after R2 on the new shape too")

    def test_the_rollup_reads_the_long_key_and_picks_the_newest_round(self):
        base, key = self._seed()
        rows = {r["key"]: r for r in eng.research_rollup(base)["rows"]}
        self.assertIn(key, rows, "the keyed group is found under its full key")
        self.assertEqual(rows[key]["rounds"], 10, "newest round wins")

    def test_the_gate_globs_match_the_long_key_and_not_its_neighbours(self):
        base, key = self._seed()
        script = (f'cd "{base}"; a=(*_R*.md); b=(R*.md); '
                  'printf "%s|%s" "${a[*]}" "${b[*]}"')
        out = subprocess.run(["/bin/bash", "-c", script],
                             capture_output=True, text=True).stdout
        keyed_hits, legacy_hits = out.split("|", 1)
        self.assertIn(f"{key}_R10.md", keyed_hits)
        for hits in (keyed_hits, legacy_hits):
            self.assertNotIn(".debounce", hits,
                             "the cooldown sentinel is not a marker")
            self.assertNotIn(".bak-", hits, "a backup is not a marker")


class BackupsAreInvisibleToReadersTests(_PerFileBase):
    """The necessary complement of "forced-aside markers remain readable".

    A backup must stay readable ON DISK yet be invisible TO EVERY READER. If any
    reader still counted it, --force would not actually reset the sequence and a
    superseded verdict could keep gating — which would make the move-aside a
    regression relative to the unlink it replaced.

    The sibling force test filters `.bak-` names out itself, so it cannot prove
    this; only a direct assertion can.
    """

    def _seed(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f"{key}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / f"{key}_R2.md.bak-20260814010000").write_text(
            "---\nverdict: ESCALATE\n---\n")
        (base / "R7.md.bak-20260814010000").write_text("---\nverdict: DIRTY\n---\n")
        return base, key

    def test_no_slot_counts_a_backup(self):
        base, key = self._seed()
        keyed = eng.ResearchMarkerSlot(base, key).existing()
        self.assertEqual([p.name for p in keyed], [f"{key}_R1.md"],
                         "the keyed slot must not count a .bak- file")
        legacy = eng._as_marker_slot(base).existing()
        self.assertEqual(legacy, [],
                         "the legacy slot must not count a .bak- file either")

    def test_the_rollup_does_not_surface_a_backup(self):
        base, key = self._seed()
        roll = eng.research_rollup(base)
        self.assertEqual([(r["key"], r["verdict"]) for r in roll["rows"]],
                         [(key, "PASS")],
                         "a superseded ESCALATE backup must not reach the gate")
        self.assertTrue(roll["all_resolved"],
                        "and must not hold the cycle unresolved")

    def test_the_shell_gates_globs_do_not_match_a_backup(self):
        """Checked through bash, not a Python approximation — the gate's globs
        are what actually run."""
        base, key = self._seed()
        script = (f'cd "{base}"; a=(*_R*.md); b=(R*.md); '
                  'printf "%s|%s" "${a[*]}" "${b[*]}"')
        out = subprocess.run(["/bin/bash", "-c", script],
                             capture_output=True, text=True).stdout
        keyed_hits, legacy_hits = out.split("|", 1)
        self.assertEqual(keyed_hits.strip(), f"{key}_R1.md")
        self.assertNotIn(".bak-", keyed_hits)
        self.assertNotIn(".bak-", legacy_hits)


class MoveAsideFailurePathTests(_PerFileBase):
    """The failure path of the safety change — where it matters most.

    `_move_aside` returns None on OSError, leaving the marker in place. If the
    force path then reset the sequence to 1 regardless, the next round's write
    would land ON that marker and destroy a recorded verdict with NO backup —
    defeating "move, never delete" exactly where the guarantee is load-bearing,
    and silently.

    That the pre-S3 `unlink()` had the same end state is not a defence: S3's
    whole purpose is that a recorded verdict is never destroyed without a
    readable copy surviving.
    """

    def _seed_group(self, key, rounds=(1, 2)):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        for n in rounds:
            (base / f"{key}_R{n}.md").write_text(
                f"---\nverdict: ESCALATE\nrounds: {n}\n---\nround {n}\n")
        return base

    def test_a_failed_move_aside_never_loses_the_marker(self):
        key_a = eng._research_file_key(self.file_a.name)
        base = self._seed_group(key_a, rounds=(1, 2))
        doomed = base / f"{key_a}_R2.md"
        before = doomed.read_bytes()

        real_move = eng._move_aside

        def _flaky(path, _now=None):
            if Path(path).name == doomed.name:
                return None                      # simulate a rename that fails
            return real_move(path, _now=_now)

        # A DISCREPANCY checker keeps dispatching, so the run reaches round 2 and
        # would write over `{key}_R2.md`. A PASS checker converges in one round
        # and never touches R2 — it would let this test pass vacuously.
        with mock.patch.object(eng, "_move_aside", side_effect=_flaky):
            self._run(_CountingChecker("DISCREPANCY"), self.file_a,
                      force=True, max_rounds=3)

        self.assertTrue(doomed.exists(),
                        "a marker that could not be moved aside must still exist")
        self.assertEqual(
            doomed.read_bytes(), before,
            "and must NOT have been overwritten by the new round's content — "
            "destroying it with no backup is the exact harm move-aside prevents")

    def test_a_failed_move_aside_at_max_rounds_reports_the_real_cause(self):
        """The boundary my own C-H fix made reachable.

        When the marker that could not be moved sits at `max_rounds`, the
        continue-past-it sequence starts beyond the budget and trips the
        pre-existing empty-range guard, which returns
        `NOOP / already_at_max_rounds` and tells the caller to "Pass --force ...
        to re-verify from round 1". But --force WAS passed, and retrying hits
        the same rename failure. Before the fix that branch was unreachable
        under force, so the fix introduced a silent no-op with a misleading
        remediation.

        The run must instead say what actually happened. Both sibling tests
        deliberately left headroom above the seeded rounds, so neither reached
        this case.
        """
        key_a = eng._research_file_key(self.file_a.name)
        base = self._seed_group(key_a, rounds=(1, 2, 3))
        doomed = base / f"{key_a}_R3.md"
        before = doomed.read_bytes()

        real_move = eng._move_aside

        def _flaky(path, _now=None):
            if Path(path).name == doomed.name:
                return None
            return real_move(path, _now=_now)

        err = io.StringIO()
        with mock.patch.object(eng, "_move_aside", side_effect=_flaky):
            with contextlib.redirect_stderr(err):
                res = self._run(_CountingChecker("PASS"), self.file_a,
                                force=True, max_rounds=3)

        self.assertEqual(doomed.read_bytes(), before,
                         "the un-backed-up marker is still not overwritten")
        blob = json.dumps(res) + err.getvalue()
        self.assertNotIn(
            "already_at_max_rounds", res.get("reason", ""),
            "a forced run must not be reported as merely being at max rounds — "
            "force was passed, so that remediation is misleading")
        self.assertIn(
            doomed.name, blob,
            "the outcome must name the marker whose move-aside failed")

    def test_a_failed_move_aside_is_surfaced_not_silent(self):
        key_a = eng._research_file_key(self.file_a.name)
        base = self._seed_group(key_a, rounds=(1,))
        doomed = base / f"{key_a}_R1.md"

        def _always_fail(path, _now=None):
            return None

        err = io.StringIO()
        with mock.patch.object(eng, "_move_aside", side_effect=_always_fail):
            with contextlib.redirect_stderr(err):
                self._run(_CountingChecker("PASS"), self.file_a, force=True)

        self.assertIn(doomed.name, err.getvalue(),
                      "the failure names the marker it could not back up")


class MoveAsideTests(_PerFileBase):
    """The backup primitive itself."""

    def test_move_aside_preserves_content_and_returns_the_backup(self):
        p = Path(self.tmpdir) / "R1.md"
        p.write_text("payload")
        dest = eng._move_aside(p)
        self.assertIsNotNone(dest)
        self.assertFalse(p.exists(), "the original name is freed")
        self.assertEqual(Path(dest).read_text(), "payload")
        self.assertIn(".bak-", Path(dest).name)

    def test_same_second_collision_retries_at_the_next_second(self):
        """Two markers moved inside one second must not clobber each other —
        second-resolution stamps collide, so the second retries."""
        a = Path(self.tmpdir) / "R1.md"
        b = Path(self.tmpdir) / "R2.md"
        a.write_text("first")
        b.write_text("second")
        frozen = 1_760_000_000.0
        d1 = eng._move_aside(a, _now=frozen)
        d2 = eng._move_aside(b, _now=frozen)      # same instant
        self.assertIsNotNone(d1)
        self.assertIsNotNone(d2)
        self.assertNotEqual(Path(d1).name, Path(d2).name,
                            "the collision resolved to a different backup name")
        self.assertEqual(Path(d1).read_text(), "first")
        self.assertEqual(Path(d2).read_text(), "second")


class DispatchSentinelOrderingTests(_PerFileBase):
    """S4 — `.dispatched-{key}` is written when the KEY IS COMPUTED.

    The ordering is the load-bearing part of this slice, and it is exactly what
    the pre-S4 code cannot do: a file suppressed by cooldown or resume-NOOP'd
    produces no marker at all, so a reader that groups only markers cannot tell
    "checked and passed" from "never checked". The sentinel is positive evidence
    that a fact-check was ATTEMPTED for that specific file, gathered from the
    PostToolUse path-glob dispatcher rather than from the session manifest —
    which is what makes it registration-independent (AD11).

    Each test below fails if the sentinel is written after the early return it
    names. The existing suite could not catch that, because every one of its
    cases presupposes a dispatch that runs to completion.
    """

    def _sentinel(self, draft):
        return self._research_base() / (
            ".dispatched-" + eng._research_file_key(draft.name))

    def test_a_completed_dispatch_leaves_a_sentinel(self):
        checker = _CountingChecker("PASS")
        self._run(checker, self.file_a)
        self.assertTrue(self._sentinel(self.file_a).exists(),
                        "a dispatched file records that it was dispatched")

    def test_the_sentinel_is_zero_content(self):
        """It is evidence, not a verdict. Giving it a body would invite a reader
        to parse one out of it."""
        self._run(_CountingChecker("PASS"), self.file_a)
        self.assertEqual(self._sentinel(self.file_a).stat().st_size, 0)

    def test_a_DEBOUNCED_dispatch_still_leaves_a_sentinel(self):
        """ORDERING ASSERTION 1 — the sentinel precedes the cooldown check.

        Red if the touch is placed after the debounce guard: the run returns
        DEBOUNCED and never reaches the touch, so a file silenced by its own
        cooldown leaves no trace and the gate cannot report it."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f".debounce-{key}").touch()          # already in cooldown

        checker = _CountingChecker("PASS")
        res = self._run(checker, self.file_a, debounce_seconds=3600)
        self.assertEqual(res.get("status"), "DEBOUNCED",
                         "precondition: this run is suppressed by the cooldown")
        self.assertEqual(checker.dispatches, 0, "precondition: nothing ran")
        self.assertTrue(self._sentinel(self.file_a).exists(),
                        "a debounced file was still DISPATCHED — the sentinel "
                        "must be written before the cooldown early return")

    def test_a_resume_NOOPed_dispatch_still_leaves_a_sentinel(self):
        """ORDERING ASSERTION 2 — the sentinel precedes the NOOP early return.

        Red if the touch is placed after the empty-range guard: a file whose
        budget is already spent returns NOOP before the touch, so the one case
        where a reader most needs to know a dispatch happened is the one case
        that records nothing."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        for n in (1, 2, 3):
            (base / f"{key}_R{n}.md").write_text("---\nverdict: DIRTY\n---\n")

        checker = _CountingChecker("PASS")
        res = self._run(checker, self.file_a, max_rounds=3)
        self.assertEqual(res.get("status"), "NOOP",
                         "precondition: the budget is already spent")
        self.assertEqual(checker.dispatches, 0, "precondition: nothing ran")
        self.assertTrue(self._sentinel(self.file_a).exists(),
                        "a resume-NOOP'd file was still DISPATCHED — the "
                        "sentinel must be written before the NOOP early return")

    def test_no_sentinel_for_a_non_research_kind(self):
        """The other four kinds are byte-for-byte unchanged (AD11 is research-
        kind only, exactly as the keying is)."""
        draft = self._draft("delta-20260101010101_THOUGHT.md")
        self._run(_CountingChecker("PASS"), draft, kind="thought", cycle_id=None)
        base = self.state_dir / self.PROJ / self.TOPIC / "thought"
        self.assertEqual(list(base.glob(".dispatched-*")), [],
                         "no dispatch sentinel is written for a non-research kind")

    def test_the_sentinel_stays_a_flat_file_in_the_cycle_directory(self):
        """Same rail S3 pins for the cooldown sentinel: the NAME is new, the
        directory layout is not."""
        self._run(_CountingChecker("PASS"), self.file_a)
        s = self._sentinel(self.file_a)
        self.assertTrue(s.is_file())
        self.assertEqual(s.parent, self._research_base())


class UncheckedVerdictClassTests(_PerFileBase):
    """S4 — a key with a sentinel and no marker of its own is UNCHECKED.

    UNCHECKED is a distinct verdict class from FAILED: the file was dispatched
    and produced no verdict, which is neither "passed" nor "failed a check".
    """

    def test_sentinel_without_a_marker_renders_UNCHECKED_and_is_unresolved(self):
        """AD22(d), C11. Red before S4: the rollup groups markers only, so a
        directory holding just a sentinel returns zero rows and `all_resolved`
        True — a green gate over a file nobody checked."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f".dispatched-{key}").touch()

        res = eng.research_rollup(base)
        rows = res["rows"]
        self.assertEqual(len(rows), 1, f"one row for the dispatched file: {rows}")
        self.assertEqual(rows[0]["key"], key, "the row names the FILE")
        self.assertEqual(rows[0]["verdict"], "UNCHECKED")
        self.assertFalse(rows[0]["resolved"])
        self.assertFalse(res["all_resolved"],
                         "a dispatched-but-unchecked file blocks")

    def test_a_sentinel_never_satisfies_the_gate(self):
        """Sentinel-is-not-a-verdict rail. Even alongside a PASSing sibling, the
        unchecked file keeps the cycle unresolved."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        (base / f"{key_a}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / f".dispatched-{key_b}").touch()

        res = eng.research_rollup(base)
        self.assertFalse(res["all_resolved"])
        keys = {r["key"]: r for r in res["rows"]}
        self.assertTrue(keys[key_a]["resolved"], "the checked file resolves")
        self.assertEqual(keys[key_b]["verdict"], "UNCHECKED")

    def test_a_sentinel_beside_its_own_marker_adds_no_row(self):
        """The union is by KEY, not a concatenation. A file that dispatched AND
        produced a marker is reported once, by its marker."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f"{key}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / f".dispatched-{key}").touch()

        res = eng.research_rollup(base)
        self.assertEqual(len(res["rows"]), 1, res["rows"])
        self.assertEqual(res["rows"][0]["verdict"], "PASS")
        self.assertTrue(res["all_resolved"])

    def test_an_unchecked_row_is_distinct_from_a_failed_one(self):
        """C12 — the operator can tell "never produced a verdict" apart from
        "produced a failing verdict"; collapsing them would hide which files
        still need running."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        (base / f"{key_a}_R1.md").write_text("---\nverdict: ESCALATE\n---\n")
        (base / f".dispatched-{key_b}").touch()

        rows = {r["key"]: r for r in eng.research_rollup(base)["rows"]}
        self.assertEqual(rows[key_a]["verdict"], "ESCALATE")
        self.assertEqual(rows[key_b]["verdict"], "UNCHECKED")
        self.assertNotEqual(rows[key_a]["verdict"], rows[key_b]["verdict"])

    def test_no_marker_and_no_sentinel_stays_no_check_owed(self):
        """The no-false-block rail, preserved as Lever B narrows. An empty cycle
        directory is the CV / Internal-KB case and must resolve clean."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        res = eng.research_rollup(base)
        self.assertEqual(res["rows"], [])
        self.assertTrue(res["all_resolved"])

    def test_a_sentinel_does_not_enter_the_omtm_denominator(self):
        """The sentinel is gate-visible only (UX6/UX7 honesty boundary). It is
        not a first-round marker and must never be counted as a data point.

        Asks the PRODUCTION helper for the name rather than spelling it out. An
        earlier version of this test hard-coded `.dispatched-{key}`, which made
        it assert about a literal in the test file instead of about the name the
        code actually writes — mutating the helper's name left it green.

        Reads through `_first_round_markers` (plural). S6 replaced the singular
        `_first_round_marker`, which could only ever describe ONE group per
        cycle; the property asserted here is unchanged — a sentinel yields no
        first-round entry at all."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        eng._dispatch_sentinel_path(base, key).touch()
        self.assertEqual(eng._first_round_markers(base), [],
                         "a sentinel is not a first-round marker")

    def test_the_sentinel_name_cannot_be_read_as_a_marker(self):
        """Disjointness, the S4 half: the sentinel must not match either marker
        regex, or it would be parsed as somebody's round.

        Same correction as above — the name under test comes from the helper,
        and the sentinel is really written so the assertion runs against the two
        globs the READERS actually use, not just against the regexes."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        sentinel = eng._dispatch_sentinel_path(base, key)
        sentinel.touch()

        self.assertIsNone(eng._KEYED_MARKER_RE.match(sentinel.name))
        self.assertIsNone(eng._LEGACY_MARKER_RE.match(sentinel.name))
        for pattern in ("*_R*.md", "R*.md"):
            self.assertNotIn(
                sentinel, list(base.glob(pattern)),
                f"a reader globbing {pattern!r} must not see the sentinel")


class ResidualR1DocumentedTests(_PerFileBase):
    """R-1 is recorded where a reader of the shipped code will find it."""

    def test_module_docstring_records_residual_R1(self):
        doc = eng.__doc__ or ""
        self.assertIn("R-1", doc, "the residual is NAMED, not merely implied")
        # Case-insensitive on purpose: the assertion is that the boundary is
        # STATED, not that it is spelled in one particular case.
        low = doc.lower()
        for token in ("never dispatched", "claude_code_remote"):
            self.assertIn(token, low,
                          f"R-1's boundary must state {token!r} explicitly")


class UncheckedGateRenderingTests(_CloseGateBase):
    """S4's reader half, through the REAL bash gate.

    Reuses the isolated-HOME harness. Note what that harness gives for free and
    why it matters here: it runs with NO manifest, so
    `R4_IN_SEQ` is false. Every block below therefore happens on sentinel
    evidence alone — which is precisely UX2's claim that missing-marker feedback
    is sentinel-driven and NOT Lever-B-driven.
    """

    def _sentinel(self, key):
        (self.gate_base / f".dispatched-{key}").touch()

    def test_a_dispatched_file_with_no_marker_BLOCKS_with_no_manifest(self):
        """C11 / C12 / C13 — the headline. Red before S4 on two counts: the
        gate's own evidence probe looks only for markers, and the rollup would
        report nothing even if it were reached."""
        key = eng._research_file_key(self.file_a.name)
        self._sentinel(key)

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2,
                         f"a dispatched-but-unchecked file must block; "
                         f"stderr={proc.stderr!r}")
        self.assertIn(key, proc.stderr, "the blocking row names the FILE")
        self.assertIn("UNCHECKED", proc.stderr)

    def test_one_files_PASS_does_not_clear_a_sibling_that_never_reported(self):
        """The masking harm in its S4 form: A passed, B was dispatched and
        vanished. A green gate here is the exact defect this item exists to
        remove."""
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(key_a, 1, "---\nverdict: PASS\n---\n")
        self._sentinel(key_b)

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn(key_b, proc.stderr)

    def test_neither_marker_nor_sentinel_still_does_not_block(self):
        """Lever B keeps its ORIGINAL narrow job. Narrowing it must not turn
        into removing it: a cycle with no evidence at all is still "no check
        owed", not "unchecked"."""
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")

    def test_the_gate_footer_states_the_detection_boundary(self):
        """UX3, delivered where the operator reads the verdict — not only in the
        module docstring. The gate's green is only trustworthy if what it does
        NOT cover is stated beside it."""
        key = eng._research_file_key(self.file_a.name)
        self._sentinel(key)
        proc = self._run_gate()
        low = proc.stderr.lower()
        self.assertIn("never dispatched", low,
                      "the footer names the undetected case in plain words")
        self.assertIn("not covered", low)


class RoundOrderingIsNumericTests(_PerFileBase):
    """S5 rail — within a key group, ordering is by round NUMBER, not by string.

    A lexical sort puts "R10" before "R2", so the reader would select round 2 as
    the latest and report a superseded verdict as the current one. The rollup
    parses the round and sorts on the int; these tests are what stop a later
    "simplification" to a string sort.
    """

    def test_round_10_beats_round_2_within_a_key_group(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f"{key}_R2.md").write_text("---\nverdict: PASS\n---\n")
        (base / f"{key}_R10.md").write_text("---\nverdict: ESCALATE\n---\n")

        rows = eng.research_rollup(base)["rows"]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["verdict"], "ESCALATE",
                         "round 10 is the latest round, not round 2")
        self.assertEqual(rows[0]["rounds"], 10)

    def test_round_10_beats_round_2_in_the_legacy_group(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        (base / "R2.md").write_text("---\nverdict: PASS\n---\n")
        (base / "R10.md").write_text("---\nverdict: ESCALATE\n---\n")

        rows = eng.research_rollup(base)["rows"]
        self.assertEqual(len(rows), 1, rows)
        self.assertTrue(rows[0]["legacy"])
        self.assertEqual(rows[0]["verdict"], "ESCALATE")
        self.assertEqual(rows[0]["rounds"], 10)


class UnparsableMarkerNameFailsClosedTests(_PerFileBase):
    """S5 / Guiding Policy 5 — a marker-shaped name whose round does not parse
    BLOCKS; it is never silently skipped.

    Before S5 both grouping loops did `if not m: continue`, so a file matching a
    marker glob but neither marker regex vanished from the rollup entirely. That
    is the fail-OPEN shape this plan exists to remove: the file is plainly a
    marker (it is sitting in the marker directory under a marker-shaped name) and
    the reader silently decided it was nothing.
    """

    def _rows_by_key(self, base):
        return {r["key"]: r for r in eng.research_rollup(base)["rows"]}

    def test_unparsable_keyed_round_blocks(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        (base / "alpha_Rabc.md").write_text("---\nverdict: PASS\n---\n")

        res = eng.research_rollup(base)
        self.assertFalse(res["all_resolved"], "an unreadable sequence position blocks")
        rows = res["rows"]
        self.assertEqual(len(rows), 1, rows)
        self.assertIsNotNone(rows[0]["error"])
        self.assertIn("alpha_Rabc.md", rows[0]["key"] or rows[0]["marker"] or "")

    def test_unparsable_legacy_round_blocks(self):
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        (base / "Rabc.md").write_text("---\nverdict: PASS\n---\n")

        res = eng.research_rollup(base)
        self.assertFalse(res["all_resolved"])
        self.assertEqual(len(res["rows"]), 1, res["rows"])
        self.assertIsNotNone(res["rows"][0]["error"])

    def test_a_well_formed_group_reports_no_unparsable_row(self):
        """The guard must not fire on names that DO parse — otherwise it would
        block every healthy directory."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f"{key}_R1.md").write_text("---\nverdict: PASS\n---\n")
        (base / "R1.md").write_text("---\nverdict: PASS\n---\n")

        res = eng.research_rollup(base)
        self.assertTrue(res["all_resolved"], res["rows"])
        self.assertEqual([r["error"] for r in res["rows"]], [None, None])

    def test_a_keyed_name_is_not_reported_unparsable_by_the_legacy_pass(self):
        """`{key}_R1.md` does not match the legacy regex, but it is not legacy —
        it is parsed by the keyed pass. A guard that looked at one pass in
        isolation would flag every keyed marker as unparsable."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        (base / f"{key}_R1.md").write_text("---\nverdict: PASS\n---\n")

        res = eng.research_rollup(base)
        self.assertTrue(res["all_resolved"], res["rows"])
        self.assertEqual(len(res["rows"]), 1)
        self.assertIsNone(res["rows"][0]["error"])


class _PipelineGateBase(_PerFileBase):
    """Harness driving the CLONE's `check-research-pipeline-gate.sh`.

    Deliberately clone-relative. The pre-existing suite at
    `tests/research_pipeline/test_s8_stop_gate.py` resolves its gate through
    `Path.home() / ".claude" / "hooks"`, so it exercises the LIVE gate no matter
    which clone it is run from — it cannot see a change made here. These tests
    point at the clone explicitly so S5's rewrite is actually under test.
    """

    GATE = HOOKS / "check-research-pipeline-gate.sh"

    def setUp(self):
        super().setUp()
        self.home = Path(self.tmpdir) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.active = Path(self.tmpdir) / "active.json"
        self.active.write_text(json.dumps(
            {self.SID: {"topic_slug": self.PROJ, "active_project": self.TOPIC}}))
        self.plan_val = Path(self.tmpdir) / "plan_val"
        self.gate_base = self.plan_val / self.PROJ / self.TOPIC / "research"
        self.gate_base.mkdir(parents=True)

    def _complete_manifest(self):
        """Advance all six checkpoints so the gate reaches its verdict phase."""
        sys.path.insert(0, str(HOOKS))
        import research_pipeline as rp
        rp.cmd_advance(self.SID, "r0_intake", {
            "research_file_path": "Thoughts/test_RESEARCH.md",
            "caller_skill": "/research", "user_approved_scope": True,
        }, state_dir=self.rp_dir)
        rp.cmd_advance(self.SID, "r1_scope", {"search_scope": "t"}, state_dir=self.rp_dir)
        rp.cmd_advance(self.SID, "r2_research", {"sources_count": 1}, state_dir=self.rp_dir)
        rp.cmd_advance(self.SID, "r3_synthesis", {"claims_count": 1}, state_dir=self.rp_dir)
        rp.cmd_advance(self.SID, "r4_factcheck", {}, state_dir=self.rp_dir)
        rp.cmd_advance(self.SID, "r5_recommend", {}, state_dir=self.rp_dir)

    def _run_gate(self, on_non_pass=None):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["RP_STATE_DIR"] = str(self.rp_dir)
        env["RP_ACTIVE_FILE"] = str(self.active)
        env["RP_PLAN_VAL_DIR"] = str(self.plan_val)
        env.pop("CLAUDE_CODE_REMOTE", None)
        if on_non_pass is None:
            env.pop("CLAUDE_RESEARCH_ON_NON_PASS", None)
        else:
            env["CLAUDE_RESEARCH_ON_NON_PASS"] = on_non_pass
        return subprocess.run(
            ["/bin/bash", str(self.GATE)],
            input=json.dumps({"session_id": self.SID, "stop_hook_active": False}),
            capture_output=True, text=True, env=env, timeout=120)

    def _marker(self, name, body):
        (self.gate_base / name).write_text(body)


class PipelineGateAdoptsTheSharedVerbTests(_PipelineGateBase):
    """S5 — the pipeline gate reads through the SAME verb as the close gate."""

    def test_a_keyed_non_pass_row_blocks_and_names_the_file(self):
        """Red before S5: the per-cycle read is `ls -1 "$DIR"/R*.md`, which
        matches no keyed name at all — so a cycle whose only marker is keyed
        looked like a cycle with NO marker."""
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md", "---\nverdict: ESCALATE\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn(key, proc.stderr, "the report names the failing FILE")
        self.assertIn("ESCALATE", proc.stderr)

    def test_one_files_pass_does_not_mask_a_siblings_escalate(self):
        """The masking harm, now at the pipeline gate. Pre-S5 this gate takes a
        single latest marker for the whole cycle."""
        self._complete_manifest()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(f"{key_a}_R2.md", "---\nverdict: PASS\nrounds: 2\n---\n")
        self._marker(f"{key_b}_R1.md", "---\nverdict: ESCALATE\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn(key_b, proc.stderr)

    def test_all_rows_passing_reports_pass_and_exits_0(self):
        self._complete_manifest()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(f"{key_a}_R1.md", "---\nverdict: PASS\nrounds: 1\n---\n")
        self._marker(f"{key_b}_R1.md", "---\nverdict: PASS\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")
        self.assertIn("RESEARCH-VERDICT: PASS", proc.stderr)

    def test_an_unchecked_sentinel_row_blocks_here_too(self):
        """S4's sentinel must be visible to BOTH readers, or the two gates
        disagree about the same directory again."""
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)
        eng._dispatch_sentinel_path(self.gate_base, key).touch()

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn("UNCHECKED", proc.stderr)
        self.assertIn(key, proc.stderr)

    def test_a_sanctioned_bypassed_row_stays_informational(self):
        """Per-row sanction resolution must survive the rewrite: a BYPASSED
        marker with a non-empty reason is informational, not a block."""
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md",
                     '---\nverdict: BYPASSED\nrounds: 1\n'
                     'bypass_reason: "all 3 checkers timed out"\n---\n')

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")
        self.assertIn("all 3 checkers timed out", proc.stderr)

    def test_an_accepted_incomplete_row_stays_informational(self):
        """"Informational" means SURFACED, not merely not-blocking.

        Asserting exit 0 alone was not enough and is recorded as a correction: a
        row that the gate silently skipped altogether also exits 0, so the
        original assertion passed even with the sanctioned branch disabled. The
        accept reason must actually reach the operator."""
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md",
                     '---\nverdict: INCOMPLETE\nrounds: 1\n'
                     'accept_reason: "operator accepted the honest incomplete"\n---\n')

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")
        self.assertIn("operator accepted the honest incomplete", proc.stderr,
                      "the accept reason is reported, not silently swallowed")
        self.assertIn(key, proc.stderr, "and it is attributed to the FILE")

    def test_an_unaccepted_incomplete_row_still_blocks(self):
        """The sanction is the accept_reason, not the verdict token."""
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md", "---\nverdict: INCOMPLETE\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")

    def test_a_legacy_row_gets_the_remediation_that_actually_applies(self):
        """A legacy row has NO owning file, so this gate's standing advice —
        "re-dispatch factcheck for the affected cycle's research file" — is
        impossible to follow for it. The row must carry the verb that does
        resolve it.

        This RUNS the gate rather than grepping the script. The earlier test
        only asserted that "adopt-legacy-markers" and "--supersede" appeared
        SOMEWHERE in each file, which was trivially true via an unrelated line
        in a different branch — so it stayed green while this branch had no
        remediation at all. Found by an independent checker."""
        self._complete_manifest()
        self._marker("R1.md", "---\nverdict: ESCALATE\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn("«unattributed legacy»", proc.stderr, "the row renders")
        self.assertIn("adopt-legacy-markers", proc.stderr,
                      "and it names the verb that resolves it")
        self.assertIn("--adopt --file", proc.stderr, "the attribute arm")
        self.assertIn("--supersede --reason", proc.stderr, "the set-aside arm")
        self.assertIn("cannot be attributed", proc.stderr,
                      "and says why re-running will not clear it")

    def test_an_unparsable_marker_name_blocks_the_pipeline_gate(self):
        """GP5 at the second gate: a marker-shaped name the verb cannot place in
        a sequence leaves a verdict undetermined, so it blocks.

        Asserting exit 2 alone was NOT enough and is recorded as a correction.
        `HAS_EVIDENCE` is true for any marker-shaped filename whether or not it
        parses, and several unrelated failures (rollup crash, absent python3)
        also exit 2 — so the weaker version would stay green even if the
        unparsable-name handling were entirely unreachable. The assertions below
        pin the actual mechanism: the row must NAME the offending file and say
        why it could not be placed."""
        self._complete_manifest()
        self._marker("alpha_Rabc.md", "---\nverdict: PASS\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn("alpha_Rabc.md", proc.stderr,
                      "the report names the offending file")
        self.assertIn("no parsable round number", proc.stderr,
                      "and says why it could not be placed in a sequence")

    def test_a_sanctioned_row_is_still_reported_when_its_marker_vanishes(self):
        """A sanctioned exit must stay auditable even if the marker cannot be
        re-read by the shell.

        The reason text comes from a SECOND, shell-side read of the marker, and
        that read can fail — the file may have been moved between the rollup and
        this loop. An earlier cut appended to the report only INSIDE that
        file-exists guard while `continue`-ing unconditionally, so the row
        vanished from the output entirely: not blocking (correct) and not
        reported (not correct). Found by an adversarial checker, not the producer.

        Reproducing it needs the rollup to report a sanctioned row whose marker
        the shell then cannot open — a TOCTOU race that is not deterministic in a
        live directory. So the ENGINE is stubbed, the same technique
        `test_empty_cycle_DOES_block_when_a_factcheck_was_expected` uses to stub
        `research_pipeline.py`. Note what this does NOT do: it does not stub the
        gate. The branch under test is the real one.
        """
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)

        stub = Path(self.tmpdir) / "stubhooks_sanctioned"
        stub.mkdir()
        shutil.copy2(self.GATE, stub / self.GATE.name)
        shutil.copy2(HOOKS / "research_pipeline.py", stub / "research_pipeline.py")
        canned = json.dumps({"status": "OK", "all_resolved": True, "rows": [
            {"key": key, "legacy": False, "rounds": 1,
             "marker": str(self.gate_base / f"{key}_R1.md"),   # never created
             "verdict": "BYPASSED", "sanctioned": True,
             "resolved": True, "error": None}]})
        (stub / "_factcheck_engine.py").write_text(
            "import sys\n"
            f"print({canned!r})\n"
            "sys.exit(0)\n")
        # The gate probes the real directory for evidence before calling the
        # verb, so give it one marker-shaped file to find.
        self._marker(f"{key}_R2.md", "---\nverdict: PASS\nrounds: 2\n---\n")

        saved, self.GATE = self.GATE, stub / self.GATE.name
        try:
            proc = self._run_gate()
        finally:
            self.GATE = saved

        self.assertEqual(proc.returncode, 0,
                         f"a sanctioned row still must not block; "
                         f"stderr={proc.stderr!r}")
        self.assertIn(key, proc.stderr,
                      "and it is still REPORTED, naming the file, rather than "
                      "silently dropped")
        self.assertIn("reason unavailable", proc.stderr,
                      "the missing reason is stated honestly, not omitted")

    def test_lever_b_no_evidence_at_all_still_blocks_when_r4_expected(self):
        """The pre-existing Lever B branch is preserved: a cycle with neither
        marker nor sentinel, where r4 IS in the sequence, still blocks."""
        self._complete_manifest()
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn("fact-check not run", proc.stderr)


class BothGatesAgreeTests(_PipelineGateBase):
    """C14 — the pipeline gate and the close gate report the SAME per-file
    grouping for the SAME directory. This is the claim the whole slice exists
    to deliver, so it is asserted directly rather than inferred."""

    CLOSE_GATE = HOOKS / "check-research-gate.sh"

    def _run_close_gate(self):
        """The close gate resolves _active.json under $HOME, so mirror the
        fixture there."""
        home_active = self.home / ".claude" / "state" / "pre_plan_gates"
        home_active.mkdir(parents=True, exist_ok=True)
        (home_active / "_active.json").write_text(self.active.read_text())
        dest = self.home / ".claude" / "state" / "plan_validation" / self.PROJ / self.TOPIC
        dest.mkdir(parents=True, exist_ok=True)
        if (dest / "research").exists():
            shutil.rmtree(dest / "research")
        shutil.copytree(self.gate_base, dest / "research")

        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env["RP_STATE_DIR"] = str(Path(self.tmpdir) / "no-manifest-here")
        env.pop("CLAUDE_CODE_REMOTE", None)
        return subprocess.run(
            ["/bin/bash", str(self.CLOSE_GATE)],
            input=json.dumps({"session_id": self.SID, "stop_hook_active": False}),
            capture_output=True, text=True, env=env, timeout=120)

    def test_both_gates_name_the_same_unresolved_files(self):
        self._complete_manifest()
        key_pass = eng._research_file_key(self.file_a.name)
        key_esc = eng._research_file_key(self.file_b.name)
        key_unchecked = eng._research_file_key(
            self._draft("charlie-20260101010101_RESEARCH.md").name)
        self._marker(f"{key_pass}_R1.md", "---\nverdict: PASS\nrounds: 1\n---\n")
        self._marker(f"{key_esc}_R1.md", "---\nverdict: ESCALATE\nrounds: 1\n---\n")
        eng._dispatch_sentinel_path(self.gate_base, key_unchecked).touch()

        pipeline = self._run_gate()
        close = self._run_close_gate()

        self.assertEqual(pipeline.returncode, 2, f"pipeline stderr={pipeline.stderr!r}")
        self.assertEqual(close.returncode, 2, f"close stderr={close.stderr!r}")
        for key in (key_esc, key_unchecked):
            self.assertIn(key, pipeline.stderr, "pipeline gate names it")
            self.assertIn(key, close.stderr, "close gate names it")
        # And BOTH stay silent about the file that genuinely passed.
        self.assertNotIn(key_pass, pipeline.stderr)
        self.assertNotIn(key_pass, close.stderr)


class NoManifestFallbackIsKeyGroupAwareTests(_PipelineGateBase):
    """S5 — the no-manifest fallback keeps its INFORMATIONAL role (never
    blocking) while learning to see keyed markers."""

    def test_fallback_surfaces_a_keyed_marker(self):
        """Red before S5: the fallback's `find -name "R*.md"` cannot match
        `{key}_R1.md`, so an Internal-KB session's verdict was invisible."""
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md", "---\nverdict: PASS\nrounds: 1\n---\n")

        proc = self._run_gate()          # no manifest advanced → fallback path
        self.assertEqual(proc.returncode, 0, "the fallback NEVER blocks")
        self.assertIn("RESEARCH-VERDICT:", proc.stderr)
        self.assertIn("PASS", proc.stderr)

    def test_fallback_is_informational_even_on_a_non_pass_verdict(self):
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md", "---\nverdict: ESCALATE\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0,
                         "informational role preserved — the fallback must not "
                         "become a blocking path")
        self.assertIn("ESCALATE", proc.stderr)

    def test_fallback_still_surfaces_a_legacy_marker(self):
        """The pre-keying corpus must keep working through this path."""
        self._marker("R1.md", "---\nverdict: PASS\nrounds: 1\n---\n")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0)
        self.assertIn("RESEARCH-VERDICT:", proc.stderr)

    def test_fallback_surfaces_a_dispatched_but_unchecked_file(self):
        """The evidence probe on THIS path must see sentinels too.

        A first cut listed only the two marker patterns here, which made the
        no-manifest fallback the single evidence probe in the system blind to
        `.dispatched-*` — exactly backwards, since AD11/UX2 introduce the
        sentinel BECAUSE it is registration-independent, i.e. precisely to cover
        the case where no manifest exists. A dispatched-but-unchecked file in an
        Internal-KB session produced no match at all, so the rollup was never
        invoked and the operator was told nothing.

        Found by a cross-slice check reading S4, S5 and S6 together; neither
        slice's own verification could see it."""
        key = eng._research_file_key(self.file_a.name)
        eng._dispatch_sentinel_path(self.gate_base, key).touch()

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0,
                         f"still informational, never blocking; "
                         f"stderr={proc.stderr!r}")
        self.assertIn("UNCHECKED", proc.stderr,
                      "the dispatched-but-unchecked file is surfaced")
        self.assertIn(key, proc.stderr, "and it is named")

    def test_fallback_stays_silent_for_an_empty_directory(self):
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("RESEARCH-VERDICT:", proc.stderr)

    def test_fallback_stays_silent_out_of_the_mtime_window(self):
        """The 6-hour window is a misattribution guard against a PRIOR session's
        markers; widening the name match must not widen the window."""
        key = eng._research_file_key(self.file_a.name)
        marker = self.gate_base / f"{key}_R1.md"
        marker.write_text("---\nverdict: PASS\nrounds: 1\n---\n")
        old = time.time() - (7 * 3600)
        os.utime(marker, (old, old))

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("RESEARCH-VERDICT:", proc.stderr)


class PipelineGateStatesTheDetectionBoundaryTests(_PipelineGateBase):
    """S4 carry-forward, closed here.

    S4 put UX3's detection boundary in the close gate's BLOCK footer, which an
    operator only sees when something blocks. UX3's rationale is that the
    boundary must be stated where the operator reads the verdict — and a GREEN
    verdict is read here, in this gate's report. Without this the boundary is
    absent from every passing run.
    """

    def test_the_boundary_is_stated_on_the_passing_report(self):
        self._complete_manifest()
        key = eng._research_file_key(self.file_a.name)
        self._marker(f"{key}_R1.md", "---\nverdict: PASS\nrounds: 1\n---\n")

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")
        low = proc.stderr.lower()
        self.assertIn("never dispatched", low)
        self.assertIn("not covered", low)


class EvidenceProbeConsistencyTests(unittest.TestCase):
    """Every probe that decides what counts as EVIDENCE names the same patterns.

    There are three, across two shell scripts: the close gate's per-cycle probe,
    the pipeline gate's per-cycle probe, and the pipeline gate's no-manifest
    fallback probe. `research_rollup` performs the same union internally in
    Python.

    This is pinned because it has already broken once. The no-manifest probe
    listed only the two marker patterns and omitted `.dispatched-*`, making it
    the single evidence probe in the system blind to dispatch sentinels — in
    exactly the sessions (no manifest at all) that AD11/UX2 introduce the
    sentinel to cover. Every per-slice check passed while that was true; only
    reading S4, S5 and S6 together surfaced it.

    An earlier draft claimed such a consistency check existed when it lived only
    in a throwaway verification script. An independent checker caught that, and
    the honest fix is this test rather than a softer claim — a rail with no
    carrier is the defect class this plan exists to remove.
    """

    EXPECTED = {"*_R*.md", "R*.md", ".dispatched-*"}
    CLOSE_GATE = HOOKS / "check-research-gate.sh"
    PIPELINE_GATE = HOOKS / "check-research-pipeline-gate.sh"

    @staticmethod
    def _for_loop_patterns(text):
        """Pattern sets from every `for _M in "$DIR"/... ; do` evidence probe."""
        out = []
        for line in re.findall(r'^\s*for _M in (.+?); do\s*$', text, re.MULTILINE):
            out.append({p.split("/")[-1] for p in re.findall(r'"\$DIR"/\S+', line)})
        return out

    @staticmethod
    def _find_name_patterns(text):
        """Pattern sets from every `find ... \\( -name A -o -name B \\)` probe.

        Matches the parenthesised group first, then pulls the `-name` operands
        out of it. Doing it in one pass was tried and was subtly wrong — an
        `-o?` alternation consumed the hyphen of `-name` and the whole probe
        silently failed to match, which reads as "no probe found" rather than as
        a broken pattern."""
        out = []
        for block in re.findall(r'\\\((.*?)\\\)', text, re.DOTALL):
            names = re.findall(r'-name\s+"([^"]+)"', block)
            if names:
                out.append(set(names))
        return out

    def test_the_close_gate_probe_names_the_expected_patterns(self):
        probes = self._for_loop_patterns(self.CLOSE_GATE.read_text())
        self.assertEqual(len(probes), 1, f"expected exactly one probe: {probes}")
        self.assertEqual(probes[0], self.EXPECTED)

    def test_the_pipeline_per_cycle_probe_names_the_expected_patterns(self):
        probes = self._for_loop_patterns(self.PIPELINE_GATE.read_text())
        self.assertEqual(len(probes), 1, f"expected exactly one probe: {probes}")
        self.assertEqual(probes[0], self.EXPECTED)

    def test_the_no_manifest_fallback_probe_names_the_expected_patterns(self):
        probes = self._find_name_patterns(self.PIPELINE_GATE.read_text())
        self.assertEqual(len(probes), 1, f"expected exactly one probe: {probes}")
        self.assertEqual(
            probes[0], self.EXPECTED,
            "the no-manifest path is the one that runs when NO manifest exists — "
            "the case the dispatch sentinel exists to cover")

    def test_all_three_probes_agree_with_each_other(self):
        """The invariant itself, stated once. Two probes that drift apart mean
        two readers disagreeing about the same directory — the harm class this
        whole item removes."""
        found = (self._for_loop_patterns(self.CLOSE_GATE.read_text())
                 + self._for_loop_patterns(self.PIPELINE_GATE.read_text())
                 + self._find_name_patterns(self.PIPELINE_GATE.read_text()))
        self.assertEqual(len(found), 3, f"expected three evidence probes: {found}")
        self.assertEqual(len({frozenset(s) for s in found}), 1,
                         f"all three probes must name the same patterns: {found}")

    def test_the_rollup_reads_the_same_three_shapes(self):
        """And the Python union matches the shell probes, so the gates cannot
        find evidence the verb then fails to group."""
        src = (HOOKS / "_factcheck_engine.py").read_text()
        start = src.index("def research_rollup(")
        body = src[start:src.index("\ndef ", start + 10)]
        for pattern in ('d.glob("*_R*.md")', 'd.glob("R*.md")',
                        'd.glob(".dispatched-*")'):
            self.assertIn(pattern, body,
                          f"research_rollup must read {pattern}")


class OmtmReaderIsKeyedTests(_PerFileBase):
    """S6 — the metric reader groups per file, so keying COMPLETES the
    denominator instead of emptying it.

    This slice is mandatory, not optional. `_first_round_marker` globbed
    `R*.md` and matched `R(\\d+)\\.md$`, neither of which matches `{key}_R1.md`,
    and `compute_omtm_rate` took exactly one marker per cycle directory. Left
    alone, per-file keying would REMOVE every keyed marker from the denominator
    — the exact inverse of the locked Metrics claim that per-file gating makes
    the OMTM more complete.
    """

    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 7, 9, tzinfo=timezone.utc)
        self.recent = (self.now - timedelta(days=2)).isoformat()

    def _omtm_base(self):
        """A plan_validation-shaped base: <base>/<proj>/<topic>/research/."""
        base = Path(self.tmpdir) / "omtm"
        (base / "p" / "t" / "research").mkdir(parents=True, exist_ok=True)
        return base

    def _research(self, base):
        return base / "p" / "t" / "research"

    def _marker(self, directory, name, verdict="PASS", checked_at=None,
                schema_version=3):
        (directory / name).write_text(
            "---\n"
            f"schema_version: {schema_version}\n"
            f"verdict: {verdict}\n"
            "rounds: 1\n"
            f"checked_at: {checked_at or self.recent}\n"
            "---\n")

    def _rate(self, base):
        return eng.compute_omtm_rate(base, window_days=30, now=self.now)

    # -- the headline (C15) --------------------------------------------------

    def test_two_keyed_first_round_markers_yield_eligible_2(self):
        """AD22(e). Red before S6: neither keyed name matches the reader's glob
        or regex, so the denominator is 0 and the rate is None — per-file keying
        would have made the metric BLIND rather than complete."""
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(rd, f"{key_a}_R1.md", "PASS")
        self._marker(rd, f"{key_b}_R1.md", "DIRTY")

        r = self._rate(base)
        self.assertEqual(r["eligible_first_round_markers"], 2,
                         "both files are data points, not zero")
        self.assertEqual(r["genuine_pass"], 1)
        self.assertEqual(r["breakdown"]["dirty"], 1)
        self.assertEqual(r["rate"], 0.5)

    def test_first_round_is_per_GROUP_not_per_cycle(self):
        """A cycle holding two files must contribute two first-round data
        points, and each file's FIRST round is its own — a file that converged
        on round 2 is not a genuine first-round pass, and that must not be
        decided by a sibling's round 1."""
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(rd, f"{key_a}_R1.md", "DIRTY")
        self._marker(rd, f"{key_a}_R2.md", "PASS")     # converged later
        self._marker(rd, f"{key_b}_R1.md", "PASS")     # genuine first-round

        r = self._rate(base)
        self.assertEqual(r["eligible_first_round_markers"], 2)
        self.assertEqual(r["genuine_pass"], 1, "only file B passed on round 1")
        self.assertEqual(r["breakdown"]["dirty"], 1)

    # -- the two exclusion rails --------------------------------------------

    def test_a_dispatch_sentinel_does_not_enter_the_denominator(self):
        """UX6: counting a sentinel would fabricate a verdict-less data point
        and corrupt the rate. A dispatched-but-unchecked file is surfaced at the
        GATE, never in the METRIC.

        The last two assertions pin the MECHANISM, not just the outcome. A
        sentinel admitted by the reader and then dropped downstream by the
        no-timestamp filter would still leave `eligible` at 1 — so an
        outcome-only test stays green even when the reader is wrong, and the
        corpus counts are quietly polluted with a phantom exclusion. Surfaced by
        a mutation that admitted sentinels and was not caught here."""
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(rd, f"{key_a}_R1.md", "PASS")
        eng._dispatch_sentinel_path(rd, key_b).touch()

        r = self._rate(base)
        self.assertEqual(r["eligible_first_round_markers"], 1,
                         "the sentinel is not a data point")
        self.assertEqual(r["rate"], 1.0)
        self.assertEqual(r["total_first_round_markers_seen"], 1,
                         "the sentinel is never even SEEN as a first-round "
                         "marker — it is excluded by the reader, not filtered "
                         "out afterwards")
        self.assertEqual(r["excluded"]["no_timestamp"], 0,
                         "and it does not inflate the exclusion counters with a "
                         "phantom entry")

    def test_a_superseded_backup_leaves_the_denominator(self):
        """A `.bak-<ts>` marker moved aside by --force or by S7 is retired: it
        no longer matches any reader glob, so it stops being counted."""
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(rd, f"{key_a}_R1.md", "PASS")
        moved = eng._move_aside(rd / f"{key_a}_R1.md")
        self.assertIsNotNone(moved)

        r = self._rate(base)
        self.assertEqual(r["eligible_first_round_markers"], 0,
                         "a superseded marker leaves the denominator")
        self.assertIsNone(r["rate"])

    # -- the legacy corpus keeps working ------------------------------------

    def test_a_legacy_unkeyed_group_still_counts_as_one(self):
        """The pre-keying corpus must not change value. A legacy cycle is one
        unattributed group and contributes exactly one data point, as before."""
        base = self._omtm_base()
        rd = self._research(base)
        self._marker(rd, "R1.md", "PASS")
        self._marker(rd, "R2.md", "DIRTY")

        r = self._rate(base)
        self.assertEqual(r["eligible_first_round_markers"], 1)
        self.assertEqual(r["genuine_pass"], 1, "R1 is the first round, not R2")

    def test_keyed_and_legacy_groups_coexist_in_one_cycle(self):
        """UX7: the rolling window is a MIX until legacy drains. Two keyed
        groups plus one legacy group is three data points, not one."""
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(rd, f"{key_a}_R1.md", "PASS")
        self._marker(rd, f"{key_b}_R1.md", "PASS")
        self._marker(rd, "R1.md", "ESCALATE")

        r = self._rate(base)
        self.assertEqual(r["eligible_first_round_markers"], 3)
        self.assertEqual(r["genuine_pass"], 2)
        self.assertEqual(r["breakdown"]["escalate"], 1)

    # -- visibility, so a grown denominator is legible -----------------------

    def test_the_per_group_count_is_reported(self):
        """UX6: an operator must be able to see the denominator grew because
        files became VISIBLE, not because behaviour changed."""
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._marker(rd, f"{key_a}_R1.md", "PASS")
        self._marker(rd, f"{key_b}_R1.md", "PASS")
        self._marker(rd, "R1.md", "PASS")

        r = self._rate(base)
        self.assertIn("first_round_groups", r)
        self.assertEqual(r["first_round_groups"]["keyed"], 2)
        self.assertEqual(r["first_round_groups"]["legacy"], 1)

    def test_the_honesty_boundary_ships_with_the_metric(self):
        """UX7: state the boundary WHEREVER the OMTM is reported. An overstated
        completeness claim would reintroduce at the measurement layer the same
        trust failure the unchecked file introduced at the gate layer."""
        r = self._rate(self._omtm_base())
        boundary = r.get("honesty_boundary")
        self.assertIsInstance(boundary, dict, "the boundary ships with the metric")
        text = json.dumps(boundary).lower()
        for token in ("never dispatched", "dispatched", "legacy"):
            self.assertIn(token, text,
                          f"the boundary must state the {token!r} case")

    # -- the reader primitive ------------------------------------------------

    def test_the_reader_returns_one_first_marker_per_group(self):
        base = self._omtm_base()
        rd = self._research(base)
        key_a = eng._research_file_key(self.file_a.name)
        self._marker(rd, f"{key_a}_R1.md", "PASS")
        self._marker(rd, f"{key_a}_R2.md", "PASS")
        self._marker(rd, "R3.md", "PASS")

        groups = eng._first_round_markers(rd)
        self.assertEqual(len(groups), 2, groups)
        by_key = {k: p for k, p in groups}
        self.assertEqual(by_key[key_a].name, f"{key_a}_R1.md",
                         "the LOWEST round in the key group")
        self.assertEqual(by_key[None].name, "R3.md",
                         "the legacy group's own lowest round")


class AdoptLegacyMarkersTests(_PerFileBase):
    """S7 — the bounded, explicit, reversible legacy exit.

    Without this verb the whole change converts a masking bug into a deadlock:
    a cycle holding an unkeyed non-PASS group has no way to resolve, because no
    code may attribute those markers to a file (it cannot know which file they
    belong to — residual R-2) and nothing may delete them.

    Every rail here exists because the design rejected two simpler designs that
    could destroy or silently retire another file's verdict. So: preview by
    default, explicit apply, an operator-named target, a required reason, pure
    renames, and refusal on collision with no partial rename.
    """

    def _seed_legacy(self, verdicts=("DIRTY", "DIRTY", "ESCALATE")):
        """The live Group IV shape: an unkeyed group whose newest round failed."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        written = []
        for n, v in enumerate(verdicts, start=1):
            p = base / f"R{n}.md"
            p.write_text(f"---\nschema_version: 3\nverdict: {v}\nrounds: {n}\n---\n"
                         f"body of round {n}\n")
            written.append(p)
        return base, written

    # -- precondition: the state this verb exists to resolve -----------------

    def test_a_legacy_non_pass_group_is_unresolved_before_adoption(self):
        base, _ = self._seed_legacy()
        res = eng.research_rollup(base)
        self.assertFalse(res["all_resolved"])
        self.assertTrue(res["rows"][0]["legacy"])
        self.assertEqual(res["rows"][0]["verdict"], "ESCALATE")

    # -- the attribute arm ---------------------------------------------------

    def test_adopt_renames_the_group_into_the_key_byte_identically(self):
        """The attribute arm's own non-deletion assertion: the markers must be
        the SAME BYTES afterwards, only under a new name."""
        base, written = self._seed_legacy()
        before = {p.name: p.read_bytes() for p in written}
        key = eng._research_file_key(self.file_a.name)

        res = eng.adopt_legacy_markers(base, adopt_file=str(self.file_a), apply=True)
        self.assertEqual(res["status"], "OK", res)
        self.assertEqual(res["arm"], "adopt")

        for n in (1, 2, 3):
            old = base / f"R{n}.md"
            new = base / f"{key}_R{n}.md"
            self.assertFalse(old.exists(), "the legacy name is freed")
            self.assertTrue(new.exists(), "and the marker is under the key")
            self.assertEqual(new.read_bytes(), before[f"R{n}.md"],
                             "byte-identical — a rename, never a rewrite")

    def test_adopt_then_force_rerun_reaches_pass(self):
        """AD22(h): the Group IV re-run is actually performable, not merely
        designed for. Adoption puts the legacy rounds into the file's OWN
        sequence, `--force` clears that sequence, and the re-run produces this
        file's own first-round PASS.

        What this asserts is the FACT-CHECK verdict, not the cycle's terminal
        status, and that distinction is deliberate rather than a weakened bar.
        The terminal status in this fixture is INCOMPLETE because
        `_run_coverage_axis_gate` appends a `coverage_provenance: UNKNOWN`
        downgrade for a draft with no cited URLs — verified by running the
        identical force+re-run with NO legacy group and no adoption at all,
        which yields exactly the same INCOMPLETE. It is a property of the
        fixture, independent of this slice, and that gate is one this plan is
        forbidden to touch (zero edits, asserted by ZeroEditGateTests).
        Asserting on it would make this test fail for a reason S7 does not own.
        """
        base, _ = self._seed_legacy()
        eng.adopt_legacy_markers(base, adopt_file=str(self.file_a), apply=True)

        checker = _CountingChecker("PASS")
        self._run(checker, self.file_a, force=True, max_rounds=3)
        self.assertGreater(checker.dispatches, 0, "the re-run really ran")

        key = eng._research_file_key(self.file_a.name)
        first = eng._parse_marker_frontmatter(base / f"{key}_R1.md") or {}
        self.assertEqual(first.get("verdict"), "PASS",
                         "the re-run reached PASS in the file's OWN first round")
        self.assertEqual(list(base.glob("R[0-9]*.md")), [],
                         "and no unattributed legacy group remains")

    def test_the_verb_refuses_when_no_target_is_named(self):
        """Refuse rather than guess: the engine cannot know which file a legacy
        group belongs to (residual R-2), so attribution rests on an explicit
        operator assertion (residual R-3).

        Renamed from `test_adopt_refuses_without_a_named_file`, which overstated
        what it checks — a mutation showed the refusal here comes from the
        "no target named" guard, not from anything specific to the adopt arm.
        The CLI-level `--adopt`-without-`--file` case is covered below, and it
        collapses onto this same guard by construction."""
        base, _ = self._seed_legacy()
        res = eng.adopt_legacy_markers(base, apply=True)
        self.assertEqual(res["status"], "REFUSED")
        self.assertIn("nothing to do", (res.get("error") or "").lower())
        self.assertTrue((base / "R1.md").exists(), "nothing was touched")

    def test_the_cli_refuses_a_selected_arm_missing_its_argument(self):
        """An arm SELECTED but not given its argument is its own mistake.

        The first version of this test asserted only REFUSED + exit 3 for
        `--adopt` with no `--file`. That could not fail if the implementation
        were wrong: `_opt("--file")` returns None whether or not `--adopt` is
        present, so the run collapsed into the generic "nothing to do" refusal
        and a mutation deleting the arm wiring left the test green. An
        independent checker caught it — the same defect class this slice had
        already fixed twice, recurring in the test added to fix it.

        The CLI now refuses each case with its OWN message, so there is a
        distinct behaviour to pin, and an operator who simply forgot an argument
        is told which one."""
        base, _ = self._seed_legacy()
        engine = str(HOOKS / "_factcheck_engine.py")

        for flags, expected in (
            (["--adopt", "--apply"], "--adopt requires --file"),
            (["--supersede", "--apply"], "--supersede requires --reason"),
        ):
            proc = subprocess.run(
                ["/usr/bin/python3", engine, "adopt-legacy-markers", str(base)] + flags,
                capture_output=True, text=True, timeout=120)
            self.assertEqual(proc.returncode, 3, proc.stdout)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["status"], "REFUSED")
            self.assertIn(expected, payload["error"],
                          f"{flags} must name the argument it is missing")
            self.assertTrue((base / "R1.md").exists(), "nothing was touched")

    def test_adopt_refuses_on_collision_with_no_partial_rename(self):
        """A collision must be detected across the WHOLE group before any rename
        lands — a half-applied adoption would leave a gap in the sequence, and
        round sizing is a count, so the next write would land on a survivor."""
        base, _ = self._seed_legacy()
        key = eng._research_file_key(self.file_a.name)
        (base / f"{key}_R2.md").write_text("---\nverdict: PASS\n---\nexisting\n")

        res = eng.adopt_legacy_markers(base, adopt_file=str(self.file_a), apply=True)
        self.assertEqual(res["status"], "REFUSED", res)
        self.assertIn("collision", (res.get("error") or "").lower())
        for n in (1, 2, 3):
            self.assertTrue((base / f"R{n}.md").exists(),
                            f"R{n}.md must be untouched — no partial rename")
        self.assertEqual((base / f"{key}_R2.md").read_text(),
                         "---\nverdict: PASS\n---\nexisting\n",
                         "and the existing keyed marker is not overwritten")

    # -- the set-aside arm ---------------------------------------------------

    def test_supersede_moves_aside_and_leaves_the_bytes_readable(self):
        base, written = self._seed_legacy()
        before = {p.name: p.read_bytes() for p in written}

        res = eng.adopt_legacy_markers(base, supersede_reason="unattributable",
                                       apply=True)
        self.assertEqual(res["status"], "OK", res)
        self.assertEqual(res["arm"], "supersede")

        for n in (1, 2, 3):
            self.assertFalse((base / f"R{n}.md").exists())
            baks = list(base.glob(f"R{n}.md.bak-*"))
            self.assertEqual(len(baks), 1, f"R{n}.md moved aside once")
            self.assertEqual(baks[0].read_bytes(), before[f"R{n}.md"],
                             "the bytes are still readable on disk")

    def test_supersede_drops_the_group_from_both_shell_readers(self):
        """The point of the arm: after set-aside the cycle resolves, because a
        `.bak-<ts>` name matches neither reader glob."""
        base, _ = self._seed_legacy()
        eng.adopt_legacy_markers(base, supersede_reason="unattributable", apply=True)

        res = eng.research_rollup(base)
        self.assertEqual([r for r in res["rows"] if r["legacy"]], [],
                         "no legacy row remains")
        self.assertTrue(res["all_resolved"])
        self.assertEqual(eng._first_round_markers(base), [],
                         "and it leaves the metric reader too")

    def test_supersede_refuses_without_a_nonempty_reason(self):
        base, _ = self._seed_legacy()
        for reason in (None, "", "   "):
            res = eng.adopt_legacy_markers(base, supersede_reason=reason, apply=True)
            self.assertEqual(res["status"], "REFUSED", f"reason={reason!r}")
            self.assertIn("reason", (res.get("error") or "").lower())
        self.assertTrue((base / "R1.md").exists(), "nothing was touched")

    def test_the_supersede_note_is_not_marker_shaped(self):
        """The note records WHY, but must not be read as somebody's round."""
        base, _ = self._seed_legacy()
        res = eng.adopt_legacy_markers(base, supersede_reason="checkers all timed out",
                                       apply=True)
        note = Path(res["note"])
        self.assertTrue(note.exists())
        self.assertIn("checkers all timed out", note.read_text())
        self.assertIsNone(eng._KEYED_MARKER_RE.match(note.name))
        self.assertIsNone(eng._LEGACY_MARKER_RE.match(note.name))
        self.assertNotIn(note, list(base.glob("R*.md")) + list(base.glob("*_R*.md")))

    # -- S4's guard rail: a stale sentinel resolves through this arm ---------

    def test_supersede_sets_aside_a_stale_dispatch_sentinel(self):
        """S4's guard rail, and a promise already made in shipped gate text.

        An UNCHECKED row's ordinary remedy is "re-run that file" — impossible
        once the file is gone, so without this the row blocks forever. Both
        gates tell the operator to set the dispatch record aside with "the
        recorded-reason operator verb"; this is that verb, and the promise is
        unmet unless the arm reaches sentinels as well as legacy markers."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        sentinel = eng._dispatch_sentinel_path(base, key)
        sentinel.touch()
        self.assertFalse(eng.research_rollup(base)["all_resolved"],
                         "precondition: the sentinel blocks")
        # The arm is scoped to a file that can no longer be re-run, so the
        # fixture has to be genuinely stale — see the sibling test asserting it
        # REFUSES while the file still exists.
        self.file_a.unlink()

        res = eng.adopt_legacy_markers(base, sentinel_file=str(self.file_a),
                                       supersede_reason="the research file was deleted",
                                       apply=True)
        self.assertEqual(res["status"], "OK", res)
        self.assertFalse(sentinel.exists(), "the sentinel name is freed")
        self.assertEqual(len(list(base.glob(f"{sentinel.name}.bak-*"))), 1,
                         "moved aside, not deleted")
        self.assertTrue(eng.research_rollup(base)["all_resolved"],
                        "and the row stops blocking")

    # -- mid-operation failure: all-or-nothing on BOTH arms ------------------

    def test_a_failed_adopt_rename_unwinds_the_whole_group(self):
        """All-or-nothing. A half-adopted group leaves a gap in the sequence,
        and round sizing is a count, so the next write would land on a survivor.

        This path was untested until an independent checker pointed out that
        none of the mutations could reach it."""
        base, written = self._seed_legacy()
        before = {p.name: p.read_bytes() for p in written}
        real_rename = Path.rename
        calls = {"n": 0}

        def flaky(self_path, target):
            calls["n"] += 1
            if calls["n"] == 2:                      # fail on the 2nd of 3
                raise OSError("simulated rename failure")
            return real_rename(self_path, target)

        with mock.patch.object(Path, "rename", flaky):
            res = eng.adopt_legacy_markers(base, adopt_file=str(self.file_a),
                                           apply=True)

        self.assertEqual(res["status"], "REFUSED", res)
        self.assertEqual({p.name: p.read_bytes() for p in sorted(base.glob("R*.md"))},
                         before, "the whole group is back exactly as it was")
        self.assertIn("group left untouched", res["error"])

    def test_a_failed_supersede_unwinds_the_whole_group(self):
        """The set-aside arm needs the same all-or-nothing guarantee as adopt.

        It had none: a failure part-way left some rounds retired and some still
        read, with NO note written — because the note is only written after the
        whole group moves. Found by an independent checker."""
        base, written = self._seed_legacy()
        before = {p.name: p.read_bytes() for p in written}
        real_move = eng._move_aside
        calls = {"n": 0}

        def flaky(path, _now=None):
            calls["n"] += 1
            if calls["n"] == 2:
                return None                          # _move_aside's failure signal
            return real_move(path, _now=_now)

        with mock.patch.object(eng, "_move_aside", flaky):
            res = eng.adopt_legacy_markers(base, supersede_reason="x", apply=True)

        self.assertEqual(res["status"], "REFUSED", res)
        self.assertEqual({p.name: p.read_bytes() for p in sorted(base.glob("R*.md"))},
                         before, "the whole group is back exactly as it was")
        self.assertEqual(list(base.glob("legacy-superseded-*.md")), [],
                         "and no note claims a set-aside that did not happen")

    def test_an_incomplete_unwind_says_so_instead_of_claiming_success(self):
        """The unwind's own failure must not be swallowed.

        An earlier cut caught OSError per entry, discarded it, and then reported
        "group left untouched" unconditionally — asserting a state it had not
        achieved. That is the exact class of claim this plan exists to remove,
        committed inside the recovery path."""
        base, _ = self._seed_legacy()
        real_rename = Path.rename
        calls = {"n": 0}

        def flaky(self_path, target):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("simulated forward failure")
            if calls["n"] > 2:
                raise OSError("simulated unwind failure")
            return real_rename(self_path, target)

        with mock.patch.object(Path, "rename", flaky):
            res = eng.adopt_legacy_markers(base, adopt_file=str(self.file_a),
                                           apply=True)

        self.assertEqual(res["status"], "REFUSED", res)
        self.assertIn("UNWIND INCOMPLETE", res["error"])
        self.assertNotIn("group left untouched", res["error"],
                         "it must not claim a state it did not reach")
        key = eng._research_file_key(self.file_a.name)
        self.assertIn(f"{key}_R1.md", res["error"],
                      "and it names the file left stranded")

    # -- the set-aside arm is not a mute button ------------------------------

    def test_supersede_sentinel_refuses_while_the_research_file_still_exists(self):
        """The arm is for a sentinel that can no longer be re-run. If the file
        is still there the remedy is to RE-RUN it — which is what both gates
        say — so setting the sentinel aside would silence a genuinely unchecked
        file rather than retire a stranded record.

        Found by an independent checker: without this the arm is a
        general-purpose mute button, which is not what it was sanctioned as."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        key = eng._research_file_key(self.file_a.name)
        sentinel = eng._dispatch_sentinel_path(base, key)
        sentinel.touch()
        self.assertTrue(self.file_a.exists(), "precondition: the file is still there")

        res = eng.adopt_legacy_markers(base, sentinel_file=str(self.file_a),
                                       supersede_reason="I would rather not re-run it",
                                       apply=True)
        self.assertEqual(res["status"], "REFUSED", res)
        self.assertIn("still exists", res["error"])
        self.assertTrue(sentinel.exists(), "the evidence is untouched")
        self.assertFalse(eng.research_rollup(base)["all_resolved"],
                         "and the row still blocks, as it should")

    # -- the rails ------------------------------------------------------------

    def test_preview_by_default_mutates_nothing(self):
        """C17. Invoked without the apply flag the verb reports what it WOULD
        change and leaves the disk alone."""
        base, written = self._seed_legacy()
        snapshot = {p.name: p.read_bytes() for p in written}
        listing_before = sorted(p.name for p in base.iterdir())

        for kwargs in ({"adopt_file": str(self.file_a)},
                       {"supersede_reason": "unattributable"}):
            res = eng.adopt_legacy_markers(base, **kwargs)
            self.assertEqual(res["status"], "OK", res)
            self.assertFalse(res["applied"], "preview is the DEFAULT")
            self.assertTrue(res["planned"], "and it says what it would do")

        self.assertEqual(sorted(p.name for p in base.iterdir()), listing_before,
                         "no file was created, renamed or removed")
        for name, blob in snapshot.items():
            self.assertEqual((base / name).read_bytes(), blob)

    def test_a_single_move_restores_either_arm(self):
        """C18. Both arms are pure renames, so recovery is one `mv` per file —
        no special undo path, nothing to reconstruct."""
        base, written = self._seed_legacy()
        before = {p.name: p.read_bytes() for p in written}

        eng.adopt_legacy_markers(base, adopt_file=str(self.file_a), apply=True)
        key = eng._research_file_key(self.file_a.name)
        for n in (1, 2, 3):
            (base / f"{key}_R{n}.md").rename(base / f"R{n}.md")
        self.assertEqual({p.name: p.read_bytes() for p in
                          sorted(base.glob("R*.md"))}, before,
                         "adopt fully undone by renaming back")

        eng.adopt_legacy_markers(base, supersede_reason="x", apply=True)
        for bak in sorted(base.glob("R*.md.bak-*")):
            bak.rename(base / bak.name.split(".bak-")[0])
        self.assertEqual({p.name: p.read_bytes() for p in
                          sorted(base.glob("R*.md"))}, before,
                         "supersede fully undone by renaming back")

    def test_the_two_arms_are_mutually_exclusive_on_the_legacy_group(self):
        base, _ = self._seed_legacy()
        res = eng.adopt_legacy_markers(base, adopt_file=str(self.file_a),
                                       supersede_reason="both at once", apply=True)
        self.assertEqual(res["status"], "REFUSED", res)
        self.assertTrue((base / "R1.md").exists(), "nothing was touched")

    def test_no_automatic_invocation_anywhere(self):
        """The rail that stops a later slice quietly wiring this in. The verb
        mutates operator state, so it may only ever run because a human ran it."""
        callers = [HOOKS / "check-research-gate.sh",
                   HOOKS / "check-research-pipeline-gate.sh"]
        for path in callers:
            self.assertNotIn("adopt_legacy_markers(", path.read_text(),
                             f"{path.name} must not call the verb")

        src = (HOOKS / "_factcheck_engine.py").read_text()
        # Match the BARE function name only. A plain substring also matches
        # `cmd_adopt_legacy_markers(`, which would have counted the CLI wrapper
        # as a second caller and made this assertion about the wrong thing.
        call_sites = [ln for ln in src.splitlines()
                      if re.search(r"(?<!\w)adopt_legacy_markers\(", ln)
                      and not ln.lstrip().startswith(("def ", "#", '"', "*"))]
        # The ONLY permitted caller is the operator CLI seam.
        self.assertEqual(len(call_sites), 1,
                         f"exactly one caller (cmd_adopt_legacy_markers): {call_sites}")
        self.assertIn("res = adopt_legacy_markers(", call_sites[0])

        # And the engine's own run paths must not reach it.
        for fn in ("factcheck_run", "research_rollup", "_first_round_markers"):
            start = src.index(f"def {fn}(")
            body = src[start:src.index("\ndef ", start + 10)]
            self.assertNotIn("adopt_legacy_markers", body,
                             f"{fn} must never invoke the operator verb")

    def test_the_close_gate_names_the_verb_that_now_exists(self):
        """UX1/UX5: the block footer must name the remediation, and the named
        remediation must be real.

        Before S7 both gates told the operator to use "the recorded-reason
        operator verb" — a promise with no mechanism, which is this plan's own
        defect class. Now that the verb exists the text names it."""
        engine_src = (HOOKS / "_factcheck_engine.py").read_text()
        self.assertIn('cmd == "adopt-legacy-markers"', engine_src,
                      "precondition: the CLI verb is dispatched")
        text = (HOOKS / "check-research-gate.sh").read_text()
        for token in ("adopt-legacy-markers", "--adopt", "--supersede"):
            self.assertIn(token, text)

    def test_residuals_R2_and_R3_are_recorded_in_the_module_docstring(self):
        """Both residuals must be NAMED and their substance stated.

        Asserting the bare label was not enough: a mutation renaming `R-3` to
        `R-3x` left the substring `r-3` intact and the test green. The label is
        now matched with a boundary, and each residual's actual content is
        checked, so a residual cannot be reduced to a heading."""
        doc = (eng.__doc__ or "")
        low = doc.lower()
        for label in ("R-2", "R-3"):
            self.assertRegex(doc, rf"\b{label}\b(?![\w-])",
                             f"{label} is named as its own residual")
        # R-2's substance: legacy markers cannot be attributed BY CODE, and the
        # clearability half is closed by this verb.
        self.assertIn("cannot be attributed", low)
        self.assertIn("adopt-legacy-markers", low)
        # R-3's substance: adoption rests on the operator's assertion, and that
        # is affordable because it is a reversible rename.
        self.assertIn("operator's assertion", low)
        self.assertIn("reversible", low)

    def test_the_cli_verb_is_reachable_and_previews_by_default(self):
        """It is an OPERATOR verb, so it has to work from a command line."""
        base, _ = self._seed_legacy()
        engine = HOOKS / "_factcheck_engine.py"
        proc = subprocess.run(
            ["/usr/bin/python3", str(engine), "adopt-legacy-markers", str(base),
             "--supersede", "--reason", "unattributable"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["status"], "OK")
        self.assertFalse(payload["applied"], "the CLI previews by default too")
        self.assertTrue((base / "R1.md").exists(), "and mutates nothing")


# ===========================================================================
# S8 — the accepted-INCOMPLETE operator path is keyed
#
# S2 made `write_accept_marker` CAPABLE of receiving a slot; it was still
# handed a plain `Path`, so every operator accept landed an UNKEYED marker
# beside the keyed groups. That marker SANCTIONS rather than blocks (verdict
# INCOMPLETE + a non-empty accept_reason resolves in `_row_is_sanctioned`), so
# the pre-S8 accept both failed to clear the row it was invoked to clear AND,
# where a legacy group existed, became that group's newest entry and sanctioned
# it — the Guiding Policy 6 table's "stopping after the legacy exit" row.
# ===========================================================================


class AcceptedIncompleteIsKeyedTests(_PerFileBase):
    """S8 — an accept lands in the NAMED file's own sequence, or refuses.

    Two rails, both from the slice's Guard rails cell:

      named-sequence  the accept marker lands in the named file's own sequence,
                      so accepting one file's incomplete never sanctions a
                      sibling;
      refuse-or-guess a cycle with more than one candidate and no file named
                      ERRORS rather than picking one.

    The second rail is the safe-defaults reading: a refusal is reversible (pass
    `--file` and re-run), a wrong accept is not — it sanctions a file nobody
    asked about.
    """

    def _keyed(self, base, key, rounds):
        """Seed one file's own marker sequence with the given verdicts."""
        base.mkdir(parents=True, exist_ok=True)
        for n, v in enumerate(rounds, start=1):
            (base / f"{key}_R{n}.md").write_text(
                f"---\nschema_version: 3\nverdict: {v}\nrounds: {n}\n---\nround {n}\n")

    def _accept(self, **kw):
        return eng.accept_research_incomplete(
            self.state_dir, self.SID, kw.pop("reason", "accepted to close"),
            _proj_topic_resolver=self.resolver, **kw)

    # -- the named-sequence rail --------------------------------------------

    def test_an_accept_for_one_file_leaves_a_sibling_still_blocking(self):
        """The slice's own validation gate: accepting A must not sanction B.

        Before S8 the accept wrote an unkeyed `R1.md`, which resolves as a
        SANCTIONED legacy row while A itself stayed unresolved — the accept
        cleared a row nobody named and left the one it was invoked for."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["DIRTY", "ESCALATE"])
        self._keyed(base, key_b, ["ESCALATE"])

        res = self._accept(research_file=str(self.file_a),
                           reason="unverifiable offline claim; accepted")
        self.assertEqual(res["status"], "ACCEPTED", res)

        rows = {r["key"]: r for r in eng.research_rollup(base)["rows"]}
        self.assertTrue(rows[key_a]["sanctioned"], "A's own row carries the accept")
        self.assertTrue(rows[key_a]["resolved"])
        self.assertFalse(rows[key_b]["resolved"], "B is untouched and still blocks")
        self.assertNotIn(None, rows,
                         "and no unattributed legacy row was created")
        self.assertFalse(eng.research_rollup(base)["all_resolved"])

    def test_the_accept_marker_lands_in_the_named_files_own_sequence(self):
        """`rounds:` is sequence-local (AD19), so the number must come from THAT
        file's group — not from the directory's file count, and not from the
        busiest sibling."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["DIRTY", "DIRTY"])
        self._keyed(base, key_b, ["DIRTY"] * 5)

        res = self._accept(research_file=str(self.file_a), reason="accepted")
        marker = Path(res["marker"])
        self.assertEqual(marker.name, f"{key_a}_R3.md",
                         "third round of A's OWN sequence")
        self.assertEqual(res["key"], key_a)

        fm = eng._parse_marker_frontmatter(marker) or {}
        self.assertEqual(fm.get("verdict"), "INCOMPLETE")
        self.assertEqual(str(fm.get("rounds")), "3", "sequence-local rounds")
        self.assertIn("accepted", (fm.get("accept_reason") or ""))
        self.assertEqual(list(base.glob("R[0-9]*.md")), [],
                         "and nothing unkeyed was written")

    # -- the refuse-rather-than-guess rail -----------------------------------

    def test_two_candidate_files_and_no_file_named_refuses(self):
        """The refusal test the plan names. It must ERROR, not pick one."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["ESCALATE"])
        self._keyed(base, key_b, ["ESCALATE"])
        before = sorted(p.name for p in base.iterdir())

        with self.assertRaises(ValueError) as ctx:
            self._accept(reason="accepted")
        msg = str(ctx.exception)
        self.assertIn(key_a, msg, "the refusal names the candidates")
        self.assertIn(key_b, msg)
        self.assertIn("--file", msg, "and names the way through")
        self.assertEqual(sorted(p.name for p in base.iterdir()), before,
                         "nothing was written — a refusal, not a partial accept")

    def test_a_marker_group_and_a_dispatch_sentinel_are_two_candidates(self):
        """A dispatched-but-unchecked file is exactly the state an operator
        might accept, so it counts as a candidate. Counting only marker groups
        would read this cycle as unambiguous and sanction the wrong file."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["ESCALATE"])
        eng._dispatch_sentinel_path(base, key_b).touch()

        with self.assertRaises(ValueError) as ctx:
            self._accept(reason="accepted")
        self.assertIn(key_b, str(ctx.exception),
                      "the sentinel-only file is named as a candidate")

    def test_a_keyed_group_and_a_legacy_group_are_two_candidates(self):
        """A legacy group is unattributed but targetable — an unqualified accept
        could sanction it instead of the keyed file, which is the direction that
        buries a verdict rather than merely leaving one blocking."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        self._keyed(base, key_a, ["ESCALATE"])
        (base / "R1.md").write_text(
            "---\nschema_version: 3\nverdict: ESCALATE\nrounds: 1\n---\nlegacy\n")

        with self.assertRaises(ValueError) as ctx:
            self._accept(reason="accepted")
        self.assertIn("legacy", str(ctx.exception).lower())

    def test_a_set_aside_sentinel_is_not_a_candidate(self):
        """`.dispatched-{key}.bak-<ts>` is retired evidence, and the rollup
        already skips it for exactly this reason. Counting it here would make
        every later accept ambiguous — so S7's one arm for clearing a stale
        sentinel would leave the cycle harder to close than before it ran."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["ESCALATE"])
        sentinel = eng._dispatch_sentinel_path(base, key_b)
        sentinel.touch()
        self.file_b.unlink()
        eng.adopt_legacy_markers(base, sentinel_file=str(self.file_b),
                                 supersede_reason="the research file was deleted",
                                 apply=True)

        self.assertEqual(eng._accept_candidates(base), [key_a],
                         "the retired sentinel is gone from the candidate set")
        res = self._accept(reason="accepted")
        self.assertEqual(res["key"], key_a,
                         "so the remaining candidate is still unambiguous")

    # -- the unambiguous cases ----------------------------------------------

    def test_a_single_candidate_is_accepted_without_a_file(self):
        """With one candidate there is nothing to guess between, so requiring
        `--file` would be friction with no safety behind it."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        self._keyed(base, key_a, ["ESCALATE"])

        res = self._accept(reason="accepted")
        self.assertEqual(Path(res["marker"]).name, f"{key_a}_R2.md")
        self.assertEqual(res["key"], key_a)
        self.assertTrue(eng.research_rollup(base)["all_resolved"])

    def test_a_legacy_only_cycle_keeps_its_unkeyed_accept(self):
        """Characterization — green before AND after. A topic that predates
        keying must still be acceptable exactly as it was; sending its accept
        into a key would strand the legacy group it was meant to clear."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        for n in (1, 2, 3):
            (base / f"R{n}.md").write_text(
                f"---\nschema_version: 3\nverdict: DIRTY\nrounds: {n}\n---\nr{n}\n")

        res = self._accept(reason="accepted, legacy topic")
        self.assertEqual(Path(res["marker"]).name, "R4.md")
        self.assertIsNone(res["key"])
        self.assertTrue(eng.research_rollup(base)["all_resolved"])

    def test_an_empty_cycle_still_accepts_unkeyed(self):
        """Characterization — green before AND after. No evidence at all means
        nothing to attribute; today's behaviour is preserved."""
        res = self._accept(reason="nothing was ever dispatched")
        self.assertEqual(Path(res["marker"]).name, "R1.md")
        self.assertIsNone(res["key"])

    def test_a_non_research_kind_refuses_a_named_file(self):
        """Per-file keying is research-only (so are the sentinels). Silently
        ignoring an explicit operator argument is worse than refusing it."""
        with self.assertRaises(ValueError) as ctx:
            self._accept(kind="workflow", research_file=str(self.file_a),
                         reason="accepted")
        self.assertIn("research", str(ctx.exception).lower())

    def test_a_non_research_kind_with_no_file_still_accepts_unkeyed(self):
        """The other half of that branch, and the half a refusal test cannot
        reach: a non-research accept with nothing named must still WORK.

        Added after an independent checker observed that only the refusing half
        was exercised, so a regression making the whole `kind != "research"`
        branch raise would have gone uncaught — the four non-research kinds
        would have lost their accept path silently."""
        base = self.state_dir / self.PROJ / self.TOPIC / "workflow"
        res = self._accept(kind="workflow", reason="accepted, workflow kind")
        self.assertEqual(Path(res["marker"]).name, "R1.md")
        self.assertIsNone(res["key"], "no per-file keying outside the research kind")
        self.assertTrue((base / "R1.md").exists(),
                        "and it landed in that kind's own directory")

    def test_a_named_file_with_no_evidence_here_refuses(self):
        """A mistyped `--file` must not mint a phantom resolved row.

        The gate blocks on rows that EXIST, so an accept for a file with no
        marker and no dispatch record clears nothing — it only adds a new row
        that reads as sanctioned, which is a resolved verdict over work nobody
        checked. In the ordinary flow the evidence is always present (a terminal
        INCOMPLETE writes its own marker), so this costs the real path nothing
        and catches the wrong-path case."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        self._keyed(base, key_a, ["ESCALATE"])
        before = sorted(p.name for p in base.iterdir())

        with self.assertRaises(ValueError) as ctx:
            self._accept(research_file=str(self.file_b), reason="accepted")
        msg = str(ctx.exception)
        self.assertIn("nothing to accept", msg)
        self.assertIn(key_a, msg, "and the refusal says what IS here")
        self.assertEqual(sorted(p.name for p in base.iterdir()), before,
                         "no phantom group was created")

    # -- the two readers of one directory must agree -------------------------

    def test_the_candidate_scan_agrees_with_the_rollup(self):
        """S6 built `EvidenceProbeConsistencyTests` after a fourth reader of the
        same directory silently disagreed with the other three. This is the same
        rail for the fifth: the candidate scan and the rollup must see the same
        evidence groups.

        A marker-shaped name with no parsable round is deliberately NOT a
        candidate — it cannot be numbered into, so it cannot be accepted into —
        while it IS an (unresolved) rollup row. That asymmetry is asserted here
        rather than left implicit."""
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["PASS"])
        eng._dispatch_sentinel_path(base, key_b).touch()
        (base / "R2.md").write_text(
            "---\nschema_version: 3\nverdict: PASS\nrounds: 2\n---\nlegacy\n")
        junk = base / "notaround_Rx.md"
        junk.write_text("---\nverdict: PASS\n---\njunk\n")

        candidates = set(eng._accept_candidates(base))
        self.assertEqual(candidates, {key_a, key_b, None})

        rollup_ids = {(None if r["legacy"] else r["key"])
                      for r in eng.research_rollup(base)["rows"]}
        self.assertEqual(candidates, rollup_ids - {junk.name},
                         "same evidence groups, minus the un-numberable name")
        self.assertIn(junk.name, rollup_ids, "which the rollup still reports")

    # -- the operator CLI ----------------------------------------------------

    def test_the_cli_routes_file_into_the_keyed_sequence(self):
        """The engine function is only half the slice — the operator reaches it
        through `pre_plan_gates.py accept-research-incomplete`, and a `--file`
        the CLI drops on the floor keys nothing.

        Asserted on the marker NAME rather than the exit code: an unwired
        `--file` still exits 0 (the accept succeeds, into the wrong sequence),
        so an exit-code assertion here could not fail."""
        home = Path(self.tmpdir) / "clihome"
        topic_state = home / ".claude" / "state" / "pre_plan_gates"
        topic_state.mkdir(parents=True)
        (topic_state / "_active.json").write_text(json.dumps(
            {self.SID: {"topic_slug": self.PROJ, "active_project": self.TOPIC}}))
        (topic_state / f"{self.PROJ}__{self.TOPIC}.json").write_text(
            json.dumps({"topic_slug": self.PROJ, "project_slug": self.TOPIC}))
        base = (home / ".claude" / "state" / "plan_validation" / self.PROJ
                / self.TOPIC / "research")
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["INCOMPLETE"])
        self._keyed(base, key_b, ["ESCALATE"])

        env = dict(os.environ, HOME=str(home))
        env.pop("RP_STATE_DIR", None)
        proc = subprocess.run(
            ["/usr/bin/python3", str(HOOKS / "pre_plan_gates.py"),
             "accept-research-incomplete", self.SID,
             "--reason", "accepted via the CLI",
             "--file", str(self.file_a)],
            capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)

        written = sorted(p.name for p in base.glob("*.md"))
        self.assertEqual(
            written, sorted([f"{key_a}_R1.md", f"{key_a}_R2.md", f"{key_b}_R1.md"]),
            "the CLI's --file put the accept in A's own sequence, not B's "
            "and not an unkeyed one")

    def test_the_cli_refusal_names_the_candidates(self):
        """The refusal has to be actionable where the operator meets it."""
        home = Path(self.tmpdir) / "clihome2"
        topic_state = home / ".claude" / "state" / "pre_plan_gates"
        topic_state.mkdir(parents=True)
        (topic_state / "_active.json").write_text(json.dumps(
            {self.SID: {"topic_slug": self.PROJ, "active_project": self.TOPIC}}))
        (topic_state / f"{self.PROJ}__{self.TOPIC}.json").write_text(
            json.dumps({"topic_slug": self.PROJ, "project_slug": self.TOPIC}))
        base = (home / ".claude" / "state" / "plan_validation" / self.PROJ
                / self.TOPIC / "research")
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        self._keyed(base, key_a, ["ESCALATE"])
        self._keyed(base, key_b, ["ESCALATE"])

        env = dict(os.environ, HOME=str(home))
        env.pop("RP_STATE_DIR", None)
        proc = subprocess.run(
            ["/usr/bin/python3", str(HOOKS / "pre_plan_gates.py"),
             "accept-research-incomplete", self.SID,
             "--reason", "accepted via the CLI"],
            capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("--file", proc.stderr)
        self.assertIn(key_a, proc.stderr)
        self.assertEqual(sorted(p.name for p in base.glob("*.md")),
                         sorted([f"{key_a}_R1.md", f"{key_b}_R1.md"]),
                         "no marker was written by the refused accept")


# ===========================================================================
# S9 — whole-plan verification against the locked Desired Outcome
#
# S9 adds NO production code. It asserts the ASSEMBLED result of S1-S8, and it
# asserts two NEGATIVES no implementation slice can demonstrate about itself:
# a slice cannot prove it did not touch something.
# ===========================================================================


class WholePlanObservableTests(_CloseGateBase):
    """The locked observable test, run against the REAL engine and the REAL gate.

    Every other suite in this file exercises one mechanism. This one runs a
    genuine multi-file `factcheck_run` into the directory the shell gate reads,
    then drives that gate as a subprocess — so what is asserted is the composed
    behaviour of S1 (keying + reader), S4 (sentinel + UNCHECKED), S5 (the shared
    verb both gates consume), S7 (the set-aside exit) and S8 (the keyed accept)
    at once.

    **On why no row here reaches PASS.** In this offline harness a research draft
    cannot reach a passing terminal at all: `_run_coverage_axis_gate` downgrades a
    draft with no cited URLs, and `_run_source_integrity_gate` escalates one whose
    cited URLs are unreachable — verified by executing both fixtures. Those two
    gates are the ones this plan is forbidden to touch (see the zero-edit test
    below), so the honest whole-plan observable is not "a file turns green" but
    "each file reaches its OWN verdict, and the close gate blocks until every row
    is resolved through a mechanism this plan shipped". That is what these tests
    assert.
    """

    def _run_into_gate_dir(self, draft, verdict, max_rounds=3):
        """A real factcheck_run whose markers land where the gate reads."""
        return eng.factcheck_run(
            self.home / ".claude" / "state" / "plan_validation",
            str(draft), "research", self.SID,
            debounce_seconds=0,
            _checker_fn=_CountingChecker(verdict),
            _proj_topic_resolver=self.resolver,
            max_rounds=max_rounds)

    def _assemble(self):
        """A cycle holding one incomplete file, one escalated file, and one file
        that was dispatched and never produced a verdict."""
        self.file_c = self._draft("gamma-20260101010101_RESEARCH.md")
        self._run_into_gate_dir(self.file_a, "PASS")
        self._run_into_gate_dir(self.file_b, "DISCREPANCY", max_rounds=2)
        keys = {n: eng._research_file_key(f.name) for n, f in
                (("a", self.file_a), ("b", self.file_b), ("c", self.file_c))}
        eng._touch_dispatch_sentinel(self.gate_base, keys["c"])
        return keys

    def test_each_file_reaches_its_own_verdict_and_close_is_blocked(self):
        keys = self._assemble()
        raw = eng.research_rollup(self.gate_base)["rows"]
        rows = {r["key"]: r for r in raw}

        # The COUNT is asserted separately from the key set, and deliberately.
        # Keying a dict by `r["key"]` collapses any two rows sharing a key, so a
        # key-set assertion alone proves "these three keys are present", not
        # "exactly three rows came back" — and the unparsable-marker branch keys
        # its rows by raw FILENAME, a different namespace, so a future duplicate
        # path could inflate the count while the key set still matched.
        self.assertEqual(len(raw), 3, f"exactly one row per file; got {raw}")
        self.assertEqual(set(rows), set(keys.values()),
                         "one row per file — no cycle-wide collapse")
        self.assertEqual(rows[keys["a"]]["verdict"], "INCOMPLETE")
        self.assertEqual(rows[keys["b"]]["verdict"], "ESCALATE",
                         "a terminal no-consensus surfaces as ESCALATE")
        self.assertEqual(rows[keys["c"]]["verdict"], "UNCHECKED")

        # UNCHECKED is a DISTINCT class, not a flavour of failure (C12): the file
        # did not fail a check, it never produced one.
        c = rows[keys["c"]]
        self.assertIsNone(c["marker"], "nothing was ever written for it")
        self.assertEqual(c["rounds"], 0)
        self.assertNotIn(c["verdict"], ("ESCALATE", "DIRTY", "INCOMPLETE"))
        self.assertFalse(eng.research_rollup(self.gate_base)["all_resolved"])

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        for name, key in keys.items():
            self.assertIn(key, proc.stderr, f"the block names file {name}")

    def test_close_unblocks_only_when_every_row_is_resolved(self):
        """Resolved one at a time, through the mechanisms this plan shipped —
        and the gate keeps blocking until the LAST one is dealt with. This is
        the property the whole item exists to deliver: no file's disposition can
        stand in for another's."""
        keys = self._assemble()
        state_dir = self.home / ".claude" / "state" / "plan_validation"

        # S8's keyed accept resolves exactly the file it names.
        eng.accept_research_incomplete(
            state_dir, self.SID, "accepted: offline source, cannot verify",
            _proj_topic_resolver=self.resolver, research_file=str(self.file_a))
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, "B and C still block")
        self.assertIn(keys["b"], proc.stderr)
        self.assertIn(keys["c"], proc.stderr)

        eng.accept_research_incomplete(
            state_dir, self.SID, "accepted: no consensus, shipping with caveat",
            _proj_topic_resolver=self.resolver, research_file=str(self.file_b))
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, "C alone still blocks")
        self.assertIn(keys["c"], proc.stderr)

        # S7's set-aside is the ONLY exit for a sentinel whose file is gone —
        # "re-run it" is impossible, so without this arm the row blocks forever.
        self.file_c.unlink()
        res = eng.adopt_legacy_markers(
            self.gate_base, sentinel_file=str(self.file_c),
            supersede_reason="the research file was deleted", apply=True)
        self.assertEqual(res["status"], "OK", res)

        proc = self._run_gate()
        self.assertEqual(proc.returncode, 0,
                         f"every row resolved -> close passes; stderr={proc.stderr!r}")

    def test_the_per_kind_lock_stays_flat_and_unkeyed(self):
        """AD14. Keying the marker NAME must not key the lock — the lock is
        per-kind concurrency control, and this item is not chartered to change
        concurrency semantics. Asserted behaviourally: two different files
        running in one cycle leave exactly ONE lock, named `.lock`."""
        self._run_into_gate_dir(self.file_a, "PASS")
        self._run_into_gate_dir(self.file_b, "PASS")
        locks = [p.name for p in self.gate_base.iterdir() if ".lock" in p.name]
        self.assertEqual(locks, [".lock"],
                         "one flat per-kind lock, carrying no file key")


class ScopeBoundaryNegativeTests(_PerFileBase):
    """The two NEGATIVES. A slice cannot demonstrate that it did not touch
    something, which is exactly why these are S9's job and not S2's or S8's.

    Both are anchored against a copy of the engine this plan has never written
    to. Two anchors are used, and WHICH ONE CARRIES THE PROPERTY CHANGES AT V1:

    * **The pristine baseline clone** (`~/.claude-staging-pfg-baseline`) is the
      DURABLE anchor. It is a copy of live taken before this plan began and it
      never receives the promotion, so it keeps meaning "unchanged relative to
      the pre-plan text" for as long as it exists.
    * **Live** `${KIT_HOOKS_DIR}/_factcheck_engine.py` is a SECOND anchor that is
      sound only until V1. The plan promotes exactly once, at V1; before that,
      live still holds the pre-plan text of anything this plan was supposed to
      leave alone. AFTER the promotion, live equals the promoted tree, so the
      live comparison degrades into the clone being compared against a copy of
      itself and stops establishing anything on its own. It is deliberately kept
      rather than removed, because post-V1 it still fails the moment either
      function is next edited by anyone — but that is a weaker, different
      property than the one this class is named for.

    **Signpost for whoever sees this go red.** These two functions are the Group
    II item 3 / Group V collision zone, and those items are chartered to edit
    them. A red here after this plan lands is therefore expected work, not
    necessarily a regression: check whether the edit belongs to one of those
    items, and if so retire the corresponding assertion as part of that item —
    the same way the sibling test below is labelled for retirement by Group I
    item 1. What must NOT happen is this class being deleted wholesale to make a
    suite green.
    """

    LIVE = Path.home() / ".claude" / "hooks" / "_factcheck_engine.py"
    BASELINE = Path.home() / ".claude-staging-pfg-baseline" / "hooks" / "_factcheck_engine.py"

    def _func(self, src, name):
        m = re.search(rf"\ndef {name}\(.*?\n(.*?)\n(?:def |class )", src, re.S)
        self.assertIsNotNone(m, f"{name} is present")
        return m.group(1)

    def _anchors(self):
        """Every un-written-to copy of the engine available to compare against.

        Baseline FIRST: it is the durable anchor (see the class docstring), and
        listing it first keeps the failure message pointing at the comparison
        that still means something after V1.

        Failing when there are NONE is deliberate: a negative with no anchor is
        not proven, and silently skipping would report it as proven."""
        found = [(label, p.read_text(encoding="utf-8"))
                 for label, p in (("baseline", self.BASELINE), ("live", self.LIVE))
                 if p.exists()]
        self.assertTrue(found, "no un-written-to engine copy to compare against — "
                               "these negatives cannot be established")
        return found

    def test_the_two_co_edited_gate_functions_carry_zero_edits(self):
        """`_run_source_integrity_gate` and `_run_coverage_axis_gate` sit in the
        Group II item 3 / Group V collision zone. S2 keyed their WRITERS without
        touching them, and this asserts the stronger form the sibling test
        cannot: byte-identity against a copy this plan never wrote to."""
        clone = Path(eng.__file__).read_text(encoding="utf-8")
        for label, other in self._anchors():
            for name in ("_run_source_integrity_gate", "_run_coverage_axis_gate"):
                self.assertEqual(
                    self._func(clone, name), self._func(other, name),
                    f"{name} differs from the {label} copy — this plan may take "
                    f"ZERO edits there (the Group II / Group V collision zone)")

    # RETIRED by Group I item 1 (2026-08-15): `test_the_terminal_writeback_still_
    # re_resolves_its_cycle_id` lived here. It was a deliberate SCOPE FENCE pinning
    # a property item 1 existed to invert — that the terminal writeback still
    # re-resolved its own cycle_id after item 2 landed (design AD21) — and its own
    # docstring chartered its deletion: "When Group I item 1 lands, DELETE this test
    # as part of that item." Item 1 has now landed: the writeback takes the committed
    # `cycle_id` as a required parameter and no longer calls `_resolve_research_cycle_id`,
    # so the assertion is false BY DESIGN and the fence has served its purpose. The
    # inverted property is now asserted positively by
    # `test_research_cycle_isolation.py::test_named_cycle_frontmatter_row_names_the_
    # same_cycle_as_the_markers`. The sibling zero-edit test above is unaffected and
    # still guards the Group II / Group V collision zone.

    def test_the_marker_schema_gained_no_field_for_identity(self):
        """AD19. Identity lives in the FILENAME. A `file:` or `key:` frontmatter
        field would be a schema change requiring every reader to become
        version-aware, and `factcheck-convergence.md` §7 pins that schema.

        Asserted over the marker's RAW frontmatter, not over
        `_parse_marker_frontmatter`. That reader extracts a FIXED field set
        (verdict / rounds / bypass_reason / accept_reason / schema_version /
        checked_at), so a dict from it structurally cannot contain a new field
        and an assertion against it could never fail — the first version of this
        test did exactly that, and a mutation adding `file_key:` to the writer
        left it green."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        slot = eng.ResearchMarkerSlot(base, eng._research_file_key(self.file_a.name))
        eng.write_accept_marker(slot, "accepted", kind="research")

        text = slot.existing()[0].read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---"), "the marker opens with frontmatter")
        block = text[3:text.find("\n---", 3)]
        fields = {ln.split(":", 1)[0].strip()
                  for ln in block.splitlines() if ":" in ln and not ln.startswith(" ")}

        self.assertRegex(block, r"schema_version:\s*3\b")
        # Asserted as EQUALITY, in both directions. A one-directional
        # "nothing outside the allow-list" check would stay green if the writer
        # stopped emitting `kind` or `checker_count`, so it could not support the
        # word "exactly" — which is what this test claims about the §7 schema.
        self.assertEqual(
            fields,
            {"schema_version", "rounds", "kind", "checker_count",
             "verdict", "accept_reason", "checked_at"},
            "the accept marker's field set is EXACTLY the §7 schema — nothing "
            "added (identity lives in the FILENAME) and nothing dropped")

    def test_all_three_residuals_are_named_in_the_shipped_module_docstring(self):
        """A residual the code does not carry is a residual the next reader will
        rediscover as a bug."""
        doc = eng.__doc__ or ""
        for label in ("R-1", "R-2", "R-3"):
            self.assertRegex(doc, rf"\b{label}\b(?![\w-])",
                             f"{label} is named as its own residual")


class GroupIVReRunIsPerformableTests(_PerFileBase):
    """S9's rail: the dependent re-run (Group IV) is performable on the RECORDED
    path — verified WITHOUT executing it.

    Executing it would re-run a real topic's fact-check as a side effect of a
    test suite, which is not this slice's business. What is checked instead is
    that the recorded state still has the shape the plan documents, and that the
    mechanism which makes it re-runnable is reachable on a faithful copy of it.
    """

    RECORDED = (Path.home() / ".claude" / "state" / "plan_validation"
                / "clarification-v2" / "Root" / "research")

    def test_the_recorded_state_still_has_the_shape_the_plan_documents(self):
        if not self.RECORDED.is_dir():
            self.skipTest(f"recorded Group IV state absent at {self.RECORDED} — "
                          "external state, not a repo artifact")
        # CHARTER EXPIRED 2026-08-16 (Group IV close-out). This assertion pins
        # EXTERNAL, MUTABLE state as a fixture, and its whole premise was that
        # Group IV had not run yet ("performable ... verified WITHOUT executing
        # it"). Group IV HAS now run: the legacy unkeyed R1-R4 group was
        # deliberately SUPERSEDED (renamed to .bak-<ts>, receipt left behind)
        # because adopting it would have re-masked R3's ESCALATE and sized the
        # next round past max_rounds=3. So the shape this test demands no longer
        # exists BY DESIGN, and demanding it turns a completed dependency into a
        # permanently red test. Skip on the recorded supersession rather than
        # deleting the class: if the legacy state is ever restored, the guard
        # applies again. The durable proof lives in the sibling test below, which
        # reproduces the shape LOCALLY and never touches the real directory.
        superseded = sorted(self.RECORDED.glob("legacy-superseded-*.md"))
        if superseded:
            self.skipTest(
                "Group IV was executed and the legacy group superseded on "
                f"{superseded[0].name} — this assertion's premise (an un-run "
                "Group IV) is spent; see the sibling test for the live proof")
        names = sorted(p.name for p in self.RECORDED.glob("*.md"))
        self.assertEqual(names, ["R1.md", "R2.md", "R3.md", "R4.md"],
                         "a single flat unkeyed sequence, exactly as recorded")
        verdicts = [(eng._parse_marker_frontmatter(self.RECORDED / n) or {}).get("verdict")
                    for n in names]
        self.assertEqual(verdicts, ["DIRTY", "DIRTY", "ESCALATE", "PASS"],
                         "R3's ESCALATE is still masked by R4's PASS")
        self.assertEqual(list(self.RECORDED.glob("*_R*.md")), [],
                         "and nothing there is attributed to a file yet")

    def test_that_shape_is_resolvable_by_the_shipped_operator_verb(self):
        """The same shape, reproduced locally, is cleared by `--adopt` — so the
        recorded state is not a deadlock. The real directory is never touched."""
        base = self._research_base()
        base.mkdir(parents=True, exist_ok=True)
        for n, v in enumerate(["DIRTY", "DIRTY", "ESCALATE", "PASS"], start=1):
            (base / f"R{n}.md").write_text(
                f"---\nschema_version: 3\nverdict: {v}\nrounds: {n}\n---\nround {n}\n")

        res = eng.adopt_legacy_markers(base, adopt_file=str(self.file_a), apply=True)
        self.assertEqual(res["status"], "OK", res)
        key = eng._research_file_key(self.file_a.name)
        self.assertEqual(sorted(p.name for p in base.glob("*.md")),
                         [f"{key}_R{n}.md" for n in (1, 2, 3, 4)],
                         "the masked group is now that file's own sequence")
        self.assertEqual(list(base.glob("R[0-9]*.md")), [],
                         "and no unattributed group is left behind")


class ThoughtKindDerivationUnchangedTests(_PerFileBase):
    """AD15 for the `thought` kind, at the two sites that also serve research.

    `FourKindCharacterizationTests` pins the other kinds' MARKER names, but it
    uses only simple lowercase unscoped fixtures (`charlie-<ts>_THOUGHT.md`), so
    it cannot see a change in the ADVISORY derivation. Two sites — the `_CLAIMS`
    registry and the slug-mirror — run under `kind in ("research", "thought")`
    and were switched to the normalised research slug, which differs from the
    bookkeeping classifier on exactly the names the classifier REJECTS:

        MyTopic_THOUGHT.md      classifier None  ->  normalised 'mytopic'
        R2D2_THOUGHT.md         classifier None  ->  normalised 'k-r2d2'
        weird name!_THOUGHT.md  classifier None  ->  normalised 'weird-name'

    A `None` produced no advisory artifact at all; a string produces one. So a
    thought file the grammar rejects began getting a `_CLAIMS.md` and a mirror it
    never used to get — a behaviour change on a kind this item may not touch.
    Found by an independent validator, not by the suite.
    """

    REJECTED = ("MyTopic_THOUGHT.md", "R2D2_THOUGHT.md", "weird name!_THOUGHT.md")

    def _classifier_slug(self, name):
        import bookkeeping_invariant as bk
        try:
            return bk.classify(name).slug
        except Exception:
            return None

    def test_raw_mode_reproduces_the_classifier_exactly(self):
        """The `raw` derivation must BE the pre-keying one, not merely similar."""
        for name in self.REJECTED + ("charlie-20260101010101_THOUGHT.md",
                                     "alpha-20260101010101_RESEARCH.md"):
            self.assertEqual(
                eng._research_display_slug(name, raw=True),
                self._classifier_slug(name),
                f"raw derivation must match the classifier for {name!r}")

    def test_a_grammar_rejected_thought_name_yields_no_advisory_slug(self):
        """The property that actually matters: None, so no artifact is created."""
        for name in self.REJECTED:
            self.assertIsNone(eng._research_display_slug(name, raw=True),
                              f"{name!r} must stay unslugged for the thought kind")

    def test_raw_mode_returns_none_when_the_classifier_RAISES(self):
        """The defensive half of raw mode, which no fixture reaches naturally.

        A rejected name makes the classifier RETURN None; nothing in the corpus
        makes it RAISE, so a mutation removing this branch went uncaught until
        this test existed. It matters because the pre-keying code swallowed the
        exception too — raising here, or falling through to the fallback
        synthesis, would both diverge from the behaviour AD15 pins."""
        import bookkeeping_invariant as bk
        with mock.patch.object(bk, "classify",
                               side_effect=RuntimeError("classifier exploded")):
            self.assertIsNone(
                eng._research_display_slug("charlie-20260101010101_THOUGHT.md", raw=True),
                "a raising classifier degrades to None, as the pre-keying path did")

    def test_the_research_kind_still_gets_the_normalised_slug(self):
        """The repair must not un-key research — raw is opt-in, per kind."""
        for name in self.REJECTED:
            self.assertIsNotNone(eng._research_display_slug(name),
                                 "research keeps the normalised derivation")
        self.assertEqual(eng._research_display_slug("MyTopic_RESEARCH.md"), "mytopic")

    def test_both_mixed_kind_sites_select_the_derivation_by_kind(self):
        """Pins the wiring, not just the helper: a site that dropped the `raw=`
        argument would silently restore the breach, and no other test would see
        it (the helper's own tests would still pass)."""
        src = Path(eng.__file__).read_text(encoding="utf-8")
        # The CALLS, not the `def` — its signature also spells `raw=`, and a
        # regex that matches the definition passes no matter how the sites are
        # wired. The sibling test at the top of this file records the same trap.
        sites = [s for s in re.findall(r"(?<!def )_research_display_slug\([^)]*\)", src)
                 if s.startswith("_research_display_slug(draft_path")]
        mixed = [s for s in sites if "raw=" in s]
        self.assertGreaterEqual(len(mixed), 2,
                                f"both mixed-kind sites pass raw=; found {sites}")
        for s in mixed:
            self.assertIn('kind != "research"', s,
                          "the derivation is selected BY KIND, not hardcoded")


class WritebackConsumerIsKeyedTests(_PerFileBase):
    """The G3 consumer the plan named and the work missed.

    `cmd_write_research_frontmatter` is the operator's engine-cannot-run escape:
    it reads each cycle's latest marker and stamps `fc_cycles:` into the research
    file. AD13 says it "derives the key from the `research_file_path` it already
    reads per cycle", and G3's Desired State is "every reader groups by the same
    per-file key … No reader silently drops a record".

    It was left calling `_latest_marker(base, cycle)` with NO key, so once the
    writers were keyed it matched nothing and the verb died with "no R*.md
    markers found for any cycle" — a REGRESSION: it worked before this plan.
    Found by two independent validators; no test at any level covered it, because
    the `key=` parameter's only callers were other tests.
    """

    ENGINE = HOOKS / "_factcheck_engine.py"

    def _harness(self, markers, *, files=None):
        """A live-shaped fixture: _active.json + RP manifest + marker dir."""
        home = Path(self.tmpdir) / "wbhome"
        ps = home / ".claude" / "state" / "pre_plan_gates"
        ps.mkdir(parents=True)
        (ps / "_active.json").write_text(json.dumps(
            {self.SID: {"topic_slug": self.PROJ, "active_project": self.TOPIC}}))
        base = (home / ".claude" / "state" / "plan_validation" / self.PROJ
                / self.TOPIC / "research")
        base.mkdir(parents=True)
        for name, verdict in markers.items():
            (base / name).write_text(
                f"---\nschema_version: 3\nverdict: {verdict}\nrounds: 1\n---\nbody\n")
        rp = self.rp_dir / f"RP-{self.SID}.json"
        cycles = {c: {"research_file_path": str(p)}
                  for c, p in (files or {"default": self.file_a}).items()}
        rp.write_text(json.dumps({"cycles": cycles}))
        env = dict(os.environ, HOME=str(home), RP_STATE_DIR=str(self.rp_dir))
        return home, base, env

    def _run(self, env):
        return subprocess.run(
            ["/usr/bin/python3", str(self.ENGINE),
             "write-research-frontmatter", self.SID],
            capture_output=True, text=True, timeout=180, env=env)

    def test_a_fully_keyed_cycle_is_not_dead_to_the_writeback(self):
        """The repro. Before the fix this exits 3 — the operator's documented
        escape path is inert for exactly the cycles this plan creates."""
        key_a = eng._research_file_key(self.file_a.name)
        _home, _base, env = self._harness({f"{key_a}_R1.md": "BYPASSED"})
        # BYPASSED needs a reason; give it one so we test the LOOKUP, not the
        # malformed-marker guard.
        _base_dir = _base / f"{key_a}_R1.md"
        _base_dir.write_text("---\nschema_version: 3\nverdict: BYPASSED\nrounds: 1\n"
                             'bypass_reason: "checkers timed out"\n---\nbody\n')

        proc = self._run(env)
        self.assertEqual(proc.returncode, 0,
                         f"the keyed marker must be found; stderr={proc.stderr!r}")
        self.assertIn("fc_cycles:", self.file_a.read_text(encoding="utf-8"))
        self.assertIn("BYPASSED", self.file_a.read_text(encoding="utf-8"))

    def test_the_writeback_reads_each_files_OWN_marker(self):
        """Two files, two cycles, two DIFFERENT verdicts. Each file must receive
        its own — the masking harm this whole item exists to end, reproduced on
        the one consumer that was left unkeyed."""
        key_a = eng._research_file_key(self.file_a.name)
        key_b = eng._research_file_key(self.file_b.name)
        _home, _base, env = self._harness(
            {f"{key_a}_R1.md": "PASS", f"{key_b}_R1.md": "ESCALATE"},
            files={"default": self.file_a, "de": self.file_b})
        # The per-cycle nest: cycle 'de' reads from research/de/.
        nest = _base / "de"
        nest.mkdir()
        (nest / f"{key_b}_R1.md").write_text(
            "---\nschema_version: 3\nverdict: ESCALATE\nrounds: 1\n---\nbody\n")

        proc = self._run(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        a_text = self.file_a.read_text(encoding="utf-8")
        b_text = self.file_b.read_text(encoding="utf-8")
        self.assertIn("PASS", a_text, "A gets its own verdict")
        self.assertNotIn("ESCALATE", a_text, "and NOT its sibling's")
        self.assertIn("ESCALATE", b_text, "B gets its own verdict")

    def test_a_legacy_unkeyed_cycle_still_writes_back(self):
        """Characterization — green before AND after. The pre-keying corpus and
        the documented hand-authored BYPASSED flow both use unkeyed markers; the
        keyed lookup must FALL BACK to them, not replace them."""
        _home, base, env = self._harness({})
        (base / "R1.md").write_text(
            "---\nschema_version: 3\nverdict: BYPASSED\nrounds: 1\n"
            'bypass_reason: "engine could not run"\n---\nbody\n')

        proc = self._run(env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = self.file_a.read_text(encoding="utf-8")
        self.assertIn("BYPASSED", text)
        self.assertIn("engine could not run", text)

    def test_an_unkeyable_path_degrades_to_the_legacy_read(self):
        """`_research_file_key` raises on a name it cannot slug. That must not
        take the whole verb down — it degrades to the unkeyed read."""
        _home, base, env = self._harness({}, files={"default": Path(self.tmpdir) / "_.md"})
        (Path(self.tmpdir) / "_.md").write_text("# draft\n")
        (base / "R1.md").write_text(
            "---\nschema_version: 3\nverdict: PASS\nrounds: 1\n---\nbody\n")

        proc = self._run(env)
        self.assertEqual(proc.returncode, 0,
                         f"an unkeyable path must not crash the verb; {proc.stderr!r}")


# --------------------------------------------------------------------------- #
# research-factcheck-refusal-evidence S1 — A1 (obligation) + A2 (guard)
# --------------------------------------------------------------------------- #

class ObligationSurvivesARefusingEngineTests(unittest.TestCase):
    """A1 — the dispatcher records that a check was OWED, before the engine runs.

    The diagnosed fault is that the only evidence a check was owed
    (`.dispatched-<key>`) is written by the engine, AFTER a raise that fires when
    the session has no resolvable topic. So every refusal leaves nothing, and each
    close-time reader treats "nothing" as "nothing was owed".

    These cases drive the SHELL dispatcher, not the engine, because the dispatcher
    is the component the fix relies on: it always runs when a check is dispatched
    and never consults `_active.json`. The engine is replaced by a stub that exits
    non-zero, which is the refusal these tests are about — the point is that the
    record is there ANYWAY.
    """

    DISPATCHER = HOOKS / "factcheck-research-file.sh"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.home = Path(self.tmpdir) / "home"
        (self.home / ".claude" / "hooks").mkdir(parents=True)
        # A stub engine that FAILS, standing in for the real raise. If the
        # obligation were written by the engine (the fault), nothing would land.
        stub = self.home / ".claude" / "hooks" / "pre_plan_gates.py"
        stub.write_text(
            "import sys\n"
            "print('No active topic for session', file=sys.stderr)\n"
            "sys.exit(1)\n",
            encoding="utf-8",
        )

    def _dispatch(self, file_path, session_id="11111111-1111-1111-1111-111111111111"):
        payload = json.dumps({
            "tool_name": "Write",
            "session_id": session_id,
            "tool_input": {"file_path": file_path},
        })
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("CLAUDE_CODE_REMOTE", None)
        proc = subprocess.run(
            ["/bin/bash", str(self.DISPATCHER)],
            input=payload, capture_output=True, text=True, env=env, timeout=60,
        )
        return proc

    def _ledger(self, session_id="11111111-1111-1111-1111-111111111111"):
        return (self.home / ".claude" / "state" / "research_obligations"
                / f"{session_id}.ledger")

    def test_an_obligation_exists_after_a_dispatch_that_raised(self):
        """A1's headline case. The engine stub exits non-zero — the refusal — and
        the record of the obligation is on disk regardless, naming the artifact.

        Red against the pre-A1 dispatcher: it wrote no ledger at all, so the only
        trace of this dispatch was a /tmp log nothing reads."""
        draft = "/x/Thoughts/alpha-20260101010101_RESEARCH.md"
        proc = self._dispatch(draft)
        self.assertEqual(proc.returncode, 0,
                         f"PostToolUse cannot block; {proc.stderr!r}")
        ledger = self._ledger()
        self.assertTrue(ledger.exists(),
                        "the dispatcher must record the obligation itself — the "
                        "engine is exactly the component that may not get there")
        body = ledger.read_text(encoding="utf-8")
        self.assertIn(draft, body,
                      "the record must NAME the artifact; 'something was owed' "
                      "is the state the gate already cannot act on")

    def test_the_engine_really_did_refuse(self):
        """Precondition for the case above: if the stub succeeded, that test
        would pass for the wrong reason."""
        stub = self.home / ".claude" / "hooks" / "pre_plan_gates.py"
        out = subprocess.run([sys.executable, str(stub)],
                             capture_output=True, text=True, timeout=30)
        self.assertNotEqual(out.returncode, 0)

    def test_a_quoted_and_spaced_path_yields_exactly_one_record(self):
        """A1 guard rail — format safety. A path carrying a quote, a space and a
        backslash must not be able to split one record into two, or forge a
        second. This is why the append is `printf '%s\\t%s\\t%s\\n'` and never a
        bare interpolated `echo`."""
        draft = '/x/Thoughts/a b"c\\d-20260101010101_RESEARCH.md'
        proc = self._dispatch(draft)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = [ln for ln in
                 self._ledger().read_text(encoding="utf-8").splitlines() if ln]
        self.assertEqual(len(lines), 1,
                         f"one dispatch is one record; got {lines!r}")
        self.assertEqual(len(lines[0].split("\t")), 3,
                         f"three tab-separated fields; got {lines[0]!r}")
        self.assertEqual(lines[0].split("\t")[2], draft,
                         "the path field round-trips verbatim")

    def test_two_dispatches_append_rather_than_overwrite(self):
        """The ledger is a record of every obligation in the session, so the
        second dispatch must not erase the first — the same truncation fault F5
        fixes in the /tmp log one line below the append."""
        a = "/x/Thoughts/alpha-20260101010101_RESEARCH.md"
        b = "/x/Thoughts/bravo-20260101010101_RESEARCH.md"
        self._dispatch(a)
        self._dispatch(b)
        lines = [ln for ln in
                 self._ledger().read_text(encoding="utf-8").splitlines() if ln]
        self.assertEqual(len(lines), 2, f"both obligations survive; got {lines!r}")
        self.assertIn(a, lines[0])
        self.assertIn(b, lines[1])

    def test_an_ineligible_path_mints_no_obligation(self):
        """Scope. The Desired Outcome is stated over reports that REACH the
        dispatcher; a path its own glob filter excludes mints no obligation, so
        nothing downstream can hold it back. Placing the append after the
        path-glob `case` is what delivers that."""
        self._dispatch("/x/Docs/notes.md")
        self.assertFalse(self._ledger().exists(),
                         "an ineligible path is not an unmet obligation")

    def test_the_dispatch_log_is_appended_not_truncated(self):
        """F5 — with `>` the second dispatch wiped the first one's traceback, so
        the only surviving record of a refusal was destroyed by the next save."""
        text = self.DISPATCHER.read_text(encoding="utf-8")
        self.assertIn('>> "/tmp/factcheck-research-${SESSION_ID}.log"', text)
        self.assertNotIn('> "/tmp/factcheck-research-${SESSION_ID}.log"',
                         text.replace('>> "/tmp/factcheck-research-${SESSION_ID}.log"', ""))


class GuardTestsTheValuesItUsesTests(unittest.TestCase):
    """A2 — guard on `_proj`/`_topic`, not on the discarded `state`.

    `state` is read nowhere after the guard; `_proj`/`_topic` are what flow into
    `topic_dir`. Guarding `state` refused a session whose slugs resolved fine but
    whose per-topic JSON was absent — a strictly broader trigger than "no bound
    topic", and one that took the dispatch sentinel down with it.

    The recovery is resolver-dependent by construction: against the production
    `_resolve_topic_default` these two states coincide, so the change is only
    observable through an injected resolver. That is a property of the fix, not a
    weakness of the test — the plan's own guard rail says so.
    """

    def _resolver_valid_slugs_no_state(self):
        return lambda _sid: ("projX", "topicY", None)

    def _resolver_empty_slugs(self):
        return lambda _sid: (None, None, None)

    def test_valid_slugs_with_no_state_no_longer_raise_at_factcheck_run(self):
        """Red against the pre-A2 guard, which raised on `state is None` even
        though both slugs were present and usable."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        draft = Path(tmp) / "alpha-20260101010101_RESEARCH.md"
        draft.write_text("# draft\n", encoding="utf-8")
        try:
            eng.factcheck_run(
                session_id="22222222-2222-2222-2222-222222222222",
                draft_path=str(draft),
                kind="research",
                state_dir=str(Path(tmp) / "state"),
                _checker_fn=_CountingChecker("PASS"),
                models=["sonnet"],
                _proj_topic_resolver=self._resolver_valid_slugs_no_state(),
            )
        except ValueError as e:
            self.assertNotIn(
                "No active topic", str(e),
                "valid slugs must not be refused for a missing per-topic JSON")

    def test_empty_slugs_still_raise_at_factcheck_run(self):
        """The guard is narrowed, not removed. A genuinely unresolvable session
        must still refuse — the plan's refusal (Guiding Policy 4) is that the
        engine is NOT made to run unbound."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        draft = Path(tmp) / "alpha-20260101010101_RESEARCH.md"
        draft.write_text("# draft\n", encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            eng.factcheck_run(
                session_id="33333333-3333-3333-3333-333333333333",
                draft_path=str(draft),
                kind="research",
                state_dir=str(Path(tmp) / "state"),
                _checker_fn=_CountingChecker("PASS"),
                models=["sonnet"],
                _proj_topic_resolver=self._resolver_empty_slugs(),
            )
        self.assertIn("No active topic", str(ctx.exception))

    def test_valid_slugs_with_no_state_no_longer_raise_at_the_accept_mirror(self):
        """The mirror site moves in the same commit. Its own docstring declares
        itself a mirror of factcheck_run's resolution, so leaving it behind would
        keep the shipped waiver unreachable in exactly the case the check
        refused — G3."""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        try:
            eng.accept_research_incomplete(
                session_id="44444444-4444-4444-4444-444444444444",
                reason="operator accepts, on the record",
                state_dir=str(Path(tmp) / "state"),
                _proj_topic_resolver=self._resolver_valid_slugs_no_state(),
            )
        except ValueError as e:
            self.assertNotIn(
                "No active topic", str(e),
                "the waiver must reach the case the check refused")

    def test_empty_slugs_still_raise_at_the_accept_mirror(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(ValueError) as ctx:
            eng.accept_research_incomplete(
                session_id="55555555-5555-5555-5555-555555555555",
                reason="operator accepts, on the record",
                state_dir=str(Path(tmp) / "state"),
                _proj_topic_resolver=self._resolver_empty_slugs(),
            )
        self.assertIn("No active topic", str(ctx.exception))


# ===========================================================================
# S2 — the gate reads the obligation, and the waiver releases it (A4 + A5).
#
# S1 wrote the record and nothing read it, so S1 changed no operator-visible
# behaviour by construction. These are the cases where it becomes visible.
# ===========================================================================


class _ObligationBase(unittest.TestCase):
    """An isolated HOME holding an obligation ledger of this suite's own making.

    The ledger is written here in the SHAPE the dispatcher writes rather than by
    running the dispatcher, because the two halves are separately pinned: the
    dispatcher's own format is asserted by `ObligationLedgerTests` above, and
    what these cases are about is the read. `test_the_reader_parses_the_shape_
    the_dispatcher_writes` is the joint that stops the two drifting.
    """

    GATE = HOOKS / "check-research-gate.sh"
    ENGINE = HOOKS / "_factcheck_engine.py"
    PPG = HOOKS / "pre_plan_gates.py"
    SID = "oblig-session"

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.home = Path(self.tmpdir) / "home"
        self.oblig = self.home / ".claude" / "state" / "research_obligations"
        self.oblig.mkdir(parents=True)
        self.research = Path(self.tmpdir) / "topicone-20260101010101_RESEARCH.md"
        self.research.write_text("# body\n", encoding="utf-8")

    def _ledger(self, *paths, sid=None):
        sid = sid or self.SID
        (self.oblig / f"{sid}.ledger").write_text(
            "".join(f"2026-09-06T10:00:00Z\t{sid}\t{p}\n" for p in paths),
            encoding="utf-8")

    def _env(self):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("RP_STATE_DIR", None)
        env.pop("CLAUDE_CODE_REMOTE", None)
        return env

    def _run_gate(self, sid=None):
        return subprocess.run(
            ["/bin/bash", str(self.GATE)],
            input=json.dumps({"session_id": sid or self.SID,
                              "stop_hook_active": False}),
            capture_output=True, text=True, env=self._env(), timeout=180)

    def _accept(self, *args, sid=None):
        return subprocess.run(
            ["/usr/bin/python3", str(self.PPG), "accept-research-incomplete",
             sid or self.SID, *args],
            capture_output=True, text=True, env=self._env(), timeout=180)


class ObligationBlocksTheCloseTests(_ObligationBase):
    """C1 / C2 / C3 / C7 / C8 / C9 — absence of evidence reads as UNMET."""

    def test_an_unbound_session_that_wrote_research_cannot_close(self):
        """The exact reported failure, stated as its inverse (C8).

        Pre-S2 this closes silently: with no topic bound the gate exits at
        `[ -z "$PROJ" ]` before it reaches the per-cycle loop at all, so a
        refusal and a session that did no research are the same state."""
        self._ledger(self.research)
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, f"stdout={proc.stdout!r}")

    def test_the_refusal_names_the_research_file(self):
        """C9 / C3 — the gate must say WHICH artifact is unmet, not merely that
        something is."""
        self._ledger(self.research)
        proc = self._run_gate()
        self.assertIn(str(self.research), proc.stderr)

    def test_the_refusal_states_what_to_do_next(self):
        """C4, and this is the surface that actually delivers it.

        A3's text reaches only /tmp/factcheck-research-<sid>.log, which nothing
        in the tree reads. This message is the one the operator sees, so the
        three routes and the background-run window are asserted HERE."""
        self._ledger(self.research)
        err = self._run_gate().stderr
        self.assertIn("What to do next", err)
        self.assertIn("accept-research-incomplete", err)
        self.assertIn("--reason", err)
        self.assertIn("factcheck-research", err)
        self.assertIn("hand-rolled", err,
                      "the sanctioned path is named where the operator chooses")
        self.assertIn("30-60s", err,
                      "a block during the async window must read as expected, "
                      "not as a bug")

    def test_a_session_that_wrote_no_research_closes_clean(self):
        """The no-false-block rail. 'No check was owed' is not 'the answer could
        not be read', and only the second may block — without this the change
        would block every session in the tree."""
        proc = self._run_gate(sid="session-that-did-nothing")
        self.assertEqual(proc.returncode, 0, f"stderr={proc.stderr!r}")

    def test_an_unreadable_ledger_row_blocks_rather_than_vanishing(self):
        """Fail-closed, pointed the same way as everything else here: a record
        that cannot be read must not read as no record."""
        (self.oblig / f"{self.SID}.ledger").write_text("not a record\n",
                                                       encoding="utf-8")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2)
        self.assertIn("could not be read", proc.stderr)
        self.assertIn("mv ", proc.stderr,
                      "a row that cannot be named to the accept verb still "
                      "needs a stated exit")

    def test_a_missing_engine_blocks_when_something_was_owed(self):
        """The false branch of A4's `PYBIN`/`ENGINE` guard, which the 0G round
        recorded as unstated. It is fail-CLOSED, and only past the point where
        an obligation exists — a session with no ledger never reaches it."""
        shim = Path(self.tmpdir) / "shim"
        shim.mkdir()
        (shim / "check-research-gate.sh").write_text(
            self.GATE.read_text(encoding="utf-8"), encoding="utf-8")
        self._ledger(self.research)
        proc = subprocess.run(
            ["/bin/bash", str(shim / "check-research-gate.sh")],
            input=json.dumps({"session_id": self.SID, "stop_hook_active": False}),
            capture_output=True, text=True, env=self._env(), timeout=180)
        self.assertEqual(proc.returncode, 2, f"stderr={proc.stderr!r}")
        self.assertIn("cannot determine", proc.stderr)


class ObligationWaiverReleasesTheBlockTests(_ObligationBase):
    """C5 / C6 — every block has a recorded exit, or the fix trades a silent
    failure for a stuck session."""

    def test_a_waiver_with_a_reason_lets_the_session_close(self):
        self._ledger(self.research)
        self.assertEqual(self._run_gate().returncode, 2, "precondition")
        acc = self._accept("--file", str(self.research),
                           "--reason", "no topic bound; accepted knowingly")
        self.assertEqual(acc.returncode, 0, acc.stderr)
        self.assertEqual(self._run_gate().returncode, 0)

    def test_the_waiver_is_recorded_with_the_operators_own_reason(self):
        self._ledger(self.research)
        self._accept("--file", str(self.research), "--reason", "REASON-SENTINEL")
        rec = json.loads(
            (self.oblig / f"{self.SID}.waivers").read_text(encoding="utf-8")
            .splitlines()[0])
        self.assertEqual(rec["reason"], "REASON-SENTINEL")
        self.assertEqual(rec["file"], str(self.research))

    def test_a_waiver_without_a_reason_is_refused(self):
        self._ledger(self.research)
        acc = self._accept("--file", str(self.research), "--reason", "   ")
        self.assertNotEqual(acc.returncode, 0)
        self.assertFalse((self.oblig / f"{self.SID}.waivers").exists())
        self.assertEqual(self._run_gate().returncode, 2,
                         "an unreasoned attempt must not clear the block")

    def test_the_waiver_does_not_require_the_artifact_to_still_exist(self):
        """The write-then-revert case, which killed the first design: an
        obligation for a file that no longer exists must still be clearable."""
        self._ledger(self.research)
        self.research.unlink()
        acc = self._accept("--file", str(self.research), "--reason", "reverted")
        self.assertEqual(acc.returncode, 0, acc.stderr)
        self.assertEqual(self._run_gate().returncode, 0)

    def test_two_obligations_refuse_an_unnamed_accept(self):
        """Same asymmetry `_accept_target_slot` states: a refusal is reversible,
        a wrong accept SANCTIONS a file nobody named and is not."""
        other = Path(self.tmpdir) / "topictwo-20260101010101_RESEARCH.md"
        other.write_text("# body\n", encoding="utf-8")
        self._ledger(self.research, other)
        acc = self._accept("--reason", "clearing both")
        self.assertNotEqual(acc.returncode, 0)
        self.assertIn("--file", acc.stderr)
        self.assertIn(str(self.research), acc.stderr)
        self.assertIn(str(other), acc.stderr)

    def test_waiving_one_of_two_leaves_the_other_blocking(self):
        other = Path(self.tmpdir) / "topictwo-20260101010101_RESEARCH.md"
        other.write_text("# body\n", encoding="utf-8")
        self._ledger(self.research, other)
        self._accept("--file", str(self.research), "--reason", "just this one")
        proc = self._run_gate()
        self.assertEqual(proc.returncode, 2, "the sibling still owes a check")
        self.assertIn(str(other), proc.stderr)
        self.assertNotIn(str(self.research), proc.stderr,
                         "the waived file is not re-reported")

    def test_a_file_the_ledger_does_not_name_is_refused(self):
        """A mistyped path must not mint a waiver that reads as sanctioned."""
        self._ledger(self.research)
        acc = self._accept("--file", "/nowhere/typo-20260101010101_RESEARCH.md",
                           "--reason", "typo")
        self.assertNotEqual(acc.returncode, 0)
        self.assertIn("obligation ledger", acc.stderr)
        self.assertEqual(self._run_gate().returncode, 2)


class ObligationReaderTests(_ObligationBase):
    """The verb itself — read-only, and classifying rather than deciding."""

    def _verb(self, sid=None, extra=()):
        return subprocess.run(
            ["/usr/bin/python3", str(self.ENGINE), "research-obligations",
             sid or self.SID, *extra],
            capture_output=True, text=True, env=self._env(), timeout=180)

    def test_the_reader_parses_the_shape_the_dispatcher_writes(self):
        """The joint between A1's writer and A4's reader. Derives the record
        from the dispatcher's own `printf` format string rather than restating
        it, so a change to one side fails here instead of silently producing a
        ledger nobody can read."""
        text = (HOOKS / "factcheck-research-file.sh").read_text(encoding="utf-8")
        self.assertIn("printf '%s\\t%s\\t%s\\n'", text)
        self._ledger(self.research)
        payload = json.loads(self._verb().stdout)
        self.assertEqual([u["file"] for u in payload["unmet"]],
                         [str(self.research)])

    def test_exit_two_carries_status_ok_so_an_older_engine_is_distinguishable(self):
        """`_main` returns 2 for an unknown command too, with no JSON at all.
        A gate keying on the code alone would read that as 'unmet obligations'
        and render an empty list, so the status field is what separates them."""
        self._ledger(self.research)
        proc = self._verb()
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stdout)["status"], "OK")

        unknown = subprocess.run(
            ["/usr/bin/python3", str(self.ENGINE), "no-such-verb"],
            capture_output=True, text=True, env=self._env(), timeout=60)
        self.assertEqual(unknown.returncode, 2)
        with self.assertRaises(ValueError):
            json.loads(unknown.stdout or "")

    def test_a_clean_session_exits_zero(self):
        self.assertEqual(self._verb(sid="nothing-owed").returncode, 0)

    def test_a_path_containing_a_tab_is_rejoined_not_truncated(self):
        """The path is the last field, so everything after the second tab
        belongs to it. Splitting on the first tab would silently key a
        different file."""
        odd = Path(self.tmpdir) / "with\ttab-20260101010101_RESEARCH.md"
        (self.oblig / f"{self.SID}.ledger").write_text(
            f"2026-09-06T10:00:00Z\t{self.SID}\t{odd}\n", encoding="utf-8")
        payload = json.loads(self._verb().stdout)
        self.assertEqual([u["file"] for u in payload["unmet"]], [str(odd)])

    def test_a_covered_file_is_not_reported_twice(self):
        """A file the rollup already has a row for is left to the rollup. Both
        readers naming it would print one file twice in one block message, and
        would let this reader second-guess a verdict that is not its business."""
        proj, topic = "TestOb", "obligations"
        state = self.home / ".claude" / "state" / "pre_plan_gates"
        state.mkdir(parents=True, exist_ok=True)
        (state / "_active.json").write_text(json.dumps(
            {self.SID: {"topic_slug": proj, "active_project": topic}}),
            encoding="utf-8")
        (state / f"{proj}__{topic}.json").write_text(
            json.dumps({"topic_slug": proj, "project_slug": topic}),
            encoding="utf-8")
        base = (self.home / ".claude" / "state" / "plan_validation" / proj
                / topic / "research")
        base.mkdir(parents=True)
        key = eng._research_file_key(self.research.name)
        (base / f"{key}_R1.md").write_text(
            "---\nschema_version: 3\nverdict: ESCALATE\nrounds: 1\n---\n",
            encoding="utf-8")

        self._ledger(self.research)
        payload = json.loads(self._verb().stdout)
        self.assertTrue(payload["bound"])
        self.assertEqual(payload["unmet"], [])
        self.assertEqual([c["file"] for c in payload["covered"]],
                         [str(self.research)])

    def test_the_reader_writes_nothing(self):
        """It is a reader. A reader that mutates the record it reads would make
        the close gate a writer of the evidence it decides on."""
        self._ledger(self.research)
        before = sorted((p.name, p.read_bytes()) for p in self.oblig.iterdir())
        self._verb()
        after = sorted((p.name, p.read_bytes()) for p in self.oblig.iterdir())
        self.assertEqual(before, after)


class WaiverIsAppendOnlyTests(_ObligationBase):
    """`safe-defaults.md` — a retirement is a compensating record, never an
    edit. The obligation itself survives its own waiver, which is what makes a
    wrong call auditable rather than invisible."""

    def test_the_ledger_is_untouched_by_a_waiver(self):
        self._ledger(self.research)
        before = (self.oblig / f"{self.SID}.ledger").read_bytes()
        self._accept("--file", str(self.research), "--reason", "accepted")
        self.assertEqual((self.oblig / f"{self.SID}.ledger").read_bytes(),
                         before)

    def test_a_second_waiver_appends_rather_than_replacing(self):
        other = Path(self.tmpdir) / "topictwo-20260101010101_RESEARCH.md"
        other.write_text("# body\n", encoding="utf-8")
        self._ledger(self.research, other)
        self._accept("--file", str(self.research), "--reason", "first")
        self._accept("--file", str(other), "--reason", "second")
        lines = (self.oblig / f"{self.SID}.waivers").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual([json.loads(x)["reason"] for x in lines],
                         ["first", "second"])

    def test_a_reason_containing_a_newline_stays_one_record(self):
        """Why the waiver is JSON and the ledger is not: the reason is free text
        the operator wrote. A tab-separated waiver split by a newline would be a
        waiver for a file nobody named."""
        self._ledger(self.research)
        self._accept("--file", str(self.research),
                     "--reason", "line one\nline two\ttabbed")
        lines = (self.oblig / f"{self.SID}.waivers").read_text(
            encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["reason"],
                         "line one\nline two\ttabbed")

    def test_an_unparseable_waiver_line_does_not_clear(self):
        """The asymmetry with the ledger reader, pointed the same way: an
        unreadable obligation still blocks, an unreadable waiver still does not
        clear."""
        self._ledger(self.research)
        (self.oblig / f"{self.SID}.waivers").write_text(
            "{not json at all\n", encoding="utf-8")
        self.assertEqual(self._run_gate().returncode, 2)

    def test_a_waiver_with_a_blank_reason_does_not_clear(self):
        """Mirrors `_row_is_sanctioned`: whitespace is not a reason, so a blank
        field cannot launder an unchecked file into a pass — even when the
        record was hand-written rather than minted by the verb."""
        self._ledger(self.research)
        key = eng._research_file_key(self.research.name)
        (self.oblig / f"{self.SID}.waivers").write_text(
            json.dumps({"key": key, "reason": "   ", "file": str(self.research)})
            + "\n", encoding="utf-8")
        self.assertEqual(self._run_gate().returncode, 2)


class BoundAcceptIsUnchangedTests(_PerFileBase):
    """A5 extends the verb; it does not re-shape the case that already worked.

    The marker arm still writes a keyed marker for a bound topic with an
    evidence group. What is ADDED is the waiver beside it — because the two
    readers are different, and clearing one while the other still blocks would
    leave the operator with a block they had already answered."""

    def _keyed(self, base, key, rounds):
        base.mkdir(parents=True, exist_ok=True)
        for n, v in enumerate(rounds, start=1):
            (base / f"{key}_R{n}.md").write_text(
                f"---\nschema_version: 3\nverdict: {v}\nrounds: {n}\n---\n",
                encoding="utf-8")

    def test_the_marker_arm_still_writes_a_keyed_marker(self):
        base = self._research_base()
        key_a = eng._research_file_key(self.file_a.name)
        self._keyed(base, key_a, ["ESCALATE"])
        res = eng.accept_research_incomplete(
            self.state_dir, self.SID, "accepted to close",
            _proj_topic_resolver=self.resolver,
            research_file=str(self.file_a),
            obligations_root=str(Path(self.tmpdir) / "oblig"))
        self.assertEqual(res["scope"], "marker")
        self.assertEqual(res["key"], key_a)
        self.assertTrue(Path(res["marker"]).name.startswith(f"{key_a}_R2"))

    def test_the_marker_arm_also_retires_a_matching_ledger_record(self):
        oblig = Path(self.tmpdir) / "oblig"
        oblig.mkdir()
        key_a = eng._research_file_key(self.file_a.name)
        (oblig / f"{self.SID}.ledger").write_text(
            f"t\t{self.SID}\t{self.file_a}\n", encoding="utf-8")
        self._keyed(self._research_base(), key_a, ["ESCALATE"])
        res = eng.accept_research_incomplete(
            self.state_dir, self.SID, "accepted to close",
            _proj_topic_resolver=self.resolver,
            research_file=str(self.file_a), obligations_root=str(oblig))
        self.assertEqual(res["scope"], "marker")
        self.assertIn("waiver", res)
        self.assertEqual(
            eng.read_obligation_waivers(self.SID, str(oblig)).keys(), {key_a})

    def test_a_wrong_path_still_gets_the_shipped_refusal(self):
        """The ledger arm is narrow by construction: it fires only for a file
        the ledger actually names, so `_accept_target_slot`'s own refusal is
        unchanged for every file it does not."""
        base = self._research_base()
        self._keyed(base, eng._research_file_key(self.file_a.name), ["ESCALATE"])
        with self.assertRaises(ValueError) as ctx:
            eng.accept_research_incomplete(
                self.state_dir, self.SID, "accepted to close",
                _proj_topic_resolver=self.resolver,
                research_file=str(self.file_b),
                obligations_root=str(Path(self.tmpdir) / "oblig"))
        self.assertIn("nothing to accept for it", str(ctx.exception))

    def test_a_bound_topic_whose_engine_refused_can_still_be_cleared(self):
        """The bound half of the diagnosed fault: slugs resolve, but the engine
        declined before writing anything, so there is no evidence group to
        number into and the shipped verb raises. The ledger is the only record
        that a check was owed."""
        oblig = Path(self.tmpdir) / "oblig"
        oblig.mkdir()
        (oblig / f"{self.SID}.ledger").write_text(
            f"t\t{self.SID}\t{self.file_a}\n", encoding="utf-8")
        res = eng.accept_research_incomplete(
            self.state_dir, self.SID, "engine refused; accepted",
            _proj_topic_resolver=self.resolver,
            research_file=str(self.file_a), obligations_root=str(oblig))
        self.assertEqual(res["scope"], "ledger")
        self.assertIsNone(res["marker"])


if __name__ == "__main__":
    unittest.main()

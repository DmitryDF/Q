"""V1 — closing verification for research-source-adapters S10.

The plan's V1 row asks for ONE WALK OVER A REAL DECLARED READ, revert-checked
per link, and warns that "a uniform pass across arms is a defect in the check".
Its guard rail says why: "Drive a REAL source — this topic's fixtures hid three
defects only a live workspace exposed."

**CARVED BY D5 (2026-09-01) — S10 IS DESCOPED AND REVERSED.** The per-claim
grading layer this file was written to verify no longer exists: the operator
decided on 2026-08-30 that a scoring layer has no value for their use, design
A8/A13/A25 are withdrawn, and `internal_claim_grader.py`,
`claim_grading_record.py` and `internal_claim_grounds.py` are deleted along with
the close-time gate. Record: spine `## Q&A` Q22 (decision) / Q23 (disposition).

Four classes went with the layer — `A2RealCitationResolvesTests`,
`RealFloorTruthTableTests`, `A3RealRecordPersistsTests` and
`A5RealPipelineTests` — plus one member of `RealWalkFindingsTests`. **The
carve-out was keyed on BEHAVIOUR, not imports**, which is load-bearing:
`A5RealPipelineTests` imported none of the three modules and still asserted
`claim_grading_axis`, globbed `*_GRADING_*.md` and drove a floor-driven
`INCOMPLETE` through `factcheck_run`. An import-keyed sweep would have left it
here, and this file would fail while the removal's own gate reported success.

**What survives, and why it was worth protecting.** The remaining tests exercise
the RETAINED admission layer, which D5 does not touch: the port records how
narrowly a source was declared, and a pin still names the real commit. Two of
them — `test_the_two_production_stores_differ_in_whether_runs_are_isolated` and
`test_a_scope_keyed_research_file_falls_back_to_one_global_store` — characterise
the admission-record FILING defect that slice **D6** exists to repair, so they
are that slice's baseline evidence. Deleting this file wholesale, which an
import-keyed reading would have invited, would have destroyed the next slice's
own starting point.

So the WALK below rests on a REAL git repository, driving the REAL
`AdmissionPort` with the REAL `CodeBaseAdapter`, producing REAL pins at a REAL
commit; nothing about the source is stubbed.

**Two tests are deliberately NOT part of that walk, and saying so is not a
footnote.** `test_the_two_production_stores_differ_in_whether_runs_are_isolated`
and `test_a_scope_keyed_research_file_falls_back_to_one_global_store`
characterise how the two production admission stores are CONSTRUCTED — which
timestamp each derives from, and which names fall back to the global default.
What is true of BOTH is the part that matters: neither reads the repository,
calls the adapter or the port, or produces a pin. They differ in technique —
the first inspects production source and a function signature, the second
constructs stores directly from synthetic filenames — and the difference is
spelled out because a previous version of this paragraph attributed the first
one's technique to both.

That is the THIRD correction this docstring has taken, and all three were the
same shape. It first said "every assertion below" rests on a real read, false
for two tests; then that both of those use `inspect`, true of one; then that
every remaining test drives the port, true of sixteen of seventeen. A checker
caught each; none was caught by the author.

They are corrected here rather than deleted, because a closing verification that
overstates its own reach is the specific failure this file exists to avoid — and
because the shape is the real finding: A STATEMENT TRUE OF THE INSTANCE IN MIND,
WRITTEN AS THOUGH IT WERE TRUE OF THE SET. It appeared five times across five
review rounds, and the same habit produced the one genuine code defect this walk
found — a claim about how production stores are built, generalised from the
single constructor that had been looked at. Where this file makes a universal
claim, the universal is the part most likely to be wrong.

Two further things are not real, and both are named rather than glossed:

* **The content checker** is injected, as in every sibling fact-check test. No
  model is called. What is exercised is the engine's own orchestration.
* **The repository is a shallow CLONE** of the live one for the arms that need
  a dirty working tree. Dirtying a live repo to make a test pass would be the
  destructive shortcut `safe-defaults.md` exists to refuse. The clone is real
  git with real history; only its location differs.

WHAT THIS FILE FOUND THAT THE FIXTURES DID NOT — the point of the exercise:

1. **The reader reads whole files, so every pin's line range starts at 1.** That
   is a property of design-A20's unbuilt narrowed-excerpt half. *(Written
   originally as a finding about the grading floor's traceability axis; restated
   by D5 as the fact about the READER that it always was, since the floor is
   gone and the reader's behaviour is not. It remains invisible to any fixture
   that hand-writes a narrowed pin, as this topic's other suites do.)*
2. **An unscoped `code` declaration enumerates nothing.** `selectors_for`
   returns empty, so `enumerate_within` yields no items — so an unscoped `code`
   read is reachable only through a caller that supplies items directly to
   `admit()`, never through `run()`. Asserted below, so it cannot quietly stop
   being true.

*(Finding 1's assertion went with `RealWalkFindingsTests`'
`test_every_pin_a_real_code_read_produces_is_traceability_weak`, which reached
it through the deleted grader. It is stated here as prose rather than silently
dropped — and the honest consequence is that it CAN now quietly stop being true.
Re-asserting it against the reader directly, with no grading vocabulary, is a
loose end this carve-out leaves rather than closes.)*
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

import _factcheck_engine as eng                       # noqa: E402
from research import admission_record as arec         # noqa: E402
from research import scope_record as srec             # noqa: E402
from research import source_port as sp                # noqa: E402
from research.adapters.code_base import CodeBaseAdapter   # noqa: E402

# A real repository this session has been promoting into all evening. Read
# ONLY — every arm that needs to mutate a tree works on a clone.
LIVE_REPO = Path("<config-source-repo>")
DECLARED_SUBDIR = Path("dot_claude") / "skills" / "research" / "adapters"


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


def _unscoped_code_scope():
    return srec.ScopeRecord(sources=(
        srec.DeclaredSource(kind=srec.KIND_CODE,
                            scope_mode=srec.SCOPE_MODE_UNSCOPED),))


@unittest.skipUnless((LIVE_REPO / ".git").exists(),
                     f"the real repository is not present at {LIVE_REPO}")
class _RealRepoBase(unittest.TestCase):
    """One shallow clone per class. Real git, real history, real files."""

    @classmethod
    def setUpClass(cls):
        cls._root = Path(tempfile.mkdtemp(prefix="s10-v1-"))
        cls.repo = cls._root / "clone"
        subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1",
             str(LIVE_REPO), str(cls.repo)],
            check=True, capture_output=True)
        cls.declared = cls.repo / DECLARED_SUBDIR
        cls.target = cls.declared / "code_base.py"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls._root, ignore_errors=True)

    def setUp(self):
        # Restore the clone to a clean tree before every arm, so one arm's
        # dirtying cannot leak into the next and make a later result untrue.
        _git(self.repo, "checkout", "--", ".")
        self.tmp = Path(tempfile.mkdtemp(prefix="s10-v1-run-"))
        self.store = arec.AdmissionRecordStore(self.tmp / "records", slug="v1",
                                               ts="20260101000000")
        self.port = sp.AdmissionPort(self.store)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dirty(self):
        self.target.write_text(
            self.target.read_text(encoding="utf-8") + "\n# v1 marker\n",
            encoding="utf-8")

    def _admit_target(self, scope, run_id):
        item = sp.SourceItem(item_id=str(self.target), kind=srec.KIND_CODE,
                             target=str(self.target), depth=0)
        return self.port.admit(item, scope, CodeBaseAdapter(), run_id)

    def _enumerated(self):
        return srec.code_scope([self.declared])


# ===========================================================================
# Link A1 — the port records how narrowly a REAL source was bounded
# ===========================================================================
class A1RealReadRecordsTheBoundTests(_RealRepoBase):
    def test_a_real_enumerated_read_records_its_bound_and_selector(self):
        result = self._admit_target(self._enumerated(), "real-1")

        self.assertTrue(result.admitted, result.degradation)
        self.assertEqual(result.declaration_bound, srec.SCOPE_MODE_ENUMERATED)
        self.assertEqual(result.matched_selector, str(self.declared.resolve()))

        entry = self.store.get_evidence(result.pin_str)
        self.assertIsNotNone(entry, "the real read left a retrievable record")
        self.assertEqual(entry.declaration_bound, srec.SCOPE_MODE_ENUMERATED)

    def test_a_real_unscoped_read_records_unscoped_with_no_selector(self):
        result = self._admit_target(_unscoped_code_scope(), "real-2")

        self.assertTrue(result.admitted, result.degradation)
        self.assertEqual(result.declaration_bound, srec.SCOPE_MODE_UNSCOPED)
        self.assertIsNone(result.matched_selector)

    def test_the_real_pin_names_the_real_commit(self):
        head = _git(self.repo, "rev-parse", "HEAD")
        result = self._admit_target(self._enumerated(), "real-3")

        self.assertIn(head, result.pin_str,
                      "the pin must address the commit that was actually read")
        self.assertTrue(result.pin_str.startswith("code:"))


# ===========================================================================
# What the real walk found — recorded as assertions, not prose
# ===========================================================================
class RealWalkFindingsTests(_RealRepoBase):

    def test_two_reads_of_one_commit_under_different_declarations_share_a_pin(self):
        """FINDING 3 — a real DEFECT, and the one only a repeated real read shows.

        A pin addresses the SOURCE STATE: repository, commit, path, lines. It
        does not address the READ. So admitting the same file at the same commit
        under an ENUMERATED declaration and again under an UNSCOPED one mints
        two identical pins, and `AdmissionRecordStore` keys evidence on the pin
        alone. The second read's grounds are shadowed by the first's, and
        `get_evidence` — which searches runs in order — hands back a declaration
        bound that did not produce the claim.

        **The direction of the error is the unsafe one.** A later, weaker read
        resolves to an earlier, stronger read's bound, so the floor UNDER-fires:
        a claim that should have its confirmation withheld is confirmed.

        **Reachability today is bounded, and that bound is not a fix.** In
        production each fact-check run builds its store with `ts=now`, and
        `run_ids()` globs only that stem, so runs do not currently see each
        other's records. But cross-run resolution is the accessor's stated
        purpose — "the pin is the address a later reader holds; they have the
        citation, not the run identity" — and S11–S13 are specified to read
        these records later. The collision is latent under exactly the use the
        accessor exists for.

        This is characterised, not repaired: keying or pin grammar is a design
        question for a slice with a mandate, and V1's is to verify and report.
        """
        clean_pin = self._admit_target(self._enumerated(), "read-a").pin_str
        second = self._admit_target(_unscoped_code_scope(), "read-b")

        self.assertEqual(second.pin_str, clean_pin,
                         "two reads of one commit no longer share a pin — "
                         "finding 3 may have been fixed; revisit it")
        self.assertEqual(second.declaration_bound, srec.SCOPE_MODE_UNSCOPED,
                         "the read itself knows it was unscoped")

        resolved = self.store.get_evidence(second.pin_str)
        self.assertEqual(resolved.declaration_bound, srec.SCOPE_MODE_ENUMERATED,
                         "but the record resolves to the FIRST read's bound")

        # (A trailing assertion here used to carry the consequence through the
        # grading channel. D5 removed that channel; the pin-collision facts
        # above stand on their own and are D6's baseline evidence.)

    def test_the_two_production_stores_differ_in_whether_runs_are_isolated(self):
        """FINDING 3, continued — how reachable the pin collision actually is.

        An earlier version of this walk said "each production fact-check run
        builds its store with a current timestamp and searches only that stem,
        so runs do not see each other's records". That was true of ONE of the
        two production constructors and false of the other, and a checker asked
        whether the finding was understated caught it.

        * `_factcheck_engine._resolve_web_admission` stamps its store with
          `datetime.now()`, so successive runs land in different files and are
          isolated.
        * `declared_read.durable_store_for` derives its stamp from the research
          FILE's own persisted timestamp, so successive reads of one
          `_RESEARCH.md` land in the SAME file. Runs there are not isolated at
          all.

        What prevents the collision on that second path today is weaker than
        isolation: `read_declared_sources` defaults `run_id` to a fixed
        constant, and `put_evidence` overwrites by pin within one run, so a
        re-read overwrites rather than shadows. That mitigation is a default
        parameter value, not a structural property — it stops working as soon
        as a caller passes a per-invocation run id into the stable store.

        Asserted so the distinction cannot quietly change in either direction.
        """
        import inspect

        from research import declared_read as dr

        # The engine's store: stamped from the clock.
        engine_src = inspect.getsource(eng._resolve_web_admission)
        self.assertIn("datetime.now(", engine_src,
                      "the web store no longer stamps from the clock — the "
                      "isolation half of finding 3 needs revisiting")

        # The declared-read store: stamped from the file, hence STABLE.
        research = self.tmp / "demo-20260101010101_RESEARCH.md"
        research.write_text("# R\n", encoding="utf-8")
        first = dr.durable_store_for(str(research))
        second = dr.durable_store_for(str(research))
        self.assertEqual(first.run_path("declared-read"),
                         second.run_path("declared-read"),
                         "the declared-read store is no longer stable across "
                         "invocations — finding 3's reachability needs revisiting")

        # And the mitigation that stands in for isolation there.
        self.assertEqual(
            inspect.signature(dr.read_declared_sources)
            .parameters["run_id"].default,
            "declared-read",
            "the fixed run_id default is what keeps a re-read overwriting "
            "rather than shadowing; if it becomes per-invocation, the stable "
            "store makes the collision reachable")

    def test_a_timestamped_scope_keyed_research_file_files_under_its_own_topic(self):
        """FINDING 3, REPAIRED by D6 — this was the worst reachability path.

        What it characterised: `durable_store_for` matched the stem against
        ``\\A(?P<slug>[a-z0-9-]+?)(?:-(?P<ts>\\d{14}))?\\Z`` — lowercase-kebab
        only, so it could not match a Bucket-2 scope-keyed name
        (``<slug>-<ts>_<SCOPE>_RESEARCH.md``), which `bookkeeping-model.md` §4
        sanctions and this topic's own `Thoughts/` folder holds three of. Those
        names took the BARE `AdmissionRecordStore()` fallback, which discards
        slug AND timestamp; combined with `read_declared_sources`' fixed
        `run_id` default, every scope-keyed research file in EVERY topic
        resolved to one identical record path. The collision was between
        unrelated topics, not between two reads of one file.

        Two loci had to move together, which is why the assertions below check
        both. Widening only the resolver would have given this topic's three
        same-timestamp siblings one identical stem — converting a cross-topic
        collision into a within-topic one, and regressing the bare-name sibling
        that filed correctly all along. So the record's NAME gained the scope
        segment as well (`AdmissionRecordStore.scope_segment`).
        """
        from research import declared_read as dr

        bare = arec.AdmissionRecordStore()

        plain = dr.durable_store_for(
            str(self.tmp / "demo-20260101010101_RESEARCH.md"))
        self.assertNotEqual(plain.directory, bare.directory,
                            "a plain Bucket-1 name should NOT fall back")
        self.assertEqual(plain.slug, "demo")

        scoped = dr.durable_store_for(
            str(self.tmp / "demo-20260101010101_DRIVEAUTH_RESEARCH.md"))
        self.assertNotEqual(scoped.directory, bare.directory,
                            "a timestamped scope-keyed name must file under its "
                            "own topic folder, not the shared fallback")
        self.assertEqual(scoped.directory, self.tmp)
        self.assertEqual(scoped.slug, "demo")
        self.assertEqual(scoped.ts, "20260101010101")
        self.assertEqual(scoped.scope_segment, "DRIVEAUTH")

        # The second locus: the scope reaches the record's NAME. Without this
        # the three same-stamp siblings of one slug still collapse onto one
        # record — a within-topic collision replacing the cross-topic one.
        self.assertEqual(
            scoped.run_path("declared-read").name,
            "demo-20260101010101_DRIVEAUTH_ADMISSION_declared-read.md")
        self.assertNotEqual(scoped.run_path("declared-read"),
                            plain.run_path("declared-read"),
                            "a scope-keyed file and its bare-name sibling share "
                            "a slug and a timestamp, so only the scope segment "
                            "keeps their records apart")

        # And the cross-topic collision is gone: two UNRELATED topics, two files.
        alpha = dr.durable_store_for(
            str(self.tmp / "topic-alpha-20260101010101_SCOPEA_RESEARCH.md"))
        beta = dr.durable_store_for(
            str(self.tmp / "topic-beta-20260202020202_SCOPEB_RESEARCH.md"))
        self.assertNotEqual(alpha.run_path("declared-read"),
                            beta.run_path("declared-read"),
                            "unrelated topics must not share one record file")

    def test_a_scope_keyed_name_without_a_timestamp_still_falls_back(self):
        """The non-regression D6 had NO test standing between it and a widening.

        A scope is admitted only when a 14-digit timestamp is present. That
        discriminator is what stops the non-greedy slug group capturing every
        ``<lowercase-kebab>_<anything>`` stem and MANUFACTURING a topic slug
        from a name that carries none — the failure three earlier drafts of
        this repair were withdrawn for. A confidently wrong slug is worse than
        an obviously generic shared one.

        It costs real coverage and that is the point of pinning it: 21 genuine
        Bucket-2 files carrying no timestamp keep the shared fallback, measured
        over the live corpus. `bookkeeping-model.md` §5 makes the timestamp
        mandatory for new files and optional only to grandfather existing ones,
        so this class is exactly "minted before the current grammar".

        Every scope-keyed fixture elsewhere in this suite carries a timestamp,
        so before this test nothing failed if the requirement were dropped.
        """
        from research import declared_read as dr

        bare = arec.AdmissionRecordStore()

        for name in ("claude-infra-overhaul_Q1_RESEARCH.md",
                     "per-app-network-routing_DNS_RESEARCH.md",
                     "workflow-phases-redesign_F_RESEARCH.md"):
            with self.subTest(name=name):
                store = dr.durable_store_for(str(self.tmp / name))
                self.assertEqual(store.directory, bare.directory,
                                 "a scope-keyed name with no timestamp must "
                                 "keep the shared fallback — admitting it "
                                 "manufactures a slug from a name that has none")
                self.assertEqual(store.slug, bare.slug)
                self.assertEqual(store.ts, "")
                self.assertEqual(store.scope_segment, "")

        # And the other half of "refuse rather than invent": a scope whose
        # FIRST component is a registered TYPE is not Bucket 2 at all (Bucket 2
        # puts SCOPE before TYPE), so it falls back even WITH a timestamp.
        mid_type = dr.durable_store_for(
            str(self.tmp / "economic-model-20260101010101_RESEARCH_C1_DE.md"))
        self.assertEqual(mid_type.directory, bare.directory)
        self.assertEqual(mid_type.slug, bare.slug)

    def test_an_unscoped_code_declaration_enumerates_nothing(self):
        """FINDING 2. `selectors_for` is empty for an unscoped record, so
        `enumerate_within` yields no items. The unscoped arm of the floor is
        reachable for `code` only through a caller supplying items directly to
        `admit()` — never through `run()`.
        """
        unscoped = _unscoped_code_scope()
        self.assertEqual(unscoped.selectors_for(srec.KIND_CODE), ())
        self.assertEqual(
            list(CodeBaseAdapter().enumerate_within(unscoped)), [],
            "an unscoped code declaration now enumerates — finding 2 is stale")

        # And the direct-admit path DOES reach it, which is what makes the
        # unscoped arms above legitimate rather than unreachable states.
        result = self._admit_target(unscoped, "finding-2")
        self.assertTrue(result.admitted)
        self.assertEqual(result.declaration_bound, srec.SCOPE_MODE_UNSCOPED)


if __name__ == "__main__":                             # pragma: no cover
    unittest.main()

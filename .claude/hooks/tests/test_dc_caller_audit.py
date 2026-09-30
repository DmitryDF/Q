#!/usr/bin/env python3
"""Tests for the caller-audit enumeration registry scan tool (Slice S7).

Plan: Thoughts/double-check-validation-targeting_S7_PLAN.md (Coherent Actions A1).

Coverage:
  C2  the scan tool re-derives the corpus from disk and reports DETERMINISTICALLY
      every newly-discovered (on disk, not in registry), every stale (in registry,
      not on disk), and every unclassified (referencing & not validly classified)
      entry; a non-referencing entry auto-classifies `not-applicable` (never
      unclassified).
  C3  the done criterion is encoded + asserted by the tool — the audit passes only
      on TWO consecutive scans with zero unclassified AND zero newly-discovered
      entries (enumeration-exhaustion); any dirty scan resets the counter.

All against a synthetic FIXTURE corpus (never the live ~/.claude corpus), with the
registry + scan-state redirected to a tmp dir.
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dc_caller_audit as dca  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixture corpus helpers
# --------------------------------------------------------------------------- #

REFERENCING = "This skill invokes /double-check 1,1,1 against a concrete predicate."
NON_REFERENCING = "An ordinary helper hook. No validation engine reference here."


def _build_corpus(root: Path, *, skills, rules, hooks):
    """skills/rules/hooks: list of (name, body). Writes the fixture tree."""
    for name, body in skills:
        d = root / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
    (root / "rules").mkdir(parents=True, exist_ok=True)
    for name, body in rules:
        (root / "rules" / name).write_text(body, encoding="utf-8")
    (root / "hooks").mkdir(parents=True, exist_ok=True)
    for name, body in hooks:
        (root / "hooks" / name).write_text(body, encoding="utf-8")


def _write_registry(path: Path, entries):
    """entries: list of dicts {path, type, classification, evidence}."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"meta": {}, "entries": entries}, sort_keys=False),
                    encoding="utf-8")


class _Fixture:
    """A tmp fixture: corpus root + registry path + state path + an audit handle."""

    def __init__(self, tmp: Path):
        self.root = tmp / "corpus"
        self.registry = tmp / "dc-caller-audit.yaml"
        self.state = tmp / "state" / "scan_state.json"

    def audit(self) -> dca.CallerAudit:
        return dca.CallerAudit(self.root, self.registry, self.state)


# --------------------------------------------------------------------------- #
# C2 — enumeration + drift detection + auto-not-applicable
# --------------------------------------------------------------------------- #

class TestC2Drift(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fx = _Fixture(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def test_enumerate_finds_all_entries_with_correct_types(self):
        _build_corpus(
            self.fx.root,
            skills=[("clar", REFERENCING)],
            rules=[("alloc.md", REFERENCING)],
            hooks=[("gate.sh", NON_REFERENCING), ("engine.py", REFERENCING)],
        )
        disk = self.fx.audit().enumerate_corpus()
        self.assertEqual(
            set(disk),
            {"skills/clar/SKILL.md", "rules/alloc.md", "hooks/gate.sh", "hooks/engine.py"},
        )
        self.assertEqual(disk["skills/clar/SKILL.md"]["type"], "skill")
        self.assertEqual(disk["rules/alloc.md"]["type"], "rules")
        self.assertEqual(disk["hooks/gate.sh"]["type"], "hook")
        self.assertEqual(disk["hooks/engine.py"]["type"], "hook")

    def test_referencing_detection_is_case_insensitive_and_deterministic(self):
        _build_corpus(
            self.fx.root,
            skills=[("a", "Uses /Double-Check here."), ("b", NON_REFERENCING)],
            rules=[], hooks=[],
        )
        disk = self.fx.audit().enumerate_corpus()
        self.assertTrue(disk["skills/a/SKILL.md"]["references"])
        self.assertFalse(disk["skills/b/SKILL.md"]["references"])

    def test_newly_discovered_flags_on_disk_not_in_registry(self):
        _build_corpus(
            self.fx.root,
            skills=[("known", REFERENCING), ("fresh", REFERENCING)],
            rules=[], hooks=[],
        )
        _write_registry(self.fx.registry, [
            {"path": "skills/known/SKILL.md", "type": "skill",
             "classification": "concrete-target", "evidence": "x"},
        ])
        res = self.fx.audit().evaluate()
        self.assertIn("skills/fresh/SKILL.md", res.newly_discovered)
        self.assertNotIn("skills/known/SKILL.md", res.newly_discovered)

    def test_stale_flags_registry_entry_not_on_disk(self):
        _build_corpus(self.fx.root, skills=[("present", REFERENCING)], rules=[], hooks=[])
        _write_registry(self.fx.registry, [
            {"path": "skills/present/SKILL.md", "type": "skill",
             "classification": "concrete-target", "evidence": "x"},
            {"path": "hooks/removed.py", "type": "hook",
             "classification": "vague-target", "evidence": "gone"},
        ])
        res = self.fx.audit().evaluate()
        self.assertIn("hooks/removed.py", res.stale)
        self.assertNotIn("skills/present/SKILL.md", res.stale)

    def test_unclassified_flags_referencing_entry_without_valid_label(self):
        _build_corpus(self.fx.root, skills=[("caller", REFERENCING)], rules=[], hooks=[])
        # present in registry but classification blank
        _write_registry(self.fx.registry, [
            {"path": "skills/caller/SKILL.md", "type": "skill",
             "classification": "", "evidence": ""},
        ])
        res = self.fx.audit().evaluate()
        self.assertIn("skills/caller/SKILL.md", res.unclassified)

    def test_invalid_classification_value_is_unclassified(self):
        _build_corpus(self.fx.root, skills=[("caller", REFERENCING)], rules=[], hooks=[])
        _write_registry(self.fx.registry, [
            {"path": "skills/caller/SKILL.md", "type": "skill",
             "classification": "concrete", "evidence": "typo"},  # not in the enum
        ])
        res = self.fx.audit().evaluate()
        self.assertIn("skills/caller/SKILL.md", res.unclassified)

    def test_non_referencing_auto_classifies_not_applicable(self):
        _build_corpus(self.fx.root, skills=[], rules=[],
                      hooks=[("plain.sh", NON_REFERENCING)])
        # present in registry with a blank classification — must NOT be unclassified
        _write_registry(self.fx.registry, [
            {"path": "hooks/plain.sh", "type": "hook",
             "classification": "", "evidence": ""},
        ])
        res = self.fx.audit().evaluate()
        self.assertIn("hooks/plain.sh", res.auto_not_applicable)
        self.assertNotIn("hooks/plain.sh", res.unclassified)
        self.assertEqual(res.classified.get("not-applicable"), 1)

    def test_non_referencing_missing_from_registry_is_newly_discovered_but_not_unclassified(self):
        _build_corpus(self.fx.root, skills=[], rules=[],
                      hooks=[("plain.sh", NON_REFERENCING)])
        _write_registry(self.fx.registry, [])  # empty
        res = self.fx.audit().evaluate()
        self.assertIn("hooks/plain.sh", res.newly_discovered)
        self.assertNotIn("hooks/plain.sh", res.unclassified)

    def test_seed_auto_fills_not_applicable_and_unclassified_placeholders(self):
        _build_corpus(
            self.fx.root,
            skills=[("caller", REFERENCING)],
            rules=[], hooks=[("plain.sh", NON_REFERENCING)],
        )
        written = self.fx.audit().seed()
        self.assertEqual(written["hooks/plain.sh"]["classification"], "not-applicable")
        self.assertEqual(written["skills/caller/SKILL.md"]["classification"], "unclassified")
        # file is on disk + parseable
        data = yaml.safe_load(self.fx.registry.read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), 2)

    def test_seed_preserves_existing_reviewed_classifications(self):
        _build_corpus(self.fx.root, skills=[("caller", REFERENCING)], rules=[], hooks=[])
        _write_registry(self.fx.registry, [
            {"path": "skills/caller/SKILL.md", "type": "skill",
             "classification": "vague-target", "evidence": "passes free-text --against"},
        ])
        written = self.fx.audit().seed()
        self.assertEqual(written["skills/caller/SKILL.md"]["classification"], "vague-target")
        self.assertEqual(written["skills/caller/SKILL.md"]["evidence"],
                         "passes free-text --against")


# --------------------------------------------------------------------------- #
# C3 — two-clean-scans done criterion (encoded + asserted)
# --------------------------------------------------------------------------- #

class TestC3Closure(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.fx = _Fixture(self.tmp)

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_clean(self):
        """Build a fully-classified, drift-free fixture (a clean corpus)."""
        _build_corpus(
            self.fx.root,
            skills=[("caller", REFERENCING)],
            rules=[], hooks=[("plain.sh", NON_REFERENCING)],
        )
        _write_registry(self.fx.registry, [
            {"path": "skills/caller/SKILL.md", "type": "skill",
             "classification": "concrete-target", "evidence": "ok"},
            {"path": "hooks/plain.sh", "type": "hook",
             "classification": "not-applicable", "evidence": "no ref"},
        ])

    def test_single_clean_scan_is_not_closed(self):
        self._seed_clean()
        audit = self.fx.audit()
        res = audit.scan()
        self.assertTrue(res.clean)
        self.assertEqual(res.consecutive_clean, 1)
        self.assertFalse(res.closed)
        self.assertFalse(audit.is_closed())

    def test_two_consecutive_clean_scans_close_the_audit(self):
        self._seed_clean()
        audit = self.fx.audit()
        audit.scan()
        res2 = audit.scan()
        self.assertTrue(res2.clean)
        self.assertEqual(res2.consecutive_clean, 2)
        self.assertTrue(res2.closed)
        self.assertTrue(audit.is_closed())

    def test_dirty_scan_resets_the_counter(self):
        self._seed_clean()
        audit = self.fx.audit()
        audit.scan()  # clean -> 1
        # introduce drift: a new unclassified referencing caller appears
        (self.fx.root / "skills" / "new").mkdir(parents=True)
        (self.fx.root / "skills" / "new" / "SKILL.md").write_text(REFERENCING, encoding="utf-8")
        res2 = audit.scan()
        self.assertFalse(res2.clean)  # newly-discovered > 0
        self.assertEqual(res2.consecutive_clean, 0)
        self.assertFalse(audit.is_closed())

    def test_unclassified_breaks_clean(self):
        _build_corpus(self.fx.root, skills=[("caller", REFERENCING)], rules=[], hooks=[])
        _write_registry(self.fx.registry, [
            {"path": "skills/caller/SKILL.md", "type": "skill",
             "classification": "", "evidence": ""},
        ])
        res = self.fx.audit().scan()
        self.assertFalse(res.clean)
        self.assertGreater(len(res.unclassified), 0)

    def test_recovery_after_classifying_then_two_clean_scans(self):
        # dirty -> classify -> two clean -> closed (the contract's full arc)
        _build_corpus(self.fx.root, skills=[("caller", REFERENCING)], rules=[],
                      hooks=[("plain.sh", NON_REFERENCING)])
        _write_registry(self.fx.registry, [])  # everything newly-discovered
        audit = self.fx.audit()
        self.assertFalse(audit.scan().clean)
        # operator seeds + classifies the referencing caller
        audit.seed()
        reg = yaml.safe_load(self.fx.registry.read_text(encoding="utf-8"))
        for e in reg["entries"]:
            if e["path"] == "skills/caller/SKILL.md":
                e["classification"] = "concrete-target"
                e["evidence"] = "invokes /double-check with a typed target"
        self.fx.registry.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
        self.assertTrue(audit.scan().clean)
        self.assertTrue(audit.scan().closed)

    def test_stale_does_not_break_clean(self):
        # locked criterion gates on newly-discovered + unclassified ONLY; stale is
        # reported but not gated.
        self._seed_clean()
        # add a stale registry row (no on-disk counterpart)
        reg = yaml.safe_load(self.fx.registry.read_text(encoding="utf-8"))
        reg["entries"].append({"path": "hooks/ghost.py", "type": "hook",
                               "classification": "not-applicable", "evidence": "removed"})
        self.fx.registry.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
        audit = self.fx.audit()
        res = audit.evaluate()
        self.assertIn("hooks/ghost.py", res.stale)
        self.assertTrue(res.clean)  # stale alone does not break clean


if __name__ == "__main__":
    unittest.main()

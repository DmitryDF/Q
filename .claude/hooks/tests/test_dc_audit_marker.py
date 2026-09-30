#!/usr/bin/env python3
"""Tests for the /double-check audit-marker substrate (Slice S1 walking skeleton).

Plan: Thoughts/double-check-validation-targeting_S1_PLAN.md (Coherent Actions A1-A5).

Coverage:
  C1  a real run writes a write-time-validated marker; no unvalidated marker persists.
  C2  marker co-located with the source + source carries a forward wikilink to it.
  C3  non-writable/adhoc source -> mirror-tree fallback dir + .dc-runs.md sidecar there.
  C4  the persisted marker is a self-contained, re-parseable YAML doc carrying the
      run's validated fields (round-trips through a compliant YAML loader + re-validates).
  A1 gate  schema field-set == the locked OQ#1 list; each required field-group raises.
  A2 gate  resolver: writable -> co-located; read-only/adhoc -> mirror; deterministic.
  A5 smoke end-to-end: real factcheck_run for the seeded type writes a validated,
      co-located, reachable marker, with no regression to R<N>.md emission.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml  # round-trip parse of the emitted YAML frontmatter (test-only dependency)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402
from pydantic import ValidationError  # noqa: E402


# Locked OQ#1 field-set for AuditMarker (A1 gate). Renaming/adding here without a
# corresponding schema change is exactly what the gate is meant to catch.
_EXPECTED_FIELDS = {
    "schema_version",
    "artifact_type", "source_path", "caller", "topic", "created_at",
    "verdict", "rounds_this_dispatch", "allocation_profile",
    "checker_rows",
    "cost",
    "axes_overlap_signal",
    "round_markers",
    "pre_check_layer", "auto_caller", "escalate_choice",
}

# Required field groups (no default ⇒ omitting raises). Optional-by-design signal
# fields (pre_check_layer/auto_caller/escalate_choice) and schema_version are excluded.
_REQUIRED_FIELDS = [
    "artifact_type", "source_path", "caller", "topic", "created_at",
    "verdict", "rounds_this_dispatch", "allocation_profile",
    "checker_rows", "cost", "axes_overlap_signal", "round_markers",
]


def _valid_marker_dict(source_path, **over):
    d = {
        "artifact_type": "recommendation",
        "source_path": str(source_path),
        "caller": "test-session",
        "topic": "double-check-validation-targeting",
        "created_at": "2026-06-25T12:00:00.000001+00:00",
        "verdict": "PASS",
        "rounds_this_dispatch": 1,
        "allocation_profile": "sonnet,sonnet,sonnet",
        "checker_rows": [
            {
                "round": 1, "model": "sonnet", "angle": "groundedness",
                "claims_examined": 3, "discrepancies": 0,
                "claim_verdicts": [
                    {"claim": "x holds", "status": "Supported",
                     "proof_source": "local-file:foo_RESEARCH.md:12"},
                ],
            },
        ],
        "cost": {
            "upgrade": {"tokens": 0, "time": 0.0},
            "self_assessment": {"tokens": 0, "time": 0.0},
            "checkers": {"tokens": 10, "time": 1.5},
        },
        "axes_overlap_signal": None,
        "round_markers": ["/tmp/R1.md"],
    }
    d.update(over)
    return d


def _read_frontmatter(marker_path):
    text = marker_path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "marker must open with a YAML fence"
    end = text.index("\n---", 3)
    return yaml.safe_load(text[4:end])


# --------------------------------------------------------------------------- #
# A1 — schema completeness + validation gate (C1, C4 substrate)
# --------------------------------------------------------------------------- #
class SchemaTests(unittest.TestCase):
    def test_field_set_matches_locked_list(self):
        self.assertEqual(set(eng.AuditMarker.model_fields.keys()), _EXPECTED_FIELDS)

    def test_valid_marker_validates(self):
        m = eng.AuditMarker.model_validate(_valid_marker_dict("/tmp/src.md"))
        self.assertEqual(m.verdict, "PASS")
        self.assertEqual(m.checker_rows[0].claims_examined, 3)
        self.assertEqual(m.checker_rows[0].claim_verdicts[0].status, "Supported")

    def test_each_required_field_group_raises_when_missing(self):
        for field in _REQUIRED_FIELDS:
            d = _valid_marker_dict("/tmp/src.md")
            d.pop(field)
            with self.assertRaises(ValidationError, msg=f"missing {field} must raise"):
                eng.AuditMarker.model_validate(d)

    def test_optional_signal_fields_may_be_absent(self):
        # Signal #1: a run without pre-check omits pre_check_layer entirely.
        m = eng.AuditMarker.model_validate(_valid_marker_dict("/tmp/src.md"))
        self.assertIsNone(m.pre_check_layer)
        self.assertIsNone(m.auto_caller)
        self.assertIsNone(m.escalate_choice)

    def test_extra_field_rejected(self):
        # "populate, never reshape" — a stray field is rejected (extra="forbid").
        d = _valid_marker_dict("/tmp/src.md", surprise_field="nope")
        with self.assertRaises(ValidationError):
            eng.AuditMarker.model_validate(d)


# --------------------------------------------------------------------------- #
# A2 — resolve_audit_location (deterministic single named fallback)
# --------------------------------------------------------------------------- #
class ResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writable_source_is_co_located(self):
        src = self.tmp / "artifact.md"
        src.write_text("x", encoding="utf-8")
        self.assertEqual(eng.resolve_audit_location(src), self.tmp.resolve())

    def test_missing_parent_falls_back_to_mirror_tree(self):
        src = self.tmp / "does_not_exist" / "adhoc.md"  # parent absent ⇒ fallback
        loc = eng.resolve_audit_location(src)
        self.assertTrue(_path_under(loc, eng.DC_AUDIT_FALLBACK_ROOT))

    def test_under_claude_home_falls_back(self):
        # adhoc artifacts already under ~/.claude/ route to the mirror tree even
        # if the dir is writable (no audit pollution inside the state tree).
        src = Path.home() / ".claude" / "state" / "plan_validation" / "adhoc" / "draft_x.md"
        loc = eng.resolve_audit_location(src)
        self.assertTrue(_path_under(loc, eng.DC_AUDIT_FALLBACK_ROOT))

    def test_deterministic_for_identical_input(self):
        src = self.tmp / "missing" / "x.md"
        self.assertEqual(
            eng.resolve_audit_location(src), eng.resolve_audit_location(src)
        )


def _path_under(child, ancestor):
    try:
        Path(child).resolve().relative_to(Path(ancestor).resolve())
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# C1 / C2 / C4 — write_audit_marker write path
# --------------------------------------------------------------------------- #
class WriteMarkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "recommendation_artifact.md"
        self.src.write_text("# A recommendation\n\nbody\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_marker_persists_co_located(self):  # C2
        res = eng.write_audit_marker(_valid_marker_dict(self.src))
        self.assertTrue(res["co_located"])
        self.assertEqual(res["marker_path"].parent, self.tmp.resolve())
        self.assertTrue(res["marker_path"].is_file())

    def test_source_carries_forward_wikilink(self):  # C2
        res = eng.write_audit_marker(_valid_marker_dict(self.src))
        self.assertTrue(res["wikilink_written"])
        text = self.src.read_text(encoding="utf-8")
        self.assertIn(f"[[{res['marker_path'].stem}]]", text)

    def test_invalid_marker_not_persisted(self):  # C1 — validate BEFORE persist
        bad = _valid_marker_dict(self.src)
        bad.pop("verdict")  # required field missing
        with self.assertRaises(ValidationError):
            eng.write_audit_marker(bad)
        markers = list(self.tmp.glob("dc-audit__*.md"))
        self.assertEqual(markers, [], "no marker may be written for an invalid input")

    def test_marker_roundtrips_and_revalidates(self):  # C4
        res = eng.write_audit_marker(_valid_marker_dict(self.src))
        fm = _read_frontmatter(res["marker_path"])
        # self-contained, human/loader-inspectable validated fields present
        self.assertEqual(fm["verdict"], "PASS")
        self.assertEqual(fm["artifact_type"], "recommendation")
        self.assertEqual(fm["checker_rows"][0]["claims_examined"], 3)
        self.assertEqual(fm["cost"]["checkers"]["tokens"], 10)
        # the marker re-validates against the schema (truly self-contained)
        eng.AuditMarker.model_validate(fm)

    def test_pre_check_absent_in_serialized_marker(self):  # Signal #1
        fm = _read_frontmatter(
            eng.write_audit_marker(_valid_marker_dict(self.src))["marker_path"]
        )
        self.assertNotIn("pre_check_layer", fm)

    def test_sidecar_records_dispatch(self):  # A4
        res = eng.write_audit_marker(_valid_marker_dict(self.src))
        sidecar = res["sidecar_path"]
        self.assertTrue(sidecar.is_file())
        body = sidecar.read_text(encoding="utf-8")
        self.assertIn("| PASS |", body)
        self.assertIn(f"[[{res['marker_path'].stem}]]", body)

    def test_escalate_sidecar_no_pass_notation(self):  # A4 / Metric S2
        res = eng.write_audit_marker(
            _valid_marker_dict(self.src, verdict="ESCALATE", rounds_this_dispatch=4)
        )
        self.assertIn("4 NO PASS", res["sidecar_path"].read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# C3 — fallback (mirror-tree) write path for a non-writable/adhoc source
# --------------------------------------------------------------------------- #
class FallbackWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fallback = self.tmp / "dc-audit-root"
        self._orig_root = eng.DC_AUDIT_FALLBACK_ROOT
        eng.DC_AUDIT_FALLBACK_ROOT = self.fallback  # hermetic: never touch real ~/.claude

    def tearDown(self):
        eng.DC_AUDIT_FALLBACK_ROOT = self._orig_root
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fallback_marker_and_sidecar_no_wikilink(self):  # C3
        # parent dir does not exist ⇒ resolver routes to the mirror tree.
        src = self.tmp / "no_such_dir" / "adhoc_source.md"
        res = eng.write_audit_marker(_valid_marker_dict(src))
        self.assertFalse(res["co_located"])
        self.assertFalse(res["wikilink_written"])
        self.assertTrue(_path_under(res["marker_path"], self.fallback))
        self.assertTrue(res["marker_path"].is_file())
        self.assertTrue(res["sidecar_path"].is_file())
        self.assertEqual(res["sidecar_path"].name, ".dc-runs.md")


# --------------------------------------------------------------------------- #
# A5 — end-to-end walking-skeleton smoke through a real factcheck_run
# --------------------------------------------------------------------------- #
class EndToEndSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.src = self.tmp / "src" / "seeded_recommendation.md"
        self.src.parent.mkdir(parents=True)
        self.src.write_text("# seeded recommendation\n\nclaim body\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, write_dc_audit):
        return eng.factcheck_run(
            state_dir=str(self.state_dir),
            draft_path=str(self.src),
            kind="recommendation",
            session_id="smoke-session",
            debounce_seconds=0,
            models=["sonnet", "sonnet", "sonnet"],
            max_rounds=2,
            proj="proj",
            topic="topic",
            _checker_fn=lambda d, i, m, r, prior: "PASS",
            write_dc_audit=write_dc_audit,
        )

    def test_real_run_emits_validated_co_located_reachable_marker(self):  # C1+C2+C4
        verdict = self._run(write_dc_audit=True)
        self.assertEqual(verdict["status"], "PASS")

        # No regression: the R<N>.md round file is still emitted in the verdict dir.
        round_file = self.state_dir / "proj" / "topic" / "recommendation" / "R1.md"
        self.assertTrue(round_file.is_file())

        # The audit marker is co-located beside the source...
        markers = list(self.src.parent.glob("dc-audit__*.md"))
        self.assertEqual(len(markers), 1)
        # ...validates standalone...
        eng.AuditMarker.model_validate(_read_frontmatter(markers[0]))
        # ...links its R<N>.md child...
        fm = _read_frontmatter(markers[0])
        self.assertTrue(any(rm.endswith("R1.md") for rm in fm["round_markers"]))
        # ...and the source reaches it in one step (forward wikilink).
        self.assertIn(f"[[{markers[0].stem}]]", self.src.read_text(encoding="utf-8"))

    def test_default_run_writes_no_audit_marker(self):  # zero-regression default
        self._run(write_dc_audit=False)
        self.assertEqual(list(self.src.parent.glob("dc-audit__*.md")), [])


if __name__ == "__main__":
    unittest.main()

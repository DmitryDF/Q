#!/usr/bin/env python3
"""S5 proof — /clarification Step 2 cutover (A10 both-surfaces witness).

Plan: refc-vs-extraction-consolidation-20260719113514_CENTRALIZATION_PLAN.md,
Coherent Actions row "S5: /clarification Step 2 cutover (A10 both-surfaces
witness)". Design: …_CENTRALIZATION_DESIGN.md, Architecture Decision A10.

Covers:
  (1) A9(c) byte-stability — `_validate_claim_gate`'s schema decision and
      `clar_advance`'s in-memory return shape are UNCHANGED for representative
      claim-sets (schema-valid+deep, schema-invalid, reason-logged fallback,
      empty-reason-rejected) — the S5 additions never touch either.
  (2) The NEW artifact-co-located witness surface (A10 surfaces i+ii): when
      `thought_file_path` is set on the topic, a `.claim-runs.md` row appears
      at the GENERIC `_claim_metrics.sidecar_path_for(thought_file_path)`
      location (surface ii), and `_claim_attest.classify_provenance` reading
      that same path returns `attested-engine` (surface i is wired correctly —
      the witness read and the write agree on one sidecar).
  (3) Best-effort / non-blocking: when `thought_file_path` is unset (`None` —
      the documented common case per the recon: nothing in the flow backfills
      it before Step 2), the new surfaces silently no-op — no exception, no
      stray file, `clar_advance`'s return is unaffected.
  (4) `_witness_claim_provenance` in isolation surfaces an ADVISORY line to
      stderr on a `flagged` disposition (deep assertion, no backing row) and
      stays silent on `attested-engine`/`authorized-legacy` — advisory-only,
      never raises.

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_s5_clarification_bytestable.py -v
Or:  python3 ${KIT_HOOKS_DIR}/tests/test_s5_clarification_bytestable.py
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".claude" / "hooks"))
import pre_plan_gates as ppg  # noqa: E402
import _claim_metrics as cm  # noqa: E402
from _claim_attest import classify_provenance  # noqa: E402

_CRITERIA = ("atomicity", "verifiability", "decontextuality",
             "minimality", "fluency", "faithfulness")


def _deep_claim_set():
    """A schema-valid, DEEP-thoroughness claim_set — the only shape that clears
    the Step-2 manageable path today (site resolves to MODE_DEEP per CF-4;
    `deep_mode_ok` refuses a NORMAL claim-set at this site)."""
    return {
        "source_type": "web", "source_path": "clarification:step2",
        "lang": "en", "thoroughness": "deep",
        "claims": [{
            "text": "The user wants offline-first sync.",
            "locator": "mirror#1", "role": "backward",
            "flags": {c: True for c in _CRITERIA},
        }],
    }


def _schema_invalid_claim_set():
    return {"source_type": "web", "source_path": "clarification:step2", "claims": []}


class S5ClarificationByteStableTests(unittest.TestCase):
    """clar_advance Step 2 — A9(c) byte-stable schema gate + new artifact witness."""

    SESSION_ID = "test-s5-clar-sid-0001"
    PROJECT_SLUG = "test_project_s5"
    TOPIC_SLUG = "test_topic_s5_clarification"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._orig_topic_dir = ppg.TOPIC_STATE_DIR
        self._orig_state_dir = ppg.STATE_DIR
        self._orig_workflow_dir = ppg.WORKFLOW_VALIDATION_DIR
        ppg.TOPIC_STATE_DIR = tmp / "topics"
        ppg.STATE_DIR = tmp / "sessions"
        ppg.WORKFLOW_VALIDATION_DIR = tmp / "workflow_validation"
        ppg.TOPIC_STATE_DIR.mkdir(parents=True)
        ppg.STATE_DIR.mkdir(parents=True)
        ppg.create_topic(
            session_id=self.SESSION_ID,
            project_slug=self.PROJECT_SLUG,
            topic_slug=self.TOPIC_SLUG,
        )
        # A fake but real (on-disk) "_THOUGHT.md" artifact for the witness surfaces.
        self._thought_dir = tmp / "Thoughts"
        self._thought_dir.mkdir(parents=True)
        self._thought_path = self._thought_dir / f"{self.TOPIC_SLUG}_THOUGHT.md"
        self._thought_path.write_text("# Idea\n\n## Problem\nsome problem\n", encoding="utf-8")

    def tearDown(self):
        ppg.TOPIC_STATE_DIR = self._orig_topic_dir
        ppg.STATE_DIR = self._orig_state_dir
        ppg.WORKFLOW_VALIDATION_DIR = self._orig_workflow_dir
        self._tmp.cleanup()

    def _read_topic_state(self):
        return ppg._read_json(ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG))

    def _advance_to_step1(self):
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})

    def _set_thought_file(self):
        ppg.set_thought_file(self.SESSION_ID, str(self._thought_path))

    def _artifact_sidecar(self):
        return cm.sidecar_path_for(str(self._thought_path))

    # ----- (1) A9(c) byte-stability: schema decision + in-memory return -----

    def test_bytestable_valid_deep_claim_set_return_shape_unchanged(self):
        """A9(c): a schema-valid DEEP claim_set still advances with the EXACT
        documented return shape {status, step, next_step} — no new keys leaked
        by the S5 additions."""
        self._advance_to_step1()
        result = ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted", "claim_set": _deep_claim_set()},
        )
        self.assertEqual(set(result.keys()), {"status", "step", "next_step"})
        self.assertEqual(result["status"], "advanced")
        self.assertEqual(result["step"], 2)
        self.assertEqual(result["next_step"], 3)
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 2)
        self.assertIn("claim_set", state["clarification_payloads"]["2"])

    def test_bytestable_schema_invalid_claim_set_still_refused_unchanged_message(self):
        """A9(c): a schema-invalid claim_set is refused with the same 'schema'
        wording, state untouched at step 1 — S5 never runs on the refusal path."""
        self._advance_to_step1()
        with self.assertRaises(ValueError) as cm_:
            ppg.clar_advance(
                self.SESSION_ID, 2,
                {"gate_token": "accepted", "claim_set": _schema_invalid_claim_set()},
            )
        self.assertIn("schema", str(cm_.exception).lower())
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 1)
        # no artifact-shape sidecar was ever created — the gate raised before
        # either new S5 helper is reached.
        self.assertFalse(self._artifact_sidecar().exists())

    def test_bytestable_reason_logged_fallback_unchanged(self):
        """A9(c): a non-empty claim_fallback_reason still advances (default path),
        same persisted payload shape."""
        self._advance_to_step1()
        result = ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted",
             "claim_fallback_reason": "user opted out of claim identification"},
        )
        self.assertEqual(set(result.keys()), {"status", "step", "next_step"})
        state = self._read_topic_state()
        self.assertEqual(
            state["clarification_payloads"]["2"]["claim_fallback_reason"],
            "user opted out of claim identification",
        )

    def test_bytestable_empty_reason_still_rejected(self):
        """A9(c): a blank reason is still refused (no silent skip)."""
        self._advance_to_step1()
        with self.assertRaises(ValueError):
            ppg.clar_advance(
                self.SESSION_ID, 2,
                {"gate_token": "accepted", "claim_fallback_reason": "   "},
            )

    def test_bytestable_preexisting_state_sidecar_still_written(self):
        """A9(c): the PRE-EXISTING state-co-located `.claim-runs.md` write
        (`_record_claim_run`) is untouched — still fires exactly as before,
        independent of thought_file_path."""
        self._advance_to_step1()
        ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted", "claim_set": _deep_claim_set()},
        )
        state_path = ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG)
        legacy_sidecar = state_path.with_name(state_path.stem + ".claim-runs.md")
        self.assertTrue(legacy_sidecar.exists())
        body = legacy_sidecar.read_text()
        self.assertIn("| manageable |", body)
        self.assertIn("clarification:step2", body)

    # ----- (2) New artifact-co-located witness surface (A10 i + ii) -----

    def test_witness_surfaces_fire_when_thought_file_path_set(self):
        """When thought_file_path is set (surface ii precondition), Step 2's
        manageable advance ALSO writes a generic-convention `.claim-runs.md` row
        next to the `_THOUGHT.md` artifact, and a subsequent
        classify_provenance() read over that same path attests it (surface i is
        wired to read what surface ii wrote)."""
        self._advance_to_step1()
        self._set_thought_file()
        ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted", "claim_set": _deep_claim_set()},
        )
        artifact_sidecar = self._artifact_sidecar()
        self.assertTrue(artifact_sidecar.exists(),
                        "expected a NEW artifact-co-located .claim-runs.md row")
        body = artifact_sidecar.read_text()
        self.assertIn("| manageable |", body)
        self.assertIn("clarification:step2", body)

        disposition = classify_provenance(
            str(self._thought_path), asserted_mode="manageable",
            asserted_thoroughness="deep",
        )
        self.assertEqual(disposition["disposition"], "attested-engine", disposition)

    def test_witness_surfaces_record_fallback_mode_too(self):
        """A default/legacy fallback also lands a BYPASSED row at the artifact
        sidecar (surface ii covers both modes, mirroring the pre-existing
        state-co-located write)."""
        self._advance_to_step1()
        self._set_thought_file()
        ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted",
             "claim_fallback_reason": "engine unavailable this run"},
        )
        artifact_sidecar = self._artifact_sidecar()
        self.assertTrue(artifact_sidecar.exists())
        body = artifact_sidecar.read_text()
        self.assertIn("| BYPASSED |", body)
        self.assertIn("engine unavailable this run", body)

        disposition = classify_provenance(
            str(self._thought_path), asserted_mode="default",
            asserted_thoroughness="n/a",
        )
        self.assertEqual(disposition["disposition"], "authorized-legacy", disposition)

    # ----- (3) Best-effort no-op when thought_file_path is unset -----

    def test_no_artifact_sidecar_when_thought_file_path_unset(self):
        """thought_file_path stays None (the common, undocumented-backfill case
        per recon) — S5's new surfaces silently no-op: no exception, no stray
        file, clar_advance behaves exactly as it did before S5."""
        self._advance_to_step1()
        state = self._read_topic_state()
        self.assertIsNone(state.get("thought_file_path"))
        result = ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted", "claim_set": _deep_claim_set()},
        )
        self.assertEqual(result["status"], "advanced")
        self.assertFalse(self._artifact_sidecar().exists())

    # ----- (4) _witness_claim_provenance in isolation — advisory-only -----

    def test_witness_helper_prints_advisory_on_flagged_disposition(self):
        """A deep assertion with NO backing row at the artifact sidecar surfaces
        an ADVISORY line to stderr — never raises, never blocks."""
        fresh_artifact = str(self._thought_dir / "no_record_yet_THOUGHT.md")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ppg._witness_claim_provenance(
                fresh_artifact, mode="manageable",
                result=type("FakeCS", (), {"thoroughness": "deep"})(),
            )
        self.assertIn("ADVISORY", buf.getvalue())

    def test_witness_helper_silent_on_authorized_legacy(self):
        """A default/legacy assertion is never flagged — no ADVISORY line."""
        fresh_artifact = str(self._thought_dir / "never_flagged_THOUGHT.md")
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ppg._witness_claim_provenance(
                fresh_artifact, mode="default", result="some fallback reason",
            )
        self.assertEqual(buf.getvalue(), "")

    def test_witness_helper_noop_on_falsy_artifact_path(self):
        """No artifact_path → silent no-op, no exception."""
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            ppg._witness_claim_provenance(None, mode="manageable", result=object())
            ppg._record_claim_run_artifact_sidecar(None, site="x", mode="manageable",
                                                   result=object())
        self.assertEqual(buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

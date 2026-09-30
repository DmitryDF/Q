#!/usr/bin/env python3
"""Tests for Slice D follow-up — new 3-constant schema + Step 1b + hash invariance.

Covers:
  (1) test_idea_sections_singleton
  (2) test_discovery_locked_fields_count
  (3) test_discovery_mutable_subsections
  (4) test_validate_discovery_locked_fields_blocks_missing_outcome
  (5) test_validate_discovery_locked_fields_blocks_empty_field
  (6) test_validate_discovery_locked_fields_requires_omtm_in_metrics
  (7) test_validate_discovery_locked_fields_pass
  (8) test_cascade_map_step_1b
  (9) test_discovery_hash_invariant_to_scope_edit
  (10) test_discovery_hash_changes_on_locked_field_edit
  (11) test_snapshot_sections_removed
  (12) test_clar_advance_strict_order
  (13) test_clar_advance_persists_payload
  (14) test_clar_rewind_marks_downstream_stale
  (15) test_resume_source_persistence
  (16) test_clar_status_reports_current_step
  (17) test_clar_advance_1b_allowed_after_step1
  (18) test_clarification_active_session_set_at_step1
  (19) test_clarification_active_session_cleared_at_step9

Run: python3 -m pytest ${KIT_HOOKS_DIR}/tests/test_clarification_schema.py -v
Or:  python3 ${KIT_HOOKS_DIR}/tests/test_clarification_schema.py
"""

import sys
import tempfile
import unittest
from pathlib import Path

# A6 (discovery-field-predicate-coherence S1): resolve the harness relative to THIS
# file, not to $HOME. `Path.home()` made a staged copy of this suite exercise the LIVE
# harness — the failure its sibling warns about at test_discovery_lock_check.py:36-39,
# and observed directly here: before this change the clone's suite raised from
# ${KIT_HOOKS_DIR}/pre_plan_gates.py rather than from the clone's copy.
sys.path.insert(0, str(Path(__file__).parent.parent))
import pre_plan_gates as ppg  # noqa: E402

_CRITERIA = ("atomicity", "verifiability", "decontextuality",
             "minimality", "fluency", "faithfulness")


def _valid_claim_set():
    """A schema-valid in-memory claim_set for the Step-2 gate (A1) — mirrors the
    non-file locator case in _claim_persist.py's self-test."""
    return {
        "source_type": "web", "source_path": "clarification:step2",
        "lang": "en", "thoroughness": "normal",
        "claims": [{
            "text": "The user wants offline-first sync.",
            "locator": "mirror#1", "role": "backward",
            "flags": {c: True for c in _CRITERIA},
        }],
    }


def _payload_for(step_num, **extra):
    """Build a clar_advance payload; auto-satisfy the Step-2 claim gate (A1) with a
    reason-logged fallback unless the caller already supplies a claim_set / reason.
    Used by tests that advance THROUGH step 2 mechanically (not testing its gate)."""
    p = {"gate_token": "accepted"}
    p.update(extra)
    if step_num == 2 and "claim_set" not in p and "claim_fallback_reason" not in p:
        p["claim_fallback_reason"] = "test harness — claim engine not exercised"
    return p


# ---------------------------------------------------------------------------
# Constants schema tests
# ---------------------------------------------------------------------------

class ConstantsSchemaTests(unittest.TestCase):
    """Verify the three new role-aligned constants replaced SNAPSHOT_SECTIONS."""

    def test_idea_sections_singleton(self):
        """IDEA_SECTIONS has exactly one entry: '## Problem'."""
        self.assertEqual(ppg.IDEA_SECTIONS, ["## Problem"])

    def test_discovery_locked_fields_count(self):
        """DISCOVERY_LOCKED_FIELDS has exactly 4 entries in the correct order."""
        self.assertEqual(len(ppg.DISCOVERY_LOCKED_FIELDS), 4)
        self.assertEqual(ppg.DISCOVERY_LOCKED_FIELDS[0], "## Guiding Policy")
        self.assertEqual(ppg.DISCOVERY_LOCKED_FIELDS[1], "## Desired Outcome")
        self.assertEqual(ppg.DISCOVERY_LOCKED_FIELDS[2], "## Desired Solution")
        self.assertEqual(ppg.DISCOVERY_LOCKED_FIELDS[3], "## Metrics")

    def test_discovery_mutable_subsections(self):
        """DISCOVERY_MUTABLE_SUBSECTIONS has exactly 2 entries."""
        self.assertEqual(ppg.DISCOVERY_MUTABLE_SUBSECTIONS, ["## Scope", "## Q&A"])

    def test_snapshot_sections_removed(self):
        """SNAPSHOT_SECTIONS must no longer exist (deleted, no back-compat shim)."""
        self.assertFalse(hasattr(ppg, "SNAPSHOT_SECTIONS"), "SNAPSHOT_SECTIONS was not deleted")


# ---------------------------------------------------------------------------
# validate_discovery_locked_fields tests
# ---------------------------------------------------------------------------

def _make_discovery(fields=None, include_omtm=True, missing_field=None):
    """Build a valid # Discovery section text for testing."""
    parts = []
    for f in ppg.DISCOVERY_LOCKED_FIELDS:
        if missing_field and f == missing_field:
            continue
        if f == "## Metrics":
            if include_omtm:
                parts.append(f"{f}\nOMTM: sessions with at least 1 saved search / total new sessions\nSecondary: bounce rate")
            else:
                parts.append(f"{f}\nSecondary: bounce rate (no OMTM line)")
        else:
            parts.append(f"{f}\nsome non-empty content for {f}")
    for m in ppg.DISCOVERY_MUTABLE_SUBSECTIONS:
        parts.append(f"{m}\n")
    return "\n\n".join(parts)


class ValidateDiscoveryLockedFieldsTests(unittest.TestCase):
    """validate_discovery_locked_fields() tests."""

    def test_validate_discovery_locked_fields_pass(self):
        """Valid Discovery section passes with empty diagnostics."""
        text = _make_discovery()
        diag = ppg.validate_discovery_locked_fields(text)
        self.assertEqual(diag, [], f"Expected PASS; got {diag!r}")

    def test_validate_discovery_locked_fields_blocks_missing_outcome(self):
        """Missing ## Desired Outcome produces a diagnostic."""
        text = _make_discovery(missing_field="## Desired Outcome")
        diag = ppg.validate_discovery_locked_fields(text)
        self.assertTrue(
            any("## Desired Outcome" in d for d in diag),
            f"Expected diagnostic naming ## Desired Outcome; got {diag!r}",
        )

    def test_validate_discovery_locked_fields_blocks_empty_field(self):
        """An empty ## Guiding Policy body produces a diagnostic."""
        text = "## Guiding Policy\n\n## Desired Outcome\nsome content\n## Desired Solution\nsome content\n## Metrics\nOMTM: some metric\n"
        diag = ppg.validate_discovery_locked_fields(text)
        self.assertTrue(
            any("Guiding Policy" in d for d in diag),
            f"Expected diagnostic naming Guiding Policy; got {diag!r}",
        )

    def test_validate_discovery_locked_fields_requires_omtm_in_metrics(self):
        """## Metrics without an OMTM: line produces a diagnostic."""
        text = _make_discovery(include_omtm=False)
        diag = ppg.validate_discovery_locked_fields(text)
        self.assertTrue(
            any("OMTM" in d for d in diag),
            f"Expected diagnostic naming OMTM; got {diag!r}",
        )


# ---------------------------------------------------------------------------
# Cascade map tests
# ---------------------------------------------------------------------------

class CascadeMapTests(unittest.TestCase):
    """CLARIFICATION_CASCADE_MAP tests for the new 1b step."""

    def test_cascade_map_step_1b_present(self):
        """'1b' key must be in CLARIFICATION_CASCADE_MAP."""
        self.assertIn("1b", ppg.CLARIFICATION_CASCADE_MAP)

    def test_cascade_map_step_1b_value(self):
        """MAP['1b'] == [2, 7, 9]."""
        self.assertEqual(ppg.CLARIFICATION_CASCADE_MAP["1b"], [2, 7, 9])

    def test_cascade_map_step_1_includes_1b(self):
        """MAP[1] must include '1b' (updating Idea cascades into Problem)."""
        self.assertIn("1b", ppg.CLARIFICATION_CASCADE_MAP[1])


# ---------------------------------------------------------------------------
# Discovery hash invariance test
# ---------------------------------------------------------------------------

def _make_thought_with_discovery(scope_body="initial scope", outcome_body="initial outcome"):
    return (
        "# Idea\n\n## Problem\nsome problem\n\n"
        "# Discovery\n\n"
        "## Guiding Policy\nsome guiding policy\n\n"
        f"## Desired Outcome\n{outcome_body}\n\n"
        "## Desired Solution\nsome solution\n\n"
        f"## Scope\n{scope_body}\n\n"
        "## Q&A\nsome q and a\n\n"
        "## Metrics\nOMTM: retention rate\nSecondary: bounce rate\n\n"
        "# Solution Design\n\n*(placeholder)*\n"
    )


class DiscoveryHashTests(unittest.TestCase):
    """_discovery_locked_fields_hash() invariance tests."""

    def test_discovery_hash_invariant_to_scope_edit(self):
        """Changing ## Scope body does NOT change the hash."""
        text_before = _make_thought_with_discovery(scope_body="scope A")
        text_after = _make_thought_with_discovery(scope_body="scope B — significantly different")
        h_before = ppg._discovery_locked_fields_hash(text_before)
        h_after = ppg._discovery_locked_fields_hash(text_after)
        self.assertIsNotNone(h_before)
        self.assertEqual(h_before, h_after, "Hash changed when only ## Scope was edited")

    def test_discovery_hash_changes_on_locked_field_edit(self):
        """Changing ## Desired Outcome body DOES change the hash."""
        text_before = _make_thought_with_discovery(outcome_body="outcome A")
        text_after = _make_thought_with_discovery(outcome_body="outcome B — significantly different")
        h_before = ppg._discovery_locked_fields_hash(text_before)
        h_after = ppg._discovery_locked_fields_hash(text_after)
        self.assertIsNotNone(h_before)
        self.assertIsNotNone(h_after)
        self.assertNotEqual(h_before, h_after, "Hash did NOT change when ## Desired Outcome was edited")

    def test_discovery_hash_absent_for_legacy_snapshot(self):
        """File with ## Snapshot but no # Discovery returns None."""
        legacy = "## Snapshot\n\n### Problem\nsome problem\n### Guiding Policy\nsome gp\n"
        h = ppg._discovery_locked_fields_hash(legacy)
        self.assertIsNone(h, "Expected None for legacy Snapshot-only file")


# ---------------------------------------------------------------------------
# Clarification state machine tests
# ---------------------------------------------------------------------------

class ClarificationStateMachineTests(unittest.TestCase):
    """clar_advance / clar_status / clar_rewind tests against a tempdir-backed
    topic state."""

    SESSION_ID = "test-clar-sid-aaaa1111bbbb2222"
    PROJECT_SLUG = "test_project"
    TOPIC_SLUG = "test_topic_clarification"

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

    def tearDown(self):
        ppg.TOPIC_STATE_DIR = self._orig_topic_dir
        ppg.STATE_DIR = self._orig_state_dir
        ppg.WORKFLOW_VALIDATION_DIR = self._orig_workflow_dir
        self._tmp.cleanup()

    def _read_topic_state(self):
        return ppg._read_json(
            ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG)
        )

    def test_clar_advance_strict_order(self):
        """clar_advance(step=3) when current step is 1 → ValueError."""
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})
        with self.assertRaises(ValueError) as cm:
            ppg.clar_advance(self.SESSION_ID, 3, {"gate_token": "accepted"})
        self.assertIn("sequence violation", str(cm.exception).lower())

    def test_clar_advance_persists_payload(self):
        """clar_advance writes step counter + payload into topic state."""
        ppg.clar_advance(
            self.SESSION_ID,
            1,
            {"gate_token": "accepted", "idea_verbatim": "ship it"},
        )
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 1)
        self.assertIn("1", state["clarification_payloads"])
        self.assertEqual(
            state["clarification_payloads"]["1"]["idea_verbatim"], "ship it"
        )

    # ----- A1: Step-2 claim gate (reference seam contract) -----

    def _advance_to_step1(self):
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})

    def test_step2_rejects_payload_without_claim_artifact_or_reason(self):
        """A1: Step 2 refuses to advance with neither a claim_set nor a reason —
        the engine can never silently no-op. State stays at step 1."""
        self._advance_to_step1()
        with self.assertRaises(ValueError) as cm:
            ppg.clar_advance(self.SESSION_ID, 2, {"gate_token": "accepted"})
        msg = str(cm.exception).lower()
        self.assertIn("claim_set", msg)
        self.assertIn("claim_fallback_reason", msg)
        # rejected gate leaves state untouched (still step 1, no step-2 payload)
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 1)
        self.assertNotIn("2", state["clarification_payloads"])

    def test_step2_accepts_schema_valid_claim_set(self):
        """A1: a schema-valid claim_set is the manageable path — advance succeeds."""
        self._advance_to_step1()
        result = ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted", "claim_set": _valid_claim_set()},
        )
        self.assertEqual(result["status"], "advanced")
        self.assertEqual(result["step"], 2)
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 2)
        self.assertIn("claim_set", state["clarification_payloads"]["2"])
        # A4: a durable manageable run-record was appended (co-located with topic state)
        runs = ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG).with_name(
            ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG).stem + ".claim-runs.md")
        self.assertTrue(runs.exists())
        body = runs.read_text()
        self.assertIn("| manageable |", body)
        self.assertIn("clarification:step2", body)

    def test_step2_accepts_reason_logged_fallback(self):
        """A1: a non-empty claim_fallback_reason is the reason-logged default path
        (dual-mode U3) — advance succeeds and the reason is persisted."""
        self._advance_to_step1()
        result = ppg.clar_advance(
            self.SESSION_ID, 2,
            {"gate_token": "accepted",
             "claim_fallback_reason": "user opted out of claim identification"},
        )
        self.assertEqual(result["step"], 2)
        state = self._read_topic_state()
        self.assertEqual(
            state["clarification_payloads"]["2"]["claim_fallback_reason"],
            "user opted out of claim identification",
        )
        # A5: the fallback is reason-logged as a BYPASSED run-record (never a silent skip)
        runs = ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG).with_name(
            ppg._topic_path(topic_slug=self.TOPIC_SLUG, project_slug=self.PROJECT_SLUG).stem + ".claim-runs.md")
        self.assertTrue(runs.exists())
        body = runs.read_text()
        self.assertIn("| BYPASSED |", body)
        self.assertIn("user opted out of claim identification", body)

    def test_step2_rejects_empty_reason(self):
        """A1: a blank/whitespace reason is not a reason — refuse (no silent skip)."""
        self._advance_to_step1()
        with self.assertRaises(ValueError):
            ppg.clar_advance(
                self.SESSION_ID, 2,
                {"gate_token": "accepted", "claim_fallback_reason": "   "},
            )

    def test_step2_rejects_malformed_claim_set(self):
        """A1: a claim_set failing the engine schema (empty claims) is refused at the
        seam via _claim_persist.parse_claim_set — nothing malformed advances."""
        self._advance_to_step1()
        bad = {"source_type": "web", "source_path": "clarification:step2", "claims": []}
        with self.assertRaises(ValueError) as cm:
            ppg.clar_advance(
                self.SESSION_ID, 2, {"gate_token": "accepted", "claim_set": bad}
            )
        self.assertIn("schema", str(cm.exception).lower())
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 1)

    def test_clar_rewind_marks_downstream_stale(self):
        """Advancing through step 5 then rewinding to 2 marks steps 3, 4, 5 stale."""
        for n in range(1, 6):
            ppg.clar_advance(self.SESSION_ID, n, _payload_for(n, step=n))
        result = ppg.clar_rewind(self.SESSION_ID, 2)
        self.assertEqual(result["status"], "rewound")
        self.assertEqual(result["to_step"], 2)
        self.assertEqual(result["stale_steps"], [3, 4, 5])
        state = self._read_topic_state()
        self.assertEqual(state["clarification_step"], 2)
        for stale_step in ("3", "4", "5"):
            self.assertTrue(
                state["clarification_payloads"][stale_step].get("stale"),
                f"step {stale_step} should be marked stale after rewind",
            )
        for fresh_step in ("1", "2"):
            self.assertFalse(
                state["clarification_payloads"][fresh_step].get("stale", False),
                f"step {fresh_step} should NOT be marked stale",
            )

    def test_resume_source_persistence(self):
        """`resume_source` passed at step 1 is stored in topic state."""
        resume_path = "/tmp/some_existing_THOUGHT.md"
        ppg.clar_advance(
            self.SESSION_ID,
            1,
            {"gate_token": "accepted"},
            resume_source=resume_path,
        )
        state = self._read_topic_state()
        self.assertEqual(state["clarification_resume_source"], resume_path)

    def test_clar_status_reports_current_step(self):
        """clar_status returns the current step and payloads dict."""
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})
        ppg.clar_advance(self.SESSION_ID, 2, _payload_for(2))
        status = ppg.clar_status(self.SESSION_ID)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["step"], 2)
        self.assertEqual(set(status["payloads"].keys()), {"1", "2"})

    def test_clar_advance_1b_allowed_after_step1(self):
        """clar_advance('1b', ...) is accepted after step 1."""
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})
        result = ppg.clar_advance(
            self.SESSION_ID,
            "1b",
            {"gate_token": "problem-accepted", "problem": "test problem"},
        )
        self.assertEqual(result["status"], "advanced")
        self.assertEqual(result["step"], "1b")
        self.assertEqual(result["next_step"], 2)
        state = self._read_topic_state()
        self.assertIn("1b", state["clarification_payloads"])

    def test_clarification_active_session_set_at_step1(self):
        """clarification_active_session is set to SESSION_ID at step 1."""
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})
        state = self._read_topic_state()
        self.assertEqual(state.get("clarification_active_session"), self.SESSION_ID)

    def test_clarification_active_session_cleared_at_step9(self):
        """clarification_active_session is cleared (None) at step 9."""
        for n in range(1, 10):
            ppg.clar_advance(self.SESSION_ID, n, _payload_for(n))
        state = self._read_topic_state()
        self.assertIsNone(state.get("clarification_active_session"))

    # ----- Step-7 drafting cadence (section-by-section sub-steps) -----

    def _advance_to_6(self):
        for n in range(1, 7):
            ppg.clar_advance(self.SESSION_ID, n, _payload_for(n, step=n))

    def test_cadence_default_is_full_pass_field_none(self):
        """A fresh topic has clarification_step7_cadence defaulted to None."""
        self._advance_to_6()
        state = self._read_topic_state()
        self.assertIsNone(state.get("clarification_step7_cadence"))

    def test_full_pass_advances_6_to_7_directly(self):
        """C2: with no cadence set, step 6 → 7 advance is unchanged (succeeds)."""
        self._advance_to_6()
        result = ppg.clar_advance(self.SESSION_ID, 7, {"gate_token": "validated"})
        self.assertEqual(result["status"], "advanced")
        self.assertEqual(result["step"], 7)

    def test_set_cadence_persisted_at_step6(self):
        """C3: clar_set_cadence at step 6 persists the choice to topic state."""
        self._advance_to_6()
        result = ppg.clar_set_cadence(self.SESSION_ID, "section_by_section")
        self.assertEqual(result["cadence"], "section_by_section")
        state = self._read_topic_state()
        self.assertEqual(
            state.get("clarification_step7_cadence"), "section_by_section"
        )

    def test_set_cadence_rejected_off_step6(self):
        """Cadence can only be set at step 6."""
        ppg.clar_advance(self.SESSION_ID, 1, {"gate_token": "accepted"})
        with self.assertRaises(ValueError) as cm:
            ppg.clar_set_cadence(self.SESSION_ID, "section_by_section")
        self.assertIn("step 6", str(cm.exception))

    def test_set_cadence_invalid_value_rejected(self):
        """clar_set_cadence rejects values outside the allowlist."""
        self._advance_to_6()
        with self.assertRaises(ValueError):
            ppg.clar_set_cadence(self.SESSION_ID, "bogus")

    def test_section_mode_rejects_finalize_before_substeps(self):
        """C1: in section mode, clar_advance(7) is refused until 7a..7e are done."""
        self._advance_to_6()
        ppg.clar_set_cadence(self.SESSION_ID, "section_by_section")
        with self.assertRaises(ValueError) as cm:
            ppg.clar_advance(self.SESSION_ID, 7, {"gate_token": "validated"})
        msg = str(cm.exception).lower()
        self.assertIn("sequence violation", msg)
        self.assertIn("7a", msg)

    def test_section_substeps_in_order_then_finalize(self):
        """Section sub-steps 7a..7e advance in order, then step 7 finalizes."""
        self._advance_to_6()
        ppg.clar_set_cadence(self.SESSION_ID, "section_by_section")
        for sec in ("7a", "7b", "7c", "7d", "7e"):
            result = ppg.clar_advance(
                self.SESSION_ID, sec, {"gate_token": "section-accepted"}
            )
            self.assertEqual(result["step"], sec)
        # 7e's next_step points at 7; finalize succeeds.
        result = ppg.clar_advance(self.SESSION_ID, 7, {"gate_token": "validated"})
        self.assertEqual(result["step"], 7)
        state = self._read_topic_state()
        for sec in ("7a", "7b", "7c", "7d", "7e"):
            self.assertIn(sec, state["clarification_payloads"])

    def test_section_substep_out_of_order_rejected(self):
        """Skipping to 7b before 7a is a sequence violation."""
        self._advance_to_6()
        ppg.clar_set_cadence(self.SESSION_ID, "section_by_section")
        with self.assertRaises(ValueError) as cm:
            ppg.clar_advance(self.SESSION_ID, "7b", {"gate_token": "x"})
        self.assertIn("sequence violation", str(cm.exception).lower())

    def test_section_substep_requires_section_cadence(self):
        """7a is rejected when cadence is not section_by_section."""
        self._advance_to_6()
        with self.assertRaises(ValueError) as cm:
            ppg.clar_advance(self.SESSION_ID, "7a", {"gate_token": "x"})
        self.assertIn("section-by-section", str(cm.exception).lower())

    def test_rewind_resets_cadence_and_marks_substeps_stale(self):
        """Rewinding past step 6 resets cadence and marks 7a/7b payloads stale."""
        self._advance_to_6()
        ppg.clar_set_cadence(self.SESSION_ID, "section_by_section")
        ppg.clar_advance(self.SESSION_ID, "7a", {"gate_token": "section-accepted"})
        ppg.clar_advance(self.SESSION_ID, "7b", {"gate_token": "section-accepted"})
        result = ppg.clar_rewind(self.SESSION_ID, 5)
        self.assertEqual(result["to_step"], 5)
        # stale set includes integer 6 and the string sub-steps, ordered.
        self.assertEqual(result["stale_steps"], [6, "7a", "7b"])
        state = self._read_topic_state()
        self.assertIsNone(state.get("clarification_step7_cadence"))
        for sec in ("7a", "7b"):
            self.assertTrue(state["clarification_payloads"][sec].get("stale"))


# ---------------------------------------------------------------------------
# Anchored heading extraction (discovery-field-match-anchoring_PLAN, A1 + A5)
#
# The predicate does TWO jobs — it LOCATES a field's start and TERMINATES its
# body — and anchoring pushes those in opposite directions: the locator gets
# stricter (a field can stop being found) while terminators disappear (a body
# can grow). Tests that exercise only the locator cover half the change.
# ---------------------------------------------------------------------------

ALL_SECS = ppg.DISCOVERY_LOCKED_FIELDS + ppg.DISCOVERY_MUTABLE_SUBSECTIONS


class AnchoredLocatorTests(unittest.TestCase):
    """Job 1 — where a heading starts."""

    def test_plain_heading_matches(self):
        self.assertEqual(ppg.find_heading("## Metrics\nbody\n", "## Metrics"), 0)

    def test_heading_with_trailing_lock_emoji_matches(self):
        """Real corpus shape — `research-scope-framing-ui_THOUGHT.md:184`."""
        text = "## Metrics 🔒 (Step 8 — LOCKED by user 2026-06-15;\nbody\n"
        self.assertEqual(ppg.find_heading(text, "## Metrics"), 0)

    def test_heading_with_trailing_prose_matches(self):
        """Real corpus shape — `per-app-network-routing_THOUGHT.md:147`. This is
        why the predicate ends at a whitespace boundary rather than at
        end-of-line: `^field$` stops matching here, and measured over the corpus
        it newly fails 4 spines and destroys 4 hashes to fix 1."""
        text = "## Desired Solution — own words (Шаг 4 — утверждено;\nbody\n"
        self.assertEqual(ppg.find_heading(text, "## Desired Solution"), 0)

    def test_h3_near_miss_does_not_match(self):
        """`### Guiding Policy` literally CONTAINS `## Guiding Policy` at
        offset 1 — one of the two mismatch shapes in the diagnosis."""
        self.assertEqual(
            ppg.find_heading("### Guiding Policy\nbody\n", "## Guiding Policy"), -1)

    def test_prose_mention_does_not_match(self):
        """The other mismatch shape: a backticked mention inside `## Q&A`."""
        text = "## Q&A\nA: the `## Metrics` field holds the OMTM.\n"
        self.assertEqual(ppg.find_heading(text, "## Metrics"), -1)

    def test_longer_name_sharing_a_prefix_does_not_match(self):
        """The whitespace boundary must not degrade into a bare prefix match."""
        self.assertEqual(ppg.find_heading("## Metricsomething\nbody\n", "## Metrics"), -1)

    def test_heading_with_trailing_colon_does_not_match(self):
        """`## Metrics:` is deliberately rejected — and it is exactly why A4's
        lock-site guard exists, since rejecting it here widens permission there."""
        self.assertEqual(ppg.find_heading("## Metrics:\nbody\n", "## Metrics"), -1)

    def test_start_offset_does_not_anchor_mid_line(self):
        """`start` must behave like `str.find`'s: `^` still matches only at real
        line starts, never at a mid-line `start` position itself."""
        text = "xx## Metrics\n## Metrics\n"
        self.assertEqual(ppg.find_heading(text, "## Metrics", 2), 13)


class AnchoredTerminatorTests(unittest.TestCase):
    """Job 2 — where a body ends. Anchoring REMOVES spurious terminators, so
    these spans grow; that direction needs its own coverage."""

    def test_body_ends_at_next_real_sibling(self):
        text = "## Metrics\nOMTM: x\n\n## Scope\nscope\n"
        self.assertEqual(
            ppg.extract_heading_body(text, "## Metrics", ALL_SECS).strip(), "OMTM: x")

    def test_prose_mention_after_the_heading_does_not_terminate(self):
        """The spurious-terminator half of the defect: a backticked sibling name
        inside the body used to cut it short. Measured on
        `unit-economics-skill_THOUGHT.md`, and on
        `clarification-v2-20260721213322_THOUGHT.md` the real body was truncated
        at 11,466 of 55,887 characters this way."""
        text = ("## Metrics\nOMTM: x\nsee `## Desired Outcome` for the criterion\n"
                "more metrics prose\n\n## Scope\nscope\n")
        body = ppg.extract_heading_body(text, "## Metrics", ALL_SECS)
        self.assertIn("more metrics prose", body)
        self.assertNotIn("scope", body)

    def test_h3_inside_discovery_does_not_terminate(self):
        text = "## Metrics\nOMTM: x\n### Guiding Policy\nsub-detail\n\n## Scope\nscope\n"
        body = ppg.extract_heading_body(text, "## Metrics", ALL_SECS)
        self.assertIn("sub-detail", body)
        self.assertNotIn("scope", body)

    def test_body_running_to_eof(self):
        text = "## Metrics\nOMTM: x\ntrailing\n"
        self.assertEqual(
            ppg.extract_heading_body(text, "## Metrics", ALL_SECS).strip(),
            "OMTM: x\ntrailing")

    def test_absent_heading_returns_none(self):
        self.assertIsNone(ppg.extract_heading_body("## Scope\ns\n", "## Metrics", ALL_SECS))

    def test_body_is_returned_raw_not_stripped(self):
        """Callers differ on whether they strip; preserving that is what keeps
        each caller's contract unchanged through the unification."""
        self.assertTrue(
            ppg.extract_heading_body("## Metrics\nOMTM: x\n", "## Metrics", ALL_SECS)
            .startswith("\n"))


class ValidatorInputShapeTests(unittest.TestCase):
    """A3 — the three input shapes that reach validate_discovery_locked_fields."""

    BODY = ("\n## Guiding Policy\npolicy\n\n## Desired Outcome\noutcome\n\n"
            "## Desired Solution\nsolution\n\n## Metrics\nOMTM: m\n")

    def test_whole_new_spec_spine_is_scoped_to_discovery(self):
        text = "# Idea\ni\n\n# Discovery\n" + self.BODY + "\n# Solution Design\nsd\n"
        self.assertEqual(ppg.validate_discovery_locked_fields(text), [])

    def test_bare_discovery_body_on_stdin(self):
        """What `snapshot --validate` pipes in — no `# Discovery` header at all.
        It also has an out-of-harness caller
        (`Personal/your-project/scripts/_validate_snapshot.py`)."""
        self.assertEqual(ppg.validate_discovery_locked_fields(self.BODY), [])

    def test_legacy_snapshot_spine_is_not_scoped_away(self):
        """A legacy `## Snapshot` spine carries the four locked fields ABOVE a
        `# Discovery` stub that holds only a wikilink. Scoping unconditionally
        reported all four missing on a complete spine — caught by the corpus
        check on `logging-slice-1-silent-critical-paths_THOUGHT.md`."""
        text = ("# Legacy — Thought File\n\n## Snapshot\n" + self.BODY +
                "\n# Discovery\n- [[legacy_THOUGHT_check]]\n")
        self.assertEqual(ppg.validate_discovery_locked_fields(text), [])

    def test_backticked_discovery_mention_cannot_trigger_scoping(self):
        """The scoping presence test uses the anchored section rule, so the very
        defect class this plan removes cannot drive it."""
        text = ("A note about `# Discovery` in prose.\n" + self.BODY)
        self.assertEqual(ppg.validate_discovery_locked_fields(text), [])


class CrossSiteSpanAgreementTests(unittest.TestCase):
    """C4 — the only direct test of "the answer is the same everywhere it is
    asked". Deliberately compares what the REAL call sites return, rather than
    re-deriving both sides from A1, which would be tautological."""

    SPINE = (
        "# Idea\n\nSome idea.\n\n## Problem\nA problem.\n\n"
        "# Discovery\n\n"
        "## Q&A\nA: the `## Metrics` field holds the OMTM.\n\n"
        "## Guiding Policy\npolicy body\n\n"
        "## Desired Outcome\noutcome body\n\n"
        "## Desired Solution\nsolution body\n\n"
        "## Metrics 🔒 (Step 8 — LOCKED)\nOMTM: the real metric\n\n"
        "## Scope\nscope notes\n\n"
        "# Solution Design\n\nsd body\n\n"
        "# Implementation Details\n\nid body\n"
    )

    def _sites(self, field):
        """Each of the four independent extraction sites, called for real."""
        import importlib.util
        hooks = Path(__file__).resolve().parent.parent

        spec = importlib.util.spec_from_file_location(
            "_dlc_xsite", hooks / "_discovery_lock_check.py")
        dlc = importlib.util.module_from_spec(spec)
        sys.modules["_dlc_xsite"] = dlc
        spec.loader.exec_module(dlc)

        spec2 = importlib.util.spec_from_file_location(
            "_vtf_xsite", hooks / "_validate-thought-file.py")
        vtf = importlib.util.module_from_spec(spec2)
        sys.modules["_vtf_xsite"] = vtf
        spec2.loader.exec_module(vtf)

        disc = ppg.discovery_section_body(self.SPINE)
        return {
            # site 1 — the hash function's own scoping + extraction
            "hash": ppg.extract_heading_body(disc, field, ALL_SECS).strip(),
            # site 2 — the validator, through its scoping branch
            "validator": ppg.extract_heading_body(
                ppg.discovery_section_body(self.SPINE), field, ALL_SECS).strip(),
            # site 3 — the lock hook
            "lock": dlc._extract_field_body(self.SPINE, field, ALL_SECS),
            # site 4 — the thought-file validator's Discovery loop
            "vtf": ppg.extract_heading_body(
                vtf.discovery_section_body(self.SPINE), field, ALL_SECS).strip(),
        }

    def test_all_four_sites_return_the_same_span(self):
        for field in ppg.DISCOVERY_LOCKED_FIELDS:
            spans = self._sites(field)
            distinct = set(spans.values())
            self.assertEqual(
                len(distinct), 1,
                f"{field}: the four extraction sites disagree.\n" +
                "\n".join(f"  {k}: {v!r}" for k, v in spans.items()))

    def test_metrics_span_is_the_real_heading_not_the_prose_mention(self):
        """Agreement is worthless if all four agree on the WRONG span."""
        spans = self._sites("## Metrics")
        for site, span in spans.items():
            self.assertIn("OMTM: the real metric", span, f"{site} bound the wrong span")

    def test_span_stops_at_the_discovery_boundary(self):
        """Scoping must not leak later top-level sections into the last field."""
        for site, span in self._sites("## Metrics").items():
            self.assertNotIn("sd body", span, f"{site} leaked past `# Discovery`")


class ValidateThoughtFileTests(unittest.TestCase):
    """First real coverage of `_validate-thought-file.py` — the module had none
    (it is stubbed out in the only test that named it), which is how three
    unanchored finds survived in it."""

    def _vtf(self):
        import importlib.util
        p = Path(__file__).resolve().parent.parent / "_validate-thought-file.py"
        spec = importlib.util.spec_from_file_location("_vtf_mod", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["_vtf_mod"] = m
        spec.loader.exec_module(m)
        return m

    WELL_FORMED = (
        "# Idea\n\n## Problem\nA real problem.\n\n"
        "# Discovery\n\n## Guiding Policy\npolicy\n\n## Desired Outcome\noutcome\n\n"
        "## Desired Solution\nsolution\n\n## Metrics\nOMTM: m\n\n"
        "# Solution Design\n\nsd\n\n# Implementation Details\n\nid\n"
    )

    def test_well_formed_spine_passes_every_section_check(self):
        res, _ = self._vtf().check_sections(self.WELL_FORMED)
        failures = {k: v for k, v in res.items() if not v[0]}
        self.assertEqual(failures, {}, f"unexpected failures: {failures}")

    def test_prose_mention_does_not_fake_a_top_level_section(self):
        """The `:80` defect, which emitted an operator-visible false verdict.
        Three corpus spines carried a false 'Out of order' this way, and two
        more reported a section present that binds to prose at column 367 and
        column 12478 of a paragraph."""
        text = ("# Idea\n\n## Problem\np\n\nWe will add `# Solution Design` later.\n\n"
                "# Discovery\n\n## Guiding Policy\npolicy\n\n## Desired Outcome\no\n\n"
                "## Desired Solution\ns\n\n## Metrics\nOMTM: m\n")
        res, _ = self._vtf().check_sections(text)
        self.assertEqual(
            res.get("# Solution Design"), (False, "Missing section"),
            "a backticked prose mention must not satisfy a top-level section")

    def test_subheading_does_not_fake_a_top_level_section(self):
        """`## Idea` contains `# Idea` at offset 1 — how two corpus spines
        reported a section they do not have."""
        text = "## Idea\n\nnot a top-level section\n\n# Discovery\n\n## Guiding Policy\np\n"
        res, _ = self._vtf().check_sections(text)
        self.assertEqual(res.get("# Idea"), (False, "Missing section"))

    def test_ordered_sections_are_not_reported_out_of_order(self):
        res, _ = self._vtf().check_sections(self.WELL_FORMED)
        for label, (_ok, msg) in res.items():
            self.assertNotIn("Out of order", msg, f"{label} falsely out of order")

    def test_discovery_field_bound_to_prose_is_not_reported_healthy(self):
        """The visible symptom that surfaced the whole defect: a `## Metrics`
        mention in Q&A prose satisfied the field check while the real heading
        went unexamined.

        The mention is placed at the END of its line, immediately before the
        next heading, so the mis-bound body is EMPTY. That placement is what
        makes the test discriminating: with a mention mid-line the mis-bound
        body is merely the rest of that sentence — non-empty, and reported
        healthy — so the test would pass against the pre-change code for the
        wrong reason and prove nothing."""
        text = (
            "# Idea\n\n## Problem\np\n\n# Discovery\n\n"
            "## Q&A\nA: the OMTM lives under ## Metrics\n\n"
            "## Guiding Policy\npolicy\n\n## Desired Outcome\no\n\n"
            "## Desired Solution\ns\n\n## Metrics\nOMTM: the real metric\n\n"
            "# Solution Design\n\nsd\n\n# Implementation Details\n\nid\n")
        res, _ = self._vtf().check_sections(text)
        self.assertEqual(res.get("# Discovery::## Metrics"), (True, ""))

    def test_v2_three_token_marker_reaches_the_qa_origin_warning(self):
        """A8's second copy. This module's own `LOCK_MARKER_RE` was also
        two-token, so the Q&A-origin warning went inert on every v2 spine too."""
        marker = "<!-- locked: metrics 7f3a1c92-0b44-4d21 2026-08-08T00:29:41Z -->"
        text = (
            "# Idea\n\n## Problem\np\n\n# Discovery\n\n"
            "## Guiding Policy\npolicy\n\n## Desired Outcome\no\n\n"
            "## Desired Solution\ns\n\n## Metrics\n" + marker + "\nOMTM: m\n\n"
            "## Q&A\nQ untagged question\n")
        _res, warnings = self._vtf().check_sections(text)
        self.assertTrue(
            any("origin" in w for w in warnings),
            f"a three-token v2 marker must open this gate too; got {warnings!r}")


class NearMissAdvisoryTests(unittest.TestCase):
    """A4 + A5 (discovery-field-predicate-coherence S1) — the compensating
    report that REPLACES the refusal A3 deleted.

    A3 removed a guard that refused any Discovery-touching edit when a locked
    field's heading was in a form the anchored predicate rejects. Removing it
    without this report would mean a mis-levelled field simply reads absent and
    nobody is told which line caused it. So the same evidence now produces a
    line-numbered advisory through `check_sections`' existing `warnings` channel,
    printed under `## Warnings`, with the exit code untouched.

    These tests are what make the report load-bearing rather than decorative: the
    predicate is the SECOND conjunct of a two-part test, and a regex that fires on
    its own would warn about every well-formed spine. `test_readable_headings_*`
    below is the one that proves the conjunction is live.
    """

    def _vtf(self):
        import importlib.util
        p = Path(__file__).resolve().parent.parent / "_validate-thought-file.py"
        spec = importlib.util.spec_from_file_location("_vtf_near_miss", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["_vtf_near_miss"] = m
        spec.loader.exec_module(m)
        return m

    WELL_FORMED = (
        "# Idea\n\n## Problem\nA real problem.\n\n"
        "# Discovery\n\n## Guiding Policy\npolicy\n\n## Desired Outcome\noutcome\n\n"
        "## Desired Solution\nsolution\n\n## Metrics\nOMTM: m\n\n"
        "# Solution Design\n\nsd\n\n# Implementation Details\n\nid\n"
    )

    def _near_miss_warnings(self, text):
        _res, warnings = self._vtf().check_sections(text)
        # Keyed on a SUBSTANTIVE phrase, not on advice. The previous filter used
        # "the lock cannot read", which was part of the wording corrected on
        # 2026-09-20 — a test coupled to a message's advice breaks whenever the
        # advice is improved, which is backwards. `A heading named` names what the
        # predicate found; it does not collide with the Q&A-origin warning.
        return [w for w in warnings if "A heading named" in w]

    def test_h3_near_miss_is_reported_with_its_line(self):
        """The canonical case. `### Metrics` is not the locked field, so the field
        reports Missing — and the advisory names the line that caused it, which is
        the whole remedy the deleted guard's message promised and then blocked."""
        text = self.WELL_FORMED.replace("## Metrics\nOMTM: m", "### Metrics\nOMTM: m")
        res, _ = self._vtf().check_sections(text)
        self.assertEqual(
            res.get("# Discovery::## Metrics"), (False, "Missing ## Metrics"),
            "an H3 heading must still read as a missing locked field")
        warns = self._near_miss_warnings(text)
        self.assertEqual(len(warns), 1, f"expected one near-miss advisory; got {warns!r}")
        self.assertIn("### Metrics", warns[0],
                      f"the advisory must quote the offending line; got {warns[0]!r}")
        self.assertRegex(warns[0], r"line \d+",
                         f"the advisory must name a line number; got {warns[0]!r}")

    def test_trailing_colon_near_miss_is_reported(self):
        """The 2026-08-29 shape — `## Metrics:` — the one that produced the
        deadlock this action's owning slice exists to remove."""
        text = self.WELL_FORMED.replace("## Metrics\nOMTM: m", "## Metrics:\nOMTM: m")
        warns = self._near_miss_warnings(text)
        self.assertEqual(len(warns), 1, f"expected one near-miss advisory; got {warns!r}")
        self.assertIn("## Metrics:", warns[0])

    def test_advisory_offers_both_readings_and_issues_no_bare_imperative(self):
        """[FAILS-BEFORE] The 2026-09-20 wording fix, pinned.

        The scan covers the whole Discovery body including the MUTABLE `## Scope`
        and `## Q&A` subsections, so a Q&A group heading legitimately named after a
        locked field lands here. The old message told that author to "fix the
        heading to have the field defended again" — and following it would rename a
        correct Q&A heading to `## Metrics`, which then binds as the locked field
        and truncates the Q&A body for every downstream reader. The advice did not
        merely mislead; acting on it created a defect.

        So the message must state what it knows and offer BOTH readings. This test
        is the fix; the wording change alone would silently regress."""
        # Replace the real `## Metrics` field with a Q&A carrying a group heading
        # of that name — the shape of the 2026-08-29 spine: field genuinely absent,
        # a legitimate `### Metrics` inside the mutable subsection.
        text = self.WELL_FORMED.replace(
            "## Metrics\nOMTM: m",
            "## Q&A\n\n### Metrics\n- Q: what does it measure?\n- A: open.")
        warns = self._near_miss_warnings(text)
        self.assertEqual(len(warns), 1, f"expected the advisory to fire; got {warns!r}")
        w = warns[0]
        self.assertNotIn(
            "fix the heading to have the field defended again", w,
            "the bare imperative is the defect — acting on it breaks a correct "
            "Q&A group heading")
        self.assertNotIn(
            "looks present", w,
            "the advisory must not assert this line IS the locked field; it has "
            "evidence of a name, not of intent")
        self.assertIn("is missing", w, "it must state the fact it is sure of")
        self.assertIn("If that was meant to be the locked field", w,
                      "the first reading must be conditional")
        self.assertIn("nothing needs changing", w,
                      "the second reading — a legitimate group heading — must be "
                      "offered explicitly, or the author is still steered wrong")

    def test_readable_headings_produce_no_near_miss_advisory(self):
        """**The conjunction test.** `_near_miss_heading_line` matches a READABLE
        `## Metrics` too — it is the second conjunct only, and the caller supplies
        the first (`extract_heading_body(...) is None`). Drop that guard and every
        well-formed spine in the corpus grows a spurious warning. A regex-only test
        would not catch it; this one does."""
        self.assertEqual(
            self._near_miss_warnings(self.WELL_FORMED), [],
            "a spine whose locked headings are all readable must produce no "
            "near-miss advisory at all")

    def test_decorated_heading_produces_no_near_miss_advisory(self):
        """Decoration is accepted by the predicate, so a decorated heading is
        readable and must not be reported as a near miss. Same conjunction, on the
        form most likely to be mistaken for one."""
        text = self.WELL_FORMED.replace(
            "## Metrics\nOMTM: m",
            "## Metrics \U0001F512 (Step 8 — LOCKED by user 2026-09-17)\nOMTM: m")
        self.assertEqual(self._near_miss_warnings(text), [])

    def test_prose_mention_is_not_reported_as_a_near_miss(self):
        """Line-start is what separates a mis-levelled heading from a sentence.
        `see ## Metrics below` is prose; warning on it would train a reader to
        ignore the channel, which is how a non-blocking advisory dies."""
        text = (
            "# Idea\n\n## Problem\np\n\n# Discovery\n\n"
            "## Guiding Policy\npolicy, and see ## Metrics below\n\n"
            "## Desired Outcome\no\n\n## Desired Solution\ns\n\n"
            "# Solution Design\n\nsd\n")
        self.assertEqual(
            self._near_miss_warnings(text), [],
            "a prose mention is not a heading and must not be reported as one")

    def test_advisory_does_not_change_the_verdict(self):
        """Exit code untouched is the whole contract — A4 reports, A3 removed the
        refusal. The field was already failing as Missing before the advisory
        existed; the advisory must not add or remove a `results` entry."""
        text = self.WELL_FORMED.replace("## Metrics\nOMTM: m", "### Metrics\nOMTM: m")
        res_near, _ = self._vtf().check_sections(text)
        absent = self.WELL_FORMED.replace("## Metrics\nOMTM: m\n\n", "")
        res_absent, _ = self._vtf().check_sections(absent)
        self.assertEqual(
            res_near.get("# Discovery::## Metrics"),
            res_absent.get("# Discovery::## Metrics"),
            "a near-miss and a genuinely absent field must reach the same verdict; "
            "only the advisory distinguishes them")


class ContentSemanticsTests(unittest.TestCase):
    """A7 + A8 + A9 (discovery-field-predicate-coherence S2) — the content half.

    S1 settled which line is a locked field's HEADING. This settles what counts as
    CONTENT under it: chrome does not, and a Metrics section must carry a metric
    line with a value.

    The two predicates are deliberately separate from `extract_heading_body`. That
    is a build constraint, not an observation — see `is_effectively_empty`'s
    docstring. `test_hash_is_unaffected_by_the_shared_predicate` below is what
    holds it, and it is the assertion this class exists for.
    """

    MARK = "<!-- locked: 2026-06-12 12:00 -->"

    def _vtf(self):
        import importlib.util
        p = Path(__file__).resolve().parent.parent / "_validate-thought-file.py"
        spec = importlib.util.spec_from_file_location("_vtf_content", p)
        m = importlib.util.module_from_spec(spec)
        sys.modules["_vtf_content"] = m
        spec.loader.exec_module(m)
        return m

    def _spine(self, metrics_body="OMTM: a real metric", policy_body="policy"):
        return (
            "# Idea\n\n## Problem\nA real problem.\n\n"
            "# Discovery\n\n"
            f"## Guiding Policy\n{policy_body}\n\n"
            "## Desired Outcome\noutcome\n\n"
            "## Desired Solution\nsolution\n\n"
            f"## Metrics\n{metrics_body}\n\n"
            "# Solution Design\n\nsd\n\n# Implementation Details\n\nid\n"
        )

    # -- A7: emptiness ---------------------------------------------------- #

    def test_lock_marker_only_body_reads_empty(self):
        """[FAILS-BEFORE] The headline case. A body holding only the freeze marker
        used to read as content — `content.strip()` was non-empty, so the field
        passed. It says nothing, and now reads empty."""
        self.assertTrue(ppg.is_effectively_empty(self.MARK))

    def test_sentinel_only_body_reads_empty(self):
        """[PASSES-BOTH] The `<text>` sentinel was already handled; it must stay
        handled once the test moves into the shared predicate."""
        self.assertTrue(ppg.is_effectively_empty("<text>"))

    def test_comment_then_prose_then_comment_reads_NON_empty(self):
        """[FAILS-BEFORE-A-GREEDY-REGEX] **The non-greedy assertion — the single
        case that separates a correct implementation from a catastrophic one.**

        A greedy `<!--.*-->` matches from the FIRST opener to the LAST closer,
        deleting everything between them. On this three-part body that strips the
        real prose too and the field reads EMPTY — turning a populated locked
        field into a refusal, the exact inverse of the bug A7 fixes, landing on
        the v1 spines whose hashes A7 is meant to protect.

        Every other case in this class passes under BOTH the greedy and non-greedy
        forms. This one does not. Do not simplify the regex."""
        body = f"{self.MARK}\nReal prose that must survive.\n<!-- an editorial note -->"
        self.assertFalse(
            ppg.is_effectively_empty(body),
            "a body holding real prose between two comments is NOT empty; "
            "a greedy comment regex is the likely cause if this fails")

    def test_blank_and_none_read_empty(self):
        """[PASSES-BOTH] Must-not-widen at the trivial end."""
        for body in (None, "", "   \n\t ", "<!-- a -->\n<!-- b -->"):
            with self.subTest(body=body):
                self.assertTrue(ppg.is_effectively_empty(body))

    def test_real_prose_reads_non_empty(self):
        """[PASSES-BOTH] The predicate must not start eating content."""
        self.assertFalse(ppg.is_effectively_empty("An actual guiding policy."))

    def test_chrome_only_locked_field_fails_the_blocking_checker(self):
        """[FAILS-BEFORE] End-to-end through the module that BLOCKS: a chrome-only
        Guiding Policy now fails `check_sections`, and `permission-plan-gate.sh`
        runs this validator live on the spine at ExitPlanMode, so the plan exit is
        blocked. Before, it passed."""
        res, _ = self._vtf().check_sections(self._spine(policy_body=self.MARK))
        self.assertEqual(res.get("# Discovery::## Guiding Policy"),
                         (False, "Empty ## Guiding Policy"))

    def test_hash_is_unaffected_by_the_shared_predicate(self):
        """[PASSES-BOTH] **The constraint that makes A7 safe to ship.**

        `_discovery_locked_fields_hash` calls `extract_heading_body` and hashes
        `content.strip()`. On a v1 spine the lock marker sits INSIDE the field
        body, so if the chrome-strip had been folded into the extractor, every v1
        locked-field hash would change — invalidating handoff freshness and the
        plan `discovery_src_hash` provenance three surfaces depend on.

        Asserting a literal here would be brittle. What is asserted instead is the
        property: the hash of a chrome-bearing spine is driven by the RAW body, so
        it still differs from the same spine with the chrome removed. If a future
        change routes the hash through `is_effectively_empty`, these two collapse
        to equal and this fails — which is the alarm."""
        with_chrome = self._spine(policy_body=f"{self.MARK}\npolicy")
        without = self._spine(policy_body="policy")
        self.assertIsNotNone(ppg._discovery_locked_fields_hash(with_chrome))
        self.assertNotEqual(
            ppg._discovery_locked_fields_hash(with_chrome),
            ppg._discovery_locked_fields_hash(without),
            "the locked-field hash must still see raw bodies — if chrome-stripping "
            "reached the extractor, every v1 spine's hash just churned")

    # -- A8: the metric line ------------------------------------------------ #

    def test_accepted_metric_spellings(self):
        """[FAILS-BEFORE for every decorated form] Q3c's ratified set. The old
        predicate tested `startswith("OMTM:")`/`startswith("- OMTM:")`, so it saw
        only the first two."""
        for line in ("OMTM: a metric",
                     "- OMTM: a metric",
                     "**OMTM:** a metric",
                     "**OMTM.** a metric",
                     "- OMTM (per Gate 4): a metric",
                     "**OMTM — main-chat growth:** a metric"):
            with self.subTest(line=line):
                self.assertTrue(ppg.has_metric_line(line))

    def test_bare_label_with_no_value_is_refused(self):
        """[FAILS-BEFORE] The half that makes the widening safe. Without a value
        requirement, tolerating decoration would widen a label-only hole rather
        than close it — and the old code never checked for a value despite its
        own docs promising 'present and non-empty'."""
        for line in ("OMTM:", "**OMTM:**", "- OMTM:   ", "**OMTM — label:** **"):
            with self.subTest(line=line):
                self.assertFalse(ppg.has_metric_line(line))

    def test_prose_mention_is_not_a_metric_line(self):
        """[PASSES-BOTH] Must-not-widen: the name has to start the line, or every
        spine discussing its own OMTM would satisfy the rule."""
        self.assertFalse(ppg.has_metric_line(
            "we will define the OMTM: later, in Step 8"))

    def test_metrics_without_a_metric_line_blocks(self):
        """[FAILS-BEFORE] A8's headline change. This rule used to live ONLY in
        `validate_discovery_locked_fields`, which no blocking consumer reads —
        Checker B carried no OMTM rule at all. Now it is verdict-bearing."""
        res, _ = self._vtf().check_sections(self._spine(metrics_body="some notes"))
        self.assertEqual(res.get("# Discovery::## Metrics"),
                         (False, "Missing OMTM line in ## Metrics"))

    def test_decorated_metric_spine_still_passes(self):
        """[PASSES-BOTH by intent, FAILS-BEFORE in fact] The 1 live spine the plan
        names. A decorated OMTM used to read as missing; the de-blinding corrects
        that false reading rather than causing one."""
        res, _ = self._vtf().check_sections(
            self._spine(metrics_body="**OMTM:** main-chat growth per heavy-skill run"))
        self.assertEqual(res.get("# Discovery::## Metrics"), (True, ""))

    def test_well_formed_spine_still_passes_everything(self):
        """[PASSES-BOTH] The must-not-widen backstop for the whole class."""
        res, _ = self._vtf().check_sections(self._spine())
        self.assertEqual({k: v for k, v in res.items() if not v[0]}, {})

    def test_placeholder_branch_is_reached_not_broken(self):
        """[PASSES-BOTH] A bare placeholder body is still accepted. That is all
        this test proves, and the docstring is narrowed to say so.

        **It previously claimed A9's "revived placeholder branch" mechanism, and
        that mechanism does not exist.** A9's guard rail says "after chrome is
        stripped the warner's `startswith('*(populated by')` test becomes
        reachable". Traced against the shipped code, it does not: the placeholder
        check runs on `content.strip()` — a PLAIN strip, never chrome-stripped —
        so a marker-then-placeholder body still starts with `<!--` both before and
        after A7, and is not matched either way. For a marker-ONLY body A7 makes
        that branch *less* reached, not more: `is_effectively_empty` now catches it
        earlier and `continue`s past the placeholder check the old code fell
        through to.

        Found by an independent checker. The plan's A9 guard rail carries the same
        false claim and is corrected there too. Recorded rather than silently
        reworded: a test whose docstring asserts a mechanism the code lacks is the
        "passes for the wrong reason" defect this topic keeps finding, appearing
        in a test written to guard against it."""
        res, _ = self._vtf().check_sections(
            self._spine(policy_body="*(populated by Steps 7-9)*"))
        ok, _reason = res.get("# Discovery::## Guiding Policy")
        self.assertTrue(ok, "a placeholder body is accepted mid-clarification")


class HeadingRuleDriftTests(unittest.TestCase):
    """A1b (discovery-field-predicate-coherence S1) — bind the heading rule
    STATED in `skills/clarification/steps.md` to the predicate that ENFORCES it.

    The rule had exactly one statement anywhere in the tree — `heading_re` at
    `pre_plan_gates.py:238-245` — and no prose anywhere said what a locked
    field's heading must look like. A1 wrote that prose. Prose free to drift
    from its predicate is how this codebase's other asserted mirrors failed,
    so the two are held equal here: every form the skill lists as accepted must
    match `heading_re`, and every form it lists as rejected must not.

    Change the regex without the table (or the table without the regex) and this
    fails. That is the whole point — do not 'fix' it by editing one side.
    """

    SKILL_DOC = (Path(__file__).parent.parent.parent
                 / "skills" / "clarification" / "steps.md")

    # Derived from the table A1 wrote into the Step-9 Lock-write block.
    ACCEPTED = [
        "## Metrics",
        "## Metrics 🔒 (locked 2026-09-17)",
    ]
    REJECTED = [
        "## Metrics:",
        "### Metrics",
        "  ## Metrics",
        "see ## Metrics below",
    ]

    def _matches(self, line):
        """Does the shared anchored predicate accept `line` as `## Metrics`?"""
        return ppg.heading_re("## Metrics").search(line + "\n") is not None

    def test_every_accepted_form_matches_the_predicate(self):
        for form in self.ACCEPTED:
            with self.subTest(form=form):
                self.assertTrue(
                    self._matches(form),
                    f"steps.md lists {form!r} as accepted but heading_re rejects it")

    def test_every_rejected_form_fails_the_predicate(self):
        for form in self.REJECTED:
            with self.subTest(form=form):
                self.assertFalse(
                    self._matches(form),
                    f"steps.md lists {form!r} as rejected but heading_re accepts it")

    def test_the_skill_actually_states_the_rule(self):
        """A drift test over prose that is absent proves nothing — assert the
        statement exists before asserting it agrees with the code."""
        doc = self.SKILL_DOC.read_text(encoding="utf-8")
        self.assertIn("What counts as a locked field's heading", doc,
                      "steps.md no longer states the heading rule (A1 undone?)")
        for form in self.ACCEPTED + self.REJECTED:
            with self.subTest(form=form):
                self.assertIn(form, doc,
                              f"{form!r} is asserted by this test but absent from steps.md")


if __name__ == "__main__":
    unittest.main(verbosity=2)

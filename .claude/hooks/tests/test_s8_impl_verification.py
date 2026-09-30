#!/usr/bin/env python3
"""Slice S8 — WHOLE-SYSTEM implementation verification (research-fc-checker-timeout).

Plan: Thoughts/research-fc-checker-timeout_PLAN.md (Mode A, S8 / A1).
Spine: Thoughts/research-fc-checker-timeout_THOUGHT.md + _DESIGN.md (Alt 1).

Role: knowledge_vs_implementation. This module ASSERTS the assembled S1–S7
engine behaviour at the shared `factcheck_run` terminal; it NEVER modifies the
engine. It drives the REAL `factcheck_run` and the REAL axis gates
(`_run_source_integrity_gate`, `_run_coverage_axis_gate`), faking ONLY the two
Python-uncrossable seams:
  * the content-checker LLM dispatch  — via `_checker_fn` (factcheck_run seam)
  * the coverage-checker LLM dispatch — via `_invoke_coverage_checker` (the
    default the REAL `_run_coverage_check` falls through to; the gate is NOT
    threaded a coverage `_checker_fn` from `factcheck_run`, so this is the only
    reachable coverage-LLM seam)
  * the network                       — `_fetch_one_url` / `_archive_lookup`

Every code-reachable seam (URL extraction, prefetch orchestration, source
classification, archive-promotion, in-place repair, angle loading via sidecar,
coverage parse/fold, severity-max verdict fold, marker writeback) runs for real.

NO live network, NO live model, NO writes outside per-test tmp dirs.

Test-class → SC map:
  Sc1GenuinePassOnlyWhenAllAxesPass   — SC1 (a)(b)(c)
  Sc2HonestIncompleteAndAcceptToClose — SC2
  Sc3BothCallersIdentical             — SC3 (a)(b)(c)
  Sc4A18FailSafe                      — SC4 (i)(ii)(iii)
"""
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# conftest.py pins the clone copy of _factcheck_engine onto sys.modules ahead of
# any live-harness path a sibling test inserts; this bare import binds the clone
# copy consistently under a full-suite run.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402

HOOKS_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Shared fakes (reuse the S1/S6/S7 shapes verbatim).
# ---------------------------------------------------------------------------
def _content_pass(dp, idx, model, rnd, prior):
    """Content checker: an explicit final VERDICT: PASS (research kind reads the
    final token only — see `_verdict_bucket`)."""
    return "reasoning about each claim\nVERDICT: PASS"


def _content_incomplete(dp, idx, model, rnd, prior):
    """Content checker over-budget / a source UNFETCHABLE it could not verify →
    the INCOMPLETE sentinel (never a DISCREPANCY on a can't-finish)."""
    return f"{eng.INCOMPLETE_SENTINEL} checker exceeded budget; source unverifiable"


def _fetch_ok(url, timeout_s, max_bytes):
    return ("ok", "the supporting source content")


def _fetch_404(url, timeout_s, max_bytes):
    return ("unfetchable", "HTTP 404")


def _no_archive(url):
    return None


def _coverage_all_covered(report_text, angles):
    """Fake coverage LLM: every angle covered."""
    return json.dumps(
        {"coverage": [{"angle": i, "covered": True}
                      for i, _ in enumerate(angles, start=1)]}
    )


def _coverage_drop_last(report_text, angles):
    """Fake coverage LLM: the LAST angle in the checklist is NOT covered."""
    entries = [{"angle": i, "covered": (i != len(angles))}
               for i, _ in enumerate(angles, start=1)]
    return json.dumps({"coverage": entries})


def _latest_marker_field(topic_dir, field, key=None):
    """Newest marker's `field`, across BOTH naming shapes.

    Since the per-file keying slice (Group I item 2 / S1) the research kind's
    canonical marker is `<key>_R<n>.md`; every other kind still writes the
    unkeyed `R<n>.md`. This helper reads whichever is present, ordered by round
    NUMBER (a lexical sort would put R10 before R2), so it stays correct for
    both shapes rather than silently returning None once a marker is keyed.
    """
    d = Path(topic_dir)
    if not d.exists():
        return None

    def _newest(pattern, keyed):
        found = []
        for p in d.glob(pattern):
            m = re.match(r"^(?:(?P<key>.+)_)?R(?P<round>\d+)\.md$", p.name)
            if not m:
                continue
            if keyed != (m.group("key") is not None):
                continue
            if key is not None and keyed and m.group("key") != key:
                continue
            found.append((int(m.group("round")), p))
        return sorted(found)[-1][1] if found else None

    # NEWEST WINS ACROSS BOTH SHAPES, by round number.
    #
    # This ordering used to be "unkeyed first, deliberately", on the premise that
    # the three writers outside the rounds loop (source-integrity, coverage,
    # accept) all still landed unkeyed, so an unkeyed marker was necessarily the
    # terminal axis verdict. **That premise is now stale for all three.**
    # S2 threads the keyed carrier into both axis gates, so source-integrity and
    # coverage downgrades land KEYED, and S8 does the same for the accept writer
    # (`accept_research_incomplete` now builds the slot and passes it), so an
    # accept for a named file lands in that file's own sequence too. An unkeyed
    # marker no longer implies anything about which writer produced it.
    #
    # Preferring unkeyed unconditionally would therefore now return a stale
    # ROUND marker in preference to a newer keyed AXIS marker. Comparing both
    # shapes on round number restores "terminal verdict wins" without depending
    # on which writer happened to produce it. (It did not change any current
    # verdict — the two orderings coincide wherever both shapes are present in
    # this file's fixtures — but it was resting on an assumption that no longer
    # holds.)
    _candidates = [p for p in (_newest("R*.md", keyed=False),
                               _newest("*_R*.md", keyed=True)) if p is not None]

    def _round_of(p):
        m = re.match(r"^(?:(?P<key>.+)_)?R(?P<round>\d+)\.md$", p.name)
        return int(m.group("round")) if m else -1

    newest = max(_candidates, key=_round_of) if _candidates else None
    if newest is None:
        return None
    for line in newest.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{field}:"):
            return line.split(":", 1)[1].strip()
    return None


# ===========================================================================
# SC1 — GENUINE PASS ONLY WHEN ALL AXES PASS (the conjunction, at the terminal)
# ===========================================================================
class Sc1GenuinePassOnlyWhenAllAxesPass(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft = self.tmp / "topic_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_sidecar(self, angles):
        (self.tmp / "topic_RESEARCH_angles.json").write_text(
            json.dumps({"angles": angles, "provenance": "USER_CONFIRMED"}),
            encoding="utf-8",
        )

    def _run(self, sid, content_checker, *, extra_patches=()):
        """Drive the REAL factcheck_run; fake only content LLM + coverage LLM +
        network. Both axis gates run for real."""
        patches = [
            mock.patch.object(eng, "_write_research_frontmatter_for_terminal"),
            mock.patch.object(eng, "_log_factcheck_run"),
        ]
        patches.extend(extra_patches)
        with _nest(patches):
            return eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft),
                kind="research",
                session_id=sid,
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=content_checker,
                proj="p",
                topic="t",
            )

    def test_sc1a_clean_run_all_axes_pass_is_pass(self):
        """SC1(a): content PASS + all links ok + all angles covered → terminal PASS.
        The only path that yields a genuine PASS — every real axis gate ran."""
        self.draft.write_text(
            "Claim [stated — https://ok.example/a] with detail.\n", encoding="utf-8"
        )
        self._write_sidecar(["angle one", "angle two"])
        result = self._run(
            "11111111-1111-1111-1111-111111111111",
            _content_pass,
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_ok),
                mock.patch.object(eng, "_invoke_coverage_checker",
                                  side_effect=_coverage_all_covered),
            ],
        )
        self.assertEqual(result["status"], "PASS")
        # All-covered coverage writes NO downgrade marker (in-memory OK only).
        self.assertEqual(result.get("coverage_axis"), "OK")

    def test_sc1b_dead_link_blocks_pass_is_escalate(self):
        """SC1(b): content PASS + a fabricated/dead link (404, no archive) →
        source-integrity BLOCK dominates → NOT PASS (ESCALATE — terminal, since
        the gate returns rather than re-dispatching). Coverage axis is isolated
        OUT here (passthrough) exactly as test_s6 does, to prove the source axis
        alone can veto a content PASS."""
        self.draft.write_text(
            "Claim [stated — https://fabricated.example/x].\n", encoding="utf-8"
        )
        result = self._run(
            "22222222-2222-2222-2222-222222222222",
            _content_pass,
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_404),
                mock.patch.object(eng, "_archive_lookup", side_effect=_no_archive),
                # Isolate the source axis: coverage passes the verdict through
                # untouched (identical to test_s6's whole-run isolation).
                mock.patch.object(
                    eng, "_run_coverage_axis_gate",
                    side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict),
            ],
        )
        self.assertEqual(result["status"], "ESCALATE")
        self.assertNotEqual(result["status"], "PASS")

    def test_sc1c_dropped_angle_blocks_pass_is_escalate(self):
        """SC1(c) — the key new proof. content PASS + all links ok + a DROPPED
        framing angle. The REAL `_run_coverage_axis_gate` runs (NOT patched
        away); ONLY the coverage LLM is faked (via `_invoke_coverage_checker`,
        the default the real `_run_coverage_check` falls through to). A dropped
        angle → the coverage hard block dominates → NOT a genuine PASS."""
        self.draft.write_text(
            "Claim [stated — https://ok.example/a].\n", encoding="utf-8"
        )
        self._write_sidecar(["kept angle", "dropped angle"])
        result = self._run(
            "33333333-3333-3333-3333-333333333333",
            _content_pass,
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_ok),
                # ONLY the coverage LLM is faked; the gate itself runs for real.
                mock.patch.object(eng, "_invoke_coverage_checker",
                                  side_effect=_coverage_drop_last),
            ],
        )
        self.assertEqual(result["status"], "ESCALATE",
                         "a silently dropped framing angle must veto the PASS")
        self.assertNotEqual(result["status"], "PASS")
        # The real coverage marker names the dropped angle (proves the real gate ran).
        topic_dir = self.state_dir / "p" / "t" / "research"
        self.assertEqual(_latest_marker_field(topic_dir, "verdict"), "ESCALATE")
        # Locator must match the keyed shape too — this line was the one raw
        # `R*.md` glob the key-aware helper above did not cover.
        _found = []
        for _p in topic_dir.glob("*.md"):
            _m = re.match(r"^(?:(?P<key>.+)_)?R(?P<round>\d+)\.md$", _p.name)
            if _m:
                _found.append((int(_m.group("round")), _p))
        self.assertTrue(_found, "a coverage marker was written")
        marker = sorted(_found)[-1][1].read_text(encoding="utf-8")
        self.assertIn("dropped angle", marker)
        self.assertEqual(_latest_marker_field(topic_dir, "coverage_provenance"),
                         "USER_CONFIRMED")


# ===========================================================================
# SC2 — HONEST INCOMPLETE + ACCEPT-TO-CLOSE
# ===========================================================================
class Sc2HonestIncompleteAndAcceptToClose(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft = self.tmp / "topic_RESEARCH.md"
        self.draft.write_text("Claim body, no citations.\n", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sc2_incomplete_terminal_then_accept_to_close(self):
        """SC2: a content checker that returns the INCOMPLETE sentinel →
        terminal status INCOMPLETE (distinct from DIRTY) AND the latest R<N>.md
        marker verdict is INCOMPLETE. Then the operator accepts-to-close via
        `write_accept_marker`, which records the reason — the close path is an
        EXPLICIT accepted-INCOMPLETE, never a silent PASS."""
        with mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            result = eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft),
                kind="research",
                session_id="44444444-4444-4444-4444-444444444444",
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=_content_incomplete,
                proj="p",
                topic="t",
            )
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertNotEqual(result["status"], "DIRTY")
        self.assertNotEqual(result["status"], "PASS")

        topic_dir = self.state_dir / "p" / "t" / "research"
        self.assertEqual(_latest_marker_field(topic_dir, "verdict"), "INCOMPLETE")
        # Pre-accept, NO marker carries an accept_reason (close gate still blocks).
        # Assert over EVERY marker in either naming shape rather than only the
        # newest: since S1 the round marker is keyed (`<key>_R<n>.md`) while the
        # axis/accept writers are still unkeyed, so a single-glob spot check
        # would silently skip half the directory.
        pre_accept = [p.read_text(encoding="utf-8")
                      for p in topic_dir.glob("*R*.md")]
        self.assertTrue(pre_accept, "at least one marker exists pre-accept")
        for text in pre_accept:
            self.assertNotIn("accept_reason:", text)

        # Accept-to-close: stamps a NEW next-round INCOMPLETE marker with the reason.
        reason = "operator accepts: two sources bot-blocked, verified manually"
        marker = eng.write_accept_marker(topic_dir, reason)
        self.assertTrue(marker.exists())
        # The accept marker is now the LATEST — verdict INCOMPLETE + records reason.
        self.assertEqual(_latest_marker_field(topic_dir, "verdict"), "INCOMPLETE")
        accept_text = marker.read_text(encoding="utf-8")
        self.assertIn("accept_reason:", accept_text)
        self.assertIn(reason, accept_text)

    def test_sc2_empty_accept_reason_refused(self):
        """SC2 corollary: an accept with no recorded reason is NOT a sanctioned
        close (write_accept_marker raises) — there is no silent accept path."""
        topic_dir = self.state_dir / "p" / "t" / "research"
        topic_dir.mkdir(parents=True)
        with self.assertRaises(ValueError):
            eng.write_accept_marker(topic_dir, "   ")


# ===========================================================================
# SC3 — BOTH CALLERS IDENTICAL (no per-caller patching)
# ===========================================================================
class Sc3BothCallersIdentical(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sc3a_terminal_has_no_caller_identity_parameter(self):
        """SC3(a): the `factcheck_run` terminal has NO caller-identity parameter
        — the engine cannot branch on who wrote the report."""
        params = inspect.signature(eng.factcheck_run).parameters
        self.assertNotIn("caller", params)
        self.assertNotIn("caller_skill", params)

    def test_sc3b_dispatcher_routes_by_path_not_caller(self):
        """SC3(b): the PostToolUse dispatcher routes by path-glob with NO branch
        on session/caller identity, and issues the single `factcheck-research`
        dispatch."""
        text = (HOOKS_DIR / "factcheck-research-file.sh").read_text(encoding="utf-8")
        self.assertNotIn("caller_skill", text)
        self.assertNotIn("caller", text)
        # Exactly one factcheck-research dispatch (the single terminal).
        self.assertEqual(text.count("factcheck-research \\"), 1)
        # Routing is path-glob based (a case over $FILE_PATH), never session id.
        self.assertIn('case "$FILE_PATH" in', text)

    def test_sc3c_two_identical_drafts_yield_identical_terminal(self):
        """SC3(c) behavioural: two structurally-identical drafts (a
        standalone-written vs a sub-agent-written _RESEARCH.md) run through the
        SAME `factcheck_run` with the SAME fakes → identical terminal status +
        identical axis outcome. The engine layer treats them the same — no
        per-caller branch exists to make them differ."""
        results = []
        for i, sid in enumerate(
            ("55555555-5555-5555-5555-555555555555",
             "66666666-6666-6666-6666-666666666666")
        ):
            draft = self.tmp / f"topic{i}_RESEARCH.md"
            draft.write_text(
                "Claim [stated — https://ok.example/a] with detail.\n",
                encoding="utf-8",
            )
            (self.tmp / f"topic{i}_RESEARCH_angles.json").write_text(
                json.dumps({"angles": ["a1", "a2"], "provenance": "USER_CONFIRMED"}),
                encoding="utf-8",
            )
            with mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_ok), \
                    mock.patch.object(eng, "_invoke_coverage_checker",
                                      side_effect=_coverage_all_covered), \
                    mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                    mock.patch.object(eng, "_log_factcheck_run"):
                results.append(eng.factcheck_run(
                    state_dir=str(self.state_dir),
                    draft_path=str(draft),
                    kind="research",
                    session_id=sid,
                    debounce_seconds=0,
                    models=["sonnet", "sonnet", "sonnet"],
                    max_rounds=2,
                    _checker_fn=_content_pass,
                    proj="p",
                    topic=f"t{i}",  # distinct topic dirs; same engine treatment
                ))
        # Identical terminal status AND identical axis outcome.
        self.assertEqual(results[0]["status"], results[1]["status"])
        self.assertEqual(results[0]["status"], "PASS")
        self.assertEqual(results[0].get("coverage_axis"),
                         results[1].get("coverage_axis"))


# ===========================================================================
# SC4 — A18 FAIL-SAFE (Layer-2 off / dependency unreachable → INCOMPLETE,
#        never crash, never silent PASS)
# ===========================================================================
class Sc4A18FailSafe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft = self.tmp / "topic_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_sidecar(self, angles):
        (self.tmp / "topic_RESEARCH_angles.json").write_text(
            json.dumps({"angles": angles, "provenance": "USER_CONFIRMED"}),
            encoding="utf-8",
        )

    def _run(self, sid, *, extra_patches=()):
        patches = [
            mock.patch.object(eng, "_write_research_frontmatter_for_terminal"),
            mock.patch.object(eng, "_log_factcheck_run"),
        ]
        patches.extend(extra_patches)
        with _nest(patches):
            return eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft),
                kind="research",
                session_id=sid,
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=_content_pass,
                proj="p",
                topic="t",
            )

    def test_sc4i_fetch_unreachable_degrades_to_incomplete(self):
        """SC4(i): the fetch dependency is unreachable (`_fetch_one_url` raises).
        The prefetch orchestrator classifies it UNFETCHABLE (a generic error →
        transient), so the REAL source-integrity axis folds to INCOMPLETE. No
        exception propagates; the terminal is never a silent PASS. Layer-1
        content verdict integrity is intact (a real content error would still be
        a hard block — here content is PASS, so the axis fail-safe is what shows)."""
        self.draft.write_text(
            "Claim [stated — https://unreachable.example/x].\n", encoding="utf-8"
        )

        def fetch_raises(url, timeout_s, max_bytes):
            raise RuntimeError("network stack down")

        # Isolate the source axis so this proves the source fail-safe specifically.
        result = self._run(
            "aaaaaaaa-0000-0000-0000-000000000001",
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=fetch_raises),
                mock.patch.object(eng, "_archive_lookup", side_effect=_no_archive),
                mock.patch.object(
                    eng, "_run_coverage_axis_gate",
                    side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict),
            ],
        )
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertNotEqual(result["status"], "PASS")

    def test_sc4ii_coverage_dispatch_fails_degrades_to_incomplete(self):
        """SC4(ii): the coverage LLM dispatch fails (`_invoke_coverage_checker`
        raises). The REAL coverage axis catches it (can't-run) and folds to
        INCOMPLETE — never crashes, never a silent PASS. Content PASS + links ok,
        so INCOMPLETE is purely the coverage fail-safe surfacing."""
        self.draft.write_text(
            "Claim [stated — https://ok.example/a].\n", encoding="utf-8"
        )
        self._write_sidecar(["angle one", "angle two"])

        def coverage_raises(report_text, angles):
            raise RuntimeError("coverage model exploded")

        result = self._run(
            "aaaaaaaa-0000-0000-0000-000000000002",
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_ok),
                mock.patch.object(eng, "_invoke_coverage_checker",
                                  side_effect=coverage_raises),
            ],
        )
        self.assertEqual(result["status"], "INCOMPLETE")
        self.assertNotEqual(result["status"], "PASS")
        topic_dir = self.state_dir / "p" / "t" / "research"
        self.assertEqual(_latest_marker_field(topic_dir, "verdict"), "INCOMPLETE")

    def test_sc4iii_archive_lookup_fails_no_crash_no_silent_pass(self):
        """SC4(iii): the archive dependency fails (`_archive_lookup` raises) while
        a cited link is a 404 (so the archive path is actually consulted). The
        source gate swallows the archive failure per-URL → treats it as no
        snapshot → 404 stale promotes to hallucinated → ESCALATE. No exception
        propagates; the terminal is never a silent PASS."""
        self.draft.write_text(
            "Claim [stated — https://dead.example/x].\n", encoding="utf-8"
        )

        def archive_raises(url):
            raise RuntimeError("wayback down")

        result = self._run(
            "aaaaaaaa-0000-0000-0000-000000000003",
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_404),
                mock.patch.object(eng, "_archive_lookup", side_effect=archive_raises),
                mock.patch.object(
                    eng, "_run_coverage_axis_gate",
                    side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict),
            ],
        )
        self.assertNotEqual(result["status"], "PASS",
                            "an archive failure must never yield a silent PASS")
        # Concretely: 404 + no reachable archive → BLOCK → ESCALATE.
        self.assertEqual(result["status"], "ESCALATE")


# ===========================================================================
# E2c — the report's OWN audit row, and the two bounding claims.
#
# Correcting the gates' terminal token to ESCALATE activates the `_RESEARCH.md`
# frontmatter writeback (keyed on ESCALATE), which a gate rejection previously
# never reached. Activating it is only an improvement if the row carries a usable
# reason — so these assert the RENDERED row, not that the writeback was called.
# ===========================================================================
class E2cAuditRowAndBoundingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft = self.tmp / "topic_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_sidecar(self, angles):
        (self.tmp / "topic_RESEARCH_angles.json").write_text(
            json.dumps({"angles": angles, "provenance": "USER_CONFIRMED"}),
            encoding="utf-8",
        )

    def _run(self, sid, *, extra_patches=()):
        """Drive the REAL factcheck_run with the REAL frontmatter writeback — the
        writeback is deliberately NOT mocked here, so the assertions below read
        the row a human would actually open the report and see."""
        patches = [mock.patch.object(eng, "_log_factcheck_run")]
        patches.extend(extra_patches)
        with _nest(patches):
            return eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft),
                kind="research",
                session_id=sid,
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=_content_pass,
                proj="p",
                topic="t",
            )

    def _cycles_summary(self):
        """The discrepancy_summary line from the report's own fc_cycles block."""
        text = self.draft.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith("discrepancy_summary:"):
                return line.split(":", 1)[1].strip().strip('"')
        return None

    def test_source_block_writes_a_non_empty_audit_row(self):
        """A single-gate rejection leaves a row in the report naming the URL."""
        self.draft.write_text(
            "Claim [stated — https://fabricated.example/x].\n", encoding="utf-8"
        )
        result = self._run(
            "e2c00000-0000-0000-0000-000000000001",
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_404),
                mock.patch.object(eng, "_archive_lookup", side_effect=_no_archive),
                mock.patch.object(
                    eng, "_run_coverage_axis_gate",
                    side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict),
            ],
        )
        self.assertEqual(result["status"], "ESCALATE")
        body = self.draft.read_text(encoding="utf-8")
        self.assertIn('verdict: "ESCALATE"', body,
                      "the report must carry its own terminal row")
        summary = self._cycles_summary()
        self.assertTrue(summary and summary.strip(),
                        "an activated audit row must never be blank")
        self.assertIn("https://fabricated.example/x", summary)

    def test_dual_gate_rejection_names_both_reasons(self):
        """Both gates run in sequence on ONE verdict dict — the second must not
        overwrite the first's reason, or half the story is lost."""
        self.draft.write_text(
            "Claim [stated — https://fabricated.example/x].\n", encoding="utf-8"
        )
        self._write_sidecar(["kept angle", "dropped angle"])
        result = self._run(
            "e2c00000-0000-0000-0000-000000000002",
            extra_patches=[
                mock.patch.object(eng, "_fetch_one_url", side_effect=_fetch_404),
                mock.patch.object(eng, "_archive_lookup", side_effect=_no_archive),
                mock.patch.object(eng, "_invoke_coverage_checker",
                                  side_effect=_coverage_drop_last),
            ],
        )
        self.assertEqual(result["status"], "ESCALATE")
        # Both axes rejected, and BOTH are named on the one row.
        self.assertEqual(result.get("source_integrity"), "BLOCK")
        self.assertEqual(result.get("coverage_axis"), "BLOCK")
        summary = self._cycles_summary()
        self.assertTrue(summary and summary.strip())
        self.assertIn("https://fabricated.example/x", summary)
        self.assertIn("dropped angle", summary)

    def test_reason_accumulator_never_overwrites_or_duplicates(self):
        """The accumulator's contract, asserted directly: append, keep order,
        ignore blanks, and stay idempotent under a repeated gate run."""
        verdict = {"status": "PASS"}
        eng._append_gate_reason(verdict, "first reason")
        eng._append_gate_reason(verdict, "")
        eng._append_gate_reason(verdict, "second reason")
        eng._append_gate_reason(verdict, "first reason")  # idempotent
        self.assertEqual(verdict["reason"], "first reason; second reason")


class E2cCloseGateDispositionTests(unittest.TestCase):
    """C3 + C4 bounding: the CLOSE-GATE disposition of a rejected report is
    unchanged by the token correction, and a clean report still closes."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.research = (self.home / ".claude" / "state" / "plan_validation"
                         / "p" / "t" / "research")
        self.research.mkdir(parents=True)
        active = self.home / ".claude" / "state" / "pre_plan_gates"
        active.mkdir(parents=True)
        # NB: check-research-gate.sh reads PROJ from .topic_slug and TOPIC from
        # .active_project — mirrored here so the dirs above resolve.
        (active / "_active.json").write_text(
            json.dumps({"sid-e2c": {"topic_slug": "p", "active_project": "t"}}),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _close_gate(self, verdict):
        (self.research / "R1.md").write_text(
            f"---\nschema_version: 3\nkind: research\nverdict: {verdict}\n"
            f"rounds: 1\n---\n\nbody\n",
            encoding="utf-8",
        )
        env = dict(os.environ, HOME=str(self.home))
        env.pop("CLAUDE_CODE_REMOTE", None)
        proc = subprocess.run(
            ["bash", str(HOOKS_DIR / "check-research-gate.sh")],
            input=json.dumps({"session_id": "sid-e2c"}),
            capture_output=True, text=True, env=env,
        )
        return proc.returncode, proc.stderr

    def test_escalate_blocks_exactly_as_dirty_did(self):
        dirty_rc, _ = self._close_gate("DIRTY")
        esc_rc, esc_err = self._close_gate("ESCALATE")
        self.assertEqual(dirty_rc, 2, "a rejected report blocked before the change")
        self.assertEqual(esc_rc, dirty_rc,
                         "the corrected token must not change how hard it blocks")
        self.assertIn("BLOCKED", esc_err)

    def test_clean_report_still_closes(self):
        rc, err = self._close_gate("PASS")
        self.assertEqual(rc, 0, "the clean path must be untouched")
        self.assertNotIn("BLOCKED", err)


class S3ObligationBoundaryTests(unittest.TestCase):
    """S3 — the two A4 properties that were true by inspection and by nobody's test.

    Both are boundary claims rather than behaviour claims, which is exactly why
    they slipped: the S2 suite exercises what the obligation block DOES, and
    these assert where it sits and what it does not reach. A boundary nothing
    tests is a boundary the next insertion moves for free.
    """

    CLOSE_GATE = HOOKS_DIR / "check-research-gate.sh"
    PIPELINE_GATE = HOOKS_DIR / "check-research-pipeline-gate.sh"
    SID = "s3-boundary"

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        oblig = self.home / ".claude" / "state" / "research_obligations"
        oblig.mkdir(parents=True)
        self.research = self.home / "unchecked-20260101010101_RESEARCH.md"
        self.research.write_text("# body\n", encoding="utf-8")
        # An obligation this session owes and nothing has answered — the state
        # in which the close gate blocks. Every assertion below is about what
        # happens to that block, so a fixture that did not block would make the
        # whole class vacuous.
        (oblig / f"{self.SID}.ledger").write_text(
            f"2026-09-06T10:00:00Z\t{self.SID}\t{self.research}\n", encoding="utf-8")

    def _env(self, **extra):
        env = dict(os.environ)
        env["HOME"] = str(self.home)
        env.pop("RP_STATE_DIR", None)
        env.pop("CLAUDE_CODE_REMOTE", None)
        env.update(extra)
        return env

    def _run(self, gate, payload, **env_extra):
        return subprocess.run(
            ["bash", str(gate)], input=json.dumps(payload),
            capture_output=True, text=True, env=self._env(**env_extra),
            timeout=180)

    def test_the_obligation_block_still_sits_behind_the_two_early_exits(self):
        """A4's guard rail puts the block AFTER the CLAUDE_CODE_REMOTE and
        stop_hook_active guards and BEFORE the _active.json exits. The S2 suite
        pinned the second half by blocking an unbound session; the first half
        had no carrier at all.

        It matters in both directions. `CLAUDE_CODE_REMOTE` marks an
        environment this gate is deliberately inert in, and `stop_hook_active`
        is the loop guard — a block emitted from inside a Stop hook that the
        Stop hook itself triggered does not terminate. Hoisting the obligation
        read above either one would turn a fix into a hang."""
        blocking = self._run(self.CLOSE_GATE,
                             {"session_id": self.SID, "stop_hook_active": False})
        self.assertEqual(blocking.returncode, 2,
                         f"precondition: this fixture must block; {blocking.stderr!r}")

        remote = self._run(self.CLOSE_GATE,
                           {"session_id": self.SID, "stop_hook_active": False},
                           CLAUDE_CODE_REMOTE="true")
        self.assertEqual(remote.returncode, 0,
                         "CLAUDE_CODE_REMOTE must still short-circuit ahead of "
                         "the obligation read")
        self.assertEqual(remote.stderr, "")

        looping = self._run(self.CLOSE_GATE,
                            {"session_id": self.SID, "stop_hook_active": True})
        self.assertEqual(looping.returncode, 0,
                         "the stop_hook_active loop guard must still fire first")
        self.assertEqual(looping.stderr, "")

    def test_the_pipeline_gate_stays_informational_under_an_unmet_obligation(self):
        """A4's guard rail confines ENFORCEMENT to the close gate: 'the pipeline
        gate's no-manifest branch stays informational'.

        Asserted behaviourally rather than by grepping the file for the verb's
        name. A grep proves only that this slice did not add the call; it says
        nothing about the gate acquiring an obligation-shaped block some other
        way, which is the thing the rail actually forbids."""
        proc = self._run(self.PIPELINE_GATE,
                         {"session_id": self.SID, "stop_hook_active": False})
        self.assertEqual(
            proc.returncode, 0,
            "the pipeline gate must not block on an obligation the close gate "
            f"owns; stderr={proc.stderr!r}")
        self.assertNotIn(str(self.research), proc.stderr,
                         "nor name the artifact — that report belongs to the "
                         "close gate alone")

    def test_enforcement_lives_in_exactly_one_gate(self):
        """The pair, stated as one fact: on identical input the close gate
        blocks and the pipeline gate does not. Neither half alone says this —
        the first could pass with both gates blocking, the second with neither."""
        payload = {"session_id": self.SID, "stop_hook_active": False}
        close = self._run(self.CLOSE_GATE, payload)
        pipeline = self._run(self.PIPELINE_GATE, payload)
        self.assertEqual((close.returncode, pipeline.returncode), (2, 0))


# ---------------------------------------------------------------------------
# Small helper: nest a list of context managers (py3.8-safe, no ExitStack noise).
# ---------------------------------------------------------------------------
class _nest:
    def __init__(self, cms):
        self._cms = list(cms)

    def __enter__(self):
        self._entered = []
        for cm in self._cms:
            cm.__enter__()
            self._entered.append(cm)
        return self

    def __exit__(self, *exc):
        for cm in reversed(self._entered):
            cm.__exit__(*exc)
        return False


if __name__ == "__main__":
    unittest.main()

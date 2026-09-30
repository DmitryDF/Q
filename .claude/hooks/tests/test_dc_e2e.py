#!/usr/bin/env python3
"""End-to-end implementation verification for the /double-check redesign (Slice S9).

Plan: Thoughts/double-check-validation-targeting_S9_PLAN.md (Coherent Actions A1).

This is the CLOSING acceptance slice. S1–S8 each shipped with their own unit tests;
S9 proves the clauses COMPOSE into the locked multi-clause safety contract end to end,
by driving the REAL subsystems (no mocks of the engine) and asserting the 6 acceptance
points hold together:

  Point 1 — the S4 comprehensiveness floor refuses an un-targetable target before any
            checker spawns, and accepts a concrete one.
  Point 2 — a whitelisted S5 `--auto` run is admitted (and the misses refused), and an
            `--auto`-assembled real marker serializes with `pre_check_layer` ABSENT
            (the locked Signal #1).
  Point 3 — a real engine-written marker (S1/S3) carries per-claim Supported/Not-Supported
            verdicts + named proof sources + per-checker per-angle discrepancy counts,
            and is reachable from the artifact (forward wikilink + `.dc-runs.md` sidecar).
  Point 4 — that SAME marker flows into the S8 `Stats.md` view, aggregated by
            `(caller, topic)` (composition: the point-3 marker is the point-4 input).
  Point 5 — the S2 drift guard returns a type/axis-naming divergence on a synthetic
            registry↔rules mismatch and `[]` on the real pair.
  Point 6 — the S7 caller-audit registry reaches the two-clean-scans closed state and a
            fresh caller breaks it.

The decisions-observability matrix (all 15 architecture + 4 UX decisions → evidence) is
the companion `dc_e2e_acceptance.md`. Fixture-only: tmp project trees + tmp registry/state;
the live corpus / live Stats.md / live scan-state are never touched.
"""
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng     # noqa: E402  (S1–S5 engine subsystems)
import dc_stats                     # noqa: E402  (S8 aggregator)
import dc_caller_audit              # noqa: E402  (S7 caller-audit closure)


# Real dispatch data shared by the marker-write points (mirrors the S3 contract:
# per-angle rows + per-claim verdicts with named proof sources + non-empty cost).
_DISPATCH = {
    "checker_rows": [
        {"round": 1, "model": "sonnet", "angle": "groundedness",
         "claims_examined": 5, "discrepancies": 1,
         "claim_verdicts": [
             {"claim": "X holds", "status": "Supported",
              "proof_source": "local-file:foo_RESEARCH.md:12"},
             {"claim": "Y holds", "status": "Not-Supported",
              "proof_source": "not found in named sources"},
         ]},
        {"round": 1, "model": "sonnet", "angle": "coverage",
         "claims_examined": 3, "discrepancies": 0, "claim_verdicts": []},
    ],
    "cost": {
        "upgrade": {"tokens": 100, "time": 1.5},
        "self_assessment": {"tokens": 50, "time": 0.5},
        "checkers": {"tokens": 400, "time": 3.0},
    },
    "axes_overlap_signal": "low",
    "escalate_choice": None,
}

_CONCRETE_TARGET = {
    "artifact_type": "research",
    "axes": ["groundedness", "coverage"],
    "claims": ["X holds per foo_RESEARCH.md"],
    "named_source_artifacts": ["local-file:foo_RESEARCH.md"],
}


def _marker_frontmatter(marker_path):
    text = Path(marker_path).read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


# --------------------------------------------------------------------------- #
# Point 1 — S4 comprehensiveness floor (refuse un-targetable; accept concrete).
# --------------------------------------------------------------------------- #

class TestPoint1Floor(unittest.TestCase):
    def test_floor_refuses_untargetable_before_dispatch(self):
        r = eng.validate_self_assessment(
            {"artifact_type": "not-a-type", "axes": [], "claims": [],
             "named_source_artifacts": []})
        self.assertFalse(r["ok"])
        self.assertTrue(r["failures"], "an un-targetable target must name failures")

    def test_floor_accepts_concrete_target(self):
        r = eng.validate_self_assessment(_CONCRETE_TARGET)
        self.assertTrue(r["ok"], r)


# --------------------------------------------------------------------------- #
# Point 2 — S5 `--auto` admission + Signal #1 (pre_check_layer absent).
# --------------------------------------------------------------------------- #

class TestPoint2Auto(unittest.TestCase):
    CALLER = "/e2e-trusted-caller"

    def setUp(self):
        eng.DC_AUTO_CALLER_WHITELIST.add(self.CALLER)
        self.addCleanup(eng.DC_AUTO_CALLER_WHITELIST.discard, self.CALLER)

    def _payload(self, **over):
        p = dict(_CONCRETE_TARGET)
        p.update(over)
        return p

    def test_admitted_when_trusted_flag_schema_valid_and_misses_refused(self):
        self.assertTrue(eng.validate_auto_request(self.CALLER, self._payload(), True)["ok"])
        self.assertIn("no-flag", eng.validate_auto_request(self.CALLER, self._payload(), False)["error"])
        self.assertIn("not-whitelisted", eng.validate_auto_request("/nope", self._payload(), True)["error"])
        self.assertIn("schema-invalid", eng.validate_auto_request(self.CALLER, "the code base", True)["error"])

    def test_auto_marker_serializes_without_pre_check_layer(self):
        with TemporaryDirectory() as tmp:
            proj = Path(tmp) / "proj" / "Docs"
            proj.mkdir(parents=True)
            artifact = proj / "foo_RESEARCH.md"
            artifact.write_text("# research\n", encoding="utf-8")
            marker = eng.assemble_audit_marker(
                artifact_type="research", source_path=str(artifact),
                caller=self.CALLER, topic="e2e-auto", verdict="PASS",
                rounds_this_dispatch=1, allocation_profile="sonnet,sonnet",
                round_markers=[],
                dispatch_data={**_DISPATCH, "auto_caller": self.CALLER},
                created_at="2026-06-25T12:00:00.000002+00:00")
            res = eng.write_audit_marker(marker)
            fm = _marker_frontmatter(res["marker_path"])
            self.assertNotIn("pre_check_layer", fm, "Signal #1: --auto leaves pre_check_layer ABSENT")
            self.assertEqual(fm.get("auto_caller"), self.CALLER)


# --------------------------------------------------------------------------- #
# Points 3 + 4 — auditable marker (S1/S3) FLOWS INTO the Stats.md view (S8).
# The single composition test: the point-3 marker is the literal point-4 input.
# --------------------------------------------------------------------------- #

class TestPoints3and4Compose(unittest.TestCase):
    def test_engine_marker_is_auditable_and_flows_into_stats(self):
        with TemporaryDirectory() as tmp, TemporaryDirectory() as fb:
            proj = Path(tmp) / "proj"
            docs = proj / "Docs"
            docs.mkdir(parents=True)
            artifact = docs / "foo_RESEARCH.md"
            artifact.write_text("# research\nclaim X\n", encoding="utf-8")

            # --- Point 3: write a REAL marker through the engine ---
            marker = eng.assemble_audit_marker(
                artifact_type="research", source_path=str(artifact),
                caller="/clarification", topic="e2e-topic", verdict="DIRTY",
                rounds_this_dispatch=1, allocation_profile="sonnet,sonnet",
                round_markers=[], dispatch_data=_DISPATCH,
                created_at="2026-06-25T12:00:00.000003+00:00")
            res = eng.write_audit_marker(marker)

            fm = _marker_frontmatter(res["marker_path"])
            statuses = [cv["status"] for row in fm["checker_rows"]
                        for cv in (row.get("claim_verdicts") or [])]
            self.assertIn("Supported", statuses)
            self.assertIn("Not-Supported", statuses)        # per-claim verdicts
            proofs = [cv["proof_source"] for row in fm["checker_rows"]
                      for cv in (row.get("claim_verdicts") or [])]
            self.assertTrue(any("foo_RESEARCH.md" in p for p in proofs))  # named proof source
            angles = {row["angle"]: row["discrepancies"] for row in fm["checker_rows"]}
            self.assertEqual(angles.get("groundedness"), 1)  # per-checker per-angle count
            self.assertTrue(Path(res["sidecar_path"]).exists())            # .dc-runs.md sidecar
            self.assertTrue(res["co_located"])
            self.assertTrue(res["wikilink_written"])                       # reachable from artifact
            self.assertIn("## /double-check audit", artifact.read_text(encoding="utf-8"))

            # --- Point 4: that SAME marker flows into the Stats.md (caller,topic) view ---
            dc_stats.write_dc_stats(proj, fallback_root=Path(fb))   # empty fallback → isolated
            stats = (proj / "Stats.md").read_text(encoding="utf-8")
            self.assertIn(dc_stats.BLOCK_BEGIN, stats)
            self.assertIn("/clarification", stats)
            self.assertIn("e2e-topic", stats)
            # tokens 100+50+400=550; time 1.5+0.5+3.0=5.0; claims 5+3=8; discrepancies 1
            self.assertIn("| 550 | 5.0 | 8 | 1 |", stats)


# --------------------------------------------------------------------------- #
# Point 5 — S2 drift guard (synthetic divergence flagged; real pair clean).
# --------------------------------------------------------------------------- #

class TestPoint5Drift(unittest.TestCase):
    def test_real_pair_reports_no_drift(self):
        self.assertEqual(eng.check_allocation_drift(eng.DC_ALLOCATION_RULES_PATH), [])

    def test_synthetic_divergence_is_flagged_naming_the_type(self):
        with TemporaryDirectory() as tmp:
            text = Path(eng.DC_ALLOCATION_RULES_PATH).read_text(encoding="utf-8")
            mutated = text.replace(
                "| `research` | groundedness, coverage, source-quality |\n", "")
            self.assertNotEqual(mutated, text, "fixture precondition: the research row exists")
            path = Path(tmp) / "double-check-allocation.md"
            path.write_text(mutated, encoding="utf-8")
            divergences = eng.check_allocation_drift(path)
            self.assertTrue(divergences)
            self.assertTrue(any("research" in d for d in divergences))


# --------------------------------------------------------------------------- #
# Point 6 — S7 caller-audit two-clean-scans closure (and a fresh caller breaks it).
# --------------------------------------------------------------------------- #

class TestPoint6CallerAuditClosure(unittest.TestCase):
    def test_two_clean_scans_close_and_fresh_caller_breaks(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "corpus"
            (root / "skills" / "a").mkdir(parents=True)
            (root / "rules").mkdir()
            (root / "hooks").mkdir()
            (root / "skills" / "a" / "SKILL.md").write_text(
                "uses /double-check 1,1,1", encoding="utf-8")
            (root / "hooks" / "plain.sh").write_text("no engine ref", encoding="utf-8")
            registry = Path(tmp) / "registry.yaml"
            state = Path(tmp) / "state.json"

            audit = dc_caller_audit.CallerAudit(root, registry, state)
            audit.seed()  # non-referencing → not-applicable; referencing → unclassified
            reg = yaml.safe_load(registry.read_text(encoding="utf-8"))
            for e in reg["entries"]:
                if e["path"].endswith("skills/a/SKILL.md"):
                    e["classification"] = "concrete-target"
                    e["evidence"] = "invokes /double-check with a typed target"
            registry.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")

            self.assertTrue(audit.scan().clean)
            self.assertTrue(audit.scan().closed)       # two consecutive clean scans
            self.assertTrue(audit.is_closed())

            # a fresh referencing caller appears → next scan dirty → not closed
            (root / "skills" / "b").mkdir()
            (root / "skills" / "b" / "SKILL.md").write_text(
                "invokes /double-check", encoding="utf-8")
            res = audit.scan()
            self.assertFalse(res.clean)
            self.assertFalse(audit.is_closed())


if __name__ == "__main__":
    unittest.main()

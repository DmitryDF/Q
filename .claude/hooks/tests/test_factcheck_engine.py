#!/usr/bin/env python3
"""Tests for KL fact-check checker-model provenance enforcement.

Plan: <KL>/Thoughts/kl-factcheck-model-provenance_PLAN.md
Covers the six validation-contract paths from A8:
  (i)   3× Sonnet happy path → PASS marker with provenance: code-verified
  (ii)  missing transcript → REFUSED_OVERWRITE, no marker
  (iii) Haiku in one transcript → REFUSED_OVERWRITE, no marker
  (iv)  mixed Sonnet+Haiku within one transcript → REFUSED_OVERWRITE, no marker
  (v)   missing agentId/sessionId in per-agent file → REFUSED_OVERWRITE, no marker
  (vi)  sonnet-4-7 accepted (family match, not exact)

Plus normalizer unit tests and resolver-file-IO unit tests.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402


FAKE_SESSION = "00000000-0000-0000-0000-000000000000"


def _write_assistant_turn(fh, model_id, role="assistant"):
    fh.write(json.dumps({
        "type": "assistant",
        "message": {"id": "msg_x", "type": "message", "role": role, "model": model_id},
    }) + "\n")


class NormalizeFamilyTests(unittest.TestCase):
    def test_sonnet_4_6_family(self):
        self.assertEqual(eng._normalize_model_family("claude-sonnet-4-6"), "sonnet")

    def test_sonnet_4_7_family(self):
        self.assertEqual(eng._normalize_model_family("claude-sonnet-4-7"), "sonnet")

    def test_haiku_family(self):
        self.assertEqual(eng._normalize_model_family("claude-haiku-4-5-20251001"), "haiku")

    def test_opus_family(self):
        self.assertEqual(eng._normalize_model_family("claude-opus-4-7"), "opus")

    def test_unrecognized_returns_none(self):
        self.assertIsNone(eng._normalize_model_family("gpt-4-turbo"))
        self.assertIsNone(eng._normalize_model_family(None))
        self.assertIsNone(eng._normalize_model_family(""))


class ResolveSubagentTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.projects_root = self.tmp / "projects"
        self.session_dir = self.projects_root / FAKE_SESSION / "subagents"
        self.session_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_transcript(self, agent_id, models):
        path = self.session_dir / f"agent-{agent_id}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for m in models:
                _write_assistant_turn(fh, m)
        return path

    def test_missing_file_returns_error(self):
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "absent-agent", projects_root=self.projects_root
        )
        self.assertFalse(res["ok"])
        self.assertIn("not found", res["error"])

    def test_empty_transcript_returns_error(self):
        path = self.session_dir / "agent-empty.jsonl"
        path.write_text("", encoding="utf-8")
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "empty", projects_root=self.projects_root
        )
        self.assertFalse(res["ok"])

    def test_all_sonnet_returns_sonnet(self):
        self._write_transcript("good", ["claude-sonnet-4-6", "claude-sonnet-4-6"])
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "good", projects_root=self.projects_root
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["family"], "sonnet")
        self.assertEqual(res["raw_models"], ["claude-sonnet-4-6"])

    def test_haiku_only_returns_haiku(self):
        self._write_transcript("hk", ["claude-haiku-4-5-20251001"])
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "hk", projects_root=self.projects_root
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["family"], "haiku")

    def test_mixed_families_returns_error(self):
        self._write_transcript(
            "mixed", ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"]
        )
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "mixed", projects_root=self.projects_root
        )
        self.assertFalse(res["ok"])
        self.assertIn("mixed", res["error"].lower())

    def test_unknown_model_id_returns_error(self):
        self._write_transcript("weird", ["gpt-9-turbo"])
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "weird", projects_root=self.projects_root
        )
        self.assertFalse(res["ok"])

    def test_sonnet_4_7_accepted_family_match(self):
        # Contract path (vi): family match, not exact ID — sonnet-4-7 must resolve to "sonnet".
        self._write_transcript("future", ["claude-sonnet-4-7"])
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "future", projects_root=self.projects_root
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["family"], "sonnet")
        self.assertEqual(res["raw_models"], ["claude-sonnet-4-7"])

    def test_user_role_turns_ignored(self):
        path = self.session_dir / "agent-mixed-role.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            _write_assistant_turn(fh, "claude-sonnet-4-6")
            # A user-role turn is not from the model — must be skipped.
            fh.write(json.dumps({
                "type": "user",
                "message": {"role": "user", "model": "irrelevant"},
            }) + "\n")
        res = eng._resolve_subagent_transcript_model(
            FAKE_SESSION, "mixed-role", projects_root=self.projects_root
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["family"], "sonnet")


class ScribeProvenanceContractTests(unittest.TestCase):
    """End-to-end: aggregate_kl_extraction_round with the new validation gate.

    Mirrors the six paths from the plan's "Verification" section.
    """
    PROJ = "<KL>"
    TOPIC = "fixture-book"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "plan_validation"
        self.chapters_dir = self.tmp / "Sources" / "Books" / self.TOPIC / "Chapters"
        self.chapters_dir.mkdir(parents=True)
        self._seq = 0
        # Per-test resolver behaviour map: agent_id -> resolver result dict
        self.resolver_map = {}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _agent_id(self):
        self._seq += 1
        return f"agent{self._seq:013d}"

    def _resolver(self, session_id, agent_id):
        return self.resolver_map.get(agent_id, {
            "ok": True, "family": "sonnet", "raw_models": ["claude-sonnet-4-6"],
            "error": None,
            "transcript_path": f"<fake>/{session_id}/agent-{agent_id}.jsonl",
        })

    def _write_per_agent(self, name, enum, *, agent_id=None, session_id=FAKE_SESSION,
                         include_agent_id=True, include_session_id=True):
        agent_id = agent_id or self._agent_id()
        lines = []
        if include_agent_id:
            lines.append(f"agentId: {agent_id}")
        if include_session_id:
            lines.append(f"sessionId: {session_id}")
        lines.append(f"verdict-per-checker: {enum}")
        lines.append("")  # body separator
        lines.append("PASS")
        path = self.chapters_dir / name
        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path), agent_id

    def _three_pass(self):
        files = []
        agents = []
        for letter in ("A", "B", "C"):
            fp, aid = self._write_per_agent(
                f"_factcheck-r1-foo-{letter}.md", "0_DISCREPANCIES"
            )
            files.append(fp)
            agents.append(aid)
        return files, agents

    def _marker_path(self):
        return self.state_dir / self.PROJ / self.TOPIC / "kl_extraction" / "R1.md"

    # ----- Contract path (i): 3× Sonnet happy path -----
    def test_happy_path_three_sonnet_pass(self):
        files, _ = self._three_pass()
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "PASS")
        marker = self._marker_path().read_text(encoding="utf-8")
        self.assertIn("schema_version: 3", marker)  # S2/A9 epoch bump
        self.assertIn("kind: kl_extraction", marker)
        self.assertIn("verdict: PASS", marker)
        self.assertIn("checker_models: [sonnet, sonnet, sonnet]", marker)
        self.assertIn("checker_models_raw: [claude-sonnet-4-6", marker)
        self.assertIn("provenance: code-verified", marker)
        # Per-checker body breadcrumbs
        self.assertIn("agentId: agent0", marker)
        self.assertIn("sessionId: 00000000", marker)
        self.assertIn("raw model: claude-sonnet-4-6", marker)
        self.assertIn("transcript: <fake>/", marker)

    # ----- Contract path (ii): missing transcript -----
    def test_missing_transcript_refused(self):
        files, agents = self._three_pass()
        self.resolver_map[agents[1]] = {
            "ok": False, "family": None, "raw_models": [],
            "error": "transcript not found: /fake/path",
            "transcript_path": "/fake/path",
        }
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "REFUSED_OVERWRITE")
        self.assertIn("provenance unverified", result["message"])
        self.assertFalse(self._marker_path().exists(),
                         "REFUSED_OVERWRITE must NOT write any R<N>.md file")

    # ----- Contract path (iii): Haiku in one transcript -----
    def test_haiku_in_one_transcript_refused(self):
        files, agents = self._three_pass()
        self.resolver_map[agents[2]] = {
            "ok": True, "family": "haiku",
            "raw_models": ["claude-haiku-4-5-20251001"],
            "error": None,
            "transcript_path": f"<fake>/{FAKE_SESSION}/agent-{agents[2]}.jsonl",
        }
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "REFUSED_OVERWRITE")
        self.assertIn("haiku", result["message"].lower())
        self.assertFalse(self._marker_path().exists())

    # ----- Contract path (iv): mixed Sonnet+Haiku within one transcript -----
    def test_mixed_families_in_transcript_refused(self):
        files, agents = self._three_pass()
        self.resolver_map[agents[0]] = {
            "ok": False, "family": None,
            "raw_models": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
            "error": "transcript contains mixed model families ['haiku', 'sonnet']",
            "transcript_path": f"<fake>/{FAKE_SESSION}/agent-{agents[0]}.jsonl",
        }
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "REFUSED_OVERWRITE")
        self.assertIn("mixed", result["message"].lower())
        self.assertFalse(self._marker_path().exists())

    # ----- Contract path (v): missing agentId/sessionId in per-agent file -----
    def test_missing_agent_id_refused(self):
        # Two files OK, one without agentId.
        f1, _ = self._write_per_agent("_factcheck-r1-foo-A.md", "0_DISCREPANCIES")
        f2, _ = self._write_per_agent("_factcheck-r1-foo-B.md", "0_DISCREPANCIES")
        f3, _ = self._write_per_agent(
            "_factcheck-r1-foo-C.md", "0_DISCREPANCIES", include_agent_id=False
        )
        result = eng.aggregate_kl_extraction_round(
            [f1, f2, f3], 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "REFUSED_OVERWRITE")
        self.assertIn("breadcrumbs", result["message"])
        self.assertFalse(self._marker_path().exists())

    def test_missing_session_id_refused(self):
        f1, _ = self._write_per_agent("_factcheck-r1-foo-A.md", "0_DISCREPANCIES")
        f2, _ = self._write_per_agent(
            "_factcheck-r1-foo-B.md", "0_DISCREPANCIES", include_session_id=False
        )
        f3, _ = self._write_per_agent("_factcheck-r1-foo-C.md", "0_DISCREPANCIES")
        result = eng.aggregate_kl_extraction_round(
            [f1, f2, f3], 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "REFUSED_OVERWRITE")
        self.assertIn("breadcrumbs", result["message"])
        self.assertFalse(self._marker_path().exists())

    # ----- Contract path (vi): sonnet-4-7 accepted -----
    def test_sonnet_4_7_accepted(self):
        files, agents = self._three_pass()
        for aid in agents:
            self.resolver_map[aid] = {
                "ok": True, "family": "sonnet",
                "raw_models": ["claude-sonnet-4-7"],
                "error": None,
                "transcript_path": f"<fake>/{FAKE_SESSION}/agent-{aid}.jsonl",
            }
        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=self._resolver,
        )
        self.assertEqual(result["status"], "PASS")
        marker = self._marker_path().read_text(encoding="utf-8")
        self.assertIn("checker_models_raw: [claude-sonnet-4-7, "
                      "claude-sonnet-4-7, claude-sonnet-4-7]", marker)
        self.assertIn("provenance: code-verified", marker)

    # ----- End-to-end integration with the real resolver (no mocks) -----
    def test_integration_real_resolver_reads_synthesized_transcripts(self):
        projects_root = self.tmp / "projects"
        session_dir = projects_root / FAKE_SESSION / "subagents"
        session_dir.mkdir(parents=True)

        files = []
        for letter in ("A", "B", "C"):
            fp, aid = self._write_per_agent(
                f"_factcheck-r1-int-{letter}.md", "0_DISCREPANCIES"
            )
            transcript = session_dir / f"agent-{aid}.jsonl"
            with open(transcript, "w", encoding="utf-8") as fh:
                _write_assistant_turn(fh, "claude-sonnet-4-6")
                _write_assistant_turn(fh, "claude-sonnet-4-6")
            files.append(fp)

        def real_resolver(session_id, agent_id):
            return eng._resolve_subagent_transcript_model(
                session_id, agent_id, projects_root=projects_root
            )

        result = eng.aggregate_kl_extraction_round(
            files, 1, self.PROJ, self.TOPIC, self.state_dir,
            _resolver_fn=real_resolver,
        )
        self.assertEqual(result["status"], "PASS")
        marker = self._marker_path().read_text(encoding="utf-8")
        self.assertIn("provenance: code-verified", marker)
        self.assertIn("checker_models_raw: [claude-sonnet-4-6", marker)
        self.assertIn(str(session_dir), marker)


if __name__ == "__main__":
    unittest.main(verbosity=2)

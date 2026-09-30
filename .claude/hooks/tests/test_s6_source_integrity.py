#!/usr/bin/env python3
"""Slice S6 — close-time source-integrity gate (research-fc-checker-timeout).

Plan: Thoughts/research-fc-checker-timeout-20260709205025_PLAN.md (Mode A, S6).
Spine: Thoughts/research-fc-checker-timeout_THOUGHT.md + _DESIGN.md (Alt 1, A8/A13/A18).

Architecture: S5 pre-fetches cited URLs → {status,content}. S6 turns that raw
outcome into a code-owned per-citation DECISION at close, with NO browser
(S4 NO-GO) and no silent pass:
  A1  _classify_source_integrity — {status,content} -> ok/transient/stale/hallucinated
  A2  _archive_lookup            — bounded Wayback availability check (injectable)
  A3  _repair_stale_citations    — in-place snapshot rewrite, original preserved
  A4  _run_source_integrity_gate — combine link-integrity with the content verdict
  A18 fail-safe                  — every component errors -> INCOMPLETE/None, never
                                   crash, never silent PASS.

NO live network anywhere — fetch fns are injected / HTTP is mocked.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402


# ---------------------------------------------------------------------------
# A1 — classification
# ---------------------------------------------------------------------------
class ClassifyTests(unittest.TestCase):
    def _classify_one(self, status, content):
        res = eng._classify_source_integrity(
            [{"url": "https://x.com/p", "status": status, "content": content}]
        )
        return res[0]

    def test_ok_status_is_ok(self):
        d = self._classify_one("ok", "fetched body text")
        self.assertEqual(d["disposition"], "ok")
        self.assertIsNone(d["archived_url"])

    def test_http_404_is_stale(self):
        self.assertEqual(self._classify_one("unfetchable", "HTTP 404")["disposition"], "stale")

    def test_http_410_is_stale(self):
        self.assertEqual(self._classify_one("unfetchable", "HTTP 410")["disposition"], "stale")

    def test_permanent_dns_is_stale(self):
        for reason in (
            "URL error: [Errno 8] nodename nor servname provided",
            "URL error: Name or service not known",
            "URL error: NXDOMAIN",
        ):
            self.assertEqual(
                self._classify_one("unfetchable", reason)["disposition"],
                "stale",
                f"reason {reason!r} should be stale",
            )

    def test_http_403_is_transient(self):
        self.assertEqual(self._classify_one("unfetchable", "HTTP 403")["disposition"], "transient")

    def test_http_401_and_429_are_transient(self):
        self.assertEqual(self._classify_one("unfetchable", "HTTP 401")["disposition"], "transient")
        self.assertEqual(self._classify_one("unfetchable", "HTTP 429")["disposition"], "transient")

    def test_bot_block_signal_is_transient(self):
        self.assertEqual(
            self._classify_one("unfetchable", "HTTP 200 but available not for AI")["disposition"],
            "transient",
        )

    def test_timeout_is_transient(self):
        self.assertEqual(self._classify_one("unfetchable", "timeout after 15s")["disposition"], "transient")

    def test_generic_url_error_is_transient(self):
        # A generic connection error (NOT a name-resolution failure) is transient — SAFE.
        self.assertEqual(
            self._classify_one("unfetchable", "URL error: [Errno 61] Connection refused")["disposition"],
            "transient",
        )

    def test_global_cap_is_transient(self):
        self.assertEqual(
            self._classify_one("unfetchable", "global fetch cap exceeded")["disposition"],
            "transient",
        )

    def test_unknown_exc_reason_is_transient_not_hallucinated(self):
        # SAFE default: an unknown ExcType reason must NEVER be hallucinated.
        self.assertEqual(
            self._classify_one("unfetchable", "SomeWeirdError: boom")["disposition"],
            "transient",
        )

    def test_empty_fetched_is_empty(self):
        self.assertEqual(eng._classify_source_integrity([]), [])
        self.assertEqual(eng._classify_source_integrity(None), [])

    def test_never_emits_hallucinated_directly(self):
        # A1 alone never produces `hallucinated` — that promotion is A4's job.
        many = [
            {"url": "a", "status": "unfetchable", "content": "HTTP 404"},
            {"url": "b", "status": "unfetchable", "content": "HTTP 403"},
            {"url": "c", "status": "ok", "content": "x"},
            {"url": "d", "status": "unfetchable", "content": "timeout after 15s"},
        ]
        dispos = {d["url"]: d["disposition"] for d in eng._classify_source_integrity(many)}
        self.assertNotIn("hallucinated", dispos.values())


# ---------------------------------------------------------------------------
# A2 — archive lookup
# ---------------------------------------------------------------------------
class ArchiveLookupTests(unittest.TestCase):
    def test_snapshot_present_returns_url(self):
        payload = (
            '{"archived_snapshots": {"closest": '
            '{"available": true, "url": "https://web.archive.org/web/2020/https://x.com/p"}}}'
        )
        got = eng._archive_lookup(
            "https://x.com/p", _fetch_fn=lambda q, t: payload
        )
        self.assertEqual(got, "https://web.archive.org/web/2020/https://x.com/p")

    def test_no_snapshot_returns_none(self):
        payload = '{"archived_snapshots": {}}'
        self.assertIsNone(eng._archive_lookup("https://x.com/p", _fetch_fn=lambda q, t: payload))

    def test_available_false_returns_none(self):
        payload = (
            '{"archived_snapshots": {"closest": '
            '{"available": false, "url": "https://web.archive.org/web/2020/x"}}}'
        )
        self.assertIsNone(eng._archive_lookup("https://x.com/p", _fetch_fn=lambda q, t: payload))

    def test_api_down_returns_none_no_raise(self):
        def boom(q, t):
            raise RuntimeError("wayback exploded")

        # Must NOT raise (A18) — returns None.
        self.assertIsNone(eng._archive_lookup("https://x.com/p", _fetch_fn=boom))

    def test_bad_json_returns_none_no_raise(self):
        self.assertIsNone(eng._archive_lookup("https://x.com/p", _fetch_fn=lambda q, t: "not json{"))

    def test_bytes_payload_decoded(self):
        payload = (
            b'{"archived_snapshots": {"closest": '
            b'{"available": true, "url": "https://web.archive.org/web/2019/y"}}}'
        )
        self.assertEqual(
            eng._archive_lookup("https://y.com", _fetch_fn=lambda q, t: payload),
            "https://web.archive.org/web/2019/y",
        )

    def test_empty_url_returns_none(self):
        self.assertIsNone(eng._archive_lookup(""))

    def test_query_url_is_wayback_availability_api(self):
        seen = {}

        def capture(q, t):
            seen["q"] = q
            return '{"archived_snapshots": {}}'

        eng._archive_lookup("https://x.com/p?a=1", _fetch_fn=capture)
        self.assertIn("archive.org/wayback/available?url=", seen["q"])
        # The target URL is percent-encoded into the query.
        self.assertIn("https%3A%2F%2Fx.com%2Fp", seen["q"])


# ---------------------------------------------------------------------------
# A3 — in-place stale repair
# ---------------------------------------------------------------------------
class RepairTests(unittest.TestCase):
    def test_stale_with_archive_is_rewritten_preserving_original(self):
        text = "See [stated — https://dead.com/x] for detail."
        dispos = [{
            "url": "https://dead.com/x",
            "disposition": "stale",
            "archived_url": "https://web.archive.org/web/2020/https://dead.com/x",
            "reason": "HTTP 404",
        }]
        new_text, repaired = eng._repair_stale_citations(text, dispos)
        self.assertIn("https://web.archive.org/web/2020/https://dead.com/x (orig: https://dead.com/x)", new_text)
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]["url"], "https://dead.com/x")

    def test_only_stale_urls_touched(self):
        text = "live https://ok.com/a and dead https://dead.com/b end"
        dispos = [
            {"url": "https://ok.com/a", "disposition": "ok", "archived_url": None},
            {"url": "https://dead.com/b", "disposition": "stale",
             "archived_url": "https://web.archive.org/snap/b"},
        ]
        new_text, repaired = eng._repair_stale_citations(text, dispos)
        self.assertIn("https://ok.com/a and", new_text)  # untouched
        self.assertNotIn("https://ok.com/a (orig:", new_text)
        self.assertIn("https://web.archive.org/snap/b (orig: https://dead.com/b)", new_text)

    def test_transient_and_hallucinated_not_repaired(self):
        text = "blocked https://blk.com/x fabricated https://fake.com/y"
        dispos = [
            {"url": "https://blk.com/x", "disposition": "transient", "archived_url": None},
            {"url": "https://fake.com/y", "disposition": "hallucinated", "archived_url": None},
        ]
        new_text, repaired = eng._repair_stale_citations(text, dispos)
        self.assertEqual(new_text, text)
        self.assertEqual(repaired, [])

    def test_stale_without_archive_not_repaired(self):
        text = "dead https://dead.com/z"
        dispos = [{"url": "https://dead.com/z", "disposition": "stale", "archived_url": None}]
        new_text, repaired = eng._repair_stale_citations(text, dispos)
        self.assertEqual(new_text, text)
        self.assertEqual(repaired, [])

    def test_idempotent(self):
        text = "See https://dead.com/x here."
        dispos = [{
            "url": "https://dead.com/x",
            "disposition": "stale",
            "archived_url": "https://web.archive.org/snap/x",
        }]
        once, r1 = eng._repair_stale_citations(text, dispos)
        twice, r2 = eng._repair_stale_citations(once, dispos)
        self.assertEqual(once, twice, "re-running must not double-wrap")
        self.assertEqual(r2, [], "second run repairs nothing")

    def test_empty_inputs_noop(self):
        self.assertEqual(eng._repair_stale_citations("", []), ("", []))
        self.assertEqual(eng._repair_stale_citations("text", []), ("text", []))
        self.assertEqual(eng._repair_stale_citations(None, [{"disposition": "stale"}]), (None, []))

    def test_atomic_writeback_replaces_file(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            f = tmp / "r_RESEARCH.md"
            f.write_text("dead https://dead.com/x tail", encoding="utf-8")
            dispos = [{
                "url": "https://dead.com/x",
                "disposition": "stale",
                "archived_url": "https://web.archive.org/snap/x",
            }]
            new_text, repaired = eng._repair_stale_citations(
                f.read_text(encoding="utf-8"), dispos
            )
            eng._atomic_write_text(f, new_text)
            on_disk = f.read_text(encoding="utf-8")
            self.assertIn("https://web.archive.org/snap/x (orig: https://dead.com/x)", on_disk)
            # No leftover temp files in the dir.
            self.assertEqual(list(tmp.glob("*.tmp")), [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# A4 — combination truth table
# ---------------------------------------------------------------------------
def _latest_marker_verdict(topic_dir):
    markers = sorted(Path(topic_dir).glob("R*.md"))
    if not markers:
        return None
    text = markers[-1].read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("verdict:"):
            return line.split(":", 1)[1].strip()
    return None


class GateCombinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.topic = self.tmp / "topic"
        self.topic.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_draft(self, text):
        self.draft.write_text(text, encoding="utf-8")

    def test_pass_plus_block_becomes_escalate(self):
        # A 404 with NO archive → hallucinated → BLOCK → ESCALATE (terminal, and
        # not OK-able: there is no accept path for a hard block).
        self._write_draft("cite https://fake.com/x")
        fetched = [{"url": "https://fake.com/x", "status": "unfetchable", "content": "HTTP 404"}]
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research",
            _archive_fn=lambda u: None,  # no snapshot -> promote to hallucinated
        )
        self.assertEqual(out["status"], "ESCALATE")
        self.assertEqual(_latest_marker_verdict(self.topic), "ESCALATE")

    def test_pass_plus_incomplete_becomes_unaccepted_incomplete(self):
        # A bot-blocked (transient) source → INCOMPLETE, un-accepted (no accept_reason).
        self._write_draft("cite https://blk.com/x")
        fetched = [{"url": "https://blk.com/x", "status": "unfetchable", "content": "HTTP 403"}]
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research",
            _archive_fn=lambda u: None,
        )
        self.assertEqual(out["status"], "INCOMPLETE")
        marker = sorted(self.topic.glob("R*.md"))[-1].read_text(encoding="utf-8")
        self.assertRegex(marker, r"(?m)^verdict:\s*INCOMPLETE\s*$")
        # Un-accepted: no accept_reason line → the close gate still blocks.
        self.assertNotIn("accept_reason:", marker)

    def test_pass_plus_ok_stays_pass_no_marker(self):
        self._write_draft("cite https://ok.com/x")
        fetched = [{"url": "https://ok.com/x", "status": "ok", "content": "body"}]
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research"
        )
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(list(self.topic.glob("R*.md")), [], "OK case must write no marker")

    def test_stale_with_archive_repairs_and_stays_pass(self):
        self._write_draft("cite https://dead.com/x tail")
        fetched = [{"url": "https://dead.com/x", "status": "unfetchable", "content": "HTTP 404"}]
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research",
            _archive_fn=lambda u: "https://web.archive.org/snap/x",
        )
        # A repairable stale (archive found) → repaired in-place, link_integrity OK → PASS.
        self.assertEqual(out["status"], "PASS")
        on_disk = self.draft.read_text(encoding="utf-8")
        self.assertIn("https://web.archive.org/snap/x (orig: https://dead.com/x)", on_disk)
        self.assertEqual(list(self.topic.glob("R*.md")), [])

    def test_content_non_pass_preserved(self):
        # A content DIRTY/ESCALATE/INCOMPLETE dominates — gate does not weaken it,
        # and writes no new marker.
        self._write_draft("cite https://fake.com/x")
        fetched = [{"url": "https://fake.com/x", "status": "unfetchable", "content": "HTTP 404"}]
        for content_status in ("ESCALATE", "INCOMPLETE", "DIRTY", "DISCREPANCY"):
            with self.subTest(content=content_status):
                verdict = {"status": content_status, "rounds": 2}
                out = eng._run_source_integrity_gate(
                    fetched, str(self.draft), verdict, self.topic, "research",
                    _archive_fn=lambda u: None,
                )
                self.assertEqual(out["status"], content_status)

    def test_no_url_report_noop(self):
        self._write_draft("no citations here")
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate([], str(self.draft), verdict, self.topic, "research")
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(list(self.topic.glob("R*.md")), [])

    def test_all_ok_report_noop(self):
        self._write_draft("cite https://a.com and https://b.com")
        fetched = [
            {"url": "https://a.com", "status": "ok", "content": "x"},
            {"url": "https://b.com", "status": "ok", "content": "y"},
        ]
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(fetched, str(self.draft), verdict, self.topic, "research")
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(list(self.topic.glob("R*.md")), [])

    def test_non_research_kind_untouched(self):
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(
            [{"url": "x", "status": "unfetchable", "content": "HTTP 404"}],
            str(self.draft), verdict, self.topic, "plan",
        )
        self.assertEqual(out["status"], "PASS")

    def test_mixed_block_wins_over_transient(self):
        # BLOCK precedence: any hallucinated → ESCALATE even with a co-occurring
        # transient (a hard block dominates an operator-acceptable INCOMPLETE).
        self._write_draft("a https://fake.com/x b https://blk.com/y")
        fetched = [
            {"url": "https://fake.com/x", "status": "unfetchable", "content": "HTTP 404"},
            {"url": "https://blk.com/y", "status": "unfetchable", "content": "HTTP 403"},
        ]
        verdict = {"status": "PASS", "rounds": 1}
        out = eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research",
            _archive_fn=lambda u: None,
        )
        self.assertEqual(out["status"], "ESCALATE")


# ---------------------------------------------------------------------------
# A18 — fail-safe (never crash, never silent PASS)
# ---------------------------------------------------------------------------
class FailSafeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.topic = self.tmp / "topic"
        self.topic.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"
        self.draft.write_text("cite https://dead.com/x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_archive_fn_raising_does_not_crash_and_blocks(self):
        # A stale 404 whose archive lookup RAISES → treated as no-snapshot →
        # hallucinated → ESCALATE. Never crashes.
        fetched = [{"url": "https://dead.com/x", "status": "unfetchable", "content": "HTTP 404"}]
        verdict = {"status": "PASS", "rounds": 1}

        def boom(u):
            raise RuntimeError("archive down")

        out = eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research", _archive_fn=boom
        )
        self.assertEqual(out["status"], "ESCALATE")

    def test_whole_gate_error_degrades_to_incomplete_not_pass(self):
        # Force a failure deep inside the gate (classification raises) → the
        # last-resort wrapper degrades to INCOMPLETE, never a silent PASS.
        fetched = [{"url": "https://dead.com/x", "status": "unfetchable", "content": "HTTP 404"}]
        verdict = {"status": "PASS", "rounds": 1}
        with mock.patch.object(eng, "_classify_source_integrity", side_effect=RuntimeError("boom")):
            out = eng._run_source_integrity_gate(
                fetched, str(self.draft), verdict, self.topic, "research"
            )
        self.assertEqual(out["status"], "INCOMPLETE")
        self.assertNotEqual(out["status"], "PASS")

    def test_double_failure_still_incomplete_never_silent_pass(self):
        # C6 absolute: even if the gate body raises AND the last-resort marker
        # write ALSO raises, a content PASS must NOT survive as a silent pass —
        # the in-memory verdict is set to INCOMPLETE before the marker attempt.
        fetched = [{"url": "https://dead.com/x", "status": "unfetchable", "content": "HTTP 404"}]
        verdict = {"status": "PASS", "rounds": 1}
        with mock.patch.object(eng, "_classify_source_integrity", side_effect=RuntimeError("boom")), \
                mock.patch.object(eng, "_write_source_integrity_marker",
                                  side_effect=OSError("disk full")):
            out = eng._run_source_integrity_gate(
                fetched, str(self.draft), verdict, self.topic, "research"
            )
        self.assertEqual(out["status"], "INCOMPLETE")
        self.assertNotEqual(out["status"], "PASS")

    def test_gate_never_upgrades_a_failed_content_verdict(self):
        # Even if the gate itself errors, a content non-PASS is preserved.
        verdict = {"status": "ESCALATE", "rounds": 2}
        with mock.patch.object(eng, "_classify_source_integrity", side_effect=RuntimeError("boom")):
            out = eng._run_source_integrity_gate(
                [{"url": "x", "status": "unfetchable", "content": "HTTP 404"}],
                str(self.draft), verdict, self.topic, "research",
            )
        self.assertEqual(out["status"], "ESCALATE")


# ---------------------------------------------------------------------------
# A4 wiring end-to-end through factcheck_run (mocked fetch, NO live network)
# ---------------------------------------------------------------------------
class GateWiredIntoFactcheckRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_content_pass_with_fabricated_link_downgrades_to_escalate(self):
        self.draft.write_text(
            "Claim [stated — https://fabricated.example/x].\n", encoding="utf-8"
        )

        def fake_fetch(url, timeout_s, max_bytes):
            return ("unfetchable", "HTTP 404")

        def passing_checker(dp, idx, model, rnd, prior):
            return "reasoning\nVERDICT: PASS"

        with mock.patch.object(eng, "_fetch_one_url", side_effect=fake_fetch), \
                mock.patch.object(eng, "_archive_lookup", side_effect=lambda u: None), \
                mock.patch.object(eng, "_run_coverage_axis_gate",
                                  side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict), \
                mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            result = eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft),
                kind="research",
                session_id="66666666-6666-6666-6666-666666666666",
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=passing_checker,
                proj="p",
                topic="t",
            )
        # Content PASS + fabricated link (404, no archive) → combined ESCALATE.
        self.assertEqual(result["status"], "ESCALATE")

    def test_content_pass_all_links_ok_stays_pass(self):
        self.draft.write_text(
            "Claim [stated — https://ok.example/a].\n", encoding="utf-8"
        )

        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", "the supporting content")

        def passing_checker(dp, idx, model, rnd, prior):
            return "reasoning\nVERDICT: PASS"

        with mock.patch.object(eng, "_fetch_one_url", side_effect=fake_fetch), \
                mock.patch.object(eng, "_run_coverage_axis_gate",
                                  side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict), \
                mock.patch.object(eng, "_write_research_frontmatter_for_terminal"), \
                mock.patch.object(eng, "_log_factcheck_run"):
            result = eng.factcheck_run(
                state_dir=str(self.state_dir),
                draft_path=str(self.draft),
                kind="research",
                session_id="77777777-7777-7777-7777-777777777777",
                debounce_seconds=0,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=2,
                _checker_fn=passing_checker,
                proj="p",
                topic="t",
            )
        self.assertEqual(result["status"], "PASS")


# ---------------------------------------------------------------------------
# A6 — retirement of research-chromium-fetch
# ---------------------------------------------------------------------------
class RetirementTests(unittest.TestCase):
    SKILL_DIR = Path(__file__).resolve().parents[2] / "skills" / "research-chromium-fetch"

    def test_entry_guard_refuses_with_retired_message(self):
        import subprocess
        guard = self.SKILL_DIR / "entry-guard.sh"
        self.assertTrue(guard.exists())
        res = subprocess.run(
            ["bash", str(guard)],
            input="", capture_output=True, text=True,
            env={"SESSION_ID": "any", "PATH": "/usr/bin:/bin"},
        )
        self.assertNotEqual(res.returncode, 0, "retired guard must exit non-zero")
        self.assertIn("RETIRED", res.stderr)

    def test_skill_md_has_retired_banner(self):
        text = (self.SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("RETIRED", text)
        self.assertIn("_run_source_integrity_gate", text)


class CitationReconcileGateTests(unittest.TestCase):
    """Slice C — the gate downgrades unreachable-source citation labels
    (defense-in-depth) BEFORE _repair_stale_citations, sharing _citation_reconcile."""
    DOWN = "[unverified — source unreachable at fetch — "

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.topic = self.tmp / "topic"
        self.topic.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, body, fetched, archive=None):
        self.draft.write_text(body, encoding="utf-8")
        eng._run_source_integrity_gate(
            fetched, str(self.draft), {"status": "PASS", "rounds": 1},
            self.topic, "research", _archive_fn=(archive or (lambda u: None)),
        )
        return self.draft.read_text(encoding="utf-8")

    def test_transient_citation_downgraded(self):
        body = self._run(
            "Quote [stated — https://blk.com/x] here.",
            [{"url": "https://blk.com/x", "status": "unfetchable", "content": "HTTP 403"}],
        )
        self.assertIn(self.DOWN + "https://blk.com/x]", body)
        self.assertNotIn("[stated —", body)

    def test_ok_citation_kept(self):
        body = self._run(
            "Quote [stated — https://ok.com/x] here.",
            [{"url": "https://ok.com/x", "status": "ok", "content": "body"}],
        )
        self.assertIn("[stated — https://ok.com/x]", body)
        self.assertNotIn(self.DOWN, body)

    def test_downgrade_precedes_repair_no_malformed_marker(self):
        # stale+archive: downgrade first (marker → unverified), then repair swaps
        # the URL inside the now-unverified marker → coherent, never a [stated — snap].
        body = self._run(
            "Quote [stated — https://dead.com/x] tail.",
            [{"url": "https://dead.com/x", "status": "unfetchable", "content": "HTTP 404"}],
            archive=lambda u: "https://web.archive.org/snap/x",
        )
        self.assertNotIn("[stated —", body)
        self.assertIn(self.DOWN, body)

    def test_idempotent_when_already_unverified(self):
        body = self._run(
            "Quote [unverified — source unreachable at fetch — https://blk.com/x] here.",
            [{"url": "https://blk.com/x", "status": "unfetchable", "content": "HTTP 403"}],
        )
        self.assertEqual(body.count(self.DOWN), 1)


# ---------------------------------------------------------------------------
# E2c — the terminal token: a source-integrity BLOCK is ESCALATE, not DIRTY.
#
# DIRTY is this system's documented word for "the checkers disagreed; the engine
# will run another round" (factcheck-convergence.md §4). This gate runs AFTER the
# rounds have returned and then hands control back — it cannot advance the run,
# so the honest token is ESCALATE ("no consensus, human review required"), which
# `aggregate_round_verdict` already computes correctly elsewhere.
# ---------------------------------------------------------------------------
class TerminalVerdictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.topic = self.tmp / "topic"
        self.topic.mkdir()
        self.draft = self.tmp / "r_RESEARCH.md"
        self.draft.write_text("cite https://fake.com/x", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _block(self):
        fetched = [{"url": "https://fake.com/x", "status": "unfetchable",
                    "content": "HTTP 404"}]
        verdict = {"status": "PASS", "rounds": 1}
        return eng._run_source_integrity_gate(
            fetched, str(self.draft), verdict, self.topic, "research",
            _archive_fn=lambda u: None,
        )

    def test_block_status_is_escalate_not_dirty(self):
        out = self._block()
        self.assertEqual(out["status"], "ESCALATE",
                         "a terminal gate must not report the retry token DIRTY")
        self.assertNotEqual(out["status"], "DIRTY")

    def test_block_marker_verdict_is_escalate_not_dirty(self):
        self._block()
        # The marker is what the close gate — and the operator — actually read.
        self.assertEqual(_latest_marker_verdict(self.topic), "ESCALATE")

    def test_block_sets_a_reason_naming_the_unverifiable_url(self):
        out = self._block()
        reason = out.get("reason") or ""
        self.assertTrue(reason.strip(), "a gate rejection must carry a reason")
        self.assertIn("https://fake.com/x", reason)

    def test_reason_is_never_empty_even_with_no_urls_to_name(self):
        # Defensive: the reason builder must still name the axis when the driving
        # list is empty, so the audit row can never be blank.
        self.assertTrue(eng._source_integrity_reason([]).strip())


if __name__ == "__main__":
    unittest.main()

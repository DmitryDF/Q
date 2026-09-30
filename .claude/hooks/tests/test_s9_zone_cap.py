#!/usr/bin/env python3
"""Slice S9 + A3-cap-recalibration — aggregate quarantine-zone cap
(research-fc-checker-timeout).

Plan: Thoughts/research-fc-checker-timeout-20260714145750_PLAN.md (Mode A, S9).
Spine: Thoughts/research-fc-checker-timeout_THOUGHT.md (## Scope item (8)).

Regression this guards: the UNBOUNDED `_build_quarantine_zone` + the ambient `claude --print`
overhead (measured ~91K pre-A3; ~6.6K now the checker runs `--safe-mode --tools`) pushed the
checker prompt past the 200K context window of the Sonnet/Haiku panel members, so those
checkers exited rc=1 ("Prompt is too long") and never ran — the report could never earn a
genuine verdict. It escaped S1–S8 because every research-path test faked the `_checker_fn`
seam, so the REAL assembled-prompt size was never asserted (S5's one live check used Opus,
whose larger window hid it).

2026-07-15 A3-cap recalibration (this file's second epoch): the shipped 512 KB cap bounded
OK-*content* bytes only and used an optimistic 3.85 B/tok. A live acceptance run (real
54-URL / 50 KB _RESEARCH.md) proved that even at 512 KB the assembled zone reached 531 KB
(7,245 B of uncounted per-source framing) and the ~550 KB prompt was REJECTED as ">200K
tokens" on Sonnet 4.6 / Haiku 4.5 (Opus 4.7's larger window masked it). The measured real
ratio was <= 2.84 B/tok. Fix: (a) the cap now bounds the FRAMING-INCLUSIVE assembled zone
(tags + STATUS + wrapper + content), (b) `_ZONE_BYTES_PER_TOKEN` lowered to a conservative
2.6, (c) `_ZONE_MAX_TOTAL_BYTES` DERIVED from the budget-model constants, (d) a source-count
backstop caps pathologically URL-dense reports so framing alone can't overflow.

Covers:
  A1  the FRAMING-INCLUSIVE guarantee — `len(zone) <= _ZONE_MAX_TOTAL_BYTES` for ANY
      fetched set (realistic, tiny, pathological source counts, long URLs, all-unfetchable).
      This is the invariant whose content-only predecessor let the regression ship.
  A2  fair-share still represents every OK source when not trimmed; degenerate + unfetchable
      + back-compat (max_total_bytes=None) unchanged.
  A3  source-count backstop — a report too URL-dense for framing alone keeps the leading
      sources + one omission sentinel, still <= cap, never a silent drop.
  A4  budget-model self-consistency (cap is derived) + the REAL assembled prompt fits the
      panel window under the recalibrated constants.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _factcheck_engine as eng  # noqa: E402


def _ok(url, content):
    return {"url": url, "status": "ok", "content": content}


def _unfetchable(url, reason):
    return {"url": url, "status": "unfetchable", "content": reason}


def _zbytes(zone):
    return len(zone.encode("utf-8"))


class FramingInclusiveGuaranteeTests(unittest.TestCase):
    """A1 — the assembled zone (framing INCLUDED) never exceeds the cap, for any set."""

    CAP = eng._ZONE_MAX_TOTAL_BYTES

    def _assert_bounded(self, fetched, label):
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=self.CAP)
        self.assertLessEqual(
            _zbytes(zone), self.CAP,
            f"{label}: assembled zone {_zbytes(zone)} B exceeds cap {self.CAP} B "
            f"(framing-inclusive guarantee violated)")
        return zone

    def _mk(self, n, content_len, urllen=60):
        base = "https://example.com/" + "p" * urllen + "/src-"
        return [_ok(f"{base}{i}?q={i}", "A" * content_len) for i in range(n)]

    def test_realistic_54_urls_bounded(self):
        # the live-acceptance shape: 54 OK x 100KB (>> per_source) + 4 unfetchable 403s.
        fetched = self._mk(54, 100_000) + [_unfetchable(f"https://b.org/{j}", "HTTP 403")
                                           for j in range(4)]
        self._assert_bounded(fetched, "54 OK x100KB + 4 unfetch")

    def test_500_urls_bounded(self):
        self._assert_bounded(self._mk(500, 50_000), "500 OK x50KB")

    def test_500_tiny_urls_bounded(self):
        self._assert_bounded(self._mk(500, 100), "500 OK x100B")

    def test_pathological_5000_urls_bounded(self):
        self._assert_bounded(self._mk(5000, 500), "5000 OK x500B")

    def test_pathological_20000_urls_bounded(self):
        self._assert_bounded(self._mk(20000, 50), "20000 OK x50B")

    def test_long_urls_bounded(self):
        self._assert_bounded(self._mk(10, 80_000, urllen=1500), "10 OK long-URL x80KB")

    def test_single_huge_source_bounded(self):
        self._assert_bounded(self._mk(1, 2_000_000), "1 OK x2MB")

    def test_all_unfetchable_bounded(self):
        fetched = [_unfetchable(f"https://x/{i}", "boom " * 20) for i in range(50)]
        self._assert_bounded(fetched, "50 unfetchable")

    def test_multibyte_content_bounded(self):
        # per_source is a BYTE budget; truncation must slice BYTES not CHARACTERS, else
        # multi-byte UTF-8 content overshoots (caught 2026-07-15 on a real report: the
        # char-slice assembled a zone ~9 KB over the cap while the ASCII tests passed).
        mb = "Résumé — “quoted” 例文 🔬 café naïve — " * 400  # em-dash/curly/CJK/emoji/accents
        fetched = [_ok(f"https://example.com/src-{i}", mb * 8) for i in range(54)]
        zone = self._assert_bounded(fetched, "54 heavy-multibyte")
        zone.encode("utf-8")  # truncated slices must re-encode cleanly (no partial char)
        self.assertIn("truncated to zone budget", zone)  # content did exceed per_source

    def test_mixed_ascii_and_multibyte_bounded(self):
        mb = "Größenwahn — “π≈3.14” 日本語 café " * 300
        fetched = [_ok(f"https://s{i}", ("A" * 50_000 if i % 2 else mb * 10))
                   for i in range(60)]
        self._assert_bounded(fetched, "60 mixed ascii/multibyte")


class FairShareAndBackCompatTests(unittest.TestCase):
    """A2 — allocation semantics preserved for the common (non-trimmed) case."""

    def test_unbounded_when_no_budget(self):
        fetched = [_ok("https://a", "X" * 5000), _ok("https://b", "Y" * 5000)]
        zone = eng._build_quarantine_zone(fetched)  # max_total_bytes=None (legacy)
        self.assertIn("X" * 5000, zone)
        self.assertIn("Y" * 5000, zone)

    def test_empty_on_no_sources(self):
        self.assertEqual(eng._build_quarantine_zone([], max_total_bytes=1024), "")

    def test_fair_share_all_represented_and_bounded(self):
        # 4 OK sources, each 100KB raw; every source represented (truncated), none dropped,
        # and the ASSEMBLED zone (framing included) stays within budget.
        fetched = [_ok(f"https://s{i}", "Z" * (100 * 1024)) for i in range(4)]
        budget = 40 * 1024
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=budget)
        self.assertEqual(zone.count('<source index='), 4)
        self.assertEqual(zone.count('STATUS: ok (truncated to zone budget)'), 4)
        self.assertLessEqual(_zbytes(zone), budget)  # framing-inclusive
        # content is fair-shared: each of 4 sources carries a roughly-equal, non-trivial slice
        z_bytes = zone.count("Z")
        self.assertGreater(z_bytes, budget // 2)  # most of the budget goes to content

    def test_small_sources_not_truncated(self):
        fetched = [_ok("https://s", "hi"), _ok("https://t", "yo")]
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=1024)
        self.assertIn("STATUS: ok\nhi", zone)
        self.assertNotIn("truncated", zone)

    def test_unfetchable_listed_and_budget_free(self):
        fetched = [_unfetchable("https://dead", "404"), _ok("https://ok", "A" * 4096)]
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=2048)
        self.assertIn("STATUS: UNFETCHABLE:404", zone)
        self.assertIn("A" * 512, zone)  # the OK source got the (framing-adjusted) budget
        self.assertLessEqual(_zbytes(zone), 2048)

    def test_degenerate_more_sources_than_budget(self):
        # budget with room for FRAMING but < 1 byte/source of content -> per_source 0 ->
        # every OK source marked budget-exceeded (framing fits, so no source-count trim).
        fetched = [_ok(f"https://s{i}", "content" * 100) for i in range(10)]
        framing = eng._zone_framing_bytes(fetched)
        budget = framing + 5
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=budget)
        self.assertEqual(zone.count("STATUS: UNFETCHABLE:zone-budget-exceeded"), 10)
        self.assertLessEqual(_zbytes(zone), budget)  # framing-inclusive, still bounded


class SourceCountBackstopTests(unittest.TestCase):
    """A3 — a report too URL-dense for framing alone is trimmed with a visible sentinel."""

    def test_no_op_when_framing_fits(self):
        fetched = [_ok(f"https://s{i}", "A" * 1000) for i in range(54)]
        self.assertEqual(
            eng._cap_source_count(fetched, eng._ZONE_MAX_TOTAL_BYTES), fetched,
            "a realistic set must NOT be trimmed (backstop is a no-op)")

    def test_trims_with_sentinel_and_stays_bounded(self):
        fetched = [_ok(f"https://s{i}", "A" * 50) for i in range(20000)]
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES)
        self.assertIn("zone-source-count-exceeded", zone)  # honest omission, not silent
        self.assertLessEqual(_zbytes(zone), eng._ZONE_MAX_TOTAL_BYTES)


class BudgetModelTests(unittest.TestCase):
    """A4 — the cap is DERIVED from the constants, and the real prompt fits the window."""

    def test_cap_is_derived_from_constants(self):
        expected = int(
            (eng._CHECKER_WINDOW_TOKENS
             - eng._CHECKER_AMBIENT_OVERHEAD_TOKENS
             - eng._CHECKER_RESERVED_TOKENS) * eng._ZONE_BYTES_PER_TOKEN)
        self.assertEqual(eng._ZONE_MAX_TOTAL_BYTES, expected)

    def test_bytes_per_token_is_conservative(self):
        # the 2026-07-15 acceptance measured real ratio <= 2.84 B/tok on dense source text;
        # the constant must stay at or below that measured bound (never regress to 3.85).
        self.assertLessEqual(eng._ZONE_BYTES_PER_TOKEN, 2.84)

    def test_budget_model_fits_window_in_tokens(self):
        # at the cap, the zone consumes exactly its usable-token share; adding the ambient
        # overhead and the reserved (scaffolding + mid-run read + response) must not exceed
        # the panel window. This is the token-budget model the byte cap encodes.
        zone_tokens_at_cap = eng._ZONE_MAX_TOTAL_BYTES / eng._ZONE_BYTES_PER_TOKEN
        total = (zone_tokens_at_cap
                 + eng._CHECKER_AMBIENT_OVERHEAD_TOKENS
                 + eng._CHECKER_RESERVED_TOKENS)
        self.assertLessEqual(total, eng._CHECKER_WINDOW_TOKENS)

    def test_real_prompt_zone_fits_window(self):
        # the REAL assembled prompt (no faked seam) on a URL-heavy fetched-set: its zone
        # portion is cap-bounded, and the estimated zone tokens + ambient + reserved fit the
        # window. (Scaffolding/grounding are covered by RESERVED, not double-counted here.)
        fetched = [_ok(f"https://src{i}.example/page", "H" * (512 * 1024)) for i in range(60)]
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES)
        prompt = eng._build_checker_input(
            "research", 0, "/tmp/does-not-matter_RESEARCH.md", 1, None,
            grounding_rules="(grounding omitted for the test)",
            scope_addendum=None, quarantined_sources=zone)
        self.assertLessEqual(_zbytes(zone), eng._ZONE_MAX_TOTAL_BYTES)
        self.assertIn(zone, prompt)  # the bounded zone is what rides in the prompt
        est_zone_tokens = _zbytes(zone) / eng._ZONE_BYTES_PER_TOKEN
        total = (est_zone_tokens
                 + eng._CHECKER_AMBIENT_OVERHEAD_TOKENS
                 + eng._CHECKER_RESERVED_TOKENS)
        self.assertLessEqual(total, eng._CHECKER_WINDOW_TOKENS)

    def test_unbounded_prompt_would_overflow(self):
        # sanity: the cap is load-bearing — without it the same fetched-set is multi-MB.
        fetched = [_ok(f"https://src{i}.example/page", "H" * (512 * 1024)) for i in range(60)]
        zone_unbounded = eng._build_quarantine_zone(fetched)  # no cap
        self.assertGreater(len(zone_unbounded.encode("utf-8")), 5 * 1024 * 1024)


import hashlib


def _dense_content(n_lines):
    """git-log / hash / yaml-value dense text (~1.9 B/tok in cl100k — matching the
    reproduced real overflow: 354,810 B -> 187,768 real tokens = 1.89 B/tok). This is the
    exact content class the static bytes÷2.5 proxy under-counts."""
    return "\n".join(
        f"{hashlib.sha1(str(i).encode()).hexdigest()} "
        f"key_{i}: {hashlib.sha256(str(i).encode()).hexdigest()}"
        for i in range(n_lines))


class RealTokenBudgetTests(unittest.TestCase):
    """Group III / E2a (A1/A2, AD1/AD5/AD7) — bound the research zone by REAL TOKENS.

    A byte-LEGAL dense zone (<= _ZONE_MAX_TOTAL_BYTES) decodes to MORE than the token
    budget under the byte-only cap (the bug). Passing `max_total_tokens` engages the A2
    two-stage trim, which brings the assembled zone within the real-token budget.
    """

    def tearDown(self):
        eng._token_oracle = None  # never let a fixture oracle leak to other tests

    _EFFECTIVE = int(eng._ZONE_MAX_TOTAL_TOKENS * 0.90)  # budget after the A2 safety margin

    def test_real_tokenizer_sees_the_density_gap_bytes_proxy_hides(self):
        # The circular guard (est = bytes / 2.5) cannot distinguish prose from dense code:
        # a real tokenizer must. Prove the two disagree on dense content.
        oracle = eng.LocalTokenizerAdapter()
        dense = _dense_content(3000)
        real = oracle.count(dense)
        proxy = len(dense.encode("utf-8")) / eng._ZONE_BYTES_PER_TOKEN
        self.assertGreater(
            real, proxy,
            "real tokenizer must count dense content HIGHER than the bytes/2.5 proxy "
            f"(real={real}, proxy={proxy:.0f}) — else the overflow is unreachable")

    def test_byte_only_path_overflows_token_budget(self):
        # PERMANENT bug guard: the byte-only cap (max_total_tokens=None) lets a byte-legal
        # dense zone overflow the real-token budget. This is WHY the token gate is load-bearing.
        oracle = eng.LocalTokenizerAdapter()
        fetched = [_ok("https://dense.example/git-log", _dense_content(8000))]
        zone = eng._build_quarantine_zone(fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES)
        self.assertLessEqual(_zbytes(zone), eng._ZONE_MAX_TOTAL_BYTES)  # byte-legal
        self.assertGreater(
            oracle.count(zone), eng._ZONE_MAX_TOTAL_TOKENS,
            "byte-only cap must let dense content overflow the token budget (the E2a bug)")

    def test_token_bounded_zone_fits_real_token_budget(self):
        # A1's RED test, turned GREEN by A2: the token-bounded research path keeps the
        # assembled zone within the real-token budget on the exact dense input that crashed.
        oracle = eng.LocalTokenizerAdapter()
        fetched = [_ok("https://dense.example/git-log", _dense_content(8000))]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        self.assertLessEqual(_zbytes(zone), eng._ZONE_MAX_TOTAL_BYTES)   # still byte-legal
        real_tokens = oracle.count(zone)
        self.assertLessEqual(
            real_tokens, eng._ZONE_MAX_TOTAL_TOKENS,
            f"A2 must bound the dense zone by tokens: {real_tokens} > "
            f"{eng._ZONE_MAX_TOTAL_TOKENS}")
        self.assertGreater(len(zone), 1000, "the bounded zone must still carry real content")

    def test_fixture_analytical_branch_uniform_density(self):
        # Uniform density → the single analytical slice lands under budget (no floor needed).
        eng._token_oracle = eng.FixtureTokenAdapter(lambda t: 1.8)  # uniform 1.8 B/tok
        fetched = [_ok("https://u.example/x", _dense_content(8000))]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        self.assertLessEqual(eng._token_oracle.count(zone), self._EFFECTIVE)

    def test_fixture_floor_branch_front_loaded_density(self):
        # Front-loaded density (the retained prefix is DENSER than the whole-zone average) →
        # the average-ratio analytical slice UNDER-trims, so the byte-safe floor must engage.
        big = eng._ZONE_MAX_TOTAL_BYTES // 2
        # Looks sparse (2.5 B/tok) when whole; dense (1.0 B/tok) once sliced below `big`.
        eng._token_oracle = eng.FixtureTokenAdapter(
            lambda t: 2.5 if len(t.encode("utf-8")) >= big else 1.0)
        fetched = [_ok("https://f.example/x", _dense_content(9000))]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        # Final zone must be within the effective budget under the fixture's own count.
        self.assertLessEqual(eng._token_oracle.count(zone), self._EFFECTIVE)

    def test_oracle_unavailable_falls_back_to_safe_floor(self):
        # A tokenizer failure must NOT crash the run and must NOT use the legacy bytes÷2.5;
        # it falls back to the safe bytes÷1.0 floor (zone bytes <= effective token budget).
        class _Boom(eng.TokenOracle):
            def count(self, text):
                raise RuntimeError("tokenizer exploded")
        eng._token_oracle = _Boom()
        fetched = [_ok("https://b.example/x", _dense_content(9000))]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        # bytes÷1.0 floor: assembled zone bytes <= effective budget, plus the AD16-lite
        # truncation note (~250 B) that a trimmed zone carries; real tokens stay <= budget.
        note_bytes = len(eng._ZONE_TRUNCATION_NOTE.encode("utf-8"))
        self.assertLessEqual(_zbytes(zone), self._EFFECTIVE + note_bytes)
        self.assertLess(_zbytes(zone), eng._ZONE_MAX_TOTAL_BYTES)  # far under the legacy cap
        self.assertTrue(zone.startswith(eng._ZONE_TRUNCATION_NOTE))  # fallback is a trim

    def test_oracle_CONSTRUCTION_failure_also_falls_back(self):
        """Sibling of the test above, for the failure mode it does not reach.

        That test injects an already-BUILT oracle whose `count` raises, so it only proves
        the fallback covers tokenizer INVOCATION failure. Both docstrings promise more than
        that — "on ANY tokenizer failure the domain falls back to the safe bytes÷1.0 floor" —
        but `_count_zone_tokens` builds the oracle OUTSIDE its own `try`, so an oracle that
        fails to CONSTRUCT (its backing module is not importable under the running
        interpreter) escapes unconverted, is never turned into `OracleUnavailable`, and
        propagates straight out of `_build_quarantine_zone`.

        Why that matters far beyond this function: at the `factcheck_run` call site the
        prefetch + zone assembly sit inside a blanket `except Exception` that silently sets
        BOTH the fetched sources and the zone to None. So a tokenizer that cannot be built
        does not fail loudly — it hands every checker a prompt with no source content, and
        the run can only return INCOMPLETE, misfiled under the residual `content` reason.
        Observed live 2026-08-16: `tiktoken` was installed only for the system Python while
        the engine ran under a newer interpreter, and all four Group IV research files came
        back INCOMPLETE with their panels reporting no `<quarantined_source_content>` zone.
        """
        class _BoomOnBuild:
            def __init__(self, *args, **kwargs):
                raise ModuleNotFoundError("No module named 'tiktoken'")

        eng._token_oracle = None            # force construction on the next count
        real_adapter = eng.LocalTokenizerAdapter
        eng.LocalTokenizerAdapter = _BoomOnBuild
        try:
            fetched = [_ok("https://b.example/x", _dense_content(9000))]
            zone = eng._build_quarantine_zone(
                fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
                max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        finally:
            eng.LocalTokenizerAdapter = real_adapter

        # Same guarantee the invocation-failure sibling asserts: a trimmed, byte-floored
        # zone — NOT an exception, and NOT the legacy bytes÷2.5 that recreates the overflow.
        note_bytes = len(eng._ZONE_TRUNCATION_NOTE.encode("utf-8"))
        self.assertLessEqual(_zbytes(zone), self._EFFECTIVE + note_bytes)
        self.assertLess(_zbytes(zone), eng._ZONE_MAX_TOTAL_BYTES)
        self.assertTrue(zone.startswith(eng._ZONE_TRUNCATION_NOTE))
        # The zone must still carry real CONTENT, not just framing. The URL alone is too
        # weak a probe: `_assemble_zone_bytes` emits the `<source url=...>` tag before any
        # status branch, so a wrapper-only zone (every source STATUS: UNFETCHABLE:
        # zone-budget-exceeded, no bytes) would satisfy an assertIn on the URL while
        # reproducing the exact symptom this test exists to catch — checkers handed a zone
        # with nothing to verify against. Size is the discriminator, per the idiom the
        # sibling trim tests in this class already use.
        self.assertIn("https://b.example/x", zone)
        self.assertGreater(len(zone), 1000,
                           "the fallback zone must still carry real source content, "
                           "not just per-source framing")


class IngestByteCeilingTests(unittest.TestCase):
    """Group III / E2a (A3 / AD10) — the aggregate raw-byte ingest ceiling bounds total
    fetched-content memory across many URLs; sources beyond it surface UNFETCHABLE, never
    a silent drop. (Per-URL byte-before-decode capping already lives in `_fetch_one_url`.)"""

    def test_aggregate_ceiling_caps_total_fetched_bytes(self):
        payload = "Z" * (300 * 1024)  # 300 KB per source

        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", payload[:max_bytes])

        urls = [f"https://ex.example/{i}" for i in range(40)]
        ceiling = 1_000_000  # 1 MB aggregate
        fetched = eng._prefetch_sources(
            urls, max_total_bytes=ceiling, _fetch_fn=fake_fetch)
        ok = [f for f in fetched if f["status"] == "ok"]
        ok_bytes = sum(len(f["content"].encode("utf-8")) for f in ok)
        # The aggregate memory bound still holds — that is the OOM guarantee, and it is
        # what this test has always been for. Slack of one per-URL share covers the
        # boundary source that is admitted while cumulative is still under the ceiling.
        share = max(eng._PREFETCH_MIN_PER_URL_BYTES, ceiling // len(urls))
        self.assertLessEqual(ok_bytes, ceiling + share)
        # Every URL is still accounted for — nothing is silently dropped.
        self.assertEqual(len(fetched), len(urls))

    def test_fair_share_fetches_EVERY_source_instead_of_stranding_the_tail(self):
        """The tail of a citation-dense report must be fetched, not stranded.

        Before fair-sharing, the leading sources spent the aggregate ceiling
        first-come-first-served and every remaining URL was marked UNFETCHABLE without
        being fetched at all — so POSITION in the citation list, not relevance, decided
        what a checker could verify. Observed on all four clarification-v2 research files
        (25-43 cited URLs): the whole tail of each list came back "ingest byte ceiling
        exceeded", a majority of every file's sources went unverified, and the run could
        only ever return INCOMPLETE for a reason that was about the budget rather than
        about the research.

        The guarantee asserted here is coverage: with a realistic citation count, EVERY
        source is fetched, and the aggregate bound is still respected.
        """
        payload = "Z" * (300 * 1024)  # each page far larger than its fair share

        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", payload[:max_bytes])

        urls = [f"https://ex.example/{i}" for i in range(43)]  # RC3V2's real URL count
        ceiling = eng._ZONE_INGEST_MAX_BYTES
        fetched = eng._prefetch_sources(
            urls, max_total_bytes=ceiling, _fetch_fn=fake_fetch)

        stranded = [f for f in fetched if f.get("content") ==
                    "ingest byte ceiling exceeded (aggregate memory cap)"]
        self.assertEqual(stranded, [], "no source may be stranded unfetched at a "
                                       "realistic citation count")
        self.assertTrue(all(f["status"] == "ok" for f in fetched))
        # Coverage must not come at the cost of the memory bound.
        ok_bytes = sum(len(f["content"].encode("utf-8")) for f in fetched)
        self.assertLessEqual(ok_bytes, ceiling)
        # And the tail specifically — the part that used to be stranded — carries real
        # content, not a status string.
        self.assertGreater(len(fetched[-1]["content"]), 1024)

    def test_backstop_still_marks_when_even_the_floor_cannot_fit(self):
        """The OOM ceiling is a backstop, not a casualty of fair-sharing.

        Fair-sharing cannot help a report citing more sources than the per-URL floor
        allows; there the aggregate ceiling must still bound memory, and the sources it
        cannot admit must be MARKED (-> INCOMPLETE via the sentinel), never dropped
        silently. This is the guarantee the original ceiling test asserted, kept here
        where it still applies.
        """
        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", "Z" * max_bytes)

        ceiling = 1_000_000
        # Far more URLs than ceiling // floor (~61), so the floor cannot fit them all.
        urls = [f"https://ex.example/{i}" for i in range(300)]
        fetched = eng._prefetch_sources(
            urls, max_total_bytes=ceiling, _fetch_fn=fake_fetch)

        marked = [f for f in fetched if f.get("content") ==
                  "ingest byte ceiling exceeded (aggregate memory cap)"]
        self.assertTrue(marked, "beyond the floor, overflow must still be marked")
        self.assertEqual(len(fetched), len(urls), "marked, never dropped")
        ok_bytes = sum(len(f["content"].encode("utf-8"))
                       for f in fetched if f["status"] == "ok")
        self.assertLessEqual(ok_bytes, ceiling + eng._PREFETCH_MIN_PER_URL_BYTES)

    def test_fair_share_never_raises_the_per_url_cap(self):
        """A report with few sources is byte-for-byte unchanged.

        `ceiling // len(urls)` is large when there are few URLs, so the share is clamped
        by `min(max_bytes, ...)`. Without that clamp this change would have widened the
        per-URL capture cap, which is a different contract entirely.
        """
        seen = []

        def fake_fetch(url, timeout_s, max_bytes):
            seen.append(max_bytes)
            return ("ok", "small")

        eng._prefetch_sources([f"https://ex.example/{i}" for i in range(3)],
                              _fetch_fn=fake_fetch)
        self.assertTrue(seen)
        self.assertTrue(all(b == eng._PREFETCH_MAX_BYTES for b in seen),
                        f"per-URL cap must stay {eng._PREFETCH_MAX_BYTES}, saw {set(seen)}")

    def test_ceiling_is_a_no_op_for_realistic_reports(self):
        # A handful of small sources never trips the ceiling (no behavior change).
        def fake_fetch(url, timeout_s, max_bytes):
            return ("ok", "small content")
        urls = [f"https://ex.example/{i}" for i in range(20)]
        fetched = eng._prefetch_sources(urls, _fetch_fn=fake_fetch)
        self.assertTrue(all(f["status"] == "ok" for f in fetched))


class IncompleteReasonAndOmtmTests(unittest.TestCase):
    """Group III / E2a (A4 / AD8) — an E2a-class checker CRASH is a DISTINCT INCOMPLETE
    signal: classified on the marker and split out in the OMTM breakdown."""

    def test_classify_crash_timeout_content(self):
        crash = [{"model": "sonnet",
                  "verdict": "INCOMPLETE: checker subprocess failed (exit 1)"}]
        timeout = [{"model": "sonnet",
                    "verdict": "INCOMPLETE: checker exceeded 900s budget"}]
        content = [{"model": "sonnet",
                    "verdict": "VERDICT: INCOMPLETE — source unreachable"}]
        self.assertEqual(eng._classify_incomplete_reason(crash), "crash")
        self.assertEqual(eng._classify_incomplete_reason(timeout), "timeout")
        self.assertEqual(eng._classify_incomplete_reason(content), "content")

    def test_incomplete_reason_round_trips_through_marker(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            rf = Path(d) / "R1.md"
            eng._write_round_file(
                rf, 1,
                [{"checker": 1, "model": "sonnet",
                  "verdict": "INCOMPLETE: checker subprocess failed (exit 1)"}],
                "INCOMPLETE", "research")
            parsed = eng._parse_marker_frontmatter(rf)
            self.assertEqual(parsed.get("verdict"), "INCOMPLETE")
            self.assertEqual(parsed.get("incomplete_reason"), "crash")

    def test_omtm_splits_crash_incomplete_out(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            research = Path(d) / "proj" / "topic" / "research"
            research.mkdir(parents=True)
            eng._write_round_file(
                research / "R1.md", 1,
                [{"checker": 1, "model": "sonnet",
                  "verdict": "INCOMPLETE: checker subprocess failed (exit 1)"}],
                "INCOMPLETE", "research")
            res = eng.compute_omtm_rate(d, window_days=3650)
            self.assertEqual(res["breakdown"]["incomplete"], 1)
            self.assertEqual(res["incomplete_by_reason"]["crash"], 1)
            self.assertEqual(res["incomplete_by_reason"]["content"], 0)


class TruncationNoteTests(unittest.TestCase):
    """Group III / E2a (A4 / AD16-lite) — a trimmed zone carries the partial-coverage note;
    a clean zone does not; the note is idempotent (never double-prepended)."""

    def tearDown(self):
        eng._token_oracle = None

    def test_trimmed_dense_zone_carries_note(self):
        fetched = [_ok("https://dense.example/x", _dense_content(8000))]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        self.assertTrue(zone.startswith(eng._ZONE_TRUNCATION_NOTE),
                        "a token-trimmed dense zone must carry the partial-coverage note")

    def test_untrimmed_small_zone_has_no_note(self):
        fetched = [_ok("https://small.example/x", "a short line of content")]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        self.assertFalse(zone.startswith(eng._ZONE_TRUNCATION_NOTE))
        self.assertNotIn(eng._ZONE_TRUNCATION_NOTE, zone)

    def test_note_prepend_is_idempotent(self):
        z = "<quarantined_source_content>\n...\n</quarantined_source_content>"
        once = eng._prepend_truncation_note(z)
        twice = eng._prepend_truncation_note(once)
        self.assertEqual(once, twice)
        self.assertEqual(twice.count(eng._ZONE_TRUNCATION_NOTE), 1)


def _prose_content(n):
    return ("The quick brown fox jumps over the lazy dog near the river bank at dawn. "
            * n)


def _code_url_content(n):
    return ("def handler(req, ctx):\n    return fetch('https://api.example.com/v2/"
            "resource?id=%d&sig=abc123&ts=1700000000')  # inline comment\n" % 1) * n


class DensitySpanningCorpusTests(unittest.TestCase):
    """Group III / E2a (A5 / AD9) — the root cause was calibration to ONE prose fixture.
    Validate the real-token gate across the FULL density range (prose ~4.5, code/URL ~2.5,
    hash ~1.7 B/tok) — EACH tier's token-bounded zone must fit the budget, never just one."""

    CORPUS = {
        "prose":    _prose_content(20000),   # ~4.5 B/tok (loose — the original calibration)
        "code_url": _code_url_content(6000),  # ~2.5 B/tok
        "hash":     _dense_content(9000),     # ~1.7-1.9 B/tok (the density that crashed)
    }

    def test_every_density_tier_is_token_bounded(self):
        oracle = eng.LocalTokenizerAdapter()
        for tier, content in self.CORPUS.items():
            fetched = [_ok(f"https://ex.example/{tier}", content)]
            zone = eng._build_quarantine_zone(
                fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
                max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
            real = oracle.count(zone)
            self.assertLessEqual(
                real, eng._ZONE_MAX_TOTAL_TOKENS,
                f"[{tier}] token-bounded zone {real} tok > budget "
                f"{eng._ZONE_MAX_TOTAL_TOKENS} — single-fixture failure would recur here")
            self.assertGreater(len(zone), 500, f"[{tier}] zone must carry real content")

    def test_observable_multi_file_cycle_each_file_bounded(self):
        # AD12 (observable): a multi-file research cycle whose files span densities — each
        # file's assembled zone independently reaches a within-budget, no-crash state.
        oracle = eng.LocalTokenizerAdapter()
        results = {}
        for tier, content in self.CORPUS.items():
            # each "file" carries several dense sources (a realistic research file)
            fetched = [_ok(f"https://ex.example/{tier}/{i}", content) for i in range(3)]
            zone = eng._build_quarantine_zone(
                fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
                max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
            results[tier] = oracle.count(zone)
        # No file mislabeled/crashed; every file independently within budget.
        self.assertEqual(set(results), set(self.CORPUS))
        for tier, real in results.items():
            self.assertLessEqual(real, eng._ZONE_MAX_TOTAL_TOKENS, f"[{tier}] over budget")

    def test_mixed_density_single_zone_stays_bounded(self):
        # One zone assembled from ALL tiers together (fair-share across mixed densities).
        oracle = eng.LocalTokenizerAdapter()
        fetched = [_ok(f"https://ex.example/{t}", c) for t, c in self.CORPUS.items()]
        zone = eng._build_quarantine_zone(
            fetched, max_total_bytes=eng._ZONE_MAX_TOTAL_BYTES,
            max_total_tokens=eng._ZONE_MAX_TOTAL_TOKENS)
        self.assertLessEqual(oracle.count(zone), eng._ZONE_MAX_TOTAL_TOKENS)


if __name__ == "__main__":
    unittest.main()

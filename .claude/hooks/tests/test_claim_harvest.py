"""Tests for the A17 harvest + /re-fc dissolution + dual-mode migration (Slice S8)."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_ledger as cl     # noqa: E402
import _claim_register as cr   # noqa: E402
import _claim_harvest as h     # noqa: E402

FIXTURES = Path("$CLAUDE_PROJECT_DIR/Thoughts")

_DOC = (
    "**Status:** ✅ VERIFIED 2026-07-06 — `/double-check 3,1,2` → PASS / COVERS\n"
    "- Port-53 hijack catches plaintext DNS. [stated — https://sing-box.example/tun]\n"
    "- NEDNSProxyProvider is macOS 10.15+. [paraphrased — https://developer.apple/t]\n"
    "- This is opaque and risky. [My assessment] plus [unverified — 403]\n"
)


@pytest.fixture
def wired(tmp_path):
    led = cl.ClaimLedger(tmp_path / "claims.ledger.jsonl")
    reg = cr.EvidenceRegister(tmp_path / "topic_CLAIMS.md", "topic", ledger=led)
    return led, reg


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# ── verification signal = prose, NOT frontmatter (S1 finding) ────────────────

def test_verified_reads_prose_not_frontmatter():
    assert h.is_verified(_DOC) is True
    # a file whose frontmatter says ESCALATE but has no VERIFIED prose is excluded
    assert h.is_verified("---\nfc_cycles:\n  verdict: ESCALATE\n---\nbody\n") is False


def test_frontmatter_escalate_does_not_block_when_prose_verified():
    doc = "---\nfc_cycles:\n  verdict: ESCALATE\n---\n" + _DOC
    assert h.is_verified(doc) is True     # prose VERIFIED wins over frontmatter ESCALATE


# ── hybrid capture: stated auto, paraphrased confirm, editorial excluded ─────

def test_marker_extraction_classifies_by_kind():
    m = h.extract_marked_claims(_DOC)
    assert len(m["stated"]) == 1
    assert len(m["paraphrased"]) == 1
    assert len(m["excluded"]) == 2       # [My assessment] + [unverified]
    assert m["stated"][0]["text"].startswith("Port-53 hijack")


def test_markers_legend_line_is_not_harvested(wired, tmp_path):
    # /challenge finding: a "> Markers: `[stated — URL]` direct fact; …" legend line
    # must NOT be harvested as a claim.
    led, reg = wired
    doc = ("**Status:** ✅ VERIFIED — `/double-check` PASS\n"
           "> Markers: `[stated — URL]` direct fact; `[paraphrased — URL]` paraphrase.\n"
           "- Port-53 hijack catches plaintext DNS. [stated — https://x/tun]\n")
    m = h.extract_marked_claims(doc)
    assert len(m["stated"]) == 1                       # only the real claim, not the legend
    assert m["stated"][0]["text"].startswith("Port-53 hijack")
    f = _write(tmp_path, "X_RESEARCH.md", doc)
    r = h.harvest(f, ledger=led, register=reg, checked_at="t0")
    assert len(r["harvested"]) == 1
    assert all("Markers" not in led.current_state(cid)["text"] for cid in r["harvested"])


def test_claim_is_last_sentence_not_whole_blob(wired):
    # atomicity guard: a long heading + prose before a marker yields the last sentence.
    doc = ("**A bold multi-clause heading about NEDNSProxyProvider and its history.** "
           "It is available on macOS 10.15+. [stated — https://x/a]\n")
    m = h.extract_marked_claims(doc)
    assert m["stated"][0]["text"] == "It is available on macOS 10.15+."


def test_editorial_prose_after_my_assessment_not_harvested():
    # positional-leak fix: prose after [My assessment] before a [stated] is commentary.
    doc = "- Claim one. [My assessment] Practically this is risky. [stated — https://x/b]\n"
    m = h.extract_marked_claims(doc)
    assert m["stated"] == []                            # the commentary is not a claim
    assert len(m["excluded"]) == 1


def test_marker_first_orientation_harvested():
    # MDM-style: marker leads, claim (quote) follows on the same line.
    doc = ('> [stated — https://x/a] "Supervision generally denotes that the org owns '
           'the device."\n')
    m = h.extract_marked_claims(doc)
    assert len(m["stated"]) == 1
    assert m["stated"][0]["text"].startswith("Supervision generally denotes")
    assert '"' not in m["stated"][0]["text"][:1]      # surrounding quote stripped


def test_multi_marker_first_no_double_count():
    # two marker-first claims on one line → 2 DISTINCT claims, no span harvested twice.
    doc = '> [stated — https://x/a] "Claim one holds." [stated — https://x/b] "Claim two holds."\n'
    m = h.extract_marked_claims(doc)
    texts = [c["text"] for c in m["stated"]]
    assert len(texts) == 2
    assert texts[0].startswith("Claim one") and texts[1].startswith("Claim two")
    assert len(set(texts)) == 2                        # no duplicate / double-count


def test_claim_first_still_works_after_orientation_fix():
    # DNS-style claim-then-marker must be unchanged: two claims, before-marker text.
    doc = "Port-53 hijack catches DNS. [stated — https://x/a] It misses DoH. [stated — https://x/b]\n"
    m = h.extract_marked_claims(doc)
    texts = [c["text"] for c in m["stated"]]
    assert texts == ["Port-53 hijack catches DNS.", "It misses DoH."]


def test_real_mdm_file_now_harvests_marker_first():
    mdm = FIXTURES / "per-app-network-routing_MDM_RESEARCH.md"
    if not mdm.exists():
        pytest.skip("fixture absent")
    m = h.extract_marked_claims(mdm.read_text(encoding="utf-8"))
    assert len(m["stated"]) > 0                        # was 0 before the orientation fix
    # no duplicate claim texts (double-count guard on the real file)
    texts = [c["text"] for c in m["stated"] + m["paraphrased"]]
    assert len(texts) == len(set(texts))


def test_is_verified_rejects_negation():
    assert h.is_verified("**Status:** Not yet verified — double-check did not pass.") is False
    assert h.is_verified("**Status:** ✅ VERIFIED — /double-check PASS") is True
    # benign 'not' elsewhere on a genuinely-verified Status line still passes
    assert h.is_verified(
        "**Status:** ✅ VERIFIED — /double-check PASS; conclusions not changed.") is True
    # requires a real 'pass' word-token, not 'passphrase'
    assert h.is_verified("**Status:** verified the double-check passphrase") is False
    # must be a Status line
    assert h.is_verified("some verified double-check pass note without the word") is False


def test_harvest_auto_stated_defers_paraphrased(wired, tmp_path):
    led, reg = wired
    f = _write(tmp_path, "X_RESEARCH.md", _DOC)
    r = h.harvest(f, ledger=led, register=reg, checked_at="t0")
    assert r["skipped"] is False
    assert len(r["harvested"]) == 1              # the [stated] claim
    assert len(r["deferred_paraphrased"]) == 1   # [paraphrased] awaits confirm
    assert len(r["excluded"]) == 2               # editorial/unverified never harvested


def test_harvest_confirm_includes_paraphrased(wired, tmp_path):
    led, reg = wired
    f = _write(tmp_path, "X_RESEARCH.md", _DOC)
    r = h.harvest(f, ledger=led, register=reg, checked_at="t0", confirm_paraphrased=True)
    assert len(r["harvested"]) == 2
    assert r["deferred_paraphrased"] == []


def test_harvested_claims_are_grounding_truth(wired, tmp_path):
    led, reg = wired
    f = _write(tmp_path, "X_RESEARCH.md", _DOC)
    h.harvest(f, ledger=led, register=reg, checked_at="t0", thought_status="DONE")
    # verified source ⇒ faithful; DONE ⇒ committed; VALID ⇒ grounding-truth
    assert len(reg.grounding_entries()) == 1


def test_harvest_preserves_native_language(wired, tmp_path):
    led, reg = wired
    doc = ("**Status:** ✅ VERIFIED — `/double-check` PASS\n"
           "- Роутинг по приложению работает на iOS. [stated — https://x/ru]\n")
    f = _write(tmp_path, "RU_RESEARCH.md", doc)
    h.harvest(f, ledger=led, register=reg, checked_at="t0", lang="ru")
    cid = reg.grounding_entries()[0].claim_id
    assert led.current_state(cid)["text"] == "Роутинг по приложению работает на iOS."


# ── on-verify gate excludes unverified / ESCALATE'd ──────────────────────────

def test_unverified_file_skipped(wired, tmp_path):
    led, reg = wired
    f = _write(tmp_path, "ESC_RESEARCH.md",
               "---\nfc_cycles:\n  verdict: ESCALATE\n---\n- A. [stated — http://x]\n")
    r = h.harvest(f, ledger=led, register=reg, checked_at="t0")
    assert r["skipped"] is True
    assert reg.entries() == []                   # nothing harvested


# ── observable (4): re-FC = re-run the validation consumer, not 2c ───────────

def test_re_fact_check_is_consumer_rerun_not_2c(wired, tmp_path):
    led, reg = wired
    f = _write(tmp_path, "X_RESEARCH.md", _DOC)
    h.harvest(f, ledger=led, register=reg, checked_at="t0")
    res = h.re_fact_check(reg, ["Port-53 hijack does NOT catch plaintext DNS."])
    assert res["used_double_check_2c"] is False
    assert res["mode"] == "validation-consumer-rerun"
    assert len(res["contradictions"]) == 1


# ── dual-mode migration + [Decommission] (Q-A) ───────────────────────────────

def test_flip_to_manageable_retains_default_and_emits_reminder():
    flip = h.flip_to_manageable("/plan Gate 0b2")
    assert flip["default_retained"] is True
    assert flip["mode"] == "manageable"
    assert flip["decommission_reminder"].startswith("[Decommission]")


def test_consumer_sites_named():
    assert "/extract-knowledge" in h.CONSUMER_SITES
    assert "/clarification Step 2" in h.CONSUMER_SITES


# ── real-fixture integration: 7 verified, main+RU excluded ───────────────────

@pytest.mark.skipif(not FIXTURES.exists(), reason="fixture dir absent")
def test_real_fixture_verification_gate_7_verified_2_excluded():
    files = sorted(FIXTURES.glob("per-app-network-routing_*RESEARCH*.md"))
    if not files:
        pytest.skip("fixture files absent")
    verified = [f.name for f in files if h.is_verified(f.read_text(encoding="utf-8"))]
    excluded = [f.name for f in files if f.name not in verified]
    assert len(verified) == 7
    assert set(excluded) == {"per-app-network-routing_RESEARCH.md",
                             "per-app-network-routing_RESEARCH_RU.md"}

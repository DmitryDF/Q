"""Tests for the Evidence Register + check_against() (Slice S7)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce    # noqa: E402
import _claim_ledger as cl    # noqa: E402
import _claim_state as cst    # noqa: E402
import _claim_register as cr  # noqa: E402


def _flags():
    return {c: True for c in ce.CRITERIA}


def _claim(text, line, cid, lang="en"):
    return ce.Claim(text, ce.Anchor.local_file("DNS_RESEARCH.md", line),
                    ce.ClaimFlags.from_dict(_flags()), ce.ClaimRole.BACKWARD, lang,
                    claim_id=cid)


@pytest.fixture
def wired(tmp_path):
    led = cl.ClaimLedger(tmp_path / "claims.ledger.jsonl")
    reg = cr.EvidenceRegister(tmp_path / "topic_CLAIMS.md", "topic", ledger=led)
    return led, reg


def _register(led, reg, text, line, cid, status="DONE", lang="en",
              validity=cst.Validity.VALID):
    c = _claim(text, line, cid, lang=lang)
    led.record_extracted(c, checked_at="t0")
    reg.add(c, cst.build_state(c, thought_status=status, validity=validity))
    return c


# ── locators + state, not content (rule 10) ──────────────────────────────────

def test_register_stores_locator_not_text(wired, tmp_path):
    led, reg = wired
    _register(led, reg, "NEDNSProxyProvider is available on macOS 10.15.", 36, "C-0001")
    body = (tmp_path / "topic_CLAIMS.md").read_text()
    assert "DNS_RESEARCH.md:36" in body
    assert "available on macOS" not in body        # text stays in the ledger/source


def test_ensure_exists_idempotent(wired):
    _, reg = wired
    assert reg.ensure_exists() is True
    assert reg.ensure_exists() is False


def test_add_requires_ledger_assigned_id(wired):
    _, reg = wired
    c = ce.Claim("X.", ce.Anchor.local_file("f.md", 1),
                 ce.ClaimFlags.from_dict(_flags()), ce.ClaimRole.BACKWARD, "en")  # no id
    with pytest.raises(ValueError):
        reg.add(c, cst.build_state(c, thought_status="DONE"))


# ── check_against surfaces contradictions (observable 3), never blocks ───────

def test_contradiction_surfaces_no_block(wired):
    led, reg = wired
    _register(led, reg, "NEDNSProxyProvider is available on macOS 10.15.", 36, "C-0001")
    res = reg.check_against("NEDNSProxyProvider is NOT available on macOS 10.15.")
    assert len(res["contradictions"]) == 1
    assert res["contradictions"][0]["claim_id"] == "C-0001"
    assert res["blocked"] is False                  # surfaces, does not decide (A9/U7)


def test_agreement_does_not_surface(wired):
    led, reg = wired
    _register(led, reg, "Port-53 hijack catches plaintext DNS.", 45, "C-0001")
    res = reg.check_against("Port-53 hijack catches plaintext DNS.")
    assert res["contradictions"] == []


def test_unrelated_statement_does_not_surface(wired):
    led, reg = wired
    _register(led, reg, "Port-53 hijack catches plaintext DNS.", 45, "C-0001")
    res = reg.check_against("The App Store review guidelines require VPN entitlements.")
    assert res["contradictions"] == []


# ── consults only the grounding set (Q-I default) ────────────────────────────

def test_check_against_ignores_non_grounding_claims(wired):
    led, reg = wired
    # a WIP claim (not committed) is present but must NOT be consulted for grounding
    _register(led, reg, "X is available.", 1, "C-0001", status="in progress")
    res = reg.check_against("X is NOT available.")
    assert res["contradictions"] == []              # WIP claim not in grounding set
    assert any(x["claim_id"] == "C-0001" for x in res["excluded_not_truth"])


def test_grounding_and_excluded_partition(wired):
    led, reg = wired
    _register(led, reg, "A holds.", 1, "C-0001", status="DONE")
    _register(led, reg, "B holds.", 2, "C-0002", status="Retired 2026-07-01")
    assert len(reg.grounding_entries()) == 1
    excluded = reg.excluded_entries()
    assert len(excluded) == 1 and "retired" in excluded[0]["reason"].lower()


# ── language-heterogeneous register + cross-lingual carry-forward ────────────

def test_register_is_language_heterogeneous(wired):
    led, reg = wired
    _register(led, reg, "A holds.", 1, "C-0001", lang="en")
    _register(led, reg, "Роутинг по приложению работает.", 2, "C-0002", lang="ru")
    langs = {e.lang for e in reg.entries()}
    assert langs == {"en", "ru"}


def test_cross_lingual_flagged_not_compared(wired):
    led, reg = wired
    _register(led, reg, "NEDNSProxyProvider доступен на macOS.", 36, "C-0001", lang="ru")
    res = reg.check_against("NEDNSProxyProvider is NOT available on macOS.", lang="en")
    assert res["contradictions"] == []              # not compared across languages
    assert len(res["cross_lingual_uncompared"]) == 1
    assert "carry-forward" in res["cross_lingual_uncompared"][0]["note"]

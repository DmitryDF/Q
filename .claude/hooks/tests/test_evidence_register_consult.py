#!/usr/bin/env python3
"""Slice S8 · A2 — Evidence Register declaration + consultation gate, on the REAL fixture.

Composes A1 (harvest) → A2 (consult): harvest a real verified `_RESEARCH` file into a
tmp-copied register, declare it in a tmp CLAUDE.md, then consult. Proves the surface-only
contradiction path (observable 3) end-to-end on real data — non-destructively.
"""

import shutil
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _claim_harvest_trigger as trig            # noqa: E402
import _evidence_register_consult as consult     # noqa: E402
from _claim_ledger import ClaimLedger             # noqa: E402

FIXTURE = Path("$CLAUDE_PROJECT_DIR/Thoughts/per-app-network-routing_DNS_RESEARCH.md")

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="per-app-network-routing fixture not materialized")


@pytest.fixture
def harvested(tmp_path):
    """Copy the DNS fixture, harvest it → a populated tmp register. Return paths."""
    dst = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, dst)
    res = trig.on_research_write(dst, checked_at="t0")
    assert not res["skipped"] and res["harvested"], res
    register = tmp_path / "per-app-network-routing_CLAIMS.md"
    ledger = tmp_path / "per-app-network-routing.claims.ledger.jsonl"
    assert register.exists()
    return {"dir": tmp_path, "register": register, "ledger": ledger,
            "claim_ids": res["harvested"]}


def test_declaration_optin_and_resolve(harvested):
    d = harvested["dir"]
    (d / "CLAUDE.md").write_text(
        "some: header\nevidence_register: per-app-network-routing_CLAIMS.md\n", encoding="utf-8")
    resolved = consult.resolve_register(d)
    assert resolved and Path(resolved["register"]).name == "per-app-network-routing_CLAIMS.md"
    # no declaration → silent no-op
    (d / "CLAUDE.md").write_text("no declaration here\n", encoding="utf-8")
    assert consult.resolve_register(d) is None


def test_contradiction_surfaces_never_blocks(harvested):
    ledger = ClaimLedger(harvested["ledger"])
    # take a real harvested grounding claim's text and negate it lexically
    cid = harvested["claim_ids"][0]
    text = ledger.current_state(cid)["text"]
    negated = "It is not true that " + text

    res = consult.consult(harvested["register"], negated)
    assert res["consulted"] is True
    assert res["blocked"] is False, "surface-only — the gate must NEVER block (U7)"
    assert len(res["contradictions"]) >= 1, f"a negation of {cid} must surface: {res}"
    assert "CONTRADICTION" in consult.format_consultation(res)


def test_agreement_surfaces_nothing(harvested):
    ledger = ClaimLedger(harvested["ledger"])
    text = ledger.current_state(harvested["claim_ids"][0])["text"]
    res = consult.consult(harvested["register"], text)          # verbatim → agreement
    assert res["contradictions"] == []
    assert consult.format_consultation(res) == ""


def test_missing_register_is_inert(tmp_path):
    res = consult.consult(tmp_path / "absent_CLAIMS.md", "anything at all")
    assert res["consulted"] is False and res["blocked"] is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

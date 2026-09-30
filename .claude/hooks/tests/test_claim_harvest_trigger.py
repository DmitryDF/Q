#!/usr/bin/env python3
"""Slice S8 · A1 — live on-verify harvest trigger, tested against the REAL fixture.

Non-destructive: the 9 `per-app-network-routing_*_RESEARCH*.md` fixture files are
COPIED into a tmpdir (never mutated in place; the real project register is never
touched). The trigger harvests into the tmp copy's co-located register/ledger/state.

Validates the plan's A1 gate: the 7 verified topic-scoped files harvest into the
empty `_CLAIMS.md`; main + RU (ESCALATE'd) are excluded by absence-of-PASS; a re-fire
adds nothing new to the register.
"""

import shutil
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _claim_harvest_trigger as trig  # noqa: E402

FIXTURE_DIR = Path("$CLAUDE_PROJECT_DIR/Thoughts")
VERIFIED_7 = [
    "per-app-network-routing_DNS_RESEARCH.md",
    "per-app-network-routing_KILLSWITCH_RESEARCH.md",
    "per-app-network-routing_MDM_RESEARCH.md",
    "per-app-network-routing_MDMATTR_RESEARCH.md",
    "per-app-network-routing_MITM_RESEARCH.md",
    "per-app-network-routing_PUSHCERT_RESEARCH.md",
    "per-app-network-routing_TWOTIER_RESEARCH.md",
]
EXCLUDED_2 = [
    "per-app-network-routing_RESEARCH.md",       # main — ESCALATE'd, no VERIFIED Status
    "per-app-network-routing_RESEARCH_RU.md",     # native RU — ESCALATE'd
]

pytestmark = pytest.mark.skipif(
    not (FIXTURE_DIR / VERIFIED_7[0]).exists(),
    reason="per-app-network-routing fixture not materialized in this checkout",
)


@pytest.fixture
def staged(tmp_path):
    """Copy all 9 fixture files into a tmpdir (same basenames → same topic slug)."""
    for name in VERIFIED_7 + EXCLUDED_2:
        src = FIXTURE_DIR / name
        if src.exists():
            shutil.copy2(src, tmp_path / name)
    return tmp_path


def test_seven_verified_files_harvest(staged):
    total = 0
    for name in VERIFIED_7:
        res = trig.on_research_write(staged / name, checked_at="t0")
        assert not res["skipped"], f"{name} should harvest: {res}"
        assert len(res["harvested"]) >= 1, f"{name} harvested nothing: {res}"
        total += len(res["harvested"])
    # The 7 verified files together auto-harvest their `[stated — URL]` claims.
    assert total >= 7, f"expected >=7 stated claims harvested across the 7 files, got {total}"
    register = staged / "per-app-network-routing_CLAIMS.md"
    assert register.exists()
    body = register.read_text(encoding="utf-8")
    # register holds locators + state, never claim text (rule 10)
    assert "_RESEARCH.md:" in body
    # every harvested row is grounding-truth (verified source + DONE + valid)
    assert body.count("| yes |") == total, "every auto-harvested stated claim is grounding-truth"


def test_main_and_ru_excluded(staged):
    for name in EXCLUDED_2:
        res = trig.on_research_write(staged / name, checked_at="t0")
        assert res["skipped"], f"{name} must be excluded: {res}"
        assert "VERIFIED" in res["reason"], res


def test_refire_adds_nothing_new(staged):
    first = trig.on_research_write(staged / VERIFIED_7[0], checked_at="t0")
    assert not first["skipped"] and first["harvested"]
    again = trig.on_research_write(staged / VERIFIED_7[0], checked_at="t1")
    assert again["harvested"] == [], f"re-fire must add no new register entries: {again}"


def test_native_language_preserved(staged):
    """The RU file lacks a VERIFIED Status (excluded), but if it DID verify, lang=ru
    must be preserved. Prove the lang-tagging path on a synthetic verified RU copy."""
    ru = staged / "per-app-network-routing_RESEARCH_RU.md"
    ru.write_text(
        "**Status:** ✅ VERIFIED — `/double-check` → PASS\n"
        "- NEDNSProxyProvider недоступен на этой ОС. [stated — https://x/ru]\n",
        encoding="utf-8")
    res = trig.on_research_write(ru, checked_at="t0")
    assert not res["skipped"] and len(res["harvested"]) == 1
    body = (staged / "per-app-network-routing_CLAIMS.md").read_text(encoding="utf-8")
    assert "| ru |" in body, "RU claim must be stored with lang=ru (no translation)"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

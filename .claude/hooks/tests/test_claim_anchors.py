"""Tests for the per-source-type anchor resolver + S5 engine features."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce      # noqa: E402
import _claim_anchors as ca     # noqa: E402
import _claim_ledger as cl      # noqa: E402


def _flags():
    return {c: True for c in ce.CRITERIA}


# ── resolver: every source type resolves a correct anchor ────────────────────

@pytest.mark.parametrize("stype,row,path,expected", [
    (ce.SourceType.LOCAL_FILE, {"source_line": 36}, "a_RESEARCH.md", "a_RESEARCH.md:36"),
    (ce.SourceType.PDF, {"page": 12}, "book.pdf", "book.pdf#page=12"),
    (ce.SourceType.EPUB, {"loc": "1337"}, "book.epub", "book.epub#loc=1337"),
    (ce.SourceType.WEB, {"fragment": "sec-3"}, "https://x/p", "https://x/p#sec-3"),
    (ce.SourceType.WEB, {}, "https://x/p", "https://x/p"),          # fragment optional
    (ce.SourceType.TRANSCRIPT, {"timestamp": "00:14:22"}, "talk.vtt", "talk.vtt@00:14:22"),
    # S12/A6 — a repository and a tracker. `path` carries the SOURCE IDENTITY for
    # these two (a repo name, a workspace), and the address within it comes from
    # the row, which is what a `code:` / `linear:` citation pin already spells.
    (ce.SourceType.CODE, {"rev": "abc123", "source_path": "src/app.py", "lines": "10-20"},
     "myrepo", "myrepo@abc123:src/app.py:10-20"),
    (ce.SourceType.LINEAR, {"version": "v7", "issue": "ENG-123"},
     "acme", "acme@v7:ENG-123"),
])
def test_every_source_type_resolves(stype, row, path, expected):
    src = ce.Source(stype, path, "…")
    anchor = ca.resolve(src, row)
    assert anchor.source_type is stype
    assert anchor.locator == expected


def test_every_source_type_has_a_scheme_single_change_locus():
    """The property this test exists for is that `SourceType` and `ANCHOR_SCHEMES`
    stay in lock-step — the Evolution Test the module claims for itself.

    It was written as an equality against the five types of the day, which S12
    opened by adding `CODE` and `LINEAR`. Stated as a RULE rather than a roster,
    it now holds for whatever the enum contains and cannot go stale the next time
    a source class is added — while still failing loudly if a member is added
    with no scheme, which is the whole point.
    """
    assert set(ca.supported_source_types()) == set(ce.SourceType)


def test_missing_required_locator_field_raises():
    src = ce.Source(ce.SourceType.PDF, "book.pdf", "…")
    with pytest.raises(ce.SchemaError):
        ca.resolve(src, {"source_line": 1})   # PDF needs 'page', not 'source_line'


# ── engine S5 features: thoroughness, document mode, regex fast-path ──────────

def _two_stage(s1, s2, **kw):
    return ce.TwoStageClaimEngine(ce.FakeModelAdapter(s1, s2), **kw)


def test_thoroughness_tier_selectable_and_recorded():
    s1 = json.dumps({"candidates": [{"text": "x", "source_line": 1,
                                     "ambiguous": False, "reason": ""}]})
    s2 = json.dumps({"claims": [{"text": "X.", "source_line": 1,
                                 "role": "backward", "flags": _flags()}]})
    fake = ce.FakeModelAdapter(s1, s2)
    eng = ce.TwoStageClaimEngine(fake)
    src = ce.Source(ce.SourceType.LOCAL_FILE, "a_RESEARCH.md", "…")
    cs = eng.identify(src, thoroughness=ce.Thoroughness.ULTRA_DEEP)
    assert cs.thoroughness is ce.Thoroughness.ULTRA_DEEP


def test_document_output_mode_persists_via_ledger(tmp_path):
    led = cl.ClaimLedger(tmp_path / "claims.ledger.jsonl")
    s1 = json.dumps({"candidates": [{"text": "x", "source_line": 1,
                                     "ambiguous": False, "reason": ""}]})
    s2 = json.dumps({"claims": [{"text": "X holds.", "source_line": 1,
                                 "role": "backward", "flags": _flags()}]})
    eng = _two_stage(s1, s2, ledger=led)
    src = ce.Source(ce.SourceType.LOCAL_FILE, "a_RESEARCH.md", "…")
    cs = eng.identify(src, output_mode=ce.OutputMode.DOCUMENT, checked_at="t0")
    assert cs.claims[0].claim_id == "C-0001"                 # ledger assigned an id
    assert led.current_state("C-0001")["text"] == "X holds."


def test_document_output_mode_requires_ledger_and_time():
    s1 = json.dumps({"candidates": []})
    eng = _two_stage(s1, json.dumps({"claims": []}))          # no ledger
    src = ce.Source(ce.SourceType.LOCAL_FILE, "a_RESEARCH.md", "…")
    with pytest.raises(ce.SchemaError):
        eng.identify(src, output_mode=ce.OutputMode.DOCUMENT, checked_at="t0")


def test_in_memory_mode_assigns_no_id():
    s1 = json.dumps({"candidates": [{"text": "x", "source_line": 1,
                                     "ambiguous": False, "reason": ""}]})
    s2 = json.dumps({"claims": [{"text": "X.", "source_line": 1,
                                 "role": "backward", "flags": _flags()}]})
    eng = _two_stage(s1, s2)
    cs = eng.identify(ce.Source(ce.SourceType.LOCAL_FILE, "a_RESEARCH.md", "…"))
    assert cs.claims[0].claim_id is None                      # not persisted


# ── regex fast-path (U2) ─────────────────────────────────────────────────────

def test_regex_fast_path_extracts_instantly_no_model():
    text = "# heading\n- Port-53 hijack catches plaintext DNS.\nNEDNSProxyProvider is macOS-only.\n\n"
    eng = ce.RegexFastPathEngine()
    cs = eng.identify(ce.Source(ce.SourceType.LOCAL_FILE, "a_RESEARCH.md", text))
    assert len(cs.claims) == 2                                # heading + blank skipped
    assert cs.claims[0].text == "Port-53 hijack catches plaintext DNS."
    assert cs.claims[0].anchor.locator == "a_RESEARCH.md:2"
    # fast path leaves the six criteria UNSCORED (the speed/quality trade-off)
    assert not cs.claims[0].flags.all_pass()


def test_regex_fast_path_is_a_claim_identification_port():
    assert isinstance(ce.RegexFastPathEngine(), ce.ClaimIdentificationPort)

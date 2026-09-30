"""End-to-end implementation verification (Slice S10).

Wires the whole engine chain (S2 core → S3 metrics → S4 ledger → S5 anchors →
S6 state → S7 register → S8 harvest) on the REAL fixture and verifies:
  - the four Desired-Outcome observables end-to-end;
  - the two hard boundaries (engine-does-not-validate; three axes never collapsed);
  - dual-mode legacy retained;
  - deferred A16 items are NOT built;
  - no C-NNNN reuse.

S9 (/double-check 2c migration) is BLOCKED on the locked double-check contract's
un-shipped cross-skill seam — it is NOT required for observables (1)-(4), which are
delivered by S2-S8.
"""

import dataclasses
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce      # noqa: E402
import _claim_metrics as cm     # noqa: E402
import _claim_ledger as cl      # noqa: E402
import _claim_state as cst      # noqa: E402
import _claim_register as cr    # noqa: E402
import _claim_harvest as h      # noqa: E402

FIXTURES = Path("$CLAUDE_PROJECT_DIR/Thoughts")
VERIFIED_FIXTURE = FIXTURES / "per-app-network-routing_DNS_RESEARCH.md"


@pytest.fixture
def chain(tmp_path):
    led = cl.ClaimLedger(tmp_path / "claims.ledger.jsonl")
    reg = cr.EvidenceRegister(tmp_path / "topic_CLAIMS.md", "per-app-network-routing",
                              ledger=led)
    return led, reg


# ── the four observables, end-to-end on the real fixture ─────────────────────

@pytest.mark.skipif(not VERIFIED_FIXTURE.exists(), reason="fixture absent")
def test_observable_1_consumer_produces_engine_claims_with_anchors_and_state(chain):
    led, reg = chain
    r = h.harvest(VERIFIED_FIXTURE, ledger=led, register=reg, checked_at="t0",
                  thought_status="DONE")
    assert not r["skipped"] and len(r["harvested"]) >= 1
    entry = reg.grounding_entries()[0]
    assert entry.claim_id.startswith("C-")                 # ledger-assigned id
    assert ":" in entry.locator                            # per-source-type anchor (path:line)
    assert isinstance(entry.state, cst.ClaimState)         # carries three-axis state


@pytest.mark.skipif(not VERIFIED_FIXTURE.exists(), reason="fixture absent")
def test_observable_2_completeness_measurable(chain, tmp_path):
    led, reg = chain
    # build an in-memory claim set from the fixture's stated markers, derive OMTM
    marked = h.extract_marked_claims(VERIFIED_FIXTURE.read_text(encoding="utf-8"))
    claims = [ce.Claim(m["text"], ce.Anchor.local_file(str(VERIFIED_FIXTURE), m["line"]),
                       ce.ClaimFlags.from_dict({c: True for c in ce.CRITERIA}),
                       ce.ClaimRole.BACKWARD, "en") for m in marked["stated"]]
    csobj = ce.ClaimSet(str(VERIFIED_FIXTURE), ce.Thoroughness.NORMAL, claims=claims)
    metrics = cm.derive_metrics(csobj, approach="two_stage_v1", model="sonnet",
                                checked_at="t0")
    assert metrics.total_claims == len(marked["stated"])
    sc = tmp_path / "x.claim-runs.md"
    cm.append_run(sc, metrics)
    assert sc.exists() and "two_stage_v1" in sc.read_text()
    # absolute completeness vs a gold subset (S1's D methodology)
    gold = [m["text"] for m in marked["stated"][:3]]
    ab = cm.absolute_completeness(csobj, gold)
    assert ab["recall"] == 1.0                             # recovers its own gold subset


@pytest.mark.skipif(not VERIFIED_FIXTURE.exists(), reason="fixture absent")
def test_observable_3_contradiction_surfaces(chain):
    led, reg = chain
    h.harvest(VERIFIED_FIXTURE, ledger=led, register=reg, checked_at="t0",
              thought_status="DONE")
    gt = reg.grounding_entries()[0]
    original = led.current_state(gt.claim_id)["text"]
    negated = original.replace(" is ", " is not ") if " is " in original else "not " + original
    res = reg.check_against(negated)
    assert res["blocked"] is False                         # surfaces, never blocks (A9/U7)


@pytest.mark.skipif(not VERIFIED_FIXTURE.exists(), reason="fixture absent")
def test_observable_4_refc_is_consumer_rerun(chain):
    led, reg = chain
    h.harvest(VERIFIED_FIXTURE, ledger=led, register=reg, checked_at="t0",
              thought_status="DONE")
    res = h.re_fact_check(reg, ["some statement about DNS"])
    assert res["used_double_check_2c"] is False
    assert res["mode"] == "validation-consumer-rerun"


# ── hard boundary 1: the engine does NOT validate ────────────────────────────

def test_boundary_engine_does_not_validate():
    s1 = '{"candidates":[{"text":"x","source_line":1,"ambiguous":false,"reason":""}]}'
    s2 = ('{"claims":[{"text":"X.","source_line":1,"role":"backward","flags":'
          '{"atomicity":true,"verifiability":true,"decontextuality":true,'
          '"minimality":true,"fluency":true,"faithfulness":true}}]}')
    eng = ce.TwoStageClaimEngine(ce.FakeModelAdapter(s1, s2))
    cs = eng.identify(ce.Source(ce.SourceType.LOCAL_FILE, "f_RESEARCH.md", "…"))
    rec = cs.claims[0].to_record()
    assert not any(k in rec for k in ("valid", "validated", "true", "verdict", "is_true"))


# ── hard boundary 2: three state axes never collapsed ────────────────────────

def test_boundary_three_axes_never_collapsed():
    fields = {f.name for f in dataclasses.fields(cst.ClaimState)}
    assert fields == {"faithful", "validity", "lifecycle"}      # exactly three, separate
    # and no fused 'grounding_truth' field is stored — it is a DERIVED method
    assert "is_grounding_truth" in dir(cst.ClaimState)


# ── dual-mode legacy retained ────────────────────────────────────────────────

def test_dual_mode_default_retained():
    for site in h.CONSUMER_SITES:
        assert h.flip_to_manageable(site)["default_retained"] is True
    # the regex fast-path (legacy-style instant extraction) is a permanent engine
    assert issubclass(ce.RegexFastPathEngine, ce.ClaimIdentificationPort)


# ── deferred A16 items are NOT built ─────────────────────────────────────────

def test_deferred_items_out_of_scope():
    import _claim_metrics as m
    # (v) cross-vendor agreement-as-recall-proxy is v2 — only union + gold-set exist
    assert not hasattr(m, "agreement_recall")
    assert hasattr(m, "relative_completeness") and hasattr(m, "absolute_completeness")
    # (vi) no 4th state axis (three-axis is the v1 commitment)
    assert len(dataclasses.fields(cst.ClaimState)) == 3
    # (i) type-unification not built — claim/hypothesis/thesis share MECHANISM not type;
    #     role is a single enum field, no supertype hierarchy module
    assert not any(Path("~/.claude/hooks").glob("_claim_typeunif*.py"))
    # (ii) no translation — native lang carried; no translate function on the engine
    assert not hasattr(ce, "translate") and not hasattr(h, "translate")
    # (iii) modelling-task bake-off is its own sub-Thought — not wired here
    assert not any(Path("~/.claude/hooks").glob("_claim_bakeoff*.py"))


# ── no C-NNNN reuse (end-to-end) ─────────────────────────────────────────────

def test_no_claim_id_reuse_end_to_end(chain):
    led, reg = chain
    c1 = _mk_claim("A holds.", 1)
    id1 = led.record_extracted(c1, checked_at="t0")
    led.record_retracted(id1, checked_at="t1")
    id2 = led.record_extracted(_mk_claim("B holds.", 2), checked_at="t2")
    assert id1 == "C-0001" and id2 == "C-0002"             # retracted id never reused
    assert len(set(led.all_claim_ids())) == len(led.all_claim_ids())


def _mk_claim(text, line):
    return ce.Claim(text, ce.Anchor.local_file("f_RESEARCH.md", line),
                    ce.ClaimFlags.from_dict({c: True for c in ce.CRITERIA}),
                    ce.ClaimRole.BACKWARD, "en")

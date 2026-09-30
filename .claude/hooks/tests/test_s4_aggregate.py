#!/usr/bin/env python3
"""R2 aggregator + U2 attestation-coverage meter tests (v2 Centralization, Slice S4).

Exercises `_claim_runs_aggregate.py` as a pytest module over a fixture set of
`.claim-runs.md` sidecars: manageable rows (-> attested-engine), default rows
(-> authorized-legacy), and a recordless deep assertion (-> flagged, via the reused
S3 `classify_provenance`). Confirms the U2 two-rate report (flagged-rate,
witnessed-coverage-rate), per-site/disposition/time slicing, and the A4 no-central-
store property (discovery reflects on-disk deletions immediately).

Run: python3 test_s4_aggregate.py   (or via pytest)
"""
import sys
from pathlib import Path

HOOKS = str(Path(__file__).resolve().parent.parent)
if HOOKS not in sys.path:
    sys.path.insert(0, HOOKS)

import _claim_runs_aggregate as agg  # noqa: E402
from _claim_attest import (  # noqa: E402
    DISPOSITION_ATTESTED_ENGINE,
    DISPOSITION_AUTHORIZED_LEGACY,
    DISPOSITION_FLAGGED,
)
from _claim_metrics import (  # noqa: E402
    append_run_record,
    fallback_record,
    manageable_record,
    sidecar_path_for,
)


def _fake_claim_set(n=2):
    class _FakeFlags:
        def all_pass(self):
            return True

    class _FakeClaim:
        flags = _FakeFlags()

    class _FakeClaimSet:
        def __init__(self, n):
            self.claims = [_FakeClaim() for _ in range(n)]

        def criteria_breakdown(self):
            return {"_total_claims": len(self.claims), "_refused": 0}

    return _FakeClaimSet(n)


def _build_fixture(root: Path):
    """Two witnessed rows (extract-knowledge manageable, plan Gate 0b2 default) + one
    unwitnessed artifact reserved for a recordless deep assertion (clarification)."""
    art1 = root / "book1_CLAIMS.md"
    sc1 = sidecar_path_for(str(art1))
    append_run_record(sc1, manageable_record(
        _fake_claim_set(), site="extract-knowledge", model="opus",
        checked_at="2026-07-19T10:00:00+00:00"))

    art2 = root / "topic_PLAN"
    sc2 = sidecar_path_for(str(art2))
    append_run_record(sc2, fallback_record(
        site="plan:0b2", reason="engine timeout", checked_at="2026-07-19T11:00:00+00:00"))

    art3 = root / "fabricated_RESEARCH.md"   # deliberately no sidecar written
    return art1, sc1, art2, sc2, art3


def test_discover_sidecars_finds_only_written_files(tmp_path):
    _build_fixture(tmp_path)
    sidecars = agg.discover_sidecars([], root=str(tmp_path))
    assert len(sidecars) == 2, sidecars
    assert all(str(p).endswith(agg.SIDECAR_SUFFIX) for p in sidecars)


def test_row_events_classify_off_mode_never_flagged(tmp_path):
    _build_fixture(tmp_path)
    sidecars = agg.discover_sidecars([], root=str(tmp_path))
    events = agg.collect_row_events(sidecars)
    assert len(events) == 2
    dispositions = {e["disposition"] for e in events}
    assert dispositions == {DISPOSITION_ATTESTED_ENGINE, DISPOSITION_AUTHORIZED_LEGACY}
    assert DISPOSITION_FLAGGED not in dispositions


def test_assertion_event_surfaces_flagged_for_recordless_deep_claim(tmp_path):
    _, _, _, _, art3 = _build_fixture(tmp_path)
    events = agg.collect_assertion_events(
        [(str(art3), "/clarification Step 2", "manageable", "deep")])
    assert len(events) == 1
    assert events[0]["disposition"] == DISPOSITION_FLAGGED
    assert events[0]["site"] == "/clarification Step 2"


def test_two_rate_coverage_report_and_slicing(tmp_path):
    _, _, _, _, art3 = _build_fixture(tmp_path)
    sidecars = agg.discover_sidecars([], root=str(tmp_path))
    events = agg.aggregate(sidecars, assertions=[
        (str(art3), "/clarification Step 2", "manageable", "deep")])
    report = agg.coverage_report(events)

    assert report["total_events"] == 3
    assert report["flagged_count"] == 1
    assert abs(report["flagged_rate"] - (1 / 3)) < 1e-9

    # Only /double-check Step 2c has zero events anywhere in this corpus.
    assert report["unwired_seams"] == ["/double-check Step 2c"], report["unwired_seams"]
    assert report["witnessed_coverage_rate"] == 0.75, report

    assert report["by_site"]["/extract-knowledge"] == {DISPOSITION_ATTESTED_ENGINE: 1}
    assert report["by_site"]["/plan Gate 0b2"] == {DISPOSITION_AUTHORIZED_LEGACY: 1}
    assert report["by_site"]["/clarification Step 2"] == {DISPOSITION_FLAGGED: 1}
    assert report["by_disposition"] == {
        DISPOSITION_ATTESTED_ENGINE: 1, DISPOSITION_AUTHORIZED_LEGACY: 1,
        DISPOSITION_FLAGGED: 1}

    text = agg.format_report(report)
    assert "double-check Step 2c" in text
    assert "flagged-rate" in text and "witnessed-coverage-rate" in text


def test_no_central_store_deletion_reflected_immediately(tmp_path):
    art1, sc1, art2, sc2, _art3 = _build_fixture(tmp_path)

    on_disk = sorted(p.name for p in tmp_path.iterdir())
    assert all(name.endswith(agg.SIDECAR_SUFFIX) for name in on_disk), on_disk

    sidecars_before = agg.discover_sidecars([], root=str(tmp_path))
    assert len(sidecars_before) == 2

    sc1.unlink()   # delete the extract-knowledge sidecar; NO index to go stale
    sidecars_after = agg.discover_sidecars([], root=str(tmp_path))
    assert len(sidecars_after) == 1, sidecars_after

    events_after = agg.aggregate(sidecars_after)
    assert len(events_after) == 1
    assert events_after[0]["site"] == "/plan Gate 0b2"


def test_empty_corpus_reports_zero_flagged_rate_no_false_alarm():
    report = agg.coverage_report([])
    assert report["total_events"] == 0
    assert report["flagged_rate"] == 0.0
    assert report["witnessed_coverage_rate"] == 0.0
    assert set(report["unwired_seams"]) == set(agg.KNOWN_DEEP_SITES)


if __name__ == "__main__":
    import tempfile

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
        else:
            with tempfile.TemporaryDirectory() as d:
                fn(Path(d))
        print(f"ok  {fn.__name__}")
    print(f"\nPASS — {len(fns)} tests")

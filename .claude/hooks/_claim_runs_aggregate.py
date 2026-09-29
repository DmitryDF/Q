#!/usr/bin/env python3
"""R2 read-only aggregator + U2 attestation-coverage meter (v2 Centralization, Slice S4).

Analysis-time aggregation over the co-located `<artifact>.claim-runs.md` sidecars the
four consumers already write (A4) — NEVER a central store. This module walks sidecars
found via explicit paths/globs and/or a root-directory scan, buckets each witnessed
event by site / disposition / time, and reports the U2 two-rate coverage meter:

  * flagged-rate (target 0) — the share of examined events that assert a deep/
    manageable engine run with no corresponding record (the S3 `_claim_attest`
    disposition, reused verbatim — never re-implemented).
  * witnessed-coverage-rate (target 100%) — of the KNOWN Deep-mode consumer sites
    (`_claim_harvest._SITE_MODE_DEFAULTS`, the MODE_DEEP entries — single source, so a
    fifth site added there extends this list automatically, Evolution Test), the share
    that have at least one witnessed (gate-emitted) event anywhere in the walked corpus.
    A site with zero events is named as the still-unwired seam.

Two kinds of witnessed event feed the report (both reuse S3's disposition vocabulary
— attested-engine / authorized-legacy / flagged — consistently):

  (1) sidecar ROWS — read via the reused `_claim_attest._iter_sidecar_rows` (RUNS_COLUMNS
      parsing, no re-implementation). A written row's own `mode` column IS its
      disposition: `manageable` -> attested-engine, `default` -> authorized-legacy. A
      written row is NEVER `flagged` by construction — `manageable_record` only exists
      because a real engine run produced it, and `fallback_record` only exists because a
      real reason-logged fallback happened (never a silent skip). This is the "classify
      rows directly from the mode column" path.

  (2) explicit ASSERTIONS — `(artifact_path, site, asserted_mode, asserted_thoroughness)`
      tuples a caller supplies for a claims-set whose OWN declared provenance needs
      cross-checking against its co-located sidecar (mirrors `_plan_claim_gate`'s S3
      call). This is the only path that can surface `flagged`: an assertion of
      manageable/deep with no backing sidecar row. Reuses `classify_provenance` verbatim
      (producer-never-verifies, separate actor, S3) — this is the "reuse classify_
      provenance to classify a[n asserting] artifact" path.

Denominator = gate-emitted (witnessed) events only — rows actually recorded, plus
assertions actually examined. Never a theoretical "every artifact anywhere" universe:
that would make coverage trivially 100% (nothing left to divide against) and is exactly
what the design rules out. Raw counts only; thresholds are deferred to instrumentation-v1.

Standalone / unit-testable: `python3 _claim_runs_aggregate.py --self-test`. Reuses the
shipped `_claim_attest` / `_claim_metrics` / `_claim_harvest` — no new engine, no central
store, no re-implementation of the row schema.
"""

from __future__ import annotations

import glob
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Sequence

from _claim_attest import (
    DISPOSITION_ATTESTED_ENGINE,
    DISPOSITION_AUTHORIZED_LEGACY,
    DISPOSITION_FLAGGED,
    _iter_sidecar_rows,
    classify_provenance,
)
from _claim_harvest import MODE_DEEP, _SITE_MODE_DEFAULTS, canonical_site
from _claim_metrics import sidecar_path_for

SIDECAR_SUFFIX = ".claim-runs.md"

# The known Deep-mode consumer sites — single source `_claim_harvest._SITE_MODE_DEFAULTS`
# (its MODE_DEEP entries). This IS the witnessed-coverage-rate denominator's universe; a
# fifth consumer site added there extends coverage tracking with no edit needed here.
KNOWN_DEEP_SITES = tuple(sorted(k for k, v in _SITE_MODE_DEFAULTS.items() if v == MODE_DEEP))


# ── discovery — read-only, on-demand, never a central store (A4) ─────────────

def discover_sidecars(paths: Sequence[str] = (), *, root: Optional[str] = None) -> list:
    """Resolve explicit paths / glob patterns AND/OR a root-directory recursive scan
    into a sorted, de-duplicated list of existing `.claim-runs.md` sidecar files.
    Purely a directory walk — no index is built or required; deleting a sidecar and
    re-scanning reflects the deletion immediately."""
    found = set()
    for p in paths:
        for hit in glob.glob(p, recursive=True):
            hp = Path(hit)
            if hp.is_file() and hp.name.endswith(SIDECAR_SUFFIX):
                found.add(hp.resolve())
    if root is not None:
        found.update(hp.resolve() for hp in Path(root).rglob(f"*{SIDECAR_SUFFIX}")
                     if hp.is_file())
    return sorted(found)


def _time_bucket(checked_at: str) -> str:
    """Day-granularity bucket from an ISO-8601 `checked_at`. Never raises — falls back to
    a best-effort prefix (advisory reporting, not a hard gate)."""
    s = str(checked_at or "").strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except Exception:  # noqa: BLE001 — advisory bucketing, never a hard failure
        return s[:10] if len(s) >= 10 else "unknown"


def _row_disposition(mode: str) -> str:
    """A written row's own disposition. `manageable` rows only exist because a real
    engine run produced them (`manageable_record`); `default` rows only exist because a
    real reason-logged fallback happened (`fallback_record`, never a silent skip). A
    written row is therefore NEVER `flagged` by construction — flagging requires an
    ASSERTION with no backing row (see `collect_assertion_events`)."""
    return DISPOSITION_ATTESTED_ENGINE if mode == "manageable" else DISPOSITION_AUTHORIZED_LEGACY


# ── event collection — the two witnessed-event sources ───────────────────────

def collect_row_events(sidecar_paths: Iterable) -> list:
    """Witnessed events from sidecar ROWS (reuses `_iter_sidecar_rows` — RUNS_COLUMNS
    parsing, no re-implementation)."""
    events = []
    for sidecar in sidecar_paths:
        for row in _iter_sidecar_rows(sidecar):
            mode = row.get("mode", "")
            events.append({
                "sidecar": str(sidecar),
                "site_raw": row.get("site", ""),
                "site": canonical_site(row.get("site", "")),
                "mode": mode,
                "disposition": _row_disposition(mode),
                "time_bucket": _time_bucket(row.get("checked_at", "")),
                "checked_at": row.get("checked_at", ""),
            })
    return events


def collect_assertion_events(assertions: Sequence) -> list:
    """Witnessed events from explicit claims-set ASSERTIONS — each a
    `(artifact_path, site, asserted_mode, asserted_thoroughness)` tuple — cross-checked
    against the artifact's OWN co-located sidecar via the reused, separate-actor
    `classify_provenance` (S3, producer-never-verifies). This is the only source that can
    surface `flagged`: an assertion of a deep/manageable engine run with no backing row."""
    events = []
    for artifact_path, site, asserted_mode, asserted_thoroughness in assertions:
        disp = classify_provenance(artifact_path, asserted_mode=asserted_mode,
                                   asserted_thoroughness=asserted_thoroughness)
        events.append({
            "sidecar": str(sidecar_path_for(artifact_path)),
            "site_raw": site,
            "site": canonical_site(site),
            "mode": asserted_mode,
            "disposition": disp["disposition"],
            "time_bucket": "n/a",
            "checked_at": "",
        })
    return events


def aggregate(sidecar_paths: Iterable = (), *, assertions: Sequence = ()) -> list:
    """All witnessed events (rows + assertions) as one flat list — the aggregator's
    single output shape, fed into `coverage_report`."""
    return collect_row_events(sidecar_paths) + collect_assertion_events(assertions)


# ── U2 two-rate coverage report ───────────────────────────────────────────────

def coverage_report(events: Sequence) -> dict:
    """The U2 two-rate report + site / disposition / time slicing.

    `flagged_rate` = flagged events / total events (0.0 on an empty corpus — nothing to
    flag is not a false alarm). `witnessed_coverage_rate` = of `KNOWN_DEEP_SITES`, the
    share with >=1 witnessed event anywhere in `events`; any 0-count site is named in
    `unwired_seams` as the still-unwired seam (U2)."""
    total = len(events)
    flagged = sum(1 for e in events if e["disposition"] == DISPOSITION_FLAGGED)
    flagged_rate = (flagged / total) if total else 0.0

    sites_seen = {e["site"] for e in events}
    unwired = [s for s in KNOWN_DEEP_SITES if s not in sites_seen]
    witnessed_coverage_rate = (
        (len(KNOWN_DEEP_SITES) - len(unwired)) / len(KNOWN_DEEP_SITES)
        if KNOWN_DEEP_SITES else 1.0
    )

    by_site = defaultdict(lambda: defaultdict(int))
    by_disposition = defaultdict(int)
    by_time = defaultdict(lambda: defaultdict(int))
    for e in events:
        by_site[e["site"]][e["disposition"]] += 1
        by_disposition[e["disposition"]] += 1
        by_time[e["time_bucket"]][e["disposition"]] += 1

    return {
        "total_events": total,
        "flagged_count": flagged,
        "flagged_rate": flagged_rate,
        "witnessed_coverage_rate": witnessed_coverage_rate,
        "unwired_seams": unwired,
        "by_site": {k: dict(v) for k, v in by_site.items()},
        "by_disposition": dict(by_disposition),
        "by_time": {k: dict(v) for k, v in by_time.items()},
    }


def format_report(report: dict) -> str:
    """Human-legible rendering of `coverage_report` — the operator-facing U2 surface."""
    lines = [
        f"flagged-rate: {report['flagged_rate']:.0%} "
        f"({report['flagged_count']}/{report['total_events']} witnessed events) [target 0]",
        f"witnessed-coverage-rate: {report['witnessed_coverage_rate']:.0%} "
        f"({len(KNOWN_DEEP_SITES) - len(report['unwired_seams'])}/{len(KNOWN_DEEP_SITES)} "
        f"known Deep-mode sites) [target 100%]",
    ]
    for seam in report["unwired_seams"]:
        lines.append(f"  below-100% — still-unwired seam: {seam}")
    lines.append("by site:")
    for site, disp_counts in sorted(report["by_site"].items()):
        lines.append(f"  {site}: {disp_counts}")
    lines.append("by disposition:")
    for disp, count in sorted(report["by_disposition"].items()):
        lines.append(f"  {disp}: {count}")
    lines.append("by time:")
    for bucket, disp_counts in sorted(report["by_time"].items()):
        lines.append(f"  {bucket}: {disp_counts}")
    return "\n".join(lines)


# ── U2 coverage CLI ────────────────────────────────────────────────────────────
# Row-events only (an artifact's own self-asserted thoroughness is consumer-specific
# domain knowledge — e.g. `_plan_claim_gate` reads it from the plan's own JSON payload —
# so the generic CLI reports what has actually been WITNESSED via a written row; a
# consumer wanting flagged-detection over its own assertions calls
# `collect_assertion_events` / `aggregate(..., assertions=...)` directly, in-process).

def _cli_coverage(argv) -> int:
    root = None
    paths = []
    i = 0
    while i < len(argv):
        if argv[i] == "--root" and i + 1 < len(argv):
            root = argv[i + 1]
            i += 2
        else:
            paths.append(argv[i])
            i += 1
    sidecars = discover_sidecars(paths, root=root)
    events = aggregate(sidecars)
    print(format_report(coverage_report(events)))
    return 0


def _main(argv) -> int:
    if argv and argv[0] == "coverage":
        return _cli_coverage(argv[1:])
    print("usage: _claim_runs_aggregate.py coverage [paths/globs...] [--root DIR] "
          "| --self-test", file=sys.stderr)
    return 2


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "coverage":
        sys.exit(_main(sys.argv[1:]))
    if "--self-test" not in sys.argv:
        sys.exit(_main(sys.argv[1:]))

    import tempfile

    from _claim_metrics import append_run_record, fallback_record, manageable_record

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

    with tempfile.TemporaryDirectory() as d:
        droot = Path(d)

        # Site 1 (/extract-knowledge) — a manageable row -> attested-engine.
        art1 = droot / "book1_CLAIMS.md"
        sc1 = sidecar_path_for(str(art1))
        rec1 = manageable_record(_fake_claim_set(), site="extract-knowledge",
                                 model="opus", checked_at="2026-07-19T10:00:00+00:00")
        append_run_record(sc1, rec1)

        # Site 2 (/plan Gate 0b2) — a default/legacy row -> authorized-legacy.
        art2 = droot / "topic_PLAN"   # /plan's virtual (.md-stripped) artifact convention
        sc2 = sidecar_path_for(str(art2))
        rec2 = fallback_record(site="plan:0b2", reason="engine timeout",
                               checked_at="2026-07-19T11:00:00+00:00")
        append_run_record(sc2, rec2)

        # Site 3 (/clarification Step 2) — a deep/manageable ASSERTION with NO backing
        # sidecar row at all -> the simulated flagged case (a recordless deep-assertion).
        art3 = droot / "fabricated_RESEARCH.md"
        assert not sidecar_path_for(str(art3)).exists()

        # (1) Discovery finds exactly the two written sidecars — no index required.
        sidecars = discover_sidecars([], root=str(droot))
        assert len(sidecars) == 2, sidecars

        # (2) Row events classify purely off the `mode` column — never flagged.
        row_events = collect_row_events(sidecars)
        assert len(row_events) == 2, row_events
        assert {e["disposition"] for e in row_events} == {
            DISPOSITION_ATTESTED_ENGINE, DISPOSITION_AUTHORIZED_LEGACY}

        # (3) Assertion event reuses classify_provenance and surfaces `flagged`.
        assertion_events = collect_assertion_events(
            [(str(art3), "/clarification Step 2", "manageable", "deep")])
        assert len(assertion_events) == 1
        assert assertion_events[0]["disposition"] == DISPOSITION_FLAGGED, assertion_events

        # (4) Combined aggregate + the U2 two-rate report.
        events = aggregate(sidecars, assertions=[
            (str(art3), "/clarification Step 2", "manageable", "deep")])
        assert len(events) == 3
        report = coverage_report(events)
        assert report["total_events"] == 3
        assert report["flagged_count"] == 1
        assert abs(report["flagged_rate"] - (1 / 3)) < 1e-9, report

        # witnessed-coverage: 3 of 4 known Deep-mode sites have >=1 event (extract-
        # knowledge + plan Gate 0b2 via rows, clarification via the flagged assertion);
        # /double-check Step 2c has NO event anywhere in this corpus -> still-unwired.
        assert report["unwired_seams"] == ["/double-check Step 2c"], report["unwired_seams"]
        assert report["witnessed_coverage_rate"] == 0.75, report

        # (5) Per-site / per-disposition slicing is consistent with classify_provenance.
        assert report["by_site"]["/extract-knowledge"] == {DISPOSITION_ATTESTED_ENGINE: 1}
        assert report["by_site"]["/plan Gate 0b2"] == {DISPOSITION_AUTHORIZED_LEGACY: 1}
        assert report["by_site"]["/clarification Step 2"] == {DISPOSITION_FLAGGED: 1}
        assert report["by_disposition"] == {
            DISPOSITION_ATTESTED_ENGINE: 1, DISPOSITION_AUTHORIZED_LEGACY: 1,
            DISPOSITION_FLAGGED: 1}

        text = format_report(report)
        assert "double-check Step 2c" in text
        assert "flagged-rate" in text and "witnessed-coverage-rate" in text

        # (6) NEVER a central store (A4): the only files on disk are the two sidecars we
        # wrote ourselves — no index/cache file was created by discovery or aggregation.
        # Deleting a sidecar and re-scanning reflects the deletion immediately, proving
        # there is no stale index to fall out of sync with the co-located files.
        on_disk = sorted(p.name for p in droot.iterdir())
        assert all(name.endswith(SIDECAR_SUFFIX) for name in on_disk), on_disk
        sc1.unlink()
        sidecars_after_delete = discover_sidecars([], root=str(droot))
        assert len(sidecars_after_delete) == 1, sidecars_after_delete
        events_after_delete = aggregate(sidecars_after_delete)
        assert len(events_after_delete) == 1
        assert events_after_delete[0]["site"] == "/plan Gate 0b2"

    print("SELF-TEST PASS: sidecar rows classify off `mode` (attested-engine / "
          "authorized-legacy, never flagged); an explicit recordless-deep assertion "
          "surfaces `flagged` via the reused classify_provenance; the U2 two-rate report "
          "computes flagged-rate and witnessed-coverage-rate with per-site/disposition/"
          "time slicing and names the still-unwired seam; no central store is built or "
          "required — deleting a sidecar and re-scanning reflects it immediately.")
    sys.exit(0)

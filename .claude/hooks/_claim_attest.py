#!/usr/bin/env python3
"""R3/R4 separate-actor provenance classifier (v2 Centralization, Slice S3) —
`classify_provenance()`.

`claim_identify()` (S1, `_claim_dispatch.py`) PRODUCES a claim set and best-effort
RECORDS a run to the co-located `<artifact>.claim-runs.md` sidecar. This module
is a DIFFERENT actor invoked AFTER that record exists (producer-never-verifies,
Rule 4/7) — it never runs inside `claim_identify()`.

`classify_provenance` reads ONLY the co-located sidecar (`_claim_metrics.sidecar_path_for`)
— never the claim payload, never the artifact body, never the ledger (A7,
Rev-4-corrected). It is:
  * content-blind  — never reads claim text.
  * artifact-blind — never reads the artifact's own body.
  * ledger-blind   — never reads `_claim_ledger` events.
  * set-level      — the disposition is a function of `(sidecar mode-rows,
                     asserted_mode, asserted_thoroughness)` only, never a
                     per-claim fold.

Three dispositions (A5):
  * attested-engine  — the assertion claims a deep/manageable engine run AND the
                       sidecar carries >=1 `mode=manageable` row.
  * authorized-legacy — the assertion is NOT a deep/manageable claim (any
                        default/legacy/regex-fast-path/non-deep assertion) —
                        NEVER flagged, NEVER warned, regardless of sidecar
                        content (U4).
  * flagged          — the assertion claims deep/manageable but the sidecar has
                       no manageable row (including an absent/empty sidecar).
                       ADVISORY ONLY — the caller (e.g. `_plan_claim_gate.py`)
                       decides how to surface it; this module never blocks
                       anything (Rule 6 / A6 / U3).

Re-affirm-on-edit is STRUCTURAL, not computed here: editing an artifact does not
delete its `.claim-runs.md` sidecar row, so calling `classify_provenance` again
after an edit returns the same disposition — there is nothing to "re-affirm" in
code because the content-blind verifier never inspected the edited payload in
the first place (A7).

Standalone / unit-testable: `python3 _claim_attest.py --self-test`. Reuses the
shipped `_claim_metrics` sidecar path + `RUNS_COLUMNS` row schema — no
re-implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from _claim_metrics import RUNS_COLUMNS, sidecar_path_for

DISPOSITION_ATTESTED_ENGINE = "attested-engine"
DISPOSITION_AUTHORIZED_LEGACY = "authorized-legacy"
DISPOSITION_FLAGGED = "flagged"


def _iter_sidecar_rows(sidecar_path) -> list:
    """Parse `.claim-runs.md` data rows into dicts keyed by `RUNS_COLUMNS` (reused
    verbatim — no re-implementation of the row schema). Malformed/short rows and
    the header are skipped rather than raising: this is a read-only advisory
    classifier, never a hard failure point."""
    p = Path(sidecar_path)
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.startswith("| ") or ln.startswith("| checked_at"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split(" | ")]
        if len(cells) != len(RUNS_COLUMNS):
            continue
        rows.append(dict(zip(RUNS_COLUMNS, cells)))
    return rows


def _is_deep_assertion(asserted_mode: Optional[str], asserted_thoroughness: Optional[str]) -> bool:
    """A 'manageable'/deep engine claim — the only assertion shape that can be
    flagged. Anything else (default/legacy/regex-fast-path/normal/unset) is an
    honest non-deep claim and is authorized-legacy by construction (U4)."""
    mode = str(asserted_mode or "").strip().lower()
    thoroughness = str(asserted_thoroughness or "").strip().lower()
    return mode == "manageable" and thoroughness == "deep"


def classify_provenance(artifact_path: str, *, asserted_mode: str = "manageable",
                        asserted_thoroughness: str = "deep") -> dict:
    """Sidecar-only, set-level provenance classification (A5/A7).

    `artifact_path` is the SOURCE string whose `sidecar_path_for()` gives the
    co-located `.claim-runs.md` this call reads — the caller is responsible for
    passing whatever string reproduces the sidecar path the producer actually
    wrote to (most sites: the artifact's own path; `/plan`'s Gate 0b2 seam uses
    a documented special-case path — see `_plan_claim_gate._plan_virtual_artifact_path`).

    Returns {"disposition": ..., "reason": <str>, "backing_row": <dict|None>}.
    Never raises on a missing/empty/malformed sidecar — that IS the `flagged`
    (or `authorized-legacy`) case, not an error."""
    sidecar = sidecar_path_for(artifact_path)
    rows = _iter_sidecar_rows(sidecar)
    manageable_rows = [r for r in rows if r.get("mode") == "manageable"]
    default_rows = [r for r in rows if r.get("mode") == "default"]

    if not _is_deep_assertion(asserted_mode, asserted_thoroughness):
        backing = default_rows[-1] if default_rows else (rows[-1] if rows else None)
        return {
            "disposition": DISPOSITION_AUTHORIZED_LEGACY,
            "reason": ("asserted mode/thoroughness is not a deep/manageable engine "
                       "claim — default/legacy/regex-fast-path is a sanctioned "
                       "retained mode, never flagged (U4)"),
            "backing_row": backing,
        }

    if manageable_rows:
        return {
            "disposition": DISPOSITION_ATTESTED_ENGINE,
            "reason": (f"sidecar {sidecar} has {len(manageable_rows)} manageable "
                       "run-record row(s) backing the asserted deep engine run"),
            "backing_row": manageable_rows[-1],
        }

    return {
        "disposition": DISPOSITION_FLAGGED,
        "reason": (f"claims-set asserts a manageable/deep engine run but the "
                   f"co-located sidecar ({sidecar}) has no corresponding "
                   "manageable run-record — advisory only, never a block"),
        "backing_row": None,
    }


# ── U1 provenance query CLI ───────────────────────────────────────────────────

def _cli_classify(argv) -> int:
    def _f(name, default=None):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default

    if not argv:
        print("usage: _claim_attest.py classify <artifact> [--asserted-mode M] "
              "[--asserted-thoroughness T]", file=sys.stderr)
        return 2
    artifact = argv[0]
    disposition = classify_provenance(
        artifact,
        asserted_mode=_f("--asserted-mode", "manageable"),
        asserted_thoroughness=_f("--asserted-thoroughness", "deep"),
    )
    backing = disposition["backing_row"]
    backing_text = (f"backed by run-record: {backing}" if backing is not None
                    else "no backing run-record found")
    print(f"[claim-attest] {artifact}: {disposition['disposition']} — "
          f"{disposition['reason']} ({backing_text})")
    return 0


def _main(argv) -> int:
    if argv and argv[0] == "classify":
        return _cli_classify(argv[1:])
    print("usage: _claim_attest.py classify <artifact> [--asserted-mode M] "
          "[--asserted-thoroughness T] | --self-test", file=sys.stderr)
    return 2


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--self-test" not in sys.argv:
        sys.exit(_main(sys.argv[1:]))

    import tempfile

    from _claim_metrics import append_run_record, fallback_record, manageable_record

    def _fake_claim_set(total=2):
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

        return _FakeClaimSet(total)

    # (1) attested-engine: a manageable row exists, assertion is deep/manageable.
    with tempfile.TemporaryDirectory() as d:
        artifact = str(Path(d) / "topic_RESEARCH.md")
        sidecar = sidecar_path_for(artifact)
        rec = manageable_record(_fake_claim_set(), site="clarification:step2",
                                model="sonnet", checked_at="t0")
        append_run_record(sidecar, rec)
        disp = classify_provenance(artifact, asserted_mode="manageable",
                                   asserted_thoroughness="deep")
        assert disp["disposition"] == DISPOSITION_ATTESTED_ENGINE, disp
        assert disp["backing_row"] is not None and disp["backing_row"]["mode"] == "manageable"

    # (2) authorized-legacy: a legacy/default assertion is NEVER flagged, even
    #     with an entirely absent sidecar (U4).
    with tempfile.TemporaryDirectory() as d:
        artifact = str(Path(d) / "topic_RESEARCH.md")   # no sidecar written at all
        disp = classify_provenance(artifact, asserted_mode="default",
                                   asserted_thoroughness="normal")
        assert disp["disposition"] == DISPOSITION_AUTHORIZED_LEGACY, disp
        assert disp["backing_row"] is None

    # (2b) authorized-legacy also wins when the sidecar DOES carry a default row
    #      and the assertion matches it (still never flagged).
    with tempfile.TemporaryDirectory() as d:
        artifact = str(Path(d) / "topic_RESEARCH.md")
        sidecar = sidecar_path_for(artifact)
        append_run_record(sidecar, fallback_record(site="clarification:step2",
                                                   reason="user opted out", checked_at="t0"))
        disp = classify_provenance(artifact, asserted_mode="default",
                                   asserted_thoroughness="n/a")
        assert disp["disposition"] == DISPOSITION_AUTHORIZED_LEGACY, disp
        assert disp["backing_row"] is not None and disp["backing_row"]["mode"] == "default"

    # (3) flagged: a deep/manageable assertion with NO manageable row (including a
    #     wholly absent sidecar, and a sidecar carrying only a default row) — never
    #     a block, advisory only.
    with tempfile.TemporaryDirectory() as d:
        artifact = str(Path(d) / "topic_RESEARCH.md")
        disp = classify_provenance(artifact, asserted_mode="manageable",
                                   asserted_thoroughness="deep")
        assert disp["disposition"] == DISPOSITION_FLAGGED, disp
        assert disp["backing_row"] is None

    with tempfile.TemporaryDirectory() as d:
        artifact = str(Path(d) / "topic_RESEARCH.md")
        sidecar = sidecar_path_for(artifact)
        append_run_record(sidecar, fallback_record(site="plan:0b2", reason="engine timeout",
                                                    checked_at="t0"))
        disp = classify_provenance(artifact, asserted_mode="manageable",
                                   asserted_thoroughness="deep")
        assert disp["disposition"] == DISPOSITION_FLAGGED, disp   # default row doesn't attest

    # (4) structural re-affirm-on-edit: the sidecar row is untouched by an "edit"
    #     to the artifact's OWN content (this classifier never reads the artifact
    #     body at all) — classifying twice, before and after simulating an edit,
    #     returns the identical attested-engine disposition (A7).
    with tempfile.TemporaryDirectory() as d:
        artifact_path = Path(d) / "topic_RESEARCH.md"
        artifact_path.write_text("original content\n", encoding="utf-8")
        sidecar = sidecar_path_for(str(artifact_path))
        rec = manageable_record(_fake_claim_set(), site="clarification:step2",
                                model="sonnet", checked_at="t0")
        append_run_record(sidecar, rec)

        before = classify_provenance(str(artifact_path), asserted_mode="manageable",
                                     asserted_thoroughness="deep")
        assert before["disposition"] == DISPOSITION_ATTESTED_ENGINE, before

        # Simulate a legitimate edit to the artifact — the sidecar row is NOT
        # deleted (structural — this is the whole point of A7).
        artifact_path.write_text("revised content after a legitimate edit\n", encoding="utf-8")

        after = classify_provenance(str(artifact_path), asserted_mode="manageable",
                                    asserted_thoroughness="deep")
        assert after["disposition"] == DISPOSITION_ATTESTED_ENGINE, after
        assert after["backing_row"] == before["backing_row"], (before, after)

    print("SELF-TEST PASS: attested-engine (manageable row backs a deep assertion); "
          "authorized-legacy (non-deep assertion never flagged, sidecar-content-"
          "independent, U4); flagged (deep assertion + no manageable row — advisory "
          "only, absent or default-only sidecar); structural re-affirm-on-edit (the "
          "sidecar row persists across an artifact edit — same disposition before/after, "
          "A7 content-blind/artifact-blind).")
    sys.exit(0)

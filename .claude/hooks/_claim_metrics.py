#!/usr/bin/env python3
"""Claim-Identification OMTM instrumentation + `.claim-runs.md` sidecar (Slice S3).

OMTM = **completeness of extraction**, DERIVED (not separately measured) from the
Stage-2 per-criterion flags each `identify()` run already produces (A3 / Metrics).
Two views:
  - relative completeness — cross-approach UNION (which approach recovered what
    share of everything anyone found); the bake-off's comparator (A16iii).
  - absolute completeness — vs a small human-decided GOLD set (S1's D≈258 denom);
    recovered / |gold|.

Transparency lives in a co-located `.claim-runs.md` sidecar (A10) so the claim set
itself stays clean; append-only, single-writer, flushed (Q-D carry-forward:
append-with-flush). Instrumentation-v1 is RAW — no thresholds (those arrive at v2).

Standalone / unit-testable (`python3 _claim_metrics.py --self-test`). Reuses the
`_claim_engine` domain; no Claude-hook imports.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from _claim_engine import CRITERIA, ClaimSet


# ── OMTM derivation ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RunMetrics:
    """One identify() run's raw metrics — DERIVED from the claim set's flags."""
    source_path: str
    approach: str          # identification approach label (bake-off dimension, A16iii)
    model: str
    thoroughness: str
    total_claims: int      # the completeness count (Metrics: "criteria-satisfying claims per run")
    refused: int           # ambiguity-gate drops (Stage-1)
    per_criterion: dict    # criterion -> count of claims satisfying it
    checked_at: str        # ISO-8601 UTC (passed in — no wall-clock here)

    def to_row(self) -> str:
        cols = [self.checked_at, self.approach, self.model, self.thoroughness,
                str(self.total_claims), str(self.refused)]
        cols += [str(self.per_criterion.get(c, 0)) for c in CRITERIA]
        return "| " + " | ".join(cols) + " |"


def derive_metrics(claim_set: ClaimSet, *, approach: str, model: str,
                   checked_at: str) -> RunMetrics:
    """Derive the OMTM from a ClaimSet's Stage-2 flags — no separate measurement."""
    bd = claim_set.criteria_breakdown()
    return RunMetrics(
        source_path=claim_set.source_path,
        approach=approach,
        model=model,
        thoroughness=claim_set.thoroughness.value,
        total_claims=bd["_total_claims"],
        refused=bd["_refused"],
        per_criterion={c: bd[c] for c in CRITERIA},
        checked_at=checked_at,
    )


# ── `.claim-runs.md` sidecar — append-only, single-writer, flushed ───────────

_SIDECAR_HEADER = (
    "# claim-identification runs (`.claim-runs.md`)\n\n"
    "| checked_at | approach | model | thoroughness | claims | refused | "
    + " | ".join(CRITERIA) + " |\n"
    "|" + "---|" * (6 + len(CRITERIA)) + "\n"
)


def sidecar_path_for(source_path: str) -> Path:
    """Co-located sidecar next to the source (keeps the claim set clean, A10)."""
    p = Path(source_path)
    return p.with_name(p.name + ".claim-runs.md")


def append_run(sidecar_path: Path, metrics: RunMetrics) -> None:
    """Append one run row. Single-writer append-with-flush (Q-D): the OS append is
    atomic for one line; fsync forces it to disk before returning."""
    header_needed = not sidecar_path.exists()
    with open(sidecar_path, "a", encoding="utf-8") as f:
        if header_needed:
            f.write(_SIDECAR_HEADER)
        f.write(metrics.to_row() + "\n")
        f.flush()
        os.fsync(f.fileno())


# ── cross-site run-record (`.claim-runs.md`) — A4/A5, shadow-benchmark substrate ─
# One line per engine run at a CONSUMER SITE (clarification / plan / extract-knowledge),
# recording either that the engine RAN (manageable — completeness derived from flags) or
# that a reason-logged DEFAULT fallback was taken (A5, the fact-check BYPASSED pattern —
# never a silent skip). This is SC2 (a durable "did it run" record off scrollback) and the
# substrate A6's shadow comparison reads (SC3). Distinct from the book-extraction bake-off
# rows above (approach/thoroughness); same append-only, single-writer, flushed discipline.

RUNS_COLUMNS = ("checked_at", "site", "mode", "model", "claims", "all6", "refused", "reason")

_RUNS_HEADER = (
    "# claim-engine runs (`.claim-runs.md`)\n\n"
    "| " + " | ".join(RUNS_COLUMNS) + " |\n"
    "|" + "---|" * len(RUNS_COLUMNS) + "\n"
)


def _all6_count(claim_set: ClaimSet) -> int:
    """Claims satisfying ALL six criteria — the 'complete' count at the point of use."""
    return sum(1 for cl in claim_set.claims if cl.flags.all_pass())


def manageable_record(claim_set: ClaimSet, *, site: str, model: str, checked_at: str) -> dict:
    """A run-record row for a manageable run — presence + completeness, not claim text
    (rule 10). `all6` = claims passing every criterion / total."""
    bd = claim_set.criteria_breakdown()
    total = bd["_total_claims"]
    return {"checked_at": checked_at, "site": site, "mode": "manageable", "model": model,
            "claims": str(total), "all6": f"{_all6_count(claim_set)}/{total}",
            "refused": str(bd["_refused"]), "reason": ""}


def fallback_record(*, site: str, reason: str, checked_at: str, model: str = "—") -> dict:
    """A reason-logged DEFAULT fallback row (A5 — the BYPASSED pattern). Raises if the
    reason is empty: a fallback is never a silent skip."""
    if not reason or not str(reason).strip():
        raise ValueError("fallback_record requires a non-empty reason (BYPASSED pattern — "
                         "a default fallback is never a silent skip)")
    return {"checked_at": checked_at, "site": site, "mode": "default", "model": model,
            "claims": "BYPASSED", "all6": "—", "refused": "—",
            "reason": " ".join(str(reason).split())}   # collapse whitespace → single row


def _runs_row(rec: dict) -> str:
    # guard the single-row invariant: newlines/pipes in free text would corrupt the table
    return "| " + " | ".join(
        str(rec.get(c, "")).replace("\n", " ").replace("|", "/") for c in RUNS_COLUMNS
    ) + " |"


def _row_signature(rec: dict) -> tuple:
    """Identity of a row ignoring the timestamp — for dedup of back-to-back identical
    writes (e.g. a plan gate that runs on BOTH the permission and stop paths)."""
    return tuple(str(rec.get(c, "")).replace("\n", " ").replace("|", "/")
                 for c in RUNS_COLUMNS if c != "checked_at")


def _last_row_signature(sidecar_path) -> tuple | None:
    p = Path(sidecar_path)
    if not p.exists():
        return None
    data = [ln for ln in p.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ") and not ln.startswith("| checked_at")]
    if not data:
        return None
    cells = [c.strip() for c in data[-1].strip().strip("|").split(" | ")]
    if len(cells) != len(RUNS_COLUMNS):
        return None
    return tuple(cells[i] for i, c in enumerate(RUNS_COLUMNS) if c != "checked_at")


def append_run_record(sidecar_path, record: dict, *, dedup_last: bool = False) -> None:
    """Append one cross-site run-record row (append-with-flush; header written once).
    `dedup_last=True` skips the write when the row (ignoring timestamp) equals the last
    data row — collapses the double-fire of a gate that runs on two hook paths."""
    if dedup_last and _last_row_signature(sidecar_path) == _row_signature(record):
        return
    p = Path(sidecar_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    header_needed = not p.exists()
    with open(p, "a", encoding="utf-8") as f:
        if header_needed:
            f.write(_RUNS_HEADER)
        f.write(_runs_row(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ── completeness comparators ─────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Identity key for a claim: lowercased, whitespace-collapsed, terminal-punct
    stripped. Cross-approach union / gold matching keys on this."""
    return re.sub(r"\s+", " ", text.strip().lower()).rstrip(".!?").strip()


def relative_completeness(runs_by_approach: dict) -> dict:
    """Cross-approach UNION (relative completeness, A3).

    runs_by_approach: {approach_label: ClaimSet}. Returns the union size and each
    approach's recovered/|union| — the bake-off comparator when there is no gold set.
    """
    keysets = {a: {_norm(cl.text) for cl in cs.claims}
               for a, cs in runs_by_approach.items()}
    union = set().union(*keysets.values()) if keysets else set()
    per_approach = {
        a: {"recovered": len(k), "of_union": len(union),
            "relative_completeness": (len(k) / len(union)) if union else 0.0}
        for a, k in keysets.items()
    }
    return {"union_size": len(union), "per_approach": per_approach}


def absolute_completeness(claim_set: ClaimSet, gold_texts) -> dict:
    """vs a human-decided GOLD set (S1 denominator D). recovered / |gold| — the
    gold-set hook (A3/U8). Missing = gold claims the run did not recover."""
    gold = {_norm(t) for t in gold_texts}
    found = {_norm(cl.text) for cl in claim_set.claims}
    recovered = gold & found
    return {
        "denominator": len(gold),
        "recovered": len(recovered),
        "recall": (len(recovered) / len(gold)) if gold else 0.0,
        "missing": sorted(gold - found),
    }


# ── CLI: `record` — append a cross-site run-record (A4/A5) ───────────────────
# Lets a non-Python consumer site (plan Gate 0b2 shell gate, extract-knowledge) emit
# the same run-record clarification's clar_advance writes directly. The Python sites
# import the functions above instead.
#   python3 _claim_metrics.py record --sidecar P --site S --mode manageable \
#           --claim-set <payload.json|-> [--model M] [--checked-at ISO]
#   python3 _claim_metrics.py record --sidecar P --site S --mode default \
#           --reason "<why>" [--checked-at ISO]

def _cli_record(argv) -> int:
    import json as _json
    import sys as _sys
    from datetime import datetime as _dt, timezone as _tz

    def _f(name, default=None):
        return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default

    sidecar = _f("--sidecar")
    site = _f("--site")
    mode = _f("--mode", "manageable")
    checked_at = _f("--checked-at") or _dt.now(_tz.utc).isoformat()
    if not sidecar or not site:
        print("record: --sidecar and --site are required", file=_sys.stderr)
        return 2
    try:
        if mode == "manageable":
            from _claim_persist import parse_claim_set
            src = _f("--claim-set")
            if not src:
                print("record: manageable mode requires --claim-set <payload.json|->", file=_sys.stderr)
                return 2
            text = _sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
            cs = parse_claim_set(_json.loads(text))
            rec = manageable_record(cs, site=site, model=_f("--model", "sonnet"), checked_at=checked_at)
        else:
            rec = fallback_record(site=site, reason=_f("--reason", ""), checked_at=checked_at,
                                  model=_f("--model", "—"))
        append_run_record(sidecar, rec)
    except Exception as e:  # noqa: BLE001 — CLI surfaces the error, non-zero exit
        print(f"record: {e}", file=_sys.stderr)
        return 1
    print(f"[claim-run-record] {rec['site']} {rec['mode']} → {sidecar}")
    return 0


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys
    import tempfile

    if len(sys.argv) > 1 and sys.argv[1] == "record":
        sys.exit(_cli_record(sys.argv[2:]))

    if "--self-test" not in sys.argv:
        print("usage: python3 _claim_metrics.py --self-test | record ...")
        sys.exit(1)

    import _claim_engine as ce

    def _flags():
        return {c: True for c in ce.CRITERIA}

    s1 = json.dumps({"candidates": [
        {"text": "A holds", "source_line": 1, "ambiguous": False, "reason": ""},
        {"text": "B holds", "source_line": 2, "ambiguous": False, "reason": ""},
        {"text": "maybe", "source_line": 3, "ambiguous": True, "reason": "vague"},
    ]})
    s2 = json.dumps({"claims": [
        {"text": "A holds.", "source_line": 1, "role": "backward", "flags": _flags()},
        {"text": "B holds.", "source_line": 2, "role": "backward", "flags": _flags()},
    ]})
    eng = ce.TwoStageClaimEngine(ce.FakeModelAdapter(s1, s2), model="sonnet")
    cs = eng.identify(ce.Source(ce.SourceType.LOCAL_FILE, "x_RESEARCH.md", "…"))

    m = derive_metrics(cs, approach="two_stage_v1", model="sonnet",
                       checked_at="2026-07-07T00:00:00Z")
    assert m.total_claims == 2 and m.refused == 1
    assert m.per_criterion["faithfulness"] == 2

    with tempfile.TemporaryDirectory() as d:
        sc = Path(d) / "x_RESEARCH.md.claim-runs.md"
        append_run(sc, m)
        append_run(sc, m)
        body = sc.read_text()
        assert body.count("two_stage_v1") == 2 and body.count("# claim-identification") == 1

    rel = relative_completeness({"two_stage_v1": cs, "regex_fastpath": cs})
    assert rel["union_size"] == 2
    assert rel["per_approach"]["two_stage_v1"]["relative_completeness"] == 1.0

    ab = absolute_completeness(cs, ["A holds", "B holds", "C never found"])
    assert ab["denominator"] == 3 and ab["recovered"] == 2
    assert ab["missing"] == ["c never found"]

    # cross-site run-record (A4/A5): manageable completeness row + BYPASSED fallback row
    with tempfile.TemporaryDirectory() as d:
        runs = Path(d) / "topic.claim-runs.md"
        mrec = manageable_record(cs, site="clarification:step2", model="sonnet",
                                 checked_at="2026-07-09T00:00:00Z")
        assert mrec["mode"] == "manageable" and mrec["claims"] == "2" and mrec["all6"] == "2/2"
        append_run_record(runs, mrec)
        frec = fallback_record(site="clarification:step2",
                               reason="user opted out\nof claim id", checked_at="t1")
        assert frec["claims"] == "BYPASSED" and "\n" not in frec["reason"]
        append_run_record(runs, frec)
        body = runs.read_text()
        assert body.count("# claim-engine runs") == 1          # header once
        assert "| manageable |" in body and "| BYPASSED |" in body
        assert body.count("clarification:step2") == 2
        # empty reason is refused (no silent skip)
        try:
            fallback_record(site="x", reason="   ", checked_at="t")
            raise AssertionError("expected ValueError on empty fallback reason")
        except ValueError:
            pass

    print("SELF-TEST PASS: OMTM derived from flags; sidecar append-with-flush "
          "(header once); relative union + absolute gold-set recall computed; "
          "cross-site run-record (manageable completeness + BYPASSED fallback; "
          "empty reason refused).")
    sys.exit(0)

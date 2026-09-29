#!/usr/bin/env python3
"""Moderate consolidation adapter (CF-4 / A3) — tame DEEP verbosity WITHOUT dropping facts.

The engine at DEEP thoroughness over-splits (~2-3x the by-hand default: benchmark
`Thoughts/refc-conformance-flip-*_RESEARCH.md`). This adapter collapses redundant /
duplicate splits back to a MINIMAL but COMPLETE claim set, invoked AFTER a Deep
production. It never runs for Automatic (no engine, nothing to consolidate).

Split of responsibility (code_first_architecture.md grading order code->model->human):
  * CODE (here, deterministic) — exact/normalized dedup (always safe) + the record + a
    SOFT, non-authoritative over-merge advisory that only prioritizes the spot-check.
  * MODEL (two scoped dispatches, producer-never-verifies) —
      1. the CONSOLIDATE pass (which finer splits are the same fact), dispatched to an
         actor that is NOT the DEEP producer;
      2. the COMPLETENESS spot-check (does the consolidated set still cover every distinct
         fact in the raw Deep set?), dispatched to a THIRD actor that is neither the DEEP
         producer nor the consolidator. This spot-check is the AUTHORITATIVE completeness
         guard — code cannot decide it.

Why the completeness guard is a MODEL check, not a code ratio: the benchmark disproves any
deterministic ratio/size floor. Cockburn 24->5 (a 79% cut, below its by-hand default of 13)
preserved completeness, while factcheck 13->5 (a 62% cut) dropped one distinct fact. A
smaller cut was UNSAFE and a larger cut was SAFE — so no ratio separates them; only reading
the merged content against the raw content can. Hence `over_merge_advisory` below is
explicitly SOFT (it prioritizes which runs to eyeball first), never a pass/fail.

Standalone / unit-testable: `python3 _claim_consolidate.py --self-test`. Reuses the
`_claim_engine` domain + `_claim_metrics` normalization/record infra; no Claude-hook imports.
"""

from __future__ import annotations

from _claim_engine import ClaimSet
from _claim_metrics import _norm, append_run_record


# ── deterministic dedup (the always-safe half) ───────────────────────────────

def dedup_exact(cs: ClaimSet) -> ClaimSet:
    """Remove normalized-duplicate claims (same `_claim_metrics._norm` key), keeping the
    first occurrence. ALWAYS safe — a normalized duplicate carries no distinct fact — so it
    never drops coverage. It does NOT do the semantic merge (that is the model's job); it
    only strips the literal repeats DEEP sometimes emits."""
    seen, kept = set(), []
    for c in cs.claims:
        k = _norm(c.text)
        if k in seen:
            continue
        seen.add(k)
        kept.append(c)
    return ClaimSet(source_path=cs.source_path, thoroughness=cs.thoroughness,
                    claims=kept, refused=list(cs.refused))


# ── soft over-merge advisory (NON-authoritative — prioritizes the spot-check) ─
# The benchmark proves a ratio cannot decide safety (cockburn 79% cut safe; factcheck 62%
# cut unsafe). So this only FLAGS extreme cuts to eyeball first; the model completeness
# spot-check (COMPLETENESS_CHECK_PROMPT) is the real guard. Editorial threshold, not a gate.
EXTREME_CUT_RATIO = 0.7   # a cut removing > 70% of deduped claims is worth a closer look


def over_merge_advisory(raw_n: int, consolidated_n: int) -> dict:
    """SOFT advisory only. `shrank` = consolidated <= raw (a real consolidation). `watch` =
    the cut removed > EXTREME_CUT_RATIO of the deduped raw — eyeball this run's completeness
    spot-check first. NOT a pass/fail: the authoritative guard is the model spot-check."""
    shrank = consolidated_n <= raw_n
    removed_ratio = (raw_n - consolidated_n) / raw_n if raw_n else 0.0
    return {"shrank": bool(shrank), "removed_ratio": round(removed_ratio, 3),
            "watch": bool(shrank and removed_ratio > EXTREME_CUT_RATIO),
            "raw": raw_n, "consolidated": consolidated_n}


# ── separate-model contracts (producer-never-verifies) ───────────────────────
# (1) the consolidator — dispatched to an actor that is NOT the DEEP producer.
CONSOLIDATE_PROMPT = """\
You are a MODERATE consolidation pass over a claim set produced at DEEP/exhaustive
thoroughness, which OVER-SPLITS (redundant, over-granular, duplicate claims). Collapse it
to a MINIMAL but COMPLETE set of distinct atomic claims.

Rules:
  * MERGE claims that express the same fact, or that were split from one compound fact,
    back into a single well-formed atomic claim.
  * REMOVE exact duplicates and claims fully entailed by another.
  * PRESERVE COMPLETENESS — do NOT drop any distinct fact. Every distinct piece of
    information in the input must still be recoverable from the output. A merge that loses
    a fact is a FAILURE; when unsure whether two claims are the same fact, keep them
    separate (stay moderate).
  * Keep each claim standalone (no dangling referents) and faithful to the source facts.

Return ONE JSON object, no prose, no fence, matching the input payload schema
(source_type / source_path / lang / thoroughness / claims[]).

Input claim set:
{claims_json}
"""

# (2) the completeness spot-check — the AUTHORITATIVE guard, dispatched to a THIRD actor
#     (neither the DEEP producer nor the consolidator).
COMPLETENESS_CHECK_PROMPT = """\
You are an independent completeness judge. You did NOT produce either claim set. A DEEP
claim set was consolidated (redundant splits merged). Decide, by MEANING (ignore wording,
granularity, count), whether the CONSOLIDATED set still covers EVERY distinct fact in the
RAW set.

Return ONE JSON object, no prose, no fence:
{ "dropped": ["<distinct fact in RAW not covered by CONSOLIDATED>", ...],
  "complete": true|false,
  "summary": "<one line: N distinct facts dropped, or 'complete'>" }
`complete` is true iff `dropped` is empty.

RAW (Deep) claims:
{raw_json}

CONSOLIDATED claims:
{consolidated_json}
"""


def completeness_ok(check_verdict: dict) -> bool:
    """Deterministic read of the model completeness spot-check: complete iff the judge
    reported zero dropped distinct facts. This is where the authoritative model verdict
    becomes a code boolean (grading order model->code-reads-it)."""
    dropped = check_verdict.get("dropped", []) if isinstance(check_verdict, dict) else []
    explicit = check_verdict.get("complete") if isinstance(check_verdict, dict) else None
    return (explicit is True) or (explicit is None and not dropped)


# ── record the consolidation to `.claim-runs.md` (reuses the runs schema) ─────

def consolidation_record(*, site: str, raw_n: int, consolidated_n: int, checked_at: str,
                         complete: bool | None = None, judge_summary: str = "") -> dict:
    """Map a consolidation onto the cross-site run-record schema (mode=`consolidate`):
    claims=`r<raw>/c<consolidated>`, all6=`cut<removed_ratio>`, refused=`cmpl<0|1|?>`
    (the model completeness verdict), reason=the judge's one-line summary."""
    adv = over_merge_advisory(raw_n, consolidated_n)
    cmpl = "?" if complete is None else str(int(bool(complete)))
    return {"checked_at": checked_at, "site": site, "mode": "consolidate", "model": "—",
            "claims": f"r{raw_n}/c{consolidated_n}", "all6": f"cut{adv['removed_ratio']}",
            "refused": f"cmpl{cmpl}", "reason": " ".join(str(judge_summary).split())}


def append_consolidation_record(sidecar_path, *, site, raw_n, consolidated_n, checked_at,
                                complete=None, judge_summary="") -> None:
    append_run_record(sidecar_path, consolidation_record(
        site=site, raw_n=raw_n, consolidated_n=consolidated_n, checked_at=checked_at,
        complete=complete, judge_summary=judge_summary))


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import tempfile
    from pathlib import Path

    if "--self-test" not in sys.argv:
        print("usage: python3 _claim_consolidate.py --self-test")
        sys.exit(1)

    import _claim_engine as ce

    def _cs(texts):
        claims = [ce.Claim(text=t, anchor=ce.Anchor.local_file("x.md", i + 1),
                           flags=ce.ClaimFlags(*(True,) * 6), role=ce.ClaimRole.BACKWARD,
                           lang="en") for i, t in enumerate(texts)]
        return ce.ClaimSet(source_path="x.md", thoroughness=ce.Thoroughness.DEEP,
                           claims=claims, refused=[])

    # dedup_exact: normalized duplicates collapse, distinct facts survive
    raw = _cs(["Apple fell into a death spiral.", "apple fell into a death spiral",  # dup (norm)
               "Gil Amelio was Apple's CEO.", "Mars has two moons."])
    dd = dedup_exact(raw)
    assert len(dd.claims) == 3, [c.text for c in dd.claims]
    assert [c.text for c in dd.claims][0] == "Apple fell into a death spiral."   # first kept

    # over_merge_advisory is SOFT: benchmark cockburn 24->5 (79% cut) IS flagged to watch,
    # but that does NOT mean unsafe — cockburn was complete. The flag only prioritizes.
    ck = over_merge_advisory(24, 5)
    assert ck["shrank"] and ck["watch"] is True and ck["removed_ratio"] > 0.7
    # jobs 29->11 (62% cut) not flagged as extreme; still needs the model spot-check
    assert over_merge_advisory(29, 11)["watch"] is False
    # not a consolidation if it grew
    assert over_merge_advisory(10, 12)["shrank"] is False

    # completeness_ok reads the AUTHORITATIVE model verdict
    assert completeness_ok({"dropped": [], "complete": True}) is True
    assert completeness_ok({"dropped": ["checker isolation property"], "complete": False}) is False
    assert completeness_ok({"dropped": []}) is True          # infer from empty dropped
    assert completeness_ok({"dropped": ["x"]}) is False

    # prompts carry their fill slots (contracts with the orchestrator)
    assert "{claims_json}" in CONSOLIDATE_PROMPT
    assert "{raw_json}" in COMPLETENESS_CHECK_PROMPT and "{consolidated_json}" in COMPLETENESS_CHECK_PROMPT

    # record maps onto the runs schema and appends cleanly (complete verdict carried)
    with tempfile.TemporaryDirectory() as d:
        sc = Path(d) / "topic.claim-runs.md"
        append_consolidation_record(sc, site="extract-knowledge", raw_n=24, consolidated_n=5,
                                    checked_at="t0", complete=True,
                                    judge_summary="complete — all raw distinct facts covered")
        body = sc.read_text()
        assert "| consolidate |" in body and "r24/c5" in body and "cmpl1" in body, body

    print("SELF-TEST PASS: exact dedup (normalized dups collapse, distinct survive); "
          "over_merge_advisory is SOFT (cockburn 24->5 flagged-to-watch but not unsafe); "
          "completeness_ok reads the authoritative model verdict; consolidate + "
          "completeness-check prompts carry their slots; record maps to runs schema.")
    sys.exit(0)

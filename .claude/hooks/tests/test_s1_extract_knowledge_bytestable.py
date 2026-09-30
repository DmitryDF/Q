#!/usr/bin/env python3
"""A9(a) byte-stable proof — S1 (`/extract-knowledge` Surface 1 threaded through
`claim_identify`).

The v2-Centralization plan (`refc-vs-extraction-consolidation-…_CENTRALIZATION_PLAN.md`,
Coherent Actions S1; design A9(a)) requires: the step-3 `_claim_persist --persist` output —
the `_CLAIMS.md` register bytes + the append-only ledger bytes + the `.claim-runs.md` row — is
**byte-identical** whether the consolidated claim-set is obtained the OLD way (hand-rolled
`identify → dedup_exact → consolidate`) or via the NEW `claim_identify(record=False, …)` path.
Same consolidated set in → same persisted bytes out.

`record=False` on the dispatcher is load-bearing: step 3's `--runs-sidecar` stays the SINGLE
`.claim-runs.md` writer, so there is no double-record. This proof also asserts exactly ONE
run-record data row is written (the no-double-record check).

Standalone: `python3 test_s1_extract_knowledge_bytestable.py` (prints the PASS line). Also runs
under pytest via the suite conftest (which pins the hooks dir on sys.path).
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_persist as cp                                    # noqa: E402
from _claim_consolidate import dedup_exact                     # noqa: E402
from _claim_dispatch import (InSessionStageProduction,         # noqa: E402
                             claim_identify, claimset_to_persist_payload)
from _claim_engine import (ClaimSet, OutputMode, Source,       # noqa: E402
                           SourceType, Thoroughness, in_session_bootstrap)

# A fixed, non-temp-dir source path so register/ledger/anchor bytes never embed a temp dir.
# `_claim_anchors.resolve` only FORMATS `path:line` (no file I/O), and the in-session adapter
# ignores `source.text`, so the file need not exist.
_SRC_PATH = "topic_RESEARCH.md"
_FLAGS = {c: True for c in ("atomicity", "verifiability", "decontextuality",
                            "minimality", "fluency", "faithfulness")}


def _stage_jsons():
    # Stage 1: A/B/C survive, one ambiguous refused.
    s1 = json.dumps({"candidates": [
        {"text": "A holds.", "source_line": 1, "ambiguous": False, "reason": ""},
        {"text": "B holds.", "source_line": 2, "ambiguous": False, "reason": ""},
        {"text": "C holds.", "source_line": 3, "ambiguous": False, "reason": ""},
        {"text": "maybe", "source_line": 4, "ambiguous": True, "reason": "vague"},
    ]})
    # Stage 2: a NORMALIZED-duplicate pair (A / "a holds") + B + C, so dedup_exact must
    # collapse the pair (4 → 3) BEFORE the consolidate callback runs.
    s2 = json.dumps({"claims": [
        {"text": "A holds.", "source_line": 1, "role": "backward", "flags": _FLAGS},
        {"text": "a holds", "source_line": 1, "role": "backward", "flags": _FLAGS},   # norm-dup
        {"text": "B holds.", "source_line": 2, "role": "backward", "flags": _FLAGS},
        {"text": "C holds.", "source_line": 3, "role": "backward", "flags": _FLAGS},
    ]})
    return s1, s2


def _source():
    return Source(SourceType.LOCAL_FILE, _SRC_PATH, "line 1: A. line 2: B. line 3: C.\n")


def _consolidate(cs: ClaimSet) -> ClaimSet:
    """Deterministic moderate-consolidation stand-in (both paths apply the SAME callback):
    keep the first two distinct claims — exercises the consolidate fold (3 → 2) on top of the
    deterministic dedup, so the proof covers dedup AND consolidate, not just identity."""
    return ClaimSet(source_path=cs.source_path, thoroughness=cs.thoroughness,
                    claims=cs.claims[:2], refused=list(cs.refused))


def _consolidated_old_way() -> ClaimSet:
    """Hand-rolled orchestration (the pre-S1 seam): identify → dedup_exact → consolidate."""
    s1, s2 = _stage_jsons()
    engine = in_session_bootstrap(s1, s2)
    cs = engine.identify(_source(), thoroughness=Thoroughness.DEEP,
                         output_mode=OutputMode.IN_MEMORY)
    cs = dedup_exact(cs)
    return _consolidate(cs)


def _consolidated_new_way() -> ClaimSet:
    """S1 shared dispatcher: ONE `claim_identify(record=False, consolidate=…)` folds
    identify → dedup_exact → consolidate (record=False → step 3 is the single sidecar writer)."""
    s1, s2 = _stage_jsons()
    return claim_identify(_source(), site="/extract-knowledge", thoroughness=Thoroughness.DEEP,
                          stage_production=InSessionStageProduction(s1, s2),
                          consolidate=_consolidate, record=False, checked_at="t0")


def _persist(payload: dict, d: str) -> None:
    """Step 3 — UNCHANGED `_claim_persist` document-mode persist + run-record. Pinned
    `checked_at='t0'` so the bytes are deterministic across the two temp dirs."""
    dd = Path(d)
    cp.run(payload, persist=True,
           register_path=dd / "topic_CLAIMS.md",
           ledger_path=dd / "topic.claims.ledger.jsonl",
           slug="topic", thought_status="DONE", checked_at="t0",
           runs_sidecar=dd / "topic_RESEARCH.md.claim-runs.md", site="extract-knowledge")


_PERSISTED = ("topic_CLAIMS.md", "topic.claims.ledger.jsonl",
              "topic_RESEARCH.md.claim-runs.md")


def _prove() -> None:
    old = _consolidated_old_way()
    new = _consolidated_new_way()

    # The two ways yield the SAME consolidated set (2 claims: A, B — dedup collapsed the
    # norm-dup, consolidate dropped C) and therefore the SAME `_claim_persist` payload.
    assert [c.text for c in old.claims] == ["A holds.", "B holds."], [c.text for c in old.claims]
    assert [c.text for c in new.claims] == [c.text for c in old.claims]
    payload_old = claimset_to_persist_payload(old)
    payload_new = claimset_to_persist_payload(new)
    assert payload_old == payload_new, "consolidated payloads diverge before persist"

    with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
        _persist(payload_old, da)
        _persist(payload_new, db)
        for fname in _PERSISTED:
            a = (Path(da) / fname).read_bytes()
            b = (Path(db) / fname).read_bytes()
            assert a == b, f"byte divergence in {fname}"

        # No-double-record: the `.claim-runs.md` has exactly ONE data row (header + 1),
        # written solely by step 3 (dispatcher ran record=False).
        rows = [ln for ln in (Path(da) / "topic_RESEARCH.md.claim-runs.md")
                .read_text(encoding="utf-8").splitlines()
                if ln.startswith("| ") and not ln.startswith("| checked_at")]
        assert len(rows) == 1, f"expected exactly one run-record row, got {len(rows)}: {rows}"
        assert "| manageable |" in rows[0] and "extract-knowledge" in rows[0], rows[0]


def test_s1_extract_knowledge_bytestable():
    _prove()


if __name__ == "__main__":
    _prove()
    print("S1 A9(a) PROOF PASS: _claim_persist register + ledger + .claim-runs.md bytes are "
          "byte-identical for the hand-rolled fold vs claim_identify(record=False); exactly one "
          "run-record row (no double-record); step 3 unchanged.")
    sys.exit(0)

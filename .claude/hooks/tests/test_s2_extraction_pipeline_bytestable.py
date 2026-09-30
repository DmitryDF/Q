#!/usr/bin/env python3
"""A9(a) byte-stable proof — S2 (`/extract-knowledge` Surface 2: the `extraction-pipeline.md`
Phase 2.5 claims pass, rerouted through `claim_identify`).

Before v2, Phase 2.5 emitted a free-form lowercase `_claims.md` with NO `parse_claim_set`, NO
ledger, and NO `.claim-runs.md` — the un-attested "self-labelled deep" surface the v2
Centralization increment kills (`…_CENTRALIZATION_PLAN.md` Coherent Actions S2; design A12).
After S2 it routes through the SAME attested seam Surface 1 uses:

    claim_identify(source=<extraction file>, site="/extract-knowledge", thoroughness=DEEP,
                   stage_production=<subagent>, output_mode=IN_MEMORY, record=False)
      → claimset_to_persist_payload
      → _claim_persist.py --persist --register <slug>_CLAIMS.md --ledger … --runs-sidecar …
                          --site extract-knowledge

This proof asserts two things the S2 cutover must hold:

  (1) BYTE-STABLE (reuses the S1 approach): the step-3 `_claim_persist --persist` output — the
      `_CLAIMS.md` register bytes + the append-only ledger bytes + the `.claim-runs.md` row — is
      **byte-identical** whether the consolidated set is obtained the OLD way (hand-rolled
      `identify → dedup_exact → consolidate`) or via the NEW `claim_identify(record=False, …)`
      path. The run-record row is `mode = manageable` + site `extract-knowledge`, and there is
      exactly ONE data row (record=False on the dispatcher → step 3 is the single writer;
      no double-record).

  (2) STRUCTURAL — LANDS-IN-THE-ATTESTED-PATH, NOT A PARALLEL FREE-FORM PRODUCER: the Phase-2.5
      output lands ONLY in the attested files (register + ledger + `.claim-runs.md`). The
      persist step writes exactly those three files and nothing else — in particular NO parallel
      free-form `_claims.md` producer file. (The free-form file was NEVER byte-stable — per-run
      free-form output — so only this structural assertion is possible, not a byte-diff of the
      old free-form output; per design A12 retirement-safety gate.)

The source here is the COMPLETED EXTRACTION FILE (`<slug>/<slug>.md`) — Phase 2.5's
`source=<extraction file>` — which is what distinguishes this proof from the S1 `_RESEARCH.md`
source. The proof drives the engine with `InSessionStageProduction` as a DETERMINISTIC test
double for the real `SubagentStageProduction`: the PERSISTED BYTES are stage-production-
independent (the execution-style axis does not change the ClaimSet the engine emits), so the
byte-stable + structural guarantees hold identically for the subagent path.

Standalone: `python3 test_s2_extraction_pipeline_bytestable.py` (prints the PASS line). Also runs
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

# The Phase-2.5 SOURCE is the completed extraction file. A fixed, non-temp-dir path so
# register/ledger/anchor bytes never embed a temp dir (the in-session adapter ignores
# `source.text`, and `_claim_anchors.resolve` only FORMATS `path:line`, so the file need
# not exist).
_SLUG = "book-slug"
_EXTRACTION_FILE = f"{_SLUG}/{_SLUG}.md"      # `<slug>/<slug>.md` — the completed extraction
_FLAGS = {c: True for c in ("atomicity", "verifiability", "decontextuality",
                            "minimality", "fluency", "faithfulness")}


def _stage_jsons():
    # Stage 1: A/B/C survive, one ambiguous refused (Claimify ambiguity-refusal gate).
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
    return Source(SourceType.LOCAL_FILE, _EXTRACTION_FILE, "line 1: A. line 2: B. line 3: C.\n")


def _consolidate(cs: ClaimSet) -> ClaimSet:
    """Deterministic moderate-consolidation stand-in (both paths apply the SAME callback):
    keep the first two distinct claims — exercises the consolidate fold (3 → 2) on top of the
    deterministic dedup, so the proof covers dedup AND consolidate, not just identity."""
    return ClaimSet(source_path=cs.source_path, thoroughness=cs.thoroughness,
                    claims=cs.claims[:2], refused=list(cs.refused))


def _consolidated_old_way() -> ClaimSet:
    """Hand-rolled orchestration (the pre-S2 free-form Phase 2.5 producer, minus the free-form
    write): identify → dedup_exact → consolidate."""
    s1, s2 = _stage_jsons()
    engine = in_session_bootstrap(s1, s2)
    cs = engine.identify(_source(), thoroughness=Thoroughness.DEEP,
                         output_mode=OutputMode.IN_MEMORY)
    cs = dedup_exact(cs)
    return _consolidate(cs)


def _consolidated_new_way() -> ClaimSet:
    """S2 shared dispatcher: ONE `claim_identify(record=False, consolidate=…)` folds
    identify → dedup_exact → consolidate. `InSessionStageProduction` is the deterministic test
    double for the real `SubagentStageProduction` (persisted bytes are stage-production-blind)."""
    s1, s2 = _stage_jsons()
    return claim_identify(_source(), site="/extract-knowledge", thoroughness=Thoroughness.DEEP,
                          stage_production=InSessionStageProduction(s1, s2),
                          consolidate=_consolidate, record=False, checked_at="t0")


# The three ATTESTED files step 3 writes for a Phase-2.5 book cutover. The `.claim-runs.md`
# sidecar is co-located with the extraction file's basename.
_REGISTER = f"{_SLUG}_CLAIMS.md"
_LEDGER = f"{_SLUG}.claims.ledger.jsonl"
_SIDECAR = f"{_SLUG}.md.claim-runs.md"
_PERSISTED = (_REGISTER, _LEDGER, _SIDECAR)


def _persist(payload: dict, d: str) -> None:
    """Step 3 — UNCHANGED `_claim_persist` document-mode persist + run-record, exactly as the
    rerouted Phase 2.5 invokes it. Pinned `checked_at='t0'` so bytes are deterministic across
    the two temp dirs; `--site extract-knowledge` enforces DEEP + tags the run `extract-knowledge`."""
    dd = Path(d)
    cp.run(payload, persist=True,
           register_path=dd / _REGISTER,
           ledger_path=dd / _LEDGER,
           slug=_SLUG, thought_status="DONE", checked_at="t0",
           runs_sidecar=dd / _SIDECAR, site="extract-knowledge")


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
    # The persisted set carries the DEEP thoroughness the Deep site requires (no NORMAL flip).
    assert payload_new["thoroughness"] == "deep", payload_new["thoroughness"]

    with tempfile.TemporaryDirectory() as da, tempfile.TemporaryDirectory() as db:
        _persist(payload_old, da)
        _persist(payload_new, db)

        # (1) BYTE-STABLE: register + ledger + .claim-runs.md byte-identical old-vs-new.
        for fname in _PERSISTED:
            a = (Path(da) / fname).read_bytes()
            b = (Path(db) / fname).read_bytes()
            assert a == b, f"byte divergence in {fname}"

        # No-double-record: the `.claim-runs.md` has exactly ONE data row (header + 1),
        # written solely by step 3 (dispatcher ran record=False), tagged manageable + the site.
        rows = [ln for ln in (Path(da) / _SIDECAR)
                .read_text(encoding="utf-8").splitlines()
                if ln.startswith("| ") and not ln.startswith("| checked_at")]
        assert len(rows) == 1, f"expected exactly one run-record row, got {len(rows)}: {rows}"
        assert "| manageable |" in rows[0] and "extract-knowledge" in rows[0], rows[0]

        # (2) STRUCTURAL — LANDS-IN-THE-ATTESTED-PATH, NOT A PARALLEL FREE-FORM PRODUCER.
        # The persist step writes the three attested files (plus at most the ledger's own
        # `.lock` sidecar) — and NOTHING resembling a parallel free-form `_claims.md`.
        written = {p.name for p in Path(db).iterdir()}
        assert set(_PERSISTED).issubset(written), (
            f"attested path is missing an expected file: wrote {sorted(written)}, "
            f"expected superset of {sorted(_PERSISTED)}")
        # Every extra file may only be the ledger's own lock artifact — never a claims producer.
        allowed = set(_PERSISTED) | {_LEDGER + ".lock"}
        assert written.issubset(allowed), (
            f"attested path wrote an unexpected file: {sorted(written - allowed)}; "
            f"a free-form _claims.md producer would show up here")
        # Belt-and-braces: no free-form argument-reconstruction file under any casing
        # (robust on a case-insensitive filesystem where a lowercase `_claims.md` would
        # collide with the uppercase `<slug>_CLAIMS.md` register).
        assert not any(n.lower() == "_claims.md" for n in written), (
            f"a free-form _claims.md was produced: {written}")


def test_s2_extraction_pipeline_bytestable():
    _prove()


if __name__ == "__main__":
    _prove()
    print("S2 A9(a) PROOF PASS: Phase-2.5 output lands in the ATTESTED path — _claim_persist "
          "register + ledger + .claim-runs.md bytes are byte-identical for the hand-rolled fold "
          "vs claim_identify(record=False, source=<extraction file>); the run-record is "
          "manageable + site=extract-knowledge with exactly one row (no double-record); the "
          "persist step writes ONLY the three attested files (NO parallel free-form _claims.md "
          "producer).")
    sys.exit(0)

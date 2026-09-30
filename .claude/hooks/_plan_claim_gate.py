#!/usr/bin/env python3
"""Plan Gate 0b2 — single-list Claims↔Gaps bidirectional cross-reference + per-claim
diagnosis-mapping presence check (/plan-scoped).

Replaces `_plan_claim_linkage.py` (retired 2026-07-18,
plan-claim-single-source-of-truth): there is no longer a second re-worded
`## Outcome Claims` table to reconcile against the engine json — the engine json
IS the single Outcome-Claims list. This module reads the claim ids from that one
list and enforces two /plan-scoped contracts:

  A3 — Claims↔Gaps bidirectional (mirrors the proven G# check in
       check-plan-gates.sh:171-186):
         * forward  — every claim id is referenced by >=1 gap-table Claim cell.
         * reverse  — every gap-table Claim ref resolves to a real claim id
                      (no phantom ref — the hole this closes).
  A6 — diagnosis-mapping (code-enforced): on the structured (success) path every
       claim must carry a NON-EMPTY `addresses_diagnosis` field AND a non-empty
       `id` (the id is what a gap Claim cell cites). Upgrades the per-claim
       diagnosis-mapping from operator-convention to a required field.

Two /plan claim-list forms:
  * SUCCESS  — a fenced ```json claim-set inside the `## Outcome Claims` section
               (between GATE0B:OUTCOME and GATE0B2:CLAIMS). ids +
               addresses_diagnosis come from the json. A6 applies.
  * FALLBACK — a `claim_fallback_reason:` line + a plain markdown Outcome-Claims
               table (the only claim list on the degraded escape path). ids come
               from the table `#` column. A6 does not apply (no structured claims).

Schema/thoroughness (DEEP) validation of the json stays in check-plan-gates.sh via
the shared `_claim_persist` seam — this module does NOT re-validate it. On a
malformed json fence it DEFERS (returns no errors) so the schema branch reports it
exactly once (no double-report, no phantom-gap false-positive).

v2 Centralization (Slice S3) adds an ADDITIVE `flags` channel alongside `errors`:
a separate-actor advisory attestation disposition (`_claim_attest.classify_provenance`,
sidecar-only, never inside `claim_identify()` — Rule 4/7) for the plan's own 0b2
claim-set. `errors` stays byte-stable (still what drives exit-1 / `MISSING`); `flags`
is informational only, populated ONLY when the caller passes `plan_path` (the CLI,
`check-plan-gates.sh`) and ONLY on a `flagged` disposition — an authorized-legacy or
attested-engine disposition (or the fallback/no-json path, which is never a deep
assertion) adds nothing (U4: legacy is never flagged; A6/U3: never a block).

Standalone / unit-testable:  python3 _plan_claim_gate.py --self-test
CLI:  python3 _plan_claim_gate.py <plan-file>   (exit 0 = no error / 1 = errors)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_OUTCOME_MARKER = "<!-- GATE0B:OUTCOME -->"
_CLAIMS_MARKER = "<!-- GATE0B2:CLAIMS -->"
_GAPS_MARKER = "<!-- GATE0C:GAPS -->"
_CID_RE = re.compile(r"\bC(\d+)\b")


def _lines_between(plan_text: str, start_marker: str, end_marker: str):
    """Return the lines strictly between two whole-line markers ([] if not found)."""
    lines = plan_text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        if ln.strip() == start_marker and start is None:
            start = i
        elif ln.strip() == end_marker and start is not None:
            end = i
            break
    if start is None or end is None or end <= start:
        return []
    return lines[start + 1:end]


def _extract_json_fence(section_lines):
    """(fence_present, parsed_dict_or_None) for the first ```json fence in a section.

    fence_present is True as soon as a ```json opener is seen — even if the body
    fails to parse — so the caller can DEFER to the schema branch on malformed json."""
    in_json = False
    body = []
    fence_present = False
    for ln in section_lines:
        s = ln.strip()
        if not in_json:
            if s == "```json":
                in_json = True
                fence_present = True
            continue
        if s == "```":
            break
        body.append(ln)
    if not fence_present:
        return (False, None)
    raw = "\n".join(body).strip()
    if not raw:
        return (True, None)
    try:
        return (True, json.loads(raw))
    except (ValueError, TypeError):
        return (True, None)


def _has_fallback_reason(section_lines) -> bool:
    return any(re.match(r"^claim_fallback_reason:\s*\S", ln) for ln in section_lines)


def _table_rows(lines):
    """Yield (header_cells, data_cells) for a markdown pipe table in `lines`.

    Header = the first pipe row; separator rows (|---|) are skipped; each later
    pipe row is a data row. Cells are stripped and leading/trailing empties dropped."""
    header = None
    for ln in lines:
        if not ln.lstrip().startswith("|"):
            # A blank/non-pipe line ends the current table run.
            if header is not None:
                header = None
            continue
        cells = [c.strip() for c in ln.split("|")]
        if cells and cells[0] == "":
            cells = cells[1:]
        if cells and cells[-1] == "":
            cells = cells[:-1]
        if not cells:
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) or c == "" for c in cells):
            continue
        if header is None:
            header = [c.lower() for c in cells]
            continue
        yield (header, cells)


def _markdown_claim_ids(section_lines):
    """C# ids from the `#` column of a markdown Outcome-Claims table (fallback path)."""
    ids = []
    for header, cells in _table_rows(section_lines):
        idx = header.index("#") if "#" in header else 0
        cell = cells[idx] if idx < len(cells) else (cells[0] if cells else "")
        m = _CID_RE.search(cell)
        if m:
            ids.append(f"C{m.group(1)}")
    return ids


def _gap_claim_refs(plan_text: str):
    """C# refs from the gap table's `Claim` column (region GATE0B2:CLAIMS→GATE0C:GAPS)."""
    region = _lines_between(plan_text, _CLAIMS_MARKER, _GAPS_MARKER)
    refs = set()
    for header, cells in _table_rows(region):
        idx = header.index("claim") if "claim" in header else (len(cells) - 1)
        cell = cells[idx] if idx < len(cells) else ""
        for m in _CID_RE.finditer(cell):
            refs.add(f"C{m.group(1)}")
    return refs


def _plan_virtual_artifact_path(plan_path: str) -> str:
    """The virtual source string whose `sidecar_path_for()` reproduces
    `check-plan-gates.sh`'s ACTUAL /plan sidecar convention —
    `${PLAN_FILE%.md}.claim-runs.md` — which strips the trailing `.md` before
    appending the suffix. This is NOT the generic `_claim_metrics.sidecar_path_for`
    convention (which would append onto the FULL `plan.md` name, giving
    `plan.md.claim-runs.md` — a file /plan never writes). Design A7 documents /plan
    as this special case; passing the `.md`-stripped path here keeps
    `_claim_attest.classify_provenance`'s generic `sidecar_path_for(artifact_path)`
    call landing on the real, existing sidecar."""
    p = str(plan_path)
    return p[: -len(".md")] if p.endswith(".md") else p


def _attestation_flags(claim_set, plan_path: str | None) -> list[str]:
    """Separate-actor advisory attestation (S3, plan-validation-engine-consumer v2).
    Sidecar-only; never touches `errors` or the exit code. Silent (returns []) when:
    `plan_path` is unset (keeps every existing byte-stable caller of `check(plan_text)`
    unaffected — this is opt-in via the CLI only); the classifier module can't be
    imported; the disposition isn't `flagged` (U4 — a legacy/default or non-deep
    assertion is never flagged); or classification itself raises (advisory-only —
    never break the gate on an attestation-read failure)."""
    if not plan_path:
        return []
    try:
        from _claim_attest import classify_provenance
    except ImportError:
        return []
    thoroughness = ""
    if isinstance(claim_set, dict):
        thoroughness = str(claim_set.get("thoroughness") or "").strip().lower()
    asserted_mode = "manageable" if thoroughness == "deep" else "default"
    try:
        disposition = classify_provenance(
            _plan_virtual_artifact_path(plan_path),
            asserted_mode=asserted_mode,
            asserted_thoroughness=thoroughness or "n/a",
        )
    except Exception:  # noqa: BLE001 — advisory-only; never break the gate on a read failure
        return []
    if disposition.get("disposition") == "flagged":
        reason = disposition.get(
            "reason", "claims assert a deep engine run with no matching .claim-runs.md record")
        return [f"ADVISORY — {reason}"]
    return []


def check(plan_text: str, *, plan_path: str | None = None) -> dict:
    """Return {"errors": [str, ...], "flags": [str, ...]}. Empty lists = pass / no
    advisory. `errors` is BYTE-STABLE (unchanged from before the `flags` channel was
    added) and is what drives exit-1 / `MISSING`. `flags` is additive-only (see the
    module docstring) and never affects `errors` or the exit code."""
    oc_section = _lines_between(plan_text, _OUTCOME_MARKER, _CLAIMS_MARKER)
    if not oc_section:
        return {"errors": [], "flags": []}

    fence_present, claim_set = _extract_json_fence(oc_section)
    errors: list[str] = []
    claim_ids: list[str] = []
    structured = False

    if fence_present:
        if claim_set is None:
            # Malformed/empty json — the schema branch in check-plan-gates.sh reports
            # it. Defer so we neither double-report nor false-positive on phantom gaps.
            return {"errors": [], "flags": []}
        structured = True
        raw_claims = claim_set.get("claims") if isinstance(claim_set, dict) else None
        if not isinstance(raw_claims, list):
            return {"errors": [], "flags": []}  # schema branch's job
        for i, c in enumerate(raw_claims):
            if not isinstance(c, dict):
                continue
            pos = i + 1
            cid = str(c.get("id") or "").strip()
            if not cid:
                errors.append(
                    f"Gate 0b2 (single list): claim #{pos} has no non-empty 'id'. Every "
                    "claim in the single structured Outcome-Claims list must carry an id "
                    "(e.g. \"id\": \"C1\") so a Gap can reference it.")
            else:
                claim_ids.append(cid)
            addr = str(c.get("addresses_diagnosis") or "").strip()
            if not addr:
                label = cid or f"#{pos}"
                errors.append(
                    f"Gate 0b2 (diagnosis-mapping): claim {label} has no non-empty "
                    "'addresses_diagnosis' field. Every Outcome Claim must map to the "
                    "aspect of the Diagnosis it resolves (code-enforced, /plan-scoped).")
    elif _has_fallback_reason(oc_section):
        # Degraded escape: ids come from the hand-authored markdown table (the only list).
        claim_ids = _markdown_claim_ids(oc_section)
    else:
        # Neither a json fence nor a fallback reason — the schema branch reports the
        # empty case; still try the markdown table so a legacy table isn't skipped.
        claim_ids = _markdown_claim_ids(oc_section)

    # A3 — bidirectional Claims↔Gaps (only when a Gap table is present).
    if _GAPS_MARKER in plan_text and _CLAIMS_MARKER in plan_text:
        gap_refs = _gap_claim_refs(plan_text)
        claim_id_set = set(claim_ids)
        # forward — every claim id has >=1 gap addressing it.
        for cid in claim_ids:
            if cid not in gap_refs:
                errors.append(
                    f"Gate 0 Coherence: Claim {cid} has no gap addressing it. Every "
                    "outcome claim must have at least one gap in the gap table.")
        # reverse — every gap Claim ref resolves to a real claim id (no phantom).
        # Skipped on the structured path only when id extraction itself failed (an
        # errors-already case), to avoid an avalanche of misleading phantom reports.
        if claim_id_set or not structured:
            for ref in sorted(gap_refs):
                if ref not in claim_id_set:
                    errors.append(
                        f"Gate 0 Coherence: Gap table references claim {ref} but no such "
                        "claim exists in the Outcome-Claims list.")

    flags = _attestation_flags(claim_set if structured else None, plan_path)
    return {"errors": errors, "flags": flags}


def _main(argv) -> int:
    if not argv:
        print("usage: _plan_claim_gate.py <plan-file>", file=sys.stderr)
        return 2
    plan_path = argv[0]
    plan_text = Path(plan_path).read_text(encoding="utf-8")
    result = check(plan_text, plan_path=plan_path)
    for e in result["errors"]:
        print(e)
    for f in result.get("flags", []):
        print(f, file=sys.stderr)
    return 1 if result["errors"] else 0


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--self-test" not in sys.argv:
        sys.exit(_main(sys.argv[1:]))

    def _flags():
        return {k: True for k in ("atomicity", "verifiability", "decontextuality",
                                  "minimality", "fluency", "faithfulness")}

    def _json_plan(claims, gap_refs):
        payload = {"source_type": "web", "source_path": "plan:0b2", "lang": "en",
                   "thoroughness": "deep", "claims": claims, "refused": []}
        gap_rows = "\n".join(
            f"| G{i+1} | gap | now | later | {r} |" for i, r in enumerate(gap_refs))
        return (
            f"{_OUTCOME_MARKER}\n## Outcome Claims\n\n"
            "```json\n" + json.dumps(payload, indent=2) + "\n```\n\n"
            "**Coverage:** ...\n"
            f"{_CLAIMS_MARKER}\n\n"
            "## Gap Analysis\n| # | Gap | Current | Desired | Claim |\n"
            "|---|-----|---------|---------|-------|\n"
            f"{gap_rows}\n{_GAPS_MARKER}\n")

    def _c(cid, addr="the problem"):
        d = {"text": f"claim {cid}", "id": cid, "role": "backward", "locator": "x",
             "flags": _flags()}
        if addr is not None:
            d["addresses_diagnosis"] = addr
        return d

    # (a) fully linked structured plan → PASS
    p = _json_plan([_c("C1"), _c("C2")], ["C1", "C2"])
    assert check(p)["errors"] == [], check(p)

    # (b) forward hole: C2 has no gap → block
    p = _json_plan([_c("C1"), _c("C2")], ["C1"])
    assert any("no gap addressing it" in e for e in check(p)["errors"]), check(p)

    # (c) reverse hole: gap cites phantom C9 → block (the new direction)
    p = _json_plan([_c("C1")], ["C1", "C9"])
    assert any("no such claim exists" in e for e in check(p)["errors"]), check(p)

    # (d) A6: claim missing addresses_diagnosis → block
    p = _json_plan([_c("C1"), _c("C2", addr=None)], ["C1", "C2"])
    assert any("diagnosis-mapping" in e for e in check(p)["errors"]), check(p)

    # (d') A6: empty addresses_diagnosis → block
    p = _json_plan([_c("C1", addr="   ")], ["C1"])
    assert any("diagnosis-mapping" in e for e in check(p)["errors"]), check(p)

    # (e) claim missing id → block
    bad = {"text": "no id", "role": "backward", "locator": "x",
           "flags": _flags(), "addresses_diagnosis": "x"}
    p = _json_plan([bad], [])
    assert any("has no non-empty 'id'" in e for e in check(p)["errors"]), check(p)

    # (f) malformed json fence → DEFER (no errors; schema branch reports it)
    p = (f"{_OUTCOME_MARKER}\n## Outcome Claims\n```json\n{{not valid json\n```\n"
         f"{_CLAIMS_MARKER}\n## Gap Analysis\n| # | Gap | Current | Desired | Claim |\n"
         f"|---|-----|---------|---------|-------|\n| G1 | g | n | l | C1 |\n{_GAPS_MARKER}\n")
    assert check(p)["errors"] == [], check(p)

    # (g) fallback path: markdown table ids, bidirectional still enforced, no A6
    fb = (f"{_OUTCOME_MARKER}\n## Outcome Claims\n"
          "| # | Claim | Addresses Diagnosis |\n|---|-------|---------------------|\n"
          "| C1 | claim text | the problem |\n\n"
          "claim_fallback_reason: user-acknowledged-skip: hand-decomposition\n\n"
          f"{_CLAIMS_MARKER}\n## Gap Analysis\n| # | Gap | Current | Desired | Claim |\n"
          "|---|-----|---------|---------|-------|\n| G1 | g | n | l | C1 |\n"
          f"{_GAPS_MARKER}\n")
    assert check(fb)["errors"] == [], check(fb)

    # (g') fallback path with a phantom gap ref → reverse block still fires
    fb2 = fb.replace("| G1 | g | n | l | C1 |", "| G1 | g | n | l | C1 |\n| G2 | g | n | l | C7 |")
    assert any("no such claim exists" in e for e in check(fb2)["errors"]), check(fb2)

    # ── S3: errors-path byte-stability + the new additive `flags` channel ───────
    import tempfile
    import _claim_metrics as _cm

    # (h) errors are BYTE-IDENTICAL whether or not plan_path is supplied — the
    # flags channel is purely additive and never perturbs the errors path.
    p_linked = _json_plan([_c("C1"), _c("C2")], ["C1", "C2"])
    with tempfile.TemporaryDirectory() as d:
        plan_path = str(Path(d) / "topic_PLAN.md")
        no_path_result = check(p_linked)
        with_path_result = check(p_linked, plan_path=plan_path)
        assert no_path_result["errors"] == with_path_result["errors"] == [], (
            no_path_result, with_path_result)
        assert no_path_result["flags"] == [], "flags must be [] with no plan_path"

    # (i) flagged: a deep/manageable claim-set with NO backing sidecar row → an
    # ADVISORY appears in `flags`, `errors` stays [] (never blocks, A6/U3).
    with tempfile.TemporaryDirectory() as d:
        plan_path = str(Path(d) / "topic_PLAN.md")
        result = check(p_linked, plan_path=plan_path)
        assert result["errors"] == [], result
        assert len(result["flags"]) == 1 and result["flags"][0].startswith("ADVISORY — "), result

    # (j) attested-engine: once the plan's co-located sidecar (the `.md`-stripped
    # convention `check-plan-gates.sh` uses) carries a manageable run-record, the
    # SAME claim-set is no longer flagged.
    with tempfile.TemporaryDirectory() as d:
        plan_path = str(Path(d) / "topic_PLAN.md")
        sidecar = Path(plan_path[: -len(".md")] + ".claim-runs.md")
        _cm.append_run_record(sidecar, {
            "checked_at": "t0", "site": "plan:0b2", "mode": "manageable",
            "model": "sonnet", "claims": "2", "all6": "2/2", "refused": "0", "reason": ""})
        result = check(p_linked, plan_path=plan_path)
        assert result["errors"] == [], result
        assert result["flags"] == [], result

    # (k) fallback path is a non-deep assertion → NEVER flagged (U4), even with a
    # wholly absent sidecar.
    with tempfile.TemporaryDirectory() as d:
        plan_path = str(Path(d) / "topic_PLAN.md")
        result = check(fb, plan_path=plan_path)
        assert result["errors"] == [], result
        assert result["flags"] == [], result

    print("SELF-TEST PASS: structured single-list linked; forward+reverse Claims↔Gaps "
          "(phantom gap blocks); addresses_diagnosis + id required on the structured path; "
          "malformed json defers; fallback markdown path linked with no A6; errors byte-"
          "stable regardless of plan_path; additive flags channel flags a recordless deep "
          "assertion (advisory only), clears once an engine run-record exists, and never "
          "fires on the fallback (default) path.")
    sys.exit(0)

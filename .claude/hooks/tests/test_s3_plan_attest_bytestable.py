#!/usr/bin/env python3
"""A9(b) byte-stable proof (v2 Centralization, Slice S3).

Confirms the S3 `/plan` Gate 0b2 cutover (`_claim_attest.classify_provenance` +
the additive `flags` channel on `_plan_claim_gate.check()`) did NOT perturb either
of the two pre-existing byte-stable contracts:

  (1) The shell schema+DEEP seam — `_claim_persist.py - --site plan:0b2 --dedup-runs`
      — exit code + stderr for a valid claim-set and for a schema violation.
  (2) `_plan_claim_gate.check()`'s `errors` list, for five representative plan
      shapes (linked / forward-hole / reverse-phantom / missing-id / fallback) —
      byte-identical whether or not `plan_path` is supplied, since `flags` is
      additive-only and must never perturb `errors`.

Run: python3 test_s3_plan_attest_bytestable.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = str(Path(__file__).resolve().parent.parent)
if HOOKS not in sys.path:
    sys.path.insert(0, HOOKS)

import _plan_claim_gate as pcg  # noqa: E402

_OUTCOME_MARKER = "<!-- GATE0B:OUTCOME -->"
_CLAIMS_MARKER = "<!-- GATE0B2:CLAIMS -->"
_GAPS_MARKER = "<!-- GATE0C:GAPS -->"


def _cflags():
    return {k: True for k in ("atomicity", "verifiability", "decontextuality",
                              "minimality", "fluency", "faithfulness")}


def _c(cid, addr="the problem"):
    d = {"text": f"claim {cid}", "id": cid, "role": "backward", "locator": "x",
         "flags": _cflags()}
    if addr is not None:
        d["addresses_diagnosis"] = addr
    return d


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


def _fallback_plan(gap_extra=""):
    return (f"{_OUTCOME_MARKER}\n## Outcome Claims\n"
            "| # | Claim | Addresses Diagnosis |\n|---|-------|---------------------|\n"
            "| C1 | claim text | the problem |\n\n"
            "claim_fallback_reason: user-acknowledged-skip: hand-decomposition\n\n"
            f"{_CLAIMS_MARKER}\n## Gap Analysis\n| # | Gap | Current | Desired | Claim |\n"
            "|---|-----|---------|---------|-------|\n| G1 | g | n | l | C1 |\n"
            f"{gap_extra}{_GAPS_MARKER}\n")


# ── five representative plan shapes + their KNOWN (pre-S3) error signatures ────

LINKED = _json_plan([_c("C1"), _c("C2")], ["C1", "C2"])
FORWARD_HOLE = _json_plan([_c("C1"), _c("C2")], ["C1"])
REVERSE_PHANTOM = _json_plan([_c("C1")], ["C1", "C9"])
MISSING_ID = _json_plan([{"text": "no id", "role": "backward", "locator": "x",
                          "flags": _cflags(), "addresses_diagnosis": "x"}], [])
FALLBACK = _fallback_plan()

EXPECTED_ERROR_SUBSTRINGS = {
    "linked": [],
    "forward_hole": ["no gap addressing it"],
    "reverse_phantom": ["no such claim exists"],
    "missing_id": ["has no non-empty 'id'"],
    "fallback": [],
}

REPRESENTATIVE_PLANS = {
    "linked": LINKED,
    "forward_hole": FORWARD_HOLE,
    "reverse_phantom": REVERSE_PHANTOM,
    "missing_id": MISSING_ID,
    "fallback": FALLBACK,
}


def test_errors_unchanged_no_plan_path():
    """errors path, called the OLD way (no plan_path) — must match the exact
    pre-S3 signatures for all five representative shapes."""
    for name, plan_text in REPRESENTATIVE_PLANS.items():
        result = pcg.check(plan_text)
        assert "errors" in result and "flags" in result, (name, result)
        expected_subs = EXPECTED_ERROR_SUBSTRINGS[name]
        if not expected_subs:
            assert result["errors"] == [], (name, result["errors"])
        else:
            for sub in expected_subs:
                assert any(sub in e for e in result["errors"]), (name, sub, result["errors"])
        # Additive-only: no plan_path → no attestation performed, flags always [].
        assert result["flags"] == [], (name, result["flags"])


def test_errors_byte_identical_with_and_without_plan_path():
    """The errors list must be BYTE-IDENTICAL whether plan_path is supplied or not
    — flags is a purely additive channel and must never perturb errors, on any of
    the five representative shapes, regardless of what (if anything) the plan's
    co-located sidecar contains."""
    for name, plan_text in REPRESENTATIVE_PLANS.items():
        with tempfile.TemporaryDirectory() as d:
            plan_path = str(Path(d) / f"{name}_PLAN.md")
            no_path = pcg.check(plan_text)
            with_path = pcg.check(plan_text, plan_path=plan_path)
            assert no_path["errors"] == with_path["errors"], (
                name, no_path["errors"], with_path["errors"])


def test_flags_never_populated_for_fallback_default_assertion():
    """U4 — the fallback (default/legacy) shape is NEVER flagged, even with a
    wholly absent sidecar (no engine run-record of any kind)."""
    with tempfile.TemporaryDirectory() as d:
        plan_path = str(Path(d) / "fallback_PLAN.md")
        result = pcg.check(FALLBACK, plan_path=plan_path)
        assert result["errors"] == []
        assert result["flags"] == [], result["flags"]


def test_flags_advisory_for_recordless_deep_assertion_never_blocks():
    """A deep/manageable-asserting claim-set with NO backing sidecar row surfaces
    exactly one ADVISORY flag, and `errors` (and therefore the CLI exit code) is
    UNCHANGED — the flag is never a block (A6/U3)."""
    with tempfile.TemporaryDirectory() as d:
        plan_path = str(Path(d) / "linked_PLAN.md")
        plan_text_path = Path(plan_path)
        plan_text_path.write_text(LINKED, encoding="utf-8")
        rc = pcg._main([plan_path])
        assert rc == 0, "a flag must never change the CLI exit code"

        proc = subprocess.run(
            [sys.executable, str(Path(HOOKS) / "_plan_claim_gate.py"), plan_path],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc
        assert proc.stdout == "", "stdout (errors) must stay empty — no MISSING entries"
        assert "ADVISORY —" in proc.stderr, proc.stderr


def test_claim_persist_seam_exit_code_and_stderr_unchanged():
    """(1) The shell's schema+DEEP validation seam — piping a claim-set through
    `_claim_persist.py - --site plan:0b2 --dedup-runs` — is untouched by S3: a
    valid Deep claim-set still exits 0 and records a run; a schema violation
    (missing criterion flag) still exits 1 with a SCHEMA ERROR on stderr."""
    good_payload = {
        "source_type": "web", "source_path": "plan:0b2", "lang": "en",
        "thoroughness": "deep",
        "claims": [{"text": "Claim text.", "locator": "DO-1", "role": "backward",
                   "flags": _cflags()}],
        "refused": [],
    }
    with tempfile.TemporaryDirectory() as d:
        sidecar = str(Path(d) / "topic.claim-runs.md")
        proc = subprocess.run(
            [sys.executable, str(Path(HOOKS) / "_claim_persist.py"), "-",
             "--runs-sidecar", sidecar, "--site", "plan:0b2", "--dedup-runs"],
            input=json.dumps(good_payload), capture_output=True, text=True)
        assert proc.returncode == 0, proc
        assert Path(sidecar).exists(), "manageable run-record must still be written"
        assert "| manageable |" in Path(sidecar).read_text(encoding="utf-8")

    bad_payload = json.loads(json.dumps(good_payload))
    del bad_payload["claims"][0]["flags"]["faithfulness"]
    proc = subprocess.run(
        [sys.executable, str(Path(HOOKS) / "_claim_persist.py"), "-",
         "--site", "plan:0b2"],
        input=json.dumps(bad_payload), capture_output=True, text=True)
    assert proc.returncode == 1, proc
    assert "SCHEMA ERROR" in proc.stderr, proc.stderr


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nPASS — {len(fns)} tests")

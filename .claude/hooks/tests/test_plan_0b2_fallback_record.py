#!/usr/bin/env python3
"""Guard test (A6) for plan Gate 0b2 fallback-record parity.

Verifies the /plan 0b2 fallback path leaves the same site-keyed `.claim-runs.md`
receipt the success path writes (tracking parity with clarification / extract-knowledge).
Covers the executable change: `_claim_persist.run_fallback` + the `--fallback-reason`
CLI mode that `check-plan-gates.sh`'s reason-logged-fallback branch invokes.

Run: python3 test_plan_0b2_fallback_record.py
"""
import sys
import tempfile
from pathlib import Path

HOOKS = str(Path(__file__).resolve().parent.parent)
if HOOKS not in sys.path:
    sys.path.insert(0, HOOKS)

import _claim_persist as cp  # noqa: E402


def _rows(sidecar: Path):
    return [ln for ln in sidecar.read_text(encoding="utf-8").splitlines()
            if ln.startswith("| ") and not ln.startswith("| checked_at")]


def test_run_fallback_writes_site_keyed_row():
    with tempfile.TemporaryDirectory() as d:
        sidecar = Path(d) / "topic.claim-runs.md"
        r = cp.run_fallback(site="plan:0b2", reason="engine timed out after 3 tries",
                            runs_sidecar=str(sidecar))
        assert r["recorded"] is True and r["mode"] == "fallback", r
        rows = _rows(sidecar)
        assert len(rows) == 1, rows
        # mode=default (BYPASSED pattern), site preserved, reason recorded
        assert "plan:0b2" in rows[0], rows[0]
        assert "default" in rows[0], rows[0]
        assert "BYPASSED" in rows[0], rows[0]
        assert "engine timed out" in rows[0], rows[0]


def test_cli_fallback_reason_mode_exit0_and_row():
    with tempfile.TemporaryDirectory() as d:
        sidecar = Path(d) / "topic.claim-runs.md"
        rc = cp._main(["--fallback-reason", "user opted out of Deep",
                       "--runs-sidecar", str(sidecar), "--site", "plan:0b2",
                       "--dedup-runs"])
        assert rc == 0, rc
        rows = _rows(sidecar)
        assert len(rows) == 1 and "user opted out" in rows[0], rows


def test_cli_fallback_requires_sidecar():
    rc = cp._main(["--fallback-reason", "x", "--site", "plan:0b2"])
    assert rc == 2, rc


def test_empty_reason_never_silent():
    with tempfile.TemporaryDirectory() as d:
        sidecar = Path(d) / "topic.claim-runs.md"
        # A blank reason must not produce a row (fallback is never a silent skip).
        r = cp.run_fallback(site="plan:0b2", reason="   ", runs_sidecar=str(sidecar))
        assert r["recorded"] is False, r
        assert not sidecar.exists() or _rows(sidecar) == [], "blank reason wrote a row"


def test_success_and_fallback_share_site_key():
    """Parity: a manageable (success) row and a fallback row carry the SAME site cell,
    so they aggregate together when filtered by site."""
    with tempfile.TemporaryDirectory() as d:
        sidecar = Path(d) / "topic.claim-runs.md"
        good = {
            "source_type": "local_file", "source_path": "plan.md", "lang": "en",
            "thoroughness": "deep",
            "claims": [{"text": "Users see convergence status.", "line": 1,
                        "role": "backward",
                        "flags": {c: True for c in ("atomicity", "verifiability",
                                  "decontextuality", "minimality", "fluency",
                                  "faithfulness")}}],
        }
        cp.run(good, runs_sidecar=str(sidecar), site="plan:0b2", dedup_runs=True)
        cp.run_fallback(site="plan:0b2", reason="second run engine failed",
                        runs_sidecar=str(sidecar), dedup_runs=True)
        rows = _rows(sidecar)
        assert len(rows) == 2, rows
        assert all("plan:0b2" in r for r in rows), rows
        assert "manageable" in rows[0] and "default" in rows[1], rows


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\nPASS — {len(fns)} tests")

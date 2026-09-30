"""Tests for the append-only claim ledger (Slice S4)."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce  # noqa: E402
import _claim_ledger as cl  # noqa: E402


def _flags():
    return {c: True for c in ce.CRITERIA}


def _claim(text, line=1, lang="en"):
    return ce.Claim(text=text, anchor=ce.Anchor.local_file("x_RESEARCH.md", line),
                    flags=ce.ClaimFlags.from_dict(_flags()),
                    role=ce.ClaimRole.BACKWARD, lang=lang)


def _claimset(texts):
    claims = [{"text": t, "source_line": i + 1, "role": "backward", "flags": _flags()}
              for i, t in enumerate(texts)]
    cands = [{"text": t, "source_line": i + 1, "ambiguous": False, "reason": ""}
             for i, t in enumerate(texts)]
    eng = ce.TwoStageClaimEngine(ce.FakeModelAdapter(
        json.dumps({"candidates": cands}), json.dumps({"claims": claims})))
    return eng.identify(ce.Source(ce.SourceType.LOCAL_FILE, "x_RESEARCH.md", "…"))


@pytest.fixture
def ledger(tmp_path):
    return cl.ClaimLedger(tmp_path / "claims.ledger.jsonl")


# ── extract ──────────────────────────────────────────────────────────────────

def test_record_run_batched_ids_sequential(ledger):
    ids = ledger.record_run(_claimset(["A holds.", "B holds."]), checked_at="t0")
    assert ids == ["C-0001", "C-0002"]
    st = ledger.current_state("C-0001")
    assert st["version"] == 1 and st["status"] == "active" and st["parent_version"] is None
    assert st["last_event"] == "extracted"


def test_record_run_is_one_append(ledger, tmp_path):
    ledger.record_run(_claimset(["A holds.", "B holds.", "C holds."]), checked_at="t0")
    # 3 events, each on its own JSONL line
    lines = (tmp_path / "claims.ledger.jsonl").read_text().splitlines()
    assert len([l for l in lines if l.strip()]) == 3


# ── revise (append-only supersede) ───────────────────────────────────────────

def test_revise_creates_new_version_with_parent(ledger):
    ledger.record_run(_claimset(["A holds."]), checked_at="t0")
    v = ledger.record_revised("C-0001", _claim("A holds firmly."), checked_at="t1")
    assert v == 2
    st = ledger.current_state("C-0001")
    assert st["version"] == 2 and st["parent_version"] == 1
    assert st["text"] == "A holds firmly." and st["status"] == "active"


def test_prior_line_untouched_history_intrinsic(ledger, tmp_path):
    ledger.record_run(_claimset(["A holds."]), checked_at="t0")
    ledger.record_revised("C-0001", _claim("A holds firmly."), checked_at="t1")
    events = [json.loads(l) for l in
              (tmp_path / "claims.ledger.jsonl").read_text().splitlines() if l.strip()]
    # both versions are on disk; v1 line is unchanged (append-only, history intrinsic)
    v1 = [e for e in events if e["version"] == 1][0]
    assert v1["event"] == "extracted" and v1["text"] == "A holds."
    assert len(events) == 2


# ── verified (consumer event, not a lifecycle change) ────────────────────────

def test_verified_event_does_not_change_status(ledger):
    ledger.record_run(_claimset(["A holds."]), checked_at="t0")
    ledger.record_verified("C-0001", verdict="PASS", checked_at="t1")
    st = ledger.current_state("C-0001")
    assert st["status"] == "active"        # verification is not a lifecycle transition (A9)
    assert st["last_event"] == "verified"


# ── retract + never-reuse ────────────────────────────────────────────────────

def test_retract_then_no_id_reuse(ledger):
    ledger.record_run(_claimset(["A holds.", "B holds."]), checked_at="t0")
    ledger.record_retracted("C-0002", checked_at="t1", reason="source corrected")
    assert ledger.current_state("C-0002")["status"] == "retracted"
    # next extract skips the retracted id — never reused
    new_id = ledger.record_extracted(_claim("C holds.", 3), checked_at="t2")
    assert new_id == "C-0003"


def test_ids_never_reused_across_reopen(tmp_path):
    p = tmp_path / "claims.ledger.jsonl"
    cl.ClaimLedger(p).record_run(_claimset(["A holds."]), checked_at="t0")
    # a fresh ledger object over the same file continues the id sequence
    new_id = cl.ClaimLedger(p).record_extracted(_claim("B holds.", 2), checked_at="t1")
    assert new_id == "C-0002"


# ── guards ───────────────────────────────────────────────────────────────────

def test_revise_retracted_refused(ledger):
    ledger.record_run(_claimset(["A holds."]), checked_at="t0")
    ledger.record_retracted("C-0001", checked_at="t1")
    with pytest.raises(cl.LedgerError):
        ledger.record_revised("C-0001", _claim("A holds firmly."), checked_at="t2")


def test_revise_unknown_refused(ledger):
    with pytest.raises(cl.LedgerError):
        ledger.record_revised("C-9999", _claim("x"), checked_at="t0")


def test_native_language_preserved_in_ledger(ledger):
    ru = "Роутинг по приложению на iOS."
    ledger.record_run(_claimset([ru]), checked_at="t0")
    assert ledger.current_state("C-0001")["text"] == ru


def test_concurrent_writers_no_id_collision(tmp_path):
    """/challenge finding + S2 fix: without the flock this race reuses C-NNNN; with it,
    concurrent writers each get a unique, never-reused id. The read-modify-write releases
    the GIL on file I/O, so threads genuinely interleave here."""
    import threading

    led = cl.ClaimLedger(tmp_path / "l.jsonl")
    errors = []
    barrier = threading.Barrier(20)

    def worker(i):
        try:
            barrier.wait()                         # maximize interleaving
            led.record_extracted(_claim(f"claim {i}", i + 1), checked_at="t")
        except Exception as e:                     # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    ids = led.all_claim_ids()
    assert len(ids) == 20
    assert len(set(ids)) == 20                      # zero collisions / reuse
    assert sorted(ids) == [f"C-{n:04d}" for n in range(1, 21)]

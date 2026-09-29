#!/usr/bin/env python3
"""Append-only claim ledger (Slice S4) — reuses the thesis_log / H-ID pattern.

The claim store. Stable, never-reused `claim_id` (C-NNNN); each claim has a
`version`, a `status ∈ {active, superseded, retracted}`, and a `parent_version`;
mutations are recorded as append-only EVENTS (extracted / revised / retracted /
verified). History is intrinsic — current state is DERIVED by folding events, never
by mutating a prior line (Event Sourcing / ledger tables, verified research A5).

Discipline (A13 + Q-D carry-forward):
  - code-appended, deterministic — **no model in the write path**;
  - batched one-write-per-run (all of a run's extracted events in a single append);
  - single-writer append-with-flush (fsync) — the OS append of whole lines is atomic;
  - `verified` events come from the Validation *consumer*, not this engine (the
    engine does not validate — A9); the ledger only records the consumer's verdict.

Storage: JSONL, one event per line. `claim_id` allocation = max existing id + 1, so
a retracted id is **never** reallocated (append-only never deletes).

Standalone / unit-testable (`python3 _claim_ledger.py --self-test`).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path

from _claim_engine import Claim


_EVENTS = ("extracted", "revised", "retracted", "verified")
_ID_RE = re.compile(r"^C-(\d+)$")


def _fmt_id(n: int) -> str:
    return f"C-{n:04d}"


class LedgerError(ValueError):
    pass


class ClaimLedger:
    """Append-only JSONL ledger. One instance = one ledger file (single writer)."""

    def __init__(self, path):
        self._path = Path(path)

    # -- reads (fold events) -------------------------------------------------
    def _events(self):
        if not self._path.exists():
            return []
        out = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def _max_id_num(self) -> int:
        n = 0
        for e in self._events():
            m = _ID_RE.match(e.get("claim_id", ""))
            if m:
                n = max(n, int(m.group(1)))
        return n

    def current_state(self, claim_id: str):
        """Fold events → the claim's current {version, status, text, ...}; None if absent."""
        state = None
        for e in self._events():
            if e.get("claim_id") == claim_id:
                state = e            # latest event wins (append order)
        if state is None:
            return None
        return {"claim_id": claim_id, "version": state["version"],
                "status": state["status"], "text": state.get("text"),
                "parent_version": state.get("parent_version"),
                "last_event": state["event"]}

    def all_claim_ids(self):
        seen = []
        for e in self._events():
            cid = e.get("claim_id")
            if cid and cid not in seen:
                seen.append(cid)
        return seen

    # -- writes (append-only, flushed) --------------------------------------
    def _append(self, events: list) -> None:
        """Append a batch of event dicts in ONE write (batched one-write-per-run)."""
        if not events:
            return
        with open(self._path, "a", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    @contextmanager
    def _id_lock(self):
        """Serialize the id read-modify-write across processes (S2 fix, /challenge
        finding). An exclusive `flock` on a sidecar lockfile spans `_max_id_num()`
        (read) → `_append` (write) so two concurrent writers cannot allocate the same
        C-NNNN. Advisory but sufficient for cooperating writers (the only writers)."""
        lock_path = self._path.with_name(self._path.name + ".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _event(self, claim: Claim, *, claim_id, version, status, parent_version,
               event, checked_at) -> dict:
        rec = claim.to_record()
        rec.pop("claim_id", None)     # id is authoritative from the ledger, not the Claim
        return {"claim_id": claim_id, "version": version, "event": event,
                "status": status, "parent_version": parent_version,
                "checked_at": checked_at, **rec}

    def record_run(self, claim_set, *, checked_at) -> list:
        """Extract a whole run: allocate a fresh id per claim, append as ONE batch.
        Returns the assigned claim_ids in order."""
        with self._id_lock():
            n = self._max_id_num()
            events, ids = [], []
            for claim in claim_set.claims:
                n += 1
                cid = _fmt_id(n)
                ids.append(cid)
                events.append(self._event(claim, claim_id=cid, version=1, status="active",
                                          parent_version=None, event="extracted",
                                          checked_at=checked_at))
            self._append(events)
        return ids

    def record_extracted(self, claim, *, checked_at) -> str:
        with self._id_lock():
            cid = _fmt_id(self._max_id_num() + 1)
            self._append([self._event(claim, claim_id=cid, version=1, status="active",
                                      parent_version=None, event="extracted",
                                      checked_at=checked_at)])
        return cid

    def record_revised(self, claim_id, new_claim, *, checked_at) -> int:
        """New version supersedes the prior (append-only: prior line untouched; the
        fold makes the new event current). Returns the new version number."""
        with self._id_lock():
            cur = self.current_state(claim_id)
            if cur is None:
                raise LedgerError(f"cannot revise unknown claim {claim_id}")
            if cur["status"] == "retracted":
                raise LedgerError(f"cannot revise retracted claim {claim_id}")
            new_version = cur["version"] + 1
            self._append([self._event(new_claim, claim_id=claim_id, version=new_version,
                                      status="active", parent_version=cur["version"],
                                      event="revised", checked_at=checked_at)])
        return new_version

    def record_retracted(self, claim_id, *, checked_at, reason="") -> None:
        with self._id_lock():
            cur = self.current_state(claim_id)
            if cur is None:
                raise LedgerError(f"cannot retract unknown claim {claim_id}")
            self._append([{"claim_id": claim_id, "version": cur["version"],
                           "event": "retracted", "status": "retracted",
                           "parent_version": cur["parent_version"],
                           "reason": reason, "checked_at": checked_at,
                           "text": cur["text"]}])

    def record_verified(self, claim_id, *, verdict, checked_at) -> None:
        """A CONSUMER (Validation engine) records its verdict here. The claim's
        status is unchanged (verification is not a lifecycle transition — A9); the
        event is an audit entry the three state axes (S6) read."""
        with self._id_lock():
            cur = self.current_state(claim_id)
            if cur is None:
                raise LedgerError(f"cannot record verification for unknown claim {claim_id}")
            self._append([{"claim_id": claim_id, "version": cur["version"],
                           "event": "verified", "status": cur["status"],
                           "parent_version": cur["parent_version"],
                           "verdict": verdict, "checked_at": checked_at,
                           "text": cur["text"]}])


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import tempfile

    if "--self-test" not in sys.argv:
        print("usage: python3 _claim_ledger.py --self-test")
        sys.exit(1)

    import _claim_engine as ce

    def _flags():
        return {c: True for c in ce.CRITERIA}

    def _claim(text, line):
        return ce.Claim(text=text, anchor=ce.Anchor.local_file("x_RESEARCH.md", line),
                        flags=ce.ClaimFlags.from_dict(_flags()),
                        role=ce.ClaimRole.BACKWARD, lang="en")

    with tempfile.TemporaryDirectory() as d:
        led = ClaimLedger(Path(d) / "claims.ledger.jsonl")

        # batched extract of a run of 2 → C-0001, C-0002
        s1 = json.dumps({"candidates": [
            {"text": "A", "source_line": 1, "ambiguous": False, "reason": ""},
            {"text": "B", "source_line": 2, "ambiguous": False, "reason": ""}]})
        s2 = json.dumps({"claims": [
            {"text": "A holds.", "source_line": 1, "role": "backward", "flags": _flags()},
            {"text": "B holds.", "source_line": 2, "role": "backward", "flags": _flags()}]})
        cs = ce.TwoStageClaimEngine(ce.FakeModelAdapter(s1, s2)).identify(
            ce.Source(ce.SourceType.LOCAL_FILE, "x_RESEARCH.md", "…"))
        ids = led.record_run(cs, checked_at="t0")
        assert ids == ["C-0001", "C-0002"], ids

        # revise C-0001 → v2, parent_version 1, prior superseded by fold
        v = led.record_revised("C-0001", _claim("A holds firmly.", 1), checked_at="t1")
        assert v == 2
        st = led.current_state("C-0001")
        assert st["version"] == 2 and st["parent_version"] == 1 and st["status"] == "active"

        # verified event (consumer) does not change status
        led.record_verified("C-0001", verdict="PASS", checked_at="t2")
        assert led.current_state("C-0001")["status"] == "active"

        # retract C-0002
        led.record_retracted("C-0002", checked_at="t3", reason="source corrected")
        assert led.current_state("C-0002")["status"] == "retracted"

        # a NEW extract must NOT reuse C-0002 (never-reused ids)
        cid = led.record_extracted(_claim("C holds.", 3), checked_at="t4")
        assert cid == "C-0003", cid

        # cannot revise a retracted claim
        try:
            led.record_revised("C-0002", _claim("x", 2), checked_at="t5")
            raise AssertionError("expected LedgerError")
        except LedgerError:
            pass

    print("SELF-TEST PASS: batched extract; revise (v2, parent_version, supersede via "
          "fold); verified leaves status; retract; no C-NNNN reuse; retracted-revise refused.")
    sys.exit(0)

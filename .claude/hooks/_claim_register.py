#!/usr/bin/env python3
"""Evidence Register + check_against() contradiction surfacing (Slice S7).

The engine's persisted, consumed surface (A11/A14). Per topic, `_CLAIMS.md` is
elevated to an Evidence Register holding **locators + state, NOT copied content**
(rule 10 — the source / ledger is master). Consumers `check_against()` new work and
CONTRADICTIONS SURFACE TO THE OPERATOR (observable 3, U7) — no auto-block, because
the engine does not validate (A9); surfacing ≠ deciding.

Consultation default = the S6 grounding set {faithful + valid + committed}; excluded
claims are shown explicitly-not-truth (Q-I / U7). The register is
LANGUAGE-HETEROGENEOUS (EN/RU/DE side by side; locators + state are language-neutral).
Cross-lingual contradiction detection (an EN claim vs a RU claim without translating
either) is a NAMED CARRY-FORWARD (A16ii) — surfaced as "not compared", not silently
missed.

Contradiction detection here is a lexical heuristic-v1 (same core tokens, opposite
negation parity). A stronger detector is a later Validation-consumer concern; the
register's job is to SURFACE, not to decide.

Standalone / unit-testable (`python3 _claim_register.py --self-test`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from _claim_engine import Claim
from _claim_state import ClaimState, Lifecycle, Validity


_TEMPLATE = (
    "Parent: [[{topic}_THOUGHT]]\n\n"
    "# Claims Registry — {topic}  (Evidence Register)\n\n"
    "Locators + state only; the source / ledger is master (rule 10). "
    "Language-heterogeneous. Do not hand-edit claim text here.\n\n"
    "## Research Claims\n"
    "<!-- claim_id | locator | lang | grounding | faithful | validity | lifecycle -->\n"
)

_ROW_RE = re.compile(
    r"^\|\s*(?P<cid>C-\d+)\s*\|\s*(?P<loc>[^|]+?)\s*\|\s*(?P<lang>[^|]+?)\s*\|"
    r"\s*(?P<grounding>[^|]+?)\s*\|\s*(?P<faithful>[^|]+?)\s*\|"
    r"\s*(?P<validity>[^|]+?)\s*\|\s*(?P<lifecycle>[^|]+?)\s*\|\s*$")


@dataclass(frozen=True)
class RegisterEntry:
    claim_id: str
    locator: str        # the anchor — NOT the claim text
    lang: str
    state: ClaimState

    def to_row(self) -> str:
        s = self.state
        return (f"| {self.claim_id} | {self.locator} | {self.lang} | "
                f"{'yes' if s.is_grounding_truth() else 'no'} | "
                f"{str(s.faithful).lower()} | {s.validity.value} | {s.lifecycle.value} |")


class EvidenceRegister:
    """A per-topic `_CLAIMS.md` register. `ledger` (S4) resolves claim text on demand
    (the register never copies text)."""

    def __init__(self, path, topic: str, ledger=None):
        self._path = Path(path)
        self._topic = topic
        self._ledger = ledger
        self._entries: dict = {}     # claim_id -> RegisterEntry (in-memory index)

    # -- ensure_exists (reuses the claims_registry.ensure_exists() convention) --
    def ensure_exists(self) -> bool:
        if self._path.exists():
            return False
        self._path.write_text(_TEMPLATE.format(topic=self._topic), encoding="utf-8")
        return True

    # -- add / persist (locators + state, not content) -----------------------
    def add(self, claim: Claim, state: ClaimState) -> None:
        if claim.claim_id is None:
            raise ValueError("register entries need a ledger-assigned claim_id (S4)")
        entry = RegisterEntry(claim_id=claim.claim_id, locator=claim.anchor.locator,
                              lang=claim.lang, state=state)
        self._entries[entry.claim_id] = entry
        self.ensure_exists()
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(entry.to_row() + "\n")

    # -- load persisted rows (rebuild the in-memory index from `_CLAIMS.md`) ---
    def load(self) -> "EvidenceRegister":
        """Rebuild `_entries` from the persisted register rows so a register written
        in a PRIOR run/session can be consulted (S8/A2 consultation gate). The row
        carries the three state axes verbatim; claim TEXT is still resolved on demand
        from the ledger (rule 10 — the register never stores text). Idempotent."""
        if not self._path.exists():
            return self
        for line in self._path.read_text(encoding="utf-8").splitlines():
            m = _ROW_RE.match(line)
            if not m:
                continue
            state = ClaimState(
                faithful=(m.group("faithful").strip().lower() == "true"),
                validity=Validity(m.group("validity").strip()),
                lifecycle=Lifecycle(m.group("lifecycle").strip()),
            )
            entry = RegisterEntry(claim_id=m.group("cid").strip(),
                                  locator=m.group("loc").strip(),
                                  lang=m.group("lang").strip(), state=state)
            self._entries[entry.claim_id] = entry
        return self

    def entries(self):
        return list(self._entries.values())

    def grounding_entries(self):
        return [e for e in self._entries.values() if e.state.is_grounding_truth()]

    def excluded_entries(self):
        """Explicitly-not-truth, with reasons (U7)."""
        return [{"claim_id": e.claim_id, "locator": e.locator,
                 "reason": e.state.exclusion_reason()}
                for e in self._entries.values() if not e.state.is_grounding_truth()]

    # -- check_against: surface contradictions to the operator (obs. 3) -------
    def check_against(self, statement: str, *, lang: str = "en") -> dict:
        """Compare `statement` against the GROUNDING set only (Q-I default). Returns
        contradictions to surface + cross-lingual pairs left uncompared (carry-forward)
        + the excluded set shown explicitly-not-truth. Never blocks (A9/U7)."""
        contradictions, cross_lingual = [], []
        for e in self.grounding_entries():
            text = self._resolve_text(e.claim_id)
            if text is None:
                continue
            if e.lang != lang:
                cross_lingual.append({"claim_id": e.claim_id, "locator": e.locator,
                                      "entry_lang": e.lang, "statement_lang": lang,
                                      "note": "cross-lingual — not compared (A16ii carry-forward)"})
                continue
            if _contradicts(statement, text):
                contradictions.append({"claim_id": e.claim_id, "locator": e.locator,
                                       "register_claim": text, "statement": statement})
        return {"contradictions": contradictions,
                "cross_lingual_uncompared": cross_lingual,
                "excluded_not_truth": self.excluded_entries(),
                "blocked": False}

    def _resolve_text(self, claim_id: str):
        if self._ledger is None:
            return None
        st = self._ledger.current_state(claim_id)
        return st["text"] if st else None


# ── lexical contradiction heuristic-v1 ───────────────────────────────────────

_NEG = {"not", "no", "never", "cannot", "cant", "isnt", "arent", "doesnt",
        "dont", "wont", "nt", "without", "unable", "cannot"}
_STOP = {"the", "a", "an", "is", "are", "of", "to", "on", "in", "for", "and",
         "or", "be", "it", "that", "this", "as", "at", "by", "with"}


def _core_and_parity(text: str):
    toks = re.findall(r"[a-z0-9]+", text.lower())
    parity = sum(1 for t in toks if t in _NEG) % 2      # odd = negated assertion
    core = {t for t in toks if t not in _NEG and t not in _STOP}
    return core, parity


def _contradicts(a: str, b: str) -> bool:
    ca, pa = _core_and_parity(a)
    cb, pb = _core_and_parity(b)
    if not ca or not cb:
        return False
    overlap = len(ca & cb) / max(len(ca), len(cb))
    return overlap >= 0.6 and pa != pb                  # same topic, opposite polarity


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import tempfile

    if "--self-test" not in sys.argv:
        print("usage: python3 _claim_register.py --self-test")
        sys.exit(1)

    import _claim_engine as ce
    import _claim_ledger as cl
    import _claim_state as cst

    def _flags():
        return {c: True for c in ce.CRITERIA}

    with tempfile.TemporaryDirectory() as d:
        led = cl.ClaimLedger(Path(d) / "claims.ledger.jsonl")
        cid = led.record_extracted(
            ce.Claim("NEDNSProxyProvider is available on macOS 10.15.",
                     ce.Anchor.local_file("DNS_RESEARCH.md", 36),
                     ce.ClaimFlags.from_dict(_flags()), ce.ClaimRole.BACKWARD, "en"),
            checked_at="t0")
        claim = ce.Claim("NEDNSProxyProvider is available on macOS 10.15.",
                         ce.Anchor.local_file("DNS_RESEARCH.md", 36),
                         ce.ClaimFlags.from_dict(_flags()), ce.ClaimRole.BACKWARD, "en",
                         claim_id=cid)
        state = cst.build_state(claim, thought_status="DONE")

        reg = EvidenceRegister(Path(d) / "topic_CLAIMS.md", "topic", ledger=led)
        assert reg.ensure_exists() is True
        reg.add(claim, state)
        # register file holds the LOCATOR, not the text
        body = (Path(d) / "topic_CLAIMS.md").read_text()
        assert "DNS_RESEARCH.md:36" in body and "available on macOS" not in body

        # a contradicting statement surfaces
        res = reg.check_against("NEDNSProxyProvider is NOT available on macOS 10.15.")
        assert len(res["contradictions"]) == 1 and res["blocked"] is False

        # an agreeing statement does not
        res2 = reg.check_against("NEDNSProxyProvider is available on macOS 10.15.")
        assert res2["contradictions"] == []

        # cross-lingual is flagged, not compared
        res3 = reg.check_against("NEDNSProxyProvider недоступен на macOS.", lang="ru")
        assert len(res3["cross_lingual_uncompared"]) == 1

        # a WIP claim is NOT in the grounding set → not consulted
        wip_claim = ce.Claim("X.", ce.Anchor.local_file("f.md", 1),
                             ce.ClaimFlags.from_dict(_flags()), ce.ClaimRole.BACKWARD,
                             "en", claim_id="C-0002")
        led.record_extracted(wip_claim, checked_at="t1")
        reg.add(wip_claim, cst.build_state(wip_claim, thought_status="in progress"))
        assert len(reg.grounding_entries()) == 1
        assert len(reg.excluded_entries()) == 1

    print("SELF-TEST PASS: register holds locators+state (not text); contradiction "
          "surfaces (no block); agreement doesn't; cross-lingual flagged; WIP excluded.")
    sys.exit(0)

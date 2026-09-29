#!/usr/bin/env python3
"""Claim-set validate + persist seam (Slice S8, Actions A3/A4/A5) — standalone.

The code-enforced storage seam for the AI-as-adapter `manageable` mode. A consumer
skill (extract-knowledge / clarification Step 2 / plan Gate 0b2) instructs the
in-session AI to run the two-stage identify→structure CONTRACT (six criteria +
ambiguity-refusal gate + decontextualization — the engine's documented contract) and
emit a structured claim set. THIS module is the port's driven side: it VALIDATES that
output against the engine schema (A13 — no unstructured AI output reaches a store; a
missing criterion flag raises SchemaError) and, in document mode, persists it via the
shipped append-only ledger (S4) + Evidence Register (S7).

Two output modes (U1):
  * in_memory (default — clarification Step 2, plan Gate 0b2): validate + return the
    typed set + the derived completeness breakdown (S3 / observable 2 available). No store.
  * document (extract-knowledge): validate + append to the ledger + add to the register
    with three-axis state (S6). Returns the assigned claim_ids + completeness.

The engine still does NOT validate truth (A9): faithfulness is a per-criterion flag vs
the source, not a verdict. Grounding-truth lifecycle comes from the owning thought's
bookkeeping status (S6), passed in as `thought_status`.

Payload contract (one JSON object):
  { "source_type": "local_file|pdf|epub|web|transcript",
    "source_path": "<path or in-memory label>",
    "lang": "en", "thoroughness": "normal|deep|ultra_deep",
    "claims": [ { "text": "...", "role": "backward|forward",
                  "locator": "<anchor>" | "line": <int>,   # line → LOCAL_FILE path:line
                  "flags": { atomicity, verifiability, decontextuality,
                             minimality, fluency, faithfulness : bool } } ],
    "refused": [ { "text": "...", "reason": "..." } ]   # optional — ambiguity-gate drops
  }

Standalone / unit-testable:  python3 _claim_persist.py --self-test
CLI:  python3 _claim_persist.py <payload.json|-> [--persist --register PATH --ledger PATH --slug S --thought-status STR]
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from _claim_engine import (Anchor, Claim, ClaimFlags, ClaimRole, ClaimSet,
                           Source, SourceType, Thoroughness, SchemaError)
from _claim_ledger import ClaimLedger
from _claim_register import EvidenceRegister
from _claim_state import build_state


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_anchor(source_type: SourceType, source_path: str, claim: dict) -> Anchor:
    loc = claim.get("locator")
    if loc:
        return Anchor(source_type=source_type, locator=str(loc))
    if source_type is SourceType.LOCAL_FILE and "line" in claim:
        return Anchor.local_file(source_path, int(claim["line"]))
    raise SchemaError(f"claim needs a 'locator' (or 'line' for local_file): {claim.get('text', '')[:60]!r}")


def parse_claim_set(payload: dict) -> ClaimSet:
    """Validate the AI-produced payload against the engine schema → a ClaimSet.
    Raises SchemaError on any structural violation (A13 — code validates at the seam)."""
    if not isinstance(payload, dict):
        raise SchemaError("payload must be a JSON object")
    try:
        source_type = SourceType(payload["source_type"])
    except (KeyError, ValueError) as e:
        raise SchemaError(f"invalid/missing source_type: {e}")
    source_path = payload.get("source_path")
    if not source_path:
        raise SchemaError("payload missing source_path")
    lang = payload.get("lang", "en")
    try:
        thoroughness = Thoroughness(payload.get("thoroughness", "normal"))
    except ValueError as e:
        raise SchemaError(f"invalid thoroughness: {e}")

    raw = payload.get("claims")
    if not isinstance(raw, list) or not raw:
        raise SchemaError("payload.claims must be a non-empty list")

    claims = []
    for c in raw:
        if not isinstance(c, dict) or not c.get("text"):
            raise SchemaError(f"each claim needs non-empty text: {c!r}")
        flags = ClaimFlags.from_dict(c.get("flags", {}))    # raises on missing criteria
        role = ClaimRole(c.get("role", "backward"))
        anchor = _build_anchor(source_type, source_path, c)
        # Optional per-claim id (S8 — plan Gate 0b2 From-linkage). Accepted when
        # present, ignored when absent (backward-compatible). It is the stable token a
        # plan's Outcome-Claims "From" cell cites (e.g. "E1"); the ledger still assigns
        # its own claim_id in document mode (that path never carries a user id, so there
        # is no collision). A present id must be a non-empty string.
        cid = c.get("id")
        if cid is not None:
            cid = str(cid).strip()
            if not cid:
                raise SchemaError(f"claim 'id' when present must be a non-empty string: {c!r}")
        claims.append(Claim(text=c["text"], anchor=anchor, flags=flags, role=role,
                            lang=lang, claim_id=cid))

    cs = ClaimSet(source_path=source_path, thoroughness=thoroughness, claims=claims,
                  refused=list(payload.get("refused", [])))
    return cs


def run(payload: dict, *, persist: bool = False, register_path=None, ledger_path=None,
        slug=None, thought_status="in progress", checked_at=None, lang="en",
        runs_sidecar=None, site=None, run_model="sonnet", dedup_runs=False) -> dict:
    """Validate always; persist to ledger+register only in document mode.

    When `runs_sidecar` is set, append a durable A4 manageable run-record (SC2) — the
    single locus that lets plan-0b2 and extract-knowledge record "the engine ran" without
    per-site bespoke code. Best-effort: a record failure never fails validation."""
    checked_at = checked_at or _now_iso()
    cs = parse_claim_set(payload)
    # CF-4/A2: a Deep-mode site must produce a DEEP claim-set — the single locus for the
    # persist-seam consumers (plan-0b2, extract-knowledge). Automatic/unset sites accept any
    # thoroughness (no-op). Fail-open if the mode module is unavailable (schema floor holds).
    if site:
        try:
            from _claim_harvest import deep_mode_ok
            _chk = deep_mode_ok(site, getattr(cs.thoroughness, "value", cs.thoroughness))
            if not _chk["ok"]:
                raise SchemaError(_chk["error"])
        except ImportError:
            pass
    completeness = cs.criteria_breakdown()

    if runs_sidecar:
        try:
            import _claim_metrics as _cm
            _cm.append_run_record(
                runs_sidecar,
                _cm.manageable_record(cs, site=site or "unknown", model=run_model,
                                      checked_at=checked_at),
                dedup_last=dedup_runs)
        except Exception as e:  # noqa: BLE001 — observability record must not fail validation
            print(f"[claim-run-record] warning: {e}", file=sys.stderr)

    if not persist:
        return {"mode": "in_memory", "validated": True,
                "claims": [c.to_record() for c in cs.claims],
                "refused": cs.refused, "completeness": completeness}

    if not (register_path and ledger_path and slug):
        raise SchemaError("document mode requires register_path, ledger_path, slug")
    ledger = ClaimLedger(ledger_path)
    register = EvidenceRegister(register_path, slug, ledger=ledger)
    register.ensure_exists()
    claim_ids = ledger.record_run(cs, checked_at=checked_at)
    for claim, cid in zip(cs.claims, claim_ids):
        persisted = Claim(text=claim.text, anchor=claim.anchor, flags=claim.flags,
                          role=claim.role, lang=claim.lang, claim_id=cid)
        register.add(persisted, build_state(persisted, thought_status=thought_status))
    return {"mode": "document", "validated": True, "claim_ids": claim_ids,
            "register": str(register_path), "refused": cs.refused,
            "completeness": completeness}


def run_fallback(*, site: str, reason: str, runs_sidecar: str, checked_at=None,
                 model: str = "—", dedup_runs: bool = False) -> dict:
    """Record a reason-logged DEFAULT fallback run (A5 — the BYPASSED pattern) at a
    consumer site, without a claim-set. Mirrors the manageable run-record write in
    `run()` so a consumer whose Deep engine could not run still leaves a durable,
    site-keyed `.claim-runs.md` receipt (never a silent skip). Best-effort: a record
    failure never raises. Requires a non-empty reason (enforced by `fallback_record`)."""
    checked_at = checked_at or _now_iso()
    try:
        import _claim_metrics as _cm
        _cm.append_run_record(
            runs_sidecar,
            _cm.fallback_record(site=site or "unknown", reason=reason,
                                checked_at=checked_at, model=model),
            dedup_last=dedup_runs)
    except Exception as e:  # noqa: BLE001 — observability record must not fail the gate
        print(f"[claim-run-record] fallback warning: {e}", file=sys.stderr)
        return {"mode": "fallback", "recorded": False, "site": site, "reason": reason}
    return {"mode": "fallback", "recorded": True, "site": site, "reason": reason}


def format_completeness(result: dict) -> str:
    """Surface the derived completeness at the point of use (U6 / observable 2 available)."""
    b = result.get("completeness", {})
    total = b.get("_total_claims", 0)
    per = ", ".join(f"{c}={b.get(c, 0)}/{total}"
                    for c in ("atomicity", "verifiability", "decontextuality",
                              "minimality", "fluency", "faithfulness"))
    tail = f" ({len(result.get('claim_ids', []))} persisted)" if result.get("mode") == "document" else ""
    return f"[claim-engine:{result.get('mode')}] {total} claim(s){tail}; refused {b.get('_refused', 0)}; completeness: {per}"


# ── CLI ──────────────────────────────────────────────────────────────────────

def _flag(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _main(argv) -> int:
    # Reason-logged DEFAULT fallback (A5) — no claim-set. A consumer whose Deep engine
    # could not run records the receipt instead. Requires --runs-sidecar + --site.
    fb_reason = _flag(argv, "--fallback-reason")
    if fb_reason is not None:
        sidecar = _flag(argv, "--runs-sidecar")
        if not sidecar:
            print("[claim-engine] --fallback-reason requires --runs-sidecar", file=sys.stderr)
            return 2
        try:
            result = run_fallback(site=_flag(argv, "--site", "unknown"), reason=fb_reason,
                                  runs_sidecar=sidecar, model=_flag(argv, "--model", "—"),
                                  dedup_runs="--dedup-runs" in argv)
        except ValueError as e:
            print(f"[claim-engine] fallback error: {e}", file=sys.stderr)
            return 1
        print(f"[claim-engine:fallback] recorded={result['recorded']} site={result['site']}")
        return 0
    positional = [a for a in argv if not a.startswith("--") or a == "-"]
    if not positional:
        print("usage: _claim_persist.py <payload.json|-> [--persist --register P --ledger P --slug S "
              "--thought-status STR --lang L] [--runs-sidecar P --site S --model M --dedup-runs]")
        return 2
    src = positional[0]
    text = sys.stdin.read() if src == "-" else Path(src).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
        result = run(payload,
                     persist="--persist" in argv,
                     register_path=_flag(argv, "--register"),
                     ledger_path=_flag(argv, "--ledger"),
                     slug=_flag(argv, "--slug"),
                     thought_status=_flag(argv, "--thought-status", "in progress"),
                     lang=_flag(argv, "--lang", "en"),
                     runs_sidecar=_flag(argv, "--runs-sidecar"),
                     site=_flag(argv, "--site"),
                     run_model=_flag(argv, "--model", "sonnet"),
                     dedup_runs="--dedup-runs" in argv)
    except (SchemaError, ValueError) as e:
        print(f"[claim-engine] SCHEMA ERROR (nothing stored): {e}", file=sys.stderr)
        return 1
    print(format_completeness(result))
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--self-test" not in sys.argv:
        sys.exit(_main(sys.argv[1:]))

    import tempfile

    def _flags(**over):
        f = {c: True for c in ("atomicity", "verifiability", "decontextuality",
                               "minimality", "fluency", "faithfulness")}
        f.update(over)
        return f

    good = {
        "source_type": "local_file", "source_path": "topic_DNS_RESEARCH.md",
        "lang": "en", "thoroughness": "normal",
        "claims": [
            {"text": "NEDNSProxyProvider is available on macOS 10.15 and later.",
             "line": 36, "role": "backward", "flags": _flags()},
            {"text": "Port-53 hijack catches plaintext DNS.", "line": 40,
             "role": "backward", "flags": _flags()},
        ],
        "refused": [{"text": "it depends", "reason": "no single checkable meaning"}],
    }

    # in-memory mode: validate + completeness, no store
    r = run(good)
    assert r["mode"] == "in_memory" and len(r["claims"]) == 2
    assert r["completeness"]["_total_claims"] == 2 and r["completeness"]["_refused"] == 1
    assert "completeness:" in format_completeness(r)

    # schema enforcement: a missing criterion flag → SchemaError, NOTHING returned
    bad = json.loads(json.dumps(good))
    del bad["claims"][0]["flags"]["faithfulness"]
    try:
        run(bad)
        raise AssertionError("expected SchemaError on missing flag")
    except SchemaError:
        pass

    # empty claims → SchemaError
    try:
        run({"source_type": "local_file", "source_path": "x", "claims": []})
        raise AssertionError("expected SchemaError on empty claims")
    except SchemaError:
        pass

    # document mode: persists to ledger + register with state
    with tempfile.TemporaryDirectory() as d:
        dd = Path(d)
        out = run(good, persist=True, register_path=dd / "topic_CLAIMS.md",
                  ledger_path=dd / "topic.claims.ledger.jsonl", slug="topic",
                  thought_status="DONE", checked_at="t0")
        assert out["mode"] == "document" and out["claim_ids"] == ["C-0001", "C-0002"]
        body = (dd / "topic_CLAIMS.md").read_text()
        assert "topic_DNS_RESEARCH.md:36" in body and "available on macOS" not in body  # locator not text
        assert body.count("| implemented |") == 2   # DONE → committed lifecycle

    # in-memory locator (no file line — clarification/plan use case)
    mem = {"source_type": "web", "source_path": "clarification:step2",
           "lang": "en", "claims": [
               {"text": "The user wants offline-first sync.", "locator": "mirror#3",
                "role": "backward", "flags": _flags()}]}
    rm = run(mem)
    assert rm["claims"][0]["anchor"]["locator"] == "mirror#3"

    # S8: optional per-claim `id` — accepted when present, absent by default (backward-compat)
    with_id = {"source_type": "web", "source_path": "plan:0b2", "lang": "en",
               "claims": [
                   {"text": "Claim with an engine id.", "id": "E1", "locator": "x",
                    "role": "backward", "flags": _flags()},
                   {"text": "Claim without an id.", "locator": "y",
                    "role": "backward", "flags": _flags()}]}
    cs_id = parse_claim_set(with_id)
    assert cs_id.claims[0].claim_id == "E1", "present id must be threaded to claim_id"
    assert cs_id.claims[1].claim_id is None, "absent id must remain None (backward-compat)"

    print("SELF-TEST PASS: schema validated at the seam (missing flag → SchemaError, nothing "
          "stored); in-memory returns typed set + completeness; document persists to "
          "ledger+register (locators not text; DONE→committed); non-file locator supported.")
    sys.exit(0)

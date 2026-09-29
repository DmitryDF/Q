#!/usr/bin/env python3
"""dc-2c-claim-engine-seam (refc S9) — the `/double-check` Step 2c ↔ claim-engine glue.

Code-first split (code_first_architecture.md): the `/double-check` SESSION *produces* the
two-stage JSON as its own reasoning (Stage 1 candidates + ambiguity flags → Stage 2
decontextualized claims + six flags); this module *wires* that JSON through the locked
`TwoStageClaimEngine` (via `in_session_bootstrap`, consuming only through
`ClaimIdentificationPort`) and *flattens* the typed `ClaimSet` into the `scope_addendum`
text that feeds the checker prompt at `_factcheck_engine.py:1841-1842`.

**Byte contract superseded by output-security S2 (authorised perturbation).** Through
slice S6 these `scope_addendum` bytes were held byte-identical to the pre-engine golden.
Output-security S2 deliberately changed them: the numbered claim list is now emitted
through the read-side containment boundary (`flatten_backward` → `output_security.spotlight`),
which is a real perturbation of a locked cross-slice contract, taken on the operator's
recorded decision because the alternative was a slice covering zero seams. The golden
fixture in `tests/test_s6_doublecheck_bytestable.py` is re-baselined to the contained
bytes, with S2 named as the authorised cause.

Boundaries held: producer-never-verifies (identification here is separate from the
isolated Explore checkers that validate) and engine-does-not-validate (the engine only
formulates claims). FORWARD (advice) claims are dropped producer-side — checkers validate
BACKWARD (source-grounded) claims. On ANY engine failure the caller gets a reason-logged
`deep_fallback` signal and runs the retained regex fast-path — never a silent skip.

S2 (walking skeleton): flatten emits claim text. S3 extends `flatten_backward` with
`<path>:<line>` anchors + typed-richness + provenance (`pre_check_layer`) + metrics
(`.claim-runs.md`).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _claim_engine import (  # noqa: E402
    Source, SourceType, Thoroughness, ClaimRole, SchemaError, in_session_bootstrap,
)
from _claim_harvest import resolve_mode, deep_fallback, MODE_DEEP  # noqa: E402
from _claim_metrics import (  # noqa: E402
    manageable_record, fallback_record, append_run_record, sidecar_path_for,
)
# The sanctioned read-side boundary for produced claims (output-security S2). Imported
# hard, not defensively: see `_contain_claim_list`.
from output_security import spotlight  # noqa: E402

SITE = "/double-check Step 2c"

#: The registry id of the ONE contained egress in this file (output-security S2).
#: The sibling `backward_claims` egress below is registered separately and uncovered.
_CONTAINED_SEAM_ID = "dc_seam.flatten_backward"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _contain_claim_list(claim_list_text: str) -> str:
    """Put the numbered BACKWARD-claim list through the sanctioned read-side boundary.

    Containment is HARD: `spotlight` is imported at module load, so a tree that has lost
    the output-security engine fails loudly here rather than silently emitting produced
    claims uncontained. That is deliberate — a soft-fail would make the boundary vanish
    exactly when it is most needed, and would defeat the `lost_coverage` scan gate.

    The read-trail row is the opposite: purely observational, so it is best-effort inside
    the same try/except idiom this file already uses for run-records. A missing registry
    module, or any failure recording the row, must never break the `/double-check` seam."""
    contained = spotlight(claim_list_text)
    try:
        from output_security_registry import record_claim_read
        record_claim_read(seam_id=_CONTAINED_SEAM_ID, spotlit=True)
    except Exception as e:  # noqa: BLE001 — observability must not break the seam
        print(f"[dc-claim-seam] read-trail warning: {e}", file=sys.stderr)
    return contained

# The exact header the retained regex path uses. It was kept identical for a SECOND
# reason beyond the golden fixture: so a checker's prompt did not differ depending on
# whether the claims came from the engine or from the regex fast-path.
#
# Output-security S2 breaks that second property, and does so knowingly rather than
# silently. This engine path now emits its claim list contained (`_contain_claim_list`);
# the retained regex path at `skills/double-check/SKILL.md:99-115` composes an
# uncontained list and is registered as its own uncovered seam (registry row 16). So the
# two paths DO diverge from S2 onward: a checker's prompt now differs by claim source.
# The header itself is still byte-identical — the divergence is the containment around
# the list below it — and the consequence is accepted: the deep path is contained, the
# fallback path is not, and the registry and the read-coverage metric both say so rather
# than implying uniform coverage.
_SCOPE_HEADER = ("Structural claims found in recommendation "
                 "(verify each, plus any others you identify independently):")


def flatten_backward(claim_set) -> str:
    """Deterministic flatten: BACKWARD claims → the numbered-list `scope_addendum` text.

    FORWARD (advice / VeriScore-excluded) claims are dropped. Each item carries its
    resolved `<path>:<line>` anchor (S3 anchor-translation) so a checker can navigate to
    the claim's source location; the six criteria flags stay INTERNAL to the engine (they
    do NOT surface). Returns "" when no BACKWARD claim survives (the caller treats empty
    as a fallback trigger).

    **Contained egress (output-security S2).** The numbered claim list is produced from
    untrusted external content, so it is emitted through `output_security.spotlight` — the
    one sanctioned way to put a produced claim in front of a model. The code-authored
    `_SCOPE_HEADER` stays OUTSIDE the container: it is this system's own instruction to the
    checker, and escaping it would deliver it as data rather than as an instruction. The
    claim text and its locators go inside.

    This is registered as row 1 of `output_security_registry.CONSUMER_REGISTRY` — the only
    `spotlit` row at S2 — and its containment holds on the DEEP path only: the retained
    regex fast-path and every `--auto` run compose their own uncontained list without ever
    reaching this function (registry row 16).

    The empty-list early exit stays BEFORE any wrapping. Wrapping first would return a
    non-empty string for a zero-claim set and invert the caller's fallback trigger."""
    items = [c for c in claim_set.claims if c.role is ClaimRole.BACKWARD]
    if not items:
        return ""
    numbered = []
    for i, c in enumerate(items, 1):
        loc = getattr(getattr(c, "anchor", None), "locator", "") or ""
        numbered.append(f"{i}. {c.text}  [{loc}]" if loc else f"{i}. {c.text}")
    return "\n".join((_SCOPE_HEADER, _contain_claim_list("\n".join(numbered))))


def _provenance(*, backward: int, refused: int, thoroughness: str, model: str) -> str:
    """The provenance token S4 folds into the marker's existing `pre_check_layer`
    `upgraded_target` free-text field (NO schema change — `PreCheckLayer`'s field set is
    byte-unchanged). Records that the verified claims came from the Deep engine (OC3)."""
    return (f"[claim-source: deep-engine ({model}); "
            f"backward={backward}, refused={refused}, thoroughness={thoroughness}]")


# ── separate-actor attested-deep advisory (S6, v2 Centralization / A8) ─────────
#
# The attested-deep "upgrade" for /double-check (design A8): compute the S3
# `_claim_attest.classify_provenance` disposition over the artifact's co-located
# `.claim-runs.md` sidecar and SURFACE it as an ADVISORY signal — ALONGSIDE, never
# replacing, the byte-stable `dc_obligation` blocking discharge (which stays at
# existence-of-any-row, `_sidecar_has_data_row`; NO tightening — a `default`/legacy
# row still discharges, Rule 5). Sidecar-only, separate-actor
# (producer-never-verifies), NEVER blocks — mirrors `_plan_claim_gate._attestation_flags`
# (S3) + `pre_plan_gates._witness_claim_provenance` (S5): an `ADVISORY —` line only on
# a `flagged` disposition; attested-engine / authorized-legacy are info/silent (None).
# Rule 5/U4: a default/fallback row is a sanctioned retained mode, asserted HONESTLY by
# the seam's own result mode, so it is NEVER flagged.


def _mode_assertion(result_mode: str):
    """Honest ``(asserted_mode, asserted_thoroughness)`` for a seam result mode — the
    same honest-assertion shape as `pre_plan_gates._witness_claim_provenance` (a
    fallback/automatic run asserts `default`/`n/a` → authorized-legacy, never flagged
    per U4; only a real `deep` run asserts `manageable`/`deep`, so a missing manageable
    run-record surfaces `flagged`)."""
    if result_mode == "deep":
        return "manageable", "deep"
    return "default", "n/a"


def provenance_advisory(source_path: str, *, asserted_mode: str = "manageable",
                        asserted_thoroughness: str = "deep"):
    """Separate-actor advisory attestation for /double-check Step 2c (S6 / A8).

    Reads ONLY the artifact's co-located `.claim-runs.md` sidecar via the S3
    `_claim_attest.classify_provenance` (sidecar-only, content/artifact/ledger-blind),
    keyed on `source_path` — the SAME `<artifact>.claim-runs.md` the `dc_obligation`
    discharge inspects, so the advisory witnesses the obligation's own discharge sidecar.
    Returns an ``ADVISORY — <reason>`` string on a `flagged` disposition (a
    deep/manageable assertion with no backing manageable run-record), else ``None``
    (attested-engine / authorized-legacy are info/silent). NEVER blocks, NEVER raises —
    a missing classifier or a read failure is silently ``None`` (advisory-only)."""
    try:
        from _claim_attest import classify_provenance
    except ImportError:
        return None
    try:
        disposition = classify_provenance(
            source_path, asserted_mode=asserted_mode,
            asserted_thoroughness=asserted_thoroughness)
    except Exception:  # noqa: BLE001 — advisory-only; never break the seam on a read failure
        return None
    if disposition.get("disposition") == "flagged":
        reason = disposition.get(
            "reason", "claims assert a deep engine run with no matching .claim-runs.md record")
        return f"ADVISORY — {reason}"
    return None


def build_scope_addendum(*, source_path: str, source_text: str,
                         stage1_json: str, stage2_json: str,
                         model: str = "sonnet", lang: str = "en",
                         runs_sidecar: str = None, checked_at: str = None) -> dict:
    """Run the Deep engine on the session-produced stage JSON and return the flattened
    `scope_addendum` + provenance + metrics. Contract:

    - `mode == "deep"`   → `scope_addendum` is a numbered list of BACKWARD claims (each
      with its `<path>:<line>` anchor); the caller injects it verbatim at the existing
      seam. Carries `backward`, `refused`, `claims_total`, `thoroughness`, and
      `provenance` (a token S4 folds into `pre_check_layer.upgraded_target`).
    - `mode == "fallback"` → the engine could not run OR yielded no BACKWARD claim; the
      caller runs the retained regex path. `fallback` carries the non-empty reason
      (`deep_fallback` refuses an empty reason — never silent).
    - `mode == "automatic"` → the site is not Deep (e.g. an override); no engine call.

    When `runs_sidecar` is given, a run-record is appended to that co-located
    `.claim-runs.md` **at identification time** (OMTM completeness + refused count, the
    same machinery as the CF-4 Deep sites): a `manageable` row on success, a reason-logged
    `default` (fallback) row otherwise. `.dc-runs.md` (dispatch audit) is never touched
    here. Metrics recording is best-effort — a sidecar failure never fails the seam.

    Every return additionally carries an **additive `provenance_advisory`** key (S6 / A8):
    the separate-actor attested-deep disposition over the artifact's co-located sidecar
    (an `ADVISORY — …` string only on a `flagged` deep-asserting-no-record case, else
    `None`). It is computed ALONGSIDE — and NEVER changes — the `scope_addendum` bytes or
    the `dc_obligation` blocking discharge; it is advisory-only, never a block.

    Note the scope of that last sentence: the S6 advisory changes nothing. It is NOT a
    claim that the `scope_addendum` bytes are stable in general — output-security S2
    deliberately changed them by routing the claim list through the read-side containment
    boundary (see `flatten_backward` and the `_SCOPE_HEADER` note above)."""
    checked_at = checked_at or _now_iso()
    # Resolve per-artifact discharge path: callers that omit runs_sidecar get the
    # co-located `<source_path>.claim-runs.md` (cross-slice discharge contract — the
    # Stop-gate reconciler reads from sidecar_path_for(source_path), not a shared
    # directory-level file).
    _sidecar = runs_sidecar if runs_sidecar is not None else str(sidecar_path_for(source_path))

    def _record(rec):
        try:
            append_run_record(_sidecar, rec, dedup_last=True)
        except Exception as e:  # noqa: BLE001 — observability must not break the seam
            print(f"[dc-claim-seam] run-record warning: {e}", file=sys.stderr)

    def _advisory(result_mode):
        # S6 additive attested-deep advisory — computed AFTER `_record` so it witnesses
        # the just-written row; honest assertion by mode (Rule 5/U4). Never blocks.
        am, at = _mode_assertion(result_mode)
        return provenance_advisory(source_path, asserted_mode=am, asserted_thoroughness=at)

    if resolve_mode(SITE) != MODE_DEEP:
        # Sentinel discharge so even the AI-free path leaves a record (cross-slice contract).
        _record(fallback_record(site=SITE, reason="site not deep (automatic mode)",
                                checked_at=checked_at, model=model))
        return {"mode": "automatic", "engine_used": False, "scope_addendum": None,
                "provenance_advisory": _advisory("automatic")}
    try:
        engine = in_session_bootstrap(stage1_json, stage2_json, model=model)
        src = Source(SourceType.LOCAL_FILE, source_path, source_text, lang=lang)
        cs = engine.identify(src, thoroughness=Thoroughness.DEEP)
    except SchemaError as e:
        # S5: carry the concrete-category prefix so the reason passes the SHARED
        # fallback-reason grammar (dc_obligation.validate_fallback_reason) that the
        # plan-mode gate also enforces — one grammar, both callers.
        reason = f"engine-error: engine identify failed: {e}"
        _record(fallback_record(site=SITE, reason=reason, checked_at=checked_at, model=model))
        return {"mode": "fallback", "engine_used": False, "scope_addendum": None,
                "fallback": deep_fallback(SITE, reason),
                "provenance_advisory": _advisory("fallback")}
    scope = flatten_backward(cs)
    if not scope:
        # S5: 'zero-claims:' concrete-category prefix (shared grammar).
        reason = "zero-claims: engine returned no BACKWARD claims"
        _record(fallback_record(site=SITE, reason=reason, checked_at=checked_at, model=model))
        return {"mode": "fallback", "engine_used": False, "scope_addendum": None,
                "fallback": deep_fallback(SITE, reason),
                "provenance_advisory": _advisory("fallback")}
    _record(manageable_record(cs, site=SITE, model=model, checked_at=checked_at))
    backward_claims = [c.text for c in cs.claims if c.role is ClaimRole.BACKWARD]
    backward = len(backward_claims)
    return {"mode": "deep", "engine_used": True, "scope_addendum": scope,
            # raw BACKWARD claim texts — Seam B (Step 2d) seeds `target.claims` from these,
            # so the interactive pre-check target is engine-sourced (same identification as
            # Seam A's scope_addendum; one identify() run, two injection points).
            "backward_claims": backward_claims,
            "claims_total": len(cs.claims), "backward": backward,
            "refused": len(cs.refused), "thoroughness": cs.thoroughness.value,
            "provenance": _provenance(backward=backward, refused=len(cs.refused),
                                      thoroughness=cs.thoroughness.value, model=model),
            "provenance_advisory": _advisory("deep")}


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — standalone (portability discipline): python3 _dc_claim_seam.py --self-test
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import json
    if "--self-test" in sys.argv:
        s1 = json.dumps({"candidates": [
            {"text": "worker.py refetches every symbol each run",
             "source_line": 1, "ambiguous": False, "reason": ""},
            {"text": "switch to a 15-minute TTL cache",
             "source_line": 1, "ambiguous": False, "reason": ""},
            {"text": "the cache should probably be fast",
             "source_line": 1, "ambiguous": True, "reason": "'fast' unquantified"},
        ]})
        s2 = json.dumps({"claims": [
            {"text": "worker.py refetches every watchlist symbol on each invocation.",
             "source_line": 1, "role": "backward",
             "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                       "minimality": True, "fluency": True, "faithfulness": True}},
            {"text": "The watchlist fetch should switch to a 15-minute TTL cache.",
             "source_line": 1, "role": "forward",
             "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                       "minimality": True, "fluency": True, "faithfulness": True}},
        ]})
        # happy path: Deep engine → scope_addendum with only the BACKWARD claim
        r = build_scope_addendum(source_path="Docs/x_RECOMMEND.md",
                                 source_text="dummy", stage1_json=s1, stage2_json=s2)
        assert r["mode"] == "deep" and r["engine_used"], r
        assert r["backward"] == 1 and r["refused"] == 1 and r["thoroughness"] == "deep", r
        assert _SCOPE_HEADER in r["scope_addendum"], r
        assert "15-minute TTL cache" not in r["scope_addendum"], "FORWARD claim leaked"
        assert "refetches every watchlist symbol" in r["scope_addendum"], r
        # Exactly 1 BACKWARD item. Counted by numbered items rather than by newlines:
        # output-security S2 wraps the list, so the line count is no longer the item count.
        sa = r["scope_addendum"]
        assert len([ln for ln in sa.splitlines() if ln.startswith("1. ")]) == 1, sa
        assert "2. " not in sa, "expected exactly 1 BACKWARD item"
        # S2 containment: the code-authored header sits OUTSIDE the container; the claim
        # list is inside exactly one container that the content cannot terminate.
        assert sa.startswith(_SCOPE_HEADER + "\n"), sa
        assert sa.count("</produced_claim>") == 1, sa
        assert sa.index("1. ") > sa.index("<produced_claim>"), "claim text escaped the container"
        # S6: the additive attested-deep advisory key is always present (advisory-only;
        # None or an `ADVISORY —` string — value is environment-dependent on the sidecar).
        assert "provenance_advisory" in r, r
        adv = r["provenance_advisory"]
        assert adv is None or adv.startswith("ADVISORY — "), adv

        # fallback: malformed stage-1 JSON → deep_fallback with a non-empty reason
        bad = build_scope_addendum(source_path="Docs/x.md", source_text="dummy",
                                   stage1_json="not json", stage2_json=s2)
        assert bad["mode"] == "fallback" and not bad["engine_used"], bad
        assert bad["fallback"]["claim_fallback_reason"] and bad["fallback"]["silent"] is False, bad
        assert bad["fallback"]["default_retained"] is True, bad
        assert "provenance_advisory" in bad, bad   # S6 additive key on the fallback path too

        # fallback: only FORWARD claims → empty BACKWARD set → fallback (not silent)
        s2_fwd = json.dumps({"claims": [
            {"text": "You should add a --no-cache flag.", "source_line": 1,
             "role": "forward",
             "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                       "minimality": True, "fluency": True, "faithfulness": True}}]})
        s1_one = json.dumps({"candidates": [
            {"text": "add a --no-cache flag", "source_line": 1,
             "ambiguous": False, "reason": ""}]})
        empt = build_scope_addendum(source_path="Docs/x.md", source_text="dummy",
                                    stage1_json=s1_one, stage2_json=s2_fwd)
        assert empt["mode"] == "fallback" and "no BACKWARD" in empt["fallback"]["claim_fallback_reason"], empt

        print("SELF-TEST PASS: Deep scope_addendum flattens BACKWARD-only; FORWARD dropped; "
              "malformed + empty-BACKWARD both route to reason-logged deep_fallback (never silent).")
    else:
        print("usage: python3 _dc_claim_seam.py --self-test", file=sys.stderr)
        sys.exit(2)

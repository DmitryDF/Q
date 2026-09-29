#!/usr/bin/env python3
"""Verified-research → Evidence Register harvest + /re-fc dissolution (Slice S8).

Implements the A17 harvest flow + on-verify trigger, plus the `/re-fc-extraction`
dissolution and the dual-mode ([Decommission]) migration scaffolding. Delivers
observable (1) — a consumer produces claims THROUGH the engine carrying anchors +
state — and observable (4) — a former `/re-fc-extraction` task done by re-running
the validation consumer.

Key rules (locked in S1):
  * Verification signal = the PROSE `**Status:** ✅ VERIFIED … /double-check … PASS`
    line — NOT the `fc_cycles` frontmatter (all fixture files show ESCALATE there
    from the E2a auto-dispatcher timeout). `is_verified()` reads the prose.
  * Hybrid capture: `[stated — URL]` → auto-harvest; `[paraphrased — URL]` →
    operator-confirm (only harvested when confirm=True); `[My assessment]` /
    `[unverified]` → NEVER harvested (faithfulness exclusion, U7).
  * A17 fast-path: marked claim text is LIFTED AS-IS — no re-extraction, no
    translation (native language preserved, A16ii).
  * ESCALATE'd / unverified files are excluded by absence of the VERIFIED signal.

The `/re-fc` dissolution: re-fact-check = re-run the register-validity validation
CONSUMER (S7 `check_against` + S6 validity axis) over the claim set — NOT
`/double-check` 2c, NOT a separate skill. Retirement is user-driven via a
`todo.py [Decommission]` reminder auto-created on the manageable-flip (Q-A).

Standalone / unit-testable (`python3 _claim_harvest.py --self-test <verified_file>`).
"""

from __future__ import annotations

import re
from pathlib import Path

import _claim_anchors
from _claim_engine import Anchor, ClaimFlags, Claim, ClaimRole, CRITERIA, SourceType
from _claim_state import Validity, build_state


# ── verification signal — PROSE, not frontmatter (S1 finding) ────────────────

# Negation directly modifying "verified"/"pass" — a hardened gate rejects these so a
# failure note ("not yet verified — double-check did not pass") cannot false-positive.
# Only NEGATED-VERDICT phrases — NOT a bare "unverified", which is a marker NAME a
# genuinely-verified Status line may mention while describing a marker-hygiene fix
# (e.g. PUSHCERT: "Addigy claim → `[unverified]`"). A truly-unverified file is excluded
# by LACKING the VERIFIED+PASS pattern, not by this guard.
_NEG_NEAR_RE = re.compile(
    r"not\s+yet\s+verified|not\s+verified|"
    r"(?:not|never|did\s*n.?t|does\s*n.?t|could\s*n.?t|failed?\s+to)\s+pass",
    re.IGNORECASE)


def is_verified(research_text: str) -> bool:
    """True iff a prose **Status:** line asserts a passing /double-check verification.

    Hardened (S2 fix, /challenge finding): anchors to a Status line, requires a real
    `pass` word-token (not 'passphrase'/'bypass'), and REJECTS negation modifying
    verified/pass. Deliberately ignores `fc_cycles:` frontmatter (which shows the
    auto-dispatcher's ESCALATE even for verified files)."""
    for line in research_text.splitlines():
        low = line.lower()
        if "status" not in low:                       # must be a Status line
            continue
        if "verified" not in low or "double-check" not in low:
            continue
        if not re.search(r"\bpass(?:es|ed)?\b", low):  # word-token, not 'passphrase'
            continue
        if _NEG_NEAR_RE.search(low):                   # 'not verified' / 'did not pass'
            continue
        return True
    return False


# ── marked-claim extraction (hybrid capture rule, S1) ────────────────────────

_MARKER_RE = re.compile(
    r"\[(?P<kind>stated|paraphrased|my assessment|unverified)"
    r"(?:\s*[—-]\s*(?P<url>https?://[^\]\s]+))?(?P<rest>[^\]]*)\]", re.IGNORECASE)


# ── citation payload (S12/A7 — design-A23) ───────────────────────────────────
#
# The marker regex above makes the URL group OPTIONAL, so an INTERNAL citation
# matches it with no URL and reaches the same lift path a URL-bearing one does.
# Until S12 nothing read what such a citation actually addressed, so every claim —
# internal or not — was anchored to the research file's own line: the address of
# the QUOTATION rather than of the source.
#
# What the payload is read WITH is deliberately not defined here. The citation
# vocabulary and each marker's payload shape live in ONE place,
# `_factcheck_engine.CITATION_MARKER_REGISTRY`, and this module consults its
# classifier rather than carrying a second copy of the grammar.

def _marker_payload(match) -> str:
    """The raw payload of one `_MARKER_RE` match — what follows the kind token.

    Reassembled from the two alternatives the regex offers (a matched URL, or the
    unmatched remainder), so the payload is recovered without changing what the
    regex MATCHES. That invariant is the point: every marker in the corpus, and
    this module's own orientation logic (claim-then-marker vs marker-then-claim),
    depend on the match being byte-identical. Stated as a rule rather than as a
    marker count, because a count goes stale the day the corpus grows and would
    then be a false claim sitting next to a true one.
    """
    url = match.group("url") or ""
    rest = (match.group("rest") or "").strip()
    if url:
        return url
    # No URL group: the remainder still carries the marker's own separator.
    return rest.lstrip("—- \t").strip()


def _classify_payload(kind: str, payload: str):
    """Ask the citation vocabulary what this payload addresses.

    Returns a `ParsedCitation`-shaped object, or None when the engine cannot be
    loaded. None means "could not be read", and the caller treats it as it treats
    every other unreadable citation: the claim is NOT admitted. Degrading to the
    old quotation anchor would put a claim in the register addressed to the wrong
    file, silently, which is the defect this action removes.

    Imported lazily: this module is loaded by a PostToolUse hook on every research
    write, and the engine is large.
    """
    try:
        import _factcheck_engine as _fce
        return _fce.classify_citation(kind, payload)
    except Exception:                                  # noqa: BLE001 — fail-safe
        return None

# markers whose preceding text is grounding-truth-harvestable
_HARVEST_KINDS = {"stated", "paraphrased"}

# A markers-LEGEND line (e.g. "> Markers: `[stated — URL]` direct fact; …") defines the
# markers rather than using them — never a claim source (S2 fix, /challenge finding).
_LEGEND_RE = re.compile(r"^\s*>?\s*markers?\b\s*[:—-]", re.IGNORECASE)


def _last_sentence(seg: str) -> str:
    """Atomicity guard (S2 fix): the claim is the LAST sentence of the preceding
    segment, stripped of markdown heading/list noise — not the whole ~200-char blob
    back to the previous marker."""
    s = seg.replace("**", "").strip(" \t-–—·•>*`").strip()
    if not s:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", s)
    for p in reversed(parts):
        p = p.strip(" \t-–—·•>*`").strip()
        if len(p) >= 3:
            return p
    return s


def _clean_after(seg: str) -> str:
    """After-marker claim text (marker-then-claim): the raw quoted span, stripped of
    surrounding quotes / blockquote / punctuation noise. Taken RAW (not via
    `_last_sentence`) so multi-sentence quotes are not truncated."""
    s = seg.strip().strip('"“”‘’').strip(" \t:—–->*`\"").strip()
    return s if len(s) >= 3 else ""


def extract_marked_claims(research_text: str) -> dict:
    """Return {stated, paraphrased, excluded}. Each item: {text, url, line}.

    ORIENTATION-AWARE (S2.5 fix, 2-Opus-validated + /double-check'd):
      - claim-then-marker (DNS-style `claim. [stated — URL]`): claim = LAST SENTENCE
        before the marker.
      - marker-then-claim (MDM-style `> [stated — URL] "quote"`): when the before-marker
        segment is empty (or editorial-preceded), claim = the AFTER-marker span up to the
        next marker / end-of-line, taken raw.
    Double-count guard: when after-text is consumed, `prev_end` advances to the next
    marker's start so the following marker cannot re-harvest the same span. Legend lines
    are skipped; an editorial `[My assessment]`/`[unverified]`-preceded segment is
    commentary, not a claim (closes the /challenge positional-leak)."""
    stated, paraphrased, excluded = [], [], []
    for lineno, line in enumerate(research_text.splitlines(), start=1):
        if _LEGEND_RE.search(line):
            continue                                  # markers-legend line — skip
        matches = list(_MARKER_RE.finditer(line))
        prev_end, prev_kind = 0, None
        for i, m in enumerate(matches):
            kind = m.group("kind").lower()
            before = _last_sentence(line[prev_end:m.start()])
            next_start = matches[i + 1].start() if i + 1 < len(matches) else len(line)

            if kind in ("my assessment", "unverified"):
                if before:
                    excluded.append({"text": before, "url": m.group("url"),
                                     "line": lineno, "marker": kind})
                prev_end, prev_kind = m.end(), "editorial"
                continue

            bucket = stated if kind == "stated" else paraphrased
            # S12/A7: carry the citation's own payload so the lift can anchor to
            # the SOURCE rather than to this line. Additive — no existing key
            # changes meaning, and `url` stays exactly what it was.
            payload = _marker_payload(m)
            # claim-then-marker: real claim precedes the marker (and is not commentary)
            if before and prev_kind != "editorial":
                bucket.append({"text": before, "url": m.group("url"),
                               "line": lineno, "payload": payload, "marker": kind})
                prev_end, prev_kind = m.end(), kind
                continue
            # marker-then-claim: claim follows the marker (before-segment empty/editorial)
            after = _clean_after(line[m.end():next_start])
            if after:
                bucket.append({"text": after, "url": m.group("url"),
                               "line": lineno, "payload": payload, "marker": kind})
                prev_end, prev_kind = next_start, kind   # advance past consumed span
                continue
            prev_end, prev_kind = m.end(), kind           # nothing to harvest here
    return {"stated": stated, "paraphrased": paraphrased, "excluded": excluded}


# ── A17 harvest flow (fast-path — lift as-is, no re-extraction) ──────────────

def citation_admission(item) -> tuple:
    """Read one marked claim's citation. Returns `(anchor_or_None, reason_or_None)`.

    S12/A7 (design-A23). Two independent decisions live here, scoped DIFFERENTLY
    on purpose — the anchor by citation kind, the admission by what the citation
    can be read as:

    **Anchor — internal citation kinds only.** A `local-file:`, `code:`, `linear:`
    (or retired `topic-CLAUDE:`) claim anchors to the address parsed from its own
    marker. A **URL-cited claim's anchor is returned as None and left exactly as it
    was**: re-addressing it is a real data change to the highest-traffic path with no
    basis in design-A23, which scopes itself to "an internal claim".

    **Admission — every kind.** A claim whose citation cannot be read as an address
    is refused, and the caller surfaces it in `excluded` with this reason. That
    reuses the mechanism this module already has for exactly this situation
    (`[My assessment]` / `[unverified]` are never harvested — "faithfulness
    exclusion, U7", above). It is ONE condition: **the address does not parse.**

    *What this does NOT do.* It does not re-value a single flag. An admitted claim
    still records all six criteria as met, exactly as before — four of them
    (atomicity, decontextuality, minimality, fluency) are properties of the claim
    TEXT, which nothing here examines. S12 narrows WHICH claims are admitted; it
    does not change what an admission asserts.

    *Why not `faithfulness: false` for a refused claim.* That flag is not a
    descriptive label: `is_grounding_truth()` reads it (`_claim_state.py:16`,
    `:81-83`) and `grounding_entries()` filters on it (`_claim_register.py:117-118`),
    so a harvested-but-unfaithful claim would silently empty the register's
    consultation set. Excluding keeps the invariant that everything in the register
    is grounding truth.

    *Never consults the admission record store.* The pin is parsed from the marker
    text. `get_evidence` has no production caller, its writer/reader identities are
    known-divergent, and reaching for it is what the Guiding Policy forbids.
    """
    kind = str(item.get("marker") or "").lower()
    # A missing payload key is NOT special-cased. An item built by a pre-S12
    # caller carries none, and a marker written with an empty bracket body
    # (`[stated]`, as a Status line describing a marker-hygiene fix does) yields
    # an empty one. Both classify as naming no locator class, and therefore take
    # the not-internal path below and are admitted exactly as before.
    #
    # Refusing them was tried and reverted. It is defensible on its own terms —
    # such a "claim" is prose ABOUT markers rather than a cited fact, and there
    # are 5 in the live corpus — but it changes the non-internal path, which
    # design-A23 and Guiding Policy clause 2 both hold fixed. It is recorded as
    # an observation instead of absorbed here.
    parsed = _classify_payload(kind, item.get("payload"))
    if parsed is None:
        return None, "the citation vocabulary could not be loaded to read this address"

    if parsed.source_class != "internal":
        # NOT an internal citation — a URL citation, or a payload naming no locator
        # class the vocabulary recognises. Admitted with its anchor unchanged,
        # exactly as before S12.
        #
        # This scoping is design-A23's own ("an INTERNAL claim anchors to its
        # original source pin") and it is what keeps the URL-cited path untouched.
        #
        # It is not a detail. A first implementation refused EVERY unparseable
        # payload, and measured against the live corpus that refused a substantial
        # share of the harvestable claims — none of them internal, all of them
        # web-cited or free-prose markers. Scoped to internal, the corpus yields
        # ZERO newly-refused claims, which is what the plan predicted. That zero is
        # re-derivable; the pre-correction count is not, and is deliberately not
        # quoted here.
        return None, None

    if parsed.well_formed is False:
        return None, parsed.reason or "citation could not be read as an address"
    if parsed.well_formed is None:
        # Well-formedness could not be CHECKED. Not admitted on an unrun check —
        # the same discipline the verification side applies to the same condition.
        return None, parsed.reason or "citation address could not be checked"

    if parsed.locator not in _internal_anchor_kinds():
        # An internal class with no anchor form registered: admitted, anchor
        # unchanged. Reached only if a class is added to the citation vocabulary
        # without a matching `ANCHOR_SCHEMES` entry.
        return None, None

    try:
        return _claim_anchors.anchor_from_citation(parsed.locator, parsed.parts), None
    except Exception as exc:                           # noqa: BLE001 — fail-safe
        return None, f"citation has no usable anchor: {exc}"


def _internal_anchor_kinds():
    """The citation locator kinds that re-anchor — from the anchor resolver, which
    is the single locus that knows anchor formats, never a list written here."""
    try:
        return set(_claim_anchors.citation_locator_kinds())
    except Exception:                                  # noqa: BLE001 — fail-safe
        return set()


def _harvest_one(item, *, source_path, lang, ledger, register, thought_status,
                 checked_at):
    """Lift one marked claim into the ledger + register (A17 fast-path).

    Returns the claim id, or None when the citation could not be read as an
    address — in which case the caller surfaces the item in `excluded` rather than
    admitting it (S12/A7).
    """
    anchor, refusal = citation_admission(item)
    if refusal:
        return None
    flags = ClaimFlags.from_dict({c: True for c in CRITERIA})  # verified-source ⇒ faithful
    claim = Claim(text=item["text"],
                  # An internal citation anchors to its own address; every other
                  # kind keeps the anchor it has always had, unchanged.
                  anchor=anchor or Anchor.local_file(source_path, item["line"]),
                  flags=flags, role=ClaimRole.BACKWARD, lang=lang)
    cid = ledger.record_extracted(claim, checked_at=checked_at)
    persisted = Claim(text=claim.text, anchor=claim.anchor, flags=claim.flags,
                      role=claim.role, lang=claim.lang, claim_id=cid)
    state = build_state(persisted, thought_status=thought_status,
                        validity=Validity.VALID)
    register.add(persisted, state)
    return cid


def harvest(research_path, *, ledger, register, thought_status="DONE",
            checked_at, lang="en", confirm_paraphrased=False) -> dict:
    """Harvest a verified `_RESEARCH` file into the register. On-verify trigger:
    files without the prose VERIFIED signal are EXCLUDED (returns skipped=True)."""
    path = Path(research_path)
    text = path.read_text(encoding="utf-8")
    if not is_verified(text):
        return {"skipped": True, "reason": "no VERIFIED /double-check status line",
                "harvested": [], "deferred_paraphrased": [], "excluded": []}

    marked = extract_marked_claims(text)
    harvested, deferred = [], []
    excluded = list(marked["excluded"])

    def _lift(it):
        """Admit one claim, or record why it was refused (S12/A7).

        A refusal goes to `excluded` with its reason — never dropped silently.
        `excluded` already carries the `[My assessment]` / `[unverified]`
        exclusions, so a reader has one place to look for "what did not get in".
        """
        anchor, refusal = citation_admission(it)
        if refusal:
            excluded.append({"text": it["text"], "url": it.get("url"),
                             "line": it["line"], "marker": it.get("marker"),
                             "reason": refusal})
            return
        cid = _harvest_one(it, source_path=str(path), lang=lang,
                           ledger=ledger, register=register,
                           thought_status=thought_status, checked_at=checked_at)
        if cid is not None:
            harvested.append(cid)

    for it in marked["stated"]:                      # auto-harvest
        _lift(it)
    for it in marked["paraphrased"]:                 # operator-confirm (hybrid rule)
        if confirm_paraphrased:
            _lift(it)
        else:
            deferred.append(it)
    return {"skipped": False, "harvested": harvested,
            "deferred_paraphrased": deferred, "excluded": excluded}


# ── /re-fc dissolution: re-run the register-validity validation consumer ─────

def re_fact_check(register, statements, *, lang="en") -> dict:
    """A former `/re-fc-extraction` task = re-run the validation consumer over the
    claim set (observable 4). Uses the register's `check_against` (S7) + the S6
    validity axis. NOT `/double-check` 2c; NOT a separate skill."""
    findings = [register.check_against(s, lang=lang) for s in statements]
    contradictions = [c for f in findings for c in f["contradictions"]]
    return {"mode": "validation-consumer-rerun", "used_double_check_2c": False,
            "contradictions": contradictions,
            "checked": len(statements),
            "grounding_consulted": len(register.grounding_entries())}


# ── dual-mode migration + user-driven retirement ([Decommission], Q-A) ───────

CONSUMER_SITES = ("/extract-knowledge", "/plan Gate 0b2", "/clarification Step 2")


def build_decommission_reminder(site: str) -> str:
    """The `todo.py [Decommission]` reminder auto-created when a site flips to
    `manageable` (precedent: claims_registry.ensure_exists / _factcheck_engine)."""
    return (f'[Decommission] legacy `default` claim-finder at {site} — retire once '
            f'`manageable` (engine-backed) is confirmed at parity (benchmark via '
            f'.claim-runs.md). User-driven; the engine never auto-retires.')


# ── CF-4: two-mode selector {Deep, Automatic} per consumer ───────────────────
# The prior binary was default<->manageable with NO thoroughness, so a "manageable"
# flip silently shipped NORMAL — the setting the benchmark showed under-recalls
# (Thoughts/refc-conformance-flip-*_RESEARCH.md). CF-4 replaces that with an explicit
# two-mode selector:
#   Deep      = the engine at DEEP thoroughness (benchmark-proven higher coverage),
#               followed by a moderate consolidation pass (see _claim_consolidate).
#   Automatic = the legacy by-hand `default` finder (the safe, known-good behavior).
# Rules (locked): unset -> Automatic (the safe default); Deep -> Deep ONLY, never also
# Automatic (no double-run); `default` (Automatic) is ALWAYS retained, never deleted
# (U3/U4). The engine does NOT validate; consolidation is a separate producer step.

MODE_DEEP = "deep"
MODE_AUTOMATIC = "automatic"
_MODES = (MODE_DEEP, MODE_AUTOMATIC)

# Engine thoroughness each mode requests. Automatic uses no engine → None.
_MODE_THOROUGHNESS = {MODE_DEEP: "deep", MODE_AUTOMATIC: None}

# Per-consumer default mode. Unset sites resolve to Automatic (the safe default).
# CF-4/A5 set the benchmarked quality consumers to Deep; refc S9 adds `/double-check`
# Step 2c (dc-2c-claim-engine-seam) as a Deep site. Every other (unset) site resolves to
# Automatic, which is retained everywhere as the unset behavior + the reason-logged fallback.
_SITE_MODE_DEFAULTS = {
    "/clarification Step 2": MODE_DEEP,
    "/plan Gate 0b2": MODE_DEEP,
    "/extract-knowledge": MODE_DEEP,
    "/double-check Step 2c": MODE_DEEP,   # refc S9 / dc-2c-claim-engine-seam — Deep default; regex retained as deep_fallback
}

# The gates use different runtime site strings than the CONSUMER_SITES display names
# (e.g. clarification's gate passes "clarification:step2"). `canonical_site` maps every
# known variant to the canonical key so the resolver is robust to which surface calls it.
_SITE_ALIASES = {
    "clarification:step2": "/clarification Step 2",
    "/clarification step 2": "/clarification Step 2",
    "plan:0b2": "/plan Gate 0b2",          # the --site string check-plan-gates.sh passes
    "plan:gate0b2": "/plan Gate 0b2",
    "plan gate 0b2": "/plan Gate 0b2",
    "/plan gate 0b2": "/plan Gate 0b2",
    "extract-knowledge": "/extract-knowledge",
    "/extract-knowledge": "/extract-knowledge",
}


def canonical_site(site: str) -> str:
    """Normalize a runtime/display site string to its canonical key (case-insensitive).
    Unknown strings pass through unchanged (→ resolve to Automatic)."""
    s = str(site).strip()
    return _SITE_ALIASES.get(s.lower(), s)


def mode_thoroughness(mode: str):
    """The engine thoroughness a mode requests: Deep -> 'deep'; Automatic -> None."""
    return _MODE_THOROUGHNESS.get(mode)


def resolve_mode(site: str, *, override: dict | None = None) -> str:
    """Resolve a consumer's mode. Precedence: explicit `override` > per-site default >
    Automatic. An unset/unknown site resolves to Automatic (the safe by-hand default);
    never raises. `override` lets a caller (settings/env/CLAUDE.md) force a site's mode
    without editing this map. Site strings are canonicalized (runtime + display forms)."""
    key = canonical_site(site)
    if override:
        for cand in (site, key):
            if cand in override:
                m = override[cand]
                return m if m in _MODES else MODE_AUTOMATIC
    return _SITE_MODE_DEFAULTS.get(key, MODE_AUTOMATIC)


def set_mode(site: str, mode: str) -> dict:
    """Record a per-consumer mode. `Deep` wires `thoroughness=DEEP` (and marks the
    Automatic finder NOT co-run — Deep-only); `Automatic` is the legacy by-hand default.
    `default` (Automatic) is ALWAYS retained (never deleted, U3/U4). Returns the resolved
    setting for the caller to act on. Raises ValueError on an unknown mode."""
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {_MODES}, got {mode!r}")
    return {"site": site, "mode": mode, "thoroughness": mode_thoroughness(mode),
            "default_retained": True, "runs_automatic_also": False,
            "decommission_reminder": build_decommission_reminder(site)
            if mode == MODE_DEEP else None}


def deep_mode_ok(site: str, thoroughness, *, override: dict | None = None) -> dict:
    """CF-4/A2 — the code enforcement of "Deep -> thoroughness=DEEP". When a site resolves
    to Deep, a `claim_set` it produces MUST carry `thoroughness == 'deep'`; a NORMAL (or
    absent) thoroughness means the producer did NOT honor Deep — the exact "flip ships
    NORMAL" bug — so the gate must refuse (the producer re-runs at DEEP, or passes a
    `claim_fallback_reason`). Automatic sites accept any thoroughness (no engine requirement).
    `thoroughness` is the value string from the claim_set payload (`None`/'' -> 'normal').
    Returns {ok, mode, required, got, error}."""
    mode = resolve_mode(site, override=override)
    got = (str(thoroughness).strip().lower() if thoroughness else "normal")
    if mode != MODE_DEEP:
        return {"ok": True, "mode": mode, "required": None, "got": got, "error": None}
    ok = got == "deep"
    return {"ok": ok, "mode": mode, "required": "deep", "got": got,
            "error": None if ok else (
                f"{site} is set to Deep but its claim_set is thoroughness='{got}', not 'deep' "
                "— the engine did not run at DEEP (this is the 'flip ships NORMAL' bug). "
                "Re-run the producer at DEEP thoroughness, or pass a non-empty "
                "'claim_fallback_reason' to fall back to Automatic.")}


def deep_fallback(site: str, reason: str) -> dict:
    """CF-4/A4 — reason-logged fallback. When a site is Deep but the engine CANNOT run
    (dispatch error, timeout, empty/invalid claim-set), the consumer records a NON-EMPTY
    reason and continues with Automatic (the by-hand default) — never a silent skip. Mirrors
    the `claim_fallback_reason` pattern (`pre_plan_gates.py`). Raises on an empty reason so a
    fallback can never be silent."""
    if not reason or not str(reason).strip():
        raise ValueError("deep_fallback requires a non-empty reason (never a silent skip)")
    return {"site": site, "mode": MODE_AUTOMATIC, "fell_back_from": MODE_DEEP,
            "claim_fallback_reason": " ".join(str(reason).split()), "silent": False,
            "default_retained": True}


def flip_to_manageable(site: str) -> dict:
    """Back-compat shim (pre-CF-4 name). A `manageable` flip now maps to **Deep** mode —
    the benchmark rejected the old NORMAL `manageable` as under-recalling, so the winning
    setting is Deep (`thoroughness=deep`). `default` (Automatic) is RETAINED; a
    `[Decommission]` reminder is emitted. New callers should use `set_mode(site, MODE_DEEP)`
    and read `resolve_mode(site)`."""
    r = set_mode(site, MODE_DEEP)
    return {"site": site, "mode": "manageable", "thoroughness": r["thoroughness"],
            "default_retained": True, "decommission_reminder": r["decommission_reminder"]}


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import tempfile

    if "--self-test" not in sys.argv:
        print("usage: python3 _claim_harvest.py --self-test [verified_research_file]")
        sys.exit(1)

    import _claim_ledger as cl
    import _claim_register as cr

    # marker extraction + verification gate on a synthetic doc
    doc = (
        "**Status:** ✅ VERIFIED 2026-07-06 — `/double-check 3,1,2` → PASS\n"
        "> Markers legend line [stated — http://x]\n"
        "- Port-53 hijack catches plaintext DNS. [stated — https://sing-box.example/tun]\n"
        "- NEDNSProxyProvider is macOS 10.15+. [paraphrased — https://developer.apple/thread]\n"
        "- This is opaque and risky. [My assessment] and [unverified — 403]\n"
    )
    m = extract_marked_claims(doc)
    assert len(m["stated"]) >= 1 and len(m["paraphrased"]) == 1 and len(m["excluded"]) >= 1
    assert is_verified(doc) is True
    assert is_verified("fc_cycles:\n  verdict: ESCALATE\nno status here") is False

    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "X_RESEARCH.md"
        f.write_text(doc, encoding="utf-8")
        led = cl.ClaimLedger(Path(d) / "claims.ledger.jsonl")
        reg = cr.EvidenceRegister(Path(d) / "topic_CLAIMS.md", "topic", ledger=led)

        # default: stated auto-harvested, paraphrased deferred (hybrid rule)
        r = harvest(f, ledger=led, register=reg, checked_at="t0")
        assert not r["skipped"] and len(r["harvested"]) >= 1 and len(r["deferred_paraphrased"]) == 1
        # harvested claims are grounding-truth (verified source + DONE + valid)
        assert len(reg.grounding_entries()) == len(r["harvested"])

        # observable (4): re-FC = re-run the validation consumer (no 2c)
        rfc = re_fact_check(reg, ["Port-53 hijack does NOT catch plaintext DNS."])
        assert rfc["used_double_check_2c"] is False and len(rfc["contradictions"]) == 1

        # unverified file excluded by the on-verify gate
        esc = Path(d) / "ESC_RESEARCH.md"
        esc.write_text("fc_cycles:\n  verdict: ESCALATE\n(no verified status)\n", "utf-8")
        assert harvest(esc, ledger=led, register=reg, checked_at="t1")["skipped"] is True

        # confirm=True harvests the paraphrased too
        led2 = cl.ClaimLedger(Path(d) / "l2.jsonl")
        reg2 = cr.EvidenceRegister(Path(d) / "c2_CLAIMS.md", "t2", ledger=led2)
        r2 = harvest(f, ledger=led2, register=reg2, checked_at="t2", confirm_paraphrased=True)
        assert r2["deferred_paraphrased"] == []

    # [Decommission] reminder + default retained
    flip = flip_to_manageable("/plan Gate 0b2")
    assert flip["default_retained"] and "[Decommission]" in flip["decommission_reminder"]
    assert flip["thoroughness"] == "deep"   # CF-4: manageable now maps to Deep, not NORMAL

    # CF-4 two-mode selector {Deep, Automatic}
    # unset site -> Automatic (safe default); the 3 quality consumers default to Deep
    assert resolve_mode("/some/unknown/site") == MODE_AUTOMATIC
    # refc S9 / dc-2c-claim-engine-seam: 2c is now a Deep site (was deliberately excluded)
    assert resolve_mode("/double-check Step 2c") == MODE_DEEP
    for s in ("/clarification Step 2", "/plan Gate 0b2", "/extract-knowledge",
              "/double-check Step 2c"):
        assert resolve_mode(s) == MODE_DEEP
    # runtime site strings (what the gates actually pass) canonicalize to the same keys
    assert resolve_mode("clarification:step2") == MODE_DEEP          # pre_plan_gates.py _site
    assert resolve_mode("extract-knowledge") == MODE_DEEP
    assert canonical_site("clarification:step2") == "/clarification Step 2"
    # override precedence (settings/env can force a mode without editing the map)
    assert resolve_mode("/extract-knowledge", override={"/extract-knowledge": MODE_AUTOMATIC}) == MODE_AUTOMATIC
    assert resolve_mode("/some/site", override={"/some/site": MODE_DEEP}) == MODE_DEEP
    # a bad override value falls back to Automatic (safe), never raises
    assert resolve_mode("/x", override={"/x": "bogus"}) == MODE_AUTOMATIC
    # thoroughness wiring: Deep -> 'deep'; Automatic -> None (no engine)
    assert mode_thoroughness(MODE_DEEP) == "deep" and mode_thoroughness(MODE_AUTOMATIC) is None
    # set_mode: Deep wires thoroughness=deep, default retained, no double-run, reminder present
    d = set_mode("/extract-knowledge", MODE_DEEP)
    assert d["thoroughness"] == "deep" and d["default_retained"] is True
    assert d["runs_automatic_also"] is False and "[Decommission]" in d["decommission_reminder"]
    # set_mode: Automatic -> no thoroughness, no reminder, default retained
    a = set_mode("/extract-knowledge", MODE_AUTOMATIC)
    assert a["thoroughness"] is None and a["decommission_reminder"] is None and a["default_retained"] is True
    # unknown mode rejected
    try:
        set_mode("/x", "ultra"); raise AssertionError("expected ValueError")
    except ValueError:
        pass

    # CF-4/A2 deep_mode_ok: a Deep site must produce a DEEP claim-set; NORMAL is refused
    assert deep_mode_ok("/extract-knowledge", "deep")["ok"] is True
    bad = deep_mode_ok("/extract-knowledge", "normal")
    assert bad["ok"] is False and bad["required"] == "deep" and "ships NORMAL" in bad["error"]
    assert deep_mode_ok("/extract-knowledge", None)["ok"] is False   # absent -> normal -> refused
    # refc S9: 2c is now a Deep site — DEEP accepted, NORMAL refused (was an Automatic example)
    assert deep_mode_ok("/double-check Step 2c", "deep")["ok"] is True
    assert deep_mode_ok("/double-check Step 2c", "normal")["ok"] is False
    # Automatic (unset) sites accept any thoroughness (no engine requirement)
    assert deep_mode_ok("/some/unset/site", None)["ok"] is True
    # override can force Automatic (then NORMAL is fine)
    assert deep_mode_ok("/extract-knowledge", "normal", override={"/extract-knowledge": MODE_AUTOMATIC})["ok"] is True

    # CF-4/A4 reason-logged fallback: Deep can't run -> Automatic, non-empty reason, not silent
    fb = deep_fallback("/extract-knowledge", "engine dispatch timed out after 180s")
    assert fb["mode"] == MODE_AUTOMATIC and fb["fell_back_from"] == MODE_DEEP
    assert fb["silent"] is False and fb["default_retained"] is True and fb["claim_fallback_reason"]
    try:
        deep_fallback("/x", "  "); raise AssertionError("expected ValueError")   # empty reason refused
    except ValueError:
        pass

    # optional: run against a real fixture file passed on argv
    args = [a for a in sys.argv[1:] if a != "--self-test"]
    if args and Path(args[0]).exists():
        txt = Path(args[0]).read_text(encoding="utf-8")
        print(f"  fixture {Path(args[0]).name}: verified={is_verified(txt)}, "
              f"stated={len(extract_marked_claims(txt)['stated'])}, "
              f"paraphrased={len(extract_marked_claims(txt)['paraphrased'])}")

    print("SELF-TEST PASS: prose-status gate; hybrid capture (stated auto / paraphrased "
          "confirm); [My assessment]/[unverified] excluded; A17 fast-path harvest → "
          "grounding-truth; re-FC = consumer rerun (no 2c); ESCALATE excluded; "
          "[Decommission] reminder; default retained.")
    sys.exit(0)

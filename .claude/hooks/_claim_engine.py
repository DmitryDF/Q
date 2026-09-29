#!/usr/bin/env python3
"""Claim-Identification engine — walking skeleton (Slice S2).

Foundation primitive: its ONE job is to *formulate* claims meeting the six named
criteria, in two stages — Stage 1 identify + Claimify-style ambiguity-refusal gate
→ Stage 2 decontextualize/structure (Molecular-Facts). It does **NOT** validate:
deciding whether a claim is true / still-holds is a separate Validation-engine job
that consumers orchestrate (producer-never-verifies — code_first_architecture.md).

Hexagonal layout (single module, layered sections per Cockburn "start with a fat
object, split only as needed"):

  Domain           — plain dataclasses / enums. No I/O, no model, no ORM.
  Ports            — abc.ABC interfaces the application depends on.
  Two-stage core   — the application: orchestrates the stages through the ports.
  Adapters         — concrete ModelAdapterPort implementations (Claude CLI + Fake).
  Bootstrap        — manual dependency injection.

S2 scope: ONE source_type (LOCAL_FILE), Normal thoroughness × in-memory output,
six per-criterion flags recorded on each claim. Deferred to later slices — the
append-only ledger + claim_id (S4), the full anchor resolver + tiers (S5), the
three state axes + lang (S6), the Evidence Register + harvest (S7/S8). This module
carries structured *placeholders* for those (role, lang, anchor) but does not
implement their machinery.

Standalone: no Claude-hook imports; unit-testable via `python3 _claim_engine.py
--self-test`. The only vendor-specific surface is ClaudeCliModelAdapter.
"""

from __future__ import annotations

import json
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Domain — plain data. No imports beyond stdlib; never touches a model or disk.
# ─────────────────────────────────────────────────────────────────────────────

class SourceType(str, Enum):
    """What the source is — sets the claim's ROLE and the anchor scheme (A8/A6).

    Anchor scheme per type lives in `_claim_anchors.ANCHOR_SCHEMES` (S5).
    """
    LOCAL_FILE = "local_file"
    PDF = "pdf"
    EPUB = "epub"
    WEB = "web"
    TRANSCRIPT = "transcript"
    # S12/A6 (design-A23): a claim drawn from a repository or a tracker had no
    # anchor form to take, so an internal citation of either kind could only ever
    # be anchored to the report that quoted it. Adding a member WIDENS what
    # resolves and cannot narrow an existing consumer: no production module
    # matches exhaustively over this enum, and `_claim_persist._build_anchor`
    # honours any type carrying a locator.
    CODE = "code"
    LINEAR = "linear"


class ClaimRole(str, Enum):
    """source_type sets role (A8): backward source-grounded vs forward hypothesis."""
    BACKWARD = "backward"   # source-grounded / verifiable / faithfulness-checked
    FORWARD = "forward"     # hypothesis / thesis / outcome-tested (VeriScore-excluded)


class Thoroughness(str, Enum):
    NORMAL = "normal"       # S2 default
    DEEP = "deep"           # S5
    ULTRA_DEEP = "ultra_deep"  # S5


class OutputMode(str, Enum):
    IN_MEMORY = "in_memory"  # S2 default
    DOCUMENT = "document"    # S5 (persists via ledger/register)


# The six named claim-quality criteria (Guiding Policy clause 2 / A2). Recorded as
# per-criterion flags; these flags are the raw material the completeness OMTM (S3)
# is DERIVED from — not separately measured.
CRITERIA = ("atomicity", "verifiability", "decontextuality",
            "minimality", "fluency", "faithfulness")


@dataclass(frozen=True)
class ClaimFlags:
    """Per-criterion boolean flags on a claim record (A2)."""
    atomicity: bool
    verifiability: bool
    decontextuality: bool
    minimality: bool
    fluency: bool
    faithfulness: bool

    def all_pass(self) -> bool:
        return all(getattr(self, c) for c in CRITERIA)

    @classmethod
    def from_dict(cls, d: dict) -> "ClaimFlags":
        missing = [c for c in CRITERIA if c not in d]
        if missing:
            raise SchemaError(f"claim flags missing criteria: {missing}")
        return cls(**{c: bool(d[c]) for c in CRITERIA})


@dataclass(frozen=True)
class Anchor:
    """Per-source-type auditable anchor (A6). S2 implements LOCAL_FILE (path:line)."""
    source_type: SourceType
    locator: str  # LOCAL_FILE -> "<path>:<line>"

    @classmethod
    def local_file(cls, path: str, line: int) -> "Anchor":
        return cls(source_type=SourceType.LOCAL_FILE, locator=f"{path}:{line}")


@dataclass(frozen=True)
class Claim:
    """One formulated claim. claim_id/state-axes are placeholders here (S4/S6)."""
    text: str                    # decontextualized, standalone, one indivisible fact
    anchor: Anchor
    flags: ClaimFlags
    role: ClaimRole
    lang: str                    # native source language (A16ii); no translation
    claim_id: Optional[str] = None   # assigned by the ledger in S4 (never here)

    def to_record(self) -> dict:
        r = asdict(self)
        r["anchor"] = {"source_type": self.anchor.source_type.value,
                       "locator": self.anchor.locator}
        r["role"] = self.role.value
        return r


@dataclass(frozen=True)
class Source:
    """Engine input = (source, source_type). S2: an in-memory local-file source."""
    source_type: SourceType
    path: str
    text: str
    lang: str = "en"


@dataclass
class ClaimSet:
    """Engine output: a typed claim set + provenance for the run."""
    source_path: str
    thoroughness: Thoroughness
    claims: list = field(default_factory=list)     # list[Claim]
    refused: list = field(default_factory=list)     # list[dict] — ambiguity-gate drops

    def criteria_breakdown(self) -> dict:
        """Per-criterion satisfied counts — the raw material S3's OMTM derives from."""
        out = {c: 0 for c in CRITERIA}
        for cl in self.claims:
            for c in CRITERIA:
                if getattr(cl.flags, c):
                    out[c] += 1
        out["_total_claims"] = len(self.claims)
        out["_refused"] = len(self.refused)
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────

class SchemaError(ValueError):
    """Raised when adapter output violates the structured contract (A13:
    no unstructured AI output ever reaches a store — code validates at the seam)."""


# ─────────────────────────────────────────────────────────────────────────────
# Ports — abstract interfaces the application depends on (code_first_architecture).
# ─────────────────────────────────────────────────────────────────────────────

class ModelAdapterPort(ABC):
    """Output port: the ONLY seam through which the two stages reach a model (A4).

    Vendor/model change touches only a concrete adapter, never the core.
    """
    @abstractmethod
    def complete(self, prompt: str, *, model: str) -> str:
        """Return the model's raw text response for `prompt`."""


class ClaimIdentificationPort(ABC):
    """Input port: what the application offers to consumers."""
    @abstractmethod
    def identify(self, source: Source, *,
                 thoroughness: Thoroughness = Thoroughness.NORMAL,
                 output_mode: OutputMode = OutputMode.IN_MEMORY) -> ClaimSet:
        """Formulate a typed claim set from `source`. Does NOT validate."""


# ─────────────────────────────────────────────────────────────────────────────
# Two-stage core — the application. Orchestrates stages through the ports only.
# ─────────────────────────────────────────────────────────────────────────────

_STAGE1_MARKER = "CLAIM-ENGINE-STAGE-1-IDENTIFY"
_STAGE2_MARKER = "CLAIM-ENGINE-STAGE-2-STRUCTURE"


_TIER_INSTRUCTION = {
    Thoroughness.NORMAL: "Thoroughness: NORMAL — surface the clearly-present claims.",
    Thoroughness.DEEP: "Thoroughness: DEEP — be exhaustive; surface implicit + "
                       "qualified claims a normal pass would miss.",
    Thoroughness.ULTRA_DEEP: "Thoroughness: ULTRA-DEEP — maximal recall; decompose "
                             "compound statements and surface every checkable sub-claim.",
}


def _stage1_prompt(source: Source, thoroughness: Thoroughness = Thoroughness.NORMAL) -> str:
    return (
        f"{_STAGE1_MARKER}\n"
        f"{_TIER_INSTRUCTION[thoroughness]}\n"
        "You are Stage 1 of a claim-identification engine: IDENTIFY + AMBIGUITY-REFUSAL GATE.\n"
        "From the source below, list candidate factual claims. For EACH candidate decide "
        "whether it resolves to a SINGLE, checkable meaning; if it does not, mark it "
        "ambiguous (it will be refused, not extracted — Claimify gate).\n"
        "Return ONE JSON object, no prose, no fence:\n"
        '{ "candidates": [ { "text": "<verbatim-ish span>", "source_line": <int>, '
        '"ambiguous": <bool>, "reason": "<why ambiguous, or empty>" } ] }\n\n'
        f"Source ({source.source_type.value}, lang={source.lang}), path={source.path}:\n"
        f"{source.text}"
    )


def _stage2_prompt(source: Source, candidates: list,
                   thoroughness: Thoroughness = Thoroughness.NORMAL) -> str:
    return (
        f"{_STAGE2_MARKER}\n"
        f"{_TIER_INSTRUCTION[thoroughness]}\n"
        "You are Stage 2 of a claim-identification engine: DECONTEXTUALIZE + STRUCTURE.\n"
        "Rewrite each surviving candidate as a STANDALONE atomic claim with no dangling "
        "referents, pared to one indivisible fact (Molecular Facts). Score the six "
        "criteria as booleans: atomicity, verifiability, decontextuality, minimality, "
        "fluency, faithfulness. Do NOT judge whether the claim is TRUE — that is a "
        "separate job (this engine does not validate).\n"
        "Return ONE JSON object, no prose, no fence:\n"
        '{ "claims": [ { "text": "<standalone atomic claim>", "source_line": <int>, '
        '"role": "backward|forward", "flags": { "atomicity": <bool>, "verifiability": '
        '<bool>, "decontextuality": <bool>, "minimality": <bool>, "fluency": <bool>, '
        '"faithfulness": <bool> } } ] }\n\n'
        f"lang={source.lang}; keep the claim in its native source language (no translation).\n"
        f"Surviving candidates:\n{json.dumps(candidates, ensure_ascii=False)}"
    )


def _parse_json_object(raw: str) -> dict:
    """Parse the adapter's response as a single JSON object. Tolerates a code fence."""
    s = raw.strip()
    if s.startswith("```"):
        s = s.strip("`")
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
        s = s.strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise SchemaError(f"adapter did not return valid JSON: {e}") from e
    if not isinstance(obj, dict):
        raise SchemaError("adapter JSON top-level must be an object")
    return obj


class TwoStageClaimEngine(ClaimIdentificationPort):
    """Stage 1 (identify + ambiguity gate) → Stage 2 (decontextualize/structure).

    Quality is enforced BY CONSTRUCTION (the gate refuses ambiguous candidates
    before any structuring), not checked after. Reaches the model only through the
    injected ModelAdapterPort.
    """

    def __init__(self, model_adapter: ModelAdapterPort, *, model: str = "sonnet",
                 ledger=None):
        self._adapter = model_adapter
        self._model = model
        self._ledger = ledger      # required only for OutputMode.DOCUMENT (S4)

    # -- public port ---------------------------------------------------------
    def identify(self, source: Source, *,
                 thoroughness: Thoroughness = Thoroughness.NORMAL,
                 output_mode: OutputMode = OutputMode.IN_MEMORY,
                 checked_at: str = None) -> ClaimSet:
        # All source types are handled via the S5 anchor resolver (single change
        # locus); an unregistered type raises SchemaError there, not here.
        candidates, refused = self._stage1_identify(source, thoroughness)
        claims = self._stage2_structure(source, candidates, thoroughness)
        # NOTE: no validation step here — producer-never-verifies (A9).
        claim_set = ClaimSet(source_path=source.path, thoroughness=thoroughness,
                             claims=claims, refused=refused)
        if output_mode is OutputMode.DOCUMENT:
            if self._ledger is None or checked_at is None:
                raise SchemaError(
                    "OutputMode.DOCUMENT requires a ledger and checked_at (S4 persistence)")
            ids = self._ledger.record_run(claim_set, checked_at=checked_at)
            claim_set.claims = [
                Claim(text=c.text, anchor=c.anchor, flags=c.flags, role=c.role,
                      lang=c.lang, claim_id=cid)
                for c, cid in zip(claim_set.claims, ids)]
        return claim_set

    # -- Stage 1: identify + Claimify ambiguity-refusal gate ------------------
    def _stage1_identify(self, source: Source, thoroughness: Thoroughness):
        raw = self._adapter.complete(_stage1_prompt(source, thoroughness), model=self._model)
        obj = _parse_json_object(raw)
        cands = obj.get("candidates")
        if not isinstance(cands, list):
            raise SchemaError("stage-1 output missing 'candidates' list")
        surviving, refused = [], []
        for c in cands:
            if not isinstance(c, dict) or "text" not in c or "ambiguous" not in c:
                raise SchemaError(f"stage-1 candidate malformed: {c!r}")
            if bool(c["ambiguous"]):
                refused.append({"text": c["text"], "reason": c.get("reason", "")})
            else:
                surviving.append(c)
        return surviving, refused

    # -- Stage 2: decontextualize / structure + six flags --------------------
    def _stage2_structure(self, source: Source, candidates: list,
                          thoroughness: Thoroughness):
        if not candidates:
            return []
        from _claim_anchors import resolve as _resolve_anchor  # late: avoid import cycle
        raw = self._adapter.complete(_stage2_prompt(source, candidates, thoroughness),
                                     model=self._model)
        obj = _parse_json_object(raw)
        rows = obj.get("claims")
        if not isinstance(rows, list):
            raise SchemaError("stage-2 output missing 'claims' list")
        claims = []
        for r in rows:
            if not isinstance(r, dict):
                raise SchemaError(f"stage-2 claim malformed: {r!r}")
            for k in ("text", "role", "flags"):
                if k not in r:
                    raise SchemaError(f"stage-2 claim missing '{k}': {r!r}")
            role = ClaimRole(r["role"]) if r["role"] in (e.value for e in ClaimRole) \
                else ClaimRole.BACKWARD
            claims.append(Claim(
                text=r["text"],
                anchor=_resolve_anchor(source, r),       # per-source-type anchor (S5)
                flags=ClaimFlags.from_dict(r["flags"]),
                role=role,
                lang=source.lang,
            ))
        return claims


class RegexFastPathEngine(ClaimIdentificationPort):
    """Instant, model-free extraction for hot/free sites (U2).

    Trades quality for speed: pulls structural candidate lines by regex and anchors
    them; the six criteria are left UNSCORED (all False) — the two-stage engine is
    the quality path. Retained PERMANENTLY (not a migration artifact) for hot paths
    and for benchmarking manageable-vs-default (U3). No model, no ambiguity gate.
    """
    _LINE_RE = re.compile(r"^\s*(?:[-*]\s+)?(?P<body>\S.+\S)\s*$")

    def identify(self, source: Source, *,
                 thoroughness: Thoroughness = Thoroughness.NORMAL,
                 output_mode: OutputMode = OutputMode.IN_MEMORY,
                 checked_at: str = None) -> ClaimSet:
        from _claim_anchors import resolve as _resolve_anchor  # late: avoid import cycle
        claims = []
        for i, line in enumerate(source.text.splitlines(), start=1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            m = self._LINE_RE.match(line)
            if not m:
                continue
            # locator hints for whichever source type this hot site uses
            row = {"source_line": i, "page": i, "loc": str(i),
                   "timestamp": f"00:00:{i:02d}", "fragment": f"L{i}"}
            claims.append(Claim(
                text=m.group("body"),
                anchor=_resolve_anchor(source, row),
                flags=ClaimFlags(*(False,) * 6),      # unscored — the trade-off
                role=ClaimRole.BACKWARD, lang=source.lang))
        return ClaimSet(source_path=source.path, thoroughness=thoroughness,
                        claims=claims, refused=[])


# ─────────────────────────────────────────────────────────────────────────────
# Adapters — concrete ModelAdapterPort implementations (infrastructure).
# ─────────────────────────────────────────────────────────────────────────────

# Vendor seam: the family→model-id map. Mirrors _factcheck_engine.py's map so a
# vendor/model swap is a one-line change confined to this adapter (Evolution Test).
_MODEL_ID = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
}


class ClaudeCliModelAdapter(ModelAdapterPort):
    """Production adapter: one non-interactive `claude --print` call per stage."""

    def __init__(self, *, timeout: int = 180):
        self._timeout = timeout

    def complete(self, prompt: str, *, model: str) -> str:
        model_id = _MODEL_ID.get(model, f"claude-{model}-4-8")
        try:
            result = subprocess.run(
                ["claude", "--print", "--model", model_id],
                input=prompt, capture_output=True, text=True, timeout=self._timeout,
            )
        except FileNotFoundError as e:
            raise SchemaError("claude CLI not found — cannot invoke model adapter") from e
        except subprocess.TimeoutExpired as e:
            raise SchemaError(f"model adapter timed out after {self._timeout}s") from e
        if result.returncode != 0:
            raise SchemaError(f"claude CLI failed (exit {result.returncode}): "
                              f"{result.stderr.strip()[:200]}")
        return result.stdout.strip()


class FakeModelAdapter(ModelAdapterPort):
    """Test-double (Cockburn test-to-test). Returns canned per-stage JSON keyed by
    the stage marker in the prompt, so the core can be exercised without a model."""

    def __init__(self, stage1_json: str, stage2_json: str):
        self._s1, self._s2 = stage1_json, stage2_json
        self.calls: list = []

    def complete(self, prompt: str, *, model: str) -> str:
        self.calls.append(model)
        if _STAGE1_MARKER in prompt:
            return self._s1
        if _STAGE2_MARKER in prompt:
            return self._s2
        raise AssertionError("FakeModelAdapter: prompt matched no known stage")


class InSessionModelAdapter(ModelAdapterPort):
    """Production adapter for the INTERACTIVE hot path (dc-2c-claim-engine-seam / refc S9).

    The engine's `ClaudeCliModelAdapter` spawns a `claude --print` subprocess, which is
    unsupported inside a live session. This adapter instead carries the two stage
    responses the *calling session produced as its own reasoning* — the `/double-check`
    skill runs Stage 1 then Stage 2 as its own turns (over `_stage1_prompt` /
    `_stage2_prompt`) and seeds this adapter with the resulting JSON strings. The engine
    then drives `identify()` unchanged, draining the responses keyed by the stage marker
    the engine embeds in each prompt.

    Consume-through-the-port only: the engine core, `ClaimIdentificationPort`,
    `ClaudeCliModelAdapter`, and `FakeModelAdapter` are all untouched — this is one new
    `ModelAdapterPort` implementation (Evolution Test: single change locus).

    On a prompt that carries no known stage marker, raise `SchemaError` (NOT
    `AssertionError`): the engine already raises `SchemaError` on malformed stage output,
    so the caller's one `try/except SchemaError` around `identify()` routes every engine
    failure — including a mis-seeded adapter — to the reason-logged `deep_fallback` regex
    path, never a silent crash.
    """

    def __init__(self, stage1_json: str, stage2_json: str):
        self._s1, self._s2 = stage1_json, stage2_json
        self.calls: list = []

    def complete(self, prompt: str, *, model: str) -> str:
        self.calls.append(model)
        if _STAGE1_MARKER in prompt:
            return self._s1
        if _STAGE2_MARKER in prompt:
            return self._s2
        raise SchemaError("InSessionModelAdapter: prompt carried no known stage marker")


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap — manual dependency injection (code_first_architecture.md).
# ─────────────────────────────────────────────────────────────────────────────

def bootstrap(*, model: str = "sonnet") -> ClaimIdentificationPort:
    """Wire the production adapter to the engine port."""
    return TwoStageClaimEngine(ClaudeCliModelAdapter(), model=model)


def in_session_bootstrap(stage1_json: str, stage2_json: str, *,
                         model: str = "sonnet") -> ClaimIdentificationPort:
    """Wire the in-session adapter to the engine port for the interactive hot path.

    The `/double-check` skill produces `stage1_json` (candidates + ambiguity flags) and
    `stage2_json` (decontextualized claims + six flags) as its own reasoning, then calls
    this to get an engine that consumes them — no `claude --print` subprocess."""
    return TwoStageClaimEngine(
        InSessionModelAdapter(stage1_json, stage2_json), model=model)


# ─────────────────────────────────────────────────────────────────────────────
# Self-test — runs without pytest (portability discipline: standalone modules
# must be runnable via `python3 _claim_engine.py --self-test`).
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        # Inline smoke (no pytest dependency): one happy-path run + one refusal.
        s1 = json.dumps({"candidates": [
            {"text": "NEDNSProxyProvider is available on macOS 10.15+",
             "source_line": 36, "ambiguous": False, "reason": ""},
            {"text": "it depends", "source_line": 40, "ambiguous": True,
             "reason": "no single checkable meaning"},
        ]})
        s2 = json.dumps({"claims": [
            {"text": "NEDNSProxyProvider is available on macOS 10.15 and later.",
             "source_line": 36, "role": "backward",
             "flags": {"atomicity": True, "verifiability": True, "decontextuality": True,
                       "minimality": True, "fluency": True, "faithfulness": True}},
        ]})
        eng = TwoStageClaimEngine(FakeModelAdapter(s1, s2), model="sonnet")
        src = Source(SourceType.LOCAL_FILE, "fixture_DNS_RESEARCH.md",
                     "line 36: NEDNSProxyProvider ...", lang="en")
        cs = eng.identify(src)
        assert len(cs.claims) == 1, cs
        assert len(cs.refused) == 1, cs
        assert cs.claims[0].anchor.locator == "fixture_DNS_RESEARCH.md:36"
        assert cs.claims[0].flags.all_pass()
        assert cs.criteria_breakdown()["_total_claims"] == 1
        print("SELF-TEST PASS: 1 claim formulated, 1 ambiguous candidate refused, "
              "anchor + six flags recorded, no validation performed.")
        sys.exit(0)
    print("usage: python3 _claim_engine.py --self-test")
    sys.exit(1)

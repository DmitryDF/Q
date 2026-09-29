#!/usr/bin/env python3
"""Three-axis claim state + grounding-truth predicate (Slice S6).

The load-bearing definition every consumer + the Evidence Register depend on. A
claim's state has THREE ORTHOGONAL AXES that are **never collapsed** (A7 / Q-J —
correcting the category error that "implemented = truth"):

  1. faithfulness      — vs the source. Derived from the per-criterion flags
                         (the `faithfulness` flag). Bool.
  2. validity-over-time — still holds? Nullable: VALID (holds) / INVALID (no longer
                         holds) / UNKNOWN (dynamic claim awaiting a signal source).
  3. work-lifecycle    — code-derived from the OWNING THOUGHT's bookkeeping:
                         in-progress → THOUGHT (WIP), DONE → IMPLEMENTED,
                         Retired/Won't-Do → EXCLUDED.

    grounding-truth  ⟺  faithful  AND  validity == VALID  AND  lifecycle == IMPLEMENTED

"committed" in the grounding predicate = the owning work reached IMPLEMENTED. A WIP
(THOUGHT), retired/won't-do (EXCLUDED), unfaithful, or not-still-valid (INVALID /
UNKNOWN) claim is NOT grounding-truth — lifecycle alone never means truth. Excluded
claims are surfaced *explicitly-not-truth* (U7), never silently dropped.

Native language is carried through untouched (A16ii) — state has no bearing on text.

Standalone / unit-testable (`python3 _claim_state.py --self-test`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from _claim_engine import Claim


class Validity(str, Enum):
    VALID = "valid"        # holds now (default at extraction for static claims)
    INVALID = "invalid"    # a dynamic claim known to no longer hold
    UNKNOWN = "unknown"    # nullable — dynamic claim awaiting a signal source


class Lifecycle(str, Enum):
    THOUGHT = "thought"          # owning thought in-progress (WIP)
    IMPLEMENTED = "implemented"  # owning thought DONE
    EXCLUDED = "excluded"        # owning thought Retired / Won't-Do


@dataclass(frozen=True)
class ClaimState:
    """The three axes as SEPARATE fields — never collapsed into one 'truth' bool."""
    faithful: bool
    validity: Validity
    lifecycle: Lifecycle

    def is_grounding_truth(self) -> bool:
        return (self.faithful
                and self.validity is Validity.VALID
                and self.lifecycle is Lifecycle.IMPLEMENTED)

    def exclusion_reason(self) -> str:
        """Plain-words why this claim is NOT grounding-truth (U7), or '' if it is."""
        if self.is_grounding_truth():
            return ""
        reasons = []
        if not self.faithful:
            reasons.append("not faithful to source")
        if self.validity is Validity.INVALID:
            reasons.append("no longer holds (validity: invalid)")
        elif self.validity is Validity.UNKNOWN:
            reasons.append("still-holds unknown (validity: unknown)")
        if self.lifecycle is Lifecycle.THOUGHT:
            reasons.append("owning work in progress (not yet committed)")
        elif self.lifecycle is Lifecycle.EXCLUDED:
            reasons.append("owning work retired / won't-do (excluded)")
        return "; ".join(reasons)


# ── derivations (code-first: each axis from its own source, never fused) ─────

def derive_faithfulness(claim: Claim) -> bool:
    """Faithfulness axis = the per-criterion faithfulness flag (A7)."""
    return bool(claim.flags.faithfulness)


_RETIRED_RE = re.compile(r"Retired\s+\d{4}-\d{2}-\d{2}", re.IGNORECASE)
_WONTDO_RE = re.compile(r"won'?t[\s-]?do", re.IGNORECASE)
_DONE_RE = re.compile(r"\b(DONE|implemented)\b", re.IGNORECASE)


def lifecycle_from_status(status: str) -> Lifecycle:
    """Map an owning-thought bookkeeping status string → the lifecycle axis.

    Precedence: excluded (retired/won't-do) > implemented (DONE) > in-progress.
    """
    s = status or ""
    if _RETIRED_RE.search(s) or _WONTDO_RE.search(s):
        return Lifecycle.EXCLUDED
    if _DONE_RE.search(s):
        return Lifecycle.IMPLEMENTED
    return Lifecycle.THOUGHT      # in-progress / not-in-progress → WIP


def build_state(claim: Claim, *, thought_status: str,
                validity: Validity = Validity.VALID) -> ClaimState:
    """Assemble the three-axis state. validity defaults to VALID (a static claim
    holds as of extraction); a dynamic claim is passed UNKNOWN/INVALID explicitly."""
    return ClaimState(
        faithful=derive_faithfulness(claim),
        validity=validity,
        lifecycle=lifecycle_from_status(thought_status),
    )


# ── grounding set (default consultation set + explicit exclusions, U7/Q-I) ───

def partition_grounding(items) -> dict:
    """items: iterable of (claim, ClaimState). Returns the default grounding set
    {faithful+valid+committed} and the explicitly-not-truth list with reasons."""
    grounding, excluded = [], []
    for claim, state in items:
        if state.is_grounding_truth():
            grounding.append(claim)
        else:
            excluded.append({"claim": claim, "reason": state.exclusion_reason()})
    return {"grounding": grounding, "excluded": excluded}


# ── self-test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if "--self-test" not in sys.argv:
        print("usage: python3 _claim_state.py --self-test")
        sys.exit(1)

    import _claim_engine as ce

    def _claim(faithful=True, lang="en"):
        flags = {c: True for c in ce.CRITERIA}
        flags["faithfulness"] = faithful
        return ce.Claim(text="X holds.", anchor=ce.Anchor.local_file("a_RESEARCH.md", 1),
                        flags=ce.ClaimFlags.from_dict(flags),
                        role=ce.ClaimRole.BACKWARD, lang=lang)

    # grounding-truth iff faithful + VALID + IMPLEMENTED
    gt = build_state(_claim(), thought_status="DONE 2026-07-07")
    assert gt.is_grounding_truth() and gt.exclusion_reason() == ""

    # lifecycle alone ≠ truth: implemented but unfaithful → excluded
    unfaithful = build_state(_claim(faithful=False), thought_status="DONE")
    assert not unfaithful.is_grounding_truth()
    assert "not faithful" in unfaithful.exclusion_reason()

    # WIP (in-progress) excluded even if faithful+valid
    wip = build_state(_claim(), thought_status="in progress 🚧 since 2026-07-07")
    assert wip.lifecycle is Lifecycle.THOUGHT and not wip.is_grounding_truth()

    # retired excluded
    retired = build_state(_claim(), thought_status="**Status:** Retired 2026-07-01")
    assert retired.lifecycle is Lifecycle.EXCLUDED and not retired.is_grounding_truth()

    # dynamic claim, validity unknown → not grounding-truth (nullable axis)
    dyn = build_state(_claim(), thought_status="DONE", validity=Validity.UNKNOWN)
    assert not dyn.is_grounding_truth()

    # axes never collapsed: all three independently readable
    assert (gt.faithful, gt.validity, gt.lifecycle) == (True, Validity.VALID,
                                                        Lifecycle.IMPLEMENTED)

    # native language carried untouched
    ru = _claim(lang="ru")
    assert ru.lang == "ru"

    part = partition_grounding([(_claim(), gt), (_claim(False), unfaithful)])
    assert len(part["grounding"]) == 1 and len(part["excluded"]) == 1

    print("SELF-TEST PASS: three axes never collapsed; grounding-truth = "
          "faithful+valid+implemented; WIP/retired/unfaithful/unknown excluded with "
          "reasons; native language untouched.")
    sys.exit(0)

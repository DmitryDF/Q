"""AdjacentPointsPort — AI-judgment adapter for Ultra Deep adjacent-points (S5).

Spec
----
`~/.claude/rules/research-scope-framing.md` §"Adjacent-Points-Generation Contract":
Ultra Deep surfaces candidate adjacent points the user did not ask about
(e.g. for EV research: tire wear, resale value, insurance differences). Three
load-bearing rules govern this port:

  1. **Generation-not-verification.** Candidates are NOT fact-checked. The user
     is the only authority on whether a candidate is relevant. The Step 2.5
     producer-never-verifies check does not apply because the AI is generating
     possibilities, not asserting facts.
  2. **5-initial + recommend-more.** Initial display = 5 candidates. The
     `{{recommend_more_label}}` option triggers another AI pass; editorial
     total cap = ~15 candidates per session (calibratable).
  3. **No Step 2.5 re-check on toggling.** Selecting candidates from the
     checklist does NOT change the scope-quality predicate Step 2.5 verifies.
     Only Edit on the base scope triggers Step 2.5 re-check.

Architecture
------------
Hexagonal port-and-adapter per `~/.claude/rules/code_first_architecture.md`:
the flow controller depends on the port; concrete adapters (Fake for S5
tests, Claude-backed for production) plug into the bootstrap site without
touching the controller (Evolution Test).

Return shape is a discriminated union (`AdjacentPointsOutcome = Result | Error`).
The flow controller switches on the union type and surfaces a degraded-path
AskUserQuestion on an error outcome. No exceptions cross the port boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Sequence, Union


DEFAULT_INITIAL_COUNT = 5
DEFAULT_TOTAL_CAP = 15


@dataclass(frozen=True)
class AdjacentPointsIntake:
    """Bounded input to the adjacent-points adapter.

    The flow controller assembles this from the approved base scope so the
    generation pass can stay coherent with what the user already asked about.
    Producer-never-verifies starts here: nothing outside this struct reaches
    the adapter.
    """

    user_query: str
    base_angles: Sequence[str] = field(default_factory=tuple)
    base_focused_questions: Sequence[str] = field(default_factory=tuple)
    conversation_language: str = "en"
    # already_surfaced is passed back on subsequent passes ("recommend more")
    # so the adapter does not propose duplicates.
    already_surfaced: Sequence[str] = field(default_factory=tuple)
    requested_count: int = DEFAULT_INITIAL_COUNT


@dataclass(frozen=True)
class AdjacentPointsResult:
    """Success outcome: a list of candidate adjacent points."""

    kind: str = "result"
    candidates: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class AdjacentPointsError:
    """Failure outcome — surfaced as a degraded path, never raised."""

    kind: str = "error"
    reason: str = ""
    code: str = "generation_failed"


AdjacentPointsOutcome = Union[AdjacentPointsResult, AdjacentPointsError]


def is_result(outcome: AdjacentPointsOutcome) -> bool:
    return isinstance(outcome, AdjacentPointsResult)


def is_error(outcome: AdjacentPointsOutcome) -> bool:
    return isinstance(outcome, AdjacentPointsError)


class AdjacentPointsPort(ABC):
    """Abstract port. One method: produce candidates from an intake."""

    @abstractmethod
    def generate(self, intake: AdjacentPointsIntake) -> AdjacentPointsOutcome:
        """Return either an AdjacentPointsResult or an AdjacentPointsError.

        Implementations must never raise — surface failure as an Error outcome
        so the flow controller can offer a degraded-path AskUserQuestion.
        """


class FakeAdjacentPointsAdapter(AdjacentPointsPort):
    """Deterministic walking-skeleton adapter for the S5 integration test.

    Generates candidates derived from the user query so the test artifact is
    diff-friendly. Honors `already_surfaced` to support the recommend-more
    pass without duplicates. Pure function of inputs — no I/O, no clock.

    Construction parameters
    -----------------------
    canned_pool : optional pre-baked list of candidates. Defaults to a deterministic
        pool derived from the intake query (12 entries — enough for one initial
        pass plus two recommend-more passes before hitting the editorial cap).
    fail_with : optional `AdjacentPointsError`. When set, every call returns it
        — used for failure-path tests (degraded-path UI exercise).
    """

    _DEFAULT_LENSES = (
        "regulatory landscape",
        "second-order economic effects",
        "geographic variation",
        "historical precedents",
        "ethical considerations",
        "infrastructure dependencies",
        "long-term maintenance costs",
        "consumer perception trends",
        "supply-chain risk",
        "labor-market impact",
        "environmental footprint",
        "tax and incentive structures",
    )

    def __init__(
        self,
        canned_pool: Optional[Sequence[str]] = None,
        fail_with: Optional[AdjacentPointsError] = None,
    ) -> None:
        self._canned_pool = canned_pool
        self._fail = fail_with

    def generate(self, intake: AdjacentPointsIntake) -> AdjacentPointsOutcome:
        if self._fail is not None:
            return self._fail
        pool = list(self._canned_pool) if self._canned_pool is not None else self._default_pool(intake)
        seen = set(intake.already_surfaced or ())
        fresh = [c for c in pool if c not in seen]
        take = max(0, int(intake.requested_count or DEFAULT_INITIAL_COUNT))
        return AdjacentPointsResult(candidates=tuple(fresh[:take]))

    @staticmethod
    def _default_pool(intake: AdjacentPointsIntake) -> list:
        query = (intake.user_query or "unspecified topic").strip()
        return [f"{query} — {lens}" for lens in FakeAdjacentPointsAdapter._DEFAULT_LENSES]

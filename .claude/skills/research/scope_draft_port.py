"""ScopeDraftPort — AI-judgment adapter for the /research scope-framing UI (S3).

Hexagonal pattern per ~/.claude/rules/code_first_architecture.md:81-108.
The port sits at the boundary between the flow controller (code) and the
AI judgment surface (Claude in S5, fake in S3). Construction follows the
Cockburn 4-step nano-increment (`code_first_architecture.md:281-291`):

    test-to-test  → real-to-test  → test-to-real  → real-to-real

S3 ships only the Fake adapter (`FakeScopeDraftAdapter`). The production
Claude-backed adapter graduates in S5; it implements the same port and is
swapped in at the bootstrap site (Skills/research-en.md flow controller).

Return shape is a discriminated union (`ScopeDraftOutcome = Output | Error`).
The flow controller switches on the union type and surfaces a degraded-path
AskUserQuestion (Retry / Re-route / Cancel) on `ScopeDraftError`
(UX Decision #2). No exceptions cross the port boundary.

Q11 — user-facing message style: this module never bakes in user-facing
strings. The `reason` field on `ScopeDraftError` is a free-form plain-text
sentence the flow controller may surface verbatim, but slot keys
(`error_drafter_failed`, etc.) live in the Localization Table of
~/.claude/rules/research-scope-framing.md, not here.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Union


# --------------------------------------------------------------------------- #
# Input — intake passed from the flow controller into the port.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScopeDraftIntake:
    """Bounded input to the scope-draft adapter.

    The flow controller assembles this from the user's `/research` invocation
    + the last-N user messages walked back from the session JSONL. The intake
    is the ONLY data the port sees — no shared context, no session state,
    no manifest access. Producer-never-verifies discipline starts here.
    """

    routing_path: str  # "ninja" | "deep" | "ultra_deep" | "internal_kb" | "autonomous"
    user_query: str
    last_user_messages: Sequence[str] = field(default_factory=tuple)
    conversation_language: str = "en"
    selected_languages: Sequence[str] = ("en",)


# --------------------------------------------------------------------------- #
# Output — discriminated union (Output | Error).
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScopeDraftOutput:
    """Success outcome: a structured scope ready for Step 2.5 + Step 3."""

    kind: str = "output"
    angles: Sequence[str] = field(default_factory=tuple)
    focused_questions: Sequence[str] = field(default_factory=tuple)
    # Per-language search terms. For multi-language runs each selected
    # language gets its own list (or `None` when the locale-native sub-pass
    # could not produce one — UX Decision #5).
    search_terms: Dict[str, Optional[Sequence[str]]] = field(default_factory=dict)
    suggested_depth: str = "standard"
    where_to_search: Sequence[str] = field(default_factory=tuple)
    languages: Sequence[str] = ("en",)

    def to_dict(self) -> dict:
        """Plain-dict view used by Step 2.5 bounded-artifact rendering."""
        return {
            "kind": self.kind,
            "angles": list(self.angles),
            "focused_questions": list(self.focused_questions),
            "search_terms": {
                lang: (list(terms) if terms is not None else None)
                for lang, terms in self.search_terms.items()
            },
            "suggested_depth": self.suggested_depth,
            "where_to_search": list(self.where_to_search),
            "languages": list(self.languages),
        }


@dataclass(frozen=True)
class ScopeDraftError:
    """Failure outcome — surfaced as a degraded path, never raised.

    `reason` is plain-English, suitable for display verbatim. `code` is a
    machine identifier the flow controller can branch on (e.g. log/tracking).
    """

    kind: str = "error"
    reason: str = ""
    code: str = "drafter_failed"


ScopeDraftOutcome = Union[ScopeDraftOutput, ScopeDraftError]


def is_output(outcome: ScopeDraftOutcome) -> bool:
    """Type guard for the discriminated union."""
    return isinstance(outcome, ScopeDraftOutput)


def is_error(outcome: ScopeDraftOutcome) -> bool:
    """Type guard for the discriminated union."""
    return isinstance(outcome, ScopeDraftError)


# --------------------------------------------------------------------------- #
# Port — the abstract boundary between flow controller and AI adapter.
# --------------------------------------------------------------------------- #

class ScopeDraftPort(ABC):
    """Abstract port. One method: produce an outcome from an intake.

    Implementations:
      * FakeScopeDraftAdapter — S3 walking skeleton; deterministic canned scope.
      * (S5) ClaudeScopeDraftAdapter — production; one autonomous Claude turn.
    """

    @abstractmethod
    def draft(self, intake: ScopeDraftIntake) -> ScopeDraftOutcome:
        """Return either a ScopeDraftOutput or a ScopeDraftError. Never raise."""


# --------------------------------------------------------------------------- #
# Fake adapter — deterministic, no I/O, no network.
# --------------------------------------------------------------------------- #

class FakeScopeDraftAdapter(ScopeDraftPort):
    """Walking-skeleton adapter — deterministic canned scope for Ninja.

    Purpose
    -------
    Lets S3 wire the entire flow end-to-end (routing → draft → Step 2.5 →
    approval → flip → unblock) without depending on a live Claude call.
    The production adapter (S5) replaces this at the bootstrap site without
    touching the flow controller (Cockburn Evolution Test).

    Construction parameters
    -----------------------
    canned_output : optional preset ScopeDraftOutput. Defaults to a
        well-formed Ninja scope keyed off the intake query.
    fail_with : optional ScopeDraftError. When set, every call returns this
        error — used for failure-path tests (drafter-failure → degraded path).

    Determinism
    -----------
    `draft()` is a pure function of the intake + the construction
    parameters. No I/O, no clock, no randomness — tests can pin the outcome.
    """

    def __init__(
        self,
        canned_output: Optional[ScopeDraftOutput] = None,
        fail_with: Optional[ScopeDraftError] = None,
    ) -> None:
        self._canned = canned_output
        self._fail = fail_with

    def draft(self, intake: ScopeDraftIntake) -> ScopeDraftOutcome:
        if self._fail is not None:
            return self._fail
        if self._canned is not None:
            return self._canned
        return self._default_for(intake)

    @staticmethod
    def _default_for(intake: ScopeDraftIntake) -> ScopeDraftOutput:
        """Build a deterministic scope from the intake.

        The Q15 source-tier axis is reflected in `suggested_depth`: ninja and
        autonomous default to "standard" external; deep / ultra_deep default
        to "deep". The flow controller passes this through to the r0_intake
        payload so the downstream pipeline can branch on tier without a
        separate CLI flag.
        """
        # Query echo keeps the test artifact diff-friendly and lets the
        # Step 2.5 checker see that the draft is consistent with the ask.
        query = intake.user_query.strip() or "unspecified topic"
        langs = tuple(intake.selected_languages or ("en",))
        terms: Dict[str, Optional[Sequence[str]]] = {
            lang: (
                f"{query} overview",
                f"{query} primary sources",
                f"{query} expert commentary",
            )
            for lang in langs
        }
        depth = "deep" if intake.routing_path in ("deep", "ultra_deep") else "standard"
        return ScopeDraftOutput(
            angles=(
                f"What is the current state of {query}?",
                f"What are the primary debates around {query}?",
                f"What practical implications follow from {query}?",
            ),
            focused_questions=(
                f"Who are the recognized authorities on {query}?",
                f"What evidence is strongest for and against the dominant view of {query}?",
                f"What changed about {query} in the last 12 months?",
            ),
            search_terms=terms,
            suggested_depth=depth,
            where_to_search=(
                "primary documents and official statements",
                "peer-reviewed or editorially-vetted sources",
                "recent news from established outlets",
            ),
            languages=langs,
        )

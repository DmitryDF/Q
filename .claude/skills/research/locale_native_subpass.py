"""LocaleNativeSubpass — per-language search-terms regeneration (S6).

For multi-language runs the shared scope core (angles, focused-questions,
depth-tier, where-to-search) is produced in the conversation language by
ScopeDraftPort. The search-terms slot is regenerated per selected language
by this locale-native sub-pass (UX Decision #5).

Return shape (mirrors the UX Decision #5 contract):
  success → LocaleNativeResult(terms=[...])
  failure → LocaleNativeError(terms=None, reason="...", code="...")

The flow controller applies the sub-pass results to ScopeDraftOutput:
  lang → terms   (success)
  lang → None    (failure — slot marked with {{error_locale_native_failed}})

Failed slots do not block other languages — the run continues with the
surviving languages.

Architecture
------------
Hexagonal port-and-adapter per ~/.claude/rules/code_first_architecture.md.
Dependency-inject the model invocation as a callable (prompt: str) -> str
(same ModelInvoker pattern as S5's ClaudeScopeDraftAdapter) so the adapter
is exercised in tests without a live Claude call.

Cockburn 4-step nano-increment:
  test-to-test  — FakeLocaleNativeAdapter + LocaleNativeIntake (no engine)
  real-to-test  — ClaudeLocaleNativeAdapter with fixture invoker (canned JSON)
  test-to-real  — apply_locale_native_subpass with FakeAdapter + ScopeDraftOutput
  real-to-real  — apply_locale_native_subpass with production ClaudeAdapter (canned)

Q11 — user-facing message style
--------------------------------
Error reason strings are plain English; they never name r0_intake,
r1_scope_approved, cycle_id, caller_skill, or other pipeline identifiers.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Union

from research.scope_draft_port import ScopeDraftOutput


# --------------------------------------------------------------------------- #
# Data types
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScopeCore:
    """Conversation-language scope core passed to the locale-native sub-pass.

    The sub-pass sees only this bounded struct — no session state, no manifest.
    Producer-never-verifies discipline starts here.
    """

    angles: Sequence[str] = field(default_factory=tuple)
    focused_questions: Sequence[str] = field(default_factory=tuple)
    suggested_depth: str = "standard"
    where_to_search: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class LocaleNativeIntake:
    """Bounded input to the locale-native adapter.

    The flow controller assembles this from the approved scope core so the
    sub-pass can generate coherent locale-native search terms.
    """

    scope_core: ScopeCore
    target_language: str          # e.g. "de", "ru"
    conversation_language: str    # e.g. "en"
    user_query: str = ""


@dataclass(frozen=True)
class LocaleNativeResult:
    """Success outcome: locale-native search terms for the target language."""

    kind: str = "result"
    terms: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class LocaleNativeError:
    """Failure outcome — surfaced as a degraded path, never raised.

    `terms` is None per the UX Decision #5 contract:
      sub-pass returns {terms: null, reason: ...} on failure.
    `reason` is plain English, suitable for display verbatim.
    """

    kind: str = "error"
    terms: None = None
    reason: str = ""
    code: str = "locale_native_failed"


LocaleNativeOutcome = Union[LocaleNativeResult, LocaleNativeError]


def is_result(outcome: LocaleNativeOutcome) -> bool:
    return isinstance(outcome, LocaleNativeResult)


def is_error(outcome: LocaleNativeOutcome) -> bool:
    return isinstance(outcome, LocaleNativeError)


# --------------------------------------------------------------------------- #
# Port
# --------------------------------------------------------------------------- #

class LocaleNativePort(ABC):
    """Abstract port for per-language search-terms regeneration."""

    @abstractmethod
    def regenerate(self, intake: LocaleNativeIntake) -> LocaleNativeOutcome:
        """Return either a LocaleNativeResult or a LocaleNativeError. Never raise."""


# --------------------------------------------------------------------------- #
# Fake adapter
# --------------------------------------------------------------------------- #

_DEFAULT_TERMS_BY_LANG: Dict[str, Sequence[str]] = {
    "de": ("Überblick", "Hauptquellen", "Expertenkommentare"),
    "ru": ("обзор", "основные источники", "экспертные комментарии"),
    "fr": ("vue d'ensemble", "sources principales", "commentaires d'experts"),
    "es": ("resumen", "fuentes principales", "comentarios de expertos"),
}


class FakeLocaleNativeAdapter(LocaleNativePort):
    """Deterministic walking-skeleton adapter for S6 integration tests.

    Returns pre-baked terms per target language. Honors the fail_with
    knob for failure-path tests (degraded-path exercise). Pure function
    of inputs — no I/O, no clock, no network.

    Construction parameters
    -----------------------
    canned_terms: optional {lang: [terms...]} preset. Defaults to built-in
        per-language defaults for "de", "ru", "fr", "es".
    fail_with: optional LocaleNativeError. When set, every call returns this
        error — used for failure-path tests.
    """

    def __init__(
        self,
        canned_terms: Optional[Dict[str, Sequence[str]]] = None,
        fail_with: Optional[LocaleNativeError] = None,
    ) -> None:
        self._canned = canned_terms or {}
        self._fail = fail_with

    def regenerate(self, intake: LocaleNativeIntake) -> LocaleNativeOutcome:
        if self._fail is not None:
            return self._fail

        lang = intake.target_language
        if lang in self._canned:
            return LocaleNativeResult(terms=self._canned[lang])

        if lang in _DEFAULT_TERMS_BY_LANG:
            query = (intake.user_query or "topic").strip()
            base = _DEFAULT_TERMS_BY_LANG[lang]
            return LocaleNativeResult(terms=tuple(f"{query} {t}" for t in base))

        # Unknown language — degrade gracefully.
        return LocaleNativeError(
            reason=f"Search terms for this language could not be generated.",
            code="unknown_language",
        )


# --------------------------------------------------------------------------- #
# Production adapter
# --------------------------------------------------------------------------- #

ModelInvoker = Callable[[str], str]

_LOCALE_NATIVE_PROMPT_HEADER = (
    "You are generating search terms in a specific target language for a /research "
    "scope-framing UI. The research topic and its core scope are provided in the "
    "conversation language. Produce locale-native search terms in the TARGET LANGUAGE "
    "that a native speaker would use to find information on this topic.\n\n"
    "Return ONE JSON object — no preamble, no markdown fences — with EXACTLY one of:\n"
    "  {\"terms\": [\"term1\", \"term2\", \"term3\"]}\n"
    "  {\"terms\": null, \"reason\": \"<plain English explanation>\"}\n"
    "Do not add other keys. Output JSON only."
)


def _build_locale_native_prompt(intake: LocaleNativeIntake) -> str:
    """Assemble the bounded prompt for the locale-native sub-pass."""
    angles = "\n".join(f"- {a}" for a in intake.scope_core.angles) or "- (none)"
    fqs = "\n".join(f"- {q}" for q in intake.scope_core.focused_questions) or "- (none)"
    return (
        f"{_LOCALE_NATIVE_PROMPT_HEADER}\n\n"
        f"Target language: {intake.target_language}\n"
        f"Conversation language: {intake.conversation_language}\n"
        f"User query (verbatim): {intake.user_query}\n\n"
        f"Approved research angles:\n{angles}\n\n"
        f"Focused questions:\n{fqs}\n"
    )


def _try_parse_json_response(text: str) -> Optional[dict]:
    """Tolerantly extract a JSON object from the model response."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _live_model_invoker(prompt: str) -> str:  # pragma: no cover - production wire
    """Default wire to the Anthropic SDK. Imported lazily to stay import-safe."""
    from anthropic import Anthropic  # type: ignore[import-not-found]

    client = Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = []
    for block in message.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


class ClaudeLocaleNativeAdapter(LocaleNativePort):
    """Production locale-native sub-pass adapter (S6).

    Dependency-injects the model invocation so tests never touch the live SDK.
    Same ModelInvoker pattern as ClaudeScopeDraftAdapter in scope_draft_adapter_claude.py.
    """

    def __init__(self, invoker: Optional[ModelInvoker] = None) -> None:
        self._invoker = invoker if invoker is not None else _live_model_invoker

    def regenerate(self, intake: LocaleNativeIntake) -> LocaleNativeOutcome:
        prompt = _build_locale_native_prompt(intake)
        try:
            response_text = self._invoker(prompt)
        except Exception as exc:  # noqa: BLE001
            return LocaleNativeError(
                reason=(
                    f"Search terms for this language could not be generated "
                    f"({type(exc).__name__})."
                ),
                code="invoker_failed",
            )

        if not isinstance(response_text, str) or not response_text.strip():
            return LocaleNativeError(
                reason="The model returned an empty response.",
                code="empty_response",
            )

        parsed = _try_parse_json_response(response_text)
        if parsed is None:
            return LocaleNativeError(
                reason="The model's response was not valid JSON.",
                code="parse_failed",
            )

        # Model may legitimately return {terms: null, reason: ...}.
        if parsed.get("terms") is None:
            reason = parsed.get("reason") or "Search terms could not be generated for this language."
            return LocaleNativeError(reason=str(reason), code="model_cannot_produce")

        terms_raw = parsed.get("terms")
        if not isinstance(terms_raw, list) or not all(isinstance(t, str) for t in terms_raw):
            return LocaleNativeError(
                reason="The model returned search terms in an unexpected format.",
                code="schema_failed",
            )

        return LocaleNativeResult(terms=tuple(terms_raw))


# --------------------------------------------------------------------------- #
# Flow controller helper — apply sub-pass results to the scope
# --------------------------------------------------------------------------- #

def apply_locale_native_subpass(
    scope_output: ScopeDraftOutput,
    adapters_by_language: Dict[str, LocaleNativePort],
    user_query: str = "",
) -> ScopeDraftOutput:
    """Apply per-language locale-native sub-passes to scope_output.

    For each language that has an adapter in adapters_by_language, regenerate
    the search-terms slot by invoking the adapter. Failures set the slot to None
    (the flow controller is responsible for marking the inline
    {{error_locale_native_failed}} message and continuing with other languages).

    Returns a new ScopeDraftOutput with updated search_terms.
    """
    conv_lang = scope_output.languages[0] if scope_output.languages else "en"
    core = ScopeCore(
        angles=scope_output.angles,
        focused_questions=scope_output.focused_questions,
        suggested_depth=scope_output.suggested_depth,
        where_to_search=scope_output.where_to_search,
    )

    updated_terms = dict(scope_output.search_terms)
    for lang, adapter in adapters_by_language.items():
        intake = LocaleNativeIntake(
            scope_core=core,
            target_language=lang,
            conversation_language=conv_lang,
            user_query=user_query,
        )
        outcome = adapter.regenerate(intake)
        if is_result(outcome):
            updated_terms[lang] = outcome.terms
        else:
            updated_terms[lang] = None  # failure slot — flow controller surfaces the error

    return ScopeDraftOutput(
        angles=scope_output.angles,
        focused_questions=scope_output.focused_questions,
        search_terms=updated_terms,
        suggested_depth=scope_output.suggested_depth,
        where_to_search=scope_output.where_to_search,
        languages=scope_output.languages,
    )

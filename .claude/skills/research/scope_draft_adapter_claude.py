"""ClaudeScopeDraftAdapter — production graduation of ScopeDraftPort (S5).

S3 shipped the FakeScopeDraftAdapter as the walking-skeleton implementation
of `ScopeDraftPort`. S5 graduates the port to a Claude-backed production
adapter that issues a single autonomous turn against the bounded intake and
returns a `ScopeDraftOutcome` discriminated union — never raising across
the port boundary (Cockburn "The application refuses to talk to anyone except
in its preferred internal language", `code_first_architecture.md:108`).

Architecture
------------
Dependency-inject the model invocation as a callable `(prompt: str) -> str`
so the adapter can be exercised in tests without a live Claude call. This
preserves the Cockburn Evolution Test — swapping the prompt strategy or the
model wire format changes only this file; nothing else moves.

Cockburn 4-step compression (per the S5 row of `dapper-coalescing-seal.md`):

  * test-to-test:   covered by S3's FakeScopeDraftAdapter unit tests
  * real-to-test:   ClaudeScopeDraftAdapter wired against a fixture caller
                    (one passing test in `test_s5_*.py`)
  * test-to-real:   FakeScopeDraftAdapter wired through this module's
                    `live_model_invoker` wrapper (one passing test)
  * real-to-real:   end-to-end Ultra Deep integration test

The "live wrapper" is `live_model_invoker` below. Through S4 it dispatched to
the Anthropic SDK (`from anthropic import Anthropic`); the `anthropic` package
is not installed and no API key is configured on this machine, so every real
call raised `ImportError` and every typed `/research` scope draft degraded to
`invoker_failed` deterministically (research-entry-point-enforcement S4 round-2,
ITEM 1). As of that fix it dispatches to a `claude --print` subprocess instead
— see `live_model_invoker`'s own docstring for the wire and the model-pin
rationale. It remains a thin pluggable boundary so tests can substitute a stub
without spawning a real process.

Q11 — user-facing message style
-------------------------------
This adapter never bakes in user-facing strings. `ScopeDraftError.reason`
carries a plain-English sentence; the flow controller surfaces it verbatim
through the rule file's Localization Table (`error_drafter_failed`).
"""

from __future__ import annotations

import json
from typing import Callable, Dict, Optional, Sequence

if __name__ == "__main__":  # pragma: no cover - CLI wire
    # BLOCKER 1 fix (research-entry-point-enforcement S4 review): this guard
    # MUST run before the `research.scope_draft_port` import below, not after
    # it. The module imports its port as `research.scope_draft_port`, so the
    # skills dir (the package parent) must already be on `sys.path` when the
    # import statement executes. Putting this in the trailing
    # `if __name__ == "__main__":` block at the bottom of the file (as a prior
    # revision did) is a no-op: Python evaluates the whole module body,
    # including the import at the top, before it ever reaches that block, so
    # the path was never fixed in time and every CLI invocation raised
    # `ModuleNotFoundError: No module named 'research'` before printing a
    # single byte of output.
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research.scope_draft_port import (
    ScopeDraftError,
    ScopeDraftIntake,
    ScopeDraftOutcome,
    ScopeDraftOutput,
    ScopeDraftPort,
)


# --------------------------------------------------------------------------- #
# Live model invoker — the production wire boundary.
# --------------------------------------------------------------------------- #

ModelInvoker = Callable[[str], str]


# Explicit model pin (research-entry-point-enforcement S4 round-2, ITEM 1,
# operator decision). A bare `claude --print` does NOT inherit the parent
# session's model — it takes the CLI binary's own default (Opus 5 as of this
# writing), verified from both an Opus 5 and a Sonnet 5 parent session, both
# children reporting `claude-opus-5[1m]`. That default is an INVISIBLE pin: it
# silently drifts on every binary upgrade and bills a bounded JSON-drafting
# task at Opus rates for no accuracy benefit this task needs. The operator
# chose an explicit pin over the bare default for exactly that reason. The id
# below was probed directly before this fix landed — `claude --print --model
# claude-sonnet-5 "Reply with exactly: OK"` — and returned exit 0 with the
# literal reply on stdout, so the id is confirmed accepted by this CLI build
# (v2.1.273), not guessed.
_LIVE_MODEL_ID = "claude-sonnet-5"

# Picked comfortably above the measured latency: a trivial one-word reply
# probed at ~8s, and the full scope-draft prompt (the realistic shape this
# adapter actually sends) probed at ~9.7s. Neither `timeout` nor `gtimeout`
# exists on this machine, so the ceiling is enforced by
# `subprocess.run(..., timeout=...)` rather than a shell wrapper.
_LIVE_MODEL_TIMEOUT_SECONDS = 120


def live_model_invoker(prompt: str) -> str:  # pragma: no cover - production wire
    """Default wire to a `claude --print` subprocess.

    Through S4 this function dispatched to the Anthropic SDK
    (`from anthropic import Anthropic`), which requires the `anthropic`
    package and an `ANTHROPIC_API_KEY` — neither is present on this machine,
    so every real call raised `ImportError` before a single token was sent.
    research-entry-point-enforcement S4 round-2 (ITEM 1, operator decision)
    replaces that wire with a subprocess call to the `claude` CLI already
    installed in this environment, pinned to an explicit model (see
    `_LIVE_MODEL_ID` above) rather than the CLI's bare (and invisibly
    drifting) default.

    The flag shape mirrors the harness's own `research-scope-gate.sh`-adjacent
    dispatch pattern and was re-verified independently by three agents before
    this fix: `--safe-mode` (skip CLAUDE.md/skills/hooks — a clean, untainted
    call) plus `--tools "" --allowedTools ""` (no tool use; this is a bounded
    text-generation call, not an agentic one). Those two options are
    *variadic* (`--tools <tools...>` / `--allowedTools <tools...>`), so
    without a `--` separator the CLI's own argument parser silently swallows
    the prompt into the tools array instead of treating it as the prompt —
    reproduced directly while building this fix (exit 1, "Input must be
    provided either through stdin or as a prompt argument when using
    --print"). The `--` below is load-bearing, not decorative.

    Only stdout is read and returned. Stderr carries permission-rule
    diagnostics unrelated to this call (this harness's own `settings.json`
    wildcard-permission warnings, observed on every probe run) and must never
    reach the JSON parser downstream.

    Never raises deliberately — `subprocess.run`'s own failure modes
    (`TimeoutExpired`, `FileNotFoundError` if the binary is missing) and a
    non-zero exit here all propagate as ordinary exceptions, which `draft()`
    already catches and degrades to a `ScopeDraftError(code="invoker_failed")`
    outcome. This function does not need its own try/except — the port
    boundary's never-raise discipline is enforced one call frame up, exactly
    as it protected the old SDK path.

    In test mode, the adapter accepts an injected invoker (a callable
    matching this signature) so no real process is spawned. The SDK path is
    removed rather than kept as a fallback: the operator's decision was to
    repoint the live wire, not to add a second one, and a dead SDK branch a
    future caller might trip over unknowingly is worse than none. A future
    model-less/API-based caller can re-add an SDK-backed invoker as an
    alternative to inject through the same `ModelInvoker` seam — nothing about
    this adapter's contract requires the live wire specifically to be a
    subprocess call.
    """
    import subprocess

    result = subprocess.run(
        [
            "claude", "--print",
            "--model", _LIVE_MODEL_ID,
            "--safe-mode",
            "--tools", "",
            "--allowedTools", "",
            "--",
            prompt,
        ],
        capture_output=True,
        text=True,
        timeout=_LIVE_MODEL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude --print exited {result.returncode}"
        )
    return result.stdout


# --------------------------------------------------------------------------- #
# Prompt assembly.
# --------------------------------------------------------------------------- #

_SCOPE_PROMPT_HEADER = (
    "You are drafting a research scope for the /research scope-framing UI. "
    "Return ONE JSON object — no preamble, no markdown fences — with EXACTLY "
    "these keys:\n"
    "  angles: array of 3-5 strings\n"
    "  focused_questions: array of 3-5 strings\n"
    "  search_terms: object mapping each requested language code to an array "
    "of 3-5 strings (or null if you cannot produce locale-native terms)\n"
    "  suggested_depth: one of \"standard\" or \"deep\"\n"
    "  where_to_search: array of 2-5 strings naming source categories\n"
    "  languages: array mirroring the requested language codes\n"
    "Do not add other keys. Do not wrap the object in any envelope. "
    "Do not include comments. Output JSON only."
)


def _build_prompt(intake: ScopeDraftIntake) -> str:
    """Assemble the bounded prompt the adapter sends to the model.

    The intake is the ONLY data that reaches the model. No session state, no
    project context, no manifest — producer-never-verifies discipline.
    """
    last_msgs = "\n".join(f"- {m}" for m in (intake.last_user_messages or ())) or "- (no prior user messages)"
    selected_langs = ", ".join(intake.selected_languages or ("en",))
    depth_hint = (
        "deep" if intake.routing_path in ("deep", "ultra_deep") else "standard"
    )
    return (
        f"{_SCOPE_PROMPT_HEADER}\n\n"
        f"Routing path: {intake.routing_path}\n"
        f"Suggested depth tier: {depth_hint} (you may override if the topic "
        f"warrants).\n"
        f"Conversation language: {intake.conversation_language}\n"
        f"Selected languages for search-terms: {selected_langs}\n\n"
        f"User query (verbatim):\n{intake.user_query}\n\n"
        f"Last user messages preceding the invocation (oldest first):\n"
        f"{last_msgs}\n"
    )


# --------------------------------------------------------------------------- #
# Adapter — graduates ScopeDraftPort from Fake (S3) to production (S5).
# --------------------------------------------------------------------------- #

class ClaudeScopeDraftAdapter(ScopeDraftPort):
    """Production `ScopeDraftPort` implementation.

    One autonomous Claude turn per `draft()` call. Surfaces every failure
    (network, parse, schema) as a `ScopeDraftError`; never raises.
    """

    def __init__(self, invoker: Optional[ModelInvoker] = None) -> None:
        # Default to the live wire; tests pass a stub.
        self._invoker = invoker if invoker is not None else live_model_invoker

    def draft(self, intake: ScopeDraftIntake) -> ScopeDraftOutcome:
        prompt = _build_prompt(intake)
        try:
            response_text = self._invoker(prompt)
        except Exception as exc:  # noqa: BLE001 — any wire failure → degraded path
            return ScopeDraftError(
                reason=(
                    "The scope draft could not be generated because the model "
                    f"call did not return a response ({type(exc).__name__})."
                ),
                code="invoker_failed",
            )

        if not isinstance(response_text, str) or not response_text.strip():
            return ScopeDraftError(
                reason="The model returned an empty response.",
                code="empty_response",
            )

        parsed = _try_parse_json(response_text)
        if parsed is None:
            return ScopeDraftError(
                reason="The model's response was not valid JSON.",
                code="parse_failed",
            )

        try:
            return _coerce_output(parsed, intake)
        except _ScopeShapeError as exc:
            return ScopeDraftError(reason=str(exc), code="schema_failed")


# --------------------------------------------------------------------------- #
# Parsing helpers (kept private; flow controller never sees them).
# --------------------------------------------------------------------------- #

class _ScopeShapeError(ValueError):
    """Raised internally when the parsed payload does not match the contract."""


def _try_parse_json(text: str) -> Optional[dict]:
    """Tolerantly extract a JSON object from the model response.

    Models occasionally wrap JSON in markdown fences even when told not to.
    We strip a single leading/trailing code fence if present, then parse.

    research-entry-point-enforcement S4 round-6 MINOR 1: fence-stripping alone
    is not enough. A repeated-invocation measurement (11 live calls) found 1
    reply with no fence at all and no bare JSON either — a preamble/trailing-
    prose shape ("Here is the drafted scope: {...}\\n\\nLet me know if you'd
    like changes.") that fails `json.loads` outright and used to fall straight
    through to the `parse_failed` degraded path even though a well-formed
    object sat right there in the reply. When the (possibly fence-stripped)
    text does not parse as JSON on its own, this now falls back to extracting
    the first balanced top-level `{...}` object from anywhere in the text —
    still only a fallback: a reply with no `{` at all, or an unbalanced one
    (truncated mid-object), still returns None and still degrades.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the first line (fence) and the trailing fence line.
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass

    candidate = _extract_balanced_json_object(stripped)
    if candidate is None:
        return None
    try:
        data = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _extract_balanced_json_object(text: str) -> Optional[str]:
    """Return the first balanced `{...}` substring in `text` (matching brace
    depth, string-aware so a `{`/`}` inside a quoted JSON string value does
    not desync the count), or None if no balanced object is found.

    String-awareness matters here specifically: a drafted focused question
    quoting code or configuration text can legitimately contain a brace
    character inside a JSON string value, and the naive "stop at the first
    `}`" approach would truncate the object there instead of at its real
    close.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _coerce_output(data: dict, intake: ScopeDraftIntake) -> ScopeDraftOutput:
    angles = _coerce_str_seq(data, "angles", minimum=1)
    focused_questions = _coerce_str_seq(data, "focused_questions", minimum=1)

    terms_raw = data.get("search_terms")
    if not isinstance(terms_raw, dict):
        raise _ScopeShapeError("'search_terms' must be a JSON object keyed by language code.")
    terms: Dict[str, Optional[Sequence[str]]] = {}
    for lang in (intake.selected_languages or ("en",)):
        slot = terms_raw.get(lang)
        if slot is None:
            terms[lang] = None
            continue
        if not isinstance(slot, list) or not all(isinstance(x, str) for x in slot):
            raise _ScopeShapeError(
                f"'search_terms[{lang}]' must be a list of strings or null."
            )
        terms[lang] = tuple(slot)

    suggested_depth = data.get("suggested_depth", "standard")
    if suggested_depth not in ("standard", "deep"):
        raise _ScopeShapeError("'suggested_depth' must be 'standard' or 'deep'.")

    where_to_search = _coerce_str_seq(data, "where_to_search", minimum=1)

    langs_raw = data.get("languages")
    if not isinstance(langs_raw, list) or not all(isinstance(x, str) for x in langs_raw):
        raise _ScopeShapeError("'languages' must be a list of language codes (strings).")

    return ScopeDraftOutput(
        angles=tuple(angles),
        focused_questions=tuple(focused_questions),
        search_terms=terms,
        suggested_depth=suggested_depth,
        where_to_search=tuple(where_to_search),
        languages=tuple(langs_raw),
    )


def _coerce_str_seq(data: dict, key: str, minimum: int = 1) -> Sequence[str]:
    seq = data.get(key)
    if not isinstance(seq, list) or not all(isinstance(x, str) for x in seq):
        raise _ScopeShapeError(f"'{key}' must be a list of strings.")
    if len(seq) < minimum:
        raise _ScopeShapeError(f"'{key}' must contain at least {minimum} entr(y/ies).")
    return seq


# --------------------------------------------------------------------------- #
# Production entry point (research-entry-point-enforcement S4 — finding 36).
#
# Until S4 this module had NO production caller. Its only construction site was
# a test fixture double, so the framing flow's "drafted proposal" came from a
# test adapter or from nothing at all — the terminating condition finding 36
# states is "a run that reaches the framing flow without a written scope
# receives a drafted proposal from PRODUCTION CODE rather than from a test
# double". The CLI below is that caller.
#
# Shape follows the harness convention (`research_pipeline.py`,
# `skills/execute-plan/run.py`): one JSON object on stdin, one JSON object on
# stdout, the outcome on the body rather than in the exit code for the
# degraded path — a drafter failure is a UX branch the flow controller handles
# (`error_drafter_failed` → retry / re-route / cancel), NOT a crash. Exit 2 is
# reserved for a malformed invocation, which is a caller bug and not a draft
# outcome.
# --------------------------------------------------------------------------- #

def draft_from_payload(payload: dict, invoker: Optional[ModelInvoker] = None) -> dict:
    """Draft a scope from a plain JSON-shaped intake. Pure plumbing.

    Returns the `ScopeDraftOutcome` as a dict, discriminated by `kind`
    ("output" | "error"). Never raises across this boundary — a bad intake is
    returned as an error outcome, exactly as a failed model turn is, because
    the flow controller has one degraded path and not two.
    """
    try:
        intake = ScopeDraftIntake(
            routing_path=payload["routing_path"],
            user_query=payload["user_query"],
            last_user_messages=tuple(payload.get("last_user_messages") or ()),
            conversation_language=payload.get("conversation_language", "en"),
            selected_languages=tuple(payload.get("selected_languages") or ("en",)),
        )
    except (KeyError, TypeError) as exc:
        return {
            "kind": "error",
            "reason": "The scope draft could not be generated: malformed intake "
                      f"({exc}).",
            "code": "malformed_intake",
        }

    outcome = ClaudeScopeDraftAdapter(invoker=invoker).draft(intake)
    if isinstance(outcome, ScopeDraftOutput):
        return {
            "kind": "output",
            "angles": list(outcome.angles),
            "focused_questions": list(outcome.focused_questions),
            "search_terms": {
                lang: (list(terms) if terms is not None else None)
                for lang, terms in outcome.search_terms.items()
            },
            "suggested_depth": outcome.suggested_depth,
            "where_to_search": list(outcome.where_to_search),
            "languages": list(outcome.languages),
        }
    return {
        "kind": "error",
        "reason": outcome.reason,
        "code": outcome.code,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        print(json.dumps({"kind": "error", "code": "bad_json",
                          "reason": f"invalid JSON on stdin: {exc}"}))
        return 2
    if not isinstance(payload, dict):
        print(json.dumps({"kind": "error", "code": "bad_json",
                          "reason": "stdin must be a JSON object"}))
        return 2

    print(json.dumps(draft_from_payload(payload), sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wire
    # sys.path is already fixed up above, before the `research.scope_draft_port`
    # import — see the guard at the top of this file.
    raise SystemExit(main())

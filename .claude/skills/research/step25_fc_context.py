"""Bounded-context extractor for Step 2.5 internal fact-check (S3).

Spec
----
~/.claude/rules/research-scope-framing.md §"Step 2.5" + locked Discovery Q7:
the Step 2.5 checker receives ONLY (a) the `/research` query, (b) the AI's
draft scope, and (c) the last N=5 user-role messages from the active session
JSONL preceding the invocation. Nothing else — no system prompts, no
project context, no manifest state, no tool transcripts.

Producer-never-verifies is structural: the checker runs in an isolated
sub-process; this module produces the only artifact the checker reads.

Architecture
------------
Code-first per ~/.claude/rules/code_first_architecture.md:81-108. The
extractor is a pure deterministic function over filesystem state — no AI
calls, no clock-dependent behavior, no randomness. Tests can pass a
synthetic JSONL path and pin every byte of the output.

Construction sequence
---------------------
Cockburn 4-step nano-increment (`code_first_architecture.md:281-291`):

    test-to-test → real-to-test → test-to-real → real-to-real

For the extractor: "real" = the production JSONL discovery walk; "test" =
an injected `transcript_path`. The 4 states are covered in
test_step25_fc_context.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence


# Default location for written artifacts. Overridable per call for tests.
DEFAULT_STATE_DIR = Path.home() / ".claude" / "state" / "plan_validation" / "adhoc"

# Default location of session transcripts. Overridable per call for tests.
DEFAULT_PROJECTS_ROOT = Path.home() / ".claude" / "projects"


@dataclass(frozen=True)
class Step25Context:
    """In-memory view of the bounded artifact (also written to disk)."""

    user_query: str
    draft_scope_markdown: str
    last_user_messages: List[str]
    transcript_path: Optional[Path] = None


def build_step25_fc_context(
    session_id: str,
    cycle_id: str,
    user_query: str,
    draft_scope_markdown: str,
    *,
    state_dir: Optional[Path] = None,
    projects_root: Optional[Path] = None,
    transcript_path: Optional[Path] = None,
    last_n: int = 5,
) -> Path:
    """Write the bounded Step 2.5 artifact and return its path.

    Parameters
    ----------
    session_id          : active session UUID; used to discover the transcript
                          when `transcript_path` is not provided.
    cycle_id            : research-pipeline cycle id; encoded into the output
                          filename so multi-cycle sessions don't clobber.
    user_query          : the `/research` query that opened the session.
    draft_scope_markdown: the AI-drafted scope rendered as a markdown block
                          (the flow controller renders it once and passes
                          the rendered string in — keeps this extractor
                          format-agnostic).
    state_dir           : output dir; defaults to DEFAULT_STATE_DIR.
    projects_root       : transcript root; defaults to DEFAULT_PROJECTS_ROOT.
    transcript_path     : direct transcript override (tests).
    last_n              : N user-role messages to include; defaults to 5.

    Returns
    -------
    Path to the written artifact:
        <state_dir>/step25_<cycle_id>.md
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("cycle_id must be a non-empty string")
    if last_n < 0:
        raise ValueError("last_n must be >= 0")

    resolved_transcript = (
        Path(transcript_path)
        if transcript_path is not None
        else _discover_transcript(session_id, projects_root or DEFAULT_PROJECTS_ROOT)
    )
    last_msgs = (
        _walk_last_user_messages(resolved_transcript, last_n)
        if resolved_transcript is not None and resolved_transcript.is_file()
        else []
    )

    out_dir = Path(state_dir) if state_dir is not None else DEFAULT_STATE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"step25_{cycle_id}.md"
    out_path.write_text(
        _render_artifact(user_query, draft_scope_markdown, last_msgs),
        encoding="utf-8",
    )
    return out_path


def build_step25_context_in_memory(
    session_id: str,
    user_query: str,
    draft_scope_markdown: str,
    *,
    projects_root: Optional[Path] = None,
    transcript_path: Optional[Path] = None,
    last_n: int = 5,
) -> Step25Context:
    """In-memory variant — useful for tests that don't need the on-disk file."""
    resolved_transcript = (
        Path(transcript_path)
        if transcript_path is not None
        else _discover_transcript(session_id, projects_root or DEFAULT_PROJECTS_ROOT)
    )
    last_msgs = (
        _walk_last_user_messages(resolved_transcript, last_n)
        if resolved_transcript is not None and resolved_transcript.is_file()
        else []
    )
    return Step25Context(
        user_query=user_query,
        draft_scope_markdown=draft_scope_markdown,
        last_user_messages=last_msgs,
        transcript_path=resolved_transcript,
    )


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #

def _discover_transcript(session_id: str, projects_root: Path) -> Optional[Path]:
    """Locate `<projects_root>/<slug>/<session_id>.jsonl` for the active session.

    The harness writes session transcripts under a cwd-derived slug dir; we
    don't know the slug a priori, so we glob. Returns None if not found
    (the extractor degrades to an empty `last_user_messages` list — the
    Step 2.5 checker still sees the query + draft, which is enough for the
    coherence predicate).
    """
    if not projects_root.is_dir():
        return None
    direct = projects_root / f"{session_id}.jsonl"
    if direct.is_file():
        return direct
    matches = sorted(projects_root.glob(f"*/{session_id}.jsonl"))
    return matches[0] if matches else None


def _walk_last_user_messages(transcript_path: Path, last_n: int) -> List[str]:
    """Return the last N real user-role messages from the JSONL transcript.

    "Real user-role" filter:
      * `type == "user"` AND `message.role == "user"`
      * `message.content` is a plain string (not a list of tool-result blocks)
      * not flagged `isMeta` (system-injected) or `isSidechain` (subagent)

    Walks the file once forward, keeps a rolling tail of size N. Returns
    them in chronological order (oldest first).
    """
    if last_n == 0:
        return []
    tail: List[str] = []
    try:
        with transcript_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not _is_real_user_message(entry):
                    continue
                content = entry["message"]["content"]
                tail.append(content)
                if len(tail) > last_n:
                    tail.pop(0)
    except OSError:
        return []
    return tail


def _is_real_user_message(entry: dict) -> bool:
    """Filter: a typed-by-human user message, not a tool result or system reminder."""
    if entry.get("type") != "user":
        return False
    if entry.get("isMeta") is True:
        return False
    if entry.get("isSidechain") is True:
        return False
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    if message.get("role") != "user":
        return False
    content = message.get("content")
    return isinstance(content, str) and bool(content.strip())


def _render_artifact(
    user_query: str,
    draft_scope_markdown: str,
    last_user_messages: Sequence[str],
) -> str:
    """Render the bounded artifact as markdown.

    Layout (stable so the Step 2.5 checker prompt can rely on it):

        # Step 2.5 — Internal fact-check bounded context

        ## /research query

        > <user_query>

        ## Draft scope

        <draft_scope_markdown>

        ## Last N user-role messages (chronological — oldest first)

        ### Message 1
        > <msg-1>

        ### Message 2
        > <msg-2>
        ...
    """
    parts = [
        "# Step 2.5 — Internal fact-check bounded context",
        "",
        "## /research query",
        "",
        _blockquote(user_query),
        "",
        "## Draft scope",
        "",
        draft_scope_markdown.rstrip() or "_(empty)_",
        "",
        "## Last user-role messages (chronological — oldest first)",
        "",
    ]
    if not last_user_messages:
        parts.append("_(no prior user-role messages found in transcript)_")
    else:
        for i, msg in enumerate(last_user_messages, start=1):
            parts.append(f"### Message {i}")
            parts.append("")
            parts.append(_blockquote(msg))
            parts.append("")
    return "\n".join(parts) + "\n"


def _blockquote(text: str) -> str:
    """Render text as a markdown blockquote, one `> ` per line."""
    text = text.rstrip()
    if not text:
        return "> _(empty)_"
    return "\n".join(f"> {line}" if line.strip() else ">" for line in text.splitlines())

"""S3 unit tests — build_step25_fc_context() bounded-context extractor.

Cockburn 4-step nano-increment coverage. For the extractor:
  * "real" side = the JSONL transcript discovery walk
  * "test" side = a synthetic JSONL injected via transcript_path

Mapping to the 4 states:
  * test-to-test : synthetic JSONL fixture, write to a tmp state_dir; both
                   inputs are test doubles
  * real-to-test : production-shaped caller (no transcript_path injection,
                   uses session_id-based discovery against a tmp projects_root)
                   against a synthetic JSONL — "real" walker, "test" inputs
  * test-to-real : test stub passes a path directly to the real walker
                   over a real JSONL on disk — adjacent to the previous test
                   but flipped (caller stubbed, walker takes a non-injected
                   path)
  * real-to-real : the S3 integration test (test_s3_walking_skeleton.py)
                   wires production discovery + a real on-disk JSONL +
                   real downstream Step 2.5 wiring.

Producer-never-verifies: the extractor sees ONLY the bounded inputs the
flow controller hands it. No manifest access, no skill state, no AI calls.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SKILLS_DIR = Path.home() / ".claude" / "skills"
sys.path.insert(0, str(SKILLS_DIR))

from research.step25_fc_context import (  # noqa: E402
    build_step25_context_in_memory,
    build_step25_fc_context,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _user_line(content: str, *, is_meta: bool = False, sidechain: bool = False) -> str:
    """Serialize one user-role transcript line per the canonical Claude Code JSONL shape."""
    entry = {
        "type": "user",
        "message": {"role": "user", "content": content},
        "uuid": f"uuid-{hash(content) & 0xffff:x}",
    }
    if is_meta:
        entry["isMeta"] = True
    if sidechain:
        entry["isSidechain"] = True
    return json.dumps(entry)


def _assistant_line(content: str) -> str:
    """An assistant-role line that the extractor must NOT pick up."""
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": content}})


def _tool_result_line() -> str:
    """A user-role line whose content is a list of blocks (tool result) — must be filtered."""
    return json.dumps(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "..."}],
            },
        }
    )


@pytest.fixture
def synthetic_jsonl(tmp_path: Path) -> Path:
    """Write a synthetic transcript covering all filter cases."""
    path = tmp_path / "session-abc.jsonl"
    path.write_text(
        "\n".join(
            [
                _user_line("first real user input"),
                _assistant_line("assistant reply 1"),
                _tool_result_line(),
                _user_line("system reminder body", is_meta=True),
                _user_line("subagent input", sidechain=True),
                _user_line("second real user input"),
                _assistant_line("assistant reply 2"),
                _user_line("third real user input"),
                _user_line("fourth real user input"),
                _user_line("fifth real user input"),
                _user_line("sixth real user input"),  # last; oldest of the kept N=5
            ]
        ),
        encoding="utf-8",
    )
    return path


# ── Cockburn step 1: test-to-test ─────────────────────────────────────────────

def test_test_to_test_synthetic_jsonl_to_tmp_state(synthetic_jsonl: Path, tmp_path: Path):
    """Synthetic transcript + tmp state dir — both inputs are test doubles."""
    out = build_step25_fc_context(
        session_id="sid-test",
        cycle_id="default",
        user_query="EV adoption Europe",
        draft_scope_markdown="**angles:** a1, a2, a3",
        state_dir=tmp_path / "adhoc",
        transcript_path=synthetic_jsonl,
        last_n=5,
    )
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "EV adoption Europe" in body
    assert "**angles:** a1, a2, a3" in body
    # Exactly N=5 real user messages kept (newest), oldest first;
    # the very first user message ("first real user input") falls outside
    # the N=5 tail and must NOT be present.
    assert "first real user input" not in body
    assert "second real user input" in body
    assert "sixth real user input" in body
    # Filters: tool results, meta reminders, sidechain inputs absent.
    assert "tool_result" not in body
    assert "system reminder body" not in body
    assert "subagent input" not in body


# ── Cockburn step 2: real-to-test ─────────────────────────────────────────────

def test_real_to_test_session_id_discovery_against_synthetic(tmp_path: Path):
    """Production caller (no transcript_path injection) uses session_id
    discovery against a tmp projects_root containing a synthetic JSONL."""
    sid = "real-to-test-session-uuid"
    projects_root = tmp_path / "projects"
    slug_dir = projects_root / "-Users-you-Some-Cwd"
    slug_dir.mkdir(parents=True)
    (slug_dir / f"{sid}.jsonl").write_text(
        _user_line("only message in this session") + "\n",
        encoding="utf-8",
    )

    out = build_step25_fc_context(
        session_id=sid,
        cycle_id="default",
        user_query="topic",
        draft_scope_markdown="draft",
        state_dir=tmp_path / "adhoc",
        projects_root=projects_root,
        last_n=5,
    )
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "only message in this session" in body


# ── Cockburn step 3: test-to-real ─────────────────────────────────────────────

def test_test_to_real_in_memory_against_disk_jsonl(synthetic_jsonl: Path):
    """Test stub (in-memory variant) against a real on-disk JSONL.

    Exercises the production walker (the same one prod will use) over a
    real file, but skips the write step — caller is a test stub.
    """
    ctx = build_step25_context_in_memory(
        session_id="ignored-because-transcript-is-injected",
        user_query="query body",
        draft_scope_markdown="...",
        transcript_path=synthetic_jsonl,
        last_n=3,
    )
    assert ctx.user_query == "query body"
    assert len(ctx.last_user_messages) == 3
    # Newest at end (chronological); oldest at start.
    assert ctx.last_user_messages[-1] == "sixth real user input"
    assert ctx.last_user_messages[0] == "fourth real user input"


# ── Boundary conditions ──────────────────────────────────────────────────────

def test_missing_transcript_degrades_to_empty_messages(tmp_path: Path):
    """Discovery miss → empty last_user_messages, artifact still written."""
    out = build_step25_fc_context(
        session_id="no-such-session",
        cycle_id="default",
        user_query="q",
        draft_scope_markdown="d",
        state_dir=tmp_path / "adhoc",
        projects_root=tmp_path / "empty-projects-root",
    )
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "no prior user-role messages found in transcript" in body


def test_artifact_filename_encodes_cycle_id(synthetic_jsonl: Path, tmp_path: Path):
    """Multi-cycle sessions don't clobber — filename keyed on cycle_id."""
    out_default = build_step25_fc_context(
        session_id="s",
        cycle_id="default",
        user_query="q",
        draft_scope_markdown="d",
        state_dir=tmp_path / "adhoc",
        transcript_path=synthetic_jsonl,
    )
    out_de = build_step25_fc_context(
        session_id="s",
        cycle_id="de",
        user_query="q",
        draft_scope_markdown="d",
        state_dir=tmp_path / "adhoc",
        transcript_path=synthetic_jsonl,
    )
    assert out_default.name == "step25_default.md"
    assert out_de.name == "step25_de.md"
    assert out_default != out_de


def test_rejects_empty_session_id():
    with pytest.raises(ValueError):
        build_step25_fc_context(
            session_id="",
            cycle_id="default",
            user_query="q",
            draft_scope_markdown="d",
        )


def test_rejects_empty_cycle_id():
    with pytest.raises(ValueError):
        build_step25_fc_context(
            session_id="s",
            cycle_id="",
            user_query="q",
            draft_scope_markdown="d",
        )


def test_last_n_zero_yields_no_messages(synthetic_jsonl: Path, tmp_path: Path):
    out = build_step25_fc_context(
        session_id="s",
        cycle_id="default",
        user_query="q",
        draft_scope_markdown="d",
        state_dir=tmp_path / "adhoc",
        transcript_path=synthetic_jsonl,
        last_n=0,
    )
    body = out.read_text(encoding="utf-8")
    assert "no prior user-role messages found in transcript" in body


def test_bounded_artifact_contains_only_three_sections(synthetic_jsonl: Path, tmp_path: Path):
    """Strict Q7 bound: the artifact body has exactly query + draft + messages.

    Nothing else (no project context, no manifest, no system prompt).
    """
    out = build_step25_fc_context(
        session_id="s",
        cycle_id="default",
        user_query="bounded query",
        draft_scope_markdown="bounded draft",
        state_dir=tmp_path / "adhoc",
        transcript_path=synthetic_jsonl,
    )
    body = out.read_text(encoding="utf-8")
    # The only ## headings allowed: query, draft, messages.
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## /research query",
        "## Draft scope",
        "## Last user-role messages (chronological — oldest first)",
    ]

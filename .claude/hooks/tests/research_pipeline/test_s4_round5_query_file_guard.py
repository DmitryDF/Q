"""S4 round-5 fix — MINOR 4: `--query-file` raises tracebacks where its
sibling `--payload-file` degrades cleanly.

`declared_read._cmd_read`'s `--query-file` read was unguarded: a missing
path raised `FileNotFoundError`, a directory raised `IsADirectoryError`, and
non-UTF8 content raised `UnicodeDecodeError` — all as unhandled tracebacks.
`research_pipeline.py advance --payload-file` (added in the same round) wraps
the equivalent read in `try/except OSError` and degrades to a clean error
instead. This matters beyond tidiness: `SKILL.md`'s "Reading the person's
own declared sources" section tells the model that a source which could not
be read "comes back refused BY NAME with its reason ... Keep it there" — an
uncaught traceback from a typo'd scratch path is confusable with that
refusal channel.

The fix wraps the read in `try/except (OSError, UnicodeDecodeError)` and
raises `SystemExit` with a clean, path-naming message — matching
`--payload-file`'s discipline (catch, name the path, degrade) without
copying its JSON-on-stdout presentation verbatim (declared_read has no
existing JSON-error convention to match; SystemExit is its own idiom
elsewhere in this codebase's CLIs for a fatal, path-naming CLI error).

This file pins the three failure cases the review reproduced, plus the
success case (round-trip + whitespace-strip), so all four are regression-
tested together.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = HOOKS_DIR.parent / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

from research import declared_read  # noqa: E402

_SESSION_ID = "s4r5-query-file-guard"


def _read_argv(query_file_path, projects_root, research_file_path):
    # MINOR 2 (round-7 fix): `research_file_path` used to be a fixed
    # relative literal (`"Thoughts/topic_RESEARCH.md"`). None of this file's
    # tests actually reach a filesystem write keyed on it today (the three
    # guard tests raise SystemExit before `_resolve_scope_from_cycle` /
    # `durable_store_for` ever run, and the success-path test mocks both
    # out) — but every call site now passes a `tmp_path`-rooted absolute
    # path anyway, so this helper carries no landmine if that changes.
    return [
        "read", _SESSION_ID, research_file_path,
        "--projects-root", str(projects_root),
        "--query-file", str(query_file_path),
    ]


def test_missing_query_file_raises_clean_system_exit_not_a_traceback(tmp_path):
    """A missing path used to raise `FileNotFoundError` (an `OSError`
    subclass) straight through `main()`. It must now degrade to a clean,
    path-naming `SystemExit`."""
    missing = tmp_path / "does-not-exist.txt"
    research_file = str(tmp_path / "topic_RESEARCH.md")
    with pytest.raises(SystemExit) as exc_info:
        declared_read.main(_read_argv(missing, tmp_path, research_file))
    message = str(exc_info.value)
    assert "--query-file" in message
    assert str(missing) in message


def test_directory_as_query_file_raises_clean_system_exit_not_a_traceback(tmp_path):
    """A directory used to raise `IsADirectoryError` (also an `OSError`
    subclass, but a different one than the missing-path case — both must be
    caught by the same guard, not just the more common one)."""
    a_directory = tmp_path / "a_directory"
    a_directory.mkdir()
    research_file = str(tmp_path / "topic_RESEARCH.md")
    with pytest.raises(SystemExit) as exc_info:
        declared_read.main(_read_argv(a_directory, tmp_path, research_file))
    message = str(exc_info.value)
    assert "--query-file" in message
    assert str(a_directory) in message


def test_non_utf8_query_file_raises_clean_system_exit_not_a_traceback(tmp_path):
    """Non-UTF8 bytes raise `UnicodeDecodeError` — NOT an `OSError` subclass,
    so a guard that only catches `OSError` would still let this one through
    as an unhandled traceback. The fix's `except (OSError,
    UnicodeDecodeError)` must catch both."""
    bad_file = tmp_path / "not_utf8.txt"
    bad_file.write_bytes(b"\xff\xfe\x00bad bytes, not valid utf-8 \xff")
    research_file = str(tmp_path / "topic_RESEARCH.md")
    with pytest.raises(SystemExit) as exc_info:
        declared_read.main(_read_argv(bad_file, tmp_path, research_file))
    message = str(exc_info.value)
    assert "--query-file" in message
    assert str(bad_file) in message


def test_valid_query_file_still_round_trips_and_strips_whitespace(tmp_path, monkeypatch):
    """The fix must not regress the success path: a real query file's
    content still reaches `read_declared_sources` as the `query` kwarg,
    stripped of surrounding whitespace, exactly as before the guard was
    added."""
    query_file = tmp_path / "query.txt"
    query_file.write_text("  what should I read first?  \n", encoding="utf-8")
    research_file = str(tmp_path / "topic_RESEARCH.md")

    captured = {}

    def _fake_resolve_scope_from_cycle(session_id, research_file):
        return object()  # any sentinel; read_declared_sources is faked below

    def _fake_read_declared_sources(scope, projects_root, store, query):
        captured["query"] = query
        return declared_read.DeclaredReadResult(readings=(), refusals=())

    monkeypatch.setattr(
        declared_read, "_resolve_scope_from_cycle", _fake_resolve_scope_from_cycle
    )
    monkeypatch.setattr(
        declared_read, "read_declared_sources", _fake_read_declared_sources
    )
    monkeypatch.setattr(declared_read, "durable_store_for", lambda _rf: None)

    rc = declared_read.main(_read_argv(query_file, tmp_path, research_file))
    assert rc == 0
    assert captured["query"] == "what should I read first?"

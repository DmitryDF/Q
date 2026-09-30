"""S4 round-3 fix — MAJOR 1: the payload goes through a file, not a shell literal.

The third independent review reproduced, against the LIVE `sanitize-bash.sh`,
that S4's own shipped `r0_intake` / scope-drafter templates are refused the
moment the operator's approved scope contains "for", "while", "done", "fi" or
"esac" as a whole word — ordinary in English research prose. The templates
wrapped the payload in a multi-line, single-quoted shell literal:
`sanitize-bash.sh` pattern 9 refuses any multi-line Bash command containing
those control-flow keywords, and — independently — a single-quoted literal
breaks outright the moment the payload contains an apostrophe ("what's",
"don't"), since the sanitizer does not catch that second failure at all.

The fix removes shell quoting from the path entirely: `research_pipeline.py
advance` grows an additive `--payload-file <path>` option that reads the JSON
from a file (written by the Write tool, never a shell redirect or heredoc),
and the CLI is then invoked as a single line — fixing the sanitizer refusal
(multi-line -> one line) AND the apostrophe breakage (no shell quoting touches
the payload content at all) at once.

This file pins:
  1. `--payload-file` registers and approves a cycle carrying a scope with
     "for", "while", "done" and apostrophes, through the actual documented
     path (`rp.main`, not `cmd_advance` directly — the CLI-level regression
     the review asked for).
  2. `--payload-file` is additive: the positional JSON-argument form still
     works unchanged.
  3. A missing `--payload-file` path is a clean JSON error, not a traceback.
  4. Reproduction against the LIVE `sanitize-bash.sh` (skipped if absent):
     the shipped one-line `--payload-file` form is NOT refused even though
     the payload it points at carries the hazardous scope; the OLD
     multi-line single-quoted form WAS refused on the same content — the
     documented before/after.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOKS_DIR))

import research_pipeline as rp  # noqa: E402

SANITIZE_HOOK = HOOKS_DIR / "sanitize-bash.sh"

# MINOR 2 (round-7 fix): `_RESEARCH_FILE` used to be a fixed relative
# literal, so `r0_intake`'s `_write_angles_sidecar` call resolved it against
# the process cwd and left a `Thoughts/topic_RESEARCH_angles.json` sidecar
# behind wherever pytest was invoked from. Every payload built below now
# takes a `tmp_path`-rooted absolute path as `research_file_path` instead.

# A scope containing "for", "while", "done" as whole words plus apostrophes —
# exactly the shape sanitize-bash.sh pattern 9 (control-flow keywords,
# multi-line) and the single-quote-literal breakage both fire on, per the
# round-3 review's own reproduction.
_HAZARD_SCOPE = {
    "angles": [
        "what's the right algorithm for bursty traffic?",
        "what happens while the bucket refills, and when is it done?",
    ],
    "focused_questions": [
        "don't we need a fallback for throttling at the edge?",
    ],
}


@pytest.fixture
def tmp_state(tmp_path):
    return tmp_path / "research_pipeline"


def _write_payload(path, research_file_path, **extra):
    payload = {
        "research_file_path": research_file_path,
        "caller_skill": "/research",
        "downstream_tool": "~/.claude/skills/research/SKILL.md",
        "caller_session_id": "s4r3-hazard",
        "user_approved_scope": True,
        "scope": _HAZARD_SCOPE,
        "scope_provenance": "fresh-answer",
    }
    payload.update(extra)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_payload_file_registers_and_approves_a_hazardous_scope(
    tmp_state, tmp_path, monkeypatch
):
    """The actual documented path: `research_pipeline.py advance ... \
    --payload-file <path>` through `rp.main`, carrying a scope with
    for/while/done and apostrophes — content that breaks BOTH the sanitizer
    (via the old multi-line single-quoted literal) and shell quoting (via the
    apostrophes) when passed positionally on the command line. Registers and
    approves cleanly because no shell quoting ever touches it."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    payload_path = tmp_path / "rp_payload.json"
    written = _write_payload(payload_path, str(tmp_path / "topic_RESEARCH.md"))
    sid = "s4r3-hazard"

    result = rp.main([
        "research_pipeline.py", "advance", sid, "r0_intake",
        "--payload-file", str(payload_path),
        "--cycle-id", "default",
    ])
    assert result == 0

    state = rp._read_state(sid, tmp_state)
    cycle = state["cycles"]["default"]
    assert cycle["r1_scope_approved"] is True
    assert cycle["approval_provenance"] == "fresh-answer"
    assert (
        cycle["checkpoints"]["r0_intake"]["data"]["scope"] == written["scope"]
    ), "the hazardous scope must round-trip byte-for-byte through the file"


def test_payload_file_is_additive_positional_form_still_works(
    tmp_state, tmp_path, monkeypatch
):
    """--payload-file is additive: the positional JSON-argument form (no
    hazardous content here — that path's own quoting hazards are pinned by
    the sanitizer reproductions below, not re-tested here) must keep
    working unchanged."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r3-positional"
    payload = {
        "research_file_path": str(tmp_path / "topic_RESEARCH.md"),
        "caller_skill": "/research",
        "downstream_tool": "~/.claude/skills/research/SKILL.md",
        "caller_session_id": sid,
        "user_approved_scope": True,
        "scope": {"angles": ["a"]},
        "scope_provenance": "fresh-answer",
    }
    result = rp.main([
        "research_pipeline.py", "advance", sid, "r0_intake", json.dumps(payload),
        "--cycle-id", "default",
    ])
    assert result == 0
    state = rp._read_state(sid, tmp_state)
    assert state["cycles"]["default"]["r1_scope_approved"] is True


def test_payload_file_missing_path_is_a_clean_error_not_a_traceback(
    tmp_state, monkeypatch, capsys
):
    """An OSError reading --payload-file must degrade to the same
    {"ok": false, "error": ...} JSON shape every other CLI failure uses —
    never an uncaught traceback."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    sid = "s4r3-missing"
    result = rp.main([
        "research_pipeline.py", "advance", sid, "r0_intake",
        "--payload-file", str(tmp_state / "does-not-exist.json"),
    ])
    assert result == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "payload-file" in out["error"]


def test_payload_file_requires_sid_and_checkpoint(tmp_state, monkeypatch):
    """--payload-file still needs SID + CHECKPOINT positionally — it swaps out
    only the JSON argument, not the whole calling convention."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_state))
    result = rp.main(["research_pipeline.py", "advance", "--payload-file", "/x"])
    assert result == 2


# --------------------------------------------------------------------------- #
# Live sanitizer reproduction — before/after, exactly as the round-3 review
# ran it, against the real registered hook (not a stand-in).
# --------------------------------------------------------------------------- #

def _run_sanitizer(command):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        [str(SANITIZE_HOOK)], input=payload, capture_output=True, text=True
    )


@pytest.mark.skipif(
    not SANITIZE_HOOK.exists(), reason="sanitize-bash.sh not present"
)
def test_AFTER_shipped_one_line_payload_file_form_is_not_refused():
    """AFTER: the shipped one-line `--payload-file` invocation is NOT refused
    by the live sanitizer — even though the payload FILE it points at (never
    inspected by this hook; the hook only ever sees the command line) carries
    the hazardous scope. This is the fix: the command the model actually runs
    no longer carries the operator's words at all."""
    cmd = (
        'python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID '
        'r0_intake --payload-file "/tmp/rp_payload.json" --cycle-id "default"'
    )
    proc = _run_sanitizer(cmd)
    assert proc.returncode == 0, (
        f"shipped one-line --payload-file form was refused: {proc.stderr!r}"
    )


@pytest.mark.skipif(
    not SANITIZE_HOOK.exists(), reason="sanitize-bash.sh not present"
)
def test_BEFORE_old_multiline_single_quoted_form_with_hazardous_content_was_refused():
    """BEFORE: the shape the pre-fix templates emitted — a multi-line,
    single-quoted JSON literal carrying the operator's own scope — is refused
    by the live sanitizer the moment that scope contains "for"/"while"/"done"
    as whole words. Pinned as the documented reproduction the fix removes,
    not as something still expected to pass."""
    hazard_json = json.dumps({"scope": _HAZARD_SCOPE}, indent=2)
    cmd = (
        "python3 ${KIT_HOOKS_DIR}/research_pipeline.py advance $SESSION_ID r0_intake \\\n"
        "  '" + hazard_json + "' \\\n"
        '  --cycle-id "default"'
    )
    proc = _run_sanitizer(cmd)
    assert proc.returncode == 2, (
        "expected the old multi-line single-quoted shape to be refused "
        f"(pattern 9); got exit={proc.returncode} stderr={proc.stderr!r}"
    )
    assert "control flow" in proc.stderr

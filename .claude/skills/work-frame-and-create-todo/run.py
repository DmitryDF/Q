#!/usr/bin/env python3
"""Orchestrator for /work-frame-and-create-todo.

Wraps `todo.py add --validate-framing` with a code-enforced 3-retry cap.

Trust Hierarchy (~/.claude/rules/code_first_architecture.md):
- Code owns the retry loop and the cap. SKILL.md only elicits the four
  framing components from the user; it cannot override the cap.
- The validator (`todo.py add --validate-framing`) owns framing checks.
- This shim contains NO framing logic — it only orchestrates.

I/O contract:
- STDIN: JSON object with keys:
    item_text     (required) — the framed item text the user composed
    bucket        (required) — one of NOW, NEXT, NEARBY, NASCENT, SCHEDULED
    cwd           (optional) — where to resolve the target TODO.md from;
                                defaults to os.getcwd()
    session_id    (optional) — stable id for the retry counter; defaults
                                to env CLAUDE_SESSION_ID or "default"
- STDOUT: JSON result object with keys:
    status              — PASS | RETRY | EXHAUSTED
    attempt             — int (1-based) — which attempt this call was
    max_retries         — int — the hard cap (3)
    missing_components  — list[str] — components the validator flagged
                                       (empty on PASS)
    stderr_verbatim     — str — stderr from todo.py for this attempt
    session_id          — str — the id used for state-file persistence
- Exit codes:
    0 — PASS
    1 — RETRY (still recoverable)
    2 — EXHAUSTED (cap reached; no further retries)
    3 — usage / input-format error
"""

import json
import os
import subprocess
import sys
from pathlib import Path

MAX_RETRIES = 3
STATE_DIR = Path(os.path.expanduser("~/.claude/state/work-frame-and-create-todo"))
TODO_PY = Path(os.path.expanduser("${KIT_HOOKS_DIR}/todo.py"))
VALID_BUCKETS = {"NOW", "NEXT", "NEARBY", "NASCENT", "SCHEDULED"}


def _emit(payload, code):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.exit(code)


def _resolve_session_id(payload):
    sid = payload.get("session_id")
    if sid:
        return str(sid)
    return os.environ.get("CLAUDE_SESSION_ID") or "default"


def _state_path(session_id):
    safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
    if not safe:
        safe = "default"
    return STATE_DIR / f"{safe}.json"


def _read_attempt(state_file):
    if not state_file.exists():
        return 0
    try:
        return int(json.loads(state_file.read_text()).get("attempt", 0))
    except (ValueError, json.JSONDecodeError):
        return 0


def _write_attempt(state_file, attempt):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"attempt": attempt}) + "\n")


def _clear_state(state_file):
    try:
        state_file.unlink()
    except FileNotFoundError:
        pass


def _walk_up_for_todo(start):
    cur = Path(start).resolve()
    while True:
        candidate = cur / "TODO.md"
        if candidate.exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def _parse_missing(stderr_text):
    missing = []
    for line in stderr_text.splitlines():
        s = line.strip()
        if s.startswith("✗ "):
            missing.append(s[2:].strip())
    return missing


def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            _emit({"status": "ERROR", "error": "empty stdin; expected JSON"}, 3)
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        _emit({"status": "ERROR", "error": f"invalid JSON on stdin: {e}"}, 3)

    item_text = payload.get("item_text")
    bucket = payload.get("bucket")
    cwd = payload.get("cwd") or os.getcwd()

    if not item_text or not isinstance(item_text, str):
        _emit({"status": "ERROR", "error": "missing required field: item_text"}, 3)
    if not bucket or bucket not in VALID_BUCKETS:
        _emit({"status": "ERROR",
               "error": f"missing/invalid bucket; one of {sorted(VALID_BUCKETS)} required"}, 3)

    session_id = _resolve_session_id(payload)
    state_file = _state_path(session_id)
    prior_attempts = _read_attempt(state_file)

    if prior_attempts >= MAX_RETRIES:
        _clear_state(state_file)
        _emit({
            "status": "EXHAUSTED",
            "attempt": prior_attempts,
            "max_retries": MAX_RETRIES,
            "missing_components": [],
            "stderr_verbatim": (
                f"Retry cap reached ({MAX_RETRIES} attempts). "
                "Re-elicit framing from the user from scratch or stop."
            ),
            "session_id": session_id,
        }, 2)

    target_root = _walk_up_for_todo(cwd)
    if target_root is None:
        _clear_state(state_file)
        _emit({
            "status": "ERROR",
            "error": (
                f"No TODO.md found at {cwd} or any parent. "
                "Create one with `touch TODO.md`, then retry."
            ),
            "session_id": session_id,
        }, 3)

    # `--require-evidence` is passed here and ONLY here — this skill is the one
    # caller that opts into the evidence bar (Guiding Policy 4). It is a
    # separate flag from `--validate-framing`, not a tightening of it, so the
    # four other `todo.py add` callers and the auto-registration obligation are
    # unaffected.
    #
    # ORDERING NOTE, because getting it wrong is silent: this argument may only
    # be sent to a `todo.py` that already DEFINES it. argparse exits 2 on an
    # unknown flag, so a run.py deployed ahead of its todo.py would fail every
    # invocation of this skill with a usage error rather than a framing message.
    cmd = [
        sys.executable, str(TODO_PY), "add",
        "--bucket", bucket,
        "--project", str(target_root),
        "--validate-framing",
        "--require-evidence",
        item_text,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    attempt_now = prior_attempts + 1

    if proc.returncode == 0:
        _clear_state(state_file)
        _emit({
            "status": "PASS",
            "attempt": attempt_now,
            "max_retries": MAX_RETRIES,
            "missing_components": [],
            "stderr_verbatim": proc.stderr,
            "session_id": session_id,
        }, 0)

    missing = _parse_missing(proc.stderr)

    # MAX_RETRIES = 3 means three RETRY attempts are permitted. The cap
    # fires on the NEXT call after attempt_now reaches MAX_RETRIES — i.e.,
    # the 4th invocation (prior_attempts == 3 at the top of main) returns
    # EXHAUSTED without re-running the validator.
    _write_attempt(state_file, attempt_now)
    _emit({
        "status": "RETRY",
        "attempt": attempt_now,
        "max_retries": MAX_RETRIES,
        "missing_components": missing,
        "stderr_verbatim": proc.stderr,
        "session_id": session_id,
    }, 1)


if __name__ == "__main__":
    main()

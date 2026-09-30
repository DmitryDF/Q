#!/usr/bin/env python3
"""Walk-state machine for /plan-followups-review.

A thin, code-owned shim that walks a set of ALREADY-VERIFIED observations one
at a time into triaged dispositions. It owns the loop, the per-observation
disposition ledger, the complete-coverage gate, the no-silent-creation
invariant, and crash-safe resume. SKILL.md only elicits judgment (why-line,
triage, /recommend, trade-off presentation, the accept/edit/discard call).

Trust Hierarchy (~/.claude/rules/code_first_architecture.md):
- Code owns the walk loop + invariants. SKILL.md cannot override them.
- This shim contains NO triage / judgment / verification logic. It does NOT
  fact-check observations — they arrive pre-verified from the caller's
  upstream gate (producer-never-verifies holds by topology).
- Reuse, never reinvent: /recommend and /work-frame-and-create-todo are
  invoked whole by the skill; this shim only records the resulting todo_ref.

Code-enforced invariants:
1. One-at-a-time      — `next` returns exactly one undisposed observation.
2. No silent creation — `record accept` REQUIRES a non-empty todo_ref.
3. Complete coverage  — `summary` emits DONE only after every observation has
                        a disposition (else INCOMPLETE, exit 2).
4. Crash-safe resume  — state persists to a per-session ledger; resume never
                        re-hands an already-disposed observation.

I/O contract:
- Subcommand on argv[1]: one of `init`, `next`, `record`, `summary`, `reset`.
- STDIN: a JSON object (the payload for the subcommand).
- STDOUT: a JSON result object.
- Exit codes:
    0 — OK         (init OK / next returned observation or DONE /
                    record RECORDED / summary SUMMARY / reset OK)
    1 — REJECTED   (record rejected: accept without todo_ref, unknown id,
                    already disposed, invalid disposition) — recoverable
    2 — INCOMPLETE (summary requested before complete coverage) — gate signal
    3 — usage / input-format / schema error

Subcommand payloads:
  init    {session_id, observations: [{id, title, body, ...}, ...]}
  next    {session_id}
  record  {session_id, obs_id, disposition: accept|edit|discard,
           todo_ref?, note?}
  summary {session_id}
  reset   {session_id}

State file: ~/.claude/state/plan-followups-review/<session_id>.json
            (override the parent dir with $PLAN_FOLLOWUPS_REVIEW_STATE_DIR for tests).
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_DISPOSITIONS = {"accept", "edit", "discard"}
SUBCOMMANDS = {"init", "next", "record", "summary", "reset"}


def _state_dir():
    override = os.environ.get("PLAN_FOLLOWUPS_REVIEW_STATE_DIR")
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.claude/state/plan-followups-review"))


def _now():
    return datetime.now(timezone.utc).isoformat()


def _emit(payload, code):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.exit(code)


def _resolve_session_id(payload):
    sid = payload.get("session_id")
    if sid:
        return str(sid)
    return os.environ.get("CLAUDE_SESSION_ID") or "default"


def _safe_name(session_id):
    safe = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
    return safe or "default"


def _state_path(session_id):
    return _state_dir() / f"{_safe_name(session_id)}.json"


def _read_state(state_file):
    if not state_file.exists():
        return None
    try:
        return json.loads(state_file.read_text())
    except (ValueError, json.JSONDecodeError):
        return None


def _write_state(state_file, state):
    state["updated_at"] = _now()
    _state_dir().mkdir(parents=True, exist_ok=True)
    # Atomic replace so a crash mid-write never leaves a half-written ledger.
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n")
    os.replace(tmp, state_file)


def _validate_observations(observations):
    """Return (ok, error_str). Schema for the caller/S9-facing input contract."""
    if not isinstance(observations, list):
        return False, "observations must be a list"
    seen = set()
    for i, obs in enumerate(observations):
        if not isinstance(obs, dict):
            return False, f"observation[{i}] must be an object"
        for field in ("id", "title", "body"):
            val = obs.get(field)
            if not val or not isinstance(val, str):
                return False, f"observation[{i}] missing/empty string field: {field}"
        oid = obs["id"]
        if oid in seen:
            return False, f"duplicate observation id: {oid}"
        seen.add(oid)
    return True, None


def _undisposed(state):
    """Return the observations (in order) with no recorded disposition."""
    disposed = state.get("dispositions", {})
    return [o for o in state["observations"] if o["id"] not in disposed]


# --------------------------------------------------------------------------- #
# Subcommands
# --------------------------------------------------------------------------- #

def cmd_init(payload):
    session_id = _resolve_session_id(payload)
    state_file = _state_path(session_id)

    observations = payload.get("observations")
    if observations is None:
        _emit({"status": "ERROR", "error": "missing required field: observations",
               "session_id": session_id}, 3)
    ok, err = _validate_observations(observations)
    if not ok:
        _emit({"status": "ERROR", "error": f"schema: {err}",
               "session_id": session_id}, 3)

    existing = _read_state(state_file)
    if existing is not None:
        # Resume an in-flight walk: never clobber recorded dispositions.
        undisposed = _undisposed(existing)
        _emit({
            "status": "RESUMED",
            "session_id": session_id,
            "total": len(existing["observations"]),
            "disposed": len(existing.get("dispositions", {})),
            "undisposed": len(undisposed),
        }, 0)

    state = {
        "session_id": session_id,
        "created_at": _now(),
        "observations": observations,
        "dispositions": {},
    }
    _write_state(state_file, state)
    _emit({
        "status": "INITIALIZED",
        "session_id": session_id,
        "total": len(observations),
        "disposed": 0,
        "undisposed": len(observations),
    }, 0)


def cmd_next(payload):
    session_id = _resolve_session_id(payload)
    state = _read_state(_state_path(session_id))
    if state is None:
        _emit({"status": "ERROR",
               "error": "no walk found for session; call `init` first",
               "session_id": session_id}, 3)

    undisposed = _undisposed(state)
    total = len(state["observations"])
    if not undisposed:
        _emit({
            "status": "DONE",
            "session_id": session_id,
            "total": total,
            "remaining": 0,
        }, 0)

    # One-at-a-time: hand out exactly the first undisposed observation.
    obs = undisposed[0]
    index = state["observations"].index(obs)
    _emit({
        "status": "OBSERVATION",
        "session_id": session_id,
        "observation": obs,
        "index": index,
        "position": total - len(undisposed) + 1,
        "total": total,
        "remaining": len(undisposed),
    }, 0)


def cmd_record(payload):
    session_id = _resolve_session_id(payload)
    state_file = _state_path(session_id)
    state = _read_state(state_file)
    if state is None:
        _emit({"status": "ERROR",
               "error": "no walk found for session; call `init` first",
               "session_id": session_id}, 3)

    obs_id = payload.get("obs_id")
    disposition = payload.get("disposition")
    todo_ref = payload.get("todo_ref")
    note = payload.get("note")

    if not obs_id or not isinstance(obs_id, str):
        _emit({"status": "ERROR", "error": "missing required field: obs_id",
               "session_id": session_id}, 3)
    if disposition not in VALID_DISPOSITIONS:
        _emit({"status": "REJECTED",
               "reason": f"invalid disposition; one of {sorted(VALID_DISPOSITIONS)}",
               "obs_id": obs_id, "session_id": session_id}, 1)

    known_ids = {o["id"] for o in state["observations"]}
    if obs_id not in known_ids:
        _emit({"status": "REJECTED", "reason": f"unknown obs_id: {obs_id}",
               "obs_id": obs_id, "session_id": session_id}, 1)
    if obs_id in state.get("dispositions", {}):
        _emit({"status": "REJECTED", "reason": "observation already disposed",
               "obs_id": obs_id,
               "existing": state["dispositions"][obs_id],
               "session_id": session_id}, 1)

    # No silent creation: an accept is only recordable WITH a real todo_ref.
    if disposition == "accept" and (not todo_ref or not str(todo_ref).strip()):
        _emit({"status": "REJECTED",
               "reason": "accept requires a non-empty todo_ref "
                         "(no silent TODO creation)",
               "obs_id": obs_id, "session_id": session_id}, 1)

    entry = {"disposition": disposition, "recorded_at": _now()}
    if todo_ref and str(todo_ref).strip():
        entry["todo_ref"] = str(todo_ref).strip()
    if note and str(note).strip():
        entry["note"] = str(note).strip()

    state.setdefault("dispositions", {})[obs_id] = entry
    _write_state(state_file, state)

    remaining = len(_undisposed(state))
    _emit({
        "status": "RECORDED",
        "session_id": session_id,
        "obs_id": obs_id,
        "disposition": disposition,
        "todo_ref": entry.get("todo_ref"),
        "remaining": remaining,
    }, 0)


def _build_summary(state):
    dispositions = state.get("dispositions", {})
    counts = {"accept": 0, "edit": 0, "discard": 0}
    rows = []
    for obs in state["observations"]:
        d = dispositions.get(obs["id"])
        rows.append({
            "id": obs["id"],
            "title": obs["title"],
            "disposition": (d or {}).get("disposition"),
            "todo_ref": (d or {}).get("todo_ref"),
            "note": (d or {}).get("note"),
        })
        if d and d.get("disposition") in counts:
            counts[d["disposition"]] += 1
    return rows, counts


def cmd_summary(payload):
    session_id = _resolve_session_id(payload)
    state = _read_state(_state_path(session_id))
    if state is None:
        _emit({"status": "ERROR",
               "error": "no walk found for session; call `init` first",
               "session_id": session_id}, 3)

    undisposed = _undisposed(state)
    rows, counts = _build_summary(state)

    # Complete-coverage gate: no DONE summary until every observation is disposed.
    if undisposed:
        _emit({
            "status": "INCOMPLETE",
            "session_id": session_id,
            "total": len(state["observations"]),
            "undisposed": [o["id"] for o in undisposed],
            "dispositions": rows,
            "counts": counts,
        }, 2)

    _emit({
        "status": "SUMMARY",
        "session_id": session_id,
        "total": len(state["observations"]),
        "counts": counts,
        "dispositions": rows,
    }, 0)


def cmd_reset(payload):
    session_id = _resolve_session_id(payload)
    state_file = _state_path(session_id)
    try:
        state_file.unlink()
        existed = True
    except FileNotFoundError:
        existed = False
    _emit({"status": "RESET", "session_id": session_id, "existed": existed}, 0)


DISPATCH = {
    "init": cmd_init,
    "next": cmd_next,
    "record": cmd_record,
    "summary": cmd_summary,
    "reset": cmd_reset,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in SUBCOMMANDS:
        _emit({"status": "ERROR",
               "error": f"usage: run.py <{'|'.join(sorted(SUBCOMMANDS))}> "
                        "< payload.json"}, 3)

    command = argv[0]
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _emit({"status": "ERROR", "error": f"invalid JSON on stdin: {e}"}, 3)

    if not isinstance(payload, dict):
        _emit({"status": "ERROR", "error": "stdin payload must be a JSON object"}, 3)

    DISPATCH[command](payload)


if __name__ == "__main__":
    main()

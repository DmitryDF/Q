#!/usr/bin/env python3
"""research_token_parser.py — whole-session token accounting for research runs.

Reads a Claude session transcript (JSONL), sums token usage by model, joins the
research_pipeline manifest (RP-{SID}.json), and appends a record to
research_runs.jsonl.

Standalone: stdlib only. Never imports from pre_plan_gates.py or
research_pipeline.py (state-dir resolution logic is mirrored, not imported, to
keep this module dependency-free per its contract).

Best-effort everywhere: parse errors and missing files produce a warning on
stderr and continue; they never raise.

CLI:
    python3 research_token_parser.py SID [--output PATH] [--state-dir PATH]
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _warn(msg):
    print("research_token_parser: {}".format(msg), file=sys.stderr)


# --------------------------------------------------------------------------- #
# Transcript discovery
# --------------------------------------------------------------------------- #

def find_transcript(sid):
    """Scan ~/.claude/projects/<hash>/<SID>.jsonl, one level deep.

    Returns the first matching path, or None. Unreadable directories are
    skipped (PermissionError handled gracefully).
    """
    projects_root = Path.home() / ".claude" / "projects"
    try:
        hash_dirs = list(projects_root.iterdir())
    except (FileNotFoundError, PermissionError):
        return None

    for hash_dir in hash_dirs:
        try:
            if not hash_dir.is_dir():
                continue
        except PermissionError:
            continue
        candidate = hash_dir / "{}.jsonl".format(sid)
        try:
            if candidate.is_file():
                return candidate
        except PermissionError:
            continue
    return None


# --------------------------------------------------------------------------- #
# Token summation
# --------------------------------------------------------------------------- #

def _normalize_model(model):
    """Strip the 'claude-' prefix for readability. Absent -> 'unknown'."""
    if not model:
        return "unknown"
    if model.startswith("claude-"):
        return model[len("claude-"):]
    return model


def sum_tokens_by_model(transcript_path):
    """Accumulate per-model token counts from assistant lines with usage.

    Returns a dict keyed by normalized model short name:
        {model: {input_tokens, output_tokens,
                 cache_creation_input_tokens, cache_read_input_tokens,
                 total_tokens}}
    total_tokens = input_tokens + output_tokens (cache tokens are already
    counted within input on the API side, so they are not re-added).
    """
    summary = {}

    try:
        fh = open(transcript_path, "r", encoding="utf-8")
    except (OSError, IOError) as exc:
        _warn("cannot open transcript {}: {}".format(transcript_path, exc))
        return summary

    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (ValueError, TypeError):
                continue  # malformed line — skip silently
            if not isinstance(obj, dict):
                continue
            if obj.get("role") != "assistant":
                continue
            usage = obj.get("usage")
            if not isinstance(usage, dict):
                continue

            model = _normalize_model(obj.get("model"))
            bucket = summary.setdefault(
                model,
                {f: 0 for f in TOKEN_FIELDS} | {"total_tokens": 0},
            )
            for field in TOKEN_FIELDS:
                val = usage.get(field, 0)
                if isinstance(val, bool) or not isinstance(val, int):
                    continue
                bucket[field] += val
            bucket["total_tokens"] = (
                bucket["input_tokens"] + bucket["output_tokens"]
            )

    return summary


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #

def _resolve_state_dir(state_dir=None):
    """Precedence: explicit arg > RP_STATE_DIR env var > default.

    Mirrors research_pipeline._resolve_state_dir without importing it.
    """
    if state_dir is not None:
        return Path(state_dir)
    env = os.environ.get("RP_STATE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".claude" / "state" / "research_pipeline"


def load_manifest(sid, state_dir=None):
    """Read RP-{SID}.json. Returns dict, or None if absent/unreadable."""
    path = _resolve_state_dir(state_dir) / "RP-{}.json".format(sid)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, IsADirectoryError):
        return None
    except (OSError, IOError) as exc:
        _warn("cannot read manifest {}: {}".format(path, exc))
        return None
    except (ValueError, TypeError) as exc:
        _warn("malformed manifest {}: {}".format(path, exc))
        return None
    if not isinstance(data, dict):
        return None
    return data


# --------------------------------------------------------------------------- #
# Record assembly + output
# --------------------------------------------------------------------------- #

def build_record(sid, token_summary, manifest):
    """Assemble the output dict. manifest may be None."""
    present = manifest is not None
    m = manifest or {}

    checkpoints = m.get("checkpoints")
    if isinstance(checkpoints, dict):
        checkpoints_completed = list(checkpoints.keys())
    else:
        checkpoints_completed = []

    return {
        "session_id": sid,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "whole_session",
        "research_file_path": m.get("research_file_path"),
        "topic_slug": m.get("topic_slug"),
        "skill_version": m.get("skill_version") or "unknown",
        "rules_version": m.get("rules_version") or "unknown",
        "checkpoints_completed": checkpoints_completed,
        "token_summary": token_summary,
        "manifest_present": present,
    }


def _default_output_path():
    return Path.home() / ".claude" / "state" / "research_runs.jsonl"


def append_run_record(record, output_path=None):
    """Best-effort append of one JSON line to research_runs.jsonl.

    Creates parent dirs and the file if absent. On any IOError, warns to
    stderr and returns False (never raises). Returns True on success.
    """
    path = Path(output_path) if output_path is not None else _default_output_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except (OSError, IOError) as exc:
        _warn("cannot append run record to {}: {}".format(path, exc))
        return False
    return True


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def main(sid=None, state_dir=None, output_path=None):
    if sid is None:
        argv = sys.argv[1:]
        if not argv:
            print(
                "usage: research_token_parser.py SID "
                "[--output PATH] [--state-dir PATH]",
                file=sys.stderr,
            )
            return 2
        sid = argv[0]
        i = 1
        while i < len(argv):
            arg = argv[i]
            if arg == "--output" and i + 1 < len(argv):
                output_path = argv[i + 1]
                i += 2
            elif arg == "--state-dir" and i + 1 < len(argv):
                state_dir = argv[i + 1]
                i += 2
            else:
                i += 1

    transcript_path = find_transcript(sid)
    if transcript_path is None:
        _warn("no transcript found for session {} — nothing to record".format(sid))
        return 0

    token_summary = sum_tokens_by_model(transcript_path)

    manifest = load_manifest(sid, state_dir)
    if manifest is None:
        _warn(
            "no research_pipeline manifest for session {} "
            "— not a research session, no run recorded".format(sid)
        )
        return 0

    record = build_record(sid, token_summary, manifest)
    append_run_record(record, output_path)
    print(json.dumps(record))
    return 0


if __name__ == "__main__":
    sys.exit(main())

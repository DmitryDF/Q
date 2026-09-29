#!/usr/bin/env python3
"""skill_runs.py — per-skill-run metrics ledger (orchestrator-pattern S3 seed).

Appends one JSONL row per heavy-skill run to
    <config>/state/metrics/skill-runs.jsonl
where <config> = $CLAUDE_CONFIG_DIR or ~/.claude.

This is the reuse-first metrics substrate SEED. Slice S3 (walking skeleton)
writes the first OMTM baseline row for a `/close` run; Slice S5 formalizes the
full capture (main-chat token delta = OMTM; model-weighted usage = Secondary;
the three signals) reusing `/double-check`'s CostBlock + `research_token_parser`.
The row schema here is forward-compatible: unknown metrics are `null` at the
baseline and filled in by S5.

Row schema (v0 baseline):
    {
      "skill": "close",
      "sid": "<session-id>",
      "ts": "<ISO-8601 UTC>",
      "run_kind": "baseline" | "full",
      "omtm_main_chat_delta": <int|null>,   # OMTM — main-chat tokens added by the run
      "secondary_model_weighted": <int|null>, # Secondary — model-weighted usage
      "note": "<free text|null>"
    }

Standalone + vendor-neutral (no Claude-specific imports); unit-testable via
`python3 skill_runs.py --self-test`.

Commands:
    append <json-row>   Append one row (merged onto the v0 schema defaults). Exit 0.
    tail [N]            Print the last N rows (default 5). Exit 0.
    --self-test         Append to a temp ledger and read it back.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA_DEFAULTS = {
    "skill": None,
    "sid": None,
    "ts": None,
    "run_kind": "baseline",
    "omtm_main_chat_delta": None,
    "secondary_model_weighted": None,
    "note": None,
}


def _config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def ledger_path(root: Path = None) -> Path:
    base = root if root is not None else _config_dir()
    return base / "state" / "metrics" / "skill-runs.jsonl"


def append_row(row: dict, root: Path = None) -> dict:
    merged = dict(_SCHEMA_DEFAULTS)
    merged.update(row or {})
    if not merged.get("ts"):
        merged["ts"] = datetime.now(timezone.utc).isoformat()
    if not merged.get("skill"):
        raise ValueError("append_row requires a 'skill' field")
    p = ledger_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(merged) + "\n")
    return {"status": "appended", "ledger": str(p), "row": merged}


def tail_rows(n: int = 5, root: Path = None) -> list:
    p = ledger_path(root)
    if not p.exists():
        return []
    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out


def _self_test() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        res = append_row(
            {"skill": "close", "sid": "sid-test", "run_kind": "baseline",
             "note": "S3 walking-skeleton baseline"},
            root,
        )
        assert Path(res["ledger"]).is_file(), "ledger not created"
        assert res["row"]["ts"], "ts not stamped"
        assert res["row"]["omtm_main_chat_delta"] is None, "baseline OMTM should be null"
        rows = tail_rows(5, root)
        assert len(rows) == 1 and rows[0]["skill"] == "close", rows
        # Missing skill → error (fail-closed on malformed row).
        try:
            append_row({"sid": "x"}, root)
            assert False, "expected ValueError on missing skill"
        except ValueError:
            pass
    print("skill_runs.py self-test: PASS")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return _self_test()
    if not argv:
        print("usage: skill_runs.py {append <json>|tail [N]|--self-test}", file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "append":
        if len(argv) < 2:
            print("append requires a JSON row argument", file=sys.stderr)
            return 2
        try:
            row = json.loads(argv[1])
        except ValueError as e:
            print(f"invalid JSON row: {e}", file=sys.stderr)
            return 2
        print(json.dumps(append_row(row)))
        return 0
    if cmd == "tail":
        n = int(argv[1]) if len(argv) > 1 else 5
        for r in tail_rows(n):
            print(json.dumps(r))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

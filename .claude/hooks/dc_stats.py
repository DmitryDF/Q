#!/usr/bin/env python3
"""dc_stats.py — code-only /double-check cost aggregator → Stats.md (Slice S8, A15).

Plan: Thoughts/double-check-validation-targeting_S8_PLAN.md (Coherent Actions A1/A2).
Design: spine `### Solution Alternative 1` A15 (+ Discovery Q-A4 / Metrics S1/S2).

WHAT THIS IS
------------
The read-side of the `/double-check` audit-marker substrate. S1+ made every run
leave a per-run audit marker carrying cost (tokens + time, tagged
upgrade/self_assessment/checkers) and per-checker per-angle discrepancy rows — but
the substrate was WRITE-ONLY. This tool renders those markers into a `Stats.md`
view grouped by `(caller, topic)`, so the engine's locked "measurable results
that enable data-driven improvements" property finally has a surface.

WHY A STANDALONE TOOL (not in _factcheck_engine.py)
---------------------------------------------------
The engine is deliberately stdlib-only — its `_dump_audit_yaml` docstring keeps
"the engine dependency surface at stdlib + pydantic, no pyyaml runtime dep". The
read side must PARSE marker frontmatter, which wants PyYAML; so the aggregator
lives in this standalone hook (the same shape as S7's `dc_caller_audit.py`,
which also imports yaml), leaving the engine's dependency invariant intact. The
tool reuses the engine's marker contract only — it imports `DC_AUDIT_FALLBACK_ROOT`
(the canonical fallback location) and reads the `AuditMarker` field names; it
never touches the engine's write side.

DETERMINISM (code_first_architecture.md; engine:20 "no AI in aggregation")
--------------------------------------------------------------------------
Discovery, aggregation, and rendering are pure code over the on-disk markers —
no AI actor, no baselines/thresholds (v1), per-project (no cross-project rollup).

CLI
---
  dc_stats.py <project_root>   discover the project's `/double-check` audit
                               markers, aggregate by (caller, topic), and
                               find-or-replace the DC-STATS block in
                               <project_root>/Stats.md (preserving all other
                               content). Exit 0.
"""
from __future__ import annotations

import sys
from collections import OrderedDict
from pathlib import Path

try:
    import yaml
except Exception as exc:  # pragma: no cover - pyyaml is present in this env
    sys.stderr.write(f"dc_stats: PyYAML required: {exc}\n")
    raise

# Reuse the engine's canonical fallback-marker location (the ONLY engine coupling;
# the engine stays stdlib-only — importing it adds no pyyaml dep to it).
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _factcheck_engine import DC_AUDIT_FALLBACK_ROOT as _ENGINE_FALLBACK_ROOT
except Exception:  # pragma: no cover - fall back to the documented constant
    _ENGINE_FALLBACK_ROOT = Path.home() / ".claude" / "state" / "dc-audit"

MARKER_GLOB = "dc-audit__*.md"
BLOCK_BEGIN = "<!-- DC-STATS:BEGIN -->"
BLOCK_END = "<!-- DC-STATS:END -->"
_VERDICTS = ("PASS", "DIRTY", "ESCALATE")
_COST_STEPS = ("upgrade", "self_assessment", "checkers")


# --------------------------------------------------------------------------- #
# Discovery (pure)
# --------------------------------------------------------------------------- #

def _within(path, root) -> bool:
    """True iff `path` resolves to a location inside `root` (or equal)."""
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _parse_marker(marker_path: Path) -> dict | None:
    """Parse the YAML frontmatter of one audit marker. Returns the dict, or None
    if the file is unreadable / has no frontmatter / is not a mapping (skipped —
    never raises)."""
    try:
        text = marker_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def discover_audit_markers(project_root, fallback_root=None) -> list[dict]:
    """Find every `/double-check` audit marker belonging to `project_root`.

    Looks BOTH co-located under the project root AND in the fallback mirror-tree
    (markers for read-only / adhoc / under-~/.claude sources land there). A marker
    belongs to the project iff its `source_path` resolves within `project_root` —
    that membership test is the authority, so a read-only-source run whose marker
    fell back to the mirror-tree still counts. Deduped by resolved marker path;
    malformed markers are skipped.

    Returns a list of parsed marker dicts (with an injected `_marker_path`).
    """
    project_root = Path(project_root).expanduser().resolve()
    fallback_root = Path(fallback_root) if fallback_root else Path(_ENGINE_FALLBACK_ROOT)

    candidates: list[Path] = []
    if project_root.is_dir():
        candidates.extend(project_root.rglob(MARKER_GLOB))
    if fallback_root.is_dir():
        candidates.extend(fallback_root.rglob(MARKER_GLOB))

    seen: set[str] = set()
    out: list[dict] = []
    for mp in candidates:
        key = str(mp.resolve())
        if key in seen:
            continue
        seen.add(key)
        data = _parse_marker(mp)
        if not data:
            continue
        src = data.get("source_path")
        if not src or not _within(src, project_root):
            continue
        data["_marker_path"] = key
        out.append(data)
    return out


# --------------------------------------------------------------------------- #
# Aggregation (pure)
# --------------------------------------------------------------------------- #

def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _float(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def aggregate_dc_stats(markers) -> "OrderedDict[tuple, dict]":
    """Group markers by `(caller, topic)` and sum the raw fields — no thresholds.

    Per group: runs, PASS/DIRTY/ESCALATE counts, tokens (upgrade+self_assessment
    +checkers), time (same three), claims_examined + discrepancies (across
    `checker_rows`). Returns an OrderedDict keyed `(caller, topic)`, sorted.
    """
    groups: dict[tuple, dict] = {}
    for m in markers:
        caller = str(m.get("caller", "") or "")
        topic = str(m.get("topic", "") or "")
        key = (caller, topic)
        g = groups.get(key)
        if g is None:
            g = {"caller": caller, "topic": topic, "runs": 0,
                 "PASS": 0, "DIRTY": 0, "ESCALATE": 0,
                 "tokens": 0, "time": 0.0, "claims": 0, "discrepancies": 0}
            groups[key] = g
        g["runs"] += 1
        verdict = str(m.get("verdict", "") or "")
        if verdict in _VERDICTS:
            g[verdict] += 1
        cost = m.get("cost") or {}
        if isinstance(cost, dict):
            for step in _COST_STEPS:
                entry = cost.get(step) or {}
                if isinstance(entry, dict):
                    g["tokens"] += _int(entry.get("tokens"))
                    g["time"] += _float(entry.get("time"))
        for row in (m.get("checker_rows") or []):
            if isinstance(row, dict):
                g["claims"] += _int(row.get("claims_examined"))
                g["discrepancies"] += _int(row.get("discrepancies"))
    return OrderedDict(sorted(groups.items(), key=lambda kv: kv[0]))


# --------------------------------------------------------------------------- #
# Rendering + write (find-or-replace; preserve manual content)
# --------------------------------------------------------------------------- #

def render_dc_stats_block(aggregates) -> str:
    """Render the sentinel-delimited managed block. Always returns a complete
    block (BEGIN…END); zero aggregates → an initialized table with no data rows."""
    lines = [
        BLOCK_BEGIN,
        "<!-- Auto-generated by dc_stats.py — edits inside this block are overwritten. -->",
        "## /double-check cost — by (caller, topic)",
        "",
        "| caller | topic | runs | PASS | DIRTY | ESCALATE | tokens | time(s) | claims | discrepancies |",
        "|--------|-------|------|------|-------|----------|--------|---------|--------|---------------|",
    ]
    for (_caller, _topic), g in aggregates.items():
        lines.append(
            f"| {g['caller'] or '—'} | {g['topic'] or '—'} | {g['runs']} | "
            f"{g['PASS']} | {g['DIRTY']} | {g['ESCALATE']} | {g['tokens']} | "
            f"{round(g['time'], 2)} | {g['claims']} | {g['discrepancies']} |"
        )
    if not aggregates:
        lines.append("| _(no /double-check runs found for this project yet)_ |||||||||| ")
    lines.append(BLOCK_END)
    return "\n".join(lines)


def _replace_or_append_block(existing: str, block: str) -> str:
    """Idempotent find-or-replace of the DC-STATS block, preserving everything
    else byte-for-byte. If both sentinels are present, replace the span between
    them (inclusive); otherwise append the block after the existing content."""
    begin = existing.find(BLOCK_BEGIN)
    end = existing.find(BLOCK_END)
    if begin != -1 and end != -1 and end > begin:
        end_full = end + len(BLOCK_END)
        return existing[:begin] + block + existing[end_full:]
    if not existing:
        return block + "\n"
    sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + block + "\n"


def write_dc_stats(project_root, fallback_root=None) -> dict:
    """Discover → aggregate → render → write the block into <project_root>/Stats.md.

    Preserves all content outside the sentinels (the `/close` session-metrics
    table + any manual prose). Creates Stats.md on first run. Returns a summary.
    """
    project_root = Path(project_root).expanduser().resolve()
    markers = discover_audit_markers(project_root, fallback_root=fallback_root)
    aggregates = aggregate_dc_stats(markers)
    block = render_dc_stats_block(aggregates)

    stats_path = project_root / "Stats.md"
    existing = stats_path.read_text(encoding="utf-8") if stats_path.exists() else ""
    new_text = _replace_or_append_block(existing, block)
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(new_text, encoding="utf-8")
    return {
        "stats_path": str(stats_path),
        "markers": len(markers),
        "groups": len(aggregates),
        "created": not existing,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: dc_stats.py <project_root>")
        return 0
    project_root = Path(argv[0]).expanduser()
    if not project_root.is_dir():
        sys.stderr.write(f"dc_stats: not a directory: {project_root}\n")
        return 2
    res = write_dc_stats(project_root)
    print(f"dc-stats: wrote {res['stats_path']} "
          f"({res['markers']} marker(s), {res['groups']} (caller,topic) group(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

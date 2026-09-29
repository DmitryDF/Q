#!/usr/bin/env python3
"""skill_metrics.py — reuse-first metrics substrate (orchestrator-pattern S5).

Formalizes the `skill_runs.py` seed (S3) into the full meter the delegation
refactor is judged by, REUSING existing substrate rather than new capture:

- **OMTM** (main-chat token delta per run) — the one genuinely-new capture; the
  orchestrator records it per run on the ledger row (`omtm_main_chat_delta`).
  Where a precise per-run delta is not available, it is left null and the
  session altimeter falls back to the session token total (below).
- **Secondary** (model-weighted token usage per run/session) — DERIVED, no new
  capture: reuse `research_token_parser.sum_tokens_by_model()` for per-model
  session totals, weight by tier. Guards the ~15x over-delegation trap.
- **Signals:** session altimeter (cumulative main-chat delta across the ledger),
  ceiling alarm (count of 1M-context-tier incidents), blocked-look-alike count
  (emitted by the S4 guardrail hook onto the ledger as `run_kind:"blocked"`).

Rolls the `skill-runs.jsonl` ledger into a managed Stats block for `/close`
Stats.md (mirrors the `dc_stats.py` DC-STATS managed-block pattern).

Standalone + stdlib-only. `research_token_parser` is imported for reuse but its
absence is tolerated (Secondary degrades to null). Unit-testable via
`python3 skill_metrics.py --self-test`.

CLI:
    python3 skill_metrics.py secondary <sid>     Print model-weighted usage for a session.
    python3 skill_metrics.py stats-block         Print the Stats managed block from the ledger.
    python3 skill_metrics.py --self-test
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:  # reuse-first: token accounting from the research parser
    import research_token_parser as _rtp
except Exception:  # pragma: no cover - tolerated absence
    _rtp = None

# Usage-intensity tier weights (editorial — relative subscription intensity, not
# dollars; Opus is the scarce tier the refactor moves routine work OFF of).
TIER_WEIGHTS = {"opus": 15, "sonnet": 3, "haiku": 1}

# 1M-context-tier alarm threshold (context tokens) — the credits-gated ceiling
# the whole refactor exists to stay under.
CEILING_TOKENS = 1_000_000

STATS_BEGIN = "<!-- SKILL-RUNS:BEGIN -->"
STATS_END = "<!-- SKILL-RUNS:END -->"


def _config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


def ledger_path(root: Path = None) -> Path:
    base = root if root is not None else _config_dir()
    return base / "state" / "metrics" / "skill-runs.jsonl"


def _tier_of(model: str) -> str:
    m = (model or "").lower()
    for tier in TIER_WEIGHTS:
        if tier in m:
            return tier
    return "sonnet"  # unknown families default to the mid tier


def model_weighted_total(per_model: dict) -> int:
    """Sum of each model's total_tokens * its tier weight (the Secondary metric)."""
    total = 0
    for model, counts in (per_model or {}).items():
        toks = counts.get("total_tokens", 0) if isinstance(counts, dict) else 0
        total += toks * TIER_WEIGHTS[_tier_of(model)]
    return total


def session_secondary(sid: str) -> dict:
    """Model-weighted usage for a session, via research_token_parser reuse."""
    if _rtp is None:
        return {"sid": sid, "per_model": {}, "secondary_model_weighted": None,
                "note": "research_token_parser unavailable"}
    tp = _rtp.find_transcript(sid)
    if not tp:
        return {"sid": sid, "per_model": {}, "secondary_model_weighted": None,
                "note": "transcript not found"}
    per_model = _rtp.sum_tokens_by_model(tp)
    return {"sid": sid, "per_model": per_model,
            "secondary_model_weighted": model_weighted_total(per_model)}


def read_ledger(root: Path = None) -> list:
    p = ledger_path(root)
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            rows.append(json.loads(ln))
        except ValueError:
            continue
    return rows


def aggregate(rows: list) -> dict:
    """Roll the ledger into the OMTM/Secondary metrics + the three signals."""
    by_skill = {}
    altimeter = 0            # cumulative main-chat delta (signal)
    ceiling_alarms = 0       # 1M-tier incidents (signal)
    blocked_lookalikes = 0   # guardrail-hook byproduct (signal)
    for r in rows:
        skill = r.get("skill", "?")
        kind = r.get("run_kind")
        if kind == "blocked":
            blocked_lookalikes += 1
            by_skill.setdefault(skill, {"runs": 0, "omtm_sum": 0, "omtm_n": 0,
                                        "secondary_sum": 0, "blocked": 0})
            by_skill[skill]["blocked"] += 1
            continue
        s = by_skill.setdefault(skill, {"runs": 0, "omtm_sum": 0, "omtm_n": 0,
                                        "secondary_sum": 0, "blocked": 0})
        s["runs"] += 1
        omtm = r.get("omtm_main_chat_delta")
        if isinstance(omtm, (int, float)):
            s["omtm_sum"] += omtm
            s["omtm_n"] += 1
            altimeter += omtm
        sec = r.get("secondary_model_weighted")
        if isinstance(sec, (int, float)):
            s["secondary_sum"] += sec
        if r.get("ceiling_incident") is True:
            ceiling_alarms += 1
    return {
        "by_skill": by_skill,
        "signals": {
            "session_altimeter_cumulative": altimeter,
            "ceiling_alarms": ceiling_alarms,
            "blocked_lookalikes": blocked_lookalikes,
        },
        "total_runs": sum(s["runs"] for s in by_skill.values()),
    }


def render_stats_block(agg: dict) -> str:
    """Managed markdown block for /close Stats.md (mirrors dc_stats DC-STATS)."""
    lines = [STATS_BEGIN, "### Skill-run metrics (orchestrator-pattern)", ""]
    lines.append("| Skill | Runs | Avg OMTM (main-chat Δ) | Σ Secondary (model-weighted) | Blocked look-alikes |")
    lines.append("|-------|------|------------------------|------------------------------|---------------------|")
    for skill, s in sorted(agg.get("by_skill", {}).items()):
        avg_omtm = round(s["omtm_sum"] / s["omtm_n"]) if s["omtm_n"] else "—"
        sec = s["secondary_sum"] or "—"
        lines.append(f"| {skill} | {s['runs']} | {avg_omtm} | {sec} | {s['blocked']} |")
    sig = agg.get("signals", {})
    lines.append("")
    lines.append(
        f"_Signals — altimeter (cumulative main-chat Δ): {sig.get('session_altimeter_cumulative', 0)} · "
        f"ceiling alarms (1M-tier incidents): {sig.get('ceiling_alarms', 0)} · "
        f"blocked look-alikes: {sig.get('blocked_lookalikes', 0)}._"
    )
    lines.append(STATS_END)
    return "\n".join(lines)


def upsert_stats_block(stats_md_text: str, block: str) -> str:
    """Replace the managed block in Stats.md text, or append it. Idempotent:
    both paths join with a single blank-line separator so re-running is a no-op."""
    if STATS_BEGIN in stats_md_text and STATS_END in stats_md_text:
        pre = stats_md_text.split(STATS_BEGIN)[0].rstrip("\n")
        post = stats_md_text.split(STATS_END, 1)[1].lstrip("\n")
        parts = [p for p in (pre, block, post) if p]
        return "\n\n".join(parts) + "\n"
    pre = stats_md_text.rstrip("\n")
    parts = [p for p in (pre, block) if p]
    return "\n\n".join(parts) + "\n"


def _self_test() -> int:
    # Tier weighting.
    pm = {"opus-4-8": {"total_tokens": 100}, "haiku-4-5": {"total_tokens": 100}}
    assert model_weighted_total(pm) == 100 * 15 + 100 * 1, model_weighted_total(pm)
    assert _tier_of("claude-sonnet-4-6") == "sonnet"
    assert _tier_of("weird-model") == "sonnet"  # unknown → mid tier
    # Aggregation over a mixed ledger.
    rows = [
        {"skill": "close", "run_kind": "full", "omtm_main_chat_delta": 200,
         "secondary_model_weighted": 3000},
        {"skill": "close", "run_kind": "full", "omtm_main_chat_delta": 100,
         "secondary_model_weighted": 1500, "ceiling_incident": False},
        {"skill": "close", "run_kind": "blocked"},  # a blocked look-alike
        {"skill": "daily-discovery", "run_kind": "baseline",
         "omtm_main_chat_delta": None},
    ]
    agg = aggregate(rows)
    assert agg["signals"]["session_altimeter_cumulative"] == 300, agg
    assert agg["signals"]["blocked_lookalikes"] == 1, agg
    assert agg["by_skill"]["close"]["runs"] == 2, agg
    assert agg["by_skill"]["close"]["blocked"] == 1, agg
    # Stats block renders + upserts idempotently.
    block = render_stats_block(agg)
    assert STATS_BEGIN in block and STATS_END in block
    once = upsert_stats_block("# Stats\n\nold\n", block)
    twice = upsert_stats_block(once, render_stats_block(agg))
    assert once == twice, "upsert not idempotent"
    assert once.count(STATS_BEGIN) == 1, "duplicate managed block"
    print("skill_metrics.py self-test: PASS")
    return 0


def main(argv) -> int:
    if "--self-test" in argv:
        return _self_test()
    if not argv:
        print("usage: skill_metrics.py {secondary <sid>|stats-block|--self-test}",
              file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "secondary":
        if len(argv) < 2:
            print("secondary requires <sid>", file=sys.stderr)
            return 2
        print(json.dumps(session_secondary(argv[1])))
        return 0
    if cmd == "stats-block":
        print(render_stats_block(aggregate(read_ledger())))
        return 0
    if cmd == "write-stats":
        if len(argv) < 2:
            print("write-stats requires <stats_md_path>", file=sys.stderr)
            return 2
        target = Path(argv[1])
        block = render_stats_block(aggregate(read_ledger()))
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(upsert_stats_block(existing, block), encoding="utf-8")
        print(json.dumps({"status": "wrote", "stats": str(target)}))
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

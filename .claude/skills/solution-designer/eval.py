"""solution-designer eval — deterministic checks only.

Schema validation + discovery-anchor coverage ratio + SHA-256 change-impact diff.
Judgment-quality eval lives in /double-check calls invoked separately by the caller.

Usage:
    python3 eval.py --fixture <path-to-fixture.json> \
                    --skill-output <path-to-captured-output.json> \
                    [--baseline <path-to-prior-output.json>]

Exits 0 if schema validation passes (independent of the metric/diff values).
Exits 1 on schema failure or unreadable inputs. Always prints a JSON report to stdout.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path

REQUIRED_INPUT_FIELDS = (
    "guiding_policy",
    "desired_outcome",
    "desired_solution",
    "metrics",
    "discovery_src_hash",
)

REQUIRED_OUTPUT_FIELDS = (
    "agreed_solution_description",
    "architecture_decisions",
    "ux_decisions",
    "cockburn_gate_results",
    "confidence",
    "assumptions",
    "discovery_anchors",
    "model_used",
)

HARD_GATES = ("abstraction_test", "responsibility_alignment_test")
ADVISORY_GATES = (
    "evolution_test",
    "communications_patterns_test",
    "data_connectedness_test",
    "data_variations_test",
)
DECISION_FIELDS = ("area", "choice", "rationale", "discovery_anchor")


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def validate_fixture(fixture: dict) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_INPUT_FIELDS:
        if field not in fixture:
            errors.append(f"fixture missing required input field: {field}")
        elif fixture[field] in (None, "") or (isinstance(fixture[field], str) and not fixture[field].strip()):
            errors.append(f"fixture field is empty: {field}")
    if "model" in fixture and fixture["model"] not in ("sonnet", "opus"):
        errors.append(f"fixture.model must be 'sonnet' or 'opus' (got {fixture['model']!r})")
    return errors


def validate_output(output: dict) -> list[str]:
    errors: list[str] = []
    if output.get("error"):
        return errors  # error envelope is a legal terminal state; skip the rest.
    for field in REQUIRED_OUTPUT_FIELDS:
        if field not in output:
            errors.append(f"output missing required field: {field}")
    if "confidence" in output and output["confidence"] not in ("low", "medium", "high"):
        errors.append(f"output.confidence must be low|medium|high (got {output['confidence']!r})")
    if "model_used" in output and output["model_used"] not in ("sonnet", "opus"):
        errors.append(f"output.model_used must be sonnet|opus (got {output['model_used']!r})")
    gate_results = output.get("cockburn_gate_results", {})
    for gate in HARD_GATES:
        if gate not in gate_results:
            errors.append(f"cockburn_gate_results missing hard gate: {gate}")
            continue
        if not isinstance(gate_results[gate].get("passed"), bool):
            errors.append(f"cockburn_gate_results.{gate}.passed must be boolean")
    for gate in ADVISORY_GATES:
        if gate not in gate_results:
            errors.append(f"cockburn_gate_results missing advisory gate: {gate}")
    for list_field in ("architecture_decisions", "ux_decisions"):
        for idx, decision in enumerate(output.get(list_field, [])):
            for sub in DECISION_FIELDS:
                if sub not in decision:
                    errors.append(f"{list_field}[{idx}] missing sub-field: {sub}")
    return errors


def anchor_coverage_ratio(output: dict) -> tuple[float | None, int, int]:
    decisions = list(output.get("architecture_decisions", [])) + list(output.get("ux_decisions", []))
    total = len(decisions)
    if total == 0:
        return None, 0, 0
    grounded = sum(1 for d in decisions if (d.get("discovery_anchor") or "").strip())
    return grounded / total, grounded, total


def _normalize(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, indent=2)


def change_impact(output: dict, baseline_path: Path | None) -> dict:
    normalized = _normalize(output)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    if baseline_path is None:
        return {"sha256": digest, "baseline_absent": True, "diff": None}
    try:
        baseline = _load_json(baseline_path)
    except (OSError, json.JSONDecodeError) as exc:
        return {"sha256": digest, "baseline_error": str(exc), "diff": None}
    baseline_normalized = _normalize(baseline)
    if baseline_normalized == normalized:
        return {"sha256": digest, "diff": ""}
    diff_lines = difflib.unified_diff(
        baseline_normalized.splitlines(keepends=True),
        normalized.splitlines(keepends=True),
        fromfile=str(baseline_path),
        tofile="skill-output",
    )
    return {"sha256": digest, "diff": "".join(diff_lines)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="solution-designer deterministic eval")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--skill-output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, default=None)
    args = parser.parse_args(argv)

    report: dict = {"fixture": str(args.fixture), "skill_output": str(args.skill_output)}
    try:
        fixture = _load_json(args.fixture)
        output = _load_json(args.skill_output)
    except (OSError, json.JSONDecodeError) as exc:
        report["load_error"] = str(exc)
        print(json.dumps(report, indent=2))
        return 1

    fixture_errors = validate_fixture(fixture)
    output_errors = validate_output(output)
    report["fixture_schema_valid"] = not fixture_errors
    report["output_schema_valid"] = not output_errors
    if fixture_errors:
        report["fixture_errors"] = fixture_errors
    if output_errors:
        report["output_errors"] = output_errors

    ratio, grounded, total = anchor_coverage_ratio(output)
    report["anchor_coverage"] = {"ratio": ratio, "grounded": grounded, "total_decisions": total}
    report["change_impact"] = change_impact(output, args.baseline)

    print(json.dumps(report, indent=2))
    return 0 if (not fixture_errors and not output_errors) else 1


if __name__ == "__main__":
    sys.exit(main())

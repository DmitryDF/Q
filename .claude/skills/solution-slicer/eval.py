"""solution-slicer eval — deterministic checks only.

Schema validation + per-slice consistency checks (closing-slice rule, agent-justification
cross-field check, slicing-idea enum) + SHA-256 change-impact diff.
Judgment-quality eval lives in /double-check calls invoked separately by the caller.

Usage:
    python3 eval.py --fixture <path-to-fixture.json> \\
                    --skill-output <path-to-captured-output.json> \\
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
    "plan_path_or_content",
    "desired_outcome",
)

REQUIRED_OUTPUT_FIELDS = (
    "assessment",
    "slices",
    "aggregation_metadata",
    "verification_metadata",
    "confidence",
    "assumptions",
)

REQUIRED_ASSESSMENT_FIELDS = (
    "slicing_needed",
    "rationale",
)

REQUIRED_SLICE_FIELDS = (
    "id",
    "name",
    "description",
    "type",
    "cohesion_rationale",
    "single_reason_for_change",
    "slicing_idea_applied",
    "agent_choice",
    "agent_justification",
    "context_fit_assessment",
    "depends_on",
)

SLICE_TYPE_ENUM = ("implementation", "implementation_verification")

# Valid for implementation slices; implementation_verification slices have no constraint.
SLICING_IDEAS = (
    "walking_skeleton",
    "sentence_extension",
    "partial_step",
    "different_ways",
    "data_rules",
    "knowledge_vs_implementation",
)

AGENT_ENUM = ("routine", "more_capable")

REQUIRED_AGGREGATION_FIELDS = (
    "assessors_run",
    "disagreements",
)

REQUIRED_VERIFICATION_FIELDS = (
    "double_check_verdict",
    "claims_checked",
    "rounds_run",
    "against",
)

VERDICT_ENUM = ("PASS", "ESCALATE")

CONFIDENCE_ENUM = ("low", "medium", "high")


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


def validate_fixture(fixture: dict) -> list[str]:
    errors: list[str] = []
    for field in REQUIRED_INPUT_FIELDS:
        if field not in fixture:
            errors.append(f"fixture missing required input field: {field}")
        elif fixture[field] in (None, "") or (
            isinstance(fixture[field], str) and not fixture[field].strip()
        ):
            errors.append(f"fixture field is empty: {field}")
    return errors


def validate_output(output: dict) -> list[str]:
    errors: list[str] = []

    # Error envelope is a legal terminal state.
    if output.get("error"):
        return errors

    # Top-level required fields.
    for field in REQUIRED_OUTPUT_FIELDS:
        if field not in output:
            errors.append(f"output missing required field: {field}")

    # assessment sub-object.
    assessment = output.get("assessment")
    if assessment is None:
        errors.append("output.assessment is null or missing")
    elif isinstance(assessment, dict):
        for sub in REQUIRED_ASSESSMENT_FIELDS:
            if sub not in assessment:
                errors.append(f"output.assessment missing required sub-field: {sub}")
        if "rationale" in assessment and not (assessment.get("rationale") or "").strip():
            errors.append("output.assessment.rationale must be non-empty")

    # confidence enum.
    if "confidence" in output and output["confidence"] not in CONFIDENCE_ENUM:
        errors.append(
            f"output.confidence must be one of {CONFIDENCE_ENUM} (got {output['confidence']!r})"
        )

    # Branch on slicing_needed.
    slicing_needed = (assessment or {}).get("slicing_needed")
    slices = output.get("slices", [])

    if slicing_needed is False:
        # assess-first short-circuit path: slices must be empty.
        if slices:
            errors.append(
                "output.slices must be [] when assessment.slicing_needed is false"
            )
    elif slicing_needed is True:
        # Slices must be non-empty.
        if not slices:
            errors.append(
                "output.slices must be non-empty when assessment.slicing_needed is true"
            )
        else:
            # Per-slice validation.
            for idx, sl in enumerate(slices):
                for sub in REQUIRED_SLICE_FIELDS:
                    if sub not in sl:
                        errors.append(f"slices[{idx}] missing required sub-field: {sub}")

                sl_type = sl.get("type")
                if sl_type not in SLICE_TYPE_ENUM:
                    errors.append(
                        f"slices[{idx}].type must be one of {SLICE_TYPE_ENUM} "
                        f"(got {sl_type!r})"
                    )

                # agent_choice enum + cross-field justification check.
                agent_choice = sl.get("agent_choice")
                if agent_choice not in AGENT_ENUM:
                    errors.append(
                        f"slices[{idx}].agent_choice must be one of {AGENT_ENUM} "
                        f"(got {agent_choice!r})"
                    )
                if agent_choice == "more_capable":
                    if not (sl.get("agent_justification") or "").strip():
                        errors.append(
                            f"slices[{idx}].agent_justification must be non-empty "
                            f"when agent_choice is 'more_capable'"
                        )

                # context_fit_assessment non-empty.
                if not (sl.get("context_fit_assessment") or "").strip():
                    errors.append(
                        f"slices[{idx}].context_fit_assessment must be non-empty"
                    )

                # single_reason_for_change non-empty.
                if not (sl.get("single_reason_for_change") or "").strip():
                    errors.append(
                        f"slices[{idx}].single_reason_for_change must be non-empty"
                    )

                # slicing_idea_applied enum — only for non-verification slices.
                if sl_type == "implementation":
                    idea = sl.get("slicing_idea_applied")
                    if idea not in SLICING_IDEAS:
                        errors.append(
                            f"slices[{idx}].slicing_idea_applied must be one of "
                            f"{SLICING_IDEAS} for implementation slices (got {idea!r})"
                        )

            # Closing-slice rule: last slice must be implementation_verification.
            last_type = slices[-1].get("type") if slices else None
            if last_type != "implementation_verification":
                errors.append(
                    f"slices[-1].type must be 'implementation_verification' "
                    f"(closing-slice rule); got {last_type!r}"
                )

    # aggregation_metadata.
    agg = output.get("aggregation_metadata")
    if agg is None:
        errors.append("output.aggregation_metadata is null or missing")
    elif isinstance(agg, dict):
        for sub in REQUIRED_AGGREGATION_FIELDS:
            if sub not in agg:
                errors.append(
                    f"output.aggregation_metadata missing required sub-field: {sub}"
                )
        if "assessors_run" in agg and not agg["assessors_run"]:
            errors.append(
                "output.aggregation_metadata.assessors_run must be a non-empty list"
            )

    # verification_metadata.
    vmeta = output.get("verification_metadata")
    if vmeta is None:
        errors.append("output.verification_metadata is null or missing")
    elif isinstance(vmeta, dict):
        for sub in REQUIRED_VERIFICATION_FIELDS:
            if sub not in vmeta:
                errors.append(
                    f"output.verification_metadata missing required sub-field: {sub}"
                )
        verdict = vmeta.get("double_check_verdict")
        if verdict not in VERDICT_ENUM:
            errors.append(
                f"output.verification_metadata.double_check_verdict must be one of "
                f"{VERDICT_ENUM} (got {verdict!r})"
            )

    return errors


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
    parser = argparse.ArgumentParser(description="solution-slicer deterministic eval")
    parser.add_argument("--fixture", required=True, type=Path)
    parser.add_argument("--skill-output", required=True, type=Path)
    parser.add_argument("--baseline", type=Path, default=None)
    args = parser.parse_args(argv)

    report: dict = {
        "fixture": str(args.fixture),
        "skill_output": str(args.skill_output),
    }
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

    report["change_impact"] = change_impact(output, args.baseline)

    print(json.dumps(report, indent=2))
    return 0 if (not fixture_errors and not output_errors) else 1


if __name__ == "__main__":
    sys.exit(main())

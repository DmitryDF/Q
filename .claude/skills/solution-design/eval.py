"""solution-design eval — deterministic checks only.

Schema validation + cross-field consistency checks (monotonic-N rule, discovery_src_hash
match, supersession marker, verification metadata completeness) + SHA-256 change-impact
diff. Judgment-quality eval lives in /double-check calls invoked separately by the caller.

Usage:
    python3 eval.py --fixture <path-to-fixture.json> \\
                    --skill-output <path-to-captured-output.json> \\
                    [--baseline <path-to-prior-output.json>]

Exits 0 if both schemas valid and no cross-field errors.
Exits 1 on schema/cross-field failure or unreadable inputs.
Always prints a JSON report to stdout.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from pathlib import Path

# --- Input fixture schema ---

REQUIRED_INPUT_FIELDS = (
    "discovery_src_hash",
    "desired_outcome",
    "candidates",
)

REQUIRED_CANDIDATE_FIELDS = (
    "candidate_id",
    "model",
    "b1_output",
)

CANDIDATE_MODEL_ENUM = ("sonnet", "opus")

REQUIRED_B1_OUTPUT_FIELDS = (
    "agreed_solution_description",
    "architecture_decisions",
    "ux_decisions",
    "cockburn_gate_results",
    "confidence",
    "assumptions",
    "discovery_anchors",
    "model_used",
)

B1_CONFIDENCE_ENUM = ("low", "medium", "high")
B1_MODEL_ENUM = ("sonnet", "opus")

REQUIRED_COCKBURN_FIELDS = (
    "abstraction_test",
    "responsibility_alignment_test",
)

# --- Skill output schema ---

REQUIRED_OUTPUT_FIELDS = (
    "alternative_n",
    "date",
    "status",
    "discovery_src_hash",
    "selector_ranking_summary",
    "chosen_candidate_id",
    "chosen_candidate",
    "slices",
    "verification_metadata",
    "aggregation_metadata",
    "supersession_marker_written",
)

STATUS_ENUM_PREFIX = ("chosen", "superseded by Alternative")

REQUIRED_VERIFICATION_FIELDS = (
    "selector_ranking",
    "final_output",
)

REQUIRED_DOUBLE_CHECK_FIELDS = (
    "double_check_verdict",
    "claims_checked",
    "rounds_run",
    "against",
)

VERDICT_ENUM = ("PASS", "ESCALATE")

REQUIRED_AGGREGATION_FIELDS = (
    "candidates_evaluated",
    "design_rounds",
    "slice_rounds",
    "user_design_pick",
    "user_override_of_rank",
    "reclarification_gate_counts",
)

CONFIDENCE_ENUM = ("low", "medium", "high")


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as fp:
        return json.load(fp)


# ---------------------------------------------------------------------------
# Fixture validation
# ---------------------------------------------------------------------------

def validate_fixture(fixture: dict) -> list[str]:
    errors: list[str] = []

    for field in REQUIRED_INPUT_FIELDS:
        if field not in fixture:
            errors.append(f"fixture missing required field: {field}")
        elif fixture[field] in (None, "") or (
            isinstance(fixture[field], str) and not fixture[field].strip()
        ):
            errors.append(f"fixture field is empty: {field}")

    candidates = fixture.get("candidates")
    if isinstance(candidates, list):
        if not candidates:
            errors.append("fixture.candidates must be a non-empty list")
        for idx, cand in enumerate(candidates):
            if not isinstance(cand, dict):
                errors.append(f"fixture.candidates[{idx}] must be an object")
                continue
            for sub in REQUIRED_CANDIDATE_FIELDS:
                if sub not in cand:
                    errors.append(
                        f"fixture.candidates[{idx}] missing required field: {sub}"
                    )
            model = cand.get("model")
            if model is not None and model not in CANDIDATE_MODEL_ENUM:
                errors.append(
                    f"fixture.candidates[{idx}].model must be one of "
                    f"{CANDIDATE_MODEL_ENUM} (got {model!r})"
                )
            b1 = cand.get("b1_output")
            if isinstance(b1, dict):
                errors.extend(_validate_b1_output(b1, f"fixture.candidates[{idx}].b1_output"))
            elif b1 is not None:
                errors.append(
                    f"fixture.candidates[{idx}].b1_output must be an object (B1 error "
                    "envelope is also accepted — omit b1_output or use an error key)"
                )

    return errors


def _validate_b1_output(b1: dict, prefix: str) -> list[str]:
    """Validate B1 output fields (used for both fixture candidates and chosen_candidate)."""
    errors: list[str] = []

    # Error envelope is a legal B1 terminal state.
    if b1.get("error"):
        return errors

    for field in REQUIRED_B1_OUTPUT_FIELDS:
        if field not in b1:
            errors.append(f"{prefix} missing required B1 field: {field}")

    confidence = b1.get("confidence")
    if confidence is not None and confidence not in B1_CONFIDENCE_ENUM:
        errors.append(
            f"{prefix}.confidence must be one of {B1_CONFIDENCE_ENUM} (got {confidence!r})"
        )

    model_used = b1.get("model_used")
    if model_used is not None and model_used not in B1_MODEL_ENUM:
        errors.append(
            f"{prefix}.model_used must be one of {B1_MODEL_ENUM} (got {model_used!r})"
        )

    cockburn = b1.get("cockburn_gate_results")
    if isinstance(cockburn, dict):
        for sub in REQUIRED_COCKBURN_FIELDS:
            if sub not in cockburn:
                errors.append(f"{prefix}.cockburn_gate_results missing sub-field: {sub}")
            elif isinstance(cockburn.get(sub), dict):
                if "passed" not in cockburn[sub]:
                    errors.append(
                        f"{prefix}.cockburn_gate_results.{sub} missing 'passed' boolean"
                    )

    return errors


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------

def validate_output(output: dict) -> list[str]:
    errors: list[str] = []

    # Error envelope is a legal terminal state (e.g. discovery_section_absent).
    if output.get("error"):
        return errors

    for field in REQUIRED_OUTPUT_FIELDS:
        if field not in output:
            errors.append(f"output missing required field: {field}")

    # alternative_n must be a positive integer.
    alt_n = output.get("alternative_n")
    if alt_n is not None:
        if not isinstance(alt_n, int) or alt_n < 1:
            errors.append(
                f"output.alternative_n must be a positive integer (got {alt_n!r})"
            )

    # status enum (prefix check — "superseded by Alternative N+1" varies by N).
    status = output.get("status")
    if status is not None:
        if status not in ("chosen",) and not (
            isinstance(status, str) and status.startswith("superseded by Alternative")
        ):
            errors.append(
                f"output.status must be 'chosen' or 'superseded by Alternative <N+1>' "
                f"(got {status!r})"
            )

    # selector_ranking_summary non-empty.
    summary = output.get("selector_ranking_summary")
    if summary is not None and not (isinstance(summary, str) and summary.strip()):
        errors.append("output.selector_ranking_summary must be a non-empty string")

    # chosen_candidate (validate as B1 output).
    chosen = output.get("chosen_candidate")
    if chosen is None:
        errors.append("output.chosen_candidate is null or missing")
    elif isinstance(chosen, dict):
        errors.extend(_validate_b1_output(chosen, "output.chosen_candidate"))

    # slices must be a list (may be empty when slicing_needed: false).
    slices = output.get("slices")
    if slices is not None and not isinstance(slices, list):
        errors.append("output.slices must be a list (possibly empty)")

    # verification_metadata — both sub-objects required.
    vmeta = output.get("verification_metadata")
    if vmeta is None:
        errors.append("output.verification_metadata is null or missing")
    elif isinstance(vmeta, dict):
        for sub in REQUIRED_VERIFICATION_FIELDS:
            if sub not in vmeta:
                errors.append(
                    f"output.verification_metadata missing required sub-object: {sub}"
                )
            else:
                dc = vmeta[sub]
                if isinstance(dc, dict):
                    for dc_field in REQUIRED_DOUBLE_CHECK_FIELDS:
                        if dc_field not in dc:
                            errors.append(
                                f"output.verification_metadata.{sub} missing "
                                f"required field: {dc_field}"
                            )
                    verdict = dc.get("double_check_verdict")
                    if verdict is not None and verdict not in VERDICT_ENUM:
                        errors.append(
                            f"output.verification_metadata.{sub}.double_check_verdict "
                            f"must be one of {VERDICT_ENUM} (got {verdict!r})"
                        )

    # aggregation_metadata.
    agg = output.get("aggregation_metadata")
    if agg is None:
        errors.append("output.aggregation_metadata is null or missing")
    elif isinstance(agg, dict):
        for sub in REQUIRED_AGGREGATION_FIELDS:
            if sub not in agg:
                errors.append(
                    f"output.aggregation_metadata missing required field: {sub}"
                )

    # supersession_marker_written must be a boolean.
    smw = output.get("supersession_marker_written")
    if smw is not None and not isinstance(smw, bool):
        errors.append(
            f"output.supersession_marker_written must be a boolean (got {smw!r})"
        )

    return errors


# ---------------------------------------------------------------------------
# Cross-field checks
# ---------------------------------------------------------------------------

def cross_field_checks(fixture: dict, output: dict) -> list[str]:
    """Checks that relate fixture inputs to output fields."""
    errors: list[str] = []

    # Error envelope short-circuit — no cross-field checks on error output.
    if output.get("error"):
        return errors

    # (a) discovery_src_hash present in output.
    out_hash = output.get("discovery_src_hash")
    if not (isinstance(out_hash, str) and out_hash.strip()):
        errors.append("output.discovery_src_hash is required and must be non-empty")
        # Cannot do hash-match check if field is absent.
    else:
        # (e) hash must match fixture hash.
        fix_hash = fixture.get("discovery_src_hash", "")
        if out_hash != fix_hash:
            errors.append(
                f"output.discovery_src_hash mismatch — chosen candidate was dispatched "
                f"against a different Discovery state "
                f"(fixture: {fix_hash!r}, output: {out_hash!r})"
            )

    # (b) monotonic-N rule.
    prior_n = fixture.get("prior_alternative_n")
    alt_n = output.get("alternative_n")
    if (
        prior_n is not None
        and isinstance(prior_n, int)
        and prior_n > 0
        and isinstance(alt_n, int)
        and alt_n <= prior_n
    ):
        errors.append(
            f"monotonic-N rule violated: output.alternative_n ({alt_n}) must be "
            f"greater than fixture.prior_alternative_n ({prior_n})"
        )

    # (c) supersession marker required when prior_n > 0.
    if (
        prior_n is not None
        and isinstance(prior_n, int)
        and prior_n > 0
    ):
        smw = output.get("supersession_marker_written")
        if smw is not True:
            errors.append(
                "prior alternative not marked superseded — "
                "output.supersession_marker_written must be true when "
                f"fixture.prior_alternative_n > 0 (got {smw!r})"
            )

    # (d) both verification_metadata sub-objects must be present (already checked in
    # validate_output, but cross-field context adds clarity — skip double-reporting).

    return errors


# ---------------------------------------------------------------------------
# SHA-256 change-impact diff
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="solution-design deterministic eval",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Validates the input fixture schema and the skill output schema.\n"
            "Cross-field checks: discovery_src_hash match, monotonic-N rule,\n"
            "supersession marker, verification_metadata completeness.\n"
            "Exits 0 on PASS, 1 on schema/cross-field failure."
        ),
    )
    parser.add_argument("--fixture", required=True, type=Path,
                        help="Path to input fixture JSON file")
    parser.add_argument("--skill-output", required=True, type=Path,
                        help="Path to captured skill output JSON file")
    parser.add_argument("--baseline", type=Path, default=None,
                        help="Path to prior skill output for change-impact diff")
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
    cf_errors = cross_field_checks(fixture, output)

    report["fixture_schema_valid"] = not fixture_errors
    report["output_schema_valid"] = not output_errors
    report["cross_field_errors"] = cf_errors

    if fixture_errors:
        report["fixture_errors"] = fixture_errors
    if output_errors:
        report["output_errors"] = output_errors

    report["change_impact"] = change_impact(output, args.baseline)

    print(json.dumps(report, indent=2))

    overall_pass = not fixture_errors and not output_errors and not cf_errors
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())

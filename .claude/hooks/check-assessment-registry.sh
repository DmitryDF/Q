#!/usr/bin/env bash
# check-assessment-registry.sh
#
# Commit-time drift guard for the assessment engine's Per-Kind Dimension Registry
# (Slice S2). Asserts the code-authoritative ASSESSMENT_DIMENSION_REGISTRY (in
# assessment_engine.py) and its human-readable mirror
# (~/.claude/rules/assessment-dimension-registry.md) carry the SAME
# (kind -> {rigor, dims}) catalog. The one comparison function lives in the engine;
# this script is the commit-time surface (mirrors check-double-check-allocation.sh).
#
# Overridable via env:
#   ASSESS_ENGINE                 (default: ${KIT_HOOKS_DIR}/assessment_engine.py)
#   ASSESSMENT_REGISTRY_RULES_FILE (consumed by the engine CLI; used by the test
#                                   harness to point at a synthetic-divergence copy)
#
# Exit codes:
#   0 — code registry and rules mirror are consistent
#   1 — drift (stderr names the divergent kind/dimension)
#   2 — engine not found / engine error

set -u

ENGINE="${ASSESS_ENGINE:-${KIT_HOOKS_DIR}/assessment_engine.py}"

if [ ! -f "$ENGINE" ]; then
  echo "FAIL: engine not found: $ENGINE" >&2
  exit 2
fi

python3 "$ENGINE" check-registry-drift
exit $?

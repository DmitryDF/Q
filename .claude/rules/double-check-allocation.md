# /double-check Allocation & Axis Catalog (Layer-2 mirror)

This file is the **human-readable mirror** of the code-authoritative
`DC_AXIS_REGISTRY` in `${KIT_HOOKS_DIR}/_factcheck_engine.py`. It carries the
per-artifact-type axis catalog and the shared allocation rule for the
`/double-check` Validation engine.

**Code is authoritative; this file is the asserted mirror.** A drift-comparison
function (`check_allocation_drift` in the engine) normalizes both this file's
catalog table and the code registry to `(artifact_type → axis-set)` maps and
**hard-fails on any divergence, naming the divergent type/axis** — at engine load
(lazily, on the double-check audit path) AND at commit time (via
`${KIT_HOOKS_DIR}/check-double-check-allocation.sh`). The two copies cannot diverge
silently. Edit one without the other and the affected path refuses to run and the
commit is blocked.

Sibling of `~/.claude/rules/factcheck-convergence.md` (the convergence canon).

---

## Axis universe

The axis universe is locked at Discovery Q4: `{groundedness, coverage, source-quality}`.
Every artifact type's axis set is a subset of this universe.

- **groundedness** — every factual claim is traceable to a named, inspectable proof
  source; no claim rests on an unstated assumption. **Always required** — present in
  every type's axis set.
- **coverage** — the artifact addresses the full scope it asserts; no in-scope
  requirement, gap, or case is silently dropped.
- **source-quality** — each cited source is authoritative and appropriate for the
  claim category it backs (per the Verification Source Registry), not a weaker or
  category-mismatched substitute.

**Comprehensiveness floor:** groundedness is always present, and every type carries
**at least two axes**. The full per-axis `AxisSpec` (operational_definition /
pass_criteria / failure_modes) lives in the code registry and is what the checker
prompt interpolates; this file mirrors only the per-type axis **set**.

---

## Per-type axis catalog

The locked artifact types and their axis sets. This table is what the drift
detector parses — it must match `DC_AXIS_REGISTRY` exactly.

| Artifact type | Axes |
|---|---|
| `recommendation` | groundedness, coverage, source-quality |
| `code-recommendation` | groundedness, coverage |
| `strategic-kernel` | groundedness, coverage |
| `design` | groundedness, coverage |
| `discovery-framing` | groundedness, coverage |
| `metrics-validation` | groundedness, source-quality |
| `research` | groundedness, coverage, source-quality |
| `plan` | groundedness, coverage |
| `scope` | groundedness, coverage |
| `audit` | groundedness, coverage |
| `review` | groundedness, coverage |
| `kl-extraction` | groundedness, coverage, source-quality |

**Editorial basis (Design Review, grounded to Q4).** coverage applies to every type
that declares a scope to cover (the common case). source-quality is added for the
types whose verdict leans on external/cited source authority —
`recommendation`, `metrics-validation`, `research`, `kl-extraction`.

---

## Allocation rule

The shared per-artifact-type allocation rule (one table for all types). This is
**documentation** — the enforcing `validate_allocation()` validator lands in S3; no
enforcement logic lives in this prose (Layer 2 = data/rules, not code).

- **`{1, 3, 4}` per model.** Round 1 dispatches `1` specific-angle checker. On
  DIRTY, Round 2 dispatches `3` specific-angle checkers plus an Opus advisory.
  Cap = `4` checkers for a single-model profile / `3` for a two-model profile.
- **`N = 2` is disallowed.** A two-specific-checker allocation is rejected — the
  rule goes `1 → 3`, never `1 → 2`.
- **Opus advisory is non-binding.** The Round-2 Opus checker is an advisory cascade;
  its verdict is **not** counted toward unanimity (the convergence verdict is
  decided by the Sonnet checkers per `factcheck-convergence.md` §1).
- **`default_allocation`** per type is `"1,3,4"` (mirrored in the code registry).

---

## Consumed by

- `_factcheck_engine.py` — `DC_AXIS_REGISTRY` (authoritative source), `check_allocation_drift`
  (the comparison function), `_assert_dc_allocation_consistent` (lazy load-time guard),
  `cmd_check_allocation_drift` (the `check-allocation-drift` CLI).
- `${KIT_HOOKS_DIR}/check-double-check-allocation.sh` — commit-time drift guard (invokes
  the CLI; exit ≠ 0 + stderr names the divergent type/axis).
- S3 — interpolates each type's `AxisSpec` (from the code registry) into the checker prompt.

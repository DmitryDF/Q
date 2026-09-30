# /double-check redesign — decisions-observability acceptance matrix (Slice S9 / A2)

Plan: `Thoughts/double-check-validation-targeting_S9_PLAN.md` (Coherent Action A2).

This is the closing acceptance record: every one of the **15 architecture decisions
(A1–A15)** and **4 UX decisions (UX1–UX4)** from the chosen design (spine
`### Solution Alternative 1`) mapped to its **observable evidence** — a named test
(`module::Case`) or a shipped artifact (file + what to look for). Every pointer below
resolves on disk. The end-to-end composition is proved by
`tests/test_dc_e2e.py` (the 6 acceptance points); the per-slice unit suites
(`tests/test_dc_*.py`) prove each clause in isolation.

Conventions: paths are relative to `~/.claude/`. `test_dc_e2e.py` points are
`TestPoint<N>...` / `TestPoints3and4Compose`.

## Architecture decisions (A1–A15)

| Decision | What it is | Observable evidence |
|----------|------------|---------------------|
| **A1** | Audit-marker substrate — a per-run YAML-frontmatter doc the engine writes, linking its `R<N>.md` children | `hooks/tests/test_dc_audit_marker.py` (marker written + schema); `test_dc_e2e.py::TestPoints3and4Compose` writes a real marker via `assemble_audit_marker`+`write_audit_marker` and reads its frontmatter |
| **A2** | Marker Pydantic schema validated at write time (field groups; optional signal fields) | `_factcheck_engine.py` `class AuditMarker` (≈1608, `extra="forbid"`); `test_dc_audit_marker.py` (validation-before-persist); `test_dc_e2e.py::TestPoints3and4Compose` (per-claim verdicts + cost + per-angle rows in frontmatter) |
| **A3** | Layer-1 in-engine: registry + allocation validator + `AuditMarker` inside `_factcheck_engine.py` (single change locus, no new top-level module) | `_factcheck_engine.py` (all `DC_*` / `assemble_audit_marker` / `validate_allocation` in-engine); `test_dc_allocation.py::TestC1Catalog` |
| **A4** | Single shared allocation table; `{1,3,4}` per model; N=2 rejected; Opus advisory non-binding | `rules/double-check-allocation.md` (Allocation rule); `_factcheck_engine.py` `DC_AXIS_REGISTRY` `default_allocation="1,3,4"`; `test_dc_allocation.py::TestC1Catalog::test_default_allocation_is_134_per_type` |
| **A5** | Sonnet pre-check agent (parent-spawned read-only `Explore`; gather-and-structure, no verdict authority) | `skills/double-check/SKILL.md` pre-check step (≈step 1–2, `## Execution`); `test_dc_precheck.py` |
| **A6** | Angle propagation: registry `AxisSpec` → checker prompt → one axis = one checker = one marker row | `_factcheck_engine.py` `class CheckerRow` (`angle`); `test_dc_dispatch.py::*real_dispatch_data*`; `test_dc_e2e.py::TestPoints3and4Compose` (per-angle `discrepancies` in frontmatter) |
| **A7** | `--auto`: code-validated whitelist + schema-conformant payload + explicit flag (fail-closed); bypass leaves `pre_check_layer` absent; `auto_caller` recorded | `_factcheck_engine.py` `validate_auto_request` (≈1532), `DC_AUTO_CALLER_WHITELIST`; `test_dc_auto.py::TestC1Admission`; `test_dc_e2e.py::TestPoint2Auto` (admission + `pre_check_layer` ABSENT) |
| **A8** | `--auto` axis-proposal: registry-default fill, NO AI; unknown type → register hint | `_factcheck_engine.py` `_dc_auto_fill_axes` (≈1499); `test_dc_auto.py` (`_dc_auto_fill_axes` cases) |
| **A9** | Fail-closed when no concrete target (no defensible type / <2 axes incl. groundedness / no claims / no sources) → refuse before any checker spawns | `_factcheck_engine.py` `validate_self_assessment` (≈1429); `test_dc_precheck.py`; `test_dc_e2e.py::TestPoint1Floor::test_floor_refuses_untargetable_before_dispatch` |
| **A10** | Layer-2 rules file `double-check-allocation.md` + load+commit drift detector (code authoritative; rules file asserted mirror) | `rules/double-check-allocation.md`; `_factcheck_engine.py` `check_allocation_drift`; `hooks/check-double-check-allocation.sh`; `test_dc_allocation.py::TestC3*`; `test_dc_e2e.py::TestPoint5Drift` |
| **A11** | Co-located storage + single `resolve_audit_location()` + mirror-tree fallback + `.dc-runs.md` sidecar | `_factcheck_engine.py` `resolve_audit_location` (≈1711), `_append_dc_runs_sidecar` (≈1775); `test_dc_audit_marker.py`; `test_dc_e2e.py::TestPoints3and4Compose` (`co_located` + `.dc-runs.md` sidecar exists) |
| **A12** | T9 one-step reachability: forward Obsidian wikilink (writable source) or read-only audit-index | `_factcheck_engine.py` `_insert_audit_wikilink` (≈1795); `test_dc_e2e.py::TestPoints3and4Compose` (`wikilink_written` + `## /double-check audit` inserted into the artifact) |
| **A13** | Caller-audit enumeration-exhaustion across skills + rules + hooks; two-clean-scans; git-tracked machine-readable registry | `hooks/dc_caller_audit.py`; `hooks/dc-caller-audit.yaml` (committed registry, 142 entries); `test_dc_caller_audit.py`; `test_dc_e2e.py::TestPoint6CallerAuditClosure` |
| **A14** | Self-assessment gate (comprehensiveness floor) enforced before the user gate | `_factcheck_engine.py` `validate_self_assessment` + `DC_SELF_ASSESSMENT_MIN_AXES`; `test_dc_precheck.py`; `test_dc_e2e.py::TestPoint1Floor` |
| **A15** | Code-only `Stats.md` cost aggregator grouped by `(caller, topic)`; `/close` + on-demand CLI; raw; per-project | `hooks/dc_stats.py`; `test_dc_stats.py`; `test_dc_e2e.py::TestPoints3and4Compose` (Point 4 — the real marker aggregated into the `(caller,topic)` Stats.md block) |

## UX decisions (UX1–UX4)

| Decision | What it is | Observable evidence |
|----------|------------|---------------------|
| **UX1** | Under-specified-target gate: `(a) Accept` / `(b) Provide a different context`; no override; converge via (b) | `skills/double-check/SKILL.md` `### Confirming the validation target — (a)/(b) (UX1)` (≈241) + step "User gate (UX1 — (a)/(b), no override)" (≈129); floor enforced by `validate_self_assessment` (`test_dc_precheck.py`) |
| **UX2** | ESCALATE 3-option (apply-fix-and-re-run / proceed-as-is / one-more-full-round); `escalate_choice` recorded in the marker | `skills/double-check/SKILL.md` `### When checkers can't converge — the 3 ESCALATE options (UX2)` (≈250); `_factcheck_engine.py` `AuditMarker.escalate_choice`; `test_dc_dispatch.py` C4 (ESCALATE handler records `escalate_choice`) |
| **UX3** | Post-run auditability: forward wikilink (or read-only audit-index) → per-run marker; skill prints verdict + pointer, never an inline dump | `skills/double-check/SKILL.md` `### After a run — verdict + pointer, never a dump (UX3)` (≈273) + step "Post-run pointer, not dump (UX3)" (≈223); reachability proved by `test_dc_e2e.py::TestPoints3and4Compose` (forward wikilink in the artifact) |
| **UX4** | Source-conflict assist modes: (1) `/challenge` 1:1 compare; (2) `/recommend`; user decides; in `--auto`, conflict recorded + proceed | `skills/double-check/SKILL.md` `### When named sources disagree — source-conflict assist modes (UX4)` (≈264) + step "Source-conflict assist (A4 / UX4)" (≈125) |

## Acceptance status

All 19 decisions (A1–A15 + UX1–UX4) carry observable evidence that resolves on disk.
The end-to-end composition (`test_dc_e2e.py`, 6 acceptance points) + the whole
`/double-check` test corpus (`test_dc_*.py` + `test_factcheck_engine.py`) + the
Layer-2 allocation drift guard run green together — see the S9 plan Verification
section. With all 9 slices shipped and verified, the redesign is acceptance-complete.

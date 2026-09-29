# Assessment Dimension Registry (Layer-2 mirror)

This file is the **human-readable mirror** of the code-authoritative
`ASSESSMENT_DIMENSION_REGISTRY` in `${KIT_HOOKS_DIR}/assessment_engine.py`. It
carries the per-kind dimension catalog (dimension slug + reference-required/free
tag + per-kind V1/V2 rigor tier) for the assessment engine's framing step.

**Code is authoritative; this file is the asserted mirror.** A drift-comparison
function (`check_registry_drift` in `assessment_engine.py`) normalizes both this
file's table and the code registry to `(kind → {rigor, dims})` maps and
**hard-fails on any divergence, naming the divergent kind/dimension** — at the
`check-registry-drift` CLI AND at commit time (via
`${KIT_HOOKS_DIR}/check-assessment-registry.sh`). The two copies cannot diverge
silently: edit one without the other and the commit is blocked.

Sibling of `~/.claude/rules/double-check-allocation.md` (the `/double-check`
allocation mirror this pattern is cloned from).

Slice S2 of `Thoughts/assessment-engine-20260727202257_PLAN.md` (design
`assessment-engine-20260709000856_DESIGN.md` → Per-Kind Dimension Registry).

---

## Three tiers

1. **Closed-v1 named catalog (10 kinds)** — the rows below with a numbered kind.
2. **Gated generic fallback** — the `generic` row; reachable ONLY when the engine
   cannot identify a known kind AND the operator explicitly accepts it
   (self-flagged "assessed with generic aspects", never automatic — facade, S7).
3. **Extensible** — a new kind/dimension is a deliberate code+mirror edit,
   drift-guarded by this file.

## Floor invariant

Every kind (including `generic`) carries **`groundedness`** and **at least two
axes**. Reference-required/free is a per-KIND property: a dimension can be `req`
in one kind and `free` in another (e.g. `groundedness` is `free` for `code` and
`operator_input` but `req` elsewhere). The `(req)`/`(free)` tag below is what a
missing required reference pre-flags as `CANNOT_ASSESS` risk.

---

## Per-kind dimension catalog

The `Kind` column is the registry KEY (the drift-comparable identifier), not a
display label. This table is what `_parse_registry_mirror` reads — it must match
`ASSESSMENT_DIMENSION_REGISTRY` exactly.

| Kind | Dimensions | Rigor |
|---|---|---|
| code | correctness (req), security (free), type-format-compliance (free), groundedness (free) | deep |
| recommendation | groundedness (req), relevance (req), completeness (req), source-quality (req) | default |
| operator_input | coherence (free), relevance (req), groundedness (free), completeness (free) | default |
| plan | task-adherence (req), intent-resolution (req), coherence (free), completeness (req), groundedness (req) | deep |
| thought | coherence (free), groundedness (req), completeness (free), relevance (req) | default |
| design | task-adherence (req), coherence (free), completeness (req), groundedness (req) | deep |
| research | groundedness (req), source-quality (req), coverage (req), relevance (req) | deep |
| cover_letter | relevance (req), groundedness (req), coherence-fluency (free), completeness (req) | default |
| skill | task-adherence (req), completeness (free), coherence (free), groundedness (req) | default |
| session_behaviour | task-adherence (req), tool-call-accuracy (req), intent-resolution (req), groundedness (req) | deep |
| generic | coherence (free), relevance (req), groundedness (free), completeness (free) | default |

**Rigor tiers:** `deep` = full `/double-check 3,1,2` V1/V2 (for `code` / `plan` /
`design` / `research` / `session_behaviour`); `default` = lean/skippable V1/V2
(the other five + `generic`), operator-overridable via `--rigor` (S5).

**Grounding:** dimension slugs map to the four-labs artifact-type→dimension
patterns (RESEARCH Q4) or the `DC_AXIS_REGISTRY` precedent (`coverage`,
`source-quality`). No kind was trimmed. Per-angle thresholds + the
adaptive-vs-static rubric choice are implementation-time calibration.

**Slug normalization (traceability):** two slugs are hyphen-normalized from the
Design's slash spelling so they can key a dict and pass the drift regex —
`type-format-compliance` == Design's `type/format-compliance`, and
`coherence-fluency` == Design's `coherence/fluency`. Semantics unchanged.

---

## Consumed by

- `${KIT_HOOKS_DIR}/assessment_engine.py` — `ASSESSMENT_DIMENSION_REGISTRY`
  (authoritative source), `check_registry_drift` (comparison), `registry_projection`,
  `_assert_registry_consistent` (lazy guard), the `check-registry-drift` CLI.
- `${KIT_HOOKS_DIR}/check-assessment-registry.sh` — commit-time drift guard
  (invokes the CLI; exit ≠ 0 + stderr names the divergent kind/dimension).
- The framing step (S3) — proposes a kind's dimension-set from this registry.

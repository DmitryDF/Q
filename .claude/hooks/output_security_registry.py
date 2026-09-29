"""Output-security read side — the consumer registry, the boundary scan, the read metric.

Sibling of ``output_security.py``. That module **contains** a produced claim; this one
**declares** every place the system reads produced claims, **detects** a new or regressed
reader, and **reports** how much of the read surface is actually covered.

**Why this is a separate module.** The two responsibilities are genuinely distinct — a
container versus a map of who reads through it — and a shipped S1 assertion
(``test_a2_exactly_three_seams_declared``) closes ``output_security`` at exactly three
``Port``-suffixed seams, so the read-trail port could not live there without falsifying it.
The split is defensible on its own merits; the test merely forced the issue.

**Honesty (locked constraint — inherited verbatim from the sibling).** Containment is a
strong reduction of injection risk, NOT an absolute guarantee. Exactly ONE of the twenty-seven
registered seams is covered, and it is covered on the deep path only. The twenty-six others
are recorded here with a stated cause precisely so that partial coverage can never read as
whole coverage. (Slice S5 added the twenty-fourth and moved these two number words with it;
slice S6 added the twenty-fifth and twenty-sixth and moved them again; slice S7 added the
twenty-seventh — the boundary's own steerability instrument — and moved them once more. The guard on them
derives its expectation from the LIVE row count and sweeps for stale
number words, so it is already generic and is deliberately left alone: if it went green over
unedited prose it would have been hollowed rather than satisfied.) Every operator-facing string this module emits comes from
``output_security.OPERATOR_COPY``; it composes no reassurance of its own, and its own reason
prose is checked at import time by ``find_over_claims``.

**What this module does NOT do (slice S2).** No judge, no disposition rule, no resolution
vocabulary, no calibration trail, no probe corpus, no rules mirror and no drift guard. Each
belongs to a named later slice or is explicitly declined. In particular there is no
Layer-2 mirror of this registry: the sibling registries have mirrors in order to be
drift-guarded, and S1 asserted in code that ITS mirror is behaviourally inert — so a mirror
here would be either inert or contradictory. The registry's authority lives in Python
beside the code it governs.

Standalone / unit-testable::

    python3 output_security_registry.py scan     # the boundary scan (exit 1 when unclean)
    python3 output_security_registry.py rows     # the registry as JSON
    python3 output_security_registry.py omtm     # the read-coverage read-out

Slice S2 of ``Thoughts/research-output-security-20260804213834_S2_PLAN.md``
(design ``…_DESIGN.md`` ``### Solution Alternative 1``, decisions A18 and A22).
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional, Protocol, Sequence, Tuple, runtime_checkable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The honesty tripwire has ONE definition site, in the containment sibling. This module
# reaches it by import and runs its OWN reason prose through it at import time — which is
# also why this module appears on the S1 consumer-assertion's infrastructure allowlist.
from output_security import OPERATOR_COPY, find_over_claims  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Domain layer — deterministic, no AI, no I/O.
# The closed mediation vocabulary, the frozen row type, the registry itself, its
# import-time invariants, the pinned signal set, and the pure metric.
# ─────────────────────────────────────────────────────────────────────────────

#: The CLOSED vocabulary for *why* a read is or is not coverable. ``mediation`` is the
#: closed field; ``reason`` beside it is free text whose only invariant is non-emptiness.
#: The two are never conflated — the kind says what sort of read it is, the reason says why
#: this particular one cannot be covered.
#:
#: Seven kinds are declared and all seven carry rows. ``boundary_self`` was declared empty at
#: S2 and reserved for the judge's own input path; slice S3 filled it with two rows.
MEDIATION_KINDS: Mapping[str, str] = {
    "code_egress": (
        "Code composes a model-facing string containing produced claim text. This is the "
        "only kind that CAN be covered by the read-side boundary."
    ),
    "code_transport": (
        "Code moves claim text from one place to another without putting it in front of a "
        "model; the containment decision belongs to whoever composes the model-facing text."
    ),
    "not_claim_text": (
        "The site passes something other than produced claim text through a "
        "claim-shaped parameter."
    ),
    "producer_input": (
        "The site reads a source document in order to PRODUCE claims from it, rather than "
        "to consume claims already produced."
    ),
    "prose": (
        "A skill instructs the session or a subagent to read the artifact; no code sits "
        "between the file and the model, so there is nothing for code to wrap."
    ),
    "unwired": (
        "A named consumer with no read path in the tree at all — nothing to instrument "
        "until one exists."
    ),
    "boundary_self": (
        "The boundary's own machinery reads claim bodies (the judge's input path). "
        "Declared for slice S3; carries no rows at S2."
    ),
}

#: The only mediation kind a covered row may carry — a read the boundary can actually wrap.
COVERABLE_KIND = "code_egress"


@dataclass(frozen=True)
class ConsumerSeam:
    """One place the system reads produced claims. Frozen: the registry is a declaration.

    ``path``/``line`` are an EXACT anchor, never a glob — a pattern would silently absorb a
    new reader, which is the failure this registry exists to prevent. ``hint`` is the text
    expected at that line, so drift is detected by content rather than assumed from a number.

    ``spotlit`` records whether this read goes through the boundary. ``mediation`` records
    what KIND of read it is, from the closed vocabulary. ``reason`` is free text and is
    REQUIRED whenever ``spotlit`` is false — an uncovered seam without a stated cause is
    exactly the silent gap this slice exists to remove.

    ``scan_visible`` is false for a seam that carries no signal any file scan can recognise.
    Such a row is excluded from the scan's expectations; without that, every run would report
    a permanently-missing row and the signal would be trained away.

    ``coverage_evidence`` is the substring whose presence in the anchored file proves the
    covered call is still there. Only meaningful on a ``spotlit`` row.
    """

    id: str
    path: Optional[str]
    line: Optional[int]
    hint: Optional[str]
    consumer: str
    mediation: str
    spotlit: bool
    reason: str
    scan_visible: bool = True
    coverage_evidence: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# THE REGISTRY — 27 rows, one per EGRESS (not one per file).
#
# 18 at slice S2; slice S3 added the two `boundary_self` rows — the judge's own prompt-build
# read and the write-seam extraction read. Slice S4 added three more: the findings record's
# store, the decorrelated meta-check's read, and `_claim_harvest_trigger`'s marked-claim
# extraction — the last of which S4 did not create but merely made VISIBLE, by giving that
# module a pinned signal it had never carried.
#
# Per-egress is load-bearing: `_dc_claim_seam.py` carries two egresses of the same kind
# with different coverage, and `double-check/SKILL.md` carries four. A file-granular
# registry could not express either, and a file-granular question ("is this file covered?")
# is ill-posed — which is why the report attributes coverage per SEAM, never per file and
# never per consumer.
#
# Rows 15 and 16 were found by independent checkers, NOT by the scan: both sit inside a
# file already registered by rows 11 and 12, where a file-granular scan is blind to them BY
# CONSTRUCTION. They are the concrete demonstration that this registry's completeness is a
# curated judgment the scan cannot supply.
# ─────────────────────────────────────────────────────────────────────────────

CONSUMER_REGISTRY: Tuple[ConsumerSeam, ...] = (
    ConsumerSeam(
        id="dc_seam.flatten_backward",
        path="hooks/_dc_claim_seam.py", line=94, hint="def flatten_backward(claim_set)",
        consumer="/double-check", mediation="code_egress", spotlit=True,
        coverage_evidence="spotlight(",
        reason=(
            "The one covered egress at S2. Code composes the numbered claim list here, so "
            "the boundary can wrap it. Its containment is CONDITIONAL on the deep path: the "
            "retained regex fast-path and every --auto run compose their own list without "
            "reaching this function (row 16), so a run that never enters the deep path is "
            "not covered by this row."
        ),
    ),
    ConsumerSeam(
        id="dc_seam.backward_claims",
        path="hooks/_dc_claim_seam.py", line=269,
        hint="backward_claims = [c.text for c in cs.claims",
        consumer="/double-check", mediation="code_egress", spotlit=False,
        reason=(
            "A second egress in the same function, deliberately left uncontained rather "
            "than wrapped opportunistically: it seeds the interactive pre-check target, "
            "whose consumer re-reads the artifact itself (row 12). Wrapping it would "
            "contain one copy while the dominant read of the same claims stays uncontained, "
            "buying a higher coverage number and no additional protection."
        ),
    ),
    ConsumerSeam(
        id="claim_engine.stage1_prompt",
        path="hooks/_claim_engine.py", line=209, hint="def _stage1_prompt(source: Source",
        consumer="claim-identification engine", mediation="producer_input", spotlit=False,
        reason=(
            "Embeds the source document verbatim into a live model prompt in order to "
            "PRODUCE claims from it. When that source is a _RESEARCH.md this is a code-owned "
            "read of produced claims — one of the reads that falsified the design's "
            "one-locus assumption. Covering it needs a caller-declared provenance flag on "
            "Source so the engine can tell a produced-claims document from any other input; "
            "S2 declines to invent that flag rather than guess at the call site."
        ),
    ),
    ConsumerSeam(
        id="claim_engine.stage2_prompt",
        path="hooks/_claim_engine.py", line=225, hint="def _stage2_prompt(source: Source",
        consumer="claim-identification engine", mediation="producer_input", spotlit=False,
        reason=(
            "The Stage 2 counterpart of the row above, on the same live production path and "
            "blocked on the same missing Source provenance flag."
        ),
    ),
    ConsumerSeam(
        id="claim_dispatch.cli_source_read",
        path="hooks/_claim_dispatch.py", line=209, hint="text = Path(src_path).read_text",
        consumer="claim-identification engine", mediation="producer_input", spotlit=False,
        reason=(
            "Reads the source file from disk and hands it to the engine as production input. "
            "Real and live — this is the default subagent stage-production path, not dead "
            "code — and blocked on the same missing Source provenance flag."
        ),
    ),
    ConsumerSeam(
        id="claim_dispatch.persist_payload",
        path="hooks/_claim_dispatch.py", line=176,
        hint='row = {"text": c.text, "role": c.role.value',
        consumer="claim-identification engine", mediation="code_transport", spotlit=False,
        reason=(
            "Moves claim text into a persistence payload. Nothing here is put in front of a "
            "model, so containment at this point would protect no reader; the boundary "
            "belongs wherever that stored text is later presented."
        ),
    ),
    ConsumerSeam(
        id="factcheck_engine.checker_prompt",
        # Line moved 1842 -> 2706 -> 2725 across the per-file fact-check gate
        # work (research-fc-backlog Group I item 2). The anchored CODE has not
        # changed once; only its position, because that plan inserted above it.
        # This row has now been re-pointed three times for edits that had nothing
        # to do with it — a line number into another topic's file is a fragile
        # anchor, and any future insertion into _factcheck_engine.py breaks it
        # again. Worth replacing with a search for `hint` rather than a fixed
        # line (the gate already has the hint and uses the line only as a window).
        path="hooks/_factcheck_engine.py", line=2725,
        hint='prompt += f"Additional scope for this check',
        consumer="/double-check", mediation="code_transport", spotlit=False,
        reason=(
            "Injects whatever scope_addendum its caller composed. The containment decision "
            "belongs to the composing caller — row 1 does contain it — and wrapping again "
            "here would double-wrap text whose provenance this transport cannot see. Its "
            "shipped callers also pass non-claim text (rows 8 and 10)."
        ),
    ),
    ConsumerSeam(
        id="assessment_engine.criterion_addendum",
        path="hooks/assessment_engine.py", line=2478,
        hint='scope_addendum=payload.get("criterion")',
        consumer="/assess", mediation="not_claim_text", spotlit=False,
        reason=(
            "Passes a code-owned criterion string through the claim-shaped parameter. It "
            "carries no produced claim text, so there is nothing here for this boundary to "
            "contain."
        ),
    ),
    ConsumerSeam(
        id="pre_plan_gates.claims_addendum",
        path="hooks/pre_plan_gates.py", line=7411, hint="scope_addendum=_claims_addendum,",
        consumer="plan validation", mediation="code_transport", spotlit=False,
        reason=(
            "Transports a plan's own claims addendum into a checker dispatch. Covering it "
            "belongs with whatever composes that addendum, not with this call site."
        ),
    ),
    ConsumerSeam(
        id="pre_plan_gates.against_text",
        # Line moved 7556 -> 7563 when the per-file fact-check gate work
        # (research-fc-backlog Group I item 2) added the accept-research-incomplete
        # `--file` argument above it. The anchored CODE is unchanged.
        path="hooks/pre_plan_gates.py", line=7563,
        hint='scope_addendum = f"Validate the recommendation against',
        consumer="plan validation", mediation="not_claim_text", spotlit=False,
        reason=(
            "Composes an against-string from an operator-supplied argument. Same file and "
            "same parameter as the row above but a DIFFERENT kind of content, which is why "
            "the registry is per-egress: one row per file could not describe both."
        ),
    ),
    ConsumerSeam(
        id="double_check.seam_b_target_claims",
        path="skills/double-check/SKILL.md", line=141,
        hint="Seam B (refc S9 / dc-2c-claim-engine-seam)",
        consumer="/double-check", mediation="code_egress", spotlit=False,
        reason=(
            "Skill prose directs the session to seed the pre-check target from the raw "
            "uncontained list at row 2. The list is composed in code but consumed by an "
            "instruction, so covering it means covering row 2 — deliberately not done, for "
            "the reason recorded there."
        ),
    ),
    ConsumerSeam(
        id="double_check.subagent_read",
        path="skills/double-check/SKILL.md", line=169,
        hint="1. Read the recommendation at:",
        consumer="/double-check", mediation="prose", spotlit=False,
        reason=(
            "The DOMINANT read of this consumer: the checker subagent opens the artifact "
            "itself. No code stands between the file and the model, so no code-applied "
            "boundary can reach it. This is why /double-check is NOT reported as a covered "
            "consumer even though one of its egresses is covered."
        ),
    ),
    ConsumerSeam(
        id="plan_skill.in_session_identification",
        path="skills/plan/SKILL.md", line=207,
        hint="**Engine-consumer — define-once-propagate",
        consumer="planning", mediation="prose", spotlit=False,
        reason=(
            "Plan validation identifies claims in-session under skill instruction. There is "
            "no code-owned read point to contain — one of the two consumers the design "
            "assumed had one."
        ),
    ),
    ConsumerSeam(
        id="solution_slicer.assessor_prompt",
        path="skills/solution-slicer/SKILL.md", line=278,
        hint="[scope_addendum built in Step 3, item 2",
        consumer="/solution-slicer", mediation="prose", spotlit=False,
        reason=(
            "A consumer the design never named: skill prose pastes a structural-claim "
            "addendum into an assessor prompt. The paste happens in the session, not in code."
        ),
    ),
    ConsumerSeam(
        id="double_check.step_2c1_session_reasoning",
        path="skills/double-check/SKILL.md", line=93,
        hint="Reason **Stage 1 (identify + ambiguity gate)**",
        consumer="/double-check", mediation="prose", spotlit=False,
        reason=(
            "The session reasons Stage 1 and Stage 2 directly over the artifact under skill "
            "instruction, with no code-owned read point. Found by independent checkers, not "
            "by the scan: it sits inside a file the scan already sees via rows 11 and 12, "
            "where file-granular detection is blind by construction."
        ),
    ),
    ConsumerSeam(
        id="double_check.step_2c2_regex_fastpath",
        path="skills/double-check/SKILL.md", line=101,
        hint="**Step 2c.2 — Regex fast-path",
        consumer="/double-check", mediation="prose", spotlit=False,
        reason=(
            "The retained regex fallback and the AI-free --auto path compose the numbered "
            "claim list in-session without ever reaching flatten_backward. This row is WHY "
            "row 1's coverage is conditional: on these paths the checker prompt carries an "
            "uncontained list. Also found by independent checkers rather than by the scan."
        ),
    ),
    ConsumerSeam(
        id="clarification.prose_handoff",
        path="skills/clarification/steps.md", line=348,
        hint="After research, I will show you the revised",
        consumer="planning", mediation="prose", spotlit=False, scan_visible=False,
        reason=(
            "Skill prose puts a research file in front of the operator and the model with no "
            "code in between. It carries NO signal any scan recognises, so it is invisible to "
            "detection by construction and is excluded from the scan's expectations — it is "
            "here because a human put it here, which is the point of a curated registry."
        ),
    ),
    ConsumerSeam(
        id="knowledge_base.unwired",
        path=None, line=None, hint=None,
        consumer="knowledge base", mediation="unwired", spotlit=False, scan_visible=False,
        reason=(
            "The knowledge base is named as a consumer by the design but has no read path in "
            "the tree at all. There is nothing to instrument until one exists; recording it "
            "as a row keeps a known consumer from reading as covered by omission."
        ),
    ),
    # ── slice S3 — the boundary's OWN reads of produced claim bodies ──────────
    #
    # `boundary_self` was declared at S2 with zero rows and reserved for exactly these. They
    # are the judge's own input path: the first code in the tree that reads a produced claim
    # in order to judge it.
    #
    # Both carry `spotlit=False`, and the reason is structural rather than an admission of a
    # gap: the registry invariant refuses a covered row whose mediation is not `code_egress`,
    # and these are not egresses to a downstream consumer — they are the boundary reading
    # claim text for its own purposes. The judge's prompt DOES place every unit inside the
    # code-owned container; what these rows decline to claim is COVERAGE, which is a
    # statement about the read surface this metric measures.
    ConsumerSeam(
        id="judge.prompt_build_read",
        # Re-pointed by slice S4: 214 → 228. The anchored code did not change; the module
        # gained an import and a docstring correction above it.
        path="hooks/output_security_judge.py", line=228,
        hint="def build_judge_prompt(units",
        consumer="output-security judge", mediation="boundary_self", spotlit=False,
        reason=(
            "The judge's prompt builder reads every attribution unit's text in order to place "
            "it in front of a model for judgment. Each unit IS wrapped in the code-owned "
            "container here, but this row is not marked covered: the coverage figure measures "
            "reads by downstream consumers, and counting the boundary's own input as covered "
            "read surface would inflate that figure with the boundary reading itself."
        ),
    ),
    ConsumerSeam(
        id="judge.write_seam_extraction_read",
        # Re-pointed by slice S4: 597 → 611, for the same reason as the row above. Re-pointed
        # again by S6: 611 → 619, moved by S6's own edit to `judge_produced_claim` above it.
        # The anchored code did not change.
        path="hooks/output_security_judge.py", line=619,
        hint="def prospective_text(tool_name",
        consumer="output-security judge", mediation="boundary_self", spotlit=False,
        reason=(
            "The write seam reconstructs the prospective file content — for an Edit, by "
            "reading the target from disk and applying the replacement in memory — so that "
            "attribution is computed against the RESULTING text rather than a fragment. It "
            "reads claim text without putting it in front of any model, so there is nothing "
            "for the read-side boundary to wrap at this point."
        ),
    ),
    # ── Slice S4 — two more of the boundary's own reads. ──────────────────────
    #
    # Both are `boundary_self` and both are `spotlit=False`, for exactly the reason the pair
    # above states: the coverage figure measures reads by DOWNSTREAM consumers, and counting
    # the boundary reading itself would inflate it. That matters here more than it did at S3,
    # because the read-coverage metric is a standing open residual on this topic — S4 does not
    # own it, and no row this slice adds may move it. Registering these as covered would have
    # bought the slice a better figure for work that did not improve any consumer's read.
    ConsumerSeam(
        id="record.findings_store",
        # Re-pointed by slice S6: 451 → 479, moved by S6's own edits above it (the provider
        # trail path and the `source_url` field on the flag row). The anchored code did not
        # change. Only S6's OWN two drifted anchors are re-pointed here — the three belonging
        # to `_factcheck_engine` and `pre_plan_gates` are other topics' and are deliberately
        # left alone.
        path="hooks/output_security_record.py", line=479,
        hint="def record_produced_findings(file_path",
        consumer="output-security record", mediation="boundary_self", spotlit=False,
        reason=(
            "Persists a flagged span so the boundary can remember it after the hook that "
            "raised it has exited. Nothing here is put in front of a model, so containment at "
            "this point would protect no reader; the span reaches a model only through the "
            "meta-check row below, which fences it."
        ),
    ),
    # A reader this slice DISCOVERED rather than added, and the discovery is the scan working
    # rather than a cost of the change. The harvest trigger has read produced claim text since
    # it shipped — it extracts every marked claim from a research file — but it carried none of
    # the pinned signals, so it was invisible here. S4's quarantine gave it one, the scan
    # immediately reported it as an unregistered reader, and it is registered rather than
    # exempted. Its invisibility is exactly what SIGNAL_LIMITATION warns about, now
    # demonstrated a second time on a real file.
    ConsumerSeam(
        id="claim_harvest.marked_claim_extraction",
        # Re-pointed by slice S5: 239 → 280, then 280 → 285 — S5's own docstring
        # growth in `_topic_paths` re-staled it a second time within the same
        # slice, without re-deriving the line before the re-point landed. The
        # anchored code did not change.
        path="hooks/_claim_harvest_trigger.py", line=285,
        hint="marked = extract_marked_claims(text)",
        consumer="claim harvest", mediation="code_transport", spotlit=False,
        reason=(
            "Extracts every marked claim from a verified research file and moves it into the "
            "topic's evidence register. Nothing here is put in front of a model, so "
            "containment at this point would protect no reader; the boundary belongs wherever "
            "that stored text is later presented. This read is now HELD by the S4 quarantine "
            "when the file carries an outstanding flag."
        ),
    ),
    ConsumerSeam(
        id="metacheck.second_reader",
        path="hooks/output_security_metacheck.py", line=147,
        hint="def build_metacheck_prompt(finding",
        consumer="output-security meta-check", mediation="boundary_self", spotlit=False,
        reason=(
            "The decorrelated second reader is handed a stored flag's span in order to grade "
            "whether the flag was grounded. The span IS placed inside the shared containment "
            "fence here — that is half of A9's decorrelation, not merely hygiene — but the row "
            "is not marked covered for the same reason as the judge's own rows: this is the "
            "boundary reading itself, not a downstream consumer's read."
        ),
    ),
    ConsumerSeam(
        id="resolution.operator_surface",
        path="skills/output-security-resolve/run.py", line=91,
        hint="def resolve_produced_flag(finding",
        consumer="output-security resolution surface", mediation="boundary_self",
        spotlit=False,
        reason=(
            "The operator is handed a stored flag's span so they can decide the flag. The "
            "span IS placed inside the shared containment fence here, and one thing is "
            "genuinely different at this row and nowhere else: the reader is the MAIN "
            "SESSION with full tool access, not a bounded subprocess, holding text that was "
            "flagged precisely because it reads as an instruction to this system. That is "
            "why the fencing matters more here than at any other row, even though the "
            "classification matches. It is not marked covered for the same reason as the "
            "meta-check's row beside it: this is the boundary reading its own finding so its "
            "own operator can decide it, not a downstream consumer reading a claim as "
            "research content."
        ),
    ),
    # ── Slice S6 — two more of the boundary's own reads. ──────────────────────
    #
    # Registered rather than exempted, which is the S4/S5 precedent: the scan caught a new
    # reader in both of those slices and both were registered. Both are `boundary_self` and
    # both are `spotlit=False`, and the classification is on WHO IS READING rather than on
    # whether the text is fenced. Read coverage does NOT move for these, and this slice says so
    # rather than implying otherwise — every row S6 adds is uncovered by construction, because
    # there is still no write→read join.
    ConsumerSeam(
        id="record.marker_origin_derivation",
        # Re-pointed by slice S7: 945 → 1009, moved by S7's own seven added `OPERATOR_COPY`
        # keys above it. The anchored code did not change. Only S7's OWN drifted anchor is
        # re-pointed — the three belonging to `_factcheck_engine` and `pre_plan_gates` are
        # other topics' and are deliberately left alone, exactly as S6 left them.
        #
        # Re-pointed AGAIN by slice S-final: 1009 → 1045, moved by its own five added
        # `OPERATOR_COPY` keys, for the same reason and with the same restraint. This is the
        # third consecutive slice to push this one anchor by adding copy above it, which is
        # worth reading as a property of the layout rather than as three coincidences: every
        # slice that ships operator-facing wording must put it in `OPERATOR_COPY` to stay
        # inside `find_over_claims`, and this anchor sits below that mapping.
        path="hooks/output_security.py", line=1045,
        hint="marker_urls.get(ordinal",
        consumer="output-security record", mediation="boundary_self", spotlit=False,
        reason=(
            "The attribution partition resolves each claimed region to the URL of the marker "
            "that governs it, so a confirmed violation can later name the source it came from. "
            "It reads claim text — the same text it was already partitioning — and puts none "
            "of it in front of a model, so there is nothing for the read-side boundary to wrap "
            "at this point. It is the boundary deriving a fact about its own input, not a "
            "downstream consumer reading a claim."
        ),
    ),
    ConsumerSeam(
        id="record.corpus_source_enumeration",
        path="hooks/output_security_record.py", line=1070,
        hint="def cited_sources(root",
        consumer="output-security record", mediation="boundary_self", spotlit=False,
        reason=(
            "The secondary metric's denominator walks every produced-claim file in the corpus "
            "and folds each provenance marker's URL to its comparison identity. It reads claim "
            "text only to extract citations from it; nothing is put in front of a model and "
            "nothing is stored but the folded host and path. On demand only — this read never "
            "runs on the write path."
        ),
    ),
    # ── Slice S7 — the boundary's own instrument. ────────────────────────────
    #
    # Registered rather than exempted, on the S4/S5/S6 precedent, and `boundary_self` on the
    # same rule those three used: classify on WHO IS READING, not on whether the text is
    # fenced. Read coverage does NOT move for this row, and this slice says so rather than
    # implying otherwise — there is still no write→read join, so every row added since S3 is
    # uncovered by construction.
    #
    # The `reason` states the technicality plainly because Design Review §2 required it to:
    # this row's claim text is SYNTHETIC. It is registered because it carries scan signals and
    # because the boundary's own instrument should be visible to the scan that polices every
    # other reader — not because it reads produced claims.
    ConsumerSeam(
        id="probe.steerability_read",
        path="hooks/output_security_probe.py", line=1,
        hint="the out-of-band steerability probe",
        consumer="output-security probe", mediation="boundary_self", spotlit=False,
        reason=(
            "The steerability probe composes both arms of a claim list and puts each in front "
            "of a pinned reader in order to measure whether the spotlighting instruction "
            "changes that reader's compliance. Its claim text is SYNTHETIC — a fixed corpus of "
            "harmless instruction-shaped probes, never a produced claim from the corpus — so "
            "this row is registered for visibility rather than because it mediates produced "
            "content. It is not marked covered for the same reason as every boundary_self row "
            "beside it: the coverage figure measures reads by downstream consumers, and this "
            "is the boundary measuring itself. The probe runs out-of-band at calibration time "
            "and is reachable from no enforcement path."
        ),
    ),
)


class RegistryInvariantError(ValueError):
    """Raised at import time when the registry violates one of its own invariants."""


def _check_registry(rows: Sequence[ConsumerSeam] = None) -> None:
    """Enforce the registry's invariants. Runs at import — a bad row cannot ship.

    Clones the sibling engine's registry-invariant shape, and the empty-reason raise from
    ``_claim_metrics.fallback_record`` ("a fallback is never a silent skip"). The
    empty-reason rule is the load-bearing one: it is what makes an uncovered seam
    self-documenting instead of merely absent.
    """
    rows = CONSUMER_REGISTRY if rows is None else rows
    seen = set()
    for row in rows:
        if row.id in seen:
            raise RegistryInvariantError(f"duplicate seam id: {row.id!r}")
        seen.add(row.id)
        if row.mediation not in MEDIATION_KINDS:
            raise RegistryInvariantError(
                f"{row.id!r}: unknown mediation {row.mediation!r}; "
                f"the vocabulary is closed: {sorted(MEDIATION_KINDS)}"
            )
        if row.spotlit and row.mediation != COVERABLE_KIND:
            raise RegistryInvariantError(
                f"{row.id!r}: a covered row must be {COVERABLE_KIND!r}, not "
                f"{row.mediation!r} — only a code-composed egress can be wrapped"
            )
        if not row.spotlit and not row.reason.strip():
            raise RegistryInvariantError(
                f"{row.id!r}: an uncovered seam requires a non-empty reason — "
                f"an uncovered read is never a silent gap"
            )
        over = find_over_claims(row.reason)
        if over:
            raise RegistryInvariantError(
                f"{row.id!r}: reason makes a forbidden assertion: {over}"
            )


_check_registry()


def registry_by_id() -> Mapping[str, ConsumerSeam]:
    """The registry keyed by seam id."""
    return {row.id: row for row in CONSUMER_REGISTRY}


def uncovered_seams(rows: Sequence[ConsumerSeam] = None) -> Tuple[Mapping[str, str], ...]:
    """Every uncovered seam with its kind and its stated cause — the honest half."""
    rows = CONSUMER_REGISTRY if rows is None else rows
    return tuple(
        {"id": r.id, "consumer": r.consumer, "mediation": r.mediation, "reason": r.reason}
        for r in rows if not r.spotlit
    )


# ─────────────────────────────────────────────────────────────────────────────
# The boundary scan — production code, callable without pytest.
# ─────────────────────────────────────────────────────────────────────────────

#: The seven code signals the scan recognises, PINNED explicitly rather than inherited.
#:
#: **This set is NOT exhaustive and cannot be.** A read path that uses none of these tokens
#: is invisible to this scan. That bound is stated rather than mitigated, because an honest
#: bound IS the mitigation — a scan trusted beyond its reach is worse than one whose reach
#: is known.
#:
#: The concrete demonstration: an earlier four-identifier set was blind to
#: `hooks/_claim_dispatch.py` and `hooks/_claim_engine.py` — precisely the files whose
#: code-owned reads falsified the design's assumption that each consumer had exactly one
#: read point. `source.text` and `StageProduction` were added for that reason.
#:
#: The rejected alternative — scanning for the literal `_RESEARCH.md` — matches roughly five
#: times this set's production surface, almost all documentation prose rather than read
#: machinery, which is why it is unusable as a predicate.
SIGNAL_SET: Tuple[str, ...] = (
    "build_scope_addendum",
    "flatten_backward",
    "scope_addendum",
    "backward_claims",
    "source.text",
    "StageProduction",
    # Slice S3's seventh: the judge's orchestration entry, which reads produced claim text
    # to inspect it. Verified tree-unique before being pinned — a signal that collided with
    # existing text would report every file carrying it as an unregistered reader.
    "judge_produced_claim",
    # Slice S4's eighth and ninth: the record's write entry, which persists a flagged span,
    # and the meta-check's entry, which puts one in front of a second reader. Both verified
    # tree-unique before being pinned, on the same rule as the seventh.
    #
    # They are pinned rather than left unsignalled deliberately. A module that reads produced
    # claim text and carries NO recognisable signal is invisible to this scan, and the scan is
    # the thing that would notice if a later slice quietly grew a new reader beside them.
    # Adding the signals means these two files are policed by the same mechanism they extend.
    "record_produced_findings",
    "metacheck_produced_flag",
    # Slice S5's tenth: the resolution surface's egress, which puts a stored flag's span in
    # front of the MAIN SESSION so the operator can decide it. Verified tree-unique before
    # being pinned, on the same rule as the seventh.
    #
    # This one is signalled for the sharpest version of the reason the two above give. The
    # scan exists to notice a new reader of produced claim text; this is the most
    # instruction-shaped text in the corpus read by the most capable reader on the topic, so
    # leaving it invisible would be the worst possible exception to the rule the scan
    # enforces.
    "resolve_produced_flag",
)

#: The one-sentence bound, rendered everywhere the scan reports. Defined once so the CLI,
#: the /close report and the test module cannot each word it differently.
#:
#: Honestly re-based to SEVEN by slice S3 rather than left reading "six", to NINE by
#: slice S4 on the same rule, and to TEN by slice S5. This constant is operator-facing — it
#: is printed on every scan run and in the /close report — so leaving it misstating its own
#: signal count would be the same defect the close-report note was corrected for in S3.
#:
#: **Only the signal count moves. The "two registered seams are invisible by construction"
#: clause does NOT, and editing it "for consistency" would be a real harm rather than
#: bookkeeping.** That clause counts ``scan_visible=False`` rows, of which there are still
#: exactly two; S5's row is scan-visible. No test pins the clause — the text pin covers only
#: the phrase before ", not every" — so nothing would fail, and an operator-facing honesty
#: constant would silently become a false statement. That is precisely what
#: ``find_over_claims`` and the add-never-reword rule exist to prevent.
SIGNAL_LIMITATION = (
    "This scan recognises an enumerated set of ten code signals, not every conceivable "
    "way to read a file. A read path using none of them is invisible to it — two registered "
    "seams are invisible by construction, and a genuinely new unsignalled read would not be "
    "reported here."
)

_SEARCH_ROOTS = ("hooks", "skills", "agents", "bin", "rules")
_SEARCHABLE_SUFFIXES = (".py", ".sh", ".md", ".json")

#: EXPLICIT allowlist of LITERAL names — never a prefix or a glob, so an unlisted importer
#: still fails. These files carry signal tokens, or import the containment engine, because
#: they are the boundary's own tooling — this registry names the seams it governs, and the
#: two test modules exercise them — NOT because they read produced claims. The same names
#: are what the amended S1 consumer assertion exempts.
#:
#: **Grown from two literal names to three by slice S3, deliberately.** S3's own test module
#: must import the containment engine to exercise `decide_disposition` and the new copy
#: keys, which made it a third importer with no legal home: the S1 consumer assertion
#: rejects an importer that is neither a registered seam nor on this list. Three
#: alternatives were rejected on the record — folding S3's tests into the S2 module (breaks
#: the one-module-per-slice discipline both prior slices followed), giving the test module a
#: CONSUMER_REGISTRY row (a category error: a row asserts a genuine mediation of produced
#: claim content, and a pytest module calling a pure function on synthetic findings mediates
#: nothing), and not importing at all (which would forfeit the source-inspection proof that
#: the disposition rule branches on nothing but its arguments).
#:
#: The property this list enforces is EXPLICIT ENUMERATION, NEVER A PATTERN, and that
#: property survives a third literal name intact. The shipped positive control — a fourth,
#: unlisted importer still failing the amended assertion — is re-run unchanged and proves
#: the list has not degraded into a pass-through.
#: **Grown to four literal names by slice S4, on exactly the S3 precedent.** S4's own test
#: module imports the containment engine to exercise the new copy keys, the honesty tripwire
#: and the disposition constants, which makes it a fourth importer with no legal home.
#:
#: What did NOT go on this list is the more important half: S4's two PRODUCTION modules — the
#: record and the meta-check — are admitted as registered read seams with their own
#: `boundary_self` rows, not as infrastructure. A module that reads a produced claim's span is
#: a reader however much it belongs to the boundary; naming it here would have been the cheap
#: way past the consumer assertion and would have left two genuine readers out of the registry
#: the scan checks against.
#: **Grown to five literal names by slice S5, on exactly the same precedent.** S5's own test
#: module imports the containment engine to exercise the resolution vocabulary, the release
#: rule and the new copy keys, which makes it a fifth importer with no legal home.
#:
#: And again, what did NOT go on this list is the more important half: S5's PRODUCTION file —
#: the resolution surface's shim — is admitted as a registered read seam with its own
#: `boundary_self` row, not as infrastructure. It reads a produced claim's span and is
#: therefore a reader, however much it belongs to the boundary; naming it here would have been
#: the cheap way past the consumer assertion and would have left the topic's highest-risk
#: reader out of the registry the scan checks against.
INFRASTRUCTURE_FILES: Tuple[str, ...] = (
    "output_security_registry.py",
    "test_s2_output_security_registry.py",
    "test_s3_output_security_judge.py",
    "test_s4_output_security_record.py",
    "test_s5_output_security_resolution.py",
    # S6's test module, admitted for the same reason as its four siblings: it imports the
    # containment engine to exercise the origin thread, the provider store and the new copy
    # keys, which makes it a sixth importer with no legal home. S6 adds NO production file —
    # the store, the renderers and the rate all live in modules that were already counted.
    "test_s6_output_security_sources.py",
    # S7's test module, admitted for the same reason as its five siblings: it imports the
    # containment engine to exercise the quoting predicate it reuses, the new copy keys and
    # the honesty tripwire over them.
    #
    # And once again, what did NOT go on this list is the more important half: S7's PRODUCTION
    # file — `output_security_probe.py` — is admitted as a registered read seam with its own
    # `boundary_self` row, not as infrastructure. It composes claim text and puts it in front
    # of a model, which makes it a reader however synthetic that text is and however much the
    # module belongs to the boundary. Naming it here would have been the cheap way past the
    # consumer assertion and would have left the boundary's own instrument out of the registry
    # the scan checks against — which is exactly the shape this list exists to refuse.
    "test_s7_output_security_probe.py",
    # S-final's composition module, admitted for the same reason as its six siblings: it
    # imports the containment engine to drive the write seam, the partition and the
    # resolution vocabulary through one composed walk, which makes it a seventh importer
    # with no legal home.
    #
    # It is worth saying what this entry does NOT mean, because the walk is the first thing
    # on this topic to touch every mechanism at once. S-final ships **no production file at
    # all** — the walk is a test, the report it feeds is rendered from copy constants, and
    # the modules it exercises were every one of them counted by an earlier slice. So unlike
    # S7, this entry has no production counterpart that had to be argued about; and unlike
    # every slice before it, S-final adds nothing to the read surface, which is why the
    # covered set and the read-coverage figure are byte-identical across it.
    "test_sfinal_output_security_composition.py",
    # ── A rejected seventh entry, recorded because the rejection is the finding. ──
    #
    # S7's Layer-2 mirror update named the two probed seams in their exact registry-id form,
    # which put SIGNAL_SET tokens into that document. The scan immediately reported it as an
    # unregistered reader — the scan working exactly as designed, on the first new file to
    # carry a signal since S5 — and the obvious fix was to add the mirror to this list.
    #
    # That was tried and REVERTED, because it broke a stronger property. Two shipped
    # assertions (S1's `test_a5_nothing_reads_the_mirror…` and S6's
    # `test_a9_the_mirror_still_gates_nothing`) fail the moment a production module so much as
    # NAMES that document — deliberately that conservative, because "the mirror gates nothing"
    # is one of this topic's load-bearing invariants and a guard that only caught literal
    # `read_text` calls would be a guard against the easy case. Weakening two shipped
    # invariants to admit a documentation file would have been the trade the wrong way round.
    #
    # This comment therefore does not name that file either. The mirror was reworded instead —
    # it still names both seams, in prose rather than in token form. No entry was added here.
)

#: Anti-vacuity floor. Set to ONE BELOW the measured production count so the scan fails on
#: genuine shrinkage rather than on the tree as it stands. A floor AT the measured count
#: would fail the day an unrelated file legitimately stopped mentioning a signal; a floor of
#: zero would let a scan that looks at nothing pass.
#:
#: Re-based 8 → 9 by slice S3 as the measured count moved 9 → 10 (`output_security_judge.py`
#: carries the seventh signal). The one-below RULE is what is preserved; the number tracks it.
#:
#: Re-based 9 → 12 by slice S4 as the measured count moved 10 → 13. THREE files, not two: the
#: record module and the meta-check module carry the two new signals, and `_claim_harvest_trigger.py`
#: became visible for the first time because the quarantine gave it one — it had been reading
#: produced claims all along without carrying anything this scan could recognise.
#:
#: Re-based 12 → 13 by slice S5 as the measured count moved 13 → 14 — ONE file, the resolution
#: surface's shim, which is a NEW PRODUCTION FILE carrying a NEW signal token. Both halves are
#: needed to move this constant: a new file carrying an EXISTING token would move the file
#: count while leaving `SIGNAL_SET` alone, and a new function inside an existing module would
#: move neither. The measured count was read from the tree rather than predicted.
#:
#: Re-based 13 → 14 by slice S7 as the measured count moved 14 → 15 — ONE file,
#: `output_security_probe.py`, which is a NEW PRODUCTION FILE. Note that it carries only
#: EXISTING tokens (`flatten_backward`, `backward_claims`, `scope_addendum`), so `SIGNAL_SET`
#: is deliberately NOT extended: per the rule stated just above, a new file carrying an
#: existing token moves the file count and leaves the signal set alone, which is exactly this
#: case. The measured count was read from the tree rather than predicted.
PRODUCTION_FLOOR = 14


def config_root() -> Path:
    """The config tree this module lives in — so the scan works in a clone and in live."""
    return Path(__file__).resolve().parent.parent


def _is_test_path(path: Path, root: Path) -> bool:
    """A test file is not a consumer. Path-part and basename rules, per the plan."""
    rel = path.relative_to(root)
    return (
        "tests" in rel.parts
        or path.name.startswith("test_")
        or path.name.startswith("conftest")
    )


def tree_files(root: Path, suffixes: Sequence[str] = _SEARCHABLE_SUFFIXES):
    """Every searchable file under ``root``, skipping caches and backups (clones S1)."""
    for root_name in _SEARCH_ROOTS:
        base = root / root_name
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in suffixes:
                continue
            if "__pycache__" in path.parts or ".pytest_cache" in path.parts:
                continue
            if ".bak-" in path.name:
                continue
            yield path


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def discover_signal_files(root: Path) -> Mapping[str, Sequence[str]]:
    """Every file carrying a SIGNAL_SET identifier, split into production and test.

    The two ``INFRASTRUCTURE_FILES`` are excluded from BOTH lists. They carry signal tokens
    only because they are the boundary's own tooling — this registry has to name the seams
    it governs, and its test has to exercise them. Counting them would inflate the
    production figure the anti-vacuity floor is measured against, so the floor would stop
    meaning "how much real read machinery is out there" the moment this slice shipped.
    """
    production, test = [], []
    for path in tree_files(root):
        if path.name in INFRASTRUCTURE_FILES:
            continue
        text = _read(path)
        if not any(sig in text for sig in SIGNAL_SET):
            continue
        rel = str(path.relative_to(root))
        (test if _is_test_path(path, root) else production).append(rel)
    return {"production": production, "test": test}


def scan_read_boundary(root: Path = None) -> Mapping[str, object]:
    """Diff the discovered read paths against the registry. **The self-defending half.**

    Compares at FILE granularity against a per-egress registry — a file is expected iff at
    least one of its rows is scan-visible. The two ``scan_visible=False`` rows are excluded
    from that expectation, or every run would report them permanently missing.

    Three outcomes, deliberately not equal in force:

    * ``unregistered`` — a production file carries a claim-read signal and has no registry
      row. **GATES.** This is a new reader rejoining the unguarded boundary silently.
    * ``lost_coverage`` — a row recorded as covered no longer shows its covering call.
      **GATES.** Silently losing the one covered seam returns the read side to where it was.
    * ``stale`` — a row's anchor no longer resolves. **REPORTED ONLY.** A drifted line
      number is documentation staleness, not a security regression.

    Registered-but-uncovered deliberately does NOT gate: this slice ships coverage openly
    below full, so gating on it would abort every promotion.

    Three anti-vacuity guards, because a scan that passes by looking at nothing is worse
    than no scan: a production floor, a requirement that the registry still contain a covered
    row, and (in the tests) a positive control on a scratch tree.
    """
    root = config_root() if root is None else Path(root)
    discovered = discover_signal_files(root)
    production = list(discovered["production"])

    registered_files = {r.path for r in CONSUMER_REGISTRY if r.path}
    expected_visible = {r.path for r in CONSUMER_REGISTRY if r.path and r.scan_visible}

    # `discover_signal_files` has already dropped the infrastructure pair.
    unregistered = sorted(p for p in production if p not in registered_files)

    stale, lost_coverage = [], []
    for row in CONSUMER_REGISTRY:
        if not row.path:
            continue
        target = root / row.path
        text = _read(target)
        if not target.exists():
            stale.append({"id": row.id, "path": row.path, "why": "file does not exist"})
        elif row.hint:
            lines = text.splitlines()
            window = lines[max(0, (row.line or 1) - 4):(row.line or 1) + 3]
            if not any(row.hint in ln for ln in window):
                where = [i + 1 for i, ln in enumerate(lines) if row.hint in ln]
                stale.append({
                    "id": row.id, "path": row.path,
                    "why": (f"anchor moved from line {row.line} to {where}" if where
                            else f"anchor text no longer found at line {row.line}"),
                })
        if row.spotlit and row.coverage_evidence and row.coverage_evidence not in text:
            lost_coverage.append({
                "id": row.id, "path": row.path,
                "why": f"covering call {row.coverage_evidence!r} is gone",
            })

    vacuity = []
    if len(production) < PRODUCTION_FLOOR:
        vacuity.append(
            f"only {len(production)} production files carry a claim-read signal "
            f"(floor {PRODUCTION_FLOOR}) — the scan may be looking at nothing"
        )
    if not any(r.spotlit for r in CONSUMER_REGISTRY):
        vacuity.append("the registry contains no covered seam at all")

    missing_visible = sorted(p for p in expected_visible if not (root / p).exists())

    clean = not (unregistered or lost_coverage or vacuity)
    return {
        "clean": clean,
        "unregistered": unregistered,
        "lost_coverage": lost_coverage,
        "stale": stale,
        "vacuity": vacuity,
        "missing_registered_files": missing_visible,
        "counts": {
            "production_signal_files": len(production),
            "test_signal_files": len(discovered["test"]),
            "registered_seams": len(CONSUMER_REGISTRY),
            "scan_visible_seams": sum(1 for r in CONSUMER_REGISTRY if r.scan_visible),
            "covered_seams": sum(1 for r in CONSUMER_REGISTRY if r.spotlit),
        },
        "limitation": SIGNAL_LIMITATION,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Ports — the read trail. TWO methods, and deliberately no aggregation method:
# aggregation is pure domain (`compute_read_omtm`) and must never reach for I/O.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ClaimReadRow:
    """One observed read of a produced claim through an instrumented seam.

    ``judged`` is a STRING, not an enum, and reads empty at S2 on purpose: its vocabulary
    belongs to the slice that ships the judge. Typing it loosely now is what keeps S3 from
    having to change this shape — the same reason S1 left its port payloads as plain
    mappings.

    ``enveloped_at_write`` ships present and FALSE rather than omitted: the conjunct must be
    joined explicitly, so that a later reader sees a conjunct that is unsatisfied rather
    than a conjunct that was forgotten.
    """

    seam_id: str
    spotlit: bool
    enveloped_at_write: bool
    judged: str
    read_at: str


@runtime_checkable
class ReadTrailPort(Protocol):
    """I/O-only read-trail surface: append a row; read the trail back.

    Two methods, cloning ``assessment_engine.MonitoringPort``. There is deliberately no
    aggregation method — the metric is computed in the pure domain function below, never in
    an adapter that could quietly reach for a file while doing it.
    """

    def append_read(self, row: ClaimReadRow) -> None:
        ...

    def read_trail(self) -> Sequence[ClaimReadRow]:
        ...


#: The conjuncts an observed read must ALL satisfy to count as covered, in the order they
#: are reported as blocking. `enveloped_at_write` first, `judged` second.
READ_CONJUNCTS: Tuple[str, ...] = ("enveloped_at_write", "spotlit", "judged")
BLOCKING_ORDER: Tuple[str, ...] = ("enveloped_at_write", "judged", "spotlit")


def compute_read_omtm(rows: Sequence[ClaimReadRow],
                      registry: Sequence[ConsumerSeam]) -> Mapping[str, object]:
    """The read-coverage read-out. **Pure domain — takes rows and a registry, does no I/O.**

    Honest in three specific ways a bare percentage would not be:

    1. It names the conjunct holding the number down, so a zero reads as an unmet conjunct
       rather than as "the boundary is failing". **Corrected by slice S3:** this line used to
       read "the judging half does not exist yet", and that stopped being true the moment a
       judge shipped. The zero now reads as "no producer envelopes at write" — the
       ``enveloped_at_write`` conjunct — which is what actually holds it down.
    2. It states what its denominator actually covers, so measuring only the ONE instrumented
       seam cannot stand in for the whole registered surface — and it records that the
       instrumented count is itself CONDITIONAL on the deep path. The size of that surface is
       deliberately NOT restated here: it is a number that moves every time a seam is
       registered, and a prose copy of it in a docstring is a thing that goes stale silently.
    3. It returns no figure at all, rather than a fabricated one, when there is nothing to
       divide.
    """
    known = {r.id for r in registry}
    counted = [r for r in rows if r.seam_id in known]
    excluded = sorted({r.seam_id for r in rows if r.seam_id not in known})

    denominator = len(counted)
    satisfied = sum(
        1 for r in counted
        if r.enveloped_at_write and r.spotlit and bool(str(r.judged).strip())
    )
    rate = None if denominator == 0 else round(satisfied / denominator, 4)

    blocking = None
    if denominator:
        for conjunct in BLOCKING_ORDER:
            if conjunct == "judged":
                unmet = any(not str(r.judged).strip() for r in counted)
            else:
                unmet = any(not getattr(r, conjunct) for r in counted)
            if unmet:
                blocking = conjunct
                break

    instrumented = [r for r in registry if r.spotlit]
    return {
        "read_coverage_rate": rate,
        "denominator": denominator,
        "satisfied": satisfied,
        "blocking_conjunct": blocking,
        "conjuncts": list(READ_CONJUNCTS),
        "denominator_covers": {
            "registered_seams": len(registry),
            "scan_visible_seams": sum(1 for r in registry if r.scan_visible),
            "instrumented_seams": len(instrumented),
            "note": (
                f"The denominator counts observed reads at the {len(instrumented)} "
                f"instrumented seam(s) only, out of {len(registry)} registered seams "
                f"({sum(1 for r in registry if r.scan_visible)} of them scan-visible). It is "
                f"NOT a measure of the whole read surface. The instrumented count is itself "
                f"conditional: its row is emitted inside flatten_backward, which the deep "
                f"path reaches but the retained regex fallback and every --auto run do not, "
                f"so a run that never enters the deep path contributes no row at all and "
                f"must not be read as a covered read. As of slice S3 a judge exists and "
                f"records a disposition per claim, so the judged conjunct is now reachable; "
                f"the figure is held by enveloped_at_write, because nothing joins those "
                f"dispositions to read rows and no producer applies the write-side envelope."
            ),
        },
        "excluded_unknown_seams": excluded,
        "uncovered_seams": list(uncovered_seams(registry)),
        "residual_risk": OPERATOR_COPY["residual_risk"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Adapters + CLI.
# ─────────────────────────────────────────────────────────────────────────────


def trail_path() -> Path:
    """Where the read trail lives. Env-overridable so tests never touch live state."""
    base = os.environ.get("OUTPUT_SECURITY_TRAIL_DIR")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".claude", "state", "output_security")
    return Path(base) / "claim-reads.jsonl"


class JsonlReadTrail:
    """``ReadTrailPort`` over a JSONL file, COMPOSING the sibling engine's trail adapter.

    Composition rather than extracting a shared ``_jsonl_trail.py``: the shared module is
    the cleaner long-term move, but it would mean editing a live engine's surface on behalf
    of a foreign slice, so it is deferred deliberately.

    **One type-lie, stated rather than hidden:** ``JsonlRunTrailAdapter.append_run`` is
    annotated as taking an ``AssessmentRunSummary``. It is passed a ``ClaimReadRow`` here.
    That works because the adapter only calls ``dataclasses.asdict`` on it, and both are
    dataclasses — but the annotation is wrong at this call, and a future change to that
    adapter which actually reads an assessment field would break this silently. If that
    happens, extract the shared module rather than patching around it here.
    """

    def __init__(self, path: Path = None) -> None:
        self._path = Path(path) if path else trail_path()

    def append_read(self, row: ClaimReadRow) -> None:
        from assessment_engine import JsonlRunTrailAdapter
        JsonlRunTrailAdapter(self._path).append_run(row)  # type: ignore[arg-type]

    def read_trail(self) -> Sequence[ClaimReadRow]:
        if not self._path.exists():
            return ()
        out = []
        for line in self._path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            try:
                out.append(ClaimReadRow(
                    seam_id=payload["seam_id"], spotlit=bool(payload.get("spotlit")),
                    enveloped_at_write=bool(payload.get("enveloped_at_write")),
                    judged=str(payload.get("judged", "")),
                    read_at=str(payload.get("read_at", "")),
                ))
            except KeyError:
                continue
        return tuple(out)


def record_claim_read(*, seam_id: str, spotlit: bool,
                      port: ReadTrailPort = None) -> None:
    """Record one observed read. **Write-only and off the enforcement path.**

    Nothing reads this back synchronously and nothing gates on it; the metric is computed on
    demand at /close.

    ``enveloped_at_write`` is False because no producer calls the write-side envelope yet.
    ``judged`` is empty for a different reason after slice S3: a judge now exists and records
    a disposition per claim at the write seam, but nothing joins those dispositions to these
    read rows. Neither may be stubbed to make the number look better — a pass-through judge
    or a read-time container counted as a write-time envelope would corrupt the exact metric
    this shape exists to protect.
    """
    row = ClaimReadRow(
        seam_id=seam_id, spotlit=spotlit,
        enveloped_at_write=False, judged="",
        read_at=datetime.now(timezone.utc).isoformat(),
    )
    (port or JsonlReadTrail()).append_read(row)


def render_close_report(rows: Sequence[ClaimReadRow] = None) -> str:
    """The /close read-out. Idempotent: a pure projection of the trail and the registry."""
    rows = JsonlReadTrail().read_trail() if rows is None else rows
    m = compute_read_omtm(rows, CONSUMER_REGISTRY)
    rate = "no figure (nothing observed to divide)" if m["read_coverage_rate"] is None \
        else f"{m['read_coverage_rate']}"
    lines = [
        "### Output-security — produced-claim read coverage",
        "",
        f"- Read coverage: **{rate}** over {m['denominator']} observed read(s).",
    ]
    if m["blocking_conjunct"]:
        lines.append(
            f"- Blocking conjunct: **{m['blocking_conjunct']}** — the figure is held here, "
            f"which is incompleteness, not failure."
        )
    d = m["denominator_covers"]
    lines += [
        f"- Denominator covers: {d['registered_seams']} registered seams, "
        f"{d['scan_visible_seams']} scan-visible, {d['instrumented_seams']} instrumented.",
        f"- {d['note']}",
        f"- {SIGNAL_LIMITATION}",
        f"- {m['residual_risk']}",
        "",
        f"Uncovered seams ({len(m['uncovered_seams'])}):",
    ]
    for seam in m["uncovered_seams"]:
        lines.append(f"  - `{seam['id']}` ({seam['mediation']}) — {seam['reason']}")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else ""
    if cmd == "scan":
        result = scan_read_boundary()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        # The bound is printed on EVERY run, not only on failure — a reader must not have
        # to fail the scan to learn what it cannot see.
        print(f"\n{SIGNAL_LIMITATION}", file=sys.stderr)
        return 0 if result["clean"] else 1
    if cmd == "rows":
        print(json.dumps([dataclasses.asdict(r) for r in CONSUMER_REGISTRY],
                         indent=2, ensure_ascii=False))
        return 0
    if cmd == "omtm":
        print(json.dumps(
            compute_read_omtm(JsonlReadTrail().read_trail(), CONSUMER_REGISTRY),
            indent=2, ensure_ascii=False))
        return 0
    if cmd == "close-report":
        print(render_close_report())
        return 0
    print("usage: output_security_registry.py [scan | rows | omtm | close-report]",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

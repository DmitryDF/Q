#!/usr/bin/env python3
"""Research pipeline state machine (code layer / L1).

Mirrors the pattern of pre_plan_gates.py but is an INDEPENDENT module.
It NEVER imports from pre_plan_gates.py — the two pipelines evolve
separately (Cockburn evolution test: a change to plan gates must not
force a change here, and vice versa).

This module enforces the research skill checkpoint sequence:

    r0_intake -> r1_scope -> r2_research -> r3_synthesis
              -> r4_factcheck -> r5_recommend

Some projects declare a subset pipeline via PIPELINE_OVERRIDES.

Stdlib only.
"""

import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SKILL_VERSION = "1.0"
RULES_VERSION = "1.0"

# Schema v2.0: per-cycle nested state under `cycles[cycle_id]`. v1.0 stored
# topic_slug/research_file_path/checkpoints at top-level (single-cycle per
# session). Migration is idempotent + non-destructive (legacy top-level
# fields stay synced with cycles[DEFAULT_CYCLE_ID] for backward-compat
# readers like research_token_parser.py).
SCHEMA_VERSION = "2.0"
DEFAULT_CYCLE_ID = "default"

# Whitelist of caller skills allowed to register r0_intake with `user_approved_
# scope` / `autonomous_scope` set at all. Non-whitelisted caller_skill values
# are rejected; absent caller_skill falls through to manual scope-framing.
#
# MAJOR 5 (research-entry-point-enforcement S4 review): this comment used to
# say the whitelist alone auto-advances a cycle to `r1_scope_approved` — true
# before S4, false since. As of S4 (`resolve_approval_artifact`, below) a
# whitelisted `caller_skill` is NECESSARY but never SUFFICIENT: the flip also
# requires the payload to carry an approval ARTIFACT (`user_approved_scope`
# plus the `scope` object it claims was approved, tagged with a `scope_
# provenance` of `fresh-answer` / `git-head` / `explicit-argument`), or the
# Autonomous route's recorded bypass. `user_approved_scope` / `autonomous_
# scope` are therefore no longer "additional state, never standalone
# authorization" — together with a whitelisted caller they ARE authorization
# now; see `resolve_approval_artifact`'s docstring for the current contract.
# Additive only; do not reorder or remove entries.
CALLER_SKILL_WHITELIST = frozenset(
    {"/clarification", "/work-decode", "/research"}
)

# Per-caller binding of which downstream research tool a whitelisted caller is
# allowed to dispatch. Sibling of CALLER_SKILL_WHITELIST: that table gates WHO
# may register; this one gates WHAT tool they declare they will dispatch.
# Enforcement is CONDITIONAL — a caller is checked only if it appears here, so
# callers absent from this table (e.g. /work-decode, /research) are unaffected
# (non-breaking). This is a registration-time, self-declared check (it catches
# honest drift where a caller declares the wrong neighbor tool); it is NOT a
# dispatch interceptor — actual tool dispatch is gated by research-scope-gate.sh.
# Additive only; do not reorder or remove entries.
#
# research-entry-point-enforcement S2 (2026-09-22): the ONE entry's token was
# `Skills/research-en.md`, the Projects-side methodology file. That file is now a
# committed pointer stub and the methodology is the harness skill
# `~/.claude/skills/research/SKILL.md`, which registers r0_intake itself for every
# caller. The token is repointed — a token, not a file read, so no alias is kept —
# and /clarification's declaration in `skills/clarification/steps.md` changed in the
# same change. `/work-decode` and `/research` stay unbound here as before.
CALLER_DOWNSTREAM_WHITELIST = {
    "/clarification": frozenset({"~/.claude/skills/research/SKILL.md"}),
}

# --------------------------------------------------------------------------- #
# Approval artifacts (research-entry-point-enforcement S4 / A4).
#
# Channel 3 of the locked Guiding Policy: "Approval is backed by an ARTIFACT,
# not a string, and is scoped per cycle. A whitelisted caller name is necessary
# and NEVER sufficient."
#
# Until S4 the flip at r0_intake read `caller_skill is not None` — so naming a
# whitelisted skill WAS the approval. A typed `/research` registering its own
# intake therefore asserted an approval no one had given. The resolver below
# replaces that test: the payload must CARRY the artifact.
#
# U3 admits exactly three ways a scope can be an artifact, and each is tagged on
# the cycle so a later reader can tell them apart:
#   fresh-answer       — the operator answered the framing exchange this session.
#   git-head           — a predefined scope read from a thought file that was in
#                        git HEAD before the session began (verifiable after the
#                        fact by anyone).
#   explicit-argument  — a predefined scope in a file named explicitly on the
#                        call (`--from <file>`), recorded with the argument that
#                        named it. This is the `/clarification` Step-6 case: the
#                        spine is uncommitted in the session writing it, so the
#                        git-HEAD branch cannot admit it.
# and one route-level exemption, which is NOT a scope artifact and is recorded
# as a bypass rather than as an approval:
#   autonomous         — the Autonomous route approves on the operator's behalf
#                        by a decision locked elsewhere.
#
# Anything else DOWNGRADES to interactive confirmation — the caller asks. It is
# never a refusal: an intake with no artifact still registers, it simply does not
# flip. That direction matters; refusing would break every caller that registers
# before framing.
#
# S4 round-2, ITEM 7 — two properties of `fresh-answer` worth stating plainly
# where a reader would actually look, rather than leaving them implicit in
# `resolve_approval_artifact`'s branching:
#
# 1. `fresh-answer`'s complete approval condition is looser than the two
#    PREDEFINED branches, and deliberately so. `git-head` and
#    `explicit-argument` were tightened to additionally require a non-empty
#    `scope_source_ref` (MINOR 8) so a predefined approval stays re-checkable
#    after the fact. `fresh-answer` has no comparable re-checkable property —
#    a direct framing exchange produces no file to name — so it is NOT
#    tightened the same way: "whitelisted caller + any non-empty `scope`
#    dict" is the whole condition. Do not tighten it to require a reference;
#    that would break every typed run, which has nothing to point at.
# 2. `provenance = payload.get("scope_provenance") or APPROVAL_FRESH_ANSWER`
#    (below, in `resolve_approval_artifact`) means a FALSY `scope_provenance`
#    (`""`, `0`, `False`, `None`) reads as ABSENT and silently defaults to
#    `fresh-answer`, while any unrecognised NON-EMPTY string instead
#    downgrades the whole intake to interactive confirmation. The two
#    failure directions differ for what looks like the same "didn't say"
#    case — an explicit "I have no provenance" (a falsy value) is therefore
#    the STRONGEST default a caller can send, stronger than a typo'd or
#    unrecognised provenance string.
APPROVAL_FRESH_ANSWER = "fresh-answer"
APPROVAL_GIT_HEAD = "git-head"
APPROVAL_EXPLICIT_ARGUMENT = "explicit-argument"
APPROVAL_AUTONOMOUS = "autonomous"

# The provenance values a PREDEFINED scope may carry (U3's two branches).
PREDEFINED_SCOPE_PROVENANCE = frozenset(
    {APPROVAL_GIT_HEAD, APPROVAL_EXPLICIT_ARGUMENT}
)
# Every provenance a `user_approved_scope` artifact may declare.
SCOPE_PROVENANCE_VALUES = frozenset(
    {APPROVAL_FRESH_ANSWER} | PREDEFINED_SCOPE_PROVENANCE
)

# The reason written onto the Autonomous route's bypass record. Channel 4: "every
# bypass leaves a record" — this one names the decision it rests on and the
# question that is still open, so it reads as carried, not as resolved.
AUTONOMOUS_BYPASS_REASON = (
    "Autonomous route, locked in `research-scope-framing-ui_THOUGHT`; "
    "pending `/clarification --from` (filed 2026-09-22)"
)


def resolve_approval_artifact(payload):
    """Decide whether an r0_intake payload CARRIES an approval artifact.

    Pure and total: takes the payload dict (or anything), returns a dict, never
    raises, never reads the filesystem, never mutates its argument.

    Returns:
        {"approved":   bool,   # may r1_scope_approved flip for this cycle?
         "provenance": str|None,   # which artifact said so (tagged on the cycle)
         "bypass":     bool,   # route-level exemption, not a scope artifact
         "reason":     str}    # why — surfaced when it did NOT approve

    Three properties are load-bearing:

    1. **A whitelisted caller is necessary and never sufficient.** The whitelist
       check stays exactly where it was (validate_schema raises on a
       non-whitelisted name), and this resolver additionally requires an
       artifact. Removing the caller requirement would let an unwhitelisted
       caller self-approve by attaching a scope object; removing the artifact
       requirement restores the channel-3 defect.

    2. **`user_approved_scope` alone does not approve — it needs the scope.**
       The flag says "an operator approved something"; the `scope` object is the
       something. A flag with no scope is the same unverifiable string the
       artifact rule exists to reject, so it downgrades.

    3. **The failure direction is DOWNGRADE, never refuse.** Every non-approving
       path returns `approved: False` with a reason the caller can show, and the
       intake still registers. `r0_intake` is how a run declares the file it will
       write; making it refuse would lose that declaration for exactly the runs
       that have not been approved yet.
    """
    if not isinstance(payload, dict):
        return {
            "approved": False,
            "provenance": None,
            "bypass": False,
            "reason": "no payload",
        }

    caller_skill = payload.get("caller_skill")
    # Property 1 — necessary, never sufficient.
    #
    # The isinstance guard is load-bearing, not defensive noise: `x not in
    # frozenset` raises TypeError on an unhashable value, so a payload carrying
    # a list here would take the intake down instead of downgrading. This
    # function is on the gate path and its own contract says it never raises.
    #
    # On the PRODUCTION path this exact hazard is already caught one call
    # earlier — `validate_schema` (`caller_skill not in CALLER_SKILL_WHITELIST`)
    # runs before `cmd_advance` ever calls this resolver — but that earlier
    # check has the same unguarded-`in` shape and would itself raise TypeError
    # on an unhashable `caller_skill`, rather than downgrade. This function's
    # own contract is "never raises" regardless of who calls it or what ran
    # before it, so the guard stays here too: a caller that reaches this
    # resolver directly (as the tests do, and as any future caller might)
    # must still get a downgrade, not a crash, independent of validate_schema.
    if (not isinstance(caller_skill, str)
            or caller_skill not in CALLER_SKILL_WHITELIST):
        return {
            "approved": False,
            "provenance": None,
            "bypass": False,
            "reason": (
                "no whitelisted caller_skill; approval requires a whitelisted "
                "caller AND an approval artifact"
            ),
        }

    # The Autonomous route: an exemption recorded as a bypass, not an approval
    # artifact. Checked before the scope branch because this route deliberately
    # carries no operator answer to point at.
    if payload.get("autonomous_scope") is True:
        return {
            "approved": True,
            "provenance": APPROVAL_AUTONOMOUS,
            "bypass": True,
            "reason": AUTONOMOUS_BYPASS_REASON,
        }

    if payload.get("user_approved_scope") is True:
        scope = payload.get("scope")
        # Property 2 — the flag needs the scope it claims was approved.
        if not isinstance(scope, dict) or not scope:
            return {
                "approved": False,
                "provenance": None,
                "bypass": False,
                "reason": (
                    "user_approved_scope set without a `scope` object; the flag "
                    "is not itself the artifact — confirm the scope with the "
                    "operator BEFORE calling r0_intake again for this cycle. "
                    "r0_intake cannot be called a second time for the same "
                    "cycle, so a cycle that registers this way is permanently "
                    "unapprovable; recover it with `python3 "
                    "${KIT_HOOKS_DIR}/research_pipeline.py reset <session-id> "
                    "--cycle-id <id>` and re-register with a real artifact."
                ),
            }
        provenance = payload.get("scope_provenance") or APPROVAL_FRESH_ANSWER
        # MAJOR 4: `provenance not in SCOPE_PROVENANCE_VALUES` raises TypeError
        # on an unhashable `provenance` (e.g. a list) exactly as the
        # `caller_skill` guard above would without its isinstance check —
        # `scope_provenance` is an optional field `validate_schema` does not
        # type-check, so a bad value reaches here unguarded. Same fix, same
        # reason: this function's contract is "never raises".
        if (not isinstance(provenance, str)
                or provenance not in SCOPE_PROVENANCE_VALUES):
            return {
                "approved": False,
                "provenance": None,
                "bypass": False,
                "reason": (
                    "scope_provenance {p!r} is not one of {v} — confirm the "
                    "scope with the operator BEFORE calling r0_intake for "
                    "this cycle (r0_intake cannot be called a second time for "
                    "the same cycle; recover an already-registered cycle with "
                    "`python3 ${KIT_HOOKS_DIR}/research_pipeline.py reset "
                    "<session-id> --cycle-id <id>` and re-register)".format(
                        p=provenance, v=sorted(SCOPE_PROVENANCE_VALUES)
                    )
                ),
            }
        # MINOR 8: a PREDEFINED-scope artifact (`git-head` / `explicit-argument`)
        # is only re-checkable if it names the file it claims was approved.
        # `git-head`'s own doc comment calls it "verifiable after the fact by
        # anyone" — but `scope_source_ref` was optional and unenforced, so a
        # `git-head` approval could carry nothing pointing at what was
        # approved. `fresh-answer` has no artifact file to name and is exempt.
        # This function stays pure: it requires the reference exists, it never
        # reads the filesystem or git to check that the reference is real.
        if provenance in PREDEFINED_SCOPE_PROVENANCE:
            scope_source_ref = payload.get("scope_source_ref")
            if not isinstance(scope_source_ref, str) or not scope_source_ref.strip():
                return {
                    "approved": False,
                    "provenance": None,
                    "bypass": False,
                    "reason": (
                        "scope_provenance {p!r} requires a non-empty "
                        "scope_source_ref naming the artifact it claims was "
                        "approved; none was given — confirm the scope with "
                        "the operator BEFORE calling r0_intake for this cycle "
                        "(r0_intake cannot be called a second time for the "
                        "same cycle; recover an already-registered cycle "
                        "with `python3 ${KIT_HOOKS_DIR}/research_pipeline.py "
                        "reset <session-id> --cycle-id <id>` and "
                        "re-register)".format(p=provenance)
                    ),
                }
        return {
            "approved": True,
            "provenance": provenance,
            "bypass": False,
            "reason": "approval artifact carried ({p})".format(p=provenance),
        }

    # Property 3 — a whitelisted name and nothing else. This is the case that
    # used to flip, and the one the whole slice exists to stop flipping.
    return {
        "approved": False,
        "provenance": None,
        "bypass": False,
        "reason": (
            "caller_skill {cs!r} names a whitelisted caller but the payload "
            "carries no approval artifact; a name is never approval".format(
                cs=caller_skill
            )
        ),
    }


RESEARCH_SEQUENCE = [
    "r0_intake",
    "r1_scope",
    "r2_research",
    "r3_synthesis",
    "r4_factcheck",
    "r5_recommend",
]

# Per-checkpoint schema. Each entry: {"required": [...], "optional": [...]}.
RESEARCH_SCHEMAS = {
    "r0_intake": {
        "required": ["research_file_path"],
        "optional": [
            "topic_slug",
            "caller_skill",
            "caller_session_id",
            "scope",
            "user_approved_scope",
            "autonomous_scope",
            # S4/A4/U3: which of the three artifact branches a predefined or
            # fresh scope arrived by. Absent + user_approved_scope -> defaults to
            # `fresh-answer`, which is what a direct framing exchange produces.
            "scope_provenance",
            # S4/U3: the argument that named the predefined scope file, recorded
            # verbatim so an `explicit-argument` approval can be re-checked later.
            "scope_source_ref",
            "downstream_tool",
            "scope_record",
        ],
    },
    "r1_scope": {"required": ["search_scope"], "optional": []},
    "r2_research": {"required": ["sources_count"], "optional": ["research_file_path"]},
    "r3_synthesis": {"required": ["claims_count"], "optional": []},
    "r4_factcheck": {"required": [], "optional": ["verdict"]},
    "r5_recommend": {"required": [], "optional": ["recommendation_written"]},
}

# Projects keyed by slug -> the list of required checkpoints for that project.
# Projects NOT in this dict must complete the full RESEARCH_SEQUENCE.
PIPELINE_OVERRIDES = {
    # your-project uses DB Context Loading (Phase 0 in stock-research.md)
    # instead of r1_scope.
    "your-project": [
        "r0_intake",
        "r2_research",
        "r3_synthesis",
        "r4_factcheck",
        "r5_recommend",
    ],
    # CV project does quick research only, no synthesis needed.
    "CV": ["r0_intake", "r1_scope", "r2_research", "r5_recommend"],
}


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #

def _default_state_dir():
    return Path.home() / ".claude" / "state" / "research_pipeline"


def _resolve_state_dir(state_dir=None):
    """Resolve the state directory.

    Precedence: explicit state_dir arg > RP_STATE_DIR env var > default.
    """
    if state_dir is not None:
        return Path(state_dir)
    env = os.environ.get("RP_STATE_DIR")
    if env:
        return Path(env)
    return _default_state_dir()


def _state_path(sid, state_dir=None):
    return _resolve_state_dir(state_dir) / f"RP-{sid}.json"


def _archive_dir(state_dir=None):
    return _resolve_state_dir(state_dir) / "archive"


def _now():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# State I/O
# --------------------------------------------------------------------------- #

def _read_state(sid, state_dir=None):
    path = _state_path(sid, state_dir)
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as fh:
        state = json.load(fh)
    return _migrate_state(state)


def _write_state(sid, state, state_dir=None):
    path = _state_path(sid, state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(str(tmp), str(path))


def _new_cycle():
    """Initialize a new per-cycle state block (v2)."""
    now = _now()
    return {
        "topic_slug": None,
        "research_file_path": None,
        "checkpoints": {},
        "r1_scope_approved": False,
        "caller_skill": None,
        "caller_session_id": None,
        # Additional state (S2): how scope was approved + revocation +
        # back-end Stop-gate non-PASS verdict bookkeeping. None of these
        # are authorization — the whitelist remains the sole gate.
        "user_approved_scope": False,
        "autonomous_scope": False,
        "r1_scope_revoked": False,
        "non_pass_abort_ts": None,
        "non_pass_verdict": None,
        "created_at": now,
        "updated_at": now,
    }


def _new_state(sid):
    now = _now()
    return {
        "session_id": sid,
        "schema_version": SCHEMA_VERSION,
        "skill_version": SKILL_VERSION,
        "rules_version": RULES_VERSION,
        # Legacy mirror — synced with cycles[DEFAULT_CYCLE_ID] on write so
        # back-compat readers (research_token_parser.py) keep working.
        "topic_slug": None,
        "research_file_path": None,
        "checkpoints": {},
        "cycles": {DEFAULT_CYCLE_ID: _new_cycle()},
        "bypass": False,
        "created_at": now,
        "updated_at": now,
    }


def _migrate_state(state):
    """Idempotent migration v1 → v2. Non-destructive: legacy top-level fields
    are preserved as a mirror of cycles[DEFAULT_CYCLE_ID].
    """
    if not isinstance(state, dict):
        return state
    if state.get("schema_version") == SCHEMA_VERSION and isinstance(
        state.get("cycles"), dict
    ):
        return state  # already v2
    # v1 → v2: move single-cycle fields into cycles[DEFAULT_CYCLE_ID].
    cycles = state.get("cycles") if isinstance(state.get("cycles"), dict) else {}
    if DEFAULT_CYCLE_ID not in cycles:
        default = _new_cycle()
        default["topic_slug"] = state.get("topic_slug")
        default["research_file_path"] = state.get("research_file_path")
        default["checkpoints"] = state.get("checkpoints", {}) or {}
        default["created_at"] = state.get("created_at", default["created_at"])
        default["updated_at"] = state.get("updated_at", default["updated_at"])
        cycles[DEFAULT_CYCLE_ID] = default
    state["cycles"] = cycles
    state["schema_version"] = SCHEMA_VERSION
    return state


def _get_or_create_cycle(state, cycle_id):
    """Return cycle dict for cycle_id; create one if missing."""
    cycles = state.setdefault("cycles", {})
    if cycle_id not in cycles:
        cycles[cycle_id] = _new_cycle()
    return cycles[cycle_id]


def _sync_legacy_mirror(state):
    """Keep top-level legacy fields in sync with cycles[DEFAULT_CYCLE_ID]
    so single-cycle back-compat readers see the same data.
    """
    default = state.get("cycles", {}).get(DEFAULT_CYCLE_ID)
    if default is None:
        return state
    state["topic_slug"] = default.get("topic_slug")
    state["research_file_path"] = default.get("research_file_path")
    state["checkpoints"] = default.get("checkpoints", {})
    return state


# --------------------------------------------------------------------------- #
# Pipeline helpers
# --------------------------------------------------------------------------- #

def _expected_sequence(topic_slug):
    if topic_slug:
        return PIPELINE_OVERRIDES.get(topic_slug, RESEARCH_SEQUENCE)
    return RESEARCH_SEQUENCE


def _next_checkpoint(completed, topic_slug):
    """First checkpoint in the expected sequence not yet completed, or None."""
    seq = _expected_sequence(topic_slug)
    completed_set = set(completed)
    for cp in seq:
        if cp not in completed_set:
            return cp
    return None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #

def validate_sequence(completed_checkpoints, checkpoint, topic_slug=None):
    """Validate that `checkpoint` is the next legal step.

    Raises ValueError on any violation.
    """
    completed = set(completed_checkpoints)
    expected_sequence = (
        PIPELINE_OVERRIDES.get(topic_slug, RESEARCH_SEQUENCE)
        if topic_slug
        else RESEARCH_SEQUENCE
    )

    if checkpoint not in expected_sequence:
        raise ValueError(
            "Checkpoint {cp} not in declared pipeline for project {proj}. "
            "Declared: {seq}. This is a subset-mismatch: the checkpoint is "
            "not in the declared PIPELINE_OVERRIDES.".format(
                cp=checkpoint,
                proj=topic_slug or "default",
                seq=expected_sequence,
            )
        )

    if checkpoint in completed:
        raise ValueError("Already completed: {cp}".format(cp=checkpoint))

    # The next expected checkpoint is the first item in expected_sequence
    # not yet completed.
    expected_next = None
    for cp in expected_sequence:
        if cp not in completed:
            expected_next = cp
            break

    if checkpoint != expected_next:
        raise ValueError(
            "Sequence violation: expected {exp}, got {got}. "
            "Completed: {done}".format(
                exp=expected_next,
                got=checkpoint,
                done=sorted(completed),
            )
        )


# Control characters, NUL included. A reason string or a path carrying one of
# these reaches a terminal and a JSON record; `output-security.md` strips the same
# class before rendering, and the same class has no business in a filename.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def validate_research_file_path(declared):
    """Validate + canonicalize the `research_file_path` a run declares at intake.

    research-entry-point-enforcement S5/A5. The run declares at `r0_intake` where
    its findings will go; `r2_research` (S7) later verifies it kept that promise,
    and S6's recorder writes there. So this is the one moment the declaration can
    be refused before anything is built on it, and the four vectors A5 names are
    refused here:

      * a NUL byte, or any other control character;
      * a `..` segment — checked on the DECLARED string, before resolution, so the
        refusal names the vector rather than some resolved path;
      * `~/.ssh/id_rsa` — caught by the `.md` requirement;
      * a symlink that leaves the declared file's own directory.

    Returns the canonical (symlink-resolved, absolute) path as a string. The caller
    records it ADDITIVELY; the declared value itself is stored verbatim, because
    every downstream reader takes the path as given (the engine's
    `os.path.dirname(draft_path)`, the harvest, `_angles_sidecar_path`) and most
    real declarations are workspace-relative — rewriting the stored value would
    change what all of them address.

    **What this does NOT promise, stated rather than implied.** A5's wording is
    "inside the project's research directory", and that is NOT what is enforced.
    There is no single such directory: research legitimately lives in more than
    one tree, and the live manifest store proves it — a cycle registers
    `~/repos/[YourProject]/Thoughts/tax-registration-…_RESEARCH.md`
    (measured 2026-09-29 over 46 cycles in 26 manifests), which a Projects-root
    containment rule would refuse. The locked Q10 reasoning refuses scope-binding
    this pipeline to one repo for exactly that reason, so a single-root rule would
    contradict it. What is enforced instead is an ESCAPE check, and it covers only
    the declared file's FINAL COMPONENT: a declared file may not itself be a
    symlink whose target leaves its own directory. An ancestor directory that is a
    symlink pointing outside is NOT caught by this check — `enclosing` is computed
    by resolving the declared file's parent directory (`os.path.realpath`) before
    the comparison below, so a symlinked ancestor is already folded into
    `enclosing` and can never disagree with the resolved file. This is a
    deliberate bound, not an oversight: containment is already disclaimed above,
    so closing the ancestor case would not buy a containment this pipeline still
    could not claim — it closes the one vector worth closing given that (a
    declared file that is itself an escaping symlink), and no more.

    **And "not an existing file the run did not create" is delivered in part.**
    Whether *this run* created an existing file is not knowable at intake — no
    create-record exists yet, and `r0_intake` is the first checkpoint. What is
    checkable is the shape of an existing target: a directory, a device or a fifo
    is refused. An existing regular `.md` file is ADMITTED, deliberately — a second
    research cycle appending to a report it already wrote is the normal path, and
    refusing it would break every re-run.
    """
    if not isinstance(declared, str) or not declared.strip():
        raise ValueError(
            "r0_intake: research_file_path must be a non-empty string "
            "(got {d!r})".format(d=declared)
        )
    if _CONTROL_CHARS_RE.search(declared):
        raise ValueError(
            "r0_intake: research_file_path contains a control character "
            "(NUL or other); refused rather than stripped"
        )
    if ".." in Path(declared).parts:
        raise ValueError(
            "r0_intake: research_file_path {d!r} contains a '..' segment; "
            "declare the path without traversal".format(d=declared)
        )
    # Case-SENSITIVE, deliberately: every downstream dispatcher that acts on this
    # file requires a lowercase `.md` and has no case-insensitive fallback —
    # `_claim_harvest_trigger.py:70` (`_RESEARCH_RE = re.compile(r"_RESEARCH(?:[_-]
    # [A-Za-z0-9]+)?\.md$")`, no `re.I`) and `factcheck-research-file.sh:20`
    # (`*/Thoughts/*_RESEARCH*.md)`, no `nocasematch`) among them. Admitting
    # `x_RESEARCH.MD` here would register a cycle for a file that then receives no
    # fact-check, no register, no harvest and no output-security inspection —
    # silently. `_research_display_slug` folding the extension is a defensive,
    # deliberately-unreachable normalization, not evidence this spelling is
    # supported (see `tests/test_per_file_fc_gate.py:483-486`).
    if not declared.endswith(".md"):
        raise ValueError(
            "r0_intake: research_file_path {d!r} is not a .md file. A run's "
            "findings go in a markdown research artifact; a path that is not one "
            "cannot be the file `r2_research` verifies or the register's "
            "address.".format(d=declared)
        )

    try:
        expanded = Path(declared).expanduser()
    except RuntimeError as exc:
        raise ValueError(
            "r0_intake: research_file_path {d!r} could not be expanded "
            "(home directory could not be determined: {e})".format(
                d=declared, e=exc
            )
        )
    try:
        lexical = Path(os.path.abspath(str(expanded)))
        canonical = expanded.resolve(strict=False)
    except OSError as exc:
        raise ValueError(
            "r0_intake: research_file_path {d!r} could not be resolved "
            "(resolution calls os.getcwd(), which fails when the current "
            "directory has been removed: {e})".format(d=declared, e=exc)
        )

    # Only when resolution actually CHANGED the path was a symlink traversed, and
    # only then is there an escape to check. A plain relative declaration whose
    # target does not exist yet — the common case, and what S6 writes into — takes
    # neither branch.
    if canonical != lexical:
        enclosing = Path(os.path.realpath(str(lexical.parent)))
        if canonical != enclosing and enclosing not in canonical.parents:
            raise ValueError(
                "r0_intake: research_file_path {d!r} resolves through a symlink "
                "to {c}, which is outside its own directory {e}".format(
                    d=declared, c=canonical, e=enclosing
                )
            )

    if canonical.exists() and not canonical.is_file():
        raise ValueError(
            "r0_intake: research_file_path {d!r} exists and is not a regular "
            "file (it resolves to {c})".format(d=declared, c=canonical)
        )

    return str(canonical)


def validate_schema(checkpoint, payload):
    """Validate the payload for a checkpoint against RESEARCH_SCHEMAS.

    Raises ValueError on any violation.

    Mostly pure. The one exception is `validate_research_file_path` at
    `r0_intake`, which touches the filesystem — it has to, because the symlink
    vector is only visible after resolution. Called from here rather than from
    `advance` so that a refusal happens before any checkpoint is written, which is
    what "refused at intake" has to mean: `r0_intake` cannot be called twice for a
    cycle, so a bad path admitted here could never be corrected.
    """
    schema = RESEARCH_SCHEMAS.get(checkpoint)
    if schema is None:
        raise ValueError("Unknown checkpoint: {cp}".format(cp=checkpoint))

    if not isinstance(payload, dict):
        raise ValueError(
            "{cp}: payload must be a JSON object".format(cp=checkpoint)
        )

    for field in schema["required"]:
        if field not in payload:
            raise ValueError(
                "{cp}: missing required field '{f}'".format(
                    cp=checkpoint, f=field
                )
            )

    # r4_factcheck: the skip path has been REMOVED (Lever E). Every research
    # report is fact-checked by the engine (which runs on every _RESEARCH.md
    # write). Reject any legacy skip record — record {"verdict":
    # "engine_running"} instead.
    if checkpoint == "r4_factcheck" and payload.get("skipped"):
        raise ValueError(
            "r4_factcheck: the skip path has been removed — every report is "
            "fact-checked by the engine. Record {'verdict': 'engine_running'} "
            "instead of a skip."
        )

    # r0_intake caller-skill whitelist (A1 / locked Discovery
    # "Programmatic-caller seam"). caller_skill is optional; when present
    # it must be in CALLER_SKILL_WHITELIST.
    if checkpoint == "r0_intake":
        # S5/A5: the declared findings path is validated where it ENTERS, before
        # any checkpoint is written. Required field, so it is always present here.
        validate_research_file_path(payload.get("research_file_path"))
        caller_skill = payload.get("caller_skill")
        if caller_skill is not None and caller_skill not in CALLER_SKILL_WHITELIST:
            raise ValueError(
                "r0_intake: caller_skill {cs!r} not in whitelist. "
                "Allowed: {wl}".format(
                    cs=caller_skill, wl=sorted(CALLER_SKILL_WHITELIST)
                )
            )
        # Caller -> downstream-tool binding (conditional-required). A caller
        # listed in CALLER_DOWNSTREAM_WHITELIST MUST declare a downstream_tool
        # on its allow-list; callers absent from the table are unaffected
        # (non-breaking). Registration-time, self-declared check — catches
        # honest drift to a wrong neighbor tool; NOT a dispatch interceptor.
        # caller_skill absent -> no check (None not in dict), preserving the
        # boundary inherited from the CALLER_SKILL_WHITELIST gate above.
        if caller_skill in CALLER_DOWNSTREAM_WHITELIST:
            allowed = CALLER_DOWNSTREAM_WHITELIST[caller_skill]
            downstream_tool = payload.get("downstream_tool")
            if downstream_tool is None or downstream_tool not in allowed:
                raise ValueError(
                    "r0_intake: caller_skill {cs!r} must declare downstream_tool "
                    "in {al} (got {dt!r}).".format(
                        cs=caller_skill, al=sorted(allowed), dt=downstream_tool
                    )
                )
        # S2 additive flags: user_approved_scope / autonomous_scope are not
        # authorization ON THEIR OWN — MINOR 6 (round-3 fix): reconciled with
        # the header comment above, which is the fuller statement.
        # Authorization is the CONJUNCTION of a whitelisted caller_skill and a
        # validated approval artifact (S4/A4, resolve_approval_artifact
        # below). Reject here when set without a whitelisted caller_skill —
        # there is no second authorization path that could supply the
        # missing half.
        if payload.get("user_approved_scope") is True or payload.get(
            "autonomous_scope"
        ) is True:
            if caller_skill is None or caller_skill not in CALLER_SKILL_WHITELIST:
                raise ValueError(
                    "r0_intake: user_approved_scope/autonomous_scope require "
                    "a whitelisted caller_skill (got {cs!r}). These flags are "
                    "not authorization on their own; authorization is the "
                    "conjunction of a whitelisted caller_skill and a "
                    "validated approval artifact.".format(cs=caller_skill)
                )
        # research-source-adapters S4: the declared-scope record, written by the
        # source picker after the person approves it and read by the containment
        # gate. SHAPE ONLY — schema_version and a list of sources.
        #
        # This deliberately does NOT import `skills/research/scope_record.py` to
        # validate semantically. Doing so would invert the hooks/skills dependency
        # direction this file has never taken (validate_schema checks fields and
        # enums and imports no domain type). Semantic refusal belongs where the
        # record is re-read through `ScopeRecord.from_dict` — the containment
        # check. The cost, stated rather than discovered: a hand-written payload
        # could register a shape-valid but semantically wrong record here; the
        # picker is the only thing that constructs one, and the gate re-reads it.
        scope_record = payload.get("scope_record")
        if scope_record is not None:
            if not isinstance(scope_record, dict):
                raise ValueError(
                    "r0_intake: scope_record must be an object (got {t}).".format(
                        t=type(scope_record).__name__
                    )
                )
            if scope_record.get("schema_version") != 1:
                raise ValueError(
                    "r0_intake: scope_record.schema_version must be 1 (got {v!r}); "
                    "an unrecognised version refuses rather than admits.".format(
                        v=scope_record.get("schema_version")
                    )
                )
            sources = scope_record.get("sources")
            if not isinstance(sources, list):
                raise ValueError(
                    "r0_intake: scope_record.sources must be a list (got {t}).".format(
                        t=type(sources).__name__
                    )
                )


# --------------------------------------------------------------------------- #
# Completion queries
# --------------------------------------------------------------------------- #

def is_complete(state, cycle_id=DEFAULT_CYCLE_ID):
    """True if bypassed or all expected checkpoints are present for cycle_id."""
    if state.get("bypass") is True:
        return True
    cycle = state.get("cycles", {}).get(cycle_id)
    if cycle is None:
        # Legacy single-cycle state with no cycles key (pre-migration).
        seq = _expected_sequence(state.get("topic_slug"))
        checkpoints = state.get("checkpoints", {})
    else:
        seq = _expected_sequence(cycle.get("topic_slug"))
        checkpoints = cycle.get("checkpoints", {})
    return all(cp in checkpoints for cp in seq)


def get_missing(state, cycle_id=DEFAULT_CYCLE_ID):
    """List of expected checkpoints missing from cycle_id, in sequence order."""
    cycle = state.get("cycles", {}).get(cycle_id)
    if cycle is None:
        seq = _expected_sequence(state.get("topic_slug"))
        checkpoints = state.get("checkpoints", {})
    else:
        seq = _expected_sequence(cycle.get("topic_slug"))
        checkpoints = cycle.get("checkpoints", {})
    return [cp for cp in seq if cp not in checkpoints]


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_advance(sid, checkpoint, payload, state_dir=None, cycle_id=DEFAULT_CYCLE_ID):
    state = _read_state(sid, state_dir)

    if state is None:
        if checkpoint == "r0_intake":
            state = _new_state(sid)
        else:
            raise ValueError(
                "No research pipeline state for session {sid}. "
                "First checkpoint must be r0_intake.".format(sid=sid)
            )

    # Ensure version stamps exist (defensive for migrated/older states).
    state.setdefault("schema_version", SCHEMA_VERSION)
    state.setdefault("skill_version", SKILL_VERSION)
    state.setdefault("rules_version", RULES_VERSION)
    state.setdefault("cycles", {})

    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")

    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("cycle_id must be a non-empty string")

    cycle = _get_or_create_cycle(state, cycle_id)

    # S2: a revoked cycle (autonomous-headless abort, S7) cannot accept a
    # fresh r0_intake under the same cycle_id. Remediation is to run
    # `research_pipeline.py reset SID --cycle-id ID`.
    if checkpoint == "r0_intake" and cycle.get("r1_scope_revoked") is True:
        raise ValueError(
            "this research session was aborted earlier and is blocked from "
            "restarting; run `python3 research_pipeline.py reset {sid} "
            "--cycle-id {cid}` to recover".format(sid=sid, cid=cycle_id)
        )

    # Determine topic_slug: cycle value, with payload taking precedence
    # at r0_intake.
    topic_slug = cycle.get("topic_slug")
    if checkpoint == "r0_intake" and payload.get("topic_slug"):
        topic_slug = payload["topic_slug"]

    completed = list(cycle.get("checkpoints", {}).keys())

    validate_sequence(completed, checkpoint, topic_slug)
    validate_schema(checkpoint, payload)

    now = _now()
    cycle.setdefault("checkpoints", {})
    cycle["checkpoints"][checkpoint] = {
        "completed_at": now,
        "data": payload,
    }
    cycle["updated_at"] = now

    if checkpoint == "r0_intake":
        cycle["research_file_path"] = payload.get("research_file_path")
        # S5/A5: the CANONICAL form, recorded additively beside the declared one.
        # `validate_schema` already refused anything unsafe, so this cannot raise;
        # it is stored so the canonicalization is auditable rather than invisible,
        # and so a later reader can tell which file a relative declaration meant
        # without knowing the CWD the run had. The declared value is left verbatim
        # — every downstream reader addresses the path as given.
        cycle["research_file_path_canonical"] = validate_research_file_path(
            payload.get("research_file_path")
        )
        if payload.get("topic_slug"):
            cycle["topic_slug"] = payload["topic_slug"]
        # S4: hoist the approved declaration onto the cycle so the run that
        # follows can read back WHAT WAS APPROVED, beside the r1_scope_approved
        # flag that says it was.
        #
        # Not for a `jq` reader, despite what this comment used to say. The
        # anticipated consumer was `research-scope-gate.sh` parsing the record
        # with `jq`; that hook reads only the approval/revocation flags, and every
        # consumer of the record itself is Python — the shape check below, and
        # `skills/research/declared_read.py` + `source_port.AdmissionPort`, which
        # read its content when the declared sources are actually read.
        #
        # Additive: a payload without one leaves the cycle byte-identical to today.
        if payload.get("scope_record") is not None:
            cycle["scope_record"] = payload["scope_record"]
        # S4/A4: approval is backed by an ARTIFACT, not a caller's name.
        #
        # This branch used to read `if caller_skill is not None: ... = True`, so
        # naming a whitelisted skill WAS the approval (channel 3). It now asks
        # `resolve_approval_artifact` whether the payload CARRIES one, and tags
        # the cycle with which artifact said so. Whitelist enforcement still
        # happens in validate_schema (raises on a non-whitelisted name), so
        # reaching here means caller_skill is absent or whitelisted; the
        # resolver re-checks it because "necessary and never sufficient" is one
        # of its own stated properties, not an assumption about its caller.
        caller_skill = payload.get("caller_skill")
        if caller_skill is not None:
            cycle["caller_skill"] = caller_skill
            cycle["caller_session_id"] = payload.get("caller_session_id")
            # S2: record additive flags so a later reader can tell which
            # coverage decisions were user-approved vs AI-auto-approved.
            # MINOR 6 (round-3 fix): not authorization on their own — this
            # branch is only reached when caller_skill is present, and
            # authorization is the conjunction of THAT whitelisted caller with
            # a validated approval artifact, computed by
            # resolve_approval_artifact below and recorded on
            # cycle["approval_provenance"].
            if payload.get("user_approved_scope") is True:
                cycle["user_approved_scope"] = True
            if payload.get("autonomous_scope") is True:
                cycle["autonomous_scope"] = True

        verdict = resolve_approval_artifact(payload)
        # Tagged on the cycle whichever way it went, so "why is this cycle not
        # approved?" is answerable from the record instead of by re-deriving it.
        cycle["approval_provenance"] = verdict["provenance"]
        cycle["approval_reason"] = verdict["reason"]
        # MINOR 3 (round-4 fix): gated on verdict["provenance"] being one of
        # the two PREDEFINED_SCOPE_PROVENANCE values, not on verdict["approved"]
        # alone. The round-3 "lesser note" fix above gated the write on
        # `approved`, but BOTH the artifact branch and the Autonomous bypass
        # branch return `approved: True` — so a payload carrying a whitelisted
        # caller + `autonomous_scope: True` + a stray `scope_source_ref` (left
        # over from, say, a caller that also set a real artifact field) still
        # wrote that reference onto the cycle, producing a self-contradictory
        # record: `approval_provenance: "autonomous"` +
        # `scope_approval_bypass: True` beside a `scope_source_ref` the
        # autonomous branch of `resolve_approval_artifact` never checked and
        # never admitted. `scope_source_ref` is only meaningful for the two
        # PREDEFINED branches (git-head/explicit-argument) — they are the only
        # ones MINOR 8 requires it to be non-empty for — so gating the write on
        # the verdict's PROVENANCE, not its approved flag, is what keeps the
        # field's presence in lock-step with what actually checked it.
        #
        # Chosen over the alternative (test `user_approved_scope` before
        # `autonomous_scope` in `resolve_approval_artifact`, so a real
        # artifact outranks a route exemption): reordering would change which
        # BRANCH a payload carrying both flags resolves through — a wider
        # behavioural change, affecting `approval_provenance` and the bypass
        # record too — than this record-fidelity fix needs. This fix touches
        # only what gets written alongside an already-correct verdict.
        #
        # MINOR 7 (round-5 fix): the comment above justifies excluding the
        # Autonomous branch; it did NOT justify the other two provenances
        # this same gate excludes, so it under-documented its own scope.
        # Decided and stated here, per-case, rather than widened — a caller
        # supplying `scope_source_ref` on either of these paths is doing so
        # gratuitously (the two PREDEFINED branches are the only ones MINOR 8
        # requires it FOR), and the safer default is to keep the field's
        # presence in lock-step with what `resolve_approval_artifact` actually
        # checked, exactly as the Autonomous case already reasons:
        #   - `fresh-answer` (verdict["provenance"] == "fresh-answer"): a
        #     direct framing exchange has no file to name, so a
        #     `scope_source_ref` here is, like the Autonomous case, a stray
        #     left over from a caller that also set a real predefined-scope
        #     field. Writing it would claim a re-checkable artifact this
        #     provenance cannot back — `git-head`'s own doc comment calls
        #     that property "verifiable after the fact by anyone", which a
        #     `fresh-answer` cycle is not.
        #   - a rejected artifact (verdict["approved"] is False,
        #     verdict["provenance"] is None): `approval_reason` already
        #     records WHY the cycle did not approve; writing an unverified
        #     `scope_source_ref` here would attach a citation to a record
        #     that explicitly did not check it. Since `r0_intake` cannot be
        #     re-called for this cycle (`validate_sequence` raises "Already
        #     completed"), this diagnostic detail is gone for good once
        #     dropped — an accepted, stated cost of not writing an unverified
        #     field, not an oversight.
        #
        # Related, same fix site: this gate only cleans the HOISTED top-level
        # copy. The raw payload is stored verbatim at
        # `cycle["checkpoints"]["r0_intake"]["data"]` regardless of the
        # verdict, so a stray `scope_source_ref` on a `fresh-answer` or
        # rejected payload still sits there beside `autonomous_scope: true`
        # (or whatever else the caller sent) — only the top-level
        # `cycle["scope_source_ref"]` this block writes is gated. That
        # contradiction (a field present one level down, absent at top
        # level) is accepted rather than hidden: `checkpoints[...]["data"]`
        # is the raw-payload audit trail by design (every checkpoint's data
        # is stored there unfiltered), and filtering it would make the audit
        # trail lie about what the caller actually sent.
        if (verdict["provenance"] in PREDEFINED_SCOPE_PROVENANCE
                and payload.get("scope_source_ref") is not None):
            cycle["scope_source_ref"] = payload["scope_source_ref"]
        if verdict["approved"]:
            cycle["r1_scope_approved"] = True
        if verdict["bypass"]:
            # Channel 4: every bypass leaves a record. Per-cycle and named —
            # deliberately NOT the session-level `state["bypass"]`, which
            # disables the pipeline gate wholesale and would be a far wider
            # grant than the Autonomous route asks for.
            #
            # MINOR 9: `verdict["reason"]` is `AUTONOMOUS_BYPASS_REASON`, a
            # compile-time constant — it names the POLICY the exemption rests
            # on, never anything about THIS run, so a record consisting only
            # of that string cannot distinguish a legitimately-chosen
            # Autonomous run from any caller that simply set the flag. The
            # constant stays as the policy half; the fields below are the
            # run-specific evidence half, drawn from data already on this
            # call (the payload, and `now` already computed above) rather
            # than from any new operator prompt.
            cycle["scope_approval_bypass"] = True
            cycle["scope_approval_bypass_reason"] = verdict["reason"]
            cycle["scope_approval_bypass_caller_skill"] = caller_skill
            cycle["scope_approval_bypass_caller_session_id"] = payload.get(
                "caller_session_id"
            )
            cycle["scope_approval_bypass_recorded_at"] = now

        # S6/A6 phase (i): code writes the findings skeleton at the validated
        # path, so the file exists before the first finding. Only for a cycle
        # that APPROVED here — an unapproved cycle may not research at all
        # (`record_finding` refuses it), so a skeleton for it would be a file
        # promising findings that can never be recorded. BEFORE `_write_state`
        # on purpose: if the file cannot be written, nothing is persisted and
        # the intake can be retried, instead of registering a cycle whose
        # findings file cannot exist.
        #
        # MAJOR 6 (FIXER review): moved here — immediately after the approval
        # verdict is applied to the cycle, BEFORE the angles sidecar and the
        # flip audit below — from its earlier position after both. Those two
        # writes are NOT gated on the skeleton succeeding: they used to run
        # first, so a skeleton `OSError` (raised below, which aborts the whole
        # `advance` call before `_write_state`) still left a flip-audit row on
        # disk and possibly an angles sidecar — a real side effect from a call
        # this function's own contract says persists nothing on failure. Skip
        # this and any later step in the same `try`/raise the moment the
        # skeleton write fails, matching "Nothing was registered" literally.
        if cycle.get("r1_scope_approved") is True:
            try:
                skeleton = write_findings_skeleton(
                    cycle["research_file_path_canonical"],
                    payload.get("scope"), state_dir)
            except OSError as exc:
                raise ValueError(
                    "r0_intake: could not write the findings skeleton at {p}: {e}. "
                    "Nothing was registered; fix the location and retry.".format(
                        p=cycle["research_file_path_canonical"], e=exc))
            # No output-security stamp here: the skeleton carries only dated
            # headings and the operator's own approved scope, not material read
            # from a source. The first recorded FINDING stamps the file.
            cycle["findings_skeleton"] = {
                "written": skeleton["written"],
                "reason": skeleton["reason"],
                "at": now,
            }

        # S7/A1: persist the user-approved scope-framing angles to a
        # `<slug>_angles.json` sidecar co-located with the report, so the
        # close-time coverage axis has a durable USER_CONFIRMED checklist.
        # No-op when angles are absent; never clobbers with an empty list;
        # best-effort (never breaks the intake path).
        #
        # MINOR 11: scoped to user_approved_scope AND `verdict["approved"]`.
        # This used to be scoped to the flag alone, on the reasoning that the
        # sidecar "records what the operator confirmed, a fact about the
        # payload [that] stays true whether or not the cycle flipped" — but
        # that is the weaker reading. A payload the resolver just rejected as
        # unverifiable (an unwhitelisted caller, a missing `scope`, a bad
        # `scope_provenance`, or — since MINOR 8 — a predefined artifact with
        # no `scope_source_ref`) is not a confirmed fact about the operator;
        # it is an unverifiable CLAIM about the payload, and stamping it
        # `USER_CONFIRMED` regardless let an unapproved cycle write a
        # checklist the coverage axis then scored the finished report
        # against, at odds with the whole point of the artifact requirement.
        #
        # research-entry-point-enforcement S4 round-6 MINOR 4: `verdict["approved"]`
        # alone is not enough either — `resolve_approval_artifact` checks
        # `autonomous_scope` BEFORE `user_approved_scope` (this module's own
        # docstring above the check, "checked before the scope branch because
        # this route deliberately carries no operator answer to point at"), so
        # a payload carrying BOTH flags resolves through the Autonomous bypass
        # branch — which never validates `scope` at all — and still comes back
        # `approved: True`. Gating on the flag + `approved` alone let that
        # payload stamp a `USER_CONFIRMED` sidecar for a scope nobody
        # validated: the same self-contradiction MINOR 3 (round-4) closed for
        # `scope_source_ref` on this identical branch, at a site MINOR 3
        # missed. `provenance` is the discriminator between the two
        # `approved: True` branches — the artifact branch tags a real
        # `scope_provenance` value; the bypass branch always tags
        # `APPROVAL_AUTONOMOUS` — so excluding that provenance here closes it
        # the same way MINOR 3's `scope_source_ref` write already does.
        if (payload.get("user_approved_scope") is True
                and verdict["approved"]
                and verdict.get("provenance") != APPROVAL_AUTONOMOUS):
            _scope = payload.get("scope") or {}
            _angles = _scope.get("angles") if isinstance(_scope, dict) else None
            _write_angles_sidecar(
                payload.get("research_file_path"), _angles, "USER_CONFIRMED"
            )
        # Optional defense-in-depth audit trail (S2 deliverable h).
        # Still keyed on a named caller — but since S4 a named caller no longer
        # implies a flip, so the verdict is recorded alongside it. An audit row
        # that said only "a whitelisted caller registered" would now describe
        # both the approved and the unapproved case identically.
        if caller_skill is not None:
            _append_flip_audit(
                sid,
                cycle_id,
                caller_skill,
                payload,
                state_dir,
                verdict=verdict,
            )

    state["updated_at"] = now
    # MAJOR 3 revert (post-S4): current_cycle_id is still written on EVERY
    # advance (not just r0_intake), exactly as before — "current" means the
    # cycle being advanced. What changed is what it is FOR. S4/A4 made it the
    # decisive input to `resolve_scope_status`'s approval axis, on the theory
    # that reading only the "current" cycle delivered channel 3's
    # no-inheritance rule. It did not: a session routinely holds several
    # cycles at once (`/work-decode` dispatches one per Part-B row
    # CONCURRENTLY; the multi-language route opens `default`/`de`/`ru`), this
    # field is last-writer-wins, and the session gate has no way to know which
    # cycle a given tool call belongs to — so one unapproved sibling
    # registering blocked every genuinely-approved cycle in the same session
    # (MAJOR 3). `resolve_scope_status` is back to existential approval and no
    # longer consults this field to decide approval. It remains a
    # naming/diagnostic aid only. S4 round-2, ITEM 6 correction: this used to
    # say "cmd_advance/cmd_check use it" — false; `_current_cycle_id(` has
    # exactly ONE call site (grep-verified), inside `resolve_scope_status`'s
    # `none` branch, below. `cmd_advance` (here) writes the
    # `state["current_cycle_id"]` FIELD directly, two lines down, without
    # calling the function; `cmd_check` touches neither the field nor the
    # function. The actual no-inheritance
    # enforcement ("one approved cycle does not approve another") now lives in
    # `resolve_cycle_scope_status(state, cycle_id)`, consulted at the
    # dispatch/read site where the cycle id is actually known. Derived state:
    # a manifest without it still resolves, by the `updated_at` fallback in
    # `_current_cycle_id`.
    state["current_cycle_id"] = cycle_id
    if cycle_id == DEFAULT_CYCLE_ID:
        _sync_legacy_mirror(state)

    _write_state(sid, state, state_dir)

    nxt = _next_checkpoint(cycle["checkpoints"].keys(), cycle.get("topic_slug"))
    result = {
        "ok": True,
        "checkpoint": checkpoint,
        "next": nxt,
        "cycle_id": cycle_id,
        "r1_scope_approved": cycle.get("r1_scope_approved", False),
    }
    print(json.dumps(result))
    return result


def cmd_bypass(sid, reason, state_dir=None):
    # Reject empty AND whitespace-only reasons (A3b — mirrors gate-side
    # whitespace-strip pattern at check-research-pipeline-gate.sh:42-43).
    if reason is None or not reason.strip():
        print("ERROR: bypass reason is empty/whitespace-only.", file=sys.stderr)
        print("Usage: research_pipeline.py bypass <session_id> <non-empty reason>", file=sys.stderr)
        sys.exit(2)
    reason = reason.strip()

    state = _read_state(sid, state_dir)
    if state is None:
        state = _new_state(sid)

    state.setdefault("checkpoints", {})
    state["bypass"] = True
    state["bypass_reason"] = reason
    state["updated_at"] = _now()

    _write_state(sid, state, state_dir)

    result = {"ok": True, "bypass": True, "bypass_reason": reason}
    print(json.dumps(result))
    return result


def cmd_reset(sid, state_dir=None, cycle_id=None):
    """Reset state. Without cycle_id, archives the whole state file.
    With cycle_id, surgically removes that cycle and writes a separate
    archive snapshot of it under archive/.
    """
    path = _state_path(sid, state_dir)

    if cycle_id is None:
        if path.exists():
            archive = _archive_dir(state_dir)
            archive.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            dest = archive / "RP-{sid}-{ts}.json".format(sid=sid, ts=ts)
            os.replace(str(path), str(dest))

        result = {"ok": True, "reset": True}
        print(json.dumps(result))
        return result

    # Per-cycle reset (S2): archive the cycle block to a separate file,
    # remove it from the live state, leave other cycles intact.
    state = _read_state(sid, state_dir)
    if state is None:
        result = {"ok": True, "reset": True, "cycle_id": cycle_id, "noop": True}
        print(json.dumps(result))
        return result

    cycles = state.get("cycles", {})
    if cycle_id not in cycles:
        result = {"ok": True, "reset": True, "cycle_id": cycle_id, "noop": True}
        print(json.dumps(result))
        return result

    archive = _archive_dir(state_dir)
    archive.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = archive / "RP-{sid}-cycle-{cid}-{ts}.json".format(
        sid=sid, cid=cycle_id, ts=ts
    )
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(
            {"session_id": sid, "cycle_id": cycle_id, "cycle": cycles[cycle_id]},
            fh,
            indent=2,
            sort_keys=True,
        )
        fh.write("\n")

    del cycles[cycle_id]
    state["updated_at"] = _now()
    if cycle_id == DEFAULT_CYCLE_ID:
        # Recreate an empty default cycle so the legacy mirror stays sane.
        cycles[DEFAULT_CYCLE_ID] = _new_cycle()
        _sync_legacy_mirror(state)
    _write_state(sid, state, state_dir)

    result = {"ok": True, "reset": True, "cycle_id": cycle_id}
    print(json.dumps(result))
    return result


def cmd_record_non_pass(sid, report, state_dir=None, cycle_id=DEFAULT_CYCLE_ID):
    """Record the Stop-gate non-PASS verdict report on a cycle (S8 consumer).

    `report` is a free-form string describing the verdict surface (e.g. the
    rendered RESEARCH-VERDICT: line) so the cycle carries an audit trail
    of how the user responded after a non-PASS.
    """
    if not isinstance(report, str) or not report.strip():
        print(
            "ERROR: record-non-pass requires a non-empty report string.",
            file=sys.stderr,
        )
        sys.exit(2)

    state = _read_state(sid, state_dir)
    if state is None:
        raise ValueError(
            "No research pipeline state for session {sid}.".format(sid=sid)
        )

    cycle = _get_or_create_cycle(state, cycle_id)
    cycle["non_pass_verdict"] = {
        "report": report.strip(),
        "recorded_at": _now(),
    }
    cycle["updated_at"] = _now()
    state["updated_at"] = _now()
    if cycle_id == DEFAULT_CYCLE_ID:
        _sync_legacy_mirror(state)
    _write_state(sid, state, state_dir)

    result = {"ok": True, "cycle_id": cycle_id, "non_pass_verdict": cycle["non_pass_verdict"]}
    print(json.dumps(result))
    return result


def cmd_revoke_cycle(sid, state_dir=None, cycle_id=DEFAULT_CYCLE_ID):
    """Revoke a cycle's r1_scope_approved by setting r1_scope_revoked (S7 consumer).

    Revocation is additional state, not a transition of r1_scope_approved.
    Authorization remains the whitelist gate. Revoked cycles persist on the
    manifest indefinitely for audit; recovery requires `reset --cycle-id`.
    """
    state = _read_state(sid, state_dir)
    if state is None:
        raise ValueError(
            "No research pipeline state for session {sid}.".format(sid=sid)
        )

    cycle = _get_or_create_cycle(state, cycle_id)
    cycle["r1_scope_revoked"] = True
    cycle["non_pass_abort_ts"] = _now()
    cycle["updated_at"] = _now()
    state["updated_at"] = _now()
    if cycle_id == DEFAULT_CYCLE_ID:
        _sync_legacy_mirror(state)
    _write_state(sid, state, state_dir)

    result = {
        "ok": True,
        "cycle_id": cycle_id,
        "r1_scope_revoked": True,
        "non_pass_abort_ts": cycle["non_pass_abort_ts"],
    }
    print(json.dumps(result))
    return result


# --------------------------------------------------------------------------- #
# S7 — on-non-pass dispatch (autonomous-headless abort)
# --------------------------------------------------------------------------- #

ON_NON_PASS_MODES = frozenset({"accepted", "another-round", "abort"})
_ENV_ON_NON_PASS = "CLAUDE_RESEARCH_ON_NON_PASS"


def resolve_on_non_pass_mode(explicit_flag=None):
    """Return the --on-non-pass mode: explicit flag > env var > 'abort' (headless default).

    The 'abort' default mirrors Anthropic's agentic-safety pattern for headless
    runs — fail-closed rather than silently accepting a non-PASS verdict.

    Raises ValueError on unrecognized mode.
    """
    mode = explicit_flag or os.environ.get(_ENV_ON_NON_PASS) or "abort"
    if mode not in ON_NON_PASS_MODES:
        raise ValueError(
            "Unknown --on-non-pass mode: {mode!r}. "
            "Valid modes: {modes}.".format(
                mode=mode, modes=", ".join(sorted(ON_NON_PASS_MODES))
            )
        )
    return mode


def cmd_dispatch_non_pass(sid, mode, state_dir=None, cycle_id=DEFAULT_CYCLE_ID):
    """Dispatch a non-pass outcome for a headless autonomous run (S7 consumer).

    Modes:
      accepted      -- records the verdict as user-accepted; cycle continues.
      another-round -- records the verdict for re-dispatch; caller must re-invoke.
      abort         -- revokes the cycle (r1_scope_revoked=True) + writes abort ts.

    Revocation is additional state — authorization is a whitelisted
    caller_skill TOGETHER WITH a validated approval artifact
    (`resolve_approval_artifact`), never the whitelist alone (MINOR 7, round-4
    fix: this docstring stated the pre-S4 rule — "authorization remains the
    whitelist gate" — inside the same file whose own header comment
    (`CALLER_SKILL_WHITELIST`, above) retires that exact formulation).
    """
    if mode not in ON_NON_PASS_MODES:
        raise ValueError(
            "Unknown mode: {mode!r}. Valid: {modes}.".format(
                mode=mode, modes=", ".join(sorted(ON_NON_PASS_MODES))
            )
        )
    if mode == "abort":
        cmd_revoke_cycle(sid, state_dir=state_dir, cycle_id=cycle_id)
        result = {
            "ok": True,
            "mode": "abort",
            "cycle_id": cycle_id,
            "action": "cycle revoked",
        }
    else:
        report = (
            "RESEARCH-VERDICT: accepted (autonomous headless run)"
            if mode == "accepted"
            else "RESEARCH-VERDICT: another-round requested (autonomous headless run)"
        )
        cmd_record_non_pass(sid, report, state_dir=state_dir, cycle_id=cycle_id)
        result = {
            "ok": True,
            "mode": mode,
            "cycle_id": cycle_id,
            "action": "non_pass_verdict recorded",
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def _angles_sidecar_path(research_file_path):
    """Return the co-located `<slug>_angles.json` sidecar Path for a report.

    The sidecar lives next to the target report and is keyed off the report's
    stem so it shares the report's slug family. Returns None when no report
    path is available.
    """
    if not research_file_path:
        return None
    rp = Path(research_file_path)
    return rp.with_name(f"{rp.stem}_angles.json")


def _write_angles_sidecar(research_file_path, angles, provenance="USER_CONFIRMED"):
    """A1: atomically persist the user-approved scope-framing angles beside the report.

    Written at the r0_intake scope-approval checkpoint so the coverage axis
    (S7) has a durable, USER_CONFIRMED checklist to score the finished report
    against. Contract:
      - No-op (returns None) when there is no report path or `angles` is falsy/empty.
      - Do NOT clobber an existing sidecar with an empty list (an already-written
        USER_CONFIRMED checklist must never be overwritten by a later empty run).
      - Atomic write (tempfile + os.replace) — never a torn file.
      - Best-effort: any I/O error is swallowed (this is a coverage aid, never a
        behavioral gate on the intake path).
    Returns the sidecar Path on a successful write, else None.
    """
    try:
        path = _angles_sidecar_path(research_file_path)
        if path is None:
            return None
        # Persist only a non-empty, user-approved angle list.
        angle_list = [str(a) for a in (angles or []) if str(a).strip()]
        if not angle_list:
            return None
        payload = {"angles": angle_list, "provenance": provenance}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Use tempfile.mkstemp for unique temporary file with atomic replace.
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True)
                fh.write("\n")
            os.replace(tmp_path, str(path))
        except Exception:
            # Clean up temp file on any error before re-raising.
            try:
                os.close(fd)
            except (OSError, ValueError):
                pass
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return path
    except Exception:
        return None


def _append_flip_audit(sid, cycle_id, caller_skill, payload, state_dir=None,
                       verdict=None):
    """Defense-in-depth: append one JSONL line per intake by a named caller.

    Failure to write is silently swallowed — this is an audit aid, not a
    behavioral gate. No behavioral consequence if the file/dir is missing
    or unwritable.

    `verdict` is the `resolve_approval_artifact` result (S4). It is optional so
    an older caller still writes a readable row, but it is what makes the row
    meaningful now: before S4 reaching this function implied the flip happened,
    so the event needed no outcome field. Since S4 a named caller may register
    WITHOUT flipping, and a row recording only the name would read identically
    in both cases — which is the exact ambiguity the slice removes from the
    cycle record, so it must not survive here.
    """
    try:
        import hashlib

        state_dir_path = _resolve_state_dir(state_dir)
        state_dir_path.mkdir(parents=True, exist_ok=True)
        audit_path = state_dir_path / "flip_audit.jsonl"
        scope = payload.get("scope")
        scope_repr = json.dumps(scope, sort_keys=True) if scope is not None else ""
        scope_hash = hashlib.sha256(scope_repr.encode("utf-8")).hexdigest()[:12]
        entry = {
            "ts": _now(),
            "session_id": sid,
            "cycle_id": cycle_id,
            "caller_skill": caller_skill,
            "user_approved_scope": bool(payload.get("user_approved_scope")),
            "autonomous_scope": bool(payload.get("autonomous_scope")),
            "scope_payload_hash": scope_hash,
            # S4: the outcome, not just the attempt.
            "approved": bool((verdict or {}).get("approved")),
            "approval_provenance": (verdict or {}).get("provenance"),
            "approval_reason": (verdict or {}).get("reason"),
        }
        with open(audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:
        return


def cmd_read(sid, state_dir=None):
    state = _read_state(sid, state_dir)
    if state is None:
        result = {"error": "no state"}
    else:
        result = state
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def cmd_check(sid, state_dir=None, cycle_id=DEFAULT_CYCLE_ID):
    state = _read_state(sid, state_dir)
    if state is None:
        result = {
            "complete": False,
            "missing": list(RESEARCH_SEQUENCE),
            "cycle_id": cycle_id,
            "r4_in_sequence": "r4_factcheck" in RESEARCH_SEQUENCE,
        }
    else:
        _seq = _expected_sequence(state.get("topic_slug"))
        result = {
            "complete": is_complete(state, cycle_id=cycle_id),
            "missing": get_missing(state, cycle_id=cycle_id),
            "cycle_id": cycle_id,
            "r4_in_sequence": "r4_factcheck" in _seq,
        }
    print(json.dumps(result))
    return result


# --------------------------------------------------------------------------- #
# D1 — session-level scope status (the resolver the scope gate consults)
# --------------------------------------------------------------------------- #

def _current_cycle_id(state, cycles):
    """Name the cycle a session is working in — naming/diagnostic only.

    Order: the explicit `current_cycle_id` written by `cmd_advance`; else the
    most recently updated cycle; else `default`. The fallback matters — a
    manifest written before this field existed carries no `current_cycle_id`,
    and must still resolve rather than silently reading as `default` when
    `default` is not the cycle in play. Ties break on the lowest sorted id, so
    two cycles stamped in the same second cannot reorder.

    **MAJOR 3 revert: this function has NO gating role.** It was briefly
    (S4/A4) the decisive input to `resolve_scope_status`'s approval axis —
    that made the session gate strict-per-cycle, which blocked every
    genuinely-approved cycle in a session the moment one sibling cycle went
    unapproved (a session routinely holds several concurrent cycles; see the
    comment at its call site in `cmd_advance`). `resolve_scope_status` is back
    to existential approval and does not call this function to decide
    approval. S4 round-2, ITEM 6 correction: this used to say "its only
    remaining callers are cmd_advance/cmd_check" — false; `_current_cycle_id(`
    has exactly ONE call site (grep-verified): `resolve_scope_status`'s `none`
    branch, which uses it to NAME a cycle in remediation text when nothing is
    approved. `cmd_advance` writes the `state["current_cycle_id"]` FIELD
    directly on every advance (see its comment at the write site) without
    calling this function; `cmd_check` touches neither the field nor the
    function. The real
    no-inheritance predicate — "is THIS cycle approved" — is
    `resolve_cycle_scope_status(state, cycle_id)`, which takes the cycle id
    explicitly and never calls this function at all.

    MINOR 10 (docstring accuracy, carried): this is NOT the tie-break that
    existed before S4. The pre-S4 tie-break ranged only over cycles that
    QUALIFIED (approved or revoked); this fallback ranges over EVERY cycle in
    the manifest, including ones that never approved. It is the right
    fallback for what this function answers ("which cycle is the session
    working in", not "which cycle decides approval") — it is just not the
    same rule as before, and this docstring used to claim it was.
    """
    explicit = state.get("current_cycle_id")
    if isinstance(explicit, str) and explicit in cycles:
        return explicit
    best, best_key = None, None
    for cid, cstate in cycles.items():
        if not isinstance(cstate, dict):
            continue
        # MINOR 4 (round-4 fix): `updated_at` must be forced to a string
        # before it enters the comparison key. The docstring above promises
        # this function never raises — the same totality discipline rounds 2
        # and 3 hardened `resolve_approval_artifact` and
        # `resolve_cycle_scope_status` against — but a manifest carrying a
        # non-string `updated_at` on one cycle (e.g. numeric, from a hand-
        # edited or malformed write) made `key > best_key` compare a `str`
        # against an `int` and raise `TypeError`. `updated_at or ""` only
        # substitutes "" for a FALSY value (None, "", 0) — a non-empty `int`
        # is truthy and passed straight through unguarded. Coercing any
        # non-string to "" keeps every `key` a `(str,)` tuple, so the compare
        # can never raise regardless of what a cycle's `updated_at` holds.
        updated = cstate.get("updated_at")
        if not isinstance(updated, str):
            updated = ""
        key = (updated, )
        # Most recent wins; lowest id breaks a tie. (A prior revision of this
        # comment said "so invert id in the compare" — the code does not
        # invert anything; `cid < best` compares ids directly. MINOR 10.)
        if best is None or key > best_key or (key == best_key and cid < best):
            best, best_key = cid, key
    return best if best is not None else DEFAULT_CYCLE_ID


def resolve_scope_status(state):
    """Decide a session's scope-approval status from a manifest dict.

    Pure and total: takes the parsed manifest (or None) and returns a dict.
    Never reads the filesystem, never mutates `state`, never raises.

    Returns:
        {"decision": "revoked" | "approved" | "none",
         "cycle_id": <the deciding cycle id>,
         "r1_scope_approved": bool,   # the DECIDING cycle's own flag
         "r1_scope_revoked":  bool}   # the DECIDING cycle's own flag

    **MAJOR 3 revert (post-S4) — this is a SESSION-level gate, and it is
    deliberately coarse and permissive.** S4/A4 narrowed the approval axis to
    read only the session's `current_cycle_id`, on the theory that this
    delivered channel 3's "one approved cycle does not approve another". It
    did not: `/work-decode` opens one cycle per Part-B row and explicitly
    dispatches them CONCURRENTLY, and the multi-language route opens
    `default`/`de`/`ru` — a session routinely holds several cycles at once,
    `current_cycle_id` is last-writer-wins, and this resolver has no way to
    know which cycle the tool call it is gating actually belongs to. Reading
    only the "current" cycle meant one unapproved sibling cycle registering
    (or simply being the last one advanced) blocked every genuinely-approved
    cycle in the same session — collateral the locked Guiding Policy never
    asked for (MAJOR 3, independently reproduced).

    So the SESSION gate is back to existential approval, exactly as it was
    before S4: ANY cycle with `r1_scope_approved is True` makes the whole
    session's decision `approved`, and no approved cycle's research is ever
    blocked by an unapproved sibling here. Revocation is unchanged — still
    evaluated first, still existential, still fail-closed (property 1 below).

    Channel 3's actual rule — "approval belongs to the cycle that was
    approved; one approved cycle does not approve another" — is enforced at
    the DISPATCH/READ site instead, where the cycle id performing the read is
    actually known, via the sibling function
    `resolve_cycle_scope_status(state, cycle_id)`. That function answers for
    one named cycle only and never consults a sibling — that
    non-consultation IS the no-inheritance property. This is a deliberate
    two-level model: a coarse, permissive session gate (this function) plus a
    strict, per-cycle predicate (the sibling) enforced only where attribution
    is actually possible. It is a recorded DEVIATION from A4's literal
    wording ("approval is resolved per cycle" — at the session gate); see
    `~/.claude/rules/research-scope-framing.md` for the reason.

    Three properties are load-bearing (D1 / Gaps G5 + G6):

    1. **Revocation is evaluated FIRST, fail-closed.** Any revoked cycle
       decides, even when a sibling cycle is approved. `cmd_revoke_cycle`
       leaves `r1_scope_approved` set ("revocation is additional state, not a
       transition"), so a plain existential over `approved` would let a session
       with `default` revoked + `de` approved through where it is blocked
       today. This ordering makes the rule never more permissive than today on
       the revoked axis.

    2. **The ordering is INTERNAL to this resolver — it does not decide which
       message a caller emits.** That is why the deciding cycle's own
       `approved`/`revoked` flags are returned alongside the decision: the
       scope gate re-evaluates its existing `approved AND revoked` predicate on
       them, unchanged. The pin is the revoked-but-never-approved cycle — it is
       blocked (property 1) and still receives the GENERIC unapproved text,
       because that predicate is false for it exactly as it is today.

    3. **The tie-break is editorial and observable only in a message string.**
       When several cycles qualify (are revoked, or — restored — are
       approved), the lowest sorted id among the qualifying set wins. A wrong
       choice misnames a cycle in remediation text; it cannot mis-gate,
       because the decision itself is existential over the qualifying set
       either way.

    `current_cycle_id` (see `_current_cycle_id`) is NOT an input to the
    approval decision any more. It is consulted only in the `none` branch
    below, to name a cycle in remediation text when NOTHING is approved — a
    naming/diagnostic aid, never a gating one.

    This answers "what is this session's approval status" — a coarse,
    permissive question. It deliberately does NOT answer "which cycle owns
    this file" — that is `_factcheck_engine._resolve_research_cycle_id`,
    keyed on a research file. It also deliberately does NOT answer "is THIS
    cycle approved" — that is `resolve_cycle_scope_status`, keyed on a named
    cycle, and it is the one that actually delivers channel 3's
    no-inheritance rule. All three may exist; none may answer another's
    question.
    """
    fallback = {
        "decision": "none",
        "cycle_id": DEFAULT_CYCLE_ID,
        "r1_scope_approved": False,
        "r1_scope_revoked": False,
    }
    if not isinstance(state, dict):
        return fallback
    cycles = state.get("cycles")
    if not isinstance(cycles, dict):
        return fallback

    # Property 1 — revocation is evaluated FIRST and stays existential across
    # every cycle. Any revoked cycle decides for the session, even when a
    # sibling is approved.
    revoked = [
        cid for cid, cstate in cycles.items()
        if isinstance(cstate, dict) and cstate.get("r1_scope_revoked") is True
    ]
    if revoked:
        cid = sorted(revoked)[0]
        cycle = cycles.get(cid) or {}
        return {
            "decision": "revoked",
            "cycle_id": cid,
            "r1_scope_approved": cycle.get("r1_scope_approved") is True,
            "r1_scope_revoked": True,
        }

    # MAJOR 3 revert — approval is existential again: ANY cycle whose own
    # r1_scope_approved is True approves the whole session. Qualifying set =
    # cycles that are themselves approved; tie-break = lowest sorted id among
    # THAT set (not over every cycle in the manifest — the pre-S4 rule).
    approved_ids = [
        cid for cid, cstate in cycles.items()
        if isinstance(cstate, dict) and cstate.get("r1_scope_approved") is True
    ]
    if approved_ids:
        cid = sorted(approved_ids)[0]
        return {
            "decision": "approved",
            "cycle_id": cid,
            "r1_scope_approved": True,
            "r1_scope_revoked": False,
        }

    # Not approved. Name the current cycle rather than `default`, so
    # remediation text points at the cycle the operator is actually in. This
    # is naming only — current_cycle_id decides nothing here.
    cid = _current_cycle_id(state, cycles)
    return {
        "decision": "none",
        "cycle_id": cid or DEFAULT_CYCLE_ID,
        "r1_scope_approved": False,
        "r1_scope_revoked": False,
    }


def resolve_cycle_scope_status(state, cycle_id):
    """Is THIS cycle approved to read sources? Pure, total, never raises.

    This is the sibling of `resolve_scope_status` that actually delivers
    channel 3's no-inheritance rule: "approval belongs to the cycle that was
    approved — one approved cycle does not approve another." Where the
    session-level `resolve_scope_status` is deliberately coarse and
    permissive (it cannot tell which cycle a tool call belongs to, so it
    reads existentially — see its docstring), THIS function is deliberately
    strict, because the caller here — the dispatch/read site — DOES know
    which cycle it is about to act on and can pass that id in explicitly.

    Answers for the ONE named cycle only:
        - that cycle's own `r1_scope_revoked` is True  -> "revoked"
        - that cycle's own `r1_scope_approved` is not True -> "none"
        - otherwise -> "approved"

    It must NOT, and does NOT, consult any sibling cycle. That
    non-consultation IS the no-inheritance property (MAJOR 3): a cycle with
    no artifact of its own is never approved by a sibling's approval, no
    matter how many other cycles in the same session are approved.

    Returns a dict in the same shape family as `resolve_scope_status`, plus a
    plain-English `reason` a caller can surface directly:
        {"decision": "revoked" | "approved" | "none",
         "cycle_id": <cycle_id, echoed back verbatim>,
         "r1_scope_approved": bool,   # this cycle's own flag
         "r1_scope_revoked":  bool,   # this cycle's own flag
         "reason": str}

    Never raises: a missing manifest, a missing `cycles` map, or an unknown
    cycle id all resolve to `"none"` with an explanatory reason rather than
    raising — mirroring `resolve_scope_status`'s total/pure discipline.
    """
    def _result(decision, approved, revoked_flag, reason):
        return {
            "decision": decision,
            "cycle_id": cycle_id,
            "r1_scope_approved": approved,
            "r1_scope_revoked": revoked_flag,
            "reason": reason,
        }

    if not isinstance(state, dict):
        return _result("none", False, False, "no manifest for this session")
    cycles = state.get("cycles")
    if not isinstance(cycles, dict):
        return _result("none", False, False, "manifest has no cycles")
    # S4 round-2, ITEM 5: `cycles.get(cycle_id)` hashes `cycle_id` — an
    # unhashable value (a list, e.g.) raises TypeError, contradicting this
    # function's own "Pure, total, never raises" docstring promise. This
    # function's sibling `resolve_approval_artifact` guards the identical
    # hazard on `caller_skill` and `scope_provenance` for exactly this reason
    # (see its comments); this is the same fix applied here. It changed from
    # harmless to load-bearing once `declared_read._resolve_scope_from_cycle`
    # started calling this function on the read path (ITEM 2, same round) —
    # a raise here would now take that read path down with it instead of
    # downgrading.
    if not isinstance(cycle_id, str):
        return _result(
            "none", False, False,
            "cycle id must be a string, got {t}".format(
                t=type(cycle_id).__name__),
        )
    cycle = cycles.get(cycle_id)
    if not isinstance(cycle, dict):
        return _result(
            "none", False, False,
            "cycle '{cid}' is not registered in this session".format(cid=cycle_id),
        )

    if cycle.get("r1_scope_revoked") is True:
        return _result(
            "revoked",
            cycle.get("r1_scope_approved") is True,
            True,
            "cycle '{cid}' scope was revoked".format(cid=cycle_id),
        )

    if cycle.get("r1_scope_approved") is not True:
        return _result(
            "none", False, False,
            "cycle '{cid}' has not been approved — no other cycle's approval "
            "carries over to it".format(cid=cycle_id),
        )

    return _result(
        "approved", True, False,
        "cycle '{cid}' is approved".format(cid=cycle_id),
    )


def cmd_scope_status(sid, state_dir=None):
    """CLI wrapper for `resolve_scope_status` — pure read, never mutates.

    A missing or unparseable manifest is not an error: it yields the `none`
    decision on cycle `default`, so a caller falls back to today's behaviour
    rather than seeing a crash. This verb must never raise.
    """
    try:
        state = _read_state(sid, state_dir)
    except (json.JSONDecodeError, OSError, ValueError):
        state = None
    result = resolve_scope_status(state)
    print(json.dumps(result))
    return result


def cmd_cycle_scope_status(sid, cycle_id, state_dir=None):
    """CLI wrapper for `resolve_cycle_scope_status` — pure read, never mutates.

    Mirrors `cmd_scope_status`'s shape and never-raise discipline: a missing
    or unparseable manifest is not an error — it yields the `none` decision
    for the named cycle, so a caller falls back to safe behaviour rather than
    seeing a crash. This is the CLI surface a dispatch/read-site caller uses
    to check ONE named cycle without pulling in the session-level question.
    """
    try:
        state = _read_state(sid, state_dir)
    except (json.JSONDecodeError, OSError, ValueError):
        state = None
    result = resolve_cycle_scope_status(state, cycle_id)
    print(json.dumps(result))
    return result


# --------------------------------------------------------------------------- #
# S6 — findings reach disk as they land (A6 + A7, the finding recorder)
# --------------------------------------------------------------------------- #
#
# research-entry-point-enforcement S6. A research file has three phases and
# three writers (design A6):
#
#   (i)   at `r0_intake`, CODE writes the skeleton — the dated Topics outline and
#         the dated Findings header the methodology's layered output already
#         prescribes (`skills/research/research-subagent.md`, "Layered Output
#         Tracking": `## [Date] — Topics`, `## [Date] — Findings`, …) — so the
#         file exists before the first finding and is openable from the start;
#   (ii)  during the run, every finding is appended by `record_finding` below,
#         which in the SAME act writes one entry to the claims register;
#   (iii) at synthesis / recommendation / Closing, the skill appends the rest as
#         it already prescribes, through the ordinary Write/Edit tools.
#
# **The seam is the parent-orchestrator one, chosen at S6 by the operator
# (2026-09-29) from the three arms S1 priced** (design Assumption 5, "RESOLVED —
# a QUALIFIED yes"): a local MCP server was net-new infrastructure with a
# binding gap, and a per-subagent PostToolUse hook would have left the model as
# the file's writer and filled the register by parsing prose — the harvest shape
# A7 exists to retire. What the chosen arm costs, stated rather than implied: a
# shell-holding run records every finding the moment it lands, but a run that
# delegates to the shell-less adapter records that dispatch's findings only when
# the adapter RETURNS, so a session killed mid-dispatch loses that dispatch's
# findings. The skill dispatches the adapter once per angle to keep that window
# small; it does not close it.
#
# **Payload only (A7).** `record_finding` takes the claim, its source, an
# optional topic and an optional marker kind — never a path, a slug or an area.
# The destination is resolved from the CYCLE (the canonical path `r0_intake`
# validated and recorded) and the register from the ONE addresser
# (`claims_registry.ensure_for_research_file`), so a caller cannot split a
# register or write outside the declared file. A payload naming any other key is
# refused rather than ignored, so a caller who believes it is steering the
# destination learns that it is not.
#
# **What the register row says, and what it deliberately does not.** A row is
# written through the existing Evidence Register machinery (`ClaimLedger` +
# `EvidenceRegister.add`) with the existing three-axis state vocabulary, at
# lifecycle `thought` — "owning work in progress (not yet committed)". That is
# what is TRUE at landing time: nothing has fact-checked the claim yet. A
# `thought`-lifecycle row is not grounding-truth (`ClaimState.is_grounding_truth`),
# so the default consultation set never treats it as established. No status
# value is added, renamed or reinterpreted here — the "claims_registry.py
# implements no claim status" TODO owns that vocabulary, and this slice's guard
# rail forbids touching it.
#
# **The area (Q6) is carried by the row's locator, not by a new column.** The
# register row's locator is `<research file>:<line>`, and the area IS a
# deterministic function of that file's name (`claims_registry.register_area`).
# Adding an area column would change the register's row schema, which belongs to
# the Evidence Register (`_claim_register.py`), not to this slice. The recorder
# returns the area it resolved so a caller can see it.
#
# **Output security.** These appends reach `_RESEARCH.md` through code, so the
# PreToolUse write-seam inspection (which fires on the Write/Edit TOOLS) does not
# see them. Rather than pretend otherwise, every finding append stamps the file's
# EXISTING output-security record "degraded since inspection" through the
# boundary's own `invalidate_on_degraded` — the call that exists for exactly
# "this file has taken content the boundary could not examine". A file with no
# record is left alone: absence already holds promotion ("never inspected").
# Both are monotone towards SAFE: they can only hold promotion, never release
# it, and the next Write/Edit of the file (the synthesis, at the latest)
# re-inspects the whole file and replaces the record. A failure to stamp is
# reported in the result, never swallowed. What is NOT recovered: the write
# seam's ability to REFUSE an instruction-shaped finding before it reaches disk.
# Findings land first and are inspected at the next tool write; the hold keeps
# them from being promoted in between.

FINDING_MARKERS = ("stated", "paraphrased")
RECORD_FINDING_KEYS = frozenset({"claim", "source", "topic", "marker"})
# The Findings header a future checkpoint would look for to verify the
# skeleton kept its promise — that checkpoint is NOT built yet (MINOR 13,
# FIXER review corrected a stale "S7" reference here). One definition, used
# by the skeleton writer and exported so a future verifier and the writer
# cannot disagree on the shape.
# MINOR 12 (round 2): `\r?` before the end anchor so a CRLF file's header
# line ("...Findings\r\n") still matches — `$` in MULTILINE mode anchors
# immediately before the '\n', which sits right after a literal '\r' when the
# text was read with `newline=""` (see `record_finding`'s read, below).
FINDINGS_HEADER_RE = re.compile(r"^## \d{4}-\d{2}-\d{2} — Findings[ \t]*\r?$", re.M)
# A payload value that would break the one-line-per-finding shape or smuggle a
# heading into the file. Refused, not stripped: a silently altered claim is a
# different claim.
#
# MINOR 10 (FIXER review): `str.splitlines()` — which `write_findings_skeleton`
# and the recorder's own line-number math both rely on — also breaks on
# U+2028 LINE SEPARATOR and U+2029 PARAGRAPH SEPARATOR, neither of which the
# `\x00-\x1f\x7f-\x9f` ranges cover (U+0085 NEL is already inside `\x7f-\x9f`
# and was already refused; named explicitly below anyway, since a reader
# checking this regex against the two unsafe-newline forms named in the
# review should not have to work out that the second range already covers
# one of them). A claim carrying either character would still read as "one
# line" to every check here while actually splitting the file's line count
# out from under the register row's `<file>:<line>` locator.
_LINE_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  \u0085]")
# MINOR 10/MINOR 8 (round 2 — retired `_BRACKETED_EM_DASH_RE`): a bracketed
# span containing an em dash caught only the `[stated — url]` SPELLING of a
# citation marker; the harvest's OWN marker grammar (`_claim_harvest.
# _MARKER_RE`) also recognises the ASCII-hyphen form (`[stated-url]`) and the
# bare-kind-token form with no separator at all (`[stated]`, `[My
# assessment]`), neither of which contains an em dash — so a forged marker in
# either shape reached disk unrefused. `_validate_finding_payload` now
# refuses via that SAME grammar, imported rather than duplicated, so there is
# one definition of "marker-shaped" for the harvest to recognise and for this
# recorder to refuse. An ordinary bracketed tag matching no marker kind
# (`[EN]`) still stays accepted.
# The lifecycle status every recorded row carries at landing time. Passed through
# `_claim_state.lifecycle_from_status`, which maps it to `Lifecycle.THOUGHT`.
_LANDING_THOUGHT_STATUS = "in progress"


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _findings_header(day):
    return "## {d} — Findings".format(d=day)


def _scope_outline(scope):
    """The Topics outline from an approved scope: its angles, else its questions.

    Returns plain one-line strings. The scope is the operator-approved object
    `r0_intake` received; nothing is invented when it carries neither list.
    """
    if not isinstance(scope, dict):
        return []
    for key in ("angles", "focused_questions"):
        items = scope.get(key)
        if isinstance(items, list):
            out = []
            for item in items:
                if isinstance(item, str) and item.strip():
                    out.append(" ".join(_LINE_UNSAFE_RE.sub(" ", item).split()))
            if out:
                return out
    return []


class _FindingsLock:
    """An exclusive lock per PATH, kept in the pipeline's state dir.

    Keyed by a hash of the canonical path so two spellings of one file share
    one lock. Since round 2 (MINOR 6), this is used ONLY for the REGISTER
    write — keyed on the register's own path, because two research files in
    one slug family can share one register (MAJOR 4b), so the register needs
    a lock keyed independently of any single research file's. A research
    file's OWN read-modify-write is no longer locked here — see
    `_ResearchFileLock` below for why and what changed.

    *(Corrected 2026-09-30, FIXER item 13: this docstring used to justify the
    state-dir location by claiming a `.lock` sibling in `Thoughts/` "would be
    a stray tracked-looking file in the operator's repository" — a rule this
    codebase does not actually follow. `_claim_ledger.ClaimLedger._id_lock`
    places exactly such a sibling, `<ledger-path>.lock`, beside the ledger in
    `Thoughts/` on every research write. The state-dir location is kept
    because this module already has one (`RP_STATE_DIR`) and every other S6
    lock lives there — not because a `Thoughts/`-sibling lock is disallowed.)*
    """

    def __init__(self, canonical, state_dir=None):
        import hashlib
        d = _resolve_state_dir(state_dir) / "findings_locks"
        d.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()[:24]
        self._path = d / (digest + ".lock")
        self._fh = None

    def __enter__(self):
        import fcntl
        self._fh = open(self._path, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
        return False


class _ResearchFileLock:
    """The research file's OWN lock — the SAME sibling `<file>.lock` flock
    `_factcheck_engine._append_research_frontmatter` takes (MINOR 6, round 2).

    Before this fix, the recorder's whole-file read-modify-write (in
    `write_findings_skeleton` and `record_finding`) held a DIFFERENT lock
    than the engine's own: this one was state-dir-keyed by a hash of the
    canonical path (`_FindingsLock`), while the engine locks a sibling
    `<report>.md.lock` file next to the report itself. Two writers, two
    locks, no mutual exclusion between them — either could observe and
    overwrite the other's half-written state while it held its own lock.
    Locking the EXACT sibling file the engine locks (`path.with_suffix(path.
    suffix + ".lock")` — the identical expression `_append_research_
    frontmatter` uses) makes the two writers mutually exclusive for real.

    Two spellings of one file share this lock only when the CALLER passes
    the same (or a symlink-equivalent) spelling — `with_suffix` is applied to
    whatever path it is given, unlike `_FindingsLock`'s own hash-of-canonical
    keying. Every call site in this module locks the cycle's CANONICAL,
    resolved path, which is exactly what `_append_research_frontmatter`
    locks too, so the two stay aligned.
    """

    def __init__(self, research_path):
        p = Path(research_path)
        self._path = p.with_suffix(p.suffix + ".lock")
        self._fh = None

    def __enter__(self):
        import fcntl
        # `write_findings_skeleton`'s CREATE path locks a file whose parent
        # directory does not exist yet — that mkdir happens INSIDE this lock,
        # deliberately (a race on directory creation is what the lock
        # protects). The lock file's own open() has to survive that ordering,
        # so its parent is ensured here rather than assumed to pre-exist —
        # `_FindingsLock` never needed this because its lock lived in the
        # pipeline's own state dir, always pre-created at import time.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self._path, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        try:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
        finally:
            self._fh.close()
        return False


def _last_h2(text):
    for line in reversed(text.splitlines()):
        if line.startswith("## "):
            return line.rstrip()
    return None


def _declare_written(path):
    """Declare a code-layer write to this session's commit scope (A5 / gap G4).

    Mirrors `claims_registry._record_ledger_write`: lazy, guarded, silent — the
    declaration is bookkeeping for a scoped commit, never a reason to fail.
    """
    try:
        from commit_scope import record_write as _rw
        _rw(str(path))
    except Exception:
        pass


def _stamp_uninspected(canonical):
    """Tell the output-security boundary the file took content it did not examine.

    Returns a short status string for the caller's result. Never raises: a
    boundary that cannot be reached is REPORTED (the result carries it), because
    silently skipping the stamp would leave a stale clean record promoting
    content nothing inspected — the fail-open this call exists to prevent.

    MAJOR 7 (FIXER review, path aliasing): a record is keyed by
    `output_security_record._file_slot`, which hashes the ABSOLUTE spelling of
    whatever path the WRITE SEAM happened to see — not a realpath-resolved
    one. A symlinked spelling of the same file therefore gets its OWN record,
    under its own key, invisible to `read_record(canonical)`. This now also
    walks `all_records()` and invalidates every record whose OWN `file_path`
    resolves (via `os.path.realpath`) to the same file as `canonical`, calling
    `invalidate_on_degraded` with each record's own stored path — never with
    `canonical` on its behalf — so a record filed under a differently-spelled
    alias is stamped in place rather than left stale.

    MAJOR 2 (round 2, revert of MINOR 15's mint): when NO record exists under
    any spelling, this now does NOTHING and returns
    `"no_record_held_by_absence"` — absence already holds promotion (see
    `_claim_harvest_trigger.output_security_hold`'s `never_inspected` branch,
    which reads a missing record exactly the same way). The MINOR 15 mint
    (calling `_osr.invalidate_on_degraded(canon_str)` to write a fresh
    `NEVER_SUCCESSFULLY_INSPECTED` record when none existed) is REVERTED: it
    always mints under the CANONICAL spelling, but the write seam's clearing
    hook (`check-output-security-clear.sh`) files a CLEAR record under
    whatever spelling the TOOL CALL happened to use — routinely the declared,
    uncanonicalized path, not the canonical one this function computes. A
    minted canonical-spelling record is therefore never superseded by that
    later CLEAR, and the file would be named in the session-end report
    forever, even after a clean inspection genuinely ran. Doing nothing here
    costs the never-inspected report line MINOR 15 wanted (a killed
    adapter-mode session's findings are held but not individually named at
    session end); it does not cost availability — the write still lands, and
    absence already holds the file back from promotion.
    """
    try:
        import output_security_record as _osr
    except Exception as exc:  # noqa: BLE001
        return "unstamped: {c}: {e}".format(c=exc.__class__.__name__, e=exc)
    try:
        canon_str = str(canonical)
        canon_real = os.path.realpath(canon_str)
        targets = []
        seen = set()
        for record in _osr.all_records():
            fp = record.get("file_path")
            if not fp or fp in seen:
                continue
            try:
                same = os.path.realpath(str(fp)) == canon_real
            except Exception:  # noqa: BLE001
                same = False
            if same:
                seen.add(fp)
                targets.append(str(fp))
        if canon_str not in seen and _osr.read_record(canon_str) is not None:
            seen.add(canon_str)
            targets.append(canon_str)

        if not targets:
            # No record under any spelling. Do NOT mint one (see the
            # docstring's MAJOR 2 paragraph) — absence already holds.
            return "no_record_held_by_absence"

        results = [_osr.invalidate_on_degraded(t) for t in targets]
        if all(r == results[0] for r in results):
            return results[0]
        return "stamped_mixed: {r}".format(r=results)
    except Exception as exc:  # noqa: BLE001
        return "unstamped: {c}: {e}".format(c=exc.__class__.__name__, e=exc)


def _findings_skeleton_parent_line(path):
    """The `Parent:` line a brand-new research file needs to satisfy G3 (MAJOR 5).

    Computed via `bookkeeping_invariant`'s own classifier + parent-target
    resolution — never re-derived — so a future change to the parenting rule
    (`_parent_target`) reaches this call automatically. Returns "" when no
    in-family parent target exists (a TODO-owned or Mode-C root file, or the
    slug is unclassifiable), matching the "no in-family parent target -> no
    Parent required" case `_parent_target`/G3 already carve out. Best-effort:
    a bookkeeping computation failure must never block a findings write, so
    any exception here degrades to "" rather than propagating.

    G4 (the spine must WIKILINK this child) is deliberately NOT addressed
    here — it is surfaced to the model at the next bookkeeping-invariant
    check with the exact fix, exactly as it was before this change.
    """
    try:
        import bookkeeping_invariant as bi
        member = bi.classify(path.name)
        if not member.slug:
            return ""
        family = bi.read_family(str(path.parent), member.slug)
        target = bi._parent_target(member, family)
        if target is None:
            return ""
        return "Parent: [[{t}]]\n\n".format(t=target.stem)
    except Exception:  # noqa: BLE001 — a bookkeeping failure never blocks the write
        return ""


def write_findings_skeleton(canonical, scope=None, state_dir=None, today=None):
    """Phase (i): write the dated Topics outline + Findings header. Idempotent.

    `state_dir` is accepted but no longer used by this function's OWN lock
    (round 2, MINOR 6): the research file's read-modify-write now takes
    `_ResearchFileLock`, a sibling `<file>.lock` next to the report rather
    than a state-dir-keyed one. The parameter is kept so every existing call
    site's positional/keyword shape stays unchanged.

    Creates the file (and its directory) when absent; APPENDS to an existing
    research file — a second cycle on a report it already wrote is the normal
    path (`validate_research_file_path` admits it for that reason) and nothing
    already on disk is ever rewritten. Skips when the file's last `## ` heading is
    already today's Findings header, so a retried intake does not stack a second
    skeleton. On CREATION only, a `Parent:` line is minted first when a spine
    exists in-family (MAJOR 5) — an existing file's Parent status is left alone.

    Returns {"written": bool, "path": str, "reason": str}. Raises OSError when the
    write itself fails — the intake treats that as a refusal, because a cycle
    whose findings file cannot exist cannot keep the promise it is registering.

    Two known limits (MINOR 12, documented rather than closed here). First, a
    RELATIVE `canonical` is resolved against whatever the process's CWD
    happens to be at call time — this function does not require an absolute
    path, and does not re-derive one — so a caller passing a relative
    `research_file_path` (rather than the cycle's own canonical, absolute
    form) can create the skeleton somewhere other than where the operator
    expects it. Second, a filename not matching the `*_RESEARCH*.md` /
    `*_CLAIMS*.md` shapes the rest of the pipeline recognises (a name this
    function itself does not check) is invisible to the output-security
    write-seam inspection, the convergence fact-check dispatcher, and the
    verified-harvest trigger — none of them pattern-match on it — so findings
    recorded into such a file bypass all three silently.
    """
    day = today or _today()
    header = _findings_header(day)
    path = Path(canonical)
    outline = _scope_outline(scope)
    lines = ["## {d} — Topics".format(d=day), ""]
    lines.extend("- " + item for item in outline)
    if outline:
        lines.append("")
    lines.extend([header, ""])
    block = "\n".join(lines) + "\n"

    with _ResearchFileLock(path):
        existing = None
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if _last_h2(existing) == header:
                return {"written": False, "path": str(path),
                        "reason": "today's Findings header is already the file's "
                                  "last section"}
        path.parent.mkdir(parents=True, exist_ok=True)
        if existing is None:
            parent_line = _findings_skeleton_parent_line(path)
            with open(path, "x", encoding="utf-8") as fh:
                fh.write(parent_line + block)
        else:
            sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(sep + block)
    _declare_written(path)
    return {"written": True, "path": str(path),
            "reason": "created" if existing is None else "appended"}


def _validate_finding_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("record-finding: payload must be a JSON object")
    extra = sorted(set(payload) - RECORD_FINDING_KEYS)
    if extra:
        raise ValueError(
            "record-finding: unexpected field(s) {x}. The recorder takes the "
            "claim, its source, an optional topic and an optional marker — never "
            "a path, slug or area: where a finding lands is decided from the "
            "cycle, not by the caller.".format(x=extra))

    # MINOR 8 (round 2): refuse via the HARVEST's own marker grammar, not a
    # second, narrower regex. See the comment above `_LANDING_THOUGHT_STATUS`
    # for why `_BRACKETED_EM_DASH_RE` was retired.
    from _claim_harvest import _MARKER_RE

    def _text(key, required):
        value = payload.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                "record-finding: '{k}' must be a non-empty string".format(k=key))
        if _LINE_UNSAFE_RE.search(value):
            raise ValueError(
                "record-finding: '{k}' contains a newline or control character; "
                "a finding is one line. Refused rather than altered.".format(k=key))
        if key in ("claim", "topic") and _MARKER_RE.search(value):
            raise ValueError(
                "record-finding: '{k}' contains a marker-shaped bracketed span "
                "(e.g. '[stated — url]', '[stated]', '[My assessment]'); a "
                "forged marker inside the claim text is refused rather than "
                "written verbatim".format(k=key))
        return value.strip()

    claim = _text("claim", True)
    source = _text("source", True)
    topic = _text("topic", False)
    if "]" in source or "[" in source:
        raise ValueError(
            "record-finding: 'source' contains a square bracket, which would end "
            "the citation marker early")
    if topic is not None and topic.startswith("#"):
        raise ValueError(
            "record-finding: 'topic' must not start with '#'; it is written as "
            "the heading's text, not as a heading")
    marker = payload.get("marker", "stated")
    if marker not in FINDING_MARKERS:
        raise ValueError(
            "record-finding: 'marker' must be one of {m}".format(
                m=list(FINDING_MARKERS)))
    return claim, source, topic, marker


def _findings_lang(cycle_id):
    """The claim's language, by the cycle-id convention the framing flow uses.

    `research-scope-framing.md` Step 4: `default` for EN, `de` for DE, `ru` for
    RU. Any other cycle id (a `/work-decode` row, a caller-named cycle) is EN.
    """
    return cycle_id if cycle_id in ("de", "ru") else "en"


def _next_unfenced_heading(text, offset):
    """Index of the next `## ` line at/after `offset`, SKIPPING one that sits
    inside a fenced code block (MINOR 12, round 2).

    A fence toggles on a line whose stripped text starts with ``` or ~~~; an
    unmatched (never-closed) fence is treated as staying open through the
    rest of `text` — the conservative reading, and the one a markdown
    renderer takes too, since there is no closing fence to say otherwise.

    Returns `len(text)` when no un-fenced `## ` line exists at/after `offset`.
    Walked with `splitlines(keepends=True)` rather than a `\n`-only split so a
    CRLF file's `## ` lines are still recognised (each kept line still starts
    with the literal text, `\r\n` and all, unaffected at the START of the
    line) and so the byte offsets this function returns stay exact for
    `record_finding`'s slicing regardless of the file's line ending.
    """
    in_fence = False
    pos = offset
    for line in text[offset:].splitlines(keepends=True):
        stripped = line.strip("\r\n").strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("## "):
            return pos
        pos += len(line)
    return len(text)


def record_finding(sid, payload, state_dir=None, cycle_id=DEFAULT_CYCLE_ID,
                   now=None):
    """Phase (ii): append ONE finding to the cycle's file and ONE register entry.

    The finding recorder (design A7): one landed finding, one append to the
    findings file and one register entry, under code control. Payload only.

    Order inside the act is file first, register second, both under the SAME
    file lock (MAJOR 4b, FIXER review) — plus a second lock keyed on the
    register's own path (see `_FindingsLock`), acquired in a fixed order (file
    lock, then register lock) so two concurrent callers can never deadlock on
    the reverse order. The file comes first because DO-1 — findings on disk
    before anything is built on them — is the property the operator can see;
    if the register half then fails, the result says so explicitly (`ok:
    False`, `finding_on_disk: True`) for `r2_research`'s independent check to
    notice a register that did not keep up — that checkpoint verification is
    a LATER slice, not built yet (MINOR 13, FIXER review corrected a stale
    "S7" reference here). Nothing here claims the two writes are
    transactional; they are sequential, serialized, and reported.

    The finding is inserted at the END OF THE LAST Findings section (MAJOR 3),
    never at end-of-file — a finding recorded after a later `## ... —
    Synthesis` or `## Insecure-input sources` section lands INSIDE the
    Findings section it belongs to, not after it, where a later section
    rewrite could otherwise remove it.

    Refuses (ValueError) when the cycle is not registered, has no declared file,
    is not approved — research on an unapproved cycle is not research this
    pipeline may record (channel 3, per cycle: `resolve_cycle_scope_status`) —
    or when the stored CANONICAL path now resolves through a symlink that
    was not there when the cycle was approved (MINOR 11a / MINOR 7, round 2 —
    a symlink planted at the approved location after approval; re-checked
    against the canonical path, never against the CWD-dependent declared one).
    """
    claim, source, topic, marker = _validate_finding_payload(payload)
    if not isinstance(cycle_id, str) or not cycle_id:
        raise ValueError("record-finding: cycle_id must be a non-empty string")

    state = _read_state(sid, state_dir)
    if state is None:
        raise ValueError(
            "record-finding: no research pipeline state for session {s}; "
            "register the run with r0_intake first".format(s=sid))
    cycle = (state.get("cycles") or {}).get(cycle_id)
    if not isinstance(cycle, dict) or "r0_intake" not in (cycle.get("checkpoints") or {}):
        raise ValueError(
            "record-finding: cycle '{c}' has not been registered with r0_intake "
            "in session {s}".format(c=cycle_id, s=sid))
    approval = resolve_cycle_scope_status(state, cycle_id)
    if approval["decision"] != "approved":
        raise ValueError("record-finding: refused — {r}".format(r=approval["reason"]))

    declared = cycle.get("research_file_path")
    if not declared:
        raise ValueError(
            "record-finding: cycle '{c}' declared no research file".format(c=cycle_id))
    canonical = cycle.get("research_file_path_canonical")
    if canonical is None:
        # Pre-S5 fallback: no canonical was stored at intake; validate the
        # declared path once, exactly as before.
        canonical = validate_research_file_path(declared)
    else:
        # MINOR 11a / MINOR 7 (round 2 — re-seamed onto the STORED CANONICAL
        # path, never the DECLARED one). The declared value is routinely
        # WORKSPACE-RELATIVE, and `validate_research_file_path` resolves a
        # relative path against the process's CURRENT working directory — so
        # re-validating `declared` at record time re-resolved it against
        # whatever CWD the process happened to have NOW, not the CWD it had
        # at intake. A caller whose CWD legitimately differs between the
        # intake call and a later record call (a session's CWD is not
        # guaranteed stable across turns) then saw every finding refused with
        # "resolves differently", for a reason that had nothing to do with
        # safety. What actually needs re-checking is whether the CANONICAL
        # location a symlink could hijack has moved since approval: refuse
        # only when a symlink now sits AT the canonical path itself, or when
        # the canonical path no longer resolves to itself (its own directory
        # became a symlink pointing elsewhere after approval) — CWD plays no
        # part in either check.
        if ((os.path.lexists(canonical) and os.path.islink(canonical))
                or os.path.realpath(canonical) != canonical):
            raise ValueError(
                "record-finding: refused — '{c}' now resolves through a "
                "symlink that was not there when this cycle was approved; a "
                "symlink may have been planted at the approved location "
                "after approval".format(c=canonical))
    path = Path(canonical)

    stamp = now or _now()
    if not path.exists():
        # A cycle registered before S6 has no skeleton. Write it now rather than
        # refuse, so the finding still lands (DO-1). The skeleton writer takes the
        # file's lock itself and is idempotent, so two concurrent first findings
        # write one skeleton between them.
        r0_data = ((cycle.get("checkpoints") or {}).get("r0_intake") or {}).get("data") or {}
        write_findings_skeleton(path, r0_data.get("scope"), state_dir)
    else:
        # MAJOR 3: a file that exists but carries NO Findings header at all (a
        # pre-S6 file, or one hand-edited) needs one before a finding can be
        # spliced into "the last Findings section" below. This must run
        # BEFORE the recorder's own lock a few lines down —
        # `write_findings_skeleton` takes the SAME per-path lock, and
        # `fcntl.flock` does not let one process re-enter it across two
        # separate `open()` calls (it would deadlock the process against
        # itself).
        try:
            _peek = path.read_text(encoding="utf-8")
        except OSError:
            _peek = ""
        if not FINDINGS_HEADER_RE.search(_peek):
            r0_data = ((cycle.get("checkpoints") or {}).get("r0_intake") or {}).get("data") or {}
            write_findings_skeleton(path, r0_data.get("scope"), state_dir)

    finding_line = "- {c} [{m} — {s}]".format(c=claim, m=marker, s=source)

    with _ResearchFileLock(path):
        # MINOR 12 (round 2): read with `newline=""` so a CRLF file's `\r\n`
        # sequences survive VERBATIM in `text` instead of being silently
        # collapsed to `\n` by Python's default universal-newlines text mode
        # — the collapse this function used to inherit, which meant ANY
        # `record_finding` call against a CRLF file rewrote the WHOLE file to
        # LF on its very next save, not merely the inserted lines.
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        matches = list(FINDINGS_HEADER_RE.finditer(text))
        if not matches:
            # The skeleton write above guarantees one exists by now, barring a
            # hostile concurrent editor between the peek and this read — not
            # handled, matching the pre-existing race tolerance around this
            # same lock (e.g. the idempotency check in write_findings_skeleton
            # itself reads outside no lock other than its own).
            raise ValueError(
                "record-finding: {p} has no Findings header to record "
                "into".format(p=path))
        # MINOR 12 (round 2): the file's OWN line ending, detected from what
        # was actually read — `\r\n` if present anywhere, `\n` otherwise. The
        # inserted lines use this, not a hardcoded `\n`, so they are never the
        # one place in an otherwise-CRLF file that mixes endings.
        newline = "\r\n" if "\r\n" in text else "\n"
        last = matches[-1]
        header_line_end = text.find("\n", last.end())
        header_line_end = header_line_end + 1 if header_line_end != -1 else len(text)
        # MINOR 12 (round 2): skip a '## ' line sitting inside a FENCED CODE
        # BLOCK when locating the end of the last Findings section. The old
        # `re.search(r"^## ", ...)` read a heading-SHAPED line quoted inside a
        # fence (e.g. a finding illustrating markdown syntax) as the section
        # boundary, truncating the section early and losing whatever followed
        # the fence but still belonged to it.
        section_end = _next_unfenced_heading(text, header_line_end)

        before = text[:section_end]
        after = text[section_end:]
        # MAJOR 3: the "last topic" scan is scoped to THIS section only — a
        # `### Topic` heading in an earlier Findings section (or in a later,
        # unrelated one) must not suppress a fresh heading here.
        section_content = before[header_line_end:]

        additions = []
        if not before.endswith("\n"):
            additions.append("")          # close the last line first
        if topic is not None:
            last_topic = None
            # MINOR 13 (round 2): split on '\n' ONLY, matching the locator's
            # own line-counting rule below — `str.splitlines()` also breaks
            # on U+2028/U+2029/\v/\f/\x1c-\x1e/\x85, which would scope this
            # scan to a DIFFERENT set of "lines" than the '\n'-only view the
            # recorded line number (and any '\n'-based reader) uses.
            for line in reversed(section_content.split("\n")):
                if line.startswith("### "):
                    last_topic = line[4:].strip()
                    break
                if line.startswith("## "):
                    break
            if last_topic != topic:
                if before.strip():
                    additions.append("")
                additions.append("### " + topic)
        additions.append(finding_line)
        chunk = newline.join(additions) + newline
        new_text = before + chunk + after
        # MINOR 13 (round 2): the recorded line number counts '\n' characters,
        # not `str.splitlines()` "lines" — the latter also breaks on
        # U+2028/U+2029/\v/\f/\x1c-\x1e/\x85, none of which a `\n`-based
        # reader (an editor, `grep -n`, or the register's own `<file>:<line>`
        # locator convention) treats as a line break, so a prior line
        # containing one of those characters used to shift the recorded
        # number away from what such a reader would count. `before + chunk`
        # always ends in a newline (`chunk` always does), so counting '\n'
        # occurrences gives exactly the 1-based line number of the just-
        # appended finding line under a '\n'-only reading — CRLF included,
        # since every "\r\n" still contains exactly one '\n'.
        line_no = (before + chunk).count("\n")

        # MAJOR 3: atomic rewrite — a temp file in the same directory, then
        # os.replace. This is a splice into the middle of the file (not a
        # pure append), so it is rewritten wholesale; every other byte is
        # preserved verbatim.
        tmp_fd, tmp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix="." + path.name + ".", suffix=".tmp")
        try:
            # `newline=""` on the write side too, for the same reason as the
            # read above: `new_text` may already carry literal "\r\n" pairs,
            # and the default text-mode write would otherwise re-translate
            # every '\n' it contains (including the one inside each "\r\n")
            # to `os.linesep` — harmless on this platform (`os.linesep` is
            # already "\n"), but relying on that would make correctness a
            # property of the OS this happens to run on rather than of the
            # code.
            with os.fdopen(tmp_fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(new_text)
            os.replace(tmp_name, str(path))
        except BaseException:
            try:
                os.remove(tmp_name)
            except OSError:
                pass
            raise

        _declare_written(path)
        osec = _stamp_uninspected(path)

        result = {"ok": True, "finding_on_disk": True, "research_file": str(path),
                  "line": line_no, "cycle_id": cycle_id, "output_security": osec}
        # MINOR 8 (round 2 — SUCCESS WHITELIST, not a failure-prefix check):
        # the finding is on disk, but a boundary call that did not cleanly
        # stamp/hold leaves the boundary's memory of this file silently
        # stale, and that is not a success either way it fails. The old check
        # (`osec.startswith("unstamped")`) is a keyword search over
        # `_stamp_uninspected`'s own free-text EXCEPTION branch — it caught
        # only that one wording and read every OTHER failure shape as success:
        # `"invalidation_failed"` (the boundary's own write failed) and a
        # partially-failed multi-alias `"stamped_mixed: [...]"` both started
        # with neither "unstamped" nor anything this check looked for, so a
        # finding whose output-security memory did NOT actually update still
        # came back `ok: True`. The two strings below are the ONLY successes:
        # every targeted record was stamped `"stamped_degraded"`, or there
        # were no targets at all (`"no_record_held_by_absence"` — absence
        # already holds promotion, see `_stamp_uninspected`'s docstring).
        # Anything else — named or not — is a failure.
        if osec not in ("stamped_degraded", "no_record_held_by_absence"):
            result["ok"] = False

        # MAJOR 4b: the register write runs INSIDE this file's lock, under a
        # SECOND lock keyed on the register's own path — two research files
        # in one slug family can share one register.
        try:
            import claims_registry as _cr
            register_addr = _cr.register_address(str(path))
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            result.update({"ok": False, "register_error": "{c}: {e}".format(
                c=exc.__class__.__name__, e=exc)})
            return result

        with _FindingsLock(Path(register_addr.path), state_dir):
            try:
                register = _record_register_entry(path, claim, line_no,
                                                  _findings_lang(cycle_id), stamp)
            except Exception as exc:  # noqa: BLE001 — reported, never swallowed
                result.update({"ok": False, "register_error": "{c}: {e}".format(
                    c=exc.__class__.__name__, e=exc)})
                return result
            result.update(register)
            # MAJOR 2 (round-2 revert): the line-keyed harvest-dedup mark that
            # used to sit here is GONE. It broke the moment the engine's
            # `_append_research_frontmatter` shifted this line's number by
            # prepending frontmatter above the findings (which it does on
            # every fact-checked report) — the harvest re-extracted the same
            # claim under its new line number and re-lifted it as a second,
            # contradictory register row, because the mark was keyed on a
            # line number that no longer matched anything. Dedup against the
            # untouched verified-harvest is now content-keyed instead, via
            # `ClaimLedger.texts_for_source` consulted directly by
            # `_claim_harvest_trigger.on_research_write` — a claim whose text
            # this ledger already holds for the file is never lifted twice,
            # regardless of which line it now sits on. See that function for
            # the fix; this recorder no longer writes harvest-state.json at
            # all (MINOR 9, round 2 — that file remains
            # `_claim_harvest_trigger`'s own dedup sidecar, untouched here).

    return result


def _record_register_entry(research_path, claim_text, line_no, lang, checked_at):
    """One register entry for one recorded finding, through the existing engine.

    The register is addressed by the one addresser and created, when absent, by
    `ensure_for_research_file` — the seam `claims_registry` kept live for this
    slice — so its skeleton carries a `Parent:` line only when a spine exists.
    The ledger lives where the harvest keeps it (`_claim_harvest_trigger.
    _topic_paths`, the one derivation of that path), so the two writers share a
    ledger and a claim id sequence rather than minting colliding ids.

    Rows are appended at the end of this register file, after the legacy
    `_SKELETON` layout's own headings, exactly as the verified-harvest already
    appends there — the two writers share the one on-disk layout; reconciling
    that layout with the recorder's `<file>:<line>` locator shape is a later
    slice (S8), not this one.

    Called under `record_finding`'s SECOND lock (MAJOR 4b, keyed on the
    register's own path) — this function itself takes no lock.
    """
    import claims_registry as _cr
    import _claim_harvest_trigger as _cht
    from _claim_engine import Anchor, Claim, ClaimFlags, ClaimRole, CRITERIA
    from _claim_ledger import ClaimLedger
    from _claim_register import EvidenceRegister
    from _claim_state import build_state

    addr, created = _cr.ensure_for_research_file(str(research_path))
    paths = _cht._topic_paths(Path(research_path))
    if paths is None or Path(paths["register"]) != Path(addr.path):
        raise ValueError(
            "the ledger and the register resolved to different addresses for "
            "{p}".format(p=research_path))
    # The same premise the verified-harvest fast path uses for a cited claim
    # (`_claim_harvest._harvest_one`): a claim carrying its source marker is
    # recorded with the per-criterion flags set. Grounding-truth is decided by
    # the lifecycle axis, which is `thought` here — so this row is never
    # consulted as established until something promotes it.
    flags = ClaimFlags.from_dict({c: True for c in CRITERIA})
    claim = Claim(text=claim_text, anchor=Anchor.local_file(str(research_path), line_no),
                  flags=flags, role=ClaimRole.BACKWARD, lang=lang)
    ledger = ClaimLedger(paths["ledger"])
    cid = ledger.record_extracted(claim, checked_at=checked_at)
    persisted = Claim(text=claim.text, anchor=claim.anchor, flags=claim.flags,
                      role=claim.role, lang=claim.lang, claim_id=cid)
    register = EvidenceRegister(addr.path, addr.slug, ledger=ledger)
    register.add(persisted, build_state(persisted,
                                        thought_status=_LANDING_THOUGHT_STATUS))
    _declare_written(addr.path)
    _declare_written(paths["ledger"])
    return {"claim_id": cid, "register": addr.path, "register_created": created,
            "area": addr.area}


def cmd_record_finding(sid, payload, state_dir=None, cycle_id=DEFAULT_CYCLE_ID):
    result = record_finding(sid, payload, state_dir, cycle_id)
    print(json.dumps(result))
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

USAGE = """\
Usage:
  research_pipeline.py advance          SID CHECKPOINT JSON [--cycle-id ID]
  research_pipeline.py advance          SID CHECKPOINT --payload-file PATH [--cycle-id ID]
    --payload-file reads the JSON payload from PATH instead of the positional
    argument (additive — the positional form keeps working). Use it whenever
    the payload may carry the operator's own words (a scope, prior messages):
    a single-quoted shell literal cannot safely carry an apostrophe, and a
    multi-line command containing "for"/"while"/"done"/"fi"/"esac" as a whole
    word is refused outright by sanitize-bash.sh pattern 9. Write the payload
    with the Write tool, then pass its path here on one line.
  research_pipeline.py record-finding   SID --payload-file PATH [--cycle-id ID]
    S6: append ONE finding to the cycle's declared research file and ONE entry
    to its claims register, in one act. PATH holds a JSON object with only:
    claim (required), source (required — a URL or a citation locator), topic
    (optional heading), marker ("stated" | "paraphrased", default "stated").
    Never a path, slug or area — the destination comes from the cycle. Refused
    for an unregistered or unapproved cycle. Exit 0 = the finding, its
    register entry and its output-security stamp all landed; 3 = the finding
    is on disk but the register entry OR the output-security stamp failed
    (the JSON says which, and why).
  research_pipeline.py bypass           SID "reason"
  research_pipeline.py reset            SID [--cycle-id ID]
  research_pipeline.py read             SID
  research_pipeline.py check            SID [--cycle-id ID]
  research_pipeline.py scope-status     SID
    Pure read. Answers "what is this session's approval status" — a coarse,
    SESSION-level, permissive question: ANY approved cycle approves the whole
    session (MAJOR 3 revert). Returns {decision, cycle_id, r1_scope_approved,
    r1_scope_revoked} where the flags are the DECIDING cycle's own.
    Revocation is evaluated first (fail-closed).
  research_pipeline.py cycle-scope-status SID --cycle-id ID
    Pure read. Answers "is THIS cycle approved to read sources" — a strict,
    per-cycle question that never consults a sibling cycle (the no-inheritance
    property). Use at a dispatch/read site that knows which cycle it is about
    to act on. Returns {decision, cycle_id, r1_scope_approved,
    r1_scope_revoked, reason}.
  research_pipeline.py record-non-pass  SID "report" [--cycle-id ID]
  research_pipeline.py revoke-cycle     SID [--cycle-id ID]
  research_pipeline.py dispatch-non-pass SID [--on-non-pass MODE] [--cycle-id ID]
    MODE: accepted | another-round | abort (default: abort)
    Overridden by CLAUDE_RESEARCH_ON_NON_PASS env var if --on-non-pass absent.
"""


def _pop_flag(argv, flag):
    """Return value of `--flag VALUE` if present, else None. Mutates argv."""
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            value = argv[i + 1]
            del argv[i:i + 2]
            return value
    return None


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)

    if len(argv) < 2:
        sys.stderr.write(USAGE)
        return 2

    cmd = argv[1]

    # state_dir resolution honors RP_STATE_DIR via _resolve_state_dir(None).
    state_dir = None

    # Pop optional --cycle-id flag (used by advance / check); default applied
    # per-command below.
    cycle_id_flag = _pop_flag(argv, "--cycle-id")

    try:
        if cmd == "advance":
            # --payload-file: reads the JSON from a file instead of the
            # positional shell argument (additive; MAJOR 1, round-3 fix). This
            # exists because the positional form forces the operator's own
            # words (a scope, prior messages) through a single-quoted shell
            # literal, and that literal is unsafe on two independent axes: an
            # apostrophe in the text breaks the quoting at execution time, and
            # a multi-line command containing "for"/"while"/"done"/"fi"/
            # "esac" as a whole word is refused outright by
            # sanitize-bash.sh pattern 9 — ordinary in English research prose.
            # A file written by the Write tool sidesteps both: no shell
            # quoting touches the payload at all.
            payload_file_flag = _pop_flag(argv, "--payload-file")
            if payload_file_flag:
                if len(argv) < 4:
                    sys.stderr.write(USAGE)
                    return 2
                sid = argv[2]
                checkpoint = argv[3]
                try:
                    with open(payload_file_flag, "r", encoding="utf-8") as fh:
                        payload = json.load(fh)
                except OSError as exc:
                    print(json.dumps({
                        "ok": False,
                        "error": "--payload-file {p!r}: {e}".format(
                            p=payload_file_flag, e=exc
                        ),
                    }))
                    return 1
            else:
                if len(argv) < 5:
                    sys.stderr.write(USAGE)
                    return 2
                sid = argv[2]
                checkpoint = argv[3]
                payload = json.loads(argv[4])
            cmd_advance(
                sid,
                checkpoint,
                payload,
                state_dir,
                cycle_id=cycle_id_flag or DEFAULT_CYCLE_ID,
            )
            return 0

        if cmd == "record-finding":
            # S6/A7. Payload through a FILE only: a finding is research prose
            # and routinely carries apostrophes and words like "for"/"do" that a
            # shell literal cannot carry safely (same reasoning as advance's
            # --payload-file). There is deliberately no positional-JSON form.
            payload_file_flag = _pop_flag(argv, "--payload-file")
            if len(argv) < 3 or not payload_file_flag:
                print("ERROR: record-finding requires SID and --payload-file PATH.",
                      file=sys.stderr)
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            try:
                with open(payload_file_flag, "r", encoding="utf-8") as fh:
                    payload = json.load(fh)
            except OSError as exc:
                print(json.dumps({"ok": False, "error": "--payload-file {p!r}: {e}".format(
                    p=payload_file_flag, e=exc)}))
                return 1
            # MINOR 9 (FIXER review): a filesystem failure that isn't caught
            # anywhere inside `record_finding` (e.g. the append/splice write
            # itself) used to escape as a raw Python traceback instead of the
            # clean JSON envelope every other failure on this CLI verb gets.
            try:
                result = cmd_record_finding(
                    sid, payload, state_dir,
                    cycle_id=cycle_id_flag or DEFAULT_CYCLE_ID)
            except OSError as exc:
                print(json.dumps({"ok": False, "error": "{c}: {e}".format(
                    c=exc.__class__.__name__, e=exc)}))
                return 1
            # A finding on disk whose register entry (or output-security
            # stamp) failed is NOT a success: exit non-zero so a caller that
            # checks only the status sees it.
            return 0 if result.get("ok") else 3

        if cmd == "bypass":
            # A3b — bypass requires both SID and a non-empty reason argument.
            # The previous default ("no reason given") was the silent-sidestep
            # loophole; cmd_bypass now strips + rejects empty/whitespace too.
            if len(argv) < 4:
                print("ERROR: bypass requires a non-empty reason argument.", file=sys.stderr)
                print("Usage: research_pipeline.py bypass <session_id> <reason>", file=sys.stderr)
                return 2
            sid = argv[2]
            reason = argv[3]
            cmd_bypass(sid, reason, state_dir)
            return 0

        if cmd == "reset":
            if len(argv) < 3:
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            cmd_reset(sid, state_dir, cycle_id=cycle_id_flag)
            return 0

        if cmd == "record-non-pass":
            if len(argv) < 4:
                print(
                    "ERROR: record-non-pass requires SID and a non-empty "
                    "report argument.",
                    file=sys.stderr,
                )
                return 2
            sid = argv[2]
            report = argv[3]
            cmd_record_non_pass(
                sid,
                report,
                state_dir,
                cycle_id=cycle_id_flag or DEFAULT_CYCLE_ID,
            )
            return 0

        if cmd == "revoke-cycle":
            if len(argv) < 3:
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            cmd_revoke_cycle(
                sid,
                state_dir,
                cycle_id=cycle_id_flag or DEFAULT_CYCLE_ID,
            )
            return 0

        if cmd == "dispatch-non-pass":
            if len(argv) < 3:
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            # --on-non-pass flag; fallback: CLAUDE_RESEARCH_ON_NON_PASS env var > "abort".
            on_non_pass_flag = _pop_flag(argv, "--on-non-pass")
            mode = resolve_on_non_pass_mode(on_non_pass_flag)
            cmd_dispatch_non_pass(
                sid, mode, state_dir,
                cycle_id=cycle_id_flag or DEFAULT_CYCLE_ID,
            )
            return 0

        if cmd == "read":
            if len(argv) < 3:
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            cmd_read(sid, state_dir)
            return 0

        if cmd == "check":
            if len(argv) < 3:
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            cmd_check(sid, state_dir, cycle_id=cycle_id_flag or DEFAULT_CYCLE_ID)
            return 0

        if cmd == "scope-status":
            # No --cycle-id: this verb answers a SESSION-level question. A
            # caller that already knew the cycle would not need to ask.
            if len(argv) < 3:
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            cmd_scope_status(sid, state_dir)
            return 0

        if cmd == "cycle-scope-status":
            # --cycle-id is REQUIRED: this verb answers "is THIS cycle
            # approved", so it has nothing to answer without a named cycle.
            if len(argv) < 3 or not cycle_id_flag:
                print(
                    "ERROR: cycle-scope-status requires SID and --cycle-id ID.",
                    file=sys.stderr,
                )
                sys.stderr.write(USAGE)
                return 2
            sid = argv[2]
            cmd_cycle_scope_status(sid, cycle_id_flag, state_dir)
            return 0

        sys.stderr.write("Unknown command: {c}\n".format(c=cmd))
        sys.stderr.write(USAGE)
        return 2

    except ValueError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": "invalid JSON payload: {e}".format(e=exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())

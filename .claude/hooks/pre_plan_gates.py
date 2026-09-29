#!/usr/bin/env python3
"""Pre-plan gates: code-enforced state machine for pre-planning quality.

Session-level state (gates 0-4):
  ~/.claude/session-state/pre-plan-{SESSION_ID}.json

Topic-level state (bypass):
  ~/.claude/state/pre_plan_gates/_active.json
  ~/.claude/state/pre_plan_gates/<topic_slug>__<project_slug>.json

CLI usage (session-level):
  advance SESSION_ID GATE '{"key": "value"}'
  check SESSION_ID
  bypass SESSION_ID "reason"
  reset SESSION_ID
  read SESSION_ID
  check-model-reasons PLAN_FILE

CLI usage (topic-level):
  create-topic SESSION_ID PROJECT_SLUG [TOPIC_SLUG]
              [--intake-source plain|thought] [--todo-line-ref <path>:<line>]
              [--thought-file-path <spine>.md]
              # TOPIC_SLUG is optional ONLY when --thought-file-path is given;
              # with neither, create-topic REFUSES (it does not fall back to the cwd).
  snapshot --validate                       (reads from stdin)
  bypass-topic SESSION_ID "reason"          (state-only; caller writes its own audit if needed)
  set-active SESSION_ID TOPIC_SLUG PROJECT_SLUG
  set-thought-file SESSION_ID PATH
  append-metrics SESSION_ID '{"duration_min": 30, "opus_tokens_k": 12, ...}'
  annotate-session SESSION_ID "<bullet>" [--writer work-done|close]           (Slice J-1: human-facing `## Sessions` log writer; S1: per-writer identity)

CLI usage (handoff prompts):
  resolve-target-file SESSION_ID [FILE_ARG]
  write-next-session-prompt FILE_PATH PROMPT_CONTENT   (A3: sole verified write gate)
  write-next-session-prompt FILE_PATH --from-file PF    (A3: file-input form)
  handoff-freshness FILE                               (A2: FRESH|STALE|ABSENT)
  handoff-verify CONTENT_HASH VERDICT DC_ARTIFACT      (A6: write PASS marker)
  content-hash PROMPT_FILE                             (A4 glue: SHA-256[:12])

CLI usage (phase authority — Slice A / C-ii):
  phase-start SESSION_ID PHASE --auth TOKEN [--artifact LINK] [--todo-text TEXT]
  phase-stop SESSION_ID
  phase-status SESSION_ID
  phase-complete SESSION_ID [--oqs-cleared --auth TOKEN]
  decision-checkpoint SESSION_ID open DESCRIPTION
  decision-checkpoint SESSION_ID resolve --auth TOKEN
  register-session-todo SESSION_ID --todo-text TEXT --bucket BUCKET [--todo-file FILE]

CLI usage (Clarification step state — Slice D):
  clar-advance SESSION_ID STEP_NUM '{payload}' [--from PATH]
  clar-status SESSION_ID
  clar-rewind SESSION_ID --to STEP_NUM
"""

import fcntl
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Session-level state (existing)
STATE_DIR = Path.home() / ".claude" / "session-state"

# Topic-level state (new in S1)
TOPIC_STATE_DIR = Path.home() / ".claude" / "state" / "pre_plan_gates"

# Workflow validation round storage (Plan 6)
WORKFLOW_VALIDATION_DIR = Path.home() / ".claude" / "state" / "workflow_validation"

# Plan + Thought validation round storage (Plan 7)
PLAN_VALIDATION_DIR = Path.home() / ".claude" / "state" / "plan_validation"

# Handoff-prompt verify markers (handoff-prompt-advisory-leak plan, Phase 1).
# A6 writes a content-hash-keyed PASS marker here; A3 (write-next-session-prompt)
# refuses to persist any prompt lacking a matching marker. Same trust shape as
# Gate 3 reading factcheck-convergence.md §7 verdict markers.
HANDOFF_VERIFY_DIR = Path(
    os.environ.get(
        "PPG_HANDOFF_VERIFY_DIR",
        str(Path.home() / ".claude" / "state" / "handoff_verify"),
    )
)

# The handoff section's heading, in ONE place (clarification-v2-cutover S5 / G6).
#
# It was spelled as a literal at the write seam and implied at both read seams,
# which is how the three drifted apart: the writer put its marker INSIDE this
# section while both readers searched the WHOLE file for a marker. On a spine
# that carries a marker of its own outside this section — a v2 spine does, at the
# top of the file — the readers found the wrong one. One constant and one
# scoping predicate (`next_session_prompt_section` below) is the fix; a second
# copy of either is how it would drift again.
NEXT_SESSION_PROMPT_HEADING = "## Next Session Prompt"

# The operator-only Advisory meta-instruction. It must never be persisted into
# `## Next Session Prompt` (handoff-prompt-advisory-leak plan, Defect 1 / C1).
# Matches the distinctive "run /close, then /clear" phrase that is unique to the
# Advisory and would never legitimately appear inside a next-session task prompt.
ADVISORY_RE = re.compile(r"run\s+/close\s*,?\s*then\s+/clear", re.IGNORECASE)

# Projects tree root (single source of truth — A9 will replace literal consumers
# elsewhere in Phase 2). Plans for new topics land under <project_root>/Thoughts/
# where project_root is captured at create-topic time by _resolve_project_root().
#
# PPG_PROJECTS_ROOT is a test seam with three distinct behaviours
# (discovery-lock-topic-state-lookup_PLAN, A2). Without it, the ~20% of
# topic-state records that are relative AND carry no `project_root` cannot be
# exercised by a subprocess test at all.
#
#   unset                      -> the production literal
#   set to a real directory    -> use it
#   set to anything else       -> LOUD on stderr AND unresolvable
#
# The third case never silently falls back to the live root: a harness that set
# the variable but forgot to create the directory would otherwise run its
# assertions against the real corpus and pass for the wrong reason. It also does
# not raise — this module is imported by PreToolUse hooks, where an uncaught
# exception exits 1, which the hook contract treats as NON-blocking (fail-open).
_PROJECTS_ROOT_LITERAL = Path("$CLAUDE_PROJECT_DIR")


def _resolve_projects_root():
    """Resolve PROJECTS_ROOT from the env seam. Returns (path, invalid_flag)."""
    raw = os.environ.get("PPG_PROJECTS_ROOT")
    if raw is None:
        return _PROJECTS_ROOT_LITERAL, False
    candidate = Path(raw)
    if candidate.is_dir() and os.access(str(candidate), os.R_OK | os.X_OK):
        return candidate, False
    print(
        f"[pre_plan_gates] PPG_PROJECTS_ROOT is set to {raw!r}, which is not a "
        f"readable directory. Vault-relative paths are treated as unresolvable; "
        f"nothing falls back to the live vault root.",
        file=sys.stderr,
    )
    # Keep a Path (never None — many consumers interpolate this value), but one
    # that cannot resolve, so callers degrade to "unresolvable" rather than
    # silently addressing real data.
    return candidate, True


PROJECTS_ROOT, PROJECTS_ROOT_ENV_INVALID = _resolve_projects_root()


def _norm_path(p):
    """The ONE path normalisation for every "is this path inside the projects
    tree" test in this module (streamed-dancing-goose S3 / A3): `realpath` +
    `normcase`, returned as a Path.

    Applied at the POINT OF USE to BOTH sides of a containment test — never at
    the constant. `$CLAUDE_PROJECT_DIR` is a symlink to
    `~/repos/Projects` on this machine, so a resolved cwd compared
    against the unresolved literal is outside the tree for every working
    directory; that is how `_resolve_project_root` returned None everywhere and
    `create_topic` stored `project_root: null`. Two other sites in this file had
    the correct idiom inline (`canonical_project_for_spine`,
    `_project_dir_for_spine`) — the same rule, three implementations, one of
    them wrong. All three now call this. Normalising at the constant instead
    would fix nothing for the many tests that assign `PROJECTS_ROOT` directly.

    `_discovery_lock_check._norm` is the same normalisation in another module
    and stays there by the plan's decision (A3). Its own docstring gives the
    reason as "stdlib, not an import", but that hook ALREADY imports ten names
    from this module, so the dependency the reason guards against exists
    regardless — folding `_norm` into this function is a one-line follow-up the
    plan did not make, not a constraint. `test_s2_locked_write_guard.py::
    CopyCount` pins the copy count at ONE `realpath` line here and one there,
    so the second copy cannot multiply unnoticed.
    """
    return Path(os.path.normcase(os.path.realpath(str(p))))

# Phase-state authority constants (Slice A — Walking Skeleton)
# Engine vocabulary aligned with the user-visible `/clarification` skill (rename + state migration shipped 2026-06-13 under plan `cryptic-mapping-torvalds.md`).
PHASE_SEQUENCE = ["thought", "clarification", "planning", "implementation", "closing"]

GATE_SEQUENCE = [
    "gate0_framing",
    "gate1_explore",
    "gate2_validate",
    "gate3_align",
    "gate4_success_metrics",
]

GATE_SCHEMAS = {
    "gate0_framing": {
        "required": ["problem_statement", "todo_item", "todo_file"],
        "optional": ["todo_created"],
    },
    "gate1_explore": {
        "required": [
            "proposed_solution",
            "concerns",
            "kl_sources_checked",
            "workflow_sources_checked",
        ],
    },
    "gate2_validate": {
        "required": ["validated", "revisions", "edge_cases"],
    },
    "gate3_align": {
        "required": [
            "architecture_aligned",
            "problem_statement",
            "guiding_policy",
            "solution_summary",
        ],
    },
    "gate4_success_metrics": {
        "required": ["metrics"],
        "optional": ["omtm", "line_in_sand", "decision_rule", "kl_sources_cited", "reason"],
    },
}

# Slice D follow-up (S-D-Follow-A 2026-05-21): the locked output of
# /clarification is split into three role-aligned regions:
#   IDEA_SECTIONS              — `# Idea` subsections (gated at Step 1b).
#   DISCOVERY_LOCKED_FIELDS    — 4 fields frozen at Step 9; the
#                                 lock-enforcement hook defends them.
#   DISCOVERY_MUTABLE_SUBSECTIONS — writable across all later phases.
IDEA_SECTIONS = ["## Problem"]
DISCOVERY_LOCKED_FIELDS = [
    "## Guiding Policy",
    "## Desired Outcome",
    "## Desired Solution",
    "## Metrics",   # OMTM line inside is the locked sub-field; see validate_discovery_locked_fields
]
DISCOVERY_MUTABLE_SUBSECTIONS = ["## Scope", "## Q&A"]


# ---------------------------------------------------------------------------
# Anchored heading extraction — the ONE predicate (discovery-field-match-anchoring, A1)
# ---------------------------------------------------------------------------
# Every site that asks "where does this heading start, and where does its body
# end" answers through the three functions below. They previously answered it
# with a bare `str.find` reimplemented at seven sites across three modules, which
# matched a heading name ANYWHERE in the text — so a backticked `## Metrics`
# inside `## Q&A` prose, or the `## Guiding Policy` that `### Guiding Policy`
# literally contains at offset 1, was indistinguishable from the real heading.
# That let the Step-9 lock bind to a prose mention and then exit 0 on an edit to
# the field it was defending.
#
# Boundary semantics, chosen against the 83-spine corpus rather than intuition:
#   * START-OF-LINE anchored. A prose mention cannot start a line with `## `,
#     and `### X` cannot match `^## X`.
#   * Terminated by a WHITESPACE BOUNDARY, not end-of-line. Real spines carry
#     decorated headings (`## Metrics 🔒 (Step 8 — LOCKED ...)`, `## Desired
#     Solution — own words (...)`). Measured over the corpus, the intuitive
#     end-anchored form `^field[ \t]*$` newly fails 4 spines and destroys 4
#     hashes to fix 1; this form fails 0 and destroys 0, fixes 1, corrects 4.
#   * A longer name sharing a prefix (`## Metricsomething`) still does not match,
#     because the boundary requires whitespace or end-of-string.
#
# A heading form this predicate rejects (`## Metrics:`) IS treated as absent
# everywhere, the lock site included — corrected 2026-09-18, A3 of
# discovery-field-predicate-coherence.
#
# This comment previously said the opposite, and pointed at
# `_discovery_lock_check._field_status` as the guard enforcing it. Both are gone:
# that guard refused any Discovery-touching edit on a bare `field in body`
# substring — looser evidence than this predicate — and on 2026-08-29 it locked a
# spine's own author out of the only edit that would have repaired the heading,
# while its message prescribed exactly that edit. The decision to remove it, and
# the two defences that make removal safe (a mis-levelled field still fails
# downstream at `_validate-thought-file.py`, which `permission-plan-gate.sh` runs
# live on the spine at ExitPlanMode -> plan-exit block (the `_THOUGHT_check.md`
# sidecar is advisory and no longer on that path, 2026-09-22); an
# in-tool demotion is still refused by the lock's main loop), are recorded in
# `~/.claude/plans/lucky-juggling-whale.md`.
#
# The near-miss is now REPORTED, not refused: `_validate-thought-file.py`'s
# `_near_miss_heading_line` names the offending line under `## Warnings`.
# Leaving this paragraph as it stood would have had the canonical statement of
# the heading rule assert a guard that no longer exists.
_HEADING_RE_CACHE = {}


def heading_re(heading):
    """Compiled start-of-line matcher for one markdown heading (cached)."""
    r = _HEADING_RE_CACHE.get(heading)
    if r is None:
        r = _HEADING_RE_CACHE[heading] = re.compile(
            r"^" + re.escape(heading) + r"(?=\s|$)", re.MULTILINE
        )
    return r


def find_heading(text, heading, start=0):
    """Offset where `heading` occurs as a real markdown heading at or after
    `start`, or -1 — the anchored replacement for `text.find(heading, start)`.

    `start` behaves like `str.find`'s: `^` still matches only at real line
    starts at or after it, never at a mid-line `start` position itself.
    """
    m = heading_re(heading).search(text, start)
    return m.start() if m is not None else -1


def extract_heading_body(text, heading, sibling_headings):
    """Raw body of `heading` — everything after the heading token up to the next
    anchored occurrence of any heading in `sibling_headings`, or end of text.
    Returns None when `heading` is not present as a heading.

    Returned RAW, not stripped: callers differ on whether they strip, and
    preserving that difference is what keeps each caller's contract unchanged.

    The locator and the terminator deliberately use the SAME rule. They are two
    halves of one span, and they fail in opposite directions — a mis-bound
    locator starts the body too early, a spurious terminator ends it too early.
    Anchoring only the locator would start the body correctly and still end it
    at a prose mention.
    """
    idx = find_heading(text, heading)
    if idx == -1:
        return None
    content_start = idx + len(heading)
    next_idx = len(text)
    for other in sibling_headings:
        pos = find_heading(text, other, content_start)
        if pos != -1 and pos < next_idx:
            next_idx = pos
    return text[content_start:next_idx]


def is_effectively_empty(content):
    """A7 (discovery-field-predicate-coherence S2) — the ONE definition of
    "this locked field says nothing", shared by every site that judges emptiness.

    Chrome does not count as content: a body holding only a lock marker, only the
    `<text>` sentinel, or only HTML comments is empty. Everything else is not.

    **This is deliberately a SEPARATE predicate, and must never be folded into
    `extract_heading_body`.** Two things break if it is, and only the first is
    obvious:

      * the guard imports `extract_heading_body` (`_discovery_lock_check.py`) and
        compares bodies for EQUALITY, so stripping chrome inside the extractor
        would make a chrome-only edit invisible to that comparison — the guard
        would stop seeing an edit it is there to see;
      * `_discovery_locked_fields_hash` also calls `extract_heading_body` and
        hashes `content.strip()`. On v1 spines the lock marker sits INSIDE the
        field body, so stripping chrome in the extractor would change every v1
        locked-field hash — invalidating handoff freshness and the plan
        `discovery_src_hash` provenance that three surfaces depend on.

    Holding the two directions apart is a build constraint, not an observation:
    the guard compares content, the checkers judge emptiness, and they stay
    independent only while this predicate lives outside the extractor.

    The regex is NON-GREEDY on purpose. A greedy `<!--.*-->` (or `<!--[\\s\\S]*-->`)
    matches from the FIRST opener to the LAST closer, deleting everything between
    — so a field holding a lock marker, then real prose, then an editorial comment
    would strip to nothing and read empty, turning a populated locked field into a
    refusal. That is the inverse of the bug this fixes, and it would land on
    exactly the v1 spines the hash-stability constraint above protects. Measured
    on that three-part body: greedy strips to `''`, non-greedy keeps the prose.
    A freeze-marker-only fixture reads empty under BOTH forms, which is why it
    alone cannot catch the greedy version.
    """
    if not content:
        return True
    stripped = re.sub(r"<!--[\s\S]*?-->", "", content).strip()
    return not stripped or stripped == "<text>"


def has_metric_line(content):
    """A8 (discovery-field-predicate-coherence S2) — the ONE definition of "this
    Metrics body carries a metric", shared by the two sites that check it.

    Accepts the decorated and separator-bearing forms Q3c ratified — `OMTM:`,
    `- OMTM:`, `**OMTM:**`, `**OMTM.**`, `**OMTM — label:**` — and REQUIRES a
    value after the label. A bare label with nothing substantive after it fails:
    the old test promised "present and non-empty" and only ever checked presence,
    so widening the accepted forms without requiring a value would have widened a
    label-only hole rather than closing it.

    Two callers, one definition, neither carrying a literal — the same
    one-definition-many-sites shape A7 uses. `validate_discovery_locked_fields`
    keeps its copy because `topic_orient` feeds `spine_locked` to three
    non-blocking consumers (`clarification-nudge.sh`, `research-scope-gate.sh`,
    `/work-start`); deleting it outright would silently stop OMTM checking for
    all three, so a spine with `## Metrics` present but no metric would newly read
    `spine_locked=True` there.
    """
    if not content:
        return False
    for line in content.splitlines():
        m = _METRIC_LINE_RE.match(line)
        if m and m.group("value").strip(" \t*_:—-"):
            return True
    return False


# `OMTM` at line start, optionally bulleted and/or bold/italic-decorated, with an
# optional ` — label` before the separator. The separator may be `:` or `.`; the
# value is whatever follows. Decoration is part of the field, not of the name —
# the same principle `heading_re` applies to headings.
_METRIC_LINE_RE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]*)?(?:\*\*|__|\*|_)?[ \t]*OMTM\b[^:.\n]*[:.](?P<value>.*)$"
)


def discovery_section_body(text):
    """The ONE Discovery-section scoping rule, shared by every site.

    Three nominally "Discovery-scoped" sites used to disagree: two scoped via
    `_DISCOVERY_SECTION_RE`, the third via its own `_section_body` with
    different start and end rules, so two tools could answer "where does this
    field end" differently even after the match was fixed. Returns the section
    body, or None when the spine has no `# Discovery` heading.
    """
    m = _DISCOVERY_SECTION_RE.search(text)
    return m.group(1) if m is not None else None


# Step-9 lock marker. v1 emits TWO space-separated tokens (`<uuid> <ts>`);
# clarification-v2 emits THREE (`<section_id> <uuid> <ts>` —
# translating_repository.py:337). The original two-token pattern could not span
# the extra space, so the lock hook returned at its marker gate before examining
# any field, leaving every locked field on every v2-emitted spine editable with
# no refusal at all. Widened to accept 2 OR 3 tokens and nothing more: the
# literal `<!-- locked: ... -->` wrapper is still required, so no prose mention
# can satisfy it, and a v1 marker matches exactly as before. Defined here once
# and imported by both consumers rather than duplicated as two literals.
LOCK_MARKER_RE = re.compile(r"<!-- locked: \S+ \S+(?: \S+)? -->")


# ---------------------------------------------------------------------------
# Handoff-prompt lifecycle helpers (handoff-prompt-advisory-leak plan)
# ---------------------------------------------------------------------------

# Freshness hash scope: the concatenated bodies of the four DISCOVERY_LOCKED_FIELDS
# inside the `# Discovery` section (prompt-for-handoff/SKILL.md step 3 reads
# `# Discovery` on new-spec files, `## Snapshot` on legacy files). The hash covers
# only the locked fields — mutable subsections (## Scope, ## Q&A) do not affect
# handoff freshness. If the set of locked fields changes, update DISCOVERY_LOCKED_FIELDS
# and this function in lockstep. Legacy files without `# Discovery` return None →
# handoff-freshness returns ABSENT (safe: composer rewrites on next handoff).
# Start-of-line anchored (A1): unanchored, `# Discovery\n` also matched at
# offset 1 of `## Discovery\n`, scoping the whole section off a subheading.
# The end bound `\n# ` was already line-anchored and is unchanged.
_DISCOVERY_SECTION_RE = re.compile(
    r"^# Discovery\n(.*?)(?=\n# |\Z)", re.DOTALL | re.MULTILINE
)
# Source-hash marker carries up to three segments (parsed-floating-naur plan, Slice S1):
#   group 1: locked-Discovery hash (12 hex) — always present
#   group 2: phase-progress fingerprint (12 hex | "n/a") — optional (legacy markers omit)
#   group 3: ISO 8601 UTC authoring timestamp — optional (legacy markers omit)
# Backward-compat: legacy 12-hex-only markers still match (groups 2 and 3 are None).
_SRC_HASH_MARKER_RE = re.compile(
    r"<!-- handoff-src-hash: ([0-9a-f]{12})"
    r"(?: phase: ([0-9a-f]{12}|n/a))?"
    r"(?: written: (\S+))?"
    r" -->"
)
_CONTENT_HASH_RE = re.compile(r"^[0-9a-f]{12}$")

# The next `## `-level heading, used to bound the handoff section.
_NEXT_H2_RE = re.compile(r"^## ", re.MULTILINE)


def next_session_prompt_section(text):
    """The body of the `## Next Session Prompt` section, or None when the
    heading is not present AS A HEADING (clarification-v2-cutover S5 / G6).

    The ONE scoping rule both freshness verbs use. Anchored via `find_heading`,
    never `in` or `str.find`: a prose line beginning with this heading exists in
    the corpus, and a substring test binds to it. Bounded by the next anchored
    `## ` heading, matching how the writer lays the section out.

    Returning None rather than "" for an absent heading is load-bearing: the
    callers turn it into ABSENT, which is the safe over-detection direction the
    freshness gate already documents (it triggers compose-and-verify, never a
    silent stale reuse). An empty string would be indistinguishable from a
    section that exists and holds no marker — the same conflation this slice is
    about one level up.

    Behaviour on a v1 spine is unchanged by construction: the write seam has
    always put the marker inside this section, so scoping the read to it finds
    exactly the marker the reader used to find by scanning the whole file.
    """
    idx = find_heading(text, NEXT_SESSION_PROMPT_HEADING)
    if idx == -1:
        return None
    start = idx + len(NEXT_SESSION_PROMPT_HEADING)
    # The heading line must be EXACTLY the heading — nothing after it but the
    # newline (or end of file). This is the same boundary the write seam's
    # replace pattern requires, and it is here because a checker found the two
    # had drifted apart in the fix that was supposed to end drift.
    #
    # `find_heading`'s lookahead is `(?=\s|$)`, which accepts a SPACE. So on a
    # decorated line — `## Next Session Prompt (draft)` — this function used to
    # report a section whose body began mid-line (" (draft)\n\n...") while the
    # write verb refused to treat that same line as the section at all. Read
    # and write disagreeing about where a section starts is a second copy of
    # the body-start rule, which is precisely the "one predicate, not two"
    # failure the header comment above warns about, reappearing one level down.
    #
    # Resolved toward the STRICTER reading, matching the writer: a decorated
    # heading is not this section. Both readers then return ABSENT, which is
    # the safe direction for each (`handoff-freshness` triggers
    # compose-and-verify; `handoff-fresh-since` exits non-zero and fails loud).
    if start < len(text) and text[start] != "\n":
        return None
    nxt = _NEXT_H2_RE.search(text, start)
    return text[start:nxt.start()] if nxt is not None else text[start:]


def handoff_marker_in_section(text):
    """The `handoff-src-hash` marker match from the `## Next Session Prompt`
    section only, or None.

    Both read seams call THIS rather than searching `text` themselves, so
    "which marker counts" has one answer. A file with no such section yields
    None, exactly as a file with no marker does.
    """
    section = next_session_prompt_section(text)
    if section is None:
        return None
    return _SRC_HASH_MARKER_RE.search(section)


def _discovery_locked_fields_hash(text):
    """SHA-256 (first 12 hex) of the concatenated bodies of the four
    DISCOVERY_LOCKED_FIELDS inside the `# Discovery` section, or None if
    the section is absent. Mutable subsections (## Scope, ## Q&A) are not
    included, so freely-evolving Discovery content does not invalidate
    handoff freshness."""
    body = discovery_section_body(text)
    if body is None:
        return None
    parts = []
    all_sections = DISCOVERY_LOCKED_FIELDS + DISCOVERY_MUTABLE_SUBSECTIONS
    for field in DISCOVERY_LOCKED_FIELDS:
        content = extract_heading_body(body, field, all_sections)
        if content is None:
            return None  # missing locked field — refuse to hash
        parts.append(content.strip())
    joined = "\n---\n".join(parts).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:12]


def _phase_progress_fingerprint(text):
    """SHA-256 (first 12 hex) of stable phase/slice progression markers on
    the spine, or None if the spine has no `# Solution Design` section
    (Mode C plan files etc.). Used by the handoff freshness gate to detect
    phase/slice advancement that the locked-Discovery hash misses by design.

    Inputs (deterministic across re-reads):
      (i)  count of `### Solution Alternative N` headings under `# Solution Design`
      (ii) first not-done slice id from the slice register (status not in
           {SHIPPED, NEVER}); empty string if no rows
      (iii) the current `Sessions: M/N done.` counter; empty string if absent
    """
    sd_match = re.search(r"# Solution Design\n(.*?)(?=\n# |\Z)", text, re.DOTALL)
    if sd_match is None:
        return None
    sd_body = sd_match.group(1)
    alt_count = len(re.findall(r"^### Solution Alternative \d+", sd_body, re.MULTILINE))

    first_not_done = ""
    for m in re.finditer(r"<!--\s*L:slice\s+id=(\S+)\s+status=(\S+)\s+", text):
        if m.group(2) not in ("SHIPPED", "NEVER"):
            first_not_done = m.group(1)
            break

    sessions_m = re.search(r"Sessions:\s*(\d+/\d+)\s*done", text)
    sessions = sessions_m.group(1) if sessions_m else ""

    joined = f"{alt_count}\n{first_not_done}\n{sessions}".encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Freshness-gate input contract (project-tracking-staleness S9 / AD-11 / OQ8)
# ---------------------------------------------------------------------------
#
# The handoff freshness gate decides FRESH/STALE from signals computed purely
# from the spine bytes. FreshnessGateInputs is the code-enforced contract that
# names WHICH signals may feed that decision: a strict-equality whitelist of
# bytes-derivable-only inputs. Construction rejects any non-whitelisted name
# (strict ==, never substring — stress case E7), so the gate's input surface
# cannot be silently widened by a future edit wiring in a wall-clock read, a
# mutable counter, or session state (the very drift the gate exists to catch).
#
# Behavior-neutral port boundary: it carries the SAME two live inputs the write
# seam (write-next-session-prompt) and read seam (handoff-freshness) already use
# — the locked-Discovery hash (_discovery_locked_fields_hash) and the
# phase-progress fingerprint (_phase_progress_fingerprint, owned by
# parsed-floating-naur Slice S1; NOT dropped) — now routed through ONE typed
# contract instead of two inline call sites. Sibling of
# _discovery_locked_fields_hash. Adding/removing a permitted input is a
# one-locus change (this frozenset) — Cockburn Evolution Test.
FRESHNESS_GATE_INPUT_WHITELIST = frozenset({
    "discovery_locked_fields_hash",
    "phase_progress_fingerprint",
})


class FreshnessGateInputs:
    """Typed strict-enum whitelist of the freshness gate's permitted inputs.

    Every supplied input name must be in FRESHNESS_GATE_INPUT_WHITELIST by
    strict `==` membership (never substring — stress case E7); a non-whitelisted
    name raises ValueError at construction. Each whitelisted signal is a pure
    function of the spine bytes (bytes-derivable-only) — no wall-clock, no
    external/session state — which is how a non-bytes-derivable signal is
    rejected: its name is not in the whitelist, so it cannot be supplied.

    Behavior-neutral: the two carried values (`discovery_locked_fields_hash`,
    `phase_progress_fingerprint`) are byte-identical to what the seams computed
    inline before this contract existed, including their None returns.
    """

    __slots__ = ("discovery_locked_fields_hash", "phase_progress_fingerprint")

    def __init__(self, **inputs):
        for name in inputs:
            if name not in FRESHNESS_GATE_INPUT_WHITELIST:
                raise ValueError(
                    f"non-whitelisted freshness-gate input: {name!r} "
                    f"(allowed: {sorted(FRESHNESS_GATE_INPUT_WHITELIST)})"
                )
        # A whitelisted input not supplied defaults to None — matching the
        # seams' existing None semantics when a spine section is absent.
        self.discovery_locked_fields_hash = inputs.get("discovery_locked_fields_hash")
        self.phase_progress_fingerprint = inputs.get("phase_progress_fingerprint")

    @classmethod
    def from_spine(cls, text):
        """Compute both whitelisted, bytes-derivable freshness signals from the
        spine text. Behavior-preserving: identical values to the direct
        _discovery_locked_fields_hash(text) / _phase_progress_fingerprint(text)
        calls, including their None returns."""
        return cls(
            discovery_locked_fields_hash=_discovery_locked_fields_hash(text),
            phase_progress_fingerprint=_phase_progress_fingerprint(text),
        )


def _content_hash(prompt_content):
    """SHA-256 (first 12 hex) of the exact prompt bytes — the key A3 and A6
    agree on so the write gate can find the matching verify marker."""
    return hashlib.sha256(prompt_content.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Session-level domain logic (no I/O)
# ---------------------------------------------------------------------------

def validate_sequence(completed_gates, gate):
    """Check that gate is the next expected in GATE_SEQUENCE."""
    idx = GATE_SEQUENCE.index(gate) if gate in GATE_SEQUENCE else -1
    if idx < 0:
        raise ValueError(f"Unknown gate: {gate}. Valid: {GATE_SEQUENCE}")
    expected_idx = len(completed_gates)
    if idx != expected_idx:
        expected = GATE_SEQUENCE[expected_idx] if expected_idx < len(GATE_SEQUENCE) else "all done"
        raise ValueError(
            f"Sequence violation: expected {expected}, got {gate}. "
            f"Completed: {list(completed_gates)}"
        )


def validate_schema(gate, output):
    """Check that output has all required fields for the gate."""
    schema = GATE_SCHEMAS.get(gate)
    if not schema:
        raise ValueError(f"No schema for gate: {gate}")
    missing = [f for f in schema["required"] if f not in output]
    if missing:
        raise ValueError(
            f"Gate {gate} missing required fields: {missing}. "
            f"Required: {schema['required']}"
        )


def next_gate(completed_gates):
    """Return the next gate name or None if all complete."""
    idx = len(completed_gates)
    return GATE_SEQUENCE[idx] if idx < len(GATE_SEQUENCE) else None


def is_complete(state):
    """True if all gates are completed or bypass is set."""
    if state is None:
        return False
    if state.get("bypass"):
        return True
    return len(state.get("gates", {})) == len(GATE_SEQUENCE)


# ---------------------------------------------------------------------------
# Session-level I/O
# ---------------------------------------------------------------------------

def _state_path(session_id):
    return STATE_DIR / f"pre-plan-{session_id}.json"


def read_state(session_id):
    """Read session state file. Returns None if not found."""
    path = _state_path(session_id)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(session_id, state):
    """Write session state file atomically."""
    path = _state_path(session_id)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    tmp.rename(path)


def delete_state(session_id):
    """Delete session state file if it exists."""
    path = _state_path(session_id)
    if path.exists():
        path.unlink()


# ---------------------------------------------------------------------------
# Topic-level I/O
# ---------------------------------------------------------------------------

def _active_path():
    return TOPIC_STATE_DIR / "_active.json"


def _topic_path(*, topic_slug, project_slug):
    return TOPIC_STATE_DIR / f"{topic_slug}__{project_slug}.json"


def _read_json(path):
    """Read JSON file. Returns None if not found."""
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path, data):
    """Write JSON file atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.rename(path)


def read_active():
    """Read _active.json. Returns empty dict if not found."""
    return _read_json(_active_path()) or {}


def write_active(data):
    """Write _active.json atomically.

    Atomic PER WRITE (tmp + rename) — which is NOT the same as safe under
    concurrency, and the difference is why `_active_lock()` below exists.
    `_write_json` uses a SHARED tmp path (`_active.tmp`) for every writer, so two
    concurrent writers can interleave their `write_text` on that one tmp file and
    rename a torn result into place. Even with per-writer tmp files the file is
    last-writer-wins, so two interleaved read-modify-writes still discard the
    earlier insert. Hold `_active_lock()` across the WHOLE read-modify-write.
    """
    _write_json(_active_path(), data)


def _active_lock():
    """THE lock for every `_active.json` read-modify-write. Single locus.

    Obligation 11 (executor obligations, Cluster-B plan) requires that every
    `_active.json` acquisition resolve to the SAME lock. Routing all four writer
    regions through this one helper is what makes that structural rather than a
    convention four call sites could drift from.

    KEYED ON `_active_path()` — deliberately, and never on a Projects-side
    target. `auto_register_topic` holds two `bookkeeping_lock(todo_target)`
    blocks keyed on the *Projects* repo, and one of them closes two lines above
    its `_active.json` read-modify-write. Extending that block downward is the
    obvious-looking edit and is WRONG: it would split the writer set across two
    different locks, leaving `prune-active`'s harness-side flock serializing
    against nothing.

    Two properties a caller must know, both verified against the shipped
    primitive rather than assumed:

    * **Cross-PROCESS only.** `bookkeeping_lock`'s `_HELD` registry is a
      process-global dict keyed on lock path with no thread identity, and a
      second acquisition of the same path in one process takes a re-entrant
      refcount fast path that acquires no `flock`. Two THREADS in one process
      therefore do not serialize at all. Production is unaffected — `/work-start`
      and `/close` are separate interpreter invocations — but any test that means
      to prove interleaving MUST drive it through subprocesses.

    * **This resolves to the harness-wide repo lock.** `~/.claude` is a git repo,
      and `lock_path_for` prefers the shared git dir whenever one exists, so this
      returns `~/.claude/.git/bookkeeping.lock` — the same lock the promotion
      adapter holds across a the deploy step. A `/work-start` or `/close` during
      an active promotion will therefore block and then raise
      `BookkeepingLockTimeout`. That is ACCEPTED and correct (operator decision,
      2026-08-17): it surfaces to the operator instead of proceeding unlocked.
      Narrowing `_active.json` to a per-file sidecar would mean changing
      `lock_path_for` — a new locking design, outside this plan's carve-out.

    The `sys.path` guard is NOT optional and is not defensive habit. This module
    is loaded by PATH (`importlib.util.spec_from_file_location`) by its own test
    suite, which puts no directory on `sys.path` — so a bare
    `from bookkeeping_lock import ...` raises `ModuleNotFoundError` there and
    every `create_topic`/auto-bind test dies inside this helper. Running as a
    script hides it, because `sys.path[0]` is then already the hooks dir. The
    guard is the module's own existing convention for a sibling import (see the
    identical `sys.path.insert(0, str(Path(__file__).parent))` before the
    by-path helper imports later in this file), so it introduces no new pattern.
    """
    hooks_dir = str(Path(__file__).resolve().parent)
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    from bookkeeping_lock import bookkeeping_lock
    return bookkeeping_lock(_active_path())


def _resolve_topic(session_id):
    """Resolve session_id → (topic_slug, project_slug, state). Returns (None, None, None) if no active topic."""
    active = read_active()
    session_data = active.get(session_id)
    if not session_data:
        return None, None, None
    topic_slug = session_data.get("topic_slug")
    project_slug = session_data.get("active_project")
    if not topic_slug or not project_slug:
        return None, None, None
    state = _read_json(_topic_path(topic_slug=topic_slug, project_slug=project_slug))
    return topic_slug, project_slug, state


def _write_topic_state(*, topic_slug, project_slug, state):
    """Write topic state file atomically."""
    _write_json(_topic_path(topic_slug=topic_slug, project_slug=project_slug), state)


def _archive_timestamp():
    """The `.bak-<ts>` stamp, as its own function so a test can pin it.

    DECLARED EXTENSION beyond A4's literal text, kept because the collision loop
    in `_archive_topic_state` is untestable without a way to force two archives
    onto one stamp. It adds no behaviour of its own.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _archive_topic_state(state_path):
    """S3/A4 — move a topic-state record aside instead of deleting it.

    Replaces the `unlink` the phase_start/phase_stop rollback used to reach when
    there was no prior state to restore (`~/.claude/rules/safe-defaults.md`: move
    aside, never destroy). Resolves `_reaped/` against TOPIC_STATE_DIR, never the
    cwd, and binds the timestamp immediately before the rename.

    ONE transaction covering mkdir-then-rename. Recovery is best-effort by
    definition and must never become the thing that hides the fault: the caller
    runs inside an `except` ending in a bare `raise`, so an OSError escaping here
    would propagate INSTEAD of the original sync failure. Any OSError therefore
    abandons the attempt with the record untouched and returns None.

    Returns the archive Path on success; None when there was nothing to archive
    or the attempt was abandoned.

    Two extensions/deviations beyond A4's literal wording, both disclosed to the
    same standard:

    1. A4 says `mkdir` happens "first"; an absent record returns BEFORE it, so a
       no-op recovery does not create an empty `_reaped/`.
    2. A4 says the move goes to `*.bak-<ts>`; the collision loop below may append
       `.<n>` when that exact name is taken. `<ts>` is second-granular, so two
       archives of one record inside one second would otherwise `rename` over the
       first — destroying a backup, which is the harm this helper replaces.

    A third divergence, disclosed because this docstring claims to disclose them:
    the `mkdir` below is inlined although the module ships `_prep_reaped()`, whose
    own docstring says re-implementing it locally is what "never parallel logic"
    forbids. A4 prescribes the inline `mkdir` verbatim, so the plan is followed
    here — but `_prep_reaped` would have been drop-in (it raises `PermissionError`,
    an `OSError`), and a future consolidation should prefer it.

    Only `OSError` is absorbed — A4's wording. `exists`/`mkdir`/`rename` raise
    `OSError` subclasses, so in production that covers the block; it is NOT a
    closed set, because the block dispatches through `_archive_timestamp`, a
    module-global a test can replace. Anything else still propagates and would
    mask the sync failure. The sibling `_restore_topic_state` absorbs the same
    and no more; see its docstring for why a briefly-widened catch was reverted.
    """
    try:
        if not state_path.exists():
            return None
        reaped = TOPIC_STATE_DIR / "_reaped"
        reaped.mkdir(parents=True, exist_ok=True)
        stamp = _archive_timestamp()
        dest = reaped / f"{state_path.name}.bak-{stamp}"
        # `.bak-<ts>` is second-granular, so two rollbacks of one record inside
        # the same second collide — and `rename` onto an existing path would
        # destroy the earlier archive, which is the exact harm this replaces.
        n = 1
        while dest.exists():
            dest = reaped / f"{state_path.name}.bak-{stamp}.{n}"
            n += 1
        state_path.rename(dest)
    except OSError as exc:
        print(
            f"[phase-rollback] archival recovery abandoned for {state_path}: {exc}",
            file=sys.stderr,
        )
        return None
    return dest


def _restore_topic_state(state_path, prev_state_bytes):
    """S3/A4 — restore a topic-state record from its pre-write bytes.

    ONE transaction covering write-then-rename, never a handler per call. If
    `write_bytes` fails (e.g. ENOSPC) and that error were swallowed per-call,
    control would fall through to `rename` and OVERWRITE the real record with a
    truncated temp file — destroying the state the rollback exists to preserve
    and doing strictly more harm than the `unlink` this pair replaces. So on any
    OSError the rename is not attempted, the temp file is left unpromoted, and
    the original exception is left to propagate.

    Returns True when the record was restored, False when the attempt was
    abandoned.

    Only `OSError` is absorbed — A4's wording ("on ANY OSError"), deliberately
    NOT widened. `Path.with_suffix` raises `ValueError` rather than `OSError`,
    and an earlier revision caught `ValueError` too on that basis. That was the
    wrong instrument: the raise is unreachable for every caller here (the suffix
    is a valid literal, and `state_path` always comes from `_topic_path`, whose
    name is `f"{a}__{b}.json"` and so never empty), and the module's own
    `_write_json` makes an unguarded `with_suffix` call of the same shape (a
    different suffix, `".tmp"`, but the same exposure). Widening cost more
    than it bought — it would silently demote any future `ValueError` in this
    block from a programming error to an abandoned recovery. So the hazard is
    described here rather than caught: anything that is not an `OSError` still
    propagates, and would mask the sync failure if it ever occurred.
    """
    try:
        tmp = state_path.with_suffix(".rollback.tmp")
        tmp.write_bytes(prev_state_bytes)
        tmp.rename(state_path)
    except OSError as exc:
        print(
            f"[phase-rollback] rollback recovery abandoned for {state_path}: {exc}",
            file=sys.stderr,
        )
        return False
    return True


def _resolve_project_root(cwd: Optional[Path] = None) -> Optional[Path]:
    """Walk up from cwd to nearest leaf-project marker (TODO.md).

    Three deterministic outcomes:
    - cwd inside a leaf project   → return that leaf's absolute path
    - cwd inside projects tree
      but no leaf TODO.md ancestor → return PROJECTS_ROOT (cross-cutting topic)
    - cwd outside PROJECTS_ROOT   → return None (caller routes to ~/.claude/plans/)

    Stops walking at PROJECTS_ROOT or filesystem root, whichever comes first.
    User-stated structural property (2026-05-11): only leaf folders are real
    projects; each leaf carries a TODO.md.
    """
    cwd = _norm_path(cwd or Path.cwd())
    # S2/A1: inside a linked worktree, resolve on main's checkout via the ONE
    # main-pinned resolver, so spine / project-root lookups (callers below at
    # ~2413/2502) target the one shared copy — TODO.md markers are
    # sparse-excluded from worktrees (A3), so a raw worktree walk would miss
    # them. Plan-authoring placement is deliberately unaffected: the caller at
    # ~505 still nulls project_root in a worktree to route the plan to
    # ~/.claude/plans. Behavior-preserving OUTSIDE a worktree (main == cwd's
    # own toplevel there, so the mapping is a no-op).
    try:
        import bookkeeping_resolver
        if bookkeeping_resolver.in_worktree(cwd):
            _main = bookkeeping_resolver.main_checkout(cwd)
            _wt = bookkeeping_resolver.current_worktree_root(cwd)
            if _main is not None and _wt is not None and _main != _wt:
                try:
                    cwd = _norm_path(_main / cwd.relative_to(_wt))
                except (ValueError, OSError):
                    cwd = _norm_path(_main)
    except Exception:
        pass
    # S3 / A3: BOTH sides normalised through the one function. This test used to
    # compare a resolved cwd against the unresolved `PROJECTS_ROOT` literal — a
    # symlink on this machine — and so returned None for every real cwd. The
    # outside-the-root answer stays `None` (this caller's own contract).
    root = _norm_path(PROJECTS_ROOT)
    try:
        cwd.relative_to(root)
    except ValueError:
        return None
    # Delegate the Root/leaf walk to the single shared locus (S5 convergence —
    # session-topic-identity-coherence plan, Cockburn Evolution Test). Both
    # arguments are normalised, which is the walk's stated precondition.
    return _nearest_leaf_or_root(cwd, root)


def _in_worktree(cwd: Optional[Path] = None) -> bool:
    """True iff cwd is inside a linked git worktree (not the primary tree).

    Thin wrapper (git-working-model S1 / A2): the `--git-common-dir` vs
    `--git-dir` detection logic that used to be inlined here now lives in the
    ONE canonical shell primitive ``worktree-detect.sh`` (deployed alongside
    this file in the hooks dir). This wrapper delegates to that same primitive
    the A3 pre-commit gate calls directly — so there is exactly one detector
    reached from both Python and the git-hook, with no second copy to drift.

    The primitive's exit contract: 0 = in worktree, 1 = not, 2 = detection
    error. Matching the former behaviour, anything other than a clean 0 (incl.
    the timeout / missing-git error paths) returns False. Same discipline as
    before — subprocess with timeout=2, exceptions swallowed.

    Callers that must NOT inherit that fail-open default use `_worktree_status`
    (S3 / A3): a detection failure there is `None`, not "not a worktree".
    """
    return _worktree_status(cwd) is True


def _worktree_status(cwd: Optional[Path] = None):
    """Tri-state worktree detection: True (linked worktree), False (primary
    tree), or None (the primitive timed out / is missing / exited 2 — the
    answer is UNKNOWN).

    S3 / A3 — why a third value exists. `_in_worktree` folds every failure into
    False, and at `create_topic` False means "collocate the plan project-side".
    Inside a linked worktree that is the damaging direction: worktrees share one
    plans directory, so a project-side plan collides. Until S3 the site that
    reads this was dormant (`_resolve_project_root` returned None everywhere), so
    the unsafe default had never actually run; now that it can, the caller asks
    this function and treats None as "route to ~/.claude/plans and say so".
    """
    cwd = cwd or Path.cwd()
    primitive = Path(__file__).resolve().parent / "worktree-detect.sh"
    try:
        result = subprocess.run(
            ["bash", str(primitive), str(cwd)],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


# ---------------------------------------------------------------------------
# Canonical (topic, project) identity resolver (session-topic-identity-coherence
# plan, Slice S1 / A1). THE single derivation locus for the project half of the
# identity: every surface that mints, binds, locks, reaps, or reads a topic key
# routes through here, so one physical topic can never acquire divergent project
# keys (the __Root/__Projects twin class this plan fixes).
# ---------------------------------------------------------------------------

def _nearest_leaf_or_root(start: Path, root: Path) -> Path:
    """Walk up from ``start`` to the nearest leaf-project directory (one that
    carries a ``TODO.md``), stopping at ``root``. Returns that leaf directory,
    or ``root`` when none is found before the walk reaches it.

    THE single locus for the Root/leaf walk (bookkeeping-model §3/§5). Both
    ``_resolve_project_root`` (Path output) and ``canonical_project_for_spine``
    (slug output) delegate here, so the rule is never re-implemented (Cockburn
    Evolution Test — the /assess coherence finding this plan closes).

    A directory literally named ``Thoughts`` (the flat staging graph) or
    ``Root`` is structural, never a leaf project — any ``TODO.md`` it carries is
    skipped and the walk continues upward (A1 guard rail; a no-op in practice
    since those dirs carry no ``TODO.md``).

    Precondition: ``start`` is at or below ``root`` and the two paths are
    comparable (both realpath-normalised, or both raw) so the ``cur == root``
    stop test fires.
    """
    cur = start
    while cur != root and cur != cur.parent:
        if cur.name not in ("Thoughts", "Root") and (cur / "TODO.md").exists():
            return cur
        cur = cur.parent
    return root


def canonical_project_for_spine(thought_file_path, project_root=None) -> str:
    """Derive the canonical *project slug* from a spine's filesystem location.

    THE single derivation locus for the project half of the (topic, project)
    identity (A1). A root-level or cross-cutting spine resolves to ``"Root"``;
    a project-scoped spine resolves to the basename of the nearest
    ``TODO.md``-bearing ancestor directory (the leaf project).

    Pure function — derives from the filesystem only: never the cwd basename,
    never a caller-supplied key, never git. ``realpath`` + ``normcase`` are
    applied to BOTH the spine and ``project_root`` before the descendant
    comparison, so neither a symlinked spine nor a symlinked root
    (``$CLAUDE_PROJECT_DIR`` -> ``~/repos/Projects`` on this
    machine) yields a divergent path or a false "outside-root" classification.
    (``os.path.normcase`` is a no-op on POSIX; it is applied per the plan for
    case-insensitive-filesystem safety and portability, and deliberately does
    NOT lowercase leaf names on macOS — that would conflate distinct
    case-sensitive project keys such as ``__Root`` vs a leaf named ``root``.)

    ``project_root`` defaults to ``PROJECTS_ROOT``; the parameter exists so the
    derivation can be unit-tested against a scratch tree.
    """
    root = Path(project_root) if project_root is not None else PROJECTS_ROOT
    root_real = _norm_path(root)                 # S3: the one normaliser
    spine_real = _norm_path(thought_file_path)
    start = spine_real.parent
    # the spine's parent must be a real directory to localise it
    if not os.path.isdir(str(start)):
        return "Root"
    # inside the projects tree?
    try:
        start.relative_to(root_real)
    except ValueError:
        return "Root"   # cross-cutting / outside the tree -> canonical default
    leaf = _nearest_leaf_or_root(start, root_real)
    return "Root" if leaf == root_real else leaf.name


def canonical_identity(session_id):
    """Return the canonical ``(topic_slug, project_slug)`` for a session,
    derived through the single locus.

    Resolves the session's bound topic, then RE-DERIVES the project half from
    the bound spine's filesystem location (the authoritative source) via
    ``canonical_project_for_spine``. Falls back to the recorded ``project_slug``
    only when no spine is known (plain TODO topics have no spine to derive from)
    or the recorded spine no longer resolves on disk.

    Returns ``(None, None)`` when the session is unbound.
    """
    topic_slug, project_slug, state = _resolve_topic(session_id)
    if not topic_slug:
        return None, None
    spine = (state or {}).get("thought_file_path")
    if spine:
        sp = Path(spine)
        if not sp.is_absolute():
            base = (state or {}).get("project_root") or str(PROJECTS_ROOT)
            sp = Path(base) / sp
        if sp.exists():
            return topic_slug, canonical_project_for_spine(sp)
    return topic_slug, project_slug


# ---------------------------------------------------------------------------
# Twin reconciliation (session-topic-identity-coherence plan, S2/A2 + S4/A4).
# Heal the __Root/__Projects twin class a caller-supplied project key produced
# before the canonical resolver existed. Safe-defaults throughout: DRY-RUN
# unless apply=True; mv-to-timestamped-backup, never rm; per-twin lock guard so
# a live session's record is never moved out from under it.
# ---------------------------------------------------------------------------

def _lock_key(slug, project):
    return f"{slug}__{project}"


def _lock_held_fresh(slug, project):
    """True iff a live (non-stale) session holds the composite lock for this
    twin. Read-only freshness check (auto-bind's on-the-fly guard); the bulk
    reconcile additionally ACQUIRES the lock (A4). Fails OPEN on any error —
    the authoritative guard is the acquire path."""
    try:
        import taskmanagement as _tm
        payload = _tm.read_lock(_lock_key(slug, project))
        if not isinstance(payload, dict):
            return False
        hb = payload.get("last_heartbeat")
        if not hb:
            return False
        age = (_tm._now() - _tm._parse_iso(hb)).total_seconds()
        return age < _tm._stale_t_seconds()
    except Exception:
        return False


def _twins_for_slug(slug):
    """All on-disk topic-state twins for a slug: list of (project, path, data)
    sorted by project (lexicographic — the deterministic order A4 acquires in)."""
    out = []
    for m in sorted(TOPIC_STATE_DIR.glob(f"{slug}__*.json")):
        stem = m.name[:-len(".json")]
        if not stem.startswith(slug + "__"):
            continue
        proj = stem[len(slug) + 2:]
        if not proj:
            continue
        try:
            data = _read_json(m)
        except (json.JSONDecodeError, OSError):
            data = None
        out.append((proj, m, data or {}))
    return out


def _state_richness(data):
    """Sortable richness of a topic-state dict — higher = more real work
    recorded. Picks which twin becomes canonical when the canonical key does not
    yet exist. (spine + phase + history depth + non-null field count.)"""
    if not isinstance(data, dict):
        return (0, 0, 0, 0)
    has_spine = 1 if data.get("thought_file_path") else 0
    has_phase = 1 if data.get("phase") else 0
    hist = len(data.get("phase_history") or [])
    nonnull = sum(1 for v in data.values() if v not in (None, [], {}, ""))
    return (has_spine, has_phase, hist, nonnull)


def _find_recorded_spine_for_slug(slug):
    """Absolute spine path recorded on ANY existing twin for this slug, or None.
    Cheap + reliable source for canonical_project_for_spine — the __Root twin of
    the live bug records thought_file_path while the __Projects twin does not.
    (No disk rglob: create_topic is on the hot path.)"""
    for _proj, _path, data in _twins_for_slug(slug):
        tfp = data.get("thought_file_path")
        if tfp:
            p = Path(tfp)
            if not p.is_absolute():
                base = data.get("project_root") or str(PROJECTS_ROOT)
                p = Path(base) / p
            if p.exists():
                return p
    return None


def canonical_project_for_slug(slug):
    """Canonical project for an existing topic slug via its recorded spine, or
    None when no spine can localise it (refuse-rather-than-guess, E26)."""
    spine = _find_recorded_spine_for_slug(slug)
    if spine is None:
        return None
    return canonical_project_for_spine(spine)


def _slug_from_spine_path(path):
    """The BASE topic slug for an artifact filename — THE single base-slug
    derivation locus (S2/A4).

    It no longer restates a stem grammar of its own. It reads
    `_timestamped_slug_from_spine` (the `<slug>-<ts>` form auto-registration
    uses as a TODO slug tag) and strips the timestamp off it, so the two
    producers of a topic key — `canonical_topic_for_spine` on the creation path
    and `auto_register_topic` at the ship boundary — now read ONE grammar. They
    previously read two, which agreed on the standard shape and disagreed on
    every scope-keyed one; nothing reconciled them, so the same artifact could
    be filed under two keys.

    Defined ABOVE `_timestamped_slug_from_spine`; the name resolves at call
    time, not at def time. The ordering is the file's existing shape, not a
    dependency claim — the base slug is the primitive every caller in this
    region wants, and the timestamped form is auto-registration's local concern.

    What CHANGED, stated rather than implied. A scope-keyed
    `<slug>-<ts>_<SCOPE>_PLAN.md` used to yield `<slug>-<ts>_<SCOPE>` here — the
    TYPE suffix was stripped BEFORE the timestamp was matched, so the timestamp
    was no longer trailing and survived the strip — while the ship boundary
    yielded `<slug>`. `<slug>` is the canonical answer (operator decision): a
    scope key names a slice of one work, not a second work. Measured 2026-08-30
    over `Projects/Thoughts/*.md`: 52 of the 295 artifacts bearing a recognised
    TYPE suffix diverged under the two grammars, and 0 of the 119 live
    topic-state records changed the slug their recorded spine derives.

    A DOUBLE-timestamped name changed too, and that was NOT disclosed until an
    independent checker found it. `foo-<ts1>-<ts2>_PLAN.md` used to yield
    `foo-<ts1>` (the TYPE suffix was stripped, then the ONE trailing timestamp)
    and now yields `foo`; `foo-<ts1>-<ts2>_S1_PLAN.md` used to yield the whole
    `foo-<ts1>-<ts2>_S1` and now also yields `foo`. The cause is the delegation
    itself: `_timestamped_slug_from_spine`'s non-greedy leading-timestamp match
    takes the FIRST timestamp, and the outer strip then removes it, collapsing
    both shapes to the base. Sixteen such artifacts exist under
    `Projects/Thoughts/` today — fifteen `git-working-model-<ts>-<ts>_REVIEW_REPORT.md`
    and one `session-topic-identity-coherence-<ts>-<ts>_ASSESSMENT_<sid>.md`.
    Every one is an advisory-bucket TYPE rather than a spine, so nothing derives
    a topic key from them and no live key moved. The new answer is the more
    correct one under the same rule that settled the scope-keyed case: the base
    slug names the work, and a second timestamp stamps one report OF that work,
    not a second work. The defect here was the silence, not the semantics.

    An UNtimestamped name keeps its previous result byte-for-byte, because the
    two grammars already agreed there: `<slug>_TYPE.md` -> `<slug>`, and an
    untimestamped scope-keyed `<slug>_<SCOPE>_TYPE.md` -> `<slug>_<SCOPE>`. With
    no timestamp there is no anchor separating the work name from the scope key,
    and inventing one would be substitution.
    """
    return re.sub(r"-\d{14}$", "", _timestamped_slug_from_spine(path))


def canonical_topic_for_spine(thought_file_path):
    """Derive the canonical *topic slug* from a work's own artifact, or ``None``.

    THE single derivation locus for the work-name half of the (topic, project)
    identity — the counterpart of ``canonical_project_for_spine`` (A1). It
    mirrors that function's shape but deliberately NOT its failure mode: the
    project half has a safe default (``"Root"``) and never fails, which is right
    there and wrong here, because a wrong guess at the work name IS the defect
    this locus exists to close. When nothing can be derived this returns ``None``
    and the caller refuses (refuse-rather-than-substitute, E26).

    Pure function of the artifact's NAME — never the cwd, never a caller-supplied
    key, never git, and never the recorded state (which is itself just ambient
    recorded ambience). It reuses ``_slug_from_spine_path`` rather than restating
    the stem grammar, so the base slug is derived in ONE place.

    Since S2/A4 that ONE place is shared with the OTHER producer of a topic key:
    ``auto_register_topic`` derives its ``slug`` from the same function, so the
    creation path and the ship boundary can no longer file the same artifact
    under two keys. Scope-keyed names are where they used to differ — see
    ``_slug_from_spine_path``'s own docstring for the before/after and the
    measurement.

    Still not module-wide, and the difference is worth stating rather than
    implying: ``_autobind_slugs_from_prompt`` still open-codes a NARROWER copy of
    the same grammar (it strips ``_THOUGHT`` but not ``_PLAN``/``_DESIGN``/
    ``_RESEARCH``/``_CLAIMS``). That copy predates this slice and is deliberately
    untouched — it is on the BIND path, not the create path, and both A1 and
    S2/A4 scope the single-locus requirement to the creation path. Note also that
    the one rule has two entry points of differing strictness: this function
    requires ``.md``, while ``_slug_from_spine_path`` — reached directly by
    ``_reconcilable_twins_for_slug`` and by ``auto_register_topic`` — does not.

    ``None`` is returned for anything that does not NAME A MARKDOWN ARTIFACT.
    That test is what structurally excludes a directory path, and it is
    load-bearing rather than decorative: the whole defect signature is a
    directory basename (``Projects``) reaching the work-name position, and a
    derivation that accepted ``~/repos/Projects`` would mint
    ``Projects__Root.json`` — the very record this plan counts. Measured
    2026-08-22 across the live record store: every recorded
    ``thought_file_path`` ends in ``.md``, so the test refuses no real spine.
    """
    if thought_file_path is None:
        return None
    raw = str(thought_file_path).strip()
    if not raw:
        return None
    name = Path(raw).name
    # Must name a Markdown artifact. Exact `.md` (not a case-folded match) to
    # stay in lock-step with `_slug_from_spine_path`, which strips exactly that
    # suffix — a looser test here would hand it a stem it does not strip.
    if not name.endswith(".md"):
        return None
    slug = _slug_from_spine_path(raw)
    # A stem that is empty or degenerate after stripping is not a work name.
    if not slug or slug in (".", ".."):
        return None
    # `__` is the RECORD-FILENAME SEPARATOR, so a slug containing it is
    # ambiguous by construction and must not be minted from an artifact.
    # `_topic_path` builds `f"{topic_slug}__{project_slug}.json"`, so an
    # artifact named `foo__bar_THOUGHT.md` derives `foo__bar` and produces
    # `foo__bar__baz.json` — byte-identical to the record for topic `foo` in a
    # project literally named `bar__baz`. A directory basename may legitimately
    # contain `__`, so that collision is reachable, and its consequence is the
    # worst one available here: `_create_topic_locked`'s idempotency guard finds
    # the colliding file, reports it as already existing, and REBINDS the new
    # session onto an unrelated topic's record — inheriting its phase,
    # phase_history and clarification payloads. That is substitution, which is
    # the one thing this function exists to refuse.
    #
    # Found adversarially, and it is a door THIS SLICE opened: before A2 no CLI
    # caller could mint a slug from an artifact name at all, so a `__`-bearing
    # topic slug arose only from the hand-renamed swapped-key era. Refused here
    # rather than downstream, because downstream cannot tell the two apart —
    # `_classify_records` parses records back with `rsplit("__", 1)`, which
    # resolves the ambiguity by guessing.
    if "__" in slug:
        return None
    return slug


def _resolve_spine_abs(data):
    """Absolute spine path recorded on a topic-state dict, resolved (relative
    paths joined against the record's project_root / PROJECTS_ROOT), or None when
    absent / not on disk."""
    tfp = (data or {}).get("thought_file_path")
    if not tfp:
        return None
    p = Path(tfp)
    if not p.is_absolute():
        base = (data or {}).get("project_root") or str(PROJECTS_ROOT)
        p = Path(base) / p
    return p if p.exists() else None


def _reconcilable_twins_for_slug(slug):
    """Twins of `slug` VERIFIED BY SPINE — the authoritative identity is the
    spine, NOT the filename. Returns only records under `slug__*` whose resolved
    spine's slug == `slug`. This is the load-bearing safety guard against the
    inverted/swapped-key era in the live state (`Projects__<topic>.json`, whose
    first filename segment is the PROJECT, not the topic): those records share a
    first segment but their spine slug differs, so they are EXCLUDED — reconcile
    can never collapse unrelated topics. A record with no resolvable spine is
    also excluded (cannot be confirmed a twin of this slug)."""
    out = []
    for proj, path, data in _twins_for_slug(slug):
        spine = _resolve_spine_abs(data)
        if spine is None:
            continue
        if _slug_from_spine_path(spine) == slug:
            out.append((proj, path, data))
    return out


def _repoint_active(slug, old_proj, new_proj):
    """Repoint any _active.json session entries from a moved twin to the
    canonical project (edge case: dead _active sessions — repoint, don't drop)."""
    if old_proj == new_proj:
        return
    # Region 1 of 4 (Cluster-B S0). The lock opens at the `read_active()` that
    # STARTS the read-modify-write, not at the `write_active()` that ends it — a
    # lock beginning at the mutation leaves the race fully intact.
    with _active_lock():
        active = read_active()
        changed = False
        for _sid, entry in active.items():
            if (isinstance(entry, dict) and entry.get("topic_slug") == slug
                    and entry.get("active_project") == old_proj):
                entry["active_project"] = new_proj
                changed = True
        if changed:
            write_active(active)


def _ensure_canonical_record(slug, canon, *, respect_locks=True):
    """Make the canonical state record exist by renaming the richest mis-keyed
    twin to the canonical key when it is absent (light healing used by auto-bind
    / mint; the redundant twins are left for the bulk `reconcile` CLI). Returns
    True iff the canonical record exists after the call. Respects a fresh lock on
    the source twin (defers to the bulk reconcile). Ensures no data loss: only a
    rename, never a delete."""
    canon_path = _topic_path(topic_slug=slug, project_slug=canon)
    if canon_path.exists():
        return True
    twins = _twins_for_slug(slug)
    if not twins:
        return False
    # A3 (topic-identity-generator-closure S2). Verify the twin set BEFORE any
    # rename. `_twins_for_slug` is a raw glob on `{slug}__*.json`, so it admits a
    # record that belongs to a DIFFERENT topic and merely shares a first filename
    # segment (the inverted `<project>__<topic>` era: `Projects__Root.json`
    # recording `widget-...._THOUGHT.md`). Renaming that onto the canonical key
    # destroys an unrelated topic's record and repoints _active with it.
    #
    # Admission is a UNION computed HERE — `_reconcilable_twins_for_slug` is
    # consumed unchanged, never widened, so `reconcile`'s locked invariant holds:
    #   (a) the twin is in `_reconcilable_twins_for_slug(slug)` — the
    #       artifact-slug-equality set `reconcile` already trusts; OR
    #   (b) the KEY is a synthetic plain slug (the SHIPPED prefix predicate, as
    #       used by `classify_active_entries`). For those the artifact slug
    #       legitimately differs from the key, so requiring (a) would empty the
    #       set, mint a blank record and orphan the rich twin — the very lost
    #       update this function exists to prevent; OR
    #   (c) the twin records no resolvable artifact — preserving today's
    #       behaviour for that class (33 of 105 live records), which (a) can
    #       neither confirm nor deny.
    # REFUSE only a twin whose artifact RESOLVES to a slug that is not the key,
    # when the key is not synthetic. A refusal means TOUCH NOTHING — the
    # unrelated twin is left exactly where it is. There is deliberately no
    # override: a refusal here is the tool working.
    if slug.startswith("plain-"):
        admissible = twins                      # arm (b): key-shape carve-out
    else:
        verified = {p for _pr, p, _dd in _reconcilable_twins_for_slug(slug)}
        admissible = [
            t for t in twins
            if t[1] in verified                 # arm (a)
            or _resolve_spine_abs(t[2]) is None  # arm (c)
        ]
    if not admissible:
        return False
    ranked = sorted(admissible, key=lambda t: _state_richness(t[2]), reverse=True)
    src_proj, src_path, _d = ranked[0]
    if src_proj == canon:
        return src_path.exists()
    if respect_locks and _lock_held_fresh(slug, src_proj):
        return False  # defer to the bulk reconcile
    try:
        src_path.rename(canon_path)
    except FileNotFoundError:
        # concurrent rename already produced the canonical record (idempotent
        # success) ONLY if the SOURCE is the missing path; otherwise re-raise.
        if src_path.exists():
            raise
    _repoint_active(slug, src_proj, canon)
    return canon_path.exists()


def reconcile_topic_identity(slug, *, apply=False, respect_locks=True,
                             acquire_locks=False):
    """Collapse a slug's divergent-key twins onto ONE canonical record.

    Derives the canonical project from the topic's recorded spine
    (canonical_project_for_spine). Ensures the canonical record exists (renaming
    the richest mis-keyed twin to the canonical key when absent) and moves every
    remaining redundant twin aside to ``_reaped/`` as a timestamped backup (mv,
    never rm — safe-defaults). A twin richer than the canonical is backed up with
    a ``.MERGE_REVIEW`` marker rather than silently dropped.

    apply=False (default) is DRY-RUN: reports the plan, mutates nothing.
    respect_locks (default True): a twin whose composite lock is held FRESH by a
    live session is SKIPPED (deferred) — never moved out from under it.
    acquire_locks (A4 bulk path): additionally ACQUIRE each twin's lock in
    lexicographic order before mutating, closing the check->act TOCTOU; released
    in a finally. When acquire_locks is set, per-twin freshness skipping is off
    (the acquire already refused on a live holder).

    Returns a structured dict (never raises for the happy path).
    """
    result = {
        "slug": slug, "canonical_project": None, "canonical_exists": False,
        "actions": [], "skipped_locked": [], "merge_review": [],
        "applied": bool(apply), "reason": None,
    }
    # Spine-verified twin set (SAFETY): only records whose spine slug == slug are
    # treated as twins, so an inverted `<project>__<topic>` record that merely
    # shares a first filename segment (e.g. slug="Projects") yields NO twins and
    # is never collapsed. This is what makes `reconcile <slug>` / `reconcile all`
    # safe against the live inverted-key era.
    twins = _reconcilable_twins_for_slug(slug)
    if not twins:
        result["reason"] = "no-twins"
        return result
    # canonical derived from the (verified) twins' shared spine
    canon = canonical_project_for_spine(_resolve_spine_abs(twins[0][2]))
    if canon is None:
        result["reason"] = "no-spine-cannot-disambiguate"
        return result
    result["canonical_project"] = canon
    canon_path = _topic_path(topic_slug=slug, project_slug=canon)
    result["canonical_exists"] = canon_path.exists()

    if len(twins) == 1 and twins[0][0] == canon and canon_path.exists():
        result["reason"] = "already-canonical"
        return result

    acquired = []
    sid = f"reconcile-{os.getpid()}"
    if apply and acquire_locks:
        import taskmanagement as _tm
        for proj, _p, _d in twins:  # already lexicographically sorted
            key = _lock_key(slug, proj)
            res = _tm.acquire_lock(key, sid)
            st = res.get("status")
            if st in ("ACQUIRED", "ALREADY_HELD_BY_SELF"):
                acquired.append(key)
            else:
                for k in acquired:
                    _tm.release_lock(k, sid)
                result["skipped_locked"].append(proj)
                result["reason"] = (f"twin-locked:{proj}" if st == "HELD_BY_OTHER"
                                    else f"lock-contention:{proj}")
                return result
    try:
        _reconcile_apply(
            slug, canon, twins, canon_path,
            apply=apply,
            respect_locks=(respect_locks and not acquire_locks),
            result=result,
        )
    finally:
        if acquired:
            import taskmanagement as _tm
            for k in acquired:
                _tm.release_lock(k, sid)
    if result["reason"] is None:
        result["reason"] = "reconciled" if apply else "dry-run"
    return result


def _prep_reaped(reaped_dir=None):
    """Quarantine pre-flight: ensure `_reaped/` exists and that BOTH it and the
    state dir are writable, so a caller never starts a move it cannot finish.

    Module scope, deliberately: this was a nested closure inside
    `_reconcile_apply`, which made it unreachable to any other caller. The
    inverted backfill (`reconcile_inverted`) and `prune-active` both need the
    same disk pre-flight, and re-implementing it locally is what the Guiding
    Policy's "never parallel logic" forbids — so it is extracted here rather
    than copied. Reads TOPIC_STATE_DIR at CALL time so a `--state-dir` override
    is honoured."""
    reaped_dir = (Path(reaped_dir) if reaped_dir is not None
                  else TOPIC_STATE_DIR / "_reaped")
    reaped_dir.mkdir(parents=True, exist_ok=True)
    if not (os.access(str(reaped_dir), os.W_OK)
            and os.access(str(TOPIC_STATE_DIR), os.W_OK)):
        raise PermissionError(
            f"state dir / _reaped not writable: {TOPIC_STATE_DIR}")
    return reaped_dir


def _reconcile_apply(slug, canon, twins, canon_path, *, apply, respect_locks,
                     result):
    now_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    reaped_dir = TOPIC_STATE_DIR / "_reaped"
    canon_exists = canon_path.exists()

    source_proj = None
    if not canon_exists:
        ranked = sorted(twins, key=lambda t: _state_richness(t[2]), reverse=True)
        source_proj = ranked[0][0]

    if canon_exists:
        try:
            canon_rich = _state_richness(_read_json(canon_path) or {})
        except (json.JSONDecodeError, OSError):
            canon_rich = None
    else:
        canon_rich = None

    for proj, path, data in twins:
        if proj == canon and canon_exists:
            continue  # the canonical record itself — keep
        # Case-insensitive-FS guard (macOS APFS): a twin that is the SAME
        # underlying file as the canonical record (e.g. `__root` vs `__Root`)
        # IS the canonical record under a differently-cased key — never back it
        # up, that would move the only copy away. If the on-disk name differs
        # only in CASE from the canonical key, correct the case in place (a
        # case-only rename is case-preserving on APFS and a real rename on a
        # case-sensitive FS); otherwise it already IS canonical — leave it.
        if canon_exists:
            try:
                same = os.path.samefile(str(path), str(canon_path))
            except OSError:
                same = False
            if same:
                if path.name != canon_path.name:
                    result["actions"].append(
                        {"op": "case-fix", "from": path.name, "to": canon_path.name})
                    if apply:
                        try:
                            path.rename(canon_path)
                        except FileNotFoundError:
                            if path.exists():
                                raise
                        _repoint_active(slug, proj, canon)
                continue
        if respect_locks and _lock_held_fresh(slug, proj):
            result["skipped_locked"].append(proj)
            continue

        if not canon_exists and proj == source_proj:
            result["actions"].append(
                {"op": "rename", "from": path.name, "to": canon_path.name})
            if apply:
                try:
                    path.rename(canon_path)
                except FileNotFoundError:
                    if path.exists():
                        raise
                _repoint_active(slug, proj, canon)
            canon_exists = True
            canon_rich = _state_richness(data)
            continue

        merge = canon_rich is not None and _state_richness(data) > canon_rich
        backup_name = f"{path.name}.bak-{now_ts}"
        entry = {"op": "backup", "twin": path.name, "to": f"_reaped/{backup_name}"}
        if merge:
            entry["merge_review"] = True
            result["merge_review"].append(proj)
        result["actions"].append(entry)
        if apply:
            _prep_reaped(reaped_dir)
            dest = reaped_dir / backup_name
            try:
                path.rename(dest)
            except FileNotFoundError:
                if path.exists():
                    raise
            if merge:
                (reaped_dir / (backup_name + ".MERGE_REVIEW")).write_text(
                    f"twin {path.name} was RICHER than canonical "
                    f"{canon_path.name}; review before discarding.\n",
                    encoding="utf-8")
            _repoint_active(slug, proj, canon)


def _classify_records():
    """Group every topic-state record by its AUTHORITATIVE spine identity.

    Returns (groups, inverted, unresolvable):
      groups       : {spine_slug: [(filename_project, path, data, canon_project)]}
                     — correct-convention records (filename topic == spine slug).
      inverted     : records whose filename topic segment != the spine's slug
                     (the swapped-key era, e.g. `Projects__<topic>.json`).
      unresolvable : records whose spine cannot be resolved on disk (can't be
                     classified — surfaced, never auto-touched).
    Read-only."""
    groups = {}
    inverted = []
    unresolvable = []
    for m in sorted(TOPIC_STATE_DIR.glob("*__*.json")):
        if m.name == "_active.json":
            continue
        stem = m.name[:-len(".json")]
        if "__" not in stem:
            continue
        ftopic, fproject = stem.rsplit("__", 1)
        try:
            data = _read_json(m) or {}
        except (json.JSONDecodeError, OSError):
            continue
        spine = _resolve_spine_abs(data)
        if spine is None:
            unresolvable.append({"file": m.name, "filename_topic": ftopic,
                                 "filename_project": fproject})
            continue
        spine_slug = _slug_from_spine_path(spine)
        canon_proj = canonical_project_for_spine(spine)
        if ftopic != spine_slug:
            inverted.append({"file": m.name, "filename_topic": ftopic,
                             "filename_project": fproject, "spine_slug": spine_slug,
                             "canonical": f"{spine_slug}__{canon_proj}"})
            continue
        groups.setdefault(spine_slug, []).append((fproject, m, data, canon_proj))
    return groups, inverted, unresolvable


def find_mis_keyed():
    """Report every RECONCILABLE slug whose correct-convention key(s) diverge
    from the canonical derivation: >1 same-spine twin, or a lone twin whose
    project != canonical. Read-only. Returns list of
    {slug, twins, canonical_project, reason}.

    Spine-authoritative + SAFE: the inverted/swapped-key era is NOT reported here
    (see find_inverted) so `reconcile all` never touches it."""
    groups, _inverted, _unres = _classify_records()
    out = []
    for slug, recs in sorted(groups.items()):
        projs = [p for p, _pt, _d, _c in recs]
        canon = recs[0][3]
        if len(projs) > 1:
            reason = "multiple-twins"
        elif canon is not None and projs and projs[0] != canon:
            reason = "mis-keyed"
        else:
            continue
        out.append({"slug": slug, "twins": sorted(projs),
                    "canonical_project": canon, "reason": reason})
    return out


def find_inverted():
    """Records from the swapped-key era (filename topic segment != spine slug).
    These are NOT auto-reconciled — surfaced for the operator. Read-only."""
    _groups, inverted, _unres = _classify_records()
    return inverted


def find_unresolvable():
    """Records whose recorded spine cannot be resolved on disk — surfaced for the
    operator, never auto-touched. Read-only."""
    _groups, _inverted, unres = _classify_records()
    return unres


def find_body_mismatch():
    """Records whose STORED identity halves disagree with their filename key.

    S5/A6 — the FOURTH reporter, and the first thing to compare a topic-state
    RECORD's stored identity halves against its filename key. It sits BESIDE the
    three above rather than altering how any of them bucket, so the
    inverted-exclusion assertions in `tests/test_identity_reconcile.py` stay green
    unchanged.

    Scoped deliberately: A6's claim is that **the classifier** reads no body
    identity field, and that is what this function is first against. The module
    reads `topic_slug` elsewhere (`_active.json` SESSION entries, not record
    bodies), so a module-wide absolute here would be the same over-claim round 0A
    already had to correct once.

    Two structural decisions, both load-bearing:

    1. **Not a fourth element of `_classify_records`' return tuple.** That
       function returns `(groups, inverted, unresolvable)` and is unpacked as a
       3-tuple at three call sites — `find_mis_keyed`, `find_inverted`,
       `find_unresolvable` — every one of which the reaper's `find-mis-keyed`
       verb calls. A fourth element raises `ValueError: too many values to
       unpack` at all three.

    2. **It walks INDEPENDENTLY rather than riding the classifier's loop.**
       `_classify_records` `continue`s on unresolvable, and again on inverted,
       before reaching its `groups` append — so a body check placed at the
       natural point would never see those records. The mismatches span all
       three buckets, so such a check would come back short. The
       body-vs-filename comparison needs NO artifact resolution whatsoever, so
       it runs before any spine lookup and therefore sees every record.

       Figures, each stated against its OWN population rather than mixed: on the
       S0 snapshot the classifier buckets 33 of 106 records unresolvable, and
       this function reports 50 mismatches over the same 106. Do not re-pair a
       subtotal measured on one population with a denominator measured on
       another — that is the defect the plan's Anchor snapshot keeps recording.

       **This property is what `test_sees_records_the_classifier_would_have_skipped`
       must pin, and pinning it requires fixture records that RESOLVE.** A fixture
       whose records all lack `thought_file_path` makes every record unresolvable,
       and the containment assertion then holds by construction — it would hold
       for an implementation that returned an empty list, or one that walked
       `find_unresolvable()` alone and missed every `groups`-bucket record.

    A record carrying NEITHER identity half is counted in NEITHER bucket — it is
    not a mismatch and not an agreement — and comes back separately. This is not
    hypothetical: `v11-project__v11-test.json` is live right now and its entire
    body is ``{"session_id": ..., "bypass_marker": false}``, so a
    ``data["topic_slug"]`` subscript would raise `KeyError` on the first full
    walk. It is also why the body-bearing total (105) and the record total (106)
    differ by exactly one.

    A body that is not a mapping at all (a top-level list, string or number) is
    treated the same way — it carries no identity halves, so it joins
    `no_identity` rather than crashing the walk. `.get()` alone does not cover
    this: it is `AttributeError`, one step over from the `KeyError` above, and
    the walk is over a LIVE directory whose contents this function does not
    control.

    A one-half record is ALWAYS a mismatch, stated because it is a definitional
    choice and not an obvious one: the absent half cannot equal a non-empty
    filename segment, so a record carrying only `topic_slug` is reported even
    when that half agrees. Incomplete is not the same as correct. No such record
    exists in the population this function walks today — of its 106 top-level
    records, 105 carry both halves and the sole exception carries neither — so
    this is a definitional statement, not a live count. (Stated against THIS
    walk's population on purpose. An earlier revision cited "161 of 162", which
    counts the whole tree INCLUDING `_reaped/`; this glob is non-recursive, so
    that denominator describes a different population — the cross-population
    re-pairing this very docstring warns against two paragraphs above.)

    An UNDECODABLE record is skipped from BOTH returned lists — it is neither a
    mismatch nor a no-identity record, so the totals silently exclude it. Said
    plainly because it is an under-count rather than a neutral omission: nothing
    tells the operator such a file exists. The population is zero today, and the
    sibling `_classify_records` drops the same file (less gracefully — it dies),
    so this is consistent rather than novel; it is recorded, not defended.

    REPORT ONLY. This function mutates nothing and has no `--apply` path.

    Read-only. Returns ``(mismatches, no_identity)``:
      mismatches  : [{file, filename_topic, filename_project, body_topic,
                      body_project, cwd_fingerprint}]
      no_identity : [{file}] — records carrying neither half.
    """
    mismatches = []
    no_identity = []
    # `.glob`, NOT `.rglob` — and that is load-bearing, not incidental. The reaper
    # quarantines a record by MOVING it into `_reaped/` under its original
    # `<topic>__<project>.json` name, so a recursive walk would fold every
    # quarantined record back into the mismatch total AND into the
    # cwd-fingerprint subtotal, which is the one number A6's gate asserts.
    # Pinned by `test_quarantined_records_under_reaped_are_NOT_walked`.
    for m in sorted(TOPIC_STATE_DIR.glob("*__*.json")):
        # The next two guards are DEAD as written, and are kept only to mirror
        # `_classify_records` line for line: `*__*.json` cannot match
        # `_active.json` (no `__`), and a name containing `__` always has it in
        # the stem (`.json` has no underscore). Said plainly rather than left to
        # imply coverage — no test exercises either branch, because neither is
        # reachable, and a fixture record placed here to "cover" the first one
        # would be excluded by the glob before the guard ever ran.
        if m.name == "_active.json":
            continue
        stem = m.name[:-len(".json")]
        if "__" not in stem:
            continue
        ftopic, fproject = stem.rsplit("__", 1)
        try:
            data = _read_json(m) or {}
        except (OSError, ValueError):
            # ValueError, not json.JSONDecodeError: an invalid-UTF-8 record
            # raises UnicodeDecodeError, which is a ValueError but NOT a
            # JSONDecodeError, so the narrower form let it escape and kill the
            # walk. This is the same broader catch the sibling `find_orphans`
            # in reap_orphan_plain_topics.py already uses (JSONDecodeError is
            # itself a ValueError, so nothing stops being caught).
            continue
        if not isinstance(data, dict):
            # A top-level list/str/int body: `.get()` would raise AttributeError.
            # It carries no identity halves, so it belongs with the no-halves
            # class rather than crashing a walk over a live directory.
            no_identity.append({"file": m.name})
            continue
        # `.get()`, NEVER `data[...]` — see the v11-project note above.
        btopic = data.get("topic_slug")
        bproject = data.get("project_slug")
        if btopic is None and bproject is None:
            no_identity.append({"file": m.name})
            continue
        if btopic == ftopic and bproject == fproject:
            continue
        # What the `proot is not None` test is actually for — stated correctly,
        # because an earlier revision of this comment said it was there so that
        # `basename(None)` would not raise, and THAT WAS FALSE: the argument is
        # `str(proot)`, so `basename(str(None))` == "None" and raises nothing.
        # Dropping the test would therefore change exactly one behaviour — a
        # record whose `topic_slug` is the literal string "None" and whose
        # `project_root` is null would score as cwd-fingerprinted, which is a
        # false positive in the one number this whole action is gated on.
        # `test_null_project_root_is_never_fingerprinted_even_when_topic_is_None`
        # pins that, rather than pinning an absence of raising that the guard
        # does not cause.
        #
        # The fingerprint is the DEFENSIBLE FLOOR of what is attributable to the
        # cwd-derived generator, never the whole story. It UNDER-counts: a
        # session run from a subdirectory, or one in a worktree where
        # `project_root` is None, produces the identical defect without matching.
        # The remainder is NOT claimed to be unrelated.
        proot = data.get("project_root")
        fingerprint = (proot is not None
                       and btopic is not None
                       and btopic == os.path.basename(str(proot)))
        mismatches.append({
            "file": m.name,
            "filename_topic": ftopic,
            "filename_project": fproject,
            "body_topic": btopic,
            "body_project": bproject,
            "cwd_fingerprint": fingerprint,
        })
    return mismatches, no_identity


# --------------------------------------------------------------------------- #
# Inverted-key backfill — the A2 Migration Contract
# --------------------------------------------------------------------------- #
#
# One-shot supervised cleanup of the swapped-key era (`<project>__<topic>.json`).
# `find_inverted()` classifies them and precomputes each one's canonical target
# but `reconcile` deliberately EXCLUDES them, so they have no path to their
# canonical `<topic>__<project>` form. This closes that gap.
#
# Per record, three cases:
#   (i)   canonical name FREE            -> atomic rename inverted -> canonical
#   (ii)  canonical exists, inverted is
#         a redundant duplicate          -> quarantine inverted to _reaped/
#   (iii) canonical exists, inverted is
#         RICHER (ambiguous)             -> skip in place + log to the persistent
#                                           MERGE_REVIEW ledger; the operator
#                                           resolves it with an explicit
#                                           --resolve keep-inverted|keep-canonical
#
# Invariants: never delete; never overwrite without a CONFIRMED + VERIFIED
# backup of the incumbent; never leave a live canonical path empty; canonical-
# first + idempotent + crash-safe; never touch `_active.json`; non-interactive.

MERGE_REVIEW_LEDGER_NAME = "MERGE_REVIEW.ledger.jsonl"

INVERTED_RESOLUTIONS = ("keep-inverted", "keep-canonical")


def _merge_review_ledger_path():
    return TOPIC_STATE_DIR / "_reaped" / MERGE_REVIEW_LEDGER_NAME


def _read_merge_review_ledger():
    """Fold the append-only ledger to {inverted_filename: record}.

    Parse-resilient BY CONSTRUCTION (obligation 7): one JSON object per line, so
    a halted append leaves at most ONE unparseable trailing line, which is
    skipped rather than bricking `--resolve`. Later records supersede earlier
    ones for the same file, which is how a resolution marks an entry complete
    without ever rewriting the file."""
    path = _merge_review_ledger_path()
    out = {}
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue                      # torn/partial append — skip, never raise
        fname = rec.get("file")
        if fname:
            out[fname] = {**out.get(fname, {}), **rec}
    return out


def _append_merge_review_ledger(rec):
    """Append ONE record as a single line, flushed + fsynced (obligation 7)."""
    _prep_reaped()
    line = json.dumps(rec, sort_keys=True) + "\n"
    with open(_merge_review_ledger_path(), "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _sha256_file(path):
    h = hashlib.sha256()
    with open(str(path), "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verified_copy(src, dest):
    """Copy src -> dest and VERIFY it (size + sha256). True only when the
    destination provably matches the source.

    A copy genuinely CAN partially write where a rename cannot — which is
    exactly why this one is verified and the same-fs `rename` backups are not
    (the rule is *verify a copy, never bother verifying a rename*). Callers
    treat a False return as fail-closed: abort the record, mutate nothing."""
    try:
        shutil.copyfile(str(src), str(dest))
        if src.stat().st_size != dest.stat().st_size:
            return False
        return _sha256_file(src) == _sha256_file(dest)
    except OSError:
        return False


def _place_atomically(src, dest):
    """Move `src` onto `dest`, replacing it atomically.

    Same filesystem (the normal case): `rename` — POSIX-atomic, so `dest` is
    occupied at every instant and cannot tear. Cross-filesystem (EXDEV): write
    `dest.tmp` first, verify it, then atomic same-fs rename `.tmp` -> `dest`, so
    a mid-copy crash can never leave a partial `dest` that would later misread
    as a richer record (obligation 2). The torn source is left in place for the
    caller to quarantine as a redundant duplicate — never deleted.

    Returns (ok, mode). mode is 'rename' or 'copy-rename'."""
    try:
        src.rename(dest)
        return True, "rename"
    except OSError as exc:
        import errno
        if exc.errno != errno.EXDEV:
            raise
    tmp = dest.with_name(dest.name + ".tmp")
    if tmp.exists():
        tmp.unlink()                       # leftover from a prior halt (obligation 8)
    if not _verified_copy(src, tmp):
        if tmp.exists():
            tmp.unlink()
        return False, "copy-rename"
    tmp.rename(dest)
    return True, "copy-rename"


def _classify_inverted_case(rec, projected=None):
    """(case, canon_path, inverted_path) for one find_inverted() record.

    case is 'rename' (canonical free), 'redundant' (canonical exists and the
    inverted record is not richer), 'ambiguous' (canonical exists and the
    inverted record IS richer), or 'gone' (the inverted source no longer exists
    — already resolved, so a re-run is a clean no-op).

    `projected` makes the DRY-RUN honest. Several inverted records can claim the
    SAME canonical name (live data has three pointing at
    `workflow-phases-redesign__Root.json`), and only the first of them finds it
    free. Classifying every record against untouched disk state would preview
    N renames where the apply run will actually do one rename and N-1
    quarantine/merge-review — i.e. the operator would review a plan that cannot
    happen. Passing a {canonical_name: richness} map of the claims made earlier
    in the same dry-run walk reproduces the sequential outcome. The apply path
    passes None because it re-reads real disk state under the lock each
    iteration, which is authoritative."""
    inverted_path = TOPIC_STATE_DIR / rec["file"]
    canon_path = TOPIC_STATE_DIR / f"{rec['canonical']}.json"
    if not inverted_path.exists():
        return "gone", canon_path, inverted_path
    try:
        inv_rich = _state_richness(_read_json(inverted_path) or {})
    except (json.JSONDecodeError, OSError):
        inv_rich = None
    if not canon_path.exists():
        if projected is not None and canon_path.name in projected:
            can_rich = projected[canon_path.name]          # claimed earlier this walk
        else:
            return "rename", canon_path, inverted_path
    else:
        try:
            can_rich = _state_richness(_read_json(canon_path) or {})
        except (json.JSONDecodeError, OSError):
            return "ambiguous", canon_path, inverted_path
    if inv_rich is None:
        return "ambiguous", canon_path, inverted_path
    if inv_rich > can_rich:
        return "ambiguous", canon_path, inverted_path
    return "redundant", canon_path, inverted_path


def _order_inverted_richest_first(records):
    """Order the walk so that, among records competing for the SAME canonical
    name, the RICHEST is migrated first.

    Only the first claimant of a free canonical name gets case (i); every later
    one is compared against it. In filename order a sparse record can therefore
    take the name and push a richer sibling into MERGE_REVIEW — not lossy, but
    it contradicts `_reconcile_apply`, which already resolves exactly this
    contest by richness (`ranked = sorted(twins, key=_state_richness,
    reverse=True)`), and it manufactures operator work the shipped convention
    avoids. Reuse the convention rather than inventing a second one.

    Groups are emitted in canonical-name order and ties broken by filename, so
    the walk stays deterministic (dry-run preview == apply)."""
    def _richness(rec):
        try:
            return _state_richness(_read_json(TOPIC_STATE_DIR / rec["file"]) or {})
        except (json.JSONDecodeError, OSError):
            return _state_richness({})

    by_canon = {}
    for rec in records:
        by_canon.setdefault(rec["canonical"], []).append(rec)
    out = []
    for canon in sorted(by_canon):
        out.extend(sorted(by_canon[canon],
                          key=lambda r: (tuple(-v for v in _richness(r)), r["file"])))
    return out


def _acquire_inverted_locks(rec, sid):
    """Acquire the topic lock on BOTH the inverted source and the target
    canonical, lexicographically, BEFORE the free-check and the move
    (symmetric locking — the source is locked, not merely stale-reaped, so a
    live action on either side blocks rather than races).

    Returns (acquired_keys, blocked_key_or_None)."""
    import taskmanagement as _tm
    src_stem = rec["file"][: -len(".json")]
    keys = sorted({src_stem, rec["canonical"]})
    acquired = []
    for key in keys:
        res = _tm.acquire_lock(key, sid)
        if res.get("status") in ("ACQUIRED", "ALREADY_HELD_BY_SELF"):
            acquired.append(key)
        else:
            for k in acquired:
                _tm.release_lock(k, sid)
            return [], key
    return acquired, None


def reconcile_inverted(*, apply=False, slug=None, inverted_file=None,
                       resolve=None):
    """Backfill the inverted/swapped-key records onto their canonical names.

    Bulk form (no `slug`/`inverted_file`): walk every `find_inverted()` record,
    applying cases (i)/(ii) and logging case (iii) to the MERGE_REVIEW ledger.
    DRY-RUN unless `apply`.

    Resolution form (`slug` or `inverted_file` + `resolve`): force ONE ledger-
    flagged ambiguous record to a terminal move — the tie-break the bulk run
    refuses to guess, and what makes `find-mis-keyed` -> 0 reachable.
    Non-interactive: the decision is this CLI argument, never a prompt.

    Returns a structured dict; never raises for the happy path."""
    result = {"applied": bool(apply), "resolve": resolve, "actions": [],
              "merge_review": [], "skipped_locked": [], "errors": [],
              "counts": {"rename": 0, "redundant": 0, "ambiguous": 0,
                         "gone": 0, "resolved": 0}}

    if resolve is not None and resolve not in INVERTED_RESOLUTIONS:
        result["errors"].append(
            f"unknown --resolve {resolve!r}; expected one of "
            f"{list(INVERTED_RESOLUTIONS)}")
        return result

    records = find_inverted()

    # ---- resolution form -------------------------------------------------- #
    if slug or inverted_file:
        ledger = _read_merge_review_ledger()
        pending = {f: e for f, e in ledger.items() if not e.get("resolved")}
        if inverted_file:
            cands = [r for r in records if r["file"] == inverted_file]
            key_desc = f"file {inverted_file!r}"
        else:
            cands = [r for r in records if r["spine_slug"] == slug]
            key_desc = f"slug {slug!r}"
        # Validate against the LEDGER first: only a flagged ambiguous record is
        # resolvable, so a mistyped slug is rejected rather than acted on.
        flagged = [r for r in cands if r["file"] in pending]
        if not cands:
            # Obligation 4: a ledger entry whose inverted source is GONE (the
            # operator resolved it by hand) is treated as already-resolved and
            # purged — a stale entry must never false-block, and must never keep
            # find-mis-keyed from reading zero.
            # Match a stale entry by its RECORDED spine_slug, never by a
            # filename prefix: ledger keys are inverted filenames shaped
            # `<project>__<topic>.json`, so `f.startswith(slug + "__")` would
            # test the PROJECT segment against a requested TOPIC slug — and
            # `--slug Projects` (the literal name of the root project here)
            # would then falsely purge unrelated topics' pending entries.
            # `spine_slug` is recorded on every entry at creation, so it is the
            # only correct key.
            stale = [f for f in pending
                     if not (TOPIC_STATE_DIR / f).exists()
                     and (f == inverted_file
                          or (slug is not None
                              and pending[f].get("spine_slug") == slug))]
            if stale:
                for f in stale:
                    if apply:
                        _append_merge_review_ledger(
                            {"file": f, "resolved": True, "resolution": "already-gone",
                             "at": datetime.now(timezone.utc).isoformat()})
                    result["actions"].append({"op": "ledger-purge-stale", "file": f})
                    result["counts"]["gone"] += 1
                return result
            result["errors"].append(f"no inverted record matches {key_desc}")
            return result
        if not flagged:
            result["errors"].append(
                f"{key_desc} is not flagged MERGE_REVIEW — nothing to resolve "
                f"(the bulk run handles non-ambiguous records)")
            return result
        if len(flagged) > 1:
            result["errors"].append(
                f"{key_desc} is ambiguous across {len(flagged)} records "
                f"({[r['file'] for r in flagged]}) — re-run with "
                f"--file <inverted-filename> to name exactly one")
            return result
        if resolve is None:
            result["errors"].append(
                f"{key_desc} needs an explicit --resolve "
                f"{'|'.join(INVERTED_RESOLUTIONS)}")
            return result
        records = flagged

    records = _order_inverted_richest_first(records)

    now_ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    reaped_dir = TOPIC_STATE_DIR / "_reaped"
    sid = f"reconcile-inverted-{os.getpid()}"

    # Dry-run only: the canonical names claimed by earlier records in THIS walk,
    # so a preview of N records competing for one name matches what apply does.
    projected = {} if not apply else None

    for rec in records:
        case, canon_path, inverted_path = _classify_inverted_case(rec, projected)

        if case == "gone":
            result["counts"]["gone"] += 1
            result["actions"].append({"op": "already-resolved", "file": rec["file"]})
            continue

        # Bulk run: case (iii) is skipped IN PLACE and logged. No lock needed to
        # decide that, and taking one would be pointless contention.
        if case == "ambiguous" and resolve is None:
            result["counts"]["ambiguous"] += 1
            result["merge_review"].append(rec["file"])
            result["actions"].append(
                {"op": "merge-review", "file": rec["file"],
                 "canonical": f"{rec['canonical']}.json",
                 "why": "canonical exists and the inverted record is richer"})
            if apply:
                _append_merge_review_ledger(
                    {"file": rec["file"], "canonical": rec["canonical"],
                     "spine_slug": rec["spine_slug"], "resolved": False,
                     "at": datetime.now(timezone.utc).isoformat()})
            continue

        if not apply:
            result["counts"][case if resolve is None else "resolved"] += 1
            result["actions"].append(
                {"op": f"dry-run:{case}" if resolve is None else f"dry-run:{resolve}",
                 "file": rec["file"], "canonical": f"{rec['canonical']}.json"})
            if projected is not None and case == "rename":
                # this record would occupy the canonical name; the next claimant
                # of the same name must see it as taken
                try:
                    projected[canon_path.name] = _state_richness(
                        _read_json(inverted_path) or {})
                except (json.JSONDecodeError, OSError):
                    projected[canon_path.name] = _state_richness({})
            continue

        acquired, blocked = _acquire_inverted_locks(rec, sid)
        if blocked is not None:
            result["skipped_locked"].append({"file": rec["file"], "locked": blocked})
            continue
        import taskmanagement as _tm
        try:
            # Re-classify UNDER the lock: the free-check must not precede the
            # lock, or it is a check-then-act race.
            case, canon_path, inverted_path = _classify_inverted_case(rec)
            if case == "gone":
                result["counts"]["gone"] += 1
                continue

            _prep_reaped(reaped_dir)

            if case == "rename":
                ok, mode = _place_atomically(inverted_path, canon_path)
                if not ok:
                    result["errors"].append(
                        f"{rec['file']}: cross-fs copy failed verification — "
                        f"aborted, both files left in place")
                    continue
                result["counts"]["rename"] += 1
                result["actions"].append(
                    {"op": "rename", "from": rec["file"],
                     "to": canon_path.name, "mode": mode})
                if mode == "copy-rename" and inverted_path.exists():
                    # Torn source from the cross-fs path: quarantine as a
                    # redundant duplicate, NEVER delete.
                    bak = reaped_dir / f"{rec['file']}.bak-{now_ts}"
                    inverted_path.rename(bak)
                    result["actions"].append(
                        {"op": "quarantine-torn-source", "file": rec["file"],
                         "to": f"_reaped/{bak.name}"})
                continue

            if case == "redundant" or resolve == "keep-canonical":
                bak = reaped_dir / f"{rec['file']}.bak-{now_ts}"
                inverted_path.rename(bak)     # same-fs atomic; no verify needed
                result["counts"]["redundant" if resolve is None else "resolved"] += 1
                result["actions"].append(
                    {"op": "quarantine-inverted", "file": rec["file"],
                     "to": f"_reaped/{bak.name}"})
                if resolve is not None:
                    _append_merge_review_ledger(
                        {"file": rec["file"], "resolved": True,
                         "resolution": "keep-canonical",
                         "at": datetime.now(timezone.utc).isoformat()})
                continue

            if resolve == "keep-inverted":
                # Strictly-ordered, FAIL-CLOSED two-step that never leaves the
                # canonical path empty:
                #   (1) COPY the incumbent canonical to _reaped/ and VERIFY it;
                #   (2) ONLY if verified, atomic rename the inverted OVER the
                #       canonical. POSIX rename replaces atomically, so the
                #       canonical path is occupied at every instant — by the
                #       incumbent before, by the inverted after, never nothing.
                bak = reaped_dir / f"{canon_path.name}.bak-{now_ts}"
                if not _verified_copy(canon_path, bak):
                    if bak.exists():
                        bak.unlink()
                    result["errors"].append(
                        f"{canon_path.name}: incumbent backup could not be "
                        f"verified — record ABORTED, both files left in place, "
                        f"canonical never overwritten")
                    continue
                ok, mode = _place_atomically(inverted_path, canon_path)
                if not ok:
                    result["errors"].append(
                        f"{rec['file']}: placement failed after a verified "
                        f"backup — aborted, both files still in place")
                    continue
                result["counts"]["resolved"] += 1
                result["actions"].append(
                    {"op": "keep-inverted", "file": rec["file"],
                     "to": canon_path.name,
                     "incumbent_backup": f"_reaped/{bak.name}", "mode": mode})
                _append_merge_review_ledger(
                    {"file": rec["file"], "resolved": True,
                     "resolution": "keep-inverted",
                     "incumbent_backup": bak.name,
                     "at": datetime.now(timezone.utc).isoformat()})
                continue
        finally:
            for k in acquired:                # obligation 1: never leak a lock
                _tm.release_lock(k, sid)

    return result


# --------------------------------------------------------------------------- #
# `_active.json` dead-entry prune + its paired restore (Cluster-B A1 / slice S1)
#
# `_active.json` is the one file this plan calls a SHARED LEDGER under active
# concurrent write: a single map {session_id: {topic_slug, active_project,
# updated}} that every `/work-start` mint and `/close` auto-register appends to.
# It gains a dead `plain-*` row per abandoned plain-intake session and never
# sheds one, so — unlike the inverted-key class, which cannot regrow — it needs a
# small STANDING pruner rather than a one-shot backfill.
#
# The prune and its undo ship TOGETHER (obligation 10). A rollback path that does
# not exist when the forward path lands is not a rollback path, and the operator
# documentation already names `restore-active` as the only sanctioned way to roll
# this file back.
#
# Four load-bearing properties, each earned rather than asserted:
#
#   * **The flock is the PRIMARY guard, never operator timing** (obligation 9).
#     S0 brought all four `_active.json` writer regions under `_active_lock()`,
#     so the exclusive lock taken here actually serializes against them. Running
#     in a quiet window remains sensible hygiene and is explicitly NOT relied on.
#   * **Liveness is decided by ACQUIRING each candidate's topic lock**, never by
#     the read-only `_lock_held_fresh` probe. That probe fails OPEN by its own
#     docstring (`except Exception: return False`), so a transient error reads as
#     "no lock" and would make a LIVE session's row prunable. This mirrors what
#     `reconcile_topic_identity(acquire_locks=True)` already does for records.
#   * **The backup is a VERIFIED COPY taken as the first action under the lock**
#     (obligation 5, invariants copy-path 3) — a copy rather than a rename
#     because renaming the live ledger aside would leave `_active.json` absent
#     until `write_active` completes, and `read_active()` callers are not under
#     the lock.
#   * **The restore is a LOGICAL MERGE, never a snapshot overwrite.** The prune
#     is deletion-only, so its exact inverse is addition-only.
#
# LOCK ORDER — topic locks FIRST, then `_active_lock()`. This is not arbitrary:
# the live path is `/work-start` acquiring the topic lock in the skill and only
# then reaching `create_topic` -> `_active_lock()`. Taking them in the opposite
# order here would invert the order against every live writer and could deadlock.
# The consequence is that the candidate set is computed from an UNLOCKED read;
# it is then re-derived from a fresh read under the lock and intersected with the
# locks actually held, so the unlocked read can only ever propose a candidate,
# never authorise a deletion.
# --------------------------------------------------------------------------- #

PRUNE_ACTIVE_LEDGER_NAME = "PRUNE_ACTIVE.ledger.jsonl"
ACTIVE_BACKUP_PREFIX = "_active.json.bak-"
ACTIVE_CORRUPT_PREFIX = "_active.json.corrupt-"

# Grounded against the lock-staleness primitive rather than picked: a session
# ROW outlives the lock that guards it, so the prune window is set far larger
# than `taskmanagement.DEFAULT_STALE_T` (3600s). Operator-overridable via
# `--prune-window` (obligation 3).
DEFAULT_PRUNE_WINDOW_SECONDS = 30 * 24 * 3600


def _prune_active_ledger_path():
    return TOPIC_STATE_DIR / "_reaped" / PRUNE_ACTIVE_LEDGER_NAME


def _read_prune_active_ledger():
    """Fold the append-only prune ledger to a list of records, oldest first.

    Parse-resilient by construction (obligation 7): one JSON object per line, so
    a halted append leaves at most ONE unparseable trailing line, which is
    skipped rather than bricking `restore-active`."""
    path = _prune_active_ledger_path()
    out = []
    if not path.exists():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    folded = {}                           # backup name -> merged record
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue                      # torn/partial append — skip, never raise
        if not (isinstance(rec, dict) and rec.get("backup")):
            continue
        name = rec["backup"]
        if name in folded:
            folded[name].update(rec)      # a later line supersedes an earlier
        else:
            folded[name] = dict(rec)
            out.append(folded[name])      # first appearance fixes the order
    return out


def _committed_prune_records(ledger=None):
    """The ledger rows for prunes that provably COMPLETED.

    A prune writes its row in TWO phases — an unconfirmed row before the
    mutation, a confirming row after it — so a record is only authoritative once
    `committed` is true. See `prune_active` for why neither single-phase
    ordering is safe.

    Rows written before the two-phase scheme existed carry no `committed` key at
    all. Those are treated as COMMITTED: they were written after a successful
    write under the old append-after ordering, so their prune did complete.
    Reading absence as "unconfirmed" would retroactively hide every historical
    backup from `restore-active`'s default resolution."""
    ledger = _read_prune_active_ledger() if ledger is None else ledger
    return [r for r in ledger if r.get("committed", True)]


def _append_prune_active_ledger(rec):
    """Append ONE prune record as a single fsynced line (obligation 7).

    This is what makes a backup traceable to the prune that produced it (F9):
    without it, `restore-active` could only guess which backup pairs with which
    prune, and restoring an older one would silently undo prunes it was never
    meant to touch."""
    _prep_reaped()
    line = json.dumps(rec, sort_keys=True) + "\n"
    with open(_prune_active_ledger_path(), "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _parse_updated(value):
    """Parse an `_active.json` row's `updated` stamp. None when unusable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def classify_active_entries(active, *, window_seconds=None, now=None):
    """Partition `_active.json` rows into prune CANDIDATES and keepers. Pure.

    A row is a candidate when BOTH hold:
      * its `topic_slug` starts with `plain-` — the synthetic per-session slug
        `/work-start` mints for a plain TODO intake; and
      * its `updated` stamp is older than the prune window.

    The third condition — that no live session holds the topic's lock — is
    deliberately NOT decided here. `_lock_held_fresh` fails OPEN, so consulting
    it would let a transient error mark a LIVE row prunable. Liveness is instead
    proved by `prune_active` ACQUIRING the row's topic lock; a row whose lock
    cannot be taken is never pruned.

    A row with a missing or unparseable `updated` is KEPT, not pruned: an
    unreadable age is not evidence of death.

    Legacy non-`plain-*` rows are intentionally left alone — a documented
    residual, not a script failure (obligation 6).
    """
    window = (DEFAULT_PRUNE_WINDOW_SECONDS if window_seconds is None
              else int(window_seconds))
    now = now or datetime.now(timezone.utc)
    candidates, kept = {}, {}
    for sid, entry in (active or {}).items():
        if not isinstance(entry, dict):
            kept[sid] = entry
            continue
        slug = str(entry.get("topic_slug") or "")
        updated = _parse_updated(entry.get("updated"))
        if not slug.startswith("plain-") or updated is None:
            kept[sid] = entry
            continue
        if (now - updated).total_seconds() >= window:
            candidates[sid] = entry
        else:
            kept[sid] = entry
    return candidates, kept


def _backup_active_verified(reaped_dir, now=None):
    """Back `_active.json` up to `_reaped/` as a VERIFIED COPY. The first
    MUTATING action under the lock (obligation 5 — see `prune_active`'s
    docstring, which is authoritative for the precise wording and for why the
    non-mutating steps that precede it do not weaken the property). Returns the
    backup Path, or None on failure — which callers treat as fail-closed: abort,
    mutate nothing.

    A COPY rather than a rename, deliberately: renaming the live ledger aside
    would leave `_active.json` absent until `write_active` completed, and
    `read_active()` callers do not hold this lock. This is copy-path 3 of the
    invariants' enumeration, and it is verified for the reason every copy here
    is — a copy genuinely can partially write where a rename cannot."""
    now = now or datetime.now(timezone.utc)
    src = _active_path()
    if not src.exists():
        return None
    dest = Path(reaped_dir) / (ACTIVE_BACKUP_PREFIX + now.strftime("%Y%m%d%H%M%S"))
    n = 1
    while dest.exists():                  # never silently overwrite a backup
        dest = dest.with_name(dest.name.split(".dup")[0] + f".dup{n}")
        n += 1
    if not _verified_copy(src, dest):
        try:
            if dest.exists():
                dest.unlink()             # remove only the failed copy we made
        except OSError:
            pass
        return None
    return dest


def prune_active(*, apply=False, window_seconds=None, now=None,
                 session_id=None):
    """Prune dead `plain-*` rows from `_active.json`. DRY-RUN unless apply=True.

    Flow (the ordering is the safety property, not a style choice):
      1. UNLOCKED read -> propose candidates by slug + age.
      2. Acquire each candidate's TOPIC lock in sorted order (proving liveness,
         and matching the live topic->active lock order). A row whose lock is
         held by a live session is dropped from the set, never pruned.
      3. Take `_active_lock()`; RE-READ `_active.json` fresh; re-apply the
         classifier; prune only rows that are still dead AND whose lock we hold.
      4. Verified-copy backup as the first MUTATING action under the lock;
         abort if it fails or cannot be verified. (Obligation 5 says "first
         action"; what precedes it here is the fresh read, the re-classify, the
         nothing-to-prune early return and `_prep_reaped` — all non-mutating or
         confined to `_reaped/`. The property obligation 5's round-14 correction
         was protecting is that the snapshot is taken INSIDE the lock, so it is
         faithful to what is about to change; that holds exactly, since no other
         writer can touch `_active.json` while the lock is held.)
      5. Atomic `write_active` of the survivors, then record the prune in the
         ledger so the backup is traceable to it.
      6. Release every lock in a `finally` (obligation 1).

    Returns a structured dict; never raises for the happy path."""
    result = {
        "applied": bool(apply),
        "window_seconds": (DEFAULT_PRUNE_WINDOW_SECONDS if window_seconds is None
                           else int(window_seconds)),
        "candidates": [], "pruned": [], "skipped_locked": [],
        "skipped_key_moved": [],
        "backup": None, "total_before": 0, "total_after": 0,
        # Whether the prune's ledger row was CONFIRMED. `reason == "pruned"` and
        # `confirmed` are NOT the same predicate: a prune whose confirming
        # append failed really did prune (so `reason` is "pruned"), but its row
        # is still uncommitted, so `restore-active` will not default to its
        # backup. Anything telling the operator how to roll back must branch on
        # THIS, not on `reason`.
        "confirmed": False,
        "reason": None, "errors": [],
    }
    now = now or datetime.now(timezone.utc)

    # --- 1. unlocked read: PROPOSE candidates ------------------------------
    try:
        active = read_active()
    except (json.JSONDecodeError, OSError) as exc:
        result["reason"] = "active-unreadable"
        result["errors"].append(f"_active.json unreadable: {exc}")
        return result
    result["total_before"] = len(active)
    candidates, _kept = classify_active_entries(
        active, window_seconds=result["window_seconds"], now=now)
    result["candidates"] = sorted(candidates)
    if not candidates:
        result["reason"] = "nothing-to-prune"
        result["total_after"] = len(active)
        return result

    if not apply:
        # Dry-run stops here deliberately: it neither takes the harness-wide
        # flock nor acquires a topic lock, so a report can never block a live
        # `/work-start`. The trade-off is stated rather than hidden — a dry-run
        # count may include a row whose lock an apply would find live.
        result["reason"] = "dry-run"
        result["total_after"] = len(active) - len(candidates)
        return result

    # --- 2. acquire each candidate's TOPIC lock (liveness by acquisition) ---
    import taskmanagement as _tm
    sid = session_id or f"prune-active-{os.getpid()}"
    acquired = []                         # lock keys held, for the finally
    held_sids = set()
    locked_key_by_sid = {}                # sid -> the key we actually locked
    try:
        # Acquire in LOCK-KEY order, not session-id order. The keys are what is
        # actually locked, so ordering by them is what makes two concurrent
        # acquirers agree on a sequence — the same discipline
        # `reconcile_topic_identity`'s bulk path uses (it walks twins already
        # sorted by project). Sorting by session id would order the acquisitions
        # by something that is not the lock, so two prunes over overlapping
        # candidate sets could take the same two locks in opposite orders.
        # `prune-active` is a supervised operator verb rather than a concurrent
        # background writer, so this is latent rather than live — but the fix is
        # one sort key, and a latent deadlock left in place is a defect waiting
        # for the day someone automates the verb.
        ordered = sorted(
            ((_lock_key(str(e.get("topic_slug") or ""),
                        str(e.get("active_project") or "")), csid)
             for csid, e in candidates.items()),
            key=lambda pair: (pair[0], pair[1]))
        for key, csid in ordered:
            try:
                res = _tm.acquire_lock(key, sid)
                status = res.get("status")
            except Exception as exc:      # noqa: BLE001 — fail CLOSED, never open
                result["skipped_locked"].append(csid)
                result["errors"].append(f"{csid}: lock probe failed ({exc}) — kept")
                continue
            if status in ("ACQUIRED", "ALREADY_HELD_BY_SELF"):
                acquired.append(key)
                held_sids.add(csid)
                locked_key_by_sid[csid] = key
            else:
                # HELD_BY_OTHER / RACE / anything else -> treat as LIVE.
                result["skipped_locked"].append(csid)

        if not held_sids:
            result["reason"] = "all-candidates-live"
            result["total_after"] = result["total_before"]
            return result

        # --- 3..5. the whole read-prune-write under the ONE `_active.json` lock
        with _active_lock():
            try:
                fresh = read_active()
            except (json.JSONDecodeError, OSError) as exc:
                result["reason"] = "active-unreadable"
                result["errors"].append(f"_active.json unreadable under lock: {exc}")
                return result

            fresh_candidates, _ = classify_active_entries(
                fresh, window_seconds=result["window_seconds"], now=now)
            # Prune ONLY rows that are (a) still dead on the fresh read,
            # (b) covered by a lock we actually hold, and (c) whose lock key
            # RE-DERIVED from the fresh row still equals the key we locked.
            #
            # (c) closes a narrow hole in "liveness by acquisition": the keys
            # were derived from the UNLOCKED step-1 snapshot, and
            # `_repoint_active` changes a row's `active_project` WITHOUT
            # refreshing `updated`. So a concurrent repoint could leave us
            # holding `slug__oldproj` while the row now reads `slug__newproj` —
            # and a live holder of the NEW key would not have blocked us. If the
            # key moved, we never held the row's real lock, so we do not prune
            # it.
            doomed = []
            for s in sorted(fresh_candidates):
                if s not in held_sids:
                    continue
                row = fresh.get(s) or {}
                current_key = _lock_key(str(row.get("topic_slug") or ""),
                                        str(row.get("active_project") or ""))
                if current_key != locked_key_by_sid.get(s):
                    # A DISTINCT bucket, not `skipped_locked`. The code observed
                    # a changed `active_project`, not a live holder — and the
                    # CLI renders `skipped_locked` as "a live session holds the
                    # topic lock", which would be a claim this code never made.
                    result["skipped_key_moved"].append(s)
                    continue
                doomed.append(s)
            if not doomed:
                result["reason"] = "nothing-to-prune"
                result["total_before"] = len(fresh)
                result["total_after"] = len(fresh)
                return result

            try:
                reaped_dir = _prep_reaped()       # F11 / obligation pre-flight
            except (PermissionError, OSError) as exc:
                result["reason"] = "reaped-unwritable"
                result["errors"].append(f"quarantine pre-flight failed: {exc}")
                return result

            backup = _backup_active_verified(reaped_dir, now=now)
            if backup is None:
                # FAIL-CLOSED (obligation 5): no verified backup, no rewrite.
                result["reason"] = "backup-failed"
                result["errors"].append(
                    "_active.json backup missing or unverifiable — prune "
                    "ABORTED, the ledger was not modified")
                result["total_before"] = len(fresh)
                result["total_after"] = len(fresh)
                return result
            result["backup"] = backup.name

            survivors = {s: e for s, e in fresh.items() if s not in set(doomed)}

            # F9: bind this backup to THIS prune, so `restore-active` can pair
            # them and refuse a superseded one.
            #
            # TWO-PHASE, because NEITHER single ordering is safe and each fails
            # silently in its own direction:
            #   * append-AFTER  — a crash between the write and the append leaves
            #     a prune that DID happen with no record. A later default
            #     `restore-active` then resolves to the PREVIOUS prune's backup
            #     and re-adds rows this prune removed: a silent over-restore.
            #   * append-BEFORE — a crash between the append and the write leaves
            #     a record for a prune that did NOT happen. That row becomes the
            #     ledger tail, so the previous REAL backup is refused as
            #     "superseded" on a reason that is false, and the default
            #     restore resolves to the orphan and no-ops while reporting
            #     success. (This was tried first and is the defect an
            #     independent verifier caught: the orphan is NOT the harmless
            #     no-op it looks like.)
            #
            # So: write an UNCONFIRMED row first, mutate, then confirm. A crash
            # anywhere leaves an unconfirmed row, which is inert — it never
            # drives a default restore and never supersedes a real backup — and
            # is SURFACED by the CLI (`_print_unconfirmed_prunes`) rather than
            # silently believed.
            #
            # Honest bound on that advantage: for a HARD kill between the write
            # and the confirming append the rows really are gone while the row
            # reads unconfirmed, so the committed tail is the previous backup —
            # behaviourally the same as append-after. Two-phase is still
            # strictly better (it closes the append-before hole with no
            # regression, and the state is now REPORTED instead of silent), but
            # it does not make that case recover itself.
            ledger_row = {
                "backup": backup.name,
                "at": now.isoformat(),
                "pruned": doomed,
                "count": len(doomed),
                "window_seconds": result["window_seconds"],
                "total_before": len(fresh),
                "total_after": len(survivors),
            }
            try:
                _append_prune_active_ledger({**ledger_row, "committed": False})
            except OSError as exc:
                # Symmetric with the write below: a disk failure here must not
                # escape as a traceback either. Nothing has been mutated yet.
                result["reason"] = "ledger-write-failed"
                result["errors"].append(
                    f"could not record the prune ({exc}) — ABORTED before "
                    f"mutating; the backup {backup.name} is on disk and "
                    f"_active.json is untouched")
                result["total_before"] = len(fresh)
                result["total_after"] = len(fresh)
                return result

            try:
                write_active(survivors)
            except OSError as exc:
                # An ordinary write failure (ENOSPC / EACCES / read-only fs) is
                # not a crash and must not escape as a traceback: `write_active`
                # is tmp+rename, so `_active.json` is untouched, and the row
                # stays unconfirmed.
                result["reason"] = "write-failed"
                result["errors"].append(
                    f"_active.json rewrite failed ({exc}) — nothing was pruned; "
                    f"the ledger row for {backup.name} is left UNCONFIRMED")
                result["total_before"] = len(fresh)
                result["total_after"] = len(fresh)
                return result

            try:
                _append_prune_active_ledger({**ledger_row, "committed": True})
                result["confirmed"] = True
            except OSError as exc:
                # The prune DID happen; only its confirmation failed. Report it
                # as the unconfirmed state it is rather than as a clean success,
                # and never as a traceback.
                result["errors"].append(
                    f"the prune completed but could not be confirmed in the "
                    f"ledger ({exc}) — its row stays UNCONFIRMED, so "
                    f"restore-active will not default to {backup.name}; pass "
                    f"that name explicitly with --allow-superseded to roll back")

            result["pruned"] = doomed
            result["total_before"] = len(fresh)
            result["total_after"] = len(survivors)
            result["reason"] = "pruned"
    finally:
        for k in acquired:                # obligation 1: never leak a lock
            try:
                _tm.release_lock(k, sid)
            except Exception:             # noqa: BLE001 — best-effort release
                pass
    return result


def _resolve_restore_backup(backup=None, *, allow_superseded=False):
    """Resolve + validate the backup to restore from, BEFORE any lock is taken.

    Returns (path, ledger_record, error). Validating first is deliberate: taking
    `bookkeeping_lock` only to discover unusable input needlessly holds the
    critical section (STABILIZED LOCK PRINCIPLE). This does not conflict with
    obligation 5 — that requires snapshotting LIVE state inside the lock,
    whereas this reads a static operator-supplied file.

    F9 — a backup OLDER than the last prune is REFUSED by default. The prune is
    deletion-only and each backup pairs with exactly one prune, so restoring a
    superseded backup would re-add rows that LATER prunes deliberately removed,
    silently undoing prunes this restore was never meant to touch."""
    # Only CONFIRMED prunes are authoritative. An unconfirmed row records a
    # prune that may never have mutated anything; letting one drive the default
    # resolution, or count as the tail for supersession, is what makes a crashed
    # prune silently block recovery.
    ledger = _committed_prune_records()
    reaped = TOPIC_STATE_DIR / "_reaped"

    if backup is None:
        if not ledger:
            return None, None, ("no completed prune has been recorded, so there "
                                "is no backup to restore by default — pass one "
                                "explicitly")
        rec = ledger[-1]
        path = reaped / rec["backup"]
    else:
        path = Path(backup)
        if not path.is_absolute() and not path.exists():
            path = reaped / path.name
        rec = next((r for r in ledger if r.get("backup") == path.name), None)

    if not path.exists():
        return None, rec, f"backup not found: {path}"

    if rec is None and not allow_superseded:
        # Distinguish "no row at all" from "a row that exists but is
        # unconfirmed". The refusal is right in both cases, but telling an
        # operator their backup "is not recorded" when it IS recorded sends them
        # looking for the wrong thing.
        unconfirmed = next(
            (r for r in _read_prune_active_ledger()
             if r.get("backup") == path.name and not r.get("committed", True)),
            None)
        if unconfirmed is not None:
            return None, None, (
                f"{path.name} is recorded but its prune was never CONFIRMED — "
                f"it either failed before mutating (nothing to restore) or was "
                f"killed after mutating. Compare it against the live ledger "
                f"first; pass --allow-superseded to restore it anyway.")
        return None, None, (
            f"{path.name} is not recorded in the prune ledger, so it cannot be "
            f"paired with a prune. Pass --allow-superseded to restore it anyway.")

    if rec is not None and ledger and rec is not ledger[-1] and not allow_superseded:
        latest = ledger[-1]
        return None, rec, (
            f"{path.name} is SUPERSEDED — {latest['backup']} is the backup of "
            f"the most recent prune ({latest.get('at')}). Restoring an older "
            f"backup would re-add rows that later prune(s) removed. Restore "
            f"{latest['backup']}, or pass --allow-superseded if you really "
            f"intend to undo those prunes too.")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return None, rec, f"backup is not readable JSON: {exc}"
    if not isinstance(data, dict):
        return None, rec, f"backup is not a JSON object: {path.name}"
    return path, rec, None


def restore_active(backup=None, *, apply=False, allow_superseded=False,
                   now=None):
    """Roll `_active.json` back from a prune backup by LOGICAL MERGE.

    DRY-RUN unless apply=True — every sibling verb on this CLI requires
    `--apply`, and a recovery verb is the last place to break that expectation.

    The merge, not a snapshot overwrite (obligation 10): under the lock, read the
    CURRENT ledger and the backup, and write

        current  U  {backup rows whose session id is absent from current}

    i.e. re-add the pruned rows and keep every row that exists now; a row present
    in both is left at its CURRENT value, never reverted. This is the CORRECT
    inverse rather than a safer one — `prune_active` is deletion-only, so its
    exact inverse is addition-only, and a blind snapshot rewrite would revert
    bindings the prune never touched.

    ONE sanctioned exception (edge case a): when the CURRENT `_active.json` is
    unparseable there are no readable rows to preserve, so the class of loss the
    merge prevents cannot occur and the backup is written whole. The corrupt
    incumbent bytes are quarantined to `_reaped/` FIRST — as a VERIFIED COPY,
    not a rename: a rename would leave the live path absent until `write_active`
    landed, and this plan's never-leave-a-live-path-empty rule applies to
    `_active.json` above all. That makes it a FOURTH verified-copy path beyond
    the invariants' enumeration of three, recorded here rather than smuggled in.
    """
    result = {
        "applied": bool(apply), "backup": None, "restored": [],
        "already_present": [], "total_before": 0, "total_after": 0,
        "mode": None, "quarantined": None, "reason": None, "errors": [],
    }
    now = now or datetime.now(timezone.utc)

    path, rec, err = _resolve_restore_backup(
        backup, allow_superseded=allow_superseded)
    if err:
        result["reason"] = "invalid-backup"
        result["errors"].append(err)
        return result
    result["backup"] = path.name
    backup_data = json.loads(path.read_text(encoding="utf-8"))

    if not apply:
        # Dry-run reports against an UNLOCKED read of the current ledger. It
        # takes no lock, so it can never block a live writer; the count it
        # reports is a faithful preview only if nothing changes in between.
        try:
            current = read_active()
            parsed = True
        except (json.JSONDecodeError, OSError):
            current, parsed = {}, False
        result["mode"] = "merge" if parsed else "snapshot (current file unparseable)"
        result["total_before"] = len(current)
        result["restored"] = sorted(s for s in backup_data if s not in current)
        result["already_present"] = sorted(s for s in backup_data if s in current)
        result["total_after"] = (len(current) + len(result["restored"]) if parsed
                                 else len(backup_data))
        result["reason"] = "dry-run"
        return result

    with _active_lock():
        # RE-VALIDATE SUPERSESSION UNDER THE LOCK.
        #
        # The pre-lock validation above is a fast fail on unusable input, and it
        # stays — holding the critical section to discover a missing file is
        # what the STABILIZED LOCK PRINCIPLE forbids. But supersession is NOT a
        # property of the operator's static file; it is a property of the
        # LEDGER'S TAIL, which is dynamic. Deciding it before the lock and never
        # re-checking is a check-then-act race: a `prune-active --apply` that
        # completes in the window between validation and lock acquisition
        # supersedes the chosen backup, and the merge below would then silently
        # re-add rows that newer prune deliberately removed — the exact harm the
        # refuse-superseded rule exists to prevent, reached WITHOUT the operator
        # ever passing `--allow-superseded`.
        #
        # This is the codebase's own stated discipline for the shape
        # (`bookkeeping-model.md` §4b: "re-check main hasn't advanced before
        # acting", rather than holding a longer lock).
        if not allow_superseded:
            fresh_ledger = _committed_prune_records()
            if fresh_ledger and fresh_ledger[-1].get("backup") != path.name:
                latest = fresh_ledger[-1]
                result["reason"] = "superseded-during-restore"
                result["errors"].append(
                    f"{path.name} was SUPERSEDED while this restore was "
                    f"starting — a prune completed in the meantime and "
                    f"{latest.get('backup')} is now the most recent backup "
                    f"({latest.get('at')}). Nothing was written. Re-run to "
                    f"restore the current latest, or pass --allow-superseded "
                    f"if you intend to undo that prune too.")
                return result

        try:
            reaped_dir = _prep_reaped()
        except (PermissionError, OSError) as exc:
            result["reason"] = "reaped-unwritable"
            result["errors"].append(f"quarantine pre-flight failed: {exc}")
            return result

        live = _active_path()
        try:
            current = json.loads(live.read_text(encoding="utf-8")) if live.exists() else {}
            if not isinstance(current, dict):
                raise ValueError("_active.json is not a JSON object")
            parsed = True
        except (ValueError, OSError):
            parsed = False

        if parsed:
            result["mode"] = "merge"
            result["total_before"] = len(current)
            merged = dict(current)
            for sid_key, entry in backup_data.items():
                if sid_key in merged:
                    result["already_present"].append(sid_key)
                else:
                    merged[sid_key] = entry
                    result["restored"].append(sid_key)
        else:
            # Sanctioned snapshot fallback — quarantine the corrupt bytes first.
            result["mode"] = "snapshot (current file unparseable)"
            quarantine = reaped_dir / (
                ACTIVE_CORRUPT_PREFIX + now.strftime("%Y%m%d%H%M%S"))
            if live.exists() and not _verified_copy(live, quarantine):
                result["reason"] = "quarantine-failed"
                result["errors"].append(
                    "could not take a verified copy of the corrupt _active.json "
                    "— restore ABORTED, nothing overwritten")
                return result
            result["quarantined"] = quarantine.name if live.exists() else None
            result["total_before"] = 0
            merged = dict(backup_data)
            result["restored"] = sorted(backup_data)

        try:
            write_active(merged)
        except OSError as exc:
            # Symmetric with the prune's write site: an ordinary ENOSPC/EACCES
            # is not a crash and must not surface as a traceback. `write_active`
            # is tmp+rename, so the live ledger is untouched — and on the
            # snapshot path the corrupt incumbent has already been quarantined,
            # so nothing is lost either way.
            result["reason"] = "write-failed"
            result["errors"].append(
                f"_active.json rewrite failed ({exc}) — nothing was restored; "
                f"the live ledger is unchanged")
            result["restored"] = []
            result["total_after"] = result["total_before"]
            return result

        result["restored"] = sorted(result["restored"])
        result["already_present"] = sorted(result["already_present"])
        result["total_after"] = len(merged)
        result["reason"] = "restored"
    return result


# ---------------------------------------------------------------------------
# Topic-level domain logic
# ---------------------------------------------------------------------------

def create_topic(session_id, project_slug, topic_slug=None,
                 intake_source=None, todo_line_ref=None,
                 thought_file_path=None):
    """Create a new topic state file + update the _active pointer.

    create-topic SESSION_ID PROJECT_SLUG [TOPIC_SLUG]
                 [--intake-source plain|thought] [--todo-line-ref <path>:<line>]
                 [--thought-file-path <spine>.md]

    Identity (A1, topic-identity-generator-closure S1). The work-name half comes
    from ONE of two places and never from the ambient environment:

      * an explicit ``topic_slug`` — a caller's CLAIM, which always wins; or
      * ``thought_file_path`` — the work's OWN artifact, via
        ``canonical_topic_for_spine``.

    With neither, this RAISES ``ValueError`` naming what is missing. It does not
    fall back to the working directory: that fallback is what minted records
    whose stored halves contradict their own filename.

    **``thought_file_path`` is used, not recorded.** It derives the key and seeds
    the project-half spine hint; it is NOT persisted — ``new_state`` still writes
    ``thought_file_path: None``, exactly as before this slice. So a topic created
    through the artifact door is correctly keyed but *spineless*: ``bound_topic``
    cannot resolve it, and it classifies as ``unresolvable``. Bind the spine
    afterwards with ``set-thought-file`` if the topic needs one. Record shape is
    deliberately left unchanged here — altering it would move freshly-minted
    records between the very buckets this plan measures.

    Replaces the legacy `classify --new-topic` flow (Slice F): the engine no
    longer carries a `classification` concept; topics are just topics. Research
    and exploration workflows live in standalone skills (/research,
    /extract-knowledge, /clarification Step 6).

    Additive fields (work-start-todo-no-thought, 2026-05-24): intake_source +
    todo_line_ref persist on the topic-state JSON; absent ⇒ None on read.
    intake_source discriminates between thought-bound and plain TODO sessions;
    todo_line_ref carries '<path>:<line>' for plain mode at acquisition time.
    """
    now = datetime.now(timezone.utc).isoformat()

    # A1 (topic-identity-generator-closure S1) — DERIVE-OR-REFUSE.
    # This block used to read `topic_slug = _derive_topic_slug()`, which returned
    # `Path.cwd().name`: when a caller did not name the work, the AMBIENT working
    # directory filled the work-name half. That is the generator this slice
    # closes — it is why records exist whose stored `topic_slug` contradicts
    # their own filename, why the later lookup misses the real record, and why a
    # second empty record then gets minted.
    #
    # The rule, in the Guiding Policy's own terms: derive identity from the
    # artifact, never from the ambient environment; where nothing resolves,
    # refuse rather than substitute. Scope, honestly: that holds for THIS
    # function. It is not a module-wide property — `_autobind_slugs_from_worktree`
    # still derives a candidate slug from a worktree directory basename, is
    # deliberately left alone (A1), and reaches this function through the
    # explicit-claim parameter below. That route can also MINT, not only rebind:
    # when `_ensure_canonical_record` defers to a fresh lock it returns False,
    # no record is found at the canonical key, and a blank record keyed on the
    # directory basename is created. Its own call site calls itself the
    # "idempotent REBOUND path", which is true in the common case and not in
    # that corner.
    #
    # THREE further cracks in the sibling half, all PRE-EXISTING and all left
    # alone here because none is A1's or A2's to close — recorded so the scope
    # of what this slice fixed is not read as wider than it is:
    #   * the explicit `topic_slug`/`project_slug` positionals get no character
    #     validation at all, so `.`/`..`/`/` in either reaches `_topic_path`;
    #   * **`__` included** — the separator guard below lives in
    #     `canonical_topic_for_spine`, so it binds the DERIVED half only. An
    #     explicit `create-topic SID PROJ foo__bar` still produces
    #     `foo__bar__baz.json`, colliding with topic `foo` in a project named
    #     `bar__baz`, and `_classify_records` resolves that by `rsplit("__", 1)`
    #     — i.e. by guessing. Named separately from the `.`/`..` cases because
    #     its consequence is worse than a path nuisance: the idempotency guard
    #     rebinds the session onto an unrelated topic's record. Not fixed here
    #     because validating the explicit path is a different question from
    #     deriving the implicit one, and any rule added there touches every
    #     shipped caller and every live slug;
    #   * a RELATIVE `thought_file_path` is passed unresolved to
    #     `canonical_project_for_spine`, whose `realpath` resolves it against the
    #     cwd — so the PROJECT half can still depend on where the CLI was run,
    #     even though the work-name half no longer can.
    #
    # ORDER IS LOAD-BEARING — an explicit `topic_slug` outranks the artifact.
    # The banned thing is ambient substitution, not explicit declaration: a
    # caller naming a TOPIC_SLUG is making a CLAIM, whereas the environment
    # leaking in is not. Live `plain-<sid8>` topics legitimately carry an
    # artifact whose slug differs from their key, so preferring the artifact
    # over the declaration would refuse exactly those.
    #
    # Blank-is-absence, NOT `topic_slug is None`. The pre-S1 code tested `is
    # None` and A1's rewrite inherited it, which left `create-topic SID PROJ ""`
    # (a shell expanding an empty variable) treated as an EXPLICIT claim: it
    # outranks the artifact, skips the derivation entirely, and mints a record
    # with an empty work name. A blank string is not a name a caller chose — it
    # is the absence of one wearing the shape of a claim, so it takes the
    # derive-or-refuse path with everything else that has no work name.
    #
    # `.strip()` on the TEST only, never on the value: a whitespace-only slug is
    # just as absent as an empty one (`"   "` is truthy, so a bare `not
    # topic_slug` misses it — which this file's own test caught), but a real slug
    # is passed through byte-for-byte rather than silently normalised.
    if topic_slug is not None and not isinstance(topic_slug, str):
        # Type-safety, so the contract this function documents is the one it
        # keeps. Without it the `.strip()` below raises AttributeError — not the
        # documented ValueError, and with no message naming what is wrong.
        #
        # SCOPE, against the REAL pre-S1 baseline — stated on the third attempt,
        # because the first understated it and the second measured it against the
        # wrong code. Pre-S1 `create_topic` had no `topic_slug or ""` and no
        # `.strip()` at all; it tested `is None` and otherwise passed the value
        # straight into the record-name f-string (an implicit `str()`) and into
        # the record body, which serializes natively for an int/dict/list and
        # falls back to `json.dumps(default=str)` for a `Path`.
        # So in the true baseline NO non-string ever raised — every one of them,
        # truthy or falsy, `int` or `dict` or `Path`, silently minted a
        # stringified record. (The truthy-only AttributeError described by the
        # previous version of this comment belonged to an intermediate draft of
        # THIS slice, not to shipped code.)
        #
        # The widening is therefore total, not partial: every non-None non-str
        # that previously "worked" now refuses. That is deliberate — minting a
        # record named `123` or `widget` -from-a-Path is substitution, and `str()`
        # coercion is the same thing one step later. Neither shipped caller
        # passes anything but str-or-None, so nothing in tree changes.
        raise ValueError(
            "create_topic: topic_slug must be a string or None, got "
            f"{type(topic_slug).__name__}. Refusing rather than coercing a "
            "non-name into the work-name position."
        )
    if not (topic_slug or "").strip():
        if thought_file_path is None:
            raise ValueError(
                "create_topic: cannot derive the topic slug. No TOPIC_SLUG was "
                "given and no --thought-file-path was supplied, so there is no "
                "artifact to derive the work name from. Pass one of them — the "
                "working directory is not a source of identity."
            )
        topic_slug = canonical_topic_for_spine(thought_file_path)
        if topic_slug is None:
            # One message for every None-returning cause, because the causes are
            # one rule: the value does not yield a usable work name. Naming only
            # the `.md` cause (as an earlier revision did) reads as wrong advice
            # for an empty value, a `..md` stem, or a `__`-bearing one.
            raise ValueError(
                "create_topic: cannot derive a topic slug from "
                f"--thought-file-path {thought_file_path!r}. It must name a "
                "Markdown artifact ('.md') whose stem, after the TYPE and "
                "timestamp suffixes are stripped, is a usable work name — not "
                "blank, not '.'/'..', and not containing '__' (the record "
                "filename separator). Otherwise name the work explicitly with "
                "TOPIC_SLUG."
            )

    # Correct-key-before-mint (S2/A2): the project half of the identity is
    # DERIVED from the topic's spine FS location through the single canonical
    # locus — never trusted from a caller-supplied arg that could disagree (the
    # __Root/__Projects twin source). Spine-less plain / worktree-origin topics
    # (no passed spine AND no twin records a spine) keep the caller arg as today.
    _orig_project = project_slug
    _spine_hint = thought_file_path or _find_recorded_spine_for_slug(topic_slug)
    if _spine_hint is not None:
        _derived = canonical_project_for_spine(_spine_hint)
        if _derived:
            project_slug = _derived
            if _derived != _orig_project:
                # a mis-keyed twin may hold the real data — rename it onto the
                # canonical key BEFORE the idempotency read below, so we rebind
                # to real state instead of minting a blank record and orphaning
                # the rich twin (respects a fresh lock; defers to bulk reconcile).
                _ensure_canonical_record(topic_slug, _derived)

    # F1 (Cluster-B S0, operator-approved carve-out widening 2026-08-17).
    # This read USED to sit at the top of the function, above the
    # `_ensure_canonical_record` call. That ordering was a lost update no lock
    # could fix: `_ensure_canonical_record` renames the state file away and then
    # calls `_repoint_active`, which does its own read-modify-write and LANDS the
    # repoint on disk — and the write exits below would then overwrite that
    # freshly-landed repoint with a snapshot taken before it happened, reverting
    # the affected sessions to a project whose record no longer exists.
    # `bookkeeping_lock` is re-entrant, so wrapping both regions prevents the
    # deadlock but NOT the loss; only reading after the repoint does. Nothing
    # between the old position and here touches `active` — the first use is the
    # idempotency guard immediately below — so this is a pure statement move.
    #
    # Region 2 of 4. ONE region, TWO write exits (rebind and fresh-mint); the
    # lock therefore opens here, at the read, and must cover both exits.
    with _active_lock():
        return _create_topic_locked(
            session_id, project_slug, topic_slug,
            intake_source, todo_line_ref, now,
        )


# S3 / A3 — the landing of the resolver fix. A topic-state record `created`
# BEFORE this instant could only ever have received `project_root: null` from
# `_resolve_project_root` (it returned None for every cwd), so for such a record a
# null root is the defect unless the record was deliberately worktree-routed.
# Records created at or after it get a real answer, so their null is a decision.
# ISO 8601 UTC; compared lexicographically against the record's `created`.
PROJECT_ROOT_FIX_LANDED = "2026-09-20T15:45:00+00:00"


def _project_root_for_new_topic():
    """The `project_root` a NEW topic record stores, with the worktree rule and
    its fail-safe failure direction (S3 / A3). Returns a Path or None.

      resolver None                 -> None   (outside the tree)
      worktree status True          -> None   (deliberate: shared plans dir)
      worktree status None          -> None   (UNKNOWN: route collision-free, say so)
      worktree status False         -> the resolved root
    """
    root = _resolve_project_root()
    if root is None:
        return None
    status = _worktree_status()
    if status is True:
        return None
    if status is None:
        print(
            "[pre_plan_gates] worktree detection did not answer (timeout / missing "
            "primitive); routing this topic's plan to ~/.claude/plans rather than "
            "collocating it project-side. Re-run `create-topic` from a healthy "
            "shell if a project-side plan was intended.",
            file=sys.stderr,
        )
        return None
    return root


def _record_artifact_inside_tree(existing):
    """Where the record's OWN artifact places the topic: True (inside the projects
    tree), False (outside), or None (the record carries no artifact to judge by).

    Candidates, in order: `thought_file_path`, then the path half of
    `todo_line_ref` (`<path>:<line>`). An absolute candidate answers by
    containment under the normalised root. A relative candidate is vault-relative
    by convention; it answers True only when it exists under the root, else it is
    skipped (an unresolvable relative path is not evidence either way). The first
    candidate that yields an answer wins.
    """
    root = _norm_path(PROJECTS_ROOT)
    candidates = []
    if existing.get("thought_file_path"):
        candidates.append(str(existing["thought_file_path"]))
    ref = existing.get("todo_line_ref")
    if ref:
        candidates.append(str(ref).rsplit(":", 1)[0])
    for cand in candidates:
        p = Path(cand)
        if p.is_absolute():
            try:
                _norm_path(p).relative_to(root)
                return True
            except ValueError:
                return False
        if (root / p).exists():
            return True
    return None


def _repair_null_project_root(existing, topic_slug, project_slug, now):
    """Repair a `project_root: null` left by the pre-S3 resolver on rebind.

    Fires only when ALL of: the stored root is null; the record was created
    before `PROJECT_ROOT_FIX_LANDED` (the cutoff discriminator — see the
    constant); the resolver now yields a root; the worktree test, re-applied
    now, says this is not a worktree (an UNKNOWN answer also leaves the record
    alone); the record's OWN artifact (spine or TODO line) places it inside the
    tree — a record with no artifact is left alone; and the resolved root
    denotes the project the record is keyed on (`project_slug`).
    Persists the repaired root plus an audit stamp and returns the new value;
    returns None when nothing was changed. Never rewrites a non-null root.
    """
    if existing.get("project_root"):
        return None
    created = str(existing.get("created") or "")
    if not created or created >= PROJECT_ROOT_FIX_LANDED:
        return None
    root = _resolve_project_root()
    if root is None or _worktree_status() is not False:
        return None
    # The cutoff cannot tell the defect from a topic GENUINELY created outside the
    # tree, and the rebinding cwd says nothing about where the topic was created.
    # So the record's OWN artifact decides, and it must place the topic INSIDE the
    # tree (refuse-to-guess, never mis-attribute): a record with no artifact at
    # all — no spine, no TODO line — is left alone; it is repaired on a later
    # rebind once one is bound, and a Mode-C plan never reads this field anyway.
    if _record_artifact_inside_tree(existing) is not True:
        return None
    # And the resolved root must denote the project the record is KEYED on —
    # `Root` for the tree root, the leaf's basename otherwise. A `__Root` record
    # rebound from a leaf cwd (or the reverse) gets no root written.
    root_slug = "Root" if root == _norm_path(PROJECTS_ROOT) else root.name
    if root_slug != project_slug:
        return None
    existing["project_root"] = str(root)
    existing["project_root_repaired_at"] = now
    existing["updated"] = now
    _write_topic_state(topic_slug=topic_slug, project_slug=project_slug, state=existing)
    return str(root)


def _create_topic_locked(session_id, project_slug, topic_slug,
                         intake_source, todo_line_ref, now):
    """The `_active.json` read-modify-write half of `create_topic`, held under
    `_active_lock()` by its caller from the opening `read_active()` through BOTH
    write exits. Split out only so the lock's extent is visually unambiguous —
    no behaviour of its own."""
    active = read_active()

    # Idempotency guard: if a topic-state file already exists for this
    # (topic_slug, project_slug) pair, rebind the session in _active.json
    # to the existing state instead of wiping it. A fresh sid entering an
    # existing topic must NOT overwrite the state JSON — that destroys
    # orchestration data (phase, phase_history, phase_complete,
    # clarification_step, clarification_payloads).
    # Narrow JSONDecodeError catch: corrupt file is treated as missing,
    # letting fresh-create overwrite it with valid blank state rather than
    # leaving the session unbound. Other I/O errors propagate normally.
    try:
        existing = _read_json(_topic_path(topic_slug=topic_slug, project_slug=project_slug))
    except json.JSONDecodeError:
        existing = None
    if existing is not None:
        active[session_id] = {
            "topic_slug": topic_slug,
            "active_project": project_slug,
            "updated": now,
        }
        write_active(active)
        result = {
            "status": "rebound",
            "topic": f"{topic_slug}__{project_slug}",
        }
        # S3 / A3 — cure the records the broken resolver produced. A `null`
        # project_root has three producers that are byte-identical on disk: the
        # defect (every record minted while `_resolve_project_root` compared a
        # resolved cwd against the unresolved symlink literal), a DELIBERATE
        # worktree routing (below), and a topic genuinely outside the tree. The
        # discriminator is the cutoff: a record created before the fix landed
        # could not have received a real root at all, so its null is the defect
        # unless the worktree test — re-applied HERE, before persisting — says
        # otherwise. Records created after the cutoff keep their null: for them
        # it was a decision. Nothing rewrites the SPELLING of a stored root.
        repaired = _repair_null_project_root(existing, topic_slug, project_slug, now)
        if repaired is not None:
            result["project_root_repaired"] = repaired
        return result

    # EC5: carry over workflow validation rounds from old topic to new
    old_session = active.get(session_id, {})
    old_topic = old_session.get("active_project")
    if old_topic and old_topic != project_slug:
        old_validation_dir = WORKFLOW_VALIDATION_DIR / topic_slug / old_topic
        new_validation_dir = WORKFLOW_VALIDATION_DIR / topic_slug / project_slug
        if old_validation_dir.exists() and not new_validation_dir.exists():
            shutil.copytree(str(old_validation_dir), str(new_validation_dir))

    # Capture project_root ONCE (avoid filesystem-walk-twice bug from inline
    # double-call). _resolve_project_root() walks up from cwd to nearest TODO.md
    # marker; returns PROJECTS_ROOT for cross-cutting topics; None outside tree.
    # Worktree override: linked worktrees share ~/.claude/plans/ with the primary
    # tree, so collocating _PLAN.md project-side would collide. Route to fallback.
    # S3 / A3: this branch is newly LIVE (the resolver returned None for every
    # cwd before), so the worktree test's failure direction matters now. An
    # UNKNOWN detection (None) routes to ~/.claude/plans like a worktree does —
    # the collision-free answer — and says so; it never inherits "not a
    # worktree", which would collocate a worktree plan project-side.
    _proj_root = _project_root_for_new_topic()
    new_state = {
        "topic_slug": topic_slug,
        "project_slug": project_slug,
        "bypass_marker": False,
        "bypass_reason": None,
        "project_root": str(_proj_root) if _proj_root else None,
        "thought_file_path": None,
        "workflow_draft_confirmed_marker": None,
        "created": now,
        "updated": now,
        # Phase authority fields (Slice A — additive, same discipline as
        # "new in S1" at pre_plan_gates.py:53; absent ⇒ None/[] on read)
        "phase": None,
        "phase_history": [],
        "phase_complete": {},
        "kernel_subset_by_phase": None,
        "clarification_phase_oqs_cleared": None,
        "clarification_phase_todo_registered": None,  # Slice C-ii
        "open_decision_checkpoint": None,
        # Clarification step state (Slice D — additive; absent ⇒ defaults
        # on read via _ensure_clarification_fields()).
        "clarification_step": 0,
        "clarification_payloads": {},
        "clarification_resume_source": None,
        # Intake-source discriminator (work-start-todo-no-thought, additive;
        # absent ⇒ None on read). 'plain' = TODO line intake, no _thought
        # file; 'thought' (or None for legacy topics) = Discovery-bound.
        "intake_source": intake_source,
        "todo_line_ref": todo_line_ref,
    }
    _write_topic_state(topic_slug=topic_slug, project_slug=project_slug, state=new_state)
    active[session_id] = {
        "topic_slug": topic_slug,
        "active_project": project_slug,
        "updated": now,
    }
    write_active(active)
    return {
        "status": "created",
        "topic": f"{topic_slug}__{project_slug}",
    }


# ---------------------------------------------------------------------------
# M13 (project-tracking-staleness S10): session -> topic auto-bind at open.
#
# When a handoff-entered session reaches a seam unbound (topic_orient -> verdict
# "new"; the `read` verb -> "no_state_file"), place it on its named topic
# automatically instead of forcing a manual `create-topic` rebind. Deterministic
# source ONLY, refuse-rather-than-guess (stress case E26): bind ONLY when the
# resolved slug matches an EXISTING on-disk topic-state (never mint a fresh
# binding), reusing the shipped `create_topic` idempotent rebound port (AD-3: no
# new state-mutation surface). topic_orient stays a pure read primitive — the
# bind is an explicit act invoked by /work-start (via the `auto-bind` verb) and
# by the `read` seam, never a side effect of the read-only orient call.
# ---------------------------------------------------------------------------

def _autobind_slugs_from_worktree(cwd=None):
    """Worktree-name bind source: inside a linked worktree the dir basename ==
    the topic slug (harness invariant: worktree dir == branch == slugify(topic))."""
    if _in_worktree(cwd):
        name = (Path(cwd).name if cwd else Path.cwd().name)
        if name:
            return [name]
    return []


def _autobind_slugs_from_prompt(prompt_body):
    """Spine-path bind source: topic slugs derived from `_THOUGHT` references in
    a caller-supplied prompt body (the handoff / `/work-start` spine path)."""
    slugs = []
    if not prompt_body:
        return slugs
    spine_refs, _plan_refs = _parse_prompt_for_artifacts(prompt_body)
    for ref in spine_refs:
        stem = Path(str(ref)).name
        if stem.endswith(".md"):
            stem = stem[:-len(".md")]
        if stem.endswith("_THOUGHT"):
            stem = stem[:-len("_THOUGHT")]
        stem = re.sub(r"-\d{14}$", "", stem)  # strip a timestamped-slug suffix
        if stem and stem not in slugs:
            slugs.append(stem)
    return slugs


def _autobind_project_for_slug(slug):
    """E26 existence check + project resolution: return the project_slug for a
    topic slug. One on-disk twin → that project. Zero twins → None (absent).

    >1 twin (the divergent-key case): DETERMINISTIC disambiguation via the
    canonical resolver (S2/A2) instead of the old blanket refuse — derive the
    canonical project from the topic's spine FS location and return it, so a
    previously-twinned topic binds to the canonical record rather than refusing
    (C3/C7). Still refuses (None) only when NO spine can localise the project."""
    projects = [p for p, _path, _d in _twins_for_slug(slug)]
    if len(projects) == 1:
        return projects[0]
    if not projects:
        return None
    return canonical_project_for_slug(slug)  # None if no spine -> refuse to guess


def auto_bind_unbound_session(session_id, prompt_body=None, cwd=None):
    """Bind an unbound session to its named topic at open (M13). Never raises.

    Returns one of:
      {"status": "already_bound", "topic": ...}   # bound path untouched
      {"status": "bound", "topic": ..., "source": "worktree"|"spine-path"}
      {"status": "no_match", "candidates": [...]}  # sources resolved, none on disk
      {"status": "unresolvable"}                   # no deterministic source
      {"status": "error", "error": ...}
    """
    try:
        topic_slug, project_slug, _state = _resolve_topic(session_id)
        if topic_slug is not None and project_slug is not None:
            return {"status": "already_bound", "topic": f"{topic_slug}__{project_slug}"}
        candidates = ([("worktree", s) for s in _autobind_slugs_from_worktree(cwd)]
                      + [("spine-path", s) for s in _autobind_slugs_from_prompt(prompt_body)])
        seen = set()
        for source, slug in candidates:
            if slug in seen:
                continue
            seen.add(slug)
            project = _autobind_project_for_slug(slug)
            if project is not None:
                # On-the-fly rename-on-resolve (S2/A2): when twins exist and the
                # canonical record is absent, make it exist so the rebind lands
                # on the real (richest) state instead of a blank mint. No-op when
                # the canonical record already exists; defers a fresh-locked twin
                # to the bulk reconcile.
                _ensure_canonical_record(slug, project)
                # Idempotent REBOUND path (existing state present) — writes
                # _active.json only, never overwrites topic-state.
                res = create_topic(session_id, project, slug)
                return {"status": "bound", "topic": f"{slug}__{project}",
                        "source": source, "create_topic_status": res.get("status")}
        if candidates:
            return {"status": "no_match", "candidates": [s for _src, s in candidates]}
        return {"status": "unresolvable"}
    except Exception as e:  # never break a seam on auto-bind failure
        return {"status": "error", "error": str(e)}


def validate_discovery_locked_fields(text, thought_bound=True):
    """Validate that all four DISCOVERY_LOCKED_FIELDS are present and non-empty.

    Within ## Metrics, also checks that a non-empty `OMTM:` line exists.

    Returns list of diagnostic strings. Empty list = PASS.
    Mutable subsections (## Scope, ## Q&A) are not checked here — they may
    be empty during early # Discovery drafts.

    thought_bound (work-start-todo-no-thought, 2026-05-24): plain-mode topics
    have no _thought file bound. When the caller passes thought_bound=False,
    the validator is a no-op and returns [] — the locked-field contract only
    fires when a _thought file is the validation target. Default True preserves
    the existing thought-bound contract for all current callers.

    Input shape (A3, discovery-field-match-anchoring): three shapes reach this
    one function, and it now handles them explicitly instead of by accident.

      1. A whole NEW-SPEC spine — the `:3119` caller. The locked fields live
         inside `# Discovery`, so scope to it; that is what makes this function
         return the same span as the hash, the lock hook and the thought-file
         validator (C4).
      2. A whole LEGACY spine — `## Snapshot` at the top carrying all four
         locked fields, above a `# Discovery` stub holding only a wikilink.
         Scoping such a file would report four missing fields on a spine that
         is complete. The corpus check caught exactly one of these
         (`logging-slice-1-silent-critical-paths_THOUGHT.md`), which is why
         scoping is conditional rather than unconditional.
      3. A bare Discovery body with no `# Discovery` header — what `snapshot
         --validate` pipes in on stdin (its own usage line documents "reads
         discovery section text from stdin"), and it has an out-of-harness
         caller besides. Nothing to scope; search the input as given.

    The rule below collapses those three into one test: scope only when
    scoping hides no locked field the unscoped view can see. That cannot turn a
    passing spine into a failing one, and on a new-spec spine — where the
    fields exist only inside Discovery — it always scopes.

    The `# Discovery` presence test uses the anchored section rule, so a
    backticked prose mention of `# Discovery` (the defect class this plan
    removes) cannot trigger scoping; only a genuine line-start heading can.
    """
    if not thought_bound:
        return []
    scoped = discovery_section_body(text)
    if scoped is not None:
        in_scope = sum(1 for f in DISCOVERY_LOCKED_FIELDS if find_heading(scoped, f) != -1)
        in_whole = sum(1 for f in DISCOVERY_LOCKED_FIELDS if find_heading(text, f) != -1)
        if in_scope == in_whole:
            text = scoped
    all_sections = DISCOVERY_LOCKED_FIELDS + DISCOVERY_MUTABLE_SUBSECTIONS
    diagnostics = []
    for field in DISCOVERY_LOCKED_FIELDS:
        content = extract_heading_body(text, field, all_sections)
        if content is None:
            diagnostics.append(f"Missing field: {field}")
            continue
        # A7 — emptiness is decided by the one shared predicate, not by a local
        # `not content or content == "<text>"`. Chrome does not count as content,
        # so a body holding only a lock marker now reads empty here too.
        if is_effectively_empty(content):
            diagnostics.append(f"Empty field: {field}")
        elif field == "## Metrics" and not has_metric_line(content):
            # A8 — the metric-line rule now goes through the shared helper, so
            # this site and the blocking one in `_validate-thought-file.py` cannot
            # drift. This copy is KEPT deliberately: `topic_orient` calls this
            # function and feeds `spine_locked` to three non-blocking consumers,
            # and deleting it would silently strip OMTM checking from all three.
            diagnostics.append("Missing OMTM line in ## Metrics")
    return diagnostics


# ---------------------------------------------------------------------------
# Clarification step state machine (Slice D)
# ---------------------------------------------------------------------------

CLARIFICATION_STEP_MAX = 10

# Step-7 section sub-steps (section-by-section drafting cadence). Ordered.
# Mirrors the "1b" string-sub-step precedent: these are inserted between
# integer step 6 and the final step-7 advance, and are only valid when the
# cadence is "section_by_section" (see clar_advance + clar_set_cadence).
CLARIFICATION_STEP7_SECTIONS = ("7a", "7b", "7c", "7d", "7e")

# Cascade-on-update map (Slice D plan §A4): when source step changes,
# the listed downstream steps re-confirm in resume mode (default) or
# re-run on `--rerun`. Step 7-tail (challenge) is internal to step 7.
# The 7a..7e section sub-steps cascade to 9 like step 7 itself.
CLARIFICATION_CASCADE_MAP = {
    1: [2, "1b", 3, 4, 5, 7, 9],
    "1b": [2, 7, 9],
    2: [5, 7, 9],
    3: [7, 9],
    4: [7, 9],
    5: [6, 7, 9],
    6: [5, 7, 9],
    7: [9],
    "7a": [9],
    "7b": [9],
    "7c": [9],
    "7d": [9],
    "7e": [9],
    8: [9],
    9: [10],
    10: [],
}


def _clar_step_ordinal(step_key):
    """Sortable numeric position for a clarification step key.

    Handles integers, the "1b" sub-step (between 1 and 2), and the
    "7a".."7e" section sub-steps (between 6 and 7). Returns None for keys
    that are not recognized clarification steps (so callers can skip them).
    """
    if isinstance(step_key, bool):
        return None
    if isinstance(step_key, int):
        return float(step_key)
    s = str(step_key)
    if s == "1b":
        return 1.5
    if s in CLARIFICATION_STEP7_SECTIONS:
        return 6.0 + (CLARIFICATION_STEP7_SECTIONS.index(s) + 1) / 10.0
    try:
        return float(int(s))
    except (TypeError, ValueError):
        return None


def _ensure_clarification_fields(state):
    """Add Clarification fields to topic state if missing (legacy state files).

    Idempotent. Mirrors the Slice-A phase fields' additive discipline
    (absent ⇒ default on read; new topics get them set in classify()).
    """
    if "clarification_step" not in state:
        state["clarification_step"] = 0
    if "clarification_payloads" not in state:
        state["clarification_payloads"] = {}
    if "clarification_resume_source" not in state:
        state["clarification_resume_source"] = None
    if "clarification_active_session" not in state:
        state["clarification_active_session"] = None
    if "clarification_step7_cadence" not in state:
        # None == full_pass (default). Other value: "section_by_section".
        state["clarification_step7_cadence"] = None


# ── A1 (claim-flip enforcement) — code gate at the manageable-primary seam ────
# Relocates the "run the Claim-Identification engine" decision from Layer-3 skill
# text (steps.md Step 2) to this Layer-1 code seam, per code_first_architecture.md
# ("If a rule can be enforced by code, it must be"; "Code owns the flow"; "AI never
# decides what happens next"). A CLI cannot call Agent, so the main-loop AI
# dispatches the scoped Explore claim adapter over the accepted mirror and passes
# its structured output in as `claim_set`; THIS gate enforces that it did — presence
# + schema — or that a reason-logged `default` fallback was recorded (dual-mode U3;
# never a silent skip). Quality stays a separate model job (producer-never-verifies,
# grading order code→model→human): this gate is only the floor.
#
# The clarification steps whose seam is manageable-primary and therefore requires
# the claim artifact. Step 2 (Reflect-back) is the reference site (A1); every other
# consumer site (plan Gate 0b2 = A2, extract-knowledge = A3) inherits this contract.
_CLAIM_GATE_STEPS = (2,)


def _validate_claim_gate(payload, *, site):
    """Enforce the claim-set-or-reason contract at a manageable-primary seam.

    Accepts iff `payload` carries EITHER a schema-valid ``claim_set`` (manageable
    path — validated through the shared `_claim_persist.parse_claim_set` seam so the
    schema has a single locus, A13) OR a non-empty ``claim_fallback_reason`` (the
    reason-logged ``default`` fallback — the fact-check BYPASSED pattern; A5's durable
    `.claim-runs.md` record). Raises ValueError (refuse to advance) otherwise, so the
    engine can never silently no-op.

    Returns ``("manageable", ClaimSet)`` or ``("default", reason_str)`` so the caller
    can append the A4/A5 run-record without re-parsing.
    """
    claim_set = payload.get("claim_set")
    if claim_set is not None:
        try:
            from _claim_persist import parse_claim_set
            from _claim_engine import SchemaError
        except ImportError as e:            # engine unavailable → fail loud, not silent
            raise ValueError(
                f"{site}: claim-identification engine unavailable ({e}); cannot "
                "validate claim_set. To use the default path instead, omit claim_set "
                "and set a non-empty 'claim_fallback_reason'."
            )
        try:
            cs = parse_claim_set(claim_set)  # code checks schema at the seam (A13)
        except SchemaError as e:
            raise ValueError(
                f"{site}: claim_set failed schema validation (nothing advanced): {e}"
            )
        # CF-4/A2: a Deep-mode site must produce a DEEP claim-set — a NORMAL (or absent)
        # thoroughness means the "flip shipped NORMAL". Enforce it in code (no silent
        # downgrade); the producer re-runs at DEEP or passes claim_fallback_reason.
        try:
            from _claim_harvest import deep_mode_ok
            _chk = deep_mode_ok(site, getattr(cs.thoroughness, "value", cs.thoroughness))
            if not _chk["ok"]:
                raise ValueError(_chk["error"])
        except ImportError:
            pass   # mode module unavailable → schema floor still applied (fail-open on import)
        return ("manageable", cs)
    reason = payload.get("claim_fallback_reason")
    if reason and str(reason).strip():
        return ("default", str(reason).strip())   # reason-logged fallback (A5)
    raise ValueError(
        f"{site}: manageable-primary seam requires either a schema-valid 'claim_set' "
        "(dispatch the scoped claim-identification adapter over the accepted mirror, "
        "then pass its structured output here) or a non-empty 'claim_fallback_reason' "
        "(reason-logged default fallback). A silent skip is not allowed "
        "(code_first_architecture.md — code owns the flow)."
    )


def _record_claim_run(topic_slug, project_slug, *, site, mode, result):
    """A4/A5 — append a durable engine-ran record to the topic's `.claim-runs.md`
    (SC2), co-located with the topic-state file: a manageable run records completeness;
    a default fallback records a reason-logged BYPASSED row (A5). Best-effort — an
    auxiliary-record failure must NEVER block the clarification advance (the gate
    already passed)."""
    try:
        import _claim_metrics as cm
        state_path = _topic_path(topic_slug=topic_slug, project_slug=project_slug)
        sidecar = state_path.with_name(state_path.stem + ".claim-runs.md")
        checked_at = datetime.now(timezone.utc).isoformat()
        if mode == "manageable":
            rec = cm.manageable_record(result, site=site, model="sonnet",
                                       checked_at=checked_at)
        else:
            rec = cm.fallback_record(site=site, reason=result, checked_at=checked_at)
        cm.append_run_record(sidecar, rec)
    except Exception as e:  # noqa: BLE001 — record is auxiliary; never break the flow
        print(f"[claim-run-record] warning: could not record run at {site}: {e}",
              file=sys.stderr)


def _record_claim_run_artifact_sidecar(artifact_path, *, site, mode, result):
    """S5 (v2 Centralization, A10 surface ii) — ADDITIVE artifact-co-located
    `.claim-runs.md` row, written via the SAME generic-convention sidecar path
    (`_claim_metrics.sidecar_path_for`) the other three migrated consumers use
    (`_claim_dispatch.claim_identify`, `_dc_claim_seam`, `/plan`'s virtual-path
    variant). This is separate from — and does NOT replace — `_record_claim_run`'s
    pre-existing state-co-located sidecar (unchanged, byte-stable); it exists so a
    later `_claim_attest.classify_provenance(thought_file_path, ...)` witness read
    finds a real backing row next to the topic's actual `_THOUGHT.md` artifact,
    making `/clarification` witnessed like the other three sites (A10, G4).

    No-op (silent) when `artifact_path` is falsy — `thought_file_path` is still
    `None` for many topics (nothing in the documented flow backfills it before
    Step 2 runs; see `set_thought_file`). Best-effort otherwise: a write failure
    warns to stderr and never blocks `clar_advance` (Rule 6)."""
    if not artifact_path:
        return
    try:
        import _claim_metrics as cm
        sidecar = cm.sidecar_path_for(str(artifact_path))
        checked_at = datetime.now(timezone.utc).isoformat()
        if mode == "manageable":
            rec = cm.manageable_record(result, site=site, model="sonnet",
                                       checked_at=checked_at)
        else:
            rec = cm.fallback_record(site=site, reason=result, checked_at=checked_at)
        cm.append_run_record(sidecar, rec, dedup_last=True)
    except Exception as e:  # noqa: BLE001 — record is auxiliary; never break the flow
        print(f"[claim-run-record-artifact] warning: could not record run for "
              f"{artifact_path!r} at {site}: {e}", file=sys.stderr)


def _witness_claim_provenance(artifact_path, *, mode, result):
    """S5 (v2 Centralization, A10 surface i) — the artifact-shape
    `_claim_attest.classify_provenance` WITNESS READ at /clarification Step 2,
    counted in the attestation-coverage denominator (`_claim_runs_aggregate.py` /
    U2 meter). Mirrors `_plan_claim_gate._attestation_flags`'s call shape (same
    separate-actor, sidecar-only, advisory-only contract) — but reads the sidecar
    `_record_claim_run_artifact_sidecar` just wrote, not `/plan`'s virtual path.

    No-op (silent) when `artifact_path` is falsy. Never raises, never affects
    `clar_advance`'s schema decision or in-memory return: a read failure or a
    `flagged` disposition is reported to stderr only (advisory/informational,
    never blocking — Rule 6)."""
    if not artifact_path:
        return
    try:
        from _claim_attest import classify_provenance
    except ImportError:
        return
    if mode == "manageable":
        asserted_mode = "manageable"
        asserted_thoroughness = str(
            getattr(result.thoroughness, "value", result.thoroughness) or ""
        ).strip().lower()
    else:
        asserted_mode = "default"
        asserted_thoroughness = "n/a"
    try:
        disposition = classify_provenance(
            str(artifact_path),
            asserted_mode=asserted_mode,
            asserted_thoroughness=asserted_thoroughness or "n/a",
        )
    except Exception as e:  # noqa: BLE001 — advisory-only; never break clar_advance
        print(f"[claim-provenance-witness] warning: could not classify "
              f"{artifact_path!r}: {e}", file=sys.stderr)
        return
    if disposition.get("disposition") == "flagged":
        reason = disposition.get(
            "reason", "claims assert a deep engine run with no matching .claim-runs.md record")
        print(f"[claim-provenance-witness] ADVISORY — {reason}", file=sys.stderr)


def clar_advance(session_id, step_num, payload, resume_source=None):
    """Advance the Clarification state machine by one step.

    Validates strict ordering (step_num == clarification_step + 1) and
    persists `payload` under `clarification_payloads[str(step_num)]`. JSON
    object keys are strings on roundtrip — callers should treat the
    payload map's keys as strings.

    Optional `resume_source` is recorded in `clarification_resume_source`
    when set at step 1 (entry via `/clarification --from <path>`).

    For manageable-primary steps (`_CLAIM_GATE_STEPS`, currently Step 2) the
    payload MUST carry either a schema-valid `claim_set` or a non-empty
    `claim_fallback_reason` — the A1 code gate (`_validate_claim_gate`) refuses
    to advance otherwise, so the claim engine can never silently no-op.

    Returns {status, step, next_step}. Raises ValueError on order violation,
    missing topic state, invalid input shape, or a failed claim gate.
    """
    # Accept "1b" and the section sub-steps "7a".."7e" (strings) in addition
    # to integers 1..CLARIFICATION_STEP_MAX.
    _valid_str_steps = ("1b",) + CLARIFICATION_STEP7_SECTIONS
    if step_num not in _valid_str_steps and (
        not isinstance(step_num, int) or step_num < 1 or step_num > CLARIFICATION_STEP_MAX
    ):
        raise ValueError(
            f"Invalid Clarification step: {step_num}. "
            f"Must be 1..{CLARIFICATION_STEP_MAX}, '1b', or a section sub-step "
            f"({', '.join(CLARIFICATION_STEP7_SECTIONS)})."
        )
    if not isinstance(payload, dict):
        raise ValueError("Clarification payload must be a JSON object.")

    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'create-topic' (or 'phase-start ... thought') first."
        )
    _ensure_clarification_fields(state)

    cur = state["clarification_step"]
    cadence = state.get("clarification_step7_cadence")
    section_mode = cadence == "section_by_section"
    sections = CLARIFICATION_STEP7_SECTIONS
    # "1b" sub-step: expected after step 1 (or after rewind to 1).
    if step_num == "1b":
        if cur not in (1, 0):
            raise ValueError(
                f"Clarification sequence violation: step '1b' requires current step to be 1, "
                f"got {cur}."
            )
    elif step_num in sections:
        # Section sub-steps are only valid in section-by-section cadence.
        if not section_mode:
            raise ValueError(
                f"Clarification sequence violation: section sub-step {step_num!r} is only "
                f"valid in section-by-section cadence (current cadence: "
                f"{cadence or 'full_pass'}). Set it first with 'clar-set-cadence'."
            )
        idx = sections.index(step_num)
        expected_prev = 6 if idx == 0 else sections[idx - 1]
        if cur != expected_prev:
            raise ValueError(
                f"Clarification sequence violation: section sub-step {step_num!r} requires "
                f"current step {expected_prev!r}, got {cur!r}."
            )
    else:
        # Integer step. Resolve the current step to an integer position.
        if cur == "1b":
            cur_int = 1
        elif cur == "7e":
            # After the last section sub-step, the next integer step is 7.
            cur_int = 6
        elif cur in sections:
            # A section sub-step other than 7e must be followed by the next
            # section sub-step, not an integer step.
            raise ValueError(
                f"Clarification sequence violation: in section-by-section cadence, step "
                f"{cur!r} must be followed by the next section sub-step, not integer step "
                f"{step_num}."
            )
        else:
            cur_int = cur
        # Section-mode guard: finalizing step 7 requires the section sub-steps first.
        if step_num == 7 and section_mode and cur == 6:
            raise ValueError(
                "Clarification sequence violation: section-by-section cadence requires "
                "section sub-steps 7a..7e before finalizing step 7. Next expected: '7a'."
            )
        expected = cur_int + 1
        if step_num != expected:
            raise ValueError(
                f"Clarification sequence violation: expected step {expected}, "
                f"got {step_num}. Current step: {cur}."
            )

    # A1 — code gate: a manageable-primary step cannot advance without the claim-set
    # artifact (or a reason-logged fallback). Runs only after the ordering checks
    # pass, before any state is mutated, so a rejected gate leaves state untouched.
    # A4/A5 — on a passing gate, append the durable run-record (SC2 / BYPASSED).
    if step_num in _CLAIM_GATE_STEPS:
        _site = f"clarification:step{step_num}"
        _mode, _result = _validate_claim_gate(payload, site=_site)
        # NB: _resolve_topic returns (topic_slug, project_slug, state); clar_advance
        # names them (proj, topic) — a pre-existing misnomer. Pass them as the real
        # (topic_slug, project_slug) so the sidecar co-locates with the topic state.
        _record_claim_run(proj, topic, site=_site, mode=_mode, result=_result)
        # S5 (v2 Centralization, A10) — ADDITIVE artifact-co-located witness surface:
        # a generic-convention `.claim-runs.md` row next to the topic's `_THOUGHT.md`
        # (so /clarification counts like the other three migrated consumers), plus a
        # sidecar-only provenance witness read over that same file. Both are
        # best-effort/advisory-only; neither touches the schema decision above nor
        # this function's in-memory return (A9(c) byte-stability).
        _artifact_path = state.get("thought_file_path")
        _record_claim_run_artifact_sidecar(_artifact_path, site=_site, mode=_mode, result=_result)
        _witness_claim_provenance(_artifact_path, mode=_mode, result=_result)

    state["clarification_step"] = step_num
    state["clarification_payloads"][str(step_num)] = payload
    if resume_source is not None and step_num == 1:
        state["clarification_resume_source"] = resume_source
    # Set unlock token at step 1 (fresh mode) or --from entry; clear at step 9 lock.
    if step_num == 1 or (resume_source is not None):
        state["clarification_active_session"] = session_id
    if step_num == 9:
        state["clarification_active_session"] = None
    state["updated"] = datetime.now(timezone.utc).isoformat()
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)

    # Compute next_step: "1b" → 2; section sub-step → next sub-step (or 7
    # after 7e); integers follow normally.
    if step_num == "1b":
        next_step = 2
    elif step_num in CLARIFICATION_STEP7_SECTIONS:
        _idx = CLARIFICATION_STEP7_SECTIONS.index(step_num)
        next_step = (
            CLARIFICATION_STEP7_SECTIONS[_idx + 1]
            if _idx + 1 < len(CLARIFICATION_STEP7_SECTIONS)
            else 7
        )
    elif isinstance(step_num, int):
        next_step = step_num + 1 if step_num < CLARIFICATION_STEP_MAX else None
    else:
        next_step = None
    return {
        "status": "advanced",
        "step": step_num,
        "next_step": next_step,
    }


def clar_set_cadence(session_id, cadence):
    """Set the Step-7 drafting cadence for the active topic.

    Settable only at clarification step 6 (after the research gate, before
    the Step-7 draft). "section_by_section" arms the 7a..7e section sub-steps
    in clar_advance; "full_pass" (the default, also represented as None) keeps
    the legacy single-advance Step-7 path. Persisting the choice is what makes
    the cadence code-enforceable across turns and resumes.

    Returns {status, cadence}. Raises ValueError on bad value, missing topic,
    or wrong current step.
    """
    if cadence not in ("full_pass", "section_by_section"):
        raise ValueError(
            f"Invalid cadence: {cadence!r}. Must be 'full_pass' or 'section_by_section'."
        )
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'create-topic' (or 'phase-start ... thought') first."
        )
    _ensure_clarification_fields(state)
    cur = state["clarification_step"]
    if cur != 6:
        raise ValueError(
            f"Cadence can only be set at clarification step 6 (after the research "
            f"gate, before step 7); current step is {cur!r}."
        )
    # Store None for full_pass so the default state stays byte-identical.
    state["clarification_step7_cadence"] = (
        None if cadence == "full_pass" else cadence
    )
    state["updated"] = datetime.now(timezone.utc).isoformat()
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {"status": "cadence_set", "cadence": cadence}


def clar_status(session_id):
    """Return current Clarification step + payload summary for `/clarification`
    resume."""
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        return {
            "status": "no_topic",
            "topic": None,
            "step": 0,
            "payloads": {},
            "resume_source": None,
        }
    _ensure_clarification_fields(state)
    return {
        "status": "ok",
        "topic": f"{proj}__{topic}",
        "step": state["clarification_step"],
        "payloads": state["clarification_payloads"],
        "resume_source": state["clarification_resume_source"],
    }


def clar_rewind(session_id, to_step):
    """Rewind to a prior Clarification step; mark downstream payloads stale.

    Per the cascade-on-update map. Payloads are preserved (stale=true added)
    so the user can review-and-accept or revise in resume mode — never
    deleted silently.
    """
    if not isinstance(to_step, int) or to_step < 0 or to_step > CLARIFICATION_STEP_MAX:
        raise ValueError(
            f"Invalid rewind target: {to_step}. "
            f"Must be 0..{CLARIFICATION_STEP_MAX}."
        )
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(f"No active topic for session {session_id}.")
    _ensure_clarification_fields(state)

    cur = state["clarification_step"]
    cur_ord = _clar_step_ordinal(cur)
    if cur_ord is not None and to_step > cur_ord:
        raise ValueError(
            f"Cannot rewind forward: target step {to_step} > current step {cur}."
        )

    stale_steps = []
    for step_key in list(state["clarification_payloads"].keys()):
        # Include string sub-step keys ("1b", "7a".."7e") that the old
        # integer-only loop skipped — they are downstream of earlier steps too.
        ordinal = _clar_step_ordinal(step_key)
        if ordinal is None:
            continue
        if ordinal > to_step:
            payload = state["clarification_payloads"][step_key]
            if isinstance(payload, dict):
                payload["stale"] = True
                # Preserve int typing for pure-integer keys (back-compat with
                # the existing return shape); string sub-steps stay strings.
                try:
                    stale_steps.append(int(step_key))
                except (TypeError, ValueError):
                    stale_steps.append(step_key)

    # Rewinding to before the Step-7 draft invalidates any section-by-section
    # cadence — reset the state field to its default (full_pass).
    if to_step <= 6:
        state["clarification_step7_cadence"] = None

    state["clarification_step"] = to_step
    state["updated"] = datetime.now(timezone.utc).isoformat()
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "rewound",
        "to_step": to_step,
        "stale_steps": sorted(stale_steps, key=_clar_step_ordinal),
    }


def bypass_topic(session_id, reason):
    """Write bypass_marker to active topic state (state-only).

    Note: state-only bypass; the caller is responsible for any audit trail
    (commit-prefix, TODO line, diary entry). This command only updates state.
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'create-topic' to register a topic first."
        )
    now = datetime.now(timezone.utc).isoformat()
    state["bypass_marker"] = True
    state["bypass_reason"] = reason
    state["updated"] = now
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "bypassed",
        "topic": f"{proj}__{topic}",
        "reason": reason,
    }


def set_active(session_id, topic_slug, project_slug):
    """Point _active.json[session_id] at an existing topic state file.

    Used to resume a multi-session topic in a new session without overwriting
    the topic's state fields.
    """
    if not _topic_path(topic_slug=topic_slug, project_slug=project_slug).exists():
        raise ValueError(
            f"No topic state at {_topic_path(topic_slug=topic_slug, project_slug=project_slug)}. "
            "Use 'create-topic' to create one first."
        )
    now = datetime.now(timezone.utc).isoformat()
    # Region 3 of 4 (Cluster-B S0) — lock opens at the read, not the write.
    with _active_lock():
        active = read_active()
        active[session_id] = {
            "topic_slug": topic_slug,
            "active_project": project_slug,
            "updated": now,
        }
        write_active(active)
    return {
        "status": "active_set",
        "session_id": session_id,
        "topic": f"{topic_slug}__{project_slug}",
    }


def set_thought_file(session_id, thought_path):
    """Backfill thought_file_path on the active topic state."""
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    state["thought_file_path"] = thought_path
    state["updated"] = datetime.now(timezone.utc).isoformat()
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "thought_file_set",
        "topic": f"{proj}__{topic}",
        "thought_file_path": thought_path,
    }


def confirm_workflow_draft(session_id):
    """Set HTML-comment confirmation marker on active topic state (A3)."""
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    marker = f"<!-- WORKFLOW_DRAFT_CONFIRMED:{session_id} -->"
    state["workflow_draft_confirmed_marker"] = marker
    state["updated"] = datetime.now(timezone.utc).isoformat()
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "workflow_draft_confirmed",
        "topic": f"{proj}__{topic}",
        "marker": marker,
    }


def is_workflow_confirmed(session_id):
    """Return True iff confirmation marker present and bound to this session_id (A4)."""
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        return False
    expected = f"<!-- WORKFLOW_DRAFT_CONFIRMED:{session_id} -->"
    return state.get("workflow_draft_confirmed_marker") == expected


# ---------------------------------------------------------------------------
# Audit-capture confirmation markers (issue-capture-workflow A5)
# Session-bound file markers in STATE_DIR — no active topic required.
# ---------------------------------------------------------------------------

def _audit_arm_path(session_id):
    return STATE_DIR / f"_audit-arm-{session_id}"


def _audit_confirm_path(session_id):
    return STATE_DIR / f"_audit-confirm-{session_id}"


def arm_audit_capture(session_id):
    """Record that an audit capture is in progress for this session (A5).

    Must be called by the skill when a capture is proposed to the user.
    Creates the arm marker so the confirm hook knows to guard writes.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _audit_arm_path(session_id).touch()
    return {"status": "audit_capture_armed", "session_id": session_id}


def confirm_audit_capture(session_id):
    """Set the confirmation marker after explicit user approval (A5).

    AI cannot self-confirm — this must be called only after parsing
    an explicit user approval response.
    Raises ValueError if not yet armed (arm-audit-capture must come first).
    """
    if not _audit_arm_path(session_id).exists():
        raise ValueError(
            f"Session {session_id} is not armed for audit capture. "
            "Call arm-audit-capture first."
        )
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _audit_confirm_path(session_id).touch()
    return {"status": "audit_capture_confirmed", "session_id": session_id}


def is_audit_capture_confirmed(session_id):
    """Return True if confirmed, False if armed-unconfirmed, raises if not armed."""
    if not _audit_arm_path(session_id).exists():
        return None  # sentinel: not armed (no capture in progress)
    return _audit_confirm_path(session_id).exists()


# ---------------------------------------------------------------------------
# Phase-state authority (Slice A — Walking Skeleton)
# ---------------------------------------------------------------------------

def _make_phase_auth_token(session_id, from_phase, to_phase):
    """Return the expected user-auth HTML-comment token for a phase transition."""
    from_str = from_phase if from_phase is not None else "none"
    return f"<!-- PHASE_AUTH:{session_id}:{from_str}->{to_phase} -->"


def _make_oqs_auth_token(session_id, phase):
    """Return the expected auth token for --oqs-cleared on a phase-complete call."""
    return f"<!-- PHASE_OQS_AUTH:{session_id}:{phase} -->"


def _make_dc_auth_token(session_id):
    """Return the expected auth token for decision-checkpoint resolve."""
    return f"<!-- DC_AUTH:{session_id} -->"


def phase_complete_predicate(state, phase):
    """Return True if phase satisfies the walking-skeleton completeness predicate.

    Walking-skeleton thin rules:
    - Any phase:    phase_complete[phase] must be True (set by phase-complete)
    - clarification:  additionally requires clarification_phase_oqs_cleared=True (OQ16-a)
    Richer per-phase step-lists are Slices C-G.
    """
    if not state.get("phase_complete", {}).get(phase):
        return False
    if phase == "clarification":
        return (
            bool(state.get("clarification_phase_oqs_cleared"))
            and bool(state.get("clarification_phase_todo_registered"))
        )
    return True


def uft_confirm(todo_text, project_root=None):
    """OQ20d stub — ultra-fast-track TODO creation in NOW bucket.

    Slice F owns the entry point and all caller logic.
    This function is NOT exposed as a CLI subcommand.
    """
    if not todo_text or not todo_text.strip():
        raise ValueError("uft_confirm requires todo_text (single-line framing convention).")
    todo_script = Path(__file__).parent / "todo.py"
    cmd = ["python3", str(todo_script), "add", todo_text,
           "--bucket", "NOW", "--validate-framing"]
    if project_root:
        cmd += ["--project", project_root]
    sp = subprocess.run(cmd, capture_output=True, text=True)
    if sp.returncode != 0:
        raise ValueError(f"UFT TODO creation failed:\n{sp.stderr.strip()}")
    return json.loads(sp.stdout) if sp.stdout.strip() else {"status": "added"}


def phase_start(session_id, phase, auth_token, artifact_link=None, todo_text=None):
    """Advance to the named phase (A2/A3).

    Code-enforced validations:
    1. Phase is the immediate successor of current phase      (C1)
    2. Current phase passes phase_complete_predicate()        (C3 / OQ16-a)
    3. No open decision-checkpoint                            (OQ14 / C4)
    4. auth_token == expected PHASE_AUTH marker               (C2 / A3)

    The legacy classification-applicability gate (Slice F removal) is gone:
    research/exploration workflows live in standalone skills (/research,
    /extract-knowledge, /clarification Step 6) — the engine carries only
    the canonical 5-phase sequence.
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )

    current = state.get("phase")
    if current is None:
        if phase != PHASE_SEQUENCE[0]:
            raise ValueError(
                f"First phase must be '{PHASE_SEQUENCE[0]}', got '{phase}'."
            )
    else:
        try:
            idx = PHASE_SEQUENCE.index(current)
        except ValueError:
            raise ValueError(f"Current phase '{current}' is not in PHASE_SEQUENCE.")
        expected_next = (
            PHASE_SEQUENCE[idx + 1] if idx + 1 < len(PHASE_SEQUENCE) else None
        )
        if phase != expected_next:
            raise ValueError(
                f"Phase '{phase}' is not the immediate successor of '{current}'. "
                f"Next expected: '{expected_next}'."
            )
        if not phase_complete_predicate(state, current):
            extra = ""
            if current == "clarification":
                missing = []
                if not state.get("clarification_phase_oqs_cleared"):
                    missing.append("--oqs-cleared (call phase-complete --oqs-cleared --auth TOKEN)")
                if not state.get("clarification_phase_todo_registered"):
                    missing.append("clarification_phase_todo_registered (call register-session-todo)")
                if missing:
                    extra = " (clarification requires: " + "; ".join(missing) + ")"
            raise ValueError(
                f"Cannot advance: phase '{current}' is not complete{extra}. "
                "Call 'phase-complete' first."
            )

    dc = state.get("open_decision_checkpoint")
    if dc:
        raise ValueError(
            f"Cannot advance: open decision checkpoint — "
            f"'{dc.get('description', '')}'. "
            "Resolve with: decision-checkpoint SESSION_ID resolve --auth TOKEN"
        )

    # Plain-mode initial entry (intake_source == "plain", current is None) is
    # a single-step skill entry — no user-adjudicated transition — so the
    # PHASE_AUTH round-trip is skipped. work-start/SKILL.md §"Step 4 / Plain
    # mode" prescribes this contract.
    plain_intake = state.get("intake_source") == "plain"
    if not (plain_intake and current is None):
        expected_auth = _make_phase_auth_token(session_id, current, phase)
        if auth_token != expected_auth:
            raise ValueError(
                f"Missing or invalid auth token. Required token:\n"
                f"  {expected_auth}\n"
                "Copy this token exactly, confirm you approve the transition, then retry."
            )

    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "from": current,
        "to": phase,
        "at": now,
        "session_id": session_id,
        "auth": auth_token,
        "artifact_link": artifact_link,
    }
    state["phase"] = phase
    state.setdefault("phase_history", []).append(entry)
    state["updated"] = now

    # Slice L A7 — snapshot prior topic-state JSON for rollback on sync failure.
    # S3/A4: `proj, topic, state = _resolve_topic(...)` binds proj=TOPIC slug
    # and topic=PROJECT slug, so the argument order below is (topic_slug,
    # project_slug) as `_topic_path` declares it. It read `(topic, proj)` until
    # S3 — a MIRRORED path that is not the record being mutated, so the snapshot
    # read nothing and the rollback acted on a phantom. The locals are
    # deliberately NOT renamed (A4); S4/A5's keyword form PRESERVES that double
    # swap — `topic_slug=proj` is right and `topic_slug=topic` would be wrong.
    state_path = _topic_path(topic_slug=proj, project_slug=topic)
    prev_state_bytes = state_path.read_bytes() if state_path.exists() else None

    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)

    # Slice L A7 — wrap with post-write sync (additive). On verifier failure
    # or rule (c) hard-fail, roll back the topic-state JSON via atomic rename
    # so the user never sees a half-state.
    sync_receipts = None
    try:
        from taskmanagement import (  # type: ignore
            sync_phase_transition as _tm_sync_phase_transition,
            WriteVerificationError as _tm_WriteVerificationError,
            SyncConflictError as _tm_SyncConflictError,
        )
        # Ensure topic_slug + project_slug are present in state for the
        # helper to derive paths (these are set by classify; defensive
        # backfill keeps the wrap additive for any callers that built
        # state directly).
        # S3/A4: proj holds the TOPIC slug, topic holds the PROJECT slug (see
        # the snapshot above). This wrote the two halves SWAPPED until S3.
        if "topic_slug" not in state:
            state["topic_slug"] = proj
        if "project_slug" not in state:
            state["project_slug"] = topic
        try:
            sync_receipts = _tm_sync_phase_transition(
                state, prev_phase=current, new_phase=phase,
                session_id=session_id,
            )
        except (_tm_WriteVerificationError, _tm_SyncConflictError):
            # Rollback the topic-state JSON. S3/A4: both arms are best-effort.
            # Each helper swallows its own OSError so the original exception,
            # not a recovery error, reaches the caller through the bare `raise`.
            # Scoped claim, not an absolute one: a non-OSError raised inside a
            # helper would still mask the sync failure. Both helpers discard
            # their return value here — an abandoned recovery is visible only
            # on stderr, which A4 permits (recovery is best-effort). Note this
            # path always re-raises, so there is no returned dict for it to
            # appear in either; the caller gets no programmatic signal at all.
            if prev_state_bytes is not None:
                _restore_topic_state(state_path, prev_state_bytes)
            else:
                _archive_topic_state(state_path)
            raise
    except ImportError:
        sync_receipts = {"status": "skipped", "reason": "taskmanagement_not_importable"}

    # Conditional-reflection routing (Slice C-ii / OQ20a/b/c)
    todo_add_result = None
    todo_pending = False
    project_root = state.get("project_root")
    todo_script = Path(__file__).parent / "todo.py"

    if phase in ("thought", "implementation") and not plain_intake:
        if not todo_text:
            raise ValueError(
                f"phase_start('{phase}') requires todo_text. "
                "Provide a single-line item following the 4-component convention: "
                "Problem: … Context: … Guiding policy: … Master plan: [[…]]."
            )
        cmd = ["python3", str(todo_script), "add", todo_text,
               "--bucket", "NOW", "--validate-framing"]
        if phase == "thought":
            cmd += ["--tag", "Thought"]
        if project_root:
            cmd += ["--project", project_root]
        sp = subprocess.run(cmd, capture_output=True, text=True)
        if sp.returncode != 0:
            raise ValueError(
                f"TODO creation failed for phase '{phase}':\n{sp.stderr.strip()}"
            )
        todo_add_result = json.loads(sp.stdout) if sp.stdout.strip() else None

    elif phase == "clarification":
        if not state.get("clarification_phase_todo_registered"):
            todo_pending = True

    # S6 (project-tracking-staleness) — phase-boundary marker (M6 write-side),
    # event=start: reflect this phase entry on the MAIN-PINNED spine as the LAST
    # step, after the durable state write + sync wrap + todo routing, OUTSIDE the
    # rollback try-block above — a marker fail-loud (WriteVerificationError)
    # surfaces the miss without rolling back a transition that genuinely happened
    # (retriable via the `write-phase-marker` CLI). Plain-mode topics are skipped
    # cleanly inside write_phase_marker (no spine → no write, no raise).
    phase_marker = write_phase_marker(session_id, "start", phase)

    return {
        "status": "phase_started",
        "topic": f"{proj}__{topic}",
        "phase": phase,
        "history_entry": entry,
        "todo_add_result": todo_add_result,
        "todo_pending": todo_pending,
        "sync_receipts": sync_receipts,
        "phase_marker": phase_marker,
    }


def phase_stop(session_id):
    """Append a stop event to phase_history; phase field unchanged (A2)."""
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    current = state.get("phase")
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "event": "stop",
        "phase": current,
        "at": now,
        "session_id": session_id,
    }
    state.setdefault("phase_history", []).append(entry)
    state["updated"] = now

    # Slice L A7 — snapshot for rollback on sync failure.
    # S3/A4: proj holds the TOPIC slug and topic holds the PROJECT slug, so the
    # order below matches `_topic_path`'s declared halves. It read `(topic,
    # proj)` until S3 — a mirrored path that is not this record. The locals are
    # deliberately NOT renamed; S4/A5's keyword form PRESERVES that double swap
    # — `topic_slug=proj` is right and `topic_slug=topic` would be wrong.
    state_path = _topic_path(topic_slug=proj, project_slug=topic)
    prev_state_bytes = state_path.read_bytes() if state_path.exists() else None

    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)

    sync_receipts = None
    try:
        from taskmanagement import (  # type: ignore
            sync_phase_stop as _tm_sync_phase_stop,
            WriteVerificationError as _tm_WriteVerificationError,
        )
        # S3/A4: these wrote the two halves SWAPPED until S3.
        if "topic_slug" not in state:
            state["topic_slug"] = proj
        if "project_slug" not in state:
            state["project_slug"] = topic
        try:
            sync_receipts = _tm_sync_phase_stop(state, session_id=session_id)
        except _tm_WriteVerificationError:
            # S3/A4 — see phase_start for the full note: best-effort recovery
            # that never deletes, and that absorbs its own OSError (only) so
            # the sync failure is what propagates.
            if prev_state_bytes is not None:
                _restore_topic_state(state_path, prev_state_bytes)
            else:
                _archive_topic_state(state_path)
            raise
    except ImportError:
        sync_receipts = {"status": "skipped", "reason": "taskmanagement_not_importable"}

    # S6 (project-tracking-staleness) — phase-boundary marker (M6 write-side),
    # event=stop. Last step, outside the rollback try-block; spine-bound only.
    phase_marker = write_phase_marker(session_id, "stop", current)

    return {
        "status": "phase_stopped",
        "topic": f"{proj}__{topic}",
        "phase": current,
        "stop_entry": entry,
        "sync_receipts": sync_receipts,
        "phase_marker": phase_marker,
    }


def phase_status(session_id):
    """Return current phase and history as a dict (A2).

    Slice F: the legacy `classification` + `applicable_phases` fields are gone;
    every topic now has access to the full 5-phase sequence.
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        return {"status": "no_active_project"}
    return {
        "status": "ok",
        "topic": f"{proj}__{topic}",
        "phase": state.get("phase"),
        "applicable_phases": list(PHASE_SEQUENCE),
        "phase_complete": state.get("phase_complete", {}),
        "clarification_phase_oqs_cleared": state.get("clarification_phase_oqs_cleared", False),
        "clarification_phase_todo_registered": state.get("clarification_phase_todo_registered", False),  # Slice C-ii
        "open_decision_checkpoint": state.get("open_decision_checkpoint"),
        "phase_history": state.get("phase_history", []),
    }


def _render_phase_event(entry):
    """One-line human-readable summary for a phase_history entry (S3, M6 read-side).

    Handles the two union shapes — transition/backfill `{from,to,at,source,...}`
    and stop `{event:"stop",phase,at,...}` — plus a safe fallback for a malformed
    entry. Deterministic; never raises (the caller has already isinstance-guarded
    the entry to a dict).
    """
    at = entry.get("at") or ""
    date = at[:10] if isinstance(at, str) else ""
    if entry.get("event") == "stop":
        return f"session stopped in {entry.get('phase')} ({date})"
    to = entry.get("to")
    if to is not None or "from" in entry:
        frm = entry.get("from")
        src = entry.get("source")
        arrow = f"{frm}→{to}" if frm is not None else f"→{to}"
        return f"{arrow} ({date}, {src})" if src else f"{arrow} ({date})"
    # Unknown/malformed shape — surface it without raising.
    return f"(unrecognized phase event, {date})"


def recent_phase_events(session_id):
    """Return the most-recent-session window of phase_history, rendered for the
    /work-start read-side surfacing (M6 read-side, S3). State-dir only — NEVER
    reads the spine or any worktree path, so it works identically from a topic
    worktree (where the main-owned spine is sparse-excluded).

    "Recent window" = entries after the second-most-recent `stop` event (all
    entries if fewer than two stops) — i.e. the most-recently-completed session's
    events plus anything after it, the scannable slice the operator reconciles
    against the spine prose (OQ4).
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        return {"status": "no_active_project"}
    history = state.get("phase_history", []) or []
    # isinstance-guard EVERY element before any .get(...) so a manually-corrupted
    # non-dict entry is skipped, never an AttributeError.
    history = [e for e in history if isinstance(e, dict)]
    stop_idxs = [i for i, e in enumerate(history) if e.get("event") == "stop"]
    start = (stop_idxs[-2] + 1) if len(stop_idxs) >= 2 else 0
    window = history[start:]
    events = [{**e, "summary": _render_phase_event(e)} for e in window]
    window_start = window[0].get("at") if window else None
    return {
        "status": "ok",
        "topic": f"{proj}__{topic}",
        "window_start": window_start,
        "count": len(events),
        "events": events,
    }


def phase_complete_cmd(session_id, oqs_cleared=False, auth_token=None):
    """Mark current phase as complete (A2/A4).

    For clarification + oqs_cleared=True: also sets clarification_phase_oqs_cleared=True
    (the OQ16-a thin boolean hook; semantic = OQ16-b / Slice D).
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    current = state.get("phase")
    if current is None:
        raise ValueError("No active phase. Call 'phase-start' first.")

    # OQ14: block if open decision-checkpoint
    dc = state.get("open_decision_checkpoint")
    if dc:
        raise ValueError(
            f"Cannot complete: open decision checkpoint — "
            f"'{dc.get('description', '')}'. "
            "Resolve with: decision-checkpoint SESSION_ID resolve --auth TOKEN"
        )

    if oqs_cleared:
        expected_oqs = _make_oqs_auth_token(session_id, current)
        if auth_token != expected_oqs:
            raise ValueError(
                f"--oqs-cleared requires auth token:\n"
                f"  {expected_oqs}\n"
                "Copy this token exactly, confirm, then retry."
            )

    now = datetime.now(timezone.utc).isoformat()
    state.setdefault("phase_complete", {})[current] = True
    if oqs_cleared and current == "clarification":
        state["clarification_phase_oqs_cleared"] = True
    state["updated"] = now
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "phase_marked_complete",
        "topic": f"{proj}__{topic}",
        "phase": current,
        "oqs_cleared_set": oqs_cleared and current == "clarification",
    }


def _existing_slug_tagged_todo_line(state, todo_file=None):
    """The body of an existing `[<slug>-<ts>]`-tagged open TODO line for this
    topic, or None. Used by `register_session_todo` to avoid minting a second
    line for a topic already registered at ship (auto-registration S-B)."""
    spine = (state or {}).get("thought_file_path")
    if not spine:
        return None
    tag = f"[{_timestamped_slug_from_spine(spine)}]"
    if todo_file:
        target = Path(todo_file)
    else:
        root = (state or {}).get("project_root")
        if not root:
            return None
        target = Path(root) / "TODO.md"
    if not target.exists():
        return None
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for ln in text.splitlines():
        stripped = ln.strip()
        if stripped.startswith("- [ ]") and tag in stripped:
            return stripped.split("- [ ]", 1)[1].strip()
    return None


def register_session_todo(session_id, todo_text, bucket, todo_file=None):
    """Register the first-session clarification-phase TODO item (OQ20b / Slice C-ii).

    Idempotent: returns already_registered if flag already set.
    Sets clarification_phase_todo_registered=True on success.
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    if state.get("clarification_phase_todo_registered"):
        return {"status": "already_registered", "topic": f"{proj}__{topic}"}

    # Cross-registrar de-duplication (auto-registration Mode-C plan, S-B). A
    # topic auto-registered at ship already carries a slug-tagged `[Thought]`
    # line; adding a second here would duplicate it. Recognise that line — but
    # credit it as a REGISTRATION only when it actually passes the framing bar,
    # because this flag also gates `phase_complete_predicate(state,
    # "clarification")`. An existing-but-unframed line is reported as such and
    # leaves the flag clear, so the clarification gate still demands real framing.
    _existing = _existing_slug_tagged_todo_line(state, todo_file)
    if _existing is not None:
        import todo as _todo_mod
        _framed = _todo_mod.cmd_validate_framing(_existing)["status"] == "pass"
        if _framed:
            state["clarification_phase_todo_registered"] = True
            state["updated"] = datetime.now(timezone.utc).isoformat()
            _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
        return {
            "status": "already_registered" if _framed else "line_exists_unframed",
            "topic": f"{proj}__{topic}",
            "framed": _framed,
            "existing_line": _existing,
        }

    todo_script = Path(__file__).parent / "todo.py"
    project_root = state.get("project_root")
    cmd = ["python3", str(todo_script), "add", todo_text,
           "--bucket", bucket, "--validate-framing"]
    if todo_file:
        cmd += ["--file", todo_file]
    elif project_root:
        cmd += ["--project", project_root]
    sp = subprocess.run(cmd, capture_output=True, text=True)
    if sp.returncode != 0:
        raise ValueError(
            f"TODO registration failed:\n{sp.stderr.strip()}"
        )
    todo_add_result = json.loads(sp.stdout) if sp.stdout.strip() else None

    now = datetime.now(timezone.utc).isoformat()
    state["clarification_phase_todo_registered"] = True
    state["updated"] = now
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "registered",
        "topic": f"{proj}__{topic}",
        "todo_add_result": todo_add_result,
    }


# ---------------------------------------------------------------------------
# A1 — ship-time topic auto-registration (auto-registration Mode-C plan, S-B)
# ---------------------------------------------------------------------------

# The honest marker that a tracking line was minted by code rather than framed by
# a human. It IS the obligation record — there is no second store. It is stripped
# only when the line passes `todo.cmd_validate_framing` (the same structural bar
# every manual `[Thought]` line passes), by the S-C reconcile pass.
AUTO_REGISTERED_MARKER = "[auto-registered]"


def _timestamped_slug_from_spine(path):
    """The `<slug>-<ts>` stem used as a TODO line's slug tag (bookkeeping-model
    §11). Prefers the text up to and including a 14-digit timestamp, so a
    scope-keyed `<slug>-<ts>_<SCOPE>_PLAN.md` still yields `<slug>-<ts>`. Falls
    back to the TYPE-stripped stem for a legacy untimestamped file."""
    stem = Path(str(path)).name
    if stem.endswith(".md"):
        stem = stem[:-len(".md")]
    m = re.match(r"^(.*?-\d{14})", stem)
    if m:
        return m.group(1)
    for suf in ("_THOUGHT", "_PLAN", "_DESIGN", "_RESEARCH", "_CLAIMS"):
        if stem.endswith(suf):
            return stem[:-len(suf)]
    return stem


def _project_dir_for_spine(spine_path):
    """The leaf project DIRECTORY that owns a spine (the path counterpart of
    `canonical_project_for_spine`'s slug). PROJECTS_ROOT for a cross-cutting
    spine. Used only to aim `todo.py`'s target resolution."""
    spine_real = _norm_path(spine_path)          # S3: the one normaliser
    root_real = _norm_path(PROJECTS_ROOT)
    start = spine_real.parent
    if not os.path.isdir(str(start)):
        return root_real
    try:
        start.relative_to(root_real)
    except ValueError:
        return root_real
    return _nearest_leaf_or_root(start, root_real)


def _auto_register_line_text(slug_ts, spine_path, declared_n=None):
    """The `[Thought]` line body minted for an unregistered topic.

    Deliberately does NOT satisfy `cmd_validate_framing`: it carries no
    `Problem:` / `Context:` / `Guiding policy:` because the system fabricates no
    framing. The marker + the failing validation ARE the standing obligation.
    """
    words = [w for w in re.split(r"[-_]+", re.sub(r"-\d{14}$", "", slug_ts)) if w]
    title = " ".join(w.capitalize() for w in words) or slug_ts
    spine_stem = Path(str(spine_path)).name
    if spine_stem.endswith(".md"):
        spine_stem = spine_stem[:-len(".md")]
    text = (f"[Thought] {AUTO_REGISTERED_MARKER} [{slug_ts}] **{title}** — "
            f"auto-registered at ship; framing incomplete. "
            f"Master plan: [[{spine_stem}]].")
    if declared_n:
        text += f" Sessions: 1/{declared_n} done."
    return text


def auto_register_topic(session_id, spine_path, bucket="NOW", todo_file=None):
    """Mint the tracking layer for a topic that shipped without `/clarification`.

    A deterministic CODE side-effect at the ship boundary — no model call, no
    operator memory. Identity is DERIVED from the ship's own anchoring artifact:
    the project half via `canonical_project_for_spine` (which always resolves —
    ``"Root"`` for a cross-cutting spine, never ``None``), the topic half from the
    spine slug. When NO anchoring artifact is available there is nothing to derive
    from, so the mint is SKIPPED with an operator-facing warning — refuse-to-guess.
    The skip signal is the ABSENCE of the artifact, never a ``None`` project.

    **Torn-state invariant (ordering).** The tracking line is written FIRST, the
    state binding SECOND. A crash after the line leaves the topic UNBOUND — which
    the omission Stop-hook still catches; a crash before it leaves nothing, also
    caught. The one state that must never exist is bound-but-untracked, because a
    bound `_active_topic` makes the omission hook pass silently. For the same
    reason the state and its ``thought_file_path`` go out in a SINGLE atomic
    ``_write_topic_state`` — never a create-then-backfill two-step, which would
    expose a bound-but-spineless window.

    **Idempotency is PER-SURFACE, never all-or-nothing.** Each surface is
    independently checked and completed, keyed on the stable slug tag
    ``[<slug>-<ts>]`` — NOT on the ``[auto-registered]`` marker, so an
    operator-edited (or already-discharged) line is still recognised and not
    duplicated. A re-invocation after a crashed mint completes only what is
    missing, and NEVER skips the state binding merely because the line exists.

    Returns a per-surface report; never raises for an ordinary miss.
    """
    from bookkeeping_lock import bookkeeping_lock
    import taskmanagement as _tm

    # --- refuse-to-guess: no anchoring artifact, nothing to derive from -----
    if not spine_path:
        return {"status": "skipped", "reason": "no_anchoring_artifact",
                "warning": "ship carried no anchoring artifact (no _THOUGHT/_PLAN) "
                           "— auto-registration skipped; register the topic manually"}
    spine = Path(str(spine_path))
    if not spine.exists():
        return {"status": "skipped", "reason": "anchoring_artifact_missing",
                "spine": str(spine),
                "warning": f"anchoring artifact not found on disk: {spine} "
                           "— auto-registration skipped; register the topic manually"}

    # ONE derivation, two views. `slug` and `slug_ts` are two readings of the
    # SAME stem parse, so the TODO line's slug tag and the topic-state key can
    # never disagree — a divergence would tag the line under one topic and bind
    # the state under another, and a later grep by either identifier would miss
    # the other.
    #
    # `_slug_from_spine_path` IS used here as of S2/A4, and the comment it
    # replaces said the opposite. That old comment was CORRECT about the OLD
    # function — it stripped the TYPE suffix before matching the timestamp, so a
    # scope-keyed `<slug>-<ts>_<SCOPE>_PLAN.md` left it with a trailing
    # `_<SCOPE>` and an unstripped timestamp — and the response was to open-code
    # the strip here instead, which is what made this the SECOND grammar. A4
    # closes that at the shared locus rather than by adding a third: the shared
    # function is now `_timestamped_slug_from_spine` minus the timestamp, i.e.
    # exactly the line this used to write out, so the two views stay two
    # readings of one parse and the creation path reads the same one.
    slug_ts = _timestamped_slug_from_spine(spine)
    slug = _slug_from_spine_path(spine)
    project = canonical_project_for_spine(spine)      # always resolves
    project_dir = _project_dir_for_spine(spine)
    todo_target = Path(todo_file) if todo_file else (project_dir / "TODO.md")

    declared_n = None
    try:
        declared_n = _tm.declared_slice_count(spine) or None
    except Exception:                                  # observability, never fatal
        declared_n = None

    report = {"status": "registered", "topic": f"{slug}__{project}",
              "slug_tag": slug_ts, "spine": str(spine),
              "todo_file": str(todo_target), "declared_slices": declared_n}

    # --- SURFACE 1 (FIRST): the tracking line -------------------------------
    slug_tag_literal = f"[{slug_ts}]"
    try:
        with bookkeeping_lock(todo_target):
            existing = (todo_target.read_text(encoding="utf-8")
                        if todo_target.exists() else "")
            if slug_tag_literal in existing:
                report["todo_line"] = "already_present"
            else:
                import io
                import contextlib
                import todo as _todo
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = _todo.cmd_add(
                        _auto_register_line_text(slug_ts, spine, declared_n),
                        bucket, None, str(todo_target), None, str(project_dir),
                        validate_framing=False,          # framing is the OBLIGATION
                    )
                if rc != 0:
                    report["status"] = "partial"
                    report["todo_line"] = "failed"
                    report["todo_line_error"] = buf.getvalue().strip()
                else:
                    report["todo_line"] = "minted"
            if report.get("todo_line") in ("minted", "already_present"):
                _tm.verify_write(
                    "todo_line", todo_target, slug_tag_literal,
                    r"^-\s*\[\s*\].*" + re.escape(slug_tag_literal) + r".*$")
    except Exception as e:                             # non-blocking at ship
        report["status"] = "partial"
        report["todo_line"] = "failed"
        report["todo_line_error"] = f"{type(e).__name__}: {e}"

    # --- SURFACE 2 (SECOND): the state binding ------------------------------
    # Runs when surface 1 SUCCEEDED (minted now, or already present from an
    # earlier run) — the crash-after-line torn state is repaired exactly here.
    #
    # It must NOT run when surface 1 failed. A `cmd_add` can fail SOFTLY (a
    # non-zero return, no exception — e.g. no `TODO.md` at the resolved target),
    # and binding after a soft failure would produce the one state the
    # Guiding Policy forbids: bound-but-untracked, which a bound `_active_topic`
    # makes the omission hook pass silently. It would also not self-heal — a
    # retry would read `already_bound` and skip, while the line kept failing.
    if report.get("todo_line") not in ("minted", "already_present"):
        report["status"] = "partial"
        report["topic_state"] = "skipped_line_failed"
        report.setdefault(
            "warning",
            "the tracking line could not be written, so the topic was left "
            "UNBOUND on purpose (binding without a line is the one state the "
            "omission gate cannot see) — fix the TODO target and re-run")
        return report

    try:
        state_path = _topic_path(topic_slug=slug, project_slug=project)
        now = datetime.now(timezone.utc).isoformat()
        # Serialized on the SAME lock as surface 1 (re-entrant, keyed on the
        # topic's repo) so a concurrent `/work-done` + `/close` pair cannot lose
        # an update on this read-modify-write (bookkeeping-model.md §4b).
        with bookkeeping_lock(todo_target):
            try:
                state = _read_json(state_path)
            except json.JSONDecodeError:
                state = None
            if state is not None and state.get("thought_file_path"):
                report["topic_state"] = "already_bound"
            else:
                state = _auto_register_state(state, slug, project,
                                             project_dir, spine, now)
                # State + spine in ONE atomic write — no bound-but-spineless window.
                _write_topic_state(topic_slug=slug, project_slug=project, state=state)
                _tm.verify_write("topic_state_json", state_path,
                                 str(spine), "thought_file_path")
                report["topic_state"] = "bound"
        if session_id:
            # Region 4 of 4 (Cluster-B S0) — the `/close` path.
            #
            # NOTE the lock this takes, and the one it deliberately does NOT.
            # The `with bookkeeping_lock(todo_target):` block just above closes
            # two lines up, and `todo_target` keys on the PROJECTS repo. This
            # read-modify-write is on `~/.claude/state/.../_active.json`, so
            # extending that block downward over these lines — the obvious-looking
            # edit — would take the wrong lock and split the `_active.json` writer
            # set across two of them, leaving `prune-active`'s harness-side flock
            # serializing against nothing. `_active_lock()` is the only correct
            # acquisition here (obligation 11).
            with _active_lock():
                active = read_active()
                cur = active.get(session_id) or {}
                if (cur.get("topic_slug") != slug
                        or cur.get("active_project") != project):
                    active[session_id] = {"topic_slug": slug,
                                          "active_project": project,
                                          "updated": now}
                    write_active(active)
                    report["session_binding"] = "bound"
                else:
                    report["session_binding"] = "already_bound"
    except Exception as e:
        report["status"] = "partial"
        report["topic_state"] = "failed"
        report["topic_state_error"] = f"{type(e).__name__}: {e}"

    return report


def _auto_register_state(state, slug, project, project_dir, spine, now):
    """The topic-state dict an auto-registration writes — state AND spine in one
    payload, so `_write_topic_state` never publishes a bound-but-spineless
    intermediate. Patches an existing record in place; mints a fresh one when
    none exists."""
    if state is None:
        state = {
            "topic_slug": slug,
            "project_slug": project,
            "bypass_marker": False,
            "bypass_reason": None,
            "project_root": str(project_dir),
            "workflow_draft_confirmed_marker": None,
            "created": now,
            "phase": None,
            "phase_history": [],
            "phase_complete": {},
            "kernel_subset_by_phase": None,
            "clarification_phase_oqs_cleared": None,
            "open_decision_checkpoint": None,
            "clarification_step": 0,
            "clarification_payloads": {},
            "clarification_resume_source": None,
            "todo_line_ref": None,
            "intake_source": "auto-registered",
        }
    # NB: `clarification_phase_todo_registered` is deliberately NOT set here.
    # That flag attests "the VALIDATED clarification-phase TODO was registered",
    # and it also gates `phase_complete_predicate(state, "clarification")` —
    # pre-setting it at ship time would let a topic clear the clarification-
    # complete gate without a framed line ever being registered. Cross-registrar
    # de-duplication is handled at its true locus instead: `register_session_todo`
    # recognises an existing slug-tagged line (see its own guard).
    state["thought_file_path"] = str(spine)
    state["auto_registered_at"] = now
    state["updated"] = now
    return state


def decision_checkpoint_open(session_id, description):
    """Open a pending decision checkpoint (A3/OQ14).

    While open, phase-start and phase-complete refuse (C4).
    Returns the resolve token so the AI can surface it to the user.
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    now = datetime.now(timezone.utc).isoformat()
    state["open_decision_checkpoint"] = {
        "description": description,
        "opened_at": now,
        "session_id": session_id,
    }
    state["updated"] = now
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "decision_checkpoint_opened",
        "topic": f"{proj}__{topic}",
        "description": description,
        "resolve_auth_token": _make_dc_auth_token(session_id),
    }


def decision_checkpoint_resolve(session_id, auth_token):
    """Resolve (clear) the pending decision checkpoint (A3/OQ14)."""
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    dc = state.get("open_decision_checkpoint")
    if not dc:
        raise ValueError("No open decision checkpoint to resolve.")
    expected = _make_dc_auth_token(session_id)
    if auth_token != expected:
        raise ValueError(
            f"Invalid auth token. Required:\n  {expected}\n"
            "Copy this token exactly, then retry."
        )
    now = datetime.now(timezone.utc).isoformat()
    state["open_decision_checkpoint"] = None
    state["updated"] = now
    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)
    return {
        "status": "decision_checkpoint_resolved",
        "topic": f"{proj}__{topic}",
        "resolved_at": now,
    }


# ---------------------------------------------------------------------------
# Topic-orientation primitive (Slice C — plan: cryptic-mapping-torvalds.md)
# ---------------------------------------------------------------------------
# One Code-layer read-only aggregator that owns spine/plan/state file-format
# knowledge. Hook scripts and skill SKILL.md consumers branch on the returned
# verdict instead of reading those files directly.
# (code_first_architecture.md:85 strong-implementation rule.)

_SOLUTION_ALT_RE = re.compile(r"^###\s+Solution Alternative (\d+)", re.MULTILINE)
_PROMPT_SPINE_RE = re.compile(r"[^\s\"'`()]+_THOUGHT\.md")
_PROMPT_PLAN_FILE_RE = re.compile(r"[^\s\"'`()]+_PLAN\.md")
_PROMPT_HARNESS_PLAN_RE = re.compile(r"[^\s\"'`()]*\.claude/plans/[\w-]+\.md")
# Filename-only fallback: paths in prompts often contain spaces (iCloud Mobile
# Documents) which the non-whitespace regex above cannot capture. Match the
# leaf filename and resolve via known parent directories.
_PROMPT_SPINE_NAME_RE = re.compile(r"\b([\w-]+_THOUGHT\.md)\b")
_PROMPT_PLAN_NAME_RE = re.compile(r"\b([\w-]+_PLAN\.md)\b")
_SPINE_HARNESS_PLAN_REF_RE = re.compile(r"~?/?[\w/.-]*\.claude/plans/([\w-]+)\.md")
_RESEARCH_PIPELINE_DIR = Path.home() / ".claude" / "state" / "research_pipeline"
_HARNESS_PLANS_DIR = Path.home() / ".claude" / "plans"


def _resolve_path_candidate(raw, project_root=None):
    """Expand and resolve a raw path token to an existing Path or None.

    Accepts absolute paths, relative paths (resolved against project_root,
    PROJECTS_ROOT, and cwd), and bare filenames like `<slug>_THOUGHT.md` or
    `<slug>_PLAN.md` (resolved against the standard staging dirs:
    PROJECTS_ROOT/Thoughts/, project_root/Thoughts/, and ~/.claude/plans/).
    The bare-filename branch is what catches handoff prompts where the path
    contains spaces (iCloud Mobile Documents) and the non-whitespace regex
    only captured the trailing filename.
    """
    if not raw:
        return None
    s = raw.strip().strip("`'\"")
    p = Path(s).expanduser()
    if p.is_absolute() and p.exists():
        return p
    candidates = []
    if project_root:
        candidates.append(Path(project_root) / s)
    candidates.append(PROJECTS_ROOT / s)
    candidates.append(Path.cwd() / s)
    # Bare-filename fallback for _THOUGHT / _PLAN files.
    if "/" not in s:
        candidates.append(PROJECTS_ROOT / "Thoughts" / s)
        if project_root:
            candidates.append(Path(project_root) / "Thoughts" / s)
        candidates.append(_HARNESS_PLANS_DIR / s)
    for c in candidates:
        if c.exists():
            return c
    return None


def _parse_prompt_for_artifacts(prompt_body):
    """Extract _THOUGHT.md and plan-path references from prompt text.

    Returns (spine_refs, plan_refs). Both contain a mix of absolute paths
    (when the regex captured a full path with no embedded whitespace) and
    leaf filenames (fallback for paths containing spaces — common in iCloud
    Mobile Documents). _resolve_path_candidate handles both shapes.
    """
    if not prompt_body:
        return [], []
    seen_s = set()
    spine_refs = []
    for m in _PROMPT_SPINE_RE.finditer(prompt_body):
        ref = m.group(0)
        if ref not in seen_s:
            seen_s.add(ref)
            spine_refs.append(ref)
    for m in _PROMPT_SPINE_NAME_RE.finditer(prompt_body):
        ref = m.group(1)
        if ref not in seen_s:
            seen_s.add(ref)
            spine_refs.append(ref)
    seen_p = set()
    plan_refs = []
    for pattern in (_PROMPT_HARNESS_PLAN_RE, _PROMPT_PLAN_FILE_RE):
        for m in pattern.finditer(prompt_body):
            ref = m.group(0)
            if ref not in seen_p:
                seen_p.add(ref)
                plan_refs.append(ref)
    for m in _PROMPT_PLAN_NAME_RE.finditer(prompt_body):
        ref = m.group(1)
        if ref not in seen_p:
            seen_p.add(ref)
            plan_refs.append(ref)
    return spine_refs, plan_refs


def _classify_intake_source(state, session_id, spine_path, plan_path):
    """Derive intake_source_class from observed signals."""
    rp_file = _RESEARCH_PIPELINE_DIR / f"RP-{session_id}.json"
    if rp_file.exists():
        return "research"
    if state is not None:
        src = state.get("intake_source")
        if src == "plain":
            return "plain"
        if src == "thought":
            return "thought-bound"
        if src == "worktree-origin":
            return "worktree-origin"
        if src == "auto-registered":
            # A ship-time mint (auto-registration Mode-C plan, A1). Classify by
            # what the anchoring artifact ACTUALLY is, never by a blanket
            # mapping: only a `_THOUGHT` spine can carry locked Discovery, which
            # is what `thought-bound` means to `/work-start`. A `_PLAN`
            # anchoring artifact (the Mode-C ship) has no Discovery section, so
            # it classifies as `worktree-origin` — plan-exists, no Discovery —
            # and is NOT routed through the thought-bound phase-token path as
            # though it had been clarified.
            recorded = (state.get("thought_file_path") or "")
            return ("thought-bound" if recorded.endswith("_THOUGHT.md")
                    else "worktree-origin")
        # Legacy / null intake — infer from state shape. Worktree-origin
        # signature: project_root None AND thought_file_path None (the
        # persisted shape after `create_topic` runs inside a linked worktree
        # per `_in_worktree()` override at pre_plan_gates.py:480).
        if (
            state.get("project_root") is None
            and state.get("thought_file_path") is None
        ):
            return "worktree-origin"
        # State present + spine resolved → thought-bound. The plan landing
        # under ~/.claude/plans/ does NOT downgrade an existing thought-bound
        # topic — that path is just the harness-slug routing for plans
        # authored in a linked worktree (Mode C plans in plan-gates.md).
        if spine_path is not None:
            return "thought-bound"
    # No state — classify by plan-path location.
    if plan_path is not None and spine_path is None:
        try:
            plan_path.resolve().relative_to(_HARNESS_PLANS_DIR.resolve())
            return "worktree-origin"
        except ValueError:
            pass
    if spine_path is not None:
        return "thought-bound"
    return "none"


def _infer_phase_from_spine(spine_locked, spine_solution_n, plan_path):
    """Return the deepest phase the spine evidence is consistent with."""
    if not spine_locked:
        return None
    if plan_path is not None and plan_path.exists():
        return "implementation"
    if spine_solution_n:
        return "planning"
    return "clarification"


def topic_orient(session_id, prompt_body=None, cwd=None):
    """Read-only inference primitive (Slice C).

    Aggregates topic-state JSON + spine markers + plan path + TODO-line
    presence + prompt-body references into one structured verdict. No
    writes, no migrations, no side effects.

    Returns dict with keys: verdict, phase_observed, phase_inferred_from_spine,
    backfill_needed, intake_source_class, evidence.
    """
    evidence = []
    proj, topic, state = _resolve_topic(session_id)

    phase_observed = state.get("phase") if state else None
    phase_history = (state.get("phase_history") or []) if state else []
    project_root = state.get("project_root") if state else None
    thought_file_path = state.get("thought_file_path") if state else None

    if state is None:
        evidence.append("topic_state: absent")
    else:
        evidence.append(f"topic_state.phase={phase_observed!r}")
        evidence.append(f"topic_state.intake_source={state.get('intake_source')!r}")

    # Hot-path short-circuit (Design Review interaction-effects mitigation):
    # post-backfill implementation sessions skip spine re-read. Common case
    # after `/work-start` runs once and keeps the gate cheap for
    # `research-scope-gate.sh` per-Agent-call.
    if phase_observed == "implementation" and phase_history:
        intake_class = _classify_intake_source(state, session_id, None, None)
        return {
            "verdict": "mid-flight",
            "phase_observed": phase_observed,
            "phase_inferred_from_spine": None,
            "backfill_needed": False,
            "intake_source_class": intake_class,
            "evidence": evidence + ["hot-path: phase=implementation + phase_history non-empty"],
        }

    if phase_observed == "closing":
        intake_class = _classify_intake_source(state, session_id, None, None)
        return {
            "verdict": "completed",
            "phase_observed": phase_observed,
            "phase_inferred_from_spine": None,
            "backfill_needed": False,
            "intake_source_class": intake_class,
            "evidence": evidence + ["phase=closing"],
        }

    # Resolve spine path: state pointer first, prompt-body parse second.
    spine_path = None
    if thought_file_path:
        p = Path(thought_file_path)
        if not p.is_absolute():
            if project_root:
                p = Path(project_root) / thought_file_path
            else:
                p = PROJECTS_ROOT / thought_file_path
        if p.exists():
            spine_path = p
            evidence.append(f"spine (from state): {spine_path}")
        else:
            evidence.append(f"spine: NOT_FOUND ({p})")

    prompt_spine_refs, prompt_plan_refs = _parse_prompt_for_artifacts(prompt_body)
    if prompt_spine_refs:
        evidence.append(f"prompt_body spine refs: {prompt_spine_refs}")
    if prompt_plan_refs:
        evidence.append(f"prompt_body plan refs: {prompt_plan_refs}")

    if spine_path is None:
        for ref in prompt_spine_refs:
            resolved = _resolve_path_candidate(ref, project_root)
            if resolved is not None:
                spine_path = resolved
                evidence.append(f"spine (from prompt): {spine_path}")
                break

    # Read spine markers.
    spine_locked = False
    spine_solution_n = None
    spine_text = None
    spine_corrupt = False
    if spine_path is not None:
        try:
            spine_text = spine_path.read_text(encoding="utf-8")
        except OSError as e:
            evidence.append(f"spine: read failed ({e})")
            spine_corrupt = True

    if spine_text is not None:
        diag = validate_discovery_locked_fields(spine_text)
        if not diag:
            spine_locked = True
            evidence.append("spine: Discovery locked")
        else:
            evidence.append(f"spine: Discovery incomplete ({len(diag)} field diagnostic(s))")
        m = _SOLUTION_ALT_RE.search(spine_text)
        if m:
            spine_solution_n = int(m.group(1))
            evidence.append(f"spine: ### Solution Alternative {spine_solution_n}")

    # Resolve plan path: in-tree alongside spine, then prompt-body refs, then
    # harness-slug fallback referenced from spine `# Implementation Details`.
    plan_path = None
    if spine_path is not None:
        spine_stem = spine_path.stem
        if spine_stem.endswith("_THOUGHT"):
            spine_stem = spine_stem[: -len("_THOUGHT")]
        in_tree = spine_path.parent / f"{spine_stem}_PLAN.md"
        if in_tree.exists():
            plan_path = in_tree
            evidence.append(f"plan (in-tree): {plan_path}")

    if plan_path is None:
        for ref in prompt_plan_refs:
            resolved = _resolve_path_candidate(ref, project_root)
            if resolved is not None:
                plan_path = resolved
                evidence.append(f"plan (from prompt): {plan_path}")
                break

    if plan_path is None and spine_text:
        hm = _SPINE_HARNESS_PLAN_REF_RE.search(spine_text)
        if hm:
            candidate = _HARNESS_PLANS_DIR / f"{hm.group(1)}.md"
            if candidate.exists():
                plan_path = candidate
                evidence.append(f"plan (harness-slug from spine): {plan_path}")

    # TODO-line presence (best-effort; absence is informational only).
    todo_present = None
    if state is not None and project_root:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from taskmanagement import _todo_match_pattern_from_state
            pattern = _todo_match_pattern_from_state(state)
        except Exception:
            pattern = None
        if pattern:
            todo_md = Path(project_root) / "TODO.md"
            if todo_md.exists():
                try:
                    txt = todo_md.read_text(encoding="utf-8")
                    todo_present = bool(re.search(pattern, txt, re.IGNORECASE))
                    evidence.append(f"todo_line: {'present' if todo_present else 'absent'}")
                except OSError:
                    pass

    phase_inferred = _infer_phase_from_spine(spine_locked, spine_solution_n, plan_path)
    if phase_inferred:
        evidence.append(f"phase_inferred_from_spine={phase_inferred!r}")

    intake_class = _classify_intake_source(state, session_id, spine_path, plan_path)
    evidence.append(f"intake_source_class={intake_class!r}")

    if spine_corrupt:
        return {
            "verdict": "stalled",
            "phase_observed": phase_observed,
            "phase_inferred_from_spine": None,
            "backfill_needed": False,
            "intake_source_class": intake_class,
            "evidence": evidence,
        }

    def _phase_idx(p):
        return PHASE_SEQUENCE.index(p) if p in PHASE_SEQUENCE else -1

    if phase_inferred is not None and _phase_idx(phase_inferred) > _phase_idx(phase_observed):
        verdict = "mid-flight"
        backfill_needed = True
    elif phase_observed in PHASE_SEQUENCE:
        verdict = "mid-flight"
        backfill_needed = False
    else:
        verdict = "new"
        backfill_needed = False

    return {
        "verdict": verdict,
        "phase_observed": phase_observed,
        "phase_inferred_from_spine": phase_inferred,
        "backfill_needed": backfill_needed,
        "intake_source_class": intake_class,
        "evidence": evidence,
    }


def phase_backfill(session_id, to_phase, evidence_payload, source_summary=None):
    """Atomically reconcile topic-state.phase to a phase observed in the spine.

    Appends one phase_history entry tagged `source="backfill"`. Auto-sets
    phase_complete[prior] = True for every prior phase so downstream
    phase_complete_predicate calls admit subsequent phase-start.

    Idempotent: re-running with phase already at target and the most-recent
    history entry source="backfill" is a no-op.

    Refuses if to_phase ∉ PHASE_SEQUENCE or if an open_decision_checkpoint
    is present.
    """
    if to_phase not in PHASE_SEQUENCE:
        raise ValueError(
            f"Refusing backfill: phase '{to_phase}' not in PHASE_SEQUENCE."
        )
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'create-topic' first."
        )
    if state.get("open_decision_checkpoint"):
        raise ValueError(
            "Refusing backfill: open decision checkpoint must be resolved first."
        )

    current = state.get("phase")
    history = state.get("phase_history") or []
    last_entry = history[-1] if history else {}
    if current == to_phase and last_entry.get("source") == "backfill":
        return {
            "status": "noop",
            "topic": f"{proj}__{topic}",
            "phase": current,
            "reason": "idempotent: phase already at target with backfill provenance",
        }

    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "from": current,
        "to": to_phase,
        "at": now,
        "session_id": session_id,
        "source": "backfill",
        "evidence": evidence_payload,
    }
    if source_summary:
        entry["source_summary"] = source_summary

    state["phase"] = to_phase
    state.setdefault("phase_history", []).append(entry)
    state.setdefault("phase_complete", {})
    to_idx = PHASE_SEQUENCE.index(to_phase)
    for prior in PHASE_SEQUENCE[:to_idx]:
        state["phase_complete"][prior] = True
    state["updated"] = now

    _write_topic_state(topic_slug=proj, project_slug=topic, state=state)

    return {
        "status": "backfilled",
        "topic": f"{proj}__{topic}",
        "phase": to_phase,
        "history_entry": entry,
        "phase_complete": state["phase_complete"],
    }


# ---------------------------------------------------------------------------
# Workflow factcheck orchestration (Plan 6 A5)
# ---------------------------------------------------------------------------

def _build_checker_prompt(draft_text, checker_idx, round_num, prior_issues):
    """Build checker prompt for one round of workflow draft verification."""
    base = (
        f"You are checker {checker_idx + 1} performing an independent factcheck "
        f"(round {round_num}) of a Workflow.md draft.\n\n"
        "Verify ONLY these criteria:\n"
        "(a) Workflow.md uses the template structure: each stage has Goal, "
        "Data sources, Guiding policy, Output, and Automation boundary fields\n"
        "(b) Every stage has all five required fields (Goal / Data sources / "
        "Guiding policy / Output / Automation boundary)\n"
        "(c) Editorial extensions are labeled with (editorial)\n"
        "(d) No fabricated source citations\n\n"
        f"Draft:\n---\n{draft_text}\n---\n"
    )
    if round_num >= 3 and prior_issues:
        base += (
            f"\nThis is a diff-only round. Check ONLY the previously identified issues:\n"
            f"{prior_issues}\n"
        )
    base += "\nRespond with: PASS  -or-  DISCREPANCY: <concise description of issue>"
    return base


def _invoke_checker(draft_path, checker_idx, model, round_num, prior_issues):
    """Invoke one checker via `claude` CLI subprocess; return verdict string."""
    draft_text = ""
    p = Path(draft_path)
    if p.exists():
        draft_text = p.read_text(encoding="utf-8")
    else:
        return f"DISCREPANCY: draft file not found at {draft_path}"

    prompt = _build_checker_prompt(draft_text, checker_idx, round_num, prior_issues)

    try:
        result = subprocess.run(
            ["claude", "--print", "--model", f"claude-{model}-4-6-20251001"
             if model == "haiku" else f"claude-{model}-4-6", prompt],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            return f"DISCREPANCY: checker subprocess failed (exit {result.returncode})"
        return result.stdout.strip() or "PASS"
    except FileNotFoundError:
        return "DISCREPANCY: claude CLI not found — cannot invoke checker"
    except subprocess.TimeoutExpired:
        return "DISCREPANCY: checker timed out"


def _append_validated_via_frontmatter(draft_path, topic_dir):
    """Append validated_via: line to Workflow.md frontmatter on PASS."""
    path = Path(draft_path)
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if "validated_via:" in text:
        return
    validated_line = f"validated_via: {topic_dir}\n"
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            new_text = text[:end] + validated_line + text[end:]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            tmp.rename(path)
            return
    # No frontmatter — prepend one
    new_text = f"---\n{validated_line}---\n\n" + text
    tmp = path.with_suffix(".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.rename(path)


_KL_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_kl_extraction_inputs(proj, topic, files):
    """Validate CLI inputs for the kl_extraction scribe (Session 4b).

    Returns a list of human-readable error strings; empty list = inputs valid.
    The scribe trusts the inputs once this helper returns []. Path-coherence
    enforcement (Q9) lives here, not inside aggregate_kl_extraction_round.
    """
    errors = []

    if proj != "<KL>":
        errors.append(
            f"--proj must be '<KL>' for kl_extraction; got '{proj}' (Q1)."
        )

    if not topic or not _KL_SLUG_RE.match(topic):
        errors.append(
            f"--topic must be a non-empty lowercase-hyphenated slug (book_slug); got '{topic}' (Q2)."
        )

    if not isinstance(files, (list, tuple)) or len(files) < 3:
        errors.append(
            f"At least three per-agent files required (canon §1 voting pattern); got {len(files) if files else 0}."
        )

    path_substring = f"/Sources/Books/{topic}/" if topic else None
    for fp in files or []:
        if path_substring and path_substring not in str(fp):
            errors.append(
                f"Per-agent file path does not contain '{path_substring}' (Q9 path-coherence): {fp}"
            )
        p = Path(fp)
        if not p.exists():
            errors.append(f"Per-agent file does not exist: {fp}")
        elif not os.access(p, os.R_OK):
            errors.append(f"Per-agent file not readable: {fp}")

    return errors


def factcheck_workflow(session_id, draft_path, _checker_fn=None):
    """Delegate to shared engine (Plan 7 A2). Plan 6 CLI surface unchanged."""
    from _factcheck_engine import factcheck_run
    return factcheck_run(
        WORKFLOW_VALIDATION_DIR, draft_path, "workflow", session_id,
        debounce_seconds=0,
        models=["sonnet", "sonnet", "sonnet"],
        _checker_fn=_checker_fn,
        _proj_topic_resolver=_resolve_topic,
    )


def is_plan_validated(session_id, plan_path):
    """Check CONVERGED marker in plan body AND state-file cross-check (anti-tampering).

    Returns:
        (0, "PASS")            marker present AND highest round file shows verdict: PASS
        (1, "state_mismatch")  marker present but state file absent or DIRTY
        (2, "marker_missing")  no CONVERGED marker in plan body
        (3, "no_active_project") no active topic for this session
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        return 3, "no_active_project"

    p = Path(plan_path)
    plan_text = p.read_text(encoding="utf-8") if p.exists() else ""
    marker_present = "<!-- VALIDATION:CONVERGED -->" in plan_text

    # Cross-check against highest round file in plan_validation/<proj>/<topic>/plan/
    plan_kind_dir = PLAN_VALIDATION_DIR / proj / topic / "plan"
    state_pass = False
    if plan_kind_dir.exists():
        round_files = sorted(
            plan_kind_dir.glob("R*.md"),
            key=lambda f: int(f.stem[1:]),
        )
        if round_files:
            last_text = round_files[-1].read_text(encoding="utf-8")
            for line in last_text.splitlines():
                stripped = line.strip()
                if stripped == "verdict: PASS":
                    state_pass = True
                    break
                if stripped.startswith("verdict:") and stripped != "verdict: PASS":
                    break

    if marker_present and state_pass:
        return 0, "PASS"
    if marker_present:
        return 1, "state_mismatch"
    return 2, "marker_missing"


def is_validation_waived(session_id, plan_path, message_path):
    """Check waiver marker in plan body AND literal substring in user's message.

    Honors waiver only when the literal '<!-- VALIDATION:WAIVED:' substring appears
    in the user's most-recent conversation message (audit:419 verbatim 'the user types').
    Distinct from _approval_patterns.py (which handles 'ok'/'approved' phrases).

    Returns:
        (0, "valid")    marker in plan body AND literal substring in user message
        (1, "invalid")  marker missing OR user message does not contain literal substring
    """
    p = Path(plan_path)
    plan_text = p.read_text(encoding="utf-8") if p.exists() else ""
    if "<!-- VALIDATION:WAIVED:" not in plan_text:
        return 1, "invalid"

    msg_p = Path(message_path)
    message_text = msg_p.read_text(encoding="utf-8") if msg_p.exists() else ""
    if "<!-- VALIDATION:WAIVED:" in message_text:
        return 0, "valid"
    return 1, "invalid"


def _session_block_header(date_iso, sid):
    """The `### YYYY-MM-DD session [sid:prefix]` heading BOTH session-block
    writers key on. One definition: the two writers used to mint it separately
    (byte-identical, by luck), and the omission gate matched the shape a third
    way. Idempotency for each writer is on (date, sid_prefix) — this line."""
    sid_short = (sid or "")[:8]
    return f"### {date_iso} session [sid:{sid_short}]"


def _format_metrics_rows(payload):
    """The `/close` metrics rows for `payload`, per Thoughts §7.3, WITHOUT the
    writer marker. Optional fields (omtm, omtm_trajectory, line_in_sand,
    decision_rule_trigger) are skipped if absent — matching §7.3's rule for
    exploration/research thoughts that have no OMTM.
    """
    rows = []

    def add(label, value):
        if value is None or value == "":
            return
        rows.append(f"- {label}: {value}")

    add("Duration", _opt_min(payload.get("duration_min")))
    add("Opus tokens", _opt_k(payload.get("opus_tokens_k")))
    add("Sonnet tokens", _opt_k(payload.get("sonnet_tokens_k")))
    add("Tasks completed", payload.get("tasks_completed"))
    files = payload.get("files_touched")
    if isinstance(files, list):
        files = ", ".join(files) if files else None
    add("Files touched", files)
    add("Gate progress", payload.get("gate_progress"))

    omtm = payload.get("omtm")
    omtm_value = payload.get("omtm_value")
    line = payload.get("line_in_sand")
    if omtm:
        if omtm_value and line:
            add("OMTM (per Gate 4)", f"{omtm} = {omtm_value} (target: {line})")
        elif omtm_value:
            add("OMTM (per Gate 4)", f"{omtm} = {omtm_value}")
        else:
            add("OMTM (per Gate 4)", omtm)

    traj_prior = payload.get("omtm_prior")
    traj_now = payload.get("omtm_value")
    rule_trigger = payload.get("decision_rule_trigger")
    if traj_prior is not None and traj_now is not None:
        rt = rule_trigger if rule_trigger in ("yes", "no") else "n/a"
        add(
            "OMTM trajectory",
            f"{traj_prior} → {traj_now} (decision rule trigger: {rt})",
        )

    rows.extend(_format_delivery_rows(payload))

    if not rows:
        rows.append("- (no metrics captured)")
    return rows


# ---------------------------------------------------------------------------
# The delivery record (streamed-dancing-goose S7 / A7) — ADDITIVE rows on the
# same `/close` block, under the same writer identity and marker. The duration
# and token rows above are untouched; these render only when their payload
# keys are present, so a `/close` that ships nothing writes nothing new.
# ---------------------------------------------------------------------------

def _format_delivery_rows(payload):
    """The delivery rows for `payload`: what shipped and what is next, with
    links. Every row is optional and keyed by its own payload field:

      delivered_slice   "S6" / "S6 — the v2 door files a validator-passing line"
      commit_sha        the landed commit; rendered as a link when `commit_url`
                        is present (see `delivery_links`), else the short sha
      commit_url        link target for the commit row
      diff_url          link target for the diff row (`Diff: [a..b](url)`)
      diff_range        the range label for the diff row, e.g. "748de86..81dfa3d";
                        defaults to the short sha when only `commit_sha` is known
      slice_counter     "6/8 slices done" or the 3-bucket form — rendered verbatim
      next_slice        "S7" / "S7 — /close's block gains the delivery record"

    Pure over the payload; no git, no I/O — the links are derived by
    `delivery_links` (code) at the call site and passed in.
    """
    rows = []

    def add(label, value):
        if value is None or value == "":
            return
        rows.append(f"- {label}: {value}")

    add("Delivered", payload.get("delivered_slice"))
    sha = payload.get("commit_sha")
    if sha:
        short = str(sha)[:8]
        url = payload.get("commit_url")
        add("Commit", f"[{short}]({url})" if url else short)
    diff_url = payload.get("diff_url")
    if diff_url:
        label = payload.get("diff_range") or (str(sha)[:8] if sha else "diff")
        add("Diff", f"[{label}]({diff_url})")
    add("Counter", payload.get("slice_counter"))
    add("Next", payload.get("next_slice"))
    return rows


_REMOTE_SSH_RE = re.compile(r"^(?:ssh://)?git@(?P<host>[^:/]+)[:/](?P<path>.+?)(?:\.git)?/?$")
_REMOTE_HTTPS_RE = re.compile(r"^https?://(?P<host>[^/]+)/(?P<path>.+?)(?:\.git)?/?$")


def remote_web_base(remote_url):
    """`https://<host>/<owner>/<repo>` for a GitHub-shaped remote (ssh or https),
    or None when the URL is not one this can turn into a browsable base. Pure."""
    u = (remote_url or "").strip()
    m = _REMOTE_SSH_RE.match(u) or _REMOTE_HTTPS_RE.match(u)
    if not m:
        return None
    return f"https://{m.group('host')}/{m.group('path')}"


def delivery_links(repo_dir, sha, base_sha=None, remote="origin"):
    """Commit + diff links for `sha` in `repo_dir`, derived from the repo's
    `<remote>` URL — never typed by hand. Returns a dict with `commit_sha`,
    `commit_url`, `diff_url`, `diff_range`; the url fields are None when the
    remote is absent or not browsable, so the rows degrade to the bare sha
    rather than to a broken link. `base_sha` defaults to `sha`'s first parent
    (a single-commit diff); a range is `base..sha`.

    Fail-soft on every git error: the delivery record is content, and a
    missing remote must not fail a `/close`."""
    def _git(*args):
        try:
            return subprocess.run(["git", "-C", str(repo_dir), *args], capture_output=True,
                                  text=True, check=True, timeout=5).stdout.strip()
        except Exception:
            return None

    full = _git("rev-parse", "--verify", f"{sha}^{{commit}}") or str(sha)
    base = base_sha or _git("rev-parse", "--verify", f"{full}^") or None
    web = remote_web_base(_git("remote", "get-url", remote))
    short = full[:8]
    out = {"commit_sha": full, "commit_url": None, "diff_url": None,
           "diff_range": f"{str(base)[:8]}..{short}" if base else short}
    if web:
        out["commit_url"] = f"{web}/commit/{full}"
        if base:
            out["diff_url"] = f"{web}/compare/{base}...{full}"
    return out


def _opt_min(v):
    return f"{v}min" if v not in (None, "") else None


def _opt_k(v):
    if v in (None, ""):
        return None
    try:
        n = float(v)
        return f"~{n:g}k"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
# `## Sessions` — the ONE locator, the per-writer identities, and the ONE
# create-if-absent path (streamed-dancing-goose S1 / A1)
# ---------------------------------------------------------------------------
#
# Two writers share the `## Sessions` section of a spine: `annotate_session`
# (the `/work-done` record — and `/close --annotate`, through the same CLI) and
# `append_metrics` (the `/close` metrics rows). Until S1 the second one wrote
# into `## Metrics` — a LOCKED Discovery field — and, on a same-day re-run,
# replaced everything from the matched `### <date> session [sid:…]` header to
# the next heading, which is where the first writer's bullet lives. Five
# `/work-done` records were destroyed that way. The rules below are what stop
# it structurally rather than by care:
#
#   * ONE locator. `_SESSIONS_HEADING_RE` is the only predicate that says
#     "this line is the `## Sessions` heading" — for both writers, for the
#     omission gate, and for the Phase-Register anchor. It is END-anchored on
#     purpose and must stay so: `extract_heading_body`'s whitespace-bounded
#     predicate would bind `## Sessions Plan`, which precedes the real section
#     in live spines, and a gate reading the wrong section blocks a compliant
#     session.
#   * ONE create path. `ensure_sessions_section` is the only code that adds
#     the heading, and it asserts the insertion point is outside `# Discovery`
#     (a `## ` heading appended inside a Discovery that runs to end-of-file
#     lands inside the last locked field's body and moves the Step-9 hash).
#   * PER-WRITER identity, per LINE. Every line a writer emits carries its own
#     marker, and a writer may replace ONLY lines carrying its marker — never a
#     span to the next heading. Two writers can therefore share one
#     `### <date> session [sid:…]` block and neither can reach the other's lines.
#     The identity is per WRITER, not per CLI: `/close --annotate` goes through
#     `annotate_session` with `writer="close"`, so the omission gate can still
#     tell a `/work-done` record from a `/close`-authored block.
_SESSIONS_HEADING_RE = re.compile(r"^##\s+(?:\d+\.\s+)?Sessions\s*$", re.MULTILINE)
# Same shape as `_SESSIONS_HEADING_RE`, for the second heading the Phase-Register
# anchor (`write_phase_marker`) accepts. It used to compare `line.rstrip()`
# against two exact strings, which disagreed with `_SESSIONS_HEADING_RE` on the
# numbered form (`## 3. Sessions`); converting the site means giving BOTH of its
# headings the regex treatment, not dropping one.
_SLICE_REGISTER_HEADING_RE = re.compile(r"^##\s+(?:\d+\.\s+)?Slice Register\s*$", re.MULTILINE)
# Any H1 / H2 heading line — what ends a `## Sessions` section. An H1 terminates
# it too: on the older spine layout `## Sessions` can be followed directly by
# `# Discovery`, and a section that ran through it would append into the locked
# region.
_SECTION_END_RE = re.compile(r"^#{1,2}\s")
_DISCOVERY_H1 = "# Discovery"
# The one definition of the `# Implementation Details` H1 — shared with
# `link_plan_to_thought` (it used to sit beside the plan-step constants).
_IMPL_DETAILS_HEADER = "# Implementation Details"

# The writer identities. `work-done` is the shipping writer — the ONLY identity
# the omission gate accepts as "this session recorded its ship". `close` is what
# `/close` writes under, for its metrics rows and for `--annotate`.
SESSION_WRITER_WORK_DONE = "work-done"
SESSION_WRITER_CLOSE = "close"
SESSION_WRITERS = (SESSION_WRITER_WORK_DONE, SESSION_WRITER_CLOSE)

# Slice J-1: machine-identifiable tag for the annotate-session-owned bullet
# under each `### YYYY-MM-DD session [sid:<prefix>]` block. Since S1 the tag
# carries the writer (`<!-- annotate-session writer=work-done -->`). The bare
# form is the LEGACY spelling every pre-S1 bullet on disk carries; it is still
# recognised on read and is attributed to `work-done` — `/work-done` was the
# only production caller that minted it unconditionally — but is never written
# again. Bullets without any tag (manual entries) are left untouched.
_ANNOTATE_MARKER = "<!-- annotate-session -->"
_ANNOTATE_MARKER_RE = re.compile(
    r"<!--\s*annotate-session(?:\s+writer=([A-Za-z0-9_-]+))?\s*-->"
)
# The `/close` metrics writer's per-line identity. Every metrics row carries it;
# a same-day re-run replaces exactly the lines that carry it and nothing else.
_CLOSE_METRICS_MARKER = "<!-- close-metrics -->"


def annotate_marker(writer):
    """The annotate-session tag for `writer` (validated against SESSION_WRITERS)."""
    if writer not in SESSION_WRITERS:
        raise ValueError(
            f"Unknown session writer {writer!r}; expected one of {list(SESSION_WRITERS)}."
        )
    return f"<!-- annotate-session writer={writer} -->"


def annotate_marker_writer(line):
    """The writer identity a line's annotate-session tag carries, or None.

    The legacy bare tag reads as `work-done` (see `_ANNOTATE_MARKER`).
    """
    m = _ANNOTATE_MARKER_RE.search(line)
    if m is None:
        return None
    return m.group(1) or SESSION_WRITER_WORK_DONE


def find_sessions_section(lines):
    """`(heading_idx, end_idx)` of the `## Sessions` section in `lines`, or None.

    `end_idx` is exclusive — the index of the next H1/H2 heading line, or
    `len(lines)`. Located by `_SESSIONS_HEADING_RE` and nothing else.
    """
    for i, line in enumerate(lines):
        if _SESSIONS_HEADING_RE.match(line):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if _SECTION_END_RE.match(lines[j]):
                    end = j
                    break
            return i, end
    return None


def _discovery_h1_bounds(lines):
    """`(start_idx, end_idx)` of the `# Discovery` H1 region, or None when the
    spine has no such heading. `end_idx` is exclusive — the next `# ` H1 line,
    or `len(lines)` when Discovery runs to end-of-file. Same boundary rule as
    `_DISCOVERY_SECTION_RE` (`\\n# ` ends it), expressed over lines.
    """
    start = None
    for i, line in enumerate(lines):
        if line.rstrip() == _DISCOVERY_H1:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("# "):
            end = j
            break
    return start, end


def _append_section_outside_discovery(lines, block):
    """Append a new H2 section (`block` = its lines, heading first) at the END of
    the spine, OUTSIDE `# Discovery`: plainly at end-of-file when EOF is not inside
    Discovery (no Discovery at all, or an H1 follows it); when Discovery runs to
    end-of-file, open a top-level boundary — `# Implementation Details` — first
    and place the section beneath it. Returns `(lines, heading_idx)`.

    The one rule for "append a section at the tail" (S1/S2): `ensure_sessions_
    section` and `write_phase_marker`'s no-anchor branch both use it. Appending a
    `## ` heading into a Discovery that runs to EOF lands inside its last locked
    field's body and moves the Step-9 hash — the write the A2 guard refuses."""
    lines = list(lines)
    while lines and lines[-1].strip() == "":
        lines.pop()
    disc = _discovery_h1_bounds(lines)
    if disc is not None and disc[1] >= len(lines):
        lines = lines + ["", _IMPL_DETAILS_HEADER]
    lines = lines + ["", *block]
    return lines, len(lines) - len(block)


def ensure_sessions_section(lines):
    """Return `(lines, heading_idx, created)` with a `## Sessions` section present.

    The ONE create-if-absent path, shared by both writers. Presence is tested
    with `_SESSIONS_HEADING_RE` (so a re-run creates nothing further), and the
    heading is placed OUTSIDE `# Discovery`, in this order:

      1. Under `# Implementation Details` when that H1 exists — the template's
         home for the section — just before `## Next Session Prompt` when that
         heading sits in the same region, else at the region's end.
      2. Else at end-of-file, provided end-of-file is not inside `# Discovery`
         (no Discovery at all, or an H1 follows it).
      3. Else — Discovery runs to end-of-file — open a top-level boundary:
         append `# Implementation Details` and place `## Sessions` beneath it.
         Appending a `## ` heading into a Discovery that runs to EOF would land
         inside its last locked field's body and move the Step-9 hash.

    The result is asserted: the heading index is never inside the Discovery H1
    region. That assertion is what makes "outside Discovery" a property rather
    than an intention.
    """
    found = find_sessions_section(lines)
    if found is not None:
        return lines, found[0], False

    lines = list(lines)
    disc = _discovery_h1_bounds(lines)

    impl_idx = None
    for i, line in enumerate(lines):
        if line.rstrip() == _IMPL_DETAILS_HEADER:
            impl_idx = i
            break

    if impl_idx is not None:
        region_end = len(lines)
        for j in range(impl_idx + 1, len(lines)):
            if lines[j].startswith("# "):
                region_end = j
                break
        nsp_idx = None
        nsp_re = heading_re(NEXT_SESSION_PROMPT_HEADING)
        for j in range(impl_idx + 1, region_end):
            if nsp_re.match(lines[j]):
                nsp_idx = j
                break
        if nsp_idx is not None:
            block = ["## Sessions", ""]
            if nsp_idx > 0 and lines[nsp_idx - 1].strip() != "":
                block = [""] + block
            lines = lines[:nsp_idx] + block + lines[nsp_idx:]
            heading_idx = nsp_idx + (len(block) - 2)
        else:
            insert_at = region_end
            while insert_at > impl_idx + 1 and lines[insert_at - 1].strip() == "":
                insert_at -= 1
            block = ["", "## Sessions", ""]
            lines = lines[:insert_at] + block + lines[insert_at:]
            heading_idx = insert_at + 1
    else:
        lines, heading_idx = _append_section_outside_discovery(lines, ["## Sessions", ""])

    assert _SESSIONS_HEADING_RE.match(lines[heading_idx]), "create path lost its own heading"
    disc_after = _discovery_h1_bounds(lines)
    if disc_after is not None and disc_after[0] < heading_idx < disc_after[1]:
        raise RuntimeError(
            "ensure_sessions_section: insertion point resolved inside `# Discovery` "
            f"(line {heading_idx}) — refusing to create `## Sessions` there."
        )
    return lines, heading_idx, True


def _session_block_bounds(lines, section_start, section_end, header):
    """`(header_idx, block_end)` of the `### … session [sid:…]` block whose
    heading line equals `header` inside the `## Sessions` section, or None.
    `block_end` is exclusive — the next `### ` line or `section_end`.
    """
    for i in range(section_start + 1, section_end):
        if lines[i].rstrip() == header:
            end = section_end
            for j in range(i + 1, section_end):
                if lines[j].startswith("### "):
                    end = j
                    break
            return i, end
    return None


def _atomic_write_text(p, content):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Locked-Discovery WRITE-PATH guard (streamed-dancing-goose S2 / A2)
# ---------------------------------------------------------------------------
#
# The Step-9 lock on the four DISCOVERY_LOCKED_FIELDS was enforced at exactly one
# layer: `_discovery_lock_check.py`, a PreToolUse hook that exits early for any
# tool that is not Edit or Write. Every direct Python write to a spine — the
# `/close` metrics append being the confirmed case — passed it unguarded. This
# section is the second layer, at the write path: a PREDICATE, not a gate
# framework.
#
#   * Predicate-first. `locked_discovery_change` answers ONE question — does the
#     proposed text change a locked body — using the SAME two shared locators the
#     hash and the hook use (`discovery_section_body` + `extract_heading_body`),
#     never a re-derived span. Bodies are compared STRIPPED, exactly as
#     `_discovery_locked_fields_hash` strips them: on a spine whose Discovery runs
#     to end-of-file the last locked body ends at EOF, so a writer that rstrips
#     before appending would otherwise change bytes "inside" it and be refused.
#   * A write that changes no locked body is allowed with NO permission check, so
#     every writer that never touches locked ground is untouched.
#   * Refuse BEFORE the temp write, never between write and replace: the guard
#     runs on the proposed text, and nothing — not even a `.tmp` — is written on
#     refusal.
#   * A heading the locator cannot read (`## Metrics:`) is treated as ABSENT —
#     the same answer the tool-layer hook gives since discovery-field-predicate-
#     coherence A3 removed its refuse-to-guess arm (it locked a spine's author out
#     of the only edit that repairs the heading). The write path gives the same
#     answer on purpose: two answers to "is this field here" is the defect this
#     plan closes. The defence that survives is structural, not a guess: an
#     in-tool DEMOTION (`## Metrics` -> `### Metrics`) still reads as a change
#     (current body is text, proposed body is None) and is refused; a spine
#     already mis-levelled is reported by `_validate-thought-file.py` and fails
#     the plan-exit gate downstream.
#   * Permission = the same signal the hook honours: the bound topic's
#     `clarification_active_session` equals the writing session (Step 9 of
#     /clarification holds it).
#   * `/clarification-v2` is EXEMPT, structurally and explicitly: the v2 door
#     writes the spine from its own repo (`Personal/clarify` →
#     `translating_repository.py`) and never reaches these helpers, and nothing
#     writes a permission signal it could carry (its session id is optional on
#     its ordinary verbs, and it binds its record DURING its first durable write).
#     The exemption is filed as a tracked obligation on the Projects-side
#     `TODO.md`, not retired here — see `SPINE_WRITE_INVENTORY`.
#
# What this layer does NOT cover, named rather than implied: a forged session id
# (the permission signal is a string compare), an edit made through a shell (a
# `sed`/`cat >` on the spine reaches neither layer), and a writer nobody
# registered (the inventory test in `hooks/tests/test_s2_locked_write_guard.py`
# is what keeps that set at zero).

class LockedDiscoveryWriteRefused(ValueError):
    """Raised by `guard_locked_discovery_write` when a write would change a locked
    Discovery field without permission, or when it cannot tell. A ValueError
    subclass on purpose: every CLI verb that catches ValueError already renders
    the `✗ …` + exit-1 shape, and the callers that branch on exit code alone see
    no new code."""


def locked_field_body(text, field):
    """Stripped body of one locked field inside `# Discovery`, or None when the
    spine has no Discovery section or the field is not a heading the anchored
    locator accepts. The one extraction rule — `_discovery_locked_fields_hash`
    and `_discovery_lock_check` read the same span."""
    body = discovery_section_body(text)
    if body is None:
        return None
    all_secs = DISCOVERY_LOCKED_FIELDS + DISCOVERY_MUTABLE_SUBSECTIONS
    content = extract_heading_body(body, field, all_secs)
    return None if content is None else content.strip()


def locked_discovery_change(current_text, proposed_text):
    """The predicate. Returns
        {"locked": bool,            # the spine carries a Step-9 lock marker
         "changed": [field, …],     # locked fields whose stripped body differs
         "discovery_changed": bool} # the whole `# Discovery` body differs
    Pure; reads nothing but its two arguments.

    A field whose heading the locator cannot read is treated as ABSENT here,
    exactly as the tool-layer hook treats it (discovery-field-predicate-coherence
    A3) — one answer to "is this field here", at both layers. A demotion of a
    readable heading still registers: the current body is text and the proposed
    body is None, so the field is `changed`."""
    locked = bool(LOCK_MARKER_RE.search(current_text))
    changed = []
    for field in DISCOVERY_LOCKED_FIELDS:
        cur = locked_field_body(current_text, field)
        if cur is None:
            continue
        if cur != locked_field_body(proposed_text, field):
            changed.append(field)
    return {
        "locked": locked,
        "changed": changed,
        "discovery_changed": (discovery_section_body(current_text)
                              != discovery_section_body(proposed_text)),
    }


def _write_has_lock_permission(session_id):
    """True when the session bound to `session_id` holds the Step-9 edit token
    (`clarification_active_session`) on its topic record — the same signal the
    tool-layer hook honours. No session → no permission."""
    if not session_id:
        return False
    try:
        _proj, _topic, state = _resolve_topic(session_id)
    except Exception:
        return False
    if not state:
        return False
    active = state.get("clarification_active_session")
    return bool(active) and active == session_id


def guard_locked_discovery_write(path, current_text, proposed_text, *,
                                 session_id=None, writer="unknown"):
    """Refuse-before-write for a spine. Returns the predicate's verdict when the
    write may proceed; raises `LockedDiscoveryWriteRefused` when it may not.

    Allow, with no permission check, whenever no locked body changes. Refuse when
    a locked body changes and the session holds no edit token. `writer` names
    the caller in the refusal so the operator knows which path tried.
    """
    verdict = locked_discovery_change(current_text, proposed_text)
    if not verdict["locked"]:
        return verdict
    if not verdict["changed"]:
        return verdict
    if _write_has_lock_permission(session_id):
        return verdict
    names = ", ".join(f"`{f}`" for f in verdict["changed"])
    raise LockedDiscoveryWriteRefused(
        f"Locked field(s) {names} in {path} would be changed by `{writer}`; these "
        f"fields were locked at Step 9 of /clarification and this session holds no "
        f"edit token. Nothing was written. To change them, re-enter Clarification: "
        f"/clarification --from {path}"
    )


def _write_spine_guarded(p, current_text, new_text, *, session_id=None, writer):
    """The ONE spine write path for this module's writers: guard, then atomic write.
    The guard runs on `new_text` before any byte — including the `.tmp` — exists."""
    p = Path(p)
    guard_locked_discovery_write(p, current_text, new_text,
                                 session_id=session_id, writer=writer)
    _atomic_write_text(p, new_text)


# Every production call site that can write a `_THOUGHT.md` spine, with its
# disposition. `hooks/tests/test_s2_locked_write_guard.py` scans the tree for
# spine-writing functions and FAILS on any that is not listed here — a new writer
# must be registered, with a disposition, before it can ship. Dispositions:
#   "guarded"      — writes through `_write_spine_guarded` (the predicate decides).
#   "inventoried"  — writes a spine but outside this module's write path; the
#                    reason it cannot reach locked ground is stated, and where a
#                    test proves it the test is named.
#   "exempt"       — deliberately outside the guard; the reason and the tracked
#                    obligation are stated.
SPINE_WRITE_INVENTORY = {
    ("pre_plan_gates", "append_metrics"): "guarded",
    ("pre_plan_gates", "annotate_session"): "guarded",
    ("pre_plan_gates", "write_phase_marker"): "guarded",
    ("pre_plan_gates", "link_plan_to_thought"): "guarded",
    ("pre_plan_gates", "main"): (
        "guarded — the `write-next-session-prompt` verb (the only spine write in "
        "main) routes through _write_spine_guarded"),
    ("taskmanagement", "_write_slice_row_unlocked"): (
        "inventoried — write_slice_row rewrites one `<!-- L:slice … -->` row under "
        "`## Slice Register`, never inside `# Discovery`; "
        "test_s2_locked_write_guard.py::AllowCases proves the hash is unchanged"),
    ("work_done", "_write_retire_marker_unlocked"): (
        "inventoried — rewrites the `**Status:**` line above the first H1; "
        "test_s2_locked_write_guard.py::AllowCases proves the hash is unchanged"),
    ("bookkeeping_migrate", "*"): (
        "inventoried — the migration backfill adds `Parent:` lines and spine "
        "wikilinks outside `# Discovery`; test_s2_locked_write_guard.py::AllowCases "
        "proves the hash is unchanged on a locked fixture"),
    ("_factcheck_engine", "_insert_audit_wikilink"): (
        "inventoried — appends an audit wikilink to a /double-check SOURCE file; a "
        "spine is a possible but unusual source, and an EOF append on a Discovery-"
        "to-EOF spine would change the last locked body. Outside S2's targets; "
        "recorded as a follow-up, not guarded"),
    ("clarify.translating_repository", "*"): (
        "exempt — the /clarification-v2 door writes the spine from Personal/clarify "
        "and never reaches these helpers; no permission signal exists for it. "
        "Tracked obligation on the Projects-side TODO.md (S2)"),
}


def append_metrics(session_id, payload):
    """`/close` writer: put this session's metrics rows under today's
    `### YYYY-MM-DD session [sid:…]` block in the spine's `## Sessions`.

    S1 (streamed-dancing-goose) — what changed and why it is shaped this way:

      * The rows live in `## Sessions`, never in `## Metrics`. `## Metrics` is a
        LOCKED Discovery field (`DISCOVERY_LOCKED_FIELDS`); writing there moved
        the Step-9 hash on every `/close`.
      * Every row carries `_CLOSE_METRICS_MARKER`, and a re-run replaces ONLY the
        lines carrying it. The block's other lines — the `/work-done` bullet
        `annotate_session` wrote under the same header, or a manual entry — are
        never touched. The previous form replaced the whole span from the header
        to the next heading, which is how five `/work-done` records were lost.
      * The section is created, when absent, by `ensure_sessions_section` — the
        same path `annotate_session` uses — so the two writers cannot disagree on
        where it goes.
      * The read-modify-write runs under `bookkeeping_lock`, like its sibling.

    Idempotency is on (today's date, sid_prefix) — the header. Returns a receipt
    dict. Raises ValueError on no active topic / no spine, and lets
    `BookkeepingLockTimeout` propagate (the CLI verb reports it).
    """
    p = _resolve_thought_path(session_id)

    # Local date — matches diary/Stats convention (`/close` uses local time too).
    today_iso = datetime.now().date().isoformat()
    header = _session_block_header(today_iso, session_id)
    rows = [f"{row} {_CLOSE_METRICS_MARKER}" for row in _format_metrics_rows(payload)]

    from bookkeeping_lock import bookkeeping_lock

    with bookkeeping_lock(p):
        content = p.read_text(encoding="utf-8")
        lines = content.split("\n")
        lines, sessions_idx, section_created = ensure_sessions_section(lines)
        _, section_end = find_sessions_section(lines)

        block = _session_block_bounds(lines, sessions_idx, section_end, header)
        if block is None:
            insert_at = section_end
            while insert_at > sessions_idx + 1 and lines[insert_at - 1].strip() == "":
                insert_at -= 1
            lines = lines[:insert_at] + ["", header, *rows, ""] + lines[insert_at:]
            status = "metrics_appended"
        else:
            header_idx, block_end = block
            mine = [j for j in range(header_idx + 1, block_end)
                    if _CLOSE_METRICS_MARKER in lines[j]]
            if mine:
                # Replace exactly my own lines: drop every line carrying my marker,
                # and put the new rows where the first of them stood.
                first = mine[0]
                keep = [lines[j] for j in range(header_idx + 1, block_end)
                        if _CLOSE_METRICS_MARKER not in lines[j]]
                offset = sum(1 for j in range(header_idx + 1, first)
                             if _CLOSE_METRICS_MARKER not in lines[j])
                body = keep[:offset] + rows + keep[offset:]
                lines = lines[:header_idx + 1] + body + lines[block_end:]
                status = "metrics_replaced"
            else:
                insert_at = block_end
                while insert_at > header_idx + 1 and lines[insert_at - 1].strip() == "":
                    insert_at -= 1
                lines = lines[:insert_at] + rows + lines[insert_at:]
                status = "metrics_appended"

        _write_spine_guarded(p, content, "\n".join(lines),
                             session_id=session_id, writer="append_metrics")
    _record_ledger_write(p)              # A5 — metrics rows appended to the spine

    return {
        "status": status,
        "thought_file": str(p),
        "block_header": header,
        "writer": SESSION_WRITER_CLOSE,
        "section_created": section_created,
    }


def _resolve_thought_path(session_id):
    """Resolve session_id → absolute thought-file Path.

    The ONE spine resolver for the session-block writers: `annotate_session`,
    `append_metrics` (since S1 — it used to carry its own inline copy, which
    this docstring described as "mirrored") and `write_phase_marker`.
    Raises ValueError if no active topic, no thought_file_path on state, or
    the path can't be located on disk.
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        raise ValueError(
            f"No active topic for session {session_id}. "
            "Use 'set-active' or 'create-topic' first."
        )
    thought_path = state.get("thought_file_path")
    if not thought_path:
        raise ValueError(
            f"No thought_file_path on topic {proj}__{topic}. "
            "Use 'set-thought-file SESSION_ID PATH' to backfill."
        )
    p = Path(thought_path)
    if not p.is_absolute():
        # A5 (project-tracking-staleness S4): resolve MAIN-PINNED first via the
        # shared bookkeeping_resolver so the write lands on the `main`-owned
        # `Thoughts/` copy even when cwd is a sparse-excluded worktree — this is
        # what makes the write side agree with the S4 read side (which resolves the
        # same way). Backward-compatible: outside a worktree main_checkout ==
        # `git rev-parse --show-toplevel`, so this yields the same path as before.
        # Fall back to the legacy CWD-relative / project-root resolution only if the
        # main-pinned candidate does not resolve/exist.
        resolved = None
        try:
            import bookkeeping_resolver as _bkr
            cand = _bkr.resolve(thought_path, cwd=state.get("project_root"))
            if cand is not None and Path(cand).exists():
                resolved = Path(cand)
        except Exception:
            resolved = None
        if resolved is not None:
            p = resolved
        else:
            candidate = Path.cwd() / thought_path
            if candidate.exists():
                p = candidate
            else:
                proj_root = _resolve_project_root()
                if proj_root and (proj_root / thought_path).exists():
                    p = proj_root / thought_path
    if not p.exists():
        raise ValueError(f"Thought file not found: {thought_path}")
    return p


def annotate_session(session_id, bullet, writer=SESSION_WRITER_WORK_DONE):
    """Slice J-1: append (or replace) a single bullet under today's session block in `## Sessions`.

    Schema: 5–200 chars after strip, single-line (no embedded newlines), ends with
    `.`. The bullet is tagged with `<!-- annotate-session writer=<writer> -->` so
    subsequent re-runs BY THE SAME WRITER replace it under the same
    `### YYYY-MM-DD session [sid:<prefix>]` heading instead of duplicating.
    Lines carrying another writer's tag, `/close`'s metrics rows, and untagged
    (manual) bullets are left untouched — a writer may replace only its own line.

    `writer` is the identity of the SKILL invoking this (S1): `/work-done` is the
    default and the only identity the omission gate treats as a ship record;
    `/close --annotate` passes `writer="close"`. A legacy bare tag on disk reads
    as `work-done` (see `annotate_marker_writer`).

    A missing `## Sessions` section is created by `ensure_sessions_section` —
    the one path both writers share — rather than refused.

    Idempotency is on (today's date, sid_prefix, writer).

    Raises ValueError on schema failure, an unknown writer, no active topic,
    missing `thought_file_path`, or a missing thought file. Lets
    `BookkeepingLockTimeout` propagate (the CLI verb reports it).
    """
    if not isinstance(bullet, str):
        raise ValueError("Bullet must be a string.")
    if "\n" in bullet or "\r" in bullet:
        raise ValueError("Bullet must be single-line (no embedded newlines).")
    text = bullet.strip()
    n = len(text)
    if n < 5 or n > 200:
        raise ValueError(
            f"Bullet length out of range (got {n}; required 5–200 chars)."
        )
    if not text.endswith("."):
        raise ValueError("Bullet must end with a period.")
    marker = annotate_marker(writer)          # validates `writer`

    p = _resolve_thought_path(session_id)

    today_iso = datetime.now().date().isoformat()
    header = _session_block_header(today_iso, session_id)
    bullet_line = f"- {text} {marker}"

    # A5 (project-tracking-staleness S4): serialize the read-modify-write under the
    # repo-wide bookkeeping lock (STABILIZED LOCK PRINCIPLE — the two writers of the
    # `main`-owned spine, bookkeeping commits and topic lands, share one lock). The
    # lock is re-entrant per-process, so a caller (e.g. /work-done) that already
    # holds it does not self-deadlock; it may raise a bounded BookkeepingLockTimeout.
    from bookkeeping_lock import bookkeeping_lock

    with bookkeeping_lock(p):
        content = p.read_text(encoding="utf-8")
        lines = content.split("\n")
        lines, sessions_idx, section_created = ensure_sessions_section(lines)
        _, section_end = find_sessions_section(lines)

        block = _session_block_bounds(lines, sessions_idx, section_end, header)
        if block is None:
            # Append heading + bullet at end of `## Sessions` section, trimming
            # blank lines before the next heading so we don't accumulate them.
            insert_at = section_end
            while insert_at > sessions_idx + 1 and lines[insert_at - 1].strip() == "":
                insert_at -= 1
            new_block = ["", header, bullet_line, ""]
            lines = lines[:insert_at] + new_block + lines[insert_at:]
            status = "heading_and_bullet_appended"
        else:
            header_idx, block_end = block
            marker_idx = None
            for j in range(header_idx + 1, block_end):
                if annotate_marker_writer(lines[j]) == writer:
                    marker_idx = j
                    break
            if marker_idx is not None:
                lines[marker_idx] = bullet_line
                status = "bullet_replaced"
            else:
                insert_at = block_end
                while insert_at > header_idx + 1 and lines[insert_at - 1].strip() == "":
                    insert_at -= 1
                lines = lines[:insert_at] + [bullet_line] + lines[insert_at:]
                status = "bullet_appended"

        _write_spine_guarded(p, content, "\n".join(lines),
                             session_id=session_id, writer="annotate_session")

    return {
        "status": status,
        "thought_file": str(p),
        "block_header": header,
        "writer": writer,
        "section_created": section_created,
    }


# ---------------------------------------------------------------------------
# S6 (project-tracking-staleness) — phase-boundary marker writer (M6 write-side)
# ---------------------------------------------------------------------------
#
# One  <!-- L:phase phase=<phase> event=<start|stop> at=<date> session=<sid8>
# updated=<date> -->  row per phase name, replaced in place under a dedicated
# auto-created `## Phase Register` section on the MAIN-PINNED spine. Distinct
# `L:phase` token from the `L:slice` family (SLICE_ROW_RE in taskmanagement.py +
# the inline L:slice regex here) so the two never collide. Mirrors the shipped
# S4 A5 `annotate_session` template (main-pinned `_resolve_thought_path` +
# re-entrant `bookkeeping_lock`), NOT the non-main-pinned `sync_phase_transition`.

_PHASE_REGISTER_HEADING = "## Phase Register"
_PHASE_ROW_RE = re.compile(r"^<!--\s*L:phase\s+phase=(\S+)\s+.*-->\s*$")


def _render_phase_row(phase, event, session_id):
    """The single-line `<!-- L:phase ... -->` row for `phase`/`event`."""
    today = datetime.now().date().isoformat()
    sid_short = (session_id or "")[:8]
    return (f"<!-- L:phase phase={phase} event={event} "
            f"at={today} session={sid_short} updated={today} -->")


def write_phase_marker(session_id, event, phase):
    """S6 — reflect a phase boundary on the topic's spine as a durable, machine-
    readable `<!-- L:phase -->` marker, then synchronously verify it landed.

    Coupling (mirrors S4's A5): the spine is resolved MAIN-PINNED via
    `_resolve_thought_path` (so the write lands on the `main`-owned `Thoughts/`
    copy even inside a sparse-excluded worktree), the read-modify-write is
    serialized under the re-entrant `bookkeeping_lock`, and the row is confirmed
    by a synchronous `verify_write` re-read (E3/AD-8 — fail-loud, no "succeeded
    silently" state; raises `WriteVerificationError` on mismatch).

    Spine-bound-only (C4 non-regression): a topic with no `thought_file_path`
    (plain mode) is skipped cleanly — no write, no raise. Byte-identical outside
    a worktree (`bookkeeping_resolver.resolve` == `git rev-parse --show-toplevel`).

    Absent-phase guard (S1): a topic with no recorded `phase` (a legitimate
    state — both `create_topic` and `_auto_register_state` mint `phase: None`)
    is also skipped cleanly — no write, no raise. Nothing is written and
    nothing is escaped; an absent phase must never be handed downstream as a
    value to render or regex-escape.

    `event` is "start" or "stop"; `phase` keys the row (replaced in place across
    events). Returns a receipt dict; the marker write NEVER rolls back the
    caller's already-durable phase transition (it is the caller's last step).
    """
    proj, topic, state = _resolve_topic(session_id)
    if state is None:
        return {"status": "no_active_topic"}
    if not state.get("thought_file_path"):
        return {"status": "skipped_no_spine"}
    if phase is None:
        return {"status": "skipped_no_phase"}

    p = _resolve_thought_path(session_id)
    row = _render_phase_row(phase, event, session_id)

    sys.path.insert(0, str(Path(__file__).parent))
    from bookkeeping_lock import bookkeeping_lock

    with bookkeeping_lock(p):
        content = p.read_text(encoding="utf-8")
        lines = content.split("\n")

        reg_idx = None
        for i, line in enumerate(lines):
            if line.rstrip() == _PHASE_REGISTER_HEADING:
                reg_idx = i
                break

        if reg_idx is None:
            # Auto-create the section. Anchor just before `## Sessions` (or
            # `## Slice Register`) when present — a stable spot on registered
            # spines that keeps the trailing `## Next Session Prompt` / handoff
            # marker undisturbed; else append at EOF. Both headings are located
            # by the shared regexes (S1): this site used to compare exact strings,
            # which disagreed with `_SESSIONS_HEADING_RE` on the numbered form.
            anchor = None
            for i, line in enumerate(lines):
                if _SESSIONS_HEADING_RE.match(line) or _SLICE_REGISTER_HEADING_RE.match(line):
                    anchor = i
                    break
            block = [_PHASE_REGISTER_HEADING, "", row, ""]
            if anchor is not None:
                lines = lines[:anchor] + block + lines[anchor:]
            else:
                # No anchor: append at the tail, OUTSIDE `# Discovery` (S2 — the
                # shared tail rule; a bare EOF append on a Discovery-to-EOF spine
                # landed inside the last locked body).
                lines, _ = _append_section_outside_discovery(lines, block)
            status = "section_created"
        else:
            section_end = len(lines)
            # Known limit (does not self-heal already-damaged spines): narrowing
            # this scan to stop at an H1 (`_SECTION_END_RE`) means a phase row an
            # earlier, buggier run already mislanded PAST an H1 is no longer found
            # here and so is never replaced — the stray row is left exactly where
            # it is, and a fresh, correct row is written above the H1 instead.
            # Repairing spines already damaged that way, migrating already-written
            # session blocks inside locked sections, and building a corpus scanner
            # are explicitly out of scope (plan Guiding Policy, streamed-dancing-goose).
            for j in range(reg_idx + 1, len(lines)):
                if _SECTION_END_RE.match(lines[j]):
                    section_end = j
                    break
            row_idx = None
            for j in range(reg_idx + 1, section_end):
                m = _PHASE_ROW_RE.match(lines[j].strip())
                if m and m.group(1) == phase:
                    row_idx = j
                    break
            if row_idx is not None:
                lines[row_idx] = row
                status = "row_replaced"
            else:
                insert_at = section_end
                while insert_at > reg_idx + 1 and lines[insert_at - 1].strip() == "":
                    insert_at -= 1
                lines = lines[:insert_at] + [row] + lines[insert_at:]
                status = "row_appended"

        _write_spine_guarded(p, content, "\n".join(lines),
                             session_id=session_id, writer="write_phase_marker")

    # E3 / AD-8 — synchronous fail-loud verify against the MAIN-PINNED copy.
    # Robust line-level check (todo_line kind): the intended row for this phase
    # must be present verbatim. Avoids the `spine_section` last-section
    # end-marker fragility (a `## Phase Register` that is the final section has
    # no following `## ` end marker).
    from taskmanagement import verify_write
    locator = r"^<!--\s*L:phase\s+phase=" + re.escape(phase) + r"\s+.*-->\s*$"
    verify_write("todo_line", p, row, locator)

    return {"status": status, "thought_file": str(p), "row": row,
            "phase": phase, "event": event}


# ---------------------------------------------------------------------------
# Session-level public API
# ---------------------------------------------------------------------------

def advance(session_id, gate, output):
    """Validate and advance to the next gate. Returns status dict."""
    state = read_state(session_id) or {
        "session_id": session_id,
        "gates": {},
        "bypass": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    completed = list(state["gates"].keys())
    validate_sequence(completed, gate)
    validate_schema(gate, output)

    state["gates"][gate] = {
        "status": "complete",
        "output": output,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    write_state(session_id, state)

    # Stamp clarification_phase_session_id on topic state when gate4 completes.
    # Phase-internal signal: records that Clarification's gate4 step completed.
    # Cross-phase Clarification→Planning transition is owned solely by phase-start
    # (Slice A). For phase=None legacy topics, behavior is byte-identical (additive).
    if gate == "gate4_success_metrics":
        try:
            p, t, ts = _resolve_topic(session_id)
            if ts is not None:
                ts["clarification_phase_session_id"] = session_id
                ts["updated"] = datetime.now(timezone.utc).isoformat()
                _write_topic_state(topic_slug=p, project_slug=t, state=ts)
        except Exception:
            pass  # Best-effort; never block gate advancement

    nxt = next_gate(list(state["gates"].keys()))
    return {"status": "advanced", "completed": gate, "next_gate": nxt}


def bypass(session_id, reason):
    """Write session-level bypass marker (bypasses all gates for this session)."""
    state = read_state(session_id) or {
        "session_id": session_id,
        "gates": {},
        "bypass": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    state["bypass"] = {
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    write_state(session_id, state)
    return {"status": "bypassed", "reason": reason}


def check_complete(session_id):
    """Return True if all gates complete or bypassed. None state = True (vanilla)."""
    state = read_state(session_id)
    if state is None:
        return True  # No state file = vanilla mode
    return is_complete(state)


# ---------------------------------------------------------------------------
# Slice I (S-I-Impl-1) — Phase 3 `/plan` foundation CLIs
#
# Four new CLI commands wire the `/plan` orchestrator (S-I-Impl-2 lands the
# orchestrator skill; this session lands the receiving surface):
#   plan-mode-init             — write session-scoped marker for EnterPlanMode
#   link-plan-to-thought       — idempotent wikilink append into spine
#   factcheck-plan-step        — per-transition coherency dispatch (0A/0B2/0C)
#   factcheck-plan-coherency   — final coherency dispatch (Gate 0g)
#
# Foundation note: production --rounds N>0 dispatches require the plan/SKILL.md
# orchestrator (Agent tool from main session). S-I-Impl-1 provides the CLI
# surface + --rounds 0 dry-run stubs.
# ---------------------------------------------------------------------------

PLAN_MODE_INIT_DIR = Path.home() / ".claude" / "state" / "plan_mode_init"
POST_PLAN_CHOICE_DIR = Path.home() / ".claude" / "state" / "post_plan_choice"
PLAN_VALIDATION_DIR = Path.home() / ".claude" / "state" / "plan_validation"
# Ack marker for the un-initiated-plan-exit guard (check-uninitiated-plan-exit.sh).
# Written by `plan-exit-ack` after the guard surfaces a silent exit and the user
# acknowledges; cleared by `plan-mode-init` so the guard re-arms per plan-mode episode.
PLAN_EXIT_ACK_DIR = Path.home() / ".claude" / "state" / "plan_exit_ack"
PLAN_VERIFICATIONS_PATH = PLAN_VALIDATION_DIR / "_verifications.jsonl"
VALID_PLAN_MODES = frozenset({"A", "B", "C"})
# 0D (Guiding Policy) + 0E (Coherent Actions) added by plan-validation-engine-consumer
# (S3): every kernel section can now be validated in isolation by the shared engine —
# the former blind spots 0b/0d/0e get a dedicated per-section checker (C5). 0b (Desired
# Outcome) is validated as part of the 0B2 Outcome-Claims↔Outcome coverage check.
VALID_PLAN_STEP_GATES = frozenset({"0A", "0B2", "0C", "0D", "0E"})
VALID_PLAN_VERDICTS = frozenset({"PASS", "DIRTY", "ESCALATE"})
# `_IMPL_DETAILS_HEADER` is defined once, beside the `## Sessions` locators (S1) —
# both `link_plan_to_thought` and `ensure_sessions_section` open that H1.


def plan_mode_init(session_id, mode=None, thought_path=None):
    """Write a session-scoped marker describing the upcoming plan-mode entry.

    Consumed by `check-thought-bound.sh` (S-I-Impl-2) on EnterPlanMode.
    Mode A/B require `thought_path`; Mode C accepts thought_path=None.
    `discovery_src_hash` is computed from `_discovery_locked_fields_hash` when
    thought_path points at a file with a parseable `# Discovery` section.
    """
    if mode is not None and mode not in VALID_PLAN_MODES:
        raise ValueError(
            f"mode must be one of {sorted(VALID_PLAN_MODES)} (got {mode!r})."
        )
    if mode in {"A", "B"} and not thought_path:
        raise ValueError(f"Mode {mode} requires --thought-path.")

    discovery_src_hash = None
    if thought_path:
        tp = Path(thought_path)
        if tp.is_file():
            discovery_src_hash = _discovery_locked_fields_hash(
                tp.read_text(encoding="utf-8")
            )

    PLAN_MODE_INIT_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = PLAN_MODE_INIT_DIR / f"{session_id}.json"
    payload = {
        "session_id": session_id,
        "mode": mode,
        "thought_path": str(thought_path) if thought_path else None,
        "discovery_src_hash": discovery_src_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Computed BEFORE the marker is written, so the marker on disk carries it.
    # The restored code computed it after `_write_json` and rebound `payload`
    # for the RETURN value only, leaving the file permanently without the key —
    # a dead write. Nothing reads it from the file today, so this is additive;
    # it makes the marker match what the verb reports.
    consumption = _record_clarify_chain_consumption(session_id, thought_path)
    if consumption is not None:
        payload = {**payload, "clarify_chain_consumption": consumption}
    _write_json(marker_path, payload)
    # Re-arm the un-initiated-plan-exit guard for this fresh plan-mode episode: clear
    # BOTH the stale ack marker AND the prior episode's post_plan_choice grant marker,
    # so a NEW silent exit in this episode is caught again (neither a stale ack nor a
    # stale grant from a previous episode can mask it — one-shot per episode).
    for stale in (
        PLAN_EXIT_ACK_DIR / f"{session_id}.marker",
        POST_PLAN_CHOICE_DIR / f"{session_id}.marker",
    ):
        if stale.exists():
            stale.unlink()
    return {"status": "wrote", "marker_path": str(marker_path), **payload}


def _record_clarify_chain_consumption(session_id, thought_path):
    """`/plan`-side half of the clarification-v2 chain-acceptance metric
    (clarification-v2-cutover, Slice S10).

    This is the EXISTING seam the plan names: `plan-mode-init` is already
    invoked when `/plan` consumes a spine and already computes the spine's
    current locked-fields hash, so the consumption record hangs here rather
    than on a new call. Because this seam runs AT consumption (not after a
    downstream file appears), it records a genuine FAIL as readily as a PASS —
    the `/solution-design` side's blind spot is not shared here.

    Records NOTHING for anything that is not a clarify-v2 spine: a v1
    `/clarification` framing, a Mode-C plan with no thought path, a missing
    file. The v2 discriminator is the engine's own `clarify:meta` chrome, NOT
    the `handoff-src-hash` marker — v1 spines carry that marker too, and
    filtering on it would sweep v1 framings into a v2 metric.

    Fail-soft for every ORDINARY fault: an unloadable module, a syntax error, an
    unwritable ledger all degrade to `None` and never abort `plan_mode_init`. A
    measurement side-port must not be able to block the flow it observes
    (`code_first_architecture.md` — observability only).

    NOT total, and the earlier wording ("any other fault") overclaimed it. This
    catches `Exception`, so a module that raises `SystemExit` or
    `KeyboardInterrupt` at import still propagates. That is deliberate:
    swallowing `BaseException` would make Ctrl-C un-interruptible inside a hook,
    which is a worse failure than the one being guarded. The claim is narrowed
    rather than the guard widened. Found by an independent bug hunt.

    RESTORED 2026-09-13. This helper and its three call-site lines above were
    silently dropped from this file between 2026-08-16T23:37 and
    2026-08-18T23:25 (present in `pre_plan_gates.py.bak-20260816233721`, absent
    in `.bak-20260818232501`); no intent for the removal was found in any
    source, and no gate noticed, so the metric read as working while recording
    nothing on the `/plan` side for roughly four weeks. The `/solution-design`
    half — the PostToolUse `clarify-design-consumption.sh` hook — kept running
    throughout, which is exactly what made the gap invisible: the ledger had
    rows, just never these ones. Restored VERBATIM from that backup rather than
    re-derived, per the owning TODO's guiding policy; `clarify_chain_metrics.py`
    still defined both recorder functions with no caller, so this is a
    reconnection rather than a rebuild."""
    if not thought_path:
        return None
    try:
        module_path = Path(__file__).resolve().parent / "clarify_chain_metrics.py"
        if not module_path.is_file():
            return None
        spec = importlib.util.spec_from_file_location(
            "_clarify_chain_metrics", str(module_path))
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        # Registered before `exec_module` (a module that imports itself needs to
        # find itself), but REMOVED AGAIN if the exec fails — otherwise a
        # half-initialised corpse stayed in `sys.modules` under that name and
        # the next importer got it instead of a fresh load. Found by an
        # independent bug hunt.
        sys.modules["_clarify_chain_metrics"] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop("_clarify_chain_metrics", None)
            raise
        # Hand it OUR `_discovery_locked_fields_hash` directly: the module's own
        # loader would re-exec this whole file, and the hash must in any case be
        # the one this module computes (never a second implementation).
        return module.record_plan_consumption(
            str(thought_path), session_id=session_id,
            hash_fn=_discovery_locked_fields_hash)
    except Exception:
        return None


def plan_exit_ack(session_id):
    """Record the user's acknowledgment of a surfaced un-initiated plan-mode exit.

    Written by the AI AFTER `check-uninitiated-plan-exit.sh` has blocked a tool and
    surfaced the anomalous silent exit to the user. Its presence suppresses further
    blocks by that guard until the next `plan-mode-init` re-arms (clears) it — so the
    guard fires exactly once per plan-mode episode rather than blocking forever.
    """
    PLAN_EXIT_ACK_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = PLAN_EXIT_ACK_DIR / f"{session_id}.marker"
    payload = {
        "session_id": session_id,
        "acknowledged_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(marker_path, payload)
    return {"status": "acked", "marker_path": str(marker_path), **payload}


def _slug_from_plan(content):
    """Derive a kebab-case bookkeeping slug from a plan's own problem statement.

    Used for Mode-C relocation when no `--dest-slug` is supplied (decided 2026-07-07):
    Mode-C plans have no upstream `/clarification` slug, so generate one from the plan's
    concise problem statement — its first Markdown H1 title, else the first non-empty body
    line (skipping YAML frontmatter). Returns "" if nothing usable is found.
    """
    title = None
    in_frontmatter = False
    for i, raw in enumerate(content.splitlines()):
        line = raw.strip()
        if i == 0 and line == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line == "---":
                in_frontmatter = False
            continue
        if line.startswith("# "):
            title = line[2:].strip()
            break
        if title is None and line:
            title = line  # fallback: first non-empty body line
    if not title:
        return ""
    # Drop a leading "Plan —"/"Plan:"-style prefix for a cleaner slug.
    title = re.sub(r"(?i)^plan\s*[—:-]\s*", "", title)
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    if len(slug) > 60:
        slug = slug[:60].rstrip("-")
    return slug


def relocate_plan_after_approval(session_id, plan_file_path, dest_slug=None):
    """Post-approval relocation of the harness-path plan into its durable Thoughts home.

    Runs OUTSIDE plan mode, AFTER a legitimate ExitPlanMode approval. Copies
    `~/.claude/plans/<harness-slug>.md` → `<project_root>/Thoughts/<dest-slug>-<ts>_PLAN.md`.

    Guard: a standalone CLI cannot read `permission_mode`, so the checkable proxy for
    "the plan was approved and we are past plan mode" is the presence of the
    `post_plan_choice/<sid>.marker` (written by post-plan-uxgate.sh on the ExitPlanMode
    grant, and NOT deleted by clear-post-plan-choice — it persists with pending=false).
    Absent that marker, no legitimate ExitPlanMode grant was recorded → refuse.

    Destination slug:
      - Modes A/B: topic_state.topic_slug (+ project_root) from the topic state.
      - Mode C:    the caller-supplied --dest-slug (skill/user-chosen); required.
    """
    src = Path(plan_file_path)
    if not src.is_file():
        raise ValueError(f"plan_file_path does not exist: {plan_file_path}")

    grant_marker = POST_PLAN_CHOICE_DIR / f"{session_id}.marker"
    if not grant_marker.exists():
        raise ValueError(
            "Refusing to relocate: no ExitPlanMode grant recorded for session "
            f"{session_id} (post_plan_choice marker absent). Relocation must run "
            "AFTER the plan is approved, outside plan mode."
        )

    content = src.read_text(encoding="utf-8")
    init_marker_path = PLAN_MODE_INIT_DIR / f"{session_id}.json"
    marker = _read_json(init_marker_path) or {}
    mode = marker.get("mode")

    if mode in {"A", "B"}:
        # Resolve via the canonical primitive every other command uses
        # (_resolve_topic): it follows _active.json's session pointer to the
        # per-topic <slug>__<proj>.json state file, which holds project_root and
        # topic_slug at top level. The old inline `state.get("topic_state", ...)`
        # read the flat, session-keyed _active.json for a key that never exists,
        # so Mode A/B relocation always failed.
        topic_slug, _project_slug, state = _resolve_topic(session_id)
        project_root = (state or {}).get("project_root")
        if not topic_slug or not project_root:
            raise ValueError(
                "Mode A/B relocation requires an active topic with project_root; "
                "none resolved for this session via _resolve_topic."
            )
        resolved_slug = topic_slug
        thoughts_dir = Path(project_root) / "Thoughts"
    else:
        # Mode C (or unbound): use the caller-supplied slug, else derive one from the
        # plan's own problem statement (decided 2026-07-07 — Mode-C plans have no upstream
        # /clarification slug, so generate from content rather than the harness filename).
        resolved_slug = dest_slug or _slug_from_plan(content)
        if not resolved_slug:
            raise ValueError(
                "Mode C relocation: no --dest-slug and could not derive a slug from the "
                "plan's problem statement (no H1 title / non-empty body line found)."
            )
        project_root = PROJECTS_ROOT
        thoughts_dir = Path(project_root) / "Thoughts"

    thoughts_dir.mkdir(parents=True, exist_ok=True)

    # Atomic create-or-bump on same-second timestamp collision (O_EXCL), per
    # bookkeeping-model §5 timestamp discipline.
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    dest = None
    for _ in range(5):
        candidate = thoughts_dir / f"{resolved_slug}-{ts}_PLAN.md"
        try:
            fd = os.open(str(candidate), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            ts = (
                datetime.strptime(ts, "%Y%m%d%H%M%S") + timedelta(seconds=1)
            ).strftime("%Y%m%d%H%M%S")
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        dest = candidate
        break
    if dest is None:
        raise ValueError("Could not allocate a non-colliding relocation timestamp.")

    # A3 — UNBIND the approved+relocated harness plan. The durable copy now exists
    # at `dest`, so the harness-path plan (`src`) must stop governing later spawns:
    # remove its manifest entry FIRST (so find-session-plan.sh no longer resolves it
    # even if the file lingers — the primary fix), THEN unlink the file. Order matters
    # for crash-safety: if we crash after the manifest remove but before unlink,
    # find-session-plan.sh's `[ ! -f ]` guard still skips a stale entry; the reverse
    # would briefly leave an untracked file (also benign). Best-effort: a relocation
    # that already succeeded must not fail on an unbind hiccup.
    unbind = {"manifest_removed": None, "src_unlinked": None}
    try:
        import _plan_manifest
        _plan_manifest.remove(session_id, str(src))
        unbind["manifest_removed"] = True
    except Exception as exc:  # non-fatal
        unbind["manifest_removed"] = f"failed: {exc}"
    try:
        src.unlink()
        unbind["src_unlinked"] = True
    except Exception as exc:  # non-fatal (e.g. already gone)
        unbind["src_unlinked"] = f"skipped: {exc}"

    # A5 / gap G4 — the relocated plan is a NEW tracked repo file this session
    # created, and nothing else records it: the relocation runs in code, so no
    # Write/Edit tool call ever fires for it.
    _record_ledger_write(dest)

    result = {
        "status": "relocated",
        "mode": mode,
        "src": str(src),
        "dest": str(dest),
        "dest_slug": resolved_slug,
        "unbind": unbind,
    }
    # Modes A/B: add the spine back-link now that the durable plan exists.
    if mode in {"A", "B"}:
        try:
            link = link_plan_to_thought(session_id, str(dest))
            result["spine_link"] = link.get("status")
        except Exception as exc:  # non-fatal: relocation already succeeded
            result["spine_link"] = f"link_failed: {exc}"
    return result


def link_plan_to_thought(session_id, plan_file_path):
    """Idempotent append of `- [[<plan-basename>]]` to the spine's
    `# Implementation Details` section.

    Mode A/B: appends the wikilink (creates the section if missing).
    Mode C: no-op (no spine to link into).
    """
    marker_path = PLAN_MODE_INIT_DIR / f"{session_id}.json"
    if not marker_path.exists():
        raise ValueError(
            f"No plan-mode-init marker for session {session_id}. Run "
            "`plan-mode-init` first."
        )
    marker = _read_json(marker_path) or {}
    mode = marker.get("mode")
    thought_path = marker.get("thought_path")
    if mode == "C" or not thought_path:
        return {"status": "no_op_mode_c"}

    tp = Path(thought_path)
    if not tp.is_file():
        raise ValueError(f"Thought file does not exist: {thought_path}")

    plan_basename = Path(plan_file_path).stem
    wikilink_line = f"- [[{plan_basename}]]"

    text = tp.read_text(encoding="utf-8")
    if wikilink_line in text:
        return {"status": "already_linked", "wikilink": wikilink_line}

    pattern = re.compile(r"^# Implementation Details\b.*?$", re.MULTILINE)
    m = pattern.search(text)
    if m:
        section_start = m.end()
        # Treat the next H1 OR H2 as the section boundary (Obsidian convention:
        # H2 inside H1 is a child subsection, so the new wikilink should land
        # before any sibling-or-larger header).
        next_section = re.search(r"^#{1,2} ", text[section_start:], re.MULTILINE)
        if next_section:
            insert_pos = section_start + next_section.start()
            new_text = (
                text[:insert_pos].rstrip("\n")
                + f"\n{wikilink_line}\n\n"
                + text[insert_pos:]
            )
        else:
            new_text = text.rstrip("\n") + f"\n{wikilink_line}\n"
    else:
        new_text = (
            text.rstrip("\n")
            + f"\n\n{_IMPL_DETAILS_HEADER}\n\n{wikilink_line}\n"
        )

    _write_spine_guarded(tp, text, new_text,
                         session_id=session_id, writer="link_plan_to_thought")
    return {
        "status": "linked",
        "wikilink": wikilink_line,
        "thought_path": str(tp),
    }


# ---------------------------------------------------------------------------
# Slice-I plan-section validation receipts (plan-validation-engine-consumer).
#
# Every per-transition (0A/0B2/0C/0D/0E) and whole-plan (0G) check writes a
# code-owned engine receipt (R<N>.md, factcheck-convergence.md §7 schema) keyed
# to (plan-file, gate). The verdict in that receipt is authored by the SHARED
# engine aggregator from the orchestrator's captured isolated-checker output — a
# producer-supplied verdict is never consulted (code_first_architecture.md: code
# owns the verdict; producer-never-verifies). check-plan-gates.sh reads these
# receipts (extending its existing Gate-3 R<N>.md reader) instead of trusting an
# author-typed `verdict:` line — closing the honor-system hole.
#
# The receipt dir is keyed on a stable hash of the RESOLVED plan-file path (NOT
# session/topic state), so the writer here and the shell reader resolve the
# identical location with zero shared mutable state. The shell reader never
# computes the hash itself — it calls the `plan-receipt-dir` CLI verb below.
# ---------------------------------------------------------------------------
PLAN_SECTION_RECEIPT_DIR = PLAN_VALIDATION_DIR / "sections"
VALID_PLAN_RECEIPT_GATES = frozenset({"0A", "0B2", "0C", "0D", "0E", "0G"})


def _plan_receipt_dir(plan_file_path, gate_id):
    """Canonical receipt directory for one (plan-file, gate).

    Single shared locus for the writer (factcheck_plan_step / _coherency) and the
    reader (check-plan-gates.sh via the `plan-receipt-dir` CLI verb). Keyed on a
    12-hex slice of the SHA-256 of the resolved plan path so both sides agree with
    no dependence on mutable session/topic state.
    """
    if gate_id not in VALID_PLAN_RECEIPT_GATES:
        raise ValueError(
            f"gate_id must be one of {sorted(VALID_PLAN_RECEIPT_GATES)} "
            f"(got {gate_id!r})."
        )
    resolved = str(Path(plan_file_path).resolve())
    h = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]
    return PLAN_SECTION_RECEIPT_DIR / h / gate_id


def _read_captured_checkers(checkers_json_arg):
    """Load the orchestrator-captured checker outputs for a plan-validation round.

    Source: a JSON file path in `checkers_json_arg`, or (when it is None or '-')
    read stdin. Accepted shapes: a JSON list of {"model": str, "verdict": str}
    (optional "checker" index filled if absent), or a {"checkers": [...]} wrapper.
    Returns a normalized list of {"checker": int, "model": str, "verdict": str}.

    Raises ValueError on empty/malformed input — there is deliberately NO fallback
    to a fabricated PASS: emitting a verdict with no checker evidence is exactly the
    honor-system hole this whole change closes.
    """
    if checkers_json_arg and checkers_json_arg != "-":
        raw = Path(checkers_json_arg).read_text(encoding="utf-8")
    else:
        raw = sys.stdin.read()
    raw = (raw or "").strip()
    if not raw:
        raise ValueError(
            "no captured checker outputs provided — plan validation cannot compute a "
            "verdict without the orchestrator's isolated-checker results (pass "
            "--checkers-json FILE or pipe the JSON list on stdin). Refusing to emit a "
            "verdict with no evidence."
        )
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"checker outputs are not valid JSON: {e}")
    if isinstance(data, dict) and "checkers" in data:
        data = data["checkers"]
    if not isinstance(data, list) or not data:
        raise ValueError(
            "checker outputs must be a non-empty JSON list of {model, verdict}."
        )
    norm = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "verdict" not in item:
            raise ValueError(f"checker #{i + 1} missing required 'verdict' field.")
        norm.append(
            {
                "checker": item.get("checker", i + 1),
                "model": item.get("model", "unknown"),
                "verdict": str(item["verdict"]),
            }
        )
    return norm


def _write_plan_receipt(gate_id, plan_file_path, checker_verdicts, round_num, max_rounds):
    """Compute the round verdict via the SHARED engine aggregator and write a
    code-owned R<N>.md receipt keyed to (plan-file, gate).

    Returns (agg_verdict, receipt_path). The verdict is authored by the engine from
    the captured checker output; no producer-supplied verdict is consulted (C4).
    """
    sys.path.insert(0, str(Path(__file__).parent))
    from _factcheck_engine import aggregate_round_verdict, _write_round_file

    is_final = round_num >= max_rounds
    agg_verdict, _prior = aggregate_round_verdict(
        checker_verdicts, is_final=is_final, kind="plan"
    )
    receipt_dir = _plan_receipt_dir(plan_file_path, gate_id)
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_file = receipt_dir / f"R{round_num}.md"
    _write_round_file(receipt_file, round_num, checker_verdicts, agg_verdict, "plan")
    return agg_verdict, receipt_file


def record_plan_override(gate_id, plan_file_path, reason, session_id=None):
    """Record an EXPLICIT operator 'proceed as is' override for one plan gate.

    Writes an OVERRIDE marker into the gate's receipt dir so check-plan-gates.sh can
    admit the section on a RECORDED override — never on an author-typed plan-text
    line (that is the honor-system hole this change closes). The reason is mandatory
    and non-empty; a bare override is refused. This is the deliberate, logged escape
    hatch from the fail-closed gate (Design Review residual bias #9).
    """
    if not reason or not reason.strip():
        raise ValueError("an operator override requires a non-empty --reason.")
    receipt_dir = _plan_receipt_dir(plan_file_path, gate_id)  # validates gate_id
    receipt_dir.mkdir(parents=True, exist_ok=True)
    marker = receipt_dir / "OVERRIDE"
    payload = {
        "gate_id": gate_id,
        "reason": reason.strip(),
        "session_id": session_id,
        "plan_file": str(Path(plan_file_path).resolve()),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(marker, payload)
    return {"status": "override_recorded", "marker": str(marker), **payload}


def factcheck_plan_step(
    gate_id, plan_file_path, checker_verdicts, round_num=1, max_rounds=3
):
    """Per-transition plan validation (0A/0B2/0C/0D/0E) — an engine CONSUMER.

    Consumes the shared engine to compute the round verdict from the orchestrator's
    captured isolated-checker outputs and writes a code-owned engine receipt. The
    producer never supplies the verdict — verdict authorship lives in code
    (code_first_architecture.md; C1/C4). Plan-kind max_rounds ceiling is 3
    (factcheck-convergence.md §4).
    """
    if gate_id not in VALID_PLAN_STEP_GATES:
        raise ValueError(
            f"gate_id must be one of {sorted(VALID_PLAN_STEP_GATES)} "
            f"(got {gate_id!r})."
        )
    if not Path(plan_file_path).is_file():
        raise ValueError(f"plan_file_path does not exist: {plan_file_path}")
    if not checker_verdicts:
        raise ValueError(
            "factcheck-plan-step requires captured checker outputs "
            "(--checkers-json FILE or stdin)."
        )
    agg_verdict, receipt_file = _write_plan_receipt(
        gate_id, plan_file_path, checker_verdicts, round_num, max_rounds
    )
    return {
        "status": "computed",
        "verdict": agg_verdict,
        "gate_id": gate_id,
        "round": round_num,
        "max_rounds": max_rounds,
        "checker_count": len(checker_verdicts),
        "checker_models": [v["model"] for v in checker_verdicts],
        "receipt": str(receipt_file),
        "plan_file_path": str(plan_file_path),
    }


def _append_plan_verification_row(row):
    """Append a single JSONL row to PLAN_VERIFICATIONS_PATH (idempotent dir)."""
    PLAN_VALIDATION_DIR.mkdir(parents=True, exist_ok=True)
    with open(PLAN_VERIFICATIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def factcheck_plan_coherency(
    session_id,
    plan_file_path,
    checker_verdicts,
    round_num=1,
    max_rounds=3,
    claims_checked=None,
):
    """Whole-plan / final coherency check (Gate 0G) — an engine CONSUMER.

    Consumes the shared engine to compute the round verdict from the orchestrator's
    captured isolated-checker outputs (3 Sonnet + 1 Opus per plan/SKILL.md Step 7),
    writes a code-owned 0G engine receipt, and appends an audit row to
    `~/.claude/state/plan_validation/_verifications.jsonl`. The producer never
    supplies the verdict; it is authored by the engine (C4). The 0G AND-gate (all
    per-axis PASS + cross-axis COHERENT) is enforced upstream by each checker folding
    its per-axis + cross-axis assessment into a single final `VERDICT:` token, which
    the plan-kind `_verdict_bucket` reads (factcheck-convergence.md §1 research/plan
    CoT-safe path). Plan-kind max_rounds ceiling is 3.
    """
    if not Path(plan_file_path).is_file():
        raise ValueError(f"plan_file_path does not exist: {plan_file_path}")
    if not checker_verdicts:
        raise ValueError(
            "factcheck-plan-coherency requires captured checker outputs "
            "(--checkers-json FILE or stdin)."
        )
    agg_verdict, receipt_file = _write_plan_receipt(
        "0G", plan_file_path, checker_verdicts, round_num, max_rounds
    )
    checker_models = [v["model"] for v in checker_verdicts]
    ts = datetime.now(timezone.utc).isoformat()
    _append_plan_verification_row(
        {
            "ts": ts,
            "session_id": session_id,
            "plan_file": str(plan_file_path),
            "verdict": agg_verdict,
            "rounds": round_num,
            "claims_checked": claims_checked,
            "checker_models": checker_models,
            "kind": "coherency",
            "receipt": str(receipt_file),
        }
    )
    return {
        "status": "computed",
        "verdict": agg_verdict,
        "gate_id": "0G",
        "round": round_num,
        "max_rounds": max_rounds,
        "session_id": session_id,
        "plan_file_path": str(plan_file_path),
        "checker_count": len(checker_verdicts),
        "checker_models": checker_models,
        "claims_checked": claims_checked,
        "receipt": str(receipt_file),
        "verifications_jsonl": str(PLAN_VERIFICATIONS_PATH),
    }


def clear_post_plan_choice(session_id):
    """Clear the post-plan UX-gate marker for a session (idempotent).

    Invoked by the AI after the user answers the AskUserQuestion (a)/(b)
    surfaced by plan/SKILL.md's F10 v5 contract. The marker pair is:
      - post-plan-uxgate.sh (PostToolUse on ExitPlanMode) sets
        `~/.claude/state/post_plan_choice/<SID>.marker` with pending=true
      - check-post-plan-pending.sh (PreToolUse `.*`) blocks every tool
        other than AskUserQuestion while pending=true
    This CLI flips pending=false (or removes the file).
    """
    POST_PLAN_CHOICE_DIR.mkdir(parents=True, exist_ok=True)
    marker_path = POST_PLAN_CHOICE_DIR / f"{session_id}.marker"
    if not marker_path.exists():
        return {"status": "no_marker", "session_id": session_id}
    payload = {
        "session_id": session_id,
        "pending": False,
        "cleared_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(marker_path, payload)
    return {"status": "cleared", "marker_path": str(marker_path), **payload}


def check_model_reasons(plan_path):
    """Validate that Opus actions in Coherent Actions table have non-empty Reason."""
    content = Path(plan_path).read_text(encoding="utf-8")
    action_rows = re.findall(
        r"^\|[^|]*\|[^|]*\|[^|]*\|[^|]*Opus[^|]*\|([^|]*)\|",
        content,
        re.MULTILINE,
    )
    empty = [i for i, reason in enumerate(action_rows) if reason.strip() in ("", "-")]
    if empty:
        print(
            f"✗ {len(empty)} Opus action(s) missing justification in Reason column.",
            file=sys.stderr,
        )
        print("Sonnet is default. Opus requires explicit reason.", file=sys.stderr)
        return False
    return True


# ---------------------------------------------------------------------------
#
# via _write_json). Concurrency lock via fcntl.flock on a sidecar .lock file
# (mirrors `_factcheck_engine.py:434, 883, 896`). Step-state machine is
# code-enforced; step bodies stub-return "step <name> not yet implemented"
# until later Slice K Implementation Sessions wire each step.
#
# explicitly NOT in the S1 surface area. Crash-resume already covers practical
# abandoning an iteration mid-flight is a manual state-JSON delete + lock
# release. If a real rewind use case surfaces post-shipping, it becomes a
# future slice.
#
# Per Slice K Plan A13 (Carry-forward #3): the `pre_plan_gates.py` →
# `phase_gates.py` module rename is explicitly DEFERRED to a future
# `pre_plan_gates.py`.
# ---------------------------------------------------------------------------


# Cadence enum → period_days. Locked at Discovery (K_DISCOVERY.md §Q-A
# resolution: 1w/2w/4w = tactical; 8w/Q/H/Y = strategy; user-defined ≤30d
# = tactical, >30d = strategy).


# Iterations dir — Projects/Iterations/. Resolved at import time from cwd
# so production runs from the Projects root land at the canonical location;
# tests monkey-patch this module attribute (same pattern as

# Locked artifact frontmatter schema (Slice K S3 — Plan A3). 10 fields.
# Order matters for writer output.

# Locked period grammar (Slice K S3 — Plan A3). 5 forms:
#   YYYY-WNN        single ISO week (NN ∈ 01..53)
#   YYYY-WNN-MM     ISO week range (01 ≤ NN < MM ≤ 53)
#   YYYY-QN         quarter (N ∈ 1..4)
#   YYYY-HN         half-year (N ∈ 1..2)
#   YYYY-Y          full year
_PERIOD_GRAMMAR_RE = re.compile(
    r"^(?:"
    r"(?P<wrange>(?P<wry>\d{4})-W(?P<wrn>\d{2})-(?P<wrm>\d{2}))"
    r"|"
    r"(?P<week>(?P<wy>\d{4})-W(?P<wn>\d{2}))"
    r"|"
    r"(?P<quarter>(?P<qy>\d{4})-Q(?P<qn>[1-4]))"
    r"|"
    r"(?P<half>(?P<hy>\d{4})-H(?P<hn>[1-2]))"
    r"|"
    r"(?P<year>(?P<yy>\d{4})-Y)"
    r")$"
)


def validate_period_grammar(period):
    """Return True if `period` matches one of the 5 locked period forms.

    Locked at Slice K S3 (Plan A3) as the single canonical period
    validator: every downstream consumer (writer, dashboard, lifecycle
    revamp) calls through this. Editorial extensions (new period forms)
    require a single-locus edit here (Cockburn Evolution Test).
    """
    if not isinstance(period, str):
        return False
    m = _PERIOD_GRAMMAR_RE.match(period)
    if not m:
        return False
    if m.group("wrange"):
        nn = int(m.group("wrn"))
        mm = int(m.group("wrm"))
        return 1 <= nn < mm <= 53
    if m.group("week"):
        wn = int(m.group("wn"))
        return 1 <= wn <= 53
    # quarter / half / year char classes already constrain enums in regex.
    return True






# ---------------------------------------------------------------------------
# Slice K S8a — Revamp / _retired flow + cadence math + auto-scheduled
# successor TODO (Plan A11).
#
# Pure cadence math (`next_run_date`) is grammar-form-preserving — given a
# period in the locked grammar and a cadence enum, it returns the next
# period in the SAME grammar form. Week-/quarter-/half-/year-boundary
# rollover is handled via ISO calendar arithmetic on Monday-anchored dates.
# Editorial extension for any new cadence enum is a single-locus edit here
# (Cockburn Evolution Test).
#
# atomically to `<id>_retired.md` (via os.rename), rewrites its frontmatter
# through the locked 10-field schema to `status: retired` + `superseded_by`,
# and writes a new artifact at the next-period filename with
# `status: current` + `supersedes: <id>_retired`. Concurrency guard is a
# dedicated fcntl lock on a sidecar `.revamp.lock` under
# lock so a revamp on a finalised iteration doesn't contend with active
# runs.
#
# `TODO.md` at finalize. Line format (locked):
# Idempotent on `(next-period, wikilink)` pair. On revamp, the helper swaps
# the wikilink from `[[<old_id>]]` to `[[<new_id>]]` in any TODO line that
# already names the retired iteration; the due-date stays unchanged because
# the iteration window has not shifted.
# ---------------------------------------------------------------------------

_PERIOD_WEEK_RE = re.compile(r"^(\d{4})-W(\d{2})$")
_PERIOD_WRANGE_RE = re.compile(r"^(\d{4})-W(\d{2})-(\d{2})$")
_PERIOD_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_PERIOD_HALF_RE = re.compile(r"^(\d{4})-H([1-2])$")
_PERIOD_YEAR_RE = re.compile(r"^(\d{4})-Y$")

# Cadence enum → expected period grammar form. Each cadence locks to one
# grammar form; mismatch raises ValueError.
_CADENCE_TO_GRAMMAR = {
    "1w": "week",
    "2w": "week",
    "4w": "week",
    "8w": "wrange",
    "Q": "quarter",
    "H": "half",
    "Y": "year",
}


def _iso_week_year(iso_year, iso_week, weeks_to_add):
    """Add `weeks_to_add` to ISO (year, week) and return the resulting
    (iso_year, iso_week). Uses Monday-anchored date arithmetic so week-53
    rollover follows ISO 8601."""
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    shifted = monday + timedelta(weeks=weeks_to_add)
    iy, iw, _ = shifted.isocalendar()
    return iy, iw


def next_run_date(period, cadence_enum):
    """Return the next-cycle period given the current period and cadence.

    Pure cadence math. Grammar-form-preserving: input `YYYY-WNN` returns
    `YYYY-WNN`; input `YYYY-Q3` returns `YYYY-Q4` (or `Y+1-Q1`); etc.
    Handles week-53 → next-year-W01, Q4 → next-year-Q1, H2 → next-year-H1,
    year+1 rollover via ISO calendar arithmetic.

    Raises ValueError on period/cadence mismatch (e.g. cadence='Q' with
    a week-form period) or unrecognised cadence.
    """
    if not validate_period_grammar(period):
        raise ValueError(
            f"period {period!r} does not match locked grammar; "
            "cannot compute next_run_date"
        )
    if cadence_enum not in _CADENCE_TO_GRAMMAR:
        raise ValueError(
            f"unknown cadence {cadence_enum!r}; allowed: "
            f"{sorted(_CADENCE_TO_GRAMMAR)}"
        )
    expected = _CADENCE_TO_GRAMMAR[cadence_enum]

    m_week = _PERIOD_WEEK_RE.match(period)
    m_wrange = _PERIOD_WRANGE_RE.match(period)
    m_quarter = _PERIOD_QUARTER_RE.match(period)
    m_half = _PERIOD_HALF_RE.match(period)
    m_year = _PERIOD_YEAR_RE.match(period)

    if expected == "week":
        if not m_week:
            raise ValueError(
                f"cadence {cadence_enum!r} requires YYYY-WNN period; "
                f"got {period!r}"
            )
        yy = int(m_week.group(1))
        nn = int(m_week.group(2))
        step = {"1w": 1, "2w": 2, "4w": 4}[cadence_enum]
        new_y, new_w = _iso_week_year(yy, nn, step)
        return f"{new_y}-W{new_w:02d}"

    if expected == "wrange":
        if not m_wrange:
            raise ValueError(
                f"cadence '8w' requires YYYY-WNN-MM period; got {period!r}"
            )
        yy = int(m_wrange.group(1))
        nn = int(m_wrange.group(2))
        new_start_y, new_start_w = _iso_week_year(yy, nn, 8)
        new_end_y, new_end_w = _iso_week_year(yy, nn, 15)
        if new_start_y != new_end_y:
            # Cross-year 8-week range — clamp end to week 53 of the
            # start year per Cockburn evolution-test single-locus
            # discipline. Downstream consumer (writer) re-validates
            # the result via validate_period_grammar.
            return f"{new_start_y}-W{new_start_w:02d}-53"
        return f"{new_start_y}-W{new_start_w:02d}-{new_end_w:02d}"

    if expected == "quarter":
        if not m_quarter:
            raise ValueError(
                f"cadence 'Q' requires YYYY-QN period; got {period!r}"
            )
        yy = int(m_quarter.group(1))
        qn = int(m_quarter.group(2))
        nq = qn + 1
        new_y = yy + (nq - 1) // 4
        new_q = ((nq - 1) % 4) + 1
        return f"{new_y}-Q{new_q}"

    if expected == "half":
        if not m_half:
            raise ValueError(
                f"cadence 'H' requires YYYY-HN period; got {period!r}"
            )
        yy = int(m_half.group(1))
        hn = int(m_half.group(2))
        nh = hn + 1
        new_y = yy + (nh - 1) // 2
        new_h = ((nh - 1) % 2) + 1
        return f"{new_y}-H{new_h}"

    if expected == "year":
        if not m_year:
            raise ValueError(
                f"cadence 'Y' requires YYYY-Y period; got {period!r}"
            )
        yy = int(m_year.group(1))
        return f"{yy + 1}-Y"

    raise ValueError(f"unhandled cadence/grammar pair: {cadence_enum}")












def _record_ledger_write(path) -> None:
    """Record a code-layer write to this session's file ledger (A5 / gap G4).

    `track-session-files.sh` sees only Write/Edit TOOL calls, so every TODO and
    plan-relocation write this module performs was invisible to the ledger — and
    therefore to any publish surface compiling its declared scope from it.

    Lazy, guarded, silent on failure. `record_write` never raises; this wrapper
    extends that to the import, because this module also runs under pytest and
    from background jobs where no session exists.
    """
    try:
        import sys as _sys
        _hooks = os.path.dirname(os.path.abspath(__file__))
        if _hooks not in _sys.path:
            _sys.path.insert(0, _hooks)
        from commit_scope import record_write as _rw
        _rw(path)
    except Exception:
        pass


def _read_iteration_artifact(path):
    """Parse the `---` fenced 10-field frontmatter from an iteration
    artifact at `path`. Returns `(frontmatter_dict, body_str)`. Strict
    against the locked schema — raises ValueError on a malformed file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError(
            f"Artifact {path} missing opening frontmatter fence"
        )
    end = text.find("\n---\n", 4)
    if end == -1:
        # Allow trailing fence without newline at EOF.
        end = text.find("\n---", 4)
        if end == -1 or text[end:] != "\n---":
            raise ValueError(
                f"Artifact {path} missing closing frontmatter fence"
            )
        body = ""
    else:
        body = text[end + len("\n---\n"):]
    fm_text = text[4:end]
    fm = {}
    for raw in fm_text.split("\n"):
        if not raw.strip():
            continue
        k, _, v = raw.partition(":")
        k = k.strip()
        v = v.strip()
        if v == "null":
            fm[k] = None
        elif v == "true":
            fm[k] = True
        elif v == "false":
            fm[k] = False
        else:
            try:
                fm[k] = int(v)
            except ValueError:
                fm[k] = v
    return fm, body






# Locked framework chains (Discovery §1.5.2 + §1.5.3). S1 stubs each step;
# bodies land in later Implementation Sessions (S4b tactical orchestrator,
# S5 strategy orchestrator).










# ---------------------------------------------------------------------------
# Slice K S4b — Tactical chain orchestrator (Plan A7).
#
# R3 N1 carve-out — code-enforced at the intake validator below:
#     diagnostic naming R3 N1.
#   - Mode-2 (`_iteration.md` / `_strategy.md`) candidates may carry
#     R/I/C/E per session (prior values surface as context, never silently
#     applied).
#
# Sub-skill dispatch is contract-driven — the orchestrator builds the
# Input contract documented in each adapter's SKILL.md and consumes the
# Output contract directly. The `_subskill_dispatch_fn=` parameter is the
# test-injection seam (mirrors the existing `_factcheck_fn=` pattern). In
# production runs without an injected callable, sub-skill steps surface a
# `SUBSKILL_DISPATCH_NEEDED` response carrying the input contract; the
# with `subskill_output=<dict>`.
#
# Cross-step discrepancy detection is the seam-only ship per Plan A7 —
# Session 9 (Plan A10) ships the user-facing resolution UX (3-option
# AskUserQuestion menu). For S4b, conflicts are appended to
# `state['pending_discrepancies'][]` as structured rows; the orchestrator
# advances past the producing step (the resolution UX gates a later
# discrepancy-pending check, not this seam).
#
# `/recommend` invocation seam — when the user signals a missing value at
# any step, the calling AI surfaces a 3-option AskUserQuestion block per
# the plan; the orchestrator carries the state flag
# `recommend_pending: {"item_id": ..., "missing_value": ...}` so a
# subsequent advance can resume from the chosen branch.
# ---------------------------------------------------------------------------

# Allowed candidate fields at intake (locked at Plan A7 + extended at
# Plan A8 to permit `context`).
# _THOUGHT.md mode: only the core fields are allowed; the prior_* fields
# are REJECTED (R3 N1). Mode-2 (_iteration.md/_strategy.md) accepts all
# core + prior_* fields. `context` is permitted in both modes: a plain-
# text candidate description carried for downstream judgment surfaces
# (Rumelt-kernel reads it in strategy mode; tactical adapters may
# ignore). `context` is NOT a pre-stored score or label — the R3 N1
# REJECTED on `_THOUGHT.md` intake.

# Sub-skill steps in the tactical chain. `challenge` + `factcheck` +
# `finalize` are orchestrator-internal (Python composes them) — not
# adapter-backed.

# Sub-skill steps in the strategy chain. `challenge_mid` + `challenge_end` +
# `factcheck` + `finalize` are orchestrator-internal. Strategy mode
# Carry-forward #5): strategy-mode Coherent Actions ARE the de facto
# "Musts" (Rumelt selects coherent actions, not a sortable
# intentionally a tactical concern (strategy mode DEFINES the strategy
# AGAINST WHICH the tactical balance is later measured).














# Map sub-skill step → SKILL.md slug used by the dispatch layer.
# `rumelt_kernel` is the FIRST step of the strategy chain (Plan A8);
# `toc` in strategy mode reuses the same SKILL slug as tactical mode (the
# adapter's Input contract is mode-agnostic — same `depends_on`-bearing
# candidate shape).








# ---------------------------------------------------------------------------
# Slice K S7 (Plan A10) — Input modes + scrape gate + Mode-2 multi-driver
# merge + 3-option discrepancy resolution menu.
#
# Mode 1 (Bootstrap): parse a .md file of wikilinks; resolve each to a
# `_thought` file on disk; hard-fail on broken/missing wikilinks. Mode 2
# (Continuation/Multi-driver): union by wikilink identity across N prior
# per R3 N1); retired `_thought` files dropped silently with a one-line
# log note (closes Plan A10 Carry-forward #1).
#
# Scrape gate: when --source is absent the user must type the literal
# string 'confirm' BEFORE any TODO-bucket scrape; any other value aborts.
#
# Discrepancy resolution menu: when prior advance(s) left rows in
# `state['pending_discrepancies']`, the NEXT advance refuses to proceed
# without a user pick — (a) override + reason, (b) drop item, (c) defer
# as TODO. The pick is logged to `state['discrepancies_resolved'][]` for
# the finalize step's body trailer.
# ---------------------------------------------------------------------------




























# ---------------------------------------------------------------------------
# Slice K S6 (Plan A9) — Six-window momentum dashboard
#
# mode (tactical / strategy), sorts each group newest-first by period, and
# computes per-mode aggregates over (defensibility_rate, over_commit_rate)
# for 6 windows (Last / Avg last 2 / 4 / 8 / 16 / Avg total). Insufficient-
# history windows surface as None (caller omits in textual render). Rendered
# BEFORE the cadence prompt by the calling skill so the team sees the
# momentum signal that motivates the new iteration. No writes.
# ---------------------------------------------------------------------------

_DASHBOARD_WINDOWS = (
    ("last_1", 1),
    ("avg_last_2", 2),
    ("avg_last_4", 4),
    ("avg_last_8", 8),
    ("avg_last_16", 16),
    ("avg_total", None),
)




def _period_sort_key(period):
    """Total order over the 5 locked period forms — used to sort artifacts
    newest-first in the dashboard."""
    if not isinstance(period, str):
        return (0, 0)
    m = _PERIOD_GRAMMAR_RE.match(period)
    if not m:
        return (0, 0)
    if m.group("wrange"):
        return (int(m.group("wry")), int(m.group("wrm")) * 7)
    if m.group("week"):
        return (int(m.group("wy")), int(m.group("wn")) * 7)
    if m.group("quarter"):
        return (int(m.group("qy")), int(m.group("qn")) * 91)
    if m.group("half"):
        return (int(m.group("hy")), int(m.group("hn")) * 182)
    # year
    return (int(m.group("yy")), 365)




def _window_aggregate(samples, n, key):
    """Aggregate a single metric over the first n (newest-first) samples.

    n=None aggregates over the full population. Returns None when there
    are fewer than n samples, or when the window has zero non-null values
    for the metric. Both metrics average per-artifact values:
      defensibility_rate: mean of [1.0 if defensibility else 0.0] over
                          non-null defensibility entries
      over_commit_rate:   mean of [incomplete / total] over entries where
                          both fields are non-null int and total > 0
    """
    if n is None:
        window = samples
        if not window:
            return None
    else:
        if len(samples) < n:
            return None
        window = samples[:n]
    vals = []
    if key == "defensibility_rate":
        for s in window:
            d = s.get("defensibility")
            if isinstance(d, bool):
                vals.append(1.0 if d else 0.0)
    elif key == "over_commit_rate":
        for s in window:
            total = s.get("selected_musts_total")
            inc = s.get("selected_musts_incomplete")
            if (
                isinstance(total, int) and not isinstance(total, bool)
                and isinstance(inc, int) and not isinstance(inc, bool)
                and total > 0
            ):
                vals.append(inc / total)
    else:
        return None
    if not vals:
        return None
    return sum(vals) / len(vals)




_DASHBOARD_ROW_LABELS = {
    "last_1": "Last time",
    "avg_last_2": "Avg last 2",
    "avg_last_4": "Avg last 4",
    "avg_last_8": "Avg last 8",
    "avg_last_16": "Avg last 16",
    "avg_total": "Avg total",
}


def _format_pct(v):
    if v is None:
        return "  N/A"
    return f"{v * 100:5.1f}%"






# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: pre_plan_gates.py <command> [args]", file=sys.stderr)
        sys.exit(2)

    cmd = sys.argv[1]

    # ------------------------------------------------------------------
    # Session-level commands
    # ------------------------------------------------------------------

    if cmd == "advance":
        session_id = sys.argv[2]
        gate = sys.argv[3]
        output = json.loads(sys.argv[4])
        try:
            result = advance(session_id, gate, output)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "check":
        session_id = sys.argv[2]
        state = read_state(session_id)
        if state is None:
            sys.exit(0)  # No state file = vanilla mode, allow
        if state.get("bypass"):
            sys.exit(0)  # Bypass accepted
        if is_complete(state):
            sys.exit(0)  # All gates complete
        # Incomplete — block
        completed = list(state.get("gates", {}).keys())
        missing = [g for g in GATE_SEQUENCE if g not in completed]
        print(
            f"✗ Pre-plan gates incomplete. Missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        print("Options: finish gates, call bypass, or reset.", file=sys.stderr)
        sys.exit(1)

    elif cmd == "bypass":
        session_id = sys.argv[2]
        reason = sys.argv[3] if len(sys.argv) > 3 else "no reason given"
        result = bypass(session_id, reason)
        print(json.dumps(result, indent=2))

    elif cmd == "reset":
        session_id = sys.argv[2]
        delete_state(session_id)
        print("Pre-plan state cleared.", file=sys.stderr)

    elif cmd == "read":
        session_id = sys.argv[2]
        state = read_state(session_id)
        # Topic-level info — surface independently of session-level state so
        # implementation sessions (no Gates 0-4 run) still see active topic.
        proj, topic, topic_state = _resolve_topic(session_id)
        # M13 auto-bind (S10): if the session is fully unbound, try to place it
        # on its named topic from the worktree name (the only deterministic
        # source available at the bare `read` seam — no prompt body here), then
        # re-resolve so the annotate_session / omission-Stop-hook read path finds
        # a bound topic. Refuse-rather-than-guess (E26): a no-op unless the
        # worktree name matches an existing on-disk topic-state.
        auto_bound = None
        if state is None and topic_state is None:
            _b = auto_bind_unbound_session(session_id)
            if _b.get("status") == "bound":
                auto_bound = _b
                state = read_state(session_id)
                proj, topic, topic_state = _resolve_topic(session_id)
        topic_block = None
        if topic_state:
            intake_source = topic_state.get("intake_source")
            phase = topic_state.get("phase")
            # Derived `lane` field — combines intake_source + phase into one
            # dispatch value. Mapping rule: lane = "plain" if intake_source ==
            # "plain" else phase. Existing phase-state-machine internals keep
            # reading raw phase; new lane-semantic consumers read lane.
            lane = "plain" if intake_source == "plain" else phase
            topic_block = {
                "topic_slug": proj,
                "project_slug": topic,
                "bypass_marker": topic_state.get("bypass_marker"),
                "project_root": topic_state.get("project_root"),
                "thought_file_path": topic_state.get("thought_file_path"),
                "intake_source": intake_source,
                "todo_line_ref": topic_state.get("todo_line_ref"),
                "lane": lane,
            }
        if state is None and topic_block is None:
            print(json.dumps({"status": "no_state_file"}))
        elif state is None:
            _out = {
                "status": "topic_only",
                "topic_state": topic_block,
            }
            if auto_bound is not None:
                _out["auto_bound"] = auto_bound
            print(json.dumps(_out, indent=2, default=str))
        else:
            if topic_block:
                state["topic_state"] = topic_block
            print(json.dumps(state, indent=2, default=str))

    elif cmd == "check-model-reasons":
        plan_path = sys.argv[2]
        if check_model_reasons(plan_path):
            sys.exit(0)
        else:
            sys.exit(1)

    # ------------------------------------------------------------------
    # Topic-level commands
    # ------------------------------------------------------------------

    elif cmd == "create-topic":
        # create-topic SESSION_ID PROJECT_SLUG [TOPIC_SLUG]
        #     [--intake-source plain|thought] [--todo-line-ref <path>:<line>]
        #     [--thought-file-path <spine>.md]
        # TOPIC_SLUG is optional ONLY when --thought-file-path is given; with
        # neither, create-topic REFUSES rather than falling back to the cwd.
        argv = list(sys.argv)
        # Strip the optional flags wherever they appear after the subcommand
        # (positional parsing below is preserved — the argv contract is
        # otherwise unchanged).
        #
        # A MALFORMED FLAG REFUSES; IT NEVER DECAYS INTO A POSITIONAL. The loop
        # used to guard each flag with `tok == "--x" and i + 1 < len(argv)`, so a
        # flag supplied with NO value failed the guard, fell through to the
        # `else`, survived the strip, and was then read as `argv[4]` — i.e. as an
        # explicit TOPIC_SLUG. Two reachable shapes, both silent:
        #
        #     create-topic SID PROJ --thought-file-path
        #         -> topic_slug == "--thought-file-path"
        #     create-topic SID PROJ --thought-file-path --intake-source plain
        #         -> thought_file_path == "--intake-source", topic_slug == "plain"
        #
        # Because an explicit slug OUTRANKS the artifact (A1), either shape walks
        # straight past the derive-or-refuse gate and mints a record named after a
        # token nobody meant as a name — the same damage class as the cwd
        # substitution this slice exists to close, reached through a different
        # door. That also breaks A2's own validation gate, which requires
        # `create-topic` with neither a TOPIC_SLUG nor an artifact to exit
        # non-zero: in these shapes it exited 0 and minted.
        #
        # So: one rule for all three flags — a flag must be followed by a value,
        # and that value must not itself be a flag. Anything else is refused
        # here, before `create_topic` is ever called. The gap predates this slice
        # (it was already true of --intake-source and --todo-line-ref); it is
        # fixed for all three because they share one loop and repairing one while
        # leaving two would leave the same hole under a different name.
        #
        # A2's guard rail says "the argv contract is otherwise unchanged". That
        # remains true for every WELL-FORMED invocation — positional order and
        # meaning are untouched, and no shipped caller changes behaviour. It is
        # now qualified for MALFORMED ones: `create-topic SID PROJ
        # --intake-source` previously exited 0 and minted `--intake-source__PROJ`,
        # and now exits 2. That is a deliberate widening beyond A2's literal
        # text, recorded here rather than left to read as unchanged.
        _FLAGS = {
            "--intake-source": "intake_source",
            "--todo-line-ref": "todo_line_ref",
            "--thought-file-path": "thought_file_path",
        }
        _flag_values = {}
        i = 2
        while i < len(argv):
            tok = argv[i]
            if tok in _FLAGS:
                if i + 1 >= len(argv):
                    print(f"✗ create-topic: {tok} requires a value, but none was "
                          f"given. Refusing rather than reading {tok!r} as the "
                          f"TOPIC_SLUG.", file=sys.stderr)
                    sys.exit(2)
                val = argv[i + 1]
                # `startswith("--")` alone: every key in _FLAGS starts with
                # `--`, so testing membership as well was a subsumed disjunct.
                if val.startswith("--"):
                    print(f"✗ create-topic: {tok} requires a value, but the next "
                          f"token is {val!r}, which is itself a flag. Refusing "
                          f"rather than silently shifting the positionals.",
                          file=sys.stderr)
                    sys.exit(2)
                _flag_values[_FLAGS[tok]] = val
                del argv[i:i + 2]
            else:
                i += 1
        intake_source = _flag_values.get("intake_source")
        todo_line_ref = _flag_values.get("todo_line_ref")
        # A2: the door A1's refusal needs. `create_topic` has always accepted
        # this parameter, but no CLI caller could reach it, so the
        # derive-from-artifact path was unreachable from production.
        thought_file_path = _flag_values.get("thought_file_path")

        if len(argv) < 4:
            print(
                "Usage: create-topic SESSION_ID PROJECT_SLUG [TOPIC_SLUG]"
                " [--intake-source plain|thought] [--todo-line-ref <path>:<line>]"
                " [--thought-file-path <spine>.md]\n"
                "       TOPIC_SLUG may be omitted ONLY when --thought-file-path is"
                " given: the work name is then derived from that artifact.\n"
                "       One of the two is REQUIRED — the working directory is not"
                " a source of identity.",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = argv[2]
        project_slug = argv[3]
        topic_slug = argv[4] if len(argv) > 4 else None

        # streamed-dancing-goose S5 (A5): `create_topic` takes the bookkeeping lock,
        # and `BookkeepingLockTimeout` is a TimeoutError, not a ValueError — a
        # lock-contended `/work-start` used to exit on a traceback instead of the
        # `✗ …` + exit-1 shape every caller parses. Same widening as the S1
        # `append-metrics` / `annotate-session` verbs.
        from bookkeeping_lock import BookkeepingLockTimeout as _BLT
        try:
            result = create_topic(session_id, project_slug, topic_slug,
                                  intake_source=intake_source,
                                  todo_line_ref=todo_line_ref,
                                  thought_file_path=thought_file_path)
            print(json.dumps(result, indent=2))
        except (ValueError, _BLT) as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "snapshot":
        if len(sys.argv) >= 3 and sys.argv[2] == "--validate":
            text = sys.stdin.read()
            diagnostics = validate_discovery_locked_fields(text)
            if diagnostics:
                for d in diagnostics:
                    print(f"✗ {d}", file=sys.stderr)
                sys.exit(1)
            print("✓ Discovery locked fields valid", file=sys.stderr)
            sys.exit(0)
        else:
            print("Usage: snapshot --validate  (reads discovery section text from stdin)", file=sys.stderr)
            sys.exit(2)

    elif cmd == "bypass-topic":
        # State-only bypass. The caller is responsible for any audit trail (commit-prefix, TODO line, diary entry).
        if len(sys.argv) < 3:
            print("Usage: bypass-topic SESSION_ID [reason]", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        reason = sys.argv[3] if len(sys.argv) > 3 else "no reason given"
        try:
            result = bypass_topic(session_id, reason)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "set-active":
        if len(sys.argv) < 5:
            print("Usage: set-active SESSION_ID TOPIC_SLUG PROJECT_SLUG", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        topic_slug = sys.argv[3]
        project_slug = sys.argv[4]
        from bookkeeping_lock import BookkeepingLockTimeout as _BLT   # S5: see create-topic
        try:
            result = set_active(session_id, topic_slug, project_slug)
            print(json.dumps(result, indent=2))
        except (ValueError, _BLT) as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "set-thought-file":
        if len(sys.argv) < 4:
            print("Usage: set-thought-file SESSION_ID PATH", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        path = sys.argv[3]
        try:
            result = set_thought_file(session_id, path)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "auto-register":
        # Usage: auto-register SESSION_ID --spine PATH [--bucket B] [--todo-file F]
        #
        # The skill->hook invocation seam for the ship-time mint (A2). Callers are
        # the `/work-done` composer (a PRE-step, before its four-surface write) and
        # `/close` (backstop). Each invocation is its own short-lived process that
        # takes `bookkeeping_lock` for its own short sections — no nested lock, and
        # never a `todo.py` subprocess underneath (that WOULD deadlock).
        #
        # NON-BLOCKING BY CONTRACT: always exits 0. The mint must never abort a
        # ship. A skip/partial is reported in the JSON body (and as a `warning:`
        # the caller surfaces), not as a non-zero exit.
        if len(sys.argv) < 3:
            print("Usage: auto-register SESSION_ID --spine PATH "
                  "[--bucket BUCKET] [--todo-file PATH]", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        spine_arg = None
        bucket_arg = "NOW"
        todo_file_arg = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--spine" and i + 1 < len(sys.argv):
                spine_arg = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--bucket" and i + 1 < len(sys.argv):
                bucket_arg = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--todo-file" and i + 1 < len(sys.argv):
                todo_file_arg = sys.argv[i + 1]; i += 2
            else:
                i += 1
        try:
            result = auto_register_topic(session_id, spine_arg,
                                         bucket=bucket_arg,
                                         todo_file=todo_file_arg)
        except Exception as e:      # fail-open: a mint failure never fails a ship
            result = {"status": "error", "error": f"{type(e).__name__}: {e}",
                      "warning": "auto-registration errored — the ship is "
                                 "unaffected; register the topic manually"}
        print(json.dumps(result, indent=2))
        if result.get("warning"):
            print(f"⚠ {result['warning']}", file=sys.stderr)

    elif cmd == "confirm-workflow-draft":
        if len(sys.argv) < 3:
            print("Usage: confirm-workflow-draft SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        try:
            result = confirm_workflow_draft(session_id)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "is-workflow-confirmed":
        if len(sys.argv) < 3:
            print("Usage: is-workflow-confirmed SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        proj, topic, state = _resolve_topic(session_id)
        if state is None:
            # E2: no active topic → hook fall-through (allow)
            print(json.dumps({"status": "no_active_project"}))
            sys.exit(3)
        confirmed = is_workflow_confirmed(session_id)
        if confirmed:
            print(json.dumps({"status": "confirmed"}))
            sys.exit(0)
        else:
            print(json.dumps({"status": "not_confirmed"}))
            sys.exit(1)

    elif cmd == "factcheck-workflow":
        if len(sys.argv) < 4:
            print("Usage: factcheck-workflow SESSION_ID DRAFT_PATH", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        draft_path = sys.argv[3]
        try:
            result = factcheck_workflow(session_id, draft_path)
            print(json.dumps(result, indent=2))
            if result.get("status") == "PASS":
                sys.exit(0)
            else:
                sys.exit(1)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "factcheck-plan":
        if len(sys.argv) < 4:
            print("Usage: factcheck-plan SESSION_ID PLAN_PATH", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        plan_path = sys.argv[3]
        try:
            from _factcheck_engine import factcheck_run
            result = factcheck_run(
                PLAN_VALIDATION_DIR, plan_path, "plan", session_id,
                debounce_seconds=30,
                models=["sonnet", "sonnet", "sonnet"],
                max_rounds=3,
                _proj_topic_resolver=_resolve_topic,
            )
            print(json.dumps(result, indent=2))
            sys.exit(0 if result.get("status") == "PASS" else 1)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "factcheck-thought":
        if len(sys.argv) < 4:
            print("Usage: factcheck-thought SESSION_ID THOUGHT_PATH", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        thought_path = sys.argv[3]
        try:
            from _factcheck_engine import factcheck_run
            result = factcheck_run(
                PLAN_VALIDATION_DIR, thought_path, "thought", session_id,
                debounce_seconds=30,
                models=["sonnet", "sonnet", "sonnet"],
                _proj_topic_resolver=_resolve_topic,
            )
            print(json.dumps(result, indent=2))
            sys.exit(0 if result.get("status") == "PASS" else 1)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "factcheck-research":
        if len(sys.argv) < 4:
            print("Usage: factcheck-research SESSION_ID RESEARCH_PATH [--auto] [--force]", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        research_path = sys.argv[3]
        # S2/A15 (Bug 9): the auto-dispatch (factcheck-research-file.sh) passes
        # --auto → 30s cooldown. A manual re-run (no --auto) uses debounce=0 so
        # it is never silently suppressed by the background run's cooldown.
        _auto = "--auto" in sys.argv[4:]
        _debounce = 30 if _auto else 0
        # research-fc-resume-skip-force (2026-07-14): --force opt-in clears prior
        # R-markers and re-verifies from round 1, instead of resume-skipping a
        # maxed-out artifact to a fake ESCALATE. Absent --force, a maxed-out re-run
        # returns an honest NOOP (see factcheck_run) rather than a disguised failure.
        _force = "--force" in sys.argv[4:]
        try:
            from _factcheck_engine import factcheck_run, run_style_check, CHECKER_MODELS
            # MAJOR 4 (round 2, replaces the round-1 addendum): a checker
            # cannot verify a `_CLAIMS.md` row at all, in either wording.
            # Every row is code-recorded by `research_pipeline.record_finding`
            # and carries only a claim id + a `<research file>:<line>`
            # locator — the claim TEXT lives in the ledger, not the register,
            # so a checker reading the register alone has nothing to compare
            # against a source. The round-1 replacement also asserted every
            # row is a `<file>:<line>` pair "and not to require a Source
            # Index" as though that were universal register shape — it
            # contradicts legacy hand-written rows, harvest rows anchored to
            # their OWN source (not the research file), and the legacy
            # skeleton headings the recorder's own registers still carry.
            # Register verification is a CODE job (locator resolves, line
            # exists, id is known to the ledger) that belongs to a later
            # slice, not yet built — not a model-judgment one this checker
            # can discharge from the file alone.
            _claims_addendum = (
                "Do not assess any _CLAIMS.md file alongside the research "
                "file. The claims register is written by code, not by the "
                "research author, and register verification is not part of "
                "this fact-check."
            )
            result = factcheck_run(
                PLAN_VALIDATION_DIR, research_path, "research", session_id,
                debounce_seconds=_debounce,
                # S3/A5: cross-family research panel (Sonnet/Opus/Haiku), the single
                # source from the engine. Scoped to the research kind — the other
                # dispatches (plan/thought/kl) keep their own 3-Sonnet lists.
                models=CHECKER_MODELS,
                max_rounds=3,
                scope_addendum=_claims_addendum,
                _proj_topic_resolver=_resolve_topic,
                force=_force,
            )
            print(json.dumps(result, indent=2))
            # A3 (Slice S1): run the writing-coach style check as its OWN cheaper pass,
            # structurally separate from the factual FC above. Best-effort and advisory —
            # it never changes the factual exit status. Skipped on non-terminal factual
            # outcomes (LOCKED/DEBOUNCED) to avoid duplicate work.
            if result.get("status") in ("PASS", "DIRTY", "INCOMPLETE", "ESCALATE"):
                try:
                    style_result = run_style_check(research_path)
                    print(json.dumps({"style_check": style_result.get("status")}, indent=2))
                except Exception:
                    pass
            # research-fc-resume-skip-force: NOOP (honest "nothing re-checked") gets a
            # distinct exit code (2) — not the content-failure code (1). Callers that
            # fire-and-forget (factcheck-research-file.sh) ignore the code; the
            # convergence Stop gates read R-markers, not this exit. The JSON
            # "status": "NOOP" + message is the operator-facing signal.
            _final = result.get("status")
            if _final == "PASS":
                sys.exit(0)
            elif _final == "NOOP":
                sys.exit(2)
            else:
                sys.exit(1)
        except ValueError as e:
            # A3 — name the sanctioned recovery at the moment the operator is
            # choosing what to do next.
            #
            # Scope, stated so nobody over-reads this: on the AUTO path this
            # stream is redirected to /tmp/factcheck-research-<sid>.log, which
            # nothing in the tree reads, so text improved here is invisible
            # there. It is visible on the MANUAL re-run, where a person ran the
            # command and is looking at the output — which is the moment the
            # observed improvisation happened. The close-time block message is
            # where this instruction reaches the operator on the auto path.
            #
            # The "not a substitute" line gives the REASON rather than asserting
            # a rule: a hand-rolled panel produces no attested `checker_models`
            # and no marker any gate can read, so its result cannot clear the
            # obligation even when its judgement is sound
            # (`~/.claude/rules/orchestrator-pattern.md` §5 classes this as
            # "creative substitution" — rules-and-discipline, not code-enforced).
            print(f"✗ {e}", file=sys.stderr)
            print(
                "  The fact-check did NOT run, so nothing has checked this file.\n"
                "  Re-run it against a bound topic:\n"
                "      python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py "
                "factcheck-research <SESSION_ID> <RESEARCH_FILE>\n"
                "  Or, to accept the file unchecked, on the record:\n"
                "      python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py "
                "accept-research-incomplete <SESSION_ID> "
                "--file <RESEARCH_FILE> --reason '<why>'\n"
                "  Do NOT stand in a hand-rolled checker panel: it produces no "
                "attested\n"
                "  checker_models and no marker any gate can read, so it cannot "
                "clear this.",
                file=sys.stderr,
            )
            sys.exit(1)

    elif cmd == "accept-research-incomplete":
        # argv: SESSION_ID --reason TEXT [--cycle-id CID]
        # S2/A14 (Bug 8): the operator consciously accepts an honest INCOMPLETE.
        # Stamps a sanctioned accepted-INCOMPLETE R-marker (verdict: INCOMPLETE +
        # accept_reason) that BOTH Stop gates honor, and records the disposition
        # on the manifest as the audit-of-response. Engine-layer — both callers
        # inherit; producer-never-verifies (engine writes on an explicit signal).
        if len(sys.argv) < 3:
            print(
                "Usage: accept-research-incomplete SESSION_ID --reason TEXT "
                "[--file PATH] [--cycle-id CID]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        reason = None
        cycle_id = "default"
        research_file = None
        i = 3
        while i < len(sys.argv):
            tok = sys.argv[i]
            if tok == "--reason" and i + 1 < len(sys.argv):
                reason = sys.argv[i + 1]; i += 2
            elif tok == "--cycle-id" and i + 1 < len(sys.argv):
                cycle_id = sys.argv[i + 1]; i += 2
            elif tok == "--file" and i + 1 < len(sys.argv):
                # A5/F3. `_accept_target_slot` has supported a named file since
                # S8 and REFUSES rather than guesses when a cycle holds more
                # than one candidate — so without this flag any session that
                # wrote two research files was permanently unclearable: the
                # engine could route the accept, and the operator had no way to
                # say where.
                research_file = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if not reason or not reason.strip():
            print(
                "✗ accept-research-incomplete requires a non-empty --reason "
                "(the recorded reason for consciously accepting the INCOMPLETE).",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            from _factcheck_engine import accept_research_incomplete
            result = accept_research_incomplete(
                PLAN_VALIDATION_DIR, session_id, reason,
                kind="research", cycle_id=cycle_id,
                _proj_topic_resolver=_resolve_topic,
                research_file=research_file,
            )
            # Audit-of-response: also record the accepted disposition on the
            # manifest (best-effort — the R-marker is the gate's source of truth).
            try:
                import research_pipeline as _rp
                _rp.cmd_record_non_pass(
                    session_id,
                    f"RESEARCH-VERDICT: accepted INCOMPLETE — {reason.strip()}",
                    cycle_id=cycle_id,
                )
            except Exception:
                pass
            print(json.dumps(result, indent=2))
            sys.exit(0)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "factcheck-recommendation":
        # argv: SESSION_ID DRAFT_PATH [--against TEXT] [--sonnet N] [--opus N] [--rounds N]
        if len(sys.argv) < 4:
            print(
                "Usage: factcheck-recommendation SESSION_ID DRAFT_PATH "
                "[--against TEXT] [--sonnet N] [--opus N] [--rounds N]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        draft_path = sys.argv[3]

        against = None
        sonnet_count = 3
        opus_count = 0
        max_rounds_arg = 2
        i = 4
        try:
            while i < len(sys.argv):
                tok = sys.argv[i]
                if tok == "--against":
                    against = sys.argv[i + 1]; i += 2
                elif tok == "--sonnet":
                    sonnet_count = int(sys.argv[i + 1]); i += 2
                elif tok == "--opus":
                    opus_count = int(sys.argv[i + 1]); i += 2
                elif tok == "--rounds":
                    max_rounds_arg = int(sys.argv[i + 1]); i += 2
                else:
                    i += 1
        except (IndexError, ValueError) as e:
            print(f"✗ Invalid argument: {e}", file=sys.stderr)
            sys.exit(2)

        models = ["sonnet"] * sonnet_count + ["opus"] * opus_count
        if len(models) < 1:
            print("✗ At least 1 checker required (--sonnet or --opus ≥ 1)", file=sys.stderr)
            sys.exit(2)

        # Validate artifact file exists and is non-empty before dispatch.
        # code_first_architecture.md:18 — "If a rule can be enforced by code, it must be."
        from pathlib import Path as _Path
        _draft = _Path(draft_path)
        if not _draft.exists():
            print(f"✗ Draft file not found: {draft_path}", file=sys.stderr)
            sys.exit(2)
        if not _draft.read_text(encoding="utf-8").strip():
            print(f"✗ Draft file is empty: {draft_path}", file=sys.stderr)
            sys.exit(2)

        # Per-invocation unique topic prevents R<N>.md collision across concurrent calls.
        # debounce_seconds=0: user-invoked on-demand; no cooldown.
        ts = int(datetime.now(timezone.utc).timestamp())
        unique_topic = f"rec_{session_id[:8]}_{ts}"

        scope_addendum = None
        if against:
            scope_addendum = f"Validate the recommendation against: {against}"

        try:
            from _factcheck_engine import factcheck_run
            result = factcheck_run(
                PLAN_VALIDATION_DIR,
                draft_path,
                "recommendation",
                session_id,
                debounce_seconds=0,
                models=models,
                max_rounds=max_rounds_arg,
                scope_addendum=scope_addendum,
                proj="adhoc",
                topic=unique_topic,
            )
            print(json.dumps(result, indent=2))
            sys.exit(0 if result.get("status") == "PASS" else 1)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "aggregate-kl-extraction":
        # argv: SESSION_ID --proj <p> --topic <t> --round <n> [--chapter <c>] <file> <file> <file> [...]
        if len(sys.argv) < 3 or sys.argv[2] in {"-h", "--help"}:
            print(
                "Usage: aggregate-kl-extraction SESSION_ID --proj <p> --topic <t> "
                "--round <n> [--chapter <c>] <file1> <file2> <file3> [...]",
                file=sys.stderr,
            )
            sys.exit(0 if len(sys.argv) >= 3 and sys.argv[2] in {"-h", "--help"} else 2)
        argv = sys.argv[3:]
        proj = topic = chapter = None
        round_n = None
        files = []
        i = 0
        try:
            while i < len(argv):
                tok = argv[i]
                if tok == "--proj":
                    proj = argv[i + 1]; i += 2
                elif tok == "--topic":
                    topic = argv[i + 1]; i += 2
                elif tok == "--round":
                    round_n = int(argv[i + 1]); i += 2
                elif tok == "--chapter":
                    chapter = argv[i + 1]; i += 2
                else:
                    files.append(tok); i += 1
        except (IndexError, ValueError) as e:
            print(f"✗ Invalid argument list: {e}", file=sys.stderr)
            sys.exit(2)

        if proj is None or topic is None or round_n is None:
            print(
                "✗ --proj, --topic, --round are required.",
                file=sys.stderr,
            )
            sys.exit(2)

        errs = _validate_kl_extraction_inputs(proj, topic, files)
        if errs:
            for e in errs:
                print(f"✗ {e}", file=sys.stderr)
            sys.exit(2)

        audit_log_path = (
            PLAN_VALIDATION_DIR / proj / topic / "kl_extraction" / "audit.jsonl"
        )
        from _factcheck_engine import aggregate_kl_extraction_round
        result = aggregate_kl_extraction_round(
            files, round_n, proj, topic, PLAN_VALIDATION_DIR,
            chapter=chapter, audit_log_path=audit_log_path,
            max_rounds=3,
        )
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("status") == "PASS" else 1)

    elif cmd == "is-plan-validated":
        if len(sys.argv) < 4:
            print("Usage: is-plan-validated SESSION_ID PLAN_PATH", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        plan_path = sys.argv[3]
        code, reason = is_plan_validated(session_id, plan_path)
        print(json.dumps({"status": reason}))
        sys.exit(code)

    elif cmd == "is-validation-waived":
        if len(sys.argv) < 5:
            print("Usage: is-validation-waived SESSION_ID PLAN_PATH MESSAGE_PATH", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        plan_path = sys.argv[3]
        message_path = sys.argv[4]
        code, reason = is_validation_waived(session_id, plan_path, message_path)
        print(json.dumps({"status": reason}))
        sys.exit(code)

    elif cmd == "append-metrics":
        if len(sys.argv) < 4:
            print(
                "Usage: append-metrics SESSION_ID '<json>'",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        try:
            payload = json.loads(sys.argv[3])
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON payload: {e}", file=sys.stderr)
            sys.exit(1)
        # A lock-contended run REPORTS in the `✗` shape rather than dying on a
        # traceback: BookkeepingLockTimeout is a TimeoutError, not a ValueError.
        from bookkeeping_lock import BookkeepingLockTimeout
        try:
            result = append_metrics(session_id, payload)
            print(json.dumps(result, indent=2))
        except (ValueError, BookkeepingLockTimeout) as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "delivery-links":
        # delivery-links REPO_DIR SHA [--base SHA] — S7: the commit/diff links
        # for the /close delivery record, derived from the repo's remote (code,
        # not typed). Prints JSON; url fields are null when there is no
        # browsable remote. Never exits non-zero on a git problem — the record
        # is content and must not fail a /close.
        if len(sys.argv) < 4:
            print("Usage: delivery-links REPO_DIR SHA [--base SHA]", file=sys.stderr)
            sys.exit(2)
        base = None
        if "--base" in sys.argv:
            i = sys.argv.index("--base")
            base = sys.argv[i + 1] if i + 1 < len(sys.argv) else None
        print(json.dumps(delivery_links(sys.argv[2], sys.argv[3], base_sha=base), indent=2))

    elif cmd == "annotate-session":
        # annotate-session SESSION_ID "<bullet>" [--writer work-done|close]
        # `--writer` is the identity of the SKILL invoking the CLI (S1). The
        # default is `work-done` so the existing /work-done call is unchanged;
        # `/close --annotate` MUST pass `--writer close`, or its bullet reads as
        # a ship record to the omission gate.
        args = list(sys.argv[2:])
        writer = SESSION_WRITER_WORK_DONE
        if "--writer" in args:
            i = args.index("--writer")
            if i + 1 >= len(args):
                print("✗ --writer requires a value", file=sys.stderr)
                sys.exit(2)
            writer = args[i + 1]
            del args[i:i + 2]
        if len(args) < 2:
            print(
                'Usage: annotate-session SESSION_ID "<bullet>" [--writer work-done|close]',
                file=sys.stderr,
            )
            sys.exit(2)
        session_id, bullet = args[0], args[1]
        from bookkeeping_lock import BookkeepingLockTimeout
        try:
            result = annotate_session(session_id, bullet, writer=writer)
            print(json.dumps(result, indent=2))
        except (ValueError, BookkeepingLockTimeout) as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd in ("get-clarification-phase-session", "get-preplanning-session"):
        # Returns clarification_phase_session_id from active topic state; empty on miss.
        # `get-preplanning-session` is a B5 back-compat alias scheduled for removal
        # one release cycle after the 2026-06-13 rename (cryptic-mapping-torvalds).
        if cmd == "get-preplanning-session":
            print(
                "deprecation: `get-preplanning-session` is an alias for "
                "`get-clarification-phase-session`; update callers.",
                file=sys.stderr,
            )
        if len(sys.argv) < 3:
            sys.exit(0)
        session_id = sys.argv[2]
        proj, topic, t_state = _resolve_topic(session_id)
        if t_state is not None:
            sid = t_state.get("clarification_phase_session_id", "")
            if sid:
                print(sid)
        sys.exit(0)

    elif cmd == "get-thought-file":
        # Returns thought_file_path from active topic state; empty on miss.
        if len(sys.argv) < 3:
            sys.exit(0)
        session_id = sys.argv[2]
        proj, topic, t_state = _resolve_topic(session_id)
        if t_state is not None:
            path = t_state.get("thought_file_path", "")
            if path:
                print(path)
        sys.exit(0)

    elif cmd == "resolve-target-file":
        # Returns the absolute path for a handoff-prompt target file.
        # Resolution hierarchy (4 levels):
        #   1. FILE_ARG provided + exists        → print absolute path
        #   2. FILE_ARG provided + missing:
        #      - matches *_THOUGHT.md|*_RESEARCH.md|*_PLAN.md → OFFER_CREATE:<abs_path>
        #      - otherwise                        → NOT_FOUND:<abs_path>
        #   3. No FILE_ARG + active topic has thought_file_path → print absolute path
        #   4. No FILE_ARG + no state             → print empty string
        if len(sys.argv) < 3:
            sys.exit(0)
        session_id = sys.argv[2]
        file_arg = sys.argv[3] if len(sys.argv) >= 4 else None

        if file_arg is not None:
            candidate = Path(file_arg)
            if not candidate.is_absolute():
                candidate = PROJECTS_ROOT / file_arg
            candidate = candidate.resolve()
            if candidate.exists():
                print(str(candidate))
            else:
                name = candidate.name
                if any(name.endswith(s) for s in ("_THOUGHT.md", "_RESEARCH.md", "_PLAN.md")):
                    print(f"OFFER_CREATE:{candidate}")
                else:
                    print(f"NOT_FOUND:{candidate}")
        else:
            _, _, t_state = _resolve_topic(session_id)
            if t_state is not None:
                path = t_state.get("thought_file_path", "")
                if path:
                    p = Path(path)
                    if not p.is_absolute():
                        p = PROJECTS_ROOT / path
                    print(str(p.resolve()))
                    sys.exit(0)
            print("")
        sys.exit(0)

    elif cmd == "content-hash":
        # Usage: content-hash PROMPT_FILE
        # Prints SHA-256[:12] of the file's exact bytes — the deterministic
        # key the skill passes to handoff-verify (A6), identical to the value
        # the A3 write gate computes for the same content. The skill must
        # never hand-compute this (code owns deterministic work).
        if len(sys.argv) < 3:
            print("Usage: content-hash PROMPT_FILE", file=sys.stderr)
            sys.exit(2)
        cp = Path(sys.argv[2])
        if not cp.exists():
            print(f"NOT_FOUND:{cp}", file=sys.stderr)
            sys.exit(2)
        print(_content_hash(cp.read_text(encoding="utf-8")))
        sys.exit(0)

    elif cmd == "write-next-session-prompt":
        # Idempotent, verified write of the ## Next Session Prompt section.
        # Usage: write-next-session-prompt FILE_PATH PROMPT_CONTENT
        #        write-next-session-prompt FILE_PATH --from-file PROMPT_FILE
        # This is the SOLE code write gate (A3). --from-file avoids fragile
        # multi-line shell-arg quoting; the bytes read here are exactly what
        # content-hash/handoff-verify keyed on.
        if len(sys.argv) < 4:
            print(
                "Usage: write-next-session-prompt FILE_PATH "
                "(PROMPT_CONTENT | --from-file PROMPT_FILE)",
                file=sys.stderr,
            )
            sys.exit(2)
        file_path = Path(sys.argv[2])
        if sys.argv[3] == "--from-file":
            if len(sys.argv) < 5:
                print(
                    "Usage: write-next-session-prompt FILE_PATH "
                    "--from-file PROMPT_FILE",
                    file=sys.stderr,
                )
                sys.exit(2)
            pf = Path(sys.argv[4])
            if not pf.exists():
                print(f"NOT_FOUND:{pf}", file=sys.stderr)
                sys.exit(2)
            prompt_content = pf.read_text(encoding="utf-8")
        else:
            prompt_content = sys.argv[3]
        if not file_path.exists():
            print(f"NOT_FOUND:{file_path}", file=sys.stderr)
            sys.exit(2)
        text = file_path.read_text(encoding="utf-8")

        # --- A3 sole-gate enforcement (handoff-prompt-advisory-leak C1/C3/C4) ---
        # (1) The operator Advisory must never reach the persisted artifact.
        if ADVISORY_RE.search(prompt_content):
            print(
                "REJECT:advisory-in-content — the operator Advisory must never "
                "be persisted into ## Next Session Prompt (it is chat-only)",
                file=sys.stderr,
            )
            sys.exit(2)
        # (2) Refuse any write lacking a matching content-hash-keyed PASS marker
        #     (producer-never-verifies; persisted <=> verified). Same trust
        #     shape as check-plan-gates.sh Gate 3 reading the §7 verdict field.
        chash = _content_hash(prompt_content)
        marker_path = HANDOFF_VERIFY_DIR / f"{chash}.md"
        if not marker_path.exists():
            print(
                f"REJECT:unverified — no handoff-verify marker for content "
                f"hash {chash} (run /double-check, then handoff-verify)",
                file=sys.stderr,
            )
            sys.exit(2)
        mfields = {}
        for line in marker_path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "---":
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                mfields[k.strip()] = v.strip()
        if mfields.get("verdict") != "PASS":
            print(
                f"REJECT:unverified — marker {chash}.md verdict is "
                f"{mfields.get('verdict') or 'absent'}, not PASS",
                file=sys.stderr,
            )
            sys.exit(2)
        if mfields.get("content_hash") != chash:
            print(
                f"REJECT:unverified — marker content_hash mismatch "
                f"({mfields.get('content_hash')} != {chash})",
                file=sys.stderr,
            )
            sys.exit(2)
        dc_artifact = mfields.get("dc_artifact", "")
        if not dc_artifact or not Path(dc_artifact).exists():
            print(
                f"REJECT:unverified — referenced dc_artifact missing on disk: "
                f"{dc_artifact or '(none)'}",
                file=sys.stderr,
            )
            sys.exit(2)

        section_header = NEXT_SESSION_PROMPT_HEADING
        # On accept, embed the source hash so handoff-freshness can later
        # decide FRESH/STALE. Hash covers the four DISCOVERY_LOCKED_FIELDS
        # only (see _discovery_locked_fields_hash; regex: _DISCOVERY_SECTION_RE;
        # write seam here). Absent `# Discovery` or missing locked fields ->
        # no marker -> handoff-freshness returns ABSENT (safe: compose-and-verify
        # on the next handoff). Legacy files with `## Snapshot` but no
        # `# Discovery` also get ABSENT — migration is owner-driven.
        _fresh_inputs = FreshnessGateInputs.from_spine(text)
        src_hash = _fresh_inputs.discovery_locked_fields_hash
        body = prompt_content.strip()
        if src_hash:
            # parsed-floating-naur Slice S1: emit 3-segment marker + render a
            # visible `*Written: <ISO>*` header as the first body line. The
            # header and the marker's `written:` segment share a single
            # timestamp value so human-visible and code-visible truths cannot
            # drift apart. Phase fingerprint = `n/a` when the spine has no
            # `# Solution Design` section (keeps marker shape stable).
            phase_hash = _fresh_inputs.phase_progress_fingerprint
            phase_segment = phase_hash if phase_hash else "n/a"
            written_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            body = f"*Written: {written_iso}*\n\n{body}"
            body += (
                f"\n\n<!-- handoff-src-hash: {src_hash} "
                f"phase: {phase_segment} written: {written_iso} -->"
            )
        new_section = f"{section_header}\n\n{body}\n"
        # S5/G6: ANCHORED, never `section_header in text`. A prose line
        # beginning with this heading exists in the corpus, and the substring
        # test took the replace branch on it — where the regex then matched
        # nothing, `sub` returned the text unchanged, and the verb reported
        # `{"status": "written"}` over a file it had not altered.
        if find_heading(text, section_header) != -1:
            # Replace from header to next ##-level header (or EOF). Use a
            # function replacement so backslashes / group refs in the prompt
            # are written verbatim (re.sub would otherwise interpret them).
            pattern = re.compile(
                r"^" + re.escape(section_header) + r"\n(.*?)(?=\n## |\Z)",
                re.DOTALL | re.MULTILINE,
            )
            replaced_text = pattern.sub(lambda _m: new_section, text, count=1)
            # The report is keyed on whether the BYTES changed, not on which
            # branch was taken. Tightening the predicate alone would close
            # today's hole and leave the next regex drift free to reopen it:
            # any future mismatch between the branch test and the pattern
            # silently reappears as a no-write reported as a write.
            if replaced_text == text:
                print(json.dumps({
                    "status": "no-op",
                    "file": str(file_path),
                    "action": "matched-nothing",
                    "reason": (
                        "the handoff heading was found but the section pattern "
                        "replaced nothing, so the file is unchanged"),
                }))
                # EXIT 1, not 0 — and the difference is the whole point.
                #
                # An earlier cut exited 0 here, on the reasoning that the verb
                # had not errored: it detected the condition and reported it
                # honestly. A conformance checker pointed out what that misses.
                # Exit 0 is the SAME code a successful write returns, so the
                # only thing distinguishing "persisted" from "silently did
                # nothing" was a JSON field a caller has to notice — and the
                # callers here are skill markdown read by a model, one of which
                # says only "On `{"status": "written"}`, report success" and is
                # silent on every other status. That leaves the signal resting
                # on AI diligence, which is the layer this codebase puts
                # LEAST trust in (`code_first_architecture.md`: Code > Rules >
                # Skill text) — and reporting a non-write as fine at the exit
                # code layer is the very defect this branch exists to end,
                # moved down one level.
                #
                # 1 rather than 2 on this file's own convention: 2 means the
                # verb REFUSED (bad input, unverified prompt, advisory leak),
                # and the input here was valid. 1 matches `handoff-fresh-since`
                # one verb over, which returns 1 for STALE/ABSENT precisely so
                # `/work-done`'s M7 step fails loud on "the handoff was not
                # actually refreshed" — the identical category of event.
                sys.exit(1)
            action = "replaced"
        else:
            # Insert after ## Next Step if present, else append at EOF.
            #
            # ANCHORED too, for the same reason the branch above is. This was
            # `insert_header in text` + `text.index(...)`, so a prose mention of
            # the heading in a file with no real `## Next Step` section made
            # this branch believe one existed and splice the handoff at
            # whatever point followed that false match.
            #
            # A deliberate two-line widening of S5's stated scope, recorded
            # rather than slipped in: the slice's guard rails name only the
            # primary heading's check. A round-2 checker confirmed this branch
            # cannot report a false `written` (every sub-path inserts non-empty
            # content, so the bytes always change) — its risk is MISPLACED
            # insertion, not phantom success, which is why it was out of scope.
            # It is fixed anyway because it is the identical defect class in
            # the same function, the machinery was already imported, and
            # leaving an unanchored match beside a freshly anchored one is how
            # the next reader concludes the anchoring was arbitrary.
            insert_header = "## Next Step"
            insert_idx = find_heading(text, insert_header)
            if insert_idx != -1:
                idx = insert_idx
                # Find end of that section (next ## or EOF)
                rest = text[idx:]
                next_section = re.search(r"\n(?=## )", rest[len(insert_header):])
                if next_section:
                    insert_pos = idx + len(insert_header) + next_section.start() + 1
                else:
                    insert_pos = len(text)
                replaced_text = text[:insert_pos] + "\n" + new_section + text[insert_pos:]
            else:
                replaced_text = text.rstrip() + "\n\n" + new_section
            action = "appended"
        # S2 / A2 — the write-path guard. On a locked spine, a handoff section that
        # would move a locked Discovery body is refused before any byte is written;
        # on every other file the predicate finds no lock marker and allows.
        # 2 = this verb's REFUSED code. The session comes from the environment —
        # this verb takes none on its argv.
        try:
            _write_spine_guarded(file_path, text, replaced_text,
                                 session_id=os.environ.get("SESSION_ID"),
                                 writer="write-next-session-prompt")
        except LockedDiscoveryWriteRefused as e:
            print(f"REJECT:locked-discovery — {e}", file=sys.stderr)
            sys.exit(2)
        print(json.dumps({"status": "written", "file": str(file_path), "action": action}))
        sys.exit(0)

    elif cmd == "handoff-freshness":
        # Usage: handoff-freshness FILE
        # Deterministic source-change signal for the handoff lifecycle (A2).
        # Hash scope = DISCOVERY_LOCKED_FIELDS inside `# Discovery` (A3/A4
        # invariant — the only fields the pfh composer reads). Prints one of:
        #   ABSENT  — no `# Discovery` section, OR no embedded src-hash marker
        #   STALE   — locked-fields hash differs from the embedded marker
        #   FRESH   — hashes equal (existing prompt reusable, no rewrite)
        # Over-detection (ABSENT/STALE) is the safe direction: it triggers
        # compose-and-verify, never a silent stale reuse.
        if len(sys.argv) < 3:
            print("Usage: handoff-freshness FILE", file=sys.stderr)
            sys.exit(2)
        fp = Path(sys.argv[2])
        if not fp.exists():
            print(f"NOT_FOUND:{fp}", file=sys.stderr)
            sys.exit(2)
        ftext = fp.read_text(encoding="utf-8")
        _fresh_inputs = FreshnessGateInputs.from_spine(ftext)
        src_hash = _fresh_inputs.discovery_locked_fields_hash
        if src_hash is None:
            print("ABSENT")
            sys.exit(0)
        # S5/G6: scoped to the `## Next Session Prompt` section, never the whole
        # file. A v2 spine carries a legacy 1-segment marker of its own at the
        # top; searching the file found THAT one and compared it against a live
        # hash it was never written from, so freshness read STALE on a spine
        # whose handoff was current.
        m = handoff_marker_in_section(ftext)
        if m is None:
            print("ABSENT")
            sys.exit(0)
        # parsed-floating-naur Slice S1: two-segment freshness check.
        # Mismatch on the locked-Discovery hash → STALE (as before). For phase
        # progression: a legacy marker (group 2 None) on a spine that now has
        # `# Solution Design` content is STALE (safe-direction over-detection
        # per the policy above); on a non-phased spine it stays FRESH so
        # already-persisted markers don't churn. New markers compare the
        # captured phase segment against the live fingerprint.
        if m.group(1) != src_hash:
            print("STALE")
            sys.exit(0)
        phase_hash = _fresh_inputs.phase_progress_fingerprint
        marker_phase = m.group(2)
        if marker_phase is None:
            print("STALE" if phase_hash is not None else "FRESH")
            sys.exit(0)
        expected_phase = phase_hash if phase_hash is not None else "n/a"
        print("FRESH" if marker_phase == expected_phase else "STALE")
        sys.exit(0)

    elif cmd == "handoff-fresh-since":
        # Usage: handoff-fresh-since SPINE SINCE_ISO
        # Code-enforced fail-loud check for the M7 ship-event handoff regen
        # (project-tracking-staleness S5). Re-reads the spine's persisted
        # `## Next Session Prompt` marker (via _SRC_HASH_MARKER_RE group-3
        # `written:`) and asserts it was (re)written at/after SINCE_ISO — the
        # ship-window start. Prints one of, and exits:
        #   NO_DISCOVERY (0) — spine has no `# Discovery`, so the write seam
        #                      embeds no `written:` marker (see write-next-
        #                      session-prompt `if src_hash:`) → best-effort,
        #                      not a failure.
        #   FRESH        (0) — written >= since (regen refreshed this ship event)
        #   STALE        (1) — written <  since (regen did not refresh)
        #   ABSENT       (1) — Discovery present but no `written:` marker
        #                      (regen should have created one)
        # /work-done's M7 step fails loud on a non-zero exit. Pure read; no writes.
        if len(sys.argv) < 4:
            print("Usage: handoff-fresh-since SPINE SINCE_ISO", file=sys.stderr)
            sys.exit(2)
        fp = Path(sys.argv[2])
        since_raw = sys.argv[3]
        if not fp.exists():
            print(f"NOT_FOUND:{fp}", file=sys.stderr)
            sys.exit(2)
        ftext = fp.read_text(encoding="utf-8")
        if _discovery_locked_fields_hash(ftext) is None:
            print("NO_DISCOVERY")
            sys.exit(0)
        # S5/G6: same scoping as `handoff-freshness`. Both verbs read the same
        # marker or neither does; two search rules is how they came apart.
        m = handoff_marker_in_section(ftext)
        written = m.group(3) if m else None
        if not written:
            print("ABSENT")
            sys.exit(1)

        def _parse_iso_utc(s):
            # Python 3.9 `fromisoformat` rejects a trailing 'Z'; the `written:`
            # marker uses `...Z` while the lock `started_at` uses `+00:00`.
            # Normalize both, then compare tz-aware (assume UTC if naive).
            s = s.strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt

        try:
            written_dt = _parse_iso_utc(written)
            since_dt = _parse_iso_utc(since_raw)
        except ValueError as e:
            print(f"BAD_TIMESTAMP:{e}", file=sys.stderr)
            sys.exit(2)
        if written_dt >= since_dt:
            print("FRESH")
            sys.exit(0)
        print("STALE")
        sys.exit(1)

    elif cmd == "handoff-verify":
        # Usage: handoff-verify CONTENT_HASH VERDICT DC_ARTIFACT
        # Records the /double-check verdict as a content-hash-keyed marker that
        # the A3 write gate consumes. Schema mirrors factcheck-convergence.md §7.
        # One marker per content hash; re-verify of identical content overwrites.
        if len(sys.argv) < 5:
            print(
                "Usage: handoff-verify CONTENT_HASH VERDICT DC_ARTIFACT",
                file=sys.stderr,
            )
            sys.exit(2)
        chash = sys.argv[2].strip()
        verdict = sys.argv[3].strip()
        dc_artifact = sys.argv[4].strip()
        if not _CONTENT_HASH_RE.match(chash):
            print(
                f"INVALID:content_hash must be 12 lowercase hex chars, "
                f"got {chash!r}",
                file=sys.stderr,
            )
            sys.exit(2)
        HANDOFF_VERIFY_DIR.mkdir(parents=True, exist_ok=True)
        checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        marker = (
            "---\n"
            "schema_version: 2\n"
            "kind: handoff_prompt\n"
            f"verdict: {verdict}\n"
            f"content_hash: {chash}\n"
            f"dc_artifact: {dc_artifact}\n"
            f"checked_at: {checked_at}\n"
            "---\n"
        )
        (HANDOFF_VERIFY_DIR / f"{chash}.md").write_text(marker, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": "marker-written",
                    "content_hash": chash,
                    "verdict": verdict,
                }
            )
        )
        sys.exit(0)

    elif cmd == "projects-root":
        # Returns the canonical PROJECTS_ROOT absolute path. Single source of
        # truth for hooks/scripts that need the projects-tree root.
        print(str(PROJECTS_ROOT))
        sys.exit(0)

    elif cmd == "canonical-topic":
        # Usage: canonical-topic <artifact>.md
        # Prints the canonical topic slug an artifact derives, or refuses.
        #
        # Exists so a SKILL never has to restate the slug grammar in prose
        # (S2/A5). `/work-start` used to instruct the AI to hand-derive
        # `<slug>_THOUGHT.md -> <slug>` and then pass the result to
        # `create-topic` as an EXPLICIT topic slug, which outranks the artifact
        # derivation by design — so a prose reading that disagreed with the code
        # won, and filed a second record for work that already had one. Prose
        # cannot be kept in lock-step with code; this verb removes the need to
        # try.
        #
        # Read-only, single value on stdout, no state touched. Exits 1 with a
        # named reason when the artifact yields no usable work name, so a caller
        # that cannot derive stops rather than substituting something ambient.
        if len(sys.argv) < 3:
            print("Usage: canonical-topic <artifact>.md", file=sys.stderr)
            sys.exit(2)
        _slug = canonical_topic_for_spine(sys.argv[2])
        if _slug is None:
            print(
                "✗ cannot derive a topic slug from "
                f"{sys.argv[2]!r}. It must name a Markdown artifact ('.md') "
                "whose stem, after the TYPE and timestamp suffixes are "
                "stripped, is a usable work name — not blank, not '.'/'..', "
                "and not containing '__' (the record filename separator).",
                file=sys.stderr,
            )
            sys.exit(1)
        print(_slug)
        sys.exit(0)

    # ------------------------------------------------------------------
    # Phase authority commands (Slice A)
    # ------------------------------------------------------------------

    elif cmd == "phase-start":
        # Usage: phase-start SESSION_ID PHASE --auth TOKEN [--artifact LINK] [--todo-text TEXT]
        # Plain-mode initial entry: --auth may be omitted when the resolved
        # topic-state has intake_source == "plain" AND phase is None
        # (work-start/SKILL.md §"Step 4 / Plain mode").
        if len(sys.argv) < 4:
            print(
                "Usage: phase-start SESSION_ID PHASE --auth TOKEN [--artifact LINK] [--todo-text TEXT]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        phase = sys.argv[3]
        auth_token = None
        artifact_link = None
        todo_text_arg = None
        i = 4
        while i < len(sys.argv):
            if sys.argv[i] == "--auth" and i + 1 < len(sys.argv):
                auth_token = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--artifact" and i + 1 < len(sys.argv):
                artifact_link = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--todo-text" and i + 1 < len(sys.argv):
                todo_text_arg = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if auth_token is None:
            # Allow the call through only for plain-mode initial entry; any
            # other shape falls back to the legacy --auth-required contract.
            try:
                _, _, _state_for_dispatch = _resolve_topic(session_id)
            except Exception:
                _state_for_dispatch = None
            plain_initial = (
                _state_for_dispatch is not None
                and _state_for_dispatch.get("intake_source") == "plain"
                and _state_for_dispatch.get("phase") is None
            )
            if not plain_initial:
                print(
                    "Usage: phase-start SESSION_ID PHASE --auth TOKEN [--artifact LINK] [--todo-text TEXT]",
                    file=sys.stderr,
                )
                sys.exit(2)
        from bookkeeping_lock import BookkeepingLockTimeout as _BLT   # S5: phase_start → write_phase_marker holds the spine lock
        try:
            result = phase_start(session_id, phase, auth_token, artifact_link, todo_text=todo_text_arg)
            print(json.dumps(result, indent=2))
        except (ValueError, _BLT) as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "phase-stop":
        if len(sys.argv) < 3:
            print("Usage: phase-stop SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        from bookkeeping_lock import BookkeepingLockTimeout as _BLT   # S5: phase_stop → write_phase_marker holds the spine lock
        try:
            result = phase_stop(session_id)
            print(json.dumps(result, indent=2))
            marker = result.get("phase_marker") or {}
            if marker.get("status") == "skipped_no_phase":
                print(
                    "Note: no phase is recorded for this topic, so the "
                    "phase-boundary marker was not written (the stop event "
                    "itself is still recorded). Run "
                    "'write-phase-marker SESSION_ID --phase PHASE' to backfill "
                    "the marker once the phase is known.",
                    file=sys.stderr,
                )
        except (ValueError, _BLT) as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "phase-status":
        if len(sys.argv) < 3:
            print("Usage: phase-status SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        result = phase_status(session_id)
        print(json.dumps(result, indent=2))

    elif cmd == "recent-phase-events":
        # M6 read-side (S3): the most-recent-session window of phase_history,
        # rendered for /work-start's session-open surfacing. Read-only, state-dir
        # only. Always exit 0 on a resolvable/absent topic (no topic is not error).
        if len(sys.argv) < 3:
            print("Usage: recent-phase-events SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        print(json.dumps(recent_phase_events(session_id), indent=2, default=str))

    elif cmd == "write-phase-marker":
        # A3 — standalone idempotent retry of the S6 phase-boundary marker after
        # a fail-loud inside phase_start/phase_stop (the phase transition is
        # already durable and cannot be re-run). Infers event+phase from the most
        # recent phase_history entry; --event/--phase override. Exit 1 on a
        # WriteVerificationError (the marker still did not land).
        # Usage: write-phase-marker SESSION_ID [--event start|stop] [--phase PHASE]
        if len(sys.argv) < 3:
            print("Usage: write-phase-marker SESSION_ID [--event start|stop] [--phase PHASE]",
                  file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        event_override = None
        phase_override = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--event" and i + 1 < len(sys.argv):
                event_override = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--phase" and i + 1 < len(sys.argv):
                phase_override = sys.argv[i + 1]; i += 2
            else:
                i += 1
        try:
            _, _, _st = _resolve_topic(session_id)
        except Exception:
            _st = None
        if _st is None:
            print(json.dumps({"status": "no_active_topic"}, indent=2))
            sys.exit(0)
        event = event_override
        phase = phase_override
        if event is None or phase is None:
            hist = _st.get("phase_history") or []
            last = hist[-1] if hist and isinstance(hist[-1], dict) else None
            if last is not None:
                if last.get("event") == "stop":
                    event = event or "stop"
                    phase = phase or last.get("phase")
                else:
                    event = event or "start"
                    phase = phase or last.get("to")
        if event is None:
            event = "start"
        if phase is None:
            phase = _st.get("phase")
        if phase is None:
            # A3 — refuse rather than guess. No phase could be inferred from
            # phase_history or the stored topic state; the recovery verb must
            # not invent one. Name the absent condition and hand the operator
            # the explicit escape.
            print(
                "✗ no phase recorded for this topic (phase_history and the "
                "stored topic state both carry no phase) — nothing was "
                "written. Re-run with 'write-phase-marker SESSION_ID "
                "--phase PHASE' to supply the phase explicitly.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            from taskmanagement import WriteVerificationError as _WVE
        except ImportError:
            _WVE = None
        try:
            result = write_phase_marker(session_id, event, phase)
            print(json.dumps(result, indent=2, default=str))
        except Exception as e:
            if _WVE is not None and isinstance(e, _WVE):
                print(f"✗ phase-marker verify failed: {e}", file=sys.stderr)
                sys.exit(1)
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "phase-complete":
        # Usage: phase-complete SESSION_ID [--oqs-cleared --auth TOKEN]
        if len(sys.argv) < 3:
            print(
                "Usage: phase-complete SESSION_ID [--oqs-cleared --auth TOKEN]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        oqs_cleared = False
        auth_token = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--oqs-cleared":
                oqs_cleared = True; i += 1
            elif sys.argv[i] == "--auth" and i + 1 < len(sys.argv):
                auth_token = sys.argv[i + 1]; i += 2
            else:
                i += 1
        try:
            result = phase_complete_cmd(session_id, oqs_cleared, auth_token)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "topic-orient":
        # Usage: topic-orient SESSION_ID [--prompt-body PATH] [--cwd PATH]
        # Read-only inference primitive. Always exits 0 (verdict carries the
        # signal; "new" is information, not error).
        if len(sys.argv) < 3:
            print(
                "Usage: topic-orient SESSION_ID [--prompt-body PATH] [--cwd PATH]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        prompt_body_path = None
        cwd_arg = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--prompt-body" and i + 1 < len(sys.argv):
                prompt_body_path = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--cwd" and i + 1 < len(sys.argv):
                cwd_arg = sys.argv[i + 1]; i += 2
            else:
                i += 1
        prompt_body = None
        if prompt_body_path:
            try:
                if prompt_body_path == "-" or prompt_body_path == "/dev/stdin":
                    prompt_body = sys.stdin.read()
                else:
                    prompt_body = Path(prompt_body_path).read_text(encoding="utf-8")
            except OSError as e:
                # Non-blocking on read failures — emit empty body and note in
                # evidence via downstream call (caller can choose to log).
                print(f"warning: prompt-body read failed: {e}", file=sys.stderr)
                prompt_body = None
        result = topic_orient(session_id, prompt_body=prompt_body, cwd=cwd_arg)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    elif cmd == "auto-bind":
        # Usage: auto-bind SESSION_ID [--prompt-body PATH] [--cwd PATH]
        # M13 (S10): bind an unbound session to its named topic from a
        # deterministic source (worktree name / a `_THOUGHT` ref in the prompt
        # body). Refuse-rather-than-guess (E26): binds only when the resolved
        # slug matches an existing on-disk topic-state. Always exits 0 — the
        # `status` field carries the signal ("bound"/"already_bound"/"no_match"/
        # "unresolvable"/"error"); a non-bind is information, not an error.
        if len(sys.argv) < 3:
            print(
                "Usage: auto-bind SESSION_ID [--prompt-body PATH] [--cwd PATH]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        prompt_body_path = None
        cwd_arg = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--prompt-body" and i + 1 < len(sys.argv):
                prompt_body_path = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--cwd" and i + 1 < len(sys.argv):
                cwd_arg = sys.argv[i + 1]; i += 2
            else:
                i += 1
        prompt_body = None
        if prompt_body_path:
            try:
                if prompt_body_path in ("-", "/dev/stdin"):
                    prompt_body = sys.stdin.read()
                else:
                    prompt_body = Path(prompt_body_path).read_text(encoding="utf-8")
            except OSError as e:
                print(f"warning: prompt-body read failed: {e}", file=sys.stderr)
                prompt_body = None
        result = auto_bind_unbound_session(
            session_id, prompt_body=prompt_body, cwd=cwd_arg)
        print(json.dumps(result, indent=2, default=str))
        sys.exit(0)

    elif cmd == "phase-backfill":
        # Usage: phase-backfill SESSION_ID --to PHASE --evidence JSON
        #        [--source-summary TEXT]
        if len(sys.argv) < 3:
            print(
                "Usage: phase-backfill SESSION_ID --to PHASE --evidence JSON "
                "[--source-summary TEXT]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        to_phase = None
        evidence_raw = None
        source_summary = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--to" and i + 1 < len(sys.argv):
                to_phase = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--evidence" and i + 1 < len(sys.argv):
                evidence_raw = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--source-summary" and i + 1 < len(sys.argv):
                source_summary = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if to_phase is None or evidence_raw is None:
            print(
                "Usage: phase-backfill SESSION_ID --to PHASE --evidence JSON "
                "[--source-summary TEXT]",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            evidence_payload = json.loads(evidence_raw)
        except json.JSONDecodeError as e:
            print(f"✗ Invalid --evidence JSON: {e}", file=sys.stderr)
            sys.exit(2)
        try:
            result = phase_backfill(session_id, to_phase, evidence_payload,
                                    source_summary=source_summary)
            print(json.dumps(result, indent=2, default=str))
            sys.exit(0)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "decision-checkpoint":
        # Usage: decision-checkpoint SESSION_ID open DESCRIPTION
        #        decision-checkpoint SESSION_ID resolve --auth TOKEN
        if len(sys.argv) < 4:
            print(
                "Usage: decision-checkpoint SESSION_ID open DESCRIPTION\n"
                "       decision-checkpoint SESSION_ID resolve --auth TOKEN",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        subcommand = sys.argv[3]
        if subcommand == "open":
            if len(sys.argv) < 5:
                print(
                    "Usage: decision-checkpoint SESSION_ID open DESCRIPTION",
                    file=sys.stderr,
                )
                sys.exit(2)
            description = sys.argv[4]
            try:
                result = decision_checkpoint_open(session_id, description)
                print(json.dumps(result, indent=2))
            except ValueError as e:
                print(f"✗ {e}", file=sys.stderr)
                sys.exit(1)
        elif subcommand == "resolve":
            auth_token = None
            i = 4
            while i < len(sys.argv):
                if sys.argv[i] == "--auth" and i + 1 < len(sys.argv):
                    auth_token = sys.argv[i + 1]; i += 2
                else:
                    i += 1
            try:
                result = decision_checkpoint_resolve(session_id, auth_token)
                print(json.dumps(result, indent=2))
            except ValueError as e:
                print(f"✗ {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print(
                f"Unknown decision-checkpoint subcommand: {subcommand}",
                file=sys.stderr,
            )
            sys.exit(2)

    elif cmd == "register-session-todo":
        # Usage: register-session-todo SESSION_ID --todo-text TEXT --bucket BUCKET [--todo-file FILE]
        session_id = sys.argv[2] if len(sys.argv) > 2 else None
        if not session_id:
            print(
                "Usage: register-session-todo SESSION_ID --todo-text TEXT --bucket BUCKET [--todo-file FILE]",
                file=sys.stderr,
            )
            sys.exit(2)
        todo_text_arg = None
        bucket_arg = None
        todo_file_arg = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--todo-text" and i + 1 < len(sys.argv):
                todo_text_arg = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--bucket" and i + 1 < len(sys.argv):
                bucket_arg = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--todo-file" and i + 1 < len(sys.argv):
                todo_file_arg = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if not todo_text_arg or not bucket_arg:
            print("✗ --todo-text and --bucket are required", file=sys.stderr)
            sys.exit(2)
        try:
            result = register_session_todo(session_id, todo_text_arg, bucket_arg, todo_file_arg)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "arm-audit-capture":
        # Usage: arm-audit-capture SESSION_ID
        if len(sys.argv) < 3:
            print("Usage: arm-audit-capture SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        result = arm_audit_capture(session_id)
        print(json.dumps(result, indent=2))

    elif cmd == "confirm-audit-capture":
        # Usage: confirm-audit-capture SESSION_ID
        if len(sys.argv) < 3:
            print("Usage: confirm-audit-capture SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        try:
            result = confirm_audit_capture(session_id)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "is-audit-capture-confirmed":
        # Usage: is-audit-capture-confirmed SESSION_ID
        # Exit 0 = confirmed; 1 = armed-but-unconfirmed; 2 = not armed
        if len(sys.argv) < 3:
            print("Usage: is-audit-capture-confirmed SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        status = is_audit_capture_confirmed(session_id)
        if status is None:
            # Not armed — no capture in progress
            sys.exit(2)
        elif status:
            sys.exit(0)
        else:
            sys.exit(1)

    # ------------------------------------------------------------------
    # Clarification step commands (Slice D)
    # ------------------------------------------------------------------

    elif cmd == "clar-advance":
        # Usage: clar-advance SESSION_ID STEP_NUM '{payload}' [--from PATH]
        if len(sys.argv) < 5:
            print(
                "Usage: clar-advance SESSION_ID STEP_NUM '{payload}' [--from PATH]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        raw_step = sys.argv[3]
        if raw_step == "1b" or raw_step in CLARIFICATION_STEP7_SECTIONS:
            step_num = raw_step
        else:
            try:
                step_num = int(raw_step)
            except ValueError:
                print(
                    f"✗ STEP_NUM must be an integer, '1b', or a section sub-step "
                    f"({', '.join(CLARIFICATION_STEP7_SECTIONS)}) (got {raw_step!r}).",
                    file=sys.stderr,
                )
                sys.exit(2)
        try:
            payload = json.loads(sys.argv[4])
        except json.JSONDecodeError as e:
            print(f"✗ Invalid payload JSON: {e}", file=sys.stderr)
            sys.exit(2)
        resume_source = None
        if "--from" in sys.argv[5:]:
            i = sys.argv.index("--from", 5)
            if i + 1 >= len(sys.argv):
                print("✗ --from requires a PATH argument.", file=sys.stderr)
                sys.exit(2)
            resume_source = sys.argv[i + 1]
        try:
            result = clar_advance(
                session_id, step_num, payload, resume_source=resume_source
            )
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "clar-set-cadence":
        # Usage: clar-set-cadence SESSION_ID {full_pass|section_by_section}
        if len(sys.argv) < 4:
            print(
                "Usage: clar-set-cadence SESSION_ID {full_pass|section_by_section}",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = sys.argv[2]
        cadence = sys.argv[3]
        try:
            result = clar_set_cadence(session_id, cadence)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "clar-status":
        # Usage: clar-status SESSION_ID
        if len(sys.argv) < 3:
            print("Usage: clar-status SESSION_ID", file=sys.stderr)
            sys.exit(2)
        session_id = sys.argv[2]
        result = clar_status(session_id)
        print(json.dumps(result, indent=2))

    elif cmd == "clar-rewind":
        # Usage: clar-rewind SESSION_ID --to STEP_NUM
        if len(sys.argv) < 5 or sys.argv[3] != "--to":
            print(
                "Usage: clar-rewind SESSION_ID --to STEP_NUM", file=sys.stderr
            )
            sys.exit(2)
        session_id = sys.argv[2]
        try:
            to_step = int(sys.argv[4])
        except ValueError:
            print(
                f"✗ STEP_NUM must be an integer (got {sys.argv[4]!r}).",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            result = clar_rewind(session_id, to_step)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    # ------------------------------------------------------------------
    # Slice I (S-I-Impl-1) — Phase 3 `/plan` foundation CLIs
    # ------------------------------------------------------------------

    elif cmd == "plan-mode-init":
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print(
                "Usage: plan-mode-init SESSION_ID [--mode A|B|C] "
                "[--thought-path PATH]"
            )
            sys.exit(0)
        if len(args) < 1:
            print(
                "Usage: plan-mode-init SESSION_ID [--mode A|B|C] "
                "[--thought-path PATH]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = args[0]
        mode = None
        thought_path = None
        if "--mode" in args:
            i = args.index("--mode")
            if i + 1 >= len(args):
                print("✗ --mode requires a value (A|B|C).", file=sys.stderr)
                sys.exit(2)
            mode = args[i + 1]
        if "--thought-path" in args:
            i = args.index("--thought-path")
            if i + 1 >= len(args):
                print("✗ --thought-path requires a PATH.", file=sys.stderr)
                sys.exit(2)
            thought_path = args[i + 1]
        try:
            result = plan_mode_init(
                session_id, mode=mode, thought_path=thought_path
            )
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "link-plan-to-thought":
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print("Usage: link-plan-to-thought SESSION_ID PLAN_FILE_PATH")
            sys.exit(0)
        if len(args) < 2:
            print(
                "Usage: link-plan-to-thought SESSION_ID PLAN_FILE_PATH",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = args[0]
        plan_path = args[1]
        try:
            result = link_plan_to_thought(session_id, plan_path)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "plan-exit-ack":
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print("Usage: plan-exit-ack SESSION_ID")
            sys.exit(0)
        if len(args) < 1:
            print("Usage: plan-exit-ack SESSION_ID", file=sys.stderr)
            sys.exit(2)
        result = plan_exit_ack(args[0])
        print(json.dumps(result, indent=2))

    elif cmd == "relocate-plan-after-approval":
        args = sys.argv[2:]
        dest_slug = None
        positional = []
        i = 0
        while i < len(args):
            if args[i] == "--dest-slug" and i + 1 < len(args):
                dest_slug = args[i + 1]
                i += 2
            elif args[i] in ("--help", "-h"):
                print(
                    "Usage: relocate-plan-after-approval SESSION_ID PLAN_FILE_PATH "
                    "[--dest-slug SLUG]"
                )
                sys.exit(0)
            else:
                positional.append(args[i])
                i += 1
        if len(positional) < 2:
            print(
                "Usage: relocate-plan-after-approval SESSION_ID PLAN_FILE_PATH "
                "[--dest-slug SLUG]",
                file=sys.stderr,
            )
            sys.exit(2)
        try:
            result = relocate_plan_after_approval(
                positional[0], positional[1], dest_slug=dest_slug
            )
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "factcheck-plan-step":
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print(
                "Usage: factcheck-plan-step GATE_ID PLAN_FILE_PATH "
                "[--round N] [--max-rounds M] [--checkers-json FILE|-]"
            )
            print("  GATE_ID ∈ {0A, 0B2, 0C, 0D, 0E}")
            print("  Captured isolated-checker outputs come from --checkers-json")
            print("  (a JSON list of {model, verdict}) or, if omitted, stdin.")
            print("  The verdict is computed by the shared engine from those")
            print("  outputs — no producer-supplied verdict is accepted.")
            sys.exit(0)
        if len(args) < 2:
            print(
                "Usage: factcheck-plan-step GATE_ID PLAN_FILE_PATH "
                "[--round N] [--max-rounds M] [--checkers-json FILE|-]",
                file=sys.stderr,
            )
            sys.exit(2)
        gate_id = args[0]
        plan_path = args[1]

        def _int_flag(name, default):
            if name in args:
                i = args.index(name)
                if i + 1 >= len(args):
                    print(f"✗ {name} requires an integer.", file=sys.stderr)
                    sys.exit(2)
                try:
                    return int(args[i + 1])
                except ValueError:
                    print(
                        f"✗ {name} must be an integer (got {args[i + 1]!r}).",
                        file=sys.stderr,
                    )
                    sys.exit(2)
            return default

        def _str_flag(name, default):
            if name in args:
                i = args.index(name)
                if i + 1 >= len(args):
                    print(f"✗ {name} requires a value.", file=sys.stderr)
                    sys.exit(2)
                return args[i + 1]
            return default

        round_num = _int_flag("--round", 1)
        max_rounds = _int_flag("--max-rounds", 3)
        checkers_json = _str_flag("--checkers-json", None)
        try:
            checker_verdicts = _read_captured_checkers(checkers_json)
            result = factcheck_plan_step(
                gate_id, plan_path, checker_verdicts,
                round_num=round_num, max_rounds=max_rounds,
            )
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "factcheck-plan-coherency":
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print(
                "Usage: factcheck-plan-coherency SESSION_ID PLAN_FILE_PATH "
                "[--round N] [--max-rounds M] [--claims-checked N] "
                "[--checkers-json FILE|-]"
            )
            print("  Whole-plan / final coherency (Gate 0G).")
            print("  Captured isolated-checker outputs come from --checkers-json")
            print("  (a JSON list of {model, verdict}) or, if omitted, stdin.")
            print("  The verdict is computed by the shared engine — no")
            print("  producer-supplied verdict is accepted.")
            sys.exit(0)
        if len(args) < 2:
            print(
                "Usage: factcheck-plan-coherency SESSION_ID PLAN_FILE_PATH "
                "[--round N] [--max-rounds M] [--claims-checked N] "
                "[--checkers-json FILE|-]",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = args[0]
        plan_path = args[1]

        def _int_flag_c(name, default):
            if name in args:
                i = args.index(name)
                if i + 1 >= len(args):
                    print(f"✗ {name} requires an integer.", file=sys.stderr)
                    sys.exit(2)
                try:
                    return int(args[i + 1])
                except ValueError:
                    print(
                        f"✗ {name} must be an integer (got {args[i + 1]!r}).",
                        file=sys.stderr,
                    )
                    sys.exit(2)
            return default

        round_num = _int_flag_c("--round", 1)
        max_rounds = _int_flag_c("--max-rounds", 3)
        claims_checked = _int_flag_c("--claims-checked", None)
        checkers_json = None
        if "--checkers-json" in args:
            i = args.index("--checkers-json")
            if i + 1 >= len(args):
                print("✗ --checkers-json requires a value.", file=sys.stderr)
                sys.exit(2)
            checkers_json = args[i + 1]
        try:
            checker_verdicts = _read_captured_checkers(checkers_json)
            result = factcheck_plan_coherency(
                session_id,
                plan_path,
                checker_verdicts,
                round_num=round_num,
                max_rounds=max_rounds,
                claims_checked=claims_checked,
            )
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "plan-receipt-dir":
        # Resolver verb: print the canonical receipt DIRECTORY for one (plan-file,
        # gate). The single shared locus consumed by check-plan-gates.sh (S2) so the
        # shell reader never re-implements the path hash. Optional --round appends the
        # R<N>.md filename.
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print("Usage: plan-receipt-dir PLAN_FILE_PATH GATE_ID [--round N]")
            print("  GATE_ID ∈ {0A, 0B2, 0C, 0D, 0E, 0G}")
            print("  Prints the receipt dir; with --round N prints the R<N>.md path.")
            sys.exit(0)
        if len(args) < 2:
            print(
                "Usage: plan-receipt-dir PLAN_FILE_PATH GATE_ID [--round N]",
                file=sys.stderr,
            )
            sys.exit(2)
        plan_path = args[0]
        gate_id = args[1]
        round_num = None
        if "--round" in args:
            i = args.index("--round")
            if i + 1 >= len(args):
                print("✗ --round requires an integer.", file=sys.stderr)
                sys.exit(2)
            try:
                round_num = int(args[i + 1])
            except ValueError:
                print(
                    f"✗ --round must be an integer (got {args[i + 1]!r}).",
                    file=sys.stderr,
                )
                sys.exit(2)
        try:
            rdir = _plan_receipt_dir(plan_path, gate_id)
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)
        print(str(rdir / f"R{round_num}.md") if round_num is not None else str(rdir))

    elif cmd == "plan-override":
        # Record an EXPLICIT operator 'proceed as is' override for one plan gate.
        # Admitted by check-plan-gates.sh in place of a converged PASS receipt — the
        # deliberate, RECORDED escape from the fail-closed gate (never author-typed).
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print("Usage: plan-override GATE_ID PLAN_FILE_PATH --reason TEXT "
                  "[--session SID]")
            print("  GATE_ID ∈ {0A, 0B2, 0C, 0D, 0E, 0G}")
            sys.exit(0)
        if len(args) < 2:
            print(
                "Usage: plan-override GATE_ID PLAN_FILE_PATH --reason TEXT "
                "[--session SID]",
                file=sys.stderr,
            )
            sys.exit(2)
        gate_id = args[0]
        plan_path = args[1]
        reason = None
        session_id = None
        if "--reason" in args:
            i = args.index("--reason")
            if i + 1 >= len(args):
                print("✗ --reason requires a value.", file=sys.stderr)
                sys.exit(2)
            reason = args[i + 1]
        if "--session" in args:
            i = args.index("--session")
            if i + 1 >= len(args):
                print("✗ --session requires a value.", file=sys.stderr)
                sys.exit(2)
            session_id = args[i + 1]
        try:
            result = record_plan_override(gate_id, plan_path, reason, session_id=session_id)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    elif cmd == "clear-post-plan-choice":
        args = sys.argv[2:]
        if "--help" in args or "-h" in args:
            print("Usage: clear-post-plan-choice SESSION_ID")
            sys.exit(0)
        if len(args) < 1:
            print(
                "Usage: clear-post-plan-choice SESSION_ID",
                file=sys.stderr,
            )
            sys.exit(2)
        session_id = args[0]
        try:
            result = clear_post_plan_choice(session_id)
            print(json.dumps(result, indent=2))
        except ValueError as e:
            print(f"✗ {e}", file=sys.stderr)
            sys.exit(1)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------





    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

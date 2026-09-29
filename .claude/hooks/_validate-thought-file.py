#!/usr/bin/env python3
"""Structural validator for *_THOUGHT.md files.

Run LIVE by permission-plan-gate.sh at ExitPlanMode on the spine the plan names —
that is the blocking consumer. Also called by verify-thought-file.sh, which writes
an advisory `<slug>_THOUGHT_check.md` sidecar on every Write/Edit of a spine;
plan approval does not read that sidecar. Checks:
  1. All 4 top-level sections present in order: # Idea, # Discovery, # Solution Design, # Implementation Details
  2. ## Problem present under # Idea and non-empty
  3. All 4 DISCOVERY_LOCKED_FIELDS present under # Discovery and non-empty
  4. # Solution Design and # Implementation Details may be empty or contain only placeholder lines
  5. TODO entry referencing this Thoughts file exists
  Soft warning (returncode 0): ## Q&A entry authored after Step-9 lock marker lacks (origin: ...) tag

The former `Classification` check (a `classification` field in the topic-state
file) was removed: Slice F retired that field, so the check could never pass and
made every verdict FAIL. With it went the two slug positionals only it consumed.

Usage:
  python3 _validate-thought-file.py THOUGHT_FILE_PATH

Extra positionals are ignored (a caller still passing the old PROJECT_SLUG /
TOPIC_SLUG arguments mid-deploy does not crash).

Exits 0 = PASS (or PASS with warnings), 1 = FAIL with diagnostics on stdout (structured format).
"""

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pre_plan_gates import (
    PROJECTS_ROOT,
    IDEA_SECTIONS,
    DISCOVERY_LOCKED_FIELDS,
    DISCOVERY_MUTABLE_SUBSECTIONS,
    LOCK_MARKER_RE,
    discovery_section_body,
    extract_heading_body,
    find_heading,
    # A7/A8 (S2) — imported, never re-declared. Both are single-definition
    # predicates shared with `validate_discovery_locked_fields`; a local copy
    # here would be the one-idea-in-two-files drift this topic exists to close.
    has_metric_line,
    is_effectively_empty,
)

TOP_LEVEL_SECTIONS = ["# Idea", "# Discovery", "# Solution Design", "# Implementation Details"]
PLACEHOLDER_PATTERNS = [
    "*(populated by /solution-design",
    "*(populated by /plan",
    "*(populated by Step 1b",
    "*(populated by Steps",
]
# `LOCK_MARKER_RE` is imported (A8), not re-declared. The local two-token copy
# that used to live here was the second of two, and both went inert on every
# clarification-v2 spine — which emits three tokens.
QA_ORIGIN_RE = re.compile(r"^Q \(origin: ")


def _section_body(text, header, next_headers):
    """Extract the body between `header` and the first of `next_headers` (or EOF).

    Anchored (A1): `text.find(header)` matched a top-level heading name anywhere,
    including at offset 1 of a deeper heading — `## Discovery` contains
    `# Discovery`. Both the locator and the terminators now use the shared
    start-of-line predicate."""
    idx = find_heading(text, header)
    if idx == -1:
        return None
    content_start = idx + len(header)
    next_idx = len(text)
    for h in next_headers:
        pos = find_heading(text, h, content_start)
        if pos != -1 and pos < next_idx:
            next_idx = pos
    return text[content_start:next_idx]


def _is_placeholder_or_empty(body):
    stripped = body.strip()
    if not stripped:
        return True
    for p in PLACEHOLDER_PATTERNS:
        if stripped.startswith(p):
            return True
    return False


def _near_miss_heading_line(discovery_body, field):
    """A2 — RELOCATED from `_discovery_lock_check._field_status`, not re-authored.

    That function answered "why did extraction return None" with four states;
    only `unmatched` had a consumer, and that consumer (the A4 guard) is deleted
    by A3. The useful half moves here, beside where it now fires, stripped of the
    `matched`/`absent`/`no-section` arms it no longer needs. No stub is left
    behind in `_discovery_lock_check.py`: one idea in one place is the whole
    thesis of the change this belongs to, and two copies free to drift would
    contradict it.

    Returns `(line_number, offending_line)` when some line of `discovery_body` is
    a line-start, hash-prefixed heading naming `field` that the anchored
    predicate rejects — or None.

    This is the SECOND conjunct only. It returns a match for a READABLE heading
    too, so the caller must already know `extract_heading_body(...) is None`;
    called alone it would warn about every well-formed spine. `_field_status`
    had the same shape — it tested `extract_heading_body(...) is not None` first
    and only then fell through to the name check.

    Line-start is what excludes a prose mention: `see ## Metrics below` is not a
    heading and must not warn. Marker adjacency is deliberately NOT part of this
    predicate — that approach was refuted for the *guard*, where a false positive
    cost a refusal; here nothing is defended, so an over-broad trigger costs one
    spurious warning line and never an edit.
    """
    bare_name = re.escape(field.lstrip("#").strip())
    pattern = re.compile(rf"^[ \t]*#{{1,6}}[ \t]*{bare_name}(?=[ \t:]|$)")
    for lineno, line in enumerate(discovery_body.splitlines(), 1):
        if pattern.match(line):
            return lineno, line.strip()
    return None


def check_sections(text):
    """Returns (results_dict, warnings_list).

    results_dict: section_label → (passed: bool, message: str)
    warnings_list: list of warning strings (returncode 0, but printed)
    """
    results = {}
    warnings = []

    # 1. Top-level section order
    # Anchored (A1): unanchored, a prose mention of a section name — or the
    # `# Discovery` inside a `## Discovery` subheading — set `positions[...]`
    # to the wrong offset and produced a visible, false "Out of order" verdict
    # against a perfectly well-ordered spine.
    positions = {}
    for section in TOP_LEVEL_SECTIONS:
        idx = find_heading(text, section)
        positions[section] = idx
        if idx == -1:
            results[section] = (False, "Missing section")

    in_order = True
    prev_pos = -1
    for section in TOP_LEVEL_SECTIONS:
        if positions[section] == -1:
            in_order = False
            break
        if positions[section] <= prev_pos:
            in_order = False
            results[section] = (False, f"Out of order (found at {positions[section]}, expected after {prev_pos})")
            break
        prev_pos = positions[section]
        if section not in results:
            results[section] = (True, "")

    # 2. ## Problem under # Idea
    idea_body = _section_body(text, "# Idea", ["# Discovery", "# Solution Design", "# Implementation Details"])
    if idea_body is not None:
        problem_found = False
        for idea_sec in IDEA_SECTIONS:
            # IDEA_SECTIONS stays its own sibling list — this is an `# Idea`
            # site, not a Discovery one, and the two constant lists must not be
            # merged even though they now share one predicate.
            content = extract_heading_body(idea_body, idea_sec, IDEA_SECTIONS)
            if content is None:
                results[f"# Idea::{idea_sec}"] = (False, f"Missing {idea_sec} under # Idea")
                continue
            # A7 — the shared emptiness predicate. Order is unchanged: emptiness
            # first, placeholder second.
            #
            # An earlier version of this comment claimed that stripping chrome
            # makes the placeholder branch REACHABLE on a body that began with a
            # lock marker. It does not, and the claim is removed rather than
            # reworded. The placeholder check below runs on `content.strip()` — a
            # plain strip — so a marker-then-placeholder body still starts with
            # `<!--` and is not matched, before or after A7. For a marker-ONLY
            # body this change makes that branch LESS reached: the emptiness test
            # now catches it and `continue`s past the placeholder check the old
            # code fell through to. (The plan's A9 guard rail stated the same
            # thing and is now annotated there too — on a third pass, after a
            # checker pointed out that this very comment recorded the plan as
            # wrong and left it uncorrected.)
            if is_effectively_empty(content):
                results[f"# Idea::{idea_sec}"] = (False, f"Empty {idea_sec}")
                continue
            content = content.strip()
            # Accept placeholder during step-1b
            if any(content.startswith(p) for p in PLACEHOLDER_PATTERNS):
                results[f"# Idea::{idea_sec}"] = (True, "placeholder (Step 1b pending)")
            else:
                results[f"# Idea::{idea_sec}"] = (True, "")
            problem_found = True
        if not problem_found:
            results["# Idea::## Problem"] = (False, "Missing ## Problem under # Idea")

    # 3. DISCOVERY_LOCKED_FIELDS under # Discovery
    all_secs = DISCOVERY_LOCKED_FIELDS + DISCOVERY_MUTABLE_SUBSECTIONS
    # The canonical Discovery scoping rule (A1), not this module's own. The
    # local `_section_body` bounded the section differently — substring start,
    # ending only at two named headings — so this validator could disagree with
    # the hash and the lock about where a locked field ends.
    discovery_body = discovery_section_body(text)
    if discovery_body is not None:
        for field in DISCOVERY_LOCKED_FIELDS:
            content = extract_heading_body(discovery_body, field, all_secs)
            if content is None:
                results[f"# Discovery::{field}"] = (False, f"Missing {field}")
                # A4 — near-miss advisory. The field is absent as far as the
                # anchored predicate is concerned, but the name may still be
                # sitting in Discovery as a heading the predicate cannot read
                # (`### Metrics`, `## Metrics:`, an indented heading). Say so,
                # by line, through the existing non-blocking channel. This
                # REPLACES the refusal `_discovery_lock_check.py` used to raise
                # on the same evidence (A3): reporting costs a spurious warning
                # line at worst, where refusing cost a deadlock — on 2026-08-29
                # it left a spine unfixable from inside the tool.
                near = _near_miss_heading_line(discovery_body, field)
                if near is not None:
                    lineno, offending = near
                    # The message states what it KNOWS and offers both readings. It
                    # must not assert that this line IS the locked field, and must
                    # not issue a bare "fix the heading" imperative.
                    #
                    # Why (validated 2026-09-20): the scan covers the whole
                    # Discovery body, including the MUTABLE `## Scope` and `## Q&A`
                    # subsections, so a Q&A group heading legitimately named after a
                    # locked field matches here. The previous wording told its
                    # author to "fix the heading to have the field defended again" —
                    # and following that advice would rename a correct Q&A heading to
                    # `## Metrics`, which then binds as the locked field and
                    # truncates the Q&A body for every downstream
                    # `extract_heading_body` caller. The advice did not merely
                    # mislead; acting on it created a real defect.
                    #
                    # Narrowing the SCAN was tried on paper and rejected: the only
                    # way to exclude a mutable subsection is to compute its extent
                    # with `extract_heading_body` — the same predicate whose failure
                    # got us here — and that extent balloons precisely when a locked
                    # field is missing, because the missing field is what would have
                    # terminated it. On the motivating spine it would have excluded
                    # ~95% of Discovery and silenced this advisory on the very
                    # document it exists for. Wording is the safe surface; scope is
                    # not.
                    bare = field.lstrip("#").strip()
                    warnings.append(
                        f"WARNING: {field} is missing. A heading named `{bare}` "
                        f"does appear at line {lineno} of `# Discovery`: "
                        f"{offending!r}. If that was meant to be the locked field, "
                        f"its heading must be `{field}` (or `{field} (note)`) — a "
                        f"locked heading starts its own line and is followed by "
                        f"whitespace or a line break, so `{field}:` and "
                        f"`#{field}` are not read. If it is a group heading inside "
                        f"`## Q&A` or `## Scope`, nothing needs changing. The edit "
                        f"is allowed either way."
                    )
                continue
            # A7 — the shared emptiness predicate, third and last consumption site.
            if is_effectively_empty(content):
                results[f"# Discovery::{field}"] = (False, f"Empty {field}")
                continue
            content = content.strip()
            if any(content.startswith(p) for p in PLACEHOLDER_PATTERNS):
                results[f"# Discovery::{field}"] = (True, "placeholder (Steps 7-9 pending)")
            elif field == "## Metrics" and not has_metric_line(content):
                # A8 — the metric-line rule RELOCATED to the blocking path. This
                # is the change with teeth: `results` is verdict-bearing, so a
                # Metrics section that says nothing now fails this validator, which
                # permission-plan-gate.sh runs live on the spine at ExitPlanMode —
                # the plan exit is refused with this finding in its message. Before
                # this, the rule lived only in `validate_discovery_locked_fields`,
                # which no blocking consumer reads — Checker B carried no OMTM
                # rule at all, which is why relocating it is ADDITIVE here and
                # nothing that passes today starts failing.
                #
                # No placeholder exception: the branch above already returns for a
                # placeholder body, and a Gate-0G checker established that the
                # "mid-clarification spine carries a placeholder Metrics" scenario
                # does not occur — all six live placeholder occurrences sit
                # directly under `# Discovery` with no field sub-heading, so
                # before Step 8 there is no `## Metrics` heading to reach here.
                results[f"# Discovery::{field}"] = (False, f"Missing OMTM line in {field}")
            else:
                results[f"# Discovery::{field}"] = (True, "")

        # 4. Q&A origin warning — after Step-9 lock marker
        lock_match = LOCK_MARKER_RE.search(discovery_body)
        if lock_match:
            post_lock = discovery_body[lock_match.end():]
            # Converted too (A2). The plan allowed leaving this single-job
            # presence check with a stated reason; converting is the same cost
            # and removes the last unanchored heading find in this file, so the
            # sweep has nothing left to account for. Note `post_lock` begins
            # mid-line (just after `-->`), and `^` also matches at offset 0 of
            # the string being searched — reachable only by the literal
            # `<!-- locked: … -->## Q&A`, which no spine writes.
            qa_idx = find_heading(post_lock, "## Q&A")
            if qa_idx != -1:
                qa_section = post_lock[qa_idx + len("## Q&A"):]
                for line in qa_section.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Q ") and not QA_ORIGIN_RE.match(stripped):
                        warnings.append(
                            f"WARNING: Q&A entry authored after Step-9 lock lacks (origin: ...) tag: {stripped[:80]}"
                        )

    # 5. Solution Design and Implementation Details: placeholder or empty is OK
    for section in ["# Solution Design", "# Implementation Details"]:
        body = _section_body(
            text,
            section,
            [s for s in TOP_LEVEL_SECTIONS if s != section],
        )
        if body is not None and section not in results:
            results[section] = (True, "")  # already marked present

    return results, warnings


def check_todo_entry(thought_file_path, project_dir):
    filename = Path(thought_file_path).name
    project_slug = filename.replace("_THOUGHT.md", "")
    search_terms = [filename, project_slug.replace("-", " "), project_slug]

    search_paths = [
        Path(project_dir) / "TODO.md",
        PROJECTS_ROOT / "TODO.md",
    ]

    for todo_path in search_paths:
        if not todo_path.exists():
            continue
        content = todo_path.read_text(encoding="utf-8")
        for term in search_terms:
            if term in content:
                return True, f"Found in {todo_path}"

    return False, f"Not found in TODO.md files (searched: {[str(p) for p in search_paths if p.exists()]})"


def main():
    if len(sys.argv) < 2:
        print("Usage: _validate-thought-file.py THOUGHT_FILE")
        sys.exit(1)

    # One positional. sys.argv[2:] is deliberately ignored: the old signature took
    # PROJECT_SLUG TOPIC_SLUG for the retired classification check, and a caller
    # still passing them (verify-thought-file.sh mid-the deploy step) must not crash.
    thought_file = sys.argv[1]
    project_dir = str(Path(thought_file).parent.parent)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if not Path(thought_file).exists():
        print(f"VERDICT: FAIL")
        print(f"CHECKED: {timestamp}")
        print(f"FILE: {thought_file}")
        print()
        print("## Checks")
        print(f"- FileExists: FAIL [File not found: {thought_file}]")
        sys.exit(1)

    text = Path(thought_file).read_text(encoding="utf-8")

    section_results, section_warnings = check_sections(text)
    todo_pass, todo_msg = check_todo_entry(thought_file, project_dir)

    section_failures = {k: v for k, v in section_results.items() if not v[0]}
    all_pass = not section_failures and todo_pass
    verdict = "PASS" if all_pass else "FAIL"

    total_checks = len(section_results) + 1
    passed_count = sum(1 for v in section_results.values() if v[0]) + int(todo_pass)

    print(f"VERDICT: {verdict}")
    print(f"CHECKED: {timestamp}")
    print(f"FILE: {thought_file}")
    print()
    print("## Checks")
    for label, (passed, msg) in section_results.items():
        status = "PASS" if passed else f"FAIL [{msg}]"
        if passed and msg:
            status = f"PASS ({msg})"
        print(f"- {label}: {status}")
    todo_status = "PASS" if todo_pass else f"FAIL [{todo_msg}]"
    print(f"- TODOEntry: {todo_status}")

    if section_warnings:
        print()
        print("## Warnings")
        for w in section_warnings:
            print(f"- {w}")

    print()
    print(f"## Summary")
    print(f"{passed_count}/{total_checks} checks passed; {verdict}")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""TODO management operations on TODO.md files.

Single script, subcommand structure. All deterministic work here; AI provides
judgment at specific points (grouping, non-dated bucket choice, completion
summary for complex items).

Subcommands:
  (default) read   - Scan vault, output prioritized summary (hook-invoked)
  deps             - Scan existing items for keyword overlaps; returns JSON
  add              - Write new item to appropriate section
  done             - Mark item [x] with today's date
  cleanup          - Move [x] items to Done section

Invoked by:
  - UserPromptSubmit hook (default read mode, JSON on stdin)
  - AI during add/done/cleanup workflows (CLI args, no stdin)
  - /close skill (cleanup subcommand)

Stdlib only.
"""

# =============================================================================
# Section 1: Constants
# =============================================================================

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

URGENCY_DAYS = 7
VAULT_ROOT_FALLBACK = Path(
    os.environ.get("CLAUDE_PROJECT_DIR", str(Path.cwd()))
)
MARKER_DIR = Path(os.path.expanduser("~/.claude/session-state"))
MARKER_TTL_HOURS = 24

ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
BOLD_SPAN_RE = re.compile(r"\*\*([^*]+?)\*\*")
# Words that signal a date is context (when/why surfaced), not a due date
CONTEXT_KEYWORDS = (
    "stale since", "since ", "surfaced", "validated", "diary",
    "last updated", "last review", "created", "logged",
)
# Provenance / reword verbs that mark a date as context (when the item was
# reframed/reopened/moved/etc.), NOT a deadline. Matched on WORD BOUNDARIES
# (a distinct check beside the CONTEXT_KEYWORDS substring loop) so a verb that
# is a substring of a common word cannot over-suppress a genuine deadline —
# e.g. "moved" must not match inside "removed" ("(feature removed, due ...)").
PROVENANCE_VERB_RE = re.compile(
    r"\b(?:reframed|reopened|amended|revised|reworked|superseded|moved|found)\b"
)
SECTION_RE = re.compile(
    r"^##\s+(Now|Next|Nearby|Nascent|Scheduled|Never|Done|Blocked|Waiting|Decision)\b",
    re.IGNORECASE,
)
SESSIONS_COUNTER_RE = re.compile(
    r"Sessions:\s*(\d+)\s*/\s*(\d+)\s*done\.?",
    re.IGNORECASE,
)
SUBSECTION_RE = re.compile(r"^###\s+(.+?)\s*$")
CLOSED_RE = re.compile(r"Status:\s*(CLOSED|HANDED OVER)", re.IGNORECASE)
OPEN_ITEM_RE = re.compile(r"^\s*-\s*\[\s*\]\s*(.*)$")
DONE_ITEM_RE = re.compile(r"^\s*-\s*\[\s*x\s*\]\s*(.*)$", re.IGNORECASE)
DONE_DATE_RE = re.compile(r"\*\*DONE\s+(\d{4}-\d{2}-\d{2})\.?\*\*", re.IGNORECASE)
TAG_RE = re.compile(r"^\[([A-Za-z][^\]]*)\]")
PROSE_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
    re.IGNORECASE,
)
EMPTY_PLACEHOLDER_RE = re.compile(r"^\s*_Empty[\s\S]*_\s*$")

BUCKET_HORIZONS = [
    ("NOW", 0, URGENCY_DAYS),
    ("NEXT", URGENCY_DAYS + 1, 30),
    ("NEARBY", 31, None),
]

# Active sections for cleanup (items get moved FROM here TO Done)
ACTIVE_SECTIONS = {"now", "next", "nearby", "nascent", "scheduled", "blocked"}

# Terminal sections — present in vocabulary but excluded from all active-work surfaces.
# Never = "Won't-Do" resting place (declined tasks stay [ ], not deleted, not completed).
# Done = completed items swept here by cleanup.
# Never must NOT appear in ACTIVE_SECTIONS.
TERMINAL_SECTIONS = {"done", "never"}


# =============================================================================
# Section 2: I/O layer
# =============================================================================

def _extract_due_date(text):
    """Extract a due date from item text.

    Heuristic — a date is a due date if it appears:
      1. Inside a bold span **...YYYY-MM-DD...** without a context keyword, OR
      2. Inside parens (YYYY-MM-DD) without a context keyword.

    Dates with context keywords ("surfaced", "since", "diary", etc.) or a
    provenance/reword verb ("reframed", "reopened", "moved", etc.) are
    references, not due dates. Bare dates outside bold/parens are treated
    as context by default.

    Returns ISO date string (YYYY-MM-DD) or None.
    """
    # 1. Bold spans
    for match in BOLD_SPAN_RE.finditer(text):
        span_lower = match.group(1).lower()
        if any(kw in span_lower for kw in CONTEXT_KEYWORDS) or PROVENANCE_VERB_RE.search(span_lower):
            continue
        date_match = ISO_DATE_RE.search(match.group(1))
        if date_match:
            return date_match.group(1)

    # 2. Parenthesized dates
    for match in re.finditer(r"\(([^)]*\d{4}-\d{2}-\d{2}[^)]*)\)", text):
        inside_lower = match.group(1).lower()
        if any(kw in inside_lower for kw in CONTEXT_KEYWORDS) or PROVENANCE_VERB_RE.search(inside_lower):
            continue
        date_match = ISO_DATE_RE.search(match.group(1))
        if date_match:
            return date_match.group(1)

    return None


def parse_todo_file(path):
    """Parse a TODO.md file into structured data.

    Returns dict with keys:
      closed: bool (file has "Status: CLOSED" or "HANDED OVER" in first 10 lines)
      sections: {section_name_lower: [line_numbers]}
      subsections: {section_name_lower: {subsection_title: [line_numbers]}}
      open_items: list of {line, text, section, subsection, iso_date, days_until, prose_date}
      done_items: list of {line, text, section, done_date}
      lines: list of raw lines (for reconstruction)
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    lines = content.split("\n")

    # Check closed flag in first 10 lines
    closed = any(CLOSED_RE.search(ln) for ln in lines[:10])

    sections = defaultdict(list)
    subsections = defaultdict(lambda: defaultdict(list))
    open_items = []
    done_items = []

    current_section = None
    current_subsection = None
    today = date.today()

    for i, line in enumerate(lines):
        sec_match = SECTION_RE.match(line)
        if sec_match:
            current_section = sec_match.group(1).lower()
            current_subsection = None
            sections[current_section].append(i)
            continue

        sub_match = SUBSECTION_RE.match(line)
        if sub_match and current_section:
            current_subsection = sub_match.group(1).strip()
            subsections[current_section][current_subsection].append(i)
            continue

        open_match = OPEN_ITEM_RE.match(line)
        if open_match and current_section:
            text = open_match.group(1)
            iso_date_str = _extract_due_date(text)
            iso_date = None
            days_until = None
            if iso_date_str:
                try:
                    iso_date = datetime.strptime(iso_date_str, "%Y-%m-%d").date()
                    days_until = (iso_date - today).days
                except ValueError:
                    pass
            prose_date = bool(PROSE_DATE_RE.search(text)) and iso_date is None
            open_items.append({
                "line": i,
                "text": text,
                "section": current_section,
                "subsection": current_subsection,
                "iso_date": iso_date.isoformat() if iso_date else None,
                "days_until": days_until,
                "prose_date": prose_date,
            })
            continue

        done_match = DONE_ITEM_RE.match(line)
        if done_match and current_section:
            text = done_match.group(1)
            done_date_match = DONE_DATE_RE.search(text)
            done_date = done_date_match.group(1) if done_date_match else None
            done_items.append({
                "line": i,
                "text": text,
                "section": current_section,
                "done_date": done_date,
            })

    # Derived subset: open items that are NOT in a terminal section.
    # All active-work surfaces (counts, overdue/urgent scan, dep-scan,
    # is_active_project) read this field — Never items never leak into
    # active views.
    active_open_items = [
        item for item in open_items if item["section"] not in TERMINAL_SECTIONS
    ]

    return {
        "closed": closed,
        "sections": dict(sections),
        "subsections": {k: dict(v) for k, v in subsections.items()},
        "open_items": open_items,
        "active_open_items": active_open_items,
        "done_items": done_items,
        "lines": lines,
    }


def write_todo_file_atomic(path, new_content):
    """Write content atomically via tempfile + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    ) as tmp:
        tmp.write(new_content)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
    _record_ledger_write(path)


def _record_ledger_write(path):
    """Record a code-layer write to this session's file ledger (A5).

    `track-session-files.sh` only sees Write/Edit TOOL calls, so every tracked
    file written through code — this module included — was invisible to the
    ledger, and therefore to any publish surface that compiles its declared
    scope from it. That is gap G4.

    Lazy, guarded, and silent on failure by design. These modules also run under
    pytest, from standalone CLI invocations, and from background jobs where no
    session exists; `record_write` itself never raises, and this wrapper extends
    the same guarantee to the import. An unrecorded write is simply not declared,
    so the file stays dirty and uncommitted rather than being swept into another
    session's commit — the safe direction.
    """
    try:
        import sys as _sys, os as _os
        _hooks = _os.path.dirname(_os.path.abspath(__file__))
        if _hooks not in _sys.path:
            _sys.path.insert(0, _hooks)
        from commit_scope import record_write as _rw
        _rw(path)
    except Exception:
        pass


# =============================================================================
# Section 3: Domain layer
# =============================================================================

def find_vault_root(cwd):
    """Find vault root through the ONE main-pinned resolver (S2/A1 —
    bookkeeping_resolver). Main-pinned: a session inside a topic worktree
    resolves main's shared copy, never the worktree's frozen (and, post-A3,
    sparse-excluded) one. Behavior-preserving OUTSIDE a worktree (identical to
    the former `git rev-parse --show-toplevel`). Falls back to the constant on
    any git/module failure."""
    try:
        import bookkeeping_resolver
        root = bookkeeping_resolver.main_checkout(cwd)
        if root is not None:
            return Path(root)
    except Exception:
        pass
    # Fallback (resolver module unavailable) — the original show-toplevel path.
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass
    return VAULT_ROOT_FALLBACK


def is_active_project(parsed):
    """Active = not closed AND has at least one open item in an active section.

    A project whose only open items are in terminal sections (Never / Done)
    is NOT considered active — declined items don't keep a project alive.
    """
    if parsed is None:
        return False
    if parsed["closed"]:
        return False
    return len(parsed["active_open_items"]) > 0


def classify_scope(cwd, project_path):
    """Return 'current' if project contains cwd (or vice versa), else 'other'."""
    try:
        cwd_resolved = Path(cwd).resolve()
        proj_resolved = project_path.resolve()
        # cwd is inside project dir (project TODO owns cwd)
        if str(cwd_resolved).startswith(str(proj_resolved) + os.sep):
            return "current"
        if cwd_resolved == proj_resolved:
            return "current"
        # cwd is ABOVE project (root-level start): all projects are "current"
        if str(proj_resolved).startswith(str(cwd_resolved) + os.sep):
            return "current"
    except (OSError, ValueError):
        pass
    return "other"


def _canonical_project_or_none(todo_path, vault_root):
    """Delegate the Root-vs-leaf DECISION to the single canonical locus
    (pre_plan_gates.canonical_project_for_spine), so this display name can never
    disagree with the identity state key on 'is this Root?' (S5/A5 convergence —
    the /assess coherence finding). Fail-safe: any import/resolver error returns
    None and find_project_name falls back to its own path logic, so the
    SessionStart TODO scan can never break on this delegation."""
    try:
        import pre_plan_gates as _ppg
        return _ppg.canonical_project_for_spine(todo_path, project_root=vault_root)
    except Exception:
        return None


def find_project_name(todo_path, vault_root):
    """Derive the display project name from a TODO.md path.

    The Root-vs-leaf DECISION is delegated to the single canonical locus
    (S5/A5 — Cockburn Evolution Test: one rule, not two copies). The historical
    two-part display format for nested leaves and the out-of-tree fallback are
    preserved so the SessionStart scan output is unchanged (behaviour-preserving)."""
    try:
        rel = todo_path.parent.relative_to(vault_root)
    except ValueError:
        return todo_path.parent.name   # out-of-tree — unchanged
    canon = _canonical_project_or_none(todo_path, vault_root)
    if canon == "Root":
        return "Root"
    parts = rel.parts
    if not parts:
        return "Root"
    return parts[-1] if len(parts) == 1 else "/".join(parts[-2:])


def scan_vault(vault_root, cwd):
    """Scan all TODO.md files under vault_root. Returns list of active projects."""
    projects = []
    for todo_path in vault_root.rglob("TODO.md"):
        # Skip known non-project dirs
        parts_lower = {p.lower() for p in todo_path.parts}
        if any(p in parts_lower for p in (".obsidian", "node_modules", "_processed")):
            continue
        parsed = parse_todo_file(todo_path)
        if not is_active_project(parsed):
            continue
        name = find_project_name(todo_path, vault_root)
        scope = classify_scope(cwd, todo_path.parent)
        projects.append({
            "name": name,
            "path": todo_path,
            "parsed": parsed,
            "scope": scope,
        })
    return projects


def classify_urgency(days_until):
    """Classify an item's urgency by days_until its date.

    Returns: 'overdue' | 'urgent' | 'soon' | 'normal' | None (no date)
    """
    if days_until is None:
        return None
    if days_until < 0:
        return "overdue"
    if days_until <= URGENCY_DAYS:
        return "urgent"
    if days_until <= 30:
        return "soon"
    return "normal"


def suggest_bucket(days_until):
    """Suggest a bucket based on days_until a date. Returns bucket name or None."""
    if days_until is None:
        return None
    for name, lo, hi in BUCKET_HORIZONS:
        if days_until < 0:
            return "NOW"  # Overdue goes to Now
        if hi is None and days_until >= lo:
            return name
        if lo <= days_until <= hi:
            return name
    return None


def tokenize(text):
    """Extract meaningful tokens from item text (≥4 chars OR capitalized term)."""
    # Strip markdown bold/italic
    clean = re.sub(r"[*_`]", " ", text)
    # Extract words: alphanumeric + some punctuation common in tickers (e.g. PNG.V)
    tokens = set()
    for word in re.findall(r"[A-Za-z][\w.]*", clean):
        if len(word) >= 4:
            tokens.add(word)
        elif word[0].isupper() and len(word) >= 2:
            tokens.add(word)  # tickers, acronyms
    # Common stopwords to drop
    stopwords = {
        "this", "that", "with", "from", "into", "after", "before", "when",
        "where", "what", "which", "will", "would", "should", "could", "must",
        "have", "been", "were", "more", "than", "some", "other", "each",
        "item", "items", "todo", "done", "items", "text", "line",
    }
    return {t for t in tokens if t.lower() not in stopwords}


def scan_deps(item_text, all_projects):
    """Find keyword overlaps with existing open items across active projects."""
    item_tokens = tokenize(item_text)
    matches = []
    existing_tags = set()

    for proj in all_projects:
        parsed = proj["parsed"]
        # Collect existing [Tag] labels from open items in active sections
        for item in parsed["open_items"]:
            if item["section"] not in ACTIVE_SECTIONS:
                continue
            tag_match = TAG_RE.match(item["text"])
            if tag_match:
                existing_tags.add(tag_match.group(1))
        # Check each ACTIVE open item for token overlap.
        # Never items are excluded — declined tasks must not appear as
        # dependency matches (they are no longer active work).
        for item in parsed["active_open_items"]:
            item_tok = tokenize(item["text"])
            shared = item_tokens & item_tok
            if shared:
                matches.append({
                    "file": str(proj["path"]),
                    "project": proj["name"],
                    "line": item["line"] + 1,  # 1-indexed for display
                    "text": item["text"][:120],
                    "section": item["section"],
                    "subsection": item["subsection"],
                    "shared_tokens": sorted(shared),
                })

    return {
        "matches": matches,
        "existing_tags": sorted(existing_tags),
        "item_tokens": sorted(item_tokens),
    }


# =============================================================================
# Section 3b: link-related helper
# =============================================================================

def cmd_link_related(pattern, target_lines, file_path, project_path, cwd):
    """Append (related: L<n>) back-references to a source item matched by pattern.

    Args:
        pattern: regex to identify the source item (the item being annotated)
        target_lines: list of 1-indexed line numbers of related existing items
        file_path, project_path, cwd: file resolution args
    """
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1

    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1

    try:
        pat = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(json.dumps({"error": f"invalid pattern: {e}"}))
        return 1

    candidates = [item for item in parsed["open_items"] if pat.search(item["text"])]
    if not candidates:
        print(json.dumps({"error": "no matching open item found", "pattern": pattern}))
        return 1
    if len(candidates) > 1:
        print(json.dumps({
            "error": "ambiguous pattern — multiple items match",
            "candidates": [{"line": c["line"] + 1, "text": c["text"][:80]} for c in candidates],
        }))
        return 1

    item = candidates[0]
    lines = list(parsed["lines"])
    old_line = lines[item["line"]]

    # Build refs string, deduplicating and sorting
    refs = " ".join(f"(related: L{n})" for n in sorted(set(target_lines)))
    new_line = old_line.rstrip() + " " + refs

    lines[item["line"]] = new_line
    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)

    print(json.dumps({
        "success": True,
        "file": str(target),
        "source_line": item["line"] + 1,
        "refs_added": refs,
    }))
    return 0


def cmd_validate_framing(text):
    """Validate 4-component labeled framing convention. Returns dict."""
    errors = []
    if not re.search(r'\*\*.+\*\*', text):
        errors.append("Missing bold title (**Title**)")
    if ' — ' not in text and ' -- ' not in text:
        errors.append("Missing em-dash separator (—) between title and body")
    if not re.search(r'\bProblem:', text):
        errors.append("Missing 'Problem:' label")
    if not re.search(r'\bContext:', text):
        errors.append("Missing 'Context:' label")
    if not re.search(r'\bGuiding policy:', text, re.IGNORECASE):
        errors.append("Missing 'Guiding policy:' label")
    if not re.search(r'\bMaster plan:', text) and not re.search(r'\[\[.+\]\]', text):
        errors.append("Missing 'Master plan:' label or [[wikilink]]")
    if errors:
        return {"status": "fail", "errors": errors}
    return {"status": "pass"}


def cmd_reframe(pattern, new_text, file_path, project_path, cwd, supersede=False, bucket=None):
    """Reframe a TODO item in-place or supersede it with a new linked item."""
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1
    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1
    pat = re.compile(pattern, re.IGNORECASE)
    candidates = [i for i in parsed["open_items"] if pat.search(i["text"])]
    if not candidates:
        done_candidates = [i for i in parsed.get("done_items", []) if pat.search(i["text"])]
        if done_candidates:
            print(json.dumps({"status": "skipped", "reason": "item already done"}))
            return 0
        sys.stderr.write(f"No match for pattern: {pattern}\n")
        return 1
    if len(candidates) > 1:
        print(json.dumps({"status": "ambiguous", "matches": [i["text"][:60] for i in candidates]}))
        return 1
    item = candidates[0]
    if supersede:
        today = date.today().isoformat()
        lines = list(parsed["lines"])
        superseded = f"- [x] {item['text']} — **SUPERSEDED {today}.**"
        lines[item["line"]] = superseded
        new_content = "\n".join(lines)
        if not new_content.endswith("\n"):
            new_content += "\n"
        write_todo_file_atomic(target, new_content)
        return cmd_add(new_text, bucket or item["section"].upper(), None,
                       str(target), None, None)
    else:
        lines = list(parsed["lines"])
        lines[item["line"]] = f"- [ ] {new_text.strip()}"
        new_content = "\n".join(lines)
        if not new_content.endswith("\n"):
            new_content += "\n"
        write_todo_file_atomic(target, new_content)
        print(json.dumps({"status": "reframed"}))
        return 0


def cmd_mark_in_progress(pattern, session_id, file_path, project_path, cwd):
    """Append (in progress: SID[:8]) to matched open item; idempotent."""
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1
    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1
    pat = re.compile(pattern, re.IGNORECASE)
    candidates = [i for i in parsed["open_items"] if pat.search(i["text"])]
    if len(candidates) != 1:
        sys.stderr.write(f"Expected 1 match, got {len(candidates)}\n")
        return 1
    item = candidates[0]
    sid_short = session_id[:8]
    lines = list(parsed["lines"])
    old_line = lines[item["line"]]
    existing = re.search(r'\(in progress: [a-f0-9-]+\)', old_line)
    if existing:
        new_line = re.sub(r'\(in progress: [a-f0-9-]+\)', f'(in progress: {sid_short})', old_line)
    else:
        new_line = old_line.rstrip() + f" (in progress: {sid_short})"
    lines[item["line"]] = new_line
    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)
    print(json.dumps({"status": "marked", "session": sid_short}))
    return 0


# Slice-L phase annotation — distinct from `(in progress: …)` (mark-in-progress).
# Format: `(in <phase>: <sid8>)`. Phase enum mirrors pre_plan_gates.PHASE_SEQUENCE.
_PHASE_ANNOT_RE = re.compile(
    r"\(in (?:thought|clarification|planning|implementation|closing): [a-f0-9-]+\)"
)


def cmd_mark_in_phase(pattern, phase, session_id, file_path, project_path, cwd):
    """Append (in <phase>: SID[:8]) to matched open item; idempotent.

    Distinct from mark-in-progress (the legacy `(in progress: …)` annotation
    is left untouched). Idempotent on phase: re-running with the same phase
    + session_id is a no-op write of identical content.
    """
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1
    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1
    pat = re.compile(pattern, re.IGNORECASE)
    candidates = [i for i in parsed["open_items"] if pat.search(i["text"])]
    if len(candidates) == 0:
        sys.stderr.write(f"No match for {pattern!r} in {target}\n")
        print(json.dumps({"status": "no_match", "pattern": pattern}))
        return 1
    if len(candidates) > 1:
        sys.stderr.write(f"Expected 1 match, got {len(candidates)}\n")
        return 1
    item = candidates[0]
    sid_short = session_id[:8]
    annotation = f"(in {phase}: {sid_short})"
    lines = list(parsed["lines"])
    old_line = lines[item["line"]]
    if _PHASE_ANNOT_RE.search(old_line):
        new_line = _PHASE_ANNOT_RE.sub(annotation, old_line)
    else:
        new_line = old_line.rstrip() + f" {annotation}"
    lines[item["line"]] = new_line
    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)
    print(json.dumps({
        "status": "marked",
        "phase": phase,
        "session": sid_short,
        "line": item["line"] + 1,
        "annotation": annotation,
    }))
    return 0


# streamed-dancing-goose S5 (A5): the "not framed yet" marker. A line minted by
# auto-registration at ship carries `[auto-registered]` and is ALREADY tracked by
# `framing_obligation` (discharged when it passes, reported until then). The audit
# used to count such a line as a plain failure, so every legacy unframed line would
# have failed this command at every close forever. Now a marked line is reported
# as `not-framed-yet` (tracked elsewhere) and never fails the audit; what FAILS it
# is an unframed `[Thought]` line carrying NO marker — the one nothing else
# tracks (a hand-typed line, the /clarification-v2 door's line). The marker is
# imported from its one home rather than re-spelled here.
def _not_framed_yet_marker():
    try:
        import framing_obligation as _fo
        return _fo.MARKER
    except Exception:
        return "[auto-registered]"


def audit_framing(target):
    """Pure audit over one TODO.md: every ACTIVE open `[Thought]` line classified
    `pass` / `fail` (unframed, untracked) / `not-framed-yet` (unframed, carries the
    marker — tracked by the obligation surface). Returns the report dict or None
    when the file is unreadable. Terminal buckets (Done / Never) are excluded —
    a declined item owes no framing."""
    parsed = parse_todo_file(target)
    if parsed is None:
        return None
    marker = _not_framed_yet_marker()
    items = [i for i in parsed["active_open_items"] if "[Thought]" in i["text"]]
    results = []
    for item in items:
        vf = cmd_validate_framing(item["text"])
        status = vf["status"]
        tracked = marker in item["text"]
        if status == "fail" and tracked:
            status = "not-framed-yet"
        results.append({
            "line": item["line"] + 1,
            "text": item["text"][:80],
            "status": status,
            "tracked": tracked,
            "errors": vf.get("errors", []),
        })
    passed = sum(1 for r in results if r["status"] == "pass")
    not_yet = sum(1 for r in results if r["status"] == "not-framed-yet")
    failed = sum(1 for r in results if r["status"] == "fail")
    return {"file": str(target), "passed": passed, "failed": failed,
            "not_framed_yet": not_yet, "total": len(results), "items": results}


def render_framing_audit(report, *, limit=5):
    """The ONE human-facing block for UNTRACKED unframed `[Thought]` lines,
    rendered identically by the Stop reporter and the SessionStart scan. None
    when nothing fails (tracked lines are the obligation surface's to report)."""
    if not report:
        return None
    failing = [r for r in report.get("items", []) if r["status"] == "fail"]
    if not failing:
        return None
    n = len(failing)
    name = Path(report.get("file", "TODO.md")).name
    head = (f"⚠ {n} [Thought] line{'s' if n != 1 else ''} in {name} "
            f"{'are' if n != 1 else 'is'} not framed and nothing tracks "
            f"{'them' if n != 1 else 'it'} (no `{_not_framed_yet_marker()}` marker): "
            f"the line does not say what the problem is or where the thinking lives.")
    body = [f"    {name}:{r['line']} — {_item_snippet(r['text'], 70)} "
            f"[{'; '.join(r['errors'][:2])}]" for r in failing[:limit]]
    if n > limit:
        body.append(f"    … and {n - limit} more")
    tail = ("  Frame each one in place (Problem / Context / Guiding policy / "
            "Master plan), or run /work-frame-and-create-todo. Reported, not blocked.")
    return "\n".join([head, *body, tail])


def cmd_audit_coverage(file_path, project_path, cwd, surface=False):
    """Validate framing on all active [Thought]-tagged items; report pass /
    not-framed-yet / fail. Exit 1 iff an UNTRACKED line fails (a `fail` row);
    `not-framed-yet` rows never fail the audit. `--surface` prints the shared
    human block (to stderr) instead of JSON."""
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1
    report = audit_framing(target)
    if report is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1
    if surface:
        block = render_framing_audit(report)
        if block:
            print(block, file=sys.stderr)
        return 0 if report["failed"] == 0 else 1
    print(json.dumps(report, indent=2))
    return 0 if report["failed"] == 0 else 1


def cmd_promote_to_thought(pattern, file_path, project_path, cwd):
    """OQ8 policy stub — ultra-fast-track items do not auto-promote."""
    print(json.dumps({
        "status": "policy",
        "message": (
            "Ultra-fast-track items do not auto-promote to Thought+master-plan. "
            "To promote a grown item: manually create a _THOUGHT.md, run "
            "/clarification, then update the TODO item text to conform to the "
            "4-component framing convention. This command is a placeholder; "
            "Slice F will implement optional automation."
        ),
    }))
    return 0


# =============================================================================
# Section 4: Command handlers
# =============================================================================

def _check_marker(session_id):
    """Check if marker exists for this session (i.e., not first prompt)."""
    if not session_id:
        return False
    marker = MARKER_DIR / f"_todo-read-{session_id}"
    return marker.exists()


def _create_marker(session_id):
    """Create marker file for this session."""
    if not session_id:
        return
    MARKER_DIR.mkdir(parents=True, exist_ok=True)
    marker = MARKER_DIR / f"_todo-read-{session_id}"
    marker.touch()


def _sweep_old_markers():
    """Delete marker files older than MARKER_TTL_HOURS."""
    if not MARKER_DIR.exists():
        return
    cutoff = datetime.now().timestamp() - (MARKER_TTL_HOURS * 3600)
    for marker in MARKER_DIR.glob("_todo-read-*"):
        try:
            if marker.stat().st_mtime < cutoff:
                marker.unlink()
        except OSError:
            pass


def cmd_read(cwd, session_id=None, force=False):
    """Read mode: scan vault, output prioritized summary.

    If session_id provided and marker exists: exit silently (not first prompt).
    """
    if session_id and not force:
        if _check_marker(session_id):
            return 0
        _sweep_old_markers()
        _create_marker(session_id)

    vault_root = find_vault_root(cwd)
    projects = scan_vault(vault_root, Path(cwd))

    if not projects:
        return 0  # Silent if no active projects

    today = date.today()
    output = _format_read_output(projects, today)
    if output:
        print(output)
    return 0


def _format_read_output(projects, today):
    """Format the read output as plain text (injected as system-reminder)."""
    lines = []

    # Header
    lines.append(f"TODO ({today.isoformat()}, {len(projects)} active projects)")

    # Collect overdue and urgent items across all projects
    overdue = []
    urgent = []
    prose_warnings = []

    for proj in projects:
        # Use active_open_items so Never items (even with past ISO dates)
        # never fire as OVERDUE or URGENT in the session scan.
        for item in proj["parsed"]["active_open_items"]:
            urgency = classify_urgency(item["days_until"])
            if urgency == "overdue":
                overdue.append((proj, item))
            elif urgency == "urgent":
                urgent.append((proj, item))
            if item["prose_date"]:
                prose_warnings.append((proj["name"], item["text"][:80]))

    # Sort by date ascending
    overdue.sort(key=lambda x: x[1]["days_until"])
    urgent.sort(key=lambda x: x[1]["days_until"])

    if overdue:
        for proj, item in overdue:
            days = abs(item["days_until"])
            snippet = _item_snippet(item["text"])
            lines.append(
                f"OVERDUE: {proj['name']}: {snippet} — {item['iso_date']} ({days}d overdue)"
            )

    if urgent:
        for proj, item in urgent:
            snippet = _item_snippet(item["text"])
            days = item["days_until"]
            day_str = "today" if days == 0 else f"{days}d"
            lines.append(
                f"URGENT: {proj['name']}: {snippet} — {item['iso_date']} ({day_str})"
            )

    # Per-project summaries
    current = [p for p in projects if p["scope"] == "current"]
    other = [p for p in projects if p["scope"] == "other"]

    for proj in sorted(current, key=lambda p: p["name"]):
        counts = _bucket_counts(proj["parsed"])
        count_str = ", ".join(
            f"{k}: {v}" for k, v in counts.items() if v > 0
        )
        lines.append(f"-- {proj['name']} ({count_str or 'empty'})")

    if other:
        other_with_now = [
            p for p in other if _bucket_counts(p["parsed"]).get("Now", 0) > 0
        ]
        if other_with_now:
            parts = [
                f"{p['name']} (Now: {_bucket_counts(p['parsed'])['Now']})"
                for p in sorted(other_with_now, key=lambda p: p["name"])
            ]
            lines.append(f"Other projects with Now items: {', '.join(parts)}")

    if prose_warnings:
        for name, text in prose_warnings[:3]:  # Cap at 3 to avoid noise
            lines.append(f"WARN: prose date in {name}: {text}")

    # Structural lint: ### subsections in active buckets
    for proj in projects:
        for section, subs in proj["parsed"]["subsections"].items():
            if section not in ACTIVE_SECTIONS:
                continue
            for sub_title in subs:
                if ISO_DATE_RE.fullmatch(sub_title.strip()):
                    continue
                lines.append(
                    f'WARN: ### "{sub_title}" in {section.title()} '
                    f'({proj["name"]}) — active buckets must be flat. '
                    f'Use [Tag] in item text.'
                )

    # Diary link lint: Done items without wikilinks
    diary_warnings = []
    for proj in projects:
        for item in proj["parsed"]["done_items"]:
            if item["section"] == "done" and "[[" not in item["text"]:
                snippet = _item_snippet(item["text"])
                diary_warnings.append(
                    f'WARN: Done item missing diary link in '
                    f'{proj["name"]}: {snippet}'
                )
    for warn in diary_warnings[:5]:  # Cap at 5 to avoid noise
        lines.append(warn)

    # Framing obligations for auto-registered lines (auto-registration S-C / A3).
    # One SHARED reconcile+render used identically here, at /close, and in the
    # omission Stop hook. Discharges any line that now passes the framing bar
    # (stripping its marker) and reports what is still outstanding. SOFT only —
    # this is the SessionStart path, so it must never raise and never block.
    try:
        import framing_obligation as _fo
        _fo_result = _fo.reconcile([p["path"] for p in projects if p.get("path")])
        _fo_block = _fo.render_surface(_fo_result)
        if _fo_block:
            lines.append(_fo_block)
    except Exception:
        pass  # fail-open: a framing-surface bug must not break the TODO scan

    # streamed-dancing-goose S5 (A5): the UNTRACKED half — unframed `[Thought]`
    # lines carrying no marker, which the obligation surface above cannot see.
    # This SessionStart injection is the channel the tree has established as
    # reaching the operator (work_done_report.py:11-28); the Stop reporter
    # `check-framing-audit-stop.sh` renders the same block at session end.
    # SOFT only: never raises, never blocks.
    try:
        for proj in projects:
            if proj.get("scope") != "current" or not proj.get("path"):
                continue
            _block = render_framing_audit(audit_framing(proj["path"]))
            if _block:
                lines.append(_block)
    except Exception:
        pass  # fail-open

    return "\n".join(lines)


def _item_snippet(text, max_len=80):
    """Truncate item text to first line, max N chars, strip markdown bold."""
    first_line = text.split("\n")[0]
    clean = re.sub(r"\*\*([^*]+)\*\*", r"\1", first_line)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_len:
        clean = clean[: max_len - 1] + "…"
    return clean


def _bucket_counts(parsed):
    """Count open items per active section (using display-case names).

    Never items are excluded — they are in a terminal section and must not
    appear in per-bucket counts or session-scan summaries.
    """
    counts = defaultdict(int)
    display_names = {
        "now": "Now", "next": "Next", "nearby": "Nearby",
        "nascent": "Nascent", "scheduled": "Scheduled", "blocked": "Blocked",
    }
    for item in parsed["active_open_items"]:
        sec = item["section"]
        display = display_names.get(sec, sec.capitalize())
        counts[display] += 1
    return dict(counts)


def cmd_deps(item_text, cwd):
    """Scan for keyword dependencies. Output JSON."""
    vault_root = find_vault_root(cwd)
    projects = scan_vault(vault_root, Path(cwd))
    result = scan_deps(item_text, projects)
    print(json.dumps(result, indent=2))
    return 0


def cmd_add(item_text, bucket, tag, file_path, project_path, cwd,
            require_confirm=None, validate_framing=False, phase=None,
            derives_from=None, require_evidence=False):
    """Add an item to a TODO file under the specified bucket.

    If require_confirm is a session_id, refuses unless is-audit-capture-confirmed
    returns exit 0 for that session. Normal callers (no flag) are unaffected.
    """
    if require_confirm:
        import subprocess as _sp
        result = _sp.run(
            ["python3", __file__, "is-audit-capture-confirmed", require_confirm],
            capture_output=True,
        )
        if result.returncode != 0:
            print(json.dumps({
                "error": "audit capture not confirmed",
                "reason": "Call `pre_plan_gates.py confirm-audit-capture SESSION_ID` "
                          "after explicit user approval before writing the one-liner.",
                "session_id": require_confirm,
            }))
            return 1

    # Phase routing (Workflow.md:158-161)
    if phase == "planning":
        print(json.dumps({"status": "withheld", "reason": "planning drafts not reflected per Workflow.md:158"}))
        return 0
    elif phase in ("thought", "clarification"):
        print(json.dumps({"status": "withheld",
                          "reason": f"{phase} items are reflected via phase-start/register-session-todo, not direct add"}))
        return 0
    # implementation: not suppressed — todos reflected per Workflow.md:160

    # Framing validation gate (caller opt-in via --validate-framing)
    if validate_framing:
        vf = cmd_validate_framing(item_text)
        if vf["status"] == "fail":
            for err in vf["errors"]:
                sys.stderr.write(f"  ✗ {err}\n")
            sys.stderr.write("Framing validation failed. Fix the item text and retry.\n")
            # streamed-dancing-goose S5 (A5): the refusal is ALSO machine-readable —
            # one JSON object on stdout, exit code unchanged. The `  ✗ ` stderr
            # shape above is untouched: work-frame-and-create-todo/run.py parses
            # exactly that into `missing_components`, and the four other callers
            # branch on the exit code alone and read stdout only on success.
            print(json.dumps({"status": "refused", "reason": "framing",
                              "errors": vf["errors"]}))
            return 1
        if derives_from and not re.search(r'\[\[.+\]\]', item_text):
            sys.stderr.write("  ✗ --derives-from set but no [[wikilink]] back-reference in item text.\n")
            print(json.dumps({"status": "refused", "reason": "derives-from-without-wikilink",
                              "errors": ["--derives-from set but no [[wikilink]] back-reference in item text."]}))
            return 1

    # Evidence gate (caller opt-in via --require-evidence).
    #
    # A SEPARATE PREDICATE, NOT A TIGHTENING OF cmd_validate_framing. Five
    # callers depend on that function's current bar and two hard-fail on a
    # refusal; `test_framing_obligation.py` pins the auto-registration
    # obligation to it. Widening it would change what that obligation
    # discharges against, so this ships as its own flag that one caller passes.
    #
    # Placed BEFORE `_resolve_target_todo` so a refusal writes nothing at all.
    # Errors use the `  ✗ ` prefix because the skill's run.py parses exactly
    # that shape into `missing_components`; any other shape is invisible to the
    # retry loop and the person never learns why the item was refused.
    if require_evidence:
        try:
            import framing_evidence
        except ImportError as e:
            # Fail OPEN, loudly. This gate raises the floor on item quality; it
            # is not a safety boundary, and a missing module must not make the
            # TODO surface unwritable.
            sys.stderr.write(
                f"  ! evidence check unavailable ({e}) — proceeding without it\n")
        else:
            ev = framing_evidence.evaluate(
                item_text, root=(project_path or cwd or None))
            for warn in ev.get("warnings", []):
                sys.stderr.write(f"  ! {warn}\n")
            if ev["status"] == "fail":
                for err in ev["errors"]:
                    sys.stderr.write(f"  ✗ {err}\n")
                sys.stderr.write(
                    "Evidence check failed. Nothing was written.\n")
                return 1

    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1

    # Validate: prose dates in item text are a warning (ISO is the convention)
    warnings = []
    if PROSE_DATE_RE.search(item_text) and not ISO_DATE_RE.search(item_text):
        warnings.append(
            "Item contains a prose date. Use ISO format (YYYY-MM-DD) per convention."
        )

    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1

    lines = list(parsed["lines"])
    section_lower = bucket.lower()

    # Find section or create it
    if section_lower not in parsed["sections"]:
        insert_at = _find_section_insertion_point(lines, parsed, section_lower)
        lines[insert_at:insert_at] = [f"## {bucket.capitalize()}", "", ""]
        section_start = insert_at
    else:
        section_start = parsed["sections"][section_lower][0]

    section_end = _find_section_end(lines, section_start)

    # Prepend [Tag] to item text if --tag provided
    if tag:
        item_text = f"[{tag}] {item_text.strip()}"
    new_item = f"- [ ] {item_text.strip()}"

    # Extract section body (lines between header and next section)
    body = lines[section_start + 1:section_end]

    # Remove placeholder lines
    body = [ln for ln in body if not EMPTY_PLACEHOLDER_RE.match(ln)]

    # Strip leading and trailing blank lines
    while body and body[0].strip() == "":
        body.pop(0)
    while body and body[-1].strip() == "":
        body.pop()

    body.append(new_item)
    insert_line_in_body = len(body) - 1

    # Reassemble: blank line after header, body, blank line before next section
    new_section_body = [""] + body + [""]
    lines[section_start + 1:section_end] = new_section_body

    insert_line = section_start + 1 + 1 + insert_line_in_body  # header + leading blank + offset

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)

    result = {
        "success": True,
        "file": str(target),
        "line": insert_line + 1,  # 1-indexed
        "bucket": bucket,
        "tag": tag,
    }
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, indent=2))
    return 0


def cmd_done(pattern, file_path, project_path, text, cwd):
    """Mark an item as done. Returns candidates if ambiguous."""
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1

    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1

    try:
        pat = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(json.dumps({"error": f"invalid pattern: {e}"}))
        return 1

    # Find matching open items
    candidates = [
        item for item in parsed["open_items"] if pat.search(item["text"])
    ]

    if not candidates:
        print(json.dumps({"error": "no matching open item", "pattern": pattern}))
        return 1

    if len(candidates) > 1:
        print(json.dumps({
            "candidates": [
                {"line": c["line"] + 1, "text": c["text"][:120], "section": c["section"]}
                for c in candidates
            ],
            "hint": "Multiple matches. Refine pattern or call again with narrower regex.",
        }, indent=2))
        return 2

    # Single match — guard: refuse to mark a terminal-section item [x].
    # Never items stay [ ] forever; they are declined, not completed.
    item = candidates[0]
    if item["section"] in TERMINAL_SECTIONS:
        print(json.dumps({
            "status": "refused",
            "reason": (
                f"item is in a terminal section ({item['section'].capitalize()} / Won't-Do); "
                "declined items stay [ ], not completed"
            ),
            "section": item["section"],
            "line": item["line"] + 1,
            "text": item["text"][:120],
        }, indent=2))
        return 1

    lines = list(parsed["lines"])
    today_iso = date.today().isoformat()

    if text:
        if not text.lstrip().startswith("- [x]"):
            print(json.dumps({"error": "--text must start with '- [x]'"}))
            return 1
        new_line = text
    else:
        # Default: [x] + original text + DONE stamp
        new_line = f"- [x] {item['text']} — **DONE {today_iso}.**"

    old_line = lines[item["line"]]
    lines[item["line"]] = new_line

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)

    print(json.dumps({
        "success": True,
        "file": str(target),
        "line": item["line"] + 1,
        "old_line": old_line,
        "new_line": new_line,
    }, indent=2))
    return 0


def cmd_advance_sessions(pattern, file_path, project_path, cwd, summary):
    """Find an open `Sessions: M/N done` item; increment M.

    If new M >= N: convert the item to [x] with today's DONE stamp + optional summary.
    Otherwise: rewrite the line with the new counter.

    Returns 0 on success (single match found, line rewritten), 1 on error,
    2 on ambiguity (multiple candidates). Mirrors cmd_done's contract.
    """
    target = _resolve_target_todo(file_path, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1

    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1

    try:
        pat = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        print(json.dumps({"error": f"invalid pattern: {e}"}))
        return 1

    candidates = [
        item for item in parsed["open_items"]
        if pat.search(item["text"]) and SESSIONS_COUNTER_RE.search(item["text"])
    ]

    if not candidates:
        print(json.dumps({
            "error": "no matching open item with `Sessions: M/N done` counter",
            "pattern": pattern,
        }))
        return 1

    if len(candidates) > 1:
        print(json.dumps({
            "candidates": [
                {"line": c["line"] + 1, "text": c["text"][:120], "section": c["section"]}
                for c in candidates
            ],
            "hint": "Multiple matches. Refine pattern or call again with narrower regex.",
        }, indent=2))
        return 2

    item = candidates[0]
    counter_match = SESSIONS_COUNTER_RE.search(item["text"])
    m_old = int(counter_match.group(1))
    n_total = int(counter_match.group(2))
    m_new = m_old + 1

    lines = list(parsed["lines"])
    today_iso = date.today().isoformat()

    if m_new >= n_total:
        # All sessions shipped — convert to [x] with DONE stamp
        new_text = SESSIONS_COUNTER_RE.sub(
            f"Sessions: {n_total}/{n_total} done.",
            item["text"],
        )
        suffix = f" — **DONE {today_iso}.**"
        if summary:
            suffix = f" — **DONE {today_iso}.** {summary.strip()}"
        new_line = f"- [x] {new_text}{suffix}"
        completed = True
    else:
        new_text = SESSIONS_COUNTER_RE.sub(
            f"Sessions: {m_new}/{n_total} done.",
            item["text"],
        )
        new_line = f"- [ ] {new_text}"
        completed = False

    old_line = lines[item["line"]]
    lines[item["line"]] = new_line

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)

    print(json.dumps({
        "success": True,
        "file": str(target),
        "line": item["line"] + 1,
        "old_line": old_line,
        "new_line": new_line,
        "counter_before": f"{m_old}/{n_total}",
        "counter_after": f"{m_new}/{n_total}",
        "completed": completed,
    }, indent=2))
    return 0


def cmd_cleanup(project_path, cwd):
    """Move [x] items from active sections to Done section under date headers."""
    target = _resolve_target_todo(None, project_path, cwd)
    if target is None:
        print(json.dumps({"error": "could not locate target TODO.md"}))
        return 1

    parsed = parse_todo_file(target)
    if parsed is None:
        print(json.dumps({"error": f"could not read {target}"}))
        return 1

    # Find [x] items in active sections
    to_move = [
        item for item in parsed["done_items"]
        if item["section"] in ACTIVE_SECTIONS
    ]

    if not to_move:
        print(json.dumps({"success": True, "moved": 0, "message": "nothing to move"}))
        return 0

    lines = list(parsed["lines"])
    today_iso = date.today().isoformat()

    # Group items to move by date
    by_date = defaultdict(list)
    for item in to_move:
        move_date = item["done_date"] or today_iso
        by_date[move_date].append(item)

    # Find or create the Done section
    done_start = None
    if "done" in parsed["sections"]:
        done_start = parsed["sections"]["done"][0]
    else:
        # Append at end of file
        while lines and lines[-1].strip() == "":
            lines.pop()
        lines.extend(["", "## Done", ""])
        done_start = len(lines) - 2

    # For each date, find or create its subheader and insert Done entries
    # We do this by mutating a working list of lines
    date_headers_created = []

    # Sort dates descending so newer entries go at top
    for move_date in sorted(by_date.keys(), reverse=True):
        items = by_date[move_date]
        # Find existing ### date header in Done section
        header_line = _find_date_header_in_done(lines, done_start, move_date)
        if header_line is None:
            # Insert new date header right after "## Done" line
            insert_at = done_start + 1
            # Skip blank lines right after Done header
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            block = [f"### {move_date}", ""]
            for it in items:
                entry_text = _done_entry_text(it['text'])
                if "[[" not in entry_text:
                    entry_text += f" [[{move_date}]]"
                block.append(f"- {entry_text}")
            block.append("")
            lines[insert_at:insert_at] = block
            date_headers_created.append(move_date)
        else:
            # Insert after existing header, before next ### or ##
            insert_at = header_line + 1
            while insert_at < len(lines) and lines[insert_at].strip() == "":
                insert_at += 1
            new_entries = []
            for it in items:
                entry_text = _done_entry_text(it['text'])
                if "[[" not in entry_text:
                    entry_text += f" [[{move_date}]]"
                new_entries.append(f"- {entry_text}")
            lines[insert_at:insert_at] = new_entries

    # Now remove the [x] items from active sections (use original line numbers from parsed)
    # We must remove in reverse order since we've already mutated lines above
    # Compute fresh: re-find the [x] lines by content match after our insertions

    # Re-parse the mutated content to find the now-shifted [x] items
    mutated_content = "\n".join(lines)
    re_parsed = parse_todo_file_from_text(mutated_content)
    active_done_lines = [
        item["line"] for item in re_parsed["done_items"]
        if item["section"] in ACTIVE_SECTIONS
    ]

    # Remove them in reverse order
    for line_idx in sorted(active_done_lines, reverse=True):
        lines.pop(line_idx)

    # Normalize active sections: strip leading/trailing blanks, add placeholder
    # if empty, preserve existing placeholders. Recompute section bounds between
    # passes since mutations shift line indices.
    empty_sections_added = []
    for section_name in list(ACTIVE_SECTIONS):
        # Find section start in current lines
        section_start = None
        for i, ln in enumerate(lines):
            m = SECTION_RE.match(ln)
            if m and m.group(1).lower() == section_name:
                section_start = i
                break
        if section_start is None:
            continue
        section_end = _find_section_end(lines, section_start)

        body = lines[section_start + 1:section_end]

        # Separate placeholders from other content
        non_placeholder = [ln for ln in body if not EMPTY_PLACEHOLDER_RE.match(ln)]
        has_content = any(ln.strip() != "" for ln in non_placeholder)

        if has_content:
            # Drop any lingering placeholders, strip leading/trailing blanks
            new_body = list(non_placeholder)
            while new_body and new_body[0].strip() == "":
                new_body.pop(0)
            while new_body and new_body[-1].strip() == "":
                new_body.pop()
        else:
            # Preserve existing placeholder if any, else add a dated one
            existing = [ln for ln in body if EMPTY_PLACEHOLDER_RE.match(ln)]
            if existing:
                new_body = [existing[0]]
            else:
                new_body = [f"_Empty — section cleaned up on {today_iso}._"]
                empty_sections_added.append(section_name)

        # Rebuild with blank after header and blank before next section
        lines[section_start + 1:section_end] = [""] + new_body + [""]

    new_content = "\n".join(lines)
    if not new_content.endswith("\n"):
        new_content += "\n"
    write_todo_file_atomic(target, new_content)

    print(json.dumps({
        "success": True,
        "file": str(target),
        "moved": len(to_move),
        "date_headers_created": date_headers_created,
        "empty_sections_filled": empty_sections_added,
    }, indent=2))
    return 0


def _done_entry_text(raw_text):
    """Convert a raw [x] item text into a one-line Done entry."""
    # Strip leading item marker artifacts already gone (text starts after [x])
    # Remove DONE stamps that duplicate the date header
    clean = DONE_DATE_RE.sub("", raw_text)
    clean = re.sub(r"\s+", " ", clean).strip()
    # Strip trailing punctuation noise from the stamp removal
    clean = clean.rstrip(" —-.")
    return clean


# -----------------------------------------------------------------------------
# File resolution helpers
# -----------------------------------------------------------------------------

def _resolve_target_todo(file_path, project_path, cwd):
    """Resolve which TODO.md to operate on.

    Priority:
      1. Explicit --file
      2. --project (dir or file)
      3. Walk up from cwd to find TODO.md
    """
    if file_path:
        p = Path(file_path)
        if p.exists() and p.is_file():
            return p
        return None

    if project_path:
        p = Path(project_path)
        if p.is_file() and p.name == "TODO.md":
            return p
        if p.is_dir():
            candidate = p / "TODO.md"
            if candidate.exists():
                return candidate
        return None

    # Walk up from cwd — main-pinned (S2/A1). Inside a topic worktree, walk on
    # main's checkout instead of the worktree filesystem: TODO.md is
    # sparse-excluded from the worktree (A3), so a raw walk there would miss it
    # and a stale private copy must never be resolved. The worktree mirrors
    # main's relative layout, so cwd maps 1:1 onto main.
    start = Path(cwd).resolve()
    try:
        import bookkeeping_resolver
        main = bookkeeping_resolver.main_checkout(cwd)
        wt = bookkeeping_resolver.current_worktree_root(cwd)
        if main is not None and wt is not None and main != wt:
            try:
                start = (main / start.relative_to(wt))
            except ValueError:
                start = Path(main)
    except Exception:
        pass
    current = start
    for _ in range(10):  # Max depth guard
        candidate = current / "TODO.md"
        if candidate.exists():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


# -----------------------------------------------------------------------------
# Section mutation helpers
# -----------------------------------------------------------------------------

def parse_todo_file_from_text(content):
    """Same as parse_todo_file but from in-memory content."""
    import tempfile as _t
    with _t.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".md", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        return parse_todo_file(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)


def _find_section_end(lines, section_start):
    """Find the line index where the section ending (next ## heading or EOF)."""
    for i in range(section_start + 1, len(lines)):
        if SECTION_RE.match(lines[i]):
            return i
    return len(lines)


def _find_section_insertion_point(lines, parsed, section_name):
    """Find where to insert a new section if it doesn't exist.

    Terminal sections (never, done) land near the end of the file:
      - 'never' goes just before 'done' (if done exists), otherwise at EOF.
    Active sections land before any terminal section present in the file
    so declined/completed work always stays at the end of the document.
    """
    if section_name in TERMINAL_SECTIONS:
        # Terminal: insert just before Done if Done already exists.
        if "done" in parsed["sections"]:
            return parsed["sections"]["done"][0]
        # else append at EOF (Never before a not-yet-created Done is fine)
    else:
        # Active: insert before the first terminal section we find.
        for terminal in ("never", "done"):
            if terminal in parsed["sections"]:
                return parsed["sections"][terminal][0]
    # Fallback: append at end of file (strip trailing blanks first).
    end = len(lines)
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    return end


def _find_subsection_line(lines, section_start, section_end, title):
    """Find ### subsection by title within a section. Returns line idx or None."""
    title_norm = title.strip().lower()
    for i in range(section_start + 1, section_end):
        m = SUBSECTION_RE.match(lines[i])
        if m and m.group(1).strip().lower() == title_norm:
            return i
    return None


def _find_subsection_end(lines, sub_start, section_end):
    """Find end of a ### subsection (next ### or ## or EOF)."""
    for i in range(sub_start + 1, section_end):
        if SUBSECTION_RE.match(lines[i]) or SECTION_RE.match(lines[i]):
            return i
    return section_end


def _section_has_only_placeholder(lines, section_start, section_end):
    """Check if section contains only _Empty_ placeholder."""
    for i in range(section_start + 1, section_end):
        line = lines[i]
        if line.strip() == "":
            continue
        if EMPTY_PLACEHOLDER_RE.match(line):
            return True
        # Any other content means not empty-placeholder
        return False
    return False


def _find_date_header_in_done(lines, done_start, target_date):
    """Find ### YYYY-MM-DD header in Done section. Returns line idx or None."""
    done_end = _find_section_end(lines, done_start)
    for i in range(done_start + 1, done_end):
        m = SUBSECTION_RE.match(lines[i])
        if m and m.group(1).strip() == target_date:
            return i
    return None


# =============================================================================
# Diary-link validation (DS7 #13a) — bookkeeping-model drift Row 3
# =============================================================================

_DONE_LINE_RE = re.compile(r"^\s*-\s*\[x\]", re.IGNORECASE)


def _diary_link_violations(text):
    """Pure predicate: Done lines (`- [x]`) lacking a `[[...]]` diary wikilink.

    Mirrors the existing cmd_read WARN (todo-management.md: every Done item ends
    with a `[[YYYY-MM-DD]]` link). Returns the list of offending line texts."""
    out = []
    for line in (text or "").split("\n"):
        if _DONE_LINE_RE.match(line) and "[[" not in line:
            out.append(line.strip())
    return out


def cmd_validate_diary_link(text):
    v = _diary_link_violations(text)
    return {"status": "fail" if v else "pass", "violations": v}


def cmd_validate_todo_write(stdin_text):
    """Hook entry (PostToolUse on **/TODO.md). Reads the tool JSON from stdin,
    validates the changed text's Done lines for diary links. Edit -> the changed
    new_string is precise, so a net-new Done line missing a link blocks (exit 2);
    Write replaces the whole file, so it WARNs only (exit 0) to avoid flagging
    pre-existing debt. Read-only — never patches."""
    try:
        payload = json.loads(stdin_text)
    except (ValueError, TypeError):
        return 0
    fp = (payload.get("tool_input", {}) or {}).get("file_path", "") or ""
    if not fp.endswith("TODO.md"):
        return 0
    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}
    if tool == "Edit":
        text, blocking = ti.get("new_string", ""), True
    elif tool == "Write":
        text, blocking = ti.get("content", ""), False
    else:
        return 0
    violations = _diary_link_violations(text)
    if not violations:
        return 0
    tag = "BLOCKED" if blocking else "WARN"
    lines = [f"{tag}: TODO Done item(s) missing a diary wikilink "
             f"(append `[[YYYY-MM-DD]]`) — bookkeeping-model drift Row 3:"]
    for v in violations[:5]:
        lines.append(f"  • {v}")
    sys.stderr.write("\n".join(lines) + "\n")
    return 2 if blocking else 0


# =============================================================================
# Section 5: main / CLI dispatch
# =============================================================================

def main():
    parser = argparse.ArgumentParser(prog="todo.py", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd")

    # read (also the default when no subcommand)
    p_read = sub.add_parser("read", help="Scan vault and output summary")
    p_read.add_argument("--cwd", default=None)
    p_read.add_argument("--force", action="store_true", help="Ignore marker")

    # deps
    p_deps = sub.add_parser("deps", help="Scan dependencies for new item text")
    p_deps.add_argument("text")
    p_deps.add_argument("--cwd", default=None)

    # add
    p_add = sub.add_parser("add", help="Add a new item")
    p_add.add_argument("text")
    p_add.add_argument("--bucket", required=True,
                       choices=["NOW", "NEXT", "NEARBY", "NASCENT", "SCHEDULED", "NEVER"])
    p_add.add_argument("--tag", default=None, help="Optional [Tag] prefix for item")
    p_add.add_argument("--file", default=None, help="Explicit TODO.md file path")
    p_add.add_argument("--project", default=None, help="Project directory")
    p_add.add_argument("--cwd", default=None)
    p_add.add_argument("--require-confirm", default=None, metavar="SESSION_ID",
                       help="Refuse unless is-audit-capture-confirmed for SESSION_ID")
    p_add.add_argument("--validate-framing", action="store_true", default=False,
                       help="Validate 4-component framing before write")
    p_add.add_argument("--phase", default=None,
                       help="Phase context; 'planning' suppresses write")
    p_add.add_argument("--derives-from", default=None, metavar="PATH",
                       help="Require [[wikilink]] back-reference when --validate-framing active")
    p_add.add_argument("--require-evidence", action="store_true", default=False,
                       help="Refuse an item that points at nothing (no path:line, "
                            "backticked file, [[wikilink]] or URL) unless it "
                            "carries the literal [build-new] exception. Separate "
                            "from --validate-framing; a citation that fails to "
                            "resolve warns, never blocks.")

    # validate-framing
    p_vf = sub.add_parser("validate-framing", help="Validate 4-component framing convention")
    p_vf.add_argument("text")

    # reframe
    p_reframe = sub.add_parser("reframe", help="Reframe a TODO item in-place or supersede it")
    p_reframe.add_argument("--pattern", required=True, help="Regex to match item text")
    p_reframe.add_argument("--new-text", required=True, dest="new_text")
    p_reframe.add_argument("--supersede", action="store_true", default=False)
    p_reframe.add_argument("--bucket", default=None)
    p_reframe.add_argument("--file", default=None)
    p_reframe.add_argument("--project", default=None)
    p_reframe.add_argument("--cwd", default=None)

    # mark-in-progress
    p_mip = sub.add_parser("mark-in-progress", help="Mark item in-progress with session ID")
    p_mip.add_argument("--pattern", required=True)
    p_mip.add_argument("--session", required=True, dest="session_id")
    p_mip.add_argument("--file", default=None)
    p_mip.add_argument("--project", default=None)
    p_mip.add_argument("--cwd", default=None)

    # mark-in-phase (Slice L A7) — distinct from mark-in-progress
    p_mip2 = sub.add_parser(
        "mark-in-phase",
        help="Mark item with (in <phase>: <sid>) annotation (Slice L)",
    )
    p_mip2.add_argument("--pattern", required=True)
    p_mip2.add_argument("--phase", required=True,
                        choices=["thought", "clarification", "planning",
                                 "implementation", "closing"])
    p_mip2.add_argument("--session", required=True, dest="session_id")
    p_mip2.add_argument("--file", default=None)
    p_mip2.add_argument("--project", default=None)
    p_mip2.add_argument("--cwd", default=None)

    # audit-coverage
    p_ac = sub.add_parser("audit-coverage", help="Validate framing on all active [Thought] items")
    p_ac.add_argument("--file", default=None)
    p_ac.add_argument("--project", default=None)
    p_ac.add_argument("--cwd", default=None)
    p_ac.add_argument("--surface", action="store_true", default=False,
                      help="print the shared human block for UNTRACKED unframed [Thought] "
                           "lines to stderr instead of JSON (the Stop reporter's mode)")

    # promote-to-thought (OQ8 policy stub)
    p_ptt = sub.add_parser("promote-to-thought",
                           help="(OQ8 policy) Guidance for promoting grown ultra-fast-track items")
    p_ptt.add_argument("--pattern", required=True)
    p_ptt.add_argument("--file", default=None)
    p_ptt.add_argument("--project", default=None)
    p_ptt.add_argument("--cwd", default=None)

    # done
    p_done = sub.add_parser("done", help="Mark an item [x]")
    p_done.add_argument("--pattern", required=True, help="Regex to match item text")
    p_done.add_argument("--file", default=None)
    p_done.add_argument("--project", default=None)
    p_done.add_argument("--text", default=None,
                        help="Replacement line (must start with '- [x]')")
    p_done.add_argument("--cwd", default=None)

    # cleanup
    p_clean = sub.add_parser("cleanup", help="Move [x] items to Done section")
    p_clean.add_argument("--project", default=None)
    p_clean.add_argument("--cwd", default=None)

    # advance-sessions (S2): increment M on a `Sessions: M/N done` counter
    p_adv = sub.add_parser(
        "advance-sessions",
        help="Increment Sessions: M/N counter on a [Thought]-tagged item",
    )
    p_adv.add_argument("--pattern", required=True, help="Regex to match item text")
    p_adv.add_argument("--file", default=None)
    p_adv.add_argument("--project", default=None)
    p_adv.add_argument("--summary", default=None,
                       help="Completion summary appended when M reaches N")
    p_adv.add_argument("--cwd", default=None)

    # link-related (A4): append (related: L<n>) to a source item
    p_link = sub.add_parser(
        "link-related",
        help="Append (related: L<n>) back-references to a matched source item",
    )
    p_link.add_argument("--pattern", required=True, help="Regex to match source item")
    p_link.add_argument("--target-line", dest="target_lines", type=int, nargs="+",
                        required=True, metavar="N",
                        help="1-indexed line number(s) of related existing items")
    p_link.add_argument("--file", default=None, help="Explicit TODO.md file path")
    p_link.add_argument("--project", default=None, help="Project directory")
    p_link.add_argument("--cwd", default=None)

    # is-audit-capture-confirmed (A5 consumer): exit 0 if confirmed, 1 if armed-unconfirmed,
    # 2 if not armed (no capture in progress)
    p_iacc = sub.add_parser(
        "is-audit-capture-confirmed",
        help="Exit 0 if audit capture confirmed for SESSION_ID",
    )
    p_iacc.add_argument("session_id")

    # validate-diary-link (DS7 #13a): pure predicate over provided text
    p_vdl = sub.add_parser("validate-diary-link",
                           help="Flag Done lines missing a [[date]] diary wikilink")
    p_vdl.add_argument("--text", required=True)

    # validate-todo-write (DS7 #13): hook entry — reads tool JSON from stdin
    sub.add_parser("validate-todo-write",
                   help="PostToolUse TODO.md diary-link check (reads tool JSON on stdin)")

    args = parser.parse_args()

    # Default mode: read (potentially from stdin JSON)
    if args.cmd is None:
        return _default_read_from_stdin()

    cwd_arg = getattr(args, "cwd", None) or os.getcwd()

    # S2/A2: serialize MUTATING commands under THE one bookkeeping flock so two
    # concurrent todo.py processes editing the same TODO.md (same repo) can
    # never lose an update. The lock is held HERE, around the whole command, so
    # it spans the read-modify-write (resolve -> parse -> mutate -> atomic
    # write) — not merely the file write, which alone would not close the
    # lost-update axis. Read-only commands take no lock. Keyed on the resolved
    # target's repo (per-repo lock = the unified main-writer rule S3 reuses);
    # degrades to unlocked if the module is unavailable or target unresolved.
    _MUTATING = {
        "add", "reframe", "mark-in-progress", "mark-in-phase",
        "promote-to-thought", "done", "cleanup", "advance-sessions",
        "link-related",
    }
    if args.cmd in _MUTATING:
        _target = _resolve_target_todo(
            getattr(args, "file", None), getattr(args, "project", None), cwd_arg
        )
        if _target is not None:
            try:
                from bookkeeping_lock import bookkeeping_lock, BookkeepingLockTimeout
            except Exception:
                return _dispatch(parser, args, cwd_arg)
            try:
                with bookkeeping_lock(_target):
                    return _dispatch(parser, args, cwd_arg)
            except BookkeepingLockTimeout as _e:
                print(json.dumps({"error": f"bookkeeping lock timeout: {_e}"}))
                return 1
    return _dispatch(parser, args, cwd_arg)


def _dispatch(parser, args, cwd_arg):
    """Command dispatch. Mutating commands run under the caller's A2 flock."""
    if args.cmd == "read":
        return cmd_read(cwd_arg, session_id=None, force=args.force)
    if args.cmd == "deps":
        return cmd_deps(args.text, cwd_arg)
    if args.cmd == "add":
        return cmd_add(
            args.text, args.bucket, args.tag, args.file, args.project, cwd_arg,
            require_confirm=getattr(args, "require_confirm", None),
            validate_framing=getattr(args, "validate_framing", False),
            phase=getattr(args, "phase", None),
            derives_from=getattr(args, "derives_from", None),
            require_evidence=getattr(args, "require_evidence", False),
        )
    if args.cmd == "validate-framing":
        result = cmd_validate_framing(args.text)
        print(json.dumps(result))
        return 0 if result["status"] == "pass" else 1
    if args.cmd == "validate-diary-link":
        result = cmd_validate_diary_link(args.text)
        print(json.dumps(result))
        return 0 if result["status"] == "pass" else 1
    if args.cmd == "validate-todo-write":
        return cmd_validate_todo_write(sys.stdin.read())
    if args.cmd == "reframe":
        return cmd_reframe(
            args.pattern, args.new_text, args.file, args.project, cwd_arg,
            supersede=args.supersede, bucket=args.bucket,
        )
    if args.cmd == "mark-in-progress":
        return cmd_mark_in_progress(
            args.pattern, args.session_id, args.file, args.project, cwd_arg,
        )
    if args.cmd == "mark-in-phase":
        return cmd_mark_in_phase(
            args.pattern, args.phase, args.session_id,
            args.file, args.project, cwd_arg,
        )
    if args.cmd == "audit-coverage":
        return cmd_audit_coverage(args.file, args.project, cwd_arg,
                                  surface=getattr(args, "surface", False))
    if args.cmd == "promote-to-thought":
        return cmd_promote_to_thought(args.pattern, args.file, args.project, cwd_arg)
    if args.cmd == "done":
        return cmd_done(args.pattern, args.file, args.project, args.text, cwd_arg)
    if args.cmd == "cleanup":
        return cmd_cleanup(args.project, cwd_arg)
    if args.cmd == "advance-sessions":
        return cmd_advance_sessions(
            args.pattern, args.file, args.project, cwd_arg, args.summary,
        )
    if args.cmd == "link-related":
        return cmd_link_related(
            args.pattern, args.target_lines, args.file, args.project, cwd_arg,
        )
    if args.cmd == "is-audit-capture-confirmed":
        # Delegate to pre_plan_gates.py is-audit-capture-confirmed
        import subprocess as _sp
        result = _sp.run(
            ["python3",
             str(Path(__file__).parent / "pre_plan_gates.py"),
             "is-audit-capture-confirmed",
             args.session_id],
            capture_output=True,
        )
        if result.returncode == 0:
            print(json.dumps({"confirmed": True, "session_id": args.session_id}))
            return 0
        else:
            print(json.dumps({"confirmed": False, "session_id": args.session_id}))
            return result.returncode

    parser.print_help()
    return 1


def _default_read_from_stdin():
    """Default mode: read JSON from stdin (hook) or fall back to manual."""
    session_id = None
    cwd = os.getcwd()

    if not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                data = json.loads(raw)
                session_id = data.get("session_id")
                cwd = data.get("cwd") or cwd
        except (json.JSONDecodeError, ValueError):
            pass

    return cmd_read(cwd, session_id=session_id)


if __name__ == "__main__":
    sys.exit(main())

"""InternalKBModule — Internal knowledge base path for /research scope-framing UI (S6).

Hexagonal port-and-adapter per ~/.claude/rules/code_first_architecture.md.
The flow controller calls discover_internal_sources() to find topic-internal
sources, then optionally runs the Q13 empty-state path-validation flow if no
sources are found, then reads the sources and synthesizes a _RESEARCH.md
whose claims carry Convention B markers.

THE READ IS PERFORMED BY THE PORT — AND THE TOOL BOUNDARY IS UNGATED (S7)
-------------------------------------------------------------------------
Before S7 this route was the one source class with a working read path and **no
declaration at all** — the class design-A24 names as "not the precedent to
follow". S7 closes that: :func:`read_declared_sources` performs the route's read
**through** ``source_port.AdmissionPort.admit()``, against a declaration the
person approved, and :func:`synthesize_from_admissions` composes the body from
**what the port returned** rather than re-reading the files. A path the
declaration does not cover comes back as a NAMED degradation the flow surfaces,
never as a silent omission.

**THIS IS A CONTRACT ON THE ROUTE'S READ PATH, NOT ON EVERY PHYSICALLY POSSIBLE
READ, AND THE DIFFERENCE IS NOT A DETAIL.** Nothing gates the tool boundary:
``settings.json`` registers **no PreToolUse hook on ``Read`` for source paths**
(its one PreToolUse ``Read`` matcher is ``check-pe-freshness.sh``, unrelated, and
its other registered ``Read`` hook, ``locked-read-track.sh``, is **PostToolUse**,
which fires after a read and so could not block one even in principle). So a read
that ignores this module entirely is still possible, and this module cannot stop
it. Gating that boundary is **design-A18**, which is **WITHDRAWN and routed to
`/clarification --from`**.

What that means for a reader of this file: the declaration here is **FOLLOWED,
not ENFORCED**. Do not cite this module as containment, and do not let a prose
pointer elsewhere read as enforcement it does not have — skill text has no
enforcement power (``code_first_architecture.md``: Code > Rules > Skill text),
and a person told the boundary is enforced when it is only followed has been told
the wrong thing. This is a large improvement on a route that declared nothing at
all, and it is less than containment.

Key contracts
-------------
* Skill does NOT call r0_intake — no manifest cycle, no scope-payload flags.
  The verification surface is engine-managed fc_cycles: frontmatter exclusively
  (PostToolUse factcheck-research-file.sh fires; _append_research_frontmatter
  writeback adds fc_cycles: automatically via file-path-driven dispatch at
  _factcheck_engine.py:1144-1147).
* Synthesis writer MUST preserve any pre-existing fc_cycles: frontmatter
  (sentinel-wrapped per _factcheck_engine.py:796-901; E2b coexistence rule).
* Convention B markers cite a source in the form it can bear (Q12 Edge 1):
  relative to Projects root when the source resolves inside the workspace,
  absolute when it resolves outside it (a cloned repo, a network share). The one
  refused shape is an inside-the-workspace source cited absolutely.
* Q12 Edge 2: walk-up for Thoughts/ staging topics resolves to Projects-root
  CLAUDE.md → citations prefixed with "(Projects-root CLAUDE.md — not topic-scoped)".
* Q12 Edge 3: _RESEARCH mtime > CLAUDE.md mtime → _RESEARCH as primary source.
* Q12 Edge 4: CLAUDE.md without a specific line → line-range or section-anchor.

Q13 path-validation flow (5 details):
  1. Input: folder paths, file paths, glob patterns one-per-line
  2. Validation: Glob expansion + existence check
  3. Confirmation surface: all-success / mixed / all-fail
  4. Glob rules: * ** ? [abc]; absolute paths accepted (expanded against their
     own root); reject **-no-dir-prefix; warn >100 files
  5. Empty content → Convention B [unverified — not found in internal KB]

Cockburn 4-step nano-increment:
  test-to-test  — InternalKBSources + emit_convention_b_marker with fake data
  real-to-test  — walk_up_topic_claude against real tmp_path structure
  test-to-real  — validate_user_paths against real tmp_path files
  real-to-real  — full discovery chain against live Thoughts/ folder
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import List, Optional, Sequence, Tuple

# Sentinel constants — must match _factcheck_engine.py exactly.
_FC_CYCLES_START = "# <!-- FC_CYCLES_START -->"
_FC_CYCLES_END = "# <!-- FC_CYCLES_END -->"

_OVER_LIMIT_THRESHOLD = 100


# --------------------------------------------------------------------------- #
# Source discovery
# --------------------------------------------------------------------------- #

@dataclass
class InternalKBSources:
    """Discovered internal sources for an Internal KB synthesis.

    Populated by discover_internal_sources(). When has_sources is False,
    the caller triggers the Q13 empty-state flow.
    """

    topic_folder: Path
    projects_root: Path
    # The closest CLAUDE.md found by walk-up (may be Projects-root CLAUDE.md).
    claude_md_path: Optional[Path] = None
    # True when claude_md_path is the Projects-root CLAUDE.md (staging topic).
    is_projects_root_claude: bool = False
    # _RESEARCH.md files found under topic_folder.
    research_files: Sequence[Path] = field(default_factory=list)
    # User-provided paths from the Q13 empty-state flow.
    user_paths: Sequence[Path] = field(default_factory=list)

    @property
    def all_source_paths(self) -> List[Path]:
        """Combined ordered list of all discovered source paths."""
        paths: List[Path] = []
        if self.claude_md_path and self.claude_md_path.exists():
            paths.append(self.claude_md_path)
        paths.extend(self.research_files)
        paths.extend(self.user_paths)
        return paths

    @property
    def has_sources(self) -> bool:
        return bool(self.all_source_paths)

    def primary_research_source(self) -> Optional[Path]:
        """Q12 Edge 3 recency rule: return the primary source for citations.

        When any _RESEARCH file's mtime > the CLAUDE.md's mtime, the most
        recently modified _RESEARCH file is primary; otherwise CLAUDE.md is.
        """
        if not self.claude_md_path or not self.claude_md_path.exists():
            return self.research_files[0] if self.research_files else None
        if not self.research_files:
            return self.claude_md_path

        claude_mtime = self.claude_md_path.stat().st_mtime
        newest_research = max(self.research_files, key=lambda p: p.stat().st_mtime if p.exists() else 0)
        if newest_research.exists() and newest_research.stat().st_mtime > claude_mtime:
            return newest_research
        return self.claude_md_path


def walk_up_topic_claude(
    topic_folder: Path,
    projects_root: Path,
) -> Tuple[Optional[Path], bool]:
    """Walk up from topic_folder to find the closest CLAUDE.md.

    Walks up to and including projects_root. Returns (path, is_projects_root_claude)
    per Q12 Edge 2. Returns (None, False) if no CLAUDE.md is found at any level.
    """
    current = topic_folder.resolve()
    root = projects_root.resolve()

    while True:
        candidate = current / "CLAUDE.md"
        if candidate.exists():
            is_root = current == root
            return candidate, is_root
        if current == root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    return None, False


def discover_research_files(topic_folder: Path) -> List[Path]:
    """Find all *_RESEARCH.md files in topic_folder (non-recursive)."""
    if not topic_folder.exists() or not topic_folder.is_dir():
        return []
    return sorted(p for p in topic_folder.glob("*_RESEARCH.md") if p.is_file())


# --------------------------------------------------------------------------- #
# The knowledge-library declaration key (S7, design-A21).
# --------------------------------------------------------------------------- #

# The CLAUDE.md key naming the folders that CONSTITUTE a project's knowledge
# library, so a person selecting "your knowledge library" is not asked to
# remember where it lives.
#
# **Why a new name rather than reusing `knowledge_library:`.** That key is
# declared as a Gate 1T opt-in (`plan-gates.md:460`) and already has TWO readers,
# both of which mean a KL *root path* by it — `/double-check`
# (`skills/double-check/SKILL.md:70`) and `/ninja-fix`
# (`skills/ninja-fix/SKILL.md:72`, `:85`). Reusing it would put a third meaning on
# one line, which design-A21 refuses. (The Gate 1T hook that looks like the
# obvious owner is not one: `check-kb-disposition-gate.sh:35` matches the
# anchored `^knowledge_library_index:`, which a bare `knowledge_library:` line
# cannot satisfy — so that key's declared home and its actual consumers are
# already different places, which is precisely the confusion not to add to.)
#
# The name says FOLDERS, plural, and says `research`, which is its single
# consumer. It cannot be confused with either existing key.
#
# This key has EXACTLY ONE reader: `read_research_library_folders` below.
RESEARCH_LIBRARY_FOLDERS_KEY = "research_library_folders"


def read_research_library_folders(
    claude_md_path: Optional[Path],
    projects_root: Path,
) -> List[Path]:
    """The folders a project declares as its knowledge library.

    Reads `research_library_folders:` from the CLAUDE.md the walk-up already
    found, as a **pointer to folders** — the CLAUDE.md itself is never quoted and
    never cited (the `topic-CLAUDE` marker pair is retired). This module already
    owns that walk-up, so it gains a second thing it reads from a file it already
    opens, not a second responsibility.

    Value form: one line, comma-separated, paths relative to `projects_root`
    (an absolute path is accepted and used as-is, for a library on a network
    share or outside the workspace).

    **A missing or empty key is not an error.** The class still has its
    pre-filled half — the topic's own `*_RESEARCH.md` files — so a project that
    never declared this key still produces a usable declaration. Returning an
    empty list rather than raising is what makes that true.

    Only folders that EXIST are returned. A declared folder that does not exist
    is dropped here and refused visibly at selection time by the picker's probe,
    which is where a person can still do something about it — not mid-run.
    """
    if claude_md_path is None or not claude_md_path.exists():
        return []
    try:
        text = claude_md_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    raw = ""
    for line in text.splitlines():
        if line.startswith(RESEARCH_LIBRARY_FOLDERS_KEY + ":"):
            raw = line.split(":", 1)[1]
            break
    if not raw.strip():
        return []

    folders: List[Path] = []
    for chunk in raw.split(","):
        name = chunk.strip()
        if not name:
            continue
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = projects_root / candidate
        candidate = candidate.resolve()
        if candidate.is_dir() and candidate not in folders:
            folders.append(candidate)
    return folders


def discover_internal_sources(
    topic_folder: Path,
    projects_root: Path,
) -> InternalKBSources:
    """Discover available internal sources for a topic.

    Returns an InternalKBSources instance. When has_sources is False, the caller
    triggers the Q13 empty-state path-validation flow.
    """
    claude_path, is_root = walk_up_topic_claude(topic_folder, projects_root)
    research = discover_research_files(topic_folder)
    return InternalKBSources(
        topic_folder=topic_folder,
        projects_root=projects_root,
        claude_md_path=claude_path,
        is_projects_root_claude=is_root,
        research_files=research,
    )


# --------------------------------------------------------------------------- #
# The production caller (S7, design-A24 / plan action A7) — NOW A DELEGATE.
#
# The route's read is PERFORMED BY the port. See this module's docstring for the
# ceiling on that sentence — the tool boundary is ungated (design-A18, withdrawn).
#
# WHY THESE ARE DELEGATES AND NOT DEFINITIONS ANY MORE
# (code-source-driver-bounded-read A2). The loop these names used to define was
# the ONLY generic declared-source read loop in production, and it served this
# route alone — while the three open-web routes that can declare a code base had
# no such loop at all. Cloning it for `code` would have bounded the new class and
# left these two unbounded, which is the same failure one level down. So the loop
# MOVED to `declared_read`, route-neutral, and gained a `code` arm; the run
# ceiling now applies to all three kinds at once.
#
# The route's behaviour is unchanged: same names, same signatures, same
# `internal-kb` run id. What changed underneath is that the ONE loop is now
# bounded — a knowledge-library scope that used to be read essentially whole now
# stops at the aggregate and says so through the refusal channel that already
# renders into the body.
# --------------------------------------------------------------------------- #

from . import declared_read as _declared_read  # noqa: E402

#: The value types are RE-EXPORTS, not subclasses or copies — the same class
#: objects `declared_read` defines, so a caller constructing one here and passing
#: it there is passing the identical type.
AdmittedReading = _declared_read.AdmittedReading
RefusedRead = _declared_read.RefusedRead
InternalKBReadResult = _declared_read.DeclaredReadResult


def read_declared_sources(
    scope,
    projects_root: Path,
    *,
    port=None,
    store=None,
    run_id: str = "internal-kb",
    query: Optional[str] = None,
) -> "InternalKBReadResult":
    """Read every declared internal source **through** ``port.admit()``.

    A delegate to :func:`declared_read.read_declared_sources`. The only thing this
    wrapper holds is the route's own default run id — a route-neutral module has
    no business defaulting to this route's name, and the records this route writes
    stay identifiable as its own.

    See the delegate for the loop itself: dispatch per kind, the enumeration
    bounds consumed from the port, and one aggregate budget for the whole run.
    """
    return _declared_read.read_declared_sources(
        scope, projects_root, port=port, store=store, run_id=run_id, query=query)


synthesize_from_admissions = _declared_read.synthesize_from_admissions


# --------------------------------------------------------------------------- #
# Q13 path-validation flow
# --------------------------------------------------------------------------- #

@dataclass
class PathValidationResult:
    """Outcome of the Q13 path-validation flow."""

    valid_paths: List[Path] = field(default_factory=list)
    failed_patterns: List[str] = field(default_factory=list)
    over_limit_warnings: List[str] = field(default_factory=list)

    @property
    def all_failed(self) -> bool:
        return bool(self.failed_patterns) and not self.valid_paths

    @property
    def mixed(self) -> bool:
        return bool(self.failed_patterns) and bool(self.valid_paths)

    @property
    def all_success(self) -> bool:
        return bool(self.valid_paths) and not self.failed_patterns


_GLOB_MAGIC = "*?["


def _has_glob_magic(part: str) -> bool:
    return any(ch in part for ch in _GLOB_MAGIC)


def _split_pattern(pattern: str, projects_root: Path) -> Tuple[Path, str]:
    """Split a declared pattern into (base directory, relative glob remainder).

    Admission is decided by where a pattern RESOLVES, never by how it is spelled:
    an absolute spelling anchors at the filesystem root, a relative one anchors at
    projects_root, and `..` segments are collapsed by resolve(). So a repo under a
    home directory and a folder on a network share — both absolute by nature — are
    expanded against their OWN root rather than against the workspace, which is
    what lets an accepted out-of-root location actually resolve to its files.

    The base takes every leading segment carrying no glob magic; the remainder is
    therefore always a relative pattern, which is the only kind Path.glob accepts.
    """
    norm = pattern.replace("\\", "/")
    pure = PurePosixPath(norm)

    if pure.is_absolute():
        base = Path("/")
        parts = pure.parts[1:]
    else:
        base = projects_root
        parts = pure.parts

    split_at = len(parts)
    for i, part in enumerate(parts):
        if _has_glob_magic(part):
            split_at = i
            break

    consumed = parts[:split_at]
    if consumed:
        base = base.joinpath(*consumed)
    remainder = "/".join(parts[split_at:])
    return base.resolve(), remainder


def _validate_pattern_structure(pattern: str) -> Optional[str]:
    """Return a plain-English error message if the pattern is structurally invalid.

    Rejects one shape only: patterns starting with `**` without a directory
    prefix, because rooted at a workspace root that enumerates the entire tree.

    An absolute path is NOT rejected — a repository a person has cloned and a
    folder on a network share are both absolute by nature, and both are locations
    a person may legitimately declare as a source.
    """
    if pattern.startswith("**"):
        return (
            f"The pattern '{pattern}' starts with '**' without a directory prefix. "
            "Use 'dir/**' instead."
        )
    return None


def validate_user_paths(
    raw_patterns: Sequence[str],
    projects_root: Path,
    over_limit: int = _OVER_LIMIT_THRESHOLD,
) -> PathValidationResult:
    """Q13 path-validation: expand patterns, confirm existence.

    Accepts folder paths, file paths, or glob patterns (one per entry), given
    absolutely or relative to projects_root. Supports *, **, ?, [abc] glob syntax.
    Only the bare-** pattern (no directory prefix) is rejected before expansion
    (structural validation); an absolute path is accepted and expanded against its
    own root, so an out-of-root location resolves to the files it names.

    projects_root is the anchor for RELATIVE patterns only — it does not bound
    what may be declared.

    over_limit: emit a warning (not an error) when a pattern matches more
    than this many files (editorial threshold, calibratable).
    """
    result = PathValidationResult()
    seen_paths: set = set()

    for raw in raw_patterns:
        pat = raw.strip()
        if not pat:
            continue

        # Structural validation — must happen before any filesystem access.
        struct_error = _validate_pattern_structure(pat)
        if struct_error:
            result.failed_patterns.append(pat)
            continue

        # Expansion against the pattern's OWN root — projects_root for a relative
        # spelling, the filesystem root for an absolute one (G2).
        try:
            base, remainder = _split_pattern(pat, projects_root)
            if remainder:
                expanded = sorted(base.glob(remainder))
            else:
                # No glob magic: the pattern names one concrete location.
                expanded = [base] if base.exists() else []
        except (ValueError, TypeError, NotImplementedError, OSError):
            result.failed_patterns.append(pat)
            continue

        files = [p for p in expanded if p.is_file()]

        if not files:
            result.failed_patterns.append(pat)
            continue

        # Warn if the pattern matches more than the editorial threshold.
        if len(files) > over_limit:
            result.over_limit_warnings.append(
                f"Pattern '{pat}' matched {len(files)} files — this is more than "
                f"{over_limit} files; consider narrowing the pattern."
            )

        for f in files:
            if f not in seen_paths:
                seen_paths.add(f)
                result.valid_paths.append(f)

    return result


# --------------------------------------------------------------------------- #
# Convention B marker emitter (Q12 edge-case defaults)
# --------------------------------------------------------------------------- #

CITATION_FORM_ABSOLUTE = "absolute"
CITATION_FORM_WORKSPACE_RELATIVE = "workspace-relative"


class CitationFormError(ValueError):
    """A citation was asked for in a form its source must not be given.

    Carries the reason in its message: a refusal that does not say which case
    fired is unactionable.
    """


def _emit_source_path(
    path: Path,
    projects_root: Path,
    form: Optional[str] = None,
) -> str:
    """Return the citation form of `path` — the one its source can actually bear.

    Emission follows the source, not the workspace (Q12 Edge 1):

      * resolves INSIDE projects_root  → workspace-relative
      * resolves OUTSIDE projects_root → absolute, by stated rule, because that
        is the only expression such a source has. A network share can never be
        written relative to the workspace root, so rewriting it would produce a
        citation nobody can reopen.

    `form` lets a caller state the shape it wants; the default follows the source.
    Exactly ONE shape is refused: an inside-the-workspace location cited
    absolutely, because it is the only case where the alternative is strictly
    better — the workspace-relative form survives the workspace moving and the
    absolute form does not. Asking for a workspace-relative form for an
    out-of-root source is NOT refused; it follows the source instead, since the
    requested form does not exist for it.

    Raises CitationFormError for the one refused shape, naming which case fired.
    """
    resolved = path.resolve()
    root = projects_root.resolve()

    try:
        inside = resolved.relative_to(root)
    except ValueError:
        inside = None

    if inside is None:
        # Out of the workspace: absolute is the source's only expression.
        return str(resolved).replace("\\", "/")

    rel = str(inside).replace("\\", "/")
    if form == CITATION_FORM_ABSOLUTE:
        raise CitationFormError(
            f"Refused: '{resolved}' is inside the workspace root '{root}', so it "
            "must not be cited as an absolute path. A workspace-relative citation "
            f"('{rel}') still resolves after the workspace moves; an absolute one "
            "does not. Cite it relatively, or declare it from outside the "
            "workspace if it genuinely lives there."
        )
    return rel


def emit_convention_b_marker(
    quote_type: str,
    source_kind: str = "local-file",
    path: Optional[Path] = None,
    locator: str = "",
    projects_root: Optional[Path] = None,
    is_projects_root_claude: bool = False,
    assessment_text: str = "",
    form: Optional[str] = None,
) -> str:
    """Generate a Convention B marker string per the Marker Contract.

    quote_type:
      "stated"      → [stated — <source>:<path>:<locator>]
      "paraphrased" → [paraphrased — <source>:<path>:<locator>]
      "inferred"    → [inferred from internal sources]
      "unverified"  → [unverified — not found in internal knowledge base]
      "assessment"  → [My assessment: <text>]

    source_kind: "local-file" | "topic-CLAUDE"

    locator: line number, line-range (e.g. "120-135"), or section anchor
             (e.g. "#section-heading"). Q12 Edge 4: AI picks based on clarity.

    is_projects_root_claude (Q12 Edge 2): when True and source_kind="topic-CLAUDE",
    prepend "(Projects-root CLAUDE.md — not topic-scoped) " to the marker.

    form: the citation shape to emit — None (default) follows the source,
    CITATION_FORM_ABSOLUTE / CITATION_FORM_WORKSPACE_RELATIVE state one
    explicitly. An inside-the-workspace path asked for absolutely raises
    CitationFormError; every other combination is emitted by the stated rule.
    See _emit_source_path.
    """
    if quote_type == "inferred":
        return "[inferred from internal sources]"
    if quote_type == "unverified":
        return "[unverified — not found in internal knowledge base]"
    if quote_type == "assessment":
        return f"[My assessment: {assessment_text}]"

    if path is None:
        return f"[{quote_type} — {source_kind}:unknown]"

    if projects_root is not None:
        rel = _emit_source_path(path, projects_root, form=form)
    else:
        rel = str(path).replace("\\", "/")

    loc_part = f":{locator}" if locator else ""
    marker = f"[{quote_type} — {source_kind}:{rel}{loc_part}]"

    if is_projects_root_claude and source_kind == "topic-CLAUDE":
        marker = f"(Projects-root CLAUDE.md — not topic-scoped) {marker}"

    return marker


# --------------------------------------------------------------------------- #
# Synthesis writer — fc_cycles frontmatter preservation (E2b coexistence)
# --------------------------------------------------------------------------- #

def extract_fc_cycles_block(text: str) -> Optional[str]:
    """Extract the raw sentinel-wrapped fc_cycles block text, or None."""
    if _FC_CYCLES_START not in text or _FC_CYCLES_END not in text:
        return None
    start = text.index(_FC_CYCLES_START)
    end = text.index(_FC_CYCLES_END) + len(_FC_CYCLES_END)
    return text[start:end]


def compose_research_file(new_body: str, existing_text: Optional[str] = None) -> str:
    """Compose a _RESEARCH.md body, preserving any pre-existing fc_cycles block.

    E2b coexistence rule: Convention B body markers are author-managed;
    the fc_cycles: YAML frontmatter written by _append_research_frontmatter is
    engine-managed. Re-synthesis appends body content without touching the
    sentinel-wrapped frontmatter block.

    When existing_text is None (first synthesis), returns new_body unchanged.
    When existing_text has a fc_cycles block, prepends it verbatim to new_body.
    """
    if existing_text is None:
        return new_body

    existing_block = extract_fc_cycles_block(existing_text)
    if existing_block is None:
        return new_body

    # If the new body already has the sentinel (shouldn't happen in normal flow),
    # return as-is to avoid duplication.
    if _FC_CYCLES_START in new_body:
        return new_body

    return existing_block + "\n\n" + new_body

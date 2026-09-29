#!/usr/bin/env python3
"""Per-source-type anchor resolver (Slice S5) — the single change locus (A6).

Each claim carries an auditable anchor keyed to its source type. Adding a new
source type is ONE entry in `ANCHOR_SCHEMES` — nothing else in the engine changes
(Cockburn Evolution Test: the resolver is the only place that knows anchor formats).

  LOCAL_FILE  -> <path>:<line>
  PDF         -> <path>#page=<page>
  EPUB        -> <path>#loc=<loc>
  WEB         -> <url>#<fragment>
  TRANSCRIPT  -> <path>@<timestamp>
  CODE        -> <repo>@<rev>:<path>:<lines>          (S12)
  LINEAR      -> <workspace>@<version>:<issue>        (S12)

The resolver reads the locator fields a Stage-2 row supplies for that source type;
a missing required field is a SchemaError (structured-output discipline, A13).

The two S12 forms are the citation vocabulary's own pin segments, so a claim
lifted from a `[stated — code:…]` / `[stated — linear:…]` citation anchors to the
address the report already carried rather than to the line that quoted it.

**What an anchor of these kinds does and does not assert.** It makes the address
well-formed and re-openable; it does NOT make it true, and it does not establish
that the source was read. A port-minted `code:` pin and a model-authored one are
the same bytes, and nothing here can tell them apart.
"""

from __future__ import annotations

from _claim_engine import Anchor, SchemaError, SourceType


def _local_file(source, row):
    """`<path>:<line>`.

    A Stage-2 row supplies a numeric line and is coerced exactly as before. A
    line reference lifted from a CITATION may legitimately be a RANGE
    (`120-135`, the Marker Contract's Edge 4), which `int()` cannot take, so a
    non-numeric reference is rendered as given. This WIDENS what resolves and
    changes nothing for a numeric row: `int("42")` and `"42"` render the same.
    """
    line = row["source_line"]
    try:
        line = int(line)
    except (TypeError, ValueError):
        line = str(line)
    return f"{source.path}:{line}"


def _pdf(source, row):
    return f"{source.path}#page={int(row['page'])}"


def _epub(source, row):
    return f"{source.path}#loc={row['loc']}"


def _web(source, row):
    frag = row.get("fragment", "")
    return f"{source.path}#{frag}" if frag else source.path


def _transcript(source, row):
    return f"{source.path}@{row['timestamp']}"


def _code(source, row):
    """`<repo>@<rev>:<path>:<lines>` — the `code` citation's own pin segment.

    `source.path` carries the repository identity here, which is what a `code:`
    pin names; the address WITHIN the repository is the row's own path + lines.
    """
    return f"{source.path}@{row['rev']}:{row['source_path']}:{row['lines']}"


def _linear(source, row):
    """`<workspace>@<version>:<issue>` — the `linear` citation's own pin segment.

    `source.path` carries the workspace identity. A comment id, when the claim
    came from a comment rather than the issue body, is part of `issue` — the
    citation vocabulary declares no separate placeholder for it.
    """
    return f"{source.path}@{row['version']}:{row['issue']}"


# THE single change locus: source_type -> (locator formatter, required row fields).
ANCHOR_SCHEMES = {
    SourceType.LOCAL_FILE: (_local_file, ("source_line",)),
    SourceType.PDF: (_pdf, ("page",)),
    SourceType.EPUB: (_epub, ("loc",)),
    SourceType.WEB: (_web, ()),            # fragment optional
    SourceType.TRANSCRIPT: (_transcript, ("timestamp",)),
    SourceType.CODE: (_code, ("rev", "source_path", "lines")),
    SourceType.LINEAR: (_linear, ("version", "issue")),
}


def resolve(source, row) -> Anchor:
    """Build the per-source-type Anchor for one Stage-2 claim row."""
    scheme = ANCHOR_SCHEMES.get(source.source_type)
    if scheme is None:
        raise SchemaError(f"no anchor scheme registered for {source.source_type}")
    fmt, required = scheme
    missing = [k for k in required if k not in row]
    if missing:
        raise SchemaError(
            f"{source.source_type.value} anchor needs {missing} in the claim row: {row!r}")
    return Anchor(source_type=source.source_type, locator=fmt(source, row))


def supported_source_types():
    return tuple(ANCHOR_SCHEMES.keys())


# ─────────────────────────────────────────────────────────────────────────────
# Citation → anchor  (S12/A6 — design-A23)
#
# The harvest lifts claims out of a research report, where the address is carried
# by a citation marker rather than by a Stage-2 row. This maps one to the other
# HERE, so the harvest never learns an anchor format — which is the property the
# module docstring above claims for itself and which a second formatter in the
# harvest would quietly end.
# ─────────────────────────────────────────────────────────────────────────────

# Citation locator kind (`_factcheck_engine.CITATION_MARKER_REGISTRY`) →
# (source type, the field carrying the source identity, the remaining row fields).
_CITATION_TO_SOURCE_TYPE = {
    "local-file": (SourceType.LOCAL_FILE, "path", {"source_line": "line"}),
    # Retired but still RECOGNISED (forward-only retirement, design-A27): a report
    # already carrying one must still anchor rather than fail.
    "topic-CLAUDE": (SourceType.LOCAL_FILE, "path", {"source_line": "line"}),
    "code": (SourceType.CODE, "repo",
             {"rev": "rev", "source_path": "path", "lines": "lines"}),
    "linear": (SourceType.LINEAR, "workspace",
               {"version": "version", "issue": "issue"}),
}


class _CitationSource:
    """The minimal `source` shape `resolve` reads — identity only.

    Deliberately not a `Source`: that dataclass carries the source's full TEXT,
    and the harvest has read no source. Constructing one would imply it had.
    """

    __slots__ = ("source_type", "path")

    def __init__(self, source_type, path):
        self.source_type = source_type
        self.path = path


def anchor_from_citation(locator_kind, parts) -> Anchor:
    """Build the Anchor a citation's own address implies.

    `parts` is the named-part mapping `_factcheck_engine.parse_citations` extracts
    per the registry's payload shape. Raises `SchemaError` for a locator kind with
    no anchor form, or for a part set that cannot fill one — the caller REFUSES the
    claim rather than anchoring it somewhere else, because an anchor that silently
    fell back to the quoting line is the defect design-A23 removes.
    """
    mapping = _CITATION_TO_SOURCE_TYPE.get(locator_kind)
    if mapping is None:
        raise SchemaError(f"no anchor form for citation locator {locator_kind!r}")
    source_type, identity_field, row_fields = mapping
    identity = (parts or {}).get(identity_field)
    if not identity:
        raise SchemaError(
            f"{locator_kind} citation supplies no {identity_field!r} to anchor to")
    row = {}
    for row_key, part_key in row_fields.items():
        value = (parts or {}).get(part_key)
        if value in (None, ""):
            raise SchemaError(
                f"{locator_kind} citation is missing {part_key!r}, which its "
                "anchor requires")
        row[row_key] = value
    return resolve(_CitationSource(source_type, identity), row)


def citation_locator_kinds():
    """The citation locator kinds that have an anchor form."""
    return tuple(_CITATION_TO_SOURCE_TYPE)

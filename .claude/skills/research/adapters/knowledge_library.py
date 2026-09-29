"""The knowledge-library adapter — a person's own library of notes and research.

research-source-adapters S7 / plan action A5 (design-A21, design-A24).

This is the class design-A24 names as **the precedent not to follow**: before S7
the knowledge library was the one source with a working read path and *no
declaration at all*, so the class the design most wanted inside the contract was
the one proving the contract optional. S7 is therefore a subtraction as much as an
addition — the adapter below is the addition; the subtraction is that the route
now reads through ``port.admit()`` against an approved declaration
(``internal_kb.py``).

**Three things and no more**, inherited whole from
:class:`~.document_folder.OnDiskFolderAdapter`: lazy enumeration holding no bound
constant, one read, one declared re-open capability
(``can_reopen_without_credentials = True`` — the file is on disk and a checker
holding no credentials can open it).

**Why this file is one line of behaviour.** ``knowledge_library`` and
``document_folder`` are two distinct SOURCE classes — a person recognises them as
different things, picks them from different rows, and declares them differently
(a library's folders are filled in from the project's own ``CLAUDE.md`` key; a
document folder is named by hand) — but *reading a file on disk* does not vary by
which class declared it. Two copies of that logic would be a split defensible only
by a predicted future in which the two READ differently, and the design names no
such future. What genuinely differs is the ``kind``, which selects the locator
kind through the 1:1 locator↔source mirror, and the declaration surface, which
lives in the picker and in ``internal_kb.py`` rather than here.

The two kinds nonetheless emit ONE citation vocabulary —
``[stated — local-file:<path>:<line>]`` — and that sharing is explicit rather than
accidental: it is ``KindRules.citation_prefix``, not a shared locator kind. The
locator kinds stay separate because ``KIND_RULES`` is keyed by the locator kind in
``render()`` and by the source kind in ``admit()``, so one shared kind would make
those two lookups disagree.
"""

from __future__ import annotations

from ..scope_record import KIND_KNOWLEDGE_LIBRARY
from .document_folder import OnDiskFolderAdapter


class KnowledgeLibraryAdapter(OnDiskFolderAdapter):
    """Reads a person's declared knowledge-library folders."""

    kind = KIND_KNOWLEDGE_LIBRARY

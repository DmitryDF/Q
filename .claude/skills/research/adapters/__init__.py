"""Source adapters for the research admission port.

research-source-adapters S3 / plan action A5.

Each adapter implements ``source_port.SourceAdapter`` and is deliberately thin:
lazy enumeration within the declared scope, a read that returns content plus the
facts the pin needs, and one self-declared re-open capability flag. Every
obligation — the pin, the scope check, the prohibition, the four bounds, the
evidence-path choice — belongs to the port, not here. If an adapter grows logic a
second adapter would also need, that logic is in the wrong module.

Ships in S3:
  * code_base — the repository adapter (the first source class).

Ships in S6 (design-A29):
  * web — the web adapter. The class research already used most, brought behind
    the same contract rather than left exempt from it. Its fetch is INJECTED,
    because the loop that owns the per-URL fair share and the wall-clock cap is
    the fact-check engine's and cannot move into an adapter that must hold no
    bound.

Ships in S7 (design-A21, A24, A31):
  * document_folder — a folder of documents on disk. Carries
    ``OnDiskFolderAdapter``, the on-disk read both S7 classes share. A MOUNTED
    CLOUD DRIVE gets no branch of its own: design-A31 admits it *unconditionally,
    as the ordinary folder it is*, and a test for one would make "ordinary"
    conditional on passing a test.
  * knowledge_library — a person's own library of notes and research. The class
    design-A24 names as the precedent NOT to follow: until S7 it was the one
    source with a working read path and no declaration at all. Its adapter is one
    line of behaviour because reading a file on disk does not vary by which class
    declared it; what differs is the declaration surface, which lives in the
    picker and ``internal_kb.py``, not here.

    The two S7 classes emit ONE citation vocabulary
    (``[stated — local-file:<path>:<line>]``) while keeping SEPARATE locator
    kinds. That is deliberate, not an inconsistency: ``KIND_RULES`` is keyed by
    the locator kind in ``render()`` and by the source kind in ``admit()``, so a
    single shared locator kind would make those lookups disagree. The sharing is
    expressed as ``KindRules.citation_prefix`` — explicitly, rather than by
    naming.
"""

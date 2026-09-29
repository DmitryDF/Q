"""The document-folder adapter — a folder of documents on disk.

research-source-adapters S7 / plan action A5 (design-A21, design-A24's read-only
half, design-A31).

Three things and no more, exactly as ``code_base.py``:

* ``enumerate_within`` — a **plain generator** yielding candidates lazily, applying
  **no bound** and holding **no bound constant**. The port consumes it and stops at
  its own limits, which is what makes "the adapter cannot widen a bound" structural
  rather than a convention.
* ``read_with_pin`` — reads one item and returns the facts the pin needs (a source
  identity, the version read, a ``(path, line)`` locator).
* ``can_reopen_without_credentials`` — a **declared capability**, not a decision.
  ``True`` here: the file is on disk and a checker holding no credentials can open
  it. Whether the ORIGINAL branch is selected is still the port's call.

**A MOUNTED CLOUD DRIVE GETS NO BRANCH — that IS design-A31.**
The decision admits a locally-mounted Drive folder *unconditionally, as the
ordinary document folder it is*. A branch testing for one would contradict the
decision it implements: it would make "ordinary" conditional on passing a test,
which is the opposite of unconditional. So there is deliberately no mount
detection, no cloud-path special case and no provider list in this file, and the
mounted-Drive case is covered by a test asserting that a mounted path takes the
same path through this module as any other.

**Read-only** (design-A24). No write path exists: no ``open`` in a write mode, no
``os.remove``, no ``Path.write_*``, no ``shutil``, and — unlike ``code_base.py`` —
no ``subprocess`` at all, because an on-disk file needs no VCS to answer what it
is or when it was read.

**The version is the read instant, and the record says so rather than implying
the file is pinned.** A repository exposes a commit; a plain file exposes only its
modification time. So ``version`` is the file's mtime in ISO-8601 UTC, and the
port's re-open instruction for these kinds says the file "was read at <version>
… unless it has been edited since" rather than ``code``'s "the working tree was
clean at <id>@<version>". Saying the latter about a plain file would be false —
that is the third job S7 split out of ``KindRules.path_shaped``
(``source_port.version_is_revision``).

**No CLAUDE.md exclusion here, deliberately.** A ``CLAUDE.md`` discovered beneath
a declared folder IS yielded, reaches ``admit()`` like any other item, and leaves
a recorded degradation — the same reasoning ``code_base.py`` gives for the same
case. A silent skip is the failure mode the prohibition must not have. (Its
sibling guard, refusing a *declared* path that names a CLAUDE.md, fires earlier
still, at record construction.)
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from ..locator_grammar import Locator
from ..scope_record import KIND_DOCUMENT_FOLDER, ScopeRecord
from ..source_port import (
    AdapterError,
    OBLIGATION_READABLE,
    OBLIGATION_SOURCE_IDENTITY,
    OBLIGATION_VERSION_READ,
    ReadResult,
    SourceAdapter,
    SourceItem,
)

# Directory names enumeration never descends into. VCS and tooling metadata is
# not document content and is not a candidate the port has any decision to make
# about — the same structural exclusion `code_base.py` states for `.git`.
_SKIP_DIRS = {".git", "__pycache__", ".obsidian"}


class OnDiskFolderAdapter(SourceAdapter):
    """Reads plain files beneath declared folders. Holds no bound, mints no pin.

    **Why one implementation with two thin bindings, rather than two files of
    duplicated logic.** ``knowledge_library`` and ``document_folder`` are two
    distinct SOURCE classes — a person recognises them as different things and
    declares them differently — but *reading a file on disk* does not vary by
    which class declared it. Cockburn's Fatness Tradeoff says to start fat and
    stop splitting as soon as possible; two copies of this logic would be a split
    defensible only by a predicted future in which the two read differently, and
    no such future is named anywhere in the design.

    What genuinely differs is carried by ``kind`` alone, which selects the locator
    kind through the 1:1 locator↔source mirror. So the subclasses below are one
    line each, and any drift between the classes would have to be deliberate.
    """

    # Bound by the subclasses. Kept abstract here so this base is never itself
    # registered as an adapter for a kind nobody declared.
    kind: str = ""

    can_reopen_without_credentials = True

    def __init__(self, projects_root: Optional[Path] = None) -> None:
        """`projects_root` decides the CITATION SHAPE, not what may be read.

        The Marker Contract's Edge 1 rule: a source resolving INSIDE the
        workspace is cited relative to it, and one resolving OUTSIDE it — a cloned
        repository, a folder on a network share, a mounted drive — is cited
        absolutely, because that is the only expression it has. The one shape
        refused outright is an inside-the-workspace source cited absolutely.

        Containment is not this parameter's job and never becomes it: what may be
        read is the declaration's business (`ScopeRecord.check`), enforced by the
        port. Passing no root simply means every citation is absolute.
        """
        self.projects_root = (
            Path(projects_root).expanduser().resolve(strict=False)
            if projects_root is not None else None
        )

    # -- enumeration -------------------------------------------------------- #

    def enumerate_within(self, scope: ScopeRecord) -> Iterator[SourceItem]:
        """Yield every file under each declared selector of this kind, lazily.

        `depth` is reported as DATA (levels below the declared selector); the port
        holds the constant and compares. Nothing here counts items, caps a walk,
        or sizes a read.
        """
        for selector in scope.selectors_for(self.kind):
            root = Path(selector).expanduser()
            if root.is_file():
                yield SourceItem(item_id=str(root.resolve(strict=False)),
                                 kind=self.kind, target=str(root), depth=0)
                continue
            if not root.is_dir():
                # A declared folder that does not exist is not an error HERE — the
                # picker's probe refuses it visibly at selection time, which is
                # where a person can still do something about it.
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
                here = Path(dirpath)
                try:
                    depth = len(here.relative_to(root).parts)
                except ValueError:
                    depth = 0
                for name in sorted(filenames):
                    target = here / name
                    yield SourceItem(
                        item_id=str(target.resolve(strict=False)),
                        kind=self.kind, target=str(target), depth=depth)

    # -- read --------------------------------------------------------------- #

    def read_with_pin(self, item: SourceItem) -> ReadResult:
        """Read one file and report what the pin needs.

        Raises :class:`AdapterError` naming the failed obligation — the port turns
        it into a recorded degradation. This adapter never decides that an item is
        rejected; it reports which obligation it could not satisfy, and never
        returns a half-answer.
        """
        path = Path(item.target).expanduser()
        resolved = path.resolve(strict=False)

        try:
            stat = path.stat()
        except OSError as e:
            raise AdapterError(
                OBLIGATION_VERSION_READ,
                f"cannot read a modification time for {item.target}: "
                f"{type(e).__name__}: {e}") from None
        version = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc).isoformat(timespec="seconds")
        if not version:
            raise AdapterError(
                OBLIGATION_VERSION_READ,
                f"{item.target} exposes no modification time to pin")

        try:
            raw = path.read_bytes()
        except OSError as e:
            raise AdapterError(OBLIGATION_READABLE,
                               f"{type(e).__name__}: {e}") from None
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            # Not text, so not citable by a line locator. Degrade rather than
            # hand the port a garbled excerpt.
            raise AdapterError(
                OBLIGATION_READABLE,
                f"{item.target} is not UTF-8 text and cannot be addressed by a "
                "line locator") from None

        cite_path = self._citation_path(resolved)
        if not cite_path:
            raise AdapterError(
                OBLIGATION_SOURCE_IDENTITY,
                f"cannot derive a citable path for {item.target}")

        n_lines = content.count("\n") + (
            0 if content.endswith("\n") or not content else 1)
        line = f"1-{n_lines}" if n_lines else "1"

        return ReadResult(
            source_id=cite_path,
            version=version,
            locator=Locator(kind=self.kind, parts={"path": cite_path, "line": line}),
            content=content,
        )

    # -- citation shape ------------------------------------------------------ #

    def _citation_path(self, resolved: Path) -> str:
        """The path AS CITED — Marker Contract Edge 1.

        Relative to the workspace when the source resolves inside it (so the
        citation survives the workspace being moved); absolute when it resolves
        outside, because a location outside the root has no relative expression
        and rewriting it would produce a citation nobody can reopen.
        """
        if self.projects_root is not None:
            try:
                return str(resolved.relative_to(self.projects_root))
            except ValueError:
                pass
        return str(resolved)


class DocumentFolderAdapter(OnDiskFolderAdapter):
    """A folder of documents the person named themselves.

    A mounted cloud-drive folder arrives here as an ordinary path and is read by
    the inherited implementation with no branch of its own — design-A31.
    """

    kind = KIND_DOCUMENT_FOLDER

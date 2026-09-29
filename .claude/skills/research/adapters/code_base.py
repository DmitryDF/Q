"""The code-base adapter — the first source class, and deliberately the thinnest.

research-source-adapters S3 / plan action A5 (design-A2, design-A20, design-A24's
read-only half).

Three things and no more:

* ``enumerate_within`` — a **plain generator** that yields candidates lazily,
  applies **no bound** and holds **no bound constant**. The port consumes it and
  stops at its own limits, which is what makes "the adapter cannot widen a bound"
  structural rather than a convention.
* ``read_with_pin`` — reads one item and returns the facts the pin needs (repo
  identity, the commit read, a ``(path, lines)`` locator) plus a ``dirty`` flag.
* ``can_reopen_without_credentials`` — a **declared capability**, not a decision.
  The port reads it together with ``dirty`` and selects the evidence branch. An
  adapter that chose its own branch would not be thin (Responsibility Alignment).

**Read-only** (design-A24). No write path exists: no ``open`` in a write mode, no
``os.remove``, no ``Path.write_*``, no ``shutil``. ``subprocess`` is used and is
NOT banned — there is no pure-Python way to resolve a commit and a dirty flag in
this codebase and none is being added — but every invocation goes through
:func:`_git`, whose subcommand is checked at runtime against
:data:`GIT_READONLY_SUBCOMMANDS` and statically by the slice's AST scan. A flat
``subprocess`` ban would fail every correct implementation of this module; a scan
that distinguishes reading from writing is the honest form.

**One structural exclusion, stated rather than hidden.** Enumeration does not
descend into ``.git`` directories: VCS metadata is not source content and is not
a candidate the port has any decision to make about. This is NOT the CLAUDE.md
case — a ``CLAUDE.md`` inside a declared directory IS yielded, reaches
``admit()`` like any other item, and leaves a recorded degradation, because a
silent skip is the failure mode the prohibition must not have.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Iterator, Optional, Tuple

from ..locator_grammar import code_locator
from ..scope_record import KIND_CODE, ScopeRecord
from ..source_port import (
    AdapterError,
    OBLIGATION_READABLE,
    OBLIGATION_SOURCE_IDENTITY,
    OBLIGATION_VERSION_READ,
    ReadResult,
    SourceAdapter,
    SourceItem,
)

# Every git subcommand this module may invoke. Read-only by inspection, and
# enforced at runtime by `_git` as well as statically by the slice's AST scan —
# two layers, because a static scan alone is defeatable by dynamic dispatch and
# says so in the plan's Gate 2 ("Code (bounded)").
GIT_READONLY_SUBCOMMANDS = (
    "rev-parse", "status", "ls-files", "rev-list", "show", "cat-file", "config",
)

# Directory names enumeration never descends into. VCS metadata only.
_SKIP_DIRS = {".git"}

_GIT_TIMEOUT_SECONDS = 20


class _GitUnavailable(AdapterError):
    """git itself could not answer — distinct from "the repo has no commits"."""


def _git(subcommand: str, *args: str, cwd: str) -> str:
    """Run ONE read-only git subcommand and return its stdout, stripped.

    The single ``subprocess`` call site in this module. `subcommand` is checked
    against :data:`GIT_READONLY_SUBCOMMANDS` before anything is spawned, so a
    write-capable subcommand cannot be reached even by a caller inside this file.
    Raises :class:`AdapterError` on a non-zero exit — never a partial answer.
    """
    if subcommand not in GIT_READONLY_SUBCOMMANDS:
        raise AdapterError(
            OBLIGATION_READABLE,
            f"git subcommand {subcommand!r} is not on the read-only allowlist "
            f"{list(GIT_READONLY_SUBCOMMANDS)}")
    argv = ["git", "-C", str(cwd), subcommand, *args]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        raise _GitUnavailable(OBLIGATION_READABLE,
                              f"could not run {' '.join(argv)}: {e}") from None
    if proc.returncode != 0:
        raise AdapterError(
            OBLIGATION_READABLE,
            f"`git {subcommand}` failed in {cwd}: "
            f"{(proc.stderr or '').strip() or f'exit {proc.returncode}'}")
    return (proc.stdout or "").strip()


def _repo_identity(repo_root: Path) -> str:
    """The repository's identity for the pin — stable across checkout location.

    Prefers the ``origin`` remote's repository name, falling back to the checkout
    directory's basename when there is no remote. The fallback alone is NOT
    good enough and this is not a refinement: a submodule's checkout name is
    chosen by the superproject (``git submodule add <donor> sub`` yields a
    toplevel basename of ``sub``), and the same upstream vendored at two paths
    would pin under two different identities. A pin has to be re-openable months
    later, so its identity part must not be a property of where someone happened
    to put the clone.

    A repo with no remote keeps the basename, which is the only identity it has.
    """
    try:
        url = _git("config", "--get", "remote.origin.url", cwd=str(repo_root))
    except AdapterError:
        url = ""                      # no remote configured — exit 1, not an error
    if url:
        name = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        if name:
            return name
    return repo_root.name


class CodeBaseAdapter(SourceAdapter):
    """Reads a repository. Holds no bound, mints no pin, chooses no branch."""

    kind = KIND_CODE

    # Declared capability, read by the port. A repository on disk can be
    # re-opened by a checker holding no credentials — but whether the ORIGINAL
    # can be re-opened *at the state that was read* also depends on the dirty
    # flag this adapter reports, and combining the two is the port's decision.
    can_reopen_without_credentials = True

    # -- enumeration -------------------------------------------------------- #

    def enumerate_within(self, scope: ScopeRecord) -> Iterator[SourceItem]:
        """Yield every file under each declared code selector, lazily.

        `depth` is reported as DATA (levels below the declared selector); the
        port holds the constant and compares. Nothing here counts items, caps a
        walk, or sizes a read.
        """
        for selector in scope.selectors_for(KIND_CODE):
            root = Path(selector).expanduser()
            if root.is_file():
                yield SourceItem(item_id=str(root.resolve(strict=False)),
                                 kind=KIND_CODE, target=str(root), depth=0)
                continue
            if not root.is_dir():
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
                        kind=KIND_CODE, target=str(target), depth=depth)

    # -- read --------------------------------------------------------------- #

    def read_with_pin(self, item: SourceItem) -> ReadResult:
        """Read one file and report what the pin needs.

        Raises :class:`AdapterError` naming the failed obligation — the port
        turns it into a recorded degradation. This adapter never decides that an
        item is rejected; it reports which obligation it could not satisfy.
        """
        path = Path(item.target).expanduser()
        parent = str(path.parent)

        # Repo identity + the commit read. `rev-parse --show-toplevel` run from
        # the FILE's own directory resolves to the repository that actually owns
        # the file — a submodule resolves to the submodule, not the outer repo
        # (E7). A pin naming the wrong repo's commit is worse than no pin.
        toplevel = _git("rev-parse", "--show-toplevel", cwd=parent)
        if not toplevel:
            raise AdapterError(OBLIGATION_SOURCE_IDENTITY,
                               f"{item.target} is not inside a git repository")
        repo_root = Path(toplevel)
        source_id = _repo_identity(repo_root)
        if not source_id:
            raise AdapterError(OBLIGATION_SOURCE_IDENTITY,
                               f"cannot derive a repository identity from {toplevel!r}")

        try:
            version = _git("rev-parse", "HEAD", cwd=str(repo_root))
        except AdapterError as e:
            # E3 — a freshly-initialised repo has no version identity, so the
            # "version read" part of the pin is unsatisfiable. Re-labelled from
            # the generic read failure so the record names the real obligation.
            raise AdapterError(
                OBLIGATION_VERSION_READ,
                f"repository {source_id!r} exposes no commit to pin ({e.reason})"
            ) from None

        # Dirty is reported per FILE, not per tree: an unrelated edit elsewhere
        # does not make this file's pinned commit wrong about these bytes.
        porcelain = _git("status", "--porcelain", "--", str(path),
                         cwd=str(repo_root))
        dirty = bool(porcelain.strip())

        try:
            raw = path.read_bytes()
        except OSError as e:
            raise AdapterError(OBLIGATION_READABLE,
                               f"{type(e).__name__}: {e}") from None
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            # E5 — not text, so not citable by a line locator. Degrade rather
            # than hand the port a garbled excerpt.
            raise AdapterError(
                OBLIGATION_READABLE,
                f"{item.target} is not UTF-8 text and cannot be addressed by a "
                "line locator") from None

        try:
            rel = str(path.resolve(strict=False).relative_to(repo_root.resolve(strict=False)))
        except ValueError:
            rel = path.name
        n_lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
        lines = f"1-{n_lines}" if n_lines else "1"

        return ReadResult(source_id=source_id, version=version,
                          locator=code_locator(rel, lines),
                          content=content, dirty=dirty)

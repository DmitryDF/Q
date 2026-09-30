#!/usr/bin/env python3
"""One locus for the live-corpus dependency shared by the harness test files.

WHY THIS EXISTS. Several assertions in this suite read a real file in the
Projects working tree — a spine, a staging folder, the vault `TODO.md`. Before
this module each call site built that path itself, and two of them still pointed
at the retired iCloud container. Five constructions of one fact is five places
for it to go stale; this is one. When the corpus moves (the pending iCloud-
container divorce is exactly the move that stranded those two sites), the single
location literal below is what changes.

WHAT A CALLER GETS.
  * `corpus_root()`  — the corpus location, overridable with `CLAUDE_CORPUS_ROOT`
    so a checkout elsewhere can be graded.
  * `require(rel, what)` — resolve one live-corpus path, or refuse. When the path
    is absent this raises `LiveCorpusUnavailable` carrying a reason that names the
    dependency as live-corpus; the caller turns that into its runner's skip. When
    `STRICT_LIVE_CORPUS=1` is set it raises `AssertionError` instead, so someone
    who needs the live corpus actually exercised can make an unrun assertion
    fatal rather than silently green.
  * `live_vault_root()` / `live_vault_todo()` — DELIBERATELY NOT OVERRIDABLE.

WHY ONE ACCESSOR IS NOT OVERRIDABLE. `smoke_auto_registration_e2e.py`'s 4b2
sub-check asserts that a `todo.py read` mutates no byte of the vault `TODO.md`.
`cmd_read` resolves the live vault through `todo.find_vault_root` and ignores the
`--cwd` it is handed, so a comparison target taken from the overridable resolver
could be pointed at some other populated tree — a file the code under test never
writes — and the check would pass trivially. That is the precise false green that
sub-check is being repaired to remove, so its target is resolved HERE, the way
`cmd_read` itself resolves: through `todo.find_vault_root` and its absolute
`VAULT_ROOT_FALLBACK` (`todo.py:39-41`), never from `Path.home()`.

Both accessors are seeded from the ONE location literal below. Do not add a
second spelling: `$HOME/Projects` is a symlink to `$HOME/repos/Projects`, so a
fallback spelling would add no coverage and would break the single-literal
property this module exists to hold.

This module must be importable by pytest modules in `tests/` and
`tests/research_pipeline/`, AND by the non-pytest smoke script — which is why it
is a plain module here and not `conftest.py`, and why it imports no pytest.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# --------------------------------------------------------------------------- #
# The ONE location literal. Both accessors below are seeded from it.
# --------------------------------------------------------------------------- #
_PROJECTS_ROOT_LITERAL = Path("~/repos/Projects")

CORPUS_ROOT_ENV = "CLAUDE_CORPUS_ROOT"
STRICT_ENV = "STRICT_LIVE_CORPUS"


class LiveCorpusUnavailable(Exception):
    """A live-corpus path is absent, and strict mode is off.

    Carries the reason text a runner should report for the skip. Deliberately
    NOT an AssertionError: an absent corpus is a not-run, not a failure — unless
    `STRICT_LIVE_CORPUS=1`, in which case `require()` raises AssertionError and
    this type never appears.
    """


def strict() -> bool:
    """True when the operator has demanded that live-corpus assertions run."""
    return os.environ.get(STRICT_ENV, "").strip() == "1"


def corpus_root() -> Path:
    """The Projects working tree these assertions read.

    Overridable with `CLAUDE_CORPUS_ROOT` so a copied or relocated checkout can
    be graded. Defaults to the repo location — NOT the retired iCloud container.
    """
    override = os.environ.get(CORPUS_ROOT_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return _PROJECTS_ROOT_LITERAL


def _missing(path: Path, what: str) -> str:
    """The bare statement of which live-corpus dependency was not satisfied."""
    return (
        f"live-corpus dependency not satisfied: {what} is not present at {path}. "
        f"This assertion reads the real Projects tree."
    )


def skip_reason(path: Path, what: str) -> str:
    """The reason text a runner reports for a live-corpus skip (strict OFF)."""
    return (
        f"{_missing(path, what)} NOT A FAILURE and NOT A PASS — nothing was "
        f"tested. Point {CORPUS_ROOT_ENV} at a checkout that has it, or set "
        f"{STRICT_ENV}=1 to make an unrun assertion fatal instead of a skip."
    )


def require(relative: str, what: str) -> Path:
    """Resolve one live-corpus path under `corpus_root()`, or refuse.

    `relative` is a path relative to the corpus root (an absolute path is passed
    through unchanged). `what` names the thing in words, so the reason a caller
    reports says which dependency was missing rather than only which path.

    Raises `AssertionError` under `STRICT_LIVE_CORPUS=1`, `LiveCorpusUnavailable`
    otherwise. It never returns a path that does not exist.
    """
    candidate = Path(relative)
    path = candidate if candidate.is_absolute() else corpus_root() / relative
    if path.exists():
        return path
    if strict():
        raise AssertionError(
            f"MISSING LIVE CORPUS, not a regression in the code under test — "
            f"and fatal because {STRICT_ENV}=1 was set: {_missing(path, what)} "
            f"Either point {CORPUS_ROOT_ENV} at a checkout that has it, or unset "
            f"{STRICT_ENV} to let this be reported as a skip.")
    raise LiveCorpusUnavailable(skip_reason(path, what))


# --------------------------------------------------------------------------- #
# The non-overridable live-vault accessors (see the module docstring).
# --------------------------------------------------------------------------- #

def live_vault_root() -> Path:
    """The vault root `todo.py cmd_read` actually resolves. NOT overridable.

    Resolves through `todo.find_vault_root`, so this cannot drift from what
    `cmd_read` does — and so `CLAUDE_CORPUS_ROOT` cannot redirect it. Falls back
    to the single location literal when `todo` is unimportable, which keeps the
    one-literal property intact.
    """
    hooks = Path(
        os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))
    ) / "hooks"
    if str(hooks) not in sys.path:
        sys.path.insert(0, str(hooks))
    try:
        import todo as _todo  # noqa: PLC0415 — deliberately lazy; see docstring
        return Path(_todo.find_vault_root(_PROJECTS_ROOT_LITERAL))
    except Exception:
        return _PROJECTS_ROOT_LITERAL


def live_vault_todo() -> Path:
    """The `TODO.md` `todo.py read` writes, if it writes anything. NOT overridable.

    May not exist — that is the case the caller must report as not-run rather
    than comparing two absent values and passing.
    """
    return live_vault_root() / "TODO.md"

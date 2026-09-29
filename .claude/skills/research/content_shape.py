"""Are these bytes the document, or a reference to one?

content-shape-gate D3 / plan action A1 (design-A31, amended).

The path-shaped read path already asks whether the bytes it got are *text*
(``raw.decode("utf-8")`` in ``adapters/document_folder.py`` and
``adapters/code_base.py``). It never asked whether they are the *document*. So a
file that merely NAMES a document — a synced-drive placeholder holding a document
id, a git-LFS pointer holding an object hash, a web shortcut holding a URL — is
perfectly good UTF-8, passes the only test there was, and is pinned and cited as
though it were the source. An empty file does the same, cited at line 1 with
nothing behind it.

This module answers the missing question and NOTHING else. It takes text, it
returns a stated reason or ``None``, and it decides nothing: deciding is the
port's, which is the same split every adapter docstring in this package already
describes.

**Content, never location.** The defect exists because something inferred content
from location. So this function's whole input is the text — there is no path
parameter to branch on, no provider list, no mount test and no extension. That is
structural rather than a convention: a location branch cannot be added here
without changing the signature, and ``test_content_shape.py`` mirrors the
design-A31 AST guard onto this module so the attempt fails the build.

**Signature specificity, not a size guess — and it is load-bearing.** A genuine
one-line note is ~50 bytes, so no *lower* bound could tell it from a 180-byte
placeholder. What separates them is that every test below names a concrete shape:
the placeholder key set, the LFS pointer's fixed first token, a shortcut's marker.
Ordinary prose matches none of them and therefore never enters the ambiguous space
where "err toward refusing" applies. The size cap is the OTHER direction — an
*upper* bound above which the shape tests do not apply at all, so a large genuine
document that happens to contain one of these keys is never caught.

**Emptiness is not a signature.** It is a universal test, so it runs before the
cap and cannot be escaped by any shape catalog. That matters because emptiness is
the live case: the placeholder shapes are a forward risk, empty document-shaped
files are already in the corpus a research run reads over.

**The catalog is meant to grow, and growing it must not touch the port.** A shape
this module has not been taught — another sync provider's stub, an alias file, a
format a vendor ships next year — matches nothing here and is admitted exactly as
before. That residue is the price of recognising shapes specifically instead of
guessing at anything short, and it is disclosed rather than hidden. Adding one is
a new :class:`Shape` in ``SHAPES`` and a test; no other file changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

__all__ = [
    "MAX_SHAPE_BYTES",
    "Shape",
    "SHAPES",
    "REASON_EMPTY",
    "not_the_document",
]


# The upper bound, and the only number in this module.
#
# Above it the shape tests do not apply, so a large genuine document that happens
# to carry one of the keys below is never caught by one. Editorial but calibrated:
# the largest real placeholder observed on this machine is 180 bytes, so 4 KiB
# sits an order of magnitude above the evidence and orders below a real document.
#
# It cannot protect a SHORT genuine file — that is signature specificity's job,
# not this constant's. An earlier draft of the design credited this cap with both
# directions; it is an upper bound and can only ever do one.
MAX_SHAPE_BYTES = 4096

REASON_EMPTY = (
    "the file holds no content, so a citation to it would name a source with "
    "nothing behind it"
)


@dataclass(frozen=True)
class Shape:
    """One named shape that a file can have INSTEAD of being a document.

    ``matches`` sees the text and nothing else. ``reason`` is what a person reads
    in the report, so it says what the file is and what to do, in the same voice
    as the refusal they already get for a PDF.
    """

    name: str
    reason: str
    matches: Callable[[str], bool]


# -- the shapes ------------------------------------------------------------- #

def _is_drive_placeholder(text: str) -> bool:
    """A synced-drive placeholder: a small JSON object naming a document id.

    Every one of the 18 real stubs on this machine is a JSON object whose keys
    are exactly a warning string, ``doc_id``, ``resource_key`` and ``email`` —
    there is no URL in it, only the id. So the signature is "a JSON object
    carrying a document-id key", which is the shape, rather than anything about
    where the file sits.

    ``doc_id`` is the ONLY key tested, deliberately. A first draft also accepted
    ``resource_id``, which appears in no observed stub and in no test — a guess
    at a shape rather than a recognised one, which is the exact discipline this
    module is built on. A provider whose stub uses a different key is an
    uncatalogued shape: not caught, disclosed as not caught, and added here with
    a test when one is actually seen.
    """
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return "doc_id" in parsed


def _is_lfs_pointer(text: str) -> bool:
    """A git-LFS pointer file: the spec fixes its first line exactly.

    Per the LFS pointer-file spec the first line is the ``version`` key naming
    the spec URL, followed by ``oid`` and ``size``. Requiring the fixed first
    token means ordinary prose that merely mentions LFS does not match.
    """
    first = text.lstrip().split("\n", 1)[0].strip()
    return first.startswith("version https://git-lfs.github.com/spec/")


def _is_web_shortcut(text: str) -> bool:
    """A shortcut file: a marker line whose whole purpose is to hold a URL.

    Three forms, each recognised by its own fixed marker rather than by anything
    resembling a URL — a document may legitimately quote a URL, and only these
    markers say "this file IS the link".
    """
    stripped = text.lstrip()
    for line in stripped.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        if candidate in ("[InternetShortcut]", "[Desktop Entry]"):
            return True
        break
    if "<key>URL</key>" in stripped and "plist" in stripped:
        return True
    return False


SHAPES: Tuple[Shape, ...] = (
    Shape(
        name="drive-placeholder",
        reason=("this file is a synced-drive placeholder naming a document kept "
                "elsewhere, not the document itself; export or download the real "
                "document and point at that"),
        matches=_is_drive_placeholder,
    ),
    Shape(
        name="lfs-pointer",
        reason=("this file is a git-LFS pointer naming an object stored "
                "elsewhere, not the object itself; fetch the LFS content and "
                "read that"),
        matches=_is_lfs_pointer,
    ),
    Shape(
        name="web-shortcut",
        reason=("this file is a shortcut holding a link, not the document it "
                "links to; point at the document itself"),
        matches=_is_web_shortcut,
    ),
)


# -- the question ----------------------------------------------------------- #

def not_the_document(content: str) -> Optional[str]:
    """Why these bytes are not a document, or ``None`` if nothing says they are not.

    Pure and total. It never raises, never reads anything, and returns a REASON
    rather than a verdict — the caller decides what a reason is worth, which for
    the admission port is a recorded refusal on the obligation the PDF case
    already uses.

    Order matters and is not incidental: emptiness first, because it is universal
    and must not be escapable by the cap; then the cap, which exempts large
    content from the shape tests; then the named shapes.
    """
    if not content or not content.strip():
        return REASON_EMPTY
    if len(content.encode("utf-8")) > MAX_SHAPE_BYTES:
        return None
    for shape in SHAPES:
        if shape.matches(content):
            return shape.reason
    return None

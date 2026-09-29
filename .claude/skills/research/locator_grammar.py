"""Per-source-kind locator grammar — the third part of the citation pin.

research-source-adapters S3 / plan action A1 (design-A3).

A pin is three parts: source identity, the version read, and a **locator**
appropriate to that source kind. This module owns the third. A locator is not a
string here: it is a set of **named, ordered parts** declared by its kind, so an
incomplete locator is *detectable* rather than merely shorter.

Why named parts rather than a flat string
-----------------------------------------
S1's Probe 2 found a Google Sheets cell is addressable only as ``(sheet, cell)``
and that the CSV/TSV export carries the **cell half only**
(``…_DRIVEPROBE_RESEARCH.md:265``, ``:311-312``). Against a flat-string grammar
that truncation is invisible — the locator is simply shorter, and nothing can
tell "shorter" from "missing a required part". With named parts the kind
declares what it needs and :func:`missing_required_parts` names what is absent.

Scope of this module
--------------------
Registers **five** kinds: ``code`` (required parts ``path``, ``lines``; S3),
``web`` (required part ``url``; S6, design-A29), ``knowledge_library`` +
``document_folder`` (required parts ``path``, ``line``; S7, design-A21/A24/A31),
and ``linear`` (required part ``issue``, optional ``comment``; S8, design-A12).
No Sheets/Drive kind is registered here — S9 is struck, and a *mounted* Drive
folder is an ordinary ``document_folder`` path with no branch of its own
(design-A31). Later source classes register their own kinds through
:func:`register_kind`; adding one touches this module and the three citation
vocabulary loci, and no existing kind.

``linear`` is the FIRST kind here to declare an optional part, and S8 is the
first slice since S3 that had to touch the citation vocabulary at all — a tracker
pin has no shipped marker to render through, so it is a drift-guarded three-loci
edit (design-A17) rather than a row added here alone.

S7 adds **no** citation-vocabulary work for the same reason ``web`` did not: the
``[stated — local-file:<path>:<line>]`` marker its two kinds render is already
shipped and already in the registry. What S7 *did* have to change is the port's
render **dispatch**, which selected between two forms on one overloaded boolean
and could produce neither of them — see ``source_port.KindRules.render_form``.

``web`` adds **no** citation-vocabulary work, which is the point of design-A29:
the ``[stated — URL]`` marker it renders is already shipped and already in the
registry. A locator kind whose rendering had to change the vocabulary would be a
different, much larger change.

Dependency direction: this module imports nothing from the package. The port
imports it, never the reverse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Optional, Tuple


class LocatorError(ValueError):
    """Raised when a locator cannot satisfy its kind's declared grammar.

    Always names the offending kind and part(s) — a locator failure that did not
    say *which* part was missing would be the "shorter vs incomplete" ambiguity
    this module exists to remove.
    """


# --------------------------------------------------------------------------- #
# The kind registry.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class LocatorKind:
    """One source kind's locator grammar.

    `required_parts` is ORDERED — the order is the render order and the parse
    order. `optional_parts` may be supplied and are rendered after the required
    ones; a kind that declares none (every kind S3 registers) has an unambiguous
    round-trip.
    """

    name: str
    required_parts: Tuple[str, ...]
    optional_parts: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise LocatorError("a locator kind must have a name")
        if not self.required_parts:
            raise LocatorError(
                f"locator kind {self.name!r} declares no required parts; a kind "
                "with no required part cannot distinguish an incomplete locator "
                "from a complete one"
            )
        overlap = set(self.required_parts) & set(self.optional_parts)
        if overlap:
            raise LocatorError(
                f"locator kind {self.name!r} declares {sorted(overlap)} as both "
                "required and optional"
            )

    @property
    def all_parts(self) -> Tuple[str, ...]:
        return tuple(self.required_parts) + tuple(self.optional_parts)


_REGISTRY: Dict[str, LocatorKind] = {}


def register_kind(kind: LocatorKind, *, replace: bool = False) -> LocatorKind:
    """Register a locator kind. Refuses a silent re-definition."""
    existing = _REGISTRY.get(kind.name)
    if existing is not None and not replace and existing != kind:
        raise LocatorError(
            f"locator kind {kind.name!r} is already registered as {existing!r}; "
            "pass replace=True to redefine it deliberately"
        )
    _REGISTRY[kind.name] = kind
    return kind


def get_kind(name: str) -> LocatorKind:
    """Look up a registered kind. **Fails closed** on an unknown kind.

    An unknown kind is never defaulted to a permissive grammar: a source class
    nobody registered has no locator contract, so nothing may be minted for it.
    """
    try:
        return _REGISTRY[name]
    except KeyError:
        raise LocatorError(
            f"unknown locator kind {name!r}; registered kinds: "
            f"{sorted(_REGISTRY) or '(none)'}"
        ) from None


def registered_kinds() -> Tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


# The kind S3 registered. `path` is repo-relative (never machine-absolute, per
# design Q6); `lines` is a single line or a range.
CODE_KIND = register_kind(LocatorKind(name="code", required_parts=("path", "lines")))

# The kind S6 registers (design-A29). ONE required part, deliberately: a web
# locator addresses the PAGE, and the page's address is the whole URL — the
# reference document already defines it that way ("the full URL … Pins the page,
# not the site", `Skills/research-sources.md:75`).
#
# One required part is the MINIMUM this grammar accepts (a kind with none is
# refused at construction, `:67-72`), and that is the right floor here rather
# than an accident: it is what makes an empty locator detectable, which is the
# whole reason locators are named parts rather than strings.
#
# **Why `url` is not split into host + path.** It would render identically
# (parts are joined by `:`) but would make a URL's own `:` ambiguous on parse,
# and nothing downstream addresses a web source by host alone.
WEB_KIND = register_kind(LocatorKind(name="web", required_parts=("url",)))

# The two kinds S7 registers (design-A21, A24, A31) — one per source kind.
#
# **Why TWO kinds and not one shared `local-file` kind.** Both source classes
# emit the SAME citation vocabulary (`[stated — local-file:<path>:<line>]`), so a
# single locator kind named for that vocabulary reads naturally and is wrong:
# `KIND_RULES` is consulted under the LOCATOR kind at `source_port.py:272`
# (`render`) and under the SOURCE kind at `:580`/`:626` (`admit`). One locator
# kind under two source kinds would make those two lookups disagree, and
# `scope_record.py:52` documents the two registries as 1:1 mirrors. The shared
# vocabulary is expressed where it belongs — as `KindRules.citation_prefix`, an
# explicit named field both rows carry deliberately — rather than by accident of
# a shared kind name.
#
# `path` is the file; `line` is a single line or a range. Neither declares an
# optional part, so `parse()` stays defined and the round-trip holds.
KNOWLEDGE_LIBRARY_KIND = register_kind(
    LocatorKind(name="knowledge_library", required_parts=("path", "line"))
)
DOCUMENT_FOLDER_KIND = register_kind(
    LocatorKind(name="document_folder", required_parts=("path", "line"))
)

# The kind S8 registers (design-A12) — the first tracker, and the first kind here
# to declare an OPTIONAL part.
#
# **Why `issue` is required and `comment` is optional.** The locked Desired
# Solution names the tracker locator as "an issue or comment identifier for a
# tracker" (`_THOUGHT.md:66`). A comment does not exist independently of the issue
# that carries it, so a locator naming a comment alone would address nothing that
# can be re-opened; and an issue alone is a complete, re-openable address. That is
# one kind with an optional second part, not two kinds — which is the reading that
# keeps `KIND_RULES`'s two lookups (locator kind at `render`, source kind at
# `admit`) pointing at one row, exactly as the S7 note above requires.
#
# **Consequence, stated because it is a real narrowing.** `parse()` is defined
# only for kinds with no optional parts (`:245-250`), so a rendered `linear`
# locator does not round-trip through it. Nothing in the read path parses a
# locator — the port renders pins and never re-reads them — so this costs nothing
# today, and the alternative (a required `comment` part) would force every
# issue-level citation to carry a fabricated comment id, which is worse than an
# absent inverse.
LINEAR_KIND = register_kind(
    LocatorKind(name="linear", required_parts=("issue",),
                optional_parts=("comment",))
)


# --------------------------------------------------------------------------- #
# The locator value.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Locator:
    """A locator for one source kind, held as named parts.

    Construction never raises on an incomplete part set — an incomplete locator
    must be *representable* so the port can degrade the item and name the missing
    part (C10). What refuses is :meth:`render`, and only there.
    """

    kind: str
    parts: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Freeze the mapping so a "frozen" dataclass is actually immutable, and
        # reject a part the kind never declared (a typo'd part name would
        # otherwise read as a missing required part later, in the wrong place).
        spec = get_kind(self.kind)
        unknown = sorted(set(self.parts) - set(spec.all_parts))
        if unknown:
            raise LocatorError(
                f"locator kind {self.kind!r} does not declare part(s) {unknown}; "
                f"declared parts: {list(spec.all_parts)}"
            )
        cleaned = {k: str(v) for k, v in self.parts.items() if str(v).strip() != ""}
        object.__setattr__(self, "parts", dict(cleaned))

    # -- completeness ------------------------------------------------------- #

    def missing_required_parts(self) -> Tuple[str, ...]:
        """The declared-required parts this locator cannot supply, in declared order.

        Empty tuple == complete. This is the predicate the port calls before
        minting a pin; a non-empty result is C10's "missing requirement named".
        """
        spec = get_kind(self.kind)
        return tuple(p for p in spec.required_parts if not self.parts.get(p))

    def is_complete(self) -> bool:
        return not self.missing_required_parts()

    # -- rendering ---------------------------------------------------------- #

    def render(self) -> str:
        """Render to the pin's locator segment: parts joined by ``:`` in declared order.

        Raises :class:`LocatorError` naming every missing required part. A pin
        can therefore never carry a silently-shortened locator.
        """
        missing = self.missing_required_parts()
        if missing:
            raise LocatorError(
                f"locator kind {self.kind!r} is missing required part(s) "
                f"{list(missing)}; cannot render"
            )
        spec = get_kind(self.kind)
        ordered = [self.parts[p] for p in spec.required_parts]
        ordered += [self.parts[p] for p in spec.optional_parts if self.parts.get(p)]
        return ":".join(ordered)


def parse(kind: str, rendered: str) -> Locator:
    """Inverse of :meth:`Locator.render` for a kind with no optional parts.

    Splits from the RIGHT, so a leading part may itself contain ``:`` (a path
    can) while the trailing parts cannot — which is true of every kind
    registered here. A field count that does not match the kind's required arity
    raises rather than guessing which part is absent.
    """
    spec = get_kind(kind)
    if spec.optional_parts:
        raise LocatorError(
            f"parse() is defined only for kinds with no optional parts; "
            f"{kind!r} declares {list(spec.optional_parts)}"
        )
    n = len(spec.required_parts)
    fields = rendered.rsplit(":", n - 1) if n > 1 else [rendered]
    if len(fields) != n or any(f.strip() == "" for f in fields):
        raise LocatorError(
            f"locator kind {kind!r} requires {n} part(s) {list(spec.required_parts)}; "
            f"{rendered!r} supplies {len([f for f in fields if f.strip()])}"
        )
    return Locator(kind=kind, parts=dict(zip(spec.required_parts, fields)))


def web_locator(url: Optional[str] = None) -> Locator:
    """Convenience constructor for the `web` kind (S6, design-A29).

    `url` is optional at the type level for the same reason `code_locator`'s
    `lines` is: the incomplete case must be CONSTRUCTIBLE so the port can degrade
    the item and name the missing part, rather than being unrepresentable.
    """
    parts = {}
    if url is not None:
        parts["url"] = url
    return Locator(kind="web", parts=parts)


def knowledge_library_locator(path: str, line: Optional[str] = None) -> Locator:
    """Convenience constructor for the `knowledge_library` kind (S7).

    `line` is optional at the type level for the same reason `code_locator`'s
    `lines` is: the incomplete case must be CONSTRUCTIBLE so the port can degrade
    the item and name the missing part.
    """
    parts = {"path": path}
    if line is not None:
        parts["line"] = line
    return Locator(kind="knowledge_library", parts=parts)


def document_folder_locator(path: str, line: Optional[str] = None) -> Locator:
    """Convenience constructor for the `document_folder` kind (S7).

    A mounted cloud-drive folder is an ordinary path here and gets no branch of
    its own — that IS design-A31.
    """
    parts = {"path": path}
    if line is not None:
        parts["line"] = line
    return Locator(kind="document_folder", parts=parts)


def linear_locator(issue: Optional[str] = None,
                   comment: Optional[str] = None) -> Locator:
    """Convenience constructor for the `linear` kind (S8, design-A12).

    Both parts are optional at the type level for the reason every sibling
    constructor here gives: the incomplete case must be CONSTRUCTIBLE so the port
    can degrade the item and name the missing part, rather than being
    unrepresentable. `issue` is nonetheless REQUIRED by the grammar, so an
    issue-less locator raises at :meth:`Locator.render` and never renders short.
    """
    parts = {}
    if issue is not None:
        parts["issue"] = issue
    if comment is not None:
        parts["comment"] = comment
    return Locator(kind="linear", parts=parts)


def code_locator(path: str, lines: Optional[str] = None) -> Locator:
    """Convenience constructor for the one kind this slice registers.

    `lines` is deliberately optional at the type level so the incomplete case is
    constructible — that is what lets a test drive the missing-part degradation
    through the real caller rather than by hand-building a dict.
    """
    parts = {"path": path}
    if lines is not None:
        parts["lines"] = lines
    return Locator(kind="code", parts=parts)

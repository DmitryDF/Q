"""The declared-scope record — the obligation that bounds where reading may go.

research-source-adapters S3 / plan action A2 (design-A4, design-A5's contract
surface, design-A11's declaration half).

An immutable declaration created once at selection time. It holds the declared
sources, doubles as the approval artifact, and answers **one** question for every
read the port is about to perform: is this fully-resolved target inside the
declaration? A target that is not is **refused before the read** — not filtered
out of the results afterwards (C5).

Three properties are load-bearing and are each tested:

* **Containment compares fully RESOLVED paths** (E1). A path reached by a
  symlink out of a declared directory is refused like any other out-of-scope
  read. Without resolution the check is decorative.
* **A declaration that NAMES a ``CLAUDE.md`` is refused at construction** (E4),
  so the prohibition surfaces before any read. A ``CLAUDE.md`` merely *inside* a
  declared directory is a different case with a different owner: it is
  enumerated like anything else and refused at ``admit()`` with a recorded
  degradation. There is deliberately no third mechanism and no silent skip.
* **``scope_mode`` separates "declared unscoped" from "declared nothing"**. An
  *enumerated* record with no selectors admits nothing (E6); an *unscoped*
  record is a declared mode meaning "the bound is the source's own exposure, not
  an absent field" (``…_DESIGN.md:294-297``) and is NOT read as empty. Collapsing
  the two would ship a tested invariant that inverts a locked upstream
  requirement.

The JSON form is a cross-surface contract, not an implementation detail: the
picker writes this record, it is carried across a process boundary — hoisted onto
the manifest cycle on the routes that open one, carried in-process on the route
that does not — and it is read back where the sources are actually read.

**No shell hook reads it, and an earlier version of this sentence said one did.**
The anticipated reader was ``research-scope-gate.sh`` parsing the record with
``jq``; what shipped is that the gate reads only the ``r1_scope_approved`` /
``r1_scope_revoked`` flags beside it, and every consumer of the record itself is
Python: ``research_pipeline`` validates its SHAPE at registration, and
``declared_read`` + ``source_port.AdmissionPort`` read its CONTENT at admission
time. Deciding the persisted shape once here is still the right call — a JSON
form crosses the process boundary that a Python type cannot — but do not reason
from "a bash hook has to parse this" when nothing does.

Dependency direction: imports nothing from the package.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple, Union

SCHEMA_VERSION = 1

SCOPE_MODE_ENUMERATED = "enumerated"
SCOPE_MODE_UNSCOPED = "unscoped"
SCOPE_MODES = (SCOPE_MODE_ENUMERATED, SCOPE_MODE_UNSCOPED)

# The registered source kinds, mirroring the locator kinds in `locator_grammar`.
# An unregistered kind fails closed everywhere it is read (construction, `check`,
# and `from_json`).
#
# Registering a kind here is what makes it SELECTABLE in the picker — that entry
# derives selectability from this tuple at call time rather than storing a flag
# (`source_picker.is_selectable`). So this line is the single authority on which
# classes a person may declare, and S6 adding `web` to it is what flips the web
# row with no catalogue edit.
#
# Note on the "single authority" sentence above: it stays true, but selectability
# is derived from **three** inputs rather than one. Registering a kind here is
# still what makes it DECLARABLE at all; the other two decide whether it is
# OFFERED.
#
#   * Whether a given ROUTE may offer it is the class's own route set
#     (`source_picker.SourceClassEntry.routes`). Registering a kind flips its row
#     on every routing path while a reader may exist on only one — which would
#     offer a person a source that was then never read. Silence, not refusal, is
#     the failure this topic exists to remove.
#   * Whether a production driver for it exists ANYWHERE is
#     `kind_reachability.KNOWN_UNREACHABLE`, read by `source_picker.is_selectable`.
#     Registration and the route set together still cannot answer that question,
#     and while it went unasked `code` was offered on three routes for four slices
#     with nothing behind it to open a repository.
#
# All three are derived at render time; none is a stored flag here or there.
KIND_CODE = "code"
KIND_WEB = "web"
KIND_KNOWLEDGE_LIBRARY = "knowledge_library"
KIND_DOCUMENT_FOLDER = "document_folder"
KIND_LINEAR = "linear"
REGISTERED_KINDS = (
    KIND_CODE,
    KIND_WEB,
    KIND_KNOWLEDGE_LIBRARY,
    KIND_DOCUMENT_FOLDER,
    KIND_LINEAR,
)

# The kinds whose targets are filesystem paths. They share one containment
# implementation (`_check_path`) rather than three copies of it: what "resolved"
# and "within" mean for a path does not vary by which class declared it, and a
# restatement is where an over-permissive check would hide.
#
# `code` is in this set and its behaviour is unchanged — `_check_path` is
# `_check_code` renamed, with the result's `kind` already taken from `self.kind`.
#
# `linear` is deliberately NOT here (S8). An issue identity is not a filesystem
# path, and admitting it to this set would resolve `LIN-123` against the CWD — the
# exact defect `_display_target` was written to fix for `web`.
PATH_SHAPED_KINDS = (KIND_CODE, KIND_KNOWLEDGE_LIBRARY, KIND_DOCUMENT_FOLDER)

# The kinds that may NEVER be declared `unscoped` (S7). A knowledge library or a
# document folder bounded by "whatever this source exposes" is the whole
# filesystem, which is not a bound. `source_picker`'s catalogue also carries
# `allows_unscoped=False` for both, but that entry governs what a person can pick
# in the list; this one governs what can be CONSTRUCTED — including by the
# `/clarification` caller that assembles a declaration outside the source list
# altogether.
#
# `linear` is deliberately NOT here (S8). "Read whatever Linear gives me" IS the
# locked bound for a tracker — the workspace the connection reaches is itself a
# boundary, unlike a filesystem root — and `source_picker`'s catalogue row already
# carries `allows_unscoped=True` for it. Adding it here would contradict the
# catalogue and make the offered option unconstructible.
UNSCOPED_FORBIDDEN_KINDS = (KIND_KNOWLEDGE_LIBRARY, KIND_DOCUMENT_FOLDER)

# --------------------------------------------------------------------------- #
# The two selector shapes a `linear` declaration may take (S8, design-A12).
#
# The locked Desired Solution admits Linear "optionally scoped by project path(s)
# **or a filtered-issues link**" (`_THOUGHT.md:54`). Those are two different
# shapes, and a one-shape selector would silently deliver half the promised bound
# — which an independent checker caught this plan doing.
#
# **The kind decides, never the shape.** A link selector is URL-shaped, and a
# URL-shaped selector must NOT be mistaken for a `web` source: what a declaration
# means is fixed by the kind it was declared under. Nothing here dispatches on
# "looks like a URL" to pick a source class.
# --------------------------------------------------------------------------- #

# A Linear issue identity: a team/project key, a hyphen, a number (`ENG-123`).
# Case-insensitive on the key half, which is how Linear itself renders them.
LINEAR_ISSUE_RE = re.compile(r"\A([A-Za-z][A-Za-z0-9_]*)-([0-9]+)\Z")


def _is_linear_link_selector(selector: str) -> bool:
    """Is this selector a filtered-issues LINK rather than a project identifier?

    A link is the URL-shaped selector. Deliberately a *shape* test on a selector
    already known to be `linear`, not a source-class decision — see the kind-decides
    note above.
    """
    return str(selector).strip().lower().startswith(("http://", "https://"))


def linear_identifier_from_url(url: str) -> Optional[str]:
    """The `ENG-123` identity carried in a Linear issue URL, or ``None``.

    **This exists because the identity is NOT a field.** The S8 Session-2 live
    round-trip against the operator's real workspace established that neither
    `list_issues` nor `get_issue` returns an `identifier`: what they return is a
    UUID `id` and a `url`, and the human-readable identity lives in the URL as
    path segment 2 (``https://linear.app/<workspace>/issue/<ENG-123>/<slug>``).

    It lives HERE, beside :data:`LINEAR_ISSUE_RE`, because this module is already
    the one home for what a Linear issue identity *is* — and because both callers
    need the same answer for the freeze to mean anything. The connect gate freezes
    a link's membership as identities, and :meth:`DeclaredSource._check_linear`
    tests an adapter's enumerated target against that frozen set. If the two
    derived identity differently, every admission would be refused as
    out-of-declaration while the run had read exactly what the person declared —
    a refusal blaming the person for the client's own vocabulary mismatch.

    **Anchored on the ``issue`` segment, not on shape alone.** The identity is the
    segment immediately following a path segment named ``issue``. Scanning the
    whole path for the first issue-SHAPED segment was tried first and is wrong in
    a way that would be silent: a workspace slug such as ``acme-2024`` matches
    :data:`LINEAR_ISSUE_RE` exactly, and it precedes the real identity, so a
    shape-only scan would return the workspace as the issue and freeze a
    membership of workspace names. Anchoring costs nothing and cannot pick the
    wrong segment.

    Returns ``None`` rather than raising, and never invents an identity: a URL
    with no anchored issue segment is not an issue URL, and a caller skips it.
    That matches the surrounding tolerance of `_default_extract_issue_ids`, which
    would otherwise be handed a fabricated key.
    """
    text = str(url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return None
    # Strip scheme, then query/fragment, then walk the path segments. Written in
    # string operations rather than `urllib.parse` to match this package's
    # existing URL handling and to stay import-light in a module the port reaches
    # on every containment check.
    after_scheme = text.split("://", 1)[1]
    path = after_scheme.split("#", 1)[0].split("?", 1)[0]
    segments = [s for s in path.split("/")[1:] if s]
    for index, segment in enumerate(segments):
        if segment.lower() != "issue":
            continue
        if index + 1 < len(segments) and LINEAR_ISSUE_RE.match(segments[index + 1]):
            return segments[index + 1]
    return None


# The name a declaration may never carry as a source identity (design-A11).
CLAUDE_MD_BASENAME = "CLAUDE.md"

# `connection_id` is an ALLOWLIST, not a secret detector: a bounded-length
# opaque identifier. It admits a connection handle and refuses the shapes a real
# credential takes (whitespace, `=`, `/`, `+`, long base64/PEM runs, `Bearer `
# prefixes, JSON). The failure direction is therefore a rejected legitimate
# handle, never an admitted credential. Nothing in this slice RESOLVES a
# connection id — the credential path proper is S8's `credential_path.py`.
CONNECTION_ID_RE = re.compile(r"\A[A-Za-z0-9._:-]{1,128}\Z")


class ScopeRecordError(ValueError):
    """Raised when a declaration cannot be constructed or parsed.

    Construction-time refusals (a declared ``CLAUDE.md``, a secret-shaped
    ``connection_id``, an unknown kind or mode) raise. A *read* that falls
    outside the declaration does NOT raise — it returns a :class:`Refusal`, so
    the caller records it rather than crashing the run.
    """


# --------------------------------------------------------------------------- #
# Check outcome — a discriminated pair, never a bare bool.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Admission:
    """The target is inside the declaration.

    `scope_mode` reports HOW NARROWLY the declaration that admitted this target
    was drawn — the mode of the `DeclaredSource` whose `check()` returned this
    result (S10/A1). It is DATA about the admission, never part of the decision:
    every arm sets it from `self.scope_mode` on the way out, and no branch reads
    it on the way in, so admit/refuse is byte-identical with and without it.

    **Why an explicit field rather than reading `matched_selector is None`.**
    An unscoped admission does carry `matched_selector=None` today, so the
    inference would work — and it would put a RULE in the reader ("no selector
    means unbounded") that three separate check arms happen to keep true. The
    Linear frozen-membership arm is one `next(..., None)` away from returning
    `None` on the NARROWEST bound this record can express, which the inference
    would read as the WEAKEST. The declaration already knows its own mode; it
    now says so.
    """

    kind: str
    resolved_target: str
    matched_selector: Optional[str] = None
    admitted: bool = True
    scope_mode: Optional[str] = None


@dataclass(frozen=True)
class Refusal:
    """The target is outside the declaration — named, so the refusal can be recorded.

    `resolved_target` is the FULLY RESOLVED path (C6): a symlinked escape is
    reported by where it actually landed, not by how it was spelled.
    """

    kind: str
    resolved_target: str
    reason: str
    admitted: bool = False


CheckResult = Union[Admission, Refusal]


# --------------------------------------------------------------------------- #
# Declared source.
# --------------------------------------------------------------------------- #

def _resolve(p: Union[str, Path]) -> Path:
    """Fully resolve a path without requiring it to exist.

    `strict=False` keeps a declaration constructible before its target exists,
    while still collapsing symlinks on every component that does — which is the
    half E1 depends on.
    """
    return Path(p).expanduser().resolve(strict=False)


def _is_within(child: Path, parent: Path) -> bool:
    """True when `child` is `parent` or lies beneath it. Both already resolved."""
    if child == parent:
        return True
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
# URL normalisation — the `web` kind's answer to `_resolve` (S6, design-A29).
#
# `_resolve` collapses a path to one canonical form so containment compares like
# with like. A URL needs the same service and CANNOT use that function: running
# `Path("https://x.com/a").resolve()` yields `<cwd>/https:/x.com/a`, which is not
# a URL, not a real path, and unrecognisable in a refusal message a person reads.
#
# The folding is EDITORIAL and its risk is deliberately SYMMETRIC — fold too hard
# and two pages count as one, fold too little and one page counts as two. What is
# NOT editorial is that ONE function decides it for BOTH sides of every
# comparison: a selector normalised one way and a target another would produce a
# containment answer that means nothing.
#
# Percent-encoding is deliberately NOT decoded: `%2F` is not `/`, and decoding it
# before a path-prefix comparison would let an encoded separator widen a bound.
# --------------------------------------------------------------------------- #

_DEFAULT_PORTS = {"http": "80", "https": "443"}

# What a hostname may look like, after lowercasing. This is load-bearing rather
# than cosmetic: `urlsplit` is permissive, so with a scheme prepended it will
# happily report `not a url at all` as a hostname. Without this check a declared
# selector that is not a location at all is ACCEPTED and then matches nothing —
# which reads to a person as "the research found nothing there" rather than "that
# was not a location", the exact confusion `_validate_web_selectors` exists to
# prevent.
_HOSTNAME_RE = re.compile(
    r"\A[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\Z")


class _NormalisedUrl(tuple):
    """A `(host, path)` pair, normalised. Deliberately not a URL string again —
    the two halves are compared by different rules (host exactly, path by prefix)
    and re-joining them would invite a substring comparison."""

    __slots__ = ()

    @property
    def host(self) -> str:
        return self[0]

    @property
    def path(self) -> str:
        return self[1]

    def render(self) -> str:
        return f"{self.host}{self.path}"


def _normalise_url(raw: Union[str, Path], *, assume_scheme: bool = False
                   ) -> Optional[_NormalisedUrl]:
    """Normalise an http(s) URL to `(host, path)`, or return None if it is not one.

    `assume_scheme` is for DECLARED SELECTORS, which a person may reasonably write
    as `example.com/docs` rather than `https://example.com/docs`. A read TARGET is
    never given that courtesy: a target arrives from a citation and must already
    be a real URL, so a bare host as a target is not a URL and is refused.
    """
    from urllib.parse import urlsplit

    text = str(raw).strip()
    if not text:
        return None
    if assume_scheme and "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        return None

    host = (parts.hostname or "").lower()
    if not host:
        return None
    if not _HOSTNAME_RE.match(host):
        return None
    # One leading `www.` is stripped. Editorial, symmetric, applied to both sides.
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    # A non-default port is part of the host's identity; a default one is noise.
    port = parts.port
    if port is not None and str(port) != _DEFAULT_PORTS.get(scheme):
        host = f"{host}:{port}"

    # Query and fragment are dropped: they address a view of a page, not a page,
    # and a bound expressed over them would not survive the page being re-opened.
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    while "//" in path:
        path = path.replace("//", "/")
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return _NormalisedUrl((host, path))


def _url_is_within(target: _NormalisedUrl, selector: _NormalisedUrl) -> bool:
    """True when `target` is at or under `selector`.

    **Host must match EXACTLY.** A subdomain does not match a declared parent
    host — `docs.example.com` is not inside a declaration of `example.com`. This
    is fail-closed on purpose and is the one ASYMMETRIC choice here: admitting a
    subdomain a person did not name is the failure that matters, and refusing one
    they did mean costs them a second line in the declaration.
    """
    if target.host != selector.host:
        return False
    if selector.path == "/":
        return True
    if target.path == selector.path:
        return True
    # Segment-wise prefix — so a declared `/docs` bounds `/docs/a` but NOT
    # `/docsearch`, which a plain string prefix would have admitted.
    return target.path.startswith(selector.path + "/")


def _display_target(kind: str, target: Union[str, Path]) -> str:
    """How a target is NAMED in a refusal, dispatched on kind.

    This exists because the one refusal branch that fires BEFORE any per-kind
    arm — "the declaration names no source of this kind" — used to resolve every
    target as a filesystem path. For a URL that produced `<cwd>/https:/host/path`
    in the message a person reads: not a URL, not a path, and unrecognisable as
    the thing they cited.
    """
    if kind == KIND_WEB:
        normalised = _normalise_url(target)
        return normalised.render() if normalised is not None else str(target)
    if kind == KIND_LINEAR:
        # An issue identity is shown AS ITSELF (S8, A4b). Falling through to the
        # path branch would render `LIN-123` as `<cwd>/LIN-123` — the identical
        # defect this function was written to fix for `web`, in a message a person
        # reads when their read was refused.
        return str(target).strip()
    return str(_resolve(target))


@dataclass(frozen=True)
class DeclaredSource:
    """One declared source: what kind it is, how it is bounded, and its selectors.

    Kind-dispatched rather than path-shaped on purpose. A path-shaped record can
    only express *unscoped* as an empty selector list, which E6 defines as
    admitting nothing — inverting the upstream requirement that an unscoped
    source records the mode **explicitly, not as an absent field**.
    """

    kind: str
    scope_mode: str = SCOPE_MODE_ENUMERATED
    selectors: Tuple[str, ...] = ()
    connection_id: Optional[str] = None
    # The frozen membership of a filtered-issues LINK selector (S8, design-A4).
    #
    # **`None` and `()` mean different things and the distinction is load-bearing.**
    # `None` is "this link has not been resolved yet"; `()` is "this link was
    # resolved and matched nothing". A filter matching zero issues is a LEGITIMATE
    # bound, not an error, so emptiness cannot be the not-yet-resolved test — the
    # person is shown the `0` at approval and consents to it. Collapsing the two
    # would either refuse a legal bound or let an unresolved link reach approval,
    # and A7's gate turns on exactly this difference.
    #
    # Populated at SELECTION time by A7, never here and never at read time: see
    # `_check_linear` for why a pure predicate cannot resolve it.
    resolved_members: Optional[Tuple[str, ...]] = None

    def __post_init__(self) -> None:
        if self.kind not in REGISTERED_KINDS:
            raise ScopeRecordError(
                f"unknown source kind {self.kind!r}; registered kinds: "
                f"{list(REGISTERED_KINDS)} (an unknown kind fails closed)"
            )
        if self.scope_mode not in SCOPE_MODES:
            raise ScopeRecordError(
                f"unknown scope_mode {self.scope_mode!r} for kind {self.kind!r}; "
                f"known modes: {list(SCOPE_MODES)}"
            )
        object.__setattr__(self, "selectors", tuple(self.selectors or ()))
        if self.scope_mode == SCOPE_MODE_UNSCOPED and self.selectors:
            raise ScopeRecordError(
                f"kind {self.kind!r} declares scope_mode 'unscoped' but also carries "
                f"selectors {list(self.selectors)}; unscoped means the bound is the "
                "source's own exposure — declare one or the other"
            )
        if self.scope_mode == SCOPE_MODE_UNSCOPED and self.kind in UNSCOPED_FORBIDDEN_KINDS:
            raise ScopeRecordError(
                f"kind {self.kind!r} may not be declared 'unscoped'; a knowledge "
                "library or document folder bounded by whatever it exposes is the "
                "whole filesystem, which is not a bound — enumerate the folders "
                "instead"
            )
        if self.resolved_members is not None:
            object.__setattr__(self, "resolved_members",
                               tuple(self.resolved_members))
        if self.connection_id is not None:
            self._validate_connection_id(self.connection_id)
        if self.kind in PATH_SHAPED_KINDS:
            self._validate_path_selectors()
        if self.kind == KIND_WEB:
            self._validate_web_selectors()
        if self.kind == KIND_LINEAR:
            self._validate_linear_selectors()
        elif self.resolved_members is not None:
            raise ScopeRecordError(
                f"kind {self.kind!r} carries resolved_members, which only a "
                f"{KIND_LINEAR!r} filtered-issues link declares; every other kind "
                "freezes its BOUNDARY and tests membership live"
            )

    # -- validation --------------------------------------------------------- #

    @staticmethod
    def _validate_connection_id(value: str) -> None:
        if not isinstance(value, str) or not CONNECTION_ID_RE.match(value):
            raise ScopeRecordError(
                "connection_id must be an opaque identifier of at most 128 "
                "characters from [A-Za-z0-9._:-]; the declaration doubles as the "
                "approval artifact and may be committed, so no secret-shaped "
                "value is admitted"
            )

    def _validate_path_selectors(self) -> None:
        """E4's declaration half: a declared path may never NAME a CLAUDE.md.

        Applies to every path-shaped kind (S7 widened this from `code` alone).
        The locked ``## Desired Solution`` says a CLAUDE.md is a pointer to where
        documents live and is "never quoted as a source" — that is a fact about
        the file, not about which class declared it, so a knowledge-library or
        document-folder declaration naming one is refused identically.

        This is the *declaration* half of a pair. The other half — a CLAUDE.md
        **discovered** beneath a legitimately declared folder — is refused at
        admission by the port's basename guard (`source_port.py:580`). Two
        different guards, both needed: this one cannot see a file nobody declared,
        and that one cannot see a declaration nobody read from.
        """
        for sel in self.selectors:
            if Path(sel).name == CLAUDE_MD_BASENAME:
                raise ScopeRecordError(
                    f"declared path {sel!r} names {CLAUDE_MD_BASENAME}; a CLAUDE.md "
                    "is a pointer to where documents live, never a source identity "
                    "— declare the document it points to instead"
                )

    def _validate_web_selectors(self) -> None:
        """A declared web selector must be a real http(s) location, and must carry
        no credential.

        Two refusals, both at CONSTRUCTION so they surface before any read:

        * **Unparseable.** A selector that is not an http(s) host-or-URL would
          silently match nothing, which reads to a person as "the research found
          nothing there" rather than "that was not a location". Refuse it with
          its reason instead.
        * **Embedded userinfo.** `https://user:token@host/` puts a bearer
          credential in the declaration — and the declaration doubles as the
          approval artifact and may be committed or shared, which is exactly the
          reasoning `connection_id` already carries.
        """
        for sel in self.selectors:
            text = str(sel).strip()
            host_part = text.split("://", 1)[-1].split("/", 1)[0]
            if "@" in host_part:
                raise ScopeRecordError(
                    f"declared web source {sel!r} embeds credentials in the URL; "
                    "the declaration doubles as the approval artifact and may be "
                    "committed, so no secret-bearing value is admitted — declare "
                    "the host and path alone"
                )
            if _normalise_url(sel, assume_scheme=True) is None:
                raise ScopeRecordError(
                    f"declared web source {sel!r} is not an http(s) location; a "
                    "selector that matches nothing would read as an empty result "
                    "rather than as a bad declaration"
                )

    def _validate_linear_selectors(self) -> None:
        """A declared Linear selector is either a project identifier or a
        filtered-issues link — and a link must carry its resolved membership.

        Three refusals, all at CONSTRUCTION so they surface before any read:

        * **An unresolved link.** A link selector whose `resolved_members` is
          `None` has never been resolved, so `check()` could only admit
          everything or refuse everything. Refusing it here is what makes A7's
          "an unresolved link must not be approvable" structural rather than a
          convention — and it is why `None` and `()` had to stay distinguishable:
          a filter that legitimately matched nothing carries `()` and is admitted.
        * **Membership without a link.** `resolved_members` on a project-only
          declaration is a frozen set nothing produced; refuse rather than
          silently ignore it.
        * **A malformed project identifier.** A selector that is neither a link
          nor a well-formed key would match nothing, which reads to a person as
          "the research found nothing there" rather than "that was not a
          project".
        """
        if self.scope_mode == SCOPE_MODE_UNSCOPED:
            if self.resolved_members is not None:
                raise ScopeRecordError(
                    "an unscoped Linear declaration carries resolved_members; "
                    "unscoped means the bound is the workspace's own exposure, "
                    "so there is no filter to freeze")
            return
        has_link = any(_is_linear_link_selector(s) for s in self.selectors)
        if has_link and self.resolved_members is None:
            raise ScopeRecordError(
                "a Linear filtered-issues link is declared but carries no "
                "resolved membership; the link must be resolved to the set of "
                "issues it matched at selection time, so the person approves the "
                "resolved set rather than the link that produced it (a filter "
                "matching zero issues is resolved-and-empty, not unresolved)")
        if self.resolved_members is not None and not has_link:
            raise ScopeRecordError(
                "resolved_members is declared but no selector is a "
                "filtered-issues link; a frozen membership with nothing that "
                "produced it cannot be re-derived or re-approved")
        for sel in self.selectors:
            if _is_linear_link_selector(sel):
                continue
            if not re.match(r"\A[A-Za-z][A-Za-z0-9_]*\Z", str(sel).strip()):
                raise ScopeRecordError(
                    f"declared Linear project {sel!r} is not a project identifier "
                    "(a key such as 'ENG') and is not a filtered-issues link; a "
                    "selector that matches nothing would read as an empty result "
                    "rather than as a bad declaration")

    # -- containment -------------------------------------------------------- #

    def check(self, target: Union[str, Path]) -> CheckResult:
        """Is `target` inside this declaration? Dispatched on kind."""
        if self.kind in PATH_SHAPED_KINDS:
            return self._check_path(target)
        if self.kind == KIND_WEB:
            return self._check_web(target)
        if self.kind == KIND_LINEAR:
            return self._check_linear(target)
        # Kept so adding a kind to REGISTERED_KINDS without a check arm fails
        # loudly rather than defaulting to admit.
        raise ScopeRecordError(
            f"kind {self.kind!r} is registered but has no containment check"
        )

    def _check_linear(self, target: Union[str, Path]) -> CheckResult:
        """Containment for the `linear` kind (S8, design-A12/A4).

        Structurally the same three branches as `_check_path` and `_check_web`,
        so the kinds stay legible side by side; what differs is only what
        "resolved" and "within" mean for an issue identity.

        **This is a PURE predicate and must stay one.** It takes only
        `(kind, target)`, and `admit()` calls it at step 1 — before any read, and
        OUTSIDE the try/except that opens at step 3 (`source_port.py:784` vs
        `:808`), so anything raised here aborts the whole run rather than
        degrading one item. It therefore holds no adapter, no transport and no
        credential, and resolves nothing over the network. A filtered-issues link
        was already resolved to its membership at SELECTION time (A7), where
        credentials exist and the record is not yet frozen; by the time this runs,
        a link bound and a project bound are the same membership test.

        An earlier draft of this arm resolved the filter here. That put
        network- and credential-dependent work inside a pre-read predicate that
        holds neither — so it could only be stubbed, silently admitting or
        silently refusing. It is the failure this arm exists to prevent,
        reintroduced by its own repair, and it is recorded rather than absorbed.

        **The asymmetry this creates is deliberate and is a carried limitation.**
        Every other kind freezes the BOUNDARY and tests membership live, so a file
        added to a declared directory after approval is still admitted. A link
        bound freezes the RESULT: an issue that starts matching afterwards is
        refused, and one that stops matching stays admissible. That is the price
        of keeping an INDEPENDENT refusal for a query-shaped selector, which has
        no boundary a credential-less predicate could test.
        """
        shown = str(target).strip()
        if self.scope_mode == SCOPE_MODE_UNSCOPED:
            return Admission(kind=self.kind, resolved_target=shown,
                             matched_selector=None,
                             scope_mode=self.scope_mode)
        if not self.selectors:
            # E6's rule, unchanged for this kind: an ENUMERATED record naming
            # nothing admits nothing.
            return Refusal(
                kind=self.kind, resolved_target=shown,
                reason="declaration is enumerated and names no sources; "
                       "nothing is in scope")
        issue = LINEAR_ISSUE_RE.match(shown)
        if issue is None:
            # Not an issue identity at all. Refused rather than raised: the caller
            # records it, exactly as it records an out-of-scope read.
            return Refusal(
                kind=self.kind, resolved_target=shown,
                reason="not a Linear issue identity (expected a form such as "
                       "'ENG-123'), so it cannot be admitted as a Linear source")
        # A link bound: membership is the frozen set, compared case-insensitively
        # because Linear renders an issue key in either case.
        if self.resolved_members is not None:
            frozen = {m.strip().upper() for m in self.resolved_members}
            if shown.upper() in frozen:
                return Admission(kind=self.kind, resolved_target=shown,
                                 matched_selector=next(
                                     (s for s in self.selectors
                                      if _is_linear_link_selector(s)), None),
                                 scope_mode=self.scope_mode)
            return Refusal(
                kind=self.kind, resolved_target=shown,
                reason=f"issue lies outside the {len(frozen)} issue(s) the "
                       "declared filtered-issues link matched when it was "
                       "approved")
        # A project bound: the issue's key half must be a declared project.
        key = issue.group(1).upper()
        for sel in self.selectors:
            if str(sel).strip().upper() == key:
                return Admission(kind=self.kind, resolved_target=shown,
                                 matched_selector=str(sel).strip(),
                                 scope_mode=self.scope_mode)
        return Refusal(
            kind=self.kind, resolved_target=shown,
            reason="issue lies outside every declared project "
                   f"({[str(s).strip() for s in self.selectors]})")

    def _check_web(self, target: Union[str, Path]) -> CheckResult:
        """Containment for the `web` kind (S6, design-A29).

        Structurally the same three branches as `_check_path`, so the kinds
        stay legible side by side; what differs is only what "resolved" and
        "within" mean for a URL.
        """
        resolved = _normalise_url(target)
        if resolved is None:
            # Not an http(s) URL at all. Refused rather than raised: the caller
            # records it, exactly as it records an out-of-scope read.
            return Refusal(
                kind=self.kind, resolved_target=str(target),
                reason="not an http(s) URL, so it cannot be admitted as a web "
                       "source")
        shown = resolved.render()
        if self.scope_mode == SCOPE_MODE_UNSCOPED:
            return Admission(kind=self.kind, resolved_target=shown,
                             matched_selector=None,
                             scope_mode=self.scope_mode)
        if not self.selectors:
            # E6's rule, unchanged for this kind: an ENUMERATED record naming
            # nothing admits nothing.
            return Refusal(
                kind=self.kind, resolved_target=shown,
                reason="declaration is enumerated and names no sources; "
                       "nothing is in scope")
        for sel in self.selectors:
            root = _normalise_url(sel, assume_scheme=True)
            if root is not None and _url_is_within(resolved, root):
                return Admission(kind=self.kind, resolved_target=shown,
                                 matched_selector=root.render(),
                                 scope_mode=self.scope_mode)
        return Refusal(
            kind=self.kind, resolved_target=shown,
            reason="URL lies outside every declared selector "
                   f"({[ (_normalise_url(s, assume_scheme=True) or _NormalisedUrl((str(s), ''))).render() for s in self.selectors ]})")

    def _check_path(self, target: Union[str, Path]) -> CheckResult:
        """Containment for every path-shaped kind — `code`, `knowledge_library`,
        `document_folder`.

        ONE implementation, not three (S7). The result's `kind` comes from
        `self.kind`, so the arms differ in nothing a copy would have had to keep
        in step. Refusal names the **RESOLVED** path, which is what makes a
        symlink pointing out of scope refused by where it LANDED rather than by
        the innocent-looking name it was declared under.
        """
        resolved = _resolve(target)
        if self.scope_mode == SCOPE_MODE_UNSCOPED:
            return Admission(kind=self.kind, resolved_target=str(resolved),
                             matched_selector=None,
                             scope_mode=self.scope_mode)
        if not self.selectors:
            # E6: an ENUMERATED record with no selectors admits nothing rather
            # than everything. The qualifier is load-bearing — the same rule
            # applied to an unscoped record would invert the mode above.
            return Refusal(
                kind=self.kind, resolved_target=str(resolved),
                reason="declaration is enumerated and names no sources; "
                       "nothing is in scope")
        for sel in self.selectors:
            root = _resolve(sel)
            if _is_within(resolved, root):
                return Admission(kind=self.kind, resolved_target=str(resolved),
                                 matched_selector=str(root),
                                 scope_mode=self.scope_mode)
        return Refusal(
            kind=self.kind, resolved_target=str(resolved),
            reason="resolved path lies outside every declared selector "
                   f"({[str(_resolve(s)) for s in self.selectors]})")

    # -- serialization ------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "kind": self.kind,
            "scope_mode": self.scope_mode,
            "selectors": list(self.selectors),
            "connection_id": self.connection_id,
        }
        # Emitted ONLY when a link bound froze one. Writing `null` for every other
        # kind would put a Linear-shaped field in every record, and reading it back
        # would then hit the "resolved_members on a non-linear kind" refusal.
        if self.resolved_members is not None:
            out["resolved_members"] = list(self.resolved_members)
        return out

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DeclaredSource":
        if not isinstance(data, Mapping):
            raise ScopeRecordError(f"a declared source must be an object, got {type(data).__name__}")
        # `None` (absent) and `[]` (resolved-to-empty) must survive the round trip
        # distinctly — see the field's own note. `data.get(...) or ()` would
        # collapse them, which is why this is an explicit membership test.
        raw_members = data.get("resolved_members")
        return cls(
            kind=data.get("kind"),
            scope_mode=data.get("scope_mode", SCOPE_MODE_ENUMERATED),
            selectors=tuple(data.get("selectors") or ()),
            connection_id=data.get("connection_id"),
            resolved_members=(None if raw_members is None else tuple(raw_members)),
        )


# --------------------------------------------------------------------------- #
# The record.
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ScopeRecord:
    """The immutable declaration for one admission run.

    Created once, never mutated. `check(kind, target)` is what every read is
    tested against BEFORE it happens.
    """

    sources: Tuple[DeclaredSource, ...] = ()
    created_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "sources", tuple(self.sources or ()))
        if not self.created_at:
            object.__setattr__(
                self, "created_at",
                datetime.now(timezone.utc).replace(microsecond=0).isoformat())

    # -- containment -------------------------------------------------------- #

    def sources_of_kind(self, kind: str) -> Tuple[DeclaredSource, ...]:
        return tuple(s for s in self.sources if s.kind == kind)

    def check(self, kind: str, target: Union[str, Path]) -> CheckResult:
        """Admit `target` iff SOME declared source of `kind` contains it.

        An unregistered kind, or a kind the record declares nothing for, is a
        refusal — never an admission. Fail-closed in both directions.
        """
        if kind not in REGISTERED_KINDS:
            return Refusal(
                kind=kind, resolved_target=str(target),
                reason=f"unknown source kind {kind!r}; registered kinds: "
                       f"{list(REGISTERED_KINDS)}")
        declared = self.sources_of_kind(kind)
        if not declared:
            return Refusal(
                kind=kind, resolved_target=_display_target(kind, target),
                reason=f"the declaration names no source of kind {kind!r}")
        last: Optional[Refusal] = None
        for source in declared:
            result = source.check(target)
            if result.admitted:
                return result
            last = result           # report the last refusal's resolved path
        return last

    def selectors_for(self, kind: str) -> Tuple[str, ...]:
        """Every selector declared for `kind`, in declaration order."""
        out = []
        for s in self.sources_of_kind(kind):
            out.extend(s.selectors)
        return tuple(out)

    # -- serialization ------------------------------------------------------ #

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": self.created_at,
            "sources": [s.to_dict() for s in self.sources],
        }

    def to_json(self, *, indent: int = 2) -> str:
        """The cross-surface form the picker writes and the readers read back.

        Not a ``jq`` hook's input, despite what this line used to say: the
        consumers are `research_pipeline` (shape, at registration) and
        `declared_read` / `source_port` (content, at admission). See the module
        docstring.
        """
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ScopeRecord":
        if not isinstance(data, Mapping):
            raise ScopeRecordError("a scope record must be a JSON object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            # An unknown schema_version is REFUSE, never admit — that clause is
            # what makes a later schema additive instead of a bash-side rewrite.
            raise ScopeRecordError(
                f"unsupported schema_version {version!r}; this build reads "
                f"{SCHEMA_VERSION} only (an unrecognised version refuses)")
        raw = data.get("sources")
        if raw is None:
            raise ScopeRecordError("scope record has no 'sources' key")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
            raise ScopeRecordError("'sources' must be a list")
        return cls(
            sources=tuple(DeclaredSource.from_dict(s) for s in raw),
            created_at=str(data.get("created_at") or ""),
        )

    @classmethod
    def from_json(cls, text: str) -> "ScopeRecord":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ScopeRecordError(f"scope record is not valid JSON: {e}") from None
        return cls.from_dict(data)


def code_scope(selectors: Iterable[Union[str, Path]],
               *, connection_id: Optional[str] = None) -> ScopeRecord:
    """Convenience constructor for an enumerated code declaration."""
    return ScopeRecord(sources=(
        DeclaredSource(kind=KIND_CODE,
                       scope_mode=SCOPE_MODE_ENUMERATED,
                       selectors=tuple(str(s) for s in selectors),
                       connection_id=connection_id),))


def knowledge_library_scope(
    selectors: Iterable[Union[str, Path]],
    *, connection_id: Optional[str] = None,
) -> ScopeRecord:
    """Convenience constructor for an enumerated knowledge-library declaration (S7).

    There is no unscoped form and this constructor cannot express one: an empty
    selector list yields an ENUMERATED record, which admits nothing (E6). That is
    the intended reading — a library nobody named is a library nothing was
    approved from, not a library with no bound.
    """
    return ScopeRecord(sources=(
        DeclaredSource(kind=KIND_KNOWLEDGE_LIBRARY,
                       scope_mode=SCOPE_MODE_ENUMERATED,
                       selectors=tuple(str(s) for s in selectors),
                       connection_id=connection_id),))


def document_folder_scope(
    selectors: Iterable[Union[str, Path]],
    *, connection_id: Optional[str] = None,
) -> ScopeRecord:
    """Convenience constructor for an enumerated document-folder declaration (S7).

    A mounted cloud-drive folder is declared through this constructor like any
    other folder and gets no branch of its own — that IS design-A31.
    """
    return ScopeRecord(sources=(
        DeclaredSource(kind=KIND_DOCUMENT_FOLDER,
                       scope_mode=SCOPE_MODE_ENUMERATED,
                       selectors=tuple(str(s) for s in selectors),
                       connection_id=connection_id),))


def linear_scope(
    selectors: Iterable[Union[str, Path]] = (),
    *,
    resolved_members: Optional[Iterable[str]] = None,
    connection_id: Optional[str] = None,
) -> ScopeRecord:
    """Convenience constructor for a Linear declaration (S8, design-A12).

    Found by DERIVATION rather than by reading the action list: every other
    registered kind carries a `<kind>_scope` helper, and without this one Linear
    would be the single kind a caller had to hand-build a `DeclaredSource` for.

    Passing no selectors yields the **unscoped** mode — "the bound is the source's
    own exposure", which for a tracker means whatever the workspace exposes. That
    follows `web_scope`'s reading rather than `knowledge_library_scope`'s, because
    `linear` permits unscoped and the two on-disk kinds do not.

    `resolved_members` is the frozen membership of a filtered-issues LINK bound,
    resolved at selection time. `None` means not-yet-resolved and an empty tuple
    means resolved-and-matched-nothing; the two are different declarations and the
    constructor preserves the difference rather than collapsing it.
    """
    selectors = tuple(str(s) for s in selectors)
    return ScopeRecord(sources=(
        DeclaredSource(
            kind=KIND_LINEAR,
            scope_mode=SCOPE_MODE_ENUMERATED if selectors else SCOPE_MODE_UNSCOPED,
            selectors=selectors,
            resolved_members=(None if resolved_members is None
                              else tuple(resolved_members)),
            connection_id=connection_id),))


def web_scope(selectors: Iterable[Union[str, Path]] = ()) -> ScopeRecord:
    """Convenience constructor for a web declaration (S6, design-A29).

    Passing no selectors yields the **unscoped** mode — "the bound is the source's
    own exposure", which for web means the open web. That is deliberately NOT the
    same as an enumerated record with an empty selector list, which admits
    nothing: the two are different declarations and this constructor cannot
    express the second by accident.
    """
    selectors = tuple(str(s) for s in selectors)
    return ScopeRecord(sources=(
        DeclaredSource(
            kind=KIND_WEB,
            scope_mode=SCOPE_MODE_ENUMERATED if selectors else SCOPE_MODE_UNSCOPED,
            selectors=selectors),))

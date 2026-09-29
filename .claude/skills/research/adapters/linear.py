"""The Linear adapter — issues in a tracker, read over MCP.

research-source-adapters S8 / plan action A10 (design-A2, design-A12, design-A16).

Three things and no more, exactly as ``code_base.py`` and ``document_folder.py``:

* ``enumerate_within`` — a **lazy generator** yielding candidates, applying **no
  bound** and holding **no bound constant**. The port consumes it and stops at its
  own limits, which is what makes "the adapter cannot widen a bound" structural.
* ``read_with_pin`` — reads one issue and returns the facts the pin needs (the
  workspace identity, the version read, an issue locator).
* ``can_reopen_without_credentials`` — a **declared capability**, not a decision.
  ``False`` here, and that one line is what makes the whole of G5 true: the port
  reads it (``source_port.py`` step 8) and takes the CAPTURED evidence branch, so
  every Linear claim leaves a stored excerpt a checker holding no credentials can
  read. Whether that branch is selected is still the port's call, never this
  module's.

**Thin by design, and that is a test of the earlier sessions rather than of this
one.** The transport is A6's and the credential is A5's; neither is built here and
neither may leak into this module's signature. If ``LinearAdapter.__init__`` ever
grows an OAuth argument, the responsibility has leaked and the split was wrong
(the plan's Responsibility Alignment finding, stated in advance).

**The frozen membership is enumerated, never re-resolved.** A declaration bounded
by a filtered-issues link carries the membership A7 froze at selection time. This
adapter enumerates THAT SET and does not re-run the filter. A live re-resolve
would look like a freshness feature and would be a defect: ``check()`` tests
against the frozen set, so an issue that entered the filter after approval would
be enumerated here and then refused under ``OBLIGATION_DECLARED_SCOPE`` — reported
to the person as reading outside their own declaration, when it read exactly what
their filter now says.

**Read-only** (design-A24). Every MCP call this module makes goes through a tool
chosen by :func:`connect_gate._pick_read_tool`, which refuses to guess into a
mutating tool; no tool name is hard-coded here, because the server's surface is a
finding rather than a formality.

**The identity is not a field.** The Session-2 live round-trip established that
neither ``list_issues`` nor ``get_issue`` returns an ``identifier`` — the
``ENG-123`` form lives in the issue ``url``. The derivation is
:func:`scope_record.linear_identifier_from_url`, shared with the connect gate's
freeze so the two cannot drift; an adapter deriving its own would produce targets
the containment check refuses.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterator, Optional, Sequence

from .. import connect_gate as _gate
from .. import mcp_transport as _mcp
from ..locator_grammar import linear_locator
from ..scope_record import (
    KIND_LINEAR,
    SCOPE_MODE_UNSCOPED,
    ScopeRecord,
    _is_linear_link_selector,
    linear_identifier_from_url,
)
from ..source_port import (
    AdapterError,
    OBLIGATION_READABLE,
    OBLIGATION_SOURCE_IDENTITY,
    OBLIGATION_VERSION_READ,
    ReadResult,
    SourceAdapter,
    SourceItem,
)

#: The obligation every remote-only failure mode degrades under (A14).
#:
#: `admit()` forces `OBLIGATION_READABLE` only for `OSError` and its catch-all; an
#: `AdapterError` is recorded under whatever obligation the RAISING adapter set
#: (`source_port.py` step 3). So the generic machinery records every failure but
#: does not name THIS class of them for us — the adapter must tag its own, which
#: is what makes "expired authorization" reach the person as those words rather
#: than as an anonymous unreadable item.
_REMOTE_OBLIGATION = OBLIGATION_READABLE


#: Plain-English lead for each mode the transport can name (A14 / U7 / U8).
#:
#: Every mode `mcp_transport` defines has a row. An exhaustiveness test asserts
#: that, so a mode added to the transport later fails the suite rather than
#: silently reaching a person as the transport's own internal wording.
_DEGRADATION_LEAD = {
    _mcp.DEGRADATION_AUTH_EXPIRED:
        "your Linear authorization has expired or been revoked, so this issue "
        "was not read; reconnect Linear and run this again",
    _mcp.DEGRADATION_REFRESH_FAILED:
        "your Linear authorization could not be renewed, so this issue was not "
        "read; reconnect Linear and run this again",
    _mcp.DEGRADATION_SCOPE_REVOKED:
        "the Linear authorization no longer carries read access to this issue, "
        "so it was not read; reconnect Linear and run this again",
    _mcp.DEGRADATION_RATE_LIMITED:
        "Linear is rate-limiting this client, so this issue was not read; the "
        "read stopped rather than retrying into a longer block",
    _mcp.DEGRADATION_UNREACHABLE:
        "Linear could not be reached, so this issue was not read; the rest of "
        "the run continued without it",
    _mcp.DEGRADATION_PROTOCOL:
        "Linear answered in a form this client could not read, so this issue "
        "was not read",
}


def _degradation_sentence(mode: str, reason: str) -> str:
    """The words a person reads for one remote failure, in plain English.

    Keyed on the transport's own named mode so the vocabulary has one home. An
    unmapped mode falls through to the transport's own reason rather than to a
    generic string — it reads as itself rather than as "something went wrong",
    which is the failure a catch-all sentence would introduce.
    """
    lead = _DEGRADATION_LEAD.get(mode)
    return f"{lead} ({reason})" if lead else reason


class LinearAdapter(SourceAdapter):
    """Reads Linear issues through the MCP transport. Holds no bound, mints no pin."""

    kind = KIND_LINEAR

    # The declared capability. `False` because a checker re-opening this claim
    # would need the operator's Linear authorization, which it does not have — so
    # the port stores the excerpt instead and the citation stays checkable.
    can_reopen_without_credentials = False

    def __init__(self,
                 transport: _mcp.McpTransport,
                 access_token: str,
                 *,
                 read_tool: Optional[str] = None,
                 workspace: Optional[str] = None,
                 extract: Optional[Callable[[Any], Sequence[str]]] = None) -> None:
        """`transport` and `access_token` are A6's and A5's — built elsewhere.

        `read_tool` / `workspace` are discovered from the server when omitted, and
        injectable so a test can drive this adapter without a network. Nothing here
        builds a transport, opens a browser, or touches a credential store: those
        are the earlier sessions' modules, and an argument of theirs appearing in
        this signature would mean the split was wrong.

        **One tool, not two.** An earlier draft took a separate `list_tool`; it was
        never used, and a constructor argument that changes nothing is a false
        affordance a caller may reasonably believe in. Listing and reading go
        through the same discovered read tool.
        """
        self.transport = transport
        self.access_token = access_token
        self._read_tool = read_tool
        self._workspace = workspace
        self._extract = extract

    # -- discovery ---------------------------------------------------------- #

    def _tool(self) -> str:
        """The read-shaped tool, asked for once and remembered for the run.

        Never pinned to a name here. `_pick_read_tool` prefers the server's own
        `readOnlyHint` annotation and REFUSES a surface with nothing read-shaped
        rather than picking arbitrarily — which is the behaviour that keeps a
        `read`-scoped client from calling into a mutating tool.
        """
        if self._read_tool is None:
            tools = self.transport.list_tools(self.access_token)
            self._read_tool = _gate._pick_read_tool(tools)
        return self._read_tool

    def _workspace_id(self) -> str:
        """The workspace half of the pin, from the server's own `get_workspace`.

        Session-2's live round-trip established this is where it comes from. A
        failure here is NOT fatal to the run: the pin needs *an* identity, and the
        adapter would rather degrade one item with a named reason than abort.
        """
        if self._workspace is None:
            try:
                result = self.transport.call_tool(
                    self.access_token, "get_workspace")
            except _mcp.McpTransportError as e:
                raise AdapterError(
                    _REMOTE_OBLIGATION,
                    _degradation_sentence(e.degradation, str(e))) from None
            # THE URL SLUG FIRST, and that ordering is load-bearing. The live
            # `get_workspace` returns `id` (a UUID), `name` (a display name a
            # person can rename, and which may carry spaces) and `url`. The slug
            # in the URL is the same token every issue URL carries, so pinning to
            # it keeps `linear:<workspace>@<version>:<issue>` coherent with the
            # issues it addresses; pinning to the display name would produce a pin
            # that stops matching the moment someone renames the workspace.
            self._workspace = (_workspace_slug(_first_str(result, ("url",)))
                               or _first_str(result, ("urlKey", "key", "name", "id"))
                               or "")
        return self._workspace

    # -- enumeration -------------------------------------------------------- #

    def enumerate_within(self, scope: ScopeRecord) -> Iterator[SourceItem]:
        """Yield candidate issue identities lazily, from WHAT THE RECORD DECLARES.

        Three shapes, and the first is the one that must never become a re-resolve:

        * **A filtered-issues link** — yield the membership A7 froze, in order. No
          call is made: the set is already the person's approved bound, and asking
          the server again would produce a different one they never approved.
        * **Named projects** — ask the server for each declared project's issues.
        * **Unscoped** — ask the server for what it gives, with no filter. The
          port's item ceiling is what stops it; no constant lives here.

        Lazy by construction: this is a generator, and each server page is yielded
        as it is read rather than collected first. A consumer that takes one item
        does not pay for the rest — which the port relies on, since it stops at its
        own bounds partway through.
        """
        for source in scope.sources_of_kind(self.kind):
            if source.resolved_members is not None:
                for member in source.resolved_members:
                    identity = str(member).strip()
                    if identity:
                        yield SourceItem(item_id=identity, kind=self.kind,
                                         target=identity, depth=0)
                continue
            if source.scope_mode == SCOPE_MODE_UNSCOPED:
                yield from self._issues_matching({})
                continue
            for selector in source.selectors:
                if _is_linear_link_selector(selector):
                    # A link with no frozen membership never reaches a read: A7's
                    # own gate refuses to let an unresolved link be approved, so
                    # arriving here means the record was built past that gate.
                    # Skipping is the honest response — re-resolving would admit a
                    # bound nobody approved.
                    continue
                yield from self._issues_matching({"query": str(selector).strip()})

    def _issues_matching(self, arguments: Dict[str, Any]) -> Iterator[SourceItem]:
        """One listing call, yielded item by item.

        A transport failure during ENUMERATION cannot be reported as a degradation
        (the port degrades ITEMS, and there is no item yet), so it is raised. The
        caller — `bounded_items`, then `read_declared_sources` — is where a run-level
        failure belongs; swallowing it here would return a short list indistinguishable
        from a small project, which is the silent-short-read this whole design refuses.
        """
        result = self.transport.call_tool(
            self.access_token, self._tool(), arguments)
        extract = self._extract or _gate._default_extract_issue_ids
        for identity in dict.fromkeys(extract(result)):
            text = str(identity).strip()
            if text:
                yield SourceItem(item_id=text, kind=self.kind, target=text, depth=0)

    # -- read ---------------------------------------------------------------- #

    def read_with_pin(self, item: SourceItem) -> ReadResult:
        """Read one issue and report what the pin needs.

        Raises :class:`AdapterError` naming the failed obligation — the port turns
        it into a recorded degradation next to the affected material. This adapter
        never decides that an item is rejected; it reports which obligation it
        could not satisfy, and never returns a half-answer.
        """
        try:
            result = self.transport.call_tool(
                self.access_token, self._tool(), {"query": item.target})
        except _mcp.McpTransportError as e:
            raise AdapterError(_REMOTE_OBLIGATION,
                               _degradation_sentence(e.degradation, str(e))) from None

        issue = _find_issue(result, item.target)
        if issue is None:
            raise AdapterError(
                _REMOTE_OBLIGATION,
                f"Linear returned no issue {item.target}; it may have been "
                "deleted, or moved out of what this authorization can see")

        version = _first_str(issue, ("updatedAt",))
        if not version:
            # The version IS the point for a tracker: an issue is mutable, so a
            # citation with no version would address "whatever it says now".
            raise AdapterError(
                OBLIGATION_VERSION_READ,
                f"Linear issue {item.target} exposes no last-updated time to "
                "pin, so the read cannot be addressed later")

        workspace = self._workspace_id()
        if not workspace:
            raise AdapterError(
                OBLIGATION_SOURCE_IDENTITY,
                "Linear exposes no workspace identity to pin this issue against")

        content = _issue_text(issue)
        if not content:
            raise AdapterError(
                _REMOTE_OBLIGATION,
                f"Linear issue {item.target} carries no readable text")

        return ReadResult(
            source_id=workspace,
            version=version,
            locator=linear_locator(issue=item.target),
            content=content,
        )


# --------------------------------------------------------------------------- #
# Envelope readers.
#
# The exact tool-result envelope is a FINDING rather than a formality (the same
# reason `connect_gate` states for its own extractor), so these read tolerantly
# and never invent a value: an absent field comes back empty and the caller raises
# a NAMED obligation, rather than a plausible-looking default reaching a pin.
# --------------------------------------------------------------------------- #

def _workspace_slug(url: str) -> str:
    """The workspace token in a Linear URL — `example-workspace` in
    `https://linear.app/example-workspace`. Empty when there is none to take."""
    text = str(url or "").strip()
    if not text.lower().startswith(("http://", "https://")):
        return ""
    path = text.split("://", 1)[1].split("#", 1)[0].split("?", 1)[0]
    segments = [s for s in path.split("/")[1:] if s]
    return segments[0] if segments else ""


def _first_str(obj: Any, keys: Sequence[str]) -> str:
    """The first non-empty string at `keys`, searched depth-first."""
    if isinstance(obj, dict):
        for key in keys:
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for val in obj.values():
            found = _first_str(val, keys)
            if found:
                return found
    elif isinstance(obj, (list, tuple)):
        for val in obj:
            found = _first_str(val, keys)
            if found:
                return found
    return ""


def _find_issue(result: Any, identity: str) -> Optional[Dict[str, Any]]:
    """The issue dict whose URL-derived identity is `identity`.

    Matched on the DERIVED identity rather than on position, because a listing
    tool answers a query with whatever it considers relevant and the first row is
    not reliably the one asked for. Taking `result[0]` would pin one issue's
    version onto another issue's citation — a wrong pin is worse than no pin.
    """
    want = identity.strip().upper()

    def walk(obj: Any) -> Optional[Dict[str, Any]]:
        if isinstance(obj, dict):
            derived = linear_identifier_from_url(obj.get("url") or "")
            if derived and derived.upper() == want:
                return obj
            for val in obj.values():
                found = walk(val)
                if found is not None:
                    return found
        elif isinstance(obj, (list, tuple)):
            for val in obj:
                found = walk(val)
                if found is not None:
                    return found
        return None

    return walk(result)


def _issue_text(issue: Dict[str, Any]) -> str:
    """The issue rendered as the text a claim is drawn from.

    Title and description, because those are what a person cites. Comments are not
    folded in: the locator grammar gives `linear` an optional `comment` part
    precisely so a comment is addressed as itself, and silently concatenating
    comments into the issue body would make an issue-level pin address more than
    it names.
    """
    title = _first_str(issue, ("title",))
    body = ""
    for key in ("description", "descriptionMarkdown", "body"):
        val = issue.get(key)
        if isinstance(val, str) and val.strip():
            body = val.strip()
            break
    if title and body:
        return f"{title}\n\n{body}"
    return title or body

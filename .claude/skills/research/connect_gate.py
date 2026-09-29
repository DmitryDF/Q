"""The connect-now step, and the freeze a query-shaped bound needs.

research-source-adapters S8 / plan action A7 (U4, design-A4).

Three responsibilities, and the plan gates all three:

1. **Connect.** Selecting a source the person has not yet connected offers the
   connection *in place*, and the person comes back to the same selection list.
2. **Resolve and freeze.** A bound given as a filtered-issues link is resolved to
   its membership ONCE, here, and that membership goes into the record.
3. **Render the resolved bound at approval.** This is not a display detail. It is
   what makes the freeze something a person consented to rather than something
   done to them — so a bundle that shows only the link is a FAILURE, not a
   terser success.

**Why the resolve belongs here and nowhere else.** `DeclaredSource.check()` is a
pure predicate reached at `admit()` step 1, before any read and outside the
try/except — it holds no transport and no credential, and a raise there aborts
the whole run rather than degrading one item. Selection time is the only moment
where credentials exist (A5/A6 have run) and the record is not yet immutable
(design-A4 freezes it from approval onward). An earlier draft of the plan put
this resolve inside `check()`; that put network- and credential-dependent work
into a predicate that holds neither, so it could only be stubbed — silently
admitting or silently refusing.

**The asymmetry this creates is real, and is surfaced rather than absorbed.**
Every other kind freezes the BOUNDARY and tests membership live, so a file added
to a declared folder after approval is still admitted. A filtered-issues bound
freezes the RESULT: an issue that starts matching afterwards is not read, and one
that stops matching still is. A query-shaped selector has no boundary a
credential-less predicate could test, so freezing is the only way to keep an
INDEPENDENT refusal rather than a rubber stamp on whatever the adapter yielded.
The person therefore approves the resolved SET, not the link.

The interactive surfaces themselves (the browser open, the question a person
answers) are driven by the skill — Python cannot ask a question or own a consent
decision. This module owns the deterministic half: what state the connection is
in, what the resolve produced, and what the approval bundle must say.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional, Sequence, Tuple

from . import credential_path as _cred
from . import mcp_transport as _mcp
from . import scope_record as _scope

#: Connection states a selected source can be in.
CONNECTION_ABSENT = "absent"          # never connected — offer the connect step
CONNECTION_PRESENT = "present"        # a credential is stored for this connection

#: What the person chose after the connect step. `DROP` must leave NOTHING behind.
OUTCOME_PROCEED = "proceed"
OUTCOME_DROP = "drop"
CONNECT_OUTCOMES = (OUTCOME_PROCEED, OUTCOME_DROP)

#: Localization slots this gate's surfaces use (`research-scope-framing.md`).
SLOT_CONNECT_PROMPT = "connect_prompt_linear"
SLOT_CONNECT_OUTCOME = "connect_outcome_question"
SLOT_RESOLVED_NOTICE = "resolved_bound_notice"


class ConnectGateError(RuntimeError):
    """Raised when the gate is asked for something it must not produce."""


def connection_state(connection_id: Optional[str],
                     store: Optional[_cred.CredentialPort] = None) -> str:
    """Is this connection already made?

    A declaration with no `connection_id` at all is ABSENT rather than an error:
    that is precisely the first-time case the connect step exists for.
    """
    if not connection_id:
        return CONNECTION_ABSENT
    store = store or _cred.default_store()
    try:
        store.fetch(connection_id)
    except _cred.CredentialError:
        return CONNECTION_ABSENT
    return CONNECTION_PRESENT


# --------------------------------------------------------------------------- #
# The freeze
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ResolvedBound:
    """A filtered-issues link, resolved to the set it matched, with its as-of time.

    `members` is ordered and de-duplicated. `as_of` is an ISO-8601 UTC instant —
    the thing the approval bundle shows beside the count, so "the 40 issues this
    view held when you approved" is what a person consents to.
    """

    link: str
    members: Tuple[str, ...]
    as_of: str

    @property
    def count(self) -> int:
        return len(self.members)


def resolve_link_membership(
    link: str,
    *,
    access_token: str,
    transport: _mcp.McpTransport,
    tool_name: Optional[str] = None,
    now: Optional[datetime] = None,
    extract: Optional[Callable[[Any], Sequence[str]]] = None,
) -> ResolvedBound:
    """Resolve a filtered-issues link to the issue identities it currently matches.

    **`tool_name` is discovered, never assumed.** When omitted, the server's own
    `tools/list` is consulted and a read-shaped tool is chosen from it. The plan
    carries this as an explicit residual: the tool surface is a FINDING, and
    hard-coding a name here would bake in a guess that the first real
    authorization might contradict.

    A failure NAMES its mode (via `McpTransportError`) rather than returning a
    short list, because a silently-short resolve would freeze a bound smaller
    than the person's filter and nothing downstream could tell.
    """
    if not link:
        raise ConnectGateError("cannot resolve an empty link")
    if tool_name is None:
        tools = transport.list_tools(access_token)
        tool_name = _pick_read_tool(tools)
    # `call_tool`, not `call` — an MCP tool result wraps its payload in a content
    # block, and reading the envelope as the payload is what made the live probe
    # freeze a membership of `['text', '{"issues":…}']`.
    result = transport.call_tool(access_token, tool_name, {"url": link})
    members = tuple(dict.fromkeys(
        (extract or _default_extract_issue_ids)(result)))
    stamp = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return ResolvedBound(link=link, members=members, as_of=stamp.isoformat())


def _is_read_only(tool: Dict[str, Any]) -> bool:
    """Does the server ITSELF declare this tool read-only?

    MCP tools may carry `annotations.readOnlyHint` / `destructiveHint`. When a
    server states it, that is a far better signal than inferring intent from a
    name — and rung (iv) showed the live server does state it. A tool the server
    marks destructive is excluded even if its name reads harmlessly.

    Absence of the annotation is NOT taken as read-only: unknown is unknown, and
    this function's whole job is to avoid guessing into a mutating tool.
    """
    ann = tool.get("annotations") or {}
    if ann.get("destructiveHint") is True:
        return False
    return ann.get("readOnlyHint") is True


def _pick_read_tool(tools: Sequence[Dict[str, Any]]) -> str:
    """Choose a read-shaped tool from the server's advertised surface.

    Never pinned to one name: the surface is a finding this slice does not get to
    presume, and a name that exists today may not tomorrow. A surface with
    nothing read-shaped is a REFUSAL, not a guess — picking an arbitrary tool
    could invoke a mutating one, and this client asked for `read` precisely so
    that cannot happen.

    Preference order, strongest evidence first:

    1. The server's OWN `readOnlyHint` annotation, among tools whose name is
       issue-shaped. Added after rung (iv) found the live server publishes these;
       trusting a declaration beats inferring from a string.
    2. A known issue-listing name that is ALSO annotated read-only.
    3. Name shape alone, and only when nothing carries annotations at all — a
       server that publishes no hints leaves nothing better to go on.
    """
    entries = [t for t in tools if isinstance(t, dict)]
    names = [t.get("name", "") for t in entries]
    annotated = [t for t in entries if (t.get("annotations") or {})]

    def issueish(name: str) -> bool:
        low = name.lower()
        return "issue" in low and any(
            v in low for v in ("list", "search", "get", "find"))

    # 1/2 — the server's own declaration, preferring a listing verb.
    read_only = [t for t in entries if _is_read_only(t) and issueish(t.get("name", ""))]
    for want in ("list_issues", "search_issues", "list_my_issues", "get_issues"):
        for t in read_only:
            if t.get("name") == want:
                return want
    for t in read_only:
        if any(v in t.get("name", "").lower() for v in ("list", "search", "find")):
            return t["name"]
    if read_only:
        return read_only[0]["name"]

    # 3 — nothing is annotated at all: fall back to name shape.
    if not annotated:
        for want in ("list_issues", "search_issues", "list_my_issues", "get_issues"):
            if want in names:
                return want
        for name in names:
            if issueish(name):
                return name

    raise ConnectGateError(
        "the server's tool surface advertises no read-shaped issue tool "
        f"(offered: {names or 'nothing'}); refusing to guess a tool name rather "
        "than risk invoking a mutating one")


def _default_extract_issue_ids(result: Any) -> Sequence[str]:
    """Pull issue identities out of a tool result, tolerantly.

    The exact envelope is a finding (see `resolve_link_membership`), so this
    accepts the shapes an MCP tool result plausibly takes and lets a caller pass
    `extract=` when the real one differs. It never invents an identity: anything
    it cannot read as one is skipped rather than guessed at.

    **`url` is consulted BEFORE `id`, and that order is load-bearing.** The S8
    Session-2 live round-trip found that the real server returns no `identifier`
    at all: an issue carries a UUID `id` and a `url`, and the `ENG-123` identity a
    person recognises — and that :meth:`DeclaredSource._check_linear` tests
    against — lives only in the URL. Reading `id` first would freeze a membership
    of UUIDs, and every later admission would then be refused as outside the
    person's own declaration. The derivation itself lives beside
    :data:`scope_record.LINEAR_ISSUE_RE`, so the freeze and the containment check
    cannot drift apart.
    """
    def _ids(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            derived = _scope.linear_identifier_from_url(obj.get("url") or "")
            if derived:
                yield derived
                return
            for key in ("identifier", "id", "key"):
                val = obj.get(key)
                if isinstance(val, str) and val:
                    yield val
                    return
            for val in obj.values():
                yield from _ids(val)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                yield from _ids(item)

    if isinstance(result, dict):
        for key in ("issues", "results", "items", "content"):
            if key in result:
                return [i for i in _ids(result[key]) if i]
    return [i for i in _ids(result) if i]


# --------------------------------------------------------------------------- #
# The approval bundle
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class BundleLine:
    """One source's line in the approval bundle."""

    kind: str
    text: str
    resolved_count: Optional[int] = None
    as_of: Optional[str] = None
    warning: Optional[str] = None


def render_bundle_line(kind: str, selector: str,
                       resolved: Optional[ResolvedBound] = None,
                       *, item_ceiling: Optional[int] = None) -> BundleLine:
    """What the approval gate SAYS about one declared source.

    For a link bound this must name the resolved count and its as-of time. A line
    that showed only the link would ask a person to consent to a query whose
    result they have never seen — which is the whole failure this action exists
    to prevent, and which prose alone cannot stop.

    Two conditions are called out AT APPROVAL rather than at read time:
    * a filter that resolved to **zero** issues — a legitimate bound, but nobody
      should approve one believing it is populated;
    * a filter whose frozen set **already exceeds** what the run can read, so the
      mismatch does not surface later as a truncated read.
    """
    if resolved is None:
        return BundleLine(kind=kind, text=selector)

    warning = None
    if resolved.count == 0:
        warning = ("this filter matched no issues when it was resolved; "
                   "approving it declares a source that will read nothing")
    elif item_ceiling is not None and resolved.count > item_ceiling:
        warning = (f"this filter froze {resolved.count} issues, which already "
                   f"exceeds the {item_ceiling} items a run can read; the read "
                   "will stop at the limit and say so")
    return BundleLine(
        kind=kind,
        text=(f"{resolved.link} — {resolved.count} issue(s) as of {resolved.as_of}"),
        resolved_count=resolved.count,
        as_of=resolved.as_of,
        warning=warning,
    )


def bundle_line_is_approvable(line: BundleLine, *, is_link_bound: bool) -> bool:
    """Does this line say enough to be consented to?

    A link-bounded line MUST carry the resolved count and the as-of time. This is
    the predicate that makes A7's third part checkable rather than prose an
    implementation can pass while still showing only a link.
    """
    if not is_link_bound:
        return bool(line.text)
    return line.resolved_count is not None and bool(line.as_of)


def drop_source(sources: Sequence[Any], kind: str) -> Tuple[Any, ...]:
    """Remove every declared source of `kind`, leaving NO partial declaration.

    The drop path is the one A7's gate calls out specifically: a person who
    connects and then decides against the source must be left with a declaration
    that does not mention it at all — not one that mentions it without a
    connection, and not one carrying a half-built entry.
    """
    return tuple(s for s in sources if getattr(s, "kind", None) != kind)

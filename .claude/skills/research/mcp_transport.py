"""MCP-over-HTTP transport — its own OAuth client against a remote MCP server.

research-source-adapters S8 / plan action A6 (design-A24, Discovery Q5).

**The one thing that must NOT be built, stated first because it is the tempting
shortcut.** This transport must never reuse Claude Code's own stored MCP session
token. That token lives in an undocumented internal store; depending on it would
make the adapter break silently whenever the harness changes, and it would put a
credential the operator granted to ONE client into the hands of another. So this
module registers its OWN client (dynamic client registration) and holds its own
`read`-scoped token, addressed through `credential_path`. A test asserts this
file names no harness credential store.

**Source-agnostic, like the credential path beside it.** This is a transport any
remote MCP source can use — not a Linear method. The Guiding Policy's test is:
if `adapters/linear.py` comes out large, the credential path and the remote
machinery were built INSIDE it instead of beside it, and Confluence and Jira each
pay the cost again. There is therefore no Linear vocabulary here.

Why this shape at all
---------------------
An adapter in this package is a plain Python class that
`declared_read.read_declared_sources` instantiates in-process, and a
model-invoked MCP tool is not reachable from there. Probing the server settled
what to build instead: `https://mcp.linear.app/mcp` is a **remote HTTP MCP
server** — it answers `401` with `www-authenticate: Bearer realm="OAuth"`, and
its authorization-server metadata advertises `registration_endpoint`,
`scopes_supported: [read, write, openid, email]`, PKCE `S256`, and
`token_endpoint_auth_methods_supported` including `none`. So the adapter becomes
its own MCP client against the same server, which satisfies the locked
"unscoped means whatever the MCP exposes" literally — same server, same exposed
surface — while fitting the shipped adapter shape.

Growth order
------------
Built per the four-step sequence in `code_first_architecture.md`, applied here
**by analogy and not by its own terms**: that rules file scopes the sequence to
"a new **AI** adapter", and this transport calls no model. The analogy is adopted
deliberately, because the sequence's reasoning — do not let the first exercise of
a novel integration be against the live system — applies with more force to an
OAuth transport than to a model call, not less.

Dependency direction: imports nothing from the package.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

#: The ONLY scope this transport ever requests. `write` is never asked for, and a
#: grant that carries it is REFUSED rather than merely unused (design-A24).
READ_SCOPE = "read"

#: Scopes that are acceptable alongside `read` in a GRANT. An authorization
#: server may attach identity scopes it issues by default; those cannot mutate a
#: workspace, so they do not make the grant unsafe. Anything else does.
BENIGN_GRANTED_SCOPES = frozenset({"read", "openid", "email", "profile", "offline_access"})

#: How a failure is NAMED when it reaches a person. Each is a distinct mode a
#: credentialed remote fails in, and none of them collapses into "the read
#: failed" — that collapse is what the honest-degradation promise forbids.
DEGRADATION_AUTH_EXPIRED = "authorization-expired"
DEGRADATION_REFRESH_FAILED = "authorization-refresh-failed"
DEGRADATION_SCOPE_REVOKED = "scope-revoked"
DEGRADATION_RATE_LIMITED = "rate-limited"
DEGRADATION_UNREACHABLE = "server-unreachable"
DEGRADATION_PROTOCOL = "protocol-error"


class McpTransportError(RuntimeError):
    """A transport failure that NAMES its mode.

    `degradation` is the machine-readable mode a caller records, so an expired
    authorization reads differently from a rate limit in the report rather than
    both collapsing into one generic recorded reason a person cannot act on.
    """

    def __init__(self, degradation: str, message: str) -> None:
        super().__init__(message)
        self.degradation = degradation


class ScopeRefused(McpTransportError):
    """The server granted more than `read`. Refused outright, never used.

    Its own class because this is not a degradation to record and carry on from:
    a grant wider than asked for is a security condition, and the run must not
    proceed to hold that credential.
    """

    def __init__(self, message: str) -> None:
        super().__init__(DEGRADATION_SCOPE_REVOKED, message)


# --------------------------------------------------------------------------- #
# PKCE
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PkcePair:
    """A PKCE verifier and its S256 challenge (RFC 7636)."""

    verifier: str
    challenge: str
    method: str = "S256"


def make_pkce(verifier: Optional[str] = None) -> PkcePair:
    """Build a PKCE pair. `verifier` is injectable so a test can pin the challenge.

    S256 only — `plain` is never produced. The server advertises
    `code_challenge_methods_supported: ["S256"]`, and offering a weaker method we
    could not need would be a downgrade with nothing to buy it.
    """
    if verifier is None:
        verifier = base64.urlsafe_b64encode(os.urandom(64)).decode().rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return PkcePair(verifier=verifier, challenge=challenge)


# --------------------------------------------------------------------------- #
# HTTP seam — injectable so every rung below the last runs against a stub.
# --------------------------------------------------------------------------- #

@dataclass
class HttpResponse:
    status: int
    body: str
    headers: Dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        """The reply as JSON, accepting BOTH plain JSON and SSE framing.

        **Found by rung (iv), not by the stub.** A remote MCP server may answer a
        JSON-RPC POST with `content-type: text/event-stream`, framing the reply as
        `event: message` / `data: {…}` rather than as a bare body — which is what
        `https://mcp.linear.app/mcp` does. The stub in the earlier rungs replied
        with plain JSON, so nothing below rung (iv) could have surfaced this. It is
        recorded here rather than quietly handled: the staged growth order exists
        precisely to find the things a double cannot show, and this is the one it
        found.

        Only `data:` lines are read, and the LAST complete JSON object among them
        wins — an SSE stream may carry keep-alive comments and multiple events, and
        the response to a request is the last message rather than the first.
        """
        ctype = (self.headers or {}).get("content-type", "")
        body = self.body or ""
        if "text/event-stream" in ctype or body.lstrip().startswith("event:"):
            payload = None
            for line in body.splitlines():
                if not line.startswith("data:"):
                    continue
                chunk = line[len("data:"):].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    payload = json.loads(chunk)
                except (ValueError, TypeError):
                    continue
            if payload is None:
                raise McpTransportError(
                    DEGRADATION_PROTOCOL,
                    "the server sent an event stream carrying no JSON message")
            return payload
        try:
            return json.loads(body)
        except (ValueError, TypeError):
            raise McpTransportError(
                DEGRADATION_PROTOCOL,
                "the server's reply was not JSON") from None


def _default_http(method: str, url: str, *, headers=None, body=None,
                  timeout: float = 30.0) -> HttpResponse:
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=dict(headers or {}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(status=resp.status,
                                body=resp.read().decode("utf-8", "replace"),
                                headers={k.lower(): v for k, v in resp.headers.items()})
    except urllib.error.HTTPError as e:
        return HttpResponse(status=e.code,
                            body=(e.read() or b"").decode("utf-8", "replace"),
                            headers={k.lower(): v for k, v in (e.headers or {}).items()})
    except urllib.error.URLError as e:
        raise McpTransportError(
            DEGRADATION_UNREACHABLE,
            f"could not reach {url}: {getattr(e, 'reason', e)}") from None


# --------------------------------------------------------------------------- #
# The transport
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ServerMetadata:
    """What discovery told us about the authorization server."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: Optional[str] = None
    scopes_supported: Tuple[str, ...] = ()
    code_challenge_methods_supported: Tuple[str, ...] = ()


class McpTransport:
    """An OAuth-2.1 + PKCE client for one remote MCP server.

    `http` is the injectable seam. Every rung below `real-to-real` drives this
    class with a stub, which is what lets the highest-risk action in the slice be
    exercised without the live server being the first thing it meets.
    """

    def __init__(self, server_url: str, *, http=None,
                 client_name: str = "claude-research-source-adapters") -> None:
        self.server_url = server_url.rstrip("/")
        self.client_name = client_name
        self._http = http or _default_http

    # -- discovery ---------------------------------------------------------- #

    def discover(self) -> ServerMetadata:
        """Fetch the authorization-server metadata (RFC 8414)."""
        origin = urllib.parse.urlsplit(self.server_url)
        url = urllib.parse.urlunsplit(
            (origin.scheme, origin.netloc, "/.well-known/oauth-authorization-server",
             "", ""))
        resp = self._http("GET", url)
        if resp.status != 200:
            raise McpTransportError(
                DEGRADATION_UNREACHABLE,
                f"authorization-server metadata is not available at {url} "
                f"(HTTP {resp.status})")
        doc = resp.json()
        for required in ("issuer", "authorization_endpoint", "token_endpoint"):
            if not doc.get(required):
                raise McpTransportError(
                    DEGRADATION_PROTOCOL,
                    f"authorization-server metadata is missing {required!r}")
        return ServerMetadata(
            issuer=doc["issuer"],
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=doc["token_endpoint"],
            registration_endpoint=doc.get("registration_endpoint"),
            scopes_supported=tuple(doc.get("scopes_supported") or ()),
            code_challenge_methods_supported=tuple(
                doc.get("code_challenge_methods_supported") or ()),
        )

    # -- registration ------------------------------------------------------- #

    def register_client(self, meta: ServerMetadata, redirect_uri: str) -> str:
        """Register OUR OWN client and return its `client_id` (RFC 7591).

        This is the step that makes the transport independent of any credential
        the harness holds. A server with no registration endpoint is a refusal,
        not a cue to fall back on someone else's client.
        """
        if not meta.registration_endpoint:
            raise McpTransportError(
                DEGRADATION_PROTOCOL,
                "the server advertises no dynamic client registration endpoint; "
                "this transport registers its own client and will not borrow "
                "another client's credentials")
        payload = json.dumps({
            "client_name": self.client_name,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": READ_SCOPE,
        })
        resp = self._http("POST", meta.registration_endpoint,
                          headers={"content-type": "application/json"},
                          body=payload)
        if resp.status not in (200, 201):
            raise McpTransportError(
                DEGRADATION_PROTOCOL,
                f"client registration was refused (HTTP {resp.status})")
        client_id = resp.json().get("client_id")
        if not client_id:
            raise McpTransportError(
                DEGRADATION_PROTOCOL,
                "client registration returned no client_id")
        return client_id

    # -- authorization ------------------------------------------------------ #

    def authorization_url(self, meta: ServerMetadata, client_id: str,
                          redirect_uri: str, pkce: PkcePair,
                          state: Optional[str] = None) -> Tuple[str, str]:
        """The URL a person opens in their browser, plus the `state` to match back.

        Requests `read` and nothing else.
        """
        state = state or secrets.token_urlsafe(24)
        query = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": READ_SCOPE,
            "state": state,
            "code_challenge": pkce.challenge,
            "code_challenge_method": pkce.method,
        })
        sep = "&" if "?" in meta.authorization_endpoint else "?"
        return f"{meta.authorization_endpoint}{sep}{query}", state

    def exchange_code(self, meta: ServerMetadata, client_id: str,
                      redirect_uri: str, code: str, pkce: PkcePair) -> Dict[str, Any]:
        """Trade the authorization code for a token bundle. Enforces the scope."""
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": pkce.verifier,
        })
        resp = self._http("POST", meta.token_endpoint,
                          headers={"content-type": "application/x-www-form-urlencoded"},
                          body=body)
        if resp.status != 200:
            raise McpTransportError(
                DEGRADATION_PROTOCOL,
                f"the token exchange failed (HTTP {resp.status})")
        bundle = resp.json()
        assert_read_only(bundle.get("scope"))
        if not bundle.get("access_token"):
            raise McpTransportError(
                DEGRADATION_PROTOCOL, "the token response carried no access_token")
        return bundle

    def refresh(self, meta: ServerMetadata, client_id: str,
                refresh_token: str) -> Dict[str, Any]:
        """Exchange a refresh token for a new bundle.

        **A refresh failure is a NAMED degradation, never a silent retry.** The
        person is told their authorization needs renewing; the run does not spin
        against the token endpoint hoping a repeat succeeds.
        """
        if not refresh_token:
            raise McpTransportError(
                DEGRADATION_REFRESH_FAILED,
                "the stored authorization carries no refresh token; it must be "
                "renewed by authorizing again")
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        })
        resp = self._http("POST", meta.token_endpoint,
                          headers={"content-type": "application/x-www-form-urlencoded"},
                          body=body)
        if resp.status != 200:
            raise McpTransportError(
                DEGRADATION_REFRESH_FAILED,
                f"the authorization could not be refreshed (HTTP {resp.status}); "
                "it must be renewed by authorizing again")
        bundle = resp.json()
        assert_read_only(bundle.get("scope"))
        if not bundle.get("access_token"):
            raise McpTransportError(
                DEGRADATION_REFRESH_FAILED,
                "the refresh response carried no access_token")
        return bundle

    # -- MCP calls ---------------------------------------------------------- #

    def call(self, access_token: str, method: str,
             params: Optional[Dict[str, Any]] = None,
             request_id: int = 1) -> Any:
        """One JSON-RPC call against the MCP endpoint.

        `method` is passed through — this transport does NOT know the names of a
        server's tools, and must not. Which tool to call is the adapter's
        business, discovered from `tools/list` at run time rather than hard-coded
        (the plan's carried residual: the tool surface is a finding, not a
        formality).
        """
        payload = json.dumps({"jsonrpc": "2.0", "id": request_id,
                              "method": method, "params": params or {}})
        resp = self._http(
            "POST", self.server_url,
            headers={"content-type": "application/json",
                     "accept": "application/json, text/event-stream",
                     "authorization": f"Bearer {access_token}"},
            body=payload)
        if resp.status == 401:
            raise McpTransportError(
                DEGRADATION_AUTH_EXPIRED,
                "the server rejected the authorization; it has expired or been "
                "revoked and must be renewed")
        if resp.status == 429:
            raise McpTransportError(
                DEGRADATION_RATE_LIMITED,
                "the server is rate-limiting this client; the read was stopped "
                "rather than retried into a longer block")
        if resp.status != 200:
            raise McpTransportError(
                DEGRADATION_PROTOCOL, f"the call failed (HTTP {resp.status})")
        doc = resp.json()
        if isinstance(doc, dict) and doc.get("error"):
            err = doc["error"]
            raise McpTransportError(
                DEGRADATION_PROTOCOL,
                f"the server returned an error: {err.get('message') or err}")
        return doc.get("result") if isinstance(doc, dict) else doc

    def list_tools(self, access_token: str) -> Sequence[Dict[str, Any]]:
        """The server's tool surface. Never assumed — always asked."""
        result = self.call(access_token, "tools/list")
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return list(tools or [])

    def call_tool(self, access_token: str, name: str,
                  arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Invoke one tool and return its PAYLOAD, unwrapped from the MCP envelope.

        Use this rather than `call(..., "tools/call", ...)` for a tool
        invocation; see :func:`unwrap_tool_result` for what the envelope is and
        why leaving it wrapped was a real defect rather than a nicety.
        """
        return unwrap_tool_result(
            self.call(access_token, "tools/call",
                      {"name": name, "arguments": arguments or {}}))


#: Substrings that identify a failure MODE inside a tool-level error payload.
#:
#: **Why this is needed at all, and why it is not over-engineering.** The HTTP
#: status arms above catch a rate limit or an expired grant only when the server
#: signals them as a STATUS. The closing verification found a server that signals
#: them in the tool-error PAYLOAD instead — `{"error":"rate_limited",…}` arriving
#: as a perfectly ordinary HTTP 200 with `isError: true`. Without this mapping,
#: every one of those degraded under "answered in a form this client could not
#: read", which is not what happened and not something a person can act on. A14's
#: whole promise is that a remote failure reaches someone as its own name.
#:
#: Substring matching on a lowercased payload, deliberately: the error vocabulary
#: is per-server and this must not become a table of one vendor's exact strings.
#: An unrecognised error keeps the protocol mode, which is the honest default.
_ERROR_MODE_MARKERS = (
    ("rate_limit", DEGRADATION_RATE_LIMITED),
    ("rate limit", DEGRADATION_RATE_LIMITED),
    ("too many requests", DEGRADATION_RATE_LIMITED),
    ("unauthorized", DEGRADATION_AUTH_EXPIRED),
    ("unauthenticated", DEGRADATION_AUTH_EXPIRED),
    ("invalid_token", DEGRADATION_AUTH_EXPIRED),
    ("token expired", DEGRADATION_AUTH_EXPIRED),
    ("forbidden", DEGRADATION_SCOPE_REVOKED),
    ("insufficient scope", DEGRADATION_SCOPE_REVOKED),
)


def _error_mode(detail: str) -> str:
    """Which named mode a tool-level error payload describes."""
    low = (detail or "").lower()
    for marker, mode in _ERROR_MODE_MARKERS:
        if marker in low:
            return mode
    return DEGRADATION_PROTOCOL


def unwrap_tool_result(result: Any) -> Any:
    """The payload inside an MCP `tools/call` result envelope.

    **Found by the S8 closing verification, against the live server.** An MCP tool
    result is not the payload: it is `{"content": [{"type": "text", "text": ...}]}`,
    and this server puts the whole answer in that text block as a JSON *string*.
    Every unit fixture in this topic modelled a tool result as the payload dict
    directly, so the suites were green while a real call returned something no
    extractor here could read — `_default_extract_issue_ids` came back with
    `['text', '{"issues":[...]}']`, and `get_workspace` yielded no identity at all.
    That is precisely the class of defect the growth order's real-to-real rung
    exists to catch, and it is recorded here rather than quietly patched.

    **It lives in the transport because the envelope is MCP's, not Linear's.** Any
    later source class speaking MCP — Confluence, Jira — meets the same envelope,
    and a copy of this in each adapter is the duplication the Guiding Policy's
    source-agnostic machinery exists to prevent.

    **A TOOL-LEVEL ERROR IS NOT DATA, and this is the second half of the same
    finding.** MCP reports a failed tool call as an ordinary result carrying
    `isError: true`, with the message in the same content block a payload would
    occupy — so a reader that only unwraps hands the caller the error TEXT and the
    caller treats it as an answer. The closing verification watched exactly that
    happen: a listing tool rejected an argument, the message came back as the sole
    "issue identity", and a filtered-issues bound was frozen to
    `('Input validation error: …',)`. A run would then have cited an error string
    as a source. It raises instead, under the transport's own named mode, so the
    failure degrades one item with words a person can act on.

    (No tool is NAMED in this module, on purpose: a shipped gate asserts the
    transport hard-codes no tool name, because the surface is discovered rather
    than assumed.)

    Tolerant in both directions otherwise, and it never invents: a result that is
    already a payload passes through unchanged, a text block that does not parse as
    JSON is returned as its own text (some tools legitimately answer in prose), and
    the blocks are concatenated only when there is more than one.
    """
    if not isinstance(result, dict):
        return result
    if result.get("isError") is True:
        blocks = result.get("content")
        detail = ""
        if isinstance(blocks, list):
            detail = " ".join(b.get("text", "") for b in blocks
                              if isinstance(b, dict)).strip()
        raise McpTransportError(
            _error_mode(detail),
            f"the tool call was refused by the server: {detail or 'no detail given'}")
    blocks = result.get("content")
    if not isinstance(blocks, list) or not blocks:
        return result
    texts = [b.get("text") for b in blocks
             if isinstance(b, dict) and isinstance(b.get("text"), str)]
    if not texts:
        return result
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except (ValueError, TypeError):
            return texts[0]
    decoded = []
    for text in texts:
        try:
            decoded.append(json.loads(text))
        except (ValueError, TypeError):
            decoded.append(text)
    return decoded


def assert_read_only(granted_scope: Optional[str]) -> None:
    """REFUSE a grant that carries more than `read` (design-A24).

    The refusal is the point, and it is deliberately not "we simply never ask for
    write". A server may grant more than was requested; a client that noticed
    only what it asked for would then hold a write-capable credential for a
    read-only job and never know. So the GRANT is checked, not the request.

    A missing scope field is not treated as a violation: some servers omit it
    when the grant matches the request exactly. Refusing there would break a
    conforming server for no security gain, since the request itself asked only
    for `read`.
    """
    if not granted_scope:
        return
    granted = {s.strip().lower() for s in str(granted_scope).replace(",", " ").split()
               if s.strip()}
    excess = sorted(granted - BENIGN_GRANTED_SCOPES)
    if excess:
        raise ScopeRefused(
            f"the authorization server granted scope(s) {excess}, which exceed "
            f"the {READ_SCOPE!r} this client requested; refusing the credential "
            "rather than holding a write-capable token for a read-only job")

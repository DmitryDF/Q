"""S8 Session 2 — the credential path, the MCP transport, and the connect gate.

research-source-adapters S8 / plan actions A5, A6, A7, A8, A9 (design-A5, A24, U4).

Named for what it tests rather than for the slice, matching this topic's other
suites.

**Scope, and the honest edge of it.** Session 2 builds the machinery a
credentialed remote needs: somewhere for a token to live that is not the approval
record, a transport that registers its OWN OAuth client, and the connect-now step
that turns a first-time selection into a consented declaration. Everything here
runs against a STUB authorization server, which is rungs (i) and (ii) of the
growth order the plan mandates for A6. Rung (iii)'s registration half and rung
(iv) — the real browser round-trip against the operator's own Linear workspace —
cannot run unattended and are NOT faked here. A suite that stubbed them would
report a green transport that has never met the live server, which is exactly the
green-suite-over-a-fake this topic's record warns about.

**Every gate observes an ABSENCE.** Per the plan's Guiding Policy: each asserts
both that the property holds AND that removing the work makes it fail.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
SKILLS_DIR = CONFIG_DIR / "skills"
sys.path.insert(0, str(SKILLS_DIR))

from research import connect_gate as cg              # noqa: E402
from research import credential_path as cp           # noqa: E402
from research import mcp_transport as mt             # noqa: E402
from research import scope_record as srec            # noqa: E402
from research import source_port as sp               # noqa: E402

REDIRECT = "http://127.0.0.1:8765/callback"
NOW = datetime(2026, 8, 28, 19, 30, tzinfo=timezone.utc)


class FakeRun:
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


class StubServer:
    """A stand-in authorization server + MCP endpoint that records what it got."""

    def __init__(self, *, granted_scope="read", refresh_status=200,
                 call_status=200, tools=("list_issues",), result=None):
        self.calls = []
        self.granted_scope = granted_scope
        self.refresh_status = refresh_status
        self.call_status = call_status
        self.tools = tools
        self.result = result if result is not None else {"issues": []}

    def __call__(self, method, url, *, headers=None, body=None, timeout=30.0):
        self.calls.append({"method": method, "url": url, "body": body})
        if url.endswith("/.well-known/oauth-authorization-server"):
            return mt.HttpResponse(200, json.dumps({
                "issuer": "https://stub.example",
                "authorization_endpoint": "https://stub.example/authorize",
                "token_endpoint": "https://stub.example/token",
                "registration_endpoint": "https://stub.example/register",
                "scopes_supported": ["read", "write", "openid", "email"],
                "code_challenge_methods_supported": ["S256"]}))
        if url.endswith("/register"):
            return mt.HttpResponse(201, json.dumps({"client_id": "stub-client-42"}))
        if url.endswith("/token"):
            form = urllib.parse.parse_qs(body or "")
            if form.get("grant_type", [""])[0] == "refresh_token":
                if self.refresh_status != 200:
                    return mt.HttpResponse(self.refresh_status, '{"error":"invalid_grant"}')
                return mt.HttpResponse(200, json.dumps({
                    "access_token": "refreshed", "refresh_token": "r2",
                    "scope": self.granted_scope}))
            return mt.HttpResponse(200, json.dumps({
                "access_token": "first", "refresh_token": "r1",
                "scope": self.granted_scope}))
        if self.call_status != 200:
            return mt.HttpResponse(self.call_status, '{"error":{"message":"no"}}')
        doc = json.loads(body)
        if doc["method"] == "tools/list":
            return mt.HttpResponse(200, json.dumps({
                "jsonrpc": "2.0", "id": 1,
                "result": {"tools": [{"name": n} for n in self.tools]}}))
        return mt.HttpResponse(200, json.dumps({
            "jsonrpc": "2.0", "id": 1, "result": self.result}))


def _meta(stub):
    return mt.McpTransport("https://stub.example/mcp", http=stub).discover()


# =========================================================================== #
# A5 — the credential path
# =========================================================================== #

def test_a5_a_fetch_by_unknown_identifier_raises_rather_than_returning_empty():
    """The plan's gate, and the direction that matters.

    An empty string would flow onward as an empty bearer token and surface as a
    remote's confusing 401 instead of the local fact that nothing was connected.
    """
    store = cp.InMemoryCredentialStore()
    with pytest.raises(cp.CredentialError) as e:
        store.fetch("never-connected")
    assert "has not been made" in str(e.value)


def test_a5_the_record_serialises_with_the_identifier_and_no_token():
    """The other half of the plan's A5 gate — C5's shape at this altitude."""
    store = cp.InMemoryCredentialStore()
    store.store("linear-acme", "tok_SENTINEL_9f3a")
    blob = srec.linear_scope(["ENG"], connection_id="linear-acme").to_json()
    assert "linear-acme" in blob
    assert "tok_SENTINEL_9f3a" not in blob
    for word in ("token", "bearer", "secret", "password", "authorization"):
        assert word not in blob.lower()


def test_a5_store_and_forget_round_trip():
    store = cp.InMemoryCredentialStore()
    store.store("c", "one")
    assert store.fetch("c") == "one"
    store.store("c", "two")                      # a re-authorization REPLACES
    assert store.fetch("c") == "two"
    store.forget("c")
    with pytest.raises(cp.CredentialError):
        store.fetch("c")
    store.forget("c")                            # idempotent


def test_a5_refuses_an_empty_secret_and_a_secret_shaped_identifier():
    store = cp.InMemoryCredentialStore()
    with pytest.raises(cp.CredentialError):
        store.store("c", "")
    with pytest.raises(cp.CredentialError):
        store.store("Bearer eyJ/abc+def=", "t")


def test_a5_the_connection_id_grammar_agrees_with_scope_records():
    """Two modules, one grammar, restated rather than imported to keep the
    dependency direction clean — so a change to one that is not made to the other
    must fail here rather than drift."""
    assert cp.CONNECTION_ID_RE.pattern == srec.CONNECTION_ID_RE.pattern


def test_a5_the_module_carries_no_source_specific_vocabulary():
    """The Guiding Policy's own test, applied to the code rather than the prose.

    "Once the contract holds, an adapter is a small thing." If a `linear`, an
    `issue` or an `oauth` argument appears in this module's CODE, the credential
    path was built inside the adapter instead of beside it, and Confluence and
    Jira each pay the cost again.
    """
    import re
    src = (SKILLS_DIR / "research" / "credential_path.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
    for word in ("linear", "issue", "workspace", "mcp", "oauth"):
        assert word not in code.lower(), f"{word!r} leaked into the credential path"


def test_a5_the_production_adapter_shells_to_the_keychain_and_replaces_in_place():
    calls = []

    def runner(argv):
        calls.append(argv)
        if argv[1] == "find-generic-password":
            return FakeRun(0, "keychain-token\n")
        return FakeRun(0)

    kc = cp.KeychainCredentialStore(runner=runner)
    kc.store("c", "tok")
    assert calls[0][:2] == ["security", "add-generic-password"]
    assert "-U" in calls[0], "a re-authorization must update, not duplicate"
    assert kc.fetch("c") == "keychain-token"


def test_a5_a_keychain_miss_or_empty_answer_raises_never_returns_empty():
    miss = cp.KeychainCredentialStore(
        runner=lambda a: FakeRun(44, "", "The specified item could not be found"))
    with pytest.raises(cp.CredentialError):
        miss.fetch("c")
    empty = cp.KeychainCredentialStore(runner=lambda a: FakeRun(0, "\n"))
    with pytest.raises(cp.CredentialError):
        empty.fetch("c")


def test_a5_forget_tolerates_absence_but_surfaces_a_real_failure():
    cp.KeychainCredentialStore(
        runner=lambda a: FakeRun(44, "", "could not be found")).forget("c")
    with pytest.raises(cp.CredentialError):
        cp.KeychainCredentialStore(
            runner=lambda a: FakeRun(1, "", "keychain is locked")).forget("c")


def test_a5_the_production_default_is_the_keychain_not_the_double():
    """An in-memory production default would make every run re-authorize, which
    trains a person to click through consent screens — the opposite of what the
    connect gate is for."""
    assert isinstance(cp.default_store(), cp.KeychainCredentialStore)
    assert isinstance(cp.default_store(in_memory=True), cp.InMemoryCredentialStore)


# =========================================================================== #
# A6 rung (i) — test-to-test
# =========================================================================== #

def test_a6_pkce_challenge_is_s256_of_the_verifier():
    pkce = mt.make_pkce(verifier="a" * 64)
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(("a" * 64).encode()).digest()).decode().rstrip("=")
    assert pkce.challenge == expected
    assert pkce.method == "S256"
    assert "=" not in pkce.challenge
    assert mt.make_pkce().verifier != mt.make_pkce().verifier


def test_a6_the_registration_payload_registers_our_own_client_asking_only_for_read():
    stub = StubServer()
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    meta = tr.discover()
    assert tr.register_client(meta, REDIRECT) == "stub-client-42"
    reg = json.loads([c for c in stub.calls if c["url"].endswith("/register")][0]["body"])
    assert reg["scope"] == mt.READ_SCOPE == "read"
    assert reg["redirect_uris"] == [REDIRECT]
    assert "refresh_token" in reg["grant_types"]
    assert reg["token_endpoint_auth_method"] == "none"


def test_a6_a_server_without_registration_is_refused_not_borrowed_from():
    """The refusal that keeps the transport independent: no registration endpoint
    means no client of our own, and borrowing another client's credential is the
    one thing this action exists to prevent."""
    meta = mt.ServerMetadata(issuer="i", authorization_endpoint="a",
                             token_endpoint="t", registration_endpoint=None)
    tr = mt.McpTransport("https://stub.example/mcp", http=StubServer())
    with pytest.raises(mt.McpTransportError) as e:
        tr.register_client(meta, REDIRECT)
    assert "will not borrow" in str(e.value)


def test_a6_the_authorization_url_requests_read_and_carries_the_challenge():
    stub = StubServer()
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    meta = tr.discover()
    pkce = mt.make_pkce()
    url, state = tr.authorization_url(meta, "cid", REDIRECT, pkce)
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert q["scope"] == ["read"]
    assert "write" not in url
    assert q["code_challenge"] == [pkce.challenge]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"] == [state]
    assert q["response_type"] == ["code"]


def test_a6_the_code_exchange_sends_the_verifier():
    stub = StubServer()
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    meta = tr.discover()
    pkce = mt.make_pkce()
    assert tr.exchange_code(meta, "cid", REDIRECT, "code", pkce)["access_token"] == "first"
    form = urllib.parse.parse_qs(
        [c for c in stub.calls if c["url"].endswith("/token")][-1]["body"])
    assert form["code_verifier"] == [pkce.verifier]


@pytest.mark.parametrize("granted", ["read write", "write", "read,write", "read admin"])
def test_a6_a_grant_exceeding_read_is_refused_and_the_refusal_is_seen(granted):
    """The plan's absence-observing conjunct, stated as a REFUSAL rather than as
    the absence of a request.

    A server may grant more than was asked for. A client that noticed only what it
    requested would hold a write-capable credential for a read-only job and never
    know — so the GRANT is checked, not the request.
    """
    stub = StubServer(granted_scope=granted)
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    with pytest.raises(mt.ScopeRefused) as e:
        tr.exchange_code(tr.discover(), "cid", REDIRECT, "code", mt.make_pkce())
    assert "exceed" in str(e.value)


@pytest.mark.parametrize("granted", ["read", "read openid email", None, ""])
def test_a6_a_benign_grant_is_accepted_so_the_refusal_is_not_vacuous(granted):
    stub = StubServer(granted_scope=granted)
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    tr.exchange_code(tr.discover(), "cid", REDIRECT, "code", mt.make_pkce())


# =========================================================================== #
# A6 rung (ii) — real-to-test
# =========================================================================== #

def test_a6_refresh_on_expiry_yields_a_new_token_and_rechecks_the_scope():
    stub = StubServer()
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    assert tr.refresh(tr.discover(), "cid", "r1")["access_token"] == "refreshed"

    wide = StubServer(granted_scope="read write")
    tr2 = mt.McpTransport("https://stub.example/mcp", http=wide)
    with pytest.raises(mt.ScopeRefused):
        tr2.refresh(tr2.discover(), "cid", "r1")


def test_a6_a_failed_refresh_names_its_mode_and_does_not_silently_retry():
    stub = StubServer(refresh_status=400)
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    meta = tr.discover()
    before = len([c for c in stub.calls if c["url"].endswith("/token")])
    with pytest.raises(mt.McpTransportError) as e:
        tr.refresh(meta, "cid", "r1")
    assert e.value.degradation == mt.DEGRADATION_REFRESH_FAILED
    after = len([c for c in stub.calls if c["url"].endswith("/token")])
    assert after - before == 1, "a failed refresh must not spin against the endpoint"


def test_a6_an_absent_refresh_token_names_its_mode_rather_than_calling_out():
    stub = StubServer()
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    with pytest.raises(mt.McpTransportError) as e:
        tr.refresh(tr.discover(), "cid", "")
    assert e.value.degradation == mt.DEGRADATION_REFRESH_FAILED


@pytest.mark.parametrize("status,mode", [
    (401, mt.DEGRADATION_AUTH_EXPIRED),
    (429, mt.DEGRADATION_RATE_LIMITED),
    (500, mt.DEGRADATION_PROTOCOL),
])
def test_a6_each_remote_failure_mode_degrades_under_its_own_name(status, mode):
    """A credentialed remote fails in ways no on-disk class does, and none of them
    may collapse into one generic recorded reason a person cannot act on."""
    stub = StubServer(call_status=status)
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    with pytest.raises(mt.McpTransportError) as e:
        tr.call("tok", "tools/list")
    assert e.value.degradation == mode


def test_a6_the_tool_surface_is_asked_never_assumed():
    stub = StubServer(tools=("alpha_tool", "beta_tool"))
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    assert [t["name"] for t in tr.list_tools("tok")] == ["alpha_tool", "beta_tool"]
    src = (SKILLS_DIR / "research" / "mcp_transport.py").read_text(encoding="utf-8")
    for guess in ("search_issues", "list_issues", "linear_search"):
        assert guess not in src, "a tool name must not be hard-coded in the transport"


def test_a6_an_sse_framed_reply_is_parsed_and_the_last_message_wins():
    """FOUND BY RUNG (iv), not by any stub — recorded because that is the point.

    The live server answers a JSON-RPC POST with `content-type:
    text/event-stream`, framing the reply as `event: message` / `data: {…}`. The
    stub in rungs (i)/(ii) replied with plain JSON, so nothing below the real
    round-trip could have surfaced it: the first live `tools/list` failed with
    "the server's reply was not JSON". This is exactly what the staged growth
    order exists to catch, and it is the one thing it caught.
    """
    body = ('event: message\n'
            'data: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"a"}]}}\n\n')
    resp = mt.HttpResponse(200, body, {"content-type": "text/event-stream"})
    assert resp.json()["result"]["tools"] == [{"name": "a"}]

    # The LAST complete message wins — a stream may carry several.
    two = ('event: message\ndata: {"result":{"n":1}}\n\n'
           'event: message\ndata: {"result":{"n":2}}\n\n')
    assert mt.HttpResponse(200, two, {"content-type": "text/event-stream"}
                           ).json()["result"]["n"] == 2

    # Keep-alive comments and [DONE] sentinels are skipped, not parsed.
    noisy = (': keep-alive\nevent: message\ndata: {"result":{"n":7}}\n\n'
             'data: [DONE]\n\n')
    assert mt.HttpResponse(200, noisy, {"content-type": "text/event-stream"}
                           ).json()["result"]["n"] == 7

    # A stream carrying no JSON message degrades under its own name rather than
    # returning something empty that would read downstream as "no tools".
    with pytest.raises(mt.McpTransportError) as e:
        mt.HttpResponse(200, ': just a comment\n\n',
                        {"content-type": "text/event-stream"}).json()
    assert e.value.degradation == mt.DEGRADATION_PROTOCOL

    # Plain JSON still works — the SSE arm is additive, not a replacement.
    assert mt.HttpResponse(200, '{"result":{"n":3}}',
                           {"content-type": "application/json"}
                           ).json()["result"]["n"] == 3


def test_a6_the_read_tool_is_chosen_by_the_servers_own_annotation():
    """Also a rung-(iv) consequence: the live surface publishes
    `annotations.readOnlyHint`, which is stronger evidence than a name.

    A destructive-hinted tool is excluded even when its NAME reads harmlessly —
    which name-shape matching alone would have admitted.
    """
    surface = [
        {"name": "list_issues", "annotations": {"readOnlyHint": True}},
        {"name": "get_issue", "annotations": {"readOnlyHint": True}},
    ]
    assert cg._pick_read_tool(surface) == "list_issues"

    # A harmless-looking name the server marks destructive must NOT be chosen.
    trap = [{"name": "list_issues_and_tidy",
             "annotations": {"readOnlyHint": False, "destructiveHint": True}}]
    with pytest.raises(cg.ConnectGateError):
        cg._pick_read_tool(trap)

    # An unannotated surface still falls back to name shape, so a server that
    # publishes no hints is not thereby unusable.
    assert cg._pick_read_tool([{"name": "search_issues"}]) == "search_issues"


def test_a6_the_transport_never_reads_the_harness_credential_store():
    """The one thing the plan says must NOT be built, asserted structurally.

    Reusing Claude Code's own MCP token would break silently whenever the harness
    changed, and would hand a credential granted to one client to another.
    """
    import re
    src = (SKILLS_DIR / "research" / "mcp_transport.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    code = "\n".join(l for l in code.splitlines() if not l.strip().startswith("#"))
    for forbidden in (".claude/", "claude.json", "mcp.json", "CLAUDE_CONFIG_DIR",
                      "claude_desktop_config"):
        assert forbidden not in code
    assert "linear" not in code.lower(), "the transport must stay source-agnostic"


# =========================================================================== #
# A7 — the connect gate
# =========================================================================== #

def test_a7_the_connect_step_fires_exactly_when_there_is_no_connection():
    store = cp.InMemoryCredentialStore()
    assert cg.connection_state(None, store) == cg.CONNECTION_ABSENT
    assert cg.connection_state("linear-acme", store) == cg.CONNECTION_ABSENT
    store.store("linear-acme", "tok")
    assert cg.connection_state("linear-acme", store) == cg.CONNECTION_PRESENT


def test_a7_declining_leaves_no_partial_declaration():
    """The drop path the plan calls out: a person who connects and then decides
    against the source must be left with a declaration that does not mention it —
    not one that mentions it without a connection."""
    sources = (srec.DeclaredSource(kind="code", selectors=("/tmp",)),
               srec.DeclaredSource(kind="linear", selectors=("ENG",),
                                   connection_id="linear-acme"))
    kept = cg.drop_source(sources, "linear")
    assert [s.kind for s in kept] == ["code"]
    assert "linear" not in srec.ScopeRecord(sources=kept).to_json()
    # non-vacuity: proceeding keeps it
    assert any(s.kind == "linear" for s in sources)


def test_a7_a_link_bound_is_resolved_once_and_frozen_with_an_as_of_time():
    stub = StubServer(tools=("list_issues",),
                      result={"issues": [{"identifier": "ENG-1"},
                                         {"identifier": "ENG-7"},
                                         {"identifier": "ENG-1"}]})
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    resolved = cg.resolve_link_membership(
        "https://linear.app/acme/view/abc", access_token="tok",
        transport=tr, now=NOW)
    assert resolved.members == ("ENG-1", "ENG-7")      # ordered + de-duplicated
    assert resolved.count == 2
    assert resolved.as_of == "2026-08-28T19:30:00+00:00"
    methods = [json.loads(c["body"])["method"] for c in stub.calls
               if c["url"].endswith("/mcp")]
    assert methods.count("tools/call") == 1, "the filter is resolved exactly once"
    assert "tools/list" in methods, "the tool surface is asked, not assumed"


def test_a7_an_unfamiliar_tool_name_is_found_by_shape():
    stub = StubServer(tools=("find_issue_records",),
                      result={"issues": [{"identifier": "X-1"}]})
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    got = cg.resolve_link_membership("https://l/v", access_token="t",
                                     transport=tr, now=NOW)
    assert got.members == ("X-1",)


def test_a7_a_surface_with_no_read_tool_refuses_rather_than_guessing():
    """Picking an arbitrary tool could invoke a mutating one — and this client
    asked for `read` precisely so that cannot happen."""
    stub = StubServer(tools=("create_issue", "delete_issue"))
    tr = mt.McpTransport("https://stub.example/mcp", http=stub)
    with pytest.raises(cg.ConnectGateError) as e:
        cg.resolve_link_membership("https://l/v", access_token="t",
                                   transport=tr, now=NOW)
    assert "refusing to guess" in str(e.value)


def test_a7_an_unresolved_link_cannot_be_constructed_so_cannot_be_approved():
    with pytest.raises(srec.ScopeRecordError):
        srec.DeclaredSource(kind="linear",
                            selectors=("https://linear.app/acme/view/x",))


def test_a7_the_bundle_names_the_resolved_count_and_the_as_of_time():
    resolved = cg.ResolvedBound(link="https://l/v", members=("ENG-1", "ENG-7"),
                                as_of="2026-08-28T19:30:00+00:00")
    line = cg.render_bundle_line("linear", resolved.link, resolved,
                                 item_ceiling=sp.MAX_ITEMS)
    assert cg.bundle_line_is_approvable(line, is_link_bound=True)
    assert "2 issue(s)" in line.text
    assert resolved.as_of in line.text
    assert line.warning is None


def test_a7_a_bundle_showing_only_the_link_is_not_approvable():
    """THE absence-observing gate for A7's third part.

    Without this, an implementation could freeze the membership, render the bundle
    exactly as before, pass every other assertion, and still ask a person to
    consent to a link rather than to the set.
    """
    bare = cg.render_bundle_line("linear", "https://l/v", None)
    assert not cg.bundle_line_is_approvable(bare, is_link_bound=True)
    assert bare.resolved_count is None and bare.as_of is None
    # non-vacuity: a project bound needs no resolved count
    assert cg.bundle_line_is_approvable(
        cg.render_bundle_line("linear", "ENG"), is_link_bound=False)


def test_a7_a_zero_resolving_filter_says_so_at_approval_and_stays_approvable():
    """Zero is a legitimate bound, not an error — but nobody should approve one
    believing it is populated."""
    zero = cg.ResolvedBound(link="https://l/v", members=(),
                            as_of="2026-08-28T19:30:00+00:00")
    line = cg.render_bundle_line("linear", zero.link, zero, item_ceiling=sp.MAX_ITEMS)
    assert line.resolved_count == 0
    assert line.warning and "no issues" in line.warning
    assert cg.bundle_line_is_approvable(line, is_link_bound=True)


def test_a7_an_over_ceiling_filter_says_so_at_approval_not_at_read_time():
    big = cg.ResolvedBound(
        link="https://l/v",
        members=tuple("ENG-%d" % i for i in range(sp.MAX_ITEMS + 5)),
        as_of="2026-08-28T19:30:00+00:00")
    line = cg.render_bundle_line("linear", big.link, big, item_ceiling=sp.MAX_ITEMS)
    assert line.warning and "exceeds" in line.warning
    assert str(sp.MAX_ITEMS) in line.warning


def test_a7_a_failed_resolve_names_its_mode_rather_than_returning_a_short_list():
    """A silently-short resolve would freeze a bound smaller than the person's
    filter, and nothing downstream could tell."""
    tr = mt.McpTransport(
        "https://stub.example/mcp",
        http=lambda m, u, **k: mt.HttpResponse(401, "{}"))
    with pytest.raises(mt.McpTransportError) as e:
        cg.resolve_link_membership("https://l/v", access_token="t",
                                   transport=tr, now=NOW)
    assert e.value.degradation == mt.DEGRADATION_AUTH_EXPIRED


# =========================================================================== #
# A8 / A9 — the localization slots and the inert row
# =========================================================================== #

def _rules_file():
    return CONFIG_DIR / "rules" / "research-scope-framing.md"


def test_a8_the_connect_gate_slots_exist_in_all_three_languages():
    rows = {}
    for line in _rules_file().read_text(encoding="utf-8").splitlines():
        if line.startswith("| ") and line.count("|") >= 5:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            rows[cells[0]] = cells[1:4]
    for slot in (cg.SLOT_CONNECT_PROMPT, cg.SLOT_CONNECT_OUTCOME,
                 cg.SLOT_RESOLVED_NOTICE):
        assert slot in rows, f"{slot} has no localization row"
        assert all(c for c in rows[slot]), f"{slot} has an empty language column"


def test_a8_the_linear_bound_prompt_offers_all_three_bounds_per_language():
    """The shipped slot offered two of the three the locked field promises. That
    drift predates S8; widening it here is what stops S8 cementing it."""
    row = [l for l in _rules_file().read_text(encoding="utf-8").splitlines()
           if l.startswith("| bound_prompt_linear |")]
    assert row, "bound_prompt_linear row not found"
    cells = [c.strip() for c in row[0].strip().strip("|").split("|")]
    en, de, ru = cells[1], cells[2], cells[3]
    for text, words in ((en, ("project", "link", "whatever")),
                        (de, ("Projekte", "Link", "alles")),
                        (ru, ("проект", "ссылк", "всё"))):
        for w in words:
            assert w.lower() in text.lower(), f"{w!r} missing from {text!r}"


def test_a9_no_route_both_offers_linear_and_cannot_read_it():
    """A9's gate. Re-adding the internal-only route to the catalogue row's route
    set makes this FAIL, rather than leaving the inert-row marking as unchecked
    prose."""
    from research import source_picker as spk
    entry = spk.entry("linear")
    assert spk.ROUTE_INTERNAL_KB not in entry.routes

    # No `hasattr` fallback: calling the real function unconditionally is what
    # keeps this from silently degrading into `route in entry.routes`, which
    # would be trivially true and assert nothing.
    for route in spk.ROUTES:
        if "linear" in spk.classes_on_route(route):
            assert route in entry.routes, (
                f"route {route!r} lists linear but the row does not declare it, "
                "so that route would offer a source it cannot read")


def test_a9_during_the_undriven_interval_the_row_is_shown_with_its_reason():
    """The rule file's A9 note, made checkable rather than left as prose.

    An earlier draft of that note claimed the row was WITHHELD from the list
    while `linear` sat registered-and-driverless, on the theory that a recorded
    driver exemption omits a row rather than marking it unavailable. That was
    false and contradicted the file's own Step 2.6 rule; an independent checker
    caught it. This test is why the corrected claim cannot drift again: it
    asserts the row RENDERS, not-selectable, carrying its reason.

    Skipped once Session 3 lands, because at that point the premise — an
    exemption standing — is gone, and asserting it would be asserting that the
    driver was never wired.
    """
    from research import kind_reachability as kr
    from research import source_picker as spk

    if "linear" not in kr.KNOWN_UNREACHABLE:
        pytest.skip("the driver has landed; the undriven interval is over")

    rows = {r.key: r for r in spk.render_catalogue(route=spk.ROUTE_NINJA)}
    assert "linear" in rows, (
        "the row must be SHOWN, not withheld — a class that cannot be read is "
        "surfaced with its reason, never silently dropped")
    assert rows["linear"].selectable is False
    assert rows["linear"].unavailable_slot == "unavailable_linear", (
        "and it must carry its reason slot; a refusal without one is the silent "
        "failure this flow exists to remove")

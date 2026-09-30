"""S8 Session 3 — the Linear adapter, its driver arm, evidence and degradations.

research-source-adapters S8 / plan actions A10, A11, A12, A12b, A13, A14, A5b.

**Scope is READING.** Session 1 made `linear` a kind the system can express and
Session 2 built the access machinery. This session is the adapter that spends that
machinery, the driver arm that makes an approved declaration actually get read, the
captured-evidence path that survives the process, the plain-words disclosure a
reader acts on, the named degradations a remote needs, and the scan that keeps a
token out of everything the run leaves behind.

**Every gate here observes an ABSENCE**, per the plan's Guiding Policy: each
asserts both that the property holds AND that removing the work makes it fail. A
gate that would still pass with the action skipped is the defect this slice was
told to expect in itself.

**One sequencing question was raised and answered here, and the answer is pinned
below rather than left in a transcript.** Session 3 was asked to hold A11's
`KNOWN_UNREACHABLE` deletion until the closing verification had walked the chain
against a real workspace, so that Linear could never be OFFERED to a person on the
strength of an unproven read. That state turned out not to be expressible:
`kind_reachability.check()` is guarded in both directions, so a driven kind that
still carries an exemption fails as stale exactly as an undriven kind without one
fails as unreachable. The adapter's mere presence is what makes the kind derive as
driven, so the driver and the row cannot coexist. The row therefore lands with the
driver and the real read is proven immediately after — see the two A11 tests.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

from research import admission_record as arec          # noqa: E402
from research import connect_gate as cg                # noqa: E402
from research import credential_path as cp             # noqa: E402
from research import declared_read as dr               # noqa: E402
from research import kind_reachability as kr           # noqa: E402
from research import mcp_transport as mcp              # noqa: E402
from research import scope_record as srec              # noqa: E402
from research import source_port as sp                 # noqa: E402
from research.adapters.linear import (                 # noqa: E402
    _DEGRADATION_LEAD,
    LinearAdapter,
    _find_issue,
    _issue_text,
)

#: The shipped unwrap, used by the double so a regression in it fails here.
_mcp_unwrap = mcp.unwrap_tool_result

WORKSPACE = "acme"


def _issue(identity: str, *, title: str = "", body: str = "",
           updated: str = "2026-08-28T10:00:00.000Z", workspace: str = WORKSPACE):
    """One issue row shaped like the live server's, per the Session-2 findings.

    Note what is ABSENT and why it matters: there is no `identifier` key. The live
    round-trip established the real server returns none, so a fixture carrying one
    would let an adapter pass here and fail against Linear.
    """
    return {
        "id": "9f1c0d3a-0000-4000-8000-000000000001",
        "url": f"https://linear.app/{workspace}/issue/{identity}/{identity.lower()}-slug",
        "title": title or f"Title for {identity}",
        "description": body or f"Body for {identity}",
        "updatedAt": updated,
    }


class FakeTransport:
    """Stands in for `McpTransport`. Records every call so laziness is observable.

    **It answers in the REAL MCP envelope**, and that is not decoration. Until the
    closing verification ran, this double returned the payload dict directly — so
    every test here passed while a live call returned
    `{"content": [{"type": "text", "text": "<json string>"}]}`, which no extractor
    in this package could read. A double shaped unlike the thing it doubles is the
    green-suite-over-a-fake this topic's record already warns about, met from a new
    direction. It now wraps exactly as the live server does, and `call_tool`
    unwraps — so a regression in the unwrap fails HERE rather than in production.
    """

    def __init__(self, issues, *, tools=None, workspace=WORKSPACE, raises=None):
        self.issues = list(issues)
        self.calls = []
        self._tools = tools if tools is not None else [
            {"name": "list_issues", "annotations": {"readOnlyHint": True}},
        ]
        self.workspace = workspace
        self.raises = raises

    @staticmethod
    def _envelope(payload):
        """Wrap as the live server does: a JSON string in one text content block."""
        return {"content": [{"type": "text", "text": json.dumps(payload)}]}

    def list_tools(self, access_token):
        self.calls.append(("tools/list", None))
        return self._tools

    def call(self, access_token, method, params=None, request_id=1):
        self.calls.append((method, dict(params or {})))
        if self.raises is not None:
            raise self.raises
        name = (params or {}).get("name")
        if name == "get_workspace":
            # The live shape: id, name, url — and NO `urlKey`.
            return self._envelope({
                "id": "cd5a0c10-0000-4000-8000-000000000000",
                "name": f"{self.workspace} Display Name",
                "url": f"https://linear.app/{self.workspace}",
            })
        query = ((params or {}).get("arguments") or {}).get("query")
        if query:
            matched = [i for i in self.issues
                       if srec.linear_identifier_from_url(i["url"]) == query
                       or query.upper() in (i["url"] or "").upper()]
            return self._envelope({"issues": matched})
        return self._envelope({"issues": list(self.issues)})

    def call_tool(self, access_token, name, arguments=None):
        return _mcp_unwrap(self.call(access_token, "tools/call",
                                     {"name": name, "arguments": arguments or {}}))


def _record(*, selectors=(), mode=srec.SCOPE_MODE_ENUMERATED, members=None):
    return srec.ScopeRecord(sources=(
        srec.DeclaredSource(kind=srec.KIND_LINEAR, scope_mode=mode,
                            selectors=tuple(selectors),
                            connection_id="linear-s8",
                            resolved_members=members),
    ))


# =========================================================================== #
# The MCP envelope — found by the closing verification, against the live server
# =========================================================================== #

def test_a_tool_result_is_an_envelope_and_the_payload_is_inside_it():
    """The live shape, pinned. Every fixture in this topic modelled a tool result
    as the payload dict directly, which is why nine green suites coexisted with a
    real call that no extractor here could read."""
    payload = {"issues": [{"id": "INT-1"}]}
    wrapped = {"content": [{"type": "text", "text": json.dumps(payload)}]}
    assert mcp.unwrap_tool_result(wrapped) == payload


def test_unwrapping_is_what_makes_the_extractors_work_at_all():
    """The ABSENCE half, and it reproduces the live failure exactly.

    Reading the envelope AS the payload is what returned `['text', '{"issues":…}']`
    from the live probe — two junk identities, neither of which is an issue.
    """
    rows = {"issues": [_issue("ENG-1"), _issue("ENG-2")]}
    wrapped = {"content": [{"type": "text", "text": json.dumps(rows)}]}

    assert list(cg._default_extract_issue_ids(mcp.unwrap_tool_result(wrapped))) == [
        "ENG-1", "ENG-2"]
    # Unwrapped-by-nobody: the failure the live probe actually produced.
    assert list(cg._default_extract_issue_ids(wrapped)) != ["ENG-1", "ENG-2"]


def test_a_tool_level_error_raises_rather_than_being_returned_as_data():
    """The second half of the same live finding, and the more dangerous half.

    MCP reports a refused tool call as an ordinary result carrying `isError: true`
    with the message where a payload would be. The closing verification watched a
    filtered-issues bound get frozen to `('Input validation error: …',)` because of
    it — one step from citing an error string as a source.
    """
    envelope = {"content": [{"type": "text",
                             "text": "Input validation error: Invalid input"}],
                "isError": True}
    with pytest.raises(mcp.McpTransportError) as excinfo:
        mcp.unwrap_tool_result(envelope)
    assert excinfo.value.degradation == mcp.DEGRADATION_PROTOCOL
    assert "Invalid input" in str(excinfo.value)


@pytest.mark.parametrize("detail,expected", [
    ('{"error":"rate_limited","message":"slow down"}', mcp.DEGRADATION_RATE_LIMITED),
    ("Too Many Requests", mcp.DEGRADATION_RATE_LIMITED),
    ("unauthorized", mcp.DEGRADATION_AUTH_EXPIRED),
    ("insufficient scope for this resource", mcp.DEGRADATION_SCOPE_REVOKED),
    ("Input validation error: Invalid input", mcp.DEGRADATION_PROTOCOL),
])
def test_a_tool_level_error_is_classified_by_the_mode_it_describes(detail, expected):
    """The live workspace signals RATE LIMITING in the tool-error payload, not as
    HTTP 429 — so without this the person is told their read failed because Linear
    "answered in a form this client could not read", which is not what happened and
    is not something they can act on. A14's promise is that a remote failure
    reaches someone as its own name.

    Absence-observing: collapse every tool error to the protocol mode and the first
    four cases fail.
    """
    envelope = {"content": [{"type": "text", "text": detail}], "isError": True}
    with pytest.raises(mcp.McpTransportError) as excinfo:
        mcp.unwrap_tool_result(envelope)
    assert excinfo.value.degradation == expected


def test_a_rate_limited_read_says_so_in_the_body_rather_than_blaming_the_format():
    """End to end: the mode reaches the report body in the right words."""
    transport = FakeTransport(
        [_issue("ENG-1")],
        raises=mcp.McpTransportError(mcp.DEGRADATION_RATE_LIMITED,
                                     'the tool call was refused by the server: '
                                     '{"error":"rate_limited"}'))
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    item = sp.SourceItem(item_id="ENG-1", kind=srec.KIND_LINEAR, target="ENG-1")
    port = sp.AdmissionPort(arec.InMemoryAdmissionRecordStore())
    result = port.admit(item, _record(selectors=("ENG",)), adapter, "run1")
    assert not result.admitted
    assert "rate-limiting" in result.degradation.reason
    assert "could not read" not in result.degradation.reason


def test_a_refused_tool_call_degrades_the_item_with_words_a_person_can_act_on():
    """It reaches the report body as a named degradation, not as a finding."""
    transport = FakeTransport(
        [_issue("ENG-1")],
        raises=mcp.McpTransportError(mcp.DEGRADATION_PROTOCOL, "refused"))
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    item = sp.SourceItem(item_id="ENG-1", kind=srec.KIND_LINEAR, target="ENG-1")
    port = sp.AdmissionPort(arec.InMemoryAdmissionRecordStore())
    result = port.admit(item, _record(selectors=("ENG",)), adapter, "run1")
    assert not result.admitted
    assert "could not read" in result.degradation.reason


def test_unwrapping_passes_through_a_result_that_is_already_a_payload():
    """Tolerant in both directions — it never invents and never mangles."""
    assert mcp.unwrap_tool_result({"issues": []}) == {"issues": []}
    assert mcp.unwrap_tool_result("plain") == "plain"
    assert mcp.unwrap_tool_result(
        {"content": [{"type": "text", "text": "not json"}]}) == "not json"


def test_the_workspace_pin_takes_the_url_slug_not_the_display_name():
    """A display name can be renamed and may carry spaces; the URL slug is the
    same token every issue URL carries, so the pin stays coherent with the issues
    it addresses."""
    transport = FakeTransport([_issue("ENG-1")])
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    item = sp.SourceItem(item_id="ENG-1", kind=srec.KIND_LINEAR, target="ENG-1")
    read = adapter.read_with_pin(item)
    assert read.source_id == WORKSPACE
    assert "Display Name" not in read.source_id


# =========================================================================== #
# The identity derivation the live findings forced (A10, shared with A7)
# =========================================================================== #

def test_identity_is_derived_from_the_url_because_it_is_not_a_field():
    assert srec.linear_identifier_from_url(
        "https://linear.app/acme/issue/ENG-123/some-slug") == "ENG-123"


def test_identity_derivation_is_anchored_and_does_not_take_a_workspace_slug():
    """The absence-observing half: a shape-only scan would return the WORKSPACE.

    `acme-2024` matches `LINEAR_ISSUE_RE` exactly and precedes the real identity,
    so an unanchored derivation would freeze a membership of workspace names and
    every later admission would be refused as outside the person's own
    declaration. Anchoring on the `issue` segment is what prevents it.
    """
    assert srec.linear_identifier_from_url(
        "https://linear.app/acme-2024/issue/ENG-7/x") == "ENG-7"


def test_identity_derivation_invents_nothing_for_a_non_issue_url():
    assert srec.linear_identifier_from_url("https://linear.app/acme/team/ENG") is None
    assert srec.linear_identifier_from_url("not-a-url") is None
    assert srec.linear_identifier_from_url("") is None


def test_the_freeze_and_the_containment_check_agree_on_one_vocabulary():
    """A7's extractor and `_check_linear` must speak the same identity language.

    This is the defect the live findings would otherwise have produced: the
    extractor read `id` first, which on the real server is a UUID, so the frozen
    membership would have been UUIDs while `check()` tests `ENG-123` forms.
    """
    rows = {"issues": [_issue("ENG-1"), _issue("ENG-2")]}
    members = tuple(cg._default_extract_issue_ids(rows))
    assert members == ("ENG-1", "ENG-2")

    record = _record(selectors=("https://linear.app/acme/view/abc",),
                     members=members)
    assert record.check(srec.KIND_LINEAR, "ENG-1").admitted
    # And the absence-observing half: a UUID-shaped freeze admits nothing.
    uuid_record = _record(selectors=("https://linear.app/acme/view/abc",),
                          members=("9f1c0d3a-0000-4000-8000-000000000001",))
    assert not uuid_record.check(srec.KIND_LINEAR, "ENG-1").admitted


# =========================================================================== #
# A10 — the adapter
# =========================================================================== #

def test_a10_the_adapter_declares_it_cannot_be_reopened_without_credentials():
    adapter = LinearAdapter(FakeTransport([]), "tok")
    assert adapter.can_reopen_without_credentials is False
    assert adapter.kind == srec.KIND_LINEAR


def test_a10_enumeration_is_lazy_and_a_consumer_of_one_item_does_not_pay_for_the_rest():
    """Absence-observing: replacing the generator with a materialised list fails.

    The port consumes this stream and stops at its own bounds partway through, so
    a materialised enumeration would do the whole workspace's work for an item
    count the port was never going to reach.
    """
    transport = FakeTransport([_issue("ENG-1"), _issue("ENG-2"), _issue("ENG-3")])
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    stream = adapter.enumerate_within(_record(mode=srec.SCOPE_MODE_UNSCOPED))

    # Nothing has been asked of the server before the first item is pulled.
    assert transport.calls == []
    first = next(stream)
    assert first.target == "ENG-1"
    calls_after_one = len(transport.calls)
    # Draining the rest costs no FURTHER listing call — the page is streamed, not
    # re-fetched — but the point is that the first item arrived without the whole
    # enumeration having been built by the constructor.
    rest = [i.target for i in stream]
    assert rest == ["ENG-2", "ENG-3"]
    assert len(transport.calls) == calls_after_one


def test_a10_a_link_bound_enumerates_the_frozen_set_and_never_re_resolves():
    """Absence-observing: a live re-resolve makes this fail by calling the server.

    A re-resolve would look like a freshness feature and is a defect — `check()`
    tests the FROZEN set, so an issue that entered the filter after approval would
    be enumerated and then refused as outside the person's own declaration.
    """
    transport = FakeTransport([_issue("ENG-9")])   # what the filter matches NOW
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    record = _record(selectors=("https://linear.app/acme/view/abc",),
                     members=("ENG-1", "ENG-2"))

    targets = [i.target for i in adapter.enumerate_within(record)]
    assert targets == ["ENG-1", "ENG-2"]      # the frozen set, not ENG-9
    assert transport.calls == []              # and NOTHING was asked of the server


def test_a10_every_enumerated_target_passes_the_containment_check():
    """The two halves must agree, or the run refuses everything it just read."""
    transport = FakeTransport([_issue("ENG-1"), _issue("ENG-2")])
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    record = _record(selectors=("ENG",))
    for item in adapter.enumerate_within(record):
        assert record.check(srec.KIND_LINEAR, item.target).admitted


def test_a10_read_with_pin_returns_the_workspace_the_version_and_an_issue_locator():
    transport = FakeTransport([_issue("ENG-1", updated="2026-08-01T09:00:00.000Z")])
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    item = sp.SourceItem(item_id="ENG-1", kind=srec.KIND_LINEAR, target="ENG-1")
    read = adapter.read_with_pin(item)

    assert read.source_id == WORKSPACE
    assert read.version == "2026-08-01T09:00:00.000Z"
    assert read.locator.kind == "linear"
    assert read.locator.parts["issue"] == "ENG-1"
    assert read.dirty is False


def test_a10_the_read_matches_on_identity_rather_than_on_position():
    """A listing tool answers a query with whatever it finds relevant.

    Taking `result[0]` would pin one issue's version onto another's citation —
    a wrong pin is worse than no pin.
    """
    rows = {"issues": [_issue("ENG-5", updated="2026-01-01T00:00:00.000Z"),
                       _issue("ENG-1", updated="2026-08-01T09:00:00.000Z")]}
    found = _find_issue(rows, "ENG-1")
    assert found is not None
    assert found["updatedAt"] == "2026-08-01T09:00:00.000Z"


def test_a10_an_issue_with_no_version_degrades_naming_the_version_obligation():
    """The version IS the point for a mutable tracker item."""
    row = _issue("ENG-1")
    del row["updatedAt"]
    adapter = LinearAdapter(FakeTransport([row]), "tok", read_tool="list_issues")
    item = sp.SourceItem(item_id="ENG-1", kind=srec.KIND_LINEAR, target="ENG-1")
    with pytest.raises(sp.AdapterError) as excinfo:
        adapter.read_with_pin(item)
    assert excinfo.value.obligation == sp.OBLIGATION_VERSION_READ


def test_a10_the_adapter_signature_holds_no_oauth_argument():
    """Cockburn's Responsibility Alignment, stated in advance by the plan.

    If this constructor grows an OAuth argument the responsibility has leaked and
    the split was wrong: the transport is A6's and the credential is A5's.
    """
    import inspect
    params = set(inspect.signature(LinearAdapter.__init__).parameters)
    for leaked in ("client_id", "redirect_uri", "pkce", "refresh_token",
                   "credential_store", "connection_id", "scope"):
        assert leaked not in params


def test_a10_comments_are_not_folded_into_an_issue_level_read():
    """The locator gives `linear` an optional `comment` part precisely so a
    comment is addressed as itself; concatenating comments into the body would
    make an issue-level pin address more than it names."""
    row = _issue("ENG-1", title="T", body="B")
    row["comments"] = [{"body": "a comment nobody cited"}]
    assert _issue_text(row) == "T\n\nB"


# =========================================================================== #
# A11 — the driver arm
# =========================================================================== #

def test_a11_the_driver_arm_routes_linear_to_the_supplied_adapter():
    """Absence-observing: removing the arm makes this raise the no-reader error.

    This is the exact omission that cost `code` four slices — a kind registered,
    offered, probed, approved, and then never read.
    """
    adapter = LinearAdapter(FakeTransport([]), "tok")
    got = dr._adapter_for(srec.KIND_LINEAR, Path("/tmp"), linear_adapter=adapter)
    assert got is adapter


def test_a11_a_linear_declaration_with_no_authorization_refuses_by_name():
    with pytest.raises(dr.MissingAuthorization) as excinfo:
        dr._adapter_for(srec.KIND_LINEAR, Path("/tmp"), linear_adapter=None)
    assert "no Linear authorization" in str(excinfo.value)


def test_a11_an_unauthorized_kind_is_refused_by_name_and_does_not_abort_the_run(
        tmp_path):
    """The refusal is recorded, and the OTHER declared kinds are still read.

    Absence-observing: let the exception escape `read_declared_sources` and this
    fails, because the document folder never gets read. That was the first shape of
    this arm — `_adapter_for` was called eagerly for every kind and a `ValueError`
    took the whole run down for one unauthorized source, which is the same
    all-or-nothing failure the per-item degradation path exists to avoid, one level
    up. A person declaring their notes AND Linear, with Linear unconnected, would
    have got nothing at all rather than their notes plus one named refusal.
    """
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "n.md").write_text("alpha\n", encoding="utf-8")

    scope = srec.ScopeRecord(sources=(
        srec.DeclaredSource(kind=srec.KIND_DOCUMENT_FOLDER,
                            selectors=(str(folder),)),
        srec.DeclaredSource(kind=srec.KIND_LINEAR, selectors=("ENG",),
                            connection_id="linear-s8"),
    ))
    result = dr.read_declared_sources(scope, tmp_path, run_id="mixed")

    assert [Path(r.item).name for r in result.readings] == ["n.md"]
    linear_refusals = [r for r in result.refusals if "linear" in r.item]
    assert len(linear_refusals) == 1
    assert "no Linear authorization" in linear_refusals[0].reason
    # And it reaches the body a person reads, rather than being dropped.
    assert "no Linear authorization" in dr.synthesize_from_admissions(result)


def test_a11_no_reader_at_all_stays_fatal_and_is_not_softened_into_a_refusal():
    """The two failures are deliberately different, and must stay so.

    "Nothing can ever read this kind" is a programming error and must be loud;
    "this run holds no credential" is an ordinary condition a person can act on.
    Collapsing them would make a genuinely unwired kind degrade quietly — the exact
    silence `declared_read` exists to remove.
    """
    assert not issubclass(dr.MissingAuthorization, ValueError)
    with pytest.raises(ValueError) as excinfo:
        dr._adapter_for("confluence", Path("/tmp"))
    assert "no reader for declared kind" in str(excinfo.value)


def test_a11_linear_derives_as_driven_and_carries_no_stale_exemption():
    """A11's own gate: the kind is DRIVEN, with no `KNOWN_UNREACHABLE` entry.

    Absence-observing in both directions, which is the shape `check()` has: an
    undriven kind with no exemption fails, AND a driven kind that still carries one
    fails as stale. So removing the driver arm makes this fail, and re-adding the
    exemption alongside the driver makes it fail too.
    """
    assert "linear" not in kr.KNOWN_UNREACHABLE
    assert kr.check() == []


def test_a11_the_deletion_had_to_land_with_the_driver_rather_than_after_v1():
    """Why the offer window was not closable, recorded where it was discovered.

    Session 3 was asked to hold the exemption until its closing verification had
    walked the chain against a real workspace. That state does not exist: the
    presence of `adapters/linear.py` is what makes the kind derive as driven, and a
    driven kind carrying an exemption fails as stale. This test pins the reason so
    the next reader does not re-propose the same unavailable sequencing.
    """
    driven = set().union(*kr.derive_driver_kinds().values())
    assert "linear" in driven, (
        "the adapter's presence is what makes the kind derive as driven; if this "
        "fails, the driver was removed and the exemption must come back with it")


# =========================================================================== #
# A12 / A12b — captured evidence, and evidence that survives the process
# =========================================================================== #

def _run_once(store, *, adapter=None, transport=None):
    record = _record(selectors=("ENG",))
    transport = transport or FakeTransport([_issue("ENG-1"), _issue("ENG-2")])
    adapter = adapter or LinearAdapter(transport, "tok", read_tool="list_issues")
    return dr.read_declared_sources(
        record, Path.cwd(), store=store, run_id="s8run", linear_adapter=adapter)


def test_a12_an_admitted_linear_item_leaves_a_captured_excerpt():
    store = arec.InMemoryAdmissionRecordStore()
    result = _run_once(store)
    assert result.readings
    entries = store.list_evidence("s8run")
    assert entries
    for entry in entries:
        assert entry.evidence_path == arec.EVIDENCE_CAPTURED
        assert entry.excerpt


def test_a12_flipping_the_capability_flag_to_true_makes_the_captured_branch_fail():
    """Absence-observing, and it guards the ONE line that makes all of G5 true.

    `admit()` selects the branch on `can_reopen_without_credentials and not
    dirty`, so the adapter's single `False` declaration is what routes every
    Linear read to the captured branch. This is what stops that line being
    silently reverted later.
    """
    transport = FakeTransport([_issue("ENG-1")])
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    adapter.can_reopen_without_credentials = True     # the reversion under test

    store = arec.InMemoryAdmissionRecordStore()
    _run_once(store, adapter=adapter)
    entries = store.list_evidence("s8run")
    assert entries
    assert all(e.evidence_path == arec.EVIDENCE_ORIGINAL for e in entries)
    assert all(not e.excerpt for e in entries)


def test_a12b_the_excerpt_is_readable_from_disk_by_a_process_that_did_not_run_it(tmp_path):
    """Absence-observing: the in-memory default makes this read FAIL.

    Without a durable store the captured-copy promise is true in every unit test
    (which holds the store in the same process) and false in production, where the
    whole point is that a LATER, credential-less checker can read the excerpt.
    """
    research_file = tmp_path / "some-topic-20260828010203_RESEARCH.md"
    research_file.write_text("# placeholder\n", encoding="utf-8")

    store = dr.durable_store_for(str(research_file))
    _run_once(store)

    written = list(tmp_path.glob("*_ADMISSION_s8run.md"))
    assert len(written) == 1, f"expected one durable record, found {written}"
    # A PLAIN FILE READ — no store object, no credential, no import of the run.
    text = written[0].read_text(encoding="utf-8")
    assert "Body for ENG-1" in text
    assert arec.EVIDENCE_CAPTURED in text


def test_a12b_the_durable_record_lands_under_the_topic_slug_family(tmp_path):
    """`bookkeeping-model.md` §4 Bucket 3: an `ADMISSION` advisory of the same
    slug family, so a slug-grep returns it and it co-retires with the topic."""
    research_file = tmp_path / "some-topic-20260828010203_RESEARCH.md"
    research_file.write_text("x", encoding="utf-8")
    store = dr.durable_store_for(str(research_file))
    assert store.slug == "some-topic"
    assert store.ts == "20260828010203"
    assert store.run_path("r1").name == "some-topic-20260828010203_ADMISSION_r1.md"


def test_a12b_an_unparseable_research_name_still_lands_somewhere_durable():
    """Never invents a slug; falls back rather than dropping the record."""
    store = dr.durable_store_for("/nowhere/that/exists/plain.md")
    assert store.directory == arec.DEFAULT_STATE_ROOT


# =========================================================================== #
# A13 — the plain-words disclosure, next to the claim
# =========================================================================== #

def test_a13_a_captured_claim_and_a_reopenable_claim_render_two_different_sentences():
    """Absence-observing: one sentence for both makes this fail.

    That is what stops the disclosure from being a constant string — a note that
    reads identically for both cases discloses nothing.
    """
    captured = dr.AdmittedReading(item="ENG-1", content="c", marker="[stated — x]",
                                  evidence_path=arec.EVIDENCE_CAPTURED)
    original = dr.AdmittedReading(item="a.md", content="c", marker="[stated — y]",
                                  evidence_path=arec.EVIDENCE_ORIGINAL)
    assert captured.evidence_sentence()
    assert original.evidence_sentence()
    assert captured.evidence_sentence() != original.evidence_sentence()


def test_a13_the_sentence_is_plain_words_and_names_no_code_or_symbol():
    """U7/U8 — a reader who knows nothing of this design must be able to act."""
    captured = dr.AdmittedReading(item="ENG-1", content="c", marker="m",
                                  evidence_path=arec.EVIDENCE_CAPTURED)
    sentence = captured.evidence_sentence()
    for jargon in ("captured", "original", "evidence_path", "admit(", "_", "["):
        assert jargon not in sentence


def test_a13_the_disclosure_is_rendered_next_to_the_claim_not_in_an_appendix():
    body = dr.synthesize_from_admissions(dr.DeclaredReadResult(readings=(
        dr.AdmittedReading(item="ENG-1", content="Claim one.",
                           marker="[stated — linear:acme@v:ENG-1]",
                           evidence_path=arec.EVIDENCE_CAPTURED),
        dr.AdmittedReading(item="a.md", content="Claim two.",
                           marker="[stated — local-file:a.md:1]",
                           evidence_path=arec.EVIDENCE_ORIGINAL),
    )))
    blocks = body.split("\n\n")
    assert len(blocks) == 2
    # Each claim carries ITS OWN sentence, in its own block.
    assert "Claim one." in blocks[0] and "stored when this ran" in blocks[0]
    assert "Claim two." in blocks[1] and "re-opening the source" in blocks[1]


def test_a13_a_reading_with_no_recorded_evidence_path_renders_no_invented_note():
    body = dr.synthesize_from_admissions(dr.DeclaredReadResult(readings=(
        dr.AdmittedReading(item="x", content="Claim.", marker="[stated — z]"),
    )))
    assert body == "Claim. [stated — z]"


# =========================================================================== #
# A14 — named degradations for the remote-only failure modes
# =========================================================================== #

@pytest.mark.parametrize("mode", [
    mcp.DEGRADATION_AUTH_EXPIRED,
    mcp.DEGRADATION_REFRESH_FAILED,
    mcp.DEGRADATION_SCOPE_REVOKED,
    mcp.DEGRADATION_RATE_LIMITED,
    mcp.DEGRADATION_UNREACHABLE,
    mcp.DEGRADATION_PROTOCOL,
])
def test_a14_each_failure_mode_reaches_the_report_body_as_its_own_words(mode):
    """Injected in turn, each produces a recorded degradation naming THAT mode,
    next to the affected material rather than in an appendix."""
    transport = FakeTransport(
        [_issue("ENG-1")],
        raises=mcp.McpTransportError(mode, "the transport's own wording"))
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    item = sp.SourceItem(item_id="ENG-1", kind=srec.KIND_LINEAR, target="ENG-1")

    port = sp.AdmissionPort(arec.InMemoryAdmissionRecordStore())
    result = port.admit(item, _record(selectors=("ENG",)), adapter, "run1")

    assert not result.admitted
    assert result.degradation.reason == _DEGRADATION_LEAD[mode] + \
        " (the transport's own wording)"


def test_a14_every_mode_the_transport_can_name_has_plain_words_here():
    """Absence-observing: a mode added to the transport later fails THIS test
    rather than silently reaching a person as the transport's internal wording."""
    transport_modes = {
        getattr(mcp, name) for name in dir(mcp)
        if name.startswith("DEGRADATION_")
    }
    assert transport_modes, "the transport defines no named modes — check the import"
    assert transport_modes <= set(_DEGRADATION_LEAD), (
        f"unmapped transport modes: {sorted(transport_modes - set(_DEGRADATION_LEAD))}")


def test_a14_one_failing_item_degrades_that_item_and_never_aborts_the_run():
    """C12: no fallback returns a shorter result silently."""
    class Flaky(FakeTransport):
        def call(self, access_token, method, params=None, request_id=1):
            args = (params or {}).get("arguments") or {}
            if args.get("query") == "ENG-2":
                raise mcp.McpTransportError(mcp.DEGRADATION_RATE_LIMITED, "slow down")
            return super().call(access_token, method, params, request_id)

    transport = Flaky([_issue("ENG-1"), _issue("ENG-2")])
    adapter = LinearAdapter(transport, "tok", read_tool="list_issues")
    result = _run_once(arec.InMemoryAdmissionRecordStore(), adapter=adapter,
                       transport=transport)

    assert [r.item for r in result.readings] == ["ENG-1"]
    assert len(result.refusals) == 1
    assert result.refusals[0].item == "ENG-2"
    assert "rate-limiting" in result.refusals[0].reason
    # And the refusal is rendered INTO the body, not dropped.
    assert "rate-limiting" in dr.synthesize_from_admissions(result)


# =========================================================================== #
# A5b — nothing the run leaves behind holds a secret
# =========================================================================== #

#: ASSEMBLED FROM PARTS, and that is required rather than stylistic.
#:
#: The committed-files arm below scans this very file, so a sentinel written as one
#: literal would be found in the suite that defines it and the clean scan would fail
#: — a self-inflicted red that says nothing about the code. Splitting it means the
#: assembled value appears nowhere on disk, which is exactly the property a real
#: token has and therefore the honest thing to test against.
SENTINEL = "lin_oauth_" + "SENTINEL_" + "0123456789abcdef"


def _committed_files_text() -> str:
    """Every source file this slice ships, concatenated.

    The committed-files arm scans the WHOLE surface the work touches rather than
    one representative file: a token pasted into a helper while debugging is at
    least as likely as one pasted into the adapter, and a scan of one file would
    report clean while the leak sat next to it. `**/*.py` under the research
    package plus this suite is that surface.
    """
    root = SKILLS_DIR / "research"
    parts = [p.read_text(encoding="utf-8")
             for p in sorted(root.rglob("*.py"))
             if "__pycache__" not in p.parts]
    parts.append(Path(__file__).read_text(encoding="utf-8"))
    return "\n".join(parts)


def _four_artifacts(*, poison=None):
    """The four artifact classes C13 enumerates, as one run would leave them."""
    record = _record(selectors=("ENG",))
    store = arec.InMemoryAdmissionRecordStore()
    result = _run_once(store)
    artifacts = {
        cp.ARTIFACT_APPROVAL_RECORD: record.to_json(),
        cp.ARTIFACT_FINDINGS_REPORT: dr.synthesize_from_admissions(result),
        cp.ARTIFACT_ADMISSION_RECORDS: "\n".join(
            str(e.__dict__) for e in store.list_evidence("s8run")),
        cp.ARTIFACT_COMMITTED_FILES: _committed_files_text(),
    }
    if poison:
        artifacts[poison] = artifacts[poison] + f"\ntoken={SENTINEL}\n"
    return artifacts


def test_a5b_a_real_run_leaves_the_secret_in_none_of_the_four_artifacts():
    cp.scan_artifacts_for_secret(SENTINEL, _four_artifacts())


@pytest.mark.parametrize("artifact", cp.C13_ARTIFACT_CLASSES)
def test_a5b_planting_the_sentinel_in_any_one_artifact_makes_the_scan_fail(artifact):
    """The absence-observing half, and the reason A5b is LAST in its table:
    three of these four artifacts do not exist until A12/A13/A14 have run."""
    with pytest.raises(cp.SecretLeak) as excinfo:
        cp.scan_artifacts_for_secret(SENTINEL, _four_artifacts(poison=artifact))
    assert excinfo.value.artifact == artifact
    assert artifact in str(excinfo.value)


def test_a5b_the_leak_report_names_the_place_and_never_prints_the_secret():
    """An exception message is itself a thing that gets logged; a detector that
    prints the leaked value has moved the leak rather than reported it."""
    with pytest.raises(cp.SecretLeak) as excinfo:
        cp.scan_artifacts_for_secret(
            SENTINEL, {cp.ARTIFACT_FINDINGS_REPORT: f"x {SENTINEL} y"})
    assert SENTINEL not in str(excinfo.value)


def test_a5b_a_scan_over_nothing_refuses_rather_than_reporting_clean():
    """A scan that reports clean when pointed at nothing produces the assurance
    without the check — which is worse than no scan at all."""
    with pytest.raises(ValueError):
        cp.scan_artifacts_for_secret(SENTINEL, {})
    with pytest.raises(ValueError):
        cp.scan_artifacts_for_secret("", {cp.ARTIFACT_FINDINGS_REPORT: "x"})
    with pytest.raises(ValueError):
        cp.scan_artifacts_for_secret("short", {cp.ARTIFACT_FINDINGS_REPORT: "x"})


def test_a5b_the_approval_record_carries_the_connection_id_and_not_a_token():
    """The record's carrier is an opaque handle by construction (S3), and the
    scan is what proves the token never joins it."""
    record = _record(selectors=("ENG",))
    serialised = record.to_json()
    assert "linear-s8" in serialised
    cp.scan_artifacts_for_secret(
        SENTINEL, {cp.ARTIFACT_APPROVAL_RECORD: serialised})

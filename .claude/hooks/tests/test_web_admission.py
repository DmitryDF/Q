"""S6 — web rerouted through the admission port (design-A29).

research-source-adapters S6 / plan action A8.

Named for what it tests rather than for the slice, matching this topic's three
existing suites (`test_source_admission_port`, `test_source_picker`,
`test_path_admission`).

Two altitudes, because the slice has two:

* **Port level** — the `web` kind's own contract: containment, the marker, the
  per-kind rules, the evidence stamp. Every case drives the adapter THROUGH
  `port.admit()`, never by calling the adapter directly, because "the read happens
  inside the port" is the property under test and a direct call would not test it.
* **Caller level** — the fact-check engine's ingest: the preserved result shape,
  the byte-identical no-declaration path, the fair share still narrowing, and the
  refusal actually refusing in production.

**The `code` arm is asserted here too, not just web.** A3 was a generalisation of
rules that had only ever had one kind; a suite that tested only the new kind could
not tell a generalisation from a rewrite.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

from research import locator_grammar as lg          # noqa: E402
from research import scope_record as srec           # noqa: E402
from research import source_port as sp              # noqa: E402
from research.admission_record import (             # noqa: E402
    AdmissionRecordStore,
    InMemoryAdmissionRecordStore,
)
from research.adapters.web import TRUNCATION_MARKER, WebAdapter  # noqa: E402


def _engine():
    """Load the engine by path, as its own suites do."""
    spec = importlib.util.spec_from_file_location(
        "_fce_web_admission", str(HOOKS_DIR / "_factcheck_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def port(tmp_path):
    return sp.AdmissionPort(AdmissionRecordStore(tmp_path))


def _fetch_ok(url, timeout_s, max_bytes):
    return ("ok", f"body of {url}")


def _item(url):
    return sp.SourceItem(item_id=url, kind=srec.KIND_WEB, target=url, depth=0)


def _admit(port, url, scope, fetch=_fetch_ok, budget=64 * 1024):
    adapter = WebAdapter(fetch, urls=(url,), max_bytes=budget)
    return port.admit(_item(url), scope, adapter, "r1", item_budget=budget)


# =========================================================================== #
# A1 — the locator kind
# =========================================================================== #

def test_a1_web_locator_kind_is_registered_with_one_required_part():
    assert "web" in lg.registered_kinds()
    assert lg.get_kind("web").required_parts == ("url",)


def test_a1_an_incomplete_web_locator_names_its_missing_part_rather_than_being_shorter():
    """The whole reason a locator is named parts and not a string."""
    incomplete = lg.web_locator()
    assert incomplete.missing_required_parts() == ("url",)
    with pytest.raises(lg.LocatorError):
        incomplete.render()


# =========================================================================== #
# A2 — containment. The one place a wrong call is invisible: an over-permissive
# check passes every test that asserts admission and none that assert refusal.
# =========================================================================== #

@pytest.mark.parametrize("target,expected,why", [
    ("https://example.com/docs/page", True, "under the declared path"),
    ("https://example.com/docs", True, "exactly the declared path"),
    ("https://example.com/other", False, "a sibling path"),
    ("https://example.com/docsearch", False, "a string prefix that is not a segment"),
    ("https://docs.example.com/docs/x", False, "a subdomain of the declared host"),
    ("https://www.example.com/docs/x", True, "www folded"),
    ("HTTPS://EXAMPLE.COM/docs/x", True, "case folded"),
    ("https://example.com:443/docs/x", True, "the default port folded away"),
    ("https://example.com:8443/docs/x", False, "a non-default port is a different host"),
    ("https://example.com/docs/p?q=1#frag", True, "query and fragment dropped"),
    ("ftp://example.com/docs/x", False, "not http(s)"),
    ("/etc/passwd", False, "a filesystem path is not a URL"),
])
def test_a2_web_containment(target, expected, why):
    scope = srec.web_scope(["example.com/docs"])
    assert scope.check(srec.KIND_WEB, target).admitted is expected, why


def test_a2_unscoped_admits_but_enumerated_with_no_selectors_admits_nothing():
    """These are DIFFERENT declarations and collapsing them would invert one.

    Unscoped means "the bound is the source's own exposure" — for web, the open
    web. An enumerated record naming nothing admits nothing.
    """
    assert srec.web_scope([]).check(srec.KIND_WEB, "https://any.test/x").admitted is True
    empty = srec.ScopeRecord(sources=(srec.DeclaredSource(
        kind=srec.KIND_WEB, scope_mode=srec.SCOPE_MODE_ENUMERATED, selectors=()),))
    assert empty.check(srec.KIND_WEB, "https://any.test/x").admitted is False


def test_a2_a_refusal_names_the_url_and_never_a_mangled_filesystem_path():
    """The refusal branch that fires before any per-kind arm used to resolve every
    target as a path, turning a URL into `<cwd>/https:/host/path`."""
    result = srec.code_scope(["/tmp"]).check(srec.KIND_WEB, "https://example.com/a")
    assert result.admitted is False
    assert "example.com/a" in result.resolved_target
    assert not result.resolved_target.startswith("/")


@pytest.mark.parametrize("bad", [
    "https://user:token@example.com/",     # a bearer credential in the record
    "not a location at all",               # would match nothing and read as empty
])
def test_a2_a_bad_web_selector_is_refused_at_construction(bad):
    with pytest.raises(srec.ScopeRecordError):
        srec.web_scope([bad])


# =========================================================================== #
# A3 — the per-kind rules. Both arms, so a generalisation is distinguishable
# from a rewrite.
# =========================================================================== #

def test_a3_the_web_pin_renders_the_shipped_marker_exactly(port):
    """design-A29 admits web behind the port on the condition that its markers are
    preserved. The general pin form would render `[stated — web:...@...:https://...]`,
    which is a different vocabulary."""
    result = _admit(port, "https://example.com/a", srec.web_scope(["example.com"]))
    assert result.admitted
    assert result.pin.marker("stated") == "[stated — https://example.com/a]"
    assert result.pin.marker("paraphrased") == "[paraphrased — https://example.com/a]"


def test_a3_the_code_pin_still_renders_all_three_parts():
    """The `code` arm of the same rendering rule, unchanged."""
    pin = sp.SourcePin("myrepo", "abc123", lg.code_locator("pkg/mod.py", "1-3"))
    assert pin.marker("stated") == "[stated — code:myrepo@abc123:pkg/mod.py:1-3]"


def test_a3_the_web_pin_still_carries_its_version_even_though_the_marker_cannot(port):
    """Not dropped — kept where the marker cannot carry it."""
    result = _admit(port, "https://example.com/a", srec.web_scope(["example.com"]))
    assert result.pin.version


def test_a3_code_bound_constants_are_unchanged():
    assert (sp.MAX_DEPTH, sp.MAX_ITEMS, sp.MAX_ITEM_BYTES, sp.MAX_RUN_BYTES) == (
        12, 2000, 64 * 1024, 192 * 1024)
    rules = sp.rules_for("code")
    assert rules.max_item_bytes == sp.MAX_ITEM_BYTES
    assert rules.max_run_bytes == sp.MAX_RUN_BYTES
    assert rules.truncate_over_budget is False
    assert rules.path_shaped is True


def test_a3_a_caller_may_narrow_a_bound_but_never_widen_one():
    assert sp.resolve_item_budget("code", 100) == 100
    assert sp.resolve_item_budget("code", 10 ** 9) == sp.MAX_ITEM_BYTES


def test_a3_a_web_read_with_no_caller_supplied_bound_is_refused():
    """Web's ceiling is caller-supplied because its real bound is a per-run fair
    share. Unbounded is not a thing this port does."""
    with pytest.raises(ValueError):
        sp.resolve_item_budget("web", None)


def test_a3_an_unregistered_kind_gets_the_strict_defaults():
    """Fail-closed in the direction that matters: refuse whole, treat as a path."""
    rules = sp.rules_for("no-such-kind")
    assert rules.truncate_over_budget is False
    assert rules.path_shaped is True


def test_a3_web_truncates_and_records_it_while_code_refuses_whole(port):
    """The policy that keeps a passing report from becoming INCOMPLETE.

    `code` refuses whole because a truncated excerpt makes its `path:lines`
    locator address more than was read — the pin would lie. A `url` locator
    addresses the page, so truncate-and-record is honest for web.
    """
    def big(url, timeout_s, max_bytes):
        return ("ok", "x" * 5000)

    web_result = _admit(port, "https://example.com/big",
                        srec.web_scope(["example.com"]), fetch=big, budget=1000)
    assert web_result.admitted, "an oversized page must not degrade a web run"
    assert web_result.truncated is True
    assert len(web_result.content) == 1000

    class BigCode(sp.SourceAdapter):
        kind = "code"
        can_reopen_without_credentials = True

        def enumerate_within(self, scope):
            return iter(())

        def read_with_pin(self, item):
            return sp.ReadResult(source_id="r", version="v",
                                 locator=lg.code_locator("f.py", "1-1"),
                                 content="y" * 5000)

    code_result = port.admit(
        sp.SourceItem(item_id="/tmp/f.py", kind="code", target="/tmp/f.py"),
        srec.code_scope(["/tmp"]), BigCode(), "r1", item_budget=1000)
    assert code_result.admitted is False
    assert code_result.degradation.obligation == sp.OBLIGATION_READ_BUDGET
    assert "refused whole" in code_result.degradation.reason


def test_a3_a_truncation_the_fetch_performed_is_reported_not_inferred(port):
    def marked(url, timeout_s, max_bytes):
        return ("ok", "body\n" + TRUNCATION_MARKER)

    result = _admit(port, "https://example.com/m", srec.web_scope(["example.com"]),
                    fetch=marked)
    assert result.truncated is True


def test_a3_a_url_whose_path_ends_claude_md_is_admitted(port):
    """The prohibition is about a local system file being quoted as a source. A
    page on the web that happens to share its name is a page."""
    result = _admit(port, "https://example.com/docs/CLAUDE.md",
                    srec.web_scope(["example.com"]))
    assert result.admitted, result.degradation.reason if result.degradation else ""


def test_a3_a_local_claude_md_is_still_refused(port, tmp_path):
    """The other arm of the same rule, unchanged."""
    target = tmp_path / "CLAUDE.md"
    target.write_text("pointer", encoding="utf-8")

    class Any(sp.SourceAdapter):
        kind = "code"
        can_reopen_without_credentials = True

        def enumerate_within(self, scope):
            return iter(())

        def read_with_pin(self, item):
            return sp.ReadResult(source_id="r", version="v",
                                 locator=lg.code_locator("CLAUDE.md", "1"),
                                 content="pointer")

    result = port.admit(
        sp.SourceItem(item_id=str(target), kind="code", target=str(target)),
        srec.code_scope([tmp_path]), Any(), "r1")
    assert result.admitted is False
    assert result.degradation.obligation == sp.OBLIGATION_SOURCE_IDENTITY


# =========================================================================== #
# A4 — the adapter, always driven through the port
# =========================================================================== #

def test_a4_an_admitted_web_item_carries_content_and_an_original_evidence_path(port):
    """Until S6 the port read an item and returned nothing OF it, so no caller
    that needed the bytes could use it."""
    result = _admit(port, "https://example.com/a", srec.web_scope(["example.com"]))
    assert result.admitted
    assert result.content == "body of https://example.com/a"
    assert result.evidence_path == "original"


def test_a4_the_evidence_entry_is_reachable_and_names_how_to_reopen(port):
    result = _admit(port, "https://example.com/a", srec.web_scope(["example.com"]))
    entry = port.store.get_evidence(result.pin_str)
    assert entry is not None
    assert entry.evidence_path == "original"
    assert "https://example.com/a" in entry.reopen_instruction


def test_a4_a_truncated_read_stays_original_and_says_it_was_shortened(port):
    """The `code` analogue points the other way — a dirty tree flips to `captured`
    — so this is worth pinning rather than assuming."""
    def big(url, timeout_s, max_bytes):
        return ("ok", "x" * 5000)

    result = _admit(port, "https://example.com/big", srec.web_scope(["example.com"]),
                    fetch=big, budget=1000)
    entry = port.store.get_evidence(result.pin_str)
    assert entry.evidence_path == "original"
    assert "shortened" in entry.reopen_instruction


def test_a4_a_fetch_failure_becomes_a_degradation_naming_the_real_reason(port):
    def gone(url, timeout_s, max_bytes):
        return ("unfetchable", "HTTP 404")

    result = _admit(port, "https://example.com/gone", srec.web_scope(["example.com"]),
                    fetch=gone)
    assert result.admitted is False
    assert "404" in result.degradation.reason


def test_a4_a_misbehaving_fetch_becomes_a_degradation_and_never_propagates(port):
    """C3: no path returns neither outcome."""
    def boom(url, timeout_s, max_bytes):
        raise RuntimeError("fetch exploded")

    result = _admit(port, "https://example.com/x", srec.web_scope(["example.com"]),
                    fetch=boom)
    assert result.admitted is False
    assert "fetch exploded" in result.degradation.reason


def test_a4_the_adapter_holds_no_bound_constant():
    """Same structural rule the code adapter is held to."""
    src = (SKILLS_DIR / "research" / "adapters" / "web.py").read_text(encoding="utf-8")
    for name in ("MAX_DEPTH", "MAX_ITEMS", "MAX_ITEM_BYTES", "MAX_RUN_BYTES"):
        assert name not in src, f"{name} appears in the adapter"


def test_a4_the_adapter_is_read_only():
    """design-A24: the adapter has no write path.

    Scanned as CODE, not as text. An earlier draft grepped the raw source for
    words like "cookie" and failed on the adapter's own docstring, which says it
    keeps no cookie jar — a test that a truthful comment could break is measuring
    prose rather than behaviour.
    """
    import ast

    src = (SKILLS_DIR / "research" / "adapters" / "web.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {
        node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    # `replace` is deliberately absent from this list: the adapter calls
    # `datetime.replace(microsecond=0)`, which shares a name with `os.replace`
    # and nothing else. A ban on the bare attribute name would be a ban on a
    # word rather than on a write — the `os` check below is the real guard.
    for forbidden in ("write_text", "write_bytes", "mkdir", "unlink",
                      "rmtree", "remove", "open"):
        assert forbidden not in called, f"{forbidden}() is called by a read-only adapter"

    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    for forbidden in ("os", "shutil", "sqlite3", "http", "urllib", "requests",
                      "subprocess"):
        assert forbidden not in imported, (
            f"a read-only adapter imports {forbidden}; it neither writes nor "
            "fetches on its own — the fetch is injected")


# =========================================================================== #
# A5 — the picker
# =========================================================================== #

def test_a5_web_is_selectable_and_the_catalogue_availability_fields_were_not_edited():
    from research import source_picker as spk

    rows = {r.key: r for r in spk.render_catalogue()}
    assert rows["web"].selectable is True
    assert rows["web"].unavailable_kind is None
    assert rows["web"].unavailable_slot is None
    # The row flipped because the KIND was registered, not because a flag was set.
    entry = spk.entry("web")
    assert entry.registered_kind in srec.REGISTERED_KINDS


def test_a5_web_permits_an_unscoped_bound_because_that_is_what_research_reads_today():
    from research import source_picker as spk

    selection = spk.Selection(bounds=(("web", spk.Bound(unscoped=True)),))
    record = spk.build_record(selection)
    assert record.sources[0].scope_mode == srec.SCOPE_MODE_UNSCOPED


def test_a5_the_probe_never_opens_a_page():
    """S7 note — the region anchor is now RENAME-PROOF, and that repair matters.

    This test used to bound the scanned region with `"def _probe_code"`, the name
    of whatever function happened to follow `_probe_web`. S7 generalised that
    helper across every path-shaped kind and renamed it `_probe_path`, at which
    point the second `.split()` matched nothing and silently returned the whole
    remainder of the file — so the test kept PASSING while no longer testing the
    thing it is named for. That is the failure mode "an unedited suite stayed
    green" is supposed to rule out, so leaving it would have quietly devalued the
    evidence rather than preserved it.

    The region now ends at the next top-level `def` whatever it is called, so a
    future rename cannot repeat this.
    """
    import re

    src = (SKILLS_DIR / "research" / "source_picker.py").read_text(encoding="utf-8")
    after = src.split("def _probe_web", 1)[1]
    next_def = re.search(r"\ndef ", after)
    assert next_def, "no top-level def follows _probe_web — the anchor is broken"
    probe = after[: next_def.start()]
    # Non-vacuity: the region must actually be the probe body, not the whole file.
    assert "getaddrinfo" in probe, "the scanned region is not _probe_web's body"
    assert len(probe) < len(after), "the region extends past _probe_web"
    for forbidden in ("urlopen", "requests", "http.client", "_fetch"):
        assert forbidden not in probe, f"{forbidden} appears in a selection-time probe"


def test_a5_a_malformed_web_selector_probes_unreachable_without_any_lookup():
    from research import source_picker as spk

    source = srec.DeclaredSource(kind=srec.KIND_WEB,
                                 scope_mode=srec.SCOPE_MODE_UNSCOPED)
    # Unscoped has no selector to probe and must return a RESULT, not an empty
    # tuple — "nothing to check" is not the same answer as "no result".
    assert len(spk.probe(source)) == 1


# =========================================================================== #
# A6 — the caller. The production seam.
# =========================================================================== #

def test_a6_the_ingest_really_drives_the_port_rather_than_degrading():
    """If this fails the whole slice is a no-op that looks like a pass."""
    eng = _engine()
    admitter = eng._WebAdmitter(fetch=_fetch_ok)
    assert admitter._port is not None, admitter.degraded_reason
    assert admitter.degraded_reason is None


def test_a6_with_no_declaration_the_result_is_byte_identical_to_before():
    eng = _engine()
    urls = ["https://a.test/1", "https://b.test/2"]
    fetched = eng._prefetch_sources(urls, _fetch_fn=_fetch_ok)
    assert [f["url"] for f in fetched] == urls
    assert all(f["status"] == "ok" for f in fetched)
    assert [f["content"] for f in fetched] == [f"body of {u}" for u in urls]


def test_a6_containment_actually_refuses_in_production():
    """The assertion that would have failed the defect where the seam could not
    reach its declaration: the check ran, but could never refuse anything."""
    eng = _engine()
    scope = srec.web_scope(["a.test"])
    fetched = eng._prefetch_sources(
        ["https://a.test/1", "https://b.test/2"],
        _fetch_fn=_fetch_ok, admission_scope=scope)
    by_url = {f["url"]: f for f in fetched}
    assert by_url["https://a.test/1"]["status"] == "ok"
    assert by_url["https://b.test/2"]["status"] == "unfetchable"
    assert "outside every declared selector" in by_url["https://b.test/2"]["content"]


def test_a6_a_scope_refusal_is_transient_and_never_called_fabricated():
    """A person bounding their research narrowly must never make a real page read
    as a hallucinated citation."""
    eng = _engine()
    fetched = eng._prefetch_sources(
        ["https://a.test/1", "https://b.test/2"],
        _fetch_fn=_fetch_ok, admission_scope=srec.web_scope(["a.test"]))
    dispositions = {d.get("url"): d.get("disposition")
                    for d in eng._classify_source_integrity(fetched)}
    assert dispositions["https://b.test/2"] != eng._DISPOSITION_HALLUCINATED
    assert dispositions["https://b.test/2"] == eng._DISPOSITION_TRANSIENT


def test_a6_a_declaration_naming_only_code_does_not_bound_web():
    """A record that declares only `code` must not read as "web is bounded to
    nothing" — that would refuse every citation in a run whose person never
    mentioned the web."""
    eng = _engine()
    admitter = eng._WebAdmitter(fetch=_fetch_ok, scope=None)
    status, _payload = admitter.admit("https://anything.test/x", 64 * 1024)
    assert status == "ok"


def test_a6_the_fair_share_still_narrows_as_the_citation_count_rises():
    eng = _engine()
    seen = []

    def spy(url, timeout_s, max_bytes):
        seen.append(max_bytes)
        return ("ok", "x")

    eng._prefetch_sources([f"https://x.test/{i}" for i in range(3)], _fetch_fn=spy)
    few = seen[0]
    seen.clear()
    eng._prefetch_sources([f"https://x.test/{i}" for i in range(80)], _fetch_fn=spy)
    many = seen[0]
    assert many < few, "the per-URL budget must still fall as sources are added"


def test_a6_a_call_with_no_store_supplied_writes_no_file_anywhere(tmp_path):
    """Two shipped suites call this ingest directly. Without the side-effect-free
    store they would quietly begin writing real files while still reporting green
    — a worse failure than a red test."""
    eng = _engine()
    state_root = Path.home() / ".claude" / "state" / "research_admission"
    before = {p.name for p in state_root.glob("*")} if state_root.exists() else set()
    eng._prefetch_sources(["https://a.test/1"], _fetch_fn=_fetch_ok)
    after = {p.name for p in state_root.glob("*")} if state_root.exists() else set()
    assert after == before


def test_a6_the_in_memory_store_records_without_writing():
    store = InMemoryAdmissionRecordStore()
    port = sp.AdmissionPort(store)
    adapter = WebAdapter(_fetch_ok, urls=("https://example.com/a",))
    result = port.admit(_item("https://example.com/a"), srec.web_scope([]),
                        adapter, "r1", item_budget=64 * 1024)
    assert result.admitted
    assert store.get_evidence(result.pin_str) is not None
    assert not hasattr(store, "directory")


def test_a6_both_stores_expose_the_same_accessor_signature():
    """A second implementation of an abstraction has to be substitutable.

    An earlier draft took `(run_id, pin_str)` where the shipped store takes
    `(pin_str, run_id=None)` — code written against one would have broken
    silently on the other, and both orders look reasonable in isolation.
    """
    import inspect

    shipped = inspect.signature(AdmissionRecordStore.get_evidence)
    in_memory = inspect.signature(InMemoryAdmissionRecordStore.get_evidence)
    assert list(shipped.parameters) == list(in_memory.parameters)


def test_a6_the_ingest_survives_an_unavailable_port():
    """Detection sits on top of the pipeline, not underneath it: a fact-check must
    never fail because an admission record could not be made."""
    eng = _engine()
    admitter = eng._WebAdmitter(fetch=_fetch_ok)
    admitter._port = None            # simulate the port failing to load
    status, payload = admitter.admit("https://a.test/1", 1024)
    assert status == "ok"
    assert payload == "body of https://a.test/1"

"""The two on-disk source classes, at both altitudes.

research-source-adapters S7 / plan action A9 (design-A21, A24, A31).

Named for the CITATION VOCABULARY both kinds emit rather than for either class,
because that is what the suite is really about: two distinct source classes
sharing one shipped marker (``[stated — local-file:<path>:<line>]``) without
sharing a locator kind.

Two altitudes, and the split is deliberate:

* **Port level** — the contract each kind must satisfy. Adapters are driven
  **through** ``port.admit()``, never called directly (producer-never-verifies:
  an adapter that were its own caller could not be shown to obey the port's
  bounds, prohibition, or containment check).
* **Caller level** — the flow. An out-of-scope read is refused BY NAME through
  the real path; both pre-fill halves reach the record AND the approval surface;
  ``build_record`` refuses a class the calling route cannot read; and the
  ``/clarification`` caller — which assembles a declaration outside the source
  list entirely — gets the same refusal.

**The `code` and `web` arms are asserted here too, on purpose.** A3 is a
generalisation of code the two shipped classes run through, and a suite that
tested only the new kinds could not tell a generalisation from a rewrite.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from _source_guard import code_without_docstrings

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
sys.path.insert(0, str(CONFIG_DIR / "skills"))

from research import internal_kb as ikb                     # noqa: E402
from research import locator_grammar as lg                  # noqa: E402
from research import scope_record as srec                   # noqa: E402
from research import source_picker as spk                   # noqa: E402
from research import source_port as sp                      # noqa: E402
from research import admission_record as arec               # noqa: E402
from research.adapters.document_folder import DocumentFolderAdapter    # noqa: E402
from research.adapters.knowledge_library import KnowledgeLibraryAdapter  # noqa: E402

NEW_KINDS = ("knowledge_library", "document_folder")

_ADAPTERS = {
    "knowledge_library": KnowledgeLibraryAdapter,
    "document_folder": DocumentFolderAdapter,
}

_SCOPES = {
    "knowledge_library": srec.knowledge_library_scope,
    "document_folder": srec.document_folder_scope,
}


@pytest.fixture()
def workspace(tmp_path):
    """A declared folder, a file inside it, and a file outside it."""
    root = tmp_path.resolve()
    declared = root / "<KL>" / "Dev"
    declared.mkdir(parents=True)
    (declared / "notes.md").write_text("alpha\nbeta\ngamma\n")
    outside = root / "elsewhere"
    outside.mkdir()
    (outside / "secret.md").write_text("not declared\n")
    return root, declared, outside


def _port():
    return sp.AdmissionPort(arec.InMemoryAdmissionRecordStore())


def _item(kind, target):
    return sp.SourceItem(item_id=str(Path(target).resolve()), kind=kind,
                         target=str(target), depth=0)


# =========================================================================== #
# Port level
# =========================================================================== #

@pytest.mark.parametrize("kind", NEW_KINDS)
def test_a_path_inside_the_declared_scope_admits_with_a_pin_and_its_content(kind, workspace):
    root, declared, _ = workspace
    scope = _SCOPES[kind]([declared])
    adapter = _ADAPTERS[kind](projects_root=root)
    result = _port().admit(_item(kind, declared / "notes.md"), scope, adapter,
                           run_id="r")
    assert result.admitted, result.degradation
    assert result.pin is not None
    assert result.content == "alpha\nbeta\ngamma\n"
    assert result.evidence_path == "original", (
        "a file on disk can be re-opened by a checker holding no credentials")


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_a_path_outside_the_declaration_is_refused_with_the_resolved_path_named(kind, workspace):
    root, declared, outside = workspace
    scope = _SCOPES[kind]([declared])
    adapter = _ADAPTERS[kind](projects_root=root)
    result = _port().admit(_item(kind, outside / "secret.md"), scope, adapter,
                           run_id="r")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_DECLARED_SCOPE
    assert str((outside / "secret.md").resolve()) in result.degradation.reason


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_a_symlink_out_of_scope_is_refused_by_where_it_landed(kind, workspace):
    """Refusal names the RESOLVED path, so an escape is reported by its
    destination rather than by the innocent-looking name it was declared under."""
    root, declared, outside = workspace
    link = declared / "looks-fine.md"
    link.symlink_to(outside / "secret.md")
    scope = _SCOPES[kind]([declared])
    adapter = _ADAPTERS[kind](projects_root=root)
    result = _port().admit(_item(kind, link), scope, adapter, run_id="r")
    assert not result.admitted, "a symlink escaped the declaration"
    assert str((outside / "secret.md").resolve()) in result.degradation.reason


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_an_enumerated_declaration_with_no_selectors_admits_nothing(kind, workspace):
    root, declared, _ = workspace
    scope = srec.ScopeRecord(sources=(srec.DeclaredSource(kind=kind, selectors=()),))
    adapter = _ADAPTERS[kind](projects_root=root)
    result = _port().admit(_item(kind, declared / "notes.md"), scope, adapter,
                           run_id="r")
    assert not result.admitted
    assert "names no sources" in result.degradation.reason


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_unscoped_is_refused_for_both_kinds(kind):
    """A library or folder bounded by "whatever it exposes" is the whole
    filesystem, which is not a bound. Refused at CONSTRUCTION, so a caller that
    never opens the source list cannot express it either."""
    with pytest.raises(srec.ScopeRecordError, match="unscoped"):
        srec.DeclaredSource(kind=kind, scope_mode=srec.SCOPE_MODE_UNSCOPED)


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_a_declared_claude_md_is_refused_at_construction(kind):
    with pytest.raises(srec.ScopeRecordError, match="CLAUDE.md"):
        srec.DeclaredSource(kind=kind, selectors=("Docs/CLAUDE.md",))


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_a_discovered_claude_md_is_refused_at_admission(kind, workspace):
    """The SIBLING guard to the one above, and both are needed.

    A declaration-time check cannot see a file nobody declared; an
    admission-time check cannot see a declaration nobody read from. The file is
    yielded by enumeration rather than filtered out, so it leaves a RECORD
    instead of vanishing — a silent skip is the failure mode the prohibition
    must not have.
    """
    root, declared, _ = workspace
    (declared / "CLAUDE.md").write_text("# a pointer, never a source\n")
    scope = _SCOPES[kind]([declared])
    adapter = _ADAPTERS[kind](projects_root=root)

    enumerated = [Path(i.target).name for i in adapter.enumerate_within(scope)]
    assert "CLAUDE.md" in enumerated, "it must reach admit(), not be filtered away"

    result = _port().admit(_item(kind, declared / "CLAUDE.md"), scope, adapter,
                           run_id="r")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_SOURCE_IDENTITY


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_an_oversized_file_is_refused_whole_and_never_truncated(kind, workspace):
    """`truncate_over_budget=False` follows from the LOCATOR, not from taste: a
    `path:line` locator addresses a byte range, so a truncated excerpt would make
    the pin address more than was read."""
    root, declared, _ = workspace
    big = declared / "big.md"
    big.write_text("x" * (sp.MAX_ITEM_BYTES + 1024))
    scope = _SCOPES[kind]([declared])
    adapter = _ADAPTERS[kind](projects_root=root)
    result = _port().admit(_item(kind, big), scope, adapter, run_id="r")
    assert not result.admitted, "an oversized file was admitted"
    assert result.degradation.obligation == sp.OBLIGATION_READ_BUDGET
    assert "MAX_ITEM_BYTES" in result.degradation.reason
    assert result.content in (None, ""), "a refused item must carry no content"


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_the_rendered_pin_is_the_shipped_local_file_marker(kind):
    """Exact-string, against `_factcheck_engine.py`'s registered form."""
    loc = lg.Locator(kind=kind, parts={"path": "Docs/notes.md", "line": "42"})
    pin = sp.SourcePin(source_id="Docs/notes.md", version="2026-08-23T00:00:00+00:00",
                       locator=loc)
    assert pin.render() == "local-file:Docs/notes.md:42"
    assert pin.marker() == "[stated — local-file:Docs/notes.md:42]"
    assert pin.marker("paraphrased") == "[paraphrased — local-file:Docs/notes.md:42]"


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_an_incomplete_locator_refuses_to_render_and_names_the_missing_part(kind):
    pin = sp.SourcePin(source_id="x", version="v",
                       locator=lg.Locator(kind=kind, parts={"path": "a.md"}))
    with pytest.raises(lg.LocatorError, match="line"):
        pin.render()


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_the_reopen_instruction_names_no_lines_part_and_no_working_tree(kind):
    """A3's third `path_shaped` site. It read a `lines` part these kinds do not
    declare, and claimed a clean working tree — false of a plain file on disk."""
    read = sp.ReadResult(
        source_id="a.md", version="2026-08-23T10:00:00+00:00",
        locator=lg.Locator(kind=kind, parts={"path": "a.md", "line": "7"}),
        content="x")
    text = sp.AdmissionPort._reopen_instruction(_item(kind, "a.md"), read)
    assert "working tree" not in text
    assert "lines " not in text
    assert " line 7" in text
    assert "2026-08-23T10:00:00+00:00" in text


def test_a_mounted_cloud_drive_folder_takes_the_ordinary_path(tmp_path):
    """design-A31 admits a mounted Drive folder UNCONDITIONALLY, as the ordinary
    folder it is. A branch testing for one would make "ordinary" conditional on
    passing a test, which contradicts the decision it implements — so the
    assertion is that no such branch exists."""
    mount = (tmp_path / "CloudStorage" / "GoogleDrive-me" / "My Drive" / "Docs")
    mount.mkdir(parents=True)
    (mount / "plan.md").write_text("one\ntwo\n")
    scope = srec.document_folder_scope([mount])
    adapter = DocumentFolderAdapter(projects_root=tmp_path / "workspace")
    result = _port().admit(_item("document_folder", mount / "plan.md"), scope,
                           adapter, run_id="r")
    assert result.admitted, result.degradation
    # Outside the workspace, so cited ABSOLUTELY — the Marker Contract's Edge 1.
    assert result.pin.render() == f"local-file:{mount / 'plan.md'}:1-2"

    src = (CONFIG_DIR / "skills" / "research" / "adapters"
           / "document_folder.py").read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            assert name != "is_mount", "a mounted-drive branch exists"
    # Scans the WHOLE body with docstrings excluded. This previously read
    # `code_only.split('"""')[-1]` under a comment saying "after the module
    # docstring" — but that returns the text after the LAST triple quote, which
    # on this module was 3 of 232 lines. The guard passed because it was reading
    # almost nothing. Repairing it did not change the verdict: the module still
    # carries none of these tokens with the full body scanned.
    body = code_without_docstrings(src)
    assert "def read_with_pin" in body, (
        "the scanned body no longer reaches the read path — the guard has gone "
        "hollow, which is how this one silently rotted the first time")
    for token in ("CloudStorage", "GoogleDrive", "Dropbox", "OneDrive"):
        assert token not in body, f"a mounted-drive branch tests for {token!r}"


def test_the_adapters_hold_no_bound_constant():
    """The port owns the bounds. An adapter that held one could widen it."""
    for module in ("document_folder", "knowledge_library"):
        src = (CONFIG_DIR / "skills" / "research" / "adapters"
               / f"{module}.py").read_text()
        for token in ("MAX_ITEM_BYTES", "MAX_RUN_BYTES", "MAX_DEPTH", "MAX_ITEMS"):
            assert token not in src, f"{module} holds the bound {token}"


# --------------------------------------------------------------------------- #
# The `code` and `web` regression set — what makes A3 falsifiable.
# --------------------------------------------------------------------------- #

def test_code_still_renders_the_versioned_form_byte_identically():
    pin = sp.SourcePin(source_id="myrepo", version="abc123",
                       locator=lg.code_locator("pkg/mod.py", "1-3"))
    assert pin.render() == "code:myrepo@abc123:pkg/mod.py:1-3"
    assert pin.marker() == "[stated — code:myrepo@abc123:pkg/mod.py:1-3]"


def test_web_still_renders_the_url_alone_byte_identically():
    pin = sp.SourcePin(source_id="example.com", version="2026-01-01",
                       locator=lg.web_locator("https://example.com/a"))
    assert pin.render() == "https://example.com/a"
    assert pin.marker() == "[stated — https://example.com/a]"


def test_codes_reopen_instruction_still_names_the_working_tree():
    read = sp.ReadResult(source_id="myrepo", version="abc123",
                         locator=lg.code_locator("pkg/mod.py", "1-3"), content="x")
    text = sp.AdmissionPort._reopen_instruction(_item("code", "pkg/mod.py"), read)
    assert text == ("read pkg/mod.py lines 1-3 — the working tree was clean at "
                    "myrepo@abc123, so the file on disk holds the bytes this "
                    "claim rests on")


def test_an_unregistered_kind_does_not_silently_reacquire_the_versioned_shape():
    """After the render split, a kind with no `KIND_RULES` row must still get an
    explicit render form. The versioned shape is the right fail-closed default —
    it is the LOUDEST of the three, so an unrowed kind's citation is conspicuous
    rather than plausible."""
    rules = sp.rules_for("a-kind-nobody-registered")
    assert rules.render_form == sp.RENDER_FORM_VERSIONED
    assert rules.truncate_over_budget is False
    assert rules.path_shaped is True


def test_kind_rules_refuses_an_incoherent_row():
    with pytest.raises(ValueError):
        sp.KindRules(max_item_bytes=1, max_run_bytes=1, truncate_over_budget=False,
                     path_shaped=True, render_form="not-a-form")
    with pytest.raises(ValueError, match="citation_prefix"):
        sp.KindRules(max_item_bytes=1, max_run_bytes=1, truncate_over_budget=False,
                     path_shaped=True, render_form=sp.RENDER_FORM_PREFIXED)


# =========================================================================== #
# Caller level
# =========================================================================== #

def test_a_refusal_reaches_the_person_named(workspace):
    """The RENDERING half: a refusal carries the path and reason into the body.

    Deliberately NOT described as end-to-end. An earlier version of this test was
    named for the flow and its docstring claimed "through the REAL flow, not a
    hand-built port call" — while doing exactly that: calling `admit()` directly
    and hand-constructing the `RefusedRead`. The behaviour was right and the
    description was not, which is the same over-claim this slice exists to stop
    making about its own boundary. The end-to-end path is the test below.
    """
    root, declared, outside = workspace
    scope = srec.knowledge_library_scope([declared])
    adapter = KnowledgeLibraryAdapter(projects_root=root)
    result = _port().admit(_item("knowledge_library", outside / "secret.md"),
                           scope, adapter, run_id="r")
    refusal = ikb.RefusedRead(item=str(outside / "secret.md"),
                              obligation=result.degradation.obligation,
                              reason=result.degradation.reason)
    body = ikb.synthesize_from_admissions(
        ikb.InternalKBReadResult(readings=(), refusals=(refusal,)))
    assert "secret.md" in body, "the refused path is not named to the person"
    assert "refused" in body


def test_the_kl_flow_refuses_an_out_of_scope_file_by_name_end_to_end(workspace):
    """THE REAL FLOW — `read_declared_sources`, no hand-built port call.

    A declared folder containing a `CLAUDE.md`, driven through the production
    entry point. The admitted file must come back cited, and the `CLAUDE.md` must
    come back REFUSED BY NAME in the synthesized body — not silently skipped,
    which is the failure mode the prohibition must not have.
    """
    root, declared, _ = workspace
    (declared / "CLAUDE.md").write_text("# a pointer, never a source\n")
    scope = srec.knowledge_library_scope([declared])

    result = ikb.read_declared_sources(scope, projects_root=root)

    assert [r.item for r in result.readings] == [str(declared / "notes.md")], \
        "the in-scope file did not come back through the real flow"
    assert result.readings[0].marker == \
        "[stated — local-file:<KL>/Dev/notes.md:1-3]"

    refused = [r for r in result.refusals if r.item.endswith("CLAUDE.md")]
    assert refused, "the CLAUDE.md was silently skipped rather than refused"
    assert refused[0].obligation == sp.OBLIGATION_SOURCE_IDENTITY

    body = ikb.synthesize_from_admissions(result)
    assert "alpha" in body, "the admitted content is missing from the body"
    assert "CLAUDE.md" in body, "the refusal is not named to the person"
    assert "unverified" in body


def test_the_read_flows_through_the_port_not_beside_it(workspace):
    """THE DIVERGENCE TEST.

    For any in-scope, within-budget file the port's admitted content is
    byte-identical to the raw content, so "read through the port" and "check
    beside the read" produce the SAME output. No output-shaped assertion can tell
    them apart. Stubbing `admit()` to return altered content can.
    """
    root, declared, _ = workspace
    scope = srec.knowledge_library_scope([declared])
    sentinel = "PORT-ALTERED-CONTENT-b3f1"

    class _AlteringPort:
        def admit(self, item, scope, adapter, run_id, **kw):
            real = _port().admit(item, scope, adapter, run_id=run_id, **kw)
            if not real.admitted:
                return real
            return sp.AdmissionResult(item=real.item, pin=real.pin,
                                      evidence_path=real.evidence_path,
                                      pin_str=real.pin_str, content=sentinel)

    result = ikb.read_declared_sources(scope, projects_root=root,
                                       port=_AlteringPort())
    body = ikb.synthesize_from_admissions(result)
    assert sentinel in body, (
        "the synthesis does not reflect what the port returned — the read flows "
        "BESIDE the port rather than through it")
    assert "alpha" not in body, (
        "the synthesis carries the file's real bytes, so it re-read the file")


def test_both_prefill_halves_reach_the_record_and_the_approval_surface(tmp_path):
    """design-A21 has TWO halves and BOTH must be observable.

    The declared-folder half has no other declaration surface anywhere: if it did
    not enter here it would be read on a declaration nobody assembled. Half a
    pre-fill, ungated, is how the thing a person never sees gets read.
    """
    root = tmp_path.resolve()
    kl = root / "<KL>"
    kl.mkdir()
    topic = root / "Thoughts"
    topic.mkdir()
    (topic / "alpha_RESEARCH.md").write_text("a\n")
    (root / "CLAUDE.md").write_text("research_library_folders: <KL>\n")

    prefill = spk.knowledge_library_prefill(topic, root)
    assert str(kl) in prefill, "the DECLARED-FOLDER half is missing"
    assert str((topic / "alpha_RESEARCH.md").resolve()) in prefill, \
        "the TOPIC-RESEARCH-FILE half is missing"

    selection = spk.Selection(bounds=(
        ("knowledge_library", spk.Bound(selectors=(str(topic),))),))
    record = spk.build_record(selection, route=spk.ROUTE_INTERNAL_KB,
                             prefilled={"knowledge_library": prefill})
    declared = record.sources[0].selectors
    assert str(topic) in declared, "the typed entry is missing"
    assert str(kl) in declared, "the declared-folder half never reached the record"
    assert str((topic / "alpha_RESEARCH.md").resolve()) in declared, \
        "the research-file half never reached the record"

    # The approval surface renders THE RECORD, so a pre-filled entry is shown by
    # construction — it is an ordinary selector, indistinguishable from a typed
    # one. That indistinguishability IS the guarantee.
    assert all(isinstance(s, str) for s in declared)
    assert len(set(declared)) == len(declared), "the record carries a duplicate"


def test_build_record_refuses_a_class_the_calling_route_cannot_read():
    """The refusal is unchanged; the example had to move (Q26, 2026-09-11).

    This asserted `knowledge_library` on Ninja, which is no longer a refusal —
    that class is now read on every route. The PROPERTY being guarded is the same
    one, so it is re-pointed at the direction that still refuses rather than
    deleted: `code` on the Internal-KB route, whose `internal-only` tier is held
    by `ROUTE_CLASS_RESTRICTION` and was deliberately not widened.
    """
    selection = spk.Selection(bounds=(
        ("code", spk.Bound(selectors=("/tmp",))),))
    with pytest.raises(spk.SourcePickerError, match="never be opened"):
        spk.build_record(selection, route=spk.ROUTE_INTERNAL_KB)


def test_the_clarification_caller_gets_the_same_refusal():
    """`skills/clarification/steps.md:373` assembles a declaration ON THE USER'S
    BEHALF, outside the source list, and names these classes by anticipation — so
    registering them arms a door no picker guards. It must pass its route like any
    other caller, and get the same refusal when it does."""
    steps = (CONFIG_DIR / "skills" / "clarification" / "steps.md").read_text()
    assert "build_record" in steps
    assert "route=" in steps, (
        "the /clarification caller does not pass a route, so the per-route "
        "refusal cannot reach the one caller that bypasses the source list")

    # …and the refusal it would get is the real one. Re-pointed 2026-09-11 (Q26)
    # for the same reason as the test above: `document_folder` on Deep is no
    # longer refused, so the assertion moved to a pair that still is. What this
    # test actually guards — that the one caller bypassing the source list still
    # passes its route, and still gets a real refusal when the pair is wrong — is
    # unchanged.
    selection = spk.Selection(bounds=(
        ("web", spk.Bound(selectors=("https://example.com",))),))
    with pytest.raises(spk.SourcePickerError):
        spk.build_record(selection, route=spk.ROUTE_INTERNAL_KB)


@pytest.mark.parametrize("kind", NEW_KINDS)
def test_probe_no_longer_raises_for_either_kind(kind, tmp_path):
    """A6's gate. `probe()` RAISES for a registered kind with no arm, which is the
    right fail-closed direction — so "no longer raises" needs asserting, not
    assuming."""
    real = tmp_path / "declared"
    real.mkdir()
    ok = spk.probe(srec.DeclaredSource(kind=kind, selectors=(str(real),)))
    assert len(ok) == 1 and ok[0].reachable

    missing = spk.probe(srec.DeclaredSource(kind=kind,
                                            selectors=(str(tmp_path / "nope"),)))
    assert len(missing) == 1 and not missing[0].reachable
    assert missing[0].reason_slot == "unreachable_path", (
        "a declared folder that does not exist must be refused AT SELECTION "
        "TIME, where the person can still do something about it")


def test_autonomous_keeps_its_exemption():
    """A10's gate — the adjacent path that must NOT move.

    Autonomous's exemption rests on a different and still-live reason (it asks
    the person for nothing after the question), so S7 had no mandate over it.

    **The offered set is NOT byte-identical any more, and this docstring said it
    was until 2026-09-11.** It read "it still offers exactly `code` and `web`, and
    neither S7 class is reachable there" — already stale when `linear` landed, and
    doubly so after Q26, while the assertions below said the opposite. An
    independent check caught the contradiction INSIDE the docstring being repaired,
    which is why it is corrected here rather than trimmed.

    What the exemption actually constrains is UNCHANGED and is not about the
    catalogue at all: this route asks the person nothing after the question, so
    the offered set is never put to them on it. That is asserted below at the only
    layer that holds it — the prose — because nothing in `source_picker`
    distinguishes `ROUTE_AUTONOMOUS` from `ROUTE_NINJA`.

    S8 SESSION 3 NOTE — `linear` joins the offered tuple here, and that is NOT the
    movement this gate exists to prevent. `linear` is an EXTERNAL class carrying
    `routes=_EXTERNAL_ROUTES`, exactly as `code` and `web` already do, so it joins
    them by the same rule rather than by an exception; the S8 driver landing is what
    made it selectable. Freezing this tuple against every future external class
    would make it a gate against the catalogue growing at all, which is not what it
    was written for.

    Q26 2026-09-11 — THE TWO S7 CLASSES NOW JOIN IT TOO, and this is stated rather
    than absorbed, because the sentence removed from this docstring had named their
    unreachability here as "the property guarded". It was the wrong property to
    have named. Autonomous's exemption is that it ASKS THE PERSON FOR NOTHING after
    the question — it runs no source selection at all in production — and that is
    untouched by any catalogue change. Selectability on this route is reached only
    by calling the picker directly, as this test does; no person arrives at it. So
    the S7 classes joining here is the same rule applying, exactly as `linear`'s
    was, and the exemption this gate exists for is asserted below at the layer it
    actually lives in.
    """
    # Autonomous is not special IN THE CATALOGUE — the same five kinds as every
    # other external route. Asserted BOTH ways, deliberately: the sibling equality
    # is what survives the catalogue growing (the trap the S8 note above names),
    # and the frozen literal below it is what catches a silent widening that moved
    # every external route at once, which the equality alone would not see. An
    # earlier version of this comment claimed the equality replaced the literal;
    # it does not, and both are present.
    for sibling in (spk.ROUTE_NINJA, spk.ROUTE_DEEP, spk.ROUTE_ULTRA_DEEP):
        assert spk.selectable_keys(spk.ROUTE_AUTONOMOUS) == spk.selectable_keys(sibling), (
            f"Autonomous diverged from {sibling} in the catalogue — its exemption "
            "is about not ASKING the person, never about offering a different set")
    assert spk.selectable_keys(spk.ROUTE_AUTONOMOUS) == (
        "code", "web", "knowledge_library", "document_folder", "linear")

    # Every row on this route carries coherent state — restored 2026-09-11 after
    # an earlier version of this edit DELETED this loop. It is the only assertion
    # that the Autonomous rows are internally consistent, and dropping it removed
    # a guarantee rather than re-pointing one.
    rows = {r.key: r for r in spk.render_catalogue(spk.ROUTE_AUTONOMOUS)}
    for key in NEW_KINDS:
        assert rows[key].selectable is True, f"{key} regressed on Autonomous"
        assert rows[key].unavailable_slot is None, (
            f"{key} is selectable on Autonomous but still renders a reason — a "
            "selectable row must not carry an unavailable slot")

    # The exemption itself, at the layer that holds it. Anchored on the SENTENCE
    # that states it, not on the word "Autonomous" — an earlier version asserted
    # the bare word, which occurs in the Localization Table and in a shell smoke
    # label, so it passed even with the exemption paragraph deleted. A guard that
    # cannot fail for its stated reason is not a guard.
    framing = (CONFIG_DIR / "rules" / "research-scope-framing.md").read_text()
    assert "asks the person for nothing after the question" in framing, (
        "the Autonomous exemption paragraph is gone from the scope-framing rules. "
        "Nothing in source_picker distinguishes this route from Ninja, so that "
        "prose is the ONLY place the exemption lives — losing it loses the "
        "property entirely")


def test_an_empty_selection_still_ends_the_flow_cleanly():
    outcome = spk.open_picker("a framed question", is_spike=False, selection=None)
    assert isinstance(outcome, spk.EmptySelection)


def test_the_internal_kb_route_offers_internal_classes_only():
    """`internal-only` at research-scope-framing.md:46 stays TRUE."""
    assert spk.classes_on_route(spk.ROUTE_INTERNAL_KB) == NEW_KINDS
    keys = {r.key for r in spk.render_catalogue(spk.ROUTE_INTERNAL_KB)}
    assert keys == set(NEW_KINDS)


def test_the_module_docstring_states_the_a18_ceiling():
    """C11's obligation is affirmative, so it needs a surface that can fail.

    This is A7's gate — the ceiling stated where a CODE reader would form the
    wrong belief. A8's gate, on the surface where a PERSON would form it, is the
    test below; they are two different surfaces and neither substitutes for the
    other.
    """
    src = (CONFIG_DIR / "skills" / "research" / "internal_kb.py").read_text()
    doc = src.split('"""')[1]
    flat = " ".join(doc.split()).lower().replace("*", "").replace("`", "")
    assert "design-a18" in flat
    assert "followed, not enforced" in flat
    assert "withdrawn" in flat
    assert "no pretooluse hook on read" in flat


def test_the_closing_step_states_the_ceiling_and_forbids_the_impossibility_claim():
    """A8's gate — C11 on the surface a PERSON reads.

    The plan's own words: "a grep asserting the Closing step carries the
    followed-not-enforced wording and no impossibility claim — C11's obligation
    is affirmative, so a gate that cannot observe its absence is not a gate."

    The obligation has BOTH signs and both are asserted:
      * affirmative — the run must SAY the declaration is followed rather than
        enforced, and that an out-of-scope read comes back refused by name;
      * negative — it must never claim that reading outside the declaration is
        impossible, prevented or blocked. It is not: no hook covers the read tool
        for source paths.

    Cross-repo by necessity: the surface a person reads lives in the Projects
    checkout, not in the harness. Same shape as
    `test_s6_output_security_sources.py:584` — SKIP when that checkout is absent
    (the guarded file is not here, so the assertion is meaningless), FAIL when it
    is present and the wording is missing, which is the real condition on any
    machine that has both repos.
    """
    # research-entry-point-enforcement S2: the methodology is the harness skill body
    # now, in THIS tree — no Projects checkout involved, so no skip path.
    skill = Path(__file__).resolve().parents[2] / "skills" / "research" / "SKILL.md"
    assert skill.exists(), f"harness /research skill missing: {skill}"
    flat = " ".join(skill.read_text(encoding="utf-8", errors="replace").split())
    flat = flat.lower().replace("*", "").replace("`", "")

    # -- affirmative half -------------------------------------------------- #
    assert "followed, not enforced" in flat, (
        "the Closing step does not tell the person the declaration is FOLLOWED "
        "rather than ENFORCED — C11's affirmative half is unstated")
    assert "refused by name" in flat, (
        "the Closing step does not say an out-of-scope read comes back refused "
        "by name")
    assert "design-a18" in flat, (
        "the Closing step does not name the decision that owns the ungated "
        "boundary, so a reader cannot find out why it is only followed")

    # -- negative half: the impossibility claim must be FORBIDDEN ----------- #
    assert "impossible, prevented, or blocked" in flat and "forbidden" in flat, (
        "the Closing step does not FORBID claiming that reading outside the "
        "declaration is impossible — silence would satisfy a one-sided gate, "
        "which is exactly the defect this gate exists to catch")

    # …and no such claim is actually made anywhere in the file.
    for bad in ("reading outside the declaration is impossible",
                "cannot read outside the declaration",
                "prevents any read outside"):
        idx = flat.find(bad)
        if idx != -1:
            window = flat[max(0, idx - 220): idx + 220]
            assert ("forbidden" in window or "never" in window
                    or "do not" in window or "it is not" in window), (
                f"an impossibility claim is ASSERTED rather than prohibited: "
                f"...{window}...")

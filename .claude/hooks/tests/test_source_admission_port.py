"""Gate for research-source-adapters S3 — the admission contract.

Named for what it tests, following S2's `test_citation_marker_registry.py`, and
deliberately NOT `test_s3_*`: `tests/research_pipeline/test_s3_walking_skeleton.py`
already exists and belongs to a different topic whose slices are also numbered
`S<n>`.

Construction follows the Cockburn four-step growth order
(`code_first_architecture.md:281-291`), and the sections below are in that order:

    test-to-test   A4 against FakeAdapter with a fake store directory
    real-to-test   the real AdmissionPort + real store against FakeAdapter
    test-to-real   the real CodeBaseAdapter against a temp git repo, driven directly
    real-to-real   the real port + real store + real adapter over a temp git repo

WHAT THIS MODULE DOES NOT PROVE — stated rather than implied:

* **Nothing about a person's experience.** S3 ships no picker (S4), no report
  (S11) and no run marker (S13). Every assertion here is about what the port
  *records*, never about what anyone *sees*.
* **Nothing about whether an excerpt supports a claim.** Nothing anywhere does:
  the per-claim grading layer (design-A8/A13) was descoped and deleted by slice
  D5 on 2026-09-01. The port's boundary is unchanged — it was outside the port
  when it existed, and there is nothing to be outside of now.
* **Nothing about the credential path beyond the field.** This slice builds the
  non-secret `connection_id` on `DeclaredSource` and nothing else; there is no
  `credential_path.py` (S8's) and nothing here resolves a connection. The
  positive test is A2's; the negative — that the seam is a *field* and not a
  credential module — is `test_a7_*` below.
* **The read-only claim is BOUNDED, not structural.** `test_a7_code_base_*` is a
  static AST scan. A static scan is defeatable by dynamic dispatch; the genuinely
  structural answer is a tool-grant restriction, which this layer does not have.
  Gate 2 files that row as **Code (bounded)** for exactly this reason.
* **Calibration of the four bounds.** They are editorial (Design Review §1). The
  tests prove a bound is *enforced and named*, never that its number is right.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import warnings
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
SKILLS_DIR = CONFIG_DIR / "skills"
RESEARCH_DIR = SKILLS_DIR / "research"

sys.path.insert(0, str(HOOKS_DIR))
sys.path.insert(0, str(SKILLS_DIR))

from research import admission_record as arec           # noqa: E402
from research import locator_grammar as lg              # noqa: E402
from research import scope_record as srec               # noqa: E402
from research import source_port as sp                  # noqa: E402
from research.adapters.code_base import (               # noqa: E402
    GIT_READONLY_SUBCOMMANDS,
    CodeBaseAdapter,
)

SLICE_MODULES = (
    RESEARCH_DIR / "locator_grammar.py",
    RESEARCH_DIR / "scope_record.py",
    RESEARCH_DIR / "admission_record.py",
    RESEARCH_DIR / "source_port.py",
    RESEARCH_DIR / "adapters" / "__init__.py",
    RESEARCH_DIR / "adapters" / "code_base.py",
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture()
def store(tmp_path):
    return arec.AdmissionRecordStore(tmp_path / "records", slug="t",
                                     ts="20260101000000")


@pytest.fixture()
def port(store):
    return sp.AdmissionPort(store)


def _load_by_path(name, path):
    """Import a module from an explicit path, bypassing the bare-name cache.

    Necessary because other modules in this suite deliberately import harness
    modules from the LIVE tree; the first importer wins the bare name. Assertions
    about the tree under test must not depend on collection order.
    """
    import importlib.util
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    # Registered under its UNIQUE name before exec: `dataclasses` resolves string
    # annotations through `sys.modules[cls.__module__]`, which is None for a
    # module that was never registered. The unique name cannot collide with the
    # bare name another test module may have cached from the live tree.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _git(cwd, *args):
    """Test-side git. The READ-ONLY constraint is on the adapter, not on tests."""
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture()
def repo(tmp_path):
    """A temp git repository with one commit — never a live repo."""
    root = tmp_path / "myrepo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", "user.email=t@example.com", "-c", "user.name=T",
         "commit", "-q", "-m", "init")
    return root


def _item(target, depth=0):
    return sp.SourceItem(item_id=str(target), kind="code", target=str(target),
                         depth=depth)


def _read(content="x", *, dirty=False, path="a.py", lines="1-1",
          source_id="repo", version="abc123"):
    return sp.ReadResult(source_id=source_id, version=version,
                         locator=lg.code_locator(path, lines),
                         content=content, dirty=dirty)


# =========================================================================== #
# A1 — locator grammar
# =========================================================================== #

def test_a1_code_kind_round_trips():
    loc = lg.code_locator("pkg/mod.py", "10-20")
    assert loc.render() == "pkg/mod.py:10-20"
    assert lg.parse("code", loc.render()) == loc


def test_a1_incomplete_locator_is_representable_and_names_the_missing_part():
    """Representable, because the port must be able to degrade it and SAY which
    part is absent (C10). Only render() refuses."""
    loc = lg.code_locator("pkg/mod.py")           # no `lines`
    assert loc.missing_required_parts() == ("lines",)
    assert not loc.is_complete()
    with pytest.raises(lg.LocatorError) as e:
        loc.render()
    assert "lines" in str(e.value)


def test_a1_two_part_kind_with_one_field_supplied_fails_loudly():
    with pytest.raises(lg.LocatorError) as e:
        lg.parse("code", "pkg/mod.py")            # one field for a 2-part kind
    assert "2 part" in str(e.value) or "requires 2" in str(e.value)


def test_a1_unknown_kind_fails_closed():
    with pytest.raises(lg.LocatorError):
        lg.get_kind("sheets")
    with pytest.raises(lg.LocatorError):
        lg.Locator(kind="sheets", parts={"cell": "A1"})


def test_a1_undeclared_part_is_refused_not_silently_ignored():
    with pytest.raises(lg.LocatorError) as e:
        lg.Locator(kind="code", parts={"path": "a.py", "sheet": "S1"})
    assert "sheet" in str(e.value)


def test_a1_registers_exactly_the_four_locator_kinds():
    """No Sheets/Drive kind here — S9 is struck, and a MOUNTED Drive folder is an
    ordinary `document_folder` path with no kind of its own (design-A31).

    Re-pointed by S6 (design-A29), which registers `web`, and again by S7
    (design-A21/A24/A31), which registers `knowledge_library` + `document_folder`.
    The assertion stays EXACT rather than becoming a containment check
    (`"code" in ...`): the point of this test is that a kind nobody registered
    cannot appear, and a loosened assertion would stop catching that.

    **Why S7 edits this test in a suite it otherwise leaves unedited.** The plan
    sanctioned one test edit — `test_source_picker.py` — on the rule that an
    assertion about *availability* is the slice's subject while every assertion
    about *behaviour* stays untouched. This is the same class: a registration
    census, not a behaviour check. The plan's rule was right and its enumeration
    was one file short, which is this topic's recorded failure mode (the write
    target list has under-counted at every slice). Every behaviour assertion in
    this file still passes unedited, which is what keeps A3's generalisation
    falsifiable.

    **The two S7 kinds are deliberately SEPARATE** even though they emit one
    shared citation vocabulary: `KIND_RULES` is keyed by the LOCATOR kind in
    `render()` and by the SOURCE kind in `admit()`, so a single shared kind would
    make those lookups disagree. The sharing is expressed as
    `KindRules.citation_prefix` instead — explicitly, rather than by naming.
    """
    assert lg.registered_kinds() == ("code", "document_folder",
                                     "knowledge_library", "linear", "web")
    assert lg.get_kind("code").required_parts == ("path", "lines")
    assert lg.get_kind("web").required_parts == ("url",)
    assert lg.get_kind("knowledge_library").required_parts == ("path", "line")
    assert lg.get_kind("document_folder").required_parts == ("path", "line")
    # No optional parts on either S7 kind, so `parse()` stays defined for them.
    assert lg.get_kind("knowledge_library").optional_parts == ()
    assert lg.get_kind("document_folder").optional_parts == ()

    # S8's `linear` (design-A12) is the FIRST kind here to declare an optional
    # part: a comment does not exist independently of the issue that carries it,
    # so `comment` is optional and `issue` alone is a complete, re-openable
    # address. The documented consequence is that `parse()` is undefined for this
    # kind — asserted, so the narrowing stays visible rather than being discovered.
    assert lg.get_kind("linear").required_parts == ("issue",)
    assert lg.get_kind("linear").optional_parts == ("comment",)
    with pytest.raises(lg.LocatorError):
        lg.parse("linear", "ENG-1")


def test_a1_path_may_contain_a_colon_because_parse_splits_from_the_right():
    loc = lg.code_locator("odd:name/mod.py", "3")
    assert lg.parse("code", loc.render()) == loc


# =========================================================================== #
# A2 — scope record
# =========================================================================== #

def test_a2_read_inside_the_declaration_is_admitted(tmp_path):
    root = tmp_path / "declared"
    root.mkdir()
    rec = srec.code_scope([root])
    result = rec.check("code", root / "sub" / "f.py")
    assert result.admitted
    assert result.matched_selector == str(root.resolve())


def test_a2_read_outside_the_declaration_is_refused_naming_the_resolved_path(tmp_path):
    root = tmp_path / "declared"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "f.py"
    rec = srec.code_scope([root])
    result = rec.check("code", outside)
    assert not result.admitted
    assert result.resolved_target == str(outside.resolve())     # C6


def test_a2_symlink_escaping_the_declaration_is_refused(tmp_path):
    """E1 — without full resolution BEFORE the comparison the check is decorative."""
    declared = tmp_path / "declared"
    declared.mkdir()
    secret_dir = tmp_path / "outside"
    secret_dir.mkdir()
    (secret_dir / "secret.py").write_text("s", encoding="utf-8")
    link = declared / "escape"
    link.symlink_to(secret_dir, target_is_directory=True)

    rec = srec.code_scope([declared])
    result = rec.check("code", link / "secret.py")
    assert not result.admitted, "a symlinked escape must not be admitted"
    # Reported by where it LANDED, not by how it was spelled.
    assert result.resolved_target == str((secret_dir / "secret.py").resolve())
    assert "declared" not in Path(result.resolved_target).parts


def test_a2_declaration_naming_a_claude_md_is_refused_at_construction(tmp_path):
    """E4's declaration half — it surfaces BEFORE any read."""
    with pytest.raises(srec.ScopeRecordError) as e:
        srec.code_scope([tmp_path / "proj" / "CLAUDE.md"])
    assert "CLAUDE.md" in str(e.value)


def test_a2_enumerated_record_with_no_selectors_admits_nothing(tmp_path):
    """E6 — fail-closed, not fail-open."""
    rec = srec.ScopeRecord(sources=(srec.DeclaredSource(
        kind="code", scope_mode=srec.SCOPE_MODE_ENUMERATED, selectors=()),))
    result = rec.check("code", tmp_path / "anything.py")
    assert not result.admitted
    assert "names no sources" in result.reason


def test_a2_unscoped_mode_is_representable_and_is_not_read_as_empty(tmp_path):
    """The shape `…_DESIGN.md:294-297` requires be recorded EXPLICITLY, "not an
    absent field". E6's fail-closed rule must not swallow it."""
    rec = srec.ScopeRecord(sources=(srec.DeclaredSource(
        kind="code", scope_mode=srec.SCOPE_MODE_UNSCOPED),))
    result = rec.check("code", tmp_path / "anything.py")
    assert result.admitted, "unscoped means the bound is the source's own exposure"


def test_a2_unscoped_with_selectors_is_refused_as_a_contradiction():
    with pytest.raises(srec.ScopeRecordError):
        srec.DeclaredSource(kind="code", scope_mode=srec.SCOPE_MODE_UNSCOPED,
                            selectors=("/tmp",))


def test_a2_unknown_kind_fails_closed_at_construction_and_at_check(tmp_path):
    # S8 NOTE — the stand-in moved from `linear` to `gitlab`. S8 Session 1
    # registers `linear`, so it can no longer play the unknown kind; `gitlab` is
    # registered nowhere. The property is unchanged.
    with pytest.raises(srec.ScopeRecordError):
        srec.DeclaredSource(kind="gitlab", selectors=("X",))
    rec = srec.code_scope([tmp_path])
    assert not rec.check("gitlab", tmp_path / "f").admitted


def test_a2_unknown_scope_mode_fails_closed():
    with pytest.raises(srec.ScopeRecordError):
        srec.DeclaredSource(kind="code", scope_mode="whatever", selectors=("/tmp",))


def test_a2_record_round_trips_through_its_json_form(tmp_path):
    rec = srec.code_scope([tmp_path / "a", tmp_path / "b"], connection_id="conn-1")
    back = srec.ScopeRecord.from_json(rec.to_json())
    assert back.to_dict() == rec.to_dict()
    doc = json.loads(rec.to_json())
    assert doc["schema_version"] == srec.SCHEMA_VERSION
    assert doc["sources"][0]["scope_mode"] == "enumerated"
    assert doc["sources"][0]["connection_id"] == "conn-1"


def test_a2_unknown_schema_version_refuses_rather_than_admits():
    """The clause that makes a later schema additive instead of a bash rewrite."""
    with pytest.raises(srec.ScopeRecordError):
        srec.ScopeRecord.from_dict({"schema_version": 99, "sources": []})


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq not installed")
def test_a2_json_form_survives_a_reader_that_cannot_import_a_python_type(tmp_path):
    """The record crosses a process boundary as JSON, so the persisted shape is a
    cross-surface contract decided here. `jq` stands in for any such reader.

    **RENAMED, because the old name and docstring asserted a false fact.** They
    said this record is enforced from `research-scope-gate.sh`, "a jq-based bash
    hook". That hook reads only the `r1_scope_approved` / `r1_scope_revoked` flags
    beside the record; every consumer of the record ITSELF is Python —
    `research_pipeline` validates its shape at registration, and `declared_read` +
    `source_port.AdmissionPort` read its content at admission. The shape assertion
    below is unchanged and was never the problem."""
    rec = srec.code_scope([tmp_path / "a", tmp_path / "b"])
    out = subprocess.run(
        ["jq", "-r", '.sources[] | select(.kind=="code") | .selectors[]'],
        input=rec.to_json(), capture_output=True, text=True, check=True).stdout
    assert out.split() == [str(tmp_path / "a"), str(tmp_path / "b")]


def test_a2_declared_source_carries_a_connection_id_field():
    """design-A5's contract surface: the record can CARRY a connection
    identifier. Nothing in this slice resolves one — see test_a7_*."""
    names = {f.name for f in dataclass_fields(srec.DeclaredSource)}
    assert "connection_id" in names
    assert srec.DeclaredSource(kind="code", selectors=("/tmp",),
                               connection_id="acct_42").connection_id == "acct_42"


@pytest.mark.parametrize("value", [
    "ghp_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA/BBBB",   # slash
    "Bearer abc123",                                        # whitespace + prefix
    "aGVsbG8gd29ybGQ=",                                     # base64 padding
    "-----BEGIN PRIVATE KEY-----",                          # whitespace
    '{"token": "x"}',                                       # JSON
    "a" * 129,                                              # over the length bound
    "tok+en",                                               # +
])
def test_a2_secret_shaped_connection_id_is_refused(value):
    """An ALLOWLIST, not a secret detector: everything that is not plainly an
    opaque identifier is refused, so the failure direction is a rejected
    legitimate handle rather than an admitted credential."""
    with pytest.raises(srec.ScopeRecordError):
        srec.DeclaredSource(kind="code", selectors=("/tmp",), connection_id=value)


def test_a2_record_is_immutable_after_creation(tmp_path):
    rec = srec.code_scope([tmp_path])
    with pytest.raises(Exception):
        rec.sources = ()                                    # frozen dataclass
    with pytest.raises(Exception):
        rec.sources[0].selectors = ("/",)
    assert isinstance(rec.sources, tuple)


# =========================================================================== #
# A3 — admission record store
# =========================================================================== #

def test_a3_evidence_entry_is_retrievable_by_its_rendered_pin_alone(store):
    pin_str = "code:myrepo@abc123:pkg/mod.py:1-3"
    store.put_evidence(arec.EvidenceEntry(
        run_id="r1", pin_str=pin_str, evidence_path=arec.EVIDENCE_ORIGINAL,
        reopen_instruction="read pkg/mod.py lines 1-3"))
    # No run_id passed — the pin is the address a later reader holds.
    got = store.get_evidence(pin_str)
    assert got is not None and got.pin_str == pin_str


def test_a3_original_entry_yields_a_reopen_instruction(store):
    store.put_evidence(arec.EvidenceEntry(
        run_id="r1", pin_str="p", evidence_path=arec.EVIDENCE_ORIGINAL,
        reopen_instruction="read /x/y.py lines 4-9"))
    entry = store.get_evidence("p")
    assert entry.evidence_path == arec.EVIDENCE_ORIGINAL
    assert entry.reopen_instruction and "read" in entry.reopen_instruction


def test_a3_original_entry_without_a_reopen_instruction_is_refused():
    """C14 — a branch that claims a re-openable source and supplies no way to
    reach it delivers neither half of the outcome."""
    with pytest.raises(arec.AdmissionRecordError):
        arec.EvidenceEntry(run_id="r", pin_str="p",
                           evidence_path=arec.EVIDENCE_ORIGINAL)


def test_a3_captured_entry_round_trips_its_excerpt(store):
    text = "line one\nline two\n"
    store.put_evidence(arec.EvidenceEntry(
        run_id="r1", pin_str="p", evidence_path=arec.EVIDENCE_CAPTURED,
        excerpt=text))
    assert store.get_evidence("p").excerpt == text


def test_a3_captured_entry_without_an_excerpt_is_refused():
    with pytest.raises(arec.AdmissionRecordError):
        arec.EvidenceEntry(run_id="r", pin_str="p",
                           evidence_path=arec.EVIDENCE_CAPTURED)


def test_a3_degradation_is_retrievable_with_no_pin_in_existence(store):
    """The pair's whole point: a pin-keyed store cannot hold the rejection half."""
    store.put_degradation(arec.DegradationRecord(
        run_id="r1", item_id="/x/CLAUDE.md",
        obligation=sp.OBLIGATION_SOURCE_IDENTITY, reason="never a source identity"))
    (only,) = store.list_degradations("r1")
    assert only.item_id == "/x/CLAUDE.md"
    assert only.obligation == sp.OBLIGATION_SOURCE_IDENTITY
    assert only.reason
    assert store.get_evidence("anything") is None       # no pin exists at all


def test_a3_degradation_needs_item_obligation_and_reason():
    for kwargs in ({"item_id": ""}, {"obligation": ""}, {"reason": ""}):
        base = {"run_id": "r", "item_id": "i", "obligation": "o", "reason": "z"}
        base.update(kwargs)
        with pytest.raises(arec.AdmissionRecordError):
            arec.DegradationRecord(**base)


def test_a3_run_scoped_accessors_return_exactly_that_run(store):
    """The accessor S13 depends on — driven against a store holding TWO runs
    rather than assumed."""
    store.put_degradation(arec.DegradationRecord(
        run_id="r1", item_id="a", obligation="o1", reason="z"))
    store.put_degradation(arec.DegradationRecord(
        run_id="r2", item_id="b", obligation="o2", reason="z"))
    store.put_evidence(arec.EvidenceEntry(
        run_id="r1", pin_str="p1", evidence_path=arec.EVIDENCE_CAPTURED, excerpt=""))
    store.put_evidence(arec.EvidenceEntry(
        run_id="r2", pin_str="p2", evidence_path=arec.EVIDENCE_CAPTURED, excerpt=""))

    assert [d.item_id for d in store.list_degradations("r1")] == ["a"]
    assert [d.item_id for d in store.list_degradations("r2")] == ["b"]
    assert [e.pin_str for e in store.list_evidence("r1")] == ["p1"]
    assert [e.pin_str for e in store.list_evidence("r2")] == ["p2"]


def test_a3_same_item_may_fail_more_than_one_obligation(store):
    store.put_degradation(arec.DegradationRecord(
        run_id="r", item_id="a", obligation="o1", reason="z"))
    store.put_degradation(arec.DegradationRecord(
        run_id="r", item_id="a", obligation="o2", reason="z"))
    assert len(store.list_degradations("r")) == 2, "a list, not a map — no overwrite"


def test_a3_records_survive_process_exit(tmp_path):
    """Written by one process, read by another — the durability C4 asserts."""
    directory = tmp_path / "records"
    writer = arec.AdmissionRecordStore(directory, slug="t", ts="20260101000000")
    writer.put_degradation(arec.DegradationRecord(
        run_id="r1", item_id="a", obligation="o", reason="z"))
    writer.put_evidence(arec.EvidenceEntry(
        run_id="r1", pin_str="p", evidence_path=arec.EVIDENCE_CAPTURED, excerpt="e"))

    script = (
        "import sys, json\n"
        f"sys.path.insert(0, {str(SKILLS_DIR)!r})\n"
        "from research import admission_record as a\n"
        f"s = a.AdmissionRecordStore({str(directory)!r}, slug='t', ts='20260101000000')\n"
        "print(json.dumps({'d': [x.item_id for x in s.list_degradations('r1')],\n"
        "                  'e': [x.pin_str for x in s.list_evidence('r1')]}))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True,
                         text=True, check=True).stdout
    assert json.loads(out) == {"d": ["a"], "e": ["p"]}


def test_a3_artifact_conforms_to_the_advisory_slug_grammar(store, tmp_path):
    """The store writes into the topic's advisory bucket, so its filename must
    parse as the ADMISSION advisory TYPE — registered in BOTH
    `bookkeeping_invariant.ADVISORY_USER_TYPES` (authoritative) and
    `rules/bookkeeping-model.md` §4 Bucket 3 (mirror). There is no drift guard
    between those two, so a mirror-only edit would fail silently.

    Loaded BY PATH rather than by bare import name: other modules in this suite
    deliberately import `bookkeeping_invariant` from the LIVE harness, and the
    first one to do so caches it under the bare name (the same collision
    `tests/conftest.py` neutralises for the two shared engine modules). This test
    is about the tree under test, so it must load that tree's copy.
    """
    bk = _load_by_path("bookkeeping_invariant_under_test",
                       HOOKS_DIR / "bookkeeping_invariant.py")
    assert "ADMISSION" in bk.ADVISORY_USER_TYPES, (
        "the advisory TYPE must be registered in the CODE — the rules-file enum "
        "is a mirror with no drift guard behind it")

    store.put_degradation(arec.DegradationRecord(
        run_id="abc123", item_id="a", obligation="o", reason="z"))
    path = store.run_path("abc123")
    assert path.name == "t-20260101000000_ADMISSION_abc123.md"
    assert path.suffix == ".md", "every pattern in the slug grammar is .md-suffixed"
    membership = bk.classify(path.name)
    assert membership.bucket == bk.B_ADVISORY
    assert membership.slug == "t"


def test_a3_the_rules_mirror_and_the_code_enum_agree_on_admission():
    """The pair has NO automated drift guard (unlike the citation registry), so
    a mirror-only edit fails silently. This test is the substitute — narrow, and
    named as covering one TYPE rather than the whole enum."""
    bk = _load_by_path("bookkeeping_invariant_under_test",
                       HOOKS_DIR / "bookkeeping_invariant.py")
    mirror = (CONFIG_DIR / "rules" / "bookkeeping-model.md").read_text(encoding="utf-8")
    assert "ADMISSION" in bk.ADVISORY_USER_TYPES
    assert "ADMISSION" in mirror, (
        "rules/bookkeeping-model.md §4 Bucket 3 does not list the ADMISSION "
        "advisory TYPE the code registers")


def test_a3_store_imports_nothing_from_source_port():
    """Dependency runs ONE way: port -> store. That is what lets A3 be built
    before A4 despite the port being its only caller."""
    tree = ast.parse((RESEARCH_DIR / "admission_record.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert "source_port" not in (node.module or "")
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "source_port" not in alias.name


def test_a3_run_id_must_be_filename_safe(store):
    with pytest.raises(arec.AdmissionRecordError):
        store.run_path("../escape")


# =========================================================================== #
# A4 — the port, test-to-test (FakeAdapter)
# =========================================================================== #

def test_a4_claude_md_identity_is_refused_with_a_reason(port, store, tmp_path):
    """design-A11's enforcement half — the FIRST code in this area that refuses
    anything. S2's retirement is a vocabulary status, not an enforcement."""
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    target = root / "sub" / "CLAUDE.md"
    target.write_text("pointer", encoding="utf-8")
    scope = srec.code_scope([root])
    adapter = sp.FakeAdapter(reads={str(target): _read()})

    result = port.admit(_item(target), scope, adapter, "r1")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_SOURCE_IDENTITY
    assert "CLAUDE.md" in result.degradation.reason
    # Recorded, not silently skipped — a silent disposal is this prohibition's
    # named failure mode.
    assert store.list_degradations("r1")[0].item_id == str(target)


def test_a4_reopenable_clean_item_is_stamped_original(port, store, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    scope = srec.code_scope([root])
    adapter = sp.FakeAdapter(reads={str(target): _read("hello\n")},
                             can_reopen_without_credentials=True)
    result = port.admit(_item(target), scope, adapter, "r1")
    assert result.admitted
    assert result.evidence_path == arec.EVIDENCE_ORIGINAL
    entry = store.get_evidence(result.pin_str, "r1")
    assert entry.reopen_instruction and str(target) in entry.reopen_instruction


def test_a4_dirty_item_is_stamped_captured_and_keeps_the_excerpt(port, store, tmp_path):
    """E2/C15 — the pinned commit does not describe the bytes that were read, so
    the excerpt itself is what the claim rests on. The `captured` branch is
    exercised by the CODE BASE, not deferred to S8."""
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    scope = srec.code_scope([root])
    adapter = sp.FakeAdapter(reads={str(target): _read("uncommitted\n", dirty=True)},
                             can_reopen_without_credentials=True)
    result = port.admit(_item(target), scope, adapter, "r1")
    assert result.evidence_path == arec.EVIDENCE_CAPTURED
    assert "+dirty" in result.pin_str
    assert store.get_evidence(result.pin_str, "r1").excerpt == "uncommitted\n"


def test_a4_adapter_that_cannot_reopen_is_stamped_captured_even_when_clean(port, tmp_path):
    """The port selects from the DECLARED capability — the adapter never chooses."""
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    adapter = sp.FakeAdapter(reads={str(target): _read("x")},
                             can_reopen_without_credentials=False)
    result = port.admit(_item(target), srec.code_scope([root]), adapter, "r1")
    assert result.evidence_path == arec.EVIDENCE_CAPTURED


def test_a4_out_of_declaration_path_is_refused_AT_THE_PORT(port, store, tmp_path):
    """Driven through `admit()`, not by calling `scope_record.check()` directly:
    Gate 2 claims the port calls through, and a unit test on the callee proves
    only the callee."""
    root = tmp_path / "declared"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "f.py"
    reads = []

    class Spy(sp.FakeAdapter):
        def read_with_pin(self, item):
            reads.append(item.item_id)
            return _read()

    result = port.admit(_item(outside), srec.code_scope([root]), Spy(), "r1")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_DECLARED_SCOPE
    assert str(outside.resolve()) in result.degradation.reason      # C6
    assert reads == [], "refused BEFORE the read, not filtered afterwards (C5)"
    assert store.list_degradations("r1")


def test_a4_symlink_escape_is_refused_at_the_port(port, tmp_path):
    declared = tmp_path / "declared"
    declared.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "s.py").write_text("s", encoding="utf-8")
    (declared / "link").symlink_to(outside, target_is_directory=True)
    target = declared / "link" / "s.py"

    result = port.admit(_item(target), srec.code_scope([declared]),
                        sp.FakeAdapter(reads={str(target): _read()}), "r1")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_DECLARED_SCOPE


def test_a4_incomplete_locator_degrades_with_the_missing_part_named(port, tmp_path):
    """Also driven through `admit()` rather than `missing_required_parts()`."""
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    incomplete = sp.ReadResult(source_id="repo", version="abc",
                               locator=lg.code_locator("a.py"),  # no `lines`
                               content="x")
    adapter = sp.FakeAdapter(reads={str(target): incomplete})
    result = port.admit(_item(target), srec.code_scope([root]), adapter, "r1")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_LOCATOR
    assert "lines" in result.degradation.reason


def test_a4_missing_version_degrades(port, tmp_path):
    """E3 — a repository with no commits has no version identity to pin."""
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    adapter = sp.FakeAdapter(reads={str(target): sp.ReadResult(
        source_id="repo", version="", locator=lg.code_locator("a.py", "1"),
        content="x")})
    result = port.admit(_item(target), srec.code_scope([root]), adapter, "r1")
    assert result.degradation.obligation == sp.OBLIGATION_VERSION_READ


def test_a4_item_over_the_per_item_budget_degrades_and_is_never_truncated(port, store, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    target = root / "big.py"
    oversized = "x" * (sp.MAX_ITEM_BYTES + 1)
    adapter = sp.FakeAdapter(reads={str(target): _read(oversized)})
    result = port.admit(_item(target), srec.code_scope([root]), adapter, "r1")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_READ_BUDGET
    assert "MAX_ITEM_BYTES" in result.degradation.reason
    assert store.list_evidence("r1") == (), "C12 — nothing partial is stored"


def test_a4_aggregate_budget_degrades_the_crossing_item_and_keeps_earlier_ones(port, store, tmp_path):
    """The aggregate is the bound that actually protects the shared checker zone
    — the per-item bound alone permits many items."""
    root = tmp_path / "p"
    root.mkdir()
    chunk = "y" * (sp.MAX_ITEM_BYTES - 1)          # each passes the per-item bound
    n_fitting = sp.MAX_RUN_BYTES // len(chunk)
    targets = [root / f"f{i}.py" for i in range(n_fitting + 1)]
    adapter = sp.FakeAdapter(reads={str(t): _read(chunk, path=f"f{i}.py")
                                    for i, t in enumerate(targets)})
    scope = srec.code_scope([root])
    budget = sp._RunBudget()

    results = [port.admit(_item(t), scope, adapter, "r1", budget) for t in targets]
    assert all(r.admitted for r in results[:n_fitting]), "earlier items stay admitted"
    last = results[n_fitting]
    assert not last.admitted
    assert last.degradation.obligation == sp.OBLIGATION_READ_BUDGET
    assert "MAX_RUN_BYTES" in last.degradation.reason


def test_a4_run_cuts_off_an_adapter_that_yields_past_the_item_count_bound(port, store, tmp_path):
    """The adapter holds no bound constant; the port stops consuming."""
    root = tmp_path / "p"
    root.mkdir()
    over = sp.MAX_ITEMS + 5
    items = [_item(root / f"f{i}.py") for i in range(over)]
    adapter = sp.FakeAdapter(items=items,
                             reads={i.item_id: _read("x", path=f"f{n}.py")
                                    for n, i in enumerate(items)})
    port.run(srec.code_scope([root]), adapter, run_id="r1")

    assert len(adapter.yielded) == sp.MAX_ITEMS + 1, (
        "the port consumes exactly one item past the bound — the one it names — "
        "and then abandons the generator")
    bound_hits = [d for d in store.list_degradations("r1")
                  if d.obligation == sp.OBLIGATION_ENUMERATION_BOUND]
    assert len(bound_hits) == 1
    assert "MAX_ITEMS" in bound_hits[0].reason


def test_a4_item_deeper_than_the_depth_bound_degrades(port, store, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    deep = _item(root / "x.py", depth=sp.MAX_DEPTH + 1)
    shallow = _item(root / "y.py", depth=0)
    adapter = sp.FakeAdapter(items=[deep, shallow],
                             reads={shallow.item_id: _read("x", path="y.py")})
    port.run(srec.code_scope([root]), adapter, run_id="r1")

    (hit,) = [d for d in store.list_degradations("r1")
              if d.obligation == sp.OBLIGATION_ENUMERATION_BOUND]
    assert hit.item_id == deep.item_id
    assert "MAX_DEPTH" in hit.reason
    assert len(store.list_evidence("r1")) == 1, "the shallow item still admits"


def test_a4_unexpected_oserror_mid_read_degrades_rather_than_propagating(port, store, tmp_path):
    """The third-outcome hole: an exception escaping the run would be an item
    that is neither admitted nor rejected."""
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    adapter = sp.FakeAdapter(reads={str(target): OSError("disk went away")})
    result = port.admit(_item(target), srec.code_scope([root]), adapter, "r1")
    assert not result.admitted
    assert result.degradation.obligation == sp.OBLIGATION_READABLE
    assert "disk went away" in result.degradation.reason


def test_a4_unexpected_non_oserror_also_degrades(port, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    target = root / "a.py"
    adapter = sp.FakeAdapter(reads={str(target): RuntimeError("boom")})
    result = port.admit(_item(target), srec.code_scope([root]), adapter, "r1")
    assert not result.admitted
    assert "RuntimeError" in result.degradation.reason, (
        "a genuine bug still becomes a NAMED record rather than a silent third outcome")


def test_a4_no_result_can_be_both_or_neither():
    """C3 is structural, not a convention the caller must honour."""
    with pytest.raises(ValueError):
        sp.AdmissionResult(item="i")
    with pytest.raises(ValueError):
        sp.AdmissionResult(item="i",
                           pin=sp.SourcePin("r", "v", lg.code_locator("a", "1")),
                           degradation=sp.Degradation("i", "o", "z"),
                           evidence_path="original")
    with pytest.raises(ValueError):
        sp.AdmissionResult(item="i",
                           pin=sp.SourcePin("r", "v", lg.code_locator("a", "1")))


def test_a4_every_degradation_the_port_records_is_retrievable_afterwards(port, store, tmp_path):
    root = tmp_path / "p"
    (root / "sub").mkdir(parents=True)
    inside = root / "sub" / "ok.py"
    claude = root / "sub" / "CLAUDE.md"
    outside = tmp_path / "away" / "no.py"
    items = [_item(inside), _item(claude), _item(outside)]
    adapter = sp.FakeAdapter(items=items,
                             reads={str(inside): _read("ok\n", path="sub/ok.py")})
    port.run(srec.code_scope([root]), adapter, run_id="r1")

    recorded = {(d.item_id, d.obligation) for d in store.list_degradations("r1")}
    assert (str(claude), sp.OBLIGATION_SOURCE_IDENTITY) in recorded
    assert (str(outside), sp.OBLIGATION_DECLARED_SCOPE) in recorded
    assert len(store.list_evidence("r1")) == 1
    assert all(d.reason for d in store.list_degradations("r1")), "never a bare rejection"


def test_a4_run_mints_a_run_id_and_threads_it_onto_both_record_kinds(port, store, tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    ok, bad = root / "a.py", tmp_path / "out.py"
    adapter = sp.FakeAdapter(items=[_item(ok), _item(bad)],
                             reads={str(ok): _read("x", path="a.py")})
    run_id = port.run(srec.code_scope([root]), adapter)     # no run_id supplied
    assert run_id and run_id != ""
    assert all(d.run_id == run_id for d in store.list_degradations(run_id))
    assert all(e.run_id == run_id for e in store.list_evidence(run_id))


def test_a4_failure_is_written_at_the_moment_it_is_reached_not_flushed_at_end(port, store, tmp_path):
    """C11 — the timing property, distinguished from a batch flush.

    The observer runs INSIDE the adapter's generator, between items. If the port
    accumulated results and wrote them after the walk, the store would still be
    empty when item 2 is yielded.
    """
    root = tmp_path / "p"
    root.mkdir()
    first_bad = tmp_path / "away" / "one.py"        # out of declaration
    second = root / "two.py"
    observed = {}

    def on_yield(item):
        if item.item_id == str(second):
            observed["mid_run"] = list(store.list_degradations("r1"))

    adapter = sp.FakeAdapter(
        items=[_item(first_bad), _item(second)],
        reads={str(second): _read("x", path="two.py")},
        on_yield=on_yield)
    port.run(srec.code_scope([root]), adapter, run_id="r1")

    assert "mid_run" in observed, "the observer must have run"
    assert [d.item_id for d in observed["mid_run"]] == [str(first_bad)], (
        "item 1's degradation was durable BEFORE item 2 was even yielded — a "
        "flush at end of run would leave this empty")


def test_a4_pin_renders_the_three_parts_and_the_citation_marker():
    pin = sp.SourcePin("myrepo", "abc123", lg.code_locator("pkg/mod.py", "1-3"))
    assert pin.render() == "code:myrepo@abc123:pkg/mod.py:1-3"
    assert pin.marker("stated") == "[stated — code:myrepo@abc123:pkg/mod.py:1-3]"
    assert pin.marker("paraphrased").startswith("[paraphrased — code:")


def test_a4_bounds_live_only_in_the_port(port):
    """One home. The plan's round-1 finding was that they had three."""
    assert (sp.MAX_DEPTH, sp.MAX_ITEMS, sp.MAX_ITEM_BYTES, sp.MAX_RUN_BYTES) == (
        12, 2000, 64 * 1024, 192 * 1024)
    assert sp.MAX_ITEM_BYTES < sp.MAX_RUN_BYTES, (
        "equal budgets are degenerate: one near-max file would exhaust the run "
        "and the per-item bound would never bind independently")
    adapter_src = (RESEARCH_DIR / "adapters" / "code_base.py").read_text(encoding="utf-8")
    for name in ("MAX_DEPTH", "MAX_ITEMS", "MAX_ITEM_BYTES", "MAX_RUN_BYTES"):
        assert name not in adapter_src, (
            f"{name} appears in the adapter; a bound the adapter can see is a "
            "bound it can widen")


# =========================================================================== #
# A5 — the code-base adapter, test-to-real then real-to-real
# =========================================================================== #

def test_a5_file_inside_the_declaration_reads_with_a_full_pin(repo):
    """test-to-real: the REAL adapter against a real repo, driven directly."""
    adapter = CodeBaseAdapter()
    target = repo / "pkg" / "mod.py"
    read = adapter.read_with_pin(_item(target))
    assert read.source_id == "myrepo"
    assert read.version == _git(repo, "rev-parse", "HEAD")
    assert read.locator.parts["path"] == "pkg/mod.py", "repo-relative, never absolute"
    assert read.locator.parts["lines"] == "1-3"
    assert read.content == "one\ntwo\nthree\n"
    assert read.dirty is False


def test_a5_real_to_real_admits_with_a_pin_naming_repo_commit_path_and_lines(repo, port, store):
    """real-to-real: real port + real store + real adapter over a real repo."""
    run_id = port.run(srec.code_scope([repo]), CodeBaseAdapter(), run_id="r1")
    entries = store.list_evidence(run_id)
    assert len(entries) == 1, f"expected one file, got {[e.pin_str for e in entries]}"
    (entry,) = entries
    head = _git(repo, "rev-parse", "HEAD")
    assert entry.pin_str == f"code:myrepo@{head}:pkg/mod.py:1-3"
    assert entry.evidence_path == arec.EVIDENCE_ORIGINAL
    assert entry.reopen_instruction


def test_a5_dirty_working_tree_yields_plus_dirty_and_routes_to_captured(repo, port, store):
    (repo / "pkg" / "mod.py").write_text("one\ntwo\nEDITED\n", encoding="utf-8")
    run_id = port.run(srec.code_scope([repo]), CodeBaseAdapter(), run_id="r1")
    (entry,) = store.list_evidence(run_id)
    assert "+dirty" in entry.pin_str
    assert entry.evidence_path == arec.EVIDENCE_CAPTURED
    assert entry.excerpt == "one\ntwo\nEDITED\n", (
        "the excerpt itself is what the claim rests on — the pinned commit does "
        "not describe these bytes")


def test_a5_repo_with_no_commits_degrades(tmp_path, port, store):
    """E3 — A9 working as intended, not an error."""
    root = tmp_path / "fresh"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.py").write_text("x\n", encoding="utf-8")
    port.run(srec.code_scope([root]), CodeBaseAdapter(), run_id="r1")
    (hit,) = store.list_degradations("r1")
    assert hit.obligation == sp.OBLIGATION_VERSION_READ
    assert store.list_evidence("r1") == ()


def test_a5_binary_file_degrades_rather_than_yielding_a_garbled_excerpt(repo, port, store):
    """E5 — not citable by a line locator."""
    (repo / "pkg" / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=T",
         "commit", "-q", "-m", "blob")
    port.run(srec.code_scope([repo]), CodeBaseAdapter(), run_id="r1")
    hits = [d for d in store.list_degradations("r1") if d.item_id.endswith("blob.bin")]
    assert len(hits) == 1 and hits[0].obligation == sp.OBLIGATION_READABLE
    assert [e for e in store.list_evidence("r1") if "mod.py" in e.pin_str], (
        "the text file beside it still admits")


def test_a5_nested_repo_resolves_to_the_repo_that_owns_the_file(repo):
    """E7 — a pin naming the WRONG repo's commit is worse than no pin.

    A nested repository, not a registered submodule: the resolution mechanism is
    identical (`rev-parse --show-toplevel` from the file's own directory), and
    the real-submodule case is covered by the test below where git allows it.
    """
    inner = repo / "vendor" / "inner"
    inner.mkdir(parents=True)
    _git(inner, "init", "-q")
    _git(inner, "config", "user.email", "t@example.com")
    _git(inner, "config", "user.name", "T")
    (inner / "lib.py").write_text("inner\n", encoding="utf-8")
    _git(inner, "add", "-A")
    _git(inner, "-c", "user.email=t@example.com", "-c", "user.name=T",
         "commit", "-q", "-m", "inner")

    read = CodeBaseAdapter().read_with_pin(_item(inner / "lib.py"))
    assert read.source_id == "inner", "resolves to the owning repo, not the outer one"
    assert read.version == _git(inner, "rev-parse", "HEAD")
    assert read.version != _git(repo, "rev-parse", "HEAD")
    assert read.locator.parts["path"] == "lib.py"


def test_a5_real_submodule_resolves_to_the_submodule(tmp_path, repo):
    """The genuine `git submodule add` case, where this git allows a file:// URL."""
    donor = tmp_path / "donor"
    donor.mkdir()
    _git(donor, "init", "-q")
    _git(donor, "config", "user.email", "t@example.com")
    _git(donor, "config", "user.name", "T")
    (donor / "d.py").write_text("donor\n", encoding="utf-8")
    _git(donor, "add", "-A")
    _git(donor, "-c", "user.email=t@example.com", "-c", "user.name=T",
         "commit", "-q", "-m", "d")

    added = subprocess.run(
        ["git", "-C", str(repo), "-c", "protocol.file.allow=always",
         "submodule", "add", "-q", str(donor), "sub"],
        capture_output=True, text=True)
    if added.returncode != 0:
        pytest.skip(f"this git refuses a local submodule: {added.stderr.strip()}")

    read = CodeBaseAdapter().read_with_pin(_item(repo / "sub" / "d.py"))
    # `sub` is where the superproject PUT the checkout; `donor` is what the
    # repository IS. Identity comes from the origin remote for exactly this
    # reason — a pin keyed on the checkout name would differ between two
    # vendorings of the same upstream.
    assert read.source_id == "donor"
    assert read.version == _git(donor, "rev-parse", "HEAD")
    assert read.locator.parts["path"] == "d.py", "repo-relative to the SUBMODULE"


def test_a5_repo_identity_comes_from_the_origin_remote_when_there_is_one(repo, tmp_path):
    """Driven, not incidental: the identity must not be a property of where the
    clone happens to sit."""
    _git(repo, "remote", "add", "origin", "https://example.com/org/canonical.git")
    read = CodeBaseAdapter().read_with_pin(_item(repo / "pkg" / "mod.py"))
    assert read.source_id == "canonical"

    for url, expected in (("git@github.com:org/scp-style.git", "scp-style"),
                          ("https://example.com/org/no-suffix", "no-suffix"),
                          ("/local/path/plain-dir/", "plain-dir")):
        _git(repo, "remote", "set-url", "origin", url)
        assert CodeBaseAdapter().read_with_pin(
            _item(repo / "pkg" / "mod.py")).source_id == expected


def test_a5_repo_with_no_remote_falls_back_to_the_checkout_name(repo):
    """The only identity such a repo has. `config --get` exits non-zero on a
    missing key, and that is not a read failure."""
    assert CodeBaseAdapter().read_with_pin(
        _item(repo / "pkg" / "mod.py")).source_id == "myrepo"


def test_a5_enumeration_yields_a_claude_md_rather_than_filtering_it_out(repo):
    """The adapter filters NOTHING the port must decide. A skip would be silent,
    and a silent disposal is the prohibition's named failure mode."""
    (repo / "CLAUDE.md").write_text("pointer\n", encoding="utf-8")
    yielded = [Path(i.target).name for i
               in CodeBaseAdapter().enumerate_within(srec.code_scope([repo]))]
    assert "CLAUDE.md" in yielded


def test_a5_enumeration_does_not_descend_into_dot_git(repo):
    """The one structural exclusion: VCS metadata is not source content."""
    yielded = [i.target for i
               in CodeBaseAdapter().enumerate_within(srec.code_scope([repo]))]
    assert yielded, "the walk found something"
    assert not any(".git" in Path(t).parts for t in yielded)


def test_a5_enumeration_is_lazy_and_applies_no_bound(repo):
    """A generator, consumed on demand — the port is what stops it."""
    import types
    gen = CodeBaseAdapter().enumerate_within(srec.code_scope([repo]))
    assert isinstance(gen, types.GeneratorType)
    first = next(gen)
    assert isinstance(first, sp.SourceItem)
    gen.close()


def test_a5_enumeration_reports_depth_as_data(repo):
    (repo / "a" / "b" / "c").mkdir(parents=True)
    (repo / "a" / "b" / "c" / "deep.py").write_text("d\n", encoding="utf-8")
    by_name = {Path(i.target).name: i.depth for i
               in CodeBaseAdapter().enumerate_within(srec.code_scope([repo]))}
    assert by_name["deep.py"] == 3
    assert by_name["mod.py"] == 1


def test_a5_a_credential_less_reader_reaches_the_claim_from_the_pin_alone(repo, port, store):
    """C16, driven end to end through the real caller on BOTH branches.

    The reader here holds no credentials, no git and no run identity — only the
    store and the rendered pin, which is what a citation gives them. The engine
    is not consulted: across this topic's own research files it delivered no
    source content to any checker (O1), which is why the evidence had to come
    from this design's own store.
    """
    # --- clean tree: the `original` branch ---
    clean_run = port.run(srec.code_scope([repo]), CodeBaseAdapter(), run_id="clean")
    (clean_entry,) = store.list_evidence(clean_run)
    pin_str = clean_entry.pin_str

    found = store.get_evidence(pin_str)                 # pin alone, no run_id
    assert found is not None
    assert found.evidence_path == arec.EVIDENCE_ORIGINAL
    # Follow the instruction with a plain file read and nothing else.
    quoted = Path(found.reopen_instruction.split(" — ")[0].removeprefix("read ")
                  .split(" lines ")[0]).read_text(encoding="utf-8")
    assert quoted == "one\ntwo\nthree\n", (
        "the instruction must lead a plain file read to the bytes the claim "
        "rests on")

    # --- dirty tree: the `captured` branch, where the original CANNOT be re-opened ---
    (repo / "pkg" / "mod.py").write_text("one\ntwo\nEDITED\n", encoding="utf-8")
    dirty_run = port.run(srec.code_scope([repo]), CodeBaseAdapter(), run_id="dirty")
    (dirty_entry,) = store.list_evidence(dirty_run)
    reached = store.get_evidence(dirty_entry.pin_str)
    assert reached.evidence_path == arec.EVIDENCE_CAPTURED
    assert reached.excerpt == "one\ntwo\nEDITED\n", (
        "the excerpt IS the reach here — re-reading the file would be reading "
        "whatever it says now, not what was read")


def test_a5_out_of_declaration_file_never_reaches_a_read(repo, port, store, tmp_path):
    """real-to-real containment: the declared subtree bounds the run."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "other.py").write_text("nope\n", encoding="utf-8")
    scope = srec.code_scope([repo / "pkg"])
    run_id = port.run(scope, CodeBaseAdapter(), run_id="r1")
    pins = [e.pin_str for e in store.list_evidence(run_id)]
    assert pins and all("mod.py" in p for p in pins)
    assert not any("other.py" in p for p in pins)


# =========================================================================== #
# A7 — read-only scan, credential-absence, package manifest
# =========================================================================== #

# Write-capable names. `replace`/`rename` cover `os.replace`/`Path.replace`; the
# adapter uses neither `str.replace` nor `str.rename`, so a bare attribute-name
# check is exact here AND fails loudly if a future edit introduces one — which is
# the behaviour a gate should have.
_FORBIDDEN_ATTRS = {
    "write_text", "write_bytes", "mkdir", "makedirs", "remove", "unlink",
    "rmdir", "rmtree", "rename", "replace", "touch", "chmod", "chown",
    "symlink", "symlink_to", "link", "copy", "copy2", "copyfile", "copytree",
    "move", "truncate", "mkstemp", "mkdtemp", "NamedTemporaryFile",
}
_FORBIDDEN_MODULES = {"shutil", "tempfile"}
_WRITE_MODE_CHARS = set("wax+")


def _adapter_tree():
    return ast.parse((RESEARCH_DIR / "adapters" / "code_base.py")
                     .read_text(encoding="utf-8"))


def test_a7_code_base_has_no_write_capable_call():
    """A static scan, NOT an import check — an import check does not stop a
    shell-out. Bounded: see the module docstring."""
    tree = _adapter_tree()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""])
            for name in names:
                assert name.split(".")[0] not in _FORBIDDEN_MODULES, \
                    f"adapter imports write-capable module {name}"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                assert func.attr not in _FORBIDDEN_ATTRS, \
                    f"adapter calls write-capable `.{func.attr}()`"
            if isinstance(func, ast.Name):
                assert func.id not in _FORBIDDEN_ATTRS, \
                    f"adapter calls write-capable `{func.id}()`"
                if func.id == "open":
                    mode = None
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        mode = node.args[1].value
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            mode = kw.value.value
                    assert mode is None or not (set(str(mode)) & _WRITE_MODE_CHARS), \
                        f"adapter opens a file in write mode {mode!r}"


def test_a7_code_base_subprocess_use_is_argument_aware_not_banned():
    """`subprocess` is NOT banned: the adapter must shell out to `git` to resolve
    the commit and the dirty flag, and there is no pure-Python alternative in
    this codebase. A flat ban would fail every correct implementation of A5 — so
    the scan distinguishes reading from writing instead."""
    tree = _adapter_tree()
    subprocess_calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
    ]
    assert len(subprocess_calls) == 1, (
        "exactly ONE subprocess call site, so the runtime allowlist in `_git` "
        f"cannot be bypassed from inside this module; found {len(subprocess_calls)}")
    assert subprocess_calls[0].func.attr == "run"

    # ...and it must be lexically inside `_git`.
    inside_git = [
        n for f in ast.walk(tree)
        if isinstance(f, ast.FunctionDef) and f.name == "_git"
        for n in ast.walk(f)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
    ]
    assert len(inside_git) == 1, "the one subprocess call site is inside `_git`"

    # Every `_git(...)` call passes a LITERAL subcommand on the read-only allowlist.
    call_sites = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "_git"]
    assert call_sites, "the adapter does call git"
    for call in call_sites:
        assert call.args, "_git() is called with a subcommand"
        first = call.args[0]
        assert isinstance(first, ast.Constant) and isinstance(first.value, str), (
            "the git subcommand must be a string literal, so the scan can read it")
        assert first.value in GIT_READONLY_SUBCOMMANDS, (
            f"git subcommand {first.value!r} is not on the read-only allowlist")


def test_a7_git_allowlist_holds_only_read_only_subcommands():
    write_capable = {"commit", "add", "push", "checkout", "reset", "clean",
                     "rm", "mv", "merge", "rebase", "stash", "apply", "init",
                     "fetch", "pull", "tag", "branch", "gc", "prune", "worktree"}
    assert not (set(GIT_READONLY_SUBCOMMANDS) & write_capable)


def test_a7_git_helper_refuses_a_subcommand_off_the_allowlist(repo):
    """Belt and braces: the runtime check, not only the static scan."""
    from research.adapters import code_base as cb
    with pytest.raises(sp.AdapterError) as e:
        cb._git("commit", "-m", "nope", cwd=str(repo))
    assert "allowlist" in str(e.value)


def test_a7_nothing_in_this_slice_resolves_a_credential():
    """The seam is a FIELD, not a credential module. The positive half — that
    `DeclaredSource` carries `connection_id` — is A2's test.

    S8 NOTE — the first assertion here was `not (RESEARCH_DIR /
    "credential_path.py").exists()`, and it has been RETIRED rather than
    relaxed. It was a scope guard for S3, whose own docstring said
    "`credential_path.py` is S8's, and pre-empting it is not this slice's to
    do" — so S8 Session 1/2 building that module is the event the assertion was
    waiting for, not a regression. Asserting its continued absence would now
    assert that S8 never happened.

    **What survives is the assertion that actually carries the property**, and it
    is now stronger than a file-absence check: S3's OWN modules still resolve no
    credential. The credential path is a SEPARATE module that `connect_gate`
    consumes, which is exactly the split the Guiding Policy demands — "the
    credential path is a port with a keychain adapter, not a Linear function". If
    a future edit pulls credential handling back into `scope_record` or
    `source_port`, this still fails.
    """
    assert (RESEARCH_DIR / "credential_path.py").exists(), (
        "S8 builds the credential path; its absence now would mean the module "
        "was lost, not that the scope guard still holds")
    for path in SLICE_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "credential" not in (node.module or "").lower(), path
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "credential" not in alias.name.lower(), path
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assert "credential" not in node.name.lower(), (
                    f"{path.name} defines {node.name} — this slice resolves no "
                    "credential")


def test_a7_package_manifest_names_every_new_module():
    """Owned HERE rather than left to V1's file-list check — relying on a
    verification gate to notice an omission is relying on the omission happening."""
    import research
    doc = research.__doc__ or ""
    for path in SLICE_MODULES:
        token = path.parent.name if path.name == "__init__.py" else path.stem
        assert token in doc, f"__init__.py does not name {token}"
    assert "research-source-adapters S3" in doc, (
        "two topics number their slices S<n> in this package; each entry must "
        "name its topic")


def test_a7_every_declared_write_target_exists():
    for path in SLICE_MODULES:
        assert path.is_file(), f"declared write target missing: {path}"


def test_a7_citation_vocabulary_landed_across_all_three_loci():
    """A17 — one atomic edit. A partial edit fails the promotion by design."""
    import _factcheck_engine as fce
    forms = {m.form for m in fce.active_citation_markers()}
    assert "[stated — code:<repo>@<rev>:<path>:<lines>]" in forms
    assert "[paraphrased — code:<repo>@<rev>:<path>:<lines>]" in forms
    by_form = {m.form: m for m in fce.CITATION_MARKER_REGISTRY}
    stated = by_form["[stated — code:<repo>@<rev>:<path>:<lines>]"]
    para = by_form["[paraphrased — code:<repo>@<rev>:<path>:<lines>]"]
    assert (stated.locator, stated.source_class, stated.antipattern) == (
        "code", "internal", "exempt")
    assert para.antipattern == "checked", "matching every existing pair"
    # S8 NOTE — compare the engine against the mirror in ITS OWN tree. The mirror
    # constant is hardwired to `~/.claude/rules/...`, so under a
    # `claude-experiment` clone this compared the CLONE's registry against the LIVE
    # mirror and reported drift for every marker the clone had added but not yet
    # deployed. That is an artifact of where the suite runs, not a real divergence,
    # and it would have made every clone-developed vocabulary change look broken.
    from pathlib import Path as _P
    _mirror = _P(fce.__file__).resolve().parent.parent / "rules" / "research-scope-framing.md"
    assert not fce.check_citation_marker_drift(rules_path=_mirror), (
        "the rules mirror must agree with the registry; the Projects-side "
        "reference skips gracefully when that repo is not checked out")

    # S8 — the linear pair, added by design-A12/A17 as the same atomic three-loci
    # edit. Asserted here beside `code` so a partial edit fails this test too.
    assert "[stated — linear:<workspace>@<version>:<issue>]" in forms
    assert "[paraphrased — linear:<workspace>@<version>:<issue>]" in forms
    lin_stated = by_form["[stated — linear:<workspace>@<version>:<issue>]"]
    lin_para = by_form["[paraphrased — linear:<workspace>@<version>:<issue>]"]
    assert (lin_stated.locator, lin_stated.source_class, lin_stated.antipattern) == (
        "linear", "internal", "exempt")
    assert lin_para.antipattern == "checked", "matching every existing pair"


# --------------------------------------------------------------------------- #
# Derived reachability — {registered kind -> has a production driver}
#
# Plans: Thoughts/source-kind-driver-pin-20260823183034_PLAN.md      (the invariant)
#        Thoughts/source-kind-reachability-gate-20260823215421_PLAN.md (this move)
#
# THE INVARIANT NO LONGER LIVES HERE. It lives in
# `skills/research/kind_reachability.py`. A guard authored as a test is a guard
# nothing runs: `claude-verify` has no test lane, and adding one would put a
# `uv run --with pytest` NETWORK FETCH in the pre-bookend of every promotion. As a
# module CLI the same guard is wired into the candidate deploy-check for the cost
# of a ten-line shell block. The tests below EXERCISE that module; they do not
# re-implement it, and `test_reach_single_authority_*` fails if anyone does.
#
# WHY THE GREEN-RUN STATEMENT IS STILL HERE. It is not the invariant — it is data
# ABOUT the invariant, rendered from the module's own `unreachable_statement()`.
# It was written because `code` could not mismatch while its exemption STOOD, so
# without it a green run said nothing about the one kind the whole thing was
# about. That exemption is gone and `code` now derives as driven; the statement
# still earns its place by making a green run SAY which kinds are driven and which
# are excepted, rather than being silent about both.
# `warnings.warn` is used because pytest captures stdout on passing tests: a
# `print()` here would be invisible under the recorded invocation and the
# statement would not exist. Keeping it in the test while the MODULE owns the rule
# is single authority, not a second home.
#
# WHAT THIS IS NOT (it is reachability, never containment) and the stated limits
# of the derivation are carried in the module's docstring — one home, same rule.
# --------------------------------------------------------------------------- #

KIND_REACHABILITY_PATH = RESEARCH_DIR / "kind_reachability.py"

kr = _load_by_path("kind_reachability_under_test", KIND_REACHABILITY_PATH)

#: The `scope_record` object the MODULE reads — loaded BY PATH under a root-unique
#: name, so it is deliberately NOT the suite's bare-name `srec` (which the first
#: importer in this suite won from the live tree). A control that needs to move
#: the registry must patch THIS object; patching `srec` leaves the module's own
#: view untouched and the control passes while proving nothing.
kr_srec = kr.load_scope_record()


def _problems_text(problems):
    return "\n".join(problems)


def test_reach_module_is_where_the_invariant_lives():
    """The module exists, is loadable, and reads the tree it lives in."""
    assert KIND_REACHABILITY_PATH.is_file()
    assert kr.config_root() == CONFIG_DIR, (
        "the module must root from its own location, not from the environment")
    assert tuple(kr_srec.REGISTERED_KINDS) == tuple(srec.REGISTERED_KINDS), (
        "same tree, so the by-path load and the bare-name import must agree")


def test_reach_driver_file_set_is_derived_and_non_empty():
    """A4 — the VACUITY guard, and only that.

    An empty or mis-scoped scan cannot equal a two-element set, so this catches
    a derivation that looked at nothing. It does NOT catch FABRICATION: a set
    equality compares the returned value, so a hardcoded return passes it
    trivially. Fabrication is caught only by the scratch-tree control below,
    which changes the INPUT.

    THE LITERAL MOVED WHEN THE LOOP DID. It named `internal_kb.py` while that
    module DEFINED the one declared-source read loop. The loop moved to
    `declared_read.py` — route-neutral, and with the `code` arm this file set is
    the first evidence of — so the driver is that file now and `internal_kb`,
    holding only delegates, drives nothing. Same property, same shape, one name
    exchanged for another.
    """
    assert set(kr.derive_driver_kinds()) == {"_factcheck_engine.py",
                                             "declared_read.py"}


def test_reach_each_driver_file_contributes_at_least_one_kind():
    """False-block control for the Name-vs-Attribute defect.

    Every source-kind reference in both drivers is Attribute-form. A visitor
    reading only `ast.Name` derives zero kinds from each, every kind reads as
    undriven, and the suite fails on a correct codebase — a failure that looks
    like a finding and would be 'fixed' by weakening the test. This makes that
    collapse fail HERE, as an obvious bug, instead.
    """
    per_file = kr.derive_driver_kinds()
    empty = sorted(name for name, kinds in per_file.items() if not kinds)
    assert not empty, (
        f"driver file(s) {empty} contributed no source kind — the kind scan is "
        "broken (most likely inspecting only ast.Name and missing the "
        "Attribute-form references every driver actually uses), NOT a real "
        "reachability finding")


def test_reach_derivation_discriminates(monkeypatch):
    """Guards a derivation collapsed to all-driven or all-undriven by a bug.

    Deliberately NOT credited with catching a hardcoded map — any constant of
    the right shape satisfies this as trivially as it satisfies the set equality.

    RE-POINTED, NOT WEAKENED. The undriven half used to be satisfied by the
    shipped catalogue: `code` was registered with no driver, so `registered -
    driven` was non-empty for free. Wiring the code driver makes every SHIPPED
    kind driven, which would have left that half asserting nothing while still
    passing — a control that stops discriminating is worse than one that fails.
    So the undriven side is now supplied deliberately: a registered kind nothing
    reads. The property under test is unchanged — the derivation must separate
    driven from undriven — and it is now non-vacuous by construction rather than
    by accident of what had not shipped yet.

    S8 NOTE — the supplied kind moved from `linear` to `gitlab`, because S8
    Session 1 registers `linear` for real. The patch was momentarily REDUNDANT
    (registered-and-undriven `linear` supplied the undriven half by itself while
    its driver exemption stood) and was kept deliberately: Session 3 wires that
    driver and deletes the exemption, at which point every registered kind is
    driven again and this half would go vacuous without a supplied one. That
    moment has now arrived, so the patch is doing the work it was left for.

    S8 SESSION 3 — the final assertion still named `linear` while the rest of the
    test had moved to `gitlab`. It passed only because `linear` was undriven, so
    it read as a check on the supplied kind while actually checking the shipped
    one; wiring the driver is what exposed it. Re-pointed to the kind this test
    actually supplies.
    """
    monkeypatch.setattr(
        kr_srec, "REGISTERED_KINDS", tuple(kr_srec.REGISTERED_KINDS) + ("gitlab",))
    driven = set().union(*kr.derive_driver_kinds(srec=kr_srec).values())
    registered = set(kr_srec.REGISTERED_KINDS)
    assert driven, "no kind derived as driven — derivation collapsed"
    assert registered - driven, (
        "a registered kind nothing reads still derived as driven — the "
        "derivation collapsed to all-driven")
    assert "gitlab" not in driven, (
        "the kind supplied precisely because nothing reads it came back driven")


def test_reach_known_unreachable_entries_carry_a_reason_and_an_owner(monkeypatch):
    """An exception with no reason is indistinguishable from an oversight.

    RE-POINTED, NOT REMOVED. This iterated the live mapping, which held exactly
    one entry — `code` — until its driver landed and the entry was deleted in the
    same commit. An empty mapping makes the loop run zero times, so the test would
    have gone on PASSING while asserting nothing about anything. It now walks the
    live entries AND a monkeypatched one, so the shape rule is exercised whether
    or not any kind is currently excepted, and a real entry added later is still
    checked by the same walk.
    """
    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "linear",
                        ("no adapter is wired for this kind yet",
                         "the slice that registers it"))
    monkeypatch.setattr(
        kr_srec, "REGISTERED_KINDS", tuple(kr_srec.REGISTERED_KINDS) + ("gitlab",))
    assert kr.KNOWN_UNREACHABLE, "the walk below would be vacuous"
    for kind, entry in kr.KNOWN_UNREACHABLE.items():
        assert kind in kr_srec.REGISTERED_KINDS, (
            f"{kind!r} is excepted but not registered — stale entry")
        assert isinstance(entry, tuple) and len(entry) == 2, kind
        reason, owner = entry
        assert reason.strip(), f"{kind!r} exception carries no reason"
        assert owner.strip(), f"{kind!r} exception names no owner"


def test_reach_every_registered_kind_has_a_production_driver():
    """THE INVARIANT, as the gate calls it. Checked in BOTH directions.

    This is `kr.check()` — the same entry point `claude-verify` runs against a
    rendered candidate — so the suite and the gate cannot disagree about what the
    rule is. The green-run statement below is rendered from the module's data.
    """
    problems = kr.check()
    assert problems == [], _problems_text(problems)
    warnings.warn(kr.unreachable_statement(), stacklevel=1)


def test_reach_admission_port_run_has_no_production_caller():
    """A deliberate ORPHAN: it maps to no gap and no outcome claim.

    Carried because the filed item requires it, and because this is the cheapest
    place to notice the port's batch arm gaining its first production caller.
    Recorded as unmapped rather than traced through a gap it does not serve.
    """
    assert kr.derive_port_run_callers() == []


# --- single authority: the invariant has exactly ONE home ------------------- #

_REACH_OWNED_NAMES = ("KNOWN_UNREACHABLE", "derive_driver_kinds",
                      "derive_port_run_callers")


def _reach_definitions_of_owned_names(base=None):
    """Every file under `base` that DEFINES one of the owned names.

    Definition, not reference: an `ast.Assign`/`AnnAssign` target or a
    `FunctionDef`. `kr.KNOWN_UNREACHABLE` in this file is an Attribute load and
    is correctly ignored. Tests are INCLUDED in the walk — the suite is exactly
    where this invariant already lived once, so excluding it would make the guard
    vacuous against its own history.

    `base` is a parameter so the control below can point THIS function at a
    scratch tree. An earlier draft gave the control its own copy of the AST walk,
    which would have proved a re-implementation works while the shipped detector
    rotted — the very defect this file is guarding against, reproduced inside the
    guard.
    """
    found = {}
    for root_name in kr.SCAN_ROOT_NAMES:
        root = (CONFIG_DIR if base is None else Path(base)) / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(".bak-" in part for part in path.parts):
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, UnicodeDecodeError, ValueError):
                continue
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name in _REACH_OWNED_NAMES:
                        names.add(node.name)
                elif isinstance(node, ast.Assign):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name) and tgt.id in _REACH_OWNED_NAMES:
                            names.add(tgt.id)
                elif isinstance(node, ast.AnnAssign):
                    tgt = node.target
                    if isinstance(tgt, ast.Name) and tgt.id in _REACH_OWNED_NAMES:
                        names.add(tgt.id)
            if names:
                found[path] = sorted(names)
    return found


def test_reach_single_authority_invariant_has_exactly_one_home():
    """STANDING guard — not a one-time implementation grep.

    A guard asserted in two places is the drift this topic keeps producing, and
    this suite is where the second copy would appear. A one-time check at move
    time would be an AI-judgment step wearing a Code label on the one property
    already lost once, so it runs on every suite run instead.
    """
    definitions = _reach_definitions_of_owned_names()
    offenders = {str(p): names for p, names in definitions.items()
                 if p != KIND_REACHABILITY_PATH}
    assert offenders == {}, (
        "the source-kind reachability invariant must have exactly ONE home "
        f"({KIND_REACHABILITY_PATH}); these files also define part of it: "
        f"{offenders}")
    assert definitions.get(KIND_REACHABILITY_PATH) == sorted(_REACH_OWNED_NAMES), (
        "the one home must still define all of it — if this fails the invariant "
        "has been moved or partially deleted, not merely duplicated")


def test_reach_single_authority_control_would_catch_a_second_copy(tmp_path):
    """Proves the guard above is not vacuous — end to end, via the SAME detector.

    A decoy re-asserting the invariant is planted in a scratch tree — including
    one inside a `tests/` directory, since the suite is where the second copy
    would actually appear — and the shipped detector must report both. Without
    this, `offenders == {}` above could hold because the detector finds nothing
    anywhere.
    """
    decoy_body = ("KNOWN_UNREACHABLE = {}\n"
                  "def derive_driver_kinds():\n    return {}\n")
    (tmp_path / "hooks").mkdir(parents=True)
    (tmp_path / "hooks" / "tests").mkdir()
    (tmp_path / "hooks" / "decoy.py").write_text(decoy_body, encoding="utf-8")
    (tmp_path / "hooks" / "tests" / "test_decoy.py").write_text(
        decoy_body, encoding="utf-8")

    found = _reach_definitions_of_owned_names(base=tmp_path)
    assert sorted(p.name for p in found) == ["decoy.py", "test_decoy.py"], (
        "the shipped detector must see a plain second copy, in a test file as "
        "well as a production one; if this fails, the standing guard above is "
        "passing vacuously")
    assert all(names == ["KNOWN_UNREACHABLE", "derive_driver_kinds"]
               for names in found.values())


# --- A5: the answer follows the TARGET TREE, from default rooting ----------- #

_SYNTHETIC_DRIVER = """\
from research import scope_record as _sr


def go(port):
    return port.admit(_sr.KIND_WEB)
"""


def _build_synthetic_tree(base, kinds):
    """A minimal config tree: its own scope_record, and one real driver.

    `kinds` becomes that tree's `REGISTERED_KINDS`, which is what makes the two
    trees give different answers for a reason that can only come from the tree.
    """
    research = base / "skills" / "research"
    research.mkdir(parents=True)
    (base / "hooks").mkdir()
    body = "".join(f'KIND_{k.upper()} = "{k}"\n' for k in kinds)
    body += "REGISTERED_KINDS = (" + ", ".join(
        f"KIND_{k.upper()}" for k in kinds) + ",)\n"
    (research / "scope_record.py").write_text(body, encoding="utf-8")
    shutil.copy2(KIND_REACHABILITY_PATH, research / "kind_reachability.py")
    (base / "hooks" / "driver.py").write_text(_SYNTHETIC_DRIVER, encoding="utf-8")
    return research / "kind_reachability.py"


def test_reach_module_answer_follows_the_tree_it_was_loaded_from(tmp_path):
    """A5 — the two-tree control. THE property the wiring depends on.

    A `roots=` override does NOT satisfy this and must never be substituted for
    it: `roots=` narrows the SCAN while the registry read stays on the default
    tree, which is precisely the split-brain this exists to exclude. So the
    MODULE ITSELF is loaded from each synthetic tree and its DEFAULT-rooted entry
    points are called with no arguments.

    Order is load-bearing. Tree 1 is loaded and its `scope_record` resolved FIRST,
    so tree 1's registry is sitting in `sys.modules` when tree 2 runs. If the
    module registered `scope_record` under a fixed name, tree 2 would silently
    read tree 1's registry — the exact cross-tree bleed `_load_by_path`'s own
    name cache would otherwise reproduce inside the thing testing for it.
    """
    one = _build_synthetic_tree(tmp_path / "one", ("web", "rocket"))
    two = _build_synthetic_tree(tmp_path / "two", ("web",))

    mod1 = _load_by_path("kind_reachability_tree_one", one)
    srec1 = mod1.load_scope_record()                       # tree 1 cached first
    mod2 = _load_by_path("kind_reachability_tree_two", two)
    srec2 = mod2.load_scope_record()

    assert tuple(srec1.REGISTERED_KINDS) == ("web", "rocket")
    assert tuple(srec2.REGISTERED_KINDS) == ("web",), (
        "tree 2 read tree 1's registry — the scope_record load is not keyed on "
        "the tree, and every candidate deploy-check would read live")
    assert srec1 is not srec2

    # The tree SCAN followed the tree too: only the synthetic driver is seen.
    assert set(mod1.derive_driver_kinds()) == {"driver.py"}
    assert set(mod2.derive_driver_kinds()) == {"driver.py"}

    # And the INVARIANT's answer differs, from default rooting, for a reason that
    # exists only in tree 1.
    text1 = _problems_text(mod1.check())
    text2 = _problems_text(mod2.check())
    assert "rocket" in text1 and "no production driver" in text1
    assert "rocket" not in text2

    # Live is unaffected by either — the default rooting is per-module-file, so
    # three copies of this module answer about three different trees at once.
    assert kr.check() == []


def test_reach_missing_scope_record_fails_loudly_rather_than_skipping(tmp_path):
    """A candidate whose registry cannot be read must go RED, never green.

    Skipping here would let a broken candidate pass, which is the one failure
    mode a reachability gate must not have.
    """
    research = tmp_path / "skills" / "research"
    research.mkdir(parents=True)
    (tmp_path / "hooks").mkdir()
    shutil.copy2(KIND_REACHABILITY_PATH, research / "kind_reachability.py")
    mod = _load_by_path("kind_reachability_no_registry",
                        research / "kind_reachability.py")
    with pytest.raises(mod.ReachabilityError, match="scope_record not found"):
        mod.check()


def test_reach_cli_check_verb_exits_zero_on_live_and_names_the_module():
    """The verb `claude-verify` calls, called the way it calls it."""
    proc = subprocess.run(
        [sys.executable, str(KIND_REACHABILITY_PATH), "check"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "source-kind reachability" in proc.stdout


# --- controls: prove the guards actually fire ------------------------------ #
# Every negative case below is in-process and self-reverting (monkeypatch /
# tmp_path). An earlier draft said to edit `scope_record.py` in place and undo it
# by hand — non-idempotent, and an interrupted run would leave live production
# source corrupted. That was a safe-defaults violation written into the
# verification section of a plan about not trusting prose.

def test_reach_control_scratch_tree_makes_the_derivation_fail(tmp_path):
    """THE anti-FABRICATION control — the only one there is.

    It works by changing the INPUT, which is why a hardcoded return cannot
    survive it and why no output comparison substitutes for it. Over an empty
    tree the derivation must find nothing; a constant would still return the
    real two-element set and be caught here.
    """
    (tmp_path / "decoy.py").write_text("x = 1\n", encoding="utf-8")
    assert kr.derive_driver_kinds(roots=(tmp_path,)) == {}


def test_reach_control_unparseable_file_is_skipped_not_fatal(tmp_path):
    """A scratch file with broken syntax must not abort the walk."""
    (tmp_path / "broken.py").write_text("def (: <<<\n", encoding="utf-8")
    assert kr.derive_driver_kinds(roots=(tmp_path,)) == {}


def test_reach_control_newly_registered_kind_fails_with_no_literal_edited(
        monkeypatch):
    """The property the invariant shape bought — demonstrated, not argued.

    A fifth kind appears with no driver and no exception. The expected set is
    generated from `REGISTERED_KINDS`, so this must fail WITHOUT anyone editing
    an expected map. Note what is NOT patched: no expected literal exists to patch.

    Patched on `kr_srec`, the object the MODULE reads — not on the suite's bare
    `srec`, which since the move is a different object and would leave this
    control passing while exercising nothing.
    """
    monkeypatch.setattr(
        kr_srec, "REGISTERED_KINDS", tuple(kr_srec.REGISTERED_KINDS) + ("gitlab",))
    text = _problems_text(kr.check())
    assert "gitlab" in text
    assert "newly registered with no driver" in text, (
        "an unrecorded kind must be routed generically, not to a hardcoded slice")


def test_reach_control_the_kind_survives_the_gates_truncation_budget(monkeypatch):
    """A2 — the message is designed against a MEASURED budget, not a guess.

    On the only path this runs, `land_port.py:963` keeps the first 400 characters
    of AGGREGATE stderr from every check in `claude-verify`. A message that buries
    the kind name behind its rationale is a message nobody gets. The first
    element must name the kind inside 80 characters.
    """
    monkeypatch.setattr(
        kr_srec, "REGISTERED_KINDS", tuple(kr_srec.REGISTERED_KINDS) + ("gitlab",))
    problems = kr.check()
    assert problems, "the control did not fire"
    assert "gitlab" in problems[0][:80], (
        f"the kind name must be legible in the first 80 chars; got "
        f"{problems[0][:80]!r}")


def test_reach_control_stale_exception_fails_the_other_way(monkeypatch):
    """The reverse direction — what stops KNOWN_UNREACHABLE becoming a mute list.

    Pretend `web` is recorded unreachable while it demonstrably has a driver.
    A one-directional check would pass; this must not.
    """
    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "web", ("stale", "nobody"))
    text = _problems_text(kr.check())
    assert "web" in text
    assert "delete its KNOWN_UNREACHABLE entry" in text


def test_reach_control_dropping_the_exception_names_an_undriven_kind_and_routes_it(
        monkeypatch):
    """Dropping an exemption for a kind nothing reads must fail, naming and routing it.

    RE-POINTED, NOT REMOVED. This deleted `code`'s exemption, which no longer
    exists: its driver landed and the entry went with it in the same commit, so
    the delete raises `KeyError` before asserting anything. The property is the
    one that mattered and is unchanged — an exemption removed while the kind is
    still unread must fail, name the kind, and route it from METADATA rather than
    from a hardcoded slice name. It is now demonstrated on a registered kind that
    genuinely has no reader, which is what `code` was when this was written.

    S8 NOTE (Session 1) — no monkeypatched registration was needed while `linear`
    was a REAL registered kind carrying a REAL exemption, so this control ran
    against the production state rather than a simulated one.

    S8 NOTE (Session 3) — that window has closed exactly as Session 1 predicted:
    the driver landed and the entry went with it, so there is again no live
    exemption to delete and the production-state arm would raise `KeyError` before
    asserting anything. The control returns to a SUPPLIED kind — the same treatment
    `code` got when its driver landed. The property is unchanged in both moves; what
    rotates is only which kind is currently between registration and a driver.
    """
    monkeypatch.setattr(
        kr_srec, "REGISTERED_KINDS", tuple(kr_srec.REGISTERED_KINDS) + ("gitlab",))
    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "gitlab",
                        ("supplied by this control", "this test"))
    assert kr.check() == [], "with its exemption in place the kind must be clean"

    monkeypatch.delitem(kr.KNOWN_UNREACHABLE, "gitlab")
    text = _problems_text(kr.check())
    assert "'gitlab'" in text and "no production driver" in text
    assert "newly registered with no driver" in text, (
        "with its entry gone the kind is routed generically — correct, and the "
        "reason it must be reported from metadata rather than hardcoded")


def test_reach_control_empty_reason_is_refused(monkeypatch):
    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "web", ("   ", "someone"))
    assert "carries no reason" in _problems_text(kr.check())


def test_reach_control_stale_exemption_for_an_unregistered_kind_is_refused(
        monkeypatch):
    """An exemption naming a kind nobody registers is dead weight, and lies."""
    monkeypatch.setitem(kr.KNOWN_UNREACHABLE, "nosuchkind", ("why", "who"))
    assert "STALE" in _problems_text(kr.check())


def test_reach_control_name_only_visitor_would_false_block(monkeypatch):
    """Pins the DEF-01 failure mode itself, not just its absence.

    Simulate the Name-only visitor the real code deliberately avoids. Every
    driver must contribute zero kinds under it — which is what proves inspecting
    `ast.Attribute` is load-bearing rather than defensive decoration.
    """
    def name_only(tree, srec_):
        values = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id.startswith("KIND_"):
                value = getattr(srec_, node.id, None)
                if isinstance(value, str) and value in srec_.REGISTERED_KINDS:
                    values.add(value)
        return values

    monkeypatch.setattr(kr, "kind_values", name_only)
    per_file = kr.derive_driver_kinds()
    assert per_file, "the driver files must still be found — only kinds collapse"
    assert all(not kinds for kinds in per_file.values()), (
        "a Name-only visitor must derive zero kinds from every driver; if this "
        "ever stops holding, the false-block control above has gone vacuous")

"""S12 — downstream trust repairs: the internal-citation axis (design-A22) and a
harvest that trusts the source rather than the marker (design-A23).

What this suite is FOR, stated once so a reader knows what a green run means:

  * A22 — a report whose citations are all internal is no longer invisible to
    every close-time SOURCE axis; a report citing nothing at all is no longer
    exempt; and a citation that fails moves the verdict instead of being an
    advisory style warning.
  * A23 — an INTERNAL claim entering the evidence register carries the address
    its own citation names, not the line of the report that quotes it; and a
    claim whose citation cannot be read as an address does not enter at all.

What a green run does NOT mean, asserted here rather than left to prose:

  * No source was opened. Whether a cited file, revision or issue still EXISTS is
    design-A10 and belongs to S13. `test_a_well_formed_citation_naming_a_nonexistent_
    path_is_accepted` pins that boundary positively.
  * No claim's six quality flags changed. S12 narrows WHICH claims are admitted;
    it does not change what an admission asserts.
  * A URL-cited claim is unaffected — which means unchecked, not vindicated.

Several tests below disable one arm and assert the check goes RED (the
`revert_*` tests). A check that stays green with its own arm disabled is
asserting the chain rather than testing it.
"""

from __future__ import annotations

import inspect
import io
import json
import os
import re
import sys
import tokenize
from pathlib import Path

import pytest


def _code_only(text: str) -> str:
    """`text` with comments and string literals removed.

    Every structural assertion below must read CODE, never prose. Without this,
    a docstring that NAMES the thing it promises not to reach — which several of
    these deliberately do, because the reason belongs beside the rule — would
    fail its own test. That is not a hypothetical: the first run of this suite
    failed exactly that way on three tests.
    """
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    except tokenize.TokenError:                       # pragma: no cover
        return text
    return " ".join(out)


def _module_code(name: str) -> str:
    return _code_only((HOOKS / name).read_text(encoding="utf-8"))

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

# The engine resolves the source layer under $CLAUDE_CONFIG_DIR. Point it at THIS
# tree, or the suite would pass or fail on whether the tree happens to be promoted
# — the same reasoning `test_citation_marker_registry.py` records for its mirrors.
os.environ.setdefault("CLAUDE_CONFIG_DIR", str(HOOKS.parent))

import _factcheck_engine as E       # noqa: E402
import _claim_anchors as CA         # noqa: E402
import _claim_engine as CE          # noqa: E402
import _claim_harvest as CH         # noqa: E402
import _claim_harvest_trigger as CHT  # noqa: E402


# --------------------------------------------------------------------------- #
# A2 — the collector
# --------------------------------------------------------------------------- #

def _corpus_file():
    """A real `_RESEARCH` file from the live corpus, or skip.

    The characterisation below must run against real prose, not a fixture: the
    property being pinned is that `_extract_cited_urls` behaves identically on the
    text it actually sees.
    """
    root = E._citation_projects_root()
    for candidate in sorted((root / "Thoughts").glob("*_RESEARCH.md"))[:40]:
        text = candidate.read_text(encoding="utf-8", errors="replace")
        if "http" in text:
            return candidate, text
    return None, None


def test_extract_cited_urls_is_unchanged_on_a_real_corpus_file():
    """Clause 2's protection for the highest-traffic path: every URL-cited marker
    in the corpus and the whole web prefetch depend on this function's bare-URL
    behaviour, and S12 does not rewrite, wrap or re-derive it. Stated as a rule
    rather than a marker count — a count goes stale as the corpus grows."""
    path, text = _corpus_file()
    if path is None:
        pytest.skip("no corpus _RESEARCH file with URLs available")
    urls = E._extract_cited_urls(text)
    assert urls, f"{path.name} was selected for having URLs"
    assert all(u.startswith(("http://", "https://")) for u in urls)
    assert len(urls) == len(set(urls)), "the collector dedupes, order-preserving"
    # It is a BARE url matcher, not a marker parser: it must still see a URL that
    # carries no citation marker at all.
    assert E._extract_cited_urls("see https://example.com/x for more") == [
        "https://example.com/x"]
    # And it must not be routed through the marker grammar, which would drop it.
    src = inspect.getsource(E._extract_cited_urls)
    assert "parse_citations" not in src and "classify_citation" not in src


def test_parse_citations_recognises_every_internal_locator_kind():
    text = (
        "[stated — local-file:Docs/x.md:12]\n"
        "[paraphrased — code:myrepo@abc123:src/a.py:10-20]\n"
        "[stated — linear:acme@v7:ENG-123]\n"
        "[stated — topic-CLAUDE:Personal/foo/CLAUDE.md:3]\n"
        "[stated — https://example.com/a]\n"
    )
    found = E.parse_citations(text)
    assert [c.locator for c in found] == [
        "local-file", "code", "linear", "topic-CLAUDE", "url"]
    assert all(c.well_formed is True for c in found), [c.reason for c in found]
    assert [c.line for c in found] == [1, 2, 3, 4, 5]


def test_parse_citations_defines_no_marker_shape_of_its_own():
    """Guiding Policy clause 1: the payload shape lives in the registry, and every
    consumer derives from it. A shape written into the collector would be a fourth
    vocabulary locus — the failure surfaced rule H2 already records."""
    for fn in (E.parse_citations, E.classify_citation, E._citation_payload_tables):
        src = _code_only(inspect.getsource(fn))
        for token in ("local-file", "topic-CLAUDE", "https?://"):
            assert token not in src, f"{fn.__name__} carries the literal {token!r}"


def test_a_payload_that_does_not_parse_is_reported_not_dropped():
    found = E.parse_citations("[stated — local-file:no-line-here]")
    assert len(found) == 1
    assert found[0].well_formed is False
    assert found[0].reason


def test_a_citation_naming_no_known_locator_class_is_reported():
    found = E.parse_citations("[stated — nonsense:foo]")
    assert [(c.locator, c.well_formed) for c in found] == [("unknown", False)]


def test_markers_that_name_no_source_are_not_citations():
    """Edge case (vi): three ACTIVE forms legitimately carry no source. They are
    claims that cite nothing — not malformed citations — and must not be treated
    as findings."""
    text = ("[inferred from internal sources] and [My assessment: hmm] "
            "and [unverified — not found]")
    assert E.parse_citations(text) == []


def test_a_well_formed_citation_naming_a_nonexistent_path_is_accepted():
    """THE S13 SCOPE BOUNDARY, pinned positively.

    Well-formedness is a PARSE, never an existence check. If this ever fails, S12
    has silently absorbed design-A10 — which belongs to S13 — and the two commitments
    can no longer be told apart."""
    found = E.parse_citations(
        "[stated — local-file:definitely/not/a/real/file-xyz.md:1]")
    assert len(found) == 1
    assert found[0].well_formed is True
    assert not Path("definitely/not/a/real/file-xyz.md").exists()


def test_linear_takes_the_optional_part_path_not_parse():
    """`locator_grammar.parse()` refuses kinds with optional parts and says so;
    `linear` is the one such kind, so its well-formedness comes from the
    `Locator` + `missing_required_parts` path instead."""
    grammar = E._load_locator_grammar()
    assert grammar is not None, "the source layer's grammar must load for this suite"
    with pytest.raises(Exception):
        grammar.parse("linear", "ENG-123")
    assert E.parse_citations("[stated — linear:acme@v7:ENG-123]")[0].well_formed is True


def test_well_formedness_is_unknown_rather_than_true_when_the_grammar_is_absent():
    """An unrunnable check must never read as a passed one. Tri-state, deliberately."""
    original = E._load_locator_grammar._cache if hasattr(
        E._load_locator_grammar, "_cache") else "unset"
    try:
        E._load_locator_grammar._cache = None
        got = E.parse_citations("[stated — local-file:Docs/x.md:12]")[0]
        assert got.well_formed is None
        assert "not checked" in (got.reason or "")
    finally:
        if original == "unset":
            delattr(E._load_locator_grammar, "_cache")
        else:
            E._load_locator_grammar._cache = original


# --------------------------------------------------------------------------- #
# A3 + A5 — the internal-citation axis
# --------------------------------------------------------------------------- #

def _verdict(status="PASS"):
    return {"status": status, "rounds": 1}


def _gate(citations, cited_urls, declaration=None, verdict=None, tmp_path=None):
    return E._run_internal_citation_gate(
        "research", citations, cited_urls, declaration,
        verdict if verdict is not None else _verdict(),
        str(tmp_path) if tmp_path else "/nonexistent-topic-dir")


def test_a_report_citing_nothing_at_all_folds_to_incomplete(tmp_path):
    verdict, _ = _gate([], [], tmp_path=tmp_path)
    assert verdict["status"] == "INCOMPLETE"
    assert verdict["internal_citations"] == "NONE_CITED"
    assert "cites no source of any kind" in verdict["reason"]


def test_a_url_only_report_is_untouched_by_this_axis(tmp_path):
    """Clause 2 at the axis level: a web-sourced report's verdict must be
    byte-identical after this slice."""
    before = _verdict()
    citations = E.parse_citations("[stated — https://example.com/a]")
    after, disclosure = _gate(citations, ["https://example.com/a"],
                              verdict=dict(before), tmp_path=tmp_path)
    assert after["status"] == "PASS"
    assert "reason" not in after
    assert disclosure is None


def test_an_internal_only_report_reaches_the_axis(tmp_path):
    """The headline defect: before S12 this report took NO source axis at all,
    because the sole collector is an http(s)-only matcher."""
    citations = E.parse_citations("[stated — local-file:Docs/x.md:12]")
    verdict, _ = _gate(citations, [], tmp_path=tmp_path)
    assert verdict["internal_citations_checked"] == 1


def test_a_malformed_internal_citation_folds_to_incomplete_naming_it(tmp_path):
    citations = E.parse_citations("[stated — local-file:no-line-here]")
    verdict, _ = _gate(citations, [], tmp_path=tmp_path)
    assert verdict["status"] == "INCOMPLETE"
    assert verdict["internal_citations_malformed"] == 1
    assert "could not be read as an address" in verdict["reason"]


def test_the_fold_is_incomplete_and_never_escalate(tmp_path):
    """Settled with the operator and not re-openable at implementation time: the
    locked Guiding Policy says degrade and say so. ESCALATE is reserved for a
    source proven fabricated-or-gone."""
    citations = E.parse_citations("[stated — local-file:broken]")
    verdict, _ = _gate(citations, [], tmp_path=tmp_path)
    assert verdict["status"] == "INCOMPLETE"
    assert verdict["status"] != "ESCALATE"


def test_a_more_severe_axis_still_dominates(tmp_path):
    """Severity-max, never assignment: a sibling axis that already escalated must
    not be weakened by this one."""
    citations = E.parse_citations("[stated — local-file:broken]")
    verdict, _ = _gate(citations, [], verdict=_verdict("ESCALATE"), tmp_path=tmp_path)
    assert verdict["status"] == "ESCALATE"


def test_the_axis_does_not_run_for_a_non_research_kind(tmp_path):
    verdict = _verdict()
    got, disclosure = E._run_internal_citation_gate(
        "plan", [], [], None, verdict, str(tmp_path))
    assert got["status"] == "PASS"
    assert disclosure is None


# -- the four-way distinction A5 must not collapse -------------------------- #

class _FakeSource:
    def __init__(self, kind, selectors):
        self.kind = kind
        self.selectors = tuple(selectors)


class _FakeResult:
    def __init__(self, admitted):
        self.admitted = admitted


class _FakeDeclaration:
    """A declaration stub exposing only the two methods the gate uses.

    Deliberately not a real `ScopeRecord`: the four-way branch is what is being
    tested, and a real record would drag path resolution into a test about
    control flow. The real record is exercised by `test_code_admission.py`.
    """

    def __init__(self, sources, admits=()):
        self._sources = list(sources)
        self._admits = set(admits)

    def sources_of_kind(self, kind):
        return tuple(s for s in self._sources if s.kind == kind)

    def check(self, kind, target):
        return _FakeResult(str(target) in self._admits)


def test_branch_declared_folds_to_nothing(tmp_path):
    """A well-formed, DECLARED citation is not examined further here — resolution
    is S13's. Pins the boundary from the gate's side."""
    decl = _FakeDeclaration([_FakeSource("linear", ["ENG"])], admits={"ENG-123"})
    citations = E.parse_citations("[stated — linear:acme@v7:ENG-123]")
    verdict, disclosure = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert verdict["status"] == "PASS"
    assert verdict["internal_citations"] == "OK"
    assert disclosure is None


def test_branch_undeclared_folds_to_incomplete_naming_the_source(tmp_path):
    decl = _FakeDeclaration([_FakeSource("linear", ["ENG"])], admits=set())
    citations = E.parse_citations("[stated — linear:acme@v7:OPS-9]")
    verdict, _ = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert verdict["status"] == "INCOMPLETE"
    assert verdict["internal_citations_undeclared"] == 1
    assert "outside your approved source list" in verdict["reason"]


def test_branch_unreachable_discloses_and_does_not_fold(tmp_path):
    """C4. A run that COULD NOT check must not be indistinguishable from one that
    checked and found nothing wrong — and must not be failed for it either."""
    citations = E.parse_citations("[stated — linear:acme@v7:ENG-123]")
    verdict, disclosure = _gate(citations, [], declaration=None, tmp_path=tmp_path)
    assert verdict["status"] == "PASS", "an unreachable list is not a failure"
    assert disclosure and "no approved source list" in disclosure


def test_a_declaration_naming_no_fitting_kind_is_not_comparable_not_undeclared(tmp_path):
    """The subtler half of the same rule: a declaration that exists but names no
    source of the citation's kind cannot answer the question either."""
    decl = _FakeDeclaration([_FakeSource("web", ["https://example.com"])])
    citations = E.parse_citations("[stated — linear:acme@v7:ENG-123]")
    verdict, disclosure = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert verdict["status"] == "PASS"
    assert disclosure and "names no source of kind" in disclosure


def test_code_identity_that_cannot_be_derived_discloses_rather_than_failing(tmp_path):
    """A `code:` pin names a repository IDENTITY and a declaration names a PATH.
    Where no identity can be derived, the honest answer is "not comparable" — a
    guess here would produce a false failure on a legitimate report."""
    decl = _FakeDeclaration([_FakeSource("code", [])])
    citations = E.parse_citations("[stated — code:myrepo@abc:src/a.py:1]")
    verdict, disclosure = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert verdict["status"] == "PASS"
    assert disclosure


def test_code_identity_matches_a_declared_checkout_basename(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    decl = _FakeDeclaration([_FakeSource("code", [str(repo)])])
    citations = E.parse_citations("[stated — code:myrepo@abc:src/a.py:1]")
    verdict, _ = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert verdict["status"] == "PASS"
    assert verdict["internal_citations"] == "OK"

    other = E.parse_citations("[stated — code:someoneelses@abc:src/a.py:1]")
    verdict2, _ = _gate(other, [], declaration=decl, tmp_path=tmp_path)
    assert verdict2["status"] == "INCOMPLETE"


def test_the_axis_never_crashes_a_run(tmp_path):
    """A18 fail-safe, and it must degrade to DISCLOSING rather than to silence."""
    class _Exploding:
        def sources_of_kind(self, kind):
            raise RuntimeError("boom")

    citations = E.parse_citations("[stated — linear:acme@v7:ENG-123]")
    verdict, disclosure = _gate(citations, [], declaration=_Exploding(),
                                tmp_path=tmp_path)
    assert verdict["status"] in ("PASS", "INCOMPLETE")
    assert disclosure


# --------------------------------------------------------------------------- #
# A4 — the declaration's reach
# --------------------------------------------------------------------------- #

def test_resolve_web_admission_names_its_third_value():
    """Cockburn Abstraction Test: the function no longer does only what its name
    says, so the value carrying that extra responsibility is NAMED."""
    assert E.WebAdmission._fields == ("web_scope", "store", "declaration")


def test_a4_introduces_no_third_cycle_lookup():
    """The single-locus rule this plan preaches in its own Guiding Policy. A fresh
    resolver would be the THIRD reader of this cycle key — `declared_read.
    _resolve_scope_from_cycle` is already the second."""
    src = (HOOKS / "_factcheck_engine.py").read_text(encoding="utf-8")
    call_sites = [ln for ln in src.splitlines()
                  if "_resolve_research_cycle_id(" in ln and "def " not in ln]
    # TWO, not one. The engine ALREADY had two call sites before S12 (verified
    # against the live tree and the pre-S12 backup), so asserting `== 1` would
    # have been asserting a premise the codebase never held. What A4 promises is
    # that it ADDS none — the widening reaches the unfiltered record from the read
    # `_resolve_web_admission` already performs, rather than resolving the cycle
    # again. If this number rises, that promise has been broken.
    assert len(call_sites) == 2, (
        "the cycle resolver gained a call site; A4's whole point is that it does "
        f"not: {call_sites}")


def test_the_web_filtered_scope_keeps_its_meaning():
    """Clause 2: the value the web ingest receives is semantically identical. A
    record naming only `code` must still yield NO web scope — reading it as
    'web is bounded to nothing' would refuse every citation in a run whose person
    never mentioned the web."""
    src = inspect.getsource(E._resolve_web_admission)
    assert "sources_of_kind(mods[\"scope_record\"].KIND_WEB)" in src
    # …and the declaration is captured BEFORE that filter, not derived from it.
    assert src.index("declaration = candidate") < src.index("if candidate.sources_of_kind")


# --------------------------------------------------------------------------- #
# A6 — the anchor vocabulary
# --------------------------------------------------------------------------- #

def test_code_and_linear_have_anchor_forms():
    assert CE.SourceType.CODE in CA.ANCHOR_SCHEMES
    assert CE.SourceType.LINEAR in CA.ANCHOR_SCHEMES


def test_a_citation_anchors_to_its_own_address():
    a = CA.anchor_from_citation("code", {"repo": "myrepo", "rev": "abc123",
                                         "path": "src/a.py", "lines": "10-20"})
    assert a.locator == "myrepo@abc123:src/a.py:10-20"
    b = CA.anchor_from_citation("linear", {"workspace": "acme", "version": "v7",
                                           "issue": "ENG-123"})
    assert b.locator == "acme@v7:ENG-123"
    c = CA.anchor_from_citation("local-file", {"path": "Docs/x.md", "line": "12"})
    assert c.locator == "Docs/x.md:12"


def test_a_line_range_anchors_without_coercion():
    """The Marker Contract's Edge 4 permits a line RANGE. The resolver's numeric
    coercion is retained for a Stage-2 row and relaxed for a citation, which
    WIDENS what resolves and changes nothing for a numeric input."""
    assert CA.anchor_from_citation(
        "local-file", {"path": "x.md", "line": "120-135"}).locator == "x.md:120-135"
    src = CE.Source(CE.SourceType.LOCAL_FILE, "a.md", "…")
    assert CA.resolve(src, {"source_line": 36}).locator == "a.md:36"


def test_a_citation_kind_with_no_anchor_form_refuses():
    with pytest.raises(CE.SchemaError):
        CA.anchor_from_citation("url", {"url": "https://example.com"})


def test_adding_a_source_type_cannot_narrow_an_existing_consumer():
    """A6's safety property, verified rather than assumed: nothing in production
    matches exhaustively over `SourceType`."""
    for module in ("_claim_persist.py", "_claim_register.py", "_claim_state.py",
                   "_claim_ledger.py", "_claim_engine.py"):
        code = _module_code(module)
        assert "match source" not in code
        # An exhaustive if/elif over every member would break on a new one; the
        # engine's own Gate-1 anchor is that `_build_anchor` honours any type
        # carrying a locator.
        assert code.count("SourceType . TRANSCRIPT") <= 2, module


# --------------------------------------------------------------------------- #
# A7 — the harvest
# --------------------------------------------------------------------------- #

def _item(marker, payload, text="A claim.", line=7):
    return {"text": text, "url": None, "line": line,
            "payload": payload, "marker": marker}


def test_an_internal_claim_anchors_to_its_source_not_to_the_quoting_line():
    """design-A23's whole point. Before S12 this claim was anchored to the
    research file's own line — the address of the QUOTATION."""
    anchor, refusal = CH.citation_admission(_item("stated", "local-file:Docs/x.md:12"))
    assert refusal is None
    assert anchor is not None
    assert anchor.locator == "Docs/x.md:12"
    assert "12" != "7", "the citation's line, not the item's"


def test_a_url_cited_claims_anchor_is_left_exactly_as_it_was():
    """The clause-2 protection A2/A3/A4 each carry, extended to the harvest:
    re-addressing the highest-traffic path is a real data change with no basis in
    design-A23, which scopes itself to an INTERNAL claim."""
    anchor, refusal = CH.citation_admission(
        _item("stated", "https://example.com/a"))
    assert refusal is None
    assert anchor is None, "None means: keep the anchor this claim has always had"


def test_an_unparseable_citation_is_refused_with_a_reason():
    anchor, refusal = CH.citation_admission(_item("stated", "local-file:no-line"))
    assert anchor is None
    assert refusal and "does not match" in refusal


def test_the_refusal_condition_is_exactly_one():
    """A7 evaluates ONE condition — the address does not parse. A second (naming a
    source outside the declaration) was specified and dropped as unbuildable: this
    process is handed only a file path and cannot reach the manifest cycle."""
    for module in ("_claim_harvest.py", "_claim_harvest_trigger.py"):
        code = _module_code(module)
        assert "scope_record" not in code, module
        assert "ScopeRecord" not in code, module
        assert "_resolve_research_cycle_id" not in code, module


def test_the_harvest_never_consults_the_admission_record_store():
    """Guiding Policy clause 5. `get_evidence` has no production caller and its
    writer/reader identities are known-divergent (spine Q17); the `(run_id, pin)`
    re-key that would repair it is descoped D4's and owned by no slice."""
    for module in ("_claim_harvest.py", "_claim_harvest_trigger.py", "_claim_engine.py"):
        code = _module_code(module)
        for token in ("admission_record", "get_evidence", "AdmissionRecordStore"):
            assert token not in code, f"{module} reaches for {token}"


def test_the_harvest_does_not_treat_is_verified_as_evidence_a_source_was_checked():
    """`is_verified()` reads a PROSE Status line and is deliberately decoupled from
    the `fc_cycles` frontmatter where the engine records its verdict
    (`_claim_harvest.py:11-13`). So a VERIFIED line implies nothing about whether
    any source was examined, and the admission decision must not consult it."""
    assert "is_verified" not in _code_only(inspect.getsource(CH.citation_admission))


def test_the_flag_set_is_byte_identical_before_and_after():
    """This action changes ADMISSION, not values. An admitted claim still records
    every criterion as met — exactly as before S12."""
    src = inspect.getsource(CH._harvest_one)
    assert "ClaimFlags.from_dict({c: True for c in CRITERIA})" in src
    assert "faithfulness" not in _code_only(src), (
        "a flag was re-valued; setting `faithfulness: false` would empty the "
        "register's grounding set — see _claim_state.py:16,81-83")


def _tiny_register(tmp_path, text):
    """Drive a real harvest through the real ledger + register."""
    from _claim_ledger import ClaimLedger
    from _claim_register import EvidenceRegister
    research = tmp_path / "t_RESEARCH.md"
    research.write_text(text, encoding="utf-8")
    ledger = ClaimLedger(tmp_path / "claims.ledger.jsonl")
    register = EvidenceRegister(tmp_path / "_evidence-register.md", "t", ledger=ledger)
    register.ensure_exists()
    result = CH.harvest(research, ledger=ledger, register=register,
                        checked_at="2026-09-07")
    return result, register


_VERIFIED = "**Status:** ✅ VERIFIED via /double-check — PASS\n\n"


def test_both_harvest_entry_points_route_through_one_admission(tmp_path):
    """`harvest` and `harvest_on_verify` are separate entry points; both must show
    the new behaviour, because both route through `_harvest_one`."""
    for fn in (CH.harvest, CHT.on_research_write):
        src = inspect.getsource(fn)
        assert "citation_admission" in src, fn.__name__


def test_a_refused_claim_is_absent_from_harvested_and_present_in_excluded(tmp_path):
    result, register = _tiny_register(
        tmp_path,
        _VERIFIED
        + "The sky is blue. [stated — local-file:Docs/x.md:12]\n"
        + "The sea is green. [stated — local-file:broken-no-line]\n")
    assert len(result["harvested"]) == 1, "only the readable citation is admitted"
    refused = [it for it in result["excluded"] if it.get("reason")]
    assert len(refused) == 1
    assert "sea is green" in refused[0]["text"]
    assert refused[0]["reason"]


def test_the_registers_grounding_invariant_holds(tmp_path):
    """The gate that would have caught the rejected `faithfulness: false` design:
    everything in the register is grounding truth."""
    result, register = _tiny_register(
        tmp_path,
        _VERIFIED
        + "The sky is blue. [stated — local-file:Docs/x.md:12]\n"
        + "Water is wet. [stated — https://example.com/a]\n")
    assert len(result["harvested"]) == 2
    assert len(register.grounding_entries()) == len(result["harvested"])


def test_a_harvested_internal_claim_carries_its_sources_address(tmp_path):
    result, register = _tiny_register(
        tmp_path, _VERIFIED + "The sky is blue. [stated — local-file:Docs/x.md:12]\n")
    entries = register.grounding_entries()
    assert len(entries) == 1
    rendered = json.dumps(entries[0], default=str)
    assert "Docs/x.md:12" in rendered
    assert "t_RESEARCH.md" not in rendered, (
        "the claim is still addressed to the report that quotes it")


def test_a_harvested_url_claim_still_carries_the_report_anchor(tmp_path):
    """Unaffected means unchecked, not vindicated — and it means UNCHANGED."""
    result, register = _tiny_register(
        tmp_path, _VERIFIED + "Water is wet. [stated — https://example.com/a]\n")
    rendered = json.dumps(register.grounding_entries()[0], default=str)
    assert "t_RESEARCH.md" in rendered


def test_the_refusal_reaches_the_operator(tmp_path):
    """Until S12 `format_summary` printed `harvested` and `deferred_paraphrased`
    only, so "surfaced, never dropped silently" was true only inside a return
    dict nothing rendered."""
    summary = CHT.format_summary({
        "skipped": False, "slug": "t", "register": "/tmp/_evidence-register.md",
        "harvested": ["C1"], "deferred_paraphrased": [],
        "excluded": [{"text": "The sea is green.", "line": 4, "marker": "stated",
                      "reason": "payload does not match the local-file shape"}],
    })
    assert "NOT harvested" in summary
    assert "The sea is green." in summary
    assert "does not match" in summary


def test_the_pre_existing_editorial_exclusions_are_not_re_rendered():
    """`[My assessment]` / `[unverified]` exclusions carry no `reason` and were
    never printed; S12 must not start printing them as refusals."""
    summary = CHT.format_summary({
        "skipped": False, "slug": "t", "register": "/tmp/r.md",
        "harvested": ["C1"], "deferred_paraphrased": [],
        "excluded": [{"text": "An opinion.", "line": 2, "marker": "my assessment"}],
    })
    assert "NOT harvested" not in summary


# --------------------------------------------------------------------------- #
# Revert-checks — each arm must flip its own check RED when disabled.
#
# A check that stays green with its arm disabled is asserting the chain rather
# than testing it, which is the shape V1-obs2 records.
# --------------------------------------------------------------------------- #

def test_revert_the_internal_collector(tmp_path):
    """Disable the collector's recognition of internal markers → the axis stops
    seeing them and the internal-only report goes back to being invisible."""
    original = E._citation_payload_tables._cache if hasattr(
        E._citation_payload_tables, "_cache") else "unset"
    try:
        E._citation_payload_tables._cache = ([], [], {}, {})
        citations = E.parse_citations("[stated — local-file:Docs/x.md:12]")
        verdict, _ = _gate(citations, [], tmp_path=tmp_path)
        assert verdict.get("internal_citations_checked") == 0, (
            "the arm is disabled and the axis STILL reports a checked citation — "
            "so the assertion is not measuring this arm")
    finally:
        if original == "unset":
            delattr(E._citation_payload_tables, "_cache")
        else:
            E._citation_payload_tables._cache = original


def test_revert_the_zero_citation_fold(tmp_path):
    """With a citation present the zero-citation branch must NOT fire — otherwise
    the branch is firing on something other than its own condition."""
    citations = E.parse_citations("[stated — local-file:Docs/x.md:12]")
    verdict, _ = _gate(citations, [], tmp_path=tmp_path)
    assert verdict.get("internal_citations") != "NONE_CITED"


def test_revert_the_malformed_fold(tmp_path):
    """A well-formed citation must not fold. If this fails the fold is firing on
    presence rather than on malformedness."""
    citations = E.parse_citations("[stated — local-file:Docs/x.md:12]")
    verdict, _ = _gate(citations, [], tmp_path=tmp_path)
    assert verdict["status"] == "PASS"


def test_revert_the_undeclared_fold(tmp_path):
    """An ADMITTED citation must not fold — otherwise the undeclared branch is
    firing on every internal citation rather than on the declaration check."""
    decl = _FakeDeclaration([_FakeSource("linear", ["ENG"])], admits={"ENG-123"})
    citations = E.parse_citations("[stated — linear:acme@v7:ENG-123]")
    verdict, _ = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert verdict["status"] == "PASS"


def test_revert_the_disclosure_path(tmp_path):
    """When everything WAS comparable the run must disclose nothing — otherwise
    the disclosure is unconditional and says nothing about this run."""
    decl = _FakeDeclaration([_FakeSource("linear", ["ENG"])], admits={"ENG-123"})
    citations = E.parse_citations("[stated — linear:acme@v7:ENG-123]")
    _verd, disclosure = _gate(citations, [], declaration=decl, tmp_path=tmp_path)
    assert disclosure is None


def test_revert_the_harvest_anchor():
    """With the internal anchor kinds emptied, an internal claim falls back to the
    report anchor — which is exactly the pre-S12 defect, and proves the passing
    assertion above is measuring the anchor arm."""
    original = CH._internal_anchor_kinds
    try:
        CH._internal_anchor_kinds = lambda: set()
        anchor, refusal = CH.citation_admission(
            _item("stated", "local-file:Docs/x.md:12"))
        assert refusal is None
        assert anchor is None, "arm disabled → no source anchor, as before S12"
    finally:
        CH._internal_anchor_kinds = original


def test_revert_the_harvest_refusal():
    """A parseable citation must NOT be refused — otherwise the refusal is firing
    on every claim rather than on an unreadable address."""
    _anchor, refusal = CH.citation_admission(_item("stated", "local-file:Docs/x.md:12"))
    assert refusal is None
    _anchor2, refusal2 = CH.citation_admission(_item("stated", "https://example.com/a"))
    assert refusal2 is None


# --------------------------------------------------------------------------- #
# Scope, as MEASURED against the live corpus at implementation time.
#
# Every assertion in this block exists because a corpus measurement contradicted a
# first implementation. They are the difference between an axis that fires on the
# cases design-A22 names and one that downgrades nearly every research file, so
# they are pinned rather than left to the prose that records them.
# --------------------------------------------------------------------------- #

def test_a_free_prose_payload_is_reported_but_never_folds(tmp_path):
    """`[paraphrased — web search summary citing several papers]` names no locator
    class. There are **1270** such payloads in the live corpus, so folding on them
    would downgrade very nearly every research file. Design-A22 scopes the
    verdict-affecting finding to an INTERNAL citation; this is marker hygiene and
    belongs to the style checker."""
    citations = E.parse_citations(
        "[paraphrased — web search summary citing multiple 2024-2025 papers]")
    assert citations[0].source_class == "unknown"
    verdict, _ = _gate(citations, [], tmp_path=tmp_path)
    assert verdict["status"] == "PASS"
    assert verdict.get("citations_unrecognised") == 1, (
        "it must still be COUNTED — reported, never folded, never invisible")


def test_a_url_with_a_trailing_note_is_well_formed(tmp_path):
    """A real corpus shape: `[stated — https://x, citing the SummaC results]`. The
    address IS present and the bare-URL collector already sees it, so refusing the
    citation over a trailing note would be a false failure."""
    got = E.parse_citations(
        "[stated — https://eugeneyan.com/writing/llm-evaluators/, citing SummaC]")[0]
    assert got.locator == "url"
    assert got.well_formed is True
    assert got.parts["url"].startswith("https://eugeneyan.com")


def test_a_web_citation_is_not_this_axiss_business(tmp_path):
    """The two URL axes already own that half; this axis exists because they could
    not see the internal one."""
    citations = E.parse_citations("[stated — not-a-url-at-all]")
    verdict, _ = _gate(citations, ["https://example.com/x"], tmp_path=tmp_path)
    assert verdict["status"] == "PASS"


def test_the_harvest_leaves_every_non_internal_citation_exactly_as_it_was():
    """Measured: refusing every unparseable payload refused a substantial share of
    the corpus's harvestable claims, none of them internal. Scoped to internal, the
    live corpus yields ZERO newly-refused claims — which is what the plan
    predicted, and which is the figure this test keeps true."""
    for payload in ("https://example.com/a",            # a URL
                    "web search summary citing papers",  # free prose
                    "",                                  # `[stated]` in prose
                    None):                               # a pre-S12 item dict
        anchor, refusal = CH.citation_admission(_item("stated", payload))
        assert refusal is None, f"{payload!r} was refused"
        assert anchor is None, f"{payload!r} was re-anchored"


# --------------------------------------------------------------------------- #
# Real-flow proof — NOT fixtures only.
#
# Every test above hands the gate a citation list it built by hand. That is the
# shape V1-obs2 records as a defect: a proof invisible to the thing production
# actually derives. These three drive `factcheck_run` itself over a REAL report on
# disk, with a REAL approved declaration hoisted onto a REAL manifest cycle by
# `r0_intake` — so the report text, the citation parse and the declaration lookup
# are all production's, not the test's.
#
# The checker panel is stubbed, and only the checker panel: what is being proven
# is the close-time source axis, not the model's verdict.
# --------------------------------------------------------------------------- #

def _real_flow(tmp_path, monkeypatch, body, declaration=None, sid="s12-real"):
    import importlib.util

    config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    sys.path.insert(0, str(config_dir / "skills"))

    def _by_path(name, path):
        spec = importlib.util.spec_from_file_location(name, str(path))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    rp = _by_path("_s12_research_pipeline", config_dir / "hooks" / "research_pipeline.py")
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_path / "rp"))

    draft = tmp_path / "Thoughts" / "real_RESEARCH.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    draft.write_text(body, encoding="utf-8")

    payload = {"research_file_path": str(draft), "caller_skill": "/research"}
    if declaration is not None:
        payload["scope_record"] = json.loads(declaration.to_json())
    rp.cmd_advance(sid, "r0_intake", payload, cycle_id="default")

    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    from unittest import mock
    with mock.patch.object(E, "_run_coverage_axis_gate",
                           side_effect=lambda rp_, cs, v, td, kind, **kw: v), \
            mock.patch.object(E, "_write_research_frontmatter_for_terminal"), \
            mock.patch.object(E, "_log_factcheck_run"):
        return E.factcheck_run(
            str(state), str(draft), "research", sid,
            debounce_seconds=0, models=["sonnet"],
            _checker_fn=lambda *a, **k: "reasoning\nVERDICT: PASS",
            proj="p", topic="t")


def test_real_flow_a_well_formed_internal_citation_does_not_fold(tmp_path, monkeypatch):
    result = _real_flow(
        tmp_path, monkeypatch,
        "# Report\n\nThe sky is blue. [stated — local-file:Docs/x.md:12]\n")
    assert result["status"] == "PASS"
    assert result.get("internal_citations_checked") == 1


def test_real_flow_a_malformed_internal_citation_folds_to_incomplete(tmp_path, monkeypatch):
    result = _real_flow(
        tmp_path, monkeypatch,
        "# Report\n\nThe sky is blue. [stated — local-file:broken-no-line]\n",
        sid="s12-real-bad")
    assert result["status"] == "INCOMPLETE"
    assert "could not be read as an address" in result["reason"]


def test_real_flow_a_report_citing_nothing_folds_to_incomplete(tmp_path, monkeypatch):
    result = _real_flow(
        tmp_path, monkeypatch, "# Report\n\nAn assertion with no citation.\n",
        sid="s12-real-none")
    assert result["status"] == "INCOMPLETE"
    assert "cites no source of any kind" in result["reason"]


def test_real_flow_an_undeclared_citation_folds_against_a_real_declaration(
        tmp_path, monkeypatch):
    """The A4→A5 chain end to end: a REAL `ScopeRecord`, hoisted onto a REAL cycle
    by `r0_intake`, reached at close time through the widened resolver, and used to
    refuse a citation naming a source outside it."""
    config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
    sys.path.insert(0, str(config_dir / "skills"))
    from research import source_picker as spk

    declared = tmp_path / "declared_library"
    declared.mkdir()
    (declared / "inside.md").write_text("x", encoding="utf-8")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "outside.md").write_text("y", encoding="utf-8")

    record = spk.build_record(
        spk.Selection(bounds=(("knowledge_library",
                               spk.Bound(selectors=(str(declared),))),)),
        route=spk.ROUTE_INTERNAL_KB)

    result = _real_flow(
        tmp_path, monkeypatch,
        f"# Report\n\nA claim. [stated — local-file:{outside / 'outside.md'}:1]\n",
        declaration=record, sid="s12-real-undeclared")
    assert result["status"] == "INCOMPLETE"
    assert "outside your approved source list" in result["reason"]

    inside = _real_flow(
        tmp_path / "second", monkeypatch,
        f"# Report\n\nA claim. [stated — local-file:{declared / 'inside.md'}:1]\n",
        declaration=record, sid="s12-real-declared")
    assert inside["status"] == "PASS", (
        "the declared citation folded too — the check is refusing everything "
        "rather than measuring the declaration")


# --------------------------------------------------------------------------- #
# A8 — the stated ceiling is present where a reader will meet it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("module,phrases", [
    ("_factcheck_engine.py", ["never opens a source", "belongs to S13"]),
    ("_claim_anchors.py", ["does NOT make it true"]),
    ("_claim_harvest.py", ["does not change what an admission asserts"]),
])
def test_the_module_states_what_it_does_not_deliver(module, phrases):
    text = (HOOKS / module).read_text(encoding="utf-8")
    for phrase in phrases:
        assert phrase in text, f"{module} does not state: {phrase}"


def test_no_surface_claims_containment_or_that_a_source_was_opened():
    for module in ("_factcheck_engine.py", "_claim_harvest.py", "_claim_anchors.py"):
        text = (HOOKS / module).read_text(encoding="utf-8")
        for banned in ("guarantees the source", "proves the source",
                       "cannot be read outside"):
            assert banned not in text, f"{module} over-claims: {banned}"

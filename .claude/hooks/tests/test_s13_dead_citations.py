#!/usr/bin/env python3
"""S13 — internal-citation resolution: marker emission + dead internal citations.

Covers Coherent Actions A1 (resolution domain module), A2 (wiring into the
existing broken-link accounting), A3 (the three-valued distinction), A4 (the
record on both routes), A5 (pinning the three properties that already hold) and
A8 (reach).

The load-bearing tests here are the ones that assert a NEGATIVE:
  * `misrooted` must never count as broken (A3) — the five-in-six false-failure
    this slice exists to avoid.
  * `unresolved` must never count as broken and never annotate a report (A1).
  * a partial writer must never destroy a field it does not own (A4 constraint b).
  * marker emission must not become source-conditional or checkpoint-dependent,
    and no per-source-kind rate may appear (A5 / design-A15, A28).
"""
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import _citation_resolve as cr                       # noqa: E402
import research_linkcheck as rlc                     # noqa: E402
import _factcheck_engine as eng                      # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _cit(raw, kind="stated", line=1):
    """One ParsedCitation, produced by the ENGINE's own classifier.

    Never hand-built: the point of routing through `classify_citation` is that
    these tests exercise the same classification production does (rule H2).
    """
    return eng.classify_citation(kind, raw, line)


def _workspace(tmp_path):
    """A miniature workspace: a root, a project inside it, and a report."""
    ws = tmp_path / "Projects"
    proj = ws / "Proj"
    (proj / "notes").mkdir(parents=True)
    (ws / "Thoughts").mkdir(parents=True)
    (ws / "Thoughts" / "real.md").write_text("live target\n")
    (proj / "notes" / "local.md").write_text("misrooted target\n")
    return ws, proj, proj / "demo_RESEARCH.md"


# --------------------------------------------------------------------------- #
# A1 — the resolution domain module
# --------------------------------------------------------------------------- #
def test_a1_module_self_test_passes_standalone():
    """The module is testable without pytest, per the _citation_reconcile
    precedent it clones."""
    out = subprocess.run(
        [sys.executable, str(HOOKS / "_citation_resolve.py"), "--self-test"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout


def test_a1_resolves_the_four_outcomes(tmp_path):
    ws, proj, report = _workspace(tmp_path)

    def R(raw):
        return cr.resolve_citation(
            _cit(raw), exists=os.path.exists, workspace_root=ws,
            citing_file=report)

    assert R("local-file:Thoughts/real.md:1").outcome == cr.LIVE
    assert R("local-file:notes/local.md:1").outcome == cr.MISROOTED
    assert R("local-file:gone/nothing.md:1").outcome == cr.DEAD
    assert R("linear:ws@v1:ISS-1").outcome == cr.UNRESOLVED


def test_a1_misrooted_names_the_root_the_target_actually_resolves_under(tmp_path):
    """A person cannot act on 'it is somewhere else' — the annotation must say
    where."""
    ws, proj, report = _workspace(tmp_path)
    res = cr.resolve_citation(
        _cit("local-file:notes/local.md:1"), exists=os.path.exists,
        workspace_root=ws, citing_file=report)
    assert res.outcome == cr.MISROOTED
    assert res.resolved_root == str(proj)


def test_a1_never_returns_dead_when_the_check_could_not_run(tmp_path):
    """The central guard rail: an unrunnable check is never a passed check, and
    is never a failed one either."""
    ws, _proj, report = _workspace(tmp_path)

    def R(cit, **kw):
        return cr.resolve_citation(cit, exists=os.path.exists,
                                   workspace_root=ws, citing_file=report, **kw)

    # linear — no credentials, ever.
    assert R(_cit("linear:ws@v1:ISS-1")).outcome == cr.UNRESOLVED
    # code — identity maps to no checkout.
    code = _cit("code:some/repo@abc1234:src/x.py:1-2")
    assert R(code).outcome == cr.UNRESOLVED
    # code — checkout known but path absent: still not dead. Existence alone
    # cannot separate a purged revision from a checkout at another revision.
    r = R(code, checkout_for=lambda _repo: str(tmp_path / "nowhere"))
    assert r.outcome == cr.UNRESOLVED and not r.is_broken
    # grammar unavailable (well_formed None) — S12's tri-state maps here.
    unk = _cit("local-file:gone/nothing.md:1")._replace(well_formed=None)
    assert R(unk).outcome == cr.UNRESOLVED

    for outcome in (cr.UNRESOLVED, cr.MISROOTED, cr.LIVE):
        assert outcome not in cr.BROKEN_OUTCOMES
    assert cr.BROKEN_OUTCOMES == {cr.DEAD}


def test_a1_performs_no_io_of_its_own(tmp_path):
    """Domain-layer contract: existence is injected. If the module did its own
    I/O, a predicate that answers 'nothing exists' could not force DEAD on a
    path that really is present."""
    ws, _proj, report = _workspace(tmp_path)
    res = cr.resolve_citation(
        _cit("local-file:Thoughts/real.md:1"),
        exists=lambda _p: False,          # lies: the file genuinely exists
        workspace_root=ws, citing_file=report)
    assert res.outcome == cr.DEAD, (
        "resolution consulted the real filesystem instead of the injected seam")


def test_a1_handles_the_shapes_the_real_corpus_contains(tmp_path):
    """Six of the corpus's ten internal citations carry NO line part, and one
    real path contains a space. Both must still resolve."""
    ws, proj, report = _workspace(tmp_path)
    (proj / "Onboarding v2").mkdir()
    (proj / "Onboarding v2" / "t.md").write_text("x\n")

    noline = _cit("local-file:notes/local.md")
    assert noline.well_formed is False, "precondition: this payload does not parse"
    r = cr.resolve_citation(noline, exists=os.path.exists, workspace_root=ws,
                            citing_file=report)
    assert r.outcome == cr.MISROOTED, "a line-less payload must still resolve"

    spaced = _cit("local-file:Onboarding v2/t.md")
    r2 = cr.resolve_citation(spaced, exists=os.path.exists, workspace_root=ws,
                             citing_file=report)
    assert r2.outcome == cr.MISROOTED


def test_a1_counts_reports_all_four_outcomes_including_zeros():
    """An absent key and a zero read differently. This slice exists because a
    silent zero was mistaken for a clean result."""
    tally = cr.counts([])
    assert set(tally) == set(cr.OUTCOMES)
    assert all(v == 0 for v in tally.values())


# --------------------------------------------------------------------------- #
# A2 — wiring into the EXISTING accounting
# --------------------------------------------------------------------------- #
def _run(report, ws, monkeypatch, head=None):
    monkeypatch.setattr(rlc, "WORKSPACE_ROOT", str(ws))
    return rlc.linkcheck_file(str(report),
                              _head_check_fn=head or (lambda _u: (200, None)))


def test_a2_a_dead_internal_citation_is_counted_and_downgraded(tmp_path,
                                                               monkeypatch):
    ws, _proj, report = _workspace(tmp_path)
    report.write_text("A [stated — local-file:gone/nothing.md:1].\n")
    res = _run(report, ws, monkeypatch)

    assert res["broken_count"] == 1, "a dead citation must reach broken_count"
    body = report.read_text()
    assert "[unverified — source not found — local-file:gone/nothing.md:1]" in body
    assert "[stated —" not in body, "the dead marker must not survive"


def test_a2_a_live_internal_citation_is_untouched(tmp_path, monkeypatch):
    ws, _proj, report = _workspace(tmp_path)
    original = "A [stated — local-file:Thoughts/real.md:1].\n"
    report.write_text(original)
    res = _run(report, ws, monkeypatch)
    assert res["broken_count"] == 0
    assert report.read_text() == original


def test_a2_is_idempotent_across_reruns(tmp_path, monkeypatch):
    ws, _proj, report = _workspace(tmp_path)
    report.write_text(
        "dead [stated — local-file:gone/x.md:1] "
        "mis [stated — local-file:notes/local.md:1]\n")
    _run(report, ws, monkeypatch)
    first = report.read_text()
    _run(report, ws, monkeypatch)
    assert report.read_text() == first, "second run mutated the report again"
    assert first.count("⚠ MISROOTED") == 1


def test_a2_a_dead_citation_counts_once_while_a_dead_url_keeps_counting(
        tmp_path, monkeypatch):
    """The asymmetry between the two, pinned because it is REAL and was not
    disclosed by the plan's "counted exactly as a dead URL is".

    A dead URL keeps incrementing `broken_count` on every re-run: its `⚠ BROKEN`
    tag stays attached to a link that is still there and still dead, and the
    recheck-window skip branch counts it again. A dead CITATION counts once: the
    marker is REWRITTEN to `[unverified — …]`, which carries no payload, so the
    citation scanner cannot see it any more.

    That is the correct behaviour rather than a leak — after the downgrade the
    claim no longer asserts a source, so there is no longer a broken source
    reference to count — but it means "exactly as a dead URL is" holds on the
    FIRST run only. Pinned so the difference is deliberate, and so a later change
    that starts re-counting downgraded citations fails loudly.
    """
    ws, _proj, report = _workspace(tmp_path)

    report.write_text("A [stated — local-file:gone/x.md:1].\n")
    first = _run(report, ws, monkeypatch)["broken_count"]
    second = _run(report, ws, monkeypatch)["broken_count"]
    assert (first, second) == (1, 0), (first, second)

    url_report = report.parent / "u_RESEARCH.md"
    url_report.write_text("A [text](https://dead.example/x).\n")
    monkeypatch.setattr(rlc, "WORKSPACE_ROOT", str(ws))
    dead = lambda _u: (404, "nf")                              # noqa: E731
    u1 = rlc.linkcheck_file(str(url_report), _head_check_fn=dead)["broken_count"]
    u2 = rlc.linkcheck_file(str(url_report), _head_check_fn=dead)["broken_count"]
    assert (u1, u2) == (1, 1), (u1, u2)


def test_a2_routes_through_the_engines_single_classifier(monkeypatch):
    """Rule H2: one answer to 'recognise a citation', not two. If the engine
    cannot be imported the check degrades to not-run rather than inventing its
    own parser."""
    monkeypatch.setitem(sys.modules, "_factcheck_engine", None)
    text, tally, details = rlc.apply_internal_citations(
        "[stated — local-file:x.md:1]", "/tmp/r_RESEARCH.md")
    assert tally is None, "an unloadable vocabulary must report not-run"
    assert details == []
    assert text == "[stated — local-file:x.md:1]", "body must be untouched"


# --------------------------------------------------------------------------- #
# A3 — the three-valued distinction (the correctness keystone)
# --------------------------------------------------------------------------- #
def test_a3_misrooted_is_annotated_but_neither_counted_nor_downgraded(
        tmp_path, monkeypatch):
    """THE load-bearing test. Applied to the real corpus a two-valued check
    reports 6 dead where 1 is dead; this is the assertion that stops it."""
    ws, proj, report = _workspace(tmp_path)
    report.write_text("A [stated — local-file:notes/local.md:1].\n")
    res = _run(report, ws, monkeypatch)

    assert res["broken_count"] == 0, "misrooted must NEVER count as broken"
    body = report.read_text()
    assert "[stated — local-file:notes/local.md:1]" in body, (
        "the marker must be left UNALTERED — the source exists")
    assert "unverified" not in body, "misrooted must never downgrade the label"
    assert f"⚠ MISROOTED (resolves under {proj})" in body


def test_a3_broken_has_a_single_definition(tmp_path, monkeypatch):
    """"Which outcomes count as broken" must have ONE home.

    Found by a revert-check arm: flipping `BROKEN_OUTCOMES` to include
    `misrooted` left `broken_count` unmoved, because the counter summed the DEAD
    tally directly instead of consulting the constant. Two answers to one
    question is the drift shape this topic keeps recording against itself, so the
    counting path now routes through the constant and this test pins it.
    """
    ws, _proj, report = _workspace(tmp_path)
    report.write_text("mis [stated — local-file:notes/local.md:1]\n")

    assert _run(report, ws, monkeypatch)["broken_count"] == 0

    monkeypatch.setattr(rlc, "BROKEN_OUTCOMES",
                        frozenset({cr.DEAD, cr.MISROOTED}))
    report.write_text("mis [stated — local-file:notes/local.md:1]\n")
    assert _run(report, ws, monkeypatch)["broken_count"] == 1, (
        "the counting path ignores BROKEN_OUTCOMES — 'broken' is defined twice")


def test_a3_the_misrooted_annotation_is_not_bracket_wrapped(tmp_path,
                                                            monkeypatch):
    """A `[…]`-shaped annotation risks being scanned as a citation marker, which
    is the vocabulary drift A17 exists to prevent. It mirrors the shipped
    ` ⚠ BROKEN (…)` paren convention instead."""
    ws, _proj, report = _workspace(tmp_path)
    report.write_text("A [stated — local-file:notes/local.md:1].\n")
    _run(report, ws, monkeypatch)
    body = report.read_text()

    assert not re.search(r"\[[^\]]*MISROOTED", body), (
        "the annotation must not be bracket-wrapped")
    # And it must be invisible to the citation scanner.
    scanned = [c.raw for c in eng.parse_citations(body)]
    assert not any("MISROOTED" in r for r in scanned)


def test_a3_unresolved_never_annotates_the_report(tmp_path, monkeypatch):
    """An unrunnable check must not mark a person's report."""
    ws, _proj, report = _workspace(tmp_path)
    original = "A [stated — linear:acme@v1:ENG-1].\n"
    report.write_text(original)
    res = _run(report, ws, monkeypatch)
    assert res["broken_count"] == 0
    assert report.read_text() == original
    assert res["citation_counts"]["unresolved"] == 1


def test_a3_all_three_outcomes_are_distinguishable_from_the_report_alone(
        tmp_path, monkeypatch):
    """C4: a reader tells the outcomes apart without opening any target."""
    ws, _proj, report = _workspace(tmp_path)
    report.write_text(
        "live [stated — local-file:Thoughts/real.md:1]\n"
        "mis [stated — local-file:notes/local.md:1]\n"
        "dead [stated — local-file:gone/x.md:1]\n")
    res = _run(report, ws, monkeypatch)
    body = report.read_text()

    assert res["citation_counts"] == {
        "live": 1, "misrooted": 1, "dead": 1, "unresolved": 0}
    assert res["broken_count"] == 1
    assert "[stated — local-file:Thoughts/real.md:1]\n" in body   # live: bare
    assert "⚠ MISROOTED" in body                                   # misrooted
    assert "[unverified — source not found" in body                # dead


def test_a3_corpus_measured_blast_radius():
    """The measured number, asserted rather than predicted: across the live
    corpus the classification is 4 live / 5 misrooted / 1 dead, and exactly ONE
    citation counts as broken. A two-valued check would report six.

    Skips when the corpus is not present (another machine / a checkout without
    it) rather than failing on an environment difference.
    """
    ws = Path.home() / "repos" / "Projects"
    if not ws.exists():
        pytest.skip("live corpus not present")
    files, seen = [], set()
    for pat in ("**/*_RESEARCH*.md", "**/*_CLAIMS*.md"):
        for p in ws.glob(pat):
            rp = p.resolve()
            if rp not in seen:
                seen.add(rp)
                files.append(p)

    tally = {o: 0 for o in cr.OUTCOMES}
    naive_dead = 0
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pairs = cr.resolve_all(eng.parse_citations(text), exists=os.path.exists,
                               workspace_root=ws, citing_file=f)
        for k, v in cr.counts(pairs).items():
            tally[k] += v
        for _c, r in pairs:
            if r.outcome in (cr.MISROOTED, cr.DEAD):
                naive_dead += 1     # what a workspace-root-only check would say

    assert tally == {"live": 4, "misrooted": 5, "dead": 1, "unresolved": 0}, tally
    assert sum(1 for k in (cr.DEAD,) for _ in range(tally[k])) == 1
    assert naive_dead == 6, (
        "precondition: the naive two-valued check reports 6 — this is the "
        "false-failure rate A3 exists to remove")


# --------------------------------------------------------------------------- #
# A4 — the record, on BOTH routes
# --------------------------------------------------------------------------- #
def test_a4_manifest_route_records_the_citation_check(tmp_path, monkeypatch):
    ws, _proj, report = _workspace(tmp_path)
    report.write_text("dead [stated — local-file:gone/x.md:1]\n")
    res = _run(report, ws, monkeypatch)

    state_dir = tmp_path / "rp"
    state_dir.mkdir()
    (state_dir / "RP-sid1.json").write_text('{"cycles": {}}')
    assert rlc.write_manifest_linkcheck("sid1", "default", res,
                                        state_dir=str(state_dir))
    import json
    block = json.loads((state_dir / "RP-sid1.json").read_text())
    lc = block["cycles"]["default"]["linkcheck"]
    assert lc["citation_check"] == "ran"
    assert lc["citation_counts"]["dead"] == 1


def test_a4_no_manifest_route_still_leaves_a_readable_record(tmp_path,
                                                             monkeypatch):
    """The Internal-KB route never calls r0_intake, so it has NO manifest at all
    — on the one route whose reports are internal by definition. The record must
    survive there or the slice delivers nothing where it matters most."""
    ws, _proj, report = _workspace(tmp_path)
    report.write_text("dead [stated — local-file:gone/x.md:1]\n")
    res = _run(report, ws, monkeypatch)

    # No RP-<sid>.json anywhere: the manifest write refuses, as it should.
    empty = tmp_path / "no-manifest"
    empty.mkdir()
    assert rlc.write_manifest_linkcheck("sid1", "default", res,
                                        state_dir=str(empty)) is False

    assert rlc.write_frontmatter_citation_check(str(report), "default", res)
    body = report.read_text()
    assert "citation_check: \"ran\"" in body
    assert "citation_outcomes:" in body and "dead=1" in body


def test_a4_the_rendered_citation_fields_survive_a_round_trip(tmp_path):
    """Regression for the silent-drop defect: `_render_cycles_block` renders a
    fixed field set and discards every other key with no error, so a field that
    is recorded but not rendered is lost invisibly."""
    rendered = eng._render_cycles_block([{
        "cycle": "default", "verdict": "PASS", "rounds": 2,
        "citation_check": "ran",
        "citation_check_at": "2026-09-09T00:00:00+00:00",
        "citation_outcomes": "live=4 misrooted=5 dead=1 unresolved=0",
    }])
    assert 'citation_check: "ran"' in rendered
    assert "citation_outcomes:" in rendered
    assert "dead=1" in rendered


def test_a4_writing_the_citation_check_preserves_the_factcheck_verdict(tmp_path):
    """THE data-loss regression. Rows used to merge WHOLE, so a writer carrying
    only some of a cycle's fields destroyed the rest — here, the fact-check's own
    verdict and rounds, in the record this slice exists to make trustworthy."""
    report = tmp_path / "r_RESEARCH.md"
    report.write_text("# body\n")

    eng._append_research_frontmatter(
        str(report), [{"cycle": "default", "verdict": "PASS", "rounds": 3}])
    assert 'verdict: "PASS"' in report.read_text()

    rlc.write_frontmatter_citation_check(
        str(report), "default",
        {"citation_check": "ran",
         "citation_counts": {"live": 1, "misrooted": 0, "dead": 0,
                             "unresolved": 0}})

    body = report.read_text()
    assert 'verdict: "PASS"' in body, "the fact-check verdict was destroyed"
    assert "rounds: 3" in body, "the fact-check round count was destroyed"
    assert 'citation_check: "ran"' in body, "the citation record did not land"
    assert 'verdict: "UNKNOWN"' not in body, (
        "a partial writer fabricated a verdict it does not own")


def test_a4_the_reverse_order_also_preserves_both(tmp_path):
    """The clobber is symmetric: whichever writer runs second used to erase the
    other. Both orders must now survive."""
    report = tmp_path / "r_RESEARCH.md"
    report.write_text("# body\n")

    rlc.write_frontmatter_citation_check(
        str(report), "default",
        {"citation_check": "ran",
         "citation_counts": {"live": 2, "misrooted": 0, "dead": 0,
                             "unresolved": 0}})
    eng._append_research_frontmatter(
        str(report), [{"cycle": "default", "verdict": "DIRTY", "rounds": 1}])

    body = report.read_text()
    assert 'verdict: "DIRTY"' in body
    assert 'citation_check: "ran"' in body, (
        "the fact-check write destroyed the citation record")


def test_a4_records_not_run_rather_than_a_zero(tmp_path):
    """C7/C8: a check that could not run must not read as a check that passed.
    A tally of zeros and 'the check did not run' are different answers."""
    report = tmp_path / "r_RESEARCH.md"
    report.write_text("# body\n")
    rlc.write_frontmatter_citation_check(
        str(report), "default",
        {"citation_check": "not-run",
         "citation_check_reason": "the citation vocabulary could not be loaded"})
    body = report.read_text()
    assert 'citation_check: "not-run"' in body
    assert "citation_check_reason:" in body
    assert "citation_outcomes:" not in body, (
        "a not-run check must not report an outcome tally")


def test_a4_does_not_deadlock_against_the_shared_report_lock(tmp_path,
                                                             monkeypatch):
    """`linkcheck_file` holds an exclusive flock on `<report>.md.lock` and
    `_append_research_frontmatter` re-locks the SAME path on a fresh descriptor —
    a same-process deadlock. The record is therefore written after the lock is
    released. This test hangs forever if that sequencing regresses."""
    ws, _proj, report = _workspace(tmp_path)
    report.write_text("dead [stated — local-file:gone/x.md:1]\n")
    res = _run(report, ws, monkeypatch)
    assert rlc.write_frontmatter_citation_check(str(report), "default", res)


# --------------------------------------------------------------------------- #
# A5 — pin the three properties that already hold (design-A15, A28)
# --------------------------------------------------------------------------- #
_EMIT_ANCHOR = "_write_round_file(round_file, round_num, checker_verdicts"


def _emission_call_site_window(chars=1200):
    """The source immediately preceding the marker-emission CALL SITE.

    The anchor string appears TWICE in the engine: once in the `def` line
    (`_factcheck_engine.py:4557`) and once at the emission call site (`:8124`).
    A plain `src.index` finds the DEFINITION, so a window taken from it inspects
    the tail of an unrelated function and pins nothing — both A28 tests below did
    exactly that until an independent Opus audit caught it, and the revert arm
    written to prove them non-vacuous shared the same wrong anchor, so it
    reddened at the wrong site and read as confirmation.

    The call site is the occurrence NOT preceded by `def `. Asserting there is
    exactly one such occurrence is part of the pin: if the engine grows a second
    emission site, this fails rather than silently guarding only the first.
    """
    src = (HOOKS / "_factcheck_engine.py").read_text()
    sites = []
    start = 0
    while True:
        i = src.find(_EMIT_ANCHOR, start)
        if i < 0:
            break
        line_start = src.rindex("\n", 0, i) + 1
        if not src[line_start:i].lstrip().startswith("def "):
            sites.append(i)
        start = i + 1

    assert len(sites) == 1, (
        f"expected exactly one marker-emission call site, found {len(sites)} — "
        "the A28 pins guard a single site and must be revisited if that changes")
    i = sites[0]
    return src[max(0, i - chars):i], src.count("\n", 0, i) + 1


def test_a5_the_emission_pins_actually_anchor_on_the_call_site():
    """Guard the guard. Both A28 pins below are only meaningful if their window
    covers the emission CALL SITE; anchored on the `def` they pass vacuously.

    This asserts the window is the real one — it sits well past the definition
    and contains the aggregation the emission follows.
    """
    window, line = _emission_call_site_window()
    assert line > 8000, (
        f"emission call site resolved to line {line}; the A28 pins are "
        "anchored on the function definition and guard nothing")
    assert "agg_verdict" in window, (
        "window does not contain the aggregation preceding emission — it is "
        "probably not the emission site's own context")


def test_a5_marker_emission_is_source_agnostic():
    """A28: the first-round marker is written by the code that owns the run,
    unconditionally after aggregation. It must not become conditional on which
    source kind the research drew on — that is how the new surface would fall
    out of the measured population while both numbers stayed green.

    Asserted against the call site rather than restated in prose.
    """
    window, _line = _emission_call_site_window()
    # The guard immediately preceding the write must not branch on source kind.
    for token in ("source_class", "source_kind", "is_internal", "locator_kind"):
        assert token not in window, (
            f"marker emission became conditional on {token!r} — emission must "
            "stay source-agnostic (design-A28)")


def test_a5_marker_emission_depends_on_no_producer_checkpoint():
    """A28: emission is the run-owner's, never the producer reporting its own
    progress. A producer-checkpoint dependency would let a producer suppress its
    own marker."""
    window, _line = _emission_call_site_window()
    for token in ("checkpoint", "producer_reported", "self_report"):
        assert token not in window, (
            f"marker emission acquired a producer-checkpoint dependency "
            f"({token!r}) — design-A28 forbids it")


def test_a5_no_per_source_kind_rate_is_exposed():
    """design-A15, enforced rather than documented. The OMTM's unit is one marker
    per research file while source selection is multi-select, so a per-kind rate
    has no well-defined denominator and would misdescribe the measurement.

    This test fails the day a per-kind key appears in the metric result.
    """
    assert eng._OMTM_PRESENT_AXES == ("verdict",), (
        "the OMTM axis set changed; a per-kind axis would give the rate a "
        "denominator it does not have (design-A15)")

    rate_fns = [n for n in dir(eng) if "omtm" in n.lower() and "rate" in n.lower()]
    assert rate_fns, "precondition: the OMTM rate reader still exists"
    for name in rate_fns:
        fn = getattr(eng, name)
        src = getattr(fn, "__doc__", "") or ""
        for token in ("per_kind", "per-source-kind", "by_source_kind"):
            assert token not in src, (
                f"{name} advertises a per-source-kind rate — design-A15 declines "
                "it because the denominator is undefined")

    # And the shipped result carries no per-kind key.
    for token in ("per_kind", "by_source_kind", "rate_by_kind",
                  "internal_rate", "web_rate"):
        assert token not in (HOOKS / "_factcheck_engine.py").read_text(), (
            f"a per-source-kind rate key {token!r} appeared — design-A15 "
            "declines it, and this non-action is enforced, not documented")


# --------------------------------------------------------------------------- #
# A8 — reach
# --------------------------------------------------------------------------- #
def _linkcheck_arms():
    return (HOOKS / "research-linkcheck.sh").read_text()


def _hook_admits(path):
    """Ask the ACTUAL hook script whether it admits a path, via its own case."""
    script = _linkcheck_arms()
    m = re.search(r"case \"\$FILE_PATH\" in\n(.*?)\nesac", script, re.DOTALL)
    assert m, "could not locate the hook's path filter"
    probe = (
        "case \"$1\" in\n" + m.group(1) + "\nesac\n"
        "exit 1\n"
    ).replace(") ;;", ") exit 0 ;;").replace("*) exit 0 ;;", "*) exit 1 ;;")
    out = subprocess.run(["bash", "-c", probe, "_", path], capture_output=True)
    return out.returncode == 0


def test_a8_the_file_holding_the_only_dead_citation_is_reached():
    """Without reach the whole chain runs on 40% of the corpus's internal
    citations and on NONE of its dead ones — a correct check that never meets
    the problem."""
    dead_file = str(Path.home() / "repos" / "Projects" / "[YourProject]" /
                    "Onboarding v2" / "tax-registration-20260830152212_RESEARCH.md")
    assert _hook_admits(dead_file), (
        "the file holding the corpus's only dead internal citation is not "
        "reached by the link-check filter")


def test_a8_a_research_file_outside_the_workspace_is_still_refused():
    """The widening is workspace-ANCHORED. The hook receives absolute paths, so
    an unanchored `*_RESEARCH*.md` arm would admit research files anywhere on
    disk — a reach change nobody measured and nobody asked for."""
    for outside in ("/tmp/foo_RESEARCH.md",
                    "${KIT_HOOKS_DIR}/x_RESEARCH.md",
                    "/etc/other_RESEARCH.md",
                    "/var/tmp/nested/deep/a_RESEARCH.md"):
        assert not _hook_admits(outside), (
            f"{outside} was admitted — the filter is unbounded")


def test_a8_the_factcheck_filter_is_unchanged():
    """Only the LINK-CHECK filter widens. Fact-check dispatches real AI checker
    rounds, so widening it would put 86 more files each through checker runs —
    a real behaviour change outside a metric slice."""
    fc = (HOOKS / "factcheck-research-file.sh").read_text()
    m = re.search(r"case \"\$FILE_PATH\" in\n(.*?)\nesac", fc, re.DOTALL)
    assert m, "could not locate the fact-check filter"
    arms = [ln.strip() for ln in m.group(1).split("\n") if ln.strip()]
    assert "*/Thoughts/*_RESEARCH*.md) ;;" in arms
    assert "*/Personal/your-project/Docs/*_RESEARCH.md) ;;" in arms
    assert "*/Personal/your-project/Assessments/watchlist_assessment_*.md) ;;" in arms
    assert any("APPLICATION_" in a for a in arms)
    assert not any(a.startswith("*/Projects/*_RESEARCH") for a in arms), (
        "the fact-check filter was widened; S13 widens link-check ONLY")


# --------------------------------------------------------------------------- #
# A6 — the zero-instance tripwire (non-action 3, carrying S12-obs5)
# --------------------------------------------------------------------------- #
def test_a6_section_anchor_citations_are_still_zero_instance():
    """S12-obs5, carried forward with a tripwire instead of a memory.

    The Marker Contract's Edge-4 section-anchor form
    (`local-file:<path>#<section-heading>`, no line part) is not resolvable by
    this slice: `_PAYLOAD_LOCAL_FILE` requires a `:<line>`, so an anchor citation
    does not parse as an address. S13 declines to fix that — the fix is either an
    optional `line` part or an A17 three-loci vocabulary edit, and both belong to
    the vocabulary owner, not to a metric slice.

    Re-measured 2026-09-09, not inherited: 0 instances across the corpus. This
    test is what turns that zero from something a person must remember into
    something the suite reports. The day the corpus gains one, this fails and
    names the file.

    Note for whoever picks it up: both workspace roots are the SAME tree
    (`~/Projects` is a symlink to `~/repos/Projects`), so a corpus scan that does
    not deduplicate by resolved path doubles every figure it reports.
    """
    ws = Path.home() / "repos" / "Projects"
    if not ws.exists():
        pytest.skip("live corpus not present")

    offenders = []
    seen = set()
    for pat in ("**/*_RESEARCH*.md", "**/*_CLAIMS*.md"):
        for p in ws.glob(pat):
            rp = p.resolve()
            if rp in seen:
                continue
            seen.add(rp)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for c in eng.parse_citations(text):
                if c.is_internal and "#" in c.raw:
                    offenders.append(f"{p}:{c.line} {c.raw}")

    assert offenders == [], (
        "the Edge-4 section-anchor citation form has appeared in the corpus. It "
        "does not parse as an address, so it resolves to `unresolved` rather "
        "than being checked. S13 deliberately left this unfixed while the count "
        "was zero — it no longer is:\n  " + "\n  ".join(offenders))


# --------------------------------------------------------------------------- #
# S12 boundary — must remain intact (V1 step 5)
# --------------------------------------------------------------------------- #
def test_s12_wellformedness_stays_a_parse_not_an_existence_check():
    """If S13 had to change this, S13 has absorbed S12's concern rather than
    extending it. A citation naming a path that does not exist is still
    well-formed; whether it EXISTS is this slice's question, asked elsewhere."""
    cit = eng.classify_citation("stated", "local-file:definitely/not/here.md:1")
    assert cit.well_formed is True, (
        "well-formedness became an existence check — S12's boundary is broken")

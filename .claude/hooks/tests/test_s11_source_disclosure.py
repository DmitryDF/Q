"""S11 — evidence & degradation disclosure (research-source-adapters).

When the admission port cannot be BUILT, the run reads the web with the person's
approved source list unenforced. The condition was already detected and already
computed; it died at the boundary of `_prefetch_sources`. S11 carries it the short
distance to the seam that already writes into the saved report.

Three links, and this suite is deliberately arranged so **each one can be turned
red alone**:

* **A1** — the channel out of `_prefetch_sources` (`admission_status`).
* **A2** — the optional field in the `fc_cycles` row renderer.
* **A3** — the disclosure section in the saved report body.

The A2 and A3 tests never call `_prefetch_sources`, and the A1 test never reads a
report; the terminal-seam tests pass the reason in explicitly. So suppressing A1's
write turns exactly one test red, and does not "pass through" to the other two.
That separation is the point — a check that stays green with its own link disabled
asserts the chain rather than testing it.

Placement is asserted **by offset**, never by the phrase "beside the findings",
which is not decidable.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
HOOKS_DIR = CONFIG_DIR / "hooks"
sys.path.insert(0, str(HOOKS_DIR))

import _factcheck_engine as eng  # noqa: E402
import output_security_record as osr  # noqa: E402


REASON = "ImportError: simulated — admission modules unavailable"


@pytest.fixture
def report(tmp_path):
    """A saved research report in the shape this corpus actually holds."""
    p = tmp_path / "topic_RESEARCH.md"
    p.write_text(
        "---\n"
        "title: topic\n"
        "---\n"
        "\n"
        "Parent: [[topic_THOUGHT]]\n"
        "\n"
        "# Findings\n"
        "\n"
        "A claim. [stated — https://example.com]\n",
        encoding="utf-8",
    )
    return p


def _break_the_port(monkeypatch):
    """Force `_WebAdmitter`'s constructor to catch a failure, as in production."""
    def boom():
        raise ImportError("simulated: admission modules unavailable")
    monkeypatch.setattr(eng, "_load_research_admission_modules", boom)


# ---------------------------------------------------------------------------
# A1 — the channel. Revert arm 1: suppress the write to `admission_status`.
# ---------------------------------------------------------------------------

def test_a1_a_failed_port_is_observable_in_the_runs_own_scope(monkeypatch):
    _break_the_port(monkeypatch)
    status = {}
    eng._prefetch_sources(
        ["https://example.com"],
        _fetch_fn=lambda u, t, b: ("ok", "body"),
        admission_status=status,
    )
    assert status.get("degraded_reason"), (
        "the reason the port could not be built must reach the caller's scope; "
        "without it nothing downstream has a condition to disclose"
    )
    assert "ImportError" in status["degraded_reason"]


def test_a1_a_healthy_run_reports_no_degradation(monkeypatch):
    status = {}
    eng._prefetch_sources(
        ["https://example.com"],
        _fetch_fn=lambda u, t, b: ("ok", "body"),
        admission_status=status,
    )
    assert status.get("degraded_reason") is None, (
        "a run whose port built normally must report nothing — a channel that "
        "always reports would make every run look degraded"
    )


def test_a1_the_return_shape_is_untouched(monkeypatch):
    """The reason travels by out-parameter precisely because this is pinned."""
    assert eng._prefetch_sources([]) == []
    assert eng._prefetch_sources([], admission_status={}) == []
    _break_the_port(monkeypatch)
    res = eng._prefetch_sources(
        ["https://example.com"],
        _fetch_fn=lambda u, t, b: ("ok", "body"),
        admission_status={},
    )
    assert res == [{"url": "https://example.com", "status": "ok", "content": "body"}]


def test_a1_the_fallback_still_fetches_and_still_verifies(monkeypatch):
    """The A18 fail-safe is untouched: disclosure never costs availability."""
    _break_the_port(monkeypatch)
    res = eng._prefetch_sources(
        ["https://example.com"],
        _fetch_fn=lambda u, t, b: ("ok", "content"),
        admission_status={},
    )
    assert res[0]["status"] == "ok"


def test_a1_a_reused_mapping_cannot_report_a_previous_runs_degradation(monkeypatch):
    _break_the_port(monkeypatch)
    status = {}
    eng._prefetch_sources([], admission_status=status)
    assert status["degraded_reason"]
    monkeypatch.undo()
    eng._prefetch_sources([], admission_status=status)
    assert status["degraded_reason"] is None


# ---------------------------------------------------------------------------
# A2 — the structured record. Revert arm 2: emit the field unconditionally.
# ---------------------------------------------------------------------------

# The exact bytes this renderer produced BEFORE S11. Pinned as a literal rather
# than recomputed, because a golden derived from the code under test would move
# with it and could never go red.
_HEALTHY_BLOCK = (
    "# <!-- FC_CYCLES_START -->\n"
    "fc_cycles:\n"
    '  - cycle: "default"\n'
    '    verdict: "PASS"\n'
    "    rounds: 1\n"
    "# <!-- FC_CYCLES_END -->\n"
)


def test_a2_a_healthy_runs_rendered_block_is_byte_identical_to_pre_s11():
    rendered = eng._render_cycles_block(
        [{"cycle": "default", "verdict": "PASS", "rounds": 1}])
    assert rendered == _HEALTHY_BLOCK, (
        "a healthy run's structured record must not change at all; this goes red "
        "the moment the S11 field is emitted unconditionally"
    )


def test_a2_a_degraded_runs_row_carries_the_condition_and_its_reason():
    rendered = eng._render_cycles_block([{
        "cycle": "default", "verdict": "PASS", "rounds": 1,
        "source_list_unenforced_reason": REASON,
    }])
    assert "source_list_unenforced: true" in rendered, "the condition"
    assert "source_list_unenforced_reason:" in rendered, "its reason"
    assert "ImportError" in rendered


def test_a2_the_field_survives_the_merge_by_cycle_name_round_trip(report):
    """Edge case (iii): merge is by cycle name over full row text."""
    eng._append_research_frontmatter(report, [{
        "cycle": "de", "verdict": "PASS", "rounds": 1}])
    eng._append_research_frontmatter(report, [{
        "cycle": "default", "verdict": "PASS", "rounds": 2,
        "source_list_unenforced_reason": REASON,
    }])
    # A third write on a DIFFERENT cycle must not disturb the degraded row.
    eng._append_research_frontmatter(report, [{
        "cycle": "ru", "verdict": "PASS", "rounds": 1}])
    text = report.read_text(encoding="utf-8")
    assert text.count("source_list_unenforced: true") == 1
    assert '- cycle: "de"' in text and '- cycle: "ru"' in text


def test_a2_the_verdict_is_not_degraded_by_the_condition(report):
    """The recorded operator decision (2026-09-03), made a checked property."""
    eng._write_research_frontmatter_for_terminal(
        report, "default", "PASS", 1, None, source_list_unenforced_reason=REASON)
    text = report.read_text(encoding="utf-8")
    assert 'verdict: "PASS"' in text
    assert "INCOMPLETE" not in text


# ---------------------------------------------------------------------------
# A3 — the saved body. Revert arm 3: remove the section write.
# ---------------------------------------------------------------------------

def test_a3_a_degraded_runs_saved_body_carries_the_disclosure(report):
    eng._write_source_disclosure_section(report, REASON)
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_HEADING in text
    assert "ImportError" in text


def test_a3_the_section_states_both_the_condition_and_its_cost(report):
    """C3 has a check of its own rather than riding on C2's placement check."""
    eng._write_source_disclosure_section(report, REASON)
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_CONDITION in text, "the condition"
    assert eng._SOURCE_DISCLOSURE_CONSEQUENCE in text, "what it means for the findings"
    assert eng._SOURCE_DISCLOSURE_CONDITION != eng._SOURCE_DISCLOSURE_CONSEQUENCE, (
        "two separately-addressable constants, so removing the consequence half "
        "cannot be masked by the condition half"
    )


def test_a3_zero_cited_sources_records_the_condition_without_claiming_a_cost(report):
    """Edge case (ii): the port failed and nothing was cited.

    Record it anyway — the declaration still went unchecked — but the wording
    must NOT imply findings were affected when none were drawn.
    """
    eng._write_source_disclosure_section(report, REASON, unchecked_reads=0)
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_CONDITION in text, "the condition IS recorded"
    assert eng._SOURCE_DISCLOSURE_CONSEQUENCE_NO_SOURCES in text
    assert eng._SOURCE_DISCLOSURE_CONSEQUENCE not in text, (
        "the general wording says findings 'may rest on sources you did not "
        "approve', which is false when nothing was read"
    )


def test_a3_an_unknown_read_count_takes_the_general_wording(report):
    """`None` means 'not established', never 'established as zero'."""
    eng._write_source_disclosure_section(report, REASON, unchecked_reads=None)
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_CONSEQUENCE in text
    assert eng._SOURCE_DISCLOSURE_CONSEQUENCE_NO_SOURCES not in text


def test_a1_reports_how_many_reads_went_through_the_unbounded_fallback(monkeypatch):
    _break_the_port(monkeypatch)
    status = {}
    eng._prefetch_sources(
        ["https://a.example", "https://b.example"],
        _fetch_fn=lambda u, t, b: ("ok", "x"),
        admission_status=status,
    )
    assert status["attempted_reads"] == 2
    status2 = {}
    eng._prefetch_sources([], admission_status=status2)
    assert status2["attempted_reads"] == 0


def test_a3_the_wording_never_claims_the_boundary_is_enforced():
    """`Skills/research-en.md:363`'s forbidden claim."""
    body = (eng._SOURCE_DISCLOSURE_HEADING + " "
            + eng._SOURCE_DISCLOSURE_CONDITION + " "
            + eng._SOURCE_DISCLOSURE_CONSEQUENCE + " "
            + eng._SOURCE_DISCLOSURE_CONSEQUENCE_NO_SOURCES).lower()
    for forbidden in ("impossible", "prevented", "blocked", "guarantee"):
        assert forbidden not in body, (
            f"the disclosure must never imply enforcement — found {forbidden!r}")


def test_a3_a_healthy_runs_body_is_byte_identical(report):
    before = report.read_text(encoding="utf-8")
    eng._write_source_disclosure_section(report, None)
    assert report.read_text(encoding="utf-8") == before
    eng._write_source_disclosure_section(report, "")
    assert report.read_text(encoding="utf-8") == before


def test_a3_the_terminal_seam_leaves_a_healthy_bodys_prose_untouched(report):
    eng._write_research_frontmatter_for_terminal(report, "default", "PASS", 1, None)
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_START not in text
    assert eng._SOURCE_DISCLOSURE_HEADING not in text
    assert "source_list_unenforced" not in text


def test_a3_re_running_produces_one_section_not_two(report):
    eng._write_source_disclosure_section(report, REASON)
    first = report.read_text(encoding="utf-8")
    eng._write_source_disclosure_section(report, REASON)
    again = report.read_text(encoding="utf-8")
    assert again == first
    assert again.count(eng._SOURCE_DISCLOSURE_START) == 1
    # A changed reason replaces rather than accumulates.
    eng._write_source_disclosure_section(report, "OSError: a different reason")
    third = report.read_text(encoding="utf-8")
    assert third.count(eng._SOURCE_DISCLOSURE_START) == 1
    assert "a different reason" in third and "ImportError" not in third


def test_a3_never_creates_a_file_that_does_not_exist(tmp_path):
    """Edge case (v): inherit `_append_research_frontmatter`'s no-op."""
    missing = tmp_path / "absent_RESEARCH.md"
    eng._write_source_disclosure_section(missing, REASON)
    assert not missing.exists()


@pytest.mark.parametrize("payload", [
    "<!-- FC_SOURCE_DISCLOSURE_END --> injected",
    "--> break out <!--",
    "line one\n## A Forged Heading\nline two",
    "```\nunterminated fence",
    "x" * 5000,
])
def test_a3_an_untrusted_reason_cannot_break_the_section(report, payload):
    """Edge case (vi): the one place non-engine-authored text reaches a report."""
    eng._write_source_disclosure_section(report, payload)
    text = report.read_text(encoding="utf-8")
    assert text.count(eng._SOURCE_DISCLOSURE_START) == 1
    assert text.count(eng._SOURCE_DISCLOSURE_END) == 1
    start = text.index(eng._SOURCE_DISCLOSURE_START)
    end = text.index(eng._SOURCE_DISCLOSURE_END)
    assert start < end, "the section must not be terminated early by its own content"
    section = text[start:end]
    assert "\n#" not in section.replace(
        "\n" + eng._SOURCE_DISCLOSURE_HEADING, ""), "no forged heading"
    assert len(section) < 2000, "an enormous reason must not dominate the report"
    # The rest of the document survives intact.
    assert "# Findings" in text and "A claim." in text


# ---------------------------------------------------------------------------
# Placement. Revert arm 5: place the section at end of document instead.
# Asserted BY OFFSET — "beside the findings" is not decidable and appears nowhere.
# ---------------------------------------------------------------------------

SHAPES = {
    "frontmatter-then-parent":
        "---\nt: x\n---\n\nParent: [[s]]\n\n# Findings\n\nbody\n",
    "parent-then-frontmatter":
        "Parent: [[s]]\n\n---\nt: x\n---\n\n# Findings\n\nbody\n",
    "frontmatter-no-parent":
        "---\nt: x\n---\n\n# Findings\n\nbody\n",
    "parent-no-frontmatter":
        "Parent: [[s]]\n\n# Findings\n\nbody\n",
    "no-frontmatter-no-parent":
        "# Findings\n\nbody\n",
    "no-body-heading-at-all":
        "---\nt: x\n---\n\njust prose, no heading anywhere\n",
}


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_placement_is_top_of_body_on_every_shape_this_corpus_holds(tmp_path, name):
    p = tmp_path / f"{name}_RESEARCH.md"
    p.write_text(SHAPES[name], encoding="utf-8")
    eng._write_source_disclosure_section(p, REASON)
    text = p.read_text(encoding="utf-8")

    at = text.index(eng._SOURCE_DISCLOSURE_START)

    # Never an EOF append: something of the original document follows it.
    tail = text[text.index(eng._SOURCE_DISCLOSURE_END):]
    assert tail.strip() != eng._SOURCE_DISCLOSURE_END, (
        "the section must not be the last thing in the document")

    # Before the first body heading, where the document has one.
    heading = text.find("# Findings")
    if heading != -1:
        assert at < heading, "the disclosure must precede the findings"

    # After the frontmatter block, where the document has one.
    if text.lstrip().startswith("---") and "---" in SHAPES[name]:
        fm_close = text.index("\n---", text.index("---") + 3)
        assert at > fm_close, "the disclosure must sit outside the frontmatter"

    # After the `Parent:` line, where the document has one.
    if "Parent:" in text:
        assert at > text.index("Parent:")


def test_placement_never_lands_inside_the_frontmatter_block(report):
    """A section inside `---` fences would corrupt the YAML the gates parse."""
    eng._append_research_frontmatter(report, [{
        "cycle": "default", "verdict": "PASS", "rounds": 1}])
    eng._write_source_disclosure_section(report, REASON)
    text = report.read_text(encoding="utf-8")
    fm_close = text.index("\n---", text.index("---") + 3)
    assert text.index(eng._SOURCE_DISCLOSURE_START) > fm_close
    assert text.index(eng._FC_CYCLES_START) < fm_close


# ---------------------------------------------------------------------------
# Concurrency. Revert arm 4: have A3 write WITHOUT taking the file's `.lock`.
#
# This drives the NEW writer against the frontmatter writer for real. The
# existing 12-thread test calls `_append_research_frontmatter` directly and is
# structurally blind to any writer placed elsewhere, so it is no evidence here.
# ---------------------------------------------------------------------------

def test_a3_participates_in_the_per_file_lock_the_frontmatter_writer_takes(
        report, monkeypatch):
    """A lost-update race, made deterministic by widening A3's own write window.

    `_atomic_write_text` is A3's writer and NOT the frontmatter writer's (which
    inlines its own mkstemp/os.replace), so delaying it widens the gap between
    A3's read and A3's write and nothing else. Under the lock that gap is inside
    the critical section and the frontmatter writer simply waits. Without the
    lock, the frontmatter row lands in that gap and A3 writes stale text over it.
    """
    real_write = eng._atomic_write_text
    entered = threading.Event()

    def slow_write(path, text):
        entered.set()
        time.sleep(0.4)
        return real_write(path, text)

    monkeypatch.setattr(eng, "_atomic_write_text", slow_write)

    errors = []

    def disclose():
        try:
            eng._write_source_disclosure_section(report, REASON)
        except Exception as exc:                      # pragma: no cover
            errors.append(exc)

    t = threading.Thread(target=disclose)
    t.start()
    assert entered.wait(5), "the disclosure writer never reached its write"
    # A3 has now read the file and is mid-write. Land a frontmatter row in
    # exactly the window an unlocked A3 would clobber.
    eng._append_research_frontmatter(report, [{
        "cycle": "default", "verdict": "PASS", "rounds": 3}])
    t.join(10)
    assert not t.is_alive()
    assert not errors, errors

    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_HEADING in text, "the disclosure was lost"
    assert '- cycle: "default"' in text, (
        "the concurrent fc_cycles row was clobbered — the disclosure writer is "
        "not taking the per-file lock the frontmatter writer takes"
    )
    assert "rounds: 3" in text


def test_a3_holds_the_same_lock_path_the_frontmatter_writer_uses(report):
    """Same lock, not merely 'a' lock — a second lock file serialises nothing."""
    lockpath = report.with_suffix(report.suffix + ".lock")
    eng._write_source_disclosure_section(report, REASON)
    assert lockpath.exists()
    assert not list(report.parent.glob("*.lock2"))
    siblings = [p.name for p in report.parent.glob("*.lock")]
    assert siblings == [lockpath.name], siblings


# ---------------------------------------------------------------------------
# Arm 6 — CHARACTERISATION, not a revert arm.
#
# The research skill's Closing step 3 (`Skills/research-en.md:349-355`) is an
# unlocked read-modify-write: it renders the merged document and writes it back.
# The ORDERING is the whole test, so both are asserted, each decidably.
#
# Falsifying condition: if the interleaved case does NOT lose the disclosure,
# edge case (vii)'s premise is wrong and must be re-derived rather than restated.
# This arm does not fix the skill-side write, which stays out of scope.
# ---------------------------------------------------------------------------

def _skill_side_read(path):
    """Step 3's read half."""
    return path.read_text(encoding="utf-8")


def _skill_side_write_back(path, text_read_earlier):
    """Step 3's modify+write half — pure merge, then a plain unlocked write."""
    merged = osr.merge_insecure_sources_section(text_read_earlier, "## Insecure-input sources\n\nNone.")
    path.write_text(merged, encoding="utf-8")


def test_arm6_sequential_ordering_preserves_the_disclosure(report):
    """Step 3 reads a document that ALREADY carries the disclosure."""
    eng._write_source_disclosure_section(report, REASON)
    read = _skill_side_read(report)
    _skill_side_write_back(report, read)
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_HEADING in text, (
        "the merge passes through everything outside its own heading, so a later "
        "save is not by itself the hazard"
    )
    assert osr.INSECURE_SOURCES_HEADING in text


def test_arm6_interleaved_ordering_loses_the_disclosure(report):
    """Step 3 reads FIRST; A3 writes; step 3 writes back its STALE text.

    This is the hazard. It is characterised, not fixed: the skill-side write is
    out of this slice's scope. If this assertion ever fails, edge case (vii)'s
    premise is wrong and must be re-derived.
    """
    read = _skill_side_read(report)                   # stale: no disclosure yet
    eng._write_source_disclosure_section(report, REASON)
    assert eng._SOURCE_DISCLOSURE_HEADING in report.read_text(encoding="utf-8")
    _skill_side_write_back(report, read)              # writes the stale text back
    text = report.read_text(encoding="utf-8")
    assert eng._SOURCE_DISCLOSURE_HEADING not in text, (
        "FALSIFIED: the interleaved ordering did NOT lose the disclosure. Edge "
        "case (vii)'s premise must be re-derived rather than restated."
    )


# ---------------------------------------------------------------------------
# The seam, end to end — A2 and A3 are two halves of ONE writeback.
# ---------------------------------------------------------------------------

def test_the_terminal_seam_writes_both_halves_on_a_degraded_run(report):
    eng._write_research_frontmatter_for_terminal(
        report, "default", "PASS", 2, None, source_list_unenforced_reason=REASON)
    text = report.read_text(encoding="utf-8")
    assert "source_list_unenforced: true" in text, "the structured record (A2)"
    assert eng._SOURCE_DISCLOSURE_HEADING in text, "the report body (A3)"
    # Ordering note: A2 first, so A3's top-of-body anchor is always well-defined.
    assert text.index(eng._FC_CYCLES_START) < text.index(eng._SOURCE_DISCLOSURE_START)
    assert text.index(eng._SOURCE_DISCLOSURE_START) < text.index("# Findings")


def test_the_terminal_seam_on_a_file_with_no_frontmatter(tmp_path):
    """Ordering note in practice: A2 creates the `---` block when absent."""
    p = tmp_path / "bare_RESEARCH.md"
    p.write_text("# Findings\n\nbody\n", encoding="utf-8")
    eng._write_research_frontmatter_for_terminal(
        p, "default", "PASS", 1, None, source_list_unenforced_reason=REASON)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    fm_close = text.index("\n---", text.index("---") + 3)
    assert text.index(eng._SOURCE_DISCLOSURE_START) > fm_close
    assert text.index(eng._SOURCE_DISCLOSURE_START) < text.index("# Findings")


# ---------------------------------------------------------------------------
# END TO END — a real `factcheck_run` with the admission port broken.
#
# Everything above drives a seam. This drives the whole path the Diagnosis
# names: the port fails to build, the fallback reads unbounded, and the saved
# report says so. Nothing on the S11 path is mocked; only the network fetch, the
# checker dispatch and the run log are.
# ---------------------------------------------------------------------------

FIXTURE_BODY = (
    "Parent: [[topic_THOUGHT]]\n"
    "\n"
    "# Findings\n"
    "\n"
    "A claim. [stated — https://example.com/a]\n"
)


def _drive_factcheck(tmp_path, draft, *, break_port):
    import contextlib
    from unittest import mock

    state_dir = tmp_path / "state"
    state_dir.mkdir(exist_ok=True)

    def fake_fetch(url, timeout_s, max_bytes):
        return ("ok", "page content")

    def passing_checker(dp, idx, model, rnd, prior):
        return "reasoning\nVERDICT: PASS"

    def boom():
        raise ImportError("simulated: admission modules unavailable")

    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.object(eng, "_fetch_one_url", side_effect=fake_fetch))
        stack.enter_context(mock.patch.object(eng, "_log_factcheck_run"))
        # The coverage axis is a DIFFERENT gate with its own provenance signal;
        # unmocked it downgrades this fixture to INCOMPLETE on grounds unrelated
        # to S11 and would mask what these tests are here to assert. Passed
        # through exactly as the shipped S6 suite does for the same reason. The
        # S11 path itself is not mocked anywhere.
        stack.enter_context(mock.patch.object(
            eng, "_run_coverage_axis_gate",
            side_effect=lambda rp, cs, verdict, td, kind, **kw: verdict))
        if break_port:
            stack.enter_context(mock.patch.object(
                eng, "_load_research_admission_modules", side_effect=boom))
        return eng.factcheck_run(
            state_dir=str(state_dir),
            draft_path=str(draft),
            kind="research",
            session_id="b11b11b1-1111-1111-1111-111111111111",
            debounce_seconds=0,
            models=["sonnet"],
            max_rounds=1,
            _checker_fn=passing_checker,
            proj="p",
            topic="t",
        )


def test_e2e_a_degraded_run_still_verifies_and_says_so(tmp_path):
    draft = tmp_path / "degraded_RESEARCH.md"
    draft.write_text(FIXTURE_BODY, encoding="utf-8")

    result = _drive_factcheck(tmp_path, draft, break_port=True)

    # 1. The A18 fail-safe is intact: the run still verifies.
    assert result["status"] == "PASS", result

    text = draft.read_text(encoding="utf-8")

    # 2. The structured record carries the condition and its reason.
    assert "source_list_unenforced: true" in text
    assert "ImportError" in text

    # 3. The saved body carries the disclosure, at its owned heading, positioned
    #    by OFFSET: after the frontmatter block and the `Parent:` line, before
    #    the first body heading.
    assert eng._SOURCE_DISCLOSURE_HEADING in text
    at = text.index(eng._SOURCE_DISCLOSURE_START)
    fm_close = text.index("\n---", text.index("---") + 3)
    assert at > fm_close, "must sit outside the frontmatter"
    assert at > text.index("Parent:")
    assert at < text.index("# Findings"), "must precede the findings"

    # 4. Re-running the degraded case produces ONE section, not two.
    _drive_factcheck(tmp_path, draft, break_port=True)
    again = draft.read_text(encoding="utf-8")
    assert again.count(eng._SOURCE_DISCLOSURE_START) == 1
    assert again.count(eng._SOURCE_DISCLOSURE_HEADING) == 1


def test_e2e_a_healthy_control_runs_saved_report_is_byte_identical_to_pre_s11(tmp_path):
    """The C5 control. Compared against a golden built from the PINNED pre-S11
    block literal, not from the renderer under test — a golden derived from the
    code being checked moves with it and can never go red."""
    draft = tmp_path / "healthy_RESEARCH.md"
    draft.write_text(FIXTURE_BODY, encoding="utf-8")

    result = _drive_factcheck(tmp_path, draft, break_port=False)
    assert result["status"] == "PASS", result

    text = draft.read_text(encoding="utf-8")
    expected = "---\n" + _HEALTHY_BLOCK + "---\n\n" + FIXTURE_BODY
    assert text == expected, (
        "a healthy run's saved report must be byte-for-byte what a pre-S11 "
        "engine produced:\n--- got ---\n" + text + "\n--- want ---\n" + expected
    )


def test_a_disclosure_failure_never_costs_the_structured_record(report, monkeypatch):
    """Availability is never traded for disclosure (A18 fail-safe, carried)."""
    def boom(*a, **k):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(eng, "_write_source_disclosure_section", boom)
    eng._write_research_frontmatter_for_terminal(
        report, "default", "PASS", 1, None, source_list_unenforced_reason=REASON)
    text = report.read_text(encoding="utf-8")
    assert "source_list_unenforced: true" in text

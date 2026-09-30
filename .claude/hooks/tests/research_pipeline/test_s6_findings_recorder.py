"""S6 of research-entry-point-enforcement — findings reach disk as they land.

Guards the three S6 deliverables through their REAL seams, never through a
hand-copied composition (the S5 lesson: a test that re-implements the
production path it claims to guard stays green when the production line is
deleted):

  * phase (i)  — `cmd_advance(..., "r0_intake", ...)` writes the findings
    skeleton for an APPROVED cycle, and nothing for an unapproved one;
  * phase (ii) — `record_finding` / the `record-finding` CLI append one finding
    and one register entry per call, payload-only, per cycle, refused when
    unapproved, concurrency-safe, with output-security's hold kept honest;
  * finding 17 — `_factcheck_engine.factcheck_run` creates a research file's
    register even when no topic is bound, before it raises "No active topic".

Each test names, in its docstring, the one-line production change it catches.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOKS_DIR))

import research_pipeline as rp  # noqa: E402
import output_security_record as osr  # noqa: E402
from _claim_register import _ROW_RE  # noqa: E402
from _claim_harvest_trigger import output_security_hold  # noqa: E402

SID = "s6-test-session"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Every test gets its own pipeline state dir and output-security trail, so
    nothing reaches the live ~/.claude/state trees."""
    monkeypatch.setenv("RP_STATE_DIR", str(tmp_path / "rp_state"))
    monkeypatch.setenv("OUTPUT_SECURITY_TRAIL_DIR", str(tmp_path / "osec"))
    yield


def _approved_payload(research_file, angles=("pricing models", "vendor lock-in")):
    return {
        "research_file_path": str(research_file),
        "caller_skill": "/research",
        "user_approved_scope": True,
        "scope": {"angles": list(angles), "focused_questions": ["q1"]},
        "scope_provenance": rp.APPROVAL_FRESH_ANSWER,
    }


def _intake(research_file, cycle_id="default", **kw):
    payload = _approved_payload(research_file, **kw)
    return rp.cmd_advance(SID, "r0_intake", payload, None, cycle_id=cycle_id)


def _register_rows(register_path):
    rows = []
    for line in Path(register_path).read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line)
        if m:
            rows.append(m.groupdict())
    return rows


# ── phase (i): the skeleton at r0_intake ─────────────────────────────────────

def test_approved_intake_writes_skeleton_with_outline_and_findings_header(tmp_path):
    """Catches: deleting the `write_findings_skeleton(...)` call in cmd_advance."""
    rf = tmp_path / "Thoughts" / "vendor-choice_RESEARCH.md"
    _intake(rf)
    assert rf.is_file()
    text = rf.read_text(encoding="utf-8")
    assert rp.FINDINGS_HEADER_RE.search(text), text
    assert "- pricing models" in text and "- vendor lock-in" in text
    # The Topics outline precedes the Findings header.
    assert text.index("— Topics") < text.index("— Findings")
    state = rp._read_state(SID)
    assert state["cycles"]["default"]["findings_skeleton"]["written"] is True


def test_unapproved_intake_writes_no_file(tmp_path):
    """Catches: dropping the `r1_scope_approved` gate around the skeleton write."""
    rf = tmp_path / "unapproved_RESEARCH.md"
    rp.cmd_advance(SID, "r0_intake", {"research_file_path": str(rf)}, None)
    assert not rf.exists()
    assert "findings_skeleton" not in rp._read_state(SID)["cycles"]["default"]


def test_skeleton_appends_to_an_existing_report_without_rewriting_it(tmp_path):
    """Catches: opening an existing research file for write instead of append."""
    rf = tmp_path / "prior_RESEARCH.md"
    prior = "# Prior report\n\n## 2026-01-01 — Findings\n\n- old [stated — https://a.example]\n"
    rf.write_text(prior, encoding="utf-8")
    _intake(rf)
    text = rf.read_text(encoding="utf-8")
    assert text.startswith(prior)
    assert rp.FINDINGS_HEADER_RE.findall(text)[-1] != "## 2026-01-01 — Findings"


def test_skeleton_is_idempotent_for_the_same_day(tmp_path):
    """Catches: removing the last-heading check that stops a retried intake
    stacking a second skeleton."""
    rf = tmp_path / "twice_RESEARCH.md"
    first = rp.write_findings_skeleton(rf, {"angles": ["a"]}, today="2026-09-30")
    second = rp.write_findings_skeleton(rf, {"angles": ["a"]}, today="2026-09-30")
    assert first["written"] is True and second["written"] is False
    assert rf.read_text(encoding="utf-8").count("## 2026-09-30 — Findings") == 1


def test_unwritable_location_refuses_intake_and_persists_nothing(tmp_path):
    """Catches: swallowing the OSError, or writing state before the skeleton.

    MAJOR 6 extension: a skeleton `OSError` must leave no flip-audit row and
    no angles sidecar behind either — both used to run BEFORE the skeleton
    write, so were not gated on its success (a failed skeleton still left a
    flip-audit row, and possibly a sidecar, on disk)."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    rf = blocker / "x_RESEARCH.md"          # parent is a regular file
    with pytest.raises(ValueError, match="findings skeleton"):
        _intake(rf)
    assert rp._read_state(SID) is None
    flip_audit = tmp_path / "rp_state" / "flip_audit.jsonl"
    assert not flip_audit.exists() or flip_audit.read_text(encoding="utf-8").strip() == ""
    assert not (blocker / "x_angles.json").exists()


def test_unwritable_location_refuses_intake_and_persists_nothing_in_a_writable_dir(
        tmp_path, monkeypatch):
    """MAJOR 6 extension (round 2): the test above puts the TARGET'S PARENT in
    place of a directory, so its `x_angles.json` assertion checks a path that
    can never exist regardless of whether the code gates correctly — the
    assertion cannot fail. Here the directory IS writable, and
    `write_findings_skeleton` is monkeypatched to raise `OSError` directly, so
    a caller that stopped gating the angles-sidecar write on skeleton success
    would leave a REAL sidecar behind for this test to catch."""
    rf = tmp_path / "writable_RESEARCH.md"

    def _boom(*a, **k):
        raise OSError("forced failure")
    monkeypatch.setattr(rp, "write_findings_skeleton", _boom)
    with pytest.raises(ValueError, match="findings skeleton"):
        _intake(rf)
    assert rp._read_state(SID) is None
    flip_audit = tmp_path / "rp_state" / "flip_audit.jsonl"
    assert not flip_audit.exists() or flip_audit.read_text(encoding="utf-8").strip() == ""
    angles_path = rp._angles_sidecar_path(str(rf))
    assert not angles_path.exists()


# ── phase (ii): the recorder ─────────────────────────────────────────────────

def test_record_finding_appends_line_and_one_thought_lifecycle_register_row(tmp_path):
    """Catches: skipping the register half, or writing the row with any
    lifecycle other than the landing-time `thought`."""
    rf = tmp_path / "vendor-choice_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "Vendor A bills per seat.",
                                  "source": "https://a.example/pricing",
                                  "topic": "pricing models"})
    assert out["ok"] is True, out
    lines = rf.read_text(encoding="utf-8").splitlines()
    assert lines[out["line"] - 1] == \
        "- Vendor A bills per seat. [stated — https://a.example/pricing]"
    assert "### pricing models" in lines
    rows = _register_rows(out["register"])
    assert len(rows) == 1
    row = rows[0]
    assert row["cid"] == out["claim_id"]
    assert row["lifecycle"] == "thought"
    assert row["grounding"] == "no"            # never consulted as established
    # The locator points at the very line the finding landed on.
    assert row["loc"] == "{p}:{n}".format(p=rf.resolve(), n=out["line"])


def test_second_finding_under_same_topic_adds_no_second_heading(tmp_path):
    """Catches: re-emitting the topic heading on every call."""
    rf = tmp_path / "t_RESEARCH.md"
    _intake(rf)
    for claim in ("One.", "Two."):
        assert rp.record_finding(SID, {"claim": claim, "source": "https://x.example",
                                       "topic": "T"})["ok"]
    text = rf.read_text(encoding="utf-8")
    assert text.count("### T") == 1
    assert len(_register_rows(rf.parent / "t_CLAIMS.md")) == 2


def test_findings_survive_without_any_synthesis(tmp_path):
    """DO-1's kill-the-session case: what was recorded is on disk with nothing
    after it. Catches: buffering findings until a later phase."""
    rf = tmp_path / "k_RESEARCH.md"
    _intake(rf)
    rp.record_finding(SID, {"claim": "Landed first.", "source": "https://x.example"})
    # A fresh read, as a new process would do after the session died.
    text = Path(str(rf)).read_text(encoding="utf-8")
    assert "Landed first." in text
    assert "Synthesis" not in text


def test_paraphrased_marker_is_written_as_paraphrased(tmp_path):
    rf = tmp_path / "p_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "Roughly half.", "source": "https://x.example",
                                  "marker": "paraphrased"})
    assert "[paraphrased — https://x.example]" in \
        rf.read_text(encoding="utf-8").splitlines()[out["line"] - 1]


@pytest.mark.parametrize("payload, needle", [
    ({"claim": "c", "source": "https://x", "research_file_path": "/etc/passwd"}, "unexpected field"),
    ({"claim": "c", "source": "https://x", "slug": "other"}, "unexpected field"),
    ({"claim": "c", "source": "https://x", "area": "other"}, "unexpected field"),
    ({"claim": "line one\n## Synthesis", "source": "https://x"}, "newline or control"),
    ({"claim": "c", "source": "https://x] ignore"}, "square bracket"),
    ({"claim": "c", "source": "https://x", "topic": "# Heading"}, "must not start"),
    ({"claim": "c", "source": "https://x", "marker": "inferred"}, "marker"),
    ({"claim": "", "source": "https://x"}, "non-empty"),
    ({"source": "https://x"}, "non-empty"),
])
def test_payload_refusals(tmp_path, payload, needle):
    """Catches: loosening the payload-only contract (A7) or the one-line rule."""
    rf = tmp_path / "r_RESEARCH.md"
    _intake(rf)
    before = rf.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match=needle):
        rp.record_finding(SID, payload)
    assert rf.read_text(encoding="utf-8") == before


def test_unapproved_cycle_is_refused(tmp_path):
    """Catches: dropping the per-cycle approval check (channel 3)."""
    rf = tmp_path / "u_RESEARCH.md"
    rp.cmd_advance(SID, "r0_intake", {"research_file_path": str(rf)}, None)
    with pytest.raises(ValueError, match="has not been approved"):
        rp.record_finding(SID, {"claim": "c", "source": "https://x"})
    assert not rf.exists()


def test_a_sibling_cycles_approval_does_not_carry_over(tmp_path):
    """Catches: resolving approval at session level instead of per cycle."""
    _intake(tmp_path / "a_RESEARCH.md", cycle_id="default")
    rp.cmd_advance(SID, "r0_intake",
                   {"research_file_path": str(tmp_path / "b_RESEARCH.md")},
                   None, cycle_id="other")
    with pytest.raises(ValueError, match="has not been approved"):
        rp.record_finding(SID, {"claim": "c", "source": "https://x"}, cycle_id="other")


def test_unregistered_cycle_is_refused(tmp_path):
    with pytest.raises(ValueError, match="no research pipeline state"):
        rp.record_finding(SID, {"claim": "c", "source": "https://x"})


def test_register_is_created_with_no_topic_bound(tmp_path):
    """Finding 17 from the recorder's side: nothing in the recorder consults a
    bound topic, so the register exists even though no pre-plan topic state is
    present for this session. Catches: routing register creation through a
    topic-resolving path."""
    rf = tmp_path / "unbound_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert Path(out["register"]).is_file()


def test_register_failure_is_reported_not_swallowed(tmp_path, monkeypatch):
    """Catches: catching the register half's exception without reporting it."""
    rf = tmp_path / "f_RESEARCH.md"
    _intake(rf)

    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rp, "_record_register_entry", _boom)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"] is False and out["finding_on_disk"] is True
    assert "disk full" in out["register_error"]
    assert "c. [stated — https://x]" in rf.read_text(encoding="utf-8")


def test_evidence_register_ensure_exists_does_not_truncate_a_pre_existing_register(tmp_path):
    """MINOR 10 (round 2): `EvidenceRegister.ensure_exists` was check-then-
    `write_text` — non-atomic and TRUNCATING: two concurrent first-callers
    could both pass the `exists()` check, and the second's `write_text` would
    silently discard whatever the first had already appended. Pre-create the
    register with a row, then call `ensure_exists()` on a FRESH instance
    pointed at the same path — the row must survive. Catches: reverting to
    `if path.exists(): return False` followed by a plain `write_text`."""
    from _claim_register import EvidenceRegister
    path = tmp_path / "evreg_CLAIMS.md"
    path.write_text(
        "# Claims Registry — evreg  (Evidence Register)\n\n"
        "## Research Claims\n"
        "| C-0001 | x_RESEARCH.md:3 | en | no | true | unverified | thought |\n",
        encoding="utf-8")
    created = EvidenceRegister(path, "evreg").ensure_exists()
    assert created is False
    assert "C-0001" in path.read_text(encoding="utf-8")


def test_pre_s6_cycle_without_skeleton_still_records(tmp_path):
    """A cycle registered before S6 has no file yet. Catches: refusing instead of
    writing the skeleton first."""
    rf = tmp_path / "legacy_RESEARCH.md"
    _intake(rf)
    rf.unlink()
    out = rp.record_finding(SID, {"claim": "late.", "source": "https://x"})
    assert out["ok"], out
    assert rp.FINDINGS_HEADER_RE.search(rf.read_text(encoding="utf-8"))


# ── output security stays honest ─────────────────────────────────────────────

def test_append_stamps_an_existing_clean_record_so_promotion_holds(tmp_path):
    """Catches: removing `_stamp_uninspected` — a stale CLEAR record would then
    promote a finding nothing inspected."""
    rf = tmp_path / "o_RESEARCH.md"
    _intake(rf)
    osr.record_produced_findings(str(rf.resolve()), "CLEAR", ())
    assert output_security_hold(rf.resolve())["held"] is False
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["output_security"] == "stamped_degraded"
    assert output_security_hold(rf.resolve())["held"] is True


def test_append_creates_no_record_when_none_exists_absence_already_holds(tmp_path):
    """MAJOR 2 (round 2, replaces MINOR 15's
    `test_append_mints_a_never_inspected_record_when_none_exists` — the mint
    it tested for is REVERTED). MINOR 15 minted a fresh
    `NEVER_SUCCESSFULLY_INSPECTED` record under the CANONICAL spelling when
    none existed, on the reasoning that absence held silently and the
    session-end report needed something to name. The mint itself was the
    defect: it always writes under the CANONICAL path, but the write seam's
    clearing hook files a CLEAR record under whatever spelling the TOOL CALL
    used — routinely the declared, uncanonicalized path — so the minted
    record is never superseded by a later clean inspection, and the file is
    named in the report forever, even after it genuinely inspects clean.
    Absence already holds promotion (`output_security_hold`'s
    `never_inspected` branch reads a missing record the same way a minted one
    would), so nothing needs to be written. Catches: reverting to the mint."""
    rf = tmp_path / "n_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"] is True, out
    assert out["output_security"] == "no_record_held_by_absence", out
    assert osr.read_record(str(rf.resolve())) is None
    assert output_security_hold(rf.resolve())["held"] is True
    assert output_security_hold(rf.resolve())["reason"] == "never_inspected"


def test_stamp_finds_a_record_filed_under_a_symlinked_spelling(tmp_path):
    """MAJOR 7 (FIXER review): a clean record filed under a SYMLINKED spelling
    of the same directory must still be found and stamped.
    `output_security_record._file_slot` hashes the ABSOLUTE spelling the
    WRITE SEAM happened to see — not a realpath-resolved one — so a
    symlinked alias of the file gets its own record under its own key,
    invisible to a plain `read_record(canonical)`. Catches: `_stamp_
    uninspected` looking up only the canonical spelling."""
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)
    sym_rf = linked_dir / "sym_RESEARCH.md"

    _intake(sym_rf)
    state = rp._read_state(SID)
    canonical = state["cycles"]["default"]["research_file_path_canonical"]
    assert canonical == str((real_dir / "sym_RESEARCH.md").resolve())

    osr.record_produced_findings(str(sym_rf), "CLEAR", ())
    # The record is filed under the SYMLINKED spelling (`_file_slot` hashes
    # the absolute, non-realpath-resolved string it is given). A plain
    # lookup under the RESOLVED canonical spelling sees nothing directly —
    # that gap is exactly what the aliasing walk in `_stamp_uninspected`
    # must close; `output_security_hold` itself has no such awareness, so it
    # is not the right predicate to assert "not held" against here.
    assert osr.read_record(canonical) is None
    assert osr.read_record(str(sym_rf)) is not None

    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["output_security"] == "stamped_degraded", out
    aliased = osr.read_record(str(sym_rf))
    assert aliased.get(osr.DEGRADED_SINCE_INSPECTION) is True


def test_output_security_stamp_failure_marks_the_result_not_ok(tmp_path, monkeypatch):
    """MINOR 8: an unstamped output-security boundary is not a success — the
    finding and (if it succeeds) the register entry are still on disk, but
    the boundary's memory of this file is now silently stale. Catches:
    leaving `ok: True` when `_stamp_uninspected` returns an `unstamped: ...`
    status."""
    rf = tmp_path / "unstamp_RESEARCH.md"
    _intake(rf)
    monkeypatch.setattr(rp, "_stamp_uninspected", lambda p: "unstamped: forced failure")
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"] is False
    assert out["output_security"] == "unstamped: forced failure"
    assert out["finding_on_disk"] is True
    assert "c. [stated — https://x]" in rf.read_text(encoding="utf-8")


def test_stamp_write_failure_on_an_existing_record_marks_the_result_not_ok(tmp_path, monkeypatch):
    """MINOR 8 (round 2 — SUCCESS WHITELIST, not a failure-PREFIX check): a
    boundary write failure that is NOT `_stamp_uninspected`'s own exception
    branch — here, `output_security_record.invalidate_on_degraded` itself
    returning `"invalidation_failed"` for an EXISTING record — must also mark
    the result not-ok. The round-1 check (`osec.startswith("unstamped")`)
    read this shape as a SUCCESS, because `"invalidation_failed"` starts with
    neither "unstamped" nor anything else it looked for. Catches: reverting
    to the failure-prefix check."""
    rf = tmp_path / "invfail_RESEARCH.md"
    _intake(rf)
    osr.record_produced_findings(str(rf.resolve()), "CLEAR", ())
    monkeypatch.setattr(osr, "invalidate_on_degraded", lambda p: "invalidation_failed")
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"] is False, out
    assert out["output_security"] == "invalidation_failed"
    assert out["finding_on_disk"] is True


def test_cli_exits_3_when_output_security_boundary_write_fails(tmp_path):
    """MINOR 8 (round 2), the CLI path: an EXISTING record whose re-stamp
    write fails must exit 3, not 0. Deterministic seam: the `findings/`
    directory the CLEAR record was just written into has its write bit
    removed, so the SAME record can still be READ (proving a target exists)
    but `invalidate_on_degraded`'s own atomic write of the re-stamped payload
    fails with a caught `OSError`, returning `"invalidation_failed"` —
    exactly the shape the in-process test above monkeypatches."""
    rf = tmp_path / "osecwritefail_RESEARCH.md"
    _intake(rf)
    osr.record_produced_findings(str(rf.resolve()), "CLEAR", ())
    findings_dir = tmp_path / "osec" / "findings"
    assert findings_dir.is_dir(), "the CLEAR record must have created findings/"
    os.chmod(findings_dir, 0o555)
    try:
        pf = tmp_path / "finding.json"
        pf.write_text(json.dumps({"claim": "c", "source": "https://x"}), encoding="utf-8")
        res = _cli(["record-finding", SID, "--payload-file", str(pf)], _env(tmp_path))
        assert res.returncode == 3, res.stdout + res.stderr
        out = json.loads(res.stdout)
        assert out["ok"] is False, out
        assert out["finding_on_disk"] is True
    finally:
        os.chmod(findings_dir, 0o755)


# ── MAJOR 1/2 (round 2): content dedup survives a line shift ─────────────────

def test_recorded_finding_survives_frontmatter_and_status_shift_without_reharvesting(tmp_path):
    """MAJOR 1 (round 2, replaces the round-1 line-keyed-dedup test): the
    round-1 fix (`_mark_finding_harvested`) marked a line already-harvested by
    its LINE NUMBER — which broke the moment the engine's
    `_append_research_frontmatter` (which prepends its frontmatter block
    above the findings on EVERY fact-checked report) or a hand-inserted
    Status line shifted that number. The fix is CONTENT dedup
    (`ClaimLedger.texts_for_source`, consulted by `on_research_write` before
    lifting): a claim whose text the ledger already holds for this file is
    never lifted twice, regardless of which line it now sits on. Catches:
    reverting to a line-keyed dedup, or dropping the `texts_for_source`
    filter in `on_research_write`."""
    import _claim_harvest_trigger as cht
    import _factcheck_engine as fe
    rf = tmp_path / "shift_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "One fact that must not double-count.",
                                  "source": "https://x.example"})
    assert out["ok"], out

    # The engine's own writeback — prepends frontmatter ABOVE the findings on
    # every fact-checked report, shifting every existing line downward.
    fe._append_research_frontmatter(
        str(rf), [{"cycle": "default", "verdict": "PASS", "rounds": 1}])

    # A verified Status line, inserted near the top — the second, independent
    # shift this fix has to survive.
    shifted = ("**Status:** ✅ VERIFIED via /double-check — PASS\n\n"
               + rf.read_text(encoding="utf-8"))
    rf.write_text(shifted, encoding="utf-8")

    # Confirm the fixture actually shifted the line — otherwise this test
    # would prove nothing about surviving a shift.
    new_line_no = rf.read_text(encoding="utf-8").splitlines().index(
        "- One fact that must not double-count. [stated — https://x.example]") + 1
    assert new_line_no != out["line"], "the fixture did not actually shift lines"

    osr.record_produced_findings(str(rf.resolve()), "CLEAR", ())
    result = cht.on_research_write(str(rf.resolve()))
    assert not result["skipped"], result
    assert result["harvested"] == [], result
    rows = _register_rows(out["register"])
    assert len(rows) == 1


def test_claim_ledger_texts_for_source_matches_across_symlinked_path_spellings(tmp_path):
    """MAJOR 1 (round 2): `ClaimLedger.texts_for_source` must recognise ONE
    file addressed through two different spellings — a symlinked directory is
    exactly how a recorder's canonical path and a harvest's own spelling can
    differ for the same file on disk. Catches: comparing anchor paths
    literally instead of through `os.path.realpath`."""
    from _claim_engine import Anchor, Claim, ClaimFlags, ClaimRole, CRITERIA
    from _claim_ledger import ClaimLedger

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)

    real_rf = real_dir / "x_RESEARCH.md"
    linked_rf = linked_dir / "x_RESEARCH.md"

    ledger = ClaimLedger(tmp_path / "x.claims.ledger.jsonl")
    flags = ClaimFlags.from_dict({c: True for c in CRITERIA})
    claim = Claim(text="A fact recorded once.",
                  anchor=Anchor.local_file(str(real_rf), 5),
                  flags=flags, role=ClaimRole.BACKWARD, lang="en")
    ledger.record_extracted(claim, checked_at="t0")

    assert ledger.texts_for_source(str(linked_rf)) == {"A fact recorded once."}
    assert ledger.texts_for_source(str(real_rf)) == {"A fact recorded once."}
    assert ledger.texts_for_source(str(real_dir / "other_RESEARCH.md")) == set()


def test_claim_ledger_texts_for_source_excludes_retracted_claims(tmp_path):
    """A retracted claim's text must not suppress re-harvesting — a retraction
    means the claim no longer holds, so the same text reappearing is not a
    duplicate of something still live."""
    from _claim_engine import Anchor, Claim, ClaimFlags, ClaimRole, CRITERIA
    from _claim_ledger import ClaimLedger

    rf = tmp_path / "y_RESEARCH.md"
    ledger = ClaimLedger(tmp_path / "y.claims.ledger.jsonl")
    flags = ClaimFlags.from_dict({c: True for c in CRITERIA})
    claim = Claim(text="A retracted fact.", anchor=Anchor.local_file(str(rf), 3),
                  flags=flags, role=ClaimRole.BACKWARD, lang="en")
    cid = ledger.record_extracted(claim, checked_at="t0")
    ledger.record_retracted(cid, checked_at="t1", reason="wrong")
    assert ledger.texts_for_source(str(rf)) == set()


# ── MAJOR 3: a finding lands at the end of the LAST Findings section ─────────

def test_finding_recorded_after_insecure_sources_section_lands_inside_findings(tmp_path):
    """MAJOR 3: a finding recorded after `## Insecure-input sources` was
    appended (Closing step 3 of a prior run) must land INSIDE the Findings
    section, before that later section — never after it, where
    `merge_insecure_sources_section`'s next re-render would delete it.
    Catches: appending at end-of-file instead of at the end of the LAST
    Findings section."""
    rf = tmp_path / "insec_RESEARCH.md"
    _intake(rf)
    with open(rf, "a", encoding="utf-8") as fh:
        fh.write("\n## Insecure-input sources\n\nNothing recorded.\n")
    out = rp.record_finding(SID, {"claim": "Landed after insecure section.",
                                  "source": "https://x.example"})
    assert out["ok"], out
    text = rf.read_text(encoding="utf-8")
    finding_idx = text.index("Landed after insecure section.")
    section_idx = text.index("## Insecure-input sources")
    assert finding_idx < section_idx
    merged = osr.merge_insecure_sources_section(text)
    assert "Landed after insecure section." in merged


def test_finding_recorded_after_synthesis_section_lands_inside_findings(tmp_path):
    """MAJOR 3: as above, for a `## ... — Synthesis` section left by a prior
    day's run — the finding must land BEFORE it, not after."""
    rf = tmp_path / "synth_RESEARCH.md"
    _intake(rf)
    with open(rf, "a", encoding="utf-8") as fh:
        fh.write("\n## 2026-09-29 — Synthesis\n\nEarlier synthesis text.\n")
    out = rp.record_finding(SID, {"claim": "Landed after synthesis.",
                                  "source": "https://x.example"})
    assert out["ok"], out
    text = rf.read_text(encoding="utf-8")
    assert text.index("Landed after synthesis.") < text.index("## 2026-09-29 — Synthesis")


def test_second_dated_findings_section_gets_its_own_topic_heading(tmp_path):
    """MAJOR 3 / untested mutation 6: the 'last topic' scan is scoped to the
    LAST Findings section only — a topic heading in an EARLIER Findings
    section must not suppress a fresh heading in a later one."""
    rf = tmp_path / "twoday_RESEARCH.md"
    _intake(rf)
    out1 = rp.record_finding(SID, {"claim": "Day one fact.", "source": "https://x.example",
                                   "topic": "pricing"})
    assert out1["ok"], out1
    rp.write_findings_skeleton(rf, {"angles": ["a"]}, today="2026-10-01")
    out2 = rp.record_finding(SID, {"claim": "Day two fact.", "source": "https://x.example",
                                   "topic": "pricing"})
    assert out2["ok"], out2
    text = rf.read_text(encoding="utf-8")
    assert text.count("### pricing") == 2


def test_finding_lands_after_a_fenced_heading_shaped_line_inside_findings(tmp_path):
    """MINOR 12 (round 2): a fenced code block quoting a '## '-shaped line
    inside the Findings section must NOT be read as the section's end — the
    finding must land AFTER the fence, still inside the same section, not
    truncated at the fenced line. Catches: a naive `re.search(r"^## ", ...)`
    with no fence awareness."""
    rf = tmp_path / "fence_RESEARCH.md"
    _intake(rf)
    with open(rf, "a", encoding="utf-8") as fh:
        fh.write("\n```\n## This looks like a heading but is quoted\n```\n")
    out = rp.record_finding(SID, {"claim": "Landed after the fence.",
                                  "source": "https://x.example"})
    assert out["ok"], out
    text = rf.read_text(encoding="utf-8")
    fence_idx = text.index("```\n## This looks like a heading")
    finding_idx = text.index("Landed after the fence.")
    assert finding_idx > fence_idx


def test_crlf_research_file_receives_a_crlf_splice(tmp_path):
    """MINOR 12 (round 2): a CRLF-line-ended file's Findings header must still
    match `FINDINGS_HEADER_RE` (accepts an optional trailing '\\r'), and the
    recorder's own splice must use '\\r\\n' too, so the inserted line does
    not become the one place in the file mixing endings. Catches: a bare
    `[ \\t]*$` header regex (misses CRLF), or a splice hardcoded to '\\n'
    (which used to also silently collapse the WHOLE file to LF on any save,
    since Python's default text-mode read/write translates line endings)."""
    today = rp._today()
    rf = tmp_path / "crlf_RESEARCH.md"
    header = "## {d} — Findings".format(d=today)
    rf.write_bytes(
        (header + "\r\n\r\n- old [stated — https://a.example]\r\n").encode("utf-8"))
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"], out
    raw = rf.read_bytes()
    assert "\r\n- c. [stated — https://x]\r\n".encode("utf-8") in raw
    # No bare '\n' survives that is not part of a '\r\n' pair.
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_line_number_counts_newlines_not_splitlines_separators(tmp_path):
    """MINOR 13 (round 2): a prior line containing U+2028 LINE SEPARATOR —
    which `str.splitlines()` treats as an extra line break that a '\\n'-based
    reader (an editor, `grep -n`, the register's own locator convention) does
    not — must not shift the recorded line number. Catches: computing
    `line_no` (or the topic-heading scan) via `.splitlines()` instead of a
    '\\n'-only count."""
    rf = tmp_path / "u2028prior_RESEARCH.md"
    rf.write_text("Prior line with a   separator inside it.\n", encoding="utf-8")
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"], out
    full_text = rf.read_text(encoding="utf-8")
    expected_line_no = full_text.split("\n").index(
        "- c. [stated — https://x]") + 1
    assert out["line"] == expected_line_no, (out["line"], expected_line_no)


# ── MAJOR 5: a brand-new skeleton file gets a Parent: line ───────────────────

def test_new_skeleton_file_gets_a_parent_line_when_a_spine_exists(tmp_path):
    """MAJOR 5: creating the skeleton with no `Parent:` line when a spine
    exists is what `bookkeeping_invariant`'s G3 rejects on the family's next
    write. Catches: `write_findings_skeleton` never minting one."""
    thoughts = tmp_path / "Thoughts"
    thoughts.mkdir()
    spine = thoughts / "widget-choice_THOUGHT.md"
    spine.write_text("# Widget Choice\n\n# Discovery\n", encoding="utf-8")
    rf = thoughts / "widget-choice_RESEARCH.md"
    rp.write_findings_skeleton(rf, {"angles": ["a"]}, today="2026-09-30")
    text = rf.read_text(encoding="utf-8")
    assert text.startswith("Parent: [[widget-choice_THOUGHT]]\n\n")

    import bookkeeping_invariant as bi
    member = bi.classify(rf.name)
    family = bi.read_family(str(thoughts), member.slug)
    violations = bi.evaluate_family(member, family)
    assert not any(v.case == "G3" for v in violations)


def test_existing_skeleton_file_gets_no_parent_line_rewrite(tmp_path):
    """MAJOR 5 (round 2, WITH a real spine — the round-1 test had none, so it
    could not fail: `_findings_skeleton_parent_line` returns "" for a file
    with no in-family spine regardless of whether the file is new or
    existing, so the old fixture could not distinguish "correctly skipped
    because the file existed" from "correctly skipped because there was
    nothing to mint anyway"). With a matching spine present, an EXISTING
    file's Parent status must still be left alone — the Parent line is
    minted only on CREATION, never injected into a file that already existed
    before this cycle's skeleton call. Catches: `write_findings_skeleton`
    minting a Parent line into an already-existing file."""
    thoughts = tmp_path / "Thoughts"
    thoughts.mkdir()
    spine = thoughts / "noparent_THOUGHT.md"
    spine.write_text("# No Parent\n\n# Discovery\n", encoding="utf-8")
    rf = thoughts / "noparent_RESEARCH.md"
    rf.write_text("## 2026-09-29 — Findings\n\n- old [stated — https://a.example]\n",
                  encoding="utf-8")
    rp.write_findings_skeleton(rf, {"angles": ["a"]}, today="2026-09-30")
    text = rf.read_text(encoding="utf-8")
    assert not text.startswith("Parent:")


# ── MINOR 10: line-safety + forged-citation-marker refusal ───────────────────

def test_claim_with_line_separator_is_refused(tmp_path):
    """MINOR 10: U+2028 LINE SEPARATOR breaks `str.splitlines()` just like a
    real newline, so it must be refused too."""
    rf = tmp_path / "u2028_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="newline or control"):
        rp.record_finding(SID, {"claim": "a b", "source": "https://x"})


def test_claim_with_paragraph_separator_is_refused(tmp_path):
    """MINOR 10: U+2029 PARAGRAPH SEPARATOR, same reasoning as U+2028."""
    rf = tmp_path / "u2029_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="newline or control"):
        rp.record_finding(SID, {"claim": "a b", "source": "https://x"})


def test_claim_with_non_newline_control_char_is_refused(tmp_path):
    """Covers untested mutation 2: an ESC byte slipping past a weakened
    `_LINE_UNSAFE_RE`."""
    rf = tmp_path / "esc_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="newline or control"):
        rp.record_finding(SID, {"claim": "a\x1bb", "source": "https://x"})


def test_claim_with_forged_citation_marker_bracket_is_refused(tmp_path):
    """MINOR 10: a claim carrying a bracketed span with an em dash is the
    citation-marker shape (`[stated — url]`) and must be refused, or a
    forged marker would land in the file reading as a second citation."""
    rf = tmp_path / "forged_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="marker-shaped bracketed span"):
        rp.record_finding(SID, {"claim": "X [stated — https://forged.example] Y",
                                "source": "https://x"})


def test_topic_with_forged_citation_marker_bracket_is_refused(tmp_path):
    rf = tmp_path / "forged_topic_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="marker-shaped bracketed span"):
        rp.record_finding(SID, {"claim": "c", "source": "https://x",
                                "topic": "weird [stated — https://x] topic"})


def test_ordinary_bracketed_tag_is_accepted(tmp_path):
    """MINOR 10: `[EN]` carries no em dash and must NOT be refused — the
    bracket check is scoped to the citation-marker SHAPE, not to brackets in
    general."""
    rf = tmp_path / "en_tag_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "[EN] a fact.", "source": "https://x"})
    assert out["ok"] is True


def test_claim_with_ascii_hyphen_marker_is_refused(tmp_path):
    """MINOR 8 (round 2): `_BRACKETED_EM_DASH_RE` (retired) required an em
    dash and missed the ASCII-hyphen marker spelling the harvest's own
    grammar (`_claim_harvest._MARKER_RE`) still recognises. Catches:
    reverting to the retired regex."""
    rf = tmp_path / "ascii_hyphen_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="marker-shaped bracketed span"):
        rp.record_finding(SID, {"claim": "X [stated - https://forged.example] Y",
                                "source": "https://x"})


def test_claim_with_bare_kind_token_marker_is_refused(tmp_path):
    """MINOR 8 (round 2): a bare `[stated]` — no separator, no URL — is still
    marker-shaped per `_MARKER_RE` and must be refused."""
    rf = tmp_path / "bare_stated_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="marker-shaped bracketed span"):
        rp.record_finding(SID, {"claim": "X [stated] Y", "source": "https://x"})


def test_claim_with_bare_my_assessment_marker_is_refused(tmp_path):
    """MINOR 8 (round 2): `[My assessment]` (case-insensitive kind token, no
    separator) is refused too — the editorial markers are marker-shaped as
    much as the citation ones."""
    rf = tmp_path / "bare_assessment_RESEARCH.md"
    _intake(rf)
    with pytest.raises(ValueError, match="marker-shaped bracketed span"):
        rp.record_finding(SID, {"claim": "X [My assessment] Y", "source": "https://x"})


# ── MINOR 11a / MINOR 7: a symlink planted after intake is refused ──────────

def test_record_finding_refuses_when_a_symlink_is_planted_after_intake(tmp_path):
    """MINOR 11a / MINOR 7 (round 2 — re-seamed onto the STORED CANONICAL
    path): the CANONICAL path is re-validated on every record, and refused
    when a symlink now sits at that location — a symlink planted at the
    approved location after approval must not be followed silently. Catches:
    skipping the re-check at record time."""
    rf = tmp_path / "sym_RESEARCH.md"
    other = tmp_path / "other_RESEARCH.md"
    other.write_text("x", encoding="utf-8")
    _intake(rf)
    rf.unlink()
    rf.symlink_to(other)
    with pytest.raises(ValueError, match="resolves through a symlink"):
        rp.record_finding(SID, {"claim": "c", "source": "https://x"})


def test_record_finding_survives_a_cwd_change_since_intake(tmp_path, monkeypatch):
    """MINOR 7 (round 2): re-validating the DECLARED path (usually workspace-
    relative) at record time re-resolved it against WHATEVER CWD the process
    happened to have NOW — a CWD change between intake and record then
    refused every finding for a reason that had nothing to do with safety.
    The fix re-checks the STORED CANONICAL path instead, which does not
    depend on CWD at all. Catches: reverting to re-validating `declared`."""
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    monkeypatch.chdir(proj_dir)
    rp.cmd_advance(SID, "r0_intake", _approved_payload("relative_RESEARCH.md"), None)
    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()
    monkeypatch.chdir(other_dir)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"], out
    assert (proj_dir / "relative_RESEARCH.md").exists()


# ── untested mutations U1 / U4 / U5 / U7 / U8 ─────────────────────────────────

def test_findings_lang_follows_the_cycle_id_convention(tmp_path):
    """U1: `_findings_lang` is keyed on the CYCLE, not hardcoded to 'en' — a
    `de` cycle's register row must carry lang 'de'."""
    rf = tmp_path / "de_RESEARCH.md"
    rp.cmd_advance(SID, "r0_intake", _approved_payload(rf), None, cycle_id="de")
    out = rp.record_finding(SID, {"claim": "Ein Fakt.", "source": "https://x.example"},
                            cycle_id="de")
    assert out["ok"], out
    rows = _register_rows(out["register"])
    assert rows[0]["lang"] == "de"


def test_record_finding_falls_back_to_revalidating_when_canonical_missing(tmp_path):
    """U4: a legacy cycle with no stored `research_file_path_canonical` (a
    pre-S5 manifest) still records to the validated path."""
    rf = tmp_path / "legacy2_RESEARCH.md"
    _intake(rf)
    state = rp._read_state(SID)
    del state["cycles"]["default"]["research_file_path_canonical"]
    rp._write_state(SID, state, None)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"], out
    assert out["research_file"] == str(rf.resolve())


def test_record_finding_refuses_a_declared_path_that_now_fails_validation(tmp_path):
    """U4: a declared path that fails `validate_research_file_path` at record
    time (here: it now escapes its own directory through a symlink) is
    refused, not silently written elsewhere."""
    import tempfile as _tempfile
    rf = tmp_path / "escape_RESEARCH.md"
    _intake(rf)
    state = rp._read_state(SID)
    del state["cycles"]["default"]["research_file_path_canonical"]
    rp._write_state(SID, state, None)
    rf.unlink()
    outside_dir = Path(_tempfile.mkdtemp())
    outside = outside_dir / "outside_RESEARCH.md"
    outside.write_text("x", encoding="utf-8")
    rf.symlink_to(outside)
    with pytest.raises(ValueError, match="resolves through a symlink"):
        rp.record_finding(SID, {"claim": "c.", "source": "https://x"})


def test_declare_written_declares_research_file_register_and_ledger(tmp_path, monkeypatch):
    """U5: `_declare_written` must be called for the research file, the
    register AND the ledger — not merely one of the three. Catches:
    monkeypatching `commit_scope.record_write` (the name `_declare_written`
    imports at call time) to capture calls."""
    calls = []
    import commit_scope
    monkeypatch.setattr(commit_scope, "record_write",
                        lambda p: calls.append(os.path.basename(str(p))))
    rf = tmp_path / "declare_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"], out
    assert "declare_RESEARCH.md" in calls
    assert "declare_CLAIMS.md" in calls
    assert "declare.claims.ledger.jsonl" in calls


def test_register_row_marks_faithful_true(tmp_path):
    """U7: the recorded row's `faithful` flag must read true."""
    rf = tmp_path / "faithful_RESEARCH.md"
    _intake(rf)
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    assert out["ok"], out
    rows = _register_rows(out["register"])
    assert rows[0]["faithful"] == "true"


def test_skeleton_starts_on_its_own_line_when_prior_file_lacks_trailing_newline(tmp_path):
    """U8: a prior file with no trailing newline must not run the new
    heading onto the same line as the file's last content."""
    rf = tmp_path / "notrail_RESEARCH.md"
    rf.write_text("Some prior content with no trailing newline", encoding="utf-8")
    rp.write_findings_skeleton(rf, {"angles": ["a"]}, today="2026-09-30")
    text = rf.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "Some prior content with no trailing newline"
    assert lines[1] == ""
    assert lines[2] == "## 2026-09-30 — Topics"


# ── the CLI ──────────────────────────────────────────────────────────────────

def _cli(args, env):
    return subprocess.run([sys.executable, str(HOOKS_DIR / "research_pipeline.py")] + args,
                          capture_output=True, text=True, env=env)


def _env(tmp_path):
    env = dict(os.environ)
    env["RP_STATE_DIR"] = str(tmp_path / "rp_state")
    env["OUTPUT_SECURITY_TRAIL_DIR"] = str(tmp_path / "osec")
    return env


def test_cli_record_finding_round_trip(tmp_path):
    rf = tmp_path / "cli_RESEARCH.md"
    _intake(rf)
    pf = tmp_path / "finding.json"
    pf.write_text(json.dumps({"claim": "It's what they don't say for sure.",
                              "source": "https://x.example"}), encoding="utf-8")
    res = _cli(["record-finding", SID, "--payload-file", str(pf)], _env(tmp_path))
    assert res.returncode == 0, res.stdout + res.stderr
    assert json.loads(res.stdout)["ok"] is True
    assert "It's what they don't say for sure." in rf.read_text(encoding="utf-8")


def test_cli_has_no_positional_payload_form(tmp_path):
    res = _cli(["record-finding", SID, '{"claim":"c","source":"s"}'], _env(tmp_path))
    assert res.returncode == 2


def test_cli_exits_nonzero_on_refusal(tmp_path):
    pf = tmp_path / "finding.json"
    pf.write_text(json.dumps({"claim": "c", "source": "https://x"}), encoding="utf-8")
    res = _cli(["record-finding", SID, "--payload-file", str(pf)], _env(tmp_path))
    assert res.returncode == 1
    assert json.loads(res.stdout)["ok"] is False


def test_cli_exits_3_when_register_write_fails_because_its_path_is_a_directory(tmp_path):
    """Covers untested mutation 3: exit 3 for a REGISTER failure through the
    CLI, not only via in-process monkeypatch. Catches: `main()` returning 0
    on `ok: False`."""
    rf = tmp_path / "regdir_RESEARCH.md"
    _intake(rf)
    (tmp_path / "regdir_CLAIMS.md").mkdir()
    pf = tmp_path / "finding.json"
    pf.write_text(json.dumps({"claim": "c", "source": "https://x"}), encoding="utf-8")
    res = _cli(["record-finding", SID, "--payload-file", str(pf)], _env(tmp_path))
    assert res.returncode == 3, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["ok"] is False
    assert out["finding_on_disk"] is True


def test_cli_record_finding_reports_oserror_as_json_not_traceback(tmp_path):
    """MINOR 9: a filesystem OSError not caught anywhere inside
    `record_finding` (here: the research file was replaced with a directory)
    must come back as clean `{"ok": false}` JSON, not a raw Python traceback."""
    rf = tmp_path / "dir_RESEARCH.md"
    _intake(rf)
    rf.unlink()
    rf.mkdir()
    pf = tmp_path / "finding.json"
    pf.write_text(json.dumps({"claim": "c", "source": "https://x"}), encoding="utf-8")
    res = _cli(["record-finding", SID, "--payload-file", str(pf)], _env(tmp_path))
    assert res.returncode == 1, res.stdout + res.stderr
    out = json.loads(res.stdout)
    assert out["ok"] is False
    assert "error" in out


def test_concurrent_recorders_each_anchor_their_own_line(tmp_path):
    """Parallel tool calls are ordinary. Catches: computing the line number or
    appending outside the per-file lock.

    MAJOR 4b (FIXER review): drains each process's stdout/stderr on its OWN
    thread, concurrently, rather than the original sequential `communicate()`
    loop. Sequential draining while 8 processes run concurrently is the
    textbook subprocess-pipe deadlock the stdlib docs warn about: once the
    S6 fix serializes the register write behind a second lock (item 4b), a
    process can sit blocked on the file lock — and while the parent's
    `communicate()` is occupied waiting on an EARLIER process in the list, a
    LATER process's own stdout/stderr pipes are not being read at all, and a
    process holding both locks can itself block on a full pipe with nothing
    downstream to drain it. Reproduced directly against this exact test
    (confirmed via `sample`/`lsof`: the stuck process was blocked in a
    `write()` syscall to its own pipe, not in the file lock). Concurrent
    per-process draining is the documented fix and changes nothing about
    what is asserted below."""
    import threading
    rf = tmp_path / "par_RESEARCH.md"
    _intake(rf)
    env = _env(tmp_path)
    procs = []
    for i in range(8):
        pf = tmp_path / "f{i}.json".format(i=i)
        pf.write_text(json.dumps({"claim": "Claim number {i}.".format(i=i),
                                  "source": "https://x.example/{i}".format(i=i)}),
                      encoding="utf-8")
        procs.append(subprocess.Popen(
            [sys.executable, str(HOOKS_DIR / "research_pipeline.py"),
             "record-finding", SID, "--payload-file", str(pf)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env))

    outputs = [None] * len(procs)

    def _drain(idx, proc):
        outputs[idx] = proc.communicate(timeout=60)

    drain_threads = [threading.Thread(target=_drain, args=(i, p))
                     for i, p in enumerate(procs)]
    for t in drain_threads:
        t.start()
    for t in drain_threads:
        t.join(timeout=65)

    results = []
    for p, out_err in zip(procs, outputs):
        assert out_err is not None, "a drain thread did not finish"
        out, err = out_err
        assert p.returncode == 0, out + err
        results.append(json.loads(out))
    lines = rf.read_text(encoding="utf-8").splitlines()
    for k, r in enumerate(results):          # results are in launch order
        assert lines[r["line"] - 1] == \
            "- Claim number {k}. [stated — https://x.example/{k}]".format(k=k)
    rows = _register_rows(results[0]["register"])
    assert len(rows) == 8
    assert len({r["cid"] for r in rows}) == 8


def test_the_file_lock_serialises_read_and_append(tmp_path, monkeypatch):
    """Deterministic twin of the concurrency test above, which the S6 mutation
    check proved could NOT catch a removed lock (the race window is too narrow
    to hit reliably).

    Re-seamed onto `os.replace` (round 2 fix — the recorder's splice never
    calls `open(..., "a")`; it reads the whole file, then writes a temp file
    via `os.fdopen` and swaps it in with `os.replace`. The old `slow_open`
    patch above matched NOTHING in that path, so this test passed even with
    the lock removed — exactly the false confidence a mutation check exists
    to catch, and did.

    Here `os.replace` — the very last thing the critical section does — is
    slowed on purpose. With the lock, the WHOLE read-compute-write section is
    one critical section, so the second recorder's read cannot start until
    the first recorder's slowed `os.replace` has returned; both line numbers
    come out right. Without the lock, the second recorder's read can land
    while the first recorder is still inside its slowed `os.replace`, so it
    reads the file BEFORE the first recorder's finding lands and computes the
    SAME line number. Catches: taking the splice out of `_ResearchFileLock`
    in `record_finding`."""
    import threading
    import time

    rf = tmp_path / "lock_RESEARCH.md"
    _intake(rf)
    real_replace = rp.os.replace

    def slow_replace(src, dst):
        if str(dst).endswith("_RESEARCH.md"):
            time.sleep(0.3)
        return real_replace(src, dst)

    monkeypatch.setattr(rp.os, "replace", slow_replace)
    results = {}

    def run(i):
        results[i] = rp.record_finding(
            SID, {"claim": "Threaded {i}.".format(i=i), "source": "https://t.example"})

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    lines = rf.read_text(encoding="utf-8").splitlines()
    assert results[0]["line"] != results[1]["line"]
    for i in range(2):
        assert lines[results[i]["line"] - 1] == \
            "- Threaded {i}. [stated — https://t.example]".format(i=i)


def test_record_finding_shares_the_engine_lock_on_the_research_file(tmp_path):
    """MINOR 6 (round 2): the recorder's research-file lock must be the SAME
    sibling `<file>.lock` `_factcheck_engine._append_research_frontmatter`
    takes — not a second, independent lock keyed some other way. Holding the
    engine's own lock on another thread must block `record_finding` until it
    is released; a differently-keyed lock would let `record_finding` proceed
    immediately regardless. Catches: keying `_ResearchFileLock` on anything
    other than `path.with_suffix(path.suffix + ".lock")`."""
    import fcntl
    import threading
    import time

    rf = tmp_path / "sharedlock_RESEARCH.md"
    _intake(rf)
    lockpath = Path(str(rf.resolve()) + ".lock")

    HOLD_SECONDS = 1.2  # generous margin over any ordinary record_finding overhead

    def hold_engine_lock():
        with open(lockpath, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            time.sleep(HOLD_SECONDS)
            fcntl.flock(fh, fcntl.LOCK_UN)

    holder = threading.Thread(target=hold_engine_lock)
    holder.start()
    time.sleep(0.1)  # let the holder acquire first
    start = time.monotonic()
    out = rp.record_finding(SID, {"claim": "c.", "source": "https://x"})
    elapsed = time.monotonic() - start
    holder.join(timeout=10)

    assert out["ok"], out
    # A DURATION threshold, not an absolute-time comparison: this isolates
    # "did THIS call block for close to the full hold", independent of
    # whatever ordinary overhead record_finding has on its own. Blocked:
    # elapsed is close to HOLD_SECONDS minus the 0.1s head start (~1.1s).
    # Not blocked (a differently-keyed lock): elapsed is the call's own
    # ordinary overhead, a small fraction of a second.
    assert elapsed >= HOLD_SECONDS - 0.3, (
        "record_finding did not block for close to the full hold ({e:.2f}s "
        "elapsed, expected close to {h}s) — it is not locking the same "
        "sibling file the engine locks".format(e=elapsed, h=HOLD_SECONDS))


# ── untested mutation: the REGISTER lock (MAJOR 4b) ──────────────────────────

def test_two_research_files_sharing_one_register_both_land_concurrently(tmp_path, monkeypatch):
    """Untested mutation (removing the register lock in `record_finding`):
    two DIFFERENT research files that share ONE register — verified via
    `claims_registry.register_address` to resolve to the same `_CLAIMS.md`
    (`foo_RESEARCH.md` and `foo-<14 digits>_RESEARCH.md` share slug `foo`) —
    record concurrently, and both rows must land.

    The register's own append (`_claim_register.EvidenceRegister.add`,
    `open(path, "a", ...)`) is slowed on purpose so the two calls genuinely
    overlap in time."""
    import builtins
    import claims_registry as cr
    import threading
    import time

    rf_a = tmp_path / "foo_RESEARCH.md"
    rf_b = tmp_path / "foo-20260101010101_RESEARCH.md"
    assert cr.register_address(str(rf_a)).path == cr.register_address(str(rf_b)).path, (
        "fixture assumption broken: these two names must share one register")

    _intake(rf_a, cycle_id="a")
    _intake(rf_b, cycle_id="b")

    real_open = builtins.open

    def slow_open(file, mode="r", *a, **k):
        if "a" in mode and str(file).endswith("_CLAIMS.md"):
            time.sleep(0.2)
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", slow_open)
    results = {}

    def run(key, cycle_id):
        results[key] = rp.record_finding(
            SID, {"claim": "Claim {k}.".format(k=key),
                  "source": "https://x.example/{k}".format(k=key)},
            cycle_id=cycle_id)

    threads = [threading.Thread(target=run, args=("a", "a")),
              threading.Thread(target=run, args=("b", "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert results["a"]["ok"] and results["b"]["ok"], results
    register_path = results["a"]["register"]
    assert register_path == results["b"]["register"]
    rows = _register_rows(register_path)
    assert len(rows) == 2
    assert {r["cid"] for r in rows} == {results["a"]["claim_id"], results["b"]["claim_id"]}


# ── finding 17: the engine creates the register before its topic raise ──────

def test_engine_creates_register_before_no_active_topic_raise(tmp_path):
    """Catches: moving the `ensure_exists` block back below the
    `No active topic` raise in `_factcheck_engine.factcheck_run`."""
    import _factcheck_engine as fe
    rf = tmp_path / "engine-unbound_RESEARCH.md"
    rf.write_text("## 2026-09-30 — Findings\n\n- c [stated — https://x]\n",
                  encoding="utf-8")
    with pytest.raises(ValueError, match="No active topic"):
        fe.factcheck_run(str(tmp_path / "fc_state"), str(rf), "research", SID,
                         _proj_topic_resolver=lambda s: (None, None, None),
                         _checker_fn=lambda *a, **k: "VERDICT: PASS")
    assert (tmp_path / "engine-unbound_CLAIMS.md").is_file()


def test_engine_creates_no_register_for_an_unbound_thought_kind_session(tmp_path):
    """MINOR 14: the relocation that fixed the research-kind case above must
    not ALSO create a register for an unbound `thought`-kind session — that
    kind's register creation stays gated on topic resolution succeeding, its
    pre-S6-relocation position. Catches: calling the shared local register-
    creation closure unconditionally (for both kinds) before the raise."""
    import _factcheck_engine as fe
    tf = tmp_path / "engine-unbound_THOUGHT.md"
    tf.write_text("# Discovery\n", encoding="utf-8")
    with pytest.raises(ValueError, match="No active topic"):
        fe.factcheck_run(str(tmp_path / "fc_state"), str(tf), "thought", SID,
                         _proj_topic_resolver=lambda s: (None, None, None),
                         _checker_fn=lambda *a, **k: "VERDICT: PASS")
    assert not (tmp_path / "engine-unbound_CLAIMS.md").exists()

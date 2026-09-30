"""DS8 Row 1 — orphan plain-* topic-state reaper (quarantine, reversible)."""
import json
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import reap_orphan_plain_topics as reaper  # noqa: E402


def _state(d, name, payload):
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


def test_finds_only_orphans(tmp_path):
    d = tmp_path / "pre_plan_gates"; d.mkdir()
    _state(d, "plain-021c1abf__Projects.json", {"topic_slug": "plain-021c1abf", "thought_file_path": None})
    _state(d, "Projects__plain-241316b4.json", {"topic_slug": "Projects", "thought_file_path": None})
    _state(d, "real-topic__Projects.json", {"topic_slug": "real-topic", "thought_file_path": None})
    # a plain- file that DOES resolve to a spine -> not an orphan
    spine = tmp_path / "x_THOUGHT.md"; spine.write_text("# x\n")
    _state(d, "plain-deadbeef__Projects.json", {"topic_slug": "plain-deadbeef", "thought_file_path": str(spine)})
    _state(d, "_active.json", {"sess": {"topic_slug": "plain-021c1abf", "active_project": "Projects"}})

    names = {os.path.basename(p) for p in reaper.find_orphans(str(d))}
    assert names == {"plain-021c1abf__Projects.json", "Projects__plain-241316b4.json"}


def test_apply_quarantines_and_is_idempotent(tmp_path):
    d = tmp_path / "pre_plan_gates"; d.mkdir()
    _state(d, "plain-021c1abf__Projects.json", {"topic_slug": "plain-021c1abf", "thought_file_path": None})

    reaped = reaper.reap(str(d), apply=True)
    assert len(reaped) == 1
    assert not (d / "plain-021c1abf__Projects.json").exists()             # moved out
    assert (d / "_reaped" / "plain-021c1abf__Projects.json").exists()     # quarantined (reversible)
    # _active.json untouched (not present here); second run finds nothing.
    assert reaper.reap(str(d), apply=True) == []


def test_dry_run_moves_nothing(tmp_path):
    d = tmp_path / "pre_plan_gates"; d.mkdir()
    _state(d, "plain-021c1abf__Projects.json", {"topic_slug": "plain-021c1abf", "thought_file_path": None})
    reaper.reap(str(d), apply=False)
    assert (d / "plain-021c1abf__Projects.json").exists()                 # untouched
    assert not (d / "_reaped").exists()


# --------------------------------------------------------------------------- #
# S5/A6 — find_body_mismatch(): the fourth reporter
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

ppg = reaper._import_ppg()


@pytest.fixture
def shapes(tmp_path, monkeypatch):
    """One record of every shape A6 names, in a state dir isolated from live.

    ISOLATION IS ASSERTED, NOT ASSUMED (the plan's own rule: "an isolation you
    have not asserted is an isolation you do not have"). These tests are green
    only under the shim HOME that `plan_env.sh gate` exports; run bare, they
    would import the LIVE module, which has no `find_body_mismatch` at all.

    THE FIXTURE MUST SPAN THE CLASSIFIER'S BUCKETS. An earlier version gave no
    record a `thought_file_path`, so `_resolve_spine_abs` returned None for
    every one and the classifier bucketed all of them `unresolvable`. The
    span-the-buckets test was then vacuous — its containment assertion held by
    construction, and would have held for an implementation returning `[]`, or
    one that walked `find_unresolvable()` alone and missed every resolvable
    record. Records 9 and 10 below carry REAL spines for exactly that reason.
    """
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        # REALPATH on both sides. The shim HOME reaches the clone through a
        # `.claude` SYMLINK, so `__file__` is the symlink path and a raw
        # `startswith` compares the wrong string — which is why `plan_env.sh`'s
        # own isolation proof compares `p.resolve()` to `want.resolve()`.
        got = os.path.realpath(ppg.__file__)
        want = os.path.realpath(os.path.join(cfg, "hooks", "pre_plan_gates.py"))
        assert got == want, (
            f"NOT the module under test: imported {got}, expected {want}. The shim "
            f"HOME is not in effect — run via `plan_env.sh gate`.")
    assert hasattr(ppg, "find_body_mismatch"), (
        f"imported {ppg.__file__}, which has no find_body_mismatch — this is the "
        f"LIVE module, not the clone under test")

    d = tmp_path / "pre_plan_gates"; d.mkdir()

    # 1. halves AGREE with the filename -> neither bucket
    _state(d, "alpha__Root.json",
           {"topic_slug": "alpha", "project_slug": "Root", "project_root": "/x/Root"})
    # 2. MISMATCH carrying the cwd fingerprint (topic half == basename(project_root))
    _state(d, "audit-thing__Root.json",
           {"topic_slug": "Projects", "project_slug": "Root",
            "project_root": "$CLAUDE_PROJECT_DIR"})
    # 3. MISMATCH with NO fingerprint (project_root disagrees with the topic half)
    _state(d, "beta__Root.json",
           {"topic_slug": "something-else", "project_slug": "Root",
            "project_root": "$CLAUDE_PROJECT_DIR"})
    # 4. MISMATCH in a WORKTREE — project_root is null, AND topic_slug is the
    #    literal "None": the one input that separates the guard from its absence
    _state(d, "gamma__Root.json",
           {"topic_slug": "None", "project_slug": "Root", "project_root": None})
    # 5. NO identity halves at all — the live `v11-project__v11-test.json` shape
    _state(d, "v11-project__v11-test.json",
           {"session_id": "abc", "bypass_marker": False})
    # 6. only ONE half present, and it DISAGREES -> mismatch
    _state(d, "delta__Root.json", {"project_slug": "Elsewhere"})
    # 7. _active.json — excluded by the `*__*.json` GLOB (it has no `__`), not by
    #    the explicit skip in the walk. Kept as a realistic-directory element, but
    #    it proves nothing about that guard, and saying so is the point.
    _state(d, "_active.json", {"sess": {"topic_slug": "alpha"}})
    # 8. AGREEING record whose fingerprint matches — proves the subtotal is
    #    computed over the MISMATCHES only, never globally
    _state(d, "Projects__Root.json",
           {"topic_slug": "Projects", "project_slug": "Root",
            "project_root": "$CLAUDE_PROJECT_DIR"})

    # --- records that RESOLVE, so the walk is tested across all three buckets ---
    spine_ok = tmp_path / "resolves-ok_THOUGHT.md"; spine_ok.write_text("# ok\n")
    spine_inv = tmp_path / "inv-topic_THOUGHT.md"; spine_inv.write_text("# inv\n")
    # 9. resolvable, filename topic == spine slug -> classifier bucket `groups`,
    #    body halves disagree -> MUST still be reported. This is the shape of
    #    `audit-session-app-runs__Root.json`, the record that motivated A6.
    _state(d, "resolves-ok__Root.json",
           {"topic_slug": "Projects", "project_slug": "Root",
            "project_root": "$CLAUDE_PROJECT_DIR",
            "thought_file_path": str(spine_ok)})
    # 10. resolvable, filename topic != spine slug -> classifier bucket `inverted`,
    #     body halves disagree -> MUST still be reported
    _state(d, "wrong-name__Root.json",
           {"topic_slug": "elsewhere", "project_slug": "Root",
            "project_root": None,
            "thought_file_path": str(spine_inv)})

    # NOTE — the two MALFORMED records deliberately do NOT live here. They are
    # added inside `test_a_malformed_record_does_not_kill_the_walk` alone,
    # because `_classify_records` (which several other tests call) CRASHES on
    # both: `_read_json` raises UnicodeDecodeError past its
    # `except (json.JSONDecodeError, OSError)`, and a list body reaches
    # `_resolve_spine_abs`'s `(data or {}).get(...)` as an AttributeError.
    # That is a PRE-EXISTING defect in the sibling, made visible by S5 and
    # recorded rather than fixed here (it is not S5's to introduce or repair).

    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", d)
    return d


def test_body_mismatch_buckets_every_shape(shapes):
    mismatches, no_identity = ppg.find_body_mismatch()
    names = {r["file"] for r in mismatches}
    assert names == {"audit-thing__Root.json", "beta__Root.json",
                     "gamma__Root.json", "delta__Root.json",
                     "resolves-ok__Root.json", "wrong-name__Root.json"}
    # the agreeing records are in NEITHER list
    assert "alpha__Root.json" not in names
    assert "Projects__Root.json" not in names

    # The FILENAME halves are part of the documented return schema and were
    # pinned by nothing: swapping or deleting them passed the whole suite. That
    # is the same defect one layer up from the one this slice exists to report —
    # A4 corrected halves that were being WRITTEN swapped, so an unpinned
    # swapped pair in the reporter's own output would be the identical mistake.
    row = next(r for r in mismatches if r["file"] == "audit-thing__Root.json")
    assert row["filename_topic"] == "audit-thing"   # vs body_topic "Projects" — discriminating
    assert row["body_topic"] == "Projects"
    # `audit-thing` CANNOT pin the project pair: its filename_project and
    # body_project are BOTH "Root", so `"filename_project": bproject` would pass.
    # `delta` is the discriminating record — filename "Root" vs body "Elsewhere".
    delta = next(r for r in mismatches if r["file"] == "delta__Root.json")
    assert delta["filename_topic"] == "delta"
    assert delta["filename_project"] == "Root"      # from the FILENAME...
    assert delta["body_project"] == "Elsewhere"     # ...not from the body
    assert delta["body_topic"] is None


def test_record_with_no_identity_halves_is_in_neither_bucket(shapes):
    """A6: 'neither a mismatch nor an agreement — count it in neither bucket.'

    The live `v11-project__v11-test.json` shape. A `data["topic_slug"]`
    subscript would raise KeyError here, which is the defect this guards.
    """
    mismatches, no_identity = ppg.find_body_mismatch()
    names = {r["file"] for r in no_identity}
    assert "v11-project__v11-test.json" in names
    assert "v11-project__v11-test.json" not in {r["file"] for r in mismatches}


def test_null_project_root_is_never_fingerprinted_even_when_topic_is_None(shapes):
    """Pins the `proot is not None` guard by the ONE input that needs it.

    The guard's original justification was wrong: the argument is `str(proot)`,
    so `basename(str(None))` == "None" and raises nothing — a test asserting
    "does not raise" passes with the guard DELETED and proves nothing. The only
    behaviour the guard actually changes is this record: `project_root` null and
    `topic_slug` the literal string "None". Without the guard it scores as
    cwd-fingerprinted, inflating the one number A6 gates on.
    """
    mismatches, _ = ppg.find_body_mismatch()
    gamma = next(r for r in mismatches if r["file"] == "gamma__Root.json")
    assert gamma["body_topic"] == "None"          # the trap value
    assert gamma["cwd_fingerprint"] is False      # fails if the guard is dropped


def test_a_trailing_slash_project_root_is_not_fingerprinted(shapes):
    """`os.path.basename` on a trailing slash yields "" — and that is the
    SHIPPED behaviour, so such a record is never fingerprinted.

    Three variants widen it back: `basename(str(proot).rstrip("/"))`,
    `basename(os.path.normpath(str(proot)))`, and `Path(str(proot)).name` all
    yield "Projects" and would fingerprint this record, inflating the one number
    A6's gate asserts. No fixture path had a trailing slash, so all three passed.
    """
    _state(shapes, "trailing-slash__Root.json",
           {"topic_slug": "Projects", "project_slug": "Root",
            "project_root": "$CLAUDE_PROJECT_DIR/"})
    mismatches, _ = ppg.find_body_mismatch()
    row = next(r for r in mismatches if r["file"] == "trailing-slash__Root.json")
    assert os.path.basename("$CLAUDE_PROJECT_DIR/") == ""       # shipped behaviour
    assert os.path.basename("$CLAUDE_PROJECT_DIR/".rstrip("/")) == "Projects"  # mutants
    assert row["cwd_fingerprint"] is False


def test_a_non_string_project_root_does_not_kill_the_walk(shapes):
    """`str(proot)` before `basename` is real protection, not decoration.

    Dropping it raises `TypeError` and kills the whole walk for a record that
    REACHES the fingerprint expression — a mismatch whose `topic_slug` is
    NON-NULL, carrying a non-str, non-None `project_root`. ("Non-null", not
    "present": the code tests `btopic is not None`, so a MISMATCH carrying the
    key with an explicit JSON `null` short-circuits and does NOT raise. Said of a
    mismatch specifically — a record with BOTH halves null is diverted earlier,
    at the no-identity guard, and short-circuits nowhere.) That is the
    same live-directory robustness the broadened `except` and the
    `isinstance(data, dict)` guard exist for, and which the implementation
    justifies in those words across its docstring and inline comments, while
    leaving this case UN-JUSTIFIED there. (Not "unguarded" — `str(proot)` IS the
    guard; the gap is in the implementation's coverage of it, not in the code.)

    SCOPED CLAIM. This once read "on any non-str, non-None `project_root`", and
    that unqualified universal was FALSE — a record can carry such a value and
    still be diverted before `basename` is ever evaluated, by a guard above the
    expression or by the conjunction's own short-circuit.

    The narrowing above is the claim; the enumeration of those exits is NOT
    restated here, deliberately. It lives in full, with the two revisions that
    got it wrong and the reasoning that settled it, in the S5 round-9/10/11
    receipts under `~/.claude/state/execplan/receipts/0b346c93b5af/` — rooted at
    `~`, matching the two sibling citations in this directory, because the clone
    carries its OWN `state/execplan/receipts/` tree that does NOT hold this
    run-id, so an unrooted path resolves there and reads as a deleted record.
    One fact kept in two places is this plan's most-repeated defect, so this
    points at the record instead of copying it.

    An unqualified universal the code does not support is a defect here, not
    shorthand — a narrowing this file and `find_body_mismatch` have each had to
    make before.
    """
    _state(shapes, "numeric-root__Root.json",
           {"topic_slug": "elsewhere", "project_slug": "Root", "project_root": 12345})
    mismatches, _ = ppg.find_body_mismatch()          # must not raise
    row = next(r for r in mismatches if r["file"] == "numeric-root__Root.json")
    assert row["cwd_fingerprint"] is False


def test_a_malformed_record_does_not_kill_the_walk(shapes):
    """A non-mapping body and invalid UTF-8 must not escape as AttributeError /
    UnicodeDecodeError. The walk is over a LIVE directory this function does not
    control, and `except (json.JSONDecodeError, OSError)` let UnicodeDecodeError
    through — it is a ValueError, not a JSONDecodeError.

    The two records are written HERE rather than in the shared fixture because
    `_classify_records` still dies on both (see the fixture's note). This test
    therefore pins `find_body_mismatch`'s robustness WITHOUT asserting anything
    about the sibling, which is not S5's to change.
    """
    (shapes / "listbody__Root.json").write_text("[1, 2]", encoding="utf-8")
    (shapes / "badbytes__Root.json").write_bytes(b'{"topic_slug": "\xff\xfe"}')

    mismatches, no_identity = ppg.find_body_mismatch()   # must not raise
    names = {r["file"] for r in mismatches} | {r["file"] for r in no_identity}
    # the list-bodied record carries no halves -> no_identity, never a crash
    assert "listbody__Root.json" in {r["file"] for r in no_identity}
    # the undecodable record is skipped entirely, not reported as a mismatch
    assert "badbytes__Root.json" not in names


def test_the_classifier_still_dies_on_the_records_S5_survives(shapes):
    """Pins the PRE-EXISTING sibling defect S5 made visible, so it is recorded in
    executable form rather than only in prose — and so this test starts FAILING
    the day someone fixes `_classify_records`, which is the signal to delete it.

    S5 deliberately does not repair it: A6's scope is a fourth reporter that
    sits beside the existing three without altering them.
    """
    (shapes / "badbytes__Root.json").write_bytes(b'{"topic_slug": "\xff\xfe"}')
    with pytest.raises(UnicodeDecodeError):
        ppg._classify_records()
    # ...while the new reporter walks the same directory unharmed.
    ppg.find_body_mismatch()


def test_one_half_present_and_AGREEING_is_still_a_mismatch(shapes):
    """The definitional choice, pinned: incomplete is not the same as correct.

    An absent half cannot equal a non-empty filename segment, so a record
    carrying only `topic_slug` — even one that agrees — is reported. Untested
    until now; the fixture's `delta` covers only the disagreeing case.
    """
    d = shapes
    _state(d, "solo__Root.json", {"topic_slug": "solo"})   # topic half AGREES
    mismatches, no_identity = ppg.find_body_mismatch()
    solo = next(r for r in mismatches if r["file"] == "solo__Root.json")
    assert solo["body_topic"] == "solo"       # agrees with the filename topic
    assert solo["body_project"] is None       # ...but the project half is absent
    assert "solo__Root.json" not in {r["file"] for r in no_identity}


def test_fingerprint_is_a_subtotal_of_the_mismatches_not_a_global_count(shapes):
    """The distinction Gate 3 #15 draws: 28 among mismatches vs 33 globally.

    `Projects__Root.json` carries the signature but its halves AGREE with its
    filename, so it must not reach the subtotal.
    """
    mismatches, _ = ppg.find_body_mismatch()
    fp_files = {r["file"] for r in mismatches if r["cwd_fingerprint"]}
    # the two fingerprinted MISMATCHES — one unresolvable, one resolvable, so the
    # subtotal is proven to span buckets too, not just the total
    assert fp_files == {"audit-thing__Root.json", "resolves-ok__Root.json"}
    subtotal = sum(1 for r in mismatches if r["cwd_fingerprint"])
    assert subtotal == 2
    # `Projects__Root.json` carries the signature but its halves AGREE, so it is
    # excluded — the miniature of the plan's 28-among-mismatches vs 33-globally split
    assert "Projects__Root.json" not in fp_files
    assert subtotal < len(mismatches)


def test_one_half_present_and_disagreeing_is_a_mismatch(shapes):
    mismatches, no_identity = ppg.find_body_mismatch()
    delta = next(r for r in mismatches if r["file"] == "delta__Root.json")
    assert delta["body_topic"] is None
    assert delta["body_project"] == "Elsewhere"
    assert "delta__Root.json" not in {r["file"] for r in no_identity}


def test_sees_records_the_classifier_would_have_skipped(shapes):
    """A6's CENTRAL property: the walk spans all three classifier buckets.

    The classifier `continue`s on unresolvable and again on inverted before its
    `groups` append, so a body check placed at the natural point comes back
    short. This test only means something if the fixture actually HAS records in
    each bucket — an all-unresolvable fixture makes the assertion hold by
    construction, and it then passes for an implementation that walks
    `find_unresolvable()` alone, or returns nothing at all.
    """
    groups, inverted, unres = ppg._classify_records()
    group_files = {p.name for recs in groups.values() for _pr, p, _d, _c in recs}
    inv_files = {r["file"] for r in inverted}
    unres_files = {r["file"] for r in unres}

    # The fixture must genuinely populate all three buckets, or this test is vacuous.
    assert group_files, "fixture has no `groups` record — the test would be vacuous"
    assert inv_files, "fixture has no `inverted` record — the test would be vacuous"
    assert unres_files, "fixture has no `unresolvable` record"

    found = {r["file"] for r in ppg.find_body_mismatch()[0]}

    # One mismatch from EACH bucket the classifier would have skipped or reached.
    assert "resolves-ok__Root.json" in group_files and "resolves-ok__Root.json" in found
    assert "wrong-name__Root.json" in inv_files and "wrong-name__Root.json" in found
    assert "beta__Root.json" in unres_files and "beta__Root.json" in found

    # ...and therefore NOT a subset of any single bucket. This is the assertion
    # that fails for a one-bucket implementation; containment would have passed.
    assert not found <= unres_files
    assert not found <= group_files
    assert not found <= inv_files


def test_find_body_mismatch_mutates_nothing(shapes):
    """A6: report only — no repair verb, no mutation."""
    before = {p.name: p.read_bytes() for p in shapes.glob("*.json")}
    ppg.find_body_mismatch()
    after = {p.name: p.read_bytes() for p in shapes.glob("*.json")}
    assert before == after
    assert not (shapes / "_reaped").exists()


def test_quarantined_records_under_reaped_are_NOT_walked(shapes):
    """The walk is NON-RECURSIVE, and that is load-bearing rather than incidental.

    `reap()` moves a quarantined record into `_reaped/` KEEPING its
    `<topic>__<project>.json` name, so a `.glob` → `.rglob` change would fold
    every quarantined record back into the mismatch total AND into the
    `cwd-fingerprint` subtotal — the single number A6's validation gate asserts.
    Nothing covered this: the fixture created no subdirectory, and the two
    mutation tests only assert `_reaped/` does not EXIST.
    """
    reaped = shapes / "_reaped"; reaped.mkdir()
    _state(reaped, "quarantined__Root.json",
           {"topic_slug": "Projects", "project_slug": "Elsewhere",
            "project_root": "$CLAUDE_PROJECT_DIR"})       # would be a fingerprinted mismatch

    mismatches, no_identity = ppg.find_body_mismatch()
    names = {r["file"] for r in mismatches} | {r["file"] for r in no_identity}
    assert "quarantined__Root.json" not in names
    # and it must not have moved the gated subtotal either
    assert sum(1 for r in mismatches if r["cwd_fingerprint"]) == 2


def test_the_fingerprint_is_BASENAME_equality_not_a_substring(shapes):
    """Pins the predicate against the plan's OWN named under-count case.

    `project_root` is the nearest `TODO.md` ANCESTOR, so a session run from a
    subdirectory produces `topic_slug: "Projects"` with
    `project_root: ".../Projects/Personal/foo"`. Correct behaviour: basename is
    `"foo"`, so it does NOT match — which is precisely why 28 is a FLOOR and not
    a total. A substring test (`btopic in str(proot)`) would match, inflating the
    one number the gate asserts and destroying the floor semantics.

    Every other fixture record has `project_root` either None or exactly
    `$CLAUDE_PROJECT_DIR`, so none of them discriminates. This one does.
    """
    root = "$CLAUDE_PROJECT_DIR/Personal/your-project"
    _state(shapes, "subdir-run__Root.json",
           {"topic_slug": "Projects", "project_slug": "Root", "project_root": root})
    # SECOND record, and it is the one that does the work. The first excludes
    # only `btopic in str(proot)`: "Projects" is neither a suffix of the path nor
    # a substring of its BASENAME.
    #
    # Its basename is `copilot_copilot`, chosen so the topic half "copilot" is a
    # PREFIX **and** a SUFFIX **and** an INFIX of it while never EQUALLING it.
    # That closes the TOKEN-BOUNDARY family from one record rather than the next
    # member of it — which is what three prior rounds each did:
    #   round 3 caught `btopic in str(proot)`;
    #   round 4 caught `endswith` and `in basename` — but its `your-project`
    #     basename is not one "copilot" is a prefix of, so
    #     `basename(...).startswith(btopic)` still passed the whole suite;
    #   round 5 caught that too.
    #
    # SCOPED CLAIM, corrected. An earlier revision of this comment said the
    # record catches "any other predicate weaker than equality on either side".
    # That was an over-generalisation from the one axis that had been fixed, and
    # it was false: `btopic.casefold() == basename(...).casefold()` is strictly
    # weaker than equality and passed the whole suite, because no record here had
    # halves differing ONLY in case. The `case-trap` record below closes that
    # second axis. Two axes are now covered; the claim is stated as two axes.
    trap_root = "$CLAUDE_PROJECT_DIR/Personal/copilot_copilot"
    _state(shapes, "suffix-trap__Root.json",
           {"topic_slug": "copilot", "project_slug": "Root", "project_root": trap_root})
    # CASE axis: differs from its basename ONLY by case, so a case-insensitive
    # equality mutant fingerprints it and exact equality does not.
    case_root = "$CLAUDE_PROJECT_DIR"
    _state(shapes, "case-trap__Root.json",
           {"topic_slug": "projects", "project_slug": "Root", "project_root": case_root})
    # REVERSED-SIDE axis. Every trap above puts the topic half INSIDE the
    # basename; none tested the other direction, so
    #   `basename(...) in btopic`, `btopic.startswith(basename(...))` and
    #   `btopic.endswith(basename(...))`
    # all survived the whole suite. Here the BASENAME ("Projects") is a proper
    # prefix, suffix and infix of the TOPIC half — with a leading and trailing
    # occurrence so all three directions are covered by one record — while the
    # two are still unequal.
    rev_root = "$CLAUDE_PROJECT_DIR"
    _state(shapes, "reversed-trap__Root.json",
           {"topic_slug": "Projects-personal-Projects", "project_slug": "Root",
            "project_root": rev_root})

    mismatches, _ = ppg.find_body_mismatch()
    row = next(r for r in mismatches if r["file"] == "subdir-run__Root.json")
    assert row["body_topic"] == "Projects"
    assert "Projects" in root                       # `in` WOULD match
    assert row["cwd_fingerprint"] is False          # ...equality does not

    trap = next(r for r in mismatches if r["file"] == "suffix-trap__Root.json")
    base = os.path.basename(trap_root)
    assert trap["body_topic"] == "copilot"
    # every TOPIC-HALF-INSIDE-BASENAME predicate WOULD match on this record...
    # (scoped deliberately. An earlier revision said "every weaker-than-equality
    # predicate", which was false — `basename in btopic` is weaker than equality
    # and does NOT match here, which is exactly how three reversed-side mutants
    # survived. The over-claim was narrowed in the fixture comment above and
    # left standing HERE for a full round: one fact, two sites, one repaired.)
    assert base.startswith("copilot")
    assert base.endswith("copilot")
    assert "copilot" in base
    assert trap_root.endswith("copilot")
    assert "copilot" in trap_root
    assert "copilot" != base                        # ...while equality does NOT
    assert trap["cwd_fingerprint"] is False         # ...so it must not be fingerprinted

    # SECOND AXIS: case. Differs from its basename only by case.
    case = next(r for r in mismatches if r["file"] == "case-trap__Root.json")
    case_base = os.path.basename(case_root)
    assert case["body_topic"] == "projects"
    assert case["body_topic"].casefold() == case_base.casefold()   # casefold WOULD match
    assert case["body_topic"] != case_base                         # ...exact equality does not
    assert case["cwd_fingerprint"] is False

    # REVERSED-SIDE axis: the basename is inside the TOPIC half, not vice versa.
    rev = next(r for r in mismatches if r["file"] == "reversed-trap__Root.json")
    rev_base = os.path.basename(rev_root)
    assert rev["body_topic"] == "Projects-personal-Projects"
    assert rev["body_topic"].startswith(rev_base)   # `btopic.startswith(base)` WOULD match
    assert rev["body_topic"].endswith(rev_base)     # `btopic.endswith(base)`   WOULD match
    assert rev_base in rev["body_topic"]            # `base in btopic`          WOULD match
    assert rev["body_topic"] != rev_base            # ...while equality does not
    assert rev["cwd_fingerprint"] is False

    # the under-count is real and deliberate: all four ARE cwd-derived records
    # the floor does not claim. Reported in the total, absent from the subtotal.
    names = {r["file"] for r in mismatches}
    assert {"subdir-run__Root.json", "suffix-trap__Root.json",
            "case-trap__Root.json", "reversed-trap__Root.json"} <= names


def test_the_reaper_report_section_actually_prints_the_two_numbers(shapes, capsys, monkeypatch):
    """A6's operator-visible deliverable — 'the invisible population gets a number
    the operator can watch' — had NO automated coverage: deleting the whole report
    block, or summing `not r["cwd_fingerprint"]`, passed every other test. This
    drives the real verb and reads its stdout.
    """
    monkeypatch.setattr(reaper, "_import_ppg", lambda: ppg)
    rc = reaper._cmd_find_mis_keyed([])
    assert rc == 0
    out = capsys.readouterr().out

    mismatches, no_identity = ppg.find_body_mismatch()
    expected_fp = sum(1 for r in mismatches if r["cwd_fingerprint"])

    # the exact tokens A6's validation gate greps for
    assert f"BODY-MISMATCH: {len(mismatches)} record(s)" in out
    assert f"cwd-fingerprint: {expected_fp}" in out
    # ...and the subtotal is not the total, nor its complement
    assert expected_fp != len(mismatches)
    assert f"cwd-fingerprint: {len(mismatches) - expected_fp}" not in out
    # the three pre-existing sections still print — the new one is ADDITIVE
    assert "RECONCILABLE" in out and "INVERTED" in out
    # the no-halves class is surfaced separately, not folded into a bucket.
    #
    # THE COUNT IS ASSERTED, and the rows are counted by their own 4-space
    # indent rather than by substring containment. `r["file"] in out` was
    # VACUOUS: the only no-identity fixture record has no `thought_file_path`,
    # so the classifier buckets it `unresolvable` and the pre-existing
    # UNRESOLVABLE loop already prints it — the substring was in `out` whether
    # or not this section rendered anything, and deleting the whole loop passed.
    assert f"plus {len(no_identity)} record(s) carrying NEITHER identity" in out
    ni_rows = [l for l in out.splitlines()
               if l.startswith("    ") and not l.startswith("     ")]
    assert len(ni_rows) == len(no_identity)
    for r in no_identity:
        assert f"    {r['file']}" in ni_rows

    # EVERY MISMATCH ROW MUST ACTUALLY BE RENDERED, with its halves in the right
    # order and the [cwd] marker on exactly the fingerprinted rows. Until this
    # was added, the whole per-record loop could be DELETED, or its halves
    # SWAPPED, or the marker INVERTED, and the suite still passed — the operator
    # would get two correct numbers and no usable list. The swap case matters
    # most: printing `body=<project>__<topic>` is the same swapped-halves defect
    # A4 exists to remove, reproduced in the reporter built to expose it.
    for r in mismatches:
        line = f"  {r['file']}  body={r['body_topic']}__{r['body_project']}"
        assert line in out, f"mismatch row not rendered (or rendered wrong): {line!r}"
        assert (line + "  [cwd]" in out) is bool(r["cwd_fingerprint"]), (
            f"[cwd] marker wrong for {r['file']}")
    # ...and the rendered set must EQUAL the mismatch set, not merely contain it.
    # `in out` is substring containment, so it passes for a loop that renders a
    # row twice, or that additionally renders non-mismatches — and in the second
    # case the "[cwd] on exactly the fingerprinted rows" claim above becomes
    # false while this test stays green. Count the rows.
    rendered = [l for l in out.splitlines() if "  body=" in l]
    assert len(rendered) == len(mismatches)
    assert len(set(rendered)) == len(rendered)          # no row rendered twice
    # the marker appears on exactly as many rows as are fingerprinted
    assert sum(1 for l in rendered if l.endswith("  [cwd]")) == expected_fp

    # A6: "Report both numbers; assert neither as the true split." That
    # requirement is carried ONLY by the disclaimer prose, which was deletable
    # with the whole suite green until this assertion existed.
    assert "FLOOR" in out.upper()
    assert "under-counts" in out


def test_filename_parse_matches_the_sibling_classifier(shapes):
    """`rsplit("__", 1)`, exactly as `_classify_records` does it.

    `split("__", 1)` survives the rest of the suite (no fixture name carries two
    `__`), but it would make this fourth reporter parse a filename DIFFERENTLY
    from the three siblings it sits beside — which is the halves-disagree class
    this reporter exists to report on, one level up. A bare `split("__")` with no
    maxsplit additionally raises ValueError on such a name.
    """
    _state(shapes, "a__b__c.json",
           {"topic_slug": "zzz", "project_slug": "yyy"})     # guaranteed mismatch
    mismatches, _ = ppg.find_body_mismatch()
    row = next(r for r in mismatches if r["file"] == "a__b__c.json")
    # rsplit puts every surplus `__` in the TOPIC half, as the classifier does
    assert (row["filename_topic"], row["filename_project"]) == ("a__b", "c")

    # ...and the sibling agrees, on the same name, by its own code path
    _groups, _inv, unres = ppg._classify_records()
    sib = next(r for r in unres if r["file"] == "a__b__c.json")
    assert (sib["filename_topic"], sib["filename_project"]) == \
           (row["filename_topic"], row["filename_project"])


def test_the_report_rows_are_ordered(shapes, capsys, monkeypatch):
    """`sorted()` on the walk — without it the report's row order is
    filesystem-dependent, so two runs over one unchanged corpus can render
    differently and a diff between them means nothing."""
    monkeypatch.setattr(reaper, "_import_ppg", lambda: ppg)
    reaper._cmd_find_mis_keyed([])
    out = capsys.readouterr().out
    mismatches, _ = ppg.find_body_mismatch()
    names = [r["file"] for r in mismatches]
    assert names == sorted(names)
    positions = [out.index(f"  {n}  body=") for n in names]
    assert positions == sorted(positions)


def test_the_report_mutates_nothing(shapes, capsys, monkeypatch):
    """Report only — A6 forbids a repair verb and any mutation."""
    monkeypatch.setattr(reaper, "_import_ppg", lambda: ppg)
    before = {p.name: p.read_bytes() for p in shapes.iterdir() if p.is_file()}
    reaper._cmd_find_mis_keyed([])
    capsys.readouterr()
    after = {p.name: p.read_bytes() for p in shapes.iterdir() if p.is_file()}
    assert before == after
    assert not (shapes / "_reaped").exists()


def test_only_reap_orphans_reaches_the_reaper_fallthrough(shapes, monkeypatch, capsys):
    """A6: `main` falls through to the orphan reaper for any verb in `_VERBS`
    lacking its own explicit `if`, so a new verb without one silently reaps live
    records. S5 added its report to an EXISTING verb; this pins that it did.

    ASSERTS THE DISPATCH, NOT THE SOURCE TEXT. An earlier version string-matched
    `verb == "<v>"` inside `inspect.getsource(reaper.main)`, which is satisfiable
    two ways WITHOUT the property holding:
      * `if verb == "reconcile": return _cmd_reap_orphans(rest)` — the token is
        present, the branch exists, and the verb still reaps; the test never
        looked at the call target;
      * a mere comment in `main` naming a new verb flips it into `guarded`.
    The second is not hypothetical: this module deliberately withholds the
    fallthrough token from a comment for exactly that collision reason, and that
    reasoning had been applied to the anchor scanner but not to this test.

    So: drive every verb through `main` with the reaper replaced by a sentinel,
    and assert which ones actually arrive.
    """
    reached = []
    monkeypatch.setattr(reaper, "_cmd_reap_orphans", lambda argv: reached.append("reap") or 0)
    monkeypatch.setattr(reaper, "_import_ppg", lambda: ppg)
    # neutralise the other verbs' side effects — we care only about WHO arrives
    for name in ("_cmd_reconcile", "_cmd_reconcile_inverted",
                 "_cmd_prune_active", "_cmd_restore_active"):
        monkeypatch.setattr(reaper, name, lambda argv: 0)

    arrived = set()
    for verb in reaper._VERBS:
        reached.clear()
        reaper.main([verb])
        capsys.readouterr()
        if reached:
            arrived.add(verb)

    assert arrived == {"reap-orphans"}, (
        f"these verbs reach the orphan reaper without their own branch: "
        f"{sorted(arrived - {'reap-orphans'})}")


def test_existing_three_buckets_are_unchanged_by_the_new_reporter(shapes):
    """Additivity: the new bucket sits BESIDE the existing three, and
    `_classify_records` still returns a 3-tuple (a 4th element would raise
    `ValueError: too many values to unpack` at all three call sites)."""
    result = ppg._classify_records()
    assert len(result) == 3
    groups, inverted, unres = result          # must not raise
    assert isinstance(groups, dict)
    # the three shipped reporters still unpack it
    ppg.find_mis_keyed(); ppg.find_inverted(); ppg.find_unresolvable()

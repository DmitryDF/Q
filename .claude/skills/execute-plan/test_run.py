#!/usr/bin/env python3
"""Regression suite for the /execute-plan walking skeleton (slice S2).

Defends the A1/A2 invariants against drift (code_first: "automated regression
suites defend those boundaries"). Exercises run.py only — functions in-process
+ the CLI via subprocess. No real Agent spawns, no network, in-memory only.

Run: python3 test_run.py   (or pytest)
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_PY = os.path.join(HERE, "run.py")
sys.path.insert(0, HERE)

import run  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

REGISTER_MD = """\
Some surrounding prose that must be ignored.

#### Slices
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | Root one | implementation | walking_skeleton | routine | — |
| S2 | Root two | implementation | walking_skeleton | more_capable | — |
| S3 | Child of S2 | implementation | different_ways | routine | S2 |
| S4 | Grandchild | implementation_verification | sentence_extension | more_capable | S2, S3 |

Trailing prose, also ignored.
"""

CYCLE_MD = """\
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | a | implementation | x | routine | S2 |
| S2 | b | implementation | y | routine | S1 |
"""

UNSATISFIABLE_MD = """\
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | a | implementation | x | routine | S9 |
"""


# --------------------------------------------------------------------------- #
# A1 — translation
# --------------------------------------------------------------------------- #

def test_translation_routine_to_sonnet():
    assert run.agent_choice_to_model_family("routine") == "sonnet"


def test_translation_more_capable_to_opus():
    assert run.agent_choice_to_model_family("more_capable") == "opus"


def test_translation_unknown_raises():
    for bad in ("wizard", "", None, "Routine"):
        try:
            run.agent_choice_to_model_family(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for agent_choice={bad!r}")


def test_translation_targets_are_canonical_slugs():
    # Reuse, not reinvention: every produced family must be a key of the
    # canonical _factcheck_engine.py:379-383 map.
    assert set(run.AGENT_CHOICE_TO_FAMILY.values()) <= set(run.CANONICAL_FAMILY_SLUGS)


# --------------------------------------------------------------------------- #
# A1 — in-memory ports
# --------------------------------------------------------------------------- #

def test_inmemory_bookkeeping_records_and_reads_back():
    bk = run.InMemoryBookkeepingAdapter()
    assert bk.get("S1") is None
    assert bk.is_completed("S1") is False
    bk.mark_started("S1", model_family="opus")
    assert bk.get("S1")["status"] == "started"
    assert bk.get("S1")["model_family"] == "opus"
    assert bk.is_completed("S1") is False
    bk.mark_completed("S1", result={"status": "ok"})
    assert bk.is_completed("S1") is True
    assert bk.all()["S1"]["result"] == {"status": "ok"}


def test_three_bookkeeping_impls_after_s3():
    # S2 shipped only InMemory; S3 adds the two real adapters behind the SAME
    # port (supersedes the S2 `..._is_only_..._in_s2` assertion).
    impls = set(run.BookkeepingPort.__subclasses__())
    assert impls == {
        run.InMemoryBookkeepingAdapter,
        run.FullBookkeepingAdapter,
        run.MinimalBookkeepingAdapter,
    }, impls


def test_fake_spawn_captures_calls():
    spawn = run.FakeSpawnAdapter()
    out = spawn.spawn("S1", "sonnet", "do the thing")
    assert out["slice_id"] == "S1" and out["model_family"] == "sonnet"
    assert spawn.calls == [
        {"slice_id": "S1", "model_family": "sonnet", "prompt": "do the thing"}
    ]


# --------------------------------------------------------------------------- #
# A2 — parser
# --------------------------------------------------------------------------- #

def test_parser_extracts_slices_and_ignores_prose():
    slices = run.parse_slice_register(REGISTER_MD)
    assert [s.id for s in slices] == ["S1", "S2", "S3", "S4"]
    s2 = next(s for s in slices if s.id == "S2")
    assert s2.agent_choice == "more_capable"
    assert s2.depends_on == ()
    s4 = next(s for s in slices if s.id == "S4")
    assert s4.depends_on == ("S2", "S3")
    assert s4.type == "implementation_verification"


def test_parser_empty_depends_on_tokens():
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | a | implementation | x | routine | — |\n"
        "| S2 | b | implementation | y | routine |  |\n"
        "| S3 | c | implementation | z | routine | none |\n"
    )
    slices = run.parse_slice_register(md)
    assert all(s.depends_on == () for s in slices)


def test_parser_empty_register():
    assert run.parse_slice_register("no table here") == []


# --------------------------------------------------------------------------- #
# S1 (A1) — pointer-aware + format-tolerant register reader
# --------------------------------------------------------------------------- #

_S1_CANONICAL = (
    "#### Slices\n"
    "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
    "|----|------|------|--------------|-------|-----------|\n"
    "| S1 | one | implementation | walking_skeleton | routine | — |\n"
    "| S2 | two | implementation | different_ways | more_capable | S1 |\n"
    "| S3 | three | implementation | partial_step | routine | S2 |\n"
    "| S4 | close | implementation_verification | verify | more_capable | S3 |\n"
)


def test_s1_canonical_six_col_parses_and_presence():
    slices = run.parse_slice_register(_S1_CANONICAL)
    assert [s.id for s in slices] == ["S1", "S2", "S3", "S4"]
    present, n = run.register_presence(_S1_CANONICAL)
    assert present is True and n == 3


def test_s1_header_depth_variants_parse_identically():
    body = (
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | one | implementation | x | routine | — |\n"
        "| S2 | two | implementation | y | routine | S1 |\n"
    )
    results = []
    for header in ("## Slices", "### Slices", "#### Slices"):
        d = run.parse_slice_register_detail(header + "\n" + body)
        results.append(([s.id for s in d["slices"]], d["valid_non_closing_count"]))
    assert results[0] == results[1] == results[2] == (["S1", "S2"], 2)


def test_s1_prefixed_header_parses():
    d = run.parse_slice_register_detail(
        "### Architecture Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | one | implementation | x | routine | — |\n"
        "| S2 | two | implementation | y | routine | — |\n"
    )
    assert d["recognized"] is True
    assert [s.id for s in d["slices"]] == ["S1", "S2"]


def test_s1_dag_guard_action_table_excluded_but_register_kept():
    doc = (
        "## Coherent Actions\n"
        "| Action | Addresses Gap | Goal | Guard rails |\n"
        "|--------|---------------|------|-------------|\n"
        "| A1 | G1 | do a thing | none |\n"
        "| A2 | G2 | do another | none |\n"
        "\n"
        "#### Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | one | implementation | x | routine | — |\n"
        "| S2 | two | implementation | y | routine | S1 |\n"
    )
    d = run.parse_slice_register_detail(doc)
    assert d["recognized"] is True
    assert [s.id for s in d["slices"]] == ["S1", "S2"]  # A1/A2 excluded


def test_s1_dag_guard_slice_worded_header_over_action_table_not_recognized():
    doc = (
        "### Implementation Session 1 — crash-safe slice machine\n"
        "| Action | Addresses Gap | Goal |\n"
        "|--------|---------------|------|\n"
        "| A1 | G1 | do a thing |\n"
        "| A2 | G2 | do another |\n"
    )
    d = run.parse_slice_register_detail(doc)
    assert d["recognized"] is False
    assert d["slices"] == [] and d["raw_row_count"] == 0


def test_s1_prose_slice_heading_over_two_col_table_not_recognized():
    doc = (
        "### Slice of Architecture\n"
        "| Aspect | Detail |\n"
        "|--------|--------|\n"
        "| layer | domain |\n"
        "| owner | code |\n"
    )
    d = run.parse_slice_register_detail(doc)
    assert d["recognized"] is False
    assert d["raw_row_count"] == 0 and d["slices"] == []


def test_s1_bold_and_sub_slice_ids_admitted():
    d = run.parse_slice_register_detail(
        "#### Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| **S1** | bold | implementation | x | routine | — |\n"
        "| S4a | sub | implementation | y | routine | S1 |\n"
        "| S-final | closing | implementation_verification | z | more_capable | S4a |\n"
    )
    assert [s.id for s in d["slices"]] == ["S1", "S4a", "S-final"]  # **S1** -> S1
    s4a = next(s for s in d["slices"] if s.id == "S4a")
    assert s4a.depends_on == ("S1",)


def test_s1_alias_columns():
    # id=#, but NO type col -> rows raw-but-not-valid.
    no_type = (
        "#### Slices\n"
        "| # | Slice | Decisions | Idea | Model | Depends on |\n"
        "|---|-------|-----------|------|-------|-----------|\n"
        "| S1 | one | picked | x | routine | — |\n"
        "| S2 | two | picked | y | routine | S1 |\n"
    )
    d = run.parse_slice_register_detail(no_type)
    assert d["recognized"] is True
    assert d["raw_non_closing_count"] >= 2 and d["valid_non_closing_count"] < 2

    # Agent=model alias, depends_on alias.
    agent_alias = (
        "#### Slices\n"
        "| ID | Name | Type | Agent | Slicing idea | depends_on |\n"
        "|----|------|------|-------|--------------|-----------|\n"
        "| S1 | one | implementation | routine | x | — |\n"
        "| S2 | two | implementation | more_capable | y | S1 |\n"
    )
    slices = run.parse_slice_register(agent_alias)
    assert [(s.id, s.agent_choice) for s in slices] == \
        [("S1", "routine"), ("S2", "more_capable")]

    # Idea before Agent, Depends on.
    idea_first = (
        "#### Slices\n"
        "| ID | Name | Type | Idea | Agent | Depends on |\n"
        "|----|------|------|------|-------|-----------|\n"
        "| S1 | one | implementation | x | routine | — |\n"
        "| S2 | two | implementation | y | more_capable | S1 |\n"
    )
    slices = run.parse_slice_register(idea_first)
    assert [(s.id, s.agent_choice, s.depends_on) for s in slices] == \
        [("S1", "routine", ()), ("S2", "more_capable", ("S1",))]


def test_s1_cols_7_9_preserved_labeled_and_positional():
    labeled = (
        "#### Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | "
        "Dispatch | Write targets |\n"
        "|----|------|------|--------------|-------|-----------|---------|"
        "---------|---------------|\n"
        "| S1 | one | implementation | x | routine | — | yes | hands-off | "
        "a.py,b.py |\n"
    )
    s = run.parse_slice_register(labeled)[0]
    assert s.confirm_override is True
    assert s.dispatch == "hands-off"
    assert s.write_targets == ("a.py", "b.py")

    # Unlabeled canonical register — 6 named header cols, 9 positional data cells.
    positional = (
        "#### Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | one | implementation | x | routine | — | yes | hands-off | "
        "a.py,b.py |\n"
    )
    s = run.parse_slice_register(positional)[0]
    assert s.confirm_override is True
    assert s.dispatch == "hands-off"
    assert s.write_targets == ("a.py", "b.py")


def test_s1_raw_vs_valid_counts_malformed_multistep_signal():
    d = run.parse_slice_register_detail(
        "#### Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | one | implementation | x | routine | — |\n"
        "|    | two | implementation | y | routine | — |\n"
        "|    | three | implementation | z | routine | — |\n"
    )
    assert d["recognized"] is True
    assert d["raw_non_closing_count"] == 3
    assert d["valid_non_closing_count"] == 1
    assert [s.id for s in d["slices"]] == ["S1"]


def test_s1_pointer_following_to_spine():
    with tempfile.TemporaryDirectory() as tmp:
        spine = os.path.join(tmp, "topic_THOUGHT.md")
        plan = os.path.join(tmp, "topic_PLAN.md")
        with open(spine, "w", encoding="utf-8") as fh:
            fh.write(
                "# Discovery\n\n"
                "#### slices\n"
                "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
                "|----|------|------|--------------|-------|-----------|\n"
                "| S1 | one | implementation | x | routine | — |\n"
                "| S2 | two | implementation | y | routine | S1 |\n"
            )
        with open(plan, "w", encoding="utf-8") as fh:
            fh.write(
                "# Implementation Details\n\n"
                "<!-- GATE0SR:SLICES -->\n"
                "slice_register_ref: topic_THOUGHT.md#slices\n"
                "No inline register in this plan text.\n"
            )
        src = run.resolve_register_source({"spine_path": plan})
        assert src["source"] == "pointer"
        assert src["recognized"] is True
        assert src["pointer_broken"] is False
        assert [s.id for s in run.parse_slice_register(src["text"])] == ["S1", "S2"]


def test_s1_broken_pointer_is_loud_not_single_step():
    src = run.resolve_register_source({
        "register_markdown": (
            "# Implementation Details\n\n"
            "slice_register_ref: does-not-exist.md#slices\n"
            "No inline register.\n"
        )
    })
    assert src["pointer_broken"] is True
    assert src["recognized"] is False
    assert src["warning"] and "does-not-exist.md" in src["warning"]


def test_s1_single_work_non_regression_presence_false():
    one_work = (
        "#### Slices\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | build | implementation | x | routine | — |\n"
        "| S2 | close | implementation_verification | y | routine | S1 |\n"
    )
    present, n = run.register_presence(one_work)
    assert present is False and n == 1


# --------------------------------------------------------------------------- #
# A2 — DAG walk + dispatch loop
# --------------------------------------------------------------------------- #

def test_next_ready_one_at_a_time_and_dependency_gated():
    slices = run.parse_slice_register(REGISTER_MD)
    bk = run.InMemoryBookkeepingAdapter()
    # Initially S1 (register order, no deps) is the single ready slice.
    nxt = run.next_ready_slice(slices, bk)
    assert nxt.id == "S1"
    bk.mark_started("S1", model_family="sonnet"); bk.mark_completed("S1", result={})
    assert run.next_ready_slice(slices, bk).id == "S2"
    bk.mark_started("S2", model_family="opus"); bk.mark_completed("S2", result={})
    # S3 ready (dep S2 done); S4 still blocked (needs S3).
    assert run.next_ready_slice(slices, bk).id == "S3"
    bk.mark_started("S3", model_family="sonnet"); bk.mark_completed("S3", result={})
    assert run.next_ready_slice(slices, bk).id == "S4"


def test_dispatch_loop_completes_in_dependency_order():
    slices = run.parse_slice_register(REGISTER_MD)
    bk = run.InMemoryBookkeepingAdapter()
    spawn = run.FakeSpawnAdapter()
    result = run.run_dispatch_loop(slices, bk, spawn)
    assert result["summary"]["order"] == ["S1", "S2", "S3", "S4"]
    assert result["summary"]["completed"] == 4
    # one in flight => exactly one spawn call per slice, in order
    assert [c["slice_id"] for c in spawn.calls] == ["S1", "S2", "S3", "S4"]
    # model families resolved correctly per slice
    fam = {c["slice_id"]: c["model_family"] for c in spawn.calls}
    assert fam == {"S1": "sonnet", "S2": "opus", "S3": "sonnet", "S4": "opus"}


# --------------------------------------------------------------------------- #
# A4 — [MODEL:fam] stamp for the runtime model enforcer (check-impl-models.sh)
# --------------------------------------------------------------------------- #

def test_stamp_model_appends_tag_and_is_idempotent():
    assert run.stamp_model("do it", "sonnet") == "do it [MODEL:sonnet]"
    # idempotent — an already-tagged prompt is unchanged
    assert run.stamp_model("do it [MODEL:sonnet]", "sonnet") == "do it [MODEL:sonnet]"
    # unknown family → no-op (the enforcer then falls back to union-with-warn)
    assert run.stamp_model("do it", "gpt-9") == "do it"
    # trailing newline/space handled without a stray double space
    assert run.stamp_model("do it\n", "opus") == "do it\n[MODEL:opus]"
    assert run.stamp_model("", "haiku") == "[MODEL:haiku]"


def test_dispatch_loop_stamps_model_tag_into_prompt():
    # Every implementation spawn's prompt must carry [MODEL:<resolved family>] so
    # check-impl-models.sh can verify the spawn ran on the intended model.
    slices = run.parse_slice_register(REGISTER_MD)
    bk = run.InMemoryBookkeepingAdapter()
    spawn = run.FakeSpawnAdapter()
    run.run_dispatch_loop(slices, bk, spawn)
    fam = {c["slice_id"]: c["model_family"] for c in spawn.calls}
    for c in spawn.calls:
        assert f"[MODEL:{fam[c['slice_id']]}]" in c["prompt"], c


def test_dispatch_loop_empty_register():
    result = run.run_dispatch_loop([], run.InMemoryBookkeepingAdapter(),
                                   run.FakeSpawnAdapter())
    assert result["summary"] == {"total": 0, "completed": 0, "order": []}


def test_dispatch_loop_no_persistence_side_effect():
    # The loop touches only the in-memory adapter; no execute-plan state dir.
    state_dir = os.path.expanduser("~/.claude/state/execute-plan")
    existed_before = os.path.exists(state_dir)
    slices = run.parse_slice_register(REGISTER_MD)
    bk = run.InMemoryBookkeepingAdapter()
    run.run_dispatch_loop(slices, bk, run.FakeSpawnAdapter())
    assert os.path.exists(state_dir) == existed_before
    assert not hasattr(bk, "_state_path")  # no file-backed state


def test_dispatch_loop_cycle_raises_deadlock():
    slices = run.parse_slice_register(CYCLE_MD)
    try:
        run.run_dispatch_loop(slices, run.InMemoryBookkeepingAdapter(),
                              run.FakeSpawnAdapter())
    except run.DeadlockError:
        return
    raise AssertionError("expected DeadlockError on a dependency cycle")


def test_dispatch_loop_unsatisfiable_raises_deadlock():
    slices = run.parse_slice_register(UNSATISFIABLE_MD)
    try:
        run.run_dispatch_loop(slices, run.InMemoryBookkeepingAdapter(),
                              run.FakeSpawnAdapter())
    except run.DeadlockError:
        return
    raise AssertionError("expected DeadlockError on an unsatisfiable depends_on")


# --------------------------------------------------------------------------- #
# S3 — real persistence adapters (Full + Minimal): round-trip, durability,
# atomicity, key-preservation, and real-to-real loop (Cockburn 4-step complete)
# --------------------------------------------------------------------------- #

def _no_tmp_residue(dir_path):
    return not any(name.endswith(".tmp") for name in os.listdir(dir_path))


def test_full_adapter_round_trip_and_durability():
    # test-to-real: a direct driver exercises the production adapter; re-opening a
    # FRESH instance on the same path (simulating process exit) reads state back.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "automate-plan-execution__root.json")
        a = run.FullBookkeepingAdapter(path)
        assert a.get("S1") is None and a.is_completed("S1") is False
        a.mark_started("S1", model_family="sonnet")
        assert a.get("S1")["status"] == "started"
        assert a.get("S1")["model_family"] == "sonnet"
        a.mark_completed("S1", result={"status": "ok"})
        # fresh instance, same file == survives process exit
        b = run.FullBookkeepingAdapter(path)
        assert b.is_completed("S1") is True
        assert b.all()["S1"]["result"] == {"status": "ok"}
        assert json.loads(open(path, encoding="utf-8").read())  # valid JSON
        assert _no_tmp_residue(d)


def test_full_adapter_preserves_unrelated_keys():
    # A13: the Full adapter owns ONLY slice_execution; sibling keys survive writes.
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "topic__root.json")
        seed = {
            "topic_slug": "topic",
            "phase": "implementation",
            "phase_history": [{"to": "implementation"}],
            "phase_complete": {"planning": True},
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(seed, fh)
        a = run.FullBookkeepingAdapter(path)
        a.mark_started("S1", model_family="opus")
        a.mark_completed("S1", result={})
        doc = json.loads(open(path, encoding="utf-8").read())
        # every seeded key preserved verbatim
        for k, v in seed.items():
            assert doc[k] == v, (k, doc.get(k), v)
        # only slice_execution was added
        assert set(doc) == set(seed) | {"slice_execution"}
        assert doc["slice_execution"]["S1"]["status"] == "completed"


def test_full_adapter_for_topic_resolves_canonical_path():
    a = run.FullBookkeepingAdapter.for_topic("automate-plan-execution", "root")
    expected = os.path.join(
        os.path.expanduser("~/.claude/state/pre_plan_gates"),
        "automate-plan-execution__root.json",
    )
    assert str(a._path) == expected


def test_minimal_adapter_round_trip_and_durability():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "plan.run-state.json")
        a = run.MinimalBookkeepingAdapter(path)
        assert a.get("S1") is None
        a.mark_started("S1", model_family="sonnet")
        a.mark_completed("S1", result={"ok": 1})
        b = run.MinimalBookkeepingAdapter(path)  # fresh instance, same file
        assert b.is_completed("S1") is True
        assert b.all()["S1"]["result"] == {"ok": 1}
        assert json.loads(open(path, encoding="utf-8").read())
        assert _no_tmp_residue(d)


def test_minimal_adapter_for_plan_derives_sibling_path():
    a = run.MinimalBookkeepingAdapter.for_plan(
        "/tmp/Thoughts/automate-plan-execution_S3_PLAN.md"
    )
    assert str(a._path) == "/tmp/Thoughts/automate-plan-execution_S3_PLAN.run-state.json"


def test_real_adapters_missing_file_reads_as_empty():
    with tempfile.TemporaryDirectory() as d:
        for adapter in (
            run.FullBookkeepingAdapter(os.path.join(d, "absent_full.json")),
            run.MinimalBookkeepingAdapter(os.path.join(d, "absent_min.json")),
        ):
            assert adapter.get("S1") is None
            assert adapter.is_completed("S1") is False
            assert adapter.all() == {}


def test_minimal_adapter_creates_file_only_on_first_write():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "plan.run-state.json")
        a = run.MinimalBookkeepingAdapter(path)
        a.all()  # pure read — must NOT create the file
        assert not os.path.exists(path)
        a.mark_started("S1", model_family="sonnet")
        assert os.path.exists(path)


def _real_to_real(adapter_factory):
    """run the PRODUCTION loop against a real adapter (real-to-real) and assert it
    completes the register in the same dependency order as the in-memory run."""
    slices = run.parse_slice_register(REGISTER_MD)
    with tempfile.TemporaryDirectory() as d:
        adapter = adapter_factory(d)
        result = run.run_dispatch_loop(slices, adapter, run.FakeSpawnAdapter())
        assert result["summary"]["order"] == ["S1", "S2", "S3", "S4"]
        assert result["summary"]["completed"] == 4
        # state was persisted and is re-readable from a fresh instance
        fresh = adapter_factory(d, reopen=adapter)
        assert all(fresh.is_completed(sid) for sid in ("S1", "S2", "S3", "S4"))
        assert _no_tmp_residue(d)


def test_dispatch_loop_real_to_real_full():
    def factory(d, reopen=None):
        path = reopen._path if reopen else os.path.join(d, "topic__root.json")
        return run.FullBookkeepingAdapter(path)
    _real_to_real(factory)


def test_dispatch_loop_real_to_real_minimal():
    def factory(d, reopen=None):
        path = reopen._path if reopen else os.path.join(d, "plan.run-state.json")
        return run.MinimalBookkeepingAdapter(path)
    _real_to_real(factory)


def test_real_adapters_substitute_inmemory_with_identical_loop_result():
    # The loop/translation/SpawnPort are unchanged: in-memory and real adapters
    # produce the SAME dispatch order + model families (boundary is swappable).
    slices = run.parse_slice_register(REGISTER_MD)
    baseline = run.run_dispatch_loop(
        slices, run.InMemoryBookkeepingAdapter(), run.FakeSpawnAdapter()
    )
    with tempfile.TemporaryDirectory() as d:
        full = run.run_dispatch_loop(
            run.parse_slice_register(REGISTER_MD),
            run.FullBookkeepingAdapter(os.path.join(d, "t__root.json")),
            run.FakeSpawnAdapter(),
        )
    assert full["summary"]["order"] == baseline["summary"]["order"]


# --------------------------------------------------------------------------- #
# S4 — crash-safe 3-checkpoint state machine: committed checkpoint, persisted
# retry counter, idempotent commit guard, resume reconciliation, retry-2x-then-
# escalate. (A1/A2/A3 validation gates; S2/S3 cases above stay green.)
# --------------------------------------------------------------------------- #

ONE_SLICE_MD = (
    "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
    "|----|------|------|--------------|-------|-----------|\n"
    "| S1 | only | implementation | x | routine | — |\n"
)

RESUME_MD = (
    "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
    "|----|------|------|--------------|-------|-----------|\n"
    "| S1 | a | implementation | x | routine | — |\n"
    "| S2 | b | implementation | y | routine | S1 |\n"
)


class _RecordingCommitWorld(run.CommitGuardPort, run.CommitPort):
    """Test double pairing the guard + adapter over ONE shared set of committed
    slice ids — `create_commit` registers the id, `commit_exists` reads it. Models
    git-history-as-truth (the spine's lock-free guard, THOUGHT:176) so the
    idempotent guard genuinely prevents a second commit when a slice is re-run."""

    def __init__(self):
        self.committed = set()
        self.create_calls = []

    def commit_exists(self, slice_id) -> bool:
        return slice_id in self.committed

    def create_commit(self, slice_id, *, result, paths=()) -> dict:
        self.create_calls.append(slice_id)
        self.committed.add(slice_id)
        return {"status": "committed", "slice_id": slice_id}


class _FakeRun:
    """Minimal `subprocess.run` result shape (only `.stdout` is read)."""
    def __init__(self, stdout):
        self.stdout = stdout


# --- A1: committed checkpoint + persisted retry counter --- #

def test_committed_checkpoint_round_trip_inmemory():
    bk = run.InMemoryBookkeepingAdapter()
    bk.record_attempt("S1", model_family="opus")
    assert bk.is_committed("S1") is False
    bk.mark_committed("S1", result={"ok": 1})
    assert bk.is_committed("S1") is True
    assert bk.is_completed("S1") is False          # committed != completed
    entry = bk.get("S1")
    assert entry["status"] == "committed"
    assert entry["model_family"] == "opus"          # preserved across transition
    assert entry["attempts"] == 1                    # counter preserved
    bk.mark_completed("S1", result={"ok": 2})
    assert bk.is_completed("S1") is True


def test_committed_checkpoint_round_trip_real_adapters():
    with tempfile.TemporaryDirectory() as d:
        for adapter in (
            run.FullBookkeepingAdapter(os.path.join(d, "full__root.json")),
            run.MinimalBookkeepingAdapter(os.path.join(d, "plan.run-state.json")),
        ):
            adapter.record_attempt("S1", model_family="sonnet")
            adapter.mark_committed("S1", result={"r": 1})
            assert adapter.is_committed("S1") is True
            assert adapter.is_completed("S1") is False
            adapter.mark_completed("S1", result={"r": 2})
            assert adapter.is_completed("S1") is True


def test_record_attempt_persists_across_reopen():
    # The retry budget must survive process death (crash+resume), not reset.
    with tempfile.TemporaryDirectory() as d:
        for path, ctor in (
            (os.path.join(d, "full__root.json"), run.FullBookkeepingAdapter),
            (os.path.join(d, "plan.run-state.json"), run.MinimalBookkeepingAdapter),
        ):
            a = ctor(path)
            assert a.record_attempt("S1", model_family="opus") == 1
            assert a.record_attempt("S1", model_family="opus") == 2
            b = ctor(path)  # fresh instance, same file == survives process exit
            assert b.get("S1")["attempts"] == 2
            assert b.record_attempt("S1", model_family="opus") == 3  # continues


def test_mark_started_does_not_reset_attempts():
    bk = run.InMemoryBookkeepingAdapter()
    bk.record_attempt("S1", model_family="opus")
    bk.record_attempt("S1", model_family="opus")
    assert bk.get("S1")["attempts"] == 2
    bk.mark_started("S1", model_family="opus")   # must NOT wipe the counter
    assert bk.get("S1")["attempts"] == 2
    assert bk.get("S1")["status"] == "started"


def test_mark_escalated_is_terminal_not_completed():
    bk = run.InMemoryBookkeepingAdapter()
    bk.record_attempt("S1", model_family="opus")
    bk.mark_escalated("S1")
    assert bk.get("S1")["status"] == "escalated"
    assert bk.is_completed("S1") is False        # dependents stay blocked


# --- A2: idempotent commit guard --- #

def test_fake_commit_guard_membership():
    g = run.FakeCommitGuard(existing={"S2"})
    assert g.commit_exists("S2") is True
    assert g.commit_exists("S1") is False
    assert g.queries == ["S2", "S1"]


def test_git_commit_guard_injected_runner():
    seen = {}

    def runner(args):
        seen["args"] = list(args)
        return _FakeRun("abc123def\n" if "^S1:" in args else "")

    g = run.GitCommitGuard(repo_dir="/tmp/repo", runner=runner)
    assert g.commit_exists("S1") is True            # non-empty git output
    a = seen["args"]
    assert a[0] == "git" and "log" in a and "--grep" in a and "^S1:" in a
    assert "-C" in a and "/tmp/repo" in a            # repo_dir threaded through
    g2 = run.GitCommitGuard(runner=lambda args: _FakeRun(""))
    assert g2.commit_exists("S9") is False           # empty output


def test_git_commit_guard_reads_never_writes():
    # The guard must only READ git history — never create a commit (S7 owns that).
    assert not hasattr(run.GitCommitGuard, "create_commit")
    assert issubclass(run.GitCommitGuard, run.CommitGuardPort)
    assert not issubclass(run.GitCommitGuard, run.CommitPort)


# --- A3: loop retry-2x-then-escalate + resume reconciliation --- #

def test_loop_retry_then_complete_creates_single_commit():
    # Post-commit conformance (S6 seam) fails the first attempt, passes the
    # second. The idempotent guard must prevent a SECOND commit on the retry.
    slices = run.parse_slice_register(ONE_SLICE_MD)
    bk = run.InMemoryBookkeepingAdapter()
    world = _RecordingCommitWorld()
    seen = {"n": 0}

    def conformance(s, attempt, result):
        seen["n"] += 1
        if seen["n"] == 1:
            raise run.SliceAttemptError("conformance DIRTY on attempt 1")

    res = run.run_dispatch_loop(
        slices, bk, run.FakeSpawnAdapter(),
        commit=world, commit_guard=world, post_commit_conformance=conformance,
    )
    assert bk.is_completed("S1")
    assert bk.get("S1")["attempts"] == 2             # 1 fail + 1 success
    assert world.create_calls == ["S1"]              # EXACTLY one commit
    assert res["summary"]["order"] == ["S1"]


def test_loop_retry_then_escalate():
    # Pre-commit verify (S5 seam) always fails → escalate after 3 attempts, with
    # no commit ever created (failure is before the commit step).
    slices = run.parse_slice_register(ONE_SLICE_MD)
    bk = run.InMemoryBookkeepingAdapter()
    world = _RecordingCommitWorld()

    def always_fail(s, attempt, result):
        raise run.SliceAttemptError("model-pin mismatch")

    try:
        run.run_dispatch_loop(
            slices, bk, run.FakeSpawnAdapter(),
            commit=world, commit_guard=world, pre_commit_verify=always_fail,
        )
    except run.EscalationRequired as e:
        assert e.slice_id == "S1"
        assert e.attempts == 3
        assert bk.get("S1")["status"] == "escalated"
        assert bk.get("S1")["attempts"] == 3
        assert world.create_calls == []              # no commit on pre-commit fail
        return
    raise AssertionError("expected EscalationRequired after 3 attempts")


def test_loop_resume_reruns_committed_without_double_commit():
    # Simulate a crash: S1 reached `committed` (its commit landed) but never
    # `completed`. On resume the loop re-runs S1; the guard sees the existing
    # commit and skips creation; S1 completes; then S2 runs.
    slices = run.parse_slice_register(RESUME_MD)
    bk = run.InMemoryBookkeepingAdapter()
    bk.record_attempt("S1", model_family="sonnet")   # attempts=1, started
    bk.mark_committed("S1", result={"crashed": True})
    world = _RecordingCommitWorld()
    world.committed.add("S1")                          # git history has S1

    res = run.run_dispatch_loop(slices, bk, run.FakeSpawnAdapter(),
                                commit=world, commit_guard=world)
    assert bk.is_completed("S1") and bk.is_completed("S2")
    assert world.create_calls == ["S2"]               # S1 NOT re-committed
    assert res["summary"]["order"] == ["S1", "S2"]    # S1 re-run, then S2
    assert bk.get("S1")["attempts"] == 2              # resume CONTINUED the count


def test_loop_resume_skips_completed():
    slices = run.parse_slice_register(RESUME_MD)
    bk = run.InMemoryBookkeepingAdapter()
    bk.record_attempt("S1", model_family="sonnet")
    bk.mark_committed("S1", result={})
    bk.mark_completed("S1", result={})                # S1 finished in a prior run
    spawn = run.FakeSpawnAdapter()
    world = _RecordingCommitWorld()
    res = run.run_dispatch_loop(slices, bk, spawn, commit=world, commit_guard=world)
    assert [c["slice_id"] for c in spawn.calls] == ["S2"]   # S1 skipped
    assert res["summary"]["order"] == ["S2"]
    assert world.create_calls == ["S2"]


def test_loop_default_args_back_compat_passes_through_committed():
    # A call with NO S4 args still completes the register in dependency order
    # (defaults: fake commit + fake guard, pass-default seams) — S2/S3 surface.
    slices = run.parse_slice_register(REGISTER_MD)
    bk = run.InMemoryBookkeepingAdapter()
    res = run.run_dispatch_loop(slices, bk, run.FakeSpawnAdapter())
    assert res["summary"]["order"] == ["S1", "S2", "S3", "S4"]
    assert all(bk.is_completed(sid) for sid in ("S1", "S2", "S3", "S4"))
    assert bk.get("S1")["attempts"] == 1              # one attempt, happy path


def test_commit_and_guard_ports_have_expected_subclasses():
    assert run.FakeCommitGuard in run.CommitGuardPort.__subclasses__()
    assert run.GitCommitGuard in run.CommitGuardPort.__subclasses__()
    assert run.FakeCommitAdapter in run.CommitPort.__subclasses__()


# --------------------------------------------------------------------------- #
# S5 — model-pin verification port (transcript reader + hard-abort before the
# `committed` checkpoint). Grown through the Cockburn 4-step: fakes (test-to-
# test), real adapter against a fixture transcript (test-to-real); real-to-real
# (a real spawn's transcript) is driven from SKILL.md. The S2/S3/S4 cases above
# stay green. (A1/A2/A3 validation gates.)
# --------------------------------------------------------------------------- #


def _write_transcript(path, *model_ids, include_user=True):
    """Write a minimal subagent .jsonl transcript: one assistant turn per model
    id (plus an optional leading user turn that must be ignored)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = []
    if include_user:
        lines.append(json.dumps({"message": {"role": "user", "content": "hi"}}))
    for mid in model_ids:
        lines.append(json.dumps(
            {"message": {"role": "assistant", "model": mid, "content": "ok"}}))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def _slice(sid="S1", agent_choice="routine"):
    return run.Slice(id=sid, name="x", type="implementation",
                     agent_choice=agent_choice)


# --- A1: normalization + port shape + fake --- #

def test_normalize_model_family_round_trip():
    assert run._normalize_model_family("claude-sonnet-4-6") == "sonnet"
    assert run._normalize_model_family("claude-opus-4-7") == "opus"
    assert run._normalize_model_family("claude-haiku-4-5-20251001") == "haiku"
    for bad in ("gpt-4", "", "sonnet", None, 123):
        assert run._normalize_model_family(bad) is None


def test_model_pin_port_has_expected_subclasses():
    subs = set(run.ModelPinPort.__subclasses__())
    assert subs == {run.TranscriptModelPinAdapter, run.FakeModelPinAdapter}, subs


def test_fake_model_pin_returns_seeded_family_and_records_queries():
    p = run.FakeModelPinAdapter(family="sonnet")
    res = p.resolve_used_family(session_id="s", agent_id="a")
    assert res["ok"] is True and res["family"] == "sonnet"
    # raw_models is a synthetic placeholder in the fake — assert it normalizes
    # back to the family rather than pinning an arbitrary id string.
    assert res["raw_models"] and run._normalize_model_family(res["raw_models"][0]) == "sonnet"
    assert p.queries == [{"session_id": "s", "agent_id": "a",
                          "transcript_path": None}]


def test_fake_model_pin_by_agent_override():
    p = run.FakeModelPinAdapter(family="sonnet", by_agent={"a2": "opus"})
    assert p.resolve_used_family(agent_id="a2")["family"] == "opus"
    assert p.resolve_used_family(agent_id="a1")["family"] == "sonnet"


def test_fake_model_pin_resolution_failure():
    p = run.FakeModelPinAdapter(ok=False, error="boom")
    res = p.resolve_used_family(agent_id="x")
    assert res["ok"] is False and res["error"] == "boom"


# --- A1: real TranscriptModelPinAdapter against a fixture transcript (test-to-real) --- #

def test_transcript_adapter_reads_fixture_single_family():
    with tempfile.TemporaryDirectory() as d:
        sid, aid = "sess-uuid", "agent42"
        tpath = os.path.join(d, sid, "subagents", f"agent-{aid}.jsonl")
        _write_transcript(tpath, "claude-sonnet-4-6", "claude-sonnet-4-6")
        a = run.TranscriptModelPinAdapter(projects_root=d)
        res = a.resolve_used_family(session_id=sid, agent_id=aid)
        assert res["ok"] is True and res["family"] == "sonnet"
        assert res["raw_models"] == ["claude-sonnet-4-6"]  # de-duped, first-seen


def test_transcript_adapter_slug_rooted_glob():
    # direct path absent → fall back to the */<sid>/subagents/... glob branch.
    with tempfile.TemporaryDirectory() as d:
        sid, aid = "sess-uuid", "agent7"
        tpath = os.path.join(d, "cwd-slug", sid, "subagents", f"agent-{aid}.jsonl")
        _write_transcript(tpath, "claude-opus-4-7")
        a = run.TranscriptModelPinAdapter(projects_root=d)
        res = a.resolve_used_family(session_id=sid, agent_id=aid)
        assert res["ok"] is True and res["family"] == "opus"


def test_transcript_adapter_explicit_path():
    with tempfile.TemporaryDirectory() as d:
        tpath = os.path.join(d, "explicit.jsonl")
        _write_transcript(tpath, "claude-haiku-4-5-20251001")
        a = run.TranscriptModelPinAdapter(projects_root=d)
        res = a.resolve_used_family(transcript_path=tpath)
        assert res["ok"] is True and res["family"] == "haiku"


def test_transcript_adapter_missing_transcript_fail_closed():
    with tempfile.TemporaryDirectory() as d:
        a = run.TranscriptModelPinAdapter(projects_root=d)
        res = a.resolve_used_family(session_id="nope", agent_id="nada")
        assert res["ok"] is False and "not found" in res["error"]


def test_transcript_adapter_mixed_families_fail_closed():
    with tempfile.TemporaryDirectory() as d:
        tpath = os.path.join(d, "mixed.jsonl")
        _write_transcript(tpath, "claude-sonnet-4-6", "claude-opus-4-7")
        a = run.TranscriptModelPinAdapter(projects_root=d)
        res = a.resolve_used_family(transcript_path=tpath)
        assert res["ok"] is False and "mixed" in res["error"]


def test_transcript_adapter_no_locator_fail_closed():
    a = run.TranscriptModelPinAdapter()           # default root, never read
    res = a.resolve_used_family()
    assert res["ok"] is False and "locator" in res["error"]


# --- A2: verify-builder at the seam (fail-closed + locator threading) --- #

def test_spawn_result_locator_extracts_keys():
    assert run._spawn_result_locator(
        {"session_id": "s", "agent_id": "a", "transcript_path": "t", "x": 1}
    ) == {"session_id": "s", "agent_id": "a", "transcript_path": "t"}
    assert run._spawn_result_locator("not a dict") == {}
    assert run._spawn_result_locator({}) == {
        "session_id": None, "agent_id": None, "transcript_path": None}


def test_make_model_pin_verify_passes_on_match():
    port = run.FakeModelPinAdapter(family="sonnet")          # routine → sonnet
    verify = run.make_model_pin_verify(port)
    verify(_slice("S1", "routine"), 1, {"session_id": "s", "agent_id": "a"})  # no raise


def test_make_model_pin_verify_raises_on_mismatch():
    port = run.FakeModelPinAdapter(family="opus")            # routine expects sonnet
    verify = run.make_model_pin_verify(port)
    try:
        verify(_slice("S1", "routine"), 1, {"agent_id": "a"})
    except run.SliceAttemptError as e:
        assert "MISMATCH" in str(e)
        return
    raise AssertionError("expected SliceAttemptError on a family mismatch")


def test_make_model_pin_verify_raises_on_unresolved_fail_closed():
    port = run.FakeModelPinAdapter(ok=False, error="transcript not found")
    verify = run.make_model_pin_verify(port)
    try:
        verify(_slice("S1", "routine"), 1, {"agent_id": "a"})
    except run.SliceAttemptError as e:
        assert "cannot verify" in str(e)
        return
    raise AssertionError("expected SliceAttemptError on an unverifiable transcript")


def test_make_model_pin_verify_threads_locator_to_port():
    port = run.FakeModelPinAdapter(family="sonnet")
    verify = run.make_model_pin_verify(port)
    verify(_slice("S1", "routine"), 2,
           {"session_id": "sid9", "agent_id": "ag9", "transcript_path": None})
    assert port.queries[-1] == {"session_id": "sid9", "agent_id": "ag9",
                                "transcript_path": None}


# --- A3: loop seam proof (match completes; mismatch hard-aborts before commit) --- #

def test_loop_model_pin_match_completes_one_commit():
    slices = run.parse_slice_register(ONE_SLICE_MD)          # S1 routine → sonnet
    bk = run.InMemoryBookkeepingAdapter()
    world = _RecordingCommitWorld()
    port = run.FakeModelPinAdapter(family="sonnet")          # matches
    res = run.run_dispatch_loop(
        slices, bk, run.FakeSpawnAdapter(),
        commit=world, commit_guard=world,
        pre_commit_verify=run.make_model_pin_verify(port),
    )
    assert bk.is_completed("S1")
    assert world.create_calls == ["S1"]                      # exactly one commit
    assert bk.get("S1")["attempts"] == 1                     # happy path, no retry
    assert res["summary"]["order"] == ["S1"]


def test_loop_model_pin_mismatch_escalates_no_commit():
    slices = run.parse_slice_register(ONE_SLICE_MD)          # S1 routine → sonnet
    bk = run.InMemoryBookkeepingAdapter()
    world = _RecordingCommitWorld()
    port = run.FakeModelPinAdapter(family="opus")            # wrong model every attempt
    try:
        run.run_dispatch_loop(
            slices, bk, run.FakeSpawnAdapter(),
            commit=world, commit_guard=world,
            pre_commit_verify=run.make_model_pin_verify(port),
        )
    except run.EscalationRequired as e:
        assert e.slice_id == "S1"
        assert e.attempts == 3
        assert bk.get("S1")["status"] == "escalated"
        assert world.create_calls == []                      # hard-abort BEFORE commit
        return
    raise AssertionError("expected EscalationRequired after 3 model-pin aborts")


# --------------------------------------------------------------------------- #
# S6 — two-layer verification: code-layer (_verify_write + make_code_verify)
# and model-layer (ConformancePort + make_conformance_check + loop proofs).
# Verification items 1-8 from the plan's ## Verification section.
# --------------------------------------------------------------------------- #

# --- Item 1: _verify_write round-trip per surface_kind (match + mismatch) --- #

def test_verify_write_todo_line_match():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] **My task** — do something\n- [ ] other\n")
        result = run._verify_write("todo_line", p, "My task", r"My task")
        assert result["status"] == "OK"
        assert "My task" in result["matched"]


def test_verify_write_todo_line_mismatch():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] something else\n")
        try:
            run._verify_write("todo_line", p, "My task", r"My task")
        except run.WriteVerificationError as e:
            assert "matched nothing" in str(e) or "mismatch" in str(e)
            return
        raise AssertionError("expected WriteVerificationError on missing locator match")


def test_verify_write_topic_state_json_match():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        data = {"phase": "implementation", "topics": {"S1": {"phase": "done"}}}
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        result = run._verify_write("topic_state_json", p, "implementation", "phase")
        assert result["status"] == "OK" and result["actual"] == "implementation"
        # nested key
        result2 = run._verify_write("topic_state_json", p, "done", "topics.S1.phase")
        assert result2["status"] == "OK"


def test_verify_write_topic_state_json_mismatch():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"phase": "planning"}, fh)
        try:
            run._verify_write("topic_state_json", p, "implementation", "phase")
        except run.WriteVerificationError as e:
            assert "mismatch" in str(e)
            return
        raise AssertionError("expected WriteVerificationError on value mismatch")


def test_verify_write_topic_state_json_missing_key():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "state.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"other": 1}, fh)
        try:
            run._verify_write("topic_state_json", p, "done", "phase")
        except run.WriteVerificationError as e:
            assert "missing" in str(e)
            return
        raise AssertionError("expected WriteVerificationError on missing key")


def test_verify_write_spine_section_match():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spine.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("## Status\n**Status:** DONE\n## End\n")
        result = run._verify_write(
            "spine_section", p, "DONE",
            ("## Status\n", "## End\n"))
        assert result["status"] == "OK"


def test_verify_write_spine_section_mismatch():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spine.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("## Status\n**Status:** IN_PROGRESS\n## End\n")
        try:
            run._verify_write(
                "spine_section", p, "DONE",
                ("## Status\n", "## End\n"))
        except run.WriteVerificationError as e:
            assert "missing expected substring" in str(e)
            return
        raise AssertionError("expected WriteVerificationError on missing substring")


def test_verify_write_spine_section_list_locator_coerced():
    # The make_code_verify builder coerces a list locator to tuple; test that
    # _verify_write accepts a tuple directly (the canonical form).
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spine.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("START\nhello world\nEND\n")
        result = run._verify_write("spine_section", p, "hello", ("START\n", "END\n"))
        assert result["status"] == "OK"


def test_verify_write_unknown_surface_kind_raises_value_error():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "f.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x")
        try:
            run._verify_write("bad_kind", p, "x", "x")
        except ValueError as e:
            assert "unknown surface_kind" in str(e)
            return
        raise AssertionError("expected ValueError on unknown surface_kind")


def test_verify_write_missing_file_raises():
    try:
        run._verify_write("todo_line", "/nonexistent/path.md", "x", "x")
    except run.WriteVerificationError as e:
        assert "surface missing" in str(e)
        return
    raise AssertionError("expected WriteVerificationError on missing file")


# --- Item 2: make_code_verify pass / raise-on-verify-mismatch / code_check-fail --- #

def test_make_code_verify_passes_on_all_clear():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] **My task** — done\n")
        verify = run.make_code_verify(
            verify_specs=[{"surface_kind": "todo_line", "path": p,
                           "expected_payload": "My task", "locator": "My task"}]
        )
        s = _slice("S1")
        # must not raise
        result = verify(s, 1, {})
        assert result is None


def test_make_code_verify_raises_on_verify_mismatch():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] something else\n")
        verify = run.make_code_verify(
            verify_specs=[{"surface_kind": "todo_line", "path": p,
                           "expected_payload": "My task", "locator": "My task"}]
        )
        s = _slice("S1")
        try:
            verify(s, 1, {})
        except run.SliceAttemptError as e:
            assert "code-layer verify_write failed" in str(e)
            return
        raise AssertionError("expected SliceAttemptError on verify_write mismatch")


def test_make_code_verify_raises_on_code_check_fail():
    def failing_check(s, result):
        return {"ok": False, "detail": "tests failed with 3 errors"}

    verify = run.make_code_verify(code_check=failing_check)
    s = _slice("S1")
    try:
        verify(s, 1, {})
    except run.SliceAttemptError as e:
        assert "code-layer code_check failed" in str(e)
        assert "tests failed" in str(e)
        return
    raise AssertionError("expected SliceAttemptError on code_check failure")


def test_make_code_verify_passes_when_code_check_ok():
    def passing_check(s, result):
        return {"ok": True, "detail": "all good"}

    verify = run.make_code_verify(code_check=passing_check)
    result = verify(_slice("S1"), 1, {})
    assert result is None


def test_make_code_verify_list_locator_coerced_to_tuple():
    # make_code_verify converts a list locator to a tuple before calling _verify_write.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "spine.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("START\nhello\nEND\n")
        verify = run.make_code_verify(
            verify_specs=[{"surface_kind": "spine_section", "path": p,
                           "expected_payload": "hello",
                           "locator": ["START\n", "END\n"]}]  # list, not tuple
        )
        result = verify(_slice("S1"), 1, {})
        assert result is None


# --- Item 3: FakeConformanceAdapter seeded + by_slice + records queries --- #

def test_fake_conformance_adapter_seeded_pass():
    port = run.FakeConformanceAdapter(verdict="PASS")
    result = port.check_conformance(slice_id="S1")
    assert result["ok"] is True
    assert result["verdict"] == "PASS"
    assert result["source"] == "fake"
    assert result["error"] is None


def test_fake_conformance_adapter_seeded_dirty():
    port = run.FakeConformanceAdapter(verdict="DIRTY")
    result = port.check_conformance(slice_id="S1")
    assert result["ok"] is False
    assert result["verdict"] == "DIRTY"


def test_fake_conformance_adapter_by_slice_override():
    port = run.FakeConformanceAdapter(verdict="PASS", by_slice={"S2": "DIRTY"})
    assert port.check_conformance(slice_id="S1")["ok"] is True
    assert port.check_conformance(slice_id="S2")["ok"] is False


def test_fake_conformance_adapter_records_queries():
    port = run.FakeConformanceAdapter(verdict="PASS")
    port.check_conformance(slice_id="S1", verdict="PASS", verdict_path=None)
    port.check_conformance(slice_id="S2")
    assert len(port.queries) == 2
    assert port.queries[0]["slice_id"] == "S1"
    assert port.queries[1]["slice_id"] == "S2"


# --- Item 4: DoubleCheckConformanceAdapter inline/file/unparseable --- #

def test_double_check_conformance_adapter_inline_pass():
    port = run.DoubleCheckConformanceAdapter()
    result = port.check_conformance(slice_id="S1", verdict="PASS")
    assert result["ok"] is True
    assert result["verdict"] == "PASS"
    assert result["source"] == "inline"
    assert result["error"] is None


def test_double_check_conformance_adapter_inline_escalate_non_pass():
    # ESCALATE is the live skill's non-PASS final verdict.
    port = run.DoubleCheckConformanceAdapter()
    result = port.check_conformance(slice_id="S1", verdict="ESCALATE")
    assert result["ok"] is False
    assert result["verdict"] == "ESCALATE"


def test_double_check_conformance_adapter_inline_dirty_non_pass():
    # DIRTY is the convergence-engine / fake vocabulary (fail-closed).
    port = run.DoubleCheckConformanceAdapter()
    result = port.check_conformance(slice_id="S1", verdict="DIRTY")
    assert result["ok"] is False
    assert result["verdict"] == "DIRTY"


def test_double_check_conformance_adapter_inline_discrepancy_non_pass():
    port = run.DoubleCheckConformanceAdapter()
    result = port.check_conformance(slice_id="S1", verdict="DISCREPANCY")
    assert result["ok"] is False


def test_double_check_conformance_adapter_file_bare_token():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "verdict.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("PASS\n")
        port = run.DoubleCheckConformanceAdapter()
        result = port.check_conformance(slice_id="S1", verdict_path=p)
        assert result["ok"] is True
        assert result["source"] == "file"
        assert result["verdict_path"] == p


def test_double_check_conformance_adapter_file_json():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "verdict.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump({"verdict": "ESCALATE", "rounds": 1}, fh)
        port = run.DoubleCheckConformanceAdapter()
        result = port.check_conformance(slice_id="S1", verdict_path=p)
        assert result["ok"] is False
        assert result["verdict"] == "ESCALATE"
        assert result["source"] == "file"


def test_double_check_conformance_adapter_file_dirty():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "verdict.txt")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("DIRTY")
        port = run.DoubleCheckConformanceAdapter()
        result = port.check_conformance(slice_id="S1", verdict_path=p)
        assert result["ok"] is False
        assert result["verdict"] == "DIRTY"


def test_double_check_conformance_adapter_unparseable_never_raises():
    # A missing verdict and a missing file must return ok=False, never raise.
    port = run.DoubleCheckConformanceAdapter()
    # No verdict, no verdict_path.
    result = port.check_conformance(slice_id="S1")
    assert result["ok"] is False
    assert result["error"] is not None


def test_double_check_conformance_adapter_missing_file_never_raises():
    port = run.DoubleCheckConformanceAdapter()
    result = port.check_conformance(slice_id="S1", verdict_path="/nonexistent/verdict.txt")
    assert result["ok"] is False
    assert "cannot read" in result["error"]


def test_double_check_conformance_adapter_never_raises_on_bad_input():
    port = run.DoubleCheckConformanceAdapter()
    # Empty string verdict.
    result = port.check_conformance(slice_id="S1", verdict="")
    assert result["ok"] is False
    assert result["error"] is not None


# --- Item 4 (subclass assertion) --- #

def test_conformance_port_subclasses():
    subs = set(run.ConformancePort.__subclasses__())
    assert subs == {run.DoubleCheckConformanceAdapter, run.FakeConformanceAdapter}, subs


# --- Item 4 (make_conformance_check): pass/raise-on-DIRTY/raise-on-ESCALATE/
#     fail-closed-on-unparseable / threads locator to port --- #

def test_make_conformance_check_passes_on_pass():
    port = run.FakeConformanceAdapter(verdict="PASS")
    check = run.make_conformance_check(port)
    result = check(_slice("S1"), 1, {"conformance_verdict": "PASS"})
    assert result is None


def test_make_conformance_check_raises_on_dirty():
    port = run.FakeConformanceAdapter(verdict="DIRTY")
    check = run.make_conformance_check(port)
    try:
        check(_slice("S1"), 1, {"conformance_verdict": "DIRTY"})
    except run.SliceAttemptError as e:
        assert "non-PASS" in str(e)
        return
    raise AssertionError("expected SliceAttemptError on DIRTY verdict")


def test_make_conformance_check_raises_on_escalate():
    port = run.DoubleCheckConformanceAdapter()
    check = run.make_conformance_check(port)
    try:
        check(_slice("S1"), 1, {"conformance_verdict": "ESCALATE"})
    except run.SliceAttemptError as e:
        assert "non-PASS" in str(e)
        return
    raise AssertionError("expected SliceAttemptError on ESCALATE verdict")


def test_make_conformance_check_fail_closed_on_unparseable():
    # No verdict supplied → DoubleCheckConformanceAdapter returns ok=False.
    port = run.DoubleCheckConformanceAdapter()
    check = run.make_conformance_check(port)
    # Pass result with no conformance_verdict key → verdict=None → ok=False.
    try:
        check(_slice("S1"), 1, {})
    except run.SliceAttemptError:
        return
    raise AssertionError("expected SliceAttemptError when verdict is absent (fail-closed)")


def test_make_conformance_check_threads_locator_to_port():
    port = run.FakeConformanceAdapter(verdict="PASS")
    check = run.make_conformance_check(port)
    check(_slice("S2"), 1, {"conformance_verdict": "PASS",
                             "conformance_verdict_path": "/some/path.txt"})
    q = port.queries[-1]
    assert q["slice_id"] == "S2"
    assert q["verdict"] == "PASS"
    assert q["verdict_path"] == "/some/path.txt"


def test_make_conformance_check_non_dict_result_fail_closed():
    # A non-dict spawn result → no conformance_verdict → fail-closed.
    port = run.DoubleCheckConformanceAdapter()
    check = run.make_conformance_check(port)
    try:
        check(_slice("S1"), 1, "not a dict")
    except run.SliceAttemptError:
        return
    raise AssertionError("expected SliceAttemptError on non-dict result")


# --- Item 5: loop — code-layer abort before commit --- #

def test_loop_code_layer_fail_aborts_before_commit():
    # A failing pre_commit_codecheck must abort BEFORE the commit.
    # create_calls == [] asserts no commit was ever created.
    slices = run.parse_slice_register(ONE_SLICE_MD)
    bk = run.InMemoryBookkeepingAdapter()
    world = _RecordingCommitWorld()

    def always_fail_code(s, attempt, result):
        raise run.SliceAttemptError("code-layer: tests failed")

    try:
        run.run_dispatch_loop(
            slices, bk, run.FakeSpawnAdapter(),
            commit=world, commit_guard=world,
            pre_commit_codecheck=always_fail_code,
        )
    except run.EscalationRequired as e:
        assert e.slice_id == "S1"
        assert e.attempts == 3
        assert bk.get("S1")["status"] == "escalated"
        assert world.create_calls == []          # hard-abort BEFORE commit
        return
    raise AssertionError("expected EscalationRequired after 3 code-layer aborts")


# --- Item 6: loop — conformance DIRTY → retry → escalate, committed-but-not-completed --- #

def test_loop_conformance_dirty_escalates_committed_not_completed():
    # post_commit_conformance = make_conformance_check(FakeConformanceAdapter("DIRTY"))
    # → every attempt aborts AFTER mark_committed, before mark_completed.
    # After 3 attempts: create_calls == ["S1"] (only ONE commit due to idempotent
    # guard), the slice is escalated (not completed), and never reaches mark_completed.
    # Note: mark_escalated overwrites "committed" with "escalated" in bookkeeping,
    # but the git commit IS present (world.committed contains "S1") — the slice's
    # work was committed but the slice itself is not marked completed.
    slices = run.parse_slice_register(ONE_SLICE_MD)
    bk = run.InMemoryBookkeepingAdapter()
    world = _RecordingCommitWorld()

    try:
        run.run_dispatch_loop(
            slices, bk, run.FakeSpawnAdapter(),
            commit=world, commit_guard=world,
            post_commit_conformance=run.make_conformance_check(
                run.FakeConformanceAdapter(verdict="DIRTY")
            ),
        )
    except run.EscalationRequired as e:
        assert e.slice_id == "S1"
        assert e.attempts == 3
        # exactly ONE commit in git history (idempotent guard prevented re-commit)
        assert world.create_calls == ["S1"]
        # "S1" is in world.committed (the git commit exists) but bookkeeping status
        # is "escalated" (mark_escalated overwrote "committed") — the slice is
        # committed-but-not-completed at the git level.
        assert "S1" in world.committed         # the commit landed
        assert bk.is_completed("S1") is False  # never mark_completed
        assert bk.get("S1")["status"] == "escalated"
        return
    raise AssertionError("expected EscalationRequired after 3 conformance-DIRTY aborts")


# --- Item 7: loop — both layers pass → completed, one commit, attempts==1 --- #

def test_loop_both_layers_pass_completes():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] **S1 task** done\n")

        slices = run.parse_slice_register(ONE_SLICE_MD)
        bk = run.InMemoryBookkeepingAdapter()
        world = _RecordingCommitWorld()

        res = run.run_dispatch_loop(
            slices, bk, run.FakeSpawnAdapter(),
            commit=world, commit_guard=world,
            pre_commit_codecheck=run.make_code_verify(
                verify_specs=[{"surface_kind": "todo_line", "path": p,
                               "expected_payload": "S1 task", "locator": "S1 task"}]
            ),
            post_commit_conformance=run.make_conformance_check(
                run.FakeConformanceAdapter(verdict="PASS")
            ),
        )
        assert bk.is_completed("S1")
        assert world.create_calls == ["S1"]      # exactly one commit
        assert bk.get("S1")["attempts"] == 1     # happy path, no retry
        assert res["summary"]["order"] == ["S1"]


# --- Item 8: CLI — check-code and check-conformance --- #

def test_cli_check_code_pass_exit_0():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] **my task** done\n")
        code, out = _cli("check-code", {
            "verify_specs": [{"surface_kind": "todo_line", "path": p,
                              "expected_payload": "my task", "locator": "my task"}]
        })
        assert code == 0 and out["status"] == "OK"


def test_cli_check_code_mismatch_exit_5():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] something else\n")
        code, out = _cli("check-code", {
            "verify_specs": [{"surface_kind": "todo_line", "path": p,
                              "expected_payload": "my task", "locator": "my task"}]
        })
        assert code == 5 and out["status"] == "FAIL"


def test_cli_check_code_no_specs_exit_3():
    code, out = _cli("check-code", {})
    assert code == 3 and out["status"] == "ERROR"


def test_cli_check_conformance_pass_exit_0():
    code, out = _cli("check-conformance", {"verdict": "PASS"})
    assert code == 0 and out["status"] == "OK" and out["verdict"] == "PASS"


def test_cli_check_conformance_dirty_exit_6():
    code, out = _cli("check-conformance", {"verdict": "DIRTY"})
    assert code == 6 and out["status"] == "NON_PASS"


def test_cli_check_conformance_escalate_exit_6():
    code, out = _cli("check-conformance", {"verdict": "ESCALATE"})
    assert code == 6 and out["status"] == "NON_PASS"


def test_cli_check_conformance_missing_args_exit_3():
    code, out = _cli("check-conformance", {})
    assert code == 3 and out["status"] == "ERROR"


# --------------------------------------------------------------------------- #
# CLI (subprocess) — topology + exit codes
# --------------------------------------------------------------------------- #

def _cli(subcommand, payload):
    proc = subprocess.run(
        [sys.executable, RUN_PY, subcommand],
        input=json.dumps(payload), capture_output=True, text=True,
    )
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def test_cli_translate_ok():
    code, out = _cli("translate", {"agent_choice": "more_capable"})
    assert code == 0 and out["model_family"] == "opus"


def test_cli_translate_unknown_exit_3():
    code, out = _cli("translate", {"agent_choice": "wizard"})
    assert code == 3 and out["status"] == "ERROR"


def test_cli_plan_slices_resolves_families():
    code, out = _cli("plan-slices", {"register_markdown": REGISTER_MD})
    assert code == 0 and out["count"] == 4
    fam = {s["id"]: s["model_family"] for s in out["slices"]}
    assert fam == {"S1": "sonnet", "S2": "opus", "S3": "sonnet", "S4": "opus"}


def test_cli_dry_run_completes_in_order():
    code, out = _cli("dry-run", {"register_markdown": REGISTER_MD})
    assert code == 0
    assert out["summary"]["order"] == ["S1", "S2", "S3", "S4"]
    assert [c["slice_id"] for c in out["spawn_calls"]] == ["S1", "S2", "S3", "S4"]


def test_cli_dry_run_deadlock_exit_2():
    code, out = _cli("dry-run", {"register_markdown": CYCLE_MD})
    assert code == 2 and out["status"] == "DEADLOCK"


def test_cli_bad_subcommand_exit_3():
    proc = subprocess.run([sys.executable, RUN_PY, "frobnicate"],
                          input="{}", capture_output=True, text=True)
    assert proc.returncode == 3


# --- S5 verify-model CLI (real adapter via subprocess) --- #

def test_cli_verify_model_match_exit_0():
    with tempfile.TemporaryDirectory() as d:
        sid, aid = "sess", "ag"
        tpath = os.path.join(d, sid, "subagents", f"agent-{aid}.jsonl")
        _write_transcript(tpath, "claude-sonnet-4-6")
        code, out = _cli("verify-model", {
            "session_id": sid, "agent_id": aid,
            "agent_choice": "routine", "projects_root": d})
        assert code == 0 and out["match"] is True and out["used"] == "sonnet"


def test_cli_verify_model_mismatch_exit_4():
    with tempfile.TemporaryDirectory() as d:
        sid, aid = "sess", "ag"
        tpath = os.path.join(d, sid, "subagents", f"agent-{aid}.jsonl")
        _write_transcript(tpath, "claude-opus-4-7")           # ran on opus
        code, out = _cli("verify-model", {
            "session_id": sid, "agent_id": aid,
            "agent_choice": "routine", "projects_root": d})    # expected sonnet
        assert code == 4 and out["status"] == "MISMATCH"
        assert out["expected"] == "sonnet" and out["used"] == "opus"


def test_cli_verify_model_unverifiable_exit_4():
    with tempfile.TemporaryDirectory() as d:
        code, out = _cli("verify-model", {
            "session_id": "nope", "agent_id": "nada",
            "expected_family": "sonnet", "projects_root": d})
        assert code == 4 and out["status"] == "UNVERIFIABLE"


def test_cli_verify_model_missing_args_exit_3():
    code, out = _cli("verify-model", {"session_id": "s", "agent_id": "a"})
    assert code == 3 and out["status"] == "ERROR"


# --------------------------------------------------------------------------- #
# S7 — unified git contract + real commit adapter + end-of-plan push gate.
# Grown through the Cockburn 4-step: pure contract + fakes (test-to-test), the
# real GitCommitAdapter over a shared fake-git world with the guard (real-to-
# test), and the real adapter against an isolated temp git repo (real-to-real —
# git IS runnable from Python). Real-to-real PUSH stays operator-confirmed
# (SKILL.md), so GitPushAdapter is proven test-to-real (fake runner) only. The
# S2–S6 cases above stay green.
# --------------------------------------------------------------------------- #


class _GitRun:
    """A `subprocess.run`-shaped result (stdout / returncode / stderr) for the
    fake git runners (the adapter checks returncode, which `_FakeRun` lacks)."""
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def _git_sub(args):
    """Strip the leading `git [-C <repo>]` noise → the git subcommand argv."""
    return args[3:] if len(args) > 2 and args[1] == "-C" else args[1:]


class _FakeGitWorld:
    """A fake `git` runner SHARED by GitCommitGuard + GitCommitAdapter so the
    loop's idempotent-commit invariant is provable with NO real git. Holds the
    list of committed subjects (git-history-as-truth, the lock-free guard,
    THOUGHT:176): answers `log --grep ^<id>:` from it, records `commit -m`, and
    returns a stable branch + sha."""

    def __init__(self, branch="feature/x", committed_subjects=None):
        self.branch = branch
        self.commits = list(committed_subjects or [])
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        sub = _git_sub(args)
        if sub[:1] == ["rev-parse"] and "--abbrev-ref" in sub:
            return _GitRun(stdout=self.branch + "\n")
        if sub[:1] == ["rev-parse"]:                       # rev-parse HEAD
            return _GitRun(stdout="deadbeefsha\n")
        if sub[:1] == ["log"]:                              # log --grep ^<id>:
            grep = sub[sub.index("--grep") + 1] if "--grep" in sub else ""
            prefix = grep.lstrip("^")
            hits = [c for c in self.commits if c.startswith(prefix)]
            return _GitRun(stdout="".join(f"sha-{i}\n" for i in range(len(hits))))
        if sub[:1] == ["add"]:
            return _GitRun()
        if sub[:1] == ["commit"]:
            subject = sub[sub.index("-m") + 1] if "-m" in sub else ""
            self.commits.append(subject)
            return _GitRun()
        if sub[:1] == ["push"]:
            return _GitRun()
        return _GitRun()


def _init_temp_git_repo(d, branch="feature/s7-smoke"):
    """Initialize an ISOLATED temp git repo on a non-protected branch WITH an
    initial commit (so HEAD is born — `rev-parse --abbrev-ref HEAD` resolves to
    the branch name, as on a real operator branch) plus one pending change for
    the adapter under test to commit. The real-to-real commit proof. Never
    touches the live ~/.claude or Projects repos (verification isolation)."""
    import subprocess

    def g(*a):
        return subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)

    subprocess.run(["git", "init", d], capture_output=True, text=True)
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "T")
    g("config", "commit.gpgsign", "false")
    g("checkout", "-b", branch)
    with open(os.path.join(d, "seed.txt"), "w", encoding="utf-8") as fh:
        fh.write("seed\n")
    g("add", "-A")
    g("commit", "-m", "init")                       # born branch + history
    # leave a NEW uncommitted change for the adapter under test to commit
    with open(os.path.join(d, "f.txt"), "w", encoding="utf-8") as fh:
        fh.write("hello\n")
    return g


# --- A1: unified git contract (format / branch policy / push gate) --- #

def test_git_contract_format_subject():
    assert run.UnifiedGitContract.format_commit_subject("S7", "x") == "S7: x"
    # strips surrounding whitespace on the summary
    assert run.UnifiedGitContract.format_commit_subject("S12", "  do it  ") == "S12: do it"


def test_git_contract_format_subject_validates():
    bad_ids = ("s7", "S", "X1", "", None, 7)
    for bad in bad_ids:
        try:
            run.UnifiedGitContract.format_commit_subject(bad, "ok")
        except run.GitContractError:
            continue
        raise AssertionError(f"expected GitContractError for slice id {bad!r}")
    for empty in ("", "   ", None, 5):
        try:
            run.UnifiedGitContract.format_commit_subject("S1", empty)
        except run.GitContractError:
            continue
        raise AssertionError(f"expected GitContractError for summary {empty!r}")


def test_git_contract_format_aligns_with_guard_grep():
    # The contract's commit format and GitCommitGuard's idempotency grep MUST
    # share the `<slice_id>:` prefix — proven from ONE place (no drift).
    subject = run.UnifiedGitContract.format_commit_subject("S7", "anything")
    captured = {}

    def runner(args):
        captured["args"] = list(args)
        return _GitRun(stdout="")

    run.GitCommitGuard(runner=runner).commit_exists("S7")
    grep = captured["args"][captured["args"].index("--grep") + 1]
    assert grep == "^S7:"
    assert subject.startswith(grep.lstrip("^"))     # "S7:" prefix shared


def test_git_contract_protected_branch():
    assert run.UnifiedGitContract.is_protected_branch("main") is True
    assert run.UnifiedGitContract.is_protected_branch("master") is True
    assert run.UnifiedGitContract.is_protected_branch("feature/x") is False
    assert run.UnifiedGitContract.is_protected_branch("rename/process-to-workflow") is False


def test_push_gate_all_clear_pushes():
    d = run.UnifiedGitContract.evaluate_push_gate(
        all_slices_done=True, work_done_succeeded=True, git_status_clean=True)
    assert d.should_push is True
    assert d.partial_prompt is None


def test_push_gate_withheld_each_unmet_condition():
    base = dict(all_slices_done=True, work_done_succeeded=True, git_status_clean=True)
    for k in base:
        kw = dict(base)
        kw[k] = False
        d = run.UnifiedGitContract.evaluate_push_gate(**kw)
        assert d.should_push is False, k
        assert d.partial_prompt                       # a non-empty operator prompt


def test_push_gate_aborted_yields_partial_prompt():
    d = run.UnifiedGitContract.evaluate_push_gate(
        all_slices_done=True, work_done_succeeded=True, git_status_clean=True,
        aborted=True)
    assert d.should_push is False
    assert "abort" in d.partial_prompt.lower()


def test_push_decision_is_frozen():
    d = run.PushDecision(should_push=True, reason="x")
    try:
        d.should_push = False
    except Exception:
        return
    raise AssertionError("PushDecision must be frozen (immutable)")


# --- A2: real GitCommitAdapter (fake runner) --- #

def test_git_commit_adapter_creates_prefixed_commit():
    world = _FakeGitWorld(branch="feature/x")
    receipt = run.GitCommitAdapter(runner=world).create_commit(
        "S7", result={"commit_summary": "do the thing"}, paths=["`src/a.py`"])
    assert receipt["status"] == "committed"
    assert receipt["subject"] == "S7: do the thing"
    assert receipt["branch"] == "feature/x"
    assert receipt["sha"] == "deadbeefsha"
    assert receipt["paths"] == ["src/a.py"]              # register backticks stripped
    subs = [_git_sub(c) for c in world.calls]
    spec = ":(top,literal)src/a.py"
    # BOTH verbs scoped — a scoped add with a bare commit is the A7 defect.
    assert ["add", "-A", "--", spec] in subs
    assert ["commit", "-m", "S7: do the thing", "--", spec] in subs
    assert ["add", "-A"] not in subs


def test_git_commit_adapter_default_summary():
    world = _FakeGitWorld(branch="feature/x")
    receipt = run.GitCommitAdapter(runner=world).create_commit(
        "S2", result={}, paths=["a.txt"])
    assert receipt["subject"] == "S2: slice S2 implementation"


def test_git_commit_adapter_refuses_protected_branch():
    calls = []

    def runner(args):
        calls.append(_git_sub(args))
        sub = _git_sub(args)
        if sub[:1] == ["rev-parse"] and "--abbrev-ref" in sub:
            return _GitRun(stdout="main\n")
        return _GitRun()

    try:
        run.GitCommitAdapter(runner=runner).create_commit(
            "S1", result={}, paths=["a.txt"])
    except run.GitContractError as e:
        assert "protected branch" in str(e)
        assert not any(c[:1] == ["commit"] for c in calls)   # never committed
        return
    raise AssertionError("expected GitContractError on a protected branch")


def test_git_commit_adapter_commit_failure_raises():
    def runner(args):
        sub = _git_sub(args)
        if sub[:1] == ["rev-parse"] and "--abbrev-ref" in sub:
            return _GitRun(stdout="feature/x\n")
        if sub[:1] == ["commit"]:
            return _GitRun(returncode=1, stderr="nothing to commit")
        return _GitRun()

    try:
        run.GitCommitAdapter(runner=runner).create_commit(
            "S1", result={}, paths=["a.txt"])
    except run.GitContractError as e:
        assert "git commit failed" in str(e)
        assert "a.txt" in str(e) and "UNCOMMITTED" in str(e)
        return
    raise AssertionError("expected GitContractError on a non-zero git commit exit")


def test_git_contract_error_is_not_slice_attempt_error():
    # A contract violation must NOT be routed into the loop's retry budget —
    # GitContractError is independent of SliceAttemptError (run.py:1225 catches
    # only SliceAttemptError).
    assert not issubclass(run.GitContractError, run.SliceAttemptError)


def test_commit_port_subclasses_after_s7():
    # S7 adds the real GitCommitAdapter alongside the FakeCommitAdapter stand-in.
    # (Membership, not equality: the S4 `_RecordingCommitWorld` test double also
    # subclasses CommitPort.)
    subs = set(run.CommitPort.__subclasses__())
    assert run.FakeCommitAdapter in subs
    assert run.GitCommitAdapter in subs


# --- A3: push port (fake runner) --- #

def test_push_port_subclasses():
    assert set(run.PushPort.__subclasses__()) == {
        run.GitPushAdapter, run.FakePushAdapter}


def test_git_push_adapter_pushes_when_allowed():
    calls = []

    def runner(args):
        calls.append(_git_sub(args))
        return _GitRun(stdout="Everything up-to-date")

    r = run.GitPushAdapter(runner=runner).push(
        run.PushDecision(should_push=True, reason="all clear"))
    assert r["pushed"] is True and r["status"] == "pushed"
    assert any(c[:1] == ["push"] for c in calls)


def test_git_push_adapter_withholds_fail_closed():
    calls = []

    def runner(args):
        calls.append(_git_sub(args))
        return _GitRun()

    r = run.GitPushAdapter(runner=runner).push(
        run.PushDecision(should_push=False, reason="not done",
                         partial_prompt="withheld message"))
    assert r["pushed"] is False and r["status"] == "withheld"
    assert r["partial_prompt"] == "withheld message"
    assert calls == []                                # never invoked git (fail-closed)


def test_git_push_adapter_push_failure_raises():
    def runner(args):
        return _GitRun(returncode=1, stderr="rejected")

    try:
        run.GitPushAdapter(runner=runner).push(
            run.PushDecision(should_push=True, reason="ok"))
    except run.GitContractError as e:
        assert "git push failed" in str(e)
        return
    raise AssertionError("expected GitContractError on a non-zero git push exit")


def test_fake_push_adapter_records():
    a = run.FakePushAdapter()
    a.push(run.PushDecision(should_push=True, reason="x"))
    a.push(run.PushDecision(should_push=False, reason="y", partial_prompt="z"))
    assert len(a.calls) == 2
    assert a.calls[0]["should_push"] is True
    assert a.calls[1]["should_push"] is False


# --- A4: loop with the REAL commit adapter over a shared fake-git world --- #

def _with_targets(slices):
    """Give each fixture slice a declared write target (`<id>.txt`) — the real
    commit adapter refuses an undeclared slice."""
    import dataclasses
    return [dataclasses.replace(s, write_targets=(f"{s.id}.txt",)) for s in slices]


def test_loop_with_real_git_commit_adapter_one_commit():
    slices = _with_targets(run.parse_slice_register(ONE_SLICE_MD))  # S1 routine
    bk = run.InMemoryBookkeepingAdapter()
    world = _FakeGitWorld(branch="feature/x")
    res = run.run_dispatch_loop(
        slices, bk, run.FakeSpawnAdapter(),
        commit=run.GitCommitAdapter(runner=world),
        commit_guard=run.GitCommitGuard(runner=world),
    )
    assert bk.is_completed("S1")
    commit_subs = [s for c in world.calls if (s := _git_sub(c))[:1] == ["commit"]]
    assert len(commit_subs) == 1                          # exactly one real commit
    assert any("S1:" in tok for tok in commit_subs[0])
    assert res["summary"]["order"] == ["S1"]


def test_loop_real_git_commit_idempotent_on_resume():
    # Crash+resume: S1's commit already landed (the shared world holds it); the
    # S4 idempotent guard must skip a second commit on the re-run. Real adapter.
    slices = _with_targets(run.parse_slice_register(RESUME_MD))   # S1, S2
    bk = run.InMemoryBookkeepingAdapter()
    bk.record_attempt("S1", model_family="sonnet")
    bk.mark_committed("S1", result={})
    world = _FakeGitWorld(branch="feature/x",
                          committed_subjects=["S1: slice S1 implementation"])
    res = run.run_dispatch_loop(
        slices, bk, run.FakeSpawnAdapter(),
        commit=run.GitCommitAdapter(runner=world),
        commit_guard=run.GitCommitGuard(runner=world),
    )
    assert bk.is_completed("S1") and bk.is_completed("S2")
    commit_subs = [s for c in world.calls if (s := _git_sub(c))[:1] == ["commit"]]
    assert len(commit_subs) == 1                          # only S2 committed now
    assert any("S2:" in tok for tok in commit_subs[0])
    assert res["summary"]["order"] == ["S1", "S2"]


# --- A4: real-to-real commit against an ISOLATED temp git repo --- #

def test_git_commit_adapter_real_temp_repo():
    with tempfile.TemporaryDirectory() as d:
        _init_temp_git_repo(d)
        receipt = run.GitCommitAdapter(repo_dir=d).create_commit(
            "S7", result={"commit_summary": "real temp commit"}, paths=["f.txt"])
        assert receipt["status"] == "committed"
        assert receipt["subject"] == "S7: real temp commit"
        assert receipt["branch"] == "feature/s7-smoke"
        assert receipt["sha"]
        # the real guard now finds the real commit (single-source prefix holds)
        assert run.GitCommitGuard(repo_dir=d).commit_exists("S7") is True
        assert run.GitCommitGuard(repo_dir=d).commit_exists("S9") is False


# --- A4: CLI — commit-slice + push-gate --- #

def test_cli_commit_slice_real_temp_repo_exit_0():
    with tempfile.TemporaryDirectory() as d:
        _init_temp_git_repo(d)
        code, out = _cli("commit-slice", {
            "slice_id": "S3", "repo_dir": d, "commit_summary": "cli temp commit",
            "paths": ["f.txt"]})
        assert code == 0 and out["status"] == "OK"
        assert out["subject"] == "S3: cli temp commit"
        assert run.GitCommitGuard(repo_dir=d).commit_exists("S3") is True


def test_cli_commit_slice_missing_slice_id_exit_3():
    code, out = _cli("commit-slice", {})
    assert code == 3 and out["status"] == "ERROR"


def test_cli_commit_slice_git_failure_exit_8():
    code, out = _cli("commit-slice", {
        "slice_id": "S1", "repo_dir": "/nonexistent/repo/xyz", "commit_summary": "x",
        "paths": ["a.txt"]})
    assert code == 8 and out["status"] == "CONTRACT_ERROR"


def test_cli_push_gate_all_clear_exit_0():
    code, out = _cli("push-gate", {
        "all_slices_done": True, "work_done_succeeded": True, "git_status_clean": True})
    assert code == 0 and out["should_push"] is True


def test_cli_push_gate_withheld_exit_7():
    code, out = _cli("push-gate", {
        "all_slices_done": True, "work_done_succeeded": False, "git_status_clean": True})
    assert code == 7 and out["status"] == "WITHHELD" and out["partial_prompt"]


def test_cli_push_gate_aborted_exit_7():
    code, out = _cli("push-gate", {
        "all_slices_done": True, "work_done_succeeded": True,
        "git_status_clean": True, "aborted": True})
    assert code == 7 and out["should_push"] is False


def test_cli_push_gate_missing_args_exit_3():
    code, out = _cli("push-gate", {"all_slices_done": True})
    assert code == 3 and out["status"] == "ERROR"


# --------------------------------------------------------------------------- #
# S8 — session/mode UX: mode model, AI-promotes-only confirm decision, pure
# renderers (confirm / escalation / session-summary), the 6 CLIs, and a smoke of
# the execplan-session-ack hook triad (env-isolated tempdir). The S2–S7 cases
# above stay green.
# --------------------------------------------------------------------------- #

HOOKS_DIR = os.path.expanduser("~/.claude/hooks")


def _cli_env(subcommand, payload, env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run([sys.executable, RUN_PY, subcommand],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


def _run_hook(hook_name, payload, state_dir):
    path = os.path.join(HOOKS_DIR, hook_name)
    env = dict(os.environ)
    env["EXECPLAN_ACK_STATE_DIR"] = state_dir
    env["CLAUDE_CODE_REMOTE"] = "false"
    proc = subprocess.run(["bash", path], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _confirm_slice(sid="S1", name="x", type="implementation",
                   agent_choice="routine", confirm_override=False):
    return run.Slice(id=sid, name=name, type=type, agent_choice=agent_choice,
                     confirm_override=confirm_override)


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


# --- mode model (A1) --- #

def test_normalize_mode_defaults_and_validates():
    assert run.DEFAULT_MODE == "observer"
    for blank in (None, "", "   "):
        assert run.normalize_mode(blank) == "observer"
    assert run.normalize_mode("Auto") == "auto"
    assert run.normalize_mode(" confirm ") == "confirm"
    assert run.normalize_mode("observer") == "observer"
    try:
        run.normalize_mode("wizard")
    except ValueError:
        return
    raise AssertionError("expected ValueError on unknown mode")


# --- confirm_override parsing (A2) --- #

def test_parse_register_6col_confirm_override_false():
    slices = run.parse_slice_register(REGISTER_MD)
    assert slices and all(s.confirm_override is False for s in slices)


def test_parse_register_7col_confirm_override():
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm |\n"
        "|----|------|------|--------------|-------|-----------|---------|\n"
        "| S1 | a | implementation | x | routine | — | yes |\n"
        "| S2 | b | implementation | y | routine | — | - |\n"
    )
    by = {s.id: s for s in run.parse_slice_register(md)}
    assert by["S1"].confirm_override is True
    assert by["S2"].confirm_override is False


# --- Layer-1 risk net (A2) --- #

def test_layer1_risky_matches_and_misses():
    assert run.layer1_risky(_confirm_slice(name="Database schema migration")) is True
    assert run.layer1_risky(_confirm_slice(name="Add a button")) is False


# --- confirm decision: monotonic, AI-promotes-only (A2) --- #

def test_slice_needs_confirm_truth_table():
    benign = _confirm_slice(name="Add a button")
    override = _confirm_slice(name="Add a button", confirm_override=True)
    risky_l1 = _confirm_slice(name="schema migration")
    # observer -> always False (orchestrator issues no auto-transitions)
    for s in (benign, override, risky_l1):
        assert run.slice_needs_confirm(s, mode="observer") is False
    # confirm -> always True
    for s in (benign, override, risky_l1):
        assert run.slice_needs_confirm(s, mode="confirm") is True
    # auto -> True iff risky (confirm_override OR layer1), else False
    assert run.slice_needs_confirm(benign, mode="auto") is False
    assert run.slice_needs_confirm(override, mode="auto") is True
    assert run.slice_needs_confirm(risky_l1, mode="auto") is True
    # monotonic: a risky slice is never demoted to False in confirm/auto
    for s in (override, risky_l1):
        assert run.slice_needs_confirm(s, mode="confirm") is True
        assert run.slice_needs_confirm(s, mode="auto") is True


def test_slice_needs_confirm_unknown_mode_raises():
    try:
        run.slice_needs_confirm(_confirm_slice(), mode="wizard")
    except ValueError:
        return
    raise AssertionError("expected ValueError on unknown mode")


# --- renderers (A3) --- #

def test_render_confirm_prompt_payload():
    s = _confirm_slice(sid="S3", name="ship it", agent_choice="routine",
                       confirm_override=True)
    p = run.render_confirm_prompt(s, mode="auto")
    assert p["slice_id"] == "S3"
    assert p["model_family"] == "sonnet"            # routine -> sonnet
    assert "confirm_override" in p["risky_reason"]
    assert len(p["options"]) == 3


def test_should_notify_on_escalation():
    assert run.should_notify_on_escalation("auto", "me@x") is True
    assert run.should_notify_on_escalation("auto", None) is False
    assert run.should_notify_on_escalation("auto", "") is False
    assert run.should_notify_on_escalation("confirm", "me@x") is False
    assert run.should_notify_on_escalation("observer", "me@x") is False


def test_render_escalation_surface_persisted_count():
    surf = run.render_escalation_surface(
        "S4", 3, [{"slice_id": "S1"}, {"slice_id": "S2"}],
        mode="auto", notify_recipient="me@x")
    assert surf["retry_count"] == 3                  # persisted count surfaced verbatim
    assert surf["completed_so_far"] == ["S1", "S2"]
    assert len(surf["in_band"]["options"]) == 3
    assert surf["notify"]["should_notify"] is True
    surf2 = run.render_escalation_surface("S4", 3, [], mode="confirm",
                                          notify_recipient="me@x")
    assert surf2["notify"]["should_notify"] is False  # not auto -> no notify
    assert surf2["retry_count"] == 3


def test_render_session_summary_buckets():
    m = {"S1": {"status": "completed"}, "S2": {"status": "committed"},
         "S3": {"status": "escalated"}, "S4": {"status": "started"}}
    out = run.render_session_summary(m)
    assert out["completed"] == ["S1"]
    assert out["in_flight"] == ["S2", "S4"]
    assert out["escalated"] == ["S3"]
    assert out["incomplete_remaining"] is True
    done = {"S1": {"status": "completed"}, "S2": {"status": "completed"}}
    assert run.render_session_summary(done)["incomplete_remaining"] is False


# --- CLIs (A4) --- #

def test_cli_mode_ok():
    code, out = _cli("mode", {"mode": "auto"})
    assert code == 0 and out["mode"] == "auto"


def test_cli_mode_unknown_exit_3():
    code, out = _cli("mode", {"mode": "wizard"})
    assert code == 3 and out["status"] == "ERROR"


def test_cli_confirm_gate_auto_risky():
    code, out = _cli("confirm-gate", {"id": "S1", "name": "ship",
                                      "mode": "auto", "confirm_override": True})
    assert code == 0 and out["needs_confirm"] is True and out["prompt"] is not None


def test_cli_confirm_gate_auto_benign():
    code, out = _cli("confirm-gate", {"id": "S2", "name": "Add a button",
                                      "type": "implementation", "mode": "auto"})
    assert code == 0 and out["needs_confirm"] is False and out["prompt"] is None


def test_cli_escalation_surface():
    code, out = _cli("escalation-surface", {"slice_id": "S4", "attempts": 3,
                                            "mode": "auto",
                                            "notify_recipient": "me@x"})
    assert code == 0 and out["retry_count"] == 3
    assert out["notify"]["should_notify"] is True


def test_cli_session_summary_inline():
    code, out = _cli("session-summary",
                     {"slice_execution": {"S1": {"status": "completed"},
                                          "S2": {"status": "started"}}})
    assert code == 0 and out["completed"] == ["S1"] and out["in_flight"] == ["S2"]


def test_cli_session_summary_state_path():
    with tempfile.TemporaryDirectory() as d:
        full = os.path.join(d, "topic__root.json")          # topic-state shape
        _write_json(full, {"phase": "implementation",
                           "slice_execution": {"S1": {"status": "completed"}}})
        code, out = _cli("session-summary", {"state_path": full})
        assert code == 0 and out["completed"] == ["S1"]
        mini = os.path.join(d, "plan.run-state.json")        # bare-map run-state
        _write_json(mini, {"S2": {"status": "escalated"}})
        code2, out2 = _cli("session-summary", {"state_path": mini})
        assert code2 == 0 and out2["escalated"] == ["S2"]


def test_cli_session_summary_non_json_state_path_is_empty():
    # LOAD-BEARING regression lock for the spine-surface self-heal crash. A
    # non-JSON state_path (a Markdown "spine" surface) must not crash
    # session-summary — it renders an empty summary. On UNPATCHED code
    # _read_json_or_none does an unguarded json.loads that raises
    # JSONDecodeError -> run.py exits nonzero (this test FAILS); after the
    # cmd_session_summary try/except it exits 0 with an empty summary.
    with tempfile.TemporaryDirectory() as d:
        md = os.path.join(d, "topic_THOUGHT.md")
        with open(md, "w", encoding="utf-8") as fh:
            fh.write("# Idea\n\nNarrative spine text, not JSON.\n")
        code, out = _cli("session-summary", {"state_path": md})
        assert code == 0 and out.get("completed") == []


def test_cli_set_and_clear_active_run():
    # A2: set-active-run writes a per-run pointer (run-<run_id>.json), not the
    # single global active-run.json; A4: clear is run_id-scoped.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        code, out = _cli_env("set-active-run",
                             {"owner_session_id": "OWNER",
                              "surface_path": "/tmp/x.json", "total_slices": 3}, env)
        assert code == 0
        run_id = out["run_id"]
        pointer = out["pointer"]
        assert os.path.basename(pointer) == f"run-{run_id}.json"
        assert os.path.exists(pointer)
        doc = json.loads(open(pointer, encoding="utf-8").read())
        assert doc["owner_session_id"] == "OWNER" and doc["total_slices"] == 3
        code2, out2 = _cli_env("clear-active-run",
                               {"run_id": run_id, "owner_session_id": "OWNER"}, env)
        assert code2 == 0 and out2["cleared"] is True
        assert not os.path.exists(pointer)


# --- Resume-scoped gate hook smoke (A1/A5/A6; env-isolated tempdir) --- #
# execplan-resume-gate-fix (2026-07-10): the retired SessionStart-arm +
# PreToolUse-`.*` block triad tests are replaced by these — the checkpoint now
# fires ONLY on the /execute-plan resume action, scoped to the specific run.

# Hook dir co-located with THIS module's tree (clone-relative, like RUN_PY), so
# hook tests exercise the CO-LOCATED (patched) hooks against the CO-LOCATED run.py
# (the hooks resolve run.py via BASH_SOURCE), rather than the hardcoded live
# ~/.claude/hooks that _run_hook targets. HERE == <root>/skills/execute-plan ->
# <root>/hooks. Resolves to the clone under experiment and to ~/.claude once
# promoted.
_LOCAL_HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "hooks")


def _run_local_hook(hook_name, payload, state_dir):
    path = os.path.join(_LOCAL_HOOKS_DIR, hook_name)
    env = dict(os.environ)
    env["EXECPLAN_ACK_STATE_DIR"] = state_dir
    env["CLAUDE_CODE_REMOTE"] = "false"
    proc = subprocess.run(["bash", path], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _arm_run(state_dir, owner="OWNER",
             surface="/tmp/topic-20260710002913_THOUGHT.md",
             total=3, worktree=None, handoff=None, walker="WALKER", pending=True):
    """Write a per-run pointer via the real set-active-run CLI (A2).

    `walker` defaults to a real walking session because since A5 only a GENUINELY
    IN-FLIGHT run gates the resume checkpoint — a run nobody is walking is listed,
    not acknowledged. Pass `walker=None` to arm a parked run, or `pending=False`
    for a pointer that is not armed at all."""
    payload = {"owner_session_id": owner, "surface_path": surface,
               "total_slices": total, "execution_pending": pending,
               "walking_session_id": walker}
    if worktree:
        payload["worktree_root"] = worktree
    if handoff:
        payload["pending_handoff"] = handoff
    _, out = _cli_env("set-active-run", payload,
                      {"EXECPLAN_ACK_STATE_DIR": state_dir})
    return out


def _skill_payload(session_id, skill="execute-plan"):
    return {"session_id": session_id, "tool_name": "Skill",
            "tool_input": {"skill": skill}}


def test_gate_hook_blocks_only_execplan_invocation():
    # A1 / C1: the resume-scoped gate fires ONLY on a /execute-plan Skill
    # invocation with a foreign, un-acked run present. A different skill and a
    # non-Skill tool are never gated (the retired `.*` block is gone).
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, handoff={"type": "continue-the-run", "dispatch": "hands-off",
                             "slice_id": "S2"})
        code_ep, _, err = _run_local_hook(
            "check-execplan-session-ack.sh", _skill_payload("FRESH"), d)
        assert code_ep == 2
        assert "topic" in err and "S2" in err          # A7: names slug + slice
        code_other, _, _ = _run_local_hook(
            "check-execplan-session-ack.sh", _skill_payload("FRESH", "close"), d)
        assert code_other == 0
        code_bash, _, _ = _run_local_hook(
            "check-execplan-session-ack.sh",
            {"session_id": "FRESH", "tool_name": "Bash",
             "tool_input": {"command": "ls"}}, d)
        assert code_bash == 0


def test_gate_hook_owner_and_acked_not_blocked():
    # A1: the owner continuing in-session is never re-gated; a session that acked
    # the run (via the clear hook) passes on the Proceed re-invocation (A6 keeps
    # the FIRST invocation prompting — the legitimate checkpoint is preserved).
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, handoff={"type": "continue-the-run", "dispatch": "attended",
                             "slice_id": "S1"})
        code_owner, _, _ = _run_local_hook(
            "check-execplan-session-ack.sh", _skill_payload("OWNER"), d)
        assert code_owner == 0
        code1, _, _ = _run_local_hook(
            "check-execplan-session-ack.sh", _skill_payload("FRESH"), d)
        assert code1 == 2                              # A6: genuine resume prompts
        code_clear, _, _ = _run_local_hook(
            "clear-execplan-session-ack.sh",
            {"session_id": "FRESH", "tool_name": "AskUserQuestion"}, d)
        assert code_clear == 0
        code2, _, _ = _run_local_hook(
            "check-execplan-session-ack.sh", _skill_payload("FRESH"), d)
        assert code2 == 0                              # acked → Proceed passes


def test_gc_hook_reaps_stale_markers():
    # A5: the SessionStart GC removes stale ack/pending markers + retired
    # per-session *.marker files past the TTL; fresh markers are preserved.
    with tempfile.TemporaryDirectory() as d:
        stale = os.path.join(d, "pending-OLD.json")
        legacy = os.path.join(d, "OLD.marker")
        fresh = os.path.join(d, "pending-NEW.json")
        for p in (stale, legacy, fresh):
            _write_json(p, {"pending": True})
        old = time.time() - 3 * 24 * 3600
        os.utime(stale, (old, old))
        os.utime(legacy, (old, old))
        code, _, _ = _run_local_hook("execplan-gc.sh", {}, d)   # 1440-min default
        assert code == 0
        assert not os.path.exists(stale)
        assert not os.path.exists(legacy)
        assert os.path.exists(fresh)                   # within TTL — preserved


# --------------------------------------------------------------------------- #
# execplan-resume-gate-fix (2026-07-10) — run.py-level coverage of the resume-
# scoped checkpoint state model: A2 per-run/worktree scoping, A3 self-heal + TTL,
# A4 owner-scoped clear, A7 informative prompt. (A1/A5/A6 covered by the hook
# smoke + the CLI tests above.)
# --------------------------------------------------------------------------- #

def test_run_id_is_topic_and_worktree_scoped():
    # A2: same topic path → same id; a worktree root changes the id (isolation).
    s = "/proj/Thoughts/topic-20260710002913_THOUGHT.md"
    assert run.compute_run_id(s) == run.compute_run_id(s)
    assert run.compute_run_id(s, "/wt/A") != run.compute_run_id(s, "/wt/B")
    assert run.compute_run_id(s, "/wt/A") != run.compute_run_id(s)


def test_surface_slug_strips_ts_and_type():
    assert run.surface_slug(
        "/x/Thoughts/execplan-resume-gate-fix-20260710002913_PLAN.md") \
        == "execplan-resume-gate-fix"
    assert run.surface_slug("/x/Thoughts/foo_THOUGHT.md") == "foo"
    assert run.surface_slug("") == "run"


# --------------------------------------------------------------------------- #
# S2 follow-up — `surface_slug` must not drift from the canonical base-slug
# locus `pre_plan_gates._slug_from_spine_path`.
#
# `surface_slug` is display-only, but the value it displays is a topic KEY: a
# gate block or a resume row that names a slug the topic is not filed under is
# wrong in exactly the domain this work is about. run.py deliberately does NOT
# import pre_plan_gates (dependency direction), so the grammar is COPIED there —
# and these tests are the only thing binding the copy to its original. If the
# canonical locus changes and the copy does not, they fail.
# --------------------------------------------------------------------------- #

_THOUGHTS_CORPUS = "~/repos/Projects/Thoughts"

# One row per filename shape that occurs on disk, including every shape the two
# grammars used to disagree on. Compared against the canonical locus, not
# against literals — the literals live in `_REAL_CORPUS_SAMPLES` below.
_SLUG_SHAPES = [
    # standard, timestamped — the shape the two grammars always agreed on
    "plan-gates-20260806223312_THOUGHT.md",
    "plan-gates-20260806223312_PLAN.md",
    "plan-gates-20260806223312_DESIGN.md",
    "plan-gates-20260806223312_RESEARCH.md",
    "plan-gates-20260806223312_CLAIMS.md",
    # scope-keyed, timestamped — THE divergent class
    "git-working-model-20260714130221_S1_PLAN.md",
    "clarification-v2-20260801100833_SEAL_PLAN.md",
    "thing-20260806223312_H_v2_B1_PLAN.md",
    # double-timestamped — the second divergent class
    "foo-20260101000000-20260102000000_PLAN.md",
    "foo-20260101000000-20260102000000_S1_PLAN.md",
    # advisory / legacy shapes carrying a timestamp
    "thing-20260806223312_THOUGHT_check.md",
    "thing-20260806223312_DOUBLECHECK_ab12cd34.md",
    "thing-20260806223312.md",
    # untimestamped — no anchor separates work name from scope key, so the
    # canonical answer KEEPS the scope key and the `_check` tail
    "legacy-topic_THOUGHT.md",
    "legacy-topic_PLAN.md",
    "legacy-topic.md",
    "legacy-topic_K_PLAN.md",
    "legacy-topic_THOUGHT_check.md",
    "foo__bar_THOUGHT.md",
    # nothing but a stamp: canonical yields "", the display default takes over
    "-20260806223312_PLAN.md",
]

# Real filenames present under `Projects/Thoughts/`, with the base slug typed BY
# HAND — computed by neither implementation, which is the point: the agreement
# assertions cannot tell "both right" from "both wrong together", so these
# literals are what carries the correctness claim. Sampled 2026-08-30; mirrors
# the sampling in hooks/tests/test_s2_slug_derivation_locus.py.
_REAL_CORPUS_SAMPLES = [
    ("git-working-model-20260714130221_THOUGHT.md", "git-working-model"),
    ("assessment-engine-20260727202257_PLAN.md", "assessment-engine"),
    ("git-working-model-20260714130221_S1_PLAN.md", "git-working-model"),
    ("audit-session-app-runs_THOUGHT.md", "audit-session-app-runs"),
    ("automate-plan-execution_S1_PLAN.md", "automate-plan-execution_S1"),
    ("git-working-model-20260714130221-20260719171000_REVIEW_REPORT.md",
     "git-working-model"),
]

_PRE_FIX_TYPE_RE = re.compile(
    r"_(THOUGHT|PLAN|DESIGN|RESEARCH|CLAIMS)(_check)?$", re.IGNORECASE)


def _pre_fix_surface_slug(surface_path):
    """Verbatim copy of the pre-fix `surface_slug` body — the TYPE suffix
    stripped BEFORE the timestamp. Kept ONLY as the characterization reference
    that proves the divergence was real, so a green result below cannot be
    vacuous."""
    name = os.path.basename(str(surface_path or "")).strip()
    name = re.sub(r"\.(md|json)$", "", name, flags=re.IGNORECASE)
    name = _PRE_FIX_TYPE_RE.sub("", name)
    name = re.sub(r"-\d{14}$", "", name)
    return name or "run"


def _canonical_slug_fn():
    """The canonical locus `pre_plan_gates._slug_from_spine_path`, imported HERE
    (a test may cross the layer boundary that run.py may not).

    An unavailable locus is a FAILURE, never a skip — a skip is precisely how a
    copied grammar re-diverges in silence."""
    hooks = os.path.abspath(os.path.join(HERE, "..", "..", "hooks"))
    assert os.path.isdir(hooks), f"canonical locus not found: {hooks}"
    if hooks not in sys.path:
        sys.path.insert(0, hooks)
    import pre_plan_gates  # noqa: E402
    return pre_plan_gates._slug_from_spine_path


def _expected_surface_slug(canonical, path):
    """The canonical base slug, plus the ONE surface-only convention
    `surface_slug` layers on it: a display field cannot be blank."""
    return canonical(path) or "run"


def test_surface_slug_never_drifts_from_the_canonical_locus():
    canonical = _canonical_slug_fn()
    disagreements = []
    for name in _SLUG_SHAPES:
        p = f"/x/Thoughts/{name}"
        got, want = run.surface_slug(p), _expected_surface_slug(canonical, p)
        if got != want:
            disagreements.append({"file": name, "surface": got, "canonical": want})
    assert disagreements == [], (
        f"{len(disagreements)} shape(s) where the display slug is not the "
        f"topic's real key: {disagreements[:5]}")

    # Non-vacuity: the divergence this closes must have been real on this table.
    was_divergent = [n for n in _SLUG_SHAPES
                     if _pre_fix_surface_slug(f"/x/Thoughts/{n}")
                     != _expected_surface_slug(canonical, f"/x/Thoughts/{n}")]
    assert was_divergent, (
        "characterization reference no longer diverges on any shape — this "
        "test would pass vacuously; re-add a scope-keyed/double-timestamped row")


def test_surface_slug_matches_hand_written_slugs_for_real_filenames():
    corpus_present = os.path.isdir(_THOUGHTS_CORPUS)
    for name, expected in _REAL_CORPUS_SAMPLES:
        if corpus_present:
            assert os.path.isfile(os.path.join(_THOUGHTS_CORPUS, name)), (
                f"sample gone from disk — re-pick a real file for {name!r}")
        assert run.surface_slug(f"{_THOUGHTS_CORPUS}/{name}") == expected, name


def test_surface_slug_agrees_with_the_canonical_locus_over_the_real_corpus():
    canonical = _canonical_slug_fn()
    if os.path.isdir(_THOUGHTS_CORPUS):
        names = sorted(n for n in os.listdir(_THOUGHTS_CORPUS)
                       if n.endswith(".md"))
        assert names, f"no artifacts under {_THOUGHTS_CORPUS}"
    else:
        # Never vacuous: with no corpus on disk, sweep the sampled REAL names
        # instead, and say so rather than skipping silently.
        names = [n for n, _ in _REAL_CORPUS_SAMPLES]

    divergent = []
    for name in names:
        p = os.path.join(_THOUGHTS_CORPUS, name)
        got, want = run.surface_slug(p), _expected_surface_slug(canonical, p)
        if got != want:
            divergent.append({"file": name, "surface": got, "canonical": want})
    assert divergent == [], (
        f"{len(divergent)} of {len(names)} artifact(s) display a slug that is "
        f"not the canonical topic key: {divergent[:5]}")

    was_divergent = [n for n in names
                     if _pre_fix_surface_slug(os.path.join(_THOUGHTS_CORPUS, n))
                     != _expected_surface_slug(
                         canonical, os.path.join(_THOUGHTS_CORPUS, n))]
    assert was_divergent, (
        "characterization reference no longer diverges on this corpus — this "
        "test would pass vacuously")


def test_surface_slug_surface_only_conventions_are_exactly_two():
    # The two things `surface_slug` adds on top of the canonical grammar, pinned
    # so a later "simplification" cannot quietly add a third.
    canonical = _canonical_slug_fn()
    # (1) a blank result becomes 'run' — a display field cannot be empty
    assert canonical("/x/Thoughts/-20260806223312_PLAN.md") == ""
    assert run.surface_slug("/x/Thoughts/-20260806223312_PLAN.md") == "run"
    assert run.surface_slug("") == "run"
    assert run.surface_slug(None) == "run"
    # (2) a `.json` run-POINTER basename is reduced first; canonical only ever
    #     sees `.md` artifacts, so this shape is outside its domain
    assert run.surface_slug("/s/legacy-topic_PLAN.json") == "legacy-topic"
    # …and nothing else: an untimestamped `_check` tail and a lowercase TYPE are
    # KEPT, exactly as the canonical locus keeps them.
    for name in ("legacy-topic_THOUGHT_check.md", "legacy-topic_plan.md"):
        p = f"/x/Thoughts/{name}"
        assert run.surface_slug(p) == canonical(p), name


def test_a2_concurrent_runs_are_isolated_by_worktree():
    # A2 / C3: two runs in distinct worktrees get distinct pointers; a session in
    # worktree A never sees worktree B's run (and vice-versa).
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        # walkers set: since A5 only a genuinely in-flight run gates the resume
        # checkpoint, and this test is about WORKTREE isolation, not the phase split.
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "A", "surface_path": "/p/tA_THOUGHT.md",
                         "total_slices": 1, "worktree_root": "/wt/A",
                         "execution_pending": True, "walking_session_id": "WALK_A"}, env)
        _, b = _cli_env("set-active-run",
                        {"owner_session_id": "B", "surface_path": "/p/tB_THOUGHT.md",
                         "total_slices": 1, "worktree_root": "/wt/B",
                         "execution_pending": True, "walking_session_id": "WALK_B"}, env)
        assert a["run_id"] != b["run_id"]
        assert os.path.exists(a["pointer"]) and os.path.exists(b["pointer"])
        # a fresh session in worktree A is gated by run A only, not run B
        dec_a = run.gate_check("FRESH_A", session_worktree="/wt/A", base=d)
        assert dec_a["block"] and dec_a["run_id"] == a["run_id"]
        dec_b = run.gate_check("FRESH_B", session_worktree="/wt/B", base=d)
        assert dec_b["block"] and dec_b["run_id"] == b["run_id"]


def test_a3_self_heal_completed_run():
    # A3: a run whose real state_path shows all slices completed self-heals
    # (removed on reap), and never gates.
    with tempfile.TemporaryDirectory() as d:
        state = os.path.join(d, "state.json")
        _write_json(state, {"slice_execution": {"S1": {"status": "completed"},
                                                "S2": {"status": "completed"}}})
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/p/t_THOUGHT.md",
                  "total_slices": 2, "state_path": state},
                 {"EXECPLAN_ACK_STATE_DIR": d})
        reaped = run.reap_stale_runs(base=d)
        assert len(reaped) == 1
        assert run.gate_check("FRESH", base=d)["block"] is False


def test_a3_stale_run_reaped_live_incomplete_untouched():
    # A3: a pointer past the TTL is reaped; a live, incomplete, in-TTL run is not.
    with tempfile.TemporaryDirectory() as d:
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/p/live_THOUGHT.md",
                  "total_slices": 3}, {"EXECPLAN_ACK_STATE_DIR": d})
        # now well before the pointer's created_at → age negative → not stale
        assert run.reap_stale_runs(base=d, now=time.time() - 3600, ttl=60) == []
        # now far after → age > ttl → stale → reaped
        reaped = run.reap_stale_runs(base=d, now=time.time() + 10 * 3600, ttl=60)
        assert len(reaped) == 1


def test_a4_clear_is_run_scoped_and_owner_guarded():
    # A4: clearing run A leaves run B intact; a non-owner cannot clear a run.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "A", "surface_path": "/p/a_THOUGHT.md",
                         "total_slices": 1, "worktree_root": "/wt/A"}, env)
        _, b = _cli_env("set-active-run",
                        {"owner_session_id": "B", "surface_path": "/p/b_THOUGHT.md",
                         "total_slices": 1, "worktree_root": "/wt/B"}, env)
        # non-owner clear is refused (guard)
        _, out_guard = _cli_env("clear-active-run",
                                {"run_id": a["run_id"], "owner_session_id": "B"}, env)
        assert out_guard["cleared"] is False
        assert os.path.exists(a["pointer"])
        # owner clear of A succeeds; B untouched
        _, out_a = _cli_env("clear-active-run",
                            {"run_id": a["run_id"], "owner_session_id": "A"}, env)
        assert out_a["cleared"] is True
        assert not os.path.exists(a["pointer"])
        assert os.path.exists(b["pointer"])


def test_a7_prompt_names_run_slice_and_dispatch():
    # A7: the prompt names the slug, the slice, and the dispatch verb.
    msg = run.render_resume_prompt("my-topic", "S3", "hands-off")
    assert "my-topic" in msg and "S3" in msg
    assert "auto-implements" in msg
    assert "Investigate" in msg and "Proceed" in msg and "Not now" in msg
    # and gate_check threads them through (walker set — since A5 only an in-flight
    # run gates; the identity/slice threading under test is unchanged)
    with tempfile.TemporaryDirectory() as d:
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER",
                  "surface_path": "/p/my-topic-20260710002913_THOUGHT.md",
                  "total_slices": 1, "execution_pending": True,
                  "walking_session_id": "WALKER",
                  "pending_handoff": {"type": "continue-the-run",
                                      "dispatch": "attended", "slice_id": "S9"}},
                 {"EXECPLAN_ACK_STATE_DIR": d})
        dec = run.gate_check("FRESH", base=d)
        assert dec["slug"] == "my-topic" and dec["slice_id"] == "S9"
        assert "S9" in dec["message"] and "my-topic" in dec["message"]


def test_migrate_legacy_pointer_folds_into_per_run():
    # A2 transitional: a pre-existing lone active-run.json migrates to run-<id>.json.
    with tempfile.TemporaryDirectory() as d:
        _write_json(os.path.join(d, "active-run.json"),
                    {"owner_session_id": "OWNER",
                     "surface_path": "/p/legacy_THOUGHT.md", "total_slices": 2})
        rid = run.migrate_legacy_pointer(d)
        assert rid == run.compute_run_id("/p/legacy_THOUGHT.md")
        assert not os.path.exists(os.path.join(d, "active-run.json"))
        assert os.path.exists(os.path.join(d, f"run-{rid}.json"))


# --------------------------------------------------------------------------- #
# S9 — end-of-plan close-out: per-slice implementation report (A16), diagnostic
# correlations (A17, captured-not-gated), and the observation-harvest collector
# (U5). Pure renderers/collectors over already-persisted state + 3 read-only
# CLIs. The S2–S8 cases above stay green.
# --------------------------------------------------------------------------- #

PFR_RUN_PY = os.path.join(os.path.dirname(HERE), "plan-followups-review", "run.py")

# A slice_execution map covering every report derivation path:
#   S1 completed (used == assigned, PROVABLE by the S5 gate; no captured used)
#   S2 escalated (used unknown -> "—"; may have aborted on mismatch)
#   S3 committed with captured used_model + model_aborts + tokens (nested result)
#   S4 started with assigned_model precedence over model_family
S9_MAP = {
    "S1": {"status": "completed", "model_family": "sonnet", "attempts": 1},
    "S2": {"status": "escalated", "model_family": "opus", "attempts": 3},
    "S3": {"status": "committed", "model_family": "sonnet", "attempts": 2,
           "result": {"used_model": "sonnet", "model_aborts": 1, "tokens": 5000}},
    "S4": {"status": "started", "assigned_model": "opus", "model_family": "sonnet"},
}


def _pfr_run(subcommand, payload, state_dir):
    env = dict(os.environ)
    env["PLAN_FOLLOWUPS_REVIEW_STATE_DIR"] = state_dir
    proc = subprocess.run([sys.executable, PFR_RUN_PY, subcommand],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return proc.returncode, out


# --- A16 report renderer --- #

def test_report_rows_and_derivation():
    rows = {r["slice_id"]: r for r in run.render_implementation_report(S9_MAP)}
    # completed: used derived == assigned (S5 fail-closed gate); aborts absent -> —
    assert rows["S1"] == {"slice_id": "S1", "assigned": "sonnet", "aborts": "—",
                          "used_model": "sonnet", "status": "completed"}
    # escalated: used NOT derivable -> —
    assert rows["S2"]["used_model"] == "—" and rows["S2"]["status"] == "escalated"
    # committed: captured used_model + aborts (0-distinct) surfaced verbatim
    assert rows["S3"]["used_model"] == "sonnet" and rows["S3"]["aborts"] == 1
    # started: assigned_model wins over model_family; used not derivable -> —
    assert rows["S4"]["assigned"] == "opus" and rows["S4"]["used_model"] == "—"


def test_report_aborts_zero_is_kept_not_dash():
    m = {"S1": {"status": "completed", "model_family": "sonnet",
                "result": {"model_aborts": 0}}}
    row = run.render_implementation_report(m)[0]
    assert row["aborts"] == 0   # a real 0 is kept, NOT collapsed to "—"


def test_report_empty_and_sorted():
    assert run.render_implementation_report({}) == []
    rows = run.render_implementation_report(
        {"S10": {"status": "completed"}, "S2": {"status": "started"}})
    assert [r["slice_id"] for r in rows] == ["S10", "S2"]   # str-sorted, stable


def test_report_markdown_table():
    md = run.render_implementation_report_md(run.render_implementation_report(S9_MAP))
    assert md.startswith("| Slice | Assigned | Aborts | Used Model | Status |")
    assert "| S3 | sonnet | 1 | sonnet | committed |" in md
    assert "_(no slices)_" in run.render_implementation_report_md([])


def test_report_both_tier_shape_identical():
    # Full tier nests slice_execution inside a topic-state doc; Minimal tier IS
    # the map. BookkeepingPort.all() yields the same map shape either way, so the
    # report rows are identical when fed the same execmap.
    full_execmap = dict(S9_MAP)
    minimal_execmap = dict(S9_MAP)
    assert (run.render_implementation_report(full_execmap)
            == run.render_implementation_report(minimal_execmap))


# --- A17 diagnostics (captured, NOT gated) --- #

def test_diagnostics_aggregation_and_not_gated():
    d = run.render_diagnostics(S9_MAP, restarts=2)
    assert d["slices"] == 4
    assert d["gated"] is False                       # captured, NOT gated
    assert d["tokens_per_family"] == {"sonnet": 5000}
    assert d["model_aborts_total"] == 1 and d["model_abort_rate"] == 0.25
    assert d["restart_count"] == 2
    assert d["captured"]["model_aborts_slices"] == 1
    assert d["captured"]["tokens_slices"] == 1


def test_diagnostics_empty_never_raises():
    d = run.render_diagnostics({})                   # must not raise
    assert d["slices"] == 0 and d["model_abort_rate"] == 0
    assert d["tokens_per_family"] == {} and d["restart_count"] is None
    assert d["gated"] is False


def test_diagnostics_absent_fields_zero_coverage():
    d = run.render_diagnostics({"S1": {"status": "completed",
                                       "model_family": "sonnet"}})
    assert d["captured"] == {"tokens_slices": 0, "model_aborts_slices": 0,
                             "conformance_failures_slices": 0,
                             "routine_intervention_slices": 0,
                             "timing_slices": 0, "attended_slices": 0}
    assert d["conformance_failure_rate"] == 0


# --- U5 observation collector --- #

def test_collect_observations_dispatched_strings_and_dicts():
    dispatched = [
        {"slice_id": "S1", "result": {"observations": ["stale premise X", "  "]}},
        {"slice_id": "S2", "result": {"observations": [{"title": "T2", "body": "B2"},
                                                        {"text": "B3"}]}},
        {"slice_id": "S3", "result": {}},
    ]
    obs = run.collect_observations(dispatched)
    assert [o["id"] for o in obs] == ["S1-obs1", "S2-obs1", "S2-obs2"]  # blank skipped
    assert obs[0]["source_ref"] == "S1" and obs[1]["title"] == "T2"
    assert obs[2]["body"] == "B3" and obs[2]["title"] == "B3"


def test_collect_observations_map_and_empty():
    assert run.collect_observations(S9_MAP) == []     # S9_MAP has no observations
    assert run.collect_observations([]) == []
    m = {"S1": {"result": {"observations": ["only one"]}}}
    assert run.collect_observations(m) == [
        {"id": "S1-obs1", "title": "only one", "body": "only one",
         "source_ref": "S1"}]


def test_collect_observations_output_passes_subskill_schema():
    obs = run.collect_observations([
        {"slice_id": "S1", "result": {"observations": ["o1", {"title": "t", "body": "b"}]}}])
    with tempfile.TemporaryDirectory() as d:
        code, out = _pfr_run("init", {"session_id": "S9-xcheck",
                                      "observations": obs}, d)
        assert code == 0 and out["status"] == "INITIALIZED" and out["total"] == 2


# --- CLIs --- #

def test_cli_report_inline():
    code, out = _cli("report", {"slice_execution": S9_MAP})
    assert code == 0 and out["count"] == 4
    assert any(r["slice_id"] == "S3" and r["aborts"] == 1 for r in out["rows"])
    assert "Used Model" in out["markdown"]


def test_cli_report_state_path_both_tiers():
    with tempfile.TemporaryDirectory() as d:
        full = os.path.join(d, "topic__root.json")
        _write_json(full, {"phase": "implementation", "slice_execution": S9_MAP})
        c1, o1 = _cli("report", {"state_path": full})
        mini = os.path.join(d, "plan.run-state.json")
        _write_json(mini, S9_MAP)
        c2, o2 = _cli("report", {"state_path": mini})
        assert c1 == 0 and c2 == 0 and o1["rows"] == o2["rows"]   # tier-identical


def test_cli_report_missing_input_exit_3():
    code, out = _cli("report", {})
    assert code == 3 and out["status"] == "ERROR"


def test_cli_diagnostics_always_exit_0():
    code, out = _cli("diagnostics", {"slice_execution": S9_MAP, "restarts": 1})
    assert code == 0 and out["gated"] is False and out["restart_count"] == 1
    code2, out2 = _cli("diagnostics", {"slice_execution": {}})
    assert code2 == 0 and out2["slices"] == 0          # empty still exit 0


def test_cli_diagnostics_missing_input_exit_3():
    code, _ = _cli("diagnostics", {})
    assert code == 3


def test_cli_harvest_observations_dispatched_and_map():
    dispatched = [{"slice_id": "S1", "result": {"observations": ["x", "y"]}}]
    code, out = _cli("harvest-observations", {"dispatched": dispatched})
    assert code == 0 and out["count"] == 2 and out["observations"][0]["id"] == "S1-obs1"
    code2, out2 = _cli("harvest-observations", {"slice_execution": S9_MAP})
    assert code2 == 0 and out2["count"] == 0           # S9_MAP has no observations


def test_cli_harvest_observations_bad_dispatched_exit_3():
    code, out = _cli("harvest-observations", {"dispatched": "not-a-list"})
    assert code == 3 and out["status"] == "ERROR"


# --------------------------------------------------------------------------- #
# S10 — END-TO-END implementation verification. ONE run of the fully-wired,
# REAL-adapter pipeline against a representative ~7-slice register, asserting the
# locked Desired Outcome claims C1–C6 as observable properties of that run. Per
# the Guiding Policy: real adapters at EVERY Python-reachable seam (Full
# persistence, real git commit + guard against an ISOLATED temp repo, real
# transcript model-pin against fixture transcripts, real code-layer verify, real
# conformance verdict normalization); fakes ONLY at the two genuinely-uncrossable
# boundaries (the SpawnPort Agent spawn; the SKILL-driven AskUserQuestion /
# /double-check surfaces). Grown through the Cockburn 4-step — real-to-real on the
# Python axis (git / files / transcripts all run from Python). NO run.py change:
# the S2–S9 cases above stay green by construction. (Plan S10 / A1–A5.)
# --------------------------------------------------------------------------- #

# A1 — representative register: 2 parallel-eligible roots (S1, S2) + a fan-in
# merge (S3) + a chained tail (S4→S6→S7); routine/more_capable mix; exactly one
# risky slice (S4 — confirm_override AND a Layer-1 "migration" name match); ends
# in an implementation_verification slice (S7). Benign slice names deliberately
# avoid every DEFAULT_RISK_PATTERN so the confirm-trigger set is exactly {S4}.
E2E_REGISTER_MD = (
    "Representative S10 end-to-end register (test fixture — not a real plan).\n"
    "\n"
    "#### Slices\n"
    "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Write targets |\n"
    "|----|------|------|--------------|-------|-----------|---------|---------------|\n"
    "| S1 | Foundation A | implementation | walking_skeleton | routine | — | - | `S1.txt` |\n"
    "| S2 | Foundation B | implementation | walking_skeleton | more_capable | — | - | `S2.txt` |\n"
    "| S3 | Core merge | implementation | different_ways | routine | S1, S2 | - | `S3.txt` |\n"
    "| S4 | Risky schema migration | implementation | sentence_extension | more_capable | S3 | yes | `S4.txt` |\n"
    "| S5 | Feature X | implementation | partial_step | routine | S3 | - | `S5.txt` |\n"
    "| S6 | Feature Y | implementation | partial_step | routine | S4, S5 | - | `S6.txt` |\n"
    "| S7 | End-to-end verify | implementation_verification | knowledge_vs_implementation | more_capable | S6 | - | `S7.txt` |\n"
)

_E2E_ORDER = ["S1", "S2", "S3", "S4", "S5", "S6", "S7"]
_E2E_FAMILY = {"S1": "sonnet", "S2": "opus", "S3": "sonnet", "S4": "opus",
               "S5": "sonnet", "S6": "sonnet", "S7": "opus"}
_FAMILY_MODEL_ID = {"sonnet": "claude-sonnet-4-6", "opus": "claude-opus-4-7"}


class _E2ESpawn(run.SpawnPort):
    """E2E spawn fake — the ONLY faked seam (Python cannot invoke the Agent tool).
    It does what a real agent would for the downstream REAL seams: writes the
    slice's work file into the temp repo (so the real GitCommitAdapter has content
    to commit) and returns a result threading the slice's transcript locator (read
    by the REAL TranscriptModelPinAdapter), conformance verdict (read by the REAL
    DoubleCheckConformanceAdapter), observations, and captured diagnostics."""

    def __init__(self, repo_dir, transcripts, *, observations=None,
                 conformance="PASS"):
        self.repo_dir = repo_dir
        self.transcripts = transcripts          # slice_id -> transcript path
        self.observations = observations or {}
        self.conformance = conformance
        self.calls = []

    def spawn(self, slice_id, model_family, prompt) -> dict:
        self.calls.append(slice_id)
        with open(os.path.join(self.repo_dir, f"{slice_id}.txt"),
                  "w", encoding="utf-8") as fh:
            fh.write(f"slice {slice_id} done by {model_family}\n")
        res = {
            "status": "ok", "slice_id": slice_id, "model_family": model_family,
            "transcript_path": self.transcripts[slice_id],
            "conformance_verdict": self.conformance,
            "used_model": model_family, "model_aborts": 0, "tokens": 1000,
        }
        obs = self.observations.get(slice_id)
        if obs:
            res["observations"] = obs
        return res


def _git_log_subjects(repo_dir):
    """The commit subjects on the temp repo's branch, newest first (real git)."""
    res = subprocess.run(["git", "-C", repo_dir, "log", "--format=%s"],
                         capture_output=True, text=True)
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


def _e2e_transcripts(tmp, slices, *, wrong_model_slice=None, wrong_family="sonnet"):
    """Write a fixture transcript per slice: the slice's CORRECT assigned family
    by default; a deliberately WRONG family for `wrong_model_slice` (to exercise
    the real model-pin hard-abort). Returns {slice_id: transcript_path}."""
    tdir = os.path.join(tmp, "transcripts")
    os.makedirs(tdir, exist_ok=True)
    out = {}
    for s in slices:
        fam = run.agent_choice_to_model_family(s.agent_choice)
        if s.id == wrong_model_slice:
            fam = wrong_family
        path = os.path.join(tdir, f"{s.id}.jsonl")
        _write_transcript(path, _FAMILY_MODEL_ID[fam])
        out[s.id] = path
    return out


def _run_e2e(tmp, *, wrong_model_slice=None, observations=None):
    """Drive ONE run of the fully-wired REAL-adapter pipeline over E2E_REGISTER_MD.

    REAL at every Python-reachable seam: FullBookkeepingAdapter (persists to a
    temp topic-state JSON OUTSIDE the repo), GitCommitGuard + GitCommitAdapter
    (an ISOLATED temp git repo on a non-protected branch — verification isolation,
    never a live repo), TranscriptModelPinAdapter (fixture transcripts),
    make_code_verify (re-reads the slice's work file), make_conformance_check
    (DoubleCheckConformanceAdapter). FAKE only at _E2ESpawn. Returns the run
    handle for assertions; propagates EscalationRequired on a wrong-model slice."""
    repo = os.path.join(tmp, "repo")
    _init_temp_git_repo(repo)                       # feature branch + history
    slices = run.parse_slice_register(E2E_REGISTER_MD)
    transcripts = _e2e_transcripts(tmp, slices, wrong_model_slice=wrong_model_slice)
    state_path = os.path.join(tmp, "topic__root.json")   # OUTSIDE the repo
    bk = run.FullBookkeepingAdapter(state_path)
    spawn = _E2ESpawn(repo, transcripts, observations=observations)

    def code_check(s, result):                      # REAL code-layer (slice-aware)
        f = os.path.join(repo, f"{s.id}.txt")
        if os.path.exists(f):
            with open(f, encoding="utf-8") as fh:
                if f"slice {s.id} done" in fh.read():
                    return {"ok": True, "detail": "work file present"}
        return {"ok": False, "detail": "missing work file"}

    result = run.run_dispatch_loop(
        slices, bk, spawn,
        commit=run.GitCommitAdapter(repo_dir=repo),
        commit_guard=run.GitCommitGuard(repo_dir=repo),
        pre_commit_verify=run.make_model_pin_verify(run.TranscriptModelPinAdapter()),
        pre_commit_codecheck=run.make_code_verify(code_check=code_check),
        post_commit_conformance=run.make_conformance_check(
            run.DoubleCheckConformanceAdapter()),
    )
    return {"result": result, "bookkeeping": bk, "repo": repo, "spawn": spawn,
            "slices": slices, "state_path": state_path}


# --- A1: the register parses + resolves + the DAG walks without deadlock --- #

def test_e2e_register_parses_and_resolves():
    slices = run.parse_slice_register(E2E_REGISTER_MD)
    assert [s.id for s in slices] == _E2E_ORDER
    fam = {s.id: run.agent_choice_to_model_family(s.agent_choice) for s in slices}
    assert fam == _E2E_FAMILY                          # routine/more_capable mix
    assert {s.id for s in slices if s.confirm_override} == {"S4"}  # one risky slice
    assert slices[-1].type == "implementation_verification"       # verification tail
    # DAG resolves (no deadlock): a dry in-memory walk completes all 7 in order.
    res = run.run_dispatch_loop(slices, run.InMemoryBookkeepingAdapter(),
                                run.FakeSpawnAdapter())
    assert res["summary"]["order"] == _E2E_ORDER


# --- A3: C1 (unattended walk) + C2 (gates compose; real per-slice commits) --- #

def test_e2e_happy_path_full_pipeline():
    with tempfile.TemporaryDirectory() as tmp:
        h = _run_e2e(tmp, observations={"S5": ["stale premise surfaced in S5"]})
        # C1: ONE run_dispatch_loop call walks the whole DAG in dependency order,
        # unattended — no per-slice operator command between slices.
        assert h["result"]["summary"]["order"] == _E2E_ORDER
        assert h["result"]["summary"]["completed"] == 7
        assert h["spawn"].calls == _E2E_ORDER          # one spawn per slice, in order
        bk = h["bookkeeping"]
        for sid in _E2E_ORDER:
            entry = bk.get(sid)
            assert entry["status"] == "completed"      # 3-checkpoint machine reached completed
            assert entry["attempts"] == 1              # happy path, no retry
        # state survived as a REAL persisted file re-readable from a fresh adapter
        fresh = run.FullBookkeepingAdapter(h["state_path"])
        assert all(fresh.is_completed(sid) for sid in _E2E_ORDER)
        # C2: exactly ONE <slice_id>:-prefixed commit per slice in the REAL repo,
        # each landed only after model-pin + code-layer passed (grading order).
        subjects = _git_log_subjects(h["repo"])
        for sid in _E2E_ORDER:
            assert sum(1 for s in subjects if s.startswith(f"{sid}: ")) == 1, sid


def test_e2e_wrong_model_hard_aborts_before_commit():
    # C2 (negative): a slice whose transcript ran the WRONG family is hard-aborted
    # by the REAL model-pin check BEFORE any commit; after retry-2x it escalates,
    # halting the walk. The completed slices before it ARE committed; it is not.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _run_e2e(tmp, wrong_model_slice="S4")      # S4 expects opus; transcript says sonnet
        except run.EscalationRequired as e:
            assert e.slice_id == "S4"
            assert e.attempts == 3
            repo = os.path.join(tmp, "repo")
            subjects = _git_log_subjects(repo)
            assert any(s.startswith("S3: ") for s in subjects)   # predecessor committed
            assert not any(s.startswith("S4: ") for s in subjects)  # wrong-model NOT committed
            bk = run.FullBookkeepingAdapter(os.path.join(tmp, "topic__root.json"))
            assert bk.get("S4")["status"] == "escalated"
            assert bk.is_completed("S4") is False
            assert bk.is_completed("S5") is False        # walk halted; dependents not run
            return
        raise AssertionError("expected EscalationRequired on a wrong-model slice")


# --- A4: C3 (decision-boundary triggers exact) + C5 (resume explains state) --- #

def test_e2e_decision_boundary_triggers_exact():
    # The harness-reachable trigger proxy: across the WHOLE register, the confirm
    # decision fires on the risky slice S4 ONLY (never a benign slice), monotonic
    # by mode. (The operator-facing AskUserQuestion/ack surfacing is the
    # Python-uncrossable SKILL/hook boundary — covered by the S8 hook-triad tests
    # above + the SKILL.md S10 walkthrough, not re-driven here.)
    slices = run.parse_slice_register(E2E_REGISTER_MD)
    auto = {s.id for s in slices if run.slice_needs_confirm(s, mode="auto")}
    assert auto == {"S4"}                                # exactly the risky slice
    assert all(run.slice_needs_confirm(s, mode="confirm") for s in slices)  # confirm: all
    assert not any(run.slice_needs_confirm(s, mode="observer") for s in slices)  # observer: none


def test_e2e_resume_explains_state():
    # C5: a paused/crashed mid-run state — persisted via the REAL FullBookkeeping
    # adapter to a REAL file — is explained on re-entry by the production summary
    # (in-process and via the session-summary CLI over the state_path).
    with tempfile.TemporaryDirectory() as tmp:
        state_path = os.path.join(tmp, "topic__root.json")
        bk = run.FullBookkeepingAdapter(state_path)
        for sid, fam in (("S1", "sonnet"), ("S2", "opus")):
            bk.record_attempt(sid, model_family=fam)
            bk.mark_committed(sid, result={})
            bk.mark_completed(sid, result={})
        bk.record_attempt("S3", model_family="sonnet")  # in-flight (started, not done)
        summary = run.render_session_summary(bk.all())
        assert summary["completed"] == ["S1", "S2"]
        assert summary["in_flight"] == ["S3"]
        assert summary["incomplete_remaining"] is True
        code, out = _cli("session-summary", {"state_path": state_path})
        assert code == 0 and out["completed"] == ["S1", "S2"] and out["in_flight"] == ["S3"]


# --- A5: C4 (close-out trio + push gate over the REAL persisted run state) --- #

def test_e2e_close_out_over_real_persisted_state():
    with tempfile.TemporaryDirectory() as tmp:
        h = _run_e2e(tmp, observations={
            "S5": ["stale premise surfaced in S5", "perf cliff at S5"],
            "S6": [{"title": "Schema drift", "body": "S6 touched the schema"}],
        })
        execmap = h["bookkeeping"].all()                # the REAL persisted slice_execution
        # report: a completed slice's used_model == assigned (PROVABLE by the S5
        # fail-closed model-pin gate — a wrong-model run never reaches commit).
        rows = {r["slice_id"]: r for r in run.render_implementation_report(execmap)}
        for sid in _E2E_ORDER:
            assert rows[sid]["status"] == "completed"
            assert rows[sid]["used_model"] == _E2E_FAMILY[sid]
            assert rows[sid]["assigned"] == _E2E_FAMILY[sid]
        # diagnostics: captured, NOT gated; coverage reflects the threaded tokens.
        diag = run.render_diagnostics(execmap)
        assert diag["gated"] is False and diag["slices"] == 7
        assert diag["captured"]["tokens_slices"] == 7
        # harvest: the run's threaded observations come through in the sub-skill
        # input contract; cross-skill-accepted by plan-followups-review init.
        obs = run.collect_observations(execmap)
        ids = [o["id"] for o in obs]
        assert ids == ["S5-obs1", "S5-obs2", "S6-obs1"]
        for o in obs:
            assert set(o) == {"id", "title", "body", "source_ref"}
        assert next(o for o in obs if o["id"] == "S6-obs1")["title"] == "Schema drift"
        code, out = _pfr_run("init", {"session_id": "S10-e2e-xcheck",
                                      "observations": obs}, tmp)
        assert code == 0 and out["status"] == "INITIALIZED" and out["total"] == 3
        # push gate: should_push when the whole run is done; withheld on abort.
        d_done = run.UnifiedGitContract.evaluate_push_gate(
            all_slices_done=True, work_done_succeeded=True, git_status_clean=True)
        assert d_done.should_push is True
        d_abort = run.UnifiedGitContract.evaluate_push_gate(
            all_slices_done=False, work_done_succeeded=False,
            git_status_clean=True, aborted=True)
        assert d_abort.should_push is False and d_abort.partial_prompt


# --------------------------------------------------------------------------- #
# NS1 (v2) — cross-session typed handoff: dispatch axis + HandoffRecord carrier
# + code-derived typed handoff (continue-the-run arm) + cross-session resume.
# --------------------------------------------------------------------------- #

def _ns1_slice(sid="S1", name="x", type="implementation",
               agent_choice="routine", depends_on=(), confirm_override=False,
               dispatch=None):
    return run.Slice(id=sid, name=name, type=type, agent_choice=agent_choice,
                     depends_on=tuple(depends_on),
                     confirm_override=confirm_override, dispatch=dispatch)


# --- additive dispatch field, still frozen --- #

def test_slice_dispatch_field_additive_and_frozen():
    s = _ns1_slice(dispatch="hands-off")
    assert s.dispatch == "hands-off"
    # default unspecified
    assert _ns1_slice().dispatch is None
    # still frozen
    try:
        s.dispatch = "attended"
    except Exception:
        return
    raise AssertionError("Slice must stay frozen")


# --- resolve_dispatch precedence (design #1) --- #

def test_resolve_dispatch_precedence():
    # explicit valid wins (case/space-insensitive)
    assert run.resolve_dispatch(_ns1_slice(dispatch="hands-off")) == "hands-off"
    assert run.resolve_dispatch(_ns1_slice(dispatch=" Out-Of-Session ")) == "out-of-session"
    # confirm_override True aliases to attended when no explicit dispatch
    assert run.resolve_dispatch(_ns1_slice(confirm_override=True)) == "attended"
    # conservative default: unspecified -> attended (pause-when-unsure)
    assert run.resolve_dispatch(_ns1_slice()) == "attended"
    # explicit dispatch beats the confirm_override alias
    assert run.resolve_dispatch(
        _ns1_slice(dispatch="hands-off", confirm_override=True)) == "hands-off"


def test_resolve_dispatch_unknown_raises():
    try:
        run.resolve_dispatch(_ns1_slice(dispatch="wizard"))
    except ValueError:
        return
    raise AssertionError("unknown dispatch must raise (no silent default)")


# --- plan-needing predicate (Q1/GP3) --- #

def test_slice_is_plan_needing():
    assert run.slice_is_plan_needing(_ns1_slice(type="plan")) is True
    assert run.slice_is_plan_needing(_ns1_slice(type="needs-plan")) is True
    assert run.slice_is_plan_needing(_ns1_slice(type="Plan_Then_Implement")) is True
    assert run.slice_is_plan_needing(_ns1_slice(type="implementation")) is False
    assert run.slice_is_plan_needing(
        _ns1_slice(type="implementation_verification")) is False


# --- HandoffRecord value object round-trip (design #3) --- #

def test_handoff_record_round_trip():
    hr = run.HandoffRecord(type="continue-the-run", dispatch="hands-off",
                           slice_id="S1")
    d = hr.to_dict()
    assert d == {"type": "continue-the-run", "dispatch": "hands-off",
                 "slice_id": "S1", "write_targets": [], "plan_path": None}
    assert run.HandoffRecord.from_dict(d) == hr
    # round-trip preserves write_targets + plan_path
    hr2 = run.HandoffRecord(type="implement-this-slice", dispatch="attended",
                            slice_id="S3", write_targets=("a", "b"),
                            plan_path="/x/_PLAN.md")
    assert run.HandoffRecord.from_dict(hr2.to_dict()) == hr2


def test_handoff_record_from_dict_rejects_malformed():
    bad = [
        {},                                                   # missing all
        {"type": "continue-the-run", "dispatch": "hands-off"},  # no slice_id
        {"type": "bogus", "dispatch": "hands-off", "slice_id": "S1"},  # bad type
        {"type": "continue-the-run", "dispatch": "nope", "slice_id": "S1"},  # bad dispatch
        "not a dict",
    ]
    for d in bad:
        try:
            run.HandoffRecord.from_dict(d)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {d!r}")


def test_handoff_record_is_frozen():
    hr = run.HandoffRecord(type="continue-the-run", dispatch="hands-off",
                           slice_id="S1")
    try:
        hr.slice_id = "S2"
    except Exception:
        return
    raise AssertionError("HandoffRecord must be a frozen value object")


# --- no HandoffPort (design #3 / OQ6 keystone decision) --- #

def test_no_handoff_port_exists():
    assert not hasattr(run, "HandoffPort"), (
        "a HandoffPort must NOT exist — the handoff is a computed value object "
        "(design #3 rejected the port as the wrong abstraction)")


# --- code-derived typed handoff: continue-the-run arm (design #4) --- #

def test_compute_handoff_type_continue_the_run():
    assert run.compute_handoff_type(_ns1_slice(type="implementation")) == "continue-the-run"


def test_compute_handoff_type_full_truth_table():
    # NS2: the full 3-way table.
    plain = _ns1_slice(type="implementation")
    plan_needing = _ns1_slice(type="plan")
    assert run.compute_handoff_type(plain, plan_exists=False) == "continue-the-run"
    assert run.compute_handoff_type(plain, plan_exists=True) == "continue-the-run"
    assert run.compute_handoff_type(plan_needing, plan_exists=False) == "plan-this-slice"
    assert run.compute_handoff_type(plan_needing, plan_exists=True) == "implement-this-slice"


def test_compute_handoff_next_ready_continue_hands_off():
    slices = [
        _ns1_slice("S1", dispatch="hands-off"),
        _ns1_slice("S2", depends_on=("S1",), dispatch="hands-off"),
    ]
    bk = run.InMemoryBookkeepingAdapter()
    hr = run.compute_handoff(slices, bk)
    assert hr.slice_id == "S1" and hr.type == "continue-the-run"
    assert hr.dispatch == "hands-off"
    # after S1 completes, the next ready slice is S2
    bk.mark_completed("S1", result={"ok": True})
    assert run.compute_handoff(slices, bk).slice_id == "S2"


def test_compute_handoff_none_when_all_complete():
    slices = [_ns1_slice("S1", dispatch="hands-off")]
    bk = run.InMemoryBookkeepingAdapter()
    bk.mark_completed("S1", result={"ok": True})
    assert run.compute_handoff(slices, bk) is None


def test_compute_handoff_reads_dispatch_verbatim():
    # an unspecified dispatch resolves to the conservative attended default
    slices = [_ns1_slice("S1")]
    hr = run.compute_handoff(slices, run.InMemoryBookkeepingAdapter())
    assert hr.dispatch == "attended"


# --- NS2: interleaved just-in-time DAG walk + plan_path_for + flat model --- #

def test_compute_handoff_just_in_time_plan_after_deps():
    # S2 (plan-needing) depends on S1. It must NOT be surfaced for planning until
    # S1 completes (just-in-time, no plan-all-in-advance).
    slices = [
        _ns1_slice("S1", type="implementation", dispatch="hands-off"),
        _ns1_slice("S2", type="plan", depends_on=("S1",)),
    ]
    bk = run.InMemoryBookkeepingAdapter()
    first = run.compute_handoff(slices, bk)
    assert first.slice_id == "S1" and first.type == "continue-the-run"
    # S2 is still blocked — never surfaced before its dep completes
    bk.mark_completed("S1", result={"ok": True})
    second = run.compute_handoff(slices, bk)
    assert second.slice_id == "S2" and second.type == "plan-this-slice"


def test_compute_handoff_implement_flip_with_plan_path():
    slices = [_ns1_slice("S1", type="plan")]
    bk = run.InMemoryBookkeepingAdapter()
    hr = run.compute_handoff(
        slices, bk,
        plan_exists=(lambda sid: True),
        plan_path_for=(lambda sid: f"/Thoughts/{sid}_PLAN.md"))
    assert hr.type == "implement-this-slice"
    assert hr.plan_path == "/Thoughts/S1_PLAN.md"


def test_compute_handoff_flat_no_recursion():
    # The flat model: exactly ONE HandoffRecord per ready slice, never a nested
    # structure (a too-big slice becomes its own referenced Thought, GP3).
    slices = [_ns1_slice("S1", type="plan")]
    hr = run.compute_handoff(slices, run.InMemoryBookkeepingAdapter())
    assert isinstance(hr, run.HandoffRecord)
    # no nested handoff field anywhere in the value object
    assert "handoff" not in hr.to_dict()


def test_compute_handoff_type_unaffected_by_plan_exists_when_plain():
    # plan_exists must not change a non-plan-needing slice's type
    plain = _ns1_slice(type="implementation_verification")
    assert run.compute_handoff_type(plain, plan_exists=True) == "continue-the-run"


# --- register Dispatch column (NS1, position 8) --- #

def test_parse_register_dispatch_column():
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Dispatch |\n"
        "|----|------|------|--------------|-------|-----------|---------|----------|\n"
        "| S1 | a | implementation | x | routine | — | - | hands-off |\n"
        "| S2 | b | implementation | y | routine | — | - | — |\n"
    )
    by = {s.id: s for s in run.parse_slice_register(md)}
    assert by["S1"].dispatch == "hands-off"
    assert by["S2"].dispatch is None          # blank -> None -> resolve to default
    assert run.resolve_dispatch(by["S2"]) == "attended"


def test_parse_register_no_dispatch_column_backcompat():
    # the 6-col REGISTER_MD fixture yields dispatch=None on every slice
    assert all(s.dispatch is None for s in run.parse_slice_register(REGISTER_MD))


# --- the cross-session resume read (the NS1 keystone) --- #

def test_read_pending_handoff_roundtrip_function_level():
    with tempfile.TemporaryDirectory() as d:
        hr = run.HandoffRecord(type="continue-the-run", dispatch="hands-off",
                               slice_id="S1")
        _write_json(os.path.join(d, "active-run.json"),
                    {"owner_session_id": "OWNER", "surface_path": "/x",
                     "pending_handoff": hr.to_dict()})
        got = run.read_pending_handoff(pointer_dir=d)
        assert got == hr
        assert run.render_resume(got)["next"] == "dispatch-hands-off"


def test_read_pending_handoff_none_when_absent():
    with tempfile.TemporaryDirectory() as d:
        assert run.read_pending_handoff(pointer_dir=d) is None
        _write_json(os.path.join(d, "active-run.json"),
                    {"owner_session_id": "OWNER", "surface_path": "/x"})
        assert run.read_pending_handoff(pointer_dir=d) is None  # no pending_handoff key


def test_render_resume_none_handoff():
    assert run.render_resume(None)["action"] == "none"


# --- CLI: the full cross-session walking skeleton (arm -> fresh-session resume) #

def test_cli_compute_handoff():
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Dispatch |\n"
        "|----|------|------|--------------|-------|-----------|---------|----------|\n"
        "| S1 | a | implementation | x | routine | — | - | hands-off |\n"
    )
    code, out = _cli("compute-handoff", {"register_markdown": md})
    assert code == 0
    assert out["handoff"]["slice_id"] == "S1"
    assert out["handoff"]["type"] == "continue-the-run"
    assert out["handoff"]["dispatch"] == "hands-off"


def test_cli_compute_handoff_null_when_complete():
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | a | implementation | x | routine | — |\n"
    )
    code, out = _cli("compute-handoff",
                     {"register_markdown": md,
                      "slice_execution": {"S1": {"status": "completed"}}})
    assert code == 0 and out["handoff"] is None


def test_cli_compute_handoff_plan_needing_arms():
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | a | plan | x | more_capable | — |\n"
    )
    # no plan on disk -> plan-this-slice
    code, out = _cli("compute-handoff", {"register_markdown": md})
    assert code == 0 and out["handoff"]["type"] == "plan-this-slice"
    # plan filed -> implement-this-slice flip
    code2, out2 = _cli("compute-handoff",
                       {"register_markdown": md, "planned_slice_ids": ["S1"]})
    assert code2 == 0 and out2["handoff"]["type"] == "implement-this-slice"


def test_cli_cross_session_resume_walking_skeleton():
    # Session A arms a typed handoff on active-run.json; a FRESH session reads it
    # back and resolves the continue-the-run / hands-off resume — no shared
    # transcript, only the durable pointer crosses the boundary.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        hr = {"type": "continue-the-run", "dispatch": "hands-off",
              "slice_id": "S1"}
        codeA, outA = _cli_env("set-active-run",
                            {"owner_session_id": "OWNER", "surface_path": "/x.json",
                             "total_slices": 2, "pending_handoff": hr}, env)
        assert codeA == 0
        pointer = outA["pointer"]
        assert os.path.basename(pointer) == f"run-{outA['run_id']}.json"
        doc = json.loads(open(pointer, encoding="utf-8").read())
        assert doc["pending_handoff"]["slice_id"] == "S1"
        assert doc["owner_session_id"] == "OWNER"   # pointer fields preserved
        # fresh session resume read
        codeB, outB = _cli_env("resume", {}, env)
        assert codeB == 0
        assert outB["action"] == "resume" and outB["slice_id"] == "S1"
        assert outB["next"] == "dispatch-hands-off"


def test_cli_resume_no_pending():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        code, out = _cli_env("resume", {}, env)
        assert code == 0 and out["action"] == "none"


def test_cli_set_active_run_rejects_bad_pending_handoff():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        code, out = _cli_env("set-active-run",
                             {"owner_session_id": "OWNER", "surface_path": "/x",
                              "pending_handoff": {"type": "bogus"}}, env)
        assert code == 3 and "pending_handoff" in out["error"]
        # malformed record never persisted — no pointer of any shape
        assert not os.path.exists(os.path.join(d, "active-run.json"))
        assert not list(__import__("glob").glob(os.path.join(d, "run-*.json")))


def test_set_active_run_without_handoff_preserves_shape():
    # no pending_handoff key when none supplied (the resume-gate arm contract)
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        code, out = _cli_env("set-active-run",
                             {"owner_session_id": "OWNER", "surface_path": "/x",
                              "total_slices": 3}, env)
        assert code == 0
        doc = json.loads(open(out["pointer"], encoding="utf-8").read())
        assert "pending_handoff" not in doc


# --------------------------------------------------------------------------- #
# NS3 (v2) — cross-session resume routing. The pending_handoff on the run pointer
# still survives gate-clear so the SKILL can route on it (the gate is route-blind).
# The retired SessionStart-arm + PreToolUse-`.*` marker triad characterization
# tests were removed by execplan-resume-gate-fix (2026-07-10) — the checkpoint now
# fires only on the /execute-plan resume action; see the resume-gate hook smoke
# tests above (test_gate_hook_*) and the run.py-level tests (test_gate_*, below).
# --------------------------------------------------------------------------- #

def test_resume_gate_clear_preserves_run_pointer():
    # The clear hook records the ack (a per-session acked-<sid> file) and never
    # touches the run pointer — the pending_handoff survives so the SKILL routes.
    with tempfile.TemporaryDirectory() as d:
        out = _arm_run(d, handoff={"type": "continue-the-run",
                                   "dispatch": "hands-off", "slice_id": "S1"})
        pointer = out["pointer"]
        # gate a fresh session (writes the pending marker), then clear (acks)
        _run_local_hook("check-execplan-session-ack.sh", _skill_payload("FRESH"), d)
        code, _, _ = _run_local_hook(
            "clear-execplan-session-ack.sh",
            {"session_id": "FRESH", "tool_name": "AskUserQuestion"}, d)
        assert code == 0
        assert os.path.exists(pointer)                 # run pointer preserved
        assert run.read_pending_handoff(pointer_dir=d).slice_id == "S1"


# --- NS3: SKILL-side routing (the new behavior, post gate-clear) --- #

def _hr(t="continue-the-run", disp="hands-off", sid="S1", **kw):
    return run.HandoffRecord(type=t, dispatch=disp, slice_id=sid, **kw)


def test_ns3_route_dispatch_arms():
    assert run.render_resume(_hr(disp="hands-off"))["next"] == "dispatch-hands-off"
    assert run.render_resume(_hr(disp="attended"))["next"] == "dispatch-attended"
    assert run.render_resume(_hr(disp="out-of-session"))["next"] == "dispatch-out-of-session"
    # implement-this-slice is dispatchable too
    assert run.render_resume(
        _hr(t="implement-this-slice", disp="hands-off"))["next"] == "dispatch-hands-off"


def test_ns3_route_plan_this_slice_to_detour():
    out = run.render_resume(_hr(t="plan-this-slice", disp="attended",
                                plan_path="/Thoughts/S1_PLAN.md"))
    assert out["next"] == "plan-detour" and out["plan_path"] == "/Thoughts/S1_PLAN.md"
    # plan-detour wins even if the dispatch axis says hands-off (planning is gated)
    assert run.render_resume(_hr(t="plan-this-slice", disp="hands-off"))["next"] == "plan-detour"


def test_ns3_gate_exists_at_dispatch_promotes_handsoff():
    hr = _hr(disp="hands-off")
    # no gate at dispatch -> promoted to attended (never runs hands-off unverified)
    out = run.render_resume(hr, has_verification_gate=False)
    assert out["next"] == "dispatch-attended"
    assert out["dispatch_effective"] == "attended"
    assert "gate" in out["gate_warning"].lower()
    # gate present -> hands-off allowed
    assert run.render_resume(hr, has_verification_gate=True)["next"] == "dispatch-hands-off"
    # unasserted (None) -> declared dispatch honored (back-compat)
    assert run.render_resume(hr)["next"] == "dispatch-hands-off"


def test_ns3_gate_discipline_does_not_demote_attended():
    # an already-attended slice is never affected by the gate assertion
    hr = _hr(disp="attended")
    assert run.render_resume(hr, has_verification_gate=False)["next"] == "dispatch-attended"


def test_ns3_out_of_session_block_scaffold():
    block = run.render_out_of_session_block(_hr(disp="out-of-session"))
    assert block["slice_id"] == "S1"
    assert block["ordering"] == "migrate-before-register"
    assert block["run_outside_producer_process_tree"] is True
    assert block["paste_result_back"] is True


def test_ns3_cli_resume_routes_attended():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/x",
                  "pending_handoff": {"type": "continue-the-run",
                                      "dispatch": "attended", "slice_id": "S1"}}, env)
        code, out = _cli_env("resume", {}, env)
        assert code == 0 and out["next"] == "dispatch-attended"


def test_ns3_cli_resume_out_of_session_carries_block():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/x",
                  "pending_handoff": {"type": "continue-the-run",
                                      "dispatch": "out-of-session",
                                      "slice_id": "S1"}}, env)
        code, out = _cli_env("resume", {}, env)
        assert code == 0 and out["next"] == "dispatch-out-of-session"
        assert out["out_of_session_block"]["ordering"] == "migrate-before-register"


def test_ns3_cli_resume_gate_promotion():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/x",
                  "pending_handoff": {"type": "continue-the-run",
                                      "dispatch": "hands-off", "slice_id": "S1"}}, env)
        code, out = _cli_env("resume", {"has_verification_gate": False}, env)
        assert code == 0 and out["next"] == "dispatch-attended"


# --------------------------------------------------------------------------- #
# NS4 (v2) — plan-detour: one-plan-per-session + plan-filing gate + detour return
# --------------------------------------------------------------------------- #

def test_ns4_one_plan_per_session_flag_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, out = _cli_env("set-active-run",
                          {"owner_session_id": "OWNER", "surface_path": "/x"}, env)
        assert run.is_plan_session_exhausted(pointer_dir=d) is False
        run.mark_plan_session_exhausted(pointer_dir=d)
        assert run.is_plan_session_exhausted(pointer_dir=d) is True
        # the flag is additive — pointer fields survive
        doc = json.loads(open(out["pointer"], encoding="utf-8").read())
        assert doc["owner_session_id"] == "OWNER"
        assert doc["plan_session_exhausted"] is True


def test_ns4_render_resume_blocks_second_plan_mode():
    hr = _hr(t="plan-this-slice", disp="attended")
    # not exhausted -> plan-detour
    assert run.render_resume(hr)["next"] == "plan-detour"
    # exhausted -> cross-session-boundary (one plan per session)
    out = run.render_resume(hr, plan_session_exhausted=True)
    assert out["next"] == "cross-session-boundary"
    assert "fresh session" in out["reason"]
    # a non-plan handoff is unaffected by the flag
    assert run.render_resume(_hr(disp="hands-off"),
                             plan_session_exhausted=True)["next"] == "dispatch-hands-off"


def test_ns4_plan_file_exists():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x_PLAN.md")
        assert run.plan_file_exists(p) is False
        assert run.plan_file_exists(None) is False
        assert run.plan_file_exists("") is False
        with open(p, "w") as fh:
            fh.write("# plan")
        assert run.plan_file_exists(p) is True


def test_ns4_flip_on_plan_existence():
    with tempfile.TemporaryDirectory() as d:
        plan_path = os.path.join(d, "S1_PLAN.md")
        slices = [_ns1_slice("S1", type="plan")]
        bk = run.InMemoryBookkeepingAdapter()
        # no plan on disk -> plan-this-slice
        hr1 = run.compute_handoff(
            slices, bk,
            plan_exists=(lambda sid: run.plan_file_exists(plan_path)),
            plan_path_for=(lambda sid: plan_path))
        assert hr1.type == "plan-this-slice"
        # file the plan -> flip to implement-this-slice + plan_path threaded
        with open(plan_path, "w") as fh:
            fh.write("# plan")
        hr2 = run.compute_handoff(
            slices, bk,
            plan_exists=(lambda sid: run.plan_file_exists(plan_path)),
            plan_path_for=(lambda sid: plan_path))
        assert hr2.type == "implement-this-slice" and hr2.plan_path == plan_path


_NS4_SPINE = """\
# Solution Design

→ [[topic_DESIGN]]

# Implementation Details

- [[topic_PLAN]]
- [[topic_S1_PLAN]]

<!-- L:slice id=S1 status=NOW sessions=0/1 plan=[[topic_S1_PLAN]] diary=_ updated=2026-06-24 -->

## Next Session Prompt
"""


def test_ns4_verify_plan_filed_ok():
    with tempfile.TemporaryDirectory() as d:
        spine = os.path.join(d, "topic_THOUGHT.md")
        with open(spine, "w") as fh:
            fh.write(_NS4_SPINE)
        out = run.verify_plan_filed(spine, "S1", "topic_S1_PLAN")
        assert out["ok"] is True and out["plan"] == "[[topic_S1_PLAN]]"


def test_ns4_verify_plan_filed_missing_wikilink_raises():
    with tempfile.TemporaryDirectory() as d:
        spine = os.path.join(d, "topic_THOUGHT.md")
        # register row present but the # Implementation Details wikilink absent
        with open(spine, "w") as fh:
            fh.write("# Implementation Details\n\n(nothing)\n\n"
                     "<!-- L:slice id=S1 status=NOW sessions=0/1 "
                     "plan=[[topic_S1_PLAN]] diary=_ updated=2026-06-24 -->\n")
        try:
            run.verify_plan_filed(spine, "S1", "topic_S1_PLAN")
        except run.WriteVerificationError:
            return
        raise AssertionError("expected WriteVerificationError on missing wikilink")


def test_ns4_verify_plan_filed_missing_register_field_raises():
    with tempfile.TemporaryDirectory() as d:
        spine = os.path.join(d, "topic_THOUGHT.md")
        # wikilink present but the register row's plan= field still empty
        with open(spine, "w") as fh:
            fh.write("# Implementation Details\n\n- [[topic_S1_PLAN]]\n\n"
                     "<!-- L:slice id=S1 status=NOW sessions=0/1 "
                     "plan=_ diary=_ updated=2026-06-24 -->\n")
        try:
            run.verify_plan_filed(spine, "S1", "topic_S1_PLAN")
        except run.WriteVerificationError:
            return
        raise AssertionError("expected WriteVerificationError on empty plan= field")


def test_ns4_plan_detour_return_composes_not_bypasses():
    out = run.render_plan_detour_return("S1", "topic_S1_PLAN")
    assert out["composes_with"] == "post-plan-uxgate"
    assert out["post_plan_choice"] == "a"
    # the ordered return sequence
    assert out["steps"] == [
        "file-plan", "verify-plan-filed", "flip-to-implement-this-slice",
        "mark-plan-session-exhausted", "arm-fresh-session-handoff"]


def test_ns4_cli_second_plan_blocked():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/x",
                  "pending_handoff": {"type": "plan-this-slice",
                                      "dispatch": "attended", "slice_id": "S1"}}, env)
        code1, out1 = _cli_env("resume", {}, env)
        assert code1 == 0 and out1["next"] == "plan-detour"
        _cli_env("mark-plan-exhausted", {}, env)
        code2, out2 = _cli_env("resume", {}, env)
        assert code2 == 0 and out2["next"] == "cross-session-boundary"


def test_ns4_cli_verify_plan_filed():
    with tempfile.TemporaryDirectory() as d:
        spine = os.path.join(d, "topic_THOUGHT.md")
        with open(spine, "w") as fh:
            fh.write(_NS4_SPINE)
        code, out = _cli("verify-plan-filed",
                         {"spine_path": spine, "slice_id": "S1",
                          "plan_basename": "topic_S1_PLAN"})
        assert code == 0 and out["ok"] is True
        # a wrong basename fails the gate (exit 9)
        code2, out2 = _cli("verify-plan-filed",
                           {"spine_path": spine, "slice_id": "S1",
                            "plan_basename": "nonexistent_PLAN"})
        assert code2 == 9 and out2["status"] == "FAIL"


def test_ns4_cli_compute_handoff_flip_via_plan_paths():
    with tempfile.TemporaryDirectory() as d:
        plan_path = os.path.join(d, "S1_PLAN.md")
        md = (
            "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
            "|----|------|------|--------------|-------|-----------|\n"
            "| S1 | a | plan | x | more_capable | — |\n"
        )
        code, out = _cli("compute-handoff",
                         {"register_markdown": md,
                          "slice_plan_paths": {"S1": plan_path}})
        assert code == 0 and out["handoff"]["type"] == "plan-this-slice"
        with open(plan_path, "w") as fh:
            fh.write("# plan")
        code2, out2 = _cli("compute-handoff",
                           {"register_markdown": md,
                            "slice_plan_paths": {"S1": plan_path}})
        assert code2 == 0 and out2["handoff"]["type"] == "implement-this-slice"
        assert out2["handoff"]["plan_path"] == plan_path


# --------------------------------------------------------------------------- #
# NS5 (v2) — fresh code-face reconciliation at attended/out-of-session resume.
# --------------------------------------------------------------------------- #

def _todo_spec(path, payload, locator):
    return {"surface_kind": "todo_line", "path": path,
            "expected_payload": payload, "locator": locator}


def test_ns5_drift_register_completed_but_code_partial():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "out.md")
        with open(f, "w") as fh:
            fh.write("S1 work is only half done\n")   # no "DONE" marker
        claimed = {"S1": {"status": "completed"}}
        specs = {"S1": [_todo_spec(f, "DONE", r"^S1 .*")]}
        report = run.reconcile_code_face(claimed, specs)
        assert report["drift"] is True
        assert report["drifts"][0]["slice_id"] == "S1"
        assert report["drifts"][0]["kind"] == "code-partial"
        assert report["clean"] == []


def test_ns5_clean_when_code_matches_claim():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "out.md")
        with open(f, "w") as fh:
            fh.write("S1 DONE and committed\n")
        claimed = {"S1": {"status": "completed"}}
        specs = {"S1": [_todo_spec(f, "DONE", r"^S1 .*")]}
        report = run.reconcile_code_face(claimed, specs)
        assert report["drift"] is False and report["clean"] == ["S1"]


def test_ns5_conformance_layer_drift():
    # code layer clean but the model-layer verdict is non-PASS -> drift
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "out.md")
        with open(f, "w") as fh:
            fh.write("S1 DONE\n")
        claimed = {"S1": {"status": "completed"}}
        specs = {"S1": [_todo_spec(f, "DONE", r"^S1 .*")]}
        report = run.reconcile_code_face(
            claimed, specs,
            conformance=run.FakeConformanceAdapter(verdict="DIRTY"),
            verdicts_by_slice={"S1": "DIRTY"})
        assert report["drift"] is True
        assert report["drifts"][0]["kind"] == "conformance-fail"


def test_ns5_only_completed_claims_reconciled():
    # a started/committed slice is NOT reconciled (only completed claims)
    claimed = {"S1": {"status": "started"}, "S2": {"status": "committed"}}
    report = run.reconcile_code_face(claimed, {})
    assert report["checked"] == [] and report["drift"] is False


def test_ns5_hands_off_path_untouched():
    # the dispatch loop never reconciles — hands-off resume is excluded by design
    assert "hands-off" not in run.RECONCILE_DISPATCH_MODES
    assert set(run.RECONCILE_DISPATCH_MODES) == {"attended", "out-of-session"}
    # a normal loop still completes with no reconciliation in its path
    slices = run.parse_slice_register(REGISTER_MD)
    bk = run.InMemoryBookkeepingAdapter()
    out = run.run_dispatch_loop(slices, bk, run.FakeSpawnAdapter())
    assert out["summary"]["completed"] == len(slices)


def test_ns5_cli_reconcile_surfaces_drift():
    with tempfile.TemporaryDirectory() as d:
        f = os.path.join(d, "out.md")
        with open(f, "w") as fh:
            fh.write("S1 partial\n")
        code, out = _cli("reconcile-code-face",
                         {"slice_execution": {"S1": {"status": "completed"}},
                          "specs_by_slice": {"S1": [_todo_spec(f, "DONE", r"^S1 .*")]}})
        assert code == 0 and out["drift"] is True
        assert out["drifts"][0]["kind"] == "code-partial"


# --------------------------------------------------------------------------- #
# NS6 (v2) — P<N>: detour-commit support. CHARACTERIZATION FIRST: pin the two
# touch points + the register-parser invariant before any edit.
# --------------------------------------------------------------------------- #

def test_ns6_char_commit_exists_greps_prefix_generic():
    # CHARACTERIZATION (permanent invariant): commit_exists interpolates the id
    # into `^<id>:` — already id-agnostic, so it admits a P<N>: subject unchanged.
    calls = []
    def runner(args):
        calls.append(list(args))
        return _GitRun(stdout="")
    guard = run.GitCommitGuard(repo_dir="/repo", runner=runner)
    guard.commit_exists("P1")
    log = calls[0]
    assert "--grep" in log and log[log.index("--grep") + 1] == "^P1:"


def test_ns6_char_register_parser_stays_S_only():
    # CHARACTERIZATION (permanent invariant): the register parser's slice-id regex
    # is ^S\d+$ — a P-row is NEVER parsed as a slice (design #11: register parser
    # slice-commit regex unchanged).
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | a | implementation | x | routine | — |\n"
        "| P1 | plan-commit | implementation | y | routine | — |\n"
    )
    ids = [s.id for s in run.parse_slice_register(md)]
    assert ids == ["S1"]   # P1 ignored


def test_ns6_format_subject_admits_S():
    # CHARACTERIZATION (permanent): S-subjects always format.
    assert run.UnifiedGitContract.format_commit_subject("S1", "do it") == "S1: do it"


# --- NS6: P<N>: now admitted by the contract; detour commit is path-scoped --- #

def test_ns6_is_valid_commit_id():
    C = run.UnifiedGitContract
    for ok in ("S1", "S42", "P1", "P9"):
        assert C.is_valid_commit_id(ok) is True
    for bad in ("X1", "p1", "s1", "P", "S", "1", "PS1", "", None):
        assert C.is_valid_commit_id(bad) is False


def test_ns6_format_subject_admits_P_keeps_rejecting_others():
    C = run.UnifiedGitContract
    assert C.format_commit_subject("P1", "file plan") == "P1: file plan"
    assert C.format_commit_subject("P12", "x") == "P12: x"
    for bad in ("X1", "1", "Sx"):
        try:
            C.format_commit_subject(bad, "x")
        except run.GitContractError:
            continue
        raise AssertionError(f"expected GitContractError for id={bad!r}")
    # empty summary still rejected
    try:
        C.format_commit_subject("P1", "   ")
    except run.GitContractError:
        return
    raise AssertionError("empty summary must still raise")


def test_ns6_guard_finds_P_commit():
    # the idempotency guard finds an existing P<N>: detour commit (generic grep)
    world = _FakeGitWorld(committed_subjects=["P1: file plan for S3"])
    guard = run.GitCommitGuard(repo_dir="/repo", runner=world)
    assert guard.commit_exists("P1") is True
    assert guard.commit_exists("P2") is False


def test_ns6_detour_commit_is_path_scoped():
    world = _FakeGitWorld(branch="feature/x")
    adapter = run.GitCommitAdapter(repo_dir="/repo", runner=world)
    receipt = adapter.create_detour_commit(
        "P1", paths=["Thoughts/topic_S3_PLAN.md"], summary="file plan for S3")
    assert receipt["commit_id"] == "P1"
    assert receipt["subject"] == "P1: file plan for S3"
    assert receipt["paths"] == ["Thoughts/topic_S3_PLAN.md"]
    # the staging call is path-scoped: `add -- <path>`, NEVER `add -A`
    add_calls = [c for c in world.calls if _git_sub(c)[:1] == ["add"]]
    assert add_calls, "expected a git add call"
    spec = ":(top,literal)Thoughts/topic_S3_PLAN.md"
    assert _git_sub(add_calls[0]) == ["add", "-A", "--", spec]
    # and the COMMIT is scoped too — the defect this path used to carry was a
    # scoped add followed by a bare commit.
    commit_calls = [_git_sub(c) for c in world.calls if _git_sub(c)[:1] == ["commit"]]
    assert commit_calls == [["commit", "-m", "P1: file plan for S3", "--", spec]]


def test_ns6_detour_commit_requires_paths():
    adapter = run.GitCommitAdapter(repo_dir="/repo", runner=_FakeGitWorld())
    try:
        adapter.create_detour_commit("P1", paths=[], summary="x")
    except run.GitContractError:
        return
    raise AssertionError("path-scoped detour commit must require ≥1 path")


def test_ns6_detour_commit_protected_branch_refused():
    world = _FakeGitWorld(branch="main")
    adapter = run.GitCommitAdapter(repo_dir="/repo", runner=world)
    try:
        adapter.create_detour_commit("P1", paths=["x"], summary="x")
    except run.GitContractError:
        return
    raise AssertionError("detour commit onto a protected branch must be refused")


def test_ns6_cli_commit_detour_real_repo_path_scoped():
    with tempfile.TemporaryDirectory() as d:
        _init_temp_git_repo(d, branch="feature/ns6-smoke")
        # two new files; the detour commit must stage ONLY a.txt
        for name in ("a.txt", "b.txt"):
            with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
                fh.write(name)
        code, out = _cli("commit-detour",
                         {"commit_id": "P1", "paths": ["a.txt"],
                          "commit_summary": "file plan", "repo_dir": d})
        assert code == 0 and out["subject"] == "P1: file plan"
        # the P1 commit is on the branch
        log = subprocess.run(["git", "-C", d, "log", "--format=%s"],
                             capture_output=True, text=True).stdout
        assert "P1: file plan" in log
        # b.txt was NOT swept in (path-scoping) — still untracked/uncommitted
        status = subprocess.run(["git", "-C", d, "status", "--porcelain"],
                                capture_output=True, text=True).stdout
        assert "b.txt" in status


# --------------------------------------------------------------------------- #
# glittery-humming-pine S5 / A7 — every execute-plan commit publishes ONLY its
# declared paths. Validation gate: a two-session fixture with a FOREIGN file
# already STAGED (not merely untracked — an untracked file was never at risk from
# a bare commit, which is why the NS6 test above could not see the defect).
# --------------------------------------------------------------------------- #


def _two_session_repo(d):
    """Temp repo where 'session A' (the walker) has written mine.txt and a
    concurrent 'session B' has written AND STAGED foreign.txt."""
    g = _init_temp_git_repo(d, branch="feature/a7")
    for name in ("mine.txt", "foreign.txt"):
        with open(os.path.join(d, name), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
    g("add", "--", "foreign.txt")
    assert "foreign.txt" in g("diff", "--cached", "--name-only").stdout  # precondition
    # B's second file: written, NOT staged. A whole-tree add would stage it even
    # if the commit stayed scoped, leaving it for the next bare commit anywhere.
    with open(os.path.join(d, "bystander.txt"), "w", encoding="utf-8") as fh:
        fh.write("B, unstaged\n")
    return g


def _committed_names(g):
    return g("show", "--name-only", "--format=", "HEAD").stdout.split()


def _assert_foreign_untouched(g):
    """Positive evidence the foreign file was left exactly as B left it: absent
    from the new commit AND still staged. Checked against a commit that DID land
    (callers assert that), so this cannot pass merely because nothing ran."""
    assert "foreign.txt" not in _committed_names(g)
    assert "foreign.txt" in g("diff", "--cached", "--name-only").stdout
    assert "bystander.txt" not in _committed_names(g)
    assert "?? bystander.txt" in g("status", "--porcelain").stdout.splitlines()


def test_a7_slice_commit_excludes_staged_foreign_file():
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        receipt = run.GitCommitAdapter(repo_dir=d).create_commit(
            "S5", result={"commit_summary": "mine"}, paths=["mine.txt"])
        assert receipt["status"] == "committed"
        assert g("log", "-1", "--format=%s").stdout.strip() == "S5: mine"
        assert _committed_names(g) == ["mine.txt"]
        _assert_foreign_untouched(g)


def test_a7_detour_commit_excludes_staged_foreign_file():
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        code, out = _cli("commit-detour", {"commit_id": "P2", "paths": ["mine.txt"],
                                           "commit_summary": "plan", "repo_dir": d})
        assert code == 0, out
        assert g("log", "-1", "--format=%s").stdout.strip() == "P2: plan"
        assert _committed_names(g) == ["mine.txt"]
        _assert_foreign_untouched(g)


def test_a7_cli_commit_slice_reads_register_write_targets():
    reg = ("#### Slices\n"
           "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Write targets |\n"
           "|----|------|------|--------------|-------|-----------|---------|---------------|\n"
           "| S5 | x | implementation | y | routine | — | - | `mine.txt` |\n")
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        code, out = _cli("commit-slice", {"slice_id": "S5", "repo_dir": d,
                                          "commit_summary": "from register",
                                          "register_markdown": reg})
        assert code == 0, out
        assert out["paths"] == ["mine.txt"]
        assert _committed_names(g) == ["mine.txt"]
        _assert_foreign_untouched(g)


def test_a7_loop_commit_uses_slice_write_targets():
    reg = ("#### Slices\n"
           "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Write targets |\n"
           "|----|------|------|--------------|-------|-----------|---------|---------------|\n"
           "| S1 | x | implementation | y | routine | — | - | `mine.txt` |\n")
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        run.run_dispatch_loop(
            run.parse_slice_register(reg), run.InMemoryBookkeepingAdapter(),
            run.FakeSpawnAdapter(),
            commit=run.GitCommitAdapter(repo_dir=d),
            commit_guard=run.GitCommitGuard(repo_dir=d))
        assert g("log", "-1", "--format=%s").stdout.strip().startswith("S1:")
        assert _committed_names(g) == ["mine.txt"]
        _assert_foreign_untouched(g)


def test_a7_undeclared_slice_refuses_and_touches_nothing():
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        head = g("rev-parse", "HEAD").stdout
        for paths in ((), ["", "—"]):
            try:
                run.GitCommitAdapter(repo_dir=d).create_commit(
                    "S5", result={}, paths=paths)
            except run.GitContractError as e:
                assert "declares no paths" in str(e)
            else:
                raise AssertionError(f"undeclared commit must refuse: {paths!r}")
        assert g("rev-parse", "HEAD").stdout == head
        assert g("diff", "--cached", "--name-only").stdout.split() == ["foreign.txt"]


def test_a7_cli_commit_slice_without_any_declaration_exit_3():
    code, out = _cli("commit-slice", {"slice_id": "S1", "commit_summary": "x"})
    assert code == 3 and "paths" in out["error"]


def test_a7_declared_directory_refused():
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "sub", "other.txt"), "w", encoding="utf-8") as fh:
            fh.write("x\n")
        head = g("rev-parse", "HEAD").stdout
        try:
            run.GitCommitAdapter(repo_dir=d).create_commit(
                "S5", result={}, paths=["mine.txt", "sub"])
        except run.GitContractError as e:
            assert "directory" in str(e)
        else:
            raise AssertionError("a declared directory must be refused")
        assert g("rev-parse", "HEAD").stdout == head


def test_a7_literal_pathspec_commits_bracket_name_and_glob_matches_nothing():
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        odd = "[care] notes.txt"
        with open(os.path.join(d, odd), "w", encoding="utf-8") as fh:
            fh.write("odd\n")
        run.GitCommitAdapter(repo_dir=d).create_commit(
            "S5", result={"commit_summary": "odd"}, paths=[odd])
        assert _committed_names(g) == ["[care]", "notes.txt"]   # split() of one name
        _assert_foreign_untouched(g)
        head = g("rev-parse", "HEAD").stdout
        try:
            run.GitCommitAdapter(repo_dir=d).create_commit(
                "S6", result={}, paths=["*.txt"])
        except run.GitContractError:
            pass
        else:
            raise AssertionError("a glob must be inert, not a sweep")
        assert g("rev-parse", "HEAD").stdout == head
        assert "mine.txt" not in _committed_names(g)


def test_a7_commit_from_subdirectory_is_root_relative():
    with tempfile.TemporaryDirectory() as d:
        g = _two_session_repo(d)
        os.makedirs(os.path.join(d, "deep"))
        run.GitCommitAdapter(repo_dir=os.path.join(d, "deep")).create_commit(
            "S5", result={"commit_summary": "root"}, paths=["mine.txt"])
        assert _committed_names(g) == ["mine.txt"]
        _assert_foreign_untouched(g)


def test_a8_slice_commit_through_the_enforcing_gate():
    """S6 rehearsal: an execute-plan slice commit lands THROUGH the real
    enforcing scope gate + waiver trailer, installed from the config under test
    into a repo whose topic worktree holds a concurrent session's staged file.
    Armed-ness is proven first: a bare commit in that worktree is refused."""
    hooks = os.path.abspath(os.path.join(HERE, "..", "..", "hooks"))
    with tempfile.TemporaryDirectory() as d:
        base = os.path.join(d, "base")
        g = _init_temp_git_repo(base, branch="main")
        subprocess.run(["bash", os.path.join(hooks, "worktree-helper.sh"),
                        "install", "--repo", base], capture_output=True, text=True)
        assert os.path.lexists(os.path.join(base, ".git", "hooks", "pre-commit"))
        assert os.path.lexists(os.path.join(base, ".git", "hooks", "commit-msg"))
        wt = os.path.join(d, "wt")
        g("worktree", "add", "-q", "-b", "feature/a8", wt)
        gw = _two_session_repo_at(wt)
        head = gw("rev-parse", "HEAD").stdout
        probe = gw("commit", "-qm", "bare probe")
        assert gw("rev-parse", "HEAD").stdout == head and "scope BLOCKED" in probe.stderr, \
            f"gate not armed in the rehearsal worktree: {probe.stderr!r}"
        receipt = run.GitCommitAdapter(repo_dir=wt).create_commit(
            "S5", result={"commit_summary": "under enforcement"}, paths=["mine.txt"])
        assert receipt["status"] == "committed"
        assert gw("log", "-1", "--format=%s").stdout.strip() == "S5: under enforcement"
        assert _committed_names(gw) == ["mine.txt"]
        _assert_foreign_untouched(gw)
        assert "Unscoped-Publish:" not in gw("log", "-1", "--format=%B").stdout


def _two_session_repo_at(wt):
    """`_two_session_repo`'s two-session state, inside an EXISTING worktree."""
    def g(*a):
        return subprocess.run(["git", "-C", wt, *a], capture_output=True, text=True)
    for name in ("mine.txt", "foreign.txt"):
        with open(os.path.join(wt, name), "w", encoding="utf-8") as fh:
            fh.write(name + "\n")
    g("add", "--", "foreign.txt")
    with open(os.path.join(wt, "bystander.txt"), "w", encoding="utf-8") as fh:
        fh.write("B, unstaged\n")
    return g


def test_a7_co_writers_announced_for_shared_append_path_only():
    """C9 — a slice commit touching a merge_union path (TODO.md) consults
    co_writers BEFORE committing and carries the result; a slice touching only
    session-owned files does not consult it. commit_scope is faked through
    sys.modules (the adapter imports it lazily); bookkeeping_paths is real."""
    import types
    calls = []

    def fake_co_writers(paths, session_id=None, cwd=None, **_):
        head = subprocess.run(["git", "-C", str(cwd), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        calls.append({"paths": list(paths), "head": head})
        return {"TODO.md": ["other-session"]}

    fake = types.ModuleType("commit_scope")
    fake.co_writers = fake_co_writers
    saved = sys.modules.get("commit_scope")
    sys.modules["commit_scope"] = fake
    try:
        with tempfile.TemporaryDirectory() as d:
            g = _two_session_repo(d)
            with open(os.path.join(d, "TODO.md"), "w", encoding="utf-8") as fh:
                fh.write("- [ ] a\n- [ ] b\n")
            head_before = g("rev-parse", "HEAD").stdout.strip()
            receipt = run.GitCommitAdapter(repo_dir=d).create_commit(
                "S5", result={"commit_summary": "todo"},
                paths=["mine.txt", "TODO.md"])
            assert calls == [{"paths": ["TODO.md"], "head": head_before}]  # before commit
            assert receipt["co_writers"] == {"TODO.md": ["other-session"]}
            assert sorted(_committed_names(g)) == ["TODO.md", "mine.txt"]
            _assert_foreign_untouched(g)

            calls.clear()
            with open(os.path.join(d, "mine.txt"), "a", encoding="utf-8") as fh:
                fh.write("more\n")
            receipt = run.GitCommitAdapter(repo_dir=d).create_commit(
                "S6", result={"commit_summary": "own"}, paths=["mine.txt"])
            assert receipt["status"] == "committed"
            assert calls == [] and receipt["co_writers"] == {}
    finally:
        if saved is not None:
            sys.modules["commit_scope"] = saved
        else:
            sys.modules.pop("commit_scope", None)


# --------------------------------------------------------------------------- #
# NS7 (v2) — resume prose: type-change recompose trigger + deferred-capture
# enumeration/completeness + re-seed context bundle.
# --------------------------------------------------------------------------- #

def test_ns7_recompose_forced_on_type_change():
    # the headline correction (b1): a same-slice plan-this-slice ->
    # implement-this-slice flip leaves the pfh fingerprint unchanged, so the type
    # change MUST force a recompose even when pfh reports fresh.
    rec, reason = run.should_recompose_handoff_prompt(
        "implement-this-slice", "plan-this-slice", pfh_stale=False)
    assert rec is True and "type changed" in reason
    # same type + pfh fresh -> no recompose
    rec2, _ = run.should_recompose_handoff_prompt(
        "continue-the-run", "continue-the-run", pfh_stale=False)
    assert rec2 is False
    # same type but pfh stale -> recompose
    rec3, _ = run.should_recompose_handoff_prompt(
        "continue-the-run", "continue-the-run", pfh_stale=True)
    assert rec3 is True
    # no prior prompt -> recompose
    rec4, _ = run.should_recompose_handoff_prompt("continue-the-run", None)
    assert rec4 is True


def test_ns7_composed_type_roundtrip_and_cli_recompose():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/x"}, env)
        # first compose for a plan-this-slice handoff
        run.record_composed_handoff_type("plan-this-slice", pointer_dir=d)
        assert run.read_composed_handoff_type(pointer_dir=d) == "plan-this-slice"
        # CLI recompose-check sees the flip to implement-this-slice -> recompose
        code, out = _cli_env("recompose-check",
                             {"current_type": "implement-this-slice"}, env)
        assert code == 0 and out["recompose"] is True
        assert out["prev_type"] == "plan-this-slice"
        # record the new type, then same-type check is fresh
        _cli_env("record-composed-type",
                 {"composed_handoff_type": "implement-this-slice"}, env)
        code2, out2 = _cli_env("recompose-check",
                               {"current_type": "implement-this-slice"}, env)
        assert code2 == 0 and out2["recompose"] is False


def test_ns7_enumerate_deferred_captures_with_origin():
    dispatched = [
        {"slice_id": "S1", "result": {"deferred": ["fix the flaky test",
                                                   {"title": "rename helper"}]}},
        {"slice_id": "S2", "result": {"deferred": ["document the gate"]}},
        {"slice_id": "S3", "result": {}},   # no deferrals
    ]
    out = run.enumerate_deferred_captures(dispatched)
    assert [c["origin_slice_id"] for c in out] == ["S1", "S1", "S2"]
    assert out[0]["id"] == "S1-def1" and out[0]["capture"] == "fix the flaky test"
    assert out[1]["capture"] == "rename helper"
    assert out[2]["id"] == "S2-def1"


def test_ns7_deferred_completeness():
    enumerated = [{"id": "S1-def1", "origin_slice_id": "S1", "capture": "A"},
                  {"id": "S2-def1", "origin_slice_id": "S2", "capture": "B"}]
    ok = run.check_deferred_completeness(enumerated, ["A", "B"])
    assert ok["complete"] is True and ok["missing"] == []
    bad = run.check_deferred_completeness(enumerated, ["A", "B", "C"])
    assert bad["complete"] is False and bad["missing"] == ["C"]


def test_ns7_resume_context_bundle():
    hr = _hr(t="implement-this-slice", disp="hands-off", plan_path="/x_PLAN.md")
    ctx = run.render_resume_context(
        hr, session_summary={"completed": ["S1"]},
        deferred_captures=[{"id": "S1-def1", "capture": "A"}])
    assert ctx["handoff"]["type"] == "implement-this-slice"
    assert ctx["session_summary"] == {"completed": ["S1"]}
    assert ctx["deferred_captures"] == [{"id": "S1-def1", "capture": "A"}]


def test_ns7_cli_deferred_captures_completeness():
    code, out = _cli("deferred-captures",
                     {"dispatched": [{"slice_id": "S1",
                                      "result": {"deferred": ["A"]}}],
                      "actual_deferred": ["A", "B"]})
    assert code == 0 and out["count"] == 1
    assert out["completeness"]["complete"] is False
    assert out["completeness"]["missing"] == ["B"]


def test_ns7_cli_resume_context_reads_pending_handoff():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": "/x",
                  "pending_handoff": {"type": "continue-the-run",
                                      "dispatch": "hands-off", "slice_id": "S2"}}, env)
        code, out = _cli_env("resume-context",
                             {"deferred_captures": [{"id": "S1-def1",
                                                     "capture": "A"}]}, env)
        assert code == 0 and out["handoff"]["slice_id"] == "S2"
        assert out["deferred_captures"] == [{"id": "S1-def1", "capture": "A"}]


# --------------------------------------------------------------------------- #
# NS8 (v2) — per-slice timing + OMTM, captured-not-gated.
# --------------------------------------------------------------------------- #

def test_ns8_iso_minutes():
    assert run._iso_minutes("2026-06-24T10:00:00Z", "2026-06-24T10:30:00Z") == 30.0
    assert run._iso_minutes(None, "2026-06-24T10:30:00Z") is None
    assert run._iso_minutes("2026-06-24T10:30:00Z", "2026-06-24T10:00:00Z") is None  # negative
    assert run._iso_minutes("garbage", "also garbage") is None


def test_ns8_render_diagnostics_captures_timing_not_gated():
    sx = {
        "S1": {"status": "completed", "started_at": "2026-06-24T10:00:00Z",
               "completed_at": "2026-06-24T10:20:00Z"},
        "S2": {"status": "completed", "attended": True,
               "started_at": "2026-06-24T11:00:00Z",
               "completed_at": "2026-06-24T11:40:00Z"},
    }
    out = run.render_diagnostics(sx)
    assert out["gated"] is False                       # contract unchanged
    assert out["timing"]["timed_slices"] == 2
    assert out["timing"]["total_minutes"] == 60.0      # 20 + 40
    assert out["timing"]["attended_slices"] == 1
    assert out["captured"]["timing_slices"] == 2
    # existing keys still present (no regression)
    assert "model_abort_rate" in out and "tokens_per_family" in out


def test_ns8_omtm_excludes_attended_and_plan_needing():
    sx = {
        # plain hands-off implementation slice (counts)
        "S1": {"status": "completed", "type": "implementation",
               "started_at": "2026-06-24T10:00:00Z",
               "completed_at": "2026-06-24T10:10:00Z"},
        # attended slice (excluded)
        "S2": {"status": "completed", "type": "implementation", "attended": True,
               "started_at": "2026-06-24T11:00:00Z",
               "completed_at": "2026-06-24T11:50:00Z"},
        # plan-needing detour slice (excluded)
        "S3": {"status": "completed", "type": "plan",
               "started_at": "2026-06-24T12:00:00Z",
               "completed_at": "2026-06-24T12:30:00Z"},
        # plain slice with no timing (excluded from numerator, counted missing)
        "S4": {"status": "completed", "type": "implementation"},
    }
    om = run.compute_omtm(sx)
    assert om["plain_impl_slices"] == 1
    assert om["omtm_minutes_per_slice"] == 10.0
    assert om["excluded"] == {"attended": 1, "plan_needing": 1, "missing_timing": 1}
    assert om["gated"] is False and om["ready"] is True


def test_ns8_omtm_not_ready_without_timing():
    om = run.compute_omtm({"S1": {"status": "completed", "type": "implementation"}})
    assert om["omtm_minutes_per_slice"] is None and om["ready"] is False


def test_ns8_cli_omtm_and_diagnostics():
    sx = {"S1": {"status": "completed", "type": "implementation",
                 "started_at": "2026-06-24T10:00:00Z",
                 "completed_at": "2026-06-24T10:15:00Z"}}
    code, out = _cli("omtm", {"slice_execution": sx})
    assert code == 0 and out["omtm_minutes_per_slice"] == 15.0
    code2, out2 = _cli("diagnostics", {"slice_execution": sx})
    assert code2 == 0 and out2["timing"]["total_minutes"] == 15.0
    assert out2["gated"] is False


# Bounded durable-state keys — what may legitimately cross a session boundary.
# A finished agent's transcript must NEVER be persisted here (C5 boundedness).
_DURABLE_ENTRY_KEYS = {"status", "model_family", "attempts", "result",
                       "type", "slice_type", "attended",
                       "started_at", "completed_at"}


def test_ns8_loop_captures_timing_via_port_and_bounded_state():
    # closes the review's finding #1: a REAL dispatch loop captures timing/type/
    # attended THROUGH the BookkeepingPort (not hand-injected), so compute_omtm
    # reads loop-produced state. Also the genuine, NON-circular C5 boundedness
    # proof: the persisted entry carries only bounded keys, never a transcript.
    slices = [run.Slice(id="S1", name="x", type="implementation",
                        agent_choice="routine", dispatch="hands-off"),
              run.Slice(id="S2", name="y", type="implementation_verification",
                        agent_choice="more_capable", dispatch="hands-off")]
    bk = run.InMemoryBookkeepingAdapter()
    run.run_dispatch_loop(slices, bk, run.FakeSpawnAdapter())
    entry = bk.get("S1")
    assert "started_at" in entry and "completed_at" in entry   # captured by the loop
    assert entry["slice_type"] == "implementation"
    assert entry["attended"] is False                          # hands-off
    # C5 — the loop-produced entry holds ONLY bounded keys (no transcript blob)
    for sid in ("S1", "S2"):
        assert set(bk.get(sid)) <= _DURABLE_ENTRY_KEYS, set(bk.get(sid))
    # OMTM now reads LOOP-PRODUCED timing, not hand-injected test fields
    om = run.compute_omtm(bk.all())
    assert om["plain_impl_slices"] == 2 and om["ready"] is True
    assert om["excluded"]["missing_timing"] == 0


def test_ns8_minimal_adapter_capture_timing():
    # the capture path works on a REAL (file-backed) adapter, not just InMemory
    with tempfile.TemporaryDirectory() as d:
        bk = run.MinimalBookkeepingAdapter(os.path.join(d, "run-state.json"))
        bk.record_attempt("S1", model_family="sonnet")
        bk.capture_timing("S1", started_at="2026-06-24T10:00:00Z",
                          completed_at="2026-06-24T10:05:00Z",
                          attended=False, slice_type="implementation")
        bk.mark_completed("S1", result={})
        entry = bk.get("S1")
        assert entry["started_at"] == "2026-06-24T10:00:00Z"
        assert entry["slice_type"] == "implementation"
        # started_at is set ONCE (first wins) across re-captures
        bk.capture_timing("S1", started_at="2026-06-24T09:00:00Z")
        assert bk.get("S1")["started_at"] == "2026-06-24T10:00:00Z"


# --------------------------------------------------------------------------- #
# NS9 (v2) — exception-intervention surface + engagement-rate secondary metric.
# --------------------------------------------------------------------------- #

def test_ns9_exception_surface_structured_block():
    surface = run.render_exception_surface(
        "conformance-fail", "S3",
        last_committed="S2", detail="verdict=DIRTY",
        dispatched=[{"slice_id": "S1"}, {"slice_id": "S2"}],
        default_option="Skip and continue",
        options=["Retry", "Skip and continue", "Abort run"])
    assert surface["exception_type"] == "conformance-fail"
    assert surface["slice_id"] == "S3"
    assert surface["last_committed"] == "S2"
    assert surface["completed_so_far"] == ["S1", "S2"]
    # the prominent default is listed FIRST
    assert surface["default_option"] == "Skip and continue"
    assert surface["options"][0] == "Skip and continue"
    assert set(surface["options"]) == {"Retry", "Skip and continue", "Abort run"}


def test_ns9_exception_surface_default_fallback():
    # no default given -> first option is the prominent default
    surface = run.render_exception_surface("escalation", "S1")
    assert surface["default_option"] == surface["options"][0]


def test_ns9_record_choice_engagement():
    # non-default choice -> engaged
    r1 = run.record_exception_choice("Abort run", "Retry")
    assert r1["non_default"] is True and r1["engaged"] is True
    # default choice -> not engaged (unless explicit)
    r2 = run.record_exception_choice("Retry", "Retry")
    assert r2["non_default"] is False and r2["engaged"] is False
    # explicit engagement overrides
    r3 = run.record_exception_choice("Retry", "Retry", engaged=True)
    assert r3["engaged"] is True


def test_ns9_engagement_rate():
    records = [
        run.record_exception_choice("Abort run", "Retry"),     # engaged
        run.record_exception_choice("Retry", "Retry"),         # not
        run.record_exception_choice("Retry", "Retry", engaged=True),  # engaged
    ]
    out = run.exception_engagement_rate(records)
    assert out["total"] == 3 and out["engaged"] == 2
    assert abs(out["engagement_rate"] - 2 / 3) < 1e-9
    assert out["gated"] is False
    # empty -> 0, no div-by-zero
    assert run.exception_engagement_rate([])["engagement_rate"] == 0


def test_ns9_cli_exception_surface_and_engagement():
    code, out = _cli("exception-surface",
                     {"exception_type": "drift", "slice_id": "S4",
                      "default_option": "Abort run"})
    assert code == 0 and out["exception_type"] == "drift"
    assert out["options"][0] == "Abort run"
    code2, out2 = _cli("engagement-rate",
                       {"choices": [{"chosen": "Abort run", "default_option": "Retry"},
                                    {"chosen": "Retry", "default_option": "Retry"}]})
    assert code2 == 0 and out2["engaged"] == 1 and out2["total"] == 2


# --------------------------------------------------------------------------- #
# NS10 (v2, PROVISIONAL) — shared-write-target serialization.
# --------------------------------------------------------------------------- #

def test_ns10_write_targets_field_additive_and_parsed():
    s = run.Slice(id="S1", name="x", type="implementation",
                  agent_choice="routine", write_targets=("a.py", "b.py"))
    assert s.write_targets == ("a.py", "b.py")
    # default empty
    assert run.Slice(id="S2", name="y", type="implementation",
                     agent_choice="routine").write_targets == ()
    # parsed from the optional 9th column
    md = (
        "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Dispatch | Write targets |\n"
        "|----|------|------|--------------|-------|-----------|---------|----------|---------------|\n"
        "| S1 | a | implementation | x | routine | — | - | hands-off | run.py, test.py |\n"
        "| S2 | b | implementation | y | routine | — | - | — | — |\n"
    )
    by = {s.id: s for s in run.parse_slice_register(md)}
    assert by["S1"].write_targets == ("run.py", "test.py")
    assert by["S2"].write_targets == ()


def test_ns10_empty_default_preserves_dag_behavior():
    # REGISTER_MD has no write_targets -> next_ready_slice unchanged
    slices = run.parse_slice_register(REGISTER_MD)
    assert all(s.write_targets == () for s in slices)
    bk = run.InMemoryBookkeepingAdapter()
    # S1 and S2 are both roots; register order returns S1 first (original behavior)
    assert run.next_ready_slice(slices, bk).id == "S1"


def test_ns10_shared_target_serializes_later_slice():
    # S1 (shares target "f", blocked by dep S3), S2 (shares "f", ready by deps),
    # S3 (no target, ready). The guard defers S2 behind the earlier incomplete S1.
    S = run.Slice
    slices = [
        S(id="S1", name="a", type="implementation", agent_choice="routine",
          depends_on=("S3",), write_targets=("f",)),
        S(id="S2", name="b", type="implementation", agent_choice="routine",
          write_targets=("f",)),
        S(id="S3", name="c", type="implementation", agent_choice="routine"),
    ]
    bk = run.InMemoryBookkeepingAdapter()
    # round 1: S1 blocked (dep), S2 serialized behind S1 -> S3 runs
    assert run.next_ready_slice(slices, bk).id == "S3"
    bk.mark_completed("S3", result={})
    # round 2: S1 now ready
    assert run.next_ready_slice(slices, bk).id == "S1"
    bk.mark_completed("S1", result={})
    # round 3: S2 releases now that the earlier target-sharer completed
    assert run.next_ready_slice(slices, bk).id == "S2"


def test_ns10_non_shared_targets_run_in_register_order():
    S = run.Slice
    slices = [
        S(id="S1", name="a", type="implementation", agent_choice="routine",
          write_targets=("f1",)),
        S(id="S2", name="b", type="implementation", agent_choice="routine",
          write_targets=("f2",)),
    ]
    bk = run.InMemoryBookkeepingAdapter()
    assert run.next_ready_slice(slices, bk).id == "S1"   # distinct targets, no block


def test_ns10_dispatch_loop_completes_with_shared_targets():
    S = run.Slice
    slices = [
        S(id="S1", name="a", type="implementation", agent_choice="routine",
          write_targets=("f",)),
        S(id="S2", name="b", type="implementation", agent_choice="routine",
          write_targets=("f",)),
    ]
    bk = run.InMemoryBookkeepingAdapter()
    out = run.run_dispatch_loop(slices, bk, run.FakeSpawnAdapter())
    # both complete, serialized in register order
    assert out["summary"]["order"] == ["S1", "S2"]
    assert out["summary"]["completed"] == 2


# --------------------------------------------------------------------------- #
# NS11 (v2) — closing END-TO-END implementation verification: a real multi-slice
# cross-session run asserted against the locked Desired Outcome observables
# C1–C7. No new mechanism — this slice only assembles the prior ten and proves
# the whole carries the outcome. The cross-session boundary is REAL: every resume
# read runs in a fresh `run.py` subprocess (no shared in-memory state), seeded
# ONLY from the durable active-run.json pointer + the persisted slice_execution.
# --------------------------------------------------------------------------- #

# A mixed register: a plain hands-off slice (C4), a plan-needing detour (C3), a
# hands-off-declared-but-ungated slice (C6 attended promotion), and an
# out-of-session verifier-isolation slice (C6 out-of-session).
_NS11_REGISTER = (
    "| ID | Name | Type | Slicing idea | Model | Depends on | Confirm | Dispatch |\n"
    "|----|------|------|--------------|-------|-----------|---------|----------|\n"
    "| S1 | plain build | implementation | x | routine | — | - | hands-off |\n"
    "| S2 | big slice | plan | y | more_capable | S1 | - | attended |\n"
    "| S3 | skill edit | implementation | z | routine | S2 | - | hands-off |\n"
    "| S4 | live gate smoke | implementation | w | more_capable | S3 | - | out-of-session |\n"
)


def test_ns11_e2e_cross_session_run_all_claims():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        statef = os.path.join(d, "run-state.json")     # the durable slice_execution
        planf = os.path.join(d, "S2_PLAN.md")          # S2's _PLAN (not yet filed)
        _write_json(statef, {})

        def execmap():
            return json.loads(open(statef, encoding="utf-8").read())

        def complete(sid, **fields):
            m = execmap(); m[sid] = {"status": "completed", **fields}
            _write_json(statef, m)

        def arm(handoff):
            _cli_env("set-active-run",
                     {"owner_session_id": "OWNER", "surface_path": statef,
                      "total_slices": 4, "pending_handoff": handoff}, env)

        # ---- SESSION 1 (owner): compute + run S1 hands-off ----
        code, out = _cli("compute-handoff",
                         {"register_markdown": _NS11_REGISTER,
                          "slice_execution": execmap()})
        assert code == 0
        h1 = out["handoff"]
        # C4 — a plain checkable slice runs hands-off
        assert h1["slice_id"] == "S1" and h1["type"] == "continue-the-run"
        assert h1["dispatch"] == "hands-off"
        arm(h1)
        # S1 completes hands-off, with timing captured (C5 instrumentation)
        complete("S1", type="implementation",
                 started_at="2026-06-24T10:00:00Z",
                 completed_at="2026-06-24T10:08:00Z")

        # ---- SESSION 2 (FRESH process): C1/C2 resume from the pointer alone ----
        # arm the next handoff (S2 plan-needing, no plan on disk yet)
        codeN, outN = _cli("compute-handoff",
                           {"register_markdown": _NS11_REGISTER,
                            "slice_execution": execmap(),
                            "slice_plan_paths": {"S2": planf}})
        h2 = outN["handoff"]
        assert h2["slice_id"] == "S2" and h2["type"] == "plan-this-slice"   # C3
        arm(h2)
        # C1/C2 — a fresh process reads ONLY the durable pointer (no transcript,
        # no manual /work-start|/work-done|/prompt-for-handoff) and knows the next
        # step + that it is a plan detour
        codeR, outR = _cli_env("resume", {}, env)
        assert codeR == 0 and outR["action"] == "resume"
        assert outR["slice_id"] == "S2" and outR["next"] == "plan-detour"

        # C3 — the plan-detour: file the plan, verify, flip, mark-exhausted
        with open(planf, "w") as fh:
            fh.write("# S2 plan")
        # one-plan-per-session: after planning S2, a SECOND plan mode is blocked
        _cli_env("mark-plan-exhausted", {}, env)
        # flip: with the _PLAN now on disk, compute yields implement-this-slice
        codeF, outF = _cli("compute-handoff",
                           {"register_markdown": _NS11_REGISTER,
                            "slice_execution": execmap(),
                            "slice_plan_paths": {"S2": planf}})
        assert outF["handoff"]["type"] == "implement-this-slice"      # C3 flip
        assert outF["handoff"]["plan_path"] == planf
        # C3 — a second plan-this-slice this session is refused (cross-session)
        secondplan = {"type": "plan-this-slice", "dispatch": "attended",
                      "slice_id": "S9"}
        arm(secondplan)
        codeX, outX = _cli_env("resume", {}, env)
        assert outX["next"] == "cross-session-boundary"
        complete("S2", type="plan",
                 started_at="2026-06-24T11:00:00Z",
                 completed_at="2026-06-24T11:30:00Z")

        # ---- SESSION 3 (FRESH): C6 ungated + out-of-session dispatch ----
        codeC, outC = _cli("compute-handoff",
                           {"register_markdown": _NS11_REGISTER,
                            "slice_execution": execmap()})
        h3 = outC["handoff"]
        assert h3["slice_id"] == "S3" and h3["dispatch"] == "hands-off"
        arm(h3)
        # C6 — S3 is declared hands-off but has NO verification gate at dispatch
        # -> promoted to attended (never runs hands-off unverified)
        codeP, outP = _cli_env("resume", {"has_verification_gate": False}, env)
        assert outP["next"] == "dispatch-attended"
        assert "gate" in outP["gate_warning"].lower()
        complete("S3", type="implementation", attended=True,
                 started_at="2026-06-24T12:00:00Z",
                 completed_at="2026-06-24T12:20:00Z")

        # ---- SESSION 4 (FRESH): C6 out-of-session verifier-isolation slice ----
        codeO, outO = _cli("compute-handoff",
                           {"register_markdown": _NS11_REGISTER,
                            "slice_execution": execmap()})
        h4 = outO["handoff"]
        assert h4["slice_id"] == "S4" and h4["dispatch"] == "out-of-session"
        arm(h4)
        codeB, outB = _cli_env("resume", {}, env)
        # C6 — rendered as an out-of-session copy-paste block, run OUTSIDE the
        # producer's process tree, migrate-before-register ordering
        assert outB["next"] == "dispatch-out-of-session"
        block = outB["out_of_session_block"]
        assert block["ordering"] == "migrate-before-register"
        assert block["run_outside_producer_process_tree"] is True
        complete("S4", type="implementation",
                 started_at="2026-06-24T13:00:00Z",
                 completed_at="2026-06-24T13:05:00Z")

        # ---- C7 — register-vs-code drift surfaced at an attended resume ----
        # claim S4 completed but the code on disk is partial -> drift
        partial = os.path.join(d, "S4_out.md")
        with open(partial, "w") as fh:
            fh.write("S4 only stubbed, still incomplete\n")
        codeD, outD = _cli("reconcile-code-face",
                           {"slice_execution": execmap(),
                            "specs_by_slice": {"S4": [{
                                "surface_kind": "todo_line", "path": partial,
                                "expected_payload": "DONE", "locator": r"^S4 .*"}]}})
        assert codeD == 0 and outD["drift"] is True
        assert outD["drifts"][0]["slice_id"] == "S4"

        # ---- C5 — context bounded: ONLY durable state crossed every boundary ----
        codeCtx, outCtx = _cli_env("resume-context", {}, env)
        # the re-seed bundle carries only the durable refs (handoff + status +
        # deferred captures) — never a finished agent's transcript
        assert "handoff" in outCtx
        # GENUINE boundedness: every persisted run-state entry holds only bounded
        # status/timing keys (no transcript / messages / output blob accumulating)
        for sid, entry in execmap().items():
            assert set(entry) <= _DURABLE_ENTRY_KEYS, (sid, set(entry))
            assert not ({"transcript", "messages", "output"} & set(entry))
        # the run is now complete: compute yields no further handoff
        codeEnd, outEnd = _cli("compute-handoff",
                               {"register_markdown": _NS11_REGISTER,
                                "slice_execution": execmap()})
        assert outEnd["handoff"] is None

        # ---- C1 — no manual cross-session lifecycle commands ----
        # Executable structural guard: the orchestrator exposes NONE of the manual
        # lifecycle skills, so a cross-session run CANNOT invoke /work-start,
        # /work-done, or /prompt-for-handoff through run.py — the whole walk above
        # was driven only by orchestrator seams (set-active-run / compute-handoff /
        # resume / mark-plan-exhausted / reconcile-code-face / resume-context).
        assert not ({"work-start", "work-done", "prompt-for-handoff"}
                    & set(run.SUBCOMMANDS))
        # OMTM excludes the attended (S3) + plan (S2) slices.
        om = run.compute_omtm(execmap())
        # plain hands-off slices counted: S1 (8 min) + S4 (5 min) = 2 slices
        assert om["plain_impl_slices"] == 2
        assert om["excluded"]["attended"] == 1 and om["excluded"]["plan_needing"] == 1


# --------------------------------------------------------------------------- #
# S1 — contract-hardening domain (execplan-contract-hardening, 2026-07-20):
# register-presence, CAS walker, atomic checkout-slice (exclusive current_slice_id
# writer), execplan-entry-check, set-active-run preserve/reset, legacy safe-default
# + reversible migration.
# --------------------------------------------------------------------------- #

def _pointer_doc(state_dir, surface, worktree=None):
    rid = run.compute_run_id(surface, worktree)
    p = os.path.join(state_dir, f"run-{rid}.json")
    return json.loads(open(p, encoding="utf-8").read())


# --- register-presence --- #

def test_register_presence_true_for_two_non_closing():
    # REGISTER_MD: S1/S2/S3 implementation + S4 implementation_verification → 3
    present, n = run.register_presence(REGISTER_MD)
    assert present is True and n == 3


def test_register_presence_false_for_single_work():
    one_work = (
        "| ID | Name | Type | Slicing idea | Model | Depends on |\n"
        "|----|------|------|--------------|-------|-----------|\n"
        "| S1 | only | implementation | x | routine | — |\n"
        "| S2 | close | implementation_verification | y | routine | S1 |\n"
    )
    present, n = run.register_presence(one_work)
    assert present is False and n == 1


def test_register_presence_counts_a_closing_typed_tail_as_closing():
    """The `| Slice | Type |` template writes `work` / `closing`, and at least
    four landed plans do. The parser knew only `implementation_verification`, so
    a `closing`-typed S-final counted as a THIRD work slice — present:true with
    count 3 on a two-work-slice plan — and an armed walk would have tried to
    implement the verification slice as ordinary work. Failable: drop
    `closing` from `CLOSING_SLICE_TYPES` and this asserts 3 == 2."""
    plan_style = (
        "| Slice | Type | Depends on | Scope | Write targets |\n"
        "|---|---|---|---|---|\n"
        "| **S1** | work | — | first | `a.py` |\n"
        "| **S2** | work | S1 | second | `b.py` |\n"
        "| **S-final** | closing | S1, S2 | verify | none |\n"
    )
    present, n = run.register_presence(plan_style)
    assert present is True and n == 2
    d = run.parse_slice_register_detail(plan_style)
    assert d["raw_non_closing_count"] == 2
    assert [s.id for s in d["slices"]] == ["S1", "S2", "S-final"]
    # Case-insensitive, and the enum spelling still counts as closing too.
    assert run._is_closing_type("Closing") and \
        run._is_closing_type("implementation_verification")
    assert not run._is_closing_type("work") and not run._is_closing_type("")


def test_register_presence_cli():
    code, out = _cli_env("register-presence", {"register_markdown": REGISTER_MD}, {})
    assert code == 0 and out["present"] is True and out["non_closing_count"] == 3
    code, out = _cli_env("register-presence", {}, {})
    assert code == 3  # neither register_markdown nor spine_path


# --- CAS walker election --- #

def test_become_walker_accepts_null_or_self_rejects_other():
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, surface="/w", worktree=None, walker=None)
        # null → this session becomes walker
        r1 = run.become_walker({"surface_path": "/w", "session_id": "S_A"}, base=d)
        assert r1["ok"] and r1["walking_session_id"] == "S_A"
        # self → still ok (idempotent)
        r2 = run.become_walker({"surface_path": "/w", "session_id": "S_A"}, base=d)
        assert r2["ok"] and r2["walking_session_id"] == "S_A"
        # a DIFFERENT session cannot clobber the first
        r3 = run.become_walker({"surface_path": "/w", "session_id": "S_B"}, base=d)
        assert r3["ok"] is False and r3["rejected"] is True
        assert r3["walking_session_id"] == "S_A"
        assert _pointer_doc(d, "/w")["walking_session_id"] == "S_A"


def test_become_walker_fail_closed_when_pointer_missing():
    with tempfile.TemporaryDirectory() as d:
        r = run.become_walker({"run_id": "deadbeef0000", "session_id": "S_A"},
                              base=d)
        assert r["ok"] is False and r["rejected"] is True


# --- atomic checkout-slice --- #

def _arm_checkout_fixture(d, worktree=None):
    spine = os.path.join(d, "topic_THOUGHT.md")
    with open(spine, "w", encoding="utf-8") as fh:
        fh.write(REGISTER_MD)
    state = os.path.join(d, "topic.run-state.json")
    payload = {"owner_session_id": "O", "surface_path": spine,
               "state_path": state}
    if worktree:
        payload["worktree_root"] = worktree
    _cli_env("set-active-run", payload, {"EXECPLAN_ACK_STATE_DIR": d})
    return spine, state


def test_checkout_slice_sets_current_slice_id_and_marks_started():
    with tempfile.TemporaryDirectory() as d:
        spine, state = _arm_checkout_fixture(d)
        res = run.checkout_slice(
            {"surface_path": spine, "slice_id": "S1", "state_path": state}, base=d)
        assert res["ok"] and res["current_slice_id"] == "S1" and res["started"]
        # pointer field written
        assert _pointer_doc(d, spine)["current_slice_id"] == "S1"
        # slice recorded started on the state surface with its resolved family
        st = json.loads(open(state, encoding="utf-8").read())
        assert st["S1"]["status"] == "started"
        assert st["S1"]["model_family"] == "sonnet"


def test_checkout_slice_rejects_non_ready():
    with tempfile.TemporaryDirectory() as d:
        spine, state = _arm_checkout_fixture(d)
        # S3 depends on S2 (incomplete) and S1 is the actual next-ready → reject
        res = run.checkout_slice(
            {"surface_path": spine, "slice_id": "S3", "state_path": state}, base=d)
        assert res["ok"] is False and res["rejected"] is True
        assert res["current_slice_id"] is None
        assert _pointer_doc(d, spine).get("current_slice_id") is None


def test_checkout_slice_after_deps_completed():
    with tempfile.TemporaryDirectory() as d:
        spine, state = _arm_checkout_fixture(d)
        _write_json(state, {"S1": {"status": "completed"},
                            "S2": {"status": "completed"}})
        res = run.checkout_slice(
            {"surface_path": spine, "slice_id": "S3", "state_path": state}, base=d)
        assert res["ok"] and res["current_slice_id"] == "S3"


def test_mark_started_never_writes_current_slice_id_single_writer():
    with tempfile.TemporaryDirectory() as d:
        spine, state = _arm_checkout_fixture(d)
        # a raw mark_started on the state surface must NOT touch the pointer field
        run.MinimalBookkeepingAdapter(state).mark_started("S1", model_family="sonnet")
        assert _pointer_doc(d, spine).get("current_slice_id") is None
        # only checkout assigns it
        run.checkout_slice(
            {"surface_path": spine, "slice_id": "S1", "state_path": state}, base=d)
        assert _pointer_doc(d, spine)["current_slice_id"] == "S1"


def test_current_slice_id_assigned_only_by_checkout_source_guard():
    # Source guard: a SLICE VALUE is assigned to current_slice_id ONLY inside the
    # checkout verb. set-active-run may reference it (preserve/null-reset), but no
    # mark_started/record_attempt method body may mention it.
    src = open(run.__file__, encoding="utf-8").read()
    import re as _re
    for m in _re.finditer(r"def (mark_started|record_attempt)\b.*?(?=\n    def |\nclass |\ndef )",
                          src, _re.DOTALL):
        assert "current_slice_id" not in m.group(0), m.group(0)[:80]
    # the only `["current_slice_id"] = <slice value>` assignment is in checkout_slice
    assert 'newdoc["current_slice_id"] = slice_id' in src


# --- execplan-entry-check --- #

def _arm_pending(d, walking=None, worktree=None, surface="/topic_THOUGHT.md"):
    payload = {"owner_session_id": "O", "surface_path": surface,
               "execution_pending": True}
    if walking is not None:
        payload["walking_session_id"] = walking
    if worktree:
        payload["worktree_root"] = worktree
    _cli_env("set-active-run", payload, {"EXECPLAN_ACK_STATE_DIR": d})


def test_entry_check_blocks_non_walking_session_in_worktree():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        target = os.path.join(wt, "src", "file.py")
        # a different session writing in the worktree → blocked
        dec = run.execplan_entry_check("S_OTHER", wt, target, base=d)
        assert dec["block"] is True and dec["run_id"]
        # the walking session itself → exempt
        assert run.execplan_entry_check("S_W", wt, target, base=d)["block"] is False


def test_entry_check_not_blocked_when_not_pending():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        # execution_pending False → never blocks
        _arm_run(d, surface="/t_THOUGHT.md", worktree=wt, pending=False)
        target = os.path.join(wt, "f.py")
        assert run.execplan_entry_check("S_X", wt, target, base=d)["block"] is False


def test_entry_check_excludes_bookkeeping_paths():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        for rel in ("TODO.md", "foo_PLAN.md", "bar_THOUGHT.md",
                    "topic.run-state.json", os.path.join("Diary", "2026-07-20.md")):
            target = os.path.join(wt, rel)
            dec = run.execplan_entry_check("S_OTHER", wt, target, base=d)
            assert dec["block"] is False, rel
        # ~/.claude/state and ~/.claude/plans are excluded too
        home = os.path.expanduser("~")
        for abs_ex in (os.path.join(home, ".claude", "state", "x.json"),
                       os.path.join(home, ".claude", "plans", "p.md")):
            assert run.execplan_entry_check("S_OTHER", wt, abs_ex, base=d)["block"] is False


def test_entry_check_out_of_worktree_not_blocked():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        outside = os.path.join(d, "elsewhere", "f.py")
        assert run.execplan_entry_check("S_OTHER", wt, outside, base=d)["block"] is False


def test_entry_check_path_suppressed_not_blocked():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        target = os.path.join(wt, "src", "file.py")
        assert run.execplan_entry_check("S_OTHER", wt, target, base=d)["block"] is True
        run.add_entry_suppression(d, "S_OTHER", target)
        assert run.execplan_entry_check("S_OTHER", wt, target, base=d)["block"] is False


def test_entry_check_own_namespace_not_gate_check_namespace():
    # The suppression file uses its OWN namespace (entry-suppress-<sid>.json) — it is
    # NOT the pending-/acked- namespace execplan-gate-check owns.
    with tempfile.TemporaryDirectory() as d:
        run.add_entry_suppression(d, "S_OTHER", "/wt/x.py")
        assert os.path.exists(os.path.join(d, "entry-suppress-S_OTHER.json"))
        assert not os.path.exists(os.path.join(d, "pending-S_OTHER.json"))
        assert not os.path.exists(os.path.join(d, "acked-S_OTHER.json"))


def test_entry_check_cli():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        code, out = _cli_env("execplan-entry-check",
                             {"writing_session_id": "S_OTHER", "worktree_root": wt,
                              "target_path": os.path.join(wt, "f.py")},
                             {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["block"] is True
        code, _ = _cli_env("execplan-entry-check",
                           {"worktree_root": wt}, {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 3  # missing writing_session_id / target_path


# --- set-active-run preserve / reset --- #

def test_set_active_run_preserves_walk_fields_on_omit_and_resets_on_null():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, out = _cli_env("set-active-run",
                          {"owner_session_id": "O", "surface_path": "/x",
                           "execution_pending": True, "walking_session_id": "W",
                           "current_slice_id": "S1"}, env)
        p = out["pointer"]
        doc = json.loads(open(p, encoding="utf-8").read())
        assert doc["execution_pending"] is True and doc["walking_session_id"] == "W"
        assert doc["current_slice_id"] == "S1"
        # re-arm OMITTING the fields → preserved (per-slice re-arm)
        _cli_env("set-active-run",
                 {"owner_session_id": "O", "surface_path": "/x", "total_slices": 9},
                 env)
        doc = json.loads(open(p, encoding="utf-8").read())
        assert doc["execution_pending"] is True and doc["walking_session_id"] == "W"
        assert doc["current_slice_id"] == "S1"
        # explicit null-set → reset (stop/completion re-engages the gate)
        _cli_env("set-active-run",
                 {"owner_session_id": "O", "surface_path": "/x",
                  "execution_pending": False, "walking_session_id": None,
                  "current_slice_id": None}, env)
        doc = json.loads(open(p, encoding="utf-8").read())
        assert doc["execution_pending"] is False
        assert doc["walking_session_id"] is None and doc["current_slice_id"] is None


# --- legacy safe-default + reversible migration --- #

def test_legacy_pointer_loads_and_defaults_safely():
    with tempfile.TemporaryDirectory() as d:
        rid = run.compute_run_id("/leg", None)
        p = os.path.join(d, f"run-{rid}.json")
        _write_json(p, {"run_id": rid, "owner_session_id": "O",
                        "surface_path": "/leg", "worktree_root": None,
                        "created_at": run._now_iso()})
        doc = json.loads(open(p, encoding="utf-8").read())
        # readers default safely with the new fields absent
        assert run._pointer_execution_pending(doc) is False
        assert run._pointer_walking_session_id(doc) is None
        assert run._pointer_current_slice_id(doc) is None
        # the verbs consume a legacy pointer without error
        assert run.become_walker({"run_id": rid, "session_id": "W"}, base=d)["ok"]
        assert run.execplan_entry_check("OTHER", "/leg-wt", "/leg-wt/f.py",
                                        base=d)["block"] is False


def test_migrate_and_restore_round_trip():
    with tempfile.TemporaryDirectory() as d:
        rid = run.compute_run_id("/leg", None)
        p = os.path.join(d, f"run-{rid}.json")
        legacy = {"run_id": rid, "owner_session_id": "O", "surface_path": "/leg",
                  "worktree_root": None, "created_at": run._now_iso()}
        _write_json(p, legacy)
        # migrate: new fields added + backup taken with the original shape
        migrated = run.migrate_run_pointer_fields(base=d)
        assert rid in migrated
        doc = json.loads(open(p, encoding="utf-8").read())
        assert doc["execution_pending"] is False
        assert "walking_session_id" in doc and "current_slice_id" in doc
        assert os.path.exists(p + ".legacy-bak")
        bak = json.loads(open(p + ".legacy-bak", encoding="utf-8").read())
        assert "execution_pending" not in bak     # backup is the pre-migration shape
        # migrate is idempotent (already-migrated pointer is skipped)
        assert run.migrate_run_pointer_fields(base=d) == []
        # restore rolls back to the legacy shape and removes the backup
        assert run.restore_legacy_pointer(p) is True
        restored = json.loads(open(p, encoding="utf-8").read())
        assert "execution_pending" not in restored and restored == legacy
        assert not os.path.exists(p + ".legacy-bak")


def test_set_active_run_backs_up_legacy_pointer_before_new_fields():
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        rid = run.compute_run_id("/leg", None)
        p = os.path.join(d, f"run-{rid}.json")
        _write_json(p, {"run_id": rid, "owner_session_id": "O",
                        "surface_path": "/leg", "worktree_root": None,
                        "created_at": run._now_iso()})
        _cli_env("set-active-run",
                 {"owner_session_id": "O", "surface_path": "/leg",
                  "execution_pending": True}, env)
        # the new fields landed AND a reversible backup exists
        doc = json.loads(open(p, encoding="utf-8").read())
        assert doc["execution_pending"] is True
        assert os.path.exists(p + ".legacy-bak")
        assert run.restore_legacy_pointer(p) is True
        assert "execution_pending" not in json.loads(open(p, encoding="utf-8").read())


# --------------------------------------------------------------------------- #
# S2 — execution-boundary receipts + walk gate (execplan-contract-hardening).
#   code receipt on check-code PASS (keyed, EXECPLAN_ACK_STATE_DIR-isolated);
#   record-conformance aggregates ISOLATED checker outputs (PASS / DISCREPANCY)
#   and REFUSES an inline verdict; legacy cmd_check_conformance writes NO receipt;
#   walk_gate_check enforces the checkout invariant (edit / [SLICE:Sn] spawn /
#   commit).
# --------------------------------------------------------------------------- #

def _receipt_dir(d, run_id):
    return os.path.join(d, "execplan", "receipts", run_id)


# --- code receipt on check-code PASS --- #

def test_check_code_writes_code_receipt_on_pass_keyed():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] **my task** done\n")
        code, out = _cli_env("check-code", {
            "run_id": "RID1", "slice_id": "S1",
            "verify_specs": [{"surface_kind": "todo_line", "path": p,
                              "expected_payload": "my task", "locator": "my task"}],
        }, {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["status"] == "OK"
        rp = os.path.join(_receipt_dir(d, "RID1"), "S1.code.json")
        assert out["receipt_path"] == rp and os.path.exists(rp)
        doc = json.loads(open(rp, encoding="utf-8").read())
        assert doc["verdict"] == "PASS" and doc["layer"] == "code"
        assert doc["kind"] == "code" and doc["slice_id"] == "S1"


def test_check_code_no_receipt_when_unkeyed():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] **my task** done\n")
        code, out = _cli_env("check-code", {
            "verify_specs": [{"surface_kind": "todo_line", "path": p,
                              "expected_payload": "my task", "locator": "my task"}],
        }, {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["status"] == "OK" and "receipt_path" not in out
        assert not os.path.exists(os.path.join(d, "execplan"))


def test_check_code_no_receipt_on_fail():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "TODO.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("- [ ] something else\n")
        code, out = _cli_env("check-code", {
            "run_id": "RID1", "slice_id": "S1",
            "verify_specs": [{"surface_kind": "todo_line", "path": p,
                              "expected_payload": "my task", "locator": "my task"}],
        }, {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 5 and out["status"] == "FAIL"
        assert not os.path.exists(
            os.path.join(_receipt_dir(d, "RID1"), "S1.code.json"))


# --- record-conformance aggregates isolated checker outputs --- #

def test_record_conformance_aggregates_pass_set_to_pass_receipt():
    with tempfile.TemporaryDirectory() as d:
        code, out = _cli_env("record-conformance", {
            "run_id": "RID1", "slice_id": "S2",
            "checkers_json": [{"model": "sonnet", "verdict": "PASS"},
                              {"model": "sonnet", "verdict": "PASS"},
                              {"model": "sonnet", "verdict": "PASS"}],
        }, {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["status"] == "OK" and out["verdict"] == "PASS"
        rp = os.path.join(_receipt_dir(d, "RID1"), "S2.conformance.json")
        assert out["receipt_path"] == rp and os.path.exists(rp)
        doc = json.loads(open(rp, encoding="utf-8").read())
        assert doc["verdict"] == "PASS" and doc["layer"] == "conformance"
        assert doc["checker_count"] == 3


def test_record_conformance_aggregates_discrepancy_set_to_non_pass_receipt():
    with tempfile.TemporaryDirectory() as d:
        code, out = _cli_env("record-conformance", {
            "run_id": "RID1", "slice_id": "S2",
            "checkers_json": [{"model": "sonnet", "verdict": "PASS"},
                              {"model": "sonnet", "verdict": "DISCREPANCY: mismatch"},
                              {"model": "sonnet", "verdict": "PASS"}],
        }, {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["status"] == "OK"
        assert out["verdict"] != "PASS"       # DIRTY / ESCALATE — a non-PASS receipt
        doc = json.loads(open(
            os.path.join(_receipt_dir(d, "RID1"), "S2.conformance.json"),
            encoding="utf-8").read())
        assert doc["verdict"] != "PASS"


def test_record_conformance_refuses_inline_verdict():
    with tempfile.TemporaryDirectory() as d:
        code, out = _cli_env("record-conformance", {
            "run_id": "RID1", "slice_id": "S2", "verdict": "PASS",
            "checkers_json": [{"model": "sonnet", "verdict": "PASS"}],
        }, {"EXECPLAN_ACK_STATE_DIR": d})
        # [C1] forgery path closed: inline verdict refused, NO receipt written.
        assert code == 3 and out["status"] == "ERROR"
        assert "REFUSES an inline 'verdict'" in out["error"]
        assert not os.path.exists(
            os.path.join(_receipt_dir(d, "RID1"), "S2.conformance.json"))


def test_record_conformance_refuses_empty_checkers():
    with tempfile.TemporaryDirectory() as d:
        code, out = _cli_env("record-conformance",
                             {"run_id": "RID1", "slice_id": "S2"},
                             {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 3 and out["status"] == "ERROR"
        assert not os.path.exists(os.path.join(d, "execplan"))


# --- legacy cmd_check_conformance is fenced out of the receipt store [C4] --- #

def test_legacy_check_conformance_writes_no_receipt_and_is_fenced():
    with tempfile.TemporaryDirectory() as d:
        code, out = _cli_env("check-conformance",
                             {"verdict": "PASS", "run_id": "RID1", "slice_id": "S2"},
                             {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["status"] == "OK" and out["verdict"] == "PASS"
        assert out.get("deprecated") is True and "[C4]" in out.get("fenced", "")
        # the S2 receipt store is untouched — the honor-system path cannot forge one
        assert not os.path.exists(os.path.join(d, "execplan"))


# --- walk_gate_check enforces the checkout invariant --- #

def _arm_walk(d, surface, worktree, walking="S_W", current=None):
    payload = {"owner_session_id": "O", "surface_path": surface,
               "worktree_root": worktree, "execution_pending": True,
               "walking_session_id": walking}
    if current is not None:
        payload["current_slice_id"] = current
    _cli_env("set-active-run", payload, {"EXECPLAN_ACK_STATE_DIR": d})
    return run.compute_run_id(surface, worktree)


def test_walk_gate_blocks_edit_when_no_slice_checked_out():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        _arm_walk(d, spine, wt, walking="S_W", current=None)
        dec = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "edit",
             "target_or_tag": os.path.join(wt, "f.py"),
             "surface_path": spine, "worktree_root": wt}, base=d, state_root=d)
        assert dec["block"] is True and dec["current_slice_id"] is None


def test_walk_gate_allows_edit_when_slice_checked_out():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        _arm_walk(d, spine, wt, walking="S_W", current="S1")
        dec = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "write",
             "target_or_tag": os.path.join(wt, "f.py"),
             "surface_path": spine, "worktree_root": wt}, base=d, state_root=d)
        assert dec["block"] is False and dec["current_slice_id"] == "S1"


def test_walk_gate_non_walking_session_is_allowed_no_double_gate():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        _arm_walk(d, spine, wt, walking="S_W", current=None)
        # a DIFFERENT session is the entry gate's concern, not the walk gate's
        dec = run.walk_gate_check(
            {"writing_session_id": "S_OTHER", "action": "edit",
             "target_or_tag": os.path.join(wt, "f.py"),
             "surface_path": spine, "worktree_root": wt}, base=d, state_root=d)
        assert dec["block"] is False


def test_walk_gate_blocks_spawn_when_slice_not_current():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        _arm_walk(d, spine, wt, walking="S_W", current="S1")
        dec = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "agent",
             "target_or_tag": "[SLICE:S3]", "surface_path": spine,
             "worktree_root": wt, "register_markdown": REGISTER_MD},
            base=d, state_root=d)
        assert dec["block"] is True and "not the checked-out slice" in dec["reason"]


def test_walk_gate_blocks_spawn_when_dep_lacks_pass_receipt():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        _arm_walk(d, spine, wt, walking="S_W", current="S3")
        # S3 depends on S2; no S2 conformance receipt yet → blocked
        dec = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "agent",
             "target_or_tag": "[SLICE:S3]", "surface_path": spine,
             "worktree_root": wt, "register_markdown": REGISTER_MD},
            base=d, state_root=d)
        assert dec["block"] is True and "S2" in dec["reason"]


def test_walk_gate_allows_spawn_when_dep_has_pass_receipt():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        rid = _arm_walk(d, spine, wt, walking="S_W", current="S3")
        # record a PASS conformance receipt for the dependency S2
        res = run.record_conformance(
            {"run_id": rid, "slice_id": "S2",
             "checkers_json": [{"model": "sonnet", "verdict": "PASS"}]},
            state_root=d)
        assert res["status"] == "OK" and res["verdict"] == "PASS"
        dec = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "agent",
             "target_or_tag": "[SLICE:S3]", "surface_path": spine,
             "worktree_root": wt, "register_markdown": REGISTER_MD},
            base=d, state_root=d)
        assert dec["block"] is False and dec["current_slice_id"] == "S3"


def test_walk_gate_blocks_commit_without_code_receipt_then_allows():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        rid = _arm_walk(d, spine, wt, walking="S_W", current="S1")
        blocked = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "commit",
             "target_or_tag": "S1:", "surface_path": spine, "worktree_root": wt},
            base=d, state_root=d)
        assert blocked["block"] is True and "code-layer receipt" in blocked["reason"]
        run.write_code_receipt(rid, "S1", state_root=d)
        allowed = run.walk_gate_check(
            {"writing_session_id": "S_W", "action": "commit",
             "target_or_tag": "S1:", "surface_path": spine, "worktree_root": wt},
            base=d, state_root=d)
        assert allowed["block"] is False


def test_walk_gate_cli_roundtrips_decision():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        spine = os.path.join(d, "topic_THOUGHT.md")
        _arm_walk(d, spine, wt, walking="S_W", current=None)
        code, out = _cli_env("walk-gate-check", {
            "writing_session_id": "S_W", "action": "edit",
            "target_or_tag": os.path.join(wt, "f.py"),
            "surface_path": spine, "worktree_root": wt},
            {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 0 and out["block"] is True
        code, out = _cli_env("walk-gate-check", {"action": "edit"},
                             {"EXECPLAN_ACK_STATE_DIR": d})
        assert code == 3  # missing writing_session_id


# --------------------------------------------------------------------------- #
# A3 — scan-unarmed SessionStart WARNING (multi-step plan taken into
# implementation OFF the /plan Step-11 arming path). Self-contained: every plan +
# pointer is written under a tempdir; no dependency on files under ~/repos.
# --------------------------------------------------------------------------- #

_S3_MULTISTEP_MD = """\
# Plan
Some prose.

#### Slices
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | a | implementation | x | routine | — |
| S2 | b | implementation | y | routine | — |
| S3 | c | implementation_verification | z | routine | S1, S2 |
"""

_S3_MALFORMED_MD = """\
# Plan
Looks like a register but has no Type column.

#### Slices
| ID | Name | Depends on |
|----|------|-----------|
| S1 | a | — |
| S2 | b | S1 |
"""

_S3_SINGLEWORK_MD = """\
# Plan
One work slice + its closing verification slice.

#### Slices
| ID | Name | Type | Slicing idea | Model | Depends on |
|----|------|------|--------------|-------|-----------|
| S1 | a | implementation | x | routine | — |
| S2 | b | implementation_verification | z | routine | S1 |
"""

_S3_BROKEN_REF_MD = """\
# Plan
Prose only, no inline register.

slice_register_ref: ./does_not_exist_THOUGHT.md#slice-register
"""


def _write_plan(dirpath, name, text):
    path = os.path.join(dirpath, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _write_arm_pointer(pointer_dir, surface_path, *, pending=True, name="run-t.json"):
    os.makedirs(pointer_dir, exist_ok=True)
    path = os.path.join(pointer_dir, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"surface_path": surface_path,
                   "execution_pending": pending}, fh)
    return path


def test_s3_unarmed_multistep_warns():
    with tempfile.TemporaryDirectory() as d:
        plan = _write_plan(d, "topic-1_PLAN.md", _S3_MULTISTEP_MD)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        res = run.scan_unarmed({"plan_paths": [plan], "pointer_dir": pd})
        assert res["count"] == 1, res
        assert res["warnings"][0]["kind"] == "unarmed-multistep"
        assert res["warnings"][0]["plan"] == plan


def test_s3_armed_multistep_no_warning():
    with tempfile.TemporaryDirectory() as d:
        plan = _write_plan(d, "topic-1_PLAN.md", _S3_MULTISTEP_MD)
        pd = os.path.join(d, "ptr")
        _write_arm_pointer(pd, plan, pending=True)
        res = run.scan_unarmed({"plan_paths": [plan], "pointer_dir": pd})
        assert res["count"] == 0, res


def test_s3_malformed_multistep_warns():
    with tempfile.TemporaryDirectory() as d:
        plan = _write_plan(d, "topic-1_PLAN.md", _S3_MALFORMED_MD)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        res = run.scan_unarmed({"plan_paths": [plan], "pointer_dir": pd})
        assert res["count"] == 1, res
        assert res["warnings"][0]["kind"] == "malformed-multistep"


def test_s3_single_work_plan_no_warning():
    with tempfile.TemporaryDirectory() as d:
        plan = _write_plan(d, "topic-1_PLAN.md", _S3_SINGLEWORK_MD)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        res = run.scan_unarmed({"plan_paths": [plan], "pointer_dir": pd})
        assert res["count"] == 0, res


def test_s3_retired_plan_skipped():
    with tempfile.TemporaryDirectory() as d:
        text = "bookkeeping: retired-2026-01-01\n" + _S3_MULTISTEP_MD
        plan = _write_plan(d, "topic-1_PLAN.md", text)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        res = run.scan_unarmed({"plan_paths": [plan], "pointer_dir": pd})
        assert res["count"] == 0, res


def test_s3_broken_ref_warns_malformed():
    with tempfile.TemporaryDirectory() as d:
        plan = _write_plan(d, "topic-1_PLAN.md", _S3_BROKEN_REF_MD)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        res = run.scan_unarmed({"plan_paths": [plan], "pointer_dir": pd})
        assert res["count"] == 1, res
        assert res["warnings"][0]["kind"] == "malformed-multistep"


def test_s3_thoughts_dir_slug_globs_bare_and_scope_keyed():
    with tempfile.TemporaryDirectory() as d:
        td = os.path.join(d, "Thoughts")
        os.makedirs(td)
        p_bare = _write_plan(td, "mytopic-20260101000000_PLAN.md", _S3_MULTISTEP_MD)
        p_scope = _write_plan(td, "mytopic-20260101000000_K_PLAN.md", _S3_MULTISTEP_MD)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        res = run.scan_unarmed({"thoughts_dir": td, "slug": "mytopic",
                                "pointer_dir": pd})
        assert res["count"] == 2, res
        found = {w["plan"] for w in res["warnings"]}
        assert found == {p_bare, p_scope}
        assert all(w["kind"] == "unarmed-multistep" for w in res["warnings"])


def test_s3_cli_scan_unarmed_exits_0():
    with tempfile.TemporaryDirectory() as d:
        plan = _write_plan(d, "topic-1_PLAN.md", _S3_MULTISTEP_MD)
        pd = os.path.join(d, "ptr")
        os.makedirs(pd)
        code, out = _cli("scan-unarmed", {"plan_paths": [plan], "pointer_dir": pd})
        assert code == 0 and out["count"] == 1
        assert out["warnings"][0]["kind"] == "unarmed-multistep"


# --------------------------------------------------------------------------- #
# S1 — the two correctness leaks (execplan-gate-blast-radius A1 + A2, gaps G6/G5)
# --------------------------------------------------------------------------- #


def test_a1_path_inside_contains_symlink_spelled_target():
    # A1/G6: a target spelled through a symlinked ancestor is INSIDE the root.
    # Before the fix both containment tests compared unresolved paths, so this
    # read as "outside" and BOTH gates silently failed OPEN. Live case: ~/Projects
    # is a symlink to ~/repos/Projects.
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "real")
        os.makedirs(os.path.join(real, "src"))
        alias = os.path.join(d, "alias")
        os.symlink(real, alias)
        # the target does not need to exist — realpath resolves the ancestors
        assert run._path_inside(os.path.join(alias, "src", "x.py"), real) is True
        assert run._path_inside(os.path.join(alias, "new_file.py"), real) is True
        # the root may be the alias and the target the real spelling, too
        assert run._path_inside(os.path.join(real, "src", "x.py"), alias) is True


def test_a1_path_inside_keeps_literal_and_outside_verdicts():
    # A1 is purely WIDENING: the literal fast path still contains what it always
    # did, and a genuinely-outside path is still outside (the second pass must not
    # turn the containment test into "everything is inside").
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "real")
        outside = os.path.join(d, "elsewhere")
        os.makedirs(real)
        os.makedirs(outside)
        assert run._path_inside(os.path.join(real, "a", "b.py"), real) is True
        assert run._path_inside(real, real) is True
        assert run._path_inside(os.path.join(outside, "b.py"), real) is False
        # a sibling whose NAME merely prefixes the root is not inside it
        assert run._path_inside(real + "_other/b.py", real) is False


def test_a2_clear_refuses_when_ownership_is_unproven():
    # A2/G5: the guard used to fire only when the caller VOLUNTEERED an owner, and
    # the gate's own printed remediation omitted it — so any session could disarm
    # any other session's in-flight run. Omitting the owner is now the unproven
    # case, not a way past the guard.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "OWNER", "surface_path": "/p/a_THOUGHT.md",
                         "total_slices": 2}, env)
        # no owner at all → refused (this is the hole A2 closes)
        _, no_owner = _cli_env("clear-active-run", {"run_id": a["run_id"]}, env)
        assert no_owner["cleared"] is False
        assert "no owner_session_id" in no_owner["reason"]
        assert "confirm_non_owner" in no_owner["remediation"]
        assert os.path.exists(a["pointer"])
        # a WRONG owner → still refused
        _, wrong = _cli_env("clear-active-run",
                            {"run_id": a["run_id"], "owner_session_id": "STRANGER"}, env)
        assert wrong["cleared"] is False
        assert os.path.exists(a["pointer"])


def test_a2_clear_succeeds_with_explicit_non_owner_confirmation():
    # A2: the override is kept deliberately — an owner session that has ended
    # cannot come back to clear its own pointer — but it is explicit and named,
    # never implied by silence.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "OWNER", "surface_path": "/p/a_THOUGHT.md",
                         "total_slices": 2}, env)
        _, out = _cli_env("clear-active-run",
                          {"run_id": a["run_id"], "confirm_non_owner": True}, env)
        assert out["cleared"] is True
        assert out.get("non_owner_override") is True
        assert not os.path.exists(a["pointer"])


def test_a2_cleared_pointer_survives_as_a_backup_file():
    # A2: retirement MOVES the pointer aside (safe-defaults.md), never unlinks it,
    # so a wrongly-cleared run is recoverable. The backup keeps the run's content.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "OWNER", "surface_path": "/p/a_THOUGHT.md",
                         "total_slices": 4}, env)
        _, out = _cli_env("clear-active-run",
                          {"run_id": a["run_id"], "owner_session_id": "OWNER"}, env)
        assert out["cleared"] is True and not os.path.exists(a["pointer"])
        backup = out["backup"]
        assert os.path.exists(backup)
        assert ".bak-" in os.path.basename(backup)
        restored = json.loads(open(backup, encoding="utf-8").read())
        assert restored["run_id"] == a["run_id"]
        assert restored["owner_session_id"] == "OWNER"
        assert restored["total_slices"] == 4


def test_a2_clear_guards_a_pointer_that_exists_but_does_not_parse():
    # A2 regression, found by an independent checker: the guard originally keyed on
    # the pointer DOC parsing rather than the pointer EXISTING. `_read_pointer_doc`
    # returns None for a corrupt file as well as an absent one, so a pointer that
    # exists but does not parse fell through to the retire call with no ownership
    # check — reopening the hole for exactly the pointers whose state is least
    # trustworthy. An unreadable pointer is the UNPROVEN case and must fail closed.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "OWNER", "surface_path": "/p/a_THOUGHT.md",
                         "total_slices": 2}, env)
        with open(a["pointer"], "w", encoding="utf-8") as fh:
            fh.write("{ this is not valid json")
        # a stranger, with no owner and no confirmation, must NOT be able to retire it
        _, out = _cli_env("clear-active-run", {"run_id": a["run_id"]}, env)
        assert out["cleared"] is False
        assert "unreadable" in out["reason"]
        assert os.path.exists(a["pointer"])
        # the explicit override still works — a corrupt pointer must stay recoverable
        _, forced = _cli_env("clear-active-run",
                             {"run_id": a["run_id"], "confirm_non_owner": True}, env)
        assert forced["cleared"] is True
        assert not os.path.exists(a["pointer"])
        assert os.path.exists(forced["backup"])


def test_a2_legacy_fallback_clear_is_guarded_too():
    # A2 regression, found by two independent checkers: the run-scoped branch was
    # guarded but the legacy `active-run.json` fallback was not, leaving a
    # no-argument way to retire another session's pointer. With no run identity
    # there is no doc to read an owner from, so this is the unproven case by
    # construction and requires the explicit override.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        legacy = os.path.join(d, "active-run.json")
        _write_json(legacy, {"owner_session_id": "SOMEONE_ELSE",
                             "surface_path": "/p/theirs_THOUGHT.md"})
        # no run_id, no surface_path, no confirmation → refused, file untouched
        _, out = _cli_env("clear-active-run", {}, env)
        assert out["cleared"] is False
        assert "cannot be verified" in out["reason"]
        assert os.path.exists(legacy)
        # explicit override retires it, and it survives as a backup
        _, forced = _cli_env("clear-active-run", {"confirm_non_owner": True}, env)
        assert forced["cleared"] is True
        assert not os.path.exists(legacy)
        assert os.path.exists(forced["backup"])


def test_a2_legacy_migration_moves_aside_instead_of_destroying():
    # A2 regression, found by an adversarial checker: migrate_legacy_pointer runs
    # from the TOP of cmd_clear_active_run's run-scoped branch, before any ownership
    # check. When the per-run target already exists the legacy doc is NOT copied, so
    # the old `legacy.unlink()` was an unrecoverable delete of the only record of
    # that run, triggerable by any caller supplying any run_id.
    with tempfile.TemporaryDirectory() as d:
        legacy = os.path.join(d, "active-run.json")
        doc = {"run_id": "deadbeefcafe", "owner_session_id": "SOMEONE_ELSE",
               "surface_path": "/p/theirs_THOUGHT.md", "total_slices": 3}
        _write_json(legacy, doc)
        # a per-run target already exists → the legacy doc is not copied anywhere
        target = os.path.join(d, "run-deadbeefcafe.json")
        _write_json(target, {"run_id": "deadbeefcafe", "owner_session_id": "OTHER"})
        assert run.migrate_legacy_pointer(d) == "deadbeefcafe"
        assert not os.path.exists(legacy)                  # retired from that path
        baks = [f for f in os.listdir(d)
                if f.startswith("active-run.json.bak-")]
        assert len(baks) == 1, f"legacy doc must survive as a backup, found {baks}"
        restored = json.loads(open(os.path.join(d, baks[0]), encoding="utf-8").read())
        assert restored["owner_session_id"] == "SOMEONE_ELSE"


def test_a2_clear_tolerates_a_source_that_already_vanished():
    # A2 guard rail: the pid makes the DESTINATION unique, so the race that
    # actually occurs is the SOURCE vanishing — a second clear finds the pointer
    # already moved. That must report "not cleared", never raise.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _, a = _cli_env("set-active-run",
                        {"owner_session_id": "OWNER", "surface_path": "/p/a_THOUGHT.md",
                         "total_slices": 1}, env)
        code1, first = _cli_env("clear-active-run",
                                {"run_id": a["run_id"], "owner_session_id": "OWNER"}, env)
        code2, second = _cli_env("clear-active-run",
                                 {"run_id": a["run_id"], "owner_session_id": "OWNER"}, env)
        assert code1 == 0 and first["cleared"] is True
        assert code2 == 0 and second["cleared"] is False


# --------------------------------------------------------------------------- #
# S2 — armed/in-flight phase split (A3) + write-target scoping (A4), gaps G1/G2/G9
# --------------------------------------------------------------------------- #


def _arm_phase(base, *, owner="OWNER", walker=None, wt=None, surface="/p/t_THOUGHT.md",
               targets=None, slice_id="S1"):
    """Write a pointer in a specific PHASE: walker=None is armed-at-approval,
    walker=<sid> is in-flight. `targets` seeds the walked slice's write targets."""
    payload = {"owner_session_id": owner, "surface_path": surface,
               "total_slices": 3, "execution_pending": True,
               "walking_session_id": walker}
    if wt:
        payload["worktree_root"] = wt
    if targets is not None:
        payload["pending_handoff"] = {"type": "continue-the-run",
                                      "dispatch": "attended", "slice_id": slice_id,
                                      "write_targets": targets}
    _, out = _cli_env("set-active-run", payload, {"EXECPLAN_ACK_STATE_DIR": base})
    return out


def test_a3_armed_pointer_does_not_block_a_stranger():
    # A3/G1: a pointer armed at /plan Step 11 asserts a walk that has not begun.
    # It must not stop a session working on an unrelated topic — the whole diagnosed
    # harm. Before A3 this blocked every session in the checkout.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER", walker=None, wt=wt)
        dec = run.execplan_entry_check("STRANGER", wt, [os.path.join(wt, "x.py")],
                                       base=d)
        assert dec["block"] is False


def test_a3_armed_pointer_still_blocks_its_own_owner():
    # A3: holding the OWNER while armed is deliberate, not collateral — it is what
    # routes a multi-slice plan through the walker instead of hand-implementation.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER", walker=None, wt=wt)
        dec = run.execplan_entry_check("OWNER", wt, [os.path.join(wt, "x.py")], base=d)
        assert dec["block"] is True
        assert dec["phase"] == "armed"
        assert dec["walking_session_id"] is None


def test_a3_in_flight_still_blocks_a_stranger_and_exempts_the_walker():
    # A3: in-flight behaviour is unchanged for a pointer with no declared targets —
    # the walker is exempt, everyone else is blocked across the checkout.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER", walker="WALKER", wt=wt)
        target = [os.path.join(wt, "src", "x.py")]
        assert run.execplan_entry_check("STRANGER", wt, target, base=d)["block"] is True
        assert run.execplan_entry_check("WALKER", wt, target, base=d)["block"] is False


def test_a3_armed_pointer_does_not_block_another_runs_walker():
    # A3/G2: "the session actually walking a plan is hit hardest" — the walker
    # exemption is per-pointer, so run B's armed pointer used to block run A's
    # walker. An armed pointer now constrains only its own owner, so it cannot.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER_A", walker="WALKER_A", wt=wt, surface="/p/a_THOUGHT.md")
        _arm_phase(d, owner="OWNER_B", walker=None, wt=wt, surface="/p/b_THOUGHT.md")
        dec = run.execplan_entry_check("WALKER_A", wt, [os.path.join(wt, "a.py")],
                                       base=d)
        assert dec["block"] is False, dec


def test_a4_in_flight_block_is_scoped_to_declared_write_targets():
    # A4/G9: with a populated list the block covers ONLY the files the walked slice
    # says it will touch; an unrelated file in the same checkout is free.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER", walker="WALKER", wt=wt,
                   targets=["src/gate.py", "hooks/x.sh"])
        declared = run.execplan_entry_check("STRANGER", wt,
                                            [os.path.join(wt, "src", "gate.py")], base=d)
        assert declared["block"] is True and declared["scoped_to_write_targets"] is True
        unrelated = run.execplan_entry_check("STRANGER", wt,
                                             [os.path.join(wt, "docs", "notes.md")],
                                             base=d)
        assert unrelated["block"] is False, unrelated


def test_a4_empty_write_targets_falls_back_to_whole_checkout():
    # A4 guard rail: empty MUST mean "whole checkout", never "nothing". A fail-open
    # default on a containment test is the failure mode that produced G6.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER", walker="WALKER", wt=wt, targets=[])
        dec = run.execplan_entry_check("STRANGER", wt,
                                       [os.path.join(wt, "anything", "at", "all.py")],
                                       base=d)
        assert dec["block"] is True
        assert dec["scoped_to_write_targets"] is False


def test_a4_symlink_spelled_target_intersects_a_real_spelled_write_target():
    # A4 guard rail: the intersection MUST reuse A1's resolved-path comparison. A
    # private string compare here would reopen G6 one layer up — a symlink-spelled
    # edit would miss every declared target and sail through unblocked. This is what
    # proves C8 is INHERITED at A4's predicate, not merely guaranteed at _path_inside.
    with tempfile.TemporaryDirectory() as d:
        real = os.path.join(d, "wt")
        os.makedirs(os.path.join(real, "src"))
        alias = os.path.join(d, "alias")
        os.symlink(real, alias)
        _arm_phase(d, owner="OWNER", walker="WALKER", wt=real, targets=["src/gate.py"])
        dec = run.execplan_entry_check("STRANGER", real,
                                       [os.path.join(alias, "src", "gate.py")], base=d)
        assert dec["block"] is True, dec


def test_a4_checkout_refreshes_write_targets_as_the_slice_advances():
    # A4(c) — the subtlest failure the plan names: without a refresh at checkout the
    # block keeps the ARM-TIME record and scopes to slice 1's files for the whole
    # walk, while later slices edit elsewhere entirely. That is narrower than the
    # truth AND disjoint from it — worse than no scoping at all.
    register = (
        "### Slice register\n\n"
        "| Slice | Type | Goal | Depends on | Model | Write targets |\n"
        "|---|---|---|---|---|---|\n"
        "| S1 | work | first | — | routine | src/one.py |\n"
        "| S2 | work | second | S1 | routine | src/two.py |\n"
        "| S3 | implementation_verification | verify | S1, S2 | routine | — |\n")
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        os.makedirs(wt)
        spine = os.path.join(wt, "topic_PLAN.md")
        with open(spine, "w", encoding="utf-8") as fh:
            fh.write(register)
        state = os.path.join(d, "topic.run-state.json")
        _write_json(state, {})
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        _cli_env("set-active-run",
                 {"owner_session_id": "OWNER", "surface_path": spine,
                  "worktree_root": wt, "state_path": state, "total_slices": 3,
                  "execution_pending": True, "walking_session_id": "WALKER",
                  "pending_handoff": {"type": "continue-the-run",
                                      "dispatch": "attended", "slice_id": "S1",
                                      "write_targets": ["src/one.py"]}}, env)
        # walk to S1, then complete it and walk to S2
        _cli_env("checkout-slice", {"surface_path": spine, "worktree_root": wt,
                                    "slice_id": "S1"}, env)
        _write_json(state, {"S1": {"status": "completed"}})
        _, out2 = _cli_env("checkout-slice", {"surface_path": spine,
                                              "worktree_root": wt,
                                              "slice_id": "S2"}, env)
        assert out2["ok"] is True and out2["current_slice_id"] == "S2"
        # the block must now scope to S2's file, NOT S1's
        two = run.execplan_entry_check("STRANGER", wt,
                                       [os.path.join(wt, "src", "two.py")], base=d)
        one = run.execplan_entry_check("STRANGER", wt,
                                       [os.path.join(wt, "src", "one.py")], base=d)
        assert two["block"] is True, f"S2's declared target must block: {two}"
        assert one["block"] is False, f"S1's target must be free once S2 is walked: {one}"


def test_a4_write_targets_are_stored_resolved_absolute():
    # A4(b): stored resolved at WRITE time against the pointer's worktree_root.
    # _path_inside resolves a relative path against the EVALUATING process's cwd, so
    # a repo-relative target compared from a session running in a subdirectory would
    # silently miss and fail open.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        out = _arm_phase(d, owner="OWNER", walker="WALKER", wt=wt,
                         targets=["src/gate.py"])
        stored = out["pending_handoff"]["write_targets"]
        assert stored == [os.path.join(wt, "src", "gate.py")], stored
        assert os.path.isabs(stored[0])


def test_a4_unanchorable_relative_declaration_falls_back_to_whole_checkout():
    # A4 regression, found by an adversarial checker: with no root to anchor against,
    # a relative declaration used to resolve against the EVALUATING process's cwd.
    # That bakes a wrong absolute path into the pointer, which then never matches the
    # real target — the block fails OPEN for a file that WAS declared, which is
    # strictly worse than not narrowing at all. It must degrade to whole checkout.
    assert run.resolve_write_targets(["src/gate.py"], None) == []
    assert run.resolve_write_targets(["src/gate.py"], "") == []
    # absolute entries need no anchor and survive
    assert run.resolve_write_targets(["/abs/gate.py"], None) == ["/abs/gate.py"]
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        # pointer with NO worktree_root, relative declaration → whole-checkout block
        _arm_phase(d, owner="OWNER", walker="WALKER", wt=None,
                   targets=["src/gate.py"])
        dec = run.execplan_entry_check("STRANGER", wt,
                                       [os.path.join(wt, "unrelated.py")], base=d)
        assert dec["block"] is True, f"must not narrow on an unanchorable list: {dec}"
        assert dec["scoped_to_write_targets"] is False


def test_a4_stale_handoff_for_another_slice_falls_back_to_whole_checkout():
    # A4 regression, found by an adversarial checker: nothing cross-checked
    # pending_handoff.slice_id against current_slice_id, so a handoff left over from
    # an earlier arm would scope the block to the WRONG slice's files — narrower than
    # the truth and disjoint from it, which A4 calls worse than no scoping at all.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        out = _arm_phase(d, owner="OWNER", walker="WALKER", wt=wt,
                         targets=["src/one.py"], slice_id="S1")
        # the walk has advanced to S2 but the handoff still describes S1
        doc = json.loads(open(out["pointer"], encoding="utf-8").read())
        doc["current_slice_id"] = "S2"
        _write_json(out["pointer"], doc)
        dec = run.execplan_entry_check("STRANGER", wt,
                                       [os.path.join(wt, "src", "two.py")], base=d)
        assert dec["block"] is True, f"a stale declaration must not narrow: {dec}"
        assert dec["scoped_to_write_targets"] is False
        # while an AGREEING handoff still narrows normally
        doc["current_slice_id"] = "S1"
        _write_json(out["pointer"], doc)
        agree = run.execplan_entry_check("STRANGER", wt,
                                         [os.path.join(wt, "src", "two.py")], base=d)
        assert agree["block"] is False, agree


def test_a4_supply_hop_the_canonical_7_column_register_yields_write_targets():
    # A4(a) — the hop that has been a functional no-op three times, each recurrence
    # one step further upstream. This asserts the SUPPLY end to end: a register
    # rendered with the exact 7-column header `/solution-design` Step 8 now pins,
    # carrying the per-slice field `/solution-slicer` now emits, must arrive at the
    # reader as populated write_targets. If either half of the authoring hop is not
    # extended, the column is unrendered and this resolves to () — the same no-op one
    # hop along, which is precisely what this test exists to catch.
    canonical = (
        "#### Slices\n\n"
        "| ID | Name | Type | Slicing idea | Model | Depends on | Write targets |\n"
        "|----|------|------|--------------|-------|------------|---------------|\n"
        "| S1 | First | implementation | walking_skeleton | routine | — | src/a.py, src/b.py |\n"
        "| S2 | Second | implementation | partial_step | more_capable | S1 | hooks/c.sh |\n"
        "| S-final | Implementation verification | implementation_verification |"
        " knowledge_vs_implementation | routine | S1, S2 | — |\n")
    slices = run.parse_slice_register(canonical)
    by_id = {s.id: s for s in slices}
    assert set(by_id) == {"S1", "S2", "S-final"}
    assert tuple(by_id["S1"].write_targets) == ("src/a.py", "src/b.py")
    assert tuple(by_id["S2"].write_targets) == ("hooks/c.sh",)
    # an em-dash cell is "declares nothing", not a literal path
    assert tuple(by_id["S-final"].write_targets) == ()
    # and the handoff the walk arms carries them through to the pointer
    rec = run.compute_handoff(slices, run.InMemoryBookkeepingAdapter())
    assert rec is not None and rec.slice_id == "S1"
    assert tuple(rec.write_targets) == ("src/a.py", "src/b.py"), rec.write_targets


def test_a3_block_surfaces_liveness_columns_for_the_operator():
    # A3: the operator gets walker identity and last-activity age at the moment they
    # are stopped, so the decision to wait or proceed rests on visible fact rather
    # than hand-reconstructed inference. Displayed only — nothing acts on it.
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_phase(d, owner="OWNER", walker="WALKER", wt=wt)
        dec = run.execplan_entry_check("STRANGER", wt, [os.path.join(wt, "x.py")],
                                       base=d)
        assert dec["block"] is True
        assert dec["walking_session_id"] == "WALKER"
        assert isinstance(dec["last_activity_age_seconds"], int)
        assert dec["last_activity_age_seconds"] >= 0
        assert dec["phase"] == "in-flight"


# --------------------------------------------------------------------------- #
# S3 — resume gate: armed runs listed not acknowledged, in-flight batched (A5, G3/G4)
# --------------------------------------------------------------------------- #


def test_a5_armed_only_backlog_costs_zero_acknowledgements_but_is_still_listed():
    # A5/G3, the diagnosed cost: the gate charged ONE operator question per parked
    # pointer. A run nobody is walking now costs NO decision — and is still SHOWN,
    # because dropping the listing would trade one Guiding Policy commitment
    # (stop charging for work nobody is doing) for another (never remove a surface
    # on which a parked run is shown to the operator).
    with tempfile.TemporaryDirectory() as d:
        for i in range(3):
            _arm_run(d, owner=f"OWNER{i}", surface=f"/p/parked{i}_THOUGHT.md",
                     walker=None,
                     handoff={"type": "continue-the-run", "dispatch": "attended",
                              "slice_id": f"S{i + 1}"})
        dec = run.gate_check("FRESH", base=d)
        assert dec["block"] is False, "parked runs must not charge an acknowledgement"
        assert dec["armed_count"] == 3
        assert len(dec["listed"]) == 3
        slugs = {r["slug"] for r in dec["listed"]}
        assert slugs == {"parked0", "parked1", "parked2"}, slugs
        # and no pending marker was written, so nothing is waiting to be acked
        assert not os.path.exists(os.path.join(d, "pending-FRESH.json"))


def test_a5_a_genuinely_in_flight_run_still_gates():
    # A5: the gate is narrowed, not removed. A run someone is actually walking
    # still stops a resuming session.
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, owner="OWNER", surface="/p/live_THOUGHT.md", walker="WALKER",
                 handoff={"type": "continue-the-run", "dispatch": "attended",
                          "slice_id": "S4"})
        dec = run.gate_check("FRESH", base=d)
        assert dec["block"] is True
        assert dec["slug"] == "live" and dec["slice_id"] == "S4"


def test_a5_three_in_flight_runs_arrive_in_one_block_and_one_ack_clears_all():
    # A5/G3: one prompt lists every gating run with its liveness columns, and ONE
    # acknowledgement clears all of them — not one question per pointer.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        for i in range(3):
            _arm_run(d, owner=f"OWNER{i}", surface=f"/p/live{i}_THOUGHT.md",
                     walker=f"WALKER{i}",
                     handoff={"type": "continue-the-run", "dispatch": "attended",
                              "slice_id": f"S{i + 1}"})
        dec = run.gate_check("FRESH", base=d)
        assert dec["block"] is True
        assert dec["remaining"] == 3 and len(dec["run_ids"]) == 3
        # ONE message naming all three, each with walker + age
        for i in range(3):
            assert f"live{i}" in dec["message"]
            assert f"WALKER{i}" in dec["message"]
        assert "last activity:" in dec["message"]
        # ONE acknowledgement clears all three
        _, ack = _cli_env("execplan-ack", {"session_id": "FRESH"}, env)
        assert len(ack["acked_run_ids"]) == 3
        assert run.gate_check("FRESH", base=d)["block"] is False


def test_a5_legacy_scalar_pending_marker_still_acks():
    # A5 guard rail: keep the scalar run_id in the pending marker so a half-applied
    # upgrade still acks. A marker written by a pre-A5 gate carries only `run_id`.
    with tempfile.TemporaryDirectory() as d:
        env = {"EXECPLAN_ACK_STATE_DIR": d}
        out = _arm_run(d, owner="OWNER", surface="/p/legacy_THOUGHT.md",
                       walker="WALKER")
        _write_json(os.path.join(d, "pending-FRESH.json"),
                    {"session_id": "FRESH", "pending": True,
                     "run_id": out["run_id"], "slug": "legacy",
                     "slice_id": "S1", "dispatch": "attended"})
        _, ack = _cli_env("execplan-ack", {"session_id": "FRESH"}, env)
        assert ack["acked"] == out["run_id"]
        assert run.gate_check("FRESH", base=d)["block"] is False


def test_a5_parked_runs_are_listed_alongside_a_gating_one():
    # A5: a mixed backlog gates on the in-flight run only, but the parked ones are
    # still surfaced in the same single prompt.
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, owner="O1", surface="/p/walking_THOUGHT.md", walker="W1",
                 handoff={"type": "continue-the-run", "dispatch": "attended",
                          "slice_id": "S2"})
        _arm_run(d, owner="O2", surface="/p/parked_THOUGHT.md", walker=None,
                 handoff={"type": "continue-the-run", "dispatch": "attended",
                          "slice_id": "S7"})
        dec = run.gate_check("FRESH", base=d)
        assert dec["block"] is True
        assert dec["remaining"] == 1 and dec["armed_count"] == 1
        assert "walking" in dec["message"]
        assert "parked" in dec["message"]
        assert "do not need a decision" in dec["message"]


def test_a5_legacy_pointer_without_walker_field_still_gates():
    # A5 regression, found by an adversarial checker: a pointer written BEFORE the
    # walk-state fields existed has no walking_session_id key. Reading that as
    # "nobody is walking" would silently stop gating a run that always blocked under
    # the pre-A5 contract. Absence is unknown, not empty — it fails CLOSED.
    with tempfile.TemporaryDirectory() as d:
        legacy = {"run_id": "legacy00run1", "owner_session_id": "OTHER",
                  "surface_path": "/p/legacy_THOUGHT.md", "total_slices": 2,
                  "created_at": run._now_iso(), "updated_at": run._now_iso()}
        assert "walking_session_id" not in legacy
        _write_json(os.path.join(d, "run-legacy00run1.json"), legacy)
        dec = run.gate_check("FRESH", base=d)
        assert dec["block"] is True, f"a legacy pointer must not silently stop gating: {dec}"
        # an explicit null walker on a CURRENT pointer is still the armed case
        _arm_run(d, owner="O2", surface="/p/modern_THOUGHT.md", walker=None)
        assert run.gate_check("FRESH2", base=d)["armed_count"] == 1


def test_a5_parked_listing_carries_last_activity_age():
    # G4: the parked listing must give the operator a basis to judge liveness. The
    # blocking inventory renders an age; so must the non-blocking hook listing, which
    # is the ONLY surface an armed-only backlog reaches.
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, owner="OWNER", surface="/p/parked_THOUGHT.md", walker=None)
        dec = run.gate_check("FRESH", base=d)
        assert dec["block"] is False
        assert dec["listed"][0]["age_seconds"] is not None
        # and the shared renderer shows it for the armed rows too
        msg = run.render_resume_inventory(
            [{"run_id": "r1", "slug": "live", "slice_id": "S1",
              "dispatch": "attended", "walking_session_id": "W",
              "age_seconds": 120}],
            dec["listed"])
        assert "last activity:" in msg
        assert msg.count("last activity:") >= 2, msg


def test_a5_hook_lists_parked_runs_with_age_and_does_not_block():
    # End to end through the real hook script: an armed-only backlog exits 0 (no
    # acknowledgement charged) yet still prints every parked run WITH its age. This
    # is the only surface that case reaches, so the jq rendering is load-bearing.
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, owner="O1", surface="/p/alpha_THOUGHT.md", walker=None,
                 handoff={"type": "continue-the-run", "dispatch": "attended",
                          "slice_id": "S2"})
        _arm_run(d, owner="O2", surface="/p/beta_THOUGHT.md", walker=None,
                 handoff={"type": "continue-the-run", "dispatch": "attended",
                          "slice_id": "S5"})
        code, _, err = _run_local_hook(
            "check-execplan-session-ack.sh", _skill_payload("FRESH"), d)
        assert code == 0, f"parked runs must not block: {err}"
        assert "2 parked run(s)" in err
        assert "alpha" in err and "beta" in err
        assert "last activity:" in err
        assert "ago" in err or "unknown" in err


def test_a5_liveness_is_displayed_and_never_acted_upon():
    # Guiding Policy: the system may SHOW that a run looks dormant; it must never
    # disarm one on that inference. A very old in-flight run inside the TTL still
    # gates, and its pointer is left untouched.
    with tempfile.TemporaryDirectory() as d:
        out = _arm_run(d, owner="OWNER", surface="/p/old_THOUGHT.md", walker="WALKER")
        before = open(out["pointer"], encoding="utf-8").read()
        dec = run.gate_check("FRESH", base=d, now=time.time() + 23 * 3600)
        assert dec["block"] is True, "a dormant-LOOKING run must still gate"
        assert dec["in_flight"][0]["age_seconds"] >= 23 * 3600 - 5
        assert open(out["pointer"], encoding="utf-8").read() == before


# --------------------------------------------------------------------------- #
# S4 — hygiene: suppression GC + widened SessionStart scan (A6, gaps G4/G7/G8)
# --------------------------------------------------------------------------- #


def test_a6_stale_suppression_files_are_reaped_and_fresh_ones_kept():
    # A6/G7: execplan-gc.sh reaped pending-, acked- and *.marker files but NOT
    # entry-suppress-*, so path exemptions accumulated permanently — 132 of them
    # across 32 sessions by the time this was diagnosed. They now age out on the
    # same TTL. Ageing a suppression out only ever RE-ARMS the gate for that path,
    # so an over-eager reap costs one extra prompt, never a missed block.
    with tempfile.TemporaryDirectory() as d:
        stale = os.path.join(d, "entry-suppress-OLDSESSION.json")
        fresh = os.path.join(d, "entry-suppress-NEWSESSION.json")
        _write_json(stale, {"session_id": "OLDSESSION", "suppressed_paths": ["/x"]})
        _write_json(fresh, {"session_id": "NEWSESSION", "suppressed_paths": ["/y"]})
        old = time.time() - 40 * 3600            # older than the 24h TTL
        os.utime(stale, (old, old))
        env = dict(os.environ)
        env["EXECPLAN_ACK_STATE_DIR"] = d
        env["CLAUDE_CODE_REMOTE"] = "false"
        subprocess.run(["bash", os.path.join(_LOCAL_HOOKS_DIR, "execplan-gc.sh")],
                       capture_output=True, text=True, env=env)
        assert not os.path.exists(stale), "a stale suppression file must age out"
        assert os.path.exists(fresh), "a fresh suppression file must survive"


def test_a6_scan_lists_an_armed_run_that_carries_no_handoff():
    # A6/G4: the SessionStart scan required a pending_handoff, so a pointer armed at
    # /plan Step 11 — no walker, no handoff yet — was surfaced NOWHERE until it
    # blocked somebody. That is the shape every freshly-approved plan starts in.
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, owner="OWNER", surface="/p/freshly-approved_THOUGHT.md",
                 walker=None)                     # armed, NO pending_handoff
        env = dict(os.environ)
        env["EXECPLAN_ACK_STATE_DIR"] = d
        env["CLAUDE_CODE_REMOTE"] = "false"
        proc = subprocess.run(
            ["bash", os.path.join(_LOCAL_HOOKS_DIR, "execplan-scan-pending.sh")],
            capture_output=True, text=True, env=env)
        assert proc.returncode == 0
        assert "freshly-approved" in proc.stdout, proc.stdout
        assert "not yet handed off" in proc.stdout


def test_a6_scan_still_lists_a_handed_off_run_and_skips_a_walked_one():
    # The widening must not lose the case the scan already covered, and must not
    # start advertising a run someone is actively walking as "parked".
    with tempfile.TemporaryDirectory() as d:
        _arm_run(d, owner="O1", surface="/p/handed-off_THOUGHT.md", walker=None,
                 handoff={"type": "plan-this-slice", "dispatch": "attended",
                          "slice_id": "S6"})
        _arm_run(d, owner="O2", surface="/p/being-walked_THOUGHT.md",
                 walker="SOMEONE")
        env = dict(os.environ)
        env["EXECPLAN_ACK_STATE_DIR"] = d
        env["CLAUDE_CODE_REMOTE"] = "false"
        proc = subprocess.run(
            ["bash", os.path.join(_LOCAL_HOOKS_DIR, "execplan-scan-pending.sh")],
            capture_output=True, text=True, env=env)
        assert "handed-off" in proc.stdout and "S6" in proc.stdout
        assert "being-walked" not in proc.stdout


def test_a6_scan_skips_a_legacy_pointer_missing_the_walker_field():
    # Consistency with the resume gate: a pointer with no walking_session_id KEY is
    # treated there as in-flight, not parked, so the scan must not advertise it as
    # parked either. Two surfaces describing one pointer must not disagree.
    with tempfile.TemporaryDirectory() as d:
        _write_json(os.path.join(d, "run-legacy00run1.json"),
                    {"run_id": "legacy00run1", "owner_session_id": "OTHER",
                     "surface_path": "/p/legacy_THOUGHT.md", "total_slices": 2,
                     "execution_pending": True})
        env = dict(os.environ)
        env["EXECPLAN_ACK_STATE_DIR"] = d
        env["CLAUDE_CODE_REMOTE"] = "false"
        proc = subprocess.run(
            ["bash", os.path.join(_LOCAL_HOOKS_DIR, "execplan-scan-pending.sh")],
            capture_output=True, text=True, env=env)
        assert "legacy" not in proc.stdout, proc.stdout


def test_a6_redirect_narrowing_allows_version_specs_and_keeps_real_redirects():
    # A6/G8 at the domain level: the `=`-prefix guard removes the phantom `=1`
    # target from ordinary shell text while every real redirect shape still resolves.
    assert run.extract_bash_write_targets('pytest "pkg>=1"', "/wt")["targets"] == []
    assert run.extract_bash_write_targets("pip install 'x>=2.0'", "/wt")["targets"] == []
    assert run.extract_bash_write_targets("echo x > /wt/f", "/wt")["targets"] == ["/wt/f"]
    assert run.extract_bash_write_targets("echo x >> /wt/f", "/wt")["targets"] == ["/wt/f"]
    assert run.extract_bash_write_targets("echo x > out.py", "/wt")["targets"] == ["/wt/out.py"]
    # fd-redirects stay excluded, as before
    assert run.extract_bash_write_targets("echo x 2> /wt/err", "/wt")["targets"] == []


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


def _run_all():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = []
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failures.append((t.__name__, e))
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())

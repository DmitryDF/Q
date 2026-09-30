"""Tests for bookkeeping_invariant — all 11 G-cases + slug grammar.

Cockburn 4-step nano-increment:
  step 1 (test-to-test): pure classify() + evaluate_family() on in-memory Family
  step 4 (real-to-real): real Thoughts/ tmp tree via read_family + the real hook
                         subprocess (stdin JSON -> exit code).
Never touches live ~/.claude — every fs test uses tmp_path.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import bookkeeping_invariant as bi  # noqa: E402

MODULE = os.path.expanduser("${KIT_HOOKS_DIR}/bookkeeping_invariant.py")


def fam(slug, files, collisions=None):
    members, contents = [], {}
    for name, text in files.items():
        members.append(bi.classify(name))
        contents[name] = text
    return bi.Family(slug=slug, members=members, contents=contents,
                     icloud_collisions=collisions or [])


def cases(violations):
    return {v.case for v in violations}


# ── slug grammar (classify) ──────────────────────────────────────────────
@pytest.mark.parametrize("name,bucket,type_,scope", [
    ("demo_THOUGHT.md", bi.B_STANDARD, "THOUGHT", None),
    ("demo-20260101000000_PLAN.md", bi.B_STANDARD, "PLAN", None),     # timestamped
    ("demo_THOUGHT_check.md", bi.B_STANDARD, "THOUGHT_check", None),  # multi-word TYPE
    ("demo_K_PLAN.md", bi.B_MULTI, "PLAN", "K"),                      # scope-keyed
    ("demo_H_v2_B1_PLAN.md", bi.B_MULTI, "PLAN", "H_v2_B1"),          # scope w/ underscores
    ("demo_CASES.md", bi.B_ADVISORY, "CASES", None),                 # advisory user
    ("demo_R3.md", bi.B_ADVISORY, "R3", None),                       # fact-check ephemera
    ("demo-20260101000000_DOUBLECHECK_a1b2c3d4.md", bi.B_ADVISORY, "DOUBLECHECK", None),
    ("demo_AUDIT.md", bi.B_OUT_OF_SCOPE, "AUDIT", None),
    ("_factcheck-convergence.md", bi.B_OUT_OF_SCOPE, None, None),    # leading underscore
    ("notes.md", bi.B_OUT_OF_SCOPE, None, None),                     # bare-name
    ("script.py", bi.B_NON_MEMBER, None, None),                      # not .md
])
def test_classify(name, bucket, type_, scope):
    m = bi.classify(name)
    assert m.bucket == bucket
    if type_ is not None:
        assert m.type == type_
    if scope is not None:
        assert m.scope == scope


def test_classify_timestamp_grandfather():
    assert bi.classify("demo_PLAN.md").ts is None
    assert bi.classify("demo-20260101000000_PLAN.md").ts == "20260101000000"
    assert bi.classify("demo-20260101000000_PLAN.md").slug == "demo"


# ── the eleven G-cases (in-memory) ───────────────────────────────────────
def test_G0_non_member_skips():
    m = bi.classify("script.py")
    assert bi.evaluate_family(m, fam("demo", {})) == []


def test_GO_out_of_scope_skips():
    m = bi.classify("demo_AUDIT.md")
    assert bi.evaluate_family(m, fam("demo", {"demo_AUDIT.md": ""})) == []


def test_G1_clean_passes():
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "Parent: [[demo_THOUGHT]]\n",
    })
    assert bi.evaluate_family(bi.classify("demo_THOUGHT.md"), f) == []
    assert bi.evaluate_family(bi.classify("demo_PLAN.md"), f) == []


def test_G2_mode_c_passes():
    f = fam("ninja", {"ninja_PLAN.md": "---\nbookkeeping: mode-c\n---\n# plan\n"})
    assert bi.evaluate_family(bi.classify("ninja_PLAN.md"), f) == []


def test_G3_missing_parent_line():
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "no parent here\n",
    })
    v = bi.evaluate_family(bi.classify("demo_PLAN.md"), f)
    assert "G3" in cases(v)
    assert all(x.blocking for x in v if x.case == "G3")
    assert "Parent: [[demo_THOUGHT]]" in next(x for x in v if x.case == "G3").fix


def test_G4_spine_missing_wikilink():
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n(no link)\n",
        "demo_PLAN.md": "Parent: [[demo_THOUGHT]]\n",
    })
    v = bi.evaluate_family(bi.classify("demo_THOUGHT.md"), f)
    assert "G4" in cases(v)
    assert "- [[demo_PLAN]]" in next(x for x in v if x.case == "G4").fix


def test_G5_parent_resolves_outside_family():
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "Parent: [[some-other-topic_THOUGHT]]\n",
    })
    v = bi.evaluate_family(bi.classify("demo_PLAN.md"), f)
    assert "G5" in cases(v)


def test_G7_parent_wrong_type():
    # PLAN points its Parent at the RESEARCH file instead of the spine.
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n# Discovery\n- [[demo_RESEARCH]]\n",
        "demo_RESEARCH.md": "Parent: [[demo_THOUGHT]]\n",
        "demo_PLAN.md": "Parent: [[demo_RESEARCH]]\n",
    })
    v = bi.evaluate_family(bi.classify("demo_PLAN.md"), f)
    assert "G7" in cases(v)


def test_G6_dropped_link_needs_snapshot():
    # Spine no longer links the DESIGN child, which still exists.
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n# Solution Design\n",
        "demo_DESIGN.md": "Parent: [[demo_THOUGHT]]\n",
        "demo_PLAN.md": "Parent: [[demo_DESIGN]]\n",
    })
    touched = bi.classify("demo_THOUGHT.md")
    # Without a snapshot: G6 is not raised (only G4 catches the missing link).
    without = cases(bi.evaluate_family(touched, f, pre_snapshot=None))
    assert "G6" not in without
    # With the historical snapshot: G6 fires.
    with_snap = cases(bi.evaluate_family(touched, f, pre_snapshot={"demo_DESIGN"}))
    assert "G6" in with_snap


def test_GA_advisory_info_only():
    f = fam("demo", {
        "demo_THOUGHT.md": "# Discovery\n",
        "demo_DOUBLECHECK_a1b2c3d4.md": "draft\n",
    })
    assert bi.evaluate_family(bi.classify("demo_DOUBLECHECK_a1b2c3d4.md"), f) == []


def test_GLC_icloud_collision_warns():
    f = fam("demo", {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "Parent: [[demo_THOUGHT]]\n",
    }, collisions=["demo_THOUGHT 2.md"])
    v = bi.evaluate_family(bi.classify("demo_THOUGHT.md"), f)
    glc = [x for x in v if x.case == "GLC"]
    assert glc and not glc[0].blocking


def test_retired_family_exempt_from_G3():
    f = fam("demo", {
        "demo_THOUGHT.md": "**Status:** Retired 2026-06-23\n# Implementation Details\n",
        "demo_PLAN.md": "no parent\n",   # would be G3, but family is retired
    })
    assert "G3" not in cases(bi.evaluate_family(bi.classify("demo_PLAN.md"), f))


def test_root_plan_no_parent_required():
    # Lone bare-name PLAN, no spine/design -> TODO-owned/root plan; no Parent demanded.
    f = fam("solo", {"solo_PLAN.md": "just a plan, no parent\n"})
    assert bi.evaluate_family(bi.classify("solo_PLAN.md"), f) == []


# ── step 4: real fs + real hook subprocess ───────────────────────────────
def _write_tree(tmp_path, files):
    tdir = tmp_path / "Thoughts"
    tdir.mkdir()
    for name, text in files.items():
        (tdir / name).write_text(text, encoding="utf-8")
    return tdir


def test_real_read_family(tmp_path):
    tdir = _write_tree(tmp_path, {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "Parent: [[demo_THOUGHT]]\n",
        "demo_AUDIT.md": "x\n",
        "demo_THOUGHT 2.md": "icloud dup\n",
    })
    f = bi.read_family(str(tdir), "demo")
    names = {m.filename for m in f.members}
    assert names == {"demo_THOUGHT.md", "demo_PLAN.md"}   # AUDIT excluded, dup not a member
    assert f.icloud_collisions == ["demo_THOUGHT 2.md"]
    # The dup is a real GLC warn (non-blocking); contract is otherwise clean.
    v = bi.evaluate_family(bi.classify("demo_PLAN.md"), f)
    assert cases(v) == {"GLC"} and not any(x.blocking for x in v)


def _run_hook(tmp_path, file_path):
    payload = {"tool_name": "Write", "session_id": "test-sess",
               "tool_input": {"file_path": str(file_path)}}
    env = dict(os.environ, HOME=str(tmp_path))   # isolate snapshot dir from live config
    return subprocess.run([sys.executable, MODULE], input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def test_real_hook_clean_exit0(tmp_path):
    tdir = _write_tree(tmp_path, {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "Parent: [[demo_THOUGHT]]\n",
    })
    r = _run_hook(tmp_path, tdir / "demo_PLAN.md")
    assert r.returncode == 0, r.stderr


def test_real_hook_violation_exit2_with_fix(tmp_path):
    tdir = _write_tree(tmp_path, {
        "demo_THOUGHT.md": "# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_PLAN.md": "no parent line\n",
    })
    r = _run_hook(tmp_path, tdir / "demo_PLAN.md")
    assert r.returncode == 2
    assert "G3" in r.stderr
    assert "Parent: [[demo_THOUGHT]]" in r.stderr


def test_real_hook_non_thoughts_path_exit0(tmp_path):
    other = tmp_path / "somefile.py"
    other.write_text("x\n")
    r = _run_hook(tmp_path, other)
    assert r.returncode == 0

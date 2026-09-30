"""DS4 tests — advisory grammar extension (_META_<date>) + G6 Pre->Post snapshot pair."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import bookkeeping_invariant as bi  # noqa: E402

MODULE = os.path.expanduser("${KIT_HOOKS_DIR}/bookkeeping_invariant.py")


# ── grammar extension (B3 Row 8) ─────────────────────────────────────────
def test_meta_iso_date_is_advisory():
    m = bi.classify("project-tracking-staleness_META_2026-06-16.md")
    assert m.bucket == bi.B_ADVISORY
    assert m.type == "META"
    assert m.sid == "2026-06-16"


def test_doublecheck_hex_sid_still_advisory():
    m = bi.classify("demo-20260101000000_DOUBLECHECK_a1b2c3d4.md")
    assert m.bucket == bi.B_ADVISORY and m.type == "DOUBLECHECK"


# ── G6 snapshot pair (unit) ──────────────────────────────────────────────
def _tree(tmp_path, files):
    tdir = tmp_path / "Thoughts"
    tdir.mkdir()
    for name, text in files.items():
        (tdir / name).write_text(text, encoding="utf-8")
    return tdir


def test_write_then_read_snapshot(tmp_path, monkeypatch):
    tdir = _tree(tmp_path, {
        "demo_THOUGHT.md": "# Solution Design\n- [[demo_DESIGN]]\n# Implementation Details\n- [[demo_PLAN]]\n",
        "demo_DESIGN.md": "Parent: [[demo_THOUGHT]]\n",
        "demo_PLAN.md": "Parent: [[demo_DESIGN]]\n",
    })
    monkeypatch.setattr(bi, "SNAPSHOT_DIR", str(tmp_path / "snap"))
    payload = {"tool_name": "Write", "session_id": "sx",
               "tool_input": {"file_path": str(tdir / "demo_THOUGHT.md")}}
    assert bi.write_snapshot(payload) == 0
    snap = bi.read_pre_snapshot("sx", "demo")
    assert snap == {"demo_DESIGN", "demo_PLAN"}


# ── G6 across a real Pre -> Post subprocess pair ─────────────────────────
def test_g6_detected_across_pre_post(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    work = tmp_path / "work"; work.mkdir()
    tdir = work / "Thoughts"; tdir.mkdir()
    (tdir / "demo_THOUGHT.md").write_text(
        "# Solution Design\n- [[demo_DESIGN]]\n# Implementation Details\n- [[demo_PLAN]]\n")
    (tdir / "demo_DESIGN.md").write_text("Parent: [[demo_THOUGHT]]\n")
    (tdir / "demo_PLAN.md").write_text("Parent: [[demo_DESIGN]]\n")
    env = dict(os.environ, HOME=str(home))
    payload = {"tool_name": "Write", "session_id": "sx",
               "tool_input": {"file_path": str(tdir / "demo_THOUGHT.md")}}

    # PreToolUse: capture the spine's current links.
    pre = subprocess.run([sys.executable, MODULE, "--snapshot"],
                         input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert pre.returncode == 0

    # The write drops the DESIGN link from the spine; DESIGN still exists.
    (tdir / "demo_THOUGHT.md").write_text(
        "# Solution Design\n# Implementation Details\n- [[demo_PLAN]]\n")

    # PostToolUse: G6 fires (order-dependent drop-link).
    post = subprocess.run([sys.executable, MODULE],
                          input=json.dumps(payload), capture_output=True, text=True, env=env)
    assert post.returncode == 2
    assert "G6" in post.stderr

#!/usr/bin/env python3
"""Tests for the S7/A9 build-time publish-scope check.

A9's validation gate: "the checker names all seven sites against the pre-fix
tree and is clean against the post-fix tree". Both halves are asserted here
against REAL trees — the pre-fix harness is rebuilt from config-source history at
a20e292 (the last promotion before glittery-humming-pine S1), the post-fix tree
is the config root this suite ships in. The unit cases below them pin each rule
so a regression names the rule rather than only a site.

Nothing is written outside a TemporaryDirectory.
"""
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOKS = HERE.parent
CONFIG_ROOT = HOOKS.parent
sys.path.insert(0, str(HOOKS))
import publish_scope_scan as pss  # noqa: E402

Q_CONFIG_SRC = Path.home() / ".<config-source-repo>"
PRE_FIX_COMMIT = "a20e292"


# --------------------------------------------------------------------------- #
# Pure core
# --------------------------------------------------------------------------- #

def test_classify_commit_forms():
    c = pss.classify_git_argv
    assert c(["git", "commit", "-m", "x"]) == "unscoped-commit"
    assert c(["git", "-C", "/r", "commit", "-m", "x"]) == "unscoped-commit"
    assert c(["git", "commit", "-a", "-m", "x"]) == "unscoped-commit"
    assert c(["git", "commit", "-m", "x", "--", "f"]) is None
    assert c(["git", "commit", "-m", "x", "--", "*"]) is None
    assert c(["git", "commit-tree", "HEAD^{tree}"]) is None
    assert c(["git", "add", "-A"]) == "whole-tree-add"
    assert c(["git", "add", "."]) == "whole-tree-add"
    assert c(["git", "add", "-u"]) == "whole-tree-add"
    assert c(["git", "add", "-A", "--", "f"]) is None
    assert c(["git", "add", "--", "f"]) is None
    assert c(["git", "status", "--short"]) is None


def test_shell_scoped_add_with_bare_commit_is_flagged():
    text = 'git -C "$R" add -- f\ngit -C "$R" commit -m "x"\n'
    rules = [r for _, r, _ in pss.scan_shell(text)]
    assert rules == ["unscoped-commit"], rules


def test_shell_chained_and_continued_statements():
    text = 'git add -A -- f && \\\n  git commit -m x -- f\n'
    assert list(pss.scan_shell(text)) == []
    text = 'x=1; git commit -m "$M" || die "nope"\n'
    assert [r for _, r, _ in pss.scan_shell(text)] == ["unscoped-commit"]


def test_shell_heredoc_bodies_and_comments_are_not_commands():
    text = ("cat >&2 <<EOF\n  ALLOW_OUT_OF_TREE=1 git commit ...\nEOF\n"
            "# git commit -m 'in a comment'\n"
            "printf '    ALLOW_UNSCOPED_COMMIT=1 git commit ...\\n'\n")
    assert list(pss.scan_shell(text)) == []
    # ...but a command AFTER the heredoc terminator is still scanned
    text += "git commit -m after\n"
    assert [r for _, r, _ in pss.scan_shell(text)] == ["unscoped-commit"]


def test_shell_command_substitution_is_scanned():
    for text in ('sha=$(git -C "$R" commit -m x && git rev-parse HEAD)\n',
                 'sha=`git commit -m x`\n',
                 'echo "$(printf %s "$(git commit -m nested)")"\n'):
        assert [r for _, r, _ in pss.scan_shell(text)] == ["unscoped-commit"], text
    ok = 'sha=$(git commit -m x -- f && git rev-parse HEAD)\n'
    assert list(pss.scan_shell(ok)) == []


def test_markdown_only_fenced_code_is_scanned():
    md = ("A bare `git commit` sweeps the index — prose, not a command.\n\n"
          "```bash\ngit -C ~/x commit -m \"v\"\n```\n")
    hits = list(pss.scan_shell(md, fenced_only=True))
    assert [r for _, r, _ in hits] == ["unscoped-commit"]
    assert hits[0][0] == 4


def test_python_call_forms():
    src = (
        "def a(self, s, specs, root, m, wt):\n"
        "    self._git('commit', '-m', s)\n"
        "    subprocess.run(['git', '-C', str(root), 'commit', '-m', m, '--', *specs])\n"
        "    self._git(wt, 'commit', '-m', 'frozen')\n"
        "    self._git('add', '-A')\n"
        "    self._git('add', '--', *specs)\n"
        "    subprocess.run(['echo', 'commit'])\n"
    )
    hits = [(n, r) for n, r, _ in pss.scan_python(src)]
    assert hits == [(2, "unscoped-commit"), (4, "unscoped-commit"), (5, "whole-tree-add")], hits


# --------------------------------------------------------------------------- #
# Tree-level rules on synthetic roots
# --------------------------------------------------------------------------- #

def _root(files: dict) -> tempfile.TemporaryDirectory:
    td = tempfile.TemporaryDirectory()
    for rel, text in files.items():
        p = Path(td.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return td


def test_capture_unbound_only_beside_an_unscoped_commit():
    bad = "#!/usr/bin/env bash\nconfig_source re-add\ngit -C \"$S\" add -A\ngit -C \"$S\" commit -m \"$M\"\n"
    good = "#!/usr/bin/env bash\nconfig_source re-add\ngit -C \"$S\" add -A -- a\ngit -C \"$S\" commit -m \"$M\" -- a\n"
    with _root({"bin/p": bad}) as d:
        rules = sorted(f["rule"] for f in pss.scan(Path(d))["findings"])
        assert rules == ["capture-unbound", "unscoped-commit", "whole-tree-add"], rules
    with _root({"bin/p": good}) as d:
        res = pss.scan(Path(d))
        assert res["ok"] and any(f["rule"] == "capture-unbound" for f in res["allowed"]), res
    # One scoped commit does not excuse the capture if ANOTHER commit in the same
    # file is unscoped — the pre-S3 claude-promote shape had both kinds of line.
    mixed = good + "git -C \"$S\" commit -m \"$M\"\n"
    with _root({"bin/p": mixed}) as d:
        rules = sorted(f["rule"] for f in pss.scan(Path(d))["findings"])
        assert rules == ["capture-unbound", "unscoped-commit"], rules


def test_prose_surface_without_publish_command_is_named():
    with _root({"skills/close/SKILL.md": "Stage session files, then commit them.\n"}) as d:
        res = pss.scan(Path(d))
        assert [(f["path"], f["rule"]) for f in res["findings"]] == \
            [("skills/close/SKILL.md", "prose-no-command")]
    with _root({"skills/close/SKILL.md":
                "```bash\npython3 ${KIT_HOOKS_DIR}/commit_scope.py publish -m \"m\" -- <paths>\n```\n"}) as d:
        assert pss.scan(Path(d))["ok"]


def test_agents_directory_is_scanned():
    agent = "---\nname: a\ntools: Bash\n---\n\n```bash\ngit -C \"$R\" commit -m \"x\"\n```\n"
    with _root({"agents/committer.md": agent}) as d:
        res = pss.scan(Path(d))
        assert [(f["path"], f["rule"]) for f in res["findings"]] == \
            [("agents/committer.md", "unscoped-commit")], res


def test_tests_fixtures_and_backups_are_excluded():
    bad = "#!/usr/bin/env bash\ngit commit -m x\n"
    with _root({"hooks/tests/t.sh": bad, "hooks/tests/fixtures/f.sh": bad,
                "hooks/x.sh.bak-20260101": bad, "skills/s/test_x.py": "x=1\n"}) as d:
        assert pss.scan(Path(d))["ok"]


def test_allowlist_requires_its_condition_and_a_reason():
    promote_old = "#!/usr/bin/env bash\ngit -C \"$S\" add -A || die\ngit -C \"$S\" commit -m \"$MSG\" || die\n"
    with _root({"bin/claude-promote": promote_old}) as d:
        rules = sorted(f["rule"] for f in pss.scan(Path(d))["findings"])
        # the SAME statements the live allowlist excuses — flagged, because the
        # override arm that justifies the exemption is not in this file
        assert rules == ["unscoped-commit", "whole-tree-add"], rules
    saved = pss.ALLOWLIST
    try:
        pss.ALLOWLIST = saved + (pss.Allow("bin/x", "unscoped-commit", "commit", "   "),)
        # A CLEAN tree: the reasonless entry is the ONLY thing that can fail the
        # scan. (The first version also planted an unscoped commit, so the scan
        # failed on that finding and the reason check was never what decided it —
        # a mutation that accepted reasonless entries still passed.)
        with _root({"bin/x": "#!/usr/bin/env bash\ngit commit -m x -- f\n"}) as d:
            res = pss.scan(Path(d))
            assert res["findings"] == [], res["findings"]
            assert not res["ok"] and res["allowlist_without_reason"], res
    finally:
        pss.ALLOWLIST = saved


# --------------------------------------------------------------------------- #
# A9's validation gate on REAL trees
# --------------------------------------------------------------------------- #

def _unmangle(part: str) -> str:
    for pre in ("executable_", "private_", "readonly_", "empty_", "symlink_"):
        while part.startswith(pre):
            part = part[len(pre):]
    if part.startswith("dot_"):
        part = "." + part[len("dot_"):]
    return part[:-5] if part.endswith(".tmpl") else part


def _render_pre_fix(dest: Path) -> None:
    arc = subprocess.run(["git", "-C", str(Q_CONFIG_SRC), "archive", PRE_FIX_COMMIT, "dot_claude"],
                         capture_output=True)
    assert arc.returncode == 0, f"cannot archive {PRE_FIX_COMMIT}: {arc.stderr.decode()[:200]}"
    with tarfile.open(fileobj=io.BytesIO(arc.stdout)) as tf:
        for m in tf.getmembers():
            if not m.isfile():
                continue
            parts = [_unmangle(p) for p in Path(m.name).parts[1:]]   # drop dot_claude
            if not parts or parts[0] not in pss.SCAN_DIRS:
                continue
            out = dest.joinpath(*parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(tf.extractfile(m).read())


# (path, rule) pairs that must appear — one or more per Diagnosis site.
SEVEN_SITES = {
    "close/SKILL.md:379-392 (prose, names no command)": ("skills/close/SKILL.md", "prose-no-command"),
    "ninja-fix/SKILL.md:254 (prose, names no command)": ("skills/ninja-fix/SKILL.md", "prose-no-command"),
    "execute-plan/run.py:1334 (add -A, bare commit)": ("skills/execute-plan/run.py", "whole-tree-add"),
    "execute-plan/run.py:1370-1376 (scoped add, bare commit)": ("skills/execute-plan/run.py", "unscoped-commit"),
    "claude-promote:174 (bare the capture step)": ("bin/claude-promote", "capture-unbound"),
    "claude-promote:223-229 (add -A, bare commit)": ("bin/claude-promote", "unscoped-commit"),
    "starter-kit/SKILL.md:157-158 (scoped add, bare commit)": ("skills/starter-kit/SKILL.md", "unscoped-commit"),
}


def test_pre_fix_tree_names_all_seven_sites():
    assert (Q_CONFIG_SRC / ".git").exists(), f"config source not found at {Q_CONFIG_SRC}"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _render_pre_fix(root)
        res = pss.scan(root)
        got = {(f["path"], f["rule"]) for f in res["findings"]}
        missing = [name for name, key in SEVEN_SITES.items() if key not in got]
        assert not missing, f"not named on the pre-fix tree: {missing}\nfindings: {sorted(got)}"
        # execute-plan carried TWO distinct unscoped commits (create_commit and
        # the detour) — both must be named, not one standing in for the other.
        ep = [f for f in res["findings"]
              if f["path"] == "skills/execute-plan/run.py" and f["rule"] == "unscoped-commit"]
        assert len(ep) >= 2, ep
        assert not res["ok"]


def test_post_fix_tree_is_clean():
    res = pss.scan(CONFIG_ROOT)
    assert res["ok"], pss.render(res)
    assert not res["stale_allowlist"], res["stale_allowlist"]


def test_wrapper_exit_codes():
    sh = HOOKS / "check-publish-scope.sh"
    ok = subprocess.run(["bash", str(sh), "--root", str(CONFIG_ROOT)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    with _root({"bin/p": "#!/usr/bin/env bash\ngit commit -m x\n"}) as d:
        bad = subprocess.run(["bash", str(sh), "--root", d], capture_output=True, text=True)
        assert bad.returncode == 1 and "unscoped-commit" in bad.stdout, bad.stdout
    usage = subprocess.run(["bash", str(sh), "--nope"], capture_output=True, text=True)
    assert usage.returncode == 2


# The managed scope plus `docs/`, which check_land_port_records reads — without
# it the CLEAN copy already fails claude-verify, and the regressed case would be
# vacuous (it would fail for a reason unrelated to publish scope).
MANAGED_SCOPE = ("agents", "bin", "CLAUDE.md", "docs", "hooks", "rules", "settings.json", "skills")


def test_claude_verify_pre_refuses_a_regressed_surface():
    """The wiring, not just the module: `claude-verify --phase pre` over a copy of
    this config root passes, and fails naming the site once one converted
    surface regresses to a bare commit. Every other check sees an identical tree,
    so the failure is attributable to the publish-scope block."""
    with tempfile.TemporaryDirectory() as d:
        copy = Path(d) / "cfg"
        copy.mkdir()
        for name in MANAGED_SCOPE:
            src = CONFIG_ROOT / name
            if src.exists():
                r = subprocess.run(["cp", "-c", "-R", str(src), str(copy / name)], capture_output=True)
                if r.returncode != 0:
                    subprocess.run(["cp", "-R", str(src), str(copy / name)], check=True)
        env = dict(os.environ, CLAUDE_VERIFY_TARGET=str(copy), CLAUDE_CONFIG_DIR=str(copy))
        verify = [ "bash", str(copy / "bin/claude-verify"), "--phase", "pre"]
        clean = subprocess.run(verify, capture_output=True, text=True, env=env)
        assert clean.returncode == 0, (clean.stdout + clean.stderr)[-1500:]

        run_py = copy / "skills/execute-plan/run.py"
        text = run_py.read_text()
        scoped = 'commit = self._git("commit", "-m", subject, "--", *specs)'
        assert text.count(scoped) == 1, "anchor moved — update this test"
        run_py.write_text(text.replace(scoped, 'commit = self._git("commit", "-m", subject)'))
        broken = subprocess.run(verify, capture_output=True, text=True, env=env)
        out = broken.stdout + broken.stderr
        assert broken.returncode == 1, out[-1500:]
        assert "publish scope check failed" in out and "skills/execute-plan/run.py" in out, out[-1500:]


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - fails}/{len(tests)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run_all())

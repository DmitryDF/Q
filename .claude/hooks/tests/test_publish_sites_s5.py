#!/usr/bin/env python3
"""A7's validation gate for the two PROSE publish sites (glittery-humming-pine S5).

The gate, per site: "a two-session fixture with a foreign file staged, asserting
it is absent from the resulting commit."

A prose site has no function to call — the model composes the command from the
SKILL.md text each run. So this suite tests the TEXT: it extracts the publish
command from each SKILL.md, substitutes the placeholders exactly as a run would,
and executes it against a throwaway repo under $TMPDIR in which a concurrent
session has already STAGED a foreign file. If the prose regresses to a bare
commit (or to an unscoped add), the extracted command changes and this suite
goes red. (The execute-plan site is code and is covered in its own suite,
`skills/execute-plan/test_run.py`, `test_a7_*`.)

Nothing touches a live repo.
"""
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
NINJA = HOME / "skills/ninja-fix/SKILL.md"
KIT = HOME / "skills/starter-kit/SKILL.md"
CS_PY = HOME / "hooks/commit_scope.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def make_repo(d):
    repo = Path(d) / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(repo, "config", k, v)
    (repo / "seed.txt").write_text("seed\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "seed")
    # Session A's edit, and session B's file written AND STAGED in the shared index.
    (repo / "mine.txt").write_text("mine\n")
    (repo / "foreign.txt").write_text("foreign\n")
    git(repo, "add", "--", "foreign.txt")
    assert "foreign.txt" in git(repo, "diff", "--cached", "--name-only").stdout
    # A second concurrent file B has written but NOT staged. A whole-tree add
    # (even followed by a scoped commit) would stage it into the shared index,
    # where the next bare commit anywhere would take it.
    (repo / "bystander.txt").write_text("B, unstaged\n")
    return repo


def make_enforced_worktree(d):
    """S6/A8 rehearsal fixture: a repo carrying the REAL enforcing scope gate and
    waiver trailer (installed by worktree-helper.sh from the config under test),
    and a linked topic worktree — where a /ninja-fix domain edit legitimately
    commits — holding the same two-session state as `make_repo`.

    Armed-ness is proven before use: a bare commit there must be refused."""
    base = Path(d) / "base"
    base.mkdir()
    git(base, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(base, "config", k, v)
    (base / "seed.txt").write_text("seed\n")
    git(base, "add", "-A")
    git(base, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "seed")
    inst = subprocess.run(["bash", str(HOME / "hooks/worktree-helper.sh"), "install", "--repo", str(base)],
                          capture_output=True, text=True)
    assert (base / ".git/hooks/pre-commit").exists() and (base / ".git/hooks/commit-msg").exists(), inst.stderr
    wt = Path(d) / "wt"
    git(base, "worktree", "add", "-q", "-b", "topic", str(wt))
    (wt / "mine.txt").write_text("mine\n")
    (wt / "foreign.txt").write_text("foreign\n")
    git(wt, "add", "--", "foreign.txt")
    (wt / "bystander.txt").write_text("B, unstaged\n")
    h0 = git(wt, "rev-parse", "HEAD").stdout
    probe = git(wt, "commit", "-qm", "bare probe")
    assert git(wt, "rev-parse", "HEAD").stdout == h0 and "scope BLOCKED" in probe.stderr, \
        f"gate not armed in the rehearsal worktree: {probe.stderr}"
    return wt


def committed(repo):
    return git(repo, "show", "--name-only", "--format=", "HEAD").stdout.splitlines()


def assert_foreign_untouched(repo, new_head, old_head):
    # A commit DID land (so an "absent" assertion cannot pass because nothing ran)…
    assert new_head != old_head, "no commit was made — the step under test never ran"
    # …and the foreign file is neither in it nor unstaged.
    assert "foreign.txt" not in committed(repo), committed(repo)
    assert "foreign.txt" in git(repo, "diff", "--cached", "--name-only").stdout
    # …and B's unstaged file was neither committed nor staged.
    assert "bystander.txt" not in committed(repo)
    assert "?? bystander.txt" in git(repo, "status", "--porcelain").stdout.splitlines()


def publish_line(skill_path):
    """The single `commit_scope.py publish` command line in a SKILL.md fence."""
    lines = [l.strip() for l in skill_path.read_text().splitlines()
             if "commit_scope.py publish" in l and l.strip().startswith("python3 ")]
    assert len(lines) == 1, f"{skill_path}: expected ONE publish command, got {lines}"
    return lines[0]


def run_line(line, subs):
    for placeholder, value in subs.items():
        assert placeholder in line, f"placeholder {placeholder!r} missing from: {line}"
        line = line.replace(placeholder, value)
    argv = shlex.split(line)
    assert argv[0] == "python3"
    argv[0] = sys.executable
    argv[1] = str(CS_PY) if argv[1].endswith("hooks/commit_scope.py") else argv[1]
    return subprocess.run(argv, capture_output=True, text=True)


# --------------------------------------------------------------------------- #
# /ninja-fix
# --------------------------------------------------------------------------- #

def _ninja(repo, edited):
    return run_line(publish_line(NINJA), {
        "<repo root containing the edited file>": str(repo),
        "<one-sentence rationale>": "fix the thing",
        "<the one file the Edit/Write call changed>": edited,
    })


def test_ninja_fix_publish_excludes_staged_foreign_file():
    with tempfile.TemporaryDirectory() as d:
        repo = make_repo(d)
        head = git(repo, "rev-parse", "HEAD").stdout
        p = _ninja(repo, "mine.txt")
        assert p.returncode == 0, p.stderr
        assert_foreign_untouched(repo, git(repo, "rev-parse", "HEAD").stdout, head)
        assert committed(repo) == ["mine.txt"]
        assert git(repo, "log", "-1", "--format=%s").stdout.strip() == "[ninja-fix] fix the thing"


def test_ninja_fix_publish_accepts_absolute_path_inside_repo():
    with tempfile.TemporaryDirectory() as d:
        repo = make_repo(d)
        head = git(repo, "rev-parse", "HEAD").stdout
        p = _ninja(repo, str(repo.resolve() / "mine.txt"))
        assert p.returncode == 0, p.stderr
        assert_foreign_untouched(repo, git(repo, "rev-parse", "HEAD").stdout, head)
        assert committed(repo) == ["mine.txt"]


def test_ninja_fix_publish_commits_through_the_enforcing_gate():
    """S6 rehearsal: the /ninja-fix command, as extracted from its SKILL.md,
    commits under the real enforcing gate — foreign staged file excluded, no
    waiver trailer on a declared commit."""
    with tempfile.TemporaryDirectory() as d:
        wt = make_enforced_worktree(d)
        head = git(wt, "rev-parse", "HEAD").stdout
        p = _ninja(wt, "mine.txt")
        assert p.returncode == 0, p.stderr
        assert_foreign_untouched(wt, git(wt, "rev-parse", "HEAD").stdout, head)
        assert committed(wt) == ["mine.txt"]
        assert "Unscoped-Publish:" not in git(wt, "log", "-1", "--format=%B").stdout


def test_ninja_fix_prose_names_no_bare_commit():
    text = NINJA.read_text()
    assert not re.search(r"^\s*git\b[^\n]*\bcommit\b", text, re.M), \
        "ninja-fix must not prescribe a hand-composed git commit"


# --------------------------------------------------------------------------- #
# /starter-kit step 6a
#
# S6 (A7b) rerouted this step: it no longer commits in the frozen ~/.claude repo
# at all, but promotes the confirmed LIVE paths through `claude-promote --paths`,
# whose scoping of both verbs is exercised end to end by
# `test_promote_scope_s3.sh` (case 3b: a STAGED foreign file survives). What is
# specific to THIS site, and therefore asserted here, is that the prose declares
# the confirmed list and never hands the model a git write against ~/.claude.
# --------------------------------------------------------------------------- #

def _kit_section_6a():
    text = KIT.read_text()
    start = text.index("**6a.")
    end = text.index("**6b.")
    return text[start:end]


def test_starter_kit_promotes_the_confirmed_list_through_claude_promote():
    sec = _kit_section_6a()
    lines = [l.strip() for l in sec.splitlines() if "claude-promote" in l
             and l.strip().startswith("~/.claude/bin/claude-promote")]
    assert len(lines) == 1, f"expected ONE claude-promote command in 6a, got {lines}"
    argv = shlex.split(lines[0])
    assert "--paths" in argv, argv
    declared = argv[argv.index("--paths") + 1]
    assert "confirmed" in declared, f"--paths must carry the confirmed list, got {declared!r}"
    assert "--session-scope" not in argv and "ALLOW_UNSCOPED_COMMIT" not in lines[0]


def test_starter_kit_never_writes_to_the_frozen_claude_repo():
    sec = _kit_section_6a()
    # `tag -l` (reading earlier release tags, which live in the frozen repo) is a
    # read; every other form of these verbs writes.
    for verb in ("commit", "add", "push", r"tag(?! -l)"):
        assert not re.search(rf"git -C ~/\.claude {verb}\b", sec), \
            f"6a still runs `git -C ~/.claude {verb}` against the frozen repo"
    assert not re.search(r"^\s*git\b[^\n]*\bcommit\b", sec, re.M), \
        "6a must not prescribe a hand-composed git commit"


def test_starter_kit_tag_lands_in_the_config-source_source():
    sec = _kit_section_6a()
    assert "git -C <config-source-repo> tag starter-kit-vN main" in sec
    assert "git -C <config-source-repo> push origin starter-kit-vN" in sec


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

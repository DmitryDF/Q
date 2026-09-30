#!/usr/bin/env python3
"""Regression tests for the defects an independent post-S8 audit found in the
declared-publish-scope work (glittery-humming-pine). None of them was visible to
the plan's own tests; each test here drives the real script or module in a
scratch repository and fails if its defect returns.

  D1  /close archived the file ledger (§2) before reading it (§3/§4).
  D2  code-layer writers filed a write in the ledger of the process cwd.
  D3  /ninja-fix on a ~/.claude file resolved `publish --repo` to the frozen repo.
  D4  path spelling (symlinked roots) and quoted porcelain names dropped entries.
  D5  a primary-checkout close mixing bookkeeping and domain files is refused.
  D6  the build-time scanner missed shell-string commands and project skill dirs.
  A5  the session-id equality the ledger's two halves rely on had no test.

Nothing is written outside TemporaryDirectory.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
CONFIG = HOOKS.parent
sys.path.insert(0, str(HOOKS))
import commit_scope  # noqa: E402
import publish_scope_scan as pss  # noqa: E402

READER = HOOKS / "session-scope.sh"
WRITER = HOOKS / "track-session-files.sh"


def git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)


def make_repo(parent: Path, name="repo") -> Path:
    r = parent / name
    (r / "Thoughts").mkdir(parents=True)
    (r / "Diary").mkdir()
    (r / "src").mkdir()
    git(r.parent, "init", "-q", str(r))
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("commit.gpgsign", "false")):
        git(r, "config", k, v)
    (r / "TODO.md").write_text("seed\n")
    git(r, "add", "-A"); git(r, "-c", "core.hooksPath=/dev/null", "commit", "-qm", "seed")
    (r / ".claude" / "logs").mkdir(parents=True)
    return r


def scope(sid, repo, *flags, cwd=None, env=None):
    e = dict(os.environ, CLAUDE_CONFIG_DIR=str(CONFIG), **(env or {}))
    r = subprocess.run(["bash", str(READER), sid, str(repo), *flags],
                       capture_output=True, text=True, cwd=cwd, env=e)
    return [l for l in r.stdout.splitlines() if l.strip()]


class D1_LedgerReadAfterArchive(unittest.TestCase):
    def test_publish_and_harness_scope_read_the_archived_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            plan = repo / "Thoughts" / "a_PLAN.md"; plan.write_text("A\n")
            hook = CONFIG / "hooks" / "todo.py"          # a managed-scope path
            logs = repo / ".claude" / "logs"
            led = logs / "_session_files-SID.log"
            led.write_text(f"{plan.resolve()}\n{hook}\n")
            before_pub, before_har = scope("SID", repo, "--publish-scope"), scope("SID", repo, "--harness-scope")
            self.assertIn("Thoughts/a_PLAN.md", before_pub)          # precondition
            self.assertIn(str(hook), before_har)
            (logs / "_processed").mkdir()
            led.rename(logs / "_processed" / led.name)              # /close §2
            self.assertFalse(led.exists())
            self.assertEqual(before_pub, scope("SID", repo, "--publish-scope"),
                             "after §2's archive, §3's declaration lost the session's own files")
            self.assertEqual(before_har, scope("SID", repo, "--harness-scope"),
                             "after §2's archive, §4 would promote nothing")

    def test_live_and_archived_ledgers_are_both_read(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            a = repo / "Thoughts" / "a.md"; a.write_text("a\n")
            b = repo / "Thoughts" / "b.md"; b.write_text("b\n")
            logs = repo / ".claude" / "logs"; (logs / "_processed").mkdir()
            (logs / "_processed" / "_session_files-SID.log").write_text(f"{a.resolve()}\n")
            (logs / "_session_files-SID.log").write_text(f"{b.resolve()}\n")
            self.assertEqual(["Thoughts/a.md", "Thoughts/b.md"], scope("SID", repo, "--publish-scope"))


class D2_LedgerFollowsTheWrittenFile(unittest.TestCase):
    def test_write_to_repo_x_from_a_cwd_in_repo_y_lands_in_x(self):
        with tempfile.TemporaryDirectory() as td:
            x = make_repo(Path(td), "x"); y = make_repo(Path(td), "y")
            target = x / "TODO.md"
            saved = os.getcwd()
            try:
                os.chdir(y)
                ok = commit_scope.record_write(target, session_id="SIDX")
            finally:
                os.chdir(saved)
            self.assertTrue(ok)
            self.assertTrue((x / ".claude/logs/_session_files-SIDX.log").is_file(),
                            "write to repo X was not filed in X's ledger")
            self.assertFalse((y / ".claude/logs/_session_files-SIDX.log").exists(),
                             "write to repo X was filed in the CWD repo's ledger")

    def test_write_from_a_cwd_outside_any_repo_still_lands(self):
        with tempfile.TemporaryDirectory() as td:
            x = make_repo(Path(td), "x")
            nowhere = Path(td) / "nowhere"; nowhere.mkdir()
            saved = os.getcwd()
            try:
                os.chdir(nowhere)
                ok = commit_scope.record_write(x / "Thoughts" / "new.md", session_id="SIDN")
            finally:
                os.chdir(saved)
            self.assertTrue(ok and (x / ".claude/logs/_session_files-SIDN.log").is_file())

    def test_a_write_inside_the_frozen_harness_uses_the_session_project_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            frozen = make_repo(Path(td), "harness"); project = make_repo(Path(td), "project")
            (frozen / "hooks").mkdir()
            target = frozen / "hooks" / "x.py"; target.write_text("x\n")
            os.environ["COMMIT_SCOPE_FROZEN_ROOT"] = str(frozen)
            saved = os.getcwd()
            try:
                os.chdir(project)
                commit_scope.record_write(target, session_id="SIDH")
            finally:
                os.chdir(saved)
                os.environ.pop("COMMIT_SCOPE_FROZEN_ROOT", None)
            self.assertTrue((project / ".claude/logs/_session_files-SIDH.log").is_file())
            self.assertFalse((frozen / ".claude/logs/_session_files-SIDH.log").exists(),
                             "a harness write created an unread ledger inside the harness repo")

    def test_a_write_from_a_linked_worktree_joins_the_canonical_ledger(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            wt = Path(td) / "wt"
            git(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
            commit_scope.record_write(wt / "TODO.md", session_id="SIDWT")
            self.assertTrue((repo / ".claude/logs/_session_files-SIDWT.log").is_file())

    def test_a_not_yet_existing_parent_dir_still_resolves(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            commit_scope.record_write(repo / "new" / "deep" / "file.md", session_id="SIDP")
            self.assertTrue((repo / ".claude/logs/_session_files-SIDP.log").is_file())

    def test_explicit_cwd_still_wins(self):
        with tempfile.TemporaryDirectory() as td:
            x = make_repo(Path(td), "x"); y = make_repo(Path(td), "y")
            commit_scope.record_write(x / "TODO.md", session_id="SIDC", cwd=y)
            self.assertTrue((y / ".claude/logs/_session_files-SIDC.log").is_file())


class D3_FrozenHarnessRepoIsRefused(unittest.TestCase):
    def test_publish_refuses_the_frozen_root(self):
        with tempfile.TemporaryDirectory() as td:
            frozen = make_repo(Path(td), "frozen")
            (frozen / "hooks").mkdir(); f = frozen / "hooks" / "x.py"; f.write_text("x\n")
            head = git(frozen, "rev-parse", "HEAD").stdout
            os.environ["COMMIT_SCOPE_FROZEN_ROOT"] = str(frozen)
            try:
                with self.assertRaises(commit_scope.PublishError) as cm:
                    commit_scope.publish(["hooks/x.py"], "m", cwd=frozen)
            finally:
                os.environ.pop("COMMIT_SCOPE_FROZEN_ROOT", None)
            self.assertIn("config-promote", str(cm.exception))
            self.assertEqual(head, git(frozen, "rev-parse", "HEAD").stdout)
            self.assertEqual("", git(frozen, "diff", "--cached", "--name-only").stdout,
                             "the refusal must happen before anything is staged")

    def test_other_repos_still_publish(self):
        with tempfile.TemporaryDirectory() as td:
            frozen = make_repo(Path(td), "frozen"); other = make_repo(Path(td), "other")
            (other / "src" / "a.py").write_text("a\n")
            os.environ["COMMIT_SCOPE_FROZEN_ROOT"] = str(frozen)
            try:
                res = commit_scope.publish(["src/a.py"], "m", cwd=other, announce=False)
            finally:
                os.environ.pop("COMMIT_SCOPE_FROZEN_ROOT", None)
            self.assertEqual("committed", res["status"])

    def test_ninja_fix_routes_harness_files_to_claude_promote(self):
        text = (CONFIG / "skills/ninja-fix/SKILL.md").read_text()
        self.assertIn('~/.claude/bin/config-promote -m "[ninja-fix] <one-sentence rationale>" '
                      '--paths "<the one ~/.claude file the Edit/Write call changed>"', text)


class D4_PathSpelling(unittest.TestCase):
    def test_symlinked_project_spelling_keeps_ledger_entries(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            link = Path(td) / "alias"; link.symlink_to(repo)
            plan = repo / "Thoughts" / "a_PLAN.md"; plan.write_text("A\n")
            (repo / ".claude/logs/_session_files-SID.log").write_text(f"{plan.resolve()}\n")
            self.assertIn("Thoughts/a_PLAN.md", scope("SID", link, "--publish-scope"),
                          "the symlinked spelling of the project root dropped the entry")

    def test_reconciliation_handles_a_path_with_spaces(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / "Diary" / "2026 notes.md").write_text("d\n")          # unledgered, merge_union
            (repo / ".claude/logs/_session_files-SID.log").write_text("")
            decl = scope("SID", repo, "--publish-scope")
            self.assertIn("Diary/2026 notes.md", decl)
            self.assertNotIn("Diary/", decl, "a collapsed untracked DIRECTORY was reconciled")


class D5_MixedCloseSplit(unittest.TestCase):
    def test_bookkeeping_and_domain_halves_partition_the_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            files = [repo / "TODO.md", repo / "Thoughts" / "a_PLAN.md", repo / "src" / "app.py"]
            for f in files:
                f.write_text(f"{f.name} changed\n")
            (repo / ".claude/logs/_session_files-SID.log").write_text(
                "".join(f"{f.resolve()}\n" for f in files))
            full = scope("SID", repo, "--publish-scope")
            book = scope("SID", repo, "--publish-scope", "--bookkeeping-only")
            dom = scope("SID", repo, "--publish-scope", "--domain-only")
            self.assertEqual(sorted(["TODO.md", "Thoughts/a_PLAN.md", "src/app.py"]), sorted(full))
            self.assertEqual(sorted(["TODO.md", "Thoughts/a_PLAN.md"]), sorted(book))
            self.assertEqual(["src/app.py"], dom)
            self.assertEqual(sorted(book + dom), sorted(full))

    def test_nested_thoughts_and_diary_are_DOMAIN_as_the_gate_classifies_them(self):
        """The gate's classifier matches a dir-prefix entry at the repo ROOT only.
        A split that called `Personal/Thoughts/x.md` bookkeeping produced a
        'bookkeeping-only' publish the gate refused as MIXED (second audit)."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            (repo / "Personal" / "Thoughts").mkdir(parents=True)
            (repo / "Personal" / "Diary").mkdir(parents=True)
            nested = [repo / "Personal" / "Thoughts" / "x.md", repo / "Personal" / "Diary" / "d.md"]
            for f in nested + [repo / "TODO.md"]:
                f.write_text("changed\n")
            (repo / ".claude/logs/_session_files-SID.log").write_text(
                "".join(f"{f.resolve()}\n" for f in nested + [repo / "TODO.md"]))
            self.assertEqual(["TODO.md"], scope("SID", repo, "--publish-scope", "--bookkeeping-only"))
            self.assertEqual(sorted(["Personal/Diary/d.md", "Personal/Thoughts/x.md"]),
                             sorted(scope("SID", repo, "--publish-scope", "--domain-only")))
            # ...and an unledgered NESTED diary file is not reconciled either.
            (repo / ".claude/logs/_session_files-SID.log").write_text("")
            self.assertNotIn("Personal/Diary/d.md", scope("SID", repo, "--publish-scope"))

    def test_worktree_close_declares_paths_relative_to_the_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            wt = Path(td) / "wt"
            git(repo, "worktree", "add", "-q", "-b", "topic", str(wt))
            (wt / "src").mkdir(exist_ok=True)            # empty dirs are not tracked
            f = wt / "src" / "feature.py"; f.write_text("x\n")
            (repo / ".claude/logs/_session_files-SIDW.log").write_text(f"{f.resolve()}\n")
            decl = scope("SIDW", wt, "--publish-scope")
            self.assertIn("src/feature.py", decl,
                          "a linked worktree's entry was relativized against the main checkout")
            res = commit_scope.publish(decl, "wt close", cwd=wt, announce=False)
            self.assertEqual("committed", res["status"])
            self.assertEqual("src/feature.py",
                             git(wt, "show", "--name-only", "--format=", "HEAD").stdout.strip())

    def test_missing_classifier_refuses_rather_than_emitting_a_wrong_split(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            fake_hooks = Path(td) / "hooks"; fake_hooks.mkdir()
            reader_copy = fake_hooks / "session-scope.sh"
            reader_copy.write_text(READER.read_text())      # sibling classifier absent
            (repo / "TODO.md").write_text("changed\n")
            r = subprocess.run(["bash", str(reader_copy), "SID", str(repo), "--publish-scope", "--bookkeeping-only"],
                               capture_output=True, text=True)
            self.assertEqual(3, r.returncode, r.stderr)
            self.assertEqual("", r.stdout)

    def test_the_bookkeeping_half_passes_the_enforcing_gate_out_of_tree(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            subprocess.run(["bash", str(HOOKS / "worktree-helper.sh"), "install", "--repo", str(repo)],
                           capture_output=True, text=True)
            (repo / "Personal" / "Thoughts").mkdir(parents=True)
            files = [repo / "TODO.md", repo / "src" / "app.py", repo / "Personal" / "Thoughts" / "x.md"]
            for f in files:
                f.write_text(f"{f.name} changed\n")
            (repo / ".claude/logs/_session_files-SID.log").write_text(
                "".join(f"{f.resolve()}\n" for f in files))
            env = dict(os.environ, CLAUDE_CONFIG_DIR=str(Path(td) / "cfg"))
            mixed = subprocess.run([sys.executable, str(HOOKS / "commit_scope.py"), "publish",
                                    "--repo", str(repo), "-m", "mixed", "--", "TODO.md", "src/app.py"],
                                   capture_output=True, text=True, env=env)
            self.assertNotEqual(0, mixed.returncode, "precondition: a mixed out-of-tree close is refused")
            git(repo, "restore", "--staged", "--", "TODO.md", "src/app.py", "Personal/Thoughts/x.md")
            book = scope("SID", repo, "--publish-scope", "--bookkeeping-only")
            head = git(repo, "rev-parse", "HEAD").stdout
            ok = subprocess.run([sys.executable, str(HOOKS / "commit_scope.py"), "publish",
                                 "--repo", str(repo), "-m", "close", "--", *book],
                                capture_output=True, text=True, env=env)
            self.assertEqual(0, ok.returncode, ok.stderr)
            self.assertNotEqual(head, git(repo, "rev-parse", "HEAD").stdout)
            self.assertEqual("TODO.md", git(repo, "show", "--name-only", "--format=", "HEAD").stdout.strip())


class D6_ScannerReach(unittest.TestCase):
    def test_shell_string_commands_are_flagged(self):
        src = ("import os, subprocess\n"
               "os.system('git commit -m x')\n"
               "subprocess.run('git -C r add -A && git commit -m y', shell=True)\n"
               "subprocess.run('git commit -m ok -- f', shell=True)\n"
               "print('use git commit -m x to commit')\n")
        hits = [(n, r) for n, r, _ in pss.scan_python(src)]
        self.assertEqual([(2, "unscoped-commit"), (3, "whole-tree-add")], hits)

    def test_fstrings_and_shell_c_forms_are_flagged(self):
        src = ("import os, subprocess\n"
               "def f(m, r):\n"
               "    os.system(f'git -C {r} commit -m {m}')\n"
               "    subprocess.run(['bash', '-c', 'git commit -m x'])\n"
               "    subprocess.run(['bash', '-c', f'git commit -m {m} -- f'])\n"
               "    print(f'use git commit -m {m}')\n"
               "    subprocess.run(['bash', '-c', f'git commit -m {m}'])\n")
        hits = [(n, r) for n, r, _ in pss.scan_python(src)]
        self.assertEqual([(3, "unscoped-commit"), (4, "unscoped-commit"), (7, "unscoped-commit")], hits)
        # Line 1 carries two violations (whole-tree add, bare commit); the splitter
        # is not quote-aware, so which one is reported first is not the point —
        # that the line IS flagged is. Line 2 is scoped and must stay clean.
        sh = "bash -c 'git add -A && git commit -m x'\nsh -c \"git commit -m y -- f\"\n"
        hits = list(pss.scan_shell(sh))
        self.assertTrue(hits and all(n == 1 for n, _, _ in hits), hits)
        self.assertEqual([], list(pss.scan_shell("bash -c 'git commit -m x -- f'\n")))
        self.assertEqual(["unscoped-commit"], [r for _, r, _ in pss.scan_shell("bash -c 'git commit -m x'\n")])

    def test_also_scans_a_project_skill_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "cfg"; (root / "hooks").mkdir(parents=True)
            skills = Path(td) / "Skills"; skills.mkdir()
            (skills / "publish.md").write_text("```bash\ngit commit -m release\n```\n")
            self.assertTrue(pss.scan(root)["ok"])
            res = pss.scan(root, (str(skills),))
            self.assertEqual([("[also] Skills/publish.md", "unscoped-commit")],
                             [(f["path"], f["rule"]) for f in res["findings"]])


class A5_SessionIdEquality(unittest.TestCase):
    def test_tool_writes_and_code_writes_join_in_one_ledger_when_ids_match(self):
        """The two halves of the ledger key the file differently: the PostToolUse
        hook on the payload's `session_id`, code-layer writers on
        CLAUDE_CODE_SESSION_ID. They join only if the harness gives both the same
        value — measured equal in S4. This pins the consequence: with equal ids,
        both halves land in ONE file, and /close's declaration sees both."""
        with tempfile.TemporaryDirectory() as td:
            repo = make_repo(Path(td))
            tool_file = repo / "Thoughts" / "tool_written.md"; tool_file.write_text("t\n")
            code_file = repo / "TODO.md"
            payload = json.dumps({"session_id": "SAME", "cwd": str(repo),
                                  "tool_input": {"file_path": str(tool_file)}})
            subprocess.run(["bash", str(WRITER)], input=payload, text=True, capture_output=True)
            os.environ["CLAUDE_CODE_SESSION_ID"] = "SAME"
            try:
                commit_scope.record_write(code_file)
            finally:
                os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
            ledgers = sorted(repo.rglob("_session_files-*.log"))
            self.assertEqual([repo / ".claude/logs/_session_files-SAME.log"], ledgers)
            body = ledgers[0].read_text()
            self.assertIn(str(code_file.resolve()), body)
            self.assertIn("tool_written.md", body)
            decl = scope("SAME", repo, "--publish-scope")
            self.assertIn("Thoughts/tool_written.md", decl)
            self.assertIn("TODO.md", decl)


if __name__ == "__main__":
    unittest.main(verbosity=2)

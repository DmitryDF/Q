#!/usr/bin/env python3
"""S3 (streamed-dancing-goose / A3) — ONE answer to "is this path inside the projects
tree", reached by all three resolvers; the newly-live branches swept; the null
project roots the broken resolver produced are repaired on rebind.

A3's validation gate, one class per clause:

  OneNormaliser     `_norm_path` is the single realpath+normcase site in the module;
                    `_resolve_project_root`, `canonical_project_for_spine` and
                    `_project_dir_for_spine` all answer the symlinked-root case the
                    same way; the outside-the-root answer stays per caller
                    (None / "Root" / the root path).
  FreshCreate       `create_topic` from a real-path cwd — and from the SYMLINK
                    spelling of the same cwd — stores a non-null root; the record is
                    then accepted by `relocate_plan_after_approval` with NO hand-edit
                    of the tracking record.
  WorktreeFailSafe  a worktree-detect timeout / missing primitive / exit-2 yields
                    UNKNOWN, and a new topic then routes to ~/.claude/plans (null
                    root) with a stderr note — never project-side; `_in_worktree`
                    keeps its bool contract for the callers that were already live.
  NullRootRepair    a record carrying a null root created BEFORE the fix landed is
                    repaired on rebind and then relocates; one created after the
                    cutoff is left alone; a worktree or an unknown detection leaves
                    it alone; a non-null root is never rewritten (spelling kept).

Run: env CLAUDE_CONFIG_DIR=<clone> python3 hooks/tests/test_s3_project_root_normaliser.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg  # noqa: E402


def _mk_tree(root: Path):
    (root / "TODO.md").write_text("# root todo\n")
    (root / "Thoughts").mkdir()
    leaf = root / "Personal" / "your-project"
    leaf.mkdir(parents=True)
    (leaf / "TODO.md").write_text("# leaf todo\n")
    (leaf / "Thoughts").mkdir()
    return leaf


class S3Base(unittest.TestCase):
    """A scratch projects tree reachable by TWO spellings: `real/` and a symlink
    `link/` -> `real/` — the shape of $CLAUDE_PROJECT_DIR on this machine.
    PROJECTS_ROOT is set to the SYMLINK spelling (as the live constant is), and the
    state dirs are redirected so nothing live is touched."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s3_root_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.real = self.tmp / "real"
        self.real.mkdir()
        self.leaf = _mk_tree(self.real)
        self.link = self.tmp / "link"
        self.link.symlink_to(self.real, target_is_directory=True)
        self._saved = {k: getattr(ppg, k) for k in
                       ("PROJECTS_ROOT", "TOPIC_STATE_DIR", "PLAN_MODE_INIT_DIR",
                        "POST_PLAN_CHOICE_DIR", "_worktree_status")}
        ppg.PROJECTS_ROOT = self.link                      # the symlink spelling
        self.state_dir = self.tmp / "state" / "pre_plan_gates"
        self.init_dir = self.tmp / "state" / "plan_mode_init"
        self.choice_dir = self.tmp / "state" / "post_plan_choice"
        for d in (self.state_dir, self.init_dir, self.choice_dir):
            d.mkdir(parents=True)
        ppg.TOPIC_STATE_DIR = self.state_dir
        ppg.PLAN_MODE_INIT_DIR = self.init_dir
        ppg.POST_PLAN_CHOICE_DIR = self.choice_dir
        ppg._worktree_status = lambda cwd=None: False     # primary tree unless a test says otherwise
        self._cwd = os.getcwd()

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._saved.items():
            setattr(ppg, k, v)

    def _state(self, topic, project):
        return json.loads((self.state_dir / f"{topic}__{project}.json").read_text())

    def _relocate(self, sid, topic, project, mode="A"):
        state = self._state(topic, project)
        root = Path(state["project_root"])
        thought = root / f"{topic}_THOUGHT.md"
        thought.write_text("# T\n\n# Implementation Details\n")
        (self.init_dir / f"{sid}.json").write_text(json.dumps({"mode": mode, "thought_path": str(thought)}))
        (self.choice_dir / f"{sid}.marker").write_text(json.dumps({"pending": False}))
        plan = self.tmp / f"harness-plan-{sid[:6]}.md"
        plan.write_text("# Plan\n")
        return ppg.relocate_plan_after_approval(sid, str(plan))


# --------------------------------------------------------------------------- #
class OneNormaliser(S3Base):

    def test_single_realpath_site_in_module(self):
        src = (HOOKS / "pre_plan_gates.py").read_text(encoding="utf-8")
        lines = [ln for ln in src.split("\n")
                 if "os.path.realpath(" in ln and not ln.strip().startswith("#")]
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("def _norm_path", src)

    def test_three_resolvers_agree_on_the_symlinked_root(self):
        # cwd/spine addressed through the REAL path, root given as the SYMLINK
        rp = ppg._resolve_project_root(self.leaf / "Thoughts")
        self.assertEqual(rp, ppg._norm_path(self.leaf))
        slug = ppg.canonical_project_for_spine(self.leaf / "Thoughts" / "a_THOUGHT.md")
        self.assertEqual(slug, "your-project")
        pd = ppg._project_dir_for_spine(self.leaf / "Thoughts" / "a_THOUGHT.md")
        self.assertEqual(pd, ppg._norm_path(self.leaf))
        # and the root-level location, through the symlink spelling this time
        rp_root = ppg._resolve_project_root(self.link / "Thoughts")
        self.assertEqual(rp_root, ppg._norm_path(self.real))
        self.assertEqual(ppg.canonical_project_for_spine(self.link / "Thoughts" / "b_THOUGHT.md"), "Root")
        self.assertEqual(ppg._project_dir_for_spine(self.link / "Thoughts" / "b_THOUGHT.md"),
                         ppg._norm_path(self.real))

    def test_outside_the_root_keeps_each_callers_own_answer(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        self.assertIsNone(ppg._resolve_project_root(outside))
        self.assertEqual(ppg.canonical_project_for_spine(outside / "x_THOUGHT.md"), "Root")
        self.assertEqual(ppg._project_dir_for_spine(outside / "x_THOUGHT.md"), ppg._norm_path(self.real))

    def test_norm_path_collapses_both_spellings(self):
        self.assertEqual(ppg._norm_path(self.link / "Personal"), ppg._norm_path(self.real / "Personal"))


# --------------------------------------------------------------------------- #
class FreshCreate(S3Base):

    def test_create_from_real_cwd_stores_root_and_relocates_without_hand_edit(self):
        os.chdir(self.leaf / "Thoughts")
        sid = f"s3-real-{uuid.uuid4().hex[:8]}"
        r = ppg.create_topic(sid, project_slug="your-project", topic_slug="s3-fresh-real",
                             intake_source="thought")
        self.assertEqual(r["status"], "created")
        st = self._state("s3-fresh-real", "your-project")
        self.assertEqual(st["project_root"], str(ppg._norm_path(self.leaf)))
        res = self._relocate(sid, "s3-fresh-real", "your-project")
        self.assertEqual(res["status"], "relocated")
        self.assertIn("Thoughts", res["dest"])

    def test_create_from_symlink_spelled_cwd_stores_root(self):
        # os.getcwd() may or may not resolve the link; either way the stored root
        # must be non-null and inside the tree.
        os.chdir(self.link / "Personal" / "your-project")
        sid = f"s3-link-{uuid.uuid4().hex[:8]}"
        ppg.create_topic(sid, project_slug="your-project", topic_slug="s3-fresh-link",
                         intake_source="thought")
        st = self._state("s3-fresh-link", "your-project")
        self.assertIsNotNone(st["project_root"])
        self.assertEqual(ppg._norm_path(st["project_root"]), ppg._norm_path(self.leaf))

    def test_create_from_root_level_cwd_stores_the_root(self):
        os.chdir(self.real / "Thoughts")
        sid = f"s3-root-{uuid.uuid4().hex[:8]}"
        ppg.create_topic(sid, project_slug="Root", topic_slug="s3-fresh-root", intake_source="thought")
        st = self._state("s3-fresh-root", "Root")
        self.assertEqual(ppg._norm_path(st["project_root"]), ppg._norm_path(self.real))

    def test_create_outside_the_tree_stores_null(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        os.chdir(outside)
        ppg.create_topic(f"s3-out-{uuid.uuid4().hex[:8]}", project_slug="Root",
                         topic_slug="s3-fresh-out", intake_source="thought")
        self.assertIsNone(self._state("s3-fresh-out", "Root")["project_root"])


# --------------------------------------------------------------------------- #
class WorktreeFailSafe(S3Base):

    def _with_detector(self, behaviour):
        """Run the REAL _worktree_status with subprocess.run replaced."""
        ppg._worktree_status = self._saved["_worktree_status"]
        orig = ppg.subprocess.run
        def fake_run(cmd, **kw):
            if behaviour == "timeout":
                raise subprocess.TimeoutExpired(cmd, 2)
            if behaviour == "missing":
                raise FileNotFoundError("worktree-detect.sh")
            class R: pass
            r = R(); r.returncode = behaviour; r.stdout = r.stderr = ""
            return r
        ppg.subprocess.run = fake_run
        self.addCleanup(setattr, ppg.subprocess, "run", orig)

    def test_status_is_tri_state(self):
        for behaviour, expected in (("timeout", None), ("missing", None), (2, None), (0, True), (1, False)):
            self._with_detector(behaviour)
            self.assertIs(ppg._worktree_status(self.real), expected, behaviour)
            # the bool wrapper keeps its contract: only a clean 0 is True
            self.assertIs(ppg._in_worktree(self.real), expected is True, behaviour)

    def test_unknown_detection_routes_to_harness_plans_and_says_so(self):
        os.chdir(self.leaf / "Thoughts")
        self._with_detector("timeout")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(ppg._project_root_for_new_topic())
            ppg.create_topic(f"s3-wt-{uuid.uuid4().hex[:8]}", project_slug="your-project",
                             topic_slug="s3-wt-unknown", intake_source="thought")
        self.assertIsNone(self._state("s3-wt-unknown", "your-project")["project_root"])
        self.assertIn("worktree detection did not answer", err.getvalue())

    def test_confirmed_worktree_routes_to_harness_plans_silently(self):
        os.chdir(self.leaf / "Thoughts")
        self._with_detector(0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertIsNone(ppg._project_root_for_new_topic())
        self.assertEqual(err.getvalue(), "")

    def test_confirmed_primary_tree_stores_the_root(self):
        os.chdir(self.leaf / "Thoughts")
        self._with_detector(1)
        self.assertEqual(ppg._project_root_for_new_topic(), ppg._norm_path(self.leaf))


# --------------------------------------------------------------------------- #
class NullRootRepair(S3Base):

    def _seed_null(self, topic, project, created, artifact="spine-abs"):
        """A pre-fix-shaped record: null root, `created` back-dated. Its artifact —
        the thing the repair judges by — is one of: 'spine-abs' (absolute spine
        inside the tree), 'spine-rel' (vault-relative spine that exists),
        'todo-ref' (a TODO line ref inside the tree), or None (no artifact)."""
        sid = f"s3-seed-{uuid.uuid4().hex[:8]}"
        os.chdir(self.tmp)                     # outside the tree → genuine null at mint
        ppg.create_topic(sid, project_slug=project, topic_slug=topic, intake_source="thought")
        p = self.state_dir / f"{topic}__{project}.json"
        st = json.loads(p.read_text())
        self.assertIsNone(st["project_root"])
        st["created"] = created
        home = self.real if project == "Root" else self.leaf
        spine = home / "Thoughts" / f"{topic}_THOUGHT.md"
        spine.write_text("# t\n")
        if artifact == "spine-abs":
            st["thought_file_path"] = str(spine)
        elif artifact == "spine-rel":
            st["thought_file_path"] = str(spine.relative_to(self.real))
        elif artifact == "todo-ref":
            st["todo_line_ref"] = f"{home / 'TODO.md'}:3"
        p.write_text(json.dumps(st))
        return sid

    def test_pre_cutoff_null_is_repaired_on_rebind_and_then_relocates(self):
        self._seed_null("s3-old", "your-project", "2026-08-08T12:00:00+00:00")
        os.chdir(self.leaf / "Thoughts")
        sid2 = f"s3-rebind-{uuid.uuid4().hex[:8]}"
        r = ppg.create_topic(sid2, project_slug="your-project", topic_slug="s3-old", intake_source="thought")
        self.assertEqual(r["status"], "rebound")
        self.assertEqual(r["project_root_repaired"], str(ppg._norm_path(self.leaf)))
        st = self._state("s3-old", "your-project")
        self.assertEqual(st["project_root"], str(ppg._norm_path(self.leaf)))
        self.assertIn("project_root_repaired_at", st)
        self.assertEqual(st["created"], "2026-08-08T12:00:00+00:00", "created is not rewritten")
        res = self._relocate(sid2, "s3-old", "your-project")
        self.assertEqual(res["status"], "relocated")

    def test_post_cutoff_null_is_left_alone(self):
        self._seed_null("s3-new", "your-project", "2099-01-01T00:00:00+00:00")
        os.chdir(self.leaf / "Thoughts")
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="your-project",
                             topic_slug="s3-new", intake_source="thought")
        self.assertEqual(r["status"], "rebound")
        self.assertNotIn("project_root_repaired", r)
        self.assertIsNone(self._state("s3-new", "your-project")["project_root"])

    def test_worktree_or_unknown_detection_leaves_null_alone(self):
        for status in (True, None):
            topic = f"s3-wt-{'t' if status else 'n'}"
            self._seed_null(topic, "your-project", "2026-08-08T12:00:00+00:00")
            os.chdir(self.leaf / "Thoughts")
            ppg._worktree_status = lambda cwd=None, s=status: s
            r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="your-project",
                                 topic_slug=topic, intake_source="thought")
            self.assertEqual(r["status"], "rebound")
            self.assertNotIn("project_root_repaired", r)
            self.assertIsNone(self._state(topic, "your-project")["project_root"])

    def test_non_null_root_is_never_rewritten(self):
        sid = self._seed_null("s3-spelled", "your-project", "2026-08-08T12:00:00+00:00")
        p = self.state_dir / "s3-spelled__your-project.json"
        st = json.loads(p.read_text())
        st["project_root"] = str(self.link / "Personal" / "your-project")   # the symlink spelling
        p.write_text(json.dumps(st))
        os.chdir(self.leaf / "Thoughts")
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="your-project",
                             topic_slug="s3-spelled", intake_source="thought")
        self.assertNotIn("project_root_repaired", r)
        self.assertEqual(self._state("s3-spelled", "your-project")["project_root"],
                         str(self.link / "Personal" / "your-project"))

    def test_root_keyed_record_rebound_from_a_leaf_cwd_is_not_mis_attributed(self):
        """The cutoff cannot tell the defect from 'genuinely outside the tree'. The
        record's own key can: a `__Root` record rebound from a leaf cwd would
        resolve the LEAF as its root — refused, nothing written."""
        self._seed_null("s3-rootkey", "Root", "2026-08-08T12:00:00+00:00")
        os.chdir(self.leaf / "Thoughts")                       # a leaf cwd, Root-keyed record
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="Root",
                             topic_slug="s3-rootkey", intake_source="thought")
        self.assertEqual(r["status"], "rebound")
        self.assertNotIn("project_root_repaired", r)
        self.assertIsNone(self._state("s3-rootkey", "Root")["project_root"])
        # and from the root-level cwd the same record IS repaired to the tree root
        os.chdir(self.real / "Thoughts")
        r2 = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="Root",
                              topic_slug="s3-rootkey", intake_source="thought")
        self.assertEqual(r2.get("project_root_repaired"), str(ppg._norm_path(self.real)))

    def test_record_with_absolute_spine_outside_the_tree_is_left_alone(self):
        """A pre-cutoff `__Root` record whose recorded spine is an absolute path
        OUTSIDE the tree was genuinely outside at creation. Rebound from the tree
        root — where the key check alone would let the repair through — it is
        still refused. (An outside spine keys as `Root` via the canonical
        resolver, so the fixture uses that key.)"""
        self._seed_null("s3-outside", "Root", "2026-08-08T12:00:00+00:00")
        p = self.state_dir / "s3-outside__Root.json"
        st = json.loads(p.read_text())
        outside = self.tmp / "elsewhere" / "s3-outside_THOUGHT.md"
        outside.parent.mkdir(exist_ok=True)
        outside.write_text("# t\n")
        st["thought_file_path"] = str(outside)
        p.write_text(json.dumps(st))
        os.chdir(self.real / "Thoughts")
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="Root",
                             topic_slug="s3-outside", intake_source="thought")
        self.assertEqual(r["status"], "rebound")
        self.assertNotIn("project_root_repaired", r)
        self.assertIsNone(self._state("s3-outside", "Root")["project_root"])

    def test_record_with_no_artifact_is_left_alone(self):
        """The residual round 2 named: with no spine and no TODO line the rebinding
        cwd is the only signal, and it says nothing about where the topic was
        created — refuse-to-guess, even from the matching root."""
        self._seed_null("s3-noart", "Root", "2026-08-08T12:00:00+00:00", artifact=None)
        os.chdir(self.real / "Thoughts")
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="Root",
                             topic_slug="s3-noart", intake_source="thought")
        self.assertEqual(r["status"], "rebound")
        self.assertNotIn("project_root_repaired", r)
        self.assertIsNone(self._state("s3-noart", "Root")["project_root"])

    def test_relative_spine_inside_the_tree_is_evidence_enough(self):
        self._seed_null("s3-rel", "your-project", "2026-08-08T12:00:00+00:00", artifact="spine-rel")
        os.chdir(self.leaf / "Thoughts")
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="your-project",
                             topic_slug="s3-rel", intake_source="thought")
        self.assertEqual(r.get("project_root_repaired"), str(ppg._norm_path(self.leaf)))

    def test_todo_line_ref_inside_the_tree_is_evidence_enough(self):
        self._seed_null("s3-todo", "your-project", "2026-08-08T12:00:00+00:00", artifact="todo-ref")
        os.chdir(self.leaf / "Thoughts")
        r = ppg.create_topic(f"s3-r-{uuid.uuid4().hex[:8]}", project_slug="your-project",
                             topic_slug="s3-todo", intake_source="plain")
        self.assertEqual(r.get("project_root_repaired"), str(ppg._norm_path(self.leaf)))

    def test_artifact_placement_predicate(self):
        root = ppg._norm_path(self.real)
        self.assertIs(ppg._record_artifact_inside_tree({}), None)
        self.assertIs(ppg._record_artifact_inside_tree({"thought_file_path": str(self.tmp / "x_THOUGHT.md")}), False)
        self.assertIs(ppg._record_artifact_inside_tree({"thought_file_path": str(self.leaf / "x_THOUGHT.md")}), True)
        self.assertIs(ppg._record_artifact_inside_tree({"thought_file_path": "Thoughts/does-not-exist_THOUGHT.md"}), None)
        (self.real / "Thoughts" / "exists_THOUGHT.md").write_text("# t\n")
        self.assertIs(ppg._record_artifact_inside_tree({"thought_file_path": "Thoughts/exists_THOUGHT.md"}), True)
        self.assertIs(ppg._record_artifact_inside_tree({"todo_line_ref": f"{self.leaf / 'TODO.md'}:7"}), True)
        self.assertIs(ppg._record_artifact_inside_tree({"todo_line_ref": f"{self.tmp / 'TODO.md'}:7"}), False)

    def test_cutoff_constant_is_iso_and_today_or_earlier(self):
        from datetime import datetime, timezone
        cutoff = datetime.fromisoformat(ppg.PROJECT_ROOT_FIX_LANDED)
        self.assertIsNotNone(cutoff.tzinfo)
        self.assertLessEqual(cutoff, datetime.now(timezone.utc))


if __name__ == "__main__":
    unittest.main(verbosity=1)

#!/usr/bin/env python3
"""Tests for ship-time topic auto-registration (auto-registration Mode-C plan, S-B).

Covers `pre_plan_gates.auto_register_topic` (action A1):

  R1   mints a slug-tagged `[Thought]` line + binds state+spine in one write
  R2   the minted line carries the `[auto-registered]` marker
  R3   the minted line deliberately FAILS `todo.cmd_validate_framing`
       (the system fabricates no framing — the failure IS the obligation)
  R4   `Sessions: 1/N done.` is present when a declared slice count resolves,
       and omitted when it does not
  R5   refuse-to-guess: no anchoring artifact -> skipped + operator warning
  R6   refuse-to-guess: anchoring artifact not on disk -> skipped + warning
  R7   idempotent: a second invocation does not duplicate the line
  R8   idempotency is keyed on the SLUG TAG, not the marker — an operator-edited
       line (marker stripped) is still recognised and not duplicated
  R9   TORN STATE: a crash after the line but before binding is repaired — a
       re-invocation binds the state even though the line already exists
  R10  the state + its thought_file_path land in ONE write (never a
       bound-but-spineless intermediate)
  R11  identity is derived from the spine, not cwd: a cross-cutting spine keys
       to `Root`, a project-scoped spine keys to the leaf project
  R12  ordering: the line exists whenever the binding does (line-first)
  R13  NO model/AI call anywhere in the mint path (the code-first C5 constraint)

Run: python3 ${KIT_HOOKS_DIR}/tests/test_auto_register.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pre_plan_gates as ppg   # noqa: E402
import todo as todo_mod        # noqa: E402


TODO_SKELETON = """# TODO

## Now

_(nothing yet)_

## Next

_(nothing yet)_

## Done
"""

DECLARED_TABLE = """
#### Slices

| ID | Name | Type | Depends on |
|----|------|------|------------|
| S1 | one   | implementation | — |
| S2 | two   | implementation | S1 |
| S3 | three | implementation | S2 |
"""


class AutoRegisterBase(unittest.TestCase):
    """Each test runs against an isolated scratch PROJECTS_ROOT + state dir."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autoreg_"))
        self.root = self.tmp / "Projects"
        (self.root / "Thoughts").mkdir(parents=True)
        (self.root / "TODO.md").write_text(TODO_SKELETON)
        self.leaf = self.root / "Personal" / "widget"
        (self.leaf / "Thoughts").mkdir(parents=True)
        (self.leaf / "TODO.md").write_text(TODO_SKELETON)

        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()

        self._patches = [
            mock.patch.object(ppg, "PROJECTS_ROOT", self.root),
            mock.patch.object(ppg, "TOPIC_STATE_DIR", self.state_dir),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def _spine(self, name="widget-thing-20260806223312_PLAN.md",
               in_leaf=False, declared=False):
        base = (self.leaf if in_leaf else self.root) / "Thoughts"
        p = base / name
        p.write_text("# Plan\n" + (DECLARED_TABLE if declared else ""))
        return p

    def _todo_text(self, in_leaf=False):
        target = (self.leaf if in_leaf else self.root) / "TODO.md"
        return target.read_text(encoding="utf-8")

    def _open_lines(self, in_leaf=False):
        return [ln for ln in self._todo_text(in_leaf).splitlines()
                if ln.strip().startswith("- [ ]")]


class AutoRegisterMintTests(AutoRegisterBase):

    def test_R1_mints_line_and_binds_state_with_spine(self):
        spine = self._spine()
        res = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res["status"], "registered", msg=json.dumps(res))
        self.assertEqual(res["todo_line"], "minted")
        self.assertEqual(res["topic_state"], "bound")

        self.assertIn("[widget-thing-20260806223312]", self._todo_text())

        state_path = self.state_dir / "widget-thing__Root.json"
        self.assertTrue(state_path.exists())
        state = json.loads(state_path.read_text())
        self.assertEqual(state["thought_file_path"], str(spine))
        self.assertEqual(state["topic_slug"], "widget-thing")
        self.assertEqual(state["project_slug"], "Root")

    def test_R2_line_carries_the_auto_registered_marker(self):
        ppg.auto_register_topic("sid-1", self._spine())
        line = next(ln for ln in self._open_lines() if "widget-thing" in ln)
        self.assertIn("[auto-registered]", line)
        self.assertIn("[Thought]", line)

    def test_R3_minted_line_fails_the_framing_validator(self):
        # The system fabricates no framing: the minted line must NOT satisfy
        # cmd_validate_framing. That failure IS the standing obligation, and it
        # is the SAME structural bar a manual [Thought] line passes.
        ppg.auto_register_topic("sid-1", self._spine())
        line = next(ln for ln in self._open_lines() if "widget-thing" in ln)
        body = line.split("- [ ]", 1)[1].strip()
        verdict = todo_mod.cmd_validate_framing(body)
        self.assertEqual(verdict["status"], "fail")
        joined = " ".join(verdict["errors"])
        self.assertIn("Problem:", joined)
        self.assertIn("Context:", joined)

    def test_R4_sessions_counter_present_only_with_a_declared_count(self):
        with_decl = self._spine(name="withn-20260806223312_PLAN.md", declared=True)
        ppg.auto_register_topic("sid-1", with_decl)
        line = next(ln for ln in self._open_lines() if "withn" in ln)
        self.assertIn("Sessions: 1/3 done.", line)

        without = self._spine(name="non-20260806223313_PLAN.md", declared=False)
        ppg.auto_register_topic("sid-2", without)
        line2 = next(ln for ln in self._open_lines() if "non-2026" in ln)
        self.assertNotIn("Sessions:", line2)


class AutoRegisterRefuseToGuessTests(AutoRegisterBase):

    def test_R5_no_anchoring_artifact_skips_with_warning(self):
        res = ppg.auto_register_topic("sid-1", None)
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "no_anchoring_artifact")
        self.assertTrue(res["warning"])
        self.assertEqual(self._open_lines(), [])
        self.assertEqual(list(self.state_dir.glob("*.json")), [])

    def test_R6_missing_artifact_on_disk_skips_with_warning(self):
        res = ppg.auto_register_topic(
            "sid-1", self.root / "Thoughts" / "ghost-20260806223312_PLAN.md")
        self.assertEqual(res["status"], "skipped")
        self.assertEqual(res["reason"], "anchoring_artifact_missing")
        self.assertTrue(res["warning"])
        self.assertEqual(self._open_lines(), [])
        self.assertEqual(list(self.state_dir.glob("*.json")), [],
                         msg="a skipped mint must write no state file either")

    def test_R5b_skip_signal_is_artifact_absence_not_a_none_project(self):
        # canonical_project_for_spine NEVER returns None — a cross-cutting spine
        # resolves to "Root". So the refuse-to-guess trigger must be the missing
        # artifact, never a None project.
        spine = self._spine()
        self.assertEqual(ppg.canonical_project_for_spine(spine), "Root")
        self.assertIsNotNone(ppg.canonical_project_for_spine(spine))


class AutoRegisterIdempotencyTests(AutoRegisterBase):

    def test_R7_second_invocation_does_not_duplicate(self):
        spine = self._spine()
        ppg.auto_register_topic("sid-1", spine)
        res2 = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res2["todo_line"], "already_present")
        self.assertEqual(res2["topic_state"], "already_bound")
        tagged = [ln for ln in self._open_lines()
                  if "[widget-thing-20260806223312]" in ln]
        self.assertEqual(len(tagged), 1)

    def test_R8_idempotency_keyed_on_slug_tag_not_the_marker(self):
        # An operator edits the line and strips the marker (i.e. frames it).
        # A later mint must still recognise it via the STABLE slug tag and not
        # write a duplicate.
        spine = self._spine()
        ppg.auto_register_topic("sid-1", spine)
        target = self.root / "TODO.md"
        target.write_text(target.read_text().replace("[auto-registered] ", ""))
        self.assertNotIn("[auto-registered]", self._todo_text())

        res2 = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res2["todo_line"], "already_present")
        tagged = [ln for ln in self._open_lines()
                  if "[widget-thing-20260806223312]" in ln]
        self.assertEqual(len(tagged), 1)
        self.assertNotIn("[auto-registered]", self._todo_text())


class AutoRegisterTornStateTests(AutoRegisterBase):

    def test_R9_crash_after_line_before_binding_is_repaired(self):
        # Simulate the torn state: the line was written, the process died before
        # the state binding. The topic is UNBOUND — which the omission arm still
        # catches — and a re-invocation must COMPLETE the binding rather than
        # skip it because the line already exists.
        spine = self._spine()
        with mock.patch.object(ppg, "_write_topic_state",
                               side_effect=RuntimeError("crash")):
            res1 = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res1["todo_line"], "minted")
        self.assertEqual(res1["status"], "partial")
        self.assertEqual(res1["topic_state"], "failed")
        self.assertFalse((self.state_dir / "widget-thing__Root.json").exists(),
                         "torn state must leave the topic UNBOUND, not bound")

        res2 = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res2["todo_line"], "already_present")
        self.assertEqual(res2["topic_state"], "bound")
        state = json.loads(
            (self.state_dir / "widget-thing__Root.json").read_text())
        self.assertEqual(state["thought_file_path"], str(spine))

    def test_R10_state_and_spine_land_in_a_single_write(self):
        # A create-then-backfill two-step would expose a bound-but-spineless
        # window. Assert _write_topic_state is called exactly once and that the
        # very first payload already carries thought_file_path.
        spine = self._spine()
        seen = []
        real = ppg._write_topic_state

        def spy(*, topic_slug, project_slug, state):
            seen.append(dict(state))
            return real(topic_slug=topic_slug, project_slug=project_slug,
                        state=state)

        with mock.patch.object(ppg, "_write_topic_state", side_effect=spy):
            ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(len(seen), 1, "state must be written exactly once")
        self.assertEqual(seen[0].get("thought_file_path"), str(spine),
                         "the FIRST state write must already carry the spine")

    def test_R14_soft_line_failure_leaves_the_topic_unbound(self):
        # The torn-state invariant must hold for a SOFT failure too, not just an
        # exception: `cmd_add` can return non-zero without raising (e.g. no
        # TODO.md at the resolved target). Binding after that would produce
        # bound-but-untracked — the one state the omission gate cannot see.
        spine = self._spine()
        missing_todo = self.tmp / "nowhere" / "TODO.md"
        res = ppg.auto_register_topic("sid-1", spine, todo_file=str(missing_todo))
        self.assertEqual(res["status"], "partial")
        self.assertNotIn(res.get("todo_line"), ("minted", "already_present"))
        self.assertEqual(res["topic_state"], "skipped_line_failed")
        self.assertTrue(res.get("warning"))
        self.assertEqual(list(self.state_dir.glob("*.json")), [],
                         msg="a failed line must leave the topic UNBOUND")

    def test_R15_soft_line_failure_self_heals_on_retry(self):
        # And it must self-heal: once the target exists, a re-run mints the line
        # AND binds. (The pre-fix code bound on the first pass, so the retry saw
        # `already_bound` and the topic stayed permanently line-less.)
        spine = self._spine()
        late = self.tmp / "late" / "TODO.md"
        res1 = ppg.auto_register_topic("sid-1", spine, todo_file=str(late))
        self.assertEqual(res1["topic_state"], "skipped_line_failed")

        late.parent.mkdir(parents=True, exist_ok=True)
        late.write_text(TODO_SKELETON)
        res2 = ppg.auto_register_topic("sid-1", spine, todo_file=str(late))
        self.assertEqual(res2["todo_line"], "minted")
        self.assertEqual(res2["topic_state"], "bound")
        self.assertIn("[widget-thing-20260806223312]",
                      late.read_text(encoding="utf-8"))

    def test_R12_line_exists_whenever_the_binding_does(self):
        spine = self._spine()
        ppg.auto_register_topic("sid-1", spine)
        bound = (self.state_dir / "widget-thing__Root.json").exists()
        line = "[widget-thing-20260806223312]" in self._todo_text()
        self.assertTrue(bound)
        self.assertTrue(line, "line-first ordering: a binding implies a line")


class AutoRegisterIdentityTests(AutoRegisterBase):

    def test_R11_identity_derives_from_the_spine_not_cwd(self):
        cross = self._spine(name="crosscut-20260806223312_PLAN.md")
        ppg.auto_register_topic("sid-1", cross)
        self.assertTrue((self.state_dir / "crosscut__Root.json").exists())

        scoped = self._spine(name="scoped-20260806223312_PLAN.md", in_leaf=True)
        ppg.auto_register_topic("sid-2", scoped)
        self.assertTrue((self.state_dir / "scoped__widget.json").exists())
        # and its line went to the LEAF project's TODO.md, not the root's
        self.assertIn("[scoped-20260806223312]", self._todo_text(in_leaf=True))
        self.assertNotIn("[scoped-20260806223312]", self._todo_text())


class AutoRegisterSlugDerivationTests(AutoRegisterBase):
    """The slug tag and the topic-state key must never disagree."""

    def _assert_agree(self, filename, expected_slug, expected_tag):
        spine = self._spine(name=filename)
        res = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res["status"], "registered", msg=json.dumps(res))
        self.assertEqual(res["slug_tag"], expected_tag)
        self.assertEqual(res["topic"], f"{expected_slug}__Root")
        self.assertTrue((self.state_dir / f"{expected_slug}__Root.json").exists())
        self.assertIn(f"[{expected_tag}]", self._todo_text())

    def test_R16_scope_keyed_spine_name(self):
        # Bucket-2 scope-keyed artifact (`<slug>-<ts>_<SCOPE>_<TYPE>.md`).
        # Deriving the key with a TYPE-suffix-first parser would yield
        # `widget-thing-20260806223312_K` and diverge from the tag.
        self._assert_agree("widget-thing-20260806223312_K_PLAN.md",
                           "widget-thing", "widget-thing-20260806223312")

    def test_R17_non_14_digit_run_still_agrees(self):
        # A 15-digit run: an anchored `-\d{14}$` strip and an unanchored
        # `^(.*?-\d{14})` match disagree on this input. Deriving one FROM the
        # other cannot. The invariant under test is agreement between the tag
        # and the key, not any particular spelling of either.
        spine = self._spine(name="widget-202608062233123_PLAN.md")
        res = ppg.auto_register_topic("sid-1", spine)
        self.assertEqual(res["status"], "registered", msg=json.dumps(res))
        slug = res["topic"].split("__")[0]
        tag = res["slug_tag"]
        self.assertTrue(tag == slug or tag.startswith(slug + "-"),
                        msg=f"tag {tag!r} does not correspond to key {slug!r}")
        self.assertTrue((self.state_dir / f"{slug}__Root.json").exists())
        self.assertIn(f"[{tag}]", self._todo_text())

    def test_R18_untimestamped_spine_name(self):
        self._assert_agree("legacy-topic_PLAN.md", "legacy-topic", "legacy-topic")

    def test_R19_thought_spine_name(self):
        self._assert_agree("widget-thing-20260806223312_THOUGHT.md",
                           "widget-thing", "widget-thing-20260806223312")


class AutoRegisterIntegrationTests(AutoRegisterBase):
    """How the minted record is read by the surfaces it exists to feed."""

    def test_R20_plan_spine_classifies_as_worktree_origin(self):
        # `auto-registered` must not masquerade as `thought-bound`: that class
        # means "spine with locked Discovery" to /work-start, and a Mode-C
        # `_PLAN` anchoring artifact has no Discovery at all.
        spine = self._spine(name="modec-20260806223312_PLAN.md")
        ppg.auto_register_topic("sid-1", spine)
        state = json.loads(
            (self.state_dir / "modec__Root.json").read_text())
        self.assertEqual(state["intake_source"], "auto-registered")
        self.assertEqual(
            ppg._classify_intake_source(state, "sid-1", spine, None),
            "worktree-origin")

    def test_R21_thought_spine_still_classifies_as_thought_bound(self):
        spine = self._spine(name="clar-20260806223312_THOUGHT.md")
        ppg.auto_register_topic("sid-1", spine)
        state = json.loads((self.state_dir / "clar__Root.json").read_text())
        self.assertEqual(
            ppg._classify_intake_source(state, "sid-1", spine, None),
            "thought-bound")

    def test_R22_mint_does_not_preset_the_clarification_flag(self):
        # `clarification_phase_todo_registered` attests that the VALIDATED
        # clarification-phase line was registered, and it also gates
        # phase_complete_predicate(state, "clarification"). The mint must NOT
        # pre-set it — that would let a topic clear the clarification-complete
        # gate with no framed line ever registered.
        spine = self._spine()
        ppg.auto_register_topic("sid-1", spine)
        state = json.loads(
            (self.state_dir / "widget-thing__Root.json").read_text())
        self.assertFalse(state.get("clarification_phase_todo_registered"))

    def test_R23_registrar_recognises_an_unframed_auto_line(self):
        # De-duplication at its true locus: register_session_todo must not mint
        # a second line for an already-auto-registered topic, and must NOT
        # credit the unframed auto line as a registration.
        spine = self._spine()
        ppg.auto_register_topic("sid-1", spine)
        state_path = self.state_dir / "widget-thing__Root.json"
        state = json.loads(state_path.read_text())
        res = ppg._existing_slug_tagged_todo_line(
            state, str(self.root / "TODO.md"))
        self.assertIsNotNone(res)
        self.assertIn("[auto-registered]", res)
        self.assertEqual(todo_mod.cmd_validate_framing(res)["status"], "fail")

    def test_R24_registrar_credits_a_framed_line(self):
        # Once a human frames the line, the SAME structural bar every manual
        # [Thought] line passes credits it as the registration.
        spine = self._spine()
        ppg.auto_register_topic("sid-1", spine)
        target = self.root / "TODO.md"
        framed = ("- [ ] [Thought] [widget-thing-20260806223312] **Widget** — "
                  "Problem: x. Context: y. Guiding policy: z. "
                  "Master plan: [[widget-thing-20260806223312_PLAN]].")
        lines = [framed if "[widget-thing-20260806223312]" in ln else ln
                 for ln in target.read_text().splitlines()]
        target.write_text("\n".join(lines) + "\n")
        state = json.loads(
            (self.state_dir / "widget-thing__Root.json").read_text())
        body = ppg._existing_slug_tagged_todo_line(state, str(target))
        self.assertIsNotNone(body)
        self.assertEqual(todo_mod.cmd_validate_framing(body)["status"], "pass")


class AutoRegisterNoAITests(AutoRegisterBase):

    # Binaries that would mean a model/AI call entered the ship path.
    _MODEL_TOKENS = ("claude", "anthropic", "llm", "openai", "gpt")

    def test_R13_no_model_call_in_the_ship_path(self):
        # C5 / G5: the mint is pure deterministic code — no model call anywhere
        # in the ship path. The path IS allowed one subprocess: the bookkeeping
        # lock resolving its repo via `git rev-parse --git-common-dir`. Assert
        # every observed call is git plumbing and none names a model binary.
        import subprocess as _sp
        spine = self._spine()
        calls = []
        real_run = _sp.run

        def spy(*a, **k):
            calls.append(a[0] if a else k.get("args"))
            return real_run(*a, **k)

        # NB: do not also patch Popen — subprocess.run is implemented on top of
        # it, so patching both would trip on run's own internals.
        with mock.patch.object(_sp, "run", side_effect=spy):
            res = ppg.auto_register_topic("sid-1", spine)

        self.assertEqual(res["status"], "registered")
        self.assertEqual(res["todo_line"], "minted")
        for cmd in calls:
            argv = cmd if isinstance(cmd, (list, tuple)) else [str(cmd)]
            joined = " ".join(str(x) for x in argv).lower()
            # S3 (streamed-dancing-goose): the ship path's `create_topic` now reaches
            # the worktree detector — `bash worktree-detect.sh` — because the project
            # root resolves for real cwds (it returned None everywhere before, so
            # the `_in_worktree()` call after it was dormant). That primitive is
            # git plumbing behind a bash wrapper, not a model call; it is the one
            # non-`git` argv this path is allowed.
            is_worktree_detect = (str(argv[0]) == "bash" and len(argv) > 1
                                  and str(argv[1]).endswith("worktree-detect.sh"))
            self.assertTrue(str(argv[0]) == "git" or is_worktree_detect,
                            msg=f"unexpected non-git subprocess in ship path: {argv}")
            if is_worktree_detect:
                # its argv is `bash <hooks dir>/worktree-detect.sh <cwd>`; the hooks
                # dir lives under ~/.claude, so the model-token scan below would
                # trip on the directory name, not on a model. Nothing else in
                # that argv can name a model.
                self.assertEqual(len(argv), 3, argv)
                continue
            for tok in self._MODEL_TOKENS:
                self.assertNotIn(tok, joined,
                                 msg=f"model call leaked into the ship path: {argv}")

    def test_R13b_no_todo_py_subprocess(self):
        # A1's deadlock guard: the mint uses the INTERNAL `todo` API. A
        # `todo.py` SUBPROCESS would re-acquire the bookkeeping lock in a CHILD
        # process while the parent holds it, and deadlock.
        import subprocess as _sp
        spine = self._spine()
        calls = []
        real_run = _sp.run

        def spy(*a, **k):
            calls.append(a[0] if a else k.get("args"))
            return real_run(*a, **k)

        with mock.patch.object(_sp, "run", side_effect=spy):
            ppg.auto_register_topic("sid-1", spine)
        for cmd in calls:
            argv = cmd if isinstance(cmd, (list, tuple)) else [str(cmd)]
            joined = " ".join(str(x) for x in argv)
            self.assertNotIn("todo.py", joined,
                             msg="the mint must never shell out to todo.py")


if __name__ == "__main__":
    unittest.main(verbosity=1)

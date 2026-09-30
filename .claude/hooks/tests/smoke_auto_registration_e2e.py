#!/usr/bin/env python3
"""S-D — end-to-end implementation verification for auto-registration (Mode-C plan).

Drives a NEVER-REGISTERED handoff topic through the whole chain and asserts the
plan's Outcome Claims against the assembled system, not against unit mocks.

Sub-tests (A5):
  1  C1  — a topic that ships without clarification gets a `[Thought]` tracking
           line with no operator action, and a bound topic-state carrying its
           spine
  2  C3  — the retire gate DEFERS while a declared slice is unshipped
  3  C3  — a legacy no-`#### Slices` spine still retires (the A0 fallback: no
           regression, the safe direction preserved)
  4  C2  — the obligation surfaces at all THREE surfaces (SessionStart scan,
           `/close`, the omission Stop hook) and renders identically
  5  C2  — discharge on upgrade: a human-framed line strips the marker, stops
           being reported, and is never re-flagged
  6  C4  — the omission arm blocks on a discoverable artifact + in-window
           commits, and refuses to guess otherwise
  7  C5  — NO model/AI call fires anywhere in the ship path
  8  torn state — a crash after the line write leaves the topic UNBOUND (which
           the omission arm catches), never bound-but-untracked
  9  A5 freshness assertion — DESCOPED, with the reason recorded (see below)

ISOLATION: every sub-test runs against a temp tree with `PROJECTS_ROOT` and
`TOPIC_STATE_DIR` patched (`_active_path()` reads `TOPIC_STATE_DIR` at call time,
so `_active.json` is redirected too). Nothing here WRITES live `TODO.md`, live
topic-state, or the live `~/.claude` namespace — which is also why it passes
cleanly under the `check-verifier-isolation.sh` pre/post pair.

One documented exception, on the READ side: sub-check 4b2 reads the live vault
`TODO.md` twice, before and after a real `todo.py read`, because "this path
mutates nothing" is not a property that can be established against a scratch
tree — `cmd_read` resolves the live vault and ignores the `--cwd` it is handed.
That dependency is named rather than implied; it is a read, never a write, and
the sub-check reports NOT-RUN when the live file is absent instead of comparing
two absent values and calling it a pass. (This paragraph previously said nothing
here read the live `TODO.md`; 4b2 always did.)

Run OUT OF SESSION, from a fresh shell outside the producing session:
    python3 ${KIT_HOOKS_DIR}/tests/smoke_auto_registration_e2e.py

EXIT CODE. Exit 0 = no sub-test FAILED. A sub-test may also report NOT-RUN: it
could not be exercised because something it depends on was absent, which is
neither a pass nor a failure and is counted separately on the OVERALL line. A
NOT-RUN keeps exit 0 by default — matching the skip-by-default rule the shared
live-corpus resolver uses — so a checkout without the corpus is not broken by it.
Set `STRICT_LIVE_CORPUS=1` to demand those sub-checks actually run: a NOT-RUN
then exits 1.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pre_plan_gates as ppg              # noqa: E402
import taskmanagement as tm               # noqa: E402
import framing_obligation as fo           # noqa: E402
import check_work_done_omission as cwo    # noqa: E402
import todo as todo_mod                   # noqa: E402
import _live_corpus as live_corpus        # noqa: E402

TODO_SKELETON = "# TODO\n\n## Now\n\n_(nothing yet)_\n\n## Done\n"

# Three states, not two. A sub-check that could not run is neither a pass nor a
# failure, and recording it as either loses the distinction this harness exists
# to make: a PASS that tested nothing is indistinguishable from a real one.
PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT-RUN"
RESULTS: list[tuple[str, str, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((label, PASS if ok else FAIL, detail))


def not_run(label: str, detail: str) -> None:
    """Record a sub-check that could not be exercised at all.

    Excluded from the failure count and from the passed count, tallied on its
    own on the OVERALL line so it cannot read as a clean run. Keeps exit 0 by
    default; `STRICT_LIVE_CORPUS=1` makes it exit 1 (see the module docstring).
    The `detail` is required, because the whole point is that an unrun check
    says why.
    """
    RESULTS.append((label, NOT_RUN, detail))


class Tree:
    """An isolated Projects tree + state dir with the module globals patched."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sd_smoke_"))
        self.root = self.tmp / "Projects"
        (self.root / "Thoughts").mkdir(parents=True)
        (self.root / "TODO.md").write_text(TODO_SKELETON)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self._p = [mock.patch.object(ppg, "PROJECTS_ROOT", self.root),
                   mock.patch.object(ppg, "TOPIC_STATE_DIR", self.state)]
        for p in self._p:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._p:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False

    # helpers -----------------------------------------------------------
    @property
    def todo(self) -> Path:
        return self.root / "TODO.md"

    def spine(self, name: str, body: str = "# Plan\n") -> Path:
        p = self.root / "Thoughts" / name
        p.write_text(body)
        return p

    def todo_text(self) -> str:
        return self.todo.read_text(encoding="utf-8")


DECLARED_3 = ("# Plan\n\n#### Slices\n\n| ID | Name |\n|----|------|\n"
              "| S1 | one |\n| S2 | two |\n| S3 | three |\n")


# ---------------------------------------------------------------------------
# 1 — C1: the tracking layer appears with no operator action
# ---------------------------------------------------------------------------
def sub1_mint():
    with Tree() as t:
        spine = t.spine("shipped-20260808000001_PLAN.md", DECLARED_3)
        res = ppg.auto_register_topic("sid-smoke-1", spine)
        line_ok = "[shipped-20260808000001]" in t.todo_text()
        st = t.state / "shipped__Root.json"
        state = json.loads(st.read_text()) if st.exists() else {}
        check("1  C1 line auto-minted, no operator action",
              res.get("status") == "registered" and line_ok,
              f"status={res.get('status')} line={line_ok}")
        check("1b C1 topic-state bound WITH its spine",
              state.get("thought_file_path") == str(spine),
              f"thought_file_path={state.get('thought_file_path')}")
        check("1c C1 counter reflects the declared slice count",
              "Sessions: 1/3 done." in t.todo_text(),
              "expected 'Sessions: 1/3 done.' from the 3-row declared table")


# ---------------------------------------------------------------------------
# 2 & 3 — C3: retire defers on unshipped declared slices; legacy still retires
# ---------------------------------------------------------------------------
def sub2_retire_defers():
    with Tree() as t:
        spine = t.spine("partial-20260808000002_THOUGHT.md", DECLARED_3)
        for sid, status in (("S1", "SHIPPED"), ("S2", "SHIPPED")):
            tm.write_slice_row(spine, sid, {"status": status, "sessions": "1/1",
                                            "updated": "2026-08-08"})
        declared = tm.declared_slice_count(spine)
        verdict = tm.all_slices_done(spine)
        check("2  C3 retire DEFERS while a declared slice is unshipped",
              declared == 3 and verdict is False,
              f"declared={declared} rows=2 all_slices_done={verdict}")


def sub3_legacy_still_retires():
    with Tree() as t:
        spine = t.spine("legacy-20260808000003_THOUGHT.md", "# Spine\n")
        for sid in ("A", "B"):
            tm.write_slice_row(spine, sid, {"status": "SHIPPED", "sessions": "1/1",
                                            "updated": "2026-08-08"})
        declared = tm.declared_slice_count(spine)
        verdict = tm.all_slices_done(spine)
        check("3  C3 legacy no-declared-table spine STILL retires (A0 fallback)",
              declared == 0 and verdict is True,
              f"declared={declared} all_slices_done={verdict}")


# ---------------------------------------------------------------------------
# 4 — C2: the obligation surfaces at all three surfaces
# ---------------------------------------------------------------------------
def sub4_three_surfaces():
    with Tree() as t:
        ppg.auto_register_topic(
            "sid-smoke-4", t.spine("obl-20260808000004_PLAN.md", DECLARED_3))

        # (a) the shared reconcile/render pair — what every surface calls
        shared = fo.render_surface(fo.reconcile([str(t.todo)], strip=False))
        check("4a C2 shared surface reports the obligation",
              bool(shared) and "obl-20260808000004" in shared,
              (shared or "(none)")[:90])

        # (b) SessionStart — `todo.py`'s read builder. NOTE: `cmd_read` resolves
        # the LIVE vault and ignores a scratch `cwd`, so it cannot be pointed at
        # this tree. It is verified two ways instead, neither of which can
        # mutate live state in this smoke:
        #   (b1) structurally — the builder calls the same shared pair 4a proved;
        #   (b2) live-safe — a real `todo.py read` exits 0 and leaves every live
        #        TODO.md byte-identical (there are no marked lines to discharge,
        #        so the documented strip-on-read is a no-op here).
        src = Path(todo_mod.__file__).read_text(encoding="utf-8")
        wired = ("import framing_obligation as _fo" in src
                 and "_fo.reconcile(" in src and "_fo.render_surface(" in src)
        check("4b1 C2 SessionStart builder calls the shared reconcile+render",
              wired, "todo.py read builder wiring")

        # The comparison target comes from the NON-OVERRIDABLE live-vault
        # accessor, which resolves the way `cmd_read` does — through
        # `todo.find_vault_root` and its absolute VAULT_ROOT_FALLBACK. It is
        # deliberately NOT taken from the overridable corpus resolver: an
        # override pointing at some other populated tree would compare a file
        # the code under test never writes, and the sub-check would pass
        # trivially — reinstating the exact false green this repair removes.
        #
        # And when that file is absent, this reports NOT-RUN. It used to read
        # `None` on both sides, find them equal, and report PASS having tested
        # nothing.
        label_4b2 = "4b2 C2 SessionStart path runs clean and mutates nothing"
        live_todo = live_corpus.live_vault_todo()
        live_root = live_todo.parent
        if not live_todo.is_file():
            not_run(label_4b2,
                    f"NOT RUN: no live vault TODO.md at {live_todo}, so there is "
                    "nothing to compare and nothing is claimed about mutation. "
                    "This sub-check reads the file `todo.py read` itself "
                    f"resolves; set {live_corpus.STRICT_ENV}=1 to make an unrun "
                    "check a failure.")
        else:
            before_bytes = live_todo.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(Path(todo_mod.__file__)), "read",
                 "--cwd", str(live_root)],
                capture_output=True, text=True, timeout=30)
            after_bytes = live_todo.read_bytes() if live_todo.is_file() else None
            if proc.returncode != 0:
                detail = (f"FAILED — the SessionStart read exited {proc.returncode} "
                          f"(expected 0): {(proc.stderr or '').strip()[:120]}")
            elif after_bytes is None:
                detail = (f"FAILED — MUTATION: {live_todo} existed before the read "
                          "and is gone after it.")
            elif before_bytes != after_bytes:
                detail = (f"FAILED — MUTATION: the SessionStart read changed "
                          f"{live_todo} ({len(before_bytes)} -> "
                          f"{len(after_bytes)} bytes).")
            else:
                detail = (f"rc=0, {live_todo} byte-identical across the read "
                          f"({len(before_bytes)} bytes read from the same path "
                          "`cmd_read` resolves)")
            check(label_4b2,
                  proc.returncode == 0 and after_bytes is not None
                  and before_bytes == after_bytes,
                  detail)

        # (c) the omission Stop hook's additive soft-warn, over the same pair
        with mock.patch.object(fo, "default_todo_paths",
                               return_value=[str(t.todo)]):
            warn = cwo._framing_soft_warn()
        check("4c C2 omission Stop hook surfaces it (additive soft-warn)",
              bool(warn) and "obl-20260808000004" in (warn or ""),
              (warn or "(none)")[:90])

        # (d) `/close` runs the same module through its CLI
        cli = subprocess.run(
            [sys.executable, str(Path(ppg.__file__).parent / "framing_obligation.py"),
             "surface", "--todo-file", str(t.todo)],
            capture_output=True, text=True)
        check("4d C2 /close CLI surface renders the same block",
              cli.returncode == 0 and "obl-20260808000004" in cli.stdout,
              cli.stdout.strip()[:90] or cli.stderr.strip()[:90])


# ---------------------------------------------------------------------------
# 5 — C2: discharge on upgrade, and never re-flagged
# ---------------------------------------------------------------------------
def sub5_discharge():
    with Tree() as t:
        ppg.auto_register_topic(
            "sid-smoke-5", t.spine("disc-20260808000005_PLAN.md", DECLARED_3))
        tag = "[disc-20260808000005]"

        before = fo.reconcile([str(t.todo)])
        # The human frames the TEXT and leaves the marker alone — stripping it
        # is the reconcile's job, and that is precisely what this asserts.
        framed = ("- [ ] [Thought] " + fo.MARKER + " " + tag
                  + " **Disc** — Problem: p. Context: c. Guiding policy: g. "
                  "Master plan: [[disc-20260808000005_PLAN]].")
        t.todo.write_text("\n".join(
            framed if tag in ln else ln
            for ln in t.todo_text().splitlines()) + "\n")

        after = fo.reconcile([str(t.todo)])
        again = fo.reconcile([str(t.todo)])
        text = t.todo_text()
        check("5a C2 unframed line is reported outstanding, marker kept",
              len(before["outstanding"]) == 1 and not before["discharged"],
              f"outstanding={len(before['outstanding'])}")
        check("5b C2 framed line DISCHARGES and its marker is stripped",
              len(after["discharged"]) == 1 and fo.MARKER not in text
              and tag in text,
              f"discharged={len(after['discharged'])} marker_gone="
              f"{fo.MARKER not in text} line_kept={tag in text}")
        check("5c C2 discharged line is NEVER re-flagged",
              again["scanned"] == 0 and fo.render_surface(again) is None,
              f"scanned={again['scanned']}")
        body = next(ln for ln in text.splitlines() if tag in ln)
        body = body.split("- [ ]", 1)[1].strip()
        check("5d C2 discharge bar == the manual [Thought] bar",
              todo_mod.cmd_validate_framing(body)["status"] == "pass",
              "framed body passes todo.cmd_validate_framing")


# ---------------------------------------------------------------------------
# 6 — C4: the omission arm blocks / refuses to guess
# ---------------------------------------------------------------------------
def sub6_omission_arm():
    """The omission arm fires on a discoverable artifact plus in-window commits,
    and refuses to guess without one.

    THE SHAPE OF "FIRES" IS DECIDED BY `check_work_done_omission.REPORT_ONLY`,
    NOT BY THIS FILE. That constant ships as `True`, which routes every block
    through `_report_only` — setting `block: False`, `report_only: True`, and
    `message: None`, and carrying the block text on `would_block_message`
    instead. This sub-check therefore READS the constant and asserts the shape
    it implies, so flipping it moves the assertion rather than breaking it.

    Why the constant is True: the gate was made to SPEAK before it ACTS, because
    fixing the plan-reference resolver gave it file scope on six topics where it
    had never had any, and taking six silent topics straight to six blocking ones
    with nobody having read the output is not a safe change
    (`check_work_done_omission.py:321-341`). Flipping it is a separately
    registered follow-up whose stated precondition is narrowing
    `_loose_scope_match` first — it is not this file's to do, and this file must
    not encode one side of it.

    Both arms run here: 6a exercises the shipped default, 6a2 patches the
    constant off to keep the block path reachable — the same technique
    `test_check_work_done_omission.py:139` and `test_framing_obligation.py:309`
    already use.
    """
    with Tree() as t:
        spine = t.spine("omit-20260808000006_PLAN.md", DECLARED_3)
        fire_patches = lambda: (                                  # noqa: E731
            mock.patch.object(cwo, "_active_topic", return_value=(None, None)),
            mock.patch.object(cwo, "_discoverable_spine", return_value=spine),
            mock.patch.object(cwo, "_session_started_at",
                              return_value="2026-08-08T00:00:00+00:00"),
            mock.patch.object(cwo, "_commits_present", return_value=True),
        )
        a, b, c, d = fire_patches()
        with a, b, c, d:
            shipped = cwo.decide("sid-smoke-6")
        a, b, c, d = fire_patches()
        with mock.patch.object(cwo, "REPORT_ONLY", False), a, b, c, d:
            flipped = cwo.decide("sid-smoke-6")
        with mock.patch.object(cwo, "_active_topic", return_value=(None, None)), \
             mock.patch.object(cwo, "_discoverable_spine", return_value=None):
            passed = cwo.decide("sid-smoke-6")

        report_only = cwo.REPORT_ONLY
        if report_only:
            # Report-only: the block is converted, not skipped. Asserting
            # `report_only is True` and the would-block keys is what separates
            # "passed because reporting is all it may do" from "passed with
            # nothing to report" — an early return that tested nothing.
            carried = shipped.get("would_block_message") or ""
            ok_a = (shipped.get("block") is False
                    and shipped.get("report_only") is True
                    and "omit-20260808000006_PLAN.md" in carried
                    and "auto-register" in carried)
            detail_a = (
                f"REPORT_ONLY={report_only} -> expected block=False, "
                f"report_only=True, artifact+auto-register on would_block_message; "
                f"got block={shipped.get('block')} "
                f"report_only={shipped.get('report_only')} "
                f"would_block_message={'set' if carried else 'EMPTY'}")
        else:
            carried = shipped.get("message") or ""
            ok_a = (shipped.get("block") is True
                    and "omit-20260808000006_PLAN.md" in carried
                    and "auto-register" in carried)
            detail_a = (
                f"REPORT_ONLY={report_only} -> expected block=True with "
                f"artifact+auto-register on message; got "
                f"block={shipped.get('block')} "
                f"message={'set' if carried else 'EMPTY'}")
        check("6a C4 arm FIRES on artifact + in-window commits, in the shape "
              "the shipped REPORT_ONLY constant implies", ok_a, detail_a)

        flipped_msg = flipped.get("message") or ""
        check("6a2 C4 the BLOCK path stays reachable with REPORT_ONLY off",
              flipped.get("block") is True
              and "omit-20260808000006_PLAN.md" in flipped_msg
              and "auto-register" in flipped_msg,
              "with the constant patched False, expected block=True with "
              "artifact+auto-register on message; got "
              f"block={flipped.get('block')} "
              f"message={'set' if flipped_msg else 'EMPTY'} — if this fails the "
              "block path itself regressed, independently of the shipped default")
        check("6b C4 arm refuses to guess with no anchoring artifact",
              passed.get("block") is False
              and "refusing to guess" in passed.get("reason", ""),
              passed.get("reason", "")
              or "expected a 'refusing to guess' reason and got none")


# ---------------------------------------------------------------------------
# 7 — C5: no model/AI call anywhere in the ship path
# ---------------------------------------------------------------------------
_MODEL_TOKENS = ("claude", "anthropic", "llm", "openai", "gpt")


def sub7_no_model_call():
    with Tree() as t:
        spine = t.spine("noai-20260808000007_PLAN.md", DECLARED_3)
        calls: list = []
        real_run = subprocess.run

        def spy(*a, **k):
            calls.append(a[0] if a else k.get("args"))
            return real_run(*a, **k)

        with mock.patch.object(subprocess, "run", side_effect=spy):
            res = ppg.auto_register_topic("sid-smoke-7", spine)
        offenders = []
        for cmd in calls:
            argv = cmd if isinstance(cmd, (list, tuple)) else [str(cmd)]
            joined = " ".join(str(x) for x in argv).lower()
            if str(argv[0]) != "git" or any(tok in joined for tok in _MODEL_TOKENS):
                offenders.append(argv)
        check("7  C5 NO model/AI call in the ship path",
              res.get("status") == "registered" and not offenders,
              f"{len(calls)} subprocess call(s), all git plumbing"
              if not offenders else f"offenders={offenders}")


# ---------------------------------------------------------------------------
# 8 — torn state: crash after the line, before the binding
# ---------------------------------------------------------------------------
def sub8_torn_state():
    with Tree() as t:
        spine = t.spine("torn-20260808000008_PLAN.md", DECLARED_3)
        with mock.patch.object(ppg, "_write_topic_state",
                               side_effect=RuntimeError("crash between steps")):
            res = ppg.auto_register_topic("sid-smoke-8", spine)
        bound = (t.state / "torn__Root.json").exists()
        active = t.state / "_active.json"
        active_data = json.loads(active.read_text()) if active.exists() else {}
        check("8a torn mint leaves the topic UNBOUND (never bound-but-untracked)",
              res.get("status") == "partial" and not bound
              and "sid-smoke-8" not in active_data,
              f"status={res.get('status')} state_file={bound}")
        # and a re-run REPAIRS it rather than skipping the binding
        res2 = ppg.auto_register_topic("sid-smoke-8", spine)
        check("8b re-run repairs the torn mint (binds, no duplicate line)",
              res2.get("todo_line") == "already_present"
              and res2.get("topic_state") == "bound"
              and t.todo_text().count("[torn-20260808000008]") == 1,
              f"line={res2.get('todo_line')} state={res2.get('topic_state')}")


# ---------------------------------------------------------------------------
# 9 — A5's freshness assertion: DESCOPED, reason recorded
# ---------------------------------------------------------------------------
def sub9_freshness_descoped():
    """The plan's A5 asserts the minted `Sessions: M/N` is folded by
    `_phase_progress_fingerprint` so a previously false-FRESH handoff stops
    being static. That is NOT deliverable by this design, for two independent
    reasons this sub-test PINS so the gap cannot be quietly forgotten:

      (i)  the fingerprint reads its `Sessions:` counter from the SPINE bytes it
           is handed; the mint writes that counter only into the `TODO.md` line;
      (ii) the fingerprint returns None outright for a spine with no
           `# Solution Design` section — i.e. exactly the Mode-C `_PLAN`
           anchoring artifact this plan targets.

    Delivering it would require writing a counter into the spine, which is
    outside S-B/S-C's scope. Recorded as an open follow-up.
    """
    with Tree() as t:
        plan_text = DECLARED_3
        fp_plan = ppg._phase_progress_fingerprint(plan_text)
        spine_text = ("# Spine\n\n# Solution Design\n\n"
                      "### Solution Alternative 1\n\nbody\n")
        fp_a = ppg._phase_progress_fingerprint(spine_text)
        fp_b = ppg._phase_progress_fingerprint(
            spine_text + "\nSessions: 1/3 done.\n")
        check("9a A5 freshness: Mode-C plan has NO fingerprint to advance",
              fp_plan is None,
              "no '# Solution Design' section -> _phase_progress_fingerprint None")
        check("9b A5 freshness: the counter must be in the SPINE to matter",
              fp_a != fp_b,
              "spine-side counter changes the fingerprint; a TODO-line counter "
              "never reaches it -> A5 freshness assertion DESCOPED (follow-up)")


def main() -> int:
    for fn in (sub1_mint, sub2_retire_defers, sub3_legacy_still_retires,
               sub4_three_surfaces, sub5_discharge, sub6_omission_arm,
               sub7_no_model_call, sub8_torn_state, sub9_freshness_descoped):
        try:
            fn()
        except Exception as e:                      # a crashed sub-test is a FAIL
            check(f"{fn.__name__} raised", False, f"{type(e).__name__}: {e}")

    print("\n=== S-D AUTO-REGISTRATION E2E RESULTS ===")
    for label, state, detail in RESULTS:
        print(f"  [{state}] {label}")
        if detail:
            print(f"         {detail}")

    failed = sum(1 for _, state, _ in RESULTS if state == FAIL)
    unrun = sum(1 for _, state, _ in RESULTS if state == NOT_RUN)
    # Passed is total minus failed minus not-run. A not-run is NOT a pass: the
    # old `len(RESULTS) - failed` counted it as one, which is the arithmetic
    # form of the false green this harness now refuses.
    passed = len(RESULTS) - failed - unrun

    print(f"\n  {passed}/{len(RESULTS)} sub-checks passed"
          + (f", {unrun} NOT-RUN (nothing was proved by those)" if unrun else ""))

    strict = live_corpus.strict()
    if failed:
        overall = f"FAIL ({failed})"
    elif unrun and strict:
        overall = (f"FAIL ({unrun} NOT-RUN, and {live_corpus.STRICT_ENV}=1 "
                   "demands they run)")
    elif unrun:
        overall = (f"PASS ({unrun} NOT-RUN — set {live_corpus.STRICT_ENV}=1 to "
                   "treat an unrun sub-check as a failure)")
    else:
        overall = "PASS"
    print("OVERALL: " + overall)
    print("=== END ===")
    if failed:
        return 1
    if unrun and strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

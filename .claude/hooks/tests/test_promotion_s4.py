#!/usr/bin/env python3
"""S4 acceptance test — git-working-model automated promotion worker (PromotionAdapter).

Every harness-specific concurrency / safety mechanic the frozen DESIGN pins
(A8/A9/A10/A15) is EXERCISED, not asserted in prose (the plan Guiding Policy:
"every concurrency mechanic must be a TESTED implementation — prose can't run").
Runs entirely in isolated, disposable `git init` scratch repos + fake live-target
dirs under MANAGED temp dirs (tempfile.TemporaryDirectory — auto-cleaned, NEVER
`rm -rf`). config-source + the network push/PR/merge tail are FAKED via injectable
command overrides (capture_cmd / apply_cmd / network_tail_cmd / green_tag_cmd),
exactly as the S3 suite fakes `check_cmd`. No live `~/.claude` / `~/repos` is
touched (P13 redirects the whole verifier-isolation guard via
VERIFIER_ISOLATION_HOME to a scratch config dir).

Covers P1–P18:
  P1  nothing-to-promote → noop (staging matches live for the session scope)
  P2  session-scoped capture — only the declared paths are staged
  P3  no-go → the ephemeral workspace is discarded; shared source untouched
  P4  go → reconciles the FROZEN ephemeral commit, deploys to live
  P5  post-approval TOCTOU → reconcile the frozen commit onto the advanced HEAD
  P6  reconcile conflict → abort; no MERGE_HEAD; shared source untouched
  P7  source-lock serializes concurrent promotions (held lock → timeout; free → applies)
  P8  path-scoped apply preserves a concurrent NON-session live edit
  P9  scope-absent — non-TTY refuses (fail-closed); confirmed subset stages only that subset
  P10 harness rollback — source revert (never reset) + re-apply; conflict → both untouched
  P11 neither lock spans the network; deploy-mutex is a distinct lock
  P12 crash during capture → worker teardown leaves no worktree/tempdir
  P13 verifier-isolation arming — bootstrap arms pre/post; injected drift caught (exit 2)
  P14 deletion-safe capture — a removed in-scope file's deletion is promoted
  P15 frozen-candidate integrity — persists across go/no-go; post-approval live edit ignored
  P16 promote ordering — deploy only AFTER the merge; extra-safety holds with no deploy
  P17 the CLI with NO seams wired — the PRODUCTION configuration — refuses by name
      before anything is written (land-port-tested-configuration S2 / A4)
  P18 the seam guard sits on EVERY guarded method, not only the one the CLI reaches
      first — `apply()` and `run()` refuse when called directly (A4's boundary-
      placement argument, which was asserted and untested until an independent
      check said so)

WHAT A GREEN COUNT HERE DOES AND DOES NOT MEAN — read this before citing it.

P1–P16 fake every production seam. `config-source` (capture_cmd / apply_cmd) and the
push→PR→merge network tail (network_tail_cmd), and the `green-<ts>` recovery-tag
emitter (green_tag_cmd), are all injected. So these tests grade the adapter's
ORCHESTRATION — lock boundaries, ordering, scoping, teardown, TOCTOU — against
doubles. They are `real-to-test` in the growth sequence
(`code_first_architecture.md` §"Growth sequence inside the port"), which is step
2 of 4, and they say NOTHING about whether the worker works against real config-source,
a real remote, or a real `gh`.

This distinction is not pedantry: for four slices `green_tag_cmd` was injected at
2 of 17 sites and its production `None` branch ran unasserted, so a promotion
driven through this worker laid no recovery tag while this suite reported green —
and a runbook row cited that green count as evidence the worker had shipped.

P17 is the only test here that exercises the production configuration, and what
it asserts is a REFUSAL. The worker is not wired for production use;
`claude-promote` is the harness's staging→prod path (git-policy.md §2).

`real-to-real` for the network tail is not reachable offline at all — it needs
`gh` and a live remote — and the two config-source seams are only conditionally
testable, where skip-on-missing-binary would be green-by-absence, the same
pathology one level down. That ceiling is stated here rather than papered over
with a passing count.

Run:  python3 hooks/tests/test_promotion_s4.py
Exit: 0 all green; 1 any failure.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HOOKS))

import bookkeeping_lock  # noqa: E402
import land_port  # noqa: E402
from land_port import PromotionAdapter  # noqa: E402

CTX = mp.get_context("fork")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def git(cwd, *args, check_rc: bool = True) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["GIT_EDITOR"] = "true"
    env["GIT_PAGER"] = "cat"
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, env=env)
    if check_rc and r.returncode != 0:
        raise RuntimeError(f"git {args} failed: {r.stderr}")
    return r


def sha(repo, ref="main") -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def is_ancestor(repo, anc, desc="main") -> bool:
    return git(repo, "merge-base", "--is-ancestor", anc, desc, check_rc=False).returncode == 0


def seed_source(root: Path) -> Path:
    """A git repo standing in for the config source (live ~/.claude captured in)."""
    src = root / "source"
    (src / "hooks").mkdir(parents=True)
    git(src, "init", "-q", "-b", "main")
    git(src, "config", "user.email", "t@t")
    git(src, "config", "user.name", "t")
    (src / "hooks" / "mod.py").write_text("live v0\n")
    (src / "settings.json").write_text("{}\n")
    (src / "other.py").write_text("other v0\n")          # a NON-session managed file
    git(src, "add", "-A")
    git(src, "commit", "-qm", "seed source")
    return src


def seed_target(root: Path) -> Path:
    """A plain dir standing in for the live deploy target (~/.claude), pre-seeded
    to the deployed state (matches source HEAD)."""
    tgt = root / "live"
    (tgt / "hooks").mkdir(parents=True)
    (tgt / "hooks" / "mod.py").write_text("live v0\n")
    (tgt / "settings.json").write_text("{}\n")
    (tgt / "other.py").write_text("other v0\n")
    return tgt


def make_capture(edits: dict):
    """Fake the capture step: mutate the ephemeral workspace worktree to reflect
    the session's live edits. edits: path -> content (str) or None (delete)."""
    def _cap(workspace, scope):
        for p, content in edits.items():
            fp = Path(workspace) / p
            if content is None:
                if fp.exists():
                    fp.unlink()
            else:
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
    return _cap


def make_apply():
    """Fake PATH-SCOPED the deploy step: deploy ONLY the scoped paths from the
    pinned checkout to the live target (add/modify/delete); never touch others."""
    def _apply(pinned, scope, target):
        for p in scope:
            src_f = Path(pinned) / p
            dst_f = Path(target) / p
            if src_f.exists():
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                dst_f.write_text(src_f.read_text())
            elif dst_f.exists():
                dst_f.unlink()
    return _apply


def make_tail(outcome: str = "merged"):
    """Fake claude-promote network tail. extra-safety → HOLD (no merge, no deploy)."""
    def _tail(pr_mode):
        if pr_mode == "extra-safety":
            return "hold"
        return outcome
    return _tail


GREEN_TAG = "green-test"


def make_green_tag(repo, name: str = GREEN_TAG):
    """Fake the `green-<ts>` recovery-tag emitter by laying a REAL git tag.

    Every site below declares this seam rather than leaving it None. That is the
    point of the re-wiring, not a formality: `green_tag_cmd` was injected at 2 of
    17 sites, so its production `None` branch ran unasserted about six times and a
    promotion driven through this worker laid no recovery tag while the suite
    stayed green. A test that reaches a phase only because the guard cannot see it
    is the same defect one level down.

    It lays a real tag so a test can observe the ARTIFACT rather than the trace
    token — see P11, which asserted the token and therefore passed either way.
    """
    def _tag():
        git(repo, "tag", "-f", name)
    return _tag


def has_green_tag(repo, name: str = GREEN_TAG) -> bool:
    return git(repo, "rev-parse", "--verify", "-q", f"refs/tags/{name}",
               check_rc=False).returncode == 0


def _hold_lock(target, held_file, secs):
    with bookkeeping_lock.bookkeeping_lock(str(target)):
        Path(held_file).write_text("held")
        time.sleep(secs)


# ─────────────────────────────────────────────────────────────────────────────

def p1_noop():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v0\n"}),
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             tmp_root=d)
        try:
            dr = a.dry_run_diff()
            check("P1 nothing-to-promote → noop", dr.status == "noop", f"status={dr.status}")
        finally:
            a.teardown()


def p2_scoped_capture():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))
        cap = make_capture({"hooks/mod.py": "live v1\n", "other.py": "SHOULD NOT STAGE\n"})
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"], capture_cmd=cap,
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src), tmp_root=d)
        try:
            dr = a.dry_run_diff()
            changed = git(src, "--no-pager", "diff", "--name-only",
                          a.source_at_build, a.candidate_sha).stdout.split()
            check("P2 scoped capture stages ONLY the declared paths",
                  dr.status == "green" and changed == ["hooks/mod.py"],
                  f"status={dr.status} candidate_changed={changed}")
        finally:
            a.teardown()


def p3_nogo_discards():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))
        before = sha(src)
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src), tmp_root=d)
        try:
            dr = a.dry_run_diff()
        finally:
            a.teardown()
        leaked_wt = "promo-ws-" in git(src, "worktree", "list").stdout
        orphan = list(Path(d).glob("promo-ws-*"))
        check("P3 no-go → workspace discarded; shared source untouched",
              dr.status == "green" and sha(src) == before and not leaked_wt and not orphan,
              f"src_unchanged={sha(src)==before} leaked_wt={leaked_wt} orphan={len(orphan)}")


def p4_go_reconciles():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            res = a.run(approve=True)
            deployed = (tgt / "hooks" / "mod.py").read_text()
            src_mod = git(src, "show", "main:hooks/mod.py").stdout
            check("P4 go → reconciles the frozen commit + deploys to live",
                  res.status == "promoted" and deployed == "live v1\n" and src_mod == "live v1\n",
                  f"status={res.status} deployed={deployed!r} src={src_mod!r}")
        finally:
            a.teardown()


def p5_toctou_reconcile():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            dr = a.dry_run_diff()
            # concurrent source advance during the (lockless) go/no-go — a DIFFERENT file.
            (src / "other.py").write_text("advanced\n")
            git(src, "add", "-A"); git(src, "commit", "-qm", "concurrent source advance")
            adv = sha(src)
            ap = a.apply()
            mod = git(src, "show", "main:hooks/mod.py").stdout
            other = git(src, "show", "main:other.py").stdout
            check("P5 post-approval TOCTOU → reconcile the frozen commit onto the advanced HEAD",
                  dr.status == "green" and ap.status == "applied"
                  and mod == "live v1\n" and other == "advanced\n"
                  and is_ancestor(src, adv),
                  f"ap={ap.status} mod={mod!r} other={other!r}")
        finally:
            a.teardown()


def p6_reconcile_conflict_abort():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "candidate change\n"}),
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src), tmp_root=d)
        try:
            dr = a.dry_run_diff()
            # concurrent advance changes the SAME file differently → 3-way merge conflict.
            (src / "hooks" / "mod.py").write_text("advance change\n")
            git(src, "add", "-A"); git(src, "commit", "-qm", "conflicting advance")
            adv = sha(src)
            ap = a.apply()
            merge_head = (src / ".git" / "MERGE_HEAD").exists()
            check("P6 reconcile conflict → abort; no MERGE_HEAD; shared source untouched",
                  dr.status == "green" and ap.status == "conflict"
                  and not merge_head and sha(src) == adv,
                  f"ap={ap.status} MERGE_HEAD={merge_head} src_at_adv={sha(src)==adv}")
        finally:
            a.teardown()


def p7_source_lock_serializes():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             lock_timeout=0.5, tmp_root=d)
        try:
            dr = a.dry_run_diff()
            held = Path(d) / "held.flag"
            proc = CTX.Process(target=_hold_lock, args=(a.source_lock_target, str(held), 2.0))
            proc.start()
            while not held.exists() and proc.is_alive():
                time.sleep(0.02)
            contended = a.apply()             # source lock held elsewhere → timeout
            proc.join(5)
            freed = a.apply()                 # lock now free → the frozen candidate applies
            check("P7 source-lock serializes concurrent promotions",
                  dr.status == "green" and contended.status == "lock-timeout"
                  and freed.status == "applied",
                  f"contended={contended.status} after_release={freed.status}")
        finally:
            a.teardown()


def p8_path_scoped_apply_preserves():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        # a concurrent session's live edit to a NON-session file, not yet captured:
        (tgt / "other.py").write_text("CONCURRENT LIVE EDIT\n")
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            res = a.run(approve=True)
            preserved = (tgt / "other.py").read_text() == "CONCURRENT LIVE EDIT\n"
            deployed = (tgt / "hooks" / "mod.py").read_text() == "live v1\n"
            check("P8 path-scoped apply preserves a concurrent NON-session live edit",
                  res.status == "promoted" and preserved and deployed,
                  f"status={res.status} preserved={preserved} deployed={deployed}")
        finally:
            a.teardown()


def p9_scope_absent():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        # (a) no session scope + non-TTY → REFUSE (fail-closed, A9/M4).
        r = subprocess.run(
            [sys.executable, str(HOOKS / "land_port.py"), "promote",
             "--source", str(src), "--target", str(tgt)],
            capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        refused = r.returncode == 5 and "REFUSED" in (r.stdout + r.stderr)
        # (b) list-and-confirm: the operator's confirmed subset IS the scope; the
        #     adapter stages exactly that subset — never a whole-tree sweep.
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src), tmp_root=d)
        try:
            dr = a.dry_run_diff()
            subset_only = dr.changed_paths == ["hooks/mod.py"]
        finally:
            a.teardown()
        check("P9 scope-absent: non-TTY refuses (fail-closed); confirmed subset stages only that subset",
              refused and dr.status == "green" and subset_only,
              f"refused={refused}(exit={r.returncode}) subset={dr.changed_paths}")


def p10_rollback():
    # (i) clean rollback — inverse commit (history preserved) + reverted re-apply.
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            res = a.run(approve=True)
            promo_sha = res.merge_sha
            pre = sha(src)
            rb = a.rollback(promo_sha)
            src_mod = git(src, "show", "main:hooks/mod.py").stdout
            tgt_mod = (tgt / "hooks" / "mod.py").read_text()
            clean_ok = (res.status == "promoted" and rb.status == "rolled-back"
                        and sha(src) != pre                    # a NEW inverse commit (no reset)
                        and is_ancestor(src, promo_sha)        # history preserved
                        and src_mod == "live v0\n" and tgt_mod == "live v0\n")
        finally:
            a.teardown()
    # (ii) revert conflict → abort; BOTH surfaces untouched; no REVERT_HEAD.
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            res = a.run(approve=True)
            promo_sha = res.merge_sha
            # a further source change to the same file makes reverting promo_sha conflict.
            (src / "hooks" / "mod.py").write_text("even newer\n")
            git(src, "add", "-A"); git(src, "commit", "-qm", "post-promo change")
            src_before = sha(src)
            tgt_before = (tgt / "hooks" / "mod.py").read_text()
            rb = a.rollback(promo_sha)
            revert_head = (src / ".git" / "REVERT_HEAD").exists()
            conflict_ok = (rb.status == "revert-conflict" and not revert_head
                           and sha(src) == src_before
                           and (tgt / "hooks" / "mod.py").read_text() == tgt_before)
        finally:
            a.teardown()
    check("P10 rollback: clean revert re-applies + preserves history; conflict → both untouched",
          clean_ok and conflict_ok, f"clean={clean_ok} conflict={conflict_ok}")


def p11_lock_boundaries():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        trace: list = []
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src), deploy_target=str(tgt),
                             trace=trace, tmp_root=d)
        try:
            res = a.run(approve=True)
        finally:
            a.teardown()

        def idx(e):
            return trace.index(e) if e in trace else -1

        src_release_before_push = 0 <= idx("source_lock:release") < idx("push")
        deploy_lock_after_merge = idx("deploy_lock:acquire") > idx("pr_merge") >= 0
        deploy_release_before_tag = 0 <= idx("deploy_lock:release") < idx("green_tag")
        distinct = (bookkeeping_lock.lock_path_for(a.source_lock_target)
                    != bookkeeping_lock.lock_path_for(a.deploy_lock_target))
        # A7: observe the ARTIFACT, not the trace token. This assertion used to
        # read only `idx("green_tag")`, which `_record` emitted BEFORE the seam ran
        # — so it passed identically whether a tag was laid or not, and certified
        # nothing about the recovery baseline it names.
        tag_laid = has_green_tag(src)
        check("P11 neither lock spans the network; deploy-mutex is a distinct lock; "
              "the recovery tag is actually laid",
              res.status == "promoted" and src_release_before_push
              and deploy_lock_after_merge and deploy_release_before_tag and distinct
              and tag_laid,
              f"trace={trace} distinct={distinct} tag_laid={tag_laid}")


def p12_teardown_on_crash():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))

        def boom(ws, scope):
            raise RuntimeError("capture crashed")

        a = PromotionAdapter(src, session_scope=["hooks/mod.py"], capture_cmd=boom,
                             network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src), tmp_root=d)
        crashed = False
        try:
            a.dry_run_diff()
        except Exception:
            crashed = True
        finally:
            a.teardown()
        leaked_wt = "promo-ws-" in git(src, "worktree", "list").stdout
        orphan = list(Path(d).glob("promo-ws-*"))
        check("P12 crash during capture → worker teardown leaves no worktree/tempdir",
              crashed and not leaked_wt and not orphan,
              f"crashed={crashed} leaked_wt={leaked_wt} orphan={len(orphan)}")


def p13_verifier_isolation_arming():
    with tempfile.TemporaryDirectory() as d:
        home = Path(d) / "cfg"; (home / "hooks").mkdir(parents=True)
        (home / "settings.json").write_text('{"x":1}\n')
        (home / "hooks" / "probe.sh").write_text("echo probe\n")
        guard = str(HOOKS / "check-verifier-isolation.sh")
        env = dict(os.environ)
        env["VERIFIER_ISOLATION_HOME"] = str(home)         # redirect the WHOLE guard — no live touch
        env.pop("CLAUDE_CODE_REMOTE", None)
        cmd = ": bootstrap-promote; echo deploy"           # bootstrap token arms is_dangerous
        jin = json.dumps({"tool_name": "Bash", "session_id": "p13",
                          "tool_input": {"command": cmd}})

        def run_guard(mode):
            return subprocess.run(["bash", guard, mode], input=jin,
                                  capture_output=True, text=True, env=env)

        # clean run: arm (pre) → no drift → post exits 0
        pre_c = run_guard("pre"); post_c = run_guard("post")
        clean_ok = pre_c.returncode == 0 and post_c.returncode == 0
        # drift run: arm (pre) → mutate config between pre and post → post exits 2
        pre_dd = run_guard("pre")
        (home / "settings.json").write_text('{"x":2}\n')   # inject drift
        post_dd = run_guard("post")
        drift_caught = pre_dd.returncode == 0 and post_dd.returncode == 2
        check("P13 verifier-isolation: bootstrap arms pre/post; injected drift caught (exit 2), clean run clean",
              clean_ok and drift_caught,
              f"clean(pre={pre_c.returncode},post={post_c.returncode}) "
              f"drift(pre={pre_dd.returncode},post={post_dd.returncode})")


def p14_deletion_safe():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": None}),   # DELETE in-scope
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            res = a.run(approve=True)
            src_has = git(src, "cat-file", "-e", "main:hooks/mod.py", check_rc=False).returncode == 0
            tgt_gone = not (tgt / "hooks" / "mod.py").exists()
            check("P14 deletion-safe capture: a removed in-scope file's deletion is promoted",
                  res.status == "promoted" and not src_has and tgt_gone,
                  f"status={res.status} src_has={src_has} tgt_gone={tgt_gone}")
        finally:
            a.teardown()


def p15_frozen_candidate_integrity():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), tmp_root=d)
        try:
            dr = a.dry_run_diff()
            frozen = a.candidate_sha
            # a post-approval in-scope LIVE edit (what a naive re-capture would pick up):
            # mutate the workspace working tree. apply() must IGNORE it — it reconciles
            # the FROZEN commit and never re-reads the live FS.
            (Path(a._workspace) / "hooks" / "mod.py").write_text("live v2 SNEAK\n")
            ap = a.apply()
            pr = a.promote()
            deployed = (tgt / "hooks" / "mod.py").read_text()
            check("P15 frozen candidate persists across go/no-go; post-approval live edit ignored",
                  dr.status == "green" and ap.status == "applied" and pr.status == "promoted"
                  and deployed == "live v1\n" and a.candidate_sha == frozen,
                  f"deployed={deployed!r} frozen_unchanged={a.candidate_sha == frozen}")
        finally:
            a.teardown()


def p16_promote_ordering():
    # (i) quick: deploy (apply) invoked ONLY after the merge.
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        trace: list = []
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=lambda: None, deploy_target=str(tgt),
                             trace=trace, tmp_root=d)
        try:
            res = a.run(approve=True)
            order_ok = ("apply" in trace and "pr_merge" in trace
                        and trace.index("apply") > trace.index("pr_merge"))
            quick_ok = res.status == "promoted" and order_ok
        finally:
            a.teardown()
    # (ii) extra-safety: PR opened, HOLD — NO deploy before external review.
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        trace2: list = []
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             apply_cmd=make_apply(), network_tail_cmd=make_tail(),
                             green_tag_cmd=make_green_tag(src),
                             deploy_target=str(tgt), pr_mode="extra-safety",
                             trace=trace2, tmp_root=d)
        try:
            res = a.run(approve=True)
            no_deploy = "apply" not in trace2 and "deploy_lock:acquire" not in trace2
            tgt_untouched = (tgt / "hooks" / "mod.py").read_text() == "live v0\n"
            safety_ok = res.status == "hold" and no_deploy and tgt_untouched
        finally:
            a.teardown()
    check("P16 promote ordering: deploy only AFTER merge; extra-safety holds with no deploy",
          quick_ok and safety_ok, f"quick={quick_ok} safety={safety_ok}")


# ─────────────────────────────────────────────────────────────────────────────
# P17 — the production configuration, reached through the production entry point
# (land-port-tested-configuration S2 / A4).
#
# Every test above injects the seams. P17 injects none, so it exercises the one
# configuration the single production construction site is actually in.


def p17_cli_refuses_unwired_seams():
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d)); tgt = seed_target(Path(d))
        scope_file = Path(d) / "_session_scope-TEST.md"
        scope_file.write_text("hooks/mod.py\n")
        before = sha(src)

        r = subprocess.run(
            [sys.executable, str(HOOKS / "land_port.py"), "promote",
             "--source", str(src), "--target", str(tgt),
             "--session-scope", str(scope_file), "--assume-yes"],
            capture_output=True, text=True, timeout=60,
        )
        out = r.stdout + r.stderr
        # The refusal must arrive BEFORE anything is written. `dry_run_diff()` is
        # what `_promote_cli` calls first, and it registers a worktree in the
        # SHARED source, runs capture against live and commits to the workspace —
        # so a guard on the mutating methods alone would sit downstream of all of it.
        leaked_wt = "promo-ws-" in git(src, "worktree", "list").stdout
        check("P17 CLI with NO seams wired → named refusal, non-zero exit, no traceback, "
              "nothing written to the shared source",
              r.returncode != 0
              and "unwired-seams" in out
              and "claude-promote" in out              # names the path that IS wired
              and "Traceback" not in out               # a return, never a raise
              and sha(src) == before
              and not leaked_wt,
              f"exit={r.returncode} leaked_wt={leaked_wt}")


def p18_seam_guard_at_every_method_boundary():
    """A4 placed the guard at each method's entry rather than at the CLI, arguing
    that a CLI-level guard would leave `run()` open and rest on there being exactly
    one caller today. That argument was asserted and never exercised: P17 only ever
    reaches `dry_run_diff()`. Call the other two DIRECTLY."""
    with tempfile.TemporaryDirectory() as d:
        src = seed_source(Path(d))
        before = sha(src)
        a = PromotionAdapter(src, session_scope=["hooks/mod.py"],
                             capture_cmd=make_capture({"hooks/mod.py": "live v1\n"}),
                             tmp_root=d)   # NO seams — the production configuration
        try:
            # `apply()` with no prior dry_run_diff would otherwise return
            # "no-candidate"; it returns the seam refusal instead, which is what
            # shows the guard sits at method ENTRY, ahead of the state check.
            ap = a.apply()
            rn = a.run(approve=True)
            dr = a.dry_run_diff()
        finally:
            a.teardown()
        check("P18 the seam guard is on every guarded method, not only the one the "
              "CLI reaches first",
              ap.status == "unwired-seams" and rn.status == "unwired-seams"
              and dr.status == "unwired-seams" and sha(src) == before,
              f"apply={ap.status} run={rn.status} dry_run={dr.status}")


def main() -> int:
    print("S4 promotion adapter acceptance (P1–P18):")
    for fn in (p1_noop, p2_scoped_capture, p3_nogo_discards, p4_go_reconciles,
               p5_toctou_reconcile, p6_reconcile_conflict_abort, p7_source_lock_serializes,
               p8_path_scoped_apply_preserves, p9_scope_absent, p10_rollback,
               p11_lock_boundaries, p12_teardown_on_crash, p13_verifier_isolation_arming,
               p14_deletion_safe, p15_frozen_candidate_integrity, p16_promote_ordering,
               p17_cli_refuses_unwired_seams,
               p18_seam_guard_at_every_method_boundary):
        try:
            fn()
        except Exception as e:  # a test that throws is a FAILURE, never a silent pass
            check(fn.__name__, False, f"EXCEPTION: {e}")
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} green")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())

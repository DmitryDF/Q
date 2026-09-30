#!/usr/bin/env python3
"""S6 legacy-cutover — DIRTY transactional migration acceptance test
(git-working-model / A12; EDGE1–EDGE17).

Adversarial coverage of the S6 dirty cutover in throwaway `tempfile` git repos
ONLY — auto-cleaned, NEVER `rm -rf`/`git clean -f`. No live ~/.claude and no live
~/repos is touched: every repo + worktree lives under a TemporaryDirectory, and
the helper resolves its siblings (worktree_cutover.py, bookkeeping_paths.py,
bookkeeping_lock.py, the gate/push sources, worktree-detect.sh) from ITS OWN dir
— here the clone's hooks dir — so the run is fully self-contained.

Mirrors the S5 harness (RESULTS + check(), a `git` helper with
GIT_EDITOR=true/GIT_PAGER=cat, `seed_repo`, shell out to `worktree-helper.sh
place`). Asserts OBSERVABLE git state, not prose.

Cases:
  H1   Happy dirty: staged + unstaged + untracked + a bookkeeping (TODO.md) edit
       + a required .env + a disposable rebuild dir → migrates zero-loss; index
       intact (staged-stays-staged / unstaged-stays-unstaged); .env carried;
       disposable dir rebuilt; TODO.md edit committed on main + ABSENT from the
       worktree (fails closed); primary on main + clean.
  H2   Drain divergence: main advanced TODO.md concurrently → after migrate, BOTH
       the main-side and the working-tree additions are retained (merge=union).
  P1   Pre-flight halts (super): mid-merge / mid-rebase / mid-cherry-pick /
       mid-revert → halt (exit 3), nothing moved.
  P2   Pre-flight halts (submodule): mid-bisect INSIDE a submodule (the Patch-A2
       regression guard — a submodule's gitdir is a FILE under .git/modules/…, so
       a hardcoded `.git/…` literal would miss it) → halt, nothing moved.
  P3   Dirty submodule → halt, nothing moved.
  P4   Detached primary (dirty) → EDGE11 prompt-or-fail-closed (non-TTY exit 3).
  P5   Active-branch-is-main (dirty) → EDGE16 prompt-or-fail-closed (non-TTY exit 3).
  P6   Drain lock held by another process → EDGE12 graceful abort (exit 3), nothing moved.
  R1   worktree-add failure → EDGE2 full unwind: primary on original branch, work
       restored staged-as-staged, the primary's ORIGINAL config UNTOUCHED, no worktree.
  R2   stash-pop-conflict → EDGE8 halt-DON'T-rollback: work in the worktree, stash
       RETAINED (never dropped), recovery + abort-warning printed, no rollback.
  R3   EACCES on a REQUIRED config → EDGE2 rollback; R4 EACCES on OPTIONAL → continue.
  R5   Untracked non-main-blob bookkeeping file → EDGE9 halt (exit 3); the file is
       NOT deleted (no `git clean -f`).
  R6   Rebuild failure → migrated (exit 0) + EDGE5 alert only (no rollback).
  S1   Static safe-defaults scan: the S6 code introduces NO banned command.

Run:  python3 test_legacy_cutover_s6.py
Exit: 0 all green; 1 any failure.
"""
from __future__ import annotations

import fcntl
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent
HELPER = HOOKS / "worktree-helper.sh"
CUTOVER = HOOKS / "worktree_cutover.py"
REAL_GIT = shutil.which("git") or "/usr/bin/git"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    line = f"{'ok  ' if ok else 'FAIL'}  {name}"
    if detail and not ok:
        line += f"  :: {detail}"
    print(line)


def _env(extra: dict | None = None) -> dict:
    e = dict(
        os.environ,
        GIT_EDITOR="true",
        GIT_PAGER="cat",
        GIT_TERMINAL_PROMPT="0",
    )
    if extra:
        e.update(extra)
    return e


def git(cwd, *args, must=True, allow_file_proto=False):
    pre = ["-c", "protocol.file.allow=always"] if allow_file_proto else []
    r = subprocess.run(
        [REAL_GIT, "-C", str(cwd), *pre, *args],
        capture_output=True, text=True, env=_env(),
    )
    if must and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {r.stderr.strip()}")
    return r


def place(repo, repos_root, topic, env_extra: dict | None = None, path_prepend: str | None = None):
    env = _env(env_extra)
    if path_prepend:
        env["PATH"] = path_prepend + os.pathsep + env["PATH"]
    return subprocess.run(
        ["bash", str(HELPER), "place", "--repo", str(repo),
         "--repos-root", str(repos_root), "--topic", topic],
        capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL,
    )


def head_branch(repo):
    r = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", must=False)
    return r.stdout.strip() if r.returncode == 0 else None  # None == detached


def porcelain(repo):
    return git(repo, "status", "--porcelain").stdout


def seed_repo(root, name):
    repo = Path(root) / name
    repo.mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@t")
    git(repo, "config", "user.name", "t")
    (repo / "src.py").write_text("print('domain')\n")
    (repo / "TODO.md").write_text("# TODO (a main-owned shared bookkeeping path)\n- a\n")
    (repo / ".gitignore").write_text(".env\nnode_modules/\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def write_cutover_config(repo, manifest=None, rebuild=None):
    import json
    cfg = {}
    if manifest is not None:
        cfg["config_manifest"] = manifest
    if rebuild is not None:
        cfg["rebuild_commands"] = rebuild
    (Path(repo) / ".worktree-cutover.json").write_text(json.dumps(cfg))
    git(repo, "add", ".worktree-cutover.json")
    git(repo, "commit", "-q", "-m", "cutover config")


# --- tests -----------------------------------------------------------------

def h1_happy_dirty(sbx):
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "h1")
    write_cutover_config(
        repo,
        manifest=[{"path": ".env", "required": True}],
        rebuild=["mkdir -p node_modules && printf built > node_modules/marker"],
    )
    git(repo, "checkout", "-q", "-b", "alpha")
    # curated dirty tree
    (repo / "staged_new.py").write_text("print('staged new')\n")   # → staged (A )
    git(repo, "add", "staged_new.py")
    (repo / "src.py").write_text("print('domain EDITED')\n")        # → unstaged ( M)
    (repo / "untracked.py").write_text("print('untracked')\n")      # → untracked (??)
    (repo / "TODO.md").write_text("# TODO\n- a\n- from-topic\n")     # bookkeeping edit
    (repo / ".env").write_text("SECRET=required\n")                 # gitignored required config

    r = place(repo, repos, "alpha")
    dest = Path(r.stdout.strip()) if r.stdout.strip() else repos / "h1" / "alpha"
    check("H1 migrate exits 0", r.returncode == 0, r.stderr.strip()[-400:])
    check("H1 worktree created at dest", dest.is_dir(), str(dest))

    st = git(dest, "status", "--porcelain").stdout if dest.is_dir() else ""
    check("H1 staged-stays-staged (A  staged_new.py)",
          any(l.startswith("A  staged_new.py") for l in st.splitlines()), st)
    check("H1 unstaged-stays-unstaged ( M src.py)",
          any(l.startswith(" M src.py") for l in st.splitlines()), st)
    check("H1 untracked stays untracked (?? untracked.py)",
          any(l.startswith("?? untracked.py") for l in st.splitlines()), st)
    check("H1 required .env carried into worktree", (dest / ".env").exists(), "")
    check("H1 disposable dir rebuilt (node_modules/marker)",
          (dest / "node_modules" / "marker").exists(), "")

    # bookkeeping drained to main + absent from the worktree (fails closed)
    main_todo = git(repo, "show", "main:TODO.md", must=False).stdout
    check("H1 TODO.md edit committed on main", "from-topic" in main_todo, main_todo)
    check("H1 TODO.md ABSENT from worktree (sparse-excluded, fails closed)",
          not (dest / "TODO.md").exists(), str(dest / "TODO.md"))
    check("H1 primary restored to main (EDGE17)", head_branch(repo) == "main",
          f"head={head_branch(repo)!r}")
    check("H1 primary clone clean after migration",
          porcelain(repo).strip() == "", porcelain(repo))


def h2_drain_divergence(sbx):
    repos = Path(sbx) / "repos"
    repo = seed_repo(sbx, "h2")
    git(repo, "checkout", "-q", "-b", "beta")
    # main advances TODO.md concurrently (a bookkeeping append on main)
    git(repo, "branch", "-f", "main", "main")  # no-op guard
    git(repo, "stash", must=False)  # nothing to stash; keep tree
    # commit an advance directly on main via a detached update
    base = git(repo, "rev-parse", "main").stdout.strip()
    tmpidx = Path(sbx) / "h2.idx"
    env = _env({"GIT_INDEX_FILE": str(tmpidx)})
    subprocess.run([REAL_GIT, "-C", str(repo), "read-tree", base], env=env, check=True)
    (repo / "TODO.md").write_text("# TODO\n- a\n- from-main\n")
    subprocess.run([REAL_GIT, "-C", str(repo), "add", "TODO.md"], env=env, check=True)
    tree = subprocess.run([REAL_GIT, "-C", str(repo), "write-tree"], env=env,
                          capture_output=True, text=True, check=True).stdout.strip()
    c = subprocess.run([REAL_GIT, "-C", str(repo), "commit-tree", tree, "-p", base,
                        "-m", "main advances TODO"], capture_output=True, text=True,
                       env=_env(), check=True).stdout.strip()
    git(repo, "update-ref", "refs/heads/main", c, base)
    # restore working TODO to the topic-base version, then make the topic edit
    (repo / "TODO.md").write_text("# TODO\n- a\n- from-topic\n")   # topic bookkeeping edit
    (repo / "src.py").write_text("print('beta domain')\n")         # a domain edit to carry

    r = place(repo, repos, "beta")
    check("H2 migrate exits 0", r.returncode == 0, r.stderr.strip()[-400:])
    main_todo = git(repo, "show", "main:TODO.md", must=False).stdout
    check("H2 divergence-safe: main-side addition retained",
          "from-main" in main_todo, main_todo)
    check("H2 divergence-safe: working-tree addition also retained (merge=union)",
          "from-topic" in main_todo, main_todo)


def _dirty(repo):
    (Path(repo) / "dirt.py").write_text("dirty\n")  # untracked domain → routes to cutover_dirty


def _induce_super_sequencer(repo, kind):
    """Induce a mid-sequencer op on the current (topic) branch, leaving it dirty."""
    if kind == "merge":
        git(repo, "checkout", "-q", "-b", "_o")
        (Path(repo) / "src.py").write_text("other\n"); git(repo, "commit", "-qam", "o")
        git(repo, "checkout", "-q", "topic")
        (Path(repo) / "src.py").write_text("topic\n"); git(repo, "commit", "-qam", "t")
        git(repo, "merge", "_o", must=False)
    elif kind == "cherry-pick":
        git(repo, "checkout", "-q", "-b", "_o")
        (Path(repo) / "src.py").write_text("other\n"); git(repo, "commit", "-qam", "o")
        oc = git(repo, "rev-parse", "HEAD").stdout.strip()
        git(repo, "checkout", "-q", "topic")
        (Path(repo) / "src.py").write_text("topic\n"); git(repo, "commit", "-qam", "t")
        git(repo, "cherry-pick", oc, must=False)
    elif kind == "revert":
        (Path(repo) / "src.py").write_text("v1\n"); git(repo, "commit", "-qam", "v1")
        rc1 = git(repo, "rev-parse", "HEAD").stdout.strip()
        (Path(repo) / "src.py").write_text("v2\n"); git(repo, "commit", "-qam", "v2")
        git(repo, "revert", "--no-edit", rc1, must=False)
    elif kind == "rebase":
        git(repo, "checkout", "-q", "-b", "_feat")
        (Path(repo) / "src.py").write_text("feat\n"); git(repo, "commit", "-qam", "feat")
        git(repo, "checkout", "-q", "topic")
        (Path(repo) / "src.py").write_text("topicchg\n"); git(repo, "commit", "-qam", "tc")
        git(repo, "checkout", "-q", "_feat")
        git(repo, "rebase", "topic", must=False)


def p1_super_sequencers(sbx):
    for kind in ("merge", "cherry-pick", "revert", "rebase"):
        repos = Path(sbx) / f"repos-{kind}"
        repo = seed_repo(sbx, f"p1-{kind}")
        git(repo, "checkout", "-q", "-b", "topic")
        _induce_super_sequencer(repo, kind)
        branch_before = head_branch(repo)
        r = place(repo, repos, "topic")
        check(f"P1[{kind}] halt exit 3", r.returncode == 3,
              f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
        # rebase leaves us on _feat; the point is: nothing was migrated
        check(f"P1[{kind}] nothing moved (no worktree at dest)",
              not (repos / f"p1-{kind}" / "topic").exists()
              and not (repos / f"p1-{kind}" / "_feat").exists(), "")
        check(f"P1[{kind}] head unchanged", head_branch(repo) == branch_before,
              f"{head_branch(repo)!r} vs {branch_before!r}")


def _add_submodule(sbx, sup_name):
    subrepo = Path(sbx) / f"{sup_name}-subsrc"
    subrepo.mkdir()
    git(subrepo, "init", "-b", "main")
    git(subrepo, "config", "user.email", "t@t"); git(subrepo, "config", "user.name", "t")
    (subrepo / "a.txt").write_text("s1\n"); git(subrepo, "add", "-A"); git(subrepo, "commit", "-qm", "s1")
    (subrepo / "a.txt").write_text("s2\n"); git(subrepo, "add", "-A"); git(subrepo, "commit", "-qm", "s2")
    repo = seed_repo(sbx, sup_name)
    git(repo, "submodule", "add", str(subrepo), "sub", allow_file_proto=True)
    git(repo, "commit", "-qm", "add submodule")
    return repo, repo / "sub"


def p2_submodule_bisect(sbx):
    repos = Path(sbx) / "repos-p2"
    repo, sub = _add_submodule(sbx, "p2")
    git(repo, "checkout", "-q", "-b", "topic")
    _dirty(repo)  # superproject dirty → routes to cutover_dirty
    # mid-bisect INSIDE the submodule (no dirty files there → only sequencer
    # detection can catch it; its gitdir is a FILE under .git/modules/sub)
    git(sub, "bisect", "start")
    git(sub, "bisect", "bad", must=False)
    root = git(sub, "rev-list", "--max-parents=0", "HEAD").stdout.strip().splitlines()[0]
    git(sub, "bisect", "good", root, must=False)
    r = place(repo, repos, "topic")
    check("P2 in-submodule bisect → halt exit 3 (Patch-A2 regression guard)",
          r.returncode == 3, f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("P2 nothing moved", not (repos / "p2" / "topic").exists(), "")


def p3_dirty_submodule(sbx):
    repos = Path(sbx) / "repos-p3"
    repo, sub = _add_submodule(sbx, "p3")
    git(repo, "checkout", "-q", "-b", "topic")
    _dirty(repo)
    (sub / "a.txt").write_text("uncommitted submodule work\n")  # dirty submodule (no sequencer)
    r = place(repo, repos, "topic")
    check("P3 dirty submodule → halt exit 3", r.returncode == 3,
          f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("P3 nothing moved", not (repos / "p3" / "topic").exists(), "")


def p4_detached_dirty(sbx):
    repos = Path(sbx) / "repos-p4"
    repo = seed_repo(sbx, "p4")
    git(repo, "checkout", "-q", "--detach", "HEAD")
    _dirty(repo)  # dirty + detached
    r = place(repo, repos, "gamma")
    check("P4 dirty+detached → EDGE11 fail-closed (exit 3)", r.returncode == 3,
          f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("P4 message names EDGE11/detached",
          "EDGE11" in r.stderr or "DETACHED" in r.stderr, r.stderr.strip()[-200:])
    check("P4 nothing moved", not (repos / "p4" / "gamma").exists(), "")


def p5_ismain_dirty(sbx):
    repos = Path(sbx) / "repos-p5"
    repo = seed_repo(sbx, "p5")  # on main
    (repo / "work.py").write_text("dirty on main\n")  # dirty on main
    r = place(repo, repos, "delta")
    check("P5 dirty-on-main → EDGE16 fail-closed (exit 3)", r.returncode == 3,
          f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("P5 message names EDGE16/protected",
          "EDGE16" in r.stderr or "protected" in r.stderr.lower(), r.stderr.strip()[-200:])
    check("P5 nothing moved", not (repos / "p5" / "delta").exists(), "")
    check("P5 primary still on main", head_branch(repo) == "main", f"{head_branch(repo)!r}")


def p6_drain_lock_held(sbx):
    repos = Path(sbx) / "repos-p6"
    repo = seed_repo(sbx, "p6")
    git(repo, "checkout", "-q", "-b", "eps")
    (repo / "src.py").write_text("edit\n")  # dirty
    common = git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    lock_path = (Path(repo) / common / "bookkeeping.lock").resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a")
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    try:
        r = place(repo, repos, "eps", env_extra={"BOOKKEEPING_LOCK_TIMEOUT": "1"})
    finally:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        f.close()
    check("P6 drain lock held → EDGE12 graceful abort (exit 3)", r.returncode == 3,
          f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("P6 nothing moved", not (repos / "p6" / "eps").exists(), "")
    check("P6 primary still on eps, dirty intact",
          head_branch(repo) == "eps" and "src.py" in porcelain(repo),
          f"head={head_branch(repo)!r} status={porcelain(repo)!r}")


def r1_worktree_add_failure(sbx):
    repos = Path(sbx) / "repos-r1"
    repo = seed_repo(sbx, "r1")
    write_cutover_config(repo, manifest=[{"path": ".env", "required": False}])
    git(repo, "checkout", "-q", "-b", "zeta")
    (repo / "staged.py").write_text("staged\n"); git(repo, "add", "staged.py")  # A
    (repo / "src.py").write_text("unstaged edit\n")                              # M
    (repo / ".env").write_text("ORIG=primary\n")                                 # primary config
    # pre-create dest as a FILE so `git worktree add` fails
    dest = repos / "r1" / "zeta"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("pre-existing NOT-a-worktree\n")

    r = place(repo, repos, "zeta")
    check("R1 worktree-add failure → non-zero", r.returncode != 0,
          f"rc={r.returncode}")
    check("R1 EDGE2: primary restored to original branch (zeta)",
          head_branch(repo) == "zeta", f"head={head_branch(repo)!r}")
    st = porcelain(repo)
    check("R1 EDGE2: work restored STAGED-as-staged (A  staged.py)",
          any(l.startswith("A  staged.py") for l in st.splitlines()), st)
    check("R1 EDGE2: work restored UNSTAGED-as-unstaged ( M src.py)",
          any(l.startswith(" M src.py") for l in st.splitlines()), st)
    check("R1 EDGE2: dest pre-existing file UNTOUCHED",
          dest.is_file() and "pre-existing" in dest.read_text(), "")
    check("R1 EDGE2: primary's ORIGINAL config UNTOUCHED (no config-delete ran)",
          (repo / ".env").exists() and "ORIG=primary" in (repo / ".env").read_text(), "")


def _pop_conflict_shim(dirpath):
    """A git shim on PATH that turns `stash pop` into `stash apply` (KEEPS the
    stash) then exits 1 — deterministically driving the EDGE8 pop-conflict code
    path while leaving the work in the worktree and the stash retained (exactly
    what a real conflicted pop does). Every other git call passes through."""
    shim = Path(dirpath) / "git"
    shim.write_text(
        "#!/usr/bin/env bash\n"
        f'REAL="{REAL_GIT}"\n'
        'args=("$@")\n'
        'joined="$*"\n'
        'if [[ "$joined" == *"stash pop"* ]]; then\n'
        '  newargs=(); for a in "${args[@]}"; do [ "$a" = "pop" ] && a=apply; newargs+=("$a"); done\n'
        '  "$REAL" "${newargs[@]}"; exit 1\n'
        'fi\n'
        'exec "$REAL" "$@"\n'
    )
    shim.chmod(0o755)
    return str(shim.parent)


def r2_pop_conflict(sbx):
    repos = Path(sbx) / "repos-r2"
    repo = seed_repo(sbx, "r2")
    git(repo, "checkout", "-q", "-b", "eta")
    (repo / "src.py").write_text("domain edit to carry\n")  # domain change → stashed
    shimdir = Path(sbx) / "r2-shim"; shimdir.mkdir()
    pp = _pop_conflict_shim(shimdir)

    r = place(repo, repos, "eta", path_prepend=pp)
    dest = repos / "r2" / "eta"
    check("R2 pop conflict → non-zero (EDGE8)", r.returncode != 0, f"rc={r.returncode}")
    check("R2 EDGE8: work is in the worktree (NOT rolled back)", dest.is_dir(), str(dest))
    check("R2 EDGE8: stash RETAINED (never dropped)",
          git(repo, "stash", "list").stdout.strip() != "", "")
    check("R2 EDGE8: recovery names 'git worktree remove . --force'",
          "git worktree remove . --force" in r.stderr, r.stderr.strip()[-300:])
    check("R2 EDGE8: abort-DISCARDS-resolutions warning present",
          "WARNING" in r.stderr and ("DISCARD" in r.stderr.upper()), r.stderr.strip()[-300:])
    check("R2 EDGE8: recovery names 'stash pop --index' restore",
          "stash pop --index" in r.stderr, r.stderr.strip()[-300:])


def r3_r4_eacces_config(sbx):
    # R3 — EACCES on a REQUIRED config → EDGE2 rollback
    repos = Path(sbx) / "repos-r3"
    repo = seed_repo(sbx, "r3")
    write_cutover_config(repo, manifest=[{"path": ".env", "required": True}])
    git(repo, "checkout", "-q", "-b", "theta")
    (repo / "src.py").write_text("edit\n")
    envf = repo / ".env"; envf.write_text("SECRET=x\n"); envf.chmod(0o000)  # unreadable REQUIRED
    try:
        r = place(repo, repos, "theta")
    finally:
        envf.chmod(0o644)
    check("R3 EACCES on REQUIRED config → rollback (non-zero)", r.returncode != 0,
          f"rc={r.returncode}")
    check("R3 EDGE2: primary restored to theta", head_branch(repo) == "theta",
          f"head={head_branch(repo)!r}")
    check("R3 EDGE2: no worktree left", not (repos / "r3" / "theta").is_dir(), "")

    # R4 — EACCES on an OPTIONAL config → advisory + continue (migrate succeeds)
    repos4 = Path(sbx) / "repos-r4"
    repo4 = seed_repo(sbx, "r4")
    write_cutover_config(repo4, manifest=[{"path": ".env", "required": False}])
    git(repo4, "checkout", "-q", "-b", "iota")
    (repo4 / "src.py").write_text("edit\n")
    envf4 = repo4 / ".env"; envf4.write_text("OPT=y\n"); envf4.chmod(0o000)  # unreadable OPTIONAL
    try:
        r4 = place(repo4, repos4, "iota")
    finally:
        envf4.chmod(0o644)
    check("R4 EACCES on OPTIONAL config → continue (migrate exit 0)",
          r4.returncode == 0, f"rc={r4.returncode} :: {r4.stderr.strip()[-200:]}")
    check("R4 worktree created despite optional-config EACCES",
          (repos4 / "r4" / "iota").is_dir(), "")


def r5_untracked_halt(sbx):
    repos = Path(sbx) / "repos-r5"
    repo = seed_repo(sbx, "r5")
    git(repo, "checkout", "-q", "-b", "kappa")
    (repo / "src.py").write_text("domain edit\n")                 # domain (routes to cutover)
    novel = repo / "Thoughts" / "novel_THOUGHT.md"
    novel.parent.mkdir(parents=True, exist_ok=True)
    novel.write_text("brand new spine, never on main\n")          # untracked bookkeeping, not on main
    r = place(repo, repos, "kappa")
    check("R5 untracked non-main-blob bookkeeping → EDGE9 halt (exit 3)",
          r.returncode == 3, f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("R5 the untracked file is NOT deleted (no git clean -f)",
          novel.exists(), str(novel))
    check("R5 nothing moved", not (repos / "r5" / "kappa").exists(), "")


def r6_rebuild_failure(sbx):
    repos = Path(sbx) / "repos-r6"
    repo = seed_repo(sbx, "r6")
    write_cutover_config(repo, rebuild=["exit 7"])  # a rebuild command that fails
    git(repo, "checkout", "-q", "-b", "lam")
    (repo / "src.py").write_text("edit\n")
    r = place(repo, repos, "lam")
    dest = repos / "r6" / "lam"
    check("R6 rebuild failure → migration still succeeds (exit 0)",
          r.returncode == 0, f"rc={r.returncode} :: {r.stderr.strip()[-200:]}")
    check("R6 worktree migrated (code safe)", dest.is_dir(), str(dest))
    check("R6 EDGE5 alert emitted (no rollback)", "EDGE5" in r.stderr,
          r.stderr.strip()[-300:])


BANNED = [
    (r"git\s+stash\s+pop(?!\s+--index)", "bare `git stash pop` (must be --index)"),
    (r"git\s+stash\s+drop", "`git stash drop`"),
    (r"git\s+clean\s+-[a-z]*f", "`git clean -f`"),
    (r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", "`rm -rf`"),
    (r"git\s+reset\s+--hard", "`git reset --hard`"),
    (r"--no-verify", "`--no-verify`"),
    (r"--force", "`--force`"),
]
# The ONLY sanctioned --force in the whole helper is the design-mandated abort
# advisory command, which lives inside a printed heredoc (stripped below). So the
# EXECUTABLE code must carry zero banned tokens; the advisory presence is asserted
# separately from the RAW text.
FORCE_ADVISORY = "git worktree remove . --force"


def _strip_sh(text):
    """Return only EXECUTABLE shell — no comment lines, no inline trailing
    comments, no heredoc bodies (advisory/message text)."""
    out, heredoc_delim = [], None
    for line in text.splitlines():
        if heredoc_delim is not None:
            if line.strip() == heredoc_delim:
                heredoc_delim = None
            continue
        if line.lstrip().startswith("#"):
            continue
        m = re.search(r"<<-?\s*['\"]?(\w+)['\"]?", line)
        code = re.sub(r"\s#.*$", "", line)  # strip inline trailing comment
        out.append(code)
        if m:
            heredoc_delim = m.group(1)
    return "\n".join(out)


def _strip_py(text):
    """Return only EXECUTABLE python — no triple-quoted docstrings, no comments."""
    text = re.sub(r'"""(?:.|\n)*?"""', "", text)
    text = re.sub(r"'''(?:.|\n)*?'''", "", text)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in text.splitlines())


def s1_static_safe_defaults_scan(sbx):
    for f, stripper in ((HELPER, _strip_sh), (CUTOVER, _strip_py)):
        code = stripper(f.read_text())
        for pat, label in BANNED:
            hits = [m.group(0) for m in re.finditer(pat, code)]
            check(f"S1 [{f.name}] executable code has no {label}",
                  not hits, f"found in code: {hits}")
    # The design-mandated abort advisory (with its --force) MUST be present in the
    # raw helper text (it is printed guidance, never executed by the cutover).
    check("S1 EDGE8 abort advisory present in raw helper (the sole sanctioned --force)",
          FORCE_ADVISORY in HELPER.read_text(), "")


def main():
    with tempfile.TemporaryDirectory(prefix="s6-cutover-") as sbx:
        for fn in (
            h1_happy_dirty,
            h2_drain_divergence,
            p1_super_sequencers,
            p2_submodule_bisect,
            p3_dirty_submodule,
            p4_detached_dirty,
            p5_ismain_dirty,
            p6_drain_lock_held,
            r1_worktree_add_failure,
            r2_pop_conflict,
            r3_r4_eacces_config,
            r5_untracked_halt,
            r6_rebuild_failure,
            s1_static_safe_defaults_scan,
        ):
            try:
                fn(sbx)
            except Exception as e:  # a setup error is a test failure, not a crash
                check(f"{fn.__name__} raised", False, repr(e))

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks green")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()

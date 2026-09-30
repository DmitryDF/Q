#!/usr/bin/env python3
"""Adversarial test matrix for the non-redirect write-target detector
(execplan-nonredirect-write-guard, 2026-07-20).

Three layers, matching the plan's Verification section:
  * UNIT — `extract_bash_write_targets` over the write-detection / allow-rule /
    redirect-&-commit-preservation matrix (Part A) + the verb CLI NUL contract
    (Part B) + the widened backward-compatible `execplan_entry_check` (Part C).
  * INTEGRATION — both gate SCRIPTS driven as subprocesses over crafted PreToolUse
    envelopes: block/allow per utility, multi-target, empty/read-only, spaces,
    fd-redirect / commit preservation, the logged BYPASS override, and a hard
    proof the Python-call count is O(1) in the number of targets (Part D).

Run: python3 -m pytest test_extract_bash_targets.py
"""

import json
import os
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
RUN_PY = os.path.join(HERE, "run.py")
# Hooks resolve run.py via BASH_SOURCE, so the CO-LOCATED (patched) gate scripts run
# against the CO-LOCATED run.py — the clone under experiment, ~/.claude once promoted.
_LOCAL_HOOKS_DIR = os.path.join(os.path.dirname(os.path.dirname(HERE)), "hooks")
ENTRY_GATE = os.path.join(_LOCAL_HOOKS_DIR, "check-execplan-entry-gate.sh")
WALK_GATE = os.path.join(_LOCAL_HOOKS_DIR, "check-execplan-walk-gate.sh")
sys.path.insert(0, HERE)

import run  # noqa: E402

WT = "/wt"


def tgt(cmd, cwd=WT):
    return run.extract_bash_write_targets(cmd, cwd)["targets"]


# --------------------------------------------------------------------------- #
# Part A — extract_bash_write_targets adversarial matrix
# --------------------------------------------------------------------------- #

# (command, cwd, expected-absolute-targets)
_DETECT_CASES = [
    # --- cp / mv / install writes (C1, C2, C5) ---
    ("cp payload.py /wt/src/x.py", WT, ["/wt/src/x.py"]),
    ("cp a.py b.py", WT, ["/wt/b.py"]),                       # relative dest → cwd
    ("mv old.py /wt/new.py", WT, ["/wt/new.py"]),
    ("cp -r src/ /wt/dest/", WT, ["/wt/dest"]),
    ("cp -t /wt/dir a.py b.py", WT, ["/wt/dir"]),             # -t DEST
    ("cp --target-directory=/wt/dir a b", WT, ["/wt/dir"]),
    ("cp --target-directory /wt/dir a b", WT, ["/wt/dir"]),
    ("install -m 755 build.sh /wt/bin/run", WT, ["/wt/bin/run"]),
    ("install -o me -g grp x /wt/y", WT, ["/wt/y"]),
    # --- sed in-place, all variants (C3) ---
    ("sed -i 's/a/b/' /wt/x.py", WT, ["/wt/x.py"]),
    ("sed -i.bak 's/a/b/' /wt/x.py", WT, ["/wt/x.py"]),
    ("sed --in-place 's/a/b/' /wt/x.py", WT, ["/wt/x.py"]),
    ("sed --in-place=.bak 's/a/b/' /wt/x.py", WT, ["/wt/x.py"]),
    ("sed -ni 's/a/b/p' /wt/x.py", WT, ["/wt/x.py"]),         # cluster, i last
    ("sed -e 's/a/b/' -i /wt/x.py", WT, ["/wt/x.py"]),        # -e then -i
    ("sed -i 's/a/b/' /wt/a.py /wt/b.py", WT, ["/wt/a.py", "/wt/b.py"]),  # multi-file
    # --- tee (C4) ---
    ("tee /wt/a /wt/b", WT, ["/wt/a", "/wt/b"]),
    ("echo hi | tee -a /wt/log", WT, ["/wt/log"]),
    ("echo hi | tee -- /wt/-weird", WT, ["/wt/-weird"]),
    # --- dd of= (C6) ---
    ("dd if=/dev/zero of=/wt/blob bs=1M", WT, ["/wt/blob"]),
    ("dd of=/wt/blob if=/wt/src", WT, ["/wt/blob"]),          # of= first
    # --- redirects still detected (folded into the verb) ---
    ("echo x > /wt/f", WT, ["/wt/f"]),
    ("echo x >> /wt/f", WT, ["/wt/f"]),
    ("echo x > out.py", WT, ["/wt/out.py"]),                 # relative redirect → cwd
    # --- env prefix + absolute-path command + `--` ---
    ("env FOO=1 cp a.py /wt/x.py", WT, ["/wt/x.py"]),
    ("FOO=1 BAR=2 cp a.py /wt/x.py", WT, ["/wt/x.py"]),
    ("/bin/cp a.py /wt/x.py", WT, ["/wt/x.py"]),
    ("cp -- -weird.py /wt/x.py", WT, ["/wt/x.py"]),
    # --- multi sub-command (;, &&, ||, |) ---
    ("cp a /wt/x && tee /wt/y", WT, ["/wt/x", "/wt/y"]),
    ("cp a /wt/x ; mv b /wt/z", WT, ["/wt/x", "/wt/z"]),
    ("false || cp a /wt/x", WT, ["/wt/x"]),
    # --- quoted paths with spaces survive (shlex) ---
    ("cp src '/wt/my dest.py'", WT, ["/wt/my dest.py"]),
    ('cp src "/wt/a b.py"', WT, ["/wt/a b.py"]),
    # --- fd-digit before redirect does not become a cp operand ---
    ("cp a b 2>/dev/null", WT, ["/wt/b"]),
]

# Rows that must yield NO target (allow-rules + preservation) — C7..C13.
_ALLOW_CASES = [
    ("sed -n 's/a/b/p' /wt/x.py", WT),                       # sed without -i  (C10)
    ("sed 's/a/b/' /wt/x.py", WT),                           # no flags at all
    ("dd if=/wt/x bs=1M", WT),                               # dd with no of=  (C11)
    ("cat /wt/x", WT),                                       # read-only
    ("ls -la /wt", WT),
    ("grep foo /wt/x", WT),
    ("echo x 2> /wt/err", WT),                               # fd-redirect     (C13)
    ("echo x 2>> /wt/err", WT),
    ("echo x &> /wt/err", WT),                               # &> fd-redirect  (C13)
    ("cmd >&2", WT),                                         # dup fd, no file
    ("git commit -m 'S1: fix > thing'", WT),                 # commit skip     (C12)
    ("git -C /wt commit -m 'S2: a > b'", WT),                # commit w/ global opt
    ("cp a.py", WT),                                         # 1 operand → no dest
    ("cp", WT),                                              # no operands
    # --- A6 / G8: a comparison operator is not a redirect ---
    # `pytest "pkg>=1"` used to yield the phantom target `=1`, which resolves to an
    # in-worktree path and cost a PERMANENT exemption for an operation that never
    # wrote anything. These are the manufactured blocks A6 removes.
    ('pytest "pkg>=1"', WT),
    ("pip install 'requests>=2.0'", WT),
    ("pip install 'django>=4.2,<5'", WT),
    ('python -c "assert x>=1"', WT),
    ("test 5 >= 3", WT),
]


@pytest.mark.parametrize("cmd,cwd,expected", _DETECT_CASES)
def test_detects_write_targets(cmd, cwd, expected):
    assert tgt(cmd, cwd) == expected


@pytest.mark.parametrize("cmd,cwd", _ALLOW_CASES)
def test_allow_rows_yield_no_target(cmd, cwd):
    assert tgt(cmd, cwd) == []


def test_bookkeeping_target_is_still_DETECTED_here():
    # The detector is containment/exclusion-agnostic — TODO.md IS a detected write;
    # the GATES exclude it (proved in the integration section). This keeps the single
    # responsibility clean (Cockburn).
    assert tgt("sed -i 's/a/b/' /wt/TODO.md", WT) == ["/wt/TODO.md"]


def test_outside_worktree_still_detected_here():
    assert tgt("cp a /tmp/y", WT) == ["/tmp/y"]              # gate filters, not verb


def test_multiple_utilities_dedup_order_preserved():
    got = tgt("cp a /wt/x && cp b /wt/x && tee /wt/y", WT)   # dup /wt/x collapses
    assert got == ["/wt/x", "/wt/y"]


def test_unparseable_command_fails_open_empty():
    # An unbalanced quote must not raise — fewer targets, never a crash.
    assert tgt("cp a '/wt/x", WT) == []


def test_no_cwd_resolves_against_process_cwd_without_crash():
    out = run.extract_bash_write_targets("cp a /wt/x.py", None)["targets"]
    assert out == ["/wt/x.py"]                               # absolute already


# --------------------------------------------------------------------------- #
# Part B — the extract-bash-targets verb CLI (NUL-terminated contract)
# --------------------------------------------------------------------------- #

def _verb(command, cwd=WT):
    proc = subprocess.run([sys.executable, RUN_PY, "extract-bash-targets"],
                          input=json.dumps({"command": command, "cwd": cwd}),
                          capture_output=True, text=True)
    return proc.returncode, proc.stdout


def test_verb_emits_nul_terminated_targets():
    code, out = _verb("cp a /wt/x && tee /wt/y")
    assert code == 0
    # trailing NUL after EVERY target (incl. the last) → split drops a trailing empty.
    assert out == "/wt/x\0/wt/y\0"
    parts = out.split("\0")
    assert parts[:-1] == ["/wt/x", "/wt/y"] and parts[-1] == ""


def test_verb_empty_on_read_only():
    code, out = _verb("cat /wt/x")
    assert code == 0 and out == ""


def test_verb_spaces_survive_the_nul_transport():
    code, out = _verb("cp src '/wt/my dest.py'")
    assert code == 0 and out == "/wt/my dest.py\0"


# --------------------------------------------------------------------------- #
# Part C — widened, backward-compatible execplan_entry_check
# --------------------------------------------------------------------------- #

def _arm_pending(d, walking, worktree, surface="/topic_THOUGHT.md"):
    payload = {"owner_session_id": "O", "surface_path": surface,
               "execution_pending": True, "walking_session_id": walking,
               "worktree_root": worktree}
    subprocess.run([sys.executable, RUN_PY, "set-active-run"],
                   input=json.dumps(payload), capture_output=True, text=True,
                   env={**os.environ, "EXECPLAN_ACK_STATE_DIR": d})


def test_entry_check_accepts_legacy_scalar_and_new_list():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        target = os.path.join(wt, "src", "file.py")
        # legacy scalar
        assert run.execplan_entry_check("S_OTHER", wt, target, base=d)["block"] is True
        # new list — identical decision, and it reports which target it gated
        dec = run.execplan_entry_check("S_OTHER", wt, [target], base=d)
        assert dec["block"] is True and dec["blocked_target"] == target
        # the walker itself is exempt on both shapes
        assert run.execplan_entry_check("S_W", wt, [target], base=d)["block"] is False


def test_entry_check_list_blocks_on_first_gateable_target():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        outside = os.path.join(d, "elsewhere", "o.py")
        bookkeeping = os.path.join(wt, "TODO.md")
        inside = os.path.join(wt, "src", "code.py")
        # a mix: outside + bookkeeping are NOT gateable; the code path IS → block on it
        dec = run.execplan_entry_check("S_OTHER", wt, [outside, bookkeeping, inside],
                                       base=d)
        assert dec["block"] is True and dec["blocked_target"] == inside


def test_entry_check_all_non_gateable_list_allows():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        outside = os.path.join(d, "elsewhere", "o.py")
        bookkeeping = os.path.join(wt, "TODO.md")
        assert run.execplan_entry_check("S_OTHER", wt, [outside, bookkeeping],
                                        base=d)["block"] is False


def test_entry_check_empty_list_no_block():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        assert run.execplan_entry_check("S_OTHER", wt, [], base=d)["block"] is False
        assert run.execplan_entry_check("S_OTHER", wt, None, base=d)["block"] is False


def test_entry_check_cli_accepts_target_paths_list():
    with tempfile.TemporaryDirectory() as d:
        wt = os.path.join(d, "wt")
        _arm_pending(d, walking="S_W", worktree=wt)
        proc = subprocess.run(
            [sys.executable, RUN_PY, "execplan-entry-check"],
            input=json.dumps({"writing_session_id": "S_OTHER", "worktree_root": wt,
                              "target_paths": [os.path.join(wt, "f.py")]}),
            capture_output=True, text=True,
            env={**os.environ, "EXECPLAN_ACK_STATE_DIR": d})
        assert proc.returncode == 0
        assert json.loads(proc.stdout)["block"] is True


# --------------------------------------------------------------------------- #
# Part D — INTEGRATION: both gate scripts driven as subprocesses
# --------------------------------------------------------------------------- #

def _git_worktree(d):
    """Init a real git repo at d and return its canonical toplevel (the value the
    gate derives via `git -C CWD rev-parse --show-toplevel`)."""
    def g(*a):
        subprocess.run(["git", "-C", d, *a], capture_output=True, text=True)
    subprocess.run(["git", "init", d], capture_output=True, text=True)
    g("config", "user.email", "t@example.com")
    g("config", "user.name", "T")
    top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    return top


def _run_gate(gate_path, envelope, state_dir, extra_env=None):
    env = dict(os.environ)
    env["EXECPLAN_ACK_STATE_DIR"] = state_dir
    env["CLAUDE_CODE_REMOTE"] = "false"
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(["bash", gate_path], input=json.dumps(envelope),
                          capture_output=True, text=True, env=env)
    return proc.returncode, proc.stdout, proc.stderr


def _bash_env(session, wt, command):
    return {"tool_name": "Bash", "session_id": session, "cwd": wt,
            "tool_input": {"command": command}}


# --- ENTRY gate: a non-walker session writing into a pending worktree --- #

@pytest.fixture()
def entry_env():
    d = tempfile.mkdtemp()
    wt = _git_worktree(os.path.join(d, "wt")) if False else None
    # git repo must be the CWD the gate resolves; create it directly at a subdir.
    repo = tempfile.mkdtemp()
    top = _git_worktree(repo)
    state = tempfile.mkdtemp()
    _arm_pending(state, walking="S_WALKER", worktree=top)
    yield top, state
    for p in (d, repo, state):
        subprocess.run(["rm", "-rf", p])


def test_entry_gate_blocks_cp_into_worktree(entry_env):
    top, state = entry_env
    code, _, err = _run_gate(ENTRY_GATE,
                             _bash_env("S_OTHER", top, f"cp payload.py {top}/src/x.py"),
                             state)
    assert code == 2 and "BLOCKED" in err


@pytest.mark.parametrize("util_cmd", [
    "cp payload.py {top}/src/x.py",
    "mv old.py {top}/src/x.py",
    "sed -i 's/a/b/' {top}/src/x.py",
    "tee {top}/src/x.py",
    "install -m 755 b.sh {top}/src/x.py",
    "dd if=/dev/zero of={top}/src/x.py",
])
def test_entry_gate_blocks_every_nonredirect_utility(entry_env, util_cmd):
    top, state = entry_env
    code, _, err = _run_gate(ENTRY_GATE,
                             _bash_env("S_OTHER", top, util_cmd.format(top=top)),
                             state)
    assert code == 2, f"expected BLOCK for: {util_cmd}"


def test_entry_gate_allows_read_only(entry_env):
    top, state = entry_env
    code, _, _ = _run_gate(ENTRY_GATE, _bash_env("S_OTHER", top, f"cat {top}/x"), state)
    assert code == 0


def test_entry_gate_allows_sed_without_inplace(entry_env):
    top, state = entry_env
    code, _, _ = _run_gate(ENTRY_GATE,
                           _bash_env("S_OTHER", top, f"sed -n 's/a/b/p' {top}/x"), state)
    assert code == 0


def test_entry_gate_allows_outside_worktree(entry_env):
    top, state = entry_env
    code, _, _ = _run_gate(ENTRY_GATE,
                           _bash_env("S_OTHER", top, f"cp {top}/x /tmp/y"), state)
    assert code == 0


def test_entry_gate_allows_bookkeeping_target(entry_env):
    top, state = entry_env
    code, _, _ = _run_gate(ENTRY_GATE,
                           _bash_env("S_OTHER", top, f"sed -i 's/a/b/' {top}/TODO.md"),
                           state)
    assert code == 0


def test_entry_gate_allows_fd_redirect(entry_env):
    top, state = entry_env
    code, _, _ = _run_gate(ENTRY_GATE,
                           _bash_env("S_OTHER", top, f"echo x 2> {top}/err"), state)
    assert code == 0


def test_entry_gate_allows_commit_with_gt_in_message(entry_env):
    top, state = entry_env
    code, _, _ = _run_gate(ENTRY_GATE,
                           _bash_env("S_OTHER", top, "git commit -m 'S1: fix > bug'"),
                           state)
    assert code == 0


def test_entry_gate_blocks_multi_target_compound(entry_env):
    top, state = entry_env
    code, _, err = _run_gate(
        ENTRY_GATE,
        _bash_env("S_OTHER", top, f"cp a /tmp/out && cp b {top}/src/x.py"), state)
    assert code == 2 and f"{top}/src/x.py" in err


def test_entry_gate_blocks_spaces_in_path(entry_env):
    top, state = entry_env
    code, _, err = _run_gate(
        ENTRY_GATE,
        _bash_env("S_OTHER", top, f"cp src '{top}/my dest.py'"), state)
    assert code == 2 and "my dest.py" in err


def test_entry_gate_bypass_override_allows_and_logs(entry_env):
    top, state = entry_env
    code, _, err = _run_gate(ENTRY_GATE,
                             _bash_env("S_OTHER", top, f"cp a {top}/src/x.py"),
                             state, extra_env={"BYPASS_EXECPLAN": "1"})
    assert code == 0 and "BYPASS_EXECPLAN=1" in err
    logf = os.path.join(state, "execplan", "bypass.log")
    assert os.path.isfile(logf) and "BYPASS_EXECPLAN=1" in open(logf).read()


def test_entry_gate_python_calls_are_O1_regardless_of_target_count(entry_env):
    """Hard proof of A4's no-per-target-N+1: a python3 shim counts invocations; a
    1-target and a 6-target Bash command each invoke python3 exactly twice (the
    extract-bash-targets verb once + execplan-entry-check once)."""
    top, state = entry_env
    shimdir = tempfile.mkdtemp()
    countf = os.path.join(shimdir, "count")
    shim = os.path.join(shimdir, "python3")
    with open(shim, "w") as fh:
        fh.write("#!/usr/bin/env bash\n"
                 f'printf "x\\n" >> "{countf}"\n'
                 f'exec "{sys.executable}" "$@"\n')
    os.chmod(shim, 0o755)
    env = {"PATH": shimdir + os.pathsep + os.environ["PATH"]}

    def _count(command):
        open(countf, "w").close()
        _run_gate(ENTRY_GATE, _bash_env("S_OTHER", top, command), state, extra_env=env)
        return sum(1 for _ in open(countf))

    one = _count(f"cp a {top}/x1")
    six = _count(" && ".join(f"cp a {top}/x{i}" for i in range(6)))
    assert one == 2, f"1-target should be 2 python calls, got {one}"
    assert six == 2, f"6-target should STILL be 2 python calls, got {six}"
    subprocess.run(["rm", "-rf", shimdir])


# --- WALK gate: the walking session's own writes (checkout invariant) --- #

@pytest.fixture()
def walk_env():
    repo = tempfile.mkdtemp()
    top = _git_worktree(repo)
    state = tempfile.mkdtemp()
    _arm_pending(state, walking="S_WALKER", worktree=top)
    yield top, state, repo
    for p in (repo, state):
        subprocess.run(["rm", "-rf", p])


def _set_current_slice(state, worktree, slice_id, surface="/topic_THOUGHT.md"):
    """Patch the run pointer's current_slice_id (checkout-slice's field) for the
    walk-gate ALLOW case — a gate-wiring fixture, not a checkout-slice test."""
    rid = run.compute_run_id(surface, worktree)
    p = os.path.join(state, f"run-{rid}.json")
    doc = json.loads(open(p).read())
    doc["current_slice_id"] = slice_id
    with open(p, "w") as fh:
        json.dump(doc, fh)


def test_walk_gate_blocks_cp_when_no_slice_checked_out(walk_env):
    top, state, _ = walk_env
    code, _, err = _run_gate(WALK_GATE,
                             _bash_env("S_WALKER", top, f"cp a {top}/src/x.py"), state)
    assert code == 2 and "no slice is checked out" in err


@pytest.mark.parametrize("util_cmd", [
    "cp a {top}/src/x.py",
    "mv a {top}/src/x.py",
    "sed -i 's/a/b/' {top}/src/x.py",
    "tee {top}/src/x.py",
    "install b.sh {top}/src/x.py",
    "dd if=/dev/zero of={top}/src/x.py",
])
def test_walk_gate_blocks_every_nonredirect_utility_pre_checkout(walk_env, util_cmd):
    top, state, _ = walk_env
    code, _, _ = _run_gate(WALK_GATE,
                           _bash_env("S_WALKER", top, util_cmd.format(top=top)), state)
    assert code == 2, f"expected BLOCK for: {util_cmd}"


def test_walk_gate_allows_cp_after_slice_checked_out(walk_env):
    top, state, _ = walk_env
    _set_current_slice(state, top, "S1")
    code, _, _ = _run_gate(WALK_GATE,
                           _bash_env("S_WALKER", top, f"cp a {top}/src/x.py"), state)
    assert code == 0


def test_walk_gate_allows_read_only_and_bookkeeping(walk_env):
    top, state, _ = walk_env
    for cmd in (f"cat {top}/x", f"sed -i 's/a/b/' {top}/TODO.md",
                f"echo x 2> {top}/err", f"cp a {top}/x /tmp/y".replace(" /tmp", " /tmp")):
        code, _, _ = _run_gate(WALK_GATE, _bash_env("S_WALKER", top, cmd), state)
        assert code == 0, cmd


def test_walk_gate_sn_commit_stays_a_commit_not_a_write(walk_env):
    # C12 at gate level: a `Sn:` commit message containing `>` is classified as a
    # commit (blocked for a MISSING CODE RECEIPT), never mis-parsed as a redirect
    # write (which would block for "no slice checked out"). Commit-first ordering.
    top, state, _ = walk_env
    _set_current_slice(state, top, "S1")     # slice checked out → a write would ALLOW
    code, _, err = _run_gate(WALK_GATE,
                             _bash_env("S_WALKER", top, 'git commit -m "S1: fix > bug"'),
                             state)
    assert code == 2 and "code-layer receipt" in err


def test_walk_gate_non_sn_commit_is_allowed(walk_env):
    top, state, _ = walk_env
    code, _, _ = _run_gate(WALK_GATE,
                           _bash_env("S_WALKER", top, 'git commit -m "routine cleanup"'),
                           state)
    assert code == 0


# --------------------------------------------------------------------------- #
# Part E — symlink-spelled containment, end to end through BOTH gate scripts
# (execplan-gate-blast-radius A1, gap G6)
#
# Both gates used to compare UNRESOLVED paths, so a target spelled through a
# symlinked ancestor read as "outside the worktree" and the gate silently failed
# OPEN — the edit was waved through rather than contained. This is live, not
# hypothetical: ~/Projects is a symlink to ~/repos/Projects, so one checkout has
# two spellings and a run pointer armed under one of them saw nothing written
# through the other.
# --------------------------------------------------------------------------- #

def _edit_env(session, cwd, file_path):
    return {"tool_name": "Edit", "session_id": session, "cwd": cwd,
            "tool_input": {"file_path": file_path}}


def _agent_env(session, cwd, prompt):
    return {"tool_name": "Agent", "session_id": session, "cwd": cwd,
            "tool_input": {"prompt": prompt}}


def test_a6_untagged_agent_spawn_is_not_probed(entry_env):
    # A6 / G8: the gate synthesized an in-worktree sentinel path for EVERY Agent
    # spawn, so a read-only checker subagent — which cannot write at all — was
    # blocked, and clearing it cost a permanent exemption. An untagged spawn is not
    # a slice-implementation spawn and now yields no target at all.
    top, state = entry_env
    code, _, _ = _run_gate(
        ENTRY_GATE,
        _agent_env("S_OTHER", top,
                   "You are an independent fact-checker. Verify the claims at ..."),
        state)
    assert code == 0, "a read-only checker spawn must not be gated"


def test_a6_tagged_agent_spawn_is_still_probed(entry_env):
    # The narrowing is bounded: a spawn that DOES stand in for slice work still
    # probes exactly as before, so the walker-exemption predicate still decides.
    top, state = entry_env
    code, _, err = _run_gate(
        ENTRY_GATE,
        _agent_env("S_OTHER", top, "[SLICE:S2] implement the thing"), state)
    assert code == 2 and "BLOCKED" in err


def test_a6_tagged_agent_spawn_exempts_the_walker(entry_env):
    # And the walker's own tagged spawn is still exempt.
    top, state = entry_env
    code, _, _ = _run_gate(
        ENTRY_GATE,
        _agent_env("S_WALKER", top, "[SLICE:S2] implement the thing"), state)
    assert code == 0


def test_a6_real_redirect_is_still_detected_after_the_narrowing(entry_env):
    # The `=`-prefix narrowing reduces detection on a security boundary, so the
    # detect side is asserted alongside the allow side: ordinary redirects, append
    # redirects and non-redirect write utilities all still block.
    top, state = entry_env
    for cmd in (f"echo x > {top}/src/a.py",
                f"echo x >> {top}/src/a.py",
                f"cp payload {top}/src/a.py"):
        code, _, _ = _run_gate(ENTRY_GATE, _bash_env("S_OTHER", top, cmd), state)
        assert code == 2, f"real write must still be blocked: {cmd}"


@pytest.fixture()
def alias_env():
    """A real git repo plus a symlink ALIAS to it. `cwd` stays the canonical
    toplevel (what git reports), while the write target is spelled through the
    alias — the exact shape of the live ~/Projects → ~/repos/Projects case."""
    repo = tempfile.mkdtemp()
    top = _git_worktree(repo)
    aliasdir = tempfile.mkdtemp()
    alias = os.path.join(aliasdir, "alias")
    os.symlink(top, alias)
    state = tempfile.mkdtemp()
    yield top, alias, state
    for p in (repo, aliasdir, state):
        subprocess.run(["rm", "-rf", p])


def test_entry_gate_blocks_symlink_spelled_edit(alias_env):
    # A non-walker's Edit spelled through the alias must be BLOCKED. Before A1 this
    # exited 0 (fail-open) because f"{alias}/src/x.py" does not prefix-match the
    # canonical toplevel.
    top, alias, state = alias_env
    _arm_pending(state, walking="S_WALKER", worktree=top)
    code, _, err = _run_gate(ENTRY_GATE,
                             _edit_env("S_OTHER", top, f"{alias}/src/x.py"), state)
    assert code == 2 and "BLOCKED" in err


def test_entry_gate_blocks_symlink_spelled_bash_write(alias_env):
    # Same leak via the Bash write detector, not just Edit.
    top, alias, state = alias_env
    _arm_pending(state, walking="S_WALKER", worktree=top)
    code, _, err = _run_gate(ENTRY_GATE,
                             _bash_env("S_OTHER", top, f"cp payload.py {alias}/src/x.py"),
                             state)
    assert code == 2 and "BLOCKED" in err


def test_walk_gate_blocks_symlink_spelled_edit_pre_checkout(alias_env):
    # The walk gate carries the SAME leak and the same fix: the walker's own edit,
    # spelled through the alias with no slice checked out, must still be blocked by
    # the checkout invariant.
    top, alias, state = alias_env
    _arm_pending(state, walking="S_WALKER", worktree=top)
    code, _, err = _run_gate(WALK_GATE,
                             _edit_env("S_WALKER", top, f"{alias}/src/x.py"), state)
    assert code == 2 and "no slice is checked out" in err


def test_symlink_alias_does_not_widen_the_gate_to_outside_paths(alias_env):
    # A1 must not become "everything is inside": a genuinely-outside path is still
    # allowed through both gates, whichever way it is spelled.
    top, alias, state = alias_env
    _arm_pending(state, walking="S_WALKER", worktree=top)
    outside = os.path.join(tempfile.mkdtemp(), "x.py")
    code_e, _, _ = _run_gate(ENTRY_GATE, _edit_env("S_OTHER", top, outside), state)
    code_w, _, _ = _run_gate(WALK_GATE, _edit_env("S_WALKER", top, outside), state)
    assert code_e == 0 and code_w == 0


def _arm_phase_gate(d, *, owner, walker, worktree, targets=None,
                    surface="/topic_THOUGHT.md"):
    payload = {"owner_session_id": owner, "surface_path": surface,
               "execution_pending": True, "walking_session_id": walker,
               "worktree_root": worktree}
    if targets is not None:
        payload["pending_handoff"] = {"type": "continue-the-run",
                                      "dispatch": "attended", "slice_id": "S1",
                                      "write_targets": targets}
    subprocess.run([sys.executable, RUN_PY, "set-active-run"],
                   input=json.dumps(payload), capture_output=True, text=True,
                   env={**os.environ, "EXECPLAN_ACK_STATE_DIR": d})


def test_entry_gate_armed_phase_frees_strangers_and_holds_the_owner(entry_env):
    # A3 end to end through the hook: an armed pointer (no walker) must let an
    # unrelated session work, and must still hold the session that approved the plan.
    # This is the diagnosed harm and its one deliberate exception, at the surface the
    # operator actually meets.
    top, state = entry_env
    for f in os.listdir(state):                      # drop the in-flight fixture
        if f.startswith("run-"):
            os.remove(os.path.join(state, f))
    _arm_phase_gate(state, owner="S_OWNER", walker=None, worktree=top)

    code_stranger, _, _ = _run_gate(
        ENTRY_GATE, _edit_env("S_STRANGER", top, f"{top}/src/x.py"), state)
    assert code_stranger == 0, "an armed pointer must not stop an unrelated session"

    code_owner, _, err = _run_gate(
        ENTRY_GATE, _edit_env("S_OWNER", top, f"{top}/src/x.py"), state)
    assert code_owner == 2
    assert "has not been walked yet" in err
    assert "Phase: armed" in err


def test_entry_gate_in_flight_message_shows_walker_and_age(entry_env):
    # A3: liveness is DISPLAYED at the moment a session is stopped, so the operator
    # decides on visible fact rather than reconstructing it from timestamps.
    top, state = entry_env
    code, _, err = _run_gate(
        ENTRY_GATE, _edit_env("S_OTHER", top, f"{top}/src/x.py"), state)
    assert code == 2
    assert "Walker: S_WALKER" in err
    assert "Last activity:" in err


def test_entry_gate_in_flight_block_is_scoped_to_declared_write_targets(entry_env):
    # A4 end to end: with a populated declaration only the walked slice's files are
    # blocked; an unrelated file in the same checkout is free.
    top, state = entry_env
    for f in os.listdir(state):
        if f.startswith("run-"):
            os.remove(os.path.join(state, f))
    _arm_phase_gate(state, owner="S_OWNER", walker="S_WALKER", worktree=top,
                    targets=["src/gate.py"])
    blocked, _, err = _run_gate(
        ENTRY_GATE, _edit_env("S_OTHER", top, f"{top}/src/gate.py"), state)
    free, _, _ = _run_gate(
        ENTRY_GATE, _edit_env("S_OTHER", top, f"{top}/docs/notes.md"), state)
    assert blocked == 2
    assert "declared write targets: true" in err
    assert free == 0, "a file outside the walked slice's declaration must be free"


def test_bookkeeping_exclusion_still_wins_over_symlink_containment(alias_env):
    # The exclusion carve-out is checked BEFORE containment and is unchanged by A1:
    # an alias-spelled TODO.md is still not gated (a walker must be able to touch
    # bookkeeping while no slice is checked out).
    top, alias, state = alias_env
    _arm_pending(state, walking="S_WALKER", worktree=top)
    code, _, _ = _run_gate(ENTRY_GATE,
                           _edit_env("S_OTHER", top, f"{alias}/TODO.md"), state)
    assert code == 0

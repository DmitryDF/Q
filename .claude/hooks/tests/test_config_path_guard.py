#!/usr/bin/env python3
"""Hook-decision regression test for config_path_guard.py + guard-config-paths.sh.

Every candidate command is passed as a LITERAL STRING to decide()/the module and
its verdict asserted — the test NEVER eval/`bash -c`s a payload, so a failed guard
can never itself execute the destructive command (Review-10 §1.1). Any real dummy
dir is made with mkdtemp; the `_guardtest_*` path is only ever a literal string.
Run: python3 test_config_path_guard.py  (exit 0 = all pass).
"""
import json
import os
import subprocess
import sys
import tempfile

HOOKS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HOOKS)
import config_path_guard as g  # noqa: E402

FAILS = []


def check(name, cond):
    print(("  ok  " if cond else "  FAIL") + f"  {name}")
    if not cond:
        FAILS.append(name)


def main():
    FH = tempfile.mkdtemp(prefix="guardtest_home_")
    base = os.path.join(FH, ".claude")
    for d in ("hooks", "agents", "rules", "skills", "logs", "cache", "state", "projects"):
        os.makedirs(os.path.join(base, d), exist_ok=True)
    open(os.path.join(base, "settings.json"), "w").close()
    proj = tempfile.mkdtemp(prefix="guardtest_proj_")  # a real "user project" cwd
    C = base  # a config cwd for git-op cases

    def d(cmd, cwd=proj, marker=False):
        return g.decide(cmd, cwd, FH, marker)

    print("== decide() unit cases (literal payloads, never executed) ==")
    # (a) config-path destructive → block (incl. the disposable dummy, literal only)
    check("rm -rf hooks (abs) → block", d(f"rm -rf {base}/hooks")["block"])
    check("rm -rf _guardtest dummy (literal) → block",
          d(f"rm -rf {base}/hooks/_guardtest_{os.getpid()}")["block"])
    check("rm settings.json → block", d(f"rm {base}/settings.json")["block"])
    check("truncate redirect > settings.json → block", d(f"echo x > {base}/settings.json")["block"])
    check("find rules -delete → block", d(f"find {base}/rules -name '*.md' -delete")["block"])
    check("dd of=hooks/x → block", d(f"dd if=/dev/zero of={base}/hooks/x")["block"])
    check("rm -rf skills/plan → block", d(f"rm -rf {base}/skills/plan")["block"])
    # (b) logs/cache cleanup (mv/rm) → allow (not protected)
    check("rm -rf logs/old → allow", not d(f"rm -rf {base}/logs/old")["block"])
    check("rm -rf cache → allow", not d(f"rm -rf {base}/cache")["block"])
    check("rm -rf state/x → allow", not d(f"rm -rf {base}/state/x")["block"])
    # project-relative false-block guard (Review-10 §2.4)
    check("rm -rf hooks in user project → allow", not d("rm -rf hooks", cwd=proj)["block"])
    # append (not truncate) → allow
    check("append >> hooks/foo → allow", not d(f"echo x >> {base}/hooks/foo")["block"])
    # git working-tree ops out of A5 scope → allow (A6 policy covers them)
    check("git clean -fd → allow (A5 no git ops)", not d("git clean -fd", cwd=C)["block"])
    check("git restore . → allow (A5 no git ops)", not d("git restore .", cwd=C)["block"])
    # per-segment tying: rm targets /tmp, settings only read → allow
    check("cat settings && rm /tmp/x → allow (per-segment)",
          not d(f"cat {base}/settings.json && rm /tmp/guardtest_x")["block"])
    # (e) marker present → exempt even a real config-path destructive
    check("marker present → rm -rf hooks EXEMPTED (allow)",
          not d(f"rm -rf {base}/hooks", marker=True)["block"])
    # (d) NOT under claude-promote ancestry (no marker) → NOT exempted
    check("no marker → rm -rf hooks NOT exempted (block)", d(f"rm -rf {base}/hooks")["block"])

    print("== module main() via stdin (portability: python runs on this OS) ==")
    def run_module(payload_json, env_extra=None):
        env = dict(os.environ, HOME=FH, GUARD_HOME=FH)
        env.pop("CLAUDE_CONFIG_DEPLOY_EXEMPT", None)
        if env_extra:
            env.update(env_extra)
        p = subprocess.run([sys.executable, os.path.join(HOOKS, "config_path_guard.py")],
                           input=payload_json, text=True, capture_output=True, env=env)
        return p
    blk = json.dumps({"tool_name": "Bash", "tool_input": {"command": f"rm -rf {base}/hooks"}, "cwd": proj})
    alw = json.dumps({"tool_name": "Bash", "tool_input": {"command": f"rm -rf {base}/logs/x"}, "cwd": proj})
    non = json.dumps({"tool_name": "Read", "tool_input": {}, "cwd": proj})
    r = run_module(blk); check("module block → stdout BLOCK, exit 0", r.stdout.splitlines()[0] == "BLOCK" and r.returncode == 0)
    r = run_module(alw); check("module allow → stdout ALLOW, exit 0", r.stdout.splitlines()[0] == "ALLOW" and r.returncode == 0)
    r = run_module(non); check("module non-Bash tool → ALLOW", r.stdout.splitlines()[0] == "ALLOW")
    r = run_module("not json at all"); check("module bad json → ALLOW + exit 0 (fail-open)", r.stdout.splitlines()[0] == "ALLOW" and r.returncode == 0)

    print("== shell wrapper exit codes (block=2, allow=0, fail-open=0) ==")
    wrapper = os.path.join(HOOKS, "guard-config-paths.sh")
    def run_wrapper(payload_json, config_dir):
        env = dict(os.environ, HOME=FH, GUARD_HOME=FH, CLAUDE_CONFIG_DIR=config_dir)
        env.pop("CLAUDE_CONFIG_DEPLOY_EXEMPT", None)
        return subprocess.run(["bash", wrapper], input=payload_json, text=True, capture_output=True, env=env)
    CLONE = os.path.dirname(HOOKS)  # CLAUDE_CONFIG_DIR/hooks/config_path_guard.py
    r = run_wrapper(blk, CLONE); check("wrapper block → exit 2 + remediation on stderr",
                                       r.returncode == 2 and "backup" in r.stderr.lower())
    r = run_wrapper(alw, CLONE); check("wrapper allow → exit 0", r.returncode == 0)
    r = run_wrapper("not json", CLONE); check("wrapper bad json → exit 0 (fail-open)", r.returncode == 0)
    # fail-open on a broken module: point CLAUDE_CONFIG_DIR at a dir whose module raises on import
    broken = tempfile.mkdtemp(prefix="guardtest_broken_")
    os.makedirs(os.path.join(broken, "hooks"), exist_ok=True)
    with open(os.path.join(broken, "hooks", "config_path_guard.py"), "w") as f:
        f.write("import sys; sys.exit(3)\n")
    r = run_wrapper(blk, broken)
    check("wrapper broken module → exit 0 (fail-open WITH warning)",
          r.returncode == 0 and "failing open" in r.stderr.lower())

    print()
    if FAILS:
        print(f"RESULT: FAIL ({len(FAILS)} case(s)): " + "; ".join(FAILS))
        return 1
    print("RESULT: PASS — all config-path guard cases green")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Best-effort PreToolUse Bash guard for ~/.claude safety-critical config paths.

Standalone, vendor-neutral check-logic (per skill-location.md): a pure `decide()`
plus a thin `main()` that reads the PreToolUse JSON on stdin and prints a verdict
token. The shell wrapper `guard-config-paths.sh` owns the fail-open-with-warning
`trap`; this module also fails open on its own internal error (defence in depth).

Scope + honest limits (A5 of readonly-skills-structural-safety):
  * Blocks the COMMON destructive shapes (rm/rmdir/unlink/shred/truncate/dd/
    find -delete / truncating `>` redirect) whose RESOLVED target is under a
    safety-critical ~/.claude config subpath: hooks/ agents/ rules/ skills/ and
    the file settings.json. It does NOT protect logs/ cache/ state/ projects/ —
    routine administrative cleanup there is allowed.
  * It is a SURFACE-LEVEL, best-effort static backstop — NOT a robust defence
    against Turing-complete Bash evasion (no in-hook parser can be), NOT a git
    guard (force-push / branch deletion is git-policy.md §4 + the pre-push hook),
    and it cannot stop a non-Bash payload (python/node/a binary). The real
    guarantee is the read-only tool grant (readonly-checker); this only reduces
    the accidental-destroy blast radius for surfaces that legitimately hold a
    shell (the main session, producer subagents).
  * It EXEMPTS a legitimate deployment when the deploy-exempt marker file is
    present. That marker is a deliberate signal a full-shell entity can spoof —
    so the exemption is a convenience to avoid false-blocking the real deploy
    path, NOT an adversary control (A1 is the control).
"""
import json
import os
import re
import shlex
import sys

DESTRUCTIVE_CMDS = {"rm", "rmdir", "unlink", "shred", "truncate", "dd"}
# Directories under ~/.claude whose contents are safety-critical config.
PROTECTED_DIRS = ("hooks", "agents", "rules", "skills")
# Individual protected files under ~/.claude.
PROTECTED_FILES = ("settings.json",)
# Deliberately NOT protected (routine cleanup allowed): logs cache state projects
# todos statsig sessions debug history.jsonl ...

_SEG_SPLIT = re.compile(r"&&|\|\||(?<!>)\|(?!\|)|;|\n")
# Truncating redirect: a single '>' or '>|' NOT part of '>>' (append), capturing
# the target (quoted or bare). Append (>>) is intentionally NOT matched.
_TRUNC_RE = re.compile(r"(?<![>\d])>\|?\s*(\"[^\"]+\"|'[^']+'|[^\s;&|<>]+)")


def _guard_home():
    # GUARD_HOME lets the regression test point at a sandbox home; production
    # uses the real HOME. Never reads live config to decide.
    return os.environ.get("GUARD_HOME") or os.path.expanduser("~")


def marker_path(home):
    return os.path.join(home, ".claude", "state", ".claude-promote-active")


def _protected_roots(home):
    base = os.path.realpath(os.path.join(home, ".claude"))
    dirs = [os.path.join(base, d) for d in PROTECTED_DIRS]
    files = [os.path.join(base, f) for f in PROTECTED_FILES]
    return dirs, files


def _resolve(token, cwd):
    """Resolve a raw path token to an absolute, symlink-normalized path against
    the command's actual execution CWD (Review-10 §2.4 — a relative token like
    `hooks` inside a user project must resolve to <project>/hooks, never
    ~/.claude/hooks)."""
    token = token.strip().strip("'\"")
    if not token:
        return None
    p = os.path.expanduser(token)
    if not os.path.isabs(p):
        p = os.path.join(cwd, p)
    # realpath resolves symlinks on the existing prefix and normalizes '..'
    # without requiring the leaf to exist.
    return os.path.realpath(p)


def _is_protected(abs_path, prot_dirs, prot_files):
    if abs_path is None:
        return False
    if abs_path in prot_files:
        return True
    for d in prot_dirs:
        if abs_path == d or abs_path.startswith(d + os.sep):
            return True
    return False


def _segment_hit(seg, cwd, prot_dirs, prot_files):
    """Return the offending resolved path for a destructive segment, else None.
    Ties a destructive verb to ITS OWN path args (per-segment) so an unrelated
    protected-path mention elsewhere in the command does not false-block."""
    try:
        tokens = shlex.split(seg)
    except ValueError:
        tokens = seg.split()
    # Truncating redirect anywhere in the segment (overwrites file content).
    for m in _TRUNC_RE.finditer(seg):
        tgt = _resolve(m.group(1), cwd)
        if _is_protected(tgt, prot_dirs, prot_files):
            return tgt
    if not tokens:
        return None
    cmd = os.path.basename(tokens[0])
    args = tokens[1:]
    if cmd in DESTRUCTIVE_CMDS:
        if cmd == "dd":
            for a in args:
                if a.startswith("of="):
                    tgt = _resolve(a[3:], cwd)
                    if _is_protected(tgt, prot_dirs, prot_files):
                        return tgt
            return None
        for a in args:
            if a.startswith("-") or a == "--":
                continue
            tgt = _resolve(a, cwd)
            if _is_protected(tgt, prot_dirs, prot_files):
                return tgt
        return None
    if cmd == "find":
        destructive = "-delete" in args or (
            "-exec" in args and any(t in ("rm", "unlink") for t in args)
        )
        if destructive:
            for a in args:
                if a.startswith("-") or a in ("rm", "unlink", "{}", ";", "+", "\\;"):
                    continue
                tgt = _resolve(a, cwd)
                if _is_protected(tgt, prot_dirs, prot_files):
                    return tgt
    return None


def decide(command, cwd, home, marker_exists):
    """Pure decision. Returns {'block': bool, 'reason': str, 'remediation': str,
    'target': str|None}. Never raises for ordinary input; callers still wrap it
    fail-open as defence in depth."""
    if marker_exists:
        return {"block": False, "reason": "deploy-exempt (claude-promote marker present)",
                "remediation": "", "target": None}
    prot_dirs, prot_files = _protected_roots(home)
    cwd = cwd or home
    for seg in _SEG_SPLIT.split(command or ""):
        tgt = _segment_hit(seg, cwd, prot_dirs, prot_files)
        if tgt:
            rem = (
                "Safe alternative — move it to a timestamped backup instead of "
                "destroying it:\n"
                f'  mv "{tgt}" "{tgt}.bak-$(date +%Y%m%d%H%M%S)"\n'
                "Protected: ~/.claude/{hooks,agents,rules,skills,settings.json}. "
                "logs/cache/state/projects are NOT protected (routine cleanup is "
                "allowed).\n"
                "This is a best-effort backstop, not the real guarantee (that is the "
                "read-only tool grant). A legitimate deploy should run via "
                "claude-promote (which sets the deploy-exempt marker)."
            )
            return {"block": True,
                    "reason": f"destructive command targets a safety-critical ~/.claude config path: {tgt}",
                    "remediation": rem, "target": tgt}
    return {"block": False, "reason": "no config-path violation", "remediation": "", "target": None}


def main():
    # Python-level fail-open-with-warning (the shell wrapper is the outer trap).
    try:
        data = json.load(sys.stdin)
    except Exception as e:  # noqa: BLE001
        print("ALLOW")
        print(f"[config_path_guard] warning: unreadable input, failing open: {e}", file=sys.stderr)
        return 0
    try:
        if data.get("tool_name") != "Bash":
            print("ALLOW")
            return 0
        command = (data.get("tool_input") or {}).get("command", "") or ""
        cwd = data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
        home = _guard_home()
        marker_exists = os.path.exists(marker_path(home)) or \
            os.environ.get("CLAUDE_CONFIG_DEPLOY_EXEMPT") == "1"
        decision = decide(command, cwd, home, marker_exists)
    except Exception as e:  # noqa: BLE001 — a guard bug must never brick Bash
        print("ALLOW")
        print(f"[config_path_guard] warning: internal error, failing open: {e}", file=sys.stderr)
        return 0
    if decision["block"]:
        print("BLOCK")
        print(f"BLOCKED (config-path guard): {decision['reason']}")
        print(decision["remediation"])
        return 0
    print("ALLOW")
    return 0


if __name__ == "__main__":
    sys.exit(main())

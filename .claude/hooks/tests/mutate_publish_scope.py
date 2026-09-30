#!/usr/bin/env python3
"""Mutation harness for six of the /ninja-fix cases in test_publish_scope_e2e.sh.

COVERED: 5.3, 5.4, 5.5, 5.6, 5.9, 5.10.  NOT COVERED: 5.7 and 5.8 — 5.7 is the
managed-scope branch's primary assertion and has no mutation; 5.8 is its
anti-vacuity partner. Do not read "mutation-tested" as covering the whole 5.3-5.10
range.

WHY THIS FILE IS RETAINED. The plan that added those cases cited a mutation run as
its evidence for trusting them, but the harnesses were scratch scripts in a session
scratchpad — so the evidence was not re-openable, which is the very failure class
that plan spent eight slices documenting ("a green result cited from an artifact
nobody can open again"). This is that harness, kept.

WHAT A MUTATION IS FOR. A case that still passes when the property it asserts is
broken is asserting nothing. Each mutation below breaks exactly one property and
names the case(s) that must therefore FAIL. A mutation that is NOT caught is a
finding about the test, not about the code.

Two mutation kinds:
  * TEXT   — rewrite the e2e script itself (run from a temp copy).
  * CONFIG — mirror the live config with symlinks, replace ONE file, and re-run
             against it via CLAUDE_CONFIG_DIR. The live tree is never written.

Usage:  python3 mutate_publish_scope.py [--only M1,M3]
Exit 0 = every mutation caught.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

CFG = os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
TEST = os.path.join(CFG, 'hooks/tests/test_publish_scope_e2e.sh')
SKILL_REL = 'skills/ninja-fix/SKILL.md'
# Capture EVERY case label, not just the 5.x ones. Scoping this to `5\.\d+` made the
# per-label scan blind to sections 1-4 and 6-8 entirely. Widening it is necessary but
# NOT sufficient: the suite reuses labels, so verdicts() still drops 15 of 52
# verdicts (see its docstring). The safety gates therefore read tally(), which parses
# the suite's own RESULT line and cannot be defeated by duplicate labels.
#
# Anchored to EXACTLY two leading spaces, which is not cosmetic. check() prints a
# verdict as '  PASS  <label>', and cases 8d/8e feed `tail -3` of
# test_commit_gate_scope.sh — a suite printing the IDENTICAL '  PASS  %s' shape —
# into a FAIL's detail. An unanchored pattern parses those as this suite's verdicts,
# and since verdicts() is a dict keyed by label, a colliding label could overwrite a
# real one.
#
# The anchor alone is NOT sufficient and was briefly documented as if it were: the
# 8-space indent used to live in check()'s format string, so it shifted only the
# FIRST detail line while lines 2-3 kept the sub-suite's own two-space indent and
# still matched. check() now indents EVERY detail line, which is what actually makes
# this anchor exact. Both halves are required — widening this pattern, or reverting
# check()'s per-line indent, reopens the hole.
CASE_RE = re.compile(r'^ {2}(PASS|FAIL) {2}(\S+)')


def _key(label):
    """Natural sort for mixed labels: '1a', '5.3', '5.10', '8f'."""
    return [int(p) if p.isdigit() else p for p in re.split(r'(\d+)', label)]

# Collateral a mutation is EXPECTED to cause, with the reason. Cases 5.3-5.5 share
# one fixture repo and run in sequence, so a mutation that changes what 5.3 does to
# that repo necessarily changes what 5.5 observes. Declaring it here keeps a real
# cascade from being reported as an anomaly — and keeps an UNdeclared collateral
# failure meaningful, which is the point of reporting collateral at all.
EXPECTED_COLLATERAL = {
    'M1': {'5.5': 'M1 lets 5.3 publish succeed, so domain.txt is already committed '
                  'by the time 5.5 runs; its publish finds nothing to commit and '
                  'HEAD does not move. A cascade through shared fixture state, not '
                  'a vacuous assertion.'},
}

# --- TEXT mutations: (id, description, old, new, cases that must fail) ----------
TEXT_MUTATIONS = [
    ('M1', 'the commit gate cannot block (bypass it at the 5.3 publish)',
     'OUT="$(python3 "$CS_PY" publish --repo "$B" -m "[ninja-fix] fix from primary checkout" -- "domain.txt" 2>&1)"; rc=$?',
     'OUT="$(ALLOW_OUT_OF_TREE=1 python3 "$CS_PY" publish --repo "$B" -m "[ninja-fix] fix from primary checkout" -- "domain.txt" 2>&1)"; rc=$?',
     ['5.3', '5.4']),
    ('M2', 'the override degrades to an unscoped whole-index commit at 5.5',
     'OUT="$(ALLOW_OUT_OF_TREE=1 python3 "$CS_PY" publish --repo "$B" -m "[ninja-fix] fix from primary checkout" -- "domain.txt" 2>&1)"\n[ "$(head_of "$B")" != "$H" ]',
     'OUT="$(ALLOW_OUT_OF_TREE=1 ALLOW_UNSCOPED_COMMIT=1 git -C "$B" add -A && ALLOW_OUT_OF_TREE=1 ALLOW_UNSCOPED_COMMIT=1 git -C "$B" commit -qm "[ninja-fix] fix from primary checkout" 2>&1)"\n[ "$(head_of "$B")" != "$H" ]',
     ['5.5']),
    # M5 disables the very guard that stops a write into the live frozen repo, so the
    # mutated command MUST keep --dry-run. On 2026-09-18 an earlier form of this
    # mutation dropped it and committed `rules/git-policy.md` into `~/.claude/.git`
    # (undone with `git reset --mixed`). The anchor therefore includes `--dry-run`:
    # if case 5.10 is ever changed back to a live publish, this anchor stops matching
    # and the harness reports a SKIP rather than silently writing to the real repo.
    ('M5', 'the DEFAULT frozen-root resolution is overridden away at 5.10',
     'OUT="$(python3 "$CS_PY" publish --dry-run --repo "$HOME/.claude" -m "[test] must refuse" -- "rules/git-policy.md" 2>&1)"; rc=$?',
     'OUT="$(COMMIT_SCOPE_FROZEN_ROOT="$T/nowhere" python3 "$CS_PY" publish --dry-run --repo "$HOME/.claude" -m "[test] must refuse" -- "rules/git-policy.md" 2>&1)"; rc=$?',
     ['5.10']),
]

# --- CONFIG mutations: (id, description, file, anchor line prefix, cases) -------
CONFIG_MUTATIONS = [
    ('M3', 'the out-of-tree guidance is removed from the ninja-fix SKILL',
     SKILL_REL, '- **If the publish is refused with `commit is outside any topic worktree`', ['5.6']),
    ('M4', 'the managed-scope routing is removed from the ninja-fix SKILL',
     SKILL_REL, '  - If the file is in the **managed config scope**', ['5.9']),
]


RESULT_RE = re.compile(r'^RESULT:\s+(\d+) passed,\s+(\d+) failed')


def verdicts(out):
    """Per-label verdicts. LOSSY BY CONSTRUCTION — see tally() below.

    The suite reuses labels across checks (1b x4, 3b x4, 2b x3, 4 x3, ...), so this
    dict keeps only the LAST occurrence of each and drops 15 of the 52 verdicts
    (37 distinct labels: 1b x4, 3b x4, 2b x3, 4 x3, and 1d/1e/3a/4a/6 x2).
    That is fine for naming WHICH case a mutation flipped, and useless as a safety
    gate: a FAIL in a non-final duplicate never appears here. Every go/no-go decision
    reads tally() instead.
    """
    return {m.group(2): m.group(1) for m in (CASE_RE.match(l) for l in out.splitlines()) if m}


def tally(out):
    """(passed, failed) from the suite's own RESULT line — the authoritative count.

    Unlike verdicts() this cannot be defeated by duplicate labels, so it is what the
    baseline-greenness gate and the aggregate collateral check are built on.
    Returns None if the suite produced no RESULT line (crash, timeout), which callers
    must treat as a failed run rather than as zero failures.
    """
    for line in out.splitlines():
        m = RESULT_RE.match(line)
        if m:
            return int(m.group(1)), int(m.group(2))
    return None


def run(script_text=None, cfg_dir=None):
    path, env = TEST, dict(os.environ)
    tmp = None
    if script_text is not None:
        fh = tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False)
        fh.write(script_text)
        fh.close()
        path = tmp = fh.name
    if cfg_dir:
        env['CLAUDE_CONFIG_DIR'] = cfg_dir
    try:
        p = subprocess.run(['bash', path], capture_output=True, text=True,
                           env=env, timeout=1800)
        return p.stdout + p.stderr
    finally:
        if tmp:
            os.unlink(tmp)


def mirror_config(dst, rel, new_text):
    """Symlink every entry of CFG except the one file being mutated."""
    os.makedirs(dst)
    parts = rel.split('/')
    for name in os.listdir(CFG):
        if name == parts[0]:
            continue
        os.symlink(os.path.join(CFG, name), os.path.join(dst, name))
    src_dir, dst_dir = CFG, dst
    for depth, seg in enumerate(parts[:-1]):
        src_dir, dst_dir = os.path.join(src_dir, seg), os.path.join(dst_dir, seg)
        os.makedirs(dst_dir)
        keep = parts[depth + 1]
        for name in os.listdir(src_dir):
            if name != keep:
                os.symlink(os.path.join(src_dir, name), os.path.join(dst_dir, name))
    with open(os.path.join(dst_dir, parts[-1]), 'w') as fh:
        fh.write(new_text)


def drop_bullet(text, anchor):
    """Remove one markdown bullet: the anchor line through the next top-level '- '."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if l.startswith(anchor)), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('- ')), len(lines))
    return ''.join(lines[:start] + lines[end:])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--only', help='comma-separated mutation ids')
    args = ap.parse_args()
    only = set(args.only.split(',')) if args.only else None

    orig = open(TEST).read()
    print('=== baseline ===')
    out = run()
    base, base_tally = verdicts(out), tally(out)
    print('  ', {k: base[k] for k in sorted(base, key=_key)})
    # Gate on the suite's OWN count, not on the lossy per-label dict: duplicate labels
    # hide a FAIL in any non-final occurrence, so `'FAIL' in base.values()` could pass
    # a red baseline. A missing RESULT line means the suite did not finish.
    if base_tally is None:
        print('BASELINE PRODUCED NO RESULT LINE — the suite crashed or timed out.')
        return 1
    print('   baseline tally: %d passed, %d failed' % base_tally)
    if base_tally[1] != 0:
        print('BASELINE NOT GREEN — fix the suite before mutating.')
        return 1
    base_passed = base_tally[0]

    ok = True
    for mid, desc, old, new, cases in TEXT_MUTATIONS:
        if only and mid not in only:
            continue
        print('\n=== %s: %s ===' % (mid, desc))
        if old not in orig:
            print('   SKIP — anchor not found; the suite has drifted from this harness.')
            ok = False
            continue
        out = run(script_text=orig.replace(old, new, 1))
        ok &= report(verdicts(out), cases, mid, tally(out), base_passed)

    for mid, desc, rel, anchor, cases in CONFIG_MUTATIONS:
        if only and mid not in only:
            continue
        print('\n=== %s: %s ===' % (mid, desc))
        mutated = drop_bullet(open(os.path.join(CFG, rel)).read(), anchor)
        if mutated is None:
            print('   SKIP — anchor not found in %s.' % rel)
            ok = False
            continue
        tmp = tempfile.mkdtemp(prefix='mutate-cfg-')
        try:
            mirror_config(os.path.join(tmp, 'cfg'), rel, mutated)
            out = run(cfg_dir=os.path.join(tmp, 'cfg'))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        ok &= report(verdicts(out), cases, mid, tally(out), base_passed)

    print('\nMUTATION RESULT:', 'all mutations caught' if ok else 'VACUOUS CASE(S) PRESENT')
    return 0 if ok else 1


def report(got, cases, mid, mut_tally, base_passed):
    print('  ', {k: got[k] for k in sorted(got, key=_key)})
    ok = True
    for c in cases:
        if got.get(c) == 'FAIL':
            print('   CAUGHT   %s failed under %s' % (c, mid))
        else:
            print('   VACUOUS  %s still %s under %s' % (c, got.get(c, 'MISSING'), mid))
            ok = False

    expected = EXPECTED_COLLATERAL.get(mid, {})
    for k in sorted((k for k, v in got.items() if v == 'FAIL' and k not in cases), key=_key):
        if k in expected:
            print('   expected collateral  %s — %s' % (k, expected[k]))
        else:
            print('   UNEXPECTED collateral  %s failed under %s — investigate before '
                  'trusting this run' % (k, mid))
            ok = False

    # Aggregate cross-check against the suite's OWN count. The per-label scan above
    # cannot see a FAIL in a duplicate label (1b x4, 3b x4, 2b x3, 4 x3 ...), so a
    # mutation with side effects outside its target cases could otherwise look clean.
    # Expected failures = the target cases + this mutation's declared collateral.
    if mut_tally is None:
        print('   NO RESULT LINE under %s — the mutated run did not finish; treat as '
              'untrusted rather than as a catch' % mid)
        return False
    want_failed = len(cases) + len(expected)
    if mut_tally[1] != want_failed:
        print('   TALLY MISMATCH under %s — suite reports %d failed, expected %d '
              '(%d target + %d declared collateral). Some flipped case is invisible to '
              'the per-label scan; investigate before trusting this run.'
              % (mid, mut_tally[1], want_failed, len(cases), len(expected)))
        ok = False
    else:
        print('   tally OK  %d passed, %d failed (baseline passed %d)'
              % (mut_tally[0], mut_tally[1], base_passed))
    return ok


if __name__ == '__main__':
    sys.exit(main())

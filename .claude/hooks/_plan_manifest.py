#!/usr/bin/env python3
"""Standalone plan-manifest writer — macOS-portable replacement for the shell
`flock` subshell that track-plan-file.sh used to run.

Records which plan files each session writes, into ~/.claude/plans/.manifest.json,
under an fcntl.flock lock (portable on macOS CPython, unlike the `flock` *command*
which is absent on macOS) with an atomic tempfile + os.replace write.

Usage:
    python3 _plan_manifest.py add <session_id> <file_path>
    python3 _plan_manifest.py remove <session_id> <file_path>
    python3 _plan_manifest.py --self-test

Manifest shape: {session_id: [file_path, ...]}  (sorted, de-duplicated) — exactly
what find-session-plan.sh consumes via `jq '.[$sid] // [] | .[]'`.

Pure stdlib; no Claude/harness imports (portable per skill-location.md). The
locking + atomic-write pattern mirrors the in-harness precedents
taskmanagement.py and _claim_ledger.py.
"""
import fcntl
import json
import os
import sys
import tempfile


def _manifest_path():
    """Resolve the manifest path. Honors $HOME (so the hermetic test's FAKE_HOME
    works) and an optional PLAN_MANIFEST_PATH override (self-test / callers)."""
    override = os.environ.get("PLAN_MANIFEST_PATH")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".claude", "plans", ".manifest.json")


def _read_manifest(path):
    """Return the manifest dict, or {} if missing / unreadable / malformed.
    Mirrors the prior shell behavior `cat "$MANIFEST" 2>/dev/null || echo '{}'`;
    the manifest is a rebuildable cache, so a corrupt file resets rather than fails."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path, data):
    """Write JSON atomically: temp file in the same dir + os.replace (same-fs rename)."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add(session_id, file_path, manifest_path=None):
    """Add file_path to session_id's list under an exclusive lock. Returns the
    updated manifest dict. No-op (returns {}) on empty inputs."""
    if not session_id or not file_path:
        return {}
    path = manifest_path or _manifest_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            manifest = _read_manifest(path)
            existing = manifest.get(session_id, [])
            if not isinstance(existing, list):
                existing = []
            manifest[session_id] = sorted(set(existing) | {file_path})
            _atomic_write(path, manifest)
            return manifest
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def remove(session_id, file_path, manifest_path=None):
    """Remove file_path from session_id's list under an exclusive lock (the inverse
    of add). If the session's list becomes empty, drop the session key entirely.
    Returns the updated manifest dict. No-op (returns the current manifest) when the
    session or path is absent. Used by relocate_plan_after_approval to UNBIND an
    approved+relocated harness plan so find-session-plan.sh stops resolving it."""
    if not session_id or not file_path:
        return _read_manifest(manifest_path or _manifest_path())
    path = manifest_path or _manifest_path()
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            manifest = _read_manifest(path)
            existing = manifest.get(session_id)
            if isinstance(existing, list) and file_path in existing:
                remaining = [p for p in existing if p != file_path]
                if remaining:
                    manifest[session_id] = remaining
                else:
                    del manifest[session_id]
                _atomic_write(path, manifest)
            return manifest
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _self_test():
    import shutil
    d = tempfile.mkdtemp(prefix="plan-manifest-selftest-")
    try:
        mp = os.path.join(d, "plans", ".manifest.json")
        # 1. create-on-missing + first add
        m = add("sidA", "/p/a_PLAN.md", mp)
        assert m == {"sidA": ["/p/a_PLAN.md"]}, m
        assert os.path.isfile(mp)
        # 2. dedup — re-adding the same path is idempotent
        m = add("sidA", "/p/a_PLAN.md", mp)
        assert m == {"sidA": ["/p/a_PLAN.md"]}, m
        # 3. a second path for the same session, kept sorted
        m = add("sidA", "/p/b_PLAN.md", mp)
        assert m["sidA"] == ["/p/a_PLAN.md", "/p/b_PLAN.md"], m
        # 4. a second session is independent
        m = add("sidB", "/p/c_PLAN.md", mp)
        assert m["sidB"] == ["/p/c_PLAN.md"], m
        assert m["sidA"] == ["/p/a_PLAN.md", "/p/b_PLAN.md"], m
        # 5. on-disk shape matches the consumer's expectation {sid: [paths]}
        with open(mp) as f:
            disk = json.load(f)
        assert disk == m, (disk, m)
        # 6. a corrupt manifest resets to {} rather than raising
        with open(mp, "w") as f:
            f.write("{ not json")
        m = add("sidC", "/p/d_PLAN.md", mp)
        assert m == {"sidC": ["/p/d_PLAN.md"]}, m
        # 7. remove one of two paths — session key survives with the remainder
        add("sidD", "/p/e_PLAN.md", mp)
        add("sidD", "/p/f_PLAN.md", mp)
        m = remove("sidD", "/p/e_PLAN.md", mp)
        assert m["sidD"] == ["/p/f_PLAN.md"], m
        # 8. remove the last path — session key is dropped entirely (unbind)
        m = remove("sidD", "/p/f_PLAN.md", mp)
        assert "sidD" not in m, m
        # 9. remove a path not present — no-op, does not raise
        m = remove("sidC", "/p/nonexistent_PLAN.md", mp)
        assert m["sidC"] == ["/p/d_PLAN.md"], m
        # 10. remove from an unknown session — no-op
        m = remove("sidZ", "/p/x_PLAN.md", mp)
        assert "sidZ" not in m, m
        print("PASS  _plan_manifest self-test (10 cases)")
        return 0
    except AssertionError as e:
        print("FAIL  _plan_manifest self-test: %s" % (e,))
        return 1
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(argv):
    if len(argv) >= 1 and argv[0] == "--self-test":
        return _self_test()
    if len(argv) == 3 and argv[0] == "add":
        add(argv[1], argv[2])
        return 0
    if len(argv) == 3 and argv[0] == "remove":
        remove(argv[1], argv[2])
        return 0
    sys.stderr.write(
        "usage: _plan_manifest.py add <session_id> <file_path>\n"
        "       _plan_manifest.py remove <session_id> <file_path>\n"
        "       _plan_manifest.py --self-test\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

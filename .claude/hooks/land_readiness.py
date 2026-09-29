#!/usr/bin/env python3
"""land_readiness — the A13 advisory land-readiness sidecar (S7 git-working-model).

A per-topic ADVISORY record of whether a topic is still `in-progress` or
`ready-to-verify-then-land`, written on handoff/close and read on resume. It is a
ROUTING HINT ONLY — never a safety gate. The A6 verify-then-land gate
(`land_port.py verify-then-land`) re-computes green/red git-natively at land time,
so a stale or wrong readiness hint can NEVER cause an unsafe land (DESIGN A13/B3).

Shape mirrors `land_port._read_session_scope` (one value per non-comment line).

Design guard rails (S7 plan A1, incl. the QA-Lead review patches):
  * Advisory-only         — the two states are the exact pair A13 names; the reader
                            defaults to `in-progress` on anything it can't parse.
  * Persists              — records live under a stable state dir (NOT a session
                            scratch that gets swept to `_processed`), so a resume in a
                            LATER session still sees what the handoff wrote.
  * Path-safety           — the record filename derives from a SANITIZED key
                            (`[a-z0-9-]`, the worktree-helper `slugify` grammar) and
                            the resolved path is CONFINED to the sidecar dir, so a
                            malformed/hostile slug can never traverse out.
  * Fail-open, both ways  — a missing/garbled/unreadable record reads as
                            `in-progress` (never blocks a resume); a WRITE error
                            (read-only fs / EACCES) degrades to an advisory no-op and
                            returns False (never crashes the caller).

CLI (for the shell surfaces + the handoff worker):
  land_readiness.py read  <topic>            -> prints the state (always exits 0)
  land_readiness.py write <topic> <state>    -> exit 0 on write, 2 on advisory no-op
  land_readiness.py path  <topic>            -> prints the resolved record path

The sidecar dir is `${LAND_READINESS_DIR:-$CLAUDE_CONFIG_DIR/state/land_readiness}`
(falling back to ~/.claude/state/land_readiness). Tests override LAND_READINESS_DIR
to a tempdir so they never touch live state.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional

# The two advisory states A13 names — nothing else is valid to WRITE.
IN_PROGRESS = "in-progress"
READY = "ready-to-verify-then-land"
VALID_STATES = (IN_PROGRESS, READY)

# Fail-open default: anything unparseable/missing reads as still-in-progress, so a
# resume never routes an un-ready topic to the land gate on a bad hint.
DEFAULT_STATE = IN_PROGRESS

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


def _default_dir() -> Path:
    """The sidecar dir. Env override first (tests + explicit callers), else the
    config-dir state tree (persists across sessions; not session scratch)."""
    env = os.environ.get("LAND_READINESS_DIR")
    if env:
        return Path(env)
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(cfg) if cfg else (Path.home() / ".claude")
    return base / "state" / "land_readiness"


def sanitize_slug(topic: str) -> str:
    """Reduce an arbitrary topic string to the `[a-z0-9-]` slug grammar
    (lowercase; runs of other chars -> single '-'; strip edge '-'). Raises
    ValueError if nothing usable survives — an empty key must never resolve to the
    bare sidecar dir. A sanitized slug contains no '/', '.', or '..', so it cannot
    encode a path traversal."""
    slug = _SLUG_STRIP_RE.sub("-", (topic or "").lower()).strip("-")
    if not slug:
        raise ValueError(f"topic {topic!r} sanitizes to an empty slug — refusing")
    return slug


def record_path(topic: str, base_dir: Optional[os.PathLike] = None) -> Path:
    """Resolve the record path for a topic, CONFINED to the sidecar dir. Defence in
    depth: the slug is already traversal-free, and we additionally assert the
    resolved path's parent is the sidecar dir itself (rejects any residual escape)."""
    base = Path(base_dir) if base_dir is not None else _default_dir()
    slug = sanitize_slug(topic)
    p = (base / f"_land_readiness-{slug}.md")
    # Confinement check against a resolved base (do not require the file to exist).
    base_res = base.resolve()
    p_res = (base_res / p.name)
    if p_res.parent != base_res:
        raise ValueError(f"resolved record path {p_res} escapes sidecar dir {base_res}")
    return p_res


def read_readiness(topic: str, base_dir: Optional[os.PathLike] = None) -> str:
    """Return the topic's advisory state. Fail-open: any error, missing file, or
    unrecognized content -> DEFAULT_STATE (`in-progress`). Never raises."""
    try:
        p = record_path(topic, base_dir)
    except ValueError:
        return DEFAULT_STATE
    try:
        if not p.is_file():
            return DEFAULT_STATE
        for ln in p.read_text(encoding="utf-8").splitlines():
            s = ln.strip()
            if s and not s.startswith("#"):
                return s if s in VALID_STATES else DEFAULT_STATE
        return DEFAULT_STATE
    except (OSError, UnicodeError):
        return DEFAULT_STATE


def write_readiness(topic: str, state: str, base_dir: Optional[os.PathLike] = None) -> bool:
    """Write the advisory record atomically. Returns True on success, False on an
    advisory no-op (invalid state, un-keyable topic, or a WRITE error such as a
    read-only fs / EACCES). NEVER raises — a broken write must not crash a /close or
    a handoff; the record is only a routing hint."""
    if state not in VALID_STATES:
        return False
    try:
        p = record_path(topic, base_dir)
    except ValueError:
        return False
    body = (
        "# advisory land-readiness record (S7 A13) — routing hint only, NOT a "
        "safety gate;\n# the A6 verify-then-land gate re-computes green/red at "
        "land time.\n"
        f"{state}\n"
    )
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Same-dir tempfile + atomic replace (never a partial record on a crash).
        fd, tmp = tempfile.mkstemp(prefix=".ldr-", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return True
    except OSError:
        # read-only fs / EACCES / no space — advisory no-op, never crash the caller.
        return False


def clear_readiness(topic: str, base_dir: Optional[os.PathLike] = None) -> bool:
    """Remove a topic's record (advisory; used when a topic retires). Fail-open."""
    try:
        p = record_path(topic, base_dir)
    except ValueError:
        return False
    try:
        p.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _cli(argv: Optional[list] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    if cmd == "read" and len(args) == 2:
        print(read_readiness(args[1]))
        return 0
    if cmd == "path" and len(args) == 2:
        try:
            print(record_path(args[1]))
            return 0
        except ValueError as e:
            print(f"refused: {e}", file=sys.stderr)
            return 2
    if cmd == "write" and len(args) == 3:
        ok = write_readiness(args[1], args[2])
        if ok:
            print(f"land-readiness[{args[1]}] = {args[2]}")
            return 0
        print(f"advisory no-op: could not write land-readiness for {args[1]!r} "
              f"(invalid state or write error) — hint skipped, safe to continue",
              file=sys.stderr)
        return 2
    print("usage: land_readiness.py {read <topic> | write <topic> <state> | "
          "path <topic>}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_cli())

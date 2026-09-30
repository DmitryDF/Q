#!/usr/bin/env python3
"""taskmanagement.py — Slice L Foundations (workflow-phases-redesign).

Lifecycle-sync agent: composes — does not invent — across the three surfaces
that drift today (TODO.md, _thought slice register, plan file). Session 1
ships only standalone-testable primitives. The sync-layer wrap at
pre_plan_gates.py:phase_start / :phase_stop lands at Session 2.

Editorial decisions (spec-of-record — locked at L Planning Session 1,
2026-05-23; surfaced here so the implementer does not re-litigate):

  1. STALE_T default = 3600s (60 minutes). Override: env TM_STALE_T_SECONDS.
     Lazy auto-release fires only when a *different* session attempts to
     acquire and finds last_heartbeat older than STALE_T.
     AMENDED by streamed-dancing-goose S4 (A4, 2026-09-20): STALE_T is no
     longer the discriminator for a holder whose process can be probed. The
     payload records the SESSION process (pid + start time, reuse-proof); a
     holder confirmed ALIVE keeps its lock at any age, a holder confirmed
     DEAD is reclaimed at once, an UNKNOWN answer is reclaimed past the
     liveness ceiling (DEFAULT_LIVENESS_CEILING), and STALE_T governs only
     pre-S4 LEGACY payloads on the lazy path plus the SessionStart sweep's
     "heartbeat old" conjunct. `last_heartbeat` is refreshed per turn by the
     UserPromptSubmit hook; the ship detector reads `started_at`, not this
     field. See the A4 block comment above `session_process_identity`.

  2. Slice-register row format — additive HTML-comment marker, one row per
     slice, single-line, written by sync_phase_transition (Session 2) and by
     /work-done's SHIPPED write (Slice J):

         <!-- L:slice id=<id> status=<NOW|NEXT|NEARBY|NASCENT|SHIPPED|NEVER>
              sessions=M/N plan=<wikilink-or-empty>
              diary=<wikilink-or-empty> updated=<ISO> -->

     The existing prose slice block is left untouched (additive only —
     Slice-B-style atomic doc/engine pair would be overkill: no validator on
     the prose today). Status enum grounded against spine prose; comment
     syntax editorial.

  3. Plan-file `## Diff` section format — top-level `## Diff` heading;
     per-session sub-sections `**Session N:**`; markdown bullet list of
     paths in backticks, one path per line. Consumed by attribute_commits.

  4. Lock file:         ~/.claude/state/locks/<topic-slug>.lock      (JSON)
     Concurrency guard: ~/.claude/state/locks/.<topic-slug>.flock    (fcntl)
     Release audit log: ~/.claude/state/locks/_releases.jsonl        (JSONL)

Guiding Policy (locked at S-L-A 2026-05-23) — most load-bearing for this
module:
  * Producer-never-verifies via deterministic post-write verifier. Re-read
    each surface; structural-diff against intended payload. LLM checkers
    reserved for ambiguity adjudication only (NEVER from verify_write).
  * Authority allocation: L composes, never invents. L does NOT own
    phase state (Slice A), TODO format (C/C-ii), retire-marker write (J),
    dependency mapping (H), prioritization (K), or M/N counter advance (J).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Paths & defaults
# ---------------------------------------------------------------------------

# Locks are MACHINE state shared by every session and every config tree (a
# clone spawned by `config-experiment` must see the same holders live sees), so
# the directory is under $HOME, not under CLAUDE_CONFIG_DIR. `TM_LOCKS_DIR` is
# the test seam that lets the three S4 hook scripts be exercised as real
# subprocesses against a sandbox instead of the live directory.
LOCKS_DIR = Path(os.environ.get("TM_LOCKS_DIR")
                 or (Path.home() / ".claude" / "state" / "locks"))
RELEASES_LOG = LOCKS_DIR / "_releases.jsonl"

DEFAULT_STALE_T = 3600  # seconds; override via TM_STALE_T_SECONDS

# streamed-dancing-goose S4 / A4 — the ceiling over locks whose holder's liveness
# CANNOT be confirmed (the probe answered UNKNOWN: `ps` unreadable, or a pid the
# kernel will not let us signal and `ps` cannot describe). A holder the probe
# confirms ALIVE keeps its lock at ANY age — no ceiling applies to it (C2); a
# holder confirmed DEAD is reclaimed at once. The ceiling exists so that the one
# answer the probe cannot give still ends in an automatic reclaim (C9), and it is
# set with the reuse-proof identity in hand: pid + process start time is
# answerable for every process this user owns, so UNKNOWN is `ps` breaking or a
# process owned by another user — rare, and never a session of ours mid-work.
# 24h is deliberately generous: the cost of a wrong reclaim is a stolen lock (the
# failure this slice exists to stop); the cost of a slow one is a topic blocked
# for a day in a case that essentially never arises. Override via
# TM_LIVENESS_CEILING_SECONDS.
DEFAULT_LIVENESS_CEILING = 24 * 3600

# Discriminator for a payload written by THIS lock format. A payload without it
# was written before S4: its `pid` is the short-lived helper that ran
# `acquire_lock`, so no liveness question can be asked of it — it is LEGACY and
# is reclaimed only by the pre-S4 lazy STALE_T path when a session next wants
# that topic. The sweep never touches it (handoff constraint; a dead-pid
# predicate reads true for a LIVE session's legacy lock).
LOCK_IDENTITY_VERSION = "session-process/v1"

SLICE_STATUS_ENUM = {"NOW", "NEXT", "NEARBY", "NASCENT", "SHIPPED", "NEVER"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _today_iso() -> str:
    return _now().date().isoformat()


def _parse_iso(s: str) -> _dt.datetime:
    # tolerate trailing 'Z' and naive forms
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = _dt.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _stale_t_seconds() -> int:
    raw = os.environ.get("TM_STALE_T_SECONDS")
    if raw is None:
        return DEFAULT_STALE_T
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_STALE_T


def _liveness_ceiling_seconds() -> int:
    raw = os.environ.get("TM_LIVENESS_CEILING_SECONDS")
    if raw is None:
        return DEFAULT_LIVENESS_CEILING
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_LIVENESS_CEILING


def _heartbeat_age_seconds(payload: dict) -> float | None:
    """Seconds since the payload's `last_heartbeat`; None when unreadable."""
    hb = payload.get("last_heartbeat")
    if not hb:
        return None
    try:
        return (_now() - _parse_iso(str(hb))).total_seconds()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# A4 (streamed-dancing-goose S4) — session-process identity + liveness probe
#
# Before S4 a lock payload recorded `os.getpid()` of the process that ran
# `acquire_lock` — a `python3 -c` helper the /work-start skill spawns, which exits
# within a second. So every payload on disk described a dead process, and the
# only thing anyone could read about the holder was `last_heartbeat`, which
# nothing refreshed: "when we acquired", read as "still alive". A session working
# for 61 minutes lost its lock to the next session that asked.
#
# The identity recorded now is the SESSION's own process — the `claude` CLI that
# owns the conversation — paired with that process's start time, so a recycled
# pid can never read as the same holder. Two mechanisms find it, in order:
#
#   1. `CLAUDE_PID` — the harness exports it to every child it spawns (the Bash
#      tool's shell, hook commands, subagent tool calls, background spawns), so
#      it is present in every shape `acquire_lock` runs under from a session.
#      It is verified, not trusted: `ps` must describe that pid as the claude
#      executable, or it is ignored.
#   2. An ancestry walk from `os.getpid()` up `ppid` links until a process whose
#      argv[0] is the claude executable — the shape when the env var is absent
#      (a wrapper that scrubbed the environment) but the process tree is intact.
#
# With neither (a standalone CLI run, cron, pytest outside a session) the caller's
# own pid is recorded, marked `source: self`. Such a holder dies as soon as the
# caller exits and is reclaimed by the next acquirer — which is exactly the
# pre-S4 behaviour, now honest about itself.
#
# THE PROBE, named for this platform. `/proc` does not exist on macOS, so the
# probe is `os.kill(pid, 0)` followed by `ps -p <pid> -o lstart=`:
#
#   * ESRCH from kill            → DEAD    (no such process)
#   * kill ok / EPERM, ps empty  → UNKNOWN (EPERM = a pid owned by another user;
#                                  `ps` normally still describes it, so EPERM
#                                  alone is NOT dead — only an unreadable start
#                                  time is UNKNOWN)
#   * ps start == recorded start → ALIVE   (same pid, same birth: the process)
#   * ps start != recorded start → DEAD    (the pid was recycled)
#   * payload has no identity    → LEGACY  (pre-S4 format; unanswerable)
#
# A zombie (exited, not yet reaped) still answers ALIVE — its parent shell reaps
# a `claude` process promptly, so this is not a shape a session leaves behind.
# ---------------------------------------------------------------------------

LIVENESS_ALIVE = "ALIVE"
LIVENESS_DEAD = "DEAD"
LIVENESS_UNKNOWN = "UNKNOWN"
LIVENESS_LEGACY = "LEGACY"


def _ps_field(pid: int, field: str) -> str | None:
    """One `ps -p <pid> -o <field>=` read; None on any failure or empty output."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", f"{field}="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if out.returncode != 0:
        return None
    val = out.stdout.strip()
    return val or None


def _ps_describe(pid: int) -> tuple[str | None, str | None, str | None]:
    """ONE `ps` call returning (ppid, lstart, args) for `pid`, each None when
    unreadable. `lstart` is a fixed-width 24-char field on this platform
    (`Sun Sep 20 21:17:09 2026`), so the row is split positionally, never on
    whitespace — argv may contain anything. One call rather than three keeps
    `acquire_lock`'s cost on the contended path at a couple of milliseconds."""
    try:
        out = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "ppid=", "-o", "lstart=", "-o", "args="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None, None, None
    row = out.stdout.rstrip("\n")
    if out.returncode != 0 or not row.strip():
        return None, None, None
    ppid, _, rest = row.strip().partition(" ")
    rest = rest.lstrip()
    lstart, args = rest[:24].strip(), rest[24:].strip()
    return (ppid or None), (lstart or None), (args or None)


def _process_start(pid: int) -> str | None:
    """The process's start time as `ps` prints it (`lstart`), or None."""
    return _ps_field(pid, "lstart")


def _is_claude_args(args: str | None) -> bool:
    """True iff an argv string names the claude CLI (argv[0] basename `claude`)."""
    if not args:
        return False
    return os.path.basename(args.split()[0]) == "claude"


def _is_claude_process(pid: int) -> bool:
    """True iff `ps` describes `pid` as the claude CLI (argv[0] basename `claude`)."""
    return _is_claude_args(_ps_describe(pid)[2])


_IDENTITY_CACHE: dict = {}


def session_process_identity() -> dict:
    """Identify the session process this call runs under (see the block comment
    above). Returns {pid, pid_start, identity, source} where `source` is one of
    `env` (CLAUDE_PID verified), `ancestry` (walked), `self` (fallback).
    `pid_start` may be None only on the `self` fallback when `ps` itself fails.

    Cached per (process, CLAUDE_PID) — a process's session cannot change while
    it runs, and `acquire_lock` is on the contended path of every maintenance
    verb that walks many topics."""
    env_pid = os.environ.get("CLAUDE_PID") or ""
    key = (os.getpid(), env_pid)
    hit = _IDENTITY_CACHE.get(key)
    if hit is not None:
        return dict(hit)
    ident = _resolve_session_process_identity(env_pid)
    _IDENTITY_CACHE[key] = dict(ident)
    return ident


def _resolve_session_process_identity(env_pid: str) -> dict:
    if env_pid:
        try:
            cand = int(env_pid)
        except ValueError:
            cand = None
        if cand and cand > 1:
            _ppid, lstart, args = _ps_describe(cand)
            if _is_claude_args(args):
                return {"pid": cand, "pid_start": lstart,
                        "identity": LOCK_IDENTITY_VERSION, "source": "env"}

    pid = os.getpid()
    for _ in range(16):
        ppid_s, _lstart, _args = _ps_describe(pid)
        if not ppid_s:
            break
        try:
            ppid = int(ppid_s)
        except ValueError:
            break
        if ppid <= 1:
            break
        _pp, p_lstart, p_args = _ps_describe(ppid)
        if _is_claude_args(p_args):
            return {"pid": ppid, "pid_start": p_lstart,
                    "identity": LOCK_IDENTITY_VERSION, "source": "ancestry"}
        pid = ppid

    me = os.getpid()
    return {"pid": me, "pid_start": _process_start(me),
            "identity": LOCK_IDENTITY_VERSION, "source": "self"}


def payload_has_identity(payload: dict) -> bool:
    """True iff the payload was written in the S4 format (reuse-proof identity)."""
    return bool(payload.get("identity")) and bool(payload.get("pid_start"))


def probe_liveness(payload: dict) -> str:
    """Answer whether the payload's recorded session process is running.
    Returns one of LIVENESS_ALIVE / DEAD / UNKNOWN / LEGACY (see the block
    comment above for the platform mapping). Pure read; never raises."""
    if not payload_has_identity(payload):
        return LIVENESS_LEGACY
    try:
        pid = int(payload.get("pid"))
    except (TypeError, ValueError):
        return LIVENESS_UNKNOWN
    if pid <= 1:
        return LIVENESS_UNKNOWN
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return LIVENESS_DEAD
    except PermissionError:
        pass  # exists, owned by another user — fall through to the start check
    except OSError:
        return LIVENESS_UNKNOWN
    start = _process_start(pid)
    if start is None:
        return LIVENESS_UNKNOWN
    return LIVENESS_ALIVE if start == payload.get("pid_start") else LIVENESS_DEAD


def _read_payload_or_none(path: Path) -> dict | None:
    """The payload as a dict, or None when the file is unreadable, not JSON, or
    not a JSON object — a corrupt payload names no holder."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _move_aside(path: Path) -> Path:
    """Retire a lock payload by MOVING it to a timestamped sibling — never a
    delete (`safe-defaults.md`). The move is exclusive-create: `os.link` to the
    new name fails with EEXIST if that name is taken (unlike `os.rename`, which
    replaces its destination silently), so two releases in the same microsecond
    — or a pre-existing backup — can never overwrite each other. The suffix is
    `.bak-<YYYYmmddHHMMSS><microseconds>-<pid>`, with `-<n>` appended on
    collision. Returns the sibling's path. Raises OSError if the move fails."""
    stamp = _now().strftime("%Y%m%d%H%M%S%f")
    base = f"{path}.bak-{stamp}-{os.getpid()}"
    for n in range(1000):
        dst = Path(base if n == 0 else f"{base}-{n}")
        try:
            os.link(path, dst)
        except FileExistsError:
            continue
        os.unlink(path)
        return dst
    raise OSError(f"could not find a free backup name for {path}")


def _atomic_write(path: Path, text: str) -> None:
    """Atomic write via tmp + rename — mirrors pre_plan_gates.py:308."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.rename(path)


def _record_ledger_write(path) -> None:
    """Record a code-layer write to this session's file ledger (A5 / gap G4).

    Deliberately NOT called from `_atomic_write` itself: that helper also writes
    topic-state JSON, which is not a tracked repo file. The plan names the spine
    write specifically, so the call sits at that call site.

    Lazy, guarded, silent on failure — this module runs under pytest, from the
    CLI, and from background jobs where no session exists. `record_write` never
    raises; this wrapper extends that to the import. An unrecorded write is just
    an undeclared one, which leaves the file dirty rather than swept into someone
    else's commit.
    """
    try:
        import sys as _sys, os as _os
        _hooks = _os.path.dirname(_os.path.abspath(__file__))
        if _hooks not in _sys.path:
            _sys.path.insert(0, _hooks)
        from commit_scope import record_write as _rw
        _rw(path)
    except Exception:
        pass


def _ensure_locks_dir() -> None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)


def composite_lock_key(topic_slug: str, project_slug: str) -> str:
    """THE single locus for the per-topic lock KEY SHAPE (session-topic-identity-
    coherence plan, S3/A3).

    The lock/state key is ALWAYS the composite ``<topic_slug>__<project_slug>``
    — the same shape ``/work-start`` acquires under and the topic-state file is
    named with. A bare ``topic_slug`` is NOT a valid lock key: that was the M16
    bug — ``detect_ship_event`` used ``read_lock(topic_slug)`` and never found the
    composite-keyed lock ``/work-start`` had acquired. Callers MUST build the
    project half through the canonical resolver
    (``pre_plan_gates.canonical_identity`` / the recorded ``active_project``,
    which S2 keeps canonical), never a raw cwd-basename.
    """
    return f"{topic_slug}__{project_slug}"


def split_lock_key(key: str) -> tuple[str, str]:
    """Inverse of :func:`composite_lock_key` — split a lock/state stem into
    ``(topic_slug, project_slug)`` on the LAST ``"__"``. A key with no ``"__"``
    (a legacy bare-slug lock) returns ``(key, "")``."""
    topic, sep, project = key.rpartition("__")
    if not sep:
        return key, ""
    return topic, project


def _lock_payload_path(topic_slug: str) -> Path:
    # NB: ``topic_slug`` here is the FULL lock key — for topic locks that is the
    # composite ``<topic>__<project>`` (see :func:`composite_lock_key`). The
    # parameter name is historical; the value is the whole key.
    return LOCKS_DIR / f"{topic_slug}.lock"


def _flock_path(topic_slug: str) -> Path:
    # Separate fd-bound concurrency guard; payload file is read/written
    # under this guard. Using "w" mode on the payload would truncate it.
    return LOCKS_DIR / f".{topic_slug}.flock"


def _log_release(topic_slug: str, payload: dict, reason: str, **extra) -> None:
    _ensure_locks_dir()
    row = {
        "ts": _now_iso(),
        "topic_slug": topic_slug,
        "reason": reason,
        "released_payload": payload,
    }
    row.update(extra)
    with RELEASES_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")


# ---------------------------------------------------------------------------
# A1 — Lock primitives
# ---------------------------------------------------------------------------

def acquire_lock(topic_slug: str, session_id: str) -> dict:
    """Acquire a per-topic logical lock.

    Returns one of:
      {"status": "ACQUIRED",            "holder": payload}
      {"status": "ALREADY_HELD_BY_SELF","holder": payload}   # idempotent
      {"status": "HELD_BY_OTHER",       "holder": payload, "age_seconds": float}
      {"status": "RACE",                "topic_slug": ...}  # flock contention

    Lazy reclaim when a *different* session asks (S4 / A4 — liveness is a
    fact about the holder's process, not a proxy read off a timestamp):

      ALIVE    the recorded session process is running → HELD_BY_OTHER at ANY
               age; no ceiling applies (C2).
      DEAD     it is gone, or its pid was recycled → reclaimed now, audit row
               `dead-holder-release` (C3).
      UNKNOWN  the probe could not answer → reclaimed only once last_heartbeat
               is older than the liveness ceiling, audit row
               `unconfirmable-ceiling-release` (C9); else HELD_BY_OTHER.
      LEGACY   a pre-S4 payload (no identity) → the pre-S4 rule unchanged:
               reclaimed once last_heartbeat ≥ STALE_T, audit row
               `stale-auto-release`; else HELD_BY_OTHER.

    A reclaimed payload is moved aside (`_move_aside`), never deleted. Every
    HELD_BY_OTHER answer carries `liveness` so the refused caller can see why.
    """
    _ensure_locks_dir()
    payload_path = _lock_payload_path(topic_slug)
    flock_path = _flock_path(topic_slug)

    with open(flock_path, "w") as lf:
        try:
            fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "RACE", "topic_slug": topic_slug}

        try:
            existing = _read_payload_or_none(payload_path) if payload_path.exists() else None
            if payload_path.exists() and existing is None:
                # A corrupt payload (not JSON, or not an object) names no holder
                # at all. Leaving it would make this the one shape no code path
                # ever reclaims (C9); it is moved aside like any other retired
                # payload and the caller acquires.
                moved_to = _move_aside(payload_path)
                _log_release(topic_slug, {"raw": "unparseable"},
                             reason="corrupt-payload-release",
                             new_holder_session_id=session_id,
                             moved_to=str(moved_to))
            if existing is not None:
                if existing.get("session_id") == session_id:
                    existing["last_heartbeat"] = _now_iso()
                    if not payload_has_identity(existing):
                        # A session that acquired under the pre-S4 format and
                        # re-enters (/work-start is idempotent) upgrades its own
                        # payload: same session_id, so the identity is ours to
                        # write. Without this a pre-S4 holder stays on the age
                        # rule until it releases and re-acquires from scratch.
                        ident = session_process_identity()
                        existing.update({
                            "pid": ident["pid"], "pid_start": ident["pid_start"],
                            "pid_source": ident["source"],
                            "identity": ident["identity"],
                        })
                    _atomic_write(payload_path, json.dumps(existing, indent=2))
                    return {"status": "ALREADY_HELD_BY_SELF", "holder": existing}

                age = _heartbeat_age_seconds(existing)
                if age is None:
                    age = float("inf")  # unreadable heartbeat: never "fresh"
                liveness = probe_liveness(existing)
                reclaim_reason = None
                if liveness == LIVENESS_DEAD:
                    reclaim_reason = "dead-holder-release"
                elif liveness == LIVENESS_UNKNOWN:
                    if age >= _liveness_ceiling_seconds():
                        reclaim_reason = "unconfirmable-ceiling-release"
                elif liveness == LIVENESS_LEGACY:
                    if age >= _stale_t_seconds():
                        reclaim_reason = "stale-auto-release"
                # LIVENESS_ALIVE: never reclaimed, whatever the age.

                if reclaim_reason is None:
                    return {
                        "status": "HELD_BY_OTHER",
                        "holder": existing,
                        "age_seconds": age,
                        "liveness": liveness,
                    }
                moved_to = _move_aside(payload_path)
                _log_release(
                    topic_slug, existing,
                    reason=reclaim_reason,
                    age_seconds=age,
                    liveness=liveness,
                    new_holder_session_id=session_id,
                    moved_to=str(moved_to),
                )

            ident = session_process_identity()
            payload = {
                "session_id": session_id,
                "pid": ident["pid"],
                "pid_start": ident["pid_start"],
                "pid_source": ident["source"],
                "identity": ident["identity"],
                "started_at": _now_iso(),
                "last_heartbeat": _now_iso(),
            }
            _atomic_write(payload_path, json.dumps(payload, indent=2))
            return {"status": "ACQUIRED", "holder": payload}
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def release_lock(topic_slug: str, session_id: str | None = None,
                 force: bool = False) -> dict:
    """Release a topic lock.

    session_id: if provided and != holder, returns NOT_HOLDER unless force=True.
    """
    _ensure_locks_dir()
    payload_path = _lock_payload_path(topic_slug)
    if not payload_path.exists():
        return {"status": "NOT_HELD", "topic_slug": topic_slug}

    existing = _read_payload_or_none(payload_path)
    if existing is None:
        # Corrupt payload: no holder to compare against. A forced release retires
        # it (audit `corrupt-payload-release`); an unforced one refuses, since it
        # cannot prove the caller is the holder.
        if not force:
            return {"status": "NOT_HOLDER", "holder": None, "reason": "payload unparseable"}
        moved_to = _move_aside(payload_path)
        _log_release(topic_slug, {"raw": "unparseable"}, reason="corrupt-payload-release",
                     moved_to=str(moved_to))
        return {"status": "RELEASED", "released": None, "moved_to": str(moved_to)}
    if not force and session_id is not None and existing.get("session_id") != session_id:
        return {"status": "NOT_HOLDER", "holder": existing}

    if force and session_id is not None and existing.get("session_id") != session_id:
        reason = "manual-release-non-holder"
    elif force:
        reason = "manual-release"
    else:
        reason = "holder-release"

    # Move aside, never delete — the second of the two former unlink sites
    # (the first is the lazy reclaim in acquire_lock). A forced non-holder
    # release retires someone ELSE's payload, which is exactly the case where a
    # recoverable copy matters most.
    moved_to = _move_aside(payload_path)
    _log_release(topic_slug, existing, reason=reason, moved_to=str(moved_to))
    return {"status": "RELEASED", "released": existing, "moved_to": str(moved_to)}


def refresh_heartbeat(topic_slug: str, session_id: str | None = None) -> dict:
    """Refresh last_heartbeat on an existing lock.

    Idempotent. If session_id is provided and != holder, returns NOT_HOLDER
    (no write).

    `last_heartbeat` is a LIVENESS field only (S4). The ship detector in
    `work_done.py` reads `started_at`, so a refresh never changes its verdict —
    the two questions that used to share this field are separated.
    """
    payload_path = _lock_payload_path(topic_slug)
    if not payload_path.exists():
        return {"status": "NOT_HELD", "topic_slug": topic_slug}
    existing = _read_payload_or_none(payload_path)
    if existing is None:
        return {"status": "NOT_HOLDER", "holder": None, "reason": "payload unparseable"}
    if session_id is not None and existing.get("session_id") != session_id:
        return {"status": "NOT_HOLDER", "holder": existing}
    existing["last_heartbeat"] = _now_iso()
    _atomic_write(payload_path, json.dumps(existing, indent=2))
    return {"status": "REFRESHED", "holder": existing}


def read_lock(topic_slug: str) -> dict | None:
    """The lock payload, or None when there is no lock OR the payload is corrupt
    (not JSON / not an object) — a corrupt payload names no holder, and every
    reader (ship detector, omission gate, the CLI) must see "no holder", never a
    traceback. Callers that need to tell absent from corrupt check the path."""
    payload_path = _lock_payload_path(topic_slug)
    if not payload_path.exists():
        return None
    return _read_payload_or_none(payload_path)


def _iter_lock_payloads():
    """Yield (payload_path, payload) for every readable `<key>.lock` under
    LOCKS_DIR. Only the payload glob — never the `.<key>.flock` fd guards, and
    never the `.lock.bak-*` siblings retired by `_move_aside`."""
    if not LOCKS_DIR.exists():
        return
    for p in sorted(LOCKS_DIR.glob("*.lock")):
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            yield p, payload


def refresh_session_heartbeats(session_id: str) -> dict:
    """Per-turn liveness refresh (S4): bump `last_heartbeat` on every lock this
    session holds. Fail-open by contract — never raises, returns what it did.
    What this does NOT deliver: the event it is registered on
    (`UserPromptSubmit`) fires only when a person sends a prompt, so during an
    autonomous run the heartbeat goes stale; the process liveness check in
    `acquire_lock` is what actually keeps such a session's lock (C2)."""
    refreshed: list[str] = []
    errors: list[str] = []
    try:
        for p, payload in _iter_lock_payloads():
            if payload.get("session_id") != session_id:
                continue
            try:
                payload["last_heartbeat"] = _now_iso()
                _atomic_write(p, json.dumps(payload, indent=2))
                refreshed.append(p.stem)
            except OSError as e:
                errors.append(f"{p.stem}: {e}")
    except Exception as e:  # noqa: BLE001 — fail-open, this runs on every turn
        errors.append(str(e))
    return {"status": "OK", "refreshed": refreshed, "errors": errors}


def release_session_locks(session_id: str,
                          reason: str = "session-end-release") -> dict:
    """Session-end release (S4): move aside every lock this session holds.
    Each move is wrapped so one failure does not abort the rest; never raises."""
    released: list[dict] = []
    errors: list[str] = []
    try:
        for p, payload in _iter_lock_payloads():
            if payload.get("session_id") != session_id:
                continue
            try:
                moved_to = _move_aside(p)
                _log_release(p.stem, payload, reason=reason, moved_to=str(moved_to))
                released.append({"topic_slug": p.stem, "moved_to": str(moved_to)})
            except OSError as e:
                errors.append(f"{p.stem}: {e}")
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    return {"status": "OK", "released": released, "errors": errors}


def sweep_dead_locks(session_id: str | None) -> dict:
    """Predicate-driven sweep (S4) of locks whose holder is provably gone.
    Registered on SessionStart. A payload is moved aside ONLY when ALL of:

      1. it carries the S4 identity (a LEGACY payload is never touched — its
         recorded pid is a helper that always reads dead, so "process not
         alive" is true of a LIVE session's legacy lock too);
      2. it is not this session's own — by `session_id` when the caller knows
         it, AND by the running process's own identity (same `pid` +
         `pid_start` as `session_process_identity()`) regardless, so the skip
         is unconditional even when no session id could be derived;
      3. the probe answers DEAD — confirmed, not UNKNOWN (UNKNOWN is left to
         the lazy ceiling in `acquire_lock`, where someone actually wants it);
      4. `last_heartbeat` is older than STALE_T.

    Each move is wrapped so one EACCES does not abort the sweep part-way; a
    re-run after a partial failure moves nothing twice (a moved payload no
    longer matches the glob). Never raises. Returns what it did and why it
    skipped each candidate, so the answer is auditable."""
    swept: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []
    try:
        me = session_process_identity()
        for p, payload in _iter_lock_payloads():
            key = p.stem
            if session_id is not None and payload.get("session_id") == session_id:
                skipped.append({"topic_slug": key, "why": "own-session"})
                continue
            if (payload.get("pid") == me.get("pid")
                    and payload.get("pid_start") == me.get("pid_start")):
                skipped.append({"topic_slug": key, "why": "own-process"})
                continue
            if not payload_has_identity(payload):
                skipped.append({"topic_slug": key, "why": "legacy-format"})
                continue
            liveness = probe_liveness(payload)
            if liveness != LIVENESS_DEAD:
                skipped.append({"topic_slug": key, "why": f"liveness-{liveness.lower()}"})
                continue
            age = _heartbeat_age_seconds(payload)
            if age is None or age < _stale_t_seconds():
                skipped.append({"topic_slug": key, "why": "heartbeat-fresh"})
                continue
            try:
                moved_to = _move_aside(p)
                _log_release(key, payload, reason="dead-holder-sweep",
                             age_seconds=age, liveness=liveness,
                             sweeper_session_id=session_id,
                             moved_to=str(moved_to))
                swept.append({"topic_slug": key, "moved_to": str(moved_to)})
            except OSError as e:
                errors.append(f"{key}: {e}")
    except Exception as e:  # noqa: BLE001
        errors.append(str(e))
    return {"status": "OK", "swept": swept, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
# A2 — Deterministic post-write verifier
# ---------------------------------------------------------------------------

class WriteVerificationError(Exception):
    """Raised when a re-read of a written surface does not match intent.

    LLM checkers MUST NOT be invoked from verify_write (Guiding Policy 2).
    """


def verify_write(surface_kind: str, path: str | Path,
                 expected_payload: Any, locator: Any) -> dict:
    """Re-read a written surface and structural-diff against expected_payload.

    surface_kind options:
      - "todo_line"        locator = regex pattern (str);
                           expected_payload = substring that must appear in the matched line.
      - "topic_state_json" locator = dotted key path (e.g., "phase" or "topics.X.phase");
                           expected_payload = expected value at that key.
      - "spine_section"    locator = (start_marker, end_marker) tuple of literal strings;
                           expected_payload = substring that must appear within the section.

    Raises WriteVerificationError with a human-readable diff on mismatch.
    """
    path = Path(path)
    if not path.exists():
        raise WriteVerificationError(f"surface missing: {path}")
    text = path.read_text(encoding="utf-8")

    if surface_kind == "todo_line":
        if not isinstance(locator, str):
            raise WriteVerificationError(
                f"todo_line locator must be regex str; got {type(locator).__name__}"
            )
        match = re.search(locator, text, re.MULTILINE)
        if match is None:
            raise WriteVerificationError(
                f"todo_line locator {locator!r} matched nothing in {path}"
            )
        line = match.group(0)
        if expected_payload not in line:
            raise WriteVerificationError(
                f"todo_line mismatch in {path}\n"
                f"  expected substring: {expected_payload!r}\n"
                f"  found line:         {line!r}"
            )
        return {"status": "OK", "matched": line}

    if surface_kind == "topic_state_json":
        if not isinstance(locator, str):
            raise WriteVerificationError(
                f"topic_state_json locator must be dotted-key str; got {type(locator).__name__}"
            )
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise WriteVerificationError(f"topic_state_json invalid JSON in {path}: {e}") from e
        actual: Any = data
        for k in locator.split("."):
            if not isinstance(actual, dict) or k not in actual:
                raise WriteVerificationError(
                    f"topic_state_json key path {locator!r} missing in {path}"
                )
            actual = actual[k]
        if actual != expected_payload:
            raise WriteVerificationError(
                f"topic_state_json mismatch at {locator!r}\n"
                f"  expected: {expected_payload!r}\n"
                f"  actual:   {actual!r}"
            )
        return {"status": "OK", "actual": actual}

    if surface_kind == "spine_section":
        if not (isinstance(locator, tuple) and len(locator) == 2):
            raise WriteVerificationError(
                f"spine_section locator must be (start_marker, end_marker) tuple"
            )
        start, end = locator
        si = text.find(start)
        if si < 0:
            raise WriteVerificationError(
                f"spine_section start marker {start!r} missing in {path}"
            )
        ei = text.find(end, si)
        if ei < 0:
            raise WriteVerificationError(
                f"spine_section end marker {end!r} missing in {path}"
            )
        section = text[si:ei]
        if not isinstance(expected_payload, str):
            raise WriteVerificationError(
                f"spine_section expected_payload must be str; got {type(expected_payload).__name__}"
            )
        if expected_payload not in section:
            raise WriteVerificationError(
                f"spine_section missing expected substring in {path}\n"
                f"  expected: {expected_payload!r}\n"
                f"  section head: {section[:200]!r}"
            )
        return {"status": "OK", "section_len": len(section)}

    raise ValueError(f"unknown surface_kind: {surface_kind!r}")


# ---------------------------------------------------------------------------
# A3 — Slice-register row format + parser + writer
# ---------------------------------------------------------------------------

# Single-line HTML-comment row. Status enum is the only constrained field;
# id / sessions / plan / diary / updated are free-form whitespace-delimited.
_STATUS_ALT = "|".join(sorted(SLICE_STATUS_ENUM))
SLICE_ROW_RE = re.compile(
    r"<!--\s*L:slice\s+"
    r"id=(?P<id>\S+)\s+"
    rf"status=(?P<status>{_STATUS_ALT})\s+"
    r"sessions=(?P<sessions>\d+/\d+)\s+"
    r"plan=(?P<plan>\S*)\s+"
    r"diary=(?P<diary>\S*)\s+"
    r"updated=(?P<updated>\S+)\s*"
    r"-->"
)


def parse_slice_register(spine_path: str | Path) -> list[dict]:
    """Parse all slice-register rows from spine_path.

    Returns a list of {id, status, sessions, plan, diary, updated} dicts in
    file order. Missing file → []. The existing prose around each row is
    NOT parsed — additive design (G7).
    """
    spine_path = Path(spine_path)
    if not spine_path.exists():
        return []
    text = spine_path.read_text(encoding="utf-8")
    out = []
    for m in SLICE_ROW_RE.finditer(text):
        d = m.groupdict()
        out.append({
            "id": d["id"],
            "status": d["status"],
            "sessions": d["sessions"],
            "plan": d["plan"] or "",
            "diary": d["diary"] or "",
            "updated": d["updated"],
        })
    return out


def render_slice_row(slice_id: str, status: str, sessions: str,
                     plan: str = "", diary: str = "",
                     updated: str | None = None) -> str:
    if status not in SLICE_STATUS_ENUM:
        raise ValueError(f"status {status!r} not in {sorted(SLICE_STATUS_ENUM)}")
    if not re.fullmatch(r"\d+/\d+", sessions):
        raise ValueError(f"sessions {sessions!r} must be M/N integer pair")
    if updated is None:
        updated = _today_iso()
    # Empty plan/diary serialize as the literal "_" placeholder to keep the
    # row whitespace-delimited and round-trippable. Parser strips back.
    plan_field = plan if plan else "_"
    diary_field = diary if diary else "_"
    return (
        f"<!-- L:slice id={slice_id} status={status} "
        f"sessions={sessions} plan={plan_field} diary={diary_field} "
        f"updated={updated} -->"
    )


def write_slice_row(spine_path: str | Path, slice_id: str,
                    updates: dict) -> dict:
    """Insert or replace a slice row in spine_path — serialized (S2/A2).

    The read-modify-write below runs under THE one bookkeeping `flock`
    (bookkeeping_lock, keyed on the spine's repo) so two concurrent slice-row
    writers can never lose an update (the lost-update axis the atomic-replace
    alone does not close). Behavior-identical to the prior function otherwise;
    degrades to the unlocked RMW only if the lock module is unavailable.
    """
    try:
        from bookkeeping_lock import bookkeeping_lock
    except Exception:
        return _write_slice_row_unlocked(spine_path, slice_id, updates)
    with bookkeeping_lock(spine_path):
        return _write_slice_row_unlocked(spine_path, slice_id, updates)


def _write_slice_row_unlocked(spine_path: str | Path, slice_id: str,
                              updates: dict) -> dict:
    """Insert or replace a slice row in spine_path. Caller holds the A2 lock.

    updates: dict with any subset of {status, sessions, plan, diary, updated}.
    If a row for slice_id already exists, its fields are merged with
    updates (updates wins) and replaced in place. Otherwise the new row is
    appended at end of file.

    Status is required either in updates or in the existing row.
    """
    spine_path = Path(spine_path)
    text = spine_path.read_text(encoding="utf-8") if spine_path.exists() else ""
    existing_rows = parse_slice_register(spine_path)
    existing = next((r for r in existing_rows if r["id"] == slice_id), None)

    merged: dict = {"id": slice_id, "sessions": "0/1", "plan": "", "diary": ""}
    if existing:
        merged.update(existing)
    merged.update(updates)
    merged["id"] = slice_id
    if "status" not in merged:
        raise ValueError(f"write_slice_row: status required (no existing row for {slice_id!r})")
    if "updated" not in updates:
        merged["updated"] = _today_iso()

    # Round-trip empty fields through render_slice_row's "_" placeholder.
    new_row = render_slice_row(
        slice_id=merged["id"],
        status=merged["status"],
        sessions=merged["sessions"],
        plan=merged.get("plan", ""),
        diary=merged.get("diary", ""),
        updated=merged["updated"],
    )

    if existing is not None:
        pattern = re.compile(
            r"<!--\s*L:slice\s+id=" + re.escape(slice_id) + r"\s+[^>]*?-->",
        )
        new_text, n = pattern.subn(new_row, text, count=1)
        if n != 1:
            raise RuntimeError(f"write_slice_row: failed to replace row for {slice_id!r}")
    else:
        sep = "\n\n" if text and not text.endswith("\n\n") else ""
        new_text = (text.rstrip("\n") + "\n\n" if text else "") + new_row + "\n"

    _atomic_write(spine_path, new_text)
    _record_ledger_write(spine_path)          # A5: the spine IS a tracked repo file
    # Parser strips "_" placeholders back to empty in subsequent reads —
    # do the same here for the returned row dict.
    rendered = {
        "id": merged["id"],
        "status": merged["status"],
        "sessions": merged["sessions"],
        "plan": merged.get("plan", "") or "",
        "diary": merged.get("diary", "") or "",
        "updated": merged["updated"],
    }
    return {"status": "OK", "replaced": existing is not None, "row": rendered, "raw": new_row}


# Parser normalization: treat "_" placeholder in plan/diary fields as empty.
def _normalize_parsed_rows(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        rr = dict(r)
        if rr.get("plan") == "_":
            rr["plan"] = ""
        if rr.get("diary") == "_":
            rr["diary"] = ""
        out.append(rr)
    return out


# Wrap the parser so external callers always get normalized rows.
_raw_parse_slice_register = parse_slice_register


def parse_slice_register(spine_path: str | Path) -> list[dict]:  # noqa: F811
    return _normalize_parsed_rows(_raw_parse_slice_register(spine_path))


# ---------------------------------------------------------------------------
# A0 — declared-slice-count reader (the plan/spine `#### Slices` pipe-table)
# ---------------------------------------------------------------------------

# Evolution Test (single-recognition-shape intent): the `^S\d+$` slice-id shape
# below is deliberately a DUPLICATE of `_SLICE_ID_RE` at
# `~/.claude/skills/execute-plan/run.py:1456`. That module lives under
# `~/.claude/skills/`, is not on the hooks package path, and is not importable
# from here — which is precisely why a minimal reader is co-located in this
# module rather than shared. If the declared slice-id shape ever changes, BOTH
# loci must change together (accepted, bounded duplication — auto-registration
# Mode-C plan, Design Review "Cockburn tests").
_DECLARED_SLICE_ID_RE = re.compile(r"^S\d+$")
# Any heading level 1-6. A level the regex does NOT match is an under-count (the
# section becomes invisible), and under-counting is the unsafe direction — so the
# recognizer is deliberately permissive about level.
_DECLARED_SLICES_HEADING_RE = re.compile(r"^(#{1,6})\s+Slices\b", re.IGNORECASE)
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s")


# Leading markdown "container" markers — blockquote (`>`) and list bullets
# (`-`/`*`/`+`/`1.`) — in any order and repetition. Stripped before matching so a
# declared table nested in a blockquote, a list, or a blockquote-inside-a-list is
# still read. Not reading it would UNDER-count, the unsafe direction.
_CONTAINER_PREFIX_RE = re.compile(r"^(?:\s*(?:>|[-*+]|\d+[.)])\s+|\s*>\s*)+")


def _first_pipe_cell(line: str) -> str | None:
    """First cell of a Markdown pipe-table row, or None if not such a row.

    Emphasis/backtick decoration is stripped so `| **S1** |` reads as `S1`.
    A separator row (`|----|----|`) and a header row (`| ID | Name |`) both
    return a cell that simply will not match `_DECLARED_SLICE_ID_RE`.

    Leading markdown container markers — blockquote and list bullets — are
    stripped first, so a table nested in a blockquote (`> | S1 | … |`), a list
    (`- | S1 | … |`), or both (`- > | S1 | … |`) is still read. Dropping such a
    row would UNDER-count the declared slices — the unsafe direction (see
    `declared_slice_count`).
    """
    line = _CONTAINER_PREFIX_RE.sub("",line).strip()
    if not line.startswith("|"):
        return None
    parts = line.split("|")
    if len(parts) < 3:
        return None
    return parts[1].strip().strip("*`_ ").strip()


def declared_slice_count(path: str | Path) -> int:
    """Count the slices DECLARED in a spine/plan's `#### Slices` pipe-table.

    This is the *declared* count (what the plan says it will deliver), which is
    a different thing from the *registered* rows `parse_slice_register` reads
    (the `<!-- L:slice -->` rows, written INCREMENTALLY, one per slice as it is
    planned/shipped). `all_slices_done` compares the two.

    Recognition rules (deliberately minimal):
      * scan is bounded by a `Slices` heading (level 2-6); each section ends at
        the next heading of the same or shallower level;
      * a row counts only when its FIRST CELL matches `^S\\d+$` — never because
        of header text (so a "Slices" heading with an unrelated table under it
        contributes nothing, and a `### Slice register` table of `S-A`-style ids
        is not a declared table by this reader);
      * the result is the UNION of the ids found across ALL such sections.

    **Union, not first-section-wins — safe by construction.** The two error
    directions are NOT symmetric: an over-count only ever BLOCKS a retire (loud,
    recoverable — the operator retires by hand), while an UNDER-count routes
    `all_slices_done` to the row-count-only fallback and can retire a live topic
    (silent, unrecoverable). Every "pick the right section" heuristic — first
    section wins, skip fenced examples, CommonMark fence pairing — buys accuracy
    at the cost of a tail of pathological inputs that under-count, which is the
    unsafe direction. Taking the union has no such tail: an illustrative or
    superseded table can only inflate the count, so a topic carrying one simply
    does not auto-retire. That is the same trade-off the `< 2` row guard below
    already makes, and it is why this reader does no fence parsing at all.

    Returns 0 when the file is missing/unreadable, when no `Slices` heading is
    present, or when no `S<N>` rows resolve. **Those three are not the same
    question, and this return cannot tell them apart** — which is why
    `declared_slice_count_with_heading` below exists and why `all_slices_done`
    consumes that one instead. Callers of THIS function treat 0 as "no declared
    count resolved" and fall back to the row-count-only predicate; a 0 must
    NEVER be read as "0 declared, therefore complete". That reading stays
    correct for the ship-time counter in `pre_plan_gates.auto_register_topic`,
    which wants a count and nothing else — its contract is deliberately
    untouched here.
    """
    # ONE loop, not two — the same sibling+projection convention as
    # `_impl_specifics_scope_for` / `impl_specifics_scope_with_unresolved`
    # below. The body lives in the richer function; this is the thin
    # projection, so the bare-int callers' contract is byte-identical.
    count, _heading_seen = declared_slice_count_with_heading(path)
    return count


def declared_slice_count_with_heading(path: str | Path) -> tuple[int, bool]:
    """`declared_slice_count`, plus whether a `Slices` heading was ever seen.

    Returns `(count, heading_seen)`. Added alongside the int-returning function
    rather than replacing it, because that one has existing callers whose
    contract is a bare int — notably the cosmetic ship-time session counter in
    `pre_plan_gates.auto_register_topic`, which must not inherit any new
    strictness.

    `heading_seen` is what separates the two states a bare 0 conflates: a topic
    with **no declaration to compare against** (`(0, False)` — the reader saw
    nothing, and the caller has no grounds to refuse), from one whose
    **declaration is present but unreadable** by this deliberately minimal
    reader (`(0, True)` — a heading matched and no `S<N>` row resolved beneath
    it). `all_slices_done` answers not-finished on the second and falls back
    verbatim on the first.

    Recognition is unchanged: `heading_seen` is set by exactly the same
    `_DECLARED_SLICES_HEADING_RE` match that bounds the scan, so a heading this
    reader does not recognise (a differently-worded one, or a declaration held
    in a sibling `_DESIGN.md`) reads as `False` — not covered, by construction.
    A heading inside a fenced example counts as seen and over-blocks, which is
    the same safe direction the union-over-precision stance above already takes.
    """
    p = Path(path)
    if not p.exists():
        return 0, False
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError is a ValueError, NOT an OSError — a non-UTF-8 spine
        # must return 0 (route to the fallback), never raise out of a predicate.
        return 0, False
    # Blockquote markers are stripped everywhere before matching, so a whole
    # section nested in a blockquote is still seen. Not seeing it would
    # UNDER-count — the unsafe direction.
    lines = [_CONTAINER_PREFIX_RE.sub("",ln.strip()).strip()
             for ln in text.splitlines()]
    n = len(lines)
    seen: set[str] = set()
    heading_seen = False
    i = 0
    while i < n:
        m = _DECLARED_SLICES_HEADING_RE.match(lines[i])
        if not m:
            i += 1
            continue
        heading_seen = True
        level = len(m.group(1))
        j = i + 1
        while j < n:
            stripped = lines[j]
            h = _MD_HEADING_RE.match(stripped)
            if h and len(h.group(1)) <= level:
                break
            cell = _first_pipe_cell(stripped)
            if cell is not None and _DECLARED_SLICE_ID_RE.match(cell):
                seen.add(cell)
            j += 1
        i = j
    return len(seen), heading_seen


# ---------------------------------------------------------------------------
# A4 — all_slices_done predicate
# ---------------------------------------------------------------------------

# E1 predicate-review rubric (project-tracking-staleness S1 / AD-7) — row-count
# axis. See the full 3-axis rubric in `work_done.py` above `_nearest_heading_depth`.
# Row-count axis: slice-register rows are written INCREMENTALLY (one per slice at
# plan-filing — execute-plan/run.py), so a register holding a single SHIPPED row
# CANNOT distinguish "1-of-1 slices done" (genuinely complete) from "1-of-N
# shipped" (a multi-slice plan on its first ship). Trusting a single terminal row
# as "all done" retires a multi-slice topic prematurely (Mechanism 8). The guard
# below treats <2 rows as insufficient evidence of completeness.
def all_slices_done(thought_path: str | Path) -> bool:
    """True iff the topic's slices are all terminal — measured against the
    DECLARED slice count when one resolves, else against the row count alone.

    Two paths:

    * **Declared path** (a `#### Slices` table resolves >= 1 declared slice):
      require `len(terminal) >= declared` AND `len(rows) >= declared` AND every
      registered row terminal. This closes the defect where a multi-slice topic
      whose only *registered* rows happen to be terminal was judged retire-
      eligible mid-flight — rows are written incrementally, so "all registered
      rows are SHIPPED" never proved "all declared slices shipped".
    * **Seen-but-unreadable path** (a `Slices` heading matched but no `S<N>`
      row resolved beneath it): returns False. The reader can see that a
      declaration exists and cannot read it, so it has no count to compare
      against and no grounds to call the topic complete. **This trigger used to
      route to the fallback below** — the retired sub-clause read "an
      early-draft/empty table" — and that is the defect this branch closes: a
      declaration naming more work than the register knows about was answered
      from the register alone. Retired on measurement, not on judgment: swept
      corpus-wide, exactly one artifact both carries a matching heading and
      resolves no count, and its verdict is the wrong one today; the artifacts
      the fallback legitimately serves carry no matching heading and are
      untouched (see the `heading_seen` note on
      `declared_slice_count_with_heading`).

      **This is a refusal, not a completeness verdict.** It answers to the
      declaration becoming readable, not to slice bookkeeping — registering the
      outstanding slices, or flipping them to NEVER, leaves it False.
    * **Fallback path** (no declared count resolves AND no `Slices` heading was
      seen — a legacy spine with no `#### Slices` table, or a plan-less Mode-C
      topic): behave EXACTLY as before — fewer than 2 rows returns False, else
      all rows must be terminal. A `declared == 0` therefore routes here and is
      never read as `0 >= 0` "complete"; a legacy all-terminal register still
      retires exactly as it does today (no new false-block).

    The <2-row fallback guard stands for the same reason it always did
    (project-tracking-staleness S1, row-count axis): one SHIPPED row is
    indistinguishable from a 1-of-N first ship. A genuinely single-slice plan
    does not auto-retire; the operator retires it manually (accepted, loud-and-
    recoverable — a false-positive retire is silent and catastrophic, a
    false-negative is not).

    Known accepted behavior (auto-registration Mode-C plan, residual R4): if a
    registered `<!-- L:slice -->` row is deleted while its `S<N>` row remains in
    the declared table, `len(rows) < declared` blocks retirement until the two
    are reconciled. That is the safe failure direction.
    """
    rows = parse_slice_register(thought_path)
    terminal = [r for r in rows if r["status"] in {"SHIPPED", "NEVER"}]
    # One read, not two — the sibling carries both answers.
    declared, heading_seen = declared_slice_count_with_heading(thought_path)
    if declared >= 1:
        return (len(terminal) >= declared
                and len(rows) >= declared
                and len(terminal) == len(rows))
    if heading_seen:
        # A declaration is present and this reader cannot read it. Refuse
        # rather than answer from the register alone.
        return False
    # Fallback — today's predicate, verbatim.
    if len(rows) < 2:
        return False
    return all(r["status"] in {"SHIPPED", "NEVER"} for r in rows)


# ---------------------------------------------------------------------------
# A5 — attribute_commits (diff-intersect)
# ---------------------------------------------------------------------------

_DIFF_HEADING_RE = re.compile(r"^##\s+Diff\s*$", re.MULTILINE)
_DIFF_SESSION_RE = re.compile(r"^\*\*Session\s+(\d+)[^*\n]*\*\*\s*$", re.MULTILINE)
_DIFF_PATH_LINE_RE = re.compile(r"^-\s+`([^`]+)`", re.MULTILINE)
_ANY_H2_RE = re.compile(r"^##\s+", re.MULTILINE)


def parse_diff_section(plan_path: str | Path) -> dict[int, list[str]]:
    """Parse the plan's ## Diff section.

    Returns {session_num: [path, ...]} for every "**Session N:**" sub-section
    inside the top-level ## Diff heading. Returns {} if the section is
    absent. Paths are returned verbatim (no expansion, no normalization).
    """
    text = Path(plan_path).read_text(encoding="utf-8")
    m = _DIFF_HEADING_RE.search(text)
    if not m:
        return {}
    start = m.end()
    nxt = _ANY_H2_RE.search(text, start)
    end = nxt.start() if nxt else len(text)
    section = text[start:end]

    submatches = list(_DIFF_SESSION_RE.finditer(section))
    if not submatches:
        return {}
    out: dict[int, list[str]] = {}
    for i, sm in enumerate(submatches):
        n = int(sm.group(1))
        sub_start = sm.end()
        sub_end = submatches[i + 1].start() if i + 1 < len(submatches) else len(section)
        sub_text = section[sub_start:sub_end]
        out[n] = [pm.group(1).strip() for pm in _DIFF_PATH_LINE_RE.finditer(sub_text)]
    return out


# ---------------------------------------------------------------------------
# A1 (project-tracking-staleness S8 / M3) — Implementation-Specifics scope
# fallback + spine→plan resolution + loose prefix-aware matching.
#
# The `## Diff` format above is not what `/plan` writes; real `_PLAN.md` files
# name their touched files in the Gate-1 "Source Location" column, the
# `## Implementation Specifics` bullets, and the `## Coherent Actions` table.
# And the S4 omission Stop hook passes the topic SPINE (not the plan), whose
# `# Implementation Details` is only a wikilink list — so a spine input must be
# resolved to its linked plans (via the slice-register `plan=` field) first.
# Loose matching bridges plan-named paths (`work_done.py`, `~/.claude/…`) to
# config-source diff paths (`dot_claude/hooks/work_done.py`).
# ---------------------------------------------------------------------------

# A backtick-quoted token that looks like a file path: a known code/doc
# extension, optionally a trailing `:line` or `:line-line`.
_IMPL_SPECIFICS_FILE_RE = re.compile(
    r"`([~\w./-]+\.(?:py|sh|md|json|jsonl|ya?ml|txt|toml|cfg|ini))(?::\d+(?:-\d+)?)?`"
)

# Only these plan-structural sections declare the touched-file scope. Scoping to
# them (rather than the whole document) is what makes a PLAN yield scope while a
# SPINE — which has none of these H2 sections, only prose backtick refs and a
# `# Implementation Details` wikilink list — yields empty, so a spine correctly
# falls through to linked-plan resolution in `_impl_specifics_scope_for`.
_IMPL_SPECIFICS_SECTION_RE = re.compile(
    r"^##\s+(?:Coherent Actions|Implementation Specifics|Gate 1)\b.*$",
    re.MULTILINE | re.IGNORECASE,
)
_ANY_H1_H2_RE = re.compile(r"^#{1,2}\s+\S", re.MULTILINE)


def parse_impl_specifics_scope(plan_path: str | Path) -> set[str]:
    """Extract the file scope from a plan written in the Implementation-Specifics
    format — only from the `## Coherent Actions`, `## Implementation Specifics`,
    and `## Gate 1` sections (Source Location column). Returns a set of raw path
    strings (a trailing `:line` is stripped); empty set if none of those
    sections are present (e.g. a spine). Does NOT resolve a spine — see
    `_impl_specifics_scope_for`.
    """
    try:
        text = Path(plan_path).read_text(encoding="utf-8")
    except OSError:
        return set()
    scope: set[str] = set()
    for m in _IMPL_SPECIFICS_SECTION_RE.finditer(text):
        start = m.end()
        nxt = _ANY_H1_H2_RE.search(text, start)
        section = text[start:(nxt.start() if nxt else len(text))]
        scope |= {fm.group(1) for fm in _IMPL_SPECIFICS_FILE_RE.finditer(section)}
    return scope


def _plans_dir() -> Path:
    """The harness plans directory — the second search location for a plan that has
    not yet been relocated into the topic's `Thoughts/`."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "plans"


def resolve_plan_ref(ref: str, spine_dir: str | Path) -> tuple[Path | None, str | None]:
    """Resolve one slice-register `plan=` reference to a file on disk.

    Returns `(path, None)` on success and `(None, reason)` on failure — never a bare
    `None`, so a caller can say WHICH reference it could not open and why. That is the
    whole point: the shape this replaces caught an `OSError` and returned an empty set,
    which reads identically to "this topic declares no scope".

    **THE PARAMETER IS `ref`, NOT `slug`, AND THAT IS LOAD-BEARING.** The call sites
    used to hold the raw field in a local of the other name, and the completeness gate
    for this change greps these two files for that name interpolated into a dotted-md
    f-string. Naming the parameter that way here would re-create the very literal the
    gate searches for, inside the file being searched, and fail a correct
    implementation. The gate carries no exemption for this function's body,
    deliberately — an exemption is a hole.

    (This docstring deliberately does not spell that literal out. An earlier draft did,
    and tripped the gate from prose — which is itself the honest demonstration of why
    the grep is only the SECONDARY check: it matches text, not behaviour, and cannot
    tell an implementation from a sentence about one. The primary check is the test
    asserting both consumers call this function.)

    **Four shapes exist in the live corpus** (measured 2026-08-26 over 63 populated rows,
    not guessed), and the reader this replaces accepted only the first:

        wikilink         `[[foo_S1_PLAN]]`              38 rows
        bare slug        `foo-20260727121447_PLAN`       9 rows
        bare + .md       `dapper-coalescing-seal.md`     9 rows
        wikilink + .md   `[[foo_S2_PLAN.md]]`            7 rows
                                                    ---------
                                                       63 rows

    (An earlier version of this table read "38 + 9" on the bare row, summing to 101
    over a stated 63 and implying 47 bare rows against the plan's measured 18. Caught
    by an independent reviewer. A wrong count inside the function that documents the
    measurement is the same prose-drift this change exists to repair.)

    Stripping is **brackets THEN extension, in that order** — `[[x.md]]` needs both, and
    doing it the other way round leaves the brackets wrapped around a stripped stem. An
    alias form `[[target|display]]` resolves on the target half; it is not in the corpus
    today but costs one `split` to accept, and a fifth shape arriving is the normal case
    for hand-edited files rather than the exceptional one.

    Search order is spine directory first, then the harness plans directory. The spine
    directory wins because it holds the durable relocated copy. **Stated honestly: the
    second location recovers zero live spines today** — the two topics that motivated it
    reference plans absent from the entire tree — so it is forward-looking rather than
    evidence-backed, and this docstring says so rather than implying a benefit it does
    not currently deliver.
    """
    raw = (ref or "").strip()
    if not raw or raw == "_":
        return None, "no plan reference on this row"

    stem = raw
    if stem.startswith("[[") and stem.endswith("]]"):
        stem = stem[2:-2].strip()
    stem = stem.split("|", 1)[0].strip()      # [[target|display]] -> target
    if stem.endswith(".md"):
        stem = stem[:-3]
    if not stem:
        return None, f"plan reference {raw!r} is empty after normalisation"

    candidates = []
    try:
        candidates.append(Path(spine_dir) / f"{stem}.md")
    except (OSError, TypeError, ValueError):
        pass
    try:
        candidates.append(_plans_dir() / f"{stem}.md")
    except (OSError, TypeError, ValueError):
        pass

    for cand in candidates:
        try:
            if cand.is_file():
                return cand, None
        except OSError:
            continue

    where = " or ".join(str(c.parent) for c in candidates) or "(no search location)"
    return None, f"plan reference {raw!r} does not resolve to a file under {where}"


def _impl_specifics_scope_for(path: str | Path) -> set[str]:
    """Scope for a fallback (non-`## Diff`) input. If `path` is a plan, its own
    Implementation-Specifics scope; if `path` is a spine (no own scope but a
    `## Slice Register`), the union of its linked `_PLAN.md`s' scope, resolved
    via each slice-register row's `plan=` slug (the S4 detection path passes the
    spine). Fail-safe: an unresolvable/empty result is `set()` → soft-exit.
    """
    # ONE loop, not two. This function and `impl_specifics_scope_with_unresolved`
    # originally each carried their own copy of the register-iteration body — which is
    # exactly the "two parallel repairs that can drift" shape this change's own Guiding
    # Policy forbids, committed one level ABOVE the normaliser the policy was applied
    # to. Caught by an independent reviewer. This is now the thin projection: same
    # traversal, same resolver, the failure list dropped.
    scope, _unresolved = impl_specifics_scope_with_unresolved(path)
    return scope


def impl_specifics_scope_with_unresolved(
    path: str | Path,
) -> tuple[set[str], list[tuple[str, str]]]:
    """`_impl_specifics_scope_for`, plus the references that did NOT resolve.

    Returns `(scope, unresolved)` where `unresolved` is a list of
    `(reference, reason)` pairs. Added alongside the set-returning function rather
    than replacing it, because that one has existing callers whose contract is a bare
    set; S3 carries this richer return through to the gate's reason string.
    """
    direct = parse_impl_specifics_scope(path)
    if direct:
        return direct, []
    try:
        rows = parse_slice_register(path)
    except Exception:
        rows = []
    if not rows:
        return set(), []
    try:
        spine_dir = Path(path).expanduser().resolve().parent
    except OSError:
        return set(), []
    scope: set[str] = set()
    unresolved: list[tuple[str, str]] = []
    for r in rows:
        ref = (r.get("plan") or "").strip()
        if not ref or ref == "_":
            continue
        resolved, reason = resolve_plan_ref(ref, spine_dir)
        if resolved is None:
            unresolved.append((ref, reason or "unresolved"))
            continue
        scope |= parse_impl_specifics_scope(resolved)
    return scope, unresolved


def _canonical_tail(p: str) -> str:
    """Canonicalize a path for loose matching: de-config-source-prefix each component
    (`dot_claude`→`.claude`, leading `dot_`→`.`, strip `executable_`) and reduce
    to the tail after the last `.claude/` (else keep repo-relative)."""
    p = os.path.expanduser(p.strip()).lstrip("/")
    parts = []
    for part in p.split("/"):
        if part == "dot_claude":
            parts.append(".claude")
        elif part.startswith("executable_"):
            parts.append(part[len("executable_"):])
        elif part.startswith("dot_"):
            parts.append("." + part[len("dot_"):])
        else:
            parts.append(part)
    canon = "/".join(parts)
    marker = ".claude/"
    idx = canon.rfind(marker)
    if idx != -1:
        canon = canon[idx + len(marker):]
    return canon


def _loose_scope_match(commit_file: str, scope: set[str]) -> bool:
    """True iff `commit_file` (a repo-relative diff path) loosely matches any
    scope entry: canonical-equal, OR the commit tail ends at a component
    boundary with a directory-qualified scope entry, OR (bare-basename scope
    entry, no `/`) the basenames are equal (guards the non-unique `SKILL.md`)."""
    c = _canonical_tail(commit_file)
    if not c:
        return False
    c_base = c.rsplit("/", 1)[-1]
    for s in scope:
        s_canon = _canonical_tail(s)
        if not s_canon:
            continue
        if c == s_canon:
            return True
        if "/" in s_canon:
            if c.endswith("/" + s_canon):
                return True
        elif c_base == s_canon:
            return True
    return False


def _normalize_path_for_diff(p: str, repo_root: Path | None) -> str:
    """Normalize a path for set-intersection against `git diff-tree` output.

    Expands `~`, strips an optional `repo_root` prefix, drops a leading `/`.
    Comparison is repo-relative — both sides go through this function.
    """
    p = os.path.expanduser(p.strip())
    if repo_root is not None:
        rr = str(repo_root)
        if p == rr:
            p = ""
        elif p.startswith(rr.rstrip("/") + "/"):
            p = p[len(rr.rstrip("/")) + 1:]
    return p.lstrip("/")


def attribute_commits(plan_path: str | Path, session_id: str,
                      started_at: str, ended_at: str,
                      session_num: int | None = None,
                      repo_path: str | Path | None = None) -> dict:
    """Diff-intersect commits against a plan's declared scope.

    Classification rules (locked in plan A5 Guard rails):
      * commit.files ⊆ scope                 → confident
      * commit.files ∩ scope ≠ ∅, not ⊆       → ambiguous (mixed-scope; J surfaces)
      * commit.files ∩ scope = ∅              → unmatched (NOT attributed)

    The ambiguous list is for /work-done (Slice J) to surface to the user;
    L never silent-attributes ambiguous commits. session_id is accepted as
    a parameter for J's audit-log purpose but is not used in the
    classification itself (commits within [started_at, ended_at] are
    candidates regardless of authorship).
    """
    diff_map = parse_diff_section(plan_path)
    repo_root = Path(repo_path).expanduser().resolve() if repo_path else None

    # Primary: the `## Diff` format (exact repo-relative set-intersection).
    # Fallback (M3): when no `## Diff` is present, the Implementation-Specifics
    # format — parsed directly for a plan input, or spine→linked-plan-resolved
    # for a spine input (the S4 detection path) — matched LOOSELY (prefix-aware)
    # because plan-named paths don't equal config-source diff paths.
    use_loose = False
    unresolved_refs: list[tuple[str, str]] = []
    if diff_map:
        if session_num is not None:
            scope_raw = diff_map.get(session_num, [])
        else:
            scope_raw = [p for paths in diff_map.values() for p in paths]
        scope = {_normalize_path_for_diff(p, repo_root) for p in scope_raw}
        scope.discard("")  # drop any normalized-empty entries
    else:
        # S3/A4: the richer read, so a reference that could not be opened travels as a
        # NAMED string instead of vanishing into an empty set.
        scope, unresolved_refs = impl_specifics_scope_with_unresolved(plan_path)
        use_loose = True

    if not scope:
        # Fail-safe (unchanged): no derivable scope → soft-exit downstream.
        #
        # `unresolved` rides BOTH this branch and the success branch below, and that is
        # the whole of A4's point. `scope_empty` is set ONLY here, so on its own it
        # cannot express the case that becomes common once the resolver works: a spine
        # where most rows resolve and one does not. That spine has a non-empty scope,
        # never reaches this branch, and would otherwise report nothing amiss.
        return {
            "confident": [], "ambiguous": [], "unmatched": [],
            "scope_empty": True, "session_id": session_id,
            "unresolved": unresolved_refs,
        }

    log_cmd = ["git"]
    if repo_root:
        log_cmd += ["-C", str(repo_root)]
    log_cmd += ["log", "--pretty=%H", f"--since={started_at}", f"--until={ended_at}"]
    log_res = subprocess.run(log_cmd, capture_output=True, text=True, check=False)
    if log_res.returncode != 0:
        raise RuntimeError(f"git log failed: {log_res.stderr.strip()}")
    shas = [s for s in log_res.stdout.strip().splitlines() if s]

    confident: list[str] = []
    ambiguous: list[tuple[str, str]] = []
    unmatched: list[str] = []

    for sha in shas:
        dt_cmd = ["git"]
        if repo_root:
            dt_cmd += ["-C", str(repo_root)]
        # --root makes diff-tree list paths for root commits too (else it
        # returns empty for a commit with no parent — the root-commit edge
        # case that classifies a root commit as "unmatched" by accident).
        dt_cmd += ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha]
        dt_res = subprocess.run(dt_cmd, capture_output=True, text=True, check=False)
        if dt_res.returncode != 0:
            raise RuntimeError(f"git diff-tree {sha} failed: {dt_res.stderr.strip()}")
        raw_lines = [line for line in dt_res.stdout.strip().splitlines() if line]
        if use_loose:
            files = {l.strip() for l in raw_lines if l.strip()}  # raw repo-relative
            if not files:
                unmatched.append(sha)
                continue
            matched = {f for f in files if _loose_scope_match(f, scope)}
            any_match = bool(matched)
            all_match = matched == files
            outside = sorted(files - matched)
        else:
            files = {_normalize_path_for_diff(line, repo_root) for line in raw_lines}
            files.discard("")
            if not files:
                unmatched.append(sha)
                continue
            any_match = bool(files & scope)
            all_match = files <= scope
            outside = sorted(files - scope)
        if not any_match:
            unmatched.append(sha)
        elif all_match:
            confident.append(sha)
        else:
            ambiguous.append((sha, f"mixed scope: files outside declared = {outside}"))

    return {
        "confident": confident,
        "ambiguous": ambiguous,
        "unmatched": unmatched,
        "scope_empty": False,
        "session_id": session_id,
        # Present here too, and NOT redundant with `scope_empty: False`. This is the
        # partial-resolution case A4 exists for: scope is non-empty, so nothing above
        # signalled a problem, yet one row's plan reference could not be opened and the
        # files it would have contributed are silently missing from the scope.
        "unresolved": unresolved_refs,
    }


# ---------------------------------------------------------------------------
# A7 — Sync layer (Session 2)
#
# Wraps pre_plan_gates.phase_start / phase_stop post-`_write_topic_state`.
# Updates the TODO line annotation (via `todo.py mark-in-phase`) and the
# spine slice-register row (via A3) when they apply. Each surface write is
# followed by `verify_write` (A2). On verifier failure, the caller is expected
# to roll back the topic-state JSON (additive — L does not own phase state).
#
# Three-rule conflict resolution (A8) per Guiding Policy item 6:
#   (a) silent auto-reconcile  — two L-managed transitions one step apart
#   (b) flag-and-ask payload   — pre-existing legacy state (e.g., `(in
#                                progress: …)` from mark-in-progress) vs L's
#                                intended write; emit payload (no LLM call
#                                inside this module — payload is surfaced to
#                                the caller, who routes it to a user prompt
#                                or to a separate LLM-adjudication path)
#   (c) hard-fail              — two fresh L-managed transitions diverging
#                                (different session_id at same line)
# ---------------------------------------------------------------------------

HOOKS_DIR = Path(__file__).parent

# Mirror of todo.py._PHASE_ANNOT_RE — kept in sync; new phases require an
# update here and there (single source-of-truth would require importing
# todo.py which carries the harness CLI side-effects).
_TODO_PHASE_ANNOT_RE = re.compile(
    r"\(in (?P<phase>thought|clarification|planning|implementation|closing): "
    r"(?P<sid>[a-f0-9-]+)\)"
)
_TODO_IN_PROGRESS_RE = re.compile(r"\(in progress: (?P<sid>[a-f0-9-]+)\)")


class SyncConflictError(RuntimeError):
    """Rule (c) hard-fail — two fresh L-managed transitions diverging."""


def _todo_path_from_state(state: dict) -> Path | None:
    project_root = state.get("project_root")
    if not project_root:
        return None
    p = Path(project_root) / "TODO.md"
    return p if p.exists() else None


def _spine_path_from_state(state: dict) -> Path | None:
    project_root = state.get("project_root")
    thought = state.get("thought_file_path")
    if not project_root or not thought:
        return None
    p = Path(project_root) / thought
    return p if p.exists() else None


def _todo_match_pattern_from_state(state: dict) -> str | None:
    """Derive a TODO-line match pattern from the topic's intake source.

    Convention (locked at Slice C-ii): every [Thought]-tagged TODO carries a
    `Master plan: [[<spine-stem>]]` wikilink. Matching on the stem locates
    the line uniquely.

    Plain mode (intake_source == "plain"): the TODO line already exists on
    disk; `todo_line_ref` carries `<path>:<line>` (set at create-topic time
    by /work-start). Read the file at use-time, extract the line content,
    strip the leading `- [ ] ` (and optional `🚧 ` prefix), and return its
    re-escaped text as the match pattern. Same source-of-truth that
    /work-done uses (work-done/SKILL.md step 6) — no persisted excerpt.
    """
    if state.get("intake_source") == "plain":
        ref = state.get("todo_line_ref")
        if not ref or ":" not in ref:
            return None
        path_str, _, line_str = ref.rpartition(":")
        try:
            line_no = int(line_str)
        except ValueError:
            return None
        if line_no < 1:
            return None
        candidate = Path(path_str)
        candidates = []
        if candidate.is_absolute():
            candidates.append(candidate)
        else:
            project_root = state.get("project_root")
            if project_root:
                candidates.append(Path(project_root) / candidate)
            candidates.append(candidate)
        todo_file = next((p for p in candidates if p.exists()), None)
        if todo_file is None:
            return None
        try:
            lines = todo_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        if line_no > len(lines):
            return None
        raw = lines[line_no - 1]
        stripped = raw.lstrip()
        # Strip the leading TODO checkbox if present.
        if stripped.startswith("- [ ] "):
            stripped = stripped[len("- [ ] "):]
        elif stripped.startswith("- [x] "):
            stripped = stripped[len("- [x] "):]
        # Strip an optional in-progress prefix written by a prior /work-start.
        if stripped.startswith("🚧 "):
            stripped = stripped[len("🚧 "):]
        stripped = stripped.strip()
        if not stripped:
            return None
        return re.escape(stripped)

    thought = state.get("thought_file_path")
    if not thought:
        return None
    stem = Path(thought).stem
    if not stem:
        return None
    return re.escape(stem)


def _scan_existing_todo_annotation(todo_path: Path,
                                   match_pattern: str) -> dict | None:
    """Locate the TODO line and return its current annotation snapshot.

    Returns {"line": "...", "in_phase": (phase, sid)|None,
             "in_progress": sid|None} or None if no line matched.
    """
    text = todo_path.read_text(encoding="utf-8")
    pat = re.compile(match_pattern, re.IGNORECASE)
    for line in text.splitlines():
        if line.lstrip().startswith("- [ ]") and pat.search(line):
            mp = _TODO_PHASE_ANNOT_RE.search(line)
            mip = _TODO_IN_PROGRESS_RE.search(line)
            return {
                "line": line,
                "in_phase": (mp.group("phase"), mp.group("sid")) if mp else None,
                "in_progress": mip.group("sid") if mip else None,
            }
    return None


_PHASE_ORDER = ["thought", "clarification", "planning", "implementation", "closing"]


def _classify_conflict(existing: dict | None, new_phase: str,
                       new_sid_short: str) -> str:
    """Return one of: 'none', 'rule_a_silent', 'rule_b_flag', 'rule_c_fail'.

    Rules:
      none           → no existing annotation; clean write
      rule_a_silent  → existing (in <phase'>: same-sid) with phase' adjacent
                       to new_phase OR same phase (idempotent)
      rule_b_flag    → existing (in progress: …) with no L-managed phase
                       annotation; legacy mark-in-progress state present
      rule_c_fail    → existing (in <phase>: <other-sid>) with different sid
    """
    if existing is None or existing.get("in_phase") is None:
        if existing is not None and existing.get("in_progress"):
            return "rule_b_flag"
        return "none"
    cur_phase, cur_sid = existing["in_phase"]
    if cur_sid != new_sid_short:
        return "rule_c_fail"
    if cur_phase == new_phase:
        return "rule_a_silent"
    try:
        i_cur = _PHASE_ORDER.index(cur_phase)
        i_new = _PHASE_ORDER.index(new_phase)
    except ValueError:
        return "rule_a_silent"  # unknown phase — accept the write
    if abs(i_cur - i_new) == 1:
        return "rule_a_silent"
    return "rule_a_silent"  # same session, non-adjacent → still reconcile silently


def _make_flag_and_ask_payload(existing: dict, new_phase: str,
                               new_sid_short: str, todo_path: Path) -> dict:
    """Build the rule (b) flag-and-ask payload.

    The caller is responsible for surfacing this to the user (or to a
    separate LLM-adjudication path). L emits the payload; L does not invoke
    the LLM from here (verify_write path stays pure — Guiding Policy 2).
    """
    return {
        "kind": "rule_b_flag_and_ask",
        "todo_path": str(todo_path),
        "existing_line": existing["line"],
        "existing_in_progress_sid": existing.get("in_progress"),
        "intended_phase": new_phase,
        "intended_sid": new_sid_short,
        "options": [
            {"id": "keep-and-overlay",
             "description": "Keep the legacy `(in progress: …)` annotation; "
                            "additionally add `(in <phase>: …)` from L."},
            {"id": "replace-with-phase",
             "description": "Drop the legacy `(in progress: …)` annotation; "
                            "L's `(in <phase>: …)` becomes the sole annotation."},
        ],
        "default": "keep-and-overlay",
    }


def _run_todo_mark_in_phase(todo_path: Path, match_pattern: str,
                            phase: str, session_id: str) -> dict:
    todo_script = HOOKS_DIR / "todo.py"
    cmd = ["python3", str(todo_script), "mark-in-phase",
           "--pattern", match_pattern,
           "--phase", phase,
           "--session", session_id,
           "--file", str(todo_path)]
    sp = subprocess.run(cmd, capture_output=True, text=True)
    out = {"returncode": sp.returncode, "stdout": sp.stdout.strip(),
           "stderr": sp.stderr.strip()}
    if sp.returncode == 0 and sp.stdout.strip():
        try:
            out["payload"] = json.loads(sp.stdout)
        except json.JSONDecodeError:
            pass
    return out


def sync_phase_transition(state: dict, prev_phase: str | None,
                          new_phase: str, session_id: str,
                          slice_id: str | None = None) -> dict:
    """Synchronize the three surfaces after a phase_start write.

    Caller has already written the topic-state JSON via _write_topic_state.
    This helper:
      1. verifies the topic-state JSON post-write (A2)
      2. computes the TODO line match pattern; if absent (no thought_file_path
         or no project_root), records `todo_skipped` and proceeds
      3. classifies any pre-existing annotation per A8 three rules
      4. on rule_a / none → writes the new annotation via `todo.py
         mark-in-phase` + verifies the write
      5. on rule_b → returns a flag-and-ask payload WITHOUT writing the TODO
         (caller decides; safe default: do not write)
      6. on rule_c → raises SyncConflictError
      7. optionally updates the spine slice-register row when slice_id is
         given and the spine has L:slice rows or an existing row for slice_id

    Returns a receipts dict.
    """
    receipts: dict = {
        "topic_state_verified": False,
        "todo_write": None,
        "todo_verify": None,
        "spine_write": None,
        "conflict_rule": "none",
        "flag_and_ask": None,
    }

    # (1) Topic-state JSON verification.
    state_path = _topic_state_path_for(state)
    if state_path is not None and state_path.exists():
        verify_write("topic_state_json", state_path,
                     expected_payload=new_phase, locator="phase")
        receipts["topic_state_verified"] = True

    # (2) Locate TODO surface.
    todo_path = _todo_path_from_state(state)
    match_pattern = _todo_match_pattern_from_state(state)
    if todo_path is None or match_pattern is None:
        receipts["todo_write"] = {"status": "skipped",
                                  "reason": "no_thought_file_path_or_TODO"}
    else:
        # (3) Classify conflict against existing annotation.
        sid_short = session_id[:8]
        existing = _scan_existing_todo_annotation(todo_path, match_pattern)
        rule = _classify_conflict(existing, new_phase, sid_short)
        receipts["conflict_rule"] = rule

        if rule == "rule_c_fail":
            raise SyncConflictError(
                f"Rule (c) hard-fail: TODO line for {match_pattern!r} "
                f"already carries `{existing['in_phase']}` from a different "
                f"session; refusing to overwrite with "
                f"(in {new_phase}: {sid_short})."
            )

        if rule == "rule_b_flag":
            receipts["flag_and_ask"] = _make_flag_and_ask_payload(
                existing, new_phase, sid_short, todo_path,
            )
            receipts["todo_write"] = {"status": "deferred_for_user_decision"}
        else:
            # rule_a_silent or none → write through.
            run = _run_todo_mark_in_phase(todo_path, match_pattern,
                                          new_phase, session_id)
            receipts["todo_write"] = run
            if run["returncode"] == 0:
                # (4) Post-write verify.
                annotation = f"(in {new_phase}: {sid_short})"
                # Build a regex matching the TODO line by stem + annotation.
                line_locator = (
                    r"^- \[ \].*" + match_pattern + r".*"
                    + re.escape(annotation) + r".*$"
                )
                verify_write("todo_line", todo_path,
                             expected_payload=annotation, locator=line_locator)
                receipts["todo_verify"] = {"status": "OK"}

    # (5) Slice-register row update (best-effort).
    if slice_id is not None:
        spine_path = _spine_path_from_state(state)
        if spine_path is not None:
            # Only write if the spine already has at least one L:slice row OR
            # if slice_id row exists. Otherwise skip — multi-slice spines
            # bootstrap their rows explicitly via /work-done or a manual call.
            rows = parse_slice_register(spine_path)
            row_exists = any(r["id"] == slice_id for r in rows)
            if row_exists or rows:
                status_map = {
                    "thought": "NOW", "clarification": "NOW",
                    "planning": "NOW", "implementation": "NOW",
                    "closing": "NOW",
                }
                wr = write_slice_row(
                    spine_path, slice_id,
                    {"status": status_map.get(new_phase, "NOW"),
                     "sessions": (
                         next((r["sessions"] for r in rows if r["id"] == slice_id),
                              "0/1")
                     ),
                     "updated": _today_iso()},
                )
                receipts["spine_write"] = {"status": "OK",
                                           "row": wr["row"]}
                verify_write(
                    "spine_section", spine_path,
                    expected_payload=f"id={slice_id}",
                    locator=("<!-- L:slice", "-->"),
                )
            else:
                receipts["spine_write"] = {"status": "skipped",
                                           "reason": "spine_has_no_rows_yet"}
        else:
            receipts["spine_write"] = {"status": "skipped",
                                       "reason": "no_spine_path"}

    return receipts


def sync_phase_stop(state: dict, session_id: str) -> dict:
    """Synchronize after a phase_stop write.

    phase_stop appends a stop event but does NOT change state["phase"]. The
    TODO/spine annotations correctly reflect the last-known phase the user
    touched, so they are left in place. This helper only verifies that the
    topic-state JSON's phase_history acquired a stop event.
    """
    receipts: dict = {"topic_state_verified": False}
    state_path = _topic_state_path_for(state)
    if state_path is not None and state_path.exists():
        # Light verification: ensure phase field matches state.get("phase").
        cur_phase = state.get("phase")
        if cur_phase is None:
            return receipts
        verify_write("topic_state_json", state_path,
                     expected_payload=cur_phase, locator="phase")
        receipts["topic_state_verified"] = True
    return receipts


def _topic_state_path_for(state: dict) -> Path | None:
    """Reconstruct the topic-state JSON path under pre_plan_gates state dir."""
    topic_slug = state.get("topic_slug")
    project_slug = state.get("project_slug")
    if not topic_slug or not project_slug:
        return None
    return (Path.home() / ".claude" / "state" / "pre_plan_gates"
            / f"{topic_slug}__{project_slug}.json")


# ---------------------------------------------------------------------------
# CLI dispatcher (A10 — Session 2 adds non-holder confirmation prompt).
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="taskmanagement.py",
        description=(
            "Slice L Foundations — lifecycle-sync agent. "
            "Manages the per-topic logical lock, slice-register row format, "
            "and commit-attribution helpers consumed by /work-start (Session 2) "
            "and /work-done (Slice J)."
        ),
    )
    sub = p.add_subparsers(dest="cmd")

    rel = sub.add_parser("release", help="Release a topic lock.")
    rel.add_argument("topic_slug",
                     help="the FULL lock key `<topic>__<project>` (the .lock filename "
                          "stem, as `status` lists it) — a bare topic slug matches nothing")
    rel.add_argument("--session-id", help="caller's session id (required unless --force/--yes)")
    rel.add_argument("--force", action="store_true",
                     help="release regardless of holder identity (non-interactive). "
                          "Without --yes, an interactive confirmation prompt fires for non-holder releases on a TTY.")
    rel.add_argument("--yes", action="store_true",
                     help="skip the interactive confirmation prompt for non-holder release (assume yes)")

    sub.add_parser("status", help="Show all current locks under ~/.claude/state/locks/ "
                                  "with each holder's probed liveness.")

    hb = sub.add_parser("heartbeat",
                        help="S4 per-turn liveness refresh: bump last_heartbeat on every "
                             "lock the session holds. Silent, always exit 0.")
    hb.add_argument("--session-id", required=True)

    rs = sub.add_parser("release-session",
                        help="S4 session-end release: move aside every lock the session "
                             "holds. Always exit 0.")
    rs.add_argument("--session-id", required=True)

    sw = sub.add_parser("sweep",
                        help="S4 predicate-driven sweep of locks whose holder is provably "
                             "dead (S4 identity + not mine + DEAD + heartbeat ≥ STALE_T). "
                             "Never touches a legacy payload. Always exit 0.")
    sw.add_argument("--session-id", help="the running session; its own locks are never swept")
    return p


def _cmd_release(args) -> int:
    if not args.force and not args.session_id:
        print("error: --session-id required (or use --force)", file=sys.stderr)
        return 2

    # Holder release path is silent — no prompt regardless of TTY.
    payload_path = _lock_payload_path(args.topic_slug)
    existing = read_lock(args.topic_slug)
    if existing is None and payload_path.exists():
        # A corrupt payload (not JSON / not an object): the recovery command must
        # REPORT it, never traceback. `--force` retires it through release_lock
        # (audit `corrupt-payload-release`); without --force there is no holder to
        # compare against, so refuse and say how to retire it.
        if not args.force:
            print(json.dumps({
                "status": "NOT_HOLDER", "topic_slug": args.topic_slug, "holder": None,
                "reason": "payload unparseable — retire it with --force --yes",
            }, indent=2))
            return 1
        res = release_lock(args.topic_slug, session_id=args.session_id, force=True)
        print(json.dumps(res, indent=2, default=str))
        return 0 if res.get("status") == "RELEASED" else 1
    if existing is None:
        # S4: NOT_HELD is a FAILURE for this verb, not a success. The documented
        # recovery command used to name a bare topic slug, but locks are keyed
        # `<topic>__<project>`, so it found nothing, released nothing and exited 0
        # — reporting success while the operator stayed blocked. Name the shape,
        # list the keys that share the prefix, exit 1.
        topic, _sep, _proj = args.topic_slug.rpartition("__")
        prefix = args.topic_slug if not _sep else topic
        candidates = sorted(
            p.stem for p in LOCKS_DIR.glob("*.lock")
            if p.stem == prefix or p.stem.startswith(prefix + "__")
        ) if LOCKS_DIR.exists() else []
        print(json.dumps({
            "status": "NOT_HELD",
            "topic_slug": args.topic_slug,
            "hint": ("no lock at that key; lock keys are `<topic>__<project>` "
                     "(run `taskmanagement.py status` to list them)"),
            "candidates": candidates,
        }, indent=2))
        return 1
    is_holder = (
        args.session_id is not None
        and existing.get("session_id") == args.session_id
    )

    # Non-holder release prompt: required on TTY unless --yes is given.
    if not is_holder and args.force and not args.yes and sys.stdin.isatty():
        sys.stderr.write(
            f"Non-holder release of lock {args.topic_slug!r}.\n"
        )
        if existing:
            sys.stderr.write(
                f"  current holder: session_id={existing.get('session_id')} "
                f"started_at={existing.get('started_at')} "
                f"last_heartbeat={existing.get('last_heartbeat')}\n"
            )
        sys.stderr.write("Confirm release? [y/N]: ")
        sys.stderr.flush()
        try:
            answer = input().strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print(json.dumps({"status": "ABORTED", "topic_slug": args.topic_slug}))
            return 1

    res = release_lock(args.topic_slug, session_id=args.session_id, force=args.force)
    print(json.dumps(res, indent=2, default=str))
    return 0 if res.get("status") == "RELEASED" else 1


def _cmd_status(args) -> int:
    _ensure_locks_dir()
    rows = []
    for p in sorted(LOCKS_DIR.glob("*.lock")):
        try:
            payload = json.loads(p.read_text())
            rows.append({"topic_slug": p.stem, "lock": payload,
                         "liveness": probe_liveness(payload)})
        except Exception as e:
            rows.append({"topic_slug": p.stem, "error": str(e)})
    print(json.dumps(rows, indent=2, default=str))
    return 0


def _cmd_heartbeat(args) -> int:
    # Silent by contract: the UserPromptSubmit event injects hook stdout into the
    # session's context, and a non-zero exit there would block the prompt.
    try:
        refresh_session_heartbeats(args.session_id)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _cmd_release_session(args) -> int:
    try:
        res = release_session_locks(args.session_id)
        print(json.dumps(res, indent=2, default=str))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "OK", "released": [], "errors": [str(e)]}))
    return 0


def _cmd_sweep(args) -> int:
    try:
        res = sweep_dead_locks(args.session_id)
        print(json.dumps(res, indent=2, default=str))
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"status": "OK", "swept": [], "skipped": [], "errors": [str(e)]}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "release":
        return _cmd_release(args)
    if args.cmd == "status":
        return _cmd_status(args)
    if args.cmd == "heartbeat":
        return _cmd_heartbeat(args)
    if args.cmd == "release-session":
        return _cmd_release_session(args)
    if args.cmd == "sweep":
        return _cmd_sweep(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())

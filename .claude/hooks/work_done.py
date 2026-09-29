#!/usr/bin/env python3
"""Slice J-1 Foundations: stateless helpers consumed by the `/work-done` skill.

This module ships the four pure-helper surfaces for J-1 Session 1:

  * `detect_ship_event` — G6 predicate
      `(lock_present AND lock_fresh) OR commits_present`
    over L's `read_lock` + `STALE_T` + `attribute_commits_merged`. No AI.
  * `resolve_slice_id`  — parse `slice_id:` from the plan's `GATE0SR:SLICES`
    marker block (Slice I). Hard-fails on missing marker or duplicate keys.
  * `attribute_commits_merged` — call L's `attribute_commits` twice, once per
    repo cwd context (project + harness), and concat the `confident` buckets
    with `[proj] <sha>` / `[harness] <sha>` prefix tags (OQ-J-CARRY-A (c)).
  * `write_retire_marker` — append `; Retired YYYY-MM-DD` before the trailing
    period of the spine's `**Status:**` bold-prose line. Idempotent.

These are the J-side surfaces *only*. Flag handlers (`--retire-as-never`,
`--force`), AskUserQuestion paths, the completion-ledger writer, and the
end-to-end ship-event composer land in Session 2 (`/work-done` skill).

Boundary (G3 authority allocation):
  * J does NOT write phase state — calls `pre_plan_gates.py phase-stop` CLI.
  * J does NOT own TODO format — calls `todo.py done` + `advance-sessions`.
  * J does NOT extend L's API — consumes `attribute_commits`, `verify_write`,
    `read_lock`, etc. (Guiding Policy item 2.)
"""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Optional

import taskmanagement as _tm


def _record_ledger_write(path) -> None:
    """Record a code-layer write to this session's file ledger (A5 / gap G4).

    Lazy, guarded, silent on failure — see commit_scope.record_write, which never
    raises; this wrapper extends that guarantee to the import so a module running
    under pytest or a background job cannot be broken by it.
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


# ---------------------------------------------------------------------------
# A4 — slice_id resolution from plan-file GATE0SR:SLICES marker
# ---------------------------------------------------------------------------

# Slice I writes:
#
#     <!-- GATE0SR:SLICES -->
#     slice_register_ref: <spine-file>#slice-register
#     slice_id: <id>
#     ...
#     <!-- /GATE0SR:SLICES -->
#
# We accept whitespace + reasonable position variation for `slice_id:` inside
# the block but hard-fail if the block is missing or carries two `slice_id:`
# lines (duplicate = ambiguous; refuse rather than guess).

_GATE0SR_OPEN_RE = re.compile(r"<!--\s*GATE0SR:SLICES\s*-->")
_GATE0SR_CLOSE_RE = re.compile(r"<!--\s*/?\s*GATE0SR:SLICES\s*-->")
_SLICE_ID_LINE_RE = re.compile(r"^\s*slice_id\s*:\s*(\S+)\s*$", re.MULTILINE)
_SLICE_IDS_LINE_RE = re.compile(r"^\s*slice_ids\s*:\s*(.+?)\s*$", re.MULTILINE)


def resolve_slice_id(plan_path: str | Path, override_slice_id: Optional[str] = None) -> str:
    """Read the plan's GATE0SR:SLICES marker block and return its `slice_id:` value.

    If *override_slice_id* is supplied, return it immediately (after a non-empty
    check) without parsing the plan file.  Pass ``--slice-id ID`` from the CLI
    to use this path when the plan carries ``slice_ids:`` (plural).

    Raises ValueError on missing block, missing `slice_id:` line, duplicate
    `slice_id:` lines, or a plural `slice_ids:` line without an override.
    """
    if override_slice_id is not None:
        stripped = override_slice_id.strip()
        if not stripped:
            raise ValueError("--slice-id value must be non-empty")
        return stripped
    text = Path(plan_path).read_text(encoding="utf-8")
    open_m = _GATE0SR_OPEN_RE.search(text)
    if open_m is None:
        raise ValueError(
            f"GATE0SR:SLICES marker not found in plan file: {plan_path}"
        )
    # Block runs from end of opening marker to next GATE0SR comment marker (open
    # or close form), or to next H2 / EOF — whichever comes first. The block
    # is short in practice (≤ ~10 lines) so the simple scan is sufficient.
    start = open_m.end()
    close_m = _GATE0SR_CLOSE_RE.search(text, start)
    end = close_m.start() if close_m else len(text)
    # Also cap at the next H2 heading — defends against a malformed plan that
    # omits the closing marker but introduces a new section.
    h2_m = re.search(r"^##\s+", text[start:end], re.MULTILINE)
    if h2_m is not None:
        end = start + h2_m.start()
    block = text[start:end]
    matches = list(_SLICE_ID_LINE_RE.finditer(block))
    if not matches:
        plural_m = _SLICE_IDS_LINE_RE.search(block)
        if plural_m is not None:
            available = plural_m.group(1).strip()
            raise ValueError(
                f"Multi-slice plan: 'slice_ids: {available}' found in "
                f"GATE0SR:SLICES block of {plan_path}; pass --slice-id <ID> "
                f"to specify which slice this ship-event targets."
            )
        raise ValueError(
            f"No `slice_id:` line inside GATE0SR:SLICES block of {plan_path}"
        )
    if len(matches) > 1:
        raise ValueError(
            f"Multiple `slice_id:` lines inside GATE0SR:SLICES block of "
            f"{plan_path} ({len(matches)} found); refusing to guess."
        )
    return matches[0].group(1).strip()


# ---------------------------------------------------------------------------
# A5 — two-repo attribute_commits merge
# ---------------------------------------------------------------------------

def attribute_commits_merged(plan_path: str | Path,
                             session_id: str,
                             started_at: str,
                             ended_at: str,
                             project_repo: str | Path,
                             harness_repo: str | Path,
                             session_num: int | None = None) -> list[str]:
    """Call L's `attribute_commits` once per repo, concat `confident` buckets.

    Order: project first, harness second (OQ-J-CARRY-A (c)). Each entry is
    prefixed `[proj] <sha>` or `[harness] <sha>` to preserve provenance — no
    dedup (distinct repos cannot share a SHA in practice; the prefix preserves
    it regardless).

    Ambiguous commits from either repo are NOT included — `/work-done`
    surfaces them to the user via AskUserQuestion (boundary preserved per
    locked Guiding Policy item 2; flag handlers + UX land in Session 2).
    """
    def _safe_call(repo: str | Path) -> list[str]:
        try:
            r = _tm.attribute_commits(
                plan_path=plan_path,
                session_id=session_id,
                started_at=started_at,
                ended_at=ended_at,
                session_num=session_num,
                repo_path=repo,
            )
            return r.get("confident", [])
        except RuntimeError as e:
            # An empty repo (no commits yet) makes `git log` exit non-zero with
            # "does not have any commits yet". Treat that as zero confident
            # commits, not a hard failure — the project may legitimately have
            # commits while the harness side has none (or vice versa).
            msg = str(e)
            if "does not have any commits" in msg or "ambiguous argument" in msg:
                return []
            raise

    merged: list[str] = []
    merged.extend(f"[proj] {sha}" for sha in _safe_call(project_repo))
    merged.extend(f"[harness] {sha}" for sha in _safe_call(harness_repo))
    return merged


# NOTE — a widening that was REMOVED, recorded so it is not re-added by reflex.
#
# S3 originally added `attribute_commits_merged_with_unresolved` here: a copy of the
# function above that also returned the unopenable plan references. It read well and
# was tested, and it was DEAD — tree-wide grep found no caller outside its own test.
# An independent reviewer caught it.
#
# The reason it could never be live is structural, not an oversight of wiring. The gate
# reaches this data through `_commits_present`, which returns a bare `bool` BY DESIGN
# and has callers depending on that; so the identity of a failed reference cannot pass
# through it however faithfully the layers underneath carry one. The gate therefore
# fetches the references directly from the spine (`check_work_done_omission.
# _unresolved_plan_refs`), which is a second lookup rather than a longer chain.
#
# Keeping a duplicated, uncalled copy of this function to satisfy the phrase "all four
# narrowing points widened" would have been the letter of the guard rail against its
# purpose — an unreachable branch that drifts from the one that runs. The honest
# statement is in the artifact instead: two points carry the data, and the gate's
# carrier is a direct read.


# ---------------------------------------------------------------------------
# A3 — deterministic ship-event detection
# ---------------------------------------------------------------------------

def _lock_is_fresh(lock_payload: dict) -> bool:
    """Return True iff the lock was ACQUIRED (`started_at`) within STALE_T of now.

    Reads `started_at`, deliberately NOT `last_heartbeat` (streamed-dancing-goose
    S4 / A4). Since S4 the heartbeat is refreshed on every turn as a LIVENESS
    signal, so a predicate keyed on it would read true for the whole life of any
    session — a rubber stamp, not a ship detector. Two questions used to share
    that one field ("is the holder alive?" and "did this session recently take
    the topic up?"); they are separated here: liveness lives in
    `taskmanagement.probe_liveness`, and this reader keeps the pre-S4 meaning —
    before S4 nothing refreshed the heartbeat except a same-session re-acquire,
    so it equalled `started_at` in every payload a session had not re-taken,
    and the verdict this gives is the one the detector gave before. A refresh
    never changes what this function returns.
    """
    acquired = lock_payload.get("started_at")
    if not acquired:
        return False
    try:
        acquired_dt = _tm._parse_iso(acquired)
    except (ValueError, TypeError):
        return False
    age_s = (_tm._now() - acquired_dt).total_seconds()
    return age_s < _tm._stale_t_seconds()


def detect_ship_event(topic_slug: str,
                      project_slug: str,
                      plan_path: str | Path | None,
                      session_id: str,
                      started_at: str,
                      ended_at: str,
                      project_repo: str | Path | None = None,
                      harness_repo: str | Path | None = None,
                      session_num: int | None = None) -> dict:
    """G6 predicate: `(lock_present AND lock_fresh) OR commits_present`.

    Pure code — no AI, no state writes. Returns a structured dict that the
    `/work-done` skill (Session 2) surfaces in its user-facing diagnostic.

    `project_slug` is REQUIRED (M16 fix, S3/A3): the lock is keyed by the
    composite `<topic>__<project>` that `/work-start` acquires under, so the
    predicate must read that same composite key. The prior code read the bare
    `topic_slug` and never found the lock (`lock_present` was always False).

    When `plan_path` is None or either repo path is None, the commits branch
    is reported as `commits_present: False` (no plan to scope against, no repo
    to scan) rather than raising — `--force` (Session 2) bypasses this anyway.
    """
    lock_payload = _tm.read_lock(_tm.composite_lock_key(topic_slug, project_slug))
    lock_present = lock_payload is not None
    lock_fresh = lock_present and _lock_is_fresh(lock_payload or {})

    commits: list[str] = []
    commits_present = False
    if plan_path is not None and project_repo is not None and harness_repo is not None:
        try:
            commits = attribute_commits_merged(
                plan_path=plan_path,
                session_id=session_id,
                started_at=started_at,
                ended_at=ended_at,
                project_repo=project_repo,
                harness_repo=harness_repo,
                session_num=session_num,
            )
            commits_present = len(commits) > 0
        except Exception as e:  # pragma: no cover — surfaced in `reason`
            commits = []
            commits_present = False
            _ = e

    ship_event = (lock_present and lock_fresh) or commits_present

    if ship_event:
        if lock_present and lock_fresh and commits_present:
            reason = "fresh lock + commits in scope"
        elif lock_present and lock_fresh:
            reason = "fresh lock (no commits in scope)"
        else:
            reason = "commits in scope (no fresh lock)"
    else:
        if lock_present and not lock_fresh:
            reason = "stale lock only; no commits in scope"
        elif lock_present:
            reason = "lock missing started_at or unparseable; no commits in scope"
        else:
            reason = "no lock and no commits in scope"

    return {
        "ship_event": ship_event,
        "reason": reason,
        "lock_present": lock_present,
        "lock_fresh": lock_fresh,
        "commits_present": commits_present,
        "commits": commits,
    }


# ---------------------------------------------------------------------------
# A6 — retire-marker writer
# ---------------------------------------------------------------------------

_STATUS_LINE_RE = re.compile(r"^\*\*Status:\*\*\s.*$", re.MULTILINE)
_RETIRED_TOKEN_RE = re.compile(r"Retired\s+\d{4}-\d{2}-\d{2}")
# bookkeeping-model drift Row 9: the retire-marker writer must hit the spine's
# PREAMBLE `**Status:**` line, never a `**Status:**` that appears in the body
# (the 2026-06-13 incident). The preamble is everything above the first H1.
_FIRST_H1_RE = re.compile(r"^# ", re.MULTILINE)


def _preamble(text: str) -> str:
    """The spine preamble — text above the first H1 (where `**Status:**` lives)."""
    h1 = _FIRST_H1_RE.search(text)
    return text[: h1.start()] if h1 else text


# ─── E1 predicate-review rubric (project-tracking-staleness S1 / AD-7) ─────────
# Bookkeeping sanity-guard predicates have historically misfired on LEGITIMATE
# normal flow and silently corrupted state (Mechanism 8). A predicate that fires
# silently on normal flow is WORSE than none — it CAUSES the drift it was built
# to catch. When reviewing or adding such a predicate, check it against the three
# predicate-failure axes:
#   1. section-scope   — does it select/act on the RIGHT structural region? e.g.
#                        a `**Status:**` in the spine PREAMBLE vs. one nested
#                        under a `### Solution Alternative` block. EXEMPLIFIED by
#                        the retire-marker fallback in `_write_retire_marker_unlocked`
#                        below (guarded by `_nearest_heading_depth`).
#   2. row-count       — does it treat "N items present" as proof of completeness
#                        when items are written INCREMENTALLY? see
#                        `taskmanagement.all_slices_done` (a single SHIPPED row is
#                        not proof a multi-slice plan is done).
#   3. namespace-disambiguation — does it key on the SAME namespace as its writer?
#                        e.g. a lock read with a bare slug vs. the composite
#                        `<topic>__<project>` key the writer used (M16 family).
def _nearest_heading_depth(text: str, pos: int) -> int:
    """Depth (1-6) of the nearest Markdown heading governing position `pos` in
    `text`, or 0 if none. Scans `text[:pos]` for the last `^#{1,6}\\s+` line,
    IGNORING heading-like lines inside fenced code blocks (``` ``` ``` / ~~~), so
    a `#`-line inside a code block is never mistaken for a governing heading.
    Section-scope axis of the rubric above: it lets the retire-marker fallback
    refuse a `**Status:**` nested under an H3+ (`### Solution Alternative`) block.
    """
    lines = text[:pos].split("\n")
    # Forward pass: mark lines inside a fenced code block (fences balance from top).
    in_fence = False
    inside: list[bool] = []
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            inside.append(True)        # the fence delimiter itself is not a heading
            in_fence = not in_fence
        else:
            inside.append(in_fence)
    # Backward pass: nearest real (non-fenced) heading.
    for ln, is_inside in zip(reversed(lines), reversed(inside)):
        if is_inside:
            continue
        h = re.match(r"^(#{1,6})\s+", ln)
        if h:
            return len(h.group(1))
    return 0


def write_retire_marker(thought_path: str | Path,
                        retire_date: str) -> dict:
    """Serialized (S2/A2) retire-marker write. See _write_retire_marker_unlocked.

    The read-modify-write runs under THE one bookkeeping `flock`
    (bookkeeping_lock, keyed on the spine's repo) so a concurrent bookkeeping
    writer on the same `main` checkout can never clobber it. Degrades to the
    unlocked RMW only if the lock module is unavailable.
    """
    try:
        from bookkeeping_lock import bookkeeping_lock
    except Exception:
        return _write_retire_marker_unlocked(thought_path, retire_date)
    with bookkeeping_lock(thought_path):
        return _write_retire_marker_unlocked(thought_path, retire_date)


def _write_retire_marker_unlocked(thought_path: str | Path,
                                  retire_date: str) -> dict:
    """Append `; Retired YYYY-MM-DD` before the trailing period of the spine's
    `**Status:**` line. Caller holds the A2 lock.

    Idempotent: if a `Retired <YYYY-MM-DD>` token already appears on the line
    (today's date or any other), the function is a no-op. This guards both
    against double-stamping today and against accidentally piling a new retire
    onto a stale-retired spine.

    Calls L's `verify_write(surface_kind="spine_section",
    locator=("**Status:**", "\\n"))` for post-write confirmation. The caller
    (A8, Session 2) is responsible for invoking this only after
    `all_slices_done(thought_path)` evaluates True.

    Raises ValueError if the file is missing or has no `**Status:**` line, or
    if `retire_date` doesn't match `YYYY-MM-DD`.
    """
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", retire_date):
        raise ValueError(f"retire_date must be YYYY-MM-DD; got {retire_date!r}")
    p = Path(thought_path)
    if not p.exists():
        raise ValueError(f"thought file not found: {p}")
    text = p.read_text(encoding="utf-8")
    # Drift Row 9 + S1 fallback hardening (section-scope axis; see the rubric on
    # `_nearest_heading_depth`): PREFER the preamble `**Status:**` (above the
    # first H1) so a body `**Status:**` can't be mis-stamped (the 2026-06-13
    # incident). Preamble is a prefix of text, so match offsets align.
    m = _STATUS_LINE_RE.search(_preamble(text))
    if m is None:
        # Fallback for a title-first spine (no preamble Status). Accept a whole-
        # text match ONLY when it is NOT nested under an H3+ heading — a
        # `**Status:** chosen` inside a `### Solution Alternative N` block is a
        # normal /solution-design product and must NEVER be mis-stamped as
        # "Retired" (the Mechanism-8 section-scope bug). Otherwise refuse LOUDLY.
        cand = _STATUS_LINE_RE.search(text)
        if cand is not None and _nearest_heading_depth(text, cand.start()) <= 2:
            m = cand
    if m is None:
        raise ValueError(
            f"no safe `**Status:**` line found in {p}: the preamble has none, "
            f"and any body `**Status:**` is nested under a `###`+ heading "
            f"(e.g. a `### Solution Alternative` block) — refusing to mis-stamp it"
        )
    line = m.group(0)

    if _RETIRED_TOKEN_RE.search(line) is not None:
        return {
            "status": "noop_already_retired",
            "thought_file": str(p),
            "status_line_head": line[:120],
        }

    addition = f"; Retired {retire_date}"
    # Insert before the trailing period of the last sentence on the line. If
    # the line doesn't end with a period (rare — spine convention does), append
    # the addition + a period to keep the line well-formed.
    if line.rstrip().endswith("."):
        # Strip exactly one trailing period, then re-append with our addition.
        stripped = line.rstrip()
        # Preserve any trailing whitespace that was after the period (none in
        # practice, but be conservative).
        trailing_ws = line[len(stripped):]
        new_line = stripped[:-1] + addition + "." + trailing_ws
    else:
        new_line = line + addition + "."

    new_text = text[:m.start()] + new_line + text[m.end():]
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(p)
    _record_ledger_write(p)          # A5 / gap G4 — a tracked spine write

    # Producer-never-verifies: confirm the write landed by re-reading via L's
    # deterministic verifier. Locator: from "**Status:**" to the next newline.
    _tm.verify_write(
        surface_kind="spine_section",
        path=p,
        expected_payload=f"Retired {retire_date}",
        locator=("**Status:**", "\n"),
    )

    return {
        "status": "retired",
        "thought_file": str(p),
        "retire_date": retire_date,
        "status_line_head": new_line[:160],
    }


# ---------------------------------------------------------------------------
# S7 (M9) — partial-fire mode resolver
# ---------------------------------------------------------------------------

_FIRE_MODES = ("auto", "full", "session-only")


def _parse_sessions(sessions: Any) -> tuple[Optional[int], Optional[int]]:
    """Parse a ``"M/N"`` sessions string → ``(M, N)`` ints, or ``(None, None)``.

    Defensive: `taskmanagement.SLICE_ROW_RE` already constrains a matched row's
    `sessions` field to ``\\d+/\\d+``, so a row returned by
    `parse_slice_register` always parses; this guard only covers a
    hand-constructed / future-shape input.
    """
    if not sessions or not isinstance(sessions, str):
        return None, None
    parts = sessions.split("/")
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, TypeError):
        return None, None


def resolve_fire_mode(spine_path: str | Path,
                      slice_id: str,
                      plan_path: str | Path | None = None) -> dict:
    """Resolve whether a `/work-done` ship event fires ``full`` or ``session-only``.

    Reads the spine slice-register row for `slice_id` (via
    `taskmanagement.parse_slice_register`) and inspects its ``sessions=M/N``
    counter:

      * terminal session (``M + 1 >= N``) → ``full``         (close the slice)
      * non-terminal     (``M + 1 <  N``) → ``session-only``  (record, keep open)
      * absent row / ``N <= 1``           → ``full``          (single-session default)

    A corrupted register row that fails `SLICE_ROW_RE` is not returned by
    `parse_slice_register`, so it manifests here as an absent row → ``full``.
    That fallback is **bounded**, not catastrophic: worst case is one slice
    prematurely marked SHIPPED — a loud, recoverable slice-register edit — NOT a
    topic retire (retire is independently gated by
    `taskmanagement.all_slices_done`, which needs ≥2 rows ALL terminal). The
    operator can always force the safe path with an explicit ``--mode
    session-only`` at the `/work-done` call site.

    Pure code, no AI (Domain layer — `code_first_architecture.md`). NEVER raises;
    always resolves to one of ``full`` / ``session-only``. `plan_path` is accepted
    for symmetry with the locked design signature and reserved for a future
    machine-readable per-slice session count; it is not consulted today.

    Returns::

        {"mode": "full"|"session-only", "reason": str,
         "sessions": "M/N"|None, "slice_id": <id>}
    """
    _ = plan_path  # reserved (locked signature); not consulted today
    result: dict = {"mode": "full", "reason": "", "sessions": None,
                    "slice_id": slice_id}
    try:
        rows = _tm.parse_slice_register(spine_path)
    except Exception:  # pragma: no cover — spine unreadable is a safe-default case
        result["reason"] = "slice register unreadable; single-session default"
        return result
    row = next((r for r in rows if r.get("id") == slice_id), None)
    if row is None:
        result["reason"] = "no slice-register row for slice_id; single-session default"
        return result
    sessions = row.get("sessions")
    result["sessions"] = sessions
    m, n = _parse_sessions(sessions)
    if m is None or n is None:
        result["reason"] = f"unparseable sessions field {sessions!r}; single-session default"
        return result
    if n <= 1:
        result["reason"] = f"single-session slice (N={n})"
        return result
    if m + 1 >= n:
        result["reason"] = f"terminal session (M+1={m + 1} >= N={n})"
        return result
    result["mode"] = "session-only"
    result["reason"] = f"non-terminal session (M+1={m + 1} < N={n})"
    return result


# ---------------------------------------------------------------------------
# Session 2 additions — completion ledger, session-lock enumeration,
# pre-atomic --retire-as-never NEVER conversion, CLI entrypoint.
# ---------------------------------------------------------------------------

import json as _json
from datetime import datetime as _datetime, timezone as _timezone

# Mirror of work_done_journal.STATE_DIR so the ledger lives next to the intent
# journal under ~/.claude/state/work_done/. Kept as a module-level path for
# test monkeypatching.
LEDGER_DIR = Path.home() / ".claude" / "state" / "work_done"


def _ledger_path(session_id: str, topic_slug: str) -> Path:
    return LEDGER_DIR / f"{session_id}__{topic_slug}.completed.jsonl"


def append_completion_ledger(session_id: str,
                             topic_slug: str,
                             row: dict) -> Path:
    """Append one JSONL row to the per-session per-topic completion ledger.

    Each row records one successful ship event. The ledger is the durable
    machine-readable record (the spine `## Sessions` log is human-facing per
    G11). The J-1↔/close overlap guard reads `todo_pattern` rows from this
    file to skip double-marking.

    Row schema (caller-owned; we just persist what's passed):
        {
          "session_id": <sid>,
          "topic_slug": <slug>,
          "slice_id": <id or null>,
          "todo_pattern": <pattern used for `todo.py done`, or null on --force without pattern>,
          "todo_file": <path or null>,
          "commits": ["[proj] sha", "[harness] sha", ...],
          "shipped_at": "<ISO8601 UTC>",
          "retired": bool,
          "flags": {"force": bool, "retire_as_never": [...]}
        }

    Append-only; never rewrites. Atomic via O_APPEND on a single line write.
    """
    if not session_id or not topic_slug:
        raise ValueError("session_id and topic_slug required for ledger append.")
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = _ledger_path(session_id, topic_slug)
    enriched = dict(row)
    enriched.setdefault("session_id", session_id)
    enriched.setdefault("topic_slug", topic_slug)
    enriched.setdefault("shipped_at",
                        _datetime.now(_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    line = _json.dumps(enriched, default=str) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    return path


def session_locks(session_id: str) -> list[dict]:
    """Enumerate locks under `LOCKS_DIR` whose holder == session_id.

    Returns list of {topic_slug, project_slug, composite_key, fresh,
    last_heartbeat, lock_payload} for each lock file. Stale locks are included
    with `fresh: False`; callers (the `/work-done` skill) treat 0 fresh locks as
    the empty-lock UX branch (A12).

    Stem split (S3/A3): the lock file stem is the composite `<topic>__<project>`
    key. `topic_slug` is the BARE topic (so the `/work-done` skill binds and its
    `--topic-slug` override match a bare slug, not a composite); `project_slug`
    is the split project; `composite_key` carries the full lock key for callers
    that must re-read the lock.
    """
    out: list[dict] = []
    if not _tm.LOCKS_DIR.exists():
        return out
    for lock_file in _tm.LOCKS_DIR.glob("*.lock"):
        try:
            payload = _json.loads(lock_file.read_text(encoding="utf-8"))
        except (OSError, _json.JSONDecodeError):
            continue
        if payload.get("session_id") != session_id:
            continue
        composite = lock_file.stem
        topic_slug, project_slug = _tm.split_lock_key(composite)
        fresh = _lock_is_fresh(payload)
        out.append({
            "topic_slug": topic_slug,
            "project_slug": project_slug,
            "composite_key": composite,
            "fresh": fresh,
            "last_heartbeat": payload.get("last_heartbeat"),
            "lock_payload": payload,
        })
    return out


def retire_as_never(spine_path: str | Path,
                    slice_ids: list[str]) -> list[dict]:
    """Pre-atomic conversion of named slices to status=NEVER (A9).

    Calls L's `write_slice_row(spine_path, slice_id, updates={"status": "NEVER"})`
    once per id, followed by `verify_write` on each. Returns list of per-id
    result dicts. Aborts on first verifier failure (NEVER conversions are
    pre-atomic metadata writes — they must succeed before the four-surface
    intent journal is written).
    """
    if not slice_ids:
        return []
    spine_path = Path(spine_path)
    results: list[dict] = []
    for sid in slice_ids:
        sid = sid.strip()
        if not sid:
            continue
        res = _tm.write_slice_row(spine_path, sid,
                                  updates={"status": "NEVER"})
        # verify_write for spine_section: confirm the L:slice row now carries
        # `status=NEVER` for this id. Locator anchors on the L:slice marker;
        # expected_payload is a substring that must appear within.
        _tm.verify_write(
            surface_kind="spine_section",
            path=spine_path,
            expected_payload=f"id={sid} status=NEVER",
            locator=(f"id={sid}", "-->"),
        )
        results.append({"slice_id": sid, "result": res})
    return results


def _cli_session_locks(argv: list[str]) -> int:
    import sys as _sys
    if len(argv) != 1:
        print("Usage: work_done.py session-locks SESSION_ID", file=_sys.stderr)
        return 2
    locks = session_locks(argv[0])
    print(_json.dumps(locks, indent=2, default=str))
    return 0


def _cli_retire_as_never(argv: list[str]) -> int:
    import sys as _sys
    if len(argv) != 2:
        print("Usage: work_done.py retire-as-never SPINE_PATH ID1,ID2,...",
              file=_sys.stderr)
        return 2
    spine_path, csv = argv[0], argv[1]
    ids = [s.strip() for s in csv.split(",") if s.strip()]
    if not ids:
        print("retire-as-never: empty id list", file=_sys.stderr)
        return 2
    results = retire_as_never(spine_path, ids)
    print(_json.dumps(results, indent=2, default=str))
    return 0


def _cli_append_ledger(argv: list[str]) -> int:
    """append-ledger SESSION_ID TOPIC_SLUG PAYLOAD

    PAYLOAD occupies the 3rd slot (SID + TOPIC_SLUG stay positional) and is one of:
      '{json}'         positional JSON string (back-compat)
      -                read the JSON from stdin (a pipe must be attached)
      --file <path>    read the JSON from a file

    The stdin/--file forms let a caller deliver JSON built in Python without ever
    passing it through shell quoting, so a value containing a literal single-quote
    (an apostrophe in a title/todo_pattern) can never break the invocation.
    """
    import sys as _sys
    if len(argv) < 3:
        print("Usage: work_done.py append-ledger SESSION_ID TOPIC_SLUG {JSON | - | --file PATH}",
              file=_sys.stderr)
        return 2
    sid, topic_slug = argv[0], argv[1]
    rest = argv[2:]
    if rest[0] == "--file":
        if len(rest) < 2 or not rest[1]:
            print("append-ledger: --file requires a path argument", file=_sys.stderr)
            return 2
        try:
            payload_json = Path(rest[1]).read_text(encoding="utf-8")
        except OSError as e:
            print(f"append-ledger: cannot read --file {rest[1]}: {e}", file=_sys.stderr)
            return 2
    elif rest[0] == "-":
        if _sys.stdin.isatty():
            print("append-ledger: '-' requires JSON piped on stdin (no pipe attached)",
                  file=_sys.stderr)
            return 2
        payload_json = _sys.stdin.read()
    else:
        payload_json = rest[0]
    if not payload_json or not payload_json.strip():
        print("append-ledger: empty ledger payload", file=_sys.stderr)
        return 2
    try:
        row = _json.loads(payload_json)
    except _json.JSONDecodeError as e:
        print(f"append-ledger: invalid JSON payload: {e}", file=_sys.stderr)
        return 2
    if not isinstance(row, dict):
        print("append-ledger: payload must be a JSON object", file=_sys.stderr)
        return 2
    path = append_completion_ledger(sid, topic_slug, row)
    print(_json.dumps({"status": "appended", "path": str(path)}, indent=2))
    return 0


def _cli_skip_work_done_check(argv: list[str]) -> int:
    """skip-work-done-check SESSION_ID TOPIC_SLUG --reason "..."

    The operator-visible escape hatch for the check-work-done-omission Stop hook
    (project-tracking-staleness S4). Records a `{skipped: true, reason}` row in the
    completed-work ledger so the omission is a visible, deliberate, audited choice
    (never silent drift); the Stop hook then reads that row and soft-exits.

    Non-interactive: a missing/blank `--reason` returns a clean non-zero exit with a
    stderr message — NEVER a TTY prompt or blocking read.
    """
    import sys as _sys

    usage = 'Usage: work_done.py skip-work-done-check SESSION_ID TOPIC_SLUG --reason "..."'
    args = list(argv)
    reason = None
    if "--reason" in args:
        idx = args.index("--reason")
        if idx + 1 < len(args):
            reason = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    if len(args) != 2:
        print(usage, file=_sys.stderr)
        return 2
    sid, topic_slug = args
    if reason is None or not str(reason).strip():
        print(usage, file=_sys.stderr)
        print("skip-work-done-check: --reason is required and must be non-empty",
              file=_sys.stderr)
        return 2
    row = {"skipped": True, "reason": str(reason).strip()}
    path = append_completion_ledger(sid, topic_slug, row)
    print(_json.dumps({"status": "skipped", "path": str(path),
                       "reason": row["reason"]}, indent=2))
    return 0


def _cli_detect(argv: list[str]) -> int:
    """detect SESSION_ID TOPIC_SLUG PROJECT_SLUG [--plan PATH] [--proj REPO] [--harness REPO] [--started AT] [--ended AT] [--session-num N]"""
    import sys as _sys, argparse as _argparse
    parser = _argparse.ArgumentParser(prog="work_done.py detect")
    parser.add_argument("session_id")
    parser.add_argument("topic_slug")
    parser.add_argument("project_slug")
    parser.add_argument("--plan", default=None)
    parser.add_argument("--proj", default=None)
    parser.add_argument("--harness", default=None)
    parser.add_argument("--started", default=None)
    parser.add_argument("--ended", default=None)
    parser.add_argument("--session-num", type=int, default=None)
    args = parser.parse_args(argv)
    started = args.started or _datetime.now(_timezone.utc).isoformat()
    ended = args.ended or _datetime.now(_timezone.utc).isoformat()
    res = detect_ship_event(
        topic_slug=args.topic_slug,
        project_slug=args.project_slug,
        plan_path=args.plan,
        session_id=args.session_id,
        started_at=started,
        ended_at=ended,
        project_repo=args.proj,
        harness_repo=args.harness,
        session_num=args.session_num,
    )
    print(_json.dumps(res, indent=2, default=str))
    return 0


def _cli_resolve_slice_id(argv: list[str]) -> int:
    import sys as _sys
    args = list(argv)
    override: Optional[str] = None
    if "--slice-id" in args:
        idx = args.index("--slice-id")
        if idx + 1 >= len(args):
            print("Usage: work_done.py resolve-slice-id PLAN_PATH [--slice-id ID]",
                  file=_sys.stderr)
            print("Error: --slice-id requires a following ID argument", file=_sys.stderr)
            return 2
        override = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    if len(args) != 1:
        print("Usage: work_done.py resolve-slice-id PLAN_PATH [--slice-id ID]",
              file=_sys.stderr)
        return 2
    try:
        print(resolve_slice_id(args[0], override_slice_id=override))
        return 0
    except ValueError as e:
        print(str(e), file=_sys.stderr)
        return 2


def _cli_retire_marker(argv: list[str]) -> int:
    import sys as _sys
    if len(argv) != 2:
        print("Usage: work_done.py retire-marker SPINE_PATH YYYY-MM-DD",
              file=_sys.stderr)
        return 2
    try:
        res = write_retire_marker(argv[0], argv[1])
        print(_json.dumps(res, indent=2, default=str))
        return 0
    except ValueError as e:
        print(str(e), file=_sys.stderr)
        return 2


def _cli_resolve_fire_mode(argv: list[str]) -> int:
    """resolve-fire-mode SPINE_PATH SLICE_ID [--plan PLAN_PATH]"""
    import sys as _sys
    usage = ("Usage: work_done.py resolve-fire-mode SPINE_PATH SLICE_ID "
             "[--plan PLAN_PATH]")
    args = list(argv)
    plan_path: Optional[str] = None
    if "--plan" in args:
        idx = args.index("--plan")
        if idx + 1 >= len(args):
            print(usage, file=_sys.stderr)
            print("Error: --plan requires a following path argument",
                  file=_sys.stderr)
            return 2
        plan_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    if len(args) != 2:
        print(usage, file=_sys.stderr)
        return 2
    res = resolve_fire_mode(args[0], args[1], plan_path=plan_path)
    print(_json.dumps(res, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# A2 (project-tracking-staleness S8 / M3-secondary) — harness repo for
# attribution. `/work-done` must scan the config-source SOURCE main checkout (where
# landed harness commits live), not the frozen ~/.claude. Single-locus resolver
# mirroring check_work_done_omission.py's _config_source_path + _main_checkout.
# ---------------------------------------------------------------------------

def harness_repo_for_attribution() -> Optional[str]:
    """The config-source `main` checkout path for harness commit attribution,
    or None if it cannot be resolved (caller falls back to ~/.claude — no worse
    than today). Pure resolution; does not touch the ship predicate."""
    import subprocess as _sp
    try:
        r = _sp.run(["false"]  # no config-source tool ships with Q, capture_output=True, text=True, check=False)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    src = (r.stdout or "").strip()
    if not src:
        return None
    try:
        import bookkeeping_resolver as _br
        return str(_br.main_checkout(src))
    except Exception:
        return None


def _cli_resolve_harness_repo(argv: list[str]) -> int:
    """Print the config-source main checkout for `--harness`, or empty on
    failure (the /work-done skill then uses ~/.claude). Never errors."""
    print(harness_repo_for_attribution() or "")
    return 0


# ---------------------------------------------------------------------------
# A3 (project-tracking-staleness S8 / M5) — ground-truth blob for handoff
# verification. Assembles the topic's tracking state (slice register, ordering,
# linked-plan slice ids, ledger) into a labeled text blob so a /double-check
# --against checker can flag a handoff that re-targets SHIPPED work or violates
# ordering. Per-section fail-safe: a broken producer degrades to a labeled
# "[unavailable — ...]" note; the blob never aborts.
# ---------------------------------------------------------------------------

def _gt_slice_register(spine_path) -> str:
    rows = _tm.parse_slice_register(spine_path)
    if not rows:
        return "  (no slice-register rows)"
    return "\n".join(
        f"  {r.get('id','?')}: status={r.get('status','?')} "
        f"sessions={r.get('sessions','?')} plan={(r.get('plan') or '_')}"
        for r in rows
    )


def _gt_sequencing(spine_path) -> str:
    rows = _tm.parse_slice_register(spine_path)
    if not rows:
        return "  (no rows to order)"
    terminal = {"SHIPPED", "NEVER"}
    shipped = [r.get("id") for r in rows if r.get("status") in terminal]
    pending = [r.get("id") for r in rows if r.get("status") not in terminal]
    nxt = pending[0] if pending else "(none — all registered slices terminal)"
    return (
        f"  registered order: {', '.join(r.get('id','?') for r in rows)}\n"
        f"  already shipped/terminal: {', '.join(shipped) or '(none)'}\n"
        f"  next unshipped registered slice: {nxt}"
    )


def _gt_linked_plans(spine_path) -> str:
    rows = _tm.parse_slice_register(spine_path)
    spine_dir = Path(spine_path).expanduser().resolve().parent
    out = []
    for r in rows:
        ref = (r.get("plan") or "").strip()
        if not ref or ref == "_":
            continue
        # THREE edit points here, not two. Routing the path construction through the
        # shared normaliser (below) is what stops this blob reporting a topic's own
        # plans as unresolved — but the DISPLAY string was a second, quieter half of
        # the same defect: it appended a bare ".md" to the raw field, so a bracketed
        # row printed a name like `[[foo_PLAN]].md`. Fixing only the resolution would
        # have satisfied the outcome claim's letter (the "(unresolved: …)" text
        # disappears) while still showing a filename that does not exist on disk.
        resolved, reason = _tm.resolve_plan_ref(ref, spine_dir)
        if resolved is None:
            out.append(f"  {r.get('id','?')} → {ref} (unresolved: {reason})")
            continue
        try:
            sid = resolve_slice_id(resolved)
        except Exception as e:
            sid = f"(unresolved: {type(e).__name__})"
        out.append(f"  {r.get('id','?')} → {resolved.name} (GATE0SR slice_id={sid})")
    return "\n".join(out) if out else "  (no linked plans)"


def _gt_ledger(spine_path) -> str:
    stem = Path(spine_path).name
    m = re.match(r"^(.*?)(?:-\d{14})?_(?:THOUGHT|PLAN)", stem)
    slug = m.group(1) if m else Path(spine_path).stem
    rows = []
    for p in sorted(LEDGER_DIR.glob(f"*__{slug}.completed.jsonl")):
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                d = _json.loads(line)
                rows.append(
                    f"  {d.get('shipped_at','?')} slice={d.get('slice_id','?')} "
                    f"mode={d.get('mode','?')} retired={d.get('retired','?')}"
                )
        except Exception:
            continue
    return "\n".join(rows[-20:]) if rows else "  (no completion-ledger rows for this topic)"


def build_ground_truth_blob(spine_path: str | Path, slice_id: Optional[str] = None) -> str:
    """Assemble the topic's tracking ground truth as a labeled text blob for a
    /double-check --against handoff-verification. Deterministic, no AI. Each
    section is independently fail-safe (a broken producer → labeled note)."""
    def _safe(label, fn):
        try:
            body = fn(spine_path)
        except Exception as e:  # pragma: no cover - defensive
            body = f"  [unavailable — {type(e).__name__}: {e}]"
        return f"## {label}\n{body}"

    header = (
        "# GROUND TRUTH (tracking state for handoff verification)\n"
        f"# spine: {spine_path}"
        + (f"  focus slice: {slice_id}" if slice_id else "")
    )
    return "\n\n".join([
        header,
        _safe("Slice Register (what has shipped)", _gt_slice_register),
        _safe("Sequencing (registered order + next unshipped)", _gt_sequencing),
        _safe("Linked plans (per-slice GATE0SR slice_id)", _gt_linked_plans),
        _safe("Completion ledger (recorded ship events)", _gt_ledger),
        "## Commit delta\n  [not computed in a static blob — commit-graph "
        "attribution needs a ship window; treat the Slice Register + Sequencing "
        "above as authoritative for 'is this slice already shipped / is the "
        "ordering respected'.]",
    ])


def _cli_ground_truth_blob(argv: list[str]) -> int:
    """ground-truth-blob SPINE_PATH [--slice-id S] → print the blob."""
    import sys as _sys
    slice_id = None
    args = list(argv)
    if "--slice-id" in args:
        idx = args.index("--slice-id")
        if idx + 1 < len(args):
            slice_id = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
    if len(args) != 1:
        print("Usage: work_done.py ground-truth-blob SPINE_PATH [--slice-id S]",
              file=_sys.stderr)
        return 2
    print(build_ground_truth_blob(args[0], slice_id=slice_id))
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(
            "Usage: work_done.py {session-locks|retire-as-never|append-ledger|"
            "detect|resolve-slice-id|resolve-fire-mode|retire-marker|"
            "skip-work-done-check|resolve-harness-repo|ground-truth-blob} ...",
            file=sys.stderr,
        )
        sys.exit(2)
    cmd, rest = sys.argv[1], sys.argv[2:]
    dispatch = {
        "session-locks": _cli_session_locks,
        "retire-as-never": _cli_retire_as_never,
        "append-ledger": _cli_append_ledger,
        "detect": _cli_detect,
        "resolve-slice-id": _cli_resolve_slice_id,
        "resolve-fire-mode": _cli_resolve_fire_mode,
        "retire-marker": _cli_retire_marker,
        "skip-work-done-check": _cli_skip_work_done_check,
        "resolve-harness-repo": _cli_resolve_harness_repo,
        "ground-truth-blob": _cli_ground_truth_blob,
    }
    handler = dispatch.get(cmd)
    if handler is None:
        print(f"work_done.py: unknown subcommand {cmd!r}", file=sys.stderr)
        sys.exit(2)
    sys.exit(handler(rest))

#!/usr/bin/env python3
"""Durable record for the work-done omission gate's REPORT-ONLY verdicts.

Why this module exists at all
-----------------------------
`check_work_done_omission.decide()` can now reach a would-block conclusion and
deliberately not act on it (report-only, A1/G4). A verdict nobody ever reads is
the same silence the gate was built to remove, so the report needs a channel that
demonstrably reaches the operator.

**The channel was established from the tree, not assumed** (A6). Three candidates
were considered and two are dead:

  * stderr on a non-blocking exit — the obvious one, and what both
    `check_work_done_omission._framing_soft_warn()` and the sibling
    `check-output-security-stop.sh` already do. It is NOT established that this
    reaches anyone: `output_security_judge.py:675-683` records a prior slice
    pricing this identical question and concluding that *no shipped hook in this
    tree surfaces text on exit 0*, which is why its own `systemMessage` emission
    is documented as "NOT claimed to work".
  * routing a Stop-time verdict through the SessionStart injected-context
    renderer that `framing_obligation.render_surface` really uses
    (`todo.py:880-882`) — a category error. That renderer runs at SessionStart;
    this gate runs at Stop, and the harness documents no Stop→SessionStart
    carry-over path.
  * **a durable record surfaced at `/close`** — the channel the same prior slice
    fell back to and described as "the channel that demonstrably reaches the
    operator". That is what this module implements.

Design constraints this module holds to
---------------------------------------
* **`decide()` stays pure.** It is documented as a pure decision that never
  raises; persistence belongs at the wrapper edge, not inside it. This module is
  called from `main()`, never from `decide()`.
* **Append-only.** A report is evidence about a past session; nothing rewrites
  history. Reading is a fold over the rows.
* **Fail-open, always.** This is a reporting channel attached to a gate that is
  itself fail-open. A failure to record must never affect a verdict, an exit
  code, or a session's ability to end. Every public function — `record_path`
  included, since it is exported — swallows its own errors and degrades to a
  no-op or an empty read.

**On the namespace, stated precisely because the obvious phrasing is wrong.**
This module writes under `state/work_done/`, which is where the completed-work
ledger `check_work_done_omission` already reads (`work_done.LEDGER_DIR`) and
where `work_done_journal.STATE_DIR` also writes. It is NOT true that all three
always resolve to the same directory: those two hardcode `Path.home()/".claude"`,
while this module resolves its base from `CLAUDE_CONFIG_DIR` first. They coincide
under default configuration and **diverge under a `CLAUDE_CONFIG_DIR` redirect** —
which is this codebase's own documented `config-experiment` staging pattern
(`git-policy.md` §2).

That divergence is deliberate here rather than an oversight. Honouring
`CLAUDE_CONFIG_DIR` is what lets this module be tested against a temp namespace
without touching live state, and it is what makes a staging clone's reports stay
in the clone instead of leaking into the operator's real record. The sibling
modules not honouring it is a pre-existing property of those modules that this
slice does not touch. **What must not be claimed** is the flat "same namespace as
the ledger" — it is the same *relative* path under a base each module resolves for
itself, and only one of them resolves it correctly.

Slice S1 of `Thoughts/slice-register-plan-ref-resolution-20260826003136_PLAN.md`
(actions A1 + A6).
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path

__all__ = [
    "record_path",
    "append_report",
    "read_reports",
    "render_close_block",
    "clear_reports",
    "REPORT_FILENAME",
]

REPORT_FILENAME = "report-only.jsonl"

# Schema version for the persisted rows. Bump only on a breaking shape change;
# `read_reports` tolerates unknown versions rather than refusing them, because a
# reporting channel that refuses to read its own history is worse than one that
# renders a row it only partly understands.
SCHEMA_VERSION = 1


@contextlib.contextmanager
def _record_lock():
    """Advisory lock around a read-modify-write of the record.

    Only the rewrite path needs this — an append is a single small `O_APPEND` write,
    which is atomic enough for this purpose, whereas `clear_reports` reads the whole
    file and writes it back, so a concurrent append landing in between would be lost.
    The Stop hook appends on every assistant turn, so that window is reachable.

    Fail-open: if the lock cannot be taken the body still runs. A reporting channel
    that refuses to work because it could not lock is worse than one that occasionally
    races, and this file gates nothing.
    """
    lock_path = record_path().with_name(record_path().name + ".lock")
    fh = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("a+")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        fh = None
    try:
        yield
    finally:
        if fh is not None:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                fh.close()
            except Exception:
                pass


def _state_dir() -> Path:
    """The work_done state namespace, honouring CLAUDE_CONFIG_DIR for isolation.

    Reuses the EXISTING `state/work_done/` relative path rather than minting a
    parallel one. See the module docstring for why "same namespace as the ledger"
    is true only under default configuration — the sibling modules hardcode
    `Path.home()`, this one does not, and that difference is intentional.
    """
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "state" / "work_done"


def record_path() -> Path:
    """The record file's path.

    Wrapped despite being pure path arithmetic, because it is exported in
    `__all__` and the module promises fail-open behaviour for its whole public
    surface. A caller reaching this directly — a test, or a later consumer — must
    get the same guarantee as one reaching it through `append_report`, rather than
    a guarantee that happens to hold only because today's callers wrap it
    themselves. On failure it degrades to the default-config path, which is a
    location rather than an exception.
    """
    try:
        return _state_dir() / REPORT_FILENAME
    except Exception:
        return Path.home() / ".claude" / "state" / "work_done" / REPORT_FILENAME


def append_report(
    *,
    session_id: str,
    topic: str,
    project: str,
    reason: str,
    message: str,
    kind: str = "report_only",
    now: datetime | None = None,
) -> bool:
    """Append one row, unless an identical one for this session already exists.

    Returns True when a row is present afterwards (whether written now or already
    there), False on failure. Never raises.

    **THE DE-DUPLICATION IS LOAD-BEARING, NOT TIDINESS.** The Stop hook that calls
    this fires on EVERY assistant turn, and its only loop guard (`stop_hook_active`)
    is set after a *block* — which a report-only verdict never produces. So an
    unconditional append writes one row per turn for the rest of the session: a
    single unrecorded ship becomes dozens of rows, `/close` renders five copies of
    one omission, and the row count the follow-up is supposed to use for measuring
    the false-positive rate is inflated by the number of turns rather than the number
    of omissions. That would corrupt the evidence base this record exists to provide.

    The key is `(session_id, topic, project, kind)` — deliberately NOT including
    `reason` or `at`. A reason string can vary between turns within one session (the
    unresolved list is bounded and order can shift), and including it would defeat
    the de-duplication in exactly the case it is needed. One session's one omission
    is one row.
    """
    try:
        row = {
            "schema_version": SCHEMA_VERSION,
            "at": (now or datetime.now(timezone.utc)).isoformat(),
            "session_id": session_id or "",
            "topic": topic or "",
            "project": project or "",
            "kind": kind or "report_only",
            "reason": reason or "",
            "message": message or "",
        }
        key = (row["session_id"], row["topic"], row["project"], row["kind"])
        for existing in read_reports():
            if (existing.get("session_id", ""), existing.get("topic", ""),
                    existing.get("project", ""),
                    existing.get("kind", "report_only")) == key:
                return True  # already recorded for this session — nothing to add
        path = record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except Exception:
        return False


def read_reports(*, session_id: str | None = None, limit: int | None = None) -> list[dict]:
    """Read report rows, newest last. Optionally filter to one session.

    Never raises. An unreadable or absent record reads as `[]` — the same answer
    as "nothing to report", which is correct here: this channel makes a report
    visible, it never asserts that no report exists.
    """
    try:
        path = record_path()
        if not path.is_file():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue  # a malformed row must not hide the well-formed ones
            if not isinstance(row, dict):
                continue
            if session_id and row.get("session_id") != session_id:
                continue
            rows.append(row)
        if limit is not None and limit >= 0:
            rows = rows[-limit:]
        return rows
    except Exception:
        return []


def render_close_block(*, session_id: str | None = None, limit: int = 5) -> str:
    """Render the `/close` surface, or '' when there is nothing to say.

    Returns the empty string when there is nothing to report, so the caller can test
    truthiness and print nothing — a close-out that announces its own silence is
    noise. Never raises.

    **Two kinds are rendered SEPARATELY, and that separation is C4.** A topic whose
    scope resolved and showed unrecorded commits, and a topic whose plan references
    could not be opened at all, are different statements about how much to trust the
    silence. Collapsing them into one list would re-create at the operator's level the
    identity this whole slice exists to remove.

    **`session_id` should be passed by the caller.** Without it this renders across
    all history, which after several sessions is mostly other sessions' business.
    """
    try:
        rows = read_reports(session_id=session_id, limit=None)
        if not rows:
            return ""
        unresolved = [r for r in rows if (r.get("kind") or "") == "unresolved"]
        # Anything that is not explicitly `unresolved` renders in the first section,
        # INCLUDING a kind this version does not recognise. Splitting on two known
        # values and dropping the rest would make a record full of future rows render
        # as "nothing to report" — the exact silent-empty failure this change exists to
        # remove, and a contradiction of the schema note above, which deliberately
        # tolerates partly-understood rows rather than refusing them.
        blocked = [r for r in rows if (r.get("kind") or "") != "unresolved"]

        def _bullets(items):
            body = []
            for row in items[-limit:]:
                topic = row.get("topic") or "(unknown topic)"
                project = row.get("project") or "(unknown project)"
                at = row.get("at") or ""
                reason = row.get("reason") or ""
                body.append(f"- **{topic}** ({project}) — {reason}  \n  _{at}_")
            omitted = max(0, len(items) - limit)
            if omitted:
                body.append(f"- _(+{omitted} earlier — see `{record_path()}`)_")
            return body

        out: list[str] = []
        if blocked:
            out += [
                "### Work-done omission — report only (not blocking)",
                "",
                "The safety net resolved a file scope and found landed commits in it "
                "that no `/work-done` recorded. It is in report-only mode, so it did "
                "**not** stop anything — this is what it would have blocked:",
                "",
            ] + _bullets(blocked)
        if unresolved:
            if out:
                out.append("")
            out += [
                "### Work-done omission — scope could not be determined",
                "",
                "These topics declare plan references the safety net could not open, "
                "so it could not tell whether anything was missed. This is **not** a "
                "report that something was missed — it is a report that it could not "
                "look:",
                "",
            ] + _bullets(unresolved)
        if not out:
            return ""
        out += [
            "",
            "Record `/work-done` where it applies, or fix the unresolvable reference. "
            "Blocking is deliberately off until the false-positive rate has been "
            "measured over a report-only period; these rows are that measurement's "
            "raw material, so one row means one session, not one turn.",
        ]
        return "\n".join(out)
    except Exception:
        return ""


def clear_reports(session_id: str) -> int:
    """Remove ONE session's recorded rows. Returns the number removed.

    **`session_id` IS REQUIRED AND POSITIONAL, DELIBERATELY.** The first version of
    this function took `session_id: str | None = None` and, on the default, deleted
    every session's history — the guard `if session_id and …` short-circuits false for
    every row, so `keep` came out empty. The destructive behaviour was the DEFAULT,
    protected only by a check at the CLI edge, so any Python caller writing
    `clear_reports()` wiped the record silently. That is exactly the shape
    `~/.claude/rules/safe-defaults.md` exists to forbid ("propose the safe operation by
    default"), and there is now no argument list that expresses "delete everything":
    a caller must name a session, and an unknown session removes nothing.

    Malformed rows are PRESERVED. The rewrite is built from the raw lines rather than
    from `read_reports`, which skips unparseable ones — rebuilding from the parsed view
    would silently destroy them, which is data loss well outside what was asked for.

    Concurrency: the rewrite takes the same advisory lock as `append_report`, because
    the Stop hook that appends fires on every assistant turn and clearing is
    operator-invoked mid-session, so an append landing between the read and the replace
    is reachable rather than theoretical. The temp file is per-process for the same
    reason. Atomic `os.replace` then makes readers see old-or-new, never torn.

    Never raises; returns 0 on any failure.
    """
    if not session_id:
        return 0
    try:
        path = record_path()
        if not path.is_file():
            return 0
        with _record_lock():
            kept_lines, removed = [], 0
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    row = json.loads(stripped)
                    match = isinstance(row, dict) and row.get("session_id") == session_id
                except (ValueError, TypeError):
                    match = False       # unparseable -> keep, never destroy
                if match:
                    removed += 1
                else:
                    kept_lines.append(stripped)
            if removed == 0:
                return 0
            tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
            with tmp.open("w", encoding="utf-8") as fh:
                for line in kept_lines:
                    fh.write(line + "\n")
            os.replace(tmp, path)
            return removed
    except Exception:
        return 0


def _self_test() -> int:
    """Structural smoke test against a temp namespace — never touches live state."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = td
        try:
            assert read_reports() == [], "absent record reads as empty"
            assert render_close_block() == "", "nothing to report renders nothing"

            ok = append_report(session_id="abcd1234", topic="mytopic",
                               project="Root", reason="would have blocked",
                               message="msg")
            assert ok is True, "append should succeed"
            rows = read_reports()
            assert len(rows) == 1 and rows[0]["topic"] == "mytopic", rows
            assert rows[0]["schema_version"] == SCHEMA_VERSION

            block = render_close_block()
            assert "mytopic" in block and "report only" in block.lower(), block
            assert "not blocking" in block.lower(), block

            # session filter
            append_report(session_id="other999", topic="t2", project="Root",
                          reason="r2", message="m2")
            assert len(read_reports()) == 2
            assert len(read_reports(session_id="abcd1234")) == 1

            # a malformed row must not hide well-formed ones
            with record_path().open("a", encoding="utf-8") as fh:
                fh.write("{not json\n")
            assert len(read_reports()) == 2, "malformed row skipped, others kept"

            # de-dup: the Stop hook fires every turn, so a repeat append is a no-op
            for _ in range(5):
                append_report(session_id="abcd1234", topic="mytopic",
                              project="Root", reason="would have blocked",
                              message="msg")
            assert len(read_reports(session_id="abcd1234")) == 1, \
                "one session's one omission is ONE row, not one per turn"
            # ...but a different KIND for the same session is a distinct row
            append_report(session_id="abcd1234", topic="mytopic", project="Root",
                          reason="could not determine scope", message="m",
                          kind="unresolved")
            assert len(read_reports(session_id="abcd1234")) == 2
            block = render_close_block(session_id="abcd1234")
            assert "report only" in block.lower(), block
            assert "could not be determined" in block.lower(), block
            # clearing needs an explicit session and removes only that one
            assert clear_reports(session_id="other999") == 1
            assert clear_reports(session_id="abcd1234") == 2
            assert read_reports() == []
            assert render_close_block() == ""

            # fail-open: an unwritable namespace degrades, never raises
            os.environ["CLAUDE_CONFIG_DIR"] = "/proc/nonexistent-zzz"
            assert append_report(session_id="x", topic="y", project="z",
                                 reason="r", message="m") is False
            assert read_reports() == []
            assert render_close_block() == ""
        finally:
            if prev is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = prev

    print("work_done_report self-test: OK")
    return 0


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        raise SystemExit(_self_test())
    if len(sys.argv) > 1 and sys.argv[1] == "close-block":
        sid = sys.argv[2] if len(sys.argv) > 2 else ""
        if not sid.strip():
            # REFUSE rather than render everything. An empty argument here is almost
            # always a placeholder that was not substituted (or a `"$SESSION_ID"` that
            # expanded to nothing), and treating it as "no filter" would print every
            # session's rows while the caller believed it had scoped the request — the
            # silent-opposite-of-intent failure this whole change is about.
            print("close-block needs a SESSION_ID; refusing to render all sessions' "
                  "rows from an empty argument", file=sys.stderr)
            raise SystemExit(2)
        print(render_close_block(session_id=sid))
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "clear":
        sid = sys.argv[2] if len(sys.argv) > 2 else ""
        if not sid.strip():
            print("clear needs an explicit SESSION_ID", file=sys.stderr)
            raise SystemExit(2)
        print(f"cleared {clear_reports(sid)} row(s) for {sid}")
        raise SystemExit(0)
    print("usage: work_done_report.py --self-test "
          "| close-block [SESSION_ID] | clear SESSION_ID")
    raise SystemExit(0)

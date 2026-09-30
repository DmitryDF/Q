#!/usr/bin/env python3
"""Framing obligations for auto-registered TODO lines (auto-registration S-C / A3).

A sibling of `dc_obligation.py` in SHAPE — seed / reconcile / fail-open — but with
two deliberate differences:

* **The store is the marker, not a JSON file.** `auto_register_topic` mints a
  tracking line carrying `[auto-registered]`; that marker IS the obligation
  record. There is no seed step and no new state directory, so the obligation
  cannot drift out of sync with the thing it describes.
* **Discharge is `todo.cmd_validate_framing`** — the IDENTICAL structural bar
  every manual `[Thought]` line passes, applied asynchronously rather than at
  mint time. On discharge the marker is STRIPPED, so a framed line drops off the
  obligation list permanently and is never re-flagged.

**This module ships the SOFT surface only.** It reports and it discharges; it
never blocks. Whether an undischarged obligation should escalate to a fail-closed
Stop (`exit 2`, the shape `check-dc-obligation-stop.sh` uses) is a probe-driven
decision that needs evidence about whether marked lines actually rot in practice
— evidence that cannot exist before the mint ships. Deferred deliberately
(auto-registration plan, residual R1); the marker-as-record makes that escalation
a purely additive later step.

**Fail-open everywhere.** A missing `TODO.md`, an unreadable one, or no marked
lines all yield an empty result — never an exception. This runs on the
SessionStart path, so a raise here would break every session open.

CLI:
    python3 framing_obligation.py scan [--todo-file PATH ...]
    python3 framing_obligation.py reconcile [--todo-file PATH ...] [--no-strip]
    python3 framing_obligation.py surface [--todo-file PATH ...]
    python3 framing_obligation.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MARKER = "[auto-registered]"

# The marker plus at most one following SPACE-LIKE character, so stripping leaves
# no double gap. `[^\S\r\n]` is "whitespace except CR/LF" — a plain `\s?` would
# swallow the line's own terminator when the marker is the last token on it,
# silently corrupting that line ending.
_MARKER_STRIP_RE = re.compile(re.escape(MARKER) + r"[^\S\r\n]?")

_OPEN_ITEM_PREFIX = "- [ ]"


def _validate_framing(body: str) -> bool:
    """True when `body` passes the same structural framing bar a manual
    `[Thought]` line passes. Fail-open: if the validator cannot be imported,
    report NOT framed — an obligation that cannot be checked stays outstanding
    (a soft surface, so this costs a warning, never a block)."""
    try:
        import todo as _todo
    except Exception:
        return False
    try:
        return _todo.cmd_validate_framing(body).get("status") == "pass"
    except Exception:
        return False


def scan(todo_path) -> list[dict]:
    """Marked open lines in one `TODO.md`.

    Returns `[{path, line_no, body, framed}]`, oldest-first by file order.
    Fail-open: a missing/unreadable file yields `[]`.
    """
    p = Path(todo_path)
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for i, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not stripped.startswith(_OPEN_ITEM_PREFIX) or MARKER not in stripped:
            continue
        body = stripped.split(_OPEN_ITEM_PREFIX, 1)[1].strip()
        out.append({"path": str(p), "line_no": i, "body": body,
                    "framed": _validate_framing(body)})
    return out


def _strip_marker_in_file(todo_path, line_nos) -> set[int]:
    """Remove the marker from the given 1-indexed lines.

    Returns the set of line numbers ACTUALLY stripped — the caller classifies
    discharge on that, never on intent, so a write that silently failed can
    never be reported as a discharge.

    Targeted line edit under the one bookkeeping lock, re-reading inside the
    lock so a concurrent writer's other edits are never clobbered. Line
    terminators are preserved (`splitlines(keepends=True)`), so a CRLF file is
    not silently rewritten to LF. ALL marker occurrences on a target line are
    removed, so a line carrying the marker twice cannot survive a "successful"
    strip and be re-flagged forever.
    """
    p = Path(todo_path)

    def _apply() -> set[int]:
        # `newline=""` on BOTH ends disables universal-newline translation, so a
        # CRLF file round-trips byte-for-byte. Reading with the default would
        # convert every `\r\n` to `\n` before `splitlines` ever saw it, and the
        # write would then silently re-terminate the whole file.
        try:
            with p.open("r", encoding="utf-8", newline="") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError):
            return set()
        lines = text.splitlines(keepends=True)   # terminators preserved
        changed: set[int] = set()
        for n in line_nos:
            if not (1 <= n <= len(lines)):
                continue
            if MARKER not in lines[n - 1]:
                continue        # re-read shows it already gone — nothing to do
            lines[n - 1] = _MARKER_STRIP_RE.sub("", lines[n - 1])  # ALL occurrences
            changed.add(n)
        if changed:
            tmp = p.with_suffix(p.suffix + ".fo-tmp")
            with tmp.open("w", encoding="utf-8", newline="") as fh:
                fh.write("".join(lines))
            tmp.replace(p)
        return changed

    def _guarded() -> set[int]:
        try:
            return _apply()
        except Exception:
            return set()

    try:
        from bookkeeping_lock import bookkeeping_lock
    except Exception:
        return _guarded()        # unlocked fallback is ALSO guarded
    try:
        with bookkeeping_lock(p):
            return _guarded()
    except Exception:
        return set()


def reconcile(todo_paths, *, strip: bool = True) -> dict:
    """Discharge what is now framed; report what is not.

    For every marked open line across `todo_paths`:
      * it NOW passes the framing bar  -> the marker is stripped (DISCHARGE), so
        the line drops off this list permanently and is never re-flagged;
      * it still fails                 -> it is reported as outstanding.

    A row counts as DISCHARGED only when the marker was actually removed. A
    framed line whose strip failed (read-only file, lost lock, …) is reported as
    still OUTSTANDING — the report never claims a discharge that did not happen.
    With `strip=False` the pass is read-only and a framed line is reported under
    `would_discharge` instead.

    Paths are de-duplicated by resolved path, so passing the same file twice
    cannot double-count its lines.

    Returns `{"scanned", "discharged": [...], "outstanding": [...],
    "would_discharge": [...]}`. Never raises: an unreadable file simply
    contributes nothing (fail-open).
    """
    discharged, outstanding, would, scanned = [], [], [], 0
    seen_paths: set[str] = set()
    for todo_path in todo_paths or []:
        try:
            key = str(Path(todo_path).resolve())
        except Exception:
            key = str(todo_path)
        if key in seen_paths:
            continue
        seen_paths.add(key)

        rows = scan(todo_path)
        scanned += len(rows)
        framed_nos = [r["line_no"] for r in rows if r["framed"]]
        stripped: set[int] = set()
        if framed_nos and strip:
            stripped = _strip_marker_in_file(todo_path, framed_nos)
        for r in rows:
            if not r["framed"]:
                outstanding.append(r)
            elif not strip:
                would.append(r)
            elif r["line_no"] in stripped:
                discharged.append(r)
            else:
                # framed, but the marker is still there — do NOT claim a discharge
                outstanding.append({**r, "strip_failed": True})
    return {"scanned": scanned, "discharged": discharged,
            "outstanding": outstanding, "would_discharge": would}


def render_surface(result: dict, *, limit: int = 5) -> str | None:
    """The ONE shared human-facing block, rendered identically at every surface
    that reports framing obligations. Returns None when nothing is outstanding,
    so a caller can simply skip an empty report."""
    outstanding = (result or {}).get("outstanding") or []
    if not outstanding:
        return None
    n = len(outstanding)
    head = (f"⚠ {n} auto-registered TODO line{'s' if n != 1 else ''} still "
            f"need{'' if n != 1 else 's'} framing "
            f"(Problem / Context / Guiding policy / Master plan). "
            f"They were created automatically at ship, so nothing is lost — "
            f"but they are not framed yet.")
    body = [f"    {Path(r['path']).name}:{r['line_no']} — {_snippet(r['body'])}"
            for r in outstanding[:limit]]
    if n > limit:
        body.append(f"    … and {n - limit} more")
    tail = ("  Frame each one in place; the marker is removed automatically "
            "once it passes.")
    return "\n".join([head, *body, tail])


def _snippet(text: str, max_len: int = 70) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    t = t.replace(MARKER, "").strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


def default_todo_paths(project_roots=None) -> list[str]:
    """Best-effort `TODO.md` set to reconcile when a caller names none. Purely
    deterministic: the passed roots, else nothing. Callers that know their
    project (the SessionStart scan, `/close`) pass their own paths."""
    out = []
    for root in project_roots or []:
        p = Path(root) / "TODO.md"
        if p.exists():
            out.append(str(p))
    return out


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cli(argv) -> int:
    ap = argparse.ArgumentParser(prog="framing_obligation.py")
    sub = ap.add_subparsers(dest="cmd", required=False)
    for name in ("scan", "reconcile", "surface"):
        s = sub.add_parser(name)
        s.add_argument("--todo-file", action="append", default=[])
        if name == "reconcile":
            s.add_argument("--no-strip", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.cmd == "scan":
        rows = [r for f in args.todo_file for r in scan(f)]
        print(json.dumps({"rows": rows, "count": len(rows)}, indent=2))
        return 0
    if args.cmd == "reconcile":
        res = reconcile(args.todo_file, strip=not args.no_strip)
        print(json.dumps(res, indent=2))
        return 0
    if args.cmd == "surface":
        res = reconcile(args.todo_file, strip=True)
        block = render_surface(res)
        if block:
            print(block)
        return 0
    ap.print_help()
    return 0


def _self_test() -> int:
    """Structural smoke test — isolated temp tree, never touches live state."""
    import tempfile
    import shutil
    tmp = Path(tempfile.mkdtemp(prefix="fo_selftest_"))
    try:
        t = tmp / "TODO.md"
        framed = ("- [ ] [Thought] [auto-registered] [a-20260101000000] **A** — "
                  "Problem: p. Context: c. Guiding policy: g. "
                  "Master plan: [[a-20260101000000_PLAN]].")
        unframed = ("- [ ] [Thought] [auto-registered] [b-20260101000000] **B** — "
                    "auto-registered at ship; framing incomplete. "
                    "Master plan: [[b-20260101000000_PLAN]].")
        t.write_text("# TODO\n\n## Now\n\n" + framed + "\n" + unframed + "\n")

        rows = scan(t)
        assert len(rows) == 2, rows
        assert rows[0]["framed"] is True, rows[0]
        assert rows[1]["framed"] is False, rows[1]

        res = reconcile([str(t)])
        assert len(res["discharged"]) == 1, res
        assert len(res["outstanding"]) == 1, res
        after = t.read_text()
        assert after.count(MARKER) == 1, "framed line's marker must be stripped"
        assert "[a-20260101000000]" in after, "the line itself must survive"

        block = render_surface(res)
        assert block and "still need" in block, block
        assert render_surface({"outstanding": []}) is None

        # fail-open: missing + unreadable files
        assert scan(tmp / "nope.md") == []
        assert reconcile([str(tmp / "nope.md")])["scanned"] == 0
        bad = tmp / "bad.md"
        bad.write_bytes(b"- [ ] [auto-registered] \xff\xfe\n")
        assert scan(bad) == []

        # idempotent: a second reconcile discharges nothing new
        res2 = reconcile([str(t)])
        assert res2["discharged"] == [], res2
        assert len(res2["outstanding"]) == 1, res2
        print("framing_obligation self-test: OK")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))

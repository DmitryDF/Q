#!/usr/bin/env python3
"""S4 — check-work-done-omission decision module (project-tracking-staleness Phase 2).

Pure decision function behind the `check-work-done-omission.sh` Stop hook. Given a
session_id, it decides whether the session should be BLOCKED at close (the operator
shipped work under an active tracked topic but did not record it with `/work-done`)
or allowed to PASS (soft-exit).

Design (Thoughts/project-tracking-staleness-<ts>_PLAN.md, Mode A / Alternative 1):

  * "Shipped" = LANDED to `main` (git-policy.md §11 — the verify-then-land merge is
    the ship event, not a topic-branch WIP commit). Each repo is therefore resolved
    to its `main` checkout via `bookkeeping_resolver.main_checkout`, so `git log`
    sees landed commits. The harness repo is the **config source** repo `main`
    (NOT the frozen ~/.claude/.git — git-policy.md §2).

  * The commit graph is the ship SIGNAL, keyed on `detect_ship_event.commits_present`
    (the OR-half of its predicate — M16 lock-key-safe). The lock is read only to
    supply the ship WINDOW start (`started_at`); when no lock is present the window
    is indeterminate and we soft-exit (fail-safe, never guess a window).

  * The assertion target is the side-effect `/work-done` writes: a bullet
    carrying the `work-done` writer identity (`<!-- annotate-session
    writer=work-done -->`, or the legacy bare tag) inside the current session's
    `### <date> session [sid:<sid8>]` block under `## Sessions` in the spine.
    The block is matched by the `[sid:<sid8>]` token INDEPENDENT of the heading's
    date (a session that ran `/work-done` before midnight and closes after must
    still count as recorded), and the section is located by the ONE shared
    locator (`pre_plan_gates._SESSIONS_HEADING_RE`, via `find_sessions_section`)
    — never by a whole-file search. Since S1 (streamed-dancing-goose) `/close`
    writes its OWN block under the same heading shape every session, so the
    header alone discriminates nothing; the gate keys on the marker only the
    shipping writer mints. A `/close`-authored block — metrics rows, or a
    `--annotate` bullet under `writer=close` — does NOT satisfy it. The spine is
    the `main`-owned `Thoughts/` file, sparse-excluded from worktrees, so it is
    read main-pinned via `bookkeeping_resolver` under `bookkeeping_lock`.

  * Block ONLY on:  commits_present  AND  no spine session-block  AND  active topic.
    Soft-exit (PASS) on: no active topic; already-recorded (spine block OR a
    `skipped:true` ledger row); no lock (window indeterminate); no in-window landed
    commits in scope. Where a plan carries no scope the current `attribute_commits`
    recognizes (`## Diff`; Implementation-Specifics recognition is M3/S8, unshipped),
    `commits_present` is False → soft-exit. S4 is confidence-gated: it never blocks
    unless it can confidently attribute landed work to the session.

  * Fail-open: `decide()` never raises to the caller for an internal error — any
    exception is caught, logged to stderr, and returned as PASS. A global hard-
    blocking Stop hook that misfires on its own bug would itself cause the drift it
    exists to catch (the S1 E1 predicate-review rubric).

Producer-never-verifies: this module only DECIDES; it never writes state.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

STATE_DIR = Path.home() / ".claude" / "state"
ACTIVE_JSON = STATE_DIR / "pre_plan_gates" / "_active.json"
TOPIC_STATE_DIR = STATE_DIR / "pre_plan_gates"

# The exact escape-hatch command the block message names (C4). Kept as a module
# constant so the behavioral test can assert the message contains it verbatim.
ESCAPE_HATCH_CMD = "python3 ${KIT_HOOKS_DIR}/work_done.py skip-work-done-check {sid} {topic} --reason \"...\""


def _pass(reason: str) -> dict:
    return {"block": False, "reason": reason, "message": None}


def _sid8(session_id: str) -> str:
    return (session_id or "")[:8]


def _active_topic(session_id: str):
    """(topic_slug, project_slug) for the session, or (None, None) if unbound.

    `_active.json` is a session-keyed dict; the entry carries `topic_slug` and
    `active_project` (verified against the live file shape + pre_plan_gates
    `_resolve_topic`).
    """
    if not ACTIVE_JSON.is_file():
        return None, None
    data = json.loads(ACTIVE_JSON.read_text(encoding="utf-8"))
    entry = data.get(session_id) or {}
    topic = entry.get("topic_slug") or None
    project = entry.get("active_project") or None
    return topic, project


def _topic_state(topic: str, project: str) -> dict | None:
    p = TOPIC_STATE_DIR / f"{topic}__{project}.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _skipped_ledger_row(session_id: str, topic: str) -> bool:
    """True if the completed-work ledger for this (sid, topic) records a skip."""
    import work_done  # sibling module

    path = work_done._ledger_path(session_id, topic)
    if not path.is_file():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("skipped") is True:
            return True
    return False


# `### <date> session [sid:<prefix>]` — match on the sid token, date-independent.
def _session_block_re(sid8: str) -> re.Pattern:
    return re.compile(
        r"^###\s+.*\bsession\s+\[sid:" + re.escape(sid8) + r"[^\]]*\]\s*$",
        re.MULTILINE,
    )


def _work_done_record_in(content: str, session_id: str) -> bool:
    """Pure predicate: does `content` (a spine) carry a `/work-done` record for
    this session — a bullet under the `work-done` writer identity inside the
    session's `[sid:<sid8>]` block, inside the `## Sessions` section?

    Section, block and identity are all read through `pre_plan_gates` (the ONE
    locator + the ONE marker grammar), so this gate cannot drift from the writers.
    """
    import pre_plan_gates as _ppg

    lines = content.split("\n")
    section = _ppg.find_sessions_section(lines)
    if section is None:
        return False
    start, end = section
    rx = _session_block_re(_sid8(session_id))
    for i in range(start + 1, end):
        if not rx.match(lines[i]):
            continue
        block_end = end
        for j in range(i + 1, end):
            if lines[j].startswith("### "):
                block_end = j
                break
        for k in range(i + 1, block_end):
            if _ppg.annotate_marker_writer(lines[k]) == _ppg.SESSION_WRITER_WORK_DONE:
                return True
    return False


def _spine_has_session_block(session_id: str, state: dict) -> bool:
    """True if the spine's `## Sessions` carries a `/work-done` record for this
    session (see `_work_done_record_in`). The name is kept — it is the seam the
    tests patch — but since S1 a bare `[sid:<sid8>]` heading is not enough: the
    `work-done` writer marker must be present under it. Reads the spine
    MAIN-PINNED under the repo-wide bookkeeping lock so it resolves correctly
    even when the caller's cwd is a sparse-excluded worktree.

    Raises on an unreadable spine — the caller's fail-open turns that into a PASS.
    """
    import bookkeeping_resolver
    from bookkeeping_lock import bookkeeping_lock

    project_root = state.get("project_root")
    thought_rel = state.get("thought_file_path")
    if not thought_rel:
        # No spine bound → cannot assert a record. Treat as "not recorded" is
        # unsafe (would block); the omission check only reaches here with an
        # active topic, but a topic with no thought_file_path has no ## Sessions
        # surface at all, so there is nothing /work-done could have written —
        # PASS (fail-safe).
        raise _NoSpine()

    rel = Path(thought_rel)
    if rel.is_absolute():
        spine = rel
    else:
        spine = bookkeeping_resolver.resolve(str(rel), cwd=project_root)
    if spine is None or not Path(spine).is_file():
        raise FileNotFoundError(f"spine not resolvable/readable: {thought_rel}")

    spine = Path(spine)
    with bookkeeping_lock(spine):
        content = spine.read_text(encoding="utf-8")
    return _work_done_record_in(content, session_id)


class _NoSpine(Exception):
    """Sentinel: the topic has no thought_file_path (no ## Sessions surface)."""


def _lock_started_at(topic: str, project: str) -> str | None:
    """`started_at` from the COMPOSITE-keyed lock, read through the single
    key-shape locus so the M16 bare-slug bug cannot make the window wrong. None
    if no lock. `topic`/`project` come from `_active.json` (`active_project`),
    which S2's canonical mint/bind keeps consistent with the resolver — so this
    composite key matches what `/work-start` acquired (S3/A3 convergence)."""
    import taskmanagement as tm

    payload = tm.read_lock(tm.composite_lock_key(topic, project))
    if not isinstance(payload, dict):
        return None
    return payload.get("started_at") or None


def _config_source_path() -> str | None:
    """The config source repo root (harness commits land here on `main`; the
    frozen ~/.claude/.git is NOT where they land — git-policy.md §2). None on error.
    """
    res = subprocess.run(
        ["false"]  # no config-source tool ships with Q, capture_output=True, text=True, check=False
    )
    if res.returncode != 0:
        return None
    out = res.stdout.strip()
    return out or None


def _main_checkout(cwd: str | None):
    import bookkeeping_resolver

    return bookkeeping_resolver.main_checkout(cwd)


def _commits_present(topic, project, state, started_at, ended_at) -> bool:
    """True if landed-to-`main` commits in [started_at, ended_at] are confidently
    attributable to the topic. Repos are resolved to their `main` checkout so
    `git log` sees landed work (git-policy.md §11 "shipped = landed to main")."""
    import work_done

    # plan_path: the topic's spine (its Implementation Details reference the plans).
    # attribute_commits scopes commits against the plan's `## Diff` section; where
    # that is absent (Implementation-Specifics-only plans, pre-M3/S8) the scope is
    # empty and commits_present is False → soft-exit. Confidence-gated by design.
    project_root = state.get("project_root")
    thought_rel = state.get("thought_file_path")
    plan_path = None
    if thought_rel:
        rel = Path(thought_rel)
        plan_path = rel if rel.is_absolute() else _resolve_main(str(rel), project_root)

    project_repo = _main_checkout(project_root)          # Projects `main`
    harness_source = _config_source_path()              # config source `main`
    harness_repo = _main_checkout(harness_source) if harness_source else None

    if plan_path is None or project_repo is None or harness_repo is None:
        # Missing a required input for a confident attribution → fail-safe.
        return False

    res = work_done.detect_ship_event(
        topic_slug=topic,
        project_slug=project,
        plan_path=str(plan_path),
        session_id="",  # not used in attribution (taskmanagement note)
        started_at=started_at,
        ended_at=ended_at,
        project_repo=str(project_repo),
        harness_repo=str(harness_repo),
    )
    return bool(res.get("commits_present"))


def _unresolved_plan_refs(topic, project, state) -> list[tuple[str, str]]:
    """The plan references this topic's spine declares but that cannot be opened.

    S3/A4, and the FOURTH narrowing point. `_commits_present` above returns a bare
    `bool`, so a resolution failure cannot survive it however faithfully the layers
    below carry one. Rather than widen that function's return — it has callers whose
    contract is a boolean, and a truthiness change there is exactly the kind of edit
    that goes wrong quietly — the identity of the failed references is fetched
    alongside it, from the same spine.

    Fail-open: any error yields an empty list. This feeds a REPORT, and a reporting
    path that can raise is worse than one that occasionally says less.
    """
    try:
        import taskmanagement as _tm

        thought_rel = state.get("thought_file_path")
        if not thought_rel:
            return []
        rel = Path(thought_rel)
        spine = rel if rel.is_absolute() else _resolve_main(
            str(rel), state.get("project_root"))
        if spine is None:
            return []
        _scope, unresolved = _tm.impl_specifics_scope_with_unresolved(spine)
        return unresolved or []
    except Exception:
        return []


def _describe_unresolved(unresolved: list[tuple[str, str]], limit: int = 3) -> str:
    """One line naming the specific references that could not be opened.

    C3 requires the SPECIFIC reference, not merely the spine that contained it — so the
    refs are rendered verbatim. Bounded, because a spine can carry a dozen and a reason
    string is not a report.

    **DE-DUPLICATED BY REFERENCE, WITH A ROW COUNT.** This is not tidiness: on the two
    real dangling spines EVERY row points at the same missing file (7 rows at
    `[[lazy-doodling-wadler]]`, 9 at `dapper-coalescing-seal.md`). Listing per-row would
    print one name three times and then "(+4 more)", which reads as seven distinct
    problems and sends the operator looking for six files that were never missing. It is
    one missing file, referenced seven times, and saying so is both shorter and true.
    Found by running this against the real corpus rather than a fixture — the fixtures
    all used distinct references and could not have shown it.
    """
    if not unresolved:
        return ""
    counts: dict[str, int] = {}
    for ref, _reason in unresolved:
        key = str(ref)
        counts[key] = counts.get(key, 0) + 1
    names = []
    for ref, n in list(counts.items())[:limit]:
        names.append(f"{ref} (referenced by {n} rows)" if n > 1 else ref)
    more = len(counts) - len(names)
    tail = f" (+{more} more)" if more > 0 else ""
    label = "reference" if len(counts) == 1 else "references"
    return f"; could not open plan {label}: " + ", ".join(names) + tail


def _resolve_main(rel: str, cwd: str | None):
    import bookkeeping_resolver

    return bookkeeping_resolver.resolve(rel, cwd=cwd)


def _block(topic: str, project: str, session_id: str) -> dict:
    cmd = ESCAPE_HATCH_CMD.format(sid=session_id, topic=topic)
    msg = (
        "BLOCKED (check-work-done-omission): this session shipped work under the "
        f"active tracked topic '{topic}' but did NOT record it with /work-done, so "
        "the plan / TODO / completed-work ledger will drift.\n"
        "  • Run  /work-done  to record the ship (writes the spine ## Sessions "
        "block + ledger), OR\n"
        "  • if the omission is deliberate, override the stop with:\n"
        f"      {cmd}\n"
        "    (records a `skipped: true` row with your reason in the completed-work "
        "ledger; the next close then passes)."
    )
    return {"block": True, "reason": "shipped work with no /work-done record",
            "message": msg, "topic": topic, "project": project}


# --------------------------------------------------------------------------- #
# Report-only mode (S1 / A1 + G4)
#
# THE TOGGLE AND ITS DEFAULT LIVE IN CODE, NOT IN PROSE. A1's guard rail requires
# a module-level constant rather than an environment variable, and the reason is
# not style: an unset env var and a deliberate default are indistinguishable to a
# reader, and this whole slice's safety argument rests on the default being
# report-only. As a constant, "report-only is the default" is a property a test
# can assert — and `test_check_work_done_omission.py` does.
#
# WHY REPORT-ONLY EXISTS. Fixing the plan-reference resolver (S2) makes this gate
# see file scope on six topics where it has never seen any. Turning that into a
# hard block in the same change would take six silent topics to six blocking ones
# with nobody having ever read the output. So the gate is made to SPEAK first and
# ACT later; turning blocking on is the registered follow-up's job, and its stated
# precondition is narrowing `_loose_scope_match` first.
#
# The BLOCK path below is deliberately left intact and simply unreached — flipping
# this constant is the whole of what the follow-up has to do here.
# --------------------------------------------------------------------------- #
REPORT_ONLY = True


def _report_only(verdict: dict) -> dict:
    """Convert a BLOCK verdict into a non-blocking PASS that still says what it
    would have blocked. A no-op on a verdict that was not going to block, and a
    no-op entirely when `REPORT_ONLY` is False.

    The would-block reason and message are carried forward under distinct keys
    rather than in `reason`, so a caller can tell "passed because there was
    nothing to report" from "passed because reporting is all it is allowed to do"
    — which is the exact distinction the silence this slice removes was hiding.
    """
    if not REPORT_ONLY or not verdict.get("block"):
        return verdict
    return {
        "block": False,
        "report_only": True,
        "reason": "report-only (not blocking): would have blocked — "
                  + (verdict.get("reason") or "shipped work with no /work-done record"),
        "message": None,
        "would_block_reason": verdict.get("reason"),
        "would_block_message": verdict.get("message"),
        "topic": verdict.get("topic"),
        "project": verdict.get("project"),
    }


def _transcript_path(session_id: str):
    try:
        import research_token_parser as _rtp
        return _rtp.find_transcript(session_id)
    except Exception:
        return None


def _session_started_at(session_id: str) -> str | None:
    """Start of the session's OWN activity window, from its transcript.

    An UNBOUND topic has no lock, so `_lock_started_at` has nothing to read —
    this is the window source for the registration-aware arm.

    The source is the FIRST timestamped record in the transcript, NOT the file's
    `st_ctime`. On POSIX every append bumps `ctime`, and the transcript is being
    appended to by the very turn that fires this Stop hook — so `ctime` is
    effectively "now", which would collapse the window to zero width and make
    `_commits_present` False for every real ship. `st_birthtime` is the fallback
    where the transcript carries no parseable timestamp.

    Returns an ISO timestamp, or None (refuse-to-guess: no window ⇒ PASS).
    """
    path = _transcript_path(session_id)
    if path is None:
        return None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(50):          # bounded scan of the head
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("timestamp") if isinstance(row, dict) else None
                if isinstance(ts, str) and ts:
                    return ts
    except Exception:
        pass
    try:
        st = path.stat()
        birth = getattr(st, "st_birthtime", None)
        if birth:
            return datetime.fromtimestamp(birth, tz=timezone.utc).isoformat()
    except Exception:
        pass
    return None


# `(?!\w)` keeps `.md` from matching as a substring of a longer extension —
# without it `foo_THOUGHT.mdx` yields a phantom reference to `foo`.
_ARTIFACT_REF_RE = re.compile(
    r"([A-Za-z0-9][\w\-.]*?)(?:-(\d{14}))?_(THOUGHT|PLAN)\.md(?!\w)")


def _spine_refs_from_transcript(session_id: str):
    """Topic slugs named by the session's OPENING message — its handoff prompt.

    This is the "handoff context" half of the plan's discovery sources. A Stop
    hook has no prompt body, but the transcript's FIRST user record is the
    durable copy of the pasted handoff prompt.

    Scope is deliberately the opening message ONLY, never the whole transcript.
    A full-transcript scan is not a discovery source, it is a guess: a long
    session mentions every artifact it read or discussed (measured on a real
    25-hour transcript: 20 slugs, including a literal `<slug>` from template
    prose and several unrelated topics), and resolving any of those would
    attribute a ship to the wrong topic — exactly what refuse-to-guess forbids.
    """
    path = _transcript_path(session_id)
    if path is None:
        return []
    text = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(50):          # bounded head scan for the first user turn
                line = fh.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("type") == "user" or (
                        isinstance(row.get("message"), dict)
                        and row["message"].get("role") == "user"):
                    text = json.dumps(row)   # the record's own text, escaped or not
                    break
    except Exception:
        return []
    if not text:
        return []
    slugs = []
    for m in _ARTIFACT_REF_RE.finditer(text):
        slug = m.group(1)
        if not slug or slug.startswith("<"):   # template placeholders are not refs
            continue
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def _discoverable_spine(cwd=None, session_id: str | None = None):
    """The ship's anchoring artifact for an UNBOUND session, or None.

    Deterministic and bounded. Two sources, both the ones auto-bind uses:
    the worktree name (`_autobind_slugs_from_worktree`) and spine references in
    the session's transcript (`_autobind_slugs_from_prompt` over it). A slug
    resolves only when EXACTLY ONE spine artifact matches it on disk, preferring
    a `_THOUGHT` over a `_PLAN`. Anything ambiguous or absent returns None —
    refuse-to-guess, never a cwd/Root default.
    """
    try:
        import pre_plan_gates as _ppg
        base = Path(cwd) if cwd else Path.cwd()
        slugs = list(_ppg._autobind_slugs_from_worktree(cwd))
        for s in (_spine_refs_from_transcript(session_id) if session_id else []):
            if s not in slugs:
                slugs.append(s)
        # Search the session's own tree first, then the Projects-root staging
        # graph — both are deterministic locations, neither is a guess.
        roots = [base]
        try:
            if _ppg.PROJECTS_ROOT not in roots:
                roots.append(_ppg.PROJECTS_ROOT)
        except Exception:
            pass
        # Collect EVERY candidate, then require the whole discovery to resolve
        # to exactly one artifact. Returning the first match would silently pick
        # a winner among several equally-plausible topics — a guess.
        # De-duplicated by RESOLVED path: `PROJECTS_ROOT` is a symlink to the
        # real checkout on this machine, so the same artifact is reachable under
        # two different path strings and would otherwise read as "ambiguous".
        found: list[Path] = []
        seen: set[str] = set()
        for slug in slugs:
            for root in roots:
                for pattern in (f"{slug}-*_THOUGHT.md", f"{slug}_THOUGHT.md",
                                f"{slug}-*_PLAN.md", f"{slug}_PLAN.md"):
                    try:
                        hits = sorted((Path(root) / "Thoughts").glob(pattern))
                    except Exception:
                        continue
                    for h in hits:
                        try:
                            key = str(h.resolve())
                        except Exception:
                            key = str(h)
                        if key not in seen:
                            seen.add(key)
                            found.append(h)
        if not found:
            return None
        # ONE topic, or nothing. Candidates spanning two different slugs are
        # ambiguous even when only one of them is a `_THOUGHT` — preferring the
        # spine there would silently pick a winner between two topics the
        # opening message named, which is the very mis-attribution this arm
        # exists to avoid.
        by_slug = {m.group(1) for f in found
                   for m in [_ARTIFACT_REF_RE.search(f.name)] if m}
        if len(by_slug) != 1:
            return None
        thoughts = [f for f in found if f.name.endswith("_THOUGHT.md")]
        if len(thoughts) == 1:
            return thoughts[0]            # within ONE topic, a spine outranks a plan
        if not thoughts and len(found) == 1:
            return found[0]
        return None                        # ambiguous → refuse to guess
    except Exception:
        return None


def _decide_unregistered(session_id: str, now: datetime | None) -> dict:
    """The registration-aware arm (auto-registration S-C / A4).

    Reached only when the session is UNBOUND — the case the hook used to
    soft-pass unconditionally, which is exactly the never-`/work-start`ed
    direct-implement path that also shipped commits. It BLOCKS only on positive
    evidence of both halves: a discoverable anchoring artifact AND in-window
    landed commits attributable to it. Every indeterminate branch PASSes
    (fail-open + refuse-to-guess). It also catches a TORN mint: a crashed
    `auto_register_topic` leaves the topic unbound, so this arm fires.
    """
    spine = _discoverable_spine(session_id=session_id)
    if spine is None:
        return _pass("unregistered topic, and no anchoring artifact is "
                     "discoverable — refusing to guess")
    started_at = _session_started_at(session_id)
    if not started_at:
        return _pass("unregistered topic, and the session activity window is "
                     "indeterminate (fail-safe)")
    try:
        import pre_plan_gates as _ppg
        project = _ppg.canonical_project_for_spine(spine)   # always resolves
        topic = _ppg._timestamped_slug_from_spine(spine)
        topic = re.sub(r"-\d{14}$", "", topic)
    except Exception:
        return _pass("unregistered topic, and identity could not be derived "
                     "from the anchoring artifact (fail-safe)")

    synthetic_state = {
        "project_root": str(_ppg._project_dir_for_spine(spine)),
        "thought_file_path": str(spine),
    }
    ended_at = (now or datetime.now(timezone.utc)).isoformat()
    if not _commits_present(topic, project, synthetic_state,
                            started_at, ended_at):
        # Same distinction as the bound arm: "I looked and found nothing" and "I could
        # not look" are opposite statements about how much the silence is worth. This
        # arm had neither the lookup nor the tag, so an unregistered topic with a
        # dangling spine reported nothing at all.
        unresolved = _unresolved_plan_refs(topic, project, synthetic_state)
        if unresolved:
            out = _pass(
                "unregistered topic, and its scope could not be determined — the "
                "declared plan references do not resolve"
                + _describe_unresolved(unresolved))
            out["unresolved_report"] = True
            out["unresolved"] = unresolved
            out["topic"] = topic
            out["project"] = project
            return out
        return _pass("unregistered topic, but no in-window landed commits in "
                     "scope")
    out = _block(topic, project, session_id)
    out["reason"] = "shipped work on an unregistered topic"
    out["message"] = (
        f"Shipped work on a topic that was never registered "
        f"({Path(spine).name}).\n"
        "  Run `/work-done` (it auto-registers the topic as a pre-step), or "
        "register it directly:\n"
        f"      python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py auto-register "
        f"{session_id} --spine {spine}\n"
        + (out.get("message") or "")
    )
    return _report_only(out)


def decide(session_id: str, *, now: datetime | None = None) -> dict:
    """Pure decision. Never raises — any internal error is caught and returned as a
    PASS (fail-open), with the traceback logged to stderr."""
    try:
        # (1) active topic
        topic, project = _active_topic(session_id)
        if not topic or not project:
            # Registration-aware arm (A4): an unbound session is no longer an
            # unconditional soft-pass. It PASSes unless BOTH an anchoring
            # artifact is discoverable AND in-window commits landed.
            return _decide_unregistered(session_id, now)

        state = _topic_state(topic, project)
        if state is None:
            return _pass("no topic-state file for the active topic")

        # (2) already-recorded FIRST (survives the released-lock + midnight cases)
        if _skipped_ledger_row(session_id, topic):
            return _pass("skip already recorded in the completed-work ledger")
        try:
            if _spine_has_session_block(session_id, state):
                return _pass("session already recorded in the spine ## Sessions block")
        except _NoSpine:
            return _pass("topic has no spine ## Sessions surface (nothing to record)")

        # (3) ship-window source — lock.started_at; no lock → fail-safe soft-exit
        started_at = _lock_started_at(topic, project)
        if not started_at:
            return _pass("no lock present → ship window indeterminate (fail-safe)")
        ended_at = (now or datetime.now(timezone.utc)).isoformat()

        # (4) landed-to-main commit signal (confidence-gated)
        if not _commits_present(topic, project, state, started_at, ended_at):
            # S3/C4 — THE DISTINCTION THIS SLICE EXISTS TO MAKE.
            #
            # Both of these used to emit the identical string, and that identity WAS
            # the defect: "I looked and there is nothing to report" and "I could not
            # look" are opposite statements about how much to trust the silence, and
            # a reader had no way to tell them apart. Naming the unresolved reference
            # is what makes the second one actionable — C3 asks for the specific
            # reference, not merely the spine that held it.
            unresolved = _unresolved_plan_refs(topic, project, state)
            if unresolved:
                out = _pass(
                    "could not determine scope — the topic declares plan references "
                    "that do not resolve, so an omission here would be invisible"
                    + _describe_unresolved(unresolved))
                # The verdict is TAGGED so `main()` can emit it. Computing a reason
                # and returning it in a dict that dies with the process is not
                # "saying so by name" — it is the same shape as the silence this
                # whole slice removes, one layer up. `main()` prints only on a block
                # and persists only on a report-only verdict, so without these keys
                # this branch would return 0 having said nothing to anyone.
                out["unresolved_report"] = True
                out["unresolved"] = unresolved
                out["topic"] = topic
                out["project"] = project
                return out
            return _pass("no in-window landed commits in scope")

        # (5) shipped work, not recorded → BLOCK (report-only while REPORT_ONLY)
        out = _report_only(_block(topic, project, session_id))
        # THE PARTIAL-RESOLUTION CASE, which an earlier version missed entirely.
        #
        # The unresolved lookup used to live only under `if not _commits_present(...)`
        # — i.e. only when there was nothing to report anyway. So a would-block report
        # computed from a scope where SOME rows resolved and others did not never
        # mentioned the unopenable ones, and the operator read a confident-looking
        # report over a scope with a hole in it. That is precisely the case A4 calls
        # "the whole point": scope is non-empty, `scope_empty` is False, and nothing
        # else signals a problem.
        unresolved = _unresolved_plan_refs(topic, project, state)
        if unresolved:
            out["unresolved"] = unresolved
            out["reason"] = (out.get("reason") or "") + _describe_unresolved(unresolved)
            if out.get("would_block_reason"):
                out["would_block_reason"] += _describe_unresolved(unresolved)
        return out
    except Exception:  # fail-open — never block on our own bug
        traceback.print_exc()
        return _pass("internal error — failing open (see traceback on stderr)")


def _self_test() -> int:
    """Structural smoke test — no live state required."""
    # sid-token match is date-independent
    rx = _session_block_re("1e9250ee")
    assert rx.search("## Sessions\n\n### 2026-07-27 session [sid:1e9250ee]\n")
    assert rx.search("### 2000-01-01 session [sid:1e9250ee-longer]\n"), "date-independent"
    assert not rx.search("### 2026-07-27 session [sid:deadbeef]\n"), "wrong sid"
    # S1 — the gate keys on the /work-done writer marker, not the heading alone.
    hdr = "## Sessions\n\n### 2026-07-27 session [sid:1e9250ee]\n"
    assert not _work_done_record_in(hdr, "1e9250ee"), "a bare heading is not a record"
    assert not _work_done_record_in(
        hdr + "- Duration: 30min <!-- close-metrics -->\n", "1e9250ee"), "/close block alone"
    assert not _work_done_record_in(
        hdr + "- Note. <!-- annotate-session writer=close -->\n", "1e9250ee"), "/close --annotate"
    assert _work_done_record_in(
        hdr + "- Shipped. <!-- annotate-session writer=work-done -->\n", "1e9250ee")
    assert _work_done_record_in(
        hdr + "- Shipped. <!-- annotate-session -->\n", "1e9250ee"), "legacy bare tag"
    assert not _work_done_record_in(
        "## Sessions Plan\n\n### 2026-07-27 session [sid:1e9250ee]\n"
        "- Shipped. <!-- annotate-session -->\n", "1e9250ee"), "wrong section"
    # no active topic → PASS (uses live _active.json; an unknown sid is unbound)
    d = decide("no-such-session-zzzz")
    assert d["block"] is False, d
    # block message names the escape hatch verbatim
    b = _block("mytopic", "Root", "abcd1234")
    assert b["block"] is True
    assert "skip-work-done-check" in b["message"]
    assert "abcd1234" in b["message"] and "mytopic" in b["message"]
    # S1 — report-only is the DEFAULT, and it is a code constant (A1 guard rail)
    assert REPORT_ONLY is True, "report-only must be the shipped default"
    r = _report_only(b)
    assert r["block"] is False, "report-only must never block"
    assert r["report_only"] is True
    assert "would have blocked" in r["reason"], r["reason"]
    assert r["would_block_message"] == b["message"], "the block text is carried, not dropped"
    assert r["topic"] == "mytopic" and r["project"] == "Root"
    # a non-blocking verdict passes through untouched
    p = _pass("nothing to see")
    assert _report_only(p) is p, "a PASS is not rewritten"
    print("check_work_done_omission self-test: OK")
    return 0


def _framing_soft_warn() -> str | None:
    """The SHARED framing-obligation surface (auto-registration S-C / A3), rendered
    here identically to the SessionStart scan and `/close`.

    Strictly ADDITIVE and non-blocking: it is emitted alongside whatever verdict
    `decide` reached and NEVER affects it. Fail-open — any error yields None.
    """
    try:
        import framing_obligation as _fo
        import bookkeeping_resolver as _br
        roots = []
        try:
            main_co = _br.main_checkout(Path.cwd())
            if main_co:
                roots.append(str(main_co))
        except Exception:
            pass
        paths = _fo.default_todo_paths(roots)
        if not paths:
            return None
        return _fo.render_surface(_fo.reconcile(paths))
    except Exception:
        return None


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--self-test":
        return _self_test()
    if not argv:
        print("usage: check_work_done_omission.py SESSION_ID | --self-test", file=sys.stderr)
        return 0  # fail-open: no session id → do not block
    session_id = argv[0]
    verdict = decide(session_id)
    # Additive SOFT surface — emitted whatever the verdict, never part of it.
    warn = _framing_soft_warn()
    if warn:
        print(warn, file=sys.stderr)
    # A6 emission path. PERSISTENCE LIVES HERE, NOT IN `decide()`.
    #
    # The reason, stated accurately rather than as the slogan it is tempting to
    # write. `decide()` is NOT I/O-free and never was: it reads `_active.json`, the
    # topic state, the ledger, the spine and the lock file, and by way of
    # `_commits_present` it shells out to `config-source` and to `git log`. What its
    # docstring actually promises is narrower and is what matters — a decision that
    # never raises, and that WRITES nothing. Adding a write here would break the
    # second half, and a write inside a fail-open path is how a reporting channel
    # turns into a source of the failures it is meant to report. So the write sits
    # at the edge, where an exception can be contained without touching the verdict.
    #
    # The record is the channel the requirement is reported against (see
    # work_done_report's module docstring for why the two obvious live channels were
    # established to be dead). The stderr line below is additive best-effort and is
    # NOT claimed to reach anyone.
    if verdict.get("report_only"):
        _emit_report_only(session_id, verdict)
    elif verdict.get("unresolved_report"):
        # C3/C4's emission. This branch was MISSING in the first version of this
        # slice: the reason naming the unresolvable reference was computed in
        # `decide()` and then reached nobody, because neither guard above matched a
        # plain PASS. An independent checker found it, and it is worth naming as the
        # defect it was — the plan's own G5 diagnoses exactly this shape ("a
        # report-only verdict has nowhere to go"), it was fixed for C5, and the
        # identical reasoning was not carried across to C3/C4. Computing a name is
        # not saying it.
        _emit_unresolved(session_id, verdict)
    if verdict.get("block"):
        print(verdict.get("message") or "BLOCKED: /work-done omission", file=sys.stderr)
        return 2
    return 0


def _emit_unresolved(session_id: str, verdict: dict) -> None:
    """Persist the could-not-determine-scope verdict so C3/C4 reach a person.

    Uses the SAME durable channel as `_emit_report_only` — one record, one `/close`
    surface — because these are two things the operator needs to hear at the same
    moment ("here is what I would have blocked" and "here is what I could not even
    look at"), and a second parallel channel would be the duplication this plan's own
    Guiding Policy warns against.

    Recorded with `kind` so the two are distinguishable in the record and can be
    rendered differently, rather than collapsing into one undifferentiated list — the
    whole point of C4 is that these two cases must not read the same.

    Fail-open in both halves, like its sibling.
    """
    try:
        import work_done_report as _wdr
        unresolved = verdict.get("unresolved") or []
        _wdr.append_report(
            session_id=session_id,
            topic=verdict.get("topic") or "",
            project=verdict.get("project") or "",
            reason=verdict.get("reason") or "",
            message="; ".join(f"{ref} — {why}" for ref, why in unresolved),
            kind="unresolved",
        )
    except Exception:
        pass
    try:
        print("[work-done omission — SCOPE UNRESOLVED, not blocking] "
              + (verdict.get("reason") or "")
              + "\n  Recorded; it is surfaced at /close.",
              file=sys.stderr)
    except Exception:
        pass


def _emit_report_only(session_id: str, verdict: dict) -> None:
    """Persist a report-only verdict and additionally say so on stderr.

    Fail-open in both halves: a reporting channel attached to a fail-open gate must
    never be the thing that changes an exit code.
    """
    try:
        import work_done_report as _wdr
        _wdr.append_report(
            session_id=session_id,
            topic=verdict.get("topic") or "",
            project=verdict.get("project") or "",
            reason=verdict.get("would_block_reason") or "",
            message=verdict.get("would_block_message") or "",
        )
    except Exception:
        pass
    try:
        print(
            "[work-done omission — REPORT ONLY, not blocking] "
            + (verdict.get("would_block_reason") or "would have blocked")
            + "\n  Recorded; it is surfaced at /close.",
            file=sys.stderr,
        )
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

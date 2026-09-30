#!/usr/bin/env python3
"""Walking skeleton of the /execute-plan orchestrator (slice S2).

A thin, code-owned dispatch loop that walks a plan's slice register one slice
in flight, synchronous and foreground, resolving each slice's model family
deterministically and recording lifecycle behind a persistence port. S2 is the
WALKING SKELETON: it proves the four structural layers — persistence port,
dispatch loop, model-family rule, spawn boundary — against in-memory stand-ins,
with one real Agent spawn (driven from SKILL.md, not here) proving the
execution boundary real-to-real.

OUT of scope for S2 (named seams, NOT implemented here):
  - real persistence adapters (Full/Minimal)        -> S3 (DONE — see below)
  - crash-safe 3-checkpoint state + idempotent guard -> S4 (DONE — see below)
  - post-dispatch transcript model-pin verification  -> S5 (DONE — see below)
  - two-layer conformance + verify_write             -> S6 (DONE — see below)
  - slice_id-prefixed git commit + unified contract  -> S7 (DONE — see below)
  - session/mode UX gates (Confirm, fresh-session)   -> S8 (DONE — see below)
  - end-of-plan close-out (harvest + report + diags)  -> S9 (DONE — see below)

Hexagonal / code-first (~/.claude/rules/code_first_architecture.md):
- Code owns the loop + the ports. The AI (SKILL.md) does only the one thing
  code cannot from Python: invoke the harness Agent tool for a real spawn.
- Two OUTPUT ports, each with an in-memory stand-in as its ONLY S2 impl:
    BookkeepingPort  -- slice lifecycle persistence  (real adapters: S3)
    SpawnPort        -- agent execution              (real spawn: SKILL.md)
- Built test-to-test first (code_first_architecture.md:286-291): the loop runs
  against InMemoryBookkeepingAdapter + FakeSpawnAdapter before any real adapter.
  Do NOT skip to step 4 on the persistence axis (real adapter is S3).

The model-family translation reuses the family-slug map already pinned at
_factcheck_engine.py:379-383 (spine A8) -- the produced slugs are exactly that
map's keys; this module never invents a new model vocabulary.

CLI (stdin JSON -> structured JSON stdout + exit codes; mirrors the topology of
plan-followups-review/run.py:30-47,340-357):
  translate        {"agent_choice": "routine"|"more_capable"}        -> {"model_family": ...}
  plan-slices      {"register_markdown": <md>} | {"spine_path": <p>}  -> {"slices": [...]}
  dry-run          {"register_markdown": <md>} | {"spine_path": <p>}  -> {"dispatched": [...], "summary": {...}}
  verify-model     {"session_id","agent_id"} | {"transcript_path"}    -> {"expected","used","match"}
                   + {"agent_choice"} | {"expected_family"}              (S5 real model-pin reader)
  check-code       {"verify_specs": [{surface_kind,path,expected_payload,locator}, ...]}
                                                                       (S6 code-layer verify_write)
  check-conformance {"verdict": "..."} | {"verdict_path": "..."}       (S6 conformance verdict)
  commit-slice     {"slice_id","repo_dir"?,"commit_summary"?}         (S7 real slice_id-prefixed git commit)
  push-gate        {"all_slices_done","work_done_succeeded","git_status_clean","aborted"?}
                                                                       (S7 end-of-plan push gate — evaluate only)
  mode             {"mode": "observer"|"confirm"|"auto"?}             -> {"mode": ...} (S8 normalize; blank->observer)
  confirm-gate     {"id","name"?,"type"?,"agent_choice"?,"confirm_override"?,"mode","layer1"?}
                                                                       -> {"needs_confirm","prompt"} (S8 AI-promotes-only)
  escalation-surface {"slice_id","attempts","mode","notify_recipient"?,"dispatched"?}
                                                                       -> {in_band, notify, retry_count} (S8; persisted count)
  session-summary  {"slice_execution": {...}} | {"state_path": <p>}    (S8 resume summary; U2 Investigate / U6 re-entry)
  set-active-run   {"owner_session_id","surface_path","surface_kind"?,"total_slices"?,"pending_handoff"?}
                                                                       (S8 mark a run resumable for the U2 arm hook;
                                                                        NS1 optionally arms a typed HandoffRecord)
  clear-active-run {}                                                  (S8 remove the active-run pointer at end-of-plan)
  compute-handoff  {"register_markdown"|"spine_path", "slice_execution"?, "planned_slice_ids"?}
                                                                       -> {"handoff": {...}|null}
                                                                       (NS1/NS2 v2 — code-derived typed handoff for the
                                                                        next ready slice; full 3-way truth table)
  resume           {"pointer_dir"?}  -> {"action","type","dispatch","slice_id","next"}
                                                                       (NS1 v2 — fresh-session cross-session resume read)
  report           {"slice_execution": {...}} | {"state_path": <p>}    (S9 per-slice impl report A16; rows + markdown)
  diagnostics      {"slice_execution": {...}} | {"state_path": <p>}    (S9 A17 correlations — captured, NOT gated; always exit 0)
                   + {"restarts": <int>}?
  harvest-observations {"dispatched": [...]} | {"slice_execution": {...}} | {"state_path": <p>}
                                                                       (S9 U5 collect verified observations into the
                                                                        /plan-followups-review input contract)
  scan-unarmed     {"plan_paths":[...]} | {"thoughts_dir","slug"} | {"session_id"}  [+ {"pointer_dir"}]
                                                                       -> {"warnings":[...],"count":N}
                                                                       (A3 SessionStart WARNING — a multi-step plan taken
                                                                        into implementation OFF the /plan Step-11 arming
                                                                        path; advisory, always exit 0, fail-soft)

Exit codes:
  0 -- OK (incl. verify-model match, check-code all-pass, check-conformance PASS,
          commit-slice committed, push-gate should_push)
  2 -- DEADLOCK / unsatisfiable register (slices remain but none are ready)
  3 -- usage / input-format / schema error (incl. unknown agent_choice)
  4 -- model-pin MISMATCH or UNVERIFIABLE transcript (verify-model only)
  5 -- code-layer verify_write FAIL (check-code)
  6 -- conformance non-PASS (check-conformance)
  7 -- push WITHHELD by the end-of-plan gate (push-gate)
  8 -- git-contract error: bad subject / protected branch / git failure (commit-slice)
  9 -- plan-filing gate FAIL: an approved plan did not land on both surfaces (verify-plan-filed)
"""

import abc
import hashlib
import json
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

SUBCOMMANDS = {"translate", "plan-slices", "dry-run", "verify-model",
               "check-code", "check-conformance", "commit-slice", "push-gate",
               "mode", "confirm-gate", "escalation-surface", "session-summary",
               "set-active-run", "clear-active-run",
               "report", "diagnostics", "harvest-observations",
               "compute-handoff", "resume",
               "mark-plan-exhausted", "verify-plan-filed", "plan-detour-return",
               "reconcile-code-face", "commit-detour",
               "record-composed-type", "recompose-check", "deferred-captures",
               "resume-context", "omtm",
               "exception-surface", "engagement-rate",
               "execplan-gate-check", "execplan-ack", "execplan-reap",
               "register-presence", "become-walker", "checkout-slice",
               "execplan-entry-check", "execplan-entry-suppress",
               "migrate-run-pointer-fields", "restore-legacy-pointer",
               "record-conformance", "walk-gate-check",
               "extract-bash-targets", "scan-unarmed"}

# Canonical family slugs — the KEYS of the model-id map pinned at
# _factcheck_engine.py:379-383. Reused, not reinvented (spine A8).
CANONICAL_FAMILY_SLUGS = ("haiku", "sonnet", "opus")

# agent_choice (the slicer's vocabulary) -> family slug (the canonical map's key).
# This is the ONLY translation; the target slugs are a subset of the canonical
# map above so a slug drift is caught by the assertion below.
AGENT_CHOICE_TO_FAMILY = {
    "routine": "sonnet",
    "more_capable": "opus",
}
assert set(AGENT_CHOICE_TO_FAMILY.values()) <= set(CANONICAL_FAMILY_SLUGS), (
    "model-family translation produced a slug not in the canonical "
    "_factcheck_engine.py:379-383 map"
)


# --------------------------------------------------------------------------- #
# Slice record (read-only contract; depends_on is the DAG edge set)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Slice:
    id: str
    name: str
    type: str
    agent_choice: str
    depends_on: tuple = field(default_factory=tuple)
    confirm_override: bool = False
    # v2 (NS1) — additive dispatch axis (design #1). None = unspecified; the
    # effective value is computed by resolve_dispatch() (explicit > confirm_override
    # alias > conservative 'attended' default). Slice stays frozen and every
    # existing construction is unaffected (additive field with a default).
    dispatch: str = None
    # v2 (NS10, PROVISIONAL — design #10) — optional shared write targets. Two
    # ready slices sharing a target are serialized by next_ready_slice (the later
    # releases only after the earlier completes + a context-reset boundary). Empty
    # default preserves the original DAG behavior exactly.
    write_targets: tuple = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# A8 — model-family translation (pure, deterministic, reused map)
# --------------------------------------------------------------------------- #

def agent_choice_to_model_family(agent_choice):
    """Map a slice's agent_choice onto a canonical model-family slug.

    routine -> sonnet, more_capable -> opus. Raises ValueError on anything
    else -- NO silent default (a wrong-model run must never start).
    """
    try:
        return AGENT_CHOICE_TO_FAMILY[agent_choice]
    except (KeyError, TypeError):
        raise ValueError(
            f"unknown agent_choice {agent_choice!r}; "
            f"expected one of {sorted(AGENT_CHOICE_TO_FAMILY)}"
        )


# --------------------------------------------------------------------------- #
# A2 — BookkeepingPort (persistence boundary) + in-memory stand-in
# --------------------------------------------------------------------------- #

class BookkeepingPort(abc.ABC):
    """Persistence boundary for slice lifecycle. S3 ships three implementations:
    the InMemory stand-in plus the real FullBookkeepingAdapter (Tier 2) and
    MinimalBookkeepingAdapter (Tier 1), all behind this same port.

    S4 extends the lifecycle into a crash-safe 3-checkpoint state machine —
    `started` -> `committed` -> `completed` — plus a persisted retry counter and
    an `escalated` terminal. The S2/S3 surface (mark_started / mark_completed /
    is_completed / get / all) is unchanged; the additions are:
      - mark_committed / is_committed  — the middle checkpoint (A6)
      - record_attempt                 — persisted retry counter, survives a
                                         crash+resume (A6/A12; the loop's per-
                                         attempt entry point, supersedes
                                         mark_started inside run_dispatch_loop)
      - mark_escalated                 — terminal for a slice that exhausted its
                                         retry budget (A12)
    All additions are additive to the slice_execution entry shape (a `committed`
    status value + an `attempts` int); no existing field or signature changes."""

    @abc.abstractmethod
    def mark_started(self, slice_id, *, model_family):
        ...

    @abc.abstractmethod
    def mark_completed(self, slice_id, *, result):
        ...

    @abc.abstractmethod
    def is_completed(self, slice_id) -> bool:
        ...

    @abc.abstractmethod
    def get(self, slice_id):
        ...

    @abc.abstractmethod
    def all(self) -> dict:
        ...

    # --- S4 crash-safe extensions (committed checkpoint + retry counter) --- #

    @abc.abstractmethod
    def mark_committed(self, slice_id, *, result):
        """Record the middle checkpoint: the slice's commit has landed but
        conformance has not yet confirmed it. Preserves model_family + attempts."""
        ...

    @abc.abstractmethod
    def is_committed(self, slice_id) -> bool:
        ...

    @abc.abstractmethod
    def record_attempt(self, slice_id, *, model_family) -> int:
        """Begin one attempt at a slice: set status `started`, set model_family,
        and increment the PERSISTED `attempts` counter (read back across a
        crash+resume — the count is continued, never reset). Returns the new
        attempt number. This is the loop's per-attempt entry point."""
        ...

    @abc.abstractmethod
    def mark_escalated(self, slice_id):
        """Terminal: the slice exhausted its retry budget. Not `completed`, so
        dependents stay blocked until a human resolves the escalation (S8 UX)."""
        ...


def _merge_timing(entry, started_at, completed_at, attended, slice_type):
    """NS8 — merge captured-not-gated timing/attended/type onto a slice entry.
    Only non-None fields are written, and `started_at` is set ONCE (first attempt
    wins across retries); nothing else in the entry is disturbed."""
    if started_at is not None:
        entry.setdefault("started_at", started_at)
    if completed_at is not None:
        entry["completed_at"] = completed_at
    if attended is not None:
        entry["attended"] = attended
    if slice_type is not None:
        entry["slice_type"] = slice_type


class InMemoryBookkeepingAdapter(BookkeepingPort):
    """In-memory stand-in for the loop's test-to-test proof (S2).

    No file, no DB, no network: running the loop against this adapter has zero
    persistence side-effect. S3 adds the Full/Minimal real adapters as SIBLINGS
    behind the same port — the loop is unchanged (Evolution Test)."""

    def __init__(self):
        self._state = {}

    def mark_started(self, slice_id, *, model_family):
        # Merge (not clobber) so a pre-existing `attempts` survives (S4).
        entry = self._state.setdefault(slice_id, {})
        entry["status"] = "started"
        entry["model_family"] = model_family

    def mark_completed(self, slice_id, *, result):
        entry = self._state.setdefault(slice_id, {})
        entry["status"] = "completed"
        entry["result"] = result

    def is_completed(self, slice_id) -> bool:
        return self._state.get(slice_id, {}).get("status") == "completed"

    def get(self, slice_id):
        return self._state.get(slice_id)

    def all(self) -> dict:
        return {k: dict(v) for k, v in self._state.items()}

    # --- S4 crash-safe extensions --- #

    def mark_committed(self, slice_id, *, result):
        entry = self._state.setdefault(slice_id, {})
        entry["status"] = "committed"
        entry["result"] = result

    def is_committed(self, slice_id) -> bool:
        return self._state.get(slice_id, {}).get("status") == "committed"

    def record_attempt(self, slice_id, *, model_family) -> int:
        entry = self._state.setdefault(slice_id, {})
        entry["status"] = "started"
        entry["model_family"] = model_family
        entry["attempts"] = entry.get("attempts", 0) + 1
        return entry["attempts"]

    def mark_escalated(self, slice_id):
        entry = self._state.setdefault(slice_id, {})
        entry["status"] = "escalated"

    def capture_timing(self, slice_id, *, started_at=None, completed_at=None,
                       attended=None, slice_type=None):
        _merge_timing(self._state.setdefault(slice_id, {}), started_at,
                      completed_at, attended, slice_type)


# --------------------------------------------------------------------------- #
# S3 — atomic file-persistence primitives (replicated locally, NOT imported)
# --------------------------------------------------------------------------- #
# The two real adapters write into / beside files pre_plan_gates also owns, in
# pre_plan_gates' format. We deliberately do NOT import pre_plan_gates: a skill
# adapter must not couple to a hooks-layer module (code_first_architecture.md
# Dependency Direction / leakage protection). Instead the narrow path + atomic
# discipline are replicated here (mirrors pre_plan_gates._read_json /
# _write_json:336-348). "Reuse" holds at the format+pattern level; dependency
# direction holds at the import level.

def _read_json_or_none(path):
    """Read a JSON file; return None if absent (a missing surface = empty state)."""
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _atomic_write_json(path, data):
    """Write JSON atomically: serialize to a sibling temp file, then rename over
    the target. The rename is atomic on POSIX, so a crash mid-write leaves either
    the old file or the new one — never a half-written ("torn") file a later read
    could mistake for a later checkpoint (spine flagged torn-write assumption,
    THOUGHT line 177, discharged at the file primitive; full 3-checkpoint
    reconciliation is S4). Mirrors pre_plan_gates._write_json:343-348."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.parent / (p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    tmp.rename(p)


# --------------------------------------------------------------------------- #
# S6 — code-layer write verification (replicated locally, NOT imported)
# --------------------------------------------------------------------------- #
# `_verify_write` + `WriteVerificationError` are replicated from
# ${KIT_HOOKS_DIR}/taskmanagement.py:263-361. We deliberately do NOT import
# taskmanagement: a skill module must not couple to a hooks-layer module
# (code_first_architecture.md Dependency Direction / leakage protection). Same
# discipline as the S3 _read_json/_atomic_write_json replication and the S5
# _normalize_model_family replication. "Reuse" holds at the contract level;
# dependency direction holds at the import level.

class WriteVerificationError(Exception):
    """Raised when a re-read of a written surface does not match intent.

    LLM checkers MUST NOT be invoked from _verify_write (Guiding Policy 2).
    Replicated from taskmanagement.WriteVerificationError (NOT imported).
    """


def _verify_write(surface_kind, path, expected_payload, locator):
    """Re-read a written surface and structural-diff against expected_payload.

    Replicated from taskmanagement.verify_write:270-361 (NOT imported).

    surface_kind options:
      - "todo_line"        locator = regex pattern (str);
                           expected_payload = substring that must appear in the matched line.
      - "topic_state_json" locator = dotted key path (e.g., "phase" or "topics.X.phase");
                           expected_payload = expected value at that key.
      - "spine_section"    locator = (start_marker, end_marker) tuple of literal strings;
                           expected_payload = substring that must appear within the section.

    Raises WriteVerificationError with a human-readable diff on mismatch.
    Raises ValueError on unknown surface_kind.
    """
    p = Path(path)
    if not p.exists():
        raise WriteVerificationError(f"surface missing: {p}")
    text = p.read_text(encoding="utf-8")

    if surface_kind == "todo_line":
        if not isinstance(locator, str):
            raise WriteVerificationError(
                f"todo_line locator must be regex str; got {type(locator).__name__}"
            )
        match = re.search(locator, text, re.MULTILINE)
        if match is None:
            raise WriteVerificationError(
                f"todo_line locator {locator!r} matched nothing in {p}"
            )
        line = match.group(0)
        if expected_payload not in line:
            raise WriteVerificationError(
                f"todo_line mismatch in {p}\n"
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
            raise WriteVerificationError(
                f"topic_state_json invalid JSON in {p}: {e}"
            ) from e
        actual = data
        for k in locator.split("."):
            if not isinstance(actual, dict) or k not in actual:
                raise WriteVerificationError(
                    f"topic_state_json key path {locator!r} missing in {p}"
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
                f"spine_section start marker {start!r} missing in {p}"
            )
        ei = text.find(end, si)
        if ei < 0:
            raise WriteVerificationError(
                f"spine_section end marker {end!r} missing in {p}"
            )
        section = text[si:ei]
        if not isinstance(expected_payload, str):
            raise WriteVerificationError(
                f"spine_section expected_payload must be str; got {type(expected_payload).__name__}"
            )
        if expected_payload not in section:
            raise WriteVerificationError(
                f"spine_section missing expected substring in {p}\n"
                f"  expected: {expected_payload!r}\n"
                f"  section head: {section[:200]!r}"
            )
        return {"status": "OK", "section_len": len(section)}

    raise ValueError(f"unknown surface_kind: {surface_kind!r}")


def make_code_verify(verify_specs=(), code_check=None):
    """Build a `pre_commit_codecheck(s, attempt, result)` callable for
    `run_dispatch_loop` from a list of verify_write specs and an optional
    injected tests/lints callable.

    Each spec in `verify_specs` is a dict with keys:
        surface_kind, path, expected_payload, locator
    A list `locator` is converted to a tuple (for spine_section).

    The returned callable runs each spec through `_verify_write`, then calls
    `code_check(s, result)` if provided (expects `{ok: bool, detail}`), and
    raises `SliceAttemptError` on any failure — a hard-abort BEFORE the commit,
    routed into the 2-retry budget by the loop's existing retry-2x-then-escalate
    machinery.  Returns None when all checks pass (happy path invisible).

    Mirrors the `make_model_pin_verify` docstring/style (S5)."""
    def pre_commit_codecheck(s, attempt, result):
        for spec in verify_specs:
            locator = spec["locator"]
            # spine_section locator may arrive as a list from JSON; normalize.
            if isinstance(locator, list):
                locator = tuple(locator)
            try:
                _verify_write(
                    spec["surface_kind"],
                    spec["path"],
                    spec["expected_payload"],
                    locator,
                )
            except WriteVerificationError as e:
                raise SliceAttemptError(
                    f"code-layer verify_write failed for slice {s.id} "
                    f"(attempt {attempt}): {e}"
                ) from e
        if code_check is not None:
            check_result = code_check(s, result)
            if not (isinstance(check_result, dict) and check_result.get("ok")):
                detail = (check_result.get("detail") if isinstance(check_result, dict)
                          else str(check_result))
                raise SliceAttemptError(
                    f"code-layer code_check failed for slice {s.id} "
                    f"(attempt {attempt}): {detail}"
                )
    return pre_commit_codecheck


# --------------------------------------------------------------------------- #
# A1 (S3) — FullBookkeepingAdapter (Tier 2: topic-state JSON slice_execution)
# --------------------------------------------------------------------------- #

class FullBookkeepingAdapter(BookkeepingPort):
    """Tier-2 persistence: slice lifecycle lives in the topic-state JSON's
    `slice_execution` sub-map. Owns ONLY that sub-map (spine A13) — every write
    reads the whole JSON, mutates only `slice_execution`, and writes the whole
    JSON back, preserving every other key (phase, phase_history, …) byte-for-byte.
    Never touches git / branch / push (that is S7's unified contract). The active
    surface is fixed for the instance's lifetime — there is no method to switch to
    the Minimal surface mid-run (spine A3). S3 surface = started/completed only;
    the `committed` checkpoint is S4."""

    _SLICE_EXEC_KEY = "slice_execution"

    def __init__(self, state_path):
        self._path = Path(state_path)

    @classmethod
    def for_topic(cls, topic_slug, project_slug):
        """Resolve the canonical topic-state JSON path (mirrors
        pre_plan_gates._topic_path:332 — `<topic>__<project>.json` under
        ~/.claude/state/pre_plan_gates) without importing the hooks module."""
        canonical = (
            Path.home() / ".claude" / "state" / "pre_plan_gates"
            / f"{topic_slug}__{project_slug}.json"
        )
        return cls(canonical)

    def _load_exec(self):
        doc = _read_json_or_none(self._path) or {}
        return doc.get(self._SLICE_EXEC_KEY) or {}

    def _write_exec(self, execmap):
        # Read-modify-write the WHOLE doc so sibling keys are preserved (A13).
        doc = _read_json_or_none(self._path) or {}
        doc[self._SLICE_EXEC_KEY] = execmap
        _atomic_write_json(self._path, doc)

    def mark_started(self, slice_id, *, model_family):
        execmap = self._load_exec()
        entry = execmap.setdefault(slice_id, {})  # merge — preserve attempts (S4)
        entry["status"] = "started"
        entry["model_family"] = model_family
        self._write_exec(execmap)

    def mark_completed(self, slice_id, *, result):
        execmap = self._load_exec()
        entry = execmap.setdefault(slice_id, {})
        entry["status"] = "completed"
        entry["result"] = result
        self._write_exec(execmap)

    def is_completed(self, slice_id) -> bool:
        return self._load_exec().get(slice_id, {}).get("status") == "completed"

    def get(self, slice_id):
        return self._load_exec().get(slice_id)

    def all(self) -> dict:
        return {k: dict(v) for k, v in self._load_exec().items()}

    # --- S4 crash-safe extensions (still ONLY the slice_execution sub-map) --- #

    def mark_committed(self, slice_id, *, result):
        execmap = self._load_exec()
        entry = execmap.setdefault(slice_id, {})
        entry["status"] = "committed"
        entry["result"] = result
        self._write_exec(execmap)

    def is_committed(self, slice_id) -> bool:
        return self._load_exec().get(slice_id, {}).get("status") == "committed"

    def record_attempt(self, slice_id, *, model_family) -> int:
        execmap = self._load_exec()
        entry = execmap.setdefault(slice_id, {})
        entry["status"] = "started"
        entry["model_family"] = model_family
        entry["attempts"] = entry.get("attempts", 0) + 1
        self._write_exec(execmap)
        return entry["attempts"]

    def mark_escalated(self, slice_id):
        execmap = self._load_exec()
        entry = execmap.setdefault(slice_id, {})
        entry["status"] = "escalated"
        self._write_exec(execmap)

    def capture_timing(self, slice_id, *, started_at=None, completed_at=None,
                       attended=None, slice_type=None):
        execmap = self._load_exec()
        _merge_timing(execmap.setdefault(slice_id, {}), started_at,
                      completed_at, attended, slice_type)
        self._write_exec(execmap)


# --------------------------------------------------------------------------- #
# A2 (S3) — MinimalBookkeepingAdapter (Tier 1: plan-sibling run-state file)
# --------------------------------------------------------------------------- #

class MinimalBookkeepingAdapter(BookkeepingPort):
    """Tier-1 persistence: a self-contained plan-sibling run-state file
    (`<plan_stem>.run-state.json`). No pre_plan_gates dependency, no topic-state
    coupling — the standalone fallback surface for when the Full machinery is not
    the chosen tier. Same slice_id-keyed state shape as InMemoryBookkeepingAdapter
    so `run_dispatch_loop` reads it identically. The file is created on first write
    only; a missing file reads as empty state. The active surface is fixed for the
    instance's lifetime — no mid-run switch to the Full surface (spine A3). S3
    surface = started/completed only; the `committed` checkpoint is S4."""

    def __init__(self, state_path):
        self._path = Path(state_path)

    @classmethod
    def for_plan(cls, plan_path):
        """Derive the run-state file beside the plan:
        `<plan_dir>/<plan_stem>.run-state.json`."""
        p = Path(plan_path)
        return cls(p.parent / f"{p.stem}.run-state.json")

    def _load(self):
        return _read_json_or_none(self._path) or {}

    def mark_started(self, slice_id, *, model_family):
        state = self._load()
        entry = state.setdefault(slice_id, {})  # merge — preserve attempts (S4)
        entry["status"] = "started"
        entry["model_family"] = model_family
        _atomic_write_json(self._path, state)

    def mark_completed(self, slice_id, *, result):
        state = self._load()
        entry = state.setdefault(slice_id, {})
        entry["status"] = "completed"
        entry["result"] = result
        _atomic_write_json(self._path, state)

    def is_completed(self, slice_id) -> bool:
        return self._load().get(slice_id, {}).get("status") == "completed"

    def get(self, slice_id):
        return self._load().get(slice_id)

    def all(self) -> dict:
        return {k: dict(v) for k, v in self._load().items()}

    # --- S4 crash-safe extensions --- #

    def mark_committed(self, slice_id, *, result):
        state = self._load()
        entry = state.setdefault(slice_id, {})
        entry["status"] = "committed"
        entry["result"] = result
        _atomic_write_json(self._path, state)

    def is_committed(self, slice_id) -> bool:
        return self._load().get(slice_id, {}).get("status") == "committed"

    def record_attempt(self, slice_id, *, model_family) -> int:
        state = self._load()
        entry = state.setdefault(slice_id, {})
        entry["status"] = "started"
        entry["model_family"] = model_family
        entry["attempts"] = entry.get("attempts", 0) + 1
        _atomic_write_json(self._path, state)
        return entry["attempts"]

    def mark_escalated(self, slice_id):
        state = self._load()
        entry = state.setdefault(slice_id, {})
        entry["status"] = "escalated"
        _atomic_write_json(self._path, state)

    def capture_timing(self, slice_id, *, started_at=None, completed_at=None,
                       attended=None, slice_type=None):
        state = self._load()
        _merge_timing(state.setdefault(slice_id, {}), started_at,
                      completed_at, attended, slice_type)
        _atomic_write_json(self._path, state)


# --------------------------------------------------------------------------- #
# SpawnPort (execution boundary) + in-memory stand-in
# --------------------------------------------------------------------------- #

class SpawnPort(abc.ABC):
    """Execution boundary. The REAL implementation is not a Python class —
    Python cannot invoke the harness Agent tool — so the production spawn is
    driven from SKILL.md (real-to-real proof). S2 ships only the fake for the
    loop's test-to-test proof. S5 inserts post-dispatch transcript model
    verification at this seam."""

    @abc.abstractmethod
    def spawn(self, slice_id, model_family, prompt) -> dict:
        ...


class FakeSpawnAdapter(SpawnPort):
    """Records every spawn call and returns a canned result. Lets the loop be
    proven end-to-end with no real agent (test-to-test)."""

    def __init__(self, result=None):
        self.calls = []
        self._result = result

    def spawn(self, slice_id, model_family, prompt) -> dict:
        self.calls.append(
            {"slice_id": slice_id, "model_family": model_family, "prompt": prompt}
        )
        if self._result is not None:
            return self._result
        return {"status": "ok", "slice_id": slice_id, "model_family": model_family}


# --------------------------------------------------------------------------- #
# A7 (S5) — model-pin verification port (ModelPinPort): read the spawned agent's
# ACTUAL model family from the harness-written subagent transcript, so the loop
# can hard-abort BEFORE the `committed` checkpoint when a slice ran on the wrong
# model (spine A7 / Guiding-Policy commitment 4). The real adapter REPLICATES the
# transcript reader at _factcheck_engine.py:50-173 (it does NOT import it — a
# skill adapter must not couple to a hooks-layer module; same dependency-
# direction discipline as the S3 _read_json/_atomic_write_json replication). A
# fake exercises the mismatch hard-abort path with no real spawn + no transcript.
# The resolved slugs are the KEYS of the model-id map at
# _factcheck_engine.py:379-383 — reused, never reinvented (spine A8).
# --------------------------------------------------------------------------- #

# Mirrors _factcheck_engine.py:50 (replicated, not imported).
_MODEL_FAMILY_RE = re.compile(r"^claude-([a-z]+)-")


def _normalize_model_family(raw_model):
    """Map an API-attested model ID to its family slug, or None if unrecognized.

    Mirrors _factcheck_engine._normalize_model_family:53-67 (replicated, not
    imported). Examples: claude-sonnet-4-6 -> "sonnet"; claude-opus-4-7 -> "opus";
    claude-haiku-4-5-20251001 -> "haiku"."""
    if not isinstance(raw_model, str):
        return None
    m = _MODEL_FAMILY_RE.match(raw_model)
    return m.group(1) if m else None


class ModelPinPort(abc.ABC):
    """Verification boundary: read a spawned agent's ACTUAL model family from its
    harness-written transcript. The loop consults this at the `pre_commit_verify`
    seam (between the awaited `spawn` and the `committed` checkpoint) so a wrong-
    model run never reaches a commit (spine A7 / Guiding-Policy commitment 4).

    `resolve_used_family` returns a dict mirroring
    _factcheck_engine._resolve_subagent_transcript_model:
        {ok, family, raw_models, error, transcript_path}
    and NEVER raises — the caller (the verify builder) decides what to do; a
    resolution failure is treated as a mismatch (fail-closed)."""

    @abc.abstractmethod
    def resolve_used_family(self, *, session_id=None, agent_id=None,
                            transcript_path=None) -> dict:
        ...


class TranscriptModelPinAdapter(ModelPinPort):
    """Real adapter: read every assistant-turn `.message.model` in the harness-
    written subagent transcript and return its single resolved family. Replicates
    _factcheck_engine._resolve_subagent_transcript_model:70-173 (NOT imported —
    dependency direction; same discipline as the S3 _read_json replication).

    Locates the transcript at
        ~/.claude/projects/<session_id>/subagents/agent-<agent_id>.jsonl  (direct)
    or */<session_id>/subagents/agent-<agent_id>.jsonl                   (slug-rooted glob),
    OR reads an explicit `transcript_path` when given. Never raises."""

    def __init__(self, projects_root=None):
        self._projects_root = (Path(projects_root) if projects_root
                               else Path.home() / ".claude" / "projects")

    def _locate(self, session_id, agent_id, transcript_path):
        if transcript_path:
            return Path(transcript_path)
        direct = (self._projects_root / session_id / "subagents"
                  / f"agent-{agent_id}.jsonl")
        if direct.exists() or not self._projects_root.is_dir():
            return direct
        matches = list(self._projects_root.glob(
            f"*/{session_id}/subagents/agent-{agent_id}.jsonl"))
        return next((p for p in matches if p.exists()), direct)

    def resolve_used_family(self, *, session_id=None, agent_id=None,
                            transcript_path=None) -> dict:
        result = {"ok": False, "family": None, "raw_models": [],
                  "error": None, "transcript_path": None}
        if not transcript_path and not (session_id and agent_id):
            result["error"] = ("no transcript locator: need transcript_path or "
                               "(session_id, agent_id)")
            return result
        transcript = self._locate(session_id, agent_id, transcript_path)
        result["transcript_path"] = str(transcript)
        if not transcript.exists():
            result["error"] = f"transcript not found: {transcript}"
            return result
        raw_seen, raw_set, families = [], set(), set()
        try:
            with open(transcript, encoding="utf-8") as fh:
                for ln, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError as e:
                        result["error"] = f"transcript line {ln} not valid JSON: {e}"
                        return result
                    msg = obj.get("message") if isinstance(obj, dict) else None
                    if not isinstance(msg, dict) or msg.get("role") != "assistant":
                        continue
                    raw = msg.get("model")
                    if not isinstance(raw, str) or not raw:
                        result["error"] = (f"transcript line {ln}: assistant turn "
                                           "missing .message.model")
                        return result
                    if raw not in raw_set:
                        raw_set.add(raw)
                        raw_seen.append(raw)
                    fam = _normalize_model_family(raw)
                    if fam is None:
                        result["error"] = (f"transcript line {ln}: model id {raw!r} "
                                           "did not normalize to a known family")
                        result["raw_models"] = list(raw_seen)
                        return result
                    families.add(fam)
        except OSError as e:
            result["error"] = f"could not read transcript: {e}"
            return result
        result["raw_models"] = list(raw_seen)
        if not raw_seen:
            result["error"] = "transcript has no assistant turns with .message.model"
            return result
        if len(families) != 1:
            result["error"] = (f"transcript contains mixed model families "
                               f"{sorted(families)}; all assistant turns must "
                               "share one family")
            return result
        result["ok"] = True
        result["family"] = next(iter(families))
        return result


class FakeModelPinAdapter(ModelPinPort):
    """Records every query and returns a seeded family (or a seeded resolution
    failure), so the loop's hard-abort path is exercised with NO real spawn and
    NO transcript file. `by_agent` overrides per agent_id; otherwise `family`/`ok`
    drive the result. Used by the test-to-test proof of the seam wiring."""

    def __init__(self, family=None, *, ok=True, error=None, raw_models=None,
                 by_agent=None):
        self.family = family
        self.ok = ok
        self.error = error
        self.raw_models = (list(raw_models) if raw_models is not None
                           else ([] if family is None else [f"claude-{family}-4-7"]))
        self.by_agent = dict(by_agent or {})
        self.queries = []

    def resolve_used_family(self, *, session_id=None, agent_id=None,
                            transcript_path=None) -> dict:
        self.queries.append({"session_id": session_id, "agent_id": agent_id,
                             "transcript_path": transcript_path})
        if agent_id in self.by_agent:
            fam = self.by_agent[agent_id]
            return {"ok": True, "family": fam,
                    "raw_models": [f"claude-{fam}-4-7"], "error": None,
                    "transcript_path": transcript_path or f"<fake:{agent_id}>"}
        if not self.ok:
            return {"ok": False, "family": None, "raw_models": self.raw_models,
                    "error": self.error or "fake resolution failure",
                    "transcript_path": transcript_path or "<fake>"}
        return {"ok": True, "family": self.family, "raw_models": self.raw_models,
                "error": None, "transcript_path": transcript_path or "<fake>"}


def _spawn_result_locator(result):
    """Extract a transcript locator from a spawn result dict. Fail-closed: a
    result with no locator yields a locator-less dict the real adapter resolves to
    ok=False, which the verify builder treats as a mismatch (no commit)."""
    if not isinstance(result, dict):
        return {}
    return {
        "session_id": result.get("session_id"),
        "agent_id": result.get("agent_id"),
        "transcript_path": result.get("transcript_path"),
    }


def make_model_pin_verify(port):
    """Build a `pre_commit_verify(s, attempt, result)` callable for
    `run_dispatch_loop` from a ModelPinPort. The callable resolves the slice's
    EXPECTED family via agent_choice_to_model_family(s.agent_choice), reads the
    spawn's USED family from the transcript via the port, and raises
    SliceAttemptError on a MISMATCH or on an UNRESOLVABLE transcript (fail-closed)
    — a hard-abort BEFORE the `committed` checkpoint, routed into the 2-retry
    budget by the loop's existing retry-2x-then-escalate machinery (spine A7).
    On a match it returns None so the commit proceeds (happy path invisible)."""
    def verify(s, attempt, result):
        expected = agent_choice_to_model_family(s.agent_choice)
        resolved = port.resolve_used_family(**_spawn_result_locator(result))
        if not resolved.get("ok"):
            raise SliceAttemptError(
                f"model-pin: cannot verify used model for slice {s.id} "
                f"(attempt {attempt}): {resolved.get('error')}"
            )
        used = resolved.get("family")
        if used != expected:
            raise SliceAttemptError(
                f"model-pin MISMATCH on slice {s.id} (attempt {attempt}): "
                f"expected {expected!r}, transcript ran {used!r} "
                f"(raw: {resolved.get('raw_models')})"
            )
    return verify


# --------------------------------------------------------------------------- #
# S6 — model-layer conformance port (ConformancePort): wrap /double-check 3,0,1
# behind a port so the loop can hard-abort BEFORE `mark_completed` when a slice's
# work does not conform to its intent (spine A10 / Q5 / Guiding-Policy grading
# order). The REAL implementation reads a `/double-check 3,0,1` verdict — Python
# cannot invoke the Agent tool, so the real run is SKILL.md-driven (like
# SpawnPort). A fake exercises the fail-closed abort path with no real spawn.
#
# Verdict vocabulary (grounded in the LIVE /double-check SKILL.md):
#   PASS     — the skill's final "all checkers agree" verdict (ok=True)
#   ESCALATE — the skill's final "budget exhausted, non-convergent" verdict
#   DIRTY    — the convergence-engine label (factcheck-convergence.md §4);
#              NOT emitted by the live skill-text, but recognized here and in
#              tests (the locked-A10 fake vocabulary) — treated as non-PASS
#   DISCREPANCY — per-checker intermediate; never the final verdict but
#                 recognized and fail-closed for defensive completeness
# Any non-PASS (ESCALATE/DIRTY/DISCREPANCY/unrecognized/unparseable) -> ok=False.
# --------------------------------------------------------------------------- #

class ConformancePort(abc.ABC):
    """Verification boundary: read a /double-check 3,0,1 verdict and return
    whether the slice's work conforms to its intent.

    `check_conformance` wraps /double-check 3,0,1 (3 Sonnet, 0 Opus, 1 round).
    The real /double-check is SKILL.md-driven — Python cannot invoke the Agent
    tool, like SpawnPort. This port never raises; the caller decides what to do.

    Returns a dict:
        {ok: bool, verdict: str|None, source: "inline"|"file"|"fake"|None,
         error: str|None, verdict_path: str|None}
    """

    @abc.abstractmethod
    def check_conformance(self, *, slice_id, verdict=None,
                          verdict_path=None) -> dict:
        ...


class DoubleCheckConformanceAdapter(ConformancePort):
    """Real adapter: normalize a /double-check 3,0,1 verdict from an inline
    value OR a verdict file into {ok, verdict, source, error, verdict_path}.

    The verdict file may contain either a bare verdict token (e.g. "PASS") or
    a JSON object with a "verdict" key (e.g. {"verdict": "PASS", ...}).

    `ok = (verdict.strip().upper() == "PASS")`.  All non-PASS recognized
    verdicts (ESCALATE/DIRTY/DISCREPANCY) and any unparseable/missing input
    are fail-closed (ok=False, error set). NEVER raises."""

    # Recognized verdict tokens (for labelling; fail-closed on ALL non-PASS).
    _RECOGNIZED = {"PASS", "ESCALATE", "DIRTY", "DISCREPANCY"}

    def check_conformance(self, *, slice_id, verdict=None,
                          verdict_path=None) -> dict:
        result = {"ok": False, "verdict": None, "source": None,
                  "error": None, "verdict_path": verdict_path}
        raw = None
        if verdict is not None:
            raw = verdict
            result["source"] = "inline"
        elif verdict_path is not None:
            result["verdict_path"] = verdict_path
            try:
                text = Path(verdict_path).read_text(encoding="utf-8").strip()
            except OSError as e:
                result["error"] = f"cannot read verdict file {verdict_path!r}: {e}"
                return result
            # Try JSON first, then bare token.
            try:
                obj = json.loads(text)
                if isinstance(obj, dict) and "verdict" in obj:
                    raw = obj["verdict"]
                else:
                    raw = text
            except json.JSONDecodeError:
                raw = text
            result["source"] = "file"
        else:
            result["error"] = "no verdict provided (need verdict or verdict_path)"
            return result

        if not isinstance(raw, str) or not raw.strip():
            result["error"] = (f"verdict is not a non-empty string: {raw!r}")
            return result

        v = raw.strip().upper()
        result["verdict"] = v
        result["ok"] = (v == "PASS")
        if not result["ok"]:
            if v not in self._RECOGNIZED:
                result["error"] = (f"unrecognized verdict {v!r}; "
                                   f"expected one of {sorted(self._RECOGNIZED)}")
        return result


class FakeConformanceAdapter(ConformancePort):
    """Records every query and returns a canned verdict. `by_slice` overrides
    per slice_id; otherwise `verdict` drives the result. Mirrors the
    FakeModelPinAdapter style (S5)."""

    def __init__(self, verdict="PASS", *, by_slice=None):
        self._verdict = verdict
        self._by_slice = dict(by_slice or {})
        self.queries = []

    def check_conformance(self, *, slice_id, verdict=None,
                          verdict_path=None) -> dict:
        self.queries.append({"slice_id": slice_id, "verdict": verdict,
                             "verdict_path": verdict_path})
        v = self._by_slice.get(slice_id, self._verdict)
        return {
            "ok": v.upper() == "PASS",
            "verdict": v.upper(),
            "source": "fake",
            "error": None,
            "verdict_path": None,
        }


def make_conformance_check(port):
    """Build a `post_commit_conformance(s, attempt, result)` callable for
    `run_dispatch_loop` from a ConformancePort.

    The callable reads `result.get("conformance_verdict")` and
    `result.get("conformance_verdict_path")` (guarded for non-dict result),
    calls `port.check_conformance(...)`, and raises `SliceAttemptError` when
    the result is not `ok` (fail-closed — DIRTY/ESCALATE/unparseable are all
    treated as abort-before-completion, routed into S4's 2-retry budget).
    Returns None on PASS (happy path invisible).

    Mirrors the `make_model_pin_verify` docstring/style (S5)."""
    def post_commit_conformance(s, attempt, result):
        if isinstance(result, dict):
            cv = result.get("conformance_verdict")
            cvp = result.get("conformance_verdict_path")
        else:
            cv = None
            cvp = None
        resolved = port.check_conformance(
            slice_id=s.id, verdict=cv, verdict_path=cvp
        )
        if not resolved.get("ok"):
            verdict_label = resolved.get("verdict") or "UNKNOWN"
            error_detail = resolved.get("error") or ""
            msg = (f"conformance non-PASS for slice {s.id} (attempt {attempt}): "
                   f"verdict={verdict_label!r}")
            if error_detail:
                msg += f" — {error_detail}"
            raise SliceAttemptError(msg)
    return post_commit_conformance


# --------------------------------------------------------------------------- #
# A2 (S4) — idempotent commit guard (CommitGuardPort) + commit-creation boundary
# (CommitPort). The guard READS git history to answer "already committed?"; it
# NEVER creates a commit. Commit CREATION + the unified git contract are S7 — S4
# ships only the FakeCommitAdapter stand-in behind CommitPort.
# --------------------------------------------------------------------------- #

class CommitGuardPort(abc.ABC):
    """Idempotency boundary: is the slice's commit already on the branch? The
    loop consults this BEFORE creating a commit so a resume/retry that re-runs an
    already-committed slice never double-commits (A6/A12)."""

    @abc.abstractmethod
    def commit_exists(self, slice_id) -> bool:
        ...


class GitCommitGuard(CommitGuardPort):
    """Real guard: a slice_id-prefixed commit already on the branch?
    Reads via `git log --grep '^<slice_id>:' --format=%H` and returns whether any
    matching commit exists. LOCK-FREE by design — git history is the source of
    truth under the single-machine + serialize-v1 boundary (spine flagged
    assumption, THOUGHT:176; would need revisiting under v2 parallelism, F8).

    Takes its repo dir + a subprocess `runner` via constructor so tests inject a
    fake runner (no real git). The runner is called with the argv list and must
    return an object with a `.stdout` string (the `subprocess.run` shape)."""

    def __init__(self, repo_dir=None, runner=None):
        self._repo_dir = str(repo_dir) if repo_dir else None
        self._runner = runner if runner is not None else self._default_runner

    @staticmethod
    def _default_runner(args):
        import subprocess
        return subprocess.run(args, capture_output=True, text=True)

    def commit_exists(self, slice_id) -> bool:
        args = ["git"]
        if self._repo_dir:
            args += ["-C", self._repo_dir]
        args += ["log", "--grep", f"^{slice_id}:", "--format=%H"]
        result = self._runner(args)
        return bool((getattr(result, "stdout", "") or "").strip())


class FakeCommitGuard(CommitGuardPort):
    """Records every query and reports membership of a seeded set. Lets the loop
    be proven test-to-test, and lets a resume test assert no double-commit by
    seeding the already-committed slice id."""

    def __init__(self, existing=()):
        self._existing = set(existing)
        self.queries = []

    def commit_exists(self, slice_id) -> bool:
        self.queries.append(slice_id)
        return slice_id in self._existing


class CommitPort(abc.ABC):
    """Commit-creation boundary. The REAL implementation — a slice_id-prefixed
    git commit governed by the unified git contract — is S7; S4 ships only the
    fake so the loop's commit step + the idempotent guard can be proven."""

    @abc.abstractmethod
    def create_commit(self, slice_id, *, result, paths=()) -> dict:
        """`paths` is the slice's DECLARED publish scope (its register
        `write_targets`). The real adapter refuses an empty declaration rather
        than widening to the whole tree (glittery-humming-pine A7)."""
        ...


class FakeCommitAdapter(CommitPort):
    """Records every create_commit call and returns a canned receipt. The loop's
    single-commit invariant (guard prevents a second commit on retry/resume) is
    asserted against `calls`."""

    def __init__(self):
        self.calls = []

    def create_commit(self, slice_id, *, result, paths=()) -> dict:
        self.calls.append({"slice_id": slice_id, "result": result,
                           "paths": list(paths or ())})
        return {"status": "committed", "slice_id": slice_id}


# --------------------------------------------------------------------------- #
# S7 — Unified git contract (ABOVE the adapter layer) + real commit adapter +
# end-of-plan push gate. The contract is the SINGLE source of truth for commit
# format / branch policy / push gating that applies equally to the orchestrator,
# /close, and all commit-producing skills (spine A13/Q6). BookkeepingPort
# adapters own INTERNAL state only — they never touch this. The commit-format
# prefix is load-bearing: GitCommitGuard (above) greps `^<slice_id>:`, so the
# format the adapter writes and the prefix the guard reads derive from ONE place
# (eliminating drift). Cockburn 4-step: the contract is pure (no I/O);
# GitCommitAdapter mirrors GitCommitGuard's injectable-runner shape and is proven
# test-to-test (fake runner) -> real-to-test (a shared fake-git world with the
# guard) -> real-to-real (an isolated temp git repo) since git IS runnable from
# Python. The real `git push` writes to a REMOTE (outward, hard-to-reverse), so
# real-to-real push stays the SKILL.md operator-confirmed path (like SpawnPort) —
# GitPushAdapter is proven test-to-real only.
# --------------------------------------------------------------------------- #

class GitContractError(Exception):
    """A unified-git-contract violation: a malformed commit subject, a write to a
    protected branch, or a refused/failed push. Deliberately NOT a
    SliceAttemptError — a contract violation is a hard configuration error, not a
    transient slice failure, so the loop's `except SliceAttemptError` does NOT
    absorb it and it is NEVER routed into S4's retry budget (retrying cannot
    change the branch or fix the format)."""


@dataclass(frozen=True)
class PushDecision:
    """The end-of-plan push gate's verdict (spine A14). `should_push` is the AND
    of all gate conditions; `partial_prompt` carries the operator-facing push-
    partial message when push is withheld (a mid-plan abort or any unmet
    condition). Frozen — a decision is a value, not mutable state."""
    should_push: bool
    reason: str
    partial_prompt: str = None


class UnifiedGitContract:
    """The single git contract ABOVE the adapter layer (spine A13/Q6). Pure
    policy — no I/O, no subprocess. Adapters OBEY it; they never own it. The
    orchestrator, /close, and every commit-producing skill share these same rules
    (skills replicate, not import — dependency direction); the BookkeepingPort
    adapters touch none of it."""

    # Branch policy: orchestrated slice work never commits/pushes directly onto a
    # protected (default/integration) branch. Branch *management* (create/switch)
    # stays operator-owned (spine A20) — the contract states only the POLICY the
    # adapter enforces, never managing branches itself.
    PROTECTED_BRANCHES = ("main", "master")

    @staticmethod
    def is_valid_commit_id(commit_id):
        r"""True for an `S\d+` slice-commit id OR a `P\d+` /plan-detour-commit id
        (NS6, design #11). Two distinct id families under ONE git contract; the
        register parser still recognizes only `S\d+` as a slice."""
        return bool(
            isinstance(commit_id, str)
            and (_SLICE_ID_RE.fullmatch(commit_id)
                 or _PLAN_COMMIT_ID_RE.fullmatch(commit_id))
        )

    @staticmethod
    def format_commit_subject(commit_id, summary):
        r"""Commit format: `<commit_id>: <summary>`. The `<commit_id>:` prefix is
        the load-bearing single source shared with GitCommitGuard's `^<commit_id>:`
        grep (idempotency). NS6: the id may be an `S\d+` slice-commit OR a `P\d+`
        /plan-detour-commit — both admitted by the SAME contract via the separate
        `_PLAN_COMMIT_ID_RE`. Any other id or an empty/whitespace summary is a
        GitContractError — no silent default (a malformed commit must never land)."""
        if not UnifiedGitContract.is_valid_commit_id(commit_id):
            raise GitContractError(
                f"commit subject needs an S<digits> slice id or a P<digits> "
                f"detour-commit id; got {commit_id!r}"
            )
        if not (isinstance(summary, str) and summary.strip()):
            raise GitContractError(
                f"commit subject needs a non-empty summary; got {summary!r}"
            )
        return f"{commit_id}: {summary.strip()}"

    @classmethod
    def is_protected_branch(cls, branch):
        """Branch policy predicate: is `branch` a protected/default branch onto
        which orchestrated slice work must NOT be committed/pushed?"""
        return branch in cls.PROTECTED_BRANCHES

    @staticmethod
    def evaluate_push_gate(*, all_slices_done, work_done_succeeded,
                           git_status_clean, aborted=False):
        """End-of-plan push gate (spine A14): push only when all_slices_done AND
        work_done_succeeded AND git_status_clean. A mid-plan abort (or ANY unmet
        condition) yields should_push=False with an operator-facing push-partial
        prompt instead of an automatic push. Pure — returns a PushDecision; it
        does not push."""
        if aborted:
            return PushDecision(
                should_push=False, reason="run aborted mid-plan",
                partial_prompt=(
                    "Run aborted mid-plan — push withheld. Review the committed "
                    "slices and push manually when ready."),
            )
        unmet = []
        if not all_slices_done:
            unmet.append("not all slices are done")
        if not work_done_succeeded:
            unmet.append("closing /work-done did not succeed")
        if not git_status_clean:
            unmet.append("git status is not clean")
        if unmet:
            joined = "; ".join(unmet)
            return PushDecision(
                should_push=False, reason=joined,
                partial_prompt=(
                    f"Push withheld — {joined}. Resolve and push manually, or "
                    "re-run the end-of-plan push gate."),
            )
        return PushDecision(should_push=True,
                            reason="all push-gate conditions met")


class GitCommitAdapter(CommitPort):
    """Real CommitPort (S7): create the slice_id-prefixed git commit governed by
    the UnifiedGitContract. Mirrors GitCommitGuard's injectable-runner shape
    (constructor repo_dir + runner) so tests drive it with a fake runner and no
    real git. OBEYS the contract (commit format + branch policy); does NOT own
    it. Stages + commits ONLY — it NEVER pushes (push is the end-of-plan gate's
    job, A14). The FakeCommitAdapter stand-in is retained for the loop's
    test-to-test proofs."""

    def __init__(self, repo_dir=None, runner=None, contract=UnifiedGitContract):
        self._repo_dir = str(repo_dir) if repo_dir else None
        self._runner = runner if runner is not None else self._default_runner
        self._contract = contract

    @staticmethod
    def _default_runner(args):
        import subprocess
        return subprocess.run(args, capture_output=True, text=True)

    def _git(self, *args):
        argv = ["git"]
        if self._repo_dir:
            argv += ["-C", self._repo_dir]
        argv += list(args)
        return self._runner(argv)

    def _current_branch(self):
        res = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return (getattr(res, "stdout", "") or "").strip()

    def create_commit(self, slice_id, *, result, paths=()) -> dict:
        """Slice commit over the slice's DECLARED paths only. There is no
        whole-tree branch any more: an undeclared slice raises rather than
        sweeping, and an under-declared slice under-publishes — its extra files
        stay dirty, which `evaluate_push_gate` already refuses on."""
        summary = result.get("commit_summary") if isinstance(result, dict) else None
        if not summary:
            summary = f"slice {slice_id} implementation"
        receipt = self._scoped_commit(slice_id, summary, paths)
        receipt["slice_id"] = slice_id
        return receipt

    def create_detour_commit(self, commit_id, *, paths, summary):
        """Path-scoped `/plan`-detour commit (NS6, design #11) with a `P<N>:`
        subject under the SAME UnifiedGitContract. Used to file an approved plan
        during a plan-detour return, before /close."""
        receipt = self._scoped_commit(commit_id, summary, paths)
        receipt["commit_id"] = commit_id
        return receipt

    # -- the ONE scoped publish both commit paths go through ---------------- #

    @staticmethod
    def _clean_target(token):
        """A register cell token as written (`\\`skills/x.py\\``) → a plain path."""
        return str(token).strip().strip("`").strip()

    def _repo_root(self):
        root = self._git("rev-parse", "--show-toplevel")
        out = (getattr(root, "stdout", "") or "").strip()
        if getattr(root, "returncode", 0) == 0 and out and os.path.isabs(out):
            return out
        return os.path.abspath(self._repo_dir) if self._repo_dir else os.getcwd()

    def _declared_rels(self, commit_id, paths):
        """Normalize a declaration to repo-root-relative file paths, refusing the
        forms that silently un-scope themselves: empty, caller pathspec magic,
        a path outside the repo, and a directory (which sweeps every concurrent
        session's file beneath it even under `:(literal)`)."""
        tokens = [self._clean_target(p) for p in (paths or ())]
        tokens = [t for t in tokens if t and t not in _EMPTY_DEP_TOKENS]
        if not tokens:
            raise GitContractError(
                f"commit {commit_id} declares no paths — a commit must name what "
                "it publishes (never `git add -A`). Declare the slice's Write "
                "targets in the register, or pass `paths`.")
        root = None
        rels, bad = [], []
        for t in tokens:
            if t.startswith(":"):
                bad.append(f"{t} (caller-supplied pathspec magic)")
                continue
            if os.path.isabs(os.path.expanduser(t)):
                root = root or self._repo_root()
                rel = os.path.relpath(os.path.abspath(os.path.expanduser(t)), root)
                if rel == ".." or rel.startswith(".." + os.sep):
                    bad.append(f"{t} (outside the repository)")
                    continue
            else:
                rel = os.path.normpath(t)
                if rel == ".." or rel.startswith(".." + os.sep) or rel == ".":
                    bad.append(f"{t} (not a file inside the repository)")
                    continue
            root = root or self._repo_root()
            if os.path.isdir(os.path.join(root, rel)):
                bad.append(f"{t} (directory)")
                continue
            rels.append(rel)
        if bad:
            raise GitContractError(
                f"commit {commit_id} refuses a declared path that is not a single "
                "file in this repo: " + ", ".join(bad) + ". Each of these would "
                "sweep other sessions' files; declare individual files instead.")
        return sorted(set(rels))

    def _announce_co_writers(self, rels):
        """C9 — before committing a shared append file (a `merge_union: true`
        manifest path such as TODO.md / Diary / Stats), say which OTHER live
        sessions also wrote it: their lines will ride in this commit. Advisory
        only; an unavailable hooks module degrades to a stderr note, never a
        failure (the commit itself is already correctly scoped)."""
        try:
            hooks_dir = str(Path(__file__).resolve().parents[2] / "hooks")
            if hooks_dir not in sys.path:
                sys.path.insert(0, hooks_dir)
            import bookkeeping_paths as _bp
            import commit_scope as _cs
        except Exception as e:  # noqa: BLE001 — advisory path
            print(f"[execute-plan] co-writer check unavailable: {e}", file=sys.stderr)
            return {}
        shared = [r for r in rels
                  if (_bp.match_entry(r) or {}).get("merge_union")]
        if not shared:
            return {}
        cwd = Path(self._repo_dir) if self._repo_dir else None
        try:
            joint = _cs.co_writers(shared, cwd=cwd)
        except Exception as e:  # noqa: BLE001
            print(f"[execute-plan] co-writer check failed: {e}", file=sys.stderr)
            return {}
        for path, others in joint.items():
            print(f"[execute-plan] NOTE: {path} was also written by "
                  f"{len(others)} other live session(s): {', '.join(others)}. "
                  "Committing it will carry their lines too — expected for a "
                  "shared append file.", file=sys.stderr)
        return joint

    def _scoped_commit(self, commit_id, summary, paths):
        """Branch policy → subject format → declared paths → co-writer notice →
        `git add -A -- <paths>` → `git commit -m <subject> -- <paths>`.

        BOTH verbs are scoped. A scoped add followed by a bare commit still
        commits everything any session has staged — the exact defect this
        adapter's detour path carried. The add runs first because a pathspec
        commit rejects an untracked file. `:(top,literal)` makes each declared
        path repo-root-relative and turns glob magic off, so `[care] x.pdf`
        commits that file and `*.md` matches nothing rather than everything."""
        branch = self._current_branch()
        if self._contract.is_protected_branch(branch):
            raise GitContractError(
                f"refusing to commit {commit_id} onto protected branch "
                f"{branch!r} (unified git contract branch policy; branch "
                "management is operator-owned)"
            )
        subject = self._contract.format_commit_subject(commit_id, summary)
        rels = self._declared_rels(commit_id, paths)
        joint = self._announce_co_writers(rels)
        specs = [f":(top,literal){r}" for r in rels]
        add = self._git("add", "-A", "--", *specs)
        if getattr(add, "returncode", 0) != 0:
            raise GitContractError(
                f"git add failed for {commit_id}: "
                f"{(getattr(add, 'stderr', '') or '').strip()!r}"
            )
        commit = self._git("commit", "-m", subject, "--", *specs)
        if getattr(commit, "returncode", 0) != 0:
            blob = ((getattr(commit, "stdout", "") or "")
                    + (getattr(commit, "stderr", "") or "")).strip()
            # The add already ran: the declared paths sit staged in the shared
            # index. Report them rather than unwinding — `git reset -- <paths>`
            # would also discard a concurrent session's staging of the same path.
            raise GitContractError(
                f"git commit failed for {commit_id}: {blob!r}. The declared "
                f"path(s) {rels} are staged and UNCOMMITTED; re-run once fixed, "
                "or unstage them with `git restore --staged -- <paths>` (never a "
                "bare `git reset`, which would unstage other sessions' work)."
            )
        sha_res = self._git("rev-parse", "HEAD")
        sha = (getattr(sha_res, "stdout", "") or "").strip()
        return {"status": "committed", "subject": subject, "branch": branch,
                "sha": sha, "paths": rels, "co_writers": joint}


class PushPort(abc.ABC):
    """End-of-plan push boundary (S7). Performs the actual `git push` ONLY when
    the unified contract's push gate allowed it (A14). The real adapter mirrors
    the injectable-runner shape; a fake records calls for the test-to-test
    proof."""

    @abc.abstractmethod
    def push(self, decision) -> dict:
        ...


class GitPushAdapter(PushPort):
    """Real PushPort: run `git push` when (and ONLY when) the PushDecision says
    so — fail-closed double-gate (a should_push=False decision NEVER pushes and
    returns a 'withheld' receipt carrying the push-partial prompt). Pushing to a
    remote is outward + hard-to-reverse, so the end-of-plan gate (evaluated above
    the adapter) plus operator confirmation (SKILL.md) are the authority; this
    adapter only executes an already-granted, already-confirmed decision."""

    def __init__(self, repo_dir=None, runner=None):
        self._repo_dir = str(repo_dir) if repo_dir else None
        self._runner = runner if runner is not None else self._default_runner

    @staticmethod
    def _default_runner(args):
        import subprocess
        return subprocess.run(args, capture_output=True, text=True)

    def push(self, decision) -> dict:
        if not decision.should_push:
            return {"status": "withheld", "pushed": False,
                    "reason": decision.reason,
                    "partial_prompt": decision.partial_prompt}
        argv = ["git"]
        if self._repo_dir:
            argv += ["-C", self._repo_dir]
        argv += ["push"]
        res = self._runner(argv)
        if getattr(res, "returncode", 0) != 0:
            raise GitContractError(
                f"git push failed: {(getattr(res, 'stderr', '') or '').strip()!r}"
            )
        return {"status": "pushed", "pushed": True,
                "stdout": (getattr(res, "stdout", "") or "").strip()}


class FakePushAdapter(PushPort):
    """Records every push call and honors the decision's fail-closed contract
    without touching git. Mirrors the FakeCommitAdapter/FakeSpawnAdapter style."""

    def __init__(self):
        self.calls = []

    def push(self, decision) -> dict:
        self.calls.append({"should_push": decision.should_push,
                           "reason": decision.reason})
        if not decision.should_push:
            return {"status": "withheld", "pushed": False,
                    "reason": decision.reason,
                    "partial_prompt": decision.partial_prompt}
        return {"status": "pushed", "pushed": True}


# --------------------------------------------------------------------------- #
# A2 — slice-register parser (spine B2 `#### Slices` pipe-table -> [Slice])
# --------------------------------------------------------------------------- #

_SLICE_ID_RE = re.compile(r"^S\d+$")
# NS6 (v2) — a SEPARATE commit-id regex for `/plan`-detour commits (design #11).
# Kept DISTINCT from `_SLICE_ID_RE` so the register parser (which uses
# `_SLICE_ID_RE` to recognize slice rows, line ~1407) is UNCHANGED — a `P<N>` is
# never a slice — while the UnifiedGitContract's commit-subject guard admits both.
_PLAN_COMMIT_ID_RE = re.compile(r"^P\d+$")
_EMPTY_DEP_TOKENS = {"", "-", "—", "–", "none", "n/a"}


def _parse_depends_on(cell):
    """Parse the 'Depends on' cell -> tuple of slice ids. '—'/blank -> ()."""
    out = []
    for tok in cell.replace(";", ",").split(","):
        tok = tok.strip()
        if tok.lower() in _EMPTY_DEP_TOKENS:
            continue
        out.append(tok)
    return tuple(out)


_CONFIRM_TRUE_TOKENS = {"yes", "true", "confirm", "risky", "y", "1", "x", "✓"}


def _parse_confirm_override(cell):
    """Parse an optional 'Confirm' register column -> bool. Truthy tokens
    (yes/true/confirm/risky/x/checkmark) -> True; blank/-/no/false -> False (S8).
    AI-promotes-only: this is the slicer's Layer-2 flag, consumed verbatim."""
    return str(cell).strip().lower() in _CONFIRM_TRUE_TOKENS


def _parse_dispatch_cell(cell):
    """Parse an optional 'Dispatch' register column (NS1, position 8) -> a
    dispatch value or None. Blank/-/—/none -> None (resolve_dispatch then applies
    the confirm_override alias or the conservative default). An explicit unknown
    token is preserved VERBATIM so resolve_dispatch raises on it at dispatch time
    (no silent default — consume the register verbatim, GP3)."""
    v = str(cell).strip()
    if v.lower() in _EMPTY_DEP_TOKENS:
        return None
    return v


def _parse_write_targets_cell(cell):
    """Parse an optional 'Write targets' register column (NS10, position 9) ->
    tuple of target tokens. Comma/semicolon-separated; '—'/blank -> () (the
    empty default that preserves the original DAG behavior)."""
    out = []
    for tok in str(cell).replace(";", ",").split(","):
        tok = tok.strip()
        if tok.lower() in _EMPTY_DEP_TOKENS:
            continue
        out.append(tok)
    return tuple(out)


# --------------------------------------------------------------------------- #
# A1 (S1) — pointer-aware + format-tolerant slice-register reader
#
# Two facets fixed at the reader (design DECIDED — do not re-litigate):
#   (facet 2) format-tolerance: recognize a register by its COLUMNS (an id-col AND
#             a depends-col — the DAG discriminator), not by a positional
#             `len(cells)>=6` + `^S\d+$` filter. Alias-resolves varied column
#             layouts, bold/backticked headers, sub-slice ids (S-final, S4a) and
#             the `**S1**` bold-id form; still preserves the additive Confirm/
#             Dispatch/Write-targets columns the live walk consumes (labeled OR at
#             their canonical positions 6/7/8).
#   (facet 1) pointer-following: `resolve_register_source` follows a plan's
#             `slice_register_ref:` pointer to the spine when the plan text carries
#             no inline register — a broken pointer is LOUD, never silently treated
#             as single-step.
# --------------------------------------------------------------------------- #

_REGISTER_HEADER_RE = re.compile(r"^#{2,4}\s*.*\bslices?\b.*$", re.IGNORECASE)
_MD_HEADER_RE = re.compile(r"^(#{1,6})\s+\S")
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_SLICE_REGISTER_REF_RE = re.compile(r"^\s*slice_register_ref:\s*(\S+)")

_ID_COL_ALIASES = {"id", "#", "slice id", "sliceid"}
_DEPENDS_COL_ALIASES = {"depends on", "depends_on", "depends", "deps", "dependencies"}
_MODEL_COL_ALIASES = {"model", "agent", "agent_choice", "agent choice"}
_NAME_COL_ALIASES = {"name", "title"}
_IDEA_COL_ALIASES = {"idea", "slicing idea", "slicing_idea", "slice idea"}
_CONFIRM_COL_ALIASES = {"confirm", "confirm_override"}
_WRITE_TARGETS_COL_ALIASES = {"write targets", "write_targets", "writes"}


def _split_row(line):
    """Split a pipe-table row into stripped cells (canonical, unchanged shape)."""
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _norm_col(cell):
    """Normalize a header cell for alias matching: strip whitespace + surrounding
    ** / backticks, lowercase."""
    v = str(cell).strip()
    for _ in range(3):
        v = v.strip().strip("*").strip("`")
    return v.strip().lower()


def _norm_id(cell):
    """Normalize a slice-id cell: strip whitespace + surrounding ** / backticks,
    but KEEP the id shape (sub-slice ids like S-final, S4a and `**S1**`->`S1`)."""
    v = str(cell).strip()
    for _ in range(3):
        v = v.strip().strip("*").strip("`")
    return v.strip()


def _first_col_idx(norm_cells, aliases):
    for i, n in enumerate(norm_cells):
        if n in aliases:
            return i
    return None


def _resolve_register_columns(header_cells):
    """Map a candidate register's column-header row to a dict of column indices by
    role. id + depends are the RECOGNITION discriminator (both must be present)."""
    norm = [_norm_col(c) for c in header_cells]
    id_idx = _first_col_idx(norm, _ID_COL_ALIASES)
    slice_used_as_id = False
    if id_idx is None:
        id_idx = _first_col_idx(norm, {"slice"})
        slice_used_as_id = id_idx is not None
    name_idx = _first_col_idx(norm, _NAME_COL_ALIASES)
    if name_idx is None and not slice_used_as_id:
        name_idx = _first_col_idx(norm, {"slice"})
    return {
        "id": id_idx,
        "depends": _first_col_idx(norm, _DEPENDS_COL_ALIASES),
        "type": _first_col_idx(norm, {"type"}),
        "model": _first_col_idx(norm, _MODEL_COL_ALIASES),
        "name": name_idx,
        "idea": _first_col_idx(norm, _IDEA_COL_ALIASES),
        "confirm": _first_col_idx(norm, _CONFIRM_COL_ALIASES),
        "dispatch": _first_col_idx(norm, {"dispatch"}),
        "write_targets": _first_col_idx(norm, _WRITE_TARGETS_COL_ALIASES),
    }


def _pipe_table_blocks(lines, lo=0, hi=None):
    """Return contiguous blocks of pipe-table rows (each a list of '|...'-lines)
    within lines[lo:hi]. A block is bounded by any non-pipe line."""
    if hi is None:
        hi = len(lines)
    blocks, cur = [], []
    for i in range(lo, hi):
        stripped = lines[i].strip()
        if stripped.startswith("|"):
            cur.append(stripped)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _cell(cells, idx):
    """Stripped cell value at a mapped column index, or None when unmapped/absent."""
    if idx is None or idx < 0 or idx >= len(cells):
        return None
    return cells[idx].strip()


# The Type-cell spellings that mean "the closing implementation-verification
# slice". `implementation_verification` is the solution-slicer enum
# (`~/.claude/skills/solution-slicer/SKILL.md` — "type: implementation or
# implementation_verification"); `closing` is the plain-language form the
# `| Slice | Type | ...` register template carries alongside `work`, and it is
# what at least four landed plans wrote. The parser was permissive on the WORK
# side (`work`, `implementation`, anything else all counted as non-closing) and
# strict only on the closing side, so a `closing`-typed S-final was silently
# counted as a third work slice — `present: true, non_closing_count: 3` on a
# two-work-slice plan — and an armed walk would have tried to implement the
# verification slice as ordinary work. One predicate, consulted everywhere the
# distinction is drawn.
CLOSING_SLICE_TYPES = frozenset({"implementation_verification", "closing"})


def _is_closing_type(type_cell):
    return (type_cell or "").strip().lower() in CLOSING_SLICE_TYPES


def _recognize_from_block(block):
    """Given a contiguous pipe-table block, return a register-detail dict if it is
    a RECOGNIZED register (has an id-col AND a depends-col — the DAG discriminator);
    otherwise None. This is what excludes the A#-Coherent-Actions table and a
    prose 2-col Aspect|Detail table even when a nearby header contains 'slice'."""
    if not block:
        return None
    cols = _resolve_register_columns(_split_row(block[0]))
    if cols["id"] is None or cols["depends"] is None:
        return None  # DISCRIMINATOR: not a register

    slices = []
    raw_row_count = 0
    raw_non_closing = 0
    for row in block[1:]:
        cells = _split_row(row)
        nonempty = [c for c in cells if c != ""]
        if nonempty and all(_SEPARATOR_CELL_RE.match(c) for c in nonempty):
            continue  # separator row (|----|----|)
        # A `P<N>` id is a plan-detour COMMIT id, never a slice (design #11) —
        # skip it entirely (not a slice row, not counted as a raw candidate).
        _id_probe = _norm_id(cells[cols["id"]]) if cols["id"] < len(cells) else ""
        if _PLAN_COMMIT_ID_RE.fullmatch(_id_probe):
            continue
        raw_row_count += 1
        type_idx = cols["type"]
        type_cell = _cell(cells, type_idx) or ""
        if not _is_closing_type(type_cell):
            raw_non_closing += 1
        # VALID-SLICE FLOOR: non-empty id AND a present, non-empty type cell.
        id_val = _norm_id(cells[cols["id"]]) if cols["id"] < len(cells) else ""
        if not id_val:
            continue
        if type_idx is None or not type_cell:
            continue
        # Additive cols 7-9: labeled column if mapped, else the canonical position.
        if cols["confirm"] is not None and cols["confirm"] < len(cells):
            confirm_override = _parse_confirm_override(cells[cols["confirm"]])
        elif len(cells) >= 7:
            confirm_override = _parse_confirm_override(cells[6])
        else:
            confirm_override = False
        if cols["dispatch"] is not None and cols["dispatch"] < len(cells):
            dispatch = _parse_dispatch_cell(cells[cols["dispatch"]])
        elif len(cells) >= 8:
            dispatch = _parse_dispatch_cell(cells[7])
        else:
            dispatch = None
        if cols["write_targets"] is not None and cols["write_targets"] < len(cells):
            write_targets = _parse_write_targets_cell(cells[cols["write_targets"]])
        elif len(cells) >= 9:
            write_targets = _parse_write_targets_cell(cells[8])
        else:
            write_targets = ()
        slices.append(
            Slice(
                id=id_val,
                name=_cell(cells, cols["name"]) or "",
                type=type_cell,
                agent_choice=_cell(cells, cols["model"]) or "",
                depends_on=_parse_depends_on(_cell(cells, cols["depends"]) or ""),
                confirm_override=confirm_override,
                dispatch=dispatch,
                write_targets=write_targets,
            )
        )
    valid_non_closing = sum(
        1 for s in slices if not _is_closing_type(s.type)
    )
    return {
        "slices": slices,
        "recognized": True,
        "raw_row_count": raw_row_count,
        "raw_non_closing_count": raw_non_closing,
        "valid_non_closing_count": valid_non_closing,
        "pointer_ref": None,
    }


def parse_slice_register_detail(text):
    """Format-tolerant register reader (facet 2). Returns the detail dict:
      {slices, recognized, raw_row_count, raw_non_closing_count,
       valid_non_closing_count, pointer_ref}.
    A register is recognized by its COLUMNS (id-col AND depends-col), not a
    positional `^S\\d+$` filter. Header-bounded scan first (a `## Slices`-style
    header + the discriminator); a headerless pipe table with the discriminator is
    the backward-compatible fallback. `raw_*` counts are the malformed-multistep
    signal downstream slices consume (a recognized register with mostly-invalid
    rows is a DIFFERENT thing than no register at all)."""
    lines = (text or "").splitlines()
    n = len(lines)
    # PRIMARY: register-header-bounded scan.
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not _REGISTER_HEADER_RE.match(stripped):
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        hi = n
        for j in range(i + 1, n):
            m = _MD_HEADER_RE.match(lines[j].strip())
            if m and len(m.group(1)) <= level:
                hi = j
                break
        for block in _pipe_table_blocks(lines, i + 1, hi):
            d = _recognize_from_block(block)
            if d is not None:
                return d
    # FALLBACK: first recognized headerless pipe-table block (backward-compat).
    for block in _pipe_table_blocks(lines, 0, n):
        d = _recognize_from_block(block)
        if d is not None:
            return d
    return {
        "slices": [],
        "recognized": False,
        "raw_row_count": 0,
        "raw_non_closing_count": 0,
        "valid_non_closing_count": 0,
        "pointer_ref": None,
    }


def parse_slice_register(text):
    """Backward-compatible reader — the list of tolerantly-valid Slice objects.
    All existing callers (dispatch loop, handoff, dry-run) keep working."""
    return parse_slice_register_detail(text)["slices"]


# --------------------------------------------------------------------------- #
# A2 — DAG walk + synchronous one-slice-in-flight dispatch loop
# --------------------------------------------------------------------------- #

class DeadlockError(Exception):
    """Slices remain but none are ready (cycle / unsatisfiable depends_on)."""


class SliceAttemptError(Exception):
    """One attempt at a slice failed — a per-attempt verify seam rejected it
    (S5 model-pin, or S6 conformance). Routed into the retry budget by
    run_dispatch_loop; not a terminal error on its own."""


class EscalationRequired(Exception):
    """A slice exhausted its retry budget (retry 2x then escalate, A12). Carries
    the slice id, the attempt count, and the partial dispatched list. The in-band
    AskUserQuestion escalation UX that renders this is S8 — S4 raises the signal
    and stops the walk so dependents are not run past an unresolved slice."""

    def __init__(self, slice_id, attempts, dispatched):
        super().__init__(f"slice {slice_id} escalated after {attempts} attempts")
        self.slice_id = slice_id
        self.attempts = attempts
        self.dispatched = dispatched


def next_ready_slice(slices, bookkeeping):
    """Return the first register-order slice that is NOT completed and whose
    depends_on are ALL completed. None if no such slice exists.

    S4 resume reconciliation: a slice left at `started`/`committed` by a crash is
    NOT skipped — it is returned again and re-run from the beginning (the
    idempotent commit guard prevents a double-commit on the re-run). Only
    `completed` slices are skipped. Dependency gating is unchanged — a dep at
    `committed` is NOT `completed`, so its dependents stay blocked.

    One-at-a-time: returns exactly one slice; never a blocked one.

    NS10 (PROVISIONAL — design #10): shared-write-target serialization. A slice is
    not co-ready while an EARLIER-in-register slice that shares one of its
    write-targets is still incomplete — the later one releases only after the
    earlier completes (+ a context-reset boundary, supplied by the cross-session
    handoff that re-reads committed state). An empty `write_targets` (the default)
    preserves the original DAG behavior exactly."""
    for s in slices:
        if bookkeeping.is_completed(s.id):
            continue  # finished — skip (resume reconciliation)
        if not all(bookkeeping.is_completed(dep) for dep in s.depends_on):
            continue
        if _blocked_by_shared_write_target(s, slices, bookkeeping):
            continue  # serialized behind an earlier incomplete slice (NS10)
        return s
    return None


def _blocked_by_shared_write_target(s, slices, bookkeeping):
    """NS10 (PROVISIONAL): True iff some EARLIER-in-register slice that is not yet
    completed shares a write-target with `s`. Register order is the deterministic
    serializer (the earlier of two target-sharing slices runs first). Empty
    write-targets → never blocked (original behavior)."""
    wt = set(getattr(s, "write_targets", ()) or ())
    if not wt:
        return False
    for other in slices:
        if other.id == s.id:
            break  # only earlier-in-register slices serialize ahead of s
        if bookkeeping.is_completed(other.id):
            continue
        if wt & set(getattr(other, "write_targets", ()) or ()):
            return True
    return False


# --------------------------------------------------------------------------- #
# NS1 (v2) — cross-session typed handoff: the walking skeleton's keystone.
#
# Adds ONE capability to v1: a smooth, typed cross-session handoff. The pieces
# are value-level + pure — there is NO new port (design #3 / OQ6: a write-once/
# read-once COMPUTED record is the wrong shape for a HandoffPort; routing is a
# pure function and the record rides the existing active-run.json pointer):
#   - resolve_dispatch / slice_is_plan_needing : pure slice predicates (design #1)
#   - HandoffRecord                            : frozen value object (the carrier)
#   - compute_handoff_type / compute_handoff   : code-derived typed handoff
#                                                (NS1 ships the continue-the-run
#                                                arm; NS2 fills the 3-way table)
#   - read_pending_handoff / render_resume     : the fresh-session resume read
#
# HandoffRecord is a value object, so there is deliberately NO FakeHandoffRecord
# port-double — tests construct it directly and assert the dict round-trip + the
# cross-session read-back. Cockburn 4-step grows along the routing axis: the loop
# walks against in-memory bookkeeping, the carrier round-trips through a real
# active-run.json, and the live ack-gate wiring is NS3.
# --------------------------------------------------------------------------- #

DISPATCH_VALUES = ("hands-off", "attended", "out-of-session")
DEFAULT_DISPATCH = "attended"   # conservative pause-when-unsure default (design #1)

HANDOFF_TYPES = ("continue-the-run", "plan-this-slice", "implement-this-slice")

# A slice is "plan-needing" (operator-declared, rides the slice `type` — Q1) when
# its type names a planning detour. Consumed verbatim; never re-derived (GP3).
PLAN_NEEDING_TYPES = ("plan", "needs-plan", "needs_plan",
                      "plan-needing", "plan_needing", "plan_then_implement")


def resolve_dispatch(s):
    """Effective dispatch-axis value for a slice (design #1, AI-promotes-only).

    Precedence: an explicit, valid `dispatch` wins; else a v1 `confirm_override`
    True aliases to 'attended' (backward-compatible alias); else the conservative
    'attended' default (pause-when-unsure — hands-off must be AFFIRMATIVELY
    declared, since it is only safe when a verification gate EXISTS AT DISPATCH).
    Raises ValueError on an explicit-but-unknown dispatch value (no silent
    default — a wrong dispatch must never start)."""
    d = getattr(s, "dispatch", None)
    if d is not None and str(d).strip():
        norm = str(d).strip().lower()
        if norm not in DISPATCH_VALUES:
            raise ValueError(
                f"unknown dispatch {d!r}; expected one of {DISPATCH_VALUES}"
            )
        return norm
    if getattr(s, "confirm_override", False):
        return "attended"
    return DEFAULT_DISPATCH


def slice_is_plan_needing(s):
    """True iff the slice's `type` names a planning detour (operator-declared,
    consumed verbatim — Q1/GP3). Case/space-insensitive."""
    return str(getattr(s, "type", "")).strip().lower() in PLAN_NEEDING_TYPES


@dataclass(frozen=True)
class HandoffRecord:
    """The typed cross-session handoff carrier (design #3, OQ6 keystone).

    A write-once/read-once value object COMPUTED from on-disk facts (slice type +
    plan-file existence + the dispatch axis), code-rendered onto active-run.json's
    `pending_handoff` field and read back by the fresh session at the ack-gate
    resume boundary. Deliberately NOT behind a HandoffPort — a computed value is
    not an ongoing collaboration, so a port would be the wrong abstraction.
    Frozen: a handoff is a value, not mutable state."""
    type: str            # one of HANDOFF_TYPES
    dispatch: str        # one of DISPATCH_VALUES
    slice_id: str
    write_targets: tuple = field(default_factory=tuple)   # NS10 (provisional)
    plan_path: str = None                                 # set on the plan-detour arms (NS2/NS4)

    def to_dict(self):
        """JSON-safe dict for persistence onto active-run.json."""
        return {
            "type": self.type,
            "dispatch": self.dispatch,
            "slice_id": self.slice_id,
            "write_targets": list(self.write_targets),
            "plan_path": self.plan_path,
        }

    @classmethod
    def from_dict(cls, d):
        """Reconstruct from the persisted dict (the fresh-session read). Raises
        ValueError on a malformed record — a torn/garbled handoff must not route."""
        if not isinstance(d, dict):
            raise ValueError(
                f"HandoffRecord.from_dict needs a dict; got {type(d).__name__}"
            )
        for key in ("type", "dispatch", "slice_id"):
            if not d.get(key):
                raise ValueError(f"HandoffRecord missing required field {key!r}")
        if d["type"] not in HANDOFF_TYPES:
            raise ValueError(
                f"unknown handoff type {d['type']!r}; expected {HANDOFF_TYPES}"
            )
        if d["dispatch"] not in DISPATCH_VALUES:
            raise ValueError(
                f"unknown dispatch {d['dispatch']!r}; expected {DISPATCH_VALUES}"
            )
        return cls(
            type=d["type"],
            dispatch=d["dispatch"],
            slice_id=d["slice_id"],
            write_targets=tuple(d.get("write_targets") or ()),
            plan_path=d.get("plan_path"),
        )


def compute_handoff_type(s, *, plan_exists=False):
    """Code-derived typed-handoff type for one slice (design #4, GP2 — no AI at
    dispatch). The full 3-way truth table (NS2):

        plan-needing AND no _PLAN on disk   -> plan-this-slice
        plan-needing AND _PLAN on disk      -> implement-this-slice
        not plan-needing                    -> continue-the-run

    `plan_exists` is the STRUCTURAL fact (does the slice's _PLAN file exist on
    disk) — a bool the caller derives deterministically (NS4 wires the real
    on-disk + `L:slice plan=` check); it is NEVER an AI judgment at dispatch."""
    if slice_is_plan_needing(s):
        return "implement-this-slice" if plan_exists else "plan-this-slice"
    return "continue-the-run"


def compute_handoff(slices, bookkeeping, *, plan_exists=None, plan_path_for=None):
    """Compute the HandoffRecord for the next ready slice — the interleaved,
    just-in-time DAG walk (design #5/#6). Returns None when no slice is ready
    (the run is complete or deadlocked — the loop owns that distinction).

    Just-in-time: `next_ready_slice` only surfaces a slice once ALL its
    `depends_on` are completed, so a plan-needing slice is reached (and planned)
    only after its upstream deliverables exist (no plan-all-in-advance, no stale
    plans). Flat model (no recursion): exactly one HandoffRecord is returned per
    ready slice — a too-big slice becomes its own referenced Thought, never a
    nested run (GP3). Pure aside from the injected `plan_exists` (slice_id->bool)
    and `plan_path_for` (slice_id->path|None) callables; both default to none.
    The dispatch axis is read VERBATIM from the slice via resolve_dispatch."""
    s = next_ready_slice(slices, bookkeeping)
    if s is None:
        return None
    pe = bool(plan_exists(s.id)) if callable(plan_exists) else False
    htype = compute_handoff_type(s, plan_exists=pe)
    plan_path = None
    if htype == "implement-this-slice" and callable(plan_path_for):
        plan_path = plan_path_for(s.id)
    return HandoffRecord(
        type=htype,
        dispatch=resolve_dispatch(s),
        slice_id=s.id,
        write_targets=tuple(getattr(s, "write_targets", ()) or ()),
        plan_path=plan_path,
    )


def _active_run_pointer(pointer_dir=None, run_id=None):
    """Resolve the run pointer for the resume-family readers (honors
    EXECPLAN_ACK_STATE_DIR via _execplan_ack_dir; an explicit pointer_dir
    overrides it). With an explicit `run_id` → the per-run `run-<id>.json` (A2).
    Without one, back-compat resolution: a pre-existing legacy `active-run.json`
    wins (unmigrated state / direct-write tests); else the single `run-<id>.json`
    present; else the (possibly-absent) legacy path as the default target."""
    base = Path(pointer_dir) if pointer_dir else _execplan_ack_dir()
    if run_id:
        return base / f"run-{run_id}.json"
    legacy = base / "active-run.json"
    if legacy.exists():
        return legacy
    runs = sorted(base.glob("run-*.json")) if base.exists() else []
    if runs:
        return runs[0]
    return legacy


def read_pending_handoff(pointer_dir=None, run_id=None):
    """Read the HandoffRecord armed on the run pointer's `pending_handoff` (the
    fresh-session resume read). Returns None when there is no pointer or no
    pending_handoff. Raises ValueError on a malformed record (via from_dict).

    NS1: this is the test-to-test cross-session read. NS3 wires it into the live
    execplan-session-ack resume boundary (SKILL-side, AFTER the gate clears — the
    gate hook itself stays marker-only, per Gate 1's mapped invariant)."""
    doc = _read_json_or_none(_active_run_pointer(pointer_dir, run_id))
    if not isinstance(doc, dict):
        return None
    ph = doc.get("pending_handoff")
    if ph is None:
        return None
    return HandoffRecord.from_dict(ph)


_DISPATCH_NEXT = {
    "hands-off": "dispatch-hands-off",
    "attended": "dispatch-attended",
    "out-of-session": "dispatch-out-of-session",
}


def render_resume(handoff, *, has_verification_gate=None,
                  plan_session_exhausted=False):
    """Render the cross-session resume routing decision for a HandoffRecord (NS3).

    Routes the operator's next move by the dispatch axis (design #1/#2), AFTER the
    execplan-session-ack gate clears. The gate hook itself stays marker-only
    (route-blind by design — Gate 1 invariant); this is the SKILL-side router that
    re-reads pending_handoff and decides:

        plan-this-slice                  -> plan-detour            (NS4 wires /plan)
        <dispatchable> + hands-off       -> dispatch-hands-off
        <dispatchable> + attended        -> dispatch-attended
        <dispatchable> + out-of-session  -> dispatch-out-of-session

    where <dispatchable> is continue-the-run or implement-this-slice.

    Gate-exists-at-dispatch discipline (design #1, C6): hands-off is only safe
    when an automatable verification gate EXISTS AT DISPATCH. When the caller
    asserts `has_verification_gate is False` for a hands-off slice, the router
    PROMOTES it to attended (monotonic toward more confirmation — AI-promotes-
    only, never demotes a flagged slice) and records the reason. `None` means the
    caller did not assert presence/absence, so the declared dispatch is honored.

    One-plan-per-session (NS4, design #7): when `plan_session_exhausted` is True
    and the next move would be a plan-detour, the router instead routes to a
    cross-session-boundary (this session already planned a slice; a fresh session
    must plan the next one)."""
    if handoff is None:
        return {"action": "none", "reason": "no pending handoff"}
    base = {
        "action": "resume",
        "type": handoff.type,
        "dispatch": handoff.dispatch,
        "slice_id": handoff.slice_id,
    }
    # A plan-needing slice with no plan yet routes to the attended /plan detour,
    # regardless of its dispatch axis (planning is always human-gated, NS4).
    if handoff.type == "plan-this-slice":
        if plan_session_exhausted:
            base["next"] = "cross-session-boundary"
            base["reason"] = (
                "one plan mode per session — this session already planned a "
                f"slice; start a fresh session to plan {handoff.slice_id}"
            )
            return base
        base["next"] = "plan-detour"
        if handoff.plan_path:
            base["plan_path"] = handoff.plan_path
        return base
    # Dispatchable (continue-the-run / implement-this-slice): route by dispatch.
    if handoff.dispatch == "hands-off" and has_verification_gate is False:
        base["next"] = "dispatch-attended"
        base["dispatch_effective"] = "attended"
        base["gate_warning"] = (
            "slice declared hands-off but no automatable verification gate exists "
            "at dispatch — promoted to attended (gate-exists-at-dispatch, "
            "design #1); declare attended or split test-creation into a prior slice"
        )
        return base
    base["next"] = _DISPATCH_NEXT.get(handoff.dispatch, "dispatch-attended")
    return base


def render_out_of_session_block(handoff):
    """Render the out-of-session copy-paste block scaffold (UX #4 / design #2).

    A dispatch=out-of-session slice runs in a SEPARATE terminal OUTSIDE the
    producer's process tree (verifier-isolation). The SKILL fills the concrete
    command + verification gate; this scaffold pins the load-bearing contract: a
    hard migrate-before-register ordering and a paste-result-back ack (the
    orchestrator waits — no inline execution)."""
    return {
        "slice_id": handoff.slice_id,
        "dispatch": "out-of-session",
        "ordering": "migrate-before-register",
        "run_outside_producer_process_tree": True,
        "paste_result_back": True,
        "label": f"[execute-plan out-of-session] slice {handoff.slice_id}",
    }


# --------------------------------------------------------------------------- #
# NS4 (v2) — plan-detour: one-plan-per-session + plan-filing-on-approval +
# post-plan-gate detour return. The actual spine/register WRITES stay SKILL-
# driven (link-plan-to-thought + write_slice_row — run.py must NOT import
# pre_plan_gates/taskmanagement; dependency direction). run.py owns the
# deterministic pieces: the session-exhaustion flag, the on-disk plan-existence
# fact that drives the flip, the plan-filing GATE (re-read both surfaces), and
# the composition descriptor that sequences the return WITHOUT bypassing the
# existing post-plan UX gate (design #7/#8/#9).
# --------------------------------------------------------------------------- #

# (a) of the post-plan UX gate's (a)/(b)/(c): generate a handoff prompt and
# proceed in a FRESH implementation session — the detour-return path.
POST_PLAN_CHOICE_HANDOFF = "a"


def mark_plan_session_exhausted(pointer_dir=None, run_id=None):
    """Set `plan_session_exhausted=true` on the run pointer (design #7 — one plan
    mode per session). Idempotent; no-op when there is no pointer."""
    pointer = _active_run_pointer(pointer_dir, run_id)
    doc = _read_json_or_none(pointer)
    if not isinstance(doc, dict):
        return {"status": "no_pointer"}
    doc["plan_session_exhausted"] = True
    _atomic_write_json(pointer, doc)
    return {"status": "exhausted", "pointer": str(pointer)}


def is_plan_session_exhausted(pointer_dir=None, run_id=None):
    """Read the one-plan-per-session flag from the run pointer (default False)."""
    doc = _read_json_or_none(_active_run_pointer(pointer_dir, run_id))
    return bool(isinstance(doc, dict) and doc.get("plan_session_exhausted"))


def plan_file_exists(plan_path):
    """The STRUCTURAL fact that drives the plan-this-slice -> implement-this-slice
    flip (design #8): does the slice's _PLAN file exist on disk? Pure I/O — never
    an AI judgment. Empty/None path -> False."""
    return bool(plan_path) and Path(os.path.expanduser(str(plan_path))).is_file()


def verify_plan_filed(spine_path, slice_id, plan_basename):
    """The plan-filing GATE (NS4, design #8): after the SKILL files an approved
    plan, re-read BOTH surfaces to confirm it landed — the spine's
    `# Implementation Details` wikilink AND the register row's `L:slice plan=`
    field. Reuses the S6 `_verify_write` re-read primitive (code-layer,
    deterministic — LLM checkers never run here). Raises WriteVerificationError on
    any mismatch; returns {"ok": True, ...} on success. Producer-never-verifies is
    satisfied at the slice level by an isolated convergence check of this gate's
    design (the function itself is the code-layer re-read, not the judge)."""
    wikilink = f"[[{plan_basename}]]"
    # (1) the # Implementation Details wikilink landed as a markdown list item
    # (`- [[basename]]`, exactly the shape link_plan_to_thought writes). Matching
    # the list-item form — not a bare substring — avoids a false PASS off the
    # register row's own `plan=[[basename]]` field.
    _verify_write(
        "todo_line", spine_path, wikilink,
        rf"^- \[\[{re.escape(plan_basename)}\]\]",
    )
    # (2) the register row's L:slice plan= field carries the wikilink
    _verify_write(
        "todo_line", spine_path, f"plan={wikilink}",
        rf"<!--\s*L:slice\s+id={re.escape(slice_id)}\b.*?-->",
    )
    return {"ok": True, "slice_id": slice_id, "plan": wikilink}


def render_plan_detour_return(slice_id, plan_basename):
    """Compose the plan-detour return with the EXISTING post-plan UX gate (design
    #9 — compose, NEVER bypass). After ExitPlanMode approval the
    post-plan-uxgate marker fires its own (a)/(b)/(c); the orchestrator does NOT
    duplicate or skip it. It consumes the operator's (a) 'generate handoff' choice
    to, in order:
      1. file the approved plan          (SKILL: link-plan-to-thought + write_slice_row)
      2. verify it landed                (verify_plan_filed — the plan-filing gate)
      3. flip the slice                  (next compute_handoff sees _PLAN -> implement-this-slice)
      4. mark plan_session_exhausted     (one plan per session)
      5. arm the FRESH-session handoff   (implement-this-slice for the next session)

    Returns the ordered step descriptor the SKILL follows; the file writes + the
    post-plan gate stay SKILL-driven (run.py cannot import pre_plan_gates /
    taskmanagement — dependency direction)."""
    return {
        "slice_id": slice_id,
        "plan_basename": plan_basename,
        "composes_with": "post-plan-uxgate",   # NOT bypassed
        "post_plan_choice": POST_PLAN_CHOICE_HANDOFF,
        "steps": [
            "file-plan",
            "verify-plan-filed",
            "flip-to-implement-this-slice",
            "mark-plan-session-exhausted",
            "arm-fresh-session-handoff",
        ],
    }


# --------------------------------------------------------------------------- #
# NS5 (v2) — fresh code-face reconciliation at an attended/out-of-session
# cross-session resume (design #16). BEFORE emitting the next-step handoff,
# re-read the ACTUAL shipped code on disk and compare it against what the
# register CLAIMS, surfacing plan-vs-reality drift. Reuses the S6 `_verify_write`
# re-read primitive (code layer) + an optional ConformancePort (model layer).
# NEVER called inside the dispatch loop — the hands-off path runs slices inline
# with synchronous per-slice conformance and is untouched. This seam attaches
# SKILL-side at the execplan-session-ack resume boundary, attended/out-of-session
# ONLY.
# --------------------------------------------------------------------------- #

# The cross-session resume modes for which the code-face reconciliation fires.
# A hands-off resume is excluded — its synchronous per-slice conformance already
# covers it (design #16: "the hands-off path is unaffected").
RECONCILE_DISPATCH_MODES = ("attended", "out-of-session")


def reconcile_code_face(claimed, specs_by_slice=None, *, conformance=None,
                        verdicts_by_slice=None, reconcile_statuses=("completed",)):
    """Reconcile register-claimed slice status against the ACTUAL shipped code on
    disk (design #16). A FRESH code-face check, NOT a status summary — only slices
    whose claim and code disagree are reported as drift.

    For each slice the register claims complete (status in `reconcile_statuses`):
      1. code layer — re-read each `_verify_write` spec; any failure is a
         `code-partial` drift (register says completed; code is partial);
      2. model layer (optional) — a ConformancePort verdict; non-PASS is a
         `conformance-fail` drift.

    `claimed`: the register's slice_execution map.
    `specs_by_slice`: {slice_id: [ _verify_write spec dict, ... ]}.
    `conformance` + `verdicts_by_slice`: optional model-layer verdict per slice.
    Pure aside from the on-disk re-reads; NEVER invoked inside run_dispatch_loop."""
    specs_by_slice = specs_by_slice or {}
    verdicts_by_slice = verdicts_by_slice or {}
    drifts, clean, checked = [], [], []
    for sid in sorted((claimed or {}).keys()):
        entry = claimed.get(sid)
        status = entry.get("status") if isinstance(entry, dict) else None
        if status not in reconcile_statuses:
            continue
        checked.append(sid)
        slice_drift = None
        # code layer — re-read each spec
        for spec in specs_by_slice.get(sid, []):
            locator = spec.get("locator")
            if isinstance(locator, list):
                locator = tuple(locator)
            try:
                _verify_write(spec["surface_kind"], spec["path"],
                              spec["expected_payload"], locator)
            except WriteVerificationError as e:
                slice_drift = {"slice_id": sid, "claimed": status,
                               "kind": "code-partial", "detail": str(e)}
                break
        # model layer — conformance verdict (only when code layer is clean)
        if (slice_drift is None and conformance is not None
                and sid in verdicts_by_slice):
            res = conformance.check_conformance(
                slice_id=sid, verdict=verdicts_by_slice[sid])
            if not res.get("ok"):
                detail = f"verdict={res.get('verdict')!r}"
                if res.get("error"):
                    detail += f" — {res['error']}"
                slice_drift = {"slice_id": sid, "claimed": status,
                               "kind": "conformance-fail", "detail": detail}
        (drifts if slice_drift else clean).append(slice_drift or sid)
    return {
        "drift": bool(drifts),
        "checked": checked,
        "drifts": drifts,
        "clean": clean,
        "summary_line": (f"{len(drifts)} drift(s) across {len(checked)} "
                         f"register-claimed-complete slice(s)"),
    }


# --------------------------------------------------------------------------- #
# NS7 (v2) — resume prose via PROGRAMMATIC /prompt-for-handoff reuse (design #15
# HYBRID). The prose composition + the slice-aware freshness gate + the
# independent /double-check + the single verified write seam ALL stay pfh's
# machinery (pre_plan_gates.py), invoked by the SKILL — run.py cannot import it
# (dependency direction). run.py owns the three deterministic corrections folded
# into the hybrid:
#   (b1) type-change recompose trigger — pfh's fingerprint is TYPE-BLIND
#        (pre_plan_gates.py:221-237 keys on alt-count + first-not-done-slice-id +
#        Sessions M/N, not the HandoffRecord type), so a same-slice
#        plan-this-slice -> implement-this-slice transition must FORCE a recompose;
#   (b3) deferred-capture enumeration (origin-slice-id) + a completeness check
#        against the run's ACTUAL deferred set;
#   re-seed completion — the composer input bundle (handoff + slice-register
#        status + ALL deferred captures).
# (b2 — prose-vs-_thought is pfh's job; register-vs-code is NS5's reconcile seam.)
# --------------------------------------------------------------------------- #

def record_composed_handoff_type(htype, pointer_dir=None, run_id=None):
    """Record the handoff type of the LAST composed resume prompt on the run
    pointer (NS7 b1). Read back by should_recompose_handoff_prompt to detect a
    type change the type-blind pfh fingerprint cannot see."""
    pointer = _active_run_pointer(pointer_dir, run_id)
    doc = _read_json_or_none(pointer)
    if not isinstance(doc, dict):
        return {"status": "no_pointer"}
    doc["composed_handoff_type"] = htype
    _atomic_write_json(pointer, doc)
    return {"status": "recorded", "composed_handoff_type": htype}


def read_composed_handoff_type(pointer_dir=None, run_id=None):
    """The handoff type of the last composed resume prompt (None if never)."""
    doc = _read_json_or_none(_active_run_pointer(pointer_dir, run_id))
    return doc.get("composed_handoff_type") if isinstance(doc, dict) else None


def should_recompose_handoff_prompt(current_type, prev_type, *, pfh_stale=False):
    """Decide whether the resume prose must be recomposed (NS7, design #15 b1).

    Recompose when (a) there is no prior composed prompt, (b) the handoff type
    changed since the last compose — the type-trigger the type-blind pfh
    fingerprint misses — or (c) pfh's own freshness gate reports staleness.
    Returns (recompose: bool, reason: str)."""
    if not prev_type:
        return True, "no prior composed prompt"
    if current_type != prev_type:
        return True, (f"handoff type changed {prev_type!r} -> {current_type!r} "
                      "(pfh fingerprint is type-blind — forced recompose)")
    if pfh_stale:
        return True, "pfh freshness gate reports staleness"
    return False, "fresh (same handoff type and pfh reports fresh)"


def enumerate_deferred_captures(dispatched_or_map):
    """Enumerate ALL deferred-this-run captures with their origin slice id (NS7,
    design #15 b3). Each slice's spawn result may carry `result["deferred"]` — a
    list of strings or {title/body/text} dicts representing work deferred to TODO
    during that slice. Returns an ordered list of {id, origin_slice_id, capture}
    with stable ids `<slice_id>-def<n>`. Reuses the _observation_pairs normalizer
    (same shape as collect_observations)."""
    out = []
    for sid, res in _observation_pairs(dispatched_or_map):
        deferred = res.get("deferred")
        if not isinstance(deferred, list):
            continue
        n = 0
        for item in deferred:
            if isinstance(item, str):
                cap = item.strip()
            elif isinstance(item, dict):
                cap = str(item.get("body") or item.get("text")
                          or item.get("title") or "").strip()
            else:
                continue
            if not cap:
                continue
            n += 1
            out.append({"id": f"{sid}-def{n}", "origin_slice_id": sid,
                        "capture": cap})
    return out


def check_deferred_completeness(enumerated, actual_deferred):
    """Completeness check (NS7 b3): every deferred item the run ACTUALLY produced
    must appear in the composer's enumerated set. `actual_deferred` is the
    authoritative collection of capture strings the run deferred. Returns
    {complete, missing, extra} — `complete` is False if any actual capture is
    absent from the enumeration (the composer would ship an incomplete re-seed)."""
    enum_caps = {e["capture"] for e in enumerated}
    actual = set(actual_deferred or [])
    missing = sorted(actual - enum_caps)
    extra = sorted(enum_caps - actual)
    return {"complete": not missing, "missing": missing, "extra": extra}


def render_resume_context(handoff, session_summary=None, deferred_captures=None):
    """Assemble the run context the reused /prompt-for-handoff COMPOSER receives
    (NS7 re-seed completion, design #15): the HandoffRecord summary + the
    slice-register status + ALL deferred-this-run captures (origin-slice-id
    threaded). Pure — the prose, the freshness gate, the independent /double-check,
    and the single verified write seam stay pfh's machinery (no manual
    /prompt-for-handoff; the SKILL invokes the seam on the operator's behalf)."""
    return {
        "handoff": handoff.to_dict() if handoff is not None else None,
        "session_summary": session_summary,
        "deferred_captures": list(deferred_captures or []),
    }


# --------------------------------------------------------------------------- #
# NS9 (v2) — exception-intervention surface (design #5 / UX #5 / M2). Reuses the
# v1 escalation engine shape (render_escalation_surface) and broadens it: a
# structured block carrying the exception type, slice context, last-committed
# state, a PROMINENT default option + the non-default options. The operator's
# choice is RECORDED (never inferred for flow), feeding the retained v1 secondary
# metric — the exception-intervention engagement rate. Pure renderers; the
# AskUserQuestion is SKILL-driven.
# --------------------------------------------------------------------------- #

EXCEPTION_TYPES = ("escalation", "deadlock", "conformance-fail", "drift",
                   "abort", "model-abort")

_DEFAULT_EXCEPTION_OPTIONS = ("Retry", "Skip and continue", "Abort run")


def render_exception_surface(exception_type, slice_id, *, last_committed=None,
                             dispatched=None, default_option=None, options=None,
                             detail=None):
    """Render the v2 exception-intervention surface (design #5, M2): a structured
    block with the exception type, slice context, last-committed state, a PROMINENT
    default option (listed first), and the non-default options. Reuses the v1
    escalation engine's completed-so-far shape. Pure — the AskUserQuestion is
    SKILL-driven; the operator's choice is RECORDED (record_exception_choice),
    never inferred for the orchestrator's flow."""
    completed = [d.get("slice_id") for d in (dispatched or [])
                 if isinstance(d, dict) and d.get("slice_id")]
    opts = list(options) if options else list(_DEFAULT_EXCEPTION_OPTIONS)
    default = default_option if default_option in opts else opts[0]
    ordered = [default] + [o for o in opts if o != default]   # prominent default first
    return {
        "exception_type": exception_type,
        "slice_id": slice_id,
        "last_committed": last_committed,
        "completed_so_far": completed,
        "detail": detail,
        "default_option": default,
        "options": ordered,
        "prompt": (f"Exception ({exception_type}) at slice {slice_id}. "
                   "How do you want to proceed?"),
    }


def record_exception_choice(chosen, default_option, *, engaged=None):
    """Record the operator's exception-intervention choice (design #5 / secondary
    metric). Engagement = the operator chose a NON-default option OR explicitly
    engaged with the content; `engaged` overrides the non-default inference. The
    choice is RECORDED, never inferred for the orchestrator's flow. Returns a
    metric row."""
    non_default = (chosen != default_option)
    is_engaged = bool(engaged) if engaged is not None else non_default
    return {"chosen": chosen, "default_option": default_option,
            "non_default": non_default, "engaged": is_engaged}


def exception_engagement_rate(records):
    """The retained v1 secondary metric (locked Metrics): exception-intervention
    engagement rate = interventions where the operator chose a non-default action
    OR engaged with the content / total interventions. CAPTURED-NOT-GATED — a
    gate-fatigue guardrail, never a blocker."""
    rows = [r for r in (records or []) if isinstance(r, dict)]
    total = len(rows)
    engaged = sum(1 for r in rows if r.get("engaged"))
    return {"total": total, "engaged": engaged,
            "engagement_rate": (engaged / total) if total else 0,
            "gated": False}


def _default_prompt(s, model_family):
    return (
        f"[execute-plan walking skeleton] Implement slice {s.id} ({s.name}) "
        f"as model family {model_family}."
    )


_MODEL_TAG_RE = re.compile(r"\[MODEL:(sonnet|opus|haiku)\]")


def stamp_model(prompt, model_family):
    """Append a machine-readable [MODEL:<family>] tag to an implementation spawn's
    prompt so the runtime model enforcer (check-impl-models.sh, A4) can verify the
    spawn ran on the family the dispatcher RESOLVED — per-spawn, with no need for a
    Coherent-Action id (execute-plan works per-slice with a resolved model_family).

    Idempotent: a prompt already carrying a [MODEL:] tag is returned unchanged.
    Only a known family slug (sonnet/opus/haiku) is stamped; anything else is a
    no-op (the enforcer then falls back to its union-with-warn path)."""
    fam = (model_family or "").strip().lower()
    if fam not in ("sonnet", "opus", "haiku"):
        return prompt
    if _MODEL_TAG_RE.search(prompt or ""):
        return prompt
    base = prompt or ""
    sep = "" if base.endswith(("\n", " ")) or base == "" else " "
    return f"{base}{sep}[MODEL:{fam}]"


def _run_one_slice(s, family, bookkeeping, spawn, commit, commit_guard,
                   pre_commit_verify, pre_commit_codecheck, post_commit_conformance,
                   prompt_for, max_attempts, dispatched):
    """Run ONE slice through the S4 crash-safe 3-checkpoint machine with
    retry-2x-then-escalate. Each attempt:

        record_attempt (started)            # persisted retry counter ++ (A6)
        -> spawn (awaited inline)
        -> pre_commit_verify  seam (S5)     # pass-default; raise -> retry
        -> pre_commit_codecheck seam (S6)   # pass-default; raise -> retry (BEFORE commit)
        -> idempotent-guarded commit        # guard.commit_exists? skip : create
        -> mark_committed (committed)        # middle checkpoint (A6)
        -> post_commit_conformance seam (S6) # pass-default; raise -> retry
        -> mark_completed (completed)

    A failed attempt (SliceAttemptError from any seam) re-runs from
    record_attempt; the idempotent commit guard prevents a double-commit if a
    prior attempt already committed (A12). After `max_attempts` the slice is
    marked `escalated` and EscalationRequired is raised. Returns the dispatched
    entry on success."""
    started_at = _now_iso()   # NS8 — slice start (captured-not-gated)
    while True:
        attempt = bookkeeping.record_attempt(s.id, model_family=family)
        prompt = prompt_for(s) if prompt_for else _default_prompt(s, family)
        # A4: stamp the resolved family so the runtime enforcer verifies this
        # implementation spawn ran on the intended model (per-spawn).
        prompt = stamp_model(prompt, family)
        try:
            result = spawn.spawn(s.id, family, prompt)  # awaited inline
            # S5 seam — post-dispatch transcript model-pin verification.
            if pre_commit_verify is not None:
                pre_commit_verify(s, attempt, result)
            # S6 seam — code-layer (tests/lints + verify_write) BEFORE the commit
            # (grading order: deterministic code-layer first, judgment model-layer
            # second; code_first_architecture.md:121-124).
            if pre_commit_codecheck is not None:
                pre_commit_codecheck(s, attempt, result)
            # Idempotent commit guard: never create a second commit for a slice
            # whose slice_id-prefixed commit already exists (resume/retry safe).
            if not commit_guard.commit_exists(s.id):
                commit.create_commit(
                    s.id, result=result,
                    paths=tuple(getattr(s, "write_targets", ()) or ()))
            bookkeeping.mark_committed(s.id, result=result)
            # S6 seam — two-layer conformance (/double-check 3,0,1).
            if post_commit_conformance is not None:
                post_commit_conformance(s, attempt, result)
            bookkeeping.mark_completed(s.id, result=result)
            # NS8 — capture per-slice timing + type via the port (captured-NOT-
            # gated: never affects flow). A slice the dispatch loop runs is
            # hands-off BY DEFINITION (attended/out-of-session slices never reach
            # the loop — the SKILL runs them and captures attended=True itself), so
            # the loop always records attended=False. Defensive getattr keeps any
            # future BookkeepingPort double that omits the method from breaking.
            _capture = getattr(bookkeeping, "capture_timing", None)
            if callable(_capture):
                _capture(s.id, started_at=started_at, completed_at=_now_iso(),
                         attended=False, slice_type=getattr(s, "type", None))
            return {
                "slice_id": s.id, "model_family": family,
                "result": result, "attempts": attempt,
            }
        except SliceAttemptError:
            if attempt >= max_attempts:
                bookkeeping.mark_escalated(s.id)
                raise EscalationRequired(s.id, attempt, dispatched)
            # else: retry — re-run from record_attempt (next loop iteration).
            continue


def run_dispatch_loop(slices, bookkeeping, spawn, *, commit=None,
                      commit_guard=None, pre_commit_verify=None,
                      pre_commit_codecheck=None, post_commit_conformance=None,
                      prompt_for=None, max_attempts=3):
    """Synchronous, foreground, one-slice-in-flight DAG walk with the S4
    crash-safe 3-checkpoint state machine + retry-2x-then-escalate.

    Per slice (via `_run_one_slice`): record_attempt -> spawn -> [S5 seam] ->
    [S6 code-layer seam] -> idempotent-guarded commit -> mark_committed ->
    [S6 conformance seam] -> mark_completed.
    Resume reconciliation lives in `next_ready_slice` (skip `completed`; re-run
    `started`/`committed`). Raises DeadlockError if slices remain but none are
    ready; raises EscalationRequired (halting the walk) when a slice exhausts its
    retry budget.

    S2/S3 back-compat: `commit`/`commit_guard` default to in-process fakes and
    all verify seams default to pass, so a call with no S4/S5/S6 args behaves
    like the S2/S3 loop in observable dependency order + completion.
    `max_attempts=3` = 1 initial + 2 retries (A12). The real S5 model-pin check,
    S6 code-layer + conformance, and S7 git commit fill the seams / replace the
    fakes at their slices."""
    commit = commit if commit is not None else FakeCommitAdapter()
    commit_guard = commit_guard if commit_guard is not None else FakeCommitGuard()
    dispatched = []
    while True:
        s = next_ready_slice(slices, bookkeeping)
        if s is None:
            incomplete = [x.id for x in slices if not bookkeeping.is_completed(x.id)]
            if incomplete:
                raise DeadlockError(
                    f"no ready slice but {len(incomplete)} incomplete "
                    f"(cycle or unsatisfiable depends_on): {incomplete}"
                )
            break
        family = agent_choice_to_model_family(s.agent_choice)
        dispatched.append(_run_one_slice(
            s, family, bookkeeping, spawn, commit, commit_guard,
            pre_commit_verify, pre_commit_codecheck, post_commit_conformance,
            prompt_for, max_attempts, dispatched,
        ))
    return {
        "dispatched": dispatched,
        "summary": {
            "total": len(slices),
            "completed": sum(1 for x in slices if bookkeeping.is_completed(x.id)),
            "order": [d["slice_id"] for d in dispatched],
        },
    }


# --------------------------------------------------------------------------- #
# S8 — session/mode UX: run-wide execution mode, AI-promotes-only confirm
# decision, and pure renderers for the confirm prompt / escalation surface /
# session-resume summary. Python cannot invoke AskUserQuestion or
# PushNotification, so — like SpawnPort's real Agent spawn and the operator-
# confirmed git push — those surfaces are SKILL.md-driven; run.py owns ONLY the
# deterministic decisions + payload rendering. None of this touches the locked
# loop topology / ports / seams (spine U1-U8 + A9/A12; S8).
# --------------------------------------------------------------------------- #

EXECUTION_MODES = ("observer", "confirm", "auto")
DEFAULT_MODE = "observer"   # Observer is the default; Confirm/Auto are opted INTO (U7).


def normalize_mode(value):
    """Validate/normalize a run-wide execution mode. None/blank -> DEFAULT_MODE
    (observer); unknown -> ValueError (no silent wrong mode). Case/space-
    insensitive. AI-promotes-only is a property of slice_needs_confirm, not of
    mode selection (U1/U3/U7)."""
    if value is None:
        return DEFAULT_MODE
    if isinstance(value, str):
        v = value.strip().lower()
        if not v:
            return DEFAULT_MODE
    else:
        v = value
    if v not in EXECUTION_MODES:
        raise ValueError(
            f"unknown execution mode {value!r}; expected one of "
            f"{EXECUTION_MODES} (default {DEFAULT_MODE!r})"
        )
    return v


# Layer-1 deterministic risk net (regex over the slice name+type). Editorial /
# calibratable -- a runtime SAFETY NET that can only PROMOTE a slice toward more
# confirmation (AI-promotes-only). The AUTHORITATIVE risk flag is the slicer's
# confirm_override (Layer-2 Opus judgment, set at planning, consumed verbatim).
DEFAULT_RISK_PATTERNS = (
    r"migrat", r"delet", r"\bdrop\b", r"destroy", r"destructive", r"irreversible",
    r"schema", r"deploy", r"\bpush\b", r"\brm\b", r"truncate", r"production",
    r"secret", r"credential", r"force",
)


def layer1_risky(s, *, patterns=DEFAULT_RISK_PATTERNS):
    """Deterministic Layer-1 risk promotion: True if any pattern matches the
    slice's name+type (case-insensitive). Promote-only; never demotes (U3)."""
    hay = f"{getattr(s, 'name', '')} {getattr(s, 'type', '')}".lower()
    return any(re.search(p, hay) for p in patterns)


def slice_needs_confirm(s, *, mode, layer1=True):
    """AI-promotes-only, monotonic confirm decision (U3/U4):
        risky = confirm_override OR (layer1 AND layer1_risky(s))
        auto     -> confirm ONLY risky slices (auto-proceeds otherwise)
        confirm  -> confirm EVERY slice
        observer -> False (orchestrator issues no auto-transitions; operator
                    drives /work-start etc. -- U7)
    Never demotes a flagged slice. `mode` is normalized first (unknown -> raise)."""
    mode = normalize_mode(mode)
    risky = bool(getattr(s, "confirm_override", False)) or (layer1 and layer1_risky(s))
    if mode == "auto":
        return risky
    if mode == "confirm":
        return True
    return False  # observer


def _risky_reason(s, *, layer1=True):
    reasons = []
    if getattr(s, "confirm_override", False):
        reasons.append("slicer-flagged (confirm_override)")
    if layer1 and layer1_risky(s):
        reasons.append("Layer-1 risk pattern matched")
    return "; ".join(reasons)


def render_confirm_prompt(s, *, mode, model_family=None):
    """Build the per-slice confirm AskUserQuestion payload SKILL.md surfaces AFTER
    gating resolves and BEFORE the agent spawns (U4). Pure -- the AskUserQuestion
    itself is SKILL.md-driven. `model_family` defaults to the slice's resolved
    family."""
    fam = model_family or agent_choice_to_model_family(s.agent_choice)
    return {
        "slice_id": s.id,
        "name": s.name,
        "mode": normalize_mode(mode),
        "model_family": fam,
        "risky_reason": _risky_reason(s) or "run-wide confirm mode",
        "question": f"Confirm slice {s.id} ({s.name}) before it runs?",
        "options": ["Proceed", "Skip this slice", "Abort run"],
    }


def should_notify_on_escalation(mode, notify_recipient):
    """Auto-only opt-in escalation notify (U8/A12): True iff mode is 'auto' AND a
    notify_recipient is configured. The in-band AskUserQuestion gate fires ALWAYS,
    independent of this."""
    return normalize_mode(mode) == "auto" and bool(notify_recipient)


def render_escalation_surface(slice_id, attempts, dispatched, *, mode,
                              notify_recipient=None):
    """Render the escalation surface for an EscalationRequired (S4/A12). The
    in-band AskUserQuestion ALWAYS fires; the Auto-only PushNotification is opt-in.
    `attempts` is the PERSISTED retry count (slice_execution.attempts) surfaced
    VERBATIM so a crash+resume shows the continued count, not a reset (U8)."""
    completed = [d.get("slice_id") for d in (dispatched or [])
                 if isinstance(d, dict) and d.get("slice_id")]
    return {
        "slice_id": slice_id,
        "retry_count": attempts,
        "completed_so_far": completed,
        "in_band": {
            "question": (f"Slice {slice_id} escalated after {attempts} attempts. "
                         "How do you want to proceed?"),
            "options": ["Retry once more", "Skip and continue", "Abort run"],
        },
        "notify": {
            "should_notify": should_notify_on_escalation(mode, notify_recipient),
            "recipient": notify_recipient,
        },
    }


_SESSION_STATUS_BUCKETS = ("completed", "committed", "started", "escalated")


def render_session_summary(slice_execution):
    """Render the slice_execution map into a human re-entry summary (U2 Investigate
    path / U6 paused-run re-entry). Groups by status and names the single in-flight
    slice (one-at-a-time, A4). Pure -- SKILL.md obtains the map (from the active
    BookkeepingPort surface) and surfaces this."""
    buckets = {k: [] for k in _SESSION_STATUS_BUCKETS}
    other = []
    for sid, entry in (slice_execution or {}).items():
        st = (entry or {}).get("status") if isinstance(entry, dict) else None
        (buckets[st] if st in buckets else other).append(sid)
    in_flight = sorted(buckets["started"] + buckets["committed"])
    completed = sorted(buckets["completed"])
    escalated = sorted(buckets["escalated"])
    return {
        "completed": completed,
        "in_flight": in_flight,
        "escalated": escalated,
        "other": sorted(other),
        "incomplete_remaining": bool(in_flight or escalated),
        "summary_line": (f"{len(completed)} completed, {len(in_flight)} in-flight, "
                         f"{len(escalated)} escalated"),
    }


def _execplan_ack_dir():
    """The execplan session-ack state dir. Honors EXECPLAN_ACK_STATE_DIR for test
    isolation (mirrors VERIFIER_ISOLATION_HOME); defaults to the live namespace."""
    return Path(os.environ.get(
        "EXECPLAN_ACK_STATE_DIR",
        str(Path.home() / ".claude" / "state" / "execplan_session_ack"),
    ))


# --------------------------------------------------------------------------- #
# Resume-scoped checkpoint state model (execplan-resume-gate-fix, 2026-07-10).
#
# Replaces the single machine-wide active-run.json + SessionStart-arm + PreToolUse
# `.*` block with per-run, topic/worktree-scoped pointers (`run-<run_id>.json`) and
# a checkpoint that fires ONLY on the /execute-plan resume action — scoped to the
# run a fresh session is picking up. run.py owns every deterministic decision
# (compute run_id / self-heal / staleness / ack); the hooks are thin adapters.
# See Thoughts/execplan-resume-gate-fix-*_PLAN.md (Mode C, A1–A7).
# --------------------------------------------------------------------------- #

EXECPLAN_ACK_TTL_DEFAULT = 24 * 3600   # 24h — editorial; scales the 6h
                                       # verifier-isolation precedent (Design Review)


def _execplan_ttl_seconds():
    """Staleness TTL for run pointers (A3/A5). Env-overridable for tests."""
    try:
        return int(os.environ.get("EXECPLAN_ACK_TTL_SECONDS",
                                  EXECPLAN_ACK_TTL_DEFAULT))
    except (TypeError, ValueError):
        return EXECPLAN_ACK_TTL_DEFAULT


def compute_run_id(surface_path, worktree_root=None):
    """Topic/worktree-scoped run id: sha256(surface_path [+ worktree_root])[:12]
    (A2). `surface_path` IS the topic key (…/Thoughts/<topic>_THOUGHT.md); a
    worktree root, when present, scopes the run to that worktree so a session only
    ever sees its own worktree's runs (git-policy.md §3). Two runs sharing a topic
    path (+ same worktree) collapse to the SAME id — the intended identity, not a
    collision (Design Review)."""
    key = os.path.abspath(os.path.expanduser(str(surface_path)))
    wt = str(worktree_root).strip() if worktree_root else ""
    if wt:
        key = os.path.abspath(os.path.expanduser(wt)) + "\x00" + key
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


# The base-slug grammar, MIRRORED from the canonical locus
# `pre_plan_gates._slug_from_spine_path` (S2/A4). It is a copy, deliberately:
# run.py does not import pre_plan_gates (dependency direction — see the S3
# persistence-block note above and `render_plan_detour_return`). The copy is
# bound to its original by `test_surface_slug_never_drifts_from_the_canonical_locus`
# in test_run.py, which fails if the two disagree on ANY shape in its table or on
# ANY real filename under `Projects/Thoughts/`.
#
# ORDER IS THE WHOLE POINT, and getting it backwards is the defect this replaces.
# Match the LEADING `-<14-digit>` stamp FIRST, then strip it. The pre-S2 ordering
# stripped `_<TYPE>` first, which left the stamp no longer trailing on a
# scope-keyed name — so it survived:
#     git-working-model-20260714130221_S1_PLAN.md
#         canonical : git-working-model
#         pre-S2    : git-working-model-20260714130221_S1      <- displayed
# A gate message about a scope-keyed plan therefore showed a slug that was not
# that topic's key.
_SLUG_LEADING_TS_RE = re.compile(r"^(.*?-\d{14})")
_SLUG_TRAILING_TS_RE = re.compile(r"-\d{14}$")
# Exact-case, and deliberately WITHOUT `_check` — the canonical locus carries
# these five and only these five, case-sensitively. A copy that "improves" on its
# original is a copy that has drifted; the drift test treats it as one.
_SLUG_TYPE_SUFFIXES = ("_THOUGHT", "_PLAN", "_DESIGN", "_RESEARCH", "_CLAIMS")


def _base_slug(name):
    """The base topic slug for an artifact BASENAME — the mirror of
    `pre_plan_gates._slug_from_spine_path` (which reads
    `_timestamped_slug_from_spine` and strips the timestamp off it). Returns the
    canonical answer verbatim, including the empty string for a name that is
    nothing but a stamp; the caller owns any display default."""
    stem = name[:-len(".md")] if name.endswith(".md") else name
    m = _SLUG_LEADING_TS_RE.match(stem)
    if m:
        stem = m.group(1)
    else:
        for suf in _SLUG_TYPE_SUFFIXES:
            if stem.endswith(suf):
                stem = stem[:-len(suf)]
                break
    return _SLUG_TRAILING_TS_RE.sub("", stem)


def surface_slug(surface_path):
    """Topic slug from a surface-path basename (A7 display). The value shown in
    an operator-facing run row or gate block, and nothing else — it files no
    record and produces no identity.

    It must nonetheless equal the topic's REAL key, or a gate message names a
    slug that exists nowhere. So the grammar is `_base_slug` (the canonical
    mirror) and the only things layered on top are two surface-only conveniences,
    both outside the canonical locus' domain:
      * a `.json` basename is reduced first — this is a run-POINTER surface, and
        the canonical locus only ever sees `.md` artifacts;
      * an empty result becomes 'run', because a display field cannot be blank.
    Empty/None → 'run'."""
    name = Path(str(surface_path or "")).name.strip()
    if name.lower().endswith(".json"):
        name = name[:-len(".json")]
    return _base_slug(name) or "run"


def _run_pointer_path(base, run_id):
    return Path(base) / f"run-{run_id}.json"


def _legacy_pointer_path(base):
    return Path(base) / "active-run.json"


def _read_pointer_doc(path):
    """Read a pointer JSON, tolerating corruption (returns None on bad JSON/IO)."""
    try:
        doc = _read_json_or_none(path)
    except (json.JSONDecodeError, OSError):
        return None
    return doc if isinstance(doc, dict) else None


def _iter_run_pointers(base):
    """Yield (path, doc) for every well-formed `run-<id>.json` pointer under base."""
    base = Path(base)
    if not base.exists():
        return
    for p in sorted(base.glob("run-*.json")):
        doc = _read_pointer_doc(p)
        if doc is not None:
            yield p, doc


def _pointer_completed_count(doc):
    """(completed_count, total_slices|None) for a run pointer, read from its JSON
    `state_path` (the real slice_execution surface — A3 fix for the dead probe that
    fed a Markdown spine to a JSON parser). A run whose completion cannot be proven
    reads as 0 completed (never falsely 'complete')."""
    total = doc.get("total_slices")
    sp = doc.get("state_path")
    if not sp:
        return 0, total
    sdoc = _read_pointer_doc(os.path.expanduser(str(sp)))
    if sdoc is None:
        return 0, total
    execmap = sdoc.get("slice_execution") if "slice_execution" in sdoc else sdoc
    if not isinstance(execmap, dict):
        return 0, total
    completed = sum(1 for e in execmap.values()
                    if isinstance(e, dict) and e.get("status") == "completed")
    return completed, total


def _run_is_complete(doc):
    completed, total = _pointer_completed_count(doc)
    try:
        return bool(total) and completed >= int(total)
    except (TypeError, ValueError):
        return False


def _run_age_seconds(doc, now):
    ts = doc.get("updated_at") or doc.get("created_at")
    if not ts:
        return None
    from datetime import datetime
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return now - dt.timestamp()


def _run_is_stale(doc, now, ttl):
    age = _run_age_seconds(doc, now)
    return age is not None and age > ttl


def migrate_legacy_pointer(base):
    """Transitional (A2 guard rail): fold a pre-existing lone `active-run.json`
    into a per-run `run-<id>.json`. Idempotent; no-op when absent/malformed."""
    legacy = _legacy_pointer_path(base)
    doc = _read_pointer_doc(legacy)
    if not doc or not doc.get("surface_path"):
        return None
    rid = doc.get("run_id") or compute_run_id(
        doc["surface_path"], doc.get("worktree_root"))
    doc["run_id"] = rid
    doc.setdefault("created_at", _now_iso())
    doc["updated_at"] = doc.get("updated_at") or doc["created_at"]
    target = _run_pointer_path(base, rid)
    if not target.exists():
        _atomic_write_json(target, doc)
    # Retire the legacy pointer by MOVING it aside, never `unlink`. When `target`
    # already exists the legacy doc was NOT copied anywhere, so an unlink here was an
    # unrecoverable delete of the only record of that run — reached from the top of
    # `cmd_clear_active_run`'s run-scoped branch (and from `cmd_set_active_run`)
    # BEFORE any ownership check runs, so any caller could trigger it for a pointer
    # they do not own. Same destructive-path class A2 closes, one call deeper
    # (`safe-defaults.md`; execplan-gate-blast-radius A2, gap G5).
    _move_pointer_aside(legacy)
    return rid


def _read_acked_run_ids(base, session_id):
    doc = _read_pointer_doc(Path(base) / f"acked-{session_id}.json")
    if doc and isinstance(doc.get("acked_run_ids"), list):
        return set(doc["acked_run_ids"])
    return set()


def _pointer_visible_to(doc, session_worktree):
    """A run pointer is visible to a session iff the run is NOT worktree-scoped
    (global fallback — legacy/migrated runs) OR its worktree matches the session's.
    A worktree-scoped run never gates a different (or no-)worktree session — the
    git-policy.md §3 isolation the old machine-global pointer violated."""
    pwt = (doc.get("worktree_root") or "").strip()
    if not pwt:
        return True
    return pwt == (session_worktree or "").strip()


def reap_stale_runs(base=None, now=None, ttl=None):
    """A3: remove run pointers that are provably complete OR older than the TTL.
    A live, incomplete, in-TTL run is left untouched. Returns the reaped run ids."""
    base = Path(base) if base else _execplan_ack_dir()
    now = time.time() if now is None else now
    ttl = _execplan_ttl_seconds() if ttl is None else ttl
    reaped = []
    for p, doc in _iter_run_pointers(base):
        if _run_is_complete(doc) or _run_is_stale(doc, now, ttl):
            try:
                p.unlink()
                reaped.append(doc.get("run_id") or p.stem)
            except OSError:
                pass
    return reaped


_DISPATCH_VERB = {
    "hands-off": "auto-implements the next slice and commits it",
    "attended": "you drive the slice in this session",
    "out-of-session": "runs as a copy-paste block in a separate terminal",
}


def render_resume_prompt(slug, slice_id, dispatch, remaining=1):
    """A7: the informative resume prompt — names the run (slug), the slice, and
    what proceeding will do (dispatch verb), plus the 3 options."""
    verb = _DISPATCH_VERB.get(dispatch, "continues the run")
    extra = "" if remaining <= 1 else f"  (+{remaining - 1} more run(s) in this worktree)"
    return (
        "BLOCKED: /execute-plan is resuming a mid-flight run from another session.\n\n"
        f"  Run:    {slug}{extra}\n"
        f"  Slice:  {slice_id}\n"
        f"  Next:   {dispatch} — {verb}\n\n"
        "Before resuming, invoke AskUserQuestion with three options:\n"
        "  (a) Investigate — show full slice status (completed / in-flight / next) first.\n"
        "  (b) Proceed — resume this run now.\n"
        "  (c) Not now — leave the run parked and keep working here.\n\n"
        "The gate clears once AskUserQuestion completes."
    )


def _resume_row(doc, now):
    """One run's display row for the resume inventory: identity plus the two
    liveness columns. DISPLAY ONLY — nothing acts on `walker`/`age_seconds`."""
    ph = doc.get("pending_handoff") or {}
    age = _run_age_seconds(doc, now)
    return {"run_id": doc.get("run_id"),
            "slug": surface_slug(doc.get("surface_path")),
            "slice_id": (_pointer_current_slice_id(doc) or ph.get("slice_id") or "—"),
            "dispatch": ph.get("dispatch") or "—",
            "walking_session_id": _pointer_walking_session_id(doc),
            "age_seconds": (int(age) if age is not None else None)}


def _fmt_age(seconds):
    """Human-readable last-activity age for the inventory."""
    if seconds is None or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def render_resume_inventory(in_flight, armed):
    """A5: ONE prompt for the whole backlog, and ONE acknowledgement clears it.

    The gate used to charge one operator question per parked pointer, in
    created_at order, which is what made the over-broad claim expensive to live
    with. Every genuinely in-flight run is now listed in a single block with its
    walker and last-activity age, and one answer covers all of them.

    Armed runs are LISTED but never acknowledged: they cost no decision, yet they
    are still shown. Dropping them from the display would trade one Guiding Policy
    commitment (stop charging for work nobody is doing) for another (never remove a
    surface on which a parked run is shown to the operator)."""
    lines = ["BLOCKED: /execute-plan is resuming while other run(s) in this worktree "
             "are mid-flight.", ""]
    lines.append(f"  In flight ({len(in_flight)}) — these need one decision:")
    for r in in_flight:
        verb = _DISPATCH_VERB.get(r["dispatch"], "continues the run")
        lines.append(f"    • {r['slug']}  slice {r['slice_id']}  "
                     f"({r['dispatch']} — {verb})")
        lines.append(f"        walker: {r['walking_session_id'] or 'unknown'}   "
                     f"last activity: {_fmt_age(r['age_seconds'])}")
    if armed:
        lines.append("")
        lines.append(f"  Parked ({len(armed)}) — approved but nobody is walking them. "
                     "Listed for visibility; they do not need a decision:")
        for r in armed:
            lines.append(f"    • {r['slug']}  next slice {r['slice_id']}   "
                         f"last activity: {_fmt_age(r['age_seconds'])}")
    lines += [
        "",
        "Walker identity and last-activity age are shown so you can decide on what is",
        "visible rather than inferring liveness from timestamps. They are informational:",
        "nothing here disarms a run.",
        "",
        "Invoke AskUserQuestion once, with three options:",
        "  (a) Investigate — show full slice status (completed / in-flight / next) first.",
        "  (b) Proceed — resume now; this one answer covers every run listed above.",
        "  (c) Not now — leave them parked and keep working here.",
        "",
        "The gate clears once AskUserQuestion completes.",
    ]
    return "\n".join(lines)


def gate_check(session_id, session_worktree=None, base=None, now=None):
    """A1+A2+A3, extended by A5: the resume-scoped checkpoint decision, called on a
    /execute-plan invocation. Migrates any legacy pointer, self-heals complete/stale
    runs, then partitions the runs in THIS session's worktree that it neither owns
    nor has acked into two phases.

    A5 phase split (mirrors the entry gate's): a run nobody is walking is LISTED but
    NOT acknowledged — it appears in the inventory the operator sees and costs no
    decision. Only genuinely in-flight runs gate, and where more than one does they
    arrive in ONE block that one acknowledgement clears.

    Returns {block: bool, ...}. `listed` carries the parked runs whether or not the
    call blocks, so the caller can always show them. On block, writes the
    `pending-<sid>` marker the ack step consumes — carrying BOTH the new `run_ids`
    list and the legacy scalar `run_id`, so a half-applied upgrade still acks."""
    base = Path(base) if base else _execplan_ack_dir()
    now = time.time() if now is None else now
    if not session_id:
        return {"block": False, "reason": "no session id"}
    migrate_legacy_pointer(base)
    reap_stale_runs(base, now=now)
    acked = _read_acked_run_ids(base, session_id)
    armed, in_flight = [], []
    for _, doc in _iter_run_pointers(base):
        rid = doc.get("run_id")
        if not rid:
            continue
        if doc.get("owner_session_id") == session_id:
            continue                          # own run — never re-gated
        if rid in acked:
            continue                          # already acknowledged this session
        if not _pointer_visible_to(doc, session_worktree):
            continue                          # different worktree — isolated
        # A pointer written BEFORE the walk-state fields existed has no
        # `walking_session_id` key at all. Reading that as "nobody is walking" would
        # silently stop gating a run that, under the pre-A5 contract, always blocked
        # — a regression rather than the intended redistribution of cost. Absence of
        # the field is unknown, not empty, so it fails CLOSED to in-flight. Every
        # pointer written by current code carries the key (set-active-run writes all
        # three walk-state fields with defaults), so this only ever catches genuine
        # legacy pointers, and the 24h TTL bounds how long one can persist.
        if "walking_session_id" not in doc:
            in_flight.append(doc)
        else:
            (armed if _pointer_walking_session_id(doc) is None
             else in_flight).append(doc)

    _sort = lambda d: (d.get("created_at") or "", d.get("run_id") or "")  # noqa: E731
    armed.sort(key=_sort)
    in_flight.sort(key=_sort)
    listed = [_resume_row(d, now) for d in armed]

    if not in_flight:
        # Parked runs alone never charge an acknowledgement — the diagnosed cost.
        # They are still returned so the caller can display them.
        return {"block": False, "listed": listed, "armed_count": len(armed)}

    rows = [_resume_row(d, now) for d in in_flight]
    rids = [r["run_id"] for r in rows]
    first = rows[0]
    _atomic_write_json(
        base / f"pending-{session_id}.json",
        {"session_id": session_id, "pending": True,
         # Scalar FIRST for back-compat: a legacy reader that knows only `run_id`
         # still acks something rather than nothing (Design Review — the codebase
         # already anticipates half-applied upgrades by sending both shapes).
         "run_id": first["run_id"], "run_ids": rids,
         "slug": first["slug"], "slice_id": first["slice_id"],
         "dispatch": first["dispatch"], "set_at": _now_iso()})
    return {"block": True, "run_id": first["run_id"], "run_ids": rids,
            "slug": first["slug"], "slice_id": first["slice_id"],
            "dispatch": first["dispatch"],
            "remaining": len(in_flight), "in_flight": rows,
            "listed": listed, "armed_count": len(armed),
            "message": render_resume_inventory(rows, listed)}


def record_ack(session_id, base=None):
    """A1: record that this session acknowledged its pending run — consumes the
    `pending-<sid>` marker and appends the run_id to `acked-<sid>`. Called by the
    PostToolUse AskUserQuestion hook. No-op when there is no pending marker."""
    base = Path(base) if base else _execplan_ack_dir()
    pending = base / f"pending-{session_id}.json"
    doc = _read_pointer_doc(pending)
    if not doc or not doc.get("pending"):
        return {"acked": None}
    rid = doc.get("run_id")
    acked = _read_acked_run_ids(base, session_id)
    # A5: ONE acknowledgement clears every run the single prompt listed. The scalar
    # `run_id` is still read, so a marker written by a pre-A5 gate (or by a
    # half-applied upgrade) still acks its one run instead of nothing.
    batch = doc.get("run_ids")
    if isinstance(batch, (list, tuple)):
        for one in batch:
            if one:
                acked.add(one)
    if rid:
        acked.add(rid)
    _atomic_write_json(base / f"acked-{session_id}.json",
                       {"session_id": session_id,
                        "acked_run_ids": sorted(acked),
                        "updated_at": _now_iso()})
    try:
        pending.unlink()
    except OSError:
        pass
    cleared = [one for one in (batch if isinstance(batch, (list, tuple)) else [])
               if one]
    if rid and rid not in cleared:
        cleared.insert(0, rid)
    # `acked` stays the scalar it always was (back-compat for any caller reading it);
    # `acked_run_ids` reports everything this one acknowledgement cleared.
    return {"acked": rid, "acked_run_ids": cleared}


# --------------------------------------------------------------------------- #
# S1 — contract-hardening domain (execplan-contract-hardening, 2026-07-20).
#
# Deterministic domain code only — NO AI, NO git, NO network; the only I/O is the
# per-run pointer file (`run-<id>.json`) + the slice_execution state surface. These
# verbs make "the walk actually ran / a slice is genuinely checked out / a
# concurrent session is writing" code-checkable rather than AI-narratable, so the
# S3/S4 PreToolUse hooks are thin adapters over them (design v3.1 §1/§2; A1).
#
# Three new per-run pointer fields carry the walk lifecycle:
#   execution_pending  : bool      — a walk is armed / in-flight for this run
#   walking_session_id : str|null  — the session currently walking (the exempt one)
#   current_slice_id   : str|null  — the slice currently checked out (a SLICE VALUE
#                                    is assigned ONLY by checkout-slice [C4]; other
#                                    verbs may only preserve it or null-reset it)
#
# Legacy in-flight pointers written before these fields exist load WITHOUT error:
# the reader helpers below default execution_pending→False, walking/current→None. A
# reversible migration (backup_legacy_pointer + restore_legacy_pointer) protects an
# in-flight run mid-upgrade (A1 guard rail).
# --------------------------------------------------------------------------- #

_NEW_POINTER_FIELDS = ("execution_pending", "walking_session_id", "current_slice_id")


def _pointer_execution_pending(doc):
    """Legacy-safe read of the `execution_pending` field (default False)."""
    return bool(doc.get("execution_pending")) if isinstance(doc, dict) else False


def _pointer_walking_session_id(doc):
    """Legacy-safe read of `walking_session_id` (default None; empty string → None)."""
    v = doc.get("walking_session_id") if isinstance(doc, dict) else None
    return v if isinstance(v, str) and v else None


def _pointer_current_slice_id(doc):
    """Legacy-safe read of `current_slice_id` (default None; empty string → None)."""
    v = doc.get("current_slice_id") if isinstance(doc, dict) else None
    return v if isinstance(v, str) and v else None


def resolve_write_targets(targets, root):
    """A4(b): resolve a slice's declared write targets to ABSOLUTE paths against
    `root`, at WRITE time — never left repo-relative.

    A register cell says `skills/execute-plan/run.py`; the gate compares against a
    target the harness supplies absolute. `_path_inside` resolves a relative path
    against the EVALUATING PROCESS's cwd, so a repo-relative declaration compared
    from a session running in a subdirectory would silently miss and fail open. The
    root is the pointer's `worktree_root` when set, else the root the writing
    session computes. An already-absolute entry is kept as-is.

    A declared target that resolves outside that root simply never matches, which is
    correct: the entry gate does not govern paths outside the worktree at all, so the
    intersection can only ever narrow within the already-contained set.

    NO-ROOT CASE — degrade to whole-checkout, never guess. When `root` is falsy and
    the list contains a relative entry, the whole list resolves to `[]`, which the
    entry gate reads as "no declaration" and therefore blocks the whole checkout.
    Anchoring a relative entry to the evaluating process's cwd instead would bake a
    wrong absolute path into the pointer at write time; it would then never match the
    real target and the block would silently fail OPEN for a file that WAS declared —
    strictly worse than not narrowing at all. Losing the narrowing is cheap; losing
    the block is the bug this plan exists to fix."""
    out = []
    if not targets:
        return out
    base = os.path.abspath(os.path.expanduser(str(root))) if root else None
    for t in targets:
        if not t:
            continue
        s = os.path.expanduser(str(t))
        if os.path.isabs(s):
            out.append(os.path.abspath(s))
        elif base:
            out.append(os.path.abspath(os.path.join(base, s)))
        else:
            return []          # unanchorable relative entry → whole checkout
    return out


def _pointer_write_targets(doc, fallback_root=None):
    """Read the walked slice's declared write targets off `pending_handoff`, resolved
    absolute. Empty/missing → [] , which the entry gate reads as "whole checkout"
    (A4's deliberate fallback — an empty list must NEVER mean "block nothing", which
    would remove the guard entirely).

    STALENESS GUARD. `checkout_slice` keeps `pending_handoff.slice_id` in step with
    `current_slice_id`, but `cmd_set_active_run` accepts an arbitrary handoff, so the
    two CAN disagree — a handoff left over from an earlier arm, or one supplied by a
    caller that did not re-derive it. A declaration belonging to a different slice is
    the failure mode A4 calls worse than no scoping at all: narrower than the truth
    AND disjoint from it, so the files the CURRENT slice is really touching fall
    outside it and go unblocked. When the two disagree the declaration is discarded
    and the gate falls back to whole-checkout."""
    if not isinstance(doc, dict):
        return []
    ph = doc.get("pending_handoff")
    if not isinstance(ph, dict):
        return []
    current = _pointer_current_slice_id(doc)
    declared_for = ph.get("slice_id")
    if current and declared_for and declared_for != current:
        return []                       # stale declaration → whole checkout
    raw = ph.get("write_targets")
    if not isinstance(raw, (list, tuple)) or not raw:
        return []
    root = (doc.get("worktree_root") or "").strip() or fallback_root
    return resolve_write_targets(raw, root)


def _target_in_write_targets(target_path, declared):
    """True iff `target_path` is one of the declared write targets, or lives under one
    (a declared directory covers its contents).

    Reuses `_path_inside` — the SAME resolved-path comparison A1 fixed — rather than a
    private string compare. A4 introduces a second spelling-sensitive test on the very
    containment boundary A1 exists to make spelling-proof, so a fresh comparison here
    would reopen G6 one layer up: a symlink-spelled edit would miss every declared
    target and fall through unblocked."""
    return any(_path_inside(target_path, d) for d in declared if d)


def _move_pointer_aside(pointer):
    """Retire a run pointer by MOVING it aside, never `unlink` — a run pointer is
    the only record that a walk is in flight, and destroying one silently discards
    another session's work (`safe-defaults.md`: `rm <file>` → `mv <file>
    <file>.bak-<ts>`; execplan-gate-blast-radius A2, gap G5).

    The suffix carries the pid as well as the epoch second: a bare timestamp
    collides when two sessions clear in the same second. Because the pid makes the
    DESTINATION unique, the race that actually occurs is the SOURCE vanishing — a
    second clear finds the pointer already moved — so a missing source is tolerated
    and reported as "not cleared", never raised.

    Returns (moved: bool, backup_path: str|None).
    """
    backup = Path(str(pointer) + f".bak-{int(time.time())}-{os.getpid()}")
    try:
        os.replace(str(pointer), str(backup))
    except FileNotFoundError:
        return False, None            # already moved by a concurrent clear
    except OSError:
        return False, None            # unwritable dir etc. — never destroy on failure
    return True, str(backup)


def _resolve_run_pointer(payload, base):
    """Resolve (run_id, pointer_path, doc) from {run_id} OR
    {surface_path[,worktree_root]}. doc is None when the pointer is absent/corrupt
    (callers treat that as fail-closed)."""
    run_id = payload.get("run_id")
    if not run_id:
        surface = payload.get("surface_path")
        if surface:
            wt = (payload.get("worktree_root") or "").strip() or None
            run_id = compute_run_id(surface, wt)
    if not run_id:
        return None, None, None
    pointer = _run_pointer_path(Path(base), run_id)
    return run_id, pointer, _read_pointer_doc(pointer)


def _path_inside(target, root):
    """True iff `target` is `root` itself or lives under it.

    Compared TWICE: first on the literal absolute spellings (the fast, common
    case), then — ONLY when that says "outside" — again on the SYMLINK-RESOLVED
    spellings. Without the second pass a symlink-spelled target reads as outside
    the worktree and BOTH /execute-plan gates silently fail OPEN: the edit is
    waved through rather than contained (execplan-gate-blast-radius A1, gap G6).
    This is live, not hypothetical — `~/Projects` is a symlink to
    `~/repos/Projects`, so the same checkout has two spellings.

    The second pass is purely WIDENING: a target the literal compare already
    contained stays contained (the fast path returns first), so no containment
    decision this function used to make is reversed. `check-execplan-walk-gate.sh`
    mirrors this two-phase shape exactly.

    Guards against the ValueError commonpath raises for mismatched drives /
    abs-vs-rel inputs.
    """
    def _cmp(t, r):
        if not r:
            return False
        try:
            return t == r or os.path.commonpath([t, r]) == r
        except (ValueError, TypeError):
            return False

    try:
        t = os.path.abspath(os.path.expanduser(str(target)))
        r = os.path.abspath(os.path.expanduser(str(root)))
    except (TypeError, ValueError):
        return False
    if _cmp(t, r):
        return True
    # Symlink-resolved second pass. realpath resolves the EXISTING ancestors of a
    # not-yet-created path, so a Write to a new file under a symlinked directory
    # resolves correctly too.
    try:
        return _cmp(os.path.realpath(t), os.path.realpath(r))
    except (TypeError, ValueError, OSError):
        return False


_SPINE_NAME_RE = re.compile(r"_(PLAN|THOUGHT)(_check)?\.md$", re.IGNORECASE)


def _is_excluded_bookkeeping_path(target_path):
    """The entry gate NEVER blocks bookkeeping surfaces (design 1b / [C6]): anything
    under ~/.claude/plans or ~/.claude/state, the `_PLAN`/`_THOUGHT` spine files,
    `TODO.md`, `Diary/**`, and the Minimal adapter's `<plan_stem>.run-state.json`
    (which can sit inside the worktree)."""
    if not target_path:
        return False
    p = os.path.abspath(os.path.expanduser(str(target_path)))
    name = os.path.basename(p)
    home = os.path.abspath(os.path.expanduser("~"))
    if (_path_inside(p, os.path.join(home, ".claude", "plans"))
            or _path_inside(p, os.path.join(home, ".claude", "state"))):
        return True
    if name == "TODO.md":
        return True
    if _SPINE_NAME_RE.search(name):
        return True
    if name.endswith(".run-state.json"):        # Minimal adapter surface [C6]
        return True
    if "Diary" in Path(p).parts[:-1]:           # any Diary/ directory component
        return True
    return False


def _entry_suppress_path(base, session_id):
    return Path(base) / f"entry-suppress-{session_id}.json"


def _read_entry_suppressions(base, session_id):
    """Own-namespace read of a session's path-scoped entry-gate suppressions —
    DISTINCT from execplan-gate-check's pending-/acked- namespace (design 1b)."""
    doc = _read_pointer_doc(_entry_suppress_path(base, session_id))
    paths = doc.get("suppressed_paths") if isinstance(doc, dict) else None
    return set(paths) if isinstance(paths, list) else set()


def _is_entry_path_suppressed(base, session_id, target_path):
    if not session_id or not target_path:
        return False
    t = os.path.abspath(os.path.expanduser(str(target_path)))
    for s in _read_entry_suppressions(base, session_id):
        if os.path.abspath(os.path.expanduser(str(s))) == t:
            return True
    return False


def add_entry_suppression(base, session_id, target_path):
    """Append `target_path` to a session's entry-gate suppression list (own
    namespace `entry-suppress-<sid>.json`). The S3 hook's option-(b) writer; kept in
    run.py so the marker namespace is owned in one deterministic place."""
    base = Path(base)
    supp = _read_entry_suppressions(base, session_id)
    supp.add(os.path.abspath(os.path.expanduser(str(target_path))))
    _atomic_write_json(_entry_suppress_path(base, session_id),
                       {"session_id": session_id,
                        "suppressed_paths": sorted(supp),
                        "updated_at": _now_iso()})
    return {"session_id": session_id, "suppressed_paths": sorted(supp)}


def _resolve_register_text(payload, doc):
    """Register markdown for a run: explicit {register_markdown|spine_path} wins,
    else the pointer's `surface_path` (the relocated _PLAN/_THOUGHT spine). Returns
    None on any failure (callers reject fail-closed — never crash)."""
    if payload.get("register_markdown") is not None:
        return payload["register_markdown"]
    sp = payload.get("spine_path") or (doc.get("surface_path") if doc else None)
    if sp:
        p = os.path.expanduser(str(sp))
        if os.path.isfile(p):
            try:
                return Path(p).read_text(encoding="utf-8")
            except OSError:
                return None
    return None


def _read_exec_map(state_path):
    """Read the slice_execution map from a state surface, mirroring the detection in
    _pointer_completed_count: a Full topic-state JSON nests it under
    `slice_execution`; a bare Minimal run-state map is keyed directly. {} on
    absent/corrupt."""
    doc = _read_pointer_doc(os.path.expanduser(str(state_path)))
    if not isinstance(doc, dict):
        return {}
    if "slice_execution" in doc:
        em = doc.get("slice_execution")
        return em if isinstance(em, dict) else {}
    return doc


def _mark_started_on_state_surface(state_path, slice_id, model_family):
    """Record `slice_id` as `started` on the slice_execution surface, preserving the
    surface's shape. Full topic-state JSON (any name NOT ending `.run-state.json`, or
    one that already carries a `slice_execution` key) nests under `slice_execution`
    and every sibling key is preserved; a bare Minimal `<plan_stem>.run-state.json`
    map is keyed directly. Merges (never clobbers) an existing entry so a prior
    `attempts` survives (S4 parity). This is the ONLY slice_execution mutation the
    checkout verb performs; it NEVER touches the run pointer (so it can never write
    `current_slice_id` — the single-writer invariant)."""
    path = Path(os.path.expanduser(str(state_path)))
    doc = _read_pointer_doc(path)
    keyed = (isinstance(doc, dict) and "slice_execution" in doc) \
        or not path.name.endswith(".run-state.json")
    if keyed:
        if not isinstance(doc, dict):
            doc = {}
        execmap = doc.get("slice_execution")
        if not isinstance(execmap, dict):
            execmap = {}
    else:
        execmap = doc if isinstance(doc, dict) else {}
    entry = execmap.setdefault(slice_id, {})
    entry["status"] = "started"
    entry["model_family"] = model_family
    if keyed:
        doc["slice_execution"] = execmap
        _atomic_write_json(path, doc)
    else:
        _atomic_write_json(path, execmap)


# --- Reversible legacy migration (A1 guard rail) --- #

def _pointer_lacks_new_fields(doc):
    """A pointer predates the S1 fields iff it carries NONE of them."""
    return isinstance(doc, dict) and not any(k in doc for k in _NEW_POINTER_FIELDS)


def _legacy_bak_path(pointer):
    return Path(str(pointer) + ".legacy-bak")


def backup_legacy_pointer(pointer):
    """Copy a pre-migration pointer to `<pointer>.legacy-bak` BEFORE the new fields
    are written onto it. Idempotent — never clobbers an existing backup. Returns
    True iff a backup was taken."""
    pointer = Path(pointer)
    bak = _legacy_bak_path(pointer)
    if pointer.exists() and not bak.exists():
        bak.write_bytes(pointer.read_bytes())
        return True
    return False


def restore_legacy_pointer(pointer):
    """Restore the pre-migration pointer shape from `<pointer>.legacy-bak` and remove
    the backup (the reversible-rollback path). Returns True iff a restore ran."""
    pointer = Path(pointer)
    bak = _legacy_bak_path(pointer)
    if not bak.exists():
        return False
    pointer.write_bytes(bak.read_bytes())
    bak.unlink()
    return True


def migrate_run_pointer_fields(base=None):
    """Fold the S1 walk-state fields onto any in-flight pointer that lacks them,
    backing each up first so the migration is reversible. Idempotent; returns the
    migrated run ids."""
    base = Path(base) if base else _execplan_ack_dir()
    migrated = []
    for p, doc in _iter_run_pointers(base):
        if _pointer_lacks_new_fields(doc):
            backup_legacy_pointer(p)
            newdoc = dict(doc)
            newdoc.setdefault("execution_pending", False)
            newdoc.setdefault("walking_session_id", None)
            newdoc.setdefault("current_slice_id", None)
            _atomic_write_json(p, newdoc)
            migrated.append(doc.get("run_id") or p.stem)
    return migrated


# --- Presence predicate + walk lifecycle verbs + entry predicate --- #

def register_presence(text):
    """(present, non_closing_count) for a slice register. A slice is NON-CLOSING iff
    its type is not one of `CLOSING_SLICE_TYPES`; a register is `present` (routes
    /execute-plan) iff it has ≥2 non-closing slices. A single-work / trivial plan
    (0 or 1 non-closing) is un-gated by construction (C6)."""
    d = parse_slice_register_detail(text)
    n = d["valid_non_closing_count"]
    return n >= 2, n


def become_walker(payload, base):
    """Compare-and-set walker election [C3]: set `walking_session_id` = session_id
    ONLY if the pointer's current value is null OR already this session; otherwise
    REJECT (a second would-be walker cannot clobber the first). Fail-closed — any
    read/parse/write problem rejects."""
    session_id = payload.get("session_id")
    if not session_id:
        return {"ok": False, "rejected": True, "walking_session_id": None,
                "reason": "no session id"}
    _run_id, pointer, doc = _resolve_run_pointer(payload, base)
    if doc is None:
        return {"ok": False, "rejected": True, "walking_session_id": None,
                "reason": "run pointer not found"}
    cur = _pointer_walking_session_id(doc)
    if cur is not None and cur != session_id:
        return {"ok": False, "rejected": True, "walking_session_id": cur,
                "reason": "another session is already walking"}
    if _pointer_lacks_new_fields(doc):              # reversible (A1 guard rail)
        backup_legacy_pointer(pointer)
    newdoc = dict(doc)
    newdoc["walking_session_id"] = session_id
    newdoc["updated_at"] = _now_iso()
    try:
        _atomic_write_json(pointer, newdoc)
    except OSError:
        return {"ok": False, "rejected": True, "walking_session_id": cur,
                "reason": "pointer write failed"}
    return {"ok": True, "rejected": False, "walking_session_id": session_id}


def checkout_slice(payload, base):
    """Atomic slice checkout [C4]: the EXCLUSIVE writer of the pointer's
    `current_slice_id`. In one verb it (a) writes `current_slice_id = slice_id` on
    the run pointer AND (b) records the slice as `started` on the slice_execution
    surface — but ONLY for the run's `next_ready_slice` (dependency-satisfied);
    anything else is REJECTED. The pointer field is written FIRST, then the slice is
    marked started, so there is no window where a slice is `started` while the
    pointer field is unset (which would block the slice's own first edit)."""
    slice_id = payload.get("slice_id")
    if not slice_id:
        return {"ok": False, "rejected": True, "current_slice_id": None,
                "started": False, "reason": "no slice_id"}
    _run_id, pointer, doc = _resolve_run_pointer(payload, base)
    if doc is None:
        return {"ok": False, "rejected": True, "current_slice_id": None,
                "started": False, "reason": "run pointer not found"}
    text = _resolve_register_text(payload, doc)
    if text is None:
        return {"ok": False, "rejected": True, "current_slice_id": None,
                "started": False, "reason": "register not resolvable"}
    state_path = payload.get("state_path") or doc.get("state_path")
    if not state_path:
        return {"ok": False, "rejected": True, "current_slice_id": None,
                "started": False, "reason": "no state_path to record the started slice"}
    slices = parse_slice_register(text)
    bk = InMemoryBookkeepingAdapter()
    for sid, entry in _read_exec_map(state_path).items():
        if isinstance(entry, dict) and entry.get("status") == "completed":
            bk.mark_completed(sid, result=entry.get("result"))
    nxt = next_ready_slice(slices, bk)
    if nxt is None or nxt.id != slice_id:
        return {"ok": False, "rejected": True, "current_slice_id": None,
                "started": False,
                "reason": (f"{slice_id} is not the next ready slice "
                           f"(got {nxt.id if nxt else None})")}
    try:
        family = agent_choice_to_model_family(nxt.agent_choice)
    except ValueError as e:
        return {"ok": False, "rejected": True, "current_slice_id": None,
                "started": False, "reason": str(e)}
    # (a) pointer field FIRST (exclusive writer), then (b) mark started.
    if _pointer_lacks_new_fields(doc):              # reversible (A1 guard rail)
        backup_legacy_pointer(pointer)
    newdoc = dict(doc)
    newdoc["current_slice_id"] = slice_id
    # A4(c): REFRESH the handoff's write targets as the slice advances. Without this
    # the pointer keeps the arm-time record and the in-flight block scopes to slice
    # 1's files for the whole walk, while later slices edit elsewhere entirely —
    # narrower than the truth AND disjoint from it, which is worse than no scoping at
    # all. `checkout_slice` is the exclusive writer of `current_slice_id`, so it is
    # the one place where "which slice is being walked" changes, and therefore the
    # only correct place to re-derive what that slice may touch.
    ph = newdoc.get("pending_handoff")
    if isinstance(ph, dict):
        ph = dict(ph)
        ph["slice_id"] = slice_id
        ph["write_targets"] = resolve_write_targets(
            getattr(nxt, "write_targets", ()) or (),
            (newdoc.get("worktree_root") or "").strip() or None)
        newdoc["pending_handoff"] = ph
    newdoc["updated_at"] = _now_iso()
    _atomic_write_json(pointer, newdoc)
    _mark_started_on_state_surface(state_path, slice_id, family)
    return {"ok": True, "rejected": False, "current_slice_id": slice_id,
            "started": True,
            "write_targets": list(
                (newdoc.get("pending_handoff") or {}).get("write_targets") or [])}


# --------------------------------------------------------------------------- #
# Non-redirect write-target detector (execplan-nonredirect-write-guard, 2026-07-20).
#
# The two /execute-plan gates historically detected a Bash worktree write ONLY via
# the `>` / `>>` shell redirect (a byte-identical `REDIR_RE` in each gate). Non-
# redirect write utilities — cp / mv / install / sed -i / tee / dd of= — write files
# with no redirect and sailed past the matcher, silently bypassing the checkout
# invariant. `extract_bash_write_targets` is the ONE deterministic domain function
# behind the port that both gates now call (Guiding Policy — single change locus):
# it folds the raw-string redirect regex together with a shlex-based per-utility
# operand parser and returns every write DESTINATION (absolute-resolved), leaving
# containment / bookkeeping exclusion to the gates (they already own those filters).
#
# Two hard constraints from the adversarial validation (design + Design Review):
#   * redirect detection runs on the RAW command string, NEVER on shlex tokens —
#     `punctuation_chars=True` tokenizes `2>` and a literal `2 >` identically, which
#     would destroy the numeric-fd (`2>`) / `&>` exclusion the existing regex gives.
#   * a `git commit` sub-command is skipped entirely (defense-in-depth: the entry
#     gate has no commit branch, so a commit MESSAGE containing `>` must not be
#     mis-parsed as a redirect write).
# --------------------------------------------------------------------------- #

# Raw-string redirect matcher — the gates' retired inline `REDIR_RE`, tightened: a
# `>`/`>>` run whose FIRST `>` is NOT preceded by a digit, `&`, or another `>`
# (excludes fd-redirects `2>`, `2>>`, `&>`, and prevents the matcher from starting
# INSIDE a `>>`/`2>>` run — the pre-existing regex could match the second `>` of a
# `2>>` because a `>` is not a digit). Optional leading double-quote, capturing the
# target up to the next whitespace / quote / redirect / pipe / semicolon / `&`.
# Matched with `finditer` so ALL redirect targets in a (sub-)command are captured.
#
# A6 / gap G8 narrowing: the captured target may not BEGIN with `=`. Without the
# `(?!=)` guard, ordinary shell text containing a comparison operator was read as a
# redirect — `pytest "pkg>=1"` yielded the target `=1`, which resolves to an
# in-worktree path and cost a permanent exemption for an operation that never wrote
# anything. `>=` is not a shell redirect operator; a redirect to a file literally
# named `=1` would have to be written `> =1`, which this still declines to detect.
#
# This narrows detection on a security boundary, so it is justified narrowly and
# tested on both sides: the allow-rows below cover the version-constraint shapes,
# and every existing detect-row for a real redirect is retained.
_REDIR_RE = re.compile(r'(?<![0-9&>])>{1,2}\s*"?(?!=)([^\s"><|;&]+)')

# A shlex `punctuation_chars=True` token that is a redirect OPERATOR (contains `<`
# or `>`) — e.g. `>`, `>>`, `<`, `<<`, `>&`, `&>`, `>|`, `<>`. Command SEPARATORS
# (`;`, `&&`, `||`, `|`, `&`, `|&`) are handled by _split_shell_subcommands before
# tokenizing, so within one sub-command the only punctuation tokens left are
# redirects; the utility operand scan stops at the first of them.
_REDIR_OP_RE = re.compile(r'^[<>&]+$')

_ENV_ASSIGN_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*=')


def _split_shell_subcommands(command):
    """Split a RAW shell command string into sub-command raw substrings on UNQUOTED
    command separators (`;`, `&&`, `||`, `|`, `&`). Redirect operators (`<`, `>`,
    `>>`, `&>`) are NOT separators. Quote- and backslash-aware so a separator inside
    quotes / after a backslash does not split. Preserving the raw substring (rather
    than re-joining shlex tokens) is what lets redirect detection keep running on
    raw text (the `2>` constraint)."""
    parts, buf = [], []
    i, n = 0, len(command)
    quote = None  # None | "'" | '"'
    while i < n:
        c = command[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            buf.append(c)
            i += 1
            continue
        if c == '\\' and i + 1 < n:
            buf.append(c)
            buf.append(command[i + 1])
            i += 2
            continue
        if c == ';':
            parts.append(''.join(buf)); buf = []; i += 1; continue
        if c == '&':
            if i + 1 < n and command[i + 1] == '&':
                parts.append(''.join(buf)); buf = []; i += 2; continue
            if i + 1 < n and command[i + 1] == '>':
                buf.append(c); i += 1; continue          # `&>` redirect, not a split
            parts.append(''.join(buf)); buf = []; i += 1; continue   # background `&`
        if c == '|':
            if i + 1 < n and command[i + 1] == '|':
                parts.append(''.join(buf)); buf = []; i += 2; continue
            parts.append(''.join(buf)); buf = []; i += 1
            if i < n and command[i] == '&':              # `|&` pipe-with-stderr
                i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        parts.append(''.join(buf))
    return [p for p in parts if p.strip()]


def _shlex_tokens(subcommand):
    """POSIX shlex tokenize with punctuation_chars so redirect operators surface as
    their own tokens (the operand scan stops at them). Returns [] on any lexer error
    (unbalanced quote etc.) — fail-open: an unparseable sub-command yields no target
    rather than crashing the gate."""
    try:
        lex = shlex.shlex(subcommand, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        return list(lex)
    except ValueError:
        return []


def _strip_env_prefix(toks):
    """Drop leading `VAR=val` assignments and an optional `env [opts] [VAR=val]…`
    prefix so the basename dispatch sees the real utility."""
    i, n = 0, len(toks)
    while i < n and _ENV_ASSIGN_RE.match(toks[i]):
        i += 1
    if i < n and os.path.basename(toks[i]) == 'env':
        i += 1
        while i < n:
            t = toks[i]
            if t == '--':
                i += 1
                break
            if _ENV_ASSIGN_RE.match(t):
                i += 1
                continue
            if t in ('-u', '-C', '-S'):        # env options that take an argument
                i += 2
                continue
            if t.startswith('-'):
                i += 1
                continue
            break
    return toks[i:]


def _trim_at_redirect(toks):
    """Cut a sub-command's tokens at the first redirect-operator token, also dropping
    a trailing pure-digit fd token that belongs to it (so `cp a b 2>log` → `cp a b`,
    not `cp a b 2`). Everything after a redirect is redirect plumbing, never a command
    operand."""
    for idx, t in enumerate(toks):
        if _REDIR_OP_RE.match(t) and ('<' in t or '>' in t):
            end = idx
            if end > 0 and toks[end - 1].isdigit():
                end -= 1
            return toks[:end]
    return toks


def _cp_mv_install_target(rest):
    """cp / mv / install destination: `-t DIR` / `--target-directory[=DIR]` wins;
    else the LAST positional operand when ≥2 operands exist (a lone operand has no
    destination). `--` ends option parsing. Best-effort: unknown options are treated
    as no-arg — the real destination is always the last positional, so this stays
    correct even when an option quietly consumes a value."""
    operands, target_dir, seen_ddash = [], None, False
    i = 0
    while i < len(rest):
        t = rest[i]
        if not seen_ddash and t == '--':
            seen_ddash = True; i += 1; continue
        if not seen_ddash and t.startswith('-') and t != '-':
            if t == '-t':
                if i + 1 < len(rest):
                    target_dir = rest[i + 1]
                i += 2; continue
            if t.startswith('--target-directory'):
                if '=' in t:
                    target_dir = t.split('=', 1)[1]
                    i += 1; continue
                if i + 1 < len(rest):
                    target_dir = rest[i + 1]
                i += 2; continue
            i += 1; continue
        operands.append(t); i += 1
    if target_dir:
        return [target_dir]
    if len(operands) >= 2:
        return [operands[-1]]
    return []


def _sed_targets(rest):
    """sed writes files ONLY in-place. Detect in-place (`-i`, `-i.bak`, `--in-place`,
    `--in-place=.bak`, or an `i` inside a short-option cluster like `-ni`/`-in`);
    when absent, no target. GNU `-i` semantics (optional suffix ATTACHED, no separate
    arg) — the plan's listed variants; BSD `-i ''` separate-suffix is an accepted
    residual. File operands are the positionals after the script (the first bare
    operand is the script unless `-e`/`-f` supplied one)."""
    inplace, files = False, []
    saw_script_opt, consumed_script, seen_ddash = False, False, False
    i = 0
    while i < len(rest):
        t = rest[i]
        if not seen_ddash and t == '--':
            seen_ddash = True; i += 1; continue
        if not seen_ddash and t.startswith('--'):
            if t == '--in-place' or t.startswith('--in-place='):
                inplace = True; i += 1; continue
            if t in ('--expression', '--file'):
                saw_script_opt = True; i += 2; continue
            if t.startswith('--expression=') or t.startswith('--file='):
                saw_script_opt = True; i += 1; continue
            i += 1; continue
        if not seen_ddash and t.startswith('-') and t != '-':
            body = t[1:]
            if 'i' in body:
                inplace = True
            if body and body[0] in ('e', 'f'):
                saw_script_opt = True
                if body in ('e', 'f'):     # bare `-e` / `-f` → next token is the arg
                    i += 2; continue
                i += 1; continue           # attached, e.g. `-e's/a/b/'`
            if body and body[-1] in ('e', 'f'):   # cluster ending in e/f, e.g. `-ne`
                saw_script_opt = True
                i += 2; continue           # consume the following script/file arg
            i += 1; continue
        # positional operand
        if not consumed_script and not saw_script_opt:
            consumed_script = True; i += 1; continue
        files.append(t); i += 1
    return files if inplace else []


def _tee_targets(rest):
    """tee writes ALL its file operands (options never take a separate arg:
    `-a`/`--append`, `-i`/`--ignore-interrupts`, `-p`, `--output-error[=MODE]`)."""
    files, seen_ddash = [], False
    for t in rest:
        if not seen_ddash and t == '--':
            seen_ddash = True; continue
        if not seen_ddash and t.startswith('-') and t != '-':
            continue
        files.append(t)
    return files


def _dd_target(rest):
    """dd writes the file named by its `of=` operand (first wins); no `of=` → read-only."""
    for t in rest:
        if t.startswith('of='):
            return [t[3:]]
    return []


_WRITE_UTILS = {
    'cp': _cp_mv_install_target,
    'mv': _cp_mv_install_target,
    'install': _cp_mv_install_target,
    'sed': _sed_targets,
    'tee': _tee_targets,
    'dd': _dd_target,
}


def _is_git_commit(toks):
    """True iff a sub-command's tokens are a `git commit` (skipped entirely — a
    commit message with a `>` must never be parsed as a redirect write). Consumes the
    argument of the global `-C <path>` / `-c <name=value>` options so their value is
    not mistaken for the subcommand (`git -C /wt commit …`)."""
    if not toks or os.path.basename(toks[0]) != 'git':
        return False
    i, n = 1, len(toks)
    while i < n:
        t = toks[i]
        if t in ('-C', '-c'):
            i += 2; continue          # global option that takes a SEPARATE argument
        if t.startswith('-'):
            i += 1; continue          # other global option (no separate arg / `=`-joined)
        return t == 'commit'          # first non-option token is the subcommand
    return False


def _resolve_target(target, cwd):
    """Resolve a raw write target to an absolute path against the command's cwd, so
    the gates' prefix-based containment/exclusion filters work uniformly. `~` is
    expanded; a relative path joins onto cwd (falling back to the process cwd only
    when the caller passed none)."""
    if not target:
        return None
    p = os.path.expanduser(str(target))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    return os.path.abspath(p)


def extract_bash_write_targets(command, cwd=None):
    """Return every worktree-agnostic write DESTINATION a Bash command would touch —
    both `>`/`>>` redirects AND the non-redirect write utilities cp / mv / install /
    sed -i / tee / dd of=. Containment (inside-worktree?) and bookkeeping exclusion
    are NOT decided here — the gates own those. Deterministic, no I/O, fail-open
    (any parse failure yields fewer targets, never a crash).

    Returns {"targets": [<abspath str>, …]} with order preserved and duplicates
    removed."""
    out, seen = [], set()

    def _add(raw):
        p = _resolve_target(raw, cwd)
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    for sub in _split_shell_subcommands(command or ""):
        toks = _shlex_tokens(sub)
        if _is_git_commit(toks):
            continue                       # skip the whole git-commit sub-command
        # (a) redirect targets — on the RAW sub-command string (never shlex tokens).
        for m in _REDIR_RE.finditer(sub):
            _add(m.group(1))
        # (b) utility write targets — from shlex tokens.
        if not toks:
            continue
        rest_toks = _trim_at_redirect(_strip_env_prefix(toks))
        if not rest_toks:
            continue
        handler = _WRITE_UTILS.get(os.path.basename(rest_toks[0]))
        if handler is None:
            continue
        for raw in handler(rest_toks[1:]):
            _add(raw)
    return {"targets": out}


def execplan_entry_check(writing_session_id, worktree_root, target_path, base=None):
    """Entry-boundary predicate (design 1b). BLOCKS iff ALL hold: a `run-<id>.json`
    in this worktree has `execution_pending:true` AND writing_session_id !=
    walking_session_id AND a target is inside worktree_root AND that target is NOT an
    excluded bookkeeping path AND not path-suppressed for this session. Own marker
    namespace (`entry-suppress-<sid>.json`) — NEVER reuses execplan-gate-check's
    pending-/acked- namespace. Deterministic; the S3 PreToolUse hook is a thin
    adapter over this.

    Backward-compatible SUPERSET signature (execplan-nonredirect-write-guard): the
    third argument accepts EITHER a single scalar path (legacy — one redirect/Edit
    target) OR a LIST of paths (the new multi-target Bash detector). This lets a
    partial the deploy step / rollback that pairs a new hook with old Python (or vice
    versa) never crash into a fail-open silent-bypass window. The pointer scan is
    target-independent (it depends only on the worktree + session), so the block
    decision is: block iff ANY supplied target is gateable AND a pending non-walker
    pointer owns this worktree — blocking on the FIRST gateable target."""
    base = Path(base) if base else _execplan_ack_dir()
    wt = (worktree_root or "").strip()
    if target_path is None:
        targets = []
    elif isinstance(target_path, (list, tuple)):
        targets = [t for t in target_path if t]
    else:
        targets = [target_path]
    if not targets:
        return {"block": False, "reason": "no target_path"}

    def _gateable_reason(tp):
        """(gateable_bool, non_block_reason_or_None) for one target — the exact
        per-target checks the legacy scalar path applied, in the same order."""
        if _is_excluded_bookkeeping_path(tp):
            return False, "excluded bookkeeping path"
        if not wt or not _path_inside(tp, wt):
            return False, "target outside worktree"
        if _is_entry_path_suppressed(base, writing_session_id, tp):
            return False, "path suppressed for this session"
        return True, None

    first_gateable = next((tp for tp in targets if _gateable_reason(tp)[0]), None)
    if first_gateable is None:
        # No target is gateable — surface the first target's reason (scalar parity).
        return {"block": False, "reason": _gateable_reason(targets[0])[1]}

    now = time.time()
    for _p, doc in _iter_run_pointers(base):
        if not _pointer_execution_pending(doc):
            continue
        if not _pointer_visible_to(doc, wt):        # different worktree — isolated
            continue

        walker = _pointer_walking_session_id(doc)
        if walker is None:
            # ---- ARMED phase (A3). A pointer armed at /plan Step 11 asserts a walk
            # that has not begun and may never begin, so it constrains ONLY the
            # session that approved the plan — every other session works untouched.
            # Holding the owner is deliberate, not collateral: it is what routes a
            # multi-slice plan through the walker instead of being hand-implemented.
            #
            # RESIDUAL, stated rather than claimed at parity: `owner_session_id` is
            # interpolated into a printf by an AI at /plan Step 11, while
            # `walking_session_id` is written by code at the moment the event occurs.
            # A wrong owner means the armed phase constrains nobody. This guarantee is
            # therefore softer than the whole-checkout block it replaces.
            if writing_session_id != (doc.get("owner_session_id") or None):
                continue
            blocked_target = first_gateable
            phase = "armed"
            reason = ("this session approved a multi-slice plan that has not been "
                      "walked yet — run /execute-plan to walk it")
        else:
            # ---- IN-FLIGHT phase. A real walk owns the worktree; the walker itself
            # is exempt, and everyone else is scoped to the files the WALKED SLICE
            # declares it will touch.
            if walker == writing_session_id:
                continue
            declared = _pointer_write_targets(doc, fallback_root=wt)
            if declared:
                blocked_target = next(
                    (tp for tp in targets
                     if _gateable_reason(tp)[0] and _target_in_write_targets(tp, declared)),
                    None)
                if blocked_target is None:
                    continue        # in flight, but not touching this slice's files
            else:
                # Empty declaration ⇒ whole checkout, today's behaviour. NEVER
                # "block nothing": a fail-open default on a containment test is the
                # failure mode that produced G6.
                blocked_target = first_gateable
            phase = "in-flight"
            reason = "an in-flight /execute-plan walk owns this worktree"

        slug = surface_slug(doc.get("surface_path"))
        ph = doc.get("pending_handoff") or {}
        slice_id = (_pointer_current_slice_id(doc) or ph.get("slice_id") or "—")
        age = _run_age_seconds(doc, now)
        return {"block": True, "run_id": doc.get("run_id"), "slug": slug,
                "slice_id": slice_id, "blocked_target": blocked_target,
                "phase": phase,
                # Liveness is DISPLAYED, never acted upon (Guiding Policy): these two
                # fields let the operator decide on visible fact instead of
                # reconstructing liveness from timestamps by hand and getting it
                # wrong. No code path may disarm a run on an inferred-dead verdict.
                "walking_session_id": walker,
                "last_activity_age_seconds": (int(age) if age is not None else None),
                "scoped_to_write_targets": bool(
                    walker is not None and _pointer_write_targets(doc, fallback_root=wt)),
                "reason": reason}
    return {"block": False}


# --------------------------------------------------------------------------- #
# S2 — execution-boundary receipts + walk gate (execplan-contract-hardening,
# 2026-07-20).
#
# Deterministic domain code only — NO AI, NO git, NO network. I/O is the receipt
# store (one JSON file per run_id+slice_id per layer) plus the pointer/state JSON
# the S1 verbs already own. These make "a slice's code specs passed" and "a slice's
# INDEPENDENT conformance check actually ran and PASSED" code-checkable rather than
# AI-narratable, so the S4 PreToolUse walk gate is a thin adapter over
# `walk_gate_check` (design v3.1 §2a/§2b; A2, gap G2).
#
# Two attested receipts per slice (factcheck-convergence.md §7 marker shape, adapted
# to JSON):
#   <slice_id>.code.json        — CODE layer: written by check-code on a deterministic
#                                 all-specs-pass run; the walk gate's commit
#                                 precondition [V3-A].
#   <slice_id>.conformance.json — MODEL layer: verdict AGGREGATED IN CODE by
#                                 record-conformance over the captured RAW outputs of
#                                 the ISOLATED /double-check Explore checkers, via the
#                                 shared engine aggregator (producer-never-verifies).
#
# The verdict is NEVER trusted from a producer-supplied inline field: record-
# conformance REFUSES a top-level `verdict` key [C1], and the legacy inline-trusting
# `cmd_check_conformance` is FENCED out of the receipt path [C4].
# --------------------------------------------------------------------------- #

_RECEIPT_SCHEMA_VERSION = 3   # factcheck-convergence.md §7 schema epoch


def _execplan_state_root():
    """State root for the S2 receipt store. Honors EXECPLAN_ACK_STATE_DIR — the SAME
    test-isolation override the S1/pointer code uses (`_execplan_ack_dir`) — else the
    live `~/.claude/state` namespace. Receipts live under `<root>/execplan/receipts`
    (a sibling of the pointer dir), so a test that isolates the pointer dir isolates
    the receipts too."""
    env = os.environ.get("EXECPLAN_ACK_STATE_DIR")
    return Path(env) if env else (Path.home() / ".claude" / "state")


def _receipt_dir(run_id, state_root=None):
    root = Path(state_root) if state_root else _execplan_state_root()
    return root / "execplan" / "receipts" / str(run_id)


def _code_receipt_path(run_id, slice_id, state_root=None):
    return _receipt_dir(run_id, state_root) / f"{slice_id}.code.json"


def _conformance_receipt_path(run_id, slice_id, state_root=None):
    return _receipt_dir(run_id, state_root) / f"{slice_id}.conformance.json"


def _read_receipt(path):
    """Read a receipt JSON (dict, or None on absent/corrupt) — tolerant like the
    pointer reader."""
    return _read_pointer_doc(path)


def _conformance_receipt_is_pass(run_id, slice_id, state_root=None):
    doc = _read_receipt(_conformance_receipt_path(run_id, slice_id, state_root))
    return isinstance(doc, dict) and doc.get("verdict") == "PASS"


def _code_receipt_exists(run_id, slice_id, state_root=None):
    return isinstance(
        _read_receipt(_code_receipt_path(run_id, slice_id, state_root)), dict)


def write_code_receipt(run_id, slice_id, *, state_root=None, detail=None):
    """Persist the CODE-layer receipt on a deterministic all-specs-pass check-code run
    [V3-A]. §7 shape adapted for a code-layer receipt (no model panel — the code layer
    has no checkers). The walk gate reads this as the commit precondition (iii)."""
    path = _code_receipt_path(run_id, slice_id, state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "kind": "code",
        "layer": "code",
        "verdict": "PASS",
        "run_id": run_id,
        "slice_id": slice_id,
        "checker_count": 0,
        "checked_at": _now_iso(),
    }
    if detail is not None:
        receipt["detail"] = detail
    _atomic_write_json(path, receipt)
    return path


def _load_aggregate_round_verdict():
    """Import the SHARED verdict aggregator `_factcheck_engine.aggregate_round_verdict`
    — REUSED, never copied [design v3.1 §5]; the SAME pure aggregator the shipped
    /plan consumer calls (`pre_plan_gates.py:3088-3094`). This is the ONE deliberate,
    design-sanctioned coupling to the engine — a PURE function over captured outputs,
    distinct from the transcript reader this module deliberately replicates. Located
    relative to the skill's config root so it resolves identically in prod (~/.claude)
    and a config-source staging clone; an env override (EXECPLAN_FACTCHECK_ENGINE_DIR)
    supports test isolation."""
    override = os.environ.get("EXECPLAN_FACTCHECK_ENGINE_DIR")
    hooks_dir = (Path(override) if override
                 else Path(__file__).resolve().parents[2] / "hooks")
    if str(hooks_dir) not in sys.path:
        sys.path.insert(0, str(hooks_dir))
    from _factcheck_engine import aggregate_round_verdict  # noqa: E402
    return aggregate_round_verdict


def _normalize_captured_checkers(checkers_json):
    """Normalize the orchestrator-captured /double-check outputs into the shape
    `aggregate_round_verdict` consumes: a non-empty list of {checker, model, verdict},
    where each `verdict` is one checker's RAW captured output text/struct. Accepts a
    list of {model?, verdict} or a {"checkers": [...]} wrapper (mirrors the shipped
    /plan consumer `_read_captured_checkers`). Raises ValueError on empty/malformed
    input — there is deliberately NO fallback to a fabricated PASS (emitting a verdict
    with no checker evidence is exactly the honor-system hole this closes)."""
    data = checkers_json
    if isinstance(data, dict) and "checkers" in data:
        data = data["checkers"]
    if not isinstance(data, list) or not data:
        raise ValueError(
            "provide checkers_json (a non-empty list of {model, verdict} — the "
            "captured isolated /double-check outputs)")
    norm = []
    for i, item in enumerate(data):
        if not isinstance(item, dict) or "verdict" not in item:
            raise ValueError(f"checker #{i + 1} missing required 'verdict' field")
        norm.append({"checker": item.get("checker", i + 1),
                     "model": item.get("model", "unknown"),
                     "verdict": str(item["verdict"])})
    return norm


def record_conformance(payload, state_root=None):
    """MODEL-layer conformance receipt writer [V3-A / C1]. Computes the verdict IN CODE
    by aggregating the captured raw outputs of the ISOLATED /double-check Explore
    checkers via the shared engine aggregator (kind='recommendation') — the SAME
    producer-never-verifies boundary the shipped /plan consumer uses. It NEVER trusts a
    producer-supplied inline verdict: a top-level `verdict` key is REFUSED [C1] — the
    verdict is ONLY ever the aggregator's output over `checkers_json`.

    Returns {status, verdict, receipt_path, run_id, slice_id}; on any refusal / usage /
    normalization failure, status='ERROR' with verdict/receipt_path None and NO receipt
    written."""
    run_id = payload.get("run_id")
    slice_id = payload.get("slice_id")

    def _err(msg):
        return {"status": "ERROR", "verdict": None, "receipt_path": None,
                "run_id": run_id, "slice_id": slice_id, "error": msg}

    # [C1] Refuse an inline verdict OUTRIGHT — the forgery path is closed. The verdict
    # is computed ONLY by the aggregator over the isolated checkers' captured outputs;
    # a producer-supplied `verdict` is never consulted (code owns the verdict).
    if "verdict" in payload:
        return _err(
            "record-conformance REFUSES an inline 'verdict' [C1]; supply 'checkers_json' "
            "(the captured isolated /double-check outputs) — the verdict is computed in "
            "code by aggregate_round_verdict, never trusted from the producer")
    if not run_id or not slice_id:
        return _err("provide run_id and slice_id")
    try:
        checkers = _normalize_captured_checkers(payload.get("checkers_json"))
    except ValueError as e:
        return _err(str(e))
    try:
        max_rounds = int(payload.get("max_rounds", 2))
    except (TypeError, ValueError):
        max_rounds = 2
    try:
        round_num = int(payload.get("round_num", 1))
    except (TypeError, ValueError):
        round_num = 1
    is_final = bool(payload.get("is_final", round_num >= max_rounds))
    aggregate_round_verdict = _load_aggregate_round_verdict()
    verdict, _prior = aggregate_round_verdict(
        checkers, is_final=is_final, kind="recommendation")
    path = _conformance_receipt_path(run_id, slice_id, state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": _RECEIPT_SCHEMA_VERSION,
        "kind": "recommendation",
        "layer": "conformance",
        "verdict": verdict,
        "run_id": run_id,
        "slice_id": slice_id,
        "rounds": round_num,
        "checker_count": len(checkers),
        "checker_models": [c["model"] for c in checkers],
        "checked_at": _now_iso(),
    }
    _atomic_write_json(path, receipt)
    return {"status": "OK", "verdict": verdict, "receipt_path": str(path),
            "run_id": run_id, "slice_id": slice_id}


_SLICE_TAG_RE = re.compile(r"\[SLICE:\s*(S\d+)\s*\]", re.IGNORECASE)
_BARE_SLICE_RE = re.compile(r"\b(S\d+)\b", re.IGNORECASE)


def _parse_slice_tag(target_or_tag):
    """Extract a slice id (S<digits>) from a spawn tag `[SLICE:Sn]` (the NEW tag on the
    existing bracketed-tag idiom [C2]); union-fallback to a bare `Sn` token when
    untagged. None when no slice token is present (the caller fails closed)."""
    if not target_or_tag:
        return None
    m = _SLICE_TAG_RE.search(str(target_or_tag))
    if m:
        return m.group(1).upper()
    m = _BARE_SLICE_RE.search(str(target_or_tag))
    return m.group(1).upper() if m else None


def _parse_commit_slice(target_or_tag):
    """Extract Sn from a commit target `Sn:` (or a bare `Sn`)."""
    if not target_or_tag:
        return None
    m = re.match(r"\s*(S\d+)\s*:?", str(target_or_tag), re.IGNORECASE)
    return m.group(1).upper() if m else None


def walk_gate_check(payload, base=None, state_root=None):
    """Execution-boundary predicate the S4 PreToolUse walk gate calls — enforces the
    checkout invariant [V3-E / design §2b]. Active ONLY for the run's
    `walking_session_id`: when writing_session_id != walking_session_id this verb is
    NOT the authority (the S1/S3 ENTRY gate blocks a non-walker), so it returns a
    non-block ALLOW — no double-gating.

    For the walking session it is fail-closed:
      (i)   action edit/write — a worktree code edit is ALLOWED only while
            `current_slice_id` is set (a dep-satisfied slice was checked out via the
            atomic `checkout-slice` verb over `next_ready_slice`); no slice checked
            out → BLOCK.
      (ii)  action agent — a slice-implementation spawn tagged `[SLICE:Sn]` is ALLOWED
            only when Sn == `current_slice_id` AND every slice in Sn's `depends_on` has
            a conformance PASS receipt; else BLOCK.
      (iii) action commit — a `Sn:` commit is BLOCKED unless Sn's code-layer receipt
            exists.

    Scope limit [C6]: this gates "SOME dep-satisfied slice is checked out," NOT "THIS
    edit belongs to THAT slice" — the slice register carries no per-file manifest, so
    out-of-order is prevented at SLICE granularity, not per file.

    Returns {block: bool, reason?, current_slice_id?}."""
    base = Path(base) if base else _execplan_ack_dir()
    writing_session_id = payload.get("writing_session_id")
    action = (payload.get("action") or "").strip().lower()
    target_or_tag = payload.get("target_or_tag")
    _run_id, _pointer, doc = _resolve_run_pointer(payload, base)
    walking = _pointer_walking_session_id(doc) if doc else None
    # Not the walking session → the ENTRY gate is the authority; do not double-gate.
    if not doc or walking is None or writing_session_id != walking:
        return {"block": False,
                "reason": "not the walking session (entry gate is the authority)"}
    run_id = doc.get("run_id") or _run_id
    current = _pointer_current_slice_id(doc)

    if action in ("edit", "write", "bash"):
        # (i) checkout invariant — a code edit needs a dep-satisfied slice checked out.
        if current is None:
            return {"block": True, "current_slice_id": None,
                    "reason": ("no slice is checked out — run `checkout-slice` for the "
                               "next ready slice before editing worktree code")}
        return {"block": False, "current_slice_id": current}

    if action in ("agent", "spawn"):
        # (ii) a [SLICE:Sn] implementation spawn — Sn must be the checked-out slice AND
        # every dep of Sn must carry a conformance PASS receipt.
        sn = _parse_slice_tag(target_or_tag)
        if sn is None:
            return {"block": True, "current_slice_id": current,
                    "reason": "spawn is missing a parseable [SLICE:Sn] tag"}
        if sn != current:
            return {"block": True, "current_slice_id": current,
                    "reason": f"{sn} is not the checked-out slice (current={current})"}
        text = _resolve_register_text(payload, doc)
        if text is None:
            return {"block": True, "current_slice_id": current,
                    "reason": "slice register not resolvable — cannot verify dependencies"}
        deps = []
        for s in parse_slice_register(text):
            if s.id == sn:
                deps = list(s.depends_on)
                break
        missing = [dep for dep in deps
                   if not _conformance_receipt_is_pass(run_id, dep, state_root)]
        if missing:
            return {"block": True, "current_slice_id": current,
                    "reason": (f"{sn} has dependencies without a conformance PASS "
                               f"receipt: {', '.join(missing)}")}
        return {"block": False, "current_slice_id": current}

    if action == "commit":
        # (iii) a `Sn:` commit needs Sn's code-layer receipt.
        sn = _parse_commit_slice(target_or_tag)
        if sn is None:
            return {"block": True, "current_slice_id": current,
                    "reason": "commit target is missing a parseable `Sn:` slice id"}
        if not _code_receipt_exists(run_id, sn, state_root):
            return {"block": True, "current_slice_id": current,
                    "reason": (f"{sn} has no code-layer receipt — run `check-code` (all "
                               "specs must pass) before committing the slice")}
        return {"block": False, "current_slice_id": current}

    # Unknown action: the walk gate's authority is edit/write, agent, and commit only —
    # a different action is not this gate's concern (the entry/other gates cover it).
    return {"block": False, "current_slice_id": current,
            "reason": f"action {action!r} is not gated by the walk gate"}


# --------------------------------------------------------------------------- #
# S9 — end-of-plan close-out: three PURE consumers of already-persisted run state
# (the slice_execution map + the loop's `dispatched` list). None of this touches
# the loop topology / ports / seams / BookkeepingPort lifecycle (spine A16/A17/U5;
# S9). The /double-check verify + the /plan-followups-review walk + AskUserQuestion
# are SKILL.md-driven (Python cannot invoke them) — run.py owns ONLY the
# deterministic rendering + collection. Producer-never-verifies holds by topology:
# collect_observations does NO verification; observations arrive pre-verified from
# the SKILL-driven /double-check gate upstream of the harvest.
# --------------------------------------------------------------------------- #

# Statuses for which the S5 fail-closed model-pin gate PROVES the slice ran its
# assigned family (a wrong-model run raises SliceAttemptError BEFORE the
# `committed` checkpoint — make_model_pin_verify, this file), so used_model can be
# derived == assigned_model when the run did not capture it explicitly.
_MODEL_PROVEN_STATUSES = ("completed", "committed")


def _first_present(*candidates):
    """Return the first candidate that is not None; else None. Distinguishes a
    real 0/False (kept) from an absent field (skipped) — `or` would not."""
    for c in candidates:
        if c is not None:
            return c
    return None


def _entry_result(entry):
    """The slice entry's nested spawn `result` dict (the SKILL-driven dispatch
    threads used_model / model_aborts / tokens / observations through it), or {}."""
    if isinstance(entry, dict) and isinstance(entry.get("result"), dict):
        return entry["result"]
    return {}


def render_implementation_report(slice_execution):
    """A16 — per-slice implementation report rows over the slice_execution map.

    Pure: reads the BookkeepingPort.all() map shape (identical under both tiers)
    and returns one row per slice, sorted by slice id:
        {slice_id, assigned, aborts, used_model, status}

    Field precedence (no fabrication — an un-captured field renders as "—"):
      assigned   ← assigned_model, else model_family, else "—"
      aborts     ← model_aborts (top-level, else nested result); 0 is kept; else "—"
      used_model ← used_model (top-level, else nested result); else, for a
                   `completed`/`committed` slice, == assigned (PROVABLE by the S5
                   fail-closed gate); else "—"
      status     ← status, else "—"
    """
    rows = []
    for sid in sorted((slice_execution or {}).keys()):
        entry = (slice_execution or {}).get(sid)
        entry = entry if isinstance(entry, dict) else {}
        result = _entry_result(entry)
        assigned = _first_present(entry.get("assigned_model"),
                                  entry.get("model_family"))
        status = entry.get("status")
        aborts = _first_present(entry.get("model_aborts"),
                                result.get("model_aborts"))
        used = _first_present(entry.get("used_model"), result.get("used_model"))
        if used is None and assigned is not None and status in _MODEL_PROVEN_STATUSES:
            used = assigned   # provable by the S5 fail-closed model-pin gate
        rows.append({
            "slice_id": sid,
            "assigned": assigned if assigned is not None else "—",
            "aborts": aborts if aborts is not None else "—",
            "used_model": used if used is not None else "—",
            "status": status if status is not None else "—",
        })
    return rows


def render_implementation_report_md(rows):
    """Render report rows (from render_implementation_report) as a markdown table.
    Pure; an empty row list yields the header + a one-line `(no slices)` note."""
    header = ("| Slice | Assigned | Aborts | Used Model | Status |\n"
              "|-------|----------|--------|------------|--------|")
    if not rows:
        return header + "\n| _(no slices)_ |  |  |  |  |"
    lines = [header]
    for r in rows:
        lines.append(
            f"| {r['slice_id']} | {r['assigned']} | {r['aborts']} "
            f"| {r['used_model']} | {r['status']} |"
        )
    return "\n".join(lines)


def render_diagnostics(slice_execution, *, restarts=None):
    """A17 — diagnostic correlations, CAPTURED but NOT gated. Pure; NEVER raises.

    Aggregates the spine A17 correlations from whatever the run captured into the
    slice_execution map (tokens / model_aborts / conformance_failures /
    routine-intervention engagement, top-level or nested in `result`). Absent
    fields contribute nothing and are reported via a `captured` coverage block —
    never fabricated, never inferred as a threshold. `gated` is hard-coded False:
    this surface informs, it never blocks close-out. `restarts` (orchestrator
    re-invocation count) is supplied by the caller; None when not provided."""
    sx = slice_execution or {}
    n = sum(1 for v in sx.values() if isinstance(v, dict))
    tokens_by_family = {}
    aborts_total, aborts_captured = 0, 0
    conf_total, conf_captured = 0, 0
    routine_engaged, routine_captured = 0, 0
    # NS8 — per-slice timing + attended share (captured-not-gated).
    attended_count = 0
    timed_count, timing_minutes_total = 0, 0.0
    for entry in sx.values():
        if not isinstance(entry, dict):
            continue
        result = _entry_result(entry)
        fam = _first_present(entry.get("assigned_model"),
                             entry.get("model_family")) or "unknown"
        tok = _first_present(entry.get("tokens"), result.get("tokens"))
        if tok is not None:
            tokens_by_family[fam] = tokens_by_family.get(fam, 0) + tok
        ab = _first_present(entry.get("model_aborts"), result.get("model_aborts"))
        if ab is not None:
            aborts_total += ab
            aborts_captured += 1
        cf = _first_present(entry.get("conformance_failures"),
                            result.get("conformance_failures"))
        if cf is not None:
            conf_total += cf
            conf_captured += 1
        ri = _first_present(entry.get("routine_intervention_engaged"),
                            result.get("routine_intervention_engaged"))
        if ri is not None:
            routine_captured += 1
            if ri:
                routine_engaged += 1
        if entry.get("attended"):
            attended_count += 1
        mins = _iso_minutes(entry.get("started_at"), entry.get("completed_at"))
        if mins is not None:
            timed_count += 1
            timing_minutes_total += mins
    return {
        "slices": n,
        "tokens_per_family": tokens_by_family,
        "restart_count": restarts,
        "model_aborts_total": aborts_total,
        "model_abort_rate": (aborts_total / n) if n else 0,
        "conformance_failures_total": conf_total,
        "conformance_failure_rate": (conf_total / n) if n else 0,
        "routine_intervention_engagement_rate": (
            (routine_engaged / routine_captured) if routine_captured else 0),
        # NS8 — captured-not-gated timing/attended diagnostics (additive).
        "timing": {
            "timed_slices": timed_count,
            "total_minutes": round(timing_minutes_total, 4),
            "attended_slices": attended_count,
            "attended_share": (attended_count / n) if n else 0,
        },
        "captured": {
            "tokens_slices": sum(1 for e in sx.values() if isinstance(e, dict)
                                 and _first_present(e.get("tokens"),
                                                    _entry_result(e).get("tokens"))
                                 is not None),
            "model_aborts_slices": aborts_captured,
            "conformance_failures_slices": conf_captured,
            "routine_intervention_slices": routine_captured,
            "timing_slices": timed_count,
            "attended_slices": attended_count,
        },
        "gated": False,   # A17: captured, NOT gated — informational only.
    }


def _now_iso():
    """Current UTC wall-clock as an ISO-8601 string (NS8 per-slice timing). A
    normal module clock — NOT a workflow-script context, so datetime.now is fine."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _iso_minutes(started_at, completed_at):
    """Wall-clock minutes between two ISO-8601 timestamps (NS8). None when either
    is absent/unparseable or the interval is negative. Pure; never raises."""
    if not started_at or not completed_at:
        return None
    from datetime import datetime
    try:
        s = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    delta = (e - s).total_seconds() / 60.0
    return delta if delta >= 0 else None


def compute_omtm(slice_execution):
    """OMTM (design #13 / locked Metrics): hands-off minutes per PLAIN
    implementation slice = total unattended orchestrator wall-clock minutes /
    count of plain implementation slices. Plan-needing slices AND attended slices
    are EXCLUDED from both numerator and denominator, so attended planning time
    cannot corrupt the orchestration-efficiency signal. CAPTURED-NOT-GATED: returns
    the value (None until timing is captured) + a coverage block; never raises,
    never blocks. A slice's type is read from `type`/`slice_type` on the entry."""
    sx = slice_execution or {}
    total_minutes, counted = 0.0, 0
    excl_attended, excl_plan, missing_timing = 0, 0, 0
    for sid in sorted(sx.keys()):
        entry = sx.get(sid)
        if not isinstance(entry, dict):
            continue
        if entry.get("attended"):
            excl_attended += 1
            continue
        stype = str(entry.get("type") or entry.get("slice_type") or "").strip().lower()
        if stype in PLAN_NEEDING_TYPES:
            excl_plan += 1
            continue
        mins = _iso_minutes(entry.get("started_at"), entry.get("completed_at"))
        if mins is None:
            missing_timing += 1
            continue
        total_minutes += mins
        counted += 1
    return {
        "omtm_minutes_per_slice": (total_minutes / counted) if counted else None,
        "total_handsoff_minutes": round(total_minutes, 4),
        "plain_impl_slices": counted,
        "excluded": {"attended": excl_attended, "plan_needing": excl_plan,
                     "missing_timing": missing_timing},
        "ready": counted > 0,   # meaningful only once ≥1 plain slice is timed
        "gated": False,         # captured, NOT gated
    }


def _observation_pairs(dispatched_or_map):
    """Normalize either the loop's `dispatched` list or the slice_execution map
    into an ordered list of (slice_id, result_dict). The SKILL-driven dispatch
    threads each slice's observations into its spawn `result["observations"]`."""
    pairs = []
    if isinstance(dispatched_or_map, list):
        for d in dispatched_or_map:
            if isinstance(d, dict) and d.get("slice_id"):
                res = d.get("result") if isinstance(d.get("result"), dict) else {}
                pairs.append((d["slice_id"], res))
    elif isinstance(dispatched_or_map, dict):
        for sid in sorted(dispatched_or_map.keys()):
            pairs.append((sid, _entry_result(dispatched_or_map.get(sid))))
    return pairs


def collect_observations(dispatched_or_map):
    """U5 — collect per-slice verified observations into the /plan-followups-review
    input contract: a list of {id, title, body, source_ref}. Pure; does NO
    verification (observations arrive pre-verified from the SKILL-driven
    /double-check gate; producer-never-verifies by topology). Stable ids
    `<slice_id>-obs<n>`. END-OF-PLAN ONLY — there is no mid-plan path (Q5).

    Each slice's observations live in its spawn `result["observations"]` (a list
    of strings, or dicts with `title`/`body`/`text`). A slice with no observations
    contributes none; an empty run yields []."""
    out = []
    for sid, res in _observation_pairs(dispatched_or_map):
        obs_list = res.get("observations")
        if not isinstance(obs_list, list):
            continue
        n = 0
        for obs in obs_list:
            if isinstance(obs, str):
                body = obs.strip()
            elif isinstance(obs, dict):
                body = str(obs.get("body") or obs.get("text") or "").strip()
            else:
                continue
            if not body:
                continue
            n += 1
            if isinstance(obs, dict) and obs.get("title"):
                title = str(obs["title"]).strip()
            else:
                title = body if len(body) <= 72 else body[:69] + "..."
            out.append({
                "id": f"{sid}-obs{n}",
                "title": title or f"{sid} observation {n}",
                "body": body,
                "source_ref": sid,
            })
    return out


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _emit(payload, code):
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.exit(code)


def _load_register_text(payload):
    """Resolve register markdown from {register_markdown} or {spine_path}."""
    if payload.get("register_markdown") is not None:
        return payload["register_markdown"]
    spine_path = payload.get("spine_path")
    if spine_path:
        path = os.path.expanduser(spine_path)
        if not os.path.isfile(path):
            _emit({"status": "ERROR", "error": f"spine_path not found: {spine_path}"}, 3)
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    _emit({"status": "ERROR",
           "error": "provide register_markdown or spine_path"}, 3)


def resolve_register_source(payload):
    """Facet 1 — pointer-following register loader. Returns
      {text, recognized, pointer_ref, pointer_broken, warning, source}.
    If the loaded text carries an inline register -> source 'inline'. Else, if it
    carries a `slice_register_ref:` pointer, follow it to the spine and read the
    register there ('pointer'). A ref that fails to resolve, or resolves to a file
    with no register, is a LOUD broken pointer (pointer_broken=True + warning) —
    never silently treated as single-step. No ref at all -> 'none' (genuinely
    register-less; not broken)."""
    raw = _load_register_text(payload)  # emits usage(3) if neither given
    d = parse_slice_register_detail(raw)
    if d["recognized"]:
        return {"text": raw, "recognized": True, "pointer_ref": None,
                "pointer_broken": False, "warning": None, "source": "inline"}
    ref = None
    for line in (raw or "").splitlines():
        m = _SLICE_REGISTER_REF_RE.match(line)
        if m:
            ref = m.group(1)
            break
    if not ref:
        return {"text": raw, "recognized": False, "pointer_ref": None,
                "pointer_broken": False, "warning": None, "source": "none"}
    ref_path = ref.split("#", 1)[0]  # strip a trailing #anchor
    spine_path = payload.get("spine_path")
    if os.path.isabs(os.path.expanduser(ref_path)):
        resolved = os.path.expanduser(ref_path)
    elif spine_path:
        resolved = os.path.join(
            os.path.dirname(os.path.expanduser(spine_path)), ref_path)
    else:
        resolved = ref_path
    try:
        if os.path.isfile(resolved):
            with open(resolved, encoding="utf-8") as fh:
                spine_text = fh.read()
            sd = parse_slice_register_detail(spine_text)
            if sd["recognized"]:
                return {"text": spine_text, "recognized": True, "pointer_ref": ref,
                        "pointer_broken": False, "warning": None, "source": "pointer"}
            warn = (f"slice_register_ref points to {ref!r} but that file contains "
                    f"no recognized slice register — register not found (not "
                    f"silently treating as single-step)")
            return {"text": spine_text, "recognized": False, "pointer_ref": ref,
                    "pointer_broken": True, "warning": warn, "source": "pointer"}
    except OSError:
        pass
    warn = (f"slice_register_ref points to {ref!r} but it does not resolve — "
            f"register not found (not silently treating as single-step)")
    return {"text": raw, "recognized": False, "pointer_ref": ref,
            "pointer_broken": True, "warning": warn, "source": "pointer"}


def cmd_translate(payload):
    agent_choice = payload.get("agent_choice")
    try:
        family = agent_choice_to_model_family(agent_choice)
    except ValueError as e:
        _emit({"status": "ERROR", "error": str(e)}, 3)
    _emit({"status": "OK", "agent_choice": agent_choice, "model_family": family}, 0)


def cmd_plan_slices(payload):
    text = resolve_register_source(payload)["text"]
    slices = parse_slice_register(text)
    out = []
    for s in slices:
        try:
            family = agent_choice_to_model_family(s.agent_choice)
        except ValueError as e:
            _emit({"status": "ERROR",
                   "error": f"slice {s.id}: {e}"}, 3)
        out.append({
            "id": s.id, "name": s.name, "type": s.type,
            "agent_choice": s.agent_choice, "model_family": family,
            "depends_on": list(s.depends_on),
        })
    _emit({"status": "OK", "count": len(out), "slices": out}, 0)


def cmd_dry_run(payload):
    text = resolve_register_source(payload)["text"]
    slices = parse_slice_register(text)
    bookkeeping = InMemoryBookkeepingAdapter()
    spawn = FakeSpawnAdapter()
    try:
        result = run_dispatch_loop(slices, bookkeeping, spawn)
    except DeadlockError as e:
        _emit({"status": "DEADLOCK", "error": str(e)}, 2)
    except ValueError as e:  # unknown agent_choice
        _emit({"status": "ERROR", "error": str(e)}, 3)
    _emit({
        "status": "OK",
        "dispatched": result["dispatched"],
        "summary": result["summary"],
        "spawn_calls": spawn.calls,
        "bookkeeping": bookkeeping.all(),
    }, 0)


def cmd_verify_model(payload):
    """S5 real model-pin reader entry point: resolve the EXPECTED family (from
    `expected_family` or `agent_choice`), read the spawn's USED family from the
    harness transcript via the real TranscriptModelPinAdapter, and compare.
    Exit 0 = match; 4 = mismatch OR unverifiable transcript (fail-closed); 3 =
    usage. This is the concrete command the SKILL.md real-to-real path invokes
    after a real spawn."""
    expected = payload.get("expected_family")
    if expected is None:
        agent_choice = payload.get("agent_choice")
        if agent_choice is None:
            _emit({"status": "ERROR",
                   "error": "provide expected_family or agent_choice"}, 3)
        try:
            expected = agent_choice_to_model_family(agent_choice)
        except ValueError as e:
            _emit({"status": "ERROR", "error": str(e)}, 3)
    port = TranscriptModelPinAdapter(projects_root=payload.get("projects_root"))
    resolved = port.resolve_used_family(
        session_id=payload.get("session_id"),
        agent_id=payload.get("agent_id"),
        transcript_path=payload.get("transcript_path"),
    )
    if not resolved.get("ok"):
        _emit({"status": "UNVERIFIABLE", "expected": expected,
               "error": resolved.get("error"),
               "transcript_path": resolved.get("transcript_path")}, 4)
    used = resolved.get("family")
    if used != expected:
        _emit({"status": "MISMATCH", "expected": expected, "used": used,
               "raw_models": resolved.get("raw_models"),
               "transcript_path": resolved.get("transcript_path")}, 4)
    _emit({"status": "OK", "expected": expected, "used": used, "match": True,
           "transcript_path": resolved.get("transcript_path")}, 0)


def cmd_check_code(payload):
    """S6 code-layer verify_write entry point: run each spec in `verify_specs`
    through `_verify_write` and report.

    S2 [V3-A]: when BOTH `run_id` and `slice_id` are supplied AND every spec passes,
    persist the CODE-layer receipt (`<slice_id>.code.json`) the walk gate reads as the
    commit precondition. A receipt is written ONLY on an all-pass run (a FAIL exits
    before the write), and ONLY when keyed — omitting run_id/slice_id preserves the
    legacy behavior exactly (no receipt, no signature change).
    Exit 0 = all pass; 5 = any WriteVerificationError; 3 = usage."""
    verify_specs = payload.get("verify_specs")
    if not verify_specs:
        _emit({"status": "ERROR",
               "error": "provide verify_specs (non-empty list of {surface_kind, path,"
                        " expected_payload, locator})"}, 3)
    results = []
    for i, spec in enumerate(verify_specs):
        locator = spec.get("locator")
        if isinstance(locator, list):
            locator = tuple(locator)
        try:
            ok = _verify_write(
                spec.get("surface_kind", ""),
                spec.get("path", ""),
                spec.get("expected_payload"),
                locator,
            )
            results.append({"index": i, "status": "OK", "detail": ok})
        except WriteVerificationError as e:
            _emit({"status": "FAIL", "index": i, "error": str(e),
                   "results": results}, 5)
        except ValueError as e:
            _emit({"status": "ERROR", "index": i, "error": str(e)}, 3)
    out = {"status": "OK", "results": results}
    run_id = payload.get("run_id")
    slice_id = payload.get("slice_id")
    if run_id and slice_id:                         # keyed → persist the code receipt
        receipt_path = write_code_receipt(
            run_id, slice_id, detail={"specs": len(verify_specs)})
        out["receipt_path"] = str(receipt_path)
    _emit(out, 0)


def cmd_check_conformance(payload):
    """DEPRECATED / FENCED [C4] — the legacy inline-verdict path.

    This verb VALIDATES an inline `{"verdict": ...}` for the pre-existing in-loop
    conformance seam ONLY; it is FENCED OUT of the S2 receipt store — it NEVER writes a
    `<slice_id>.conformance.json` receipt, so it can no longer be used to FORGE a
    slice's recorded conformance verdict. The receipt the walk gate reads is produced
    EXCLUSIVELY by `record-conformance`, which aggregates the ISOLATED /double-check
    checker outputs in code and REFUSES an inline verdict [C1]. A future caller cannot
    reintroduce the honor-system hole through this path: the S4 walk gate consults only
    `record-conformance` receipts, never this verb's output. The additive
    `deprecated`/`fenced` fields on the response advertise the fence to any caller.
    Exit 0 = PASS (ok); 6 = non-PASS; 3 = usage (neither verdict nor verdict_path)."""
    if "verdict" not in payload and "verdict_path" not in payload:
        _emit({"status": "ERROR",
               "error": "provide verdict or verdict_path"}, 3)
    port = DoubleCheckConformanceAdapter()
    resolved = port.check_conformance(
        slice_id=None,
        verdict=payload.get("verdict"),
        verdict_path=payload.get("verdict_path"),
    )
    # [C4] fence marker — no receipt is written here; record-conformance owns receipts.
    _fence = {"deprecated": True,
              "fenced": "[C4] no receipt written; use record-conformance for the "
                        "attested conformance receipt"}
    if resolved.get("ok"):
        _emit({"status": "OK", "verdict": resolved.get("verdict"),
               "source": resolved.get("source"), **_fence}, 0)
    else:
        _emit({"status": "NON_PASS", "verdict": resolved.get("verdict"),
               "error": resolved.get("error"),
               "source": resolved.get("source"), **_fence}, 6)


def cmd_commit_slice(payload):
    """S7 real commit entry point: create the slice_id-prefixed git commit via
    GitCommitAdapter (governed by the unified git contract). This is the concrete
    command the SKILL.md real-to-real path invokes to land a slice's commit.
    Exit 0 = committed (receipt with subject/branch/sha); 8 = GitContractError
    (bad id/summary, protected branch, or git failure); 3 = usage."""
    slice_id = payload.get("slice_id")
    if not slice_id:
        _emit({"status": "ERROR", "error": "provide slice_id"}, 3)
    # Declared scope: explicit `paths`, else the slice's register Write targets
    # (from `spine_path` / `register_markdown`). Neither -> usage error; there is
    # no whole-tree fallback (glittery-humming-pine A7).
    paths = payload.get("paths")
    if paths is not None and not isinstance(paths, list):
        _emit({"status": "ERROR", "error": "paths must be a list"}, 3)
    if paths is None:
        if (payload.get("spine_path") is None
                and payload.get("register_markdown") is None):
            _emit({"status": "ERROR",
                   "error": "provide paths[] or spine_path/register_markdown so "
                            "the commit is scoped to the slice's write targets"}, 3)
        text = resolve_register_source(payload)["text"]
        match = [s for s in parse_slice_register(text) if s.id == slice_id]
        if not match:
            _emit({"status": "ERROR",
                   "error": f"slice {slice_id} not found in the register"}, 3)
        paths = list(match[0].write_targets)
    adapter = GitCommitAdapter(repo_dir=payload.get("repo_dir"))
    summary = payload.get("commit_summary")
    result = {"commit_summary": summary} if summary else {}
    try:
        receipt = adapter.create_commit(slice_id, result=result, paths=paths)
    except GitContractError as e:
        _emit({"status": "CONTRACT_ERROR", "error": str(e),
               "slice_id": slice_id}, 8)
    # Spread the receipt first so the CLI envelope `status: OK` wins over the
    # receipt's own `status: committed` (consistent OK envelope across commands).
    _emit({**receipt, "status": "OK"}, 0)


def cmd_commit_detour(payload):
    """NS6 path-scoped /plan-detour commit entry point: create the P<N>:-prefixed
    git commit over ONLY the given paths via GitCommitAdapter.create_detour_commit
    (same unified git contract). {commit_id, paths: [...], commit_summary,
    repo_dir?}. Exit 0 = committed; 8 = GitContractError; 3 = usage."""
    commit_id = payload.get("commit_id")
    paths = payload.get("paths")
    summary = payload.get("commit_summary")
    if not commit_id or not isinstance(paths, list) or not paths or not summary:
        _emit({"status": "ERROR",
               "error": "provide commit_id, non-empty paths[], and commit_summary"}, 3)
    adapter = GitCommitAdapter(repo_dir=payload.get("repo_dir"))
    try:
        receipt = adapter.create_detour_commit(
            commit_id, paths=paths, summary=summary)
    except GitContractError as e:
        _emit({"status": "CONTRACT_ERROR", "error": str(e),
               "commit_id": commit_id}, 8)
    _emit({**receipt, "status": "OK"}, 0)


def cmd_push_gate(payload):
    """S7 end-of-plan push gate entry point: evaluate the unified contract's push
    gate (A14). Evaluation ONLY — it never pushes (the real `git push` is the
    SKILL.md operator-confirmed path). Exit 0 = should_push (all conditions met);
    7 = withheld (any unmet condition or an abort); 3 = usage."""
    required = ("all_slices_done", "work_done_succeeded", "git_status_clean")
    if not all(k in payload for k in required):
        _emit({"status": "ERROR",
               "error": f"provide all of {list(required)} (booleans); "
                        "optional aborted (bool)"}, 3)
    decision = UnifiedGitContract.evaluate_push_gate(
        all_slices_done=bool(payload.get("all_slices_done")),
        work_done_succeeded=bool(payload.get("work_done_succeeded")),
        git_status_clean=bool(payload.get("git_status_clean")),
        aborted=bool(payload.get("aborted", False)),
    )
    out = {"status": "OK" if decision.should_push else "WITHHELD",
           "should_push": decision.should_push, "reason": decision.reason,
           "partial_prompt": decision.partial_prompt}
    _emit(out, 0 if decision.should_push else 7)


def cmd_mode(payload):
    """S8: validate/normalize a run-wide execution mode. 0 ok / 3 unknown."""
    try:
        m = normalize_mode(payload.get("mode"))
    except ValueError as e:
        _emit({"status": "ERROR", "error": str(e)}, 3)
    _emit({"status": "OK", "mode": m, "default": DEFAULT_MODE}, 0)


def cmd_confirm_gate(payload):
    """S8: AI-promotes-only confirm decision for one slice + the confirm prompt
    payload when it needs confirmation. 0 ok / 3 usage-or-unknown-mode."""
    sid = payload.get("id")
    if not sid:
        _emit({"status": "ERROR", "error": "provide slice id"}, 3)
    try:
        mode_n = normalize_mode(payload.get("mode"))
        s = Slice(
            id=sid, name=payload.get("name", ""), type=payload.get("type", ""),
            agent_choice=payload.get("agent_choice", "routine"),
            depends_on=tuple(payload.get("depends_on", ()) or ()),
            confirm_override=bool(payload.get("confirm_override", False)),
        )
        layer1 = payload.get("layer1", True)
        needs = slice_needs_confirm(s, mode=mode_n, layer1=layer1)
        prompt = render_confirm_prompt(s, mode=mode_n) if needs else None
    except ValueError as e:
        _emit({"status": "ERROR", "error": str(e)}, 3)
    _emit({"status": "OK", "needs_confirm": needs, "mode": mode_n,
           "prompt": prompt}, 0)


def cmd_escalation_surface(payload):
    """S8: render the escalation surface (in-band always + Auto-only opt-in notify)
    from the PERSISTED retry count. 0 ok / 3 usage."""
    sid = payload.get("slice_id")
    if not sid:
        _emit({"status": "ERROR", "error": "provide slice_id"}, 3)
    if "attempts" not in payload:
        _emit({"status": "ERROR",
               "error": "provide attempts (the persisted retry count)"}, 3)
    try:
        surface = render_escalation_surface(
            sid, payload.get("attempts"), payload.get("dispatched"),
            mode=payload.get("mode"),
            notify_recipient=payload.get("notify_recipient"))
    except ValueError as e:
        _emit({"status": "ERROR", "error": str(e)}, 3)
    _emit({"status": "OK", **surface}, 0)


def cmd_session_summary(payload):
    """S8: render the slice_execution map summary (U2 Investigate / U6 re-entry)
    from an inline map or a state_path file. 0 ok / 3 usage."""
    if payload.get("slice_execution") is not None:
        execmap = payload["slice_execution"]
    elif payload.get("state_path"):
        # A non-JSON / unreadable state_path (e.g. a Markdown "spine" surface a
        # caller mislabels as a state map) must not crash session-summary: treat
        # it as absent (renders an empty summary). _read_json_or_none itself is
        # left unchanged — it is used broadly to read real JSON state where a
        # decode error should surface genuine corruption, not be swallowed.
        try:
            doc = _read_json_or_none(os.path.expanduser(payload["state_path"]))
        except (json.JSONDecodeError, OSError):
            doc = None
        if doc is None:
            _emit({"status": "OK", **render_session_summary({})}, 0)
        execmap = (doc.get("slice_execution")
                   if isinstance(doc, dict) and "slice_execution" in doc else doc)
    else:
        _emit({"status": "ERROR",
               "error": "provide slice_execution or state_path"}, 3)
    if not isinstance(execmap, dict):
        _emit({"status": "ERROR",
               "error": "slice_execution must be a JSON object"}, 3)
    _emit({"status": "OK", **render_session_summary(execmap)}, 0)


def cmd_set_active_run(payload):
    """A2: write the per-run pointer (`run-<run_id>.json`) the resume-scoped gate
    reads. run_id = compute_run_id(surface_path [+ worktree_root]) so concurrent
    runs never clobber (last-writer-wins is gone). 0 ok / 3 usage."""
    owner = payload.get("owner_session_id")
    surface = payload.get("surface_path")
    if not owner or not surface:
        _emit({"status": "ERROR",
               "error": "provide owner_session_id and surface_path"}, 3)
    base = _execplan_ack_dir()
    migrate_legacy_pointer(base)                       # transitional (A2)
    worktree_root = (payload.get("worktree_root") or "").strip() or None
    run_id = compute_run_id(surface, worktree_root)
    pointer = _run_pointer_path(base, run_id)
    now_iso = _now_iso()
    data = {"run_id": run_id, "owner_session_id": owner, "surface_path": surface,
            "surface_kind": payload.get("surface_kind", "full"),
            "total_slices": payload.get("total_slices"),
            "worktree_root": worktree_root}
    # state_path (the JSON slice_execution surface) powers the A3 completion
    # self-heal — distinct from surface_path (the Markdown spine used for display).
    if payload.get("state_path"):
        data["state_path"] = payload["state_path"]
    # The run's created_at tracks the RUN (age/staleness), so it survives an owner
    # change (a fresh session resuming). Session-scoped flags (one-plan-per-session
    # + last-composed-type) are preserved ONLY within the same owner; a genuinely
    # fresh owner starts with a fresh plan budget.
    existing = _read_pointer_doc(pointer)
    if existing:
        data["created_at"] = existing.get("created_at", now_iso)
        if existing.get("owner_session_id") == owner:
            for k in ("plan_session_exhausted", "composed_handoff_type"):
                if k in existing:
                    data[k] = existing[k]
    else:
        data["created_at"] = now_iso
    data["updated_at"] = now_iso
    # S1 [C5] — walk-state lifecycle fields with preserve-on-omit / reset-on-null.
    # A key PRESENT in the payload is authoritative (a value OR an explicit null that
    # RESETS on stop/completion); a key OMITTED preserves the existing pointer value
    # (a per-slice re-arm), else the default. `current_slice_id` is only ever
    # PRESERVED or NULL-RESET here — a SLICE VALUE is assigned exclusively by
    # checkout-slice [C4]. A pre-migration pointer is backed up before the new fields
    # land on it (reversible; A1 guard rail).
    if existing and _pointer_lacks_new_fields(existing):
        backup_legacy_pointer(pointer)
    for fkey, fdefault in (("execution_pending", False),
                           ("walking_session_id", None),
                           ("current_slice_id", None)):
        if fkey in payload:
            data[fkey] = payload[fkey]              # explicit: value or null-reset
        elif existing and fkey in existing:
            data[fkey] = existing.get(fkey)         # preserve across a re-arm
        else:
            data[fkey] = fdefault
    # NS1 — optionally arm a typed cross-session handoff onto the pointer. Only
    # added when provided, so the legacy 4-field pointer shape is unchanged for
    # the U2 arm hook + the existing set/clear tests. Round-tripped through
    # HandoffRecord so a malformed record is rejected BEFORE it is persisted.
    ph = payload.get("pending_handoff")
    if ph is not None:
        try:
            rec = HandoffRecord.from_dict(ph).to_dict()
        except ValueError as e:
            _emit({"status": "ERROR", "error": f"bad pending_handoff: {e}"}, 3)
        # A4(b): store the slice's write targets ALREADY RESOLVED against the same
        # root the gate uses for containment, so the entry gate's intersection never
        # depends on the evaluating process's cwd.
        wt_list = rec.get("write_targets")
        if wt_list:
            rec["write_targets"] = resolve_write_targets(wt_list, worktree_root)
        data["pending_handoff"] = rec
    _atomic_write_json(pointer, data)
    _emit({"status": "OK", "pointer": str(pointer), **data}, 0)


def cmd_compute_handoff(payload):
    """NS1: compute the typed HandoffRecord for the next ready slice from a
    register ({register_markdown}|{spine_path}) + an optional slice_execution map
    (only the `completed` statuses gate readiness). continue-the-run arm only — a
    plan-needing slice raises -> NS2. 0 ok (handoff dict or null) / 3 usage or
    not-yet-implemented arm."""
    text = _load_register_text(payload)
    slices = parse_slice_register(text)
    execmap = payload.get("slice_execution") or {}
    if not isinstance(execmap, dict):
        _emit({"status": "ERROR",
               "error": "slice_execution must be a JSON object"}, 3)
    bookkeeping = InMemoryBookkeepingAdapter()
    for sid, entry in execmap.items():
        if isinstance(entry, dict) and entry.get("status") == "completed":
            bookkeeping.mark_completed(sid, result=entry.get("result"))
    # The plan-this-slice -> implement-this-slice flip is driven by whether the
    # slice's _PLAN exists. Two caller forms:
    #   planned_slice_ids: explicit list of slices whose _PLAN exists (NS2 form);
    #   slice_plan_paths:  {slice_id: plan_path} — NS4 derives existence from disk
    #                      via plan_file_exists AND threads the path onto the record.
    planned = set(payload.get("planned_slice_ids") or [])
    plan_paths = payload.get("slice_plan_paths") or {}
    if not isinstance(plan_paths, dict):
        _emit({"status": "ERROR",
               "error": "slice_plan_paths must be a JSON object"}, 3)

    def _plan_exists(sid):
        if sid in planned:
            return True
        return plan_file_exists(plan_paths.get(sid))

    try:
        handoff = compute_handoff(
            slices, bookkeeping,
            plan_exists=_plan_exists,
            plan_path_for=(lambda sid: plan_paths.get(sid)))
    except ValueError as e:
        _emit({"status": "ERROR", "error": str(e)}, 3)
    _emit({"status": "OK",
           "handoff": handoff.to_dict() if handoff else None}, 0)


def cmd_mark_plan_exhausted(payload):
    """NS4: set the one-plan-per-session flag on the run pointer. 0 ok."""
    _emit({"status": "OK",
           **mark_plan_session_exhausted(payload.get("pointer_dir"),
                                         payload.get("run_id"))}, 0)


def cmd_verify_plan_filed(payload):
    """NS4: the plan-filing gate — re-read the spine's # Implementation Details
    wikilink + the register's L:slice plan= field. 0 ok / 3 usage / 9 gate FAIL."""
    spine = payload.get("spine_path")
    sid = payload.get("slice_id")
    basename = payload.get("plan_basename")
    if not (spine and sid and basename):
        _emit({"status": "ERROR",
               "error": "provide spine_path, slice_id, plan_basename"}, 3)
    try:
        result = verify_plan_filed(os.path.expanduser(spine), sid, basename)
    except WriteVerificationError as e:
        _emit({"status": "FAIL", "error": str(e)}, 9)
    _emit({"status": "OK", **result}, 0)


def cmd_plan_detour_return(payload):
    """NS4: the plan-detour return descriptor (composes with post-plan-uxgate).
    0 ok / 3 usage."""
    sid = payload.get("slice_id")
    basename = payload.get("plan_basename")
    if not (sid and basename):
        _emit({"status": "ERROR",
               "error": "provide slice_id and plan_basename"}, 3)
    _emit({"status": "OK", **render_plan_detour_return(sid, basename)}, 0)


def cmd_reconcile_code_face(payload):
    """NS5: fresh code-face reconciliation at an attended/out-of-session resume.
    Re-reads the actual shipped code vs register claims and surfaces drift. Body:
    {claimed|slice_execution, specs_by_slice?, verdicts_by_slice?}. Inline
    verdicts are normalized through the DoubleCheckConformanceAdapter. Always
    exit 0 on valid input (the `drift` flag lives in the body); 3 on usage."""
    claimed = payload.get("claimed")
    if claimed is None:
        claimed = payload.get("slice_execution")
    if not isinstance(claimed, dict):
        _emit({"status": "ERROR",
               "error": "provide claimed (or slice_execution) as a JSON object"}, 3)
    specs = payload.get("specs_by_slice") or {}
    verdicts = payload.get("verdicts_by_slice") or {}
    if not isinstance(specs, dict) or not isinstance(verdicts, dict):
        _emit({"status": "ERROR",
               "error": "specs_by_slice and verdicts_by_slice must be objects"}, 3)
    conformance = DoubleCheckConformanceAdapter() if verdicts else None
    report = reconcile_code_face(
        claimed, specs_by_slice=specs,
        conformance=conformance, verdicts_by_slice=verdicts)
    _emit({"status": "OK", **report}, 0)


def cmd_record_composed_type(payload):
    """NS7: record the handoff type of the last composed resume prompt. 0 ok /
    3 usage."""
    htype = payload.get("composed_handoff_type")
    if not htype:
        _emit({"status": "ERROR", "error": "provide composed_handoff_type"}, 3)
    _emit({"status": "OK",
           **record_composed_handoff_type(htype, payload.get("pointer_dir"),
                                          payload.get("run_id"))}, 0)


def cmd_recompose_check(payload):
    """NS7: the type-change recompose trigger. Reads the last composed handoff
    type from active-run.json and compares to current_type, forcing a recompose
    on a type change the type-blind pfh fingerprint misses. {current_type,
    pointer_dir?, pfh_stale?}. 0 ok / 3 usage."""
    current = payload.get("current_type")
    if not current:
        _emit({"status": "ERROR", "error": "provide current_type"}, 3)
    prev = read_composed_handoff_type(payload.get("pointer_dir"),
                                      payload.get("run_id"))
    recompose, reason = should_recompose_handoff_prompt(
        current, prev, pfh_stale=bool(payload.get("pfh_stale")))
    _emit({"status": "OK", "recompose": recompose, "reason": reason,
           "current_type": current, "prev_type": prev}, 0)


def cmd_deferred_captures(payload):
    """NS7: enumerate ALL deferred-this-run captures (origin-slice-id) + an
    optional completeness check against the run's actual deferred set. Accepts
    {dispatched: [...]} OR a slice_execution map (inline or state_path); optional
    {actual_deferred: [...]}. 0 ok / 3 usage."""
    if payload.get("dispatched") is not None:
        src = payload["dispatched"]
        if not isinstance(src, list):
            _emit({"status": "ERROR", "error": "dispatched must be a JSON array"}, 3)
    else:
        src = _resolve_slice_execution(payload)
    enumerated = enumerate_deferred_captures(src)
    out = {"status": "OK", "count": len(enumerated), "captures": enumerated}
    if payload.get("actual_deferred") is not None:
        out["completeness"] = check_deferred_completeness(
            enumerated, payload["actual_deferred"])
    _emit(out, 0)


def cmd_resume_context(payload):
    """NS7: assemble the composer input bundle (HandoffRecord summary + slice-
    register status + deferred captures) the reused /prompt-for-handoff composer
    receives. Reads pending_handoff from the pointer; {session_summary?,
    deferred_captures?, pointer_dir?}. 0 ok / 3 malformed record."""
    try:
        handoff = read_pending_handoff(payload.get("pointer_dir"),
                                       payload.get("run_id"))
    except ValueError as e:
        _emit({"status": "ERROR", "error": f"malformed pending_handoff: {e}"}, 3)
    ctx = render_resume_context(
        handoff,
        session_summary=payload.get("session_summary"),
        deferred_captures=payload.get("deferred_captures"))
    _emit({"status": "OK", **ctx}, 0)


def cmd_resume(payload):
    """NS1/NS3: the fresh-session cross-session resume read + routing. Reads the
    HandoffRecord armed on active-run.json's pending_handoff
    (EXECPLAN_ACK_STATE_DIR-isolated or an explicit {pointer_dir}) and renders the
    SKILL-side routing decision (dispatch arm / plan-detour). Optional
    `has_verification_gate` drives the gate-exists-at-dispatch promotion. An
    out-of-session route carries the copy-paste block scaffold. 0 ok / 3
    malformed record."""
    pointer_dir = payload.get("pointer_dir")
    run_id = payload.get("run_id")
    try:
        handoff = read_pending_handoff(pointer_dir, run_id)
    except ValueError as e:
        _emit({"status": "ERROR", "error": f"malformed pending_handoff: {e}"}, 3)
    decision = render_resume(
        handoff,
        has_verification_gate=payload.get("has_verification_gate"),
        plan_session_exhausted=is_plan_session_exhausted(pointer_dir, run_id))
    if decision.get("next") == "dispatch-out-of-session":
        decision["out_of_session_block"] = render_out_of_session_block(handoff)
    _emit({"status": "OK", **decision}, 0)


def cmd_execplan_gate_check(payload):
    """A1: the resume-scoped checkpoint decision — called by the PreToolUse gate on
    a /execute-plan invocation. {session_id, worktree_root?, now_epoch?}. Always
    exit 0; the decision (`block`) rides the body so the hook stays a thin adapter.
    On block, the `pending-<sid>` marker is written and `message` carries the
    informative prompt (A7)."""
    session_id = payload.get("session_id")
    if not session_id:
        _emit({"status": "ERROR", "error": "provide session_id"}, 3)
    decision = gate_check(
        session_id,
        session_worktree=(payload.get("worktree_root") or "").strip() or None,
        now=payload.get("now_epoch"))
    _emit({"status": "OK", **decision}, 0)


def cmd_execplan_ack(payload):
    """A1: record that this session acknowledged its pending run — called by the
    PostToolUse AskUserQuestion hook. {session_id}. 0 ok."""
    session_id = payload.get("session_id")
    if not session_id:
        _emit({"status": "ERROR", "error": "provide session_id"}, 3)
    _emit({"status": "OK", **record_ack(session_id)}, 0)


def cmd_execplan_reap(payload):
    """A3/A5: reap run pointers that are provably complete OR older than the TTL —
    called by the SessionStart GC hook. Optional {now_epoch}. 0 ok."""
    reaped = reap_stale_runs(now=payload.get("now_epoch"))
    _emit({"status": "OK", "reaped": reaped, "count": len(reaped)}, 0)


def cmd_clear_active_run(payload):
    """A4: retire ONLY the named run's pointer at end-of-plan / abort (owner/run-
    scoped — never a cross-run wipe). Target: an explicit {run_id}, else the run_id
    derived from {surface_path [+ worktree_root]}.

    Owner guard (execplan-gate-blast-radius A2, gap G5). The guard used to fire
    only when the caller VOLUNTEERED `owner_session_id` — and the gate's own printed
    remediation omitted it — so any session could disarm any other session's
    in-flight run in one command. The guard is now UNCONDITIONAL: a clear proceeds
    only when the caller proves ownership (`owner_session_id` equals the pointer's
    recorded owner) or explicitly overrides with `confirm_non_owner: true`. Omitting
    the owner is no longer a way past the guard — it IS the unproven case.

    The override is deliberately kept: an owner session that has ended cannot come
    back to clear its own pointer, so a human must be able to retire it. It is
    explicit and named, never implied by silence.

    Retirement MOVES the pointer aside (`.bak-<epoch>-<pid>`), never unlinks it, so
    a clear is reversible (`safe-defaults.md`). `cleared: true` means the pointer no
    longer gates; `backup` names where it went.

    Legacy fallback (no run_id/surface): retire the single `active-run.json` — also
    moved aside, not unlinked. 0 ok."""
    base = _execplan_ack_dir()
    run_id = payload.get("run_id")
    surface = payload.get("surface_path")
    owner = payload.get("owner_session_id")
    confirm = bool(payload.get("confirm_non_owner"))
    if not run_id and surface:
        run_id = compute_run_id(surface, payload.get("worktree_root"))
    if run_id:
        migrate_legacy_pointer(base)                   # transitional
        pointer = _run_pointer_path(base, run_id)
        doc = _read_pointer_doc(pointer)
        # The guard keys on the pointer EXISTING, not on its doc parsing.
        # `_read_pointer_doc` returns None on a corrupt/unreadable file as well as an
        # absent one; guarding on `doc` would let a pointer that exists but does not
        # parse fall straight through to the retire call with no ownership check —
        # the very hole A2 exists to close, reopened for exactly the pointers whose
        # state is least trustworthy. An unreadable pointer is the UNPROVEN case, so
        # it fails closed. A genuinely absent pointer needs no guard: there is
        # nothing to protect and the retire below is a reported no-op.
        if pointer.exists():
            recorded = doc.get("owner_session_id") if doc else None
            is_owner = bool(owner) and bool(recorded) and owner == recorded
            if not is_owner and not confirm:
                if doc is None:
                    reason = ("pointer unreadable — ownership cannot be verified; "
                              "run not cleared")
                elif owner:
                    reason = "owner mismatch — run not cleared"
                else:
                    reason = "no owner_session_id supplied — run not cleared"
                _emit({"status": "OK", "cleared": False, "run_id": run_id,
                       "reason": reason,
                       "owner_session_id": recorded,
                       "remediation": ("re-run with the owning session's "
                                       "owner_session_id, or with "
                                       "confirm_non_owner: true to override"),
                       "pointer": str(pointer)}, 0)
        moved, backup = _move_pointer_aside(pointer)
        out = {"status": "OK", "cleared": moved, "run_id": run_id,
               "pointer": str(pointer)}
        if backup:
            out["backup"] = backup
        if moved and not (bool(owner) and doc and owner == doc.get("owner_session_id")):
            out["non_owner_override"] = True
        _emit(out, 0)
    # Legacy fallback: no run identity supplied → retire the lone active-run.json.
    # This branch is guarded on the SAME rule as the run-scoped one above. With no
    # run identity there is no pointer doc to read an owner from, so ownership can
    # never be proven here — which makes this the unproven case by construction, not
    # an exemption from it. Leaving it ungated would have kept a one-command,
    # no-argument way to retire a pointer belonging to another session: the exact
    # hole A2 closes, one branch over.
    legacy = _legacy_pointer_path(base)
    if legacy.exists() and not confirm:
        _emit({"status": "OK", "cleared": False, "pointer": str(legacy),
               "reason": ("no run identity supplied — ownership of the legacy "
                          "pointer cannot be verified; run not cleared"),
               "remediation": ("re-run with run_id (or surface_path) to target a "
                               "specific run, or with confirm_non_owner: true to "
                               "override")}, 0)
    moved, backup = _move_pointer_aside(legacy)
    out = {"status": "OK", "cleared": moved, "pointer": str(legacy)}
    if backup:
        out["backup"] = backup
    if moved:
        out["non_owner_override"] = True
    _emit(out, 0)


def _resolve_slice_execution(payload):
    """S9: resolve a slice_execution map from {slice_execution} inline OR
    {state_path} (a topic-state JSON with a slice_execution key, OR a bare
    run-state map). Mirrors cmd_session_summary. A missing state_path file reads
    as an empty map ({}); _emits a usage error (exit 3) when neither key is given
    or the resolved value is not a dict."""
    if payload.get("slice_execution") is not None:
        execmap = payload["slice_execution"]
    elif payload.get("state_path"):
        doc = _read_json_or_none(os.path.expanduser(payload["state_path"]))
        if doc is None:
            return {}
        execmap = (doc.get("slice_execution")
                   if isinstance(doc, dict) and "slice_execution" in doc else doc)
    else:
        _emit({"status": "ERROR",
               "error": "provide slice_execution or state_path"}, 3)
    if not isinstance(execmap, dict):
        _emit({"status": "ERROR",
               "error": "slice_execution must be a JSON object"}, 3)
    return execmap


def cmd_report(payload):
    """S9 A16: per-slice implementation report (rows + markdown table). 0 ok /
    3 usage."""
    execmap = _resolve_slice_execution(payload)
    rows = render_implementation_report(execmap)
    _emit({"status": "OK", "count": len(rows), "rows": rows,
           "markdown": render_implementation_report_md(rows)}, 0)


def cmd_diagnostics(payload):
    """S9 A17: diagnostic correlations — CAPTURED, NOT gated. Always exit 0 on a
    valid map (3 only on missing/malformed input)."""
    execmap = _resolve_slice_execution(payload)
    _emit({"status": "OK",
           **render_diagnostics(execmap, restarts=payload.get("restarts"))}, 0)


def cmd_omtm(payload):
    """NS8 OMTM: hands-off minutes per plain implementation slice (plan-needing +
    attended slices excluded). CAPTURED, NOT gated — always exit 0 on a valid map
    (3 on missing/malformed input)."""
    execmap = _resolve_slice_execution(payload)
    _emit({"status": "OK", **compute_omtm(execmap)}, 0)


def cmd_exception_surface(payload):
    """NS9: render the exception-intervention surface (structured block). Body:
    {exception_type, slice_id, last_committed?, dispatched?, default_option?,
    options?, detail?}. 0 ok / 3 usage."""
    etype = payload.get("exception_type")
    sid = payload.get("slice_id")
    if not etype or not sid:
        _emit({"status": "ERROR",
               "error": "provide exception_type and slice_id"}, 3)
    surface = render_exception_surface(
        etype, sid,
        last_committed=payload.get("last_committed"),
        dispatched=payload.get("dispatched"),
        default_option=payload.get("default_option"),
        options=payload.get("options"),
        detail=payload.get("detail"))
    _emit({"status": "OK", **surface}, 0)


def cmd_engagement_rate(payload):
    """NS9: compute the exception-intervention engagement rate (retained v1
    secondary metric) from a list of recorded choices. Body: {records: [...]} —
    each row {chosen, default_option, engaged?}; or {choices} as raw
    {chosen, default_option} pairs to be scored. CAPTURED-NOT-GATED. 0 ok."""
    records = payload.get("records")
    if records is None and isinstance(payload.get("choices"), list):
        records = [record_exception_choice(c.get("chosen"), c.get("default_option"),
                                           engaged=c.get("engaged"))
                   for c in payload["choices"] if isinstance(c, dict)]
    _emit({"status": "OK", **exception_engagement_rate(records or [])}, 0)


def cmd_harvest_observations(payload):
    """S9 U5: collect verified observations into the /plan-followups-review input
    contract. Accepts {dispatched: [...]} inline, OR a slice_execution map
    (inline or state_path). 0 ok / 3 usage."""
    if payload.get("dispatched") is not None:
        src = payload["dispatched"]
        if not isinstance(src, list):
            _emit({"status": "ERROR",
                   "error": "dispatched must be a JSON array"}, 3)
    else:
        src = _resolve_slice_execution(payload)
    observations = collect_observations(src)
    _emit({"status": "OK", "count": len(observations),
           "observations": observations}, 0)


def cmd_register_presence(payload):
    """S1: route-presence predicate — {register_markdown|spine_path} → {present,
    non_closing_count, raw_non_closing_count, recognized}. present iff ≥2 non-closing
    VALID slices (C6). `raw_non_closing_count` is the count of candidate non-closing
    rows under a recognized register BEFORE the valid-slice floor — A2/A3 warn when
    raw_non_closing ≥ 2 BUT present is false (a genuinely malformed multi-step
    register that would otherwise downgrade silently). 0 ok / 3 usage."""
    src = resolve_register_source(payload)       # emits usage(3) if neither given
    detail = parse_slice_register_detail(src["text"])
    present = detail["valid_non_closing_count"] >= 2
    out = {"status": "OK", "present": present,
           "non_closing_count": detail["valid_non_closing_count"],
           "raw_non_closing_count": detail["raw_non_closing_count"],
           "recognized": detail["recognized"]}
    if src.get("pointer_broken"):
        out["pointer_broken"] = True
    if src.get("warning"):
        out["warning"] = src["warning"]
        print(src["warning"], file=sys.stderr)   # loud (facet 1)
    _emit(out, 0)


def cmd_become_walker(payload):
    """S1: CAS walker election [C3]. {run_id | surface_path[,worktree_root],
    session_id}. Decision rides the body (like execplan-gate-check); always exit 0
    — `rejected` distinguishes clobber-attempts. Fail-closed on any read problem."""
    base = _execplan_ack_dir()
    _emit({"status": "OK", **become_walker(payload, base)}, 0)


def cmd_checkout_slice(payload):
    """S1: atomic checkout of the next-ready slice [C4] — the EXCLUSIVE writer of the
    pointer's current_slice_id. {run_id | surface_path[,worktree_root], slice_id,
    state_path?}. Decision rides the body; always exit 0 (`rejected` on a non-ready
    slice / missing pointer). Deterministic — pointer + state JSON only."""
    base = _execplan_ack_dir()
    _emit({"status": "OK", **checkout_slice(payload, base)}, 0)


def cmd_execplan_entry_check(payload):
    """S1: entry-boundary predicate the S3 PreToolUse hook calls. {writing_session_id,
    worktree_root, target_path|target_paths} → {block, run_id?, slug?, slice_id?,
    reason?}. Own marker namespace (never execplan-gate-check's). Always exit 0.
    3 = usage.

    Backward-compatible superset (execplan-nonredirect-write-guard): accepts the new
    `target_paths` LIST (the multi-target Bash detector) OR the legacy scalar
    `target_path` — a partial hook/Python version skew never fails closed."""
    ws = payload.get("writing_session_id")
    target = payload.get("target_paths")
    if target is None:
        target = payload.get("target_path")
    if not ws or not target:
        _emit({"status": "ERROR",
               "error": "provide writing_session_id and target_path(s)"}, 3)
    base = _execplan_ack_dir()
    decision = execplan_entry_check(
        ws, payload.get("worktree_root"), target, base=base)
    _emit({"status": "OK", **decision}, 0)


def cmd_extract_bash_targets(payload):
    """Non-redirect write-target detector verb (execplan-nonredirect-write-guard).
    {command, cwd?} → the write DESTINATIONS (redirect + cp/mv/install/sed -i/tee/dd)
    written to stdout NUL-TERMINATED (a trailing NUL after every target INCLUDING the
    last, so a `read -r -d ''` / `mapfile -d ''` loop drops no element and needs no
    JSON parsing in Bash — Bash argv/vars cannot hold NUL, so the gates ingest this
    via process substitution, never `$()`). Empty result (read-only forms) → nothing
    on stdout. Fail-open: any error → nothing, exit 0."""
    try:
        res = extract_bash_write_targets(payload.get("command") or "",
                                         payload.get("cwd"))
        targets = res.get("targets", []) if isinstance(res, dict) else []
    except Exception:
        targets = []
    sys.stdout.write("".join(str(t) + "\0" for t in targets if t))
    sys.exit(0)


def cmd_execplan_entry_suppress(payload):
    """S1: append a path-scoped entry-gate suppression (own namespace) — the S3
    option-(b) writer. {session_id, target_path}. 0 ok / 3 usage."""
    sid = payload.get("session_id")
    target = payload.get("target_path")
    if not sid or not target:
        _emit({"status": "ERROR",
               "error": "provide session_id and target_path"}, 3)
    _emit({"status": "OK",
           **add_entry_suppression(_execplan_ack_dir(), sid, target)}, 0)


def cmd_migrate_run_pointer_fields(payload):
    """S1 (A1 guard rail): fold the walk-state fields onto any in-flight pointer that
    lacks them, backing each up first (reversible). Optional {pointer_dir}. 0 ok."""
    base = payload.get("pointer_dir") or _execplan_ack_dir()
    migrated = migrate_run_pointer_fields(base)
    _emit({"status": "OK", "migrated": migrated, "count": len(migrated)}, 0)


def cmd_restore_legacy_pointer(payload):
    """S1 (A1 guard rail): reversible-rollback path — restore a pointer's pre-migration
    shape from its `<pointer>.legacy-bak`. {run_id | surface_path[,worktree_root]}.
    0 ok (restored bool)."""
    base = _execplan_ack_dir()
    _run_id, pointer, _doc = _resolve_run_pointer(payload, base)
    if pointer is None:
        _emit({"status": "ERROR",
               "error": "provide run_id or surface_path"}, 3)
    restored = restore_legacy_pointer(pointer)
    _emit({"status": "OK", "restored": restored, "pointer": str(pointer)}, 0)


def cmd_record_conformance(payload):
    """S2: MODEL-layer conformance receipt writer [C1]. {run_id, slice_id,
    checkers_json[, round_num?, max_rounds?, is_final?]} → aggregates the captured
    ISOLATED /double-check outputs via aggregate_round_verdict (reused, never copied)
    and writes `<slice_id>.conformance.json`. REFUSES an inline `verdict` key — the
    verdict is only ever computed in code. Exit 0 on OK; 3 on refusal/usage (ERROR)."""
    res = record_conformance(payload)
    _emit(res, 0 if res.get("status") == "OK" else 3)


def cmd_walk_gate_check(payload):
    """S2: execution-boundary predicate the S4 walk gate calls. {writing_session_id,
    action, target_or_tag, run_id|surface_path[,worktree_root], register_markdown?|
    spine_path?, state_path?} → {block, reason?, current_slice_id?}. Decision rides the
    body; always exit 0. 3 = usage (missing writing_session_id or action)."""
    if not payload.get("writing_session_id") or not payload.get("action"):
        _emit({"status": "ERROR",
               "error": "provide writing_session_id and action"}, 3)
    _emit({"status": "OK", **walk_gate_check(payload)}, 0)


# --------------------------------------------------------------------------- #
# A3 (scan-unarmed) — SessionStart WARNING for a multi-step plan taken into
# implementation OFF the /plan Step-11 arming path (hand-authored, resumed fresh,
# backfilled). Arming (the `execution_pending` run pointer the entry gate reads)
# only ever happens at /plan Step 11, so such a plan would run unsafely AND
# SILENTLY. This verb re-detects "looks multi-step" from the plan's own register
# (following a `slice_register_ref` pointer, loud on a broken pointer — reuses
# resolve_register_source + parse_slice_register_detail, never a new reader),
# cross-checks the run-pointer dir for an arming pointer, and returns a WARNING
# (never a silent pass) for any unarmed-but-multistep or malformed-multistep plan.
# Advisory ONLY — the CLI always exits 0; the loop never gates. Fail-soft: any
# internal error yields no warnings (a scan failure must not block a session).
# --------------------------------------------------------------------------- #

# Terminal status tokens on a `**Status:**` line that mark a plan retired/done and
# so exempt from the scan (mirrors the retirement predicate in bookkeeping-model).
_RETIRED_STATUS_TOKENS = ("retired", "deprecated", "done", "✅")


def _plan_is_retired(text):
    """True iff the plan text marks itself retired/done (skip it). Cheap
    substring/regex check: a `bookkeeping: retired-` frontmatter line OR a
    `**Status:**` line (case-insensitively) carrying a terminal token."""
    if "bookkeeping: retired-" in text:
        return True
    for line in text.splitlines():
        low = line.lower()
        if "**status:**" in low and any(tok in low for tok in _RETIRED_STATUS_TOKENS):
            return True
    return False


def _glob_slug_plans(thoughts_dir, slug):
    """`<thoughts_dir>/<slug>*_PLAN.md` — catches the bare `<slug>-<ts>_PLAN.md`
    AND every scope-keyed `<slug>-<ts>_<SCOPE>_PLAN.md`. Never raises."""
    import glob as _glob
    try:
        return sorted(_glob.glob(os.path.join(str(thoughts_dir), f"{slug}*_PLAN.md")))
    except Exception:  # noqa: BLE001 — a glob failure = no plans, never a crash
        return []


def _resolve_bound_topic(session_id):
    """Import hooks/bound_topic (sibling ../../hooks) and resolve(session_id) ->
    {slug, thoughts_dir, project_root} or None. Wrapped so an unavailable module /
    resolution failure yields "no topic", NEVER a crash (per A3 spec)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        hooks_dir = os.path.join(here, "..", "..", "hooks")
        sys.path.insert(0, hooks_dir)
        import bound_topic  # noqa: WPS433 — deliberate late import behind try/except
        return bound_topic.resolve(session_id)
    except Exception:  # noqa: BLE001 — module missing / resolve raised -> cold
        return None


def _resolve_scan_plan_paths(payload):
    """Resolve the plan-file list by priority: explicit `plan_paths` (TEST path) >
    `thoughts_dir`+`slug` glob > `session_id` -> bound_topic -> glob. Returns a
    (possibly empty) list of plan paths; never raises."""
    plan_paths = payload.get("plan_paths")
    if isinstance(plan_paths, list):
        return [p for p in plan_paths if p]
    thoughts_dir = payload.get("thoughts_dir")
    slug = payload.get("slug")
    if thoughts_dir and slug:
        return _glob_slug_plans(thoughts_dir, slug)
    session_id = payload.get("session_id")
    if session_id:
        info = _resolve_bound_topic(session_id)
        if info and info.get("thoughts_dir") and info.get("slug"):
            return _glob_slug_plans(info["thoughts_dir"], info["slug"])
    return []


def _scan_pointer_armed_targets(pointer_dir):
    """The set of normalized abspaths some run-*.json ARMS (execution_pending true).
    Each doc is read with json.load inside try/except (unreadable docs skipped)."""
    import glob as _glob
    armed = set()
    try:
        docs = _glob.glob(os.path.join(str(pointer_dir), "run-*.json"))
    except Exception:  # noqa: BLE001
        return armed
    for docpath in docs:
        try:
            with open(docpath, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:  # noqa: BLE001 — unreadable/malformed pointer -> skip
            continue
        if not _pointer_execution_pending(doc):
            continue
        sp = doc.get("surface_path") if isinstance(doc, dict) else None
        if not isinstance(sp, str) or not sp:
            continue
        armed.add(os.path.abspath(os.path.expanduser(sp)))
    return armed


def _scan_one_plan(plan_file, armed_targets):
    """One plan file -> a warning dict {plan,kind,present,raw_non_closing,message}
    or None. Skips unreadable/missing (silent), retired/done, armed, and genuinely
    single-work (C6, no spurious warning) plans."""
    try:
        text = Path(plan_file).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None  # unreadable/missing -> skip silently
    if _plan_is_retired(text):
        return None
    # Detection via the shared pointer-following reader (loud on a broken pointer).
    src = resolve_register_source({"spine_path": plan_file})
    detail = parse_slice_register_detail(src["text"])
    present = detail["valid_non_closing_count"] >= 2
    raw_nc = detail["raw_non_closing_count"]
    pointer_broken = bool(src.get("pointer_broken"))
    norm_plan = os.path.abspath(os.path.expanduser(str(plan_file)))
    if norm_plan in armed_targets:
        return None  # armed -> no warning
    if present:
        msg = (f"{plan_file}: this multi-step plan is not armed for /execute-plan; "
               f"run /execute-plan (or /work-start) to arm it before taking it "
               f"into implementation.")
        return {"plan": plan_file, "kind": "unarmed-multistep",
                "present": present, "raw_non_closing": raw_nc, "message": msg}
    if raw_nc >= 2 or pointer_broken:
        msg = (f"{plan_file}: this plan LOOKS multi-step but its register did not "
               f"resolve to >=2 valid slices (malformed columns or a broken "
               f"slice_register_ref) — it is NOT being silently treated as a "
               f"one-shot; fix the register or drive it via /execute-plan.")
        return {"plan": plan_file, "kind": "malformed-multistep",
                "present": present, "raw_non_closing": raw_nc, "message": msg}
    return None  # single-work / no register -> no spurious warning (C6)


def scan_unarmed(payload):
    """Deterministic domain function: warn for every multi-step plan bound to the
    active topic that is NOT armed for /execute-plan. Returns
    {status:"OK", warnings:[...], count:N}. Fail-soft: any internal exception ->
    {status:"OK", warnings:[], count:0} (a scan failure never blocks a session)."""
    try:
        plan_paths = _resolve_scan_plan_paths(payload)
        pointer_dir = payload.get("pointer_dir") or _execplan_ack_dir()
        armed_targets = _scan_pointer_armed_targets(pointer_dir)
        warnings = []
        for plan_file in plan_paths:
            try:
                w = _scan_one_plan(plan_file, armed_targets)
            except (Exception, SystemExit):  # noqa: BLE001 — resolve_* may sys.exit
                continue
            if w is not None:
                warnings.append(w)
        return {"status": "OK", "warnings": warnings, "count": len(warnings)}
    except Exception:  # noqa: BLE001 — fail-soft (advisory only)
        return {"status": "OK", "warnings": [], "count": 0}


def cmd_scan_unarmed(payload):
    """A3 SessionStart WARNING verb — {plan_paths | thoughts_dir+slug | session_id}
    [+ pointer_dir override] -> warnings for unarmed/malformed multi-step plans.
    Advisory: ALWAYS exit 0 (never gates). Fail-soft on any internal error."""
    try:
        result = scan_unarmed(payload)
    except Exception:  # noqa: BLE001
        result = {"status": "OK", "warnings": [], "count": 0}
    _emit(result, 0)


DISPATCH = {
    "translate": cmd_translate,
    "plan-slices": cmd_plan_slices,
    "dry-run": cmd_dry_run,
    "verify-model": cmd_verify_model,
    "check-code": cmd_check_code,
    "check-conformance": cmd_check_conformance,
    "commit-slice": cmd_commit_slice,
    "push-gate": cmd_push_gate,
    "mode": cmd_mode,
    "confirm-gate": cmd_confirm_gate,
    "escalation-surface": cmd_escalation_surface,
    "session-summary": cmd_session_summary,
    "set-active-run": cmd_set_active_run,
    "clear-active-run": cmd_clear_active_run,
    "report": cmd_report,
    "diagnostics": cmd_diagnostics,
    "harvest-observations": cmd_harvest_observations,
    "compute-handoff": cmd_compute_handoff,
    "resume": cmd_resume,
    "mark-plan-exhausted": cmd_mark_plan_exhausted,
    "verify-plan-filed": cmd_verify_plan_filed,
    "plan-detour-return": cmd_plan_detour_return,
    "reconcile-code-face": cmd_reconcile_code_face,
    "commit-detour": cmd_commit_detour,
    "record-composed-type": cmd_record_composed_type,
    "recompose-check": cmd_recompose_check,
    "deferred-captures": cmd_deferred_captures,
    "resume-context": cmd_resume_context,
    "omtm": cmd_omtm,
    "exception-surface": cmd_exception_surface,
    "engagement-rate": cmd_engagement_rate,
    "execplan-gate-check": cmd_execplan_gate_check,
    "execplan-ack": cmd_execplan_ack,
    "execplan-reap": cmd_execplan_reap,
    # S1 — contract-hardening domain (execplan-contract-hardening, 2026-07-20)
    "register-presence": cmd_register_presence,
    "become-walker": cmd_become_walker,
    "checkout-slice": cmd_checkout_slice,
    "execplan-entry-check": cmd_execplan_entry_check,
    "execplan-entry-suppress": cmd_execplan_entry_suppress,
    "migrate-run-pointer-fields": cmd_migrate_run_pointer_fields,
    "restore-legacy-pointer": cmd_restore_legacy_pointer,
    # S2 — execution-boundary receipts + walk gate
    "record-conformance": cmd_record_conformance,
    "walk-gate-check": cmd_walk_gate_check,
    # execplan-nonredirect-write-guard — shared write-target detector
    "extract-bash-targets": cmd_extract_bash_targets,
    # A3 — SessionStart unarmed-multistep-plan WARNING
    "scan-unarmed": cmd_scan_unarmed,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in SUBCOMMANDS:
        _emit({"status": "ERROR",
               "error": f"usage: run.py <{'|'.join(sorted(SUBCOMMANDS))}> "
                        "< payload.json"}, 3)
    command = argv[0]
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as e:
        _emit({"status": "ERROR", "error": f"invalid JSON on stdin: {e}"}, 3)
    if not isinstance(payload, dict):
        _emit({"status": "ERROR", "error": "stdin payload must be a JSON object"}, 3)
    DISPATCH[command](payload)


if __name__ == "__main__":
    main()

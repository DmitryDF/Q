#!/usr/bin/env python3
"""PreToolUse lock-enforcement companion for check-discovery-lock.sh.

Reads PreToolUse JSON from stdin. If the targeted file is a _THOUGHT.md,
checks whether any DISCOVERY_LOCKED_FIELDS body is being changed. If so,
blocks (exit 2) unless the topic's `clarification_active_session` matches
the current SESSION_ID.

Exit 0 → allow the edit.
Exit 2 → block; prints a message to stderr naming the locked field and unlock path.
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pre_plan_gates import (
    DISCOVERY_LOCKED_FIELDS,
    DISCOVERY_MUTABLE_SUBSECTIONS,
    LOCK_MARKER_RE,
    PROJECTS_ROOT_ENV_INVALID,
    TOPIC_STATE_DIR,
    _resolve_spine_abs,
    _slug_from_spine_path,
    canonical_project_for_spine,
    discovery_section_body,
    extract_heading_body,
)

# NOTE: `pre_plan_gates._read_json` is deliberately NOT imported. It raises where
# the local `_read_json` below returns {}, and a corrupt record must degrade to a
# skipped record, never to a hook crash (an uncaught raise exits 1, which
# PreToolUse treats as NON-blocking — i.e. fail-open).

# NOTE: `LOCK_MARKER_RE` is imported, not re-declared. It used to be a local
# two-token literal here and a second copy in `_validate-thought-file.py`; both
# went inert on every clarification-v2 spine, which emits three tokens. One
# definition site is what keeps the two from drifting apart again (A8).


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_field_body(text, field, all_secs):
    """Extract the body of `field` from the `# Discovery` section of `text`,
    bounded by other headings in `all_secs`. Returns None when `# Discovery`
    or `field` is absent.

    Uses the shared anchored predicate, so this now returns the SAME span as
    `pre_plan_gates._discovery_locked_fields_hash`,
    `validate_discovery_locked_fields` and `_validate-thought-file.py` for the
    same spine — it used to only "mirror" them by copied code, which is how the
    six copies drifted."""
    body = discovery_section_body(text)
    if body is None:
        return None
    content = extract_heading_body(body, field, all_secs)
    return None if content is None else content.strip()


def _norm(path):
    """realpath + normcase — the normalization that collapses the vault symlink
    pair (`$CLAUDE_PROJECT_DIR` -> `~/repos/Projects`) so the two
    absolute forms of one file compare equal. Same pattern as
    `pre_plan_gates.canonical_project_for_spine`; stdlib, not an import."""
    return os.path.normcase(os.path.realpath(str(path)))


def _find_topic_state(thought_path):
    """Every topic-state record that describes this spine, and why.

    Returns ``(candidates, reason)`` where ``candidates`` is a list of
    ``(state_dict, state_file)`` — a SET, never a single winner, because a topic's
    records routinely straddle both tiers and the unlock token may sit on any one
    of them. ``reason`` is operator-facing: ``"matched"`` (non-empty) or
    ``"no-record"`` (nothing describes this spine).

    Identity is resolved in two DISJOINT tiers, decided globally over all records
    rather than per-file inside one unsorted glob:

    Tier A — spine-path identity (authoritative). A record joins when its
    recorded path, resolved against its own ``project_root`` and normalized,
    is this file. Two records that resolve to one file are about one thing
    however their keys are spelled.

    Tier B — name identity. A record joins ONLY when it records no path at all,
    and its key segments carry BOTH halves of this spine's identity: the
    timestamp-stripped slug AND the canonical project. A name alone cannot tell
    two same-named topics in different projects apart, so project agreement is a
    per-record admission condition — never a set-level check, or a foreign record
    could ride in behind a valid Tier-A anchor.

    Two distinctions carry most of the safety:

    * "records no path" means the field is FALSY (absent / null / empty string),
      NOT "records a path that does not resolve". A record naming another file
      must be reachable by neither tier, whether or not that file still exists.
    * Tier B is never gated on Tier A being empty. Every record currently holding
      a live token is path-less, so a short-circuit would drop the holder whenever
      any sibling happened to store the path.
    """
    state_dir = Path(str(TOPIC_STATE_DIR))
    if not state_dir.exists():
        return [], "no-record"

    # An unusable vault root can neither resolve a path nor derive a project, so
    # there is nothing to be confident about. Refuse rather than guess.
    if PROJECTS_ROOT_ENV_INVALID:
        return [], "no-record"

    target = _norm(thought_path)
    slug = _slug_from_spine_path(thought_path)
    canonical_project = canonical_project_for_spine(thought_path)

    candidates = []
    for state_file in sorted(state_dir.glob("*.json")):
        if state_file.name == "_active.json":
            continue
        try:
            state = _read_json(state_file)
            if not state:
                continue

            if state.get("thought_file_path"):
                resolved = _resolve_spine_abs(state)
                if resolved is not None and _norm(resolved) == target:
                    candidates.append((state, state_file))
                # Recorded a path -> never reachable by name, resolvable or not.
                continue

            # Read identity from the key SEGMENTS as an unordered set. Not the
            # `topic_slug` field: 57 live records fill it with the PROJECT, so it
            # cannot be trusted as a topic identifier. Membership over a split
            # list also handles 3-segment stems without positional indexing.
            segments = state_file.stem.split("__")
            if slug in segments and canonical_project in segments:
                candidates.append((state, state_file))
        except Exception:
            continue        # one unreadable record must not abort the scan

    return candidates, ("matched" if candidates else "no-record")


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw)
    except Exception:
        sys.exit(0)  # non-JSON input → allow

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", os.environ.get("SESSION_ID", ""))

    # Only care about Edit and Write
    if tool_name not in ("Edit", "Write"):
        sys.exit(0)

    file_path = tool_input.get("file_path", "")
    if not file_path.endswith("_THOUGHT.md"):
        sys.exit(0)

    # File must exist with locked fields to block
    p = Path(file_path)
    if not p.exists():
        sys.exit(0)

    current_text = p.read_text(encoding="utf-8")

    # Check if any locked marker exists in the file
    if not LOCK_MARKER_RE.search(current_text):
        sys.exit(0)  # not yet locked — no block

    # Determine proposed new text
    if tool_name == "Write":
        proposed_text = tool_input.get("content", "")
    elif tool_name == "Edit":
        old_string = tool_input.get("old_string", "")
        new_string = tool_input.get("new_string", "")
        if old_string not in current_text:
            sys.exit(0)  # edit won't match — allow (will fail anyway)
        proposed_text = current_text.replace(old_string, new_string, 1)
    else:
        sys.exit(0)

    # Check if any locked field body differs
    all_secs = DISCOVERY_LOCKED_FIELDS + DISCOVERY_MUTABLE_SUBSECTIONS
    locked_fields_changed = []
    for field in DISCOVERY_LOCKED_FIELDS:
        current_body = _extract_field_body(current_text, field, all_secs)
        proposed_body = _extract_field_body(proposed_text, field, all_secs)
        if current_body is None:
            continue  # field not present yet → allow (but see the guard below)
        if current_body != proposed_body:
            locked_fields_changed.append(field)

    # A3 (discovery-field-predicate-coherence S1) — the A4 guard that stood here
    # is DELETED, together with the `_field_status` predicate that fed it.
    #
    # What it did: refuse any Discovery-touching edit whenever a locked field's
    # name appeared in the body but its heading was in a form the anchored
    # predicate could not read. It was authored as the compensating hedge for
    # narrowing extraction, and it answered "is this field here" with a bare
    # `field in body` substring while the locator it guarded used the anchored
    # predicate — looser evidence than the thing it compensated for.
    #
    # Why removing it is safe, argued structurally rather than by corpus count:
    #   * a mis-levelled locked field is still refused downstream —
    #     `permission-plan-gate.sh` runs `_validate-thought-file.py` live on the
    #     spine at ExitPlanMode, it reports `Missing <field>`, and the gate blocks
    #     the plan exit (the `_THOUGHT_check.md` sidecar is advisory and no longer
    #     on that path, 2026-09-22);
    #   * an in-tool demotion (`## Metrics` -> `### Metrics`) is still refused by
    #     the main loop above, because the current body is text while the
    #     proposed body is None.
    # The residual widening is narrow and nameable: a spine ALREADY in the
    # unmatched state, reached out-of-band. That is how 2026-08-29 happened —
    # the guard locked a section's own author out of the only edit that would
    # have fixed it, while its message named a remedy (`Fix the heading … then
    # retry`) the guard itself blocked.
    #
    # What replaces it: a NON-BLOCKING report. `_validate-thought-file.py`'s
    # `_near_miss_heading_line` (A2 — this file's `_field_status` `unmatched`
    # arm, relocated, not re-authored) names the offending line under
    # `## Warnings` and the edit proceeds.

    if not locked_fields_changed:
        sys.exit(0)

    # Resolve identity. An unexpected raise here must degrade to a BLOCK: letting
    # it escape would exit 1, which PreToolUse treats as non-blocking — the one
    # failure mode worse than a false refusal.
    try:
        candidates, reason = _find_topic_state(file_path)
    except Exception:
        candidates, reason = [], "no-record"

    # Check the unlock token across every record describing this spine. The set
    # only ever contains records already proven to share one identity, so this
    # widens where the token may be recorded without widening identity itself.
    for state, _state_file in candidates:
        active = state.get("clarification_active_session")
        if active and active == session_id:
            sys.exit(0)  # unlock token matches — allow

    # Block. Which of the two failures the operator hit decides the remedy.
    field_names = ", ".join(f"`{f}`" for f in locked_fields_changed)
    if reason == "no-record":
        print(
            f"✗ Locked field(s) {field_names} cannot be edited directly.\n"
            f"  No tracking record could be matched to this file, so there was no\n"
            f"  place to look for permission to edit it. This is not the lock\n"
            f"  refusing you — the topic could not be identified.\n"
            f"  Point this topic's record at this file, then retry:\n"
            f"    python3 ${KIT_HOOKS_DIR}/pre_plan_gates.py set-thought-file "
            f"<SESSION_ID> {file_path}",
            file=sys.stderr,
        )
    else:
        print(
            f"✗ Locked field(s) {field_names} cannot be edited directly.\n"
            f"  These fields were locked at Step 9 of /clarification.\n"
            f"  To change them, re-enter Clarification: /clarification --from {file_path}",
            file=sys.stderr,
        )
    sys.exit(2)


if __name__ == "__main__":
    main()

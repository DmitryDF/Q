#!/usr/bin/env python3
"""bookkeeping_invariant — on-disk-shape enforcement for the Thoughts/ slug graph.

Single sectioned module (Cockburn Evolution Test, single locus — package-split
only when a 2nd predicate-consumer appears):

  ── domain (pure, no I/O) ──
      slug grammar regexes / TYPE sets / TYPE_TO_SLOT
      classify(filename) -> SlugMembership
      evaluate_family(touched, family, pre_snapshot) -> list[Violation]   # the 11 G-cases
  ── filesystem-read port ──
      read_family(thoughts_dir, slug) / read_pre_snapshot(key)
  ── stderr-output port ──
      emit(violations) -> exit code

Canonical contract: ~/.claude/rules/bookkeeping-model.md (§4 grammar, §8 contract,
§9 the eleven G-cases). This module is the executable form of that file; the two
move together.

Read-only: the hook never patches. On a blocking case it exits 2 with the exact
fix on stderr; the operator pastes the fix and the next save converges.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import sys

# ══════════════════════════════════════════════════════════════════════════
# ── domain (pure, no I/O) ──
# ══════════════════════════════════════════════════════════════════════════

# TYPE vocabularies (bookkeeping-model.md §4).
STANDARD_TYPES = ("THOUGHT_check", "THOUGHT", "DESIGN", "PLAN", "RESEARCH", "CLAIMS")
MULTI_TYPES = ("THOUGHT_check", "DESIGN", "PLAN", "RESEARCH")          # scope-keyed
ADVISORY_USER_TYPES = ("DISCOVERY_WIP", "DISCOVERY", "META", "CASES", "NEXT_SESSION_PROMPT", "ASSESSMENT", "ADMISSION")
ADVISORY_EPHEMERA_TYPES = ("DOUBLECHECK", "RECOMMEND", "CHALLENGE")    # + R<N> pattern

# Mandatory members (subject to G3/G4/G5/G7); THOUGHT is the spine, not a child.
MANDATORY_CHILD_TYPES = ("DESIGN", "PLAN", "RESEARCH", "CLAIMS", "THOUGHT_check")

# TYPE -> spine heading. An OQ18 rename is a one-line change here.
TYPE_TO_SLOT = {
    "DESIGN": "# Solution Design",
    "PLAN": "# Implementation Details",
    "RESEARCH": "# Discovery",
    "CLAIMS": "# Discovery",
    "THOUGHT_check": "# Discovery",
}

# slug = lowercase kebab; optional trailing -<14 digits> timestamp.
_SLUG_TS = r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*?)(?:-(?P<ts>\d{14}))?"
_STEM_RE = re.compile(r"^" + _SLUG_TS + r"_(?P<rest>.+)$")

# rest = [<SCOPE>_]<TYPE>[_<sid>]. SCOPE may contain underscores / mixed case
# (e.g. H_v2_B1), so the TYPE is anchored at the END of rest.
_STD_RE = re.compile(r"^(?P<type>" + "|".join(STANDARD_TYPES) + r")$")
_MULTI_RE = re.compile(r"^(?P<scope>.+)_(?P<type>" + "|".join(MULTI_TYPES) + r")$")
# Trailing suffix on advisory files: a session-id (hex), an ISO date
# (`_META_2026-06-16` — B3 Row 8), or any alphanumeric token, length >= 4.
_ADV_SID = r"(?:_(?P<sid>[0-9A-Za-z][0-9A-Za-z-]{3,}))?"
_ADV_USER_RE = re.compile(
    r"^(?:(?P<scope>.+)_)?(?P<type>" + "|".join(ADVISORY_USER_TYPES) + r")" + _ADV_SID + r"$"
)
_ADV_EPH_RE = re.compile(
    r"^(?:(?P<scope>.+)_)?(?P<type>R\d+|" + "|".join(ADVISORY_EPHEMERA_TYPES) + r")" + _ADV_SID + r"$"
)

# iCloud duplicate: "<base> 2.md", "<base> 3.md", ...
_ICLOUD_RE = re.compile(r"^(?P<base>.+) \d+\.md$")

# A Parent: line carrying a wikilink.
_PARENT_RE = re.compile(r"^\s*Parent:\s*\[\[(?P<target>[^\]|#]+?)(?:\.md)?(?:[|#][^\]]*)?\]\]\s*$", re.MULTILINE)

_RETIRED_RE = re.compile(r"^\*\*Status:\*\*.*\bRetired\s+\d{4}-\d{2}-\d{2}", re.MULTILINE)
_MODE_C_RE = re.compile(r"^bookkeeping:\s*mode-c\s*$", re.MULTILINE)
_MODE_C_RETIRED_RE = re.compile(r"^bookkeeping:\s*retired-\d{4}-\d{2}-\d{2}\s*$", re.MULTILINE)

# Buckets.
B_STANDARD = "standard"
B_MULTI = "multi"
B_ADVISORY = "advisory"
B_OUT_OF_SCOPE = "out_of_scope"
B_NON_MEMBER = "non_member"


@dataclasses.dataclass
class SlugMembership:
    filename: str           # basename, e.g. bookkeeping-model_PLAN.md
    bucket: str             # one of B_*
    slug: str | None = None
    ts: str | None = None
    scope: str | None = None
    type: str | None = None
    sid: str | None = None

    @property
    def stem(self) -> str:
        return self.filename[:-3] if self.filename.endswith(".md") else self.filename

    @property
    def is_spine(self) -> bool:
        return self.bucket == B_STANDARD and self.type == "THOUGHT"

    @property
    def is_mandatory_child(self) -> bool:
        return self.bucket in (B_STANDARD, B_MULTI) and self.type in MANDATORY_CHILD_TYPES


def classify(filename: str) -> SlugMembership:
    """Pure: filename -> SlugMembership. No filesystem access."""
    name = os.path.basename(filename)
    if not name.endswith(".md"):
        return SlugMembership(name, B_NON_MEMBER)
    # Leading-underscore audit-trail variants (e.g. _factcheck-convergence.md).
    if name.startswith("_"):
        return SlugMembership(name, B_OUT_OF_SCOPE)
    stem = name[:-3]
    m = _STEM_RE.match(stem)
    if not m:
        # No "_<TYPE>" segment -> bare-name .md, out-of-surface.
        return SlugMembership(name, B_OUT_OF_SCOPE)
    slug, ts, rest = m.group("slug"), m.group("ts"), m.group("rest")

    # Out-of-scope: _AUDIT[_<date>].
    if rest == "AUDIT" or rest.startswith("AUDIT_"):
        return SlugMembership(name, B_OUT_OF_SCOPE, slug=slug, ts=ts, type="AUDIT")

    # Advisory ephemera (fact-check) first — most specific keywords.
    am = _ADV_EPH_RE.match(rest)
    if am:
        return SlugMembership(name, B_ADVISORY, slug=slug, ts=ts,
                              scope=am.group("scope"), type=am.group("type"), sid=am.group("sid"))
    am = _ADV_USER_RE.match(rest)
    if am:
        return SlugMembership(name, B_ADVISORY, slug=slug, ts=ts,
                              scope=am.group("scope"), type=am.group("type"), sid=am.group("sid"))
    # Standard (no scope).
    sm = _STD_RE.match(rest)
    if sm:
        return SlugMembership(name, B_STANDARD, slug=slug, ts=ts, type=sm.group("type"))
    # Scope-keyed.
    mm = _MULTI_RE.match(rest)
    if mm:
        return SlugMembership(name, B_MULTI, slug=slug, ts=ts,
                              scope=mm.group("scope"), type=mm.group("type"))
    # Recognized slug but unknown TYPE -> not a member we own.
    return SlugMembership(name, B_NON_MEMBER, slug=slug, ts=ts)


@dataclasses.dataclass
class Family:
    slug: str
    members: list[SlugMembership]            # standard + multi + advisory
    contents: dict[str, str]                 # filename -> text
    icloud_collisions: list[str]             # raw filenames "<x> 2.md" colliding with the slug

    @property
    def spine(self) -> SlugMembership | None:
        return next((m for m in self.members if m.is_spine), None)

    def by_type(self, type_: str, scope: str | None = None) -> SlugMembership | None:
        for m in self.members:
            if m.type == type_ and (scope is None or m.scope == scope):
                return m
        return None


@dataclasses.dataclass
class Violation:
    case: str          # "G3".."G7", "GLC"
    blocking: bool
    fix: str


def _is_retired(family: Family) -> bool:
    spine = family.spine
    if spine is not None:
        if _RETIRED_RE.search(family.contents.get(spine.filename, "")):
            return True
    # Mode-C retired frontmatter on a lone plan.
    for m in family.members:
        if m.type == "PLAN" and _MODE_C_RETIRED_RE.search(family.contents.get(m.filename, "")):
            return True
    return False


def _is_mode_c(family: Family) -> bool:
    """Singleton bare-name {_PLAN.md} with `bookkeeping: mode-c` frontmatter."""
    core = [m for m in family.members if m.bucket in (B_STANDARD, B_MULTI)]
    if len(core) != 1:
        return False
    only = core[0]
    if only.type != "PLAN" or only.bucket != B_STANDARD:
        return False
    return bool(_MODE_C_RE.search(family.contents.get(only.filename, "")))


def _parent_target(child: SlugMembership, family: Family) -> SlugMembership | None:
    """Expected parent member, computable from on-disk state (bookkeeping-model.md §8)."""
    spine = family.spine
    if child.type == "PLAN":
        if child.bucket == B_MULTI:                       # scope-keyed plan
            sk_design = family.by_type("DESIGN", scope=child.scope)
            if sk_design:
                return sk_design
        bare_design = next((m for m in family.members            # bare-name design
                            if m.type == "DESIGN" and m.bucket == B_STANDARD), None)
        if bare_design:
            return bare_design
        return spine
    # DESIGN / RESEARCH / CLAIMS / THOUGHT_check -> spine.
    return spine


def _spine_links(spine_text: str, stem: str) -> bool:
    # Matches [[stem]], [[stem|alias]], [[stem#sec]], [[stem.md]].
    pat = re.compile(r"\[\[" + re.escape(stem) + r"(?:\.md)?(?:[|#\]])")
    return bool(pat.search(spine_text))


def evaluate_family(touched: SlugMembership, family: Family,
                    pre_snapshot: set[str] | None = None) -> list[Violation]:
    """The eleven G-cases. Returns the list of violations (empty == G1 clean).

    Cases that resolve to skip/pass/info return [] (callers distinguish via the
    touched bucket where they need the label). Blocking cases are G3-G7."""
    # G0 / GO — not a tracked member.
    if touched.bucket in (B_NON_MEMBER, B_OUT_OF_SCOPE):
        return []
    # GA — advisory write: recognized, info only.
    if touched.bucket == B_ADVISORY:
        return []
    # G2 — Mode-C ninja-plan exempt.
    if _is_mode_c(family):
        return []

    violations: list[Violation] = []

    # GLC — iCloud collisions (warn, non-blocking) — checked even on retired families.
    for dup in family.icloud_collisions:
        violations.append(Violation(
            "GLC", False,
            f"iCloud collision: `{dup}` duplicates a slug-family file. "
            f"Reconcile by hash+mtime and remove the duplicate (or quarantine to `_icloud_quarantine/`)."))

    if _is_retired(family):
        return violations  # retired family exempt from G3-G7

    spine = family.spine
    children = [m for m in family.members if m.is_mandatory_child]

    for child in children:
        ctext = family.contents.get(child.filename, "")
        target = _parent_target(child, family)
        # No in-family parent target (root/TODO-owned plan) -> no Parent required.
        if target is not None:
            pm = _PARENT_RE.search(ctext)
            if not pm:
                violations.append(Violation(
                    "G3", True,
                    f"`{child.filename}` is missing its parent link. "
                    f"Add to the top of the file:\n    Parent: [[{target.stem}]]"))
            else:
                tgt_stem = pm.group("target").strip()
                resolved = next((m for m in family.members if m.stem == tgt_stem), None)
                if resolved is None:
                    violations.append(Violation(
                        "G5", True,
                        f"`{child.filename}` has `Parent: [[{tgt_stem}]]`, which resolves to no "
                        f"file in the `{family.slug}` family. Fix it to:\n    Parent: [[{target.stem}]]"))
                elif resolved.type != target.type:
                    violations.append(Violation(
                        "G7", True,
                        f"`{child.filename}` points its Parent at a {resolved.type} "
                        f"(`{tgt_stem}`); the expected parent is the {target.type} "
                        f"`{target.stem}`. Fix it to:\n    Parent: [[{target.stem}]]"))
        # G4 — spine must wikilink this child.
        if spine is not None:
            stext = family.contents.get(spine.filename, "")
            if not _spine_links(stext, child.stem):
                slot = TYPE_TO_SLOT.get(child.type, "# Discovery")
                violations.append(Violation(
                    "G4", True,
                    f"The spine `{spine.filename}` is missing its link to `{child.filename}`. "
                    f"Add under `{slot}`:\n    - [[{child.stem}]]"))

    # G6 — order-dependent drop-link (needs the PreToolUse pre-snapshot).
    if pre_snapshot and spine is not None:
        stext = family.contents.get(spine.filename, "")
        existing_child_stems = {m.stem for m in children}
        for prev_stem in pre_snapshot:
            if prev_stem in existing_child_stems and not _spine_links(stext, prev_stem):
                violations.append(Violation(
                    "G6", True,
                    f"The spine `{spine.filename}` previously linked `{prev_stem}`, the link is "
                    f"now gone, and `{prev_stem}.md` still exists (order-dependent drop). "
                    f"Restore the link or delete the child."))
    return violations


# ══════════════════════════════════════════════════════════════════════════
# ── filesystem-read port ──
# ══════════════════════════════════════════════════════════════════════════

SNAPSHOT_DIR = os.path.join(os.path.expanduser("~"), ".claude", "state", "bookkeeping")


def read_family(thoughts_dir: str, slug: str) -> Family:
    members: list[SlugMembership] = []
    contents: dict[str, str] = {}
    collisions: list[str] = []
    try:
        names = os.listdir(thoughts_dir)
    except OSError:
        names = []
    for name in names:
        # iCloud collision detection on raw names.
        icm = _ICLOUD_RE.match(name)
        if icm:
            base_member = classify(icm.group("base") + ".md")
            if base_member.slug == slug and base_member.bucket in (B_STANDARD, B_MULTI, B_ADVISORY):
                collisions.append(name)
            continue
        m = classify(name)
        if m.slug == slug and m.bucket in (B_STANDARD, B_MULTI, B_ADVISORY):
            members.append(m)
            try:
                with open(os.path.join(thoughts_dir, name), "r", encoding="utf-8", errors="replace") as fh:
                    contents[name] = fh.read()
            except OSError:
                contents[name] = ""
    return Family(slug=slug, members=members, contents=contents, icloud_collisions=collisions)


def _snapshot_path(key: str, slug: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", f"{key}_{slug}")
    return os.path.join(SNAPSHOT_DIR, f"snapshot_{safe}.json")


def read_pre_snapshot(key: str, slug: str) -> set[str] | None:
    """Spine child-link snapshot written by the DS4 PreToolUse sidecar, keyed by
    (session, slug). Returns the set of child stems the spine linked before the
    write, or None if no snapshot exists (then G6 is skipped — single-process-safe
    within a session; cross-session race is the deferred plan_locks item)."""
    try:
        with open(_snapshot_path(key, slug), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return set(data.get("child_wikilinks", []))
    except (OSError, ValueError):
        return None


def consume_pre_snapshot(key: str, slug: str) -> None:
    """Delete a consumed snapshot so a later Post without a fresh Pre can't reuse it."""
    try:
        os.remove(_snapshot_path(key, slug))
    except OSError:
        pass


def write_snapshot(payload: dict) -> int:
    """PreToolUse sidecar: capture each touched family's spine outbound child
    wikilinks BEFORE the write, so the PostToolUse G6 check can detect an
    order-dependent drop-link. Always exits 0 (never blocks a write)."""
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    key = payload.get("session_id") or str(os.getppid())
    for path in _touched_paths(tool_name, tool_input):
        resolved = _thoughts_dir_and_member(path)
        if resolved is None:
            continue
        thoughts_dir, member = resolved
        if not member.slug or member.bucket in (B_NON_MEMBER, B_OUT_OF_SCOPE):
            continue
        family = read_family(thoughts_dir, member.slug)
        spine = family.spine
        if spine is None:
            continue
        stext = family.contents.get(spine.filename, "")
        linked = [m.stem for m in family.members
                  if m.is_mandatory_child and _spine_links(stext, m.stem)]
        try:
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
            with open(_snapshot_path(key, member.slug), "w", encoding="utf-8") as fh:
                json.dump({"key": key, "slug": member.slug,
                           "spine": spine.filename, "child_wikilinks": linked}, fh)
        except OSError:
            pass
    return 0


def _thoughts_dir_and_member(path: str) -> tuple[str, SlugMembership] | None:
    """Resolve a touched path to its Thoughts/ dir + membership, or None."""
    norm = os.path.normpath(path)
    parts = norm.split(os.sep)
    if "Thoughts" not in parts:
        return None
    idx = len(parts) - 1 - parts[::-1].index("Thoughts")
    thoughts_dir = os.sep.join(parts[: idx + 1])
    member = classify(parts[-1])
    return thoughts_dir, member


# ══════════════════════════════════════════════════════════════════════════
# ── stderr-output port ──
# ══════════════════════════════════════════════════════════════════════════

def emit(violations: list[Violation]) -> int:
    """Print violations to stderr. Return the process exit code (2 if any blocking)."""
    if not violations:
        return 0
    blocking = [v for v in violations if v.blocking]
    warns = [v for v in violations if not v.blocking]
    out = []
    if blocking:
        out.append("BLOCKED: bookkeeping-model on-disk contract violated "
                   "(read-only check — paste the fix below; the next save converges).")
    for v in blocking + warns:
        tag = "✗" if v.blocking else "⚠"
        out.append(f"  {tag} [{v.case}] {v.fix}")
    out.append("  Contract: ~/.claude/rules/bookkeeping-model.md §9")
    sys.stderr.write("\n".join(out) + "\n")
    return 2 if blocking else 0


def _touched_paths(tool_name: str, tool_input: dict) -> list[str]:
    if tool_name in ("Write", "Edit"):
        fp = tool_input.get("file_path")
        return [fp] if fp else []
    if tool_name == "Bash":
        cmd = tool_input.get("command", "") or ""
        # Best-effort: any token containing a Thoughts/ .md path.
        return re.findall(r"[^\s'\";|&]*Thoughts/[^\s'\";|&]*\.md", cmd)
    return []


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return 0
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    key = payload.get("session_id") or str(os.getppid())

    all_violations: list[Violation] = []
    seen_dirs_slugs: set[tuple[str, str]] = set()
    for path in _touched_paths(tool_name, tool_input):
        resolved = _thoughts_dir_and_member(path)
        if resolved is None:
            continue
        thoughts_dir, member = resolved
        if member.bucket in (B_NON_MEMBER, B_OUT_OF_SCOPE) or not member.slug:
            continue
        if (thoughts_dir, member.slug) in seen_dirs_slugs:
            continue
        seen_dirs_slugs.add((thoughts_dir, member.slug))
        family = read_family(thoughts_dir, member.slug)
        pre = read_pre_snapshot(key, member.slug)
        all_violations.extend(evaluate_family(member, family, pre_snapshot=pre))
        consume_pre_snapshot(key, member.slug)
    return emit(all_violations)


if __name__ == "__main__":
    if "--snapshot" in sys.argv[1:]:
        try:
            sys.exit(write_snapshot(json.load(sys.stdin)))
        except (ValueError, OSError):
            sys.exit(0)
    sys.exit(main())

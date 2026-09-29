#!/usr/bin/env python3
"""bookkeeping_migrate — one-time backfill of the bidirectional contract.

Walks a Thoughts/ directory, groups files into slug-families, and for each
family adds the two things the contract needs so navigation works on day 1:
  - a `Parent: [[...]]` line on every mandatory child that lacks one
  - a `- [[child]]` wikilink under the right spine heading for every mandatory
    child the spine doesn't already list

Idempotent (a second run makes zero changes) and grandfather-aware (existing
untimestamped files are accepted as-is — no renames, no timestamps invented).
Retired and Mode-C families are skipped. Reuses bookkeeping_invariant's domain
layer as the single source of grammar + parent-target rules (Cockburn single
locus).

Usage:
    bookkeeping_migrate.py <thoughts_dir>            # dry-run (default): print plan
    bookkeeping_migrate.py <thoughts_dir> --apply    # write the changes
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bookkeeping_invariant as bi


def _insert_parent_line(text: str, parent_stem: str) -> tuple[str, bool]:
    if bi._PARENT_RE.search(text):
        return text, False
    line = f"Parent: [[{parent_stem}]]"
    lines = text.split("\n")
    insert_idx = 0
    if lines and lines[0].strip() == "---":            # skip YAML frontmatter
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                insert_idx = i + 1
                break
    lines.insert(insert_idx, line)
    return "\n".join(lines), True


def _insert_spine_link(text: str, child_stem: str, slot_heading: str) -> tuple[str, bool]:
    if bi._spine_links(text, child_stem):
        return text, False
    link = f"- [[{child_stem}]]"
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == slot_heading:
            lines.insert(i + 1, link)
            return "\n".join(lines), True
    # Heading absent -> append it with the link.
    if lines and lines[-1] != "":
        lines.append("")
    lines.append(slot_heading)
    lines.append(link)
    return "\n".join(lines), True


def _slugs_in_dir(thoughts_dir: str) -> list[str]:
    slugs = set()
    try:
        names = os.listdir(thoughts_dir)
    except OSError:
        return []
    for name in names:
        m = bi.classify(name)
        if m.slug and m.bucket in (bi.B_STANDARD, bi.B_MULTI, bi.B_ADVISORY):
            slugs.add(m.slug)
    return sorted(slugs)


def plan_family(thoughts_dir: str, slug: str) -> tuple[dict[str, str], list[str]]:
    """Return (changed_contents, change_descriptions) for one family. Empty == nothing to do."""
    family = bi.read_family(thoughts_dir, slug)
    changed: dict[str, str] = {}
    notes: list[str] = []
    if bi._is_mode_c(family) or bi._is_retired(family):
        return changed, notes

    spine = family.spine
    work = dict(family.contents)
    for child in (m for m in family.members if m.is_mandatory_child):
        target = bi._parent_target(child, family)
        if target is None:
            continue
        # Parent: line on the child.
        new_child, did = _insert_parent_line(work[child.filename], target.stem)
        if did:
            work[child.filename] = new_child
            changed[child.filename] = new_child
            notes.append(f"  + {child.filename}: Parent: [[{target.stem}]]")
        # Wikilink in the spine.
        if spine is not None:
            slot = bi.TYPE_TO_SLOT.get(child.type, "# Discovery")
            new_spine, did = _insert_spine_link(work[spine.filename], child.stem, slot)
            if did:
                work[spine.filename] = new_spine
                changed[spine.filename] = new_spine
                notes.append(f"  + {spine.filename}: - [[{child.stem}]]  (under {slot})")
    return changed, notes


def migrate_dir(thoughts_dir: str, apply: bool) -> list[str]:
    all_notes: list[str] = []
    for slug in _slugs_in_dir(thoughts_dir):
        changed, notes = plan_family(thoughts_dir, slug)
        if not changed:
            continue
        all_notes.append(f"[{slug}]")
        all_notes.extend(notes)
        if apply:
            for fname, content in changed.items():
                with open(os.path.join(thoughts_dir, fname), "w", encoding="utf-8") as fh:
                    fh.write(content)
    return all_notes


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    apply = "--apply" in argv
    if not args:
        sys.stderr.write(__doc__ + "\n")
        return 2
    thoughts_dir = args[0]
    if not os.path.isdir(thoughts_dir):
        sys.stderr.write(f"not a directory: {thoughts_dir}\n")
        return 2
    notes = migrate_dir(thoughts_dir, apply)
    mode = "APPLIED" if apply else "DRY-RUN (no changes written; pass --apply)"
    if not notes:
        print(f"{mode}: nothing to backfill — already conformant.")
        return 0
    print(f"{mode}: {sum(1 for n in notes if n.startswith('  + '))} change(s):")
    print("\n".join(notes))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

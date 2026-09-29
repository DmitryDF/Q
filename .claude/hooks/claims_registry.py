#!/usr/bin/env python3
"""claims_registry — the ONE naming authority for a research file's claims register.

Two jobs live here, and since research-entry-point-enforcement S5 the first one
is the load-bearing half:

1. **`register_address(research_file_path)` — the register addresser (A8).**
   The single place a register's address is derived. The harvest trigger
   (`_claim_harvest_trigger._topic_paths`) calls it directly; the engine reaches
   the same slug through `register_slug()` — the naming half of this same
   derivation — applied to its own already-derived display key, rather than
   calling `register_address` itself. Either path yields the same register no
   matter which one gets there first.
2. **`ensure_exists(directory, slug)`** — auto-create a conformant skeleton when
   absent, a no-op when present (bookkeeping-model drift Row 6;
   `claims-registry.md` rule #10 says the registry is "auto-generated on first
   fact-check run" and for a long time nothing created it).

Why the addresser exists (findings 14, 15, 29, 34). Before S5 there were two
derivations for one address and they disagreed:

  * `_factcheck_engine._research_display_slug(path)` — TOTAL (it falls back to the
    case-folded stem for a name the bookkeeping grammar rejects) and it joins a
    scope segment with `--`, producing e.g. `foo--bar`.
  * `_claim_harvest_trigger`'s `_bi.classify(name).slug` — PARTIAL: it returns
    `None` for any name the grammar rejects (uppercase, underscores in the slug —
    every CV-arm file), so the harvest could address **no register at all** for
    such a file, and it drops the scope segment entirely.

So a scope-keyed research file got `foo--bar_CLAIMS.md` skeleton-created by the
engine's `ensure_exists` call and `foo_CLAIMS.md` addressed by the harvest — two
files per topic. No claim was ever actually written at the engine's address (its
only `_CLAIMS` involvement is `ensure_exists`, which writes the skeleton and
nothing else); the real harm was a duplicate, ungrammatical, permanently-empty
skeleton register sitting alongside the one the harvest actually populates
(finding 34). The addresser ends that by being the only derivation.

**What the addresser decides, and the evidence for it.**

*It clones the TOTAL derivation.* `_research_display_slug` is reused verbatim
rather than re-implemented — S1 established the partial one would leave the S7
checkpoint unable to address a register for every CV-arm run, and the engine's own
single-locus rail means there is exactly one `classify(` call in that module. This
module adds none.

*The register name is Bucket-1 shaped, and that is the only shape the bookkeeping
grammar recognises.* Measured against `bookkeeping_invariant.classify` on
2026-09-29:

    foo_CLAIMS.md                    -> standard   (slug='foo',  type='CLAIMS')
    foo-<14-digit-ts>_CLAIMS.md      -> standard   (slug='foo',  type='CLAIMS')
    foo--bar_CLAIMS.md               -> out_of_scope (slug=None) <- finding 29
    foo-<ts>_BAR_CLAIMS.md           -> non_member  (CLAIMS is not in MULTI_TYPES)

There is no scope-keyed register shape to emit: `CLAIMS` is a STANDARD type, not a
MULTI one, so `<slug>_<SCOPE>_CLAIMS.md` is outside the grammar just as `--` is.
Widening `MULTI_TYPES` is barred — that module is a shared harness contract this
topic does not own (A8's guard rail), so the closing move is to generate a
conforming name, which is what happens here.

*Hence: one register per slug family, and the scope becomes the AREA.* This is
Q6 read literally — "the slug binds the register to its document family; the area
records what the research was done for". The slug binds (it is in the filename);
the area records (it is returned for the recorder to write into a row). The design
left the choice of where the area is read from to this slice ("`cycle_id` is the
other candidate — S5 decides which the addresser reads"); the filename's scope
segment wins, for two reasons: the addresser is handed a path and nothing else, so
reading the filename keeps A8's "slug and area are never parameters from the
adapter" true by construction; and `cycle_id` is not in scope at every call site
(the engine's is not, and the harvest's PostToolUse wrapper has none at all).

*The area is carried by the row's LOCATOR, not by a column.* Since S6 the finding
recorder (`research_pipeline.record_finding`) addresses every register entry
through `ensure_for_research_file`, and each entry it writes is anchored at
`<research file>:<line>` — the file whose name this module derives the area from.
So a row records what its research was for by pointing at it; no area column
exists, because adding one would change the Evidence Register's row schema
(`_claim_register.py`), which this module does not own. Said plainly so nobody
reads a register row expecting an `area` field.

**Authority is not delegated (A8).** `register_address` derives slug and area
itself. The lower-level `claims_path` / `ensure_exists` still take a slug, because
they are also the module's CLI and the `thought`-kind call site — so that surface
is whitelisted to `^[a-zA-Z0-9_-]+$` and the resolved register path is asserted
inside the directory it was given. An adversarial prompt injection in a fetched
web source is a realistic way a traversal token reaches this code.

Standalone CLI:
    claims_registry.py <thoughts_dir> <slug>      # create if absent; print path
    claims_registry.py --address <research_file>   # print the derived address
"""
from __future__ import annotations

import dataclasses
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bookkeeping_invariant as bi

_SKELETON = """{parent}# Claims Registry — {slug}

Auto-generated by `claims_registry.ensure_exists()` on first fact-check round.
Convention: `.claude/rules/claims-registry.md` (locators, not full claim text;
the source file is master). Do not create manually; do not hand-edit claim text.

## Source Index
<!-- S1 | <url> -->

## Research Claims
<!-- locator (section :: distinctive_phrase) | source ID | status | checked date -->

## Initiative Claims
<!-- locator | derived_from (research claim IDs) | status -->

## Sync Log
<!-- date | action -->
"""

# The outside-surface whitelist (A8). Deliberately LOOSER than the bookkeeping
# grammar it does NOT converge on — convergence there was measured and rejected
# (the bookkeeping grammar forbids consecutive hyphens and uppercase, and
# converging onto it regresses eight existing registers). Looser is correct
# because this pattern's job is containment, not grammar conformance: `/`, `\`,
# NUL and `..` all fail it, which is the whole point, and admitting uppercase,
# underscores and consecutive hyphens costs nothing towards that job. Grammar
# conformance (the register's actual NAME) is a separate concern, handled by
# `register_slug()` below.
_SLUG_RE = re.compile(r"^[a-zA-Z0-9_-]+$")

# The scope join `_research_display_slug` emits. It can never occur inside a
# normalized part (that function collapses every run of non-alphanumerics to a
# single '-'), so splitting on it is unambiguous and recovers exactly the two
# parts it joined.
_SCOPE_JOIN = "--"

# One or more trailing 14-digit timestamp segments (`(-<yyyymmddHHMMSS>)+`) —
# the shape `bookkeeping_invariant.classify()`'s own grammar recognises and
# strips off (one segment, from the end) when it parses a REGISTER filename
# (`_STEM_RE`'s `-(?P<ts>\d{14})` group). In PRACTICE this arises almost
# entirely through `_research_display_slug`'s FALLBACK branch (a name the
# grammar rejects at the stem level): that branch never runs the raw name
# through `classify()`, so a stamp embedded anywhere in it survives into the
# display key unstripped. It is NOT reached exclusively through that branch,
# though — a grammar-conforming name carrying TWO consecutive trailing
# timestamp segments (e.g. `foo-20260101000000-20260101000000_RESEARCH.md`)
# also leaves one stamp embedded in the slug `classify()` hands back: that
# function's own `_SLUG_TS` grammar is greedy on the timestamp group and
# lazy on the slug group, so only the LAST of the two segments is split off
# and the second-to-last stays folded into what `classify()` calls the
# slug. No such doubled-timestamp name exists anywhere in the corpus
# measured for this fix (432 `*_RESEARCH*.md` files under `~/repos`, zero
# carrying even one trailing timestamp segment) — it is closed here because
# the accompanying test asserts the `addr.slug == classify(register).slug`
# invariant UNIVERSALLY, not because the shape is reachable today.
#
# Left unstripped, `register_slug` used to mint e.g.
# `my-topic-20260101000000_CLAIMS.md` — a register whose OWN filename,
# re-classified, strips that same timestamp back off and reports
# `slug='my-topic'`. That mismatch (`addr.slug != classify(basename).slug`)
# is what `_spine_stem` searches under, so it looked for a spine named
# `my-topic-20260101000000_THOUGHT.md` (classify would strip ITS timestamp
# too and report `my-topic`) and never found one — no `Parent:` line minted,
# recreating finding 16's defect on this class of input (MAJOR 2, S5 fixer
# review). A DOUBLED trailing timestamp reproduces the identical mismatch
# one segment further out (a single-segment strip left one stamp still
# embedded); the pattern below strips every trailing timestamp segment
# rather than only the last one, closing that further-out case at the same
# time (S5 closing-review fix — universal invariant, no live instance).
# Fixed here, not in `_factcheck_engine.py`: this function already
# owns exactly this decision (folding a display key into a grammar-
# conforming register slug), a trailing timestamp the grammar itself would
# strip is a grammar-conformance concern, and an engine edit risks both the
# single-`classify(`-call rail and AD15's byte-for-byte `thought`-kind pin.
_TRAILING_TS_RE = re.compile(r"(?:-\d{14})+$")


@dataclasses.dataclass(frozen=True)
class RegisterAddress:
    """Where a research file's claims register lives, and what it is for.

    `directory` and `path` are absolute. `slug` is the grammar-conforming
    Bucket-1 slug that names the register; `area` is the research file's scope
    segment (normalized, lowercase) or None when it has none.
    """

    directory: str
    slug: str
    area: str | None
    path: str


def _check_slug(slug) -> str:
    """Whitelist a slug arriving from an outside surface. Raises ValueError."""
    if not isinstance(slug, str) or not slug:
        raise ValueError(
            f"claims_registry: slug must be a non-empty string, got {slug!r}")
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"claims_registry: slug {slug!r} is not [a-zA-Z0-9_-]+ — a slash, "
            f"backslash, NUL byte or '..' segment is refused outright, not "
            f"sanitized away")
    return slug


def _assert_inside(directory: str, path: str) -> None:
    """The resolved register path must not leave the directory it was given.

    Replicated rather than imported from `skills/research/scope_record.py`, which
    holds the same two primitives: `hooks/` does not import from `skills/`
    (`research_pipeline.validate_schema` states the same dependency-direction
    rule at its own `scope_record` decision), and the harness convention is that
    a shared rule is replicated, never imported across that boundary.
    """
    root = os.path.realpath(directory)
    target = os.path.realpath(path)
    if target != root and not target.startswith(root + os.sep):
        raise ValueError(
            f"claims_registry: register path {target!r} resolves outside its "
            f"directory {root!r}")


def claims_path(directory: str, slug: str) -> str:
    """`<directory>/<slug>_CLAIMS.md`, whitelisted and containment-asserted."""
    _check_slug(slug)
    path = os.path.join(directory, f"{slug}_CLAIMS.md")
    _assert_inside(directory, path)
    return path


def register_slug(display_key: str) -> str:
    """Fold a research file's display key into the register's Bucket-1 slug.

    The ONE place that decision is made. `_factcheck_engine`'s keyed derivation
    emits `<slug>--<scope>` for a scope-keyed research file; the grammar
    recognises no scope-keyed register shape (see this module's header for the
    measurements), so the register is named for the slug family and the scope
    becomes the `area`.

    Also strips a trailing 14-digit timestamp segment the FALLBACK half of
    that derivation can leave behind (see `_TRAILING_TS_RE` above) — a
    grammar-conformance fix, not a naming-authority one: the grammar-
    conforming branch never produces one here, so this is a no-op for every
    input this function already handled correctly.

    Kept as its own function, separate from `register_address`, because the engine
    reaches this module already holding its own display key: that derivation is
    pinned to a single locus in `_factcheck_engine` by its own rail, so
    re-deriving it here would be a second locus. The engine supplies the key; this
    module decides the NAME. The harvest, which holds only a path, goes through
    `register_address` and gets the same answer.
    """
    if not isinstance(display_key, str) or not display_key:
        raise ValueError(
            f"claims_registry: display key must be a non-empty string, "
            f"got {display_key!r}")
    slug, _, _area = display_key.partition(_SCOPE_JOIN)
    _stripped = _TRAILING_TS_RE.sub("", slug)
    if _stripped:
        slug = _stripped
    return _check_slug(slug)


def register_area(display_key: str) -> str | None:
    """The `area` half of the same fold — what the research was done for (Q6).

    Returned to the finding recorder with every address (S6); a register row
    carries it through its locator, which names the research file this is derived
    from (see the module header). Exposed here so the decision about where the
    area comes from lives beside the decision about the name.
    """
    if not isinstance(display_key, str):
        return None
    _slug, _, area = display_key.partition(_SCOPE_JOIN)
    return area or None


def register_address(research_file_path) -> RegisterAddress:
    """**The one register addresser.** Derive a research file's register address.

    Slug and area are derived here from the file's own name — never accepted as
    parameters — so no caller can split a topic's register or address one outside
    its own directory.

    Raises ValueError on three causes, checked in this order: (1) the path
    contains a NUL byte — checked FIRST, before any name parsing is attempted;
    (2) no address can be derived (an empty or unnameable basename); and (3)
    `claims_path` → `_assert_inside` finds that `<slug>_CLAIMS.md` already
    exists as a symlink resolving outside the directory — the containment
    check, not a naming failure.

    *(Corrected 2026-09-30, research-entry-point-enforcement S6 FIXER review
    — this used to claim `_claim_harvest_trigger._topic_paths` was "the one
    production call site". It was already one of at least two by S6 —
    `ensure_for_research_file` is the other, and is itself called from
    `research_pipeline._record_register_entry` — and the S6 recorder
    (`research_pipeline.record_finding`) now also calls this function
    directly, to derive the register's own lock key before writing to it. The
    singular claim is retired rather than repeated.)*

    `_claim_harvest_trigger._topic_paths` treats any of these as "no
    register", exactly as it treated a `None` slug before — and it does so by
    catching `Exception`, not `ValueError`, so a failure from any cause here
    (or from the lazy `_factcheck_engine` import `_display_key` makes) reads
    the same way. The `--address` CLI verb below reports the error instead of
    swallowing it. The engine does not call this function at all — it derives
    its own display key and calls `register_slug()`, wrapped in a broad
    `except Exception: pass` that is not the same handling.
    """
    raw = str(research_file_path)
    if "\x00" in raw:
        raise ValueError("claims_registry: research file path contains a NUL byte")
    directory = os.path.dirname(os.path.abspath(raw))
    key = _display_key(raw)
    slug = register_slug(key)
    path = claims_path(directory, slug)
    return RegisterAddress(directory=directory, slug=slug,
                           area=register_area(key), path=path)


def _display_key(research_file_path: str) -> str:
    """The TOTAL slug derivation, reused from the engine rather than re-derived.

    Lazy import: `_factcheck_engine` imports this module (inside a function) on
    its own register-creation path, so importing it at module scope here would
    close a cycle. It is also large, and the `thought`-kind and CLI callers of
    `ensure_exists` never need it.
    """
    import _factcheck_engine as _fe
    return _fe._research_display_slug(research_file_path)


def _spine_stem(directory: str, slug: str) -> str | None:
    try:
        names = os.listdir(directory)
    except OSError:
        return None
    for name in names:
        m = bi.classify(name)
        if m.slug == slug and m.is_spine:
            return m.stem
    return None


def ensure_exists(directory: str, slug: str) -> tuple[str, bool]:
    """Create `<directory>/<slug>_CLAIMS.md` if absent. Returns (path, created).

    A refused slug raises (see `_check_slug`), and so does a containment
    failure (`claims_path` → `_assert_inside`, register_address's cause (3),
    reached here when a passing slug's `<slug>_CLAIMS.md` already exists as a
    symlink resolving outside the directory) — neither writes somewhere
    surprising. Every OTHER failure (an `OSError` from the write itself) still
    degrades to `(path, False)` so a registry hiccup cannot break a
    fact-check run.

    MAJOR 4a (research-entry-point-enforcement S6 FIXER review): the actual
    create is EXCLUSIVE (`open(path, "x", ...)`), never check-then-truncate.
    The `os.path.exists` check below is an advisory fast path only — it
    exists to skip the spine lookup and skeleton formatting for the common
    case where the register already exists, never to decide whether the
    write itself is safe. Two concurrent first-callers can both pass that
    check (both see the file absent); before this fix both then opened in
    `"w"` mode, and the SECOND writer's truncating open silently discarded
    whatever the first writer had already written — a real race on a file a
    concurrent recorder (S6) can be appending rows into at the same moment.
    `"x"` makes the loser of that race fail with `FileExistsError` (an
    `OSError` subclass, caught below) instead of truncating, and that
    failure is treated exactly like "the file was already there": `(path,
    False)`.
    """
    path = claims_path(directory, slug)
    if os.path.exists(path):
        return path, False
    spine = _spine_stem(directory, slug)
    parent = f"Parent: [[{spine}]]\n\n" if spine else ""
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(_SKELETON.format(parent=parent, slug=slug))
        _record_ledger_write(path)   # A5 / gap G4 — a new tracked repo file
        return path, True
    except OSError:
        return path, False


def ensure_for_research_file(research_file_path) -> tuple[RegisterAddress, bool]:
    """`register_address` + `ensure_exists`, the pair every research writer wants.

    Production caller since S6: `research_pipeline._record_register_entry`, the
    finding recorder's register half. Creating through here (rather than
    letting `EvidenceRegister.ensure_exists` do it) is what gives a recorded
    register this module's skeleton, whose `Parent:` line is minted only when a
    spine exists.
    """
    addr = register_address(research_file_path)
    _, created = ensure_exists(addr.directory, addr.slug)
    return addr, created


def _record_ledger_write(path) -> None:
    """Record a code-layer write to this session's file ledger (A5 / gap G4).

    Placed INSIDE the success branch: a registry that was not created is not a
    file this session wrote, and declaring it would name a path that does not
    exist — which aborts the whole `git add` on an unmatched pathspec.

    Lazy, guarded, silent on failure; `record_write` never raises and this
    wrapper extends that to the import.
    """
    try:
        from commit_scope import record_write as _rw   # sibling dir already on
        _rw(path)                                      # sys.path (the insert above)
    except Exception:
        pass


def main(argv: list[str]) -> int:
    if argv and argv[0] == "--address":
        if len(argv) < 2:
            sys.stderr.write("usage: claims_registry.py --address <research_file>\n")
            return 2
        try:
            addr = register_address(argv[1])
        except ValueError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2
        print(f"slug={addr.slug} area={addr.area} path={addr.path}")
        return 0
    if len(argv) < 2:
        sys.stderr.write(
            "usage: claims_registry.py <thoughts_dir> <slug>\n"
            "       claims_registry.py --address <research_file>\n")
        return 2
    try:
        path, created = ensure_exists(argv[0], argv[1])
    except ValueError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2
    print(f"{'created' if created else 'exists'}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

"""Same-class refusals render as one entry — without losing a name or a signal.

`refusal-grouping` slice D4 / plan action A3.

**Why these tests and not corpus numbers.** The plan was written against measured
runs over three real repositories, and the very first implementation invalidated
one of those corpora: `declared_read.py` grew, so it consumed more of the
aggregate budget and the run stopped before reaching the files whose repetition
was being measured. A real-corpus figure is evidence that the problem exists; it
is not a regression test. These fixtures are.

**The two failures this suite exists to catch** are both ones that leave every
other assertion green:

* An **obligation-only merge key.** It would fold the missing-authorization
  notice into the decode-failure group and bury it — the exact inverse of what
  the grouping is for. `test_a_refusal_needing_a_decision_never_merges_into_the_bulk`.
* A merge exclusion written over the **obligation** instead of the **reason**.
  `OBLIGATION_ENUMERATION_BOUND` spans two branches with opposite needs, so a
  blanket exclusion forces the unbounded depth class unmerged and the ordering
  then hoists it above the bulk. `test_depth_refusals_still_merge_and_stay_below_the_fold`.

A suite that planted only the missing-authorization case would stay green through
both. The second test is the one that distinguishes the shipped narrow form from
the blanket form that was tried first and rejected.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
sys.path.insert(0, str(CONFIG_DIR / "skills"))

from research import declared_read as dr                              # noqa: E402
from research import source_port as sp                                # noqa: E402


def _decode_refusal(path: str) -> dr.RefusedRead:
    """The dominant real-world class: reason interpolates the path, so no two are
    string-equal and a raw-reason key would collapse none of them."""
    return dr.RefusedRead(
        item=path,
        obligation=sp.OBLIGATION_READABLE,
        reason=f"{path} is not UTF-8 text and cannot be addressed by a line locator")


def _result(*refusals: dr.RefusedRead) -> dr.DeclaredReadResult:
    return dr.DeclaredReadResult(readings=(), refusals=tuple(refusals))


# --------------------------------------------------------------------------- #
# Merging, and what it must not cost.
# --------------------------------------------------------------------------- #

def test_same_class_refusals_render_as_one_entry_naming_every_member():
    """The headline. Six decode failures are one line, and a reader can still act
    on any of them because all six paths are on it."""
    paths = [f"/repo/__pycache__/mod{n}.cpython-314.pyc" for n in range(6)]
    result = _result(*(_decode_refusal(p) for p in paths))

    lines = result.refusal_lines
    assert len(lines) == 1, f"expected one merged entry, got {lines}"
    assert "6 items" in lines[0], f"the count is missing: {lines[0]}"
    for path in paths:
        assert path in lines[0], (
            f"{path} stopped being named, so a person cannot act on it: {lines[0]}")


def test_a_lone_refusal_renders_byte_identically_to_today():
    """The non-regression that matters most: a report with nothing repetitive in
    it must not move at all. Compared against `render()` itself rather than a
    re-derived string, because a re-derivation that drifts would pass a looser
    check."""
    one = _decode_refusal("/repo/asset.bin")
    assert _result(one).refusal_lines == (one.render(),)


def test_refusals_of_different_classes_do_not_merge():
    """An empty file and a binary are both `[readable]`, and they are different
    problems with different remedies."""
    empty = dr.RefusedRead(item="/repo/blank.py",
                           obligation=sp.OBLIGATION_READABLE,
                           reason="the file holds no content, so a citation to it "
                                  "would name a source with nothing behind it")
    result = _result(empty, _decode_refusal("/repo/a.pyc"))
    assert len(result.refusal_lines) == 2


# --------------------------------------------------------------------------- #
# The two failures that would otherwise ship green.
# --------------------------------------------------------------------------- #

def test_a_refusal_needing_a_decision_never_merges_into_the_bulk():
    """FAILS under an obligation-only key.

    A missing authorization carries the same `[readable]` tag as a decode
    failure, so keying on the obligation alone folds it into the group and buries
    the one entry in the list a person has to do something about.
    """
    authorization = dr.RefusedRead(
        item="every declared linear source",
        obligation=sp.OBLIGATION_READABLE,
        reason="the declaration names a Linear source but this run holds no "
               "Linear authorization; connect Linear and run this again")
    result = _result(authorization,
                     _decode_refusal("/repo/a.pyc"),
                     _decode_refusal("/repo/b.pyc"))

    lines = result.refusal_lines
    assert len(lines) == 2, f"expected the notice plus one group, got {lines}"
    own = [l for l in lines if "Linear authorization" in l]
    assert len(own) == 1 and "2 items" not in own[0], (
        f"the authorization notice was merged into the bulk: {own}")


def test_depth_refusals_still_merge_and_stay_below_the_fold():
    """FAILS if the merge exclusion is written over the OBLIGATION.

    `OBLIGATION_ENUMERATION_BOUND` covers two branches. The item-count ceiling
    ends the run and is decision-needing; the depth bound `continue`s and repeats
    without limit on any vendored tree. Excluding the obligation would force these
    unmerged AND hoist the whole run above the bulk.
    """
    depth = [dr.RefusedRead(item=f"/repo/node_modules/p{n}/i.js",
                            obligation=sp.OBLIGATION_ENUMERATION_BOUND,
                            reason=f"enumeration depth 13 exceeds bound "
                                   f"MAX_DEPTH={sp.MAX_DEPTH}")
             for n in range(4)]
    result = _result(*depth)

    lines = result.refusal_lines
    assert len(lines) == 1, (
        f"depth refusals stopped merging — the exclusion is probably written "
        f"over the obligation instead of the reason: {lines}")
    assert "4 items" in lines[0]


def test_two_item_ceiling_notices_never_merge():
    """The other branch of the same obligation. A second one is reachable on a
    multi-kind declaration, and merging them would sink both."""
    ceiling = [dr.RefusedRead(item=f"/repo{n}/f.md",
                              obligation=sp.OBLIGATION_ENUMERATION_BOUND,
                              reason=f"enumeration item-count bound "
                                     f"MAX_ITEMS={sp.MAX_ITEMS} reached; this item "
                                     f"and any after it were not read")
               for n in range(2)]
    lines = _result(*ceiling).refusal_lines
    assert len(lines) == 2, f"the two ceiling notices merged: {lines}"


# --------------------------------------------------------------------------- #
# Ordering.
# --------------------------------------------------------------------------- #

def test_unmerged_entries_render_above_merged_groups():
    """Position, not just volume. Merging alone cuts lines without cutting text,
    so the entry a person must act on has to sit above the bulk rather than
    somewhere inside it."""
    authorization = dr.RefusedRead(
        item="every declared linear source",
        obligation=sp.OBLIGATION_READABLE,
        reason="the declaration names a Linear source but this run holds no "
               "Linear authorization; connect Linear and run this again")
    # planted AFTER the group members, so passing cannot be an artifact of order
    result = _result(_decode_refusal("/repo/a.pyc"),
                     _decode_refusal("/repo/b.pyc"),
                     authorization)

    lines = result.refusal_lines
    assert "Linear authorization" in lines[0], (
        f"the decision-needing entry did not lead: {lines}")
    assert "2 items" in lines[1]


def test_ordering_leaves_an_all_singleton_run_untouched():
    """The control. With nothing to merge the partition must reproduce the
    original order exactly — this is what keeps a clean repository's report
    byte-identical."""
    ones = (
        _decode_refusal("/repo/asset.bin"),
        dr.RefusedRead(item="/repo/blank.py",
                       obligation=sp.OBLIGATION_READABLE,
                       reason="the file holds no content, so a citation to it "
                              "would name a source with nothing behind it"),
        dr.RefusedRead(item="/repo/huge.md",
                       obligation=sp.OBLIGATION_READ_BUDGET,
                       reason="item is 115669 bytes, over the per-item budget "
                              "MAX_ITEM_BYTES=65536; it is refused whole"),
    )
    result = _result(*ones)
    assert result.refusal_lines == tuple(r.render() for r in ones)


# --------------------------------------------------------------------------- #
# The record the presentation is derived from.
# --------------------------------------------------------------------------- #

def test_grouping_does_not_touch_the_per_item_record():
    """`--json` serializes `result.refusals` directly, so the structured output
    stays per-item only as long as grouping never reaches back into it."""
    refusals = tuple(_decode_refusal(f"/repo/{n}.pyc") for n in range(3))
    result = _result(*refusals)

    assert len(result.refusal_lines) == 1
    assert result.refusals == refusals, "the per-item record was mutated"

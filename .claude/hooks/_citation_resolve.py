#!/usr/bin/env python3
"""Internal-citation resolution — shared pure domain module (S13 / design-A10).

A research report can cite a person's own code, notes, tracker and document
folders. `_citation_reconcile.py` keeps a *web* citation's trust label honest
against reachability; this module answers the same question for an *internal*
citation: does the thing it addresses still resolve?

Design-A10 asks that "an unresolvable path, purged revision or stale locator is
classified as a dead citation by the same broken-link accounting that handles a
dead URL". This module supplies only the CLASSIFICATION. Tagging, counting and
label-downgrading stay with `research_linkcheck.py`, which already owns them —
so there is one broken-link answer, not two that can disagree.

WHY THE OUTCOME IS FOUR-VALUED AND NOT A BOOLEAN
------------------------------------------------
Measured over the live corpus on 2026-09-09 (263 `_RESEARCH*`/`_CLAIMS*` files,
5898 citation markers, 10 of them internal): a check that simply asks "does this
path resolve from the workspace root?" reports **6 dead where 1 is dead**. The
other five are not dead — they were written relative to their project folder
rather than the workspace root the Marker Contract's Edge 1 names, so they
resolve perfectly under a different root. Telling a person their sources are
gone when the sources are fine would be worse than the silence it replaces.

So resolution is a four-valued enum, and only ONE of the four is broken:

  live       — the target resolves under the root the citation convention names.
  misrooted  — the target EXISTS, but under a different root than the convention
               names (typically the citing file's own project folder). Reported,
               never counted as broken, never downgraded: the source is there.
  dead       — the target does not resolve under any root tried. THE ONLY value
               that counts as a broken link.
  unresolved — the check could not run (no credentials, no checkout, grammar
               unavailable). Reported, never counted as broken, never annotated
               in the body.

`unresolved` is load-bearing. An unrunnable check that reads as a passed check is
the exact failure this slice exists to remove — so where resolution is impossible
the outcome says so rather than defaulting to either answer. This mirrors
`ParsedCitation.well_formed`'s tri-state and `_citation_reconcile`'s
"never downgrade on unknown" rule.

DOMAIN LAYER — NO I/O
---------------------
Deterministic logic only (`code_first_architecture.md` Domain Layer: "No AI, no
external calls, no I/O"). Existence is INJECTED by the caller as a predicate, the
same contract `_citation_reconcile.reconcile_citation_labels` uses for
reachability. That is what keeps the metadata restriction structural rather than
remembered: this module cannot read a cited file's CONTENT even by accident,
because it cannot read at all. Reading content would be a source read, and the
architecture does not enforce containment on reads the flow performs (spine Q13,
design-A18 superseded).

Contract:
  resolve_citation(citation, *, exists, workspace_root, citing_file,
                   checkout_for=None) -> Resolution

  `citation`      a `ParsedCitation` from `_factcheck_engine.parse_citations`.
                  Never re-parsed here — the single-classifier rule (H2).
  `exists`        callable `str -> bool`. The ONLY I/O seam.
  `workspace_root` the root the Marker Contract names (the Projects root).
  `citing_file`   absolute path of the report holding the citation; its ancestors
                  are the alternative roots a `misrooted` target is sought under.
  `checkout_for`  optional callable `repo_identity -> root|None` for `code`
                  citations. Absent, or returning None, yields `unresolved`.

WHAT THIS SLICE DELIBERATELY DID NOT DO (S13, six recorded non-actions)
----------------------------------------------------------------------
Each carries a measurement or a named blocking reason — never a bare
"out of scope". Recorded here because this is where a later reader meets them.

1. **No per-source-kind rate** (design-A15, as specified). The OMTM's unit is one
   marker per research file while source selection is multi-select, so a per-kind
   rate has no well-defined denominator. Delivered as a TEST
   (``test_a5_no_per_source_kind_rate_is_exposed``) rather than a sentence, so
   the non-action is enforced rather than documented.

2. **The FACT-CHECK filter is not widened; the LINK-CHECK filter is.** Measured
   2026-09-09: 86 of 227 ``_RESEARCH*.md`` files sat outside every filter (40
   ``Personal/``, 39 ``[Author Name] Profile/``, 4 ``[YourProject]/``, 3 at the
   Projects root). Link-check is a non-blocking PostToolUse doing bounded local
   work, so it was widened; fact-check dispatches real AI checker rounds, so
   widening it would put 86 more files each through checker runs — a real
   behaviour change outside a metric slice. **Honest consequence:** the
   marker-population half stays limited to the already-covered set, while the
   dead-citation half gains full reach.

3. **The Edge-4 section-anchor citation form is not made resolvable.** Re-measured
   2026-09-09 (not inherited): **0 instances**. Fixing it needs either an optional
   ``line`` part or an A17 three-loci vocabulary edit, both of which belong to the
   vocabulary owner. The zero is now PINNED
   (``test_a6_section_anchor_citations_are_still_zero_instance``), so the day the
   corpus gains one the suite says so.

4. **The broken-link escape RATE is not built.** Measured: ``broken_count`` is
   written per file and **no code reads it as a rate** — unlike the OMTM, which
   has a rate reader and a CLI. S13 makes internal citations *enter* that
   counting, which is what design-A10 asks; building the missing reader is not
   internal-source-specific.

5. **The marker-verdict enum is NOT changed.** Code writes ``INCOMPLETE`` while
   ``factcheck-convergence.md`` §7 declares the enum without it. S13 does not
   touch this: A10 puts a dead internal citation in the same broken-link
   accounting, and that accounting **tags, downgrades and counts — it does not
   fold the run verdict**. So this slice emits no new verdict value and the enum
   needs no edit. The divergence is real, pre-existing, and left standing
   deliberately rather than widened from a slice that does not write the value.

6. **The run-level degradation roll-up is declined**, with its reason recorded at
   ``skills/research/source_port.py`` (which no longer assigns it to S13).
   ``list_degradations`` is run-scoped rather than cross-run, and 143 of 251
   reachable files still share one admission store — so a roll-up built on the
   current addressing would return another topic's degradations.

Standalone / unit-testable:  python3 _citation_resolve.py --self-test
"""

from __future__ import annotations

import os
from typing import NamedTuple, Optional

# The four outcomes. Exactly one of them is broken.
LIVE = "live"
MISROOTED = "misrooted"
DEAD = "dead"
UNRESOLVED = "unresolved"

OUTCOMES = (LIVE, MISROOTED, DEAD, UNRESOLVED)

#: The outcomes that count toward the broken-link accounting. A frozenset of one,
#: written as a set so the "only `dead` is broken" rule has a single named home
#: rather than being spelled as an equality test at each call site.
BROKEN_OUTCOMES = frozenset({DEAD})

#: Locator classes this module resolves. `url` is deliberately absent — a web
#: citation's reachability is `_citation_reconcile`'s, via HEAD.
_PATH_LOCATORS = ("local-file", "topic-CLAUDE")


class Resolution(NamedTuple):
    """One internal citation, resolved to exactly one outcome.

    `resolved_root` is set only for `misrooted`, and names the root under which
    the target actually resolves — the annotation A3 renders needs to say where
    the file really is, or a person cannot act on the finding.
    """

    outcome: str                    # one of OUTCOMES
    reason: str                     # why, in words, always populated
    locator: Optional[str] = None   # the citation's locator class
    target: Optional[str] = None    # the address that was resolved
    resolved_root: Optional[str] = None   # misrooted only

    @property
    def is_broken(self):
        """True iff this outcome counts in the broken-link accounting."""
        return self.outcome in BROKEN_OUTCOMES


def _target_path(citation):
    """The path portion of a path-shaped citation payload.

    Prefers the grammar's parsed `path` part. Falls back to stripping the known
    prefix off the raw payload, because a payload with no `:line` part does not
    parse — six of the corpus's ten internal citations are exactly that shape —
    and refusing to resolve them would reproduce the blindness this slice removes.
    Returns None when no path can be recovered at all.
    """
    part = (citation.parts or {}).get("path")
    if part:
        return part
    raw = (citation.raw or "").strip()
    for prefix in ("local-file:", "topic-CLAUDE:"):
        if raw.startswith(prefix):
            candidate = raw[len(prefix):].strip()
            # Drop a trailing `:<line>` / `:<a>-<b>` if one is present but the
            # payload failed to parse for some other reason.
            return candidate or None
    return None


def _expand(target):
    """Expand a leading `~`. Never touches anything else.

    Expanding can only turn a false `dead` into a `live`, so it is the safe
    direction. The corpus's one genuinely-dead citation is home-relative and
    stays dead under expansion — verified 2026-09-09.
    """
    if target.startswith("~"):
        return os.path.expanduser(target)
    return target


def _candidate_roots(workspace_root, citing_file):
    """The roots a target is sought under, in order.

    Workspace root FIRST, because that is the root the Marker Contract's Edge 1
    names as correct — so a target that resolves there is `live`, and one that
    resolves only elsewhere is `misrooted` against a written convention rather
    than against a guess.

    Then the citing file's ancestors, nearest first, up to and including the
    workspace root. Nearest-first matters: it names the most specific root that
    explains the citation, which is the one a person would recognise as "the
    project folder I wrote this relative to".
    """
    roots = [str(workspace_root)]
    ws = os.path.abspath(str(workspace_root))
    cur = os.path.dirname(os.path.abspath(str(citing_file)))
    seen = {ws}
    while True:
        if cur not in seen:
            seen.add(cur)
            roots.append(cur)
        if cur == ws or os.path.dirname(cur) == cur:
            break
        cur = os.path.dirname(cur)
    return roots


def resolve_citation(citation, *, exists, workspace_root, citing_file,
                     checkout_for=None):
    """Resolve ONE parsed internal citation to exactly one outcome.

    See the module docstring for the contract. Never returns `dead` for a check
    that could not run — that is the module's central guard rail, and every early
    return below honours it.
    """
    locator = getattr(citation, "locator", None)

    # A web citation is not this module's business; `_citation_reconcile` owns it.
    if not getattr(citation, "is_internal", False):
        return Resolution(UNRESOLVED, "not an internal citation", locator)

    # The grammar would not load, so well-formedness could not be checked. S12
    # makes that `None`; it must map to `unresolved`, never to `dead`.
    if getattr(citation, "well_formed", True) is None:
        return Resolution(
            UNRESOLVED,
            "locator grammar unavailable — the citation could not be read as an "
            "address, so its target was not looked for",
            locator,
        )

    # A tracker issue needs credentials this checker does not hold. Always
    # unresolved — never dead, and never silently passed.
    if locator == "linear":
        return Resolution(
            UNRESOLVED,
            "tracker issues need credentials the citation checker does not hold",
            locator,
            target=(citation.parts or {}).get("issue"),
        )

    if locator == "code":
        return _resolve_code(citation, exists, checkout_for)

    if locator in _PATH_LOCATORS:
        return _resolve_path(citation, exists, workspace_root, citing_file)

    return Resolution(
        UNRESOLVED,
        f"no resolution rule for locator class {locator!r}",
        locator,
    )


def _resolve_code(citation, exists, checkout_for):
    """Resolve a `code:<repo>@<rev>:<path>:<lines>` citation.

    A `code` pin names a repository IDENTITY, not a path (`code_base.py:100-113`),
    so resolving it needs a caller-supplied identity->checkout mapping. Without
    one — or with one that does not know this repository — the outcome is
    `unresolved`.

    Even WITH a checkout, a missing path yields `unresolved` rather than `dead`.
    Existence alone cannot separate "the revision was purged" from "this checkout
    is at a different revision" or "the file moved since", and telling those apart
    needs git object access this module deliberately does not perform. The arm has
    zero corpus instances, so it is specified in the safe direction: it can report
    a source is fine, never that a source is gone.
    """
    parts = citation.parts or {}
    repo = parts.get("repo")
    path = parts.get("path")
    if not repo or not path:
        return Resolution(
            UNRESOLVED, "code citation names no repository or no path", "code")

    root = None
    if callable(checkout_for):
        try:
            root = checkout_for(repo)
        except Exception:                       # noqa: BLE001 — never raise
            root = None
    if not root:
        return Resolution(
            UNRESOLVED,
            f"repository identity {repo!r} maps to no local checkout",
            "code", target=path)

    candidate = os.path.join(str(root), path)
    if _exists(exists, candidate):
        return Resolution(LIVE, f"resolves in checkout of {repo!r}", "code",
                          target=candidate)
    return Resolution(
        UNRESOLVED,
        f"path not present in the checkout of {repo!r}; existence alone cannot "
        f"distinguish a purged revision from a checkout at another revision",
        "code", target=path)


def _resolve_path(citation, exists, workspace_root, citing_file):
    """Resolve a `local-file:` / `topic-CLAUDE:` citation against the roots."""
    target = _target_path(citation)
    if not target:
        return Resolution(
            UNRESOLVED, "citation carries no readable path", citation.locator)

    expanded = _expand(target)

    # An absolute path addresses itself. Marker Contract Edge 1 permits it for a
    # source that resolves OUTSIDE the workspace, where it is the only expression
    # the source has — so there is no alternative root to try, and the answer is
    # two-valued for this shape alone.
    if os.path.isabs(expanded):
        if _exists(exists, expanded):
            return Resolution(LIVE, "absolute path resolves", citation.locator,
                              target=expanded)
        return Resolution(DEAD, "absolute path does not resolve",
                          citation.locator, target=expanded)

    roots = _candidate_roots(workspace_root, citing_file)
    for i, root in enumerate(roots):
        candidate = os.path.join(root, expanded)
        if _exists(exists, candidate):
            if i == 0:
                return Resolution(LIVE, "resolves under the workspace root",
                                  citation.locator, target=candidate)
            return Resolution(
                MISROOTED,
                "target exists, but under a root other than the one the citation "
                "convention names",
                citation.locator, target=expanded, resolved_root=root)
    return Resolution(
        DEAD, "target resolves under no root tried", citation.locator,
        target=expanded)


def _exists(exists, path):
    """Call the injected predicate defensively. A raising predicate is not an
    existence answer, so it reads as absent rather than propagating."""
    try:
        return bool(exists(path))
    except Exception:                           # noqa: BLE001
        return False


def resolve_all(citations, *, exists, workspace_root, citing_file,
                checkout_for=None):
    """Resolve every INTERNAL citation in `citations`.

    Returns a list of `(citation, Resolution)` in report order. Web citations are
    skipped entirely rather than returned as `unresolved` noise — the caller asked
    for the internal ones.
    """
    out = []
    for c in citations or []:
        if not getattr(c, "is_internal", False):
            continue
        out.append((c, resolve_citation(
            c, exists=exists, workspace_root=workspace_root,
            citing_file=citing_file, checkout_for=checkout_for)))
    return out


def counts(resolutions):
    """Tally a sequence of `Resolution` (or `(citation, Resolution)`) by outcome.

    Always returns all four keys, so a zero is stated rather than absent — an
    absent key and a zero read differently to a later reader, and this slice
    exists because a silent zero was mistaken for a clean result.
    """
    tally = {o: 0 for o in OUTCOMES}
    for item in resolutions or []:
        # `Resolution` IS a tuple (NamedTuple), so isinstance(item, tuple) cannot
        # tell a bare Resolution from a (citation, Resolution) pair. Discriminate
        # on the attribute that only a Resolution carries.
        res = item if hasattr(item, "outcome") else item[1]
        if res.outcome in tally:
            tally[res.outcome] += 1
    return tally


# --------------------------------------------------------------------------- #
# Self-test (no pytest needed): python3 _citation_resolve.py --self-test
# --------------------------------------------------------------------------- #
def _self_test():
    class _Cit(NamedTuple):
        locator: str
        source_class: str
        raw: str
        parts: dict
        well_formed: Optional[bool] = True

        @property
        def is_internal(self):
            return self.source_class == "internal"

    WS = "/ws"
    CITING = "/ws/Proj/Docs/a_RESEARCH.md"
    PRESENT = {
        "/ws/Thoughts/x.md",                 # under the workspace root
        "/ws/Proj/notes/y.md",               # under the citing file's project
        "/abs/there.md",                     # absolute, present
        "/checkout/src/z.py",                # code checkout
    }

    def exists(p):
        return os.path.normpath(p) in PRESENT

    def R(cit, **kw):
        return resolve_citation(cit, exists=exists, workspace_root=WS,
                                citing_file=CITING, **kw)

    # 1. live — resolves under the workspace root.
    live = _Cit("local-file", "internal", "local-file:Thoughts/x.md:5",
                {"path": "Thoughts/x.md", "line": "5"})
    r = R(live)
    assert r.outcome == LIVE, r
    assert not r.is_broken

    # 2. misrooted — resolves under the citing file's project, not the root.
    mis = _Cit("local-file", "internal", "local-file:notes/y.md:1",
               {"path": "notes/y.md", "line": "1"})
    r = R(mis)
    assert r.outcome == MISROOTED, r
    assert r.resolved_root == "/ws/Proj", r
    assert not r.is_broken, "misrooted must NEVER count as broken"

    # 3. dead — resolves nowhere. The ONLY broken outcome.
    dead = _Cit("local-file", "internal", "local-file:gone/nope.md:1",
                {"path": "gone/nope.md", "line": "1"})
    r = R(dead)
    assert r.outcome == DEAD, r
    assert r.is_broken

    # 4. a payload with NO line part still resolves (6 of the corpus's 10).
    noline = _Cit("local-file", "internal", "local-file:notes/y.md", {},
                  well_formed=False)
    assert R(noline).outcome == MISROOTED, R(noline)

    # 5. linear is ALWAYS unresolved — never dead.
    lin = _Cit("linear", "internal", "linear:ws@v1:ISS-1",
               {"workspace": "ws", "version": "v1", "issue": "ISS-1"})
    r = R(lin)
    assert r.outcome == UNRESOLVED and not r.is_broken, r

    # 6. code with no checkout mapping → unresolved, never dead.
    code = _Cit("code", "internal", "code:repo@abc:src/z.py:1-2",
                {"repo": "repo", "rev": "abc", "path": "src/z.py",
                 "lines": "1-2"})
    assert R(code).outcome == UNRESOLVED, R(code)
    # ...and with a checkout that has the file → live.
    assert R(code, checkout_for=lambda r_: "/checkout").outcome == LIVE
    # ...and with a checkout that does NOT → still unresolved, NOT dead.
    r = R(code, checkout_for=lambda r_: "/elsewhere")
    assert r.outcome == UNRESOLVED and not r.is_broken, r

    # 7. grammar unavailable (well_formed None) → unresolved, never dead.
    unk = _Cit("local-file", "internal", "local-file:gone/nope.md:1",
               {"path": "gone/nope.md"}, well_formed=None)
    r = R(unk)
    assert r.outcome == UNRESOLVED and not r.is_broken, r

    # 8. absolute paths: present → live, absent → dead.
    absent_abs = _Cit("local-file", "internal", "local-file:/abs/gone.md:1",
                      {"path": "/abs/gone.md", "line": "1"})
    present_abs = _Cit("local-file", "internal", "local-file:/abs/there.md:1",
                       {"path": "/abs/there.md", "line": "1"})
    assert R(present_abs).outcome == LIVE
    assert R(absent_abs).outcome == DEAD

    # 9. a web citation is not this module's business.
    web = _Cit("url", "web", "https://x.example/y", {"url": "https://x.example/y"})
    assert R(web).outcome == UNRESOLVED

    # 10. a raising existence predicate reads as absent, never as a crash.
    def boom(_p):
        raise RuntimeError("nope")
    r = resolve_citation(live, exists=boom, workspace_root=WS,
                         citing_file=CITING)
    assert r.outcome == DEAD, r

    # 11. paths with spaces resolve (the real corpus has `Onboarding v2/`).
    PRESENT.add("/ws/Proj/Onboarding v2/t.md")
    sp = _Cit("local-file", "internal", "local-file:Onboarding v2/t.md", {},
              well_formed=False)
    assert R(sp).outcome == MISROOTED, R(sp)

    # 12. counts() always reports all four keys, zeros included.
    t = counts([R(live), R(mis), R(dead)])
    assert t == {LIVE: 1, MISROOTED: 1, DEAD: 1, UNRESOLVED: 0}, t

    # 13. resolve_all skips web citations rather than reporting them.
    pairs = resolve_all([live, web, dead], exists=exists, workspace_root=WS,
                        citing_file=CITING)
    assert len(pairs) == 2, pairs

    print("citation-resolve self-test: OK (13 checks)")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print(__doc__)

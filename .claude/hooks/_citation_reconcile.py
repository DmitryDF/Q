#!/usr/bin/env python3
"""Citation-label reconciliation — shared pure domain module.

Keeps a research report's body trust-labels honest with respect to source
reachability. A `[stated — URL]` marker asserts "verbatim quote from a page I
read"; a `[paraphrased — URL]` marker asserts "my summary of that source". If
the source URL did not actually load, that assertion is unproven, so the label
must be downgraded to `[unverified — source unreachable at fetch — URL]`.

This module is DETERMINISTIC domain logic (code_first_architecture.md Domain
Layer — "if the answer is deterministic, it belongs here; no AI"). It performs
no I/O: reachability is INJECTED by the caller. Two callers share it:
  * research_linkcheck.py — the synchronous save-time link-checker (primary,
    HEAD-based; co-locates the downgrade with its `⚠ BROKEN` write).
  * _factcheck_engine.py — the async source-integrity gate (defense-in-depth,
    richer GET-based disposition status).

Contract:
  reconcile_citation_labels(text, is_reachable) -> (new_text, downgraded)

  `is_reachable` is either a callable `url -> (True|False|None)` or a mapping
  `{url: True|False|None}`:
    * True   → source loaded; keep the label untouched.
    * False  → source did NOT load; downgrade the label.
    * None   → unknown (e.g. not checked / budget exhausted); keep untouched
               (never downgrade on unknown — Plan guard rail).

  `downgraded` is a list of {"url", "from"} for each marker actually rewritten.

Properties:
  * Downgrade-only. There is no upgrade path — a `[unverified …]` marker is
    never restored to `[stated]` (re-asserting a verbatim quote is the
    producer's job, not the gate's).
  * Idempotent. `_TRUST_TAG_RE` matches only `stated|paraphrased`, so a
    rewritten `[unverified …]` marker can never re-match; re-runs are no-ops.
  * Disjoint from markdown links. The regex requires `[stated|paraphrased —
    URL]` with no `](`, so it never touches a `[text](url)` markdown link
    (which research_linkcheck's own LINK_RE owns) or the `[inferred …]` /
    `[My assessment …]` / `[unverified …]` markers.

Standalone / unit-testable:  python3 _citation_reconcile.py --self-test
"""

from __future__ import annotations

import re

# Matches a trust-tag citation marker: `[stated — URL]` / `[paraphrased — URL]`.
# The separator is an em-dash (U+2014) in the marker contract, but we tolerate
# en-dash (U+2013) and ASCII hyphen so a hand-typed near-miss is still caught.
# The URL runs to the closing `]` (no whitespace, no `]`). No `](` → never
# matches a markdown link.
_TRUST_TAG_RE = re.compile(
    r"\[(stated|paraphrased)\s*[—–-]\s*(https?://[^\]\s]+)\]"
)

# The canonical downgrade target (em-dash separators, URL preserved for audit).
_DOWNGRADE_FMT = "[unverified — source unreachable at fetch — {url}]"


def _resolve_reachable(is_reachable, url):
    """Return True / False / None for `url` from a callable or a mapping."""
    if callable(is_reachable):
        try:
            return is_reachable(url)
        except Exception:
            return None
    get = getattr(is_reachable, "get", None)
    if callable(get):
        return is_reachable.get(url)
    return None


def reconcile_citation_labels(text, is_reachable):
    """Downgrade `[stated|paraphrased — URL]` markers whose URL is unreachable.

    See module docstring for the contract. Returns (new_text, downgraded).
    """
    if not text:
        return text, []
    downgraded = []

    def _repl(m):
        tag = m.group(1)
        url = m.group(2)
        reachable = _resolve_reachable(is_reachable, url)
        if reachable is False:  # ONLY a definite failure downgrades (not None)
            downgraded.append({"url": url, "from": tag})
            return _DOWNGRADE_FMT.format(url=url)
        return m.group(0)

    new_text = _TRUST_TAG_RE.sub(_repl, text)
    return new_text, downgraded


# --------------------------------------------------------------------------- #
# Self-test (no pytest needed): python3 _citation_reconcile.py --self-test
# --------------------------------------------------------------------------- #
def _self_test():
    U_DEAD = "https://dead.example/x"
    U_LIVE = "https://live.example/y"

    def reach(url):
        return {U_DEAD: False, U_LIVE: True}.get(url)

    # 1. stated + unreachable → downgraded; live stated → untouched.
    t = f"A [stated — {U_DEAD}] and B [stated — {U_LIVE}]."
    out, dg = reconcile_citation_labels(t, reach)
    assert _DOWNGRADE_FMT.format(url=U_DEAD) in out, out
    assert f"[stated — {U_LIVE}]" in out, out
    assert dg == [{"url": U_DEAD, "from": "stated"}], dg

    # 2. paraphrased downgrades too.
    out2, dg2 = reconcile_citation_labels(f"[paraphrased — {U_DEAD}]", reach)
    assert out2 == _DOWNGRADE_FMT.format(url=U_DEAD), out2
    assert dg2[0]["from"] == "paraphrased"

    # 3. idempotent: re-running over the downgraded text is a no-op.
    out3, dg3 = reconcile_citation_labels(out, reach)
    assert out3 == out, "not idempotent"
    assert dg3 == [], dg3

    # 4. other markers untouched.
    other = ("[inferred from sources] [My assessment: x] "
             f"[unverified — no source] [text]({U_DEAD})")
    out4, dg4 = reconcile_citation_labels(other, reach)
    assert out4 == other, out4
    assert dg4 == [], dg4

    # 5. unknown (None) never downgrades.
    out5, dg5 = reconcile_citation_labels(
        f"[stated — {U_DEAD}]", lambda u: None)
    assert out5 == f"[stated — {U_DEAD}]", out5
    assert dg5 == [], dg5

    # 6. dash tolerance: en-dash and hyphen forms still match.
    for dash in ("–", "-"):
        s = f"[stated {dash} {U_DEAD}]"
        o, d = reconcile_citation_labels(s, reach)
        assert o == _DOWNGRADE_FMT.format(url=U_DEAD), (dash, o)
        assert d and d[0]["url"] == U_DEAD

    # 7. multi-occurrence of the same URL: all downgraded.
    multi = f"[stated — {U_DEAD}] x [paraphrased — {U_DEAD}]"
    o7, d7 = reconcile_citation_labels(multi, reach)
    assert o7.count("[unverified") == 2, o7
    assert len(d7) == 2, d7

    # 8. mapping (dict) reachability works like a callable.
    o8, d8 = reconcile_citation_labels(
        f"[stated — {U_DEAD}]", {U_DEAD: False})
    assert o8 == _DOWNGRADE_FMT.format(url=U_DEAD), o8

    print("citation-reconcile self-test: OK (8 checks)")


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print(__doc__)

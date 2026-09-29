#!/usr/bin/env python3
"""Evidence predicate for a framed TODO item — does it point at anything?

WHAT THIS DECIDES, AND WHAT IT DELIBERATELY DOES NOT

`cmd_validate_framing` tests that four LABELS are present. It never tests that
they carry content, and nothing tests that the described problem exists — so an
item filed without opening the code is indistinguishable from one filed after
opening it. This module adds the one thing code can decide from the text alone:
**does the item carry a pointer at all.**

It does NOT decide whether the pointer is right, whether it resolves, or whether
the problem is real. Those are not text-decidable, and a gate that blocked on
them would be this codebase's own diagnosed convention in a new place — a cheap
proxy standing in for an expensive property.

BLOCK vs WARN — the split is measured, not chosen for taste

Blocking on the ABSENCE of a pointer is honest: absence is decidable from the
text. Blocking on a pointer that fails to RESOLVE is not: of 46 framed lines in
this corpus citing `path:line`, 41 name files in another repo or by an ambiguous
basename. A resolve-gate would refuse the majority of correctly-framed items.
So a failed resolution WARNS and never blocks.

THE CEILING, STATED PLAINLY SO IT IS NOT LATER OVERSTATED

A `[[wikilink]]` counts as a pointer, and a framed line's `Master plan:` field is
very often a wikilink. So for most items this predicate is satisfied by the
Master-plan reference alone, and what it actually rules out is the item that
points at NOTHING WHATEVER — roughly 11% of the current corpus. That is the
floor it was scoped to raise, and it is a floor, not a guarantee. Do not describe
this as ensuring an item describes a real problem: it ensures the item carries
somewhere to look.

THE EXCEPTION IS VISIBLE ON PURPOSE

Build-new work has nothing to point at yet and must stay fileable. The escape is
the literal token `[build-new]` written INTO the item text, so it stays on the
list where its overuse is legible — never a flag that vanishes after the call.
"""

from __future__ import annotations

import os
import re

# The literal exception token. Greppable, reads as deliberate on the list, and
# documented verbatim in work-frame-and-create-todo/SKILL.md. The two MUST stay
# identical — a documented form the code rejects is the same class of defect
# this module exists to reduce.
EXCEPTION_TOKEN = "[build-new]"

# `path/to/thing.ext:123` — a file with a line number. The extension keeps a
# bare `word:123` (e.g. a time, a ratio) from reading as a citation.
PATH_LINE_RE = re.compile(
    r"\b[\w./~-]+\.[A-Za-z0-9]{1,8}:\d+(?:-\d+)?\b")

# A backticked token that looks like a file or a dotted module path.
BACKTICK_FILE_RE = re.compile(
    r"`[^`\n]*?[\w/-]+\.[A-Za-z0-9]{1,8}[^`\n]*?`")

WIKILINK_RE = re.compile(r"\[\[[^\]\n]+\]\]")

URL_RE = re.compile(r"\bhttps?://\S+")

# An UNBACKTICKED bare path — recognised, but only as a WARNING-grade signal.
# The corpus holds at least one item whose only pointer is spelled this way, so
# treating it as nothing would refuse a correctly-framed line; treating it as a
# full pointer would let ordinary prose containing a dotted word satisfy the
# check. It is reported so a caller can see it, and it does NOT satisfy the gate.
BARE_PATH_RE = re.compile(r"(?<![`\[\w/])\b[\w-]+/[\w./-]+\.[A-Za-z0-9]{1,8}\b")


def has_exception(text: str) -> bool:
    """Is the visible build-new exception present in the item text?"""
    return EXCEPTION_TOKEN in (text or "")


def find_pointers(text: str) -> list:
    """Every gate-satisfying pointer in the text, as {kind, value} dicts."""
    text = text or ""
    found = []
    for kind, rx in (("path-line", PATH_LINE_RE),
                     ("backticked-file", BACKTICK_FILE_RE),
                     ("wikilink", WIKILINK_RE),
                     ("url", URL_RE)):
        for m in rx.finditer(text):
            found.append({"kind": kind, "value": m.group(0)})
    return found


def find_weak_pointers(text: str) -> list:
    """Pointer-shaped text that does NOT satisfy the gate (bare unbacked paths).
    Reported so a refusal can say 'this looks like a path — backtick it'."""
    text = text or ""
    return [{"kind": "bare-path", "value": m.group(0)}
            for m in BARE_PATH_RE.finditer(text)]


def _resolve_candidate(value: str, root: str):
    """True/False if the pointer resolves under `root`; None if not checkable."""
    if not root:
        return None
    path = value.strip("`").split(":", 1)[0].strip()
    if not path or path.startswith(("http://", "https://")):
        return None
    if os.path.isabs(path) or path.startswith("~"):
        return os.path.exists(os.path.expanduser(path))
    return os.path.exists(os.path.join(root, path))


def evaluate(text: str, root: str = None) -> dict:
    """The predicate. Plain str in, plain dict out — no harness types.

    Returns {status: pass|fail, errors: [...], warnings: [...],
             pointers: [...], exception: bool}.

    `root` enables the non-blocking resolution warning; omit it to skip that.
    """
    text = text or ""
    exception = has_exception(text)
    pointers = find_pointers(text)
    weak = find_weak_pointers(text)

    warnings = []
    if root and pointers:
        # Deduplicate by the PATH the pointer names, not by the matched text: a
        # backticked `foo.py:12` matches both the path-line and backticked-file
        # patterns, and warning twice about one citation is noise that trains a
        # reader to skip warnings.
        seen = set()
        for p in pointers:
            if p["kind"] not in ("path-line", "backticked-file"):
                continue
            key = p["value"].strip("`").split(":", 1)[0].strip()
            if key in seen:
                continue
            seen.add(key)
            if _resolve_candidate(p["value"], root) is False:
                warnings.append(
                    f"cited path does not resolve under {root}: {key} "
                    "(not blocking — many correct citations name another repo "
                    "or an ambiguous basename)")

    if pointers or exception:
        return {"status": "pass", "errors": [], "warnings": warnings,
                "pointers": pointers, "exception": exception}

    hint = ""
    if weak:
        hint = (f" This looks like a path — {weak[0]['value']} — but it is not "
                "backticked, so it is not counted. Wrap it in backticks or add "
                "a line number.")
    return {
        "status": "fail",
        "errors": [
            "No pointer to where the problem lives. Cite a `path:line`, a "
            "backticked file, a [[wikilink]], or a URL." + hint
            + f" If there is nothing to point at yet (build-new work), write "
              f"{EXCEPTION_TOKEN} in the item text to record that deliberately."
        ],
        "warnings": warnings,
        "pointers": [],
        "exception": False,
    }


def _self_test() -> int:
    """Both directions, plus the exception and the warn-never-block split."""
    results = []

    def case(name, ok, detail=""):
        results.append((name, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}"
              + (f" — {detail}" if detail else ""))

    base = "**T** — Problem: x. Context: y. Guiding policy: z. "

    r = evaluate(base + "Master plan: none.")
    case("(i) no pointer at all → fail", r["status"] == "fail", str(r["errors"])[:70])

    r = evaluate(base + "Master plan: [[some-plan]].")
    case("(ii) wikilink → pass", r["status"] == "pass")

    r = evaluate(base + "see todo.py:441. Master plan: none.")
    case("(iii) path:line → pass", r["status"] == "pass")

    r = evaluate(base + "see `run.py`. Master plan: none.")
    case("(iv) backticked file → pass", r["status"] == "pass")

    r = evaluate(base + "see https://example.com/x. Master plan: none.")
    case("(v) URL → pass", r["status"] == "pass")

    r = evaluate(base + "Master plan: none. " + EXCEPTION_TOKEN)
    case("(vi) explicit exception → pass", r["status"] == "pass"
         and r["exception"] is True)

    r = evaluate(base + "see hooks/todo.py. Master plan: none.")
    case("(vii) bare unbacked path → still fails, but says so",
         r["status"] == "fail" and "not backticked" in r["errors"][0],
         r["errors"][0][-60:])

    r = evaluate(base + "see `nonexistent_xyz.py:1`. Master plan: none.",
                 root="/tmp")
    case("(viii) unresolvable pointer → passes WITH a warning, never blocks",
         r["status"] == "pass" and len(r["warnings"]) == 1)

    r = evaluate(base + "Master plan: [[p]].", root="/tmp")
    case("(ix) wikilink is never resolution-checked",
         r["status"] == "pass" and not r["warnings"])

    passed = sum(1 for _, ok in results if ok)
    print(f"\n  {passed}/{len(results)} green")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys
    if "--self-test" in sys.argv:
        raise SystemExit(_self_test())
    print(__doc__)

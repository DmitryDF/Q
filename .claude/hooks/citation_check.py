#!/usr/bin/env python3
"""Section-citation validator — plan-gates-citation-enforcement A5.

Roughly two dozen skill and rules files cite NAMED PROSE SECTIONS inside
`~/.claude/rules/*.md`. Nothing checked that those citations resolve, so a
rename or a relocation broke them silently: no error, no warning, no failing
test, because nothing executes a citation. Four were broken before anyone
looked, and they were found by hand.

This module answers one question per citation: **does the thing it names still
exist?** A cited section resolves when it matches a markdown heading, a bold
run-in label, or an `<!-- anchor: slug -->` comment in the cited file. A cited
FILE resolves when it is present in the harness rules tree.

Standalone by design (`skill-location.md` — "check-logic MUST be kept as
standalone Python modules callable directly without the Claude hook harness"):
plain dict in, plain dict out, no Claude imports, `--self-test` runnable.
`check-citation-check.sh` is the thin per-vendor trigger adapter.

NOT related to `check-citation-marker-drift.sh`, `_citation_resolve.py` or
`_citation_reconcile.py` — those three concern RESEARCH PROVENANCE MARKERS
(`[stated — URL]`) from research-source-adapters. Different axis entirely; the
name collision is unfortunate and is called out here so the next reader does not
extend the wrong one.

Honest limits, stated rather than discovered later:
  * A PostToolUse hook fires only for edits made THROUGH the harness. A citation
    broken by an external editor, a git operation or a the deploy step lands
    silently and is caught at the next harness edit or corpus scan.
  * Section extraction is conservative. It recognises quoted, backticked and
    `Gate N`-shaped references plus verbatim target containment. Free prose that
    names a section in wholly unanticipated wording is NOT extracted, so it is
    neither credited nor failed — a floor on detection, never a guarantee.
  * Resolution proves a NAME still exists, not that the prose beneath it still
    says what the citer relied on.
"""

from __future__ import annotations

import json
import os
import re
import sys

HOME = os.path.expanduser("~")
RULES_DIR = os.path.join(HOME, ".claude", "rules")
SKILLS_DIR = os.path.join(HOME, ".claude", "skills")
DOCS_DIR = os.path.join(HOME, ".claude", "docs")
ACCEPTANCES = os.path.join(HOME, ".claude", "hooks", "citation-acceptances.json")

# --- what is NOT a citation of a rules document ------------------------------
# A0 measured 70 of 123 grep hits as noise; re-measured 2026-09-13 the corpus had
# grown and the noise with it. These three exclusions are what keep the validator
# off ~100 non-citations.
NOISE_SUBSTRINGS = (
    "check-plan-gates",   # the enforcing HOOK — a different file, correctly named
    "starter-kit",        # bundle machinery matching the literal bundle id
    ".bak-",              # timestamped backups carry stale copies of every citation
)

# Aliases: shorthand a citer legitimately uses that appears nowhere verbatim in
# the cited file. A0 found 11 such citations across 7 files; re-measure found 16
# across 9. A literal matcher would reject every one of them to catch 4 real
# breaks, so the operator chose alias-then-blocking over report-only.
# CLOSED MAP, not a heuristic: an unknown `Gate <n>` fails.
ALIASES = {
    "gate 0a": "0a: Diagnosis",
    "gate 0b": "0b: Desired Outcome",
    "gate 0b2": "0b2: Outcome Claims",
    "gate 0c": "0c: Gap Analysis",
    "gate 0d": "0d: Guiding Policy",
    "gate 0e": "0e: Coherent Actions",
    "gate 0f": "0f: Design Review",
    "gate 0": "Gate 0",
    "gate 1": "Gate 1",
    "gate 2": "Gate 2",
    "gate 3": "Gate 3",
    "gate 1t": "Gate 1T",
}


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def targets_in(text: str) -> set[str]:
    """Every citable name the cited document offers.

    Three kinds, per A5's assertion: a markdown heading, a bold run-in label, or
    an explicit `<!-- anchor: slug -->`. The anchors exist because the two
    most-cited targets are bold run-ins with no markdown anchor, matchable only
    on exact prose until A2 added them.
    """
    out: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"^#{1,6}\s+(.*?)\s*$", s)
        if m:
            out.add(m.group(1).strip())
        m = re.match(r"^<!--\s*anchor:\s*([A-Za-z0-9._-]+)\s*-->$", s)
        if m:
            out.add(m.group(1).strip())
        # Bold run-in: a paragraph opening with **Label.** or **Label** …
        m = re.match(r"^\*\*(.+?)\*\*", s)
        if m:
            label = m.group(1).strip().rstrip(".:")
            if label:
                out.add(label)
    return out


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def resolves(candidate: str, targets: set[str]) -> bool:
    """Does one cited name resolve against the cited document's targets?"""
    cand = candidate.strip().strip("\"'`").strip()
    if not cand:
        return False
    low = cand.lower()

    lowered = {t.lower() for t in targets}
    slugs = {_slugify(t) for t in targets}

    if low in lowered or _slugify(cand) in slugs:
        return True
    # A citation may name a heading by its distinctive half
    # ("Verification Source Registry" for "## Verification Source Registry").
    for t in lowered:
        if low and (low in t or t in low) and len(low) >= 8:
            return True
    def _alias_hit(key: str) -> bool:
        if key not in ALIASES:
            return False
        alias = ALIASES[key].lower()
        if alias in lowered or _slugify(alias) in slugs:
            return True
        return any(alias in t for t in lowered)

    if _alias_hit(low):
        return True

    # Compound shorthand: `Gate 0a/0d/0e` and `Gate 0/1/2/3` name several
    # sections at once. Split on the slash and require EVERY part to resolve —
    # a compound naming one real and one phantom section is still broken.
    m = re.match(r"^gate\s+(.+)$", low)
    if m and "/" in m.group(1):
        parts = [p.strip() for p in m.group(1).split("/") if p.strip()]
        if parts and all(_alias_hit(f"gate {p}") for p in parts):
            return True
    return False


# A reference shaped like a section name. Conservative on purpose — see limits.
_GATE_RE = re.compile(r"\bGate\s+\d+[A-Za-z]?(?:\s*/\s*\d+[a-z]?)*", re.I)
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{4,80})[\"”]")
_TICKED_RE = re.compile(r"`([^`]{4,80})`")


def extract_candidates(trailing: str, targets: set[str]) -> list[str]:
    """Pull section-name-shaped references out of the text after a filename.

    Returns only things that LOOK like a section reference. Prose that names no
    section yields an empty list — a filename-only citation, which is not a
    section citation and is therefore not a failure.
    """
    cands: list[str] = []

    for m in _GATE_RE.finditer(trailing):
        cands.append(m.group(0))

    for m in _QUOTED_RE.finditer(trailing):
        if _section_shaped(m.group(1)):
            cands.append(m.group(1))

    for m in _TICKED_RE.finditer(trailing):
        v = m.group(1)
        # Skip paths, URLs and code — they are not section names.
        if "/" in v or v.startswith(("~", ".", "http")) or v.endswith((".md", ".sh", ".py")):
            continue
        if _section_shaped(v):
            cands.append(v)

    # Verbatim containment of a known target counts as a resolved citation, so a
    # correct un-quoted reference like "Verification Source Registry" is credited
    # rather than silently ignored.
    for t in targets:
        if len(t) >= 12 and t.lower() in trailing.lower():
            cands.append(t)

    seen, out = set(), []
    for c in cands:
        k = c.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(c.strip())
    return out


def _load_acceptances(path: str = ACCEPTANCES) -> list[dict]:
    raw = _read(path)
    if not raw:
        return []
    try:
        return json.loads(raw).get("acceptances", [])
    except (ValueError, AttributeError):
        return []


def _accepted(citing_rel: str, cited_file: str, acceptances: list[dict]) -> bool:
    for a in acceptances:
        if (a.get("cited_file") == cited_file
                and a.get("citing_file") in (citing_rel, None)):
            return True
    return False


_CITED_RE = re.compile(r"([A-Za-z0-9._-]*\.md)\b")

# SCOPE, stated rather than implied. A0 surveyed the citations of ONE document —
# `plan-gates.md` — and found 53, of which 4 were broken; `pre-plan-gates.md`
# appears only as the cited-but-absent half of three of those breaks. This
# validator checks exactly that surveyed surface.
#
# Widening it to every rules document was tried first and produced 339 findings
# on a corpus with 4 known breaks, because sibling rules files quote each other's
# PROSE constantly (`"Code owns the flow."`, `"branch -d"`) and cite Thoughts
# spines, agent files and Knowledge-Library books that were never in the rules
# tree. A validator that red on day one is a validator that gets disabled — the
# Guiding Policy says so in as many words. Widening is a later, surveyed decision,
# not a default.
IN_SCOPE_CITED = {"plan-gates.md", "pre-plan-gates.md"}

# A quoted or backticked phrase is a SECTION NAME candidate only if it reads like
# one. Sibling rules files quote each other's sentences; those are quotations, not
# citations, and treating them as section references is what produced the noise.
def _section_shaped(s: str) -> bool:
    v = s.strip()
    if not v or len(v) < 4:
        return False
    if v.endswith((".", "!", "?")) and " " in v:   # a quoted sentence
        return False
    if any(ch in v for ch in "§<>|"):              # a marker or a table fragment
        return False
    if v.split()[0].lower() in {"and", "or", "the", "a", "an", "but", "when", "if"}:
        return False
    if re.search(r"\b(never|must|should|cannot|does not)\b", v, re.I):
        return False                                # reads as a rule, not a name
    return True


def check_text(citing_rel: str, text: str, rules_texts: dict[str, str],
               acceptances: list[dict]) -> list[dict]:
    """Check one file's citations. Pure: no I/O, injected corpus."""
    findings: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(n in line for n in NOISE_SUBSTRINGS):
            continue
        for m in _CITED_RE.finditer(line):
            cited = m.group(1)
            if cited not in IN_SCOPE_CITED:
                continue
            if cited == os.path.basename(citing_rel):
                continue
            if cited not in rules_texts:
                # Cited file is not in the harness rules tree. Must be decided.
                if _accepted(citing_rel, cited, acceptances):
                    continue
                findings.append({
                    "citing_file": citing_rel, "line": lineno,
                    "cited_file": cited, "section": None,
                    "kind": "cited-file-missing",
                    "detail": (f"{citing_rel}:{lineno} cites `{cited}`, which is not in "
                               f"~/.claude/rules/ and carries no recorded acceptance"),
                })
                continue

            targets = targets_in(rules_texts[cited])
            # The cited-section phrase is whatever follows the filename UP TO the
            # next filename on the same line. Without that boundary a line like
            #   `plan-gates.md` "A" — distinct from `prompt-engineering.md` "B"
            # attributes B to plan-gates.md and reports a break that is not one.
            trailing = line[m.end():]
            nxt = _CITED_RE.search(trailing)
            if nxt:
                trailing = trailing[:nxt.start()]
            for cand in extract_candidates(trailing, targets):
                if resolves(cand, targets):
                    continue
                if _accepted(citing_rel, cited, acceptances):
                    continue
                findings.append({
                    "citing_file": citing_rel, "line": lineno,
                    "cited_file": cited, "section": cand,
                    "kind": "section-unresolved",
                    "detail": (f"{citing_rel}:{lineno} cites `{cited}` section "
                               f"\"{cand}\", which resolves to no heading, bold "
                               f"run-in or anchor in that file"),
                })
    return findings


_AT_DOCS_RE = re.compile(r"(?<!`)@~?/?\S*\.claude/docs/\S+")


def check_docs_pointers(citing_rel: str, text: str) -> list[dict]:
    """Second assertion: a `~/.claude/docs/` pointer is backticked, never
    `@`-prefixed. An `@` re-imports the target into the always-loaded set, which
    would silently undo a relocation done to get content OUT of that set."""
    out = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _AT_DOCS_RE.finditer(line):
            out.append({
                "citing_file": citing_rel, "line": lineno,
                "cited_file": None, "section": None,
                "kind": "at-prefixed-docs-pointer",
                "detail": (f"{citing_rel}:{lineno} uses `{m.group(0)}` — an @-prefix "
                           f"re-imports the target into the always-loaded set. "
                           f"Backtick the path instead."),
            })
    return out


def _corpus(rules_dir: str = RULES_DIR) -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.isdir(rules_dir):
        return out
    for root, _dirs, files in os.walk(rules_dir):
        for fn in files:
            if fn.endswith(".md") and ".bak-" not in fn:
                out[fn] = _read(os.path.join(root, fn))
    return out


def _rel(path: str) -> str:
    home_claude = os.path.join(HOME, ".claude") + os.sep
    return path[len(home_claude):] if path.startswith(home_claude) else path


def scan_corpus(rules_dir: str = RULES_DIR,
                skills_dir: str = SKILLS_DIR) -> dict:
    """Whole-corpus scan. Reports every undecided citation."""
    rules_texts = _corpus(rules_dir)
    acceptances = _load_acceptances()
    findings: list[dict] = []
    scanned = 0

    for base in (rules_dir, skills_dir):
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            for fn in files:
                if not fn.endswith((".md", ".py", ".sh")) or ".bak-" in fn:
                    continue
                p = os.path.join(root, fn)
                if "starter-kit" in p:
                    continue
                text = _read(p)
                scanned += 1
                rel = _rel(p)
                findings += check_text(rel, text, rules_texts, acceptances)
                findings += check_docs_pointers(rel, text)

    return {
        "status": "FAIL" if findings else "PASS",
        "scanned_files": scanned,
        "rules_files": len(rules_texts),
        "acceptances": len(acceptances),
        "undecided": len(findings),
        "findings": findings,
    }


def check_path(path: str) -> dict:
    """Check ONE written file — the PostToolUse entry point."""
    rules_texts = _corpus()
    acceptances = _load_acceptances()
    text = _read(path)
    rel = _rel(path)
    findings = check_text(rel, text, rules_texts, acceptances)
    findings += check_docs_pointers(rel, text)
    return {
        "status": "FAIL" if findings else "PASS",
        "path": path,
        "undecided": len(findings),
        "findings": findings,
    }


def render(findings: list[dict]) -> str:
    """One line per finding, naming citer, cited file and unresolved section —
    the three parts the Desired Outcome requires so the fix needs no
    investigation."""
    return "\n".join(f"  - {f['detail']}" for f in findings)


# --- self test ---------------------------------------------------------------
def _self_test() -> int:
    cited = (
        "# Plan Gates\n"
        "<!-- anchor: verification-source-registry -->\n"
        "## Verification Source Registry\n"
        "### 0a: Diagnosis\n"
        "### 0b: Desired Outcome\n"
        "### 0d: Guiding Policy\n"
        "### 0e: Coherent Actions\n"
        "<!-- anchor: skill-internal-locked-step-contracts -->\n"
        "**Skill-internal locked step contracts.** Some prose here.\n"
        "## Gate 1T — Business Rule Disposition (opt-in per project)\n"
    )
    corpus = {"plan-gates.md": cited}
    acc = [{"citing_file": "skills/audit-session/SKILL.md",
            "cited_file": "pre-plan-gates.md", "section": None}]
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    t = targets_in(cited)
    check("Verification Source Registry" in t, "heading must be a target")
    check("verification-source-registry" in t, "anchor must be a target")
    check("Skill-internal locked step contracts" in t, "bold run-in must be a target")

    # the real break A0 found
    f = check_text("skills/lean-analytics-metrics/SKILL.md",
                   "- `plan-gates.md` — Gate 4 (Success Metrics) uses the rubric\n",
                   corpus, [])
    check(len(f) == 1 and f[0]["kind"] == "section-unresolved",
          f"'Gate 4' must fail as unresolved, got {f}")
    check("Gate 4" in f[0]["section"], "finding must name the unresolved section")
    check("lean-analytics-metrics" in f[0]["detail"] and "plan-gates.md" in f[0]["detail"],
          "detail must name citer AND cited file")

    # the 11-to-16 semantic citations the alias table exists for
    for alias_line in ("- `plan-gates.md` Gate 0a OQ19 — Plain business words first.\n",
                       "- `plan-gates.md` — Gate 0b (Desired Outcome) — rubric\n",
                       "- `plan-gates.md` Gate 0a/0d/0e canon\n"):
        f = check_text("skills/x/SKILL.md", alias_line, corpus, [])
        check(not f, f"alias form must resolve: {alias_line.strip()} -> {f}")

    # literal + quoted + backticked forms
    for ok_line in ('- `plan-gates.md` — Verification Source Registry (KL-primary)\n',
                    '- `plan-gates.md` "Skill-internal locked step contracts" carve-out\n',
                    '- `plan-gates.md` — `0b: Desired Outcome`, **Testability**\n',
                    '- `plan-gates.md` — Gate 1T — Business Rule Disposition\n'):
        f = check_text("skills/x/SKILL.md", ok_line, corpus, [])
        check(not f, f"valid citation must resolve: {ok_line.strip()} -> {f}")

    # a second file named later on the same line owns its own quoted phrase
    f = check_text(
        "skills/x/SKILL.md",
        'sanctioned by `plan-gates.md` "Skill-internal locked step contracts" — '
        'distinct from tables bound by `prompt-engineering.md` "Totally Absent Section"\n',
        corpus, [])
    check(not f, f"a later file's quoted phrase must not attribute to the earlier one: {f}")

    # a compound naming a phantom section is still broken
    f = check_text("skills/x/SKILL.md", "- `plan-gates.md` Gate 0a/0z canon\n", corpus, [])
    check(len(f) == 1, f"compound with a phantom part must fail, got {f}")

    # filename-only citation is not a section citation
    f = check_text("skills/x/SKILL.md",
                   "See `plan-gates.md` for the canonical kernel definitions.\n",
                   corpus, [])
    check(not f, f"filename-only citation must not fail: {f}")

    # missing cited file, and the acceptance that decides it
    line = "- `process` — deviation from workflow/gate rules (`pre-plan-gates.md`)\n"
    f = check_text("skills/audit-session/SKILL.md", line, corpus, [])
    check(len(f) == 1 and f[0]["kind"] == "cited-file-missing",
          f"missing cited file must fail without an acceptance, got {f}")
    f = check_text("skills/audit-session/SKILL.md", line, corpus, acc)
    check(not f, f"a recorded acceptance must decide it: {f}")

    # noise exclusions
    for noise in ("run `check-plan-gates.sh` against the plan\n",
                  "  - src: starter-kit plan-gates.md bundle\n",
                  "see plan-gates.md.bak-20260808224015 for history\n"):
        f = check_text("rules/x.md", noise, corpus, [])
        check(not f, f"noise must be excluded: {noise.strip()} -> {f}")

    # docs-pointer assertion
    f = check_docs_pointers("rules/x.md", "see @~/.claude/docs/runbook.md for detail\n")
    check(len(f) == 1 and f[0]["kind"] == "at-prefixed-docs-pointer",
          f"@-prefixed docs pointer must fail: {f}")
    f = check_docs_pointers("rules/x.md", "see `~/.claude/docs/runbook.md` for detail\n")
    check(not f, f"backticked docs pointer must pass: {f}")

    if fails:
        for m in fails:
            print("FAIL:", m)
        return 1
    print("SELF-TEST PASS — citation_check")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return _self_test()
    if "--scan" in argv:
        res = scan_corpus()
        print(json.dumps({k: v for k, v in res.items() if k != "findings"}, indent=2))
        if res["findings"]:
            print("\nUNDECIDED CITATIONS:")
            print(render(res["findings"]))
        return 0 if res["status"] == "PASS" else 1
    if len(argv) > 1 and os.path.isfile(argv[1]):
        res = check_path(argv[1])
        if res["findings"]:
            print(render(res["findings"]), file=sys.stderr)
        return 0 if res["status"] == "PASS" else 1
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

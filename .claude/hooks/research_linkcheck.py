#!/usr/bin/env python3
"""Research linkcheck worker (S6 / Plan A6).

Invoked by ${KIT_HOOKS_DIR}/research-linkcheck.sh (PostToolUse on
Write|Edit to *_RESEARCH*.md). HEAD-checks every external URL in the
file, tags broken links inline as

    [text](url) ⚠ BROKEN (HTTP <code> at <iso-ts>)

and writes {ok_count, broken_count, checked_at} to the cycle's manifest
state at cycles[cycle_id]['linkcheck'].

Guard rails (per plan A6):
  - bounded per-URL HEAD timeout (PER_URL_TIMEOUT_S, default 5s)
  - bounded total budget (TOTAL_BUDGET_S, default 20s)
  - sequential HEADs (no parallel storms)
  - URLs already tagged BROKEN within RECHECK_SKIP_HOURS are skipped
  - idempotent re-runs do not re-tag already-broken URLs

Stdlib only.
"""

import fcntl
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Sibling shared module (same hooks dir). Guard sys.path for import-as-module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _citation_reconcile import reconcile_citation_labels, _TRUST_TAG_RE  # noqa: E402
from _citation_resolve import (  # noqa: E402
    BROKEN_OUTCOMES, DEAD, MISROOTED, UNRESOLVED, counts, resolve_all,
)

PER_URL_TIMEOUT_S = float(os.environ.get("LINKCHECK_PER_URL_TIMEOUT_S", "5"))
TOTAL_BUDGET_S = float(os.environ.get("LINKCHECK_TOTAL_BUDGET_S", "20"))
RECHECK_SKIP_HOURS = float(os.environ.get("LINKCHECK_SKIP_HOURS", "24"))
USER_AGENT = "claude-code-research-linkcheck/1.0"

# Markdown inline link: [text](url). Excludes images ![..](..) by anchoring
# to start-of-string or a non-'!' character.
LINK_RE = re.compile(r"(?<!\!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)")

# Detect an existing BROKEN tag immediately after a link:
#   ⚠ BROKEN (HTTP <code> at <iso-ts>)
BROKEN_TAG_RE = re.compile(
    r"\s*⚠ BROKEN \(HTTP (\d+) at ([0-9T:\-+.Z]+)\)"
)

# --------------------------------------------------------------------------- #
# S13 / design-A10 — internal citations.
#
# The downgrade a DEAD internal citation receives. This is a FILLING of the
# already-registered `[unverified — …]` marker's free-text slot, exactly as
# `_citation_reconcile._DOWNGRADE_FMT`'s "source unreachable at fetch" filling
# is — so it needs no citation-vocabulary edit (which would be an A17 three-loci
# change across code and both Layer-2 mirrors).
#
# COUNTING ASYMMETRY, disclosed rather than left to be discovered: a dead
# citation reaches `broken_count` on the run that finds it and NOT on re-runs,
# whereas a dead URL keeps counting every run. The URL's `⚠ BROKEN` tag stays
# attached to a link that is still present and still dead; this rewrite removes
# the citation marker entirely (`[unverified — …]` carries no payload, so the
# citation scanner cannot see it), and after the downgrade the claim no longer
# asserts a source at all. So design-A10's "the same broken-link accounting"
# holds for the FIRST run; "exactly as a dead URL is" is first-run-only.
# Pinned by `test_a2_a_dead_citation_counts_once_while_a_dead_url_keeps_counting`.
_DEAD_CITATION_FMT = "[unverified — source not found — {locator}]"

# The `misrooted` annotation. Deliberately mirrors the shipped
# ` ⚠ BROKEN (HTTP <code> at <ts>)` convention — trailing, parenthesised, placed
# after an UNALTERED marker. Deliberately NOT bracket-wrapped: a `[…]`-shaped
# annotation risks being scanned as a citation marker by the engine's
# `_citation_scan_re`, which is exactly the vocabulary drift A17 exists to stop.
_MISROOTED_TAG_FMT = " ⚠ MISROOTED (resolves under {root})"

# Recognise an already-placed MISROOTED annotation so re-runs are idempotent.
_MISROOTED_TAG_RE = re.compile(r"\s*⚠ MISROOTED \(resolves under [^)]*\)")

# The workspace root the Marker Contract's Edge 1 names as the correct root for a
# citation that resolves inside it. Overridable for tests.
WORKSPACE_ROOT = os.environ.get(
    "LINKCHECK_WORKSPACE_ROOT", str(Path.home() / "repos" / "Projects"))


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    # Compact ISO-8601 to seconds (no microseconds) for inline tag legibility.
    return dt.replace(microsecond=0).isoformat()


def _parse_iso(ts):
    """Best-effort ISO-8601 parse; returns None on failure."""
    try:
        # Python 3.11+ accepts 'Z'; for earlier, normalize.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def head_check(url):
    """Return (status_code, error_text). status_code=None on connection error.

    Tries HEAD first; falls back to GET-with-truncation if HEAD is rejected
    (some servers return 405 on HEAD but 200 on GET).
    """
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(
            url, method=method, headers={"User-Agent": USER_AGENT}
        )
        try:
            with urllib.request.urlopen(req, timeout=PER_URL_TIMEOUT_S) as resp:
                return resp.status, None
        except urllib.error.HTTPError as e:
            # 405 on HEAD → retry GET; otherwise return the code as-is.
            if method == "HEAD" and e.code == 405:
                continue
            return e.code, str(e.reason or "")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, str(e)
    return None, "unknown"


def _looks_broken(status, err):
    """Treat 4xx/5xx and connection failures as broken. Redirects are followed
    by urllib by default, so a 200 final response is healthy."""
    if status is None:
        return True
    return status >= 400


def find_links(text):
    """Yield (match, text, url, has_existing_tag, tag_age_h) for each link.

    has_existing_tag is True when a BROKEN tag follows the link immediately.
    tag_age_h is the age of that tag in hours, or None if absent / unparseable.
    """
    for m in LINK_RE.finditer(text):
        link_text = m.group(1)
        url = m.group(2)
        after = text[m.end(): m.end() + 100]
        tag_m = BROKEN_TAG_RE.match(after)
        if tag_m:
            ts = _parse_iso(tag_m.group(2))
            if ts is None:
                age_h = None
            else:
                age_h = (_now() - ts).total_seconds() / 3600.0
            yield m, link_text, url, True, age_h
        else:
            yield m, link_text, url, False, None


def _scan_citations_with_spans(text):
    """Every citation in `text`, as (match, ParsedCitation).

    Uses the ENGINE's own scanner and its single classifier — never a regex of
    this module's own. `parse_citations` gives the same classifications but not
    the match spans, and the body annotations below need offsets. Re-parsing
    markers here would be the drift rule H2 removed, so the scanner is borrowed
    rather than reimplemented.

    Returns [] when the engine cannot be imported: an internal-citation check
    that cannot run must degrade to "not run", never to "nothing found".
    """
    try:
        from _factcheck_engine import _citation_scan_re, classify_citation
    except Exception:                            # noqa: BLE001 — never block
        return None
    out = []
    for m in _citation_scan_re().finditer(text):
        line = text.count("\n", 0, m.start()) + 1
        out.append((m, classify_citation(m.group("kind"), m.group("payload"),
                                         line)))
    return out


def apply_internal_citations(text, citing_file, *, exists=None,
                             workspace_root=None, checkout_for=None):
    """Resolve every INTERNAL citation in `text` and annotate the body.

    Three of the four outcomes are visible or countable, and the fourth
    deliberately is not:

      dead       the marker is downgraded in place to `[unverified — source not
                 found — <locator>]` and counted broken — the same treatment a
                 dead URL's marker already gets.
      misrooted  an annotation is appended after the UNALTERED marker naming the
                 root the target actually resolves under. Not counted broken and
                 NOT downgraded: the source exists. Downgrading it would be the
                 five-in-six false failure this slice exists to avoid.
      unresolved recorded only. An unrunnable check never marks a person's report.
      live       nothing.

    Returns (new_text, tally, details). `tally` is None when the check could not
    run at all — distinct from a tally of zeros, which means it ran and found
    nothing. Callers must keep those apart; conflating them is the silence this
    slice removes.
    """
    scanned = _scan_citations_with_spans(text)
    if scanned is None:
        return text, None, []

    pairs = resolve_all(
        [c for _m, c in scanned],
        exists=exists or os.path.exists,
        workspace_root=workspace_root or WORKSPACE_ROOT,
        citing_file=citing_file,
        checkout_for=checkout_for,
    )
    # Re-associate each resolution with its match. `resolve_all` preserves report
    # order over the internal subset, so zipping against the internal matches in
    # the same order is exact, not approximate.
    internal_matches = [m for m, c in scanned if getattr(c, "is_internal", False)]

    details, patches = [], []
    for (m, (cit, res)) in zip(internal_matches, pairs):
        details.append({
            "outcome": res.outcome,
            "locator": res.locator,
            "raw": cit.raw,
            "line": cit.line,
            "reason": res.reason,
            "resolved_root": res.resolved_root,
        })
        if res.outcome == DEAD:
            patches.append((m.start(), m.end(),
                            _DEAD_CITATION_FMT.format(locator=cit.raw)))
        elif res.outcome == MISROOTED:
            after = text[m.end(): m.end() + 200]
            if _MISROOTED_TAG_RE.match(after):
                continue                        # already annotated → idempotent
            patches.append((m.end(), m.end(),
                            _MISROOTED_TAG_FMT.format(root=res.resolved_root)))

    # Apply right-to-left so earlier offsets stay valid.
    for start, end, replacement in sorted(patches, key=lambda x: -x[0]):
        text = text[:start] + replacement + text[end:]

    return text, counts(pairs), details


def linkcheck_file(file_path, _head_check_fn=None):
    """HEAD-check every URL in file_path; tag broken markdown links inline AND
    downgrade unreachable-source citation markers — in ONE flock-protected write.

    A `[stated — URL]` / `[paraphrased — URL]` citation whose source URL does not
    load is rewritten to `[unverified — source unreachable at fetch — URL]` via
    the shared `_citation_reconcile` module, in the same write that inserts the
    `⚠ BROKEN` markdown-link tags — so a report's body trust-label and its
    reachability facts can never disagree on disk. The read-modify-write runs
    under the SAME `<report>.md.lock` flock the fact-check engine uses, so the two
    body-mutators serialize (HEAD network stays OUTSIDE the lock).

    `_head_check_fn` is an injection seam for tests (defaults to `head_check`).

    Returns dict {ok_count, broken_count, skipped_count, budget_exhausted,
    downgraded_count}.
    """
    head_fn = _head_check_fn or head_check
    p = Path(file_path)
    if not p.exists():
        return {
            "ok_count": 0,
            "broken_count": 0,
            "skipped_count": 0,
            "budget_exhausted": False,
            "downgraded_count": 0,
            "error": "file not found",
        }

    text = p.read_text(encoding="utf-8")

    start = _now()
    budget_exhausted = False
    seen = {}  # url -> (status, is_broken)

    def _head_into_seen(url):
        """HEAD-check url once (budget-bounded), recording it in `seen`.
        Returns is_broken, or None if the budget was exhausted first."""
        nonlocal budget_exhausted
        if url in seen:
            return seen[url][1]
        if (_now() - start).total_seconds() >= TOTAL_BUDGET_S:
            budget_exhausted = True
            return None
        status, _err = head_fn(url)
        is_broken = _looks_broken(status, _err)
        seen[url] = (status, is_broken)
        return is_broken

    # --- Phase 1: HEAD-check every URL (markdown links + citation markers)
    #     OUTSIDE the lock. Network is slow; never hold the file lock for it. ---
    for m, link_text, url, has_tag, age_h in find_links(text):
        if has_tag and age_h is not None and age_h < RECHECK_SKIP_HOURS:
            continue  # already tagged within the recheck window → no HEAD
        _head_into_seen(url)
    for cm in _TRUST_TAG_RE.finditer(text):
        _head_into_seen(cm.group(2))

    # Reachability closure over the shared `seen` cache: True/False when checked,
    # None (unknown → never downgrade) when the budget skipped it.
    def _is_reachable(url):
        entry = seen.get(url)
        if entry is None:
            return None
        return not entry[1]

    # --- Phase 2: read-modify-write under the engine's <report>.md.lock. ---
    lockpath = p.with_suffix(p.suffix + ".lock")
    ok = broken = skipped = downgraded_count = 0
    citation_tally, citation_details = None, []
    with open(lockpath, "w") as _lock:
        fcntl.flock(_lock, fcntl.LOCK_EX)
        try:
            text = p.read_text(encoding="utf-8")  # re-read INSIDE the lock
            original = text

            # Markdown BROKEN tags — recompute offsets against the in-lock text.
            patches = []
            for m, link_text, url, has_tag, age_h in find_links(text):
                if has_tag and age_h is not None and age_h < RECHECK_SKIP_HOURS:
                    skipped += 1
                    broken += 1  # still broken — count it for the gate report
                    continue
                entry = seen.get(url)
                if entry is None:
                    continue  # unchecked (budget) → leave untouched
                status, is_broken = entry
                if is_broken:
                    broken += 1
                    if not has_tag:
                        code = status if status is not None else 0
                        tag = " ⚠ BROKEN (HTTP {code} at {ts})".format(
                            code=code, ts=_iso(_now())
                        )
                        patches.append((m.end(), tag))
                else:
                    ok += 1
            for end, tag in sorted(patches, key=lambda x: -x[0]):
                text = text[:end] + tag + text[end:]

            # Citation-label downgrade — SAME write as the BROKEN tags above.
            text, downgraded = reconcile_citation_labels(text, _is_reachable)
            downgraded_count = len(downgraded)

            # S13/A2+A3 — internal citations, in the SAME write again. A dead one
            # is downgraded and counted broken exactly as a dead URL is; a
            # misrooted one is annotated but neither counted nor downgraded.
            text, citation_tally, citation_details = apply_internal_citations(
                text, str(p))
            if citation_tally is not None:
                # design-A10: the SAME broken-link accounting, so there is one
                # answer to "how many broken links does this report have".
                #
                # Summed over BROKEN_OUTCOMES rather than over DEAD directly, so
                # "which outcomes count as broken" has ONE definition. Counting
                # `DEAD` here while `Resolution.is_broken` consulted the constant
                # was two answers to one question — the drift shape this topic
                # keeps recording against itself, and a revert-check arm caught
                # it: flipping the constant left this counter unmoved.
                broken += sum(citation_tally.get(o, 0) for o in BROKEN_OUTCOMES)

            if text != original:
                p.write_text(text, encoding="utf-8")
        finally:
            fcntl.flock(_lock, fcntl.LOCK_UN)

    result = {
        "ok_count": ok,
        "broken_count": broken,
        "skipped_count": skipped,
        "budget_exhausted": budget_exhausted,
        "downgraded_count": downgraded_count,
    }
    # `None` means the check could not run; a tally of zeros means it ran and
    # found nothing. Those are different answers and are reported differently.
    if citation_tally is None:
        result["citation_check"] = "not-run"
        result["citation_check_reason"] = (
            "the citation vocabulary could not be loaded, so internal citations "
            "were not resolved")
    else:
        result["citation_check"] = "ran"
        result["citation_counts"] = citation_tally
        result["citation_details"] = citation_details
    return result


def write_manifest_linkcheck(sid, cycle_id, result, state_dir=None):
    """Write linkcheck result into cycles[cycle_id]['linkcheck'] of the
    research_pipeline manifest. No-op when the manifest is absent (not a
    research session).
    """
    if state_dir is None:
        state_dir = os.environ.get("RP_STATE_DIR") or str(
            Path.home() / ".claude" / "state" / "research_pipeline"
        )
    path = Path(state_dir) / "RP-{sid}.json".format(sid=sid)
    if not path.exists():
        return False

    with open(path, "r", encoding="utf-8") as fh:
        state = json.load(fh)

    cycles = state.setdefault("cycles", {})
    cycle = cycles.setdefault(cycle_id, {})
    cycle["linkcheck"] = {
        "ok_count": result.get("ok_count", 0),
        "broken_count": result.get("broken_count", 0),
        "skipped_count": result.get("skipped_count", 0),
        "budget_exhausted": result.get("budget_exhausted", False),
        "checked_at": _iso(_now()),
        # S13/A4 — the internal-citation check. Recorded as "ran" + a per-outcome
        # tally, or "not-run" + a reason; never as a zero standing in for both.
        "citation_check": result.get("citation_check", "not-run"),
        "citation_counts": result.get("citation_counts"),
        "citation_check_reason": result.get("citation_check_reason"),
    }
    state["updated_at"] = _iso(_now())

    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(str(tmp), str(path))
    return True


def _citation_cycle_fields(result):
    """The fc_cycles row fields carrying this run's internal-citation outcome."""
    fields = {
        "citation_check": result.get("citation_check", "not-run"),
        "citation_check_at": _iso(_now()),
    }
    tally = result.get("citation_counts")
    if tally:
        fields["citation_outcomes"] = " ".join(
            f"{k}={tally.get(k, 0)}"
            for k in ("live", MISROOTED, DEAD, UNRESOLVED))
    why = result.get("citation_check_reason")
    if why:
        fields["citation_check_reason"] = why
    return fields


def write_frontmatter_citation_check(file_path, cycle_id, result,
                                     _already_locked=False):
    """Record the internal-citation outcome in the report's OWN frontmatter.

    This is the no-manifest route. `write_manifest_linkcheck` returns False when
    `RP-<sid>.json` is absent, and the Internal-KB route never calls `r0_intake`
    (`research-scope-framing.md` Step 4) — so on the one route whose reports are
    internal BY DEFINITION there is no manifest to extend. The frontmatter
    writeback is file-path-driven and needs no manifest, which is why the record
    lands there as well as in the cycle.

    DEADLOCK GUARD (verified, not defensive): `linkcheck_file` holds an exclusive
    flock on `<report>.md.lock`, and `_append_research_frontmatter` re-opens and
    re-locks that SAME path on a fresh descriptor — a same-process deadlock. So
    this is called AFTER `linkcheck_file` has released, taking the lock itself;
    a caller that already holds it must pass `_already_locked=True`, which routes
    to the inner `_locked` form instead.

    Returns True when a record was written.
    """
    p = Path(file_path)
    if not p.exists():
        return False
    # `partial_row` — this writer owns the citation fields and NOTHING else. It
    # must not emit `verdict`/`rounds`, which it does not know: rows merge per
    # field with fresh winning, so emitting the renderer's "UNKNOWN"/0 defaults
    # would overwrite the fact-check's own verdict for this cycle.
    row = {"cycle": cycle_id or "default", "partial_row": True}
    row.update(_citation_cycle_fields(result))
    try:
        from _factcheck_engine import (
            _append_research_frontmatter, _append_research_frontmatter_locked,
        )
    except Exception:                            # noqa: BLE001 — never block
        return False
    try:
        if _already_locked:
            _append_research_frontmatter_locked(p, [row])
        else:
            _append_research_frontmatter(str(p), [row])
    except Exception:                            # noqa: BLE001 — never block
        return False
    return True


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if len(argv) < 3:
        sys.stderr.write(
            "Usage: research_linkcheck.py SID FILE_PATH [--cycle-id ID]\n"
        )
        return 2

    sid = argv[1]
    file_path = argv[2]

    # D1: resolve the cycle from the RESEARCH FILE — the strongest key this
    # surface holds — rather than from `${CYCLE_ID:-default}`, a variable no
    # code in the harness ever sets. Filing under `default` did two wrong
    # things at once: it recorded a run's findings against a run that did not
    # produce them, and (because write_manifest_linkcheck returns early only
    # when the MANIFEST is absent, not when the cycle is) it minted a `default`
    # cycle the run never had.
    #
    # `_resolve_research_cycle_id` is the EXISTING file->cycle matcher; it is
    # reused rather than reimplemented so there is one answer to "which cycle
    # owns this file", not two that can disagree. It already returns
    # ('default', None) for an absent manifest or an unclaimed file, which is
    # the honest fallback when this surface's own key does not resolve.
    #
    # An explicit --cycle-id still wins, so a caller that genuinely knows its
    # cycle is not overridden.
    cycle_id = None
    if "--cycle-id" in argv:
        i = argv.index("--cycle-id")
        if i + 1 < len(argv):
            cycle_id = argv[i + 1]
    if not cycle_id:
        try:
            from _factcheck_engine import _resolve_research_cycle_id
            cycle_id, _ = _resolve_research_cycle_id(sid, file_path)
        except Exception:                       # noqa: BLE001 — never block
            cycle_id = "default"
        if not cycle_id:
            cycle_id = "default"

    result = linkcheck_file(file_path)
    wrote = write_manifest_linkcheck(sid, cycle_id, result)
    result["manifest_updated"] = wrote
    result["cycle_id"] = cycle_id

    # S13/A4 — record the citation check on the report itself as well. This runs
    # AFTER linkcheck_file has released `<report>.md.lock`; calling it from
    # inside would deadlock (same process, second open file description on the
    # same lock path). It is unconditional rather than a manifest fallback: the
    # frontmatter is where a later reader looks, and on the Internal-KB route it
    # is the ONLY record that exists.
    result["frontmatter_updated"] = write_frontmatter_citation_check(
        file_path, cycle_id, result)

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

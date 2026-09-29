#!/usr/bin/env python3
"""bookkeeping_paths — the single source of truth for the main-owned shared
bookkeeping path set (S2 git-working-model / A4).

Canonical DATA lives in the sibling `bookkeeping-paths.json` manifest. This
module is the standalone, harness-free check-logic that reads it (per
`skill-location.md` — check-logic is a standalone Python module callable
without the Claude hook harness; plain dict/JSON in and out). Three code paths
consume it:

  * A1  bookkeeping_resolver  — `shared_entries()` is the resolver's scope.
  * A3  worktree-helper.sh    — `exclusion-globs` is the sparse-checkout set.
  * A5  check-worktree-commit-gate.sh — `classify` is the commit allow-rule.

The `bookkeeping-model.md` path table is GENERATED from the manifest
(single-source + code-gen link, design A3 Cycle-7); `check-drift` fails on any
divergence (cloning the extract-both-sides/diff/fail shape of
check-localization-table.sh).

Pure: no Claude-specific imports, no network. Every path is env-overridable so
the same logic serves the live tree, a config source tree, and the V1 scratch
repo.

CLI (each verb prints to stdout and sets a meaningful exit code):
  shared-globs                 one repo-relative glob per shared entry
  exclusion-globs              sparse-checkout EXCLUDE patterns (A3)
  gitattributes                `<glob> merge=union` lines for merge_union entries (A2)
  classify --repo <root> P...  bookkeeping-only | harness | mixed | none  (A5)
  gen-prose [--write]          render the prose path table (SCL generated side)
  check-drift                  0 in-sync, 1 drift (names the divergent side)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# --- locations (all env-overridable for source-tree / scratch-repo reuse) ----
_HOOKS_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = Path(
    os.environ.get("BOOKKEEPING_PATHS_MANIFEST", _HOOKS_DIR / "bookkeeping-paths.json")
)
# The prose contract whose path table is generated from the manifest.
PROSE_PATH = Path(
    os.environ.get(
        "BOOKKEEPING_MODEL_FILE",
        _HOOKS_DIR.parent / "rules" / "bookkeeping-model.md",
    )
)

# Sentinel markers delimiting the generated block inside the prose file.
GEN_BEGIN = "<!-- BEGIN GENERATED bookkeeping-paths -->"
GEN_END = "<!-- END GENERATED bookkeeping-paths -->"


# ══════════════════════════════════════════════════════════════════════════
# domain (pure) — manifest read + matching
# ══════════════════════════════════════════════════════════════════════════

def load_manifest(path: Optional[Path] = None) -> dict:
    p = Path(path) if path else MANIFEST_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def shared_entries(manifest: Optional[dict] = None) -> list[dict]:
    m = manifest if manifest is not None else load_manifest()
    return list(m.get("shared_bookkeeping", []))


def _norm(rel: str) -> str:
    """Normalize a repo-relative path to forward slashes, no leading './'."""
    r = rel.replace("\\", "/").strip()
    while r.startswith("./"):
        r = r[2:]
    return r.lstrip("/")


def match_entry(rel_path: str, manifest: Optional[dict] = None) -> Optional[dict]:
    """Return the shared_bookkeeping entry a repo-relative path matches, else None.

    match kinds:
      basename    — the path's final component equals the entry path
      exact       — the whole repo-relative path equals the entry path
      dir-prefix  — the repo-relative path is inside the entry directory
    """
    rel = _norm(rel_path)
    base = rel.rsplit("/", 1)[-1]
    for e in shared_entries(manifest):
        target = _norm(e["path"])
        kind = e.get("match")
        if kind == "basename":
            if base == target.rstrip("/"):
                return e
        elif kind == "exact":
            if rel == target:
                return e
        elif kind == "dir-prefix":
            pref = target if target.endswith("/") else target + "/"
            if rel == pref.rstrip("/") or rel.startswith(pref):
                return e
        else:
            raise ValueError(f"unknown match kind {kind!r} in manifest entry {e!r}")
    return None


def is_shared(rel_path: str, manifest: Optional[dict] = None) -> bool:
    return match_entry(rel_path, manifest) is not None


def shared_globs(manifest: Optional[dict] = None) -> list[str]:
    """One repo-relative glob per shared entry (stable order = manifest order)."""
    out: list[str] = []
    for e in shared_entries(manifest):
        target = _norm(e["path"])
        kind = e["match"]
        if kind == "basename":
            out.append(f"**/{target}")
        elif kind == "exact":
            out.append(target)
        elif kind == "dir-prefix":
            out.append((target.rstrip("/")) + "/**")
    return out


def sparse_checkout_patterns(manifest: Optional[dict] = None) -> list[str]:
    """Non-cone `git sparse-checkout set` patterns that INCLUDE everything and
    EXCLUDE the shared bookkeeping set (A3 fail-closed worktree exclusion).

    Leading `/*` includes all top-level entries recursively; each `!` line then
    re-excludes a shared surface (last matching pattern wins). Generated from
    the manifest so the exclusion set has no hardcoded twin.
    """
    pats = ["/*"]
    for e in shared_entries(manifest):
        target = _norm(e["path"])
        kind = e["match"]
        if kind == "basename":
            pats.append("!" + target.rstrip("/"))       # any depth (e.g. !TODO.md)
        elif kind == "exact":
            pats.append("!/" + target)                   # anchored to repo root
        elif kind == "dir-prefix":
            pats.append("!/" + target.rstrip("/") + "/")  # a root-anchored dir
    return pats


def gitattributes_lines(manifest: Optional[dict] = None) -> list[str]:
    """`<pattern> merge=union` lines for the append-list-shaped shared files (A2).

    merge=union is git's built-in union merge driver (no driver registration
    needed) — it concatenates both sides' added lines on a merge instead of
    conflicting. Applied ONLY to entries flagged merge_union (append lists:
    TODO buckets, Diary sections, Stats rows — design A4 Cycle-11); spines are
    excluded so a genuine structured conflict still surfaces.
    """
    lines: list[str] = []
    for e in shared_entries(manifest):
        if not e.get("merge_union"):
            continue
        target = _norm(e["path"])
        kind = e["match"]
        if kind == "basename":
            pat = target  # gitattributes matches a bare name at any depth
        elif kind == "exact":
            pat = "/" + target  # anchor to repo root
        elif kind == "dir-prefix":
            pat = target.rstrip("/") + "/**"
        lines.append(f"{pat} merge=union")
    return lines


# ══════════════════════════════════════════════════════════════════════════
# A5 — commit classification
# ══════════════════════════════════════════════════════════════════════════

def _config_source_path() -> Optional[Path]:
    """Resolve the configured source path, or None if config-source is unavailable.

    Overridable via Q_CONFIG_SOURCE_PATH so the V1 scratch test can simulate a
    'harness' repo without a real config-source.
    """
    env = os.environ.get("Q_CONFIG_SOURCE_PATH")
    if env:
        try:
            return Path(env).resolve()
        except OSError:
            return None
    try:
        out = subprocess.run(
            ["false"],  # no config-source tool ships with Q
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip()).resolve()
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass
    return None


def is_config_source_repo(repo_root: Path) -> bool:
    src = _config_source_path()
    if src is None:
        return False
    try:
        return Path(repo_root).resolve() == src
    except OSError:
        return False


def classify_commit(
    repo_root: Path, changed_rel_paths: list[str], manifest: Optional[dict] = None
) -> str:
    """Classify a would-be main-side (out-of-worktree) commit.

    Returns:
      "harness"          — the repo IS the config source (a promote surface);
                           allowed structurally (retires ALLOW_OUT_OF_TREE).
      "bookkeeping-only" — every changed path is a shared bookkeeping path;
                           allowed without prompting.
      "mixed"            — some shared + some non-shared; block-with-override.
      "none"             — no shared paths at all; ordinary out-of-tree block.
    """
    m = manifest if manifest is not None else load_manifest()
    if is_config_source_repo(repo_root):
        return "harness"
    changed = [p for p in changed_rel_paths if p.strip()]
    if not changed:
        return "none"
    shared = [p for p in changed if is_shared(p, m)]
    if len(shared) == len(changed):
        return "bookkeeping-only"
    if shared:
        return "mixed"
    return "none"


# ══════════════════════════════════════════════════════════════════════════
# A4 — SCL: prose generation + drift-check
# ══════════════════════════════════════════════════════════════════════════

def render_prose_table(manifest: Optional[dict] = None) -> str:
    """Render the marker-delimited path table generated from the manifest.

    This IS the generated side of the SCL link. The block (markers included) is
    what lives in bookkeeping-model.md; check-drift regenerates and compares.
    """
    m = manifest if manifest is not None else load_manifest()
    rows = [
        "| Path | Match | Kind | merge=union |",
        "|---|---|---|---|",
    ]
    for e in m.get("shared_bookkeeping", []):
        mu = "yes" if e.get("merge_union") else "no"
        rows.append(f"| `{e['path']}` | {e['match']} | {e['kind']} | {mu} |")
    hm = m.get("harness_main_owned", {})
    body = "\n".join(rows)
    tail = ""
    if hm:
        tail = (
            f"\n\nHarness (config-source) repo: recognized by "
            f"`{hm.get('recognize_by', '')}` — every commit in its primary clone "
            "is a main-side promotion, allowed structurally."
        )
    return f"{GEN_BEGIN}\n{body}{tail}\n{GEN_END}"


def _extract_block(text: str) -> Optional[str]:
    i = text.find(GEN_BEGIN)
    j = text.find(GEN_END)
    if i == -1 or j == -1 or j < i:
        return None
    return text[i : j + len(GEN_END)]


def check_drift(
    manifest: Optional[dict] = None, prose_path: Optional[Path] = None
) -> tuple[bool, str]:
    """(in_sync, message). True iff the prose block matches the generated table."""
    p = Path(prose_path) if prose_path else PROSE_PATH
    generated = render_prose_table(manifest)
    if not p.exists():
        return False, f"prose file not found: {p}"
    text = p.read_text(encoding="utf-8")
    block = _extract_block(text)
    if block is None:
        return False, (
            f"no generated bookkeeping-paths block in {p} "
            f"(expected between {GEN_BEGIN!r} and {GEN_END!r}) — "
            f"run `python3 {Path(__file__).name} gen-prose --write`"
        )
    if block.strip() != generated.strip():
        return False, (
            f"bookkeeping-paths manifest and the generated table in {p} DIVERGE — "
            f"the manifest ({MANIFEST_PATH}) is authoritative; "
            f"regenerate with `python3 {Path(__file__).name} gen-prose --write`"
        )
    return True, "bookkeeping-paths manifest and prose table are in sync"


def write_prose(manifest: Optional[dict] = None, prose_path: Optional[Path] = None) -> str:
    """Replace (or refuse-and-instruct) the generated block in the prose file.

    Requires the BEGIN/END markers already present (an author places them once
    at the intended location); this only regenerates the block between them so
    the surrounding prose is never touched.
    """
    p = Path(prose_path) if prose_path else PROSE_PATH
    generated = render_prose_table(manifest)
    text = p.read_text(encoding="utf-8")
    i = text.find(GEN_BEGIN)
    j = text.find(GEN_END)
    if i == -1 or j == -1 or j < i:
        raise SystemExit(
            f"cannot write: markers not found in {p}. Place the pair\n"
            f"  {GEN_BEGIN}\n  {GEN_END}\n"
            "once at the intended location, then re-run."
        )
    new = text[:i] + generated + text[j + len(GEN_END):]
    p.write_text(new, encoding="utf-8")
    return f"regenerated bookkeeping-paths block in {p}"


# ══════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="bookkeeping_paths")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("shared-globs")
    sub.add_parser("exclusion-globs")
    sub.add_parser("sparse-patterns")
    sub.add_parser("gitattributes")
    pc = sub.add_parser("classify")
    pc.add_argument("--repo", required=True)
    pc.add_argument("--stdin0", action="store_true",
                    help="read NUL-separated changed paths from stdin (commit-gate use)")
    pc.add_argument("paths", nargs="*")
    pm = sub.add_parser("match",
                        help="filter repo-relative paths (NUL-separated on stdin) by manifest membership")
    pm.add_argument("--merge-union-only", action="store_true",
                    help="match only entries flagged merge_union")
    pm.add_argument("--invert", action="store_true", help="print paths that do NOT match")
    pg = sub.add_parser("gen-prose")
    pg.add_argument("--write", action="store_true")
    sub.add_parser("check-drift")

    args = ap.parse_args(argv)

    if args.cmd == "match":
        # The SAME `match_entry` the commit gate's classifier uses. /close's
        # declaration split once re-implemented matching in bash and matched a
        # dir-prefix entry at ANY depth (`Personal/Thoughts/x.md`) where the gate
        # matches at the repo root only — so a "bookkeeping-only" publish was
        # refused as MIXED after staging. One classifier, not two (post-S8 audit).
        raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
        for p in [x for x in raw.split("\0") if x]:
            e = match_entry(p)
            hit = e is not None and (not args.merge_union_only or bool(e.get("merge_union")))
            if hit != args.invert:
                sys.stdout.write(p + "\n")
        return 0

    if args.cmd == "shared-globs":
        print("\n".join(shared_globs()))
        return 0
    if args.cmd == "exclusion-globs":
        # Sparse-checkout exclusion uses the same globs the resolver scopes on.
        print("\n".join(shared_globs()))
        return 0
    if args.cmd == "sparse-patterns":
        print("\n".join(sparse_checkout_patterns()))
        return 0
    if args.cmd == "gitattributes":
        print("\n".join(gitattributes_lines()))
        return 0
    if args.cmd == "classify":
        paths = list(args.paths)
        if args.stdin0:
            raw = sys.stdin.buffer.read().decode("utf-8", "surrogateescape")
            paths += [p for p in raw.split("\0") if p]
        verdict = classify_commit(Path(args.repo), paths)
        print(verdict)
        # Exit code mirrors the gate decision: 0 allow, 1 mixed, 2 none.
        return {"harness": 0, "bookkeeping-only": 0, "mixed": 1, "none": 2}[verdict]
    if args.cmd == "gen-prose":
        if args.write:
            print(write_prose())
        else:
            print(render_prose_table())
        return 0
    if args.cmd == "check-drift":
        ok, msg = check_drift()
        print(("PASS: " if ok else "FAIL: ") + msg, file=sys.stderr if not ok else sys.stdout)
        return 0 if ok else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

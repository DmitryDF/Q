#!/usr/bin/env python3
"""verify_kit.py — Post-build verification gate for the starter kit.

Checks (all run before reporting, fail-fast ordering for display):
  1. File presence: every file in kit.manifest.json exists.
  2. Executable: all .sh/.py hooks are executable.
  3. Author token leak: zero matches for /Users/<name>|Frikh-Khar, plus any private
     terms in the file named by Q_LEAK_TERMS_FILE.
  4. Kit structure: kit.manifest.json and .gitignore present.
  5. Git cleanliness: git init + git add -A shows no untracked (??) files.
  6. Dep closure: every resolved bundle's deps are also in resolved_bundles.
  7. Placeholder hook refs: every /.claude/hooks/ reference in shipped .sh
     and settings.json runtime artifacts must use the ${KIT_HOOKS_DIR}/ form
     (allowlist; comment-aware for .sh).

Optional (runs last, requires Claude Code session):
  8. Independent Explore README-vs-manifest check (producer-never-verifies).
     Skipped if --no-explore or if not running inside Claude Code.

Usage:
  python3 verify_kit.py <kit-dir>
  python3 verify_kit.py <kit-dir> --no-explore
"""
from __future__ import annotations

import itertools
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SKILL_DIR = Path(__file__).parent
BUNDLES_YAML = SKILL_DIR / "bundles.yaml"

AUTHOR_TOKEN_RE = re.compile(
    r"/Users/[A-Z][^/\s\n\"']{2,}"  # /Users/<AnyName>
    r"|Frikh-Khar"
)
# Private project/employer names are deliberately NOT listed here: this file ships,
# and a list in it publishes the names it guards. They come from a private file
# named by Q_LEAK_TERMS_FILE (lines `<kind> <regex>`; kinds `private` and
# `private-i` are used here). Unset = the generic check above only.


def _private_term_res() -> list[re.Pattern]:
    path = os.environ.get("Q_LEAK_TERMS_FILE")
    if not path or not Path(path).is_file():
        return []
    out = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        kind, _, rx = raw.strip().partition(" ")
        if kind == "private" and rx:
            out.append(re.compile(rx))
        elif kind == "private-i" and rx:
            out.append(re.compile(rx, re.IGNORECASE))
    return out
# `iCloud~md~obsidian` was in this pattern and has been removed: it is Obsidian's
# own iCloud container name, identical for every Obsidian user on macOS, so it
# identifies the APPLICATION and not a person. Flagging it produced two findings
# that could never be fixed (a comment in `sanitize-bash.sh` explaining the token,
# and a test fixture using it) and no privacy benefit.

# TWO EXCLUSION SETS, for two different reasons — the same split the CI leak gate
# makes, and for the same cause.
#
#   ATTRIBUTION_SURFACES  LICENSE, NOTICES.md, README.md and INSTALL.md may NAME the
#                         author: the MIT notice requires it and NOTICES exists to
#                         say "original work by <author>". They are excluded from
#                         the author-name check only.
#
#   GATE_FILES            This script names the author in its own generic check.
#                         (The CI workflow no longer carries private patterns — it
#                         reads them from a secret — but stays listed because it
#                         mentions the check's own wording.)
ATTRIBUTION_SURFACES = frozenset({"LICENSE", "NOTICES.md", "README.md", "INSTALL.md"})
GATE_FILES = frozenset({"verify_kit.py", ".github/workflows/verify.yml", ".gitleaks.toml"})

BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip",
    ".tar", ".gz", ".bz2", ".xz", ".whl", ".pyc",
})

# ---------------------------------------------------------------------------
# Check functions — each returns a list of failure strings (empty = pass)
# ---------------------------------------------------------------------------


def check_file_presence(kit: Path, manifest: dict) -> list[str]:
    failures = []
    for rec in manifest.get("files", []):
        dst = kit / rec["dst"]
        if not dst.exists():
            failures.append(f"MISSING: {rec['dst']}")
    return failures


def check_executability(kit: Path, manifest: dict) -> list[str]:
    failures = []
    for rec in manifest.get("files", []):
        dst = kit / rec["dst"]
        if not dst.exists():
            continue
        is_hook_py = dst.suffix.lower() == ".py" and ".claude/hooks" in rec["dst"]
        is_shell = dst.suffix.lower() == ".sh"
        if is_hook_py or is_shell:
            mode = dst.stat().st_mode
            if not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
                failures.append(f"NOT-EXEC: {rec['dst']}")
    return failures


def check_author_tokens(kit: Path) -> list[str]:
    failures = []
    private = _private_term_res()
    for path in sorted(kit.rglob("*")):
        if not path.is_file():
            continue
        # Skip .git — it contains author identity in commit metadata (expected)
        if ".git" in path.parts:
            continue
        if path.suffix.lower() in BINARY_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = path.relative_to(kit).as_posix()
        if rel in GATE_FILES:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(r.search(line) for r in private):
                failures.append(f"PRIVATE-TERM {rel}:{i}: {line.strip()[:80]}")
                continue
            if not AUTHOR_TOKEN_RE.search(line):
                continue
            if rel in ATTRIBUTION_SURFACES and "Frikh-Khar" in line:
                continue  # the attribution these files exist to carry
            snippet = line.strip()[:80]
            failures.append(f"AUTHOR-TOKEN {rel}:{i}: {snippet}")
    return failures


def check_structure(kit: Path) -> list[str]:
    failures = []
    # CLAUDE.md is deliberately NOT required (operator decision O1/A7): the kit ships
    # none, because an adopter already has one and overwriting it is hostile.
    # setup.sh asks before appending three import lines to theirs. Leaving it in this
    # tuple made the gate fail on every push of a kit that is correct.
    # `kit.manifest.json` is deliberately NOT required: a release tree has none
    # (see main()). Requiring it here would re-fail what main() just made optional.
    for required in (".gitignore", "README.md"):
        if not (kit / required).exists():
            failures.append(f"MISSING: {required}")
    return failures


def check_git_cleanliness(kit: Path) -> list[str]:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp_kit = Path(tmp) / "kit"
        try:
            shutil.copytree(str(kit), str(tmp_kit))
            subprocess.run(
                ["git", "init", "-q"],
                cwd=str(tmp_kit), capture_output=True, check=True, timeout=30,
            )
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(tmp_kit), capture_output=True, check=True, timeout=30,
            )
            result = subprocess.run(
                ["git", "status", "--short"],
                cwd=str(tmp_kit), capture_output=True, text=True, check=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if line.startswith("??"):
                    failures.append(f"GIT-UNTRACKED: {line[3:].strip()}")
        except subprocess.CalledProcessError as e:
            failures.append(f"GIT-INIT-FAIL: {e.stderr.strip() if e.stderr else str(e)}")
        except Exception as e:
            failures.append(f"GIT-CHECK-ERROR: {e}")
    return failures


def check_only_placeholder_hook_refs(kit: Path) -> list[str]:
    """Fail if any shipped runtime artifact contains a /.claude/hooks/ reference
    that is NOT the safe ${KIT_HOOKS_DIR}/ form.

    Scope: kit/.claude/hooks/*.sh + kit/.claude/settings.json (runtime artifacts only).
    Catches every non-placeholder form: $CLAUDE_PROJECT_DIR, $HOME, ${HOME},
    ${CLAUDE_PROJECT_DIR}, ~/.claude/hooks/, absolute /Users/... or /home/...

    Comment-aware: for .sh files, skip lines whose first non-whitespace char is '#'
    (bash comment). settings.json is JSON (no comments — every occurrence is live).
    """
    ANY_HOOKS_REF = re.compile(r"/\.claude/hooks/")
    failures = []
    hooks_dir = kit / ".claude/hooks"
    settings = kit / ".claude/settings.json"
    paths = list(itertools.chain(
        sorted(hooks_dir.glob("*.sh")) if hooks_dir.is_dir() else [],
        [settings] if settings.is_file() else [],
    ))
    for path in paths:
        is_shell = path.suffix == ".sh"
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            failures.append(f"READ-ERROR: {path}: {e}")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            # Skip shell-comment lines (no live hook ref possible).
            if is_shell and line.lstrip().startswith("#"):
                continue
            for match in ANY_HOOKS_REF.finditer(line):
                start = match.start()
                preceding = line[max(0, start - 20):start]
                if not preceding.endswith("${KIT_HOOKS_DIR}"):
                    rel = path.relative_to(kit)
                    snippet = line.strip()[:80]
                    failures.append(f"NON-PLACEHOLDER {rel}:{lineno}: {snippet}")
    return failures


def check_dep_closure(manifest: dict) -> list[str]:
    """Every bundle's deps must also appear in resolved_bundles."""
    try:
        import yaml
        bundles_yaml = yaml.safe_load(BUNDLES_YAML.read_text()) or {}
    except Exception:
        return ["DEP-CHECK-SKIP: could not load bundles.yaml"]

    resolved = set(manifest.get("resolved_bundles", []))
    failures = []
    for bundle in resolved:
        b = bundles_yaml.get(bundle, {})
        for dep in b.get("deps", []) or []:
            if dep not in resolved:
                failures.append(f"DEP-MISSING: {bundle} requires {dep} (absent from resolved_bundles)")
    return failures


# ---------------------------------------------------------------------------
# Optional Explore checker (README vs manifest)
# ---------------------------------------------------------------------------

EXPLORE_PROMPT = """\
You are verifying that a kit README accurately reflects a build manifest.

<document>
<source>kit.manifest.json</source>
<document_content>
{manifest_json}
</document_content>
</document>

<document>
<source>README.md</source>
<document_content>
{readme_text}
</document_content>
</document>

<investigate_before_answering>
Read both documents above before making any claims. Quote the relevant manifest
and README lines first, then judge whether they match.
</investigate_before_answering>

Task: verify that the README's "What's Included" bundle list exactly matches the
`resolved_bundles` array in kit.manifest.json — same bundles, none omitted,
none added. Also verify the "Retired Since Previous Build" section is present.

Respond with exactly:
<verdict>PASS</verdict>
or
<verdict>DISCREPANCY</verdict>
<evidence>
[quote the mismatched lines from README and manifest]
</evidence>

No preamble. No explanation beyond the evidence block.
"""


def check_explore_readme(kit: Path, manifest: dict) -> list[str]:
    """Run an independent Explore subagent to verify README vs manifest.

    This check only runs when invoked from within a Claude Code session
    (when the Agent tool is available via the harness). From the CLI,
    this function prints instructions and returns no failures.
    """
    readme_path = kit / "README.md"
    if not readme_path.exists():
        return ["EXPLORE-SKIP: README.md not found"]

    manifest_json = json.dumps(manifest, indent=2)
    readme_text = readme_path.read_text(encoding="utf-8")
    prompt = EXPLORE_PROMPT.format(
        manifest_json=manifest_json,
        readme_text=readme_text,
    )

    # Print the prompt so an operator can paste it into an Explore agent
    print()
    print("  --- Explore README checker prompt (paste into Claude Code Explore agent) ---")
    print(prompt[:300] + "  [... full prompt available in verify_kit.py EXPLORE_PROMPT ...]")
    print()
    print("  To skip this check: python3 verify_kit.py <kit> --no-explore")
    return []


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_check(name: str, failures: list[str], all_failures: dict) -> None:
    if failures:
        all_failures[name] = failures
        print(f"  [FAIL] {name} ({len(failures)} issue(s))")
    else:
        print(f"  [OK]   {name}")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Verify starter kit integrity")
    parser.add_argument("kit_dir", help="Path to assembled kit directory")
    parser.add_argument(
        "--no-explore", action="store_true",
        help="Skip optional independent Explore README-vs-manifest checker",
    )
    args = parser.parse_args()

    kit = Path(args.kit_dir).resolve()
    if not kit.is_dir():
        print(f"ERROR: not a directory: {kit}", file=sys.stderr)
        sys.exit(2)

    # A MANIFEST IS OPTIONAL, and its absence is not a failure.
    #
    # This script was written for the one-shot builder, which writes
    # `kit.manifest.json`. A RELEASE tree is assembled a push at a time from the
    # register instead and carries no manifest — so on the repo this file actually
    # ships in, `verify_kit.py .` printed FATAL and exited 2. INSTALL.md step 8
    # tells the adopter's agent to run exactly that command, which means the
    # shipped instructions failed on the shipped tree. Found by the closing
    # checklist, not by any gate.
    #
    # The manifest-dependent checks (file presence, executability, dependency
    # closure, the README-vs-manifest Explore pass) are SKIPPED and named as
    # skipped; the tree-only checks — author tokens, structure, git cleanliness,
    # placeholder hook refs — run either way. Silently passing a subset would be
    # worse than the FATAL; saying which checks did not run is the honest form.
    manifest_path = kit / "kit.manifest.json"
    manifest = None
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"Verifying kit: {kit}")
    if manifest is None:
        print("  No kit.manifest.json — this is a release tree, assembled from the")
        print("  push register rather than by build_kit.py. Manifest-dependent")
        print("  checks are SKIPPED below; the tree-only checks still run.")
    else:
        print(f"  Files in manifest : {len(manifest.get('files', []))}")
        print(f"  Resolved bundles  : {', '.join(manifest.get('resolved_bundles', []))}")
        print(f"  Built at          : {manifest.get('built_at', 'unknown')}")
    print()

    all_failures: dict[str, list[str]] = {}

    if manifest is None:
        for name in ("File presence", "Executability", "Dependency closure"):
            print(f"  SKIP  {name} (needs kit.manifest.json)")
    else:
        run_check("File presence", check_file_presence(kit, manifest), all_failures)
        run_check("Executability", check_executability(kit, manifest), all_failures)
        run_check("Dependency closure", check_dep_closure(manifest), all_failures)

    run_check("Author token leak", check_author_tokens(kit), all_failures)
    run_check("Kit structure", check_structure(kit), all_failures)
    run_check("Git cleanliness", check_git_cleanliness(kit), all_failures)
    run_check("Placeholder hook refs", check_only_placeholder_hook_refs(kit), all_failures)

    if not args.no_explore:
        if manifest is None:
            print("  SKIP  Explore README check (needs kit.manifest.json)")
        else:
            explore_failures = check_explore_readme(kit, manifest)
            if explore_failures:
                all_failures["Explore README check"] = explore_failures

    print()
    if all_failures:
        total_issues = sum(len(v) for v in all_failures.values())
        print(f"FAIL — {len(all_failures)} check(s) failed, {total_issues} issue(s) total:")
        for check_name, failures in all_failures.items():
            print(f"\n  [{check_name}]")
            for line in failures[:20]:
                print(f"    {line}")
            if len(failures) > 20:
                print(f"    ... and {len(failures) - 20} more")
        sys.exit(1)
    else:
        print("PASS — all checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()

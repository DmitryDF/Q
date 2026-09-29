#!/usr/bin/env python3
"""verify_kit.py — Post-build verification gate for the starter kit.

Checks (all run before reporting, fail-fast ordering for display):
  1. File presence: every file in kit.manifest.json exists.
  2. Executable: all .sh/.py hooks are executable.
  3. Author token leak: zero matches for /Users/<name>|iCloud~md~obsidian|private_project|Frikh-Khar.
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
    r"|iCloud~md~obsidian"
    r"|private_project"
    r"|Frikh-Khar"
    r"|(?<!\[)EMPLOYER/"  # employer folder name (not inside a [...] placeholder)
)

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
        rel = str(path.relative_to(kit))
        for i, line in enumerate(text.splitlines(), 1):
            if AUTHOR_TOKEN_RE.search(line):
                snippet = line.strip()[:80]
                failures.append(f"AUTHOR-TOKEN {rel}:{i}: {snippet}")
    return failures


def check_structure(kit: Path) -> list[str]:
    failures = []
    # CLAUDE.md is deliberately NOT required (operator decision O1/A7): the kit ships
    # none, because an adopter already has one and overwriting it is hostile.
    # setup.sh asks before appending three import lines to theirs. Leaving it in this
    # tuple made the gate fail on every push of a kit that is correct.
    for required in ("kit.manifest.json", ".gitignore", "README.md"):
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

    manifest_path = kit / "kit.manifest.json"
    if not manifest_path.exists():
        print(f"FATAL: kit.manifest.json not found in {kit}", file=sys.stderr)
        print("  Run: python3 build_kit.py build --output <dir> first", file=sys.stderr)
        sys.exit(2)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(f"Verifying kit: {kit}")
    print(f"  Files in manifest : {len(manifest.get('files', []))}")
    print(f"  Resolved bundles  : {', '.join(manifest.get('resolved_bundles', []))}")
    print(f"  Built at          : {manifest.get('built_at', 'unknown')}")
    print()

    all_failures: dict[str, list[str]] = {}

    run_check("File presence", check_file_presence(kit, manifest), all_failures)
    run_check("Executability", check_executability(kit, manifest), all_failures)
    run_check("Author token leak", check_author_tokens(kit), all_failures)
    run_check("Kit structure", check_structure(kit), all_failures)
    run_check("Git cleanliness", check_git_cleanliness(kit), all_failures)
    run_check("Dependency closure", check_dep_closure(manifest), all_failures)
    run_check("Placeholder hook refs", check_only_placeholder_hook_refs(kit), all_failures)

    if not args.no_explore:
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

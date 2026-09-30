"""Which registered research source kinds have a production driver.

WHY THIS IS A MODULE AND NOT A TEST. The invariant below shipped first inside
`hooks/tests/test_source_admission_port.py`, where nothing ran it: `config-verify`
— the canonical pre-ship check (`land_port.py:64-67`) — runs CLI-verb checks and
zero tests, and the only working pytest invocation under `~/.claude` is
`uv run --with pytest`, which would put a network fetch in the pre-bookend of
every promotion. So the *form* an invariant is authored in decides the cost of
enforcing it, and at pytest prices the answer is reliably "no". Authored as a
sub-second module CLI, it is priced at what the gate already pays six times.

WHAT THIS IS NOT. It is NOT containment and must never be cited as enforcement.
Nothing gates the tool boundary — a read that ignores the port entirely is still
possible (see `internal_kb.py`'s module docstring; design-A18 is withdrawn). A
driver existing means a kind is *reachable*, not that reads are *confined*.

STATED LIMITS OF THE DERIVATION (carried verbatim from where it was authored).
  * It is textual/AST, so a caller reaching the port via a wrapper, a dynamic
    import or a re-export is a FALSE NEGATIVE it cannot see.
  * Kind attribution is per-FILE: a kind merely *named* in a file that drives
    some other kind is a FALSE POSITIVE. This leans opposite to the usual
    preference and is stated rather than hidden.
  * It keys on the method NAME `admit`, not on the port type. A non-port
    `.admit()` already exists (`_factcheck_engine.py`, `_WebAdmitter`) and is
    matched; harmless only because that file is independently a real driver.
  * It measures reachability of the PORT, not of the picker. A driver wired but
    not routed still reads as driven; person-level offerability is
    `source_picker.is_selectable` and is not what this measures.

ROOTING — THE PROPERTY THIS MODULE EXISTS TO GET RIGHT. Every path resolves from
`Path(__file__)`, never from `CLAUDE_CONFIG_DIR` and never from `Path.home()`.
The deploy-check runs this against a *rendered candidate* with a sanitized env
built from `land_port.py`'s `DEFAULT_ENV_ALLOWLIST`, which simply omits
`CLAUDE_CONFIG_DIR` — so an env-rooted fallback would silently read live — the
split-brain where the tree scan says one thing and the registry says another.
`scope_record` is therefore loaded BY PATH from the same root, under a name
unique to that root: importing it by bare name would let a live copy already
in `sys.modules` answer for a candidate tree.
Precedents for the self-relative idiom: `bookkeeping_paths.py:44`,
`harness_code_paths.py:52`, `output_security_registry.py:840-842`.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

#: Registered kinds with no production driver, each with WHY and WHO owns it.
#: A kind here is exempt from the invariant below — and the exemption is itself
#: checked in both directions, so a stale entry fails just as a missing one does.
#:
#: **EMPTY, and that is the shipped end state for both kinds that ever sat here.**
#: `linear` was registered by S8 Session 1 (locator grammar, the two registries,
#: the probe arm, the containment arm) and is DRIVEN by S8 Session 3, which builds
#: `adapters/linear.py` and its arm in `declared_read`. Between those two sessions
#: the kind was registered with no driver — exactly the combination the invariant
#: below refuses — so the exemption is what made the intermediate state shippable,
#: and its deletion is part of Session 3's own commit rather than a follow-up.
#:
#: **The two directions are what forced that, and it is worth recording why the
#: alternative was not available.** Session 3 was asked to hold this row back until
#: its closing verification had walked the chain against a real workspace, so that
#: nobody could be OFFERED Linear on the strength of an unproven read. That state
#: is not expressible: `check()` fails a driven kind that still carries an
#: exemption just as it fails an undriven kind that lacks one, so the driver and
#: the row cannot coexist. There is no "driven but not yet offered" state, and a
#: second withholding mechanism invented to create one would be a mechanism written
#: only to be reverted. The row therefore goes when the driver lands, and the real
#: read is proven immediately after rather than before.
#:
#: It previously carried exactly one entry, `code`, from the slice that shipped the
#: adapter until the slice that wired the
#: driver (`skills/research/declared_read.py`). That entry was deleted in the
#: driver's own commit rather than as a follow-up: `check()` below fails with
#: "NOW DRIVEN — its exemption is stale" the moment a driver exists, and among the
#: gates that run AUTOMATICALLY it runs only on the deploy path (`config-verify`
#: has no test lane), so a split commit clears everything that fires on its own and
#: then fails at promotion. `hooks/tests/test_code_admission.py` asserts it too,
#: but only for whoever runs that suite.
#:
#: A mapping that empties out is not a dead constant. `check()` reads it in both
#: directions every run, so the next kind registered without a driver fails here
#: with no literal to edit — and the exemption-hygiene arms below are exercised by
#: their own controls as well as by a live entry.
#:
#: SECOND READER, DELIBERATE — AND THE TWO HALVES ARE A PAIR.
#: `source_picker.is_selectable` reads this mapping so a class RECORDED as having
#: no driver cannot be OFFERED to a person. That reader does not derive driver
#: presence and cannot: a kind registered with no driver and no entry here would
#: still be offered. `check()` is what makes that combination unshippable, by
#: failing a registered kind that has neither. The gate compels the record; the
#: picker consumes it. Neither half generalises alone — this gate alone was
#: already in place while `code` sat here exempt and stayed tickable for four
#: slices. The borrowing is also conservative in one direction only: see the
#: stated limits above and the call site's own comment.
KNOWN_UNREACHABLE: Mapping[str, Tuple[str, str]] = {}

#: Scan roots, named relative to the config root. Named explicitly so a sibling
#: `hooks.bak-*` tree is out by construction rather than by filter.
SCAN_ROOT_NAMES = ("hooks", "skills")

#: Where `scope_record` lives relative to the config root.
SCOPE_RECORD_RELPATH = ("skills", "research", "scope_record.py")


class ReachabilityError(RuntimeError):
    """The derivation could not run — as distinct from running and finding a gap.

    Raised when the target tree has no readable `scope_record`. It must FAIL, not
    skip: a candidate whose registry cannot be read is a broken candidate, and
    skipping would let it pass as green.
    """


# --------------------------------------------------------------------------- #
# Rooting
# --------------------------------------------------------------------------- #

def config_root() -> Path:
    """The config tree this module lives in — so the check works in a clone and in live.

    `skills/research/kind_reachability.py` → `parents[2]` is the config root.
    """
    return Path(__file__).resolve().parents[2]


def _tree_key(root: Path) -> str:
    """A `sys.modules` name unique to `root`.

    Load-bearing for the two-tree property: `importlib` caches by NAME, so a
    fixed name would make the second tree's `scope_record` resolve to the first
    tree's object — reproducing, inside the check, exactly the cross-tree bleed
    the check exists to exclude.
    """
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:12]
    return f"_kind_reachability_scope_record_{digest}"


def load_scope_record(root: Optional[Path] = None):
    """Load `scope_record` from `root`, by path, under a root-unique name."""
    root = config_root() if root is None else Path(root).resolve()
    path = root.joinpath(*SCOPE_RECORD_RELPATH)
    if not path.is_file():
        raise ReachabilityError(
            f"scope_record not found under the tree being checked ({path}) — "
            "the source-kind registry cannot be read, so reachability cannot be "
            "derived. This is a FAILURE, not a skip: a candidate whose registry "
            "is unreadable must not pass as green.")
    name = _tree_key(root)
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ReachabilityError(f"could not load scope_record from {path}")
    module = importlib.util.module_from_spec(spec)
    # Registered under its unique name BEFORE exec: `dataclasses` resolves string
    # annotations through `sys.modules[cls.__module__]`, which is None for a
    # module that was never registered.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:                      # pragma: no cover - defensive
        sys.modules.pop(name, None)
        raise ReachabilityError(f"scope_record at {path} failed to import: {exc}")
    return module


def scan_roots(root: Optional[Path] = None) -> Tuple[Path, ...]:
    root = config_root() if root is None else Path(root).resolve()
    return tuple(root / name for name in SCAN_ROOT_NAMES)


# --------------------------------------------------------------------------- #
# Derivation
# --------------------------------------------------------------------------- #

def _is_test_path(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    return ("tests" in rel.parts
            or path.name.startswith("test_")
            or path.name.startswith("conftest"))


def production_files(roots: Sequence[Path]) -> Iterable[Path]:
    """Production `.py` under the NAMED roots only.

    `.bak-` is filtered on ANY path part, which is defence rather than a live
    need: every `.bak-` path under these roots today is a basename case that the
    `*.py` glob already drops.
    """
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(".bak-" in part for part in path.parts):
                continue
            if "__pycache__" in path.parts:
                continue
            if _is_test_path(path, root):
                continue
            if path.name == "source_port.py":
                continue          # the port defines admit(); it does not drive it
            yield path


def _parse(path: Path):
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return None


def kind_values(tree, srec) -> Set[str]:
    """Source-kind VALUES referenced in a module.

    Two things here are load-bearing and were both defects before they were fixed:

    1. Both `ast.Name.id` AND `ast.Attribute.attr` are inspected. EVERY source-kind
       reference in both real driver files is Attribute-form (`self._sr.KIND_WEB`,
       `_scope.KIND_KNOWLEDGE_LIBRARY`, ...). A Name-only visitor derives ZERO
       kinds from both and produces a 100% false block on a correct codebase.
    2. `getattr(..., None)` — the default, not a bare getattr. `_factcheck_engine`
       carries `KIND_PROMPT_TEMPLATES`, a `KIND_*` name `scope_record` does not
       define; a defaultless getattr raises AttributeError outside the `ast.parse`
       guard and aborts the whole derivation.
    """
    registered = set(srec.REGISTERED_KINDS)
    values: Set[str] = set()
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name) and node.id.startswith("KIND_"):
            name = node.id
        elif isinstance(node, ast.Attribute) and node.attr.startswith("KIND_"):
            name = node.attr
        if name is None:
            continue
        value = getattr(srec, name, None)
        if isinstance(value, str) and value in registered:
            values.add(value)
    return values


def drives_port(tree) -> bool:
    """True iff the module contains a real `.admit(` CALL.

    An `ast.Call` test, never a substring one. A token scan for `AdmissionPort` /
    `source_port` / `.admit(` matches many production files — importers and
    docstring mentions included — several of which reference `KIND_CODE`, so kind
    attribution over that set returns `code: driven` and inverts the very fact
    this module exists to pin.
    """
    return any(isinstance(n, ast.Call)
               and isinstance(n.func, ast.Attribute)
               and n.func.attr == "admit"
               for n in ast.walk(tree))


def derive_driver_kinds(root: Optional[Path] = None,
                        roots: Optional[Sequence[Path]] = None,
                        srec=None) -> Dict[str, Set[str]]:
    """{driver-file basename -> {source-kind values it can serve}}.

    `root` is the interesting parameter: it moves BOTH the tree scan and the
    registry read together. `roots` narrows the scan only and exists for the
    scratch-tree controls; passing it alone leaves the registry on the default
    tree, which is why it does not satisfy the two-tree property.
    """
    if srec is None:
        srec = load_scope_record(root)
    scan = tuple(roots) if roots is not None else scan_roots(root)
    out: Dict[str, Set[str]] = {}
    for path in production_files(scan):
        tree = _parse(path)
        if tree is None or not drives_port(tree):
            continue
        out[path.name] = kind_values(tree, srec)
    return out


def derive_port_run_callers(root: Optional[Path] = None,
                            roots: Optional[Sequence[Path]] = None) -> List[str]:
    """Production callers of `AdmissionPort.run()`, as `basename:lineno`.

    Receiver-qualified on purpose. A bare substring scan for `.run(` is useless
    here — most `.run(` in `skills/` is `subprocess.run(`. Both receiver shapes
    are accepted: `port.run(...)` (Name) and `self._port.run(...)` (Attribute).
    """
    scan = tuple(roots) if roots is not None else scan_roots(root)
    callers: List[str] = []
    for path in production_files(scan):
        tree = _parse(path)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            if n.func.attr != "run":
                continue
            recv = n.func.value
            if isinstance(recv, ast.Name):
                recv_name = recv.id
            elif isinstance(recv, ast.Attribute):
                recv_name = recv.attr
            else:
                continue
            if recv_name == "port" or recv_name.endswith("_port"):
                callers.append(f"{path.name}:{n.lineno}")
    return sorted(callers)


def route(kind: str) -> str:
    """What a reader should DO about `kind`, from metadata — never a fixed slice.

    A kind already recorded as unreachable reports its own owner. A kind that is
    not recorded is newly registered, so the slice that registered it owns the
    decision. Hardcoding one slice name would misroute every case but that one.
    """
    entry = KNOWN_UNREACHABLE.get(kind)
    if entry:
        return f"owned by {entry[1]} — recorded reason: {entry[0]}"
    return ("newly registered with no driver: the slice that registered it must "
            "either wire a production driver through AdmissionPort.admit(), or "
            "add a KNOWN_UNREACHABLE entry in "
            "skills/research/kind_reachability.py with a reason and an owner. "
            "The next kind expected to reach this point is Linear (S8).")


# --------------------------------------------------------------------------- #
# The invariant
# --------------------------------------------------------------------------- #

def unreachable_statement(root: Optional[Path] = None, srec=None) -> str:
    """The affirmative form: what is unreachable, and what is driven.

    This is the GREEN-run statement. It was written because `code` could not
    mismatch while its exemption STOOD, so without it a green run said nothing
    about the one kind the whole thing was about. That exemption is now gone and
    `code` derives as driven, so the statement's value has changed rather than
    lapsed: it is what makes a green run SAY which kinds are driven and which are
    excepted, instead of being silent about both. The same reasoning will apply to
    the next kind that arrives with an exemption.

    It is DATA about the invariant, not a second assertion of it — which is why
    the suite may render it (A4) without breaking single authority.
    """
    if srec is None:
        srec = load_scope_record(root)
    per_file = derive_driver_kinds(root, srec=srec)
    driven = set().union(*per_file.values()) if per_file else set()
    exempt = "; ".join(
        f"{k} is UNREACHABLE ({KNOWN_UNREACHABLE[k][0]}; {KNOWN_UNREACHABLE[k][1]})"
        for k in sorted(KNOWN_UNREACHABLE)) or "nothing recorded unreachable"
    return (f"source-kind reachability: {exempt} | "
            f"driven: {', '.join(sorted(driven)) or '(none)'}")


def check(root: Optional[Path] = None) -> List[str]:
    """Return the problems found. Empty list means the invariant holds.

    Checked in BOTH directions, so neither side can rot. The expected set is
    generated from `REGISTERED_KINDS`, so registering a kind with no driver fails
    with no literal to edit; wiring a driver for an excepted kind fails the other
    way until its exception is removed.

    MESSAGE ORDER IS LOAD-BEARING, not style. On the only path that runs this,
    the gate keeps `chk.stderr.strip()[:400]` — the first 400 characters of
    AGGREGATE stderr from every check (`land_port.py:963`) — so a message that
    buries the kind name is a message nobody gets. Element 0 names the kinds
    within its first 80 characters; remedy and owner follow behind it.
    """
    srec = load_scope_record(root)
    registered = set(srec.REGISTERED_KINDS)
    problems: List[str] = []

    # Exemption hygiene first in evaluation, appended after the invariant lines
    # so the invariant keeps the front of the budget when both fire.
    exemption_problems: List[str] = []
    for kind, entry in KNOWN_UNREACHABLE.items():
        if kind not in registered:
            exemption_problems.append(
                f"exemption {kind!r} STALE — excepted but not a registered kind")
            continue
        if not (isinstance(entry, tuple) and len(entry) == 2):
            exemption_problems.append(
                f"exemption {kind!r} MALFORMED — expected (reason, owner)")
            continue
        reason, owner = entry
        if not str(reason).strip():
            exemption_problems.append(f"exemption {kind!r} carries no reason")
        if not str(owner).strip():
            exemption_problems.append(f"exemption {kind!r} names no owner")

    per_file = derive_driver_kinds(root, srec=srec)
    driven = set().union(*per_file.values()) if per_file else set()

    undriven = sorted((registered - set(KNOWN_UNREACHABLE)) - driven)
    if undriven:
        names = ", ".join(repr(k) for k in undriven)
        problems.append(
            f"source kind {names} UNREACHABLE — registered, no production driver")
        problems.extend(f"    {k}: {route(k)}" for k in undriven)

    now_driven = sorted(set(KNOWN_UNREACHABLE) & driven)
    if now_driven:
        names = ", ".join(repr(k) for k in now_driven)
        problems.append(
            f"source kind {names} NOW DRIVEN — its exemption is stale")
        problems.extend(
            f"    {k}: delete its KNOWN_UNREACHABLE entry in "
            f"skills/research/kind_reachability.py. {route(k)}"
            for k in now_driven)

    problems.extend(exemption_problems)
    return problems


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _cmd_check(args) -> int:
    root = Path(args.root).resolve() if args.root else None
    try:
        problems = check(root)
    except ReachabilityError as exc:
        print(f"source-kind reachability COULD NOT RUN — {exc}", file=sys.stderr)
        return 2
    if problems:
        for line in problems:
            print(line, file=sys.stderr)
        return 1
    # Silent on the gated path by construction: the deploy-check captures and
    # discards stdout on green (`config-verify` vlogs it, `land_port.py:967-984`
    # drops it). The affirmative form is here for a by-hand run, and in the suite.
    print(unreachable_statement(root))
    return 0


def _cmd_statement(args) -> int:
    root = Path(args.root).resolve() if args.root else None
    try:
        print(unreachable_statement(root))
    except ReachabilityError as exc:
        print(f"source-kind reachability COULD NOT RUN — {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kind_reachability",
        description="Which registered research source kinds have a production driver.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser(
        "check", help="Fail when a registered kind has no driver, or an exemption is stale.")
    p_check.add_argument("--root", default=None,
                         help="Config root to check (default: the tree this module lives in).")
    p_check.set_defaults(func=_cmd_check)

    p_stmt = sub.add_parser(
        "statement", help="Print the reachability statement and exit 0.")
    p_stmt.add_argument("--root", default=None)
    p_stmt.set_defaults(func=_cmd_statement)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":                                    # pragma: no cover
    raise SystemExit(main())

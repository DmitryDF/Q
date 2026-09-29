#!/usr/bin/env python3
"""Validation rigor — the one dial, resolved at dispatch.

WHY THIS MODULE EXISTS
----------------------
Verification costs tokens, and an adopter on a different budget should be able to
turn the dial rather than turn the system off. Before this module the dial was a
claim: `Q_VALIDATION_RIGOR` was asked for at install and documented in INSTALL.md,
and **nothing read it** — every skill named its own checker counts inline, so an
adopter who chose `minimal` still got a three-checker panel thirteen times in
`/solution-design`. A setting that governs nothing is worse than no setting: it
tells the operator a false thing about the system.

Two properties follow from the operator's own two requirements, and both are
structural rather than advisory:

  1. the configured tier must actually govern the dispatch sites, and
  2. it must be changeable AFTER install — so it is read AT DISPATCH, never
     baked in at setup time. That is why this is a module a skill calls and not
     a number a setup script writes into a skill.

WHAT NEVER MOVES, at any tier
-----------------------------
  * producer-never-verifies. Every tier still dispatches at least one ISOLATED
    checker that is not the producer, with no shared context
    (`code_first_architecture.md`). A tier that dispatched none would be
    self-assessment wearing a verification label. The floor is not on the dial.
  * the fixed pipelines. `factcheck-convergence.md` §1 — "the 3-checker default
    is unchanged for all fixed pipelines". Plan gates, research fact-check and KL
    extraction keep the canon allocation whatever the setting says. That is what
    the `fixed` site class below is for: a site that asks the dial for a canon
    pipeline gets canon back, at every tier, rather than a cheaper answer.

WHAT DOES MOVE, and the honest cost
-----------------------------------
At `thorough` three binding checkers must AGREE — the voting pattern of
`factcheck-convergence.md` §1. At `light` and `minimal` there is exactly one
binding checker, so there is no vote: one isolated opinion, not a consensus.
Much better than self-assessment; not the same guarantee. Every surface that
reports a tier says so rather than implying the tiers differ only in price.

THE DEFAULT IS `standard`, WHICH IS NOT TODAY'S BEHAVIOUR
---------------------------------------------------------
The harness this framework was extracted from runs at `thorough` — three Sonnet
plus an Opus advisory at its blocking gates. The shipped default is `standard`
because that is the right recommendation for a new adopter, not because it
reproduces the author's setup. An operator who wants the original behaviour sets
the tier explicitly; `rigor.py set thorough` is that, and it is one command.

Layers, per the trust hierarchy in `code_first_architecture.md`: this module is
Layer 1 (code — the allocation table and the resolution order cannot be argued
with). The skills' prose that calls it is Layer 3, which is exactly why
`conformance()` below exists — it is the code check that a skill did not go back
to naming numbers.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Tiers
# ---------------------------------------------------------------------------

TIERS: tuple[str, ...] = ("thorough", "standard", "light", "minimal")
DEFAULT_TIER = "standard"
TIER_ENV = "Q_VALIDATION_RIGOR"
CONFIG_BASENAME = "q-rigor.json"

TIER_LABELS: dict[str, str] = {
    "thorough": "3 binding checkers must agree, plus an Opus advisory (a vote; highest cost)",
    "standard": "1 binding Sonnet checker, plus an Opus advisory (no vote)",
    "light": "a single Opus checker — more capable, still no vote",
    "minimal": "a single Sonnet checker — the cheapest allocation that still verifies",
}

TIER_COSTS: dict[str, str] = {
    "thorough": "highest",
    "standard": "moderate",
    "light": "low",
    "minimal": "lowest",
}

# ---------------------------------------------------------------------------
# Site classes
# ---------------------------------------------------------------------------
#
# Per-site-CLASS, not one flat number, because the stakes differ. A gate whose
# verdict decides whether the work proceeds is not the same call as a sanity
# check whose discrepancy causes a revision, and neither is a generation panel
# where N is the skill's design rather than its confidence.

SITE_CLASSES: dict[str, str] = {
    "gate": "the verdict BLOCKS — the work does not proceed on a DISCREPANCY",
    "check": "routine verification — a discrepancy causes a revision, not a stop",
    "panel": "multi-perspective GENERATION (proposers, critics, designers) — the "
             "dial is a CAP on the site's own authored count, not a replacement",
    "fixed": "a canon-locked pipeline — NOT on the dial at any tier",
}

# The canon allocation the fixed pipelines keep, quoted from
# factcheck-convergence.md §1 / code_first_architecture.md: three independent
# Sonnet checkers that must agree.
FIXED_CANON_FLAGS = "--sonnet 3"


@dataclass(frozen=True)
class Allocation:
    """One resolved allocation. `flags` is what a caller pastes at the dispatch."""

    site_class: str
    tier: str
    flags: str
    binding: dict[str, int] = field(default_factory=dict)
    advisory: dict[str, int] = field(default_factory=dict)
    vote: bool = False
    note: str = ""

    @property
    def total_binding(self) -> int:
        return sum(self.binding.values())

    def as_dict(self) -> dict:
        return {
            "site_class": self.site_class,
            "tier": self.tier,
            "flags": self.flags,
            "binding": dict(self.binding),
            "advisory": dict(self.advisory),
            "vote": self.vote,
            "total_binding": self.total_binding,
            "note": self.note,
        }


# Verification allocations, per (class, tier).
#
# Every entry below is legal under `_factcheck_engine.validate_allocation`:
# binding counts come from DC_ALLOCATION_ALLOWED_COUNTS = (1, 3, 4), N=2 is
# disallowed, a family with no binding checkers is ABSENT from the map rather
# than present as zero, and the Opus checker is advisory (non-binding) whenever a
# Sonnet panel is present. `selftest()` asserts this against the engine rather
# than trusting the comment.
#
# `check` is deliberately identical at `thorough` and `standard`. At `thorough`
# the escalation budget goes to the sites whose verdict BLOCKS; a non-blocking
# check that finds a discrepancy already triggers a revision, so a vote there
# buys less per token than a vote at a gate. Stated because an identical row
# otherwise reads like an oversight.
_VERIFY: dict[str, dict[str, dict]] = {
    "gate": {
        "thorough": {"binding": {"sonnet": 3}, "advisory": {"opus": 1}, "vote": True},
        "standard": {"binding": {"sonnet": 1}, "advisory": {"opus": 1}, "vote": False},
        "light": {"binding": {"opus": 1}, "advisory": {}, "vote": False},
        "minimal": {"binding": {"sonnet": 1}, "advisory": {}, "vote": False},
    },
    "check": {
        "thorough": {"binding": {"sonnet": 1}, "advisory": {"opus": 1}, "vote": False},
        "standard": {"binding": {"sonnet": 1}, "advisory": {"opus": 1}, "vote": False},
        "light": {"binding": {"opus": 1}, "advisory": {}, "vote": False},
        "minimal": {"binding": {"sonnet": 1}, "advisory": {}, "vote": False},
    },
}

# Generation panels: a CEILING on the site's authored count, never a replacement.
#
# Why a cap rather than a count: for a panel, N is part of the skill's design —
# `/recommend` proposes one bounded move by default and `/challenge --mode
# surface_oqs` wants two agents specifically to widen the question set. Replacing
# those with one dial number would flatten deliberate design choices into a
# budget setting and would change what the skills DO at the default tier. A cap
# leaves every authored default intact at `thorough`/`standard` and shrinks the
# wide panels when the operator asked for a smaller budget.
_PANEL_CAP: dict[str, int] = {
    "thorough": 3,
    "standard": 2,
    "light": 1,
    "minimal": 1,
}


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

CONFIG_ENV = "Q_RIGOR_CONFIG"


def config_path() -> Path:
    """Where the tier is stored. Deliberately NOT settings.json.

    `settings.json` and `hooks/*` are the two paths the verifier-isolation guard
    snapshots (`code_first_architecture.md` §Verification Isolation); a setting a
    skill may rewrite at the operator's request has no business inside that
    scope. A sibling file at the config root is writable without arming a drift
    alarm on every change.

    The root is THIS MODULE'S OWN install — `hooks/rigor.py` → its parent's
    parent — rather than `CLAUDE_CONFIG_DIR`. The kit installs two ways (global
    into `~/.claude`, or project-scoped with the tree left in place), and a
    resolver that read the environment would answer for the wrong install
    whenever those two disagree. Asking "which tree do I live in" cannot
    disagree with itself.

    `$Q_RIGOR_CONFIG` overrides with an explicit file path — the seam a test
    uses so it never writes to a real install.
    """
    override = os.environ.get(CONFIG_ENV)
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parent.parent / CONFIG_BASENAME


def read_config() -> dict:
    """The stored settings, or {} — a missing or corrupt file is never fatal.

    A malformed config must degrade to the default tier, not to a traceback in
    the middle of someone's session.
    """
    p = config_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_config(tier: str, *, set_by: str = "rigor.py") -> tuple[str, Path]:
    """Store the tier atomically. Returns (normalised tier, path written).

    Raises ValueError on an unknown tier: `set` is an explicit operator act, and
    silently storing something other than what they typed would leave the file
    disagreeing with their intent. The forgiving path is READ-side resolution,
    which must never fail — not the write side, which has someone present.
    """
    norm = (tier or "").strip().lower()
    if norm not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; choose one of {', '.join(TIERS)}")
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "tier": norm,
        "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "set_by": set_by,
    }
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".q-rigor-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return norm, p


def resolve_tier(name: str | None = None) -> tuple[str, str]:
    """Resolve the active tier. Returns (tier, where-it-came-from).

    Precedence: explicit argument > environment > stored config > default. The
    environment sits above the file because an exported value is the deliberate
    per-session override, and a file the operator set weeks ago should not win
    against a choice they are making right now.

    An unrecognised value anywhere resolves to the DEFAULT and says so — never
    to "no verification". Degrading to zero checkers on a typo is the one
    failure mode this whole system exists to prevent.
    """
    for raw, origin in (
        (name, "argument"),
        (os.environ.get(TIER_ENV), f"${TIER_ENV}"),
        (read_config().get("tier"), str(config_path())),
    ):
        if raw is None:
            continue
        norm = str(raw).strip().lower()
        if not norm:
            continue
        if norm in TIERS:
            return norm, origin
        return DEFAULT_TIER, f"default (unrecognised {norm!r} from {origin})"
    return DEFAULT_TIER, "default"


def rigor_for(site_class: str, *, authored: int | None = None,
              tier: str | None = None, family: str = "opus") -> Allocation:
    """The allocation for one site class at the active tier.

    `authored` is required for the `panel` class (the site's own design count)
    and ignored elsewhere. `family` names the model a panel is authored to use.

    An UNKNOWN class resolves to `gate` — the strictest — with a warning on
    stderr, rather than raising. A typo in a skill's prose must not crash a run
    mid-session, and must not quietly buy the cheapest allocation either: the
    safe direction for an authoring mistake is too much verification, not too
    little. `conformance()` is what catches the typo itself.
    """
    resolved, _origin = resolve_tier(tier)
    cls = (site_class or "").strip().lower()

    if cls == "fixed":
        return Allocation(
            site_class="fixed", tier=resolved, flags=FIXED_CANON_FLAGS,
            binding={"sonnet": 3}, advisory={}, vote=True,
            note="canon-locked pipeline — not on the dial at any tier "
                 "(factcheck-convergence.md §1)",
        )

    if cls == "panel":
        if authored is None:
            raise ValueError(
                "the 'panel' class needs the site's authored count: "
                "rigor_for('panel', authored=N)"
            )
        if not isinstance(authored, int) or isinstance(authored, bool) or authored < 1:
            raise ValueError(f"authored count must be a positive integer (got {authored!r})")
        cap = _PANEL_CAP[resolved]
        n = min(authored, cap)
        capped = n < authored
        return Allocation(
            site_class="panel", tier=resolved, flags=f"--{family} {n}",
            binding={}, advisory={}, vote=False,
            note=(f"generation panel — capped from {authored} to {n} at '{resolved}'"
                  if capped else
                  f"generation panel — the authored count of {authored} is within "
                  f"the '{resolved}' cap of {cap}"),
        )

    if cls not in _VERIFY:
        print(f"rigor.py: unknown site class {site_class!r} — falling back to "
              f"'gate' (the strictest). Known classes: "
              f"{', '.join(sorted(SITE_CLASSES))}", file=sys.stderr)
        cls = "gate"

    row = _VERIFY[cls][resolved]
    return Allocation(
        site_class=cls, tier=resolved, flags=_flags(row["binding"], row["advisory"]),
        binding=dict(row["binding"]), advisory=dict(row["advisory"]),
        vote=bool(row["vote"]),
        note=("three binding checkers must agree" if row["vote"]
              else "one binding checker — an isolated opinion, not a vote"),
    )


def _flags(binding: dict[str, int], advisory: dict[str, int]) -> str:
    """Render an allocation as `/double-check` flags.

    A family with no checkers is OMITTED rather than passed as `--sonnet 0`:
    `validate_allocation` reads a zero as a malformed binding count, and the
    engine's own contract is that an absent family means absent.
    """
    total: dict[str, int] = {}
    for src in (binding, advisory):
        for fam, n in src.items():
            if n:
                total[fam] = total.get(fam, 0) + n
    return " ".join(f"--{fam} {n}" for fam, n in
                    sorted(total.items(), key=lambda kv: (kv[0] != "sonnet", kv[0])))


# ---------------------------------------------------------------------------
# Self-test — every verification allocation is legal for the engine
# ---------------------------------------------------------------------------

def selftest() -> list[str]:
    """Check every (class, tier) against the engine, and the two floors.

    Returns a list of problems; empty means clean. Imports the engine lazily so
    this module stays usable (and importable) in a tree where the engine has not
    landed yet — the release ships substrate in order, and a resolver that could
    not be imported before its neighbour would be unshippable.
    """
    problems: list[str] = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from _factcheck_engine import validate_allocation  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        problems.append(f"engine unavailable, allocations UNVERIFIED: {e}")
        validate_allocation = None  # type: ignore[assignment]

    for cls, rows in _VERIFY.items():
        for tier in TIERS:
            if tier not in rows:
                problems.append(f"{cls}/{tier}: no allocation defined")
                continue
            row = rows[tier]
            # Floor 1 — producer-never-verifies: at least one binding checker,
            # always. A tier with none is self-assessment with a label on it.
            if sum(row["binding"].values()) < 1:
                problems.append(f"{cls}/{tier}: zero binding checkers — "
                                f"violates producer-never-verifies")
            if validate_allocation is not None:
                try:
                    validate_allocation(row["binding"], advisory=row["advisory"])
                except Exception as e:  # noqa: BLE001
                    problems.append(f"{cls}/{tier}: illegal allocation — {e}")

    # Floor 2 — the fixed pipelines do not move.
    for tier in TIERS:
        a = rigor_for("fixed", tier=tier)
        if a.flags != FIXED_CANON_FLAGS:
            problems.append(f"fixed/{tier}: returned {a.flags!r}, not the canon "
                            f"{FIXED_CANON_FLAGS!r}")

    # A panel never shrinks below one agent, and never inflates an authored count.
    for tier in TIERS:
        for authored in (1, 2, 3, 5):
            a = rigor_for("panel", authored=authored, tier=tier)
            n = int(a.flags.rsplit(" ", 1)[1])
            if n < 1:
                problems.append(f"panel/{tier}: authored {authored} resolved to {n}")
            if n > authored:
                problems.append(f"panel/{tier}: authored {authored} INFLATED to {n}")
    return problems


# ---------------------------------------------------------------------------
# Conformance — the code check that a skill did not go back to naming numbers
# ---------------------------------------------------------------------------
#
# The allocation a skill dispatches lives in prose, which is Layer 3 and enforces
# nothing. This scan is the Layer-1 half: it fails when a skill file carries a
# literal checker allocation that is not on the register below. The register is
# the same shape as `publish_scope_scan.PROSE_SURFACES` — an explicit list of
# known sites with a reason, so that a NEW hard-coded allocation is a failure
# rather than an addition nobody notices.

_LITERAL = re.compile(
    r"""(?:
          /double-check\s+\d+\s*,\s*\d+\s*,\s*\d+     # the M,N,R shorthand
        | --sonnet\s+\d+                              # explicit family counts
        | --opus\s+\d+
        )""",
    re.VERBOSE,
)

# A per-LINE waiver, for the case a file-level exemption would be too blunt: a
# worked example of an explicit operator override, or an anti-pattern quoted to
# be argued against, legitimately names numbers while every other line in the
# same file must not. The reason is written at the site, in the same line, so a
# waiver cannot accumulate silently the way a register entry can.
#
#     `/recommend --opus 3`   <!-- rigor-ok: an explicit operator override -->
#
# A bare marker with no reason after the colon does NOT waive anything. The
# reason must start with a letter or digit: `\S` was tried first and let
# `<!-- rigor-ok: -->` through, because the comment's own `-->` satisfied it.
_LINE_WAIVER = re.compile(r"rigor-ok:[ \t]*[A-Za-z0-9]")

# Files where a literal allocation is CORRECT, with the reason. A file whose job
# is to document or define the flags must contain them — the same rule the
# release tool's leak allowlist records for a gate that greps for its own
# patterns.
CONFORMANCE_EXEMPT: dict[str, str] = {
    "skills/double-check/SKILL.md":
        "defines the flags themselves: the Parameters table and the "
        "scope-coverage dependency (`--opus >= 1`) are the contract other "
        "skills call through",
    "skills/starter-kit/q_release.py":
        "the release tool renders the tier table for INSTALL.md; it reads this "
        "module rather than holding a second copy",
    "skills/unit-economics/unit_economics.py":
        "a python dispatch that builds its own command string; its allocation "
        "is a fixed-pipeline constant, not an on-demand surface",
    "skills/unit-economics/scripts/bootstrap/verification_port.py":
        "as above — the bootstrap port's own constant",
    "skills/unit-economics/tests/test_s5.py":
        "a test that asserts the bootstrap port's constant — it must name the "
        "allocation it is pinning",
    "skills/execute-plan/run.py":
        "code, not prose: the per-slice verification allocation is passed "
        "through to the engine and is covered by the plan-gate canon",
}


@dataclass
class ConformanceFinding:
    path: str
    line: int
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.text.strip()[:120]}"


def conformance(root: Path | str | None = None) -> list[ConformanceFinding]:
    """Literal allocations in skill files that are not on the exempt register.

    `root` is a rendered config tree (`~/.claude`, or a built kit's `.claude`).
    """
    base = Path(root).expanduser() if root else Path(__file__).resolve().parent.parent
    findings: list[ConformanceFinding] = []
    skills = base / "skills"
    if not skills.is_dir():
        return findings
    for path in sorted(skills.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".py", ".sh"):
            continue
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(base).as_posix()
        if rel in CONFORMANCE_EXEMPT:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if _LITERAL.search(line) and not _LINE_WAIVER.search(line):
                findings.append(ConformanceFinding(rel, i, line))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_for(args) -> int:
    a = rigor_for(args.site_class, authored=args.authored, tier=args.tier,
                  family=args.family)
    if args.json:
        print(json.dumps(a.as_dict(), indent=2, sort_keys=True))
    elif args.explain:
        print(f"{a.flags}    # {a.site_class} @ {a.tier} — {a.note}")
    else:
        print(a.flags)
    return 0


def _cmd_cap(args) -> int:
    a = rigor_for("panel", authored=args.authored, tier=args.tier, family=args.family)
    print(a.flags.rsplit(" ", 1)[1] if args.count_only else a.flags)
    return 0


def _cmd_get(args) -> int:
    tier, origin = resolve_tier(args.tier)
    print(f"{tier}    # from {origin}")
    return 0


def _cmd_set(args) -> int:
    try:
        tier, path = write_config(args.tier_value, set_by=args.set_by)
    except ValueError as e:
        print(f"rigor.py: {e}", file=sys.stderr)
        return 2
    print(f"verification rigor set to '{tier}' ({TIER_LABELS[tier]})")
    print(f"stored in {path}")
    env = os.environ.get(TIER_ENV)
    if env and env.strip().lower() != tier:
        print(f"NOTE: ${TIER_ENV} is set to '{env}' and takes precedence over the "
              f"file. Unset it for the stored tier to take effect.", file=sys.stderr)
    return 0


def _cmd_tiers(args) -> int:
    if args.json:
        print(json.dumps(
            {t: {"label": TIER_LABELS[t], "cost": TIER_COSTS[t],
                 "default": t == DEFAULT_TIER,
                 "gate": rigor_for("gate", tier=t).flags,
                 "check": rigor_for("check", tier=t).flags,
                 "panel_cap": _PANEL_CAP[t]}
             for t in TIERS}, indent=2, sort_keys=True))
        return 0
    for t in TIERS:
        mark = "  (default)" if t == DEFAULT_TIER else ""
        print(f"{t:<9}{TIER_LABELS[t]}{mark}")
    return 0


def _cmd_show(args) -> int:
    tier, origin = resolve_tier(args.tier)
    print(f"tier: {tier}   (from {origin})")
    print(f"cost: {TIER_COSTS[tier]} — {TIER_LABELS[tier]}")
    print("")
    print(f"{'class':<8}{'dispatches':<26}what it is")
    print(f"{'-'*8}{'-'*26}{'-'*44}")
    for cls in ("gate", "check", "fixed"):
        print(f"{cls:<8}{rigor_for(cls, tier=tier).flags:<26}{SITE_CLASSES[cls]}")
    print(f"{'panel':<8}{'cap ' + str(_PANEL_CAP[tier]) + ' agents':<26}{SITE_CLASSES['panel']}")
    return 0


def _cmd_selftest(args) -> int:
    problems = selftest()
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"rigor selftest OK — {len(_VERIFY) * len(TIERS)} verification "
          f"allocations legal, both floors hold")
    return 0


def _cmd_conformance(args) -> int:
    findings = conformance(args.root)
    for f in findings:
        print(f"HARD-CODED {f}", file=sys.stderr)
    if findings:
        print(f"\n{len(findings)} literal allocation(s) outside the exempt "
              f"register. A dispatch site reads the dial:\n"
              f"  python3 ${KIT_HOOKS_DIR}/rigor.py for gate|check\n"
              f"A worked example or a quoted anti-pattern waives one LINE with "
              f"an inline `rigor-ok: <reason>`. A whole file that defines the "
              f"flags themselves goes in CONFORMANCE_EXEMPT in rigor.py, with "
              f"its reason.", file=sys.stderr)
        return 1
    print("rigor conformance OK — no skill names a checker count inline")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rigor.py",
        description="The validation-rigor dial: resolve an allocation at dispatch.")
    p.add_argument("--tier", default=None,
                   help="resolve as if this tier were configured (does not store it)")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("for", help="print the flags for a site class")
    f.add_argument("site_class", choices=sorted(SITE_CLASSES))
    f.add_argument("--authored", type=int, default=None,
                   help="the site's own agent count (required for 'panel')")
    f.add_argument("--family", default="opus", help="model family for a panel")
    f.add_argument("--json", action="store_true")
    f.add_argument("--explain", action="store_true",
                   help="append the tier and what it means as a comment")
    f.set_defaults(func=_cmd_for)

    c = sub.add_parser("cap", help="cap a generation panel's authored count")
    c.add_argument("authored", type=int)
    c.add_argument("--family", default="opus")
    c.add_argument("--count-only", action="store_true",
                   help="print just the number, without the flag")
    c.set_defaults(func=_cmd_cap)

    g = sub.add_parser("get", help="print the active tier and where it came from")
    g.set_defaults(func=_cmd_get)

    s = sub.add_parser("set", help="store a tier (changeable at any time)")
    s.add_argument("tier_value", metavar="TIER", choices=list(TIERS))
    s.add_argument("--set-by", default="rigor.py set")
    s.set_defaults(func=_cmd_set)

    t = sub.add_parser("tiers", help="list the tiers")
    t.add_argument("--json", action="store_true")
    t.set_defaults(func=_cmd_tiers)

    sh = sub.add_parser("show", help="the active tier and every class it resolves")
    sh.set_defaults(func=_cmd_show)

    st = sub.add_parser("selftest", help="check every allocation against the engine")
    st.set_defaults(func=_cmd_selftest)

    cf = sub.add_parser("conformance",
                        help="fail on a skill that names a checker count inline")
    cf.add_argument("root", nargs="?", default=None,
                    help="a rendered config tree (default: this module's parent)")
    cf.set_defaults(func=_cmd_conformance)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

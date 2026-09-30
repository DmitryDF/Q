"""S-final gate for source-kind-reachability-gate — the WIRING, end to end.

Plan: Thoughts/source-kind-reachability-gate-20260823215421_PLAN.md

WHY THIS FILE EXISTS SEPARATELY from `test_source_admission_port.py`. That suite
tests the invariant's LOGIC against the module. This one tests whether the gate
RUNS it — which is the entire point of the slice, and a different question with a
different failure mode: every logic test can pass while the block never fires.

WHAT IT DOES. It reproduces the deploy-check faithfully rather than approximating
it: a candidate tree is rendered, the LIVE `claude-verify` is invoked against it
through `CLAUDE_VERIFY_TARGET` with a SANITIZED env that omits
`CLAUDE_CONFIG_DIR` (the same allowlist `land_port.py:825-828` builds, and the
same variable `:950` sets), and the verdict is read the way the gate reads it —
the first 400 characters of AGGREGATE stderr (`land_port.py:963`).

Live is the reference: it must be GREEN and the block must NOT fire there, since
the two `claude-promote` bookends run against live and only the deploy-check sets
a target.

THESE TESTS NEVER WRITE TO LIVE. Every mutation is applied to a `tmp_path` copy.
The live tree is read, and read only.

Cost: ~2.6s per arm (0.5s render + 2.1s check), measured. That is why the arms
are written as separate tests rather than folded into one — a failure names which
property broke — and why the red arm's output is computed once and shared.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

CONFIG_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
CLAUDE_VERIFY = CONFIG_DIR / "bin" / "claude-verify"

#: `land_port.DEFAULT_ENV_ALLOWLIST`. Mirrored rather than imported so this file
#: states the contract it is testing; the assertion below pins them together, so
#: a change to the allowlist fails here instead of silently drifting.
ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "USER", "LOGNAME", "LANG", "LC_ALL",
                 "SHELL")

#: What a rendered candidate needs to be checkable by `claude-verify --phase pre`.
#
# FAITHFUL, NOT MINIMAL — and the difference was load-bearing. This named five
# directories and omitted four managed surfaces (`docs`, `state`, `CLAUDE.md`,
# `statusline.sh`), so the "candidate" was a SUBSET of what the deploy step
# deploys rather than a stand-in for it. Two `--phase pre` checks read outside
# `hooks/` and `rules/` and treat an absent record as drift, so they failed on
# the omission rather than on anything a candidate had done: omitting `docs/`
# made `check_land_port_records.py` report `missing-record` and exit 1 on EVERY
# candidate arm, and omitting `state/` made `check_framing_surface_records.py`
# emit a `NOT COMPARED … FileNotFoundError` that flooded the 400-char budget the
# legibility arms read. The second was invisible until the first was fixed.
#
# Worse than the red: it silently DISABLED the anti-vacuity control below.
# `test_gate_seeded_arm_flips_when_the_block_is_removed` requires the
# block-removed run to come back GREEN, and land-port fired independently of the
# reachability block — so that arm could never have caught a deleted block.
#
# `test_render_candidate_is_faithful_to_config-source` pins this to the managed set,
# so the next surface added to the harness fails HERE instead of drifting.
RENDER_DIRS = ("bin", "hooks", "skills", "rules", "agents", "docs")

#: Managed top-level FILES. Omitting these was the same defect in a second shape.
RENDER_FILES = ("settings.json", "CLAUDE.md", "statusline.sh")

#: `state/` is rendered SELECTIVELY: it is ~82 MB live but only these two
#: subtrees are config-source-managed, and copying it whole would cost an order of
#: magnitude more than the check it enables (the file's whole arm budget is
#: ~2.6s). A new managed `state/` subtree is caught by the faithfulness pin.
RENDER_SUBTREES = (("state", "skill-markers"), ("state", "impl-models"))

#: The gate keeps only this much of AGGREGATE stderr (`land_port.py:963`).
STDERR_BUDGET = 400

MODULE_RELPATH = ("skills", "research", "kind_reachability.py")
SCOPE_RECORD_RELPATH = ("skills", "research", "scope_record.py")

# The insertion point the scratch-tree arms below splice an exemption into.
#
# WIDENED, NOT MOVED. It carried the trailing newline while the mapping always
# had at least one entry and so was always written across several lines. The code
# driver emptied it — `= {}` on one line — and every arm that splices here went
# red on the anchor rather than on anything it was written to catch. Dropping the
# newline matches BOTH shapes: the empty literal, and a multi-entry one the moment
# a kind is exempted again. The module's own assertion message asked for exactly
# this ("update EXEMPTION_ANCHOR rather than deleting this control").
EXEMPTION_ANCHOR = "KNOWN_UNREACHABLE: Mapping[str, Tuple[str, str]] = {"

#: A source kind production can never adopt.
#
# NOT AN ANCHOR, AND DELIBERATELY SO. The previous control spliced a fifth kind
# into `REGISTERED_KINDS` by matching the tuple's exact four-kind TEXT, and it
# broke in both halves at once. The literal went stale the moment a fifth kind
# was registered — the S13-obs8 shape, a test locating production by text — and
# the kind it seeded was `linear`, which production then ADOPTED, so the control
# was asserting a state that no longer exists and cannot be restored by editing
# the literal. Bumping the tuple fixes neither half and goes stale on kind six.
#
# `kind_reachability` reads the registry by IMPORT (`:232`,
# `set(srec.REGISTERED_KINDS)`) — never by parsing the literal — so APPENDING a
# rebinding line needs no text match against production at all. Every
# `REGISTERED_KINDS` reference in `scope_record` is inside a function body, so
# there is no import-time derived structure for the rebinding to leave stale.
#
# The kind is synthetic so production can never adopt it the way it adopted
# `linear`. That is what makes this control unable to go stale, rather than
# merely re-anchored to go stale later.
SEEDED_KIND = "seeded_unreachable_kind"

SEED_APPEND = f'''

# --- appended by test_kind_reachability_gate (never present in production) --- #
KIND_SEEDED_UNREACHABLE = "{SEEDED_KIND}"
REGISTERED_KINDS = REGISTERED_KINDS + (KIND_SEEDED_UNREACHABLE,)
'''

SYNTHETIC_DRIVER = '''\
"""Synthetic production driver for a seeded kind (S-final arm E)."""
from research import scope_record as _sr


def read_seeded(port, source):
    return port.admit(source, kind=_sr.KIND_SEEDED_UNREACHABLE)
'''

pytestmark = pytest.mark.skipif(
    not CLAUDE_VERIFY.is_file(),
    reason=f"claude-verify not present at {CLAUDE_VERIFY} — nothing to gate")


# --------------------------------------------------------------------------- #
# Rendering a candidate
# --------------------------------------------------------------------------- #

IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache",
                                "*.bak-*")


def render_candidate(dest: Path) -> Path:
    """A checkable copy of the live tree — the stand-in for the deploy step.

    Faithful to the MANAGED set, not to a convenient subset of it: see
    `RENDER_DIRS` for what an unfaithful render cost, and
    `test_render_candidate_is_faithful_to_config-source` for what holds it there.
    """
    dest.mkdir(parents=True, exist_ok=True)
    for name in RENDER_FILES:
        src = CONFIG_DIR / name
        if src.is_file():
            shutil.copy2(src, dest / name)
    for name in RENDER_DIRS:
        src = CONFIG_DIR / name
        if src.is_dir():
            shutil.copytree(src, dest / name, symlinks=True, ignore=IGNORE)
    for parts in RENDER_SUBTREES:
        src = CONFIG_DIR.joinpath(*parts)
        if src.is_dir():
            sub = dest.joinpath(*parts)
            sub.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, sub, symlinks=True, ignore=IGNORE)
    return dest


def run_verify(target: Path | None, verbose: bool = False):
    """`claude-verify --phase pre`, in the deploy-check's sanitized env.

    `target=None` is the LIVE invocation the two bookends make.

    `verbose` matters for exactly one arm and the reason is worth stating: on a
    GREEN run the reachability block reports only through `vlog`, which prints
    NOTHING without `-v`. So "live said nothing about reachability" is true
    whether the block was guarded or not, and an arm asserting it non-verbosely
    passes vacuously. `-v` is what makes the block's presence observable — 0
    reachability lines against live, 1 against a target — and is therefore the
    only way to test the guard rather than assert it.
    """
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    if target is not None:
        env["CLAUDE_VERIFY_TARGET"] = str(target)
    assert "CLAUDE_CONFIG_DIR" not in env, (
        "the deploy-check env must not carry CLAUDE_CONFIG_DIR — if it did, a "
        "module rooting on it would read live and this whole file would be "
        "measuring the wrong tree")
    cmd = [str(CLAUDE_VERIFY), "--phase", "pre"] + (["-v"] if verbose else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def budget(proc: subprocess.CompletedProcess) -> str:
    """Exactly what the gate surfaces to a person."""
    return proc.stderr.strip()[:STDERR_BUDGET]


# --- mutations applied to a CANDIDATE (never to live) ---------------------- #

def seed_undriven_kind(root: Path) -> None:
    """Register a synthetic kind with no driver — the intended red.

    Renamed from `seed_fifth_kind`: the ordinal was accurate when the registry
    held four kinds, and it is not a property this control depends on. The name
    now says what the seed IS for — a registered kind nothing drives.

    The only thing pinned to production is the IDENTIFIER `REGISTERED_KINDS`,
    which is the actual contract (`kind_reachability:232` reads it as an
    attribute). Its formatting, length and membership are all free to change.
    """
    path = root.joinpath(*SCOPE_RECORD_RELPATH)
    text = path.read_text(encoding="utf-8")
    assert "REGISTERED_KINDS" in text, (
        "scope_record no longer defines REGISTERED_KINDS — the registry this "
        "gate exists to check has moved or been renamed. Re-point the seed "
        "rather than deleting this control")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(SEED_APPEND)


def add_exemption(root: Path, kind: str, reason: str, owner: str) -> None:
    path = root.joinpath(*MODULE_RELPATH)
    text = path.read_text(encoding="utf-8")
    assert EXEMPTION_ANCHOR in text, (
        "exemption anchor not found — KNOWN_UNREACHABLE's declaration changed; "
        "update EXEMPTION_ANCHOR rather than deleting this control")
    entry = f'    "{kind}": (\n        "{reason}",\n        "{owner}",\n    ),\n'
    path.write_text(text.replace(EXEMPTION_ANCHOR, EXEMPTION_ANCHOR + entry),
                    encoding="utf-8")


def add_driver_for_seeded_kind(root: Path) -> None:
    (root / "hooks" / "linear_reader.py").write_text(SYNTHETIC_DRIVER,
                                                     encoding="utf-8")


# --------------------------------------------------------------------------- #
# The reference arms
# --------------------------------------------------------------------------- #

def test_gate_env_allowlist_still_matches_land_port():
    """Pins the mirrored allowlist to its source.

    Without this, `land_port` could start forwarding `CLAUDE_CONFIG_DIR` and
    every arm below would keep passing while measuring live.
    """
    import sys
    sys.path.insert(0, str(CONFIG_DIR / "hooks"))
    import land_port                                          # noqa: E402
    assert tuple(land_port.DEFAULT_ENV_ALLOWLIST) == ENV_ALLOWLIST
    assert "CLAUDE_CONFIG_DIR" not in land_port.DEFAULT_ENV_ALLOWLIST


def test_render_candidate_is_faithful_to_config-source(tmp_path):
    """Every config-source-managed file must survive into the rendered candidate.

    This is the same mirror-and-pin discipline as the arm above, applied to the
    render surface, and it EARNED its place: the render previously covered five
    of the ten managed top-level entries, and two `--phase pre` checks failed on
    what was missing rather than on what the candidate contained. That is not a
    detectable-by-inspection failure — it presents as an unrelated check going
    red, and it took a runtime bisect to attribute.

    Pinned per FILE, not per top-level name, and the distinction is the whole
    point: `state/` is rendered selectively, so a name-level pin would have
    passed while `state/skill-markers/shadow-map.json` — the omission that
    produced the second, masked symptom — went on missing.

    A managed path that is not deployed live is skipped: this asserts the render
    is faithful to what EXISTS, not that the machine is fully applied. Directory
    entries are implied by their contents.
    """
    proc = subprocess.run(["config-source", "managed"], capture_output=True,
                          text=True)
    if proc.returncode != 0:
        pytest.skip("config-source unavailable — cannot derive the managed set")

    root = render_candidate(tmp_path / ".claude")
    prefix = ".claude/"
    missing = []
    for line in proc.stdout.splitlines():
        rel = line.strip()
        if not rel.startswith(prefix):
            continue
        rel = rel[len(prefix):]
        live = CONFIG_DIR / rel
        if not live.is_file():
            continue
        if not (root / rel).exists():
            missing.append(rel)

    assert not missing, (
        "the rendered candidate is missing managed files, so it is not a "
        "stand-in for the deploy step and any check reading them will fail on "
        "the omission rather than on the candidate:\n  "
        + "\n  ".join(sorted(missing)[:20]))


def test_gate_does_not_run_against_live():
    """The block must be absent from BOTH `claude-promote` bookends.

    They run against live — the pre-bookend before the capture step, so no
    candidate even exists — and a reachability verdict about the shipper's own
    machine is noise at best and someone else's uncommitted edit at worst.

    Run VERBOSE deliberately. An earlier version of this arm ran non-verbosely
    and was VACUOUS: on a green run the block speaks only through `vlog`, so the
    assertion held with the guard removed. That was caught by a revert check, not
    by the arm going red, which is why the control below is committed beside it.
    """
    proc = run_verify(None, verbose=True)
    assert proc.returncode == 0, f"live is not green: {budget(proc)}"
    assert "reachab" not in (proc.stdout + proc.stderr).lower(), (
        "the reachability block ran on a LIVE invocation — the "
        "CLAUDE_VERIFY_TARGET guard has been removed or weakened")


def test_gate_does_run_against_a_candidate():
    """The other half of the arm above, and what stops it going vacuous again.

    Together the pair says the block is present AND conditional. Alone, the arm
    above is satisfied just as well by a block that was deleted outright.
    """
    proc = run_verify(CONFIG_DIR, verbose=True)
    assert "reachab" in (proc.stdout + proc.stderr).lower(), (
        "no reachability line even with a target set — the block is not being "
        "reached at all, so the guard arm above proves nothing")


def test_gate_unseeded_candidate_is_green(tmp_path):
    """The baseline every red arm below is measured against."""
    proc = run_verify(render_candidate(tmp_path / ".claude"))
    assert proc.returncode == 0, f"an unseeded candidate went red: {budget(proc)}"


# --- the stop, and its legibility ------------------------------------------ #

@pytest.fixture(scope="module")
def seeded_red(tmp_path_factory):
    """One render of a candidate registering a fifth kind with no driver.

    Module-scoped: two tests read this same output — that it stopped, and that
    the kind is legible in the budget — and rendering twice would buy nothing.
    """
    root = render_candidate(tmp_path_factory.mktemp("seeded") / ".claude")
    seed_undriven_kind(root)
    return run_verify(root)


def test_gate_seeded_candidate_stops_the_promotion(seeded_red):
    """C3 — a guard that stops nothing is indistinguishable from no guard."""
    assert seeded_red.returncode != 0, (
        "a candidate registering a kind with no production driver came back "
        "GREEN — the gate is not enforcing")


def test_gate_names_the_kind_inside_the_truncation_budget(seeded_red):
    """C2 — read COLD, as the gate surfaces it, not as the module prints it.

    `land_port.py:963` keeps the first 400 characters of AGGREGATE stderr from
    every check in `claude-verify`. A message that is correct but arrives past
    that point is a message nobody gets. If this fails, the message is wrong or
    the block has drifted behind a chattier check — not the budget.
    """
    surfaced = budget(seeded_red)
    assert SEEDED_KIND in surfaced, (
        "the kind name is not legible in the gate's 400-char budget; got:\n"
        f"{surfaced!r}")


# --- the two honest exits, and the reverse direction ----------------------- #

def test_gate_exemption_with_reason_and_owner_lifts_the_stop(tmp_path):
    """C5 + C6 — own it in writing, and the promotion proceeds."""
    root = render_candidate(tmp_path / ".claude")
    seed_undriven_kind(root)
    add_exemption(root, SEEDED_KIND, "no reader ships for this seed",
                  "the S-final control")
    proc = run_verify(root)
    assert proc.returncode == 0, (
        f"a recorded exemption did not lift the stop: {budget(proc)}")


def test_gate_exemption_with_a_blank_reason_does_not_lift_the_stop(tmp_path):
    """C6 — the record must actually say why, or it is not a record.

    This is what keeps the exemption from degrading into a mute list.
    """
    root = render_candidate(tmp_path / ".claude")
    seed_undriven_kind(root)
    add_exemption(root, SEEDED_KIND, "   ", "the S-final control")
    proc = run_verify(root)
    assert proc.returncode != 0, "a reason-less exemption lifted the stop"
    assert "carries no reason" in budget(proc), budget(proc)


def test_gate_wiring_a_driver_lifts_the_stop(tmp_path):
    """C4 — fixing the thing must work, or the guard is obstruction."""
    root = render_candidate(tmp_path / ".claude")
    seed_undriven_kind(root)
    add_driver_for_seeded_kind(root)
    proc = run_verify(root)
    assert proc.returncode == 0, (
        f"wiring a production driver did not lift the stop: {budget(proc)}")


def test_gate_stale_exemption_goes_red_on_a_candidate(tmp_path):
    """The REVERSE direction, at candidate level — S-final item 5's middle
    conjunct, which S1 proved only in-process.

    `web` demonstrably has a production driver. Recording it as unreachable must
    fail, or `KNOWN_UNREACHABLE` becomes a place to silence findings rather than
    to own them. A one-directional gate would pass this.
    """
    root = render_candidate(tmp_path / ".claude")
    add_exemption(root, "web", "pretend it is unreachable", "nobody")
    proc = run_verify(root)
    assert proc.returncode != 0, (
        "a stale exemption on a kind that HAS a driver came back green — the "
        "gate checks only one direction")
    surfaced = budget(proc)
    assert "web" in surfaced, surfaced
    assert "delete its KNOWN_UNREACHABLE entry" in surfaced, surfaced


# --- anti-vacuity: the arms above test the chain, they do not assert it ----- #

BLOCK_START = "# 4e. source-kind reachability"
BLOCK_END = "# 4d. citation-marker drift"
GUARD_LINE = 'if [ -n "${CLAUDE_VERIFY_TARGET:-}" ]; then'


def _mutated_verify(dest: Path, kind: str) -> Path:
    """A COPY of `claude-verify` with one half of the wiring disabled.

    Never mutates live: the copy is written under `tmp_path` and invoked there.
    """
    text = CLAUDE_VERIFY.read_text(encoding="utf-8")
    if kind == "no-block":
        i, j = text.index(BLOCK_START), text.index(BLOCK_END)
        text = text[:i] + text[j:]
    elif kind == "no-guard":
        assert text.count(GUARD_LINE) == 1, (
            "the target guard is no longer a unique line — this control would "
            "disable the wrong `if`")
        text = text.replace(GUARD_LINE, "if true; then", 1)
    else:                                                     # pragma: no cover
        raise ValueError(kind)
    path = dest / f"claude-verify-{kind}"
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


def _run_with(verify: Path, target: Path | None, verbose: bool = False):
    env = {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}
    env.setdefault("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    if target is not None:
        env["CLAUDE_VERIFY_TARGET"] = str(target)
    cmd = [str(verify), "--phase", "pre"] + (["-v"] if verbose else [])
    return subprocess.run(cmd, capture_output=True, text=True, env=env)


def test_gate_seeded_arm_flips_when_the_block_is_removed(tmp_path):
    """The seeded candidate must go red BECAUSE of the block, not incidentally.

    Without this, `test_gate_seeded_candidate_stops_the_promotion` could be green
    for any unrelated reason a seeded tree might fail — and would keep passing
    after someone deleted the block.
    """
    root = render_candidate(tmp_path / ".claude")
    seed_undriven_kind(root)
    assert _run_with(CLAUDE_VERIFY, root).returncode != 0, "the intact gate missed the seed"
    stripped = _run_with(_mutated_verify(tmp_path, "no-block"), root)
    assert stripped.returncode == 0, (
        "the seeded candidate went red WITHOUT the reachability block — the "
        f"seeded arm is not measuring this wiring. stderr: {budget(stripped)}")


def test_gate_guard_arm_flips_when_the_target_condition_is_removed(tmp_path):
    """The live-silence arm must be measuring the guard, not the block's absence.

    This control EARNED its place: the first version of the live arm ran
    non-verbosely and passed with the guard removed, because a green run reports
    only through `vlog`. The arm was green and vacuous, and only this flip test
    said so.
    """
    intact = _run_with(CLAUDE_VERIFY, None, verbose=True)
    unguarded = _run_with(_mutated_verify(tmp_path, "no-guard"), None, verbose=True)
    assert "reachab" not in (intact.stdout + intact.stderr).lower()
    assert "reachab" in (unguarded.stdout + unguarded.stderr).lower(), (
        "removing the target condition changed nothing observable — the live "
        "arm is vacuous again, most likely because it stopped passing -v")


def test_gate_candidate_without_the_module_is_skipped_not_failed(tmp_path):
    """An older tree predating this slice must not be blocked by its absence.

    The existence guard is the same idiom every sibling check uses. This is the
    one arm where silence is the correct answer.
    """
    root = render_candidate(tmp_path / ".claude")
    root.joinpath(*MODULE_RELPATH).unlink()
    proc = run_verify(root)
    assert proc.returncode == 0, (
        f"a tree predating the module was blocked by its absence: {budget(proc)}")


def test_gate_candidate_with_the_module_but_no_registry_fails_loudly(tmp_path):
    """A BROKEN candidate must go red, never skip.

    Distinct from the arm above: there, the check is absent; here, it is present
    and cannot read the registry. Skipping would let a corrupt candidate ship.
    """
    root = render_candidate(tmp_path / ".claude")
    root.joinpath(*SCOPE_RECORD_RELPATH).unlink()
    proc = run_verify(root)
    assert proc.returncode != 0, (
        "a candidate whose source-kind registry is unreadable came back GREEN")
    assert "COULD NOT RUN" in budget(proc), budget(proc)

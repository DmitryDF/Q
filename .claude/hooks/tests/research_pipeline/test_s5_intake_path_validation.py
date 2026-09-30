"""Adversarial + legitimate-path tests for
`research_pipeline.validate_research_file_path` (A5's intake validation gate —
Slice S5 of research-entry-point-enforcement).

Committed per the GAP found at S5 round 5: A5's own Validation-gate cell
requires adversarial coverage ("Adversarial tests: `~/.ssh/id_rsa`, `../../x`,
a symlink out, a null byte — all refused at intake"), and until this file
existed that requirement was guarded only by a throwaway scratch script the
orchestrator ran by hand — never a committed, re-runnable test.

Round 6 (MINOR 3): relocated here from the top-level `hooks/tests/` directory
so it sits alongside the other `research_pipeline` test modules — eighteen of
them in this directory, of which six resolve `HOOKS_DIR` the same
self-relative way this file does (`test_s4_approval_artifact.py`,
`test_s4_round2_declared_read_gate.py`, `test_s4_round3_payload_file.py`,
`test_s4_round4_bash_sweep.py`, `test_s4_round5_non_default_cycle.py`,
`test_s4_round5_query_file_guard.py`); the rest resolve it a different way
(see the comment below `HOOKS_DIR`).
Round 7 (MINOR 2) corrects the reason given for the move: the standing
`~/.claude/rules/research-scope-framing.md` out-of-session smoke block's
`run` lines each pin a specific FILE under `$TESTS`, never the directory
itself — a `pytest <dir>` invocation collects this module by virtue of
sitting here, but the smoke block does not yet name it in any `run` line.
Adding that line is not this session's to make: the smoke block carries its
own producer-never-verifies rule ("The implementer of S1-S8 does NOT author
this smoke block; the S9 verifier session does. The block above must not be
edited from inside a producer session" — see that rules file's
"## Verification-isolation contract (C5)" section), and this session is an
S5 producer.

Round 6 (MINOR 4): added a seam-level class that goes through
`validate_schema("r0_intake", ...)` rather than calling
`validate_research_file_path` directly, so a future edit that removed the
call inside `validate_schema` would still be caught here.

Round 8 (MINOR 2) corrects two over-broad statements this docstring and the
`HOOKS_DIR` comment below it each carried: neither "every other
`research_pipeline` test module" (this paragraph, as originally worded) nor
"matching every sibling in this directory" (the comment, as originally
worded) was true — only six of the eighteen other modules here resolve
`HOOKS_DIR` the same self-relative way this file does. Both are corrected in
place above rather than left standing.
"""

import os
import sys
from pathlib import Path

import pytest

# Resolve from this file's own location, matching the six S4 siblings that
# resolve HOOKS_DIR the same way (`test_s4_approval_artifact.py`,
# `test_s4_round2_declared_read_gate.py`, `test_s4_round3_payload_file.py`,
# `test_s4_round4_bash_sweep.py`, `test_s4_round5_non_default_cycle.py`,
# `test_s4_round5_query_file_guard.py`) — never `Path.home()`, which
# `~/.claude/skills/research/kind_reachability.py`'s module docstring
# documents as the anti-pattern a prior slice fixed ("Every path resolves
# from `Path(__file__)`, never from `CLAUDE_CONFIG_DIR` and never from
# `Path.home()`"): a `Path.home()`-rooted import always reads the LIVE tree
# regardless of which tree (e.g. a `config-experiment` clone) is under test.
# Not every sibling in this directory resolves this way: several still pin
# `Path.home()` directly, and `test_s3_walking_skeleton.py` fixed the same
# underlying problem — a `Path.home()` pin silently binding the whole pytest
# process to the live tree — by resolving `CLAUDE_CONFIG_DIR` instead, a
# different mechanism from this file's. This comment states the reason for
# the self-relative form, not a claim that the directory is uniform.
HOOKS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(HOOKS_DIR))

import research_pipeline as rp  # noqa: E402


# ── refusal vectors — each must raise ValueError specifically ───────────────

@pytest.mark.parametrize("declared", [
    "~/.ssh/id_rsa",
    "../../x",
    "../../x_RESEARCH.md",
    "Thoughts/x\x00_RESEARCH.md",  # NUL
    "Thoughts/x\x1b_RESEARCH.md",  # another control character (ESC)
    "",
    "   ",
    "n/a-implementation-slice",
    "x_RESEARCH.MD",  # post-revert: the extension check is case-sensitive
])
def test_refused_vectors_raise_value_error(declared):
    with pytest.raises(ValueError):
        rp.validate_research_file_path(declared)


def test_none_raises_value_error():
    with pytest.raises(ValueError):
        rp.validate_research_file_path(None)


def test_non_md_extension_refused():
    with pytest.raises(ValueError):
        rp.validate_research_file_path("Thoughts/x_RESEARCH.txt")


# ── symlinks ──────────────────────────────────────────────────────────────────

def test_symlink_escaping_its_directory_refused(tmp_path):
    root = tmp_path.resolve()
    inside = root / "inside"
    outside = root / "outside"
    inside.mkdir()
    outside.mkdir()
    target = outside / "target_RESEARCH.md"
    target.write_text("x")
    link = inside / "link_RESEARCH.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(ValueError):
        rp.validate_research_file_path(str(link))


def test_symlink_within_its_directory_admitted(tmp_path):
    root = tmp_path.resolve()
    inside = root / "inside"
    inside.mkdir()
    target = inside / "target_RESEARCH.md"
    target.write_text("x")
    link = inside / "link_RESEARCH.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    result = rp.validate_research_file_path(str(link))
    assert result == str(target.resolve())


# ── existing non-regular-file target ─────────────────────────────────────────

def test_existing_directory_target_refused(tmp_path):
    directory = tmp_path / "somedir_RESEARCH.md"
    directory.mkdir()
    with pytest.raises(ValueError):
        rp.validate_research_file_path(str(directory))


# ── legitimate admits ────────────────────────────────────────────────────────

def test_workspace_relative_admitted():
    result = rp.validate_research_file_path("Thoughts/x_RESEARCH.md")
    assert result.endswith("Thoughts/x_RESEARCH.md")


def test_home_relative_admitted():
    result = rp.validate_research_file_path("~/x_RESEARCH.md")
    assert result.endswith("x_RESEARCH.md")
    assert os.path.isabs(result)


def test_absolute_path_in_different_repo_root_admitted():
    # A live value the pipeline has actually registered (measured 2026-09-29,
    # 46 cycles / 26 manifests) — a different repository entirely. Need not
    # exist: a non-existent target is admitted by design.
    declared = (
        "~/repos/[YourProject]/Thoughts/"
        "tax-registration-20260830152212_RESEARCH.md"
    )
    result = rp.validate_research_file_path(declared)
    assert result.endswith(
        "[YourProject]/Thoughts/tax-registration-20260830152212_RESEARCH.md"
    )


# ── ValueError, not RuntimeError/OSError, for the unknown-user case ─────────

def test_unknown_user_expansion_raises_value_error_not_runtime_error():
    # `~nosuchuser` expansion raises RuntimeError from pathlib's own
    # expanduser(); the function wraps it in ValueError so it never escapes
    # as a bare RuntimeError. pytest.raises(ValueError) would itself fail
    # (as a different exception type) if a RuntimeError escaped instead.
    with pytest.raises(ValueError):
        rp.validate_research_file_path("~nosuchuser/x_RESEARCH.md")


# ── MINOR 4 — through the SEAM (validate_schema), not the bare function ─────
#
# Every test above calls `rp.validate_research_file_path` directly. That
# leaves the actual admission seam — `validate_schema("r0_intake", payload)`,
# which is what `r0_intake` really calls — untested. Round 7 (MINOR 3)
# corrects what deleting the call inside `validate_schema` would actually do:
# it would NOT let anything unsafe through, because `cmd_advance` calls
# `validate_research_file_path` a second time, unguarded, on the canonical
# write (that call's own comment: "`validate_schema` already refused anything
# unsafe, so this cannot raise") — so with the first call removed, the second
# would raise instead and `_write_state` would never be reached. What deleting
# the first call actually moves is WHEN the refusal happens:
# `validate_schema`'s own docstring gives the real reason it is called from
# here rather than from `advance` — "so that a refusal happens before any
# checkpoint is written" — because `r0_intake` cannot be called twice for a
# cycle, so a bad path admitted at the in-memory-checkpoint step could never
# be corrected. The two call sites are a deliberate pair, each trusting the
# other as the guard; this test exercises the seam so a future edit that
# removed either half would be caught here rather than only shifting the
# refusal point silently.

@pytest.mark.parametrize("declared", [
    "~/.ssh/id_rsa",
    "../../x_RESEARCH.md",
    "Thoughts/x\x00_RESEARCH.md",  # NUL
])
def test_seam_refuses_adversarial_vectors_at_r0_intake(declared):
    with pytest.raises(ValueError):
        rp.validate_schema(
            "r0_intake", {"research_file_path": declared}
        )


def test_seam_refuses_symlink_escaping_its_directory(tmp_path):
    root = tmp_path.resolve()
    inside = root / "inside"
    outside = root / "outside"
    inside.mkdir()
    outside.mkdir()
    target = outside / "target_RESEARCH.md"
    target.write_text("x")
    link = inside / "link_RESEARCH.md"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")

    with pytest.raises(ValueError):
        rp.validate_schema(
            "r0_intake", {"research_file_path": str(link)}
        )


def test_seam_admits_legitimate_payload():
    # Must NOT raise — the seam's refusal is targeted, not a blanket block.
    rp.validate_schema(
        "r0_intake", {"research_file_path": "Thoughts/x_RESEARCH.md"}
    )

"""Tests for the three-axis claim state + grounding-truth predicate (Slice S6)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import _claim_engine as ce   # noqa: E402
import _claim_state as cst    # noqa: E402


def _claim(faithful=True, lang="en"):
    flags = {c: True for c in ce.CRITERIA}
    flags["faithfulness"] = faithful
    return ce.Claim(text="X holds.", anchor=ce.Anchor.local_file("a_RESEARCH.md", 1),
                    flags=ce.ClaimFlags.from_dict(flags),
                    role=ce.ClaimRole.BACKWARD, lang=lang)


# ── grounding-truth predicate = faithful AND valid AND committed ─────────────

def test_grounding_truth_requires_all_three_axes():
    st = cst.build_state(_claim(), thought_status="DONE 2026-07-07")
    assert st.is_grounding_truth()
    assert st.exclusion_reason() == ""


@pytest.mark.parametrize("faithful,status,validity,expected", [
    (True, "DONE", cst.Validity.VALID, True),
    (False, "DONE", cst.Validity.VALID, False),        # unfaithful
    (True, "in progress", cst.Validity.VALID, False),  # WIP not committed
    (True, "Retired 2026-07-01", cst.Validity.VALID, False),  # excluded
    (True, "Won't Do", cst.Validity.VALID, False),     # excluded
    (True, "DONE", cst.Validity.INVALID, False),       # no longer holds
    (True, "DONE", cst.Validity.UNKNOWN, False),       # unverified / dynamic
])
def test_grounding_matrix(faithful, status, validity, expected):
    st = cst.build_state(_claim(faithful=faithful), thought_status=status,
                         validity=validity)
    assert st.is_grounding_truth() is expected


# ── axes never collapsed ─────────────────────────────────────────────────────

def test_three_axes_are_independent_fields():
    st = cst.build_state(_claim(faithful=False),
                         thought_status="in progress", validity=cst.Validity.INVALID)
    # all three readable independently; no single 'truth' field collapses them
    assert st.faithful is False
    assert st.validity is cst.Validity.INVALID
    assert st.lifecycle is cst.Lifecycle.THOUGHT
    assert not hasattr(st, "truth")


def test_lifecycle_alone_is_not_truth():
    # implemented but unfaithful → not grounding-truth (the Q-J category-error fix)
    st = cst.build_state(_claim(faithful=False), thought_status="DONE")
    assert st.lifecycle is cst.Lifecycle.IMPLEMENTED
    assert not st.is_grounding_truth()


# ── lifecycle derivation from bookkeeping status ─────────────────────────────

@pytest.mark.parametrize("status,expected", [
    ("in progress 🚧 since 2026-07-07", cst.Lifecycle.THOUGHT),
    ("not in progress", cst.Lifecycle.THOUGHT),
    ("DONE 2026-07-07", cst.Lifecycle.IMPLEMENTED),
    ("implemented", cst.Lifecycle.IMPLEMENTED),
    ("**Status:** Retired 2026-07-01", cst.Lifecycle.EXCLUDED),
    ("Won't Do (Out of Scope)", cst.Lifecycle.EXCLUDED),
])
def test_lifecycle_from_status(status, expected):
    assert cst.lifecycle_from_status(status) is expected


def test_retired_precedence_over_done():
    # a retired thought that also mentions DONE resolves to EXCLUDED (precedence)
    assert cst.lifecycle_from_status("DONE then Retired 2026-07-01") is cst.Lifecycle.EXCLUDED


# ── exclusions are surfaced explicitly (U7), not silently dropped ────────────

def test_exclusion_reasons_are_plain_words():
    wip = cst.build_state(_claim(), thought_status="in progress")
    assert "in progress" in wip.exclusion_reason()
    retired = cst.build_state(_claim(), thought_status="Retired 2026-07-01")
    assert "retired" in retired.exclusion_reason().lower()
    unknown = cst.build_state(_claim(), thought_status="DONE", validity=cst.Validity.UNKNOWN)
    assert "unknown" in unknown.exclusion_reason()


def test_partition_grounding_splits_and_keeps_reasons():
    gt = cst.build_state(_claim(), thought_status="DONE")
    bad = cst.build_state(_claim(False), thought_status="DONE")
    part = cst.partition_grounding([(_claim(), gt), (_claim(False), bad)])
    assert len(part["grounding"]) == 1
    assert len(part["excluded"]) == 1
    assert part["excluded"][0]["reason"]      # non-empty reason


# ── native language untouched by state ───────────────────────────────────────

def test_native_language_untouched_by_state():
    ru = _claim(lang="ru")
    st = cst.build_state(ru, thought_status="DONE")
    assert ru.lang == "ru" and ru.text == "X holds."   # state derivation mutates nothing
    assert st.is_grounding_truth()

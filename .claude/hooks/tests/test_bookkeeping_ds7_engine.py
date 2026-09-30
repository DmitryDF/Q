"""DS7 #15 — claims_registry.ensure_exists() wired into _factcheck_engine.factcheck_run.

The _CLAIMS creation happens at round 0 (before checker dispatch), so we assert the
side-effect and tolerate any downstream dispatch error from the injected fake checker."""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import _factcheck_engine as eng  # noqa: E402


def _run(tmp_path, kind):
    draft = tmp_path / "Thoughts" / "demo_RESEARCH.md"
    draft.parent.mkdir(parents=True)
    draft.write_text("# research\n")
    (draft.parent / "demo_THOUGHT.md").write_text("# Discovery\n")
    try:
        eng.factcheck_run(
            str(tmp_path / "state"), str(draft), kind, "sess",
            debounce_seconds=0, models=["sonnet"],
            _checker_fn=lambda *a, **k: '{"verdict":"PASS","discrepancies":[]}',
            proj="proj", topic="demo",
        )
    except Exception:
        pass  # round-0 side-effect already ran; downstream dispatch is not under test
    return draft.parent / "demo_CLAIMS.md"


def test_research_round0_creates_claims(tmp_path):
    claims = _run(tmp_path, "research")
    assert claims.exists()
    assert "Parent: [[demo_THOUGHT]]" in claims.read_text()   # hook-clean (spine present)


def test_thought_round0_creates_claims(tmp_path):
    assert _run(tmp_path, "thought").exists()


def test_plan_kind_does_not_create_claims(tmp_path):
    # plan is not a claim-bearing kind -> no _CLAIMS side-effect.
    claims = _run(tmp_path, "plan")
    assert not claims.exists()


# ── DS6 #9: advisory R<N>.md mirror under the slug ───────────────────────
def test_research_round_mirrors_R_marker_under_slug(tmp_path):
    import sys as _sys
    _sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
    import bookkeeping_invariant as bi
    _run(tmp_path, "research")            # PASS mock -> writes R1 + mirror
    mirror = tmp_path / "Thoughts" / "demo_R1.md"
    assert mirror.exists()                                  # advisory copy under the slug
    m = bi.classify("demo_R1.md")
    assert m.bucket == bi.B_ADVISORY and m.type == "R1" and m.slug == "demo"


def test_plan_kind_no_R_mirror(tmp_path):
    _run(tmp_path, "plan")
    assert not (tmp_path / "Thoughts" / "demo_R1.md").exists()

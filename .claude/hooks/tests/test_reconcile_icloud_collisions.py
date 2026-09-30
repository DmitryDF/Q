"""DS8 #14 — iCloud twin reconciler (quarantine-only, base-missing left in place)."""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import reconcile_icloud_collisions as ric  # noqa: E402


def test_classify_three_kinds(tmp_path):
    (tmp_path / "a.md").write_text("same\n")
    (tmp_path / "a 2.md").write_text("same\n")          # exact-dup
    (tmp_path / "b.md").write_text("base\n")
    (tmp_path / "b 2.md").write_text("DIFFERENT\n")      # divergent
    (tmp_path / "c 2.md").write_text("only copy\n")      # base-missing (no c.md)

    kinds = {os.path.basename(it["twin"]): it["kind"] for it in ric.classify(str(tmp_path))}
    assert kinds == {"a 2.md": "exact-dup", "b 2.md": "divergent", "c 2.md": "base-missing"}


def test_apply_quarantines_but_spares_base_missing(tmp_path):
    (tmp_path / "a.md").write_text("same\n")
    (tmp_path / "a 2.md").write_text("same\n")
    (tmp_path / "b.md").write_text("base\n")
    (tmp_path / "b 2.md").write_text("DIFF\n")
    (tmp_path / "c 2.md").write_text("only copy\n")

    ric.reconcile(str(tmp_path), apply=True)
    q = tmp_path / "_icloud_quarantine"
    assert (q / "a 2.md").exists() and (q / "b 2.md").exists()   # quarantined
    assert not (tmp_path / "a 2.md").exists()
    assert (tmp_path / "c 2.md").exists()                        # base-missing left in place
    assert (tmp_path / "a.md").exists() and (tmp_path / "b.md").exists()  # bases untouched


def test_dry_run_moves_nothing(tmp_path):
    (tmp_path / "a.md").write_text("same\n")
    (tmp_path / "a 2.md").write_text("same\n")
    ric.reconcile(str(tmp_path), apply=False)
    assert (tmp_path / "a 2.md").exists()
    assert not (tmp_path / "_icloud_quarantine").exists()

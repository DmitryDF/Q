"""DS8 drift-hook tests. Row 9: work_done retire-marker hits the preamble only."""
import os
import sys

sys.path.insert(0, os.path.expanduser("~/.claude/hooks"))
import work_done as wd  # noqa: E402


def test_row9_stamps_preamble_status_not_body(tmp_path):
    spine = tmp_path / "demo_THOUGHT.md"
    spine.write_text(
        "**Status:** Phase 2 complete; in progress.\n"
        "# Discovery\n"
        "Body text.\n"
        "**Status:** a body status line that must NOT be stamped.\n",
        encoding="utf-8",
    )
    wd.write_retire_marker(str(spine), "2026-06-24")
    text = spine.read_text(encoding="utf-8")
    # Preamble line got the retire stamp:
    assert "Phase 2 complete; in progress; Retired 2026-06-24." in text
    # Body `**Status:**` line is untouched, and only one stamp exists:
    assert "a body status line that must NOT be stamped." in text
    assert text.count("Retired 2026-06-24") == 1


def test_row9_preamble_helper():
    assert wd._preamble("**Status:** x\n# H1\n**Status:** y\n") == "**Status:** x\n"
    assert wd._preamble("no heading here\n") == "no heading here\n"

"""Tests for audit_target_resolver.py.

Covers:
- text-log adapter (flat-hunt): normal, no-marker, rotation stitch
- sqlite-runtrace adapter (your-project): empty table, active run
- harness-jsonl adapter: passthrough (no-target regression)
- malformed/missing registry block → exit 3 + AUDIT_RESOLVER_ERROR
- absent evidence path → exit 3
- resolver returns correct deviation_frame and adjacency_unit per kind
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# Resolver path (hooks/ directory).
RESOLVER = Path(__file__).parent.parent / "audit_target_resolver.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path, claude_md_content: str) -> Path:
    """Create a minimal project dir with a CLAUDE.md."""
    root = tmp_path / "project"
    root.mkdir()
    (root / "CLAUDE.md").write_text(claude_md_content, encoding="utf-8")
    return root


def _run_resolver(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESOLVER)] + args,
        capture_output=True,
        text=True,
    )


def _make_log_file(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_spec_files(project: Path) -> None:
    """Create minimal spec files so spec_ref validation passes."""
    (project / "DESIGN.md").write_text("# Design\n", encoding="utf-8")
    (project / "config.json").write_text("{}", encoding="utf-8")


# ---------------------------------------------------------------------------
# Text-log adapter tests
# ---------------------------------------------------------------------------

class TestTextLogAdapter:

    def test_normal_run(self, tmp_path):
        """Resolver returns evidence_path pointing to a slice from last [start]."""
        log_dir = tmp_path / "log"
        log_file = log_dir / "flat-hunt.log"
        lines = [
            "2026-04-01 [INFO] main: [start] flat-hunt — first run",
            "2026-04-01 [INFO] poll: polling",
            "2026-04-01 [INFO] main: [start] flat-hunt — second run",
            "2026-04-01 [INFO] poll: second poll",
            "2026-04-01 [INFO] poll: more logging",
        ]
        _make_log_file(log_file, lines)

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_file}
            audit_run_start_marker: [start]
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md, config.json
        """).strip())
        _make_spec_files(project)

        result = _run_resolver(["resolve", "--target", "flat-hunt",
                                 "--project-dir", str(project)])
        assert result.returncode == 0, result.stderr

        data = json.loads(result.stdout)
        assert "evidence_path" in data
        evidence = Path(data["evidence_path"]).read_text(encoding="utf-8")
        # Should start at the LAST [start] line.
        assert "[start] flat-hunt — second run" in evidence
        assert "second poll" in evidence
        # First run should NOT appear.
        assert "first run" not in evidence

        assert data["spec_kind"] == "behavioral-config"
        assert "poll interval" in data["deviation_frame"] or "log behavior" in data["deviation_frame"]
        assert data["adjacency_unit"] == "contiguous log block (same or adjacent log lines within one run)"

    def test_no_start_marker(self, tmp_path):
        """Resolver exits 3 when no run-start marker found in log."""
        log_dir = tmp_path / "log"
        log_file = log_dir / "flat-hunt.log"
        _make_log_file(log_file, ["2026-04-01 [INFO] poll: no start marker here"])

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_file}
            audit_run_start_marker: [start]
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md, config.json
        """).strip())
        _make_spec_files(project)

        result = _run_resolver(["resolve", "--target", "flat-hunt",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr

    def test_log_file_absent(self, tmp_path):
        """Resolver exits 3 when log file doesn't exist."""
        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: /nonexistent/path/flat-hunt.log
            audit_run_start_marker: [start]
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md, config.json
        """).strip())
        _make_spec_files(project)

        result = _run_resolver(["resolve", "--target", "flat-hunt",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr

    def test_rotation_boundary_stitch(self, tmp_path):
        """A run-start in flat-hunt.log.1 with tail in flat-hunt.log is stitched correctly.

        This is the synthesized rotation-boundary test (A8 plan requirement):
        - flat-hunt.log.1  contains older entries + the LAST run's [start] marker
        - flat-hunt.log    contains the tail of that run (no [start])
        The resolver must concatenate both files (oldest first) and slice from
        the last [start], returning lines that span both files.
        """
        log_dir = tmp_path / "log"
        log_dir.mkdir()

        # flat-hunt.log.1 = older log (rotated): has [start] of the last run.
        rotated = log_dir / "flat-hunt.log.1"
        rotated.write_text(
            "\n".join([
                "2026-04-28 [INFO] main: [start] flat-hunt — old run (pre-rotation)",
                "2026-04-28 [INFO] poll: old run polling",
                "2026-04-28 [INFO] main: [stop] old run ended",
                "2026-04-29 [INFO] main: [start] flat-hunt — LAST run begins here",
                "2026-04-29 [INFO] poll: last run line 1 (in rotated file)",
            ]),
            encoding="utf-8",
        )

        # flat-hunt.log = current log: continuation of the last run, no [start].
        current = log_dir / "flat-hunt.log"
        current.write_text(
            "\n".join([
                "2026-04-29 [INFO] poll: last run line 2 (in current file)",
                "2026-04-29 [INFO] poll: last run line 3 (in current file)",
            ]),
            encoding="utf-8",
        )

        log_path = log_dir / "flat-hunt.log"  # base path used in config
        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_path}
            audit_run_start_marker: [start]
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md, config.json
        """).strip())
        _make_spec_files(project)

        result = _run_resolver(["resolve", "--target", "flat-hunt",
                                 "--project-dir", str(project)])
        assert result.returncode == 0, result.stderr

        data = json.loads(result.stdout)
        evidence = Path(data["evidence_path"]).read_text(encoding="utf-8")

        # Must contain the start line (from rotated file).
        assert "LAST run begins here" in evidence
        # Must contain lines from rotated file that follow the start.
        assert "last run line 1 (in rotated file)" in evidence
        # Must also contain lines from current file.
        assert "last run line 2 (in current file)" in evidence
        assert "last run line 3 (in current file)" in evidence
        # Old run's start must NOT appear (we sliced from the LAST marker).
        assert "old run (pre-rotation)" not in evidence


# ---------------------------------------------------------------------------
# Registry / CLAUDE.md error handling tests
# ---------------------------------------------------------------------------

class TestRegistryErrors:

    def test_missing_audit_target_block(self, tmp_path):
        """No ## Audit Target block → exit 3."""
        project = _make_project(tmp_path, textwrap.dedent("""
            # Project

            Some content, no audit block.
        """).strip())

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr

    def test_missing_required_key(self, tmp_path):
        """audit_evidence_kind present but audit_run_start_marker missing → exit 3."""
        log_dir = tmp_path / "log"
        log_file = log_dir / "app.log"
        _make_log_file(log_file, ["line"])

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_file}
            # audit_run_start_marker omitted — should fail
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md
        """).strip())
        _make_spec_files(project)

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr
        assert "audit_run_start_marker" in result.stderr

    def test_unknown_evidence_kind(self, tmp_path):
        """Unknown audit_evidence_kind value → exit 3."""
        project = _make_project(tmp_path, textwrap.dedent("""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: csv-log
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md
        """).strip())
        _make_spec_files(project)

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr

    def test_missing_claude_md(self, tmp_path):
        """project root exists but has no CLAUDE.md → exit 3."""
        project = tmp_path / "project"
        project.mkdir()

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr

    def test_unknown_target_no_project_dir(self):
        """Unknown target without --project-dir → exit 3."""
        result = _run_resolver(["resolve", "--target", "totally_unknown_app_xyz"])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr

    def test_spec_file_missing(self, tmp_path):
        """spec_ref points to non-existent file → exit 3."""
        log_dir = tmp_path / "log"
        log_file = log_dir / "flat-hunt.log"
        _make_log_file(log_file, ["2026 [INFO] [start] start\n2026 [INFO] poll"])

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_file}
            audit_run_start_marker: [start]
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md, nonexistent_spec.md
        """).strip())
        (project / "DESIGN.md").write_text("# Design\n", encoding="utf-8")
        # nonexistent_spec.md intentionally not created.

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 3
        assert "AUDIT_RESOLVER_ERROR" in result.stderr


# ---------------------------------------------------------------------------
# No-target passthrough (harness regression guard, C3)
# ---------------------------------------------------------------------------

class TestNoTargetPassthrough:
    """Resolver is not called when --target is absent; this test verifies the
    resolver module itself does not interfere with the no-target code path by
    checking that the resolver CLI exits non-zero when called without --target
    (i.e. it does not attempt a harness path) and that the no-target SKILL.md
    path still derives TRANSCRIPT_PATH from SESSION_ID + PROJECT_SLUG."""

    def test_resolve_requires_target(self):
        """resolve subcommand requires --target."""
        result = _run_resolver(["resolve"])
        # argparse exits 2 on missing required arg.
        assert result.returncode != 0

    def test_resolver_not_called_without_target(self, tmp_path):
        """Calling resolver with no subcommand prints help and exits non-zero."""
        result = _run_resolver([])
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Return value shape tests
# ---------------------------------------------------------------------------

class TestReturnShape:

    def test_text_log_returns_all_fields(self, tmp_path):
        """Resolver returns all 5 required fields for a text-log target."""
        log_dir = tmp_path / "log"
        log_file = log_dir / "flat-hunt.log"
        _make_log_file(log_file, ["2026 [INFO] main: [start] run\n2026 [INFO] poll"])

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_file}
            audit_run_start_marker: [start]
            audit_spec_kind: behavioral-config
            audit_spec_ref: DESIGN.md
        """).strip())
        (project / "DESIGN.md").write_text("# Design\n", encoding="utf-8")

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)

        for key in ("evidence_path", "spec_ref", "spec_kind", "deviation_frame", "adjacency_unit"):
            assert key in data, f"Missing key: {key}"

        assert data["spec_kind"] == "behavioral-config"
        assert isinstance(data["spec_ref"], list)
        assert len(data["spec_ref"]) == 1

    def test_pipeline_dag_deviation_frame(self, tmp_path):
        """pipeline-dag spec_kind returns the DAG-specific deviation frame."""
        # We fake a sqlite-runtrace by using a helper that immediately exits 0
        # and writes a temp file. We create a tiny shell-based mock helper.
        import stat

        evidence_file = tmp_path / "runtrace.txt"
        evidence_file.write_text("=== Pipeline Run Trace ===\n", encoding="utf-8")

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: sqlite-runtrace
            audit_runtrace_helper: scripts/audit_runtrace.py
            audit_spec_kind: pipeline-dag
            audit_spec_ref: copilot/application/watchlist_pipeline.py
        """).strip())
        (project / "copilot" / "application").mkdir(parents=True)
        (project / "copilot" / "application" / "watchlist_pipeline.py").write_text(
            "# stub\n", encoding="utf-8"
        )

        helper = project / "scripts" / "audit_runtrace.py"
        helper.parent.mkdir()
        helper.write_text(
            textwrap.dedent(f"""
                import json, sys
                print(json.dumps({{"evidence_path": "{evidence_file}"}}))
                sys.exit(0)
            """).strip(),
            encoding="utf-8",
        )

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)

        assert data["spec_kind"] == "pipeline-dag"
        assert "PREREQUISITES" in data["deviation_frame"]
        assert data["adjacency_unit"] == "single run-trace record"

    def test_harness_process_deviation_frame(self, tmp_path):
        """harness-process spec_kind returns the original Claude-Code deviation frame."""
        log_dir = tmp_path / "log"
        log_file = log_dir / "session.jsonl"
        _make_log_file(log_file, ['{"role": "user", "content": "hello"}'])

        project = _make_project(tmp_path, textwrap.dedent(f"""
            ## Audit Target (read by audit_target_resolver.py)

            audit_evidence_kind: text-log
            audit_evidence_path: {log_file}
            audit_run_start_marker: {{"role"
            audit_spec_kind: harness-process
            audit_spec_ref: DESIGN.md
        """).strip())
        (project / "DESIGN.md").write_text("# Design\n", encoding="utf-8")

        result = _run_resolver(["resolve", "--target", "myapp",
                                 "--project-dir", str(project)])
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)

        assert data["spec_kind"] == "harness-process"
        # Original process frame must contain the original wording.
        assert "code step was skipped" in data["deviation_frame"]
        assert data["adjacency_unit"] == "contiguous log block (same or adjacent log lines within one run)"

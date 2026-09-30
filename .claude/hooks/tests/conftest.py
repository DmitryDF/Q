"""Pytest bootstrap for the hooks test suite.

Several tests insert the LIVE harness (`~/.claude/hooks`) onto `sys.path` and
import shared modules (`_factcheck_engine`, `research_pipeline`) from there.
Under a full-suite run that can cache a STALE live copy of those modules under
their bare import name, so a test that expects a clone-local symbol (e.g. the
S7 `_run_coverage_axis_gate`) then sees a module that lacks it — a pure
test-ordering / module-caching artifact, not a product bug.

This conftest pins the resolution: it puts THIS clone's hooks dir at the front
of `sys.path` and eagerly imports the two shared modules from the clone BEFORE
any test module runs, so every `import _factcheck_engine` / `import
research_pipeline` in the suite binds the clone copy consistently.

Scope: only the two cross-cutting engine modules are pinned. Bookkeeping/other
tests that deliberately import from `~/.claude/hooks` still do so for THEIR own
modules; only the shared-engine name collision is neutralized here.
"""
import importlib
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parents[1])

# Clone hooks dir must win over any live-harness path a test inserts.
if _HOOKS_DIR in sys.path:
    sys.path.remove(_HOOKS_DIR)
sys.path.insert(0, _HOOKS_DIR)

for _name in ("_factcheck_engine", "research_pipeline"):
    _cached = sys.modules.get(_name)
    _from_clone = _cached is not None and str(
        getattr(_cached, "__file__", "")
    ).startswith(_HOOKS_DIR)
    if not _from_clone:
        sys.modules.pop(_name, None)
        importlib.import_module(_name)

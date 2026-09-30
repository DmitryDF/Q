#!/usr/bin/env python3
"""S2 (streamed-dancing-goose / A2) — locked Discovery sections are guarded at the
WRITE PATH, predicate-first, and every spine-writing call site is inventoried.

A2's validation gate, one class per clause:

  RefuseCases   a direct Python write that changes a locked body is refused BEFORE the
                temp write (the pre-S1 `append_metrics` shape — rows inserted into
                `## Metrics` — is the confirmed case); an unreadable locked heading while
                Discovery moves is refused (refuse-to-guess); nothing — not even a
                `.md.tmp` — is written on refusal.
  AllowCases    every guarded writer and every inventoried writer (incl. the migration
                backfill) runs on a LOCKED fixture and leaves the locked-fields hash
                unchanged; a write that changes no locked body needs no permission;
                bodies are compared STRIPPED (a Discovery-to-EOF spine + a writer that
                rstrips is allowed); the Step-9 token grants permission.
  Inventory     a scan of the tree for spine-writing functions fails on any call site
                not registered in `SPINE_WRITE_INVENTORY`, and is proven non-vacuous on a
                synthetic unregistered writer.
  CopyCount     the path-normalisation idiom's copy count is pinned, so the rule this
                plan watched re-duplicate cannot grow a new copy unnoticed (S3 lowers it).

Run: env CLAUDE_CONFIG_DIR=<clone> python3 hooks/tests/test_s2_locked_write_guard.py
"""

from __future__ import annotations

import ast
import glob
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg  # noqa: E402
import taskmanagement as tm  # noqa: E402
import work_done as wd  # noqa: E402
import bookkeeping_migrate as bm  # noqa: E402

SID = "abcd1234-s2-locked-write-guard-00000000"
TODAY = datetime.now().date().isoformat()
LOCK = "<!-- locked: 0f1e2d3c-4b5a-6978-8a9b-c0d1e2f3a4b5 2026-09-01T00:00:00Z -->"

LOCKED_TEMPLATE = f"""# Idea

## Problem
Something.

# Discovery
{LOCK}

## Guiding Policy
gp body

## Desired Outcome
do body

## Desired Solution
ds body

## Metrics
OMTM: x

## Q&A
q body

# Solution Design

_(pending)_

# Implementation Details

## Slice Register

<!-- L:slice id=S1 status=NOW updated=2026-09-01 -->

## Sessions

## Next Session Prompt

paste me
"""

LOCKED_DISC_EOF = f"""# Old Topic — Thought File

**Status:** active.

# Discovery
{LOCK}

## Guiding Policy
gp body

## Desired Outcome
do body

## Desired Solution
ds body

## Metrics
OMTM: x
"""

UNREADABLE = LOCKED_DISC_EOF.replace("## Metrics\n", "## Metrics:\n")


class _Bound:
    def __init__(self, spine: Path, token=None, phase="implementation"):
        self.state = {"thought_file_path": str(spine), "phase": phase,
                      "clarification_active_session": token}

    def __enter__(self):
        self._orig = ppg._resolve_topic
        ppg._resolve_topic = lambda sid: ("Root", "s2-topic", self.state)
        return self

    def __exit__(self, *exc):
        ppg._resolve_topic = self._orig


class S2Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s2_guard_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._orig_ledger = ppg._record_ledger_write
        ppg._record_ledger_write = lambda p: None
        self.addCleanup(setattr, ppg, "_record_ledger_write", self._orig_ledger)

    def spine(self, body, name="s2_THOUGHT.md") -> Path:
        p = self.tmp / name
        p.write_text(body, encoding="utf-8")
        return p

    def assertHashUnchanged(self, before_text, path):
        self.assertEqual(ppg._discovery_locked_fields_hash(path.read_text(encoding="utf-8")),
                         ppg._discovery_locked_fields_hash(before_text))

    def assertNoTmp(self, path):
        self.assertEqual(sorted(path.parent.glob("*.tmp")), [], "a temp file survived a refusal")


# --------------------------------------------------------------------------- #
# Refuse cases
# --------------------------------------------------------------------------- #
class RefuseCases(S2Base):

    def test_pre_s1_append_metrics_shape_is_refused_before_any_byte(self):
        """The confirmed defect: metrics rows inserted into `## Metrics`. Reproduced as
        a proposed text, guarded through the module's one write path."""
        p = self.spine(LOCKED_TEMPLATE)
        current = p.read_text(encoding="utf-8")
        block = f"### {TODAY} session [sid:{SID[:8]}]\n- Duration: 30min\n"
        proposed = current.replace("## Metrics\nOMTM: x\n", f"## Metrics\nOMTM: x\n\n{block}")
        verdict = ppg.locked_discovery_change(current, proposed)
        self.assertEqual(verdict["changed"], ["## Metrics"])
        with self.assertRaises(ppg.LockedDiscoveryWriteRefused) as cm:
            ppg._write_spine_guarded(p, current, proposed, session_id=SID, writer="append_metrics")
        self.assertIn("`## Metrics`", str(cm.exception))
        self.assertIn("append_metrics", str(cm.exception))
        self.assertEqual(p.read_text(encoding="utf-8"), current, "file changed on refusal")
        self.assertNoTmp(p)

    def test_refusal_is_a_value_error_so_cli_verbs_render_it(self):
        self.assertTrue(issubclass(ppg.LockedDiscoveryWriteRefused, ValueError))

    def test_unreadable_heading_is_treated_as_absent_at_both_layers(self):
        """`## Metrics:` is not a heading the locator reads. The tool-layer hook
        treats it as absent (discovery-field-predicate-coherence A3 removed its
        refuse-to-guess arm); the write path gives the SAME answer — one rule."""
        p = self.spine(UNREADABLE)
        current = p.read_text(encoding="utf-8")
        proposed = current.replace("OMTM: x", "OMTM: y")
        v = ppg.locked_discovery_change(current, proposed)
        self.assertNotIn("## Metrics", v["changed"], "an unreadable heading is absent, not a field")
        self.assertNotIn("unreadable", v, "the refuse-to-guess arm must not come back silently")
        # …but its TEXT is still defended: with `## Metrics:` invisible to the
        # locator, the preceding locked sibling's body runs through it, so the
        # edit is refused via `## Desired Solution` — no guessing involved.
        self.assertEqual(v["changed"], ["## Desired Solution"])
        with self.assertRaises(ppg.LockedDiscoveryWriteRefused):
            ppg.guard_locked_discovery_write(p, current, proposed, session_id=None, writer="x")
        # a write that leaves Discovery alone is allowed on the same spine
        outside = current.rstrip() + "\n\n# Implementation Details\n\n## Sessions\n"
        ppg.guard_locked_discovery_write(p, current, outside, session_id=None, writer="x")
        # and the hook module agrees: no `_field_status` predicate exists any more
        import _discovery_lock_check as hook
        self.assertFalse(hasattr(hook, "_field_status"))

    def test_demotion_of_a_readable_heading_is_refused(self):
        """`## Metrics` -> `### Metrics`: current body is text, proposed is None —
        the structural defence that survives the removal of refuse-to-guess."""
        p = self.spine(LOCKED_TEMPLATE)
        current = p.read_text(encoding="utf-8")
        proposed = current.replace("## Metrics\n", "### Metrics\n")
        # `## Metrics` reads as changed (body -> None) AND the preceding sibling
        # absorbs the demoted text, so it changes too — both are refusals.
        self.assertEqual(ppg.locked_discovery_change(current, proposed)["changed"],
                         ["## Desired Solution", "## Metrics"])
        with self.assertRaises(ppg.LockedDiscoveryWriteRefused):
            ppg._write_spine_guarded(p, current, proposed, session_id=None, writer="x")
        self.assertNoTmp(p)

    def test_no_permission_without_token_even_for_the_bound_session(self):
        p = self.spine(LOCKED_TEMPLATE)
        current = p.read_text(encoding="utf-8")
        proposed = current.replace("gp body", "gp body edited")
        with _Bound(p, token=None):
            with self.assertRaises(ppg.LockedDiscoveryWriteRefused):
                ppg.guard_locked_discovery_write(p, current, proposed, session_id=SID, writer="x")
        with _Bound(p, token="someone-else"):
            with self.assertRaises(ppg.LockedDiscoveryWriteRefused):
                ppg.guard_locked_discovery_write(p, current, proposed, session_id=SID, writer="x")

    def test_next_session_prompt_verb_refuses_on_locked_spine_via_cli_shape(self):
        """The `write-next-session-prompt` verb's refusal is the verb's own REJECT
        shape (exit 2), exercised through the guarded helper it now calls."""
        p = self.spine(LOCKED_DISC_EOF)
        current = p.read_text(encoding="utf-8")
        proposed = current.rstrip() + "\n\n## Next Session Prompt\n\nnew prompt\n"
        # NSP appended inside a Discovery-to-EOF spine extends the last locked body.
        self.assertEqual(ppg.locked_discovery_change(current, proposed)["changed"], ["## Metrics"])
        with self.assertRaises(ppg.LockedDiscoveryWriteRefused):
            ppg._write_spine_guarded(p, current, proposed, session_id=None,
                                     writer="write-next-session-prompt")
        self.assertNoTmp(p)


# --------------------------------------------------------------------------- #
# Allow cases — every writer on a locked fixture, hash unchanged
# --------------------------------------------------------------------------- #
class AllowCases(S2Base):

    def test_guarded_append_metrics_and_annotate_on_locked_spine(self):
        for body in (LOCKED_TEMPLATE, LOCKED_DISC_EOF):
            p = self.spine(body)
            with _Bound(p):
                ppg.annotate_session(SID, "Shipped under lock.", writer="work-done")
                ppg.append_metrics(SID, {"duration_min": 5, "opus_tokens_k": 1})
                ppg.annotate_session(SID, "Close note.", writer="close")
            self.assertHashUnchanged(body, p)
            t = p.read_text(encoding="utf-8")
            self.assertIn("Shipped under lock.", t)
            self.assertIn("Duration: 5min", t)

    def test_guarded_write_phase_marker_on_locked_spine_incl_no_anchor(self):
        for body in (LOCKED_TEMPLATE, LOCKED_DISC_EOF):
            p = self.spine(body)
            with _Bound(p):
                r = ppg.write_phase_marker(SID, "start", "implementation")
            self.assertEqual(r["status"], "section_created")
            self.assertHashUnchanged(body, p)
            lines = p.read_text(encoding="utf-8").split("\n")
            disc = ppg._discovery_h1_bounds(lines)
            reg = next(i for i, l in enumerate(lines) if l.rstrip() == ppg._PHASE_REGISTER_HEADING)
            self.assertFalse(disc[0] < reg < disc[1], "phase register landed inside Discovery")

    def test_guarded_link_plan_to_thought_on_locked_spine(self):
        for body in (LOCKED_TEMPLATE, LOCKED_DISC_EOF):
            p = self.spine(body)
            marker_dir = self.tmp / "plan_mode_init"
            marker_dir.mkdir(exist_ok=True)
            (marker_dir / f"{SID}.json").write_text(
                '{"mode": "A", "thought_path": "%s"}' % str(p), encoding="utf-8")
            orig = ppg.PLAN_MODE_INIT_DIR
            ppg.PLAN_MODE_INIT_DIR = marker_dir
            try:
                r = ppg.link_plan_to_thought(SID, str(self.tmp / "s2-20260901000000_PLAN.md"))
            finally:
                ppg.PLAN_MODE_INIT_DIR = orig
            self.assertEqual(r["status"], "linked")
            self.assertHashUnchanged(body, p)

    def test_stripped_comparison_allows_rstrip_append_on_discovery_to_eof(self):
        p = self.spine(LOCKED_DISC_EOF)
        current = p.read_text(encoding="utf-8")
        # the house idiom: rstrip, then open a new H1 — raw bytes "inside" Metrics change
        proposed = current.rstrip() + "\n\n# Implementation Details\n\n## Sessions\n"
        v = ppg.locked_discovery_change(current, proposed)
        self.assertEqual(v["changed"], [], "stripped bodies must compare equal")
        ppg._write_spine_guarded(p, current, proposed, session_id=None, writer="x")
        self.assertHashUnchanged(current, p)

    def test_token_grants_permission_to_change_a_locked_body(self):
        p = self.spine(LOCKED_TEMPLATE)
        current = p.read_text(encoding="utf-8")
        proposed = current.replace("gp body", "gp body edited")
        with _Bound(p, token=SID):
            ppg._write_spine_guarded(p, current, proposed, session_id=SID, writer="clarification")
        self.assertIn("gp body edited", p.read_text(encoding="utf-8"))

    def test_unlocked_spine_is_never_checked(self):
        body = LOCKED_TEMPLATE.replace(LOCK + "\n", "")
        p = self.spine(body)
        current = p.read_text(encoding="utf-8")
        proposed = current.replace("gp body", "gp body edited")
        v = ppg.guard_locked_discovery_write(p, current, proposed, session_id=None, writer="x")
        self.assertFalse(v["locked"])

    # ---- inventoried writers (outside this module's write path) ---- #

    def test_inventoried_write_slice_row_leaves_hash_unchanged(self):
        for body in (LOCKED_TEMPLATE,):
            p = self.spine(body)
            tm.write_slice_row(p, "S1", updates={"status": "SHIPPED"})
            self.assertHashUnchanged(body, p)
            self.assertIn("status=SHIPPED", p.read_text(encoding="utf-8"))

    def test_inventoried_retire_marker_leaves_hash_unchanged(self):
        # the retire marker stamps a `**Status:**` line in the PREAMBLE (above the
        # first H1); give the template fixture one, as real spines carry.
        with_status = "**Status:** active.\n\n" + LOCKED_TEMPLATE
        for body in (with_status, LOCKED_DISC_EOF):
            p = self.spine(body)
            wd.write_retire_marker(p, "2026-09-20")
            self.assertHashUnchanged(body, p)
            self.assertIn("Retired 2026-09-20", p.read_text(encoding="utf-8"))

    def test_inventoried_migration_backfill_leaves_hash_unchanged(self):
        """The backfill inserts a wikilink directly under `# Discovery` (before the
        first locked heading) and a `Parent:` line at the top — neither touches a
        locked body."""
        for body in (LOCKED_TEMPLATE, LOCKED_DISC_EOF):
            new, did = bm._insert_spine_link(body, "s2-20260901000000_RESEARCH", "# Discovery")
            self.assertTrue(did)
            self.assertEqual(ppg.locked_discovery_change(body, new)["changed"], [])
            self.assertEqual(ppg._discovery_locked_fields_hash(new),
                             ppg._discovery_locked_fields_hash(body))
            new2, did2 = bm._insert_parent_line(body, "s2-20260901000000_THOUGHT")
            self.assertTrue(did2)
            self.assertEqual(ppg.locked_discovery_change(body, new2)["changed"], [])


# --------------------------------------------------------------------------- #
# Inventory — every spine-writing call site is registered
# --------------------------------------------------------------------------- #
_WRITE_RE = re.compile(
    r"\.write_text\(|\.write_bytes\(|open\([^)]*['\"]w|os\.replace\(|\.replace\(p\)|"
    r"\.replace\(path\)|\.rename\(|_atomic_write\(|_atomic_write_text\(|_write_spine_guarded\(")
_SPINE_RE = re.compile(r"thought_file_path|_THOUGHT|spine|_resolve_thought_path|thought_path")
_SKIP_FILES = {"bookkeeping_migrate.py"}   # registered with a wildcard; module-level writer


def scan_spine_writers(paths):
    """(module, function) pairs whose body carries BOTH a write primitive and a spine
    signal — the candidate writers. Test helpers and tests are the caller's business."""
    found = set()
    for path in paths:
        src = Path(path).read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.split("\n")
        module = Path(path).stem
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                body = "\n".join(lines[node.lineno - 1: node.end_lineno])
                if _WRITE_RE.search(body) and _SPINE_RE.search(body):
                    found.add((module, node.name))
    return found


def unregistered(found, inventory):
    out = set()
    for module, func in found:
        if (module, func) in inventory or (module, "*") in inventory:
            continue
        out.add((module, func))
    return out


class Inventory(unittest.TestCase):

    # Functions the scan flags that are NOT spine writers — each with the reason.
    # A new entry here is a claim about the code and must say why.
    _KNOWN_NON_WRITERS = {
        ("pre_plan_gates", "_ensure_canonical_record"): "writes topic-state JSON, names the spine as data",
        ("pre_plan_gates", "reconcile_inverted"): "writes topic-state JSON",
        ("pre_plan_gates", "_create_topic_locked"): "writes topic-state JSON",
        ("pre_plan_gates", "relocate_plan_after_approval"): "moves a PLAN file, never edits a spine",
        ("pre_plan_gates", "_atomic_write_text"): "the primitive itself; every spine caller goes through _write_spine_guarded",
        ("pre_plan_gates", "_write_spine_guarded"): "the guarded write path itself",
        ("_factcheck_engine", "_run_factcheck_rounds"): "writes R<N>.md round files beside the draft",
        ("_factcheck_engine", "_write_superseded_note"): "writes into a research draft, not a spine",
        ("claims_registry", "ensure_exists"): "creates a _CLAIMS.md registry beside the spine",
        ("check_framing_surface_records", "_self_test"): "self-test fixture writer",
        ("work_done", "write_retire_marker"): "lock wrapper around _write_retire_marker_unlocked (registered)",
    }

    def _tree(self):
        return sorted(p for p in glob.glob(str(HOOKS / "*.py"))
                      if Path(p).name not in _SKIP_FILES)

    def test_every_spine_writer_is_registered(self):
        found = scan_spine_writers(self._tree())
        missing = unregistered(found, ppg.SPINE_WRITE_INVENTORY) - set(self._KNOWN_NON_WRITERS)
        self.assertEqual(missing, set(),
                         f"spine-writing call sites not in SPINE_WRITE_INVENTORY: {sorted(missing)} "
                         "— register each with a disposition (guarded / inventoried / exempt)")

    def test_registered_guarded_writers_actually_call_the_guarded_path(self):
        src = (HOOKS / "pre_plan_gates.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        lines = src.split("\n")
        bodies = {n.name: "\n".join(lines[n.lineno - 1: n.end_lineno])
                  for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for (module, func), disp in ppg.SPINE_WRITE_INVENTORY.items():
            if module == "pre_plan_gates" and str(disp).startswith("guarded"):
                self.assertIn("_write_spine_guarded(", bodies[func],
                              f"{func} is registered as guarded but does not call _write_spine_guarded")
                if func == "main":
                    # main() also writes the handoff-verify marker file (not a spine);
                    # its one spine write is the write-next-session-prompt verb.
                    seg = bodies[func].split('cmd == "write-next-session-prompt"', 1)[1]
                    seg = seg.split('elif cmd == "handoff-freshness"', 1)[0]
                    self.assertNotIn(".write_text(", seg,
                                     "write-next-session-prompt still carries a raw write_text")
                    continue
                self.assertNotIn(".write_text(", bodies[func].replace("_write_spine_guarded(", ""),
                                 f"{func} still carries a raw write_text beside the guarded path")

    def test_scan_fails_on_an_unregistered_writer(self):
        """Non-vacuous: a synthetic module with an unregistered spine writer is flagged."""
        tmp = Path(tempfile.mkdtemp(prefix="s2_inv_"))
        self.addCleanup(shutil.rmtree, tmp, True)
        mod = tmp / "rogue_writer.py"
        mod.write_text(
            "from pathlib import Path\n"
            "def stamp(thought_path):\n"
            "    p = Path(thought_path)\n"
            "    p.write_text(p.read_text() + '\\nstamped', encoding='utf-8')\n",
            encoding="utf-8")
        found = scan_spine_writers([str(mod)])
        self.assertEqual(found, {("rogue_writer", "stamp")})
        self.assertEqual(unregistered(found, ppg.SPINE_WRITE_INVENTORY), {("rogue_writer", "stamp")})

    def test_exemption_is_explicit_in_the_inventory(self):
        disp = ppg.SPINE_WRITE_INVENTORY[("clarify.translating_repository", "*")]
        self.assertTrue(disp.startswith("exempt"))
        self.assertIn("TODO", disp)


# --------------------------------------------------------------------------- #
# Copy count — the path-normalisation idiom cannot grow a new copy unnoticed
# --------------------------------------------------------------------------- #
class CopyCount(unittest.TestCase):
    # S2 pinned the count the tree had then: four inline realpath lines across two
    # copies of the containment normalisation in pre_plan_gates.py. S3 collapsed them
    # into ONE named function (`_norm_path`) and lowered the pin to 1; the
    # deliberately separate stdlib copy in _discovery_lock_check._norm stays at 1.
    # Anyone adding a copy raises a pin, and must say why.
    PRE_PLAN_GATES_REALPATH_LINES = 1
    LOCK_HOOK_REALPATH_LINES = 1

    def _count(self, path):
        return sum(1 for ln in Path(path).read_text(encoding="utf-8").split("\n")
                   if "os.path.realpath(" in ln and not ln.strip().startswith("#"))

    def test_pre_plan_gates_normalisation_copy_count_is_pinned(self):
        self.assertEqual(self._count(HOOKS / "pre_plan_gates.py"), self.PRE_PLAN_GATES_REALPATH_LINES)

    def test_lock_hook_keeps_its_one_stdlib_copy(self):
        self.assertEqual(self._count(HOOKS / "_discovery_lock_check.py"), self.LOCK_HOOK_REALPATH_LINES)


if __name__ == "__main__":
    unittest.main(verbosity=1)

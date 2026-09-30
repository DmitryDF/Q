#!/usr/bin/env python3
"""streamed-dancing-goose S7 (A7) — `/close`'s session block gains the delivery
record.

The validation gate, one class per clause:

  D   The block carries commit/diff links, the delivered slice name, the updated
      counter and the next slice — additive rows under the SAME writer marker,
      rendered only when their payload keys are present; the duration and token
      rows are byte-unchanged.
  L   The links are derived from the repo's remote by code (`delivery_links`),
      ssh and https shapes alike, and degrade to the bare sha with no remote —
      never a broken or hand-typed URL. The CLI verb prints the same.
  A   The annotate entry beside the block is untouched, through a real
      annotate → append → re-append walk over a fixture spine, with the locked
      hash unchanged.

Run: env CLAUDE_CONFIG_DIR=<clone> python3 hooks/tests/test_s7_delivery_record.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude"))) / "hooks"
sys.path.insert(0, str(HOOKS))

import pre_plan_gates as ppg  # noqa: E402

SID = "abcd7777-s7-delivery-record-000000000"
WD = ppg.SESSION_WRITER_WORK_DONE
METRICS = {"duration_min": 30, "opus_tokens_k": 100, "tasks_completed": 3}
DELIVERY = {"delivered_slice": "S6 — the v2 door files a validator-passing line",
            "commit_sha": "812b9fe1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "commit_url": "https://example.test/o/r/commit/812b9fe1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "diff_url": "https://example.test/o/r/compare/da37a5e4...812b9fe1",
            "diff_range": "da37a5e4..812b9fe1",
            "slice_counter": "6/8 slices done",
            "next_slice": "S7 — /close's block gains the delivery record"}

SPINE = """# Topic — Thought File

**Status:** active.

# Discovery

## Guiding Policy
gp body

## Metrics
OMTM: x

# Implementation Details

## Sessions

*Updated at each /close.*
"""


class _Bound:
    def __init__(self, spine: Path):
        self.state = {"thought_file_path": str(spine), "phase": "implementation"}

    def __enter__(self):
        self._orig = ppg._resolve_topic
        ppg._resolve_topic = lambda sid: ("Root", "s7-topic", self.state)
        return self

    def __exit__(self, *exc):
        ppg._resolve_topic = self._orig


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s7_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self._orig_ledger = ppg._record_ledger_write
        ppg._record_ledger_write = lambda p: None
        self.addCleanup(setattr, ppg, "_record_ledger_write", self._orig_ledger)


# --------------------------------------------------------------------------- #
# D — the rows
# --------------------------------------------------------------------------- #
class DeliveryRows(unittest.TestCase):

    def test_D1_every_delivery_row_renders_from_its_own_key(self):
        rows = ppg._format_delivery_rows(DELIVERY)
        self.assertEqual(rows, [
            "- Delivered: S6 — the v2 door files a validator-passing line",
            "- Commit: [812b9fe1](https://example.test/o/r/commit/812b9fe1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)",
            "- Diff: [da37a5e4..812b9fe1](https://example.test/o/r/compare/da37a5e4...812b9fe1)",
            "- Counter: 6/8 slices done",
            "- Next: S7 — /close's block gains the delivery record",
        ])

    def test_D2_no_keys_means_no_rows_and_the_metrics_rows_are_byte_unchanged(self):
        self.assertEqual(ppg._format_delivery_rows({}), [])
        self.assertEqual(ppg._format_delivery_rows(METRICS), [])
        before = ["- Duration: 30min", "- Opus tokens: ~100k", "- Tasks completed: 3"]
        self.assertEqual(ppg._format_metrics_rows(METRICS), before)
        self.assertEqual(ppg._format_metrics_rows({**METRICS, **DELIVERY})[:3], before,
                         "delivery rows are appended AFTER the existing rows, which do not change")

    def test_D3_a_sha_with_no_url_degrades_to_the_short_sha_and_a_diff_needs_a_url(self):
        rows = ppg._format_delivery_rows({"commit_sha": "0123456789abcdef", "diff_range": "a..b"})
        self.assertEqual(rows, ["- Commit: 01234567"])
        rows = ppg._format_delivery_rows({"commit_sha": "0123456789abcdef", "diff_url": "u"})
        self.assertEqual(rows, ["- Commit: 01234567", "- Diff: [01234567](u)"])

    def test_D4_delivery_alone_is_not_no_metrics_captured(self):
        rows = ppg._format_metrics_rows({"delivered_slice": "S6"})
        self.assertEqual(rows, ["- Delivered: S6"])
        self.assertEqual(ppg._format_metrics_rows({}), ["- (no metrics captured)"])


# --------------------------------------------------------------------------- #
# L — links from the remote, by code
# --------------------------------------------------------------------------- #
class Links(_Tmp):

    def _repo(self, remote=None):
        r = self.tmp / "repo"
        r.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
        def g(*a):
            return subprocess.run(["git", "-C", str(r), *a], check=True, capture_output=True,
                                  text=True, env=env).stdout.strip()
        g("init", "-q")
        (r / "f").write_text("1\n"); g("add", "f"); g("commit", "-q", "-m", "one")
        first = g("rev-parse", "HEAD")
        (r / "f").write_text("2\n"); g("add", "f"); g("commit", "-q", "-m", "two")
        second = g("rev-parse", "HEAD")
        if remote:
            g("remote", "add", "origin", remote)
        return r, first, second

    def test_L1_remote_web_base_handles_ssh_and_https_and_refuses_the_rest(self):
        self.assertEqual(ppg.remote_web_base("git@github.com:you/your-dotfiles.git"),
                         "https://github.com/you/your-dotfiles")
        self.assertEqual(ppg.remote_web_base("ssh://git@github.com/o/r.git"), "https://github.com/o/r")
        self.assertEqual(ppg.remote_web_base("https://github.com/o/r.git"), "https://github.com/o/r")
        self.assertEqual(ppg.remote_web_base("https://github.com/o/r/"), "https://github.com/o/r")
        self.assertIsNone(ppg.remote_web_base("/Users/x/bare.git"))
        self.assertIsNone(ppg.remote_web_base(""))
        self.assertIsNone(ppg.remote_web_base(None))

    def test_L2_links_are_derived_from_an_ssh_remote_with_the_first_parent_as_base(self):
        r, first, second = self._repo("git@github.com:o/r.git")
        d = ppg.delivery_links(r, second)
        self.assertEqual(d["commit_sha"], second)
        self.assertEqual(d["commit_url"], f"https://github.com/o/r/commit/{second}")
        self.assertEqual(d["diff_url"], f"https://github.com/o/r/compare/{first}...{second}")
        self.assertEqual(d["diff_range"], f"{first[:8]}..{second[:8]}")

    def test_L3_a_short_sha_and_an_explicit_base_resolve(self):
        r, first, second = self._repo("https://github.com/o/r.git")
        d = ppg.delivery_links(r, second[:7], base_sha=first)
        self.assertEqual(d["commit_sha"], second)
        self.assertEqual(d["diff_url"], f"https://github.com/o/r/compare/{first}...{second}")

    def test_L4_no_remote_degrades_to_the_bare_sha_with_null_urls_and_a_root_commit_has_no_diff(self):
        r, first, second = self._repo(None)
        d = ppg.delivery_links(r, second)
        self.assertEqual(d["commit_sha"], second)
        self.assertIsNone(d["commit_url"]); self.assertIsNone(d["diff_url"])
        rows = ppg._format_delivery_rows(d)
        self.assertEqual(rows, [f"- Commit: {second[:8]}"])
        d0 = ppg.delivery_links(r, first)        # root commit: no parent, no range
        self.assertEqual(d0["diff_range"], first[:8]); self.assertIsNone(d0["diff_url"])

    def test_L5_a_missing_repo_never_raises(self):
        d = ppg.delivery_links(self.tmp / "nope", "deadbeef")
        self.assertEqual(d, {"commit_sha": "deadbeef", "commit_url": None, "diff_url": None,
                             "diff_range": "deadbeef"})

    def test_L6_the_cli_verb_prints_the_same_json_and_exits_0(self):
        r, first, second = self._repo("git@github.com:o/r.git")
        out = subprocess.run([sys.executable, str(HOOKS / "pre_plan_gates.py"), "delivery-links",
                              str(r), second, "--base", first], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), ppg.delivery_links(r, second, base_sha=first))
        usage = subprocess.run([sys.executable, str(HOOKS / "pre_plan_gates.py"), "delivery-links"],
                               capture_output=True, text=True)
        self.assertEqual(usage.returncode, 2)


# --------------------------------------------------------------------------- #
# A — the annotate entry beside it is untouched
# --------------------------------------------------------------------------- #
class AnnotateUntouched(_Tmp):

    def test_A1_delivery_rows_land_in_the_block_beside_the_annotate_bullet_and_replace_only_themselves(self):
        p = self.tmp / "s7_THOUGHT.md"
        p.write_text(SPINE, encoding="utf-8")
        h0 = ppg._discovery_locked_fields_hash(SPINE)
        with _Bound(p):
            ppg.annotate_session(SID, "Shipped S6.", writer=WD)
            ppg.append_metrics(SID, {**METRICS, **DELIVERY})
        t1 = p.read_text(encoding="utf-8")
        lines = t1.split("\n")
        bullet = [l for l in lines if "Shipped S6." in l]
        self.assertEqual(len(bullet), 1)
        for row in ("- Duration: 30min", "- Delivered: S6 — the v2 door files a validator-passing line",
                    "- Commit: [812b9fe1](", "- Diff: [da37a5e4..812b9fe1](", "- Counter: 6/8 slices done",
                    "- Next: S7 — /close's block gains the delivery record"):
            self.assertTrue(any(l.startswith(row) and l.endswith(ppg._CLOSE_METRICS_MARKER) for l in lines), row)
        # One block: header once, bullet and rows under it, nothing in Discovery.
        header = ppg._session_block_header(__import__("datetime").datetime.now().date().isoformat(), SID)
        self.assertEqual(t1.count(header), 1)
        self.assertNotIn("Delivered:", ppg.discovery_section_body(t1))
        self.assertEqual(ppg._discovery_locked_fields_hash(t1), h0)
        # Re-close with a new counter: only the close-marked lines change; the bullet is byte-identical.
        with _Bound(p):
            ppg.append_metrics(SID, {**METRICS, **DELIVERY, "slice_counter": "7/8 slices done"})
        t2 = p.read_text(encoding="utf-8")
        self.assertEqual([l for l in t2.split("\n") if "Shipped S6." in l], bullet)
        self.assertIn("- Counter: 7/8 slices done", t2)
        self.assertNotIn("- Counter: 6/8 slices done", t2)
        self.assertEqual(t2.count("- Delivered:"), 1)
        self.assertEqual(ppg._discovery_locked_fields_hash(t2), h0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

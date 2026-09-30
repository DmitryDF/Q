#!/usr/bin/env python3
"""reap CLI — quarantine orphan `plain-*` topic-state AND reconcile divergent-key
identity twins (the __Root/__Projects class).

Two concerns, one safe tool (session-topic-identity-coherence plan, S4/A4):

  reap-orphans [STATE_DIR] [--apply]
      bookkeeping-model drift Row 1: `/work-start` mints durable topic state with
      a `plain-<sid8>` slug when it hits an unresolved `Master plan:` wikilink.
      Those never trace to a real spine; find + QUARANTINE them (mv to `_reaped/`
      — reversible, never delete). `_active.json` is left untouched.

  find-mis-keyed [--state-dir DIR]
      Read-only report of every slug whose on-disk key(s) diverge from the
      canonical derivation (>1 twin, or a lone mis-keyed twin).

  reconcile [SLUG|all] [--apply] [--state-dir DIR]
      Collapse a slug's divergent-key twins onto ONE canonical record (the
      historical backfill = `reconcile all --apply`). DRY-RUN unless --apply.
      --apply acquires every twin's lock in lexicographic order before mutating
      (TOCTOU-closed), skips a topic whose twin holds a live lock, and mv's the
      redundant twin(s) to `_reaped/*.bak-<ts>` (never rm).

  reconcile-inverted [--apply] [--state-dir DIR]
  reconcile-inverted (--slug SLUG | --file FILE) --resolve keep-inverted|keep-canonical
      One-shot supervised backfill of the INVERTED / swapped-key era
      (`<project>__<topic>.json`) that `reconcile` deliberately excludes.
      Per record: canonical free -> atomic rename in place; canonical exists and
      the inverted record is redundant -> quarantine it to `_reaped/`; canonical
      exists and the inverted record is RICHER -> skip in place and log to the
      persistent MERGE_REVIEW ledger for an explicit `--resolve` re-run.
      Never deletes; never overwrites without a verified backup of the
      incumbent; never leaves a live canonical path empty. NON-INTERACTIVE.

  prune-active [--apply] [--prune-window 30d|12h|SECONDS]
               [--state-dir DIR] [--locks-dir DIR]
      Standing pruner for the shared `_active.json` ledger. Unlike the inverted
      class, dead `plain-*` session rows keep accumulating, so this is a small
      recurring verb rather than a one-shot. A row is prunable only when its
      topic slug is `plain-*`, its `updated` stamp is older than the window, AND
      its topic lock can be ACQUIRED — liveness is proved by acquisition, never
      by a read-only probe that fails open. The rewrite runs under the one
      `_active.json` lock, after a VERIFIED backup COPY to `_reaped/`, and is
      recorded in a prune ledger so the backup is traceable to the prune.
      Legacy non-`plain-*` rows are intentionally left alone.

  restore-active [BACKUP] [--apply] [--allow-superseded]
                 [--state-dir DIR] [--locks-dir DIR]
      The ONLY sanctioned way to roll `_active.json` back — never a shell copy,
      which would clobber every binding minted since the backup. Defaults to the
      most recent prune's backup. Performs a LOGICAL MERGE (re-add the pruned
      rows, keep every row that exists now; on a collision the CURRENT value
      wins), because the inverse of a deletion-only operation is addition-only.
      A backup older than the last prune is REFUSED unless --allow-superseded.

`--locks-dir DIR` pairs with `--state-dir` to redirect the LOCK namespace too.
Without it a "copy fixture" run still acquires and releases REAL topic locks
in the live `~/.claude/state/locks` under the fixture's slugs — transient, but
not the isolation a copy-fixture smoke is supposed to have.

Safe-defaults throughout: DRY-RUN default, `--apply` required to mutate,
mv-to-timestamped-backup never rm, reap/reconcile locks before touching state.
The reconcile LOGIC lives in `pre_plan_gates.py` (the single canonical locus);
this file is the CLI adapter.

Legacy form (backward-compatible): `reap_orphan_plain_topics.py [STATE_DIR]
[--apply]` with no verb == `reap-orphans`.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

DEFAULT_STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude", "state", "pre_plan_gates")

_VERBS = ("find-mis-keyed", "reconcile", "reconcile-inverted",
          "prune-active", "restore-active", "reap-orphans")

_RESOLUTIONS = ("keep-inverted", "keep-canonical")


# ---------------------------------------------------------------------------
# reap-orphans (legacy behaviour, unchanged logic)
# ---------------------------------------------------------------------------

def find_orphans(state_dir: str) -> list[str]:
    out = []
    for path in sorted(glob.glob(os.path.join(state_dir, "*__*.json"))):
        name = os.path.basename(path)
        if name == "_active.json" or "plain-" not in name:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        tfp = data.get("thought_file_path")
        if tfp and os.path.exists(tfp):
            continue  # genuinely backed by a spine -> not an orphan
        out.append(path)
    return out


def reap(state_dir: str, apply: bool) -> list[str]:
    orphans = find_orphans(state_dir)
    if apply and orphans:
        reaped_dir = os.path.join(state_dir, "_reaped")
        os.makedirs(reaped_dir, exist_ok=True)
        for path in orphans:
            shutil.move(path, os.path.join(reaped_dir, os.path.basename(path)))
    return orphans


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _import_ppg():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import pre_plan_gates as ppg  # noqa: E402
    return ppg


def _apply_state_dir_override(ppg, argv: list[str]) -> None:
    """When --state-dir DIR is present, point the reconcile logic at DIR (used
    by the S6 e2e smoke against a COPY of the live twin fixture).

    `--locks-dir DIR` additionally redirects the LOCK namespace. Without it a
    "copy fixture" run still acquires and releases REAL topic locks in the live
    `~/.claude/state/locks` under the fixture's slugs — transient, since they are
    released in a `finally`, but it means the fixture smoke is not actually
    isolated from live state, which is what the plan's COPY-fixture discipline
    asks for. `--state-dir` alone could not deliver that discipline; this makes
    it achievable rather than aspirational."""
    from pathlib import Path
    if "--state-dir" in argv:
        i = argv.index("--state-dir")
        if i + 1 < len(argv):
            ppg.TOPIC_STATE_DIR = Path(argv[i + 1])
    if "--locks-dir" in argv:
        j = argv.index("--locks-dir")
        if j + 1 < len(argv):
            import taskmanagement as _tm
            locks = Path(argv[j + 1])
            locks.mkdir(parents=True, exist_ok=True)
            _tm.LOCKS_DIR = locks
            _tm.RELEASES_LOG = locks / "_releases.jsonl"


def _cmd_reap_orphans(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    apply = "--apply" in argv
    state_dir = args[0] if args else DEFAULT_STATE_DIR
    orphans = reap(state_dir, apply)
    verb = "QUARANTINED" if apply else "DRY-RUN (pass --apply to quarantine to _reaped/)"
    print(f"{verb}: {len(orphans)} orphan plain-* topic-state file(s)")
    for p in orphans:
        print(f"  {os.path.basename(p)}")
    return 0


def _cmd_find_mis_keyed(argv: list[str]) -> int:
    ppg = _import_ppg()
    _apply_state_dir_override(ppg, argv)
    rows = ppg.find_mis_keyed()
    print(f"RECONCILABLE (correct-convention, divergent project): {len(rows)} slug(s)")
    for r in rows:
        print(f"  {r['slug']}: twins={r['twins']} "
              f"canonical={r['canonical_project']} ({r['reason']})")
    inverted = ppg.find_inverted()
    print(f"\nINVERTED / swapped-key era (NOT auto-reconciled — operator review): "
          f"{len(inverted)} record(s)")
    for r in inverted:
        print(f"  {r['file']}  ->  should be {r['canonical']}.json")
    unres = ppg.find_unresolvable()
    if unres:
        print(f"\nUNRESOLVABLE (spine not on disk — surfaced only): {len(unres)} record(s)")
        for r in unres:
            print(f"  {r['file']}")

    # S5/A6 — the FOURTH report section. REPORT ONLY: no repair verb, no
    # mutation, no `--apply` path.
    #
    # It deliberately EXTENDS this existing verb rather than adding a new one.
    # `main`'s dispatch (see the bottom of this file) FALLS THROUGH to the
    # orphan reaper for any verb in `_VERBS` that lacks its own explicit `if` —
    # so a new verb added here without one would SILENTLY REAP live records.
    # Extending `find-mis-keyed` sidesteps that hazard entirely instead of
    # relying on remembering to guard it.
    #
    # The fallthrough call is deliberately NOT spelled out here: the plan's
    # Anchor snapshot cites that exact token, and a second occurrence of it in
    # this comment gives the citation scanner a rival binding inside the wrong
    # enclosing function. Naming it in prose keeps the anchor unambiguous.
    mismatches, no_identity = ppg.find_body_mismatch()
    fingerprint = sum(1 for r in mismatches if r["cwd_fingerprint"])
    print(f"\nBODY-MISMATCH: {len(mismatches)} record(s) — the stored identity "
          f"halves disagree with the filename key")
    print(f"  cwd-fingerprint: {fingerprint}  (body.topic_slug == "
          f"basename(body.project_root))")
    print("  The subtotal is a FLOOR of what is attributable to the cwd-derived")
    print("  generator, computed over THESE mismatches only — not globally. It")
    print("  under-counts (a session run from a subdirectory, or one in a")
    print("  worktree where project_root is null, produces the same defect")
    print("  without matching); the remainder is not claimed to be unrelated.")
    for r in mismatches:
        mark = "  [cwd]" if r["cwd_fingerprint"] else ""
        print(f"  {r['file']}  body={r['body_topic']}__{r['body_project']}{mark}")
    if no_identity:
        print(f"\n  ...plus {len(no_identity)} record(s) carrying NEITHER identity "
              f"half — counted in neither bucket (not a mismatch, not an "
              f"agreement):")
        for r in no_identity:
            print(f"    {r['file']}")
    return 0


def _cmd_reconcile(argv: list[str]) -> int:
    ppg = _import_ppg()
    _apply_state_dir_override(ppg, argv)
    apply = "--apply" in argv
    pos = [a for a in argv if not a.startswith("--")]
    # strip a --state-dir value that landed in pos (it followed the flag)
    if "--state-dir" in argv:
        i = argv.index("--state-dir")
        if i + 1 < len(argv):
            pos = [a for a in pos if a != argv[i + 1]]
    target = pos[0] if pos else "all"
    if target == "all":
        slugs = [r["slug"] for r in ppg.find_mis_keyed()]
    else:
        slugs = [target]
    head = "APPLY" if apply else "DRY-RUN (pass --apply to reconcile)"
    print(f"reconcile {head}: {len(slugs)} slug(s)")
    for slug in slugs:
        res = ppg.reconcile_topic_identity(
            slug, apply=apply, respect_locks=True, acquire_locks=apply)
        print(f"  {slug}: canonical={res['canonical_project']} "
              f"reason={res['reason']}")
        for a in res["actions"]:
            print(f"      {a}")
        if res["skipped_locked"]:
            print(f"      skipped (locked, deferred): {res['skipped_locked']}")
        if res["merge_review"]:
            print(f"      MERGE_REVIEW (orphan richer, review backup): {res['merge_review']}")
    return 0


def _flag_value(argv: list[str], flag: str):
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return None


def _cmd_reconcile_inverted(argv: list[str]) -> int:
    """One-shot supervised backfill of the inverted/swapped-key era.

    reconcile-inverted [--apply] [--state-dir DIR]
    reconcile-inverted --slug SLUG --resolve keep-inverted|keep-canonical [--apply]
    reconcile-inverted --file INVERTED.json --resolve ... [--apply]

    DRY-RUN unless --apply. Non-interactive throughout: the ambiguous tie-break
    is the --resolve FLAG, never a prompt, so this never waits on a TTY."""
    ppg = _import_ppg()
    _apply_state_dir_override(ppg, argv)
    apply = "--apply" in argv
    slug = _flag_value(argv, "--slug")
    inverted_file = _flag_value(argv, "--file")
    resolve = _flag_value(argv, "--resolve")

    if resolve is not None and resolve not in _RESOLUTIONS:
        print(f"ERROR: --resolve must be one of {list(_RESOLUTIONS)}", file=sys.stderr)
        return 2

    res = ppg.reconcile_inverted(apply=apply, slug=slug,
                                 inverted_file=inverted_file, resolve=resolve)

    head = "APPLY" if apply else "DRY-RUN (pass --apply to migrate)"
    scope = "resolution" if (slug or inverted_file) else "bulk"
    print(f"reconcile-inverted {head} [{scope}]")
    c = res["counts"]
    print(f"  renamed={c['rename']} quarantined={c['redundant']} "
          f"merge-review={c['ambiguous']} resolved={c['resolved']} "
          f"already-gone={c['gone']}")
    for a in res["actions"]:
        print(f"      {a}")
    if res["merge_review"]:
        print(f"\n  MERGE_REVIEW — {len(res['merge_review'])} ambiguous record(s) "
              f"skipped in place (canonical exists AND is less rich).")
        print("  Resolve each explicitly, e.g.:")
        for f in res["merge_review"]:
            print(f"      reconcile-inverted --file {f} "
                  f"--resolve keep-inverted|keep-canonical --apply")
    if res["skipped_locked"]:
        print(f"\n  skipped (locked, deferred): {res['skipped_locked']}")
    if res["errors"]:
        for e in res["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def _parse_window(raw):
    """Parse a `--prune-window` value: `30d`, `12h`, or bare SECONDS.

    Suffix-aware because the default is naturally spoken in days while the
    underlying primitive is seconds; a bare number would be ambiguous between
    the two and is therefore read as seconds, the unit the code uses."""
    s = str(raw).strip().lower()
    mult = 1
    if s.endswith("d"):
        mult, s = 86400, s[:-1]
    elif s.endswith("h"):
        mult, s = 3600, s[:-1]
    elif s.endswith("s"):
        s = s[:-1]
    return int(float(s) * mult)


def _print_unconfirmed_prunes(ppg) -> None:
    """Surface any UNCONFIRMED prune rows.

    This is what makes the two-phase ledger's stated advantage real rather than
    asserted. An unconfirmed row means a prune wrote its record but its outcome
    was never confirmed — either it failed cleanly (nothing pruned) or it was
    killed between the mutation and the confirming append (rows ARE gone). The
    two cases are indistinguishable from the ledger alone, and neither is
    self-announcing, so without a surface here the operator would simply never
    learn about it. An independent verifier pointed out that the justification
    comment claimed this surfacing existed when nothing implemented it."""
    try:
        rows = ppg._read_prune_active_ledger()
    except Exception:                     # noqa: BLE001 — advisory, never fatal
        return
    pending = [r for r in rows if not r.get("committed", True)]
    if not pending:
        return
    print(f"\n  ⚠ {len(pending)} UNCONFIRMED prune record(s) — a prune wrote its "
          f"ledger row but never confirmed the outcome:")
    for r in pending:
        print(f"      {r.get('backup')}  (at {r.get('at')}, "
              f"{r.get('count')} row(s) intended)")
    print(f"  Either it failed before mutating (nothing was pruned) or it was "
          f"killed after mutating. Compare the backup against the live ledger "
          f"before acting; these rows are ignored by restore-active's default "
          f"resolution, so recovering one needs its name plus "
          f"--allow-superseded.")


def _cmd_prune_active(argv: list[str]) -> int:
    """Prune dead `plain-*` rows from the shared `_active.json` ledger.

    prune-active [--apply] [--prune-window 30d|12h|SECONDS]
                 [--state-dir DIR] [--locks-dir DIR]

    DRY-RUN unless --apply. Liveness is proved by ACQUIRING each candidate's
    topic lock, never by a read-only probe that fails open; the rewrite runs
    under the one `_active.json` lock after a VERIFIED backup copy."""
    ppg = _import_ppg()
    _apply_state_dir_override(ppg, argv)
    apply = "--apply" in argv
    raw_window = _flag_value(argv, "--prune-window")
    window = None
    if raw_window is not None:
        try:
            window = _parse_window(raw_window)
        except ValueError:
            print(f"ERROR: --prune-window must be a number, optionally suffixed "
                  f"d/h/s (got {raw_window!r})", file=sys.stderr)
            return 2

    res = ppg.prune_active(apply=apply, window_seconds=window)

    head = "APPLY" if apply else "DRY-RUN (pass --apply to prune)"
    days = res["window_seconds"] / 86400.0
    print(f"prune-active {head}  [window={res['window_seconds']}s ≈ {days:.1f}d]")
    print(f"  reason={res['reason']}  entries {res['total_before']} -> {res['total_after']}")
    moved = res.get("skipped_key_moved") or []
    print(f"  candidates={len(res['candidates'])} pruned={len(res['pruned'])} "
          f"kept-live={len(res['skipped_locked'])} kept-key-moved={len(moved)}")
    if res["backup"]:
        print(f"  backup (verified copy): _reaped/{res['backup']}")
    for s in res["pruned"]:
        print(f"      pruned  {s}")
    if not apply:
        for s in res["candidates"]:
            print(f"      would prune  {s}")
    if res["skipped_locked"]:
        print(f"\n  kept — a live session holds the topic lock: "
              f"{len(res['skipped_locked'])}")
        for s in res["skipped_locked"]:
            print(f"      {s}")
    if moved:
        # Reported separately from the lock-held bucket on purpose: the code
        # observed a CHANGED active_project, not a live holder. Folding these
        # into "kept-live" would assert an observation the code never made.
        print(f"\n  kept — the row's topic moved after its lock was taken, so "
              f"the lock held no longer covered it: {len(moved)}")
        for s in moved:
            print(f"      {s}")
    # Gated on a prune having actually happened, NOT merely on a backup existing.
    # On the `write-failed` path a verified backup IS taken before the mutation
    # is attempted, so `backup` is set — but nothing was pruned, so there is
    # nothing to roll back, and `restore-active` would not default to that
    # backup anyway (its ledger row is unconfirmed and therefore filtered out,
    # so the default resolves to the PREVIOUS prune's backup and would re-add
    # rows that earlier prune removed). Printing the hint here would have the
    # operator perform an unintended mutation by following the tool's own
    # instruction.
    # Gated on CONFIRMED, not on `reason == "pruned"`. They differ on exactly one
    # path: a prune whose confirming ledger append failed really did prune (so
    # `reason` is "pruned") but its row is still uncommitted, so
    # `restore-active` would NOT default to its backup — the default would
    # resolve to the PREVIOUS committed prune and undo that one instead. Gating
    # on `reason` here printed a true instruction with a false parenthetical,
    # which is the same operator-mutates-unintended-state harm as the
    # write-failed case, reached through a different door.
    if res["backup"] and res.get("confirmed"):
        print(f"\n  Roll this prune back with:")
        print(f"      reap_orphan_plain_topics.py restore-active --apply")
        print(f"  (that defaults to this backup — the most recent completed prune's)")
    elif res["backup"] and res["reason"] == "pruned":
        print(f"\n  This prune COMPLETED but its ledger row was never confirmed, "
              f"so restore-active will NOT default to it.")
        print(f"  Roll it back by naming it explicitly:")
        print(f"      reap_orphan_plain_topics.py restore-active "
              f"{res['backup']} --allow-superseded --apply")
    elif res["backup"]:
        # Two reasons reach here and they differ: `write-failed` left an
        # unconfirmed row, while `ledger-write-failed` wrote no row at all.
        # Naming the wrong one would be a small falsehood in the same class as
        # the ones this slice has been removing; the ADVICE is identical either
        # way and errs toward inaction, so only the reason needs to be right.
        why = ("its ledger row is unconfirmed"
               if res["reason"] == "write-failed"
               else "no ledger row was written for it")
        print(f"\n  A verified backup was taken (_reaped/{res['backup']}) but "
              f"NOTHING was pruned, so there is nothing to roll back.")
        print(f"  Do NOT run restore-active for this: {why}, so the default "
              f"would resolve to an EARLIER prune's backup and undo that one "
              f"instead.")

    _print_unconfirmed_prunes(ppg)
    if res["errors"]:
        for e in res["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def _cmd_restore_active(argv: list[str]) -> int:
    """Roll `_active.json` back from a prune backup, by LOGICAL MERGE.

    restore-active [BACKUP] [--apply] [--allow-superseded]
                   [--state-dir DIR] [--locks-dir DIR]

    With no BACKUP, defaults to the most recent recorded prune's backup. A
    SUPERSEDED backup (one older than the last prune) is refused unless
    --allow-superseded, because restoring it would re-add rows that later
    prunes deliberately removed. DRY-RUN unless --apply."""
    ppg = _import_ppg()
    _apply_state_dir_override(ppg, argv)
    apply = "--apply" in argv
    allow_superseded = "--allow-superseded" in argv

    pos = [a for a in argv if not a.startswith("--")]
    for flag in ("--state-dir", "--locks-dir"):
        v = _flag_value(argv, flag)
        if v is not None:
            pos = [a for a in pos if a != v]
    backup = pos[0] if pos else None

    res = ppg.restore_active(backup, apply=apply,
                             allow_superseded=allow_superseded)

    head = "APPLY" if apply else "DRY-RUN (pass --apply to restore)"
    print(f"restore-active {head}")
    # Surfaced BEFORE the refusal branch, deliberately. The refusal is where an
    # operator most needs this: being told "no completed prune has been
    # recorded" while an UNCONFIRMED row sits in the ledger is exactly the
    # moment the unconfirmed row explains the refusal. Printing it only on the
    # success path would surface it precisely when it does not matter.
    _print_unconfirmed_prunes(ppg)

    # Both refusal reasons take the REFUSED branch. `superseded-during-restore`
    # is decided under the lock and carries no merge figures, so falling through
    # to the summary below would print a misleading `mode=None entries 0 -> 0`
    # before the error.
    if res["errors"] and res["reason"] in ("invalid-backup",
                                           "superseded-during-restore"):
        for e in res["errors"]:
            print(f"  REFUSED: {e}", file=sys.stderr)
        return 2
    print(f"  backup={res['backup']}  mode={res['mode']}  reason={res['reason']}")
    print(f"  entries {res['total_before']} -> {res['total_after']}  "
          f"re-added={len(res['restored'])} left-as-is={len(res['already_present'])}")
    if res["quarantined"]:
        print(f"  corrupt incumbent quarantined (verified copy): "
              f"_reaped/{res['quarantined']}")
    for s in res["restored"]:
        print(f"      {'re-added' if apply else 'would re-add'}  {s}")
    if res["already_present"]:
        print(f"  kept at their CURRENT value (present in both): "
              f"{len(res['already_present'])}")
    if res["errors"]:
        for e in res["errors"]:
            print(f"  ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str]) -> int:
    if argv and argv[0] in _VERBS:
        verb, rest = argv[0], argv[1:]
        if verb == "find-mis-keyed":
            return _cmd_find_mis_keyed(rest)
        if verb == "reconcile":
            return _cmd_reconcile(rest)
        if verb == "reconcile-inverted":
            return _cmd_reconcile_inverted(rest)
        if verb == "prune-active":
            return _cmd_prune_active(rest)
        if verb == "restore-active":
            return _cmd_restore_active(rest)
        return _cmd_reap_orphans(rest)
    # legacy positional form: [STATE_DIR] [--apply] == reap-orphans
    return _cmd_reap_orphans(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

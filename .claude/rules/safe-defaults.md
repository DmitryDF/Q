# Safe Defaults — Destructive-Operation Policy

**Status:** active. Authored 2026-07-20 (S4/A6 of `Thoughts/readonly-skills-structural-safety-20260720175417_PLAN.md`).
**Enforcement layer:** Rules (Layer 2 — auto-loaded every session; the AI reads and applies judgment). This is the *judgment* layer that sits ATOP the code enforcement, not a substitute for it. See the trust hierarchy in `~/.claude/rules/code_first_architecture.md` (Code > Rules > Skill text).

---

## Rule — propose the safe operation by default

When an operation would **irreversibly destroy** local state (delete a file, discard
uncommitted work, rewrite pushed history), do **not** reach for the destructive form.
Propose the **absolute safe default** below instead. If a genuinely destructive action
is truly required, surface it to the user with the trade-off and get explicit approval
first — never as a silent shortcut.

This is a Layer-2 judgment property (there is no automated test that the AI "proposes
safe operations by default"); it is verified by this file loading each session plus a
**registered periodic calibration** (see *Calibration owner* below).

## Absolute safe defaults

| Instead of (destructive) | Use (safe default) | Why |
|---|---|---|
| `rm -rf <path>` | `mv "<path>" "<path>.bak-$(date +%Y%m%d%H%M%S)"` | A timestamped backup is reversible; a delete is not. |
| `rm <file>` | `mv "<file>" "<file>.bak-<ts>"` | Same — move aside, don't destroy. |
| `git push --force` / `--force-with-lease` | reconcile — `git pull` / merge, don't rewrite | Force-push rewrites shared/pushed history (git-policy.md §4 already bans force-pushing the default branch). |
| `git branch -D <b>` | `git branch -d <b>` only | `-D` force-deletes unmerged commits; `-d` refuses unless merged. If `-d` refuses because the commits are in `main` but not the tracking ref, **reconcile** (fetch / set upstream) — do not force. Aligns git-policy.md §9. |
| `git reset --hard` | `git reset --soft` (or `--mixed`) | `--hard` discards working-tree + index changes irreversibly; `--soft` keeps them. |
| `git stash drop` | `git stash pop --index` | `drop` throws a stash away; `pop --index` restores it (and un-stashes). |
| `git clean -fd` | back up or reconcile first | Silently deletes untracked files (the working-tree blindspot the A5 path guard cannot see). |
| `git checkout -- .` / `git restore .` | back up or reconcile first | Silently discards ALL uncommitted working-tree changes. Restore specific files only, after confirming. |
| `--no-verify` on commit/push | run the hooks | Bypassing hooks defeats the very gates that protect the repo. |
| `chmod 777 <path>` | the minimal mode the task needs (e.g. `chmod 755`/`644`) | `777` is world-writable — never the right answer. |
| interactive destructive prompts auto-answered `y` | pause and confirm | Don't pre-answer a destructive confirmation. |

## Backup hygiene (retention)

The `mv`-to-timestamped-backup default is safe but accumulates `.bak-<ts>` files. To keep
the safe default from itself exhausting disk, stale backups are pruned periodically. This
retention/cleanup is owned by the registered calibration below (an editorial cadence, not
a code gate): during the periodic review, prune `*.bak-*` backups older than the retention
window (default: 30 days) from working trees and `~/.claude`.

## Relationship to the code layers (this is the weakest layer — by design)

- **A1 — the real control (Layer 1, un-bypassable).** Read-only verification helpers hold
  no shell at all (the `readonly-checker` named agent, `tools: Read, Grep, Glob`). A helper
  with no Bash *cannot construct* a destructive command. This policy does not protect them —
  they are already structurally safe.
- **A5 — the best-effort backstop (Layer 1, code).** For surfaces that legitimately hold a
  shell (the main session, producer subagents), `guard-config-paths.sh` blocks the *common*
  destructive shapes whose target resolves inside a safety-critical `~/.claude` config path
  (`hooks/agents/rules/skills/settings.json` — not `logs/cache/state/projects`) and prints
  the `mv`-to-backup alternative. It is best-effort and surface-level: it does **not** cover
  git-history (that is git-policy.md §4 + the pre-push hook), non-Bash payloads, or Turing-
  complete evasion.
- **This policy (Layer 2, judgment).** Everything the code cannot catch — working-tree git
  discards, `--no-verify`, `chmod 777`, destructive ops outside the config paths — is caught
  only by the AI applying this policy. That is why it is documented where the AI reads it.

## Calibration owner

The periodic calibration of this policy (does the AI in fact propose safe defaults? are new
destructive shapes emerging that need a safe alternative? prune stale `.bak-*` backups) is
registered in `Projects/.claude/rules/periodic-reviews.md` under the Monthly review. That is
the standing owner; without it this Layer-2 property has no drift check.

---

*Provenance: S4/A6 of `Thoughts/readonly-skills-structural-safety-20260720175417_PLAN.md`. Reconciles `factcheck-convergence.md §3` (Explore-grants-Bash caution) and `git-policy.md §9` (`branch -d`-only) to this absolute ban.*

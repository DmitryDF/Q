---
name: session-start
description: Start session - git check, read diary, check TODOs, check reminders
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
disable-model-invocation: true
---

# Session Start

## Pre-Check

**After context compaction:** Re-read CLAUDE.md inheritance chain before continuing.

## Git Check

`git fetch && git status` — if behind, suggest pull; if uncommitted changes, warn; never force-pull or auto-resolve.

## Core Checklist (adapt scope to location)

1. Read CLAUDE.md inheritance chain
2. Read `USER_MANUAL.md`
3. Read last `Diary/` entry
4. TODO summary is auto-injected by the `todo.py` hook on the first prompt. Reference it; do not re-read TODO.md unless user requests detail. See `~/.claude/rules/todo-management.md`.
5. Show pending (not completed) `[CC]` reminders; if none, show "[CC] Reminders: -"
7. Scan for recent research: `Thoughts/*_RESEARCH.md` and `*/Thoughts/*_RESEARCH.md` modified in last 14 days — list if any found
8. Report status, ask what to work on

## Reminders Integration

- All Claude-created reminders use `[CC]` prefix
- When TODO is marked done: check if corresponding `[CC]` reminder exists → delete it
- When reminder is no longer needed: delete it directly
- When creating reminders: always add `[CC]` prefix
- Before setting reminder due date: check system date format via `osascript -e 'return (current date) as string'` and use that format

## TODO Display

The `todo.py` hook injects the TODO scan (URGENT / OVERDUE / per-project counts) on the first prompt of every session. Use that output to orient. Friday → run weekly review (still reads all sections).

## Weekly Review Catch-Up

- Check: Was weekly review done in last 7 days? (Look for diary entry with "Weekly review completed: YYYY-MM-DD")
- If no recent review AND 7+ days since last:
  1. Announce: "Weekly review is due (last done: [date] or never)"
  2. Ask: "Run now or defer?"
  3. If now: run full review (all 5N states, Thoughts/, etc.)
  4. If defer: note in diary, will prompt again next session

## Friday Review

Done this week → Not done (reschedule?) → Plan next 2 weeks → Show all states → Review Thoughts/ files > 30 days (promote or archive?) → Prompt analysis (see below).

## Prompt Analysis (weekly)

- Read all `_processed/` prompt logs from the week across `~/.claude/logs/*/`
- Auto-cluster by topic, assess each: clear or could be improved
- For weak prompts: suggest tighter version
- Add consolidated Prompts section to Friday diary entry
- If patterns found: note in diary. No separate TODO needed — reviewed inline.

## Bi-Weekly (every second Friday)

- **System audit:** Run `/system-audit` — see `Skills/system-audit.md`
- Next: 2026-02-13 (then Feb 27, Mar 13, ...)

## Cross-Referencing

See `CROSS_REF_RULES.md`

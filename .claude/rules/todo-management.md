# TODO Management

All mechanical TODO operations run through `${KIT_HOOKS_DIR}/todo.py`. This
rules file contains only conventions and judgment-layer guidance. Code handles
parsing, date math, section moves, and file writes.

## Date Convention

Time-sensitive dates in TODO items use ISO format (YYYY-MM-DD), inside either
a bold span (`**PNG.V 2026-04-16**`) or parens (`(2026-04-03)`). Only dates
in these positions are treated as due dates. Dates preceded by context words
(`surfaced 2026-04-10`, `since 2026-02-23`, `diary 2026-03-24`) are ignored
as references.

The read hook warns if prose dates are detected — normalize to ISO.

## Read: what's next

The hook auto-injects a TODO scan on the first prompt of every conversation.
Reference that output to orient. Do not re-parse raw TODO.md for priority.

If the scan is absent (hook disabled or running outside a session):
```
python3 ${KIT_HOOKS_DIR}/todo.py read --cwd "PROJECT_PATH"
```

## Add a new item

1. Scan for dependencies:
   ```
   python3 ${KIT_HOOKS_DIR}/todo.py deps "item text"
   ```
   Returns JSON with keyword matches in existing TODOs + existing subsection
   names (candidate groups).

2. Apply grouping judgment (see Grouping Triggers below). If a group fits,
   include it with `--group "Name"`.

3. Write the item:
   ```
   python3 ${KIT_HOOKS_DIR}/todo.py add "item text" \
       --bucket NOW|NEXT|NEARBY|NASCENT|SCHEDULED \
       [--group "Subsection Name"] \
       [--file PATH | --project PATH]
   ```

**`[Thought]`-tagged items at gate boundaries:** always pass `--validate-framing`:
```
python3 ${KIT_HOOKS_DIR}/todo.py add "[Thought] **Title** — Problem: … Context: … \
    Guiding policy: … Master plan: [[…]]." \
    --bucket BUCKET --validate-framing [--file PATH | --project PATH]
```
The validator enforces the 4-component labeled structure and exits 1 with per-component
errors if framing fails. Use `--phase planning` to suppress write during Planning phase.

Bucket choice:
- Item has an ISO date ≤ 7 days → NOW
- ISO date 8–30 days → NEXT
- ISO date > 30 days → NEARBY
- No date, actionable now → NOW
- No date, needs thought → NEARBY
- Idea / someday → NASCENT

If no date and the bucket is ambiguous, ask the user per 5N just-in-time
(see `.claude/rules/topic-workflows.md`).

## Mark an item done

Simple completion:
```
python3 ${KIT_HOOKS_DIR}/todo.py done --pattern "regex" --file PATH
```
Script replaces the line with `[x]` + today's DONE stamp.

Items needing a completion summary (complex work shipped with context worth
recording inline):
```
python3 ${KIT_HOOKS_DIR}/todo.py done --pattern "regex" --file PATH \
    --text "- [x] **Thing** — **DONE 2026-04-16.** Shipped X, Y, Z. Plan: \`plan.md\`."
```
The `--text` flag must start with `- [x]`. Cleanup preserves the summary.

If the pattern matches multiple items, the script returns candidates as JSON
for disambiguation — refine the pattern and call again.

## Cleanup (runs during /close)

```
python3 ${KIT_HOOKS_DIR}/todo.py cleanup --project PATH
```

Moves `[x]` items from active sections (Now, Next, Nearby, Nascent, Scheduled,
Blocked) into the Done section under `### YYYY-MM-DD` headers. Empty sections
get an italic placeholder (preserves any existing placeholder).

Idempotent: re-running produces no changes when nothing needs moving.

## Tagging Convention

Items can have an optional `[Topic]` tag at the start of the item text for
categorization. Tags are lightweight — no structural nesting.

Format: `- [ ] [Knowledge Library] **Item title** — description`

Use `--tag "Topic"` with `todo.py add` to prepend the tag automatically.

**Active buckets (Now, Next, Scheduled, Nearby, Nascent) must be flat.**
No `### Subsection` headers. The `todo.py read` hook warns on violations.
Done section keeps `### YYYY-MM-DD` date headers (different purpose).

## `[Thought]` Multi-Session Tag

For work tracked in a Thoughts file that spans multiple sessions, use the
`[Thought]` tag plus a `Sessions: M/N done.` counter on a single line:

```
- [ ] [Thought] **Topic title** — see Thoughts/<topic>_THOUGHT.md. <one-sentence outcome>. Sessions: M/N done.
```

`M` = sessions shipped so far. `N` = total planned sessions (set when entry is
created; matches the Sessions Plan in the Thoughts file).

Advance the counter at each `/close` with:

```
python3 ${KIT_HOOKS_DIR}/todo.py advance-sessions --pattern "REGEX" --file PATH \
    [--summary "what shipped this session"]
```

When `M` reaches `N`, the helper auto-converts the item to `[x]` with today's
DONE stamp + summary; the next `cleanup` pass sweeps it into Done. The close
skill calls this automatically when the active SESSION_ID has a topic with
`thought_file_path` set (see `~/.claude/skills/close/SKILL.md` step 1b.2).

## Diary Links in Done

Every Done item must end with a diary wikilink: `[[YYYY-MM-DD]]`.
The `cleanup` command appends this automatically when moving items.
`todo.py read` warns on Done items missing links.

For Obsidian navigation to a specific section, use the full format:
`[[Diary/YYYY-MM-DD#Section Title|YYYY-MM-DD]]`

## Scope

Applies to any project with a `TODO.md`. If no TODO.md exists, skip.

---
name: q-update
description: Update an installed Q framework to a newer release from a local clone of the Q repo — shows what changed, applies only the delta, and never overwrites the files that belong to you. Use when the operator asks to update Q, pull a newer Q release, or check whether their Q install is current. Not for the first install; that is INSTALL.md.
---

# /q-update — bring an installed Q up to a newer release

You are updating a framework that is already installed. That is a different job from
installing it, and the difference matters: the operator has been using this, so
their `CLAUDE.md`, their `settings.json`, and anything they have edited are theirs.

**If Q is not installed yet, stop and read `INSTALL.md` in the clone instead.** This
skill assumes `<config>/.q-release` exists.

## Prohibitions — not preferences

- **Never overwrite `CLAUDE.md`.** Nothing in an update touches it. If a new release
  needed a new always-on import, say so and let the operator add it.
- **Never replace `settings.json`.** New hook registrations are *merged*, after a
  timestamped backup, and only with the operator's yes.
- **Never delete a file because it left the release.** Report it as "no longer
  shipped" and leave it. A file the operator edited or depends on is not yours to
  remove.
- **Never update a file the operator has modified** without showing them the
  conflict first.
- **Do not run the test suite.** It asserts whole-tree state and is slow; the checks
  in step 5 are what matter.

## Step 1 — Establish both versions

```bash
cat "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.q-release"   # what is installed
git -C <clone> describe --tags --abbrev=0              # what the clone offers
```

If they match, say so and stop — there is nothing to do. Do not "refresh anyway";
a no-op that rewrites files is how local edits get lost.

## Step 2 — Show what changed, before changing anything

```bash
git -C <clone> log --oneline <installed-tag>..<clone-tag>
git -C <clone> diff --stat <installed-tag>..<clone-tag>
```

Summarise for the operator in plain terms: which skills are new, which files change,
whether any hook registrations were added. Then ask whether to proceed.

## Step 3 — Find local modifications first

For every file the update would touch, compare the installed copy against the
version recorded at `.q-release`:

```bash
git -C <clone> show <installed-tag>:<path> | diff - "<config>/<path>"
```

Any file that differs has been modified locally. **List those separately and ask per
file**: keep the local version, take the new one, or show the diff. Overwriting a
local edit silently is the single worst thing an update can do.

## Step 4 — Apply the delta

Copy only the changed files, only for paths under `.claude/` in the clone. Nothing
else in the config directory is yours to touch.

New hook registrations: show the operator the added entries from
`settings.example.json`, back up their `settings.json` with a timestamp, and merge
per event key on their yes. Stop on any conflict.

## Step 5 — Verify and record

```bash
bash -n "<config>"/hooks/*.sh
python3 -c "import sys; sys.path.insert(0, '<config>/hooks'); import _factcheck_engine"
git -C <clone> describe --tags --abbrev=0 > "<config>/.q-release"
```

Write the new marker **last** — only after the copy and the checks succeeded. A
marker claiming a release that did not fully land makes the next update skip work it
should have done.

## Step 6 — Report

- versions, from and to
- what was copied
- what was skipped because the operator had modified it
- whether `settings.json` was merged, and where the backup is
- anything no longer shipped and therefore left in place
- anything the operator must do by hand (a new always-on import, a new env seam)

Do not report success for a step you skipped.

## Why this is a skill and INSTALL.md is not

A skill has to be in `<config>/skills/` to be invocable, so the first install cannot
use one — it is a plain file the agent reads. Once Q is installed, `/q-update` is
available and can do the thing a re-install cannot: reconcile, rather than copy over
the top. **Do not re-run `setup.sh` to update.** It installs; it does not compare.

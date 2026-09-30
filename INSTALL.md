# Installing Q

**This file is written for an AI agent to execute.** Clone the repo, then tell your
agent: *"read INSTALL.md and install this."*

**If you are the human: read this first.** It tells your agent what to do, so you
should be able to see exactly what that is. Here is the complete list of what an
install writes, and nothing else:

| Path | What happens |
|---|---|
| `~/.claude/skills/`, `rules/`, `hooks/`, `agents/` | new files copied in |
| `~/.claude/.q-release` | a one-line marker recording which release is installed |
| `~/.claude/CLAUDE.md` | **three lines appended — only if you say yes** |
| `~/.claude/settings.json` | **hook entries merged — only if you say yes; backed up first** |

Nothing is deleted. Nothing is overwritten without being backed up. If your agent
proposes anything outside that table, it has gone off-script — stop it.

**This release ships the complete skill set.**

---

## Agent: read this whole file before running anything

You are installing a skill framework into the user's `~/.claude/`. Two steps below
require the user's explicit answer. Do not infer either one, and do not proceed
past them on silence.

### Prohibitions — these are not preferences

- **Never overwrite `~/.claude/CLAUDE.md`.** Append to it, only with a yes, only the
  three lines in step 4.
- **Never replace `~/.claude/settings.json`.** Merge per event key, after backing it
  up. If a merge would drop an existing hook, stop and show the user the conflict.
- **Never delete anything under `~/.claude/`.** No `rm`, no `rm -rf`, no "clean up
  the old version". If something looks stale, say so and leave it.
- **Never invent a value for the two questions.** Silence is not consent.
- **Do not run the test suite as part of installing.** It is tree-coupled and slow;
  step 8 has the checks that matter.

---

## Step 1 — Check prerequisites, report, stop on a failure

```bash
python3 --version                 # need 3.11+
python3 -c "import yaml; print('pyyaml ok')"
git --version
```

`pyyaml` is a hard requirement — `dc_stats.py` raises without it. `pydantic` and
`tiktoken` are optional; the engine guards both. If `pyyaml` is missing, tell the
user the one command to fix it and stop.

## Step 2 — Decide the install root

```bash
echo "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
```

Everything below writes under that path. If the user wants to trial Q without
touching their real config, they set `CLAUDE_CONFIG_DIR` to a temp directory first —
offer that if they seem unsure.

## Step 3 — Copy the files

```bash
./setup.sh --global
```

This copies `skills/`, `rules/`, `hooks/`, `agents/` into the install root and
rewrites the hook-path placeholders. It does **not** touch `CLAUDE.md` or
`settings.json` — those are steps 4 and 5.

Report how many files landed.

## Step 4 — ASK: the three always-on rules

`~/.claude/rules/` is **not** an auto-load location — nothing reads it on its own.
Three rules must be imported explicitly or the skills lose their grounding.

**Ask the user, in these words or close to them:**

> Q needs three lines added to your `~/.claude/CLAUDE.md` so its always-on rules
> load each session. I will append exactly this and change nothing else:
>
> ```
> @rules/grounding.md
> @rules/safe-defaults.md
> @rules/subagent-tools.md
> ```
>
> Append them? (yes / show me where to paste them myself / skip)

- **yes** → append the three lines. If the file does not exist, create it containing
  only those lines.
- **show me** → print the block and the file path. Do not write.
- **skip** → say plainly what degrades: the AI will drive file I/O through Bash and
  trip the sandbox guards, it will not apply the investigate-before-answering
  discipline, and the destructive-operation policy will not be loaded. Continue
  anyway; this is recoverable at any time.

Every other rules file is lazy by design — read by the skill that needs it, when it
needs it. `rules/research-scope-framing.md` alone is 880 lines. **This is the
complete list; no later release asks for more.**

## Step 5 — ASK: how thorough verification should be

Q's proposition is independent verification, and verification costs tokens.

| Tier | A blocking gate | A routine check | Generation panel | Cost |
|---|---|---|---|---|
| `thorough` | `--sonnet 3 --opus 1` | `--sonnet 1 --opus 1` | at most 3 | highest |
| `standard` *(default)* | `--sonnet 1 --opus 1` | `--sonnet 1 --opus 1` | at most 2 | moderate |
| `light` | `--opus 1` | `--opus 1` | at most 1 | low |
| `minimal` | `--sonnet 1` | `--sonnet 1` | at most 1 | lowest |

**Ask the user which tier**, then store it — one command, and it takes effect on
the next skill run:

```bash
python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/rigor.py" set <their answer>
```

The setting is read **at dispatch**, not baked into anything, so it is changeable
at any time afterwards — `rigor.py set <tier>`, or `./setup.sh --settings` for the
same question again. `rigor.py show` prints the active tier, where it came from,
and what each kind of site currently dispatches. An exported
`Q_VALIDATION_RIGOR` outranks the stored value, for a one-session override.

State the trade honestly when you ask: **only `thorough` is a vote** — three
checkers that must agree. The other three are one isolated opinion. Better than
self-assessment; not the same guarantee. And at `minimal` there is no Opus
checker, so the advisory scope-coverage pass does not run.

Two things are not on this dial, and say so if asked: every tier still dispatches
an isolated checker that is not the producer, and the fixed pipelines (plan gates,
research fact-check, KL extraction) keep their canonical three-checker allocation
regardless.

If the user has no preference, use `standard` and tell them you did.

## Step 6 — Merge the hook registrations

Hooks are what turn these skills from advice into gates. This release registers **142 hooks**. `settings.example.json` is generated from exactly the set in this tree, so a registration never points at a hook that is not here.

```bash
cp "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json" \
   "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json.bak-$(date +%Y%m%d%H%M%S)"
jq '.hooks | keys' settings.example.json
```

Merge `settings.example.json`'s `hooks` entries into the user's `settings.json`
**per event key**, preserving every hook already there. If the user has no
`settings.json`, copy the example and say so.

**If a merge would drop or reorder an existing hook, stop.** Show the user the
conflict and let them decide.

## Step 7 — Record the release marker

```bash
git describe --tags --abbrev=0 > "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.q-release"
```

`/q-update` reads this later to know what is installed. Without it, updating has to
guess.

## Step 8 — Verify and report

```bash
python3 verify_kit.py . --no-explore
bash -n .claude/hooks/*.sh
```

```bash
# every gate is syntactically sound
bash -n .claude/hooks/*.sh

# the engines import (without the optional extras, too)
python3 -c 'import sys; sys.path.insert(0, ".claude/hooks"); import _factcheck_engine'
```

Then report to the user, in this shape:

- files installed, and where
- whether the three rules were appended, shown, or skipped
- which rigor tier is set
- whether `settings.json` was merged, and where the backup is
- anything you chose not to do, and why

Do not report success for a step you skipped.

## Step 9 — First run

Try the smallest loop first: ask your agent something, then run `/recommend` on its answer.

Available in this release: `/recommend`, `/challenge`, `/double-check`, `/work-frame-and-create-todo`, `/session-start`, `/outcome-framing`, `/lean-analytics-metrics`, `/research`, `/clarification`, `/prompt-for-handoff`, `/solution-designer`, `/solution-slicer`, `/solution-design`, `/plan`, `/plan-followups-review`, `/work-start`, `/work-done`, `/execute-plan`, `/close`, `/ninja-fix`, `/q-update`.

---

## Updating later

Once installed, `/q-update` handles subsequent releases: it reads `.q-release`,
compares it with the clone, shows what changed, and re-applies only the delta —
under the same prohibitions as above. Do not re-run `setup.sh` to update; it copies
over the top rather than reconciling.

---

## What this repo does **not** ship, and why

- **A `CLAUDE.md`.** Yours is yours. Step 4 shows the three lines to add.
- **A `settings.json`.** Same reason — step 6 merges an example instead.
- **The knowledge-library extractions.** Several skills ground their judgment in
  book extractions that are the authors' copyright — see [NOTICES.md](NOTICES.md).
  Where the source is unreachable, those skills fall back and say so.
- **The author's deployment tooling.** Q is developed in a private tree with its own
  promotion machinery. None of that is part of the framework, and none of it ships.

---

## Uninstalling

Q writes only under `~/.claude/`. Remove the files this release installed, restore
`settings.json` from the backup step 6 made, and delete the three `@rules/` lines
from `CLAUDE.md`.

<!-- Generated by q-release from INSTALL.tmpl.md. Do not edit directly — edit the
     template, then run `q-release docs install`. -->

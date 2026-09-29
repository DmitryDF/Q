# Skill Location Rule

**Source:** claude-infra-overhaul Q&A-1 (2026-06-21) + Design A9 (2026-06-26)
**Enforcement layer:** Rules (Layer 2) — AI reads and applies judgment

---

## Rule

**Project-specific skills stay in `[Project]/Skills/`.**
**Only truly cross-cutting skills migrate into `~/.claude/skills/`.**

**Location IS the scope statement.**

If a skill needs topic-scoping (it should fire only in certain projects), it does not
belong in the harness. The harness is global to whatever `CLAUDE_CONFIG_DIR` points at —
anything in `~/.claude/skills/` fires everywhere. There is no manifest, allowlist, or
conditional loader. The folder location is the only enforcement mechanism.

Selection rule:
- Does the skill help with one project or project-type only? → keep it in `[Project]/Skills/`.
- Does the skill help with any topic, any project, everywhere? → it belongs in the harness.

This rule resolves the apparent conflict between "topic-scoped skills" and the worktree
"structural binding, not convention" principle: worktrees bind branch↔filesystem path;
skill-location binds scope↔folder location. Both are topology, not discipline.
The two-rule system is not contradictory — they operate at different levels.

---

## Authoring convention: Agent Skills SKILL.md standard

Skills and commands are authored to the **Agent Skills `SKILL.md`** open standard
(Linux Foundation / Agentic AI Foundation, AAIF; `agentskills.io`; Anthropic Dec 2025;
30+ tools including Claude Code, Gemini CLI, OpenAI Codex CLI, Cursor, Cline, etc.).

A `SKILL.md` file has:
1. **YAML frontmatter** — minimum required fields:
   - `name`: the skill's name (one word or hyphenated slug, e.g. `double-check`)
   - `description`: one or two sentence description used by the AI to decide when to invoke the skill
2. **Markdown body** — the skill's instructions, in any structure the skill author chooses.

```yaml
---
name: my-skill
description: One-sentence description the AI uses to decide when to invoke this skill.
---
```

The body format is skill-internal. The frontmatter is the portability contract.

**Vendor-frontmatter extensions** (Claude-specific fields: `disable-model-invocation`,
`user-invocable`, `agent`, `model`, `context`) are ignored by other tools. Author only
`name` + `description` in frontmatter for maximum portability. Vendor extensions are
advisory, never load-bearing.

**Install paths differ per vendor** (`.claude/skills/` vs `.agents/skills/` vs
`~/.gemini/skills/`). The `SKILL.md` content itself is identical; only the directory
changes. For cross-vendor distribution, use symlinks or `npx skills add`.

---

## MCP tools — the cross-vendor tool layer

For tools (callable functions the AI invokes), use **MCP (Model Context Protocol)**,
also AAIF-governed (Anthropic Nov 2024; full cross-vendor support: Claude, Gemini,
Codex, Cline, Cursor, etc.).

MCP primitives:
- **Tools** — model-controlled callables (the primary mechanism for check-logic)
- **Resources** — read-only data sources
- **Prompts** — user-triggered workflow templates (≈ slash commands via MCP)

An MCP server is the most vendor-agnostic distribution path for check-logic:
the check-logic lives in the MCP server; vendors call it over JSON-RPC.

---

## Standalone check-logic — the portability discipline

Check-logic (gates, validators, linters) MUST be kept as **standalone Python modules**
callable directly without the Claude hook harness:

```
# DO NOT: bury logic inside a hook script
# check-something.sh:
#   complex Python inline logic here

# DO: standalone module + thin hook wrapper
# check_logic/validate_something.py:
#   def validate(input: dict) -> dict: ...
#
# check-something.sh (thin wrapper):
#   python3 "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/validate_something.py" ...
```

Standalone logic is:
- **Unit-testable** without the Claude harness (run `python3 module.py --self-test`)
- **Vendor-neutral** (the logic itself carries no Claude-specific imports)
- **Portable to other trigger adapters** (MCP servers, Gemini hooks, Codex hooks)

The check function should accept plain JSON/dict inputs and return plain dict outputs.
It must not import Claude-specific APIs, settings, or hook infrastructure.

---

## Per-vendor adapter — the ONLY vendor-specific surface

The per-vendor **invocation/enforcement trigger** is the ONLY Claude-specific surface.

In Claude Code: `PreToolUse` / `PostToolUse` / `Stop` hook registration in `settings.json`.

This is the "swappable adapter" in the hexagonal architecture
(`code_first_architecture.md` — Cockburn Ports & Adapters):

```
[SKILL.md body / check-logic Python module]  ← vendor-neutral
        ↓ (port)
[per-vendor hook registration]               ← Claude-specific adapter
        ↓ (trigger)
[Claude Code hook harness]                   ← Claude runtime
```

On Gemini CLI, the same Python scripts would be registered under `BeforeTool` hooks.
On Codex CLI, under `PreToolUse` with a different config format. The scripts do not
change; only the trigger/adapter config changes per vendor.

**Practical constraint:** hook configuration formats are proprietary per vendor
(Zylos Research, Mar 2026). The concept ports to Claude Code / Gemini CLI / Codex CLI /
OpenHands (all support exit-2 pre-tool blocking); aider and opencode have no blocking
hooks. The most durable cross-vendor enforcement is an MCP proxy (V1).

---

## Summary table

| Surface | Location | Scope | Portability |
|---------|----------|-------|-------------|
| Skill instructions | `[Project]/Skills/SKILL.md` or `~/.claude/skills/<name>/SKILL.md` | project or global | portable (Agent Skills standard) |
| Check-logic module | `${KIT_HOOKS_DIR}/<module>.py` (standalone callable) | global | portable (pure Python) |
| Hook trigger adapter | `~/.claude/settings.json` `hooks:` section | Claude-specific | Claude only — swap per vendor |
| MCP tool | MCP server (any language) | cross-vendor | portable |

---

*Lock source: Q&A-1 (2026-06-21): "Location IS the scope statement." Design A9 (2026-06-26):
"project-specific skills stay in [Project]/Skills/; only cross-cutting migrate."*

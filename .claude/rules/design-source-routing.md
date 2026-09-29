# Design Source Routing

Phase: Explore (read-only, before Gate 0a in Plan Mode).

## When

Entering Plan Mode for a task that involves code implementation
(new system, refactor, or adding AI to a system).

Skip for: research, writing, process, or non-coding plans.

Note: "Is this a coding plan?" is a judgment call — this rule
is Layer 2 (rules file) because the routing decision cannot be
fully automated.

## What to Load

| Condition | Source | Location |
|---|---|---|
| Any coding plan | Cockburn design principles | <KL>/Development/Sources/Books/simplifying-software-design-cockburn/simplifying-software-design-cockburn.md |
| AI is a runtime actor | Also: code-first architecture | ~/.claude/rules/code_first_architecture.md |

Read the loaded sources. Read the relevant codebase.
Do not make design decisions yet — that's 0d.

## Anthropic Basis

Supported: "Never speculate about code you have not opened.
Read the file before answering." (Claude 4 best practices).
"Explore the landscape before drilling into specifics."
(Multi-agent research system).

Editorial extension: Anthropic recommends loading API/SDK docs
before tool-building. Extending this to design frameworks
(Cockburn) is editorial — not Anthropic doctrine.

# Subagent Tool Preference

When searching file contents, ALWAYS use the Grep tool — never `grep`, `rg`, or `cd && grep` via Bash.
When searching for files by name, ALWAYS use the Glob tool — never `find` or `ls` via Bash.
When reading files, ALWAYS use the Read tool — never `cat`, `head`, or `tail` via Bash.

When creating or saving files, ALWAYS use the Write tool — never redirects (`>`, `>>`), heredocs, or `echo` via Bash.
When merging files, Read both files, concatenate in context, Write the result — never `{ cat a; cat b } > c` via Bash.

These dedicated tools handle paths with spaces (iCloud Mobile Documents) correctly and don't trigger sandbox permission prompts.

## Sandbox Heuristic Triggers

Claude Code has a **command injection detection** layer that fires **independently of permission allow rules** and cannot be suppressed. From the docs: "Suspicious bash commands require manual approval even if previously allowlisted." Allow rules like `Bash(cd * && git *)` do NOT prevent these prompts.

Avoid these patterns entirely:

| Pattern | Trigger message | Use instead |
|---------|----------------|-------------|
| `cd "path" && git ...` | "Compound commands with cd and git require approval to prevent bare repository attacks" *(hook-absent: subsumed into `git -C` rule — rules file guidance, no regex needed)* | `git -C "path" ...` (single command, no `cd`) |
| `cd "path" && python` | Same trigger *(hook-present: P2 blocks)* | Use absolute path or write `.py` script |
| `python3 -c "...\n..."` (any newlines) | "multi-line content in -c argument" or "quoted newline followed by a #-prefixed line" *(hook-present: P1 blocks)* | Write a `.py` file, then `python3 script.py` |
| `python << 'EOF'` (heredoc to python stdin) | "brace obfuscation" or "Parser aborted" *(hook-present: P6 blocks)* | Write a `.py` file |
| `cat > file` or `cat > file << 'EOF'` | "brace obfuscation" *(hook-present: P8 blocks)* | Write tool |
| `$(cmd)` | "Command contains $() command substitution" *(hook-present: P3 blocks, with `$(cat <<` exception)* | Native tools or `.py`/`.sh` script |
| `` `cmd` `` | Backtick substitution *(hook-absent: regex-ambiguous)* | Native tools or script |
| `$!` (background PID capture) | "variables in dangerous contexts" or "newlines that could separate multiple commands" *(hook-present: P4 blocks)* | Write a `.sh` script or use `run_in_background` |
| `\| xargs kill` / `\| xargs rm` | "output redirection (>)" (sandbox flags the `2>/dev/null` in the same command) *(hook-present: P5 blocks)* | `fuser -k <port>/tcp` or find-then-kill separately |
| `sed -n 'N,Mp' file` | "sed command contains operations that require explicit approval" *(hook-present: P7 blocks)* | Read tool with offset and limit |
| Multi-line script with `for`/`while`/`done`/`fi`/`esac` | "Unhandled node type: string" *(hook-present: P9 blocks)* | Write a `.sh` file |
| `$'...'` | ANSI-C quoting *(hook-absent: regex-ambiguous)* | Regular quotes |
| `"" -flag` | Empty quotes before dashes *(hook-absent: regex-ambiguous)* | Avoid empty-string args |
| `} > "$var"` | Brace with quote (expansion obfuscation) *(hook-absent: regex-ambiguous)* | Write tool |

### Git commands — always use `-C` flag

Never: `cd "path" && git status`
Always: `git -C "path" status`

This applies to all git subcommands: `status`, `add`, `commit`, `diff`, `log`, `stash`, `push`.

Note: `${var}` is not a confirmed trigger, but often co-occurs with triggers above. Safest: avoid Bash for file operations entirely.

## Decision Rule

Before writing a Bash command, ask: **can Read, Write, Glob, or Grep do this?** If yes, use the native tool. Bash is for: `pandoc`, `pdftotext`, `git`, `pytest`, `chmod`, and running `.py`/`.sh` scripts. Never for file I/O, `sed`, `awk`, or shell control flow.

For JSONL task output parsing, use `<KL>/Scripts/parse-task-output.py` — never inline `python3 -c`.

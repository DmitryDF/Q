#!/bin/bash
# PreToolUse hook: block Bash patterns that trigger injection detection heuristics.
# These heuristics fire ABOVE the permission layer — allow rules can't suppress them.
# Blocking here prevents the "Do you want to proceed?" prompt entirely.
#
# Runs in parallel with fix-backslash-paths.sh. Uses exit 2 (not JSON) to avoid
# conflicting with the other hook's updatedInput JSON output.

[ "$CLAUDE_CODE_REMOTE" = "true" ] && exit 0

INPUT=$(cat)

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')
[ "$TOOL_NAME" != "Bash" ] && exit 0

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
[ -z "$COMMAND" ] && exit 0

# Pattern 1: python3 -c with ANY newlines (not just # comments)
# Triggers: "quoted newline followed by a #-prefixed line" and "multi-line content in -c"
# Regex fix: python3?"? handles quoted paths like "/path/to/python" -c
if printf '%s' "$COMMAND" | grep -qE 'python3?"?\s+-c\s'; then
  if printf '%s' "$COMMAND" | perl -0777 -ne 'exit 0 if /\n/; exit 1'; then
    echo "BLOCKED: python3 -c with multi-line content triggers sandbox heuristic. Write a .py file instead, then run it." >&2
    exit 2
  fi
fi

# Pattern 2: cd "path" && any-command
# Triggers: "Compound commands with cd and git require approval to prevent bare
# repository attacks" / "Compound command contains cd with output redirection —
# manual approval required to prevent path resolution bypass"
# Broadened from cd+python only: all cd && forms trigger the sandbox.
if printf '%s' "$COMMAND" | grep -qE '^cd\s.*&&'; then
  echo "BLOCKED: cd && command triggers sandbox heuristic. Use absolute paths, git -C, or write a script." >&2
  exit 2
fi

# Pattern 3: $() command substitution
# Exception: $(cat << used in git commit heredoc pattern
if printf '%s' "$COMMAND" | grep -qF '$('; then
  if ! printf '%s' "$COMMAND" | grep -qF '$(cat <<'; then
    echo "BLOCKED: \$() command substitution triggers sandbox heuristic. Write a .sh or .py script instead, then run it. For polling, use run_in_background." >&2
    exit 2
  fi
fi

# Pattern 4: $! (PID capture from backgrounded process)
# Always implies multi-line process orchestration
if printf '%s' "$COMMAND" | grep -qF '$!'; then
  echo "BLOCKED: \$! (background PID capture) triggers sandbox heuristic. Write a .sh script or use run_in_background parameter." >&2
  exit 2
fi

# Pattern 5: pipe to xargs kill/rm
if printf '%s' "$COMMAND" | grep -qE '\|\s*xargs\s+(kill|rm)'; then
  echo "BLOCKED: pipe to xargs kill/rm triggers sandbox heuristic. Use fuser -k <port>/tcp, or find PIDs first then kill as a separate command." >&2
  exit 2
fi

# Pattern 6: python heredoc reading stdin (audit line 740, R13)
# Triggers: sandbox "Contains brace with quote character (expansion obfuscation)"
# when body contains dict literals; also "Parser aborted" on long bodies.
if printf '%s' "$COMMAND" | grep -qE 'python\w*\s+-\s*<<'; then
  echo "BLOCKED: python heredoc triggers sandbox heuristic. Use scripts/worker.py (Session 6 deliverable; until it lands, write a .py file and run it)." >&2
  exit 2
fi

# Pattern 7: sed -n with numeric line address (Np, N,Mp, N,$p, N,M p)
# Triggers: "sed command contains operations that require explicit approval"
# Three-stage check: (1) sed-with-n flag, (2) numeric-address-with-p (POSIX
# allows optional whitespace before p), (3) NOT -i (in-place edit).
if printf '%s' "$COMMAND" | grep -qE 'sed[[:space:]]+-[a-zA-Z]*n[a-zA-Z]*[[:space:]]+'; then
  if printf '%s' "$COMMAND" | grep -qE "'?[0-9]+(,([0-9]+|\\\$))?[[:space:]]*p'?"; then
    if ! printf '%s' "$COMMAND" | grep -qE 'sed[[:space:]]+-[a-zA-Z]*i'; then
      echo "BLOCKED: sed -n '<range>p' for reading lines triggers sandbox heuristic. Use the Read tool with offset and limit instead (e.g. Read file_path=/abs/path offset=391 limit=4). For content search use Grep; for file enumeration use Glob." >&2
      exit 2
    fi
  fi
fi

# Pattern 7b: sed reformatting the output of a read command (no -n flag).
# Triggers the SAME "sed command contains operations that require explicit
# approval" heuristic as P7, but P7's numeric-address guard only sees the
# `sed -n '<range>p'` form and misses substitution forms — measured 2026-08-16:
# `grep -o … | sed 's|.*/||' | head -40` passed P7 and prompted anyway, and a
# second form (`grep -n … | sed 'expr' 'expr'`) was mis-parsed by the sandbox as
# a glob write-target. Both sit ABOVE the permission layer, so no allow rule can
# suppress them; blocking here prevents the prompt entirely.
#
# Scope (deliberately narrow): sed DOWNSTREAM of a pipe whose upstream is a read
# command (grep/rg/ls/cat/find) — the read-and-reformat shape. A standalone sed,
# and any -i in-place edit, are left alone.
if printf '%s' "$COMMAND" | grep -qE '\b(grep|rg|ls|cat|find)\b[^|]*\|[^|]*\bsed\b'; then
  if ! printf '%s' "$COMMAND" | grep -qE 'sed[[:space:]]+-[a-zA-Z]*i'; then
    echo "BLOCKED: sed reformatting command output triggers the sandbox sed heuristic, which fires above the permission layer (no allow rule can suppress it). Use the Read tool, or Grep with -o / output_mode, or do the transformation in a .py script." >&2
    exit 2
  fi
fi

# Pattern 8: cat > file write-redirect (including heredoc form cat > file << 'EOF')
# Triggers: "Contains brace with quote character (expansion obfuscation)"
# Scope: cat[[:space:]]+> matches write-redirect form only (cat directly followed
# by spaces then >). Exception: $(cat << used in git commit heredoc (P3 pattern) may
# have "cat >" in the commit message body text — same exception as P3.
if printf '%s' "$COMMAND" | grep -qE 'cat[[:space:]]+>'; then
  if ! printf '%s' "$COMMAND" | grep -qF '$(cat <<'; then
    echo "BLOCKED: cat > file write-redirect triggers sandbox heuristic. Use the Write tool instead." >&2
    exit 2
  fi
fi

# Pattern 9: shell control flow keywords (for/while/if/done/fi/esac)
# Triggers: "Unhandled node type: string" — sandbox AST parser fails on control flow scripts.
# Two forms: (A) multi-line (newlines present), (B) single-line (semicolon-separated).
# Exception: $(cat << git commit heredoc may have control flow keywords in the message body.
# Form A: multi-line
if printf '%s' "$COMMAND" | perl -0777 -ne 'exit 0 if /\n/; exit 1'; then
  if ! printf '%s' "$COMMAND" | grep -qF '$(cat <<'; then
    if printf '%s' "$COMMAND" | grep -qwE 'for|while|done|fi|esac'; then
      echo "BLOCKED: multi-line shell script with control flow triggers sandbox heuristic. Write a .sh file instead, then run it." >&2
      exit 2
    fi
  fi
fi
# Form B: single-line (for/while ... do ... done on one line with semicolons)
if ! printf '%s' "$COMMAND" | grep -qF '$(cat <<'; then
  if printf '%s' "$COMMAND" | grep -qE '\bfor\b.*\bdo\b|\bwhile\b.*\bdo\b'; then
    echo "BLOCKED: single-line shell loop triggers sandbox heuristic. Write a .sh file instead, then run it." >&2
    exit 2
  fi
fi

# Pattern 10: variable-assignment preamble (VAR=value; cmd ... "$VAR")
# Triggers: "Contains expansion" / "simple_expansion" / "shell syntax (string) that
# cannot be statically analyzed". Measured 2026-08-14: 193 of 2,936 Bash calls over
# 50 sessions used this shape, and it prompts even when the command itself is
# allowlisted (python3, grep, bash and config-source all carry wildcard allow rules and
# prompted anyway) — the heuristic sits above the permission layer.
#
# Discriminator (deliberately narrow): an assignment that ENDS a statement — value
# is unquoted-without-spaces or a quoted string, followed by optional spaces and
# then ";" or end-of-line — exists only so a later "$VAR" can reference it.
# An env-var PREFIX (VAR=value command, space-separated) is a different, legitimate
# form (ALLOW_OUT_OF_TREE=1 git …, VERIFY_THEN_LAND_SKIP=1 claude-promote …) and is
# NOT matched. Same '$(cat <<' exception as P3/P8/P9 so commit-message bodies
# containing KEY=value lines are not caught.
# Form A: tilde in an assignment value — bash may expand at assignment time.
# Checked FIRST: it overlaps Form B (VAR=~/x; cmd matches both) and carries the
# more specific remedy, so the more actionable diagnosis wins. Also catches the
# env-prefix form (FOO=~/bar cmd), and note that iCloud paths containing
# "iCloud~md~obsidian" trip this even when quoted.
if ! printf '%s' "$COMMAND" | grep -qF '$(cat <<'; then
  if printf '%s' "$COMMAND" | grep -qE '(^|[[:space:];&|])[A-Za-z_][A-Za-z0-9_]*=~'; then
    echo "BLOCKED: tilde in an assignment value (VAR=~/path) triggers sandbox heuristic. Use \$HOME or the full /Users/... path, or inline it at the use site." >&2
    exit 2
  fi
fi
# Form B: assignment as its own statement.
if ! printf '%s' "$COMMAND" | grep -qF '$(cat <<'; then
  if printf '%s' "$COMMAND" | grep -qE '^[A-Za-z_][A-Za-z0-9_]*=("[^"]*"|[^[:space:];]*)[[:space:]]*(;|$)'; then
    echo "BLOCKED: VAR=value; ... \"\$VAR\" assignment preamble triggers sandbox heuristic. Inline the absolute path at each use site instead, or write a .sh script. (Env-var prefixes like FOO=1 cmd are fine.)" >&2
    exit 2
  fi
fi

exit 0

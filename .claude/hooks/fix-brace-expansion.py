#!/usr/bin/env python3
"""Transform commands with bash brace expansion {a,b,c} patterns.

The sandbox flags "Brace expansion" when it detects {comma,...} groups in
unquoted command regions. This transformer pre-expands the braces so the
resulting command uses explicit arguments instead.

Example input:
  ls "...Chapters/"_factcheck-r{1,2}-1{3,4,5}-*.md 2>/dev/null | sort

Example output:
  ls "...Chapters/"_factcheck-r1-13-*.md "...Chapters/"_factcheck-r1-14-*.md \
    "...Chapters/"_factcheck-r1-15-*.md "...Chapters/"_factcheck-r2-13-*.md \
    "...Chapters/"_factcheck-r2-14-*.md "...Chapters/"_factcheck-r2-15-*.md \
    2>/dev/null | sort

Reads command from stdin, writes transformed command to stdout.
Exits 0 whether or not a transformation was performed.
"""
import re
import sys


def expand_braces(s):
    """Recursively expand {a,b,...} groups, returning all expanded strings."""
    m = re.search(r'\{([^{}]*,[^{}]*)\}', s)
    if not m:
        return [s]
    pre = s[:m.start()]
    post = s[m.end():]
    options = m.group(1).split(',')
    result = []
    for opt in options:
        result.extend(expand_braces(pre + opt + post))
    return result


def find_token_bounds(command, first_brace_start, last_brace_end):
    """Find the start/end of the shell token that contains the brace groups.

    Scans backward from first_brace_start to find the token's start
    (respecting double-quoted strings), and forward from last_brace_end
    to find the end.
    """
    # Scan backward for token start
    in_dq = False
    tok_start = 0
    for k in range(first_brace_start - 1, -1, -1):
        c = command[k]
        if c == '"':
            in_dq = not in_dq
        elif c == ' ' and not in_dq:
            tok_start = k + 1
            break
    # (if we exhausted the loop, tok_start stays 0)

    # Scan forward for token end
    in_dq = False
    tok_end = len(command)
    for k in range(last_brace_end, len(command)):
        c = command[k]
        if c == '"':
            in_dq = not in_dq
        elif c == ' ' and not in_dq:
            tok_end = k
            break

    return tok_start, tok_end


def transform(command):
    """Expand brace expressions outside quoted strings; return transformed command."""
    # Quick check: any {a,b} pattern present at all?
    if not re.search(r'\{[^{}]*,[^{}]*\}', command):
        return command

    # Find brace groups that sit outside double-quoted strings
    in_dq = False
    brace_groups = []  # (start, end) positions in command
    i = 0
    while i < len(command):
        c = command[i]
        if c == '"':
            in_dq = not in_dq
        elif c == '{' and not in_dq:
            # Find matching }
            j = i + 1
            while j < len(command) and command[j] != '}':
                j += 1
            if j < len(command):
                inner = command[i + 1:j]
                if ',' in inner and '{' not in inner:
                    brace_groups.append((i, j + 1))
        i += 1

    if not brace_groups:
        return command

    first_start = brace_groups[0][0]
    last_end = brace_groups[-1][1]

    tok_start, tok_end = find_token_bounds(command, first_start, last_end)
    token = command[tok_start:tok_end]

    expanded = expand_braces(token)
    if len(expanded) <= 1:
        return command

    pre = command[:tok_start]
    post = command[tok_end:]
    return pre + ' '.join(expanded) + post


command = sys.stdin.read()
result = transform(command)
sys.stdout.write(result)
sys.exit(0)

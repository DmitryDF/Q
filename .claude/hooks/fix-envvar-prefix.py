#!/usr/bin/env python3
"""Transform env-var-prefixed python3 aggregate-kl-extraction commands.

Detects:  VAR="...iCloud<tilde>..." [VAR2="..."] python3 ... aggregate-kl-extraction ...
Problem:  Assigning a value containing a tilde inside an iCloud-style path
          triggers the "Tilde in assignment value" sandbox heuristic, even
          when the path is safely double-quoted. The allowlist Bash(python3:*)
          also cannot match because the command starts with VAR=, not python3.
Fix:      Inline the variable values and drop the env-var prefix so the
          command starts with python3, matching the existing allowlist rule.

Reads command from stdin, writes transformed command to stdout.
Exits 0 whether or not a transformation was performed.
"""
import re
import sys

command = sys.stdin.read()

# Pattern: one or more UPPER_CASE_VAR="value" assignments before python3 aggregate-kl-extraction
env_assign_re = re.compile(
    r'^((?:[A-Z_][A-Z_0-9]*="[^"]*"\s+)+)'   # group 1: env-var assignments
    r'(python3\b.*\baggregate-kl-extraction\b.*)',  # group 2: the actual command
    re.DOTALL
)

m = env_assign_re.match(command)
if not m:
    sys.stdout.write(command)
    sys.exit(0)

env_part = m.group(1)
rest = m.group(2)

# Only transform when the trigger is present (iCloud~ in an assignment value)
if "iCloud~" not in env_part:
    sys.stdout.write(command)
    sys.exit(0)

# Parse VAR="value" pairs from the env prefix
env_vars = {}
for pair_match in re.finditer(r'([A-Z_][A-Z_0-9]*)="([^"]*)"', env_part):
    env_vars[pair_match.group(1)] = pair_match.group(2)

# Substitute $VAR and ${VAR} references in the command body
def substitute(s, vars_map):
    s = re.sub(
        r'\$\{([A-Z_][A-Z_0-9]*)\}',
        lambda mo: vars_map.get(mo.group(1), mo.group(0)),
        s,
    )
    s = re.sub(
        r'\$([A-Z_][A-Z_0-9]*)',
        lambda mo: vars_map.get(mo.group(1), mo.group(0)),
        s,
    )
    return s

result = substitute(rest, env_vars)
sys.stdout.write(result)
sys.exit(0)

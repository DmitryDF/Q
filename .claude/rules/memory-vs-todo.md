# Memory vs TODO Decision Rule

When the user corrects AI behavior ("don't do X", "you keep doing Y wrong"):

**Ask: can the fix be automated (hook, gate, script, code check)?**

- **Yes → create a TODO** with the automation approach. Not a memory entry.
- **No → create a memory entry** (genuine preference, style, context that can't be coded).

Examples:

| Correction | Automatable? | Action |
|-----------|-------------|--------|
| "You don't verify plans thoroughly enough" | Yes (hook in check-plan-gates.sh) | TODO |
| "You don't follow convergence protocol" | Yes (round tracking in hook) | TODO |
| "Background agents get stuck" | Yes (timeout + auto-kill) | TODO |
| "Don't mock the database in tests" | No (judgment call per test) | Memory |
| "I prefer terse responses" | No (style preference) | Memory |
| "Load skills before launching agents" | Borderline — could be a hook, but judgment needed | Memory (for now) + TODO if repeated |

**The test:** If the correction describes something that failed because AI "forgot" or "optimized for speed" — it's an automation problem, not a memory problem. Memory doesn't fix AI forgetting.

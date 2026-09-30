"""S4 / A5 — `_topic_path` and `_write_topic_state` are KEYWORD-ONLY.

A5's goal is narrow and worth stating exactly, because the neighbouring slices
cover the other halves: making these two key-shaping functions keyword-only
makes a positional *transposition* of the two identity halves INEXPRESSIBLE at
the call site. It does NOT prevent misbinding by a misleading NAME (A4's
rollback tests in `test_phase_rollback.py` are what catch that, and A5's gate
requires them to stay green across the conversion), and it does NOT close the
shell positional route, which the plan carries as declared residual #1.

WHY THIS IS ITS OWN FILE, rather than more tests in `test_phase_rollback.py`.
That file pins an A4-scoped red/green measurement (`EXPECTED_TESTS` plus a
prose split) and warns in its own docstring that adding a test there keeps the
pin green while silently rotting the split. A5's tests have a different premise
from A4's, so folding them in would have invalidated a measurement this plan
deliberately maintains. Kept apart on purpose.

WHAT THE INVARIANT WALK SEES, AND WHAT IT DOES NOT.

This boundary has now been mis-stated in three successive rounds, each time in
the text written to fix the previous mis-statement, and each time because it was
written as an OPEN LIST of shapes that turned out to be incomplete. It is
therefore stated here as a CLOSED RULE in two halves, which can be checked
against the code and cannot rot by omission.

RULE 1 — WHAT RESOLVES TO A WATCHED FUNCTION. A name is treated as one of the
two functions only when it is bound, in a form below, to a bare `Name` or
`Attribute` reference naming one of them:
  * a DIRECT reference at the call — `_topic_path(...)`, `ppg._write_topic_state(...)`;
  * a plain assignment — `real = ppg._write_topic_state`;
  * an annotated assignment WITH a value — `real: Callable = ppg._topic_path`;
  * a walrus — `(real := ppg._topic_path)`;
  * tuple/list unpacking where BOTH sides are literal sequences and neither
    target is starred — `a, b = ppg._topic_path, other`.
Anything else on the right-hand side resolves to nothing: a call
(`functools.partial(...)`, `getattr(mod, "_topic_path")` — the literal form is
missed too, because the RHS is a Call), a conditional expression, a subscript, a
comprehension, an `import ... as`, or a value assembled at runtime. Those are
MISSES, and this rule says so rather than listing them and hoping the list is
complete.

RULE 2 — WHAT SHADOWS, AND WHY A MISS CANNOT BECOME A FALSE POSITIVE. Any name
bound ANYWHERE in a scope by ANY mechanism this file does not resolve under
Rule 1 is REMOVED from the inherited alias map for that whole scope. Parameters,
`for` targets, `with ... as`, `except ... as`, comprehension targets, star
targets, `import`, `global`/`nonlocal`, a nested `def`/`class` of the same name,
and every unresolvable assignment all shadow.

The two rules together AIM at the property that matters: an unrecognised binding
form should make the walk report LESS, never more, because a false positive here
accuses correct code while a miss only fails to catch a defect the signature
itself already turns into a loud `TypeError` at runtime.

That aim is stated as a DIRECTION, not as a proof, and the reason is worth
recording. Four successive review rounds each found a defect in the sentence
written to fix the previous round's — three of them in absolute claims of
exactly this shape. The known false-positive shapes found that way are now
closed and pinned by tests: an attribute call whose `.attr` collides with an
alias name (`unrelated.real(...)`), a class body treated as a closure link, a
parameter that shadows one of the two literal names, and every binding form in
Rule 2. What is NOT claimed is that no other such shape exists. The honest
statement is that the walk is a convenience which catches these calls at test
time, and that A5's actual enforcement is the keyword-only SIGNATURE, which
fails loudly at runtime no matter what this walk sees.

One version caveat, stated because Rule 2 is otherwise absolute: `match` capture
patterns bind, and they ARE handled — but only on an interpreter that has the
`ast.Match*` node classes. The gate runs Python 3.9, where a `match` statement
does not parse at all, so such a file is skipped by the SyntaxError guard in
`_walk` and the question cannot arise there.

Scope handling covers `def`, `async def`, `class`, `lambda`, and comprehensions.
Resolution is scope-chained: a closure inherits its enclosing map, minus that
scope's own bound names. Within ONE scope a rebind is source-ordered, so it
governs only the calls that follow it. A NESTED scope inherits the enclosing
map as it stands after the whole enclosing scope has been read — which is
Python's late-binding closure semantics, not an ordering bug.

The parametrized tests below deliberately make positional calls through
`getattr` so that they MUST raise; they are not flagged, and that is Rule 1's
call-on-the-right-hand-side miss rather than a special case.

An earlier revision claimed this walk was "strictly stronger than the grep the
gate asks for". That was false and is corrected here rather than reworded: a
plain name-grep for `_write_topic_state` WOULD have surfaced the alias binding
at `test_auto_register.py:251`, which the name-matching AST walk of the day
could not. The two techniques miss different things. This one is broader on call
SHAPE — it sees multi-line calls, which a line-oriented grep does not
(`test_work_done.py:114` was such a case) — and it is alias-aware, but it is not
a superset of grep.

Isolation: `HOOKS` resolves from `Path.home()`, exactly as
`test_phase_rollback.py` does, and the gate verb is what redirects HOME to the
clone's shim. Run it through the gate, not bare pytest:

    bash "$CLAUDE_CONFIG_DIR/hooks/tests/plan_env.sh" gate

A bare `python3 -m pytest <this file>` resolves HOOKS to the LIVE tree and so
grades the wrong binary — it fails rather than falsely passing, because live is
still positional.
"""
import ast
import importlib.util
from pathlib import Path

import pytest

HOOKS = Path.home() / ".claude" / "hooks"

# The two functions A5 converts. Named once; every test below reads this.
KEYWORD_ONLY = ("_topic_path", "_write_topic_state")

_SCOPE_NODES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                ast.GeneratorExp)


def _import(name, filename):
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ppg = _import("pre_plan_gates_s4_keyword_only", "pre_plan_gates.py")


def _referenced_name(node):
    """The bare name a Name/Attribute node refers to, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _split_scope(scope):
    """Partition a scope into (its own nodes, its TOP-MOST nested scopes).

    Recurses through non-scope children ONLY and stops at the first scope
    boundary, so a `def` nested inside an `if`/`with`/`try` is returned as a
    nested scope instead of having its body leak into this one. A grand-nested
    scope is reached when that nested scope is itself split, never handed to the
    grandparent.

    The previous version used `ast.walk`, which prunes nothing: it yielded a
    compound-nested function's statements into the enclosing stream AND yielded
    the function again as a nested scope. Measured consequences were a silenced
    genuine call, a flagged innocent call, and a duplicated report — all three
    pinned by tests below.
    """
    own, nested = [], []

    def rec(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPE_NODES):
                nested.append(child)
            else:
                own.append(child)
                rec(child)

    rec(scope)
    return own, nested


def _binding_pairs(node):
    """(local_name, resolved_or_None) pairs a node binds — or None if it binds
    nothing at all.

    `None` as the RETURN means "not a binding node, keep looking at it"; `None`
    as a pair's second element means "this name is bound to something Rule 1
    cannot resolve", which UNBINDS any inherited alias of that name. Keeping
    both in one function is what makes Rule 2 apply in source order as well as
    scope-wide — a `for real in fns:` after `real = ppg._topic_path` must
    unbind, and a whole-scope pre-drop alone re-established it.
    """
    if isinstance(node, ast.Assign):
        pairs = []
        for target in node.targets:
            if isinstance(target, ast.Name):
                pairs.append((target.id, _referenced_name(node.value)))
            elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(
                    node.value, (ast.Tuple, ast.List)) and not any(
                    isinstance(e, ast.Starred) for e in target.elts):
                # `a, b = ppg._topic_path, other` -- pair elementwise. A star
                # target makes the correspondence positional-unsafe, so it falls
                # through to the unresolved branch below instead.
                for tgt, val in zip(target.elts, node.value.elts):
                    if isinstance(tgt, ast.Name):
                        pairs.append((tgt.id, _referenced_name(val)))
            else:
                pairs.extend((n, None) for n in _target_names(target))
        return pairs
    if isinstance(node, ast.AnnAssign):
        # A BARE annotation (`real: Callable`) binds nothing in Python.
        if node.value is None:
            return []
        return [(n, _referenced_name(node.value)) for n in _target_names(node.target)]
    if isinstance(node, ast.NamedExpr):
        return [(n, _referenced_name(node.value)) for n in _target_names(node.target)]

    # Every other binding form is unresolvable by Rule 1, so it unbinds.
    if isinstance(node, ast.AugAssign):
        return [(n, None) for n in _target_names(node.target)]
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [(n, None) for n in _target_names(node.target)]
    if isinstance(node, (ast.With, ast.AsyncWith)):
        # Handled at STATEMENT level, not on the bare `withitem`: a `withitem`
        # carries no `lineno`, so `_order_key` sorted it to position 0 and it
        # unbound before the assignment it was meant to follow.
        names = []
        for item in node.items:
            if item.optional_vars is not None:
                names.extend(_target_names(item.optional_vars))
        return [(n, None) for n in names]
    if isinstance(node, ast.ExceptHandler):
        return [(node.name, None)] if node.name else []
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [((a.asname or a.name).split(".")[0], None) for a in node.names]
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return [(n, None) for n in node.names]
    if isinstance(node, ast.Delete):
        # `del real` unbinds. A later call is a NameError in Python, so flagging
        # it would accuse code that cannot run -- still a false positive.
        names = []
        for target in node.targets:
            names.extend(_target_names(target))
        return [(n, None) for n in names]
    match_names = _match_bound_names(node)
    if match_names:
        return [(n, None) for n in sorted(match_names)]
    return None


def _order_key(node):
    return (getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


# `match` statements parse only on Python 3.10+, and the gate's interpreter is
# 3.9, where these node classes do not exist. Resolved defensively so the file
# imports on both and the handling is real wherever `match` can occur at all.
_MATCH_NODES = tuple(
    n for n in (getattr(ast, "MatchAs", None), getattr(ast, "MatchStar", None))
    if n is not None
)
_MATCH_MAPPING = getattr(ast, "MatchMapping", None)


def _match_bound_names(node):
    """Names a `match` capture pattern binds, on interpreters that have them."""
    names = set()
    if _MATCH_NODES and isinstance(node, _MATCH_NODES):
        if getattr(node, "name", None):
            names.add(node.name)
    if _MATCH_MAPPING is not None and isinstance(node, _MATCH_MAPPING):
        if getattr(node, "rest", None):
            names.add(node.rest)
    return names


def _target_names(node):
    """Every plain name a target expression binds (recursing tuples/stars)."""
    names = set()
    if isinstance(node, ast.Name):
        names.add(node.id)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            names |= _target_names(elt)
    elif isinstance(node, ast.Starred):
        names |= _target_names(node.value)
    return names


def _bound_names(scope, own, nested):
    """Every name this scope binds by ANY mechanism — Rule 2's input.

    Deliberately over-inclusive. A name here is dropped from the inherited alias
    map, so a binding form this file cannot resolve makes the walk report LESS,
    never more. That is what keeps an unrecognised shape from turning into a
    false positive against correct code.
    """
    names = set()

    args = getattr(scope, "args", None)
    if isinstance(args, ast.arguments):
        for group in (getattr(args, "posonlyargs", []), args.args, args.kwonlyargs):
            for a in group:
                names.add(a.arg)
        if args.vararg:
            names.add(args.vararg.arg)
        if args.kwarg:
            names.add(args.kwarg.arg)

    for gen in getattr(scope, "generators", []) or []:
        names |= _target_names(gen.target)

    for node in own:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                names |= _target_names(target)
        elif isinstance(node, ast.AnnAssign):
            # A BARE annotation (`real: Callable`) binds nothing in Python.
            if node.value is not None:
                names |= _target_names(node.target)
        elif isinstance(node, ast.AugAssign):
            names |= _target_names(node.target)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            names |= _target_names(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                names |= _target_names(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                names.add(node.name)
        elif isinstance(node, ast.NamedExpr):
            names |= _target_names(node.target)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                names |= _target_names(target)
        else:
            names |= _match_bound_names(node)

    for child in nested:
        child_name = getattr(child, "name", None)
        # `def _topic_path(...)` binds that name to the FUNCTION ITSELF, so it
        # is not an unresolvable shadow. Belt-and-braces: the ordered pass in
        # `_scan_scope` is what the non-vacuity guard actually pins (reverting
        # THIS line alone changes no result, because that pass re-establishes
        # the binding). Kept so the inherited map is correct in its own right.
        if child_name and child_name not in KEYWORD_ONLY:
            names.add(child_name)

    return names


def _scan_scope(scope, inherited, label, found, counter):
    """Resolve aliases in this scope, flag its calls, then recurse.

    `inherited` maps a local name to the function name it is bound to, carried
    down the scope chain so a closure sees its enclosing function's alias. A
    binding in this scope SHADOWS an inherited one -- including a rebind to
    something unwatched, which correctly removes it.

    Nodes are visited in SOURCE ORDER, so a rebind takes effect only for calls
    that follow it. Collecting every binding first made the map last-write-wins,
    which silenced a genuine call appearing before a later rebind.
    """
    own, nested = _split_scope(scope)
    # Rule 2: a name this scope binds by any unresolved mechanism stops being a
    # watched alias here. Applied to the WHOLE scope, matching Python's
    # function-scope binding (a name assigned anywhere in a function is local
    # throughout it), then re-established below for the forms Rule 1 resolves.
    bound = _bound_names(scope, own, nested)
    aliases = {k: v for k, v in inherited.items() if k not in bound}
    # Bound-but-unresolved names are recorded EXPLICITLY as None rather than
    # merely absent, so the literal-name arm below can see that a scope has
    # shadowed one of the two function names itself -- `def f(_topic_path):`.
    for name in bound:
        aliases.setdefault(name, None)

    # Nested scopes join the ordered pass for their NAME binding only: `def
    # real(): ...` rebinds `real` in THIS scope at its own line. Their bodies
    # are walked afterwards, with the map as it finally stands.
    # Membership by id(): `in` on a list of AST nodes is an O(n) scan per
    # iteration, which took the gate from ~6s to ~47s across the tree.
    nested_ids = {id(n) for n in nested}
    for node in sorted(own + nested, key=_order_key):
        if id(node) in nested_ids:
            child_name = getattr(node, "name", None)
            if child_name:
                # A `def` of one of the two names IS that function; anything
                # else of the same name shadows it.
                aliases[child_name] = (child_name if child_name in KEYWORD_ONLY
                                       else None)
            continue
        pairs = _binding_pairs(node)
        if pairs is not None:
            for local, referenced in pairs:
                aliases[local] = referenced
            continue
        if isinstance(node, ast.Call):
            name = _referenced_name(node.func)
            if isinstance(node.func, ast.Name):
                # A bare name: the alias map decides, and an entry of None
                # (shadowed in this scope) beats the literal match.
                resolved = (aliases[name] if name in aliases
                            else (name if name in KEYWORD_ONLY else None))
            else:
                # An ATTRIBUTE: only a literal `<recv>._topic_path` resolves.
                # The alias map must NOT be consulted here -- `_referenced_name`
                # returns `.attr`, so `unrelated.real(...)` would otherwise hit
                # an alias named `real` and flag a call to a different object.
                resolved = name if name in KEYWORD_ONLY else None
            if resolved in KEYWORD_ONLY:
                counter[0] += 1
                if node.args:
                    found.append(
                        f"{label}:{node.lineno}: {name}() with "
                        f"{len(node.args)} positional arg(s)"
                    )

    # A CLASS BODY is not a closure link: a method does not see names bound in
    # the class body (referencing one raises NameError), so its children inherit
    # what the class itself inherited, never the class-body map.
    child_map = inherited if isinstance(scope, ast.ClassDef) else aliases
    for child in nested:
        _scan_scope(child, child_map, label, found, counter)


def _scan_tree(tree, label):
    """One pass per file: (positional offenders, count of resolved calls).

    Both are produced by the SAME traversal. Running the offender scan and the
    non-vacuity count as two separate walks doubled the cost of the tree sweep,
    which is the slowest thing in the gate.
    """
    found, counter = [], [0]
    _scan_scope(tree, {}, label, found, counter)
    return found, counter[0]


def _positional_calls_in(tree, label):
    """Positional calls to either function, direct or via a resolvable alias."""
    return _scan_tree(tree, label)[0]


def _walk(root):
    """Yield (label, tree) for every importable module under `root`.

    Skips `.bak-*` snapshots -- frozen copies of superseded revisions, which are
    not importable (`.bak-<ts>` is not a `.py` suffix) and are expected to hold
    the old form -- and files that do not parse, so an unrelated non-Python
    fixture cannot fail this walk for the wrong reason.
    """
    for path in sorted(root.rglob("*.py")):
        if ".bak" in path.name:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Rule 1 resolves a call only through a bare Name/Attribute naming one
        # of the two functions, so the literal token must appear in the source.
        # A file mentioning neither cannot hold a resolvable call, and skipping
        # it is what keeps this sweep from parsing the whole tree.
        if not any(name in text for name in KEYWORD_ONLY):
            continue
        try:
            yield (str(path.relative_to(root)), ast.parse(text))
        except SyntaxError:
            continue


def test_no_positional_calls_to_the_keyword_only_identity_functions():
    """A5's zero-positional-calls invariant, over the whole hooks tree.

    Non-vacuity: the walk must actually FIND these functions somewhere, or a
    typo'd name / an empty tree would let this pass by discovering nothing.
    """
    seen_calls = 0
    offenders = []
    per_file = {}
    for label, tree in _walk(HOOKS):
        found, count = _scan_tree(tree, label)
        seen_calls += count
        per_file[label] = count
        offenders.extend(found)

    assert seen_calls > 0, (
        f"found no calls to either function anywhere under {HOOKS} — this test "
        "would pass vacuously, so it is failing instead"
    )
    # A bare `seen_calls > 0` is too weak: this file's OWN fixtures satisfy it,
    # so the sweep could go blind to the module under test and still pass. That
    # is not hypothetical -- a round-4 narrowing fix marked `_topic_path`'s own
    # `def` as a shadow and took `pre_plan_gates.py` from 28 resolved calls to
    # ZERO, while this assertion stayed green on the fixtures alone.
    defining = "pre_plan_gates.py"
    assert per_file.get(defining, 0) > 0, (
        f"the module that DEFINES both functions resolved no calls at all "
        f"({defining}: {per_file.get(defining, 0)}). The sweep has gone blind "
        f"to its own subject; per-file counts: {per_file}"
    )
    assert offenders == [], (
        "these call sites still pass identity halves POSITIONALLY, which is "
        "the transposition A5 exists to make inexpressible:\n  "
        + "\n  ".join(offenders)
    )


def test_the_walk_detects_an_aliased_positional_call():
    """The alias arm is non-vacuous — pinned on a fixture, not on the tree.

    Without this, the alias handling could silently stop working and the
    invariant above would go quiet again, which is the exact regression that
    let `test_auto_register.py:255` through the first sweep.
    """
    src = (
        "real = ppg._write_topic_state\n"
        "def spy(a, b, c):\n"
        "    return real(a, b, c)\n"
    )
    offenders = _positional_calls_in(ast.parse(src), "<fixture>")
    assert len(offenders) == 1 and "real()" in offenders[0], offenders

    # ...and the keyword form of the same alias is NOT flagged.
    ok = (
        "real = ppg._write_topic_state\n"
        "def spy(*, a, b, c):\n"
        "    return real(topic_slug=a, project_slug=b, state=c)\n"
    )
    assert _positional_calls_in(ast.parse(ok), "<fixture>") == []


def test_the_walk_does_not_flag_an_unrelated_alias_of_the_same_name():
    """The false-positive direction, pinned because it actually happened.

    A file-level alias map let the WATCHED binding `real = ppg._topic_path` in
    one function taint a different function's unrelated `real(...)`, and
    reported `test_phase_rollback.py:349` — where `real` is bound to
    `_archive_topic_state` — as an offender. The tainting binding was the
    watched one; line 349 was the victim. Scope-chain resolution is what fixes
    it, so the fix needs its own guard.
    """
    src = (
        "def outer_watched():\n"
        "    real = ppg._topic_path\n"
        "    return real(a, b)\n"
        "def outer_unrelated():\n"
        "    real = ppg._archive_topic_state\n"
        "    return real(path)\n"
    )
    offenders = _positional_calls_in(ast.parse(src), "<fixture>")
    assert len(offenders) == 1, offenders
    assert ":3:" in offenders[0], (
        "only the watched alias should be flagged; the unrelated one at line 6 "
        f"must not be. Got: {offenders}"
    )

    # An inner rebind to something unwatched SHADOWS the outer watched binding.
    shadowed = (
        "real = ppg._topic_path\n"
        "def inner():\n"
        "    real = ppg._archive_topic_state\n"
        "    return real(path)\n"
    )
    assert _positional_calls_in(ast.parse(shadowed), "<fixture>") == []


def test_a_scope_nested_inside_a_compound_statement_is_isolated():
    """The scope boundary holds for an INDIRECT child, not just a direct one.

    `ast.walk` prunes nothing, so the first version of this walker leaked the
    body of a `def` nested under an `if` into the enclosing scope. All three
    measured symptoms are pinned here; the earlier guards used only top-level
    `def`s and were structurally blind to this layout.
    """
    # (1) an unwatched rebind inside an if-nested def must NOT leak upward and
    #     silence the enclosing genuine call.
    leak_up = (
        "def outer():\n"
        "    real = ppg._topic_path\n"
        "    if cond:\n"
        "        def inner():\n"
        "            real = ppg._archive_topic_state\n"
        "            return real(p)\n"
        "    return real(a, b)\n"
    )
    offenders = _positional_calls_in(ast.parse(leak_up), "<fixture>")
    assert len(offenders) == 1 and ":7:" in offenders[0], offenders

    # (2) a watched bind inside an if-nested def must NOT taint the enclosing
    #     innocent call -- and must be reported exactly ONCE, not duplicated.
    taint_up = (
        "def outer():\n"
        "    real = ppg._archive_topic_state\n"
        "    if cond:\n"
        "        def inner():\n"
        "            real = ppg._topic_path\n"
        "            return real(a, b)\n"
        "    return real(p)\n"
    )
    offenders = _positional_calls_in(ast.parse(taint_up), "<fixture>")
    assert len(offenders) == 1 and ":6:" in offenders[0], offenders


def test_a_rebind_takes_effect_only_for_calls_that_follow_it():
    """Source order matters; a later rebind must not silence an earlier call."""
    src = (
        "def outer():\n"
        "    real = ppg._topic_path\n"
        "    real(a, b)\n"
        "    real = ppg._archive_topic_state\n"
        "    real(p)\n"
    )
    offenders = _positional_calls_in(ast.parse(src), "<fixture>")
    assert len(offenders) == 1 and ":3:" in offenders[0], offenders


def test_annotated_and_tuple_bindings_are_resolved():
    """Two binding shapes the docstring claims are RESOLVED — checked, not assumed."""
    annotated = (
        "real: Callable = ppg._write_topic_state\n"
        "real(a, b, c)\n"
    )
    assert len(_positional_calls_in(ast.parse(annotated), "<fixture>")) == 1

    unpacked = (
        "first, second = ppg._topic_path, other\n"
        "first(a, b)\n"
        "second(a, b)\n"
    )
    offenders = _positional_calls_in(ast.parse(unpacked), "<fixture>")
    assert len(offenders) == 1 and ":2:" in offenders[0], offenders


def test_any_unresolved_binding_shadows_rather_than_false_positives():
    """Rule 2, across every binding mechanism named in the docstring.

    Each fixture rebinds a watched alias name by a form Rule 1 does NOT resolve.
    The correct answer in every case is silence: the walk must report LESS on an
    unrecognised shape, never accuse correct code. A parameter named `real` is
    the realistic one — a spy taking the function as an argument.
    """
    shadowing_forms = {
        "parameter": "def f(real):\n    return real(a, b)\n",
        "kwonly parameter": "def f(*, real):\n    return real(a, b)\n",
        "vararg": "def f(*real):\n    return real(a, b)\n",
        "lambda parameter": "g = lambda real: real(a, b)\n",
        "for target": "for real in fns:\n    real(a, b)\n",
        "with target": "with ctx() as real:\n    real(a, b)\n",
        "except target": "try:\n    pass\nexcept E as real:\n    real(a, b)\n",
        "comprehension target": "xs = [real(a, b) for real in fns]\n",
        "star target": "*real, last = items\nreal(a, b)\n",
        "non-literal unpack": "real, = get()\nreal(a, b)\n",
        "import as": "from m import other as real\nreal(a, b)\n",
        "nested def of same name": "def real():\n    pass\nreal(a, b)\n",
        "unresolvable rhs": "real = wrap(ppg._topic_path)\nreal(a, b)\n",
        "conditional rhs": "real = a if c else ppg._topic_path\nreal(a, b)\n",
        # `del` leaves a NameError behind, so a flag here accuses code that
        # cannot run. `match` capture patterns are handled too, but cannot be
        # tested here: they parse only on 3.10+ and the gate runs on 3.9.
        "del": "del real\nreal(a, b)\n",
    }
    for label, body in shadowing_forms.items():
        src = "real = ppg._topic_path\n" + body
        offenders = _positional_calls_in(ast.parse(src), "<fixture>")
        assert offenders == [], f"{label} should shadow, but flagged: {offenders}"

    # Control: without a shadowing binding, the same call IS flagged — so the
    # assertions above cannot be passing merely because nothing is ever flagged.
    control = "real = ppg._topic_path\ndef f(other):\n    return real(a, b)\n"
    assert len(_positional_calls_in(ast.parse(control), "<fixture>")) == 1


def test_resolution_does_not_leak_across_receivers_or_class_bodies():
    """Three false-positive shapes found in round 4, each pinned.

    All three had the walk resolving MORE broadly than Rule 1 licenses, which is
    the direction that accuses correct code.
    """
    # An attribute call whose `.attr` merely collides with an alias name is a
    # call on a different object entirely.
    src = "real = ppg._topic_path\nunrelated.real(a, b)\n"
    assert _positional_calls_in(ast.parse(src), "<fixture>") == []

    # A class body is not a closure link -- a method referencing a class-body
    # name raises NameError, so it cannot be a call to the aliased function.
    src = (
        "class C:\n"
        "    real = ppg._topic_path\n"
        "    def m(self):\n"
        "        return real(a, b)\n"
    )
    assert _positional_calls_in(ast.parse(src), "<fixture>") == []

    # A parameter shadowing one of the two LITERAL names must shadow it too --
    # the literal arm previously bypassed the shadow map.
    src = "def f(_topic_path):\n    return _topic_path(a, b)\n"
    assert _positional_calls_in(ast.parse(src), "<fixture>") == []

    # Controls: the legitimate forms of each are still flagged.
    assert len(_positional_calls_in(
        ast.parse("ppg._topic_path(a, b)\n"), "<fixture>")) == 1
    assert len(_positional_calls_in(
        ast.parse("real = ppg._topic_path\nreal(a, b)\n"), "<fixture>")) == 1


def test_a_bare_annotation_does_not_unbind():
    """`real: Callable` binds nothing in Python, so it must not shadow."""
    src = (
        "real = ppg._topic_path\n"
        "real: Callable\n"
        "real(a, b)\n"
    )
    assert len(_positional_calls_in(ast.parse(src), "<fixture>")) == 1


def test_a_walrus_binding_is_resolved():
    """The one RESOLVED claim that previously had no test of its own."""
    src = "(real := ppg._topic_path)\nreal(a, b)\n"
    assert len(_positional_calls_in(ast.parse(src), "<fixture>")) == 1


def test_the_declared_holes_are_really_holes():
    """The NOT-RESOLVED list is accurate — an unflagged shape stays unflagged.

    A docstring that over-states coverage is the defect class this file has
    already paid for twice. These assert the boundary is where it is claimed to
    be, so a future widening that quietly closes one of them fails here and the
    prose gets updated with it.
    """
    partial = (
        "import functools\n"
        "bound = functools.partial(ppg._topic_path, t, p)\n"
        "bound()\n"
    )
    assert _positional_calls_in(ast.parse(partial), "<fixture>") == []

    # getattr with a LITERAL name is missed too -- the right-hand side is a Call.
    getattr_literal = (
        "func = getattr(ppg, '_topic_path')\n"
        "func(a, b)\n"
    )
    assert _positional_calls_in(ast.parse(getattr_literal), "<fixture>") == []


@pytest.mark.parametrize("fname", KEYWORD_ONLY)
def test_a_legacy_positional_call_raises_type_error(fname, tmp_path, monkeypatch):
    """The signature itself refuses the old form — not merely convention.

    This is what makes the invariant above enforceable rather than advisory: a
    caller written against the pre-A5 signature fails loudly at the call, not
    silently with the two halves swapped.

    Pre-A5 the arities were exactly 2 and 3 positional, which is what is passed
    here — so before A5 both calls SUCCEEDED, and the `TypeError` can only come
    from the keyword-only marker. It cannot pass for a wrong-arity reason.

    `TOPIC_STATE_DIR` is redirected even though the call must raise before
    writing: if the refusal ever regressed, the fallback would write a real
    record, and it should land in a tmp dir rather than in the state dir this
    plan is measuring.
    """
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", tmp_path)
    func = getattr(ppg, fname)
    with pytest.raises(TypeError):
        if fname == "_write_topic_state":
            func("widget-topic", "Root", {})
        else:
            func("widget-topic", "Root")


@pytest.mark.parametrize("fname", KEYWORD_ONLY)
def test_the_keyword_form_still_works(fname, tmp_path, monkeypatch):
    """The positive direction, so the pair above cannot be satisfied by a
    function that simply refuses everything — the failure mode that let an
    unrealizable guard survive eight review rounds elsewhere in this plan."""
    monkeypatch.setattr(ppg, "TOPIC_STATE_DIR", tmp_path)
    if fname == "_topic_path":
        got = ppg._topic_path(topic_slug="widget-topic", project_slug="Root")
        assert got == tmp_path / "widget-topic__Root.json"
    else:
        ppg._write_topic_state(
            topic_slug="widget-topic", project_slug="Root", state={"k": "v"},
        )
        # The halves land in the declared order: <topic>__<project>.json.
        assert (tmp_path / "widget-topic__Root.json").exists()
        assert not (tmp_path / "Root__widget-topic.json").exists()

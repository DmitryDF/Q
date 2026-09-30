"""One definition site for the source-scanning half of a structural guard.

Several guards in this suite assert that a module's CODE does not contain a
token — a provider path, a mount test — while allowing that module's PROSE to
discuss the very thing it must not do. The design-A31 guard on
``adapters/document_folder.py`` is the original; ``content_shape.py`` mirrors it.

Both were written as::

    code_only = "\\n".join(l for l in src.splitlines()
                           if not l.strip().startswith("#"))
    body = code_only.split('\"\"\"')[-1]        # after the module docstring

The comment says "after the module docstring". The expression returns the text
after the **last** triple quote in the file, so on any module with more than one
docstring it returns a trailing fragment instead. Measured when this was found:
it left **3 of 232 lines** of ``document_folder.py`` and **9 of 190** of
``content_shape.py`` — in both cases everything the guard exists to inspect had
already been sliced away. The guards passed because they were reading almost
nothing.

:func:`code_without_docstrings` replaces the idiom. It parses the module and
drops exactly the docstring nodes, so the whole body is scanned and prose in any
docstring is still exempt. Repairing it did **not** change either verdict — both
modules pass with the full body scanned, which is why this is a repair to a
guard rather than a change to what the guards permit.

Lives here rather than in ``conftest.py`` because that file's own docstring
declares a deliberately narrow scope (``sys.path`` pinning for two shared engine
modules), and rather than in either test file because two copies of a guard is
how the first one silently rotted.
"""

from __future__ import annotations

import ast

__all__ = ["code_without_docstrings"]


def code_without_docstrings(src: str) -> str:
    """Every line of ``src`` that is neither a comment nor part of a docstring.

    Returns the remaining lines joined by newlines, so a caller can ask
    ``token not in code_without_docstrings(src)`` and mean it.
    """
    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            doc_lines.update(
                range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return "\n".join(
        line for n, line in enumerate(src.splitlines(), 1)
        if n not in doc_lines and not line.strip().startswith("#"))

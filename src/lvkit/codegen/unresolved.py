"""Soft-mode codegen helper for unresolved primitives and vi.lib VIs.

When `CodeGenContext.soft_unresolved=True`, the codegen does NOT raise on
unknown primitives or vi.lib VIs at build time. Instead it emits an inline
`raise <SameException>(<same kwargs>)` AST statement in the generated
Python — using the very same exception classes (`PrimitiveResolutionNeeded`,
`VILibResolutionNeeded`) that hard mode would have raised at codegen time.

This module is the single place where that pattern lives. Both
`nodes/primitive.py` and `nodes/subvi.py` call into it so the two paths
stay consistent.

Output binding contract
-----------------------

The helper pre-binds each output terminal of the unresolved node to
``None`` *before* the inline ``raise``. This satisfies the codegen
dataflow contract: downstream operations that consume these outputs see
a defined name during AST construction, and Python parses the result
without unbound-name errors. At *runtime* the ``raise`` fires before
the consumer ever reads the value.

If a downstream LLM rewrites the soft-mode `raise` into a real
implementation, it must also replace the `output = None` pre-binding —
otherwise consumers will see ``None`` instead of a real value.
"""

from __future__ import annotations

import ast

from lvkit.models import Operation

from .ast_utils import build_assign, to_var_name
from .context import CodeGenContext
from .fragment import CodeFragment


def emit_soft_unresolved(
    node: Operation,
    ctx: CodeGenContext,
    exception_module: str,
    exception_class: str,
    positional_args: list[object] | None = None,
    literal_kwargs: dict[str, object] | None = None,
    source_kwargs: dict[str, str] | None = None,
    extra_imports: set[str] | None = None,
) -> CodeFragment:
    """Build a CodeFragment that pre-binds outputs and raises inline.

    Used by both primitive and vi.lib soft-mode codegen paths to keep the
    pattern in one place.

    Args:
        node: The unresolved Operation. Output terminals are pre-bound to
            ``None`` so downstream codegen still sees a value.
        ctx: Active CodeGenContext. Used for output variable allocation
            and terminal binding.
        exception_module: Dotted module path of the exception class
            (e.g. ``"lvkit.primitive_resolver"``). Added to imports.
        exception_class: Name of the exception class to raise
            (e.g. ``"PrimitiveResolutionNeeded"``).
        positional_args: Positional arguments for the exception. Each
            must be a JSON-shaped literal (None/bool/int/float/str/list/
            tuple/dict/nested). They are serialized via ``repr()``.
        literal_kwargs: Keyword arguments whose values are JSON-shaped
            literals. Same serialization as ``positional_args``.
        source_kwargs: Keyword arguments whose values are pre-formatted
            Python source expressions (e.g.
            ``{"context": "ResolutionContext(caller_vi='Foo')"}``). Used
            for non-literal values like dataclass constructors.
        extra_imports: Additional import strings (e.g. for the dataclass
            referenced in ``source_kwargs``).

    Returns:
        CodeFragment whose statements are: pre-bind assignments followed
        by a single ``raise <Exception>(...)`` statement. Imports include
        the exception class and any extras.
    """
    pos = list(positional_args or [])
    lkw = dict(literal_kwargs or {})
    skw = dict(source_kwargs or {})

    # Defensive: literal values must be JSON-shaped so repr() round-trips
    # into valid source. Non-literal expressions go through source_kwargs.
    for i, v in enumerate(pos):
        _check_json_safe(f"positional[{i}]", v)
    for k, v in lkw.items():
        _check_json_safe(k, v)

    statements: list[ast.stmt] = []

    # Pre-bind output terminals to None so downstream dataflow has a
    # defined name to read. The runtime raise will fire before any
    # consumer actually reads the value. Operation.Terminal.direction
    # is always "input" or "output" (the parser-side "out"/"in" form
    # only appears on resolver-side PrimitiveTerminal/VITerminal).
    for term in node.terminals:
        if term.direction != "output":
            continue
        var_name = ctx.make_output_var(
            to_var_name(term.name or f"out_{term.index}"),
            node.id,
            terminal_id=term.id,
        )
        ctx.bind(term.id, var_name)
        statements.append(build_assign(var_name, ast.Constant(value=None)))

    # Build `raise <Exception>(<positional>, <literal kw>, <source kw>)`.
    parts: list[str] = []
    parts.extend(repr(v) for v in pos)
    parts.extend(f"{k}={v!r}" for k, v in lkw.items())
    parts.extend(f"{k}={v}" for k, v in skw.items())
    raise_src = f"raise {exception_class}({', '.join(parts)})"
    statements.append(ast.parse(raise_src).body[0])

    imports = {f"from {exception_module} import {exception_class}"}
    if extra_imports:
        imports |= extra_imports

    return CodeFragment(statements=statements, imports=imports)


_JSON_SAFE_TYPES = (type(None), bool, int, float, str, list, tuple, dict)


def _check_json_safe(label: str, value: object) -> None:
    """Assert that ``value`` is a JSON-shaped literal.

    Repr-then-parse only round-trips for builtin literal types. If a
    custom object slips in, the generated source will be invalid Python.
    Catching this at codegen time is much easier than debugging a
    SyntaxError in the output.
    """
    if not _is_json_safe(value):
        raise TypeError(
            f"emit_soft_unresolved {label} has unsupported type"
            f" {type(value).__name__}; positional_args and literal_kwargs"
            f" only accept None/bool/int/float/str/list/tuple/dict"
            f" (recursively). For non-literal expressions like dataclass"
            f" constructors, use source_kwargs instead."
        )


def _is_json_safe(value: object) -> bool:
    """Recursive check for JSON-safe literals."""
    if not isinstance(value, _JSON_SAFE_TYPES):
        return False
    if isinstance(value, list | tuple):
        return all(_is_json_safe(v) for v in value)
    if isinstance(value, dict):
        return all(
            isinstance(k, str) and _is_json_safe(v)
            for k, v in value.items()
        )
    return True

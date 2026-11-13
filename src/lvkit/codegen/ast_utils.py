"""AST building utilities for code generation."""

from __future__ import annotations

import ast
import builtins
import keyword
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from lvkit.graph.core import uid_of
from lvkit.lv_type_codes import (
    COMPLEX_TYPE_NAMES,
    INTEGER_TYPE_NAMES,
    REAL_FLOAT_TYPE_NAMES,
    is_boolean_type_name,
    is_string_type_name,
)
from lvkit.models import (
    ClusterField,
    LVType,
    LVTypeKind,
    ScalarValue,
    Terminal,
    TypeNamer,
    _is_error_cluster,
)
from lvkit.path_value import python_path
from lvkit.timestamp_value import EPOCH_1904_EXPRESSION
from lvkit.vi_files import strip_vi_extension

from .errors import CodeGenError
from .errors_module import LABVIEW_ERROR_NAME
from .label_names import python_label


def type_default_expr(lv_type: LVType | None) -> ast.expr:
    """The default value of a LabVIEW type, as LabVIEW gives it: what a skipped
    node's output, an unwired output tunnel, a shift register with nothing wired
    to it and every other "default of the type" takes.

    A number is 0, a Boolean False, a string ``""``, an enum or ring its first
    item, a path the empty path, a timestamp 1904-01-01 00:00:00 UTC, an array
    ``[]``; a named cluster is a
    ``SimpleNamespace`` of its fields' defaults and an anonymous one a tuple of
    them (the forms cluster constants take); an error cluster is ``None`` (no
    error). A type with no Python value (a refnum, a variant) is ``None``, and so
    is a class here: the class's default object needs the class imported, which
    only a function with a ``CodeGenContext`` can do (``type_default`` of
    ``type_default.py``, used for every default written in a function body).
    This one gives what a ``def`` parameter and a module-level variable start at
    (``state_seed``). The module that holds the expression imports what it names
    (``implicit_imports``)."""
    if lv_type is None:
        return ast.Constant(value=None)
    kind = lv_type.kind
    underlying = lv_type.underlying_type
    if kind == LVTypeKind.ARRAY:
        return ast.List(elts=[], ctx=ast.Load())
    if kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF):
        return cluster_default_expr(lv_type, type_default_expr)
    if kind == LVTypeKind.PRIMITIVE:
        if underlying == "Path":
            return parse_expr("Path('')")
        if lv_type.is_timestamp:
            return parse_expr(EPOCH_1904_EXPRESSION)
        return _scalar_default(underlying)
    if kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return ast.Constant(value=0)
    return ast.Constant(value=None)


def mark_default_object(call: ast.expr) -> ast.expr:
    """``call`` (a class's default object, ``Cls()``), marked as a type's default
    value, which the optimizer drops when it is overwritten at once as it does a
    constant default (``is_default_object``)."""
    setattr(call, _DEFAULT_OBJECT_MARK, True)
    return call


def is_default_object(expression: ast.expr) -> bool:
    """Whether ``expression`` was made by ``mark_default_object``."""
    return getattr(expression, _DEFAULT_OBJECT_MARK, False) is True


_DEFAULT_OBJECT_MARK = "lvkit_default_object"


def parameter_default_expr_of_type(lv_type: LVType | None) -> ast.expr:
    """The default a parameter of a LabVIEW type takes in a ``def`` when the
    control saves none: the type's default for a number, Boolean, string, enum
    or ring, and ``None`` for anything else (a ``def`` default is evaluated once,
    so a list or object there would be shared by every call; the function makes
    the array or class object for ``None`` in its body). A timestamp and a path
    are the type's default: a ``hightime.datetime`` and a ``pathlib.Path`` cannot
    change, so every call shares one safely, and ``None`` stays Not a Path."""
    if lv_type is None:
        return ast.Constant(value=None)
    if lv_type.kind == LVTypeKind.PRIMITIVE:
        if lv_type.is_timestamp:
            return parse_expr(EPOCH_1904_EXPRESSION)
        if lv_type.underlying_type == "Path":
            return parse_expr("Path('')")
        return _scalar_default(lv_type.underlying_type)
    if lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return ast.Constant(value=0)
    return ast.Constant(value=None)


def _scalar_default(underlying: str | None) -> ast.Constant:
    if is_string_type_name(underlying):
        return ast.Constant(value="")
    if is_boolean_type_name(underlying):
        return ast.Constant(value=False)
    if underlying in INTEGER_TYPE_NAMES:
        return ast.Constant(value=0)
    if underlying in REAL_FLOAT_TYPE_NAMES:
        return ast.Constant(value=0.0)
    if underlying in COMPLEX_TYPE_NAMES:
        return ast.Constant(value=0j)
    return ast.Constant(value=None)


def cluster_default_expr(
    lv_type: LVType, field_default: Callable[[LVType | None], ast.expr]
) -> ast.expr:
    """The default of a cluster: ``field_default`` of each field's type, in the
    form a cluster constant takes (``type_default_expr`` for the plain form)."""
    if _is_error_cluster(lv_type) or lv_type.classname or not lv_type.fields:
        return ast.Constant(value=None)
    values = [field_default(f.type) for f in lv_type.fields]
    if not lv_type.typedef_name:
        return ast.Tuple(elts=values, ctx=ast.Load())
    names = field_attribute_names(f.name for f in lv_type.fields)
    return ast.Call(
        func=ast.Name(id="SimpleNamespace", ctx=ast.Load()),
        args=[],
        keywords=[
            ast.keyword(arg=name, value=value)
            for name, value in zip(names, values, strict=True)
        ],
    )


# The names a generated module spells bare and imports only when it uses them.
_IMPLICIT_IMPORTS = {
    "Callable": "from collections.abc import Callable",
    "Enum": "from enum import Enum",
    "UTC": "from datetime import UTC",
    "hightime": "import hightime",
    "os": "import os",
    "Path": "from pathlib import Path",
    "SimpleNamespace": "from types import SimpleNamespace",
}


def implicit_imports(statements: Iterable[ast.stmt]) -> list[str]:
    """The imports of the bare names lvkit writes into generated code without
    importing them where it writes them (``os`` of a path volume, ``Path`` and
    ``SimpleNamespace`` of a type's default): one per name the statements load.
    A generated variable or function never takes one of these names
    (``to_var_name``)."""
    used = {
        node.id
        for stmt in statements
        for node in ast.walk(stmt)
        if isinstance(node, ast.Name) and node.id in _IMPLICIT_IMPORTS
    }
    return [_IMPLICIT_IMPORTS[name] for name in sorted(used)]


def parse_expr(template: str) -> ast.expr:
    """Parse a string template into an AST expression.

    Args:
        template: Python expression as string

    Returns:
        AST expression node
    """
    try:
        tree = ast.parse(template, mode="eval")
        return tree.body
    except SyntaxError as e:
        raise CodeGenError(f"Invalid expression template: {template!r} — {e}") from e


def parse_stmt(template: str) -> ast.stmt:
    """Parse a string template into an AST statement.

    Args:
        template: Python statement as string

    Returns:
        AST statement node
    """
    try:
        tree = ast.parse(template, mode="exec")
        if tree.body:
            return tree.body[0]
        return ast.Pass()
    except SyntaxError as e:
        raise CodeGenError(f"Invalid statement template: {template!r} — {e}") from e


def build_assign(target: str, value: ast.expr) -> ast.Assign:
    """Build an assignment statement: target = value."""
    return ast.Assign(
        targets=[ast.Name(id=target, ctx=ast.Store())],
        value=value,
    )


def build_assign_attr(owner: str, attr: str, value: ast.expr) -> ast.stmt:
    """``owner.attr = value``."""
    return ast.Assign(
        targets=[
            ast.Attribute(
                value=ast.Name(id=owner, ctx=ast.Load()), attr=attr, ctx=ast.Store()
            )
        ],
        value=value,
    )


def build_assign_from_str(target: str, value_expr: str) -> ast.Assign:
    """Build an assignment from string expression."""
    return build_assign(target, parse_expr(value_expr))


def build_multi_assign(targets: list[str], value: ast.expr) -> ast.Assign:
    """Build a tuple unpacking assignment: a, b, c = value."""
    if len(targets) == 1:
        return build_assign(targets[0], value)

    return ast.Assign(
        targets=[
            ast.Tuple(
                elts=[ast.Name(id=t, ctx=ast.Store()) for t in targets],
                ctx=ast.Store(),
            )
        ],
        value=value,
    )


def build_call(
    func: str, args: list[str], keywords: dict[str, str] | None = None
) -> ast.Call:
    """Build a function call: func(args..., **keywords)."""
    return ast.Call(
        func=ast.Name(id=func, ctx=ast.Load()),
        args=[ast.Name(id=a, ctx=ast.Load()) for a in args],
        keywords=[
            ast.keyword(arg=k, value=ast.Name(id=v, ctx=ast.Load()))
            for k, v in (keywords or {}).items()
        ],
    )


def build_method_call(obj: str, method: str, args: list[str]) -> ast.Call:
    """Build a method call: obj.method(args...)."""
    return ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=obj, ctx=ast.Load()),
            attr=method,
            ctx=ast.Load(),
        ),
        args=[ast.Name(id=a, ctx=ast.Load()) for a in args],
        keywords=[],
    )


def build_attr_access(obj: str, attr: str) -> ast.Attribute:
    """Build attribute access: obj.attr."""
    return ast.Attribute(
        value=ast.Name(id=obj, ctx=ast.Load()),
        attr=attr,
        ctx=ast.Load(),
    )


def build_name(name: str) -> ast.Name:
    """Build a name reference."""
    return ast.Name(id=name, ctx=ast.Load())


def build_constant(value: ScalarValue) -> ast.Constant:
    """Build a constant value."""
    return ast.Constant(value=value)


def substitute_names(node: ast.expr, mapping: dict[str, str]) -> ast.expr:
    """Replace Name nodes with mapped variable names.

    Args:
        node: AST expression to transform
        mapping: name → replacement mapping

    Returns:
        Transformed AST expression
    """

    class NameReplacer(ast.NodeTransformer):
        def visit_Name(self, node: ast.Name) -> ast.AST:
            name_lower = node.id.lower()
            # Try exact match first
            if node.id in mapping:
                return ast.Name(id=mapping[node.id], ctx=node.ctx)
            # Try case-insensitive match
            for key, value in mapping.items():
                if key.lower() == name_lower:
                    return ast.Name(id=value, ctx=node.ctx)
            return node

    return NameReplacer().visit(node)


def substitute_in_template(template: str, mapping: dict[str, str]) -> ast.expr:
    """Parse template and substitute variable names.

    Args:
        template: Python expression template
        mapping: variable name → replacement value

    Returns:
        AST expression with substitutions applied
    """
    expr = parse_expr(template)
    return substitute_names(expr, mapping)


def build_return(values: list[str], result_class: str | None = None) -> ast.Return:
    """Build a return statement.

    Args:
        values: Variable names to return
        result_class: If provided, wraps in result_class(var=value, ...)

    Returns:
        Return statement AST
    """
    if not values:
        return ast.Return(value=None)

    if len(values) == 1 and not result_class:
        return ast.Return(value=ast.Name(id=values[0], ctx=ast.Load()))

    if result_class:
        # Return ResultClass(field1=val1, field2=val2, ...)
        return ast.Return(
            value=ast.Call(
                func=ast.Name(id=result_class, ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(arg=v, value=ast.Name(id=v, ctx=ast.Load()))
                    for v in values
                ],
            )
        )

    # Return tuple
    return ast.Return(
        value=ast.Tuple(
            elts=[ast.Name(id=v, ctx=ast.Load()) for v in values],
            ctx=ast.Load(),
        )
    )


def to_var_name(name: str) -> str:
    """Convert a name to a valid Python variable name."""
    if not name:
        return "var"

    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    if result and not result[0].isalpha() and result[0] != "_":
        result = "var_" + result
    if not result:
        result = "var"
    # Avoid shadowing Python keywords, builtins and the modules the generated
    # code imports (e.g. an output named "sum" must not become a `sum` variable
    # that then breaks `sum(...)`, nor one named "os" hide `os.name`).
    if keyword.iskeyword(result) or result in RESERVED_NAMES:
        result = result + "_"
    return result


def result_field_name(name: str) -> str:
    """A VI output's field name on its result class. A label with a leading
    underscore gets an ``output`` prefix (the name stays public). The result
    class and every caller reading the field share this one definition."""
    name = to_var_name(name or "output")
    return "output" + name if name.startswith("_") else name


def unique_names(names: list[str]) -> list[str]:
    """``names`` made pairwise distinct, in order: a repeat gets ``_2``, ``_3``…
    (the first holder of a name keeps it)."""
    used: set[str] = set()
    result: list[str] = []
    for name in names:
        candidate, n = name, 1
        while candidate in used:
            n += 1
            candidate = f"{name}_{n}"
        used.add(candidate)
        result.append(candidate)
    return result


def field_attribute_names(field_names: Iterable[str]) -> list[str]:
    """The Python attribute name of each field of one cluster, given the fields'
    labels: the fields whose labels become one name ("Count" and "count") each
    get their own."""
    return unique_names([to_var_name(name) for name in field_names])


def field_attribute_paths(fields: list[ClusterField]) -> list[list[str]]:
    """The attribute path of every field of a cluster hierarchy, depth first in
    the order of ``_flatten_fields`` (a sub-cluster is its own slot, followed by
    its fields): the names ``field_attribute_names`` gives at each level."""
    paths: list[list[str]] = []
    names = field_attribute_names(f.name for f in fields)
    for field, name in zip(fields, names, strict=True):
        paths.append([name])
        if field.type and field.type.fields:
            paths.extend(
                [name, *sub] for sub in field_attribute_paths(field.type.fields)
            )
    return paths


def parameter_names(terminals: list[Terminal]) -> dict[str, str]:
    """Terminal id -> the Python parameter name of each VI control, unique across
    ``terminals`` (connector-pane controls first, then off-pane ones), made from
    its ``python_label``. The function signature and the variable each control is
    bound to both take their name from here."""
    names = unique_names([to_var_name(python_label(t) or "input") for t in terminals])
    return {t.id: name for t, name in zip(terminals, names, strict=True)}


def result_field_names(outputs: list[Terminal]) -> dict[str, str]:
    """Terminal id -> result field name of each connector-pane indicator:
    ``result_field_name`` of its ``python_label`` (callers read them by it), made
    distinct when two labels give the same name."""
    names = unique_names(
        [result_field_name(python_label(t) or "output") for t in outputs]
    )
    return {t.id: name for t, name in zip(outputs, names, strict=True)}


def numbered_name(name: str, taken: Callable[[str], bool]) -> str:
    """``name_2``, ``name_3``…: the first that ``taken`` says is free."""
    n = 2
    while taken(f"{name}_{n}"):
        n += 1
    return f"{name}_{n}"


def distinct_names(taken: Iterable[str], wanted: Iterable[str]) -> list[str]:
    """The names ``wanted``, each made distinct from every name in ``taken`` and
    from the others (the first holder of a name keeps it)."""
    used = list(taken)
    result: list[str] = []
    for name in wanted:
        chosen = unique_names([*used, name])[-1]
        used.append(chosen)
        result.append(chosen)
    return result


@dataclass(frozen=True)
class InterfaceNames:
    """The Python names of a VI's interface: its parameters and result fields, by
    terminal id."""

    parameters: dict[str, str]
    fields: dict[str, str]


def interface_names(
    inputs: list[Terminal],
    off_pane_inputs: list[Terminal],
    outputs: list[Terminal],
) -> InterfaceNames:
    """The one place a VI's parameter names and result field names are made from
    its terminals, in interface order, each set distinct. The connector pane is
    the interface: ``parameters`` names its inputs, and also the local variable of
    each off-pane control (distinct from every parameter); ``fields`` names its
    outputs. The VI's own function and result class use them, and so does every
    caller that passes it keyword arguments and reads its result fields."""
    return InterfaceNames(
        parameters=parameter_names([*inputs, *off_pane_inputs]),
        fields=result_field_names(outputs),
    )


# Builtin names a generated variable must not shadow.
_PY_BUILTINS = frozenset(dir(builtins))

# Modules the generated code imports by a bare name (``import os``), which a
# generated variable or function must not shadow. Each name must match an
# ``import <name>`` some generated module can contain: the codegen's own imports
# and the ``imports`` of the primitive and vi.lib catalogs
# (``tests/test_reserved_module_names.py`` checks this).
GENERATED_MODULE_NAMES = frozenset(
    {
        "builtins", "concurrent", "copy", "ctypes", "filecmp", "fnmatch",
        "functools", "getpass", "hightime",
        "importlib", "inspect", "io", "ipaddress", "keyword", "math", "nidaqmx",
        "nidcpower", "nidigital", "nidmm", "nifgen", "niscope", "niswitch",
        "nptdms", "numpy", "os", "pathlib", "pickle", "pyvisa", "re", "serial",
        "shutil", "socket", "stat", "statistics",
        "struct", "subprocess", "sys", "tempfile", "threading", "time", "uuid",
        "zipfile",
    }
)  # fmt: skip

# The keyword-only parameter of a VI that touches its front panel while it runs
# (``panel_hooks``): no variable of the function takes its name.
LIVE_IO = "live_io"

# The wanted name of the variable that holds the queue of events of a VI with an
# Event Structure (``panel_hooks.entry_events``); numbered when the VI already has
# the name, so it is not reserved.
EVENTS = "events"

RESERVED_NAMES = _PY_BUILTINS | GENERATED_MODULE_NAMES | {LIVE_IO}


def node_owned_name(prefix: str, function_name: str, node_id: str) -> str:
    """The name of module-level code one node of a VI owns: an uninitialized
    shift register's global (codegen/nodes/loop.py), First Call?'s flag
    (codegen/nodes/first_call.py), a Formula Node's function
    (codegen/nodes/formula.py), the value an indicator keeps between calls
    (codegen/local_indicators.py).

    ``<prefix>_<function>_<uid>``: the VI's function name, unchanged, and the
    node's or terminal's uid within its VI (``node_id`` is ``<vi path>::<uid>``,
    see ``InMemoryVIGraph._qid``), each run of characters that cannot be in a
    name made one ``_``. A module's functions have names of their own, so the
    name is unique in a module that holds several VIs (the variants of a
    polymorphic VI), and it is the same wherever the project sits on disk.
    """
    uid = re.sub(r"\W+", "_", uid_of(node_id))
    return f"{prefix}_{function_name}_{uid}"


def to_function_name(vi_name: str) -> str:
    """Convert VI name to Python function name."""
    # Strip class/library prefix (e.g., "TestCase.lvclass:X.vi" → "X")
    if ":" in vi_name:
        vi_name = vi_name.split(":")[-1]
    name = strip_vi_extension(vi_name)
    return to_var_name(name)


def class_style_name(vi_name: str) -> str:
    """The VI's function name in class style (``get_settings`` is
    ``GetSettings``)."""
    parts = to_function_name(vi_name).split("_")
    return "".join(p.capitalize() for p in parts if p)


def build_result_class_name(vi_name: str) -> str:
    """Build result class name from VI name."""
    return f"{class_style_name(vi_name)}Result"


RESULT_CLASS_IMPORT = "from dataclasses import dataclass"


def terminal_annotation(term: Terminal, spell: TypeNamer | None) -> str:
    """The annotation of a parameter or result field of the terminal ``term``: an
    error cluster is ``LabVIEWError | None`` (``None`` is no error), any other
    type ``Terminal.python_type``."""
    if term.is_error_cluster:
        return f"{LABVIEW_ERROR_NAME} | None"
    return term.python_type(spell)


def result_class_def(
    class_name: str, fields: list[tuple[str, ast.expr]]
) -> ast.ClassDef:
    """The class a VI's or method's results are returned as: a frozen
    ``dataclass`` with one annotated field per connector-pane output, read by
    name (``fields``: name and annotation). The module imports
    ``RESULT_CLASS_IMPORT``."""
    return ast.ClassDef(
        name=class_name,
        bases=[],
        keywords=[],
        body=[
            ast.AnnAssign(
                target=ast.Name(id=name, ctx=ast.Store()),
                annotation=annotation,
                simple=1,
            )
            for name, annotation in fields
        ]
        or [ast.Pass()],
        decorator_list=[
            ast.Call(
                func=ast.Name(id="dataclass", ctx=ast.Load()),
                args=[],
                keywords=[ast.keyword(arg="frozen", value=ast.Constant(value=True))],
            )
        ],
    )


def module_part(name: str) -> str:
    """One path part (a folder or a file stem) as the Python module or package
    name it is generated as: lower case, spaces and hyphens to ``_``, every other
    character that is not a letter, digit or ``_`` dropped, and ``var_`` in front
    of a part that does not start with a letter or ``_`` (a name that starts with
    a digit is a SyntaxError in ``from <name> import ...``), and ``_`` after a
    Python keyword (a folder named ``Class`` is the package ``class_``).

    Generated code that opens a VI by a path computed when it runs applies this
    same transform to each part, so the two always agree
    (``codegen/nodes/vi_reference.py`` ``MODULE_PART_SOURCE``)."""
    result = name.lower().replace(" ", "_").replace("-", "_")
    result = "".join(c for c in result if c.isalnum() or c == "_")
    if result and not result[0].isalpha() and result[0] != "_":
        result = "var_" + result
    if keyword.iskeyword(result):
        result += "_"
    return result or "module"


def package_root_name(stem: str) -> str:
    """The generated package's name: the input file's ``stem`` by ``module_part``,
    with ``_`` after a name Python already has as a top-level module (the
    standard library's, or a package generated code imports), so importing the
    package never hides that module (``Numbers.vi`` is the package ``numbers_``)."""
    name = module_part(stem)
    if name in sys.stdlib_module_names or name in GENERATED_MODULE_NAMES:
        name += "_"
    return name


def to_module_name(vi_name: str) -> str:
    """Convert VI name to Python module name (strips library prefix).

    Examples:
        "Get Settings Path.vi" -> "get_settings_path"
        "GraphicalTestRunner.lvlib:Run.vi" -> "run"
        "1D Boolean Array Changed.vi" -> "var_1d_boolean_array_changed"
    """
    if ":" in vi_name:
        vi_name = vi_name.split(":")[-1]
    return module_part(strip_vi_extension(vi_name))


def parameter_default_expr(inp: Terminal) -> ast.expr:
    """The default of a control as a ``def`` parameter: its saved default when
    that maps cleanly to its scalar type, else ``parameter_default_expr_of_type``."""
    saved = saved_default_expr(inp.default_value, inp.lv_type)
    return saved if saved is not None else parameter_default_expr_of_type(inp.lv_type)


def control_default_expr(inp: Terminal) -> ast.expr:
    """The value a control or indicator holds as a variable of the function (an
    off-pane control, an indicator a Local Variable reads): its saved default
    when that maps cleanly to its scalar type, else ``type_default_expr``."""
    saved = saved_default_expr(inp.default_value, inp.lv_type)
    return saved if saved is not None else type_default_expr(inp.lv_type)


def saved_default_expr(dv: ScalarValue, lv_type: LVType | None) -> ast.expr | None:
    """A control's saved default ``dv`` as its scalar type ``lv_type`` reads it (the
    generated function and the generated front panel both start a control from
    it); None when it saves none or one that is not a scalar of its type."""
    if dv is None:
        return None
    underlying = lv_type.underlying_type if lv_type else None
    try:
        if lv_type and lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            return ast.Constant(value=int(dv))
        if underlying and underlying.startswith(("NumInt", "NumUInt")):
            return ast.Constant(value=int(dv))
        if underlying in REAL_FLOAT_TYPE_NAMES:
            return ast.Constant(value=float(dv))
        if underlying == "Boolean":
            return ast.Constant(value=str(dv).strip().lower() in ("true", "1"))
        if underlying == "Path":
            expression = python_path(str(dv))
            if expression is not None:
                return parse_expr(expression)
        if underlying == "String":
            # A saved string default is stored as a Python string literal
            # (``'abc'``), so its value is what that literal evaluates to.
            value = ast.literal_eval(str(dv))
            if isinstance(value, str):
                return ast.Constant(value=value)
    except (ValueError, TypeError, SyntaxError):
        pass
    return None


def with_statements_after_docstring(
    statements: list[ast.stmt], body: list[ast.stmt]
) -> list[ast.stmt]:
    """``body`` with ``statements`` at its start, after the docstring if it has
    one."""
    first = body[0] if body else None
    has_docstring = (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )
    if has_docstring:
        return [*body[:1], *statements, *body[1:]]
    return [*statements, *body]


def build_type_checking_block(statements: set[str]) -> list[ast.stmt]:
    """``if TYPE_CHECKING:`` block importing the types the annotations name; empty
    when there are none."""
    if not statements:
        return []
    names = ast.parse("from typing import TYPE_CHECKING").body
    guarded = [stmt for imp in sorted(statements) for stmt in ast.parse(imp).body]
    block = ast.If(
        test=ast.Name(id="TYPE_CHECKING", ctx=ast.Load()), body=guarded, orelse=[]
    )
    return [*names, block]


def paren_if_compound(expr: str) -> str:
    """Wrap an inlined operand in parens if it is a compound expression.

    Operand substitution into a template is string-level, so an inlined
    sub-expression must keep its precedence: an upstream ``z | x`` dropped into
    ``in_1 & in_2`` has to stay ``(z | x) & …``, not collapse to ``z | x & …``
    (``&`` binds tighter than ``|``). Wrapping is always safe — redundant parens
    never change meaning — and also fixes ``~in_1`` / ``in_1[0]`` / etc."""
    try:
        node = ast.parse(expr, mode="eval").body
    except (SyntaxError, ValueError):
        return expr
    if isinstance(
        node,
        (
            ast.BoolOp,
            ast.BinOp,
            ast.UnaryOp,
            ast.Compare,
            ast.IfExp,
            ast.Lambda,
            ast.NamedExpr,
            ast.Await,
        ),
    ):
        return f"({expr})"
    return expr

# The import of the ``copy`` module, for the code that makes a one-level copy.
COPY_IMPORT = "import copy"

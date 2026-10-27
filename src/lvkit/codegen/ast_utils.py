"""AST building utilities for code generation."""

from __future__ import annotations

import ast
import builtins
import keyword
import logging

from lvkit.lv_type_codes import is_boolean_type_name, is_string_type_name
from lvkit.models import LVType, LVTypeKind, ScalarValue, Terminal
from lvkit.vi_files import strip_vi_extension

logger = logging.getLogger(__name__)


def default_value_expr(lv_type: LVType | None) -> ast.expr:
    """A conservative Python default value (as an AST expr) for a LabVIEW type.

    Scalars get their real default (int→0, float→0.0, bool→False, string→"",
    enum/ring→0); mutable/complex types (array, cluster, path, typedef) get
    ``None`` to stay import-free and avoid the mutable-default-argument
    anti-pattern. Used for connector-pane parameter defaults AND for
    pre-declaring case/sequence output tunnels that a frame may leave unwired
    (LabVIEW's "Use Default If Unwired" emits the type default, not None)."""
    if lv_type is None:
        return ast.Constant(value=None)
    kind = lv_type.kind
    underlying = lv_type.underlying_type
    if kind == LVTypeKind.ARRAY:
        return ast.Constant(value=None)
    if kind == LVTypeKind.PRIMITIVE:
        if is_string_type_name(underlying):
            return ast.Constant(value="")
        if is_boolean_type_name(underlying):
            return ast.Constant(value=False)
        if underlying in (
            "NumInt8",
            "NumInt16",
            "NumInt32",
            "NumInt64",
            "NumUInt8",
            "NumUInt16",
            "NumUInt32",
            "NumUInt64",
        ):
            return ast.Constant(value=0)
        if underlying in ("NumFloat32", "NumFloat64"):
            return ast.Constant(value=0.0)
    elif kind in (LVTypeKind.ENUM, LVTypeKind.RING):
        return ast.Constant(value=0)
    return ast.Constant(value=None)


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
        logger.warning("Invalid expression template: %r — %s", template, e)
        return ast.Constant(value=None)


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
        logger.warning("Invalid statement template: %r — %s", template, e)
        return ast.Expr(value=ast.Constant(value=None))


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
    # Avoid shadowing Python keywords and builtins (e.g. an output named
    # "sum" must not become a `sum` variable that then breaks `sum(...)`).
    if keyword.iskeyword(result) or result in _PY_BUILTINS:
        result = result + "_"
    return result


def result_field_name(name: str) -> str:
    """A VI output's field name on its NamedTuple result. NamedTuple rejects a
    leading underscore, so such labels get an ``output`` prefix. The result
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


def parameter_names(terminals: list[Terminal]) -> dict[str, str]:
    """Terminal id -> the Python parameter name of each non-error-cluster VI
    control, unique across ``terminals`` (connector-pane controls first, then
    off-pane ones). The function signature and the variable each control is
    bound to both take their name from here."""
    params = [t for t in terminals if not t.is_error_cluster]
    names = unique_names([to_var_name(t.name or "input") for t in params])
    return {t.id: name for t, name in zip(params, names, strict=True)}


def result_field_names(
    outputs: list[Terminal], off_pane_outputs: list[Terminal]
) -> dict[str, str]:
    """Terminal id -> result field name of each non-error-cluster indicator.
    Connector-pane fields keep ``result_field_name`` (callers read them by it);
    an off-pane indicator's field is made distinct from every earlier one."""
    pane = [t for t in outputs if not t.is_error_cluster]
    names = {t.id: result_field_name(t.name or "output") for t in pane}
    taken = list(names.values())
    for t in off_pane_outputs:
        if t.is_error_cluster:
            continue
        name = unique_names([*taken, result_field_name(t.name or "output")])[-1]
        taken.append(name)
        names[t.id] = name
    return names


# Builtin names a generated variable must not shadow.
_PY_BUILTINS = frozenset(dir(builtins))


def sanitize_state_var_suffix(uid: str) -> str:
    """Turn an arbitrary terminal/node uid into a valid identifier suffix.

    Used for module-level persistent-state variable names: uninitialized
    shift-register globals (see codegen/nodes/loop.py) and stateful
    primitives like First Call? (codegen/nodes/first_call.py). Graph uids
    are already qualified as ``"<vi_name>::<raw_uid>"`` (see
    ``InMemoryVIGraph._qid``), so the sanitized result stays unique and
    stable across regenerations of the same VI -- unlike ``to_var_name``,
    which strips non-alnum characters entirely, this replaces runs of them
    with a single ``_`` so the ``vi_name`` and ``raw_uid`` portions don't
    collapse into each other.
    """
    chars: list[str] = []
    prev_underscore = False
    for ch in uid:
        if ch.isalnum():
            chars.append(ch.lower())
            prev_underscore = False
        elif not prev_underscore:
            chars.append("_")
            prev_underscore = True
    ident = "".join(chars).strip("_")
    if not ident:
        ident = "x"
    if ident[0].isdigit():
        ident = "_" + ident
    return ident


def to_function_name(vi_name: str) -> str:
    """Convert VI name to Python function name."""
    # Strip class/library prefix (e.g., "TestCase.lvclass:X.vi" → "X")
    if ":" in vi_name:
        vi_name = vi_name.split(":")[-1]
    name = strip_vi_extension(vi_name)
    return to_var_name(name)


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


def param_default_expr(inp: Terminal) -> ast.expr:
    """Default-value AST for a parameter: the terminal's real connector-pane
    default when it maps cleanly to the scalar type, else the type default."""
    dv = inp.default_value
    if dv is None:
        return default_value_expr(inp.lv_type)
    underlying = inp.lv_type.underlying_type if inp.lv_type else None
    try:
        if inp.lv_type and inp.lv_type.kind in (LVTypeKind.ENUM, LVTypeKind.RING):
            return ast.Constant(value=int(dv))
        if underlying and underlying.startswith(("NumInt", "NumUInt")):
            return ast.Constant(value=int(dv))
        if underlying in ("NumFloat32", "NumFloat64"):
            return ast.Constant(value=float(dv))
        if underlying == "Boolean":
            return ast.Constant(value=str(dv).strip().lower() in ("true", "1"))
        if underlying == "String":
            # A saved string default is stored as a Python string literal
            # (``'abc'``), so its value is what that literal evaluates to.
            value = ast.literal_eval(str(dv))
            if isinstance(value, str):
                return ast.Constant(value=value)
    except (ValueError, TypeError, SyntaxError):
        pass
    return default_value_expr(inp.lv_type)


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

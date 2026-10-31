"""Copies for the values LabVIEW passes by value that Python passes by reference.

A class object or a named cluster (a typedef cluster) is a value in LabVIEW: when
its wire forks, each branch has its own. In Python the generated code changes
such an object where it is (Bundle By Name, an In Place Element Structure, a
setter, a method called on it, a VI that takes it). So a node that changes one
in place works on a copy when the graph says something else can still read the
value (``InMemoryVIGraph.value_is_shared``).

A class or typedef that holds a refnum (a queue, a Data Value Reference, a file,
a VI reference) gets a ``__deepcopy__`` that keeps sharing the refnum: a refnum is
a reference in LabVIEW too, and a queue cannot be copied."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.models import LVType, LVTypeKind, Terminal

from .ast_utils import build_assign, field_attribute_names, parse_expr, to_var_name

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode

    from .context import CodeGenContext

COPY_IMPORT = "import copy"


def is_by_value_object(lv_type: LVType | None) -> bool:
    """Whether a wire of this type carries an object that generated code changes
    in place: a class object, or a named cluster."""
    if lv_type is None:
        return False
    if lv_type.ref_type == "UDClassInst":
        return True
    return (
        lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.TYPEDEF_REF)
        and lv_type.typedef_name is not None
        and bool(lv_type.fields)
    )


def owned_input(
    ctx: CodeGenContext,
    term: Terminal,
    node: AnyGraphNode,
    statements: list[ast.stmt],
) -> str | None:
    """The expression feeding the input ``term`` of ``node`` when ``node`` changes
    the object in place: the wire's own value, or, when something else can still
    read that value, a deep copy made in ``statements`` first."""
    value = ctx.wired_input(term.id, node)
    if (
        value is None
        or ctx.graph is None
        or not is_by_value_object(term.lv_type)
        or not ctx.graph.value_is_shared(term.id)
    ):
        return value
    base = value if value.isidentifier() else "value"
    copy_var = ctx.make_output_var(f"{to_var_name(base)}_copy", node.id)
    ctx.add_import(COPY_IMPORT)
    statements.append(build_assign(copy_var, parse_expr(f"copy.deepcopy({value})")))
    return copy_var


def holds_refnum(lv_type: LVType | None) -> bool:
    """Whether a value of this type holds a refnum (other than a class object,
    which is a value): itself, the elements of its array, or a field."""
    if lv_type is None:
        return False
    if lv_type.ref_type == "UDClassInst":
        return False
    if lv_type.kind == LVTypeKind.PRIMITIVE and lv_type.underlying_type == "Refnum":
        return True
    if lv_type.kind == LVTypeKind.ARRAY:
        return holds_refnum(lv_type.element_type)
    if lv_type.kind == LVTypeKind.CLUSTER:
        return any(holds_refnum(f.type) for f in lv_type.fields or [])
    return False


def refnum_paths(lv_type: LVType | None, path: str) -> list[ast.stmt]:
    """The statements that put each refnum of the value at ``path`` into ``memo``
    (when the value is set; a field starts as ``None`` when its type has no
    literal default), see ``_refnum_statements``."""
    statements = _refnum_statements(lv_type, path)
    if not statements:
        return []
    return [ast.If(test=parse_expr(f"{path} is not None"), body=statements, orelse=[])]


def _refnum_statements(lv_type: LVType | None, path: str) -> list[ast.stmt]:
    """The statements that put each refnum of the value at the expression ``path``
    into ``memo`` as its own copy, so ``copy.deepcopy`` shares it: the refnum, each
    element of an array of refnums, the refnums of the fields of a cluster."""
    if lv_type is None or not holds_refnum(lv_type):
        return []
    if lv_type.kind == LVTypeKind.ARRAY:
        element = lv_type.element_type
        nested = element is not None and element.kind in (
            LVTypeKind.ARRAY,
            LVTypeKind.CLUSTER,
        )
        return [
            ast.For(
                target=ast.Name(id="item", ctx=ast.Store()),
                iter=parse_expr(path),
                body=refnum_paths(element, "item") if nested else [_memo_share("item")],
                orelse=[],
            )
        ]
    if lv_type.kind == LVTypeKind.CLUSTER:
        statements: list[ast.stmt] = []
        fields = lv_type.fields or []
        for field, name in zip(
            fields, field_attribute_names(f.name for f in fields), strict=True
        ):
            statements.extend(refnum_paths(field.type, f"{path}.{name}"))
        return statements
    return [_memo_share(path)]


def _memo_share(expression: str) -> ast.stmt:
    return ast.parse(f"memo[id({expression})] = {expression}").body[0]


def share_refnums_method(
    fields: list[tuple[str, LVType | None]], base_shares: bool
) -> list[ast.stmt]:
    """The methods of a class that holds refnums in the attributes ``fields``:
    ``_share_refnums`` puts them (and, with ``base_shares``, the class's base's
    through ``super()``) into ``memo``, and ``__deepcopy__`` (defined by the class
    nearest the root that holds refnums) deep-copies the rest."""
    body: list[ast.stmt] = []
    if base_shares:
        body.append(ast.parse("super()._share_refnums(memo)").body[0])
    for attribute, lv_type in fields:
        body.extend(refnum_paths(lv_type, f"self.{attribute}"))
    share = ast.parse("def _share_refnums(self, memo): pass").body[0]
    assert isinstance(share, ast.FunctionDef)
    share.body = body or [ast.Pass()]
    share.args.args[1].annotation = parse_expr("dict[int, object]")
    share.returns = parse_expr("None")
    statements: list[ast.stmt] = [share]
    if not base_shares:
        statements.append(_deepcopy_method())
    return statements


def _deepcopy_method() -> ast.stmt:
    source = (
        "def __deepcopy__(self, memo: dict[int, object]) -> object:\n"
        "    self._share_refnums(memo)\n"
        "    cls = type(self)\n"
        "    clone = cls.__new__(cls)\n"
        "    memo[id(self)] = clone\n"
        "    clone.__dict__.update(copy.deepcopy(self.__dict__, memo))\n"
        "    return clone\n"
    )
    return ast.parse(source).body[0]

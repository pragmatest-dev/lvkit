"""The Map and Set functions: Collection Size (2450), Empty Collection? (2451),
Look In Map (2458), Insert Into Map (2456), Insert Into Set (2452) and Remove From
Set (2453).

A LabVIEW Map is a Python ``dict`` and a Set a ``set``. A collection is a value in
LabVIEW: an insert or remove makes a new collection (``{**map, key: value}``,
``set | {element}``, ``set - {element}``) and leaves the one that came in as it
was, which is what ``set out`` / ``map out`` are NI's "the set with the inserted
element". A key or element is a number, Boolean, string or path (hashable); any
other type has no code generation yet.

NI ("Look In Map Function", "Insert Into Map Function", "Insert Into Set Function",
"Remove From Set Function", "Empty Collection? Function", "Collection Size
Function", labview-api-ref ``functions/``):

* Look In Map: "key not found? returns TRUE if map does not include an entry with
  the specified key"; "value returns the value of the entry with the associated key
  if key not found? is FALSE. If key not found? is TRUE, this output returns the
  value in default value"; an unwired ``default value`` is the default of the map's
  value type.
* Insert Into Map: "If the key is already included in the map, this function
  replaces the original value"; ``key already included?`` is whether it was.
  ``value unchanged?`` ("whether any values in the input map are updated after the
  insertion") has no NI statement for a key that is not in the map: wired, it is a
  gap.
* Insert Into Set: ``already included?`` is whether the element was in the set; "If
  already included? is TRUE, set out returns the same set as set in". Remove From
  Set: ``not included?`` is whether it was not; "If not included? is TRUE, set out
  returns the same set as set in".
* Collection Size is the number of elements or entries; Empty Collection? is TRUE
  for zero.

Terminals (from the primitives entries): Collection Size, Empty Collection?: in 1 =
collection; out 0. Look In Map: in 9 = default value, 10 = key, 11 = map; out 1 =
value, 2 = key not found?. Insert Into Map: in 9 = value, 10 = key, 11 = map in;
out 1 = value unchanged?, 2 = key already included?, 3 = map out. Insert Into Set
and Remove From Set: in 10 = element, 11 = set in; out 2 = already included? / not
included?, 3 = set out.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import PrimitiveNode
from lvkit.models import LVType, LVTypeKind
from lvkit.primitive_resolver import ResolvedPrimitive

from ...ast_utils import build_assign, parse_expr
from ...context import CodeGenContext
from ...fragment import CodeFragment
from ...type_default import type_default
from . import register_op, register_op_fragment
from ._flat_node import terminal
from ._regex_node import input_expression, is_wired_output, string_variable


@register_op("COLLECTION_SIZE")
def collection_size(node: PrimitiveNode, resolved: ResolvedPrimitive) -> dict[str, str]:
    return {"size": "len(in_1)"}


@register_op("EMPTY_COLLECTION")
def empty_collection(
    node: PrimitiveNode, resolved: ResolvedPrimitive
) -> dict[str, str]:
    return {"empty": "len(in_1) == 0"}


def _hashable(node: PrimitiveNode, index: int, what: str) -> None:
    """Raise a gap unless input ``index`` is a number, Boolean, string or path."""
    term = terminal(node, "input", index)
    lv_type = term.lv_type if term is not None else None
    if lv_type is None or lv_type.kind != LVTypeKind.PRIMITIVE:
        kind = "an unknown type" if lv_type is None else lv_type.kind.value
        raise ConversionGap(
            f"{node.name} with {what} of {kind} type has no code generation yet: "
            "only a number, Boolean, string or path can be a key or element",
            node,
        )


def _required(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    index: int,
    what: str,
    statements: list[ast.stmt],
) -> str:
    text = input_expression(ctx, node, index)
    if text is None:
        raise ConversionGap(f"{node.name} has no {what} wired", node)
    return string_variable(ctx, node, text, statements, what.replace(" ", "_"))


def _bind(
    ctx: CodeGenContext,
    node: PrimitiveNode,
    index: int,
    base: str,
    expression: str,
    statements: list[ast.stmt],
    bindings: dict[str, str],
) -> None:
    term = terminal(node, "output", index)
    if term is None or not is_wired_output(ctx, node, index):
        return
    name = ctx.make_output_var(base, node.id, terminal_id=term.id)
    statements.append(build_assign(name, parse_expr(expression)))
    bindings[term.id] = name


def _value_type(node: PrimitiveNode) -> LVType | None:
    term = terminal(node, "output", 1)
    return term.lv_type if term is not None else None


@register_op_fragment("LOOK_IN_MAP")
def look_in_map(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    _hashable(node, 10, "a key")
    statements: list[ast.stmt] = []
    collection = _required(ctx, node, 11, "map", statements)
    key = _required(ctx, node, 10, "key", statements)
    default = input_expression(ctx, node, 9)
    if default is None:
        default = ast.unparse(type_default(ctx, _value_type(node)))
    bindings: dict[str, str] = {}
    _bind(
        ctx,
        node,
        1,
        "value",
        f"{collection}.get({key}, {default})",
        statements,
        bindings,
    )
    _bind(
        ctx,
        node,
        2,
        "key_not_found",
        f"{key} not in {collection}",
        statements,
        bindings,
    )
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("INSERT_INTO_MAP")
def insert_into_map(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    _hashable(node, 10, "a key")
    if is_wired_output(ctx, node, 1):
        raise ConversionGap(
            "Insert Into Map with value unchanged? wired: NI does not say what it "
            "is for a key that is not in the map",
            node,
        )
    statements: list[ast.stmt] = []
    collection = _required(ctx, node, 11, "map in", statements)
    key = _required(ctx, node, 10, "key", statements)
    value = _required(ctx, node, 9, "value", statements)
    bindings: dict[str, str] = {}
    _bind(
        ctx,
        node,
        2,
        "key_already_included",
        f"{key} in {collection}",
        statements,
        bindings,
    )
    _bind(
        ctx,
        node,
        3,
        "map_out",
        f"{{**{collection}, {key}: {value}}}",
        statements,
        bindings,
    )
    return CodeFragment(statements=statements, bindings=bindings)


def _set_operation(
    node: PrimitiveNode, ctx: CodeGenContext, result: str, flag: str, flag_base: str
) -> CodeFragment:
    _hashable(node, 10, "an element")
    statements: list[ast.stmt] = []
    collection = _required(ctx, node, 11, "set in", statements)
    element = _required(ctx, node, 10, "element", statements)
    bindings: dict[str, str] = {}
    _bind(
        ctx,
        node,
        2,
        flag_base,
        flag.format(c=collection, e=element),
        statements,
        bindings,
    )
    _bind(
        ctx,
        node,
        3,
        "set_out",
        result.format(c=collection, e=element),
        statements,
        bindings,
    )
    return CodeFragment(statements=statements, bindings=bindings)


@register_op_fragment("INSERT_INTO_SET")
def insert_into_set(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    return _set_operation(node, ctx, "{c} | {{{e}}}", "{e} in {c}", "already_included")


@register_op_fragment("REMOVE_FROM_SET")
def remove_from_set(node: PrimitiveNode, ctx: CodeGenContext) -> CodeFragment | None:
    return _set_operation(node, ctx, "{c} - {{{e}}}", "{e} not in {c}", "not_included")

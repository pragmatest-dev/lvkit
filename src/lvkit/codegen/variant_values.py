"""Where a value becomes a ``Variant``: the type it carries and the wire that makes it.

A variant is made where a typed wire meets a Variant terminal (To Variant, or a
coercion dot into a Variant input, cluster field or indicator). The type it
records is that wire's type. ``variants.Variant`` and ``LVType`` are the package's
own classes (``variants_module``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.lv_type_codes import is_variant_type_name
from lvkit.models import LVType, LVTypeKind, Terminal

from .ast_utils import COPY_IMPORT
from .variants_module import VARIANT_NAME, lv_type_expression

if TYPE_CHECKING:
    from lvkit.graph.models import AnyGraphNode

    from .context import CodeGenContext


def is_variant_type(lv_type: LVType | None) -> bool:
    """Whether ``lv_type`` is a Variant."""
    return lv_type is not None and is_variant_type_name(lv_type.underlying_type)


def type_expression(ctx: CodeGenContext, lv_type: LVType) -> str:
    """The expression of the package's ``LVType`` for ``lv_type`` (a constant of
    ``variants`` or ``LVType(...)`` written out), its names imported."""
    expression = lv_type_expression(lv_type)
    ctx.import_variant(*expression.names)
    return expression.text


def is_array_or_cluster(lv_type: LVType) -> bool:
    return lv_type.kind in (
        LVTypeKind.ARRAY,
        LVTypeKind.CLUSTER,
        LVTypeKind.TYPEDEF_REF,
    )


def as_variant(ctx: CodeGenContext, expr: str, source_type: LVType) -> str:
    """``Variant(<expr>, <its type>)``: ``expr`` copied one level first when it is
    an array or a cluster, as To Variant copies its data."""
    type_expr = type_expression(ctx, source_type)
    value = expr
    if is_array_or_cluster(source_type):
        ctx.add_import(COPY_IMPORT)
        value = f"copy.copy({expr})"
    return f"{VARIANT_NAME}({value}, {type_expr})"


def _source_type(
    ctx: CodeGenContext, terminal_id: str, source_id: str | None
) -> LVType | None:
    """The type of the wire feeding the terminal (``source_id``, else the one wired
    to it); None when nothing known is wired."""
    if ctx.graph is None:
        return None
    if source_id is None:
        sources = ctx.graph.incoming_edges(terminal_id)
        if not sources:
            return None
        source_id = sources[0].terminal_id
    source = ctx.graph.get_terminal(source_id)
    return source.lv_type if source is not None else None


def _coerced_type(
    ctx: CodeGenContext, terminal_id: str, source_id: str | None
) -> LVType | None:
    """The type of the typed wire a Variant terminal is fed by; None when the
    terminal is not a Variant or its wire is a Variant or has no known type."""
    if ctx.graph is None:
        return None
    terminal = ctx.graph.get_terminal(terminal_id)
    if terminal is None or not is_variant_type(terminal.lv_type):
        return None
    source_type = _source_type(ctx, terminal_id, source_id)
    if source_type is None or is_variant_type(source_type):
        return None
    return source_type


def is_coerced(
    ctx: CodeGenContext, terminal_id: str, source_id: str | None = None
) -> bool:
    """Whether the terminal is a Variant fed by a typed wire: the generated code
    then builds a new ``Variant`` for it (``wrap_if_coerced``)."""
    return _coerced_type(ctx, terminal_id, source_id) is not None


def wrap_if_coerced(
    ctx: CodeGenContext, terminal_id: str, expr: str, source_id: str | None = None
) -> str:
    """``expr`` made a ``Variant`` when the terminal ``terminal_id`` is coerced
    (``is_coerced``; the wire is ``source_id``, else the one wired to the
    terminal). A gap constant (``None``) stays as it is."""
    if expr == "None":
        return expr
    source_type = _coerced_type(ctx, terminal_id, source_id)
    if source_type is None:
        return expr
    return as_variant(ctx, expr, source_type)


def variant_input(
    ctx: CodeGenContext, node: AnyGraphNode, term: Terminal | None
) -> str:
    """The ``Variant`` a node reads from the input ``term``: a new empty
    ``Variant`` when it is unwired, the wired value made a ``Variant`` when a typed
    wire meets it (``wired_input`` does so for a Variant terminal; a polymorphic
    input is made one here), else the wired ``Variant``."""
    value = ctx.wired_input(term.id, node) if term is not None else None
    if term is None or value is None:
        ctx.import_variant()
        return f"{VARIANT_NAME}()"
    if is_variant_type(term.lv_type):
        return value
    source_type = _source_type(ctx, term.id, None)
    if source_type is None or is_variant_type(source_type):
        return value
    return as_variant(ctx, value, source_type)

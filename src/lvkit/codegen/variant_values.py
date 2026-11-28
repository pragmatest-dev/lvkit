"""Where a value becomes a ``Variant``: the type it carries and the wire that makes it.

A variant is made where a typed wire meets a Variant terminal (To Variant, or a
coercion dot into a Variant input, cluster field or indicator). The type it
records is that wire's type. ``variants.Variant`` and ``LVType`` are the package's
own classes (``variants_module``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lvkit.graph.models import ConstantNode, PrimitiveNode, VINode
from lvkit.lv_type_codes import is_variant_type_name
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal

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


def variant_label(ctx: CodeGenContext, source: Terminal | None) -> str:
    """The label of the data a wire carries into a variant, which Get Type
    Information returns as its ``name`` and the variant's type string records: the
    label the wire's type descriptor records in the VI's type pool
    (``LVType.descriptor_label``), for every kind of source, a function's output
    included (Variant To Data's ``data`` shares its descriptor with the constant
    wired to its ``type``). Where the descriptor records none, the label the graph
    keeps for the source: a control or indicator (``FPTerminal.name``), the field
    label of a Bundle or Unbundle By Name lane (``Terminal.display_name``), a
    constant's ``label`` and a subVI output's ``Terminal.name`` (the label the
    calling VI records for the called VI's connector-pane slot). "" when neither
    records one."""
    if source is None:
        return ""
    if source.lv_type is not None and source.lv_type.descriptor_label:
        return source.lv_type.descriptor_label
    if isinstance(source, FPTerminal):
        return source.name or ""
    if source.nmux_role == "list":
        return source.display_name or ""
    if ctx.graph is None:
        return ""
    node = ctx.graph.get_graph_node(ctx.graph.terminal_node_id(source.id))
    if isinstance(node, ConstantNode):
        return node.label or ""
    if isinstance(node, VINode):
        return source.name or ""
    return ""


def as_variant(
    ctx: CodeGenContext, expr: str, source_type: LVType, label: str = ""
) -> str:
    """``Variant(<expr>, <its type>)``: ``expr`` copied one level first when it is
    an array or a cluster, as To Variant copies its data. A ``label`` (see
    ``variant_label``) is the variant's ``name``; the type string a variant is
    flattened with ends in it (``flat_writer``)."""
    type_expr = type_expression(ctx, source_type)
    value = expr
    if is_array_or_cluster(source_type):
        ctx.add_import(COPY_IMPORT)
        value = f"copy.copy({expr})"
    name = f", name={label!r}" if label else ""
    return f"{VARIANT_NAME}({value}, {type_expr}{name})"


def _source(
    ctx: CodeGenContext, terminal_id: str, source_id: str | None
) -> Terminal | None:
    """The terminal feeding the terminal (``source_id``, else the one wired to it);
    None when nothing known is wired."""
    if ctx.graph is None:
        return None
    if source_id is None:
        sources = ctx.graph.incoming_edges(terminal_id)
        if not sources:
            return None
        source_id = sources[0].terminal_id
    return ctx.graph.get_terminal(source_id)


def _adapts_to_variant(ctx: CodeGenContext, terminal: Terminal) -> bool:
    """Whether ``terminal`` is a terminal of a primitive that has the type of the
    node's other terminals of the same kind (``PrimitiveTerminal.shares_type``:
    Select's ``t``, ``f`` and result) and one of those is a Variant: a typed wire
    on this one meets a Variant, a coercion dot."""
    graph = ctx.graph
    if graph is None:
        return False
    node = graph.get_graph_node(graph.terminal_node_id(terminal.id))
    if not isinstance(node, PrimitiveNode) or node.prim_id is None:
        return False
    resolved = ctx.primitive_catalog.resolve(
        prim_id=node.prim_id,
        node_slots={(t.index, t.direction) for t in node.terminals},
    )
    if resolved is None:
        return False
    sharing = {(t.index, t.direction) for t in resolved.terminals if t.shares_type}
    shared = [t for t in node.terminals if (t.index, t.direction) in sharing]
    return any(t.id == terminal.id for t in shared) and any(
        t.id != terminal.id and is_variant_type(t.lv_type) for t in shared
    )


def _coerced_source(
    ctx: CodeGenContext, terminal_id: str, source_id: str | None
) -> Terminal | None:
    """The terminal of the typed wire a Variant terminal is fed by; None when the
    terminal is not a Variant or its wire is a Variant or has no known type. A
    subVI call input is a Variant when the called VI's control there is (a call
    to an adapt-to-type input records the caller's own type on the terminal)."""
    if ctx.graph is None:
        return None
    terminal = ctx.graph.get_terminal(terminal_id)
    if terminal is None:
        return None
    if not is_variant_type(terminal.lv_type):
        control = ctx.graph.callee_pane_terminal(terminal_id)
        if (
            control is None or not is_variant_type(control.lv_type)
        ) and not _adapts_to_variant(ctx, terminal):
            return None
    source = _source(ctx, terminal_id, source_id)
    if source is None or source.lv_type is None or is_variant_type(source.lv_type):
        return None
    return source


def is_coerced(
    ctx: CodeGenContext, terminal_id: str, source_id: str | None = None
) -> bool:
    """Whether the terminal is a Variant fed by a typed wire: the generated code
    then builds a new ``Variant`` for it (``wrap_if_coerced``)."""
    return _coerced_source(ctx, terminal_id, source_id) is not None


def wrap_if_coerced(
    ctx: CodeGenContext, terminal_id: str, expr: str, source_id: str | None = None
) -> str:
    """``expr`` made a ``Variant`` when the terminal ``terminal_id`` is coerced
    (``is_coerced``; the wire is ``source_id``, else the one wired to the
    terminal). A gap constant (``None``) stays as it is."""
    if expr == "None":
        return expr
    source = _coerced_source(ctx, terminal_id, source_id)
    if source is None or source.lv_type is None:
        return expr
    return as_variant(ctx, expr, source.lv_type, variant_label(ctx, source))


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
    source = _source(ctx, term.id, None)
    if source is None or source.lv_type is None or is_variant_type(source.lv_type):
        return value
    return as_variant(ctx, value, source.lv_type, variant_label(ctx, source))

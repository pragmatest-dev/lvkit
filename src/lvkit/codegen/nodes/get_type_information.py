"""``Get Type Information.vi`` (NI_Data Type.lvlib, Data Type Parsing palette).

NI, "Get Type Information" (labview-api-ref, vi-lib/utility/data-type): ``type``
"returns the data type stored in variant", as the item of the ``Data Type`` enum
the output carries. A generated variant records its type-descriptor code
(``variants.LVType.code``), so the item is the one the table
``data/variant_type_items.json`` gives for that code, written into the call as a
dict literal of the codes the table lists, each with the value of that item in the
enum the caller's output holds. An empty variant holds no type: its item is Void,
the code 0 (the rule ``Get TDEnum from Data`` follows, variant-values.md). A code
the table does not list (a waveform, a refnum or class, which the code alone does
not tell apart from other items) raises ``NotImplementedError`` where the node runs.

``name`` "returns the name of the data type stored in variant" (NI's page). On NI's
forum it is "the label of the data" the variant was made from ("Get type info in
malleable VI returns VIM input control name", forums.ni.com td-p/4234698: "the name
of the data that gets embedded in the variant is taken from the calling VI"). The
generated ``Variant`` records it as ``Variant.name``, set where the variant is made
from the label the graph keeps for the wire's source (``variant_values.variant_label``:
a control or indicator, a Bundle or Unbundle By Name field, a constant); the type
string the variant is flattened with ends in the same label. A variant made from a
wire whose source records no label, an empty variant and one read from a flattened
string have the name "" (NI's page does not say what they return). ``type
definition`` and ``type definition info`` read the type definition's name the
variant's type records (``_type_definition_fragment``).
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.lv_type_codes import is_string_type_name
from lvkit.models import LVType, Terminal, TerminalDirection
from lvkit.variant_type_items import item_values_by_code
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import cluster_default_expr, parse_expr, type_default_expr
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .grpc_pane import terminal_of, wired


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of ``Get Type Information.vi``: the ``Data Type`` item of the
    variant's type-descriptor code, its name, and whether it is an instance of a
    type definition (with the definition's information)."""
    out = terminal_of(node, entry, "type", TerminalDirection.OUTPUT)
    name = terminal_of(node, entry, "name", TerminalDirection.OUTPUT)
    flag = terminal_of(node, entry, "type_definition", TerminalDirection.OUTPUT)
    info = terminal_of(node, entry, "type_definition_info", TerminalDirection.OUTPUT)
    fragment = CodeFragment.empty()
    if name is not None and ctx.is_wired(name.id):
        variant = wired(node, entry, "variant", ctx)
        if variant is None:
            raise ConversionGap("no variant is wired to it", node)
        name_var = ctx.make_output_var("name", node.id)
        fragment += CodeFragment(
            statements=ast.parse(f"{name_var} = {variant}.name\n").body,
            bindings={name.id: name_var},
        )
    if out is not None and ctx.is_wired(out.id):
        fragment += _type_fragment(node, ctx, entry, out)
    if (flag is not None and ctx.is_wired(flag.id)) or (
        info is not None and ctx.is_wired(info.id)
    ):
        fragment += _type_definition_fragment(node, ctx, entry, flag, info)
    return fragment


def _type_definition_fragment(
    node: VINode,
    ctx: CodeGenContext,
    entry: VIEntry,
    flag: Terminal | None,
    info: Terminal | None,
) -> CodeFragment:
    """The ``type definition`` and ``type definition info`` outputs. NI: "type
    definition returns TRUE when the data type stored in variant is an instance of
    a type definition", "type definition info returns the type definition
    information only if type definition is TRUE", its ``Name`` "returns the name of
    the type definition" and its ``Timestamp`` "returns the timestamp of the last
    edit to the .ctl file if the data type stored in Variant is an instance of a
    strict type definition. Otherwise, Timestamp returns 0". The variant's type
    records the definition's name (``variants.LVType.typedef_name``); an empty
    variant and a type that is not an instance of a definition give FALSE and an
    empty name. The edit time of the ``.ctl`` file is not in the VI, so the
    timestamp is the 0 NI gives for a type that is not a strict type definition."""
    variant = wired(node, entry, "variant", ctx)
    if variant is None:
        raise ConversionGap("no variant is wired to it", node)
    recorded = ctx.make_output_var("variant_type", node.id)
    source = (
        f"{recorded} = {variant}.type\n"
        f"{recorded} = {recorded}.typedef_name if {recorded} is not None else None\n"
    )
    bindings: dict[str, str] = {}
    if flag is not None and ctx.is_wired(flag.id):
        var = ctx.make_output_var("type_definition", node.id)
        source += f"{var} = {recorded} is not None\n"
        bindings[flag.id] = var
    if info is not None and ctx.is_wired(info.id):
        var = ctx.make_output_var("type_definition_info", node.id)
        source += f"{var} = {_info_cluster(node, info, recorded)}\n"
        bindings[info.id] = var
    return CodeFragment(statements=ast.parse(source).body, bindings=bindings)


def _info_cluster(node: VINode, info: Terminal, name: str) -> str:
    """The value of the ``type definition info`` cluster: its string field is the
    definition's name (empty when there is none), its Timestamp field the 0 default."""
    lv_type = info.lv_type
    fields = lv_type.fields if lv_type is not None and lv_type.fields else []
    kinds = [
        "name"
        if f.type is not None and is_string_type_name(f.type.underlying_type)
        else "timestamp"
        if f.type is not None and f.type.is_timestamp
        else ""
        for f in fields
    ]
    if lv_type is None or sorted(kinds) != ["name", "timestamp"]:
        raise ConversionGap(
            "the Type Definition Info output is not recorded as a cluster of a "
            "Name string and a Timestamp",
            node,
        )

    def value(field_type: LVType | None) -> ast.expr:
        if field_type is not None and field_type.is_timestamp:
            return type_default_expr(field_type)
        return parse_expr(f"{name} or ''")

    return ast.unparse(cluster_default_expr(lv_type, value))


def _type_fragment(
    node: VINode, ctx: CodeGenContext, entry: VIEntry, out: Terminal
) -> CodeFragment:
    """The ``Type`` output: the ``Data Type`` item of the variant's code."""
    items = item_values_by_code(out.lv_type) if out.lv_type is not None else None
    if items is None:
        raise ConversionGap(
            "the Type output is not recorded as the items of the "
            "Data Type enum, so the value of each type's item is not known",
            node,
        )
    variant = wired(node, entry, "variant", ctx)
    if variant is None:
        raise ConversionGap("no variant is wired to it", node)
    recorded = ctx.make_output_var("variant_type", node.id)
    result = ctx.make_output_var("type", node.id)
    table = "{" + ", ".join(f"{code}: {value}" for code, value in items.items()) + "}"
    source = (
        f"{recorded} = {variant}.type\n"
        f"{result} = {table}.get({recorded}.code if {recorded} is not None else 0)\n"
        f"if {result} is None:\n"
        f"    raise NotImplementedError(\n"
        f"        'Get Type Information: the type with descriptor code '\n"
        f"        f'{{{recorded}.code}} ({{{recorded}.name}}) is not one item '\n"
        f"        'of the Data Type enum by its code alone'\n"
        f"    )\n"
    )
    return CodeFragment(statements=ast.parse(source).body, bindings={out.id: result})

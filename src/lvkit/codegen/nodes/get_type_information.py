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
from lvkit.lv_type_codes import INTEGER_TYPE_NAMES, is_string_type_name
from lvkit.models import LVType, Terminal, TerminalDirection
from lvkit.variant_type_items import item_values_by_code
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import (
    cluster_default_expr,
    field_attribute_names,
    parse_expr,
    type_default_expr,
)
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
    empty name. The edit time of the ``.ctl`` file is recorded in no saved file
    (labview-binary-format.md), so the timestamp is the 0 NI gives for a type that
    is not a strict type definition, and for a strict one (the generated
    ``LVType.strict_typedef``, set where the variant is made, ``variant_values``)
    reading the Timestamp field raises ``NotImplementedError`` naming the
    definition. The info cluster of a strict definition is a ``SimpleNamespace``
    whose ``timestamp`` is a property that raises, so a read of the Name alone
    does not raise; a cluster with no field names (a tuple) raises where it is
    made."""
    variant = wired(node, entry, "variant", ctx)
    if variant is None:
        raise ConversionGap("no variant is wired to it", node)
    recorded = ctx.make_output_var("variant_type", node.id)
    strict = ctx.make_output_var("strict_typedef", node.id)
    source = (
        f"{recorded} = {variant}.type\n"
        f"{strict} = {recorded} is not None and {recorded}.strict_typedef\n"
        f"{recorded} = {recorded}.typedef_name if {recorded} is not None else None\n"
    )
    bindings: dict[str, str] = {}
    if flag is not None and ctx.is_wired(flag.id):
        var = ctx.make_output_var("type_definition", node.id)
        source += f"{var} = {recorded} is not None\n"
        bindings[flag.id] = var
    if info is not None and ctx.is_wired(info.id):
        var = ctx.make_output_var("type_definition_info", node.id)
        source += _info_statements(node, ctx, info, var, recorded, strict)
        bindings[info.id] = var
    return CodeFragment(statements=ast.parse(source).body, bindings=bindings)


def _info_statements(
    node: VINode, ctx: CodeGenContext, info: Terminal, var: str, name: str, strict: str
) -> str:
    """The statements that bind ``var`` to the ``type definition info`` cluster:
    the one of a type that is not a strict definition (Timestamp 0), and for a
    strict one the cluster whose Timestamp raises when read. The refusal's text is
    built at run time from the definition's name."""
    cluster, stamp = _info_cluster(node, info, name)
    plain = ast.unparse(cluster)
    refusal = (
        "NotImplementedError(f'Get Type Information Timestamp of the strict type "
        f"definition {{{name}!r}}: the .ctl\\'s last edit time is not recorded in "
        "any saved file')"
    )
    if not isinstance(cluster, ast.Call) or stamp is None:
        return f"if {strict}:\n    raise {refusal}\n{var} = {plain}\n"
    cls = ctx.make_output_var("strict_info", node.id)
    kept = ", ".join(
        f"{k.arg}={ast.unparse(k.value)}" for k in cluster.keywords if k.arg != stamp
    )
    return (
        f"if {strict}:\n"
        f"    class {cls}(SimpleNamespace):\n"
        f"        @property\n"
        f"        def {stamp}(self):\n"
        f"            raise {refusal}\n"
        f"\n"
        f"    {var} = {cls}({kept})\n"
        f"else:\n"
        f"    {var} = {plain}\n"
    )


def _field_role(field_type: LVType | None) -> str:
    """``name`` for a string field, ``timestamp`` for a time stamp or integer one."""
    if field_type is None:
        return ""
    if is_string_type_name(field_type.underlying_type):
        return "name"
    if field_type.is_timestamp or field_type.underlying_type in INTEGER_TYPE_NAMES:
        return "timestamp"
    return ""


def _info_cluster(
    node: VINode, info: Terminal, name: str
) -> tuple[ast.expr, str | None]:
    """The value of the ``type definition info`` cluster and the attribute name of
    its Timestamp field (None when the cluster has no attribute names): its string
    field is the definition's name (empty when there is none), its Timestamp field
    the 0 default (the pane records it as an unsigned integer; a timestamp type is
    also accepted)."""
    lv_type = info.lv_type
    fields = lv_type.fields if lv_type is not None and lv_type.fields else []
    kinds = [_field_role(f.type) for f in fields]
    if lv_type is None or sorted(kinds) != ["name", "timestamp"]:
        raise ConversionGap(
            "the Type Definition Info output is not recorded as a cluster of a "
            "Name string and a Timestamp",
            node,
        )

    def value(field_type: LVType | None) -> ast.expr:
        if _field_role(field_type) == "timestamp":
            return type_default_expr(field_type)
        return parse_expr(f"{name} or ''")

    cluster = cluster_default_expr(lv_type, value)
    stamp = None
    if lv_type.typedef_name:
        names = field_attribute_names(f.name for f in fields)
        stamp = names[kinds.index("timestamp")]
    return cluster, stamp


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

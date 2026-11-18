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
from a control or indicator's wire (``variant_values.variant_label``); a variant
made from any other wire, an empty variant and one read from a flattened string
have the name "" (NI's page does not say what they return; the wire out of a node
carries no label we have read). ``type definition`` and ``type definition info``,
which a variant's type does not record, stay gaps.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.models import Terminal, TerminalDirection
from lvkit.variant_type_items import item_values_by_code
from lvkit.vilib_resolver import VIEntry

from ..context import CodeGenContext
from ..fragment import CodeFragment
from .grpc_pane import terminal_of, wired

_NOT_RECORDED = {
    "type_definition": "a variant does not record whether its type is a type "
    "definition",
    "type_definition_info": "a variant does not record the type definition's "
    "file or edit time",
}


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """One call of ``Get Type Information.vi``: the ``Data Type`` item of the
    variant's type-descriptor code."""
    for param, reason in _NOT_RECORDED.items():
        term = terminal_of(node, entry, param, TerminalDirection.OUTPUT)
        if term is not None and ctx.is_wired(term.id):
            raise ConversionGap(
                f"the {term.name or param} output is wired, and "
                f"{reason}",
                node,
            )
    out = terminal_of(node, entry, "type", TerminalDirection.OUTPUT)
    name = terminal_of(node, entry, "name", TerminalDirection.OUTPUT)
    if name is not None and ctx.is_wired(name.id):
        variant = wired(node, entry, "variant", ctx)
        if variant is None:
            raise ConversionGap("no variant is wired to it", node)
        name_var = ctx.make_output_var("name", node.id)
        fragment = CodeFragment(
            statements=ast.parse(f"{name_var} = {variant}.name\n").body,
            bindings={name.id: name_var},
        )
        if out is None or not ctx.is_wired(out.id):
            return fragment
        return fragment + _type_fragment(node, ctx, entry, out)
    if out is None or not ctx.is_wired(out.id):
        return CodeFragment.empty()
    return _type_fragment(node, ctx, entry, out)


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

"""Graph builders for OPERATION nodes (formula box, primitives, cpdArith,
property/invoke). Unlike structure builders, these consume the ordinary
``node_terminals`` + ``description`` built in the pre-dispatch block, so their
``build`` takes them as arguments. Bodies lifted verbatim from the old
_add_vi_to_graph fBox / else branches (byte-identical output).

Dispatch: NODE_BUILD_HANDLERS keyed by node_type, else DEFAULT_NODE_BUILD_HANDLER
(the generic primitive/operation handler — the "GenericHandler" of this stage).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from lvkit.models import PropertyDef, Terminal
from lvkit.parser.node_types import (
    CpdArithNode,
    InvokeNode,
    PropertyNode,
    SubVINode,
)
from lvkit.parser.node_types import (
    FormulaNode as ParserFormulaNode,
)
from lvkit.parser.node_types import (
    PrimitiveNode as ParserPrimitiveNode,
)

from ..models import AnyGraphNode, VINode
from ..models import FormulaNode as GraphFormulaNode
from ..models import PrimitiveNode as GraphPrimitiveNode
from .context import GraphBuildContext

# Node types that represent a SubVI call (dynamic or static). Single source of
# truth — construction.py imports this (it also drives name/description
# resolution and _connect_subvi_calls).
SUBVI_CALL_NODE_TYPES: frozenset[str] = frozenset(
    {
        "iUse",
        "polyIUse",
        "dynIUse",
        "callParentDynIUse",
        "callByRefNode",
    }
)


class NodeBuildHandler(ABC):
    """Builds one kind of operation graph node from a parsed node + its already
    built ordinary terminals."""

    @abstractmethod
    def build(
        self,
        node: Any,
        node_name: str | None,
        q_node_uid: str,
        node_terminals: list[Terminal],
        description: str | None,
        ctx: GraphBuildContext,
    ) -> AnyGraphNode: ...


class FormulaBuildHandler(NodeBuildHandler):
    def build(self, node, node_name, q_node_uid, node_terminals, description, ctx):
        # The "fBox" class is only ever parsed into a ParserFormulaNode; anything
        # else means the parser dispatch is broken — fail loud rather than
        # silently drop the script.
        if not isinstance(node, ParserFormulaNode):
            raise TypeError(
                f"fBox node {q_node_uid!r} is {type(node).__name__}, "
                "expected ParserFormulaNode (script would be lost)"
            )
        return GraphFormulaNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=node_terminals,
            description=description,
            script=node.script,
        )


class PrimitiveBuildHandler(NodeBuildHandler):
    """Default handler — primitive / cpdArith / property / invoke / generic."""

    def build(self, node, node_name, q_node_uid, node_terminals, description, ctx):
        prim_kwargs: dict[str, Any] = {}
        if isinstance(node, ParserPrimitiveNode):
            prim_kwargs["prim_id"] = node.prim_res_id
            prim_kwargs["prim_index"] = node.prim_index
        if isinstance(node, CpdArithNode):
            prim_kwargs["operation"] = node.operation
        if isinstance(node, (PropertyNode, InvokeNode)):
            prim_kwargs["object_name"] = node.object_name
            prim_kwargs["object_method_id"] = node.object_method_id
            if isinstance(node, PropertyNode):
                prim_kwargs["properties"] = [
                    PropertyDef(name=p.get("name", "")) if isinstance(p, dict) else p
                    for p in node.properties
                ]
                prim_kwargs["property_value_terminal_ids"] = [
                    ctx.qid(uid) for uid in node.dco_terminal_uids
                ]
            elif isinstance(node, InvokeNode):
                prim_kwargs["method_name"] = node.method_name
                prim_kwargs["method_code"] = node.method_code
                prim_kwargs["invoke_row_terminal_ids"] = [
                    ctx.qid(uid) for uid in node.row_terminal_uids
                ]

        return GraphPrimitiveNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=node_terminals,
            description=description,
            **prim_kwargs,
        )


class SubVIBuildHandler(NodeBuildHandler):
    """SubVI call (iUse/poly/dyn/callParent/callByRef) → VINode."""

    def build(self, node, node_name, q_node_uid, node_terminals, description, ctx):
        poly_variant = None
        if isinstance(node, SubVINode) and node.poly_variant_name:
            poly_variant = node.poly_variant_name
        # Persist the FULLY QUALIFIED callee name (e.g.
        # "TestCase.lvclass:CallTestMethod.vi"): prefer the graph-canonical key
        # when the callee is loaded, otherwise KEEP the qualified iUse name.
        # ``iuse_to_qname`` is parsed from the caller's OWN LIbd/BDHP (same
        # source as ``subvi_qualified_names``), so it is already class-qualified
        # and load-mode-independent. Dropping to the bare ``node_name`` here was
        # a bug: under a MINIMAL load the callee usually ISN'T in the graph, so
        # every call node came out bare ("CallTestMethod.vi") instead of
        # qualified ("TestCase.lvclass:CallTestMethod.vi") — which is exactly the
        # whole-repo state the facts index builds on. ``callee_q`` falls back to
        # ``node_name`` only when the iUse map has no entry, so this is never
        # None and never a downgrade.
        callee_q = ctx.iuse_to_qname.get(node.uid) or node_name
        resolved_q = ctx.resolve_vi_name(callee_q) if callee_q else None
        qualified_name = (
            resolved_q if resolved_q and resolved_q in ctx.graph else callee_q
        )
        # Dynamic-dispatch calls get their class-qualified target after type
        # propagation — see _resolve_dispatch_qnames.
        return VINode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            label=node.label,
            caption=node.caption,
            node_type=node.node_type,
            terminals=node_terminals,
            description=description,
            poly_variant_name=poly_variant,
            qualified_path=ctx.iuse_to_qpath.get(node.uid),
            qualified_name=qualified_name,
        )


_subvi_handler = SubVIBuildHandler()

NODE_BUILD_HANDLERS: dict[str, NodeBuildHandler] = {
    "fBox": FormulaBuildHandler(),
    **{nt: _subvi_handler for nt in SUBVI_CALL_NODE_TYPES},
}
DEFAULT_NODE_BUILD_HANDLER: NodeBuildHandler = PrimitiveBuildHandler()

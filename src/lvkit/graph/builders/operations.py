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
)
from lvkit.parser.node_types import (
    FormulaNode as ParserFormulaNode,
)
from lvkit.parser.node_types import (
    PrimitiveNode as ParserPrimitiveNode,
)

from ..models import AnyGraphNode
from ..models import FormulaNode as GraphFormulaNode
from ..models import PrimitiveNode as GraphPrimitiveNode
from .context import GraphBuildContext


class NodeBuildHandler(ABC):
    """Builds one kind of operation graph node from a parsed node + its already
    built ordinary terminals."""

    @abstractmethod
    def build(
        self, node: Any, node_name: str | None, q_node_uid: str,
        node_terminals: list[Terminal], description: str | None,
        ctx: GraphBuildContext,
    ) -> AnyGraphNode:
        ...


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
                    PropertyDef(name=p.get("name", ""))
                    if isinstance(p, dict) else p
                    for p in node.properties
                ]
            elif isinstance(node, InvokeNode):
                prim_kwargs["method_name"] = node.method_name
                prim_kwargs["method_code"] = node.method_code

        return GraphPrimitiveNode(
            id=q_node_uid,
            vi=ctx.vi_name,
            name=node_name,
            node_type=node.node_type,
            terminals=node_terminals,
            description=description,
            **prim_kwargs,
        )


NODE_BUILD_HANDLERS: dict[str, NodeBuildHandler] = {
    "fBox": FormulaBuildHandler(),
}
DEFAULT_NODE_BUILD_HANDLER: NodeBuildHandler = PrimitiveBuildHandler()

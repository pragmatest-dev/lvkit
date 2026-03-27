"""Code generator for Node Multiplexer (nMux) nodes."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

from vipy.graph_types import Operation

from ..ast_utils import build_assign, parse_expr, to_var_name
from ..fragment import CodeFragment
from .base import NodeCodeGen

if TYPE_CHECKING:
    from ..context import CodeGenContext


class NMuxCodeGen(NodeCodeGen):
    """Generate code for LabVIEW nMux (Node Multiplexer).

    nMux is a bundle/unbundle node at structure boundaries, NOT
    an indexed selector. Terminals are marked with roles:
    - agg: aggregate (cluster/object wire passthrough)
    - list: field values entering/exiting the cluster

    Currently generates passthrough bindings only. Actual
    bundle/unbundle expressions (cluster.field) need type info
    we don't yet have.
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        """Generate code for nMux (bundle/unbundle at structure boundaries).

        nMux terminals have roles from the parser:
        - agg: the cluster/object wire (passthrough)
        - list: field values being bundled into or unbundled from the cluster

        The codegen treats agg terminals as passthrough bindings and
        list terminals as the actual data values at the boundary.
        """
        agg_in = [t for t in node.terminals if t.direction == "input" and t.nmux_role == "agg"]
        agg_out = [t for t in node.terminals if t.direction == "output" and t.nmux_role == "agg"]
        list_in = [t for t in node.terminals if t.direction == "input" and t.nmux_role == "list"]
        list_out = [t for t in node.terminals if t.direction == "output" and t.nmux_role == "list"]

        # Fallback: if no roles marked, use old passthrough logic
        if not any(t.nmux_role for t in node.terminals):
            inputs = [t for t in node.terminals if t.direction == "input"]
            outputs = [t for t in node.terminals if t.direction == "output"]
            return self._passthrough(inputs, outputs, ctx)

        bindings: dict[str, str] = {}

        # AGG passthrough: bind agg outputs to agg input value
        agg_var = None
        if agg_in:
            agg_var = ctx.resolve(agg_in[0].id)
        for t in agg_out:
            if agg_var:
                bindings[t.id] = agg_var

        # LIST passthrough: bind list outputs to list input values
        # At structure boundaries, list terminals carry the actual
        # data values through. Pair them by position.
        for i, t in enumerate(sorted(list_out, key=lambda t: t.index)):
            if i < len(list_in):
                val = ctx.resolve(list_in[i].id)
                if val:
                    bindings[t.id] = val
            elif agg_var:
                # Single list output with no list input:
                # field extraction from the cluster. Derive field
                # name from downstream consumer's terminal name.
                field = self._derive_field_name(t, agg_var, ctx)
                bindings[t.id] = field

        # LIST inputs with AGG output (bundle): bind agg output
        # to first list input as passthrough
        if list_in and agg_out and not list_out:
            val = ctx.resolve(list_in[0].id)
            if val:
                for t in agg_out:
                    bindings[t.id] = val

        return CodeFragment(statements=[], bindings=bindings)

    @staticmethod
    def _derive_field_name(
        term: Any, agg_var: str, ctx: CodeGenContext,
    ) -> str:
        """Derive a field name for an nMux LIST output from downstream wiring.

        When the LIST output is a field extracted from the AGG cluster,
        the downstream consumer's terminal name often reveals the field name
        (e.g., wired to a SubVI input named "testmethodname").
        Falls back to agg_var if no name found.
        """
        if ctx.graph is not None:
            for dest in ctx.graph.outgoing_edges(term.id):
                if dest.name:
                    field = to_var_name(dest.name)
                    return f"{agg_var}.{field}"
        # Fallback: use terminal's own name if available
        if term.name:
            field = to_var_name(term.name)
            return f"{agg_var}.{field}"
        return agg_var

    @staticmethod
    def _passthrough(
        inputs: list, outputs: list, ctx: CodeGenContext,
    ) -> CodeFragment:
        """Fallback: bind all outputs to first input."""
        bindings: dict[str, str] = {}
        input_var = None
        if inputs:
            input_var = ctx.resolve(inputs[0].id)
        for out_term in outputs:
            bindings[out_term.id] = input_var or "None"
        return CodeFragment(statements=[], bindings=bindings)

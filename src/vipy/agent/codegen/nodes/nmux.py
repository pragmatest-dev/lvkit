"""Code generator for Node Multiplexer (nMux) nodes.

nMux is bundle/unbundle at structure boundaries. Field resolution
uses the <i> field index from XML + dep_graph class fields. No heuristics.
"""

from __future__ import annotations

import ast

from vipy.graph_types import ClusterField, Operation, Terminal, TypeResolutionNeeded

from ..ast_utils import parse_expr, to_var_name
from ..context import CodeGenContext
from ..fragment import CodeFragment
from .base import NodeCodeGen


class NMuxCodeGen(NodeCodeGen):
    """Generate code for LabVIEW nMux (bundle/unbundle at structure boundaries).

    Terminals have roles set by graph construction:
    - agg: the aggregate cluster/class wire (passthrough)
    - list: individual field values (each has nmux_field_index from <i> in XML)

    Field resolution: nmux_field_index → dep_graph fields[i].name.
    No string matching, no downstream guessing.
    """

    def generate(self, node: Operation, ctx: CodeGenContext) -> CodeFragment:
        def _by_role(direction: str, role: str) -> list[Terminal]:
            return [
                t for t in node.terminals
                if t.direction == direction and t.nmux_role == role
            ]

        agg_in = _by_role("input", "agg")
        agg_out = _by_role("output", "agg")
        list_in = _by_role("input", "list")
        list_out = _by_role("output", "list")

        bindings: dict[str, str] = {}
        statements: list[ast.stmt] = []

        # Resolve AGG input variable
        agg_var = None
        if agg_in:
            agg_var = ctx.resolve(agg_in[0].id)

        # AGG passthrough: bind agg outputs to agg input value
        for t in agg_out:
            if agg_var:
                bindings[t.id] = agg_var

        # Get fields for field index lookup.
        class_fields = None
        agg_terminals = agg_in or agg_out
        if agg_terminals and agg_terminals[0].lv_type:
            if ctx.graph is not None:
                class_fields = ctx.graph.get_type_fields(
                    agg_terminals[0].lv_type,
                )

        if list_in and list_out:
            # LIST in + LIST out = passthrough at structure boundary
            sorted_in = sorted(list_in, key=lambda t: t.index)
            sorted_out = sorted(list_out, key=lambda t: t.index)
            for i, t_out in enumerate(sorted_out):
                if i < len(sorted_in):
                    val = ctx.resolve(sorted_in[i].id)
                    if val:
                        bindings[t_out.id] = val

        elif list_out and not list_in:
            # LIST out only = unbundle (extract fields from cluster)
            for t in sorted(list_out, key=lambda t: t.index):
                if agg_var:
                    field_expr = self._field_expr(
                        t, agg_var, class_fields,
                    )
                    bindings[t.id] = field_expr

        elif list_in and not list_out:
            # LIST in only = bundle (assign fields on cluster)
            if agg_var:
                for t in sorted(list_in, key=lambda t: t.index):
                    val = ctx.resolve(t.id)
                    if not val:
                        continue
                    field_name = self._field_name(
                        t, class_fields,
                    )
                    if field_name:
                        stmt = ast.Assign(
                            targets=[ast.Attribute(
                                value=parse_expr(agg_var),
                                attr=field_name,
                                ctx=ast.Store(),
                            )],
                            value=parse_expr(val),
                        )
                        statements.append(stmt)
                    else:
                        raise TypeResolutionNeeded(
                            type_name=f"field[{t.nmux_field_index}]",
                            context=t.id,
                        )

        return CodeFragment(statements=statements, bindings=bindings)

    @staticmethod
    def _field_name(
        term: Terminal, class_fields: list[ClusterField] | None,
    ) -> str | None:
        """Get Python field name for a LIST terminal using nmux_field_index.

        Returns None if field index is missing or out of range.
        """
        if term.nmux_field_index is not None and class_fields:
            if term.nmux_field_index < len(class_fields):
                return to_var_name(class_fields[term.nmux_field_index].name)
        return None

    @staticmethod
    def _field_expr(
        term: Terminal, agg_var: str,
        class_fields: list[ClusterField] | None,
    ) -> str:
        """Resolve field expression for a LIST output terminal (unbundle).

        Uses nmux_field_index → dep_graph fields[i].name.
        Returns bare agg_var with warning if field cannot be resolved.
        """
        if term.nmux_field_index is not None and class_fields:
            if term.nmux_field_index < len(class_fields):
                field_name = to_var_name(class_fields[term.nmux_field_index].name)
                return f"{agg_var}.{field_name}"

        raise TypeResolutionNeeded(
            type_name=f"field[{term.nmux_field_index}]",
            context=term.id,
        )

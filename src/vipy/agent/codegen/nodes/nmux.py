"""Code generator for Node Multiplexer (nMux) nodes.

nMux is bundle/unbundle at structure boundaries. Field resolution
uses the <i> field index from XML + dep_graph class fields. No heuristics.
"""

from __future__ import annotations

import ast

from vipy.graph_types import (
    ClusterField,
    Operation,
    Terminal,
    TypeResolutionNeeded,
    _is_error_cluster,
)

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
            # If bundling status on an error cluster → raise instead
            if (
                agg_terminals
                and agg_terminals[0].lv_type
                and _is_error_cluster(agg_terminals[0].lv_type)
            ):
                # Error cluster bundle: raise if setting status,
                # skip otherwise (no error cluster object in
                # exception model)
                if self._bundles_status(list_in, class_fields):
                    return self._generate_error_bundle(
                        list_in, class_fields, ctx,
                    )
                return CodeFragment.empty()

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
    def _bundles_status(
        list_in: list[Terminal],
        class_fields: list[ClusterField] | None,
    ) -> bool:
        """Check if any LIST input maps to the 'status' field."""
        if not class_fields:
            return False
        for t in list_in:
            if t.nmux_field_index is not None:
                if t.nmux_field_index < len(class_fields):
                    if to_var_name(
                        class_fields[t.nmux_field_index].name,
                    ) == "status":
                        return True
        return False

    def _generate_error_bundle(
        self,
        list_in: list[Terminal],
        class_fields: list[ClusterField] | None,
        ctx: CodeGenContext,
    ) -> CodeFragment:
        """Generate conditional raise for error cluster bundle.

        Only called when status IS being bundled (checked by caller).
        Generates:
            if <status_val>:
                raise LabVIEWError(code=<code_val>, source=<source_val>)
        """
        field_values: dict[str, str] = {}
        for t in sorted(list_in, key=lambda t: t.index):
            val = ctx.resolve(t.id)
            if not val:
                continue
            field_name = self._field_name(t, class_fields)
            if field_name:
                field_values[field_name] = val

        status_val = field_values.get("status", "True")
        code_val = field_values.get("code", "0")
        source_val = field_values.get("source", "''")

        raise_stmt = ast.Raise(
            exc=ast.Call(
                func=ast.Name(id="LabVIEWError", ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(
                        arg="code", value=parse_expr(code_val),
                    ),
                    ast.keyword(
                        arg="source", value=parse_expr(source_val),
                    ),
                ],
            ),
            cause=None,
        )
        if_stmt = ast.If(
            test=parse_expr(status_val),
            body=[raise_stmt],
            orelse=[],
        )

        imports = {"from vipy.labview_error import LabVIEWError"}
        return CodeFragment(
            statements=[if_stmt], bindings={}, imports=imports,
        )

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

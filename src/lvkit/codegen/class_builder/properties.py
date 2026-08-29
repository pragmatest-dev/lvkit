"""Build @property accessors from LabVIEW getter/setter method pairs."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.structure import LVMethod

from ..ast_utils import to_var_name
from ._shared import _EMPTY_VI_CONTEXT

if TYPE_CHECKING:
    from lvkit.graph.models import VIContext


class _PropertyBuilderMixin:
    """Accessor complexity classification and @property generation."""

    if TYPE_CHECKING:
        # Populated on the composed ClassBuilder (core.py); declared here
        # (type-check only, zero runtime effect) so pyright can resolve
        # cross-mixin access.
        _method_contexts: dict[str, VIContext]

    def _is_simple_accessor(
        self,
        method: LVMethod,
    ) -> bool:
        """Check if accessor VI is simple (just unbundle/bundle + error handling).

        Simple accessors only contain case structures and unbundle/bundle operations.
        They translate to direct attribute access, not @property.

        Complex accessors contain additional operations (math, SubVI calls, validation).
        They need @property with backing field.

        Without VI context, we assume all accessors are simple. This can be
        refined when method_contexts is provided.
        """
        vi_context = self._method_contexts.get(method.name, _EMPTY_VI_CONTEXT)
        if vi_context is _EMPTY_VI_CONTEXT:
            # No VI context - assume simple accessor
            return True

        operations = vi_context.operations
        if not operations:
            # No operations - simple accessor
            return True

        # Check if all operations are just unbundle/bundle/case/error handling
        simple_node_types = {
            "select",
            "case",
            "unbundle",
            "bundle",
            "nMux",
            "nDmux",
            "mux",
            "demux",
        }
        # Error-handling primitives that keep an accessor "simple". Bundle /
        # Unbundle are node-CLASSES, already covered by simple_node_types
        # above -- they are not primResID prims and must not be listed here.
        # Clear Errors is a vi.lib VI (no primResID), so it cannot be
        # whitelisted by id. (This set was historically populated with stale
        # ids -- 1340/1302/2075/2076 actually map to One Button Dialog / Wait
        # (ms) / Destroy User Event / Unregister For Events -- so real Merge
        # Errors was never recognized here; corrected to 2401, then to 2147
        # once nodes.json's VI-Scripting export proved 2401 is really Swap
        # Values and 2147 is the real Merge Errors -- #59.)
        simple_prim_ids = {
            2147,  # Merge Errors
        }

        for op in operations:
            node_type = getattr(op, "node_type", "") or ""
            prim_id = getattr(op, "primResID", 0) or 0

            if node_type in simple_node_types:
                continue
            if prim_id in simple_prim_ids:
                continue

            # Found a non-simple operation
            return False

        return True

    def _build_properties(
        self,
        accessors: list[LVMethod],
    ) -> list[ast.stmt]:
        """Build @property and @setter from getter/setter pairs.

        For SIMPLE accessors (just unbundle/bundle + error handling):
        - Do NOT generate @property
        - The field is already created in __init__ with correct visibility
        - The accessor VI logic is just LabVIEW error handling idiom

        For COMPLEX accessors (have real logic):
        - Generate @property with private backing field (_field)
        - The property can contain computed logic
        """
        property_stmts: list[ast.stmt] = []

        # Group by field name
        by_field: dict[str, dict[str, LVMethod]] = {}
        for acc in accessors:
            if acc.accessor_field:
                field = acc.accessor_field
                if field not in by_field:
                    by_field[field] = {}
                if acc.accessor_type:
                    by_field[field][acc.accessor_type] = acc

        # Build property definitions only for complex accessors
        for field, acc_dict in by_field.items():
            getter = acc_dict.get("getter")
            setter = acc_dict.get("setter")

            # Check if any accessor is complex
            is_complex = False
            if getter and not self._is_simple_accessor(getter):
                is_complex = True
            if setter and not self._is_simple_accessor(setter):
                is_complex = True

            if not is_complex:
                # Simple accessors - skip property generation
                # Field visibility is already set correctly in __init__
                continue

            prop_name = to_var_name(field)
            # Complex accessor - always use private backing field
            backing_field = "_" + prop_name

            # Build getter
            if getter:
                getter_body: list[ast.stmt] = [
                    ast.Return(
                        value=ast.Attribute(
                            value=ast.Name(id="self", ctx=ast.Load()),
                            attr=backing_field,
                            ctx=ast.Load(),
                        )
                    )
                ]

                getter_def = ast.FunctionDef(
                    name=prop_name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[ast.arg(arg="self", annotation=None)],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=getter_body,
                    decorator_list=[ast.Name(id="property", ctx=ast.Load())],
                    returns=ast.Name(id="Any", ctx=ast.Load()),
                )
                property_stmts.append(getter_def)

            # Build setter
            if setter:
                setter_body: list[ast.stmt] = [
                    ast.Assign(
                        targets=[
                            ast.Attribute(
                                value=ast.Name(id="self", ctx=ast.Load()),
                                attr=backing_field,
                                ctx=ast.Store(),
                            )
                        ],
                        value=ast.Name(id="value", ctx=ast.Load()),
                    )
                ]

                setter_def = ast.FunctionDef(
                    name=prop_name,
                    args=ast.arguments(
                        posonlyargs=[],
                        args=[
                            ast.arg(arg="self", annotation=None),
                            ast.arg(
                                arg="value",
                                annotation=ast.Name(id="Any", ctx=ast.Load()),
                            ),
                        ],
                        vararg=None,
                        kwonlyargs=[],
                        kw_defaults=[],
                        kwarg=None,
                        defaults=[],
                    ),
                    body=setter_body,
                    decorator_list=[
                        ast.Attribute(
                            value=ast.Name(id=prop_name, ctx=ast.Load()),
                            attr="setter",
                            ctx=ast.Load(),
                        )
                    ],
                    returns=ast.Constant(value=None),
                )
                property_stmts.append(setter_def)

        return property_stmts

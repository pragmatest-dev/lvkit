"""Build static and instance methods from LabVIEW class methods."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.models import TypeNamer
from lvkit.structure import LVMethod

from ..ast_utils import interface_names, to_function_name
from ..builder import CodeGenContext, annotated_terminals, build_args, generate_body
from ..context import type_namer
from ..gaps import Gap, vi_body
from ..import_resolver import ImportResolver
from ._shared import _EMPTY_VI_CONTEXT

if TYPE_CHECKING:

    from lvkit.graph import InMemoryVIGraph
    from lvkit.graph.models import VIContext
    from lvkit.models import Terminal


class _MethodBuilderMixin:
    """Static-method and instance-method AST generation."""

    if TYPE_CHECKING:
        # Attributes populated on the composed ClassBuilder (see core.py) and
        # methods provided by sibling mixins (_TerminalClassifierMixin).
        # Declared here (type-check only, zero runtime effect) so pyright can
        # resolve cross-mixin access — same pattern as vilib_resolver's mixins.
        _method_contexts: dict[str, VIContext]
        _import_resolver: ImportResolver | None
        _graph: InMemoryVIGraph | None
        _gaps: list[Gap]
        _collected_imports: set[str]
        _collected_annotation_imports: set[str]

        def _is_self_input(self, inp: Terminal, class_name: str) -> bool: ...
        def _is_self_output(self, out: Terminal, class_name: str) -> bool: ...
        def _is_error_output(self, out: Terminal) -> bool: ...
        def _build_return_annotation(
            self, outputs: list[Terminal], spell: TypeNamer | None = None
        ) -> ast.expr: ...

    def _generate_method_body(
        self, vi_context: VIContext
    ) -> tuple[CodeGenContext, list[ast.stmt]]:
        """The context and statements of a method, from the method VI's
        top-level graph nodes."""
        key = vi_context.qualified_name or vi_context.name
        if self._graph is not None:
            key = self._graph.resolve_vi_name(key)
        ctx = CodeGenContext.from_vi_context(
            vi_context,
            graph=self._graph,
            import_resolver=self._import_resolver,
            vi_name=key,
            gaps=self._gaps,
        )

        def build() -> list[ast.stmt]:
            for term in annotated_terminals(vi_context):
                ctx.import_types(term.lv_type, at_run_time=False)
            operations = self._graph.top_level_nodes(key) if self._graph else []
            return [*ctx.entry_raises, *generate_body(operations, ctx)]

        body = vi_body(ctx, key, build)
        self._collected_imports.update(ctx.imports)
        self._collected_annotation_imports.update(ctx.annotation_imports)
        return ctx, body

    def _build_static_method(
        self,
        method: LVMethod,
        class_name: str,
    ) -> ast.FunctionDef:
        """Build a static method."""
        func_name = to_function_name(method.name)
        vi_context = self._method_contexts.get(method.name, _EMPTY_VI_CONTEXT)
        spell = type_namer(self._import_resolver)

        # Extract inputs/outputs from VI context
        inputs = vi_context.inputs
        outputs = vi_context.outputs

        # Filter out class instance input (even for "static" methods in LabVIEW)
        filtered_inputs = [
            inp for inp in inputs if not self._is_self_input(inp, class_name)
        ]

        # Use existing build_args() - handles types and error filtering
        args_obj = build_args(
            filtered_inputs,
            vi_context.off_pane_inputs,
            interface_names(inputs, vi_context.off_pane_inputs, [], []).parameters,
            spell,
        )

        ctx, body = self._generate_method_body(vi_context)

        # Ensure non-empty body
        if not body:
            body: list[ast.stmt] = [ast.Pass()]

        # Build return annotation - filter error clusters and class output
        filtered_outputs = [
            out
            for out in outputs
            if not self._is_error_output(out)
            and not self._is_self_output(out, class_name)
        ]

        returns = self._build_return_annotation(filtered_outputs, spell)

        return ast.FunctionDef(
            name=func_name,
            args=args_obj,
            body=body,
            decorator_list=[ast.Name(id="staticmethod", ctx=ast.Load())],
            returns=returns,
        )

    def _build_instance_method(
        self,
        method: LVMethod,
        class_name: str,
        prefix: str = "",
    ) -> ast.FunctionDef:
        """Build an instance method.

        Args:
            method: The LVMethod to convert
            class_name: Name of the containing class (for self parameter detection)
            prefix: Prefix for method name (e.g., "_" for protected, "__" for private)
        """
        func_name = prefix + to_function_name(method.name)
        vi_context = self._method_contexts.get(method.name, _EMPTY_VI_CONTEXT)
        spell = type_namer(self._import_resolver)

        # Extract inputs/outputs from VI context
        inputs = vi_context.inputs
        outputs = vi_context.outputs

        # Find the class instance input by TYPE (becomes self)
        instance_input = None
        for inp in inputs:
            if self._is_self_input(inp, class_name):
                instance_input = inp
                break

        # Filter out the class-typed input (becomes self)
        filtered_inputs = [
            inp for inp in inputs if not self._is_self_input(inp, class_name)
        ]

        # Use existing build_args() for proper types and error filtering
        args_obj = build_args(
            filtered_inputs,
            vi_context.off_pane_inputs,
            interface_names(inputs, vi_context.off_pane_inputs, [], []).parameters,
            spell,
        )

        # Prepend self
        args_obj.args.insert(0, ast.arg(arg="self", annotation=None))

        ctx, body = self._generate_method_body(vi_context)

        # Get instance variable name from context bindings (not from input name!)
        instance_var_name = None
        if instance_input and instance_input.id:
            instance_var_name = ctx.resolve(instance_input.id)

        # Transform instance variable references to self
        if instance_var_name:
            body = self._transform_instance_to_self(body, instance_var_name)

        # Ensure non-empty body
        if not body:
            body: list[ast.stmt] = [ast.Pass()]

        # Build return annotation - filter error clusters and class output
        filtered_outputs = [
            out
            for out in outputs
            if not self._is_error_output(out)
            and not self._is_self_output(out, class_name)
        ]

        returns = self._build_return_annotation(filtered_outputs, spell)

        return ast.FunctionDef(
            name=func_name,
            args=args_obj,
            body=body,
            decorator_list=[],
            returns=returns,
        )

    def _transform_instance_to_self(
        self, body: list[ast.stmt], instance_var: str
    ) -> list[ast.stmt]:
        """Transform references to instance variable into self.

        Walks the AST and replaces Name nodes matching instance_var with 'self'.
        """

        class InstanceToSelfTransformer(ast.NodeTransformer):
            def visit_Name(self, node: ast.Name) -> ast.AST:
                if node.id == instance_var:
                    return ast.Name(id="self", ctx=node.ctx)
                return node

        transformer = InstanceToSelfTransformer()
        return [transformer.visit(stmt) for stmt in body]

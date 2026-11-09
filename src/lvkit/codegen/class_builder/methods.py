"""Build the methods of a class from the member VIs and their ``MethodShape``."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VIContext
from lvkit.models import Terminal, TypeNamer

from ..ast_utils import (
    build_assign,
    distinct_names,
    interface_names,
    parse_expr,
    result_class_def,
    terminal_annotation,
    with_statements_after_docstring,
)
from ..builder import (
    CodeGenContext,
    annotated_terminals,
    build_args,
    function_body,
    output_value,
)
from ..class_model import ClassModel, MethodKind, MethodShape
from ..context import type_namer
from ..errors import CodeGenError
from ..gaps import Gap, gap_of, raise_statement, vi_body
from ..import_cycles import imports_inside
from ..import_resolver import ImportResolver
from ..panel_hooks import PanelHooks, method_enum_name
from ..type_fields import annotation_imports

if TYPE_CHECKING:
    from lvkit.graph import InMemoryVIGraph

    from .shared_state import ModuleParts


class _MethodBuilderMixin:
    """Method AST generation."""

    if TYPE_CHECKING:
        _graph: InMemoryVIGraph
        _import_resolver: ImportResolver
        _gaps: list[Gap]
        _parts: ModuleParts
        _lazy: bool
        _model: ClassModel
        _unavailable: dict[str, str]

    def _build_method(
        self,
        shape: MethodShape,
        name: str | None = None,
        decorators: list[ast.expr] | None = None,
        property_accessor: bool = False,
    ) -> ast.FunctionDef:
        """The method of the member VI ``shape.key``: a static method when it
        takes no class wire, else an instance method whose ``self`` is that wire.
        The getter or setter of a Python property (``property_accessor``) is
        called as ``obj.name`` and ``obj.name = value``: its value is positional
        and a getter returns the value itself.
        A member that cannot be a method (its name is taken, its class has no
        base) raises where it would run."""
        graph = self._graph
        key = shape.key
        vi_context = graph.get_vi_context(key)
        resolver = self._import_resolver.scoped(key)
        spell = type_namer(resolver)
        ctx = CodeGenContext.from_vi_context(
            vi_context,
            graph=graph,
            import_resolver=resolver,
            data_files=self._parts.data_files,
            module_stem=self._parts.module_stem,
            vi_name=key,
            gaps=self._gaps,
        )
        ctx.function_name = shape.name
        ctx.qualified_vi_name = vi_context.qualified_name
        self._parts.share_with(ctx)

        receiver = _receiver_of(vi_context, shape)
        names = _parameter_names(vi_context, shape, receiver)
        for term in [*vi_context.inputs, *vi_context.off_pane_inputs]:
            if term.id in names:
                ctx.bind(term.id, names[term.id])
        parameters = [t for t in vi_context.inputs if t is not receiver]
        # A property's getter and setter take no error in: it is a variable of
        # the body that holds no error.
        error_locals: list[ast.stmt] = [
            build_assign(names[t.id], ast.Constant(value=None))
            for t in parameters
            if property_accessor and t.is_error_cluster
        ]
        if property_accessor:
            parameters = [t for t in parameters if not t.is_error_cluster]
        ctx.panel_hooks = (
            None
            if property_accessor
            else PanelHooks.for_vi(
                ctx,
                vi_context,
                graph,
                key,
                method_enum_name(self._model.name, shape.name),
                taken=self._module_names(),
                parameter_names=names,
            )
        )
        args = build_args(parameters, names, spell, hooks=ctx.panel_hooks)
        if property_accessor:
            args.args.extend(args.kwonlyargs)
            args.kwonlyargs, args.kw_defaults = [], []
        if receiver is not None:
            args.args.insert(0, ast.arg(arg="self", annotation=None))

        reason = self._unavailable.get(key)
        # The signature annotates these types whether or not the body is written.
        for term in annotated_terminals(vi_context):
            ctx.import_types(term.lv_type, at_run_time=False)
            if term.is_error_cluster:
                ctx.import_labview_error(at_run_time=False)

        def build() -> list[ast.stmt]:
            if reason is not None:
                return [raise_statement(reason)]
            try:
                return with_statements_after_docstring(
                    error_locals,
                    function_body(
                        vi_context,
                        key,
                        ctx,
                        parameter_names=names,
                        return_stmt=lambda: self._return_stmt(
                            shape, vi_context, ctx, property_accessor
                        ),
                    ),
                )
            except CodeGenError as error:
                # One method that cannot be written is that VI's gap; the rest of
                # the class is still written.
                raise ConversionGap(str(error), error.node) from error

        body = vi_body(ctx, key, build)
        self._parts.collect(ctx)
        imports = ctx.imports
        if self._lazy:
            body, imports = imports_inside(body, imports)
        self._parts.imports.update(imports)
        if shape.kind is MethodKind.STATIC:
            decorators = [
                ast.Name(id="staticmethod", ctx=ast.Load()),
                *(decorators or []),
            ]
        return ast.FunctionDef(
            name=name or shape.name,
            args=args,
            body=body or [ast.Pass()],
            decorator_list=decorators or [],
            returns=self._return_annotation(
                shape, vi_context, spell, property_accessor
            ),
        )

    def _return_stmt(
        self,
        shape: MethodShape,
        vi_context: VIContext,
        ctx: CodeGenContext,
        property_accessor: bool,
    ) -> ast.Return | None:
        """The return of a method: its result class of the connector-pane results;
        nothing when it has none. A property getter returns its one value."""
        if not shape.results:
            return None
        outputs = _outputs_by_index(vi_context)
        vi_node = self._graph.get_graph_node(shape.key)
        values = [
            output_value(outputs[index], vi_node, ctx) for index in shape.results
        ]
        if property_accessor and len(values) == 1:
            return ast.Return(value=values[0])
        assert shape.result_class is not None
        return ast.Return(
            value=ast.Call(
                func=ast.Name(id=shape.result_class, ctx=ast.Load()),
                args=[],
                keywords=[
                    ast.keyword(arg=shape.result_fields[index], value=value)
                    for index, value in zip(shape.results, values, strict=True)
                ],
            )
        )

    def _return_annotation(
        self,
        shape: MethodShape,
        vi_context: VIContext,
        spell: TypeNamer | None,
        property_accessor: bool,
    ) -> ast.expr:
        if not shape.results:
            return ast.Constant(value=None)
        if property_accessor and len(shape.results) == 1:
            out = _outputs_by_index(vi_context)[shape.results[0]]
            return parse_expr(terminal_annotation(out, spell))
        assert shape.result_class is not None
        return ast.Name(id=shape.result_class, ctx=ast.Load())

    def _module_names(self) -> list[str]:
        """The names the class module defines besides its helpers: the class and
        the result classes of its methods."""
        return [
            self._model.name,
            *(s.result_class for s in self._model.shapes.values() if s.result_class),
        ]

    def _result_class_def(
        self, shape: MethodShape, vi_context: VIContext
    ) -> ast.ClassDef:
        """The result class the results of the method (and its overrides) are
        returned as."""
        assert shape.result_class is not None
        spell = type_namer(self._import_resolver.scoped(shape.key))
        outputs = _outputs_by_index(vi_context)
        self._parts.annotation_imports.update(
            annotation_imports(
                [outputs[index].lv_type for index in shape.results],
                self._import_resolver.scoped(shape.key),
            )
        )
        if any(outputs[index].is_error_cluster for index in shape.results):
            self._parts.annotation_imports.add(self._import_resolver.error_import())
        return result_class_def(
            shape.result_class,
            [
                (
                    shape.result_fields[index],
                    parse_expr(terminal_annotation(outputs[index], spell)),
                )
                for index in shape.results
            ],
        )

    def _gap_for(self, key: str, message: str) -> str:
        """Record the gap of a member that cannot be a method; its text."""
        gap = gap_of(ConversionGap(message), key, None)
        self._gaps.append(gap)
        return gap.message


def _receiver_of(vi_context: VIContext, shape: MethodShape) -> Terminal | None:
    if shape.receiver_in is None:
        return None
    return next((t for t in vi_context.inputs if t.index == shape.receiver_in), None)


def _outputs_by_index(vi_context: VIContext) -> dict[int, Terminal]:
    return {t.index: t for t in vi_context.outputs}


def _parameter_names(
    vi_context: VIContext, shape: MethodShape, receiver: Terminal | None
) -> dict[str, str]:
    """Terminal id to name of every input: ``self`` for the class wire, the
    keyword the declaration of the method gives each other connector-pane input
    (an override takes its declaration's, so a call by keyword reaches either),
    and for each off-pane control the name of its local variable, distinct from
    every parameter."""
    own = interface_names(vi_context.inputs, vi_context.off_pane_inputs, []).parameters
    names: dict[str, str] = {}
    for term in vi_context.inputs:
        if receiver is not None and term is receiver:
            names[term.id] = "self"
        elif term.index in shape.parameters:
            names[term.id] = shape.parameters[term.index]
        elif term.id in own:
            names[term.id] = own[term.id]
    controls = [t for t in vi_context.off_pane_inputs if t.id in own]
    locals_ = distinct_names(names.values(), [own[t.id] for t in controls])
    names.update({t.id: name for t, name in zip(controls, locals_, strict=True)})
    return names

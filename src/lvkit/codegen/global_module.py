"""Python module for a Global VI.

A Global VI has no diagram: it is its front-panel controls, and every VI that
reads or writes one of them shares the value. The module holds each control as a
module-level name set to the control's saved default; a VI that uses the global
imports the module and reads or writes those attributes (see
``nodes/global_variable``).

An error cluster control gets no name. Errors are exceptions in generated code, so
an error cluster carries nothing: ``build_args`` and ``build_result_class`` skip
it, and the property node generator skips its error terminals. A global variable
node on such a control generates nothing for the same reason.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.core import uid_of
from lvkit.graph.models import Constant, VINode
from lvkit.models import LVTypeKind, Terminal

from .ast_utils import (
    build_type_checking_block,
    param_default_expr,
    parameter_names,
    parse_expr,
    to_function_name,
)
from .context import (
    _SIMPLENAMESPACE_IMPORT,
    _format_constant,
    _needs_simplenamespace,
    type_namer,
)
from .data_files import DATA_FILE_IMPORTS, data_file_expression, holds_array_whole
from .gaps import Gap, gap_of, raise_statement
from .import_resolver import ImportResolver


def _saved_default(
    control: Terminal, module_stem: str, data_files: dict[str, bytes] | None
) -> tuple[ast.expr, bool]:
    """The control's saved default as an expression, and whether it needs
    ``SimpleNamespace``. A cluster or array keeps its saved value, formatted by the
    same path as a cluster or array constant on a diagram; unlike a function
    parameter default this is not a signature default, so a mutable container is
    the intended initial value. Every other control takes ``param_default_expr``."""
    lv_type = control.lv_type
    default = control.default_value
    if control.unwritable is not None:
        text = data_file_expression(
            what="control",
            name=control.name,
            uid=uid_of(control.id),
            module_stem=module_stem,
            unwritable=control.unwritable,
            lv_type=lv_type,
            data_files=data_files,
            whole_value=holds_array_whole(control.unwritable, lv_type),
        )
        return ast.parse(text, mode="eval").body, False
    if (
        lv_type is not None
        and lv_type.kind in (LVTypeKind.CLUSTER, LVTypeKind.ARRAY)
        and isinstance(default, str)
    ):
        text = _format_constant(
            Constant(
                id=control.id,
                value=default,
                lv_type=lv_type,
                unwritable=control.unwritable,
                label=control.name,
            )
        )
        try:
            return ast.parse(text, mode="eval").body, _needs_simplenamespace(text)
        except SyntaxError:
            pass
    return param_default_expr(control), False


def build_global_module(
    vi_node: VINode,
    import_resolver: ImportResolver | None = None,
    data_files: dict[str, bytes] | None = None,
    gaps: list[Gap] | None = None,
) -> str:
    """Module source for a Global VI: one annotated module-level name per
    front-panel control, in the VI's own control order, bound to the control's
    saved default. The names are the ones a global variable node reads
    (``parameter_names`` over the same control list). A control of a class or
    typedef type is annotated with that class, imported under ``TYPE_CHECKING``.
    A control whose saved default cannot be written is a gap: the module raises
    where the control is defined, and the gap is added to ``gaps``."""
    controls = vi_node.terminals
    spell = type_namer(import_resolver)
    annotation_imports: set[str] = set()
    names = parameter_names(controls)
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        ),
        ast.ImportFrom(module="typing", names=[ast.alias(name="Any")], level=0),
        ast.ImportFrom(module="pathlib", names=[ast.alias(name="Path")], level=0),
    ]
    assignments: list[ast.stmt] = []
    needs_namespace = False
    needs_runtime = False
    for control in controls:
        if control.id not in names:
            continue
        try:
            value, namespace = _saved_default(
                control, to_function_name(vi_node.name or ""), data_files
            )
        except ConversionGap as exc:
            gap = gap_of(exc, vi_node.id, None, label=control.name)
            if gaps is not None:
                gaps.append(gap)
            assignments.append(raise_statement(gap.message))
            continue
        if control.unwritable is not None:
            needs_runtime = True
        needs_namespace = needs_namespace or namespace
        if isinstance(import_resolver, ImportResolver) and control.lv_type:
            for named in control.lv_type.named_types():
                type_ref = named.type_ref()
                imported = import_resolver.type_import(type_ref) if type_ref else None
                if imported is not None:
                    annotation_imports.add(imported.statement)
        assignments.append(
            ast.AnnAssign(
                target=ast.Name(id=names[control.id], ctx=ast.Store()),
                annotation=parse_expr(control.python_type(spell)),
                value=value,
                simple=1,
            )
        )
    if needs_namespace:
        body.extend(ast.parse(_SIMPLENAMESPACE_IMPORT).body)
    if needs_runtime:
        body.extend(ast.parse(DATA_FILE_IMPORTS).body)
    body.extend(build_type_checking_block(annotation_imports))
    module = ast.Module(body=[*body, *assignments], type_ignores=[])
    ast.fix_missing_locations(module)
    return ast.unparse(module)

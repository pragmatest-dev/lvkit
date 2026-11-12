"""Python module for a Global VI.

A Global VI has no diagram: it is its front-panel controls, and every VI that
reads or writes one of them shares the value. The module holds each control as a
module-level name set to the control's saved default; a VI that uses the global
imports the module and reads or writes those attributes (see
``nodes/global_variable``). An error cluster control holds ``None`` (no error)
or a ``LabVIEWError``, like any error wire.

Like every VI, a Global VI has a front panel, and its panel is these same names:
the module's ``panel`` is a ``PanelValues`` over the module's namespace
(``panels_module``), so ``panel_of(<reference to the Global VI>)`` (the reference
is the module) sets and gets the very values a global variable node reads and
writes. Run VI on it is NI's error 1035.
"""

from __future__ import annotations

import ast

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.core import uid_of
from lvkit.graph.models import Constant, VINode
from lvkit.models import LVTypeKind, Terminal

from .ast_optimizer import remove_unused_imports
from .ast_utils import (
    build_type_checking_block,
    control_default_expr,
    distinct_names,
    implicit_imports,
    parameter_names,
    parse_expr,
    terminal_annotation,
    to_function_name,
)
from .context import _format_constant, type_namer
from .data_files import DATA_FILE_IMPORTS, data_file_expression, holds_array_whole
from .gaps import Gap, gap_of, raise_statement
from .import_resolver import ImportResolver
from .panels_module import PANEL_VALUES_NAME, PANELS_MODULE_NAME
from .type_default import holds_class, state_seed

# The module-level name of a Global VI's panel, which no control takes.
PANEL_ATTRIBUTE = "panel"


def global_attribute_names(controls: list[Terminal]) -> dict[str, str]:
    """Terminal id -> the module-level name of each control of a Global VI: its
    parameter name (``parameter_names``), made distinct from the module's
    ``panel``. The module and every global variable node take the names from
    here."""
    names = parameter_names(controls)
    distinct = distinct_names([PANEL_ATTRIBUTE], names.values())
    return dict(zip(names, distinct, strict=True))


def _saved_default(
    control: Terminal, module_stem: str, data_files: dict[str, bytes] | None
) -> ast.expr:
    """The control's saved default as an expression. A cluster or array keeps its
    saved value, formatted by the same path as a cluster or array constant on a
    diagram; unlike a function parameter default this is not a signature default,
    so a mutable container is the intended initial value. Every other control
    takes ``control_default_expr``; a class (or a cluster holding one) is ``None``,
    which the VI that reads it makes the object for (``type_default``)."""
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
        return ast.parse(text, mode="eval").body
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
            return ast.parse(text, mode="eval").body
        except SyntaxError:
            pass
    if holds_class(lv_type):
        return state_seed(lv_type)
    return control_default_expr(control)


def _panel_import(import_resolver: ImportResolver | None) -> str:
    statement = (
        import_resolver.panels_import(PANEL_VALUES_NAME)
        if isinstance(import_resolver, ImportResolver)
        else None
    )
    return statement or f"from .{PANELS_MODULE_NAME} import {PANEL_VALUES_NAME}"


def _panel_assignment(labels: dict[str, str]) -> ast.stmt:
    """``panel = PanelValues(controls={name: label}, storage=globals())``: the
    panel is the module's own names."""
    return ast.Assign(
        targets=[ast.Name(id=PANEL_ATTRIBUTE, ctx=ast.Store())],
        value=ast.Call(
            func=ast.Name(id=PANEL_VALUES_NAME, ctx=ast.Load()),
            args=[],
            keywords=[
                ast.keyword(
                    arg="controls",
                    value=ast.Dict(
                        keys=[ast.Constant(value=name) for name in labels],
                        values=[ast.Constant(value=label) for label in labels.values()],
                    ),
                ),
                ast.keyword(
                    arg="storage",
                    value=ast.Call(
                        func=ast.Name(id="globals", ctx=ast.Load()),
                        args=[],
                        keywords=[],
                    ),
                ),
            ],
        ),
    )


def build_global_module(
    vi_node: VINode,
    import_resolver: ImportResolver | None = None,
    data_files: dict[str, bytes] | None = None,
    gaps: list[Gap] | None = None,
) -> str:
    """Module source for a Global VI: one annotated module-level name per
    front-panel control, in the VI's own control order, bound to the control's
    saved default. The names are the ones a global variable node reads
    (``global_attribute_names`` over the same control list). A control of a class or
    typedef type is annotated with that class, imported under ``TYPE_CHECKING``.
    A control whose saved default cannot be written is a gap: the module raises
    where the control is defined, and the gap is added to ``gaps``."""
    controls = vi_node.terminals
    spell = type_namer(import_resolver)
    annotation_imports: set[str] = set()
    names = global_attribute_names(controls)
    labels: dict[str, str] = {}
    body: list[ast.stmt] = [
        ast.ImportFrom(
            module="__future__", names=[ast.alias(name="annotations")], level=0
        ),
    ]
    imports = ["from typing import Any"]
    assignments: list[ast.stmt] = []
    needs_runtime = False
    for control in controls:
        if control.id not in names:
            continue
        try:
            value = _saved_default(
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
        if isinstance(import_resolver, ImportResolver) and control.is_error_cluster:
            annotation_imports.add(import_resolver.error_import())
        if isinstance(import_resolver, ImportResolver) and control.lv_type:
            for named in control.lv_type.named_types():
                imported = import_resolver.type_import(named)
                if imported is not None:
                    annotation_imports.add(imported.statement)
        labels[names[control.id]] = control.name or ""
        assignments.append(
            ast.AnnAssign(
                target=ast.Name(id=names[control.id], ctx=ast.Store()),
                annotation=parse_expr(terminal_annotation(control, spell)),
                value=value,
                simple=1,
            )
        )
    imports.append(_panel_import(import_resolver))
    if needs_runtime:
        imports.extend(DATA_FILE_IMPORTS.splitlines())
    imports.extend(implicit_imports(assignments))
    for statement in dict.fromkeys(imports):
        body.extend(ast.parse(statement).body)
    body.extend(build_type_checking_block(annotation_imports))
    module = remove_unused_imports(
        ast.Module(
            body=[*body, _panel_assignment(labels), *assignments],
            type_ignores=[],
        )
    )
    ast.fix_missing_locations(module)
    return ast.unparse(module)

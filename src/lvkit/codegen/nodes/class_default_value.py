"""``Get LV Class Default Value.vi`` (``<vilib>/Utility/LVClass``).

NI, "Get LV Class Default Value" (labview-api-ref, vi-lib/utility/lvclass):
"Returns the class data for the class whose path you wire to the class path
input ... Use this VI to dynamically create class objects, including class
objects that are unknown at edit time." A class is a plain Python class, so the
object is a new instance of the class at that path, with its default private data.

The path is computed when the code runs, so it is looked up the way a computed
VI path is (``vi_reference``): read as a ``/`` path from the folder of the module,
and found in a module-level dictionary ``_lv_classes`` of the project's classes,
keyed by their path from that folder, each with its module and its class's name.
A path no class of the project has is LabVIEW's error 7 (file not found): the
forum threads on this VI's failures report "Error 7 occurred at Get LV Class
Default Value.vi" with the path it was given in the message
(forums.ni.com/t5/LabVIEW/Error-7-at-Get-LV-Class-Default/m-p/3941299).
The ``options`` input only says whether LabVIEW prompts for missing
dependencies, which Python has no counterpart of.
"""

from __future__ import annotations

import ast
from pathlib import Path

from lvkit.conversion_gap import ConversionGap
from lvkit.graph.models import VINode
from lvkit.vi_files import class_stem
from lvkit.vilib_resolver import VIEntry

from ..ast_utils import build_assign, module_part, parse_expr
from ..class_model import python_class_name
from ..computed_path import PATH_FROM_HERE, computed_path_module_key, path_from
from ..context import CodeGenContext
from ..fragment import CodeFragment
from ..import_resolver import ImportResolver
from ..native_failure import labview_error
from .grpc_pane import output_of, wired

FILE_NOT_FOUND = 7
_TABLE = "_lv_classes"
_NAME = "Get LV Class Default Value.vi"

_FIND = (
    PATH_FROM_HERE
    + """\
{entry} = {table}.get({rel})
"""
)


def _classes_table(ctx: CodeGenContext) -> None:
    """The module-level dictionary of the project's classes by their path from
    the folder of the module being written, each ``(module spec, class name)``."""
    assert ctx.graph is not None
    resolver = ctx.import_resolver
    module_key = computed_path_module_key(ctx)
    entries: dict[str, tuple[str, str]] = {}
    for class_key in ctx.graph.list_class_keys():
        spec = (
            resolver.package_spec(class_key)
            if isinstance(resolver, ImportResolver)
            else "."
        )
        module = spec + module_part(class_stem(Path(class_key).name))
        entries[path_from(resolver, class_key, module_key)] = (
            module,
            python_class_name(class_key),
        )
    ctx.add_module_global(
        _TABLE,
        ast.Dict(
            keys=[ast.Constant(value=path) for path in sorted(entries)],
            values=[
                ast.Tuple(
                    elts=[ast.Constant(value=part) for part in entries[path]],
                    ctx=ast.Load(),
                )
                for path in sorted(entries)
            ],
        ),
    )


def generate(node: VINode, ctx: CodeGenContext, entry: VIEntry) -> CodeFragment:
    """A new instance of the class at the wired path, or error 7."""
    obj = output_of(node, entry, "object_")
    if obj is None or not ctx.is_wired(obj.id):
        return CodeFragment.empty()
    if ctx.graph is None:
        raise ConversionGap("there is no graph to list the project's classes", node)
    value = wired(node, entry, "class_path", ctx)
    if value is None:
        raise ConversionGap("no class path is wired", node)
    _classes_table(ctx)
    ctx.add_import("import importlib")
    ctx.add_import("import os")
    ctx.import_labview_error()
    var = ctx.make_output_var("object", node.id, terminal_id=obj.id)
    names = {
        "here": f"{var}_here",
        "path": f"{var}_path",
        "rel": f"{var}_rel",
        "entry": f"{var}_entry",
        "table": _TABLE,
        "value": value,
    }
    error = ctx.make_output_var("own_error", node.id)
    missing = labview_error(
        FILE_NOT_FOUND, _NAME, f"f'no LabVIEW class at {{os.fspath({value})!r}}'"
    )
    source = _FIND.format(**names)
    statements = ast.parse(source).body
    statements.append(
        ast.If(
            test=parse_expr(f"{names['entry']} is None"),
            body=[build_assign(var, parse_expr("None")), build_assign(error, missing)],
            orelse=[
                build_assign(
                    var,
                    parse_expr(
                        f"getattr(importlib.import_module({names['entry']}[0], "
                        f"__package__), {names['entry']}[1])()"
                    ),
                ),
                build_assign(error, parse_expr("None")),
            ],
        )
    )
    return CodeFragment(
        statements=statements, bindings={obj.id: var}, own_error=error
    )

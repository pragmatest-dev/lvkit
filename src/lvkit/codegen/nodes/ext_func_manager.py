"""LabVIEW's own functions called through a Call Library Function Node (library
``LabVIEW``), written out as the Python that ``data/labview_manager.json`` lists
for each."""

from __future__ import annotations

import ast

from lvkit.graph.models import PrimitiveNode
from lvkit.manager_functions import manager_functions
from lvkit.parser.extfunc_prototype import ExtFuncCall, ParamKind

from ..context import CodeGenContext
from ..fragment import CodeFragment
from .ext_func_marshal import default_value, marshal_row
from .primitive import raise_unknown


def generate_manager_call(
    node: PrimitiveNode, call: ExtFuncCall, ctx: CodeGenContext
) -> CodeFragment:
    """The statements of the function's template, with its inputs substituted and
    each output side bound to the variable the template assigns."""
    function = manager_functions().get(call.function)
    if function is None:
        raise_unknown(
            node,
            None,
            ctx,
            reason=f"LabVIEW's function {call.function} has no Python equivalent",
        )
    if len(function.parameters) != len(call.row_terminal_ids) - 1:
        raise_unknown(
            node,
            None,
            ctx,
            reason=(
                f"it has {len(call.row_terminal_ids) - 1} parameters but NI's "
                f"{call.function} has {len(function.parameters)}"
            ),
        )
    values: dict[str, str] = {}
    for name, param, (input_id, _) in zip(
        function.parameters, call.params[1:], call.row_terminal_ids[1:]
    ):
        var = ctx.wired_input(input_id, node) if input_id else None
        values[name] = var or default_value(param, None) or "None"
    by_id = {t.id: t for t in node.terminals}
    bindings: dict[str, str] = {}
    for row, (_, output_id) in enumerate(call.row_terminal_ids):
        terminal = by_id.get(output_id)
        if terminal is None:
            values[f"out{row}"] = "_"
            continue
        name = ctx.make_output_var(
            terminal.name or f"param_{row}", node.id, terminal_id=terminal.id
        )
        values[f"out{row}"] = name
        bindings[output_id] = name
    for declaration in function.declarations:
        values[declaration.key] = ctx.module_declarations.declare(
            ("manager", declaration.key),
            declaration.name,
            lambda n, d=declaration: "\n".join(d.source).format(name=n) + "\n",
        )
    lines: list[str] = []
    after: list[str] = []
    if function.marshalled:
        for index, (name, param) in enumerate(
            zip(function.parameters, call.params[1:]), start=1
        ):
            if param.kind == ParamKind.ADAPT_TO_TYPE:
                raise_unknown(
                    node,
                    None,
                    ctx,
                    reason=f"parameter {index} of {call.function} is Adapt to Type",
                )
            packed = marshal_row(
                node,
                call,
                index,
                param,
                None,
                values[name],
                f"_{call.function}_{index}",
                ctx.module_declarations,
            )
            lines += packed.setup
            after += packed.after
            values[f"{name}_arg"] = packed.passed
            after.append(f"{values[f'out{index}']} = {packed.result}")
    lines += [line.format(**values) for line in function.statements]
    source = "\n".join(lines + after)
    return CodeFragment(
        statements=ast.parse(source).body,
        bindings=bindings,
        imports=set(function.imports),
    )

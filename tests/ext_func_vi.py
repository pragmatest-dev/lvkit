"""Build and load a one-node VI around a Call Library Function Node.

Controls are wired to the node's input sides, the node calls the named library, and
indicators are wired to its output sides. The generated Python runs."""

from __future__ import annotations

from typing import Any

from lvkit.codegen.builder import build_module
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from lvkit.parser.extfunc_prototype import ExtFuncCall, ExtFuncParam
from tests.helpers import build_graph


def lv_type(name: str | LVType) -> LVType:
    if isinstance(name, LVType):
        return name
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def build_vi(
    library: str | None,
    function: str,
    params: list[ExtFuncParam],
    wire_types: list[str | LVType],
    controls: dict[int, str],
    indicators: dict[int, str],
    convention: str = "c",
) -> tuple[Any, str]:
    """Generate and load a VI around one Call Library Function Node.

    ``params``/``wire_types`` are the prototype rows (return value first).
    ``controls`` maps a row to the control name wired to its input side;
    ``indicators`` maps a row to the indicator wired to its output side."""
    rows = [
        (None if row == 0 else f"n:in{row}", f"n:out{row}")
        for row in range(len(params))
    ]
    terminals: list[Terminal] = []
    for row, (input_id, output_id) in enumerate(rows):
        if input_id:
            terminals.append(
                Terminal(
                    id=input_id,
                    index=2 * row,
                    direction="input",
                    name=f"p{row}",
                    lv_type=lv_type(wire_types[row]),
                )
            )
        terminals.append(
            Terminal(
                id=output_id,
                index=2 * row + 1,
                direction="output",
                name=f"p{row}_out",
                lv_type=lv_type(wire_types[row]),
            )
        )
    node = PrimitiveNode(
        id="n",
        vi_path="Clfn Test.vi",
        name=f"{library}:{function}",
        node_type="extFunc",
        terminals=terminals,
        ext_func=ExtFuncCall(
            library=library,
            function=function,
            calling_convention=convention,
            path_in_terminal_id=None,
            row_terminal_ids=tuple(rows),
            params=tuple(params),
        ),
    )
    inputs = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="input",
            name=name,
            is_public=True,
            is_indicator=False,
            lv_type=lv_type(wire_types[row]),
        )
        for i, (row, name) in enumerate(controls.items())
    ]
    outputs = [
        FPTerminal(
            id=f"fp:{name}",
            index=i,
            direction="output",
            name=name,
            is_public=True,
            is_indicator=True,
            lv_type=lv_type(wire_types[row]),
        )
        for i, (row, name) in enumerate(indicators.items())
    ]
    wires = [
        Wire.from_terminals(from_terminal_id=f"fp:{name}", to_terminal_id=f"n:in{row}")
        for row, name in controls.items()
    ] + [
        Wire.from_terminals(from_terminal_id=f"n:out{row}", to_terminal_id=f"fp:{name}")
        for row, name in indicators.items()
    ]
    context = VIContext(
        name="Clfn Test.vi", inputs=inputs, outputs=outputs, data_flow=wires
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace: dict[str, Any] = {}
    exec(compile(source, context.name, "exec"), namespace)  # noqa: S102
    return namespace["clfn_test"], source

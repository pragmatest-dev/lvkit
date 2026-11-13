"""A hand-made caller diagram for one catalog primitive: ``run_primitive``
generates the VI from the primitive's id and pane, runs it, and returns the
generated source and the result object, so a test asserts what the catalog's
Python does."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, Terminal
from tests.helpers import build_graph, exec_generated
from tests.vilib_caller import Slot


def run_primitive(
    prim_id: int,
    pane: list[Slot],
    inputs: Mapping[str, tuple[int, Any]],
    outputs: Mapping[str, tuple[int, Any]],
    arguments: Mapping[str, Any],
    gaps: list[Gap] | None = None,
    name: str = "Primitive",
) -> tuple[str, Any]:
    """The generated VI's source and the result of running it.

    ``pane`` is every terminal of the primitive node as the caller's diagram
    saves them, ``inputs`` and ``outputs`` the VI's own controls and indicators
    by name with the node terminal index each is wired to, ``arguments`` the
    controls' values. A pane terminal no control or indicator names is unwired.
    """
    node = PrimitiveNode(
        id="prim",
        vi_path="Caller.vi",
        name=name,
        prim_id=prim_id,
        terminals=[
            Terminal(
                id=f"p:{direction}{index}",
                index=index,
                direction=direction,  # type: ignore[arg-type]
                lv_type=lv_type,
            )
            for index, direction, lv_type in pane
        ],
    )
    controls = [
        FPTerminal(
            id=f"fp:{label}",
            index=position,
            direction="input",
            wiring_rule=1,
            name=label,
            is_public=True,
            is_indicator=False,
            lv_type=lv_type,
        )
        for position, (label, (_index, lv_type)) in enumerate(inputs.items())
    ]
    indicators = [
        FPTerminal(
            id=f"fp:{label}",
            index=position,
            direction="output",
            name=label,
            is_public=True,
            is_indicator=True,
            lv_type=lv_type,
        )
        for position, (label, (_index, lv_type)) in enumerate(outputs.items())
    ]
    context = VIContext(
        name="Caller.vi",
        inputs=controls,
        outputs=indicators,
        data_flow=[
            *(
                Wire.from_terminals(
                    from_terminal_id=f"fp:{label}",
                    to_terminal_id=f"p:input{index}",
                )
                for label, (index, _type) in inputs.items()
            ),
            *(
                Wire.from_terminals(
                    from_terminal_id=f"p:output{index}",
                    to_terminal_id=f"fp:{label}",
                )
                for label, (index, _type) in outputs.items()
            ),
        ],
    )
    graph = build_graph(context, context.name, [node])
    found = gaps if gaps is not None else []
    source = build_module(context, context.name, graph=graph, gaps=found)
    assert gaps is not None or found == [], found
    namespace: dict[str, Any] = {}
    exec_generated(source, namespace, context.name)
    return source, namespace["caller"](**arguments)

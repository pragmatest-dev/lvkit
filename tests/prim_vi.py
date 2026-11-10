"""A tiny VI around one primitive node, for the catalog primitive tests: build it,
generate Python, run the function; and the same primitive as block-diagram XML,
for describe and lvnet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.graph.netlist import build_netlist_from_graph
from lvkit.graph.render_lvnet import render_lvnet
from lvkit.models import FPTerminal, LVType, LVTypeKind, Terminal
from tests.helpers import build_graph


def scalar(name: str) -> LVType:
    """A numeric, Boolean, string or path type by its underlying type name."""
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def array_of(element: LVType) -> LVType:
    return LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=element)


@dataclass
class Pin:
    """One terminal of the primitive. ``source`` names the VI control that feeds
    an input (None leaves it unwired); ``sink`` names the VI indicator an output
    feeds (None leaves it unwired)."""

    index: int
    direction: str
    lv_type: LVType | None
    source: str | None = None
    sink: str | None = None


def generate(
    prim_id: int,
    name: str,
    pins: list[Pin],
    controls: dict[str, LVType],
    indicators: dict[str, LVType],
    vi_name: str = "Prim Test.vi",
) -> tuple[str, Callable[..., Any]]:
    """The module generated for a VI whose controls (in the order given) feed the
    primitive ``prim_id`` and whose indicators read it, and the function in it."""
    inputs: list[Terminal] = []
    for index, (label, lv_type) in enumerate(controls.items()):
        inputs.append(
            FPTerminal(
                id=f"fp:{label}",
                index=index,
                direction="input",
                wiring_rule=1,
                name=label,
                is_public=True,
                is_indicator=False,
                lv_type=lv_type,
            )
        )
    outputs: list[Terminal] = []
    for index, (label, lv_type) in enumerate(indicators.items()):
        outputs.append(
            FPTerminal(
                id=f"fp:{label}",
                index=index,
                direction="output",
                name=label,
                is_public=True,
                is_indicator=True,
                lv_type=lv_type,
            )
        )
    node = PrimitiveNode(
        id="prim",
        vi_path=vi_name,
        name=name,
        prim_id=prim_id,
        terminals=[
            Terminal(
                id=f"prim:{pin.direction}{pin.index}",
                index=pin.index,
                direction=pin.direction,
                lv_type=pin.lv_type,
            )
            for pin in pins
        ],
    )
    wires: list[Wire] = []
    for pin in pins:
        terminal_id = f"prim:{pin.direction}{pin.index}"
        if pin.source is not None:
            wires.append(
                Wire.from_terminals(
                    from_terminal_id=f"fp:{pin.source}", to_terminal_id=terminal_id
                )
            )
        if pin.sink is not None:
            wires.append(
                Wire.from_terminals(
                    from_terminal_id=terminal_id, to_terminal_id=f"fp:{pin.sink}"
                )
            )
    context = VIContext(name=vi_name, inputs=inputs, outputs=outputs, data_flow=wires)
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec(compile(source, vi_name, "exec"), namespace)  # noqa: S102
    return source, namespace[vi_name.removesuffix(".vi").lower().replace(" ", "_")]


def block_diagram_xml(prim_id: int, parm_indices: list[tuple[int, bool]]) -> str:
    """Block-diagram XML of one primitive node with the given terminals, each a
    ``(parmIndex, is_output)``, all unwired."""
    terms = []
    for number, (index, is_output) in enumerate(parm_indices):
        flags = "<objFlags>1</objFlags>" if is_output else ""
        terms.append(
            f'<SL__arrayElement class="term" uid="{11 + number * 2}">{flags}'
            f'<dco class="parm" uid="{12 + number * 2}">{flags}'
            f"<parmIndex>{index}</parmIndex></dco></SL__arrayElement>"
        )
    return (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<SL__rootObject class="oHExt" uid="1">\n'
        '  <root class="diag" uid="2">\n'
        '    <zPlaneList elements="0" />\n'
        '    <nodeList elements="1">\n'
        '      <SL__arrayElement class="prim" uid="10">\n'
        f"        <primResID>{prim_id}</primResID>\n"
        f'        <termList elements="{len(terms)}">{"".join(terms)}</termList>\n'
        "      </SL__arrayElement>\n"
        "    </nodeList>\n"
        '    <signalList elements="0" />\n'
        "  </root>\n"
        "</SL__rootObject>\n"
    )


def describe_and_lvnet(
    tmp_path: Path, prim_id: int, parm_indices: list[tuple[int, bool]]
) -> tuple[str, str]:
    """``describe`` and ``lvnet`` text of a VI holding just the primitive."""
    bd_xml = tmp_path / "Prim_BDHb.xml"
    bd_xml.write_text(block_diagram_xml(prim_id, parm_indices))
    graph = InMemoryVIGraph()
    graph.load_vi(bd_xml)
    vi_name = graph.list_vis()[0]
    return (
        describe_vi(graph, vi_name),
        render_lvnet(build_netlist_from_graph(graph, vi_name)),
    )

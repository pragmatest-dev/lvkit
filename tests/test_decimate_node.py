"""Decimate 1D Array (the ``decimate`` node): output k is elements k, k+n, ...,
every output as long as the shortest."""

from __future__ import annotations

from lvkit.codegen.builder import build_module
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, Terminal
from tests.helpers import build_graph, exec_generated
from tests.prim_vi import array_of, scalar

ARRAY = array_of(scalar("NumInt32"))


def _generated(outputs: int, wired: tuple[int, ...]):
    controls = [
        FPTerminal(
            id="fp:array",
            index=0,
            direction="input",
            wiring_rule=1,
            name="array",
            is_public=True,
            is_indicator=False,
            lv_type=ARRAY,
        )
    ]
    indicators = [
        FPTerminal(
            id=f"fp:out{k}",
            index=k - 1,
            direction="output",
            name=f"out{k}",
            is_public=True,
            is_indicator=True,
            lv_type=ARRAY,
        )
        for k in wired
    ]
    node = PrimitiveNode(
        id="dec",
        vi_path="Decimate.vi",
        name="decimate",
        node_type="decimate",
        terminals=[
            Terminal(id="dec:in", index=0, direction="input", lv_type=ARRAY),
            *(
                Terminal(id=f"dec:out{k}", index=k, direction="output", lv_type=ARRAY)
                for k in range(1, outputs + 1)
            ),
        ],
    )
    wires = [Wire.from_terminals(from_terminal_id="fp:array", to_terminal_id="dec:in")]
    wires += [
        Wire.from_terminals(from_terminal_id=f"dec:out{k}", to_terminal_id=f"fp:out{k}")
        for k in wired
    ]
    context = VIContext(
        name="Decimate.vi", inputs=controls, outputs=indicators, data_flow=wires
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    return source, namespace["decimate"]


def test_ni_example_sixteen_elements_into_four_outputs() -> None:
    source, fn = _generated(4, (1, 2, 3, 4))
    assert "lvkit" not in source
    row = fn(list(range(16)))
    assert row.out1 == [0, 4, 8, 12]
    assert row.out2 == [1, 5, 9, 13]
    assert row.out3 == [2, 6, 10, 14]
    assert row.out4 == [3, 7, 11, 15]


def test_every_output_is_as_long_as_the_shortest() -> None:
    _, fn = _generated(4, (1, 2, 3, 4))
    row = fn(list(range(15)))
    assert row.out1 == [0, 4, 8]
    assert row.out2 == [1, 5, 9]
    assert row.out3 == [2, 6, 10]
    assert row.out4 == [3, 7, 11]


def test_an_unwired_output_still_counts_and_a_short_array_is_empty() -> None:
    _, fn = _generated(3, (1, 3))
    row = fn(list(range(7)))
    assert row.out1 == [0, 3]
    assert row.out3 == [2, 5]
    assert fn([1, 2]).out1 == []

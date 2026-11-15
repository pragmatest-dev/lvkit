"""Interleave 1D Arrays (the ``interLeave`` node): element k of every array in
turn, as long as the shortest array times the number of arrays."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph.models import PrimitiveNode, VIContext, Wire
from lvkit.models import FPTerminal, Terminal
from lvkit.parser.vi import _extract_nodes
from tests.helpers import build_graph, exec_generated
from tests.prim_vi import array_of, scalar

ARRAY = array_of(scalar("NumInt32"))


def _generated(count: int, wired: tuple[int, ...]):
    """A VI that interleaves ``count`` arrays; only ``wired`` inputs have a wire."""
    controls = [
        FPTerminal(
            id=f"fp:a{k}",
            index=k - 1,
            direction="input",
            wiring_rule=1,
            name=f"a{k}",
            is_public=True,
            is_indicator=False,
            lv_type=ARRAY,
        )
        for k in wired
    ]
    indicator = FPTerminal(
        id="fp:out",
        index=0,
        direction="output",
        name="out",
        is_public=True,
        is_indicator=True,
        lv_type=ARRAY,
    )
    node = PrimitiveNode(
        id="il",
        vi_path="Interleave.vi",
        name="Interleave 1D Arrays",
        node_type="interLeave",
        terminals=[
            Terminal(id="il:out", index=0, direction="output", lv_type=ARRAY),
            *(
                Terminal(id=f"il:in{k}", index=k, direction="input", lv_type=ARRAY)
                for k in range(1, count + 1)
            ),
        ],
    )
    wires = [
        Wire.from_terminals(from_terminal_id=f"fp:a{k}", to_terminal_id=f"il:in{k}")
        for k in wired
    ]
    wires.append(
        Wire.from_terminals(from_terminal_id="il:out", to_terminal_id="fp:out")
    )
    context = VIContext(
        name="Interleave.vi", inputs=controls, outputs=[indicator], data_flow=wires
    )
    graph = build_graph(context, context.name, [node])
    source = build_module(context, context.name, graph=graph)
    namespace: dict = {}
    exec_generated(source, namespace, context.name)
    return source, namespace["interleave"]


def test_ni_example_three_equal_arrays() -> None:
    source, fn = _generated(3, (1, 2, 3))
    assert "lvkit" not in source
    assert fn([0, 3], [1, 4], [2, 5]).out == [0, 1, 2, 3, 4, 5]


def test_ni_example_unequal_arrays_stop_at_the_smallest() -> None:
    _, fn = _generated(3, (1, 2, 3))
    assert fn([0, 3, 5], [1], [2, 4]).out == [0, 1, 2]


def test_an_empty_array_makes_an_empty_result() -> None:
    _, fn = _generated(2, (1, 2))
    assert fn([1, 2], []).out == []


def test_an_input_with_nothing_wired_is_a_gap_that_raises_where_it_runs() -> None:
    _, fn = _generated(3, (1, 3))
    with pytest.raises(NotImplementedError, match="nothing wired"):
        fn([0], [1])


def test_the_node_is_parsed_with_its_name_in_labview_words() -> None:
    root = ET.Element("root")
    elem = ET.SubElement(root, "SL__arrayElement", {"class": "interLeave", "uid": "7"})
    ET.SubElement(elem, "bounds").text = "(0, 0, 10, 10)"
    ET.SubElement(elem, "termList", {"elements": "0"})
    nodes = _extract_nodes(root)
    assert [(n.node_type, n.name) for n in nodes] == [
        ("interLeave", "Interleave 1D Arrays")
    ]

"""A ``Void`` terminal with a wire keeps its line in lvnet, a type the file cannot
read keeps its reason, and a terminal name that needs quoting is quoted wherever it
is written (#181).

Each case is a tiny diagram of Increment nodes (and a Property Node) written as heap
XML (``structure_diagrams.py``): what lvnet, JSON and describe print, and that the
text reads back to the same graph. A ``Void`` terminal nothing is wired to is a dead
connector-pane slot and stays out.
"""

from __future__ import annotations

from pathlib import Path

from lvkit.graph.lvnet_parse import parse_lvnet
from lvkit.graph.lvnet_reconstruct import reconstruct_module
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests import vi_reference_diagrams as d
from tests.structure_diagrams import (
    I32,
    STUB,
    TYPES,
    VOID,
    VOID_TYPES,
    Views,
    constant,
    increment,
    load,
    root_diagram,
    srn,
    wire,
)
from tests.test_lvnet_identity import _assert_graph_identity

_UNREADABLE = 'Void (unreadable "its type descriptor could not be read")'
_PROPERTY = "Image Parameters : Image Type"


def _round_trips(views: Views) -> None:
    module = build_netlist_from_graph(views.graph, views.vi)
    rebuilt = reconstruct_module(parse_lvnet(views.lvnet))
    _assert_graph_identity(module, rebuilt, "T.vi")
    assert render_lvnet(rebuilt, display_name="T.vi", verbose=True) == views.lvnet


def _void_chain(tmp_path: Path) -> Views:
    """Increment_10 (Void in nothing is wired to; Void out read by Increment_20),
    Increment_20 (Void in wired; Void out nothing reads), Increment_30 (I32 in
    unwired; out an unreadable type) read by Increment_40."""
    nodes = [
        increment(10, VOID),
        increment(20, VOID),
        increment(30, I32, out_type=STUB),
        increment(40, STUB),
    ]
    xml = root_diagram(
        nodes,
        [srn(800, [constant(701, I32, 7)])],
        [wire(901, 11, 23), wire(902, 31, 43)],
    )
    return load(tmp_path, xml, TYPES + VOID_TYPES)


def test_a_wired_void_keeps_its_line_and_an_unwired_one_does_not(
    tmp_path: Path,
) -> None:
    views = _void_chain(tmp_path)
    assert views.diagram.splitlines()[:4] == [
        "    function Increment_10 : Increment",
        "      out  x+1 : Void",
        "    function Increment_20 : Increment",
        "      in   x : Void = Increment_10::x+1",
    ]
    _round_trips(views)


def test_a_type_the_file_cannot_read_keeps_its_line_and_says_why(
    tmp_path: Path,
) -> None:
    views = _void_chain(tmp_path)
    assert views.diagram.splitlines()[4:] == [
        "    function Increment_30 : Increment",
        "      in   x   : I32 default",
        f"      out  x+1 : {_UNREADABLE}",
        "    function Increment_40 : Increment",
        f"      in   x   : {_UNREADABLE} = Increment_30::x+1",
        f"      out  x+1 : {_UNREADABLE}",
    ]
    out = next(
        i
        for i in views.json["body"]
        if i["name"] == "Increment" and i["uid"] == "30"
    )["outputs"][0]
    assert out["lv_type"]["unreadable"] == "its type descriptor could not be read"
    assert "unreadable" not in next(
        i for i in views.json["body"] if i["uid"] == "10"
    )["outputs"][0]["lv_type"]


def test_a_terminal_name_is_quoted_in_its_declaration_and_its_readers(
    tmp_path: Path,
) -> None:
    prop = d.vi_name_property(60, 500, _PROPERTY)
    xml = root_diagram([], [prop, d.close_reference(80)], [d.wire(901, 69, 81)])
    views = load(tmp_path, xml)
    assert f'      out  "{_PROPERTY}" : ?\n' in views.lvnet
    assert f'= Property_Node_60::"{_PROPERTY}"' in views.lvnet
    _round_trips(views)


def test_an_unreadable_reason_is_a_quoted_literal_whatever_it_says() -> None:
    """The reason sits in a type token on a terminal line; quoted, it cannot be
    taken for the line's own `` : ``, ``, `` or ``default``."""
    reason = 'cut off : here, default "q"'
    text = (
        "vi V.vi :\n  front-panel :\n"
        f'    in   x : I32 (unreadable "{reason.replace(chr(34), chr(92) + chr(34))}")'
        " default 0 @0\n  block-diagram :\n"
    )
    row = parse_lvnet(text).boundary[0]
    assert row.default == "0"
    assert row.type.startswith('I32 (unreadable "cut off : here, default ')
    assert row.type.endswith('")')
    assert row.index == 0

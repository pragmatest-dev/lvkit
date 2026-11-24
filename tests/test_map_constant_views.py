"""A Set or Map constant keeps its value type through the graph, so its saved bytes
decode and every view shows, and ``generate`` writes, the stored entries.

Each case is a tiny VI written as heap XML (``structure_diagrams.py``): one
constant wired to an indicator, run through the parser, graph, describe, lvnet
(terse and verbose), JSON and generate, and the generated Python is run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.codegen.gaps import Gap
from lvkit.graph import InMemoryVIGraph
from lvkit.graph.describe import describe_vi
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from lvkit.graph.netlist_json import netlist_to_dict
from tests.helpers import exec_generated
from tests.structure_diagrams import (
    Views,
    main_xml,
    root_diagram,
    srn,
    term,
    wire,
)

STRING, I32, SET, MAP = range(4)
TYPES = [
    '<TypeDesc Type="String" />',
    '<TypeDesc Type="NumInt32" />',
    '<TypeDesc Type="115"><TypeDesc TypeID="0" /></TypeDesc>',
    '<TypeDesc Type="116"><TypeDesc TypeID="0" /><TypeDesc TypeID="1" /></TypeDesc>',
]

_SET_OF_TWO = "00000002" + "00000001" + "61" + "00000001" + "62"
_MAP_OF_TWO = (
    "00000002" + "00000001" + "61" + "00000001" + "00000001" + "62" + "00000002"
)


def _indicator(type_id: int) -> str:
    return (
        '<SL__arrayElement class="fPDCO" uid="30"><objFlags>1</objFlags>'
        f"<typeDesc>TypeID({type_id})</typeDesc>"
        f'<ddo class="stdString" uid="301"><typeDesc>TypeID({type_id})</typeDesc>'
        '<partsList><SL__arrayElement class="label" uid="302">'
        '<textRec class="textHair"><text>"result"</text></textRec>'
        "</SL__arrayElement></partsList></ddo></SL__arrayElement>"
    )


def _front_panel(type_id: int) -> str:
    return (
        '<SL__rootObject class="oHExt" uid="1"><root class="supC" uid="2">'
        '<conPane class="conPane" uid="9"><conId>4800</conId><cons>'
        '<SL__arrayElement class="ConpaneConnection" index="0">'
        '<ConnectionDCO uid="30" /></SL__arrayElement></cons></conPane>'
        '<ddoList><SL__arrayElement uid="30" /></ddoList>'
        '<paneHierarchy class="pane" uid="3"><zPlaneList>'
        f"{_indicator(type_id)}</zPlaneList></paneHierarchy></root></SL__rootObject>"
    )


def _views(tmp_path: Path, type_id: int, value_hex: str) -> Views:
    """A VI whose indicator ``result`` shows one constant holding ``value_hex``."""
    (tmp_path / "T.xml").write_text(main_xml(TYPES), encoding="utf-8")
    (tmp_path / "T_FPHb.xml").write_text(_front_panel(type_id), encoding="utf-8")
    xml = root_diagram(
        [],
        [
            srn(
                800,
                [
                    term(
                        701,
                        f'<dco class="bDConstDCO" uid="702"><typeDesc>TypeID({type_id})'
                        f"</typeDesc><ConstValue>{value_hex}</ConstValue></dco>",
                    ),
                    '<SL__arrayElement class="fPTerm" uid="5030">'
                    '<dco uid="30"/></SL__arrayElement>',
                ],
            )
        ],
        [wire(901, 701, 5030)],
    )
    bd = tmp_path / "T_BDHb.xml"
    bd.write_text(xml, encoding="utf-8")
    graph = InMemoryVIGraph()
    graph.load_vi(bd)
    vi = graph.list_vis()[0]
    module = build_netlist_from_graph(graph, vi)
    return Views(
        graph,
        vi,
        render_lvnet(module, display_name="T.vi", verbose=True),
        render_lvnet(module, display_name="T.vi"),
        describe_vi(graph, vi),
        netlist_to_dict(module, verbose=True),
    )


def _generate(views: Views) -> tuple[object, str, list[Gap]]:
    gaps: list[Gap] = []
    context = views.graph.get_vi_context(views.vi)
    source = build_module(context, views.vi, graph=views.graph, gaps=gaps)
    namespace: dict[str, object] = {}
    exec_generated(source, namespace, "T.py")  # noqa: S102
    return namespace["t"], source, gaps


@pytest.mark.parametrize(
    ("type_id", "value_hex", "shown", "value"),
    [
        (MAP, "00000000", "{}", {}),
        (MAP, _MAP_OF_TWO, "{'a': 1, 'b': 2}", {"a": 1, "b": 2}),
        (SET, "00000000", "set()", set()),
        (SET, _SET_OF_TWO, "{'a', 'b'}", {"a", "b"}),
    ],
)
def test_a_set_or_map_constant_shows_and_generates_its_entries(
    tmp_path: Path, type_id: int, value_hex: str, shown: str, value: object
) -> None:
    views = _views(tmp_path, type_id, value_hex)
    described = "map{String: I32}" if type_id == MAP else "set{String}"
    assert f"  (unnamed): {described} = {shown}" in views.describe.splitlines()
    assert f'    result = "{shown}"' in views.terse.splitlines()
    assert f'    result = "{shown}"' in views.diagram.splitlines()
    assert views.json["outputs"][0]["source"]["bare"] == shown
    function, source, gaps = _generate(views)
    assert gaps == []
    assert function().result == value  # type: ignore[attr-defined]
    assert "lvkit" not in source

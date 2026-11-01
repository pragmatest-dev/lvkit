"""A Property Node row whose terminal type is Void takes the property's declared
type from its ``propItemInfo`` (#181); a row whose terminal has a type keeps it."""

from __future__ import annotations

from pathlib import Path

import pytest

from lvkit.codegen.builder import build_module
from lvkit.graph import InMemoryVIGraph, LoadMode
from lvkit.graph.netlist import build_netlist_from_graph, render_lvnet
from tests.structure_diagrams import (
    I32,
    TYPES,
    Views,
    increment,
    load,
    root_diagram,
    wire,
)

# Type ids after ``TYPES``.
VOID, U32, UNIT_U8, U8 = 3, 4, 5, 6
_TYPES = TYPES + [
    '<TypeDesc Type="Void" />',
    '<TypeDesc Type="NumUInt32" />',
    '<TypeDesc Type="UnitUInt8" />',
    '<TypeDesc Type="NumUInt8" />',
]


def _property_node(uid: int, name: str, terminal_type: int, declared: int) -> str:
    """A Property Node with one row; its value terminal is ``uid + 9``."""
    return (
        f'<SL__arrayElement class="propNode" uid="{uid}">'
        f'<termList elements="1">'
        f'<SL__arrayElement class="term" uid="{uid + 9}">'
        f'<dco class="propItem" uid="{uid + 10}"><objFlags>65537</objFlags>'
        f"<typeDesc>TypeID({terminal_type})</typeDesc>"
        '<propList elements="1">'
        f'<SL__arrayElement class="propItemInfo" uid="{uid + 11}">'
        f"<dataTypeDesc>TypeID({declared})</dataTypeDesc>"
        f'<PropItemName>"{name}"</PropItemName><PropItemCode>8577</PropItemCode>'
        "</SL__arrayElement></propList></dco></SL__arrayElement></termList>"
        f'<dcoList elements="1"><SL__arrayElement uid="{uid + 10}" /></dcoList>'
        '<nodeName>"Task"</nodeName></SL__arrayElement>'
    )


def _views(tmp_path: Path, terminal_type: int, declared: int) -> Views:
    xml = root_diagram(
        [],
        [_property_node(60, "NumChans", terminal_type, declared), increment(10, I32)],
        [wire(901, 69, 13)],
    )
    return load(tmp_path, xml, _TYPES)


def _generated(views: Views) -> str:
    ctx = views.graph.get_vi_context(views.vi)
    return build_module(ctx, views.vi, graph=views.graph)


# Generation reads the row as a Property of an object nothing opened; the text is
# the same whatever the row's type, and must not change with it.
_GENERATED = (
    "from __future__ import annotations\n"
    "from typing import Any, NamedTuple\n\n"
    "def t():\n"
    "    raise NotImplementedError("
    "\"Property 'NumChans' of the default Task object has no Python equivalent\\n"
    "gap kind: unsupported_node\\nVI: T_BDHb.xml\\nnode: Property Node\")"
)


def test_a_void_terminal_takes_the_declared_type_in_every_view(
    tmp_path: Path,
) -> None:
    views = _views(tmp_path, VOID, U32)
    assert views.diagram.splitlines()[:2] == [
        "    property-node Property_Node_60 : Task",
        "      out  NumChans : U32",
    ]
    assert "Void" not in views.lvnet
    out = next(i for i in views.json["body"] if i["uid"] == "60")["outputs"][0]
    assert out["lv_type"]["underlying_type"] == "NumUInt32"
    assert views.describe.endswith(
        "## Operations\nProperty Node [Property Node]\nIncrement [prim 1057]"
    )
    assert _generated(views) == _GENERATED




def test_a_terminal_with_a_type_keeps_it(tmp_path: Path) -> None:
    views = _views(tmp_path, U32, U32)
    assert views.diagram.splitlines()[:2] == [
        "    property-node Property_Node_60 : Task",
        "      out  NumChans : U32",
    ]
    assert _generated(views) == _GENERATED


def test_a_unit_u8_terminal_keeps_its_unit_type_over_the_declared_u8(
    tmp_path: Path,
) -> None:
    views = _views(tmp_path, UNIT_U8, U8)
    assert views.diagram.splitlines()[:2] == [
        "    property-node Property_Node_60 : Task",
        "      out  NumChans : enum{}",
    ]
    out = next(i for i in views.json["body"] if i["uid"] == "60")["outputs"][0]
    assert out["lv_type"]["underlying_type"] == "UnitUInt8"
    assert out["lv_type"]["kind"] == "enum"
    assert _generated(views) == _GENERATED


_DAQ = (
    Path(__file__).resolve().parent.parent
    / ".lvkit"
    / "cache"
    / "samples"
    / "LabVIEW-OOP-Classes"
    / "DAQ"
)


@pytest.mark.needs_samples
@pytest.mark.parametrize(
    "relative",
    ["Analog Input/AI_class/Setup AI.vi", "Digital Input/DI_class/Setup DI.vi"],
)
def test_the_saved_broken_setup_vis_print_numchans_as_u32(relative: str) -> None:
    vi_path = _DAQ / relative
    if not vi_path.exists():
        pytest.skip(f"{relative} not present in sample corpus")
    graph = InMemoryVIGraph()
    graph.load_vi(vi_path, LoadMode.NONE)
    vi = graph.list_vis()[0]
    text = render_lvnet(build_netlist_from_graph(graph, vi), display_name=vi_path.name)
    assert "            out  NumChans : U32" in text.splitlines()

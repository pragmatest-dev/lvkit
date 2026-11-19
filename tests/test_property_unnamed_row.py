"""A Property Node row saved with no property name and no code (the layout
LabVIEW 8.2 writes) is a gap that says what the row does record."""

from __future__ import annotations

from pathlib import Path

from lvkit.codegen.builder import build_module
from tests.structure_diagrams import I32, TYPES, increment, load, root_diagram, wire


def _unnamed_row_node(uid: int) -> str:
    return (
        f'<SL__arrayElement class="propNode" uid="{uid}">'
        f'<termList elements="1">'
        f'<SL__arrayElement class="term" uid="{uid + 9}">'
        f'<dco class="propItem" uid="{uid + 10}"><objFlags>1</objFlags>'
        f"<typeDesc>TypeID({I32})</typeDesc>"
        '<propList elements="1">'
        f'<SL__arrayElement class="propItemInfo" uid="{uid + 11}">'
        f"<dataTypeDesc>TypeID({I32})</dataTypeDesc>"
        "</SL__arrayElement></propList></dco></SL__arrayElement></termList>"
        f'<dcoList elements="1"><SL__arrayElement uid="{uid + 10}" /></dcoList>'
        '<nodeName>"Bool"</nodeName></SL__arrayElement>'
    )


def test_a_row_without_name_or_code_names_its_class_and_what_it_does_record(
    tmp_path: Path,
) -> None:
    xml = root_diagram(
        [], [_unnamed_row_node(60), increment(10, I32)], [wire(901, 69, 13)]
    )
    views = load(tmp_path, xml, TYPES)
    ctx = views.graph.get_vi_context(views.vi)
    source = build_module(ctx, views.vi, graph=views.graph)
    assert (
        "Property name is unresolved: the row on the Bool class records no "
        "property name and no property code, only its data type (int)"
    ) in source

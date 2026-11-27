"""The value a control's settable properties start from: the parser reads each from
its ddo field (``parser/fp_starting_properties.py``, tiny handmade heap xml), and a
Property Node on a control reference reads it before any write
(``codegen/control_property_state.py``, generated packages that are imported and
run). Every listed property works on a reference as it does on a bound control; the
saved value of one the conversion does not decode raises, naming it, until a write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests.class_graphs import INT
from tests.error_diagrams import REQUIRED, Vi
from tests.error_diagrams import Project as XmlProject
from tests.error_diagrams import project as xml_project  # noqa: F401
from tests.panel_diagrams import array_type
from tests.test_combo_box_strings import STRINGS
from tests.test_front_panel_reference import STRING
from tests.test_reference_properties_listed import (
    DBL,
    DOC_MAX,
    X_MAX,
    generate,
    read,
    reference,
    write,
)

INTS = LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=INT)

TIP_STRIP = (104013844,)
X_MIN = (104078347, 104001541)
Y_MIN = (104078348, 104001541)
Y_MAX = (104078348, 104001542)
INDEX_VALUES = (104041476,)
ITEM_NAMES = (416911360,)
ACTIVE_CELL = (416911374,)
EDIT_POSITION = (416911420,)

TIP_HEAP = (
    '<description class="cLStrObj" uid="9"><strings elements="2">'
    "<SL__arrayElement>[NULL]</SL__arrayElement>"
    '<SL__arrayElement>"Save the report"</SL__arrayElement>'
    "</strings></description>"
)
DESCRIPTION_ONLY_HEAP = (
    '<description class="cLStrObj" uid="9"><strings elements="1">'
    '<SL__arrayElement>"Only a description"</SL__arrayElement>'
    "</strings></description>"
)


def scale(part_id: int, minimum: str | None, maximum: str | None) -> str:
    """The heap xml of a graph's scale part ``part_id`` with the hex doubles."""
    return (
        f'<SL__arrayElement class="scale" uid="{part_id}"><partID>{part_id}</partID>'
        + (f"<scaleDMin>{minimum}</scaleDMin>" if minimum else "")
        + (f"<scaleDMax>{maximum}</scaleDMax>" if maximum else "")
        + "</SL__arrayElement>"
    )


def test_the_tip_strip_is_the_second_string_of_the_description(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi("Tip.vi")
    vi.control("c", vi.add_type('<TypeDesc Type="String" />'), REQUIRED, inner=TIP_HEAP)
    vi.control(
        "d",
        vi.add_type('<TypeDesc Type="String" />'),
        REQUIRED,
        inner=DESCRIPTION_ONLY_HEAP,
    )
    vi.control("e", vi.add_type('<TypeDesc Type="String" />'), REQUIRED)
    xml_project.add(vi)
    graph = xml_project.views("Tip.vi").graph
    found = {
        t.name: t.starting_properties["tip_strip"]
        for t in graph.get_vi_context(graph.list_vis()[0]).inputs
        if isinstance(t, FPTerminal)
    }
    assert found == {"c": "Save the report", "d": "", "e": ""}


def test_a_scrollbar_starts_at_its_doc_max(xml_project: XmlProject) -> None:  # noqa: F811
    vi = Vi("Scroll.vi")
    kind = vi.add_type('<TypeDesc Type="NumFloat64" />')
    inner = "<scrollbarMin>4024000000000000</scrollbarMin>"
    inner += "<scrollbarMax>4069000000000000</scrollbarMax>"
    vi.control("c", kind, REQUIRED, ddo_class="scrollbar", inner=inner)
    xml_project.add(vi)
    graph = xml_project.views("Scroll.vi").graph
    (control,) = graph.get_vi_context(graph.list_vis()[0]).inputs
    assert isinstance(control, FPTerminal)
    assert control.starting_properties["doc_max"] == 200.0


def test_a_graphs_scales_start_at_their_range_and_an_absent_field_is_zero(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi("Graph.vi")
    kind = vi.add_type('<TypeDesc Type="NumFloat64" />')
    parts = (
        '<partsList elements="2">'
        + scale(18, None, "4059000000000000")
        + scale(19, "C024000000000000", "4024000000000000")
        + "</partsList>"
    )
    vi.control("c", kind, REQUIRED, ddo_class="stdGraph", inner=parts)
    xml_project.add(vi)
    graph = xml_project.views("Graph.vi").graph
    (control,) = graph.get_vi_context(graph.list_vis()[0]).inputs
    assert isinstance(control, FPTerminal)
    found = control.starting_properties
    assert (found["x_min"], found["x_max"]) == (0.0, 100.0)
    assert (found["y_min"], found["y_max"]) == (-10.0, 10.0)


def test_an_array_starts_at_its_index_values(xml_project: XmlProject) -> None:  # noqa: F811
    vi = Vi("Array.vi")
    kind = array_type(vi, vi.add_type('<TypeDesc Type="NumInt32" />'))
    inner = (
        '<arrayIndices elements="2"><SL__arrayElement>3</SL__arrayElement>'
        "<SL__arrayElement>1</SL__arrayElement></arrayIndices>"
    )
    vi.control("c", kind, REQUIRED, ddo_class="indArr", inner=inner)
    xml_project.add(vi)
    graph = xml_project.views("Array.vi").graph
    (control,) = graph.get_vi_context(graph.list_vis()[0]).inputs
    assert isinstance(control, FPTerminal)
    assert control.starting_properties["index_values"] == [3, 1]


def test_a_listbox_starts_with_its_items_row_major_and_its_active_cell(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi("Listbox.vi")
    kind = vi.add_type('<TypeDesc Type="NumInt32" />')
    inner = (
        '<baseListboxItemStrings class="SL__multiDimArray">'
        '<multiDimArraySizes elements="2"><SL__arrayElement>3</SL__arrayElement>'
        "<SL__arrayElement>2</SL__arrayElement></multiDimArraySizes>"
        '<multiDimArrayElems elements="6">'
        + "".join(f'<SL__arrayElement>"{text}"</SL__arrayElement>' for text in "abcdef")
        + "</multiDimArrayElems></baseListboxItemStrings>"
        '<activeCell class="StorageRowCol"><row>2</row><col>1</col></activeCell>'
    )
    vi.control("c", kind, REQUIRED, ddo_class="listbox", inner=inner)
    xml_project.add(vi)
    graph = xml_project.views("Listbox.vi").graph
    (control,) = graph.get_vi_context(graph.list_vis()[0]).inputs
    assert isinstance(control, FPTerminal)
    found = control.starting_properties
    assert found["item_names"] == [["a", "b"], ["c", "d"], ["e", "f"]]
    assert found["active_cell"] == (2, 1)


DECODED = [
    ("Str", TIP_STRIP, STRING, "stdString", {"tip_strip": "Save"}, "Save", "x"),
    ("Str", TIP_STRIP, STRING, "stdString", {}, "", "x"),
    ("Scroll", DOC_MAX, DBL, "scrollbar", {"doc_max": 200.0}, 200.0, 5.0),
    ("XYGraph (strict)", X_MIN, DBL, "stdGraph", {"x_min": -2.5}, -2.5, 1.0),
    ("XYGraph (strict)", X_MAX, DBL, "stdGraph", {"x_max": 100.0}, 100.0, 1.0),
    ("XYGraph (strict)", Y_MIN, DBL, "stdGraph", {"y_min": 0.0}, 0.0, 1.0),
    ("XYGraph (strict)", Y_MAX, DBL, "stdGraph", {"y_max": 10.0}, 10.0, 1.0),
    ("Arr", INDEX_VALUES, INTS, "indArr", {"index_values": [3, 1]}, [3, 1], [0, 0]),
    (
        "MCListbox (strict)",
        ITEM_NAMES,
        STRINGS,
        "listbox",
        {"item_names": [["a", "b"], ["c", "d"]]},
        [["a", "b"], ["c", "d"]],
        [],
    ),
    (
        "MCListbox (strict)",
        ACTIVE_CELL,
        INT,
        "listbox",
        {"active_cell": (2, 1)},
        (2, 1),
        (0, 0),
    ),
    ("MCListbox (strict)", EDIT_POSITION, INT, "listbox", {}, (-2, -2), (0, 0)),
]


@pytest.mark.parametrize(
    ("class_name", "codes", "kind", "control_type", "starting", "expected", "written"),
    DECODED,
)
def test_a_read_before_a_write_gives_the_saved_value_then_the_last_write(
    tmp_path: Path,
    class_name: str,
    codes: tuple[int, ...],
    kind: LVType,
    control_type: str,
    starting: dict[str, Any],
    expected: Any,
    written: Any,
) -> None:
    gaps: list[Gap] = []
    package = generate(
        tmp_path,
        class_name,
        codes,
        kind,
        control_type,
        {"starting_properties": starting},
        gaps,
    )
    assert gaps == []
    with package.use() as module:
        assert read(module) == expected
        write(module, written)
        assert read(module) == written


def test_a_decoded_property_a_control_has_no_value_for_is_error_1026(
    tmp_path: Path,
) -> None:
    """The control is no graph, so it has no scale to start from."""
    package = generate(tmp_path, "XYGraph (strict)", X_MAX, DBL, "stdString")
    with package.use() as module:
        ref = reference(module)
        assert module("reader").reader(ctl=ref).error_out.code == 1026

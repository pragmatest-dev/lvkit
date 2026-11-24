"""``Array Element`` of an array reference (``codegen/nodes/panel_reference.py``): the
reference to the array's element control, of the element's own class, holding what
the element is saved with (an Enum element's ``Strings[]``, a Boolean element's
``MechAction``). Each case is a handmade diagram whose generated package is imported
and run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lvkit.codegen.gaps import Gap
from lvkit.graph.models import GraphNode, VIContext
from lvkit.models import FPTerminal, LVType, LVTypeKind
from tests.class_graphs import INT, Project, pane, wire
from tests.error_diagrams import BOOL, REQUIRED, Vi
from tests.error_diagrams import Project as XmlProject
from tests.error_diagrams import project as xml_project  # noqa: F401
from tests.panel_diagrams import array_element, array_type
from tests.test_combo_box_strings import STRINGS
from tests.test_control_references import REFNUM, property_node
from tests.test_enum_strings import ENUM, ITEMS
from tests.test_front_panel_reference import ERROR
from tests.test_mech_action import BOOLEAN, U16

ELEMENT_CODE = (104041479,)
CLASS_ID_CODE = (103970816,)
STRINGS_CODE = (104056839,)
MECH_CODE = (104019976,)
LABEL_CODE = (104013829, 103995392)
U32 = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="NumUInt32")


def array_of(element: LVType) -> LVType:
    return LVType(kind=LVTypeKind.ARRAY, underlying_type="Array", element_type=element)


def array_control(
    vi: str, tag: str, index: int, element: LVType, element_class: str, **extra: Any
) -> FPTerminal:
    terminal = pane(
        vi,
        "input",
        tag,
        array_of(element),
        tag,
        index,
        tab_index=index,
        control_type="indArr",
    )
    return terminal.model_copy(
        update={"element_control_type": element_class, **extra}
    )


def target(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI whose panel has an array of enums ``colors``, an array of latch-when-
    released Booleans ``stops``, an array of numbers ``nums`` and a number ``n``."""
    return (
        VIContext(
            name=vi,
            inputs=[
                array_control(
                    vi, "colors", 0, ENUM, "stdRing", enum_values=ITEMS
                ),
                array_control(
                    vi,
                    "stops",
                    1,
                    BOOLEAN,
                    "stdBool",
                    element_mechanical_action=4,
                ),
                array_control(vi, "nums", 2, INT, "stdNum"),
                pane(vi, "input", "n", INT, "n", 3, tab_index=3, control_type="stdNum"),
            ],
            outputs=[pane(vi, "output", "out", INT, "out", 4, tab_index=4)],
            data_flow=[wire(vi, "n", "out")],
        ),
        [],
    )


def element_reader(
    class_name: str, code: tuple[int, ...], out_type: LVType
) -> Any:
    """A VI that reads the Array Element of the array reference it is given, then
    the property ``code`` of ``class_name`` of the element's reference."""

    def build(vi: str) -> tuple[VIContext, list[GraphNode]]:
        array = property_node(
            vi, "arr", "Arr", [("elem", ELEMENT_CODE, "output", REFNUM)]
        )
        element = property_node(
            vi, "el", class_name, [("p", code, "output", out_type)]
        )
        return (
            VIContext(
                name=vi,
                inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
                outputs=[
                    pane(vi, "output", "result", out_type, "result", 1),
                    pane(vi, "output", "err", ERROR, "error out", 2),
                ],
                data_flow=[
                    wire(vi, "ctl", "arr.ref"),
                    wire(vi, "arr.elem", "el.ref"),
                    wire(vi, "arr.err_out", "el.err_in"),
                    wire(vi, "el.p", "result"),
                    wire(vi, "el.err_out", "err"),
                ],
            ),
            [array, element],
        )

    return build


def array_label_reader(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that reads the Array Element and the Label.Text of the array reference
    it is given, as measurement-plugin's Filter for Enum Controls does."""
    node = property_node(
        vi,
        "arr",
        "Arr",
        [
            ("elem", ELEMENT_CODE, "output", REFNUM),
            ("label", LABEL_CODE, "output", STRING_T),
        ],
    )
    return (
        VIContext(
            name=vi,
            inputs=[pane(vi, "input", "ctl", REFNUM, "ctl", 0)],
            outputs=[
                pane(vi, "output", "elem", REFNUM, "elem", 1),
                pane(vi, "output", "label", STRING_T, "label", 2),
                pane(vi, "output", "err", ERROR, "error out", 3),
            ],
            data_flow=[
                wire(vi, "ctl", "arr.ref"),
                wire(vi, "arr.elem", "elem"),
                wire(vi, "arr.label", "label"),
                wire(vi, "arr.err_out", "err"),
            ],
        ),
        [node],
    )


STRING_T = LVType(kind=LVTypeKind.PRIMITIVE, underlying_type="String")


def element_writer(vi: str) -> tuple[VIContext, list[GraphNode]]:
    """A VI that writes the Array Element of the array reference it is given."""
    node = property_node(vi, "arr", "Arr", [("elem", ELEMENT_CODE, "input", REFNUM)])
    return (
        VIContext(
            name=vi,
            inputs=[
                pane(vi, "input", "ctl", REFNUM, "ctl", 0),
                pane(vi, "input", "other", REFNUM, "other", 1),
            ],
            outputs=[pane(vi, "output", "err", ERROR, "error out", 2)],
            data_flow=[
                wire(vi, "ctl", "arr.ref"),
                wire(vi, "other", "arr.elem"),
                wire(vi, "arr.err_out", "err"),
            ],
        ),
        [node],
    )


def project_with(tmp_path: Path) -> Project:
    project = Project(tmp_path)
    project.add_vi("Target", target)
    project.add_vi("Classid", element_reader("Ctl", CLASS_ID_CODE, U32))
    project.add_vi("Strings", element_reader("Enum", STRINGS_CODE, STRINGS))
    project.add_vi("Mech", element_reader("Bool", MECH_CODE, U16))
    project.add_vi("Arraylabel", array_label_reader)
    project.add_vi("Writer", element_writer)
    return project


def by_label(module: Any) -> dict[str, Any]:
    return {c.label: c for c in module("target").target.panel.controls}


def test_the_panel_describes_each_arrays_element(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate([])
    source = package.sources["target"]
    assert "elements={" in source
    assert "'colors': ('Enum', None, {'strings': ['red', 'green', 'blue']})" in source
    assert "'stops': ('Boolean', None, {'mech_action': 4})" in source
    assert "'nums': ('Digital', None, {})" in source
    assert "'n':" not in source.split("elements=")[1].split("}")[0]


def test_the_element_is_a_control_of_the_elements_class(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    package = project_with(tmp_path).generate(gaps)
    # only the Writer's write of the element is a gap (last test)
    assert [Path(g.vi).name for g in gaps] == ["Writer.vi"]
    assert "as_control(ctl).array_element" in package.sources["classid"]
    with package.use() as module:
        controls = by_label(module)
        read = module("classid").classid
        # NI's ClassID table: Enum 35, Boolean 8, Digital 18
        assert read(ctl=controls["colors"]).result == 35
        assert read(ctl=controls["stops"]).result == 8
        assert read(ctl=controls["nums"]).result == 18
        assert read(ctl=controls["colors"]).error_out is None


def test_the_element_is_the_same_object_each_time(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate([])
    with package.use() as module:
        colors = by_label(module)["colors"]
        assert colors.array_element is colors.array_element
        assert colors.array_element is not colors


def test_an_enum_elements_strings_and_a_boolean_elements_action(
    tmp_path: Path,
) -> None:
    package = project_with(tmp_path).generate([])
    with package.use() as module:
        controls = by_label(module)
        assert module("strings").strings(ctl=controls["colors"]).result == ITEMS
        assert module("mech").mech(ctl=controls["stops"]).result == 4
        # an element of a numeric array is no Enum: it has no Strings[]
        assert module("strings").strings(ctl=controls["nums"]).error_out.code == 1026


def test_a_control_that_is_not_an_array_has_no_element(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate([])
    with package.use() as module:
        result = module("classid").classid(ctl=by_label(module)["n"])
        assert result.error_out.code == 1026


def test_the_array_label_is_the_arrays_own(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate([])
    with package.use() as module:
        result = module("arraylabel").arraylabel(ctl=by_label(module)["colors"])
        assert result.label == "colors"
        assert result.elem is by_label(module)["colors"].array_element


def test_what_the_panel_does_not_record_of_an_element_raises(tmp_path: Path) -> None:
    package = project_with(tmp_path).generate([])
    with package.use() as module:
        element = by_label(module)["colors"].array_element
        with pytest.raises(NotImplementedError, match="Value of an element of"):
            element.value
        with pytest.raises(NotImplementedError, match="Label.Text of an element"):
            element.label
        with pytest.raises(NotImplementedError, match="Setting strings"):
            element.set_property("strings", [])


def test_a_write_of_the_element_stays_a_gap(tmp_path: Path) -> None:
    gaps: list[Gap] = []
    project_with(tmp_path).generate(gaps)
    assert [g.reason for g in gaps] == [
        "Property 'p104041479' on a Arr reference is written, and NI's page says it "
        "cannot be set while the VI runs ("
        "https://docs-be.ni.com/api/bundle/labview-api-ref/page/properties-and-methods/"
        "vi-server/generic/gobject/control/array/arrayelement.html)"
    ]


def test_the_parser_reads_the_elements_class_and_action(
    xml_project: XmlProject,  # noqa: F811
) -> None:
    vi = Vi("Arr.vi")
    kind = array_type(vi, BOOL)
    element = array_element(
        vi, "stdBool", BOOL, inner="<objFlags>393216</objFlags>"
    )
    vi.control("stops", kind, REQUIRED, ddo_class="indArr", inner=element)
    xml_project.add(vi)
    graph = xml_project.views("Arr.vi").graph
    (stops,) = graph.get_vi_context(graph.list_vis()[0]).inputs
    assert isinstance(stops, FPTerminal)
    assert stops.control_type == "indArr"
    assert stops.element_control_type == "stdBool"
    assert stops.element_mechanical_action == 4
    assert stops.element_vi_server_class == "Boolean"

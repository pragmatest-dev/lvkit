"""How the panel reads a control and its type: a cluster type definition is held
as an object with an attribute per field, nested fields build recursively, and
the call the panel module writes for a cluster names every builder it needs."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nicegui import ui

from lvkit.models import ClusterField, EnumValue, LVType, LVTypeKind
from lvkit.panel.kinds import PanelKind
from lvkit.panel.runtime import (
    LiveValues,
    Member,
    bool_port,
    cluster_indicator,
    cluster_port,
    number_port,
    path_text,
)
from lvkit.panel.shape import describe
from lvkit.panel.widget_call import (
    builders_used,
    member_call,
    modules_used,
    widget_call,
)
from lvkit.parser.models import ParsedFPControl, ParsedFPPart
from lvkit.path_value import format_path
from tests.panel_harness import found, simulate


def _primitive(name: str) -> LVType:
    return LVType(kind=LVTypeKind.PRIMITIVE, underlying_type=name)


def _fp(name: str, control_type: str, *children: ParsedFPControl) -> ParsedFPControl:
    return ParsedFPControl(
        uid=name,
        name=name,
        control_type=control_type,
        bounds=(0, 0, 20, 100),
        children=list(children),
    )


def _inner_cluster() -> tuple[ParsedFPControl, LVType]:
    control = _fp(
        "Inner", "stdClust", _fp("Gain", "stdNum"), _fp("On", "stdBool")
    )
    lv_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name="Inner.ctl",
        fields=[
            ClusterField(name="Gain", type=_primitive("NumInt32")),
            ClusterField(name="On", type=_primitive("Boolean")),
        ],
    )
    return control, lv_type


def test_a_cluster_type_definition_is_held_as_an_object_of_its_fields() -> None:
    control = _fp(
        "Settings", "stdClust", _fp("Start Time", "stdNum"), _fp("On", "stdBool")
    )
    lv_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name="Settings.ctl",
        fields=[
            ClusterField(name="Start Time", type=_primitive("NumFloat64")),
            ClusterField(name="On", type=_primitive("Boolean")),
        ],
    )
    described = describe(control, lv_type, None, "settings")
    assert described.gap is None
    assert described.named is True
    assert [(m.key, m.label) for m in described.members] == [
        ("start_time", "Start Time"),
        ("on", "On"),
    ]
    assert widget_call(described, described.kind, "'settings'") == (  # type: ignore[arg-type]
        "cluster_control(values, 'settings', True, ["
        "Member('start_time', 'Start Time', number_port, 0, False), "
        "Member('on', 'On', bool_port, False)])"
    )


def test_a_cluster_without_a_definition_is_held_as_a_tuple_in_field_order() -> None:
    control, lv_type = _inner_cluster()
    lv_type.typedef_name = None
    described = describe(control, lv_type, None, "inner")
    assert described.named is False
    assert [m.key for m in described.members] == ["0", "1"]


def test_a_cluster_in_a_cluster_builds_its_fields_and_names_every_builder() -> None:
    inner, inner_type = _inner_cluster()
    outer = _fp("Outer", "stdClust", inner, _fp("Label", "stdString"))
    outer_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="Inner", type=inner_type),
            ClusterField(name="Label", type=_primitive("String")),
        ],
    )
    described = describe(outer, outer_type, None, "outer", indicator=True)
    assert described.gap is None
    assert described.is_indicator is True
    assert all(m.is_indicator for m in described.members)
    assert described.members[0].named is True
    call = widget_call(described, described.kind, "'outer'")  # type: ignore[arg-type]
    assert call == (
        "cluster_indicator(values, 'outer', False, ["
        "Member('0', 'Inner', cluster_port, True, ["
        "Member('gain', 'Gain', number_port, 0, True), "
        "Member('on', 'On', bool_port, False)]), "
        "Member('1', 'Label', string_port, '')])"
    )
    assert builders_used(described, described.kind) == {  # type: ignore[arg-type]
        "cluster_indicator",
        "Member",
        "cluster_port",
        "number_port",
        "bool_port",
        "string_port",
    }
    assert member_call(described.members[1]) == (
        "Member('1', 'Label', string_port, '')"
    )


def test_an_error_cluster_control_is_an_error_widget() -> None:
    control = _fp("error in", "stdClust", _fp("status", "stdBool"))
    lv_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="status", type=_primitive("Boolean")),
            ClusterField(name="code", type=_primitive("NumInt32")),
            ClusterField(name="source", type=_primitive("String")),
        ],
    )
    control.children = [
        _fp("status", "stdBool"),
        _fp("code", "stdNum"),
        _fp("source", "stdString"),
    ]
    described = describe(control, lv_type, None, "error_in")
    assert described.kind is PanelKind.ERROR
    assert described.gap is None


def _error_type() -> LVType:
    return LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="status", type=_primitive("Boolean")),
            ClusterField(name="code", type=_primitive("NumInt32")),
            ClusterField(name="source", type=_primitive("String")),
        ],
    )


def test_an_error_cluster_field_of_a_cluster_is_an_error_widget() -> None:
    control = _fp("Result", "stdClust", _fp("err", "stdClust"), _fp("n", "stdNum"))
    lv_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="err", type=_error_type()),
            ClusterField(name="n", type=_primitive("NumInt32")),
        ],
    )
    described = describe(control, lv_type, None, "result")
    assert described.gap is None
    assert [m.kind for m in described.members] == [PanelKind.ERROR, PanelKind.NUMBER]
    assert member_call(described.members[0]) == "Member('0', 'err', error_port)"


def test_an_array_of_error_clusters_is_a_gap() -> None:
    control = _fp("Errors", "indArr", _fp("err", "stdClust"))
    lv_type = LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=_error_type(),
        dimensions=1,
    )
    described = describe(control, lv_type, None, "errors")
    assert described.kind is None
    assert described.gap is not None


def test_a_named_cluster_port_gives_and_shows_an_object_of_its_fields() -> None:
    ports: list[Any] = []

    def build() -> None:
        holder = LiveValues()
        ports.append(
            cluster_port(
                True,
                [Member("gain", "Gain", number_port, 0, True)],
                readonly=False,
            )
        )
        ports.append(
            cluster_indicator(
                holder, "settings", True, [Member("gain", "Gain", number_port, 0, True)]
            )
        )

    async def scenario(user: Any) -> None:
        control, indicator = ports
        (number,) = found(user, ui.number)
        number.set_value(4)
        assert control.get() == SimpleNamespace(gain=4)
        indicator.show(SimpleNamespace(gain=9))
        assert indicator.get() == SimpleNamespace(gain=9)
        (shown,) = found(user, ui.input)
        assert shown.value == "9"

    simulate(build, scenario)


def test_a_change_in_a_nested_field_reaches_the_holder_as_the_whole_cluster() -> None:
    holder = LiveValues()

    def build() -> None:
        from lvkit.panel.runtime import cluster_control

        cluster_control(
            holder,
            "outer",
            False,
            [
                Member("0", "Flag", bool_port, False),
                Member(
                    "1",
                    "Inner",
                    cluster_port,
                    True,
                    [Member("gain", "Gain", number_port, 0, True)],
                ),
            ],
        )

    async def scenario(user: Any) -> None:
        assert holder.value("outer") == (False, SimpleNamespace(gain=0))
        (number,) = found(user, ui.number)
        number.set_value(3)
        assert holder.value("outer") == (False, SimpleNamespace(gain=3))
        held = holder.value("outer")
        held[1].gain = 99  # the function may change what it is given
        assert holder.value("outer") == (False, SimpleNamespace(gain=3))

    simulate(build, scenario)


def _array_of(element: ParsedFPControl, element_type: LVType) -> Any:
    control = _fp("Rows", "indArr", *element.children)
    return describe(
        control,
        LVType(
            kind=LVTypeKind.ARRAY,
            underlying_type="Array",
            element_type=element_type,
            dimensions=1,
        ),
        None,
        "rows",
    )


def test_an_array_of_clusters_with_a_cluster_field_is_a_gap() -> None:
    inner, inner_type = _inner_cluster()
    row = _fp("Row", "stdClust", inner, _fp("Label", "stdString"))
    row_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        fields=[
            ClusterField(name="Inner", type=inner_type),
            ClusterField(name="Label", type=_primitive("String")),
        ],
    )
    described = _array_of(row, row_type)
    assert described.kind is None
    assert described.gap == (
        "the panel has no widget for an array of cluster (element of the array)"
    )


def test_an_array_of_clusters_names_the_element_and_every_column_builder() -> None:
    inner, _ = _inner_cluster()
    row_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name="Row.ctl",
        fields=[
            ClusterField(name="Gain", type=_primitive("NumInt32")),
            ClusterField(name="On", type=_primitive("Boolean")),
        ],
    )
    described = _array_of(inner, row_type)
    assert described.gap is None
    assert described.kind is not None
    assert widget_call(described, described.kind, "'rows'") == (
        "array_control(values, 'rows', Member('value', '', cluster_port, True, ["
        "Member('gain', 'Gain', number_port, 0, True), "
        "Member('on', 'On', bool_port, False)]), 2, [])"
    )
    assert builders_used(described, described.kind) == {
        "array_control",
        "Member",
        "cluster_port",
        "number_port",
        "bool_port",
    }


def _array_of_numbers(saved: object) -> Any:
    control = _fp("Values", "indArr")
    control.parts = [ParsedFPPart(None, "stdNum", (0, 0, 22, 80))]
    return describe(
        control,
        LVType(
            kind=LVTypeKind.ARRAY,
            underlying_type="Array",
            element_type=_primitive("NumInt32"),
            dimensions=1,
        ),
        saved,
        "values",
    )


def test_an_array_starts_with_the_elements_it_is_saved_with() -> None:
    described = _array_of_numbers("[3, 1, 2]")
    assert described.initial == (3, 1, 2)
    assert widget_call(described, described.kind, "'values'").endswith(  # type: ignore[arg-type]
        ", 2, [3, 1, 2])"
    )


def test_an_array_saved_with_values_of_another_kind_starts_empty() -> None:
    assert _array_of_numbers("['a', 'b']").initial == ()
    assert _array_of_numbers("[True]").initial == ()
    assert _array_of_numbers("not a list").initial == ()
    assert _array_of_numbers(None).initial == ()


def _scalar(control_type: str, type_name: str, saved: object) -> Any:
    return describe(
        _fp("Value", control_type), _primitive(type_name), saved, "value"
    )


def test_a_string_control_starts_from_the_text_it_is_saved_with() -> None:
    # The parser keeps a saved string as a Python string literal.
    described = _scalar("stdString", "String", "'abc'")
    assert described.default == "abc"
    assert widget_call(described, described.kind, "'value'") == (
        "string_control(values, 'value', 'abc')"
    )
    assert _scalar("stdString", "String", "''").default == ""
    assert _scalar("stdString", "String", None).default == ""


def test_a_boolean_or_number_saved_as_text_starts_from_its_value() -> None:
    assert _scalar("stdBool", "Boolean", "False").default is False
    assert _scalar("stdBool", "Boolean", "True").default is True
    assert _scalar("stdBool", "Boolean", None).default is False
    assert _scalar("stdNum", "NumFloat64", "2.5").default == 2.5
    assert _scalar("stdNum", "NumInt32", "7").default == 7
    assert _scalar("stdNum", "NumInt32", None).default == 0


def test_a_path_control_starts_from_its_saved_path_on_the_system_it_runs_on() -> (
    None
):
    saved = format_path(0, ["D", "data", "run1.tdms"])
    described = _scalar("stdPath", "Path", saved)
    call = widget_call(described, described.kind, "'value'")
    assert call == (
        "path_control(values, 'value', path_text(Path('D:/' if os.name == 'nt' "
        "else '/D', 'data', 'run1.tdms')))"
    )
    assert "path_text" in builders_used(described, described.kind)
    assert modules_used(described) == {"os"}
    names = {"path_text": path_text, "Path": Path, "os": os}
    volume = "D:/" if os.name == "nt" else "/D"
    assert eval(f"path_text({described.default})", names) == str(
        Path(volume, "data", "run1.tdms")
    )


def test_an_empty_or_unsaved_path_starts_with_no_text() -> None:
    for saved in (format_path(1, []), None):
        described = _scalar("stdPath", "Path", saved)
        assert widget_call(described, described.kind, "'value'") == (
            "path_control(values, 'value', path_text(Path('')))"
        )
        assert modules_used(described) == set()
        assert path_text(Path("")) == ""


def test_an_enum_type_definition_is_an_enum_control_of_its_items() -> None:
    control = _fp("Shape", "stdEnum")
    lv_type = LVType(
        kind=LVTypeKind.ENUM,
        underlying_type="UnitUInt16",
        typedef_name="Shape.ctl",
        values={"Sine": EnumValue(value=0), "Square": EnumValue(value=1)},
    )
    assert lv_type.to_python() == "Shape"
    described = describe(control, lv_type, None, "shape")
    assert described.gap is None
    assert described.items == (("Sine", 0), ("Square", 1))

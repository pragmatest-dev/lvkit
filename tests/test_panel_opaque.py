"""Complex numbers, variants, references and LabVIEW class objects on the panel: a
complex number is two number boxes, a variant is read-only text, and a reference
or class object is not drawn and the function gets the default of its type."""

from __future__ import annotations

from typing import Any

import pytest
from nicegui import ui
from nicegui.testing.user import User

from lvkit.models import ClusterField, LVType, LVTypeKind
from lvkit.panel.kinds import PanelKind
from lvkit.panel.shape import describe
from lvkit.panel.widget_call import builders_used, member_call, widget_call
from lvkit.parser.models import ParsedFPControl, ParsedFPPart
from tests.error_diagrams import (  # noqa: F401
    BOOL,
    FILE,
    OPTIONAL,
    REQUIRED,
    Project,
    Vi,
    project,
)
from tests.panel_diagrams import ADD
from tests.panel_harness import (
    controls_of,
    found,
    panel_of,
    run_once,
    simulate,
)

NAME = "Opaque Demo.vi"
STEM = "opaque_demo"


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


def _class_type() -> LVType:
    return LVType(kind=LVTypeKind.CLASS, underlying_type="Class", classname="X.lvclass")


def _record_calls(monkeypatch: pytest.MonkeyPatch, panel: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    original = panel.vi_function

    def recorded(**arguments: Any) -> Any:
        calls.append(arguments)
        return original(**arguments)

    monkeypatch.setattr(panel, "vi_function", recorded)
    return calls


# -- what the panel makes of each control ------------------------------------


@pytest.mark.parametrize("name", ["NumComplex64", "NumComplex128", "NumComplexExt"])
def test_a_complex_number_control_is_the_complex_kind(name: str) -> None:
    described = describe(_fp("Z", "stdNum"), _primitive(name), None, "z")
    assert described.gap is None
    assert described.kind is PanelKind.COMPLEX
    assert described.default == 0j
    assert widget_call(described, PanelKind.COMPLEX, "'z'") == (
        "complex_control(values, 'z', 0j)"
    )


def test_a_real_number_control_stays_a_number() -> None:
    described = describe(_fp("X", "stdNum"), _primitive("NumFloat64"), None, "x")
    assert described.kind is PanelKind.NUMBER


def test_an_array_of_complex_numbers_has_complex_cells() -> None:
    control = _fp("Zs", "indArr")
    control.parts = [ParsedFPPart(None, "stdNum", (0, 0, 22, 80))]
    lv_type = LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=_primitive("NumComplex128"),
        dimensions=1,
    )
    described = describe(control, lv_type, None, "zs")
    assert described.gap is None
    assert described.kind is PanelKind.ARRAY
    assert described.element is not None
    assert described.element.kind is PanelKind.COMPLEX
    assert widget_call(described, PanelKind.ARRAY, "'zs'") == (
        "array_control(values, 'zs', Member('value', '', complex_port, 0j), 2, [])"
    )
    assert builders_used(described, PanelKind.ARRAY) == {
        "array_control",
        "Member",
        "complex_port",
    }


def test_a_variant_control_is_the_variant_kind_for_a_control_and_an_indicator() -> None:
    described = describe(_fp("V", "stdLvVariant"), _primitive("Variant"), None, "v")
    assert described.gap is None
    assert described.kind is PanelKind.VARIANT
    assert widget_call(described, PanelKind.VARIANT, "'v'") == (
        "variant_control(values, 'v')"
    )
    indicator = describe(
        _fp("V", "stdLvVariant"), _primitive("Variant"), None, "v", indicator=True
    )
    assert widget_call(indicator, PanelKind.VARIANT, "'v'") == (
        "variant_indicator(values, 'v')"
    )


def test_a_reference_and_a_class_object_are_hidden_and_not_a_gap() -> None:
    reference = describe(_fp("R", "stdRefNum"), _primitive("Refnum"), None, "r")
    assert (reference.kind, reference.gap, reference.hidden) == (
        PanelKind.HIDDEN,
        None,
        True,
    )
    class_object = describe(_fp("C", "udClassDDO"), _class_type(), None, "c")
    assert (class_object.kind, class_object.gap, class_object.hidden) == (
        PanelKind.HIDDEN,
        None,
        True,
    )


def test_a_cluster_with_a_variant_and_a_reference_field_builds_both() -> None:
    control = _fp(
        "Settings",
        "stdClust",
        _fp("Value", "stdLvVariant"),
        _fp("Ref", "stdRefNum"),
        _fp("Gain", "stdNum"),
    )
    lv_type = LVType(
        kind=LVTypeKind.CLUSTER,
        underlying_type="Cluster",
        typedef_name="Settings.ctl",
        fields=[
            ClusterField(name="Value", type=_primitive("Variant")),
            ClusterField(name="Ref", type=_primitive("Refnum")),
            ClusterField(name="Gain", type=_primitive("NumInt32")),
        ],
    )
    described = describe(control, lv_type, None, "settings")
    assert described.gap is None
    assert [m.kind for m in described.members] == [
        PanelKind.VARIANT,
        PanelKind.HIDDEN,
        PanelKind.NUMBER,
    ]
    assert widget_call(described, PanelKind.CLUSTER, "'settings'") == (
        "cluster_control(values, 'settings', True, ["
        "Member('value', 'Value', variant_port), "
        "Member('ref', 'Ref', hidden_port), "
        "Member('gain', 'Gain', number_port, 0, True)])"
    )
    assert "hidden_port" in builders_used(described, PanelKind.CLUSTER)
    assert member_call(described.members[1]) == "Member('ref', 'Ref', hidden_port)"


def test_an_array_of_references_is_a_gap_that_says_so() -> None:
    control = _fp("Refs", "indArr")
    control.parts = [ParsedFPPart(None, "stdRefNum", (0, 0, 22, 80))]
    lv_type = LVType(
        kind=LVTypeKind.ARRAY,
        underlying_type="Array",
        element_type=_primitive("Refnum"),
        dimensions=1,
    )
    described = describe(control, lv_type, None, "refs")
    assert described.gap == (
        "the panel has no widget for an array of reference or LabVIEW class object "
        "(element of the array)"
    )


# -- the generated panel ------------------------------------------------------


def _complex_vi() -> Vi:
    vi = Vi(NAME)
    cdb = vi.add_type('<TypeDesc Type="NumComplex128" />')
    z = vi.control("z", cdb, REQUIRED, bounds=(40, 20, 62, 260))
    twice = vi.indicator("twice", cdb, bounds=(40, 300, 62, 540))
    total = vi.primitive(
        ADD, [("sum", 0, cdb, True), ("y", 1, cdb, False), ("x", 2, cdb, False)]
    )
    vi.wire(z, total["x"], total["y"])
    vi.wire(total["sum"], twice)
    return vi


def test_a_complex_control_is_two_number_boxes_and_the_indicator_shows_both_parts(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_complex_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        real, imaginary = found(user, ui.number)
        assert (real.label, imaginary.label) == ("re", "im")
        real.set_value(1)
        imaginary.set_value(2)
        await run_once(user)
        assert calls[0]["z"] == 1 + 2j
        assert type(calls[0]["z"]) is complex
        shown_real, shown_imaginary = found(user, ui.input)
        assert (shown_real.value, shown_imaginary.value) == ("2.0", "4.0")

    simulate(panel.build_panel, scenario)


def _reference_vi(rule: int) -> Vi:
    vi = Vi(NAME)
    vi.control("Ref", FILE, rule, ddo_class="stdRefNum", bounds=(40, 20, 62, 160))
    on = vi.control(
        "On", BOOL, REQUIRED, ddo_class="stdBool", bounds=(40, 200, 70, 300)
    )
    shown = vi.indicator("Shown", BOOL, ddo_class="stdBool", bounds=(40, 340, 70, 440))
    vi.wire(on, shown)
    return vi


def test_a_required_reference_is_not_drawn_and_gets_none(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_reference_vi(REQUIRED))
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    source = (package.root / f"{STEM}_panel.py").read_text(encoding="utf-8")
    assert "ref=None," in source
    assert "caption('Ref')" not in source
    assert "caption('On')" in source
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        await run_once(user)
        assert calls[0]["ref"] is None

    simulate(panel.build_panel, scenario)


def test_an_optional_reference_is_not_passed(
    project: Project,  # noqa: F811
) -> None:
    project.add(_reference_vi(OPTIONAL))
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    source = (package.root / f"{STEM}_panel.py").read_text(encoding="utf-8")
    assert "ref=" not in source
    assert "caption('Ref')" not in source


def _variant_vi() -> Vi:
    vi = Vi(NAME)
    variant = vi.add_type('<TypeDesc Type="Variant" />')
    value = vi.control(
        "V", variant, REQUIRED, ddo_class="stdLvVariant", bounds=(40, 20, 62, 160)
    )
    shown = vi.indicator(
        "Shown", variant, ddo_class="stdLvVariant", bounds=(40, 200, 62, 340)
    )
    vi.wire(value, shown)
    return vi


def test_a_variant_control_and_indicator_are_read_only_text_of_the_value(
    project: Project,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project.add(_variant_vi())
    package = project.generate(NAME, panel=True)
    assert package.gaps == []
    panel = panel_of(package, STEM)
    calls = _record_calls(monkeypatch, panel)

    async def scenario(user: User) -> None:
        control, indicator = found(user, ui.input)
        assert control.props["readonly"] is True
        assert control.value == "None"
        await run_once(user)
        assert calls[0]["v"] is None
        assert indicator.value == "None"

    simulate(panel.build_panel, scenario)


def test_a_variant_indicator_shows_the_text_of_what_the_function_writes(
    project: Project,  # noqa: F811
) -> None:
    project.add(_variant_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    holder = controls.LiveValues()

    def build() -> None:
        controls.variant_indicator(holder, "v")

    async def scenario(user: User) -> None:
        (shown,) = found(user, ui.input)
        holder.show("v", [1, "a"])
        assert shown.value == "[1, 'a']"

    simulate(build, scenario)


def test_a_cluster_holds_none_for_a_field_it_does_not_draw(
    project: Project,  # noqa: F811
) -> None:
    project.add(_variant_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    holder = controls.LiveValues()
    members = [
        controls.Member("ref", "Ref", controls.hidden_port),
        controls.Member("gain", "Gain", controls.number_port, 0, True),
    ]

    def build() -> None:
        controls.cluster_control(holder, "c", False, members)

    async def scenario(user: User) -> None:
        assert holder.value("c") == (None, 0)
        found(user, ui.number)[0].set_value(5)
        assert holder.value("c") == (None, 5)
        assert "Ref" not in " ".join(
            str(e.text) for e in user.find(kind=ui.label).elements
        )
        assert "Gain" in " ".join(
            str(e.text) for e in user.find(kind=ui.label).elements
        )

    simulate(build, scenario)


def test_an_array_of_complex_numbers_has_a_real_and_an_imaginary_column(
    project: Project,  # noqa: F811
) -> None:
    project.add(_complex_vi())
    package = project.generate(NAME, panel=True)
    controls = controls_of(package)
    holder = controls.LiveValues()
    element = controls.Member("value", "", controls.complex_port, 0j)

    def build() -> None:
        controls.array_control(holder, "zs", element, 3, [1 + 2j, 3j])

    async def scenario(user: User) -> None:
        grid = found(user, ui.aggrid)[0]
        headers = [c["headerName"] for c in grid.options["columnDefs"]]
        assert headers[1:3] == ["re", "im"]
        assert grid.options["rowData"] == [
            {"value_re": 1.0, "value_im": 2.0},
            {"value_re": 0.0, "value_im": 3.0},
        ]
        assert holder.value("zs") == [1 + 2j, 3j]
        user.find(marker="add-row").click()
        assert holder.value("zs") == [1 + 2j, 3j, 0j]

    simulate(build, scenario)

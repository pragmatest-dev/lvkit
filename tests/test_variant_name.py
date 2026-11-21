"""A generated ``Variant`` records its ``name``: the label of the control or
indicator whose wire the data came from (what Get Type Information's ``name``
returns). Each case is a tiny diagram, generated and run (``tests/error_diagrams.py``):
To Variant of a control, a control wired into a subVI's Variant control (the typed
wire meets a Variant terminal), a wire that comes out of a node (no label), a copy
of a variant, and a Variant constant."""

from __future__ import annotations

import copy

from lvkit import type_string
from lvkit.models import LVType, LVTypeKind
from tests.error_diagrams import (
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
    unbundle,
)

NAME = "Var.vi"
_VARIANT = '<TypeDesc Type="Variant" Format="inline" />'
TAKE = """
    from types import SimpleNamespace

    def take(v=None):
        return SimpleNamespace(r=v)
"""


def _to_variant(project: Project, label: str) -> Package:  # noqa: F811
    """The control ``label`` through To Variant into the indicator ``seen``."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    x = vi.control(label, I32, REQUIRED)
    to_variant = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, I32, False)]
    )
    vi.wire(x, to_variant["anything"])
    vi.wire(to_variant["Variant"], vi.indicator("seen", variant_type))
    project.add(vi)
    return project.generate(NAME)


def test_to_variant_of_a_control_is_named_by_the_controls_label(
    project: Project,  # noqa: F811
) -> None:
    package = _to_variant(project, "Voltage")
    seen = package.function(5).seen
    assert seen.name == "Voltage"
    assert seen.value == 5
    assert "name='Voltage'" in package.source
    assert "lvkit" not in package.source


def _into_subvi(project: Project, source: str) -> Package:  # noqa: F811
    """A control (``source == "control"``) or an integer constant wired into the
    Variant control of a subVI whose Variant indicator is the VI's ``seen``."""
    caller = Vi(NAME)
    variant_type = caller.add_type(_VARIANT)
    call = caller.subvi(
        "Take.vi", v=(0, variant_type, False), r=(1, variant_type, True)
    )
    if source == "control":
        caller.wire(caller.control("Setpoint", I32, REQUIRED), call["v"])
    else:
        caller.wire(caller.int_constant(5), call["v"])
    caller.wire(call["r"], caller.indicator("seen", variant_type))
    project.add(caller)
    callee = Vi("Take.vi")
    callee.control("v", variant_type, REQUIRED)
    callee.indicator("r", variant_type)
    callee.extra_types = caller.extra_types
    project.add(callee)
    return project.generate(NAME, {"take": TAKE})


def test_a_control_wired_into_a_variant_control_names_the_variant(
    project: Project,  # noqa: F811
) -> None:
    package = _into_subvi(project, "control")
    assert package.function(5).seen.name == "Setpoint"


def test_a_constant_has_no_label_so_the_name_is_empty(
    project: Project,  # noqa: F811
) -> None:
    package = _into_subvi(project, "constant")
    seen = package.function().seen
    assert seen.name == ""
    assert seen == package.variants.Variant(5, package.variants.LV_I32)
    assert "name=" not in package.source


def test_a_copy_of_a_variant_keeps_its_name(project: Project) -> None:  # noqa: F811
    package = _to_variant(project, "Voltage")
    variants = package.variants
    named = variants.Variant(1, variants.LV_I32, {}, "Voltage")
    assert copy.copy(named).name == "Voltage"
    # The name is not part of what two variants are equal by.
    assert named == variants.Variant(1, variants.LV_I32)


def test_the_type_string_ends_in_the_label_the_name_returns(
    project: Project,  # noqa: F811
) -> None:
    package = _to_variant(project, "Voltage")
    variants = package.variants
    seen = package.function(5).seen
    assert seen.name == "Voltage"
    numbers, _ = variants.variant_to_flattened(seen)
    assert numbers == type_string.encode(
        LVType(LVTypeKind.PRIMITIVE, underlying_type="NumInt32"), "Voltage"
    )
    flattened = variants.LVType(3, "I32", tuple(numbers))
    assert variants.read_type(flattened).name == seen.name
    # A variant with no name flattens with the plain type string.
    plain = variants.Variant(5, variants.LV_I32)
    assert variants.variant_to_flattened(plain)[0] == [4, 3]


def _unbundled(project: Project) -> Package:  # noqa: F811
    """The ``code`` field of the error in, through To Variant into ``seen``."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    node = unbundle(vi, [("code", 1)])
    vi.wire(vi.error_in(), node["cluster"])
    to_variant = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, I32, False)]
    )
    vi.wire(node["code"], to_variant["anything"])
    vi.wire(to_variant["Variant"], vi.indicator("seen", variant_type))
    project.add(vi)
    return project.generate(NAME)


def test_an_unbundle_by_name_output_is_named_by_its_field(
    project: Project,  # noqa: F811
) -> None:
    package = _unbundled(project)
    seen = package.function().seen
    assert seen.name == "code"
    numbers, _ = package.variants.variant_to_flattened(seen)
    assert numbers == type_string.encode(
        LVType(LVTypeKind.PRIMITIVE, underlying_type="NumInt32"), "code"
    )


def _from_constant(project: Project, label: str | None) -> Package:  # noqa: F811
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    to_variant = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, I32, False)]
    )
    vi.wire(vi.constant(I32, "00000005", label=label), to_variant["anything"])
    vi.wire(to_variant["Variant"], vi.indicator("seen", variant_type))
    project.add(vi)
    return project.generate(NAME)


def test_a_labelled_constant_is_named_by_its_label(
    project: Project,  # noqa: F811
) -> None:
    seen = _from_constant(project, "Gain").function().seen
    assert seen.name == "Gain"
    assert seen.value == 5


def test_a_subvi_output_is_named_by_its_indicators_label(
    project: Project,  # noqa: F811
) -> None:
    caller = Vi(NAME)
    variant_type = caller.add_type(_VARIANT)
    call = caller.subvi("Read.vi", Reading=(0, I32, True))
    to_variant = caller.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, I32, False)]
    )
    caller.wire(call["Reading"], to_variant["anything"])
    caller.wire(to_variant["Variant"], caller.indicator("seen", variant_type))
    project.add(caller)
    callee = Vi("Read.vi")
    callee.indicator("Reading", I32)
    project.add(callee)
    seen = project.generate(NAME).function().seen
    assert seen.name == "Reading"
    assert seen.type == project.generate(NAME).variants.LV_I32


def _through_variant_to_data(
    project: Project,  # noqa: F811
    data_type_desc: str | None,
) -> Package:
    """A control through To Variant and Variant To Data, whose ``data`` output has
    the type descriptor ``data_type_desc`` (None: the plain I32), through To
    Variant into ``seen``."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    data_type = I32 if data_type_desc is None else vi.add_type(data_type_desc)
    first = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, I32, False)]
    )
    vi.wire(vi.control("x", I32, REQUIRED), first["anything"])
    to_data = vi.primitive(
        8003,
        [
            ("error in", 0, 6, False),
            ("error out", 3, 6, True),
            ("Variant", 8, variant_type, False),
            ("type", 9, I32, False),
            ("data", 11, data_type, True),
        ],
    )
    vi.wire(first["Variant"], to_data["Variant"])
    second = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, data_type, False)]
    )
    vi.wire(to_data["data"], second["anything"])
    vi.wire(second["Variant"], vi.indicator("seen", variant_type))
    vi.wire(to_data["error out"], vi.error_out())
    project.add(vi)
    return project.generate(NAME)


def test_a_function_output_whose_descriptor_has_a_label_is_named_by_it(
    project: Project,  # noqa: F811
) -> None:
    """The ``data`` output of Variant To Data records the label of its type
    descriptor, which Name returns and the type string ends in."""
    package = _through_variant_to_data(
        project, '<TypeDesc Type="NumInt32" Label="raw xml #xml" />'
    )
    seen = package.function(5).seen
    assert (seen.value, seen.name) == (5, "raw xml #xml")
    numbers, _ = package.variants.variant_to_flattened(seen)
    assert numbers == type_string.encode(
        LVType(LVTypeKind.PRIMITIVE, underlying_type="NumInt32"), "raw xml #xml"
    )


def test_a_function_output_whose_descriptor_has_no_label_has_no_name(
    project: Project,  # noqa: F811
) -> None:
    seen = _through_variant_to_data(project, None).function(5).seen
    assert (seen.value, seen.name) == (5, "")


def test_a_controls_descriptor_label_names_the_variant(
    project: Project,  # noqa: F811
) -> None:
    """The control's type descriptor carries the label, not its front-panel name."""
    vi = Vi(NAME)
    variant_type = vi.add_type(_VARIANT)
    labelled = vi.add_type('<TypeDesc Type="NumInt32" Label="Pressure" />')
    to_variant = vi.primitive(
        8201, [("Variant", 0, variant_type, True), ("anything", 1, labelled, False)]
    )
    vi.wire(vi.control("Gauge", labelled, REQUIRED), to_variant["anything"])
    vi.wire(to_variant["Variant"], vi.indicator("seen", variant_type))
    project.add(vi)
    assert project.generate(NAME).function(5).seen.name == "Pressure"


def test_an_unlabelled_constant_has_no_name_and_the_plain_type_string(
    project: Project,  # noqa: F811
) -> None:
    package = _from_constant(project, None)
    seen = package.function().seen
    assert seen.name == ""
    assert seen.type == package.variants.LV_I32

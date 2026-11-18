"""A generated ``Variant`` records its ``name``: the label of the control or
indicator whose wire the data came from (what Get Type Information's ``name``
returns). Each case is a tiny diagram, generated and run (``tests/error_diagrams.py``):
To Variant of a control, a control wired into a subVI's Variant control (the typed
wire meets a Variant terminal), a wire that comes out of a node (no label), a copy
of a variant, and a Variant constant."""

from __future__ import annotations

import copy

from tests.error_diagrams import (
    I32,
    REQUIRED,
    Package,
    Project,
    Vi,
    project,  # noqa: F401  (the fixture)
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

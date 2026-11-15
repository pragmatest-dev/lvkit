"""A Property Node row the file records with no name and no code (a Property Node
saved by an older LabVIEW keeps only the property's data type) is a gap that says
so, naming the control; a row with a recorded code next to it is unaffected."""

from __future__ import annotations

from tests.error_diagrams import (
    REQUIRED,
    STR,
    Project,
    Vi,
    project,  # noqa: F401
)
from tests.panel_diagrams import value_property

NAME = "Prop.vi"


def test_a_row_with_no_recorded_property_names_the_control_and_the_missing_name(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    s = vi.control("s", STR, REQUIRED, ddo_class="stdString")
    (row,) = value_property(vi, s, [("", 0)])
    seen = vi.indicator("seen", STR, ddo_class="stdString")
    vi.wire(row, seen)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    message = str(package.gaps[0])
    assert "the Property Node of control 's' reads a property the VI file" in message
    assert "reads ''" not in message
    assert "does not name" in message


def test_a_write_row_with_no_recorded_property_is_the_same_gap(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    s = vi.control("s", STR, REQUIRED, ddo_class="stdString")
    (row,) = value_property(vi, s, [("", 0)])
    vi.wire(s, row)
    project.add(vi)
    package = project.generate(NAME)
    assert len(package.gaps) == 1
    assert "writes a property the VI file does not name" in str(package.gaps[0])


def test_a_row_with_a_recorded_value_code_still_generates(
    project: Project,  # noqa: F811
) -> None:
    vi = Vi(NAME)
    s = vi.control("s", STR, REQUIRED, ddo_class="stdString")
    out = vi.indicator("out", STR, ddo_class="stdString")
    (value,) = value_property(vi, out, ["Value"])
    vi.wire(s, value)
    project.add(vi)
    package = project.generate(NAME)
    assert package.gaps == []
    assert package.function(s="x").out == "x"

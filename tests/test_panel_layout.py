"""How the drawn controls are grouped into rows and columns from their stored
bounds (top, left, bottom, right)."""

from __future__ import annotations

from lvkit.panel.kinds import PanelControl, PanelKind
from lvkit.panel.layout import arrange


def _control(label: str, top: int, left: int, bottom: int, right: int) -> PanelControl:
    return PanelControl(
        label=label,
        kind=PanelKind.NUMBER,
        is_indicator=False,
        bounds=(top, left, bottom, right),
        key=label,
        member=None,
        parameter=label,
        field=None,
        default=0,
        integer=False,
    )


def _labels(controls: list[PanelControl]) -> list[str]:
    return [c.label for c in controls]


def _shape(controls: list[PanelControl]) -> list[list[list[str]]]:
    return [[_labels(column) for column in row] for row in arrange(controls)]


def test_two_controls_side_by_side_are_one_row_of_two_columns() -> None:
    left = _control("left", 10, 0, 30, 100)
    right = _control("right", 10, 120, 30, 220)
    assert _shape([right, left]) == [[["left"], ["right"]]]


def test_two_controls_one_under_the_other_are_two_rows() -> None:
    upper = _control("upper", 0, 0, 20, 100)
    lower = _control("lower", 40, 0, 60, 100)
    assert _shape([lower, upper]) == [[["upper"]], [["lower"]]]


def test_a_tall_control_beside_two_stacked_ones_is_one_row() -> None:
    tall = _control("tall", 0, 0, 100, 50)
    upper = _control("upper", 0, 60, 40, 160)
    lower = _control("lower", 50, 60, 90, 160)
    assert _shape([lower, tall, upper]) == [[["tall"], ["upper", "lower"]]]


def test_controls_drawn_on_top_of_each_other_are_stacked_in_one_column() -> None:
    back = _control("back", 0, 0, 50, 100)
    front = _control("front", 20, 10, 70, 90)
    assert _shape([front, back]) == [[["back", "front"]]]


def test_equal_tops_are_ordered_by_left() -> None:
    a = _control("a", 5, 200, 25, 300)
    b = _control("b", 5, 0, 25, 100)
    c = _control("c", 5, 100, 25, 150)
    assert _shape([a, b, c]) == [[["b"], ["c"], ["a"]]]
    assert _shape([c, a, b]) == _shape([a, b, c])


def test_no_controls_is_no_rows() -> None:
    assert arrange([]) == []
